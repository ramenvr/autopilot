"""Finding the way round what blocks the body: a map felt out lazily with sonar as the player goes, and the shortest
way across it (A*) to a target. Nothing is planned up front from the level's data: the map starts empty, what isn't
felt yet counts as open, and the way is planned again whenever what the player feels shows it blocked.

The map is a grid over the floor in the player's own terms: a sonar way is the body's capsule swept out (see
control.sonar), so where it stops is where the body's middle can't go (the body's radius is already taken off), and
everything short of it is where the body's middle can be. A cell is:
- hit (blocked): a sweep stopped in it (a wall, a floor too steep, a drop too deep), unless a sweep went through its
  middle since;
- solid (blocked): behind a hit (an obstacle is a body wide at least, to the body's middle), or between two hits near
  each other on one flat face (two neighbouring ways that met the same plane), unless a sweep ever went through it;
- guessed: between two hits farther apart on one face (the face likely goes on between them, but an opening the
  sweeps missed from afar may be there: a door in a long wall), unless a sweep ever went through it. A way may cross
  a guess, at a cost (GUESS_COST): it is tried when going round is much longer, and felt out on the way;
- else open (felt or not).
Each mark keeps the floor's height it was felt at: a map holds every floor of a level (one storey's walls don't block
the next). It is kept on the Game per level (nav_of(game)), so later walks there know the layout already.
"""

import heapq
import math

from .geom import Vec

CELL = 25.0  # cm across a cell
STOREY = 250.0  # cm of height a map layer covers (marks felt this much higher or lower are another floor's)
SAME_FACE = 0.94  # cos of the largest angle between two hit faces' normals that counts them one flat face
FACE_GAP = 30.0  # cm off a face's plane a neighbouring hit may be and still be on it
MAX_FACE_SPAN = 450.0  # cm: two hits farther apart aren't joined
SOLID_SPAN = 100.0  # cm: two hits on a face nearer than this are joined solid (else guessed)
DEPTH = 60.0  # cm past where a sweep stopped that is solid too (an obstacle is at least a body wide to the
# body's middle: its own thickness and the body's radius each side)
GUESS_COST = 6.0  # cost of a step into a guessed cell, times an open one's
LINGER_CELL = 100.0  # cm across a square the walk's lingering is counted in
LINGER_SECONDS = 2.0  # seconds spent in one square that double the cost of a way through it (more: costlier still)
LINGER_MAX = 6.0  # at most this many times the cost
LINGER_HALF_LIFE = 60.0  # s: lingering counts half as much this much later
HIT_OUTLIVES = 2.0  # s: a hit this much newer than a sweep through a cell's middle blocks it again
NEAR_COST = 1.5  # extra cost of a step into a cell next to a blocked one (keeps the way off walls)
HEURISTIC_WEIGHT = 1.2  # A* leans this much toward the target (fewer cells searched; a way at most this much longer)
MAX_EXPANSIONS = 120000  # A* gives up after this many cells (a far target in a big level: no way found)


def _ij(x, y, cell=CELL):
    return int(math.floor(x / cell)), int(math.floor(y / cell))


class NavMap:
    def __init__(self, cell=None):
        self.cell = cell or CELL
        self.hits = {}  # (i, j, k) -> time a sweep stopped in it
        self.solid = {}  # (i, j, k) -> time it was found solid (behind a hit, or between near hits)
        self.guesses = {}  # (i, j, k) -> time it was guessed blocked
        self.passes = {}  # (i, j, k) -> time a sweep went through its middle
        # (i, j, k) of a LINGER_CELL square -> (seconds the walk spent in it, when last counted): a way through where
        # the walk has gone round and round costs more, so the plan tries another way (out of a dead end's inside
        # the sonar didn't show: the space under a structure, a camp of things too low or thin to feel)
        self.lingered = {}
        self.linger_t = 0.0  # (the time of the latest count)
        self.version = 0  # bumped when a cell becomes blocked or guessed (a planned way may be blocked now)

    # -- marks ---------------------------------------------------------------------------------------------

    def _k(self, z):
        return int(math.floor(z / STOREY))

    def key(self, p, z=None):
        i, j = _ij(p.x, p.y, self.cell)
        return i, j, self._k(p.z if z is None else z)

    def center(self, i, j, z=0.0):
        return Vec((i + 0.5) * self.cell, (j + 0.5) * self.cell, z)

    def blocked_key(self, key):
        h = self.hits.get(key)
        p = self.passes.get(key)
        if h is None:
            return p is None and key in self.solid
        # (a sweep through the cell's middle shows the middle open, whatever stopped a sweep at its edge; a hit a while
        # after it is something come in the way: a door closed)
        return p is None or h > p + HIT_OUTLIVES or key in self.solid

    def guessed_key(self, key):
        return key in self.guesses and key not in self.passes

    def blocked(self, p, z=None):
        return self.blocked_key(self.key(p, z))

    def _hit(self, p, z, t):
        key = self.key(p, z)
        if not self.blocked_key(key):
            self.version += 1
        self.hits[key] = t

    def _guess(self, p, z, t, solid=False):
        key = self.key(p, z)
        if key in self.passes or key in self.hits:
            return
        marks = self.solid if solid else self.guesses
        if key not in marks:
            self.version += 1
        marks[key] = t

    def _pass(self, a, direction, length, z, t):
        """A sweep went from a along direction for length: every cell whose middle it went through is open."""
        c = self.cell
        k = self._k(z)
        s = 0.0
        while s <= length:
            p = a + direction * s
            i, j = _ij(p.x, p.y, c)
            m = self.center(i, j)
            # (through the middle: within half a cell of it, so a sweep grazing a corner doesn't open it; hits are
            # marked well inside the cell past where a sweep stopped)
            off = abs((m.x - a.x) * direction.y - (m.y - a.y) * direction.x)
            if off <= c * 0.5:
                self.passes[(i, j, k)] = t
            s += c * 0.5

    def observe(self, at, yaw, ways, t, avoid_drops=True, max_drop=300.0, feet=None):
        """Marks what a sonar look from `at` (the body's middle) with the view at `yaw` felt. feet: the feet's height
        (the floor the marks belong to; default at.z)."""
        z = at.z if feet is None else feet
        stops = []
        for w in ways:
            clear = float(w.get("clear") or 0.0)
            ang = math.radians(yaw + float(w.get("turn") or 0.0))
            d = Vec(math.cos(ang), math.sin(ang), 0.0)
            by = w.get("by")
            deep = by == "drop" and (w.get("drop") is None or w.get("drop") > max_drop)
            stop = by in ("wall", "steep") or (avoid_drops and deep)
            self._pass(Vec(at.x, at.y, 0.0), d, clear - self.cell * 0.5 if stop else clear, z, t)
            if stop:
                hp = Vec(at.x, at.y, 0.0) + d * (clear + self.cell * 0.6)
                self._hit(hp, z, t)
                for s in range(1, int(DEPTH / (self.cell * 0.5)) + 1):
                    self._guess(hp + d * (s * self.cell * 0.5), z, t, solid=True)
                n = w.get("normal")
                stops.append((math.degrees(ang), hp, Vec(n.get("X", 0.0), n.get("Y", 0.0), 0.0) if n else None, by))
        # Join neighbouring hits on one flat face (or one drop's edge): the face goes on between them
        stops.sort(key=lambda s: s[0])
        for a, b in zip(stops, stops[1:] + stops[:1]):
            if a is b or (b[0] - a[0]) % 360 > 50:
                continue
            span = b[1].dist2d(a[1])
            if span > MAX_FACE_SPAN or span < self.cell:
                continue
            if a[3] == "wall" and b[3] == "wall":
                if not (a[2] and b[2]) or a[2].dot(b[2]) < SAME_FACE or abs((b[1] - a[1]).dot(a[2])) > FACE_GAP:
                    continue
            elif a[3] != b[3]:
                continue
            steps = int(span / (self.cell * 0.5))
            inward = (a[2] * -1.0) if a[2] else None
            solid = span < SOLID_SPAN
            for s in range(1, steps):
                q = a[1] + (b[1] - a[1]) * (s / steps)
                self._guess(q, z, t, solid)
                if inward:
                    self._guess(q + inward * self.cell, z, t, solid)

    def block_ahead(self, at, yaw_deg, t, feet=None, distance=None):
        """Something the sonar didn't feel stops the body here (it walked into it and got nowhere): marks the cells
        ahead of it (a body wide)."""
        d = Vec(math.cos(math.radians(yaw_deg)), math.sin(math.radians(yaw_deg)), 0.0)
        side = Vec(-d.y, d.x, 0.0)
        z = at.z if feet is None else feet
        ahead = Vec(at.x, at.y, 0.0) + d * (distance or self.cell * 1.5)
        for k in (-1, 0, 1):
            self._hit(ahead + side * (k * self.cell), z, t)

    def linger(self, at, seconds, t, feet=None):
        """Counts `seconds` the walk spent at `at` (its middle) by time t."""
        key = (int(math.floor(at.x / LINGER_CELL)), int(math.floor(at.y / LINGER_CELL)),
               self._k(at.z if feet is None else feet))
        spent, when = self.lingered.get(key, (0.0, t))
        self.lingered[key] = (spent * 0.5 ** ((t - when) / LINGER_HALF_LIFE) + seconds, t)
        self.linger_t = max(self.linger_t, t)

    def linger_cost(self, i, j, k, t):
        """How many times the cost of a step into nav cell (i, j, k) the walk's lingering there makes it (1: none)."""
        x, y = (i + 0.5) * self.cell, (j + 0.5) * self.cell
        hit = self.lingered.get((int(math.floor(x / LINGER_CELL)), int(math.floor(y / LINGER_CELL)), k))
        if not hit:
            return 1.0
        spent = hit[0] * 0.5 ** (max(0.0, t - hit[1]) / LINGER_HALF_LIFE)
        return min(LINGER_MAX, 1.0 + spent / LINGER_SECONDS)

    def forget(self, before, guesses=True):
        """Forgets hits felt before `before` (things that may have moved: doors, other characters) and the guesses."""
        self.hits = {k: t for k, t in self.hits.items() if t >= before}
        if guesses:
            self.guesses, self.solid = {}, {}
        self.version += 1

    # -- the way ---------------------------------------------------------------------------------------------

    def line_free(self, a, b, z, guesses=True):
        """Whether nothing blocks the straight way from a to b on the floor at height z (nor is guessed to, unless
        guesses=False)."""
        d = b.dist2d(a)
        if d < 1e-6:
            return True
        n = int(d / (self.cell * 0.5)) + 1
        k = self._k(z)
        start = _ij(a.x, a.y, self.cell)
        for s in range(1, n + 1):
            p = a + (b - a) * (s / n)
            i, j = _ij(p.x, p.y, self.cell)
            if (i, j) != start and (self.blocked_key((i, j, k)) or (guesses and self.guessed_key((i, j, k)))):
                return False
        return True

    def plan(self, start, goal, within, z, margin=None):
        """The shortest way from start to within `within` of goal (across the floor) on the floor at height z, as a list
        of points to walk to in turn (corners only), or None when the map shows no way. It looks for one in the box
        round start and goal, `margin` wider each way (by default wider and wider: round a long wall)."""
        if margin is not None:
            return self._plan(start, goal, within, z, margin)
        base = max(1500.0, 0.6 * goal.dist2d(start))
        for m in (base, base * 3, base * 8):
            way = self._plan(start, goal, within, z, m)
            if way is not None:
                return way
        return None

    def _plan(self, start, goal, within, z, margin):
        c = self.cell
        k = self._k(z)
        lo_i, lo_j = _ij(min(start.x, goal.x) - margin, min(start.y, goal.y) - margin, c)
        hi_i, hi_j = _ij(max(start.x, goal.x) + margin, max(start.y, goal.y) + margin, c)
        s = _ij(start.x, start.y, c)
        reach = max(within * 0.9, c * 0.75)
        blocked = {}
        lingered = bool(self.lingered)

        def is_blocked(i, j):
            v = blocked.get((i, j))
            if v is None:
                v = blocked[(i, j)] = (i, j) != s and self.blocked_key((i, j, k))
            return v

        def near(i, j):
            for di in (-1, 0, 1):
                for dj in (-1, 0, 1):
                    if (di or dj) and is_blocked(i + di, j + dj):
                        return True
            return False

        def h(i, j):
            dx = abs((i + 0.5) * c - goal.x)
            dy = abs((j + 0.5) * c - goal.y)
            return HEURISTIC_WEIGHT * max(0.0, math.hypot(dx, dy) - reach)

        def arrived(i, j):
            return math.hypot((i + 0.5) * c - goal.x, (j + 0.5) * c - goal.y) <= reach

        frontier = [(h(*s), 0.0, s)]
        came = {s: None}
        cost = {s: 0.0}
        end = None
        expansions = 0
        while frontier:
            _, g, cur = heapq.heappop(frontier)
            if g > cost.get(cur, 1e18):
                continue
            if arrived(*cur):
                end = cur
                break
            expansions += 1
            if expansions > MAX_EXPANSIONS:
                break
            ci, cj = cur
            for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
                ni, nj = ci + di, cj + dj
                if ni < lo_i or ni > hi_i or nj < lo_j or nj > hi_j or is_blocked(ni, nj):
                    continue
                if di and dj and (is_blocked(ci + di, cj) or is_blocked(ci, cj + dj)):
                    continue  # (no cutting a corner)
                step = c * (1.4142 if di and dj else 1.0)
                if self.guessed_key((ni, nj, k)):
                    step *= GUESS_COST
                elif near(ni, nj):
                    step *= NEAR_COST
                if lingered:
                    step *= self.linger_cost(ni, nj, k, self.linger_t)
                ng = g + step
                if ng < cost.get((ni, nj), 1e18):
                    cost[(ni, nj)] = ng
                    came[(ni, nj)] = cur
                    heapq.heappush(frontier, (ng + h(ni, nj), ng, (ni, nj)))
        if end is None:
            return None
        cells = []
        while end is not None:
            cells.append(end)
            end = came[end]
        cells.reverse()
        points = [self.center(i, j, z) for i, j in cells[1:]] or [Vec(goal.x, goal.y, z)]
        return self.smooth(start, points, z)

    def smooth(self, start, points, z):
        """The corners of a way: from each point, straight on to the farthest later one with nothing in between."""
        out = []
        at, i = start, 0
        while i < len(points):
            j = len(points) - 1
            while j > i and not self.line_free(at, points[j], z):
                j -= 1
            out.append(points[j])
            at, i = points[j], j + 1
        return out

    def way_blocked(self, at, path, z, ahead=None):
        """Whether the way left (from at through path's points) is blocked now (guesses aside: a way may cross them);
        ahead: only its next this many cm (a block farther on is dealt with once nearer: planning round every far
        mark as it comes in turns the walk this way and that)."""
        prev, gone = at, 0.0
        for p in path:
            seg = p.dist2d(prev)
            if ahead is not None and gone + seg > ahead:
                p = prev + (p - prev) * ((ahead - gone) / max(seg, 1e-6))
            if not self.line_free(prev, p, z, guesses=False):
                return True
            gone += seg
            if ahead is not None and gone >= ahead:
                return False
            prev = p
        return False


def nav_of(game):
    """The game's map for the level now (made on first use, kept per level on the Game)."""
    maps = getattr(game, "_navmaps", None)
    if maps is None:
        maps = game._navmaps = {}
    try:
        world = game.world
    except Exception:
        world = ""
    if world not in maps:
        maps[world] = NavMap()
    return maps[world]
