"""The plugin: a game build's restart takes what the game added to the viewport off it
before reopening the level, so text the game drew before the restart (a message shown when the pilot was knocked out)
doesn't stay on screen over the reopened level. A source check: the plugin is C++, built separately."""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRIVATE = os.path.join(ROOT, "plugin", "unreal", "Autopilot", "Source", "AutopilotPlaytest", "Private")
SESSION_OPS = os.path.join(PRIVATE, "PlaytestOpsSession.cpp")  # `session`, `context`, `pause`, ...
INPUT_OPS = os.path.join(PRIVATE, "PlaytestOpsInput.cpp")  # `input`, `move`, `key`


def test_the_plugins_restart_takes_the_games_widgets_off_the_viewport_first():
    source = open(SESSION_OPS, encoding="utf-8").read()
    restart = source[source.index("restarting reopens the map the game was launched with"):]
    restart = restart[:restart.index("Bridge.AddPending")]
    assert restart.index("RemoveGameWidgets(World);") < restart.index("UGameplayStatics::OpenLevel(")
    helper = source[source.index("void RemoveGameWidgets(UWorld* World)"):]
    helper = helper[:helper.index("\n\t}\n")]
    assert "RemoveAllViewportWidgets()" in helper
    # ... the game's software cursors aren't on-screen content: put back
    assert re.search(r"GetSoftwareCursorWidget\(Type\).*RemoveAllViewportWidgets\(\).*SetSoftwareCursorWidget\(",
                     helper, re.S)


def test_a_tap_lasts_two_engine_frames():
    """A press and release in the same frame never reach Enhanced Input's triggers: every tap (a key, a legacy action)
    waits for two engine frames as well as its 0.05 s before letting go."""
    source = open(INPUT_OPS, encoding="utf-8").read()
    helper = source[source.index("bool TapDone("):]
    helper = helper[:helper.index("\n\t}\n")]
    assert "GFrameCounter - StartFrame >= TapFrames" in helper and "TapFrames = 2" in source
    for op in ('Pending.Op = TEXT("key");', 'Pending.Op = TEXT("input");'):
        tap = source[source.index(op):]
        tap = tap[:tap.index("Bridge.AddPending")]
        assert "StartFrame = GFrameCounter" in tap and "TapDone(P, Now, StartFrame)" in tap


def _read(*parts):
    return open(os.path.join(ROOT, *parts), encoding="utf-8").read()


def _private(name):
    return open(os.path.join(PRIVATE, name), encoding="utf-8").read()


def test_a_request_for_another_player_plays_that_players_world():
    """Unreal's multiplayer Play In Editor: a request's "player" n (the kit's game.player(n), as Roblox's plugin takes
    it) is read once in Handle, a player not in the game is a 404 that says how many there are, and the player's ops
    (keys, input, move, view, screen, ui, watch, context, a still, rc's world context) act on that player's world.
    The hello says "players"; Unity's and Godot's don't (one player)."""
    ops = _private("PlaytestOps.cpp")
    handle = ops[ops.index("void Handle("):]
    assert handle.index("PlayerOf(Args)") < handle.index('Op == TEXT("key")')
    assert "!Bridge.GetPlayerWorld(Player)" in handle and "No player %d in the game" in handle and "404" in handle
    assert 'NumberField(Args, TEXT("player"), 1.0)' in ops
    subsystem = _private("PlaytestSubsystem.cpp")
    assert 'TEXT("players")' in subsystem[subsystem.index("void UPlaytestSubsystem::SendHello()"):]
    worlds = subsystem[subsystem.index("TArray<UWorld*> UPlaytestSubsystem::GetPlayerWorlds()"):]
    worlds = worlds[:worlds.index("\n}\n")]
    assert "RunAsDedicated" in worlds and "PIEInstance" in worlds and "StableSort" in worlds
    assert "return GetPlayerWorld(1);" in subsystem  # (GetGameWorld: player 1, as before)
    for name, count in (("PlaytestOpsInput.cpp", 3), ("PlaytestOpsUI.cpp", 3), ("PlaytestOpsView.cpp", 1),
                        ("PlaytestOpsSession.cpp", 1), ("PlaytestOpsRc.cpp", 1), ("PlaytestOpsTrace.cpp", 1)):
        assert _private(name).count("PlayerWorld(Bridge, Args)") >= count, name
    watch = _private("PlaytestOpsWatch.cpp")
    assert "Bridge.GetPlayerWorld(Watcher)" in watch and "Bridge.GetGameWorld()" not in watch
    media = _private("PlaytestMedia.cpp")
    capture = media[media.index("void FPlaytestMedia::Capture("):]
    assert 'TEXT("player")' in capture[:capture.index("\n}\n")]
    session = _private("PlaytestOpsSession.cpp")
    context = session[session.index("void Context("):]
    assert 'SetArrayField(TEXT("players"), Players(Bridge, Player))' in context
    assert 'SetNumberField(TEXT("you"), Player)' in context
    assert "Bridge.GetServerWorld(World)" in session  # (a client player's own server copies)
    unity = _read("plugin", "unity", "com.ramenvr.autopilot", "Runtime", "PlaytestClient.cs")
    godot = _read("plugin", "godot", "addons", "autopilot", "playtest.gd")
    godot_editor = _read("plugin", "godot", "addons", "autopilot", "editor_bridge.gd")
    for hello in (unity[unity.index('["features"]'):][:200], godot[godot.index('"features":'):][:120],
                  godot_editor[godot_editor.index('"features":'):][:120]):
        assert '"players"' not in hello


def test_each_player_holds_its_own_keys_and_the_pointer_is_player_ones():
    """Held keys are kept per player (a key player 2 holds isn't player 1's, and each player's lease runs out apart);
    the one pointer is player 1's: another player's pointer request is refused with what to use instead."""
    source = _private("PlaytestOpsInput.cpp")
    assert "TMap<int32, FPlayerKeys> HeldKeys;" in source and "KeysController" not in source
    tick = source[source.index("void TickKeys("):]
    assert "TickPlayerKeys(Bridge, PlayerKeys.Key, PlayerKeys.Value, DeltaTime)" in tick
    assert "Bridge.GetGameWorld()" not in source  # (every player's own world)
    # Each player's own lease: player 2's short lease doesn't let go of a key player 1 holds on a long one
    assert "KeyLeaseSeconds" not in source
    held = source[source.index("struct FPlayerKeys"):]
    assert "double LeaseSeconds" in held[:held.index("};")]
    player_tick = source[source.index("void TickPlayerKeys("):]
    assert "Now - Held.Renewed > Keys.LeaseSeconds" in player_tick[:player_tick.index("\n\t}")]
    key_op = source[source.index("void KeyOp("):]
    assert "Keys.LeaseSeconds = FMath::Clamp(Lease" in key_op
    pointer = _private("PlaytestOpsPointer.cpp")
    op = pointer[pointer.index("void PointerOp("):]
    assert op.index("PlayerOf(Args) != 1") < op.index("Bridge.GetGameWorld()") and "player 1's only" in op


def test_the_number_of_players_is_the_editors():
    """A session start or restart asking for another number of players than the editor's Play settings start is refused
    before anything changes (the run plays the game as the user set it up); the editor waits for its other players to
    join after player 1 plays, then answers whether or not they did."""
    session = _private("PlaytestOpsSession.cpp")
    start = session[session.index("void Session("):]
    asked = start.index('TEXT("players"), AskedPlayers')
    assert asked < start.index("Bridge.SessionHandler(") and "can't be changed from here" in start
    assert "Bridge.PlaySettingsPlayers" in start
    # Against the session the action leaves running: the editor's restart, or its start in another level, plays the Play
    # settings' number (it starts play again), whatever plays now; a start of a running game keeps its players
    check = start[asked:start.index("Bridge.SessionHandler(")]
    assert start.index("const bool bSwitch") < asked
    assert 'Bridge.SessionHandler && (Action == TEXT("restart") || bSwitch)' in check
    assert "!bNewSession ? Playing : Bridge.PlaySettingsPlayers" in check
    editor = _read("plugin", "unreal", "Autopilot", "Source", "AutopilotPlaytestEditor", "Private",
                   "AutopilotPlaytestEditorModule.cpp")
    assert "GetPlayNumberOfClients(Clients)" in editor and "GetRunUnderOneProcess(bOneProcess)" in editor
    assert "WaitForPlayers(" in editor[editor.index("void StartPie("):]
