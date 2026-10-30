"""The plan in the harness contract (kit.harness.Step): the current step's options, what Jev is told, the stall watch,
progress carried into the next episode, the summary, and check(). Against a fake game (no Unreal, no models)."""

import json
import types

import pytest
from test_autopilot import FakeLink

from autopilot import runtime
from autopilot.kit import Harness, Option, Result, Status
from autopilot.kit.game import Game
from autopilot.kit.harness import Step


class RecordingJev:
    """Picks `likes` when offered, else the first; keeps what each call offered and told."""

    def __init__(self, likes):
        self.likes, self.calls = likes, []

    def system_one(self, state, questions):
        pick = questions["pick"]
        names = list(pick.criteria)
        self.calls.append({"names": names, "state": json.loads(json.dumps(state)), "instructions": pick.instructions})
        best = self.likes if self.likes in names else names[0]
        probs = {n: (0.8 if n == best else 0.2 / max(1, len(names) - 1)) for n in names}
        choice = types.SimpleNamespace(choice=best, probabilities=probs, confidence=0.6)
        return types.SimpleNamespace(choices={"pick": choice}, nouls={}, scores={})


class LeverHarness(Harness):
    """Pull the lever, then go through the gate it unlocks. Pulling stays legal after the lever is pulled (as a real
    harness's first option did, and Jev took it four times over): only the plan keeps Jev from pulling again."""

    goal = "Pull the lever and go through the gate"
    tempo = 0.0
    tick_hz = 30.0
    stall_seconds = 0.0
    plan = [Step("lever", "Pull the lever", lambda o: o["pulls"] >= 1, options=["pull_lever"]),
            Step("gate", "Go through the gate", lambda o: o["gate"] == "open", options=["open_gate"])]

    def setup(self, game):
        self.pulls, self.gate, self.resumed = 0, "shut", None
        self.lever_works = True

    def resume(self, game, progress):
        self.resumed = progress

    def observe(self, game):
        return {"_pose": game.pose(), "pulls": self.pulls, "gate": self.gate}

    def pull(self, game, obs):
        for _ in range(3):
            yield
        if self.lever_works:
            self.pulls += 1
        return Result(self.lever_works, "pulled" if self.lever_works else "jammed")

    def open_gate(self, game, obs):
        for _ in range(3):
            yield
        self.gate = "open"
        return Result(True, "pushed")

    def options(self, obs):
        return [
            Option("pull_lever", "Pull the lever", self.pull, effect=lambda a, b: b["pulls"] > a["pulls"]),
            Option("open_gate", "Push the gate open", self.open_gate, effect=lambda a, b: b["gate"] != a["gate"]),
            Option("flee", "Run from danger", lambda g, o: (x for x in range(2)), urgent=True),
        ]

    def status(self, obs):
        return Status("done", "through the gate", {"gate": obs["gate"]}) if obs["gate"] == "open" else Status()


class StuckHarness(LeverHarness):
    """The lever is jammed, and pulling it is the only option: nothing advances the plan."""

    stall_seconds = 1.0

    def setup(self, game):
        super().setup(game)
        self.lever_works = False

    def options(self, obs):
        return super().options(obs)[:1]


@pytest.fixture
def game():
    g = Game(FakeLink())
    g.look = {"x": 0.1, "y": 0.1}
    return g


def events_of(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def test_only_the_current_steps_options_are_offered(game, tmp_path):
    jev = RecordingJev("pull_lever")
    path = str(tmp_path / "t.jsonl")
    res = runtime.Runtime(game, LeverHarness(), path, client=jev, max_seconds=10).run()
    assert res["status"]["state"] == "done", res
    assert res["options"]["pull_lever"]["started"] == 1  # (not four times: the step was done)
    assert res["options"]["open_gate"].get("worked") == 1
    offered = [c["names"] for c in jev.calls]
    assert offered[0] == ["pull_lever", "flee"]  # (the step's option, plus the urgent one)
    assert ["open_gate", "flee"] in offered and ["pull_lever", "open_gate", "flee"] not in offered
    steps = [(e.get("from"), e.get("to")) for e in events_of(path) if e["kind"] == "step"]
    assert steps == [(None, "lever"), ("lever", "gate"), ("gate", None)]
    assert res["progress"]["done"] == ["lever", "gate"] and res["progress"]["step"] is None
    assert res["progress"]["stalled"] is None and res["progress"]["t"] > 0


def test_jev_is_told_where_the_plan_stands(game, tmp_path):
    jev = RecordingJev("pull_lever")
    runtime.Runtime(game, LeverHarness(), str(tmp_path / "t.jsonl"), client=jev, max_seconds=10).run()
    first = jev.calls[0]["state"]["plan"]
    assert first["done"] == [] and first["next"] == ["Go through the gate"]
    assert first["now"]["step"] == "lever" and first["now"]["what"] == "Pull the lever"
    assert first["now"]["for_seconds"] >= 0
    later = next(c["state"]["plan"] for c in jev.calls if c["names"][0] == "open_gate")
    assert later == {"done": ["lever"], "now": {"step": "gate", "what": "Go through the gate",
                                                "for_seconds": later["now"]["for_seconds"]}, "next": []}
    assert "plan.now" in jev.calls[0]["instructions"]


def test_a_plan_that_doesnt_advance_ends_the_episode_as_stalled(game, tmp_path):
    path = str(tmp_path / "t.jsonl")
    res = runtime.Runtime(game, StuckHarness(), path, client=None, max_seconds=10).run()
    assert res["reason"] == "stalled" and res["status"]["state"] == "running", res
    stalled = res["progress"]["stalled"]
    assert stalled["step"] == "lever" and stalled["seconds"] >= 1.0
    tried = stalled["tried"]["pull_lever"]
    assert tried["started"] >= 1 and tried["failed"] >= 1 and not tried.get("worked")
    assert stalled["player"]["moved"] == 0 and stalled["player"]["at"] == stalled["player"]["now"] == [0, 0, 100]
    assert res["progress"]["done"] == [] and res["progress"]["step"] == "lever"
    end = next(e for e in events_of(path) if e["kind"] == "end")
    assert end["progress"]["stalled"]["step"] == "lever"
    assert any(e["kind"] == "stalled" for e in events_of(path))


def test_an_option_that_works_is_progress_without_a_plan(game, tmp_path):
    class NoPlan(StuckHarness):
        plan = []

        def setup(self, game):
            super().setup(game)
            self.lever_works = True  # (works every time: never a stall)

        def status(self, obs):
            return Status("done", "enough") if obs["pulls"] >= 4 else Status()

    res = runtime.Runtime(game, NoPlan(), str(tmp_path / "t.jsonl"), client=None, max_seconds=10).run()
    assert res["reason"] == "status done" and res["progress"]["done"] == [] and res["progress"]["step"] is None
    res = runtime.Runtime(game, type("Stuck", (NoPlan,), {"setup": StuckHarness.setup})(),
                          str(tmp_path / "t2.jsonl"), client=None, max_seconds=10).run()
    assert res["reason"] == "stalled" and res["progress"]["stalled"]["step"] is None


def test_the_next_episode_continues_from_the_progress(game, tmp_path):
    first = runtime.Runtime(game, StuckHarness(), str(tmp_path / "t1.jsonl"), client=None, max_seconds=10).run()
    progress = dict(first["progress"], episode=1, done=["lever"])  # (as if the lever had been pulled)
    h = LeverHarness()
    jev = RecordingJev("pull_lever")
    path = str(tmp_path / "t2.jsonl")
    res = runtime.Runtime(game, h, path, client=jev, max_seconds=10, progress=progress).run()
    assert h.resumed == progress  # (resume() was told, after setup())
    assert res["status"]["state"] == "done"
    assert "pull_lever" not in res["options"]  # (the step done before stays done)
    assert jev.calls[0]["state"]["plan"]["done"] == ["lever"] and jev.calls[0]["names"] == ["open_gate", "flee"]
    events = events_of(path)
    continued = next(e for e in events if e["kind"] == "continued")
    assert continued["progress"]["episode"] == 1 and continued["progress"]["stalled"]["step"] == "lever"
    assert next(e for e in events if e["kind"] == "plan")["done"] == ["lever"]
    assert res["progress"]["done"] == ["lever", "gate"]
    text = runtime.summarize(path)
    assert "1. lever: done before this episode" in text and "2. gate: done at t=" in text


def test_the_summary_shows_the_plan_and_the_stall(game, tmp_path):
    path = str(tmp_path / "t.jsonl")
    runtime.Runtime(game, StuckHarness(), path, client=None, max_seconds=10).run()
    text = runtime.summarize(path)
    assert "Plan (the steps in order):" in text
    assert "1. lever: not reached  <- the current step at the end  (Pull the lever)" in text
    assert "2. gate: not reached  (Go through the gate)" in text
    assert "STALLED at step lever for" in text and "tried: pull_lever started" in text
    assert "the player moved 0 units (from [0, 0, 100] to [0, 0, 100])" in text
    assert "run_harness(restart=False)" in text and "Ended: stalled" in text
    done = str(tmp_path / "done.jsonl")
    runtime.Runtime(game, LeverHarness(), done, client=RecordingJev("pull_lever"), max_seconds=10).run()
    text = runtime.summarize(done)
    assert "1. lever: done at t=" in text and "2. gate: done at t=" in text and "STALLED" not in text


def test_a_step_naming_options_that_arent_legal_offers_all(game, tmp_path):
    class Mismatch(LeverHarness):
        plan = [Step("lever", "Pull the lever", lambda o: o["pulls"] >= 1, options=["find_lever"]),
                Step("gate", "Go through the gate", lambda o: o["door"]["open"])]  # (done() raises: KeyError)

        def status(self, obs):
            return Status("done", "pulled") if obs["pulls"] >= 1 else Status()

    jev = RecordingJev("pull_lever")
    path = str(tmp_path / "t.jsonl")
    res = runtime.Runtime(game, Mismatch(), path, client=jev, max_seconds=10).run()
    assert res["status"]["state"] == "done"
    assert jev.calls[0]["names"] == ["pull_lever", "open_gate", "flee"]
    events = events_of(path)
    mismatches = [e for e in events if e["kind"] == "plan_mismatch"]
    assert len(mismatches) == 1 and mismatches[0]["step"] == "lever" and mismatches[0]["listed"] == ["find_lever"]
    errors = [e for e in events if e["kind"] == "error" and e["where"] == "plan step gate done()"]
    assert len(errors) == 1 and "KeyError" in errors[0]["error"]
    assert res["progress"]["done"] == ["lever"] and res["progress"]["step"] == "gate"
    assert "PLAN MISMATCH at step lever" in runtime.summarize(path)


def test_check_tries_the_current_steps_options(game):
    text = runtime.check(game, LeverHarness(), seconds=3, restart=False)
    assert "setup() ran." in text
    assert "options legal now: ['pull_lever', 'open_gate', 'flee']" in text
    assert "plan: at step 1 of 2, lever (Pull the lever); done: []" in text
    assert "tried (the current step's options): ['pull_lever', 'flee']" in text
    assert "\npull_lever: ok" in text and "effect=True" in text and '"pulls": [0, 1]' in text
    assert "plan moved on: at step 2 of 2, gate (Go through the gate); done: ['lever']" in text
    assert "legal now as well: ['open_gate'] (tried next)" in text
    assert "\nopen_gate: ok" in text and "status is done now" in text
    assert text.index("pull_lever: ok") < text.index("\nflee: ok") < text.index("open_gate: ok")


def test_check_without_a_plan_tries_every_option(game):
    class NoPlan(LeverHarness):
        plan = []

    text = runtime.check(game, NoPlan(), seconds=3, restart=False)
    assert "plan:" not in text and "\npull_lever: ok" in text and "\nopen_gate: ok" in text
    text = runtime.check(game, LeverHarness(), seconds=3, restart=False, only=["open_gate"])
    assert "\nopen_gate: ok" in text and "pull_lever: ok" not in text


def test_a_step_that_takes_too_long_though_options_work_is_a_stall(game, tmp_path):
    class Loop(LeverHarness):
        """Pulling works every time, but the step wants a hundred pulls: a loop that never gets the step done."""

        stall_seconds = 0.0
        step_seconds = 1.0
        plan = [Step("lever", "Pull the lever a hundred times", lambda o: o["pulls"] >= 100, options=["pull_lever"]),
                Step("gate", "Go through the gate", lambda o: o["gate"] == "open", options=["open_gate"])]

        def options(self, obs):
            return super().options(obs)[:1]

    path = str(tmp_path / "t.jsonl")
    res = runtime.Runtime(game, Loop(), path, client=None, max_seconds=10).run()
    assert res["reason"] == "stalled", res
    stalled = res["progress"]["stalled"]
    assert stalled["kind"] == "step" and stalled["step"] == "lever" and stalled["seconds"] >= 1.0
    assert stalled["tried"]["pull_lever"]["worked"] >= 1
    text = runtime.summarize(path)
    assert "STALLED at step lever" in text and "though options worked" in text
