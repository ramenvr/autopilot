"""Unreal Engine 5, through the Autopilot plugin (plugin/unreal/Autopilot). Reads go through Remote Control
compatible calls (`rc`); the codex is the project's C++ source and config plus the AutopilotCodex commandlet's Blueprint
and asset export; the kit's frame is Unreal's own.
"""

import glob
import os
import re
import shutil
import subprocess
import time

from ..kit.game import GAMEPLAY, KISMET_SYSTEM, GameError, name_of
from ..kit.geom import Vec
from .base import Engine, safe_rel

# The engine's folder (the one with Engine/ in it): AUTOPILOT_UNREAL, else the Epic launcher's default for UE 5.8
UNREAL = os.environ.get("AUTOPILOT_UNREAL", r"C:\Program Files\Epic Games\UE_5.8")
ENGINE_CMD = os.path.join(UNREAL, "Engine", "Binaries", "Win64", "UnrealEditor-Cmd.exe")
EDITOR = os.path.join(UNREAL, "Engine", "Binaries", "Win64", "UnrealEditor.exe")
SOURCE_EXT = (".h", ".hpp", ".cpp", ".inl", ".cs")
EXPORT_PREFIX = "Saved/AutopilotCodex/"


class Unreal(Engine):
    name = "unreal"
    title = "Unreal Engine"
    constants = {"GAMEPLAY": GAMEPLAY}
    export_dir = "Saved/AutopilotCodex"
    # the run history, each run's report files and recording, the codex export, its log and its progress file
    plugin_files = ("Saved/Autopilot", "Saved/Playtest", export_dir, "Saved/Logs/AutopilotCodexExport.log",
                    "Saved/Logs/AutopilotCodexExport.progress")
    no_export_note = ("note: no Blueprint export in the codex (the plugin couldn't make one: an older build without "
                      "the AutopilotCodex commandlet?)")
    default_exe = EDITOR

    # -- the project on disk -----------------------------------------------------------------------------

    def project_at(self, path):
        """The .uproject: the file itself, or the one in a folder (the one named after the folder first)."""
        if not path:
            return None
        if os.path.isfile(path):
            return os.path.abspath(path) if path.lower().endswith(".uproject") else None
        if not os.path.isdir(path):
            return None
        files = sorted(f for f in os.listdir(path) if f.lower().endswith(".uproject"))
        named = [f for f in files if os.path.splitext(f)[0].lower() == os.path.basename(os.path.normpath(path)).lower()]
        pick = (named or files or [None])[0]
        return os.path.abspath(os.path.join(path, pick)) if pick else None

    def project_name(self, project):
        return os.path.splitext(os.path.basename(project))[0]

    def find_project(self, game, project):
        """The .uproject in the running game's project directory (KismetSystemLibrary.GetProjectDirectory), the one
        named after the project first."""
        try:
            folder = game.ret(KISMET_SYSTEM, "GetProjectDirectory")
        except Exception:
            return None
        if not folder or not os.path.isdir(folder):
            return None
        files = sorted(f for f in os.listdir(folder) if f.lower().endswith(".uproject"))
        named = [f for f in files if os.path.splitext(f)[0].lower() == project.lower()]
        pick = (named or files or [None])[0]
        return os.path.join(folder, pick) if pick else None

    # -- the codex ---------------------------------------------------------------------------------------

    def codex_path(self, rel):
        return codex_path(rel)

    # -- the implementer's prompt ------------------------------------------------------------------------

    def kit_doc(self, kit):
        """KIT.md as it is: it is written for Unreal games (the prompt they were tuned on, word for word)."""
        return kit

    def copy_source(self, project, codex_root):
        return copy_source(project, codex_root)

    def build_local(self, project, codex_root, export=True):
        copy_source(project, codex_root)
        if export and not os.path.exists(os.path.join(codex_root, "summary.txt")) and not has_commandlet(project):
            print("codex: the project's Autopilot plugin has no AutopilotCodex commandlet yet: no Blueprint export")
        elif export and not os.path.exists(os.path.join(codex_root, "summary.txt")):
            t0 = time.time()
            code = export_assets(project, codex_root)
            print(f"codex: commandlet exit {code} in {time.time() - t0:.0f}s")

    def has_export(self, codex_root):
        return os.path.exists(os.path.join(codex_root, "summary.txt"))

    def write_index(self, codex_root, project):
        write_index(codex_root, project)

    # -- the game backend --------------------------------------------------------------------------------

    def name_of(self, path):
        return name_of(path)

    def resolve_class(self, game, name):
        """A class path from a name: a path as is; a Blueprint's name (BP_Door) from the codex; a C++ class name
        (Door, ADoor) in the project's modules or the engine."""
        if name.startswith("/"):
            return name
        if name in game._classes:
            return game._classes[name]
        candidates = []
        if game.codex:
            candidates += game.codex.class_paths(name)
        bare = name[1:] if len(name) > 1 and name[0] in "AU" and name[1].isupper() else name
        for module in (game.codex.modules() if game.codex else []) + ["Engine"]:
            candidates += [f"/Script/{module}.{bare}"]
        for c in candidates:
            # A class that exists (loaded) describes; one not loaded yet counts once the level has actors of it
            try:
                game.describe(c)
            except GameError:
                try:
                    r = game.call(GAMEPLAY, "GetAllActorsOfClass", WorldContextObject=game.world, ActorClass=c)
                except GameError:
                    continue
                if not r.get("OutActors"):
                    continue
            game._classes[name] = c
            return c
        raise GameError(f"no class named {name} (tried {', '.join(candidates) or 'nothing'})")

    def actors(self, game, cls):
        r = game.call(GAMEPLAY, "GetAllActorsOfClass", WorldContextObject=game.world,
                      ActorClass=game.resolve_class(cls))
        return list(r.get("OutActors") or [])

    def all_actors(self, game):
        return game.actors("/Script/Engine.Actor")

    def named(self, game, part):
        p = part.lower()
        return [a for a in game.all_actors() if p in name_of(a).lower()]

    def exists(self, game, obj):
        # A destroyed actor still describes and reads until garbage collection; its flag says it is gone (and its
        # components with it)
        actor = owner_actor(obj)
        if actor:
            try:
                if game.get(actor, "bActorIsBeingDestroyed") is True:
                    return False
                if actor == obj:
                    return True
            except GameError:
                pass  # not an actor, or gone: describe says
        return super().exists(game, obj)

    def loc(self, game, actor):
        return Vec.of(game.ret(actor, "K2_GetActorLocation"))

    def rot(self, game, actor):
        return game.ret(actor, "K2_GetActorRotation")

    def bounds(self, game, actor):
        r = game.call(actor, "GetActorBounds", bOnlyCollidingComponents=False)
        return Vec.of(r.get("Origin")), Vec.of(r.get("BoxExtent"))

    def velocity(self, game, actor):
        return Vec.of(game.ret(actor, "GetVelocity"))

    def view_rotation(self, game):
        r = game.ret(game.controller, "GetControlRotation") or {}
        pitch = r.get("Pitch", 0.0)
        return r.get("Yaw", 0.0), pitch - 360 if pitch > 180 else pitch

    def eye(self, game):
        cm = game.context()["camera_manager"]
        return Vec.of(game.ret(cm, "GetCameraLocation")) if cm else game.me()

    def pose(self, game):
        cm = game.context()["camera_manager"]
        at, eye, rot, vel = game.batch([
            lambda: game.me(), lambda: Vec.of(game.ret(cm, "GetCameraLocation")) if cm else None,
            lambda: game.view_rotation(), lambda: game.velocity(game.pawn)])
        for v in (at, rot):
            if isinstance(v, GameError):
                raise v
        eye = eye if isinstance(eye, Vec) else at
        return {"at": at, "eye": eye, "yaw": rot[0], "pitch": rot[1],
                "velocity": vel if isinstance(vel, Vec) else Vec()}

    def movement(self, game):
        """From the pawn's CharacterMovement and capsule."""
        pawn = game.pawn
        out = {}
        for comp in ("CharMoveComp", "CharacterMovement"):
            try:
                p = game.props(f"{pawn}.{comp}")
            except GameError:
                continue
            out["walk_speed"] = p.get("MaxWalkSpeed")
            out["jump_z"] = p.get("JumpZVelocity")
            out["air_control"] = p.get("AirControl")
            # a ledge this much above the feet at the top of a jump is stepped onto
            out["step"] = p.get("MaxStepHeight")
            try:  # the component's own gravity: the world's, times its GravityScale
                out["gravity"] = abs(float(game.ret(f"{pawn}.{comp}", "GetGravityZ")))
            except (GameError, TypeError, ValueError):
                out["gravity"] = 980.0 * float(p.get("GravityScale") or 1.0)
            break
        for comp in ("CollisionCylinder", "CapsuleComponent"):
            try:
                out["half_height"] = game.get(f"{pawn}.{comp}", "CapsuleHalfHeight")
                break
            except GameError:
                continue
        return out

    # -- a headless game ---------------------------------------------------------------------------------

    def launch_command(self, project, url, log, map=None, exe=None):
        """The editor's executable as a game, without rendering (-game -nullrhi)."""
        return [exe or EDITOR, project] + ([map] if map else []) + [
            "-game", "-nullrhi", "-unattended", "-nosplash", "-log", "-Playtest=goal", "-PlaytestGoal=autopilot",
            f"-PlaytestUrl={url}", f"-abslog={log}"]


# -- the codex (functions: autopilot.codex keeps their old names) ---------------------------------------------

def owner_actor(path):
    """The actor a level object belongs to, from its path (the path itself for an actor), or None for an object outside
    a level: /Game/M.M:PersistentLevel.BP_Door_C_3.Hinge -> /Game/M.M:PersistentLevel.BP_Door_C_3."""
    head, sep, sub = str(path).partition(":")
    parts = sub.split(".")
    if not sep or len(parts) < 2 or not all(parts[:2]):
        return None
    return f"{head}:{parts[0]}.{parts[1]}"


def has_commandlet(uproject):
    """Whether the project's Autopilot plugin was built with the AutopilotCodex commandlet."""
    proj = os.path.dirname(os.path.abspath(uproject))
    if glob.glob(os.path.join(proj, "Plugins", "**", "UnrealEditor-AutopilotCodex*.dll"), recursive=True):
        return True  # the commandlet's own module
    editor_dlls = os.path.join(proj, "Plugins", "**", "UnrealEditor-AutopilotPlaytestEditor*.dll")
    for dll in glob.glob(editor_dlls, recursive=True):
        try:
            with open(dll, "rb") as f:
                data = f.read()
        except OSError:
            continue
        if b"AutopilotCodex" in data or "AutopilotCodex".encode("utf-16-le") in data:
            return True
    return False


def export_assets(uproject, out, timeout=1800):
    """Run the plugin's AutopilotCodex commandlet on a project (a copy, or a project not open in an editor that holds
    its files). Returns the commandlet's exit code."""
    os.makedirs(out, exist_ok=True)
    log = os.path.join(out, "commandlet.log")
    # Two AutopilotCodex commandlets starting at once hang at plugin start-up: one at a time on this machine
    lock = os.path.join(os.environ.get("TEMP", "."), "autopilot_codex.lock")
    deadline = time.time() + timeout
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            break
        except FileExistsError:
            if time.time() - os.path.getmtime(lock) > timeout or time.time() > deadline:
                os.remove(lock)  # left by a run that died
            time.sleep(2)
    try:
        return _run_commandlet(uproject, out, log, timeout)
    finally:
        try:
            os.remove(lock)
        except OSError:
            pass


def _run_commandlet(uproject, out, log, timeout):
    cmd = [ENGINE_CMD, uproject, "-run=AutopilotCodex", f"-out={out}", "-unattended", "-nullrhi", "-nosplash", "-nop4",
           f"-abslog={log}"]
    from .. import processes

    p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **processes.GROUP)
    processes.track(p)
    try:
        return p.wait(timeout)
    except subprocess.TimeoutExpired:
        processes.kill(p)
        return -1


def copy_source(uproject, codex_root):
    """Source/ and Config/*.ini of a project into the codex."""
    proj = os.path.dirname(os.path.abspath(uproject))
    n = 0
    src = os.path.join(proj, "Source")
    if os.path.isdir(src):
        for f in glob.glob(os.path.join(src, "**", "*"), recursive=True):
            if f.endswith(SOURCE_EXT) and os.path.isfile(f):
                dst = os.path.join(codex_root, "source", os.path.relpath(f, src))
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copyfile(f, dst)
                n += 1
    cfg = os.path.join(proj, "Config")
    for f in glob.glob(os.path.join(cfg, "*.ini")):
        dst = os.path.join(codex_root, "config", os.path.basename(f))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(f, dst)
    shutil.copyfile(uproject, os.path.join(codex_root, os.path.basename(uproject)))
    return n


def write_index(codex_root, project):
    """README.md: what is in the codex and where to start."""
    def count(pattern):
        return len(glob.glob(os.path.join(codex_root, pattern), recursive=True))

    parents = {}
    for f in glob.glob(os.path.join(codex_root, "blueprints", "**", "*.txt"), recursive=True):
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                head = fh.read(600)
        except OSError:
            continue
        m = re.search(r"^parent:?\s*(\S+)", head, re.I | re.M)
        parent = re.sub(r"_C$", "", m.group(1).rsplit(".", 1)[-1]) if m else "?"
        parents.setdefault(parent, []).append(os.path.relpath(f, codex_root).replace(os.sep, "/"))
    ini = {}
    for name in ("DefaultEngine.ini", "DefaultGame.ini", "DefaultInput.ini"):
        p = os.path.join(codex_root, "config", name)
        if os.path.exists(p):
            with open(p, encoding="utf-8", errors="replace") as fh:
                ini[name] = fh.read()
    keys = []
    for k in ("GameDefaultMap", "EditorStartupMap", "GlobalDefaultGameMode", "DefaultPlayerInputClass",
              "DefaultInputComponentClass"):
        m = re.search(rf"^{k}=(.*)$", ini.get("DefaultEngine.ini", ""), re.M)
        if m:
            keys.append(f"- {k} = {m.group(1).strip()}")
    lines = [
        f"# Codex of {project}",
        "",
        "The game's code and data as text. Search it with Grep/Glob, read files with Read.",
        "",
        "| where | what | count |",
        "|---|---|---|",
        f"| source/ | C++ source (Source/) | {count('source/**/*.*')} |",
        f"| blueprints/ | Blueprints as pseudo-code: variables, components, defaults, graphs | "
        f"{count('blueprints/**/*.txt')} |",
        f"| input/ | Enhanced Input actions and mapping contexts (key -> action) | {count('input/**/*.txt')} |",
        f"| data/ | data tables, data assets, enums, structs | {count('data/**/*.*')} |",
        f"| config/ | Config/*.ini (DefaultInput.ini has legacy key bindings) | {count('config/*.ini')} |",
        "| assets.txt | every asset: path and class |  |",
        "| maps.txt | each map's placed actors |  |",
        "| level.txt | the actors of the level running now, with locations |  |",
        "| controls.txt | the input actions mapped for the player now, with their keys |  |",
        "",
        "## Settings",
        *keys,
        "",
        "## Blueprints by parent class",
    ]
    for parent in sorted(parents, key=lambda p: (-len(parents[p]), p)):
        files = parents[parent]
        lines.append(f"- {parent} ({len(files)}): " + ", ".join(os.path.basename(f)[:-4] for f in files[:25])
                     + (" ..." if len(files) > 25 else ""))
    with open(os.path.join(codex_root, "README.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def codex_path(rel):
    """Where a file the plugin lists (a project-relative path) goes in the codex, or None for anything else. The
    plugin is the customer's: a path that could leave the codex (absolute, .., backslashes) is refused."""
    parts = safe_rel(rel)
    if parts is None:
        return None
    if rel.startswith("Source/"):
        return "source/" + rel[len("Source/"):]
    if rel.startswith("Config/") and len(parts) == 2 and rel.endswith(".ini"):
        return "config/" + parts[1]
    if rel.startswith(EXPORT_PREFIX) and len(parts) > 2:
        return rel[len(EXPORT_PREFIX):]
    if len(parts) == 1 and rel.endswith(".uproject"):
        return rel
    return None
