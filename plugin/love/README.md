# Autopilot for LÖVE

The LÖVE module of Autopilot. It lets the Autopilot agent play your [LÖVE](https://love2d.org/) game: explore it,
test it, verify a claim ("Verify that the score goes up when a coin is taken") or play toward a goal, and report.

The module is the agent's eyes and hands inside the game: it answers questions about the game (where things are, what
text is on screen, the game's Lua values), presses keys and the mouse the way a player would, and gives the agent your
game's Lua files to read. The agent itself runs on your machine as a separate Python program (`autopilot serve`, see
[Running from a clone](../../CONTRIBUTING.md#running-from-a-clone)); the module connects out to it over a WebSocket.

It is plain Lua on what LÖVE ships (luasocket, LuaJIT's ffi and bit), with no other library. It needs **LÖVE 11**
and is tested with LÖVE 11.5.

**Status: a first version.** LÖVE has no editor, so there is no dock: runs start from the command line (below) or
from the agent (`autopilot play "..."`, your own Claude Code or Codex through `autopilot mcp`).

## Install

1. Copy this folder's `autopilot/` into your game's folder (beside `main.lua`; keep the name).
2. Require it first thing in `main.lua`:

   ```lua
   require("autopilot")
   ```

That is all. Started without playtest arguments, the module does nothing at all: no socket, no hooks, your game as it
was. To keep it out of a release, leave `autopilot/` out of the `.love` you ship (the `require` then needs a `pcall`).

## Run it

Start the agent service, then the game with a run:

```
autopilot serve
love path/to/game --playtest=verify --playtest-claim="Verify that the score goes up when a coin is taken"
```

or let the agent drive the game (it asks for what it wants):

```
love path/to/game --playtest-url=ws://127.0.0.1:8765/v1/bridge
autopilot play "Explore the game and tell me everything that looks wrong"
```

| Argument | Meaning |
|---|---|
| `--playtest=verify\|explore\|goal` | The kind of run. Without it the game only connects (`--playtest-url`) and the agent drives it. |
| `--playtest-claim=...`, `--playtest-goal=...` | The claim (verify) or the goal (goal). |
| `--playtest-minutes=N`, `--playtest-time-limit=N` | An exploration's minutes; a verify or goal run's whole time limit. |
| `--playtest-brief=...`, `--playtest-brief-file=<path>` | How the game is meant to work: an exploration reports what differs from it. |
| `--playtest-url=ws://...` | The agent service. Default: `AUTOPILOT_PLAYTEST_URL`, else the one running on this machine (`Autopilot/brain.json`), else `ws://127.0.0.1:8765/v1/bridge`. Only `ws://` (LÖVE has no TLS). |
| `--playtest-token=...` | A bearer token to send (default: `AUTOPILOT_PLAYTEST_TOKEN`). |
| `--playtest-report-dir=<path>` | Where `result.json` and the report files go. Default: `playtest/<time>/` in the game's save folder. |
| `--playtest-connect-timeout=<seconds>` | How long to wait for the agent service's welcome (default 120); then the game exits with 1. |
| `--playtest-stay-open` | Don't quit when the run is over. |
| `--playtest-share=0\|1`, `--playtest-research=0\|1` | Share this run's data; run the research agent (default: `Autopilot/preferences.json`). |
| `--playtest-log=<path>` | Also write what the module prints to this file. |

Godot's and Unreal's spellings work too (`-PlaytestUrl=...`): any argument whose name, ignoring dashes, underscores
and case, starts with `playtest`. `AUTOPILOT_PLAYTEST_AUTOCONNECT=1` connects without arguments.

**Exit codes**: the result's `exit_code` when it has one; otherwise **0** CONFIRMED (or achieved, or explored with no
bugs), **2** DENIED (or bugs found), **3** INCONCLUSIVE, **1** an error (no welcome in time, an error from the agent
service, the connection lost before the result: `result.json` then says what went wrong).

**No display?** LÖVE has no headless mode. On Linux, run it under `xvfb-run -a love ...` (the agent's bench does so
by itself).

## Helping it find your things (optional)

LÖVE has no scene tree, so the module looks for the game's things itself: tables that have a place (numeric `x` and
`y`, a `pos` / `position` table, or a love.physics body), reachable from the globals or from main.lua's locals that
the `love.*` callbacks use (`local state, ui = ...`: found as `/@local/state/...`), the bodies of the physics worlds
the game makes, and a global `player` (or `hero`, `ship`, or a thing whose class or faction says hero or player) for
the player. Tables with a life of their own (`hp`, `alive`, `state`, a velocity) come first; the rest (a coin, but
also a sprite's rectangle) after them. Most games need nothing more. When yours keeps its things in a module's locals,
or the guess is wrong, tell it:

```lua
local autopilot = require("autopilot")
autopilot.player(player)                     -- the table that is the player
autopilot.track(enemy, "Enemy")              -- a thing, and its class (game.actors("Enemy"))
autopilot.track(door, "Door", {center = true})  -- its x, y is its middle, not its top left
autopilot.untrack(enemy)                     -- gone
autopilot.camera(function() return cam.x, cam.y, cam.scale end)  -- the middle of the view, and its zoom
```

These calls cost nothing when no run is going.

## What the module answers

The wire protocol is the same for every engine (`autopilot/bridge.py`). What is particular to LÖVE:

| Request | In LÖVE |
|---|---|
| `context` | `character`: the player's path; `dimension` `2d`; `engine` `love`; `project_dir`: the game's folder; `locals`: main.lua's locals that are searched; `controls`: the keys the game's Lua reads (`love.keyboard.isDown("a")`, `key == "space"`), as LÖVE names them, with the agent's names. `{"player": path}` makes that thing the player. |
| `session` | `restart` starts the whole game over (`love.event.quit("restart")`: every global and module, from `main.lua`): the connection closes as coming back (1012) and the new game answers the request on the same session. No scenes: a `map` is a 404. |
| `find`, `transforms`, `pose`, `movement`, `view`, `watch` | Over the things described above, the live ones first (`view` marks the others `prop`, and leaves out the members of big collections of data). Paths are keys from the global table (`/player`, `/enemies/3`) or from main.lua's locals (`/@local/game/...`); a class is what `autopilot.track` named, a class's name (middleclass), a `type`/`kind`/`tag` field, a metatable's `__name`, or the table a thing is in. A table with a width (`w`, `h`) has its top left at `x`, `y`; a circle (`r`, `radius`) or a point its middle. `movement` reads the player's `speed`, `jumpForce` / `jumpHeight` and `gravity` fields (or its physics world's gravity). |
| `rc` | Any Lua value by path: fields (nested with `.`), `position` (a thing's place), function calls with parameters by name (from the function's definition; a first parameter `self` makes it a method), `describe` (fields and functions, a class's methods too). |
| `key` | Unreal key names or LÖVE's, delivered both as events (`love.keypressed`, `love.keyreleased`, `love.textinput`, `love.mousepressed`...) and as state (`love.keyboard.isDown`, `isScancodeDown`, `love.mouse.isDown`). A press stays down 0.05 s and 2 frames. Holds are leased as in the other engines; a hold with `seconds` lets go after that much game time. |
| `input` | An error: LÖVE has no input actions. |
| `move` | `keys` (the default): W/A/S/D, or the arrows when the game's code doesn't read W/A/S/D, toward the direction; `controller`: the player's place moved directly at its walk speed. |
| `pointer` | The mouse at a place on screen, a kit point or a thing: `love.mouse.getPosition` reports it and a `love.mousemoved` comes. The real cursor never moves. |
| `screen` | What the game drew last frame with `love.graphics.print` / `printf`, keyed by where it landed on screen (`Text@10,10`). Pieces side by side on a line are one text (glyphs a terminal-styled game prints one at a time read as words; a shadow isn't the text twice). |
| `ui`, `ui_act` | No controls (`ui` is empty, `ui_act` a 404): a LÖVE game draws its own. |
| `pause` | The game's `love.update` isn't called (drawing and the module go on). |
| `timestep` | `fixed`: every frame's dt is 1/`fps` (`love.timer.step`). |
| `record`, `capture` | The window, as the player sees it (`love.graphics.captureScreenshot`). LÖVE 11 encodes no JPEG, so the pictures go as PNGs: `png` where other engines put `jpeg`, and the brain makes each a JPEG as it arrives. The main thread only asks for the frame; scaling to `max_dim` (a box filter), the PNG and its base64 are made in a thread of the module's own, and a frame due while the one before is still there is skipped (a gap in `n`). No frames while paused. A recording ends with its connection (a restart: the brain asks again). No window: 409. |
| `codex_manifest`, `codex_read` | The game's `.lua` files and shaders (`.glsl`, `.frag`, `.vert`), from its folder or `.love`, not its save folder, not folders starting with `.`, not `autopilot/`. Paths follow the protocol's rules (relative, no `..`). |
| events | `heartbeat` every second; `session` when the game (re)starts; `log` and a `log_error` finding for a Lua error in a callback (the frame is dropped and the game goes on, as an editor's debugger would let it), `hitch` (a frame over 0.75 s), `nan` (the player's place), while a run uses the game. |

**The frame hook**: the module wraps LÖVE's main loop (`love.run`, the default or the game's own, even one defined
after the `require`): every frame it answers the agent's requests and delivers its input, then runs the game's frame.
A game whose `love.run` doesn't return a frame function (LÖVE 0.10's blocking style) isn't hooked.

## Security

The agent can read and change any Lua value of the running game and call its functions, so connect only to an agent
service you trust. The module only connects out, to the address it is given, and does nothing until it is started
with playtest arguments.

## For contributors

| File | What it does |
|---|---|
| `autopilot/init.lua` | The entry: the command line, the frame hook, input (events and the keyboard and mouse state), the drawn texts, the link's frames, the ops, the detectors, a command-line run's report and exit code. |
| `autopilot/ws.lua` | A WebSocket client over luasocket that never blocks the game. |
| `autopilot/json.lua` | JSON, with Lua's arrays and objects kept apart. |
| `autopilot/world.lua` | The things, the player and the camera; `find`, `transforms`, `pose`, `movement`, `view`, `watch`. |
| `autopilot/rc.lua` | `rc`: reads, writes, calls and `describe`. |
| `autopilot/codex.lua` | `codex_manifest`, `codex_read`, and the keys the game's code reads. |
| `autopilot/media.lua` | `record` and `capture`: the frame from LÖVE, scaled and made a PNG in a thread of its own. |
| `autopilot/keys.lua` | The agent's key names as LÖVE's. |

### Running the tests

`tests/live.py` starts LÖVE on the test game, **Coin Field** (`tests/game/`: a square walking a field, coins, a
patrolling slime, a bell with a planted bug, a HUD with text printed glyph by glyph, birds and a sprite atlas kept in
main.lua's locals), against the brain's own bridge and kit: every op the brain needs,
the things and the player, the HUD's text, keys and `move`, `rc`, the codex, the planted bug as a `log_error`, a still
and a recording (PNGs from the game, a JPEG and an MP4 in the brain), a restart on the same session, and command-line
runs against a stand-in brain (the hello's run, the report files, exit codes 2 and 1). From the repository root, with
the repository's Python environment:

```
python plugin/love/tests/live.py
```

It needs `love` on `PATH` (or `AUTOPILOT_LOVE`) and, on Linux without a display, `xvfb-run`. The adapter's own tests
(`tests/test_love.py`) need no LÖVE and run with the rest (`pytest`).

## License

Apache-2.0, as the rest of the repository.
