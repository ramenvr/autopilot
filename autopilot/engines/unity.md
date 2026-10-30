
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
`Physics.gravity` (`Physics2D.gravity` without the Physics module, else 9.81)); a number it can't find is None: read
the script in the codex and use the real one.

**Input.** Keys go in as a keyboard, mouse and gamepad would (the plugin's own Input System devices): FKey names
(`W`, `SpaceBar`, `E`, `LeftShift`, `Escape`, `One`, `LeftMouseButton`, `MouseX`/`MouseY` in pixels of mouse
movement, `Gamepad_LeftX`, `Gamepad_FaceButton_Bottom`...). Input System actions by `Map/Action` or name:
`game.press("Player/Jump")`, `game.hold("Player/Move", value=(0, 1))` (a Vector2: x right, y forward) then
`game.release("Player/Move")`. `game.context()["controls"]` lists each action with its keys and bindings; the
codex's `input/` has the action maps. Mouse look goes through the game's own look code: `game.calibrate_look()` once,
then `game.turn` / `look_at`.
- Code that reads the legacy Input Manager (`UnityEngine.Input.GetKey`, `GetAxis`, `GetButton`, `GetMouseButton`,
  `mousePosition`...) sees the same keys too: the plugin routes those reads through itself when the game compiles.
  Axes follow the project's Input Manager settings: `GetAxis("Horizontal")` from `D` rises toward 1 over a few frames
  (its sensitivity) and falls back when let go, `MouseX` 20 is `GetAxis("Mouse X")` 2 (its sensitivity 0.1),
  `MouseWheelAxis` is one wheel notch, and the gamepad is joystick 1. `game.context()["input"]["legacy_input_reads"]`
  counts the reads routed; with `"keys": False` nothing reaches the game: act through its own methods with
  `game.call` (report them as direct changes), click its UI, and move the player with
  `game.request("move", {"direction": {...}, "seconds": s})`, which moves its CharacterController at the walk speed.
- **Pointing and clicking.** `game.request("pointer", {"screen_x": x, "screen_y": y})` puts the mouse there without
  moving across (-1 to 1 from the middle, y up, the same numbers `view` gives a thing), or `{"world": kit point}`, or
  `{"object": path}` (where the camera shows it); it answers `{"screen_x", "screen_y", "x", "y", "on_screen"}`. Then
  `game.press("LeftMouseButton")` (or `RightMouseButton`) clicks there, in the world as on the UI. To drag (a selection
  box, a slider): point, `game.hold("LeftMouseButton")`, point again in two or three steps, `game.release(...)`. Games
  often ignore a drag that starts over their HUD, and pan the camera while the pointer is at a screen edge: point well
  inside the view. A click lands on whatever is in front at that spot (another object may cover the one you meant):
  check what got selected or ordered.
- `game.request("move", {"direction": kit vector, "seconds": s, "via"?: "stick"|"keys"|"controller"})` walks a
  direction of the kit's frame through the game's move action (its gamepad stick: any direction; its keys: 8
  directions), or the CharacterController (`controller`); answers `{"moved", "via"}`.

**UI.** `game.screen()` keys texts `<Canvas>.<object>` (uGUI Text and TextMeshPro on screen-space canvases) and
`<UIDocument object>.<element>` (UI Toolkit). `game.ui()` lists uGUI and UI Toolkit buttons, toggles, sliders,
dropdowns and text fields with ids `<Canvas>.<object>`; `game.ui_act(id=...)` clicks them as a mouse would (the
button's `onClick` runs). Text drawn with `OnGUI` (IMGUI) can't be read or clicked. A world-space canvas is a thing in
the world: its texts are in `view`'s `look.texts`.

**Sessions.** In the editor, the session is play mode: `game.restart()` reloads the scene play mode started with
(`map=` a scene name or path from the codex's build_scenes.txt), `game.request("session", {"action": "restart",
"fresh": True})` leaves and re-enters play mode (static fields reset too). A player build restarts its first scene,
or the one it was launched with. Objects kept with `DontDestroyOnLoad` (game managers, scores) survive a scene reload,
as they do for the game itself: read their state after a restart instead of assuming it reset. A game that boots
into a menu: set the harness's `start_map` to the gameplay scene, or click through the menu in setup().

**What the plugin leaves out.** `game.view()` tells visible things (name, path, class, center, size, distance,
screen position, crosshair, `look.parts` mesh names, texts, animation clip) and the body (velocity, on_ground,
falling, standing_on, touching), with world-aligned boxes, and `game.view(sonar=True)` (`sonar()`, which `walk_to`
steers by) feels each way with the player's own capsule (a step above the floor) and rays down for drops: `by` wall,
drop, steep or reach. It has no `held`, `openings`, `ways_up`, `point_blank` or `lane` (listed in its `omitted`), so `walk_to` finds no way
up to a raised goal by itself: walk it to the ramp's or stairs' foot and top first (from the codex, `game.locs`).
`walk_to` finds its way round obstacles on the map it feels out with the sonar, as in Unreal. `watch` sees objects move, turn and change size, not meshes appearing or effects. Detectors: errors
and exceptions in the log, NaN transforms, falling out of the level, long falls, hitches, on-screen counts above their
maximum.
