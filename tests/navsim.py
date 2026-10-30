"""A simulated level for walking tests: a flat floor with boxes on it (walls) and pits (drops), a round body that
walks at `speed` while W is held and slides along what it meets, and a sonar like the plugin's (the body swept out
each way: where its middle stops). Time is simulated: `run(gen)` steps a controller one tick at a time, so a minute
of walking takes a moment.

    world = World(walls=[(x0, y0, x1, y1), ...], pits=[...], start=(x, y), yaw=0)
    game = world.game()
    res, seconds = world.run(walk_to(game, Vec(...)), limit=60)
"""

import math
import threading
import types

from autopilot.kit import control
from autopilot.kit.game import Game
from autopilot.kit.geom import Vec

RADIUS = 35.0
HALF_HEIGHT = 90.0


class Clock:
    def __init__(self):
        self.t = 0.0

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.t += s


class World:
    def __init__(self, walls=(), pits=(), start=(0.0, 0.0), yaw=0.0, speed=500.0, tick=1 / 30, radius=RADIUS, lag=0,
                 view_locked=False):
        self.walls = [tuple(map(float, w)) for w in walls]
        self.pits = [tuple(map(float, p)) for p in pits]
        self.pos = Vec(start[0], start[1], HALF_HEIGHT)
        self.yaw = float(yaw)
        self.pitch = 0.0
        self.speed = speed
        self.tick = tick
        self.radius = radius
        self.held = {}
        self.clock = Clock()
        self.game_time = 0.0
        self.fell = False
        self.lock = threading.Lock()
        self.hello = {"project": "sim"}
        self.travelled = 0.0
        self.sonar_looks = 0
        self.lag = lag  # ticks before an input takes effect (a real game's latency)
        self.view_locked = view_locked  # the mouse doesn't turn the view (a menu open)
        self.pending = []  # [(tick it applies at, op args)]
        self.ticks = 0

    # -- geometry -------------------------------------------------------------------------------------------

    def _inside(self, x, y):
        r = self.radius
        for x0, y0, x1, y1 in self.walls:
            # (the round body against a box: the nearest point of the box within its radius)
            nx, ny = min(max(x, x0), x1), min(max(y, y0), y1)
            if (nx - x) ** 2 + (ny - y) ** 2 < r * r:
                return True
        return False

    def _in_pit(self, x, y):
        return any(x0 <= x <= x1 and y0 <= y <= y1 for x0, y0, x1, y1 in self.pits)

    def _cast(self, x, y, dx, dy, reach):
        """How far the body's middle goes from (x, y) along (dx, dy): (clear, by, normal)."""
        step = 4.0
        s = 0.0
        while s < reach:
            n = s + step
            px, py = x + dx * n, y + dy * n
            if self._inside(px, py):
                # the face's normal: which way out of the box nearest
                return s, "wall", self._normal(px, py)
            if self._in_pit(px, py):
                return s, "drop", None
            s = n
        return reach, "reach", None

    def _normal(self, px, py):
        best, nrm = 1e18, (1.0, 0.0)
        for x0, y0, x1, y1 in self.walls:
            nx, ny = min(max(px, x0), x1), min(max(py, y0), y1)
            d = (nx - px) ** 2 + (ny - py) ** 2
            if d < best:
                best = d
                vx, vy = px - nx, py - ny
                L = math.hypot(vx, vy) or 1.0
                nrm = (vx / L, vy / L)
        return {"X": round(nrm[0], 2), "Y": round(nrm[1], 2)}

    # -- time ------------------------------------------------------------------------------------------------

    def step(self):
        self.ticks += 1
        due = [a for at, a in self.pending if at <= self.ticks]
        self.pending = [(at, a) for at, a in self.pending if at > self.ticks]
        for a in due:
            self._key(a)
        dt = self.tick
        self.clock.t += dt
        self.game_time += dt
        f = Vec(math.cos(math.radians(self.yaw)), math.sin(math.radians(self.yaw)), 0)
        r = Vec(-f.y, f.x, 0)
        move = Vec()
        if "W" in self.held:
            move = move + f
        if "S" in self.held:
            move = move - f
        if "D" in self.held:
            move = move + r
        if "A" in self.held:
            move = move - r
        if move.length2d < 1e-6 or self.fell:
            return
        v = move.unit * (self.speed * dt)
        x, y = self.pos.x, self.pos.y
        # slide: the whole move, else along x or y alone
        for mx, my in ((v.x, v.y), (v.x, 0.0), (0.0, v.y)):
            if not self._inside(x + mx, y + my):
                self.pos = Vec(x + mx, y + my, self.pos.z)
                self.travelled += math.hypot(mx, my)
                break
        if self._in_pit(self.pos.x, self.pos.y):
            self.fell = True
            self.pos = Vec(self.pos.x, self.pos.y, -1000.0)

    def run(self, gen, limit=120.0):
        """Steps a controller to its end (or `limit` simulated seconds): (its Result or None, seconds taken)."""
        saved = control.time
        control.time = types.SimpleNamespace(monotonic=self.clock.monotonic, sleep=self.clock.sleep)
        t0 = self.clock.t
        try:
            while self.clock.t - t0 < limit:
                try:
                    next(gen)
                except StopIteration as s:
                    return s.value, self.clock.t - t0
                self.step()
            gen.close()
            return None, self.clock.t - t0
        finally:
            control.time = saved

    def _key(self, args):
        mode, key = args.get("mode"), args.get("key")
        if mode == "release_all":
            self.held.clear()
        elif mode == "hold":
            self.held[key] = args.get("value")
        elif mode == "release":
            self.held.pop(key, None)
        elif key == "MouseX" and not self.view_locked:
            self.yaw = (self.yaw + 0.1 * args.get("value", 0)) % 360
        elif key == "MouseY" and not self.view_locked:
            self.pitch = max(-89, min(89, self.pitch + 0.1 * args.get("value", 0)))

    # -- the bridge ------------------------------------------------------------------------------------------

    def game(self):
        g = Game(self)
        g.look = {"x": 0.1, "y": 0.1}
        return g

    def post(self, op, args=None):
        self.request(op, args)

    def request(self, op, args=None, timeout=None):
        from autopilot.bridge import BridgeError

        args = args or {}
        with self.lock:
            if op == "context":
                return {"world": "/Game/Sim.Sim", "character": "/Game/Sim.Sim:PersistentLevel.Hero_C_0",
                        "controller": "/Game/Sim.Sim:PersistentLevel.PC_0",
                        "camera_manager": "/Game/Sim.Sim:PersistentLevel.Cam_0", "game_time": self.game_time}
            if op == "key":
                if self.lag:
                    self.pending.append((self.ticks + self.lag, dict(args)))
                else:
                    self._key(args)
                return {}
            if op == "view":
                if not args.get("sonar"):
                    return {}
                self.sonar_looks += 1
                ways = []
                for turn in (args.get("sonar_turns") or [0]):
                    a = math.radians(self.yaw + turn)
                    clear, by, normal = self._cast(self.pos.x, self.pos.y, math.cos(a), math.sin(a),
                                                   float(args.get("sonar_reach", 1500)))
                    w = {"turn": turn, "clear": round(clear), "by": by}
                    if normal:
                        w["normal"] = normal
                    if by == "drop":
                        w["drop"] = 1000
                    ways.append(w)
                return {"body": {"sonar": {"ways": ways}}}
            if op == "rc":
                body = args["body"]
                fn = body.get("functionName")
                if fn == "K2_GetActorLocation":
                    return {"ReturnValue": self.pos.ue()}
                if fn == "GetControlRotation":
                    return {"ReturnValue": {"Yaw": self.yaw, "Pitch": self.pitch % 360, "Roll": 0}}
                if fn == "GetCameraLocation":
                    return {"ReturnValue": (self.pos + Vec(0, 0, 60)).ue()}
                if fn == "GetVelocity":
                    return {"ReturnValue": {"X": 0, "Y": 0, "Z": 0}}
                raise BridgeError(400, f"no function {fn}")
            return {}


# Levels for walking tests: (name, World kwargs, target, within)
def levels():
    return [
        ("open field", dict(), Vec(2000, 300, HALF_HEIGHT), 100),
        ("wide wall", dict(walls=[(500, -1500, 560, 1500)]), Vec(1100, 0, HALF_HEIGHT), 100),
        ("very wide wall", dict(walls=[(500, -3000, 560, 2600)]), Vec(1100, 0, HALF_HEIGHT), 100),
        # a pocket open away from the target: the body starts in it facing the target
        ("dead end", dict(walls=[(400, -500, 460, 500), (-200, -560, 460, -500), (-200, 500, 460, 560)]),
         Vec(1200, 0, HALF_HEIGHT), 100),
        # a room with a doorway (120 wide) on its far side; the target inside
        ("room door far side", dict(walls=[(800, -600, 860, 600), (1660, -600, 1720, 600), (800, -660, 1720, -600),
                                           (800, 600, 1220, 660), (1340, 600, 1720, 660)]),
         Vec(1260, 0, HALF_HEIGHT), 100),
        # two staggered walls (a zigzag)
        ("zigzag", dict(walls=[(400, -1200, 460, 300), (900, -300, 960, 1200)]), Vec(1500, 0, HALF_HEIGHT), 100),
        # a long wall with a 120-wide gap off to one side
        ("wall with a gap", dict(walls=[(600, -3000, 660, 640), (600, 760, 660, 3000)]),
         Vec(1200, 0, HALF_HEIGHT), 100),
        # a pit across the straight way, to walk round
        ("pit", dict(pits=[(500, -700, 900, 700)]), Vec(1300, 0, HALF_HEIGHT), 100),
        # pillars to weave through
        ("pillars", dict(walls=[(400 + 300 * i, -150 + (i % 2) * 200, 500 + 300 * i, 50 + (i % 2) * 200)
                                for i in range(5)]), Vec(2200, 0, HALF_HEIGHT), 100),
        # a doorway only 100 wide (30 to spare for a body 70 wide) in a wall across the way, facing away
        ("narrow door", dict(walls=[(600, -2000, 640, -50), (600, 50, 640, 2000)], yaw=180),
         Vec(1200, 300, HALF_HEIGHT), 100),
        # the target in a closed box: no way there (the walk should give up, not wander)
        ("closed room", dict(walls=[(800, -400, 840, 400), (1600, -400, 1640, 400), (800, -440, 1640, -400),
                                    (800, 400, 1640, 440)]), Vec(1200, 0, HALF_HEIGHT), 100),
    ]


def clutter(seed, n=14, size=(80, 700), span=2400):
    """A random level: n boxes between the start (0, 0) and a target `span` away, the start turned any way."""
    import random

    rnd = random.Random(seed)
    walls = []
    target = Vec(span, rnd.uniform(-600, 600), HALF_HEIGHT)
    while len(walls) < n:
        w, h = rnd.uniform(*size), rnd.uniform(*size)
        if rnd.random() < 0.5:
            w = rnd.uniform(40, 80)  # (thin walls, long one way)
        else:
            h = rnd.uniform(40, 80) if rnd.random() < 0.5 else h
        x, y = rnd.uniform(150, span - 150), rnd.uniform(-1200, 1200)
        box = (x - w / 2, y - h / 2, x + w / 2, y + h / 2)
        # (keep the start and the target clear)
        if any(box[0] - 120 < p.x < box[2] + 120 and box[1] - 120 < p.y < box[3] + 120 for p in (Vec(), target)):
            continue
        walls.append(box)
    return dict(walls=walls, yaw=rnd.uniform(0, 360)), target
