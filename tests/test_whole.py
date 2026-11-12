"""Nothing an agent or the user is given is cut (autopilot/agents/whole.py): what fits is given whole; what is longer
than its budget is written whole to a file, and what is given says where, with the whole lines that fit."""

import json
import os
import re
import types

import pytest
from test_activity import harness_file, open_run
from test_autopilot import FakeLink

from autopilot import cli, runtime
from autopilot.agents import external, session, whole
from autopilot.agents.files import FileTools
from autopilot.kit.game import Game
from autopilot.words import change_text


@pytest.fixture
def game():
    g = Game(FakeLink())
    g.look = {"x": 0.1, "y": 0.1}
    return g


def kept_path(text):
    """The file a delivered text says all of it is in."""
    return re.search(r"all of it \(\d+ lines\) is in (\S+?)\.(?:\s|\])", text).group(1)


# -- the mechanism ---------------------------------------------------------------------------------------------------

def test_what_fits_is_given_whole_and_the_rest_is_pointed_to():
    assert whole.deliver("a\nb", 10, "x.txt") == "a\nb"
    text = "\n".join(f"line {i}" for i in range(1000))
    out = whole.deliver(text, 2000, "runs/r1/whole/x-1.txt", what="The probe's output")
    note, *lines = out.split("\n")
    assert len(out) <= 2000 and lines == [f"line {i}" for i in range(len(lines))] and len(lines) > 100
    assert note.startswith(f"[The probe's output is {len(text):,} characters, more than one tool result holds: all of "
                           "it (1000 lines) is in runs/r1/whole/x-1.txt.")
    assert f"Lines 1-{len(lines)} follow; read on from line {len(lines) + 1} of that file.]" in note
    # one line longer than the budget: the part of it that fits, and where it goes on
    out = whole.deliver("y" * 5000 + "\nz", 1000, "f.txt")
    note, part = out.split("\n")
    assert len(out) <= 1000 and set(part) == {"y"} and f"read on from line 1, column {len(part) + 1:,}" in note
    # for the user: no tool's words
    assert "read on" not in whole.deliver(text, 2000, "C:/x.txt", ui=True)
    assert whole.next_name(os.path.dirname(__file__), "nothing-here") == "nothing-here-1.txt"


def test_the_budget_is_under_what_an_mcp_client_takes():
    # Claude Code refuses an MCP result over 25,000 tokens; plain text is at least ~1 character a token
    assert whole.RESULT_CHARS <= 25000 and whole.ANSWER_CHARS < whole.RESULT_CHARS


# -- the file tools --------------------------------------------------------------------------------------------------

def files_in(tmp_path):
    (tmp_path / "runs" / "r1").mkdir(parents=True)
    return FileTools(str(tmp_path), "runs/r1", session.PACKAGE)


def test_read_file_cuts_no_line(tmp_path):
    files = files_in(tmp_path)
    long = "".join(chr(97 + i % 26) for i in range(50000))
    (tmp_path / "big.txt").write_text("first\n" + long + "\nlast\n", encoding="utf-8")
    out = files.read_file("big.txt")
    assert out.splitlines()[0] == "     1\tfirst" and "read on from line 2" in out  # (line 2 doesn't fit beside)
    got, column = "", 1
    while True:  # the long line, in parts, is all there
        out = files.read_file("big.txt", offset=2, column=column)
        assert len(out) <= whole.ANSWER_CHARS
        m = re.search(r"read on with offset=2, column=(\d+)\)$", out)
        got += out.split("\t", 1)[1].split("\n(line 2 is")[0].removeprefix(f"(from character {column:,}) ")
        if not m:
            break
        column = int(m.group(1))
    assert got.split("\n")[0] == long and "     3\tlast" in out
    # many short lines: as many as a call holds, then where to read on
    (tmp_path / "many.txt").write_text("".join(f"{'x' * 90} {i}\n" for i in range(1500)), encoding="utf-8")
    out = files.read_file("many.txt")
    shown = [line for line in out.splitlines() if "\t" in line]
    assert len(out) <= whole.ANSWER_CHARS and shown[-1].endswith(f" {len(shown) - 1}")
    assert out.endswith(f"read on from line {len(shown) + 1})")


def test_search_gives_whole_lines_or_all_of_them_in_a_file(tmp_path):
    files = files_in(tmp_path)
    (tmp_path / "codex").mkdir()
    (tmp_path / "codex" / "a.txt").write_text("door " + "d" * 3000 + "\nnothing\n", encoding="utf-8")
    out = files.search("door")
    assert out == "codex/a.txt:1: door " + "d" * 3000  # (a long line whole)
    (tmp_path / "runs" / "r0").mkdir()
    with open(tmp_path / "runs" / "r0" / "trace1.jsonl", "w", encoding="utf-8") as f:
        for i in range(300):
            f.write(json.dumps({"kind": "obs", "door": i, "facts": "f" * 2000}) + "\n")
    out = files.search("door")
    assert len(out) <= whole.ANSWER_CHARS
    assert out.startswith("301 matches for door in 2 files: more than one answer holds, so all of them, whole lines as "
                          "path:line: text, are in runs/r1/whole/search-1.txt")
    assert "codex/a.txt: 1" in out and "runs/r0/trace1.jsonl: 300" in out
    assert "\ncodex/a.txt:1\nruns/r0/trace1.jsonl:1\nruns/r0/trace1.jsonl:2\n" in out + "\n"
    kept = (tmp_path / "runs" / "r1" / "whole" / "search-1.txt").read_text(encoding="utf-8").splitlines()
    assert kept[0] == "codex/a.txt:1: door " + "d" * 3000 and len(kept[1]) > 2000
    assert sum(1 for line in kept if re.match(r"\S+:\d+: ", line)) == 301
    assert kept[-2:] == ["codex/a.txt: 1", "runs/r0/trace1.jsonl: 300"]
    # a later search doesn't find the matches kept whole in the run's folder (unless asked to look there)
    assert files.search("door").startswith("301 matches") and "search-1.txt: " not in files.search("door")
    assert "runs/r1/whole/search-1.txt: 301" in files.search("door", path="runs/r1/whole")


def test_list_files_lists_all(tmp_path):
    files = files_in(tmp_path)
    (tmp_path / "codex").mkdir()
    for i in range(400):
        (tmp_path / "codex" / f"{'f' * 80}_{i}.txt").write_text("x")
    out = files.list_files("codex/*.txt")
    assert len(out) <= whole.ANSWER_CHARS and "all of it" in out and "list-1.txt" in out
    assert len((tmp_path / "runs" / "r1" / "whole" / "list-1.txt").read_text().splitlines()) == 400


# -- the game's tools ------------------------------------------------------------------------------------------------

def test_a_long_probe_output_is_kept_whole(game, tmp_path, monkeypatch):
    s, frames, said = open_run(game, tmp_path, monkeypatch)
    assert s.probe("print('short')").strip() == "short"
    out = s.probe("for i in range(3000): print('row', i, 'x' * 40)")
    assert len(out) <= whole.ANSWER_CHARS and out.startswith("[This probe's output is ")
    kept = kept_path(out)
    assert kept.startswith("runs/r1/whole/probe-")
    text = open(os.path.join(s.root, kept), encoding="utf-8").read()
    assert text.splitlines()[-1] == "row 2999 " + "x" * 40 and len(text.splitlines()) == 3000
    # the feed's detail points to the same file, and the last value of a probe is whole too
    assert os.path.join(s.root, *kept.split("/")) in frames[-1]["detail"]
    out = s.probe("list(range(20000))")
    assert "19999" in open(os.path.join(s.root, kept_path(out)), encoding="utf-8").read()


def test_a_long_summary_points_to_its_file(game, tmp_path, monkeypatch):
    s, frames, said = open_run(game, tmp_path, monkeypatch)
    harness_file(s.run_dir)
    out = s.run_harness(15, restart=False)
    assert "[Episode 1's summary" not in out and "this summary also in trace1_summary.txt" in out
    long = "Harness x\n" + "".join(f"  t={i} walk -> worked\n" for i in range(5000))
    monkeypatch.setattr(runtime, "summarize", lambda *a, **k: long)
    out = s.run_harness(15, restart=False)
    assert len(out) <= whole.ANSWER_CHARS and "[Episode 2's summary is" in out
    assert "is in runs/r1/trace2_summary.txt" in out
    with open(os.path.join(s.run_dir, "trace2_summary.txt"), encoding="utf-8") as f:
        assert f.read() == long
    assert os.path.join(s.run_dir, "trace2_summary.txt") in frames[-1]["detail"]


def test_a_tool_result_stays_within_the_budget_with_the_news(game, tmp_path, monkeypatch):
    s, frames, said = open_run(game, tmp_path, monkeypatch)
    news = "\n\n" + "\n".join(f"Finding {i}: something in the code " + "z" * 100 for i in range(100))
    monkeypatch.setattr(s, "news", lambda: news)
    assert s.result("short").startswith("short\n\nFinding 0")
    out = s.result("a" * (whole.ANSWER_CHARS - 10))
    assert len(out) <= whole.RESULT_CHARS and out.startswith("a" * 100)
    assert "[The news with this result is" in out and "runs/r1/news/news-1.txt" in out
    with open(os.path.join(s.run_dir, "news", "news-1.txt"), encoding="utf-8") as f:
        assert f.read() == news.strip() + "\n"


def test_the_snapshot_is_whole(game, tmp_path, monkeypatch):
    s, frames, said = open_run(game, tmp_path, monkeypatch)
    monkeypatch.setattr(game, "screen", lambda: {f"text{i}": "w" * 100 for i in range(400)})
    out = s.snapshot()
    assert len(out) <= whole.ANSWER_CHARS and "snapshot-1.txt" in out
    assert '"text399"' in open(os.path.join(s.root, kept_path(out)), encoding="utf-8").read()


# -- the briefing and the start call ---------------------------------------------------------------------------------

def test_the_briefing_quotes_what_fits_and_says_where_the_rest_is(game, tmp_path, monkeypatch):
    s, frames, said = open_run(game, tmp_path, monkeypatch)
    root = tmp_path / "fake"
    (root / "notes.md").write_text("".join(f"- fact {i}: " + "n" * 60 + "\n" for i in range(1000)), encoding="utf-8")
    (root / "lib.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    for i in range(30):
        (root / "runs" / f"r0{i:02}").mkdir(parents=True)
        (root / "runs" / f"r0{i:02}" / "prompt.txt").write_text(f"Request {i} " + "q" * 400, encoding="utf-8")
        (root / "runs" / f"r0{i:02}" / "report.json").write_text(json.dumps({"outcome": "achieved",
                                                                              "summary": f"Done {i} " + "s" * 600}))
    game.level_info = types.SimpleNamespace(levels=[f"/Game/Maps/Level_{i:04}" for i in range(2000)], playing="",
                                            open="/Game/Maps/Level_0000", asked="", hint="", playing_asked=False,
                                            failed="")
    opening = "screen_texts: " + json.dumps({f"t{i}": "o" * 100 for i in range(300)})
    text = session.briefing(s, opening)
    notes = text[text.index("## notes.md"):].split("\n\n")[0]
    assert "[notes.md is " in notes and "all of it (1000 lines) is in notes.md" in notes
    assert "Read the rest of it before you rely on what it says." in notes and len(notes) <= session.BRIEF_PART + 200
    assert "- fact 0: " in notes and "- fact 999: " not in notes
    assert "def helper():\n    return 1" in text  # (a short file whole)
    runs = text[text.index("## Earlier runs"):].split("\n\n")[0]
    assert "(30 earlier runs, one a line, whole in runs/r1/whole/earlier_runs-1.txt; the latest" in runs
    assert f"Done 29 {'s' * 600}" in runs and "runs/r000:" not in runs  # (the latest, whole)
    kept = (root / "runs" / "r1" / "whole" / "earlier_runs-1.txt").read_text(encoding="utf-8")
    assert f"- runs/r000: Request 0 {'q' * 400} -> achieved: Done 0 {'s' * 600}" in kept
    assert "all of them, one a line, in runs/r1/whole/levels-1.txt" in text and "/Game/Maps/Level_1999" not in text
    assert (root / "runs" / "r1" / "whole" / "levels-1.txt").read_text().splitlines()[-1] == "/Game/Maps/Level_1999"
    live = text[text.index("## The live game now"):]
    assert "runs/r1/whole/opening-1.txt" in live and len(live) <= session.BRIEF_PART + 200
    assert (root / "runs" / "r1" / "whole" / "opening-1.txt").read_text(encoding="utf-8") == opening


def test_the_start_answer_points_to_request_md_when_the_briefing_is_long():
    short = external.start_text("r1-ab", "fake", "Open the crate", "Request: Open the crate\n\nbrief", "runs/r1")
    assert short.endswith("Request: Open the crate\n\nbrief") and "runs/r1/INSTRUCTIONS.md" in short
    message = "Request: Open the crate\n\n" + "b" * 60000
    long = external.start_text("r1-ab", "fake", "Open the crate", message, "runs/r1")
    assert len(long) <= whole.RESULT_CHARS and "b" * 100 not in long
    assert "The request: Open the crate" in long and f"{len(message):,} characters" in long
    assert long.index("runs/r1/INSTRUCTIONS.md") < long.index("runs/r1/REQUEST.md")
    huge = external.start_text("r1-ab", "fake", "p" * 30000, message, "runs/r1")
    assert len(huge) <= whole.RESULT_CHARS and "30,000 characters, at the top of runs/r1/REQUEST.md" in huge


# -- what the summary and the result say -----------------------------------------------------------------------------

def test_the_summary_cuts_nothing(tmp_path):
    path = tmp_path / "trace1.jsonl"
    events = [{"kind": "start", "harness": "H", "goal": "g", "t": 0}]
    for i in range(120):
        events += [{"kind": "option_start", "option": "walk", "t": i, "p": 0.5},
                   {"kind": "option_end", "option": "walk", "t": i + 0.5, "ok": True,
                    "why": f"reached {i} " + "w" * 100, "seconds": 0.5}]
    events += [{"kind": "error", "where": f"option o{i}", "error": f"E{i}", "traceback": "T" * 2000} for i in range(9)]
    events += [{"kind": "finding", "summary": f"bug {i} " + "b" * 300, "t": 1} for i in range(20)]
    events += [{"kind": "decision", "latency": 0.1, "action": "walk", "state": {"x": "s" * 3000}, "probs": {}},
               {"kind": "obs", "t": 1, "facts": {"f": "v" * 500}, "status": "running", "options": ["walk"]},
               {"kind": "end", "t": 120, "reason": "time", "decisions": 120,
                "status": {"state": "running", "evidence": ["e" * 1000]}}]
    path.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    text = runtime.summarize(str(path))
    assert "e" * 1000 in text and "s" * 3000 in text and "v" * 500 in text
    assert all(f"reached {i} {'w' * 100}" in text for i in range(120)) and "more ..." not in text
    assert all(f"[option o{i}] E{i}" in text for i in range(9)) and "T" * 2000 in text
    assert all(f"bug {i} {'b' * 300}" in text for i in range(20))
    # the long parts come last
    assert text.index("Errors in harness code") < text.index("\nTimeline") < text.index("Situation over time")


def test_the_users_result_cuts_nothing():
    report = {"outcome": "achieved", "summary": "s", "evidence": [f"e{i}" for i in range(12)],
              "findings": [f"f{i}" for i in range(12)]}
    frame = cli.result_frame(report, "Open the crate")
    assert frame["notes"] == ["s"] + [f"e{i}" for i in range(12)] + [f"f{i}" for i in range(12)]
    assert change_text({"name": "A", "property": "P", "value": "x" * 500}).endswith("x" * 500 + '"')
