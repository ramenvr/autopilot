"""Roblox, through Roblox Studio's built-in MCP server ("StudioMCP", engines/roblox_mcp.py): Route A, the engine
"roblox-mcp". No Autopilot plugin runs inside Studio: a connector process (`python -m autopilot.engines.roblox_connector
connect`) stands in for one. It connects out to the brain over the bridge's WebSocket like the other plugins, says hello
as engine "roblox-mcp", and answers the protocol's requests (docs/protocol.md) by calling Studio's MCP tools:

- reads (context, find, transforms, pose, movement, rc, screen, ui, view) are Luau ops (engines/roblox.luau) run with
  execute_luau; the requests that come in together (a harness's game.batch in observe()) are coalesced into ONE
  execute_luau call, whose answer is one JSON string (HttpService:JSONEncode);
- inputs: user_keyboard_input (keys), user_mouse_input (clicks, the pointer, the wheel), character_navigation (`move`);
  camera turns are the `turn` Luau op in the player's client (Studio's synthetic right-drag doesn't turn the camera);
- the session: start_stop_play (restart is stop, then start); `capture` is screen_capture (stills, no video);
- the errors the game logs: get_console_output, polled while the game plays, as `log` events and log_error findings;
- the codex: a Rojo project's files from disk when there is one, else every script read with script_read; and the
  place's instance tree (search_game_tree) and its CollectionService tags.

Roblox's frame: x right, y up, -z forward (a camera's LookVector at rest), studs, right-handed; Orientation in degrees,
y turning left (from -z toward -x), x tilting the view up; applied y, then x, then z. Roblox's physics takes 1 stud as
0.28 m. The kit's: X forward, Y right, Z up, centimetres:
    kit (X, Y, Z) = 28 * (-z, x, y);  kit yaw = -orientation.y;  kit pitch = orientation.x

The tool argument shapes were checked against a live Studio (docs/studio_tools.txt); the tests run against
engines/roblox_mcp.FakeStudio and tests/robloxsim.py, which follow them.
"""

import base64
import hashlib
import json
import math
import os
import queue
import re
import sys
import threading
import time
import uuid

from ..kit.game import GameError
from ..kit.geom import Vec, wrap
from . import roblox_mcp as mcp
from .base import CODEX_OWN, OpEngine, safe_rel

HERE = os.path.dirname(os.path.abspath(__file__))
CM_PER_STUD = 28.0  # (Roblox's physics: 1 stud = 0.28 m)
PRELUDE_FILE = os.path.join(HERE, "roblox.luau")
# The live place's export, as the connector lists it (codex_manifest), at the codex's top: summary.txt, tree.txt,
# tags.txt, and place/<Service>/... (each script, named the way Rojo names script files)
EXPORT_PREFIX = ".autopilot/roblox_codex/"
CODE_EXT = (".lua", ".luau")
DATA_EXT = (".json", ".toml", ".rbxlx", ".rbxmx", ".csv", ".txt", ".md")
TOP_FILES = ("default.project.json", "aftman.toml", "rokit.toml", "foreman.toml", "wally.toml", "selene.toml",
             "stylua.toml", ".luaurc")
SKIP = ("Packages", "ServerPackages", "DevPackages", "node_modules", "build", "out")
# Seconds between console reads while the game plays (each is an MCP call: errors show up this late at most)
CONSOLE_EVERY = float(os.environ.get("AUTOPILOT_ROBLOX_CONSOLE_SECONDS") or 2.0)
# How long requests that come in together wait for one another before their execute_luau goes (seconds)
COALESCE_SECONDS = 0.004
# The seconds a restart waits for the player's character in the new play session
RESTART_SECONDS = 45.0
# FKey names (what the kit and every plugin use) as Roblox's KeyCode names, where they differ
KEYCODES = {"SpaceBar": "Space", "Enter": "Return", "BackSpace": "Backspace", "LeftCmd": "LeftMeta",
            "RightCmd": "RightMeta", "CapsLock": "CapsLock", "Tilde": "Backquote", "Hyphen": "Minus",
            "Equals": "Equals", "Semicolon": "Semicolon", "Comma": "Comma", "Period": "Period", "Slash": "Slash",
            "Backslash": "BackSlash", "LeftBracket": "LeftBracket", "RightBracket": "RightBracket",
            "Apostrophe": "Quote", "NumPadZero": "KeypadZero", "NumPadOne": "KeypadOne"}
# The requests that are the player's inputs: answered one after another, in the order they came
INPUT_OPS = ("key", "input", "pointer", "ui_act", "move")
MOUSE_BUTTONS = {"LeftMouseButton": "left", "RightMouseButton": "right", "MiddleMouseButton": "middle"}
# Degrees a mouse count (MouseX / MouseY) turns the camera: the kit's calibrate_look() measures it; about what a
# pixel of a right-drag turns Roblox's default camera
DEGREES_PER_COUNT = 0.5
WHEEL_NOTCHES = 10  # the most wheel notches one MouseScroll press sends
DATAMODEL_PREFIX = re.compile(r"^(server|client|edit):", re.I)
INPUT_NOTES = [
    "Keys and clicks go into Studio's play session through its MCP server (user_keyboard_input, user_mouse_input), "
    "each a round trip of its own: inputs land tens to hundreds of milliseconds after they are sent.",
    "MouseX / MouseY turn the camera by setting its look direction in the player's client (one count: "
    f"{DEGREES_PER_COUNT:g} degree), as a right-drag would: Studio's MCP mouse can't drag the camera itself.",
    "Studio's MCP mouse has left and right buttons only: no middle button.",
    "game.request('move', {'to': kit point} or {'object': path}) walks the character there with Studio's "
    "character_navigation (pathfinding), not the player's keys.",
]


# -- the kit frame -------------------------------------------------------------------------------------------------

def kit_of(v):
    """A Roblox position or velocity ({"x","y","z"} studs, or a sequence) in the kit's frame (a Vec, cm)."""
    n = Vec.of(v)
    return None if n is None else Vec(-CM_PER_STUD * n.z, CM_PER_STUD * n.x, CM_PER_STUD * n.y)


def native_of(v):
    """A kit-frame point as Roblox's {"x","y","z"} in studs."""
    k = Vec.of(v)
    return {"x": k.y / CM_PER_STUD, "y": k.z / CM_PER_STUD, "z": -k.x / CM_PER_STUD}


def kit_dir(v):
    """A Roblox direction (a unit vector: no scaling) in the kit's axes."""
    n = Vec.of(v)
    return None if n is None else Vec(-n.z, n.x, n.y)


def kit_extent(size):
    """A Roblox box size (studs, full) as the kit's half extent (cm), axis for axis."""
    s = Vec.of(size) or Vec()
    return Vec(abs(s.z), abs(s.x), abs(s.y)) * (CM_PER_STUD / 2.0)


def yaw_pitch(forward):
    """(yaw, pitch) in degrees of a kit-frame direction."""
    if forward is None or forward.length < 1e-9:
        return 0.0, 0.0
    return (math.degrees(math.atan2(forward.y, forward.x)),
            math.degrees(math.atan2(forward.z, math.hypot(forward.x, forward.y))))


# -- the place on disk ---------------------------------------------------------------------------------------------

def _rojo_name(folder):
    try:
        with open(os.path.join(folder, "default.project.json"), encoding="utf-8") as f:
            name = json.load(f).get("name")
        return name.strip() if isinstance(name, str) and name.strip() else None
    except (OSError, ValueError, AttributeError):
        return None


def rojo_mappings(project_json):
    """[(instance path, filesystem $path)] of a Rojo project file's tree (default.project.json), for the codex's
    index: where each service's code lives on disk."""
    try:
        with open(project_json, encoding="utf-8") as f:
            tree = json.load(f).get("tree") or {}
    except (OSError, ValueError, AttributeError):
        return []
    out = []

    def walk(node, path):
        if not isinstance(node, dict):
            return
        if isinstance(node.get("$path"), str):
            out.append(("/".join(path) or "game", node["$path"]))
        for k, v in node.items():
            if not k.startswith("$"):
                walk(v, path + [k])

    walk(tree, [])
    return out


def script_file(path, cls):
    """The codex file of a script of the place (an instance path and class), named as Rojo names script files
    (Main.server.luau, Hud.client.luau, Util.luau), under place/."""
    parts = []
    for p in re.split(r"(?<!\\)/", path):
        p = p.replace("\\/", "/").replace("\\@", "@").replace("\\\\", "\\")
        p = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", p).strip(". ") or "_"
        parts.append(p)
    suffix = {"Script": ".server.luau", "LocalScript": ".client.luau"}.get(cls, ".luau")
    return "place/" + "/".join(parts) + suffix


class RobloxMcp(OpEngine):
    name = "roblox-mcp"
    title = "Roblox"  # (in the prompt: "a video game made in Roblox")
    # (the connector keeps nothing in the project: its export of the live place is only in the codex)
    export_dir = None
    plugin_files = ()
    no_export_note = ("note: no export of the live place in the codex (the Roblox connector couldn't read it through "
                      "Studio's MCP server)")
    default_exe = sys.executable

    # -- the project on disk -----------------------------------------------------------------------------

    def project_at(self, path):
        """A Rojo project's folder (the one with default.project.json; a *.project.json file stands for its folder),
        or a place file (.rbxl / .rbxlx: the file itself, or the one in a folder, the one named after the folder
        first)."""
        if not path:
            return None
        path = os.path.abspath(path)
        if os.path.isfile(path):
            low = path.lower()
            if low.endswith(".project.json"):
                return os.path.dirname(path)
            return path if low.endswith((".rbxl", ".rbxlx")) else None
        if not os.path.isdir(path):
            return None
        if os.path.isfile(os.path.join(path, "default.project.json")):
            return path
        places = sorted(f for f in os.listdir(path) if f.lower().endswith((".rbxl", ".rbxlx")))
        named = [f for f in places if os.path.splitext(f)[0].lower() == os.path.basename(path).lower()]
        pick = (named or places or [None])[0]
        return os.path.join(path, pick) if pick else None

    def project_name(self, project):
        """A Rojo project's `name`, else the place file's name, else the folder's (what the connector's hello says)."""
        folder = self.project_folder(project)
        if os.path.isdir(project):
            return _rojo_name(folder) or super().project_name(project)
        return os.path.splitext(os.path.basename(project))[0]

    def is_rojo(self, project):
        return bool(project) and os.path.isfile(os.path.join(self.project_folder(project), "default.project.json"))

    # -- the codex ---------------------------------------------------------------------------------------

    def codex_path(self, rel):
        """Kept at their project paths: a Rojo project's Luau (.lua, .luau), its project and model files (.json),
        toolchain files at the top, text places and models (.rbxlx, .rbxmx), outside hidden folders and package
        folders (Packages/: Wally's); the connector's export of the live place (.autopilot/roblox_codex/) at the
        codex's top."""
        parts = safe_rel(rel)
        if parts is None:
            return None
        if rel.startswith(EXPORT_PREFIX) and len(parts) > 2:
            dest = rel[len(EXPORT_PREFIX):]
            return dest if dest not in CODEX_OWN else None
        if any(p.startswith(".") or p in SKIP for p in parts[:-1]):
            return None
        if len(parts) == 1 and rel in TOP_FILES:
            return rel
        low = rel.lower()
        if low.endswith(CODE_EXT) or low.endswith((".rbxlx", ".rbxmx")):
            return rel
        if len(parts) > 1 and low.endswith(DATA_EXT) and not parts[-1].startswith("."):
            return rel
        if len(parts) == 1 and low.endswith(".project.json"):
            return rel
        return None

    def skip_dir(self, rel):
        name = rel.split("/")[-1]
        return name.startswith(".") or name in SKIP

    def has_export(self, codex_root):
        return os.path.exists(os.path.join(codex_root, "summary.txt"))

    def index_notes(self, codex_root):
        """Where to start in a Roblox place: the live export (summary, tree, tags, scripts) and a Rojo project's map
        of instances to folders."""
        lines = ["", "## Where to start", ""]
        if os.path.exists(os.path.join(codex_root, "summary.txt")):
            lines.append("- summary.txt: the place at a glance (services, scripts by kind, CollectionService tags).")
        if os.path.exists(os.path.join(codex_root, "tags.txt")):
            lines.append("- tags.txt: every CollectionService tag with its instances (game.actors('tag:<Tag>') "
                         "paths), where each is (studs, Roblox's axes) and its attributes.")
        if os.path.exists(os.path.join(codex_root, "tree.txt")):
            lines.append("- tree.txt: the place's instance tree, as Studio's search_game_tree gives it.")
        if os.path.isdir(os.path.join(codex_root, "place")):
            lines.append("- place/<Service>/...: the place's scripts as Studio has them (*.server.luau: Scripts, on "
                         "the server; *.client.luau: LocalScripts, on the player's client; *.luau: ModuleScripts).")
        maps = rojo_mappings(os.path.join(codex_root, "default.project.json"))
        if maps:
            lines.append("- A Rojo project (default.project.json) maps instances to folders: "
                         + "; ".join(f"{inst} <- {fs}" for inst, fs in maps[:20]))
            lines.append("  Script files: *.server.luau a Script, *.client.luau a LocalScript, other .luau a "
                         "ModuleScript; init.*.luau is the folder's own script.")
        lines.append("- Gameplay objects are usually found by tag (CollectionService:GetTagged / HasTag in the "
                     "code) and talk across the client/server split with RemoteEvents / RemoteFunctions "
                     "(ReplicatedStorage): read both sides.")
        return lines

    # -- the kit frame -----------------------------------------------------------------------------------

    def to_kit(self, v):
        return kit_of(v)

    def to_native(self, v):
        return native_of(v)

    def rotation_to_kit(self, r):
        """Orientation {"x","y","z"} (degrees; a CFrame as game.get reads it carries one) as (yaw, pitch)."""
        r = r or {}
        if isinstance(r.get("orientation"), dict):
            r = r["orientation"]
        return wrap(-float(r.get("y", 0.0))), wrap(float(r.get("x", 0.0)))

    def rotation_to_native(self, yaw, pitch=0.0):
        return {"x": float(pitch), "y": -wrap(yaw), "z": 0.0}

    # -- the game backend --------------------------------------------------------------------------------

    def name_of(self, path):
        """An instance's name from its path, as `view` names things ("Workspace/Track/Ramp@2" ->
        "Ramp@2"); a data-model prefix (server:) is left out."""
        path = DATAMODEL_PREFIX.sub("", str(path)).rstrip("/")
        return re.split(r"(?<!\\)/", path)[-1]

    def all_actors(self, game):
        """The place's things: Workspace's parts and models (through Folders) and what is tagged in it."""
        return list((game.request("find", {"things": True}) or {}).get("objects") or [])

    def exists(self, game, obj):
        """Whether the instance is in the data model now (one transforms read: a destroyed one is gone at once)."""
        return self.transforms(game, [obj]).get(obj) is not None

    def pose(self, game):
        """OpEngine's pose, with what a Humanoid also tells: its health, state, and whether it fell out of the
        world."""
        p = self._pose(game)
        if p.get("at") is None:
            raise GameError("pose: no player")
        at = Vec.of(p["at"])
        out = {"at": at, "eye": Vec.of(p.get("eye")) or at, "yaw": float(p.get("yaw") or 0.0),
               "pitch": float(p.get("pitch") or 0.0), "velocity": Vec.of(p.get("velocity")) or Vec(),
               "on_ground": p.get("on_ground")}
        out.update({k: p.get(k) for k in ("health", "max_health", "state", "fallen") if p.get(k) is not None})
        return out

    # -- a game for bench/run_autopilot.py --------------------------------------------------------------------

    def launch_command(self, project, url, log, map=None, exe=None):
        """Not a headless game: the connector, which plays the place open in Roblox Studio (Studio must be open on it,
        with its MCP server on) and connects to the brain at url as a plugin would. exe: the Python to run it with.
        Studio's whole console goes to console.txt beside the log (the bench keeps it with each prompt)."""
        console = os.path.join(os.path.dirname(os.path.abspath(log)), "console.txt")
        cmd = [exe or sys.executable, "-m", "autopilot.engines.roblox_connector", "connect", "--url", url, "--log", log,
               "--console", console]
        if project:
            cmd += ["--project", project]
        return cmd


# -- the Luau the reads run --------------------------------------------------------------------------------------

_prelude = None


def prelude():
    global _prelude
    if _prelude is None:
        with open(PRELUDE_FILE, encoding="utf-8") as f:
            _prelude = f.read()
    return _prelude


def long_string(text):
    """text as a Luau long-bracket string literal ([==[...]==], with as many = as it needs)."""
    level = 0
    while f"]{'=' * level}]" in text:
        level += 1
    eq = "=" * level
    return f"[{eq}[{text}]{eq}]"


def strip_none(v):
    """A JSON value without nulls (HttpService:JSONDecode would leave holes in arrays)."""
    if isinstance(v, dict):
        return {k: strip_none(x) for k, x in v.items() if x is not None}
    if isinstance(v, (list, tuple)):
        return [strip_none(x) for x in v if x is not None]
    return v


def luau_chunk(ops, player=None, nonce=None, echo=False):
    """The Luau sent to execute_luau for a batch of ops [(op, args)]: the prelude, then RUN(REQ). REQ goes as JSON in
    a long string, decoded in Studio."""
    req = strip_none({"ops": [[op, args or {}] for op, args in ops], "player": player or "", "nonce": nonce or "",
                      "echo": bool(echo)})
    return (prelude() + "\nreturn RUN(game:GetService(\"HttpService\"):JSONDecode("
            + long_string(json.dumps(req, separators=(",", ":"))) + "))\n")


def parse_answer(text, nonce=None):
    """The {"t", "r": [...]} a chunk answered, from execute_luau's text: between the nonce markers when it printed
    them, else the first JSON object with "r" in it (the tool may wrap the returned value in words, or quote it).
    None when there is none."""
    if text is None:
        return None
    text = str(text)
    if nonce and nonce in text:
        parts = text.split(nonce)
        for inner in parts[1::2]:
            got = _json_with(inner.strip(), "r")
            if got is not None:
                return got
    return _json_with(text, "r")


def _json_with(text, key):
    s = text.strip()
    if s.startswith('"'):  # (the value as a quoted string)
        try:
            inner = json.loads(s)
            if isinstance(inner, str):
                s = inner
        except ValueError:
            pass
    dec = json.JSONDecoder()
    i = s.find("{")
    while i != -1:
        try:
            obj, _ = dec.raw_decode(s, i)
            if isinstance(obj, dict) and key in obj:
                return obj
        except ValueError:
            pass
        i = s.find("{", i + 1)
    return None


class OpFailed(Exception):
    """An op answered an error (status, message), as a bridge error answer."""

    def __init__(self, status, error):
        super().__init__(f"{status}: {error}")
        self.status, self.error = status, error


def failed(message):
    msg = str(message)
    status = 404 if "does not exist" in msg or "not found" in msg.lower() else 400
    return OpFailed(status, msg)


# -- coalescing reads ----------------------------------------------------------------------------------------------

class Coalescer:
    """Luau ops that come in together go to Studio as one execute_luau call per data model (a harness's game.batch:
    the reads of an observe resolve in one call, one frame of the place). While a call is out, the ops that come in
    wait for it and go together in the next."""

    def __init__(self, run_batch, window=COALESCE_SECONDS):
        self.run_batch = run_batch  # fn(datamodel, [(op, args)]) -> [result or OpFailed], game time
        self.window = window
        self.queue = queue.Queue()
        self.calls = 0  # execute_luau calls made
        self.ops = 0  # ops answered
        threading.Thread(target=self._work, name="roblox-reads", daemon=True).start()

    def submit(self, datamodel, op, args, timeout=None):
        """One op's answer (its result, the game time), raising OpFailed for an error answer."""
        done = threading.Event()
        box = {}
        self.queue.put((datamodel, op, args, done, box))
        if not done.wait(timeout or mcp.TIMEOUT + 15):
            raise mcp.McpTimeout(f"Studio didn't answer {op} in time")
        if "exc" in box:
            raise box["exc"]
        if isinstance(box["result"], OpFailed):
            raise box["result"]
        return box["result"], box.get("t")

    def _work(self):
        while True:
            items = [self.queue.get()]
            deadline = time.monotonic() + self.window
            while True:
                try:
                    items.append(self.queue.get(timeout=max(0.0, deadline - time.monotonic())))
                except queue.Empty:
                    break
            by_model = {}
            for item in items:
                by_model.setdefault(item[0], []).append(item)
            for model, group in by_model.items():
                self.calls += 1
                try:
                    results, t = self.run_batch(model, [(i[1], i[2]) for i in group])
                    for item, r in zip(group, results):
                        item[4]["result"], item[4]["t"] = r, t
                except Exception as e:
                    for item in group:
                        item[4]["exc"] = e
                self.ops += len(group)
                for item in group:
                    item[3].set()


# -- the backend: the protocol's ops over Studio's tools -------------------------------------------------------------

class StudioBackend:
    """The protocol's requests (op, args) answered through Studio's MCP tools: what the connector serves the brain.
    Values it answers are in the kit's frame where the protocol says so (find, transforms, pose, movement, view) and
    Roblox's own for rc (game.get / game.set: studs, Roblox's axes)."""

    def __init__(self, studio, project=None, emit=None, console_file=None):
        self.studio = studio  # a roblox_mcp.Studio
        self.console_file = console_file  # a path every line of Studio's console is also appended to (--console)
        self.engine = RobloxMcp()
        self.project = project  # the Rojo project folder or place file on this machine, if known
        self.emit = emit or (lambda event: None)  # events for the brain: log lines, findings
        self.running = None  # whether a play session runs (None: not known yet)
        self.player = ""  # the character to play, when the brain named one (context's player)
        self.t = 0.0
        self.nonce = "@@AP" + uuid.uuid4().hex[:10] + "@@"
        self.echo = (os.environ.get(mcp.ENV_RESULT) or "").strip().lower() == "print"
        self.reads = Coalescer(self._run_batch)
        self.held = {}  # Roblox KeyCode -> lease end (time.monotonic())
        self.held_buttons = {}  # mouse button -> lease end
        self.inputs = queue.Queue()
        self.inputs_pending = 0  # inputs posted and not yet sent to Studio (reads wait for them: see _run_batch)
        self.inputs_done = threading.Condition()
        self.async_errors = []
        self.pointer_at = None  # (x, y) viewport pixels where the mouse is (None: the middle)
        self.viewport = (1280.0, 720.0)
        self._place = ""
        self.console_text = ""
        self.console_seen = []
        self.findings_seen = set()
        self.lock = threading.Lock()
        self.stopped = threading.Event()
        self.input_thread = threading.Thread(target=self._input_worker, name="roblox-inputs", daemon=True)
        self.input_thread.start()
        threading.Thread(target=self._lease_worker, name="roblox-leases", daemon=True).start()

    # -- answering ------------------------------------------------------------------------------------------

    def handle(self, op, args=None):
        """(result, game time) of a request; raises OpFailed for an error answer."""
        args = args or {}
        fn = getattr(self, f"op_{op}", None)
        if fn is None:
            raise OpFailed(400, f"unknown op {op}")
        try:
            result = fn(args)
        except OpFailed:
            raise
        except mcp.ToolMissing as e:
            raise OpFailed(501, str(e)) from None
        except mcp.McpError as e:
            raise OpFailed(503, str(e)) from None
        return result, self.t

    def post(self, op, args=None, on_done=None):
        """An input (key, input, pointer, ui_act, move): run in order on the input thread, so a key's hold and its
        release reach Studio in the order they were sent. A held key's renewal only extends its lease, at once: it
        never waits behind the input calls queued before it. on_done(result, error): called when it has run."""
        args = args or {}
        if op == "key" and args.get("mode") == "hold" and not args.get("seconds") and self._renew(args):
            if on_done:
                on_done({}, None)
            return
        with self.inputs_done:
            self.inputs_pending += 1
        self.inputs.put((op, args, on_done))

    def _renew(self, args):
        key = str(args.get("key") or "")
        lease = time.monotonic() + float(args.get("lease") or 0.6)
        with self.lock:
            if key in MOUSE_BUTTONS and MOUSE_BUTTONS[key] in self.held_buttons:
                self.held_buttons[MOUSE_BUTTONS[key]] = max(self.held_buttons[MOUSE_BUTTONS[key]], lease)
                return True
            if keycode(key) in self.held:
                self.held[keycode(key)] = max(self.held[keycode(key)], lease)
                return True
        return False

    def _input_worker(self):
        while True:
            op, args, on_done = self.inputs.get()
            result, error = None, None
            try:
                result = self.handle(op, args)
            except Exception as e:  # (kept: the brain reads them as async errors)
                error = e
                self.async_errors = (self.async_errors + [str(getattr(e, "error", e))])[-20:]
            finally:
                if on_done:
                    try:
                        on_done(result, error)
                    except Exception:
                        pass
                with self.inputs_done:
                    self.inputs_pending -= 1
                    self.inputs_done.notify_all()

    def wait_for_inputs(self, seconds=5.0):
        """Wait until the inputs posted so far have gone to Studio (a read after a key press sees the press, as
        within one frame of a plugin's game); whether they all had. An input's own reads (a pointer at a world point)
        don't wait: they come from the input thread itself."""
        if threading.current_thread() is self.input_thread:
            return True
        deadline = time.monotonic() + seconds
        with self.inputs_done:
            while self.inputs_pending > 0:
                left = deadline - time.monotonic()
                if left <= 0:
                    return False
                self.inputs_done.wait(left)
        return True

    # -- Luau reads -----------------------------------------------------------------------------------------

    def datamodel(self, args=None, write=False):
        """The data model an op runs in: the one asked for (args "datamodel"), else, while the game plays, the
        player's client for reads (what the player's copy shows) and the server for writes (its truth); else Edit."""
        asked = str((args or {}).get("datamodel") or "").strip().capitalize()
        if asked in mcp.DATAMODELS:
            return asked
        if not self.is_running():
            return "Edit"
        return "Server" if write else "Client"

    def luau(self, op, args=None, datamodel=None):
        model = datamodel or self.datamodel(args)
        self.wait_for_inputs()  # (on the caller's thread: the input thread's own reads go at once)
        try:
            result, t = self.reads.submit(model, op, args or {})
        except mcp.McpError:
            if model != "Edit":
                self.running = None  # (the play session may have ended in Studio itself: asked again next time)
            raise
        return result

    def _run_batch(self, datamodel, ops):
        code = luau_chunk(ops, self.player, self.nonce, self.echo)
        text = self.studio.execute_luau(code, datamodel=datamodel)
        got = parse_answer(text, self.nonce)
        if got is None and not self.echo:
            # (the server gives back only what a chunk prints, not what it returns: print from now on)
            self.echo = True
            text = self.studio.execute_luau(luau_chunk(ops, self.player, self.nonce, True), datamodel=datamodel)
            got = parse_answer(text, self.nonce)
        if got is None:
            raise mcp.McpError(f"execute_luau answered no JSON in {datamodel}: {str(text)[:300]}")
        if got.get("error"):
            raise mcp.McpError(f"execute_luau: {got['error']}")
        if isinstance(got.get("t"), (int, float)):
            self.t = float(got["t"])
        answers = got.get("r") or []
        out = []
        for i in range(len(ops)):
            a = answers[i] if i < len(answers) and isinstance(answers[i], dict) else {"ok": False, "e": "no answer"}
            out.append(a.get("v") if a.get("ok") else failed(a.get("e") or "error"))
        return out, self.t

    # -- the session --------------------------------------------------------------------------------------

    def is_running(self):
        """Whether a play session runs: from get_studio_state when Studio says, else what this connector did, else
        whether the Client data model answers."""
        if self.running is None:
            state = self._studio_says_running()
            if state is None:
                try:
                    self.studio.execute_luau("return 1", datamodel="Client", timeout=10)
                    state = True
                except mcp.McpError:
                    state = False
            self.running = state
        return self.running

    def _studio_says_running(self):
        """True / False when get_studio_state's answer says it plainly, else None. The live server: text lines,
        "- Current Studio Mode: Edit" or "... Play"."""
        try:
            state = self.studio.studio_state()
        except mcp.McpError:
            return None
        m = re.search(r"current studio mode:\s*(\w+)", str(state or ""), re.I) if not isinstance(state, dict) else None
        if m:
            return m.group(1).lower() != "edit"
        if isinstance(state, dict):
            for key in ("is_playing", "isPlaying", "playing", "running", "is_running"):
                if isinstance(state.get(key), bool):
                    return state[key]
            mode = str(state.get("mode") or state.get("state") or state.get("play_state") or "").lower()
        else:
            mode = str(state or "").lower()
        if any(w in mode for w in ("stop", "edit")):
            return False
        if any(w in mode for w in ("play", "run")):
            return True
        return None

    def op_session(self, args):
        action = args.get("action") or "status"
        place = self.place_name()
        if args.get("map") and args["map"] not in (place, self.project):
            raise OpFailed(404, f"Roblox plays the place open in Studio ({place}): {args['map']} isn't it")
        if action == "status":
            return {"running": self.is_running(), "loading": False, "map": place}
        if action == "start":
            if not self.is_running():
                self._play(True)
            self._wait_for_character()
            return {"running": True, "map": place}
        if action == "stop":
            if self.is_running():
                self._play(False)
            return {"running": False}
        if action == "restart":
            if self.is_running():
                self._play(False)
            self._play(True)
            return {"running": True, "map": place, "character": self._wait_for_character()}
        raise OpFailed(400, f"session: unknown action {action}")

    def _play(self, start):
        self.release_everything()
        self.studio.start_stop_play(start)
        self.running = bool(start)
        self.console_text, self.console_seen = "", []
        self.emit({"kind": "session", "t": 0.0, "running": bool(start), "scene": self.place_name()})

    def _wait_for_character(self, seconds=RESTART_SECONDS):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                c = self.luau("context", {}, "Client")
                if c.get("character"):
                    return c["character"]
            except (OpFailed, mcp.McpError):
                pass
            time.sleep(0.25)
        return ""

    def op_pause(self, args):
        return {"paused": False, "why": "Roblox Studio's MCP server can't pause a play session: the game runs on "
                                        "while the agent reads and thinks"}

    def op_ping(self, args):
        return {"t": self.t}

    def op_levels(self, args):
        place = self.place_name()
        return {"levels": [place], "open": place, "playing": place if self.is_running() else ""}

    def place_name(self):
        """The place's name: its Rojo project's or file's, else the name Studio gives it (game.Name, without a file
        extension), else "place"."""
        if self.project:
            return self.engine.project_name(self.project)
        if not self._place:
            try:
                self._place = str((self.luau("context", {}, "Edit") or {}).get("place") or "")
            except (OpFailed, mcp.McpError):
                pass
        return re.sub(r"\.rbxlx?$", "", self._place, flags=re.I) or "place"

    def op_timestep(self, args):
        raise OpFailed(501, "Roblox: no fixed time step through Studio's MCP server")

    def op_record(self, args):
        if args.get("action") == "stop":
            return {"recording": False}
        raise OpFailed(501, "Roblox: no video through Studio's MCP server, only stills (capture)")

    # -- reads ---------------------------------------------------------------------------------------------

    def op_context(self, args):
        if "player" in args:
            self.player = str(args.get("player") or "")
        c = self.luau("context", {})
        if c.get("place") and not self._place:
            self._place = c["place"]
        vp = c.get("viewport") or {}
        if vp.get("x") and vp.get("y"):
            self.viewport = (float(vp["x"]), float(vp["y"]))
        folder = self.engine.project_folder(self.project) if self.project else ""
        return {"world": c.get("place") or self.place_name(), "character": c.get("character") or "",
                "controller": "", "camera_manager": c.get("camera") or "", "player": c.get("player") or "",
                "game_time": self.t, "project_dir": folder, "controls": [], "tags": c.get("tags") or [],
                "datamodel": "Client" if c.get("client") else ("Server" if c.get("running") else "Edit"),
                "input": {"notes": INPUT_NOTES, "keys": True}}

    def op_find(self, args):
        return self.luau("find", {k: args[k] for k in ("class", "name", "limit", "things") if k in args})

    def op_transforms(self, args):
        got = obj((self.luau("transforms", {"objects": list(args.get("objects") or [])}) or {}).get("objects"))
        out = {}
        for path in args.get("objects") or []:
            t = got.get(path)
            if not t:
                out[path] = None
            elif t.get("location") is None:  # (an instance with no place in the world: a Folder, a script)
                out[path] = {"location": None, "active": t.get("active")}
            else:
                rot = t.get("rotation") or {}
                out[path] = {"location": kit_of(t["location"]).ue(), "center": kit_of(t.get("center")).ue(),
                             "extent": kit_extent(t.get("size")).ue(),
                             "velocity": kit_of(t.get("velocity") or {}).ue(),
                             "rotation": {"Pitch": float(rot.get("x", 0.0)), "Yaw": wrap(-float(rot.get("y", 0.0))),
                                          "Roll": float(rot.get("z", 0.0))},
                             "active": t.get("active")}
        return {"objects": out}

    def op_pose(self, args):
        p = self.luau("pose", {})
        if not p.get("at"):
            return {"at": None}
        yaw, pitch = yaw_pitch(kit_dir(p.get("look")))
        return {"at": kit_of(p["at"]).ue(), "eye": kit_of(p.get("eye") or p["at"]).ue(), "yaw": yaw, "pitch": pitch,
                "velocity": kit_of(p.get("velocity") or {}).ue(), "on_ground": p.get("on_ground"),
                "state": p.get("state"), "health": p.get("health"), "max_health": p.get("max_health"),
                "fallen": p.get("fallen")}

    def op_movement(self, args):
        return movement_in_kit(self.luau("movement", {}))

    def op_rc(self, args):
        """game.get / props / set / call / describe, on instance paths; a path may start with server: / client: /
        edit: to read or write that data model's copy."""
        verb, body = args.get("verb"), args.get("body") or {}
        if verb == "probe":
            return {}
        path = str(body.get("objectPath") or "")
        m = DATAMODEL_PREFIX.match(path)
        model = m.group(1).capitalize() if m else None
        path = DATAMODEL_PREFIX.sub("", path)
        if verb == "describe":
            return self.luau("describe", {"path": path}, model or self.datamodel())
        if verb == "property":
            prop = body.get("propertyName")
            if body.get("access") == "WRITE_ACCESS":
                value = (body.get("propertyValue") or {}).get(prop)
                self.luau("set", {"path": path, "prop": prop, "value": value}, model or self.datamodel(write=True))
                return {}
            if not prop:
                return obj(self.luau("props", {"path": path}, model or self.datamodel()))
            got = self.luau("get", {"path": path, "prop": prop}, model or self.datamodel())
            return {prop: (got or {}).get("value")}
        if verb == "call":
            params = dict(body.get("parameters") or {})
            given = params.pop("args", None)
            positional = list(given) if isinstance(given, (list, tuple)) else list(params.values())
            return self.luau("call", {"path": path, "fn": body.get("functionName"), "args": positional},
                             model or self.datamodel(write=True))
        raise OpFailed(400, f"rc: unknown verb {verb}")

    def op_screen(self, args):
        return {"texts": obj((self.luau("screen", {}) or {}).get("texts"))}

    def op_ui(self, args):
        return self.luau("ui", {})

    def op_view(self, args):
        a = {"max_count": args.get("max_count"), "only": args.get("only"), "sonar": bool(args.get("sonar")),
             "sonar_turns": args.get("sonar_turns")}
        if args.get("max_distance"):
            a["max_distance"] = float(args["max_distance"]) / CM_PER_STUD
        if args.get("sonar_reach"):
            a["sonar_reach"] = float(args["sonar_reach"]) / CM_PER_STUD
        return view_in_kit(self.luau("view", {k: v for k, v in a.items() if v is not None}))

    def op_luau(self, args):
        """Raw Luau in a data model (research): execute_luau's text."""
        code = str(args.get("code") or "")
        if not code.strip():
            raise OpFailed(400, "luau: no code")
        return {"output": self.studio.execute_luau(code, datamodel=self.datamodel(args, write=True))}

    def op_inspect(self, args):
        return {"text": self.studio.inspect_instance(mcp.dot_path(DATAMODEL_PREFIX.sub("", str(args.get("path")))))}

    def op_grep(self, args):
        return {"text": self.studio.script_grep(str(args.get("pattern") or ""))}

    def op_watch(self, args):
        """Things' moves and size changes over `seconds`, from their transforms read every half second."""
        objects, seconds = list(args.get("objects") or []), float(args.get("seconds") or 1.0)
        if not objects:
            time.sleep(min(seconds, 0.05))
            return {"changes": []}
        moved = float(args.get("moved") or 2.0) * 100.0  # (metres in the protocol: cm here)
        first = self.op_transforms({"objects": objects})["objects"]
        changes, found = [], set()
        t0 = time.monotonic()
        while time.monotonic() - t0 < seconds:
            time.sleep(min(0.5, max(0.0, seconds - (time.monotonic() - t0))))
            now = self.op_transforms({"objects": objects})["objects"]
            for o in objects:
                a, b = first.get(o) or {}, now.get(o) or {}
                if a and not b and (o, "gone") not in found:
                    found.add((o, "gone"))
                    changes.append({"t": self.t, "object": o, "what": "gone"})
                if a.get("location") and b.get("location"):
                    if Vec.of(a["location"]).dist(Vec.of(b["location"])) > moved and (o, "moved") not in found:
                        found.add((o, "moved"))
                        changes.append({"t": self.t, "object": o, "what": "moved"})
                    if (a.get("extent") and b.get("extent") and (o, "size") not in found
                            and (Vec.of(a["extent"]) - Vec.of(b["extent"])).length > 1.0):
                        found.add((o, "size"))
                        changes.append({"t": self.t, "object": o, "what": "changed size"})
        return {"changes": changes}

    # -- pictures ------------------------------------------------------------------------------------------

    def op_capture(self, args):
        data, mime = self.studio.screen_capture()
        if not data:
            raise OpFailed(503, "screen_capture gave no picture")
        jpeg, w, h = to_jpeg(data, int(args.get("max_dim") or 1280), int(args.get("quality") or 80))
        return {"jpeg": base64.b64encode(jpeg).decode("ascii"), "width": w, "height": h, "t": self.t}

    # -- inputs --------------------------------------------------------------------------------------------

    def op_key(self, args):
        key, mode = str(args.get("key") or ""), args.get("mode") or "press"
        if mode == "release_all":
            self.release_everything()
            return {}
        if not key:
            return {}  # (the brain's probe)
        if key in ("MouseX", "MouseY"):
            if mode == "press":
                self.turn(float(args.get("value") or 0.0), key)
            return {}
        if key in ("MouseScrollUp", "MouseScrollDown", "MouseWheelAxis"):
            delta = float(args.get("value") or 1.0) * (-1 if key == "MouseScrollDown" else 1)
            notches = max(1, min(WHEEL_NOTCHES, round(abs(delta))))
            x, y = self.pointer()
            self.studio.mouse([mcp.mouse_scroll(delta > 0, x, y)] * notches)
            return {}
        if key in MOUSE_BUTTONS:
            return self._button(MOUSE_BUTTONS[key], mode, float(args.get("lease") or 0.6))
        code = keycode(key)
        lease = time.monotonic() + float(args.get("lease") or 0.6)
        if mode == "press":
            self.studio.keyboard([mcp.key_action("press", code)])
        elif mode == "hold":
            with self.lock:
                new = code not in self.held
                self.held[code] = lease
            if new:  # (a renewal only extends the lease: no call)
                self.studio.keyboard([mcp.key_action("down", code)])
            if args.get("seconds"):
                with self.lock:
                    self.held[code] = time.monotonic() + float(args["seconds"])
        elif mode == "release":
            with self.lock:
                was = self.held.pop(code, None)
            if was is not None:
                self.studio.keyboard([mcp.key_action("up", code)])
        return {}

    def pointer(self):
        """Where the mouse is (viewport pixels): where game.pointer put it, else the middle of the view."""
        return self.pointer_at or (self.viewport[0] / 2, self.viewport[1] / 2)

    def _button(self, button, mode, lease):
        if button not in mcp.MOUSE:
            raise OpFailed(400, f"Studio's MCP mouse has no {button} button (left and right only)")
        x, y = self.pointer()
        if mode == "press":
            self.studio.mouse([mcp.mouse_click(button, x, y)])
        elif mode == "hold":
            with self.lock:
                new = button not in self.held_buttons
                self.held_buttons[button] = time.monotonic() + lease
            if new:
                self.studio.mouse([mcp.mouse_button("down", button, x, y)])
        elif mode == "release":
            with self.lock:
                was = self.held_buttons.pop(button, None)
            if was is not None:
                self.studio.mouse([mcp.mouse_button("up", button, x, y)])
        return {}

    def turn(self, counts, axis):
        """Turn Roblox's camera by `counts` (DEGREES_PER_COUNT each): MouseX right, MouseY up. The `turn` op sets the
        camera's look in the player's client, and Roblox's camera scripts carry on from there, as after a right-drag
        (on the live server the MCP mouse's right-drag doesn't reach the camera)."""
        degrees = float(counts) * DEGREES_PER_COUNT
        self.luau("turn", {"yaw": degrees, "pitch": 0.0} if axis == "MouseX" else {"yaw": 0.0, "pitch": degrees},
                  "Client")

    def op_input(self, args):
        if args.get("mode") == "release":
            return {}
        raise OpFailed(404, f"Roblox has no named input actions here ({args.get('action')!r}): press its keys")

    def op_pointer(self, args):
        if "screen_x" in args:
            sx, sy = float(args.get("screen_x") or 0.0), float(args.get("screen_y") or 0.0)
            x, y = (sx + 1) / 2 * self.viewport[0], (1 - sy) / 2 * self.viewport[1]
            on = abs(sx) <= 1 and abs(sy) <= 1
        else:
            a = {"object": DATAMODEL_PREFIX.sub("", args["object"])} if args.get("object") else {
                "world": native_of(args.get("world"))}
            p = self.luau("point", a)
            vp = p.get("viewport") or {}
            if vp.get("x"):
                self.viewport = (float(vp["x"]), float(vp["y"]))
            x, y, on = float(p.get("x") or 0.0), float(p.get("y") or 0.0), bool(p.get("on_screen"))
            sx, sy = x / self.viewport[0] * 2 - 1, 1 - y / self.viewport[1] * 2
        self.pointer_at = (x, y)
        self.studio.mouse([mcp.mouse_move(x, y)])
        return {"screen_x": sx, "screen_y": sy, "x": x, "y": self.viewport[1] - y, "on_screen": on}

    def op_ui_act(self, args):
        target, action = str(args.get("id") or ""), args.get("action") or "click"
        dot = mcp.dot_path(target)
        if action in ("click", "double_click", "right_click"):
            button = "right" if action == "right_click" else "left"
            clicks = [mcp.mouse_click(button)] * (2 if action == "double_click" else 1)
            self.studio.mouse([mcp.mouse_move(target=dot)] + clicks)
        elif action == "set":
            text = args.get("text") if args.get("text") is not None else args.get("value")
            self.studio.keyboard([mcp.text_action(str(text if text is not None else ""), target=dot)])
        else:
            raise OpFailed(400, f"ui_act: Roblox clicks and types only ({action})")
        return {"ok": True, "id": target}

    def op_move(self, args):
        """Walk the character with character_navigation: to a kit point (`to`), an object (`object`), or `direction`
        (kit) for `seconds` at its walk speed."""
        if args.get("object"):
            self.studio.navigate(instance=mcp.dot_path(DATAMODEL_PREFIX.sub("", args["object"])))
            return {"moved": True, "via": "navigation"}
        if args.get("to") is not None:
            target = Vec.of(args["to"])
        else:
            d = Vec.of(args.get("direction") or {})
            if d is None or d.length < 1e-6:
                raise OpFailed(400, "move: a direction, a point (to) or an object")
            at = Vec.of(self.op_pose({})["at"] or {})
            speed = movement_in_kit(self.luau("movement", {})).get("walk_speed") or 16 * CM_PER_STUD
            target = at + d.flat.unit * (speed * float(args.get("seconds") or 1.0) * float(args.get("scale") or 1.0))
        self.studio.navigate(position=native_of(target))
        return {"moved": True, "via": "navigation"}

    def release_everything(self):
        with self.lock:
            keys, buttons = list(self.held), list(self.held_buttons)
            self.held.clear()
            self.held_buttons.clear()
        try:
            if keys:
                self.studio.keyboard([mcp.key_action("up", k) for k in keys])
            if buttons:
                x, y = self.pointer()
                self.studio.mouse([mcp.mouse_button("up", b, x, y) for b in buttons])
        except mcp.McpError as e:
            self.async_errors = (self.async_errors + [str(e)])[-20:]

    def _lease_worker(self):
        """Lets go of a held key whose lease ran out (the brain stopped renewing it: a crashed harness)."""
        while not self.stopped.wait(0.1):
            now = time.monotonic()
            with self.lock:
                keys = [k for k, end in self.held.items() if end < now]
                buttons = [b for b, end in self.held_buttons.items() if end < now]
                for k in keys:
                    self.held.pop(k, None)
                for b in buttons:
                    self.held_buttons.pop(b, None)
            try:
                if keys:
                    self.studio.keyboard([mcp.key_action("up", k) for k in keys])
                if buttons:
                    x, y = self.pointer()
                    self.studio.mouse([mcp.mouse_button("up", b, x, y) for b in buttons])
            except mcp.McpError:
                pass

    # -- the errors the game logs ------------------------------------------------------------------------

    def poll_console(self):
        """Read Studio's console; send the errors and warnings it has since the last read (`log` events, and a
        log_error finding per error). Returns how many lines were new."""
        text = self.studio.console_output() or ""
        if self.console_text and text.startswith(self.console_text):
            new = text[len(self.console_text):].splitlines()
        else:
            seen = set(self.console_seen)
            new = [line for line in text.splitlines() if line not in seen]
        self.console_text = text
        self.console_seen = (self.console_seen + new)[-2000:]
        kept = [line for line in new if line.strip()]
        if self.console_file and kept:
            # (Studio keeps only the end of its console: this keeps all of it. A line can come twice, after a restart)
            with open(self.console_file, "a", encoding="utf-8") as f:
                f.write("\n".join(kept) + "\n")
        n = 0
        for line in new:
            line = line.strip()
            if not line or self.nonce in line:
                continue
            n += 1
            level = console_level(line)
            if not level:
                continue
            self.emit({"kind": "log", "t": self.t, "verbosity": level, "category": "Luau", "message": line[:2000]})
            if level == "Error":
                sig = "log_error:" + re.sub(r"\d+", "#", line)[:200]
                if sig not in self.findings_seen:
                    self.findings_seen.add(sig)
                    self.emit({"kind": "finding", "t": self.t, "finding": {
                        "detector": "log_error", "severity": "error", "summary": f"Luau error: {line[:300]}",
                        "signature": sig}})
        return n

    # -- the codex -----------------------------------------------------------------------------------------

    def op_codex_manifest(self, args):
        """The codex's files: a Rojo project's from disk (this machine's), else every script of the place (by its
        Source's hash, read with script_read); and the live export (summary, tree, tags)."""
        self._codex = {}
        files = []
        if self.project and self.engine.is_rojo(self.project):
            folder = self.engine.project_folder(self.project)
            for rel in self.engine.walk_project(folder):
                if self.engine.codex_path(rel):
                    full = os.path.join(folder, rel)
                    with open(full, "rb") as f:
                        data = f.read()
                    files.append({"path": rel, "size": len(data), "hash": hashlib.md5(data).hexdigest()})
                    self._codex[rel] = ("disk", full)
        else:
            for s in (self.luau("scripts", {"hash": True}, "Edit") or {}).get("scripts") or []:
                rel = EXPORT_PREFIX + script_file(s["path"], s.get("class"))
                files.append({"path": rel, "size": int(s.get("size") or 0), "hash": str(s.get("hash"))})
                self._codex[rel] = ("script", s["path"])
        for name, text in self.export_files().items():
            data = text.encode("utf-8")
            files.append({"path": EXPORT_PREFIX + name, "size": len(data), "hash": hashlib.md5(data).hexdigest()})
            self._codex[EXPORT_PREFIX + name] = ("text", text)
        return {"files": files, "export": {"running": False, "done": len(files), "total": len(files)}}

    def op_codex_read(self, args):
        if not hasattr(self, "_codex"):
            self.op_codex_manifest({})
        out, missing = {}, []
        for rel in args.get("paths") or []:
            kind, what = self._codex.get(rel, (None, None))
            try:
                if kind == "disk":
                    with open(what, encoding="utf-8", errors="replace") as f:
                        out[rel] = f.read()
                elif kind == "text":
                    out[rel] = what
                elif kind == "script":
                    out[rel] = self.read_script(what)
                else:
                    missing.append(rel)
            except (OSError, mcp.McpError, OpFailed):
                missing.append(rel)
        return {"files": out, "missing": missing}

    def read_script(self, path):
        """A script's source: script_read on its dot path, else its Source read with execute_luau."""
        if self.studio.has(mcp.SCRIPT_READ):
            try:
                return self.studio.script_read(mcp.dot_path(path))
            except mcp.McpError:
                pass
        got = self.luau("get", {"path": path, "prop": "Source"}, "Edit")
        return str((got or {}).get("value") or "")

    def export_files(self):
        """summary.txt, tags.txt and tree.txt of the place as Studio has it now."""
        out = {}
        try:
            scripts = (self.luau("scripts", {}, "Edit") or {}).get("scripts") or []
        except (OpFailed, mcp.McpError):
            scripts = []
        try:
            tags = obj((self.luau("tags", {}, "Edit") or {}).get("tags"))
        except (OpFailed, mcp.McpError):
            tags = {}
        kinds = {}
        for s in scripts:
            kinds.setdefault(s.get("class"), []).append(s["path"])
        lines = [f"# Roblox place: {self.place_name()}", "",
                 f"Scripts: {len(scripts)} ("
                 + ", ".join(f"{len(v)} {k}" for k, v in sorted(kinds.items(), key=lambda kv: str(kv[0]))) + ")",
                 f"CollectionService tags: {', '.join(f'{t} ({len(v)})' for t, v in sorted(tags.items())) or 'none'}",
                 f"Rojo project: {'yes (its files are in the codex as they are on disk)' if self.project and self.engine.is_rojo(self.project) else 'no'}",  # noqa: E501
                 "", "Scripts by service:"]
        for s in scripts:
            extra = f" RunContext={s['run_context']}" if s.get("run_context") not in (None, "Legacy") else ""
            lines.append(f"  {s['path']} ({s.get('class')}{extra})")
        out["summary.txt"] = "\n".join(lines) + "\n"
        tag_lines = ["# CollectionService tags: instance paths (game.actors('tag:<Tag>')), location in studs on "
                     "Roblox's axes (x right, y up, -z forward), attributes", ""]
        for tag in sorted(tags):
            tag_lines.append(f"{tag} ({len(tags[tag])}):")
            for item in tags[tag]:
                at = item.get("at")
                where = f" at ({at['x']:.1f}, {at['y']:.1f}, {at['z']:.1f})" if at else ""
                attrs = f" {json.dumps(item['attributes'])}" if item.get("attributes") else ""
                tag_lines.append(f"  {item['path']} [{item.get('class')}]{where}{attrs}")
        out["tags.txt"] = "\n".join(tag_lines) + "\n"
        if self.studio.has(mcp.SEARCH_GAME_TREE):
            parts = ["# The place's instance tree (Studio's search_game_tree, by service)", ""]
            for svc in ("Workspace", "ReplicatedStorage", "ServerScriptService", "ServerStorage", "StarterGui",
                        "StarterPlayer", "StarterPack", "ReplicatedFirst", "Lighting", "Teams"):
                try:
                    text = self.studio.search_game_tree(path=svc, depth=6, datamodel=self.datamodel(write=True))
                except mcp.McpError as e:
                    text = f"(couldn't read: {e})"
                parsed = mcp.parse_json(text)
                parts += [f"## {svc}", json.dumps(parsed, indent=1) if parsed is not None else text, ""]
            out["tree.txt"] = "\n".join(parts)
        return out

    def close(self):
        self.stopped.set()
        self.release_everything()
        self.studio.close()


def obj(v):
    """v as a dict: Roblox's JSONEncode writes an empty table as [], whatever it was meant to hold."""
    return v if isinstance(v, dict) else {}


def keycode(key):
    """An FKey name (or a Roblox KeyCode name) as Roblox's KeyCode name."""
    return KEYCODES.get(key, key)


def console_level(line):
    """"Error", "Warning" or None for a console line, by its words: Studio's get_console_output doesn't say a line's
    message type (an error, a warn() and a print() look alike)."""
    low = line.lower()
    if re.search(r"\b(error|exception)\b|attempt to (index|call|perform|compare|concatenate)|is not a valid member|"
                 r"stack begin|script timeout|unable to cast|missing argument", low):
        return "Error"
    if re.search(r"\bwarn(ing)?\b|infinite yield possible", low):
        return "Warning"
    return None


def movement_in_kit(m):
    """The movement op's Humanoid numbers (studs) as the kit's (cm): walk speed, the jump's take-off speed (JumpPower
    is one; JumpHeight gives sqrt(2 g h)), gravity, the half height (HumanoidRootPart's middle above the feet: HipHeight
    plus half its height), radius, max slope; full air control (Roblox's Humanoid steers in the air). Step: None (a
    Humanoid has no step height to read)."""
    m = m or {}
    g = float(m.get("gravity") or 196.2)
    if m.get("use_jump_power"):
        jump = float(m.get("jump_power") or 0.0)
    else:
        jump = math.sqrt(2 * g * max(0.0, float(m.get("jump_height") or 0.0)))
    half = (float(m.get("hip_height") or 0.0) + float(m.get("root_half") or 0.0)) * CM_PER_STUD
    out = {"walk_speed": float(m.get("walk_speed") or 0.0) * CM_PER_STUD, "jump_z": jump * CM_PER_STUD,
           "gravity": g * CM_PER_STUD, "half_height": half or None, "step": None,
           "max_slope": m.get("max_slope"), "air_control": 1.0}
    if m.get("radius") is not None:
        out["radius"] = float(m["radius"]) * CM_PER_STUD
    return out


def view_in_kit(v):
    """The view op's answer (studs, Roblox's axes) in the kit's frame."""
    v = v or {}
    for t in v.get("visible") or []:
        if t.get("center") is not None:
            t["center"] = kit_of(t["center"]).ue()
        if t.get("size") is not None:
            t["size"] = (kit_extent(t["size"]) * 2).ue()
        if t.get("distance") is not None:
            t["distance"] = float(t["distance"]) * CM_PER_STUD
        if (t.get("prompt") or {}).get("max_distance") is not None:
            t["prompt"]["max_distance"] = float(t["prompt"]["max_distance"]) * CM_PER_STUD
    body = v.get("body") or {}
    if body.get("velocity") is not None:
        body["velocity"] = kit_of(body["velocity"]).ue()
    for w in (body.get("sonar") or {}).get("ways") or []:
        w["clear"] = float(w.get("clear") or 0.0) * CM_PER_STUD
        if w.get("drop") is not None:
            w["drop"] = float(w["drop"]) * CM_PER_STUD
        if w.get("normal") is not None:
            w["normal"] = kit_dir(w["normal"]).ue()
    cam = v.get("camera") or {}
    if cam.get("location") is not None:
        cam["location"] = kit_of(cam["location"]).ue()
    if cam.get("look") is not None:
        cam["look"] = kit_dir(cam["look"]).ue()
    return v


def to_jpeg(data, max_dim=1280, quality=80):
    """(JPEG bytes, width, height) of a picture (PNG or JPEG, as screen_capture gives it), scaled to fit max_dim."""
    import av

    fmt = "mjpeg" if data[:3] == b"\xff\xd8\xff" else "png"
    dec = av.CodecContext.create(fmt, "r")
    frames = dec.decode(av.Packet(data))
    if not frames:
        raise OpFailed(503, "screen_capture's picture couldn't be read")
    frame = frames[0]
    scale = min(1.0, max_dim / max(frame.width, frame.height))
    w, h = max(2, int(frame.width * scale) // 2 * 2), max(2, int(frame.height * scale) // 2 * 2)
    enc = av.CodecContext.create("mjpeg", "w")
    enc.width, enc.height, enc.pix_fmt = w, h, "yuvj420p"
    from fractions import Fraction

    enc.time_base = Fraction(1, 25)
    enc.qscale = max(2, min(31, round(31 - quality * 0.29)))  # (80: about 8)
    out = frame.reformat(width=w, height=h, format="yuvj420p")
    out.pts = 0
    return b"".join(bytes(p) for p in enc.encode(out)) + b"".join(bytes(p) for p in enc.encode(None)), w, h


# -- the link in this process, and the connector -------------------------------------------------------------------

def hello(backend, project_name=None, share=None):
    """The hello the connector says (as a plugin's: docs/protocol.md)."""
    has = backend.studio.has
    h = {"type": "hello", "protocol": 1, "engine": "roblox-mcp", "plugin_version": "roblox-mcp-0.1",
         "project": project_name or backend.place_name(), "host": "editor",
         "features": ["levels", "pointer"] + (["media"] if has(mcp.SCREEN_CAPTURE) else []),
         "input": {"notes": INPUT_NOTES, "keys": has(mcp.USER_KEYBOARD_INPUT)},
         "studio": (backend.studio.client.server_info.get("serverInfo") or {})}
    if share is not None:
        h["share"] = bool(share)
    return h


class LocalLink:
    """bridge.Link's interface over a backend in this process (no WebSocket): Game(LocalLink(backend)) plays the
    place directly (tests, tools/roblox_latency.py)."""

    def __init__(self, backend, project_name=None):
        import queue as q

        self.backend = backend
        self.hello = hello(backend, project_name)
        self.closed = None
        self.game_time = None
        self.game_time_at = None
        self.last_heartbeat = time.time()
        self.findings, self.logs = [], []
        self.listeners, self.frame_listeners, self.rebound = [], [], []
        self.messages = q.Queue()
        self.loopback = True
        self.session = f"roblox-{uuid.uuid4().hex[:8]}"
        self.resumes = 0
        self.auth_token = self.mode = None
        backend.emit = self._event

    @property
    def async_errors(self):
        return self.backend.async_errors

    def _event(self, event):
        kind = event.get("kind")
        if kind == "finding":
            f = dict(event.get("finding") or {})
            f.setdefault("t", event.get("t"))
            self.findings.append(f)
        elif kind == "log":
            self.logs.append(event)
        for fn in list(self.listeners):
            try:
                fn(event)
            except Exception as e:
                print(f"roblox listener failed: {e}", file=sys.stderr)

    def request_at(self, op, args=None, timeout=None):
        from ..bridge import BridgeError

        if self.closed:
            import requests

            raise requests.ConnectionError(f"the game is disconnected ({self.closed})")
        try:
            if op in INPUT_OPS:  # (in order with the inputs posted before it, as the connector answers them)
                result, t = self._input(op, args or {}, timeout)
            else:
                result, t = self.backend.handle(op, args or {})
        except OpFailed as e:
            raise BridgeError(e.status, e.error) from None
        self.game_time, self.game_time_at = t, time.time()
        return result, t

    def _input(self, op, args, timeout=None):
        done, box = threading.Event(), {}
        self.backend.post(op, args, on_done=lambda result, error: (box.update(result=result, error=error),
                                                                     done.set()))
        if not done.wait(timeout or mcp.TIMEOUT + 15):
            raise mcp.McpTimeout(f"Studio didn't take {op} in time")
        if box.get("error") is not None:
            e = box["error"]
            raise e if isinstance(e, OpFailed) else OpFailed(500, f"{type(e).__name__}: {e}")
        return box.get("result") or ({}, self.backend.t)

    def request(self, op, args=None, timeout=None):
        return self.request_at(op, args, timeout)[0]

    def post(self, op, args=None):
        self.backend.post(op, args or {})

    def send(self, frame):
        pass

    def status(self, text):
        pass

    def missing_ops(self, probes=None):
        from .. import bridge

        missing = []
        for op, args in (probes or bridge.required_ops(self.hello)).items():
            try:
                self.request(op, args)
            except bridge.BridgeError as e:
                if str(e.error).lower().startswith("unknown op"):
                    missing.append(op)
        return missing

    def next_message(self, timeout=None):
        try:
            return self.messages.get(timeout=timeout)
        except Exception:
            return None

    def close(self, why="closed"):
        if not self.closed:
            self.closed = why
            self.backend.close()


def serve_connection(ws, backend, project_name=None, share=None, log=print):
    """Be the plugin on a WebSocket to the brain: say hello, then answer its requests (each in a thread of its own,
    so a slow one doesn't hold up the rest; the backend coalesces the reads), send its events, poll the console while
    the game plays. Returns why the connection ended."""
    send_lock = threading.Lock()
    events = queue.Queue()

    def send(frame):
        with send_lock:
            ws.send(json.dumps(frame, default=str))

    backend.emit = events.put
    send(hello(backend, project_name, share))
    first = json.loads(ws.recv(timeout=30))
    if first.get("type") != "welcome":
        return f"the brain didn't welcome the connector: {first}"
    log(f"Connected to the brain (session {first.get('session')})")
    done = threading.Event()

    def reply(rid, result, error):
        if error is None:
            frame = {"type": "response", "id": rid, "ok": True, "result": result[0], "t": result[1]}
        elif isinstance(error, OpFailed):
            frame = {"type": "response", "id": rid, "ok": False, "status": error.status, "error": error.error,
                     "t": backend.t}
        else:
            frame = {"type": "response", "id": rid, "ok": False, "status": 500,
                     "error": f"{type(error).__name__}: {error}"}
        try:
            send(frame)
        except Exception:
            done.set()

    def answer(frame):
        rid = frame.get("id")
        try:
            result, error = backend.handle(frame.get("op"), frame.get("args") or {}), None
        except Exception as e:
            result, error = None, e
        reply(rid, result, error)

    def pump():
        """Events (log lines, findings, sessions), a heartbeat every 2 s, the console while the game plays."""
        last_beat = last_console = 0.0
        while not done.is_set():
            now = time.monotonic()
            batch = []
            try:
                batch.append(events.get(timeout=0.2))
                while True:
                    batch.append(events.get_nowait())
            except queue.Empty:
                pass
            if now - last_beat >= 2.0:
                batch.append({"kind": "heartbeat", "t": backend.t})
                last_beat = now
            if batch:
                try:
                    send({"type": "events", "events": batch, "t": backend.t})
                except Exception:
                    done.set()
                    return
            if backend.running and now - last_console >= CONSOLE_EVERY:
                last_console = now
                try:
                    backend.poll_console()
                except Exception as e:
                    log(f"note: couldn't read Studio's console: {e}")

    threading.Thread(target=pump, name="roblox-events", daemon=True).start()
    why = "the brain closed the connection"
    try:
        for message in ws:
            try:
                frame = json.loads(message)
            except ValueError:
                continue
            if frame.get("type") == "request" and frame.get("op") in INPUT_OPS:
                # (in order, on the backend's input thread: a renewal of a held key is answered at once)
                backend.post(frame["op"], frame.get("args") or {},
                             on_done=lambda result, error, rid=frame.get("id"): reply(
                                 rid, (result[0], result[1]) if result else ({}, backend.t), error))
            elif frame.get("type") == "request":
                threading.Thread(target=answer, args=(frame,), daemon=True).start()
            elif frame.get("type") == "result":
                log(f"Result: {json.dumps(frame.get('result'))[:400]}")
    except Exception as e:
        why = str(e) or "connection lost"
    done.set()
    return why


def default_url():
    """The brain's address from brain.json (a running `autopilot serve` says it there), else the default port's."""
    from .. import bridge

    try:
        with open(bridge.brain_file(), encoding="utf-8") as f:
            return json.load(f).get("url") or "ws://127.0.0.1:8765/v1/bridge"
    except (OSError, ValueError, AttributeError):
        return "ws://127.0.0.1:8765/v1/bridge"


def main(argv=None):
    """python -m autopilot.engines.roblox_connector connect [--url ws://...] [--project PATH] [--no-share] [--log FILE]
    [--console FILE]"""
    import argparse

    ap = argparse.ArgumentParser(prog="python -m autopilot.engines.roblox_connector",
                                 description="Play the place open in Roblox Studio for Autopilot, through Studio's "
                                             "built-in MCP server (stands in for a plugin).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("connect", help="connect to the brain and serve it the place open in Studio")
    c.add_argument("--url", help="the brain's bridge (default: brain.json's, else ws://127.0.0.1:8765/v1/bridge)")
    c.add_argument("--project", help="the place on this machine: a Rojo project folder or a .rbxl/.rbxlx file")
    c.add_argument("--no-share", action="store_true", help="say in the hello that runs' data isn't shared")
    c.add_argument("--log", help="also write what the connector says to this file")
    c.add_argument("--retry", type=float, default=30.0, help="seconds to keep trying to reach the brain")
    c.add_argument("--console", help="also append every line of Studio's console (its Output) to this file")
    args = ap.parse_args(argv)
    from websockets.sync.client import connect

    out = open(args.log, "a", encoding="utf-8") if args.log else None

    def log(text):
        print(text, flush=True)
        if out:
            out.write(text + "\n")
            out.flush()

    project = RobloxMcp().project_at(args.project) if args.project else None
    if args.project and not project:
        log(f"note: no Rojo project or place file at {args.project}: playing what Studio has open")
    try:
        studio = mcp.Studio.connect()
    except mcp.McpError as e:
        log(str(e))
        return 2
    lacking = studio.check()
    for tool, why in lacking.items():
        log(f"note: Studio's MCP server has no {tool}: {why}")
    backend = StudioBackend(studio, project, console_file=args.console)
    name = RobloxMcp().project_name(project) if project else None
    url = args.url or default_url()
    deadline = time.monotonic() + args.retry
    while True:
        try:
            with connect(url, max_size=None, open_timeout=10) as ws:
                why = serve_connection(ws, backend, name, share=False if args.no_share else None, log=log)
            log(f"Disconnected: {why}")
            break
        except OSError as e:
            if time.monotonic() > deadline:
                log(f"couldn't reach the brain at {url}: {e}")
                backend.close()
                return 1
            time.sleep(1.0)
    backend.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
