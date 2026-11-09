
## The engine: LÖVE

- **No scene tree.** A LÖVE game's things are its own Lua tables that have a place (numeric `x` and `y`, a
  `pos`/`position` table, or a love.physics body), named by their keys: from the global table (`/player`,
  `/enemies/3`), or from main.lua's locals that the love.* callbacks use, which is where many games keep their state
  (`/@local/state/level/entities/7`; `game.context()["locals"]` names them). `game.name_of(path)` is the
  last part (`7`). Things the game names itself (`autopilot.track(obj, class)`) or that only a physics body reaches
  have paths of their own (`/@track/2`, `/@body/5`). A table met under several keys is named by its shortest path.
  A module's own locals (`local enemies = {}` in enemies.lua) aren't reachable: read the code, and use what is found.
- **Live things and data.** A thing with a life of its own (`hp`, `health`, `alive`, `state`, a velocity, a body)
  is live; one without (a pickup, a door, but also a sprite's rectangle or a map's marker, data with an `x` and
  a `y`) comes after the live ones in `game.all_actors()` and `game.actors(...)`, and in `game.view()` too, marked
  `prop`. `game.view()` leaves out the members of big collections of data (hundreds of icon rectangles are nothing on
  screen).
- **Classes**: `game.actors("Enemy")` matches a thing's class as the game says it, case aside: what
  `autopilot.track` named, its class's name (middleclass's `obj.class.name`), a `type`/`kind`/`tag` field, its
  metatable's `__name`, or the table it is in (`enemies`, `enemy` for the members of `enemies`). A class nothing is
  is an error, not an empty list. `game.all_actors()` is every thing found; `game.named("bat")` searches their
  names and paths. `transforms` says a thing isn't `active` when it says so itself (`dead`, `removed`, `alive =
  false`, `visible = false`).
- **Places**: a table with `x`, `y` and a width (`w`/`width`, `h`/`height`) has its top left at `x`, `y`: its place
  is its middle. A circle (`r` or `radius`) or a point has its middle at `x`, `y`. A body's place is the body's
  position. Velocities come from the game's fields (`vx`/`vy`, `dx`/`dy`, `velocity`...) or the body, else from how
  the thing moved between frames.
- **Reads and calls** (`game.get`, `game.props`, `game.call`, `game.describe`) reach any Lua value by path: a global
  (`game.get("/", "score")`), a field of a table (`game.get("/player", "hp")`), a nested one with `.`
  (`game.get("/", "gameState.level")`). `game.call` takes arguments by their names in the function's definition (a
  function whose first parameter is `self` is called as a method: `game.call("/door", "open", force=true)`); a
  function that returns several values answers `ReturnValue`, `Return2`... `game.describe(path)` lists a table's
  fields and its functions (its metatable's, a class's methods, too). LÖVE's own modules are there by path:
  `game.call("/love/keyboard", "isDown", ...)`.
- **Values from `game.get` are LÖVE's own**: pixels, x right, y down; a rotation in radians. `game.to_kit(value)` turns
  `{"x", "y"}` into the kit's frame; `game.to_native(vec)` turns a kit point back. `game.set(path, "position", {"x",
  "y"})` puts a thing at a place (its middle). Everything else the kit gives (`game.loc`, `game.locs`, `game.bounds`,
  `game.pose`, `game.view`, `movement`) is in the kit's frame already.
- **The player**: what the game named (`autopilot.player`), else the global `player` (or `hero`, `ship`...), else the
  first thing whose class says player. When that is wrong, `game.request("context", {"player": "/<path>"})` makes
  another thing the player (`"player": ""` lets it find it again). `game.controller` is "": there is no controller.
  `movement(game)` reads the player's fields (`speed`, `jumpForce` or `jumpHeight`, `gravity`): None for what it
  can't find; read the code then.
- **Input**: keys are Unreal names as everywhere (W, SpaceBar, E, Left, LeftMouseButton...) or LÖVE's own (`"w"`,
  `"space"`, `"return"`). They reach the game both as events (`love.keypressed`, `love.keyreleased`,
  `love.textinput`) and as state (`love.keyboard.isDown`, `love.mouse.isDown`), so polled and event-driven input both
  see them. LÖVE has no input actions: `game.request("input", ...)` is an error; press the keys the code reads
  (`game.context()["controls"]` lists them, as LÖVE names them). `game.request("move", {"direction": kit vector,
  "seconds": s})` holds W/A/S/D (or the arrows, when the code doesn't read W/A/S/D) toward the direction;
  `"via": "controller"` moves the player's place directly at its walk speed.
- **Pointing and clicking**: `game.pointer(screen_x, screen_y)`, `game.pointer(world=kit point)` or
  `game.pointer(object=path)` puts the mouse there (`love.mouse.getPosition` reports it, and a `love.mousemoved`
  comes); then `game.press("LeftMouseButton")` clicks there (`love.mousepressed` / `love.mousereleased`). Until the
  first point, the mouse is in the middle of the window. A LÖVE game draws its own buttons: `game.ui()` is empty;
  find a button's place in the code (or its text in `game.screen()`) and click it.
- **The screen**: `game.screen()` is the text the game drew last frame with `love.graphics.print` / `printf`, keyed by
  where it landed on screen (`Text@10,10`: window pixels from the top left). Pieces drawn side by side on a line are
  one text: a terminal-styled game that prints one glyph at a time reads as its words, and a shadow drawn under a
  text isn't the text twice. Text drawn from a `Text` object or an image isn't there.
- **The camera**: the view is the window, unless the game names its camera (`autopilot.camera(...)`) or has a global
  `camera`/`cam` with `x`, `y`: a hump.camera or gamera one is the middle of the view, another the top left
  (`love.graphics.translate(-camera.x, -camera.y)`); `scale` or `zoom` zooms it. `game.view` lists the things in
  that view, nearest the player first.
- **Restarts**: `game.restart()` starts the whole game over (LÖVE's own restart: every global, every module, from
  `main.lua`), on the same session. There are no scenes: `map` is an error; a game with levels has its own way to
  pick one (a field to set, a function to call: read the code).
- **Findings** the module reports by itself: `log_error` (a Lua error in a callback: the game goes on from the next
  frame, as a debugger would let it), `hitch` (a frame over 0.75 s) and `nan` (the player's place).
- **Pictures**: the game's window, as the player sees it (the recording, stills, `describe_frames`), as in the
  other engines. A game without a window has none (`record` and `capture` answer 409).
- **Time**: `game.request("timestep", {"fixed": True, "fps": 30})` makes every frame's dt 1/fps (`love.timer.step`);
  `fixed: False` puts it back. `pause` stops the game's `love.update` (drawing and the module go on).
