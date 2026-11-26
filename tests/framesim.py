"""A fake link to a game that answers in frames, as the engine plugins do (Unreal's PlaytestSubsystem Tick: every
request that came in since the last frame is answered in this frame). A request waits until the next frame; requests
sent together share it, requests sent one after another take a frame each. Every answer is logged with the frame
that gave it, so a test can count the frames a piece of harness code cost.

It plays an Unreal game with `players` players (its hello's features say "players"), every read answered with
plain values: enough for Game.pose(), Game.player(n) and the controllers' reads. No engine, no network.
"""

import threading
from dataclasses import dataclass, field

LEVEL = "/Game/UEDPIE_0_Sim.Sim:PersistentLevel."


@dataclass
class Answer:
    op: str
    args: dict
    player: int
    frame: int
    thread: str = field(default="")


class FrameLink:
    """The link a Game reads through (request, post, hello, game_time), answering a frame every `period` seconds.

    frame: frames answered so far. log: every Answer, in order. posts: every (op, args) posted."""

    def __init__(self, players=2, period=0.005, features=("players",)):
        self.hello = {"project": "FrameSim", "engine": "unreal", "features": list(features)}
        self.players = players
        self.period = period
        self.frame = 0
        self.game_time = 0.0
        self.log = []
        self.posts = []
        self._pending = []  # (op, args, thread name, event, box)
        self._lock = threading.Lock()
        self._closed = threading.Event()
        self._thread = threading.Thread(target=self._run, name="framesim", daemon=True)
        self._thread.start()

    # -- the link --------------------------------------------------------------------------------------

    def request(self, op, args=None, timeout=None):
        from autopilot.bridge import BridgeError

        done, box = threading.Event(), {}
        with self._lock:
            if self._closed.is_set():
                raise BridgeError(503, "closed")
            self._pending.append((op, dict(args or {}), threading.current_thread().name, done, box))
        if not done.wait(timeout or 5.0):
            raise BridgeError(504, f"{op}: no frame answered in {timeout or 5.0} s")
        if "error" in box:
            raise box["error"]
        return box["value"]

    def post(self, op, args=None):
        with self._lock:
            self.posts.append((op, dict(args or {})))

    def close(self):
        self._closed.set()
        self._thread.join(timeout=1.0)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # -- what a test reads -------------------------------------------------------------------------------

    def mark(self):
        """Where the log is now: pass it to since() to see what came after."""
        with self._lock:
            return len(self.log)

    def since(self, mark):
        with self._lock:
            return list(self.log[mark:])

    def frames(self, answers):
        """The frames that answered these requests (a set: two reads in one frame count once)."""
        return {a.frame for a in answers}

    # -- the game --------------------------------------------------------------------------------------

    def _run(self):
        while not self._closed.wait(self.period):
            with self._lock:
                pending, self._pending = self._pending, []
                self.frame += 1
                self.game_time = round(self.frame * self.period, 4)
                frame = self.frame
                for op, args, thread, done, box in pending:
                    try:
                        box["value"] = self._answer(op, args)
                    except Exception as e:  # (a BridgeError, as the plugin's refusal)
                        box["error"] = e
                    self.log.append(Answer(op, args, int(args.get("player", 1)), frame, thread))
            for *_, done, _box in pending:
                done.set()
        with self._lock:  # (closed: nothing waits forever)
            pending, self._pending = self._pending, []
        for *_, done, box in pending:
            from autopilot.bridge import BridgeError

            box["error"] = BridgeError(503, "closed")
            done.set()

    def _answer(self, op, args):
        from autopilot.bridge import BridgeError

        n = int(args.get("player", 1))
        if n > self.players:
            raise BridgeError(400, f"no player {n}")
        if op == "context":
            return {"world": "/Game/UEDPIE_0_Sim.Sim", "character": f"{LEVEL}Hero_C_{n}",
                    "controller": f"{LEVEL}PC_{n}", "camera_manager": f"{LEVEL}PCM_{n}",
                    "input_subsystem": "", "game_time": self.game_time, "you": n,
                    "players": [{"player": i, "name": f"Player {i}", "path": f"{LEVEL}PS_{i}",
                                 "character": f"{LEVEL}Hero_C_{i}", "you": i == n}
                                for i in range(1, self.players + 1)]}
        if op == "rc" and args.get("verb") == "call":
            fn = (args.get("body") or {}).get("functionName")
            if fn == "GetControlRotation":
                return {"ReturnValue": {"Pitch": 0.0, "Yaw": 90.0 * n, "Roll": 0.0}}
            if fn == "GetVelocity":
                return {"ReturnValue": {"X": 0.0, "Y": 0.0, "Z": 0.0}}
            # (K2_GetActorLocation, GetCameraLocation: player n stands at x = 100 n)
            return {"ReturnValue": {"X": 100.0 * n, "Y": 0.0, "Z": 90.0}}
        if op == "rc" and args.get("verb") == "property":
            prop = (args.get("body") or {}).get("propertyName") or "Value"
            return {prop: 0}
        return {}
