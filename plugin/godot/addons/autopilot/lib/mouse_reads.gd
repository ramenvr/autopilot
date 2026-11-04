## The game's own reads and writes of the mouse mode, routed through the addon (as Unity's LegacyInputRewriter routes
## UnityEngine.Input). During a run a captured mouse would lock the desktop's cursor, so the run keeps it off
## (lib/isolation.gd), but a game's mouse look often works only while it reads MOUSE_MODE_CAPTURED, and Godot's own
## override of the mouse mode can't be reached from a script. So as the game starts, each of its scripts that touches
## the mouse mode gets those calls pointed at the autoload, in memory: no file changes, the lines stay where they are,
## and a scene loaded later uses these scripts. While a run goes the game reads the mode it asked for and its window
## holds nothing; any other time the calls do what they did.
## GDScript only, and only where the source is there (a game run from the editor, not an exported one). A C# game, or a
## script this can't reload, reads the mouse as visible during a run. The text of a string that quotes one of these
## calls changes with it.
extends RefCounted

const SKIP := ["res://.godot", "res://addons/autopilot"]
const READ := "AutopilotPlaytest.game_mouse_mode"
const WRITE := "AutopilotPlaytest.set_game_mouse_mode("

static var _kept := []  # the scripts changed: kept loaded, so what loads them later gets these


## Route every script of the game's. Returns how many were changed, those that couldn't be, and what it took.
static func reroute() -> Dictionary:
	var started := Time.get_ticks_usec()
	var out := {"routed": 0, "failed": [], "ms": 0.0}
	var routes := _routes()
	for path in _scripts("res://"):
		var source := FileAccess.get_file_as_string(path)
		if not source.contains("mouse_"):
			continue  # (nearly every script: this runs at each start of the game, so they cost a read and no more)
		var changed := source
		for route in routes:
			changed = route[0].sub(changed, route[1], true)
		if changed == source:
			continue
		var script := ResourceLoader.load(path) as GDScript
		if script == null:
			out.failed.append(path)
			continue
		script.source_code = changed
		if script.reload(true) != OK:
			script.source_code = source
			script.reload(true)
			out.failed.append(path)
			continue
		_kept.append(script)
		out.routed += 1
	out.ms = (Time.get_ticks_usec() - started) / 1000.0
	return out


static func _routes() -> Array:
	var out := []
	for pair in [
		["Input\\.get_mouse_mode\\(\\s*\\)", READ],
		["DisplayServer\\.mouse_get_mode\\(\\s*\\)", READ],
		["Input\\.set_mouse_mode\\(", WRITE],
		["DisplayServer\\.mouse_set_mode\\(", WRITE],
		["Input\\.mouse_mode\\b", READ],
	]:
		out.append([RegEx.create_from_string("(?<![\\w.])" + pair[0]), pair[1]])
	return out


static func _scripts(dir: String) -> Array:
	var out := []
	var d := DirAccess.open(dir)
	if d == null:
		return out
	for name in d.get_directories():
		var sub := dir.path_join(name)
		if not SKIP.has(sub):
			out.append_array(_scripts(sub))
	for name in d.get_files():
		if name.get_extension() == "gd":
			out.append(dir.path_join(name))
	return out
