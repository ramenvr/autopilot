"""Finding the way (kit.nav, and walk_to on it) in simulated levels (tests/navsim.py: simulated time, no engine)."""

import math
import random
import time

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


# -- a goal the map shows no way to (here inside another player's felt outline): the ~5 s pauses of the CircleEachOther
# replays, 26 Nov, were the tick thread inside nav.plan from _walk_on, which searched three boxes, then again ---------

def felt_body(goal, half=25.0):
    """A level with a body (another player, 50 wide) at goal, and a map that felt it with sonar from all round it, as
    walking round and round it does."""
    world = navsim.World(walls=[(goal.x - half, goal.y - half, goal.x + half, goal.y + half)])
    nav = NavMap()
    for n in range(12):
        a = math.radians(30 * n)
        world.pos = Vec(goal.x + 110 * math.cos(a), goal.y + 110 * math.sin(a), Z)
        world.yaw = (30 * n + 180) % 360
        nav.observe(world.pos, world.yaw, look(world), t=10.0 + 0.2 * n)
    return world, nav


def test_plan_gives_up_at_once_on_a_body_it_cant_get_near():
    goal = Vec(1000, 0, Z)
    _, nav = felt_body(goal)
    start = Vec(1400, 0, Z)
    real, looked = nav.blocked_key, []

    def counted(key):
        looked.append(key)
        return real(key)

    nav.blocked_key = counted
    t0 = time.perf_counter()
    first = nav.plan(start, goal, 40, Z)
    n1 = len(looked)
    # (what _walk_on does next when no way is found: forget what may have moved, and plan again)
    nav.forget(12.5 - 5.0)
    nav.plan(start, goal, 40, Z)
    n2 = len(looked) - n1
    seconds = time.perf_counter() - t0
    assert first is None, "no way to within 40 of a body's middle"
    assert n1 <= 5000 and n2 <= 5000, f"cells looked up: {n1} then {n2}"
    assert seconds < 0.5, f"{seconds:.2f} s on the tick thread"


def test_walk_to_a_body_it_cant_get_near_never_holds_a_tick():
    goal = Vec(400, 0, Z)
    world = navsim.World(walls=[(goal.x - 25, goal.y - 25, goal.x + 25, goal.y + 25)])
    worst = [0.0]

    def timed(gen):
        while True:
            t0 = time.perf_counter()
            try:
                next(gen)
            except StopIteration as s:
                return s.value
            finally:
                worst[0] = max(worst[0], time.perf_counter() - t0)
            assert worst[0] < 0.5, f"one tick of the walk held the thread {worst[0]:.2f} s"
            yield

    res, _ = world.run(timed(walk_to(world.game(), goal, within=40, timeout=6)), limit=8)
    assert res is not None and not res.ok, res  # (it can't get within 40 of the body's middle)
    assert worst[0] < 0.5


def test_walled_in_check_never_changes_the_way(monkeypatch):
    rnd = random.Random(7)
    cases = []
    for _ in range(300):
        nav = NavMap()
        k = nav._k(Z)
        goal = Vec(rnd.uniform(200, 800), rnd.uniform(-300, 300), Z)
        for _ in range(rnd.randint(0, 150)):
            nav.hits[(rnd.randint(-5, 40), rnd.randint(-15, 15), k)] = 1.0
        if rnd.random() < 0.4:  # (a closed ring round the goal, some cells wide)
            gi, gj, _ = nav.key(goal)
            r = rnd.randint(1, 4)
            for d in range(-r, r + 1):
                for cell in ((gi + d, gj - r), (gi + d, gj + r), (gi - r, gj + d), (gi + r, gj + d)):
                    nav.hits[cell + (k,)] = 1.0
        cases.append((nav, Vec(0, 0, Z), goal, rnd.choice([40, 100, 200])))
    walled = sum(bool(nav.walled_in(s, g, w, Z)) for nav, s, g, w in cases)
    way = lambda w: None if w is None else [(p.x, p.y, p.z) for p in w]
    checked = [way(nav.plan(s, g, w, Z, margin=300)) for nav, s, g, w in cases]
    monkeypatch.setattr(NavMap, "walled_in", lambda self, *a: False)
    searched = [way(nav.plan(s, g, w, Z, margin=300)) for nav, s, g, w in cases]
    assert checked == searched
    assert walled >= 30, walled  # (the check had walled-in goals to decide, not only open ones)
