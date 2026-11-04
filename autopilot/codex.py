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
import shutil
import threading
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
        self.indexer = None  # an Indexer while (and after) the plugin's export streams in, else None

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


def fetch_from_plugin(game, codex_root, export="auto", batch_bytes=512_000, wait=True, first_map=None, follow=False):
    """The game's code and asset export, fetched from the plugin (codex_manifest / codex_read). Only files that changed
    since the last fetch are read; which files go where is the game's engine's (Engine.codex_path).
    wait False: a plugin that can answers at once while its export runs (export "running", with "done" and "total"),
    with the files written so far; files it doesn't list then stay (the export hasn't written them again yet).
    first_map: the level the run plays, which an export started now does first.
    follow: asking again about the export an earlier answer said runs: the plugin never starts one for this, so an
    export the user stopped stays stopped while the run finishes up.
    Returns {"files", "fetched", "removed", "export", "arrived": [(codex path, "new" or "changed")]}, or None when the
    plugin has no such ops (an older build)."""
    from .kit.game import GameError

    codex_path = engines.of(game).codex_path
    args = {"export": export}
    if not wait:
        args["wait"] = False
    if first_map:
        args["first_map"] = first_map
    if follow:
        args["follow"] = True
    try:
        manifest = game.request("codex_manifest", args, timeout=900)
    except GameError as e:
        if "unknown op" in str(e).lower():
            return None
        raise
    running = bool((manifest.get("export") or {}).get("running"))
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
    arrived = []
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
            arrived.append((dest, "changed" if os.path.exists(full) else "new"))
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
    for p in [p for p in have if p not in listed and not running]:
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
    return {"files": len(listed), "fetched": fetched, "removed": removed, "export": manifest.get("export"),
            "arrived": arrived}


# What the export brought while a run worked (Indexer): in the project's folder beside codex/, started over by each
# run. status.json says how far the export is; batch-N.txt lists the files of each fetch that brought some
INDEXING = ".indexing"
POLL_SECONDS = 3.0  # how often the plugin is asked for what its export has written since
GONE_TRIES = 5  # failed asks in a row (the game gone, the editor busy) before the indexer gives up
ASIDE_TRIES = 3  # tries of a piece of background work (the level's snapshot) before it is left out of the run
ASIDE_RETRY_SECONDS = 20.0


def digest(codex_root, rel):
    """One line on what a codex file is, from its text (no model): a Blueprint's parent, events and functions; a data
    table's rows and columns; a C++ file's classes; else its first line. Every name the file has is in it."""
    try:
        with open(os.path.join(codex_root, rel), encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return "(gone)"
    first = next((line.strip() for line in text.splitlines() if line.strip()), "(empty)")
    if rel.startswith("blueprints/") and rel.endswith(".txt"):
        parent = re.search(r"^Parent:\s*(\S+)", text, re.M)
        events = re.findall(r"^\s+(?:Custom )?Event ([^(\n]+)\(", text, re.M)
        functions = re.findall(r"^Function ([^(\n]+)\(", text, re.M)
        parts = [first.split("  (")[-1].rstrip(")") if "  (" in first else "Blueprint"]
        if parent:
            parts.append("parent " + parent.group(1).rsplit(".", 1)[-1].removesuffix("_C"))
        if events:
            parts.append("events: " + ", ".join(dict.fromkeys(e.strip() for e in events)))
        if functions:
            parts.append("functions: " + ", ".join(dict.fromkeys(f.strip() for f in functions)))
        return "; ".join(parts)
    if rel.endswith(".csv"):
        lines = text.splitlines()
        columns = lines[0].split(",")[1:] if lines else []
        return f"data table, {max(0, len(lines) - 1)} rows; columns: {', '.join(columns)}"
    if rel.startswith("source/"):
        classes = re.findall(r"^\s*(?:class|struct)\s+(?:\w+_API\s+)?(\w+)\s*(?::|\{|$)", text, re.M)
        if classes:
            return "C++: " + ", ".join(dict.fromkeys(classes))
    if rel == "maps.txt":
        maps = re.findall(r"^Map (\S+)", text, re.M)
        return f"{len(maps)} maps so far: " + ", ".join(m.rsplit("/", 1)[-1] for m in maps)
    return first


class Indexer:
    """The plugin's export fetched as it is written, in a thread of its own: a run starts on the codex there is, and its
    agent is told what arrives while it works (agents/session.py, news). Each fetch that brings files is a batch: its
    files, new or changed, each with its digest(), kept in `batches` and written to <project>/.indexing/batch-N.txt;
    status.json there says how far the export is (the probes' `codex` reads it: research.CodexView, in any process).
    A plugin without the wait-less manifest (an older build, Unity, Godot) answers when its export is done: the first
    fetch is then the whole codex, as before."""

    def __init__(self, game, codex_root, first_map=None, on_done=None):
        self.game = game
        self.root = codex_root
        self.dir = os.path.join(os.path.dirname(codex_root), INDEXING)
        self.first_map = first_map
        self.on_done = on_done or (lambda: None)  # (once the export is in: the codex's index written again)
        self.lock = threading.Lock()
        self.batches = []  # [{"n", "at": time.time(), "files": [{"path", "kind", "line"}]}]
        self.listeners = []  # callables(batch), called from the indexer's thread (the background researcher)
        self.running = False
        self.done = 0  # the export's files written, of total
        self.total = 0
        self.files = 0  # the codex files the plugin lists now
        self.started = time.time()
        self.finished_at = None  # time.time() once the export is in (or the indexer gave up: error)
        self.error = None
        self.export = None  # the manifest's last word on the export
        self.working = []  # what aside() is still writing (its names: "level snapshot")
        self._stop = threading.Event()
        self.thread = None

    def start(self):
        """The first fetch (what the plugin has now), then the rest in the background while the export runs. Returns
        the first fetch's result (fetch_from_plugin), or None when the plugin has no codex ops."""
        shutil.rmtree(self.dir, ignore_errors=True)
        os.makedirs(self.dir, exist_ok=True)
        first = fetch_from_plugin(self.game, self.root, wait=False, first_map=self.first_map)
        if first is None:
            return None
        self._take(first, announce=False)  # (what is there as the run starts is the codex it reads, not news)
        if self.running:
            self.thread = threading.Thread(target=self._follow, name="indexing", daemon=True)
            self.thread.start()
        else:
            self._finish()
        return first

    def _follow(self):
        failed = 0
        while not self._stop.wait(POLL_SECONDS):
            try:
                res = fetch_from_plugin(self.game, self.root, wait=False, follow=True)
            except Exception as e:
                failed += 1
                if failed >= GONE_TRIES:
                    self.error = f"the plugin stopped answering: {e}"
                    break
                continue
            failed = 0
            if res is None:
                self.error = "the plugin has no codex ops any more"
                break
            self._take(res)
            if not self.running:
                break
        self._finish()

    def _take(self, res, announce=True):
        export = res.get("export") or {}
        with self.lock:
            self.export = export
            self.running = bool(export.get("running"))
            self.done, self.total = int(export.get("done") or self.done), int(export.get("total") or self.total)
            self.files = int(res.get("files") or 0)
            if export.get("error") and not self.running:
                self.error = export["error"]
            elif export.get("ran") and not self.running:
                self.done = self.total  # (the end's answer has no progress: an export that ended well wrote it all)
        if announce and res.get("arrived"):
            self._arrive(res["arrived"])
        self._write_status()

    def _arrive(self, arrived):
        """A batch of files ([(codex path, "new" or "changed")]): kept, written down, told to the listeners."""
        files = [{"path": p, "kind": kind, "line": digest(self.root, p)} for p, kind in arrived]
        with self.lock:
            batch = {"n": len(self.batches) + 1, "at": time.time(), "files": files}
            self.batches.append(batch)
        with open(os.path.join(self.dir, f"batch-{batch['n']}.txt"), "w", encoding="utf-8") as f:
            f.write(batch_text(batch))
        codex = getattr(self.game, "codex", None)
        if codex is not None:
            codex._bp = None  # (Blueprint names are looked up again: new ones came)
        for listener in list(self.listeners):
            try:
                listener(batch)
            except Exception as e:
                print(f"codex: a listener failed on batch {batch['n']}: {e}")

    def aside(self, rels, work, name="codex-work"):
        """work() writes the codex files rels (the level's snapshot) in a thread of its own, so the run doesn't wait
        for it; they arrive as a batch of their own once written (those it wrote: a file it didn't is left out)."""
        before = {rel: _stamp(os.path.join(self.root, rel)) for rel in rels}
        with self.lock:
            self.working.append(name)

        def run():
            t0 = time.time()
            try:
                for attempt in range(1, ASIDE_TRIES + 1):
                    try:
                        work()
                        break
                    except Exception as e:  # (a busy game: the agent's probes and play share its few frames)
                        print(f"codex: {name} failed ({attempt} of {ASIDE_TRIES}): {e}")
                        if attempt == ASIDE_TRIES or self._stop.wait(ASIDE_RETRY_SECONDS):
                            return
            finally:
                with self.lock:
                    self.working.remove(name)
                self._write_status()
            print(f"codex: {name} done in {time.time() - t0:.1f} s")
            written = [(rel, "changed" if before[rel] else "new") for rel in rels
                       if _stamp(os.path.join(self.root, rel)) not in (None, before[rel])]
            if written:
                self._arrive(written)

        threading.Thread(target=run, name=name, daemon=True).start()

    def _finish(self):
        with self.lock:
            self.running = False
            self.finished_at = time.time()
        self._write_status()
        try:
            self.on_done()
        except Exception as e:
            print(f"codex: couldn't finish the index: {e}")
        for listener in list(self.listeners):
            try:
                listener(None)  # (None: the export is in)
            except Exception as e:
                print(f"codex: a listener failed at the end: {e}")

    def status(self):
        """{"running", "done", "total", "files", "batches", "seconds", "finished", "error", "working"}."""
        with self.lock:
            return {"running": self.running, "done": self.done, "total": self.total, "files": self.files,
                    "batches": len(self.batches), "seconds": round(time.time() - self.started, 1),
                    "finished": self.finished_at is not None, "error": self.error, "working": list(self.working)}

    def _write_status(self):
        try:
            with open(os.path.join(self.dir, "status.json"), "w", encoding="utf-8") as f:
                json.dump(self.status(), f)
        except OSError:
            pass

    def since(self, n):
        """The batches after the n-th."""
        with self.lock:
            return list(self.batches[n:])

    def stop(self):
        self._stop.set()


def progress(status):
    """How far an export is, in words: "412 of 983 written", or that it hasn't counted its files yet."""
    if not status.get("total"):
        return "starting, nothing written yet"
    return f"{status.get('done')} of {status.get('total')} written"


class View:
    """`codex` in probes: the export's indexing as the Indexer writes it down (<project>/.indexing/), readable in any
    process (the sandbox's child too). status(): how far it is; arrivals(): the batches of files it brought, whole;
    files(pattern): the codex's files now."""

    def __init__(self, project_root):
        self.project = project_root
        self.root = os.path.join(project_root, "codex")
        self.dir = os.path.join(project_root, INDEXING)

    def status(self):
        """{"running", "done", "total", "files", "batches", "seconds", "finished", "error"}; {"finished": True} when
        this run streamed nothing (the codex was there whole when it started)."""
        try:
            with open(os.path.join(self.dir, "status.json"), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {"finished": True, "running": False}

    def arrivals(self, after=0):
        """The batches after the `after`-th: [{"n", "files": [{"path", "kind": "new" or "changed", "line"}]}]."""
        out = []
        n = after + 1
        while True:
            try:
                with open(os.path.join(self.dir, f"batch-{n}.txt"), encoding="utf-8") as f:
                    lines = f.read().splitlines()
            except OSError:
                return out
            files = []
            for line in lines:
                m = re.match(r"([+~]) codex/(.+?)  (.*)$", line)
                if m:
                    files.append({"path": m.group(2), "kind": "new" if m.group(1) == "+" else "changed",
                                  "line": m.group(3)})
            out.append({"n": n, "files": files})
            n += 1

    def files(self, pattern="**/*"):
        """The codex's files matching a glob, as paths from codex/ (e.g. "blueprints/**/BP_*.txt")."""
        return sorted(os.path.relpath(p, self.root).replace(os.sep, "/")
                      for p in glob.glob(os.path.join(self.root, pattern), recursive=True) if os.path.isfile(p))

    def __repr__(self):
        s = self.status()
        if s.get("running"):
            return (f"<codex: indexing (the export: {progress(s)}), {s.get('files')} "
                    f"files in the codex, {s.get('batches')} batches arrived (codex.arrivals())>")
        return f"<codex: indexed, {s.get('files', 'all')} files; {s.get('batches', 0)} batches arrived this run>"


def _stamp(path):
    """(size, mtime_ns) of a file, None when there is none."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return st.st_size, st.st_mtime_ns


def batch_text(batch):
    """A batch as its file has it: one line a file, "+" new or "~" changed, its codex path, then its digest()."""
    return "".join(f"{'+' if f['kind'] == 'new' else '~'} codex/{f['path']}  {f['line']}\n" for f in batch["files"])


def build(project, path=None, game=None, export=True, engine=None, stream=False, first_map=None):
    """Make (or refresh) a project's codex. path: the project on this machine (Unreal: the .uproject; Unity, Godot:
    the folder), for its files and the commandlet; game: the live game, for level.txt; engine: the game's engine
    (else the game's, else the one path is a project of). stream: an export the plugin runs now is fetched as it is
    written, in the background (the Codex's `indexer`, an Indexer), rather than waited for; first_map: the level the
    run plays, which a new export does first. Returns the Codex."""
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
            if stream and export:
                indexer = Indexer(game, codex.root, first_map=first_map,
                                  on_done=lambda: eng.write_index(codex.root, project))
                fetched = indexer.start()
                if fetched is not None:
                    codex.indexer = indexer
            else:
                fetched = fetch_from_plugin(game, codex.root, export="auto" if export else "skip")
        except Exception as e:
            print(f"codex: couldn't fetch the code from the plugin: {e}")
        if fetched is not None:
            print(f"codex: {fetched['files']} files from the plugin ({fetched['fetched']} new or changed, "
                  f"{fetched['removed']} gone); export: {fetched.get('export')}")
    if path and fetched is None:
        # (a plugin without the codex ops: read the project's files directly)
        eng.build_local(path, codex.root, export=export)
    if game is not None and codex.indexer is not None:
        # The level's snapshot reads every actor of the running level (on a big World Partition map, tens of
        # thousands at a few frames a second: minutes): written while the run works, told when it is in
        codex.indexer.aside(["level.txt", "controls.txt"], lambda: snapshot_level(game, codex.root),
                            name="level snapshot")
    elif game is not None:
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
