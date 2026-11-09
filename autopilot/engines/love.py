"""LÖVE 11 (Love2D): the Autopilot module (plugin/love: an `autopilot/` folder in the game, `require("autopilot")` at
the top of main.lua). The module answers the engine-neutral ops (find, transforms, pose, movement) in the kit frame,
and `rc` in Lua's own terms: paths through the game's global tables (/player, /enemies/3), fields as they are.

LÖVE has no scene tree: the module's "things" are the game's tables that have a place (numeric x and y), found
through the globals, the bodies of love.physics worlds, and whatever the game names itself (autopilot.track,
autopilot.player). Paths are "/"-separated keys from the global table: /player, /world/entities/12.

LÖVE's frame is the screen's: x right, y down, pixels; a rotation in radians, clockwise on screen. The kit's: X
forward, Y right, Z up, centimetres. As Godot's 2D games:
    kit (X, Y, Z) = (x, 0, -y), 1 pixel = 1 unit;  kit pitch = -deg(rotation), yaw = 0

The codex is the game's own Lua files (and its shaders), as they are in its folder.
"""

import math
import os
import re
import shutil
import sys

from ..kit.geom import Vec, wrap
from .base import OpEngine, safe_rel

# The LÖVE executable: AUTOPILOT_LOVE, else `love` on PATH
EXE = os.environ.get("AUTOPILOT_LOVE") or shutil.which("love") or "love"
CODE_EXT = (".lua", ".glsl", ".frag", ".vert")
# The module's own folder in a game (never in the codex)
PLUGIN_DIR = "autopilot"
# LÖVE's callbacks, as a game defines them in main.lua (love.update, love.keypressed...)
CALLBACKS = ("load", "update", "draw", "keypressed", "keyreleased", "textinput", "mousepressed", "mousereleased",
             "mousemoved", "wheelmoved", "gamepadpressed", "gamepadreleased", "joystickpressed", "focus", "resize",
             "quit", "run")
# Libraries LÖVE games often carry, by their file or folder name: what each tells the agent to look for
LIBRARIES = {
    "hump": "hump (gamestate: the game's screens are states; camera: hump.camera, x/y its middle; timer; class)",
    "bump": "bump.lua (collisions: world:move / world:check; the world's items are the game's things)",
    "middleclass": "middleclass (classes: an object's class is obj.class.name)",
    "classic": "classic (classes: Object:extend())",
    "sti": "Simple Tiled Implementation (Tiled maps: map.layers, map:update, map:draw)",
    "anim8": "anim8 (sprite animations: an animation's position and status)",
    "lume": "lume (helpers)",
    "knife": "knife (helpers)",
    "baton": "baton (input: actions bound to keys; input:down('action'))",
    "tiny": "tiny-ecs (entities in a world: world.entities)",
    "concord": "Concord (ECS: entities, components, systems)",
    "windfield": "windfield (love.physics wrapper: colliders)",
    "gamera": "gamera (camera: cam:getPosition is its middle)",
    "flux": "flux (tweens)",
    "suit": "SUIT (immediate-mode UI: buttons are drawn each frame)",
}


class Love(OpEngine):
    name = "love"
    title = "LÖVE"
    default_exe = EXE

    # -- the project on disk -----------------------------------------------------------------------------

    def project_at(self, path):
        """The project folder: the one with main.lua (the folder itself, or the folder of that file)."""
        if not path:
            return None
        path = os.path.abspath(path)
        if os.path.isfile(path):
            return os.path.dirname(path) if os.path.basename(path) == "main.lua" else None
        return path if os.path.isfile(os.path.join(path, "main.lua")) else None

    def project_name(self, project):
        """What the module's hello says: conf.lua's window title (unless LÖVE's "Untitled"), else its identity, else
        the folder's name."""
        conf = read_conf(os.path.join(self.project_folder(project), "conf.lua"))
        for key in ("window.title", "identity"):
            value = conf.get(key)
            if isinstance(value, str) and value.strip() and value != "Untitled":
                return value
        return super().project_name(project)

    # -- the codex ---------------------------------------------------------------------------------------

    def codex_path(self, rel):
        """Kept at their project paths: **/*.lua and shaders (.glsl, .frag, .vert), outside folders whose name starts
        with "." (.git...), node_modules and the module itself (autopilot/)."""
        parts = safe_rel(rel)
        if parts is None:
            return None
        if any(p.startswith(".") or p == "node_modules" for p in parts[:-1]) or parts[0] == PLUGIN_DIR:
            return None
        return rel if rel.lower().endswith(CODE_EXT) else None

    def skip_dir(self, rel):
        name = rel.split("/")[-1]
        return name.startswith(".") or name == "node_modules" or rel == PLUGIN_DIR

    def index_notes(self, codex_root):
        """Where to start in a LÖVE game: main.lua's callbacks, conf.lua's settings, the libraries it carries and the
        keys its code reads."""
        lines = ["", "## Where to start", ""]
        main = os.path.join(codex_root, "main.lua")
        if os.path.isfile(main):
            defined = callbacks_of(_read(main))
            lines.append("- main.lua runs first; LÖVE calls its callbacks every frame: "
                         + (", ".join(f"love.{c}" for c in defined) if defined else "(none defined in main.lua)"))
        conf = read_conf(os.path.join(codex_root, "conf.lua"))
        if conf:
            shown = ", ".join(f"{k} = {v}" for k, v in sorted(conf.items()))
            lines.append(f"- conf.lua: {shown}")
        libs = libraries_in(codex_root)
        if libs:
            lines.append("- Libraries: " + "; ".join(libs))
        keys = keys_read(codex_root)
        if keys:
            lines.append("- Keys the code reads (love.keyboard.isDown, or a key compared in a key callback): "
                         + ", ".join(keys))
        lines.append("- No scene tree: the game's things are its Lua tables, reached by their paths from the globals "
                     "(/player, /enemies/3).")
        return lines

    # -- the kit frame -----------------------------------------------------------------------------------

    def to_kit(self, v):
        """{"x", "y"} (or a sequence) in pixels, y down, as the kit's (x, 0, -y)."""
        if v is None:
            return None
        if isinstance(v, dict):
            x, y = v.get("x", v.get("X")), v.get("y", v.get("Y"))
        else:
            x, y = (list(v) + [0, 0])[:2]
        if x is None or y is None:
            return None
        return Vec(float(x), 0.0, -float(y))

    def to_native(self, v):
        k = Vec.of(v)
        return {"x": k.x, "y": -k.z}

    def rotation_to_kit(self, r):
        """A rotation in radians (a number, or a table's r / angle / rotation) as (yaw, pitch) in degrees: clockwise
        on screen is a kit pitch down."""
        if isinstance(r, dict):
            r = r.get("r", r.get("angle", r.get("rotation", 0.0)))
        return 0.0, wrap(-math.degrees(float(r or 0.0)))

    def rotation_to_native(self, yaw, pitch=0.0):
        return -math.radians(wrap(pitch))

    # -- a headless game ---------------------------------------------------------------------------------

    def launch_command(self, project, url, log, map=None, exe=None):
        """LÖVE on the project's folder (or a fused build of the game: exe, which needs no folder), the module's
        arguments after it. LÖVE has no headless mode: on Linux without a display it runs under xvfb-run when there
        is one. `map` has no meaning in LÖVE (no scenes) and is passed on for a game that reads it itself."""
        where = [] if exe and is_fused(exe) else [self.project_folder(project)]
        cmd = [exe or EXE] + where + ["--playtest=goal", "--playtest-goal=autopilot", f"--playtest-url={url}",
                                      f"--playtest-log={log}"] + ([f"--playtest-map={map}"] if map else [])
        if sys.platform.startswith("linux") and not os.environ.get("DISPLAY") and shutil.which("xvfb-run"):
            cmd = ["xvfb-run", "-a"] + cmd
        return cmd


def is_fused(exe):
    """Whether an executable is a fused game (the game's own name), not LÖVE itself that runs a folder."""
    return not os.path.basename(exe).lower().startswith("love")


def _read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def read_conf(path):
    """The settings a conf.lua sets with plain values (t.identity = "x", t.window.width = 800, t.console = true), as
    {"identity": "x", "window.width": 800, ...}; strings unquoted. {} when there is none."""
    out = {}
    for m in re.finditer(r"^\s*t\.([\w.]+)\s*=\s*(\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*'|-?\d+(?:\.\d+)?|true|false)",
                         _read(path), re.M):
        key, value = m.group(1), m.group(2)
        if value[0] in "\"'":
            out[key] = value[1:-1]
        elif value in ("true", "false"):
            out[key] = value == "true"
        else:
            out[key] = float(value) if "." in value else int(value)
    return out


def callbacks_of(source):
    """The love.* callbacks a file defines (function love.update(dt) ... / love.update = function), in LÖVE's
    order."""
    found = set(re.findall(r"\bfunction\s+love\.(\w+)\s*\(", source))
    found |= set(re.findall(r"\blove\.(\w+)\s*=\s*function\b", source))
    return [c for c in CALLBACKS if c in found]


def libraries_in(codex_root):
    """The libraries of LIBRARIES the game carries (a file or folder of that name), as their notes."""
    names = set()
    for base, dirs, files in os.walk(codex_root):
        for n in dirs + [os.path.splitext(f)[0] for f in files]:
            names.add(n.lower())
    return [note for lib, note in LIBRARIES.items() if lib in names]


def keys_read(codex_root):
    """The keys the game's code reads: love.keyboard.isDown("a", ...) and key == "a" (in its key callbacks), sorted."""
    keys = set()
    for base, _, files in os.walk(codex_root):
        for f in files:
            if not f.endswith(".lua"):
                continue
            src = _read(os.path.join(base, f))
            for args in re.findall(r"isDown\s*\(([^)]*)\)", src):
                keys |= set(re.findall(r"[\"']([\w ]+)[\"']", args))
            keys |= set(re.findall(r"\b(?:key|k|scancode)\s*==\s*[\"']([\w ]+)[\"']", src))
    return sorted(keys)
