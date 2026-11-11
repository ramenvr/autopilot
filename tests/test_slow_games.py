"""Playing a slow game: a low frame rate and slow round trips (each read waits for the game's answer, every input
reaches it late). The kit measures the pace (game.fps, game.round_trip), calibrates the mouse however long a turn takes
to show, keeps held keys down between slow steps, reads each controller step in one round trip, takes off for jumps
at half-second steps, and tells the agent the pace (the briefing, each episode's summary, SLOW STEPS)."""

import collections
import math
import time
import types

import pytest
from conftest import wall_clock
from test_autopilot import FakeLink, drive

from autopilot import runtime
from autopilot.kit import Harness, Option, Status, control, look_at, walk_to
from autopilot.kit import game as game_mod
from autopilot.kit.game import Game
from autopilot.kit.geom import Vec


class PacedLink(FakeLink):
    """FakeLink's flat world run at `fps` frames a second of real time, its round trips `trip` seconds long: a request
    takes half of it to arrive, is answered at the next frame, and takes the other half to come back; an input (a post)
    arrives half a trip after it was sent and acts at the next frame. A mouse turn is spread over `spread` frames, and a
    turn after a pause starts only `late_frames` frames after it arrived (as a camera that locks the mouse first). Held
    keys are leased as the plugins lease them: a key whose lease runs out is let go (kept in let_go). The game says its
    frame rate (fps_reports, as bridge.Link keeps the heartbeats')."""

    def __init__(self, fps=10.0, trip=0.15, spread=1, late_frames=0):
        super().__init__()
        self.dt, self.trip, self.spread, self.late_frames = 1.0 / fps, trip, spread, late_frames
        self.start = time.monotonic()
        self.frame = self.target = 0
        self.inbox, self.turning, self.leases, self.let_go = [], [], {}, []
        self.turned_at = -1e9  # (the frame the last turn began)
        self.fps_reports = collections.deque([float(fps)] * 3, maxlen=10)

    def _advance(self):  # (FakeLink.request calls it, with the lock held: the frames up to the one answering)
        while self.frame < self.target:
            self.frame += 1
            gt = self.frame * self.dt
            due = [a for at, a in self.inbox if at <= self.start + gt]
            self.inbox = [(at, a) for at, a in self.inbox if at > self.start + gt]
            for a in due:
                self._input(a, gt)
            for turn in list(self.turning):
                if turn["from"] <= self.frame:
                    self.yaw = (self.yaw + turn["yaw"]) % 360
                    self.pitch = max(-89.0, min(89.0, self.pitch + turn["pitch"]))
                    turn["left"] -= 1
                    if turn["left"] <= 0:
                        self.turning.remove(turn)
            for key, until in list(self.leases.items()):
                if gt > until:
                    self.leases.pop(key)
                    self.held.pop(key, None)
                    self.let_go.append((round(gt, 2), key))
            if "W" in self.held:
                f = Vec(math.cos(math.radians(self.yaw)), math.sin(math.radians(self.yaw)), 0)
                self.pos = self.pos + f * (400 * self.dt)
            self.game_time = gt

    def _input(self, a, gt):
        self.inputs.append(dict(a))
        mode, key = a.get("mode"), a.get("key")
        if mode == "release_all":
            self.held.clear()
            self.leases.clear()
        elif mode == "hold":
            self.held[key] = a.get("value")
            self.leases[key] = gt + float(a.get("lease", 0.6))
        elif mode == "release":
            self.held.pop(key, None)
            self.leases.pop(key, None)
        elif key in ("MouseX", "MouseY"):
            first = self.frame + (self.late_frames if self.frame - self.turned_at > 10 else 0)
            self.turned_at = first
            per = 0.1 * a.get("value", 0) / self.spread
            self.turning.append({"from": first, "left": self.spread, "yaw": per if key == "MouseX" else 0.0,
                                 "pitch": per if key == "MouseY" else 0.0})

    def post(self, op, args=None):
        with self.lock:
            self.inbox.append((time.monotonic() + self.trip / 2, dict(args or {})))

    def request(self, op, args=None, timeout=None):
        if op == "key":
            self.post(op, args)
            return {}
        time.sleep(self.trip / 2)
        f = int((time.monotonic() - self.start) / self.dt) + 1  # (answered at the next frame)
        time.sleep(max(0.0, self.start + f * self.dt - time.monotonic()))
        with self.lock:
            self.target = max(self.target, f)
        out = super().request(op, args, timeout)
        time.sleep(self.trip / 2)
        return out

    def view(self):
        """(yaw -180..180, pitch) of the game now."""
        with self.lock:
            self.target = max(self.target, int((time.monotonic() - self.start) / self.dt))
            self._advance()
            return (self.yaw + 180) % 360 - 180, self.pitch


# -- the pace ---------------------------------------------------------------------------------------------------

def test_the_game_measures_its_round_trips_and_frame_rate():
    from autopilot import bridge

    link = bridge.Link(lambda frame: None)
    for t, fps in ((None, 60), (1.0, 11), (2.0, 13), (3.0, 240), (4.0, 12), (5.0, 0), (6.0, "x")):
        link.feed({"type": "events", "events": [{"kind": "heartbeat", "t": t, "fps": fps}]})
    # (not the editor's own heartbeat without a game, nor nonsense; a median, so one hitch-free frame's 240 doesn't
    # move it)
    assert list(link.fps_reports) == [11.0, 13.0, 240.0, 12.0]
    game = Game(PacedLink(fps=12, trip=0.1))
    assert game.round_trip is None and game.fps == 12.0
    pace = game.measure_pace(reads=4)
    # (a 100 ms trip and up to a frame's wait: 100-185 ms here; a busy CI machine's sleeps run long, so only the lower
    # bound is tight)
    assert pace["fps"] == 12.0 and 90 <= pace["round_trip_ms"] <= 1000, pace
    assert game.round_trip_p90 >= game.round_trip and game.slow()
    fast = Game(FakeLink())  # (no frame rate reported: not waited for)
    t0 = time.monotonic()
    pace = fast.measure_pace()
    assert time.monotonic() - t0 < 1.0 and pace["fps"] is None and pace["round_trip_ms"] < 50 and not fast.slow()


def test_a_slow_game_holds_its_keys_with_longer_leases():
    game = Game(FakeLink())
    assert game.key_lease == game_mod.KEY_LEASE  # (nothing measured yet)
    game._trips.extend([0.3] * 20)  # (reads of 300 ms)
    assert game.key_lease == pytest.approx(1.2)
    game._trips.extend([30.0] * 60)  # (a game that hardly answers: the lease stays short enough for a gone brain)
    assert game.key_lease == game_mod.KEY_LEASE_MAX


def test_renewals_come_once_a_third_of_a_lease_however_fast_the_ticks():
    link = FakeLink()
    game = Game(link)
    game.hold("W")
    t0 = time.monotonic()
    while time.monotonic() - t0 < 0.15:  # (ticks renew, and an executor holds again, every few ms here)
        game.hold("W")
        game.renew_due()
        time.sleep(0.005)
    holds = [i for i in link.inputs if i.get("key") == "W" and i.get("mode") == "hold"]
    assert 1 <= len(holds) <= 2, holds
    game.hold("W", 0.5)  # (a changed hold goes at once)
    holds = [i for i in link.inputs if i.get("key") == "W" and i.get("mode") == "hold"]
    assert holds[-1].get("value") == 0.5
    n = len(holds)
    game.renew()  # (renew() itself renews at once)
    assert len([i for i in link.inputs if i.get("key") == "W" and i.get("mode") == "hold"]) == n + 1
    game.release_all()


# -- the mouse ---------------------------------------------------------------------------------------------------

@wall_clock
@pytest.mark.parametrize("fps, trip, spread, late", [(10, 0.15, 2, 4), (60, 0.005, 1, 0)])
def test_calibrate_look_measures_the_mouse_however_slowly_the_view_turns(fps, trip, spread, late):
    link = PacedLink(fps=fps, trip=trip, spread=spread, late_frames=late)
    game = Game(link)
    t0 = time.monotonic()
    look = game.calibrate_look()
    took = time.monotonic() - t0
    assert look["x"] == pytest.approx(0.1, rel=0.1) and look["y"] == pytest.approx(0.1, rel=0.1), look
    yaw, pitch = link.view()
    assert abs(yaw) < 1.0 and abs(pitch) < 1.0, (yaw, pitch)  # (turned back where it was)
    if fps >= 60:
        assert took < 1.5, took  # (a fast game: as quick as ever)


@wall_clock
def test_calibrate_look_keeps_no_result_a_second_turn_disagrees_with():
    class Jittery(PacedLink):
        """A mouse whose turns come out a different size each time (half, then whole, then half...)."""

        n = 0

        def _input(self, a, gt):
            if a.get("key") == "MouseX":
                self.n += 1
                a = dict(a, value=a["value"] * (0.4 if self.n % 2 else 1.0))
            super()._input(a, gt)

    game = Game(Jittery(fps=60, trip=0.005))
    assert game.calibrate_look()["x"] is None  # (not a wrong number kept for the run: measured again later)


# -- held keys ---------------------------------------------------------------------------------------------------

@wall_clock
def test_held_keys_stay_down_between_slow_steps():
    link = PacedLink(fps=10, trip=0.15)
    game = Game(link)
    seen = []

    def slow_walk(steps):
        for _ in range(steps):
            game.hold("W")
            game.pose()  # (three reads one after another, and more: over a second a step here, longer than a lease)
            game.view_rotation()
            game.me()
            time.sleep(0.5)
            seen.append("W" in link.held)
            yield

    control.run(slow_walk(5), game=game)  # (as a probe drives a controller; an episode keeps them held the same way)
    assert link.let_go == [] and all(seen), (link.let_go, seen)
    assert game.key_lease > game_mod.KEY_LEASE  # (the lease follows the reads' time)
    game.pose()
    assert not link.held  # (let go at the end)


@wall_clock
def test_a_key_nobody_renews_is_let_go():
    link = PacedLink(fps=60, trip=0.005)
    game = Game(link)
    game.hold("D")  # (a probe's bare hold: no run(), no episode)
    time.sleep(1.5)
    link.view()
    assert [key for _, key in link.let_go] == ["D"]


@wall_clock
def test_a_key_held_by_code_that_does_nothing_for_long_is_let_go(monkeypatch):
    monkeypatch.setattr(game_mod, "KEY_ALONE", 0.4)
    link = PacedLink(fps=60, trip=0.005)
    game = Game(link)
    with game.keeping_held():
        game.hold("W")
        time.sleep(1.5)  # (hung: no read, no input)
        link.view()
    assert [key for _, key in link.let_go] == ["W"]


# -- a step's reads in one round trip ------------------------------------------------------------------------------

def _waits_per_step(game, gen, limit=400):
    waits = []
    for _ in range(limit):
        w0 = game.waits()
        try:
            next(gen)
        except StopIteration as s:
            waits.append(game.waits() - w0)
            return s.value, waits
        waits.append(game.waits() - w0)
        game.renew()
        time.sleep(1 / 60)
    raise AssertionError("the controller didn't finish")


def test_walk_to_and_look_at_wait_on_the_game_once_a_step():
    link = FakeLink()
    game = Game(link)
    game.look = {"x": 0.1, "y": 0.1}
    r, waits = _waits_per_step(game, walk_to(game, Vec(600, -300, 100), within=80, timeout=10))
    assert r.ok, r
    # (its first step also reads the character's movement and feels the way first; then each step one round trip:
    # the player, the target and the sonar together; once it stopped short of the target and walks on again, the last
    # look at where it stopped and the next step's are in one step)
    assert len(waits) > 10 and max(waits[1:]) <= 2 and waits[1:].count(2) <= 2, waits
    crate = "/Game/M.M:PersistentLevel.Crate_C_0"
    r, waits = _waits_per_step(game, look_at(game, crate, timeout=3))
    assert r.ok, r
    assert waits[0] <= 2 and max(waits[1:] or [1]) == 1, waits  # (the first: also how big the crate looks)


def test_melee_reads_once_a_step():
    link = FakeLink()
    game = Game(link)
    game.look = {"x": 0.1, "y": 0.1}
    crate = "/Game/M.M:PersistentLevel.Crate_C_0"
    gen = control.melee(game, crate, "E", reach=150, timeout=3)
    _, waits = _waits_per_step(game, gen)
    assert max(waits) == 1, waits


# -- jumping at half-second steps --------------------------------------------------------------------------------

class JumpWorld(FakeLink):
    """A flat floor with a long platform 60 high (too high to step onto), in simulated time: each controller step
    takes `step` seconds of it (a game that answers that slowly). The player speeds up to 600 while W is held and jumps
    at 700 against a gravity of 1960."""

    BOX = "/Game/M.M:PersistentLevel.Crate_C_0"

    def __init__(self, step=0.5):
        super().__init__()
        self.step_s = step
        self.clock = types.SimpleNamespace(t=0.0)
        self.pos = Vec(0, 0, 90)
        self.vel = Vec()
        self.box = (Vec(1600, 0, 30), Vec(400, 300, 30))  # (top at 60)
        self.jumps = 0

    def _advance(self):
        pass  # (time moves only in step())

    def _floor(self, x, y):
        c, e = self.box
        return c.z + e.z if abs(x - c.x) <= e.x and abs(y - c.y) <= e.y else 0.0

    def request(self, op, args=None, timeout=None):
        args = args or {}
        body = args.get("body") or {}
        if op == "key" and args.get("mode") == "press" and args.get("key") == "SpaceBar":
            with self.lock:
                if abs(self.pos.z - 90 - self._floor(self.pos.x, self.pos.y)) < 1:
                    self.vel = Vec(self.vel.x, self.vel.y, 700)
                    self.jumps += 1
            return {}
        if op == "rc" and body.get("functionName") == "GetVelocity" and "Hero" in str(body.get("objectPath")):
            return {"ReturnValue": self.vel.ue()}
        if op == "rc" and body.get("functionName") == "GetActorBounds" and "Crate" in str(body.get("objectPath")):
            return {"Origin": self.box[0].ue(), "BoxExtent": self.box[1].ue()}
        return super().request(op, args, timeout)

    def step(self, seconds=None):
        """The world moved on by `seconds` (a controller step's: step_s), at 120 updates a second."""
        seconds = self.step_s if seconds is None else seconds
        n = max(1, round(seconds * 120))
        dt = seconds / n
        for _ in range(n):
            with self.lock:
                feet, floor = self.pos.z - 90, self._floor(self.pos.x, self.pos.y)
                ground = abs(feet - floor) < 1 and self.vel.z <= 0
                if ground:
                    f = Vec(math.cos(math.radians(self.yaw)), math.sin(math.radians(self.yaw)), 0)
                    want = f * 600 if "W" in self.held else Vec()
                    dv = want - self.vel.flat
                    k = min(1.0, 2048 * dt / max(1e-6, dv.length))
                    self.vel = Vec(self.vel.x + dv.x * k, self.vel.y + dv.y * k, 0)
                else:
                    self.vel = Vec(self.vel.x, self.vel.y, self.vel.z - 1960 * dt)
                nx, ny, nz = self.pos.x + self.vel.x * dt, self.pos.y + self.vel.y * dt, self.pos.z + self.vel.z * dt
                if nz - 90 < self._floor(nx, ny) - 1 and self._floor(nx, ny) > self._floor(self.pos.x, self.pos.y):
                    nx, ny = self.pos.x, self.pos.y  # (into the platform's side: stopped)
                    self.vel = Vec(0, 0, self.vel.z)
                landed = self._floor(nx, ny)
                if nz - 90 <= landed and self.vel.z <= 0:
                    nz, self.vel = landed + 90, Vec(self.vel.x, self.vel.y, 0)
                self.pos = Vec(nx, ny, nz)
                self.clock.t += dt
                self.game_time += dt

    def run(self, gen, limit=60.0):
        saved = control.time
        # (a sleep inside a step lets the world move on meanwhile, as a game does while the code waits)
        control.time = types.SimpleNamespace(monotonic=lambda: self.clock.t,
                                             sleep=lambda s: self.step(s) if s > 0 else None)
        try:
            while self.clock.t < limit:
                try:
                    next(gen)
                except StopIteration as s:
                    return s.value
                self.step()
            raise AssertionError("the jump didn't finish")
        finally:
            control.time = saved


def test_jump_to_takes_off_when_steps_take_half_a_second(monkeypatch):
    numbers = {"pawn": "/Game/M.M:PersistentLevel.Hero_C_0", "walk_speed": 600.0, "jump_z": 700.0, "gravity": 1960.0,
               "half_height": 90.0, "step": 45.0, "air_control": 0.05}
    numbers["peak"] = 700.0 ** 2 / (2 * 1960.0)
    numbers["reach_up"] = numbers["peak"] + 45.0
    numbers["reach_up_max"] = numbers["reach_up"] + 10.0
    monkeypatch.setattr(control, "movement", lambda game: numbers)
    world = JumpWorld(step=0.5)
    game = Game(world)
    game.look = {"x": 0.1, "y": 0.1}
    r = world.run(control.jump_to(game, JumpWorld.BOX))
    assert world.jumps >= 1, r  # (it took off: "at running speed" from its last readings, not the last 0.3-0.5 s)
    assert r.ok, r


# -- telling the agent -------------------------------------------------------------------------------------------

def test_pace_lines():
    assert runtime.pace_line({"fps": 11.6, "round_trip_ms": 183}, now=True) == (
        "This game runs at about 12 fps; a read of it takes about 183 ms. A slow game: see the kit's 'A slow game'.")
    assert runtime.pace_line({"fps": 60, "round_trip_ms": 8}) == (
        "This game ran at about 60 fps; a read of it took about 8 ms.")
    assert runtime.pace_line({"fps": None, "round_trip_ms": 12}, now=True).startswith("This game's frame rate isn't")
    assert runtime.pace_line({}) == "" and runtime.pace_line(None) == ""


@wall_clock
def test_the_briefing_says_how_fast_the_game_runs_and_answers(tmp_path, monkeypatch):
    from autopilot import codex
    from autopilot.agents import session

    monkeypatch.setattr(codex, "MEMORY", str(tmp_path))
    s = session.Session(Game(PacedLink(fps=12, trip=0.15)), "fake", "Verify that x", "r1")
    opening = s.opening()
    line = next(x for x in opening.splitlines() if x.startswith("pace: "))
    assert "This game runs at about 12 fps; a read of it takes about " in line and "A slow game" in line, line
    assert line in session.briefing(s, opening)
    fast = session.Session(Game(FakeLink()), "fake", "Verify that x", "r2").opening()
    line = next(x for x in fast.splitlines() if x.startswith("pace: "))
    assert "frame rate isn't reported" in line and "A slow game" not in line, line


@wall_clock
def test_the_summary_says_the_pace_and_flags_steps_slow_by_their_time(tmp_path):
    class SlowReads(Harness):
        goal = "Watch"
        tempo = 0.0
        tick_hz = 20.0

        def observe(self, game):
            return {"_pose": game.pose()}

        def watch(self, game, obs):
            while True:
                game.view_rotation()
                time.sleep(0.3)  # (one read, but a slow one: a game at a few frames a second)
                yield

        def options(self, obs):
            return [Option("watch", "Watch", self.watch)]

        def status(self, obs):
            return Status()

    link = FakeLink()
    link.fps_reports = collections.deque([9.0, 10.0, 11.0])
    game = Game(link)
    path = str(tmp_path / "t.jsonl")
    res = runtime.Runtime(game, SlowReads(), path, client=None, max_seconds=5).run()
    assert res["pace"]["fps"] == 10.0 and res["steps"]["watch"]["waits_mean"] < runtime.SLOW_WAITS, res
    text = runtime.summarize(path)
    assert "This game ran at about 10 fps; a read of it took about" in text, text[:800]
    assert "SLOW STEPS: watch" in text and "each read itself takes long" in text, text


@wall_clock
def test_until_and_track_count_game_time():
    link = FakeLink()
    game = Game(link)
    game.look = {"x": 0.1, "y": 0.1}
    real = link._advance

    def half():  # (the game's clock runs at half the real pace: 0.5 s of it are a second of real time)
        before = link.game_time
        real()
        link.game_time = before + (link.game_time - before) / 2

    link._advance = half
    for gen in (control.until(game, lambda: (game.pose(), False)[1], timeout=0.5),
                control.track(game, Vec(800, 0, 160), 0.5)):
        t0 = time.monotonic()
        drive(game, gen)
        assert time.monotonic() - t0 >= 0.85, time.monotonic() - t0
