"""Roblox: the Autopilot plugin for Roblox Studio (plugin/roblox: Autopilot.rbxmx in Studio's Plugins folder). Studio
runs the plugin in each of its DataModels: the place being edited holds the link to the brain (hello host "editor") and
plays the place (a play session's server and the player's client), whose sides answer through it (the pipe in
autopilot/bridge.py). The plugin answers the engine-neutral ops (find, transforms, pose, movement) in the kit frame, and
`rc` in Roblox's own terms: instance paths (/Workspace/Map/Door), properties, attributes and value objects, a
ModuleScript's table; studs on Roblox's axes.

Roblox's frame: x right, y up, -z forward (a CFrame's LookVector at rest), studs, right-handed; CFrame rotations in
radians (ToEulerAnglesYXZ), ry turning left. The kit's: X forward, Y right, Z up, centimetres, 1 stud = 28 cm:
    kit (X, Y, Z) = 28 * (-z, x, y);  kit yaw = -deg(ry);  kit pitch = deg(rx)

The codex is the place's scripts as Studio has them (src/<Service>/.../<Name>.server.luau, .client.luau, .luau for a
ModuleScript), and the plugin's export of the rest of the place (.autopilot/summary.txt and tree/<Service>.txt: every
service's instances with their class, place, size, attributes, tags and values). A place made with Rojo (or another
tool that syncs files into Studio) is the same: the codex reads what Studio has.
"""

import math
import os

from ..kit.geom import Vec, wrap
from .base import CODEX_OWN, OpEngine, safe_rel

STUD = 28.0  # centimetres per stud (Roblox's own conversion)
# The plugin's export of the place (summary.txt, tree/<Service>.txt), project-relative as its manifest lists it
EXPORT_PREFIX = ".autopilot/"
SCRIPT_EXT = (".luau", ".lua")
# A project on disk: a Rojo project (its *.project.json), a place file, or a folder holding one
PROJECT_FILES = ("default.project.json",)
PLACE_EXT = (".rbxl", ".rbxlx")
# What a Rojo project's folder holds besides its code that tells how the game is put together
TOP_FILES = ("default.project.json", "wally.toml", "aftman.toml", "rokit.toml", "selene.toml", ".luaurc")


class Roblox(OpEngine):
    name = "roblox"
    title = "Roblox"
    export_dir = EXPORT_PREFIX.rstrip("/")
    error_log = True  # (the plugin sends the errors and warnings the game's scripts print, both sides: `log` events)
    plugin_files = ()  # (the plugin keeps its settings and run history in Studio's plugin settings, never in a project)

    # -- the project on disk -----------------------------------------------------------------------------

    def project_at(self, path):
        """A Rojo project's folder (with default.project.json or another *.project.json), or a place file
        (.rbxl/.rbxlx), or a folder holding exactly one place file."""
        if not path:
            return None
        path = os.path.abspath(path)
        if os.path.isfile(path):
            low = path.lower()
            if low.endswith(".project.json"):
                return os.path.dirname(path)
            return path if low.endswith(PLACE_EXT) else None
        if not os.path.isdir(path):
            return None
        names = os.listdir(path)
        if any(n.lower().endswith(".project.json") for n in names):
            return path
        places = [n for n in names if n.lower().endswith(PLACE_EXT)]
        return os.path.join(path, places[0]) if len(places) == 1 else None

    def project_name(self, project):
        """A place file's name without its extension (what the plugin's hello says: Studio names the place by its
        file), else the folder's (a Rojo project's own "name" when its project file has one)."""
        if os.path.isfile(project):
            return os.path.splitext(os.path.basename(project))[0]
        try:
            import json

            with open(os.path.join(project, "default.project.json"), encoding="utf-8") as f:
                name = json.load(f).get("name")
            if isinstance(name, str) and name.strip():
                return name.strip()
        except (OSError, ValueError, AttributeError):
            pass
        return super().project_name(project)

    def walk_project(self, folder):
        if os.path.isfile(folder):  # (a place file: no source files beside it are its)
            return
        yield from super().walk_project(folder)

    def skip_dir(self, rel):
        name = os.path.basename(rel)
        return name.startswith(".") or name in ("node_modules", "Packages", "DevPackages", "ServerPackages", "build")

    # -- the game backend --------------------------------------------------------------------------------

    def all_actors(self, game):
        """The Workspace's things, as Unreal's actors: its models and parts at the top or in folders (a model of
        models, a level's "Map", counts as a folder), not every part inside a model."""
        return list((game.request("find", {"things": True}) or {}).get("objects") or [])

    # -- the codex ---------------------------------------------------------------------------------------

    def codex_path(self, rel):
        """The plugin's files at their paths: src/... (the place's scripts as .luau) and the export at the codex's top
        (.autopilot/summary.txt -> summary.txt, .autopilot/tree/Workspace.txt -> tree/Workspace.txt). A Rojo project
        copied from this machine's disk (no plugin): its .luau/.lua files and project files under local/."""
        parts = safe_rel(rel)
        if parts is None:
            return None
        if rel.startswith(EXPORT_PREFIX) and len(parts) > 1:
            dest = rel[len(EXPORT_PREFIX):]
            return dest if dest not in CODEX_OWN and dest.endswith(".txt") else None
        if parts[0] == "src" and len(parts) > 1 and rel.lower().endswith(SCRIPT_EXT):
            return rel
        if parts[0] == "local":
            return None  # (only ever written by copy_source)
        return None

    def copy_source(self, project, codex_root):
        """A Rojo project's files from this machine's disk (`autopilot codex`, or a plugin without the codex ops):
        its scripts and project files under local/ (a place file holds its scripts inside: only the plugin reads
        those)."""
        import shutil

        folder = self.project_folder(project) if os.path.isdir(project) else None
        if not folder:
            return 0
        n = 0
        for rel in self.walk_project(folder):
            low = rel.lower()
            if not (low.endswith(SCRIPT_EXT) or low.endswith(".project.json") or rel in TOP_FILES):
                continue
            if safe_rel(rel) is None:
                continue
            full = os.path.join(codex_root, "local", *rel.split("/"))
            os.makedirs(os.path.dirname(full), exist_ok=True)
            shutil.copyfile(os.path.join(folder, rel), full)
            n += 1
        return n

    def has_export(self, codex_root):
        return os.path.exists(os.path.join(codex_root, "summary.txt"))

    no_export_note = "note: no summary of the place in the codex (the Roblox plugin didn't make one)"

    def index_notes(self, codex_root):
        """Where to start in a Roblox place: what runs where, and where its scripts and instances are."""
        lines = ["", "## Where to start", "",
                 "- summary.txt: the place, its Workspace and StarterPlayer settings (walk speed, jump, camera), the "
                 "CollectionService tags, and every script with where it runs (server, client, module).",
                 "- src/<Service>/...: the scripts, at their places in the DataModel: `.server.luau` runs on the "
                 "server (Script), `.client.luau` on the player's client (LocalScript, or a Script with RunContext "
                 "Client), `.luau` is a ModuleScript (it runs where it is required). A script's children are in a "
                 "folder of its name beside it.",
                 "- tree/<Service>.txt: every instance of a service (parts with their place and size, models, GUI "
                 "texts, value objects, attributes, tags): the level is tree/Workspace.txt, the HUD and menus "
                 "tree/StarterGui.txt."]
        if os.path.isdir(os.path.join(codex_root, "local")):
            lines.append("- local/: the Rojo project's files on this machine, as they are on disk.")
        return lines

    # -- the kit frame -----------------------------------------------------------------------------------

    def to_kit(self, v):
        n = Vec.of(v)
        return None if n is None else Vec(-STUD * n.z, STUD * n.x, STUD * n.y)

    def to_native(self, v):
        k = Vec.of(v)
        return {"x": k.y / STUD, "y": k.z / STUD, "z": -k.x / STUD}

    def rotation_to_kit(self, r):
        """A CFrame as rc reads it ({"euler": {"x","y","z"} radians, "look", ...}), or Euler angles {"x","y","z"}
        (radians), as (yaw, pitch) in degrees."""
        r = r or {}
        look = r.get("look") if isinstance(r, dict) else None
        if isinstance(look, dict):
            x, y, z = (float(look.get(k, 0.0)) for k in ("x", "y", "z"))
            return wrap(math.degrees(math.atan2(x, -z))), math.degrees(math.asin(max(-1.0, min(1.0, y))))
        e = r.get("euler", r) if isinstance(r, dict) else {}
        return wrap(-math.degrees(float(e.get("y", 0.0)))), wrap(math.degrees(float(e.get("x", 0.0))))

    def rotation_to_native(self, yaw, pitch=0.0):
        """The kit's (yaw, pitch) as a CFrame's Euler angles (ToEulerAnglesYXZ, radians), for game.set of a CFrame's
        `euler`."""
        return {"x": math.radians(pitch), "y": -math.radians(wrap(yaw)), "z": 0.0}

    # -- a headless game ---------------------------------------------------------------------------------

    def launch_command(self, project, url, log, map=None, exe=None):
        raise NotImplementedError("Roblox Studio has no headless game: open the place in Studio with the Autopilot "
                                  "plugin and connect it to `autopilot serve`")
