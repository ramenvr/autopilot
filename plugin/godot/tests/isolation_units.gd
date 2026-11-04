## A run's input is Autopilot's alone (lib/isolation.gd), in the test game headless: the person's input is made the way
## the operating system's arrives (Input.parse_input_event with Godot's own devices, flushed as the display server
## flushes it, never marked as Autopilot's), Autopilot's goes through the addon's ops. Checks that during a run the
## person's keys, mouse and joypad reach neither the game's nodes nor Input's state (actions, keys, buttons, the mouse's
## place, joypad controls), that Autopilot's keys, actions, pointer, mouse look and move still do, and that between runs
## and after one everything is as it was. Run by units.py:
##     godot --headless --path <test game> --script res://isolation_units.gd
## prints PASS / FAIL per check and exits 0 when all pass.
extends SceneTree

const Isolation = preload("res://addons/autopilot/lib/isolation.gd")

var failed := 0
var passed := 0
var pt  # the autoload (/root/AutopilotPlaytest)
var gate  # its input gate (/root/AutopilotInputGate)
var probe: Probe
var bindings_before := {}  # action -> the game's own bindings (the events themselves) before any run


## A node of the game's: counts the events its _input and _unhandled_input see.
class Probe extends Node:
	var seen := []
	var unhandled := 0

	func _input(event: InputEvent) -> void:
		seen.append(event)

	func _unhandled_input(_event: InputEvent) -> void:
		unhandled += 1


func check(name: String, ok: bool, detail = "") -> void:
	if ok:
		passed += 1
		print("PASS  %s" % name)
	else:
		failed += 1
		print("FAIL  %s  [%s]" % [name, str(detail)])


func _initialize() -> void:
	await process_frame
	pt = root.get_node_or_null("AutopilotPlaytest")
	gate = root.get_node_or_null("AutopilotInputGate")
	check("the autoload and its gate are there", pt != null and gate != null, str(root.get_children()))
	if pt == null or gate == null:
		quit(1)
		return
	change_scene_to_file("res://scenes/level.tscn")
	for i in 4:
		await process_frame
	probe = Probe.new()
	probe.name = "Probe"
	current_scene.add_child(probe)
	pt._prepare_headless()  # (the root viewport the project's size, as a run has it)
	for action in ["move_forward", "jump"]:
		bindings_before[action] = InputMap.action_get_events(action)
	await process_frame
	await between_runs()
	await in_a_run()
	await after_the_run()
	await held_through_focus_loss()
	await mouse_mode()
	print("\n%d/%d checks passed" % [passed, passed + failed])
	quit(0 if failed == 0 else 1)


# --- the person's input, as the operating system delivers it ---------------------------------------------

func real(ev: InputEvent) -> void:
	Input.parse_input_event(ev)
	Input.flush_buffered_events()  # (as the display server does each frame)


func real_key(code: int, pressed: bool) -> void:
	var ev := InputEventKey.new()  # (device 16: Godot's keyboard)
	ev.keycode = code
	ev.physical_keycode = code
	ev.key_label = code
	ev.pressed = pressed
	real(ev)


func real_motion(at: Vector2, rel: Vector2) -> void:
	var ev := InputEventMouseMotion.new()  # (device 32: Godot's mouse)
	ev.position = at
	ev.global_position = at
	ev.relative = rel
	ev.screen_relative = rel
	real(ev)


func real_button(button: int, pressed: bool, at := Vector2(400, 300)) -> void:
	var ev := InputEventMouseButton.new()
	ev.button_index = button
	ev.pressed = pressed
	ev.position = at
	ev.global_position = at
	ev.button_mask = (1 << (button - 1)) if pressed else 0
	real(ev)


func real_joy_button(device: int, button: int, pressed: bool) -> void:
	var ev := InputEventJoypadButton.new()
	ev.device = device
	ev.button_index = button
	ev.pressed = pressed
	real(ev)


func real_joy_axis(device: int, axis: int, value: float) -> void:
	var ev := InputEventJoypadMotion.new()
	ev.device = device
	ev.axis = axis
	ev.axis_value = value
	real(ev)


# --- Autopilot's, through its ops -------------------------------------------------------------------------

func op(name: String, args: Dictionary):
	return pt.dispatch(1, name, args)


# --- the game's window loses the focus: Godot lets go of every key, Autopilot's too ---------------

func held_through_focus_loss() -> void:
	set_run(true)
	op("key", {"key": "W", "mode": "hold"})
	await frames(2)
	check("Autopilot holds W", Input.is_action_pressed("move_forward") and Input.is_key_pressed(KEY_W))
	gate._ours += 1  # (as Input.release_pressed_events does, which a script can't call: not the person's, so not countered)
	var up := InputEventKey.new()
	up.keycode = KEY_W
	up.physical_keycode = KEY_W
	up.pressed = false
	Input.parse_input_event(up)
	Input.flush_buffered_events()
	Input.action_release("move_forward")
	gate._ours -= 1
	check("the window lost the focus: W is up", not Input.is_action_pressed("move_forward"))
	gate._held_check_at = 0.0
	await create_timer(0.25).timeout
	check("Autopilot's W goes down again, still held", Input.is_action_pressed("move_forward") and Input.is_key_pressed(KEY_W))
	op("key", {"mode": "release_all"})
	await frames(2)
	check("and it lets go when Autopilot does", not Input.is_action_pressed("move_forward"))
	set_run(false)


# --- the game's reads and writes of the mouse mode ----------------------------------------------

## What the operating system has: not a read the addon could route (lib/mouse_reads.gd only changes the game's scripts,
## and this file is the game's too).
func os_mouse() -> int:
	return Input.get("mouse_mode")


func mouse_mode() -> void:
	var path := "res://scripts/mouse_user.gd"
	var user: Node = (load(path) as GDScript).new()
	current_scene.add_child(user)
	var on_disk := FileAccess.get_file_as_string(path)
	check("the game's script that touches the mouse mode is routed", pt.mouse_routed.routed >= 1 and pt.mouse_routed.failed.is_empty(),
		str(pt.mouse_routed))
	check("... in memory: its file doesn't change, and in memory it does", on_disk.contains("Input.mouse_mode")
		and not on_disk.contains("AutopilotPlaytest") and user.get_script().source_code.contains("AutopilotPlaytest.game_mouse_mode"))
	check("... the lines stay where they are", user.get_script().source_code.count("\n") == on_disk.count("\n"))
	check("the addon's own scripts aren't routed", not (load("res://addons/autopilot/lib/isolation.gd") as GDScript).source_code.contains("AutopilotPlaytest.game_mouse"))
	set_run(true)
	for setter in ["set_property", "set_call", "set_display"]:
		user.set_property(Input.MOUSE_MODE_VISIBLE)
		user.call(setter, Input.MOUSE_MODE_CAPTURED)
		check("in a run, %s: the game reads captured all three ways" % setter, user.read_property() == Input.MOUSE_MODE_CAPTURED
			and user.read_getter() == Input.MOUSE_MODE_CAPTURED and user.read_display() == Input.MOUSE_MODE_CAPTURED, str(user.read_property()))
		check("... and the operating system's mouse stays free", os_mouse() == Input.MOUSE_MODE_VISIBLE, str(os_mouse()))
		check("... compared with either enum, or kept in a typed variable", user.display_captured() and user.input_captured()
			and user.held_in_typed_var() == Input.MOUSE_MODE_CAPTURED)
	user.set_property(Input.MOUSE_MODE_VISIBLE)
	check("in a run the game can show the mouse: it reads what it set last", user.read_property() == Input.MOUSE_MODE_VISIBLE)
	user.set_property(Input.MOUSE_MODE_CONFINED)
	check("in a run a confined mouse waits too", user.read_property() == Input.MOUSE_MODE_CONFINED and os_mouse() == Input.MOUSE_MODE_VISIBLE)
	user.set_property(Input.MOUSE_MODE_HIDDEN)
	set_run(false)
	gate.set_game_mouse(Input.MOUSE_MODE_VISIBLE)
	user.set_property(Input.MOUSE_MODE_CAPTURED)
	check("headless, with no run: the game reads what it asked for", user.read_property() == Input.MOUSE_MODE_CAPTURED)
	gate.set_game_mouse(Input.MOUSE_MODE_VISIBLE)
	# a game the editor started in a run: it holds the mouse off before the editor can attach and say so
	gate._starting_in_run = true
	gate.set_game_mouse(Input.MOUSE_MODE_CAPTURED)
	check("started in a run, before the attach: the game reads captured", user.read_property() == Input.MOUSE_MODE_CAPTURED)
	check("... and the operating system's mouse stays free", os_mouse() == Input.MOUSE_MODE_VISIBLE, str(os_mouse()))
	gate.attached()
	check("the attach ends the wait", not gate._starting_in_run)
	gate.set_game_mouse(Input.MOUSE_MODE_VISIBLE)
	user.queue_free()


func set_run(on: bool) -> void:
	# (a brain there, a run going or not: as a run asked on the command line has it, with no link to answer on)
	pt.active = true
	pt.cli_run = {"kind": "verify"}
	pt._welcomed = true
	pt.run_active = on
	pt._isolate()


func player() -> Node3D:
	return current_scene.get_node("Player")


func frames(n: int) -> void:
	for i in n:
		await physics_frame
	await process_frame


func seen_since(start: int) -> int:
	return probe.seen.size() - start


# --- between runs: the person's input reaches the game -----------------------------------------------------

func between_runs() -> void:
	set_run(false)
	check("between runs: no isolation", not gate.on)
	var n := probe.seen.size()
	real_key(KEY_W, true)
	check("between runs: the person's W reaches the game's _input and the move_forward action",
		seen_since(n) == 1 and Input.is_action_pressed("move_forward") and Input.is_key_pressed(KEY_W),
		"%d %s" % [seen_since(n), Input.is_action_pressed("move_forward")])
	var p0 := player().global_position
	await frames(6)
	check("between runs: the person's W moves the player", player().global_position.distance_to(p0) > 0.05,
		str(player().global_position - p0))
	real_key(KEY_W, false)
	await frames(2)


# --- in a run ------------------------------------------------------------------------------------------

func in_a_run() -> void:
	# held by the person as the run starts: let go
	real_key(KEY_D, true)
	real_button(MOUSE_BUTTON_RIGHT, true)
	check("before the run: the person holds D and the right button", Input.is_action_pressed("move_right")
		and Input.is_mouse_button_pressed(MOUSE_BUTTON_RIGHT))
	set_run(true)
	check("in a run: isolation on, the joypads in the background left out",
		gate.on and Input.is_ignoring_joypad_on_unfocused_application())
	check("the run's start lets go of what the person held", not Input.is_action_pressed("move_right")
		and not Input.is_key_pressed(KEY_D) and not Input.is_mouse_button_pressed(MOUSE_BUTTON_RIGHT),
		"%s %s %s" % [Input.is_action_pressed("move_right"), Input.is_key_pressed(KEY_D), Input.get_mouse_button_mask()])
	real_key(KEY_D, false)
	real_button(MOUSE_BUTTON_RIGHT, false)

	# the gate is the root's last child: it gets input first
	var late := Node.new()
	late.name = "AddedByTheGame"
	root.add_child(late)
	gate.tick(Time.get_ticks_msec() / 1000.0)
	check("the gate stays the root's last child (a node the game adds to the root goes before it)",
		gate.get_index() == root.get_child_count() - 1, str(gate.get_index()))
	late.queue_free()

	var events := InputMap.action_get_events("move_forward")
	check("in a run: the game's key bindings are bound to Autopilot's keyboard",
		events.size() == 2 and events.all(func(e): return e.device == Isolation.KEYBOARD), str(events.map(func(e): return e.device)))

	# the person's key: nothing of it reaches the game
	var n := probe.seen.size()
	var u := probe.unhandled
	var blocked: int = gate.blocked
	var p0 := player().global_position
	real_key(KEY_W, true)
	check("in a run: the person's W reaches no node (_input, _unhandled_input)", seen_since(n) == 0 and probe.unhandled == u,
		str(probe.seen.slice(n)))
	check("in a run: the person's W is in no action (pressed, just pressed, strength)", not Input.is_action_pressed("move_forward")
		and not Input.is_action_just_pressed("move_forward") and Input.get_action_strength("move_forward") == 0.0
		and Input.get_vector("move_left", "move_right", "move_forward", "move_back") == Vector2.ZERO)
	check("in a run: the person's W is in none of Input's keys", not Input.is_key_pressed(KEY_W)
		and not Input.is_physical_key_pressed(KEY_W) and not Input.is_key_label_pressed(KEY_W) and not Input.is_anything_pressed())
	check("in a run: the gate counted the person's event", gate.blocked == blocked + 1, str(gate.blocked - blocked))
	await frames(6)
	check("in a run: the person's W doesn't move the player", player().global_position.distance_to(p0) < 0.01,
		str(player().global_position - p0))
	real_key(KEY_W, false)
	real_key(KEY_SPACE, true)
	await frames(1)
	check("in a run: the person's Space doesn't make the player jump", not Input.is_action_just_pressed("jump")
		and absf(player().velocity.y) < 0.01, str(player().velocity))
	real_key(KEY_SPACE, false)
	var just_released := Input.is_action_just_released("jump")
	check("in a run: the person's release is in no action either (no just released)", not just_released)

	# Autopilot's key: reaches the game as before, and the person's same key doesn't let it go
	n = probe.seen.size()
	op("key", {"key": "W", "mode": "hold"})
	check("Autopilot's held W reaches the game's _input (on Autopilot's keyboard)", seen_since(n) == 1
		and probe.seen[-1] is InputEventKey and probe.seen[-1].device == Isolation.KEYBOARD, str(probe.seen.slice(n)))
	check("Autopilot's held W: the action and the key are down, just pressed", Input.is_action_pressed("move_forward")
		and Input.is_action_just_pressed("move_forward") and Input.is_key_pressed(KEY_W))
	p0 = player().global_position
	real_key(KEY_W, false)
	check("the person's W release doesn't let Autopilot's W go", Input.is_action_pressed("move_forward") and Input.is_key_pressed(KEY_W))
	real_key(KEY_W, true)
	real_key(KEY_W, false)
	check("the person's W press and release don't either", Input.is_action_pressed("move_forward") and Input.is_key_pressed(KEY_W))
	await frames(6)
	check("Autopilot's held W moves the player", player().global_position.distance_to(p0) > 0.05, str(player().global_position - p0))
	op("key", {"key": "W", "mode": "release"})
	check("Autopilot's W let go", not Input.is_action_pressed("move_forward") and not Input.is_key_pressed(KEY_W))
	await frames(2)

	# the mouse: the person's motion turns nothing and doesn't move Input's mouse; Autopilot's pointer and look do
	op("pointer", {"screen_x": -0.5, "screen_y": 0.5})
	var ours: Vector2 = root.get_mouse_position()
	var yaw := player().rotation.y
	n = probe.seen.size()
	real_motion(Vector2(900, 500), Vector2(80, 10))
	check("in a run: the person's mouse motion reaches no node and doesn't turn the view", seen_since(n) == 0
		and is_equal_approx(player().rotation.y, yaw), "%d %f" % [seen_since(n), player().rotation.y - yaw])
	check("in a run: Input's mouse stays where Autopilot's pointer is", root.get_mouse_position().distance_to(ours) < 0.5,
		"%s %s" % [root.get_mouse_position(), ours])
	real_button(MOUSE_BUTTON_LEFT, true)
	check("in a run: the person's button is in no Input state", not Input.is_mouse_button_pressed(MOUSE_BUTTON_LEFT)
		and Input.get_mouse_button_mask() == 0 and root.get_mouse_position().distance_to(ours) < 0.5)
	real_button(MOUSE_BUTTON_LEFT, false)
	op("key", {"key": "MouseX", "mode": "press", "value": 100})
	check("Autopilot's mouse look turns the view", absf(player().rotation.y - yaw) > 0.1, str(player().rotation.y - yaw))
	op("key", {"key": "LeftMouseButton", "mode": "hold"})
	real_button(MOUSE_BUTTON_LEFT, false)
	check("Autopilot's held button stays down through the person's release", Input.is_mouse_button_pressed(MOUSE_BUTTON_LEFT))
	op("key", {"key": "LeftMouseButton", "mode": "release"})
	check("Autopilot's button let go", not Input.is_mouse_button_pressed(MOUSE_BUTTON_LEFT))

	# joypads: the person's don't count; Autopilot's (joypad 0) does
	real_joy_button(0, JOY_BUTTON_A, true)
	check("in a run: the person's joypad button reaches no state", not Input.is_joy_button_pressed(0, JOY_BUTTON_A))
	real_joy_button(0, JOY_BUTTON_A, false)
	real_joy_axis(1, JOY_AXIS_LEFT_X, 0.8)
	check("in a run: the person's second joypad's stick reaches no state", Input.get_joy_axis(1, JOY_AXIS_LEFT_X) == 0.0)
	op("key", {"key": "Gamepad_FaceButton_Bottom", "mode": "hold"})
	real_joy_button(0, JOY_BUTTON_A, false)
	check("Autopilot's held joypad button stays down through the person's release", Input.is_joy_button_pressed(0, JOY_BUTTON_A))
	op("key", {"key": "Gamepad_LeftX", "mode": "hold", "value": 0.5})
	await frames(1)  # (a held axis goes out every frame)
	real_joy_axis(0, JOY_AXIS_LEFT_X, -1.0)
	check("Autopilot's held stick keeps its value through the person's", is_equal_approx(Input.get_joy_axis(0, JOY_AXIS_LEFT_X), 0.5),
		str(Input.get_joy_axis(0, JOY_AXIS_LEFT_X)))
	op("key", {"mode": "release_all"})

	# actions: `input` (InputEventAction) and `move` (Input.action_press)
	op("input", {"action": "sprint", "mode": "hold"})
	real_key(KEY_SHIFT, true)
	real_key(KEY_SHIFT, false)
	check("Autopilot's held action stays down through the person's key of it", Input.is_action_pressed("sprint"))
	op("input", {"action": "sprint", "mode": "release"})
	p0 = player().global_position
	op("move", {"direction": {"X": 1, "Y": 0, "Z": 0}, "seconds": 0.4})
	await frames(4)
	real_key(KEY_D, true)
	real_key(KEY_D, false)
	real_key(KEY_W, true)
	real_key(KEY_W, false)
	var pressed_during := false
	for a in ["move_forward", "move_back", "move_left", "move_right"]:
		pressed_during = pressed_during or Input.is_action_pressed(a)
	check("move's actions stay pressed through the person's keys of them", pressed_during)
	await create_timer(0.5).timeout
	await frames(2)
	check("move walks the player in a run", player().global_position.distance_to(p0) > 0.5, str(player().global_position - p0))

	# a binding the game adds in the run goes to Autopilot's keyboard too
	var added := InputEventKey.new()
	added.physical_keycode = KEY_J
	added.device = -1
	InputMap.action_add_event("jump", added)
	gate._rebind_at = 0.0
	gate.tick(Time.get_ticks_msec() / 1000.0)
	var jump := InputMap.action_get_events("jump")
	check("a binding the game adds in the run is bound to Autopilot's keyboard", jump.size() == 2
		and jump.all(func(e): return e.device == Isolation.KEYBOARD), str(jump.map(func(e): return e.device)))
	real_key(KEY_J, true)
	check("the person's key of that binding is in no action", not Input.is_action_pressed("jump"))
	real_key(KEY_J, false)

	# the UI ops' events (pushed to the viewport straight) still reach it
	var button := Button.new()
	button.text = "Test"
	button.position = Vector2(20, 20)
	button.size = Vector2(120, 40)
	var layer := CanvasLayer.new()
	layer.add_child(button)
	current_scene.add_child(layer)
	await process_frame
	var clicks := {"n": 0}
	button.pressed.connect(func(): clicks.n += 1)
	pt.ui._push_mouse(button, MOUSE_BUTTON_LEFT)
	check("in a run: ui_act's click (pushed to the viewport) presses a button", clicks.n == 1, str(clicks.n))
	real_button(MOUSE_BUTTON_LEFT, true, Vector2(80, 40))
	real_button(MOUSE_BUTTON_LEFT, false, Vector2(80, 40))
	check("in a run: the person's click on that button doesn't press it", clicks.n == 1, str(clicks.n))
	layer.queue_free()

	# the window losing focus lets go of held keys (Godot's own): Autopilot's go down again
	op("key", {"key": "W", "mode": "hold"})
	pt._reassert_held()
	check("Autopilot's holds can be pressed again (after the window lost focus)", Input.is_action_pressed("move_forward"))
	op("key", {"mode": "release_all"})
	await frames(2)


# --- after the run: as it was -------------------------------------------------------------------------------

func after_the_run() -> void:
	op("key", {"key": "W", "mode": "hold"})
	set_run(false)
	check("the run's end: isolation off, Autopilot's holds let go, the joypad setting back", not gate.on
		and not Input.is_action_pressed("move_forward") and not Input.is_key_pressed(KEY_W)
		and not Input.is_ignoring_joypad_on_unfocused_application())
	var events := InputMap.action_get_events("move_forward")
	check("the run's end: the game's own bindings back, the same events in the same order", events == bindings_before["move_forward"]
		and events[0].device == InputEvent.DEVICE_ID_KEYBOARD, str(events.map(func(e): return e.device)))
	var jump := InputMap.action_get_events("jump")
	check("the run's end: the binding the game added in the run is its own again (any device)",
		jump.size() == 2 and jump[0] == bindings_before["jump"][0] and jump[1].device == -1, str(jump.map(func(e): return e.device)))
	var n := probe.seen.size()
	real_key(KEY_W, true)
	check("after the run: the person's W reaches the game again", seen_since(n) == 1 and Input.is_action_pressed("move_forward")
		and Input.is_key_pressed(KEY_W))
	real_key(KEY_W, false)
	op("key", {"key": "W", "mode": "hold"})
	check("after the run: Autopilot's keys go out on Godot's keyboard", probe.seen[-1].device == InputEvent.DEVICE_ID_KEYBOARD
		and Input.is_action_pressed("move_forward"))
	op("key", {"mode": "release_all"})
	check("after the run: let go", not Input.is_action_pressed("move_forward"))
	set_run(true)
	pt.active = false  # (the brain went: detached)
	pt._isolate()
	check("a brain gone ends the isolation (connected, run and play all needed)", not gate.on)
