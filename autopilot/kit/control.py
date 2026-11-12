"""Controllers: the low-level skills harness executors are made of, each a generator that does one tick of control per
step and yields, so the runtime can interrupt it between ticks (`yield from walk_to(game, door)`). Each returns a
Result (truthy when it worked) with why it stopped.

They act only through the player's inputs (mouse flicks, held movement keys, presses) and read the game to close the
loop, as a player's eyes do.
"""

import math
import time
from dataclasses import dataclass, field

from .game import GameError
from .geom import Vec, bearing, wrap

TURN_GAIN = 0.8  # share of the aim error corrected per tick (the rest next tick: no overshoot)
MAX_TURN_PER_TICK = 60.0  # degrees
# A loop that steers by what it reads gets at least this many corrections before its timeout counts: on a machine that
# runs the game slowly every read takes a frame of it, so a step can take a tenth of a second or more
MIN_CORRECTIONS = 6


@dataclass
class Result:
    ok: bool
    why: str = ""
    data: dict = field(default_factory=dict)

    def __bool__(self):
        return self.ok


def point_of(game, target):
    """A world point from a target: a Vec/dict/sequence, an actor path (its box's center), or a callable giving one."""
    if callable(target):
        target = target()
    if isinstance(target, str):
        center, _ = game.bounds(target)
        return center if center is not None else game.loc(target)
    return Vec.of(target)


class _Pace:
    """The time between a controller's steps: the brain's tick, which takes longer the slower the game's frame rate
    (each read waits for a frame). What a controller does now lasts until its next step, so it acts that far ahead."""

    def __init__(self):
        self.last, self.dt = None, 0.1

    def step(self):
        now = time.monotonic()
        if self.last is not None:
            self.dt = min(1.0, max(0.02, now - self.last))
        self.last = now
        return self.dt


def _lead(pose, goal, dt):
    """How much nearer goal (across the floor) the player gets before the next step: its speed toward it times dt."""
    v = pose.get("velocity") or Vec()
    to = goal - pose["at"]
    n = math.hypot(to.x, to.y)
    if n < 1e-6:
        return 0.0
    return max(0.0, (v.x * to.x + v.y * to.y) / n) * dt


def _flick_toward(game, right_deg, up_deg):
    look = game.calibrate_look()
    rx, ry = look.get("x"), look.get("y")
    r = max(-MAX_TURN_PER_TICK, min(MAX_TURN_PER_TICK, right_deg * TURN_GAIN))
    u = max(-MAX_TURN_PER_TICK, min(MAX_TURN_PER_TICK, up_deg * TURN_GAIN))
    game.mouse(r / rx if rx else 0.0, u / ry if ry else 0.0)
    return (r if rx else 0.0), (u if ry else 0.0)


def wait(game, seconds):
    """Do nothing for `seconds` of game time (keys held stay held). While the game's clock stands still (a menu
    that pauses the game, a loading screen) the seconds pass as real time."""
    last_t = game.t
    changed = credited = checked = time.monotonic()
    waited = 0.0
    end = changed + seconds * 3 + 1
    while waited < seconds and time.monotonic() < end:
        yield
        now, t = time.monotonic(), game.t
        if t <= last_t and now - changed > 0.4 and now - checked > 0.4:
            try:  # (no fresh answer for a while: ask, so a clock that runs isn't taken for a stopped one)
                game.context(fresh=True)
            except GameError:
                pass
            checked, t = now, game.t
        if t > last_t:
            waited += t - last_t
            last_t, changed, credited = t, now, now
        elif now - changed > 0.4:  # the clock stands still though the game answers: real time passes instead
            waited += now - max(credited, changed)
            credited = now
    return Result(True, "waited")


def until(game, condition, timeout=5.0):
    """Wait until condition() is true (checked every tick) or timeout seconds pass."""
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if condition():
            return Result(True, "condition met")
        yield
    return Result(False, "timed out")


def fit_tolerance(game, target, eye=None):
    """How near the view must come to an actor's box center for the crosshair to be on it: a quarter of the angle its
    box spans from the eye (between 0.4 and 2.5 degrees). A small thing a few metres off spans 2-3 degrees, so
    "within 2.5" stopped on its edge. For a point (not an actor): 2.5."""
    if not isinstance(target, str):
        return 2.5
    try:
        center, ext = game.bounds(target)
        eye = eye or game.pose()["eye"]
    except GameError:
        return 2.5
    if center is None or ext is None:
        return 2.5
    size = sorted((abs(ext.x), abs(ext.y), abs(ext.z)))[1]  # (the middle one: a flat or a long thing still shows it)
    spans = 2 * math.degrees(math.atan2(size, max(1.0, center.dist(eye))))
    return max(0.4, min(2.5, spans / 4))


def look_at(game, target, tolerance=None, timeout=1.5, level=False):
    """Turn the view until it points at target (see point_of) within `tolerance` degrees; by default (None) what puts
    the crosshair well inside an actor's box (fit_tolerance), 2.5 for a point. level=True looks at the horizon in
    target's direction (for walking)."""
    t0 = time.monotonic()
    steps = 0
    if tolerance is None:
        tolerance = 2.5 if level else fit_tolerance(game, target)
    # After a turn is sent the view is judged only once it has answered and come to rest: the input may not have
    # reached the game yet, and a game may spread a turn over several frames (mouse smoothing, a slow frame rate), so
    # a read taken in between shows a view still on its way and "on target" there doesn't last
    sent = None  # (yaw, pitch) when the last turn was sent, and its size in degrees
    waits = 0
    # How much the aim error shrinks per degree the view turns, per axis: 1 for a camera at the eyes; less for a
    # third-person camera, which swings round the player as it pitches so a thing below moves down the view with it
    # (corrections of the error alone then creep up on it). Measured from each turn, so the next one turns enough
    k = [1.0, 1.0]
    before = None  # (yaw, pitch, right, up, sent up) as the last turn was sent
    stalled = 0
    while True:
        try:
            p = game.pose()
            point = point_of(game, target)
        except GameError as e:
            return Result(False, f"can't read: {e}")
        view = (p["yaw"], p["pitch"])
        if sent is not None and waits < 6:
            (yaw0, pitch0), size = sent
            # (most of it shown: judge now; a game that answers at once loses no time to waiting)
            if abs(wrap(view[0] - yaw0)) + abs(view[1] - pitch0) < 0.9 * size:
                waits += 1
                yield
                continue
        sent, waits = None, 0
        right, up = game.aim_error(point, p)
        if level:
            up = -p["pitch"]
        if abs(right) <= tolerance and abs(up) <= tolerance:
            return Result(True, "on target", {"error": [round(right, 1), round(up, 1)]})
        below = p["eye"].z - point.z
        if not level and below > 0 and point.dist2d(p["eye"]) < 0.15 * below and point.dist2d(p["at"]) < 80:
            # (straight down from the camera, standing on it: no turn puts the crosshair there)
            return Result(False, "it is right below the player: step back from it (1-2 m) to aim at it",
                          {"error": [round(right, 1), round(up, 1)], "distance": round(point.dist2d(p["at"]))})
        if before is not None:
            turned = (wrap(view[0] - before[0]), view[1] - before[1])
            for i, (e0, e1) in enumerate(((before[2], right), (before[3], up))):
                if abs(turned[i]) >= 1.0:
                    k[i] = max(0.25, min(2.0, (e0 - e1) / turned[i]))
            # the view no longer pitches the way it was told: the camera's pitch limit
            stalled = stalled + 1 if abs(before[4]) >= 1.0 and abs(turned[1]) < 0.15 * abs(before[4]) else 0
            if stalled >= 2 and abs(up) > tolerance:
                return Result(False, f"the view stops pitching {'down' if up < 0 else 'up'} at {view[1]:.0f} degrees "
                              f"(the camera's limit), {abs(up):.1f} short of the target: "
                              + ("it is too close below the camera: stand further from it" if up < 0 else
                                 "it is too high above: stand further from it"),
                              {"error": [round(right, 1), round(up, 1)], "pitch": round(view[1], 1)})
        waited = time.monotonic() - t0
        if (waited > timeout and steps >= MIN_CORRECTIONS) or waited > 4 * timeout:
            if not game.look.get("x"):
                return Result(False, "the mouse doesn't turn the view (yet?): no effect measured; controls not active "
                              "(an intro, a menu, a cutscene)?", {"error": [round(right, 1), round(up, 1)]})
            return Result(False, "turn timed out", {"error": [round(right, 1), round(up, 1)]})
        r, u = _flick_toward(game, right / k[0], up / k[1])
        before = (view[0], view[1], right, up, u)
        if abs(r) + abs(u) >= 0.5:
            sent = (view, abs(r) + abs(u))
        steps += 1
        yield


def track(game, target, seconds, tolerance=1.0):
    """Keep the view on a (moving) target for `seconds` (e.g. while firing)."""
    t0 = time.monotonic()
    while time.monotonic() - t0 < seconds:
        try:
            p = game.pose()
            right, up = game.aim_error(point_of(game, target), p)
        except GameError as e:
            return Result(False, f"lost target: {e}")
        if abs(right) > tolerance or abs(up) > tolerance:
            _flick_toward(game, right, up)
        yield
    return Result(True, "tracked")


def hold_for(game, key, seconds, value=None):
    """Hold a key (or Enhanced Input action) for `seconds` of game time, then let go. When the plugin can, the game
    itself lets go after exactly that long, so the hold lasts the same on any machine whatever the brain's delay."""
    if game.hold_timed(key, seconds, value):
        finished = False
        try:
            yield from wait(game, seconds)
            finished = True
        finally:
            if not finished:  # (stopped early: let go now)
                game.post("key", {"key": key, "mode": "release"})
        return Result(True, "held")
    game.hold(key, value)
    try:
        yield from wait(game, seconds)
    finally:
        game.release(key)
    return Result(True, "held")


def press(game, key, times=1, every=0.15, value=None):
    """Press a key `times` times, `every` seconds apart."""
    for i in range(times):
        game.press(key, value)
        if i < times - 1:
            yield from wait(game, every)
    yield
    return Result(True, "pressed")


def sonar(game, turns=(0, -30, 30, -60, 60, -90, 90, 180), reach=1500):
    """How far the body could walk each way: [{"turn", "clear", "by": wall|drop|steep|reach, ...}] (turn: degrees
    right of the view). [] if the plugin can't tell."""
    try:
        v = game.view(max_count=1, sonar=True, sonar_turns=list(turns), sonar_reach=reach)
    except GameError:
        return []
    return ((v or {}).get("body") or {}).get("sonar", {}).get("ways", []) or []


def deep_drop(way, max_drop):
    """Whether a sonar way ends at a drop deeper than max_drop (a drop too deep to be told counts as deep)."""
    return way.get("by") == "drop" and (way.get("drop") is None or way.get("drop") > max_drop)


def _goal_height(game, target):
    """How high a goal is, to be set against the player's own location (its middle): an actor's location (a prop's
    is at its base or middle, a character's at its middle: on the player's floor either is level with the player's
    middle or below it), a point's own height. (Not its box: a box can reach far below the thing, a trigger zone's.)"""
    return game.loc(target).z if isinstance(target, str) else point_of(game, target).z


def _ways_up_seen(game):
    """The ways up (ramps, stairs) the player sees now, as the plugin's view finds them: [(foot, top)]; None when the
    plugin can't tell ways up."""
    try:
        v = game.view(ways_up=True, max_count=1)
    except GameError:
        return None
    body = (v or {}).get("body") or {}
    if "ways_up" not in body:
        return None
    out = []
    for w in body.get("ways_up") or []:
        try:
            out.append((Vec.of(w["foot"]), Vec.of(w["top"])))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def find_way_up(game, goal, floor, look_around=True):
    """A way up (a ramp, stairs) the player sees whose top reaches `floor` (the height of the floor the goal stands
    on), the one that makes the walk to `goal` shortest: (foot, top), or None. With none in view it looks round, a
    quarter turn at a time. None at once when the plugin can't tell ways up."""
    best = None

    def consider(ways):
        nonlocal best
        at = game.pose()["at"]
        for foot, top in ways:
            if top.z < floor - 70:  # (it doesn't get up there)
                continue
            # (one that ends short of the floor costs as much as a long way round: a step part way up loses to the
            # ramp that goes all the way)
            cost = foot.dist2d(at) + top.dist2d(goal) + 10.0 * max(0.0, floor - top.z)
            if best is None or cost < best[0]:
                best = (cost, foot, top)

    ways = _ways_up_seen(game)
    if ways is None:
        return None
    consider(ways)
    far = game.pose()["at"].dist2d(goal)
    # A way up whose top is near the goal is the one (another raised place's ramp, in view first, isn't): look round
    # until one is seen, then take the best of all seen; none that brings it nearer, none at all
    near = lambda: best is not None and best[2].dist2d(goal) <= max(700.0, 0.75 * far)
    turns = 0
    while not near() and look_around and turns < 3:
        game.turn(90, 0)
        turns += 1
        yield from wait(game, 0.3)
        consider(_ways_up_seen(game) or [])
    if best is None or best[2].dist2d(goal) >= far:
        return None
    return best[1], best[2]


def walk_to(game, target, within=120.0, timeout=20.0, avoid_drops=True, jump_when_stuck=True, run_key=None,
            look_level=True, max_drop=300.0, climb=True):
    """Walk to target (see point_of; re-read every tick, so it follows a moving thing) until within `within` units
    across the floor. Turns with the mouse and holds the forward key; steers round walls it feels (sonar); jumps and
    sidesteps when stuck; won't walk off a drop deeper than max_drop (a ledge lower than that is stepped off) unless
    avoid_drops=False. run_key: a key held while walking (sprint).
    A goal standing higher than the player steps up (on a platform, an upper floor) is reached by a way up the player
    sees (a ramp, stairs: find_way_up) whose top is near it: to its foot, up it, then on. With none in view it walks
    straight, and if that fails, looks round for one and goes that way. climb=False: straight only."""
    t0 = time.monotonic()
    left = lambda: max(2.0, timeout - (time.monotonic() - t0))
    kw = dict(avoid_drops=avoid_drops, jump_when_stuck=jump_when_stuck, run_key=run_key, look_level=look_level,
              max_drop=max_drop)
    high, floor = False, None
    if climb:
        try:
            m = movement(game)
            at = game.pose()["at"]
            goal_z = _goal_height(game, target)
            # higher than a step up from where the player stands (with room for a prop's middle), a floor above
            high = goal_z - at.z > float(m.get("step") or 45.0) + 40.0
            floor = goal_z - 40.0  # (what a way up's top must reach: about the goal's floor)
        except (GameError, TypeError, ValueError):
            high = False
    climbed = False
    if high:  # (a way up in view toward the goal; looking round for one only if the straight way fails)
        climbed = yield from _climb(game, target, floor, left, kw, look_around=False)
    r = yield from _walk_on(game, target, within, left(), settle=True, **kw)
    if not r and high and not climbed and time.monotonic() - t0 < timeout:
        # (the straight way didn't get there: a way up, then again)
        if (yield from _climb(game, target, floor, left, kw, look_around=True)):
            r = yield from _walk_on(game, target, within, left(), settle=True, **kw)
    return r


def _climb(game, target, floor, left, kw, look_around):
    """Up a way up toward a goal on a higher floor: True when it got to the way's top."""
    try:
        goal = point_of(game, target)
    except GameError:
        return False
    if not look_around:  # (facing the goal, as the walk will: the ways up near it come into view)
        yield from look_at(game, goal, tolerance=10.0, timeout=0.8, level=True)
    way = yield from find_way_up(game, goal, floor, look_around=look_around)
    if not way:
        return False
    foot, top = way
    at = game.pose()["at"]
    # (already on it: on from where it is, not back down to its foot)
    if not (at.z > foot.z + 30 and at.dist2d(top) < foot.dist2d(top)):
        r = yield from _walk_on(game, foot, 80.0, left(), **kw)
        if not r:
            return False
    up = dict(kw, avoid_drops=False)  # (a ramp's side is a drop, though the way up it is straight)
    r = yield from _walk_on(game, top, 80.0, left(), **up)
    return bool(r)


def _coast(game):
    """Seconds' worth of its speed the body slides on after forward is let go (the game's braking, and the frames
    before the release reaches it): measured at each stop (_settle), at first a guess."""
    return getattr(game, "coast_s", None) or 0.12


def _settle(game, speed, start, timeout=1.0):
    """After letting go: wait until the body has stopped, and learn how far it slid for its speed (game.coast_s)."""
    t0 = time.monotonic()
    p = None
    while time.monotonic() - t0 < timeout:
        yield
        try:
            p = game.pose()
        except GameError:
            return None
        if p["velocity"].flat.length < 20:
            break
    if p is not None and speed > 150:
        slid = p["at"].dist2d(start) / speed
        game.coast_s = max(0.0, min(0.6, slid if getattr(game, "coast_s", None) is None
                                    else 0.5 * game.coast_s + 0.5 * slid))
    return p


SONAR_EVERY = 0.2  # seconds between sonar looks while walking
SONAR_RING = 12  # ways all round in a look (the rest of its 16 go where the player is heading)
WAYPOINT_REACHED = 45.0  # cm from a corner of the way at which the walk heads on to the next
NO_TURN_GIVE_UP = 2.0  # s of turning toward the way without the view moving at all before the walk gives up
REPLAN_AHEAD = 600.0  # cm of the planned way checked against what the sonar newly felt (farther on: once nearer)
REVERSE_AHEAD = 200.0  # cm: turning back again (within REVERSE_HOLD) once the old way is blocked this near
REVERSE_HOLD = 3.0  # s after turning back during which turning back again waits for a close block
TURN_BACK = 100.0  # degrees between the old way's heading and the new one's that count as turning back


def _turns_back(at, old, new):
    """Whether the new way heads off more than TURN_BACK degrees from the old one (from `at`, to their next corners)."""
    a, b = old[0] - at, new[0] - at
    if a.flat.length < 1.0 or b.flat.length < 1.0:
        return False
    return abs(wrap(math.degrees(math.atan2(b.y, b.x) - math.atan2(a.y, a.x)))) > TURN_BACK


def _look_turns(heading, flip):
    """Sonar turns for a look while walking: a ring all round (turned half a step every other look, so two looks
    feel twice as many ways) and close ones either side of where the player is heading."""
    step = 360.0 / SONAR_RING
    ring = {round(wrap(i * step + (step / 2 if flip else 0.0))) for i in range(SONAR_RING)}
    near = {round(wrap(heading + k)) for k in (0, -10, 10, -20, 20)}
    return sorted(ring | near)[:16]


def _walk_on(game, target, within=120.0, timeout=20.0, avoid_drops=True, jump_when_stuck=True, run_key=None,
             look_level=True, max_drop=300.0, settle=False):
    """walk_to's walk: the shortest way the map of the level leaves open (kit.nav: felt out with sonar as it walks,
    kept on the game), planned again whenever what it feels shows that way blocked. settle: return once the body has
    stopped (where it ends up is where the next thing is done from, e.g. aiming at what it walked to)."""
    from .nav import nav_of

    fwd = game.keys["forward"]
    nav = nav_of(game)
    t0 = time.monotonic()
    last_progress_at, best_left = t0, None
    since_progress = 0
    stuck_tries = 0
    short = 0
    sonar_at, flip = -1e9, False
    path, planned_version, planned_goal = None, -1, None
    replans = 0
    ways, ways_yaw = [], 0.0
    blind = False  # (no way on the map: straight at the target)
    turning_since = None  # (time, view yaw) since which it has been turning on the spot
    turn_sent = None  # (view yaw then, degrees of turn on the way, steps it has been on the way) of the last turn
    turned_back_at = -1e9  # when the way last turned back on itself
    pace = _Pace()
    try:
        while True:
            try:
                p = game.pose()
                goal = point_of(game, target)
            except GameError as e:
                return Result(False, f"can't read: {e}")
            at = p["at"]
            d = goal.dist2d(at)
            # It slides on after letting go, and keeps walking until the step it lets go on (the next one: half a step
            # on average when it settles, a whole one when it doesn't): let go that much early, so it stops about
            # `within` away however long a step takes, and not on top of the target
            step = pace.step()
            if d - _lead(p, goal, _coast(game) + (0.5 * step if settle else step)) <= within:
                if not settle:
                    return Result(True, "arrived", {"distance": round(d), "replans": replans})
                game.release(fwd)
                if run_key:
                    game.release(run_key)
                end = yield from _settle(game, p["velocity"].flat.length, at)
                try:
                    d = point_of(game, target).dist2d(end["at"]) if end else d
                except GameError:
                    pass
                # (stopped well short, the slide guessed long: on again, a little)
                if d > within + max(30.0, 0.25 * within) and short < 2 and time.monotonic() - t0 < timeout:
                    short += 1
                    continue
                return Result(True, "arrived", {"distance": round(d), "replans": replans})
            now = time.monotonic()
            if now - t0 > timeout:
                return Result(False, "walk timed out", {"distance": round(d), "replans": replans,
                                                         "way": [q.rounded() for q in path or []]})
            feet = at.z
            nav.linger(at, step, now, feet=feet)  # (where it goes round and round costs more to plan through)
            # Feel the way and put it on the map
            if now - sonar_at >= SONAR_EVERY:
                sonar_at, flip = now, not flip
                heading = bearing(at, p["yaw"], path[0] if path else goal)
                ways, ways_yaw = sonar(game, turns=_look_turns(heading, flip), reach=min(1500, d + 200)), p["yaw"]
                if ways:
                    nav.observe(at, p["yaw"], ways, now, avoid_drops=avoid_drops, max_drop=max_drop, feet=feet)
            # The way: straight there while the map shows nothing in between, else planned round what's in the way
            moved_goal = planned_goal is None or goal.dist2d(planned_goal) > max(100.0, 0.15 * d)
            if path is None or moved_goal or (nav.version != planned_version
                                              and nav.way_blocked(at, path, feet, ahead=REPLAN_AHEAD)):
                planned_version, planned_goal = nav.version, goal
                old = path
                if nav.line_free(at, goal, feet):
                    path = [goal]
                else:
                    path = nav.plan(at, goal, within, feet)
                    if path is None:
                        # Nothing open on the map: forget what may have moved and the guessed faces, and try again
                        nav.forget(now - 5.0)
                        planned_version = nav.version
                        path = nav.plan(at, goal, within, feet)
                    replans += 1
                # Turning back again soon after turning back is taken only once the block is close: each look adds
                # marks round the player and the cheaper side flips, and turning back at every flip, it dithers
                if path is not None and old and not moved_goal and _turns_back(at, old, path):
                    if now - turned_back_at < REVERSE_HOLD and not nav.way_blocked(at, old, feet, ahead=REVERSE_AHEAD):
                        path = old
                    else:
                        turned_back_at = now
                blind = path is None
                if blind:
                    path = [goal]  # (no way on the map: straight at it, and see; it may be stepped or jumped)
                path = list(path)
            # The next corner (and on past ones reached, or ones seen straight to)
            # (reached: within a step's run of it, however long a step takes, or close and already behind the way it
            # moves: a fast walker on a slow game steps over a corner and would turn back for it)
            vel = p["velocity"].flat
            reach = max(WAYPOINT_REACHED, vel.length * step)
            while len(path) > 1 and (path[0].dist2d(at) < reach or nav.line_free(at, path[1], feet) or (
                    path[0].dist2d(at) < 2 * reach and vel.length > 100 and (path[0] - at).flat.dot(vel) < 0)):
                path.pop(0)
            aim = goal if len(path) == 1 else path[0]
            steer = bearing(at, p["yaw"], aim)
            # A turn sent that the view doesn't show yet (input reaches a slow game a frame or more later) is on its
            # way: turn only by the rest, or the same turn goes twice and the walk swings to and fro
            on_way = 0.0
            if turn_sent is not None:
                yaw0, sent_deg, ticks = turn_sent
                shown = wrap(p["yaw"] - yaw0)
                if ticks < 4 and abs(shown) < 0.9 * abs(sent_deg):
                    on_way = sent_deg - shown
            r, _ = _flick_toward(game, steer - on_way, -p["pitch"] if look_level else 0.0)
            ticks = turn_sent[2] + 1 if turn_sent is not None and on_way else 0
            turn_sent = (p["yaw"], on_way + r, ticks) if abs(on_way + r) >= 1.0 else None
            # (close to what it walks to, turn on the spot first: walking on while turning circles round it)
            walking = abs(steer) < (50 if aim.dist2d(at) > 3 * within + 150 else 20)
            # Never off the edge of a deep drop just ahead (the map steers round drops it knows)
            if walking and avoid_drops and ways:
                ahead = min(ways, key=lambda w: abs(wrap(ways_yaw + w.get("turn", 0) - p["yaw"] - steer)))
                if deep_drop(ahead, max_drop) and ahead.get("clear", 1e9) < 80:
                    if blind:
                        return Result(False, f"a drop of {ahead.get('drop', 'unknown depth')} ahead",
                                      {"distance": round(d), "replans": replans})
                    walking = False
            if walking:
                game.hold(fwd)
                if run_key:
                    game.hold(run_key)
                turning_since = None
            else:
                game.release(fwd)
                # The view won't turn (an intro, a menu, a held interaction that takes the mouse): give up now, not at
                # the timeout
                if turning_since is None or abs(wrap(p["yaw"] - turning_since[1])) > 3.0:
                    turning_since = (now, p["yaw"])
                elif now - turning_since[0] > NO_TURN_GIVE_UP:
                    return Result(False, "the view doesn't turn: the mouse has no effect now (a menu, an intro, or "
                                  "an interaction holding the mouse?)", {"distance": round(d), "replans": replans})
            # Stuck: no progress along the way for a second (and a few steps: a slow game steps seldom) while walking
            left = aim.dist2d(at) + sum(a.dist2d(b) for a, b in zip(path, path[1:]))
            if best_left is None or left < best_left - 25 or not walking:
                # (turning on the spot toward the way isn't being stuck: the clock starts once it walks)
                best_left, last_progress_at, since_progress = left, now, 0
            else:
                since_progress += 1
            if now - last_progress_at > 1.0 and since_progress >= MIN_CORRECTIONS // 2 and walking:
                stuck_tries += 1
                if stuck_tries > 5:
                    return Result(False, "stuck", {"distance": round(d), "replans": replans,
                                                   "way": [q.rounded() for q in path]})
                # Something the sonar doesn't feel (low, thin) is in the way: on the map, and a jump in case it's low
                nav.block_ahead(at, wrap(p["yaw"] + steer), now, feet=feet)
                # (never a jump at the edge of a deep drop: it would carry the body over)
                edge = avoid_drops and any(deep_drop(w, max_drop) and w.get("clear", 1e9) < 150 and abs(wrap(
                    ways_yaw + w.get("turn", 0) - p["yaw"] - steer)) < 45 for w in ways)
                if jump_when_stuck and not edge:
                    game.press(game.keys["jump"])
                    yield from wait(game, 0.3)
                last_progress_at, best_left, since_progress = time.monotonic(), None, 0
            yield
    finally:
        game.release(fwd)
        if run_key:
            game.release(run_key)


def keep_away(game, threat, distance=800.0, timeout=6.0, run_key=None):
    """Move away from a threat (see point_of) until it is `distance` away or timeout: picks the open way (sonar) that
    points most away from it, so the player doesn't back into a wall."""
    fwd = game.keys["forward"]
    t0 = time.monotonic()
    heading, heading_at = None, 0.0
    try:
        while time.monotonic() - t0 < timeout:
            try:
                p = game.pose()
                danger = point_of(game, threat)
            except GameError as e:
                return Result(False, f"can't read: {e}")
            d = danger.dist2d(p["at"])
            if d >= distance:
                return Result(True, "far enough", {"distance": round(d)})
            away = wrap(bearing(p["at"], p["yaw"], danger) + 180.0)
            now = time.monotonic()
            if heading is None or now - heading_at > 0.6:
                heading_at = now
                ways = sonar(game, turns=sorted({round(away) + k for k in (-90, -60, -30, 0, 30, 60, 90)}), reach=600)
                open_ways = [w for w in ways if w.get("clear", 0) >= 250 and not deep_drop(w, 300.0)]
                pick = min(open_ways, key=lambda w: abs(wrap(w["turn"] - away))) if open_ways else None
                heading = wrap(p["yaw"] + (pick["turn"] if pick else away))
            steer = wrap(heading - p["yaw"])
            _flick_toward(game, steer, -p["pitch"])
            if abs(steer) < 60:
                game.hold(fwd)
                if run_key:
                    game.hold(run_key)
            else:
                game.release(fwd)
            yield
        return Result(False, "still close", {"distance": round(d)})
    finally:
        game.release(fwd)
        if run_key:
            game.release(run_key)


def melee(game, target, key, reach=150.0, cooldown=0.5, alive=None, timeout=12.0, tolerance=8.0):
    """Close in on target and strike it with `key` every `cooldown` seconds while it is in reach and in view, until
    alive() is false (e.g. its health read <= 0) or timeout. alive defaults to "the actor still exists"."""
    alive = alive or (lambda: game.exists(target) if isinstance(target, str) else True)
    fwd = game.keys["forward"]
    t0 = time.monotonic()
    last_hit = -1e9
    hits = 0
    pace = _Pace()
    try:
        while time.monotonic() - t0 < timeout:
            if not alive():
                return Result(True, "target down", {"hits": hits})
            try:
                p = game.pose()
                at = point_of(game, target)
            except GameError:
                return Result(True, "target gone", {"hits": hits})
            d = at.dist2d(p["at"])
            right, up = game.aim_error(at, p)
            _flick_toward(game, right, up)
            if d - _lead(p, at, pace.step()) > reach * 0.8:
                game.hold(fwd)
            else:
                game.release(fwd)
            if d <= reach and abs(right) <= tolerance and game.t - last_hit >= cooldown:
                game.press(key)
                last_hit = game.t
                hits += 1
            yield
        return Result(False, "timed out", {"hits": hits})
    finally:
        game.release(fwd)


def aim_and_strike(game, target, key, strikes=1, cooldown=0.5, stop=None):
    """Look at target (within 8 degrees, as melee strikes) and press `key` `strikes` times, `cooldown` seconds apart:
    one volley, for tactics that choose when to be in reach (melee loops closing in and striking until the target is
    down). stop: a callable checked before each strike; true ends the volley early (the target down, a hit taken).
    data: strikes made, whether it was aimed."""
    aimed = yield from look_at(game, target, tolerance=8.0, timeout=1.0)
    made = 0
    for i in range(strikes):
        if stop is not None and stop():
            break
        game.press(key)
        made += 1
        if i < strikes - 1:
            yield from wait(game, cooldown)
        else:
            yield
    return Result(made > 0, "struck" if made else "stopped", {"strikes": made, "aimed": aimed.ok})


def _step_until(gen, stop):
    """Drive a controller a tick at a time, yielding between, until it ends (its Result) or stop() says to after a
    tick (None: it is closed, so it lets go of what it held)."""
    while True:
        try:
            next(gen)
        except StopIteration as s:
            return s.value
        if stop():
            gen.close()
            return None
        yield


def _health_of(game, target):
    """An actor's Health, best-effort: a number, or None when it can't be read as one (a point, no such property)."""
    if not isinstance(target, str):
        return None
    try:
        v = game.get(target, "Health")
    except GameError:
        return None
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def strike_and_retreat(game, target, key, reach=150.0, strikes=1, cooldown=0.5, retreat=None, recover=2.0,
                       alive=None, health=None, timeout=30.0, run_key=None):
    """Hit and run: close in on target (walk_to within `reach`, look_at it), strike with `key` `strikes` times
    `cooldown` apart, back off out of its reach (keep_away to `retreat`, default 3 x reach) and wait `recover` seconds
    before closing in again, until alive() is false (default: the actor still exists) or timeout.
    For a fight a straight melee loses: an enemy that out-trades the player in reach (hits harder, or oftener) beats
    standing and swapping blows, but after its attack it recovers for a moment: strike in that opening and be gone
    before the next. Measure `recover` (its attack's recovery: its animation's length, a timer in the code) and `reach`
    (the player's weapon) in the code, and set `retreat` past its own reach plus a step.
    health: a callable giving the player's health. With it, a drop while closing in or striking backs off at once (the
    strikes left are skipped: that was its attack landing, not an opening) and 0 ends the tactic with "player dead".
    Returns ok when alive() became false; data: strikes (made), approaches, seconds, damage (taken: with health),
    hits (strikes after which the target's Health read lower: when game.get(target, "Health") reads a number). Each
    approach is logged ("tactic": what it walked, struck, took and how far it got away)."""
    alive = alive or (lambda: game.exists(target) if isinstance(target, str) else True)
    retreat = 3.0 * reach if retreat is None else retreat
    t0 = time.monotonic()
    left = lambda: max(0.5, timeout - (time.monotonic() - t0))
    out = {"strikes": 0, "approaches": 0, "seconds": 0.0}
    hp = health() if health is not None else None
    if health is not None:
        out["damage"] = 0
    th = _health_of(game, target)
    if th is not None:
        out["hits"] = 0
    hit = False  # a hit taken this approach

    def done(ok, why):
        out["seconds"] = round(time.monotonic() - t0, 1)
        return Result(ok, why, out)

    def over():
        """The tactic's end, if it has come: the target down, the player dead, or time up."""
        if not alive():
            return done(True, "target down")
        if hp is not None and hp <= 0:
            return done(False, "player dead")
        if time.monotonic() - t0 > timeout:
            return done(False, "timeout")
        return None

    def took_hit():
        """Reads the player's health: whether it dropped since the last read (the damage summed up)."""
        nonlocal hp, hit
        if health is None:
            return False
        now = health()
        if now is None:
            return False
        if hp is not None and now < hp:
            out["damage"] += hp - now
            hit = True
        hp = now
        return hit

    def landed():
        """Reads the target's health: whether it dropped since the last read (a hit counted)."""
        nonlocal th
        now = _health_of(game, target) if th is not None else None
        if now is None:
            return False
        th, was = now, th
        if now < was:
            out["hits"] += 1
            return True
        return False

    def stop():  # (before each strike: the target down, or its attack landed: no opening after all)
        landed()
        return not alive() or took_hit()

    while True:
        r = over()
        if r is not None:
            return r
        out["approaches"] += 1
        hit = False
        entry = {"approach": out["approaches"], "health": hp, "target_health": th, "strikes": 0}
        # Close in; a hit on the way (its attack reaches further than thought, or it struck first) turns back at once
        walked = yield from _step_until(walk_to(game, target, within=0.8 * reach, timeout=left(), run_key=run_key),
                                        took_hit)
        if walked is not None and not walked:
            game.log("tactic", **entry, hit=hit, walk=walked.why)
            return done(False, f"can't close in: {walked.why}")
        if walked is not None:
            volley = yield from aim_and_strike(game, target, key, strikes, cooldown, stop=stop)
            out["strikes"] += volley.data["strikes"]
            entry["strikes"] = volley.data["strikes"]
            landed()
        r = over()
        if r is not None:
            game.log("tactic", **entry, hit=hit, retreat=None)
            return r
        away = yield from keep_away(game, target, distance=retreat, timeout=min(6.0, left()), run_key=run_key)
        took_hit()
        if alive() and not (hp is not None and hp <= 0):
            yield from wait(game, min(recover, left()))
            took_hit()
        game.log("tactic", **entry, hit=hit, retreat=away.data.get("distance"), damage=out.get("damage"))


def movement(game):
    """The player character's movement numbers, read once (Unreal: from its CharacterMovement and capsule; other
    engines: the plugin's movement op): {"walk_speed", "jump_z" (take-off speed up), "gravity" (positive, cm/s^2),
    "half_height", "peak" (how high a jump lifts the feet), "air_control"}. Missing values are None (not a
    Character)."""
    cached = getattr(game, "_movement", None)
    if cached and cached.get("pawn") == game.pawn:
        return cached
    pawn = game.pawn
    out = {"pawn": pawn, "walk_speed": None, "jump_z": None, "gravity": None, "half_height": None, "peak": None,
           "air_control": None, "step": None, "reach_up": None, "reach_up_max": None}
    out.update(game.engine.movement(game))  # (autopilot.engines: the engine's raw numbers)
    if out["jump_z"] and out["gravity"]:
        out["peak"] = out["jump_z"] ** 2 / (2 * out["gravity"])
        out["reach_up"] = out["peak"] + float(out["step"] or 0.0)
        # (the capsule floats a little over the floor and its rounded bottom perches on edges: a few cm more can work)
        out["reach_up_max"] = out["reach_up"] + 10.0
    game._movement = out
    return out


def _footprint_distance(at, center, ext):
    """How far across the floor `at` is from a box's footprint (0 inside it), and the nearest point of it."""
    nx = min(max(at.x, center.x - ext.x), center.x + ext.x)
    ny = min(max(at.y, center.y - ext.y), center.y + ext.y)
    near = Vec(nx, ny, center.z + ext.z)
    return near.dist2d(at), near


def jump_to(game, target, run_key=None, timeout=8.0, tries=3):
    """Run and jump to land on something (see _jump_once), trying again (backing off for a fresh run-up) up to `tries`
    times when an attempt never got to take off at speed."""
    r = Result(False, "not tried")
    for _ in range(max(1, tries)):
        r = yield from _jump_once(game, target, run_key, timeout)
        if r.ok or r.why not in ("jump timed out", "ran past the take-off point at too low a speed"):
            return r
    return r


def _jump_once(game, target, run_key=None, timeout=8.0):
    """Run and jump to land on something: an actor (a box, a platform, a ledge: landing on its top, taking off so the
    top of the jump comes over its near edge) or a point (the far side of a gap: landing on it). From the character's
    own jump speed, gravity and step height (movement()) it works out whether the height is reachable and where to
    take off, runs there (holding forward, and run_key if given; start far enough back to reach full speed), jumps,
    and keeps going forward in the air. Returns ok when the feet end up within 40 of the top's / point's height and
    on it; data tells how high the feet got."""
    m = movement(game)
    fwd, jump = game.keys["forward"], game.keys["jump"]
    if not (m["jump_z"] and m["gravity"]):
        return Result(False, "not a character with jump numbers (movement() found none)")
    box = None
    if isinstance(target, str):
        try:
            center, ext = game.bounds(target)
        except GameError as e:
            return Result(False, f"can't read the target: {e}")
        box = (center, ext)
    vz, g = m["jump_z"], m["gravity"]
    t_apex = vz / g
    # Room for a run-up: from too close to the edge the character can't reach its running speed before take-off, so
    # back off first along the line from the target
    if box:
        try:
            p = game.pose()
        except GameError as e:
            return Result(False, f"can't read: {e}")
        edge, near = _footprint_distance(p["at"], *box)
        run_speed = max(p["velocity"].length2d, 0.6 * float(m["walk_speed"] or 600))
        need = run_speed * t_apex + 450.0  # take-off distance + room to reach running speed
        if edge < need:
            away = (p["at"] - near).flat
            away = away.unit if away.length > 1 else (p["at"] - box[0]).flat.unit
            start = near + away * (need + 60.0)
            yield from walk_to(game, Vec(start.x, start.y, p["at"].z), within=60, timeout=6, avoid_drops=True)
    t0 = time.monotonic()
    jumped_at = None
    rise_at_takeoff = 0.0
    best_feet = -1e9
    speeds = []  # (time, speed across the floor) while running up: take off once it stops rising
    prev_still = False
    pace = _Pace()
    try:
        while time.monotonic() - t0 < timeout:
            try:
                asked = time.monotonic()
                p = game.pose()
                read = time.monotonic() - asked  # (about a frame of the game: how old what it says is)
                goal = Vec(box[0].x, box[0].y, box[0].z + box[1].z) if box else point_of(game, target)
            except GameError as e:
                return Result(False, f"can't read: {e}")
            dt = pace.step()
            feet = p["at"].z - (m["half_height"] or 90.0)
            best_feet = max(best_feet, feet)
            rise = goal.z - feet
            d = goal.dist2d(p["at"])
            speed = p["velocity"].length2d
            if jumped_at is None:
                if rise > (m["reach_up_max"] or 0):
                    return Result(False, f"too high: {rise:.0f} above the feet, a jump reaches {m['reach_up']:.0f} "
                                  f"(lifts {m['peak']:.0f}, steps up {m['step'] or 0:.0f})",
                                  {"rise": round(rise), "reach_up": round(m["reach_up"])})
                if box:
                    # over the near edge at the top of the jump: take off one apex time's run from the edge
                    edge, near = _footprint_distance(p["at"], *box)
                    aim_at = near if edge > 60 else goal
                    # (+ how far it runs before the press acts: what it reads is a frame old, and the press comes
                    # within a step, half of one on average; both longer on a machine that runs the game slowly)
                    takeoff = speed * t_apex + speed * (read + 0.5 * dt)
                    ready = edge <= takeoff
                else:
                    # land on the point: the time until the feet come back down to its height
                    disc = vz * vz - 2 * g * max(0.0, rise)
                    air = (vz + math.sqrt(disc)) / g if disc > 0 else t_apex
                    aim_at = goal
                    ready = d - speed * (read + 0.5 * dt) <= speed * air * 0.9
                b = bearing(p["at"], p["yaw"], goal if box is None else aim_at)
                _flick_toward(game, b, -p["pitch"])
                if abs(b) < 25:
                    game.hold(fwd)
                    if run_key:
                        game.hold(run_key)
                else:
                    game.release(fwd)
                now = time.monotonic()
                speeds = [(t, v) for t, v in speeds if now - t < 0.5] + [(now, speed)]
                # at its running speed: moving, and no faster than 0.3 s ago (the game's own speed, whatever
                # MaxWalkSpeed says)
                older = [v for t, v in speeds if now - t > 0.3]
                fast = speed > 150 and bool(older) and speed <= max(older) * 1.04
                if ready and abs(b) < 12 and fast:
                    game.press(jump)
                    jumped_at = time.monotonic()
                    rise_at_takeoff = rise
                elif box and not fast and edge < 0.5 * takeoff:
                    return Result(False, "ran past the take-off point at too low a speed",
                                  {"edge": round(edge), "speed": round(speed)})
            else:
                b = bearing(p["at"], p["yaw"], goal)
                # Coming down inside the top's footprint: stop pushing on, or a small top is crossed before the
                # landing is seen (a step or two of running, more on a machine that runs the game slowly)
                if box and p["velocity"].z < 0 and feet - goal.z < 60 and _footprint_distance(p["at"], *box)[0] < 1:
                    game.release(fwd)
                else:
                    _flick_toward(game, b * 0.5, -p["pitch"])
                    game.hold(fwd)
                # landed: no vertical speed on two ticks in a row (at the top of the jump it only passes through 0)
                still = abs(p["velocity"].z) < 5
                landed, prev_still = still and prev_still, still
                if time.monotonic() - jumped_at > 0.25 and landed:
                    game.release(fwd)
                    # On the top already (feet at its height, inside its footprint): done. A small top is crossed in
                    # a step or two of walking, more so on a machine that runs the game slowly, so it doesn't walk on
                    if box and abs(feet - goal.z) < 40 and _footprint_distance(p["at"], *box)[0] < 1:
                        return Result(True, "landed on it", {"feet": round(feet), "target_z": round(goal.z),
                                                             "distance": round(d), "best_feet": round(best_feet)})
                    # Perched on an edge (the capsule's rounded bottom on it) or short of the point: walk in toward
                    # the middle, then judge by where the feet are
                    if box or d > 60:
                        game.release(fwd)
                        yield from walk_to(game, goal, within=50 if box else 40, timeout=2.0, avoid_drops=True)
                        try:
                            p = game.pose()
                        except GameError as e:
                            return Result(False, f"can't read: {e}")
                        feet = p["at"].z - (m["half_height"] or 90.0)
                        d = goal.dist2d(p["at"])
                    on = abs(feet - goal.z) < 40
                    if box:
                        on = on and _footprint_distance(p["at"], *box)[0] < 1
                    else:
                        on = on and d < 200
                    why = "landed on it" if on else "landed short or off"
                    if not on and box and rise_at_takeoff > (m["reach_up"] or 0):
                        # (between reach_up and reach_up_max: made by perching on the edge, which a miss doesn't
                        # rule out: the edge is met at another moment of the jump on another try)
                        why += (f" (the top was {rise_at_takeoff:.0f} above the feet: within a jump's reach "
                                f"({m['reach_up_max']:.0f}) by perching on the edge, so a miss is not proof it can't "
                                "be done: try again)")
                    return Result(on, why, {"feet": round(feet), "target_z": round(goal.z), "distance": round(d),
                                            "best_feet": round(best_feet)})
            yield
        return Result(False, "jump timed out", {"best_feet": round(best_feet), "jumped": jumped_at is not None})
    finally:
        game.release(fwd)
        if run_key:
            game.release(run_key)


def face_and_press(game, target, key, tolerance=None):
    """Look at target, then press key (e.g. use, attack, pick up what the crosshair is on)."""
    r = yield from look_at(game, target, tolerance=tolerance)
    game.press(key)
    yield
    return Result(True, "pressed", {"aimed": r.ok})


def go_and_press(game, target, key, within=150.0, tolerance=None, timeout=20.0):
    """Walk to target, look at it and press key: the common "use X" macro."""
    r = yield from walk_to(game, target, within=within, timeout=timeout)
    if not r:
        return r
    return (yield from face_and_press(game, target, key, tolerance))


def run(gen, timeout=15.0, hz=15.0, game=None):
    """Drive a controller or executor generator to its end from plain code (setup(), teardown(), a probe), ticking at
    `hz`, for at most `timeout` seconds; returns its result. Executors `yield from` controllers instead. game: the one
    the generator acts on (default: its `game` argument)."""
    game = game or (getattr(gen, "gi_frame", None) and gen.gi_frame.f_locals.get("game"))
    t0 = time.monotonic()
    try:
        while time.monotonic() - t0 < timeout:
            if game is not None:
                game.renew()
            next(gen)
            time.sleep(1.0 / hz)
        gen.close()
        return Result(False, "run timed out")
    except StopIteration as s:
        return s.value
    finally:
        if game is not None:
            game.release_all()
