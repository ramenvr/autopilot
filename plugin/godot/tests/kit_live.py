"""The brain's kit (autopilot.kit through autopilot.engines.godot) on the live headless test game: Game reads, the menu's UI,
movement(), calibrate_look, look_at, sees, walk_to, jump_to, go_and_press, restart(map=...).

    .venv\\Scripts\\python.exe plugin\\godot\\tests\\kit_live.py [--port 18953]
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from harness import Brain, Checks, project, start_godot, stop  # noqa: E402

from autopilot.kit import control  # noqa: E402
from autopilot.kit.game import Game  # noqa: E402
from autopilot.kit.geom import Vec  # noqa: E402


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=18953)
    ap.add_argument("--max-fps", type=int, default=0, help="cap the game's frame rate (Godot's --max-fps), as a slow "
                                                            "machine runs it")
    a = ap.parse_args()
    c = Checks()
    brain = Brain(a.port)
    log = os.path.join(os.environ.get("TEMP", "."), "godot_kit_live.log")
    fps = ["--max-fps", str(a.max_fps)] if a.max_fps else []
    game_proc = start_godot(fps + ["--path", project(), "--", f"--playtest-url={brain.url}"], log=log)
    try:
        link = brain.wait(60)
        run(c, Game(link))
    except Exception as e:
        import traceback

        traceback.print_exc()
        c.check("no exception", False, repr(e))
    finally:
        stop(game_proc)
        brain.close()
    sys.exit(0 if c.summary() else 1)


def gs(game, prop):
    return game.get("/root/GameState", prop)


def run(c, game):
    c.check("engine from hello", game.engine.name == "godot")
    time.sleep(0.3)
    c.check("in the menu: no pawn", game.pawn == "" and game.world == "/root/Menu", game.world)
    ui = {e["id"]: e for e in game.ui()}
    c.check("game.ui()", "Menu.PlayButton" in ui, str(sorted(ui)))
    game.ui_act(id="Menu.PlayButton", action="click")
    t0 = time.monotonic()
    while not game.context(fresh=True).get("character") and time.monotonic() - t0 < 10:
        time.sleep(0.1)
    c.check("ui_act Play: the level runs, a pawn", game.pawn == "/root/Level/Player", game.pawn)
    time.sleep(0.3)
    apples = game.actors("Fruit")
    c.check("game.actors(class_name)", len(apples) == 6, str(apples))
    c.check("game.named", len(game.named("apple")) >= 6)
    c.check("game.name_of", game.name_of(apples[0]).startswith("Apple"), game.name_of(apples[0]))
    locs = game.locs(apples)
    c.check("game.locs (kit frame)", locs.get("/root/Level/Apple1") == Vec(400, 0, 60), str(locs.get("/root/Level/Apple1")))
    center, ext = game.bounds("/root/Level/Gate")
    c.check("game.bounds", center == Vec(1600, 0, 150) and abs(ext.y - 150) < 1, f"{center} {ext}")
    pos = game.get("/root/Level/Apple4", "position")
    c.check("game.to_kit(native)", game.to_kit(pos).dist(Vec(600, 300, 60)) < 0.01, f"{pos} -> {game.to_kit(pos)}")
    c.check("game.to_native(kit)", game.to_native(Vec(600, 300, 60)) == {"x": 3.0, "y": 0.6, "z": -6.0})
    p = game.pose()
    c.check("game.pose / me / eye / view_rotation", p["at"] == Vec(0, 0, 90) and game.eye() == Vec(0, 0, 160)
            and game.view_rotation() == (0.0, 0.0) and game.me() == Vec(0, 0, 90), str(p))
    m = control.movement(game)
    c.check("movement()", m["walk_speed"] == 500 and m["jump_z"] == 450 and m["half_height"] == 90
            and abs(m["peak"] - 450 ** 2 / (2 * 980)) < 1, str({k: m[k] for k in ("walk_speed", "jump_z", "gravity", "peak", "reach_up")}))
    look = game.calibrate_look()
    c.check("calibrate_look", look["x"] and abs(look["x"] - 0.1719) < 0.005 and look["y"] and abs(look["y"] - 0.1719) < 0.005, str(look))
    c.check("hello features: timed_hold", "timed_hold" in game.features, str(sorted(game.features)))
    p0 = game.pose()["at"]
    game.hold_timed("D", 0.4)
    time.sleep(1.5)  # (the brain silent: no renewal, no release)
    moved = game.pose()["at"].dist2d(p0)
    c.check("a timed hold lasts its game time (0.4 s at 500/s), the brain silent", 120 < moved < 270, f"moved {moved:.0f}")
    r = drive(control.look_at(game, "/root/Level/Apple4"), timeout=5)
    c.check("look_at", r, getattr(r, "why", ""))
    seen = game.sees(apples)
    c.check("sees", "/root/Level/Apple4" in seen and abs(seen["/root/Level/Apple4"]["screen_x"]) < 0.1, str(seen.get("/root/Level/Apple4")))
    r = drive(control.walk_to(game, "/root/Level/Apple1", within=40, timeout=15))
    time.sleep(0.2)
    c.check("walk_to an apple picks it up", gs(game, "apples") >= 1 and not game.exists("/root/Level/Apple1"),
            f"{r and r.why} apples={gs(game, 'apples')}")
    for path in ("/root/Level/Apple2", "/root/Level/Apple5"):
        drive(control.walk_to(game, path, within=40, timeout=15))
    time.sleep(0.2)
    n = gs(game, "apples")
    c.check("three apples", n >= 3, str(n))
    r = drive(control.jump_to(game, "/root/Level/HayBale"), timeout=20)
    feet = game.pose()["at"].z - 90
    c.check("jump_to the hay bale", r and feet > 70, f"{r and r.why} feet={feet:.0f}")
    r = drive(control.go_and_press(game, "/root/Level/Lever", "interact", within=150), timeout=25)
    time.sleep(0.2)
    c.check("go_and_press the lever (an InputMap action)", gs(game, "gate_open") is True, f"{r and r.why}")
    game.release_all()
    ok = game.restart(map="level")
    c.check("game.restart(map)", ok and game.pawn == "/root/Level/Player" and game.exists("/root/Level/Apple1"))
    ok = game.restart()
    c.check("game.restart() reopens the launch scene (menu: no pawn)", ok is False and game.world == "/root/Menu", game.world)


if __name__ == "__main__":
    main()
