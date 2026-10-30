# Autopilot: AI playtesting for Unreal Engine

Autopilot is an Unreal Engine 5 plugin that lets an AI agent play your game to test it. A prompt is one of four types:

- **Exploration**, e.g. "Explore the crafting system in Lvl_Main.": the agent explores the game for a set number of
  minutes and reports likely bugs: engine errors, Blueprint runtime errors, things falling out of the world,
  teleports, values over their maximum, on-screen counts above their limit, and more.
- **Testing**, e.g. "Go through the currently open level and tell me everything that looks wrong.": an exploration
  asked for in your own words, with a report of the problems found.
- **Verification**, e.g. "Verify that the tree shakes when you hit it with an axe.": the agent plays the game to
  check it and answers **CONFIRMED**, **DENIED** or **INCONCLUSIVE**, with evidence.
- **Goal**, e.g. "Kill the boss while keeping your health above 20% of its maximum value.": something to get done in
  play, achieved or not.

For every prompt, Autopilot starts Play In Editor by itself, reads your project's code, tries things in the game and
plays according to your prompt; when it is done, the play session ends by itself and the report opens.

The plugin is only the game-side half. The decisions are made by the **agent service**, a separate program
from this repository that runs the language models. In the editor, the plugin installs it once (from the installer
it carries in a release download) and starts it itself, hidden, whenever it connects and none is running; it ends
with the editor (see [The agent service](#the-agent-service)). You can also run it yourself with `autopilot serve`
(`python -m autopilot serve` from the repository). The plugin connects out to it over a WebSocket (by default
`ws://127.0.0.1:8765/v1/bridge`, or wherever it says it listens), and the service then reads the game and presses
its controls through the plugin, in JSON messages (a versioned protocol, `protocol: 1`).

In the code, the plugin's modules are still called `AutopilotPlaytest`, `AutopilotPlaytestEditor` and `AutopilotCodex`;
the plugin itself (`Autopilot.uplugin`) and its editor tab are called Autopilot.

## Requirements

- Unreal Engine 5.4-5.8, on Windows or macOS (the releases build Win64 and Mac for each).
- The Enhanced Input plugin (enabled automatically).
- The agent service: the release download's plugin carries its installer (`AgentService/`), which the first-run
  setup runs. From a repository checkout, set **Agent service command** (see [Settings](#settings)) or run
  `python -m autopilot serve` yourself; see [Running from a clone](../../../CONTRIBUTING.md#running-from-a-clone).

## Install

1. Copy this folder into your project as `<YourProject>/Plugins/Autopilot/`.
2. Build it:
   - **C++ project**: regenerate project files and build your editor target, or just open the project and
     let the editor build the plugin.
   - **Blueprint-only project**: the editor can't compile C++ for you, so build the plugin once with
     `RunUAT BuildPlugin` (see [Building](#building)) and copy the packaged output, which includes
     `Binaries/Win64`, into `Plugins/Autopilot/` instead.
3. Open the project. Click the orange **Autopilot** icon on the level editor toolbar (or **Window > Tools >
   Autopilot**) to open Autopilot.

To update, put the new download's `Autopilot` folder in `Plugins/` (in place of the old one, or copied over it) and
open the project: there is no need to delete `Binaries` or `Intermediate` (yours or the plugin's). The first time it
connects, the plugin runs the agent service it carries: it installs it over an older one and replaces an older
one still running (see [The agent service](#the-agent-service)).

## First-run setup

The first time you open the **Autopilot** tab, a setup of three steps, or four when Autopilot doesn't know your game yet,
takes its place (a stepper at the top shows where you are and what you chose). With Aura (recommended), in short: click
**Create Account** if you don't have an Aura account yet, or **Sign in** if you do; the tab sets up the agent service,
then your browser opens on Aura's sign-up or sign-in page; once you are signed in, **Agent service ready** shows: click
**Continue**; choose whether to share your runs; then, optionally, let Autopilot explore your game (10 minutes by
default; safe to skip) and send your first prompt. In detail:

1. **Account**: how to run the agent.
   - **Sign in with Aura** is billed to your Aura plan (the card says "Autopilot is maintained by the developers behind Aura, the multiagent AI solution to Unreal and Unity game development. You can use your Aura subscription to run Autopilot. New users get free Aura credits."). The agent service signs in for you on Aura's
     website: the tab installs and starts it if needed ("Setting up the agent service…", "Starting the agent
     service…"), asks it to sign in, and your browser opens on Aura's page. While it waits, the card shows the link
     (**Open again**, **Copy link**, **Cancel**); once you choose Allow it shows **Signed in as** *your email*, with
     **Continue** and **Use another account**. If the sign-in is denied, times out or Aura can't be reached, it says
     so in one line with **Try again**. While the sign-in goes on (getting ready, in your browser, waiting for the
     agent service) the BYOK card is not shown; **Cancel** brings it back. Once signed in it is folded and dimmed,
     with **Show**. The plugin never sees Aura's tokens: the agent service keeps the sign-in for
     the computer, so every editor here uses it.
   - The card has two buttons, the Aura app installed or not: **Sign in** signs in as above. **Create Account** (the
     default) opens Aura's account creation page in your browser, and the card then says "Finish creating your Aura
     account" and "Once you have created your account, sign in and start using Autopilot", with **Sign in** (as above).
     The page is on the Aura server the agent service signs in to (`AUTOPILOT_AURA_SERVER` in autopilot.env, else the
     download's), else https://app.tryaura.dev.
   - **Bring your own keys (BYOK)**, folded until **Show** (**Hide** folds it again): a **Decision provider API key** (e.g. TypeSafe), then the **Coding Agent** that
     writes and runs the harness (**Claude Code**, **Codex** or **OpenCode**, the default), then an **LLM provider API
     key**, with the LLM provider in a dropdown at the start of its row. Claude Code takes Anthropic's keys only and
     Codex OpenAI's, so for them the dropdown shows **Anthropic** or **OpenAI**, locked; with OpenCode you pick one of
     **Anthropic**, **OpenAI**, **OpenCode Zen**, **OpenRouter**, **DeepSeek** and **Gemini** (switching agents and
     back to OpenCode brings back the provider you picked for it). Both dropdowns start on what the agent service says
     it uses, else what is in `autopilot.env`, else OpenCode and Anthropic. Both keys are optional (leave them empty if
     they're already set where the agent service runs). **Use my keys** writes the agent (and OpenCode's provider) and
     the keys you fill in to `autopilot.env` and turns **Use with Aura** off.
2. **Agent service**: whether it is ready. Already running: **Agent service ready** at once. Not installed ("One-time
   installation takes approximately 1 minute."): **Install** runs the installer the plugin carries ("Installing the
   agent service…"), then starts it. A plugin without the
   installer (a repository checkout) says **No agent service found**, with an **Agent service command** field and
   **Start** (it saves the setting and starts it), or `autopilot serve` to run yourself (**Copy**). If installing or
   starting fails, the step says why, with **View logs** and **Try again**. **I'll do it
   later** goes on. A release download always runs the agent service it carries (see
   [The agent service](#the-agent-service)): a newer download over an earlier one installs its own before it first
   starts it, and replaces an older one still running. An **Agent service command** setting is never replaced.
3. **Privacy settings**: whether to share your runs' data (**Share my runs to improve Autopilot**, on by default:
   "Enabling this sends recordings, screenshots, and agent traces and memories to the developers of Autopilot."; see
   [Data sharing](#data-sharing)), and a recap of steps 1 and 2 (e.g. **Account: Aura · you@studio.com**, **Account:
   BYOK**, **Agent service: Ready**; the stepper says **1 ACCOUNT · AURA**, **· AURA APP** or **· BYOK**).
4. **First run**, only when the game's memory is empty (no earlier runs, notes or library: what the agent service
   says when the tab connects). Step 3's button then reads **Next**; it saves your choices and connects, and step 4
   waits for the connection ("Connecting to the agent service…", then "Checking what Autopilot remembers about this
   game…", with **Skip**). With the memory empty it recommends an Explore run so Autopilot learns your game first:
   **Minutes** (10 by default, the composer's value) and **Explore and finish**, which starts the run and shows it
   live, or **Skip and finish** (the card: "Autopilot still works if you skip this step."). When the memory holds something, the stepper has three steps,
   step 3's button reads **Finish**, and step 4 is skipped by itself.

**Finish** (or **Next**) saves your choices and connects; the tab shows once the setup is done. The choices are kept for every project and engine on this
machine, in `Autopilot/preferences.json` in your user settings folder (`%LOCALAPPDATA%` on Windows,
`~/Library/Application Support` on macOS, `~/.config` elsewhere), next to `autopilot.env`; if Unity's or Godot's
Autopilot already asked, the Unreal tab doesn't ask again. **Rerun setup**, on the Settings page (the gear beside
**New run**), opens it again at step 1. While a run is going it is disabled ("Finish or stop the run first"):
changing the account reconnects, which would end the run. Command-line runs never show it.

The **Settings** page starts with the **Account** corner: how runs go (**Aura** with your email and, when Aura says it,
what Autopilot billed your Aura account this billing period, e.g. **Billing period (Oct 1 – Oct 31): $12.34 USD**;
**Aura app**; **BYOK** (**Your own keys**) with the last characters of the keys the agent service has, if any, e.g.
**Saved on this computer: Decision provider ••••a91f · OpenCode (Anthropic) ••••3c2e**; or **Not set up**), whether the
agent service runs (**Agent service: running** with its address; starting or installing; **Agent service: not
installed** with **Install**; **The agent service didn’t start.** with **View logs**; **Agent service: starts when
Autopilot connects**), **Rerun setup** (the setup from its account step), and for Aura **Sign out** (red; it asks
first, in place: every editor on the computer uses the sign-in). When the Aura sign-in has ended it says **Signed out**, with **Sign in again** right there. The tab asks the
agent service for the account again after the setup, after a sign-out, at every connection and when the Settings page
opens. The Settings page ends with **Uninstall Autopilot** (see [Removing Autopilot](#removing-autopilot)).

The tab's look is Aura's "The Flatness" (dark only): flat fills and 1px rules, one amber accent (`#f0873a`), IBM Plex
Sans and IBM Plex Mono, 8px corners on cards and square controls. Its last row, on every page but the setup and the
removal, is a **status line** that says what just happened for 8 seconds, in one line (its whole text as its tooltip):
**Copied the report**, **Copied**, **Told the agent: … is not a bug**, **Showing …**, **Open the level to show this
finding in it**, why something was refused (**Not connected: connect first**, **A run is going: wait for its result,
or stop it**, **Not connected: connect first to reset the memory**, **A run is going: reset the memory once it is
done**), **Couldn’t start Aura**, **Couldn’t save the setup to … (error n)**, **A bundled font couldn’t be read: using
the editor’s font**, and the plugin's own notices (a run started, stopping, the connection).

Set up before, but the agent service isn't on this computer any more (removed by hand, e.g. `uv tool uninstall
autopilot`): the first time the tab opens in an editor session, the setup opens at step 2, **Agent service**, in place
of the connect card, so **Install** (or, from a repository checkout, how to set it up) is right there. Your other
choices are kept. This happens only when the editor starts the agent service itself (see
[The agent service](#the-agent-service)) and none is running.

Every page of the tab reflows with its size: rows wrap or stack when it is narrow (down to about 320 px), content is at
most 620 px wide (New run; Settings 640, the setup 600, the connect card 540) and centered when it is wide, and pages
scroll when it is short. Below 700 px the history becomes a button over the page (the run picked, or **New** and how
many runs this session) that opens the list, with the **Memory** row, under it.

## The agent service

Once the first-run setup is done, the editor connects by itself (when it starts and when the tab opens; not after you
press **Disconnect**, for the rest of the editor session, until **Reconnect** on the connect card). With the Agent
Service URL left at its default and **Use with Aura** off, connecting starts the agent service when none is running:

- **Finding it**: the **Agent service command** setting if set (a full command line, e.g.
  `C:\autopilot\.venv\Scripts\python.exe -m autopilot`); it is used as it is, never updated or replaced. Else, in a
  release download (its `AgentService/` folder has the installer and `agent-service.json`), the agent service it
  carries, where its installer puts it (`%USERPROFILE%\.local\bin\autopilot.exe`, `~/.local/bin/autopilot`): when the
  one there was installed from another download (the `agent-service.json` the installer copies next to
  `autopilot.env` names another wheel), or it isn't there but an `autopilot` is on `PATH`, the plugin installs its own
  first, then starts it (once per editor session; if updating fails, the one installed starts). An `autopilot`
  elsewhere on `PATH` is never used. Nothing installed at all: the tab offers **Install** (the first-run setup's step
  2). Without the installer (a repository checkout): `autopilot` on `PATH`, else the installer's place; none: the tab
  says how to set it up.
- **Starting it**: one already running (it says where it listens in `Autopilot/brain.json`) is used: a second one is
  never started. In a release download, only if it was built from the same commit as the one the plugin carries
  (`brain.json` and `agent-service.json` both name it), unless **Agent service command** is set: an older one is
  stopped (only if its program is `autopilot` or a Python and its process is the one that wrote `brain.json`: created
  before the `started` time it wrote there, so a process id reused since by another program is never stopped; anything
  else, or a `brain.json` that doesn't say when it started, is left running and used), its `brain.json`
  removed, and the plugin's own installed if needed and started. The log says so (`[Autopilot] An older agent service
  was running (pid N): replacing it with this plugin's`). This happens once per editor session (one that comes back is
  used), never to the one the plugin is connected to. So installing a new download is only: unzip it into `Plugins/`
  and open the project. Else the plugin runs `autopilot serve --port 0 --exit-with <the editor's process id>`, hidden, with its
  output in `Autopilot/logs/agent-service-<date-time>.log` (the 5 newest are kept), waits up to 30 s for it to say
  where it listens, and connects. If it stops before that, the tab says **The agent service stopped** with **View
  logs**. It ends with the editor that started it; other editors on the computer use the same one, and once it has
  ended the next connect starts a new one. One that ends within a minute of starting isn't started again until you
  press **Try again**.
- **Installing it**: a release download carries the installer in `Plugins/Autopilot/AgentService/` (a `autopilot-*.whl`,
  `install.ps1`, `install.sh`). **Install** runs `powershell -NoProfile -ExecutionPolicy Bypass -File install.ps1`
  (`sh install.sh` elsewhere), hidden, with its output in `Autopilot/logs/install-<date-time>.log`. It is needed once
  per computer.

The processes are watched on threads of their own: nothing waits on the editor's game thread.

## Quick start (editor)

1. In the editor, open the **Autopilot** tab: the orange **Autopilot** icon on the toolbar, or **Window > Tools > Autopilot**,
   and finish the [first-run setup](#first-run-setup) the first time.
2. The tab connects by itself, and the plugin starts the agent service if none is running (see
   [The agent service](#the-agent-service)); **New run** then says **● CONNECTED** and the address at its foot, with
   **Disconnect**. Until then the connect card says what is happening (**Agent service**, **Not connected**:
   connecting, installing, starting, or why it can't, with what to do: **Install**, **Try again**, **Try now**,
   **View logs**, **Get Aura**, **Start Aura**, **Stop**; disconnected after a run was shown: **Back to the last
   run**), and **Advanced** unfolds the **Agent service URL**, the **Access token** and the **Agent service command**
   (kept when you press Enter or leave the field; the same settings as in Editor Preferences). A run shown while
   there is no connection says so in a bar over it (**Reconnecting to the agent service…** with **Details**, or **Not
   connected to the agent service** with **Reconnect**). To run it yourself instead (e.g. from the repository:
   `python -m autopilot serve`), start it first: the plugin uses one already running.
3. Type what to do in the box, in your own words, and press **Run** (in the box, at its right). The **Time limit**
   beside it (minutes; it starts at the one you last set, 30 the first time, kept per project) bounds every kind of run. As the run starts,
   the decision model reads which of the four types the words ask for, and the run shows that type from then on:
   - **Exploration**: explore the game or a part of it for bugs, e.g. "Explore the crafting system in Lvl_Main.",
     "Find bugs in the shop". The agent plays for the Time limit's minutes, with the **Design Brief** setting as what
     the game should do. Its report's status is **EXPLORED**.
   - **Testing**: a test in your own words, asking for the problems to be reported, e.g. "Go through the currently
     open level and tell me everything that looks wrong.". It plays as an exploration; its report's status is
     **GAME TESTED**.
   - **Verification**: a claim or a question about the game, e.g. "Verify that the tree shakes when you hit it with an
     axe.", "Can the player swim?". The Time limit is the whole run's.
   - **Goal**: something to get done, e.g. "Kill the boss while keeping your health above 20% of its maximum
     value.". The Time limit is the whole run's.

   The agent service reads the command line's prompts the same way. Runs are real time: the game runs on while the
   agent decides. The agent starts Play In Editor by itself when it needs the game running, and ends it when the run
   is done.

   Play In Editor plays the level open in the editor, unless the prompt means another ("In Lvl_Arena, verify that
   ...", "Explore the Harbor level", "in the desert one", `/Game/Maps/Harbor`): the agent picks it from the project's
   levels before it starts playing. That level plays without being opened in the editor (the play session's map
   override): the open level and its unsaved changes stay as they are.

While a run is going, the tab shows:

- **Live**: the stages of the run (Launch, Analyze, Probe env, Build harness, Rollout · H*n*, Process result, Report;
  each name always whole: the row wraps onto a second line when the tab is narrow; under each, how long it took, m:ss,
  the current one's so far in the accent; the stage follows the agent service's activity frames, or its status lines
  for an older one), the feed's last 10 lines (one line each, no folds; the Feed tab has them all), what the agent is doing now, its
  **Harness attempts** so far (a square each, in one row that never wraps: the newest that fit, at most 24, so the current one, outlined, is always last; the others filled; then the count of them all), and **Stop** to finish
  early (it says **Stopping…** until the result; the agent still writes its report). "Saving what it learned to
  memory", at the end, is part of the report. If the agent service supports it, a box at the foot ("Send a steering
  prompt to Autopilot") lets you nudge the run, e.g. "go round the rocks to the left first": you can type at any
  stage, and **Steer** sends it while the game is playing (the Rollout stage; before that its tooltip says "You can
  steer once the game is playing"). While it stops, both are locked. The Feed tab has the same box, with the same
  words.
- **Feed**: the run as a transcript, grouped by stage ("Nothing yet." before the first line): a heading per stage
  (Launch, Analyze, Probe, Build harness, Play, Results, Report) with its count of lines, and under it what the agent did
  and said, one line per event of the agent service's activity feed (its `activity` frames): its time (m:ss), a
  small mark for its kind (? the agent's words, R read, S searched, W wrote, P tried in the game, E episode, L looked, !
  report or alert, > what you steered, * restart, M the game's memory refined), its words and, for a call that came back
  (a probe, an episode, a look), the result's words on the same line, quieter. Under a line, a fold for each detail ("+
  the code", "the output", "what was written", "the summary", "the edit and its evidence", "the whole message"…) opens a
  mono block with it, at most 320 px tall,
  scrollable and selectable. The status lines, engine findings, what you steered ("You: …") and the result ("Result:
  Goal hit") are among them, each under its stage; the agent's findings are tagged red, the engine's by severity. An
  agent service too old to send activity frames gives the status lines alone, as before. When a run changed what the
  agent remembers about the game, its last line says so (**Memory updated**). A run's feed is saved with it, so a
  run reopened from the History shows it whole. A tab that connects again while a run goes asks the agent service for
  the events it missed (`activity_since`); an event sent twice is shown once. While the run goes and the agent service is on this computer, the tab bar says
  **● LOCAL AGENT**. The prompt box stays editable during a run.
- **Result** once it ends: the verdict above its reason, the run's recording when it has one, the evidence and the
  findings (no findings section when there are none). A run without a recording says why in one muted line where the
  player would be, **No recording:** followed by the agent service's reason (for example "the game can't be recorded
  here: it runs without rendering"; nothing when it gave none). The line is saved with the run, so the history shows it
  too. A run whose recording file is gone keeps the player, dimmed, with nothing to press, and says **The recording
  isn’t there any more.** A stopped run's reason is "Stopped before a result."; one whose connection closed first says
  **NO RESULT**. Evidence lines that start with their harness and time ("H1 12.4 s") show those in the accent. The
  recording plays in a screen under the verdict (230 px high, 200 in a narrow tab; a play/pause button, a time bar to
  scrub, the time and the length, and a button (a folder) that shows the file in File Explorer, Finder or your file
  manager); nothing plays until you press play. The
  agent service records every run it can (a game that renders: not `-nullrhi`); the plugin copies the recording into
  the run's report folder as `recording.mp4`, off the editor's game thread, so it stays with the run (a Reset memory
  doesn't take it, and runs opened from the history show it). It is played with the engine's Media Framework (Windows
  Media Foundation or Electra, both on by default); if it can't be played there, that button still shows you the file to open in another player. Each finding's severity tag says **ERROR**, **WARNING** or **INFO**. For each finding, **Show in level**
  moves the level viewport camera to where it happened (**Showing *actor***, **Showing the place in the open level**,
  or **Open the level to show this finding in it**), **Not a bug** tells the agent not to report it
  again in this project (then it says **Marked not a bug**), and **Verify a fix** fills in a new Verify run that checks
  the bug is gone ("Verify that this is fixed: …"). **Run again** (a circular arrow, filled), **Copy report** (a
  clipboard) and **Run folder** (a folder) are icons at the right of the run's id line, above its prompt; their names
  are their tooltips; **Run folder** is disabled when the folder is gone. **Copy report** copies Markdown: the prompt,
  the verdict, **Duration:** · **Harnesses:** · **Policy steps:** · **Alerts:**, the alerts as quotes, **## Reason**,
  the evidence and the findings. The direct changes' heading is **Changed directly · not through play · n** (**Changed
  directly · n** when narrow). The tab bar says the run's id, kind, **DURATION** and attempts when the tab is wide.
- **Policy log**: every step the agent's policy took, with the observation it was sent, the action
  probabilities it returned, the action it chose and how long the step took. Filter by harness and by what the step
  did, or find an action (**Find an action…**: the chosen or the current action). Picking any step, the newest too,
  pauses on it (**PAUSED ON H*n* #*step***); step with the ◀ ▶ buttons (disabled at the ends), or press **Follow
  live** to follow new steps again (**● FOLLOWING**). The legend lists the actions that ran in the harness shown (at most
  8); the footer counts every step of that harness (or of all), whatever the filters keep. **Copy step JSON** copies
  the frame as the agent service sent it (its questions' instructions left out), and **Raw JSON** shows it. The agent service only sends these to an editor on the same machine (unless it is started
  with `AUTOPILOT_INSPECT=1`), and you can turn the log off with the **Show Policy Log** setting.

Each run in the history says when it started, how long it took and its harness attempts (e.g. **Oct 19 13:25 · 03:26
· 2 attempts**). The run history (the last 50 runs) is kept in `Saved/Autopilot/Runs/`, so closing the tab or the editor loses
nothing. Each run's report files are written to `Saved/Playtest/<date-time>/`.

**Settings** (the gear beside **New run**; **Back to runs** closes it) also lists **This project**'s runs with usage or
a bill (**Time**, **Prompt**, **Billed**; **No usage yet.** when there are none; plain rows, nothing to pick): what Autopilot was billed for each through Aura (the agent service says it when the run is
finished; two decimals, **< $0.01** below a cent, **—** for a BYOK run), and **Billed by Aura $X.XX**, their sum. The
tokens the models used are still counted and saved with each run, for the agent service's reports, but the page no
longer shows them. It also holds the [Data sharing](#data-sharing) choice, then the card **Autopilot in Claude Code &
Codex** (see [Runs from Claude Code or Codex](#runs-from-claude-code-or-codex)) and, last, **Uninstall Autopilot**. Those
two settings are laid out alike: the setting's name, what it does, then the switch (with **Data sharing on** or **Data
sharing off** beside it) or the commands. A run's time limit is the prompt box's own (**Time limit**, beside **Run**).

### Runs from Claude Code or Codex

You can also run Autopilot from your own Claude Code or Codex, on your Claude or ChatGPT plan: the Settings page's card
**Autopilot in Claude Code & Codex** says how ("One-time CLI setup to run Autopilot through Claude Code or Codex using
your Claude or ChatGPT subscriptions, respectively."), with `autopilot connect claude` and `autopilot connect codex`,
each with **Copy**. Then ask
it, for example, "Use Autopilot to verify that the door opens with the red key." Runs still use your decision
provider API key, or Aura. Keep this project open in the editor with the tab connected: such a run shows in the tab as
one started from the prompt box would (from the Settings page too; the first-run setup, when open, stays until you
finish it), with **Started from Claude Code** (or **Started from Codex**, **Started from your coding agent**) under
its words, live and in its result, and in the history like any run. **Stop** and steering work on it as on any run.
The agent service runs one run at a time: while one goes, another can't start.

**Memory** at the bottom of the history shows how many earlier runs the agent remembers about this game (e.g. **· 12
runs**, or **· empty**). **Reset memory…** asks first, in place ("Reset memory?" / "The agent forgets what it has learned
about this game.", **Reset memory** or **Cancel**), then makes it forget, so the next run starts fresh; under the row
it says **Resetting memory…** (the button disabled), then for 8 seconds **Memory reset successful** or **Memory not reset:** and why. A
reset also takes the runs before it off the history: their files move to `Saved/Autopilot/Runs/.reset-backup/<time>/`
(kept, never listed again; the Settings page still counts their usage and bills). Not
while a run goes (its tooltip: "A run is going: reset memory once it is done").

## Settings

Open **Edit > Editor Preferences > Plugins > Autopilot**. These settings are per user: they are saved in
`Saved/Config/.../EditorPerProjectUserSettings.ini`, not in `Config/`, so they are never committed with the
project.

| setting | what it does |
|---|---|
| **Agent Service URL** | Where the agent service is. Left at the default `ws://127.0.0.1:8765/v1/bridge`, the agent service running on this machine, on whatever port it uses (`python -m autopilot serve` says where it listens in `Autopilot/brain.json` in your user settings folder). Use `wss://<host>/v1/bridge` for one you host elsewhere. |
| **Use with Aura** | Off by default. On, Autopilot connects to the agent service the [Aura](https://www.tryaura.dev/) app runs on this machine instead: runs then go through Aura, on your Aura plan, and what the agent learns about each game is kept with Aura across your team's machines. The first-run setup turns it on with **Use Aura app** (and off with the other choices). Off, Autopilot never connects to Aura's. |
| **Access Token** | Optional. If set, it is sent as `Authorization: Bearer <token>` when connecting, for a self-hosted agent service that asks for one. Leave it empty for a local one. If empty, the `AUTOPILOT_PLAYTEST_TOKEN` environment variable is used instead. |
| **Agent service command** | Empty by default: the agent service the plugin carries (a release download), else `autopilot` on `PATH`, else where its installer puts it. Set a full command line to run another, e.g. `C:\autopilot\.venv\Scripts\python.exe -m autopilot` for a repository checkout (`serve --port 0 --exit-with <pid>` is added). The first-run setup asks for it too when it finds no agent service. |
| **Default Explore Minutes** | The minutes of play of the first-run setup's Explore run (10 minutes unless you change it). |
| **Design Brief** | Optional. How the game is meant to work, i.e. the rules players are told ("a crop grows after 3 waterings; the stall buys ripe crops only"). Explore reports behavior that differs from it as a bug. Sent with every run the prompt box starts, for the agent to use should the run be an exploration. |
| **Show Policy Log** | Ask for the policy log when connecting. Takes effect at the next connect. |

Two choices are machine-wide rather than per project, in `Autopilot/preferences.json` in your user settings folder
(written by the [first-run setup](#first-run-setup) and the Settings page; shared with Autopilot for Unity and Godot):

| key | what it does |
|---|---|
| `share_data` | Share runs' data (default `true`); see [Data sharing](#data-sharing). Sent as `share` in every connection's hello. `-PlaytestShare=0` or `1` overrides it for a command-line run. |
| `access` | How you chose to run the agent in the setup: `aura` or `own_keys`. The agent service reads it before every run (`aura`: runs go through Aura, on the sign-in it keeps). |
| `onboarded` | The setup was finished or skipped. |

The setup's API keys go to `Autopilot/autopilot.env` in the same folder: `TYPESAFE_API_KEY=...` for the decision provider,
`AUTOPILOT_AGENT=claude`, `=codex` or `=opencode` for the Coding Agent, for OpenCode only `AUTOPILOT_OPENCODE_PROVIDER=` the LLM
provider picked (`anthropic`, `openai`, `opencode` for OpenCode Zen, `openrouter`, `deepseek` or `google` for Gemini),
and the LLM key under its provider's name: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `OPENCODE_API_KEY`,
`OPENROUTER_API_KEY`, `DEEPSEEK_API_KEY` or `GEMINI_API_KEY` (Claude Code's is Anthropic's, Codex's OpenAI's). A key left
empty keeps its line; other providers' key lines are kept too, and so is `AUTOPILOT_OPENCODE_PROVIDER` when you pick Claude
Code or Codex. `autopilot serve` reads the
file when it starts and before each run, over its own environment. Other lines in it are kept. The agent service's
logs, and its installer's, are in `Autopilot/logs/`.

## Data sharing

When sharing is on (the default), "Enabling this sends recordings, screenshots, and agent traces and memories to the
developers of Autopilot. For runs from your own Claude Code or Codex, the agent traces are its conversation during the
run." (the agent's traces include the parts of your code it read); the text export of the game's
code never is. Runs
through Aura also follow your Aura Privacy setting. Turn it off on the Settings page (**Share runs to improve Autopilot**) or in the
first-run setup; the plugin tells the agent service in its hello, so a change applies from the next connection (the tab
reconnects by itself when no run is going). For a command-line run, `-PlaytestShare=0` turns it off.

## Recording and screenshots

While a run plays, the agent service can ask the plugin for a recording of the game (a few JPEG frames a second) and for
stills at moments that matter, kept with the run's data when it is shared. The picture is what the player sees: the
game viewport with its HUD and UMG widgets, in Play In Editor only the PIE viewport (never the rest of the editor),
in a game its window. The plugin never lets this slow the game: the picture is read back from the GPU without waiting
for it, and scaled and encoded on worker threads; a frame whose turn comes while the one before is still being encoded
is skipped. Frames are only sent while the game runs and isn't paused.

It needs a game that renders, in a window that is drawn:

- A run with `-nullrhi` (or with no game running) has no picture: the plugin says so and the run goes on without
  recording.
- While a run plays, the editor keeps its pace in the background and when minimized (the plugin turns the editor's
  slowdown off for the run, never changing Editor Preferences): Play In Editor goes on playing and recording. A
  standalone game's own window must stay shown: a minimized or hidden one isn't drawn, so it has no picture.
- The back buffer formats read are 8-bit RGBA/BGRA, 10-bit (HDR output) and 16-bit float.

## What the agent can do in your game

The agent uses the game's own controls, whichever input system the game uses (Enhanced Input actions, or
the legacy action and axis mappings in Project Settings > Input). It also uses the game's UI: buttons, tabs,
item slots, list entries, check boxes and combo boxes can be clicked, right-clicked and dragged, even when
the game runs headless.

Besides that, the plugin runs bug detectors inside the engine every frame, so short glitches between the
agent's steps are caught too:

| detector | fires when |
|---|---|
| `log_error` / `ensure` | an Error-level log line, an `ensure` failure, or a Blueprint runtime error such as "Accessed None" |
| `nan` | an actor's transform is NaN or infinite |
| `out_of_world` | an actor is below KillZ or outside the world bounds |
| `teleport` | a pawn moves farther in one frame than its speed allows |
| `long_fall` | a character keeps falling for 8 s |
| `hitch` | a frame takes longer than 0.75 s |
| `actor_growth` | the number of actors of one class keeps growing for 2 minutes |
| `clamp` | a project property is outside its `ClampMin`/`ClampMax` |
| `over_max` | a project number `X` is above its sibling `MaxX` |
| `hud_fraction` | on-screen text shows `a/b` with `a > b` (e.g. a bag showing `6/5`) |

The agent service adds its own checks on top, confirms findings by replaying them, and can turn them into
claims to verify.

## Headless and CI runs (command line)

Start the game (or the editor) with `-Playtest=<kind>`. The plugin connects to the agent service on its own,
asks for the run, writes the report and exits with a code your CI can check. For example, with the agent
service already running on the same machine:

```
UnrealEditor-Cmd.exe C:/Path/To/MyGame.uproject -game -nullrhi -unattended -nosplash -log ^
    -Playtest=explore -PlaytestMinutes=30 -PlaytestReportDir=C:/ci/playtest
```

`-nullrhi` runs the game without rendering or a window, which is what you want on a build machine.

| argument | meaning |
|---|---|
| `-Playtest=explore\|verify\|goal` | the kind of run (required to start a command-line run) |
| `-PlaytestClaim="Verify that ..."` | the claim, for `verify` |
| `-PlaytestGoal="..."` | the goal, for `goal` |
| `-PlaytestMinutes=N` | the time budget, for `explore` |
| `-PlaytestTimeLimit=N` | the whole run's time limit in minutes, for `verify` and `goal` (default: the agent service's, 30 minutes) |
| `-PlaytestBrief="..."` or `-PlaytestBriefFile=<path>` | the design brief (default: the **Design Brief** setting) |
| `-PlaytestUrl=<ws url>` | the agent service (default: the **Agent Service URL** setting, else `ws://127.0.0.1:8765/v1/bridge`) |
| `-PlaytestToken=<token>` | the optional access token (also read from `AUTOPILOT_PLAYTEST_TOKEN`) |
| `-PlaytestReportDir=<dir>` | where to write the report (default `Saved/Playtest/<date-time>/`) |
| `-PlaytestConnectTimeout=<seconds>` | how long to wait for the agent service to take the run (its welcome), default 120; `0` waits forever. When it passes, `result.json` gets the `error` and the game exits with 1 |
| `-PlaytestStayOpen` | don't exit when the run is over |
| `-PlaytestShare=0\|1` | whether this run's data is shared (default: `share_data` in `preferences.json`, else on); see [Data sharing](#data-sharing) |
| `-PlaytestSteps=N`, `-PlaytestAttempts=N` | passed on to the agent service; the current one ignores them |

The report folder gets `result.json`: the run's result (verdict or goal, the reason, the evidence, the
findings), plus any files the agent service sends with it.

Exit codes:

| code | meaning |
|---|---|
| `0` | CONFIRMED, goal achieved, or exploration finished (findings are in `result.json`; they don't fail the run) |
| `2` | DENIED |
| `3` | INCONCLUSIVE |
| `1` | goal not achieved, or an error (for example the agent service refused the run, or didn't take it within `-PlaytestConnectTimeout`) |

The agent service sets the code in the result (`exit_code`); the plugin uses it. While it can't reach the agent
service, the plugin retries until `-PlaytestConnectTimeout` passes. Once the run has started, a dropped connection
is retried without a limit, so still give the CI step a timeout.

## Security

The agent service can call functions and set properties in the running game through this plugin, so:

- The runtime module is **never built into Shipping** builds (`TargetConfigurationDenyList: Shipping`).
- It stays **inactive** until the editor's first-run setup is done (then the editor connects by itself; not an
  unattended editor), or you pass `-Playtest` on the command line. Only the editor starts the agent service; a game never does.
- It only makes **outbound** connections, to the URL you set. It opens no port.
- Only connect it to an agent service you trust.

Everything the agent reads from the game (property values, on-screen text, log lines) is sent to the agent
service, and from there to whichever model provider it is configured to use.

## Removing Autopilot

**From this computer**: the Settings page's last section, **Uninstall Autopilot**, has **Uninstall** (not while a run
goes). It asks first, in place (**Uninstall Autopilot from this computer?**, "Removes the agent service, your keys,
your Aura sign-in and your setup choices, for every project.", **Uninstall** or **Cancel**), with **[Optional] Delete Autopilot's memories for all projects in this machine across all editors. I understand that this action is
destructive and deletes Autopilot's memories for my entire team.** (unchecked each time it asks). Then it runs `autopilot
uninstall --yes` (with `--keep-memory` when that is left unchecked: the memories are kept), hidden, from the temporary
folder, with its output in `autopilot-uninstall-<date-time>.log` there. That removes, for every project and editor on
the computer: the agent services running (never the Aura app's), your Aura sign-in (ended on Aura's side too), the
settings folder (`Autopilot/` in your user settings folder: keys, setup choices, logs and, when checked, what
Autopilot learned) and the `autopilot` command. When it is checked, the memories are deleted on this computer and also
the copy kept with Aura for your team (and this install's copy kept for your own keys), and it also removes what each
game's plugin keeps about the runs in its project (below: the run history, reports, recordings and the codex export),
for every project the agent service has run, and the tab forgets its runs at once. Shared runs not sent yet are sent
first (those that can't be sent now go with the rest); shared run data already sent is never deleted. Without the agent
service installed, the plugin removes the settings folder itself (`memory/` too, when checked). uv and Bun, which the
installer may have added, stay: other programs may use them.

While it works the tab says **Uninstalling Autopilot from this computer…**, in place of everything else; then
**Uninstallation successful**, under it what is left to do (*To finish, close the editor and delete Plugins/Autopilot/
from each project that has it.*), with **Set up again** (the first-run setup from step 1) and **View logs** (**View logs**
first when the log names files it couldn't remove), or **Couldn’t uninstall Autopilot from this computer.** with
**View logs**, **Try again** and **Cancel**. Until you press **Set up again** or restart the editor, the
tab starts, installs and connects to no agent service.

From a terminal, `autopilot uninstall` does the same (it lists what it removes and asks first; `--yes` doesn't ask,
`--keep-memory` keeps what Autopilot learned). From a repository checkout, `python -m autopilot uninstall` removes
what is outside the checkout; delete the checkout for the rest (its `memory/`, `.env` and `.venv`).

**From a project**: close the editor, then delete `Plugins/Autopilot/`. Nothing else in the project needs to change: the
plugin never adds itself to the `.uproject` (if you listed it there, take that entry out too, or the editor asks about
a missing plugin) and writes nothing in `Config/`. What it leaves in `Saved/`, all of it safe to delete (Uninstall
removes all but the settings, unless the memories are kept):

- `Saved/Config/<platform>Editor/EditorPerProjectUserSettings.ini`: the `[/Script/AutopilotPlaytest.PlaytestSettings]`
  section (the plugin's settings, including an access token if you set one);
- `Saved/Autopilot/Runs/`: the run history the tab shows;
- `Saved/Playtest/`: each run's report and its recording;
- `Saved/AutopilotCodex/` and `Saved/Logs/AutopilotCodexExport.log`: the Blueprint export the agent reads.

## Troubleshooting

- **"Can’t reach the agent service, retrying"** The service isn't running, or is on another port or
  address. When the editor starts it (the Agent Service URL at its default, not with Aura), the tab says why it
  couldn't (not installed; **The agent service stopped**, where **View logs** shows its output). Otherwise check that
  `autopilot serve` is running and that **Agent Service URL** (under **Advanced**) matches its port. A firewall can also block `localhost` connections in
  rare setups.
- **"No agent service found."** The plugin has no installer (a repository checkout) and found no `autopilot`. Set
  **Agent service command** (under **Advanced**, or in the setup's step 2; e.g. the repository's
  `.venv\Scripts\python.exe -m autopilot`), or start it yourself with `autopilot serve`.
- **"Couldn't install the agent service."** **View logs** shows the installer's output
  (`Autopilot/logs/install-*.log`); it needs the internet the first time (it fetches uv and Python).
- **A refusal message, shown in red.** The agent service answered with an error, e.g. a bad or missing
  access token (check **Access Token** or `AUTOPILOT_PLAYTEST_TOKEN`), or a protocol version mismatch (update the
  plugin and the service together). Press **Try again** after fixing it.
- **The policy log stays empty.** It is only sent to an editor on the same machine as the agent service,
  unless the service runs with `AUTOPILOT_INSPECT=1`. Also check **Show Policy Log**, then reconnect.
- **Reset memory… is refused.** It only works between runs: wait for the run to finish (or press **Stop**),
  then try again.
- **The plugin doesn't load in a Blueprint-only project.** Use the packaged build from `RunUAT BuildPlugin`
  (it includes `Binaries/Win64`), not the bare source.
- **Building from the editor fails while the editor is open.** Live Coding blocks normal builds of an open
  editor. Close the editor, or use `RunUAT BuildPlugin`, which builds into a separate folder.
- **Logs.** Everything the plugin does is logged under the `LogPlaytest` category, with lines prefixed
  `[Autopilot]`.

## Building

To build the plugin on its own (for example to check a change, or to ship prebuilt binaries):

```
"C:\Program Files\Epic Games\UE_5.8\Engine\Build\BatchFiles\RunUAT.bat" BuildPlugin ^
    -Plugin=C:\path\to\repo\plugin\unreal\Autopilot\Autopilot.uplugin -Package=C:\Temp\AutopilotBuild ^
    -TargetPlatforms=Win64 -Rocket
```

Use a short `-Package` path: deep paths can go over Windows' path length limit.

**An engine built from source.** Use that engine's `RunUAT.bat` and replace `-TargetPlatforms=Win64 -Rocket` with
`-NoTargetPlatforms`: that builds the editor's binaries only, since your game builds compile the plugin's `Source/`
themselves (otherwise BuildPlugin compiles the whole engine for the game, twice). The binaries load only in the engine
that built them. In a C++ project you can skip this: put the plugin in `Plugins/Autopilot/` and build your editor
target. To make a whole download (plugin and agent service) for such an engine, run
`tools\build_unreal.ps1 -EngineDir <its folder>` from the repository (it uses `tools/package_release.py`'s
`--engine-dir`).

## Source map (for contributors)

The plugin has three modules:

**`Source/AutopilotPlaytest`** (runtime, never in Shipping): the bridge itself.

| file | what it holds |
|---|---|
| `Public/PlaytestSubsystem.h`, `Private/PlaytestSubsystem.cpp` | `UPlaytestSubsystem`: the WebSocket connection, the `hello` handshake, reconnects, the command-line run and exit codes, run reports, the event queue and heartbeats, and the policy log messages |
| `Public/PlaytestSettings.h` | `UPlaytestSettings`: the per-user settings listed above |
| `Private/PlaytestOps.h` | declarations shared by the request handlers below: every `op` the agent service sends, one file per family |
| `Private/PlaytestOps.cpp` | `Handle`, which routes each request to its op's handler, and small helpers the handlers share |
| `Private/PlaytestOpsRc.cpp` | `rc`: Remote Control compatible calls, property reads and writes, descriptions |
| `Private/PlaytestOpsSession.cpp` | `context`, `session` (start, stop, restart; in the `map` asked for), `levels`, `pause`, `timestep`, `ping` |
| `Public/PlaytestLevels.h`, `Private/PlaytestLevels.cpp` | the project's levels, and the one a run asks for by path or name (Play In Editor's map override) |
| `Private/PlaytestOpsInput.cpp` | `input`, `move`, `key`: input actions, movement, and keys held between frames |
| `Private/PlaytestOpsUI.cpp` | `screen`, `ui`, `ui_act` |
| `Private/PlaytestOpsView.cpp` | `view`: what the player sees and what its body feels |
| `Private/PlaytestOpsOpenings.cpp`, `Private/PlaytestOpsSonar.cpp` | view's `openings` and `ways_up`, and its `sonar` |
| `Private/PlaytestOpsLook.cpp` | how a thing is drawn (meshes, effects, lights, animation), for `view` and `watch` |
| `Private/PlaytestOpsWatch.cpp` | `watch`: what changes over a stretch of game time |
| `Private/PlaytestDetectors.cpp` | the in-engine bug detectors |
| `Private/PlaytestScreen.cpp`, `Private/PlaytestUI.cpp` | reading what is on screen (widgets and text) and clicking or dragging game UI |
| `Private/PlaytestCodex.cpp` | the codex ops: exporting the project's game logic and data for the agent to research |
| `Private/PlaytestMedia.cpp` | `record` and `capture`: the game viewport's part of its window's back buffer, read back with `FRHIGPUTextureReadback` on the render thread, scaled and JPEG-encoded on a pool thread |
| `Public/PlaytestPreferences.h`, `Private/PlaytestPreferences.cpp` | `preferences.json` (setup, data sharing), `autopilot.env` (the setup's keys, Coding Agent and LLM provider: written, merged, read back) and the agent service's `usage` counts |
| `Public/PlaytestAura.h`, `Private/PlaytestAura.cpp` | Use with Aura: where the Aura app's agent service listens, starting Aura; the user settings folder (`SettingsDir`) |
| `Public/PlaytestAgentService.h`, `Private/PlaytestAgentService.cpp` | the agent service the editor starts: finding the `autopilot` command, its start and install command lines, `brain.json` and its process, the logs; `FPlaytestAgentService` starts, installs and watches it |
| `Public/PlaytestAccount.h`, `Private/PlaytestAccount.cpp` | the `account`, `aura_sign_in` and `bill` frames (how runs go, the keys, Coding Agent and LLM provider BYOK runs use, the Aura sign-in the agent service does, what Autopilot billed through Aura), the setup's Coding Agents and LLM providers, and how sums of money show |
| `Public/PlaytestInspect.h`, `Private/PlaytestInspect.cpp` | parsing the policy log messages (`jev`, `memory`, `memories`) off the game thread |
| `Public/PlaytestJson.h`, `Private/PlaytestJson.cpp` | JSON reading and writing, and converting Unreal properties to and from JSON |
| `Private/PlaytestInternal.h` | declarations shared by the files above |
| `Private/PlaytestInspectTests.cpp` | automation tests for the policy log parsing |
| `Private/PlaytestPreferencesTests.cpp` | automation tests for `preferences.json`, `autopilot.env` (with the Coding Agent and LLM provider picked: each provider's key line, Claude Code's and Codex's fixed ones, empty keys, other providers' lines kept) and the usage counts |
| `Private/PlaytestAgentServiceTests.cpp` | automation tests for finding, starting and installing the agent service (with stand-in processes), which one a release download runs (its own: installed or updated first, PATH ignored, an older one running replaced; only `autopilot` or Python programs stopped), the account frames (with `coding_agent` and `llm_provider`, the dropdowns' start and the saved keys' line), a sign-in picked up again after the connection comes back and the bill (frames, money); removing Autopilot: `autopilot uninstall`'s command lines, the settings folder removed by the plugin when none is installed (the Aura app's file kept, memory/ when asked), nothing started or installed until Set up again (`AgentService.Uninstall`), and the command run from the temporary folder, its exit code shown (`AgentService.UninstallProcess`, with a stand-in) |

**`Source/AutopilotPlaytestEditor`** (editor only): the Autopilot tab.

| file | what it holds |
|---|---|
| `Private/AutopilotPlaytestEditorModule.cpp` | registers the tab and toolbar button, and starts or stops Play In Editor for the agent |
| `Private/AutopilotSession.h/.cpp` | `FAutopilotSession`: the runs, their feed, results, usage and bills, saved to `Saved/Autopilot/Runs/`; also keeps internal wording out of the UI |
| `Private/SPlaytestPanel.h/.cpp` | the tab's main widget: first-run setup (account, agent service, privacy settings, first run), connect card, composer, live view, result, Settings page with its account corner |
| `Private/SPlaytestPolicyLog.h/.cpp` | the policy log view |
| `Private/SPlaytestVideo.h/.cpp` | a run's recording in its result: the Media Framework player (`UMediaPlayer`, `UFileMediaSource`, `UMediaTexture` through a brush), letterboxed in its screen (230 px; 200 narrow), with its controls |
| `Private/AutopilotPanelTests.cpp` | automation tests: every page laid out at 320 to 1400 px wide (nothing past the edges; Uninstall asked, and what the tab shows while and after it uninstalls; the BYOK keys and the Coding Agent stacked, the LLM provider dropdown and key on one row, each key's hint and each provider's name fitting its box, with each agent; the Settings page with long bills, BYOK with and without keys, with each agent and LLM provider), where the dropdowns start, picks over them, the provider each agent locks, the LLM key emptied when its provider changes, and a run's bill kept, saved and summed; every stage name of the Live page given the room it needs, the steer box taking words at every stage (its one hint fitting the box), Steer at the Rollout only, its words kept, the composer's hint fitting its box, the kind's tag and **Duration** following the words (no explaining lines), the gear beside **New run** at every width (no **Rerun setup** outside the Settings page), **Harness attempts** on the Live page, the Settings page's billing period, no usage table, this project's runs with their bills, what sharing sends (as the setup says it) and no time limit (each run's is the prompt box's), the result with and without a recording (the line saying why there is none wrapping inside the tab, never while the run goes on or with a player) and with and without findings (the agent's **Found**, **Engine findings · n** with ×n; nothing with none); the kind of run each text asks for and its start frame (`Editor.RunKind`); the end of a run: "Saving what it learned to memory" as the report, the memory line, the recording copied and saved, why a run has none (as the agent service said; else nothing) decided, saved and read back, the history's harness attempts (`Editor.RunEnd`); steering only at the Rollout, stage by stage as the status lines come (`Editor.Steer`); the gears opening and closing the Settings page, **Rerun setup** on it (one button, whatever the account) opening the setup at step 1, disabled while a run goes, BYOK as the mode's one name, the setup's privacy words (what sharing sends) and no explaining paragraphs in any step (`Editor.RerunSetup`); the connect card's states, each with its words and buttons (**Reconnect** after Disconnect, never a **Connect** to press, **Stop**), **Advanced**, no Aura line (`Editor.Connect`); the status line (said, gone after 8 s), the Feed tab's steer box saying the Live page's words, **Back to the last run** (`Editor.StatusLine`); the Aura card's **Use Aura app** before **Sign in with browser**, Explore's 10 minutes, step 3's **Next** or **Finish** and the stepper's 4th step by what the memory holds, step 4 waiting (**Skip**), offering the Explore run with the memory empty (**Explore and finish** sending the composer's start frame for "Explore" with the minutes, not while a run goes; **Skip and finish**) and closing by itself when the memory holds something (`Editor.FirstRun`); step 4 laid out at every size, waiting and offering (`Editor.Layout`); **Uninstall Autopilot** last on the Settings page, asked in place (what it removes, **Delete Autopilot's memories** unchecked each time it asks), not while a run goes, and what the tab shows while and after it removes, in place of the setup too, with **View logs**, **Set up again** (the setup's step 1) and **Cancel** (`Editor.Remove`); a run the user's own Claude Code or Codex started (`run_started`): a run of the tab's, shown from wherever the tab was (the Settings page too; the first-run setup stays), its words and **Started from Claude Code**, **Codex** or **your coding agent** under them, live and in its result, kept in the history; nothing while a run goes; the card **Claude Code and Codex** after Data sharing and before Uninstall, its words in order, each command's **Copy** copying it; what sharing sends for those runs (`Editor.RunStarted`); the activity feed: each `activity` frame a line under its stage (which drives the stage track), a call and its result one line with two folds, a frame sent again changing nothing, an orphan result its own line, the Feed tab's headings with their counts, the folds' words, what the hello asks for (`activity_since`), a run saved and read back with its events (`Editor.Activity`) |
| `Private/AutopilotStyle.h/.cpp`, `Private/AutopilotWidgets.h` | the tab's look, Aura's "The Flatness" (dark only: flat fills, 1px rules, one amber accent `#f0873a`; every color a named token), its fonts (sizes in the design's px), icons, brushes (8px corners on plates, square controls, a pill only for the switch) and small shared widgets (buttons, tags, plates, the switch, the check box, the spinner, tooltips) |

**`Source/AutopilotCodex`** (editor only): the `AutopilotCodex` commandlet, which exports a project's Blueprints
(as pseudo-code), input assets, data tables and maps as text without the game running:

```
UnrealEditor-Cmd.exe <project>.uproject -run=AutopilotCodex -out=<dir> [-root=/Game[,/Other]] [-maxmaps=N] [-nomaps]
```

It lives in its own module because commandlets are looked up before `PostEngineInit` modules load.
`python -m autopilot codex` runs it for you.

| file | what it holds |
|---|---|
| `Private/AutopilotCodexCommandlet.h/.cpp` | the commandlet: `Main` finds the assets and writes each file and `summary.txt` |
| `Private/AutopilotCodexInternal.h` | declarations shared by the files below |
| `Private/AutopilotCodexText.cpp` | names, types and property values as text, and the properties that differ from a class's defaults |
| `Private/AutopilotCodexBlueprints.cpp` | `blueprints/*.txt`: a Blueprint's components, variables and defaults, and its graphs as pseudo-code |
| `Private/AutopilotCodexInputData.cpp` | `input/*.txt` and `data/*.txt`: input actions and mapping contexts, enums, structs, curves and data assets (`Main` writes data tables itself) |
| `Private/AutopilotCodexMaps.cpp` | `maps.txt`: the actors placed in each map |

`Resources/` holds the tab's icons and fonts, IBM Plex Sans (Regular, Medium, SemiBold) and IBM Plex Mono (Regular,
Medium), Aura's, and JetBrains Mono for the marks those two lack (● ◀ ▶ ◂ ▾) (the fonts are under the SIL Open Font
License; see the `*-OFL.txt` files next to them).
