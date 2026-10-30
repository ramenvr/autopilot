"""Unity 6: the Autopilot Playtest Unity package (plugin/unity/com.ramenvr.autopilot). Its plugin answers the
engine-neutral ops (find, transforms, pose, movement) in the kit frame, and `rc` in Unity's own terms: GameObject and
component paths (`Main:/Level/Coins/Coin@2`, `Main:/Player|PlayerMover`, `type:UnityEngine.Physics`), metres on Unity's
axes.

Unity's frame: x right, y up, z forward, metres, left-handed; Transform.eulerAngles in degrees, y turning right
(from +z toward +x), x tilting the view down. The kit's: X forward, Y right, Z up, centimetres:
    kit (X, Y, Z) = 100 * (z, x, y);  kit yaw = euler y;  kit pitch = -euler x

The codex: the project's C# scripts, input actions and assembly definitions (Assets/ and embedded packages), its
settings (ProjectSettings/*.asset, Packages/manifest.json), and the plugin's text export of its scenes, prefabs, data
and input (Library/AutopilotCodex/: summary.txt, build_scenes.txt, scripts.txt, scenes/, prefabs/, data/, input/), which
goes to the codex's top.

A headless game: a Development player build run `-batchmode -nographics` (bench's --engine <Game.exe>), or the
editor in batch mode running the plugin's Headless.Run (it opens the scene, enters play mode and connects; never with
-quit). Both read the -Playtest* arguments as Unreal's plugin does.
"""

import os
import re

from ..kit.geom import Vec, wrap
from .base import CODEX_OWN, OpEngine, safe_rel

# The Unity editor: AUTOPILOT_UNITY, else Unity Hub's default folder for the version the package is tested with
EDITOR = os.environ.get("AUTOPILOT_UNITY", r"C:\Program Files\Unity\Hub\Editor\6000.6.5f1\Editor\Unity.exe")
# The plugin's own text export of the project (Editor/CodexExport.cs)
EXPORT_PREFIX = "Library/AutopilotCodex/"
OWN_PACKAGE = "com.ramenvr.autopilot"
CODE_EXT = (".cs", ".inputactions", ".asmdef")
SKIP = ("Library", "Temp", "Logs", "obj", "Build", "Builds", "UserSettings", "MemoryCaptures", ".git", ".vs",
        "node_modules")


def _unescaped_split(text, sep):
    """Split on sep where it isn't escaped with a backslash (the plugin escapes \\ / | @ : in names)."""
    parts, cur, i = [], "", 0
    while i < len(text):
        c = text[i]
        if c == "\\" and i + 1 < len(text):
            cur += text[i:i + 2]
            i += 2
            continue
        if c == sep:
            parts.append(cur)
            cur = ""
        else:
            cur += c
        i += 1
    parts.append(cur)
    return parts


def built_from(exe):
    """The project folder a player build was built from, as the plugin baked it into the build's first scene
    (PlaytestBuildInfo.projectDir in <Game>_Data/level0), when that folder is a Unity project on this machine."""
    level0 = os.path.join(os.path.splitext(exe)[0] + "_Data", "level0")
    try:
        with open(level0, "rb") as f:
            data = f.read()
    except OSError:
        return None
    # An absolute path: C:\... or C:/... (built on Windows), /... (built on Linux or macOS). A POSIX path may follow
    # other printable bytes, so each "/" in a run of them is tried as its start
    for m in re.finditer(rb"[A-Za-z]:[\\/][^\x00-\x1f\"<>|?*]{2,259}|/[^\x00-\x1f\"<>|?*]{2,259}", data):
        text = m.group().decode("utf-8", "replace")
        starts = [0] if text[0] != "/" else [i for i, c in enumerate(text) if c == "/"]
        for i in starts:
            folder = text[i:]
            if os.path.isfile(os.path.join(folder, "ProjectSettings", "ProjectVersion.txt")):
                return os.path.normpath(folder)
    return None


def _skipped(name):
    """A folder the plugin and Unity skip: its own (Library, Temp...), hidden (.x) or ignored by Unity (x~)."""
    return name in SKIP or name.startswith(".") or name.endswith("~")


class Unity(OpEngine):
    name = "unity"
    title = "Unity"
    export_dir = EXPORT_PREFIX.rstrip("/")
    # each run's report files and recording, the codex export and its log, the Settings page's token counts, the run
    # history
    plugin_files = (
        "Logs/Playtest", export_dir, "Logs/AutopilotCodexExport.log", "UserSettings/AutopilotUsage.json",
        "UserSettings/AutopilotRuns",
    )
    default_exe = EDITOR
    HEADLESS_METHOD = "Autopilot.Playtest.Editor.Headless.Run"

    # -- the project on disk -----------------------------------------------------------------------------

    def __init__(self):
        self.players = {}  # project folder -> the player build given for it (a .exe as the project: see project_at)

    def project_at(self, path):
        """The project folder: one with ProjectSettings/ProjectVersion.txt (the folder itself, or the folder of that
        file, or the project of a path inside its Assets/). A player build (Game.exe, a Development build with the
        plugin) stands for the project it was built from (the folder the plugin baked into its first scene), and is
        then what launch_command starts for that project."""
        if not path:
            return None
        path = os.path.abspath(path)
        if os.path.isfile(path) and path.lower().endswith(".exe"):
            project = built_from(path)
            if project:
                self.players[os.path.normcase(project)] = path
            return project
        if os.path.isfile(path):
            path = os.path.dirname(path)
        for folder in (path, os.path.dirname(path), os.path.dirname(os.path.dirname(path))):
            if os.path.isfile(os.path.join(folder, "ProjectSettings", "ProjectVersion.txt")):
                return folder
        return None

    def project_name(self, project):
        """The product name in Player Settings (what the plugin's hello says), else the folder's name."""
        try:
            with open(os.path.join(self.project_folder(project), "ProjectSettings", "ProjectSettings.asset"),
                      encoding="utf-8", errors="replace") as f:
                m = re.search(r"^\s*productName:\s*(.+?)\s*$", f.read(), re.M)
            if m and m.group(1).strip():
                return m.group(1).strip().strip("'\"")
        except OSError:
            pass
        return super().project_name(project)

    # -- the game backend --------------------------------------------------------------------------------

    def name_of(self, path):
        """A GameObject's name from the plugin's path, as `view` names things ("Main:/Level/Coins/Coin@2" ->
        "Coin@2"; a component's, "Main:/Player|PlayerMover", is its GameObject's: "Player"). Escapes are kept, as the
        plugin writes names."""
        path = str(path)
        obj = _unescaped_split(path, "|")[0].rstrip("/")
        return _unescaped_split(obj, "/")[-1]

    def all_actors(self, game):
        """The scene's things, as Unreal's actors: objects that aren't plain groups (nothing but a Transform), at the
        top or under such a group (not every part under them)."""
        return list((game.request("find", {"things": True}) or {}).get("objects") or [])

    # -- the codex ---------------------------------------------------------------------------------------

    def codex_path(self, rel):
        """Kept at their project paths: Assets/**/*.cs, *.inputactions, *.asmdef, the same in embedded packages
        (Packages/<name>/, not the plugin's own), ProjectSettings/*.asset (and ProjectVersion.txt),
        Packages/manifest.json; the plugin's export (Library/AutopilotCodex/) at the codex's top."""
        parts = safe_rel(rel)
        if parts is None:
            return None
        if rel.startswith(EXPORT_PREFIX) and len(parts) > 2:
            dest = rel[len(EXPORT_PREFIX):]
            return dest if dest not in CODEX_OWN else None
        low = rel.lower()
        if any(_skipped(p) for p in parts[:-1]):
            return None
        if parts[0] == "Assets" and len(parts) > 1 and low.endswith(CODE_EXT):
            return rel
        if (parts[0] == "Packages" and len(parts) > 2 and parts[1] != OWN_PACKAGE
                and low.endswith(CODE_EXT)):
            return rel
        if (parts[0] == "ProjectSettings" and len(parts) == 2
                and (low.endswith(".asset") or parts[1] == "ProjectVersion.txt")):
            return rel
        if rel == "Packages/manifest.json":
            return rel
        return None

    def skip_dir(self, rel):
        if rel == EXPORT_PREFIX.rstrip("/") or rel.startswith(EXPORT_PREFIX) or EXPORT_PREFIX.startswith(rel + "/"):
            return False  # (the way down to the export, inside Library/)
        parts = rel.split("/")
        return _skipped(parts[0]) or _skipped(parts[-1]) or rel == f"Packages/{OWN_PACKAGE}"

    def has_export(self, codex_root):
        return os.path.exists(os.path.join(codex_root, "summary.txt"))

    no_export_note = ("note: no scene/asset export in the codex (the Unity plugin makes it in the editor before play "
                      "mode, or a player runs the editor headless on its project: neither happened)")

    def index_notes(self, codex_root):
        """Where to start in a Unity project: the export's summary and scenes, the input actions, the scripts."""
        lines = ["", "## Where to start", ""]
        if os.path.exists(os.path.join(codex_root, "summary.txt")):
            lines.append("- summary.txt: the project at a glance (input handling, scenes, counts); build_scenes.txt: "
                         "the scenes in the build, in order (index 0 is where a player build starts); scripts.txt: "
                         "each script's class, base class and file.")
            lines.append("- scenes/<path>.txt: each scene's objects by the bridge's paths (what game.actors / "
                         "game.loc take), their components and serialized values in Unity's units (metres, x right, "
                         "y up, z forward), references by path, and UnityEvent listeners (what a button calls); "
                         "prefabs/ and data/ (ScriptableObjects) the same way.")
            lines.append("- input/: the Input System action maps (actions, bindings, composites) and the legacy "
                         "Input Manager's axes.")
        else:
            lines.append("- (no export: scenes, prefabs and input actions are only in their Unity files, not here)")
        lines.append("- Assets/**/*.cs: the game's C# scripts (MonoBehaviours: fields are what game.get reads; "
                     "Update/OnTriggerEnter/... are where the rules are). *.inputactions: the Input System actions "
                     "(JSON). ProjectSettings/ProjectSettings.asset: activeInputHandler 0 = legacy Input Manager only, "
                     "1 = Input System, 2 = both.")
        return lines

    # -- the kit frame -----------------------------------------------------------------------------------

    def to_kit(self, v):
        n = Vec.of(v)
        return None if n is None else Vec(100.0 * n.z, 100.0 * n.x, 100.0 * n.y)

    def to_native(self, v):
        k = Vec.of(v)
        return {"x": k.y / 100.0, "y": k.z / 100.0, "z": k.x / 100.0}

    def rotation_to_kit(self, r):
        """eulerAngles {"x","y","z"} (degrees) as (yaw, pitch)."""
        r = r or {}
        if isinstance(r.get("euler"), dict):  # (a Quaternion as the plugin sends it: x, y, z, w and its euler)
            r = r["euler"]
        return wrap(float(r.get("y", 0.0))), wrap(-float(r.get("x", 0.0)))

    def rotation_to_native(self, yaw, pitch=0.0):
        return {"x": (-pitch) % 360.0, "y": yaw % 360.0, "z": 0.0}

    # -- a headless game ---------------------------------------------------------------------------------

    def launch_command(self, project, url, log, map=None, exe=None):
        """A Development player build of the game (--engine <Game.exe>), or the editor in batch mode running the
        plugin's Headless.Run (it opens -PlaytestMap or the build's first scene, writes the codex export, enters play
        mode and connects); no window, no graphics either way, never -quit. The plugin reads the -Playtest* arguments
        as Unreal's does."""
        args = ["-batchmode", "-nographics", "-logFile", log, "-Playtest=goal", "-PlaytestGoal=autopilot",
                f"-PlaytestUrl={url}"] + ([f"-PlaytestMap={map}"] if map else [])
        exe = exe or self.players.get(os.path.normcase(self.project_folder(project)))
        if exe and os.path.basename(exe).lower() != "unity.exe":
            return [exe] + args
        return [exe or EDITOR, "-projectPath", self.project_folder(project),
                "-executeMethod", self.HEADLESS_METHOD] + args
