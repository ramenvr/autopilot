"""Finding the way (kit.nav, and walk_to on it) in simulated levels (tests/navsim.py: simulated time, no engine)."""

import navsim
import pytest

from autopilot.kit import walk_to
from autopilot.kit.geom import Vec
from autopilot.kit.nav import NavMap

Z = navsim.HALF_HEIGHT


def look(world, turns=range(-180, 180, 10), reach=1500):
    """A sonar look in the simulated level, as the plugin answers it."""
    view = world.request("view", {"sonar": True, "sonar_turns": list(turns), "sonar_reach": reach})
    return view["body"]["sonar"]["ways"]


def test_map_marks_a_wall_and_plans_round_it():
    world = navsim.World(walls=[(500, -1500, 560, 1500)])
    nav = NavMap()
    nav.observe(world.pos, world.yaw, look(world), t=1.0)
    goal = Vec(1100, 0, Z)
    assert not nav.line_free(world.pos, goal, Z)
    way = nav.plan(world.pos, goal, 100, Z)
    assert way and len(way) >= 2, "a way with a corner, not straight through"
    assert not nav.way_blocked(world.pos, way, Z)
    # (what one look felt is crossed nowhere near the middle, where it felt the wall closely)
    assert all(abs(p.y) > 150 for p in way[:-1])


def test_a_sweep_through_a_cell_opens_it_again():
    nav = NavMap()
    at = Vec(0, 0, Z)
    nav.observe(at, 0.0, [{"turn": 0, "clear": 300, "by": "wall", "normal": {"X": -1, "Y": 0}}], t=1.0)
    assert nav.blocked(Vec(325, 0, Z), Z)
    nav.observe(at, 0.0, [{"turn": 0, "clear": 1000, "by": "reach"}], t=2.0)  # (a door opened)
    assert not nav.blocked(Vec(325, 0, Z), Z)


def test_marks_belong_to_their_floor():
    nav = NavMap()
    nav.observe(Vec(0, 0, Z), 0.0, [{"turn": 0, "clear": 300, "by": "wall"}], t=1.0, feet=0.0)
    assert nav.blocked(Vec(325, 0, 0), 0.0)
    assert not nav.blocked(Vec(325, 0, 0), 400.0)  # (the floor above: its own walls)


def shortest(world, start, target, within):
    """The length of the way A* plans on a map that knows the whole level already (every cell the body's middle can't
    be in marked hit): what a walk with a perfect map would walk."""
    nav = NavMap()
    xs = [x for w in world.walls + world.pits for x in (w[0], w[2])] + [start.x, target.x]
    ys = [y for w in world.walls + world.pits for y in (w[1], w[3])] + [start.y, target.y]
    c = nav.cell
    for i in range(int(min(xs) // c) - 8, int(max(xs) // c) + 8):
        for j in range(int(min(ys) // c) - 8, int(max(ys) // c) + 8):
            x, y = (i + 0.5) * c, (j + 0.5) * c
            if world._inside(x, y) or world._in_pit(x, y):
                nav.hits[(i, j, nav._k(Z))] = 0.0
    way = nav.plan(start, target, within, Z, margin=5000)
    return sum(a.dist2d(b) for a, b in zip([start] + way, way))


@pytest.mark.parametrize("name", ["open field", "wide wall", "very wide wall", "dead end", "room door far side",
                                  "zigzag", "wall with a gap", "pit", "pillars", "narrow door"])
def test_walk_to_finds_its_way(name):
    kw, target, within = next((kw, t, w) for n, kw, t, w in navsim.levels() if n == name)
    world = navsim.World(**kw)
    res, seconds = world.run(walk_to(world.game(), target, within=within, timeout=40), limit=45)
    assert res is not None and res.ok, (name, res)
    assert not world.fell
    best = shortest(navsim.World(**kw), Vec(0, 0, Z), target, within)
    # (within a third of the way a perfect map would walk; a room whose door is round its far side must be walked
    # round to find it)
    bound = 2.5 if name == "room door far side" else 1.35
    assert world.travelled < bound * best + 300, (name, round(world.travelled), round(best))


def test_walk_to_straight_across_open_floor_is_as_quick_as_before():
    world = navsim.World()
    res, seconds = world.run(walk_to(world.game(), Vec(2000, 300, Z), within=100, timeout=20))
    assert res.ok and res.data["replans"] == 0
    assert seconds < 2000 / world.speed + 0.8


def test_walk_to_gives_up_on_a_target_it_cant_get_to():
    kw, target, within = next((kw, t, w) for n, kw, t, w in navsim.levels() if n == "closed room")
    world = navsim.World(**kw)
    res, seconds = world.run(walk_to(world.game(), target, within=within, timeout=30), limit=35)
    assert res is not None and not res.ok
    assert res.why in ("stuck", "walk timed out")


def test_walk_to_remembers_the_level_for_the_next_walk():
    kw, target, within = next((kw, t, w) for n, kw, t, w in navsim.levels() if n == "dead end")
    world = navsim.World(**kw)
    game = world.game()
    first, t1 = world.run(walk_to(game, target, within=within, timeout=40), limit=45)
    world.pos, world.yaw = Vec(0, 0, Z), 0.0  # (back to the start, as after a level restart)
    second, t2 = world.run(walk_to(game, target, within=within, timeout=40), limit=45)
    assert first.ok and second.ok
    assert t2 < t1


def test_walk_to_through_random_clutter():
    arrived = 0
    for seed in range(12):
        kw, target = navsim.clutter(seed)
        world = navsim.World(**kw)
        res, _ = world.run(walk_to(world.game(), target, within=100, timeout=40), limit=45)
        arrived += bool(res is not None and res.ok)
    assert arrived == 12


def test_walk_to_never_walks_off_a_deep_drop():
    # (the target across a pit with no way round: the walk stops at its edge)
    world = navsim.World(pits=[(400, -5000, 900, 5000)])
    res, _ = world.run(walk_to(world.game(), Vec(1300, 0, Z), within=100, timeout=20), limit=25)
    assert res is not None and not res.ok
    assert not world.fell


def test_walk_to_gives_up_soon_when_the_view_wont_turn():
    # (the target behind the player and the mouse doing nothing: no waiting out the timeout)
    world = navsim.World(yaw=180.0, view_locked=True)
    res, seconds = world.run(walk_to(world.game(), Vec(1500, 0, Z), within=100, timeout=25), limit=30)
    assert res is not None and not res.ok
    assert "turn" in res.why
    assert seconds < 4.0
