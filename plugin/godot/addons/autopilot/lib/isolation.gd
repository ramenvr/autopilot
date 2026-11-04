## A run's input is Autopilot's alone (as Unity's Hands.IsolateDevices): while a run goes (`on`: the brain is attached
## and a run is active; the game process is the play session), what the person at the computer does with their own
## keyboard, mouse and gamepads doesn't reach the game, and Autopilot's input reaches it as before. Off, everything is
## as it was.
##
## How Godot delivers the operating system's input: the display server hands each event to Input.parse_input_event,
## and once Input has updated its own state from it (the keys, mouse buttons and joypad controls down, the mouse's
## place, the InputMap actions' states with their just-pressed frames) it gives the event to the window, whose viewport
## calls the nodes' _input (the last in the tree first), then the GUI, then _unhandled_input. A node swallowing an event
## comes too late for Input's state, so isolation is in three parts:
##  - the actions: during a run each InputMap binding of a key or a mouse button is bound to Autopilot's own keyboard
##    and mouse (KEYBOARD, MOUSE: device ids no real device has) instead of Godot's keyboard and mouse (16 and 32, what
##    project.godot's bindings get) or every device (-1), and Autopilot's key and mouse events carry those ids. The
##    person's keys and buttons then match no action at all: Input.is_action_pressed,
##    is_action_just_pressed, get_vector and the rest never see them. The bindings are put back when the run ends;
##  - this node, kept as the root's last child, gets every event of the game's window first (its _input) and swallows
##    the ones that aren't Autopilot's, so no other node, the GUI included, sees them;
##  - Input's own state, which such an event changed already, is put back at once with a counter event (a key's
##    release, the mouse back where Autopilot's pointer is, a joypad control back to Autopilot's value): Input.is_key_
##    pressed, get_mouse_button_mask, get_mouse_position and get_joy_axis read what Autopilot pressed and nothing else.
##    The counter events are the engine's internal device (-2) and are swallowed here too.
## Joypads: Autopilot's gamepad is joypad 0, as the person's first one is, so their actions can't be told apart by
## device; while the game's window doesn't have focus the person's joypads are left out by Godot itself (Input.ignore_
## joypad_on_unfocused_application, on during a run), and while it has focus their events are swallowed and countered
## here as the rest (an action bound to a joypad control can still see a press and release in the same frame then).
## The game's view doesn't take the mouse: a captured or confined mouse locks the desktop's cursor in the game's window,
## whichever window is in front. During a run such a mode is off (the mouse visible) while the game goes on reading the
## mode it asked for (lib/mouse_reads.gd). After the run the game takes the mouse again at the next click in its window,
## not by itself: the person may be working elsewhere.
extends Node

const KEYBOARD := 1016  # Autopilot's keyboard and mouse during a run (Godot's own are 16 and 32; 0-15 are joypads)
const MOUSE := 1032
const INTERNAL := -2  # InputEvent.DEVICE_ID_INTERNAL: counter events (the window_input signal leaves these out)
const ALL_DEVICES := -1

var host  # playtest.gd
var on := false
var blocked := 0  # the person's events swallowed since the run began (tests and the log)

var _ours := 0  # >0 while Autopilot's own event is being delivered
var _countering := 0  # >0 while counter events are being delivered
var _counter_ids := {}  # instance ids of the joypad counter events (their device is a real joypad's)
var _swapped := {}  # action -> {instance id of our binding: the game's binding it stands for}
var _joy_unfocused_before := false
var _rebind_at := 0.0
var _held_check_at := 0.0
var _game_mouse := -1  # the mouse mode the game asked for, while the run has it off (-1: nothing held off)
var _waiting_click := false  # the run is over: the game takes the mouse at the next click in its window
# Started by the editor during a run: until the editor attaches, the game can't be told of the run, and a mouse it
# captured in its first frames would lock the cursor and move it to the game's window.
var _starting_in_run := OS.get_environment("AUTOPILOT_RUN_GOING") == "1"

# What Autopilot has down (its events, never the person's): what a counter event puts back
var _keys := {}  # keycode -> true
var _physical := {}
var _labels := {}
var _mask := 0  # mouse buttons
var _joy_buttons := {}  # joypad 0's buttons -> true
var _joy_axes := {}  # joypad 0's axes -> value
var _pressed_on := {}  # "key:<code>" / "mouse:<button>" -> the device the press went out on (its release goes there too)


func _init(playtest = null) -> void:
	host = playtest
	name = "AutopilotInputGate"
	process_mode = Node.PROCESS_MODE_ALWAYS


## On: the game gets Autopilot's input alone. Off: put back what that changed. Autopilot's holds are let go at both
## turns (the brain starts a run with nothing held; a run that ended holds nothing), so each press and its release
## go out on the same device.
func set_on(want: bool) -> void:
	if want == on:
		return
	if host:
		host._release_all()
	if want:
		on = true
		blocked = 0
		_joy_unfocused_before = Input.is_ignoring_joypad_on_unfocused_application()
		Input.set_ignore_joypad_on_unfocused_application(true)
		_rebind()
		_settle()
		keep_last()
		_waiting_click = false
		_hold_mouse()
	else:
		_waiting_click = _game_mouse >= 0 and not _no_window()
		if not _waiting_click and not _no_window():
			_game_mouse = -1
		_unbind()
		Input.set_ignore_joypad_on_unfocused_application(_joy_unfocused_before)
		on = false


## Every frame: stay the root's last child (the first to get input: a node the game added to the root since would get
## it before this), and bind to Autopilot's devices any key or mouse binding the game added since (twice a second).
func tick(now: float) -> void:
	hold_while_starting()
	if not on:
		return
	keep_last()
	_hold_mouse()
	# The game's window lost the focus (the person went to another window) and Godot let go of every key, Autopilot's
	# too: what it holds goes down again, whatever let it go. (Not every frame: a tap's key is down for two.)
	if host and now >= _held_check_at and _lost_held():
		_held_check_at = now + 0.1
		host._reassert_held()
	if now >= _rebind_at:
		_rebind_at = now + 0.5
		_rebind()


func keep_last() -> void:
	var root := get_tree().root if is_inside_tree() else null
	if root and get_parent() == root and get_index() != root.get_child_count() - 1:
		root.move_child(self, -1)


## Whether something Autopilot pressed isn't down in Input any more.
func _lost_held() -> bool:
	for code in _keys:
		if not Input.is_key_pressed(code):
			return true
	for code in _physical:
		if not Input.is_physical_key_pressed(code):
			return true
	if (Input.get_mouse_button_mask() & _mask) != _mask:
		return true
	for button in _joy_buttons:
		if not Input.is_joy_button_pressed(0, button):
			return true
	for action in host._pressed_actions:
		if InputMap.has_action(action) and not Input.is_action_pressed(action):
			return true
	return false


## A mouse mode that would lock the desktop's cursor goes off, again whenever the game sets one.
func _hold_mouse() -> void:
	if _traps(Input.mouse_mode):
		_game_mouse = Input.mouse_mode
		Input.mouse_mode = Input.MOUSE_MODE_VISIBLE


static func _traps(mode: int) -> bool:
	return mode == Input.MOUSE_MODE_CAPTURED or mode == Input.MOUSE_MODE_CONFINED or mode == Input.MOUSE_MODE_CONFINED_HIDDEN


static func _no_window() -> bool:
	return DisplayServer.get_name() == "headless"


## The mouse mode as the game knows it: the one it asked for, whether or not it holds the mouse now.
func game_mouse() -> int:
	return _game_mouse if _game_mouse >= 0 else Input.mouse_mode


## The game sets the mouse mode (lib/mouse_reads.gd). One that would lock the cursor waits while the run goes and until
## the click after it; with no window (headless) the game just reads what it asked for, as the engine would ignore it.
func set_game_mouse(mode: int) -> void:
	if _traps(mode) and (on or _waiting_click or _starting_in_run or _no_window()):
		_game_mouse = mode
		return
	_game_mouse = -1
	_waiting_click = false
	Input.mouse_mode = mode as Input.MouseMode


## A game started in a run, before the editor attached: a mouse a script captured before lib/mouse_reads.gd routed it
## (an autoload listed before the addon's) goes off now, and every frame until then.
func hold_while_starting() -> void:
	if _starting_in_run:
		_hold_mouse()


## The editor attached: what it says of the run holds from now on (no run going after all: the game has the mouse it
## asked for).
func attached() -> void:
	if _starting_in_run and not on:
		_starting_in_run = false
		if not _no_window():
			_give_back_mouse()
	_starting_in_run = false


func _give_back_mouse() -> void:
	_waiting_click = false
	if _game_mouse >= 0:
		Input.mouse_mode = _game_mouse as Input.MouseMode
	_game_mouse = -1


# --- Autopilot's own events -------------------------------------------------------------------------

## Deliver Autopilot's event to the game, as the operating system's are (Input.parse_input_event, then the window):
## at once, not merged into an event Input holds (accumulated input), and let through here.
func deliver(ev: InputEvent) -> void:
	if ev is InputEventKey or ev is InputEventMouse:
		ev.device = _device_for(ev)
	_note(ev)
	Input.flush_buffered_events()  # (anything already waiting is the person's, and goes through the gate as such)
	var accumulate := Input.is_using_accumulated_input()
	Input.set_use_accumulated_input(false)
	_ours += 1
	Input.parse_input_event(ev)
	Input.flush_buffered_events()  # (agile event flushing buffers even without accumulation; headless, nothing else flushes)
	_ours -= 1
	Input.set_use_accumulated_input(accumulate)


## Autopilot's event given to a viewport straight (the UI ops: Viewport.push_input), let through here.
func push(vp: Viewport, ev: InputEvent) -> void:
	if ev is InputEventMouse:
		ev.device = MOUSE if on else InputEvent.DEVICE_ID_MOUSE
	_ours += 1
	vp.push_input(ev, true)
	_ours -= 1


## The device an event of Autopilot's goes out on: its own keyboard or mouse during a run, Godot's own otherwise; a
## release goes where its press went.
func _device_for(ev: InputEvent) -> int:
	var id := ""
	if ev is InputEventKey:
		id = "key:%d" % ev.keycode
	elif ev is InputEventMouseButton:
		id = "mouse:%d" % ev.button_index
	var natural := InputEvent.DEVICE_ID_KEYBOARD if ev is InputEventKey else InputEvent.DEVICE_ID_MOUSE
	var now := (KEYBOARD if ev is InputEventKey else MOUSE) if on else natural
	if id == "":
		return now
	if ev.pressed:
		_pressed_on[id] = now
		return now
	return int(_pressed_on.get(id, now))


func _note(ev: InputEvent) -> void:
	if ev is InputEventKey and not ev.echo:
		for pair in [[_keys, ev.keycode], [_physical, ev.physical_keycode], [_labels, ev.key_label]]:
			if pair[1] != KEY_NONE:
				if ev.pressed:
					pair[0][pair[1]] = true
				else:
					pair[0].erase(pair[1])
		if not ev.pressed:
			_pressed_on.erase("key:%d" % ev.keycode)
	elif ev is InputEventMouseButton:
		var bit := _bit(ev.button_index)
		_mask = (_mask | bit) if ev.pressed else (_mask & ~bit)
		if not ev.pressed:
			_pressed_on.erase("mouse:%d" % ev.button_index)
	elif ev is InputEventJoypadButton and ev.device == 0:
		if ev.pressed:
			_joy_buttons[ev.button_index] = true
		else:
			_joy_buttons.erase(ev.button_index)
	elif ev is InputEventJoypadMotion and ev.device == 0:
		_joy_axes[ev.axis] = ev.axis_value


static func _bit(button: int) -> int:
	return (1 << (button - 1)) if button >= MOUSE_BUTTON_LEFT and button <= MOUSE_BUTTON_XBUTTON2 else 0


# --- the gate ---------------------------------------------------------------------------------------

func _input(event: InputEvent) -> void:
	if _ours > 0:
		return
	if not on:
		if _waiting_click and event is InputEventMouseButton and event.pressed:
			_give_back_mouse()  # (the person clicked in the game's window: as it would, it takes the mouse)
		return
	if _countering > 0 and (event.device == INTERNAL or _counter_ids.has(event.get_instance_id())):
		get_viewport().set_input_as_handled()
		return
	if event.device == INTERNAL:
		return  # (the engine's own, not the person's: a cursor shape refresh)
	blocked += 1
	_counter(event)
	get_viewport().set_input_as_handled()  # (after the counter events: delivering them reset it)


## Put back what the person's event changed in Input's state: Autopilot's own state (what it has down, where its
## pointer is) as it was before the event.
func _counter(event: InputEvent) -> void:
	var out := []
	if event is InputEventKey:
		if event.echo:
			return
		var ev := InputEventKey.new()
		ev.pressed = not event.pressed
		var any := false
		if event.keycode != KEY_NONE and _keys.has(event.keycode) != event.pressed:
			ev.keycode = event.keycode
			any = true
		if event.physical_keycode != KEY_NONE and _physical.has(event.physical_keycode) != event.pressed:
			ev.physical_keycode = event.physical_keycode
			any = true
		if event.key_label != KEY_NONE and _labels.has(event.key_label) != event.pressed:
			ev.key_label = event.key_label
			any = true
		if any:
			out.append(ev)
	elif event is InputEventMouse:
		var at := _pointer()
		if event is InputEventMouseButton:
			var held := (_mask & _bit(event.button_index)) != 0
			if held != event.pressed:
				var b := InputEventMouseButton.new()
				b.button_index = event.button_index
				b.pressed = held
				b.position = at
				b.global_position = at
				b.button_mask = _mask
				out.append(b)
		var m := InputEventMouseMotion.new()
		m.position = at
		m.global_position = at
		m.button_mask = _mask
		if event is InputEventMouseMotion:
			m.relative = -event.relative  # (Input's mouse velocity: the person's motion taken back)
			m.screen_relative = -event.screen_relative
		out.append(m)
	elif event is InputEventJoypadButton:
		var want: bool = event.device == 0 and _joy_buttons.has(event.button_index)
		if want != event.pressed:
			var j := InputEventJoypadButton.new()
			j.device = event.device
			j.button_index = event.button_index
			j.pressed = want
			j.pressure = 1.0 if want else 0.0
			out.append(j)
	elif event is InputEventJoypadMotion:
		var value: float = float(_joy_axes.get(event.axis, 0.0)) if event.device == 0 else 0.0
		if not is_equal_approx(value, event.axis_value):
			var j := InputEventJoypadMotion.new()
			j.device = event.device
			j.axis = event.axis
			j.axis_value = value
			out.append(j)
	for ev in out:
		if not (ev is InputEventJoypadButton or ev is InputEventJoypadMotion):
			ev.device = INTERNAL
	_send_counters(out)
	if host and (event is InputEventJoypadButton or event is InputEventJoypadMotion):
		host._reassert_actions()  # (a joypad release matched by an action lets go of Input.action_press too)


func _send_counters(events: Array) -> void:
	if events.is_empty():
		return
	var accumulate := Input.is_using_accumulated_input()
	Input.set_use_accumulated_input(false)
	_countering += 1
	for ev in events:
		_counter_ids[ev.get_instance_id()] = true
		Input.parse_input_event(ev)
	Input.flush_buffered_events()
	_countering -= 1
	if _countering == 0:
		_counter_ids.clear()
	Input.set_use_accumulated_input(accumulate)


## Where Autopilot's mouse is, in the window's pixels (as its own mouse events carry it).
func _pointer() -> Vector2:
	return host._to_window(host._mouse_at()) if host else Vector2.ZERO


# --- the run's start: what the person had down lets go ------------------------------------------------

## Make Input's state Autopilot's alone: keys, mouse buttons and joypad controls the person holds now let go (with
## counter events, swallowed here), the actions they held too, and the mouse goes where Autopilot's pointer is.
func _settle() -> void:
	var out := []
	for code in _key_codes():
		var ev := InputEventKey.new()
		ev.pressed = false
		var any := false
		if Input.is_key_pressed(code) and not _keys.has(code):
			ev.keycode = code
			any = true
		if Input.is_physical_key_pressed(code) and not _physical.has(code):
			ev.physical_keycode = code
			any = true
		if Input.is_key_label_pressed(code) and not _labels.has(code):
			ev.key_label = code
			any = true
		if any:
			ev.device = INTERNAL
			out.append(ev)
	var at := _pointer()
	for button in range(MOUSE_BUTTON_LEFT, MOUSE_BUTTON_XBUTTON2 + 1):
		if Input.is_mouse_button_pressed(button) and (_mask & _bit(button)) == 0:
			var b := InputEventMouseButton.new()
			b.button_index = button
			b.position = at
			b.global_position = at
			b.device = INTERNAL
			out.append(b)
	var m := InputEventMouseMotion.new()
	m.position = at
	m.global_position = at
	m.button_mask = _mask
	m.device = INTERNAL
	out.append(m)
	for device in Input.get_connected_joypads():
		for button in JOY_BUTTON_SDL_MAX:
			if Input.is_joy_button_pressed(device, button) and not (device == 0 and _joy_buttons.has(button)):
				var j := InputEventJoypadButton.new()
				j.device = device
				j.button_index = button
				out.append(j)
		for axis in JOY_AXIS_SDL_MAX:
			var want: float = float(_joy_axes.get(axis, 0.0)) if device == 0 else 0.0
			if not is_equal_approx(Input.get_joy_axis(device, axis), want):
				var j := InputEventJoypadMotion.new()
				j.device = device
				j.axis = axis
				j.axis_value = want
				out.append(j)
	_send_counters(out)
	for action in InputMap.get_actions():
		if Input.is_action_pressed(action):
			Input.action_release(action)


static func _key_codes() -> Array:
	var out := []
	for c in range(KEY_A, KEY_Z + 1):
		out.append(c)
	for c in range(KEY_0, KEY_9 + 1):
		out.append(c)
	for c in range(KEY_F1, KEY_F12 + 1):
		out.append(c)
	for c in range(KEY_KP_MULTIPLY, KEY_KP_9 + 1):
		out.append(c)
	for c in [KEY_SPACE, KEY_ENTER, KEY_ESCAPE, KEY_TAB, KEY_BACKSPACE, KEY_SHIFT, KEY_CTRL, KEY_ALT, KEY_META, KEY_UP,
			KEY_DOWN, KEY_LEFT, KEY_RIGHT, KEY_DELETE, KEY_INSERT, KEY_HOME, KEY_END, KEY_PAGEUP, KEY_PAGEDOWN,
			KEY_CAPSLOCK, KEY_PAUSE, KEY_SEMICOLON, KEY_EQUAL, KEY_COMMA, KEY_MINUS, KEY_PERIOD, KEY_SLASH,
			KEY_QUOTELEFT, KEY_BRACKETLEFT, KEY_BACKSLASH, KEY_BRACKETRIGHT, KEY_APOSTROPHE, KEY_UNDERSCORE]:
		out.append(c)
	return out


# --- the InputMap: the game's key and mouse bindings match Autopilot's devices alone ----------------------

## Bind every key and mouse binding that any keyboard or mouse matches (device -1, or Godot's own 16 and 32) to
## Autopilot's keyboard and mouse instead, in place (the action's other bindings and their order stay).
func _rebind() -> void:
	for action in InputMap.get_actions():
		var events := InputMap.action_get_events(action)
		var touch := false
		for ev in events:
			if _bound_to_person(ev):
				touch = true
				break
		if not touch:
			continue
		var swapped: Dictionary = _swapped.get(action, {})
		var now := []
		for ev in events:
			if _bound_to_person(ev):
				var mine: InputEvent = ev.duplicate()
				mine.device = KEYBOARD if ev is InputEventKey else MOUSE
				swapped[mine.get_instance_id()] = ev
				now.append(mine)
			else:
				now.append(ev)
		InputMap.action_erase_events(action)
		for ev in now:
			InputMap.action_add_event(action, ev)
		_swapped[action] = swapped


static func _bound_to_person(ev: InputEvent) -> bool:
	if ev is InputEventKey:
		return ev.device == ALL_DEVICES or ev.device == InputEvent.DEVICE_ID_KEYBOARD
	if ev is InputEventMouseButton:
		return ev.device == ALL_DEVICES or ev.device == InputEvent.DEVICE_ID_MOUSE
	return false


## Put the game's own bindings back where ours stand (bindings the game added or removed during the run stay so).
func _unbind() -> void:
	for action in _swapped:
		if not InputMap.has_action(action):
			continue
		var swapped: Dictionary = _swapped[action]
		var events := InputMap.action_get_events(action)
		var back := []
		var changed := false
		for ev in events:
			var original = swapped.get(ev.get_instance_id())
			if original != null:
				back.append(original)
				changed = true
			else:
				back.append(ev)
		if not changed:
			continue
		InputMap.action_erase_events(action)
		for ev in back:
			InputMap.action_add_event(action, ev)
	_swapped.clear()


## Whether a binding of the game's is one of ours standing in for its own (tests).
func is_ours(ev: InputEvent) -> bool:
	for action in _swapped:
		if _swapped[action].has(ev.get_instance_id()):
			return true
	return false
