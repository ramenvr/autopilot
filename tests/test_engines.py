"""The engine seam (autopilot/engines): the engine from hello, project discovery, the codex's files per engine, the kit
frame against Unity's and Godot's, and the Game backend over the engine-neutral ops (no engine needed)."""

import json
import math
import os
import threading
import time
import types

import pytest

from autopilot import engines
from autopilot.kit import Vec, control, forward, walk_to
from autopilot.kit.game import Game, GameError

# -- the engine from hello ---------------------------------------------------------------------------------

def test_engine_from_hello():
    assert engines.for_hello({"engine": "unity"}).name == "unity"
    assert engines.for_hello({"engine": "Godot"}).name == "godot"
    assert engines.for_hello({"engine": "unreal"}).name == "unreal"
    for hello in ({}, None, {"engine": ""}, {"engine": "cryengine"}, "not a dict"):
        assert engines.for_hello(hello).name == "unreal"  # (a plugin from before engines is Unreal's)
    link = types.SimpleNamespace(hello={"project": "g", "engine": "godot"})
    game = Game(link)
    assert game.engine.name == "godot" and game.keys["jump"] == "SpaceBar"
    assert Game(types.SimpleNamespace()).engine.name == "unreal"
    assert engines.of(types.SimpleNamespace(link=link)).name == "godot"


def test_required_ops_by_engine():
    from autopilot import bridge

    assert bridge.required_ops({}) == bridge.REQUIRED_OPS
    ops = bridge.required_ops({"engine": "unity"})
    assert {"find", "transforms", "pose", "movement", "view", "key"} <= set(ops)


# -- the project on disk -----------------------------------------------------------------------------------

def _unreal(tmp_path):
    d = tmp_path / "Shooter"
    (d / "Source" / "Shooter").mkdir(parents=True)
    (d / "Config").mkdir()
    (d / "Shooter.uproject").write_text("{}")
    (d / "Source" / "Shooter" / "Gun.cpp").write_text("int ammo = 30;")
    (d / "Config" / "DefaultGame.ini").write_text("[x]")
    return d


def _unity(tmp_path):
    d = tmp_path / "Runner"
    for sub in ("Assets/Scripts", "ProjectSettings", "Packages", "Library/ScriptAssemblies",
                "Library/AutopilotCodex/scenes", "Assets/Input"):
        (d / sub).mkdir(parents=True)
    (d / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 6000.6.4f1")
    (d / "ProjectSettings" / "InputManager.asset").write_text("%YAML")
    (d / "Packages" / "manifest.json").write_text("{}")
    (d / "Assets" / "Scripts" / "Player.cs").write_text("class Player {}")
    (d / "Assets" / "Scripts" / "Player.cs.meta").write_text("guid: 1")
    (d / "Assets" / "Input" / "Controls.inputactions").write_text("{}")
    (d / "Library" / "ScriptAssemblies" / "Game.dll").write_text("binary")
    (d / "Library" / "AutopilotCodex" / "summary.txt").write_text("ok")
    (d / "Library" / "AutopilotCodex" / "scenes" / "Main.txt").write_text("Main scene")
    return d


def _godot(tmp_path):
    d = tmp_path / "Platformer"
    for sub in ("scripts", "scenes", ".godot/imported", "addons/autopilot", "addons/other"):
        (d / sub).mkdir(parents=True)
    (d / "project.godot").write_text('[application]\nconfig/name="Platformer"\n')
    (d / "scripts" / "player.gd").write_text("extends CharacterBody3D")
    (d / "scenes" / "main.tscn").write_text("[gd_scene]")
    (d / ".godot" / "imported" / "x.gd").write_text("cache")
    (d / ".godot" / "autopilot_codex").mkdir()
    (d / ".godot" / "autopilot_codex" / "summary.txt").write_text("main scene: res://scenes/main.tscn")
    (d / "addons" / "autopilot" / "plugin.gd").write_text("the addon itself")
    (d / "addons" / "other" / "tool.gd").write_text("another addon")
    (d / "icon.png").write_bytes(b"\x89PNG")
    return d


def test_find_project_for_each_engine(tmp_path):
    u = _unreal(tmp_path)
    eng, path = engines.find_project(str(u / "Shooter.uproject"))
    assert eng.name == "unreal" and path.endswith("Shooter.uproject")
    assert engines.find_project(str(u))[1].endswith("Shooter.uproject")  # (its folder)
    unity = _unity(tmp_path)
    for p in (unity, unity / "Assets", unity / "ProjectSettings" / "ProjectVersion.txt"):
        eng, path = engines.find_project(str(p))
        assert eng.name == "unity" and os.path.samefile(path, unity), p
    g = _godot(tmp_path)
    for p in (g, g / "project.godot"):
        eng, path = engines.find_project(str(p))
        assert eng.name == "godot" and os.path.samefile(path, g), p
    assert engines.find_project(str(tmp_path)) == (None, None)
    assert engines.find_project(str(tmp_path / "nothing")) == (None, None)
    assert engines.find_project(str(g), "unity") == (None, None)
    assert engines.get("godot").project_name(str(g)) == "Platformer"
    assert engines.get("unreal").project_name(str(u / "Shooter.uproject")) == "Shooter"


def test_find_project_asks_the_game(tmp_path, monkeypatch):
    """Unity and Godot plugins tell their folder in context's project_dir; it is remembered with its engine."""
    import json

    from autopilot import cli, codex

    monkeypatch.setattr(codex, "MEMORY", str(tmp_path / "mem"))
    g = _godot(tmp_path)
    game = types.SimpleNamespace(engine=engines.get("godot"), context=lambda fresh=False: {"project_dir": str(g)})
    found = cli.find_project(game, "Platformer")
    assert found and os.path.samefile(found, g)
    cli.remember_project("Platformer", found, game.engine)
    info = json.load(open(tmp_path / "mem" / "Platformer" / "project.json"))
    assert info["engine"] == "godot" and os.path.samefile(info["project"], g)
    assert cli.find_project(game, "Platformer") is None  # remembered: not asked again
    unity = _unity(tmp_path)
    game = types.SimpleNamespace(engine=engines.get("unity"), context=lambda fresh=False: {"project_dir": str(unity)})
    assert os.path.samefile(cli.find_project(game, "Runner"), unity)
    game = types.SimpleNamespace(engine=engines.get("unity"), context=lambda fresh=False: {"project_dir": ""})
    assert cli.find_project(game, "Other") is None


def test_the_project_is_found_through_the_game_and_remembered(tmp_path, monkeypatch):
    """The plugin tells its project's folder (context's project_dir): the codex is built from it, and the path is
    remembered in the project's memory."""
    from autopilot import cli, codex

    monkeypatch.setattr(codex, "MEMORY", str(tmp_path / "mem"))
    g = _godot(tmp_path)

    def request(op, args=None, timeout=None):
        if op == "session":
            return {"running": True}
        if op == "context":
            return {"world": "/root/Main", "character": "/root/Main/Player", "controller": "", "camera_manager": "",
                    "project_dir": str(g)}
        return {}

    link = types.SimpleNamespace(hello={"project": "Platformer", "engine": "godot"}, request=request,
                                 status=lambda text: None)
    built = []
    monkeypatch.setattr(codex, "build",
                        lambda project, path=None, **_: built.append(path) or codex.Codex(str(tmp_path)))
    remembered = tmp_path / "mem" / "Platformer" / "project.json"
    assert not remembered.exists()
    cli.prepare(link)
    assert built[-1] and os.path.samefile(built[-1], g) and remembered.exists()


def test_two_projects_of_one_name_keep_their_own_memory(tmp_path, monkeypatch):
    """Memory is per project: by name, and by the plugin's project_id when another project of that name has the
    folder. A folder from before ids were recorded stays its old project's; Delete memory keeps whose it is."""
    from autopilot import codex, memory

    monkeypatch.setattr(codex, "MEMORY", str(tmp_path / "mem"))
    a = {"project": "MyProject", "project_id": "1A2B3C4D-0000-0000-0000-00000000000A"}
    b = {"project": "MyProject", "project_id": "9f8e7d6c-0000-0000-0000-00000000000b"}
    assert codex.project_of({"project": "MyProject"}) == "MyProject"  # (no id: by name, as before)
    assert codex.project_of(a) == "MyProject"  # (the folder had no id yet: a's from now on)
    assert codex.project_of(b) == "MyProject-9f8e7d6c"
    assert codex.project_of(a) == "MyProject" and codex.project_of(b) == "MyProject-9f8e7d6c"
    with open(tmp_path / "mem" / "MyProject" / "project.json", encoding="utf-8") as f:
        assert json.load(f)["project_id"] == a["project_id"].lower()
    # Delete memory leaves project.json, so the folder stays a's
    (tmp_path / "mem" / "MyProject" / "notes.md").write_text("a's notes", encoding="utf-8")
    memory.forget(codex.project_dir("MyProject"))
    assert codex.project_of(b) == "MyProject-9f8e7d6c" and codex.project_of(a) == "MyProject"
    # A folder from before ids, with its path remembered: the first project with an id keeps it and what's in it
    old = tmp_path / "mem" / "Old"
    old.mkdir()
    (old / "project.json").write_text(json.dumps({"project": "C:/Games/Old/Old.uproject"}), encoding="utf-8")
    (old / "notes.md").write_text("learned", encoding="utf-8")
    assert codex.project_of({"project": "Old", "project_id": "abc"}) == "Old"
    info = json.loads((old / "project.json").read_text(encoding="utf-8"))
    assert info == {"project": "C:/Games/Old/Old.uproject", "project_id": "abc"}
    assert (old / "notes.md").read_text(encoding="utf-8") == "learned"
    # The first 8 digits' folder another id's too: all the digits
    taken = tmp_path / "mem" / "Old-abcdef12"
    taken.mkdir()
    (taken / "project.json").write_text(json.dumps({"project_id": "abcdef12-0000"}), encoding="utf-8")
    assert codex.project_of({"project": "Old", "project_id": "abcdef12-1111"}) == "Old-abcdef121111"
    assert codex.project_of({"project": "Old", "project_id": "abcdef12-0000"}) == "Old-abcdef12"


def test_a_second_project_of_one_name_is_prepared_in_its_own_folder(tmp_path, monkeypatch):
    """prepare() keeps the second project's codex and path in its own folder, and Unreal still finds the project
    file by the project's own name."""
    from autopilot import cli, codex

    monkeypatch.setattr(codex, "MEMORY", str(tmp_path / "mem"))
    folder = tmp_path / "Games" / "Shooter"
    folder.mkdir(parents=True)
    for name in ("Other", "Shooter"):
        (folder / f"{name}.uproject").write_text("{}", encoding="utf-8")
    (tmp_path / "mem" / "Shooter").mkdir(parents=True)
    (tmp_path / "mem" / "Shooter" / "project.json").write_text(json.dumps({"project_id": "first"}), encoding="utf-8")

    def request(op, args=None, timeout=None):
        if op == "session":
            return {"running": True}
        if op == "context":
            return {"world": "W", "character": "C", "controller": "", "camera_manager": "", "project_dir": str(folder)}
        return {}

    link = types.SimpleNamespace(hello={"project": "Shooter", "engine": "unreal", "project_id": "1234abcd-ef"},
                                 request=request, status=lambda text: None)
    built, asked = [], []
    monkeypatch.setattr(codex, "build",
                        lambda project, path=None, **_: built.append((project, path)) or codex.Codex(str(tmp_path)))
    unreal = type(engines.get("unreal"))
    monkeypatch.setattr(unreal, "find_project",
                        lambda self, game, name: asked.append(name) or str(folder / f"{name}.uproject"))
    project, _ = cli.prepare(link)
    assert project == "Shooter-1234abcd" and built[-1][0] == "Shooter-1234abcd"
    assert asked == ["Shooter"]  # (the project file is looked for by the project's name, not its folder's)
    assert os.path.samefile(built[-1][1], folder / "Shooter.uproject")
    info = json.loads((tmp_path / "mem" / "Shooter-1234abcd" / "project.json").read_text(encoding="utf-8"))
    assert info["project_id"] == "1234abcd-ef" and os.path.samefile(info["project"], folder / "Shooter.uproject")
    assert json.loads((tmp_path / "mem" / "Shooter" / "project.json").read_text(encoding="utf-8")) == {
        "project_id": "first"}


def test_codex_rules_per_engine():
    unity, godot, unreal = engines.get("unity"), engines.get("godot"), engines.get("unreal")
    assert unity.codex_path("Assets/Scripts/Player.cs") == "Assets/Scripts/Player.cs"
    assert unity.codex_path("Assets/Input/Controls.inputactions") == "Assets/Input/Controls.inputactions"
    assert unity.codex_path("ProjectSettings/InputManager.asset") == "ProjectSettings/InputManager.asset"
    assert unity.codex_path("Packages/manifest.json") == "Packages/manifest.json"
    assert unity.codex_path("Library/AutopilotCodex/scenes/Main.txt") == "scenes/Main.txt"
    # (the plugin writes its export to Library/ only)
    assert unity.codex_path("Temp/AutopilotCodex/summary.txt") is None
    for bad in ("Assets/Scripts/Player.cs.meta", "Library/ScriptAssemblies/Game.dll", "ProjectSettings/x/y.asset",
                "Packages/packages-lock.json", "Library/AutopilotCodex/README.md", "Assets/../../x.cs", "/Assets/x.cs",
                "C:/Assets/x.cs", "Assets\\x.cs", "Logs/x.cs", "", None):
        assert unity.codex_path(bad) is None, bad
    assert godot.codex_path("project.godot") == "project.godot"
    assert godot.codex_path("export_presets.cfg") == "export_presets.cfg"
    for ok in ("scripts/player.gd", "scenes/main.tscn", "res/theme.tres", "fx/water.gdshader", "Mono/Game.cs",
               "addons/other/tool.gd"):
        assert godot.codex_path(ok) == ok, ok
    assert godot.codex_path(".godot/autopilot_codex/summary.txt") == "summary.txt"  # (the addon's summary)
    for bad in (".godot/imported/x.gd", ".import/x.gd", "a/.hidden/x.gd", "node_modules/x.gd",
                ".godot/autopilot_codex/README.md", "addons/autopilot/plugin.gd", "icon.png", "sub/project.godot",
                "../x.gd", "/x.gd", "scripts\\x.gd", "", None):
        assert godot.codex_path(bad) is None, bad
    # (Unreal's are as they were)
    assert unreal.codex_path("Saved/AutopilotCodex/blueprints/BP_Door.txt") == "blueprints/BP_Door.txt"
    assert unreal.codex_path("Assets/Scripts/Player.cs") is None


def test_fetched_codex_follows_the_games_engine(tmp_path):
    from autopilot.codex import fetch_from_plugin

    files = {"project.godot": "[application]", "scripts/player.gd": "extends Node", ".godot/x.gd": "cache",
             "addons/autopilot/a.gd": "addon", "Source/G/A.cpp": "not godot's"}

    class FakeGame:
        engine = engines.get("godot")

        def request(self, op, args, timeout=None):
            if op == "codex_manifest":
                return {"files": [{"path": p, "size": len(t), "hash": str(len(t))} for p, t in files.items()]}
            return {"files": {p: files[p] for p in args["paths"]}}

    root = str(tmp_path / "codex")
    os.makedirs(root)
    stats = fetch_from_plugin(FakeGame(), root)
    assert stats["files"] == 2 and stats["fetched"] == 2
    assert open(os.path.join(root, "scripts", "player.gd")).read() == "extends Node"
    assert not os.path.exists(os.path.join(root, ".godot")) and not os.path.exists(os.path.join(root, "source"))


def test_local_codex_per_engine(tmp_path, monkeypatch):
    from autopilot import codex

    monkeypatch.setattr(codex, "MEMORY", str(tmp_path / "mem"))
    cx = codex.build("Runner", path=str(_unity(tmp_path)))
    have = sorted(os.path.relpath(os.path.join(b, f), cx.root).replace(os.sep, "/")
                  for b, _, fs in os.walk(cx.root) for f in fs)
    assert have == ["Assets/Input/Controls.inputactions", "Assets/Scripts/Player.cs", "Packages/manifest.json",
                    "ProjectSettings/InputManager.asset", "ProjectSettings/ProjectVersion.txt", "README.md",
                    "scenes/Main.txt", "summary.txt"]
    assert "Unity" in open(os.path.join(cx.root, "README.md")).read()
    cx = codex.build("Platformer", path=str(_godot(tmp_path)))
    have = sorted(os.path.relpath(os.path.join(b, f), cx.root).replace(os.sep, "/")
                  for b, _, fs in os.walk(cx.root) for f in fs)
    assert have == ["README.md", "addons/other/tool.gd", "project.godot", "scenes/main.tscn", "scripts/player.gd",
                    "summary.txt"]
    cx = codex.build("Shooter", path=str(_unreal(tmp_path)), export=False)
    assert os.path.exists(os.path.join(cx.root, "source", "Shooter", "Gun.cpp"))
    assert os.path.exists(os.path.join(cx.root, "config", "DefaultGame.ini"))
    assert "Blueprints by parent class" in open(os.path.join(cx.root, "README.md")).read()


# -- the kit frame -----------------------------------------------------------------------------------------

def _unity_forward(rot):
    """Unity's forward for eulerAngles (x, y) in degrees: Quaternion.Euler(x, y, 0) * Vector3.forward."""
    x, y = math.radians(rot["x"]), math.radians(rot["y"])
    return {"x": math.cos(x) * math.sin(y), "y": -math.sin(x), "z": math.cos(x) * math.cos(y)}


def _godot_forward(rot):
    """Godot's forward (-Z of the basis) for rotation (x, y) in radians, Basis.from_euler (YXZ)."""
    x, y = rot["x"], rot["y"]
    return {"x": -math.cos(x) * math.sin(y), "y": math.sin(x), "z": -math.cos(x) * math.cos(y)}


def _close(a, b, tol=1e-6):
    return all(abs(p - q) <= tol for p, q in zip((a.x, a.y, a.z), (b.x, b.y, b.z)))


@pytest.mark.parametrize("name, native_forward", [("unity", _unity_forward), ("godot", _godot_forward)])
def test_frames_round_trip(name, native_forward):
    eng = engines.get(name)
    for v in (Vec(120, -35.5, 980), Vec(0, 0, 0), Vec(-1, 2, -3)):
        assert _close(eng.to_kit(eng.to_native(v)), v)
    for yaw in (-170, -90, -30, 0, 45, 90, 135, 180):
        for pitch in (-60, -10, 0, 25, 80):
            rot = eng.rotation_to_native(yaw, pitch)
            ky, kp = eng.rotation_to_kit(rot)
            assert abs(((ky - yaw + 180) % 360) - 180) < 1e-6 and abs(kp - pitch) < 1e-6, (yaw, pitch, rot)
            # the native view's forward, in the kit's frame, is the kit's forward for that yaw and pitch: the axes,
            # the turning ways and the handedness agree
            f = eng.to_kit(native_forward(rot))
            assert _close(f / 100.0, forward(yaw, pitch)), (name, yaw, pitch)


def test_frames_known_values():
    unity, godot = engines.get("unity"), engines.get("godot")
    # metres to centimetres, forward / right / up
    assert unity.to_kit({"x": 0, "y": 0, "z": 1}) == Vec(100, 0, 0)
    assert unity.to_kit({"x": 1, "y": 0, "z": 0}) == Vec(0, 100, 0)
    assert unity.to_kit([0, 1, 0]) == Vec(0, 0, 100)
    assert godot.to_kit({"x": 0, "y": 0, "z": -1}) == Vec(100, 0, 0)
    assert godot.to_kit({"x": 1, "y": 0, "z": 0}) == Vec(0, 100, 0)
    assert godot.to_kit({"x": 0, "y": 1, "z": 0}) == Vec(0, 0, 100)
    assert godot.to_native(Vec(250, 0, 0)) == {"x": 0.0, "y": 0.0, "z": -2.5}
    # Unity looks down with a positive euler x; Godot turns right with a negative rotation.y
    assert unity.rotation_to_kit({"x": 30, "y": 90, "z": 0}) == (90, -30)
    assert unity.rotation_to_kit({"x": 330, "y": 270, "z": 0}) == (-90, 30)
    yaw, pitch = godot.rotation_to_kit({"x": math.radians(20), "y": -math.pi / 2, "z": 0})
    assert abs(yaw - 90) < 1e-9 and abs(pitch - 20) < 1e-9

    def det(eng):
        x, y, z = (eng.to_kit(v) for v in ([1, 0, 0], [0, 1, 0], [0, 0, 1]))
        return x.x * (y.y * z.z - y.z * z.y) - x.y * (y.x * z.z - y.z * z.x) + x.z * (y.x * z.y - y.y * z.x)

    # Unity is left-handed like the kit (a turn of the axes); Godot is right-handed (a mirror)
    assert det(unity) > 0 and det(godot) < 0
    unreal = engines.get("unreal")
    assert (unreal.to_kit({"X": 1, "Y": 2, "Z": 3}) == Vec(1, 2, 3)
            and unreal.to_native(Vec(1, 2, 3)) == {"X": 1, "Y": 2, "Z": 3})


# -- the Game backend over the engine-neutral ops ----------------------------------------------------------

class OpLink:
    """A Unity-like plugin's answers (kit frame): the player walks at 400/s while W is held, the mouse turns the view
    0.1 degree per count; two doors and a lamp."""

    def __init__(self, engine="unity"):
        self.hello = {"project": "ops", "engine": engine}
        self.pos, self.yaw, self.pitch = Vec(0, 0, 90), 0.0, 0.0
        self.held = set()
        self.things = {"Level/Door_A": Vec(500, 0, 100), "Level/door_b": Vec(-300, 400, 100),
                       "Level/Lamp": Vec(0, 900, 300)}
        self.classes = {"Door": ["Level/Door_A", "Level/door_b"], "Lamp": ["Level/Lamp"]}
        self.game_time, self._wall = 0.0, time.monotonic()
        self.lock = threading.Lock()
        self.log = []
        self.closed = None

    def post(self, op, args=None):
        self.request(op, args)

    def request_at(self, op, args=None, timeout=None):
        return self.request(op, args, timeout), self.game_time

    def request(self, op, args=None, timeout=None):
        from autopilot.bridge import BridgeError

        args = args or {}
        with self.lock:
            now = time.monotonic()
            dt, self._wall = now - self._wall, now
            self.game_time += dt
            if "W" in self.held:
                self.pos = self.pos + Vec(math.cos(math.radians(self.yaw)),
                                          math.sin(math.radians(self.yaw)), 0) * (400 * dt)
            self.log.append(op)
            if op == "context":
                return {"world": "Level", "character": "Level/Player", "controller": "", "camera_manager": "",
                        "project_dir": ""}
            if op == "find":
                objs = (self.classes.get(args.get("class"), []) if args.get("class")
                        else list(self.things) + ["Level/Player"])
                if args.get("name"):
                    objs = [o for o in objs if args["name"] in o.rsplit("/", 1)[-1]]
                return {"objects": objs[: args.get("limit") or None]}
            if op == "transforms":
                out = {}
                for o in args["objects"]:
                    at = self.pos if o == "Level/Player" else self.things.get(o)
                    out[o] = None if at is None else {
                        "location": at.ue(), "rotation": {"Pitch": 0, "Yaw": 0, "Roll": 0},
                        "velocity": {"X": 0, "Y": 0, "Z": 0}, "center": (at + Vec(0, 0, 50)).ue(),
                        "extent": {"X": 50, "Y": 10, "Z": 100}}
                return {"objects": out}
            if op == "pose":
                return {"at": self.pos.ue(), "eye": (self.pos + Vec(0, 0, 70)).ue(), "yaw": self.yaw,
                        "pitch": self.pitch, "velocity": {"X": 0, "Y": 0, "Z": 0}, "on_ground": True}
            if op == "movement":
                return {"walk_speed": 400, "jump_z": 500, "gravity": 980, "half_height": 90, "radius": 40,
                        "step": 30, "max_slope": 45}
            if op == "view":
                return {"visible": [], "body": {}}
            if op == "pause":
                return {"paused": args.get("paused", True)}
            if op == "key":
                key, mode = args.get("key"), args.get("mode")
                if mode == "release_all":
                    self.held.clear()
                elif mode == "hold":
                    self.held.add(key)
                elif mode == "release":
                    self.held.discard(key)
                elif key == "MouseX":
                    self.yaw = (self.yaw + 0.1 * args.get("value", 0) + 180) % 360 - 180
                elif key == "MouseY":
                    self.pitch = max(-89, min(89, self.pitch + 0.1 * args.get("value", 0)))
                return {}
            if op == "rc":
                body = args["body"]
                if args["verb"] == "describe":
                    if body["objectPath"] in self.things:
                        return {"Name": body["objectPath"], "Class": "GameObject"}
                    raise BridgeError(400, f"Object {body['objectPath']} does not exist")
                if args["verb"] == "property" and body.get("propertyName") == "position":
                    return {"position": {"x": 2.0, "y": 1.0, "z": 5.0}}  # (native: Unity's metres and axes)
            raise BridgeError(400, f"unknown op {op}")


def test_game_backend_over_the_engine_ops():
    link = OpLink()
    game = Game(link)
    assert game.engine.name == "unity"
    assert game.actors("Door") == ["Level/Door_A", "Level/door_b"]
    assert set(game.all_actors()) == {"Level/Door_A", "Level/door_b", "Level/Lamp", "Level/Player"}
    assert game.named("door") == ["Level/Door_A", "Level/door_b"]  # (any case, as Unreal's)
    assert game.resolve_class("Door") == "Door"
    assert game.loc("Level/Lamp") == Vec(0, 900, 300)
    link.log.clear()
    locs = game.locs(["Level/Door_A", "Level/Lamp", "Level/Gone"])
    assert locs == {"Level/Door_A": Vec(500, 0, 100), "Level/Lamp": Vec(0, 900, 300)} and link.log == ["transforms"]
    center, ext = game.bounds("Level/Door_A")
    assert center == Vec(500, 0, 150) and ext == Vec(50, 10, 100)
    assert game.rot("Level/Door_A")["Yaw"] == 0 and game.velocity("Level/Door_A") == Vec()
    with pytest.raises(GameError):
        game.loc("Level/Gone")
    assert game.exists("Level/Lamp") and not game.exists("Level/Gone")
    p = game.pose()
    assert p["at"] == Vec(0, 0, 90) and p["eye"] == Vec(0, 0, 160) and p["on_ground"] is True
    assert game.eye() == Vec(0, 0, 160) and game.view_rotation() == (0.0, 0.0) and game.me() == Vec(0, 0, 90)
    m = control.movement(game)
    assert m["walk_speed"] == 400 and m["half_height"] == 90 and m["radius"] == 40
    assert abs(m["peak"] - 500 ** 2 / (2 * 980)) < 1e-9 and m["reach_up"] == m["peak"] + 30
    assert game.to_kit(game.get("Level/Lamp", "position")) == Vec(500, 200, 100)
    assert game.name_of("Level/Door_A") == "Door_A" and game.name_of("Main:/Level/Pickups/Gem@2") == "Gem@2"
    assert game.name_of("Main:/Player|PlayerMover") == "Player"
    assert engines.get("godot").name_of("/root/Main/Player:position") == "Player"


def test_controllers_play_over_the_engine_ops():
    """The kit's controllers work unchanged on a Unity or Godot game: they read through the backend."""
    for engine in ("unity", "godot"):
        link = OpLink(engine)
        game = Game(link)
        game.look = {"x": 0.1, "y": 0.1}
        gen = walk_to(game, "Level/Door_A", within=80, timeout=8)
        t0 = time.monotonic()
        try:
            while time.monotonic() - t0 < 10:
                next(gen)
                time.sleep(1 / 30)
            pytest.fail("walk_to didn't end")
        except StopIteration as s:
            assert s.value, s.value.why
        assert link.pos.dist2d(Vec(500, 0, 100)) <= 80


def test_sandbox_child_knows_the_engine(tmp_path, monkeypatch):
    """Model-written code runs in the sandbox's child, whose Game takes the engine from the hello the parent passes."""
    from autopilot import codex, sandbox

    monkeypatch.setenv("AUTOPILOT_SANDBOX", "process")
    monkeypatch.setattr(codex, "MEMORY", str(tmp_path))
    root = os.path.join(str(tmp_path), "ops")
    os.makedirs(os.path.join(root, "runs", "r1"))
    sb = sandbox.Sandbox(OpLink("godot"), None, "ops", "x", "r1", root, str(tmp_path))
    try:
        out = sb.call("probe", code="print(game.engine.name)\ngame.loc(game.actors('Lamp')[0])", timeout=60)
        assert "godot" in out and "900" in out, out
    finally:
        sb.close()


# -- the prompt and the headless launch --------------------------------------------------------------------

def test_prompt_names_the_engine_and_appends_its_section():
    from autopilot.agents import session

    unreal = session.system_prompt(engines.get("unreal"), "runs/r")
    with open(session.KIT_DOC, encoding="utf-8") as f:
        kit = f.read()
    # Unreal's prompt is the one it was tuned on: KIT.md word for word, no engine section
    assert "a video game made in Unreal Engine." in unreal and kit in unreal and "## The engine:" not in unreal
    assert "GAMEPLAY" in unreal and "{engine}" not in unreal and "{kit}" not in unreal
    for name, title in (("unity", "Unity"), ("godot", "Godot")):
        text = session.system_prompt(engines.get(name), "runs/r")
        assert f"a video game made in {title}." in text and f"## The engine: {title}" in text
        assert "## The engine: Unreal Engine" not in text and "GetPlayerCharacter" not in text
        for unreal_name in ("Blueprint", "/Game/", "UMG", "Enhanced Input", "MaxStepHeight", "CharacterMovement"):
            assert unreal_name not in text[:text.index(f"## The engine: {title}")], (name, unreal_name)


def test_a_prompt_too_long_for_a_windows_command_line_goes_to_claude_code_as_a_file():
    from autopilot.agents import claude_code, session

    short = "line one\n" + "x" * 1000 + '\n"quoted"\n'
    assert claude_code.prompt_option(short, windows=True) == (short, None)  # (on the command line)
    long ="line one\n" + "x" * 40000 + '\n"quoted"\n'
    assert claude_code.prompt_option(long, windows=False) == (long, None)  # (elsewhere: no such limit)
    option, path = claude_code.prompt_option(long, windows=True)
    try:
        assert option == {"type": "file", "path": path}
        assert not os.path.normcase(path).startswith(os.path.normcase(session.codexmod.MEMORY))
        with open(path, "rb") as f:
            assert f.read() == long.encode("utf-8")  # word for word: no CRLF
    finally:
        os.remove(path)


def test_every_unreal_passage_the_other_engines_reword_is_in_kit_md():
    """KIT.md is Unreal's; engines.base.NEUTRAL_KIT rewords its Unreal passages for the other engines. A passage edited
    in KIT.md but not there would reach a Unity or Godot game in Unreal's words."""
    from autopilot.agents import session
    from autopilot.engines import base

    with open(session.KIT_DOC, encoding="utf-8") as f:
        kit = f.read()
    for unreal, _ in base.NEUTRAL_KIT:
        assert kit.count(unreal) == 1, unreal


def test_headless_launch_per_engine(tmp_path):
    url = "ws://127.0.0.1:9999/v1/bridge"
    cmd = engines.get("unreal").launch_command("C:/G/G.uproject", url, "g.log", map="/Game/Maps/A")
    assert cmd[1:3] == ["C:/G/G.uproject", "/Game/Maps/A"] and "-nullrhi" in cmd and f"-PlaytestUrl={url}" in cmd
    g = _godot(tmp_path)
    cmd = engines.get("godot").launch_command(str(g), url, "g.log")
    assert "--headless" in cmd and cmd[cmd.index("--path") + 1] == str(g) and f"-PlaytestUrl={url}" in cmd
    assert cmd.index("--") < cmd.index(f"-PlaytestUrl={url}")
    unity = engines.get("unity")
    cmd = unity.launch_command(str(_unity(tmp_path)), url, "g.log", exe="C:/Builds/Runner.exe")
    assert cmd[0] == "C:/Builds/Runner.exe" and "-batchmode" in cmd and "-nographics" in cmd
    if not unity.HEADLESS_METHOD:
        with pytest.raises(NotImplementedError):
            unity.launch_command(str(tmp_path / "Runner"), url, "g.log")
