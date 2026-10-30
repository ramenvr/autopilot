"""The activity feed: one `activity` frame per thing the agent does or says (docs/protocol.md, The activity feed)."""

import json
import os
import threading
import time
import types

import pytest
from test_autopilot import CrateHarness, FakeJev, FakeLink  # noqa: F401 (CrateHarness: the harness file below)

from autopilot import bridge, cli
from autopilot.agents import feed, session
from autopilot.kit.game import Game


@pytest.fixture
def game():
    g = Game(FakeLink())
    g.look = {"x": 0.1, "y": 0.1}
    return g


def harness_file(run_dir):
    """This test module's CrateHarness as the run's harness.py."""
    src = open(os.path.join(os.path.dirname(__file__), "test_autopilot.py"), encoding="utf-8").read()
    crate = src[src.index("class CrateHarness"):src.index("@pytest.fixture")]
    with open(os.path.join(run_dir, "harness.py"), "w", encoding="utf-8") as f:
        f.write("from autopilot.kit import *\n\n" + crate)


def open_run(game, tmp_path, monkeypatch, **kwargs):
    from autopilot import codex

    monkeypatch.setattr(codex, "MEMORY", str(tmp_path))
    frames, said = [], []
    s = session.Session(game, "fake", "Open the crate", "r1", say=said.append, activity=frames.append,
                        client=FakeJev("open_crate"), **kwargs)
    return s, frames, said


def log_of(s):
    with open(s.log, encoding="utf-8") as f:
        return f.read()


def test_events_are_numbered_in_order_with_the_time_and_the_stage(game, tmp_path, monkeypatch):
    s, frames, said = open_run(game, tmp_path, monkeypatch)
    s.say(session.READING)
    s.tool_call("Read", {"file_path": os.path.join(s.root, "codex", "README.md")}, log="tool Read: {}")
    s.say(session.PROBING)
    s.activity("text", "The crate opens with E")
    s.say(session.WRITING)
    s.tool_call("Write", {"file_path": "runs/r1/harness.py", "content": "x = 1\n"}, log="tool Write: {}")
    s.say("Trying the harness (run 1)")
    s.activity("episode", "Episode 1: 10 s of play")
    s.say(session.RESULTS)
    s.activity("text", "Done")
    assert [f["id"] for f in frames] == [1, 2, 3, 4, 5]
    assert all(f["type"] == "activity" for f in frames)
    ts = [f["t"] for f in frames]
    assert ts == sorted(ts) and all(isinstance(t, float) and 0 <= t < 60 for t in ts)
    assert [f["stage"] for f in frames] == ["analyze", "probe", "build", "play", "results"]
    assert [f["kind"] for f in frames] == ["read", "text", "write", "episode", "text"]
    assert frames[0]["text"] == "Read codex/README.md" and frames[0]["detail"] == "codex/README.md"
    assert frames[2]["text"] == "Wrote runs/r1/harness.py" and frames[2]["detail"] == "x = 1\n"
    assert set(frames[0]) == {"type", "id", "t", "stage", "kind", "text", "detail", "ends"}
    # the same list is kept in the session, and in the run's folder, one object a line
    assert [e["id"] for e in s.events] == [1, 2, 3, 4, 5] and "type" not in s.events[0]
    with open(os.path.join(s.run_dir, "activity.jsonl"), encoding="utf-8") as f:
        lines = [json.loads(line) for line in f.read().splitlines()]
    assert lines == s.events


def test_the_stage_map_is_one_place(game, tmp_path, monkeypatch):
    assert session.stage_of(session.READING) == "analyze"
    assert session.stage_of(session.PROBING) == "probe"
    assert session.stage_of(session.WRITING) == "build"
    assert session.stage_of("Trying the harness (run 3)") == "play"
    assert session.stage_of(session.RESULTS) == "results"
    assert session.stage_of("Now: Walk to the crate") is None and session.stage_of("Steering: go left") is None
    s, frames, said = open_run(game, tmp_path, monkeypatch)
    s.say("Trying the harness (run 1)")
    s.say("Now: Walk to the crate")  # (an option's line names no stage: the feed stays at play)
    s.activity("text", "still playing")
    assert frames[-1]["stage"] == "play"
    s.looking = True
    s.say(session.READING)  # (after an episode, reading is working on its results)
    s.activity("text", "reading the trace")
    assert frames[-1]["stage"] == "results" and said[-1] == session.RESULTS


def test_a_repeated_stage_line_is_said_once(game, tmp_path, monkeypatch):
    s, frames, said = open_run(game, tmp_path, monkeypatch)
    for line in (session.READING, session.READING, session.READING, session.PROBING, session.READING,
                 "Now: Walk", "Now: Walk", session.READING):
        s.say(line)
    assert said == [session.READING, session.PROBING, session.READING, "Now: Walk", session.READING]


def test_a_probe_and_its_result_share_the_call_id_and_the_log_is_as_before(game, tmp_path, monkeypatch):
    s, frames, said = open_run(game, tmp_path, monkeypatch)
    code = "print(len(game.all_actors()))\ngame.loc(game.actors('Crate')[0])"
    s.tool_call("mcp__game__probe", {"code": code}, log=f"tool mcp__game__probe: {json.dumps({'code': code})}")
    out = s.probe(code)
    assert "2" in out
    call, result = frames
    assert call["kind"] == result["kind"] == "probe" and call["ends"] is None and result["ends"] == call["id"]
    assert call["text"] == "Tried 2 lines in the game" and call["detail"] == code
    assert result["text"].startswith("Got ") and " lines back in " in result["text"] and "800" in result["detail"]
    log = log_of(s)
    assert f"] tool mcp__game__probe: {json.dumps({'code': code})}\n" in log
    assert f"] probe (0.0s):\n{code}\n-> 2\n" in log or f"] probe (0.1s):\n{code}\n-> 2\n" in log
    # a failed probe says so; the next call is a new row
    s.tool_call("mcp__game__probe", {"code": "raise ValueError('boom')"}, log="tool probe: x")
    s.probe("raise ValueError('boom')")
    assert frames[2]["ends"] is None and frames[3]["ends"] == frames[2]["id"]
    assert frames[3]["text"].startswith("Failed after") and "ValueError: boom" in frames[3]["detail"]
    assert "probe (" in log_of(s) and "-> Traceback" in log_of(s)


def test_an_episode_and_what_came_of_it(game, tmp_path, monkeypatch):
    s, frames, said = open_run(game, tmp_path, monkeypatch)
    s.tool_call("mcp__game__run_harness", {"seconds": 15}, log="tool run_harness: {}")
    assert "doesn't exist yet" in s.run_harness(15)
    assert frames[0]["kind"] == "episode" and frames[0]["text"] == "Playing the harness for 15 s"
    assert frames[1]["kind"] == "episode" and frames[1]["ends"] == 1
    assert frames[1]["text"].startswith("No episode: runs/r1/harness.py doesn't exist yet")
    harness_file(s.run_dir)
    s.tool_call("mcp__game__run_harness", {"seconds": 15, "restart": False}, log="tool run_harness: {}")
    out = s.run_harness(15, restart=False)
    assert "status=done" in out, out
    assert frames[2]["text"] == "Playing the harness for 15 s, going on from where the game is"
    assert frames[3]["ends"] == 3 and frames[3]["kind"] == "episode" and frames[3]["stage"] == "results"
    assert frames[3]["text"].startswith("Episode 1: ") and "s of play (status done)" in frames[3]["text"]
    assert "open_crate" in frames[3]["detail"] and len(frames[3]["detail"].splitlines()) <= 31
    log = log_of(s)
    assert "] run_harness 1 (15s): status done, " in log and "Jev decisions" in log
    assert "Jev" not in json.dumps(frames)  # (the feed is in the user's words)


def test_said_text_is_its_first_line_with_the_whole_as_detail(game, tmp_path, monkeypatch):
    s, frames, said = open_run(game, tmp_path, monkeypatch)
    s.said("Done.")
    s.said("\n\nFirst Jev checks the crate.\nThen it opens it.\n")
    assert frames[0] == {**frames[0], "kind": "text", "text": "Done.", "detail": None}
    assert frames[1]["text"] == "First the agent checks the crate." and "Then it opens it." in frames[1]["detail"]
    log = log_of(s)
    assert "] says: Done.\n" in log and "says: \n\nFirst Jev checks" in log  # (the log keeps the agent's words)


def test_steer_alert_restart_and_report_events(game, tmp_path, monkeypatch):
    s, frames, said = open_run(game, tmp_path, monkeypatch)
    game.level_info = types.SimpleNamespace(levels=["/Game/Maps/Workshop"], playing=None)
    assert s.restart_level("Workshop").startswith("Restarted")
    assert frames[-1]["kind"] == "restart" and frames[-1]["text"] == "Restarted the level Workshop"
    assert frames[-1]["detail"] == "/Game/Maps/Workshop" and "level: /Game/Maps/Workshop" in log_of(s)
    assert s.restart_level().startswith("Restarted")
    assert frames[-1]["kind"] == "restart" and frames[-1]["text"] == "Restarted the level"
    harness_file(s.run_dir)
    s.run_harness(15)
    s.steering.add("go to the lantern")
    s.steer_note()  # (from here play goes on from where the game is: no more restarts)
    assert frames[-1]["kind"] == "steer" and frames[-1]["text"] == "Steered: go to the lantern"
    assert "steered: ['go to the lantern']" in log_of(s)
    s.alert("The recording could not be analyzed.")
    s.alert("The recording could not be analyzed.")  # (once)
    assert [f["kind"] for f in frames].count("alert") == 1 and frames[-1]["text"].startswith("The recording")
    s.do_report({"outcome": "CONFIRMED", "summary": "The crate opens.", "evidence": ["episode 1: opened"]})
    assert frames[-1]["kind"] == "report" and frames[-1]["stage"] == "report"
    assert frames[-1]["text"] == "Reported CONFIRMED"
    assert frames[-1]["detail"] == "The crate opens.\n- episode 1: opened"


def test_text_and_detail_are_in_the_users_words_and_free_of_secrets(game, tmp_path, monkeypatch):
    monkeypatch.setenv("LANTERN_API_KEY", "lantern-secret-0123456789")
    s, frames, said = open_run(game, tmp_path, monkeypatch)
    s.activity("text", "Jev's turn: key lantern-secret-0123456789", detail="Jev picks. " + "x" * 40000)
    assert frames[0]["text"] == "The agent's turn: key [secret]"
    assert frames[0]["detail"].startswith("The agent picks.") and len(frames[0]["detail"]) < 30100
    assert frames[0]["detail"].endswith("more characters)")
    # a long detail is cut on a line boundary where there is one
    cut = session._cut_lines("\n".join(f"line {i}" for i in range(5000)), 30000)
    assert cut.rsplit("\n", 1)[0].endswith(tuple(f"line {i}" for i in range(5000))) and "more characters" in cut


def test_tool_calls_as_the_feed_shows_them(tmp_path):
    root = str(tmp_path)
    d = feed.describe_tool
    assert d("Read", {"file_path": os.path.join(root, "codex", "a.txt")}, root) == ("read", "Read codex/a.txt",
                                                                                     "codex/a.txt")
    assert d("read", {"filePath": "codex/a.txt"}, root)[1] == "Read codex/a.txt"  # OpenCode
    assert d("read_file", json.dumps({"path": "codex/a.txt"}), root)[1] == "Read codex/a.txt"  # the host's text
    assert d("Grep", {"pattern": "Crop", "path": "codex"}, root) == (
        "search", "Searched the game's code for Crop in codex", "Crop")
    assert d("Glob", {"pattern": "codex/**/*.txt"}, root)[:2] == ("search", "Looked for files matching codex/**/*.txt")
    assert d("list_files", {"pattern": "runs/*"}, root)[0] == "search"
    kind, text, detail = d("Edit", {"file_path": "runs/r1/harness.py", "old_string": "a", "new_string": "b\n" * 40},
                           root)
    assert (kind, text) == ("write", "Edited runs/r1/harness.py") and detail.count("\n") == 40  # (whole)
    assert d("edit", {"filePath": "lib.py", "oldString": "a", "newString": "b"}, root)[2] == "b"
    assert d("write_file", {"path": "notes.md", "content": "notes"}, root) == ("write", "Wrote notes.md", "notes")
    assert d("Agent", {"description": "find the crate's code", "prompt": "Where..."}, root)[:2] == (
        "search", "Asked a researcher: Where...")
    assert d("mcp__game__probe", {"code": "a\n\nb\n"}, root) == ("probe", "Tried 2 lines in the game", "a\n\nb\n")
    assert d("probe", {"code": "a"}, root)[1] == "Tried 1 line in the game"
    assert d("run_harness", {"seconds": 60, "restart": False}, root) == (
        "episode", "Playing the harness for 60 s, going on from where the game is", None)
    assert d("run_harness", {}, root)[1] == "Playing the harness for 30 s"
    assert d("describe_frames", {"questions": ["Is the lid open?", "Any light?"], "episode": 2}, root) == (
        "look", "Looking at episode 2's frames", "Is the lid open?\nAny light?")
    assert d("frames", {}, root)[:2] == ("look", "Taking the last episode's frames")
    for name in ("report", "restart_level", "snapshot", "mcp__game__report", "Bash"):
        assert d(name, {"outcome": "CONFIRMED"}, root) == (None, None, None)


def test_a_tool_the_feed_has_no_row_for_is_a_log_line_alone(game, tmp_path, monkeypatch):
    s, frames, said = open_run(game, tmp_path, monkeypatch)
    log = 'tool report: {"outcome": "CONFIRMED"}'
    assert s.tool_call("mcp__game__report", {"outcome": "CONFIRMED"}, log=log) is None
    assert not frames and '] tool report: {"outcome": "CONFIRMED"}\n' in log_of(s)


def test_the_child_forwards_its_events_and_the_parent_numbers_them(game, tmp_path, monkeypatch):
    """In the sandbox the probe runs in a child whose session forwards the result event; the parent's session gives
    it the id, the log line and the frame, and ends the call the parent saw."""
    s, frames, said = open_run(game, tmp_path, monkeypatch)
    forwarded = []
    child = session.Session(game, "fake", "Open the crate", "r1", say=lambda t: None)
    child.forward = forwarded.append
    s.tool_call("mcp__game__probe", {"code": "print(1)"}, log="tool probe: x")
    assert child.activity("probe", "Got 1 line back in 0.0 s", detail="1", ends="probe",
                          log="probe (0.0s):\nprint(1)\n-> 1") is None
    assert forwarded == [{"kind": "probe", "text": "Got 1 line back in 0.0 s", "detail": "1", "ends": "probe",
                          "stage": None, "log": "probe (0.0s):\nprint(1)\n-> 1"}]
    assert not child.events
    s.activity(**forwarded[0])
    assert [f["id"] for f in frames] == [1, 2] and frames[1]["ends"] == 1 and frames[1]["kind"] == "probe"
    assert log_of(s).count("probe (0.0s):\nprint(1)\n-> 1\n") == 1


def test_a_sandboxed_run_feeds_the_parent(game, tmp_path, monkeypatch):
    """A real child process: its probe's result reaches the parent's feed, once, ending the parent's call."""
    from autopilot import codex

    monkeypatch.setenv("AUTOPILOT_SANDBOX", "process")
    monkeypatch.setattr(codex, "MEMORY", str(tmp_path))
    frames, said = [], []
    s = session.open_session(game, "fake", "Open the crate", "r1", say=said.append, client=FakeJev("open_crate"),
                             activity=frames.append)
    try:
        s.tool_call("mcp__game__probe", {"code": "print(len(game.all_actors()))"}, log="tool probe: x")
        out = s.probe("print(len(game.all_actors()))")
        assert "2" in out
        for _ in range(100):  # (the child's event arrives on the parent's reader thread)
            if len(frames) >= 2:
                break
            time.sleep(0.05)
        assert [f["kind"] for f in frames] == ["probe", "probe"] and frames[1]["ends"] == frames[0]["id"] == 1
        assert frames[1]["text"].startswith("Got 1 line back") and frames[1]["detail"].strip() == "2"
        log = log_of(s)
        assert log.count("probe (") == 1 and "-> 2" in log
    finally:
        s.sandbox.close()


class Plugin:
    """Enough of a link for the serve loop: what it was sent, and the frames it says."""

    def __init__(self, messages):
        self.hello = {"project": "Farm"}
        self.sent = []
        self.rebound = []
        self.current = self
        self._messages = messages

    def send(self, frame):
        self.sent.append(frame)

    def status(self, text):
        self.sent.append({"type": "status", "text": text})

    def next_message(self, timeout=None):
        msg = self._messages()
        if msg is None:
            raise KeyboardInterrupt
        return msg


def test_the_serve_loop_sends_the_feed_again_to_a_plugin_that_asks(monkeypatch):
    """A plugin that connects again while a run goes (or just ended) says `activity_since`: it gets the events it
    hasn't got, in order, after the welcome's frames."""
    ran = threading.Event()

    def fake_run(link, prompt, *a, feed=None, **k):
        feed.start()
        for i, text in enumerate(("Read codex/README.md", "Tried 3 lines in the game", "Got 2 lines back"), 1):
            feed.add(link, {"type": "activity", "id": i, "t": float(i), "stage": "analyze", "kind": "read",
                            "text": text, "detail": None, "ends": None})
        ran.set()

    monkeypatch.setattr(cli, "run_prompt", fake_run)
    monkeypatch.setattr(cli, "send_memory_state", lambda *a, **k: None)
    monkeypatch.setattr(cli.account, "send_state", lambda *a, **k: None)
    monkeypatch.setattr(cli.account, "SignIn", lambda link: None)
    hellos = iter([{"type": "hello", "project": "Farm", "activity_since": 1},
                   {"type": "hello", "project": "Farm", "activity_since": 0},
                   {"type": "hello", "project": "Farm"}])  # (a plugin that doesn't ask gets nothing again)

    def messages():
        assert ran.wait(10)
        return next(hellos, None)

    link = Plugin(messages)
    link.hello = {"project": "Farm", "run": {"kind": "goal", "goal": "Open the crate"}}
    with pytest.raises(KeyboardInterrupt):
        cli.cmd_serve(link, types.SimpleNamespace(path=None, model=None))
    ids = [f["id"] for f in link.sent if f["type"] == "activity"]
    assert ids == [1, 2, 3, 2, 3, 1, 2, 3], link.sent
    assert len(link.rebound) == 1  # (the feed watches the link for a plugin back from a reload: once)


def test_the_whole_feed_goes_again_after_a_reload(monkeypatch):
    """A plugin back after a 1012 close gets what was held while it was away, then every event of the run."""
    out = []
    link = bridge.Link(lambda frame: out.append(("old", frame)))
    feed_ = cli.Feed()
    feed_.watch_rebind(link)
    feed_.watch_rebind(link)  # (once)
    assert len(link.rebound) == 1
    feed_.add(link, {"type": "activity", "id": 1, "text": "a"})
    assert link.suspend()
    feed_.add(link, {"type": "activity", "id": 2, "text": "b"})  # (held)
    assert out == [("old", {"type": "activity", "id": 1, "text": "a"})]
    link.rebind(lambda frame: out.append(("new", frame)))
    assert [(where, f["id"]) for where, f in out] == [("old", 1), ("new", 2), ("new", 1), ("new", 2)]
    assert feed_.replay(link, since=2) == 0
    feed_.start()  # (the next run: the feed begins empty)
    assert feed_.replay(link, 0) == 0 and feed_.events == []


def test_the_link_sends_a_status_line_once_however_many_times_it_is_said():
    """The run's setup says "Reading the game's code" and then its session says it again: the tab got the stage
    line twice (seen live). The link drops a status identical to the last one it sent."""
    from autopilot import bridge

    link = bridge.Link.__new__(bridge.Link)
    sent = []
    link.send = sent.append
    for text in ("Reading the game's code", "Reading the game's code", "Trying things in the game",
                 "Reading the game's code"):
        link.status(text)
    assert [f["text"] for f in sent] == ["Reading the game's code", "Trying things in the game",
                                          "Reading the game's code"]


def test_an_episodes_call_is_a_play_event_whatever_stage_line_was_last(game, tmp_path, monkeypatch):
    """The call to play comes before the session says "Trying the harness", so its event was tagged with the stage
    last said (the results of the episode before, seen live under the wrong heading)."""
    from autopilot import codex
    from autopilot.agents import session

    monkeypatch.setattr(codex, "MEMORY", str(tmp_path))
    s = session.Session(game, "fake", "Open the crate", "r1")
    s.say(session.RESULTS)
    assert s.stage == "results"
    s.tool_call("mcp__game__run_harness", {"seconds": 30}, log="tool run_harness: {}")
    assert s.events[-1]["kind"] == "episode" and s.events[-1]["stage"] == "play" and s.stage == "play"
