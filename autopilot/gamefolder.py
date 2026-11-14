"""A game's Autopilot folder: what Autopilot knows about a game, kept with the game so a machine that only has a build
of it (a QA machine, CI) plays it as well as the developer's.

    <game>/Autopilot/
        regressions/     CONFIRMED verifications that replay without a model (autopilot/regress.py), saved in the
                         memory when they're verified and copied here by an export
        codex/           the code index: the game's code, its Blueprints, scenes or data as text, which a run reads.
                         A build carries no source, so it is made from the source (the editor's runs, or
                         `autopilot codex --project <source>` headless, e.g. in the CI job that builds the game)
        memory/          what runs learned: notes.md, lib.py
        export.json      when and from where the folder was exported

`autopilot codex --project <source> --out <game>/Autopilot` writes regressions/, codex/, memory/ and export.json
(export()): nothing is written into the game's folder otherwise. A replay on a build reads regressions/ where it is.
A run on a build given the folder (`autopilot play ... --build <exe> --game-folder <game>/Autopilot`, or the QA window)
brings the codex and memory into this machine's memory of the game first (seed()):
- the codex, when this machine has none, or the folder's export is newer than the codex here;
- notes.md and lib.py, file by file: taken when this machine has none, or when the folder's copy changed since it was
  last taken here and the one here didn't (what this machine learned since is never overwritten; both changed: the
  one here is kept, and the run says so).
"""

import hashlib
import json
import os
import shutil
import time

CODEX, MEMORY, EXPORT = "codex", "memory", "export.json"
LEARNED = ("notes.md", "lib.py")
SEEDED = ".game-folder.json"  # (in the project's memory folder: what was last taken from a game folder)
# Codex files rewritten on this machine at every run, or bookkeeping of its own fetches: never exported
LOCAL = (".fetched.json", ".hashes.json", "level.txt", "controls.txt")


def resolve(path):
    """The game's Autopilot folder for a path given: the folder itself (it holds codex/, memory/, regressions/ or
    export.json), the game's folder holding Autopilot/, or a regressions folder inside one. None for none."""
    if not path:
        return None
    path = os.path.abspath(path)
    if not os.path.isdir(path):
        return None
    marks = (CODEX, MEMORY, "regressions", EXPORT)
    if any(os.path.exists(os.path.join(path, m)) for m in marks):
        return path
    if os.path.isdir(os.path.join(path, "Autopilot")):
        return os.path.join(path, "Autopilot")
    if os.path.basename(path) == "regressions":
        return os.path.dirname(path)
    return None


def regressions(folder):
    """The regressions folder of a game's Autopilot folder, or None when it has none."""
    path = os.path.join(folder, "regressions")
    return path if os.path.isdir(path) else None


def _hash(path):
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _newest(folder):
    """The newest modification time of the files in folder (LOCAL ones aside), or 0."""
    newest = 0.0
    for here, _, names in os.walk(folder):
        for name in names:
            if name in LOCAL:
                continue
            try:
                newest = max(newest, os.path.getmtime(os.path.join(here, name)))
            except OSError:
                pass
    return newest


def _has_codex(folder):
    return os.path.isdir(folder) and any(n not in LOCAL for _, _, names in os.walk(folder) for n in names)


def export(project_root, dest, project, source=None):
    """Write this machine's codex and learned memory of a project (its memory folder, project_root) into a game's
    Autopilot folder dest: codex/ (replaced whole), memory/notes.md and lib.py (those there are), regressions/ (its
    saved regressions: export_regressions), export.json. A Godot project's folder gets a .gdignore. Returns
    export.json's content."""
    os.makedirs(dest, exist_ok=True)
    codex = os.path.join(project_root, CODEX)
    files = 0
    if _has_codex(codex):
        target = os.path.join(dest, CODEX)
        staging = target + ".new"
        shutil.rmtree(staging, ignore_errors=True)
        shutil.copytree(codex, staging, ignore=shutil.ignore_patterns(*LOCAL, "__pycache__"))
        shutil.rmtree(target, ignore_errors=True)
        os.replace(staging, target)
        files = sum(len(names) for _, _, names in os.walk(target))
    learned = []
    for name in LEARNED:
        src = os.path.join(project_root, name)
        if os.path.isfile(src):
            os.makedirs(os.path.join(dest, MEMORY), exist_ok=True)
            shutil.copyfile(src, os.path.join(dest, MEMORY, name))
            learned.append(name)
    regressions = export_regressions(project_root, dest, project)
    game = os.path.dirname(os.path.abspath(dest))
    if os.path.isfile(os.path.join(game, "project.godot")) and not os.path.exists(os.path.join(dest, ".gdignore")):
        open(os.path.join(dest, ".gdignore"), "w").close()
    info = {"project": project, "exported": time.strftime("%Y-%m-%dT%H:%M:%S"), "exported_at": time.time(),
            "codex_files": files, "memory": learned, "regressions": regressions,
            **({"source": os.path.abspath(source)} if source else {})}
    with open(os.path.join(dest, EXPORT), "w", encoding="utf-8") as f:
        json.dump(info, f, indent=1)
    return info


def export_regressions(project_root, dest, project):
    """Copy the project's saved regressions (regress.folder: its memory's, or AUTOPILOT_REGRESSIONS') into dest's
    regressions/, one folder per run: a run already there is replaced, the others there (exported from another
    machine, or a teammate's) stay. Returns how many were copied."""
    from . import regress

    src = regress.folder(project, project_root)
    if not os.path.isdir(src):
        return 0
    target = os.path.join(dest, "regressions")
    copied = 0
    for reg in regress.listing(src):
        here = os.path.join(target, os.path.basename(os.path.normpath(reg)))
        staging = here + ".new"
        shutil.rmtree(staging, ignore_errors=True)
        shutil.copytree(reg, staging, ignore=shutil.ignore_patterns("__pycache__"))
        shutil.rmtree(here, ignore_errors=True)
        os.replace(staging, here)
        copied += 1
    return copied


def seed(project_root, folder):
    """Bring a game's Autopilot folder's codex and learned memory into this machine's memory of the project
    (project_root), as the module's docstring says. Returns what was done, as lines for the run's log."""
    said = []
    os.makedirs(project_root, exist_ok=True)
    record_file = os.path.join(project_root, SEEDED)
    record = _read_json(record_file)
    info = _read_json(os.path.join(folder, EXPORT))
    theirs = os.path.join(folder, CODEX)
    mine = os.path.join(project_root, CODEX)
    if _has_codex(theirs):
        exported = float(info.get("exported_at") or os.path.getmtime(theirs))
        # (an export already taken isn't taken again: copied, its files keep their older times)
        if not _has_codex(mine) or (exported != record.get("codex") and exported > _newest(mine)):
            staging = mine + ".new"
            shutil.rmtree(staging, ignore_errors=True)
            shutil.copytree(theirs, staging)
            shutil.rmtree(mine, ignore_errors=True)
            os.replace(staging, mine)
            said.append(f"the code index from {folder} ({info.get('exported') or 'its export'})")
        record["codex"] = exported
    taken = record.setdefault("learned", {})
    for name in LEARNED:
        src, here = os.path.join(folder, MEMORY, name), os.path.join(project_root, name)
        new = _hash(src)
        if new is None:
            continue
        last = taken.get(name)
        local = _hash(here)
        if local == new:
            taken[name] = new  # (the same here: in step with the folder from now on)
        elif local is None or (new != last and local == last):
            shutil.copyfile(src, here)
            taken[name] = new
            if local != new:
                said.append(f"{name} from {folder}")
        elif new != last and local != new:
            said.append(f"kept this machine's {name}: it changed here since it was taken from the game's folder, "
                        f"and the folder's changed too")
    with open(record_file, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=1)
    return said
