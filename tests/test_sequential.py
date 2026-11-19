"""Verifications one after another in one game (start_run's keep_game_open) and headless runs (start_run's headless):
the agent service keeps the game open between the runs and restarts in place; `autopilot mcp` starts a headless agent
service of its own."""

import json
import os
import queue
import sys
import threading
import time
import types

import pytest

from autopilot import cli, mcp
from autopilot.kit.game import Game

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Plugin:
    """Enough of a link for the serve loop: what it was sent and asked, and the frames it says."""

    def __init__(self):
        self.hello = {"project": "Farm", "host": "editor", "features": []}
        self.sent, self.asked, self.rebound = [], [], []
        self.current = self
        self.messages = queue.Queue()
        self.done = threading.Event()

    def send(self, frame):
        self.sent.append(frame)

    def status(self, text):
        self.sent.append({"type": "status", "text": text})

    def request(self, op, args=None, timeout=None):
        self.asked.append((op, dict(args or {})))
        return {"running": True}

    def next_message(self, timeout=None):
        if self.done.is_set():
            raise KeyboardInterrupt
        try:
            return self.messages.get(timeout=0.05)
        except queue.Empty:
            return None


class Desk:
    def __init__(self):
        self.client, self.ended = "claude-code", None


@pytest.fixture
def serve(monkeypatch):
    """cmd_serve in a thread with a fake run_prompt: (link, control, runs, closed). Each run "reports" at once (its
    desk ended) and records what run_prompt was asked."""
    runs, closed = [], []

    def fake_run(link, prompt, *a, desk=None, stop=None, keep_game_open=False, restart_in_place=False, **k):
        runs.append({"prompt": prompt, "keep_game_open": keep_game_open, "restart_in_place": restart_in_place})
        if desk is not None:
            desk.ended = "reported"
        if not keep_game_open or (stop and stop()):
            cli.close_game(link)
        return 0

    monkeypatch.setattr(cli, "run_prompt", fake_run)
    monkeypatch.setattr(cli, "send_memory_state", lambda *a, **k: None)
    monkeypatch.setattr(cli, "load_user_keys", lambda: None)
    monkeypatch.setattr(cli.account, "send_state", lambda *a, **k: None)
    monkeypatch.setattr(cli.account, "SignIn", lambda link: None)
    monkeypatch.setattr(cli.account, "follow_preferences", lambda: None)
    monkeypatch.setattr(cli, "decision_key_missing", lambda: None)
    real_close = cli.close_game
    monkeypatch.setattr(cli, "close_game", lambda link: (closed.append(time.time()), real_close(link)))
    link = Plugin()
    control = types.SimpleNamespace(starter=None)
    args = types.SimpleNamespace(path=None, model=None, control=control)
    thread = threading.Thread(target=lambda: pytest.raises(KeyboardInterrupt, cli.cmd_serve, link, args),
                              daemon=True)
    thread.start()
    deadline = time.time() + 5
    while control.starter is None and time.time() < deadline:
        time.sleep(0.01)
    yield link, control, runs, closed
    link.done.set()
    thread.join(5)


def start(control, **params):
    refused = control.starter({"kind": "verify", **params}, Desk())
    assert refused is None, refused


def wait_for(cond, seconds=5):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_verifications_one_after_another_play_in_one_game_closed_after_the_last(serve):
    link, control, runs, closed = serve
    start(control, request="Verify that reload works", keep_game_open=True)
    assert wait_for(lambda: len(runs) == 1)
    start(control, request="Verify that jump works", keep_game_open=True)  # (waits for the first to finish)
    assert wait_for(lambda: len(runs) == 2)
    start(control, request="Check whether you can switch weapons")
    assert wait_for(lambda: len(runs) == 3) and wait_for(lambda: len(closed) == 1)
    assert [r["keep_game_open"] for r in runs] == [True, True, False]
    # every run of the batch restarts in the game playing, the last one too (the game was kept open for it)
    assert [r["restart_in_place"] for r in runs] == [True, True, True]
    assert [a for op, a in link.asked if op == "session"] == [{"action": "stop"}]  # (once: after the last)


def test_a_run_without_keep_game_open_closes_its_game_and_the_next_starts_afresh(serve):
    link, control, runs, closed = serve
    start(control, request="Verify that reload works")
    assert wait_for(lambda: len(closed) == 1)
    start(control, request="Verify that jump works")
    assert wait_for(lambda: len(runs) == 2) and wait_for(lambda: len(closed) == 2)
    assert [r["restart_in_place"] for r in runs] == [False, False]


def test_a_game_kept_open_closes_when_no_run_comes(serve, monkeypatch):
    monkeypatch.setenv("AUTOPILOT_KEEP_OPEN_SECONDS", "0.2")
    link, control, runs, closed = serve
    start(control, request="Verify that reload works", keep_game_open=True)
    assert wait_for(lambda: len(runs) == 1)
    assert not closed
    assert wait_for(lambda: len(closed) == 1, seconds=5)
    start(control, request="Verify that jump works")  # (the game was closed: this one starts it afresh)
    assert wait_for(lambda: len(runs) == 2)
    assert runs[1]["restart_in_place"] is False


def test_the_tabs_stop_between_runs_closes_the_game_kept_open(serve):
    link, control, runs, closed = serve
    start(control, request="Verify that reload works", keep_game_open=True)
    assert wait_for(lambda: len(runs) == 1)
    time.sleep(0.2)
    link.messages.put({"type": "stop"})
    assert wait_for(lambda: len(closed) == 1)


def test_keep_game_open_is_for_verifications_and_not_with_parallel(serve):
    link, control, runs, closed = serve
    refused = control.starter({"request": "Kill the boss", "kind": "goal", "keep_game_open": True}, Desk())
    assert "for verifications" in refused
    refused = control.starter({"request": "Verify that jump works", "kind": "verify", "keep_game_open": True,
                               "parallel": 2}, Desk())
    assert "one or the other" in refused
    assert not runs


def test_a_headless_service_keeps_its_one_game_for_every_run_of_any_kind(serve, monkeypatch):
    link, control, runs, closed = serve
    # (the fixture's serve isn't headless: a headless one is started here, with a game process that stays)
    link2 = Plugin()
    link2.hello = {"project": "Farm", "host": "game", "run": {"kind": "goal", "goal": "autopilot"}}
    control2 = types.SimpleNamespace(starter=None)
    game = types.SimpleNamespace(poll=lambda: None)
    args = types.SimpleNamespace(path="Farm.uproject", model=None, control=control2, headless=True,
                                 game_process=game)
    thread = threading.Thread(target=lambda: pytest.raises(KeyboardInterrupt, cli.cmd_serve, link2, args),
                              daemon=True)
    thread.start()
    assert wait_for(lambda: control2.starter is not None)
    time.sleep(0.2)
    assert not runs  # (its command line's run is only how the game connects)
    assert control2.starter({"request": "Kill the boss", "kind": "goal"}, Desk()) is None
    assert wait_for(lambda: len(runs) == 1)
    assert runs[0]["keep_game_open"] is True and runs[0]["restart_in_place"] is True
    link2.done.set()
    thread.join(5)


def test_a_headless_service_ends_with_its_game(monkeypatch):
    monkeypatch.setattr(cli, "send_memory_state", lambda *a, **k: None)
    monkeypatch.setattr(cli.account, "send_state", lambda *a, **k: None)
    monkeypatch.setattr(cli.account, "SignIn", lambda link: None)
    link = Plugin()
    args = types.SimpleNamespace(path="Farm.uproject", model=None, control=None, headless=True,
                                 game_process=types.SimpleNamespace(poll=lambda: 1))
    assert cli.cmd_serve(link, args) == 1


def test_a_restart_in_place_says_so_to_the_plugin():
    asked = []
    link = types.SimpleNamespace(hello={"engine": "unreal", "features": []}, post=lambda op, args=None: None,
                                 request=lambda op, args=None, timeout=None: asked.append((op, dict(args))) or {})
    game = Game(link)
    game.context = lambda fresh=False: {"character": "BP_Hero_C_0"}
    game.restart()
    game.restart_in_place = True
    game.restart(map="/Game/Maps/Arena")
    sessions = [a for op, a in asked if op == "session"]
    assert sessions == [{"action": "restart"}, {"action": "restart", "in_place": True, "map": "/Game/Maps/Arena"}]


def test_start_level_moves_a_game_kept_open_in_place():
    asked = []
    link = types.SimpleNamespace(request=lambda op, args=None, timeout=None: asked.append((op, dict(args))) or
                                 {"running": True}, status=lambda text: None)
    game = types.SimpleNamespace(restart_in_place=True, level=None)
    level = cli.levelsmod.Level()
    level.asked, level.playing = "/Game/Maps/Arena", "/Game/Maps/Lobby"
    cli.start_level(link, game, level)
    assert ("session", {"action": "restart", "map": "/Game/Maps/Arena", "in_place": True}) in asked


def test_external_run_passes_keep_game_open_only_when_true():
    assert cli.external_run({"request": "Verify that jump works", "kind": "verify", "keep_game_open": True})[
        "keep_game_open"] is True
    assert "keep_game_open" not in cli.external_run({"request": "Verify that x", "kind": "verify",
                                                     "keep_game_open": "yes"})


# -- headless: the front ------------------------------------------------------------------------------------------

def test_headless_finds_the_project_in_the_working_folder_or_above(tmp_path):
    game = tmp_path / "Farm"
    (game / "Source").mkdir(parents=True)
    (game / "Farm.uproject").write_text("{}", encoding="utf-8")
    eng, path = mcp.find_headless_project(cwd=str(game / "Source"))
    assert eng.name == "unreal" and path.endswith("Farm.uproject")
    eng, why = mcp.find_headless_project(cwd=str(tmp_path))
    assert eng is None and "pass project" in why
    eng, why = mcp.find_headless_project(asked=str(tmp_path / "nowhere"))
    assert eng is None and "No Unreal, Unity or Godot project" in why


def test_headless_roblox_parallel_and_a_pinned_brain_are_refused(tmp_path, monkeypatch):
    front = mcp.Server(out=open(os.devnull, "w"), find=lambda: ([], "none"))
    place = tmp_path / "Kart.rbxl"
    place.write_bytes(b"<roblox!")
    answer = front.forward("start_run", {"request": "Verify that the kart drifts", "kind": "verify",
                                         "headless": True, "project": str(place)})
    assert answer["isError"] and "Roblox has no headless game" in answer["content"][0]["text"]
    answer = front.forward("start_run", {"request": "Verify x", "kind": "verify", "headless": True, "parallel": 2})
    assert answer["isError"] and "one run at a time" in answer["content"][0]["text"]
    monkeypatch.setenv("AUTOPILOT_MCP_BRAIN", str(tmp_path / "brain.json"))
    answer = front.forward("start_run", {"request": "Verify x", "kind": "verify", "headless": True})
    assert answer["isError"] and "aren't available here" in answer["content"][0]["text"]


def test_the_front_starts_a_headless_service_once_and_sends_its_runs_there(tmp_path, monkeypatch):
    project = tmp_path / "Farm.uproject"
    project.write_text("{}", encoding="utf-8")
    started, posts = [], []

    class FakeHeadless:
        def __init__(self, project, engine=None):
            self.project, self.engine, self.log = project, engine, "headless.log"
            started.append(project)
            self.polls = 0

        def alive(self):
            return True

        def control(self):
            self.polls += 1
            return "http://127.0.0.1:1/x" if self.polls > 1 else None  # (not said yet, the first time)

    monkeypatch.setattr(mcp, "Headless", FakeHeadless)
    monkeypatch.setattr(mcp.time, "sleep", lambda s: None)
    answers = iter([{"connected": False, "content": [], "isError": True},
                    {"content": [{"type": "text", "text": "Autopilot run 20261119-1-ab12 started on Farm"}],
                     "isError": False},
                    {"content": [{"type": "text", "text": "Autopilot run 20261119-2-cd34 started on Farm"}],
                     "isError": False},
                    {"content": [{"type": "text", "text": "Reported"}], "isError": False}])

    def post(url, body):
        posts.append((url, body))
        return next(answers)

    front = mcp.Server(out=open(os.devnull, "w"), find=lambda: ([], "none"))
    front.post = post
    first = front.forward("start_run", {"request": "Verify that reload works", "kind": "verify", "headless": True,
                                        "project": str(project)})
    assert "draws no picture" in first["content"][0]["text"] and not first["isError"]
    front.forward("start_run", {"request": "Verify that jump works", "kind": "verify", "headless": True,
                                "project": str(project)})
    assert len(started) == 1  # (one service, one game, for every headless run)
    assert all("headless" not in body and "project" not in body for _, body in posts)
    front.forward("report", {"run": "20261119-1-ab12", "outcome": "CONFIRMED"})
    assert posts[-1][0] == "http://127.0.0.1:1/x/call/report"


def test_a_headless_service_that_ends_says_why(tmp_path, monkeypatch):
    project = tmp_path / "Farm.uproject"
    project.write_text("{}", encoding="utf-8")
    from autopilot import bridge

    monkeypatch.setattr(bridge, "brain_file", lambda name="brain.json": str(tmp_path / name))
    cmd = [sys.executable, "-c", "print('No game: the engine is missing'); raise SystemExit(1)"]
    real_popen = mcp.subprocess.Popen
    monkeypatch.setattr(mcp.subprocess, "Popen", lambda c, **k: real_popen(cmd, **k))
    front = mcp.Server(out=open(os.devnull, "w"), find=lambda: ([], "none"))
    answer = front.forward("start_run", {"request": "Verify x", "kind": "verify", "headless": True,
                                         "project": str(project)})
    assert answer["isError"]
    assert "the engine is missing" in answer["content"][0]["text"]


def test_serve_headless_refuses_roblox_and_an_open_unity_project(tmp_path, monkeypatch):
    place = tmp_path / "Kart.rbxl"
    place.write_bytes(b"<roblox!")
    assert "Roblox has no headless game" in cli.headless_refused(types.SimpleNamespace(path=str(place), engine=None))
    assert "--project" in cli.headless_refused(types.SimpleNamespace(path=None, engine=None))
    unity = tmp_path / "Rts"
    (unity / "ProjectSettings").mkdir(parents=True)
    (unity / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 6000.6.5f1\n", encoding="utf-8")
    from autopilot.engines import unity as unitymod

    monkeypatch.setattr(unitymod, "project_open", lambda folder: True)
    assert "open in the Unity editor" in cli.headless_refused(types.SimpleNamespace(path=str(unity), engine=None))


def test_the_headless_game_is_told_to_stay_open(monkeypatch, tmp_path):
    project = tmp_path / "Farm.uproject"
    project.write_text(json.dumps({"EngineAssociation": "5.8"}), encoding="utf-8")
    launched = []
    monkeypatch.setattr(cli, "start_game", lambda args, cmd: launched.append(cmd))
    args = types.SimpleNamespace(path=str(project), cmd="serve", engine=None, watch=False)
    assert cli.launch_headless(args, "ws://127.0.0.1:9/v1/bridge", say=lambda t: None)
    assert launched[0][-1] == "-PlaytestStayOpen" and "-nullrhi" in launched[0]
    args.cmd = "regress"
    cli.launch_headless(args, "ws://127.0.0.1:9/v1/bridge", say=lambda t: None)
    assert "-PlaytestStayOpen" not in launched[1]


# -- the plugins restart in place ------------------------------------------------------------------------------

def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def test_every_plugin_restarts_in_place_when_asked():
    unreal = read("plugin/unreal/Autopilot/Source/AutopilotPlaytest/Private/PlaytestOpsSession.cpp")
    assert 'TEXT("in_place")' in unreal and "bInPlace && Bridge.SessionHandler" in unreal
    unity = read("plugin/unity/com.ramenvr.autopilot/Editor/EditorHost.cs")
    assert '"in_place"' in unity and "LoadSceneInPlayMode" in unity
    godot = read("plugin/godot/addons/autopilot/editor_bridge.gd")
    assert '"in_place"' in godot
    roblox = read("plugin/roblox/src/Editor.luau")
    assert "args.in_place == true" in roblox and 'op = "respawn"' in roblox
    assert "respawn = function()" in read("plugin/roblox/src/Server.luau")
    assert "function O.respawn" in read("autopilot/engines/roblox.luau")


def test_a_headless_godot_is_the_editor_that_last_opened_the_project(tmp_path, monkeypatch):
    from autopilot.engines import godot

    monkeypatch.delenv("AUTOPILOT_GODOT", raising=False)
    monkeypatch.setattr(godot.shutil, "which", lambda name: None)
    exe = tmp_path / "Godot_v4.7.2-stable_win64.exe"
    exe.write_bytes(b"")
    (tmp_path / "game" / ".godot" / "editor").mkdir(parents=True)
    (tmp_path / "game" / "project.godot").write_text("[application]\n", encoding="utf-8")
    (tmp_path / "game" / ".godot" / "editor" / "project_metadata.cfg").write_text(
        f'[editor_metadata]\n\nexecutable_path="{exe.as_posix()}"\n', encoding="utf-8")
    assert godot.editor_for(str(tmp_path / "game")) == exe.as_posix()
    assert godot.editor_for(str(tmp_path / "nowhere")) == godot.EXE
