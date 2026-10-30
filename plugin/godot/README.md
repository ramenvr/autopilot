# Autopilot for Godot 4

Autopilot is the Godot addon of Autopilot. It lets the Autopilot agent play your Godot 4 game. A prompt is one of four
types:

- **Exploration**, e.g. "Explore the crafting system in Lvl_Main.": it plays for a few minutes and reports what looks
  wrong;
- **Testing**, e.g. "Go through the currently open level and tell me everything that looks wrong.": it plays as an
  exploration, and its report says GAME TESTED;
- **Verification**, e.g. "Verify that the tree shakes when you hit it with an axe.": CONFIRMED, DENIED or INCONCLUSIVE,
  with evidence;
- **Goal**, e.g. "Kill the boss while keeping your health above 20% of its maximum value.": achieved or not.

For every prompt, Autopilot starts the game for you (as the Play button does), reads your project's code, tries things
in the game and plays according to your prompt. When it is done, the game stops by itself and the dock shows the report.

The addon is the agent's eyes and hands inside the game: it answers questions about the game (what is on screen, where
things are), presses keys and clicks UI the way a player would, and gives the agent your project's scripts and scenes
to read. The agent itself runs on your machine as a separate Python program (`python -m autopilot serve`, see
[Running from a clone](../../CONTRIBUTING.md#running-from-a-clone)); the addon connects out to it over a WebSocket.

It is written in GDScript only, so it works in the standard (non-.NET) build of Godot, in the editor's play sessions
and in exported builds. It needs **Godot 4.7 or later** and is tested with Godot 4.7.2.

## Install

1. Unzip the download and move its `addons/autopilot/` folder into your project's `addons/` folder (from a repository
   checkout: copy `plugin/godot/addons/autopilot/`; keep the folder name).
2. Open your project. In **Project > Project Settings > Plugins**, enable **Autopilot**. This:
   - registers an autoload named `AutopilotPlaytest` (`res://addons/autopilot/playtest.gd`);
   - gives the project a stable id, `autopilot/project_id` in `project.godot` (a GUID: the agent keeps what it
     learns about your game under it).

   Commit both changes to `project.godot`.
3. The **Autopilot** dock appears on the right. You can move it to any dock slot, including the bottom panel.

Until something connects it, the addon does nothing: it opens no port and never listens. It only ever connects out,
to the address you give it.

## Use it from the editor

The first time, the dock shows a **first-run setup** in place of everything else (see [First-run setup](#first-run-setup)):

1. We recommend using Autopilot with your Aura account: click **Create Account** if you don't have one yet, or
   **Sign in** if you do (or bring your own keys: BYOK).
2. Autopilot sets up the agent service, then opens Aura's sign-up or sign-in page in your browser: finish there.
3. Once you are signed in, the dock shows **Agent service ready**: click **Continue**.
4. Choose whether to share your runs to improve Autopilot (on by default).
5. Optionally, when Autopilot doesn't know your game yet, run a first exploration (10 minutes by default) so later runs
   start knowing it. It is safe to skip this and send your own prompt straight away.
6. Prompt Autopilot (one of the four types above).

Using it from then on:

1. **The agent service** runs the agent on your machine. The dock starts it itself when it connects (see
   [The agent service](#the-agent-service)): `autopilot serve --port 0 --exit-with <the editor's process id>`, which says
   where it listens in `Autopilot/brain.json` and ends with the editor. Or start it yourself (`autopilot serve`, or
   `python -m autopilot serve` in the Autopilot repository): the dock finds it. Set up to use the Aura app (step 1 of
   the [first-run setup](#first-run-setup)), the Aura app runs it.
2. **Connected by itself**: the dock connects as soon as the project opens (once the first-run setup is done; never in
   a headless editor). While it isn't connected the dock shows the connect card: "Agent service", **Not connected**,
   how to start it, and what is going on (connecting, starting or installing it, what went wrong) with its buttons
   (with the Aura app: **Get Aura**, its download page, when Aura isn't installed). Disconnected after a run was shown,
   **Back to the last run** under the card goes back to it. The New run page's foot shows `● CONNECTED`, the address
   and **Disconnect** together on one row at its right (never split onto two lines); **Disconnect** stops connecting
   for this editor session, and **Reconnect** (there and on the card) connects again.
3. **New run**: describe what to test in the one prompt box ("Describe what to verify, reach or explore") and press
   **Run**. The words are sent as typed (a `start` frame whose run is `{"kind": "auto", "prompt": ...,
   "time_limit_minutes": ...}`, the protocol's "One prompt box"), and the agent service's decision model reads which of
   the four types it is: an exploration for bugs, a test, a claim to verify, or a goal to play. The dock reads nothing
   from them: the run shows no kind until the agent service says which (its `run_kind` frame, before the game plays),
   then that kind (VERIFY, GOAL, EXPLORE). An exploration asked for as a test of the game in your own words
   ("Lvl_HordeArea test this game and find all the bugs...") ends **GAME TESTED** rather than EXPLORED, in the same
   colours.

   The **Time limit** box at the right of the box's last row, beside **Run**, is the run's minutes in all (an
   exploration's minutes of play), 1-600, 30 by default. It is sent with the run as `time_limit_minutes` and kept per
   user and project as you change it (the editor's project metadata), so the next run starts with it. Run again sends
   the run's own Time limit.
   Runs are real time: the game keeps running while the agent decides.

   When your Aura team is out of credits (the `account` frame's `out_of_credits`), Aura's words show in red directly
   over the prompt box, with **Add credits** (it opens the page Aura names; no button when it names none). **Run**
   stays enabled: Aura checks again as a run starts. The notice goes once an `account` frame comes without it.

   The game plays the main scene (as the Play button does), unless the prompt means another scene ("Explore the
   harbor scene", "in the 2D one", `res://levels/harbor.tscn`): the agent picks it from the project's scenes before
   it starts playing. Every restart of the run plays that scene again.
4. **Follow the run**. The agent starts the game itself (as the Play button does), restarts it when it needs a fresh
   start, and stops it at the end. While it plays, the dock shows:
   - the stages (launch, analyze, probe, build the harness, rollout, process the result, report; a name too long for its
     column wraps, and in a narrow dock the names go on rows of their own under the track: never cut) and what the
     agent is doing now. "Saving what it learned to memory", after the agent finishes, is part of the report;
   - the **Feed** tab: the run as a transcript grouped by stage: a heading per stage (Launch, Analyze, Probe, Build
     harness, Play, Results, Report) with its count of lines, and under it what the agent did and said, one line per
     event of the agent service's activity feed (the protocol's `activity` frames): its time (m:ss), a small mark for
     its kind (? the agent's words, R read, S searched, W wrote, P tried in the game, E episode, L looked, ! report or
     alert, > what you steered, * restart, M the game's memory refined), its words and, for a call that came back (a
     probe, an episode, a look), the result's words on the same line in the muted colour. Under a line, a fold for each
     detail ("+ the code", "the output", "what was written", "the summary", "the edit and its evidence", "the whole
     message"…; click it) opens a mono block with it, at most
     320 px tall, scrolling past that, its text selectable. The status lines, engine alerts, what you steered and the
     result are among them, each under its stage; an agent service too old to send activity frames gives the status
     lines alone. Last, when the run added to what the agent remembers about the game (the result's `memory.updated`),
     "Memory updated". The stage track follows the frames' stages (the status lines for an older agent service). The
     feed is kept with the run, so a run reopened from the history shows it whole; a dock that connects again while a
     run goes asks for the events it missed (`activity_since`), and an event sent twice is shown once;
   - the **Policy log** tab: every step of the agent's policy (the action it chose, its probabilities, what changed in the
     observation), filtered by harness, by tag and by **Find an action…** (the action's name); pick a row (or step with
     ◀ ▶ beside **Copy step JSON**) to pause on it ("PAUSED ON H2 #18"), **Follow live** to follow again; its footer sums
     up the steps of the harness shown ("H2: …": the harness filtered, or the picked step's; "All harnesses: …" with All
     and no step picked);
   - a **Steer** box at the page's foot ("Send a steering prompt to Autopilot"): type a hint ("go round the rocks to
     the left first") and the agent takes it into account from where the game is. You can type at any stage; the
     **Steer** button sends only while the game is playing (the Rollout stage): before that it is disabled ("You can
     steer once the game is playing") and what you typed is kept until it can be sent. In a narrow dock the **Steer**
     button goes under the words. The **Feed** tab has the same box at its foot while the run goes on (one box: what
     you type shows on both).

   **Stop** ends the run early ("Stopping…", the steer box locked); the agent still reports.
5. **Read the result**: the verdict, the agent's reason and evidence, the run's recording, its findings and the engine
   alerts (script errors, NaN transforms, falling out of the world...). Three icon buttons at the right of the run's id
   line (`RUN <id> // VERIFY // OCT 20 13:25`, just above the prompt), at every width, each named by its tooltip:
   **Run again** (a circular arrow, filled in the accent; greyed while another run is going or not connected), **Copy report**
   (outlined, a clipboard: the report as markdown, its "Duration:") and **Run folder** (outlined, a folder: `result.json` and the report files the agent service sends, saved under
   `user://playtest/<time>/`; disabled when the folder isn’t there). From there you can also mark an alert **Not a bug**, **Show in level** (selects the nearest node in the open
   scene) or **Verify a fix** for a finding (it fills the prompt box: "Verify that this is fixed: ...").

   **The recording**: when the agent service recorded the run (the result's `recording`), it plays under the verdict as
   a flipbook of its JPEG frames (Godot plays no MP4): a picture as wide as the page and 230 px tall (200 in a narrow
   dock; letterboxed, a play mark on it while paused), a play / pause button (its icon), a 2 px scrub bar, the time and
   the length, and a button with Run folder's icon (the folder) ("Show in File Explorer", "Show in
   Finder" or "Show in folder": the agent service's MP4 selected in the file manager when it is there, else the frames'
   folder). Nothing plays until you press play; only a few frames around the playhead are held. The frames are
   copied into the run folder (`recording/`, off the editor's thread; `result.json` then names that folder and keeps
   `frames_fps`), so a Reset memory doesn't take them; until the copy is done, or if it fails, they play from where the
   agent service wrote them. A run without a recording shows no player: in its place a muted line says why, when that
   is known: `No recording: <why>`, with the reason the agent service gave (the result's `no_recording`, e.g. "the
   game can't be recorded here: it runs without rendering"), or, for a result with neither `recording` nor `memory`,
   "an older agent service ran this." It is decided
   when the result arrives and kept in the run's `result.json` (`no_recording`). A recording whose frames are gone
   says "The recording isn’t there any more."

   The findings (each agent finding tagged **Found**, then "Engine findings · N") show only when the run has some: no
   heading otherwise. The tabs are **Result** (**Live** while it goes on), **Feed** and **Policy log · N**, with the
   run's id line at the right when the dock is wide: `RUN <id> // GOAL // DURATION 03:26 // 2 ATTEMPTS` (then
   `● LOCAL AGENT` while the run goes on with the agent service on this computer). An engine finding's tag is its
   severity (red for error, critical, fatal or bug; amber for warning; blue, `INFO`, otherwise); in the feed, the
   engine's findings follow `ENGINE` and the agent's `AGENT` (red). The Live page's feed shows the last 10 lines, the
   Feed tab all of them ("Nothing yet." while there are none).

The **History** lists the runs, newest first (a 280 px side rail when the dock is wide, its rows alternating
two grounds; when it is narrow, a button named "History" that opens the list in a floating panel, the Memory line at
its foot; the run shown has a warm dark fill and a 2 px amber rule on its left; each run's line says when it ran, how
long it took and its harness attempts: "Oct 19 13:25 · 03:26 · 2 attempts"; the narrow button, with no run shown,
says **New** and "12 runs", or "No runs yet"). The last 50 runs are kept in `.godot/editor/autopilot_runs/` (one
JSON file each; per user, not committed), so closing the dock or the editor loses nothing: a run from an earlier
session shows its result, feed, findings and recording as they were, but not its Policy log (its steps aren't kept), and
a run still going when the editor closed shows **NO RESULT**, "The editor closed before the result came.". The gear beside **New run** (its tooltip
"Settings"; amber on a dark fill while open) opens and closes the [Settings page](#settings-page), whose Account card
has **Rerun setup**. The **Memory** line shows how many runs the agent remembers of this game ("· 12 runs", or "· empty");
**Reset
memory…** asks in place, in the Memory row ("Reset memory?", "The agent forgets what it has learned about this game.",
**Reset memory** and **Cancel**; no dialog) and then has it forget them (`clear_memory`): "Resetting memory…", then
"Memory reset successful" (the line says "· empty") or "Memory not reset: <why>". A reset also takes the runs before it off
the history: their files move to `.godot/editor/autopilot_runs/.reset-backup/<time>/` (kept, never listed again; the
Settings page's usage keeps them). While a run is
going (or not connected) the button is greyed ("A run is going: reset memory once it is done"). The toolbar badge (the Autopilot logo)
opens the dock.

The **status line**, the main column's last row on every page (over a 1 px rule, one line, its whole text in its
tooltip), says what your own action did or why it couldn't ("Copied the report", "Copied", "Told the agent: <name> is
not a bug", "Not connected: connect first"...); it goes after 8 seconds (or at the next message) and takes no room while
empty. A button that can't act now because of the state (**Run** and **Run again** while not connected or a run is
going, **Not a bug** while not connected, **Reset memory…** while not connected or a run is going) is greyed but can still
be pressed: the status line then says why ("Not connected: connect first", "A run is going: wait for its result, or stop
it", "Not connected: connect to send feedback", "Not connected: connect first to reset the memory", "A run is going:
reset the memory once it is done"). **Run** with an empty prompt box stays disabled. The agent service's progress lines are on the Live and Feed pages, never there.

Every page of the dock follows its size: rows of buttons go onto more lines when it is narrow, text wraps, a page
taller than the dock scrolls (never sideways), and in a wide dock the pages keep their content at most so wide,
centred: the composer 620 px, the Settings page 640, the setup 600, the connect and removal pages 540 (the result
takes the page's width). The first-run setup's steps all take the same column (the dock's width, less the scroll
bar's room, up to 600 px), whatever each one shows and whether it scrolls. The history is a side rail from 700 px wide.

The look is Aura's (The Flatness, dark only): IBM Plex Sans (400, 500, 600) and IBM Plex Mono (400, 500), bundled in
`fonts/` with their licences (JetBrains Mono stands in for the symbols they lack); one amber accent (#f0873a); flat
surfaces with 1 px rules; 8 px corners on plates (cards, the feed, the prompt and steer boxes, the recording, menus)
and square controls (buttons, tags, inputs, tabs), the switch a pill; no shadow but on floating panels, no gradients.

### Settings

Open **Advanced** on the dock's connect card (shown while it isn't connected). A changed address or token is used
at once (Enter, or leaving the field): the dock connects again with it.

| Setting | What it does |
|---|---|
| **Agent service URL** | Where to connect. Empty means `ws://127.0.0.1:8765/v1/bridge`, the agent service on this computer, which the dock starts when none is running. Use `ws://<host>:<port>/v1/bridge` (or `wss://...`) for one you run elsewhere (the dock then starts none). |
| **Access token · optional** | When set, it is sent as `Authorization: Bearer <token>` when connecting, for an agent service that asks for one. Empty: no token is sent (unless `AUTOPILOT_PLAYTEST_TOKEN` is set). |
| **Agent service command · optional** | Empty by default: the agent service the addon carries, installed where its installer puts it (`%USERPROFILE%\.local\bin\autopilot.exe` on Windows, `~/.local/bin/autopilot` elsewhere; a repository checkout, which carries none, looks for `autopilot` on `PATH` first). Set, it is used as it is: never installed over, and an agent service running is never replaced. Or a full command line without `serve`, e.g. `C:\autopilot\.venv\Scripts\python.exe -m autopilot` (an Autopilot repository's environment). |
Using the Aura app (**Use Aura app** in step 1 of the first-run setup; **Rerun setup** changes it), Autopilot connects
to the agent service the [Aura](https://www.tryaura.dev/) app runs on this machine (the address it announces in
`Autopilot/aura-brain.json`) instead of the URL above: runs then go through Aura, on your Aura plan, and what the agent
learns about each game is kept with Aura across your team's machines. The connect card then says "Start the Aura app:
it runs the agent service.", with **Start Aura** while Aura isn't running. Otherwise Autopilot never connects to Aura's.

These are kept per user and per project in the editor's project metadata (`.godot/editor/project_metadata.cfg`), never
in `project.godot`, so they are not committed.

### First-run setup

Shown in the dock until `Autopilot/preferences.json` says `"onboarded": true` (never in a headless editor), and again
from **Rerun setup** (on the Settings page's Account card), from its first step. A stepper at the top: **1 Account**, **2 Agent service**,
**3 Privacy settings** (a step done shows its choice: `1 ACCOUNT · AURA`, `· AURA APP`, `· BYOK`) and **4 First run**,
unless the agent service has said (its `memory_state`, on the connection open now) that it remembers something about
this game. When the computer was set up before but its agent service is gone (none running, none installed: e.g.
`uv tool uninstall autopilot`), and the dock starts the agent service itself (no **Agent service command**, not the
Aura app's, the default address, not headless), the editor's start opens
the setup at step 2 instead of the connect page, the account as chosen then, so that **Install** shows.

1. **Account** ("Welcome to Autopilot"):
   - **Sign in with Aura** ("Autopilot is maintained by the developers behind Aura, the multiagent AI solution to Unreal and Unity game development. You can use your Aura subscription to run Autopilot. New users get free Aura credits."): billed to your Aura plan, signed in through your browser. The dock gets the
     agent service running (installing it first when needed: "Setting up the agent service…", then "Starting the
     agent service…") and asks it to sign in (`aura_sign_in`, the protocol's "Account"): the agent service opens
     Aura's page in your browser and keeps the sign-in for the computer; the addon never sees Aura's tokens. While it
     waits: "Finish signing in in your browser…", the page's address, **Open again**, **Copy link**, **Cancel**. Then
     "Signed in as *email*", **Continue** (to step 2) or **Use another account**; or what went wrong in one line, and
     **Try again**.
   - The card has two buttons, the Aura app installed or not: **Sign in** signs in as above. **Create Account** (the
     default) opens Aura's account creation page in your browser, and the card then says "Finish creating your Aura
     account" and "Once you have created your account, sign in and start using Autopilot", with **Sign in** (as above).
     The page is on the Aura server the agent service signs in to (`AUTOPILOT_AURA_SERVER` in autopilot.env, else the
     download's), else https://app.tryaura.dev.
   - **Bring your own keys (BYOK)**: a **Decision provider API key** (e.g. TypeSafe), the **Coding Agent** (**Claude
     Code**, **Codex** or **OpenCode**) and an **LLM provider API key**, its provider in the dropdown beside it: with
     Claude Code it shows **Anthropic**, locked; with Codex **OpenAI**, locked; with OpenCode any of **Anthropic**,
     **OpenAI**, **OpenCode Zen**, **OpenRouter**, **DeepSeek** and **Gemini** (switching back to OpenCode brings back
     the provider last picked for it). Both keys are optional ("Optional": leave them empty if they are set where you
     run the agent service). The Coding Agent and OpenCode's provider start at what the agent service's `account` frame names
     (`coding_agent`, `llm_provider`), else what `autopilot.env` names, else OpenCode with Anthropic (an unknown agent is
     OpenCode, an unknown provider Anthropic). **Use my keys** writes to `Autopilot/autopilot.env` the Coding Agent
     (`AUTOPILOT_AGENT=claude`, `codex` or `opencode`, always), with OpenCode its provider (`AUTOPILOT_OPENCODE_PROVIDER=`
     `anthropic`, `openai`, `opencode`, `openrouter`, `deepseek` or `google`; for Claude Code and Codex that line is
     kept) and the keys you typed (`TYPESAFE_API_KEY=...`, and the LLM key under the shown provider's name:
     `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `OPENCODE_API_KEY`, `OPENROUTER_API_KEY`, `DEEPSEEK_API_KEY` or
     `GEMINI_API_KEY`; a key left empty keeps its line, other providers' keys are kept, other lines are kept, the same
     key is replaced) and stops using the Aura app's. While the sign-in with Aura gets ready, waits in the browser or
     needs the agent service, this card isn't shown (**Cancel** brings it back). Otherwise it is folded ("Bring your
     own keys (BYOK)", **Show**; **Hide** folds it again), and dimmed once signed in to Aura.
2. **Agent service** ("Set up the agent service"): found running, or started ("Agent service ready", "Starts with your
   editor", its address, **Continue**); "Not installed on this computer" ("One-time installation takes approximately 1
   minute."):
   **Install** or **I’ll do it later**; with no installer (a repository checkout) "No agent service found", the
   **Agent service command** field with **Start**, or "Or run `autopilot serve`" (**Copy**); what went wrong, with
   **View logs** and **Try again** ("The agent service didn’t start.", what it said under it); started by
   nobody (a headless editor), "Waiting for the agent service: start it with autopilot serve." with **Continue** once it
   is connected.
3. **Privacy settings**: **Share my runs to improve Autopilot** (a switch, on by default: "Enabling this sends
   recordings, screenshots, and agent traces and memories to the developers of Autopilot."), and a recap of steps 1
   and 2 (the agent service "Ready" once it is).
   **Next** (**Finish** when the agent service has said it remembers something about this game) writes
   `preferences.json` (`access` as chosen, unless you decided later), keeps the Aura app's choice, connects and asks the
   agent service for the account again (the `account` frame sent at the sign-in came before `preferences.json` said
   `"aura"`). With **Finish** the dock shows; with **Next**, step 4.
4. **First run** ("Let Autopilot learn your game"): it waits for the connection and its `memory_state` ("Connecting to
   the agent service…", "Checking what Autopilot remembers about this game…", **Skip**). When the game's memory has
   something (past runs, notes or code of its own) the setup closes by itself. When it is empty, an Explore run is
   offered: "Explore your game" (**Recommended**: "You've never used Autopilot with this game before. To help Autopilot
   understand your game, use this first exploration run and watch it improve. Autopilot still works if you skip this
   step."), a **Minutes** box (the composer's, 10 by default), **Explore and finish** (starts the run as the composer
   does for "Explore", closes the setup and shows the run live; only when connected and no run is going) and **Skip and
   finish**.

### The agent service

The dock starts the agent service when it connects to the default address (always; not with the Aura app's, and never
in a headless editor, which is automation):

- a release download carries its own agent service (`addons/autopilot/agent_service/`: the wheel, `install.ps1`,
  `install.sh`, `agent-service.json` naming its commit and sha256, and a `.gdignore`), and that one always runs: putting
  a newer addon in the project is all an update takes. Unless **Agent service command** is set (then that command runs,
  never installed over nor replaced):
  - an agent service `Autopilot/brain.json` names, still running, is used when its `commit` is the addon's
    `agent-service.json`'s: several editors share one, and the dock never starts a second. One that names another
    commit, or none (from before), is stopped, but only when its program is an agent service's (`autopilot`, or the
    Python uv's tool runs it under) and it is the process that wrote `brain.json`: it started no later than the
    file's `started` + 5 s (a process started later got the pid of one that ended: it is never stopped). Without
    `started`, or when the process's start time can't be read, it isn't stopped either (the log says why). Stopped,
    `brain.json` is removed once it has ended, the log says `[Autopilot] An older agent service was running (pid N):
    replacing it with this plugin's`, and the addon's starts as below. Once per editor
    session (if it comes back, or won't stop, the one running is used, and the log says so), and never while the dock
    is connected to it: a connection already open is left alone. Any other program is left running and used;
  - otherwise the installer's place (`%USERPROFILE%\.local\bin\autopilot.exe` on Windows, `~/.local/bin/autopilot`
    elsewhere) is started. When `Autopilot/agent-service.json` (which the installer writes) names another agent
    service than the addon's (by sha256), or the installer's place is empty but an `autopilot` is on `PATH`, the addon's
    installer runs first (once per editor session; if that fails, the installed one is started anyway). An `autopilot`
    elsewhere on `PATH` is not used. When nothing is installed at all, the dock says so and the first-run setup's
    **Install** (or the connect card's) installs it;
- a repository checkout carries none: an agent service `brain.json` names, still running, is used whatever it is;
  otherwise the one found (**Agent service command**, else `autopilot` on `PATH`, else the installer's place) is started;
  set **Agent service command** (the setup and the connect page say so), or run `autopilot serve` yourself;
- started as `autopilot serve --port 0 --exit-with <the editor's process id>`, without a window (through `cmd.exe` on
  Windows, `sh` elsewhere), its output appended to `Autopilot/logs/agent-service-<yyyymmdd-hhmmss>.log` (the 5 newest
  kept). The dock connects once `brain.json` names it (looked at every 250 ms, for up to 30 s). If it ends first: "The
  agent service stopped (see *log*)", **View logs**. It ends with the editor that started it; the next connect (another
  editor's too) starts a new one;
- installed by the installer the addon carries (`powershell -NoProfile -ExecutionPolicy Bypass -File install.ps1` on
  Windows, `sh install.sh` elsewhere; no window; output in `Autopilot/logs/install-<yyyymmdd-hhmmss>.log`), then found
  and started. **Install** runs it too.

### Settings page

The gear (**Settings**, beside **New run**) shows the page: "SETTINGS" and **Back to runs**, then the **Account**:
from the agent service's `account` frame (and `preferences.json` before one came) — **Aura** (your email; what
Autopilot billed your Aura account this period, from the `account` frame's `bill`: "Billing period (Oct 1 – Oct 31):
$12.34 USD", nothing when Aura couldn’t say; under it in red, when the team is out of credits, Aura's words from the
`account` frame's `out_of_credits`, with **Add credits** first among the card's buttons when Aura names a page;
**Sign out**, which asks first: "Every editor on this computer stops using Aura until you sign in again."), **Aura
app** (the Aura app’s agent service, with its billing period and the same out-of-credits words), **BYOK** (the last four characters of the keys the agent service
has, the LLM key with its Coding Agent and provider: "Saved on this computer: Decision provider ••••a91f · OpenCode
(Anthropic) ••••3c2e", from the `account` frame's `keys`, `coding_agent` and `llm_provider`, OpenCode
and the provider its agent takes when it has none, another id as it is; with none saved, just the tag and the
buttons), **Signed out** ("Your Aura sign-in on this computer ended.": **Sign in again**, in place), or **Not set up**
("Choose how to run Autopilot"); and **Rerun setup** (before **Sign out**, a red text button, when signed in to
Aura; Sign out is disabled while the agent service isn't connected: "Connect to the agent service to sign out"), the way back to the
first-run setup, from its first step (to sign in with Aura, use the Aura app or switch to BYOK; disabled while a run
is going, "Finish or stop the run first": switching accounts reconnects to another agent service). Under it, but with
the Aura app, the agent service's line: "Agent service: running" and its address, "Starting the agent service…",
"Installing the agent service…", "Agent service: not installed" (**Install**), "The agent service didn’t start."
(**View logs**), or "Agent service: starts when Autopilot connects". The dock asks the agent service for the account
(`{"type": "account"}`) at every connection, when the page opens, after the setup, a sign-out and a change of account.
Then the project's runs:

- **This project · N runs**, with "Billed by Aura $X.XX" (the sum of their bills), and each run (newest first): its
  time, its prompt and, for a run through Aura, what Autopilot was billed for it ("$0.59": 2 decimals, "< $0.01" below
  a cent; BYOK runs have no bill). The rows are a list, not buttons: nothing to pick, no hover. With none, "No usage yet."

The bill comes from the agent service's `bill` frame (`{"type": "bill", "usd": 0.593177, "billed": true}`), sent after
a run through Aura is finished and before its result; it is kept on that run. `"billed": false` (with a `reason`: Aura
bills it later, or didn't) is kept but shows no amount.
Each of these settings is a plate laid out the same way: its name (mono caps), a line on what it does, then its control.

- **Share runs to improve Autopilot** (a switch under the text, "Data sharing on" or "off" beside it; `share_data` in `preferences.json`): "Enabling this sends recordings,
  screenshots, and agent traces and memories to the developers of Autopilot. For runs from your own Claude Code or
  Codex, the agent traces are its conversation during the run." (The code export itself never is.) The agent service learns the choice when the dock connects: with no run going the dock reconnects
  at once, otherwise it applies from the next connection.
- **Autopilot in Claude Code & Codex**: "One-time CLI setup to run Autopilot through Claude Code or Codex using your
  Claude or ChatGPT subscriptions, respectively." then `autopilot connect claude` and `autopilot connect codex`, each
  with **Copy** (to the clipboard) (see [Runs from your own Claude Code or Codex](#runs-from-your-own-claude-code-or-codex)).
- **Uninstall Autopilot**, last: **Uninstall** (disabled while a run is going), see
  [Removing Autopilot](#removing-autopilot).

The runs' usage and bills are kept for the project in `.godot/editor/autopilot_usage.json` (per user, not
committed), so the page keeps the project's runs across editor sessions (more of them than the run history's 50).
Runs saved before bills load without one.

### Runs from your own Claude Code or Codex

Set up with `autopilot connect claude|codex` (the Settings page's **Autopilot in Claude Code & Codex**), your own
coding agent can start a run on the game open in the editor (`autopilot mcp`, on your Claude or ChatGPT plan). The
agent service tells the dock with a `run_started` frame (the protocol's Runs), and the dock shows the run as one
started from the prompt box: live, its request (the claim, the goal, "Explore" and its brief, or the words as typed
for a run whose kind the agent service reads: no kind shown until its `run_kind` frame), and under it
**Started from Claude Code**, **Started from Codex** or **Started from your coding agent**; Stop, steering, the result,
the run history and the bill work as for any run. A run already going stays: the agent service starts one run at a
time. With the first-run setup open, the run goes on behind it.

### Data sharing and the files in Autopilot/

The choices above are the user's, for every engine and project on the machine, in `Autopilot/` under the user
settings folder: `%LOCALAPPDATA%` on Windows, `~/Library/Application Support` on macOS, `$XDG_CONFIG_HOME` or
`~/.config` elsewhere.

| File | What it is |
|---|---|
| `preferences.json` | `{"version": 1, "onboarded": bool, "share_data": bool, "access": "aura" \| "own_keys"}`, written by the first-run setup and the Settings page (merged: other keys are kept). Missing or unreadable: not onboarded, sharing on. Every `hello` carries `"share": <share_data>`. |
| `autopilot.env` | The BYOK keys of the first-run setup, its Coding Agent and OpenCode's LLM provider, as `KEY=value` lines (readable by you only where the system allows): `TYPESAFE_API_KEY`, `AUTOPILOT_AGENT` (`claude`, `codex` or `opencode`), `AUTOPILOT_OPENCODE_PROVIDER` (`anthropic`, `openai`, `opencode`, `openrouter`, `deepseek` or `google`) and the LLM provider's key (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `OPENCODE_API_KEY`, `OPENROUTER_API_KEY`, `DEEPSEEK_API_KEY` or `GEMINI_API_KEY`). `autopilot serve` reads it when it starts and before each run, and its values win over its environment's. |
| `brain.json`, `aura-brain.json` | Where a running agent service (yours, Aura's) listens (`{"url", "pid", "started"}`): written by the service, read by the dock. |
| `logs/` | The output of the agent services the dock started (`agent-service-*.log`) and of the installer (`install-*.log`). |

A game started from the command line reads `preferences.json` too, and `--playtest-share=0|1` overrides it for that run.

Environment variables read by the editor (useful for automation or a headless editor):

| Variable | What it does |
|---|---|
| `AUTOPILOT_PLAYTEST_URL` | The server URL, when the URL setting is empty. |
| `AUTOPILOT_PLAYTEST_TOKEN` | The bearer token, when the token setting is empty. |
| `AUTOPILOT_PLAYTEST_AUTOCONNECT=1` | Connect when the editor starts, even a headless one (automation). |
| `AUTOPILOT_PLAYTEST_START` | A run as JSON, e.g. `{"kind": "verify", "claim": "Verify that ..."}`, started once connected, as the **Run** button would. |

### How the editor and the game work together

In Godot the editor and the running game are separate processes. The agent expects one connection per run, so:

- the **editor** holds the one WebSocket to the agent server (its `hello` says `"host": "editor"`);
- the agent's `session` requests are handled by the editor: `start` plays the main scene
  (`EditorInterface.play_main_scene()`, or `play_custom_scene(map)`), `stop` stops it, and `restart` stops it and plays
  the same scene (or `map`) again: a fresh game process, autoloads included. The answer comes once the new game's
  autoload is attached;
- `codex_manifest` and `codex_read` (the project's files) are answered by the editor;
- every other request goes to the running game's autoload over the editor's **debugger connection**
  (`EditorDebuggerPlugin` and `EngineDebugger` messages named `autopilot:*`); its answers and events come back the
  same way, unchanged. With no game running, the editor answers `context` (`session_running: false`) and `ping` itself,
  and other requests get an error (status 409, "No game is running");
- a GDScript runtime error normally stops an editor-run game in the debugger. While the agent is connected, the editor
  lets the game continue at once (the error still reaches the agent as a `log` event and a `log_error` finding).
  Without a connection the game stays stopped for you, as usual.

The editor itself can run headless (`godot --headless --editor --path <project>`): the games it starts get the
project's `editor/run/main_run_args`, so put `--headless` there to run them headless too.

## Use it from the command line (CI, headless runs)

A game started with playtest arguments connects to the agent server by itself (its `hello` says `"host": "game"`),
asks for the run, writes the report files and quits with the run's exit code. Put the arguments after `--` so Godot
passes them to the game:

```
python -m autopilot serve
godot --headless --path <project> -- --playtest=verify --playtest-claim="Verify that pausing the game stops the timer" --playtest-report-dir=build/playtest
```

| Argument | Meaning |
|---|---|
| `--playtest=verify\|explore\|goal` | The kind of run. Without it the game only connects (with `--playtest-url`) and the agent server drives it, as `python -m autopilot play "<prompt>"` does. |
| `--playtest-claim=...` | The claim, for `verify`. |
| `--playtest-goal=...` | The goal, for `goal`. |
| `--playtest-minutes=N` | The time budget, for `explore`. |
| `--playtest-brief=...`, `--playtest-brief-file=<path>` | The design brief, for `explore` (optional). |
| `--playtest-steps=N`, `--playtest-attempts=N` | Options for other agent servers; Autopilot ignores them. |
| `--playtest-url=ws://...` | The agent server. Default: `AUTOPILOT_PLAYTEST_URL`, else the agent server running on this machine (the address it announces in `Autopilot/brain.json`), else `ws://127.0.0.1:8765/v1/bridge`. |
| `--playtest-token=...` | A bearer token to send. Default: `AUTOPILOT_PLAYTEST_TOKEN`, else none. |
| `--playtest-report-dir=<path>` | Where `result.json` and the report files the agent service sends (`report.json`, `junit.xml`) go. Default: `user://playtest/<time>/`. |
| `--playtest-map=<scene>` | The scene to start in: a `res://` path or a scene file's name (`level` finds `res://scenes/level.tscn`). A restart reopens it. |
| `--playtest-connect-timeout=<seconds>` | How long to wait for the agent server's welcome (it may still be starting). Default: 120. When it passes, the game writes `result.json` with the `error` and exits with 1. |
| `--playtest-stay-open` | Don't quit when the run is over. |
| `--playtest-share=0\|1` | Whether this run's data is shared (`hello`'s `share`). Default: `share_data` in `Autopilot/preferences.json`, else on. |

The Unreal plugin's spellings work too (`-Playtest=goal -PlaytestGoal=... -PlaytestUrl=... -PlaytestReportDir=... -PlaytestConnectTimeout=... -PlaytestShare=0`):
any argument whose name, ignoring dashes, underscores and case, starts with `playtest` is read the same way. An
exported build takes the same arguments, with or without `--`.

**Exit codes**: the result's own `exit_code` when it has one; otherwise **0** CONFIRMED (or achieved, or explored with
no bugs), **2** DENIED (or bugs found), **3** INCONCLUSIVE. **1** means an error: the agent server sent an error
frame, didn't welcome the game within the connect timeout (`--playtest-connect-timeout`, 120 s by default), or the
connection closed before the result. The report folder then gets a `result.json` holding `kind`, `error` (what went
wrong, in words) and `exit_code` 1.

A run asked on the command line is asked only once: if the connection drops before the result, the game exits with 1
instead of connecting again (a new connection would start the run over).

A headless game has no picture: its `hello` doesn't say `media`, and `record` and `capture` answer 409.

Headless, the addon sizes the root viewport to the project's window size (`display/window/size/viewport_*`; a headless
viewport is otherwise 64x64, which would squeeze the UI and the camera's view) and caps an uncapped frame rate at 60.

## Reference: what the addon answers

The wire protocol is the same for every engine (`autopilot/bridge.py`, `PROTOCOL`, in the repository). This
section lists what is specific to Godot.

### Paths and `rc`

- **Objects** are node paths: `/root/Level/Player`, `/root/Level/Player/Head/Camera3D` (a "component" is a child
  node). Also accepted: a path relative to `/root` (`Level/Player`) or to the current scene (`Player`), an autoload or
  engine singleton by name (`GameState`, `Input`, `Engine`, `ProjectSettings`, `OS`, `Time`), a script class by its
  `class_name`, a resource by `res://` path (`res://items/sword.tres`). A missing one gives
  `Object <path> does not exist`.
- **Properties**: script variables, script constants (read only, e.g. `SPEED`) and built-in properties; nested ones with
  Godot's `a:b` syntax (`position:y`, `stats:hp`). A read without `propertyName` returns all of them.
- **call**: script and built-in methods. `parameters` go by argument name (or as a list, in order); missing arguments
  take their defaults. Static functions are called on the script (`res://util.gd` or its `class_name`).
- **describe**: `{"Name", "Class", "Properties", "Functions"}`, the script's own members first (their `Description` is
  "script variable", "script constant" or "script function"), with `Returns` for each function.
- **Values** are native Godot units: metres; vectors as `{"x","y","z"}` (Vector2 as `{"x","y"}`); Color as
  `{"r","g","b","a"}`; Quaternion as `{"x","y","z","w"}`; Basis, Transform3D, Transform2D, Rect2, AABB and Plane as
  objects of those; a node as its path; a resource as its `res://` path (`<Class>` for one built into a scene without a
  path); an int with an enum hint as the enum's name (writes take the name or the number); arrays and dictionaries as
  lists and objects. Writes convert to the property's declared type (a typed array keeps its element type); an untyped
  variable keeps its current value's type.

### The kit frame (every request but `rc`)

All geometry outside `rc` uses Unreal's frame, so the agent's geometry is the same in every engine.

- **3D**: Z up, left-handed, centimetres. Godot `(x, y, z)` in metres becomes kit `(X, Y, Z) = 100 * (-z, x, y)`:
  Godot's forward (-Z) is kit +X, its right (+X) is kit +Y. Yaw is in degrees from +X toward +Y (turning right is
  positive), `-deg(rotation.y)` of the global basis (Euler YXZ); pitch is `deg(rotation.x)`, positive up; roll is
  `-deg(rotation.z)`. Directions given to `move` are in this frame.
- **2D**: `(x right, y down)` pixels become kit `(X, Y, Z) = (x, 0, -y)`, 1 pixel = 1 unit. A 2D rotation (clockwise on
  screen) is a kit pitch, `Pitch = -deg(rotation)`, with `Yaw = Roll = 0`. For `move` in 2D, kit X is screen right and
  kit Z is screen up.

### Requests

Names, arguments and result shapes are the same as for the other engines unless said here.

| Request | In Godot |
|---|---|
| `context` | `world` = the current scene's path, `character` = the player (see below), `camera` / `camera_manager` = the current camera's path, `controller` / `input_subsystem` = `""`. Also: `scene` (its `res://` path), `dimension` (`3d`, `2d` or `ui`), `engine: "godot"`, `project_dir`, and `controls`: the InputMap actions (`action` = the action's name) with their keys as Unreal key names (`godot_keys`: Godot's own names). Godot's built-in `ui_*` actions are listed only when the project changed them. |
| `session` | In a game started from the command line: `restart` reopens the scene the game was launched with (or `map`: a `res://` path or a scene file's name), and answers once it is loaded. Autoloads keep their state, as Unreal's GameInstance does across `OpenLevel`; `"fresh": true` also resets the autoloads' script variables to a new instance's values. `start` and `stop` do nothing. In the editor: see above. |
| `pause` | Sets `get_tree().paused`. The addon runs with `PROCESS_MODE_ALWAYS`, so requests are still answered while paused. |
| `key` | Unreal key names (`W`, `SpaceBar`, `LeftShift`, `One`, `F5`, `LeftMouseButton`, `MouseScrollUp`, `MouseX`/`MouseY` (mouse motion, `relative`; MouseY is positive up), `Gamepad_FaceButton_Bottom`, `Gamepad_LeftX`...), else an InputMap action's name (`jump`), else a Godot key name (`Space`, `Ctrl`). Events go through `Input.parse_input_event` and are flushed at once, so `_input`, `_unhandled_input`, `_gui_input`, `Input.is_action_pressed` / `get_vector` and `Input.is_key_pressed` all see them. A press stays down 0.05 s and 2 frames. Holds are leased as in Unreal; a hold with `seconds` (a timed hold, `features` has `timed_hold`) lets go after that much game time, without renewal. |
| `input` | `{"action": "<InputMap action>", "value"?, "mode"}`: an `InputEventAction` (strength from `value`). `press` goes down and up 0.05 s and 2 frames later; `hold` stays down until `release`. |
| `move` | Presses the actions bound to W/S/A/D (else the arrow keys) with analog strengths (`Input.action_press`), so that the camera-relative input points the given kit direction, every frame for `seconds` (the keys themselves when the game has no such actions). This fits the usual `Input.get_vector(...)` plus basis movement; a game that moves differently needs `key` or `input`. |
| `watch` | Local transforms sampled every frame (`moved` in kit units). `drawn`: the visible meshes' transforms in the thing's own frame (named `<path>/<mesh path>`), and `part shown/hidden` (meshes), `light on/off` (Light3D), `animation started/ended` (AnimationPlayer). |
| `view` | See below. |
| `screen` | The visible texts of Label, RichTextLabel (parsed text), Button, LineEdit and TextEdit, keyed `<UI>.<Name>`, where `<UI>` is the nearest CanvasLayer, scene instance root or owner (`HUD.ScoreLabel`, `Menu.StartButton`), or `Screen` for controls made in code. |
| `ui`, `ui_act` | Buttons (and Link, Texture and Menu buttons), CheckBox / CheckButton / toggle buttons (`checkbox`), OptionButton (`combo`, with `options`), Slider / SpinBox (`slider`, with `min` and `max`), LineEdit / TextEdit (`text_input`); each with `path`, `class` and `screen` (its middle in viewport pixels). `click` pushes a mouse press and release into the viewport at the control's middle (whatever is on top gets it, as with a player's mouse); when nothing takes it, the `pressed` signal is emitted instead (`"via": "signal"`). Also `set` (a slider's value, text, a toggle), `right_click`, `double_click` and `drag` (through the controls' `_get_drag_data` / `_can_drop_data` / `_drop_data`). |
| `timestep` | Godot has no fixed frame time at runtime: `fixed` caps the frame rate at `fps` (the answer has a `note`). |
| `record`, `capture` | The running game's root viewport, UI included (what the player sees). The picture is read back with `RenderingDevice.texture_get_data_async` (Forward+ and Mobile); the Compatibility renderer has no asynchronous readback, so there the viewport's `get_image()` is taken, at most `fps` times a second. Scaling, the JPEG (and, for a game that holds the connection itself, the base64 and the message) are made on the `WorkerThreadPool`; a frame due while the one before is still on its way is skipped (a gap in `n`). Through the editor, the game sends the JPEG's bytes over the debugger connection and the editor makes the `frame` message (or the `capture` answer). A recording goes on in the next play session of the same connection (each is a new game process) unless the agent stopped it, and ends with the connection; `record` `stop` is answered by the editor. Headless (`--headless`, or the dummy renderer), or with no game running: 409 `no picture: ...`. |
| `detectors` | `log_error` (`push_error`, script errors and engine errors, through a `Logger`), `nan`, `out_of_world` (a physics body 50 m below the lowest drawn point of the scene at its start), `long_fall` (the player falling for 8 s), `hitch` (a frame over 0.75 s, not in a scene's first 3 s), `hud_fraction`. Thresholds: `long_fall_seconds`, `hitch_seconds`, `out_of_world_margin`. |
| `codex_manifest`, `codex_read` | `project.godot`, `export_presets.cfg` and every `.gd .cs .tscn .tres .gdshader` file, skipping folders whose name starts with `.` (`.godot`, `.git`), `node_modules`, `addons/autopilot` and any folder holding a `.gdignore`. Plus the addon's summary of the project (main scene, autoloads, input actions, global classes, every scene's node tree) as `.godot/autopilot_codex/summary.txt` (written to `user://autopilot_codex/` when `res://` is read-only). Paths follow the protocol's rules (relative, no `..`, no drive, no skipped folder). `export`: the summary is rewritten on each manifest (`"skip"` keeps the last one). In an exported build it serves what the pack holds (scripts may be binary tokens there unless the export preset keeps them as text). |
| `find` (Godot only) | `{"class"?, "name"?, "limit"?, "things"?}` -> `{"objects": [paths]}`. `class` is a Godot class (subclasses included), a script `class_name` (or a script's file name), or `group:<name>`; `name` is a case-insensitive part of the node's name; `things: true` returns only the current scene's "things", like Unreal's actors: its direct children, physics bodies and areas, and instanced scenes' roots, not every mesh and shape under them. |
| `transforms` (Godot only) | `{"objects": [paths]}` -> `{"objects": {path: {"location", "rotation", "velocity", "center", "extent"} \| null}}`: the node's origin, global rotation, velocity (CharacterBody / RigidBody), and the box around its drawn parts (VisualInstance3D AABBs; else its collision shapes), `extent` being half its size; all in the kit frame. |
| `pose` (Godot only) | `{"at", "feet", "eye", "yaw", "pitch", "velocity", "on_ground", "path"}` of the player: `at` is the middle of its body (its collision shape's centre), `feet` half its height below, `eye` the camera; yaw and pitch are the camera's. |
| `movement` (Godot only) | `{"walk_speed", "jump_z", "gravity", "half_height", "radius", "step", "max_slope", "sources"}` in kit units: speed and jump from the player script's variables or constants (`SPEED`, `WALK_SPEED`, `JUMP_VELOCITY`, `jump_height` as `sqrt(2 g h)`...), gravity from a script `gravity` or the project's, half height and radius from its collision shape, `max_slope` from `floor_max_angle`. `step` is null (a CharacterBody doesn't step up); `sources` says where each value came from. |

**The player** is the physics body the current camera hangs from, else the first node in the group `player`, else the
scene's first CharacterBody3D (or CharacterBody2D).

### `view`

**3D**: the things with a drawn body (GeometryInstance3D) inside the camera's frustum that a line of sight from the
camera reaches (the box's centre, top or nearest point), nearest first, up to `max_count` within `max_distance`. A
"thing" is a node's nearest physics body or area, else the scene instance it belongs to, else the mesh. The player and
a large floor it stands on are left out. Each thing has: `name`, `path`, `class`, `center`, `size` (full size, kit
axes), `yaw`, `distance` (camera to the box), `screen_x` / `screen_y` (-1..1, right and up positive),
`partly_in_view`, `speed`, `top_above_feet`, `bottom_above_feet`, `crosshair_on` (a ray from the camera's middle,
areas included), `look` (`parts`: mesh names, `lights`, `animation`, `texts`: Label3D) and `label`.

`camera` has `location`, `yaw`, `pitch`, `fov` (horizontal, as Unreal's) and `vfov`. `body` has `velocity`, `feet_z`,
`on_ground`, `falling`, `crouched` (always false), `standing_on`, `touching` (the last `move_and_slide`'s contacts that
aren't floor) and `floor_slope_degrees`. `only` and `sonar` work as in Unreal (`ways` from sweeps of the body's own
shape with `test_move`, rays down for drops, edges to a 32nd of a step; no `point_blank`).

Not provided (listed in the answer's `omitted`): `held`, `scene.sun`, `openings`, `ways_up`, `lane`, slope ends,
`body.under` / `overhead`, `highlighted`, `limp`, and effects (`effects_play` is false headless).

**2D**: the drawn CanvasItems (sprites, polygons) inside the camera's rectangle, nearest the player first, with
`screen_x` / `screen_y`.

**Headless** (`--headless`), physics, raycasts, `test_move`, `Camera3D.unproject_position` / `is_position_in_frustum`
and GUI input all work as with a display (the tests below check this). What differs: the root viewport is 64x64 until
the addon resizes it; mouse capture is ignored (`Input.mouse_mode` stays visible, so a game that only turns its view
while the mouse is captured won't turn headless); effects don't run; and nothing flushes `Input.parse_input_event`
events (the addon flushes them itself).

## Removing Autopilot

### From this computer

The Settings page's **Uninstall** asks first ("Uninstall Autopilot from this computer?", "Removes the
agent service, your keys, your Aura sign-in and your setup choices, for every project.", with **[Optional] Delete
Autopilot's memories for all projects in this machine across all editors. I understand that this action is destructive and deletes Autopilot's memories for my entire team.**,
unticked each time it asks), then disconnects and runs `autopilot uninstall --yes [--keep-memory]` with the command
the dock would start the agent service with (autopilot/uninstall.py), from the temporary folder, its output in
`<temp>/autopilot-uninstall-<yyyymmdd-hhmmss>.log`. Left unticked, the memories are kept (`--keep-memory`). It
removes, for every project and editor on the computer: the agent services running (never the Aura app's), the Aura
sign-in (ended on Aura's side too), the `Autopilot/` settings folder (your keys, setup choices, logs, this machine's
upload id and, with the box ticked, what Autopilot learned about each game in `memory/`), and the `autopilot`
command (`uv tool uninstall autopilot`; on Windows once the removal has
ended). With the box ticked the memories are deleted on this computer and also the copy kept with Aura for your team
(and this install's copy kept for your own keys), and it also removes what the addon keeps about the runs in each
project the agent service has run (the run history, the usage, the codex summary and `user://playtest/`, below), and the dock forgets
its runs at once. Shared run data already sent is never removed; shared runs not sent yet are sent first, and those that
can't be sent now go with the rest. uv and Bun stay (other programs may use them).

While it runs the dock shows "Uninstalling Autopilot from this computer…" in place of everything else, and nothing starts,
installs or connects to the agent service until **Set up again** (or the editor's restart). Then: "Uninstallation
successful", under it what is left to do ("To finish, disable Autopilot in Project > Project Settings > Plugins and
delete addons/autopilot/ from each project that has it.") (**Set up again**, **View logs**; **View logs** first when the log names files it couldn't remove), or
"Couldn’t uninstall Autopilot from this computer." (**View logs**, **Try again**, **Cancel**; it is stopped after 10
minutes). With no agent service on the computer the dock empties `Autopilot/` itself, `memory/`
too with the box ticked, but never the Aura app's `aura-brain.json`.

From a terminal: `autopilot uninstall` (it lists what it removes and asks; `--keep-memory`, `--yes`), or
`python -m autopilot uninstall` in a repository checkout (whose own files, `memory/` and `.env` among them, stay:
delete the checkout for those).

### From a project

Disable **Autopilot** in **Project > Project Settings > Plugins** first (that removes the `AutopilotPlaytest` autoload;
deleting the folder first leaves the autoload in `project.godot`, pointing at a script that isn't there), then delete
`addons/autopilot/`. What stays in the project until you remove it:

| What | Where |
|---|---|
| The project's id (kept on purpose: enabling the addon again finds what the agent learned about the game under it) | `autopilot/project_id` in `project.godot` |
| The dock's settings (address, token, agent service command, the Aura app's choice, the composer's time limit...) | the `autopilot` section of `.godot/editor/project_metadata.cfg` |
| The run history (the last 50 runs) | `.godot/editor/autopilot_runs/` |
| The runs' usage and bills | `.godot/editor/autopilot_usage.json` |
| The codex summary | `.godot/autopilot_codex/` (or `user://autopilot_codex/`) |
| Each run's files, report and recording | `user://playtest/` (the project's user data folder) |

## Troubleshooting

| What you see | What to do |
|---|---|
| "The agent service isn’t installed on this computer." / "No agent service found." | **Install** (a release download), or set **Agent service command** under **Advanced**, or start it yourself (`autopilot serve`). |
| "The agent service didn’t start." | **View logs** shows its output (`Autopilot/logs/`); **Try again**. |
| "Can’t reach the agent service, retrying" | Start it (`autopilot serve`), and check the URL under **Advanced**. The dock retries by itself (1 s, doubling up to 15 s); **Try now** retries at once. |
| "Aura isn’t running on this machine." | The dock uses the Aura app's agent service and it isn't running: start the Aura app (**Start Aura**), and the dock connects by itself. Or choose another account with **Rerun setup** on the Settings page. |
| `record` / `capture` answer 409 "no picture" | The game runs headless (or no game is running): only a game that draws has a picture. |
| The connect card shows the agent service's own words (it refused the connection) | The agent service sent an error frame; its message is shown. One that requires a token needs it under **Access token** (or `AUTOPILOT_PLAYTEST_TOKEN`). |
| The dock doesn't appear | Check that the plugin is enabled (**Project Settings > Plugins**) and that you use Godot 4.7 or later. |
| **Reset memory…** says the memory was not reset | A run is going: the agent service resets memory only between runs. Stop the run (or let it finish) and try again. |
| The **Policy** tab stays empty | Policy steps are sent only to a dock on the agent service's own machine. |
| A command-line run exits with 1 | Read `result.json`'s `error` (or the game's lines starting with `AutopilotPlaytest:`): no welcome from the server within the connect timeout (`--playtest-connect-timeout`, 120 s by default), the server sent an error, or the connection closed before the result. |
| The game doesn't turn its view headless | It probably turns only while the mouse is captured, which headless Godot ignores. |

## Security

The agent can call functions and set properties in the running game, so connect only to an agent server you trust.
The addon only connects out, to the URL you set, and is inert until connected. To keep it out of a release build,
disable the plugin (or remove the `AutopilotPlaytest` autoload) before exporting, or exclude `addons/autopilot/*` in
the export preset's resource filter and remove the autoload.

## For contributors

### Source map

All paths are under `addons/autopilot/`.

| File | What it does |
|---|---|
| `plugin.cfg`, `plugin.gd` | The editor plugin: registers the autoload, sets the project id, adds the dock and the toolbar badge. |
| `playtest.gd` | The autoload inside the game: parses the command line, answers requests, delivers input, writes a command-line run's report and exit code. |
| `editor_bridge.gd` | The editor's connection to the agent server: the `hello`, runs (start, stop, steer), `session` (play, stop, restart), the codex, `record` across play sessions and the game's pictures, the `usage` frames, the `account` and `aura_sign_in` frames, and relaying everything else to the game. |
| `debugger.gd` | The editor's end of the debugger channel to the running game (`autopilot:*` messages). |
| `dock.gd` | The Autopilot dock: the connect card (connected by itself), the gear (Settings), the run composer, the live run, the result, run history, the footer, the Settings page's and first-run setup's data, the agent service it starts and the Aura sign-in it asks for, the removal's page. |
| `lib/service.gd` | The agent service started by the dock: which one (the addon's own when it carries one: installed, and one running from another build replaced), finding `autopilot`, the start and install command lines, `brain.json`, the logs; removing Autopilot from this computer (`autopilot uninstall`, or the settings folder emptied here). |
| `lib/link.gd` | The WebSocket client: connect (to the address given, the one `brain.json` announces, or Aura's), reconnect with backoff, the optional bearer token. |
| `lib/prefs.gd` | `Autopilot/` in the user settings folder: `preferences.json`, `autopilot.env`, the agent services announced there, the Aura app. |
| `lib/media.gd` | `record` and `capture`: the viewport's picture read back, scaled and encoded off the game thread. |
| `lib/frame.gd` | Conversions between Godot's frame and the kit frame. |
| `lib/keys.gd` | Unreal key names as Godot input events; InputMap actions. |
| `lib/values.gd` | Godot values to and from JSON (for `rc`). |
| `lib/rc.gd` | `rc`: property reads and writes, calls, `describe`. |
| `lib/world.gd` | The player, `find`, `transforms`, `pose`, `movement`. |
| `lib/sight.gd` | `view` and `watch`. |
| `lib/ui.gd` | `screen`, `ui`, `ui_act`. |
| `lib/codex.gd` | `codex_manifest`, `codex_read` and the project summary. |
| `lib/detectors.gd` | The in-game detectors (`log_error`, `nan`, `hud_fraction`...). |
| `ui/model.gd` | The dock's data without UI: runs (their verdicts, GAME TESTED among them), the composer's start frame (`run_of`: the words as typed, the kind read by the agent service), stages, policy steps, the markdown report. |
| `ui/history.gd` | The run history kept between editor sessions: a run as JSON and back, its file per run, the newest 50. |
| `ui/usage.gd` | The Settings page's data without UI: usage parsed and summed, numbers as shown, the usage file. |
| `ui/settings_page.gd`, `ui/setup_page.gd` | The Settings page (the Account corner, the project's runs and bills, Data sharing, Autopilot in Claude Code & Codex, Uninstall Autopilot) and the first-run setup (the stepper; Account, Agent service, Privacy settings, First run). |
| `ui/policy_log.gd` | The Policy log tab (filters, the replay of an attempt, the steps' table, the step: π(a \| s) and the Observation in plates or as raw JSON). |
| `ui/widgets.gd` | Drawn controls: the verdict block, the stage track (a 2 px progress rule, its names' layout measured by the tests), the harness attempts' squares, the spinner, probability bars, the replay, the recording's flipbook player; the pages' centred column. |
| `ui/autopilot_theme.gd` | Aura's look (The Flatness, dark): the palette (near-black grounds, 1 px rules, the amber accent #f0873a for what is interactive or current, green for what went well), the bundled fonts (IBM Plex Sans, IBM Plex Mono), 8 px plates and square controls, and the dock's Theme. |
| `fonts/`, `icons/` | Bundled fonts (SIL Open Font License, see the `OFL-*.txt` files) and the icon. Both folders hold a `.gdignore`. |

Every `.gd` file has a matching `.gd.uid` file: keep them together when you move, add or delete a script.

### Running the tests

The tests start Godot headless (always `--headless`; never a window) against a small test game, with a stub agent
server written in Python on top of `autopilot/bridge.py`. The test game, **Orchard Walk**, is in `tests/game/`: a menu
(a button, a checkbox, a slider, an option list, a text field), a first-person 3D orchard with six apples (`Fruit`),
a lever that opens a gate, a hay bale to jump on, a pond, a mailbox, a HUD (`Basket n / 10`), and a small 2D level. It
has two planted bugs for the detectors: the mailbox's script calls a method on `null` (`log_error`), and the gate
sinks through the ground when the lever is pulled (`out_of_world`). Nothing else is needed: the tests copy the game
and this addon into a temporary folder, import it there, and delete it at the end.

From the repository root, with the Autopilot repository's Python environment (`.venv`, see
[CONTRIBUTING.md](../../CONTRIBUTING.md#development-setup)) and `AUTOPILOT_GODOT` set to the Godot 4.7+ **console**
executable (on Windows, `Godot_v4.x_win64_console.exe`):

```
set AUTOPILOT_GODOT=C:\path\to\Godot_v4.7.2-stable_win64_console.exe
.venv\Scripts\python.exe plugin\godot\tests\run_all.py
```

It runs the five test scripts one after another on one copy of the game (about two minutes), prints
`PASS` / `FAIL` per check and a summary, and exits 0 when all pass. One script alone: `run_all.py smoke` (or run the
script itself, which assembles its own copy):

| Script | What it tests |
|---|---|
| `tests/units.py` | Without a game: `tests/units.gd` (`preferences.json` and `autopilot.env`, the usage sums and file, the Autopilot bill: `bill` frames, the account's `bill`, money and dates as shown, bills kept with the usage and old records loading, the picture's scaling, encoding and message text, the agent service: finding `autopilot`, which one starts and whether the addon's is installed first, the one running kept or replaced (only an agent service's program stopped, once a session, never with the setting or a connection open), its start and install command lines, `brain.json`, the logs, with a stand-in launcher and stand-in processes; the uninstall command line (run for real with a stand-in `autopilot`: in the temporary folder, its words, its exit code, its log) and the settings folder emptied here but for `memory/` and `aura-brain.json`; the kind of run the composer's words ask for and its start frame; the stage track's names whole and within their room at every width from 160 to 1400 px; the stages at which a run can be steered; Aura's look: its fonts bundled and read, the old ones gone, its palette, round corners, accent and success kept apart, the verdict banner unslanted, the icon's color) and `tests/dock_units.gd` (the first-run setup with its stepper, Use Aura app before Sign in with browser, Next or Finish by
what the agent service remembers, step 4 (First run: waiting, the Explore run offered with the memory empty and its start
frame, closed by itself with the memory not empty, Skip), the first run's Explore for 10 minutes by default, the agent service and the Aura sign-in driven by `aura_sign_in` frames, the Settings page and its Account corner driven by `account` frames (asked again after Finish), BYOK wording, the Coding Agent and LLM provider dropdowns (preselected from the `account` frame or `autopilot.env`, the provider locked for Claude Code and Codex, OpenCode's own brought back, written to `autopilot.env`) and the Coding Agent with its provider on the Settings page, the bill on the run and the Settings page, the gear opening and closing the Settings page (Rerun setup there only, not in the footer), no account named in the footer, BYOK and never "Own keys", Reset memory (confirmed in place in the Memory row, its outcome there), real-time runs only, the settings; the one prompt box (no kind read as the words are typed, its Time limit at the right, kept per project, the start frames, `run_kind` and GAME TESTED, Verify a fix), Rerun setup on the Settings page (one button there, the setup from its first step, disabled while a run is going), the run history's selection, memory at the end of a run, Uninstall Autopilot (last on the Settings page, only ever a stand-in `autopilot` and a stand-in launcher, disabled while a run is going, confirmed in place with Delete Autopilot's memories off each time (ticked: no `--keep-memory`), `autopilot uninstall --yes [--keep-memory]` run disconnected, its page by exit code 0, 3, other or too long with Set up again, View logs, Try again and Cancel, nothing started or connected meanwhile, no setup with `preferences.json` gone, the settings folder emptied here but for `memory/` without an agent service), the setup at step 2 at start when the agent service is gone (and not with it there, the command set, Use with Aura, not set up, headless), the recording copied into the run folder and its player; and every page laid out at 320x900, 360x640, 560x480, 900x400 and 1400x900 and narrowed again: nothing past the dock's edge or its scroll container's, nothing making the dock wider or taller, the 560 px column centred, the prompt box's placeholder on one line, the live stage track's names whole, the steer box's "You can steer once the game is playing (Rollout)" on one line; the gear whole, one at a time, inside the dock and beside New run; the steer box enabled only at Rollout, the words kept and nothing sent at the other stages, the same box on the Feed tab; the status line (last row of every page, gone after 8 s, never the progress lines); the Live page without a policy column; the buttons that can't act now greyed but pressable, the status line saying why; the Policy log's footer by the harness shown; the Policy log's Find an action… and ◀ ▶; Get Aura; Back to the last run; runs from the user's own Claude Code or Codex (`run_started`: shown live with Started from Claude Code, Codex or your coding agent, ignored while a run is going, behind the first-run setup), the Settings page's Autopilot in Claude Code & Codex with its Copy buttons, and the data sharing text; the activity feed: each `activity` frame a line under its stage (which moves the stage track), a call and its result one line with two folds, a frame sent again changing nothing, an orphan result its own line, the Feed tab's headings with their counts and its folds opening, what the hello asks for (`activity_since`), a run saved and read back with its events; built and driven headless, with a stand-in link), with the user settings folder pointed at a temporary one. |
| `tests/smoke.py` | Every request against the game, the detectors, codex, 2D. |
| `tests/ci_test.py` | Command-line runs against a stub server: exit codes, report files, the token, the connect timeout; `hello`'s `share` (`preferences.json`, `--playtest-share`), no `media` headless, `record` and `capture` answering 409, `usage` frames. |
| `tests/kit_live.py` | The agent's kit (`autopilot.kit`, `autopilot/engines/godot.py`) on the game. |
| `tests/editor_test.py` | A headless editor: the dock's connection (`hello` with `media` and `share`), session start/restart/stop, relaying (`record` and `capture` answering 409 for a headless game). |

Each starts its own Godot process (the editor test, also the games the editor starts), so run one at a time.
`GODOT_TEST_PROJECT` (optional) points the scripts at an already assembled and imported copy instead.
`tests/harness.py` holds the shared helpers: assembling the game (`project()`), the stub server (`Brain`),
`start_godot` (always headless) and `stop` (kills the whole process tree).

## License

Apache-2.0, as the rest of the repository. The bundled fonts are under the SIL Open Font License 1.1.
