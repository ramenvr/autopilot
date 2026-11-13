### An exploration: cover the mechanics, note what looks wrong

1. **Map what there is to try** from the briefing and the codex (the mechanics the request names, their inputs,
   limits, edge cases) and one probe to see the level as it starts. List the things to try in notes.md as you go.
2. **Write a harness whose options cover the mechanics broadly**, preferring what hasn't been tried (Option facts
   say how often each has run), with `watch_questions` for what the picture shows (an effect that should play, a
   counter on screen) and detectors' findings read from the summary. Keep the first episode short.
3. **After each episode**: what was tried and what came of it; the game's own detector findings and error log; the
   picture's death causes and where the player looked stuck (a place the player gets stuck for no game reason is
   a finding; a hazard that kills from further than its look suggests is one too). Add options for what wasn't
   reached; remove ones that only repeat.
4. **A finding is what the game did wrong**, with the observation (values, times, the trace): never what the harness
   couldn't do. When the game's numbers prove something impossible, say which numbers.
5. **Spend the minutes of play playing**; report as they run out, with the findings and what was covered.
