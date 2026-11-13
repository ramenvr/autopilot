"""Game time for controllers: the clock controllers time their moves by, whatever the machine's frame rate.

A controller that counts wall seconds (time.monotonic) acts in a game that may run slower or faster than the wall
clock: on a machine that renders slowly every read waits a frame and a "0.3 s" wait covers less of the game; while
the game stands still (paused between an agent's tool calls, a menu) wall time passes and game time doesn't. Timed
by the game's own clock a move lasts the same in every run, so a harness tuned on one machine plays the same on
another.

    now(game)       the game time at this moment: the latest answer's game time, plus the wall time since it came
                    while the game runs (the plugin stamps every answer and event with the game's clock)
    Clock(game)     a controller's stopwatch in game seconds: elapsed() counts them since it was made, step() the
                    game seconds since the last step (the pace of its control loop). While the game's clock stands
                    still for STILL_AFTER (a pause menu, a loading screen, the game held between an agent's calls)
                    real time counts instead, so a wait on a stopped clock still ends.
    rate            how fast the game's clock runs against the wall's lately (1: real time; less: a slow game)
"""

import time

EXTRAPOLATE = 1.0  # seconds of game time guessed past the latest answer, at most (then the game has stopped)
STILL_AFTER = 0.4  # wall seconds without the game's clock moving before real time counts instead
RATE_WINDOW = 2.0  # wall seconds the game's rate against the wall clock is measured over


def latest(game):
    """(the latest answer's game time, the wall time it came in or None) from the game's link."""
    link = game.link
    t = getattr(link, "game_time", None)
    return (float(t) if isinstance(t, (int, float)) and not isinstance(t, bool) else 0.0,
            getattr(link, "game_time_at", None))


def now(game):
    """The game time now: the latest answer's, plus the wall time since it came while the game runs (none while it
    is paused, or past EXTRAPOLATE: a game that hasn't answered for that long has stopped or is being held)."""
    t, at = latest(game)
    if at is None or getattr(game, "paused", False):
        return t
    return t + max(0.0, min(EXTRAPOLATE, time.time() - at))


def uptime(game):
    """Game seconds counted since the Game was made, never going back (a restarted level's clock starts over): one
    timeline for what is kept on the Game across controllers (the level's map, kit.nav), stamped in game seconds."""
    t = now(game)
    state = game.__dict__.setdefault("_uptime", [t, 0.0])  # [the game time last seen, the seconds counted]
    if t > state[0]:
        state[1] += min(t - state[0], EXTRAPOLATE + 1.0)
    state[0] = t  # (a clock that went back is a restart: counted on from there)
    return state[1]


class Clock:
    """A stopwatch in game seconds for one controller (see the module's docstring)."""

    def __init__(self, game):
        self.game = game
        self.t = 0.0  # game seconds counted so far
        self.last = now(game)  # the game time last counted up to
        wall = time.monotonic()
        self.moved = wall  # wall time the game's clock last moved
        self.credited = wall  # wall time real seconds were last counted up to (while the clock stood still)
        self.stepped = None  # the count at the last step()
        self.rate = 1.0  # game seconds a wall second, lately
        self._samples = []  # (wall, game time) over RATE_WINDOW

    def elapsed(self):
        """Count the time since the last call and return the game seconds since the clock was made."""
        wall, t = time.monotonic(), now(self.game)
        if t > self.last:
            self.t += min(t - self.last, EXTRAPOLATE)
            self.last, self.moved, self.credited = t, wall, wall
        elif wall - self.moved > STILL_AFTER:  # the game's clock stands still: real time passes instead
            self.t += wall - max(self.credited, self.moved)
            self.credited = wall
        self._samples.append((wall, t))
        while len(self._samples) > 2 and wall - self._samples[0][0] > RATE_WINDOW:
            self._samples.pop(0)
        if len(self._samples) >= 2 and wall - self._samples[0][0] > 0.2:
            dt_game = self._samples[-1][1] - self._samples[0][1]
            self.rate = max(0.0, min(4.0, dt_game / (wall - self._samples[0][0])))
        return self.t

    def step(self):
        """The game seconds since the last step() (the first: a guess of a tenth), between 0.02 and 1: the pace a
        control loop runs at, so what it does now lasts about this long."""
        t = self.elapsed()
        dt = 0.1 if self.stepped is None else t - self.stepped
        self.stepped = t
        return min(1.0, max(0.02, dt))

    def since(self, t0):
        """Game seconds since the count stood at t0 (an earlier elapsed())."""
        return self.elapsed() - t0
