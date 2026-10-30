# The harness kit (autopilot.kit)

`from autopilot.kit import *` gives everything below. Units: centimetres (Unreal units), degrees, seconds of game time.

## Game (`game`)

Reads (privileged: any object, any property, protected and Blueprint variables included):
- `game.pawn`, `game.controller`, `game.world`: paths ("" while there is no pawn, e.g. dead). `game.context(fresh=True)` re-reads.
- `game.actors("BP_Door")` / `game.actors("Door")` / `game.actors("/Game/X/BP_Door.BP_Door_C")`: actor paths of a class (subclasses too). A Blueprint's name is resolved from the codex, a C++ class name from the project's modules.
- `game.named("Door")`: actors whose object name contains the text. `game.all_actors()`.
- `game.get(path, "Health")`: one property. `game.props(path)`: all of them. Components: `f"{actor}.MeshComp"`.
- `game.call(path, "FunctionName", Param=value, ...)` -> dict (`ReturnValue` and out params); `game.ret(...)` -> its ReturnValue.
  Static library functions go to class default objects, e.g. `game.ret(GAMEPLAY, "GetPlayerCharacter", WorldContextObject=game.world, PlayerIndex=0)`.
- `game.describe(path)`: its class, properties, Blueprint-callable functions (with their arguments).
- `game.exists(path)`: whether it is in the level now. An actor the game destroyed (picked up, broken, killed) is gone
  at once, though `game.get` can still read it for a moment: ask `exists`, don't wait for a read to fail.
- `game.loc(path)` -> Vec; `game.locs(paths)` -> {path: Vec}; `game.bounds(path)` -> (center, half extent); `game.velocity(path)`; `game.rot(path)`.
- `game.pose()` -> {"at", "eye", "yaw", "pitch", "velocity"} of the player (one frame).
- `game.aim_error(point)` -> (right, up) degrees from the view to a point.
- `game.view(**args)`: what the player sees (visible things with distance/screen position, held items, camera, body:
  on_ground, falling, touching...). `game.view(sonar=True)` adds `body.sonar.ways`: how far the body could walk each way
  (`turn` right of the view, `clear` distance, `by` wall/drop/steep/reach). Costs part of a frame: not every tick.
- `game.sees(paths)` -> {path: {"distance", "screen_x", "screen_y", "crosshair_on" (a bool)}} for those of the actors the player
  can see now (in view, line of sight; screen_x/screen_y -1..1 from the view's center, right/up +): cheap enough for every tick (reflex play: is the enemy visible, is the crosshair on it).
- `game.screen()` -> {widget key: text} of every text on screen (HUD, menus, prompts; UMG keyed
  `<UserWidget>.<TextBlock>`).
- `game.ui()` -> the interactive UI elements on screen now: [{"id", "kind" (button/checkbox/combo/slider/text_input/
  widget), "name", "owner" (its user widget), "label" (the texts inside it, as one string), "enabled", "checked"?, "selected"?, "value"?}].
  `game.ui_act(id=..., action="click"|"right_click"|"double_click"|"set"|"drag", value=..., text=..., option=...,
  to=<id to drop on, for drag>)` acts on one as the player's mouse would (ids are from the latest ui() listing).
  Open menus with their key first (from the input mappings), then list and click.
- `game.batch([lambda: ..., lambda: ...])`: many reads at once, resolved in one game frame; failed ones come back as
  GameError values. `game.map(fn, items)`: fn(item) for each, at once. Use these in observe(): every read is a round trip.
- `game.t`: game time of the latest answer.

Inputs (the player's hands; the only way to act in play):
- `game.press("E")`: a quick press of a key (FKey names: W, A, S, D, E, F, R, Q, SpaceBar, LeftShift, LeftControl, Tab,
  Escape, Enter, One..Nine, LeftMouseButton, RightMouseButton, MiddleMouseButton...) or of an Enhanced Input action path
  (`/Game/Input/IA_Jump.IA_Jump`, value optional).
- `game.hold("W")` ... `game.release("W")`; `game.release_all()`. Holds are leased: the runtime renews them every tick.
- `game.mouse(right_counts, up_counts)`: a mouse flick. `game.turn(right_deg, up_deg)`: turn the view by degrees (uses
  the measured sensitivity; `game.calibrate_look()` measures it, once).
- `game.keys`: movement keys the controllers use, {"forward": "W", "back": "S", "left": "A", "right": "D", "jump": "SpaceBar"}:
  set them in setup() from the game's input mappings if they differ.
- `game.set(path, prop, value, why=...)`: writes state directly. NOT a player action. In a probe it is research (with
  the research helpers: teleport, fight_trial, call_any); in an episode only to set up a verification (e.g. take away
  what only gets in the way of testing). Every write is reported, an episode's apart from research's.
- `game.restart(map=None)`: start the run's level over (the briefing's), or open a map (`/Game/Maps/Level`). For a
  game that boots into a main menu, set the harness's `start_map` to its gameplay level (from the codex's maps.txt /
  config), or click through the menu in setup() (`game.ui()`, `game.ui_act`).

## Controllers (kit.control): generators, `yield from` them inside executors

Each does one tick of control per step and returns a `Result` (truthy when it worked, `.why`, `.data`).
- `walk_to(game, target, within=120, timeout=20, avoid_drops=True, jump_when_stuck=True, run_key=None)`: target is a
  Vec/point, an actor path (its box center, re-read each tick: follows moving things) or a callable. Turns with the mouse,
  holds forward and finds its way: it feels the way with sonar, keeps it on a map of the level (later walks know it)
  and walks the shortest way the map leaves open, planned again when what it feels blocks it (round rocks and walls,
  out of dead ends, through doorways). Jumps when stuck on something it can't feel; won't walk off a drop deeper than
  `max_drop` (300). No waypoints needed. A goal higher than a step up (a platform, an upper floor) it reaches by a way
  up it sees (a ramp, stairs: the view's `ways_up`, where the engine's plugin tells them), else it looks round for one:
  don't write routes of the level's ramps for it. `climb=False`: no ways up. It lets go of forward early by how far
  the body slides (measured in this game) and returns stopped, about `within` from the target. Fails with "stuck" or
  "walk timed out" (`.data`: distance left, the `way` it last planned) when it finds no way: the target likely needs
  a jump (jump_to) or isn't reachable. Fails within about 2 s with "the view doesn't turn" when the mouse has no
  effect (a menu or an interaction holding the mouse): end that first.
- `look_at(game, target, tolerance=None, timeout=1.5)`: turn the view onto a target: by default until the crosshair is
  well inside an actor's box (2.5 degrees for a point), measuring each turn's effect (third-person cameras too). Pass
  a tolerance only to be looser on purpose (a big enemy). Fails fast when the thing is right below the player or past
  the camera's pitch limit: step back from it.
- `track(game, target, seconds)`: keep the view on a moving target (e.g. while holding fire).
- `face_and_press(game, target, key)`, `go_and_press(game, target, key, within=150)`: the "use X" macros.
- `keep_away(game, threat, distance=800, timeout=6)`: move away from a threat along the most open way.
- `movement(game)` -> the character's {"walk_speed", "jump_z", "gravity", "half_height", "peak" (how high a jump
  lifts the feet), "step" (MaxStepHeight), "reach_up" (peak + step: about the highest ledge a jump gets onto), "reach_up_max" (reach_up + 10: a ledge
  between the two is borderline, reached by perching on the edge: try it, never rule it out), "air_control"}, read from its CharacterMovement and capsule.
- `jump_to(game, target, run_key=None)`: run and jump to land on something. target: an actor path (a box, a
  platform, a ledge: lands on its top, taking off so the top of the jump comes over its near edge, then steps in off
  the edge) or a point (the far side of a gap: lands on it). Works out reachability and take-off from movement() and
  the speed the character actually runs at; it backs off for a run-up when it starts too close. ok when the feet are on it.
- `melee(game, target, key, reach=150, cooldown=0.5, alive=None, timeout=12)`: close in and strike every cooldown
  while in reach and aimed, until alive() is false.
- `strike_and_retreat(game, target, key, reach=150, strikes=1, cooldown=0.5, retreat=None, recover=2.0, alive=None,
  health=None, timeout=30)`: hit and run, for a fight a straight `melee` loses (the enemy out-trades the player in
  reach): close in, strike `strikes` times, back off to `retreat` (3 x reach) and wait `recover` (its attack's recovery:
  measure it in the code, don't guess) before closing in again, until alive() is false. `health` (a callable: the
  player's) backs off at once when it drops mid-approach and ends with "player dead" at 0. `.data`: strikes,
  approaches, seconds, damage (taken), hits (that lowered the target's Health); each approach is in the trace ("tactic").
- `aim_and_strike(game, target, key, strikes=1, cooldown=0.5)`: look at the target and press `key` `strikes` times: one
  volley, for tactics of your own that choose when to be in reach.
- `hold_for(game, key, seconds)`, `press(game, key, times=1, every=0.15)`, `wait(game, seconds)`, `until(game, cond, timeout)`.
- `run(gen, timeout=15)`: drive a controller to its end from plain code (`setup()`, `teardown()`), as in probes;
  inside executors `yield from` it instead.
- `sonar(game, turns=(...), reach=1500)`: the sonar ways (see view).
- Write your own controllers the same way when the game needs one (aim leading a moving target, timing a jump off an
  edge, circling an enemy): a loop that reads, acts, `yield`s, and returns a Result.

## Geometry
`Vec(x, y, z)` (`Vec.of(dict)`, `.dist`, `.dist2d`, `.length`, `.unit`, `.flat`, `.rounded()`), `bearing(at, yaw, point)`
(degrees right), `clock(bearing)` (ahead / ahead-left / left / behind...), `forward(yaw, pitch)`, `right_of(yaw)`,
`wrap`, `yaw_of`, `pitch_of`, `where(pose, point)` -> {"distance", "direction", "bearing", "height"} (facts for Jev).

## The harness (kit.harness: read its docstrings)

```python
class MyHarness(Harness):
    goal = "..."            # the goal in the game's own words, as Jev reads it
    tempo = 2.0             # decisions/second while an option runs (reflex play: 4-8; deliberate: 0.5-2)
    tick_hz = 15.0          # observe + one executor step per tick
    switch_margin = 0.25    # how much more Jev must prefer another option to interrupt the running one

    def setup(self, game): ...                 # find things, set game.keys, calibrate
    def observe(self, game) -> dict: ...       # cheap reads each tick; "_"-keys hidden from Jev
    def facts(self, obs) -> dict: ...          # optional: what Jev sees of the situation
    def options(self, obs) -> list[Option]: ...# legal options now, each with facts
    def questions(self, obs) -> dict: ...      # optional extra Noul/Score/Choice -> on_answers(answers, obs)
    def status(self, obs) -> Status: ...       # running / done / failed (+ note, evidence)
    def idle(self, game, obs): yield           # while nothing runs
```

`Option(name, description, run, facts={}, interruptible=True, urgent=False, effect=None, max_seconds=30)`.
`Status(state="running"|"done"|"failed", note="", evidence={})`.

## The plan (a request of more than one step)

A goal that takes steps in order (gather, then make, then use; open, then cross, then reach) is a `plan`: ordered
`Step`s, each done when the game's own state says so, each naming the options legal in it. The runtime offers Jev the
current step's options (plus urgent ones), tells it the plan's standing (`plan.done`, `plan.now`, `plan.next`), ends
an episode that stands still (no step advancing, no option working, for `stall_seconds`; or one step taking
`step_seconds` though options work) as STALLED, and on a continuation (run_harness restart=False) hands `resume()`
the progress so the steps done stay done.

```python
class RepairHarness(Harness):
    goal = "Fetch the part from the shed and fit it to the pump"
    stall_seconds = 45

    def setup(self, game):
        self.part = game.actors("BP_PumpPart")[0]
        self.pump = game.actors("BP_Pump")[0]
        self.plan = [
            Step("get_part", "Pick up the pump part from the shed", lambda o: o["holding_part"],
                 options=["walk_to_shed", "pick_up_part"]),
            Step("fit_part", "Fit the part to the pump", lambda o: o["pump_repaired"],
                 options=["walk_to_pump", "fit_part"]),
        ]

    def observe(self, game):
        holding, repaired, pose = game.batch([lambda: game.get(game.pawn, "HeldItem"),
                                              lambda: game.get(self.pump, "bRepaired"), lambda: game.pose()])
        return {"_pose": pose, "holding_part": holding == "Part", "pump_repaired": bool(repaired),
                "part": where(pose, game.loc(self.part)), "pump": where(pose, game.loc(self.pump))}

    def options(self, obs):      # every option that is legal now: the plan picks the current step's among them
        ...

    def status(self, obs):
        return Status("done", "the pump works", {"repaired": True}) if obs["pump_repaired"] else Status()
```

A done() reads the game (a counter, a flag, what is held), never the options' history; a step with nothing to do
yet (the thing isn't there) is one to get around in the plan, not to wait on. `check_harness()` in a probe says which
step the first observation is at and tries that step's options.

## An example (an invented game, for the shape only)

A game where the player must open a gate by pulling a lever, then escort a cart to town while wolves attack it.
Pulling needs the player within 150 of the lever and the Use key (E, found in the input mapping context); the code
says wolves have 60 health and the sword (left mouse) deals 20 per swing with a 0.6 s cooldown.

```python
from autopilot.kit import *

class EscortHarness(Harness):
    goal = "Open the gate with the lever, then keep the cart alive until it reaches town"
    tempo = 4.0     # fights need quick reactions
    switch_margin = 0.2

    def setup(self, game):
        self.lever = game.actors("BP_Lever")[0]
        self.cart = game.actors("BP_Cart")[0]
        self.lever_at = game.loc(self.lever)
        game.calibrate_look()

    def observe(self, game):
        wolves = game.actors("BP_Wolf")
        pose, lever_on, cart_hp, arrived, cart_at, wolf_at, wolf_hp = game.batch([
            lambda: game.pose(), lambda: game.get(self.lever, "bPulled"), lambda: game.get(self.cart, "Health"),
            lambda: game.get(self.cart, "bArrived"),
            lambda: game.loc(self.cart), lambda: game.locs(wolves),
            lambda: game.map(lambda w: game.get(w, "Health"), wolves)])
        live = [(w, wolf_at[w]) for w, hp in zip(wolves, wolf_hp)
                if w in wolf_at and isinstance(hp, (int, float)) and hp > 0]
        return {"_pose": pose, "_wolves": live, "gate_open": bool(lever_on), "cart_health": cart_hp,
                "cart_arrived": bool(arrived),
                "cart": where(pose, cart_at),
                "wolves_near_cart": sum(1 for _, at in live if at.dist(cart_at) < 800),
                "nearest_wolf": where(pose, min((at for _, at in live), key=lambda a: a.dist(pose["at"]))) if live else None}

    def options(self, obs):
        opts = []
        if not obs["gate_open"]:
            opts.append(Option("pull_lever", "Walk to the lever and pull it to open the gate",
                               lambda g, o: go_and_press(g, self.lever, "E", within=140),
                               facts={"lever": where(obs["_pose"], self.lever_at)},
                               effect=lambda a, b: b["gate_open"]))
        if obs["_wolves"]:
            opts.append(Option("fight_nearest_wolf", "Attack the wolf nearest to the player with the sword",
                               self.fight, facts={"wolf": obs["nearest_wolf"]}, urgent=True,
                               effect=lambda a, b: len(b["_wolves"]) < len(a["_wolves"])))
        opts.append(Option("stay_with_cart", "Walk beside the cart as it travels",
                           lambda g, o: walk_to(g, self.cart, within=250, timeout=8), facts={"cart": obs["cart"]}))
        return opts

    def fight(self, game, obs):
        wolf = min(obs["_wolves"], key=lambda w: w[1].dist(obs["_pose"]["at"]))[0]
        r = yield from walk_to(game, wolf, within=170, timeout=6)
        if not r:
            return r
        for _ in range(4):                       # 3 swings kill at full health; one spare
            yield from look_at(game, wolf, tolerance=6, timeout=0.5)
            game.press("LeftMouseButton")
            yield from wait(game, 0.65)          # the cooldown from the code
            if not game.exists(wolf) or game.get(wolf, "Health") <= 0:
                return Result(True, "wolf down")
        return Result(False, "wolf still standing")

    def status(self, obs):
        if obs["cart_health"] is not None and obs["cart_health"] <= 0:
            return Status("failed", "the cart was destroyed", {"cart_health": obs["cart_health"]})
        if obs["cart_arrived"]:      # the cart's own flag, found in BP_Cart's graph (set when it reaches town)
            return Status("done", "the cart reached town", {"cart_health": obs["cart_health"]})
        return Status()
```

## What makes a harness play well

- Read the code for the exact rules: what input does what, ranges, cooldowns, what a pickup or a hit needs (aim? an
  item in hand? a mode?), what counts as done. Put those numbers into executors (cooldown waits, approach distances).
- Then check them in the live game (probe) before relying on them: press the key, read what changed.
- Facts in the player's terms: distances, directions, counts against limits, what is held, what is in danger.
- Few options (2-8), each a whole meaningful step, each only when legal. A plan for a multi-step goal goes in the
  facts (what is done, what is next by the game's rules) so Jev can follow it; it is not a script.
- Reflex games: higher tempo, short executors that aim/steer in code every tick, urgent options for threats; a hit
  to dodge or block is a reflex in code (see Reacting in time).
- Every option gets an `effect`, so the trace shows which ones don't work and why.
- Using a thing by the crosshair (pick up, open, talk to): walk to inside the game's use range but not on top of it
  (a thing on the floor needs 1-2.5 m: standing over it, a third-person camera can't see it), `look_at` it with the
  default tolerance, check the game's own sign that it is targeted (`game.sees([thing])[thing]["crosshair_on"]`, or
  the game's focus/target property from the code) and only then press.

## Reacting in time (dodging, blocking, jumping)

A decision by Jev takes about 0.2-0.3 s from what it saw to what the player does: too slow for a hit that lands a
fraction of a second after it shows. Reactions like that go in executor code, which runs every tick:

- Find the threat's tell in the code, the earliest sign that the hit is coming: the attacker's current animation and
  how far into it it is (an animation montage and its position, an animator state's time, an animation player's
  position), the stretches of it in which a hit lands (animation notifies or events, a damage window), a
  projectile's position and velocity, a charge-up flag. Turn it into seconds until the hit.
- Read the player's own move in the code: how long it protects (invincibility frames, a guard window) and from when,
  how long it lasts, what it costs (stamina, a cooldown), whether it cancels an attack of the player's own.
- Every tick, read the tell (one `game.batch`) and make the move once the hit is less than a lead away: the time from
  the press until the protection starts, plus the step time (the summary's "Executor steps"). Measure the lead in
  play (hits taken against moves made), don't guess it: it changes with the frame rate.
- React once per hit (remember which hit it was) and not again before the move's recovery ends: a reflex that fires
  for as long as a threat lasts leaves the player dodging and never attacking. Attack in the openings, when the next
  hit is further off than an attack takes.
- Put the reflex in every executor that runs near the threat (one helper in lib.py), so the player defends whatever
  Jev picked; Jev's choices are what reflexes can't decide (close in or back off, heal, which target).
- Record each hit taken with the threat's tell at that moment, so the trace shows which hits still land and whether
  the lead is early or late.
- tick_hz up to the game's frame rate for reflex executors (no gain above it: every read waits for a frame).
