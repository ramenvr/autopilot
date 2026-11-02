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


def named(v, name):
    return next((t for t in v.get("visible", []) if t.get("name") == name), None)


def view_space(g, game):
    """view's spatial reads, as Unreal's: ways up (the ramp to the platform, with its lane) and walk_to up it, a
    thing's top slope and ends, openings (the doorway), point blank, what is held, what is overhead, lights, effects
    and highlights. Unity's axes to the kit's: (X, Y, Z) = 100 * (z, x, y)."""
    teleport(g, "Main:/Player", {"x": -13, "y": 1.08, "z": 1})
    face(g, 0)
    v = game.view(ways_up=True)
    check("view: nothing omitted (held, openings, ways_up, lane, lights... all told)", "omitted" not in v, v.get("omitted"))
    ways = v["body"].get("ways_up") or []
    up = next((w for w in ways if abs(w["foot"]["Y"] + 1300) < 120), None)
    check("ways_up: the ramp ahead, foot on the floor 3-4 m out, top 2 m up near the platform",
          up is not None and 280 <= up["foot"]["X"] <= 460 and abs(up["foot"]["Z"]) <= 15 and abs(up["top"]["Z"] - 200) <= 20
          and 950 <= up["top"]["X"] <= 1200 and abs(up["rises_by"] - 200) <= 25, ways)
    lane = (up or {}).get("lane") or {}
    check("ways_up: the ramp's lane (2 m wide, drops at its sides)", 140 <= lane.get("width", 0) <= 215
          and lane.get("left", {}).get("by") == "drop" and lane.get("right", {}).get("by") == "drop", lane)
    ramp = named(v, "Ramp")
    check("view: the ramp's top slope (18 degrees), uphill straight ahead, its low end on the floor",
          ramp is not None and abs(ramp.get("top_slope_degrees", 0) - 18) <= 2 and abs(ramp.get("uphill", {}).get("yaw", 99)) <= 5
          and abs(ramp.get("low_end", {}).get("at", {}).get("Z", 99)) <= 15, ramp)
    check("view: the ramp's lane", 140 <= ((ramp or {}).get("lane") or {}).get("width", 0) <= 215, (ramp or {}).get("lane"))
    box = (ramp or {}).get("box") or {}
    check("view: the ramp tipped 18 degrees (tilt) and its own box (6.3 m long, 2 m wide, 20 cm thick)",
          abs((ramp or {}).get("tilt", {}).get("pitch", 0) - 18) <= 1 and abs(box.get("size", {}).get("X", 0) - 632) <= 3
          and abs(box.get("size", {}).get("Y", 0) - 200) <= 2 and abs(box.get("size", {}).get("Z", 0) - 20) <= 2 and v.get("oriented") is True,
          ((ramp or {}).get("tilt"), box))
    target = Vec(1300, -1300, 250)  # (kit: on the platform, Unity (-13, 2.5, 13))
    r = step(game, control.walk_to(game, target, within=120, timeout=40), limit=45)
    p = game.pose()["at"]
    check("walk_to a goal on the platform: up the ramp it sees", bool(r) and p.z > 270 and p.dist2d(target) <= 170,
          f"{r.why if r is not None else r}, ended {p.rounded()}")

    # The doorway in the wall across z = -14 (x -8.6 to -7.4), 4 m ahead
    teleport(g, "Main:/Player", {"x": -8, "y": 1.08, "z": -10})
    face(g, 180)
    v = game.view(openings=True)
    door = next((o for o in v["body"].get("openings") or [] if abs(o["width"] - 120) <= 30), None)
    check("openings: the doorway 4 m ahead, 1.2 m wide, passable", door is not None and 340 <= door["distance"] <= 430
          and abs(door["turn"]) <= 10 and door["passable"] is True and door.get("reachable_straight") is True
          and set(door.get("sides", [])) <= {"HouseWall", "HouseWall@1"}, v["body"].get("openings"))
    check("openings: free, how far the body goes across the view", len(v["body"].get("free") or []) >= 4, v["body"].get("free"))

    # Point blank: the block 80 cm ahead (its face 40 cm past the body), lower than the chest
    teleport(g, "Main:/Player", {"x": 4, "y": 1.08, "z": 0.7})
    face(g, 0)
    v = game.view(max_count=1, sonar=True, sonar_turns=[0], sonar_reach=300)
    pb = v["body"]["sonar"].get("point_blank") or {}
    check("sonar point_blank: the block at the knees, nothing at the chest", pb.get("thing") == "Block" and 32 <= (pb.get("knee") or 0) <= 48
          and pb.get("chest") is None and pb.get("within") == 120, pb)

    # The lamp's light at the block, the beacon's sparks and outline, the torch in hand, the sun
    teleport(g, "Main:/Player", {"x": 4, "y": 1.08, "z": -1})
    face(g, 0)
    v = game.view()
    block = named(v, "Block") or {}
    check("view: the lamp's light counted at the block", (block.get("look", {}).get("lights") or {}).get("on", 0) >= 1, block.get("look"))
    teleport(g, "Main:/Player", {"x": 0, "y": 1.08, "z": 0})
    face(g, 0)
    time.sleep(0.5)
    v = game.view()
    beacon = (named(v, "Beacon") or {}).get("look") or {}
    check("view: the beacon is highlighted (an outline material over its mesh)", beacon.get("highlighted") is True, beacon)
    check("view: the beacon's sparks playing", any(e.get("asset") == "Sparks" for e in beacon.get("effects") or []), beacon)
    held = {h["name"]: h for h in v.get("held") or []}
    torch = held.get("Torch") or {}
    check("held: the torch in front of the camera", torch.get("attached_at") == "CameraPivot" and torch.get("in_view") is True
          and torch.get("look", {}).get("parts") == ["Cube"] and "Torch" not in [t["name"] for t in v["visible"]], v.get("held"))
    check("scene: the sun's brightness", v.get("scene", {}).get("sun") == 1.0, v.get("scene"))

    # Under the canopy (its underside 7 cm over the head)
    teleport(g, "Main:/Player", {"x": -12, "y": 1.08, "z": -4})
    time.sleep(0.2)
    v = game.view(openings=True)
    over = v["body"].get("overhead") or {}
    check("overhead: the canopy just over the head", over.get("thing") == "Canopy" and 0 <= over.get("clearance", -1) <= 15, v["body"].get("overhead"))
    check("under: the canopy (openings asked)", (v["body"].get("under") or {}).get("thing") == "Canopy", v["body"].get("under"))


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
        view_space(g, game)
    except Exception as e:
        import traceback

        traceback.print_exc()
        check("sonar: ran to the end", False, repr(e))
    finally:
        proc.kill()
        brain.close()


if __name__ == "__main__":
    main()
