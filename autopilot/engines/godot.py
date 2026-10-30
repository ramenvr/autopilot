"""Godot 4: the Autopilot Playtest addon (plugin/godot: addons/autopilot in a project). The addon answers the
engine-neutral ops (find, transforms, pose, movement) in the kit frame, and `rc` in Godot's own terms: node paths
(/root/Main/Player), properties with Godot's `a:b` sub-paths (position:y), metres on Godot's axes.

Godot's frame: x right, y up, -z forward, metres, right-handed; rotation in radians, rotation.y turning left (from -z
toward -x), rotation.x tilting the view up. The kit's: X forward, Y right, Z up, centimetres:
    kit (X, Y, Z) = 100 * (-z, x, y);  kit yaw = -deg(rotation.y);  kit pitch = deg(rotation.x)
2D games: (x right, y down) pixels -> kit (x, 0, -y), 1 pixel = 1 unit.

The codex is the project's own text files (scripts, scenes, resources: Godot keeps them as text) and the addon's
summary of the project (.godot/autopilot_codex/summary.txt: main scene, autoloads, input actions, classes, scene trees).
"""

import math
import os
import re
import shutil
import sys

from ..kit.geom import Vec, wrap
from .base import CODEX_OWN, OpEngine, safe_rel

# The Godot executable (on Windows, the _console one): AUTOPILOT_GODOT, else `godot` on PATH
EXE = os.environ.get("AUTOPILOT_GODOT") or shutil.which("godot") or "godot"
CODE_EXT = (".gd", ".cs", ".tscn", ".tres", ".gdshader")
TOP_FILES = ("project.godot", "export_presets.cfg")
# The addon's summary of the project (main scene, autoloads, input actions, global classes, each scene's nodes)
EXPORT_PREFIX = ".godot/autopilot_codex/"


class Godot(OpEngine):
    name = "godot"
    title = "Godot"
    export_dir = EXPORT_PREFIX.rstrip("/")
    error_log = False  # the addon sends no `log` events
    # the codex export, the run history, the Settings page's token counts; and in the project's user:// folder, each
    # run's report files and frames, and the codex export when res:// couldn't be written (user_folder)
    plugin_files = (export_dir, ".godot/editor/autopilot_runs", ".godot/editor/autopilot_usage.json")
    user_files = ("playtest", "autopilot_codex")
    default_exe = EXE

    # -- the project on disk -----------------------------------------------------------------------------

    def project_at(self, path):
        """The project folder: the one with project.godot (the folder itself, or the folder of that file)."""
        if not path:
            return None
        path = os.path.abspath(path)
        if os.path.isfile(path):
            return os.path.dirname(path) if os.path.basename(path) == "project.godot" else None
        return path if os.path.isfile(os.path.join(path, "project.godot")) else None

    def project_name(self, project):
        """config/name in project.godot (what the addon's hello says), else the folder's name."""
        try:
            with open(os.path.join(self.project_folder(project), "project.godot"), encoding="utf-8") as f:
                m = re.search(r'^config/name\s*=\s*"((?:[^"\\]|\\.)*)"', f.read(), re.M)
            if m and m.group(1).strip():
                return m.group(1)
        except OSError:
            pass
        return super().project_name(project)

    def user_folder(self, project):
        """The project's user:// folder, as the editor has it: app_userdata/<config/name> in Godot's data folder."""
        home = os.path.expanduser("~")
        if os.name == "nt":
            base = os.path.join(os.environ.get("APPDATA") or os.path.join(home, "AppData", "Roaming"), "Godot")
        elif sys.platform == "darwin":
            base = os.path.join(home, "Library", "Application Support", "Godot")
        else:
            base = os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.join(home, ".local", "share"), "godot")
        return os.path.join(base, "app_userdata", self.project_name(project))

    def plugin_data(self, project):
        user = self.user_folder(project)
        return super().plugin_data(project) + [os.path.join(user, name) for name in self.user_files]

    # -- the game backend --------------------------------------------------------------------------------

    def all_actors(self, game):
        """The scene's things, as Unreal's actors: its direct children, physics bodies and areas, and the roots of the
        scenes instanced in it (not every mesh and collision shape under them)."""
        return list((game.request("find", {"things": True}) or {}).get("objects") or [])

    # -- the codex ---------------------------------------------------------------------------------------

    def codex_path(self, rel):
        """Kept at their project paths: project.godot, export_presets.cfg, and **/*.gd, .cs, .tscn, .tres, .gdshader
        outside folders whose name starts with "." (.godot, .import...), node_modules and the addon itself; the
        addon's summary (.godot/autopilot_codex/) at the codex's top."""
        parts = safe_rel(rel)
        if parts is None:
            return None
        if rel.startswith(EXPORT_PREFIX) and len(parts) > 2:
            dest = rel[len(EXPORT_PREFIX):]
            return dest if dest not in CODEX_OWN else None
        if (any(p.startswith(".") or p == "node_modules" for p in parts[:-1])
                or rel.startswith("addons/autopilot/")):
            return None
        if len(parts) == 1 and rel in TOP_FILES:
            return rel
        if rel.lower().endswith(CODE_EXT):
            return rel
        return None

    def skip_dir(self, rel):
        if rel == ".godot" or rel.startswith(EXPORT_PREFIX.rstrip("/")):
            return False  # (the way down to the summary)
        name = rel.split("/")[-1]
        return name.startswith(".") or name == "node_modules" or rel == "addons/autopilot" or (
            rel.startswith(".godot/"))

    def has_export(self, codex_root):
        return os.path.exists(os.path.join(codex_root, "summary.txt"))

    no_export_note = "note: no summary of the project in the codex (the Godot addon didn't make one)"

    def index_notes(self, codex_root):
        """Where to start in a Godot project: its main scene, autoloads and input actions (from project.godot)."""
        settings = read_project_godot(os.path.join(codex_root, "project.godot"))
        if not settings:
            return []
        lines = ["", "## Where to start (from project.godot)", ""]
        main = settings.get("application", {}).get("run/main_scene")
        if main:
            lines.append(f"- Main scene (the game starts there): {main} (in the codex as {res_to_codex(main)})")
        autoloads = settings.get("autoload", {})
        game_autoloads = {k: v for k, v in autoloads.items() if k != "AutopilotPlaytest"}
        if game_autoloads:
            lines.append("- Autoloads (global nodes at /root/<Name>, alive across scene changes and restarts in a "
                         "game, reset only when the game process starts again):")
            for k, v in game_autoloads.items():
                lines.append(f"  - /root/{k} = {v.lstrip('*')}")
        actions = settings.get("input", {})
        if actions:
            lines.append("- Input actions (the InputMap: press them by name with game.press / game.hold): "
                         + ", ".join(f"{a} ({', '.join(keys) or '?'})" for a, keys in actions.items()))
        lines.append("- Scripts are .gd files; scenes (.tscn) and resources (.tres) are text: a node's script is its "
                     "`script = ExtResource(...)` line, its exported variables follow it.")
        return lines

    # -- the kit frame -----------------------------------------------------------------------------------

    def to_kit(self, v):
        n = Vec.of(v)
        return None if n is None else Vec(-100.0 * n.z, 100.0 * n.x, 100.0 * n.y)

    def to_native(self, v):
        k = Vec.of(v)
        return {"x": k.y / 100.0, "y": k.z / 100.0, "z": -k.x / 100.0}

    def rotation_to_kit(self, r):
        """rotation {"x","y","z"} (radians) as (yaw, pitch) in degrees."""
        r = r or {}
        return wrap(-math.degrees(float(r.get("y", 0.0)))), wrap(math.degrees(float(r.get("x", 0.0))))

    def rotation_to_native(self, yaw, pitch=0.0):
        return {"x": math.radians(pitch), "y": -math.radians(wrap(yaw)), "z": 0.0}

    # -- a headless game ---------------------------------------------------------------------------------

    def launch_command(self, project, url, log, map=None, exe=None):
        """Godot without a window or rendering (--headless) on the project's folder. The addon reads the arguments
        after `--` (Unreal's spellings, or its own --playtest-url=...); -PlaytestMap: the scene to start in (a res://
        path or a scene file's name)."""
        return [exe or EXE, "--headless", "--path", self.project_folder(project), "--log-file", log, "--",
                "-Playtest=goal", "-PlaytestGoal=autopilot", f"-PlaytestUrl={url}"] + (
                    [f"-PlaytestMap={map}"] if map else [])


# -- project.godot -----------------------------------------------------------------------------------------

def read_project_godot(path):
    """{section: {key: value}} of a project.godot, enough for the index: strings unquoted; the [input] section as
    {action: [key names]} (from each event's physical_keycode / keycode / button_index)."""
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return {}
    out, section, key, buf = {}, "", None, []
    for line in text.splitlines():
        if key is not None:  # a value spanning lines (an input action's events)
            buf.append(line)
            if line.strip() == "}":
                out.setdefault(section, {})[key] = "\n".join(buf)
                key, buf = None, []
            continue
        s = line.strip()
        if not s or s.startswith(";"):
            continue
        m = re.match(r"^\[(\w+)\]$", s)
        if m:
            section = m.group(1)
            continue
        m = re.match(r"^([^=]+?)\s*=\s*(.*)$", line)
        if not m:
            continue
        k, v = m.group(1).strip(), m.group(2).strip()
        if v == "{" or (v.startswith("{") and not v.endswith("}")):
            key, buf = k, [v]
            continue
        out.setdefault(section, {})[k] = v[1:-1] if len(v) >= 2 and v[0] == v[-1] == '"' else v
    if "input" in out:
        out["input"] = {a: input_keys(v) for a, v in out["input"].items()}
    return out


def input_keys(value):
    """The keys of an [input] action's events, as Unreal key names where there is one (W, SpaceBar, LeftMouseButton),
    else Godot's keycode number."""
    keys = []
    for ev in re.finditer(r"Object\((InputEvent\w+),(.*?)\)\s*(?=,\s*Object|\]|$)", value, re.S):
        kind, body = ev.group(1), ev.group(2)
        fields = dict(re.findall(r'"(\w+)":\s*([^,]+)', body))
        name = None
        if kind == "InputEventKey":
            code = int(fields.get("physical_keycode", "0") or 0) or int(fields.get("keycode", "0") or 0)
            name = KEY_NAMES.get(code) or (chr(code) if 48 <= code <= 90 else str(code))
        elif kind == "InputEventMouseButton":
            name = MOUSE_NAMES.get(int(fields.get("button_index", "0") or 0))
        elif kind == "InputEventJoypadButton":
            name = f"joypad button {fields.get('button_index', '?')}"
        elif kind == "InputEventJoypadMotion":
            name = f"joypad axis {fields.get('axis', '?')}"
        if name and name not in keys:
            keys.append(name)
    return keys


DIGITS = ("Zero", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine")
KEY_NAMES = {32: "SpaceBar", 4194305: "Escape", 4194306: "Tab", 4194308: "BackSpace", 4194309: "Enter",
             4194310: "Enter", 4194311: "Insert", 4194312: "Delete", 4194317: "Home", 4194318: "End",
             4194319: "Left", 4194320: "Up", 4194321: "Right", 4194322: "Down", 4194323: "PageUp", 4194324: "PageDown",
             4194325: "LeftShift", 4194326: "LeftControl", 4194328: "LeftAlt", 4194329: "CapsLock",
             **{48 + i: d for i, d in enumerate(DIGITS)}, **{4194332 + i: f"F{i + 1}" for i in range(12)}}
MOUSE_NAMES = {1: "LeftMouseButton", 2: "RightMouseButton", 3: "MiddleMouseButton", 4: "MouseScrollUp",
               5: "MouseScrollDown", 8: "ThumbMouseButton", 9: "ThumbMouseButton2"}


def res_to_codex(res_path):
    """res://scenes/level.tscn -> scenes/level.tscn (where the codex keeps it)."""
    return res_path[len("res://"):] if str(res_path).startswith("res://") else res_path
