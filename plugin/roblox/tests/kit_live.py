"""The brain's kit (autopilot.kit through autopilot.engines.roblox) on the test game in Roblox Studio: Game reads, the
kit frame, movement(), calibrate_look, look_at, sees, walk_to (lanterns collected), jump_to (the crate), go_and_press
(the lever's ProximityPrompt), walk_to up a ramp to a raised goal (the view's ways_up), the UI, restart. Studio needn't be in front (see harness.py).

    python plugin/roblox/tests/kit_live.py
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from harness import brain, check, summary  # noqa: E402

from autopilot.kit import control  # noqa: E402
from autopilot.kit.game import Game  # noqa: E402
from autopilot.kit.geom import Vec  # noqa: E402

STUD = 28.0


def drive(gen, hz=15.0, timeout=30.0):
    """Run a controller (a generator) to its end at hz ticks a second; its Result."""
    t0 = time.monotonic()
    try:
        while time.monotonic() - t0 < timeout:
            next(gen)
            time.sleep(1.0 / hz)
    except StopIteration as s:
        return s.value
    return control.Result(False, "test timeout")


def lanterns(game):
    return game.get("/ReplicatedStorage/GameState", "collected")


def run(game):
    check("engine from hello", game.engine.name == "roblox")
    game.request("session", {"action": "restart"}, timeout=90)
    t0 = time.monotonic()
    while not game.context(fresh=True).get("character") and time.monotonic() - t0 < 30:
        time.sleep(0.2)
    check("a pawn (the character) and its controller (the Player)", game.pawn.startswith("/Workspace/")
          and game.controller.startswith("/Players/"), f"{game.pawn} {game.controller}")
    time.sleep(1.0)
    found = game.actors("tag:Lantern")
    check("game.actors(tag)", len(found) == 6, str(found))
    check("game.named", len(game.named("lantern")) == 6, str(game.named("lantern")))
    check("game.name_of", game.name_of(found[0]).startswith("Lantern@"), game.name_of(found[0]))
    locs = game.locs(found)
    first = min(locs.values(), key=lambda v: v.y)
    check("game.locs (kit frame: Roblox (-12, 2, -24) is kit (672, -336, 56))", first.dist(Vec(24 * STUD, -12 * STUD,
                                                                                               2 * STUD)) < 1, str(first))
    center, ext = game.bounds("/Workspace/Grove/Crate")
    check("game.bounds", center.dist(Vec(6 * STUD, -16 * STUD, 2 * STUD)) < 1 and abs(ext.z - 2 * STUD) < 1,
          f"{center} {ext}")
    pos = game.get("/Workspace/Grove/Crate", "Position")
    check("game.to_kit(native)", game.to_kit(pos).dist(center) < 1, f"{pos} -> {game.to_kit(pos)}")
    n = game.to_native(Vec(6 * STUD, -16 * STUD, 2 * STUD))
    check("game.to_native(kit)", abs(n["x"] + 16) < 1e-6 and abs(n["y"] - 2) < 1e-6 and abs(n["z"] + 6) < 1e-6, str(n))
    p = game.pose()
    check("game.pose / me / eye / view_rotation", p["at"].dist(game.me()) < 30 and game.eye().z > p["at"].z,
          str(p))
    m = control.movement(game)
    check("movement()", m["walk_speed"] == 16 * STUD and m["jump_z"] > 1000 and m["half_height"] > 60
          and m["peak"] > 150, str({k: m.get(k) for k in ("walk_speed", "jump_z", "gravity", "peak", "reach_up",
                                                          "half_height")}))
    look = game.calibrate_look()
    check("calibrate_look (the default camera, turned with the right button held)", look["x"] and look["y"],
          str(look))
    check("hello features: timed_hold", "timed_hold" in game.features, str(sorted(game.features)))
    p0 = game.pose()["at"]
    game.hold_timed("D", 0.4)
    time.sleep(1.5)  # (the brain silent: no renewal, no release)
    moved = game.pose()["at"].dist2d(p0)
    check("a timed hold lasts its game time (0.4 s at 448/s)", 100 < moved < 300, f"moved {moved:.0f}")
    target = sorted(found, key=lambda a: game.loc(a).dist(game.me()))[0]
    r = drive(control.look_at(game, target), timeout=6)
    check("look_at a lantern", r, getattr(r, "why", ""))
    seen = game.sees([target])
    check("sees it near the middle", target in seen and abs(seen[target]["screen_x"]) < 0.2, str(seen))
    before = lanterns(game)
    r = drive(control.walk_to(game, target, within=30, timeout=20), timeout=25)
    time.sleep(0.5)
    check("walk_to a lantern collects it", lanterns(game) > before and not game.exists(target),
          f"{r and r.why} collected={lanterns(game)}")
    for t in sorted(game.actors("tag:Lantern"), key=lambda a: game.loc(a).dist(game.me()))[:2]:
        drive(control.walk_to(game, t, within=30, timeout=20), timeout=25)
    time.sleep(0.5)
    check("three lanterns", lanterns(game) >= 3, str(lanterns(game)))
    check("the HUD says so", game.screen().get("HUD.LanternCount", "").startswith(f"Lanterns {lanterns(game)}"),
          str(game.screen()))
    r = drive(control.jump_to(game, "/Workspace/Grove/Crate"), timeout=25)
    feet = game.pose()["at"].z - control.movement(game)["half_height"]
    check("jump_to the crate (4 studs high)", r and feet > 3 * STUD, f"{r and r.why} feet={feet:.0f}")
    r = drive(control.go_and_press(game, "/Workspace/Grove/Lever/Handle", "E", within=200), timeout=30)
    time.sleep(0.5)
    check("go_and_press the lever (ProximityPrompt, E)", game.get("/Workspace/Grove/Gate", "Open") is True,
          f"{r and r.why}")
    # walk_to a goal higher than a jump: up the ramp the view's ways_up tells (the Lookout, 9 studs, its ramp's foot at
    # Roblox x -34; the player put 10 studs east of it, off the grove's things)
    game.request("rc", {"verb": "property", "body": {"objectPath": "Character", "propertyName": "position",
                                                     "propertyValue": {"position": {"x": -24, "y": 3, "z": 0}},
                                                     "access": "WRITE_ACCESS"}})
    time.sleep(1.0)
    drive(control.look_at(game, "/Workspace/Grove/Lookout", tolerance=10.0), timeout=6)
    ways = control._ways_up_seen(game)
    check("view ways_up: the ramp up to the lookout", ways is not None and any(
        abs(top.z - 9 * STUD) < 40 and foot.dist2d(Vec(0, -34 * STUD, 0)) < 150 for foot, top in ways), str(ways))
    r = drive(control.walk_to(game, "/Workspace/Grove/Beacon", within=120, timeout=30), timeout=35)
    feet = game.pose()["at"].z - control.movement(game)["half_height"]
    check("walk_to the beacon on the lookout: up its ramp (ways_up)", r and feet > 8 * STUD,
          f"{r and r.why} feet={feet:.0f} at={game.pose()['at']}")
    ui = {e["id"]: e for e in game.ui()}
    check("game.ui()", "Menu.RingButton" in ui, str(sorted(ui)))
    game.ui_act(id="Menu.RingButton", action="click")
    time.sleep(0.5)
    check("ui_act: the button's script ran", game.screen().get("Menu.RingButton") == "Rang!", str(game.screen()))
    game.release_all()
    ok = game.restart()
    check("game.restart(): a fresh session", ok and game.get("/ReplicatedStorage/GameState", "collected") == 0
          and len(game.actors("tag:Lantern")) == 6)


def main():
    link = brain()
    try:
        run(Game(link))
    except Exception as e:
        import traceback

        traceback.print_exc()
        check("no exception", False, repr(e))
    finally:
        try:
            link.request("session", {"action": "stop"}, timeout=60)
        except Exception:
            pass
        link.server.shutdown()
    return summary()


if __name__ == "__main__":
    sys.exit(main())
