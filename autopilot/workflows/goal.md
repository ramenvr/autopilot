### A goal: make it happen in play, fast, and learn from every episode

1. **Know the chain before the first episode.** From the briefing (notes.md, lib.py, earlier runs) and one or two
   probes: what the goal needs step by step, which inputs do it, what kills or stops the player, where the targets
   are. Prove the last step once in a probe. Reuse lib.py where it fits; don't rewrite what worked.
2. **Write the harness as a plan** (kit.Step per thing the goal needs, done() from the game's own state), options
   that are whole meaningful moves, executors from the kit's controllers, every move timed by the game's clock, a
   move that must land on a frame handed to the game (`jump_at`, `moveset`). Add `watch_questions` for what the
   picture tells and the numbers don't. `check_harness()` in a probe, then a short first `run_harness` (20-60 s).
3. **After every episode, read the summary in this order**, and let each reading decide the next change:
   - *Where the plan got* and the status: the next step to make work.
   - *What the picture showed*: each death's cause (hazard, fall, enemy, moving object) with its sentence, and
     when the player looked stuck. A cause that shows twice is the next change: a wait for the hazard's clear
     moment (its schedule from the code), a jump the game times, another way round, a different spot to stand.
   - *Options*: one that failed most, and why; a "held back" or "timed out" one is a harness bug to fix now.
   - *Executor steps*: slow steps (many waits a step) make a reaction late: batch the reads.
4. **One change at a time, then play again.** Continue from where play stands (`run_harness(restart=False)`)
   while the state is good (alive, on the course, the plan's steps done stay done); restart when the state is lost
   or setup is wrong. Make each episode long enough for the plan's next step to show, not longer.
5. **Never run the same harness unchanged after two episodes that ended the same way.** If the picture and the
   numbers disagree about a death (the numbers say "fell", the picture says "hit a beam"), the picture wins:
   describe_frames the moment if the sentence isn't enough, then fix the cause.
6. **Report when the goal is reached** (that episode is the evidence) or when more tries won't help, saying what
   was learned. Before that, put what later requests need into notes.md (facts, numbers, pitfalls) and the reusable
   executors into lib.py.
