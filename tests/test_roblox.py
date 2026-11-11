"""The Roblox adapter (autopilot/engines/roblox.py) and plugin (plugin/roblox) without Studio: projects on disk (a Rojo
project, a place file), the codex's files and index, the kit frame, the game backend's things, the teleport, the
Roblox section of the implementer's prompt, the plugin's build (one .rbxmx with every module) and its sources (each
module it requires is there, every op the brain needs is answered somewhere)."""

import json
import os
import re
import shutil
import subprocess
import sys

import pytest

from autopilot import bridge, engines, research
from autopilot.kit.game import Game
from autopilot.kit.geom import Vec

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "plugin", "roblox", "src")
sys.path.insert(0, os.path.join(ROOT, "tools"))


def _rojo(tmp_path):
    d = tmp_path / "skyfarm"
    (d / "src" / "server").mkdir(parents=True)
    (d / "src" / "client").mkdir(parents=True)
    (d / "Packages" / "Lib").mkdir(parents=True)
    (d / "default.project.json").write_text(json.dumps({"name": "Sky Farm", "tree": {"$className": "DataModel"}}))
    (d / "src" / "server" / "Farm.server.luau").write_text("print('farm')")
    (d / "src" / "client" / "Hud.client.lua").write_text("print('hud')")
    (d / "Packages" / "Lib" / "init.lua").write_text("return {}")
    (d / "notes.png").write_bytes(b"\x89PNG")
    return d


def test_projects_on_disk(tmp_path):
    rb = engines.get("roblox")
    d = _rojo(tmp_path)
    assert rb.project_at(str(d)) == str(d)
    assert rb.project_at(str(d / "default.project.json")) == str(d)
    assert rb.project_name(str(d)) == "Sky Farm"  # (the Rojo project's own name)
    place = tmp_path / "places" / "Obby World.rbxl"
    place.parent.mkdir()
    place.write_bytes(b"<roblox!")
    assert rb.project_at(str(place)) == str(place)
    assert rb.project_at(str(place.parent)) == str(place)  # (a folder of one place file)
    assert rb.project_name(str(place)) == "Obby World"  # (as Studio names the place: its file)
    assert rb.project_at(str(tmp_path / "nothing")) is None
    eng, found = engines.find_project(str(d))
    assert eng.name == "roblox" and found == str(d)


def test_codex_paths():
    rb = engines.get("roblox")
    for ok in ("src/ServerScriptService/GameManager.server.luau", "src/StarterGui/HUD/Hud.client.luau",
               "src/ReplicatedStorage/Modules/Config.luau", "src/Workspace/Door/DoorScript.server.luau"):
        assert rb.codex_path(ok) == ok, ok
    assert rb.codex_path(".autopilot/summary.txt") == "summary.txt"
    assert rb.codex_path(".autopilot/tree/Workspace.txt") == "tree/Workspace.txt"
    for bad in ("src/../x.luau", "/src/x.luau", "src\\x.luau", "C:/x.luau", "src/x.txt", "other/x.luau",
                ".autopilot/README.md", ".autopilot/level.txt", "local/x.luau", "x.luau", ".autopilot/x.json"):
        assert rb.codex_path(bad) is None, bad


def test_local_codex_of_a_rojo_project(tmp_path):
    rb = engines.get("roblox")
    d = _rojo(tmp_path)
    root = tmp_path / "codex"
    root.mkdir()
    assert rb.copy_source(str(d), str(root)) == 3
    have = sorted(str(p.relative_to(root)).replace(os.sep, "/") for p in root.rglob("*") if p.is_file())
    assert have == ["local/default.project.json", "local/src/client/Hud.client.lua",
                    "local/src/server/Farm.server.luau"]
    # (a place file holds its scripts inside: only the plugin reads those)
    place = tmp_path / "One.rbxl"
    place.write_bytes(b"<roblox!")
    assert rb.copy_source(str(place), str(tmp_path / "codex2")) == 0


def test_codex_index_says_where_to_start(tmp_path):
    rb = engines.get("roblox")
    root = tmp_path / "codex"
    (root / "src" / "ServerScriptService").mkdir(parents=True)
    (root / "tree").mkdir()
    (root / "src" / "ServerScriptService" / "Main.server.luau").write_text("print(1)")
    (root / "tree" / "Workspace.txt").write_text("# Workspace")
    (root / "summary.txt").write_text("Roblox place: Sky Farm")
    assert rb.has_export(str(root))
    rb.write_index(str(root), "Sky Farm")
    text = (root / "README.md").read_text(encoding="utf-8")
    assert "Roblox project" in text and ".server.luau" in text and "tree/<Service>.txt" in text
    assert "summary.txt" in text and ".autopilot/" in text


class ThingsLink:
    def __init__(self):
        self.hello = {"project": "Sky Farm", "engine": "roblox"}
        self.asked = []
        self.written = []
        self.game_time = 3.0

    def request(self, op, args=None, timeout=None):
        self.asked.append((op, args))
        if op == "find":
            if args.get("things"):
                return {"objects": ["/Workspace/Barn", "/Workspace/Crops/Carrot@41", "/Workspace/Crops/Carrot@42"]}
            return {"objects": ["/Workspace", "/Workspace/Barn", "/Workspace/Barn/Door", "/Workspace/Crops/Carrot@41"]}
        if op == "transforms":
            return {"objects": {o: {"location": {"X": 28, "Y": 0, "Z": 56}} for o in args["objects"]}}
        if op == "rc":
            self.written.append(args["body"])
            return {}
        if op == "context":
            return {"character": "/Workspace/Farmer", "controller": "/Players/Farmer"}
        raise AssertionError(op)


def test_all_actors_are_the_workspaces_things():
    link = ThingsLink()
    game = Game(link)
    assert game.engine.name == "roblox"
    assert game.all_actors() == ["/Workspace/Barn", "/Workspace/Crops/Carrot@41", "/Workspace/Crops/Carrot@42"]
    assert link.asked[-1] == ("find", {"things": True})
    assert game.named("carrot") == ["/Workspace/Crops/Carrot@41", "/Workspace/Crops/Carrot@42"]
    assert game.name_of("/Workspace/Crops/Carrot@41") == "Carrot@41"
    assert game.locs(["/Workspace/Barn"]) == {"/Workspace/Barn": Vec(28, 0, 56)}


def test_teleport_moves_the_pivot(monkeypatch):
    link = ThingsLink()
    game = Game(link)
    monkeypatch.setattr(research.time, "sleep", lambda s: None)
    out = research.teleport(game, Vec(280, 56, 28), why="measure the jump")
    body = link.written[-1]
    assert body["objectPath"] == "/Workspace/Farmer" and body["propertyName"] == "position"
    assert body["propertyValue"] == {"position": {"x": 2.0, "y": 1.0, "z": -10.0}}
    assert game.writes[-1]["why"] == "measure the jump" and "landed" in out


def test_no_headless_launch():
    with pytest.raises(NotImplementedError, match="Studio"):
        engines.get("roblox").launch_command("p", "ws://x", "log")


def test_prompt_roblox_section_is_general():
    from autopilot.agents import session

    text = session.system_prompt(engines.get("roblox"), "runs/r")
    section = text[text.index("## The engine: Roblox"):]
    for needle in ("server", "client", "client:", "server:", "@", "tag:", "leaderstats", "ModuleScript", "to_kit",
                   "28", "ProximityPrompt", "ClickDetector", "right mouse button", "game.restart()", "omitted",
                   "Gamepad_LeftX", "Gamepad_FaceButton_Bottom", '"device"', '"touch"', "TouchEnabled",
                   "Studio's MCP server", "whether or not Studio's window is in front", "game.player(2)",
                   "players=2"):
        assert needle in section, needle
    # nothing of a particular game (the test game's names stay out of the brain)
    for word in ("Lantern", "Grove", "Mailbox", "Crate", "Lever", "Gate", "Pond", "GameState", "Ring", "Wave"):
        assert not re.search(rf"\b{word}\b", section), word


# -- the plugin ---------------------------------------------------------------------------------------------

def _sources():
    return {f[:-5]: open(os.path.join(SRC, f), encoding="utf-8").read() for f in os.listdir(SRC) if f.endswith(".luau")}


def test_the_plugin_builds_as_one_model_with_every_module(tmp_path):
    import build_roblox

    out = build_roblox.build(str(tmp_path / "Autopilot.rbxmx"))
    text = open(out, encoding="utf-8").read()
    names = re.findall(r'<Item class="ModuleScript" referent="RBX\d+"><Properties><string name="Name">(\w+)</string>',
                       text)
    assert sorted(names) == sorted(_sources()) and "Main" in names
    assert text.count('<Item class="Script"') == 1 and "require(script.Main).start(plugin, {})" in text
    assert "DevRelay" not in names  # (the dev loader's, never the built plugin's)
    # A source with "]]>" in it survives the CDATA
    xml = build_roblox.model_xml({"Main": "local s = [[a]]>b]]\nreturn {}"})
    import xml.etree.ElementTree as ET

    item = ET.fromstring(xml).find(".//Item[@class='ModuleScript']")
    assert item.find("Properties/ProtectedString").text == "local s = [[a]]>b]]\nreturn {}"


def test_the_test_game_is_saved_as_a_place():
    tests = os.path.join(ROOT, "plugin", "roblox", "tests")
    with open(os.path.join(tests, "LanternGrove.rbxl"), "rb") as f:
        assert f.read(8) == b"<roblox!"  # (Studio's binary place file)
    assert os.path.exists(os.path.join(tests, "testgame.luau"))


def test_every_module_the_plugin_requires_is_there():
    sources = _sources()
    for name, src in sources.items():
        for needed in re.findall(r"require\(script\.Parent\.(\w+)\)", src):
            assert needed in sources, f"{name} requires {needed}"


def test_the_plugin_answers_every_op_the_brain_needs():
    """Each op of bridge.required_ops for a Roblox hello is answered: by the play session's client (Client.luau's ops)
    or by the editor itself (Editor.luau)."""
    sources = _sources()
    client = sources["Client"]
    ops_table = client[client.index("local ops: { [string]: any } = {"):]
    answered = set(re.findall(r"^\t\t(\w+) = ", ops_table, re.M))
    editor = sources["Editor"]
    answered |= set(re.findall(r'op == "(\w+)"', editor))
    needed = set(bridge.required_ops({"engine": "roblox"}))
    assert needed <= answered, needed - answered
    assert {"levels", "codex_manifest", "codex_read", "pointer", "move", "pause", "touch", "device", "capture",
            "record"} <= answered


def test_the_plugins_hello_is_what_the_brain_reads():
    editor = _sources()["Editor"]
    hello = editor[editor.index('local h: any = { type = "hello"'):]
    hello = hello[:hello.index("}\n")]
    for field in ('engine = "roblox"', 'host = "editor"', "protocol = Link.PROTOCOL", '"levels"', '"timed_hold"',
                  '"touch"', '"gamepad"', '"device"', '"players"', "share ="):
        assert field in hello, field
    # (pictures: "media" only when Studio gives the plugin its own, StudioCaptureService; else the agent service takes
    # them from Studio's MCP server and adds it: tests/test_studio_mcp.py)
    assert '"media"' not in hello and 'if self.media then\n\t\ttable.insert(h.features, "media")' in editor


def test_the_plugin_takes_gamepad_and_touch():
    """Gamepad keys by Unreal's names (buttons as KeyCodes, sticks and triggers for Pad.luau), a finger (`touch`), and
    the devices of Studio's simulator by kind."""
    sources = _sources()
    keys = sources["Keys"]
    for fkey, code in (("Gamepad_FaceButton_Bottom", "ButtonA"), ("Gamepad_FaceButton_Right", "ButtonB"),
                       ("Gamepad_LeftShoulder", "ButtonL1"), ("Gamepad_RightTrigger", "ButtonR2"),
                       ("Gamepad_DPad_Left", "DPadLeft"), ("Gamepad_Special_Right", "ButtonStart")):
        assert re.search(rf'{fkey} = "{code}"', keys), fkey
    for axis in ("Gamepad_LeftX", "Gamepad_LeftY", "Gamepad_RightX", "Gamepad_RightY", "Gamepad_LeftTriggerAxis",
                 "Gamepad_RightTriggerAxis"):
        assert f"{axis} = {{" in keys, axis
    assert "SendPointerAction(probe, { Wheel = 1e-4 })" in keys  # (the simulator's offset, measured)
    ui = sources["Ui"]
    for action in ("tap", "double_tap", "long_press", "swipe", "down", "move", "up"):
        assert f'"{action}"' in ui, action
    device = sources["Device"]
    for kind in ("phone", "tablet", "console", "vr"):
        assert re.search(rf"^\t{kind} = {{ form = ", device, re.M), kind
    assert "StudioDeviceSimulatorService" in device and "device_before" in device
    assert "needs_focus = false" in sources["Ui"] and "Ui.inputCaps({ playing = true" in sources["Client"]


def test_the_plugin_compiles():
    """Every module is valid Luau (when a Luau compiler is on PATH: luau-compile, from github.com/luau-lang/luau)."""
    exe = shutil.which("luau-compile")
    if not exe:
        pytest.skip("no luau-compile on PATH")
    paths = [os.path.join(SRC, name + ".luau") for name in sorted(_sources())]
    cases = os.path.join(ROOT, "plugin", "roblox", "tests")
    paths += [os.path.join(cases, name) for name in sorted(os.listdir(cases)) if name.endswith(".luau")]
    for path in paths:
        r = subprocess.run([exe, "--text", path], capture_output=True, text=True)
        assert r.returncode == 0, f"{path}: {r.stdout[-500:]}{r.stderr[-500:]}"


class PlayersLink:
    def __init__(self):
        self.hello = {"project": "Sky Farm", "engine": "roblox", "features": ["levels", "timed_hold", "players"]}
        self.sent = []
        self.game_time = 1.0

    def request(self, op, args=None, timeout=None):
        self.sent.append(("request", op, dict(args or {})))
        if op == "context":
            n = (args or {}).get("player", 1)
            return {"character": f"/Workspace/Player{n}", "controller": f"/Players/Player{n}", "you": n,
                    "players": [{"player": 1, "name": "Player1"}, {"player": 2, "name": "Player2"}]}
        if op == "session":
            return {"running": True, "players": args.get("players", 1)}
        return {}

    def post(self, op, args=None):
        self.sent.append(("post", op, dict(args or {})))


def test_one_brain_several_bodies():
    """game.player(n): another player of a multiplayer game, its requests sent as player n's; its keys held apart and
    renewed and let go with the game's."""
    link = PlayersLink()
    game = Game(link)
    assert game.player(1) is game and game.index == 1
    two = game.player(2)
    assert two is game.player(2) and two.index == 2 and two.player(1) is game and two.player(3) is game.player(3)
    assert game.pawn == "/Workspace/Player1" and two.pawn == "/Workspace/Player2"
    assert [p["player"] for p in game.players()] == [1, 2]
    two.hold("W")
    game.hold("D")
    assert two.held() == {"W": None} and game.held() == {"D": None}
    link.sent.clear()
    game.renew()
    renewed = {(a.get("player", 1), a["key"]) for kind, op, a in link.sent if op == "key"}
    assert renewed == {(1, "D"), (2, "W")}
    link.sent.clear()
    two.renew()  # (a body renews its own only)
    assert {a["key"] for _, op, a in link.sent if op == "key"} == {"W"}
    link.sent.clear()
    game.release_all()
    assert not two.held() and not game.held()
    assert {a.get("player", 1) for _, op, a in link.sent if op == "key" and a.get("mode") == "release_all"} == {1, 2, 3}
    # (writes are the run's, whichever body made them)
    two.set("Character", "position", {"x": 1}, why="setup")
    assert game.writes and game.writes[-1]["why"] == "setup"
    link.sent.clear()
    game.restart(players=2)
    session = [a for kind, op, a in link.sent if op == "session"][0]
    assert session == {"action": "restart", "players": 2}


def test_the_plugin_plays_several_players():
    """A multiplayer test (ExecuteMultiplayerTestAsync), a request's `player` going to that player's client, the
    server's rc taking the short names as that player's, and the clients finding the pipe in the test's arguments."""
    sources = _sources()
    editor = sources["Editor"]
    assert "ExecuteMultiplayerTestAsync(want, testArgs)" in editor and 'op = "add_players"' in editor
    assert "Host.clientSide(n)" in editor and "No player " in editor
    assert "add_players = function" in sources["Server"] and "Paths.forPlayer" in sources["Server"]
    host = sources["Host"]
    assert "GetTestArgs()" in host and 'return if (n or 1) <= 1 then "client" else "client" .. tostring(n)' in host
    assert "Host.clientSide(index)" in sources["Client"] and "players = players()" in sources["Client"]
    assert "function Paths.forPlayer" in sources["Paths"] and "p.UserId < 0" in sources["Paths"]


def test_the_detectors_work_only_while_a_run_uses_the_game():
    """As the other engines' plugins: the detectors (and the game's log) only while the editor's run is going or the
    brain asked that side something in the last 30 s; afresh each time they come on."""
    sources = _sources()
    host = sources["Host"]
    assert "function Host:runUsingGame()" in host and "Host.RUN_USE_SECONDS = 30" in host
    assert "self.lastRequestAt = os.clock()" in host and 'kind == "run"' in host
    for side in ("Client", "Server"):
        assert "detectors:setActive(host:runUsingGame())" in sources[side]
    detectors = sources["Detectors"]
    assert "function Detectors:setActive(on: boolean)" in detectors
    assert "if not self.active then" in detectors and "if not self.active or not self.enabled" in detectors
    editor = sources["Editor"]
    assert editor.count("run_active = self.runActive") == 2 and 'type = "run", active = self.runActive' in editor


def test_the_view_tells_what_the_other_engines_do():
    """view as Godot's and Unreal's: nothing omitted; held (a Tool, what is welded on, the camera's viewmodel) left out
    of the things and the rays; the room round the body (openings, free, ways_up with lane and inside) and top surfaces
    found by the body's own probes (Reach.luau: a Humanoid's step and slope, its CollisionGroup, Blockcasts); tilt, own
    box, blocks_player, crosshair_part, body_center, overhead/under, scene.sun, look's effects/highlighted/limp."""
    sources = _sources()
    sight, reach, world = sources["Sight"], sources["Reach"], sources["World"]
    assert "Sight.OMITTED = {}" in sight and "require(script.Parent.Reach)" in sight
    for arg in ("args.ways_up == true", "args.openings == true", "args.openings_reach", "args.floor_size",
                "args.overhead_reach"):
        assert arg in sight, arg
    for key in ("result.held = heldJson(held, cam)", "result.scene = { sun =", "item.blocks_player = true",
                "item.crosshair_part = crosshairPart", "item.tilt = {", "item.box = {", "item.body_center",
                "item.body_shown", "body.overhead = told", "body.under = {", "look.highlighted = true",
                "look.limp = true", "{ asset = e, scale = effects[e] }", "attached_at", "in_view"):
        assert key in sight, key
    # held: an equipped Tool, welded things outside the character, the camera's children; out of every ray
    assert 'c:IsA("Tool")' in sight and "GetConnectedParts(true)" in sight and "cam:GetChildren()" in sight
    assert "for _, h in ipairs(held or Sight.heldThings()) do" in sight and "heldSet[thing]" in sight
    assert "p.CollisionGroup = root.CollisionGroup" in sight
    # the body's own probes: a Humanoid's step (0.9 x HipHeight), slopes to at most 60 degrees, Blockcast sweeps
    assert "math.max(hipHeight, 0.5) * 0.9 * S" in reach and "math.clamp(maxSlope, 0, 60)" in reach
    assert "workspace:Blockcast(" in reach and "workspace:Raycast(o, d, self.params)" in reach
    for out in ("body.ways_up = waysUp(", "body.openings = listed", "body.free = free", "o.lane = {", "o.inside",
                'o.seen_from = "side"', "item.top_slope_degrees", "item.low_end", "item.high_end", "item.lane = lane",
                "item.uphill = {"):
        assert out in reach, out
    assert 'inst:IsA("Terrain")' in sight  # (the terrain: a floor and a wall, never a thing named)
    # a thing's own box, as it is turned
    assert "function World.box(inst: Instance): (Vector3?, Vector3?, CFrame?, Vector3?)" in world
    assert "return c, s, inst.CFrame, inst.Size" in world


def test_watch_tells_effects_stopped_and_went_limp():
    sight = _sources()["Sight"]
    assert '{ "effects", "effect", "stopped" }' in sight and '{ "sounds", "sound", "ended" }' in sight
    assert '"went limp"' in sight


def test_the_test_game_has_a_way_up_and_a_tool():
    game = open(os.path.join(ROOT, "plugin", "roblox", "tests", "testgame.luau"), encoding="utf-8").read()
    for name in ('Name = "Lookout"', 'Name = "Ramp"', 'Name = "Beacon"', 'torch.Name = "Torch"',
                 "torch.Parent = StarterPack"):
        assert name in game, name
    tests = os.path.join(ROOT, "plugin", "roblox", "tests")
    assert '"ways_up": True' in open(os.path.join(tests, "smoke.py"), encoding="utf-8").read()
    assert "/Workspace/Grove/Beacon" in open(os.path.join(tests, "kit_live.py"), encoding="utf-8").read()
    assert 'attached_at") == "HumanoidRootPart"' in open(os.path.join(tests, "multiplayer.py"), encoding="utf-8").read()


def test_the_detectors_as_godots_teleport_actor_growth_over_max():
    """As Godot's detectors.gd: the same severities and thresholds; teleport, actor_growth and over_max on the server
    only (one finding each, every character, no streaming), with Godot's rules and shapes; no clamp (documented)."""
    detectors = _sources()["Detectors"]
    for key in ("teleport_factor = 3.0", "teleport_min = 1000.0", "growth_seconds = 120.0", "property_every = 0.5"):
        assert key in detectors, key
    for name in ("long_fall", "out_of_world", "hud_fraction"):
        assert f'detector = "{name}", severity = "warning"' in detectors, name
    assert 'detector = "teleport", severity = "suspicion"' in detectors and '"teleport:" .. kind' in detectors
    assert "from = kit1(track.at), to = kit1(pos), moved = " in detectors
    assert "allowed = math.round(allowed)" in detectors
    assert "track.frames > 5" in detectors and "track.root ~= part" in detectors  # (5 frames; a respawn's new root)
    assert "AssemblyLinearVelocity.Magnitude * S" in detectors and 'FindFirstChild("HumanoidRootPart")' in detectors
    assert "math.max(least, factor * math.max(speed, track.speed) * dt)" in detectors
    assert 'detector = "actor_growth", severity = "warning"' in detectors and "World.things()" in detectors
    assert "details = { from = first, to = last, seconds = self.config.growth_seconds }" in detectors
    assert "last < 2 * first or last - first < 50" in detectors and "GROWTH_EVERY = 10" in detectors
    assert 'detector = "over_max", severity = "warning"' in detectors
    assert "details = { property = property, value = v, max_property = maxProperty, max = mx }" in detectors
    for pattern in ('"max_" .. n', 'n .. "_max"', '"Max" .. pascal', 'n .. "Max"', '"Max_" .. n', 'n .. "_Max"'):
        assert pattern in detectors, pattern
    assert "CollectionService:GetTags(inst)" in detectors and 'string.gsub(inst.Name, "%d+", "#")' in detectors
    # (the server's only, at a bounded cost: no descendant walk a frame for them)
    assert "self.server = RunService:IsServer()" in detectors and "if self.server then" in detectors
    assert "SWEEP_BUDGET = 500" in detectors and "DescendantAdded:Connect" in detectors
    tick = detectors[detectors.index("function Detectors:tick("):detectors.index("function Detectors:_hud(")]
    assert "GetDescendants" not in tick
    readme = open(os.path.join(ROOT, "plugin", "roblox", "README.md"), encoding="utf-8").read()
    row = next(line for line in readme.splitlines() if line.startswith("| `detectors` |"))
    assert row.startswith("| `detectors` | Only while a run uses the game")
    for word in ("`teleport`", "`actor_growth`", "`over_max`", "No `clamp`", "`teleport_factor`", "`teleport_min`",
                 "`growth_seconds`", "`property_every`", "suspicion", "leaderstats"):
        assert word in row, word
    guide = open(os.path.join(ROOT, "autopilot", "engines", "roblox.md"), encoding="utf-8").read()
    assert "teleports" in guide and "actor growth" in guide and "No `clamp`" in guide


def test_a_teleport_the_agent_made_is_not_the_games():
    """The edit side tells the server what the agent did as it passes a request on (the server sees the characters,
    the client gets the keys): a teleport right after the agent's own rc write or call is not told; right after its key,
    action or UI press it is info, saying the key (a reset key may be meant to teleport), with details.after_input."""
    sources = _sources()
    detectors, editor = sources["Detectors"], sources["Editor"]
    assert "teleport_after_input_seconds = 1.0" in detectors and "MOVE_GRACE = 0.5" in detectors
    assert "function Detectors:noteActed(note: any)" in detectors
    assert 'if moved > allowed and not agentMoved then' in detectors
    assert 'detector = "teleport", severity = "info"' in detectors
    assert 'summary .. " right after the agent pressed " .. input.key' in detectors
    assert '"teleport:" .. kind .. " after " .. input.key' in detectors
    assert "details.after_input = { key = input.key, seconds = Frame.r2(since) }" in detectors
    assert "self.heldKeys[key] then" in detectors  # (a hold renewed isn't a press)
    assert "function Editor.acted(op: string, args: any): any" in editor
    assert 'acted.to = "server"' in editor and 'acted.type = "acted"' in editor
    request = editor[editor.index("function Editor:_onRequest("):]
    # the agent's act is noted before the request goes on
    assert request.index("local acted = Editor.acted(op, args)") < request.index('if op == "rc" then')
    assert 'kind == "acted"' in sources["Host"] and "detectors:noteActed(frame)" in sources["Server"]
    readme = open(os.path.join(ROOT, "plugin", "roblox", "README.md"), encoding="utf-8").read()
    row = next(line for line in readme.splitlines() if line.startswith("| `detectors` |"))
    for word in ("`teleport_after_input_seconds`", "`details.after_input`", "right after the agent pressed"):
        assert word in row, word
    assert "counts too" not in row
    guide = open(os.path.join(ROOT, "autopilot", "engines", "roblox.md"), encoding="utf-8").read()
    assert "after_input" in guide and "is one too" not in guide


def test_a_detectors_many_kinds_are_told_once():
    """A detector with more than 3 kinds of finding in a run (an out_of_world for each of 33 classes) is one report
    line listing every kind, one result card (its own cards on Show each), and a feed line for its first 3 kinds and
    one saying there are more, as the other engines' docks."""
    sources = _sources()
    model, page, dock = sources["RunModel"], sources["RunPage"], sources["Dock"]
    assert "M.GROUP_AT = 3" in model and "#list <= M.GROUP_AT" in model
    assert '"- [engine · %s · %s] %d kinds: %s — e.g. %s"' in model
    assert '" (not a bug)"' in model and 'string.format(" (×%d)", count)' in model
    assert "error = 3, warning = 2, suspicion = 1, info = 0" in model
    assert '" (and more " .. det .. " findings: all of them are in the result)"' in model
    assert "index < M.GROUP_AT" in model and "index == M.GROUP_AT and fresh" in model
    assert "local line = r:findingLine(f, fresh)" in dock
    assert "function RunPage:_groupCard(" in page and '"Show each"' in page and '"Hide"' in page
    assert "M.findingGroups(engine)" in page and '"%d kinds"' in page
    for text in (model, page):
        assert "more)" not in text.replace("(and more", "")  # (no "+N more" that hides kinds)


class PlacelessLink(ThingsLink):
    """transforms as the plugin answers an instance with no place in the world (a script, a value, a GUI object):
    its fields null, `active` still there."""

    def request(self, op, args=None, timeout=None):
        if op == "transforms":
            out = {}
            for o in args["objects"]:
                if o.endswith("Gone"):
                    out[o] = None
                elif o.startswith("/Workspace"):
                    out[o] = {"location": {"X": 28, "Y": 0, "Z": 56}, "rotation": {"Pitch": 0, "Yaw": 90, "Roll": 0},
                              "velocity": {"X": 0, "Y": 0, "Z": 0}, "center": {"X": 28, "Y": 0, "Z": 70},
                              "extent": {"X": 14, "Y": 14, "Z": 14}, "active": True}
                else:
                    out[o] = {k: None for k in ("location", "rotation", "velocity", "center", "extent")}
                    out[o]["active"] = False
            return {"objects": out}
        return super().request(op, args, timeout)


def test_an_instance_with_no_place_is_there_but_has_no_location():
    """transforms' `active` for every instance: one with no place (a script) answers null fields, and the kit takes it
    as there with no location (not as gone, which only a null answer is)."""
    game = Game(PlacelessLink())
    script = "/ServerScriptService/GameManager"
    assert game.loc(script) is None and game.velocity(script) == Vec(0, 0, 0)
    assert game.bounds(script) == (None, Vec(0, 0, 0))
    assert game.engine.transforms(game, [script])[script]["active"] is False
    assert game.locs([script, "/Workspace/Barn", "/Workspace/Gone"]) == {"/Workspace/Barn": Vec(28, 0, 56)}
    assert game.bounds("/Workspace/Barn") == (Vec(28, 0, 70), Vec(14, 14, 14))
    with pytest.raises(Exception, match="does not exist"):
        game.loc("/Workspace/Gone")


def test_the_plugin_speaks_the_protocol_as_the_other_plugins():
    """Godot's (and Unity's) protocol, in Roblox's terms: context and hello `input` with notes; context `net` (a client
    of the game's server, rc on the server); session status `loading` and `scene`, a map that isn't the place 404,
    session events with the scene; move via auto/stick/keys/controller, scale -1..1; ui's text_input `text`; find's
    limit and an unknown class 400; transforms' `active`; no-game screen/ui/timestep; hello's `pointer`."""
    sources = _sources()
    editor, client, ui, world = sources["Editor"], sources["Client"], sources["Ui"], sources["World"]
    hello = editor[editor.index('local h: any = { type = "hello"'):]
    hello = hello[:hello.index("}\n")]
    assert '"pointer"' in hello and "input = Ui.inputCaps({ playing = false })" in hello
    caps = ui[ui.index("function Ui.inputCaps"):]
    caps = caps[:caps.index("\nend\n")]
    for field in ('handling = "roblox"', "keys = true", "actions = true", "pointer = true", 'move = "stick"',
                  "notes = notes", "keys_reach = reach", "needs_focus = false", "touch = touch", "out.pad = opts.pad"):
        assert field in caps, field
    for note in ("right mouse button is held", "Escape", "touch device", "Scriptable", "didn't reach the game"):
        assert note in caps, note
    context = editor[editor.index("function Editor:_editorContext"):]
    context = context[:context.index("\nend\n")]
    assert "input = Ui.inputCaps" in context and 'net = { mode = "standalone" }' in context
    assert 'mode = "client", rc_side = "server"' in client and "out.clients = n" in client and "net = net()," in client
    # session
    assert "loading = self:loading(), scene = game.Name" in editor and "function Editor:loading(): boolean" in editor
    assert "self.joining = true" in editor and "self.joining = false" in editor
    assert ': a Roblox place is one level (" .. game.Name .. ")", 404)' in editor
    assert editor.count('kind = "session"') == 2 and editor.count("scene = game.Name } }") == 2
    # no game
    for answer in ('op == "screen" then', "texts = Json.object()", 'op == "ui" then', "elements = {}",
                   'op == "timestep" then'):
        assert answer in editor, answer
    # move
    assert ('local VIAS = { auto = "stick", stick = "stick", humanoid = "stick", keys = "keys", '
            'controller = "controller" }') in client
    assert "move: unknown via" in client and "math.clamp(tonumber(args.scale) or 1, -1, 1)" in client
    assert "(hum :: Humanoid):Move(v, false)" in client and "moved = Frame.r2(moved), via = via" in client
    # ui
    assert "item.text = (g :: TextBox).Text" in ui and "item.value = item.text" in ui and "item.placeholder" in ui
    # find, transforms
    assert "tonumber(args.limit) or 10000" in world and ":GetClass(cls)" in world
    assert '"find: no class, tag or script named "' in world and "return false, why, 400" in client
    assert "function World.activeOf(inst: Instance): boolean" in world
    assert world.count("active = active") == 2 and "location = Json.null" in world


def test_the_dock_sends_the_design_brief_research_and_inspect():
    """As Godot's dock: the Settings' Design brief in every run's start (`design_brief`), Show Policy Log as the hello's
    `inspect` and the Research agent switch as its `research` (only once chosen), the hello sent again when either
    changes; the Policy log of the "policy" events, the run's usage, and "Waiting for the game to start"."""
    sources = _sources()
    settings, editor = sources["Settings"], sources["Editor"]
    # (the dock is several modules: the layout and the runs in Dock, the run page, the Policy log, the Settings page
    # and the first-run setup in their own)
    dock = "\n".join(sources[m] for m in DOCK_MODULES)
    defaults = settings[settings.index("local DEFAULTS"):]
    defaults = defaults[:defaults.index("\n}\n")]
    assert '\tdesign_brief = "",' in defaults and "\tshow_policy_log = true," in defaults
    assert "\tresearch =" not in defaults  # (unset until the user chooses: the agent service decides)
    assert dock.count("design_brief = brief") == 2  # (the composer's run and Run again)
    assert 'local run: any = { kind = "auto", prompt = words, time_limit_minutes = n }' in dock
    hello = editor[editor.index("function Editor:_hello()"):]
    hello = hello[:hello.index("\nend\n")]
    assert 'inspect = Settings.get(self.plugin, "show_policy_log") ~= false' in hello and "inspect = true" not in hello
    assert 'local research = Settings.get(self.plugin, "research")' in hello
    assert 'if type(research) == "boolean" then\n\t\th.research = research' in hello
    assert "function Editor:helloChanged()" in editor and dock.count("d.editor:helloChanged()") == 2
    for setting in ('Settings.set(d.plugin, "research", not research)',
                    'Settings.set(d.plugin, "show_policy_log", not policy)',
                    'Settings.set(d.plugin, "design_brief", brief.Text)'):
        assert setting in dock, setting
    assert 'on("policy"' in dock and 'on("usage"' in dock
    assert "POLICY_KEPT = 300" in dock and "POLICY_SHOWN = 100" in dock
    assert '"Waiting for the game to start"' in dock and "ed.runActive and ed.starting and not ed.playing" in dock


DOCK_MODULES = ("Dock", "RunPage", "PolicyLog", "SettingsPage", "SetupPage", "RunModel", "Theme")


def test_the_dock_is_laid_out_as_the_other_plugins_panels():
    """The Unreal, Unity and Godot panels' layout: the run history as a rail when wide and a dropdown when narrow, the
    gear and the ⋮ menu (Connect or Disconnect, Settings, Rerun setup, Reset memory…), pages in place of one scroll (the
    connect card, the composer, the run page's Live / Feed / Policy log tabs, the Settings page, the first-run setup),
    the Memory row; Aura's palette; and only the panels' words (attempts, never episodes; no architecture words)."""
    sources = _sources()
    dock = sources["Dock"]
    assert "local WIDE_AT = 700" in dock and "local RAIL_WIDTH = 280" in dock
    for item in ('"Disconnect" else "Connect"', '{ "Settings",', '{ "Rerun setup",', '{ "Reset memory…",'):
        assert item in dock, item
    for page in ('self:_page("Connect")', 'self:_page("Composer")', 'self:_page("Run")', 'self:_page("Settings")'):
        assert page in dock, page
    run = sources["RunPage"]
    assert '"Policy log · %d"' in run and '"Live" else "Result"' in run and '"Feed"' in run
    # (a feed line is its time and its words: no kind letter in place of the other docks' icons, which a Roblox plugin
    # can't draw in the playtest's client, where the dock is while a run plays)
    assert "GLYPHS" not in run and "GLYPHS" not in sources["RunModel"]
    theme = sources["Theme"]
    for color in ('Color3.fromHex("121316")', 'Color3.fromHex("181a1e")', 'Color3.fromHex("24262b")',
                  'Color3.fromHex("f0873a")', "Theme.R_PLATE = 8"):
        assert color in theme, color
    setup = sources["SetupPage"]
    for step in ('"1 ACCOUNT"', '"2 AGENT SERVICE"', '"3 PRIVACY SETTINGS"', '"4 FIRST RUN"'):
        assert step in setup, step
    assert "ed:saveSetup(setup)" in setup
    # (the dock tells the person to run the agent service themselves, and waits for it quietly: a Roblox plugin can't
    # start it, and its not running yet isn't an error to show)
    assert 'local SERVE_COMMAND = "autopilot serve"' in dock and '"Start the agent service"' in dock
    for m in ("Dock", "SetupPage", "SettingsPage"):
        assert "SERVE_COMMAND" in sources[m], m
        assert "Waiting for the agent service…" in sources[m] or m == "SettingsPage", m
        # (the link's own "Can't reach …" line stays in the dock's QUIET list: the status line never shows it)
        for alarm in ("Can’t reach the agent service", "No agent service", "service: not running", '"Try now"',
                      '"Reconnect"'):
            assert alarm not in sources[m], (m, alarm)
    # (the words shown: the quoted strings of the dock's modules with a space in them, not the protocol's names)
    # (M.scrub's own patterns, the words it takes out of the agent service's lines, aside)
    texts = {m: re.sub(r"function M\.scrub\(.*?\nend\n", "", sources[m], flags=re.S) for m in DOCK_MODULES}
    shown = " ".join(s for m in DOCK_MODULES for s in re.findall(r'"([^"\n]*)"', texts[m]) if " " in s.strip())
    for word in ("episode", "Episode", "Jev", "brain", "policy network", " RL "):
        assert word not in shown, word


def _function_body(src, header):
    body = src[src.index(header):]
    return body[:body.index("\nend\n")]


def test_the_dock_is_mirrored_in_the_playtests_client():
    """Studio draws no edit widget during play: player 1's client draws the same dock (Mirror.luau), fed the edit dock's
    state (a snapshot) and the editor's events (batched) over the editor's pipe, its actions sent back to the edit side;
    the mirror's editor stands in for every editor call the dock's modules make, and what makes no sense mid-play is
    disabled."""
    sources = _sources()
    mirror, dock, editor, host, main = (sources[m] for m in ("Mirror", "Dock", "Editor", "Host", "Main"))
    # every editor event the dock listens to is forwarded
    listened = set(re.findall(r'^\ton\("(\w+)"', _function_body(dock, "function Dock:_listen()"), re.M))
    events = set(re.findall(r'"(\w+)"', mirror[mirror.index("Mirror.EVENTS = {"):mirror.index("}", mirror.index(
        "Mirror.EVENTS = {"))]))
    assert listened and listened <= events, listened - events
    # the mirror's editor has every method the dock's modules call on their editor
    called = set()
    for m in DOCK_MODULES:
        called |= set(re.findall(r"(?:self\.editor|d\.editor|\bed):(\w+)\(", sources[m]))
    defined = set(re.findall(r"^function MirrorEditor:(\w+)\(", mirror, re.M))
    assert called <= defined, called - defined
    # the pipe: a snapshot when the client joins, the events in batches, actions back
    assert 'self:_fire("side_ready", side)' in editor
    assert 'kind == "dock_action" and side == "client"' in editor and 'self:_fire("dock_action", frame)' in editor
    assert 'kind == "dock" or kind == "dock_snapshot"' in host and "self.onDock(frame)" in host
    assert 'type = "dock_snapshot", state = dock:snapshot(Mirror.STEPS)' in mirror
    assert 'to = "client", type = "dock", events = batch, ed = Dock.editorState(editor)' in mirror
    assert "now - self.flushAt >= Mirror.FLUSH" in mirror and "Mirror.FLUSH = 0.1" in mirror
    assert 'frame.type = "dock_action"' in mirror
    for action in ('a == "start"', 'a == "stop"', 'a == "steer"', 'a == "feedback"'):
        assert action in _function_body(mirror, "function Mirror.act("), action
    # only player 1's client draws it, first thing in the client's start, in a widget of the dock's id
    assert "Paths.playerIndex(me) ~= 1" in mirror and 'CreateDockWidgetPluginGuiAsync("AutopilotDock", info)' in mirror
    client = main[main.index('elseif side == "client" then'):]
    assert client.index("Mirror).client(plugin)") < client.index("Client).start(plugin)")
    assert "mirror.attach(client.host)" in client and "host = host," in sources["Client"]
    # the mirror writes nothing and does nothing mid-play that would half work
    assert "self.plugin = if self.mirror then Dock.readOnly(plugin) else plugin" in dock
    assert "SetSetting = function() end," in dock
    assert 'Dock.DURING_PLAY = "Not during play: once the playtest stops"' in dock
    for guard in ('{ "Rerun setup", idle and not self.mirror', "and not self.setupOpen and not self.mirror, false",
                  "self.stripBtn.Visible = not trying and not self.mirror", "box.TextEditable = not self.mirror",
                  "disconnect.Visible = not self.mirror", "T.disable(cb, self.mirror)"):
        assert guard in dock, guard
    settings = sources["SettingsPage"]
    assert settings.count("{ disabled = d.mirror }") == 3 and "brief.TextEditable = not d.mirror" in settings
    for method in ("connect", "disconnect", "clearMemory", "signOut", "saveSetup", "setSharing"):
        body = mirror[mirror.index(f"function MirrorEditor:{method}("):]
        assert "_act(" not in body[:body.index("end")], method


def test_a_steer_is_a_hint_or_a_redirect_when_the_agent_service_tells_them_apart():
    """As in the other plugins: with "steer_mode" in the welcome the steer box says play goes on, Send sends a hint and
    Redirect beside it a redirect (the frame's "mode"); without it one Steer button and no mode, as before. The mirror's
    dock passes which one back to the edit side."""
    sources = _sources()
    editor, mirror, run, dock = (sources[m] for m in ("Editor", "Mirror", "RunPage", "Dock"))
    assert 'elseif f == "steer_mode" then\n\t\t\t\tself.brainSteerModes = true' in editor
    steer = _function_body(editor, "function Editor:steer(")
    assert 'local frame: any = { type = "steer", text = said }' in steer
    assert 'if self:canRedirect() then\n\t\t\tframe.mode = if redirect then "redirect" else "hint"' in steer
    assert "return self:canSteer() and self.brainSteerModes" in _function_body(editor, "function Editor:canRedirect(")
    assert 'local STEER_HINT_MODES = "Add a hint; Autopilot keeps playing"' in run
    assert 'T.button(well, "Redirect", "default", function()\n\t\tself:_onSteer(true)' in run
    update = _function_body(run, "function RunPage:_updateSteer(")
    assert "self.steerBox.PlaceholderText = if modes then STEER_HINT_MODES else STEER_HINT" in update
    assert 'if modes then "Send" else "Steer"' in update and "self.redirectBtn.Visible = modes" in update
    assert "T.disable(self.redirectBtn, not now or not typed)" in update
    send = _function_body(run, "function RunPage:_onSteer(")
    assert "ed:steer(said, redirecting)" in send and '"You (redirect): "' in send
    # the mirror: its state carries steer_mode, its Redirect goes back to the edit side as such
    assert "brainSteerModes = ed.brainSteerModes" in dock
    assert "self.brainSteerModes = s.brainSteerModes == true" in mirror
    assert 'self:_act({ action = "steer", text = text, redirect = redirect == true })' in mirror
    act = _function_body(mirror, "function Mirror.act(")
    assert "local redirect = f.redirect == true and editor:canRedirect()" in act
    assert "editor:steer(text, redirect)" in act


def test_the_edit_dock_always_comes_back_after_play():
    """The edit dock is hidden only while a mirror says it is drawing (dock_mirror, said every second): stale or gone,
    the edit dock shows again as it was, a stopped mirror clears it at once, the edit mirror's stop shows the dock, and
    a dock hidden when Studio went away is shown at the next start (dock_hidden)."""
    sources = _sources()
    mirror, dock, main = sources["Mirror"], sources["Dock"], sources["Main"]
    assert "Mirror.FRESH_MS = 3000" in mirror
    fresh = _function_body(mirror, "function Mirror.fresh(")
    assert "(now or nowMs()) - m.at < Mirror.FRESH_MS" in fresh
    poll = _function_body(mirror, "\tfunction self.poll()")
    assert "if Mirror.fresh(m) then" in poll and "dock:setPlayHidden(true)" in poll
    assert "elseif dock.playHidden then\n\t\t\tdock:setPlayHidden(false)" in poll
    assert 'elseif Settings.get(plugin, "dock_hidden") ~= nil then' in poll  # (Studio went away with it hidden)
    assert "\tself.poll()\n" in mirror  # (at once when the edit side starts)
    assert "now - self.pollAt >= 0.25" in mirror and "pcall(self.poll)" in mirror
    stop = _function_body(mirror, "\tfunction self.stop()\n\t\tself.dead = true")
    assert "dock:setPlayHidden(false)" in stop
    client_stop = mirror[mirror.rindex("\tfunction self.stop()"):]
    assert 'Settings.set(plugin, "dock_mirror", nil)' in client_stop
    assert "os.clock() - beatAt >= 1" in mirror  # (the mirror says it is there every second)
    hide = _function_body(dock, "function Dock:setPlayHidden(on: boolean)")
    assert 'Settings.set(self.plugin, "dock_hidden", { was = w.Enabled })' in hide
    assert 'Settings.set(self.plugin, "dock_hidden", nil)' in hide and "w.Enabled = was" in hide
    assert "require(script.Parent.Mirror).edit(plugin, editor, dock)" in main  # (stopped with the plugin)


def test_the_settings_have_a_bug_detectors_switch():
    """As the other plugins' Settings: Bug detectors, this project's, shown as memory_state's "detectors" says (on when
    an older agent service says nothing), flipped through the editor's link as Reset memory and Not a bug go (so over
    the WebSocket or HTTP alike), not changed while not connected or mid-play; the mirror's dock shows it too."""
    sources = _sources()
    page, editor, mirror, dock = (sources[m] for m in ("SettingsPage", "Editor", "Mirror", "Dock"))
    assert 'settingCard(v, "Bug detectors", DETECTORS)' in page
    assert ('"During gameplay, Autopilot watches for potential bugs and reports them. You can enable/disable this "\n'
            '\t.. "per-project."') in page
    assert 'T.switch(dc, d.editor.detectors ~= false, "Bug detectors on", "Bug detectors off"' in page
    assert "if not d.editor:setDetectors(on) then" in page
    assert "{ disabled = d.mirror or not d.editor:connected() }" in page
    # (after the Research agent, before the Design brief, as in the other plugins)
    assert page.index('"Research agent", RESEARCH') < page.index('"Bug detectors", DETECTORS') < \
        page.index('"Design brief", BRIEF_TEXT')
    assert "\tself.detectors = true\n" in editor
    assert "self.detectors = frame.detectors ~= false" in editor
    flip = _function_body(editor, "function Editor:setDetectors(on: boolean): boolean")
    assert "if not self:connected() then" in flip and "self.detectors = on" in flip
    assert 'return self.link:send({ type = "detectors", enabled = on })' in flip
    # (the same way as the other control messages: the link's send, which takes the WebSocket or HTTP)
    assert 'self:_send({ type = "clear_memory" })' in editor
    assert "function Editor:_send(frame: any)\n\tself.link:send(" in editor
    # every memory_state redraws the Settings page
    memory = dock[dock.index('on("memory", function()'):]
    assert "self:renderPages()" in memory[:memory.index("end)")]
    # the mirror's dock: shown as the edit side's, never flipped
    assert "detectors = ed.detectors }" in dock and "self.detectors = s.detectors ~= false" in mirror
    body = mirror[mirror.index("function MirrorEditor:setDetectors("):]
    assert "_act(" not in body[:body.index("end")]
    readme = open(os.path.join(ROOT, "plugin", "roblox", "README.md"), encoding="utf-8").read()
    row = next(line for line in readme.splitlines() if line.startswith("| **Bug detectors** |"))
    for word in ("`memory_state`", '`{"type": "detectors", "enabled": ...}`', "`detectors.json`",
                 "only while connected"):
        assert word in row, word


# Enough of Roblox for the Editor's and the Settings page's modules to load and draw in Luau's own CLI: every service,
# instance and other module a stand-in that takes anything, the Theme's switch and labels written down, the plugin's
# settings empty.
_LUAU_PRELUDE = r"""
local function any(): any
	return setmetatable({}, { __index = function() return any() end, __call = function() return any() end,
		__newindex = function() end })
end
local game = any()
local script = { Parent = setmetatable({}, { __index = function(_, k) return k end }) }
local Instance, UDim2, Color3, Vector2, Enum, DateTime = any(), any(), any(), any(), any(), any()
local task = { defer = function() end, spawn = function() end, delay = function() end }
local function warn(...) end
local switches, labels = {}, {}
local Theme = setmetatable({ focused = false, C = any(),
	switch = function(parent, on, onWords, offWords, onToggle, opts)
		table.insert(switches, { on = on, onWords = onWords, offWords = offWords, onToggle = onToggle,
			opts = opts or {} })
		return any()
	end,
	label = function(parent, text) table.insert(labels, text) return any() end },
	{ __index = function() return any() end })
local modules = { Theme = Theme }
local function require(name: any): any
	return modules[name] or any()
end
"""

_LUAU_CHECKS = r"""
local plugin = { GetSetting = function() return nil end, SetSetting = function() end }
local sent = {}
local link = { connected = true, role = "editor", url = "ws://127.0.0.1:8765/v1/bridge", problem = "",
	send = function(_, frame) table.insert(sent, frame) return true end }
local ed = setmetatable({ plugin = plugin, link = link, listeners = {}, memory = {}, detectors = true,
	runActive = false, account = {} }, Editor)
local memoryEvents = 0
ed:on("memory", function() memoryEvents += 1 end)
-- an older agent service's memory_state: on
ed:_onBrainFrame({ type = "memory_state", runs = 2 })
assert(ed.detectors == true and memoryEvents == 1, "no detectors field: on")
ed:_onBrainFrame({ type = "memory_state", runs = 2, detectors = false })
assert(ed.detectors == false and memoryEvents == 2, "memory_state's detectors false: off")
local function card(d)
	table.clear(switches)
	table.clear(labels)
	SettingsPage.render(d, any())
	for _, s in ipairs(switches) do
		if s.onWords == "Bug detectors on" then
			return s
		end
	end
	error("no Bug detectors switch")
end
local d = { editor = ed, plugin = plugin, runs = {}, mirror = false, SERVE_COMMAND = "autopilot serve" }
local s = card(d)
assert(s.on == false and s.offWords == "Bug detectors off" and not s.opts.disabled, "shows off, can be flipped")
assert(table.find(labels, "Bug detectors"), "its title")
assert(table.find(labels, "During gameplay, Autopilot watches for potential bugs and reports them. "
	.. "You can enable/disable this per-project."), "its words")
-- flipped: sent at once over the link, shown flipped until the next memory_state
s.onToggle(true)
local f = sent[#sent]
assert(f.type == "detectors" and f.enabled == true and ed.detectors == true, "flipping sends detectors")
-- (the same link as Reset memory's and Not a bug's)
ed:clearMemory()
assert(sent[#sent].type == "clear_memory", "clear_memory over the same link")
ed:feedback("teleport:Player", "not_a_bug")
assert(sent[#sent].type == "feedback", "feedback over the same link")
-- the agent service says it as saved: the memory_state sets it
ed:_onBrainFrame({ type = "memory_state", runs = 2, detectors = false })
assert(ed.detectors == false and card(d).on == false, "the memory_state corrects it")
ed:_onBrainFrame({ type = "memory_state", runs = 2, detectors = true })
assert(card(d).on == true, "and every memory_state sets it")
-- not connected: the last value, and it can't be changed
link.connected = false
local n = #sent
s = card(d)
assert(s.on == true and s.opts.disabled == true, "not connected: disabled, the last value")
assert(ed:setDetectors(false) == false and #sent == n and ed.detectors == true, "not connected: nothing sent")
-- mid-play (the mirror's dock): disabled
link.connected = true
d.mirror = true
assert(card(d).opts.disabled == true, "the mirror: disabled")
print("ok")
"""


def _wrapped(name, source):
    return f"local {name} = (function()\n{source}\nend)()\nmodules.{name} = {name}\n"


def test_the_bug_detectors_switch_in_luau(tmp_path):
    """The Editor and the Settings page themselves, run in Luau's CLI (when `luau` is on PATH, as in CI's Luau step):
    memory_state's "detectors" sets the switch (on without it), flipping it sends {"type": "detectors"} over the link
    as clear_memory and feedback go, the next memory_state corrects it, and it is disabled while not connected or
    mid-play."""
    exe = shutil.which("luau")
    if not exe:
        pytest.skip("no luau on PATH")
    sources = _sources()
    chunk = _LUAU_PRELUDE + _wrapped("Settings", sources["Settings"]) + _wrapped("Editor", sources["Editor"]) \
        + _wrapped("SettingsPage", sources["SettingsPage"]) + _LUAU_CHECKS
    path = tmp_path / "detectors_switch.luau"
    path.write_text(chunk, encoding="utf-8")
    r = subprocess.run([exe, str(path)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and r.stdout.strip().endswith("ok"), f"{r.stdout[-2000:]}{r.stderr[-2000:]}"
