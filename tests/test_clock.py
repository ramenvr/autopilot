"""Game time (autopilot/kit/clock.py): controllers time their moves by the game's clock, and a move that must land on
a frame is handed to the game (Game.sequence, moveset, jump_at) where the plugin runs sequences."""

import threading
import time

from test_autopilot import FakeLink

from autopilot.kit import Clock, Game, Vec, jump_at, moveset, until, wait, walk_to
from autopilot.kit.clock import now, uptime


class SlowGameLink(FakeLink):
    """A game whose clock runs at `rate` of the wall clock (a machine that renders slowly): what the plugin stamps
    answers with is game time."""

    def __init__(self, rate=0.5):
        super().__init__()
        self.rate = rate

    def _advance(self):
        wall = time.monotonic()
        dt, self._wall = wall - self._wall, wall
        if self.paused:
            return
        self.game_time += dt * self.rate
        if "W" in self.held:
            import math

            f = Vec(math.cos(math.radians(self.yaw)), math.sin(math.radians(self.yaw)), 0)
            self.pos = self.pos + f * (400 * dt * self.rate)


class SequenceLink(FakeLink):
    """A plugin that runs input sequences (features: sequence): the steps fire by the game's clock, a `past`
    condition read against the player's position, as the Roblox plugin does."""

    def __init__(self):
        super().__init__()
        self.hello = {"project": "fake", "features": ["sequence"]}
        self.sequences = {}
        self.fired = []  # (game time, key, mode)

    def _advance(self):
        super()._advance()
        for sid, q in self.sequences.items():
            while not q["done"]:
                steps = q["steps"]
                if q["i"] >= len(steps):
                    q["done"] = True
                    break
                s = steps[q["i"]]
                due = q["t0"] + s["at"] if s.get("at") is not None else q["last"] + s.get("after", 0.0)
                if self.game_time < due:
                    break
                when = s.get("when") or {}
                if "past" in when:
                    p = when["past"]
                    along = (self.pos.x - p["X"]) * p["dX"] + (self.pos.y - p["Y"]) * p["dY"]
                    if along < 0:
                        if self.game_time - due > s.get("timeout", 3.0):
                            q["skipped"].append(q["i"])
                            q["i"] += 1
                            q["last"] = self.game_time
                            continue
                        break
                self.fired.append((round(self.game_time, 3), s["key"], s["mode"]))
                if s["mode"] == "hold":
                    self.held[s["key"]] = s.get("value")
                elif s["mode"] == "release":
                    self.held.pop(s["key"], None)
                q["fired"] += 1
                q["i"] += 1
                q["last"] = self.game_time

    def request(self, op, args=None, timeout=None):
        args = args or {}
        if op == "sequence":
            with self.lock:
                self._advance()
                if args.get("action") == "start":
                    self.sequences[args["id"]] = {"steps": args["steps"], "i": 0, "t0": self.game_time,
                                                  "last": self.game_time, "fired": 0, "skipped": [], "done": False}
                    return {"id": args["id"], "t0": self.game_time}
                q = self.sequences.get(args.get("id"))
                if args.get("action") == "status":
                    return {"done": True} if q is None else {"done": q["done"], "fired": q["fired"],
                                                             "skipped": q["skipped"], "t": self.game_time}
                self.sequences.pop(args.get("id"), None)
                return {}
        return super().request(op, args, timeout)


def drive(game, gen, seconds=30.0, hz=30.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < seconds:
        game.renew()
        try:
            next(gen)
        except StopIteration as s:
            return s.value
        time.sleep(1.0 / hz)
    raise AssertionError("the controller didn't end in time")


def test_now_runs_on_from_the_latest_answer_while_the_game_runs():
    link = FakeLink()
    link.game_time_at = time.time()
    link.game_time = 10.0
    game = Game(link)
    assert 10.0 <= now(game) < 10.05
    link.game_time_at = time.time() - 0.3
    assert 10.25 < now(game) < 10.4
    link.game_time_at = time.time() - 5.0  # (no answer for seconds: the game has stopped or is held)
    assert now(game) == 11.0
    game.paused = True
    assert now(game) == 10.0
    # a link that doesn't say when its answer came (a simulation) gives the answer's time
    link2 = FakeLink()
    assert now(Game(link2)) == link2.game_time


def test_a_clock_counts_game_seconds_and_real_ones_only_while_the_game_stands_still():
    link = SlowGameLink(rate=0.5)
    game = Game(link)
    clock = Clock(game)
    t0 = time.monotonic()
    while clock.elapsed() < 0.5:  # (the game's half second: a whole wall second)
        time.sleep(0.02)
        game.context(fresh=True)
    assert 0.8 < time.monotonic() - t0 < 1.5, time.monotonic() - t0
    assert 0.3 < clock.rate < 0.7, clock.rate
    link.paused = True  # the clock stands still: real time counts after a moment
    clock = Clock(game)
    t0 = time.monotonic()
    while clock.elapsed() < 0.5:
        time.sleep(0.02)
        game.context(fresh=True)
    assert 0.4 < time.monotonic() - t0 < 1.2, time.monotonic() - t0


def test_uptime_never_goes_back_when_the_level_restarts():
    link = FakeLink()
    game = Game(link)
    link.game_time = 40.0
    a = uptime(game)
    link.game_time = 41.0
    b = uptime(game)
    link.game_time = 0.5  # (a restart: the game's clock starts over)
    c = uptime(game)
    link.game_time = 2.0
    d = uptime(game)
    assert b - a == 1.0 and c == b and d - c == 1.5


def test_controllers_time_out_in_game_seconds_not_wall_seconds():
    link = SlowGameLink(rate=0.5)
    game = Game(link)
    t0 = time.monotonic()
    # (a condition that reads the game, as a controller's loop does: each read brings the game's time)
    r = drive(game, until(game, lambda: game.context(fresh=True) is None, timeout=0.6), seconds=10)
    assert not r.ok
    assert 1.0 < time.monotonic() - t0 < 1.9, time.monotonic() - t0  # (0.6 game seconds take 1.2 wall seconds)
    t0 = time.monotonic()
    drive(game, wait(game, 0.5), seconds=10)
    assert 0.85 < time.monotonic() - t0 < 1.7, time.monotonic() - t0


def test_a_slow_game_is_walked_the_same_distance():
    """A walk's result depends on the game's clock, not on how fast the machine runs it."""
    fast, slow = Game(FakeLink()), Game(SlowGameLink(rate=0.5))
    for g in (fast, slow):
        g.look = {"x": 0.1, "y": 0.1}
        r = drive(g, walk_to(g, Vec(600, 0, 100), within=60, timeout=8), seconds=30)
        assert r.ok, r
    assert abs(fast.link.pos.x - slow.link.pos.x) < 120, (fast.link.pos, slow.link.pos)


def test_moveset_is_run_by_the_game_when_the_plugin_can():
    link = SequenceLink()
    game = Game(link)
    steps = [{"key": "W", "mode": "hold"}, {"key": "SpaceBar", "mode": "press", "at": 0.3},
             {"key": "W", "mode": "release", "after": 0.2}]
    r = drive(game, moveset(game, steps), seconds=10)
    assert r.ok and r.data["by"] == "game" and r.data["fired"] == 3, r
    kinds = [(k, m) for _, k, m in link.fired]
    assert kinds == [("W", "hold"), ("SpaceBar", "press"), ("W", "release")]
    # (each step fires on the first game frame at or after its time; this fake's frames come when the test reads
    # it, so on a slow machine a frame can come a good while late: never early, though)
    t0 = next(iter(link.sequences.values()))["t0"]
    t_hold, t_jump, t_release = (t for t, _, _ in link.fired)
    assert t_hold <= t0 + 0.25 and 0.3 <= t_jump - t0 + 1e-6 <= 0.6, (t0, link.fired)
    assert 0.2 <= t_release - t_jump + 1e-6 <= 0.5, (t0, link.fired)
    assert "W" not in game.held()  # (released by the game, and the brain knows)


def test_jump_at_presses_the_frame_the_player_passes_the_edge():
    link = SequenceLink()
    game = Game(link)
    game.look = {"x": 0.1, "y": 0.1}
    r = drive(game, jump_at(game, Vec(300, 0, 100), Vec(1, 0, 0), hold=0.3), seconds=10)
    assert r.ok and r.data["by"] == "game", r
    jumped = next((t for t, k, m in link.fired if k == "SpaceBar"), None)
    assert jumped is not None
    # (the game pressed as the body crossed x=300, running at 400/s from x=0: about 0.75 s in)
    hold_t = link.fired[0][0]
    assert 0.6 < jumped - hold_t < 1.0, link.fired


def test_moveset_runs_from_the_brain_by_the_games_clock_without_the_feature():
    link = FakeLink()
    game = Game(link)
    past = {"past": {"point": Vec(200, 0, 0), "dir": Vec(1, 0, 0)}}
    steps = [{"key": "W", "mode": "hold"}, {"key": "SpaceBar", "mode": "press", "when": past, "timeout": 3.0},
             {"key": "W", "mode": "release", "after": 0.1}]
    r = drive(game, moveset(game, steps), seconds=10)
    assert r.ok and r.data["by"] == "brain" and r.data["fired"] == 3, r
    presses = [i for i in link.inputs if i.get("key") == "SpaceBar" and i.get("mode") == "press"]
    assert len(presses) == 1 and link.pos.x >= 200
    assert "W" not in link.held


def test_moveset_skips_a_step_whose_condition_never_comes():
    link = FakeLink()
    game = Game(link)
    steps = [{"key": "SpaceBar", "mode": "press", "when": {"past": {"point": Vec(-500, 0, 0), "dir": Vec(-1, 0, 0)}},
              "timeout": 0.3}]
    r = drive(game, moveset(game, steps), seconds=10)
    assert not r.ok and r.data["skipped"] == [0], r


def test_a_sequence_is_only_posted_with_the_feature():
    game = Game(FakeLink())
    assert game.sequence([{"key": "W", "mode": "hold"}]) is None
    link = SequenceLink()
    game = Game(link)
    sid = game.sequence([{"key": "W", "mode": "hold"}])
    assert sid and sid in link.sequences
    assert "W" in game.held()  # (a hold the sequence leaves down is the brain's to renew)
    sid2 = game.sequence([{"key": "W", "mode": "release", "after": 0.1}])
    assert "W" not in game.held()  # (and one it releases is let go of)
    done = threading.Event()
    deadline = time.time() + 5
    while time.time() < deadline and not game.sequence_status(sid2).get("done"):
        time.sleep(0.05)
    done.set()
    assert game.sequence_status(sid2).get("done") and "W" not in link.held
