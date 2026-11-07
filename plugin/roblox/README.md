# Autopilot for Roblox Studio

Autopilot is the Roblox Studio plugin of Autopilot. It lets the Autopilot agent play your Roblox game. A prompt is one of
four types:

- **Exploration**, e.g. "Explore the obby's second stage.": it plays for a few minutes and reports what looks wrong;
- **Testing**, e.g. "Go through the place and tell me everything that looks wrong.": it plays as an exploration, and its
  report says GAME TESTED;
- **Verification**, e.g. "Verify that the door opens when you pull the lever.": CONFIRMED, DENIED or INCONCLUSIVE, with
  evidence;
- **Goal**, e.g. "Collect every coin without falling off.": achieved or not.

For every prompt, Autopilot plays the place for you (as Studio's Play button does: a server and one player), reads the
place's scripts, tries things in the game and plays according to your prompt. When it is done, the play session stops by
itself and the dock shows the report.

The plugin is the agent's eyes and hands inside the game: it answers questions about the game (what is on screen, where
things are, what the game's scripts hold), presses keys and clicks the UI the way a player would, and gives the agent
your place's scripts and instances to read. The agent itself runs on your computer as a separate Python program
(`autopilot serve`, see [Running from a clone](../../CONTRIBUTING.md#running-from-a-clone)); the plugin connects out to
it.

It needs a current Roblox Studio (it uses `UserInputService:CreateVirtualInput`, `HttpService:CreateWebStreamClient`,
`StudioTestService` and `StudioDeviceSimulatorService`; tested with Studio 0.745 and 0.746) on Windows or macOS. For pictures of
the game (the run's recording, stills, `describe_frames`), turn on Studio's MCP server once (see [Setting up Autopilot
with Roblox Studio](../../docs/roblox-studio-setup.md#3-turn-on-studios-mcp-server)).

## Install

1. Build the plugin (from a repository checkout): `python tools/build_roblox.py --install`. This writes
   `dist/roblox/Autopilot.rbxmx` and copies it into Studio's local plugins folder (`%LOCALAPPDATA%\Roblox\Plugins` on
   Windows, `~/Documents/Roblox/Plugins` on macOS). Or copy a downloaded `Autopilot.rbxmx` there yourself.
2. Open (or reopen) a place in Studio: Studio loads local plugins when a place opens. The **Autopilot** button appears
   in the Plugins tab, and the **Autopilot** dock on the right.
3. The first time a play session starts, Studio may ask whether the plugin may make HTTP requests or open a
   connection to `127.0.0.1`: allow it (it only ever talks to the agent service on your computer, or the address you
   give it).

Nothing is added to your place: the plugin keeps its settings and run history in Studio's own plugin settings (per user
and computer), never in the place file. While a run plays, it puts a small helper script into the running play session
(see [How Studio and the game work together](#how-studio-and-the-game-work-together)); that copy goes with the session
and is never saved.

## Use it from Studio

1. **Start the agent service** in a terminal: `autopilot serve` (installed), or `python -m autopilot serve` in the
   Autopilot repository. It listens on `ws://127.0.0.1:8765/v1/bridge` (and, for the plugin's HTTP fallback, on the next
   port, 8766). A Roblox plugin can't start programs or read files, so it can't start the agent service itself as the
   other engines' plugins do, nor find one that `autopilot serve --port 0` put elsewhere: when 8765 is taken, start it
   on another port (`autopilot serve --port 8770`) and set **Agent service URL** in the dock's Settings to match
   (`ws://127.0.0.1:8770/v1/bridge`).
2. **Connect**: the dock connects by itself when a place opens (retrying with a backoff of up to 15 s; **Connect** tries
   at once). The header shows `● CONNECTED`, and `▶ PLAYING` while a play session is attached.
3. **Account**: how runs go. **Sign in with Aura** asks the agent service to sign in to Aura in your browser (runs then
   go through your Aura plan); **Use my keys (BYOK)** takes a decision provider key (TypeSafe), the coding agent
   (OpenCode, Claude Code or Codex), the LLM provider and its key, and has the agent service save them in
   `Autopilot/autopilot.env` (the plugin itself can't write files: the agent service does it for it, the `save_setup`
   frame). Keys left empty keep the ones the agent service has.
4. **New run**: describe what to test in the prompt box and press **Run**. The words are sent as typed (a `start` frame
   with `{"kind": "auto", "prompt": ..., "time_limit_minutes": ...}`, and `design_brief` when the Settings' **Design
   brief** has words); the agent service reads which of the four types it is and the live page shows it (VERIFY, GOAL,
   EXPLORE, TEST). **Time limit** is the run's minutes in all (1-600, 30 by default), kept for the next run.
5. **Follow the run**: the stages (Launch, Analyze, Probe, Build harness, Play, Results, Report), the agent service's
   status line (**Waiting for the game to start** while the run's play session comes up), **Stop**, and **Steer** (a
   hint for the agent while the game plays, from the Play stage on). The **Feed** lists what the agent did and said, by
   stage; **+ the details** opens a line's code, output or text.  The **Policy log** (unless **Show Policy Log** is
   off), folded under its step count: the agent's policy steps in the game, one row each (step, game time, START,
   KEEP, SWITCH, HELD or DROPPED, the action chosen, its probability, ms), the last 100 shown, following the newest
   while scrolled to the bottom.
6. **Read the result**: the verdict (CONFIRMED, DENIED, INCONCLUSIVE; ACHIEVED, NOT ACHIEVED; EXPLORED, GAME TESTED),
   the reason, the evidence, alerts, the direct changes the run made to the game, and the engine's findings (script
   errors on the server and the client, parts fallen out of the world, long falls, hitches, HUD counts over their
   maximum) with **Not a bug** and **Show in level** (selects it in the Explorer). **Run again**, **Report (to copy)**
   (the report as text, selectable) and **Verify a fix** (fills the prompt box).  **Usage**: the run's tokens per
   model (the agent service's `usage` frames), kept with the run in the history.  (No ledger of more runs than the
   history, as Godot's Settings page keeps: Studio's plugin settings are small.)

**Studio needn't be in front while a run plays.** The plugin's keys, mouse, gamepad and touches go in through Studio's
virtual input, which reaches the game whether or not Studio's game view is the focused window: work in another app
meanwhile. (Should a key ever not reach the game, the plugin says so in Studio's output and the run's findings, and puts
the keys Roblox's own controls read in the way those controls do: W/A/S/D and the arrows walk, SpaceBar jumps, a
ProximityPrompt's key triggers the nearest prompt in reach.)

The **History** lists the last 30 runs (kept in Studio's plugin settings); pick one to show its result and feed again.
The **Memory** line says how many runs the agent remembers of this game; **Reset memory…** asks in place, then has the
agent forget them.

### Settings

| Setting | What it does |
|---|---|
| **Agent service URL** | Where to connect. Empty means `ws://127.0.0.1:8765/v1/bridge`. A changed address is used at once. |
| **Access token** · optional | Sent as `Authorization: Bearer <token>`, for an agent service that asks for one. |
| **HTTP fallback URL** | Optional HTTP base URL (for example `http://127.0.0.1:9000/v1/http/` with `autopilot serve --http-port 9000`). Empty uses the WebSocket port plus one. Used by the editor and Play server; see the client limitation below. |
| **Design brief** · optional | How the game is meant to work: its design, the rules players are told.  Explorations report what differs from it as a bug.  Sent with every run as the `start`'s `design_brief`, trimmed; kept as typed. |
| **Research agent** | Whether the agent service runs its research agent during runs (the `hello`'s `research`): more difficult goals in fewer attempts, at a higher cost.  Unset until you flip it, and the `hello` leaves `research` out (the agent service decides: on for its own, off for the Aura app's); the switch shows on until then.  Sent again at once, for the next run. |
| **Show Policy Log** | On by default: the Policy log, and the `hello`'s `inspect` (the agent service sends policy steps only when asked).  Sent again at once: applies to the run going on too. |
| **Data sharing** | Synchronized with the agent service’s shared privacy preference before runs. Changes are saved for every editor; an existing opt-out is preserved. |

### Runs from your own Claude Code or Codex

Set up with `autopilot connect claude` or `autopilot connect codex`, your own coding agent can start a run on the place
open in Studio (`autopilot mcp`, on your Claude or ChatGPT plan): the dock shows it as one of its own, with **Stop**,
steering and the result.

## How Studio and the game work together

(A multiplayer test is the same with more parts: its server and each player's client are Studio processes of their
own, unnamed in Studio's MCP server's list, each running the plugin. They find the pipe in the test's arguments
(`StudioTestService:GetTestArgs()`) when the plugin's settings don't say it, player 1's client answers as `client`, the
others as `client2`, `client3`..., and the pictures are player 1's window's.)

Studio runs a plugin in every DataModel it has: the place being edited (**edit**), and while a play session goes, the
session's **server** and its player's **client**. They can't reach each other inside Studio, so:

- the **edit** plugin holds the one connection to the agent service (its `hello` says `"engine": "roblox"`,
  `"host": "editor"`), shows the dock, plays and stops the place (`StudioTestService:ExecutePlayModeAsync`, as the Play
  button does; `EndTest` on the server to stop), answers `levels`, `codex_manifest` and `codex_read` from the place,
  and with no play session answers `context` and `ping` itself (`session_running: false`), `screen` (`{"texts": {}}`),
  `ui` (`{"elements": []}`), `timestep` and `detectors` (`{}`), as the other plugins' editors do; everything else gets
  409 ("No game is running"). Its hello's `features`: `levels`, `timed_hold`, `pointer`, `touch`, `gamepad`, `device`,
  `players` (and `media` when Studio gives the plugin pictures); its `input` is as `context`'s;
- the play session's **server** and **client** plugins join the agent service's **pipe** for that connection
  (`ws://<agent service>/v1/pipe/<session>`, autopilot/bridge.py: the address and key are left in the plugin's
  settings, which every DataModel shares). The edit plugin sends each request on to the side that answers it, through
  its own connection; the side answers the agent service directly. `rc` goes to the server (what the game's Scripts
  see and change) unless its path is the client's own (the player's PlayerGui and PlayerScripts, the camera) or it says
  `client:`; one the server has no instance for is passed on to the client. Every other request (view, screen, ui,
  ui_act, pointer, key, input, move, watch, find, transforms, pose, movement, context, ping) goes to the client: what
  the player sees and does. `pause` pauses both sides (each runs its own simulation);
- on each side the plugin puts a helper script into the running game (`AutopilotPlaytest`: a Script in
  ServerScriptService, a LocalScript in the player's PlayerScripts) through which it reads and calls the game's
  ModuleScripts: a plugin's own `require` runs a module again for the plugin alone, not the game's copy;
- **pictures**: a plugin can't read the game's pixels in today's Studio (see [Pictures of the game](#pictures-of-the-game)),
  so the agent service answers `capture` and `record` itself, through Studio's MCP server; `device` is answered by the
  editor with no play session and by the client during one;
- **sockets**: Studio lets each DataModel have only four WebSocket clients, every plugin's counted. The edit plugin
  needs one; when none is free it talks to the agent service over plain HTTP instead (long polls at the configured
  HTTP fallback URL, defaulting to the next port after the WebSocket's). A play session's client can make no HTTP requests at all (only a
  server can), so it needs one free WebSocket.

Findings and errors come from both sides: each runs its own detectors on its own log and physics.

## Reference: what the plugin answers

The wire protocol is the same for every engine (`autopilot/bridge.py`). This section lists what is particular to
Roblox.

### Paths and `rc`

- **Instances** are paths from the DataModel, the first part the service: `/Workspace/Map/Door`,
  `/ReplicatedStorage/Modules/Config`, `/Players/<Name>/leaderstats/Coins`. Siblings that share a name are numbered by
  their debug id, the same on the server and the client: `Coin@617854`; the plain name of twins is the one with the
  lowest number, and a numbered path stays that instance (or is gone). `\` escapes `/`, `@` and `\`. Also accepted: a
  dotted full name (`Workspace.Map.Door`), a path without the leading `/`, a service by name, and `LocalPlayer`,
  `Character`, `Humanoid`, `Camera`. `server:` or `client:` in front chooses the side.
- **Properties** (a read or write's `propertyName`), in turn: a property of the instance's class (from
  `ReflectionService`), an attribute, the `Value` of a child value object of that name, on a player or its character
  its `leaderstats` entry; `position` / `pivot` (a model's or part's pivot: written with `PivotTo`), `attributes`,
  `tags`, `children`. On a ModuleScript, the fields of the table it returns (dotted names go into nested tables). A
  read without `propertyName` returns all of them. Writing a name nothing has sets an attribute.
- **call**: an instance's methods, arguments by the reflection's parameter names or as a list (`args`); a
  ModuleScript's functions, arguments as `args` (or `"1"`, `"2"`...), `self: true` for a method. The answer has
  `ReturnValue` (and `ReturnValues` when there are more).
- **describe**: `{"Name", "Class", "Path", "Properties", "Functions", "Tags", "Children"}`; a module's fields and
  functions first.
- **Values** are Roblox's own: studs; Vector3 `{"x","y","z"}`; CFrame `{"position", "look", "up", "euler"}` (euler:
  `ToEulerAnglesYXZ`, radians); Color3 `{"r","g","b","hex"}`; an EnumItem as its name; an Instance as its path;
  BrickColor as its name; UDim2, NumberRange, Rect, sequences as objects and lists; tables as objects and lists
  (functions as `"<function>"`). Writes convert to the current value's type (or the property's declared type).

### The kit frame (every request but `rc`)

Roblox's frame is x right, y up, -z forward (a CFrame's LookVector at rest), studs, right-handed; the kit's is X
forward, Y right, Z up, centimetres. With 1 stud = 28 cm (Roblox's own conversion): kit `(X, Y, Z) = 28 * (-z, x, y)`.
Yaw is degrees from +X toward +Y (turning right positive) = `-deg(ry)` of `ToEulerAnglesYXZ`; pitch `deg(rx)`, positive
up; roll `-deg(rz)`.

### Requests

| Request | In Roblox |
|---|---|
| `context` | `world` = `/Workspace`, `character` = the local player's character, `controller` = the Player, `camera` = the current camera, `dimension` `3d`, `engine: "roblox"`, `project_dir` `""` (Studio doesn't say where a place's file is), `controls`: the game's ContextActionService actions with their keys, and Roblox's default controls; `camera_type`, `mouse_behavior`, `place_id`, `universe_id`, `input` (as the other plugins': `handling` `"roblox"`, `keys`, `actions`, `pointer` true, `move` `"stick"` (what `move`'s `auto` does), `notes` (the default camera turns only while the right mouse button is held, or the camera is Scriptable, or the game locks the mouse; point before clicking; Escape and a few console buttons are refused; a touch device; a key that didn't reach the game); and Roblox's own: `keys_reach`, `needs_focus` false, `keyboard`, `mouse`, `gamepad`, `touch`, `pad`: the sticks and triggers held; the hello carries it too), `net` (`{"mode": "client", "rc_side": "server"}`: a play session is always a client of the game's server, and `rc` reads and changes the server's copy; `clients`: the players of a multiplayer test; the agent service's network note says it), `device` (as `device` `get`). With no game, the editor answers it (`session_running` false, `net` `{"mode": "standalone"}`). |
| `session` | `start` plays the place (a server and one player), `stop` ends the play session, `restart` stops and plays again (the game's scripts start over, the place as Studio has it). `players` (2-7) plays it as Studio's multiplayer test does (`StudioTestService:ExecuteMultiplayerTestAsync`: a server and a client per player, each a Studio process with the plugin; a player that doesn't join in 40 s is asked for again through the server's `AddPlayers`); kept for later restarts until another `players`. Answers `{"running", "scene", "players"}`; `status` says `running`, `loading` (a play session asked for or starting, player 1's client not ready yet, or a multiplayer test's other players still joining: the agent service waits it out), `scene` (the place's name, as `levels` says it) and `players`. A play session the user started joins by itself. `map` other than the place's name: 404, before anything changes (a place is one level). The `session` events carry the `scene`. |
| `player` (in any request's args) | Which player a request is for: 1 (the default) is the player Autopilot plays, its client answering as before; n goes to player n's client (`client<n>`: its view, keys, touches, UI, context), and an `rc` the server answers takes `LocalPlayer`, `Character` and `Humanoid` as player n's. A player not in the game: 404. Events from another player's client carry its `player`. `context` has `you` (the player answering) and `players` (`[{"player", "name", "path", "character", "you"}]`). |
| `levels` | `{"levels": [the place], "open": the place, "playing": the place while it plays}` |
| `pause` | `RunService:Pause()` / `Run()` on both sides: physics and the game's scripts stop, the plugin goes on answering. |
| `key` | Unreal key names (W, SpaceBar, LeftShift, One, F5, Enter, LeftMouseButton, MouseX/MouseY in pixels, MouseY positive up, MouseScrollUp/Down, MouseWheelAxis, the `Gamepad_` keys), a KeyCode's name (ButtonA, DPadLeft too), or a ContextActionService action's name (its first key). Through Studio's virtual input, focused or not. A press stays down 0.05 s and 2 frames; holds are leased; `seconds` makes a timed hold. MouseX/MouseY with the mouse free hold the right button for the turn (Roblox's default camera turns only while it is held). Gamepad buttons go in as their KeyCodes (UserInputType Keyboard: ContextActionService actions and InputBegan see the KeyCode); ButtonA also jumps unless the game bound it. `Gamepad_LeftX`/`LeftY` (-1..1) walk the player relative to the camera, `Gamepad_RightX`/`RightY` turn the camera (about 240°/s), `Gamepad_LeftTriggerAxis`/`RightTriggerAxis` past 0.5 are L2/R2: held with `value` (a press pushes for 0.15 s). A key Roblox keeps for its menus (Escape; ButtonB, ButtonStart, DPadUp on a console): 409. |
| `input` | A ContextActionService action by name: its first key, as `key`. |
| `move` | `{"direction": kit vector, "seconds", "scale"? (-1..1, negative: the other way), "via"?}`: `stick` (the player's `Move` toward it every frame, after the PlayerModule's, as an analog stick; `humanoid` is the same), `keys` (W/A/S/D toward it, 8 ways), `controller` (the character's `Humanoid:Move` itself every frame: for a game whose controls don't go through the Player), `auto` (the default: `stick`); another `via`: 400. Answers `{"moved", "via"}` (the `via` it went). |
| `watch` | Pivots sampled every frame (`moved` in kit units, `turned`, `changed size`); with `drawn`, the biggest parts in the thing's own frame and `part shown/hidden`, `light on/off`, `animation started/ended` (the Animator's tracks), `sound started/ended`, `effect started/stopped` (particles, fire, beams, trails), `went limp`. |
| `view` | See below. |
| `screen` | The visible texts of TextLabels, TextButtons and TextBoxes in the player's PlayerGui (ContentText), keyed `<ScreenGui>.<Name>` (`#2`, `#3` for repeats). With no game: `{"texts": {}}`. |
| `ui`, `ui_act` | GuiButtons (`button`) and TextBoxes (`text_input`, with its `text` (also as `value`) and `placeholder`) on screen, with `path`, `class`, `screen` (their middle in viewport pixels, top bar inset included). Roblox has no checkbox, slider or combo of its own (a game builds them from buttons and frames): they list as their buttons, and `set` takes only a TextBox. With no game: `{"elements": []}`. `click` (and `right_click`, `double_click`) moves the mouse there and presses it: the answer's `fired` says whether the button's `Activated` came. `set` types into a TextBox and submits it (`FocusLost` with Enter); `drag` presses on one and lets go on another. |
| `pointer` | `{"screen_x", "screen_y"}` (-1..1, y up), `{"x", "y"}` (viewport pixels), `{"world": kit point}`, `{"object": path}` or `{"ui": id}`: the mouse put there. Answers `{"x", "y", "screen_x", "screen_y", "on_screen"}`. A ClickDetector is clicked by pointing at its part and pressing LeftMouseButton. |
| `touch` | A finger: `{"action": "tap" \| "double_tap" \| "long_press" \| "swipe" \| "down" \| "move" \| "up", a spot as `pointer` takes it, "to": spot (a swipe's end), "seconds"}`. While Studio simulates a phone or tablet (`device`) Studio makes the virtual mouse's left button a finger: the game gets real touches (TouchStarted, TouchTap, TouchSwipe, TouchLongPress, Activated; Roblox's on-screen thumbstick and jump button take them). Elsewhere the finger is the mouse's left button. Answers `{"as": "touch" \| "mouse", "x", "y", "screen_x", "screen_y"}`. |
| `device` | Studio's Device Simulator: `{"device": "phone" \| "tablet" \| "console" \| "vr" \| "desktop" \| a device's id or name, "orientation"?: "portrait" \| "landscape"}` switches it (a phone or tablet turns as the game's StarterGui.ScreenOrientation says unless told), `{"action": "list"}` lists Studio's devices, `{"action": "get"}` says the device now: `{"id", "name", "form", "simulated", "touch", "keyboard", "mouse", "ten_foot", "orientation", "viewport"}`. `session` `start` / `restart` take `device` and `orientation` too. The user's own device comes back when the run ends (or the connection does). |
| `record`, `capture` | Answered by the agent service, from Studio's MCP server (see [Pictures of the game](#pictures-of-the-game)): `capture` `{"jpeg", "width", "height", "t", "via": "studio_mcp"}`; `record` `start` sends `frame`s a few times a second while a game plays, across play sessions, until `stop`. 409 `no picture: ...` with no game running, or when Studio's MCP server is off. A Studio whose StudioCaptureService works gives the plugin its own pictures (its hello then says `media`; PNGs, made JPEGs by the agent service). |
| `detectors` | Only while a run uses the game (one is going, or the agent service asked the game something in the last 30 s): off in your own play sessions, so your game plays at its normal speed. `log_error` (errors the game prints, server and client: LogService; error), `nan` (error), `out_of_world` (an unanchored part 50 m below the level's lowest drawn point at the start, or past FallenPartsDestroyHeight; warning), `long_fall` (the player in Freefall for 8 s; warning), `hitch` (a frame over 0.75 s; warning), `hud_fraction` (warning), `no_input` (a key that didn't reach the game), and as Godot's, on the server only (it sees every character, and each finding comes once): `teleport` (a character, a Model with a Humanoid and a HumanoidRootPart, the players' and NPCs', moved farther in one frame than `teleport_factor` (3) times its speed (AssemblyLinearVelocity) allows, at least `teleport_min` (1000) units, after 5 frames of following it, again after a respawn; a move by the agent, `set` of a position, counts too; a suspicion; signature `teleport:<kind>`, `details` `from`, `to`, `moved`, `allowed`), `actor_growth` (every 10 s of game time, the Workspace's things by kind: `Player` for a player's character, else its first CollectionService tag, else its name with its digits as `#`, else its class; a kind that kept growing over `growth_seconds` (120) to twice as many and 50 more; warning; `details` `from`, `to`, `seconds`), `over_max` (every `property_every` (0.5) s, a number attribute above its sibling attribute `max_<name>`, `<name>_max`, `Max<Name>`, `<Name>Max`, `Max_<name>` or `<name>_Max`, or an IntValue or NumberValue above such a sibling value object, players' leaderstats too, when that is above 0; under the Workspace, Players, ReplicatedStorage and ServerStorage, a few hundred instances looked at a frame; not a Humanoid's Health, which the engine clamps; warning; `details` `property`, `value`, `max_property`, `max`).  No `clamp`: Roblox declares no ranges for a game's numbers (the old Int/DoubleConstrainedValue clamp themselves).  Thresholds: `long_fall_seconds`, `hitch_seconds`, `out_of_world_margin`, `teleport_factor`, `teleport_min`, `growth_seconds`, `property_every`.  The game's warnings go as `log` events. |
| `codex_manifest`, `codex_read` | Every script of the place as `src/<Service>/.../<Name>.server.luau` (a Script), `.client.luau` (a LocalScript, or a Script with RunContext Client), `.luau` (a ModuleScript), a script's children in a folder of its name; plus `.autopilot/summary.txt` (the place, Workspace and StarterPlayer settings, tags, every script with where it runs) and `.autopilot/tree/<Service>.txt` (each service's instances: class, place and size, attributes, tags, values, texts; runs of identical siblings told once with their count). |
| `find` | `{"class"?, "name"?, "limit"? (10000), "things"?}`: `class` is a Roblox class (`IsA`), `tag:<name>` (CollectionService; a plain tag name works when no class has it), or the name of a script inside a model; a name that is none of these (no class: `ReflectionService:GetClass`; no tag; no script): 400. `things: true` the Workspace's things (models and parts at the top or in folders; a model of models is a folder). |
| `transforms` | `{path: {"location", "rotation", "velocity", "center", "extent", "active"} \| null}` (null: no such instance): the pivot, the assembly's velocity, the bounding box (world-aligned), in the kit frame. An instance with no place in the world (a script, a value, a GUI object) answers those null but `active`. `active`: in the DataModel; a part not fully transparent; a script, light, effect or anything with `Enabled` enabled; a GUI object visible (with every GuiObject above it) in an enabled ScreenGui in a PlayerGui (or SurfaceGui, BillboardGui). |
| `pose` | `{"at", "feet", "eye", "yaw", "pitch", "velocity", "on_ground", "path"}`: `at` the HumanoidRootPart's centre, `feet` its half height plus HipHeight below, `eye` the camera. |
| `movement` | `{"walk_speed", "jump_z", "gravity", "half_height", "radius", "step", "max_slope", "air_control", "sources"}` from the Humanoid (WalkSpeed, JumpPower or JumpHeight as `sqrt(2 g h)`, MaxSlopeAngle, HipHeight) and the Workspace's Gravity, in kit units. |

### `view`

What Unreal's tells (its `omitted` is empty).  The Workspace's things inside the camera's view that a line of sight from
the camera reaches (the box's centre, top or nearest point), nearest first, up to `max_count` within `max_distance`;
the player, what it holds, a thing with no visible part and the ground are left out (a large floor it stands on, and
anything wider than `floor_size`, 4000 units, and flat).  Each has `name`, `path`, `class`, `tags`, `center`, `size`,
`yaw`, `distance`, `screen_x` / `screen_y` (-1..1, right and up positive), `partly_in_view`, `speed`, `top_above_feet`,
`bottom_above_feet`, `crosshair_on` (a ray from the view's middle) and `crosshair_part` (the character's part it is on),
`tilt` (`pitch`, `roll` when tipped a degree or more) and `box` (its own box when turned: a part's CFrame and Size, a
model's GetBoundingBox: `center`, `size` along its own axes, `rotation`), `blocks_player` (the body is against it),
`health` / `max_health` (a Humanoid's), `player` (another player's character), for a model with a Humanoid
`body_center` and `body_shown` (rays through a 7×7 grid over its box: the middle of what is seen of it, and how much
shows), `look` (`parts`: visible parts' names, `lights`, `animation`, `sounds`, `effects`: `[{"asset", "scale"}]` of its
particles, fire, smoke, sparkles, beams and trails on, by name, `scale` its model's GetScale; `texts`: SurfaceGui and
BillboardGui; `highlighted`: an enabled Highlight or a visible SelectionBox or handle adornment on it, inside it or in
the PlayerGui, the camera or the Workspace's top; `limp`: its Humanoid in the Physics, Ragdoll or FallingDown state,
or most of its Motor6Ds off with BallSocketConstraints on) and `label`; and its top surface (as Unreal's and Godot's
lib/reach.gd, `src/Reach.luau`): `top_slope_degrees` along the way its own box's top face rises most (on its own
collision, so a WedgePart's slope; its box's top face when nothing of it collides) and, for a slope a body walks (5 to
50 degrees), `low_end`, `high_end`, `lane` and `uphill`.

`held`: what the player holds: an equipped Tool (`attached_at` the hand gripping it), what outside the character is
welded onto it (a Weld, WeldConstraint, Motor6D or RigidConstraint: a thing carried; `attached_at` the character's part;
not a seat it sits in, nothing anchored) and the camera's children (a viewmodel), with something drawn; not
Accessories.  Each `name`, `path`, `class`, `look.parts` (biggest first), `center`, `size`, `distance`, `screen_x` /
`screen_y`, `in_view`; nearest the camera first.  Held things are left out of every ray, as the character is.
`scene.sun`: the Lighting's Brightness while the sun is up, else 0.

`camera` has `location`, `yaw`, `pitch`, `fov` (horizontal), `vfov`, `type`.  `body` has `velocity`, `feet_z`,
`on_ground`, `falling`, `state` (the Humanoid's), `health`, `standing_on`, `floor_slope_degrees`, `touching` and
`overhead` (the first thing straight above the head within `overhead_reach`, 2000 units, when it is in view, against
the body or within 10 units).  With `openings` or `ways_up` asked, `under` (what is straight above, whatever is in
view), and, found by the body's own rays and Blockcasts (its CollisionGroup, CanCollide respected): `openings` and
`free`, and `ways_up` (the ramps and stairs up from its floor it sees: `turn`, `distance_to_foot`, `rises_by`,
`top_distance`, `foot`, `top`, `seen_from` "side" with `reachable_straight`, `lane`, `inside`) within `openings_reach`
(3000 units), as Godot's.  A Humanoid steps up: the floor may rise from one place to the next by `movement`'s `step`
(0.9 × HipHeight) at once, or by what a slope no steeper than its MaxSlopeAngle (taken as at most 60 degrees: the
default, 89, would make a wall a slope) rises over the way, so stairs whose risers are lower than the step are a way
up.  The terrain is a floor and a wall like any part, never a thing.  `sonar` works as in Unreal: a box the body's width
swept each way above its step height (a walkable slope or a step is gone up), rays down for drops (`by`: wall, drop,
reach).  With `only`: no `held`, `scene`, `overhead`, openings, ways up, top surfaces or `body_center`.

## Pictures of the game

A Studio plugin can't read the game's pixels today: CaptureService's screenshots are textures a script can show but
never read back (an EditableImage refuses them), and StudioCaptureService, Studio's screenshots for plugins, answers
"Feature not supported yet" (the plugin uses it once Studio turns it on: `src/Media.luau`). Studio's own MCP server
can: its `screen_capture` tool gives the game view (the 3D view and the game's own UI, not Roblox's top bar) whether or
not Studio is in front. So for a Roblox plugin the agent service, which runs on the same computer, starts Studio's MCP
server (`autopilot/studio_mcp.py`) and answers `capture` and `record` from it: runs get a recording, stills and
`describe_frames` as in the other engines.

- Turn it on once in Studio: **Assistant** (far right of the top bar), **…**, **Settings** (or **Manage MCP Servers**),
  **MCP Servers** tab, **Enable Studio as MCP server**. The status changes to **MCP Ready**. Nothing under **MCP
  Permissions** needs changing, and a place needn't be saved to Roblox.
- The agent service starts `/Applications/RobloxStudio.app/Contents/MacOS/StudioMCP` on macOS and `cmd.exe /c
  %LOCALAPPDATA%\Roblox\mcp.bat` on Windows (Roblox's own command lines); `AUTOPILOT_STUDIO_MCP` sets another,
  `AUTOPILOT_STUDIO_MCP=off` turns pictures off. With several Studios open it takes the one whose place the plugin's
  hello names.
- Pictures come at most a few a second (each is a round trip through Studio), only while a game plays.
- A picture asked for while a play session starts or stops can fail or go unanswered (`Target is closed (INVOKE_ScreenCaptureTool...)`, or
  nothing): the server is then started over, and the next picture usually comes a few seconds later, so a recording can miss those moments.

## Limitations

- **Gamepad sticks as the game reads them.** Studio's virtual input has keys and a mouse, not a gamepad: buttons go in as
  their KeyCodes (an InputObject of UserInputType Keyboard), and the sticks and analog triggers as Roblox's own gamepad
  controls use them (walking, the camera, L2/R2). A game that reads a stick's position itself (InputChanged with
  Thumbstick1, `GetGamepadState`) or checks for `Gamepad1` doesn't see them; `UserInputService.GamepadEnabled` stays
  false.
- **One finger.** A touch is the virtual mouse's left button, so there is one finger at a time: no pinch or two-finger
  rotate (`MouseScrollUp`/`Down` zoom instead).
- **No headless or command-line runs** (CI): a Roblox game runs only in Studio (or Roblox's clients). Bench runs
  (`bench/run_autopilot.py`) need another engine.
- **One level.** A place is one level; teleporting between a universe's places isn't played.
- **Your input reaches the game during a run.** The other engines' plugins keep the person's keyboard, mouse and gamepad
  out of the game while a run plays; Roblox delivers the person's input and the plugin's virtual input to the game the
  same way (an InputObject says nothing of where it came from), so it can't. Studio's game view takes your input only
  while it has focus: work in another window, as the dock reminds you during a run.
- **Players.** A multiplayer test's players are Studio's own test players (Player1, Player2...), up to 7; the device
  simulator (touch, a console's interface) is for a game of one player.
- **Client WebSocket capacity.** Roblox forbids HTTP requests from a Play client, including plugins. The editor and
  Play server can fall back to HTTP when their WebSocket slots are occupied, but the client needs one free slot.
  If all four are occupied, Autopilot reports this in Studio's Output and retries until a slot becomes available.

## Security

The agent can call functions and set properties in the running game, so connect only to an agent service you trust.
The plugin only connects out, to the address you set (by default this computer), and does nothing in a play session
until the edit plugin is connected. The play session's helper script exists only while a session runs.

The local bridge and development server accept native clients without an `Origin` header and reject browser
origins. This prevents browser cross-origin control; it does not authenticate other local processes. Keep these
listeners on loopback. Setup changes are saved immediately and applied to model credentials and access mode at
the next run.

## Removing it

Delete `Autopilot.rbxmx` from Studio's Plugins folder. Its settings and run history stay in Studio's plugin settings
until Studio forgets the plugin; nothing of Autopilot's is in your places. `autopilot uninstall` removes the agent
service and its files from the computer.

## For contributors

### Source map

All paths are under `plugin/roblox/`.

| File | What it does |
|---|---|
| `src/Main.luau` | The start: which DataModel this is (edit, server, client) and which parts run there. |
| `src/Editor.luau` | The edit DataModel's link to the agent service: the hello, runs, `session` (play, stop, restart, on a device), `levels`, `device`, the codex, routing requests to the play session's sides, the account and setup frames. |
| `src/Dock.luau` | The dock: connection, account (Aura sign-in, BYOK), memory, the composer, the live run (stages, Stop, Steer), the Policy log, the feed, the result and its usage, history, settings. |
| `src/Host.luau` | A play session's side joined to the pipe: requests answered in threads of their own, events once a frame, attach and detach. |
| `src/Client.luau` | The client's ops: input (keys, holds, the mouse, the gamepad), touch, device, move, watch, view, screen, ui, pointer, find, transforms, pose, movement, context, pause, capture, record. |
| `src/Server.luau` | The server's ops: rc, find, transforms, pause, ending the play session. |
| `src/Link.luau` | The connection out: a WebSocket, else HTTP long polls; reconnect with backoff. |
| `src/Modules.luau` | The helper script on each side: the game's ModuleScripts as the game has them. |
| `src/Keys.luau` | Unreal key names as virtual input; the mouse; gamepad buttons; ContextActionService actions; points on the screen as virtual input takes them. |
| `src/Pad.luau` | The gamepad's sticks and triggers, as Roblox's gamepad controls take them; ButtonA's jump. |
| `src/Device.luau` | `device`: Studio's Device Simulator (a phone or tablet makes the game a touch device). |
| `src/Media.luau` | `capture`, `record` from StudioCaptureService, once Studio gives plugins pictures. |
| `src/Fallback.luau` | Keys for a game that took none: walking, jumping, prompts as Roblox's own controls put them. |
| `src/Rc.luau`, `src/Values.luau`, `src/Paths.luau` | `rc`: reads, writes, calls, descriptions; values to and from JSON; instances by path. |
| `src/World.luau`, `src/Sight.luau`, `src/Reach.luau`, `src/Ui.luau` | find, transforms, pose, movement; view, sonar, watch; the room round the body (openings, ways up) and top surfaces; screen, ui, ui_act, pointer, touch. |
| `src/Codex.luau`, `src/Detectors.luau` | The codex; the in-game detectors. |
| `src/Frame.luau`, `src/Json.luau`, `src/Settings.luau` | The kit frame; JSON (empty objects, NaN); the plugin's settings. |
| `dev/` | The development loader (`AutopilotDev.server.luau`) and its client relay (`DevRelay.luau`): never built into the plugin. |
| `tests/` | The test game (`LanternGrove.rbxl`, built by `testgame.luau`), the live tests (`smoke.py`, `kit_live.py`, `multiplayer.py`, `harness.py`) and the focused regressions (`Regressions_Test.luau`, `client_disconnect.luau`). |

### Building and the development loop

`python tools/build_roblox.py [--install]` builds `dist/roblox/Autopilot.rbxmx`: a Script that starts the plugin and one
ModuleScript per `src/*.luau` (no Rojo needed: the model file is written as XML).

While working on the plugin, `python tools/roblox_dev.py install` puts the development loader in Studio's Plugins folder
in place of the built plugin (remove `Autopilot.rbxmx` meanwhile), then reopen the place once, and run `python
tools/roblox_dev.py serve`. The loader fetches the plugin's modules from the dev server in every DataModel and starts
the plugin; when a file in `src/` changes, every DataModel restarts the plugin with it, without reopening anything.
The DataModels' output goes to the dev server's log, and `python tools/roblox_dev.py eval [--dm edit|server|client]
'<Luau>'` runs code in one of them (the edit DataModel too during play). The loader talks HTTP to the dev server, so it
takes none of the DataModel's few WebSockets; the client, which can make no HTTP requests, is fed through the plugin's
settings by `dev/DevRelay.luau`, which only the dev server's bundle carries.

### Running the tests

The Python tests (`tests/test_roblox.py`, in `pytest`) check the adapter and the plugin's sources and build without
Studio (with `luau-compile` on PATH, also that every module compiles). The live tests need Studio:

For focused regressions, load `src/` as ModuleScripts under `ServerStorage.UnitTest.Modules` and
`tests/Regressions_Test.luau` under `UnitTest.Cases` in a new, unsaved Baseplate. Run it with Studio's unit-test
harness in the Play server. It checks native CFrame round trips, duplicate paths, privacy synchronization, and
detach cleanup without account access. `tests/client_disconnect.luau` returns a function taking the source module
folder and a scratch local bridge's pipe settings; run it in the Play client to check a real WebSocket closure
releases keyboard, fallback movement, and camera inputs. Use dummy settings and credentials for integration tests.
Pass `true` as its third argument to also occupy the client's WebSocket slots, check its diagnostic, free one slot,
and verify automatic recovery without manually reconnecting.

1. Open the test game in Studio with the plugin (built or from the dev loader): `tests/LanternGrove.rbxl`. Or build
   it into an empty Baseplate: paste `tests/testgame.luau` (its source) into the command bar (or `python
   tools/roblox_dev.py eval - < plugin/roblox/tests/testgame.luau`). After changing `testgame.luau`, run it in
   `LanternGrove.rbxl` and save the place, so the two stay the same. **Lantern Grove** has six lanterns to collect (a CollectionService tag), a
   lever with a ProximityPrompt that opens a gate, a crate to jump on, a pond, three same-named posts, a mailbox with a
   ClickDetector, a module with the game's state, remotes, a HUD (`Lanterns n / 6`), a menu (a button, a text box) and a
   ContextActionService action (Q waves). Two planted bugs: the mailbox's script indexes nil when clicked
   (`log_error`), and the gate's door falls through the ground once opened (`out_of_world`). For two players, a basket
   to pick up (E), hand to another player (G at them) and load onto a cart (E), which only takes a basket that was
   handed over; its planted bug: the HUD of the player it was handed to never says they carry it.
2. Stop any `autopilot serve` (the tests listen where the plugin connects; Studio needn't be in front), and run `python plugin/roblox/tests/smoke.py` (every request) and `python plugin/roblox/tests/kit_live.py`
   (the brain's kit: walk_to, look_at, jump_to, go_and_press, the UI, restart) and `python
   plugin/roblox/tests/multiplayer.py` (two players: the basket's hand-off). `AUTOPILOT_ROBLOX_TEST_PORT` sets the
   port when the plugin is set to another than 8765.

## License

Apache-2.0, as the rest of the repository.
