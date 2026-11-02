## In-game detectors, frame accurate (docs/protocol.md "Findings"): log_error, nan, out_of_world, long_fall, hitch,
## hud_fraction, and as Unreal's: teleport (a body moved farther in one frame than its speed allows), actor_growth (the
## nodes of one kind kept growing for growth_seconds), clamp (a script variable outside its @export_range) and
## over_max (a number variable above its sibling max_<name> / <name>_max). Errors come from a Logger (push_error, script errors, engine errors), which may be called on any thread.
extends RefCounted

const Frame = preload("frame.gd")


class ErrorLog extends Logger:
	var mutex := Mutex.new()
	var lines := []

	func _log_error(function: String, file: String, line: int, code: String, rationale: String, _editor_notify: bool, error_type: int, script_backtraces: Array) -> void:
		var message := rationale if rationale != "" else code
		var where := "%s:%d" % [file, line]
		# push_error's own place is in the engine: the script's place is the backtrace's first frame
		for bt in script_backtraces:
			if bt != null and not bt.is_empty() and bt.get_frame_count() > 0:
				where = "%s:%d" % [bt.get_frame_file(0), bt.get_frame_line(0)]
				break
		mutex.lock()
		if lines.size() < 1000:
			lines.append({"type": error_type, "message": message, "where": where, "function": function})
		mutex.unlock()

	func _log_message(_message: String, _error: bool) -> void:
		pass

	func drain() -> Array:
		mutex.lock()
		var out := lines
		lines = []
		mutex.unlock()
		return out


var host: Node
var world
var ui
var errors := ErrorLog.new()
var enabled := true
var counts := {}  # signature -> times seen
var config := {"long_fall_seconds": 8.0, "hitch_seconds": 0.75, "hud_every": 0.25, "scan_every": 0.5, "out_of_world_margin": 50.0,
	"teleport_factor": 3.0, "teleport_min": 1000.0, "growth_seconds": 120.0, "property_every": 0.5}
var _bodies := {}  # body -> {"at": kit point, "frames", "speed"}: the bodies teleport follows
var _growth := []  # node counts by kind, one sample every 10 s of game time (actor_growth)
var _growth_at := 0.0
var _property_at := 0.0
var _props := {}  # script -> what clamp and over_max read of it
var _falling := 0.0
var _hud_at := 0.0
var _scan_at := 0.0
var _kill_y = null
var _scene: Node = null
var _scene_at := 0
var _flagged := {}


func _init(owner_node: Node, world_module, ui_module) -> void:
	host = owner_node
	world = world_module
	ui = ui_module
	OS.add_logger(errors)


func shutdown() -> void:
	OS.remove_logger(errors)


func configure(args: Dictionary) -> void:
	if args.has("enabled"):
		enabled = bool(args.enabled)
	for k in config:
		if args.has(k):
			config[k] = float(args[k])


## Log events and findings for this frame. emit(event_dict) sends an event; t: game time; real_dt: the frame's wall
## time; paused: whether the game is paused.
func tick(emit: Callable, t: float, real_dt: float, paused: bool) -> void:
	for e in errors.drain():
		var error: bool = e.type != Logger.ERROR_TYPE_WARNING
		var category := "Script" if e.type == Logger.ERROR_TYPE_SCRIPT else ("Shader" if e.type == Logger.ERROR_TYPE_SHADER else "Engine")
		var msg := "%s (%s)" % [e.message, e.where]
		emit.call({"kind": "log", "t": t, "verbosity": "Error" if error else "Warning", "category": category, "message": msg.substr(0, 2000)})
		if error and enabled:
			var sig: String = "log_error:" + _normalize(e.message) + "@" + e.where
			_finding(emit, t, {"detector": "log_error", "severity": "error", "summary": "%s error: %s" % [category, e.message.substr(0, 300)],
				"signature": sig, "details": {"message": e.message, "where": e.where, "function": e.function, "category": category}})
	if not enabled:
		return
	var s: Node = world.scene()
	if s != _scene:
		_scene = s
		_scene_at = Time.get_ticks_msec()
		_kill_y = null
		_flagged.clear()
		_falling = 0.0
		_bodies.clear()
		_growth.clear()
		_growth_at = t
	var loading := Time.get_ticks_msec() - _scene_at < 3000
	if not paused and not loading and real_dt > config.hitch_seconds:
		_finding(emit, t, {"detector": "hitch", "severity": "warning", "summary": "A frame took %.2f s" % real_dt,
			"signature": "hitch", "details": {"seconds": snappedf(real_dt, 0.01)}})
	var p: Node = world.player()
	if p is CharacterBody3D or p is CharacterBody2D:
		var falling_now: bool = not p.is_on_floor() and (p.velocity.y < 0.0 if p is CharacterBody3D else p.velocity.y > 0.0)
		if paused:
			pass
		elif falling_now:
			_falling += real_dt * Engine.time_scale
		else:
			_falling = 0.0
		if _falling > config.long_fall_seconds:
			_falling = -1e9  # once per fall
			_finding(emit, t, {"detector": "long_fall", "severity": "warning", "summary": "%s kept falling for %d s" % [p.name, int(config.long_fall_seconds)],
				"signature": "long_fall:" + str(p.name), "actor": str(p.name), "where": _where(p)})
	if not paused and s != null:
		_teleports(emit, t, s)
	if s != null and t >= _growth_at:
		_growth_at = t + 10.0
		_count_kinds(emit, t)
	var now := Time.get_ticks_msec() / 1000.0
	if now - _property_at >= config.property_every and s != null and not loading:
		_property_at = now
		_properties(emit, t)
	if now - _hud_at >= config.hud_every:
		_hud_at = now
		_hud_fraction(emit, t)
	if now - _scan_at >= config.scan_every and s != null and not loading:
		_scan_at = now
		_scan(emit, t, s)


func _where(n: Node):
	if n is Node3D:
		return Frame.kit(n.global_position)
	if n is Node2D:
		return Frame.kit2d(n.global_position)
	return null


static func _normalize(s: String) -> String:
	var re := RegEx.create_from_string("[0-9]+")
	return re.sub(s, "#", true).substr(0, 160)


func _finding(emit: Callable, t: float, f: Dictionary) -> void:
	var sig: String = f.signature
	var n: int = counts.get(sig, 0) + 1
	counts[sig] = n
	if not (n == 1 or n == 10 or n == 100 or n == 1000):
		return
	f.t = t
	f.count = n
	f.source = "engine"
	emit.call({"kind": "finding", "t": t, "finding": f})


var _fraction := RegEx.create_from_string("(\\d+)\\s*/\\s*(\\d+)")


func _hud_fraction(emit: Callable, t: float) -> void:
	var texts: Dictionary = ui.screen({}).texts
	for key in texts:
		for m in _fraction.search_all(texts[key]):
			var a := int(m.get_string(1))
			var b := int(m.get_string(2))
			if b > 0 and a > b:
				_finding(emit, t, {"detector": "hud_fraction", "severity": "warning",
					"summary": "HUD text %s shows %d/%d: more than its stated maximum" % [key, a, b],
					"signature": "hud_fraction:" + key, "details": {"key": key, "text": texts[key], "value": a, "max": b}})


## NaN transforms and bodies below the level (the lowest drawn point at the scene's start, less a margin).
func _scan(emit: Callable, t: float, s: Node) -> void:
	if _kill_y == null and s is Node3D:
		var low := INF
		for v in s.find_children("*", "VisualInstance3D", true, false):
			if v is GeometryInstance3D:
				low = minf(low, (v.global_transform * v.get_aabb()).position.y)
		_kill_y = (low if low != INF else 0.0) - config.out_of_world_margin
	var checked := 0
	var stack: Array = [s]
	while stack.size() > 0 and checked < 5000:
		var n: Node = stack.pop_back()
		stack.append_array(n.get_children())
		if n is Node3D:
			checked += 1
			var pos: Vector3 = n.global_position
			if not (is_finite(pos.x) and is_finite(pos.y) and is_finite(pos.z)) or not n.global_basis.is_finite():
				if not _flagged.has(n):
					_flagged[n] = true
					_finding(emit, t, {"detector": "nan", "severity": "error", "summary": "%s has a NaN or infinite transform" % n.name,
						"signature": "nan:" + str(n.name), "actor": str(n.name)})
			elif _kill_y != null and (n is PhysicsBody3D) and pos.y < _kill_y and not _flagged.has(n):
				_flagged[n] = true
				_finding(emit, t, {"detector": "out_of_world", "severity": "warning",
					"summary": "%s fell out of the world (below %.0f m)" % [n.name, _kill_y],
					"signature": "out_of_world:" + str(n.name), "actor": str(n.name), "where": Frame.kit(pos)})
		elif n is Node2D:
			checked += 1
			var p2: Vector2 = n.global_position
			if not (is_finite(p2.x) and is_finite(p2.y)) and not _flagged.has(n):
				_flagged[n] = true
				_finding(emit, t, {"detector": "nan", "severity": "error", "summary": "%s has a NaN or infinite transform" % n.name,
					"signature": "nan:" + str(n.name), "actor": str(n.name)})


# --- as Unreal's: teleport, actor_growth, clamp, over_max --------------------------------------------

## What a node is, as a finding names its kind (Unreal's ClassLabel): its script's class_name, else its script file's
## name, else the scene it was saved as (an instanced scene's root), else its Godot class.
static func kind_of(n: Node) -> String:
	var sc = n.get_script()
	if sc is Script:
		if str(sc.get_global_name()) != "":
			return str(sc.get_global_name())
		if sc.resource_path != "" and not ("::" in sc.resource_path):
			return sc.resource_path.get_file().get_basename()
	if n.scene_file_path != "":
		return n.scene_file_path.get_file().get_basename()
	return n.get_class()


static func _fmt(v: float) -> String:
	return str(int(v)) if v == floorf(v) and absf(v) < 1e15 else str(snappedf(v, 0.001))


static func _vec1(k: Dictionary) -> Dictionary:
	return {"X": snappedf(k.X, 0.1), "Y": snappedf(k.Y, 0.1), "Z": snappedf(k.Z, 0.1)}


## teleport: a character body (the player's or another's) moved farther in one frame than teleport_factor times its
## speed allows (at least teleport_min units): a respawn, a bad snap, a body pushed out of a wall. A suspicion.
func _teleports(emit: Callable, t: float, s: Node) -> void:
	var dt := host.get_process_delta_time() * Engine.time_scale
	var alive := {}
	var bodies: Array = s.find_children("*", "CharacterBody3D", true, false) + s.find_children("*", "CharacterBody2D", true, false)
	var p: Node = world.player()
	if (p is PhysicsBody3D or p is PhysicsBody2D) and not bodies.has(p):
		bodies.append(p)
	for b in bodies:
		if not is_instance_valid(b) or not b.is_inside_tree():
			continue
		alive[b] = true
		var at: Dictionary = Frame.kit(b.global_position) if b is Node3D else Frame.kit2d(b.global_position)
		var here := Vector3(at.X, at.Y, at.Z)
		var track: Dictionary = _bodies.get(b, {"at": here, "frames": 0, "speed": 0.0})
		var v = Vector3.ZERO
		if b is CharacterBody3D or b is CharacterBody2D:
			v = b.velocity
		elif b is RigidBody3D or b is RigidBody2D:
			v = b.linear_velocity
		var speed: float = v.length() * (Frame.SCALE if b is Node3D else 1.0)
		if track.frames > 5:
			var allowed := maxf(config.teleport_min, config.teleport_factor * maxf(speed, track.speed) * dt)
			var moved := here.distance_to(track.at)
			if moved > allowed:
				var label := kind_of(b)
				_finding(emit, t, {"detector": "teleport", "severity": "suspicion",
					"summary": "%s moved %.0f units in one frame (at most %.0f expected)" % [label, moved, allowed],
					"signature": "teleport:" + label, "actor": str(b.name), "where": _vec1(at),
					"details": {"from": _vec1({"X": track.at.x, "Y": track.at.y, "Z": track.at.z}), "to": _vec1(at),
						"moved": roundf(moved), "allowed": roundf(allowed)}})
		_bodies[b] = {"at": here, "frames": track.frames + 1, "speed": speed}
	for b in _bodies.keys():
		if not alive.has(b):
			_bodies.erase(b)


## The nodes the detectors read as the game's: the scene's and the autoloads' (not this addon's).
func _game_roots() -> Array:
	var out := []
	for c in host.get_tree().root.get_children():
		if not world.is_ours(c):
			out.append(c)
	return out


## actor_growth: every 10 s of game time, how many nodes of each kind (kind_of) the game has, counting the nodes that
## are things of their own (with a script, an instanced scene's root, a physics body or area: not every mesh and shape
## under them); a kind that kept growing over growth_seconds, to at least twice as many and 50 more, is a possible leak.
func _count_kinds(emit: Callable, t: float) -> void:
	var counts := {}
	var stack: Array = _game_roots()
	while stack.size() > 0:
		var n: Node = stack.pop_back()
		stack.append_array(n.get_children())
		if n.get_script() != null or n.scene_file_path != "" or n is CollisionObject3D or n is CollisionObject2D:
			var k := kind_of(n)
			counts[k] = counts.get(k, 0) + 1
	_growth.append(counts)
	var needed := maxi(2, roundi(config.growth_seconds / 10.0) + 1)
	while _growth.size() > needed:
		_growth.remove_at(0)
	if _growth.size() < needed:
		return
	for k in _growth[-1]:
		var first: int = _growth[0].get(k, 0)
		var last: int = _growth[-1][k]
		if last < 2 * first or last - first < 50:
			continue
		var rising := true
		for i in range(1, _growth.size()):
			if _growth[i].get(k, 0) < _growth[i - 1].get(k, 0):
				rising = false
				break
		if rising:
			_finding(emit, t, {"detector": "actor_growth", "severity": "warning",
				"summary": "%s actors kept growing from %d to %d in %.0f s (a possible leak)" % [k, first, last, config.growth_seconds],
				"signature": "actor_growth:" + k, "details": {"from": first, "to": last, "seconds": config.growth_seconds}})


## What clamp and over_max read of a script: its own number variables with an allowed range (@export_range:
## [name, min, max], a side left open by or_greater / or_less as null), and those with a sibling maximum (max_<name>,
## <name>_max, Max<Name>, <Name>Max: [name, that sibling's name]).
func _checks_of(sc: Script) -> Array:
	if _props.has(sc):
		return _props[sc]
	var out := []
	var numbers := {}
	for prop in sc.get_script_property_list():
		if prop.usage & PROPERTY_USAGE_SCRIPT_VARIABLE and prop.type in [TYPE_INT, TYPE_FLOAT]:
			numbers[prop.name] = true
			if prop.hint == PROPERTY_HINT_RANGE:
				var parts: PackedStringArray = str(prop.hint_string).split(",")
				if parts.size() >= 2 and parts[0].strip_edges().is_valid_float() and parts[1].strip_edges().is_valid_float():
					var lo = null if "or_less" in parts else float(parts[0])
					var hi = null if "or_greater" in parts else float(parts[1])
					out.append([prop.name, lo, hi])
	for name in numbers:
		var n := str(name)
		if n.to_lower().begins_with("max") or n.to_lower().ends_with("max"):
			continue
		for other in ["max_" + n, n + "_max", "Max" + n.to_pascal_case(), n + "Max"]:
			if other != n and numbers.has(other):
				out.append([n, other])
				break
	_props[sc] = out
	return out


## clamp and over_max, on the nodes of the scene and the autoloads whose scripts are the game's (not this addon's).
func _properties(emit: Callable, t: float) -> void:
	var stack: Array = _game_roots()
	while stack.size() > 0:
		var n: Node = stack.pop_back()
		stack.append_array(n.get_children())
		var sc = n.get_script()
		if not (sc is Script) or str(sc.resource_path).begins_with("res://addons/autopilot/"):
			continue
		var label := kind_of(n)
		for check in _checks_of(sc):
			var value = n.get(check[0])
			if typeof(value) not in [TYPE_INT, TYPE_FLOAT]:
				continue
			var v := float(value)
			if check.size() == 3:
				var below: bool = check[1] != null and v < float(check[1]) - 1e-4
				var above: bool = check[2] != null and v > float(check[2]) + 1e-4
				if below or above:
					var lo := _fmt(check[1]) if check[1] != null else ""
					var hi := _fmt(check[2]) if check[2] != null else ""
					_finding(emit, t, {"detector": "clamp", "severity": "warning",
						"summary": "%s %s is %s, outside its allowed range %s..%s" % [label, check[0], _fmt(v), lo, hi],
						"signature": "clamp:%s.%s" % [label, check[0]], "actor": str(n.name), "where": _where(n),
						"details": {"property": check[0], "value": v, "clamp": "[%s, %s]" % [lo, hi]}})
			else:
				var mx = n.get(check[1])
				if typeof(mx) in [TYPE_INT, TYPE_FLOAT] and float(mx) > 0.0 and v > float(mx) + 1e-4:
					_finding(emit, t, {"detector": "over_max", "severity": "warning",
						"summary": "%s %s is %s, above %s %s" % [label, check[0], _fmt(v), check[1], _fmt(float(mx))],
						"signature": "over_max:%s.%s" % [label, check[0]], "actor": str(n.name), "where": _where(n),
						"details": {"property": check[0], "value": v, "max_property": check[1], "max": float(mx)}})
