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
