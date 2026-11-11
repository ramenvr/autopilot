"""The game, as harness code sees it: reads of any object (privileged), and the player's inputs (keys, mouse, Enhanced
Input actions, UI clicks). Everything goes over the plugin's bridge (bridge.Link, docs/protocol.md). How things are
found and read depends on the game's engine (autopilot.engines: Unreal, Unity, Godot, Roblox); positions are in the
kit's frame (Unreal's) whatever the engine.

Reads are synchronous; `game.batch([...])` sends several at once so they resolve in one game frame. Inputs are
fire-and-forget. Held keys are leased: the plugin lets go of a key that isn't renewed, so a crashed harness never
leaves the player running forever (renewed about three times a lease: from the runtime's ticks, and from a thread of
the game's between them while an episode or kit.control.run drives, see renew_due() and keeping_held()).

The game's pace is measured as it is played: each read's round trip (game.round_trip, round_trip_p90) and the frame
rate the plugin reports (game.fps). A slow game (a low frame rate, a slow link) gets longer key leases and waits as
long as it needs when it calibrates the mouse.
"""

import collections
import concurrent.futures as futures
import contextlib
import re
import statistics
import threading
import time

from .geom import Vec, aim_error

GAMEPLAY = "/Script/Engine.Default__GameplayStatics"
KISMET_SYSTEM = "/Script/Engine.Default__KismetSystemLibrary"
KEY_LEASE = 0.6  # seconds a held key stays down without renewal, at the least (key_lease: longer on a slow game)
# ... and at the most: a key of a brain that is gone (crashed, disconnected) goes up within this. Every plugin clamps a
# lease to 0.2-10 s
KEY_LEASE_MAX = 5.0
# Seconds a held key is renewed (inside Game.keeping_held) for code that has neither read the game nor sent it an input
# meanwhile: a harness hung on something (a loop that never yields) doesn't leave the player running
KEY_ALONE = 10.0
PACE_SAMPLES = 60  # the latest reads' round trips kept (game.round_trip)
# Requests whose round trip says nothing of the game's pace (they take as long as what they do: start a session, a
# picture, a timed move); posts aren't timed at all (nothing waits for them)
UNTIMED_OPS = frozenset(("session", "pause", "capture", "record", "levels", "detectors", "move", "watch", "touch"))
# A game slower than this (fewer frames a second, or longer reads) is a slow game: the agent is told so
SLOW_FPS = 25.0
SLOW_READ = 0.1
_renewing = threading.local()  # (set in the thread that renews held keys: its posts aren't the code's doing)
# Function calls that change the game rather than read it: reported as direct changes (see Game.call)
CHANGES_STATE = re.compile(r"^(K2_)?(Destroy|Set|Spawn|Apply|Add|Remove|Teleport|Launch|Kill|Damage|Give|Grant|"
                           r"Equip|Unequip|Use|Fire|Open|Close|Start|Stop|Reset|Respawn|Possess|Attach|Detach)", re.I)
PLAYER_CALLS = set()  # (none: a harness acts through inputs)


class GameError(RuntimeError):
    """The game refused a request (a missing object, a wrong function or argument)."""


class Game:
    def __init__(self, link, codex=None, log=None, engine=None):
        from ..engines import for_link

        self.link = link
        # The game's engine (autopilot.engines, from the plugin's hello): how things are found and read
        self.engine = engine or for_link(link)
        self.codex = codex  # autopilot.codex.Codex or None: resolves class names
        self.log = log or (lambda *kind, **fields: None)  # the runtime's trace (runtime.Trace)
        # (32: an executor step's batch and observe()'s, each with batches inside, run at once; a batch's reads wait in
        # the pool's threads for the reads inside them)
        self._pool = futures.ThreadPoolExecutor(max_workers=32, thread_name_prefix="game")
        self._ctx, self._ctx_at = None, 0.0
        self._classes = {}
        self._held = {}  # key -> value held
        self._lock = threading.Lock()
        self._trips = collections.deque(maxlen=PACE_SAMPLES)  # seconds each of the latest reads took to answer
        # The thread that renews held keys between the runtime's ticks (None while nothing is held), when each key's
        # hold was last sent, and when the code last read the game or sent it an input
        self._renewer, self._renewer_lock = None, threading.Lock()
        self._sent = {}  # key -> when its hold was last sent (renew_due())
        self._acted_at = time.monotonic()
        self._keepers = 0  # how many drivers (an episode, a run()) have the thread keep keys held (keeping_held)
        self.look = {"x": None, "y": None}  # degrees of view per mouse count (calibrate_look); y signed: + looks up
        self.turn_asked = 0.0  # degrees of yaw the mouse was moved to turn the view, all told
        self.keys = dict(self.engine.keys)
        self.writes = []  # state the harness changed directly (reported: only for verification setups)
        self.restarts = 0  # times the level started over (Session.started_over tells the agent)
        # The level the run plays when it isn't the one open in the editor (autopilot/levels.py): every restart
        # starts it over. level_info: how it was chosen (a levels.Level, for the agent's briefing)
        self.level = None
        self.level_info = None
        self._waited = threading.local()  # reads this thread waited on, one by one (see waits())
        self._bodies = {}  # n -> Body: the other players of a multiplayer game (player())
        self.index = 1  # which player this is (1: the run's own; a Body is another)
        self._primary = self  # (player 1's game, which a Body shares)

    # -- low level -------------------------------------------------------------------------------------

    def request(self, op, args=None, timeout=None):
        from ..bridge import BridgeError

        self._waited.n = getattr(self._waited, "n", 0) + 1
        began = self._primary._acted_at = time.monotonic()
        try:
            out = self.link.request(op, args or {}, timeout=timeout)
        except BridgeError as e:
            self._timed(op, began)  # (a refusal is an answer too: it came back as fast as any)
            raise GameError(f"{op}: {e.error}") from None
        self._timed(op, began)
        return out

    def _timed(self, op, began):
        if op not in UNTIMED_OPS:
            self._trips.append(time.monotonic() - began)

    def post(self, op, args=None):
        if not getattr(_renewing, "on", False):
            self._primary._acted_at = time.monotonic()
        self.link.post(op, args or {})

    def waits(self):
        """How many times this thread has waited on the game so far: each read waits for an answer (a game frame), a
        batch counts once. The runtime measures an executor step's reads by it."""
        return getattr(self._waited, "n", 0)

    @property
    def t(self):
        """The game time (seconds) of the latest answer from the game."""
        return self.link.game_time or 0.0

    # -- the game's pace -----------------------------------------------------------------------------------

    @property
    def round_trip(self):
        """Seconds a read takes to come back, the median of the latest ones (None before any): the time from asking
        to the answer, a frame of the game or more (a slow frame rate, a slow link)."""
        trips = sorted(self._trips)
        return trips[len(trips) // 2] if trips else None

    @property
    def round_trip_p90(self):
        """Seconds within which 9 of 10 of the latest reads came back (None before any)."""
        trips = sorted(self._trips)
        return trips[min(len(trips) - 1, int(0.9 * len(trips)))] if trips else None

    @property
    def fps(self):
        """The game's frame rate: the median of the latest the plugin reported (a heartbeat a second while the game
        plays), None when none has been."""
        reports = list(getattr(self.link, "fps_reports", None) or [])
        return round(statistics.median(reports), 1) if reports else None

    def pace(self):
        """{"fps", "round_trip_ms", "round_trip_p90_ms"} of the game now (None for what isn't known)."""
        ms = lambda s: None if s is None else round(1000 * s)
        return {"fps": self.fps, "round_trip_ms": ms(self.round_trip), "round_trip_p90_ms": ms(self.round_trip_p90)}

    def slow(self):
        """Whether the game is slow: fewer than SLOW_FPS frames a second, or reads that take over SLOW_READ s."""
        fps, trip = self.fps, self.round_trip
        return (fps is not None and fps < SLOW_FPS) or (trip is not None and trip > SLOW_READ)

    def measure_pace(self, reads=5, wait=1.5):
        """Measure the game's pace (while it runs): a few reads one after another for the round trip, and up to `wait`
        seconds for the plugin's first frame rate when none has come yet (a plugin that reports none isn't waited
        for). -> pace()"""
        for _ in range(reads):
            try:
                self.view_rotation()
            except GameError:
                break
        if getattr(self.link, "fps_reports", None) is not None:
            end = time.monotonic() + wait
            while self.fps is None and time.monotonic() < end and not getattr(self.link, "closed", None):
                time.sleep(0.1)
        return self.pace()

    def _frame(self):
        """About a frame of the game, in seconds (its frame rate's; else its reads' time, a frame or more)."""
        fps = self.fps
        return 1.0 / fps if fps else (self.round_trip or 0.0)

    def batch(self, thunks):
        """Run several reads at once (they resolve in the same game frame): a list of zero-argument callables, e.g.
        [lambda: game.loc(a), lambda: game.get(b, "Health")]. Returns their results in order; a read that failed
        gives its GameError instead of raising."""
        futs = [self._pool.submit(f) for f in thunks]
        if futs:
            self._waited.n = getattr(self._waited, "n", 0) + 1  # (one wait here, however many reads)
        out = []
        for f in futs:
            try:
                out.append(f.result())
            except GameError as e:
                out.append(e)
        return out

    def map(self, fn, items):
        """fn(item) for each item at once (see batch)."""
        return self.batch([lambda i=i: fn(i) for i in items])

    # -- objects ---------------------------------------------------------------------------------------

    def context(self, fresh=False):
        """{"world", "character", "controller", "input_subsystem", "camera_manager", "game_time", ...}; paths are ""
        when absent (no pawn while dead). Cached for half a second."""
        if fresh or self._ctx is None or time.monotonic() - self._ctx_at > 0.5:
            self._ctx, self._ctx_at = self.request("context"), time.monotonic()
        return self._ctx

    @property
    def world(self):
        return self.context()["world"]

    @property
    def pawn(self):
        return self.context()["character"]

    @property
    def controller(self):
        return self.context()["controller"]

    def call(self, obj, function, **params):
        """Call a Blueprint-callable function on an object; returns {"ReturnValue": ..., <out params>}. Static
        library functions are called on their class default object (e.g. GAMEPLAY)."""
        body = {"objectPath": obj, "functionName": function, "parameters": params}
        if CHANGES_STATE.match(function) and function not in PLAYER_CALLS:
            # Not a player's action: reported as a direct change to the game, like set()
            self.writes.append({"t": self.t, "object": obj, "name": self.engine.name_of(obj), "call": function,
                                "parameters": params})
            self.log("write", object=obj, call=function, parameters=params)
        return self.request("rc", {"verb": "call", "body": body}) or {}

    def ret(self, obj, function, **params):
        """call(), just its ReturnValue."""
        return self.call(obj, function, **params).get("ReturnValue")

    def get(self, obj, prop):
        """One property's value (any property, protected and Blueprint variables too)."""
        r = self.request("rc", {"verb": "property", "body": {"objectPath": obj, "propertyName": prop}}) or {}
        if prop in r:
            return r[prop]
        return next(iter(r.values()), None) if len(r) == 1 else r

    def props(self, obj):
        """Every property of an object, as a dict."""
        return self.request("rc", {"verb": "property", "body": {"objectPath": obj}}) or {}

    def describe(self, obj):
        """{"Name", "Class", "Properties": [...], "Functions": [...]} of an object."""
        return self.request("rc", {"verb": "describe", "body": {"objectPath": obj}})

    def set(self, obj, prop, value, why=""):
        """Write a property. Not a player's action: only for setting up a verification (e.g. removing what only gets
        in the way), always reported with its reason."""
        self.request("rc", {"verb": "property", "body": {
            "objectPath": obj, "propertyName": prop, "propertyValue": {prop: value}, "access": "WRITE_ACCESS"}})
        self.writes.append({"t": self.t, "object": obj, "name": self.engine.name_of(obj), "property": prop,
                            "value": value, "why": why})
        self.log("write", object=obj, property=prop, value=value, why=why)

    def resolve_class(self, name):
        """A class from a name, as the engine takes it. Unreal: a path as is; a Blueprint's name (BP_Door) from the
        codex; a C++ class name (Door, ADoor) in the project's modules or the engine. Unity, Godot: the type name."""
        return self.engine.resolve_class(self, name)

    def actors(self, cls):
        """Paths of every actor of a class (subclasses included) in the level now. cls: see resolve_class."""
        return self.engine.actors(self, cls)

    def all_actors(self):
        return self.engine.all_actors(self)

    def named(self, part):
        """Paths of the actors whose object name contains `part` (case-insensitive), e.g. "Tree" -> BP_Tree_C_3."""
        return self.engine.named(self, part)

    def exists(self, obj):
        """Whether the object is in the game now. An actor the game destroyed is gone at once, though its properties
        can still be read for a moment (Unreal: until garbage collection)."""
        return self.engine.exists(self, obj)

    def loc(self, actor):
        return self.engine.loc(self, actor)

    def rot(self, actor):
        """{"Pitch", "Yaw", "Roll"}"""
        return self.engine.rot(self, actor)

    def bounds(self, actor, visual=False):
        """(center, half extent) of the box square to the world round the actor's collision (what blocks or overlaps:
        trigger volumes too; its whole drawn box when nothing of it collides). visual=True: the box round everything
        drawn of it. A box: a big, turned, hollow or ring-shaped thing is far from solid all through it (see trace)."""
        if visual:
            return self.engine.bounds(self, actor, visual=True)
        return self.engine.bounds(self, actor)

    def velocity(self, actor):
        return self.engine.velocity(self, actor)

    def locs(self, actors):
        """{path: Vec} for many actors at once (gone ones left out)."""
        return self.engine.locs(self, actors)

    def name_of(self, path):
        """An object's name from its path, as the engine names it."""
        return self.engine.name_of(path)

    def to_kit(self, v):
        """A native vector the game answered (game.get of a position or velocity) in the kit's frame, as a Vec.
        Unreal: as it is."""
        return self.engine.to_kit(v)

    def to_native(self, v):
        """A kit-frame vector as the engine's own value (for game.set / game.call). Unreal: as it is."""
        return self.engine.to_native(v)

    # -- the player ------------------------------------------------------------------------------------

    def me(self):
        """The player's pawn location (feet are lower by its half height)."""
        return self.engine.me(self)

    def view_rotation(self):
        """(yaw, pitch) of the player's view (the controller's rotation), pitch in -90..90."""
        return self.engine.view_rotation(self)

    def eye(self):
        """The camera's location."""
        return self.engine.eye(self)

    def pose(self):
        """{"at", "eye", "yaw", "pitch", "velocity"} of the player, read in one frame."""
        return self.engine.pose(self)

    def aim_error(self, target, pose=None):
        """(right, up) degrees from the view to target."""
        p = pose or self.pose()
        return aim_error(p["eye"], p["yaw"], p["pitch"], Vec.of(target))

    def view(self, **args):
        """What the player sees (the plugin's view op: visible things, held things, camera, body; sonar=True adds
        how far the body could walk each way)."""
        return self.request("view", args)

    def sees(self, actors):
        """Which of these actors the player can see now (in view and in line of sight), cheaply:
        {path: {"distance", "screen_x", "screen_y", "crosshair_on", "partly_in_view"?}} (the unseen left out).
        screen_x/screen_y: -1..1 from the view's center (right and up positive); crosshair_on: the crosshair is on it
        (always there, False when the plugin didn't say)."""
        by_name = {self.name_of(a): a for a in actors}
        if not by_name:
            return {}
        v = self.request("view", {"only": list(by_name), "max_count": len(by_name)}) or {}
        out = {}
        for t in v.get("visible", []):
            a = by_name.get(t.get("name"))
            if a:
                out[a] = {k: t.get(k) for k in ("distance", "screen_x", "screen_y", "partly_in_view")
                          if t.get(k) is not None}
                out[a]["crosshair_on"] = bool(t.get("crosshair_on"))
        return out

    def trace(self, start, end, radius=0.0, sight=False):
        """The first thing a line from `start` to `end` meets (a ball of `radius` swept along it, when radius > 0: room
        for a body that wide), by the game's collision: what is solid, whatever its box says. Asked of what stops the
        player's body (its own collision; sight=True: what stops a line of sight instead). The player's own body and
        what it carries are never hit. -> None when nothing is in the way, else {"location" (where the line stops: the
        ball's center when it touches), "point" (where it touches), "normal", "distance" (from start to location),
        "actor" (its path), "name", "component"?, "inside" (start was already in it: distance 0)}. Positions in the
        kit's frame."""
        if "trace" not in self.features:
            raise GameError("trace: this game's Autopilot plugin can't trace yet (update the plugin)")
        args = {"start": Vec.of(start).ue(), "end": Vec.of(end).ue(), "radius": max(0.0, float(radius or 0.0))}
        if sight:
            args["channel"] = "sight"
        hit = (self.request("trace", args) or {}).get("hit")
        if not hit:
            return None
        out = dict(hit)
        for k in ("location", "point", "normal"):
            if out.get(k) is not None:
                out[k] = Vec.of(out[k])
        out["inside"] = bool(out.get("inside"))
        return out

    def screen(self):
        """{key: text} of every text on screen."""
        return (self.request("screen") or {}).get("texts", {})

    def ui(self):
        """The UI elements on screen that can be clicked or typed into."""
        return (self.request("ui") or {}).get("elements", [])

    def ui_act(self, **args):
        out = self.request("ui_act", args)
        self.log("input", key="ui", mode=args.get("action"), value=args)
        return out

    def pointer(self, screen_x=None, screen_y=None, world=None, object=None):
        """Put the mouse at a place on the game's view without moving it across (no mouse movement on the way): a
        screen place (-1..1 from the middle, y up: the numbers view() gives a thing), a point in the kit's frame
        (`world`) or an object (`object`: where the camera shows it). The mouse buttons pressed after it click there,
        in the world as on the UI; hold one, point again in steps, release: a drag.
        -> {"screen_x", "screen_y", "x", "y", "on_screen"} (and a `note` when the game can't do all of that)."""
        if world is not None:
            args = {"world": Vec.of(world).ue()}
        elif object is not None:
            args = {"object": object}
        else:
            args = {"screen_x": float(screen_x or 0.0), "screen_y": float(screen_y or 0.0)}
        out = self.request("pointer", args)
        self.log("input", key="pointer", mode="point", value=args)
        return out

    # -- inputs (a player's hands) ---------------------------------------------------------------------

    def press(self, key, value=None):
        """A quick press of a key (an FKey name: W, SpaceBar, E, LeftMouseButton, One, LeftShift...) or of an
        Enhanced Input action (a path /Game/.../IA_Jump.IA_Jump), or one move of an axis key by `value`."""
        if key.startswith("/"):
            self.post("input", {"action": key, "value": _axis(value), "mode": "press"})
        else:
            args = {"key": key, "mode": "press"}
            if value is not None:
                args["value"] = value
            self.post("key", args)
        self.log("input", key=key, mode="press", value=value)

    def hold(self, key, value=None):
        """Hold a key (or Enhanced Input action) down until release(). A new or changed hold is sent at once; holding it
        again, as a controller does every step, sends nothing until a renewal is due (see renew_due())."""
        lease = self.key_lease
        with self._lock:
            new = key not in self._held or self._held[key] != value
            self._held[key] = value
            if new or time.monotonic() - self._sent.get(key, -1e9) >= lease / 3:
                self._send_hold(key, value, lease)
        if new:
            self.log("input", key=key, mode="hold", value=value)
        self._primary._keep_renewing()

    def _send_hold(self, key, value, lease):
        """(with self._lock held)"""
        self._sent[key] = time.monotonic()
        if key.startswith("/"):
            self.post("input", {"action": key, "value": _axis(value), "mode": "hold"})
        else:
            args = {"key": key, "mode": "hold", "lease": lease}
            if value is not None:
                args["value"] = value
            self.post("key", args)

    @property
    def key_lease(self):
        """Seconds a held key stays down without renewal: KEY_LEASE, longer on a slow game (4 of its slow reads, or 4
        of its frames: a renewal comes a read or a frame late there), never over KEY_LEASE_MAX."""
        frame = 1.0 / self.fps if self.fps else 0.0
        return round(min(KEY_LEASE_MAX, max(KEY_LEASE, 4 * (self.round_trip_p90 or 0.0), 4 * frame)), 2)

    @property
    def features(self):
        """What the game's plugin does beyond the protocol's first version (its hello's "features")."""
        return set((getattr(self.link, "hello", None) or {}).get("features") or [])

    def hold_timed(self, key, seconds, value=None):
        """Hold a key for `seconds` of game time, let go by the game itself (exact, however late the brain is), when
        the plugin can (False: it can't, or the key is an input action; then hold() and release() it)."""
        if key.startswith("/") or "timed_hold" not in self.features:
            return False
        args = {"key": key, "mode": "hold", "seconds": float(seconds), "lease": self.key_lease}
        if value is not None:
            args["value"] = value
        self.post("key", args)
        self.log("input", key=key, mode="hold", value=value, seconds=seconds)
        return True

    def release(self, key):
        with self._lock:
            was = self._held.pop(key, "none")
        if was == "none":
            return
        if key.startswith("/"):
            self.post("input", {"action": key, "mode": "release"})
        else:
            self.post("key", {"key": key, "mode": "release"})
        self.log("input", key=key, mode="release")

    def release_all(self):
        """Let go of every key held, the other players' too."""
        self._release_own()
        for body in list(self._bodies.values()):
            body._release_own()

    def _release_own(self):
        with self._lock:
            held = list(self._held)
            self._held.clear()
        for key in held:
            if key.startswith("/"):
                self.post("input", {"action": key, "mode": "release"})
        self.post("key", {"mode": "release_all"})
        if held:
            self.log("input", key="*", mode="release_all")

    def held(self):
        with self._lock:
            return dict(self._held)

    def renew(self):
        """Renew every held key's lease now, the other players' too."""
        self._renew_own(due=False)
        for body in list(self._bodies.values()):
            body._renew_own(due=False)

    def renew_due(self):
        """Renew the held keys whose hold was sent a third of a lease ago or more, the other players' too: a renewal is
        needed only before the lease runs out, and each costs the game a request (Roblox's are counted against a
        limit). The runtime calls it every tick (and kit.control.run), and a thread of the game's between ticks (see
        keeping_held): however fast the ticks, a key is renewed about three times a lease."""
        self._renew_own(due=True)
        for body in list(self._bodies.values()):
            body._renew_own(due=True)

    def _renew_own(self, due=False):
        lease = self.key_lease
        now = time.monotonic()
        # (sent while the keys can't change: a renewal another thread sends after a release would press the key again)
        with self._lock:
            for key, value in self._held.items():
                if not key.startswith("/") and (not due or now - self._sent.get(key, -1e9) >= lease / 3):
                    self._send_hold(key, value, lease)

    def _holding(self):
        return bool(self._held) or any(body._held for body in list(self._bodies.values()))

    @contextlib.contextmanager
    def keeping_held(self):
        """Inside the block a thread of the game's renews the held keys between ticks (renew_due): the runtime's episode
        and kit.control.run drive inside one, so a step that takes longer than a lease (a slow game) doesn't let go of
        them. Outside, a key nobody renews is let go when its lease runs out (a probe's bare hold())."""
        g = self._primary
        with g._renewer_lock:
            g._keepers += 1
        try:
            g._keep_renewing()
            yield
        finally:
            with g._renewer_lock:
                g._keepers -= 1

    def _keep_renewing(self):
        """Renew the held keys from a thread of their own while any is held inside keeping_held(): between the ticks
        (a step that takes longer than a lease on a slow game would let go of them)."""
        with self._renewer_lock:
            if self._renewer is None and self._keepers > 0 and self._holding():
                self._renewer = threading.Thread(target=self._renew_between, name="renew-keys", daemon=True)
                self._renewer.start()

    def _renew_between(self):
        _renewing.on = True
        while True:
            lease = self.key_lease
            time.sleep(max(0.05, lease / 4))
            with self._renewer_lock:
                if not self._holding() or self._keepers <= 0 or getattr(self.link, "closed", None):
                    self._renewer = None
                    return
            # (the code has done nothing for KEY_ALONE: hung on something, its keys are let go)
            if time.monotonic() - self._acted_at > KEY_ALONE:
                continue
            try:
                self.renew_due()
            except Exception:  # (the game gone: the keys go up with it)
                pass

    def mouse(self, right=0.0, up=0.0):
        """Flick the mouse by counts (the game's sensitivity applies). Prefer turn()/look_at in kit.control."""
        if abs(right) >= 0.5:
            self.post("key", {"key": "MouseX", "mode": "press", "value": round(right, 1)})
            self.turn_asked += abs(right * (self.look["x"] or 0.0))  # degrees of yaw (the runtime checks they come)
            self.log("input", key="MouseX", mode="press", value=round(right, 1))
        if abs(up) >= 0.5:
            self.post("key", {"key": "MouseY", "mode": "press", "value": round(up, 1)})
            self.log("input", key="MouseY", mode="press", value=round(up, 1))

    def turn(self, right_deg=0.0, up_deg=0.0):
        """Turn the view by about these degrees with the mouse (needs calibrate_look(); open loop)."""
        self.calibrate_look()
        rx, ry = self.look["x"], self.look["y"]
        self.mouse(right_deg / rx if rx else 0.0, up_deg / ry if ry else 0.0)

    def calibrate_look(self, counts=60.0):
        """Measure how many degrees one mouse count turns the view (once; kept on the game). Each turn is read until the
        view has come to rest, for as long as this game's pace needs (a slow frame rate, a slow link, a camera that
        turns only a few frames after the move: Roblox's waits for the right button it presses to lock the mouse), and
        a result is kept only when a second turn agrees with it."""
        if self.look["x"] and self.look["y"]:
            return self.look
        # (not every tick while the mouse does nothing, e.g. during an intro before the controls are active)
        if time.monotonic() - getattr(self, "_calibrated_at", -1e9) < getattr(self, "_calibrate_every", 0.5):
            return self.look
        try:
            for axis in ("x", "y"):
                if not self.look[axis]:
                    self.look[axis] = self._measure_turn(axis, counts)
        finally:
            self._calibrated_at = time.monotonic()
            self._calibrate_every = max(0.5, self._turn_wait())
        self.log("calibrated", look=self.look)
        return self.look

    def _turn_wait(self):
        """The longest a turn is waited for to show and come to rest: 0.4 s on a fast game, more on a slow one (its
        reads and frames), at most 4 s."""
        frame = 1.0 / self.fps if self.fps else 0.0
        return min(4.0, max(0.4, 0.25 + 6 * (self.round_trip_p90 or 0.0) + 4 * frame))

    def _turned(self, axis, counts, start, limit):
        """Move the mouse by `counts` on one axis ("x": right, "y": up) and read the view until it has come to rest (two
        reads a frame or more apart that agree, once it has moved) or `limit` seconds pass: (degrees it turned, the
        view (yaw, pitch) then). 0 degrees when it didn't turn."""
        from .geom import wrap

        self.mouse(counts if axis == "x" else 0.0, counts if axis == "y" else 0.0)
        end = time.monotonic() + limit
        gap = min(0.25, max(0.03, self._frame()))  # (a frame apart: a turn the game spreads over frames shows on each)
        prev, view = None, start
        while True:
            time.sleep(gap)
            view = self.view_rotation()
            moved = wrap(view[0] - start[0]) if axis == "x" else view[1] - start[1]
            if prev is not None and abs(moved) > 0.05 and abs(moved - prev) <= 0.02 + 0.01 * abs(moved):
                return moved, view
            prev = moved
            if time.monotonic() >= end:
                return (moved if abs(moved) > 0.05 else 0.0), view

    def _measure_turn(self, axis, counts):
        """Degrees one count turns the view on an axis (y signed: + looks up), or None when it can't be told now. A turn
        and its turn back must agree; when they don't (the pitch stopped at the camera's limit one way, or part of a
        turn landed late), the way that turned more is checked again. The view ends about where it began."""
        view = self.view_rotation()
        limit = self._turn_wait()  # (after that read: a measure of the pace too)
        agree = lambda a, b: a * b > 0 and abs(a - b) <= 0.25 * max(abs(a), abs(b))
        first, view = self._turned(axis, counts, view, limit)
        if not first:
            if axis == "x":
                return None  # (the mouse doesn't turn the view now: controls not active yet)
            # The pitch may sit at a limit: the other way, twice (a late first turn would show in the first as less)
            second, view = self._turned(axis, -counts, view, limit)
            if not second:
                return None
            check, view = self._turned(axis, -counts, view, limit)
            self._turned(axis, 2 * counts, view, limit)
            return (-second - check) / 2 / counts if agree(second, check) else None
        back, view = self._turned(axis, -counts, view, limit)
        if agree(first, -back):
            return (first - back) / 2 / counts
        # Disagreeing: the way that turned more again (the other may have stopped at the camera's limit)
        sign = 1.0 if abs(first) >= abs(back) else -1.0
        big = first if sign > 0 else -back
        check, view = self._turned(axis, sign * counts, view, limit)
        self._turned(axis, -sign * counts, view, limit)
        return (big + sign * check) / 2 / counts if agree(big, sign * check) else None

    # -- the session -----------------------------------------------------------------------------------

    def pause(self, paused=True):
        return self.request("pause", {"paused": paused})

    def restart(self, map=None, wait_for_pawn=20.0, players=None):
        """Start the level over (or open `map`, e.g. the gameplay level of a game that boots into a menu) and wait up
        to wait_for_pawn seconds for the player's pawn. Returns whether a pawn is there (False: a menu, or loading).
        Without a map, the run's level (self.level) starts over when it has one. players: how many players the game
        starts with, for an engine whose plugin plays multiplayer (its hello's features say "players"); the others are
        game.player(2), game.player(3)... Roblox starts that many; Unreal plays the number its editor's Play settings
        start and refuses another."""
        if players is not None and int(players) != 1:
            self._need_players(f"restart(players={int(players)})")
        self.release_all()
        args = {"action": "restart"}
        map = map or self.level
        if map:
            args["map"] = map
        if players is not None:
            args["players"] = int(players)
        # (Unity's is a Stop and a Play; its plugin gives up after 90 s. Each further player is another client to start)
        self.request("session", args, timeout=120 + 40 * max(0, int(players or 1) - 1))
        self.restarts += 1
        self._ctx = None
        for body in self._bodies.values():
            body._ctx = None
        deadline = time.monotonic() + wait_for_pawn
        while time.monotonic() < deadline:
            try:
                if self.context(fresh=True).get("character"):
                    return True
            except GameError:
                pass
            time.sleep(0.25)
        return False

    def sleep(self, seconds):
        """Wait (wall time; the game keeps running)."""
        time.sleep(seconds)

    # -- the other players ---------------------------------------------------------------------------------

    def player(self, n):
        """Player n of a multiplayer game, as a game of its own (a Body): its pawn, view, screen, keys, UI and every kit
        function (walk_to(game.player(2), ...)) are that player's; reads of the world are the same. 1 is the run's own
        player: this game. A game the plugin plays with one player only refuses n > 1 (GameError) rather than play
        player 1 for it. How many there are: game.players()."""
        n = int(n)
        if n <= 1:
            return self._primary
        self._need_players(f"player({n})")
        if n not in self._bodies:
            self._bodies[n] = Body(self._primary, n)
        return self._bodies[n]

    def players(self):
        """The game's players: [{"player": n, "name", "path", "character", "you"}], 1 the run's own."""
        self._need_players("players()")
        return list(self.context(fresh=True).get("players") or [])

    def _need_players(self, what):
        """Several players need a plugin that plays them (its hello's features say "players"): without one a request's
        "player" would go unread, and player 1 would be played for player 2."""
        if "players" not in self.features:
            engine = getattr(self.engine, "title", "") or getattr(self.engine, "name", "") or "engine"
            raise GameError(f"game.{what}: this game's {engine} plugin plays one player only (its features don't "
                            "include 'players'): play game itself, player 1")


class Body(Game):
    """Another player of a multiplayer game (Game.player): the same game, its requests sent as player n's (their
    "player"), its keys held and its camera calibrated apart."""

    def __init__(self, game, n):
        self.__dict__.update(game.__dict__)  # (the link, engine, codex, log, pool, writes and bodies are shared)
        self.index = n
        self._ctx, self._ctx_at = None, 0.0
        self._held = {}
        self._sent = {}
        self._lock = threading.Lock()
        self.look = {"x": None, "y": None}
        self.turn_asked = 0.0

    def request(self, op, args=None, timeout=None):
        return super().request(op, {**(args or {}), "player": self.index}, timeout=timeout)

    def post(self, op, args=None):
        super().post(op, {**(args or {}), "player": self.index})

    def renew(self):
        self._renew_own()

    def renew_due(self):
        self._renew_own(due=True)

    def release_all(self):
        self._release_own()


def name_of(path):
    """An object's name from its path: /Game/Map.Map:PersistentLevel.BP_Tree_C_3 -> BP_Tree_C_3."""
    return path.rsplit(".", 1)[-1].rsplit(":", 1)[-1]


def _axis(value):
    if value is None:
        return {"X": 1.0, "Y": 0.0, "Z": 0.0}
    if isinstance(value, (int, float)):
        return {"X": float(value), "Y": 0.0, "Z": 0.0}
    if isinstance(value, Vec):
        return value.ue()
    return value
