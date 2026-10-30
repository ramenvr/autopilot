import json
import time

import pytest
import requests
from conftest import FakeGame, connect

from autopilot import bridge


@pytest.fixture
def link():
    game = FakeGame()
    link = connect(bridge.Link(lambda f: None), game)
    link.game = game
    return link


def test_requests_read_the_game(link):
    body = {"objectPath": "/Game/L.L:PersistentLevel.Player_C_0.Cargo"}
    assert link.request("rc", {"verb": "property", "body": body}) == {"Capacity": 7, "Items": []}
    assert link.request("rc", {"verb": "property", "body": {**body, "propertyName": "Capacity"}}) == {"Capacity": 7}
    assert link.request_at("ping") == ({"game_time": link.game.time}, link.game.time)


def test_an_error_answer_is_an_http_error_with_remote_controls_wording(link):
    with pytest.raises(requests.HTTPError) as e:
        link.request("rc", {"verb": "property", "body": {"objectPath": "/Game/L.L:PersistentLevel.Gone_0"}})
    assert isinstance(e.value, bridge.BridgeError)
    assert e.value.status == 400
    assert "does not exist" in e.value.response.text
    with pytest.raises(requests.HTTPError):
        link.request("nonsense")


def test_events_route_findings_logs_and_heartbeats():
    link = bridge.Link(lambda f: None)
    seen = []
    link.listeners.append(seen.append)
    link.feed({"type": "events", "t": 12.5, "events": [
        {"kind": "heartbeat", "t": 12.5, "fps": 60},
        {"kind": "log", "t": 12.5, "verbosity": "Error", "category": "LogScript", "message": "Accessed None"},
        {"kind": "finding", "t": 12.5,
         "finding": {"detector": "hud_fraction", "summary": "8/7", "signature": "hud_fraction:x"}},
    ]})
    assert link.game_time == 12.5
    assert link.findings[0]["t"] == 12.5 and link.findings[0]["signature"] == "hud_fraction:x"
    assert link.logs[0]["message"] == "Accessed None"
    assert len(seen) == 3
    assert link.last_heartbeat is not None


def test_closing_fails_waiting_requests_quickly():
    link = bridge.Link(lambda f: None)  # nobody answers
    import threading

    threading.Timer(0.2, link.close, args=("gone",)).start()
    started = time.time()
    with pytest.raises(requests.ConnectionError):
        link.request("ping", timeout=5)
    assert time.time() - started < 2
    with pytest.raises(requests.ConnectionError):
        link.request("ping")


def test_local_server_accepts_the_plugin_and_answers():
    """A real WebSocket round trip: a fake plugin connects to serve_local and answers a request."""
    import threading

    from websockets.sync.client import connect as ws_connect

    port = 18765
    result = {}

    def plugin():
        for _ in range(50):
            try:
                ws = ws_connect(f"ws://127.0.0.1:{port}/v1/bridge").__enter__()
                break
            except OSError:
                time.sleep(0.1)
        ws.send(json.dumps({"type": "hello", "protocol": 1, "project": "fakegame", "host": "game"}))
        result["welcome"] = json.loads(ws.recv())
        request = json.loads(ws.recv())
        ws.send(json.dumps({"type": "response", "id": request["id"], "ok": True, "result": {"game_time": 3.0},
                            "t": 3.0}))
        time.sleep(0.5)
        ws.close()

    threading.Thread(target=plugin, daemon=True).start()
    link = bridge.serve_local(port=port, wait=10)
    assert link.hello["project"] == "fakegame"
    assert link.loopback
    assert link.request("ping") == {"game_time": 3.0}
    assert result["welcome"]["role"] == "cli"
    link.server.shutdown()


def test_posted_requests_do_not_wait_and_their_errors_are_kept(link):
    sent = []
    link._send = lambda frame: sent.append(frame)  # nobody answers now
    link.post("key", {"key": "SpaceBar", "mode": "press"})
    assert [f["op"] for f in sent] == ["key"]
    # An error answer to a request nobody waits for is kept, not raised
    link.feed({"type": "response", "id": sent[0]["id"], "ok": False, "error": "boom"})
    assert link.async_errors == ["boom"]


def test_status_reaches_the_plugin():
    sent = []
    link = bridge.Link(sent.append)
    link.status("Starting the game")
    assert sent == [{"type": "status", "text": "Starting the game"}]


class CurrentPlugin(FakeGame):
    """Answers every op the brain needs, refusing bad arguments the way the plugin does."""

    def answer(self, op, args):
        if op in ("view", "screen", "ui", "watch"):
            self.log.append((op, args))
            return {}
        if op == "key":
            self.log.append((op, args))
            raise KeyError(f"Unknown key {args.get('key')}")
        if op == "input" and not args.get("action"):
            self.log.append((op, args))
            raise KeyError("Object  does not exist")
        if op == "rc" and args.get("verb") == "probe":
            self.log.append((op, args))
            raise KeyError("unknown rc verb probe")
        return super().answer(op, args)


def test_a_plugin_lacking_ops_the_brain_needs_is_found_at_once():
    # An older plugin (or an old copy the project loaded instead) has no view or key ops: the brain would play
    # blind with keys that do nothing
    link = connect(bridge.Link(lambda f: None), FakeGame())
    assert set(link.missing_ops()) >= {"view", "key", "screen"}
    link = connect(bridge.Link(lambda f: None), CurrentPlugin())
    assert link.missing_ops() == []


def test_checking_the_ops_changes_nothing():
    game = CurrentPlugin()
    link = connect(bridge.Link(lambda f: None), game)
    link.missing_ops()
    assert not any(op == "move" for op, _ in game.log)
    assert all(not args.get("key") for op, args in game.log if op == "key")


def test_a_serving_brain_says_where_it_is_and_takes_it_back_only_while_its_own(tmp_path, monkeypatch):
    import atexit
    import json
    import os

    registered = []
    monkeypatch.setattr(atexit, "register", lambda fn: registered.append(fn))
    path = str(tmp_path / "Autopilot" / "brain.json")
    bridge.announce("ws://127.0.0.1:50123/v1/bridge", path)
    with open(path, encoding="utf-8") as f:
        said = json.load(f)
    assert said["url"] == "ws://127.0.0.1:50123/v1/bridge" and said["pid"] == os.getpid()
    assert said["commit"] == ""  # (a checkout: no build.json)
    import autopilot

    monkeypatch.setattr(autopilot, "build", lambda: "0123abc")  # (a release wheel: the commit it was built from)
    bridge.announce("ws://127.0.0.1:50123/v1/bridge", path)
    with open(path, encoding="utf-8") as f:
        assert json.load(f)["commit"] == "0123abc"
    registered.pop()
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"url": "ws://127.0.0.1:1/v1/bridge", "pid": os.getpid() + 1}, f)  # (a brain started since)
    registered[0]()
    assert os.path.exists(path)  # (not ours any more: left alone)
    bridge.announce("ws://127.0.0.1:50124/v1/bridge", path)
    registered[1]()
    assert not os.path.exists(path)


def test_the_brain_file_is_in_the_folder_unreal_calls_the_user_settings_folder(monkeypatch):
    import sys

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\u\AppData\Local")
    assert bridge.brain_file().replace("\\", "/").endswith("AppData/Local/Autopilot/brain.json")
    monkeypatch.setattr(sys, "platform", "darwin")
    assert bridge.brain_file().replace("\\", "/").endswith("Library/Application Support/Autopilot/brain.json")
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", "/home/u/.config")
    assert bridge.brain_file().replace("\\", "/") == "/home/u/.config/Autopilot/brain.json"
