"""The kit's controllers (autopilot.kit.control) through the engine seam on a live, headless Unity player of the test
game (testgame.py): movement(), look_at, walk_to, jump_to, go_and_press, sees(), ui()/ui_act on the menu, restart.
Each controller is stepped at 15 Hz as the runtime steps executors (holds renewed every tick).

    python plugin/unity/com.ramenvr.autopilot/Tests~/kit_live.py [--exe <Build>/OrbCourse.exe] [--fps N]

Without --exe it makes the test game first and deletes it afterwards (--keep keeps it).
"""

import argparse
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import smoke  # noqa: E402
import testgame  # noqa: E402
from smoke import TOKEN, Brain, Proc, check, player_cmd  # noqa: E402

from autopilot.kit import control  # noqa: E402
from autopilot.kit.game import Game  # noqa: E402


def step(game, gen, hz=15.0, limit=60.0):
    """Run a controller to its end as the runtime does: one step a tick, holds renewed."""
    t0 = time.monotonic()
    try:
        while time.monotonic() - t0 < limit:
            game.renew()
            next(gen)
            time.sleep(1.0 / hz)
    except StopIteration as stop:
        return stop.value
    finally:
        game.release_all()
    return control.Result(False, "the test's limit")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", help="a Development player build of the test game (default: make the test game)")
    ap.add_argument("--keep", action="store_true", help="keep the test game this made")
    ap.add_argument("--port", type=int, default=18931)
    ap.add_argument("--work", default=os.path.join(tempfile.gettempdir(), "autopilot-unity-kit"))
    ap.add_argument("--fps", type=int, default=0, help="cap the player's frame rate (the test game's FrameCap: -fps N), "
                                                        "as a slow machine runs it")
    args = ap.parse_args()
    os.makedirs(args.work, exist_ok=True)
    made = None
    if not args.exe:
        made = testgame.make()
        args.exe = made.exe
    try:
        run(args)
    finally:
        if made and not args.keep:
            made.remove()
        elif made:
            print(f"kept the test game: {made.root}")
    passed = sum(1 for _, ok, _ in smoke.RESULTS if ok)
    print(f"\nkit: {passed}/{len(smoke.RESULTS)} checks passed")
    sys.exit(0 if passed == len(smoke.RESULTS) and smoke.RESULTS else 1)


def run(args):
    brain = Brain(args.port)
    proc = Proc(player_cmd(args.exe, brain.url, os.path.join(args.work, "player.log"), "-PlaytestConnect",
                           f"-PlaytestToken={TOKEN}", "-PlaytestMap=Main", *(["-fps", str(args.fps)] if args.fps else [])),
                None)
    try:
        link = brain.wait(60)
        game = Game(link)
        game.wait = None
        check("-PlaytestMap=Main: the player starts in the level", smoke.Game(brain).wait_for(
            "the level", lambda: game.context(fresh=True).get("world", "").endswith("Main.unity"), 15) is not None)
        check("engine from the hello", game.engine.name == "unity")
        m = control.movement(game)
        check("movement(): walk speed, jump, gravity, peak", m["walk_speed"] == 400 and abs(m["peak"] - 120) < 1
              and m["gravity"] == 2000 and m["half_height"] == 100, {k: m[k] for k in ("walk_speed", "jump_z", "gravity", "peak", "step", "reach_up")})
        look = game.calibrate_look()
        check("calibrate_look: degrees per mouse count", abs(look["x"] - 0.1) < 0.01 and abs(look["y"] - 0.1) < 0.01, look)
        check("hello features: timed_hold", "timed_hold" in game.features, sorted(game.features))
        p0 = game.pose()["at"]
        game.hold_timed("D", 0.4)
        time.sleep(1.5)  # (the brain silent: no renewal, no release)
        moved = game.pose()["at"].dist2d(p0)
        check("a timed hold lasts its game time (0.4 s at 400/s), the brain silent", 100 < moved < 230, round(moved))

        orbs = game.actors("Collectible")
        things = game.all_actors()
        check("all_actors: the scene's things, not their parts", "Main:/Gate" in things and "Main:/Gate/Panel" not in things
              and "Main:/Player/CameraPivot" not in things and "Main:/Orbs" not in things and set(orbs) <= set(things), things)
        far = orbs[1]
        r = step(game, control.look_at(game, far, tolerance=2.0, timeout=3.0))
        right, up = game.aim_error(game.bounds(far)[0])
        check("look_at an orb", bool(r) and abs(right) < 2.5 and abs(up) < 2.5, (r, round(right, 2), round(up, 2)))
        seen = game.sees(orbs)
        check("sees(): the orbs ahead, with the crosshair on the one looked at", far in seen and seen[far].get("crosshair_on") is True,
              {game.name_of(k): v for k, v in seen.items()})

        hud0 = game.screen().get("HUD.OrbsText")
        target = game.bounds(far)[0]
        r = step(game, control.walk_to(game, target, within=40, timeout=15))
        time.sleep(0.3)
        gone = not game.engine.transforms(game, [far]).get(far)
        check("walk_to an orb: reached and picked up", bool(r) and gone and game.screen().get("HUD.OrbsText") != hud0,
              (r.why if r else r, gone, game.screen().get("HUD.OrbsText")))

        block = "Main:/Level/Block"
        r = step(game, control.jump_to(game, block), limit=30)
        p = game.pose()
        feet = p["at"].z - m["half_height"]
        check("jump_to the block: the feet on its top (100 cm)", bool(r) and abs(feet - 100) < 15, (r.why, round(feet, 1)))

        gate = "Main:/Gate"
        r = step(game, control.go_and_press(game, gate, "E", within=150), limit=30)
        time.sleep(0.3)
        check("go_and_press the gate with E: it opens", game.get(gate + "|Gate", "isOpen") is True, r.why if r is not None else r)

        # Input actions by name through game.press / game.hold (the plugin's key op takes action names)
        jumps = game.get("Main:/Player", "jumps")
        step(game, control.until(game, lambda: game.pose().get("on_ground"), timeout=3))
        game.press("Player/Jump")
        time.sleep(0.3)
        check("game.press('Player/Jump') jumps", game.get("Main:/Player", "jumps") == jumps + 1)
        a = game.pose()
        # (back, away from the gate just opened: forward the player would walk into it)
        step(game, control.hold_for(game, "Player/Move", 0.6, value=(0, -1)))
        time.sleep(0.5)  # (stopped)
        b = game.pose()
        moved = (b["at"] - a["at"]).flat.length
        check("game.hold('Player/Move', (0, -1)) for 0.6 s walks back 0.6 s at 400/s", 200 < moved < 280, round(moved))

        # The menu: restart into it, click Start
        check("restart(map='Menu'): no pawn in the menu", game.restart(map="Menu", wait_for_pawn=2) is False)
        ui = {e["id"]: e for e in game.ui()}
        check("ui() on the menu", "MenuCanvas.StartButton" in ui, sorted(ui))
        game.ui_act(id="MenuCanvas.StartButton")
        check("ui_act Start: the level", smoke.Game(brain).wait_for("level", lambda: game.context(fresh=True).get("character"), 10) is not None)
        check("restart(): the level again, with its pawn", game.restart() is True and game.context(fresh=True)["world"].endswith("Main.unity"),
              game.context()["world"])
        check("restart: the orbs are back", len(game.actors("Collectible")) == 4)
    except Exception as e:
        import traceback

        traceback.print_exc()
        check("kit: ran to the end", False, repr(e))
    finally:
        proc.kill()
        brain.close()


if __name__ == "__main__":
    main()
