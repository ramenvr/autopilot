"""Several runs at once (autopilot/cli.py Lanes): the user's own agent keeps up to three going (start_run's `parallel`),
each in a game of its own that Unreal's editor plays side by side (Play In Editor instances). Each run's link is a
bridge.Lane: its requests are its player's, its recorded frames, events and clock its own; the MCP front and the
agent service tell the runs apart by the id start_run answers with."""

import threading
import time

from autopilot import bridge, cli, mcp
from autopilot.agents import external


def answering_link(results=None, sent=None):
    """A Link whose plugin answers every request at once with results[op] (and the request's player's game time:
    5 + player)."""
    sent = [] if sent is None else sent
    results = results or {}
    box = {}

    def send(frame):
        sent.append(frame)
        if frame.get("type") == "request":
            player = (frame.get("args") or {}).get("player", 1)
            threading.Thread(target=lambda: box["link"].feed({
                "type": "response", "id": frame["id"], "ok": True, "result": results.get(frame["op"], {}),
                "t": 5.0 + player}), daemon=True).start()

    link = box["link"] = bridge.Link(send)
    link.hello = {"engine": "unreal", "features": ["players", "instances", "media"], "project": "Game"}
    return link, sent


def requests_of(sent, op):
    return [f for f in sent if f.get("type") == "request" and f.get("op") == op]


def test_a_lanes_requests_are_its_players_and_its_clock_is_its_own():
    link, sent = answering_link({"context": {"character": "/Game/P", "players": [{"player": 1}, {"player": 2}],
                                             "net": {"mode": "standalone", "players": 3, "player": 2}}})
    lane = bridge.Lane(link, 2, 3)
    assert lane.request("ping") == {} and requests_of(sent, "ping")[-1]["args"] == {"player": 2}
    assert lane.game_time == 7.0  # (player 2's world's clock, from its own answers)
    lane.post("key", {"key": "W"})
    assert requests_of(sent, "key")[-1]["args"] == {"key": "W", "player": 2}
    # A session start or restart asks for the games side by side; the plugin starts them once
    lane.request("session", {"action": "start"})
    assert requests_of(sent, "session")[-1]["args"] == {"action": "start", "player": 2, "instances": 3}
    # The run sees a game of one player: the others are other runs' games
    context = lane.request("context")
    assert "players" not in context and context["net"] == {"mode": "standalone"}
    assert lane.hello["features"] == ["media"] and lane.hello["instance"] == 2
    assert "players" in link.hello["features"]  # (the link's own hello as it was)


def test_a_lanes_stop_ends_nothing_while_another_game_plays():
    link, sent = answering_link()
    busy = {"beside": True}
    lane = bridge.Lane(link, 1, 3, busy_beside=lambda: busy["beside"])
    assert lane.request("session", {"action": "stop"})["running"] is True
    assert not requests_of(sent, "session") and requests_of(sent, "pause")[-1]["args"] == {"paused": True, "player": 1}
    busy["beside"] = False  # (the last run going: the play session ends)
    lane.request("session", {"action": "stop"})
    assert requests_of(sent, "session")[-1]["args"]["action"] == "stop"


def test_only_player_ones_run_reaches_the_tab():
    link, sent = answering_link()
    first, second = bridge.Lane(link, 1, 3), bridge.Lane(link, 2, 3)
    for lane in (first, second):
        lane.send({"type": "activity", "id": lane.player, "text": "x"})
        lane.status(f"Playing {lane.player}")
        lane.send({"type": "result", "result": {"outcome": "CONFIRMED"}})
    assert [f.get("id") for f in sent if f["type"] == "activity"] == [1]
    assert [f["text"] for f in sent if f["type"] == "status"] == ["Playing 1"]
    assert len([f for f in sent if f["type"] == "result"]) == 1


def test_each_games_frames_and_events_go_to_its_run():
    link, _ = answering_link()
    first, second = bridge.Lane(link, 1, 3).attach(), bridge.Lane(link, 2, 3).attach()
    frames = {1: [], 2: []}
    events = {1: [], 2: [], "link": []}
    first.frame_listeners.append(frames[1].append)
    second.frame_listeners.append(frames[2].append)
    first.listeners.append(events[1].append)
    second.listeners.append(events[2].append)
    link.listeners.append(events["link"].append)
    link.feed({"type": "frame", "n": 0, "t": 1.0, "jpeg": "AA"})
    link.feed({"type": "frame", "n": 0, "t": 2.0, "player": 2, "jpeg": "AA"})
    assert [f["t"] for f in frames[1]] == [1.0] and [f["t"] for f in frames[2]] == [2.0]
    link.feed({"type": "events", "events": [{"kind": "log", "text": "LogTemp: x"},
                                            {"kind": "finding", "finding": {"detector": "stuck"}},
                                            {"kind": "watch", "player": 2, "what": "door"}]})
    assert [e["kind"] for e in events[1]] == ["log", "finding"]  # (the detectors watch player 1's game)
    assert [e["kind"] for e in events[2]] == ["log", "watch"]
    assert [e["kind"] for e in events["link"]] == ["log", "finding", "watch"]
    assert first.findings == link.findings and second.findings == []
    second.detach()
    link.feed({"type": "frame", "n": 1, "t": 3.0, "player": 2, "jpeg": "AA"})
    assert len(frames[2]) == 1  # (its run has ended: nobody records it)


def test_a_lanes_recording_keeps_its_own_players_frames(tmp_path):
    import base64
    import os

    from test_telemetry import jpeg

    from autopilot import media

    link, sent = answering_link()
    lane = bridge.Lane(link, 2, 3).attach()
    now = time.time() - 60
    with media.Recorder(lane, str(tmp_path)) as rec:
        rec.begin(1)
        for n in range(3):  # (the game side by side's frames say its player: they are this run's own)
            link.feed({"type": "frame", "n": n, "t": n, "wall": now + n * 0.2, "player": 2,
                       "jpeg": base64.b64encode(jpeg()).decode()})
        rec.end(1)
    assert rec.kept == 3 and rec.received == 3
    assert os.path.exists(tmp_path / "media" / "recording-1.mp4")
    assert not requests_of(sent, "context")  # (a lane's game plays one player)
    assert {f["args"]["player"] for f in requests_of(sent, "record")} == {2}


class Alive:
    def __init__(self):
        self.alive = True

    def is_alive(self):
        return self.alive


def test_lanes_take_up_to_the_size_the_first_run_asked_for():
    lanes = cli.Lanes()
    threads = []
    for want, expected in ((3, 1), (1, 2), (3, 3)):
        lane, refused = lanes.take(want)
        assert (lane, refused) == (expected, None)
        threads.append(Alive())
        lanes.going[lane] = (threads[-1], threading.Event(), None)
    assert lanes.size == 3
    lane, refused = lanes.take(3)
    assert lane is None and "3 runs are going" in refused
    threads[1].alive = False  # (lane 2's run reported: the next takes its game)
    assert lanes.take(3) == (2, None)
    for t in threads:
        t.alive = False
    assert not lanes.busy() and lanes.size == 1  # (every run ended: the next one sets the size again)
    assert lanes.take(1) == (1, None)
    lanes.going[1] = (Alive(), threading.Event(), None)
    lane, refused = lanes.take(3)
    assert lane is None and "A run is going" in refused  # (one run at a time: its play session plays one game)


def test_max_parallel_follows_the_setting(monkeypatch):
    monkeypatch.delenv("AUTOPILOT_MAX_PARALLEL", raising=False)
    assert cli.max_parallel() == 3
    monkeypatch.setenv("AUTOPILOT_MAX_PARALLEL", "2")
    assert cli.max_parallel() == 2
    assert cli.plays_side_by_side(answering_link()[0])
    assert not cli.plays_side_by_side(type("L", (), {"hello": {"features": ["players"]}})())


def test_a_start_call_says_how_many_runs_its_agent_keeps_going():
    assert cli.external_run({"request": "Verify that jump works", "kind": "verify", "parallel": 3})["parallel"] == 3
    assert "parallel" not in cli.external_run({"request": "Verify that jump works", "kind": "verify"})


class FakeDesk:
    def __init__(self, marker, ended=None):
        self.marker, self.ended, self.calls = marker, ended, []

    def call(self, tool, args):
        self.calls.append((tool, args))
        return external.text_result(f"{self.marker}: {tool}")


def test_the_agent_service_tells_its_runs_apart_by_their_ids():
    control = external.Control()
    try:
        a, b = FakeDesk("r1-a"), FakeDesk("r2-b")
        control.desks = {"r1-a": a, "r2-b": b}
        control.desk = b
        refused = control.call("snapshot", {"args": {}})
        assert refused["isError"] and "2 runs are going (r1-a, r2-b)" in refused["content"][0]["text"]
        assert control.call("snapshot", {"run": "r1-a", "args": {}})["content"][0]["text"] == "r1-a: snapshot"
        a.ended = "reported"
        # (one going: a call that names none is its)
        assert control.call("snapshot", {"args": {}})["content"][0]["text"] == "r2-b: snapshot"
        gone = control.call("snapshot", {"run": "r9-x", "args": {}})
        assert gone["going"] is False and "isn't going any more" in gone["content"][0]["text"]
    finally:
        control.close()


def test_the_front_sends_each_call_to_its_run_and_waits_for_each_apart(monkeypatch):
    monkeypatch.setattr(mcp, "WAIT_SECONDS", 0.2)
    posted = []
    slow = threading.Event()

    class Front(mcp.Server):
        def post(self, url, body):
            posted.append((url, body))
            if body.get("args", {}).get("seconds") == 99:
                slow.wait(5)
            return mcp.text(f"{url} {body.get('run')}")

    front = Front(out=None, find=lambda: (["http://s"], None))
    front.runs = {"r1-a": "http://a", "r2-b": "http://b"}
    front.control, front.run = "http://b", "r2-b"
    assert front.call("snapshot", {"run": "r1-a"})["content"][0]["text"] == "http://a/call/snapshot r1-a"
    assert posted[-1] == ("http://a/call/snapshot", {"run": "r1-a", "args": {}})
    # Several runs started here: a call that names none goes to the service, which decides (refuses while several go)
    front.call("snapshot", {})
    assert posted[-1] == ("http://b/call/snapshot", {"run": None, "args": {}})
    # One run's long call holds up its own run's calls, never another run's
    assert "still going" in front.call("run_harness", {"run": "r1-a", "seconds": 99})["content"][0]["text"]
    held = front.call("probe", {"run": "r1-a", "code": "1"})
    assert held["isError"] and "call wait" in held["content"][0]["text"]
    assert not front.call("probe", {"run": "r2-b", "code": "1"})["isError"]
    slow.set()
    deadline = time.time() + 5
    while True:
        answer = front.call("wait", {"run": "r1-a"})
        if "still going" not in answer["content"][0]["text"] or time.time() > deadline:
            break
    assert answer["content"][0]["text"] == "http://a/call/run_harness r1-a"


def test_starts_at_once_each_wait_apart(monkeypatch):
    monkeypatch.setattr(mcp, "WAIT_SECONDS", 0.2)
    release = threading.Event()

    class Front(mcp.Server):
        def first_taker(self, path, body, flag):
            release.wait(5)
            return "http://s", mcp.text(f"Autopilot run r{body['request']}-x started on Game")

    front = Front(out=None, find=lambda: (["http://s"], None))
    one = front.call("start_run", {"request": "1", "kind": "verify"})["content"][0]["text"]
    two = front.call("start_run", {"request": "2", "kind": "verify"})["content"][0]["text"]
    assert 'run="start-1"' in one and 'run="start-2"' in two  # (Claude Code's subagents share this server)
    refused = front.call("wait", {})
    assert refused["isError"] and "pass run" in refused["content"][0]["text"]
    release.set()
    deadline = time.time() + 5
    while "still going" in (answer := front.call("wait", {"run": "start-2"}))["content"][0]["text"]:
        assert time.time() < deadline
    assert answer["content"][0]["text"].startswith("Autopilot run r2-x")
    assert front.call("wait", {"run": "start-1"})["content"][0]["text"].startswith("Autopilot run r1-x")
    assert front.runs == {"r1-x": "http://s", "r2-x": "http://s"}
    assert mcp.with_run({"type": "object", "properties": {}})["properties"]["run"] == mcp.RUN_PROPERTY


def test_calls_made_one_after_another_still_play_side_by_side(monkeypatch):
    # (Claude Code makes an agent's calls on the game one at a time, even those it sends together: while several runs
    # started here go, each answers "still going" soon, so the next run's episode starts while this one's plays)
    monkeypatch.setattr(mcp, "WAIT_SECONDS", 5)
    monkeypatch.setattr(mcp, "SHARED_WAIT_SECONDS", 0.2)
    playing, release = set(), threading.Event()

    class Front(mcp.Server):
        def post(self, url, body):
            if url.endswith("/report"):
                return mcp.text("Reported. You can stop now.")
            playing.add(body["run"])
            release.wait(5)
            return mcp.text(f"episode of {body['run']}")

    front = Front(out=None, find=lambda: (["http://s"], None))
    front.runs = {"r1-a": "http://a", "r2-b": "http://b", "r3-c": "http://c"}
    started = time.time()
    for run in front.runs:
        answer = front.call("run_harness", {"run": run, "seconds": 60})["content"][0]["text"]
        assert "still going" in answer and "plays on meanwhile" in answer
    assert playing == {"r1-a", "r2-b", "r3-c"} and time.time() - started < 3
    release.set()
    for run in ("r2-b", "r1-a", "r3-c"):  # (the waits a client makes side by side)
        assert front.call("wait", {"run": run})["content"][0]["text"] == f"episode of {run}"
    # Once the others have reported, the one left waits its whole time again
    front.call("report", {"run": "r1-a", "outcome": "CONFIRMED"})
    front.call("report", {"run": "r3-c", "outcome": "CONFIRMED"})
    assert front.ended == {"r1-a", "r3-c"} and not front.side_by_side()
    release.clear()
    threading.Timer(0.5, release.set).start()
    assert front.call("run_harness", {"run": "r2-b", "seconds": 60})["content"][0]["text"] == "episode of r2-b"
