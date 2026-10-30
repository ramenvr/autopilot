"""What the brain needs to know about the engine a game is made in: one Engine per engine (unreal.py, unity.py,
godot.py), chosen from the plugin's hello (engines.for_hello).

An engine covers:
- the project on disk: what a project is (a .uproject, a Unity folder, a Godot folder), asked of the running game too;
- the codex: which of the project's files go into it (fetched from the plugin, or copied from this machine's disk),
  and its README;
- the game backend: how Game finds things and reads where they are, the player's pose and its movement numbers;
- the kit frame: harness code works in Unreal's frame (Z up, centimetres, yaw from +X toward +Y, pitch up) whatever
  the engine; to_kit / to_native convert a native value read with game.get;
- the implementer's prompt: the engine's name and its version of the kit's reference (Unreal: KIT.md as it is; the
  others: KIT.md said without Unreal's names, NEUTRAL_KIT, and their own section, autopilot/engines/<name>.md);
- a headless game for bench/run_autopilot.py.

Engine is the interface and holds the parts shared by every engine; OpEngine (below) is the game backend over the
engine-neutral ops (find, transforms, pose, movement) that the Unity and Godot plugins answer.
"""

import os
import shutil

from ..kit.game import GameError
from ..kit.geom import Vec

HERE = os.path.dirname(os.path.abspath(__file__))
KEYS = {"forward": "W", "back": "S", "left": "A", "right": "D", "jump": "SpaceBar"}
# Files of the codex the brain writes itself: never taken from a project (fetched or copied)
CODEX_OWN = {"README.md", "level.txt", "controls.txt", ".fetched.json"}


# KIT.md is the kit's reference as Unreal games have it in the prompt (tuned on them: kept word for word). For the other
# engines, its passages that name Unreal's things are said this way instead, and the engine's section
# (autopilot/engines/<name>.md, appended) says how that engine names them. Each Unreal passage must be in KIT.md
# (tests/test_engines.py checks): an edit to one of them in KIT.md needs the same edit here.
NEUTRAL_KIT = [
    ("Units: centimetres (Unreal units), degrees, seconds of game time.\n",
     "Units: centimetres (Unreal units), degrees, seconds of game time.\n"
     "What is particular to the game's engine (how its objects, classes, input actions and maps are named) is in the\n"
     "engine's section at the end.\n"),
    ("Reads (privileged: any object, any property, protected and Blueprint variables included):\n",
     "Reads (privileged: any object, any property):\n"),
    ('- `game.actors("BP_Door")` / `game.actors("Door")` / `game.actors("/Game/X/BP_Door.BP_Door_C")`: '
     "actor paths of a class (subclasses too). A Blueprint's name is resolved from the codex, a C++ class name "
     "from the project's modules.\n",
     "- `game.actors(cls)`: actor paths of a class (subclasses too); how a class is named: the engine's section.\n"),
    ('`game.props(path)`: all of them. Components: `f"{actor}.MeshComp"`.\n',
     "`game.props(path)`: all of them.\n"),
    ('  Static library functions go to class default objects, e.g. `game.ret(GAMEPLAY, "GetPlayerCharacter", '
     "WorldContextObject=game.world, PlayerIndex=0)`.\n"
     "- `game.describe(path)`: its class, properties, Blueprint-callable functions (with their arguments).\n",
     "- `game.describe(path)`: its class, properties, callable functions (with their arguments).\n"),
    ('- `game.pose()` -> {"at", "eye", "yaw", "pitch", "velocity"} of the player (one frame).\n',
     '- `game.pose()` -> {"at", "eye", "yaw", "pitch", "velocity"} of the player (one frame).\n'
     "- These and `game.view` are in the kit's frame: X forward, Y right, Z up, centimetres; "
     "yaw turns right, pitch up.\n"
     "  `game.to_kit(value)`: a position or velocity read with `game.get` in the engine's own frame and units, "
     "as a Vec in\n"
     "  the kit's; `game.to_native(vec)`: back, for `game.set` / `game.call`.\n"),
    ("(HUD, menus, prompts; UMG keyed\n  `<UserWidget>.<TextBlock>`).\n",
     "(HUD, menus, prompts).\n"),
    ('- `game.press("E")`: a quick press of a key (FKey names: W, A, S, D, E, F, R, Q, SpaceBar, LeftShift, '
     "LeftControl, Tab,\n  Escape, Enter, One..Nine, LeftMouseButton, RightMouseButton, MiddleMouseButton...) "
     "or of an Enhanced Input action path\n  (`/Game/Input/IA_Jump.IA_Jump`, value optional).\n",
     '- `game.press("E")`: a quick press of a key (FKey names, whatever the engine: W, A, S, D, E, F, R, Q, SpaceBar, '
     "LeftShift,\n  LeftControl, Tab, Escape, Enter, One..Nine, LeftMouseButton, RightMouseButton, "
     "MiddleMouseButton...) or of an input\n  action (as the engine's section names them; value optional).\n"),
    ("- `game.restart(map=None)`: start the run's level over (the briefing's), or open a map (`/Game/Maps/Level`). "
     "For a\n  game that boots into a main menu, set the harness's `start_map` to its gameplay level (from the codex's "
     "maps.txt /\n  config), or click through the menu in setup() (`game.ui()`, `game.ui_act`).\n",
     "- `game.restart(map=None)`: start the run's level over (the briefing's), or open a map (named as the engine's "
     "section\n  says). For a game that boots into a main menu, set the harness's `start_map` to its gameplay level "
     "(from the codex),\n  or click through the menu in setup() (`game.ui()`, `game.ui_act`).\n"),
    ('"step" (MaxStepHeight)', '"step" (the highest step it walks up)'),
    ('"air_control"}, read from its CharacterMovement and capsule.\n', '"air_control"}, read from the game.\n'),
]


def safe_rel(rel):
    """The parts of a project-relative path the plugin lists, or None for one that could leave the codex (absolute,
    a drive, `..`, backslashes): the plugin is the customer's."""
    if not isinstance(rel, str) or not rel or rel.startswith("/") or "\\" in rel or ":" in rel:
        return None
    parts = rel.split("/")
    if any(p in ("", ".", "..") for p in parts):
        return None
    return parts


class Engine:
    name = ""  # as the plugin's hello says it ("engine")
    title = ""  # in the implementer's prompt: "a video game made in <title>"
    constants = {}  # names harness code can use (Unreal: GAMEPLAY, the GameplayStatics default object)
    keys = KEYS  # the movement keys the controllers press (Unreal FKey names: every plugin maps them)
    export_dir = None  # where the plugin writes its text export of the project, project-relative (None: no export)
    error_log = True  # the plugin sends the errors the game logs in play (`log` events): none in an episode = none

    # -- the project on disk -----------------------------------------------------------------------------

    def project_at(self, path):
        """The project at a path the user gave (a project file or its folder), as this engine names it (Unreal: the
        .uproject file; Unity, Godot: the folder), or None when the path holds no project of this engine."""
        raise NotImplementedError

    def project_folder(self, project):
        """The folder of a project (as project_at gives it)."""
        return project if os.path.isdir(project) else os.path.dirname(os.path.abspath(project))

    def project_name(self, project):
        """A project's name from its path: what its plugin most likely says as hello's `project`."""
        return os.path.basename(os.path.normpath(self.project_folder(project)))

    plugin_files = ()  # what the plugin keeps in a project about its runs (history, reports, recordings, codex export)

    def plugin_data(self, project):
        """The files and folders the plugin keeps about its runs for a project, as paths (`autopilot uninstall`
        removes them with the memory): plugin_files in the project's folder."""
        folder = self.project_folder(project)
        return [os.path.join(folder, *rel.split("/")) for rel in self.plugin_files]

    def find_project(self, game, project):
        """The running game's project on this machine, asked of the game itself (its project folder). None when the
        game doesn't say or the folder isn't on this machine (the brain runs elsewhere)."""
        try:
            folder = (game.context(fresh=True) or {}).get("project_dir")
        except Exception:
            return None
        if not folder or not os.path.isdir(folder):
            return None
        return self.project_at(folder)

    # -- the codex ---------------------------------------------------------------------------------------

    def codex_path(self, rel):
        """Where a file the plugin lists (a project-relative path, forward slashes) goes in the codex, or None for
        anything else. Every path is checked with safe_rel first."""
        raise NotImplementedError

    def copy_source(self, project, codex_root):
        """Copy the project's files that go into the codex from this machine's disk (a plugin without the codex ops,
        or `python -m autopilot codex`). Returns how many were copied."""
        folder = self.project_folder(project)
        n = 0
        for rel in self.walk_project(folder):
            dest = self.codex_path(rel)
            if not dest:
                continue
            full = os.path.join(codex_root, dest)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            shutil.copyfile(os.path.join(folder, rel), full)
            n += 1
        return n

    def walk_project(self, folder):
        """The project-relative paths (forward slashes) of the files copy_source looks at, skipping the folders
        skip_dir names."""
        for base, dirs, files in os.walk(folder):
            rel_base = os.path.relpath(base, folder).replace(os.sep, "/")
            rel_base = "" if rel_base == "." else rel_base + "/"
            dirs[:] = sorted(d for d in dirs if not self.skip_dir(rel_base + d))
            for f in sorted(files):
                yield rel_base + f

    def skip_dir(self, rel):
        """Whether copy_source skips a project folder (project-relative path) and all in it."""
        return os.path.basename(rel) in (".git", "node_modules")

    def build_local(self, project, codex_root, export=True):
        """The codex from this machine's disk: the project's files, and the engine's export where it has one that can
        be made without the game."""
        n = self.copy_source(project, codex_root)
        print(f"codex: {n} files from {self.project_folder(project)}")

    def has_export(self, codex_root):
        """Whether the codex has the plugin's export of the project (or the engine needs none)."""
        return True

    no_export_note = ""  # printed when has_export is false

    def write_index(self, codex_root, project):
        """README.md: what is in the codex and where to start."""
        raise NotImplementedError

    # -- the kit frame -----------------------------------------------------------------------------------

    def to_kit(self, v):
        """A native vector (a position or a velocity as game.get reads it: {"x","y","z"} or a sequence) in the kit
        frame, as a Vec."""
        return Vec.of(v)

    def to_native(self, v):
        """A kit-frame vector (a Vec, {"X","Y","Z"} or a sequence) as the engine's own {"x","y","z"}, for game.set /
        game.call."""
        return Vec.of(v).ue()

    def rotation_to_kit(self, r):
        """A native rotation (as game.get reads it) as the kit's (yaw, pitch) in degrees."""
        r = r or {}
        return float(r.get("Yaw", 0.0)), float(r.get("Pitch", 0.0))

    def rotation_to_native(self, yaw, pitch=0.0):
        """The kit's (yaw, pitch) as the engine's own rotation value."""
        return {"Pitch": pitch, "Yaw": yaw, "Roll": 0.0}

    # -- the game backend (Game's reads of things and of the player) -------------------------------------

    def name_of(self, path):
        """An object's name from its path."""
        raise NotImplementedError

    def resolve_class(self, game, name):
        return name

    def actors(self, game, cls):
        raise NotImplementedError

    def all_actors(self, game):
        raise NotImplementedError

    def named(self, game, part):
        p = part.lower()
        return [a for a in game.all_actors() if p in self.name_of(a).lower()]

    def exists(self, game, obj):
        try:
            game.describe(obj)
            return True
        except GameError:
            return False

    def loc(self, game, actor):
        raise NotImplementedError

    def locs(self, game, actors):
        out = {}
        for a, v in zip(actors, game.map(game.loc, actors)):
            if isinstance(v, Vec):
                out[a] = v
        return out

    def rot(self, game, actor):
        raise NotImplementedError

    def bounds(self, game, actor):
        raise NotImplementedError

    def velocity(self, game, actor):
        raise NotImplementedError

    def me(self, game):
        return game.loc(game.pawn)

    def view_rotation(self, game):
        raise NotImplementedError

    def eye(self, game):
        raise NotImplementedError

    def pose(self, game):
        raise NotImplementedError

    def movement(self, game):
        """The player's raw movement numbers for kit.control.movement: some of {"walk_speed", "jump_z", "gravity",
        "half_height", "step", "air_control", ...} in kit units; what it can't tell is left out or None."""
        raise NotImplementedError

    # -- the implementer's prompt ------------------------------------------------------------------------

    def kit_doc(self, kit):
        """The kit's reference for a game of this engine, from KIT.md (`kit`, written for Unreal): its Unreal passages
        said without Unreal's names (NEUTRAL_KIT), then the engine's own section."""
        for unreal, neutral in NEUTRAL_KIT:
            kit = kit.replace(unreal, neutral)
        return kit + self.kit_section()

    def kit_section(self):
        """This engine's part of the kit's reference, appended to KIT.md in the implementer's prompt."""
        path = os.path.join(HERE, f"{self.name}.md")
        try:
            with open(path, encoding="utf-8") as f:
                return f.read()
        except OSError:
            return ""

    # -- a headless game (bench/run_autopilot.py) ------------------------------------------------------------

    default_exe = None  # the engine (or a build of the game) bench/run_autopilot.py starts, when --engine isn't given

    def launch_command(self, project, url, log, map=None, exe=None):
        """The command line of a headless game of the project (no window, no rendering) whose plugin connects to the
        brain at url and asks it for a goal run, logging to log."""
        raise NotImplementedError(f"no headless launch for {self.title or self.name} yet")


class OpEngine(Engine):
    """The game backend over the engine-neutral ops the Unity and Godot plugins answer (docs/protocol.md, Engines):
    `find`, `transforms`, `pose` and `movement`, all in the kit frame. Paths are the engine's own (a Unity GameObject,
    a Godot node path), so classes are given by their type names as they are (the plugin resolves them)."""

    def name_of(self, path):
        """The last part of a path, without a :sub-path ("/root/Main/Player:position" -> "Player")."""
        return str(path).rstrip("/").rsplit("/", 1)[-1].split(":", 1)[0]

    def actors(self, game, cls):
        return list((game.request("find", {"class": cls}) or {}).get("objects") or [])

    def all_actors(self, game):
        return list((game.request("find", {}) or {}).get("objects") or [])

    def transforms(self, game, objects):
        """{path: {"location", "rotation", "velocity", "center", "extent"} or None (gone)} for these objects."""
        return (game.request("transforms", {"objects": list(objects)}) or {}).get("objects") or {}

    def _one(self, game, actor):
        t = self.transforms(game, [actor]).get(actor)
        if not t:
            raise GameError(f"transforms: Object {actor} does not exist")
        return t

    def loc(self, game, actor):
        return Vec.of(self._one(game, actor).get("location"))

    def locs(self, game, actors):
        """One request for all of them (gone ones left out)."""
        if not actors:
            return {}
        got = self.transforms(game, actors)
        return {a: Vec.of(got[a]["location"]) for a in actors if (got.get(a) or {}).get("location") is not None}

    def rot(self, game, actor):
        return self._one(game, actor).get("rotation")

    def bounds(self, game, actor):
        t = self._one(game, actor)
        return Vec.of(t.get("center") or t.get("location")), Vec.of(t.get("extent") or {"X": 0, "Y": 0, "Z": 0})

    def velocity(self, game, actor):
        return Vec.of(self._one(game, actor).get("velocity") or {"X": 0, "Y": 0, "Z": 0})

    def _pose(self, game):
        return game.request("pose") or {}

    def me(self, game):
        return Vec.of(self._pose(game).get("at"))

    def view_rotation(self, game):
        p = self._pose(game)
        return float(p.get("yaw") or 0.0), float(p.get("pitch") or 0.0)

    def eye(self, game):
        p = self._pose(game)
        return Vec.of(p.get("eye") or p.get("at"))

    def pose(self, game):
        p = self._pose(game)
        if p.get("at") is None:
            raise GameError("pose: no player")
        at = Vec.of(p["at"])
        return {"at": at, "eye": Vec.of(p.get("eye")) or at, "yaw": float(p.get("yaw") or 0.0),
                "pitch": float(p.get("pitch") or 0.0), "velocity": Vec.of(p.get("velocity")) or Vec(),
                "on_ground": p.get("on_ground")}

    def movement(self, game):
        try:
            m = game.request("movement") or {}
        except GameError:
            return {}
        return {k: m.get(k) for k in ("walk_speed", "jump_z", "gravity", "half_height", "radius", "step", "max_slope",
                                      "air_control") if k in m}

    def write_index(self, codex_root, project):
        """README.md: the codex's files by kind (the engine's own index may say more)."""
        groups = {}
        for base, _, files in os.walk(codex_root):
            for f in files:
                rel = os.path.relpath(os.path.join(base, f), codex_root).replace(os.sep, "/")
                if rel in CODEX_OWN:
                    continue
                groups.setdefault(os.path.splitext(f)[1].lower() or f, []).append(rel)
        lines = [f"# Codex of {project}", "",
                 f"The game's code and data as text ({self.title} project). Search it with Grep/Glob, read files with "
                 "Read.", "", "| kind | files | e.g. |", "|---|---|---|"]
        for ext in sorted(groups, key=lambda e: (-len(groups[e]), e)):
            files = sorted(groups[ext])
            lines.append(f"| {ext} | {len(files)} | {', '.join(files[:3])}{' ...' if len(files) > 3 else ''} |")
        lines += ["", "- level.txt: the objects of the scene running now, with locations",
                  "- controls.txt: the input actions mapped for the player now, with their keys"]
        if self.export_dir:
            lines.append(f"- summary.txt and what is beside it: the plugin's export "
                         f"({self.export_dir}/ in the project)")
        lines += self.index_notes(codex_root)
        with open(os.path.join(codex_root, "README.md"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    def index_notes(self, codex_root):
        """More lines for the README (where to start in this engine's project): the engine's own."""
        return []
