"""A tiny game for test_regress.py, behind a link as a plugin would answer it: no engine, no
language model, no Jev (FakeJev picks). Its game clock runs with the wall clock, as a live game's does.

The game: a hero on a line with a coin and a door ahead of it, and a coin count. `move` {"to": "Coin"} walks the hero
up to the thing, stopping a step short of it (28 cm short of the coin, 56 cm of the door), and a restart puts the
hero back at the start. Reads are rc properties, as Unreal's plugin answers them.

The harnesses import the project's `lib` (lib.arrived(distance_cm)) and `tuning` (tuning.SETTLE_TICKS) from a memory
folder the test writes, so a change to lib.py changes whether a saved regression still passes.
"""

import os
import shutil
import sys
import threading
import time
import types

from autopilot import runtime
from autopilot.kit.game import Game

LEVEL = "/Game/Tiny.Tiny:PersistentLevel."
HERO = LEVEL + "Hero_C_0"

LIB = '''def arrived(distance):
    """Whether the player stands at a target distance_cm away."""
    return distance < 10
'''
TUNING = "SETTLE_TICKS = 10  # ticks an option waits before it moves\n"

GO = '''from autopilot.kit import Harness, Option, Status
import lib
import tuning

HERO = "/Game/Tiny.Tiny:PersistentLevel.Hero_C_0"
THING = "/Game/Tiny.Tiny:PersistentLevel.__Thing__"


def go(game, obs):
    for _ in range(tuning.SETTLE_TICKS):
        yield
    game.request("move", {"to": "__Thing__"})
    yield


def idle(game, obs):
    for _ in range(3):
        yield


class Go(Harness):
    goal = "walk to the __thing__"
    tempo = 10.0
    tick_hz = 20.0
    stall_seconds = 0

    def observe(self, game):
        return {"distance": round(abs(game.get(THING, "X") - game.get(HERO, "X")))}

    def options(self, obs):
        return [Option("go_to___thing__", "walk to the __thing__", go),
                Option("look_around", "look around", idle)]

    def status(self, obs):
        if lib.arrived(obs["distance"]):
            return Status("done", "next to the __thing__", {"near___thing__": True, "distance": obs["distance"]})
        return Status()
'''

COUNT = '''from autopilot.kit import Harness, Option, Status

HERO = "/Game/Tiny.Tiny:PersistentLevel.Hero_C_0"


def idle(game, obs):
    for _ in range(2):
        yield


class Count(Harness):
    goal = "read the coin count"
    tempo = 10.0
    tick_hz = 20.0
    stall_seconds = 0

    def observe(self, game):
        return {"coins": game.get(HERO, "Coins")}

    def options(self, obs):
        return [Option("check_coins", "read the count", idle), Option("wait", "wait", idle)]

    def status(self, obs):
        if obs.get("coins") is not None:
            return Status("done", "the count read", {"coins": obs["coins"]})
        return Status()
'''

HARNESSES = {"coin": GO.replace("__thing__", "coin").replace("__Thing__", "Coin"),
             "door": GO.replace("__thing__", "door").replace("__Thing__", "Door"),
             "count": COUNT}
LIKES = {"coin": "go_to_coin", "door": "go_to_door", "count": "check_coins"}

class World:
    """The game, as the link a Game reads it through (request, post, hello, game_time). calls: every (op, args)."""

    THINGS = {"Coin": 500.0, "Door": 900.0}
    SHORT = {"Coin": 28.0, "Door": 56.0}  # how far short of each thing a move stops

    def __init__(self):
        self.hello = {"project": "Tiny"}
        self.listeners = []
        self.closed = False
        self.lock = threading.Lock()
        self.calls = []
        self.reset()

    def reset(self):
        self.t0 = time.monotonic()
        self.x = 0.0
        self.coins = 0

    @property
    def game_time(self):
        return round(time.monotonic() - self.t0, 3)

    def close(self):
        self.closed = True

    def post(self, op, args=None):
        self.request(op, args)

    def request(self, op, args=None, timeout=None):
        from autopilot.bridge import BridgeError

        args = args or {}
        with self.lock:
            self.calls.append((op, args))
            if op == "context":
                return {"world": "/Game/Tiny.Tiny", "character": HERO, "controller": LEVEL + "PC_0",
                        "game_time": self.game_time}
            if op == "session":
                if args.get("action") == "restart":
                    self.reset()
                return {"running": True}
            if op == "move":
                to = args.get("to")
                if to not in self.THINGS:
                    raise BridgeError(400, f"no {to} to move to")
                self.x = self.THINGS[to] - self.SHORT[to]
                return {"moved": True}
            if op == "rc" and args.get("verb") == "property":
                body = args["body"]
                path, prop = body.get("objectPath"), body.get("propertyName")
                name = path[len(LEVEL):] if path.startswith(LEVEL) else path
                values = {"Hero_C_0": {"X": self.x, "Coins": self.coins}}
                values.update({k: {"X": v} for k, v in self.THINGS.items()})
                if name not in values or prop not in values[name]:
                    raise BridgeError(400, f"no property {prop} on {path}")
                return {prop: values[name][prop]}
            if op in ("pause", "key", "input", "timestep", "detectors"):
                return {}
            raise BridgeError(400, f"unknown op {op}")


class FakeJev:
    """Prefers the option named `likes` when offered, else the first."""

    def __init__(self, likes):
        self.likes = likes

    def system_one(self, state, questions):
        names = list(questions["pick"].criteria)
        best = self.likes if self.likes in names else names[0]
        probs = {n: (0.8 if n == best else 0.2 / max(1, len(names) - 1)) for n in names}
        ns = types.SimpleNamespace
        return ns(choices={"pick": ns(choice=best, probabilities=probs, confidence=0.8)}, nouls={}, scores={})


def connect(world=None):
    """(a Game on the world, what closes it, the world)."""
    world = world or World()
    return Game(world), world, world


def fresh_imports(memory):
    """lib and tuning from memory, as the files are now (no stale module or bytecode)."""
    for name in ("lib", "tuning"):
        sys.modules.pop(name, None)
    shutil.rmtree(os.path.join(memory, "__pycache__"), ignore_errors=True)
    if memory not in sys.path:
        sys.path.insert(0, memory)


def write_memory(memory, lib=LIB, tuning=TUNING):
    os.makedirs(memory, exist_ok=True)
    with open(os.path.join(memory, "lib.py"), "w", encoding="utf-8") as f:
        f.write(lib)
    with open(os.path.join(memory, "tuning.py"), "w", encoding="utf-8") as f:
        f.write(tuning)


def play(run_dir, which, n=1, max_seconds=3.0, memory=None):
    """One episode of harness `which` on a fresh world, as a session plays one: run_dir/harness<n>.py and
    trace<n>.jsonl. The runtime's result."""
    os.makedirs(run_dir, exist_ok=True)
    path = os.path.join(run_dir, "harness.py")
    with open(path, "w", encoding="utf-8") as f:
        f.write(HARNESSES[which])
    if memory:
        fresh_imports(memory)
    harness = runtime.load_harness(path)
    game, world, _ = connect()
    try:
        game.restart()
        game.pause(False)
        rt = runtime.Runtime(game, harness, os.path.join(run_dir, f"trace{n}.jsonl"), client=FakeJev(LIKES[which]),
                             max_seconds=max_seconds)
        result = rt.run()
    finally:
        world.close()
    shutil.copyfile(path, os.path.join(run_dir, f"harness{n}.py"))
    return result
