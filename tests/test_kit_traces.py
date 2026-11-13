"""Boxes and traces: `game.bounds` is the box round what of a thing collides (its drawn box only when nothing of it
collides, or when asked), and `game.trace` asks the game's collision along a line or a swept ball from any point. Every
engine's plugin answers `trace` (its hello's "trace"); an older one gives a clear error."""
import os
import re

import pytest

from autopilot import engines
from autopilot.kit.game import Body, Game, GameError
from autopilot.kit.geom import Vec

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Link:
    """A plugin that records what it was asked and answers from `answers` (op -> callable(args))."""

    def __init__(self, engine="unreal", features=("timed_hold", "pointer", "trace"), answers=None):
        self.hello = {"project": "fake", "engine": engine, "features": list(features)}
        self.answers = answers or {}
        self.asked = []
        self.game_time = 0.0

    def request(self, op, args=None, timeout=None):
        self.asked.append((op, args))
        if op in self.answers:
            return self.answers[op](args or {})
        return {}

    def post(self, op, args=None):
        self.request(op, args)


def _bounds_answer(colliding, drawn):
    def rc(args):
        body = args["body"]
        assert body["functionName"] == "GetActorBounds"
        box = colliding if body["parameters"]["bOnlyCollidingComponents"] else drawn
        return {"Origin": box[0], "BoxExtent": box[1]}
    return {"rc": rc}


def _colliding_flags(link):
    return [a["body"]["parameters"]["bOnlyCollidingComponents"] for op, a in link.asked if op == "rc"]


def test_unreal_bounds_are_the_colliding_components_box():
    # A big prop: drawn 3600 wide, solid 550 of it
    link = Link(answers=_bounds_answer(({"X": 10, "Y": 0, "Z": 50}, {"X": 275, "Y": 275, "Z": 50}),
                                       ({"X": 0, "Y": 0, "Z": 400}, {"X": 1800, "Y": 1800, "Z": 400})))
    game = Game(link)
    center, ext = game.bounds("/Game/M.M:PersistentLevel.Prop_3")
    assert (center, ext) == (Vec(10, 0, 50), Vec(275, 275, 50))
    assert _colliding_flags(link) == [True]
    # ... and the drawn box when asked
    link.asked.clear()
    assert game.bounds("/Game/M.M:PersistentLevel.Prop_3", visual=True) == (Vec(0, 0, 400), Vec(1800, 1800, 400))
    assert _colliding_flags(link) == [False]


def test_unreal_bounds_fall_back_to_the_drawn_box_when_nothing_collides():
    # (GetActorBounds with nothing colliding answers an empty box: zero extent at the origin)
    zero = {"X": 0, "Y": 0, "Z": 0}
    link = Link(answers=_bounds_answer((zero, zero), ({"X": 500, "Y": 20, "Z": 30}, {"X": 40, "Y": 40, "Z": 40})))
    center, ext = Game(link).bounds("/Game/M.M:PersistentLevel.Decal_1")
    assert (center, ext) == (Vec(500, 20, 30), Vec(40, 40, 40))
    assert _colliding_flags(link) == [True, False]


@pytest.mark.parametrize("engine", ["unity", "godot", "roblox"])
def test_op_engine_bounds_ask_for_the_drawn_box_only_when_asked(engine):
    def transforms(args):
        drawn = args.get("visual") is True
        box = {"location": {"X": 1, "Y": 2, "Z": 3}, "center": {"X": 0, "Y": 0, "Z": 9 if drawn else 5},
               "extent": {"X": 90 if drawn else 10, "Y": 10, "Z": 10}}
        return {"objects": {o: box for o in args["objects"]}}

    link = Link(engine=engine, answers={"transforms": transforms})
    game = Game(link)
    assert game.bounds("Main:/Prop") == (Vec(0, 0, 5), Vec(10, 10, 10))
    assert game.bounds("Main:/Prop", visual=True) == (Vec(0, 0, 9), Vec(90, 10, 10))
    asked = [a for op, a in link.asked if op == "transforms"]
    assert asked == [{"objects": ["Main:/Prop"]}, {"objects": ["Main:/Prop"], "visual": True}]


def test_trace_asks_the_game_and_answers_vecs():
    hit = {"location": {"X": 480, "Y": 0, "Z": 60}, "point": {"X": 520, "Y": 0, "Z": 60},
           "normal": {"X": -1, "Y": 0, "Z": 0}, "distance": 480.0, "actor": "/Game/M.M:PersistentLevel.Rail_2",
           "name": "Rail_2", "component": "StaticMeshComponent0", "inside": False}
    answers = iter([{"channel": "body", "hit": hit}, {"channel": "sight", "hit": None}])
    link = Link(answers={"trace": lambda args: next(answers)})
    game = Game(link)
    got = game.trace(Vec(0, 0, 60), (1000, 0, 60), radius=40)
    assert got["location"] == Vec(480, 0, 60) and got["point"] == Vec(520, 0, 60) and got["normal"] == Vec(-1, 0, 0)
    assert got["actor"].endswith("Rail_2") and got["distance"] == 480.0 and got["inside"] is False
    assert game.trace({"X": 0, "Y": 0, "Z": 60}, Vec(0, 0, -500), sight=True) is None
    asked = [a for op, a in link.asked if op == "trace"]
    assert asked == [
        {"start": {"X": 0.0, "Y": 0.0, "Z": 60.0}, "end": {"X": 1000.0, "Y": 0.0, "Z": 60.0}, "radius": 40.0},
        {"start": {"X": 0.0, "Y": 0.0, "Z": 60.0}, "end": {"X": 0.0, "Y": 0.0, "Z": -500.0}, "radius": 0.0,
         "channel": "sight"}]


def test_trace_from_another_player_says_which_player():
    link = Link(features=("trace", "players"), answers={"trace": lambda args: {"hit": None}})
    game = Game(link)
    body = Body(game, 2)
    assert body.trace(Vec(0, 0, 0), Vec(100, 0, 0)) is None
    assert link.asked[-1][1]["player"] == 2


def test_trace_with_an_older_plugin_says_so():
    link = Link(features=("timed_hold", "pointer"))
    with pytest.raises(GameError, match="can't trace"):
        Game(link).trace(Vec(0, 0, 0), Vec(100, 0, 0))
    assert not [op for op, _ in link.asked if op == "trace"]


def test_every_plugin_says_it_traces_and_answers_trace():
    private = os.path.join(ROOT, "plugin", "unreal", "Autopilot", "Source", "AutopilotPlaytest", "Private")
    unity = os.path.join(ROOT, "plugin", "unity", "com.ramenvr.autopilot", "Runtime")
    godot = os.path.join(ROOT, "plugin", "godot", "addons", "autopilot")
    roblox = os.path.join(ROOT, "plugin", "roblox", "src")

    def read(*parts):
        with open(os.path.join(*parts), encoding="utf-8") as f:
            return f.read()

    # Unreal: the hello's feature, the op routed, and its handler on the pawn's own collision
    assert 'TEXT("trace")' in read(private, "PlaytestSubsystem.cpp")
    assert re.search(r'Op == TEXT\("trace"\)\)\s*\{\s*Trace\(', read(private, "PlaytestOps.cpp"))
    trace = read(private, "PlaytestOpsTrace.cpp")
    for call in ("InitSweepCollisionParams", "SweepSingleByChannel", "LineTraceSingleByChannel"):
        assert call in trace
    # Unity (with its Physics module), Godot (game and editor hellos), Roblox (the editor's hello; the client answers)
    assert '"trace"' in read(unity, "PlaytestClient.cs") and 'case "trace":' in read(unity, "Ops.cs")
    assert os.path.exists(os.path.join(unity, "Trace.cs.meta"))  # (a package file without one is ignored)
    assert '"trace"]' in read(godot, "playtest.gd").split('"features":', 1)[1].split("\n", 1)[0]
    assert '"trace"]' in read(godot, "editor_bridge.gd").split('"features":', 1)[1].split("\n", 1)[0]
    assert '"trace":\n\t\t\treturn Trace.trace(world, args)' in read(godot, "playtest.gd")
    assert '"trace" }' in read(roblox, "Editor.luau").split("features = {", 1)[1].split("\n", 1)[0]
    assert "trace = function(args)" in read(roblox, "Client.luau")


def test_the_kit_reference_tells_boxes_traces_and_analog_input():
    kit = open(os.path.join(ROOT, "autopilot", "KIT.md"), encoding="utf-8").read()
    for words in ("game.trace(start, end, radius=0, sight=False)", "bounds(path, visual=True)", "Gamepad_LeftY",
                  "slow-down radius"):
        assert words in kit
    # ... and every other engine's section what its boxes and traces are (Unreal's prompt is KIT.md itself)
    for name in ("unity", "godot", "roblox"):
        assert "Boxes and traces" in engines.get(name).kit_section(), name
