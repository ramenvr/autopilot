### A claim to verify: test it in play and measure

1. **Say what true and false look like** before anything else: the observation that would confirm the claim and the
   one that would deny it, in the game's own numbers (or, for a claim about how something looks, in visible
   features). Write them in the harness's Status.evidence.
2. **Find the mechanic in the code** (where the claim's words appear; the input that drives it, its conditions and
   numbers), then check it once in a probe: do the step, read what changed.
3. **Write a harness that puts Jev in the claim's situation** by play, with 2+ sensible options at each decision,
   and `watch_questions` for what only the picture tells (an effect, an animation, a state the numbers don't name).
   Measure in observe() and record in evidence. `check_harness()`, then `run_harness`.
4. **After each episode**: did the situation the claim is about happen? If not, what kept play from it (the
   summary's options, the picture's death causes, a stall): fix that, continue (`restart=False`) and play again. If
   it did, is the measurement decisive? A claim about how something looks is settled only by a description of the
   frames (describe_frames, open questions, held against step 1).
5. **Report** CONFIRMED or DENIED on what play showed (the trace and times, the description), INCONCLUSIVE when it
   couldn't be made to happen or the measurement isn't decisive, saying what stood in the way. A failure of the
   harness to do something is never a denial. Update notes.md with what was verified.
