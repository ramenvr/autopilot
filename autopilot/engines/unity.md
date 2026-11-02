
## The engine: Unity

**Objects and paths.** Things are GameObjects, named by path: `Main:/Level/Door` (`<scene>:/<root>/<child>`; a path
starting with `/` is looked for in every loaded scene). Objects that share a name under one parent are numbered:
the first is plain (`Coin`), the others `Coin@1`, `Coin@2`... and each keeps its number while it lives (a number is
never given to another object), so a path stays that object or is gone. A component is `<object path>|<Type>`
(`Main:/Player|PlayerMover`); a type is `type:<Full.Name>`, for static members (`game.get("type:UnityEngine.Time",
"timeScale")`, `game.ret("type:GameManager", "Restart")`; a short name works when only one type has it). Names escape `\ / | @` with `\`. `game.name_of(path)`
is the last part (`Coin@2`), as `game.view` / `game.sees` name things.

**Finding things.** `game.actors("Door")`: the GameObjects with a component of that type (a MonoBehaviour's class
name, subclasses and interfaces too; `Collider`, `Rigidbody`, `Camera` work as well). `game.all_actors()`: the scene's
things (objects that aren't plain groups, at the top or under a plain group). `game.named("door")`: by name.
`game.request("find", {"class"?, "name"?, "inactive": True})` also lists inactive objects. The codex's
`scenes/<scene>.txt` lists every object of a scene by these same paths with its components and their values.

**Reading and calling (`game.get`, `game.props`, `game.call`, `game.describe`).** On a component: its fields,
public and private (an auto-property by its name), and properties; methods by name with arguments by parameter name
(`game.call("Main:/Door|Door", "Open")`, `game.call(path, "Damage", amount=10)`). On a GameObject path: its own state
(`name`, `tag`, `layer`, `activeSelf`, `position`, `eulerAngles`, `components`, `children`) and, for a name it doesn't
have, the member of its component that has it (the game's scripts first), so `game.get("Main:/Player", "health")`
reads the player script's field. A method returning `IEnumerator` is started as a coroutine. Values are Unity's own:
metres, x right, y up, z forward; vectors `{"x","y","z"}`; references as paths (`""` for none). Turn a position or
velocity into the kit's frame with `game.to_kit(value)` (and back with `game.to_native(vec)`) before mixing it with
`game.loc`, `game.pose` or `game.view`, which are already in the kit's frame. Rotations read with `game.get` are
`eulerAngles` (y turns right; x > 0 looks down) or quaternions `{"x","y","z","w","euler"}`. When a verification's
setup must move something out of the way (never the player), `game.set(path, "position", game.to_native(v),
why=...)`; an object with a CharacterController keeps its own position until it is disabled and enabled again.

**The player.** The object tagged `Player`, else the one with a `PlayerInput`, else the one with a
`CharacterController` (the main camera's first). If the game's player is something else, name it once in setup():
`game.request("context", {"player": path})`. `game.pose()["at"]` is the middle of its body (the controller's centre:
the feet are `movement(game)["half_height"]` lower), the eye is the main camera. `movement(game)` reads the
CharacterController (half height, radius, step, max slope) and the player script's fields (walk speed from a field
like `walkSpeed`/`moveSpeed`/`speed`, jump from `jumpSpeed`/`jumpForce` or `jumpHeight`, gravity from `gravity` or
`Physics.gravity` (`Physics2D.gravity` without the Physics module, else 9.81), air control from `airControl`); a number it can't find is None: read
the script in the codex and use the real one.

**Input.** Keys go in as a keyboard, mouse and gamepad would (the plugin's own Input System devices): FKey names
(`W`, `SpaceBar`, `E`, `LeftShift`, `Escape`, `One`, `LeftMouseButton`, `MouseX`/`MouseY` in pixels of mouse
movement, `Gamepad_LeftX`, `Gamepad_FaceButton_Bottom`...). Input System actions by `Map/Action` or name:
`game.press("Player/Jump")`, `game.hold("Player/Move", value=(0, 1))` (a Vector2: x right, y forward) then
`game.release("Player/Move")`. `game.context()["controls"]` lists each action with its keys and bindings, and the
legacy Input Manager's axes as `legacy:<Name>` (a button: `game.request("input", {"action": "legacy:Jump"})` presses
its key) and `legacy-axis:<Name>` (with their keys); the codex's `input/` has the action maps. Releasing a key name
the game doesn't have is an error. Mouse look goes through the game's own look code: `game.calibrate_look()` once,
then `game.turn` / `look_at`.
- Code that reads the legacy Input Manager (`UnityEngine.Input.GetKey`, `GetAxis`, `GetButton`, `GetMouseButton`,
  `mousePosition`...) sees the same keys too: the plugin routes those reads through itself when the game compiles.
  Axes follow the project's Input Manager settings: `GetAxis("Horizontal")` from `D` rises toward 1 over a few frames
  (its sensitivity) and falls back when let go, `MouseX` 20 is `GetAxis("Mouse X")` 2 (its sensitivity 0.1),
  `MouseWheelAxis` is one wheel notch, and the gamepad is joystick 1. `game.context()["input"]["legacy_input_reads"]`
  counts the reads routed; with `"keys": False` nothing reaches the game: act through its own methods with
  `game.call` (report them as direct changes), click its UI, and move the player with
  `game.request("move", {"direction": {...}, "seconds": s})`, which moves its CharacterController at the walk speed.
- **Pointing and clicking.** `game.pointer(screen_x, screen_y)` puts the mouse there without moving across (-1 to 1
  from the middle, y up, the same numbers `view` gives a thing), or `game.pointer(world=kit point)`, or
  `game.pointer(object=path)` (where the camera shows it); it answers `{"screen_x", "screen_y", "x", "y", "on_screen"}`. Then
  `game.press("LeftMouseButton")` (or `RightMouseButton`) clicks there, in the world as on the UI. To drag (a selection
  box, a slider): point, `game.hold("LeftMouseButton")`, point again in two or three steps, `game.release(...)`. Games
  often ignore a drag that starts over their HUD, and pan the camera while the pointer is at a screen edge: point well
  inside the view. A click lands on whatever is in front at that spot (another object may cover the one you meant):
  check what got selected or ordered.
- `game.request("move", {"direction": kit vector, "seconds": s, "via"?: "stick"|"keys"|"controller"})` walks a
  direction of the kit's frame through the game's move action (its gamepad stick: any direction; its keys: 8
  directions), or the CharacterController (`controller`); answers `{"moved", "via"}`.

**UI.** `game.screen()` keys texts `<Canvas>.<object>` (uGUI Text and TextMeshPro on screen-space canvases),
`<UIDocument object>.<element>` (UI Toolkit) and, for `OnGUI` (IMGUI), `<Script>.Label` / `<Script>.Box` (`#2`, `#3`
for more) and the buttons' and toggles' words under their ids. `game.ui()` lists uGUI and UI Toolkit buttons, toggles,
sliders (with `min`, `max`), dropdowns and text fields (with `placeholder`) with ids `<Canvas>.<object>`, and `OnGUI`
buttons, toggles and text fields as `<Script>.<words>` / `<Script>.TextField` (`"imgui": true`, with `screen_x`,
`screen_y`); `game.ui_act(id=...)` clicks them as a mouse would (the button's `onClick` runs; an `OnGUI` button returns
true at its next drawing, a toggle flips; `action="set"` gives a toggle's value or a text field's `text`); a UI Toolkit
element also takes `right_click` and `drag` (`to`: the element to drop on). `OnGUI` is only drawn while the game
renders: a batch-mode (`-batchmode`) game calls no `OnGUI`, so none of it is there (`game.context()["imgui"]` says how
many of the game's `OnGUI` controls are routed and how many were drawn). `game.request("screen", {"ui": True})` gives
the controls with the texts. A world-space canvas is a thing in the world: its texts are in `view`'s `look.texts`.

**Sessions.** In the editor, the session is play mode: `game.restart()` reloads the scene play mode started with
(`map=` a scene name or path from the codex's build_scenes.txt), `game.request("session", {"action": "restart",
"fresh": True})` leaves and re-enters play mode (static fields reset too). A player build restarts its first scene,
or the one it was launched with. Objects kept with `DontDestroyOnLoad` (game managers, scores) survive a scene reload,
as they do for the game itself: read their state after a restart instead of assuming it reset. A game that boots
into a menu: set the harness's `start_map` to the gameplay scene, or click through the menu in setup().
`game.request("session", {"action": "start", "map": ...})` while another scene plays switches to it; a scene the
project (a player: the build) hasn't is refused (404) before anything changes. In the editor a script error doesn't
pause the game while the agent plays it (the Console's Error Pause is held off and put back after): the run reports it
(`log_error`). `game.context()["net"]` says how a game is networked (Netcode for GameObjects or Mirror): `standalone`,
`listen_server`, `dedicated_server` or `client` (whose server runs in another process: `pause` can't pause it and
answers `why`).

**What the plugin tells.** `game.view()` tells as Unreal's: visible things (name, path, class, center, size, `box`
(its own box when it is turned) and `tilt`, distance, screen position, crosshair and `crosshair_part` (a bone's
collider), `body_center` / `body_shown` for a character with colliders, `label` (its first text), `look`: `parts` mesh
names, texts, animation clip, `effects` playing (particle systems and VFX Graph), `lights` (its own and a lamp's beside
it), `highlighted` (an outline or overlay material), `limp` (a ragdoll)), a thing's top surface (`top_slope_degrees`;
for a ramp `low_end`, `high_end`, `uphill` and its `lane`), what is `held` (things hanging from the player or its
camera, as its camera draws them), `scene.sun`, and the body (velocity, on_ground, falling, standing_on, touching,
`overhead`). With `openings=True` the doorways it sees (`body.openings`, `free`, `under`), with `ways_up=True` the
ramps and stairs up (`body.ways_up`, with their lanes: `walk_to` climbs to a raised goal by them), and
`game.view(sonar=True)` (`sonar()`, which `walk_to` steers by) feels each way with the player's own capsule (a step
above the floor) and rays down for drops: `by` wall, drop, steep or reach, and `point_blank` ahead. All found with the
player's own CharacterController capsule and colliders: a game whose player has none (no CharacterController or
capsule) gets no openings, ways up or sonar, and a project without the Physics module none of them (the view's
`omitted` lists what it can't tell). `watch` sees objects move, turn and change size, and with `drawn=True`
how a thing is drawn: "part shown" / "part hidden", "effect started" / "effect stopped", "light on" / "light off",
"animation started" / "animation ended", "went limp". Detectors: errors and exceptions in the log, NaN transforms,
falling out of the level, long falls, hitches, on-screen counts above their maximum, `teleport` (a character moved
farther in one frame than its speed allows; never what the agent's own `rc` moved), `actor_growth` (objects of one
kind growing for 2 minutes), `clamp` (a script's number outside its `[Range]` / `[Min]`) and `over_max` (a number
above its sibling maximum: `health` above `maxHealth`).
