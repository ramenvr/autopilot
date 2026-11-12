"""Looking while playing (autopilot/watch.py): the recording's last seconds answered as questions while a harness
plays, the answers reaching the harness and the trace, each death reviewed at the episode's end, and the summary
saying what the picture showed."""

import json
import threading
import time

from test_autopilot import CrateHarness, FakeJev, FakeLink

from autopilot import runtime, watch
from autopilot.kit import Game, Harness, Option, Status
from autopilot.vision import Frames


class FakeLooker:
    """Answers every look with what the test sets, after `delay` seconds; keeps what it was asked."""

    def __init__(self, answers=None, delay=0.0, text="The player ran into a red beam and fell."):
        self.answers = answers or {"blocked": 0.1, "cause": {"hazard": 0.8, "fall": 0.1, "nothing": 0.1}, "menu": 0.0}
        self.delay = delay
        self.text = text
        self.looks = []
        self.described = []
        self.lock = threading.Lock()

    def available(self):
        return None

    def can_describe(self):
        return True

    def look(self, wall0, wall1, questions, facts=None, rate=watch.RATE, limit=watch.MAX_FRAMES):
        time.sleep(self.delay)
        with self.lock:
            self.looks.append({"wall0": wall0, "wall1": wall1, "questions": list(questions), "facts": facts})
        return {**self.answers, "frames": [1.0, 2.0], "by": "fake", "latency": self.delay}

    def describe(self, wall0, wall1, question=watch.REVIEW_QUESTION, rate=watch.RATE, limit=watch.MAX_FRAMES):
        with self.lock:
            self.described.append((wall0, wall1))
        return self.text


class DyingHarness(Harness):
    """A player that dies twice (its deaths count goes up) while an option runs, then opens the crate."""
    goal = "Open the crate without dying"
    tempo = 4.0
    tick_hz = 20.0
    watch_every = 0.5

    def setup(self, game):
        self.deaths = 0
        self.steps = 0
        self.watched = []
        self.asked = 0

    def observe(self, game):
        return {"deaths": self.deaths, "opened": self.steps >= 30}

    def options(self, obs):
        return [Option("go", "go to the crate", self.go), Option("wait", "wait", self.idle_a_bit)]

    def go(self, game, obs):
        for _ in range(12):
            self.steps += 1
            if self.steps in (5, 9):
                self.deaths += 1
            yield

    def idle_a_bit(self, game, obs):
        yield

    def watch_questions(self, obs):
        self.asked += 1
        return {"beam": "A beam crosses the player's way", "menu": None, "clear_shot": True}

    def on_watch(self, answers, obs):
        self.watched.append(answers)

    def status(self, obs):
        return Status("done", "opened") if obs["opened"] else Status()


def test_the_watcher_looks_after_deaths_and_the_harness_hears_the_answers(tmp_path):
    link = FakeLink()
    game = Game(link)
    looker = FakeLooker()
    h = DyingHarness()
    rt = runtime.Runtime(game, h, str(tmp_path / "t.jsonl"), client=FakeJev("go"), max_seconds=20, looker=looker)
    res = rt.run()
    assert res["status"]["state"] == "done", res
    assert res["deaths"] == 2
    assert looker.looks, "no look was made"
    # the harness's questions went with the defaults, its None dropped one, its True turned an optional one on
    asked = looker.looks[0]["questions"]
    assert "beam" in asked and "clear_shot" in asked and "blocked" in asked and "menu" not in asked
    assert h.watched and h.watched[0]["cause"]["hazard"] == 0.8
    assert game.watch.get("cause", {}).get("hazard") == 0.8 and "t" in game.watch
    events = [json.loads(line) for line in open(tmp_path / "t.jsonl", encoding="utf-8")]
    kinds = [e["kind"] for e in events]
    assert kinds.count("death") == 2 and "watch" in kinds and kinds.count("review") == 2
    assert any(e.get("trigger") == "death" for e in events if e["kind"] == "watch")
    assert len(looker.described) == 2  # (each death's last seconds described at the end)
    text = runtime.summarize(str(tmp_path / "t.jsonl"))
    assert "What the picture showed" in text and "deaths: 2" in text
    assert "red beam" in text and "hazard" in text, text


def test_a_slow_look_never_holds_up_the_episode(tmp_path):
    link = FakeLink()
    game = Game(link)
    looker = FakeLooker(delay=1.5)
    h = DyingHarness()
    rt = runtime.Runtime(game, h, str(tmp_path / "t.jsonl"), client=FakeJev("go"), max_seconds=20, looker=looker)
    t0 = time.monotonic()
    res = rt.run()
    assert res["status"]["state"] == "done"
    # the episode's ticks ran at their rate (the look's 1.5 s never blocked them); the end waited for the reviews
    assert res["ticks"]["ran_hz"] > 10, res["ticks"]
    assert time.monotonic() - t0 < 15


def test_no_looker_means_no_looks_and_a_plain_summary(tmp_path):
    game = Game(FakeLink())
    rt = runtime.Runtime(game, CrateHarness(), str(tmp_path / "t.jsonl"), client=FakeJev("open_crate"), max_seconds=20)
    rt.run()
    text = runtime.summarize(str(tmp_path / "t.jsonl"))
    assert "What the picture showed" not in text


def test_a_looker_that_cant_look_is_noted_once(tmp_path):
    class NoLook(FakeLooker):
        def available(self):
            return "the game isn't filmed in this run"

    game = Game(FakeLink())
    rt = runtime.Runtime(game, CrateHarness(), str(tmp_path / "t.jsonl"), client=FakeJev("open_crate"), max_seconds=20,
                         looker=NoLook())
    rt.run()
    events = [json.loads(line) for line in open(tmp_path / "t.jsonl", encoding="utf-8")]
    assert [e["kind"] for e in events].count("watch_off") == 1
    assert rt.watcher is None


def test_the_looker_samples_the_last_seconds_and_asks_jev_with_the_frames():
    frames = Frames()
    wall = time.time()
    for i in range(40):  # 8 s of frames at 5 a second
        frames.add(b"\xff\xd8\xff" + bytes([i]), 100.0 + i * 0.2, wall - 8.0 + i * 0.2)
    seen = {}

    class Jev:
        def system_one(self, state, questions, **kwargs):
            seen["state"], seen["questions"], seen["headers"] = state, questions, kwargs.get("extra_headers")
            import types

            ns = types.SimpleNamespace
            return ns(nouls={"blocked": ns(noul=0.2), "menu": ns(noul=0.0)},
                      choices={"cause": ns(choice="fall", probabilities={"fall": 0.7, "hazard": 0.3})}, scores={})

    looker = watch.Looker(frames, jev=Jev(), route=None)
    answers = looker.look(wall - 5.0, wall, watch.DEFAULT, facts={"stage": 2})
    assert answers["by"] == "jev" and answers["blocked"] == 0.2 and answers["cause"]["fall"] == 0.7
    assert len(answers["frames"]) == 15  # 5 s at 3 a second
    assert len(seen["state"]["frames"]) == watch.JEV_FRAMES and seen["state"]["situation"] == {"stage": 2}
    assert set(seen["questions"]) == {"blocked", "cause", "menu"}
    assert seen["headers"] == {"TypeSafe-Danger-Zone-Images": "true"}  # (TypeSafe's opt-in for images)


def test_jev_failing_on_pictures_gives_way_to_the_vision_model(monkeypatch):
    frames = Frames()
    wall = time.time()
    for i in range(20):
        frames.add(b"\xff\xd8\xff" + bytes([i]), 10.0 + i * 0.25, wall - 5.0 + i * 0.25)

    class BrokenJev:
        def system_one(self, state, questions):
            raise RuntimeError("assets not supported")

    asked = []

    class FakeWire:
        def __init__(self, route, provider, **kw):
            self.route = route

        def ask(self, parts, system, max_tokens=None, effort=None):
            asked.append(parts)
            return '{"blocked": 0.9, "cause": {"hazard": 1.0}, "menu": 0, "note": "stuck at a ledge"}', "m", None

    from autopilot import vision

    monkeypatch.setattr(vision, "Wire", FakeWire)
    looker = watch.Looker(frames, jev=BrokenJev(), route=("anthropic", "http://x", "k", "m", None))
    a = looker.look(wall - 5.0, wall, watch.DEFAULT)
    assert a["by"] == "model" and a["blocked"] == 0.9 and a["cause"]["hazard"] == 1.0
    assert a["note"] == "stuck at a ledge"
    assert looker.jev_failed == 1
    # the pictures went: one image part per frame, and the questions as text
    kinds = [k for k, _ in asked[0]]
    assert 12 <= kinds.count("image") <= 15 and kinds[-1] == "text", kinds
    looker.look(wall - 5.0, wall, watch.DEFAULT)
    assert looker.jev_failed == 2
    looker.look(wall - 5.0, wall, watch.DEFAULT)  # (Jev isn't asked any more)
    assert looker.jev_failed == 2 and looker.calls["model"] == 3


def test_the_sandbox_child_looks_through_the_parent(monkeypatch):
    """A RemoteLooker's messages are served by the parent's looker (autopilot/sandbox.py)."""
    looker = FakeLooker()
    msg = {"op": "look", "wall0": 1.0, "wall1": 6.0, "questions": watch.jsonable_questions(watch.DEFAULT),
           "facts": {"x": 1}, "rate": 3.0, "limit": 15}
    out = watch.serve(looker, msg)
    assert out["cause"]["hazard"] == 0.8 and looker.looks[0]["questions"] == ["blocked", "cause", "menu"]
    assert looker.looks[0]["facts"] == {"x": 1}
    assert watch.serve(looker, {"op": "describe", "wall0": 1.0, "wall1": 6.0}) == looker.text


def test_the_picture_summary_reads_the_trace():
    events = [{"kind": "start", "wall": 1.0}, {"kind": "obs", "t": 10.0},
              {"kind": "death", "t": 14.0, "how": "a death count went up", "n": 1},
              {"kind": "watch", "t": 14.7, "trigger": "death", "answers": {"blocked": 0.1, "cause": {"hazard": 0.9,
                                                                                                    "fall": 0.1},
                                                                           "menu": 0.0, "note": "it hit a beam"}},
              {"kind": "watch", "t": 20.0, "trigger": "time", "answers": {"blocked": 0.95, "cause": {"nothing": 1.0},
                                                                          "menu": 0.0, "beam": 0.8}},
              {"kind": "review", "t": 14.0, "how": "a death count went up", "cause": "hazard", "p": 0.9,
               "text": "The player walked into a horizontal red beam."}]
    lines = watch.picture_summary(events)
    text = "\n".join(lines)
    assert "Deaths: 1" in text and "hazard x1" in text
    assert "Death at t=4.0s [hazard 0.9]: The player walked into a horizontal red beam." in text
    assert "looked stuck in 1" in text and "beam: yes in 1 of 1 looks" in text
    assert watch.picture_summary([{"kind": "start"}]) == []
