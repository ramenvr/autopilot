
## The engine: Godot

- **Things are nodes**, named by their paths in the scene tree: `/root/<Scene>/Player`, `/root/<Scene>/Enemies/Bat2`.
  A "component" is a child node (`/root/<Scene>/Player/Head/Camera3D`). Autoloads (the game's global singletons) are
  `/root/<Name>` and outlive scene changes. `game.name_of(path)` is the last part (`Bat2`).
- **Classes**: `game.actors("CharacterBody3D")` (a Godot class: subclasses too) or `game.actors("Enemy")` (a script's
  `class_name`, scripts extending it too). A script without `class_name` is matched by its file name
  (`game.actors("bat")` for `bat.gd`). `game.actors("group:enemies")`: the nodes in a group (`add_to_group`, or
  `groups=[...]` in a .tscn). `game.all_actors()`: the scene's things (its direct children, physics bodies and areas,
  instanced scenes), not every mesh and collision shape under them; `game.named("bat")` searches those.
- **Reads and calls** (`game.get`, `game.props`, `game.call`, `game.describe`) reach script variables, script constants
  (`SPEED`, read only), built-in properties and methods. A property's part with Godot's `a:b` path: `game.get(path,
  "position:y")`. `game.call` takes arguments by name; leave out those with defaults. `game.describe(path)` lists the
  script's own variables, constants and functions first. Engine singletons and autoloads by name:
  `game.call("Input", "is_action_pressed", action="jump")`, `game.get("/root/<Autoload>", "score")`.
- **Values from `game.get` are Godot's own**: metres, Godot's axes (x right, y up, -z forward), vectors as
  `{"x","y","z"}`, a node as its path, a resource as its `res://` path, an enum as its name. `game.to_kit(value)` turns a
  position or velocity into the kit's frame (`kit = 100 * (-z, x, y)`); `game.to_native(vec)` turns a kit point back
  for `game.set` / `game.call`. Everything else the kit gives (`game.loc`, `game.locs`, `game.bounds`, `game.pose`,
  `game.view`, `game.sees`, `movement`) is in the kit's frame already. A node's `rotation` from `game.get` is in radians.
- **The player**: the physics body the current camera hangs from (else the first node in group `player`). `game.pawn`
  is its path; `game.pose()["at"]` is the middle of its body (its collision shape's centre), the feet `half_height`
  below. `game.controller` is "" (Godot has no controller): use `game.pose()` / `game.view_rotation()` for the view.
- **Input**: keys are Unreal names as everywhere (W, SpaceBar, E, LeftMouseButton, MouseX...); the addon turns them into
  Godot input events, so the game's `_input`/`_unhandled_input`, `Input.is_action_pressed` and `Input.get_vector` all
  see them. The game's InputMap actions (project.godot `[input]`, listed with their keys in controls.txt and the codex
  README) can be pressed by name: `game.press("interact")`, `game.hold("sprint")`. Prefer an action to its key when the
  code reads the action. Mouse look: `game.mouse` / `game.turn` move the mouse (`InputEventMouseMotion.relative`).
  Headless, the mouse is never captured: a game that turns its view only while `Input.mouse_mode` is captured won't
  turn; read its code for that, and call it out rather than guess.
- **Scenes**: `game.restart()` reopens the scene the game started in (a game that boots into a menu: the menu);
  `game.restart(map="res://scenes/level.tscn")` (or the scene file's name, `map="level"`) opens another. Autoloads keep
  their variables across a restart in a running game (as a game's own scene change would): read or reset what the
  verification needs. In the editor, a restart is a fresh game process (autoloads too). Game time (`game.t`) starts at 0
  in each scene.
- **Physics**: a CharacterBody doesn't step up ledges (`movement(game)["step"]` is None, so `reach_up` is the jump's
  peak alone); its `floor_max_angle` is `max_slope`. Walk speed and jump come from the player script's variables or
  constants (`SPEED`, `JUMP_VELOCITY`...); `movement(game)` says None for what it can't find: read the script then.
- **`game.view`** here tells: visible things (name, path, class, center, size, distance, screen position,
  crosshair_on, top/bottom against the feet, look: meshes, lights, animation, 3D texts), the camera (fov horizontal),
  the body (on_ground, falling, standing_on, touching, floor slope) and, with `sonar=True`, the sonar ways. It never
  tells `held`, `openings`, `ways_up`, slope ends or lanes, `under`/`overhead`, highlights or effects (the answer's
  `omitted` lists them): use the codex and `game.locs` / `game.bounds` for those. Without `ways_up`, `walk_to` finds no way
  up to a raised goal by itself: walk it to the ramp's or stairs' foot and top first.
- **`game.screen()`** keys are `<UI>.<Node name>`: `<UI>` is the CanvasLayer or the scene the control belongs to
  (`HUD.ScoreLabel`, `PauseMenu.ResumeButton`). `game.ui()` lists Buttons, CheckBoxes, OptionButtons (combo), Sliders and
  SpinBoxes (slider), LineEdits (text_input); `ui_act` clicks with the mouse at the control's middle.
- **2D games**: positions are (x right, y down) pixels as kit (X = x, Y = 0, Z = -y), 1 unit = 1 pixel: kit X is right,
  kit Z is up. `game.view` lists drawn sprites; the 3D controllers that turn the view (walk_to, look_at) don't apply:
  press the movement actions and read `game.loc`.
- **In the editor**, the brain starts the game itself (the session is started before the harness runs). With no game
  running, only `game.context()` answers.
