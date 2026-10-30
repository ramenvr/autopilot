"""The harness contract: what the implementer writes for a game and a prompt, and the runtime (autopilot.runtime) runs.

A harness turns a game into a decision problem Jev can play, as the hand-made Jev harnesses do (Doom: monsters as
distance and bearing, ATTACK/TURN_LEFT/TURN_RIGHT; Tetris: every legal placement described in words; Pac-Man: one
block of facts per legal direction at a junction; Flappy: FLAP/WAIT 20 times a second). The runtime loop:

    over and over:          obs = observe(game)           code reads the game: the facts that matter (in a thread of
                            status(obs)                    its own: a slow observe never holds up the executor)
                                                           done / failed / running (the episode ends when not running)
    at decision points:     options(obs)                   the legal options now, each with its own facts
                            Jev picks one (a Choice over the options' descriptions, given goal + facts + options'
                            facts + what is running + how recent options turned out), plus questions(obs) if any
    every tick (tick_hz):   the chosen option's run() generator advances one step (an executor: controllers from
                            autopilot.kit.control that drive the player's inputs)

Decision points: when no option is running, when the running one finished, when the set of legal options changed,
and every 1/tempo seconds while one runs (tempo=0: never mid-option). A different pick replaces the running option
only if it is interruptible and Jev prefers the new one by `switch_margin` (or the new one is urgent): this keeps
play from dithering. A running option that options(obs) no longer lists keeps going until its executor returns (or
max_seconds): an executor ends itself when its target is gone. A single legal option runs without asking Jev.

Design rules (they are why hand-made harnesses work):
- Options are game-meaningful macro actions (mine the nearest ore vein, carry the crate to the dock, shoot the closest
  enemy, jump the gap ahead), not keys. Make only legal/possible options available (Tetris: reachable placements
  only; Pac-Man: no "walk into the wall").
- Facts are computed by code, in plain words and numbers a player would use (distance, ahead/left/behind, counts
  against maximums, what the backpack holds). Give each option its own facts (Pac-Man's per-direction block). Keep the
  state small: what bears on the choice.
- Describe options in the same words the facts use (Flappy: "upper half of the gap" in both).
- Executors do the fast part in code every tick (aim, steer, time a press); Jev chooses what to do, at its tempo.
- Every option says what should change when it worked (`effect`), so a run shows which options don't work.
- A goal of several things in order (find the key, then open the door) is a plan: ordered Steps (`plan`) whose
  done(obs) predicates read the game's own state (the backpack holds the key; the door's state is open), never what
  options ran. The runtime offers Jev only the current step's options and tells it where the plan stands, so an
  option that stays legal after its step is done (pull the lever once more) isn't taken again by mistake. When
  nothing advances the plan for `stall_seconds` the episode ends as stalled, with what was tried and where the player
  stood; a stalled harness is continued, not restarted: revise the plan's continuation (the steps from here on, or
  an option's approach) and run_harness(restart=False) goes on from where play stands, the steps done staying done
  (resume() is told the progress).
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass
class Option:
    """One thing the player can do now.

    name:          identifier (letters, digits, _), stable across ticks: it's how Jev's pick and the running option
                   are matched. Name it by its role (harvest_nearest_mushroom, attack_closest_enemy), not by
                   an instance (harvest_mushroom_7) that changes as the scene does
    description:   what it does, in words for Jev (the Choice's criterion for it); keep it the same across ticks
    run:           run(game, obs) -> generator: the executor; yield once per tick; return a kit.control.Result (or
                   anything) when finished. It may `yield from` kit.control controllers.
    facts:         facts about this option now (distance to its target, what it would get, how often it worked)
    interruptible: whether a different pick may stop it midway (False for things that must finish, e.g. a jump)
    urgent:        switch to it without the margin when Jev picks it (e.g. defending when hit)
    effect:        effect(before_obs, after_obs) -> True/False/None: did it work (None: can't tell)
    max_seconds:   the runtime stops it after this long (it then counts as failed)
    """

    name: str
    description: str
    run: Callable[..., Any]
    facts: dict = field(default_factory=dict)
    interruptible: bool = True
    urgent: bool = False
    effect: Optional[Callable[[dict, dict], Optional[bool]]] = None
    max_seconds: float = 30.0


@dataclass
class Step:
    """One step of the plan (Harness.plan): a thing the goal needs achieved before the next step.

    name:        identifier (letters, digits, _), stable across ticks: how the plan's progress is kept and reported
    description: what the step achieves, in the game's own words, for Jev (it sees it as `plan.now.what`, and the
                 later steps' as `plan.next`)
    done:        done(obs) -> True once the step is achieved. Read it from the observation, i.e. the game's own state
                 (the backpack holds the key; the door's state is open), not from what options ran: the
                 runtime calls it on every observation, and a step found done stays done for the episode
    options:     names of the options legal in this step: while it is the current step, Jev is offered only these
                 (plus urgent ones). None: every legal option. When none of the named options is legal, every legal
                 option is offered (the player is never stranded) and the mismatch goes to the trace
    """

    name: str
    description: str
    done: Callable[[dict], bool]
    options: Optional[list] = None


@dataclass
class Status:
    """How the episode stands. state: "running", "done" (the goal is reached / the question is settled), or "failed"
    (it can't be reached any more, e.g. the player died in a run that must not die). note: why, in words.
    evidence: values that support it (for verification: what was observed)."""

    state: str = "running"
    note: str = ""
    evidence: dict = field(default_factory=dict)

    @property
    def over(self):
        return self.state != "running"


class Harness:
    """Subclass this in harness.py (one class per file; the runtime takes the last Harness subclass defined)."""

    goal: str = ""  # the goal as Jev should see it, in the game's own terms
    tempo: float = 2.0  # decisions a second while an option runs (0: only when it ends or the options change)
    tick_hz: float = 15.0  # runtime ticks a second (observe + one executor step)
    switch_margin: float = 0.25  # how much more Jev must prefer another option to stop the running one
    instructions: Optional[str] = None  # the Choice's instructions (default: see runtime.DEFAULT_INSTRUCTIONS)
    recent_kept: int = 6  # how many recent option outcomes Jev sees
    start_map: Optional[str] = None  # a map to start each episode in (e.g. the gameplay level of a game that boots
    # into a main menu); None: the level is restarted as it is. setup() can also get through menus itself.
    # The plan: ordered Steps the goal needs achieved one after another (see Step; empty: no plan, every legal option
    # is offered all the time). The class attribute, or set in setup(). The current step is the first not yet done;
    # Jev is offered the options it names and sees `plan` (done, now, next) with every decision
    plan: list = []
    # Seconds of game time the plan may stand still (no step achieved, no option that worked) before the runtime
    # ends the episode as stalled, reporting what was tried and where the player stood. 0: never. Works without a
    # plan too (then only options working count as progress)
    stall_seconds: float = 45.0
    # Seconds of game time one step of the plan may take, options working or not, before the runtime ends the episode
    # as stalled: options that keep working without the step getting done (what they gather is lost again, the wrong
    # thing is counted, done() reads the wrong state) are a loop too. 0: never
    step_seconds: float = 150.0

    def setup(self, game):
        """Once, before the episode (the level is fresh): find the things the harness works with, calibrate. When play
        goes on from where it was (run_harness restart=False), the game is paused through setup() and the first
        observation (reads work; inputs and the game clock don't) and runs again just before the first option's
        first step: a setup that must press inputs or wait for the game then calls game.pause(False) first."""

    def resume(self, game, progress):
        """After setup(), when the episode continues the previous one (run_harness restart=False: play went on from
        where it was, the level not restarted). progress is where the previous episode left the plan: {"episode": its
        number, "done": [names of the steps done then], "step": the step it was at (or None: all done), "t": seconds
        of game time it played, "stalled": why it stalled (step, seconds, tried: what each option did meanwhile,
        player: where it stood and how far it moved) or None, "last_facts": the facts at its end}. The steps in
        "done" start this episode done (the runtime keeps them so). Default: nothing; a harness that keeps state
        between options (a target it was after) can take it up here."""

    def observe(self, game) -> dict:
        """Read the game: return the observation (a dict). Every tick, so keep it to a few reads; use game.batch()
        to read many things in one frame. Keys starting with "_" are kept from Jev (paths, raw vectors)."""
        return {}

    def facts(self, obs) -> dict:
        """What Jev is told about the situation (default: obs without "_" keys)."""
        return {k: v for k, v in obs.items() if not k.startswith("_")}

    def options(self, obs) -> list:
        """The legal options now (Option list)."""
        return []

    def questions(self, obs) -> dict:
        """Extra questions for Jev, asked with each decision: {id: typesafe_sdk.Noul/Score/Choice}; their answers
        go to on_answers (e.g. a Noul "is the player in danger" that code uses to gate options)."""
        return {}

    def on_answers(self, answers, obs):
        """answers: {id: probability (Noul) or {option: probability} (Choice/Score)} of questions()."""

    def status(self, obs) -> Status:
        return Status()

    def idle(self, game, obs):
        """What the player does while no option runs (default: nothing)."""
        yield

    def teardown(self, game):
        """After the episode."""
