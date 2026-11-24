"""What Autopilot's own agents (OpenCode, Claude Code, Codex) read first, pinned by hash: their system prompt (for every
engine) and the request message (first_message) for fixed games. An outside agent (`autopilot connect`) is given a
shorter INSTRUCTIONS.md and REQUEST.md, built beside these; these stay byte for byte what they were.

A change to SYSTEM, KIT.md, an engine's kit section, research.DOC, a workflow or the briefing changes these hashes on
purpose: run `python tests/test_own_agents_text.py` and put the hashes it prints in EXPECTED.
"""

import hashlib
import json
import pathlib
import sys
import tempfile
import types

import pytest
from test_activity import open_run
from test_autopilot import FakeLink

from autopilot import engines
from autopilot.agents import claude_code, codex, opencode, session
from autopilot.kit.game import Game

ENV = ("AUTOPILOT_RESEARCH_SECONDS", "AUTOPILOT_RESEARCH_CALLS", "AUTOPILOT_RESEARCHERS", "AUTOPILOT_REVIEWER")


def _game():
    g = Game(FakeLink())
    g.look = {"x": 0.1, "y": 0.1}
    return g


def _runs(root, n, summary):
    for i in range(n):
        run = root / "runs" / f"r0{i:02}"
        run.mkdir(parents=True)
        (run / "prompt.txt").write_text(f"Request {i} " + "q" * 400, encoding="utf-8")
        (run / "report.json").write_text(json.dumps({"outcome": "achieved", "summary": summary(i)}))


def _rich(root, game):
    (root / "notes.md").write_text("".join(f"- fact {i}: " + "n" * 60 + "\n" for i in range(1000)), encoding="utf-8")
    (root / "lib.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    _runs(root, 30, lambda i: f"Done {i} " + "s" * 600)
    game.level_info = types.SimpleNamespace(levels=[f"/Game/Maps/Level_{i:04}" for i in range(2000)], playing="",
                                            open="/Game/Maps/Level_0000", asked="", hint="", playing_asked=False,
                                            failed="")
    return "screen_texts: " + json.dumps({f"t{i}": "o" * 100 for i in range(300)})


def _long_parts(root, game):
    """A game whose briefing has every part the outside agent's is shorter in: a long codex README, many files with
    the request's words, earlier runs with long summaries over many lines, a live game with long lists."""
    (root / "notes.md").write_text("".join(f"- note {i}: " + "n" * 80 + "\n" for i in range(60)), encoding="utf-8")
    (root / "lib.py").write_text("".join(f"def helper_{i}():\n    return {i}\n\n" for i in range(30)),
                                 encoding="utf-8")
    _runs(root, 8, lambda i: "\n".join(f"Line {k} of run {i}: " + "w" * 140 for k in range(6)))
    codex_dir = root / "codex" / "blueprints"
    codex_dir.mkdir(parents=True)
    head = "".join(f"| table {i} | what is where |\n" for i in range(20))
    (root / "codex" / "README.md").write_text(
        head + "".join(f"- Parent_{i} (25): " + "b" * 150 + "\n" for i in range(100)), encoding="utf-8")
    for i in range(60):
        (codex_dir / f"BP_Crate{i:02}.txt").write_text("open the crate\n" * (1 + i % 5), encoding="utf-8")
    for i in range(200):  # (so the request's words are rare: ranked, not "in most files")
        (codex_dir / f"BP_Other{i:03}.txt").write_text("nothing here\n", encoding="utf-8")
    return "\n".join([
        'level: "/Game/Maps/Demo"',
        "controls: " + json.dumps({f"Action{i}": [f"Key{i}"] for i in range(40)}),
        "screen_texts: " + json.dumps({f"t{i}": "text " * 4 for i in range(200)}),
        "ui: " + json.dumps([{"id": f"button_{i}", "kind": "button", "label": f"Button {i}"} for i in range(300)]),
        "network: " + json.dumps("The game runs as a listen server. " + "n" * 900),
    ])


FIXTURES = {
    "empty": lambda root, game: "(the game)",
    "rich": _rich,
    "rich-going-on": _rich,
    "long-parts": _long_parts,
}


def system_texts():
    """{name: text} of every own agent's system prompt, for every engine."""
    out = {}
    with pytest.MonkeyPatch.context() as mp:
        for name in ENV:
            mp.delenv(name, raising=False)
        for name, engine in engines.ENGINES.items():
            out[f"system opencode {name}"] = opencode.system_text(engine, "runs/r1")
            out[f"system codex {name}"] = codex.system_text(engine, "runs/r1")
            out[f"system claude-code {name}"] = session.system_prompt(
                engine, "runs/r1", agents=claude_code.agents_note(claude_code.RESEARCHERS, True))
    return out


def request_messages():
    """{name: text} of the request message every own agent starts from, for each fixed game."""
    out = {}
    for name, build in FIXTURES.items():
        with tempfile.TemporaryDirectory() as tmp, pytest.MonkeyPatch.context() as mp:
            for env in ENV:
                mp.delenv(env, raising=False)
            game = _game()
            s, _, _ = open_run(game, pathlib.Path(tmp), mp)
            opening = build(pathlib.Path(tmp) / "fake", game)
            mp.setattr(session.Session, "opening", lambda self, text=opening: text)
            s.going_on = name.endswith("going-on")
            out[f"message {name}"] = session.first_message(s)
    return out


def hashes():
    return {name: [len(text), hashlib.sha256(text.encode("utf-8")).hexdigest()]
            for name, text in {**system_texts(), **request_messages()}.items()}


# [characters, sha256], computed at 5c6319c (before the outside agent's files got shorter)
EXPECTED = {
    "message empty": [3089, "8bcbdfc9e1638bc6a3f88b8c02bd0021fbcbea40d97938aad6d163c0f56a4558"],
    "message long-parts": [37933, "815ae46bb5b568aadf7894eafa4f2cc1367c2ddfa3fd2488d81b79e6547d5f4b"],
    "message rich": [39233, "1ababf408872ca2e21c8bf2e514ca4c42a9d29134649f6548458b9e094c60ff6"],
    "message rich-going-on": [39564, "42f3cb8f41aeea822adc51e2ad63e76eddf5129ca674f0c8bf85b9f66eadd5d4"],
    "system claude-code godot": [78447, "a918e5d239f6c6c3f6f2d3853cd4dc0fa8ed156b724a52b8667d3ff80fd38411"],
    "system claude-code roblox": [82519, "c3b24608d568175028972394e7cae6417c3da6ad1ac00661123c8202b2fc5236"],
    "system claude-code roblox-mcp": [76599, "007e76a6917432e558195b62280d01972927e34f21d075aaf6f6ff991da7fa3e"],
    "system claude-code unity": [79414, "43850e99b10620b2578e3c30c01a2b44f9d41cbb6063fc8254a88c8074f5e02f"],
    "system claude-code unreal": [67419, "172caac7a0504533d7b54b6cf5a0db4a43e44b193d746ec82ebba30e29982c6c"],
    "system codex godot": [78591, "83f5b0a7421fe3da56b8d79c4906903919b9fcb043c93da3f907f7ab0e993c0d"],
    "system codex roblox": [82663, "6450efb19f0a5382fef1063b61e8709b16311b3a1277e8a8dc861e6a3c9d67d5"],
    "system codex roblox-mcp": [76743, "25f95f1f29ae32f2b8236972778a7e4049703116ee42b2e71f18dde7af01490b"],
    "system codex unity": [79558, "9975bb1755a9082245598139ad7b40b7a74dd800c3d1e1553759e755e5ef5475"],
    "system codex unreal": [67563, "8a68349459c84728dc4408da037eb127d501b9f3a80cbcc490afc7508cca1687"],
    "system opencode godot": [78547, "bafdeb8cbe4a02ba60c89a3f9ea704a7bd0fba12da1f62c77074790039fe20f0"],
    "system opencode roblox": [82619, "36bc58a4d761bf8adfd1fceebd62b9e6c722590cb2eaa1df50f1f81373ea2569"],
    "system opencode roblox-mcp": [76699, "a5a7ec86e36e4cbec41bb9ad1b77d28f619621da94b82506b5a3ee8cfc88cae6"],
    "system opencode unity": [79514, "e2d6c84c1dff12bb7d86f196fd3d819055642607a3bdc60f8eeff58e10e49182"],
    "system opencode unreal": [67519, "b53be89671a3a29dfe787d6ed7380d9b8e9e998c0d5bac25fa192e44a6d5b874"],
}


def test_the_own_agents_read_what_they_read_before():
    got = hashes()
    assert set(got) == set(EXPECTED)
    changed = [name for name in EXPECTED if got[name] != EXPECTED[name]]
    assert not changed, changed


if __name__ == "__main__":
    json.dump(hashes(), sys.stdout, indent=1, sort_keys=True)
    print()
