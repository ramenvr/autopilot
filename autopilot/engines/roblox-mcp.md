
## The engine: Roblox

The game is a Roblox place played in Roblox Studio. There is no Autopilot plugin inside Studio: every read and input
goes through Studio's built-in MCP server (Luau run in the place with `execute_luau`, keys and clicks with its input
tools). Each call is a round trip of tens to hundreds of milliseconds, so this game is slower to read and to drive than
the others. Plan for that (see **Limits**).

- **Things are instances**, named by their paths from the data model: `Workspace/Obstacles/Spinner`,
  `ReplicatedStorage/Remotes/Damage`, `Players/<Name>/leaderstats`. A service is the first part. When siblings share a
  name, the first is plain (`Stage`) and the others are numbered `Stage@1`, `Stage@2`... in `GetChildren` order. These
  numbers can shift when siblings are added or removed, so find things again by tag or class rather than keeping a
  numbered path. A name's `/`, `@` and `\` are escaped with `\`. `game.name_of(path)` is the last part. The codex and
  Studio write the same instance as `game.Workspace.Obstacles.Spinner`.
- **Finding things by tag.** Gameplay objects are usually tagged with CollectionService (`CollectionService:GetTagged`,
  `HasTag` or `AddTag` in the code, a tag list in Studio's Properties). `game.actors("tag:Hazard")` returns the
  instances with that tag. `game.actors("Hazard")` returns the tagged instances and the instances of that class
  (`IsA`: `game.actors("SpawnLocation")`, `game.actors("BasePart")`, `game.actors("Model")`). `game.all_actors()` is
  Workspace's parts and models (through Folders) and everything tagged in Workspace, not every part inside a model.
  The codex's `tags.txt` lists every tag with its instances, where they are and their attributes.
- **Reads and writes.** `game.get(path, name)` reads a property (`Position`, `Transparency`, `CanCollide`, `Enabled`,
  `Text`...). If there is no such property, it reads an attribute (`GetAttribute`), then the `.Value` of a child value
  object of that name (`IntValue`, `BoolValue`: `game.get("Players/<Name>/leaderstats", "Gems")`).
  `game.props(path)` returns the common properties, the attributes and the child values. `game.set(path, name, value,
  why=...)` writes them the same way. `game.describe(path)` lists class, tags, attributes, children and (for a
  ModuleScript) the module's functions.
- **Calls.** `game.call(path, fn, args=[...])` calls a ModuleScript's function (`require(module)[fn](...)`) or an
  instance's method (`inst:fn(...)`), with the arguments in order. `ReturnValue` is the first result and `returns`
  holds all of them. An argument `{"path": "..."}` is that instance, and `{"x","y","z"}` is a Vector3. A debug or admin
  module in the code (give an item, set a stage, reset) is usually the quickest way to set up a verification. Report
  its use as a direct change.
- **Values from `game.get` are Roblox's own**: studs, Roblox's axes (x right, y up, -z forward), vectors as
  `{"x","y","z"}`, a CFrame as its position with `orientation` (degrees) and `look`, an instance as its path, an enum as
  its name. `game.to_kit(value)` turns a position or velocity into the kit's frame (1 stud = 28 cm, as Roblox's physics
  takes it: `kit = 28 * (-z, x, y)`), and `game.to_native(vec)` turns a kit point back. Everything else the kit gives
  (`game.loc`, `game.locs`, `game.bounds`, `game.pose`, `game.view`, `game.sees`, `movement`) is already in the kit's
  frame.
- **The client/server split.** While the game plays, reads run on the player's client (what that player's copy of the
  place shows, including PlayerGui and LocalScript state) and writes and calls run on the server (where the truth is
  kept and changes replicate). Prefix a path with `server:`, `client:` or `edit:` to choose a side:
  `game.get("server:Workspace/Bridge", "Raised")` reads the server's copy. Code in ServerScriptService (Scripts) never
  runs on the client, and LocalScripts (StarterPlayerScripts, StarterGui) never run on the server. Read both sides of
  a mechanic in the codex. They talk through RemoteEvents and RemoteFunctions (usually in ReplicatedStorage): a client
  `FireServer` reaches the server's `OnServerEvent`, and the server checks it there. A change made on the client's copy
  doesn't replicate and won't count for the game's rules.
- **The player.** The local player's character. `game.pawn` is its path (`Workspace/<Name>`), and `game.controller` is
  "". `game.pose()["at"]` is its HumanoidRootPart, and the feet are `movement(game)["half_height"]` lower. `yaw` and
  `pitch` are the camera's. `pose()` also gives the Humanoid's `health` and `state`. `movement(game)` reads the
  Humanoid: walk speed (WalkSpeed), the jump's take-off speed (JumpPower, or JumpHeight as `sqrt(2 g h)`), gravity
  (`workspace.Gravity`), the half height (HipHeight plus half the root part), `max_slope` (MaxSlopeAngle). `step` is
  None: a Humanoid climbs small ledges by itself, so test a ledge rather than assume it. A character that falls below
  `workspace.FallenPartsDestroyHeight` dies and respawns (`pose()` says `fallen`). To name another character as the
  player: `game.request("context", {"player": "Workspace/<Model>"})`.
- **Input.** Keys use the kit's names (`W`, `SpaceBar`, `E`, `LeftShift`...), turned into Roblox KeyCodes and sent with
  Studio's keyboard tool. Mouse buttons (left and right: there is no middle button) click where the pointer is
  (`game.pointer(...)`, else the middle of the view). `game.mouse` / `game.turn` turn the camera (half a degree a
  count, set in the player's client, where Roblox's camera carries on from it), so `game.calibrate_look()` works as
  usual. A ProximityPrompt (`view`'s `prompt`: its key, action and range) fires when
  its key is held near it for its `hold` time. `game.ui()` lists PlayerGui's buttons and text boxes, and
  `game.ui_act(id=...)` clicks one or types into it. `game.screen()` keys PlayerGui's texts as `<ScreenGui>.<name>`.
  `game.request("move", {"to": kit point})` (or `{"object": path}`, or `{"direction": kit vector, "seconds": s}`)
  walks the character there with Studio's pathfinding (`character_navigation`). It is quick to write but is not the
  player's own input: prefer keys and `walk_to` for anything the run is judged on. There are no named input actions:
  ContextActionService and UserInputService bindings are read in the code and pressed by their keys.
- **The session.** `game.restart()` stops play and starts it again (Studio's play button), so every script starts over
  and every value resets. A restart takes several seconds: restart between episodes, not inside one.
  `game.restart(map=...)` can only restart the open place, because Studio plays one place at a time. `game.pause` can't
  pause a Roblox play session. The game goes on while the agent reads and thinks, so after a long think, read the
  state again before acting on it.
- **Research.** `game.request("luau", {"code": "...", "datamodel": "Server"|"Client"|"Edit"})` runs Luau in the place
  and returns what it printed. It is a probe's tool, not a player's action: report what it changed as a direct change.
  `game.request("inspect", {"path": ...})` returns Studio's description of an instance, and
  `game.request("grep", {"pattern": ...})` searches every script in the place.
- **Findings** come from the console. Errors and warnings in Studio's Output (read every couple of seconds while the
  game plays) are reported as `log_error` with the script and line. No detector here runs every frame: there is no
  NaN, hitch, teleport or long-fall detector. Check those in the harness's own observations.
- **`game.view`** returns the visible things (name, path, class, `tags`, center, size, distance, screen position,
  crosshair_on, `label` (its first text), `prompt` (its ProximityPrompt)), the body (on_ground, falling, standing_on,
  health), the camera, and with `sonar=True` the ways the body could walk (shape casts the size of the root part, drops
  felt with rays). Its `omitted` lists what it can't tell here: held items, openings, ways up (so `walk_to` won't find
  ramps by itself: jump or route by the level's parts), how things look, effects.

**Limits.** Every read and input is one or more MCP round trips. Put all of an observation's reads in one
`game.batch` / `game.map`: they go to Studio as one `execute_luau` call and are read in the same frame. Separate reads
pay a round trip each. Inputs land after a delay, and the place can't be paused or stepped frame by frame. Keep the
harness's `tempo` within what the round trip allows: about one decision per two round trips. Measured on Studio's
server, a batched observe takes about 70 ms (p95), so keep `tempo` at about 7 decisions a second or less and `tick_hz`
at about 14 or less. A key press or release lands in about 35 ms, but a mouse click takes 0.25-0.6 s, and a
`move` waits until the walk ends. The reflex play of the kit's **Reacting in time** (dodging a hit a fraction of a
second ahead) is mostly out of reach: plan around hazards rather than reacting to them. `tools/roblox_latency.py`
measures the round trip on another machine. Pictures are stills (`screen_capture`), not video: there is no
recording, and how something looks is judged from stills taken at the moments that matter.
