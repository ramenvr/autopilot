"""What Autopilot remembers about each game between runs.

A project's memory is the folder Autopilot works in, ``memory/<project>/``:

- ``notes.md``: facts earlier runs verified (controls, mechanics, pitfalls)
- ``lib.py``: harness code earlier runs found reusable
- ``runs/<run id>/``: each run's harness versions, traces and report
- ``codex/``: the text copy of the game's code (rebuilt as needed, so it doesn't count as memory)
- ``.codex-seen.json.gz``: the codex the last run worked from, as paths and hashes only (autopilot/codex.py), so the
  next run is told what changed since
- ``detectors.json``: the project's settings for the game's detectors, its muted findings above all
  (autopilot/detectors.py): the user's, not learned, so a reset keeps it

The Autopilot tab shows a summary of it (the ``memory_state`` frame, docs/protocol.md); its Reset memory
button forgets what runs learned (``forget``): the files are moved aside, never deleted.

One memory folder may be used by more than one agent service at once (two `autopilot serve`s, or one started by
another app with the same AUTOPILOT_MEMORY), and one agent service saves a run's memory while its next run starts. So
that they never corrupt a project's memory:

- every file Autopilot writes there is written whole, in one step (``write_atomic``: a temporary file beside it, then a
  rename): a reader, a run or a sync sees the old file or the new one, never half of one;
- what moves files around or keeps a record of them (a sync with a stored copy, autopilot/aura_memory.py, and a Reset
  memory) holds the project's lock (``locked``: LOCK, a file in its folder) while it does. Another waits for it, up to
  LOCK_WAIT_SECONDS; a sync that still can't have it is skipped (the next one catches up: memory is a copy), and a
  reset answers that the memory is in use. A lock whose holder is gone (its process ended, or LOCK_STALE_SECONDS
  passed) is taken over.

Runs themselves don't hold the lock: two runs on one game at the same time both read the memory and both write
notes.md and lib.py (each edit reads the file as it is then), and a sync merges what either wrote. Each run has a
folder of its own (``new_run``: two that start in the same second get 20261110-172422 and 20261110-172422-2).
"""

import contextlib
import json
import os
import socket
import tempfile
import time
import uuid

# Folders that are a run's scratch, never memory: Claude Code's own folder and Python's bytecode caches
SCRATCH = (".agent", "__pycache__")
# write_atomic's temporary files end so: never memory (a process killed while writing leaves one)
TEMP_SUFFIX = ".autopilot-tmp"
LOCK = ".memory.lock"  # a project's lock (locked): in its folder, never synced
LOCK_WAIT_SECONDS = 60.0  # how long locked() waits for another holder by default
LOCK_STALE_SECONDS = 3600.0  # a lock this old is taken over whoever holds it (no sync takes this long)
LOCK_POLL_SECONDS = 0.2
REPLACE_TRIES = 20  # write_atomic's tries at the rename (Windows refuses it while another process reads the file)


def files(root):
    """{path relative to root, with forward slashes: full path} of every file under root, scratch folders, the lock and
    temporary files excepted."""
    found = {}
    for folder, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SCRATCH]
        for name in names:
            if name == LOCK or name.endswith(TEMP_SUFFIX):
                continue
            full = os.path.join(folder, name)
            found[os.path.relpath(full, root).replace(os.sep, "/")] = full
    return found


def write_atomic(path, data, sleep=time.sleep):
    """Write `data` (bytes, or text written as UTF-8 as it is) to `path` in one step: a temporary file beside it (its
    own name, so two writers never share one), then a rename over it. A reader sees the old file or the new one."""
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix="." + os.path.basename(path) + ".", suffix=TEMP_SUFFIX)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data.encode("utf-8") if isinstance(data, str) else data)
        for attempt in range(REPLACE_TRIES):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:  # (Windows: another process has the file open this moment)
                if attempt + 1 == REPLACE_TRIES:
                    raise
                sleep(0.05)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(tmp)
        raise


def new_run(folder, now=None):
    """A new run's id, with its folder (folder/runs/<id>/) made by this call: the time it starts, to the second
    (20261110-172422), with -2, -3, ... after it when another run started in the same second (another agent service
    on this memory folder) or a folder of that name came from elsewhere. Made in one step, so two runs never share a
    folder (their harness.py and traces would overwrite each other)."""
    runs = os.path.join(folder, "runs")
    os.makedirs(runs, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
    n = 1
    while True:
        run_id = stamp if n == 1 else f"{stamp}-{n}"
        try:
            os.mkdir(os.path.join(runs, run_id))
            return run_id
        except FileExistsError:
            n += 1


class Busy(OSError):
    """locked() waited, and another holder still had the project's lock. holder: what its lock file says ({"pid",
    "host", "at", "what"}; {} when it couldn't be read)."""

    def __init__(self, folder, holder):
        super().__init__(f"the memory of {os.path.basename(os.path.normpath(folder))} is in use "
                         f"({holder.get('what') or 'by another Autopilot'}, pid {holder.get('pid', '?')})")
        self.holder = holder


def _holder(path):
    """(what the lock file at path says, or {}, its age in seconds), or None when there is none."""
    try:
        age = time.time() - os.path.getmtime(path)
    except OSError:
        return None
    try:
        with open(path, encoding="utf-8") as f:
            said = json.load(f)
        return (said if isinstance(said, dict) else {}), age
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return {}, age  # (being written this moment, or damaged: only its age can make it stale)


def _stale(holder, age, alive):
    """Whether a lock's holder is gone: its process (on this machine) ended, or the lock is older than any sync."""
    if age > LOCK_STALE_SECONDS:
        return True
    pid = holder.get("pid")
    return isinstance(pid, int) and holder.get("host") == socket.gethostname() and not alive(pid)


@contextlib.contextmanager
def locked(folder, what="a memory sync", wait=LOCK_WAIT_SECONDS, sleep=time.sleep, alive=None):
    """Hold a project's lock (LOCK in its folder) while the block runs: across processes (any agent service using this
    memory folder) and across threads of one (it is never re-entered: one holder at a time). Waits up to `wait`
    seconds for another holder, taking over a stale one; then raises Busy. `what`: what the holder is doing, for one
    that waits to say."""
    if alive is None:
        from .processes import alive
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, LOCK)
    mine = {"pid": os.getpid(), "host": socket.gethostname(), "at": time.time(), "what": what,
            "id": uuid.uuid4().hex}
    deadline = time.monotonic() + wait
    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except (FileExistsError, PermissionError):  # (PermissionError: Windows, a lock being removed this moment)
            found = _holder(path)
            if found is not None and _stale(*found, alive):
                again = _holder(path)
                if again is not None and again[0] == found[0]:  # (not one another waiter took over meanwhile)
                    with contextlib.suppress(OSError):
                        os.remove(path)
                continue
            if time.monotonic() >= deadline:
                raise Busy(folder, found[0] if found else {}) from None
            sleep(LOCK_POLL_SECONDS)
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(mine, f)
        break
    try:
        yield
    finally:
        found = _holder(path)
        if found is not None and found[0].get("id") == mine["id"]:
            with contextlib.suppress(OSError):
                os.remove(path)


def state_of(rels):
    """A summary of a memory folder from its files' relative paths: ``{"runs": n, "notes": bool, "lib": bool}``.

    All empty means the next run starts as on a new game.
    """
    rels = [r.replace("\\", "/") for r in rels]
    runs = {r.split("/")[1] for r in rels if r.startswith("runs/") and r.count("/") >= 2}
    return {"runs": len(runs), "notes": "notes.md" in rels, "lib": "lib.py" in rels}


def state(folder):
    """state_of the memory folder at ``folder`` (a missing folder is empty)."""
    return state_of(files(folder) if os.path.isdir(folder) else {})


NOTES = ("notes.md", "lib.py")  # what a run writes for the next ones (beside its own folder in runs/)


def snapshot(folder):
    """{name: its bytes, or None} for notes.md and lib.py now: what learned() compares with."""
    taken = {}
    for name in NOTES:
        try:
            with open(os.path.join(folder, name), "rb") as f:
                taken[name] = f.read()
        except OSError:
            taken[name] = None
    return taken


def learned(folder, before, run_id):
    """What a run added to the memory since ``before`` (a snapshot()), as the result tells the plugin:
    ``{"updated": bool, "notes": bool, "lib": bool}``: notes.md or lib.py changed, or the run left a harness in
    runs/<run_id>/ that later runs read."""
    now = snapshot(folder)
    notes, lib = (now[n] is not None and now[n] != before.get(n) for n in NOTES)
    harness = os.path.isfile(os.path.join(folder, "runs", run_id, "harness.py"))
    return {"updated": notes or lib or harness, "notes": notes, "lib": lib}


# What forget() moves aside: what runs learned. The codex (it comes from the project's files), project.json and
# detectors.json (the user's own settings: what the project muted) stay.
LEARNED = ("notes.md", "lib.py", "runs")
# Where a reset puts them: beside the projects' folders, <memory>/.reset-backup/<project>/<time>/. Autopilot never reads
# it
RESET_BACKUP = ".reset-backup"
# A reset not yet passed on to the copy of the memory kept with Aura (autopilot/aura_memory.py): in the project's folder
RESET_MARK = ".memory-reset.json"


class InUse(OSError):
    """forget() couldn't move what runs learned: something has a file of it open (on Windows a folder can't move while
    a file in it is open). Nothing was moved. path: what couldn't move, relative to the project's folder."""

    def __init__(self, path, error):
        super().__init__(f"{path} is in use: {error}")
        self.path = path


FORGET_LOCK_SECONDS = 10.0  # how long a reset waits for a sync of the project's memory to finish (then: InUse)


def forget(folder, wait=FORGET_LOCK_SECONDS):
    """Reset a game's memory, so the next run starts as on a new game: what earlier runs learned is moved, not
    deleted, to <memory>/.reset-backup/<project>/<time>/, which Autopilot never reads; and the reset is noted
    (RESET_MARK) so the memory kept with Aura is reset too, its files kept aside there as well. Returns how many files
    were moved.

    All or nothing: notes.md, lib.py and runs/ are each renamed in one step (never copied), and when one can't be
    (InUse), those already moved are put back, so the memory stays as it was and no reset is noted. It holds the
    project's lock (locked) while it does, waiting up to `wait` seconds for a sync to finish; InUse (path: LOCK)
    when one is still going."""
    if not os.path.isdir(folder):
        return 0
    try:
        with locked(folder, "a memory reset", wait=wait):
            return _forget(folder)
    except Busy as e:
        raise InUse(LOCK, e) from e


def _forget(folder):
    names = [name for name in LEARNED if os.path.exists(os.path.join(folder, name))]
    if not names:
        return 0
    moved = sum(len(files(path)) if os.path.isdir(path) else 1 for path in (os.path.join(folder, n) for n in names))
    stamp = time.strftime("%Y%m%d-%H%M%S")
    kept = os.path.join(os.path.dirname(os.path.abspath(folder)), RESET_BACKUP, os.path.basename(folder))
    backup, n = os.path.join(kept, stamp), 1
    while os.path.exists(backup):  # (two resets in one second)
        backup, n = os.path.join(kept, f"{stamp}-{n}"), n + 1
    os.makedirs(backup)
    done = []
    try:
        for name in names:
            os.rename(os.path.join(folder, name), os.path.join(backup, name))
            done.append(name)
    except OSError as e:
        for name in reversed(done):
            os.rename(os.path.join(backup, name), os.path.join(folder, name))
        os.rmdir(backup)  # (empty again)
        raise InUse(names[len(done)], e) from e
    if moved:
        write_atomic(os.path.join(folder, RESET_MARK), json.dumps({"at": time.time(),
                                                                   "backup": os.path.basename(backup)}))
    return moved
