"""The roblox-mcp engine (autopilot/engines/roblox_connector.py) without Roblox Studio: the place is
tests/robloxsim.py's, served by roblox_mcp.FakeStudio. Project detection, the kit frame, registration, the batched
observe (one execute_luau per game.batch), the session's restart, inputs, the console as a detector, the codex, the
Roblox section of the prompt, and the connector speaking the bridge's protocol to a real brain link. What runs in
Studio itself (roblox.luau's Luau) is checked only for its framing here."""

import json
import math
import os
import re
import threading
import time

import pytest
import robloxsim

from autopilot import engines
from autopilot.engines import roblox_connector as roblox
from autopilot.engines import roblox_mcp as mcp
from autopilot.kit import Vec, control, forward
from autopilot.kit.game import Game, GameError

HERE = os.path.dirname(os.path.abspath(__file__))
TESTPLACE = os.path.join(HERE, "..", "examples", "roblox_testplace")


@pytest.fixture
def place():
    return robloxsim.Place()


def connect(place, **kw):
    studio = mcp.Studio.connect(mcp.LocalTransport(place.server(**kw)))
    backend = roblox.StudioBackend(studio)
    link = roblox.LocalLink(backend)
    return Game(link), link, backend


@pytest.fixture
def playing(place):
    game, link, backend = connect(place)
    link.request("session", {"action": "start"})
    yield game, link, backend, place
    backend.close()


# -- registration and the project on disk ------------------------------------------------------------------------

def test_roblox_is_registered():
    eng = engines.get("roblox-mcp")
    assert isinstance(eng, roblox.RobloxMcp) and eng.name == "roblox-mcp" and eng.title == "Roblox"
    assert engines.ENGINES["roblox-mcp"] is eng
    assert engines.for_hello({"engine": "roblox-mcp"}) is eng
    assert engines.get("unreal").name == "unreal"  # (the default is as it was)
    from autopilot import bridge

    assert {"find", "transforms", "pose", "movement"} <= set(bridge.required_ops({"engine": "roblox-mcp"}))


def test_projects_rojo_and_place_files(tmp_path):
    rojo = tmp_path / "Obby"
    (rojo / "src" / "server").mkdir(parents=True)
    (rojo / "default.project.json").write_text(json.dumps({"name": "Obby Rush", "tree": {"$className": "DataModel"}}))
    for p in (rojo, rojo / "default.project.json"):
        eng, path = engines.find_project(str(p), "roblox-mcp")
        assert eng.name == "roblox-mcp" and os.path.samefile(path, rojo), p
    assert engines.get("roblox-mcp").project_name(str(rojo)) == "Obby Rush"
    places = tmp_path / "Places"
    places.mkdir()
    (places / "Other.rbxl").write_bytes(b"<roblox!")
    (places / "Places.rbxlx").write_text("<roblox>")
    eng, path = engines.find_project(str(places), "roblox-mcp")
    assert eng.name == "roblox-mcp" and path.endswith("Places.rbxlx")  # (the one named after the folder)
    eng, path = engines.find_project(str(places / "Other.rbxl"), "roblox-mcp")
    assert eng.name == "roblox-mcp" and path.endswith("Other.rbxl")
    assert engines.get("roblox-mcp").project_name(path) == "Other"
    assert engines.find_project(str(tmp_path / "Obby" / "src")) == (None, None)
    assert engines.find_project(str(rojo), "godot") == (None, None)
    # (the other engines' projects are still theirs)
    (tmp_path / "g").mkdir()
    (tmp_path / "g" / "project.godot").write_text("")
    assert engines.find_project(str(tmp_path / "g"))[0].name == "godot"


def test_the_test_place_is_a_roblox_project():
    eng, path = engines.find_project(TESTPLACE, "roblox-mcp")
    assert eng.name == "roblox-mcp" and eng.project_name(path) == "Autopilot Test Place"


def test_codex_rules():
    r = engines.get("roblox-mcp")
    for ok in ("default.project.json", "src/server/Main.server.luau", "src/client/Hud.client.lua",
               "src/shared/Config.luau", "src/shared/Data.json", "places/Level.rbxlx", "src/Model.rbxmx",
               "wally.toml", "aftman.toml"):
        assert r.codex_path(ok) == ok, ok
    assert r.codex_path(".autopilot/roblox_codex/tags.txt") == "tags.txt"
    script = "place/Workspace/Door.server.luau"
    assert r.codex_path(".autopilot/roblox_codex/" + script) == script
    for bad in ("Packages/roact/init.lua", ".git/x.lua", "node_modules/x.luau", "Game.rbxl", "x.png",
                ".autopilot/roblox_codex/README.md", "../x.luau", "/x.luau", "src\\x.luau", "", None):
        assert r.codex_path(bad) is None, bad
    assert roblox.script_file("ServerScriptService/Main", "Script") == "place/ServerScriptService/Main.server.luau"
    assert roblox.script_file("StarterGui/Hud/Coins", "LocalScript") == "place/StarterGui/Hud/Coins.client.luau"
    assert roblox.script_file("ReplicatedStorage/A\\/B@2", "ModuleScript") == "place/ReplicatedStorage/A_B@2.luau"


def test_local_codex_of_a_rojo_project(tmp_path, monkeypatch):
    from autopilot import codex

    monkeypatch.setattr(codex, "MEMORY", str(tmp_path / "mem"))
    monkeypatch.setenv("AUTOPILOT_ENGINE", "roblox-mcp")
    cx = codex.build("TestPlace", path=TESTPLACE)
    have = sorted(os.path.relpath(os.path.join(b, f), cx.root).replace(os.sep, "/")
                  for b, _, fs in os.walk(cx.root) for f in fs)
    assert "default.project.json" in have and "README.md" in have
    assert any(p.endswith("Debug.luau") for p in have) and any(p.endswith(".server.luau") for p in have)
    readme = open(os.path.join(cx.root, "README.md"), encoding="utf-8").read()
    assert "Roblox project" in readme and "A Rojo project" in readme and "ServerScriptService <-" in readme


# -- the kit frame ---------------------------------------------------------------------------------------------

def _roblox_forward(o):
    """Roblox's LookVector for Orientation (x, y) degrees: CFrame.fromOrientation(x, y, 0).LookVector (y then x)."""
    x, y = math.radians(o["x"]), math.radians(o["y"])
    return {"x": -math.cos(x) * math.sin(y), "y": math.sin(x), "z": -math.cos(x) * math.cos(y)}


def test_frame_round_trip_and_axes():
    r = engines.get("roblox-mcp")
    for v in (Vec(120, -35.5, 980), Vec(0, 0, 0), Vec(-1, 2, -3)):
        back = r.to_kit(r.to_native(v))
        assert max(abs(back.x - v.x), abs(back.y - v.y), abs(back.z - v.z)) < 1e-9
    # 1 stud = 28 cm, forward / right / up
    assert r.to_kit({"x": 0, "y": 0, "z": -1}) == Vec(28, 0, 0)
    assert r.to_kit({"x": 1, "y": 0, "z": 0}) == Vec(0, 28, 0)
    assert r.to_kit([0, 1, 0]) == Vec(0, 0, 28)
    assert r.to_native(Vec(280, 0, 0)) == {"x": 0.0, "y": 0.0, "z": -10.0}
    for yaw in (-170, -90, -30, 0, 45, 90, 135, 180):
        for pitch in (-60, -10, 0, 25, 80):
            o = r.rotation_to_native(yaw, pitch)
            ky, kp = r.rotation_to_kit(o)
            assert abs(((ky - yaw + 180) % 360) - 180) < 1e-6 and abs(kp - pitch) < 1e-6
            # Roblox's look for that orientation, in the kit's frame, is the kit's forward for that yaw and pitch
            f = roblox.kit_dir(_roblox_forward(o))
            want = forward(yaw, pitch)
            assert max(abs(f.x - want.x), abs(f.y - want.y), abs(f.z - want.z)) < 1e-9, (yaw, pitch)
            assert roblox.yaw_pitch(f) == pytest.approx((wrap180(yaw), pitch), abs=1e-6)
    assert r.rotation_to_kit({"x": 10, "y": 90, "z": 0}) == (-90, 10)  # (orientation.y turns left)
    assert r.rotation_to_kit({"x": 0, "y": 0, "z": 0, "orientation": {"x": 0, "y": -45, "z": 0}}) == (45, 0)
    # Roblox is right-handed (as Godot): a mirror of the kit's frame
    x, y, z = (r.to_kit(v) for v in ([1, 0, 0], [0, 1, 0], [0, 0, 1]))
    assert x.x * (y.y * z.z - y.z * z.y) - x.y * (y.x * z.z - y.z * z.x) + x.z * (y.x * z.y - y.y * z.x) < 0
    assert roblox.kit_extent({"x": 4, "y": 10, "z": 2}) == Vec(28, 56, 140)


def wrap180(a):
    return (a + 180) % 360 - 180 if a != 180 else 180


def test_movement_from_the_humanoid():
    m = roblox.movement_in_kit({"walk_speed": 16, "use_jump_power": False, "jump_height": 7.2, "jump_power": 50,
                                "hip_height": 2, "root_half": 1, "radius": 1, "gravity": 196.2, "max_slope": 89})
    assert m["walk_speed"] == 16 * 28 and m["gravity"] == pytest.approx(196.2 * 28)
    assert m["jump_z"] == pytest.approx(math.sqrt(2 * 196.2 * 7.2) * 28)
    assert m["jump_z"] ** 2 / (2 * m["gravity"]) == pytest.approx(7.2 * 28)  # (the peak is JumpHeight)
    assert m["half_height"] == 3 * 28 and m["radius"] == 28 and m["step"] is None and m["air_control"] == 1.0
    assert roblox.movement_in_kit({"use_jump_power": True, "jump_power": 50, "gravity": 196.2})["jump_z"] == 50 * 28


# -- reads: one execute_luau per batch -----------------------------------------------------------------------------

def test_a_batched_observe_is_one_execute_luau(playing):
    game, link, backend, place = playing
    coin = game.actors("tag:Coin")[0]
    # (reads that come in together wait COALESCE_SECONDS for one another: live, a few ms is enough, and one that comes
    # later only costs another round trip. On a loaded test machine a thread can start later than that, so the window
    # is widened here: this checks that reads asked together go as one call, not how fast threads start)
    backend.reads.window = 0.25
    place.calls.clear()
    pose, coin_at, coins, doors, gone = game.batch([
        lambda: game.pose(), lambda: game.loc(coin), lambda: game.get("Players/Hero/leaderstats", "Coins"),
        lambda: game.actors("tag:Door"), lambda: game.get("Workspace/Nothing", "Open")])
    calls = place.luau_calls()
    assert len(calls) == 1, calls
    assert calls[0][1] == "Client" and sorted(calls[0][2]) == ["find", "get", "get", "pose", "transforms"]
    assert pose["at"] == Vec(0, 0, 84) and pose["yaw"] == 0 and pose["health"] == 100
    assert coin_at == Vec(560, 280, 56) and coins == 0 and doors == ["Workspace/Door"]
    assert isinstance(gone, GameError) and "does not exist" in str(gone)


def test_the_chunk_carries_the_prelude_and_a_json_request():
    code = roblox.luau_chunk([("pose", {}), ("get", {"path": "A/B", "prop": "Name ]] x", "none": None})], "Workspace/X",
                             "@@n@@")
    assert code.startswith(roblox.prelude()) and "local function RUN(REQ)" in code
    assert code.rstrip().endswith("))")
    req = robloxsim.req_of(code)
    assert req["ops"] == [["pose", {}], ["get", {"path": "A/B", "prop": "Name ]] x"}]]  # (nulls left out)
    assert req["player"] == "Workspace/X" and req["nonce"] == "@@n@@" and req["echo"] is False
    assert roblox.long_string("a]]b]=]c") == "[==[a]]b]=]c]==]"
    # what HttpService:JSONEncode returns comes back however the tool wraps it
    ans = {"t": 1.5, "r": [{"ok": True, "v": 1}]}
    for text in (json.dumps(ans), "Returned: " + json.dumps(ans), json.dumps(json.dumps(ans)),
                 "log line\n@@n@@" + json.dumps(ans) + "@@n@@\nmore", "{not json} " + json.dumps(ans)):
        assert roblox.parse_answer(text, "@@n@@") == ans, text
    assert roblox.parse_answer("nothing here", "@@n@@") is None


def test_a_server_that_returns_only_printed_output_is_read_by_printing(playing):
    game, link, backend, place = playing
    place.mode = "print"
    backend.echo = False
    assert game.pose()["at"] == Vec(0, 0, 84)
    assert backend.echo is True  # (from then on, every chunk prints)
    assert game.loc("Workspace/Coin") == Vec(560, 280, 56)


def test_reads_writes_and_calls(playing):
    game, link, backend, place = playing
    assert game.to_kit(game.get("Workspace/Door", "Position")) == Vec(1120, 0, 140)
    assert game.rot("Workspace/Door")["Yaw"] == -90
    center, ext = game.bounds("Workspace/Door")
    assert center == Vec(1120, 0, 140) and ext == Vec(14, 112, 140)
    place.calls.clear()
    game.set("Workspace/Door", "Open", True, why="test")
    assert place.luau_calls()[-1][1] == "Server"  # (writes go to the server's copy)
    assert game.get("Workspace/Door", "Open") is True and game.writes[-1]["why"] == "test"
    r = game.call("ServerScriptService/Debug", "GiveCoin", args=[2])
    assert r["ReturnValue"] == 2 and game.get("Players/Hero/leaderstats", "Coins") == 2
    assert game.screen() == {"Hud.Coins": "Coins: 2"}
    with pytest.raises(GameError, match="does not exist"):
        game.loc("Workspace/Gone")
    assert not game.exists("Workspace/Gone") and game.exists("Workspace/Coin")
    assert game.named("lever") == ["Workspace/Lever"]
    assert game.name_of("server:Workspace/Obby/Checkpoint@1") == "Checkpoint@1"
    m = control.movement(game)
    assert m["walk_speed"] == 448 and m["peak"] == pytest.approx(7.2 * 28)


def test_reads_name_the_data_model(playing):
    game, link, backend, place = playing
    place.calls.clear()
    game.get("server:Workspace/Door", "Open")
    game.get("edit:Workspace/Door", "Open")
    assert [c[1] for c in place.luau_calls()] == ["Server", "Edit"]


def test_view_and_sonar_are_in_the_kit_frame(playing):
    game, link, backend, place = playing
    v = game.view(sonar=True, max_distance=2800)
    coin = v["visible"][0]
    assert coin["name"] == "Coin" and coin["center"] == {"X": 560, "Y": 280, "Z": 56}
    assert coin["distance"] == pytest.approx(22.4 * 28) and coin["tags"] == ["Coin"]
    way = v["body"]["sonar"]["ways"][0]
    assert way["clear"] == 20 * 28 and way["by"] == "wall" and way["normal"] == {"X": -1.0, "Y": 0.0, "Z": 0.0}
    assert "held" in v["omitted"]
    assert control.sonar(game)[0]["clear"] == 560


# -- inputs --------------------------------------------------------------------------------------------------------

def test_held_keys_walk_and_are_let_go(playing):
    game, link, backend, place = playing
    game.hold("W")
    for _ in range(6):
        time.sleep(0.1)
        game.renew()
    game.release("W")
    backend.wait_for_inputs()
    at = game.pose()["at"]
    assert at.x > 100 and abs(at.y) < 1e-6  # (forward)
    kb = [c[2] for c in place.calls if c[0] == mcp.USER_KEYBOARD_INPUT]
    assert kb[0] == [{"action": "keyDown", "key_code": "W"}] and kb[-1] == [{"action": "keyUp", "key_code": "W"}]
    assert len(kb) == 2  # (renewals extend the lease: no calls)
    game.press("SpaceBar")
    backend.wait_for_inputs()
    assert [c[2] for c in place.calls if c[0] == mcp.USER_KEYBOARD_INPUT][-1] == [
        {"action": "keyPress", "key_code": "Space"}]


def test_a_hold_not_renewed_is_let_go(playing):
    game, link, backend, place = playing
    game.hold("D")
    time.sleep(1.2)  # (no renew: the lease of 0.6 s runs out)
    assert "D" not in place.held and not backend.held


def test_the_mouse_turns_the_camera_and_calibrates(playing):
    game, link, backend, place = playing
    game.mouse(right=40)
    backend.wait_for_inputs()
    yaw, _ = game.view_rotation()
    assert yaw == pytest.approx(40 * roblox.DEGREES_PER_COUNT, abs=1e-6)  # (right)
    # (the camera is turned in the player's client with the turn op: Studio's MCP right-drag doesn't reach it)
    assert (mcp.EXECUTE_LUAU, "Client", ["turn"]) in place.calls
    assert not [c for c in place.calls if c[0] == mcp.USER_MOUSE_INPUT]


def test_mouse_buttons_and_the_wheel_carry_a_position(playing):
    """Studio's MCP mouse needs a position in every call (a button let go in a call of its own too), has no middle
    button, and scrolls a notch an action."""
    game, link, backend, place = playing
    game.hold("RightMouseButton")
    backend.wait_for_inputs()
    game.release("RightMouseButton")
    backend.wait_for_inputs()
    game.press("MouseScrollDown", value=3)
    backend.wait_for_inputs()
    mouse = [c[2] for c in place.calls if c[0] == mcp.USER_MOUSE_INPUT]
    assert mouse[0] == [{"action": "mouseButtonDown", "mouse_button": "right", "x": 640, "y": 360}]
    assert mouse[1] == [{"action": "mouseButtonUp", "mouse_button": "right", "x": 640, "y": 360}]
    assert mouse[2] == [{"action": "scrollDown", "x": 640, "y": 360}] * 3
    with pytest.raises(Exception, match="no middle button"):
        backend.op_key({"key": "MiddleMouseButton", "mode": "press"})


def test_pointer_clicks_ui_and_navigation(playing):
    game, link, backend, place = playing
    t0 = time.monotonic()
    game.pointer(world=Vec(560, 280, 56))  # (an input whose own read must not wait behind itself)
    assert time.monotonic() - t0 < 2
    p = game.pointer(0.5, 0.5)
    assert p["x"] == pytest.approx(960) and p["on_screen"]
    game.press("LeftMouseButton")
    backend.wait_for_inputs()
    click = [c[2] for c in place.calls if c[0] == mcp.USER_MOUSE_INPUT][-1]
    assert click == [{"action": "mouseButtonClick", "mouse_button": "left", "x": 960, "y": 180}]
    game.pointer(world=Vec(560, 280, 56))
    assert backend.pointer_at == (800, 300)
    assert game.ui()[0]["label"] == "Reset"
    game.ui_act(id="Players/Hero/PlayerGui/Hud/Reset")
    assert [c[2] for c in place.calls if c[0] == mcp.USER_MOUSE_INPUT][-1] == [
        {"action": "moveTo", "instance_path": "game.Players.Hero.PlayerGui.Hud.Reset"},
        {"action": "mouseButtonClick", "mouse_button": "left"}]
    game.request("move", {"to": Vec(280, 560, 84).ue()})
    assert place.calls[-1] == (mcp.CHARACTER_NAVIGATION, None, {
        "datamodel_type": "Client", "x": 20.0, "y": 3.0, "z": -10.0, "studio_id": mcp.FAKE_STUDIO})


def test_watch_sees_a_thing_move(playing):
    game, link, backend, place = playing

    def nudge():
        time.sleep(0.2)
        place.things["Workspace/Coin"]["at"] = [10, 2, -40]

    threading.Thread(target=nudge, daemon=True).start()
    out = game.request("watch", {"objects": ["Workspace/Coin", "Workspace/Door"], "seconds": 1.2})
    assert [(c["object"], c["what"]) for c in out["changes"]] == [("Workspace/Coin", "moved")]


def test_no_named_actions_and_no_pause(playing):
    game, link, backend, place = playing
    assert "why" in game.pause(True)
    with pytest.raises(GameError, match="no named input actions"):
        game.request("input", {"action": "Jump", "mode": "press"})


# -- the session ---------------------------------------------------------------------------------------------------

def test_restart_is_stop_then_start(place):
    game, link, backend = connect(place)
    assert link.request("session", {"action": "status"})["running"] is False
    assert game.restart() is True
    plays = [c[2] for c in place.calls if c[0] == mcp.START_STOP_PLAY]
    assert plays == [True]  # (not running: just started)
    game.hold("W")
    time.sleep(0.3)
    game.release("W")
    backend.wait_for_inputs()
    assert game.pose()["at"].x > 0
    game.hold("A")
    backend.wait_for_inputs()
    place.calls.clear()
    assert game.restart() is True
    plays = [c[2] for c in place.calls if c[0] == mcp.START_STOP_PLAY]
    assert plays == [False, True]  # (stop, then start)
    assert game.pose()["at"] == Vec(0, 0, 84)  # (everything starts over)
    assert not backend.held and "A" not in place.held  # (keys let go first)
    assert link.request("session", {"action": "status"})["running"] is True
    link.request("session", {"action": "stop"})
    assert link.request("session", {"action": "status"})["running"] is False
    with pytest.raises(GameError, match="plays the place open in Studio"):
        game.restart(map="SomeOtherPlace")
    assert link.request("levels") == {"levels": ["TestPlace"], "open": "TestPlace", "playing": ""}


def test_the_hello_names_the_place_studio_has_open(place):
    game, link, backend = connect(place)
    assert link.hello["project"] == "TestPlace" and link.hello["engine"] == "roblox-mcp"  # (game.Name, in Edit)
    studio = mcp.Studio.connect(mcp.LocalTransport(place.server()))
    rojo = roblox.LocalLink(roblox.StudioBackend(studio, project=os.path.abspath(TESTPLACE)))
    assert rojo.hello["project"] == "Autopilot Test Place"  # (a Rojo project's own name)


def test_studio_state_is_read_when_the_connector_doesnt_know(place):
    place.running = True
    game, link, backend = connect(place)
    assert backend.is_running() is True
    place2 = robloxsim.Place()
    game2, link2, backend2 = connect(place2, without=[mcp.GET_STUDIO_STATE])
    assert backend2.is_running() is False  # (the Client data model isn't there)


# -- the console as a detector ---------------------------------------------------------------------------------

def test_the_whole_console_can_be_kept_in_a_file(place, tmp_path):
    game, link, backend = connect(place)
    backend.console_file = str(tmp_path / "console.txt")
    link.request("session", {"action": "start"})
    backend.poll_console()
    place.console.append('[Gauntlet] {"event": "death"}')
    backend.poll_console()
    lines = (tmp_path / "console.txt").read_text().splitlines()
    assert lines == ["Hero joined the game", "ServerScriptService.Broken:3: attempt to index nil with 'Value'",
                     '[Gauntlet] {"event": "death"}']  # (every line, not just the errors the brain is sent)
    backend.close()


def test_console_errors_are_log_events_and_findings(playing):
    game, link, backend, place = playing
    seen = []
    link.listeners.append(seen.append)
    assert backend.poll_console() == 2
    logs = [e for e in seen if e["kind"] == "log"]
    assert len(logs) == 1 and logs[0]["verbosity"] == "Error" and "attempt to index nil" in logs[0]["message"]
    assert link.findings[0]["detector"] == "log_error" and "Broken:3" in link.findings[0]["summary"]
    place.console.append("ServerScriptService.Broken:3: attempt to index nil with 'Value'")
    place.console.append("Infinite yield possible on 'Workspace:WaitForChild(\"Gate\")'")
    assert backend.poll_console() == 2
    assert len(link.findings) == 1  # (the same error again: one finding)
    assert [e["verbosity"] for e in seen if e["kind"] == "log"] == ["Error", "Error", "Warning"]
    assert backend.poll_console() == 0
    assert roblox.console_level("Hero joined the game") is None
    assert roblox.console_level("Workspace.Door.Script:12: Stack Begin") == "Error"
    assert engines.get("roblox-mcp").error_log is True


def test_the_runtime_traces_console_errors(playing, tmp_path):
    from autopilot import runtime
    from autopilot.kit import Harness, Option, Status

    game, link, backend, place = playing

    class H(Harness):
        goal = "stand"
        tempo = 4.0
        tick_hz = 10.0

        def observe(self, game):
            backend.poll_console()
            return {"_pose": game.pose()}

        def options(self, obs):
            return [Option("wait", "wait", lambda g, o: (x for x in range(3)))]

        def status(self, obs):
            return Status("done", "ok") if game.t > 0.5 else Status()

    class Jev:
        def system_one(self, state, questions):
            import types

            crit = list(questions["pick"].criteria)
            return types.SimpleNamespace(choices={"pick": types.SimpleNamespace(
                choice=crit[0], probabilities={crit[0]: 1.0}, confidence=1.0)}, nouls={}, scores={})

    rt = runtime.Runtime(game, H(), str(tmp_path / "t.jsonl"), client=Jev(), max_seconds=10)
    rt.run()
    text = runtime.summarize(str(tmp_path / "t.jsonl"), error_log=True)
    assert "attempt to index nil" in text


# -- pictures ------------------------------------------------------------------------------------------------------

def test_capture_is_a_jpeg_still(playing):
    import base64

    game, link, backend, place = playing
    shot = link.request("capture", {"max_dim": 640, "quality": 80})
    jpeg = base64.b64decode(shot["jpeg"])
    assert jpeg[:3] == b"\xff\xd8\xff" and shot["width"] == 2 and shot["height"] == 2
    assert "media" in link.hello["features"]
    from autopilot.bridge import BridgeError

    with pytest.raises(BridgeError, match="only stills"):
        link.request("record", {"action": "start"})
    game2, link2, _ = connect(robloxsim.Place(), without=[mcp.SCREEN_CAPTURE])
    assert "media" not in link2.hello["features"]


# -- the codex -----------------------------------------------------------------------------------------------------

def test_codex_from_the_live_place(playing, tmp_path):
    from autopilot.codex import fetch_from_plugin

    game, link, backend, place = playing
    root = tmp_path / "codex"
    root.mkdir()
    stats = fetch_from_plugin(game, str(root))
    have = sorted(os.path.relpath(os.path.join(b, f), root).replace(os.sep, "/") for b, _, fs in os.walk(root)
                  for f in fs if f != ".fetched.json")
    assert have == ["place/ServerScriptService/Debug.luau", "place/ServerScriptService/Main.server.luau",
                    "place/StarterPlayer/StarterPlayerScripts/Hud.client.luau", "summary.txt", "tags.txt",
                    "tree.txt"], have
    assert stats["fetched"] == 6
    assert (root / "place/ServerScriptService/Debug.luau").read_text() == "return {GiveCoin = function(n) end}"
    tags = (root / "tags.txt").read_text()
    assert "Coin (1):" in tags and "Workspace/Coin [Part] at (10.0, 2.0, -20.0)" in tags
    assert "ServerScriptService/Main (Script)" in (root / "summary.txt").read_text()
    assert "game.Workspace.Coin" in (root / "tree.txt").read_text()
    # unchanged scripts aren't read again
    reads = sum(1 for c in place.calls if c[0] == mcp.SCRIPT_READ)
    stats = fetch_from_plugin(game, str(root))
    assert sum(1 for c in place.calls if c[0] == mcp.SCRIPT_READ) == reads and stats["fetched"] == 0
    engines.get("roblox-mcp").write_index(str(root), "TestPlace")
    readme = (root / "README.md").read_text()
    assert "tags.txt" in readme and "*.server.luau" in readme


def test_codex_of_a_rojo_project_comes_from_disk(place):
    from autopilot.codex import fetch_from_plugin

    studio = mcp.Studio.connect(mcp.LocalTransport(place.server()))
    backend = roblox.StudioBackend(studio, project=os.path.abspath(TESTPLACE))
    game = Game(roblox.LocalLink(backend))
    import tempfile

    with tempfile.TemporaryDirectory() as root:
        fetch_from_plugin(game, root)
        assert os.path.exists(os.path.join(root, "default.project.json"))
        assert os.path.exists(os.path.join(root, "tags.txt"))
        assert not os.path.isdir(os.path.join(root, "place"))  # (the scripts are the project's own files)
    assert not any(c[0] == mcp.SCRIPT_READ for c in place.calls)
    assert game.context()["project_dir"] == os.path.abspath(TESTPLACE)


# -- the prompt ----------------------------------------------------------------------------------------------------

def test_prompt_roblox_section():
    from autopilot.agents import session

    text = session.system_prompt(engines.get("roblox-mcp"), "runs/r")
    assert "a video game made in Roblox." in text and "## The engine: Roblox" in text
    head = text[:text.index("## The engine: Roblox")]
    for unreal_name in ("Blueprint", "/Game/", "UMG", "Enhanced Input", "MaxStepHeight", "CharacterMovement"):
        assert unreal_name not in head, unreal_name
    section = engines.get("roblox-mcp").kit_section()
    assert section.strip() and section in text
    for needle in ("tag:", "CollectionService", "RemoteEvent", "server:", "client:", "game.restart()", "28 cm",
                   "stills", "tempo", "roblox_latency.py", "execute_luau", "HumanoidRootPart", "omitted"):
        assert needle in section, needle
    # nothing of a particular game (the test place's names stay out of the brain)
    for word in ("Obby", "Coin", "Lever", "Door", "Checkpoint", "GiveCoin", "TestPlace"):
        assert not re.search(rf"\b{word}\b", section), word


def test_launch_is_the_connector(tmp_path):
    cmd = engines.get("roblox-mcp").launch_command(TESTPLACE, "ws://127.0.0.1:9/v1/bridge", "g.log", exe="py")
    assert cmd[:4] == ["py", "-m", "autopilot.engines.roblox_connector", "connect"]
    assert cmd[cmd.index("--url") + 1] == "ws://127.0.0.1:9/v1/bridge" and "--project" in cmd


# -- the connector on the bridge ------------------------------------------------------------------------------------

def test_the_connector_plays_over_the_bridge(place):
    """The connector says hello as a plugin does, and a brain's Link (serve_local) plays the place through it: reads
    in one call per batch, inputs, events (the console's errors as findings)."""
    from websockets.sync.client import connect as ws_connect

    from autopilot import bridge

    studio = mcp.Studio.connect(mcp.LocalTransport(place.server()))
    backend = roblox.StudioBackend(studio)
    backend.reads.window = 0.25  # (see test_a_batched_observe_is_one_execute_luau: not how fast threads start)
    port_box = {}
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    t = threading.Thread(target=lambda: port_box.__setitem__("link", bridge.serve_local(port=port, wait=20)),
                         daemon=True)
    t.start()
    time.sleep(0.5)
    with ws_connect(f"ws://127.0.0.1:{port}/v1/bridge", max_size=None) as ws:
        worker = threading.Thread(target=roblox.serve_connection, args=(ws, backend, "TestPlace"),
                                  kwargs={"log": print}, daemon=True)
        worker.start()
        t.join(20)
        played_over_the_bridge(port_box["link"], place)


def played_over_the_bridge(link, place):
    from autopilot import bridge

    try:
        assert link.hello["engine"] == "roblox-mcp" and link.hello["project"] == "TestPlace"
        assert link.missing_ops() == []
        game = Game(link)
        assert game.engine.name == "roblox-mcp"
        link.request("session", {"action": "start"}, timeout=30)
        place.calls.clear()
        pose, coins = game.batch([lambda: game.pose(), lambda: game.get("Players/Hero/leaderstats", "Coins")])
        assert pose["at"] == Vec(0, 0, 84) and coins == 0 and len(place.luau_calls()) == 1
        game.press("E")
        pressed = lambda: any(c[0] == mcp.USER_KEYBOARD_INPUT for c in place.calls)  # noqa: E731
        deadline = time.time() + 10
        while time.time() < deadline and not (link.findings and pressed()):
            time.sleep(0.1)
        assert link.findings and link.findings[0]["detector"] == "log_error"
        assert pressed() and not link.async_errors
        with pytest.raises(bridge.BridgeError) as e:
            link.request("rc", {"verb": "property", "body": {"objectPath": "Workspace/Nope", "propertyName": "X"}})
        assert e.value.status == 404
    finally:
        link.server.shutdown()


# -- the test place and its bench suite ---------------------------------------------------------------------------

def test_the_test_place_tags_what_the_suite_asks_about():
    """The example place's objects are found by the tags the suite's prompts need, and its debug module has the
    helpers its README names."""
    src = os.path.join(TESTPLACE, "src")
    config = open(os.path.join(src, "shared", "Config.luau"), encoding="utf-8").read()
    for tag in ("Checkpoint", "Coin", "Door", "Lever", "Hazard"):
        assert f'{tag} = "{tag}"' in config, tag
    debug = open(os.path.join(src, "server", "Debug.luau"), encoding="utf-8").read()
    for fn in ("GiveCoin", "SetPosition", "SetStage", "Reset"):
        assert f"function Debug.{fn}(" in debug, fn
    mapped = dict(roblox.rojo_mappings(os.path.join(TESTPLACE, "default.project.json")))
    assert mapped["ServerScriptService"] == "src/server" and mapped["ReplicatedStorage"] == "src/shared"


def test_the_bench_suite_is_in_the_bench_format():
    with open(os.path.join(HERE, "..", "bench", "suites", "roblox_testplace.json"), encoding="utf-8") as f:
        suite = json.load(f)
    prompts = suite["prompts"]
    assert len(prompts) >= 6 and len({p["id"] for p in prompts}) == len(prompts)
    for p in prompts:
        assert p["prompt"].strip() and p.get("expected") in (None, "CONFIRMED", "DENIED", "INCONCLUSIVE", "achieved",
                                                             "not_achieved")
    words = " ".join(p["prompt"].lower() for p in prompts)
    assert "lever" in words and "coin" in words and "final checkpoint" in words


def test_the_latency_tool_runs_against_the_fake(capsys):
    import importlib.util

    spec = importlib.util.spec_from_file_location("roblox_latency", os.path.join(HERE, "..", "tools",
                                                                                 "roblox_latency.py"))
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    assert tool.main(["--fake", "--calls", "10"]) == 0
    out = capsys.readouterr().out
    assert "p50" in out and "p95" in out and "tempo <=" in out and "n=10" in out
    assert tool.recommend(100, 150) == (6.7, 3.3)  # (150 ms: about 3 decisions a second, too slow for reflexes)
    assert tool.recommend(10, 20) == (30.0, 8.0)
    assert tool.percentile([5, 1, 4, 2, 3], 50) == 3


def test_the_connector_says_why_it_cant_start(monkeypatch, capsys):
    import sys

    fake = f'"{sys.executable}" -m autopilot.engines.roblox_mcp --fake --without execute_luau'
    monkeypatch.setenv(mcp.ENV_CMD, fake)
    assert roblox.main(["connect", "--url", "ws://127.0.0.1:9/v1/bridge", "--retry", "0"]) == 2
    assert "doesn't offer execute_luau" in capsys.readouterr().out


def test_the_luau_prelude_ships_with_the_package():
    """An installed Autopilot reads a place with engines/roblox.luau: it is package data, as the engines' .md are."""
    with open(os.path.join(HERE, "..", "pyproject.toml"), encoding="utf-8") as f:
        assert '"engines/*.luau"' in f.read()
    assert os.path.isfile(roblox.PRELUDE_FILE)


def test_roblox_mcp_beside_the_studio_plugin_engine(monkeypatch, tmp_path):
    """The Studio plugin's engine is registered first and claims Roblox places too, so a place goes to it, and
    AUTOPILOT_ENGINE=roblox-mcp chooses the connector; a plugin's hello says which engine it is either way."""
    rojo = tmp_path / "Obby"
    rojo.mkdir()
    (rojo / "default.project.json").write_text(json.dumps({"name": "Obby", "tree": {"$className": "DataModel"}}))
    assert engines.find_project(str(rojo))[0].name == "roblox"
    monkeypatch.setenv("AUTOPILOT_ENGINE", "roblox-mcp")
    eng, path = engines.find_project(str(rojo))
    assert eng.name == "roblox-mcp" and os.path.samefile(path, rojo)
    monkeypatch.setenv("AUTOPILOT_ENGINE", "no-such-engine")  # (ignored)
    assert engines.find_project(str(rojo))[0].name == "roblox"
    assert engines.find_project(str(rojo), "godot") == (None, None)  # (an engine asked for by name still wins)
    assert engines.for_hello({"engine": "roblox-mcp"}).name == "roblox-mcp"
    assert engines.for_hello({"engine": "roblox"}).name == "roblox"
