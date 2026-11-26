"""The user's own Claude Code or Codex as the agent (autopilot/agents/external.py) through `autopilot mcp`
(autopilot/mcp.py): a run started and worked on through the MCP front and the agent service's control endpoint, calls
that take long answered "still going" and given to `wait`, frames handed over as images, the decision model's key,
brain.json, and the agent's transcript kept with a shared run (autopilot/transcripts.py)."""

import base64
import io
import json
import os
import re
import threading
import time
import types

import pytest
from test_autopilot import FakeLink

from autopilot import bridge, cli, codex, dataset, mcp, sandbox, transcripts, vision
from autopilot.agents import external
from autopilot.agents import session as sessionmod
from autopilot.kit.game import Game


def text_of(result):
    return "".join(c.get("text", "") for c in result["content"] if c.get("type") == "text")


def settled(front, result):
    """result, or once it says "still going", what wait gives at last."""
    while "still going" in text_of(result):
        result = front.call("wait", {})
    return result


@pytest.fixture
def started(tmp_path, monkeypatch):
    """A run on the fake game, started by the user's own agent through the MCP front: (front, control, the run's
    report box)."""
    monkeypatch.setattr(codex, "MEMORY", str(tmp_path / "memory"))
    monkeypatch.setattr(mcp, "WAIT_SECONDS", 0.3)
    control = external.Control()
    box = {}

    def starter(params, desk):
        game = Game(FakeLink())
        game.look = {"x": 0.1, "y": 0.1}

        def run():
            box["report"] = external.implement(game, "fake", params["request"], run_id="r1", desk=desk, timeout=120)

        box["thread"] = threading.Thread(target=run, daemon=True)
        box["thread"].start()
        return None

    control.starter = starter
    front = mcp.Server(out=io.StringIO(), find=lambda: ([control.url], None))
    front.client = "claude-code"
    yield front, control, box
    control.close()


def test_a_run_is_worked_on_through_the_mcp_front(started, tmp_path):
    front, control, box = started
    assert "start_run first" in text_of(front.call("probe", {"code": "1"}))
    answer = settled(front, front.call("start_run", {"request": "Verify that the crate opens"}))
    opening = text_of(answer)
    assert not answer["isError"] and opening.startswith("Autopilot run r1-")
    desk = control.desk
    assert front.run == desk.marker and desk.client == "claude-code"
    assert "Request: Verify that the crate opens" in opening and "runs/r1/INSTRUCTIONS.md" in opening
    root = tmp_path / "memory" / "fake"
    instructions = (root / "runs" / "r1" / "INSTRUCTIONS.md").read_text(encoding="utf-8")
    assert "You write the harness" in instructions and "## Your tools here" in instructions
    assert "frame-describer" in instructions and "never the request" in instructions  # (the describer is told no more)
    assert (root / "runs" / "r1" / "REQUEST.md").read_text(encoding="utf-8") in opening

    # The file tools: the game's folder (and the kit) to read, this run's folder, notes.md and lib.py to write
    assert "You write the harness" in text_of(front.call("read_file", {"path": "runs/r1/INSTRUCTIONS.md"}))
    assert text_of(front.call("write_file", {"path": "runs/r1/harness.py", "content": "# h"})).startswith("Wrote")
    assert text_of(front.call("write_file", {"path": "codex/README.md", "content": "x"})).startswith("Refused")

    # The game's tools, with the clock; the report's own rules hold, as for every agent
    # (game calls through settled: with WAIT_SECONDS at 0.3, a slow machine (a CI runner) can take longer than that
    # over the control port, and the call answers "still going")
    assert "no run_harness episode" in text_of(settled(front, front.call("report", {"outcome": "CONFIRMED",
                                                                                   "summary": "s", "evidence": []})))
    assert "min used]" in text_of(settled(front, front.call("snapshot", {})))

    # A call that takes long answers "still going"; other game calls wait for it; wait gives its result
    session = desk.session
    session.run_harness = lambda *a, **k: (time.sleep(1.0), "episode 1: the crate opened")[1]
    assert "still going" in text_of(front.call("run_harness", {"seconds": 20}))
    held = front.call("probe", {"code": "1"})
    assert held["isError"] and "call wait" in text_of(held)
    assert "the crate opened" in text_of(settled(front, front.call("wait", {})))
    assert "Nothing is going" in text_of(front.call("wait", {}))

    # Another front that started no run (Codex starts one for each subagent: the frames' describer) works on the
    # run going
    other = mcp.Server(out=io.StringIO(), find=lambda: ([control.url], None))
    assert "min used]" in text_of(settled(other, other.call("snapshot", {})))
    assert "isn't filmed" in text_of(settled(other, other.call("frames", {"episode": 1})))

    # What the user types in the tab reaches the agent with its next tool result
    session.steering.add("open the other crate")
    assert '"open the other crate"' in text_of(settled(front, front.call("snapshot", {})))

    # The report ends the run: its report says whose agent it was; later calls are told it is over
    session.episodes, session.most_decisions = 1, sessionmod.MIN_JEV_DECISIONS
    # (the episode whose recording is the run's: one that was played, else the report is refused)
    report = {"outcome": "CONFIRMED", "summary": "It opens.", "evidence": ["trace1: opened at 4.2 s"]}
    assert "1 to 1" in text_of(settled(front, front.call("report", {**report, "episode": 3})))
    assert "Reported" in text_of(settled(front, front.call("report", {**report, "episode": 1})))
    box["thread"].join(30)
    report = box["report"]
    assert report["outcome"] == "CONFIRMED" and report["agent"] == "external" and report["client"] == "claude-code"
    assert report["stopped"] is None and report["episode"] == 1
    over = settled(front, front.call("snapshot", {}))
    assert over["isError"] and "has ended" in text_of(over)
    # (frames still answers: a describer may still be looking; this game isn't filmed)
    looked = settled(front, front.call("frames", {"episode": 1}))
    assert not looked["isError"] and "isn't filmed" in text_of(looked)


def test_an_outside_run_logs_what_its_agent_is_given(started, tmp_path):
    front, control, box = started
    settled(front, front.call("start_run", {"request": "Verify that the crate opens"}))
    desk = control.desk
    run = tmp_path / "memory" / "fake" / "runs" / "r1"
    instructions = (run / "INSTRUCTIONS.md").read_text(encoding="utf-8")
    request = (run / "REQUEST.md").read_text(encoding="utf-8")
    with open(desk.session.log, encoding="utf-8") as f:
        lines = [line for line in f.read().splitlines() if "] instructions sizes: " in line]
    assert len(lines) == 1, lines
    sizes = json.loads(lines[0].split("] instructions sizes: ", 1)[1])
    assert sizes == {"instructions": len(instructions), "request": len(request), "start": len(desk.opening),
                     "inline": request in desk.opening}
    assert sizes["inline"] is True  # (the fake game's briefing is short)
    assert "crate" not in lines[0] and "Verify" not in lines[0]  # (numbers only, never the text)
    desk.session.stop = lambda: True  # (the tab's Stop)
    box["thread"].join(30)


def test_an_outside_agent_gets_the_shorter_briefing_and_instructions(started, tmp_path):
    front, control, box = started
    folder = tmp_path / "memory" / "fake" / "codex"
    folder.mkdir(parents=True)
    (folder / "README.md").write_text("".join(f"- Parent_{i} (25): " + "b" * 150 + "\n" for i in range(120)),
                                      encoding="utf-8")
    settled(front, front.call("start_run", {"request": "Verify that the crate opens"}))
    run = tmp_path / "memory" / "fake" / "runs" / "r1"
    request = (run / "REQUEST.md").read_text(encoding="utf-8")
    part = request[request.index("## codex/README.md"):].split("\n\n")[0]
    assert len(part) <= sessionmod.README_PART + 200 and "all of it (120 lines) is in codex/README.md" in part
    # The kit's longer parts are there to read when the instructions say so
    instructions = (run / "INSTRUCTIONS.md").read_text(encoding="utf-8")
    assert "runs/r1/KIT_MORE.md" in instructions and "## Reacting in time" not in instructions
    assert "## Reacting in time" in (run / "KIT_MORE.md").read_text(encoding="utf-8")
    assert "## Reacting in time" in text_of(front.call("read_file", {"path": "runs/r1/KIT_MORE.md"}))
    assert "class Harness" in text_of(front.call("read_file", {"path": "autopilot/kit/harness.py"}))
    control.desk.session.stop = lambda: True  # (the tab's Stop)
    box["thread"].join(30)


def reads(text, tmp_path):
    """How many read_file calls read all of text, as an agent reads INSTRUCTIONS.md."""
    from autopilot.agents.files import FileTools

    (tmp_path / "runs" / "r").mkdir(parents=True, exist_ok=True)
    (tmp_path / "runs" / "r" / "I.md").write_text(text, encoding="utf-8", newline="\n")
    files, calls, offset = FileTools(str(tmp_path), "runs/r", sessionmod.PACKAGE), 0, 1
    while True:
        answer = files.read_file("runs/r/I.md", offset=offset)
        calls += 1
        on = re.search(r"read on from line (\d+)\)$", answer.rsplit("\n", 1)[-1])
        if not on or calls > 20:
            return calls
        offset = int(on.group(1))


def test_an_outside_agents_instructions_are_shorter_and_say_where_the_rest_is(tmp_path):
    from autopilot import engines

    assert sessionmod.CONTRACT in sessionmod.SYSTEM
    for name, engine in engines.ENGINES.items():
        own = sessionmod.system_prompt(engine, "runs/r")
        text = external.instructions_text(engine, "runs/r", "r-1")
        more = sessionmod.kit_more(engine)
        assert len(own) - len(sessionmod.system_prompt(engine, "runs/r", short=True)) >= 21000, name
        # the contract: read in the kit, not quoted
        assert "autopilot/kit/harness.py with read_file" in text and "class Harness" not in text
        assert "The contract's docstrings" not in text
        # the kit's reference sections read on demand: listed, with where they are, and whole in KIT_MORE.md
        assert "runs/r/KIT_MORE.md" in text
        for start in sessionmod.KIT_ON_DEMAND:
            title = next(line[3:] for line in more.splitlines() if line.startswith("## " + start))
            assert f"\n- {title}: " in text and f"\n## {title}\n" not in text, (name, title)
        for kept in ("## Controllers", "## The harness (kit.harness", "## The plan (", "## What makes a harness"):
            assert kept in text and kept not in more, (name, kept)
        if name != "unreal":
            assert "## The engine: " in text
        # nothing of the kit is lost: each of its lines is in the instructions or in KIT_MORE.md
        lost = [line for line in sessionmod.kit_doc(engine).splitlines() if line.strip() and line not in text
                and line not in more]
        assert not lost, (name, lost[:3])
    unreal = external.instructions_text(engines.get("unreal"), "runs/20261124-060037", "20261124-060037-0000abcd")
    assert reads(unreal, tmp_path) == 3  # (it was 4 when the instructions quoted all of the kit)


def test_the_agent_is_told_where_the_rest_of_the_kit_is():
    short = external.start_text("r1-ab", "fake", "Open the crate", "Request: Open the crate\n\nbrief", "runs/r1")
    long = external.start_text("r1-ab", "fake", "Open the crate", "b" * 60000, "runs/r1")
    for text in (short, long):
        assert "runs/r1/KIT_MORE.md" in text and "autopilot/kit/harness.py" in text
    with open(os.path.join(sessionmod.PACKAGE, "assistants", "SKILL.md"), encoding="utf-8") as f:
        skill = f.read()
    assert "KIT_MORE.md" in skill and "autopilot/kit/harness.py" in skill


def test_file_calls_are_never_held_up_by_a_call_still_going(started):
    front, control, box = started
    settled(front, front.call("start_run", {"request": "Reach the gate"}))
    session = control.desk.session
    release = threading.Event()
    session.run_harness = lambda *a, **k: (release.wait(10), "done")[1]
    assert "still going" in text_of(front.call("run_harness", {"seconds": 20}))
    assert not front.call("read_file", {"path": "runs/r1/REQUEST.md"})["isError"]
    release.set()
    session.stop = lambda: True  # (the tab's Stop)
    box["thread"].join(30)
    assert box["report"]["stopped"] == "stopped" and box["report"]["outcome"] == "not_achieved"


def test_an_argument_name_a_tool_doesnt_take_is_refused_never_dropped(started):
    front, control, box = started
    # start_run: refused before a run starts; an argument of another tool is named with it
    refused = front.call("start_run", {"request": "Reach the gate", "frames_fps": 30})
    words = text_of(refused)
    assert refused["isError"] and control.desk is None
    assert words.startswith("start_run: unknown argument(s) frames_fps; it takes request, kind, minutes, "
                            "time_limit_minutes, level, parallel, keep_game_open, headless, project, engine. "
                            "frames_fps is an argument of run_harness.")
    assert "Nothing was done" in words
    settled(front, front.call("start_run", {"request": "Reach the gate"}))
    desk = control.desk
    calls = []
    desk.session.run_harness = lambda *a, **k: calls.append((a, k)) or "episode 1"
    # The game's tools, the report and the file tools, through the front
    refused = front.call("run_harness", {"seconds": 10, "fps": 30, "Restart": False})
    assert refused["isError"] and not calls
    assert text_of(refused).startswith("run_harness: unknown argument(s) fps, Restart; it takes seconds, restart, "
                                       "frames_fps, run.")
    refused = front.call("report", {"outcome": "achieved", "summary": "s", "evidence": [], "notes": "x"})
    assert refused["isError"] and desk.session.report is None
    assert "unknown argument(s) notes; it takes outcome, summary, evidence, findings, visual" in text_of(refused)
    assert "it takes run" in text_of(front.call("snapshot", {"what": "all"}))  # (every call may name its run)
    restarted = text_of(front.call("restart_level", {"episode": 2}))
    assert "episode is an argument of describe_frames and report and frames" in restarted
    assert "unknown argument(s) file" in text_of(front.call("read_file", {"file": "runs/r1/REQUEST.md"}))
    assert "unknown argument(s) timeout" in text_of(front.call("wait", {"timeout": 5}))
    # And at the agent service, for a front older than the check
    direct = control.call("run_harness", {"run": desk.marker, "args": {"seconds": 10, "fps": 30}})
    assert direct["isError"] and "unknown argument(s) fps" in text_of(direct) and not calls
    assert "unknown argument(s) path" in text_of(control.call("frames", {"run": desk.marker, "args": {"path": "x"}}))
    # The arguments a tool takes still go through
    assert "episode 1" in text_of(settled(front, front.call("run_harness", {"seconds": 10, "frames_fps": 30})))
    assert calls == [((10, True), {"frames_fps": 30})]
    desk.session.stop = lambda: True
    box["thread"].join(30)


def test_unknown_args_names_the_tool_an_argument_belongs_to():
    assert sessionmod.unknown_args("probe", {"code": "1"}) is None
    assert sessionmod.unknown_args("nope", {"x": 1}) is None  # (an unknown tool is answered as one)
    why = sessionmod.unknown_args("probe", {"code": "1", "level": "L", "zzz": 2})
    assert why == ("probe: unknown argument(s) level, zzz; it takes code. level is an argument of restart_level. "
                   "Nothing was done: call it again with only the arguments it takes.")


def test_a_start_call_is_told_why_a_run_wont_start():
    control = external.Control()
    try:
        assert "isn't connected to the editor" in text_of(control.start({"request": "x"}))
        control.starter = lambda params, desk: "A run is going in the editor"
        refused = control.start({"request": "x"})
        assert refused["isError"] and "A run is going" in text_of(refused)
        assert "start_run first" in text_of(control.call("probe", {"run": "nope", "args": {}}))
        # (a run that ends before its session opens: Aura refused it, the game didn't start)
        control.starter = lambda params, desk: desk.end({"summary": "Aura refused the run: no credits left"})
        assert "didn't start: Aura refused the run: no credits left" in text_of(control.start({"request": "x"}))
    finally:
        control.close()


def test_sandbox_startup_failure_reaches_the_start_call(tmp_path, monkeypatch):
    monkeypatch.setattr(codex, "MEMORY", str(tmp_path / "memory"))
    monkeypatch.setenv("AUTOPILOT_SANDBOX", "process")

    def fail_sandbox(*args, **kwargs):
        raise sandbox.SandboxError("the sandbox ended (1)")

    monkeypatch.setattr(sandbox, "Sandbox", fail_sandbox)
    control = external.Control()
    worker_done = threading.Event()
    answered = threading.Event()
    result = {}

    def starter(params, desk):
        def run():
            try:
                external.implement(Game(FakeLink()), "fake", params["request"], run_id="r1", desk=desk)
            except Exception as e:
                result["worker_error"] = e
            finally:
                worker_done.set()

        threading.Thread(target=run, daemon=True).start()
        return None

    control.starter = starter

    def start():
        result["answer"] = control.start({"request": "Verify that the crate opens"})
        answered.set()

    try:
        threading.Thread(target=start, daemon=True).start()
        assert worker_done.wait(5), "the sandbox failure wasn't reached"
        assert isinstance(result.get("worker_error"), sandbox.SandboxError)
        assert answered.wait(5), "start_run stayed pending after sandbox startup failed"
        answer = result["answer"]
        assert answer["isError"] and "didn't start: SandboxError: the sandbox ended (1)" in text_of(answer)
        assert control.desk.session is None and control.desk.ready.is_set()
    finally:
        control.close()


def test_the_decision_models_key_is_asked_for_in_the_file_never_in_the_conversation(monkeypatch):
    monkeypatch.delenv("AUTOPILOT_AURA_URL", raising=False)
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    why = cli.decision_key_missing()
    assert "TYPESAFE_API_KEY=" in why and "autopilot.env" in why and "Don't paste the key" in why
    monkeypatch.setenv("TYPESAFE_API_KEY", "ts-test")
    assert cli.decision_key_missing() is None
    monkeypatch.delenv("TYPESAFE_API_KEY")
    monkeypatch.setenv("AUTOPILOT_AURA_URL", "https://aura.example")  # (Aura's runs: Jev through its relay)
    assert cli.decision_key_missing() is None


def test_a_start_call_asks_for_the_run_a_start_frame_would():
    # With no kind given, Jev reads the request's as the run starts (an exploration's minutes: its time limit)
    assert cli.external_run({"request": "Can the player swim?"}) == {"kind": "auto", "prompt": "Can the player swim?"}
    assert cli.external_run({"request": "Sell three crops", "time_limit_minutes": 20}) == {
        "kind": "auto", "prompt": "Sell three crops", "time_limit_minutes": 20}
    run = cli.external_run({"request": "Explore the market stall", "minutes": 5})
    assert run == {"kind": "auto", "prompt": "Explore the market stall", "time_limit_minutes": 5}
    assert cli.prompt_of(run) == "Explore the market stall" and cli.explore_minutes(run) == 5
    run = cli.external_run({"request": "Explore the market stall", "minutes": 5, "kind": "explore"})
    assert run == {"kind": "explore", "brief": "the market stall", "minutes": 5}
    assert cli.prompt_of(run) == "Explore the game for bugs: the market stall (5 minutes of play)"
    assert cli.external_run({"request": "the doors", "kind": "explore"})["kind"] == "explore"
    # (the words the user asked their agent with: "Use Autopilot to ..." is the request without them)
    assert cli.external_run({"request": "Use Autopilot to verify that the crate turns green", "kind": "verify"}) == {
        "kind": "verify", "claim": "Verify that the crate turns green"}
    goal = cli.external_run({"request": "please have autopilot light a lantern", "kind": "goal"})
    assert goal["goal"] == "Light a lantern"


def test_frames_are_handed_over_as_images_and_count_for_a_visual_report(tmp_path, monkeypatch):
    monkeypatch.setattr(codex, "MEMORY", str(tmp_path / "memory"))
    session = sessionmod.Session(Game(FakeLink()), "fake", "Verify that the door swings open", "r1")
    session.looks_itself = True
    session.marker = "r1-abcd"
    kept = vision.Frames()
    t0 = time.time()
    for i in range(20):
        kept.add(f"jpeg{i}".encode(), t=i * 0.25, wall=t0 + i * 0.25)
    link = types.SimpleNamespace(hello={"features": ["media"]})
    recorder = types.SimpleNamespace(frames=kept, link=link, received=True, problem=None, missing=lambda: None)
    session.describer = vision.Describer(recorder, "deepseek")  # (no vision model of ours: the user's agent looks)
    assert "describer subagent" in session.looking_note()
    assert "No episode has been played" in session.frames()[0]
    with open(os.path.join(session.run_dir, "trace1.jsonl"), "w", encoding="utf-8") as f:
        f.write(json.dumps({"kind": "start", "wall": t0}) + "\n")
        f.write(json.dumps({"kind": "obs", "wall": t0, "t": 30.0}) + "\n")  # (the game's clock: 30 s at its start)
        f.write(json.dumps({"kind": "end", "wall": t0 + 5, "t": 34.75}) + "\n")
    for frame in kept.items:
        frame["t"] += 30.0
    session.episodes, session.most_decisions = 1, sessionmod.MIN_JEV_DECISIONS
    refused = session.do_report({"outcome": "CONFIRMED", "summary": "s", "evidence": [], "visual": True})
    assert "describer subagent" in refused
    # Times are seconds from the episode's start, as its summary's t= gives them (not the game's clock)
    text, jpegs = session.frames(1, start=1.0, end=3.0, count=4)
    assert len(jpegs) == 4 and jpegs[0] == b"jpeg4" and "(run r1-abcd)" in text and "at t= 1.00, " in text
    assert "31.00" not in text
    assert session.do_report({"outcome": "CONFIRMED", "summary": "s", "evidence": [], "visual": True}).startswith(
        "Reported")

    desk = external.Desk("codex")
    desk.marker = session.marker
    desk.open(session, None, "opening")
    session.report = None
    result = desk.call("frames", {"episode": 1, "count": 2})
    images = [c for c in result["content"] if c["type"] == "image"]
    assert len(images) == 2 and images[0]["mimeType"] == "image/jpeg"
    # (an answer with no frames names the run too: the describer's transcript is found by it)
    missed = desk.call("frames", {"episode": 1, "start": 50, "end": 60})
    assert "No frames of episode 1" in text_of(missed) and "(run r1-abcd)" in text_of(missed)
    assert not [c for c in missed["content"] if c["type"] == "image"]

    # Once the run has ended (reported), a describer still looking gets the frames the run kept, and nothing else
    # happens: no feed line, no status for the tab; other game tools are refused
    desk.end("reported")
    events, said = len(session.events), []
    session.say = said.append
    after = desk.call("frames", {"episode": 1, "start": 1.0, "end": 3.0, "count": 3})
    assert not after["isError"] and len([c for c in after["content"] if c["type"] == "image"]) == 3
    assert "at t= 1.00, " in text_of(after) and "(run r1-abcd)" in text_of(after) and "has ended" in text_of(after)
    assert "min used]" not in text_of(after)  # (no clock: the run is over)
    assert len(session.events) == events and not said
    # (an argument frames doesn't take is refused then too, as while the run went)
    wrong = desk.call("frames", {"episode": 1, "frames": 3})
    assert wrong["isError"] and "frames: unknown argument(s) frames" in text_of(wrong)
    assert desk.call("snapshot", {})["isError"] and "has ended" in text_of(desk.call("snapshot", {}))
    gone = desk.call("describe_frames", {"questions": ["What moves?"]})
    assert gone["isError"] and "`frames`" in text_of(gone)
    # Only once the run's files are gone does it refuse
    import shutil

    shutil.rmtree(session.run_dir)
    lost = desk.call("frames", {"episode": 1})
    assert "files are gone" in text_of(lost) and not [c for c in lost["content"] if c["type"] == "image"]


def players_session(tmp_path, monkeypatch, players=2, looks_itself=True, share=False):
    """A session whose run has played an episode, with a recorder stub as media.Recorder is in a game of several
    players: player 1's, its `others` the other players' (each with frames of its own)."""
    monkeypatch.setattr(codex, "MEMORY", str(tmp_path / "memory"))
    session = sessionmod.Session(Game(FakeLink()), "fake", "Reach the gate together", "r1")
    session.looks_itself = looks_itself
    session.marker = "r1-abcd" if looks_itself else None
    base = os.path.join(session.run_dir, "telemetry") if share else session.run_dir
    link = types.SimpleNamespace(hello={"features": ["media", "players", "instances"]})

    def stub(player):
        return types.SimpleNamespace(frames=vision.Frames(), link=link, received=True, problem=None, confirmed=True,
                                     missing=lambda: None, player=player, base=base,
                                     folder=os.path.join(base, "media"), others={}, players_missing=lambda e=None: {})

    recorder = stub(1)
    recorder.others = {k: stub(k) for k in range(2, players + 1)}
    session.describer = vision.Describer(recorder, "deepseek")
    session.episodes, session.most_decisions = 1, sessionmod.MIN_JEV_DECISIONS
    return session, recorder


def test_the_outside_agents_report_names_every_players_video(tmp_path, monkeypatch):
    report = {"outcome": "achieved", "summary": "Both reached the gate", "evidence": []}
    session, _ = players_session(tmp_path, monkeypatch)
    answer = session.do_report(dict(report))
    assert answer.startswith("Reported. You can stop now.\n")
    # Paths in this game's folder, as its file tools take them; the one the tab plays is said
    assert "player 1: runs/r1/media/recording-1.mp4" in answer
    assert "player 2: runs/r1/media/recording-1-p2.mp4" in answer
    assert "the Autopilot tab plays player 1's" in answer
    # The episode the report names, in a shared run's folder; 3 players
    session, _ = players_session(tmp_path, monkeypatch, players=3, share=True)
    session.episodes = 2
    answer = session.do_report(dict(report, episode=1))
    assert "episode 1" in answer and "runs/r1/telemetry/media/recording-1-p3.mp4" in answer
    assert "recording-2" not in answer
    # A player the plugin never pictured has no video to name
    session, recorder = players_session(tmp_path, monkeypatch, players=3)
    recorder.others[3].confirmed = False
    answer = session.do_report(dict(report))
    assert "recording-1-p2.mp4" in answer and "-p3" not in answer


def test_a_one_player_or_inner_agents_report_answer_is_as_before(tmp_path, monkeypatch):
    report = {"outcome": "achieved", "summary": "Reached the gate", "evidence": []}
    session, _ = players_session(tmp_path, monkeypatch, players=1)
    assert session.do_report(dict(report)) == "Reported. You can stop now."
    # An agent Autopilot runs itself (not the outside agent): as before, in a game of several players too
    session, _ = players_session(tmp_path, monkeypatch, players=2, looks_itself=False)
    assert session.do_report(dict(report)) == "Reported. You can stop now."
    # Every other player's never pictured: nothing to name
    session, recorder = players_session(tmp_path, monkeypatch, players=2)
    recorder.others[2].confirmed = False
    assert session.do_report(dict(report)) == "Reported. You can stop now."


def filmed_players(tmp_path, monkeypatch, **k):
    """players_session with episode 1 played and filmed: player 1's frames at game times 30.0, 30.25... (its summary's
    t=0 is 30.0), player 2's a little after each (0.1 s) on a game clock of its own, 0.83 s behind player 1's (each
    player's world keeps its own time). (session, recorder)"""
    session, recorder = players_session(tmp_path, monkeypatch, **k)
    t0 = time.time()
    for i in range(20):
        recorder.frames.add(f"p1-{i}".encode(), t=30.0 + i * 0.25, wall=t0 + i * 0.25)
        if 2 in recorder.others:
            recorder.others[2].frames.add(f"p2-{i}".encode(), t=29.17 + 0.1 + i * 0.25, wall=t0 + 0.1 + i * 0.25)
    with open(os.path.join(session.run_dir, "trace1.jsonl"), "w", encoding="utf-8") as f:
        f.write(json.dumps({"kind": "start", "wall": t0}) + "\n")
        f.write(json.dumps({"kind": "obs", "wall": t0, "t": 30.0}) + "\n")
        f.write(json.dumps({"kind": "end", "wall": t0 + 5, "t": 34.75}) + "\n")
    return session, recorder


def test_frames_of_another_player_are_that_players(tmp_path, monkeypatch):
    session, recorder = filmed_players(tmp_path, monkeypatch)
    # Player 2's frames of the same stretch, its times on player 1's clock (the summary's t=, the only one the agent
    # knows): player 2's frame 0.1 s after player 1's t=1.00 is at t=1.10
    text, jpegs = session.frames(1, start=0.9, end=3.0, count=4, player=2)
    assert jpegs == [b"p2-4", b"p2-6", b"p2-9", b"p2-11"]
    assert text == ("4 frames of 8 (episode 1, player 2), in order, at t= 1.10, 1.60, 2.35, 2.85 s (seconds from the "
                    "episode's start, as its summary's t=) (run r1-abcd):")
    assert session.events[-1]["text"] == "Took 4 frames of episode 1, player 2"
    # Without player 1's frames to set the clocks side by side, the episode's start does it
    recorder.frames.items.clear()
    assert session.frames(1, start=0.9, end=3.0, count=4, player=2) == (text, jpegs)
    # Through the tool, while the run goes and once it has ended
    desk = external.Desk("codex")
    desk.marker = session.marker
    desk.open(session, None, "opening")
    result = desk.call("frames", {"episode": 1, "start": 0.9, "end": 3.0, "count": 2, "player": 2})
    images = [base64.b64decode(c["data"]) for c in result["content"] if c["type"] == "image"]
    assert images == [b"p2-4", b"p2-11"] and "player 2" in text_of(result)
    desk.end("reported")
    after = desk.call("frames", {"episode": 1, "start": 0.9, "end": 3.0, "count": 2, "player": 2})
    images = [base64.b64decode(c["data"]) for c in after["content"] if c["type"] == "image"]
    assert images == [b"p2-4", b"p2-11"] and "has ended" in text_of(after)


def test_frames_of_a_player_not_filmed_says_so(tmp_path, monkeypatch):
    session, recorder = filmed_players(tmp_path, monkeypatch)
    events = len(session.events)
    assert session.frames(1, player=3) == ("No frames of player 3: only players 1 and 2 are filmed in this run.", [])
    for wrong in (0, -1, "two", 1.5, True):
        text, jpegs = session.frames(1, player=wrong)
        assert text.startswith(f"No player {wrong!r} here: player is a player's number") and not jpegs
    assert not session.alerts  # (the agent's to fix)
    # The reason the run knows, when it knows one: a plugin that records player 1 only
    session, recorder = filmed_players(tmp_path, monkeypatch, players=1)
    why = "this plugin records player 1 only (an Autopilot plugin built before per-player recording, or Roblox Studio)"
    recorder.players_missing = lambda episode=None: {2: why}
    assert session.frames(1, player=2) == (f"No frames of player 2: {why}.", [])
    assert session.frames(1, player=3) == ("No frames of player 3: only player 1 is filmed in this run.", [])
    assert len(session.events) >= events
    # A player recorded but not seen in the stretch: no alert about the game's window (player 1's picture came)
    session, recorder = filmed_players(tmp_path, monkeypatch, players=3)
    text, jpegs = session.frames(1, player=3)
    assert text.startswith("No frames of episode 1, player 3") and not jpegs and not session.alerts


def test_why_a_player_has_no_frames_names_no_full_path(tmp_path, monkeypatch):
    """A player whose video couldn't be made says where its pictures are: in the tool's answer (which the agent may
    copy into its report, which is synced) that folder is named in the game's folder, as report answers name videos."""
    session, recorder = filmed_players(tmp_path, monkeypatch, players=3)
    pictures = os.path.join(session.run_dir, "media", "frames-1-p3")
    recorder.players_missing = lambda episode=None: {
        3: f"the video couldn't be made (RuntimeError: x): its pictures are in {pictures}"}
    why = "No frames of episode 1, player 3: the video couldn't be made (RuntimeError: x): its pictures are in "
    text, jpegs = session.frames(1, player=3)
    assert text == f"{why}runs/r1/media/frames-1-p3." and not jpegs
    session.describer.unavailable = lambda: None  # (a describer that could look: the answer comes before it does)
    session.looks_itself = False
    assert session.describe_frames(["Where is the gate?"], episode=1, player=3) == text


def test_frames_without_player_are_player_ones_as_before(tmp_path, monkeypatch):
    session, _ = filmed_players(tmp_path, monkeypatch)
    text, jpegs = session.frames(1, start=0.9, end=3.0, count=4)
    assert jpegs == [b"p1-4", b"p1-7", b"p1-9", b"p1-12"]
    assert text == ("4 frames of 9 (episode 1), in order, at t= 1.00, 1.75, 2.25, 3.00 s (seconds from the episode's "
                    "start, as its summary's t=) (run r1-abcd):")
    shown = {k: session.events[-1][k] for k in ("kind", "text", "detail")}
    assert shown == {"kind": "look", "text": "Took 4 frames of episode 1",
                     "detail": "At t= 1.00, 1.75, 2.25, 3.00 s of it"}
    assert session.frames(1, start=0.9, end=3.0, count=4, player=1) == (text, jpegs)
    assert {k: session.events[-1][k] for k in ("kind", "text", "detail")} == shown
    assert session.frames(1, start=0.9, end=3.0, count=4, player=None) == (text, jpegs)


def test_frames_and_describe_frames_take_a_player():
    tools = {t["name"]: t for t in mcp.tools()}
    for name in ("frames", "describe_frames"):
        assert tools[name]["inputSchema"]["properties"]["player"] == {"type": "integer"}
        assert "player: whose view, in a game of several players (default 1)" in tools[name]["description"]
        assert "player" not in tools[name]["inputSchema"].get("required", [])
    assert "player" in dict((n, s) for n, _, s in sessionmod.TOOL_SPECS)["describe_frames"]["properties"]
    assert unknown_args_none("frames", {"episode": 1, "player": 2})
    assert unknown_args_none("describe_frames", {"questions": ["?"], "player": 2})
    # The describer is briefed how to ask for another player's view
    assert "player=<n: only for another player's view>" in external.EXTERNAL_TOOLS


def unknown_args_none(tool, args):
    return sessionmod.unknown_args(tool, args, sessionmod.TOOL_SPECS + (external.FRAMES_SPEC,)) is None


def test_the_players_note_says_each_player_is_filmed_when_the_plugin_records_each():
    net = {"mode": "listen_server", "players": 2, "player": 1}
    before = sessionmod.players_note(net)
    filmed = sessionmod.players_note(net, ["media", "players", "instances"])
    assert filmed == before + " Each player is filmed: frames and describe_frames take player=n for player n's view."
    # A plugin that records player 1 only, or one player: as before
    assert sessionmod.players_note(net, ["media", "players"]) == before
    assert sessionmod.players_note({"mode": "listen_server", "players": 1}, ["players", "instances"]) == ""
    host = sessionmod.network_note(net, None, ["players", "instances"])
    assert host.endswith("Each player is filmed: frames and describe_frames take player=n for player n's view.")
    assert sessionmod.network_note(net) == sessionmod.network_note(net, None, ["players"])
    client = sessionmod.network_note({"mode": "client", "server_world": "/Game/UEDPIE_0_Map.Map", "clients": 2,
                                      "players": 2}, None, ["players", "instances"])
    assert "Each player is filmed" in client


def test_the_mcp_front_lists_its_tools_and_finds_the_service(tmp_path, monkeypatch):
    out = io.StringIO()
    server = mcp.Server(out=out)
    server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                   "params": {"protocolVersion": "2025-06-18", "clientInfo": {"name": "codex-mcp-client"}}})
    server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    init, listed = (json.loads(line) for line in out.getvalue().splitlines())
    assert server.client == "codex"
    assert init["result"]["instructions"].startswith("Autopilot playtests the game") and \
        "start_run" in init["result"]["instructions"][:250]  # (Codex shows as little as 250 characters)
    tools = {t["name"]: t for t in listed["result"]["tools"]}
    assert {"start_run", "wait", "probe", "run_harness", "frames", "describe_frames", "report", "read_file",
            "write_file"} <= set(tools)
    assert tools["snapshot"]["annotations"]["readOnlyHint"] and not tools["probe"]["annotations"]["readOnlyHint"]
    assert tools["describe_frames"]["description"].startswith("Fallback only")
    # (times as the summaries give them; the report names the episode whose recording is the run's)
    for name in ("frames", "describe_frames"):
        assert "seconds from its start" in tools[name]["description"] and "game times" not in tools[name]["description"]
    assert tools["report"]["inputSchema"]["properties"]["episode"] == {"type": "integer"}
    assert "recording" in tools["report"]["description"]
    assert tools["start_run"]["inputSchema"]["required"] == ["request", "kind"]  # (a claim isn't taken for a goal)

    monkeypatch.setattr(bridge, "brain_file", lambda name=bridge.BRAIN_FILE: str(tmp_path / name))
    assert mcp.control_urls() == ([], mcp.NOT_RUNNING)
    (tmp_path / "brain.json").write_text(json.dumps({"url": "ws://127.0.0.1:1/v1/bridge", "started": 1}))
    assert mcp.control_urls() == ([], mcp.TOO_OLD)
    (tmp_path / "aura-brain.json").write_text(json.dumps({"url": "ws://x", "started": 2, "control": "http://a/b"}))
    assert mcp.control_urls() == (["http://a/b"], None)
    (tmp_path / "brain.json").write_text(json.dumps({"url": "ws://y", "started": 3, "control": "http://c/d"}))
    assert mcp.control_urls() == (["http://c/d", "http://a/b"], None)
    bridge.announce("ws://127.0.0.1:2/v1/bridge", str(tmp_path / "own.json"), {"control": "http://e/f"})
    assert json.loads((tmp_path / "own.json").read_text())["control"] == "http://e/f"
    # A program that runs an agent service of its own names the one this server uses: no other is looked for
    monkeypatch.setenv("AUTOPILOT_MCP_BRAIN", str(tmp_path / "own.json"))
    assert mcp.control_urls() == (["http://e/f"], None)
    (tmp_path / "old.json").write_text(json.dumps({"url": "ws://z", "started": 9}))
    monkeypatch.setenv("AUTOPILOT_MCP_BRAIN", str(tmp_path / "old.json"))
    assert mcp.control_urls() == ([], mcp.TOO_OLD)
    monkeypatch.setenv("AUTOPILOT_MCP_BRAIN", str(tmp_path / "none.json"))
    assert mcp.control_urls() == ([], mcp.NOT_RUNNING)  # (not the others': the one named isn't running)
    monkeypatch.delenv("AUTOPILOT_MCP_BRAIN")
    assert mcp.control_urls() == (["http://c/d", "http://a/b"], None)


def test_a_start_call_says_where_it_was_asked_for_when_the_server_was_told(monkeypatch):
    monkeypatch.delenv("AUTOPILOT_MCP_TRACE", raising=False)
    assert mcp.run_trace() is None
    asked = []
    front = mcp.Server(out=io.StringIO(), find=lambda: (["http://control"], None))
    front.client = "codex"
    monkeypatch.setattr(front, "post",
                        lambda url, body: asked.append((url, body)) or mcp.text("Autopilot run r1-ab started"))
    front.forward("start_run", {"request": "The gate opens", "kind": "verify"})
    assert asked[-1] == ("http://control/start", {"request": "The gate opens", "kind": "verify", "client": "codex"})
    monkeypatch.setenv("AUTOPILOT_MCP_TRACE", json.dumps({"thread_id": "t1", "tool": "playtest"}))
    front.forward("start_run", {"request": "The gate opens", "kind": "verify"})
    assert asked[-1][1] == {"request": "The gate opens", "kind": "verify", "client": "codex",
                            "trace": {"thread_id": "t1", "tool": "playtest"}}
    for told in ("not json", "[1]", "{}", ""):  # (anything but an object with something in it: no trace)
        monkeypatch.setenv("AUTOPILOT_MCP_TRACE", told)
        assert mcp.run_trace() is None
    # (the run the agent service makes of the call keeps it, as a start frame's run does)
    run = cli.external_run({"request": "The gate opens", "kind": "verify", "trace": {"thread_id": "t1"}})
    assert run == {"kind": "verify", "claim": "The gate opens", "trace": {"thread_id": "t1"}}
    assert "trace" not in cli.external_run({"request": "The gate opens", "kind": "verify", "trace": "t1"})


def test_the_run_goes_to_the_agent_service_the_editor_is_connected_to(started):
    # The Aura app's brain started after the editor's agent service and has no editor connected (2026-10-29: BoatGame's
    # editor was connected, start_run went to the Aura app's brain and was told "isn't connected"); a service that has
    # ended (its brain file left behind) is passed over too
    front, control, box = started
    idle = external.Control()
    gone = "http://127.0.0.1:9/gone"
    try:
        front.find = lambda: ([gone, idle.url, control.url], None)
        answer = settled(front, front.call("start_run", {"request": "Verify that the crate opens"}))
        assert not answer["isError"] and text_of(answer).startswith("Autopilot run r1-")
        assert front.control == control.url
        assert "connected" not in answer and "reached" not in answer  # (the flags are the front's, not the client's)

        # Another front that started no run finds the service with the run going
        other = mcp.Server(out=io.StringIO(), find=lambda: ([gone, idle.url, control.url], None))
        assert "min used]" in text_of(settled(other, other.call("snapshot", {})))

        # With no editor connected anywhere, the user's agent is told so (not that a service couldn't be reached)
        lone = mcp.Server(out=io.StringIO(), find=lambda: ([gone, idle.url], None))
        refused = settled(lone, lone.call("start_run", {"request": "x"}))
        assert refused["isError"] and "isn't connected to the editor" in text_of(refused) and "connected" not in refused
        alone = mcp.Server(out=io.StringIO(), find=lambda: ([gone], None))
        assert "couldn't be reached" in text_of(settled(alone, alone.call("start_run", {"request": "x"})))
        # (an agent service older than the flags says the same in words)
        old = external.text_result("Autopilot isn't connected to the editor yet: open ...", True)
        assert mcp.passes_on(old, "connected")
        assert not mcp.passes_on(external.text_result("A run is going in the editor", True), "connected")
    finally:
        if control.desk is not None and control.desk.session is not None:
            control.desk.session.stop = lambda: True  # (the tab's Stop)
            box["thread"].join(30)
        idle.close()


def _lines(path, entries):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")


def test_the_users_agents_transcript_is_kept_from_the_start_call_on_without_pictures(tmp_path, monkeypatch):
    claude, codex_home = tmp_path / "claude", tmp_path / "codex"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    marker = "r1-abcd1234"
    project = claude / "projects" / "C--work"
    _lines(project / "s1.jsonl", [
        {"type": "user", "message": "fix my shader first"},  # (the user's other work, before the run)
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "mcp__autopilot__start_run"}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "content": [
            {"type": "text", "text": f"Autopilot run {marker} started on fake: ..."}]}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "content": [
            {"type": "image", "source": {"type": "base64", "data": "QUJD" * 100}}]}]}},
    ])
    _lines(project / "s1" / "subagents" / "agent-1.jsonl", [
        {"type": "user", "message": "Call the autopilot tool frames with episode=1"},
        {"type": "user", "message": {"content": [{"type": "text", "text": f"8 frames of 40 (run {marker}):"},
                                                 {"type": "image", "source": {"type": "base64", "data": "QUJD"}}]}},
    ])
    _lines(project / "other.jsonl", [{"type": "user", "message": "another session"}])
    # (a session that only mentions the run, the user's pasting its id: not the run's)
    _lines(project / "mention.jsonl", [{"type": "user", "message": f"what was run {marker} about?"}])
    _lines(codex_home / "sessions" / "2026" / "10" / "20" / "rollout-x.jsonl",
           [{"payload": {"type": "input_image", "image_url": "data:image/jpeg;base64,QUJD"}}])
    out = tmp_path / "telemetry" / "transcript.jsonl"
    said = transcripts.keep(marker, time.time() - 5, str(out))
    assert said.startswith("5 lines from 2 file(s)")
    kept = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    main = [k for k in kept if k["file"] == "main"]
    assert [k["line"] for k in main] == [2, 3, 4]  # (from the start_run call on)
    assert main[0]["client"] == "claude-code" and "fix my shader" not in out.read_text(encoding="utf-8")
    assert transcripts.IMAGE in json.dumps(main[2]) and "QUJDQUJD" not in out.read_text(encoding="utf-8")
    assert [k["file"] for k in kept[3:]] == ["subagent 1"] * 2
    assert transcripts.keep("r9-none", time.time() - 5, str(tmp_path / "none.jsonl")) is None

    # Codex: the start call's answer names the tool in the same line; a picture inside a longer text goes too
    other = "r2-ffff0000"
    _lines(codex_home / "sessions" / "2026" / "10" / "20" / "rollout-main.jsonl", [
        {"type": "event_msg", "payload": {"message": "earlier work"}},
        {"type": "response_item", "payload": {"type": "function_call", "name": "mcp__autopilot__start_run"}},
        {"type": "event_msg", "payload": {"invocation": {"tool": "start_run"}, "result": {"content": [
            {"type": "text", "text": f"Autopilot run {other} started on fake: ..."}]}}},
        {"type": "event_msg", "payload": {"text": "see data:image/png;base64,QUJDRA== here"}},
    ])
    out2 = tmp_path / "codex.jsonl"
    assert transcripts.keep(other, time.time() - 5, str(out2)).startswith("3 lines from 1 file(s)")
    kept2 = [json.loads(line) for line in out2.read_text(encoding="utf-8").splitlines()]
    assert {k["file"] for k in kept2} == {"main"} and kept2[0]["client"] == "codex"
    assert "QUJDRA" not in out2.read_text(encoding="utf-8")
    old = time.time() - 3600
    os.utime(project / "s1.jsonl", (old, old))  # (a file not written to during the run isn't read)
    assert transcripts.keep(marker, time.time() - 5, str(out)).startswith("2 lines from 1 file(s)")


def test_a_shared_runs_transcript_goes_with_its_data(tmp_path):
    run_dir = tmp_path / "runs" / "r1"
    (run_dir / "telemetry").mkdir(parents=True)
    (run_dir / "telemetry" / "transcript.jsonl").write_text('{"client": "codex"}\n', encoding="utf-8")
    planned = {dst for _, dst, _ in dataset.plan(str(run_dir))}
    assert "agent/transcript.jsonl.gz" in planned
