"""A project's settings for the game's detectors (autopilot/detectors.py): what it muted, sent with every run, the
editor's Not a bug adding to it, and Reset memory leaving it."""

import json
import types

import pytest

from autopilot import bridge, cli, codex, detectors, memory


def test_the_mute_list_is_kept_clean_and_the_other_settings_pass_as_written(tmp_path):
    folder = str(tmp_path / "Game")
    assert detectors.load(folder) == {} and detectors.config(folder) == {"mute": []}
    assert detectors.mute(folder, "out_of_world:BP_Tree")
    assert not detectors.mute(folder, " out_of_world:BP_Tree ") and not detectors.mute(folder, "")  # (once each)
    (tmp_path / "Game" / "detectors.json").write_text(json.dumps(
        {"mute": ["out_of_world:BP_Tree", 3, "", "hitch", "hitch"], "max_distance": 5e6, "enabled": True,
         "note": "the trees are far out", "nested": {"x": 1}}))
    assert detectors.config(folder) == {"mute": ["out_of_world:BP_Tree", "hitch"], "max_distance": 5e6,
                                        "enabled": True}
    assert detectors.mute(folder, "teleport:BP_Board")
    data = json.loads((tmp_path / "Game" / "detectors.json").read_text())
    assert data["mute"] == ["out_of_world:BP_Tree", "hitch", "teleport:BP_Board"]
    assert data["note"] == "the trees are far out" and data["max_distance"] == 5e6  # (the user's own keys stay)


def test_a_broken_file_leaves_the_detectors_as_they_are(tmp_path, capsys):
    (tmp_path / "detectors.json").write_text("{not json")
    assert detectors.config(str(tmp_path)) == {"mute": []}
    assert "can't be read" in capsys.readouterr().err
    (tmp_path / "detectors.json").write_text("[1, 2]")
    assert detectors.config(str(tmp_path)) == {"mute": []}


def test_reset_memory_keeps_what_the_project_muted(tmp_path):
    game = tmp_path / "memory" / "Game"
    for rel in ("notes.md", "runs/r1/report.json"):
        (game / rel).parent.mkdir(parents=True, exist_ok=True)
        (game / rel).write_text("x")
    detectors.mute(str(game), "hitch")
    assert "detectors.json" not in memory.LEARNED
    assert memory.forget(str(game)) == 2
    assert detectors.config(str(game))["mute"] == ["hitch"]


class Plugin:
    """A plugin's answers to what prepare() and the service's loop ask; its requests (and posts) kept."""

    def __init__(self, messages=()):
        self.hello = {"project": "Game"}
        self.asked, self.sent, self.said = [], [], []
        self.messages = list(messages)
        self.rebound = []
        self.running = False

    def request(self, op, args=None, timeout=None):
        args = args or {}
        self.asked.append((op, args))
        if op == "levels":
            return {"levels": ["/Game/L"], "open": "/Game/L", "playing": "/Game/L" if self.running else ""}
        if op == "session":
            if args.get("action") in ("start", "restart"):
                self.running = True
            return {"running": self.running}
        if op == "context":
            return {"world": "/Game/L", "character": "/Game/P" if self.running else ""}
        return {}

    def post(self, op, args=None):
        self.asked.append((op, args or {}))

    def send(self, frame):
        self.sent.append(frame)

    def status(self, text):
        self.said.append(text)

    def next_message(self, timeout=None):
        if not self.messages:
            raise KeyboardInterrupt  # (the end of the test)
        return self.messages.pop(0)

    def sent_detectors(self):
        return [args for op, args in self.asked if op == "detectors"]


@pytest.fixture
def mem(tmp_path, monkeypatch):
    monkeypatch.setattr(codex, "MEMORY", str(tmp_path / "mem"))
    monkeypatch.setattr(codex, "build", lambda project, path=None, **_: codex.Codex(str(tmp_path)))
    return tmp_path / "mem" / "Game"


def test_every_run_tells_the_game_what_the_project_muted_before_it_starts(mem):
    detectors.mute(str(mem), "out_of_world:BP_Tree")
    with open(mem / "detectors.json", "r+", encoding="utf-8") as f:
        data = json.load(f)
        data["max_distance"] = 5000000
        f.seek(0)
        json.dump(data, f)
    plugin = Plugin()
    cli.prepare(plugin, None)
    ops = [op for op, _ in plugin.asked]
    assert plugin.sent_detectors() == [{"mute": ["out_of_world:BP_Tree"], "max_distance": 5000000}]
    first_start = next(i for i, (op, a) in enumerate(plugin.asked) if op == "session" and a.get("action") == "start")
    assert ops.index("detectors") < first_start  # (its first findings are filtered too)
    assert plugin.muted == {"out_of_world:BP_Tree"}
    # a plugin back from a reload (a Unity domain reload starts its detectors afresh) is told again, without a wait
    assert len(plugin.rebound) == 1
    plugin.rebound[0]()
    assert len(plugin.sent_detectors()) == 2
    cli.prepare(plugin, None)  # (the next run: still one watcher)
    assert len(plugin.rebound) == 1


def test_a_project_with_nothing_muted_still_sends_an_empty_list(mem):
    # (an emptied list un-mutes what an earlier run's list muted: the plugins keep the last list)
    plugin = Plugin()
    cli.prepare(plugin, None)
    assert plugin.sent_detectors() == [{"mute": []}]


def test_a_plugin_without_the_op_doesnt_stop_the_run(mem, capsys):
    class Old(Plugin):
        def request(self, op, args=None, timeout=None):
            if op == "detectors":
                raise bridge.BridgeError(400, "unknown op detectors")
            return super().request(op, args, timeout)

    detectors.mute(str(mem), "hitch")
    plugin = Old()
    cli.prepare(plugin, None)
    assert "didn't take the project's settings" in capsys.readouterr().err
    assert plugin.muted == {"hitch"}  # (its findings are dropped by the service instead)


def test_not_a_bug_mutes_the_finding_for_the_project_and_tells_the_game(mem, monkeypatch):
    monkeypatch.setattr(cli.account, "send_state", lambda *a, **k: None)
    monkeypatch.setattr(cli.account, "SignIn", lambda link: None)
    monkeypatch.setattr(cli, "send_memory_state", lambda *a, **k: None)
    link = Plugin([{"type": "feedback", "signature": "out_of_world:BP_Tree", "verdict": "not_a_bug"},
                   {"type": "feedback", "signature": "out_of_world:BP_Tree", "verdict": "not_a_bug"},
                   {"type": "feedback", "signature": "hitch", "verdict": "confirmed"},
                   {"type": "feedback", "verdict": "not_a_bug"}])
    with pytest.raises(KeyboardInterrupt):
        cli.cmd_serve(link, types.SimpleNamespace(path=None, model=None))
    assert json.loads((mem / "detectors.json").read_text()) == {"mute": ["out_of_world:BP_Tree"]}
    assert link.sent_detectors() == [{"mute": ["out_of_world:BP_Tree"]}] * 2  # (sent again: harmless)
    assert link.muted == {"out_of_world:BP_Tree"}


def test_the_service_drops_muted_findings_a_plugin_still_sends():
    link = bridge.Link(lambda frame: None)
    seen = []
    link.listeners.append(seen.append)
    link.muted = frozenset({"out_of_world:BP_Tree", "hitch"})
    for finding in ({"detector": "out_of_world", "signature": "out_of_world:BP_Tree"},
                    {"detector": "out_of_world", "signature": "out_of_world:BP_Rock"},
                    {"detector": "hitch", "signature": "hitch"}):
        link.feed({"type": "events", "events": [{"kind": "finding", "t": 1.0, "finding": finding}]})
    assert [f["signature"] for f in link.findings] == ["out_of_world:BP_Rock"]
    assert [e["finding"]["signature"] for e in seen] == ["out_of_world:BP_Rock"]


def test_a_muted_teleport_is_muted_right_after_the_agents_input_too():
    # A teleport right after the agent's key is told as "teleport:<kind> after <key>" (info): muting the kind's teleport
    # ("Not a bug" on it) mutes that one too, in the service and in every plugin; muting the info alone doesn't mute
    # the kind's other teleports
    import pathlib

    muted = frozenset({"teleport:BP_Player"})
    assert detectors.is_muted({"detector": "teleport", "signature": "teleport:BP_Player after R"}, muted)
    assert detectors.is_muted({"detector": "teleport", "signature": "teleport:BP_Player"}, muted)
    assert not detectors.is_muted({"detector": "teleport", "signature": "teleport:BP_Enemy after R"}, muted)
    assert not detectors.is_muted({"detector": "teleport", "signature": "teleport:BP_Player"},
                                  frozenset({"teleport:BP_Player after R"}))
    # (only a teleport's: another detector's signature may say "after" in its own words)
    assert not detectors.is_muted({"detector": "log_error", "signature": "log_error:x after y"},
                                  frozenset({"log_error:x"}))
    root = pathlib.Path(__file__).resolve().parent.parent / "plugin"
    for rel, check in (("unreal/Autopilot/Source/AutopilotPlaytest/Private/PlaytestDetectors.cpp",
                        "Muted.Contains(Signature.Left(After))"),
                       ("unity/com.ramenvr.autopilot/Runtime/Detectors.cs",
                        "muted.Contains(signature.Substring(0, after))"),
                       ("godot/addons/autopilot/lib/detectors.gd", "muted.has(sig.substr(0, after))"),
                       ("roblox/src/Detectors.luau", 'string.match(sig, "^(.*) after ")')):
        assert check in (root / rel).read_text("utf-8"), rel


def test_the_agent_is_told_where_muting_is():
    from autopilot.agents import session

    # (in the working folder's listing: where it is and what goes in it, not its contents)
    listing = session.SYSTEM.split("How to work:")[0]
    assert "detectors.json" in listing and "out_of_world:BP_Tree" in listing
