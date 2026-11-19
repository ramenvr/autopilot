---
name: autopilot
description: Playtest the game open in the user's Unreal, Unity, Godot or Roblox Studio editor with Autopilot (its `autopilot` MCP server) - explore the game for bugs, test it (go over it and report every problem), verify a claim about how it works or looks, or reach a goal in play. Use when the user asks to playtest, test, verify, check or explore their game, or to use Autopilot.
---

# Playtesting with Autopilot

Autopilot plays the game open in the user's editor: a fast decision model makes the choices in play, inside a
harness you write for this game and this request. You research the game's code, try things in the live game, write
and revise the harness, and report what play showed. Its tools come from the `autopilot` MCP server.

1. The game's project must be open in Unreal, Unity, Godot or Roblox Studio with the Autopilot plugin, its Autopilot tab connected.
   When start_run says Autopilot's agent service isn't running, tell the user that and stop.
2. Call `start_run` with the request in the user's words and its kind. A request is one of four types:
   - an exploration ("Explore the crafting system in Lvl_Main"): `explore`, with `minutes` of play when the user
     gives them;
   - a test ("Go through the currently open level and tell me everything that looks wrong"): `explore` too, as it
     plays as an exploration;
   - a verification ("Verify that the tree shakes when you hit it with an axe"): `verify`;
   - a goal ("Kill the boss while keeping your health above 20% of its maximum value"): `goal`.
   Pass `time_limit_minutes` when the user gives a time limit.
3. Read the INSTRUCTIONS.md it names, all of it, with Autopilot's `read_file` (in parts: it is long). Follow it: it is
   how to work, what the kit is, and the rules a report must meet.
4. A call that answers "still going" (an episode, the game starting) isn't done: call `wait` until it gives the
   result.
5. To look at the game's picture, never call `frames` yourself: hand the run's id, the episode, the times in it
   (seconds from its start, as its run_harness summary's t= gives them) and open questions (nothing about the request
   or what you expect) to the `frame-describer` subagent, as INSTRUCTIONS.md says.
6. If start_run asks for the decision model's key, tell the user where it goes (the file or the Autopilot tab it
   names). Never ask them to paste a key into this conversation.
7. Name in `report`'s `episode` the episode that shows the result best: its recording is the one the user watches.
   Once `report` says "Reported", the run is over: tell the user its outcome in a few lines. The Autopilot tab in
   their editor shows the whole result, with the recording.

## Several requests

When the user gives several requests to go through (a list, a spreadsheet, a CSV of prompts), choose how:

- **Verifications: one after another in one game. This is the default**, whatever the editor, and however many
  requests there are. One game stays open for the whole list; it isn't torn down and started again for each request,
  and nothing else is started beside it.
- **Games side by side** (up to three at once, Unreal only) **only when the user asks for it** in so many words:
  "in parallel", "at the same time", "side by side", "several windows", "three at a time". A long list, a deadline or
  wanting it done quickly is not asking for it: go one after another.
- Goals and explorations each get a run of their own (their game closes when they report): put them before or after
  the verifications, never between two that share the game.

Every request gets its own Autopilot run and its own verdict from that run's `report`, whichever way. When every run
has reported, tell the user each request's outcome in one line, in their order (a table when they gave a table), and
fill in whatever they asked to be filled in (a results column, repro steps) from the runs' reports.

### One game, one after another (the default)

- You choose the order: put requests that build on the same state next to each other (verify reloading right after
  firing, a building's roof after entering it), and the ones that change the game for good (dying, using up a
  resource) late.
- Work through them yourself, one run at a time, with no subagents: `start_run` with `kind` verify and
  `keep_game_open` true for every request but the last, steps 3-7 for it, then the next `start_run`. Don't pass
  `parallel`. Read INSTRUCTIONS.md once, for the first run; the next runs' instructions are the same.
- Every run reports its own verdict with `report`, as one run alone would: never settle a request yourself without
  its run, and never report one run's verdict for another request. Each run's id is its own: pass it as `run`.
- When a run has reported, its game stays open, paused where the run left it, and the next run plays on in it. Its
  briefing shows the game as it is now; its first `run_harness` may go on from there with `restart=False` when that
  suits its request, or restart. Restarts reopen the level in the game playing: in Roblox Studio they respawn the
  player and keep what the world changed.
- The game closes when the last run (the one without `keep_game_open`) reports, or when no run has come for 10
  minutes.

### Games side by side (only when the user asks for it)

Autopilot plays up to three games side by side in the editor (Unreal), one run in each.

- Give each request to a subagent of its own, in the background, with the request, its kind and these steps 2-7 (the
  subagent calls `start_run` itself and works its run to the report); start at most three at once and, as each one
  reports, start the next. Each start passes `parallel` set to the number you keep going (3, or fewer when there are
  fewer requests): the first start of a batch starts that many games side by side; a start while that many runs go is
  refused until one reports.
- Every run has its own id (start_run answers with it): pass it as `run` with every Autopilot call of that run, and
  to its frame describer and episode reviewer. A run's subagent that can't start subagents of its own looks at the
  picture with `describe_frames` (Autopilot's own describer) instead.
- Working several runs yourself instead, keep their games playing together: make each run's `run_harness` (or
  `probe`) call in one message; while several runs go, each answers "still going" within seconds and plays on, so
  every run's episode starts at once. Then call `wait` for each of them, in one message.
- Don't end your turn while a run's subagent is still going: wait for each one's result (a session run without a
  person, `claude -p` or `codex exec`, ends with your turn, and its subagents with it).

## Headless

When the user asks for Autopilot headless (no window, no editor), pass `headless` true with every `start_run`:
Autopilot starts the project's game itself with no window (Unreal, Unity, Godot; not Roblox, whose games play only
in Roblox Studio) and plays every headless run in that one game, one after another, as above (no `keep_game_open`
needed, and no `parallel`). `project` names the game's project when it isn't the one in your working folder. The
first start takes a while (the game starts; say so to the user). A headless game draws no picture: settle requests
from the game's state, and a request about how something looks is INCONCLUSIVE there (tell the user it needs the
editor). Unity opens a project in one editor at a time: a headless Unity game needs the project closed in the Unity
editor.

Use Autopilot's own file tools for the game's folder (paths relative to it), never your own, and don't change the
user's project or the game's files: the game is acted on only through play, with Autopilot's tools. The user can
watch the run in the Autopilot tab, stop it there, or steer it; what they type there reaches you with your next tool
result. A hint leaves the episode playing; a redirect ended the episode there and changes the request from then on.
After either, they likely want play to go on from where it is: prefer run_harness restart=False unless what they said
asks for a restart.
