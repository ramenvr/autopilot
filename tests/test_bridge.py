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


def test_the_bridge_takes_plugins_and_refuses_web_pages():
    """A plugin that sends no Origin gets in, and so does Unreal's WebSocket library, which always sends
    http://<host>; a web page's Origin (another site, or another port on this machine) is refused."""
    import threading

    from websockets.exceptions import InvalidStatus
    from websockets.sync.client import connect as ws_connect

    port = 18769
    served = {}
    threading.Thread(target=lambda: served.update(link=bridge.serve_local(port=port, wait=5)), daemon=True).start()
    hello = json.dumps({"type": "hello", "protocol": bridge.PROTOCOL, "project": "p", "engine": "unreal"})

    def opens(origin):
        for _ in range(50):
            try:
                with ws_connect(f"ws://127.0.0.1:{port}/v1/bridge", origin=origin, proxy=None) as ws:
                    ws.send(hello)
                    return json.loads(ws.recv(timeout=5))["type"] == "welcome"
            except InvalidStatus as e:
                assert e.response.status_code == 403
                return False
            except OSError:
                time.sleep(0.05)
        raise AssertionError("the bridge didn't start")

    try:
        assert opens("http://127.0.0.1") and opens(None) and opens("http://localhost")
        for page in ("https://untrusted.invalid", "http://127.0.0.1:3000", "http://localhost:3000", "null", ""):
            assert not opens(page), page
    finally:
        served["link"].server.shutdown()


def test_a_pipe_joins_a_plugins_parts_to_its_link():
    """A plugin's parts that can't reach each other (Roblox Studio's edit, server and client DataModels) meet through
    /v1/pipe/<the link's session>: the editor's {"type": "pipe"} frames go to every part, a part's "to": "brain" frames
    are the plugin's own (a request it answers), "to": "edit" ones reach the editor wrapped as pipe frames, the rest go
    to the other parts. A key that is no session's is a plain pipe."""
    import threading

    from websockets.sync.client import connect as ws_connect

    port = 18768
    editor = {}

    def plugin():
        for _ in range(50):
            try:
                ws = ws_connect(f"ws://127.0.0.1:{port}/v1/bridge").__enter__()
                break
            except OSError:
                time.sleep(0.1)
        ws.send(json.dumps({"type": "hello", "protocol": 1, "project": "piped", "host": "editor"}))
        editor["welcome"] = json.loads(ws.recv())
        editor["ws"] = ws
        editor["got"] = []
        try:
            for message in ws:
                editor["got"].append(json.loads(message))
        except Exception:
            pass

    threading.Thread(target=plugin, daemon=True).start()
    link = bridge.serve_local(port=port, wait=10)
    try:
        key = link.session
        deadline = time.time() + 5
        while "ws" not in editor and time.time() < deadline:
            time.sleep(0.02)
        assert editor["welcome"]["session"] == key
        client = ws_connect(f"ws://127.0.0.1:{port}/v1/pipe/{key}?dm=client").__enter__()
        server = ws_connect(f"ws://127.0.0.1:{port}/v1/pipe/{key}?dm=server").__enter__()
        time.sleep(0.2)  # (both joined)
        # The editor sends a request on to the parts; the client answers the brain itself
        answer = {}

        def ask():
            answer["view"] = link.request("view", {}, timeout=5)

        asker = threading.Thread(target=ask)
        asker.start()
        req = json.loads(editor_frame(editor, "request"))
        editor["ws"].send(json.dumps({"type": "pipe", "frame": {"to": "client", **req}}))
        got = json.loads(client.recv(timeout=5))
        assert got["to"] == "client" and got["op"] == "view"
        assert json.loads(server.recv(timeout=5))["op"] == "view"  # (every part reads it: each keeps its own)
        client.send(json.dumps({"to": "brain", "from": "client", "type": "response", "id": got["id"], "ok": True,
                                "result": {"visible": []}, "t": 2.5}))
        asker.join(5)
        assert answer["view"] == {"visible": []} and link.game_time == 2.5
        # A part's frame to the editor; to the other parts
        client.send(json.dumps({"to": "edit", "from": "client", "type": "ready"}))
        deadline = time.time() + 5
        while time.time() < deadline and not any(f.get("type") == "pipe" for f in editor["got"]):
            time.sleep(0.05)
        piped = [f for f in editor["got"] if f.get("type") == "pipe"]
        assert piped and piped[0]["frame"] == {"to": "edit", "from": "client", "type": "ready"}
        server.send('{"to": "client", "type": "request", "op": "rc"}')
        assert json.loads(client.recv(timeout=5))["op"] == "rc"
        # A plain pipe (no session's key): every frame to the others
        a = ws_connect(f"ws://127.0.0.1:{port}/v1/pipe/plain").__enter__()
        b = ws_connect(f"ws://127.0.0.1:{port}/v1/pipe/plain").__enter__()
        time.sleep(0.2)
        a.send("hi")
        assert b.recv(timeout=5) == "hi"
        with pytest.raises(TimeoutError):
            client.recv(timeout=0.3)
        for ws in (client, server, a, b):
            ws.close()
        assert not link.closed and link.server_state["current"] is link
    finally:
        link.server.shutdown()


def editor_frame(editor, kind, seconds=5):
    """The first frame of a kind the fake editor got (waiting for it)."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        for f in editor.get("got", []):
            if f.get("type") == kind:
                editor["got"].remove(f)
                return json.dumps(f)
        time.sleep(0.02)
    raise AssertionError(f"no {kind} frame: {editor.get('got')}")
UNITY_A = {"engine": "unity", "project": "A", "project_id": "1"}


@pytest.mark.parametrize("a, b, same", [
    (UNITY_A, dict(UNITY_A), True),
    (UNITY_A, {**UNITY_A, "project_id": "2"}, False),  # (two games, one name)
    (UNITY_A, {**UNITY_A, "project": "Renamed"}, True),
    ({"engine": "unity", "project": "A"}, {"engine": "godot", "project": "A"}, False),
    ({"engine": "godot", "project": "A"}, {"engine": "godot", "project": "B"}, False),
    ({"engine": "godot", "project": "A"}, {"engine": "godot", "project": "A"}, True),
    ({"project": "A"}, {"engine": "unreal", "project": "A"}, True),
])
def test_same_project(a, b, same):
    assert bridge.same_project(a, b) is same


def test_a_run_keeps_its_game_when_another_editor_opens():
    """The run's calls stay with its game: another game's plugin is refused and told why, the same game's still comes
    back, and once the run has ended any plugin connects as before."""
    from websockets.sync.client import connect as ws_connect

    from autopilot.cli import LiveLink, run_holds

    port = 18766
    url = f"ws://127.0.0.1:{port}/v1/bridge"
    rts = {"type": "hello", "protocol": 1, "project": "rts", "engine": "unity", "project_id": "r", "host": "editor"}
    other = {"type": "hello", "protocol": 1, "project": "lantern", "engine": "godot", "host": "editor"}
    open_sockets = []

    def plugin(hello):
        for _ in range(50):
            try:
                ws = ws_connect(url).__enter__()
                break
            except OSError:
                time.sleep(0.1)
        open_sockets.append(ws)
        ws.send(json.dumps(hello))
        return ws, json.loads(ws.recv())

    import threading

    first = {}
    threading.Thread(target=lambda: first.update(ws=plugin(rts)[0]), daemon=True).start()
    served = bridge.serve_local(port=port, wait=10)
    link = LiveLink(served)
    try:
        with run_holds(link):
            ws, answer = plugin(other)
            assert answer["type"] == "error" and "busy with a run of rts" in answer["message"]
            assert link.current is served  # (nothing moved)
            ws, answer = plugin(rts)  # (the editor restarted: the run goes on with it)
            assert answer["type"] == "welcome"
            deadline = time.time() + 5
            while link.current is served and time.time() < deadline:
                time.sleep(0.02)
            assert link.current is not served and link.hello["project"] == "rts"
        back = link.current
        ws, answer = plugin(other)
        assert answer["type"] == "welcome"
        deadline = time.time() + 5
        while link.current is back and time.time() < deadline:
            time.sleep(0.02)
        assert link.hello["project"] == "lantern"
    finally:
        for ws in open_sockets:
            ws.close()
        served.server.shutdown()


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


def test_a_plugin_without_websockets_talks_over_http():
    """A plugin that can't keep a WebSocket (Roblox Studio's DataModels have four, every plugin's counted) opens its
    connection over HTTP (bridge.HTTP_PATH): its hello and the welcome, a request answered, its parts' pipe, the
    close."""
    import threading

    port = 18767
    base = f"http://127.0.0.1:{port + 1}/v1/http/"
    got = {}

    def plugin():
        for _ in range(50):
            try:
                peer = requests.post(base + "open?path=/v1/bridge", headers={"Authorization": "Bearer t0k"},
                                     timeout=5).json()["peer"]
                break
            except requests.ConnectionError:
                time.sleep(0.1)
        requests.post(base + f"send?peer={peer}", json=[{"type": "hello", "protocol": 1, "project": "polled",
                                                        "host": "editor"}], timeout=5)
        frames = []
        while not any(f.get("type") == "request" for f in frames):
            frames += requests.get(base + f"poll?peer={peer}", timeout=20).json()
        got["welcome"] = next(f for f in frames if f["type"] == "welcome")
        req = next(f for f in frames if f["type"] == "request")
        requests.post(base + f"send?peer={peer}", json=[{"type": "response", "id": req["id"], "ok": True,
                                                        "result": {"pong": 1}, "t": 4.0}], timeout=5)
        got["peer"] = peer

    threading.Thread(target=plugin, daemon=True).start()
    link = bridge.serve_local(port=port, wait=10, http_port=port + 1)
    try:
        assert link.hello["project"] == "polled" and link.loopback and link.auth_token == "t0k"
        assert link.request("ping", timeout=10) == {"pong": 1} and link.game_time == 4.0
        assert got["welcome"]["session"] == link.session
        # A part on the pipe over HTTP: its "to": "brain" frames are the plugin's
        part = requests.post(base + f"open?path=/v1/pipe/{link.session}", timeout=5).json()["peer"]
        time.sleep(0.2)
        answer = {}
        asker = threading.Thread(target=lambda: answer.setdefault("v", link.request("view", {}, timeout=10)))
        asker.start()
        frames = requests.get(base + f"poll?peer={got['peer']}", timeout=20).json()
        req = next(f for f in frames if f["type"] == "request")
        requests.post(base + f"send?peer={got['peer']}", json=[{"type": "pipe", "frame": {"to": "client", **req}}],
                      timeout=5)
        piped = requests.get(base + f"poll?peer={part}", timeout=20).json()
        assert piped[0]["op"] == "view" and piped[0]["to"] == "client"
        requests.post(base + f"send?peer={part}", json=[{"to": "brain", "type": "response", "id": req["id"],
                                                        "ok": True, "result": {"visible": []}}], timeout=5)
        asker.join(10)
        assert answer["v"] == {"visible": []}
        # Closing ends the link
        requests.post(base + f"close?peer={got['peer']}&code=1000", timeout=5)
        deadline = time.time() + 5
        while not link.closed and time.time() < deadline:
            time.sleep(0.05)
        assert link.closed
        assert requests.get(base + f"poll?peer={got['peer']}", timeout=5).status_code == 410
    finally:
        link.server.shutdown()
