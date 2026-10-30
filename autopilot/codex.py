"""A project's codex: the game's code and data as plain text, for the agent to research (docs/architecture.md).

Which of the project's files go into it depends on the engine (autopilot.engines): for Unreal, the plugin's
AutopilotCodex commandlet writes blueprints/, input/, data/, assets.txt and maps.txt beside the C++ source and config;
for Unity and Godot, their scripts, settings and scenes as text. This module fetches them from the plugin (or copies
them from this machine's disk) and adds a snapshot of the running level (level.txt) and the index (README.md).
"""

import glob
import gzip
import hashlib
import json
import os
import re
import time

from . import bridge, engines

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def default_memory(here=HERE):
    """memory/ in the repository when Autopilot runs from a checkout of it; installed (a wheel: `uv tool install`, the
    standalone installer, the Aura app), Autopilot/memory in the user settings folder, beside brain.json."""
    if os.path.exists(os.path.join(here, "pyproject.toml")) and os.path.isdir(os.path.join(here, "autopilot")):
        return os.path.join(here, "memory")
    return bridge.brain_file("memory")


# Where Autopilot keeps what it learns about each game (autopilot/memory.py): AUTOPILOT_MEMORY, else default_memory()
MEMORY = os.environ.get("AUTOPILOT_MEMORY") or default_memory()


def project_dir(project):
    """A project's memory folder."""
    return os.path.join(MEMORY, project)


def project_of(hello):
    """The memory folder's name for the game a plugin's hello is from: the project's name, unless the folder of that
    name is another project's. Then, when the plugin sends the project's id (project_id: Unreal's ProjectID, the
    GUID the Godot plugin keeps), the name with the id's first 8 digits (or all of them, if those are taken too).
    A folder records the id it is for (project_id in its project.json); one from before ids were recorded is the
    first project's that comes with one. Without an id, the name, as before."""
    hello = hello or {}
    name = hello.get("project") or "game"
    pid = str(hello.get("project_id") or "").strip().lower()
    if not pid:
        return name
    digits = re.sub(r"[^0-9a-z]", "", pid)
    for folder in dict.fromkeys((name, f"{name}-{digits[:8]}", f"{name}-{digits}")):
        file = os.path.join(project_dir(folder), "project.json")
        try:
            with open(file, encoding="utf-8") as f:
                info = json.load(f)
        except (OSError, ValueError):
            info = {}
        owner = info.get("project_id")
        if owner == pid:
            return folder
        if not owner:  # (a new folder, or one from before ids were recorded: this project's from now on)
            os.makedirs(project_dir(folder), exist_ok=True)
            with open(file, "w", encoding="utf-8") as f:
                json.dump({**info, "project_id": pid}, f, indent=1)
            return folder
    return f"{name}-{digits}"  # (unreachable unless the full id's folder is another id's)


class Codex:
    """A project's codex folder (memory/<project>/codex/)."""

    def __init__(self, root):
        self.root = root
        self._bp = None

    @staticmethod
    def for_project(project):
        return Codex(os.path.join(project_dir(project), "codex"))

    def exists(self):
        return os.path.isdir(self.root) and bool(os.listdir(self.root))

    def blueprints(self):
        """{Blueprint name: its codex file}"""
        if self._bp is None:
            self._bp = {}
            for f in glob.glob(os.path.join(self.root, "blueprints", "**", "*.txt"), recursive=True):
                self._bp[os.path.splitext(os.path.basename(f))[0]] = f
        return self._bp

    def class_paths(self, name):
        """Generated class paths for a Blueprint name (BP_Door -> /Game/.../BP_Door.BP_Door_C)."""
        name = name[:-2] if name.endswith("_C") else name
        f = self.blueprints().get(name)
        if not f:
            return []
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                head = fh.read(2000)
        except OSError:
            return []
        m = re.search(r"generated class:?\s*(/[^\s),]+)", head, re.I)
        if m:
            return [m.group(1)]
        rel = os.path.relpath(f, os.path.join(self.root, "blueprints"))[:-4].replace(os.sep, "/")
        return [f"/Game/{rel}.{name}_C"]

    def modules(self):
        src = os.path.join(self.root, "source")
        if not os.path.isdir(src):
            return []
        return sorted(d for d in os.listdir(src) if os.path.isdir(os.path.join(src, d)))


def snapshot_level(game, codex_root):
    """level.txt: every actor in the running level, grouped by kind, with where it is (from the live game)."""
    name_of = engines.of(game).name_of
    actors = game.all_actors()
    locs = game.locs(actors)
    groups = {}
    for a in actors:
        kind = re.sub(r"(_C)?(_\d+)*(@\d+)?$", "", name_of(a))  # (Unity numbers same-named objects Name@2)
        groups.setdefault(kind, []).append(a)
    lines = [f"# Actors in the running level ({len(actors)}), t={game.t:.1f}, by kind",
             f"# player pawn: {game.pawn}", ""]
    me = locs.get(game.pawn)
    for kind in sorted(groups):
        items = groups[kind]
        lines.append(f"{kind} ({len(items)}):")
        for a in items[:40]:
            v = locs.get(a)
            where = f" at {v.rounded()}" + (f" ({v.dist(me):.0f} from player)" if me and v else "") if v else ""
            lines.append(f"  {a}{where}")
        if len(items) > 40:
            lines.append(f"  ... {len(items) - 40} more")
    with open(os.path.join(codex_root, "level.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    # The input mappings active now (the plugin reads the player's mapping contexts)
    controls = game.context(fresh=True).get("controls") or []
    if controls:
        with open(os.path.join(codex_root, "controls.txt"), "w", encoding="utf-8") as f:
            f.write("# Input actions mapped for the player now: action (path) <- keys\n")
            for c in controls:
                f.write(f"{c.get('name')} ({c.get('action')}) {'button' if c.get('digital') else 'axis'} <- "
                        f"{', '.join(c.get('keys') or [])}\n")
    return len(actors)


FETCH_INDEX = ".fetched.json"  # {project-relative path: md5} of the files fetched from the plugin


def fetch_from_plugin(game, codex_root, export="auto", batch_bytes=512_000):
    """The game's code and asset export, fetched from the plugin (codex_manifest / codex_read). Only files that changed
    since the last fetch are read; which files go where is the game's engine's (Engine.codex_path).
    Returns {"files", "fetched", "removed", "export"}, or None when the plugin has no such ops (an older build)."""
    from .kit.game import GameError

    codex_path = engines.of(game).codex_path
    try:
        manifest = game.request("codex_manifest", {"export": export}, timeout=900)
    except GameError as e:
        if "unknown op" in str(e).lower():
            return None
        raise
    index_file = os.path.join(codex_root, FETCH_INDEX)
    try:
        with open(index_file, encoding="utf-8") as f:
            have = json.load(f)
    except (OSError, ValueError):
        have = {}
    listed = {f["path"]: f.get("hash") for f in manifest.get("files", []) if codex_path(f.get("path"))}
    wanted = [p for p, h in listed.items()
              if have.get(p) != h or not os.path.exists(os.path.join(codex_root, codex_path(p)))]
    fetched = 0
    while wanted:
        batch, size = [], 0
        sizes = {f["path"]: int(f.get("size") or 0) for f in manifest.get("files", [])}
        for p in wanted:
            if batch and size + sizes.get(p, 0) > batch_bytes:
                break
            batch.append(p)
            size += sizes.get(p, 0)
        res = game.request("codex_read", {"paths": batch, "max_bytes": max(batch_bytes, size)}, timeout=120) or {}
        got = res.get("files") or {}
        for p, text in got.items():
            dest = codex_path(p)
            if p not in listed or dest is None or not isinstance(text, str):
                continue
            full = os.path.join(codex_root, dest)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w", encoding="utf-8", newline="") as f:
                f.write(text)
            have[p] = listed[p]
            fetched += 1
        skipped = set(res.get("missing") or []) | set(res.get("too_big") or [])
        progressed = set(got) | skipped
        if not progressed:
            break  # (the plugin sent nothing back: don't loop)
        wanted = [p for p in wanted if p not in progressed]
    removed = 0
    for p in [p for p in have if p not in listed]:
        dest = codex_path(p)
        if dest:
            try:
                os.remove(os.path.join(codex_root, dest))
                removed += 1
            except OSError:
                pass
        have.pop(p, None)
    with open(index_file, "w", encoding="utf-8") as f:
        json.dump(have, f)
    return {"files": len(listed), "fetched": fetched, "removed": removed, "export": manifest.get("export")}


def build(project, path=None, game=None, export=True, engine=None):
    """Make (or refresh) a project's codex. path: the project on this machine (Unreal: the .uproject; Unity, Godot:
    the folder), for its files and the commandlet; game: the live game, for level.txt; engine: the game's engine
    (else the game's, else the one path is a project of). Returns the Codex."""
    eng = engine or (engines.of(game) if game is not None else None)
    if path:
        found, canonical = engines.find_project(path, eng.name if eng else None)
        path, eng = canonical or path, eng or found
    eng = eng or engines.get()
    codex = Codex.for_project(project)
    os.makedirs(codex.root, exist_ok=True)
    fetched = None
    if game is not None:
        # The plugin sends the code and the asset export (re-exported when assets changed)
        try:
            fetched = fetch_from_plugin(game, codex.root, export="auto" if export else "skip")
        except Exception as e:
            print(f"codex: couldn't fetch the code from the plugin: {e}")
        if fetched is not None:
            print(f"codex: {fetched['files']} files from the plugin ({fetched['fetched']} new or changed, "
                  f"{fetched['removed']} gone); export: {fetched.get('export')}")
    if path and fetched is None:
        # (a plugin without the codex ops: read the project's files directly)
        eng.build_local(path, codex.root, export=export)
    if game is not None:
        try:
            snapshot_level(game, codex.root)
        except Exception as e:  # the level snapshot helps but isn't needed
            print(f"codex: level snapshot failed: {e}")
    eng.write_index(codex.root, project)
    return codex


# -- what changed since the last run --------------------------------------------------------------------------------

# The codex the last run worked from, so the next one is told what changed since (autopilot/agents/session.py): in the
# project's folder beside notes.md, so it is kept with the memory, and only paths and hashes, never the code
SEEN_FILE = ".codex-seen.json.gz"
# The hashes fingerprint() worked out, by file size and time: only files rewritten since are read again (a big
# project's codex has thousands of files, and reading them all would hold up every run's start)
HASH_CACHE = ".hashes.json"
# Files at the codex's top rewritten for every run whatever changed (the index, the export's summary with its timing,
# the level and controls of the moment, the fetch's own index, the hashes): never counted as changes
VOLATILE = ("README.md", "summary.txt", "level.txt", "controls.txt", FETCH_INDEX, HASH_CACHE)


def fingerprint(codex_root):
    """{path in the codex: a hash of its contents} of its files, the VOLATILE ones excepted."""
    cache_file = os.path.join(codex_root, HASH_CACHE)
    try:
        with open(cache_file, encoding="utf-8") as f:
            cache = json.load(f)
    except (OSError, ValueError):
        cache = {}
    found, fresh = {}, {}
    for folder, _, names in os.walk(codex_root):
        for name in names:
            full = os.path.join(folder, name)
            rel = os.path.relpath(full, codex_root).replace(os.sep, "/")
            if rel in VOLATILE:
                continue
            try:
                st = os.stat(full)
                stamp = [st.st_size, st.st_mtime_ns]
                known = cache.get(rel)
                if isinstance(known, list) and known[:2] == stamp:
                    digest = known[2]
                else:
                    h = hashlib.md5()
                    with open(full, "rb") as f:
                        for chunk in iter(lambda: f.read(1 << 20), b""):
                            h.update(chunk)
                    digest = h.hexdigest()[:12]
            except OSError:
                continue
            found[rel] = digest
            fresh[rel] = [*stamp, digest]
    if fresh != cache:
        try:
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(fresh, f, separators=(",", ":"))
        except OSError:
            pass
    return found


def seen(project_root):
    """{"run", "at", "files", "pending"}: the codex the last run worked from (its run id, when it ended, its
    fingerprint()), and the changes to files notes.md or lib.py name that no run has checked yet ({path: {"since",
    "kind"}}, autopilot/agents/session.py); None before any run recorded one."""
    try:
        with gzip.open(os.path.join(project_root, SEEN_FILE), "rt", encoding="utf-8") as f:
            record = json.load(f)
    except (OSError, ValueError, EOFError):
        return None
    return record if isinstance(record, dict) and isinstance(record.get("files"), dict) else None


def record_seen(project_root, run_id, files, pending=None):
    """Note `files` (a fingerprint()) as the codex run_id worked from, and the changes still to check (`pending`): the
    next run's changes are counted from it."""
    tmp = os.path.join(project_root, SEEN_FILE + ".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as f:
        json.dump({"run": run_id, "at": round(time.time()), "files": files, "pending": pending or {}}, f,
                  separators=(",", ":"))
    os.replace(tmp, os.path.join(project_root, SEEN_FILE))


def changes(before, now):
    """{"added", "changed", "removed"}: sorted paths, between two fingerprints."""
    return {"added": sorted(p for p in now if p not in before),
            "changed": sorted(p for p in now if p in before and now[p] != before[p]),
            "removed": sorted(p for p in before if p not in now)}
