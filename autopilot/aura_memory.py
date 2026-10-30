"""A game's memory kept with Aura, for runs through Aura (autopilot/aura.py): the same files on every machine of the
team.

What runs learned in the memory folder (memory/<project>/: notes.md, lib.py, runs/) is copied to Aura's storage after
every run and copied back before a run on a machine that has none of it (a new machine, a reinstall). The codex
(codex/, the game's code as text) never is: every run rebuilds it from the game, and Autopilot doesn't store whole
codebases. A save removes any codex an older Autopilot stored there. Aura keeps the memory per team and
project, behind the run's token (POST <AUTOPILOT_AURA_URL>/api/autopilot/memory, ops list / sign / delete): the brain
sends and fetches the files themselves over short-lived signed URLs Aura gives it, and never holds the storage's
credentials.

    restore(folder)  before the run: when the folder has never been synced and holds nothing runs learned, the
                     stored files (all but runs older than the RECENT_RUNS most recent) are fetched into it.
    save(folder)     after the run: files that are new or changed are sent, and files this machine had synced but
                     has since removed (Delete memory in the Autopilot tab, memory.forget) are removed from Aura too.

A restore is all or nothing: the files are fetched into a staging folder beside the projects' and moved in only once
every one has arrived. One that fails (Aura or the storage unreachable, a quota, the disk) leaves the folder as it was
and marks the restore pending in SYNC_FILE. While it is pending, the run goes on from the memory the machine has, a
save sends only files Aura doesn't hold (never one over a stored file this machine never had: the stored notes.md
stays the team's), and the next run's restore tries again, even over what that run learned: the stored notes.md and
lib.py replace this machine's, which are kept aside in <memory>/.restore-backup/<project>/<time>/ (never read).

Runs with the user's own keys keep it the same way when the user shares their runs (install_memory): with this
machine's install token (autopilot/training.py), in Aura's storage for this install, so its Reset memory reaches a
stored copy too. Each copy has its own SYNC_FILE, and a reset is passed on to each copy once (RESET_MARK says which have
it).

A save or a restore that fails for a reason that passes (the network, Aura or the storage busy or down:
autopilot/retry.py) is tried again at once, after short waits that double from RETRY_SECONDS (both happen while the run
starts or ends); one refused (a quota, a bad token) is reported as below.

What was synced last is in the folder's SYNC_FILE, so a cleared memory stays cleared. Never synced: the codex, SCRATCH
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

import requests

from . import memory, retry

SYNC_FILE = ".aura-sync.json"
INSTALL_SYNC_FILE = ".install-sync.json"  # the copy kept for this install (runs with the user's own keys)
# (project.json: where the project is on this machine; .training-sync.json: what shared run data was sent)
LOCAL_ONLY = ("project.json", SYNC_FILE, INSTALL_SYNC_FILE, ".training-sync.json", memory.RESET_MARK)
TELEMETRY = "telemetry"  # a run's shared data (autopilot/telemetry.py): sent by autopilot/training.py, never synced
MEDIA = "media"  # a run's recording when its data isn't shared (autopilot/media.py): this machine's only
CODEX = "codex"  # the game's code as text (autopilot/codex.py): this machine's only, rebuilt by every run
MAX_FILE_BYTES = 64 * 1024 * 1024  # the most Aura accepts for one file
RECENT_RUNS = 20  # the runs a restore fetches (the most recent): the rest stay stored
BATCH = 100  # files per sign or delete request (Aura's limit)
WORKERS = 8  # transfers at once
TIMEOUT = 120.0  # seconds for one request or one file's transfer
RETRY_SECONDS = 2.0  # the first wait before a save or restore is tried again (2, 4, 8 s): the run waits for it
# Beside the projects' folders, like memory.RESET_BACKUP: a restore's staging, and this machine's files a retried
# restore replaced (<memory>/.restore-backup/<project>/<time>/). Autopilot never reads either
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
    return {rel: full for rel, full in memory.files(folder).items()
            if rel not in LOCAL_ONLY and not is_telemetry(rel) and not is_codex(rel)}


def is_codex(rel):
    """Whether a path is in the codex (codex/...)."""
    return rel.split("/")[0] == CODEX


def is_telemetry(rel):
    """Whether a path is in a run's telemetry folder (runs/<id>/telemetry/...) or is its recording when not shared
    (runs/<id>/media/..., kept on this machine for the plugin's report)."""
    parts = rel.split("/")
    return len(parts) >= 4 and parts[0] == "runs" and parts[2] in (TELEMETRY, MEDIA)


def recent(paths, keep=RECENT_RUNS):
    """The paths a restore fetches: all but the runs older than the `keep` most recent (runs/<id>/..., by id, which
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

    def listing(self):
        """{path: md5} of what Aura stores for the project."""
        return {f["path"]: f.get("md5") for f in self._call({"op": "list"}).get("files", [])}

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
        """Fetch the stored memory into a folder that has never been synced and holds nothing runs learned, or whose
        last restore failed (pending). All or nothing: on a failure the folder stays as it was and the restore stays
        pending for the next run. Returns how many files were fetched (0: nothing to do). Never raises."""
        pending = _restore_pending(folder, self.sync_file)
        if not pending and (os.path.exists(os.path.join(folder, self.sync_file)) or any(memory.state(folder).values())):
            return 0

        def attempt():
            staging = None
            try:
                stored = self.listing()
                if not stored:
                    if pending:
                        _write_baseline(folder, {}, self.sync_file)  # (nothing to restore any more)
                    return 0
                fetched = recent(sorted(p for p in stored if not is_telemetry(p) and not is_codex(p)))
                # What this machine lacks, and (a retried restore, after a run went on without it) what runs learned
                # that it holds otherwise than Aura: the stored copy is the one earlier runs built on
                wanted = [p for p in fetched if not os.path.exists(_inside(folder, p))
                          or (is_learned(p) and md5_of(_inside(folder, p)) != stored[p])]
                if wanted:
                    self.say("Fetching what earlier runs learned about this game")
                    parent = os.path.dirname(os.path.abspath(folder))
                    os.makedirs(os.path.join(parent, RESTORE_STAGING), exist_ok=True)
                    staging = tempfile.mkdtemp(prefix=os.path.basename(folder) + "-",
                                               dir=os.path.join(parent, RESTORE_STAGING))
                    self._get_all(wanted, staging)
                    _move_in(staging, folder, wanted)
                # (what this machine now has: older runs it didn't fetch stay stored and are never removed from here)
                _write_baseline(folder, {p: stored[p] for p in fetched}, self.sync_file)
                print(f"Memory restored from Aura: {len(wanted)} files")
                return len(wanted)
            finally:
                if staging:
                    shutil.rmtree(staging, ignore_errors=True)

        try:
            return retry.call(attempt, "fetching the memory from Aura", RETRY_SECONDS, sleep=self.sleep)
        except Exception as e:  # noqa: BLE001 - the run goes on from the memory this machine has
            print(f"Couldn't restore the memory from Aura (the next run tries again): {type(e).__name__}: {e}")
            try:
                _write_baseline(folder, _read_baseline(folder, self.sync_file), self.sync_file, pending=True)
            except OSError as mark:
                print(f"Couldn't note the restore as pending: {mark}")
            return 0

    def pass_on_reset(self, folder):
        """A Reset memory made on this machine (memory.forget), passed on to this stored copy, once: Aura moves the
        project's learned files (notes.md, lib.py, runs/) aside, where no run reads them (op "reset"), never deleting
        them. The mark lists the copies that have it (`passed`), so the other copy gets it at its next save. Raises
        SyncError when Aura couldn't (the mark stays as it was, and the next save tries again)."""
        path = os.path.join(folder, memory.RESET_MARK)
        try:
            with open(path, encoding="utf-8") as f:
                mark = json.load(f)
        except (OSError, ValueError):
            return 0
        if not isinstance(mark, dict) or self.copy in (mark.get("passed") or []):
            return 0
        moved = int(self._call({"op": "reset"}).get("moved") or 0)
        mark["passed"] = [*(mark.get("passed") or []), self.copy]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(mark, f)
        _write_baseline(folder, {}, self.sync_file)  # (nothing learned is synced now)
        print(f"Memory reset with Aura too: {moved} files kept aside")
        return moved

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
        # (a restore that failed: this machine never had the stored files, so it sends only what Aura doesn't hold)
        pending = _restore_pending(folder, self.sync_file)
        local = synced_files(folder)
        stored = self.listing()
        changed = []
        for rel, full in sorted(local.items()):
            if os.path.getsize(full) > MAX_FILE_BYTES or (pending and rel in stored):
                continue
            m = md5_of(full)
            if stored.get(rel) != m:
                changed.append((rel, full, m))
        baseline = _read_baseline(folder, self.sync_file)
        # (and any codex an older Autopilot stored, from whichever machine: the codex is never kept with Aura)
        gone = sorted({p for p in baseline if p not in local and p in stored} | {p for p in stored if is_codex(p)})
        if changed or gone:
            self.say("Saving what this run learned")
        self._put_all(changed)
        self._delete_all(gone)
        # What this machine has synced: its own files that Aura now holds as they are here. A later save removes
        # from Aura only what was in this list and is gone from here, never what other machines added
        sent = {rel: m for rel, _, m in changed}
        if pending:  # (only what it sent: what Aura holds otherwise isn't this machine's to remove)
            _write_baseline(folder, sent, self.sync_file, pending=True)
        else:
            _write_baseline(folder, {rel: sent.get(rel) or stored[rel] for rel in local
                                     if rel in sent or rel in stored}, self.sync_file)
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


def _move_in(staging, folder, paths):
    """Move a restore's fetched files from staging into the memory folder. A file this machine has there already is
    moved aside first, to <memory>/.restore-backup/<project>/<time>/."""
    backup = os.path.join(os.path.dirname(os.path.abspath(folder)), RESTORE_BACKUP, os.path.basename(folder),
                          time.strftime("%Y%m%d-%H%M%S"))
    for p in paths:
        full = _inside(folder, p)
        if os.path.exists(full):
            kept = _inside(backup, p)
            os.makedirs(os.path.dirname(kept), exist_ok=True)
            os.replace(full, kept)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        os.replace(_inside(staging, p), full)


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


def _restore_pending(folder, sync_file=SYNC_FILE):
    """Whether this copy's last restore into the folder failed (and the next run tries again)."""
    return _sync_record(folder, sync_file).get("restore") == "pending"


def _write_baseline(folder, files, sync_file=SYNC_FILE, pending=False):
    os.makedirs(folder, exist_ok=True)
    tmp = os.path.join(folder, sync_file + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"files": files, **({"restore": "pending"} if pending else {})}, f)
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
