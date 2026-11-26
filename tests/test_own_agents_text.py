"""What Autopilot's own agents (OpenCode, Claude Code, Codex) read first, pinned by hash: their system prompt (for every
engine) and the request message (first_message) for fixed games. An outside agent (`autopilot connect`) is given a
shorter INSTRUCTIONS.md and REQUEST.md, built beside these; these stay byte for byte what they were.

A change to SYSTEM, KIT.md, an engine's kit section, research.DOC, a workflow or the briefing changes these hashes on
purpose: run `python tests/test_own_agents_text.py` and put the hashes it prints in EXPECTED.
"""

import ast
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
    "system claude-code godot": [82398, "032d9606f67b2636126f80d3e22209ab15d2184c3d05bad78ad80e68cf2a51c4"],
    "system claude-code roblox": [86470, "fed4bced6fd04d625f5c63f0daf34097c99c0ae6afea4ebc574a9ac374fe4982"],
    "system claude-code roblox-mcp": [80550, "ebf4ec973d62bf37bb74d3879571179f3e10ad648f246b895525946577680b7f"],
    "system claude-code unity": [83365, "e1f6990e631ddbe4270861e1ccd42c982ce786b5853e52453819893cc7044168"],
    "system claude-code unreal": [71370, "e898efa5b7de735af4286c0281710358115d120fd0123c6355063f950ba1a72f"],
    "system codex godot": [82542, "7ad7878fb63704cad1d500c9e541e1f75cec13d4151ff81a3688e5aec72d5c44"],
    "system codex roblox": [86614, "71a28f00b677bb52699b0b3c7cfa12205e004f87ce8186ed2d695982fa31aa85"],
    "system codex roblox-mcp": [80694, "3cd2c3edd066abfe04b5d7d5683b0743a307cbfddf1ec3cc38a47ec2306651a7"],
    "system codex unity": [83509, "cbcf0b06d9f6d6e89d760731db87d54258bbc7c096853f096cc34395e1187f98"],
    "system codex unreal": [71514, "d101120e26af10623e214246e2aa25f89621256bddafc1cc835bde39f6e079fa"],
    "system opencode godot": [82498, "c2cc3e4cbfbb6ac1b010033446be6acd2faffa1ce4626d90e74c9919989ff1f3"],
    "system opencode roblox": [86570, "aee9d3bb6b235a599f912c96b664bfa3c169a9b6f5f3eb7984a23c87c652861f"],
    "system opencode roblox-mcp": [80650, "84195e7b76d67362b82ceae87c33f16566637b94c8a7bb9bdc2260789d19c26e"],
    "system opencode unity": [83465, "ab8942482c7728dd68a916a18e4af6e898254394e1c275b85321fc7542a79bf4"],
    "system opencode unreal": [71470, "162073698c7149c08c51e94dd962be2fa643c7f36e13da8fdbc6481e37a3d6cc"],
}


def test_the_own_agents_read_what_they_read_before():
    got = hashes()
    assert set(got) == set(EXPECTED)
    changed = [name for name in EXPECTED if got[name] != EXPECTED[name]]
    assert not changed, changed


# The test above calls first_message and system_prompt itself; this one reads the code that calls them, so an own
# agent that asks for the shorter text by mistake fails here. Only the outside agent's path (external.py) and the
# session's own first_message (which hands short on to briefing) may ask for it.
SHORTER = {"first_message": 1, "system_prompt": 3, "briefing": 2}  # (name: positional arguments of the full text)
MAY_ASK = {"external.py", "session.py"}


def _calls(path):
    """[(name, line, asks for the shorter text)] of each call to a SHORTER function in the file at path."""
    out = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
        if name not in SHORTER:
            continue
        asks = (len(node.args) > SHORTER[name] or any(isinstance(a, ast.Starred) for a in node.args)
                or any(k.arg in (None, "short", "room") for k in node.keywords))  # (None: **kwargs)
        out.append((name, node.lineno, asks))
    return out


def test_no_own_agent_asks_for_the_outside_agents_shorter_text():
    package = pathlib.Path(session.__file__).parents[1]
    asking, callers = [], {}
    for path in sorted(package.rglob("*.py")):
        for name, line, asks in _calls(path):
            callers.setdefault(path.name, set()).add(name)
            if asks and path.name not in MAY_ASK:
                asking.append(f"{path.relative_to(package)}:{line} {name}")
    assert not asking, asking
    # (the check reads the own agents' calls: each starts from first_message, and has a system prompt)
    for module in (claude_code, codex, opencode):
        assert {"first_message", "system_prompt"} <= callers[pathlib.Path(module.__file__).name], module.__name__


if __name__ == "__main__":
    json.dump(hashes(), sys.stdout, indent=1, sort_keys=True)
    print()
