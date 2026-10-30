"""The level a run plays (autopilot/levels.py, cli.prepare): the editor's own, unless the run asks for another by its
start frame's `level` or the agent moves to the one the request means; played without opening it in the editor
(docs/protocol.md)."""

import contextlib
import types

import pytest

from autopilot import cli, codex, levels
from autopilot.bridge import BridgeError

UNREAL = ["/Game/Maps/Lvl_Harbor", "/Game/Maps/ThirdPersonMap", "/Game/Maps/MainMenu", "/Game/Maps/Arena",
          "/Game/Maps/Desert_02", "/Game/Arena/Arena"]


def test_paths_and_names():
    assert levels.short("res://levels/harbor.tscn") == "harbor"
    assert levels.short("Assets/Scenes/Main Menu.unity") == "Main Menu"
    # Play In Editor's world names the level it plays
    assert levels.same("/Game/Maps/UEDPIE_0_Arena.Arena", "/Game/Maps/Arena")


def test_a_runs_level_by_path_or_its_own_name():
    assert levels.resolve("/Game/Maps/Arena.Arena", UNREAL) == "/Game/Maps/Arena"
    assert levels.resolve("lvl_harbor", UNREAL) == "/Game/Maps/Lvl_Harbor"
    assert levels.resolve("Harbor.unity", ["Assets/Scenes/Harbor.unity"]) == "Assets/Scenes/Harbor.unity"
    # words are no level's name (the agent decides what they mean), nor is a name two levels share
    assert levels.resolve("the harbor", UNREAL) is None and levels.resolve("Arena", UNREAL) is None
    assert levels.resolve("Nowhere", UNREAL) is None
    # (no list: the plugin finds it, or says it can't)
    assert levels.resolve("Harbor", []) == "Harbor"


def test_no_words_of_the_prompt_choose_the_level():
    """The agent decides (its briefing's first decision): the game starts in the editor's own, whatever the prompt."""
    import inspect

    assert not hasattr(levels, "named") and "prompt" not in inspect.signature(cli.choose_level).parameters


class Plugin:
    """A plugin's answers to levels, session and context; its requests kept."""

    def __init__(self, listed=None, open_="/Game/Maps/Arena", running=False, playing="", refuse_map=None,
                 host="editor"):
        self.listed, self.open, self.running, self.playing = listed, open_, running, playing
        self.refuse_map, self.asked, self.said = refuse_map, [], []
        self.hello = {"project": "Harbor Game", "host": host}

    def request(self, op, args=None, timeout=None):
        args = args or {}
        self.asked.append((op, dict(args)))
        if op == "levels":
            if self.listed is None:
                raise BridgeError(400, "unknown op levels")
            return {"levels": self.listed, "open": self.open, "playing": self.playing if self.running else ""}
        if op == "session":
            if args.get("action") in ("start", "restart"):
                if args.get("map") and args["map"] == self.refuse_map:
                    raise BridgeError(404, f"No level named {args['map']} in the project")
                self.running, self.playing = True, args.get("map") or self.open
            elif args.get("action") == "stop":
                self.running = False
            return {"running": self.running}
        if op == "context":
            return {"world": self.playing, "character": "/Game/P" if self.running else ""}
        return {}

    def status(self, text):
        self.said.append(text)

    def post(self, op, args=None):
        self.asked.append((op, dict(args or {})))

    def sessions(self):
        return [a for op, a in self.asked if op == "session" and a.get("action") != "status"]


@pytest.fixture
def no_codex(tmp_path, monkeypatch):
    monkeypatch.setattr(codex, "MEMORY", str(tmp_path / "mem"))
    monkeypatch.setattr(codex, "build", lambda project, path=None, **_: codex.Codex(str(tmp_path)))


def test_the_runs_level_plays_and_every_restart_starts_it_over(no_codex):
    plugin = Plugin(UNREAL)
    _, game = cli.prepare(plugin, None, level="Lvl_Harbor")
    assert plugin.sessions() == [{"action": "start", "map": "/Game/Maps/Lvl_Harbor"}]
    assert game.level == "/Game/Maps/Lvl_Harbor" and "Playing the level Lvl_Harbor" in plugin.said
    assert plugin.said.index("Starting the game") < plugin.said.index("Playing the level Lvl_Harbor")
    game.restart(wait_for_pawn=0.0)
    assert plugin.sessions()[-1] == {"action": "restart", "map": "/Game/Maps/Lvl_Harbor"}
    game.restart(map="/Game/Maps/Arena", wait_for_pawn=0.0)  # (a harness's start_map still wins)
    assert plugin.sessions()[-1] == {"action": "restart", "map": "/Game/Maps/Arena"}
    brief = levels.briefing(game.level_info)
    assert "This run plays /Game/Maps/Lvl_Harbor: the run was asked to play it." in brief
    assert "/Game/Maps/MainMenu" in brief and "Your first decision" not in brief
    assert cli.level_alert(game.level_info) is None


def test_without_a_level_asked_the_editors_own_plays_as_before(no_codex):
    plugin = Plugin(UNREAL)
    _, game = cli.prepare(plugin, None)
    assert plugin.sessions() == [{"action": "start"}] and game.level is None
    assert not any(s.startswith("Playing the level") for s in plugin.said)
    brief = levels.briefing(game.level_info)
    assert "This run plays /Game/Maps/Arena: the editor's own level" in brief
    assert "Your first decision: does the request mean a level other than this one" in brief
    game.restart(wait_for_pawn=0.0)
    assert plugin.sessions()[-1] == {"action": "restart"}
    # a game already playing goes on as it is
    plugin = Plugin(UNREAL, running=True, playing="/Game/Maps/Arena")
    cli.prepare(plugin, None)
    assert plugin.sessions() == []


def test_the_game_holds_still_while_its_code_is_read(tmp_path, monkeypatch):
    """Analyze: the game is paused once the player is there, before the code is read (the agent's session lets it go
    when it ends); a run that fails before its agent lets it go at once."""
    plugin = Plugin(UNREAL)
    monkeypatch.setattr(codex, "MEMORY", str(tmp_path / "mem"))
    monkeypatch.setattr(codex, "build", lambda project, path=None, **_: plugin.asked.append(("build", {}))
                        or codex.Codex(str(tmp_path)))
    _, game = cli.prepare(plugin, None)
    ops = [op for op, _ in plugin.asked]
    assert plugin.asked[ops.index("build") - 1] == ("pause", {"paused": True})
    assert ops.index("context") < ops.index("pause")
    with pytest.raises(RuntimeError), contextlib.ExitStack() as setup:
        setup.push(cli.resume_if_failed(game))
        raise RuntimeError("the agent couldn't start")
    assert plugin.asked[-1] == ("pause", {"paused": False})
    with contextlib.ExitStack() as setup:  # (a run that ended well: its agent let the game go)
        setup.push(cli.resume_if_failed(game))
    assert [a for op, a in plugin.asked if op == "pause"] == [{"paused": True}, {"paused": False}]


class LoadingPlugin(Plugin):
    """A plugin whose play session is still loading its level for the first `loads` status answers."""

    def __init__(self, loads, **kwargs):
        super().__init__(UNREAL, **kwargs)
        self.loads = loads

    def request(self, op, args=None, timeout=None):
        answer = super().request(op, args, timeout)
        if op == "session" and (args or {}).get("action") == "status":
            self.loads -= 1
            return dict(answer, loading=self.loads >= 0)
        return answer


def test_a_play_session_still_loading_is_waited_out_before_the_reading_time_counts(no_codex, monkeypatch):
    """Analyze reads "Waiting for PIE" while the play session loads: the run waits it out, and its time for reading
    and probing (agents/session.py, RESEARCH_SECONDS) counts from when the game is ready."""
    clock = {"now": 1000.0}
    monkeypatch.setattr(cli.time, "sleep", lambda s: clock.__setitem__("now", clock["now"] + s))
    monkeypatch.setattr(cli.time, "time", lambda: clock["now"])
    plugin = LoadingPlugin(5)
    _, game = cli.prepare(plugin, None)
    statuses = [a for op, a in plugin.asked if op == "session" and a.get("action") == "status"]
    assert len(statuses) == 1 + 5  # (start_level's own, then four loading and the ready one)
    assert plugin.said[-1] == "Reading the game's code"
    assert game.ready_at == pytest.approx(clock["now"]) and game.ready_at >= 1000.0 + 4 * 0.5
    # a plugin that doesn't say (an older one, Unity, Godot) is ready at once
    plugin = Plugin(UNREAL, running=True, playing="/Game/Maps/Arena")
    assert cli.wait_for_start(plugin) == 0.0
    # nor does the run wait for a game that is gone
    plugin.request = lambda *a, **k: (_ for _ in ()).throw(ConnectionError("the game is disconnected"))
    assert cli.wait_for_start(plugin) == 0.0


def test_a_game_playing_another_level_is_restarted_in_the_one_asked_for(no_codex):
    plugin = Plugin(UNREAL, running=True, playing="/Game/Maps/Arena")
    _, game = cli.prepare(plugin, None, level="thirdpersonmap")
    assert plugin.sessions() == [{"action": "restart", "map": "/Game/Maps/ThirdPersonMap"}]
    assert "the run was asked to play it" in levels.briefing(game.level_info)
    # already in it: nothing restarts
    plugin = Plugin(UNREAL, running=True, playing="/Game/Maps/UEDPIE_0_ThirdPersonMap.ThirdPersonMap")
    _, game = cli.prepare(plugin, None, level="ThirdPersonMap")
    assert plugin.sessions() == [] and game.level == "/Game/Maps/ThirdPersonMap"


def test_a_level_that_cant_play_leaves_the_editors_own_and_the_result_says_so(no_codex):
    plugin = Plugin(UNREAL, refuse_map="/Game/Maps/Desert_02")
    _, game = cli.prepare(plugin, None, level="Desert_02")
    assert plugin.sessions() == [{"action": "start", "map": "/Game/Maps/Desert_02"}, {"action": "start"}]
    assert game.level is None and plugin.running
    alert = cli.level_alert(game.level_info)
    assert alert.startswith("The run played the editor's own level, not Desert_02: No level named /Game/Maps/Desert_02")
    assert "couldn't be played" in levels.briefing(game.level_info)


def test_a_runs_level_that_names_none_of_the_list_is_the_agents_to_find(no_codex):
    """Aura's level_name, or the user's own agent's start_run level, in words: the game starts in the editor's own
    and the agent picks the level meant from the list."""
    plugin = Plugin(UNREAL)
    _, game = cli.prepare(plugin, None, level="the third person one")
    assert plugin.sessions() == [{"action": "start"}] and game.level is None
    assert game.level_info.hint == "the third person one" and cli.level_alert(game.level_info) is None
    brief = levels.briefing(game.level_info)
    assert 'The run asked for the level "the third person one", which is none of the list' in brief
    assert "Your first decision" in brief


def test_a_plugin_from_before_levels(no_codex):
    """Its editor plays only its own level: never asked for a map, and the result says why. A game build's restart
    takes a map whatever its version."""
    plugin = Plugin(None)
    _, game = cli.prepare(plugin, None)  # (no list: no level is named)
    assert plugin.sessions() == [{"action": "start"}] and game.level_info.asked == ""
    plugin = Plugin(None)
    _, game = cli.prepare(plugin, None, level="/Game/Maps/Lvl_Harbor")
    assert plugin.sessions() == [{"action": "start"}] and game.level is None
    assert "update it" in cli.level_alert(game.level_info)
    plugin = Plugin(None, running=True, playing="/Game/Maps/Arena", host="game")
    _, game = cli.prepare(plugin, None, level="/Game/Maps/Lvl_Harbor")
    assert plugin.sessions() == [{"action": "restart", "map": "/Game/Maps/Lvl_Harbor"}]
    assert game.level == "/Game/Maps/Lvl_Harbor"


def test_the_agent_changes_the_level_with_restart_level(no_codex):
    from autopilot.agents.session import TOOL_SPECS, Session

    plugin = Plugin(UNREAL)
    _, game = cli.prepare(plugin, None)
    notes = []
    session = types.SimpleNamespace(steered=False, steering=types.SimpleNamespace(pending=lambda: False),
                                    sandbox=None, game=game, note=notes.append,
                                    activity=lambda kind, text, detail=None, log=None, **k: notes.append(log))
    game.restart = lambda map=None, wait_for_pawn=20.0: plugin.request(
        "session", {"action": "restart", **({"map": map or game.level} if map or game.level else {})}) and True
    assert Session.restart_level(session, "/Game/Maps/Lvl_Harbor") == "Restarted in /Game/Maps/Lvl_Harbor."
    assert game.level == "/Game/Maps/Lvl_Harbor" and game.level_info.playing == "/Game/Maps/Lvl_Harbor"
    assert Session.restart_level(session) == "Restarted."
    assert plugin.sessions()[-1] == {"action": "restart", "map": "/Game/Maps/Lvl_Harbor"}
    # a level the project hasn't: nothing restarts, the agent is told so
    restarts = len(plugin.sessions())
    assert Session.restart_level(session, "the docks").startswith("Not restarted: the project has no level the docks")
    assert len(plugin.sessions()) == restarts and game.level == "/Game/Maps/Lvl_Harbor"
    spec = dict((name, schema) for name, _, schema in TOOL_SPECS)["restart_level"]
    assert spec["properties"] == {"level": {"type": "string"}}


def test_the_start_frames_level_reaches_the_run(monkeypatch):
    assert cli.external_run({"request": "Win the race", "kind": "goal", "level": " Harbor "})["level"] == "Harbor"
    assert "level" not in cli.external_run({"request": "Win the race", "kind": "goal"})
