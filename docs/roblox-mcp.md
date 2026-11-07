# Roblox through Studio's MCP server: `roblox-mcp` (experimental)

The `roblox-mcp` engine plays Roblox places through **Roblox Studio's built-in MCP server**. There is no Autopilot
plugin inside Studio. A small connector process (`autopilot/engines/roblox_connector.py`) connects to Autopilot as a
plugin would and turns the protocol's requests into Studio MCP tool calls. This is called **Route A**.

It is meant for runs that are scripted: the bench (`bench/run_autopilot.py`), and what is built on it, can start the
connector for each prompt as it starts the other engines' headless games, with nothing installed in Studio. A native
Studio plugin (Route B, below) is the way to play a place from inside Studio. Should both be installed, a Roblox
place goes to the engine registered first; `AUTOPILOT_ENGINE=roblox-mcp` chooses this one.

> **Status: experimental, and whole runs work.** On Windows with Studio 0.746, the bench played a test FPS through
> this engine: three prompts (a match starting, a planted HUD bug, a wave cleared in a fight), three correct results.
> The tool arguments, the input actions' JSON and the tools' answers were read from Studio's own MCP server
> (RobloxStudio 1.0.0 on macOS, 2026-11: its `tools/list` is in [studio_tools.txt](studio_tools.txt)) and tried in a
> play session on the test place (see [What a live Studio answered](#what-a-live-studio-answered)). The tests run
> against a stand-in that follows them (`FakeStudio` in `autopilot/engines/roblox_mcp.py`, and a simulated place in
> `tests/robloxsim.py`).
> `python tools/roblox_latency.py --tools` prints the live list again.

## How it works

```
Autopilot (the brain) <-- bridge WebSocket, protocol v1 --> Roblox connector <-- MCP over stdio --> Studio's MCP server
                                                            (engines/roblox_connector.py)                    (StudioMCP / mcp.bat)
```

| What Autopilot does | The Studio MCP tool it uses |
| --- | --- |
| reads: find, transforms, pose, movement, get, set, call, screen, ui, view | `execute_luau` with the Luau ops of `engines/roblox.luau`; all the reads of one `game.batch` go in **one** call, answered as one JSON string (`HttpService:JSONEncode`) |
| keys, mouse buttons, the wheel, UI clicks | `user_keyboard_input`, `user_mouse_input` |
| camera turns (`MouseX`, `MouseY`) | `execute_luau` in the Client: the `turn` op sets the camera's look (Studio's synthetic right-drag doesn't turn it) |
| `game.request("move", {"to": ...})` | `character_navigation` |
| start, stop and restart the session | `start_stop_play` (a restart is stop, then start) |
| the game's errors (`log_error`) | `get_console_output`, read every couple of seconds while the game plays |
| stills | `screen_capture` |
| the codex | a Rojo project's files from disk, else every script via `script_read`; the place's CollectionService tags and its instance tree (`search_game_tree`) |

While the game plays, reads run in the **Client** data model (what the player's copy shows), and writes and calls run
in the **Server** data model (where the truth is kept). The kit's frame is Roblox's Y-up studs at 1 stud = 28 cm (Roblox's
physics takes a stud as 0.28 m).

## Setup

1. **Turn on Studio's MCP server**, as [the setup guide](roblox-studio-setup.md#3-turn-on-studios-mcp-server) shows.
2. **Open the place** in Studio. A Rojo project needs building first (`rojo build <project> -o Place.rbxlx`) or serving
   with the Rojo plugin (`rojo serve`).
3. **Install Autopilot from a clone** (see [CONTRIBUTING.md](../CONTRIBUTING.md#development-setup)) and set your keys in
   `.env`. Through Aura, sign in as usual.
4. **Start the brain**: `autopilot play "Verify that ..." --project <the place>`, or `autopilot serve`.
5. **Start the connector** on the same machine as Studio:

   ```bash
   python -m autopilot.engines.roblox_connector connect --project <the Rojo folder or .rbxl/.rbxlx>
   ```

   It starts Studio's MCP server, checks its tools (with a message on what to do when one is missing), and connects to
   the brain (the address in `brain.json`, else `ws://127.0.0.1:8765/v1/bridge`; `--url` to say another).

The server's command is the one Roblox documents: `cmd.exe /c %LOCALAPPDATA%\Roblox\mcp.bat` on Windows,
`/Applications/RobloxStudio.app/Contents/MacOS/StudioMCP` on macOS. Studio doesn't run on Linux.

### Environment variables

| Variable | Default | What it does |
| --- | --- | --- |
| `AUTOPILOT_ROBLOX_MCP_CMD` | Roblox's documented command | the command line that starts Studio's MCP server |
| `AUTOPILOT_ROBLOX_STUDIO_ID` | (the only one) | which Studio to play when the server is connected to several (`list_roblox_studios`) |
| `AUTOPILOT_ROBLOX_RESULT` | `return` | `print`: Luau chunks also print their JSON answer, for a server that gives back only printed output (the connector switches by itself when it sees that) |
| `AUTOPILOT_ROBLOX_CONSOLE_SECONDS` | `2` | how often the console is read for errors while the game plays |
| `AUTOPILOT_ROBLOX_LOCALAPPDATA` | (set by the bench) | the real `%LOCALAPPDATA%`, for `mcp.bat`, when the bench gives the brain a settings folder of its own |

**Recommended: `AUTOPILOT_SHARE=0`** while Roblox support is experimental, so runs' data (the place's scripts among
it) is never shared: `AUTOPILOT_SHARE=0 autopilot play ...`. The connector's `--no-share` says the same in its hello.

## Measure the round trip first

```bash
python tools/roblox_latency.py                 # 100 execute_luau calls in play mode: p50, p95, recommended tempo
python tools/roblox_latency.py --tools         # what the server offers, with each tool's input schema
```

Each read and input is a round trip through Studio's MCP server. The tool times the engine's own batched observe and
prints the `tick_hz` and `tempo` a harness can keep at that speed. The agent's kit section tells it to stay within them.

## The bench

```bash
AUTOPILOT_ENGINE=roblox-mcp python bench/run_autopilot.py --project examples/roblox_testplace --suite bench/suites/roblox_testplace.json
```

For a Roblox project the bench starts the connector instead of a headless game, so Studio must be open on the built
test place with its MCP server on. Prompts run one at a time (one Studio, one play session). The test place
(`examples/roblox_testplace`, see its README) has an obby with checkpoints, a coin, a lever that opens a door, and a
debug ModuleScript, all tagged with CollectionService. The suite's expected verdicts come from the place's code: none of
them has been run against Studio yet.

## Known limits

- **Latency.** Every read and input is an MCP round trip. Measured on a Mac (`tools/roblox_latency.py`, a batched
  observe of 4 reads in the Client, n=100): p50 67 ms, p95 72 ms, so `tick_hz` <= 13.9 and `tempo` <= 6.9 decisions a
  second. Windows measured the same: p50 67 ms, p95 68 ms. A key press is about 35 ms, a mouse click 0.25-0.6 s. Batched reads cost one round trip, so the agent is
  told to batch them. Reflex play (dodging a hit a fraction of a second ahead) is mostly out of reach.
- **No pause.** Studio's MCP server can't pause a play session: the game runs while the agent thinks.
- **Stills, not video.** There is no recording, only `screen_capture` stills.
- **No frame-accurate detectors.** Findings come from the console (errors and warnings). NaN, hitch, teleport and
  long-fall detectors don't exist here.
- **The view tells less** than the other engines': no held items, openings or ways up (so `walk_to` won't find ramps by
  itself), and no looks or effects. Its `omitted` lists them.
- **One place.** `game.restart(map=...)` can only restart the place open in Studio.
- **No named input actions.** ContextActionService and UserInputService bindings are pressed by their keys.
- **Not headless.** Studio must be open, so it can't run in CI.
- **Clicks are real clicks.** Roblox's chat window takes the presses that land on it. In a small game view it can cover
  part of a menu, so a button can seem dead while its uncovered part, or the keyboard, still works.

## What a live Studio answered

Read from Studio's MCP server (RobloxStudio 1.0.0, macOS, 2026-11) on the test place, in Edit and in a play session:

- **Every tool takes and requires `studio_id`**, from `list_roblox_studios`: `{"studios": [{"id", "name"}]}` (the
  name is the place's file). Studio registers with the server a moment after the server starts (0.25-2 s), so the
  list can be empty at first: the connector waits for it.
- **Argument names**: `execute_luau(code, datamodel_type)`, `start_stop_play(is_start)` (a boolean),
  `script_read(target_file)` (a dot path), `script_grep(query)`, `inspect_instance(path)`,
  `search_game_tree(datamodel_type, path, instance_type, keywords, max_depth)` (`datamodel_type` required; a path with
  or without `game.`), `screen_capture(capture_id)` (required), `character_navigation(datamodel_type: "Client", x, y, z
  | instance_path, speed_multiplier)`.
- **Inputs**: `{"actions": [...], "datamodel_type": "Client"}`, each step `{"action": ...}`. Keyboard: `keyDown`,
  `keyUp`, `keyPress` with `key_code` (a KeyCode name: `W`, `Space`, `LeftShift`), `textInput` with `text_inputs`,
  `wait` with `wait_time_ms`. A key held down stays down between calls until its `keyUp`. Mouse: `moveTo`,
  `mouseButtonDown`, `mouseButtonUp`, `mouseButtonClick` with `mouse_button` (`left` or `right`: no middle),
  `scrollUp`, `scrollDown`, `wait`; each step at `x`, `y` (viewport pixels) or an `instance_path`, and a call without a
  position in it is refused. **A right-drag doesn't turn Roblox's camera** (the camera scripts don't see a synthetic
  drag), so the connector turns it by setting `workspace.CurrentCamera`'s look in the Client, which the camera
  scripts carry on from (and `W` then walks the new way). Holding the Left/Right arrow keys turns it too, at about
  120 degrees a second, but without pitch.
- **`execute_luau` returns** the chunk's first return value as text (`return 1, "two"` gives `1`; a chunk that only
  prints gives `nil`, and its prints go to the Output window). An error is the tool's error, with the message. In a
  play session both `Client` and `Server` answer, and `Players.LocalPlayer` is set in the Client; `Edit` is refused
  ("Edit datamodel is not available in Play mode").
- **`get_console_output`**: the Output window as plain lines, without each message's type (a `warn` and a `print` look
  alike), from before the play session too and including other plugins' messages. Errors are told by their words.
- **`get_studio_state`**: text lines, `- Current Studio Mode: Edit` (or `Play`), `- Available DataModels: Edit` (or
  `Client, Server`), `- Focused DataModel in the viewport: ...`.
- **`character_navigation`** answers `Success` once the walk ends (about 4 studs took 0.4 s).
- **`screen_capture`** returns a JPEG image item (the viewport, 1219x870 on the machine tried).
- **`script_read`** numbers each line (`     1→--!strict`); the connector takes the numbers off.

Windows (Studio 0.746) agrees with the above, apart from the server's command (`mcp.bat`). Its server also offers
`generate_animation` and `open_cloud_invoke`, which Autopilot doesn't use.

## Route B: a native Studio plugin

Route B is a native Luau Studio plugin that speaks Autopilot's bridge protocol itself, from inside Studio, as the
Unreal, Unity and Godot plugins do: it answers each read inside one frame, runs frame-accurate detectors and plays a
place from a dock in Studio. It is the way to play a place from Studio; Route A (this engine) is the way to run one from
a script, with nothing installed in Studio. The two can be installed side by side (see the top of this page).
