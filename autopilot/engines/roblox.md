
## The engine: Roblox

- **A play session has two sides.** The game runs as a server (Scripts: the game's rules and its authoritative state)
  and the player's client (LocalScripts: input, camera, UI). Reads and writes (`game.get`, `game.set`, `game.call`,
  `game.props`, `game.describe`) go to the server, which is what the game's Scripts see; a path the server doesn't
  have (an instance only the client made) is read on the client. The player's own GUI, PlayerScripts and the camera
  are the client's. Put `client:` or `server:` in front of a path to choose: `game.get("client:/Workspace/Thing",
  "Transparency")`. Everything else the kit gives (`game.view`, `game.sees`, `game.screen`, `game.ui`, `game.pose`,
  `game.actors`, `game.loc`, `game.locs`, `game.bounds`, inputs) is the client's: what the player sees, a moment after
  the server changed it (replication).
- **Things are instances**, named by their paths from the DataModel: `/Workspace/Map/Door`, `/ReplicatedStorage/
  Modules/Config`, `/ServerScriptService/GameManager`, `/Players/<Name>/leaderstats/Coins`. Instances that share a name
  under one parent all get their debug-ID suffix, for example `Coin@617854` and `Coin@617860`, not sequential
  indices. Use the exact paths returned by discovery; do not invent suffixes. A plain duplicate name resolves to
  the lowest debug ID. `game.name_of(path)` is the last part.
  `LocalPlayer`, `Character`, `Humanoid` and `Camera` name the player's own.
- **Finding things**: `game.actors("Model")` / `game.actors("BasePart")` (a Roblox class, subclasses too),
  `game.actors("tag:Enemy")` (a CollectionService tag; a plain name that is a tag and no class works too), or the name
  of a script inside a model (`game.actors("DoorScript")`: the models holding a script of that name); a name that is
  none of these is an error.
  `game.all_actors()`: the Workspace's things (its models and parts at the top or in folders, not the parts inside a
  model; a model of models, such as a level's "Map", is a folder). `game.named("coin")` searches those by name.
- **Reads and calls**: a property name is a property of the instance's class (`Transparency`, `CFrame`, `Health`), else
  one of its attributes (`GetAttribute`), else the Value of a child value object of that name (an IntValue `Coins`),
  else, on a player or its character, its leaderstats entry: `game.get(game.pawn, "Coins")`. Also `position` (a model's
  or part's pivot point; `game.set(path, "position", game.to_native(v), why=...)` moves it as `PivotTo` does),
  `attributes`, `tags`, `children`. A ModuleScript's path reads the table it returns (`game.get(module, "Score")`, as
  the server's scripts have it) and `game.call(module, "AddScore", args=[5])` calls its function (`self=True` for a
  method). An instance's methods take arguments by name (`game.call(humanoid, "TakeDamage", amount=10)`) or
  `args=[...]`. Calling a RemoteEvent's `FireServer` on a `client:` path sends what the game's client would send.
- **Values from `game.get` are Roblox's own**: studs, Roblox's axes (x right, y up, -z forward), Vector3 as
  `{"x","y","z"}`, a CFrame as `{"position", "look", "up", "euler"}`, Color3 as `{"r","g","b"}`, an Enum as its name, an
  instance as its path. `game.to_kit(value)` turns a position or velocity into the kit's frame (`kit = 28 * (-z, x, y)`:
  1 stud = 28 cm); `game.to_native(vec)` turns a kit point back. `game.loc`, `game.bounds`, `game.pose`, `game.view` and
  `movement(game)` are in the kit's frame already.
- **The player** is the local player's character (`game.pawn`, a Model); `game.controller` is the Player.
  `game.pose()["at"]` is its HumanoidRootPart's centre, the feet `half_height` below (the root part's half height plus
  the Humanoid's HipHeight). `movement(game)` reads the Humanoid: WalkSpeed, JumpPower (or JumpHeight), the Workspace's
  Gravity, MaxSlopeAngle; a Humanoid steps up ledges lower than about its hip height, steers fully in the air, and
  takes no fall damage unless the game deals it. When the character dies Roblox spawns a new one after RespawnTime:
  `game.pawn` changes; wait for it (`game.context(fresh=True)["character"]`).
- **Input**: keys are Unreal names as everywhere (W, SpaceBar, E, LeftShift, One, LeftMouseButton, MouseX, MouseY,
  MouseScrollUp...), or a Roblox KeyCode's name (`Return`, `KeypadOne`). They go in as a player's keyboard and mouse do
  (Studio's virtual input), so UserInputService, ContextActionService, ProximityPrompts and the default PlayerModule
  (W/A/S/D walking, SpaceBar jumping) all see them, whether or not Studio's window is in front. An action the game
  bound with ContextActionService can be pressed by name (`game.press("Sprint")`: its first key);
  `game.context()["controls"]` lists them. A key Roblox keeps for its own menus (Escape) is refused with a 409.
  A ProximityPrompt is triggered by its key (`E` by default) while the player is within its MaxActivationDistance and
  it is shown; one with a HoldDuration needs `hold_for(game, "E", hold + 0.2)`. A ClickDetector is clicked by pointing
  at the part (`game.request("pointer", {"object": path})`) and pressing LeftMouseButton.
- **Gamepad**: `Gamepad_FaceButton_Bottom` (ButtonA), `Gamepad_FaceButton_Right` (ButtonB), `_Left` (ButtonX), `_Top`
  (ButtonY), `Gamepad_LeftShoulder`/`RightShoulder` (L1/R1), `Gamepad_LeftTrigger`/`RightTrigger` (L2/R2),
  `Gamepad_DPad_Up`..., `Gamepad_Special_Right` (Start), or Roblox's names (`ButtonA`, `DPadLeft`): each goes in as its
  KeyCode, so the game's ContextActionService actions bound to it and InputBegan handlers that check the KeyCode get
  it (the InputObject's UserInputType is Keyboard, not Gamepad1: code that checks for a gamepad by UserInputType
  doesn't see it). ButtonA also jumps, as Roblox's gamepad controls do, unless the game bound ButtonA itself. The sticks
  and analog triggers are axes for `game.hold(key, value=...)` (or a press for a moment): `Gamepad_LeftX` /
  `Gamepad_LeftY` (-1..1, y forward) walk the player relative to the camera as Roblox's gamepad controls do,
  `Gamepad_RightX` / `Gamepad_RightY` turn the camera (about 240 degrees a second full over), and
  `Gamepad_LeftTriggerAxis` / `Gamepad_RightTriggerAxis` past 0.5 are L2/R2. Code that reads a stick itself (InputChanged
  with Thumbstick1, GetGamepadState) sees nothing: drive what it drives some other way and say so.
- **Devices and touch**: `game.request("device", {"device": "phone"})` makes Studio simulate a phone (also `tablet`,
  `console`, `vr`, `desktop` for none, or a device's name from `{"action": "list"}`; `orientation`: `portrait` or
  `landscape`, else the game's own), as a player on that device sees the game: its screen size, the game's mobile UI,
  `TouchEnabled`, Roblox's on-screen thumbstick and jump button (a console: Roblox's ten-foot interface). A session's
  start takes `device` too (`game.request("session", {"action": "restart", "device": "phone"})`), and the user's own
  device comes back when the run ends. On a phone or tablet, `game.request("touch", {...})` is a finger:
  `{"action": "tap", "object": path}` (or `"ui"`: an id from `game.ui()`, `"screen_x"/"screen_y"` -1..1, `"x"/"y"`,
  `"world"`), `"double_tap"`, `"long_press"` (`seconds`), `"swipe"` with `"to": {spot}`, and `"down"` / `"move"` /
  `"up"` for a drag (the on-screen thumbstick: down at its spot, move up from there, hold, up). The game gets real
  touches (TouchStarted, TouchTap, TouchSwipe, a GUI button's Activated); the answer's `as` is `"touch"`. Off a touch
  device the finger is the mouse's left button (`as` `"mouse"`). On a touch device the camera turns by a finger's drag
  (a swipe on the right half of the screen), not by MouseX or the right stick. `game.context()["device"]` and
  `["input"]` say what the game gets now.
- **Camera**: Roblox's default camera turns only while the right mouse button is held (a player drags); the plugin holds
  it for `game.turn` / `game.mouse` when the mouse is free, so `look_at` and `walk_to` work, and when the game locks the
  mouse (first person, shift lock, its own camera) it moves the mouse alone. `game.calibrate_look()` measures either.
  `game.context()["camera_type"]` and `["mouse_behavior"]` say which. A game with a Scriptable camera turns the view in
  its own code: read it.
- **Moving without the keys**: `game.request("move", {"direction": kit vector, "seconds": s})` walks the character
  that way as an analog stick would (the player's Move, after the PlayerModule's: `"via": "stick"`, the default;
  `"keys"` for W/A/S/D; `"controller"` for the Humanoid's own Move, for a game whose controls don't go through the
  Player); `scale` -1..1 (negative walks the other way); answers `{"moved", "via"}`.
- **UI**: `game.screen()` keys texts `<ScreenGui>.<Name>` (TextLabels, TextButtons, TextBoxes in the player's
  PlayerGui); `game.ui()` lists GuiButtons (`button`) and TextBoxes (`text_input`, its `text` and `placeholder`) on
  screen; `ui_act` clicks with the mouse at the element's middle (its answer's `fired` says whether the button took it);
  `action="set"` types into a TextBox and submits it. Roblox has no checkbox, slider or drop-down of its own: a game's
  are its own buttons and frames (click them; read their state with `game.get`). Texts in the world (SurfaceGui,
  BillboardGui) are in `view`'s `look.texts`.
- **Sessions**: a run plays the place in Studio as the Play button does (a server and one player). `game.restart()`
  stops the session and plays again: the server's and client's scripts start over, the place as it is saved in Studio
  (nothing a session changed stays). Game time (`game.t`) is the Workspace's DistributedGameTime, from 0 in each session.
  `game.context()["net"]` is `{"mode": "client", "rc_side": "server"}` (with `clients`, the players, in a multiplayer
  test): the run plays a client, and reads and writes go to the server. `game.loc(path)` of an instance with no place
  (a script, a value, a GUI object) is None; `game.engine.transforms(game, [path])` still says whether it is `active`.
- **Several players**: most Roblox games are multiplayer, and some bugs only show with two players (state kept per
  player, what one player does reaching another's screen, handing things over, teams). `game.restart(players=2)` plays
  the place as Studio's multiplayer test does (a server and a client per player, each a Studio window of its own:
  slower to start, up to a minute) and `game.player(2)` is player 2: its keys, view, screen, UI and the kit's
  controllers act as player 2, and `game.get`/`game.set` of `Character`, `Humanoid`, `LocalPlayer` through it are player
  2's (`game.player(2).set("Character", "position", ...)`). The players are Player1, Player2... (`game.players()`);
  `game` is player 1, and another player's character is a thing in `view` with `player` true. Compare what each player
  sees (`game.screen()` against `game.player(2).screen()`) to check that a change reached everyone. A ProximityPrompt
  on another player shows only within a few studs. `game.restart(players=1)` goes back to one. The device simulator
  (touch) is for one player only.
- **Pictures**: the game's view (3D and the game's own UI, not Roblox's top bar) comes from Studio itself, through
  Studio's MCP server (the agent service starts it beside Studio), so recordings and `describe_frames` work as in the
  other engines while a game plays; Studio must have its MCP server on (Assistant, ..., Settings, MCP Servers). When it
  isn't, there are no frames: settle a request about how something looks by what `game.view` tells (parts shown,
  transparency, colours read with `game.get`, lights, animations, sounds, effects, texts) and say so.
- **What `view` tells**: as the other engines' (its `omitted` is empty): visible things (name, path, class, tags,
  center, size, distance, screen position, crosshair_on and crosshair_part, health of a Humanoid, tilt and box,
  blocks_player, body_center, the top surface's slope and ends, look: parts, lights, animations, sounds, effects,
  texts, highlighted, limp), `held` (an equipped Tool, what is welded onto the character, the camera's viewmodel),
  `scene.sun`, the body (on_ground, falling, state, health, standing_on, touching, floor slope, overhead), with
  `ways_up=True` the ramps and stairs up from the player's floor (`walk_to` climbs them to a raised goal), with
  `openings=True` the gaps round it, and with `sonar=True` the sonar ways.  A Humanoid steps up a ledge lower than
  `movement()`'s `step` and walks slopes to its MaxSlopeAngle (taken as at most 60 degrees).  Detectors: errors and
  warnings the game's scripts print (server and client), NaN positions, parts falling out of the world, long falls,
  hitches, on-screen counts above their maximum,
  and on the server teleports (a character, the player's or an NPC's, jumping farther in a frame than its speed allows;
  a suspicion, and your own `set` of a position is one too), actor growth (the Workspace's things of one kind, by tag
  or name, still growing after 120 s) and numbers over their maximum (an attribute or IntValue/NumberValue above its
  sibling `Max<Name>`, `max_<name>`, `<Name>Max`, `<name>_max`..., leaderstats too).  No `clamp`: Roblox declares no
  ranges.
