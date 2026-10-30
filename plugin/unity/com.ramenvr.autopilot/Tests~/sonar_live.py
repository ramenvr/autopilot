"""view's sonar on a live, headless Unity player of the test game (testgame.py), and the kit's walk_to steering round
a wall with it. The test level: a 40 m floor (edges at +-20 m: drops), a wall 10 m wide across z = -8 m behind the
player's start, a gate's panel 6 m to its right (+x), a 1 m block.

    python plugin/unity/com.ramenvr.autopilot/Tests~/sonar_live.py [--exe <Build>/OrbCourse.exe]

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
from kit_live import step  # noqa: E402
from smoke import TOKEN, Brain, Proc, check, player_cmd, teleport  # noqa: E402

from autopilot.kit import control  # noqa: E402
from autopilot.kit.game import Game  # noqa: E402
from autopilot.kit.geom import Vec  # noqa: E402


def face(g, yaw):
    """Turn the player (and so its view) to a Unity euler y."""
    g.set("Main:/Player", "eulerAngles", {"x": 0, "y": yaw, "z": 0})
    time.sleep(0.2)


def ways(game, turns, reach=1500):
    v = game.view(max_count=1, sonar=True, sonar_turns=turns, sonar_reach=reach)
    return {w["turn"]: w for w in v["body"]["sonar"]["ways"]}, v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", help="a Development player build of the test game (default: make the test game)")
    ap.add_argument("--keep", action="store_true", help="keep the test game this made")
    ap.add_argument("--port", type=int, default=18961)
    ap.add_argument("--work", default=os.path.join(tempfile.gettempdir(), "autopilot-unity-sonar"))
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
    print(f"\nsonar: {passed}/{len(smoke.RESULTS)} checks passed")
    sys.exit(0 if passed == len(smoke.RESULTS) and smoke.RESULTS else 1)


def run(args):
    brain = Brain(args.port)
    proc = Proc(player_cmd(args.exe, brain.url, os.path.join(args.work, "player.log"), "-PlaytestConnect",
                           f"-PlaytestToken={TOKEN}", "-PlaytestMap=Main"), None)
    try:
        link = brain.wait(60)
        g = smoke.Game(brain)
        game = Game(link)
        g.wait_for("the level", lambda: game.context(fresh=True).get("character"), 15)
        # Facing the wall (euler y 180: toward -z) from the start: the wall's face is 7.75 m away, the body 0.4 m wide
        face(g, 180)
        w, v = ways(game, [0, -30, 30, 90, -90, 180])
        check("sonar is told (not in omitted)", "sonar" not in v.get("omitted", []) and v["body"]["sonar"]["reach"] == 1500, v.get("omitted"))
        ahead = w[0]
        check("sonar: the wall straight ahead (about 735 cm across the floor)", ahead["by"] == "wall" and ahead.get("thing") == "Wall"
              and 700 <= ahead["clear"] <= 760, ahead)
        check("sonar: the wall's normal faces the player (kit +X: toward +z)", ahead.get("normal", {}).get("X", 0) > 0.95, ahead.get("normal"))
        check("sonar: 30 degrees off, the wall is farther (about 735/cos 30)", w[30]["by"] == "wall" and 800 <= w[30]["clear"] <= 900
              and w[-30]["by"] == "wall", (w[30], w[-30]))
        check("sonar: right and behind, the floor goes on to the reach", all(w[t]["by"] == "reach" and w[t]["clear"] == 1500 for t in (90, 180)),
              {t: (w[t]["by"], w[t]["clear"]) for t in (90, 180)})
        # (left, toward +x: the gate's panel, 6 m away, its near face at 5.9 m, the body 0.36 m wide)
        check("sonar: left, the gate's panel", w[-90]["by"] == "wall" and w[-90].get("thing") == "Gate" and 530 <= w[-90]["clear"] <= 570, w[-90])
        # Near the floor's edge (x = 20 m), facing it: a drop with no floor under it
        teleport(g, "Main:/Player", {"x": 15, "y": 1.08, "z": 4})
        face(g, 90)
        w, _ = ways(game, [0, 180], reach=1000)
        check("sonar: the floor's edge 5 m ahead is a drop, too deep to tell", w[0]["by"] == "drop" and w[0].get("drop") is None
              and 440 <= w[0]["clear"] <= 520, w[0])
        check("sonar: behind, open floor", w[180]["by"] == "reach" and w[180]["clear"] == 1000, w[180])
        # The block (1 m high) ahead: a wall (higher than a step)
        teleport(g, "Main:/Player", {"x": 4, "y": 1.08, "z": -1})
        face(g, 0)
        w, _ = ways(game, [0], reach=600)
        check("sonar: the block ahead is a wall (1 m, above a step)", w[0]["by"] == "wall" and w[0].get("thing") == "Block"
              and 200 <= w[0]["clear"] <= 230, w[0])  # (its face 2.5 m ahead, the body 0.36 m wide)

        # walk_to a point behind the wall: straight there would walk into it; sonar steers round
        teleport(g, "Main:/Player", {"x": 0.5, "y": 1.08, "z": 0})
        face(g, 180)
        target = Vec(-1100, 350, 108)  # kit frame: Unity (3.5, 1.08, -11), 3 m behind the wall, toward its end at x = 5
        path = []

        def watched():
            p = game.pose()["at"]
            path.append(p)
            return target

        started = time.time()
        r = step(game, control.walk_to(game, watched, within=120, timeout=40), limit=45)
        p = game.pose()["at"]
        widest = max(abs(q.y) for q in path) if path else 0
        # (walk_to settles as arrived up to `within` + 30 cm away: it stops early rather than overshoot)
        check("walk_to behind the wall: arrives by going round it", bool(r) and p.dist2d(target) <= 150 and widest > 500,
              f"{r.why if r is not None else r} in {time.time() - started:.0f}s, ended {p.rounded()}, widest |Y| {widest:.0f} cm")
    except Exception as e:
        import traceback

        traceback.print_exc()
        check("sonar: ran to the end", False, repr(e))
    finally:
        proc.kill()
        brain.close()


if __name__ == "__main__":
    main()
