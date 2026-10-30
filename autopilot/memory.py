"""What Autopilot remembers about each game between runs.

A project's memory is the folder Autopilot works in, ``memory/<project>/``:

- ``notes.md``: facts earlier runs verified (controls, mechanics, pitfalls)
- ``lib.py``: harness code earlier runs found reusable
- ``runs/<run id>/``: each run's harness versions, traces and report
- ``codex/``: the text copy of the game's code (rebuilt as needed, so it doesn't count as memory)
- ``.codex-seen.json.gz``: the codex the last run worked from, as paths and hashes only (autopilot/codex.py), so the
  next run is told what changed since

The Autopilot tab shows a summary of it (the ``memory_state`` frame, docs/protocol.md); its Reset memory
button forgets what runs learned (``forget``): the files are moved aside, never deleted.
"""

import json
import os
import time

# Folders that are a run's scratch, never memory: Claude Code's own folder and Python's bytecode caches
SCRATCH = (".agent", "__pycache__")


def files(root):
    """{path relative to root, with forward slashes: full path} of every file under root, scratch folders excepted."""
    found = {}
    for folder, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SCRATCH]
        for name in names:
            full = os.path.join(folder, name)
            found[os.path.relpath(full, root).replace(os.sep, "/")] = full
    return found


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


# What forget() moves aside: what runs learned. The codex (it comes from the project's files) and project.json stay.
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


def forget(folder):
    """Reset a game's memory, so the next run starts as on a new game: what earlier runs learned is moved, not
    deleted, to <memory>/.reset-backup/<project>/<time>/, which Autopilot never reads; and the reset is noted
    (RESET_MARK) so the memory kept with Aura is reset too, its files kept aside there as well. Returns how many files
    were moved.

    All or nothing: notes.md, lib.py and runs/ are each renamed in one step (never copied), and when one can't be
    (InUse), those already moved are put back, so the memory stays as it was and no reset is noted."""
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
        with open(os.path.join(folder, RESET_MARK), "w", encoding="utf-8") as f:
            json.dump({"at": time.time(), "backup": os.path.basename(backup)}, f)
    return moved
