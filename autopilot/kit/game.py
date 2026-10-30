"""The game, as harness code sees it: reads of any object (privileged), and the player's inputs (keys, mouse, Enhanced
Input actions, UI clicks). Everything goes over the plugin's bridge (bridge.Link, docs/protocol.md). How things are
found and read depends on the game's engine (autopilot.engines: Unreal, Unity, Godot); positions are in the kit's frame
(Unreal's) whatever the engine.

Reads are synchronous; `game.batch([...])` sends several at once so they resolve in one game frame. Inputs are
fire-and-forget. Held keys are leased: the plugin lets go of a key that isn't renewed, so a crashed harness never
leaves the player running forever (the runtime renews holds every tick, see renew()).
"""

import concurrent.futures as futures
import re
import threading
import time

from .geom import Vec, aim_error

GAMEPLAY = "/Script/Engine.Default__GameplayStatics"
KISMET_SYSTEM = "/Script/Engine.Default__KismetSystemLibrary"
KEY_LEASE = 0.6  # seconds a held key stays down without renewal
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
        self.log = log or (lambda kind, **fields: None)  # the runtime's trace
        self._pool = futures.ThreadPoolExecutor(max_workers=16, thread_name_prefix="game")
        self._ctx, self._ctx_at = None, 0.0
        self._classes = {}
        self._held = {}  # key -> value held
        self._lock = threading.Lock()
        self.look = {"x": None, "y": None}  # degrees of view per mouse count (calibrate_look); y signed: + looks up
        self.turn_asked = 0.0  # degrees of yaw the mouse was moved to turn the view, all told
        self.keys = dict(self.engine.keys)
        self.writes = []  # state the harness changed directly (reported: only for verification setups)
        # The level the run plays when it isn't the one open in the editor (autopilot/levels.py): every restart
        # starts it over. level_info: how it was chosen (a levels.Level, for the agent's briefing)
        self.level = None
        self.level_info = None
        self._waited = threading.local()  # reads this thread waited on, one by one (see waits())

    # -- low level -------------------------------------------------------------------------------------

    def request(self, op, args=None, timeout=None):
        from ..bridge import BridgeError

        self._waited.n = getattr(self._waited, "n", 0) + 1
        try:
            return self.link.request(op, args or {}, timeout=timeout)
        except BridgeError as e:
            raise GameError(f"{op}: {e.error}") from None

    def post(self, op, args=None):
        self.link.post(op, args or {})

    def waits(self):
        """How many times this thread has waited on the game so far: each read waits for an answer (a game frame), a
        batch counts once. The runtime measures an executor step's reads by it."""
        return getattr(self._waited, "n", 0)

    @property
    def t(self):
        """The game time (seconds) of the latest answer from the game."""
        return self.link.game_time or 0.0

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

    def bounds(self, actor):
        """(center, half extent) of the actor's box."""
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

    def screen(self):
        """{key: text} of every text on screen."""
        return (self.request("screen") or {}).get("texts", {})

    def ui(self):
        """The UI elements on screen that can be clicked or typed into."""
        return (self.request("ui") or {}).get("elements", [])

    def ui_act(self, **args):
        return self.request("ui_act", args)

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
        """Hold a key (or Enhanced Input action) down until release(); renewed by the runtime every tick."""
        with self._lock:
            new = key not in self._held or self._held[key] != value
            self._held[key] = value
        if key.startswith("/"):
            self.post("input", {"action": key, "value": _axis(value), "mode": "hold"})
        else:
            args = {"key": key, "mode": "hold", "lease": KEY_LEASE}
            if value is not None:
                args["value"] = value
            self.post("key", args)
        if new:
            self.log("input", key=key, mode="hold", value=value)

    @property
    def features(self):
        """What the game's plugin does beyond the protocol's first version (its hello's "features")."""
        return set((getattr(self.link, "hello", None) or {}).get("features") or [])

    def hold_timed(self, key, seconds, value=None):
        """Hold a key for `seconds` of game time, let go by the game itself (exact, however late the brain is), when
        the plugin can (False: it can't, or the key is an input action; then hold() and release() it)."""
        if key.startswith("/") or "timed_hold" not in self.features:
            return False
        args = {"key": key, "mode": "hold", "seconds": float(seconds), "lease": KEY_LEASE}
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
        """Renew every held key's lease (the runtime calls this every tick)."""
        for key, value in self.held().items():
            if not key.startswith("/"):
                args = {"key": key, "mode": "hold", "lease": KEY_LEASE}
                if value is not None:
                    args["value"] = value
                self.post("key", args)

    def mouse(self, right=0.0, up=0.0):
        """Flick the mouse by counts (the game's sensitivity applies). Prefer turn()/look_at in kit.control."""
        if abs(right) >= 0.5:
            self.post("key", {"key": "MouseX", "mode": "press", "value": round(right, 1)})
            self.turn_asked += abs(right * (self.look["x"] or 0.0))  # degrees of yaw (the runtime checks they come)
        if abs(up) >= 0.5:
            self.post("key", {"key": "MouseY", "mode": "press", "value": round(up, 1)})

    def turn(self, right_deg=0.0, up_deg=0.0):
        """Turn the view by about these degrees with the mouse (needs calibrate_look(); open loop)."""
        self.calibrate_look()
        rx, ry = self.look["x"], self.look["y"]
        self.mouse(right_deg / rx if rx else 0.0, up_deg / ry if ry else 0.0)

    def calibrate_look(self, counts=60.0):
        """Measure how many degrees one mouse count turns the view (once; kept on the game)."""
        if self.look["x"] and self.look["y"]:
            return self.look
        # (not every tick while the mouse does nothing, e.g. during an intro before the controls are active)
        if time.monotonic() - getattr(self, "_calibrated_at", -1e9) < 0.5:
            return self.look
        self._calibrated_at = time.monotonic()
        for axis in ("x", "y"):
            if self.look[axis]:
                continue
            y0, p0 = self.view_rotation()
            self.mouse(counts if axis == "x" else 0.0, counts if axis == "y" else 0.0)
            time.sleep(0.15)
            y1, p1 = self.view_rotation()
            from .geom import wrap

            moved = wrap(y1 - y0) if axis == "x" else (p1 - p0)
            if abs(moved) > 0.05:
                self.look[axis] = moved / counts
                # turn back
                self.mouse(-counts if axis == "x" else 0.0, -counts if axis == "y" else 0.0)
                time.sleep(0.1)
            else:
                # pitch may sit at a limit: try the other way
                self.mouse(0.0, -counts if axis == "y" else 0.0)
                time.sleep(0.15)
                y2, p2 = self.view_rotation()
                moved = (p2 - p1) if axis == "y" else 0.0
                if abs(moved) > 0.05:
                    self.look[axis] = -moved / counts
                    self.mouse(0.0, counts)
                    time.sleep(0.1)
        self.log("calibrated", look=self.look)
        return self.look

    # -- the session -----------------------------------------------------------------------------------

    def pause(self, paused=True):
        return self.request("pause", {"paused": paused})

    def restart(self, map=None, wait_for_pawn=20.0):
        """Start the level over (or open `map`, e.g. the gameplay level of a game that boots into a menu) and wait up
        to wait_for_pawn seconds for the player's pawn. Returns whether a pawn is there (False: a menu, or loading).
        Without a map, the run's level (self.level) starts over when it has one."""
        self.release_all()
        args = {"action": "restart"}
        map = map or self.level
        if map:
            args["map"] = map
        self.request("session", args, timeout=60)
        self._ctx = None
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
