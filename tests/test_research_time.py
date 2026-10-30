"""The time a run's reading and probing have before its first episode (agents/session.py, RESEARCH_SECONDS): the
Analyze and Probe stages together, however often the run goes back and forth, from when the game is ready; writing the
harness doesn't use it. Once it is up the run builds the harness: probes are refused, the stage line stays Build, and
the Claude Code agent reads only what the harness is built from."""

import asyncio
import os
import time

import pytest
from test_autopilot import FakeJev, FakeLink

from autopilot.agents import claude_code as cc
from autopilot.agents import session
from autopilot.kit.game import Game


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def clock(monkeypatch):
    c = Clock(1000.0)
    monkeypatch.setattr(time, "time", c)
    return c


def open_run(tmp_path, monkeypatch, ready_at=None):
    from autopilot import codex

    monkeypatch.setattr(codex, "MEMORY", str(tmp_path))
    monkeypatch.setenv("AUTOPILOT_RESEARCH_SECONDS", "600")
    game = Game(FakeLink())
    if ready_at is not None:
        game.ready_at = ready_at
    frames, said = [], []
    s = session.Session(game, "fake", "Open the crate", "r1", say=said.append, activity=frames.append,
                        client=FakeJev("open_crate"))
    return s, frames, said


def test_reading_and_probing_share_the_time_and_writing_the_harness_uses_none(tmp_path, monkeypatch, clock):
    s, frames, said = open_run(tmp_path, monkeypatch, ready_at=990.0)  # (10 s of reading before the agent started)
    s.say(session.READING)
    clock.now = 1100.0
    s.say(session.PROBING)
    clock.now = 1150.0
    s.say(session.READING)  # (back and forth: one budget)
    clock.now = 1200.0
    s.say(session.WRITING)
    clock.now = 1500.0  # (300 s of writing the harness: not counted)
    assert s.research_left() == pytest.approx(600 - 210)
    s.say(session.PROBING)
    clock.now = 1600.0
    assert s.research_left() == pytest.approx(600 - 310)
    assert "reading and probing: 5.2 of 10 min used]" in s.clock()
    clock.now = 1800.0
    assert "90 s of reading and probing left" in s.clock()
    assert not s.research_over()
    clock.now = 1900.0
    assert s.research_over()
    s.say(session.READING)  # (past it, reading and probing are building the harness)
    s.say(session.PROBING)
    assert said == [session.READING, session.PROBING, session.READING, session.WRITING, session.PROBING,
                    session.WRITING]
    assert s.stage == "build" and s.research_left() == pytest.approx(-10.0)
    assert "The time for reading and probing is up" in s.clock()
    assert "the 10 minutes of reading and probing are up" in open(s.log, encoding="utf-8").read()


def test_a_probe_is_refused_once_the_time_is_up_until_an_episode_has_played(tmp_path, monkeypatch, clock):
    s, frames, said = open_run(tmp_path, monkeypatch)
    ran = []
    monkeypatch.setattr(s, "_probe", lambda code, keep_running=False, seconds=None: ran.append(seconds) or "ok")
    s.say(session.READING)
    clock.now = 1000.0 + 600 - 30
    assert s.probe("print(1)") == "ok" and ran == [pytest.approx(30.0)]  # (cut off when the time runs out)
    clock.now = 1000.0 + 601
    text = s.probe("print(1)")
    assert text.startswith("Not run: the 10 minutes of reading and probing") and "runs/r1/harness.py" in text
    assert len(ran) == 1
    assert frames[-1]["kind"] == "probe" and frames[-1]["text"].startswith("Not run")
    s.looking = True  # (an episode played: probes work on its results, with their own time)
    assert s.research_left() is None and not s.research_over()
    assert s.probe("print(2)") == "ok" and ran[-1] == session.PROBE_SECONDS
    s.say(session.PROBING)
    assert said[-1] == session.RESULTS
    assert "reading and probing" not in s.clock()


def test_a_probe_cut_off_by_the_time_says_so(tmp_path, monkeypatch):
    s, frames, said = open_run(tmp_path, monkeypatch)
    text = s._probe("import time as t\nprint('before')\nt.sleep(1.0)\nprint('after')", seconds=0.2)
    assert "before" in text and "THE TIME FOR READING AND PROBING RAN OUT DURING THE PROBE" in text
    assert frames[-1]["text"] == "Cut off after 0 s"


def test_the_research_time_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("AUTOPILOT_RESEARCH_SECONDS", "300")
    assert session.research_seconds() == 300
    text = session.system_prompt(Game(FakeLink()).engine, "runs/r1")
    assert "have 5 minutes in all before the first episode" in text.replace("\n   ", " ")
    assert "{research_minutes" not in text
    monkeypatch.setenv("AUTOPILOT_RESEARCH_SECONDS", "soon")
    assert session.research_seconds() == session.RESEARCH_SECONDS
    monkeypatch.delenv("AUTOPILOT_RESEARCH_SECONDS")
    assert session.research_seconds() == 600


def test_claude_code_reads_only_what_the_harness_is_built_from_once_the_time_is_up(tmp_path):
    root = tmp_path / "memory" / "Farm"
    (root / "runs" / "r1").mkdir(parents=True)
    (root / "codex").mkdir()
    over = {"now": False}
    s = type("S", (), {"root": str(root), "run_dir": str(root / "runs" / "r1"), "run_rel": "runs/r1",
                       "research_budget": 600.0, "research_over": lambda self: over["now"]})()
    hook = cc._guard(s)["PreToolUse"][0].hooks[0]

    def ask(name, args):
        out = asyncio.run(hook({"tool_name": name, "tool_input": args}, "toolu_x", {}))
        spec = out.get("hookSpecificOutput") or {}
        return spec.get("permissionDecision", "allow"), spec.get("permissionDecisionReason", "")

    codex_file = {"file_path": os.path.join(str(root), "codex", "README.md")}
    assert ask("Read", codex_file) == ("allow", "")
    over["now"] = True
    decision, why = ask("Read", codex_file)
    assert decision == "deny" and "10 minutes of reading and probing" in why and "runs/r1/harness.py" in why
    assert ask("Grep", {"pattern": "Health"})[0] == "deny"  # (the whole folder: the codex)
    assert ask("Agent", {"subagent_type": cc.RESEARCHER, "prompt": "where?"})[0] == "deny"
    assert ask("Read", {"file_path": os.path.join(str(root), "runs", "r1", "harness.py")}) == ("allow", "")
    assert ask("Read", {"file_path": os.path.join(str(root), "notes.md")}) == ("allow", "")
    assert ask("Read", {"file_path": os.path.join(session.PACKAGE, "KIT.md")}) == ("allow", "")
    assert ask("Write", {"file_path": os.path.join(str(root), "runs", "r1", "harness.py")}) == ("allow", "")
    assert ask("mcp__game__run_harness", {"seconds": 60}) == ("allow", "")
