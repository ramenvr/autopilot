"""A game's memory kept with Aura, for runs through Aura (autopilot/aura.py): the same files on every machine of the
team.

What runs learned in the memory folder (memory/<project>/: notes.md, lib.py, runs/) is kept the same in every folder
that syncs the project, in both directions: a new machine, a reinstall, or a second Autopilot on one computer with
its own folder. The codex (codex/, the game's code as text) never is: every run rebuilds it from the game, and
Autopilot doesn't store whole codebases. A save removes any codex an older Autopilot stored there. Aura keeps the
memory per team and project, behind the run's token (POST <AUTOPILOT_AURA_URL>/api/autopilot/memory, ops list / sign /
delete): the brain sends and fetches the files themselves over short-lived signed URLs Aura gives it, and never holds
the storage's credentials.

    restore(folder)  before every run: what other folders stored since this one last synced is brought in. With
                     nothing new it costs one listing: a file is only read when its stored MD5 has moved.
    save(folder)     after the run: the same first, then what is new or changed here is sent, and files this folder
                     had synced but has since removed are removed from Aura too.

Each file is compared three ways: this folder, Aura's copy, and the copy both last agreed on (the MD5s in SYNC_FILE).

    changed in one place           the change is taken: sent, or fetched over the local file
    changed in both, notes.md      merged by line (merge_notes): every line either side has survives, once. A line one
                                   side removed may come back from the other: a merge can't tell, and never drops one
    changed in both, lib.py or     Aura's copy stays where it is, because other folders build on it, and this folder's
    a run's file                   goes to runs/conflict-<hash>/<path>, so it is synced and read like an earlier run
    changed in both, the rest      Aura's copy is taken (.codex-seen.json.gz: a run rebuilds it); this folder's goes to
                                   <memory>/.restore-backup/<project>/<time>/ (never read)
    removed from Aura              removed here too, moved to .restore-backup, unless it changed here since: then it
                                   is sent again
    removed here                   removed from Aura, unless Aura's copy changed since: then it is fetched again

A folder that holds memory but has never synced, or was last synced by an older Autopilot (which overwrote stored
files and so left no copy both agreed on), goes through the same comparison with no agreed copy: it merges rather
than replacing either side. Nothing is fetched for runs older than the RECENT_RUNS most recent, which stay stored.

Everything is fetched into a staging folder before anything in the memory folder changes, so a sync that fails leaves
the folder as it was: the run goes on from the memory it has, and the next sync tries again. The storage has no
conditional write, so a save lists right before sending and checks the listing after: a file another folder sent
first is merged rather than overwritten, and one that lost a race stays unsynced until the next sync merges it. Only
two saves landing inside one upload can still lose the earlier one's change.

A Reset memory (memory.forget) moves the stored learned files aside and leaves RESET_ID, a stored file naming the
reset. A folder that hasn't seen that reset moves the learned files it had synced to memory.RESET_BACKUP, its edits
to them included, so a stale folder never sends them back, and then fetches what was learned since. A file it never
synced was learned after the reset and is kept. Older Autopilots reset without RESET_ID: their reset reaches other
folders as files removed from Aura. A store that is wholly empty for a folder that has synced is never taken for a
reset: it was erased, or the run's account now has another store, so the folder's memory is sent to it.

Runs with the user's own keys keep it the same way when the user shares their runs (install_memory): with this
machine's install token (autopilot/training.py), in Aura's storage for this install, so its Reset memory reaches a
stored copy too. Each copy has its own SYNC_FILE, and a reset is passed on to each copy once (RESET_MARK says which have
it), whether it was pressed in this folder or reached it through the other copy.

A save or a restore that fails for a reason that passes (the network, Aura or the storage busy or down:
autopilot/retry.py) is tried again at once, after short waits that double from RETRY_SECONDS (both happen while the run
starts or ends); one refused (a quota, a bad token) is reported as below.

Never synced: the codex, SCRATCH
folders (Claude Code's own folder, which holds the session's transcript, and bytecode caches), LOCAL_ONLY files (where
the project is on this machine) and files over MAX_FILE_BYTES. A failure is reported and the run goes on: memory is a
copy, never what a run depends on.
"""

import base64
import concurrent.futures as futures
import hashlib
import json
import os
import shutil
import tempfile
import time
import uuid

import requests

from . import memory, retry

SYNC_FILE = ".aura-sync.json"
INSTALL_SYNC_FILE = ".install-sync.json"  # the copy kept for this install (runs with the user's own keys)
SYNC_VERSION = 2  # a sync file with no version: an older Autopilot's, whose MD5s are no copy both sides agreed on
RESET_ID = ".memory-reset-id"  # stored, never in a folder: names the last reset, for the folders that haven't seen it
NOTES = "notes.md"  # the one file merged line by line
# (project.json: where the project is on this machine; .training-sync.json: what shared run data was sent)
LOCAL_ONLY = ("project.json", SYNC_FILE, INSTALL_SYNC_FILE, ".training-sync.json", memory.RESET_MARK, RESET_ID)
TELEMETRY = "telemetry"  # a run's shared data (autopilot/telemetry.py): sent by autopilot/training.py, never synced
MEDIA = "media"  # a run's recording when its data isn't shared (autopilot/media.py): this machine's only
CODEX = "codex"  # the game's code as text (autopilot/codex.py): this machine's only, rebuilt by every run
MAX_FILE_BYTES = 64 * 1024 * 1024  # the most Aura accepts for one file
RECENT_RUNS = 20  # the runs a restore fetches (the most recent): the rest stay stored
BATCH = 100  # files per sign or delete request (Aura's limit)
WORKERS = 8  # transfers at once
TIMEOUT = 120.0  # seconds for one request or one file's transfer
RETRY_SECONDS = 2.0  # the first wait before a save or restore is tried again (2, 4, 8 s): the run waits for it
SAVE_ROUNDS = 3  # a save's tries at catching up with folders saving at the same time, before it sends what it can
# Beside the projects' folders, like memory.RESET_BACKUP: a sync's staging, and the files a sync replaced that exist
# nowhere else (<memory>/.restore-backup/<project>/<time>/). Autopilot never reads either
RESTORE_STAGING = ".restore-staging"
RESTORE_BACKUP = ".restore-backup"


class SyncError(Exception):
    """A sync that failed. passing: whether trying again soon may work (an answer in retry.STATUSES)."""

    def __init__(self, message, passing=False):
        super().__init__(message)
        self.passing = passing


def md5_of(path):
    """The file's MD5, base64 (as the storage reports it)."""
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return base64.b64encode(h.digest()).decode()


def synced_files(folder):
    """{relative path: full path} of the files that are synced: memory.files() less the local-only ones and the
    codex."""
    return {rel: full for rel, full in memory.files(folder).items() if is_synced(rel)}


def is_synced(rel):
    """Whether a path is one a sync moves, in either direction."""
    return rel not in LOCAL_ONLY and not is_telemetry(rel) and not is_codex(rel)


def is_codex(rel):
    """Whether a path is in the codex (codex/...)."""
    return rel.split("/")[0] == CODEX


def is_telemetry(rel):
    """Whether a path is in a run's telemetry folder (runs/<id>/telemetry/...) or is its recording when not shared
    (runs/<id>/media/..., kept on this machine for the plugin's report)."""
    parts = rel.split("/")
    return len(parts) >= 4 and parts[0] == "runs" and parts[2] in (TELEMETRY, MEDIA)


def merge_notes(stored, local):
    """notes.md changed on both sides, as bytes: Aura's copy with the lines only this folder has put in, in this
    folder's order, each after the nearest line before it that both have (at the end when there is none). The same
    inputs give the same bytes, a line both have is never repeated, and merging an already merged copy changes nothing.
    None when one isn't text (kept as a conflict instead)."""
    try:
        merged, ours = stored.decode("utf-8").splitlines(), local.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        return None
    have = {line.rstrip() for line in merged}
    at, blanks, added = len(merged), [], False
    for line in ours:
        key = line.rstrip()
        if not key:
            blanks.append(line)
        elif key in have:
            blanks = []
            at = next((i + 1 for i, m in enumerate(merged) if m.rstrip() == key), at)
        else:
            merged[at:at] = [*blanks, line]
            at += len(blanks) + 1
            blanks, added = [], True
            have.add(key)
    return "\n".join(merged).encode("utf-8") + b"\n" if added else stored


def conflict_path(rel, md5):
    """Where this folder's copy of a file changed on both sides goes: a run's folder, so it is synced, listed for the
    next run (by its prompt.txt) and reset like one, named by what it holds, so the same conflict never makes two."""
    return f"runs/conflict-{base64.b64decode(md5).hex()[:8]}/{rel}"


def recent(paths, keep=RECENT_RUNS):
    """The paths a sync fetches: all but the runs older than the `keep` most recent (runs/<id>/..., by id, which
    starts with the run's time)."""
    runs = sorted({p.split("/")[1] for p in paths if p.startswith("runs/") and p.count("/") >= 2}, reverse=True)
    dropped = set(runs[keep:])
    return [p for p in paths if not (p.startswith("runs/") and p.count("/") >= 2 and p.split("/")[1] in dropped)]


class MemorySync:
    """The memory of the project a run is for. With a run's token Aura picks the project from the run; with an install
    token, `project` names it (its key: the plugin's project id, else its name). `copy` names the stored copy: its
    SYNC_FILE, and what a reset mark lists once the reset reached it."""

    def __init__(self, url, run_token, http=None, say=None, project=None, copy="aura", sync_file=SYNC_FILE,
                 sleep=None):
        self.sleep = sleep  # (between tries: retry.pause unless given)
        self.url = url
        self.headers = {"Authorization": f"Bearer {run_token}"}
        self.http = http or requests.Session()
        self.say = say or (lambda text: None)
        self.project = project
        self.copy = copy
        self.sync_file = sync_file

    # -- Aura's API -------------------------------------------------------------------------------------------

    def _call(self, body):
        body = {**body, "project": self.project} if self.project else body
        r = self.http.post(self.url, json=body, headers=self.headers, timeout=TIMEOUT)
        if r.status_code != 200:
            raise SyncError(f"HTTP {r.status_code}: {r.text[:300]}", passing=r.status_code in retry.STATUSES)
        return r.json()

    def _list(self):
        """({path: md5} of what Aura stores for the project, whether that is all of it)."""
        answer = self._call({"op": "list"})
        return {f["path"]: f.get("md5") for f in answer.get("files", [])}, not answer.get("truncated")

    def listing(self):
        """{path: md5} of what Aura stores for the project."""
        return self._list()[0]

    def _transfer(self, signed, work):
        """work(signed file): True, or (why it failed, its HTTP status)."""
        with futures.ThreadPoolExecutor(WORKERS) as pool:
            for done in pool.map(work, signed):
                if done is not True:
                    raise SyncError(done[0], passing=done[1] in retry.STATUSES)

    def _put_all(self, items):
        """items: [(path, full path, md5)]."""
        for i in range(0, len(items), BATCH):
            batch = items[i:i + BATCH]
            put = [{"path": p, "md5": m, "size": os.path.getsize(full)} for p, full, m in batch]
            fulls = {p: full for p, full, _ in batch}
            signed = self._call({"op": "sign", "put": put}).get("put", [])

            def send(s):
                with open(fulls[s["path"]], "rb") as f:
                    r = requests.put(s["url"], data=f.read(), headers=s.get("headers") or {}, timeout=TIMEOUT)
                return True if r.status_code in (200, 201) else (f"{s['path']}: HTTP {r.status_code}: {r.text[:200]}",
                                                                  r.status_code)

            self._transfer(signed, send)

    def _get_all(self, paths, folder):
        for i in range(0, len(paths), BATCH):
            signed = self._call({"op": "sign", "get": paths[i:i + BATCH]}).get("get", [])
            if sorted(s["path"] for s in signed) != sorted(paths[i:i + BATCH]):
                raise SyncError("Aura signed other files than asked")

            def fetch(s):
                r = requests.get(s["url"], timeout=TIMEOUT)
                if r.status_code != 200:
                    return f"{s['path']}: HTTP {r.status_code}", r.status_code
                full = _inside(folder, s["path"])
                os.makedirs(os.path.dirname(full), exist_ok=True)
                with open(full, "wb") as f:
                    f.write(r.content)
                return True

            self._transfer(signed, fetch)

    def _delete_all(self, paths):
        for i in range(0, len(paths), BATCH):
            self._call({"op": "delete", "paths": paths[i:i + BATCH]})

    # -- the two steps ----------------------------------------------------------------------------------------

    def restore(self, folder):
        """Before a run: bring in what other folders stored since this one last synced, passing on a reset made here
        first. All or nothing: on a failure the folder stays as it was and the next sync tries again. Returns how many
        files were fetched (0: nothing to do). Never raises."""

        def attempt():
            self.pass_on_reset(folder)
            fetched = self._reconcile(folder, *self._list())
            if fetched:
                print(f"Memory restored from Aura: {fetched} files")
            return fetched

        try:
            return retry.call(attempt, "fetching the memory from Aura", RETRY_SECONDS, sleep=self.sleep)
        except Exception as e:  # noqa: BLE001 - the run goes on from the memory this machine has
            print(f"Couldn't restore the memory from Aura (the next run tries again): {type(e).__name__}: {e}")
            return 0

    def _reconcile(self, folder, stored, complete=True):
        """Bring the folder up to `stored` (the module's table). What it leaves for a save to send: files changed only
        here. complete: whether `stored` is all of it (else nothing is taken as removed). Returns how many files were
        fetched; raises what failed, with the folder as it was."""
        record = _sync_record(folder, self.sync_file)
        synced = _read_baseline(folder, self.sync_file)
        if not stored and not synced:
            return 0
        agreed = record.get("version") == SYNC_VERSION
        reset_id = stored.get(RESET_ID) or record.get("reset")
        baseline, reset = synced, []
        if reset_id != record.get("reset"):
            reset = [p for p in synced if is_learned(p) and os.path.exists(_inside(folder, p))]
            baseline = {p: m for p, m in synced.items() if not is_learned(p)}

        files = {p: m for p, m in stored.items() if is_synced(p)}
        fetchable = set(recent(sorted(files)))
        after = dict(baseline)
        take, merge, conflict, replace, removed = [], [], [], [], []
        for p, m in files.items():
            if m is None or m == baseline.get(p):
                continue
            full = _inside(folder, p)
            if p in reset or not os.path.exists(full):
                if p in fetchable:
                    take.append(p)
                else:
                    after.pop(p, None)
                continue
            if os.path.getsize(full) > MAX_FILE_BYTES:
                continue
            local = md5_of(full)
            if local == m:
                after[p] = m
            elif agreed and local == baseline.get(p):
                take.append(p)
            elif p == NOTES:
                merge.append(p)
            elif is_learned(p):
                conflict.append(p)
            else:
                replace.append(p)
        if not stored:
            after = {}
        elif complete:
            for p, m in baseline.items():
                if p not in stored:
                    after.pop(p)
                    full = _inside(folder, p)
                    if os.path.exists(full) and md5_of(full) == m:
                        removed.append(p)

        wanted = take + merge + conflict + replace
        if not (wanted or reset or removed) and after == synced and agreed and reset_id == record.get("reset"):
            return 0
        staging = None
        try:
            if wanted:
                self.say("Fetching what earlier runs learned about this game")
                parent = os.path.dirname(os.path.abspath(folder))
                os.makedirs(os.path.join(parent, RESTORE_STAGING), exist_ok=True)
                staging = tempfile.mkdtemp(prefix=os.path.basename(folder) + "-",
                                           dir=os.path.join(parent, RESTORE_STAGING))
                self._get_all(wanted, staging)
            _keep_aside(folder, reset, memory.RESET_BACKUP)
            if reset:
                self._note_reset(folder)
            _keep_aside(folder, removed + replace, RESTORE_BACKUP)
            for p in merge:
                with open(_inside(staging, p), "rb") as f:
                    theirs = f.read()
                with open(_inside(folder, p), "rb") as f:
                    ours = f.read()
                merged = merge_notes(theirs, ours)
                if merged is None:
                    conflict.append(p)
                    continue
                with open(_inside(staging, p), "wb") as f:
                    f.write(merged)
                take.append(p)
            for p in conflict:
                kept = conflict_path(p, md5_of(_inside(folder, p)))
                _place(_inside(folder, p), _inside(folder, kept))
                with open(_inside(folder, kept[:-len(p)] + "prompt.txt"), "w", encoding="utf-8") as f:
                    f.write(f"{p} as one folder had it when another changed it too (the other's is the one in use)")
            for p in take + conflict + replace:
                _place(_inside(staging, p), _inside(folder, p))
                after[p] = files[p]
            _write_baseline(folder, after, self.sync_file, reset=reset_id)
            return len(wanted)
        finally:
            if staging:
                shutil.rmtree(staging, ignore_errors=True)

    def pass_on_reset(self, folder):
        """A Reset memory made on this machine (memory.forget), passed on to this stored copy, once: Aura moves the
        project's learned files (notes.md, lib.py, runs/) aside, where no run reads them (op "reset"), never deleting
        them, and RESET_ID tells the other folders. The mark lists the copies that have it (`passed`), so the other
        copy gets it at its next sync. Raises SyncError when Aura couldn't (the mark stays as it was, and the next sync
        tries again)."""
        path = os.path.join(folder, memory.RESET_MARK)
        try:
            with open(path, encoding="utf-8") as f:
                mark = json.load(f)
        except (OSError, ValueError):
            return 0
        if not isinstance(mark, dict) or self.copy in (mark.get("passed") or []):
            return 0
        moved = int(self._call({"op": "reset"}).get("moved") or 0)
        reset_id = self._send_reset_id()
        mark["passed"] = [*(mark.get("passed") or []), self.copy]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(mark, f)
        _write_baseline(folder, {}, self.sync_file, reset=reset_id)  # (nothing learned is synced now)
        print(f"Memory reset with Aura too: {moved} files kept aside")
        return moved

    def _send_reset_id(self):
        """Store a new RESET_ID. Returns its MD5."""
        with tempfile.TemporaryDirectory() as temp:
            full = os.path.join(temp, RESET_ID)
            with open(full, "w", encoding="utf-8") as f:
                json.dump({"id": uuid.uuid4().hex, "at": time.time()}, f)
            md5 = md5_of(full)
            self._put_all([(RESET_ID, full, md5)])
        return md5

    def _note_reset(self, folder):
        """A reset that reached this folder through this copy: the folder's other copy gets it at its next sync."""
        path = os.path.join(folder, memory.RESET_MARK)
        try:
            with open(path, encoding="utf-8") as f:
                mark = json.load(f)
        except (OSError, ValueError):
            mark = None
        mark = mark if isinstance(mark, dict) else {"at": time.time()}
        mark["passed"] = [*(mark.get("passed") or []), self.copy]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(mark, f)

    def save(self, folder):
        """Send what changed since Aura's copy and remove what this machine removed. Returns (sent, removed); never
        raises."""
        if not os.path.isdir(folder):
            return 0, 0
        try:
            return retry.call(lambda: self._save(folder), "saving the memory to Aura", RETRY_SECONDS, sleep=self.sleep)
        except Exception as e:  # noqa: BLE001 - the local copy stays, and the next run sends it
            print(f"Couldn't save the memory to Aura: {type(e).__name__}: {e}")
            return 0, 0

    def _save(self, folder):
        """One try of save(): raises what failed."""
        self.pass_on_reset(folder)
        for _ in range(SAVE_ROUNDS):  # (a fetch takes time: list again, right before sending)
            stored, complete = self._list()
            if not self._reconcile(folder, stored, complete):
                break
        local = synced_files(folder)
        baseline = _read_baseline(folder, self.sync_file)
        changed = []
        for rel, full in sorted(local.items()):
            if os.path.getsize(full) > MAX_FILE_BYTES or stored.get(rel) != baseline.get(rel):
                continue  # (too big to store, or another folder got there first: the next sync merges it)
            m = md5_of(full)
            if stored.get(rel) != m:
                changed.append((rel, full, m))
        # (and any codex an older Autopilot stored, from whichever machine: the codex is never kept with Aura)
        gone = sorted({p for p, m in baseline.items() if p not in local and p in stored and stored[p] == m}
                      | {p for p in stored if is_codex(p)})
        if changed or gone:
            self.say("Saving what this run learned")
        self._put_all(changed)
        self._delete_all(gone)
        # What both now agree on. A file another folder overwrote in the meantime stays as it was, so the next sync
        # merges it. A later save removes from Aura only what is in this list and gone from here, never what other
        # folders added
        kept = self.listing() if changed else {}
        for p in gone:
            baseline.pop(p, None)
        baseline.update({rel: m for rel, _, m in changed if kept.get(rel) == m})
        _write_baseline(folder, baseline, self.sync_file, reset=_sync_record(folder, self.sync_file).get("reset"))
        print(f"Memory saved to Aura: {len(changed)} files sent, {len(gone)} removed")
        return len(changed), len(gone)


def _inside(folder, rel):
    """rel's full path under folder; refuses one that would leave it (a path from Aura is still checked)."""
    full = os.path.normpath(os.path.join(folder, *rel.split("/")))
    root = os.path.normpath(folder)
    if os.path.commonpath([full, root]) != root or full == root:
        raise SyncError(f"not a path inside the memory folder: {rel!r}")
    return full


def is_learned(rel):
    """Whether a path is something runs learned (memory.LEARNED: notes.md, lib.py, runs/...)."""
    return rel in memory.LEARNED or ("/" in rel and rel.split("/")[0] in memory.LEARNED)


def _place(source, target):
    """Move a file, making the target's folder."""
    os.makedirs(os.path.dirname(target), exist_ok=True)
    os.replace(source, target)


def _keep_aside(folder, paths, backups):
    """Move files out of the memory folder to <memory>/<backups>/<project>/<time>/, which Autopilot never reads."""
    backup = os.path.join(os.path.dirname(os.path.abspath(folder)), backups, os.path.basename(folder),
                          time.strftime("%Y%m%d-%H%M%S"))
    for p in paths:
        _place(_inside(folder, p), _inside(backup, p))


def _sync_record(folder, sync_file):
    try:
        with open(os.path.join(folder, sync_file), encoding="utf-8") as f:
            record = json.load(f)
        return record if isinstance(record, dict) else {}
    except (OSError, ValueError):
        return {}


def _read_baseline(folder, sync_file=SYNC_FILE):
    try:
        return dict(_sync_record(folder, sync_file).get("files") or {})
    except (TypeError, ValueError):
        return {}


def _write_baseline(folder, files, sync_file=SYNC_FILE, reset=None):
    """What this folder and the stored copy agree on; `reset`: the MD5 of the RESET_ID it has seen."""
    os.makedirs(folder, exist_ok=True)
    tmp = os.path.join(folder, sync_file + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"version": SYNC_VERSION, "files": files, **({"reset": reset} if reset else {})}, f)
    os.replace(tmp, os.path.join(folder, sync_file))


def install_memory(project, say=None):
    """The memory copy kept for this install, for a run with the user's own keys that the user shares: Aura's memory
    endpoint beside the shared data's (AUTOPILOT_MEMORY_URL names another), with this machine's install token.
    project: the project's key (the plugin's project id, else its name). None when there is no install token."""
    from . import training

    url = (os.environ.get("AUTOPILOT_MEMORY_URL") or "").strip() or training.endpoint().rsplit("/", 1)[0] + "/memory"
    try:
        token = training.install_token()
    except training.UploadError as e:
        print(f"note: the memory isn't kept with Aura this run: {e}")
        return None
    return MemorySync(url, token, say=say, project=project, copy="install", sync_file=INSTALL_SYNC_FILE)
