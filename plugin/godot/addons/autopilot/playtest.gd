## Autopilot Playtest: the game-side bridge (an autoload, /root/AutopilotPlaytest). Runs inside the game process, in the editor's
## play sessions and in exported builds alike, and keeps working while the game is paused (process_mode ALWAYS).
##
## It is inert until one of these:
##  - the command line asks for it (`-- --playtest-url=ws://...`, `--playtest=verify ...`: CI and headless copies): it
##    connects out to the brain itself (hello host "game") and, for a run asked on the command line, writes the report
##    files and quits with the run's exit code;
##  - the editor's Playtest dock attaches to this play session over the editor's debugger connection: the dock holds the
##    one link to the brain (hello host "editor") and relays requests here, and this answers them back the same way.
## Ops: docs/protocol.md, plus find/transforms/pose/movement (plugin/godot/README.md); `record` and `capture` (the
## game's picture) in lib/media.gd. While a run goes, the game gets Autopilot's input alone (lib/isolation.gd).
extends Node

const Values = preload("lib/values.gd")
const Frame = preload("lib/frame.gd")
const Keys = preload("lib/keys.gd")
const Rc = preload("lib/rc.gd")
const World = preload("lib/world.gd")
const Sight = preload("lib/sight.gd")
const Ui = preload("lib/ui.gd")
const Codex = preload("lib/codex.gd")
const Detectors = preload("lib/detectors.gd")
const RUN_USE_SECONDS := 30.0  # how long after the brain's last request a run counts as using the game
const Link = preload("lib/link.gd")
const Media = preload("lib/media.gd")
const Prefs = preload("lib/prefs.gd")
const Isolation = preload("lib/isolation.gd")
const MouseReads = preload("lib/mouse_reads.gd")

const CAPTURE := "autopilot"
const KEY_TAP_SECONDS := 0.05
const KEY_TAP_FRAMES := 2  # a tap stays down this many frames too: input read once a frame misses a shorter one

var rc
var world
var sight
var ui
var detectors
var media
var isolation  # lib/isolation.gd: the root's last child, the gate the person's input stops at during a run
## The mouse mode as the game knows it: what the game's own scripts read and write (lib/mouse_reads.gd). An int: they
## may use Input's enum or DisplayServer's.
var game_mouse_mode: int:
	get:
		return isolation.game_mouse() if isolation else Input.mouse_mode
	set(value):
		set_game_mouse_mode(value)
var mouse_routed := {"routed": 0, "failed": [], "ms": 0.0}  # lib/mouse_reads.gd: what it did at the game's start
var link = null  # direct mode: our own connection to the brain
var relay := false  # the editor's dock relays for us
var active := false  # a brain is there (welcomed, or the editor attached)
var run_active := false  # a run is going: told by the dock, or known here (the hello carried one, a `cli` brain)
var _last_request_at := -1.0  # when the brain last asked this game something (-1: not yet on this connection)
var _detecting := false  # the detectors worked on the last frame: they start afresh when on again

var game_time := 0.0
var _scene: Node = null
var _launch_scene := ""
var _events := []
var _pending := []  # deferred answers: {"id", "op", "tick": Callable(now) -> bool}
var _held := {}  # key name -> {"spec", "value", "rate", "renewed"}
var _lease := 1.0
var _pressed_actions := {}  # actions move pressed (Input.action_press) -> strength, released with the keys
var _heartbeat_at := 0.0
var _last_real := 0.0
var _codex_thread: Thread = null
var _codex_queue := []
var _timestep_saved = null
var _pointer = null  # where `pointer` put the mouse (the root viewport's pixels), null: the crosshair
var _changing := false  # a scene change asked for (session, the command line's map) and not in yet: `loading`
var _fixed_fps := 0.0  # `timestep` fixed: each frame's game time 1/fps (0: off)
var _fixed_scale := 1.0  # the time scale the game itself set (kept under the fixed step's own)
var _fixed_set := -1.0  # the time scale the fixed step set last

# The command line (CI, headless copies)
var cli_run := {}
var cli_url := ""
var cli_token := ""
var cli_report_dir := ""
var cli_stay_open := false
var cli_map := ""  # -PlaytestMap / --playtest-map: the scene to start in (a res:// path or a scene file's name)
var cli_connect_timeout := 120.0  # -PlaytestConnectTimeout / --playtest-connect-timeout: seconds to wait for the welcome
var cli_share := ""  # -PlaytestShare=0|1 / --playtest-share=...: overrides preferences.json's share_data for this run
var cli_research := ""  # -PlaytestResearch=0|1 / --playtest-research=...: overrides preferences.json's research_agent for this run
var _cli_deadline := 0.0
var _exiting := false
var _run_asked := false  # the command line's run went to a brain (asked once: never again on a new connection)
var _welcomed := false  # the brain welcomed the connection now open


func _ready() -> void:
	process_mode = Node.PROCESS_MODE_ALWAYS
	process_priority = -1000  # before the game's nodes: inputs land before they read them
	rc = Rc.new(self)
	world = World.new(self)
	sight = Sight.new(self, world)
	ui = Ui.new(self)
	media = Media.new(self)
	isolation = Isolation.new(self)
	get_tree().root.add_child.call_deferred(isolation)  # (the root is busy adding the autoloads now)
	isolation.hold_while_starting()  # (first: the routing takes a while in a big project, and the cursor is locked meanwhile)
	if OS.has_feature("editor"):
		mouse_routed = MouseReads.reroute()
	_parse_command_line()
	if EngineDebugger.is_active():
		EngineDebugger.register_message_capture(CAPTURE, _on_editor_message)
	if cli_url != "" or not cli_run.is_empty():
		_prepare_headless()
		detectors = Detectors.new(self, world, ui)
		link = Link.new()
		link.on_open = _send_hello
		link.open(cli_url, cli_token)
		_cli_deadline = _now() + cli_connect_timeout
		print("AutopilotPlaytest: connecting to %s" % link.url)
	if cli_map != "":
		var scene := find_scene(cli_map)
		if scene == "":
			push_warning("AutopilotPlaytest: no scene %s to start in" % cli_map)
		else:
			_launch_scene = scene  # (what a restart reopens)
			_changing = true
			get_tree().change_scene_to_file.call_deferred(scene)


func _exit_tree() -> void:
	isolation.set_on(false)
	if detectors:
		detectors.shutdown()
	if EngineDebugger.is_active() and EngineDebugger.has_capture(CAPTURE):
		EngineDebugger.unregister_message_capture(CAPTURE)
	if _codex_thread and _codex_thread.is_started():
		_codex_thread.wait_to_finish()


func _now() -> float:
	return Time.get_ticks_msec() / 1000.0


## Headless (CI, automated runs): give the root viewport the project's window size (headless it is 64x64, which would squeeze
## the UI and the camera's view), and cap the frame rate the way a display's refresh would.
func _prepare_headless() -> void:
	if DisplayServer.get_name() != "headless":
		return
	var w := int(ProjectSettings.get_setting("display/window/size/viewport_width", 1152))
	var h := int(ProjectSettings.get_setting("display/window/size/viewport_height", 648))
	get_tree().root.size = Vector2i(w, h)
	if Engine.max_fps == 0:
		Engine.max_fps = 60


func _parse_command_line() -> void:
	var args := OS.get_cmdline_user_args()
	if args.is_empty():
		args = OS.get_cmdline_args()
	var kind := ""
	# Both spellings: --playtest-report-dir=... (Godot style) and -PlaytestReportDir=... (as Unreal's plugin takes)
	for a in args:
		var s := str(a)
		var name := s.get_slice("=", 0)
		var key := name.lstrip("-").to_lower().replace("-", "").replace("_", "")
		if not key.begins_with("playtest"):
			continue
		var value := s.substr(name.length() + 1) if "=" in s else ""
		value = value.trim_prefix("\"").trim_suffix("\"")
		match key:
			"playtest":
				kind = value.to_lower()
			"playtestclaim":
				cli_run.claim = value
			"playtestgoal":
				cli_run.goal = value
			"playtestminutes":
				cli_run.minutes = float(value)
			"playtesttimelimit":
				cli_run.time_limit_minutes = float(value)  # (a verify or goal run's whole time limit)
			"playteststeps":
				cli_run.steps = int(value)
			"playtestattempts":
				cli_run.attempts = int(value)
			"playtestturnbased":
				pass  # (runs are always real time: still accepted, so older command lines work, and ignored)
			"playtestbrief":
				cli_run.brief = value
			"playtestbrieffile":
				var f := FileAccess.open(value, FileAccess.READ)
				if f:
					cli_run.brief = f.get_as_text().strip_edges()
			"playtesturl":
				cli_url = value
			"playtesttoken":
				cli_token = value
			"playtestreportdir":
				cli_report_dir = value
			"playtestmap":
				cli_map = value
			"playteststayopen":
				cli_stay_open = true
			"playtestshare":
				cli_share = value if value != "" else "1"
			"playtestresearch":
				cli_research = value if value != "" else "1"
			"playtestconnecttimeout":
				if value.is_valid_float() and float(value) > 0.0:
					cli_connect_timeout = float(value)
				else:
					push_warning("AutopilotPlaytest: --playtest-connect-timeout wants a number of seconds (got \"%s\")" % value)
	if kind != "":
		cli_run.kind = kind
	else:
		cli_run = {}
	if cli_url == "" and not cli_run.is_empty():
		cli_url = OS.get_environment("AUTOPILOT_PLAYTEST_URL")


func project_id() -> String:
	return str(ProjectSettings.get_setting("autopilot/project_id", ""))


func plugin_version() -> String:
	var cfg := ConfigFile.new()
	if cfg.load("res://addons/autopilot/plugin.cfg") == OK:
		return str(cfg.get_value("plugin", "version", "1.0.0"))
	return "1.0.0"


func hello(host: String) -> Dictionary:
	var h := {"type": "hello", "protocol": Link.PROTOCOL, "plugin_version": plugin_version(), "engine": "godot",
		"engine_version": Engine.get_version_info().string, "project": str(ProjectSettings.get_setting("application/config/name", "")),
		"host": host,
		# what this addon does beyond the protocol's first version: the brain falls back for one without them (a key
		# hold with "seconds" let go in the game; `levels`, and `session` playing the `map` asked for; `pointer`;
		# `record` and `capture`, in a game that draws)
		"features": ["timed_hold", "levels", "pointer"] + (["media"] if Media.can_capture() else []),
		# whether the user shares runs' data (preferences.json, or -PlaytestShare on the command line)
		"share": Prefs.share_for_run(cli_share),
		# how input reaches the game (as Unity's: point before clicking, what headless changes)
		"input": Keys.input_caps(DisplayServer.get_name() == "headless")}
	# whether the agent service runs its research agent, only when chosen (preferences.json, or -PlaytestResearch on the
	# command line); left out, the agent service decides (off for the Aura app's, on for this plugin's own)
	var research = Prefs.research_for_run(cli_research)
	if typeof(research) == TYPE_BOOL:
		h.research = research
	if project_id() != "":
		h.project_id = project_id()
	return h


func _send_hello() -> void:
	var h := hello("game")
	if not cli_run.is_empty() and not _run_asked:
		h.run = cli_run
	link.send(h)


func t_value():
	return Frame.r2(game_time) if get_tree().current_scene != null else null


# --- sending ----------------------------------------------------------------------------------------

func _send(frame: Dictionary) -> void:
	if relay:
		EngineDebugger.send_message(CAPTURE + ":frame", [JSON.stringify(frame)])
	elif link:
		link.send(frame)


## A message already made as JSON text (a picture's frame or still, made off the game thread).
func send_text(text: String) -> void:
	if relay:
		EngineDebugger.send_message(CAPTURE + ":frame", [text])
	elif link:
		link.send_text(text)


## A picture for the editor to pass on: head ({"kind": "frame"|"still", ...}) and the JPEG's bytes (the editor makes
## the base64 and the message: the game sends the bytes as they are).
func send_media(head: Dictionary, jpeg: PackedByteArray) -> void:
	if relay:
		EngineDebugger.send_message(CAPTURE + ":media", [JSON.stringify(head), jpeg])


func respond(id, result) -> void:
	_send({"type": "response", "id": id, "ok": true, "result": result, "t": t_value()})


func respond_error(id, error: String, status := 400) -> void:
	_send({"type": "response", "id": id, "ok": false, "status": status, "error": error, "t": t_value()})


func emit_event(event: Dictionary) -> void:
	if active:
		_events.append(event)


# --- the editor's relay -----------------------------------------------------------------------------

func _on_editor_message(message: String, data: Array) -> bool:
	match message:
		"attach":
			relay = true
			active = true
			_last_request_at = -1.0
			_prepare_headless()
			if detectors == null:
				detectors = Detectors.new(self, world, ui)
			if data.size() > 0 and typeof(data[0]) == TYPE_STRING:
				var cfg = JSON.parse_string(data[0])
				if typeof(cfg) == TYPE_DICTIONARY:
					run_active = bool(cfg.get("run_active", false))
				if typeof(cfg) == TYPE_DICTIONARY and typeof(cfg.get("detectors")) == TYPE_DICTIONARY:
					detectors.configure(cfg.detectors)
				# a recording the brain started in an earlier play session of this connection goes on in this one
				if typeof(cfg) == TYPE_DICTIONARY and typeof(cfg.get("record")) == TYPE_DICTIONARY and not media.recording:
					media.start(cfg.record, int(cfg.record.get("n", 0)))
			_isolate()
			isolation.attached()
			EngineDebugger.send_message(CAPTURE + ":ready", [JSON.stringify({"scene": _scene_path(), "t": t_value()})])
			return true
		"detach":
			active = false
			run_active = false
			_isolate()
			_release_all()
			media.stop()
			return true
		"run":
			# a run started or ended in the editor (sent as it changes, before the requests that follow it)
			run_active = data.size() > 0 and bool(data[0])
			_isolate()
			return true
		"record_stop":
			media.stop()
			return true
		"request":
			if data.size() > 0:
				var frame = JSON.parse_string(str(data[0]))
				if typeof(frame) == TYPE_DICTIONARY:
					_handle_request(frame)
			return true
	return false


func _scene_path() -> String:
	var s := get_tree().current_scene
	return s.scene_file_path if s else ""


## The game gets Autopilot's input alone while a brain is there and a run goes (as Unity's: connected, running, in
## play; this process is the play session): the editor's run, or the run asked on the command line until its result.
func _isolate() -> void:
	isolation.set_on(active and run_active)


func set_game_mouse_mode(mode: int) -> void:
	if isolation:
		isolation.set_game_mouse(mode)
	else:
		Input.mouse_mode = mode as Input.MouseMode


func _notification(what: int) -> void:
	# The game's window lost focus: Godot lets go of the keys and actions held (Input.release_pressed_events), Autopilot's
	# with the person's. In a run, Autopilot's go down again.
	if what == NOTIFICATION_APPLICATION_FOCUS_OUT and isolation and isolation.on:
		_reassert_held()


# --- the frame --------------------------------------------------------------------------------------

## Whether a run is using the game now: one is going, or the brain asked this game something in the last
## RUN_USE_SECONDS (a run this side didn't hear start).
func run_using_game(now: float) -> bool:
	return active and (run_active or (_last_request_at >= 0.0 and now - _last_request_at < RUN_USE_SECONDS))


func _process(delta: float) -> void:
	var now := _now()
	var real_dt := now - _last_real if _last_real > 0.0 else delta
	_last_real = now
	var tree := get_tree()
	if _fixed_fps > 0.0:
		_fix_step(real_dt)
	if not tree.paused:
		game_time += delta
	if tree.current_scene != _scene:
		var had := _scene != null
		_scene = tree.current_scene
		game_time = 0.0
		if _launch_scene == "" and _scene and _scene.scene_file_path != "":
			_launch_scene = _scene.scene_file_path
		if _scene != null:
			_changing = false
		if had or _scene != null:
			_release_all()
			emit_event({"kind": "session", "t": t_value(), "running": _scene != null, "scene": _scene_path()})
	_flush_events()
	if link:
		_poll_link(now)
	_isolate()
	isolation.tick(now)
	if not active:
		_events.clear()
		return
	_tick_keys(now, real_dt)
	var i := 0
	while i < _pending.size():
		var p: Dictionary = _pending[i]
		if p.tick.call(now):
			_pending.remove_at(i)
		else:
			i += 1
	_tick_codex()
	media.tick(now)
	# The detectors work only while a run uses the game: in the user's own play sessions nothing reads what they find,
	# and their sweeps over the scene cost frames
	var detect := run_using_game(now)
	if detectors and detect:
		if not _detecting:
			detectors.restart()  # (afresh: what was seen before this run isn't this run's)
		detectors.tick(emit_event, game_time, real_dt, tree.paused)
	_detecting = detect
	if now - _heartbeat_at >= 1.0:
		_heartbeat_at = now
		emit_event({"kind": "heartbeat", "t": t_value(), "fps": Engine.get_frames_per_second(), "paused": tree.paused})
	_flush_events()


## Send the events gathered so far (once a frame; and before the frame's answers, so a scene change is told before
## an answer from the new scene).
func _flush_events() -> void:
	if _events.size() > 0 and active:
		var batch := _events
		_events = []
		_send({"type": "events", "events": batch, "t": t_value()})


func _poll_link(now: float) -> void:
	for frame in link.poll():
		var type := str(frame.get("type", ""))
		match type:
			"request":
				_handle_request(frame)
			"welcome":
				link.role = str(frame.get("role", ""))
				active = true
				_welcomed = true
				_last_request_at = -1.0
				if not cli_run.is_empty():
					_run_asked = true  # (its hello carried the run)
				run_active = not cli_run.is_empty() or link.role == "cli"
				print("AutopilotPlaytest: connected (%s)" % link.role)
			"status":
				print("AutopilotPlaytest: %s" % frame.get("text", ""))
			"finding":
				var f: Dictionary = frame.get("finding", {})
				print("AutopilotPlaytest: finding: %s" % f.get("summary", ""))
			"result":
				_handle_result(frame)
			"error":
				link.problem = str(frame.get("message", ""))
				link.problem_code = int(frame.get("code", 0))
				push_warning("AutopilotPlaytest: the brain sent an error: %s" % link.problem)
				link.close()
				active = false
				if not cli_run.is_empty() and not cli_stay_open:
					_fail_run("the brain sent an error: %s" % link.problem, false)
			"_closed":
				active = false
				run_active = false
				_release_all()
				media.stop()  # (a recording never outlives its connection)
				var welcomed := _welcomed
				_welcomed = false
				if _run_asked and not _exiting and not cli_stay_open:
					# The run asked on the command line lost its brain before its result: a new connection would
					# start it over, so the run ends here as an error
					_fail_run("the connection to the brain closed before the run's result")
				elif not cli_run.is_empty() and frame.get("was_connected", false) and not welcomed and not _exiting \
						and not cli_stay_open:
					# The brain took the connection and closed it without a welcome: it turned the run away (its error
					# frame, if any, can be lost with the close: WebSocketPeer drops what a closing poll buffered).
					# Asking again would be turned away again, and in a loop
					_fail_run("the brain closed the connection without taking the run (%s)" % link.problem)
				elif frame.get("was_connected", false):
					_cli_deadline = now + cli_connect_timeout
	if not cli_run.is_empty() and not _exiting and not _welcomed and not _run_asked and now > _cli_deadline:
		_fail_run("no welcome from the brain at %s within %d s" % [link.url, int(cli_connect_timeout)])


## The run asked on the command line ends without a result: result.json gets the error, and the game exits 1.
func _fail_run(message: String, log_error := true) -> void:
	if log_error:
		push_error("AutopilotPlaytest: " + message)
	var dir := _write_report({"kind": str(cli_run.get("kind", "")), "error": message, "exit_code": 1}, {})
	print("AutopilotPlaytest: run failed. Report: %s" % dir)
	_exit(1)


func _handle_result(frame: Dictionary) -> void:
	run_active = false
	_isolate()
	var result: Dictionary = frame.get("result", {}) if typeof(frame.get("result")) == TYPE_DICTIONARY else {}
	var dir := _write_report(result, frame.get("files", {}))
	print("AutopilotPlaytest: run finished. Report: %s" % dir)
	if cli_run.is_empty() or cli_stay_open:
		return
	var code := 0
	if result.has("exit_code"):
		code = int(result.exit_code)
	elif result.has("verdict"):
		code = {"CONFIRMED": 0, "DENIED": 2}.get(str(result.verdict), 3)
	else:
		code = 2 if typeof(result.get("bugs")) == TYPE_ARRAY and result.bugs.size() > 0 else 0
	_exit(code)


## Write the brain's report files and result.json into the report folder (--playtest-report-dir, or
## user://playtest/<time>); the folder's absolute path.
func _write_report(result: Dictionary, files) -> String:
	var dir := cli_report_dir
	if dir == "":
		dir = "user://playtest/" + Time.get_datetime_string_from_system().replace(":", "").replace("-", "").replace("T", "-")
	dir = ProjectSettings.globalize_path(dir) if dir.begins_with("user://") or dir.begins_with("res://") else dir
	DirAccess.make_dir_recursive_absolute(dir)
	if typeof(files) == TYPE_DICTIONARY:
		for name in files:
			var clean := str(name).get_file()  # never outside the report folder
			if clean == "" or clean == "." or clean == "..":
				continue
			var f := FileAccess.open(dir.path_join(clean), FileAccess.WRITE)
			if f:
				f.store_string(str(files[name]))
	var rf := FileAccess.open(dir.path_join("result.json"), FileAccess.WRITE)
	if rf:
		rf.store_string(JSON.stringify(result, "  "))
	return dir


func _exit(code: int) -> void:
	if _exiting:
		return
	_exiting = true
	print("AutopilotPlaytest: exiting with code %d" % code)
	_release_all()
	if link:
		link.close()
	get_tree().quit(code)


# --- requests ---------------------------------------------------------------------------------------

func _handle_request(frame: Dictionary) -> void:
	_last_request_at = _now()  # (a run is using the game: run_using_game)
	var id = frame.get("id")
	if typeof(id) == TYPE_FLOAT:
		id = int(id)
	var op := str(frame.get("op", ""))
	var args = frame.get("args", {})
	if typeof(args) != TYPE_DICTIONARY:
		args = {}
	var r = dispatch(id, op, args)
	if r == null:
		return  # deferred: answered later
	if r[0]:
		respond(id, r[1])
	else:
		respond_error(id, str(r[1]), r[2] if r.size() > 2 else 400)


## [true, result] | [false, error, status?] | null (the op answers later).
func dispatch(id, op: String, args: Dictionary):
	match op:
		"rc":
			return rc.handle(str(args.get("verb", "")), args.get("body", null))
		"context":
			if args.has("player"):
				world.chosen = str(args.get("player", ""))
			return [true, context()]
		"session":
			return session(id, args)
		"levels":
			return [true, levels()]
		"pointer":
			return pointer_op(args)
		"pause":
			return [true, pause_op(bool(args.get("paused", false)))]
		"input":
			return input_op(id, args)
		"key":
			return key_op(id, args)
		"move":
			return move_op(id, args)
		"watch":
			return watch_op(id, args)
		"view":
			return sight.view(args)
		"screen":
			return [true, ui.screen(args)]
		"ui":
			return [true, {"elements": ui.list()}]
		"ui_act":
			var r: Array = ui.act(args)
			if r[0] and r[1] is Dictionary:
				return [true, r[1]]
			return r
		"timestep":
			return timestep(args)
		"detectors":
			if detectors:
				detectors.configure(args)
			return [true, {}]
		"ping":
			return [true, {"game_time": Frame.r2(game_time), "wall_time": Time.get_unix_time_from_system()}]
		"record":
			match str(args.get("action", "")):
				"start":
					return media.start(args)
				"stop":
					return [true, media.stop()]
			return [false, "record wants action start or stop"]
		"capture":
			return media.capture(id, args)
		"codex_manifest":
			_codex_queue.append({"id": id, "op": op, "args": args})
			return null
		"codex_read":
			var paths: Array = args.get("paths", [])
			if paths.size() <= 3:
				return [true, Codex.read(paths, int(args.get("max_bytes", Codex.DEFAULT_READ)), _summary_file())]
			_codex_queue.append({"id": id, "op": op, "args": args})
			return null
		"find":
			return world.find(args)
		"transforms":
			return [true, world.transforms(args, rc.resolve)]
		"pose":
			return world.pose(args)
		"movement":
			return world.movement(args)
	return [false, "unknown op %s" % op]


## How the game is networked (Unreal's context `net`): {"mode": "standalone" | "listen_server" | "dedicated_server" |
## "client"}; a server says how many clients are connected (`clients`), a client says where the server's world is
## (`server_world`: "" for another process, which a Godot server always is) and how many other clients it knows.
func net() -> Dictionary:
	var mp := multiplayer
	var peer = mp.multiplayer_peer if mp else null
	if peer == null or peer is OfflineMultiplayerPeer or peer.get_connection_status() == MultiplayerPeer.CONNECTION_DISCONNECTED:
		return {"mode": "standalone"}
	if mp.is_server():
		return {"mode": "dedicated_server" if DisplayServer.get_name() == "headless" else "listen_server",
			"clients": mp.get_peers().size()}
	return {"mode": "client", "server_world": "", "clients": maxi(0, mp.get_peers().size() - 1)}


## `pause`: the game's tree paused or not. A networked client pauses only its own copy of the game (the server's goes
## on, as Unreal's can't be paused from a client): the answer says why with `why`.
func pause_op(paused: bool) -> Dictionary:
	get_tree().paused = paused
	var out := {"paused": get_tree().paused}
	if net().mode == "client":
		out.why = "the game's server runs in another process: it can't be paused from here (only this client's copy is)"
	return out


func context() -> Dictionary:
	var p: Node = world.player()
	var s := get_tree().current_scene
	var cam = world.camera3d()
	if cam == null:
		cam = world.camera2d()
	return {"world": str(s.get_path()) if s else "", "scene": _scene_path(), "character": str(p.get_path()) if p else "",
		"controller": "", "input_subsystem": "", "camera_manager": str(cam.get_path()) if cam else "",
		"camera": str(cam.get_path()) if cam else "", "dimension": world.dimension(),
		"game_name": str(ProjectSettings.get_setting("application/config/name", "")), "host": "editor" if relay else "game",
		"engine": "godot", "session_running": s != null, "paused": get_tree().paused, "game_time": Frame.r2(game_time),
		"project_dir": ProjectSettings.globalize_path("res://"), "controls": Keys.controls(),
		"input": Keys.input_caps(DisplayServer.get_name() == "headless", game_mouse_mode, _move_actions().any(func(a): return a != ""),
			isolation.on),
		"net": net(), "player_found_by": "context's player" if world.chosen != "" and p != null else ""}


# --- session ----------------------------------------------------------------------------------------

## A scene by res:// path, or by its file's name ("level" or "level.tscn"), searched in the project.
func find_scene(name: String) -> String:
	if name.begins_with("res://") or name.begins_with("uid://"):
		return name if ResourceLoader.exists(name) else ""
	var want := name.get_file()
	if not want.ends_with(".tscn") and not want.ends_with(".scn"):
		want += ".tscn"
	for f in Codex.scenes():
		if str(f).get_file().to_lower() == want.to_lower():
			return f
	return ""


## `levels` (docs/protocol.md) in the game itself (headless, an exported build): the project's scenes, and the one
## playing, which is also what a start without a map plays (the game is already in it).
func levels() -> Dictionary:
	var playing := _scene_path()
	return {"levels": Codex.scenes(), "open": playing, "playing": playing}


## `session` in the game itself: `restart` reopens the scene the game was launched with (or `map`), as a fresh start
## of it; `start` with a `map` the game isn't playing plays that one the same way (a brain choosing the level asks for
## it so); `start` without one, `stop` and `status` say whether a scene runs.
func session(id, args: Dictionary):
	var action := str(args.get("action", "status"))
	var map := str(args.get("map", ""))
	if action == "start" and map != "":
		var asked := find_scene(map)
		if asked == "":
			return [false, "No scene %s" % map, 404]
		if asked == _scene_path():
			return [true, {"running": true, "scene": asked}]
		action = "restart"
	if action != "restart":
		return [true, {"running": get_tree().current_scene != null, "loading": _changing, "scene": _scene_path()}]
	var target := ""
	if map != "":
		target = find_scene(map)
		if target == "":
			return [false, "No scene %s" % map, 404]
	else:
		target = _launch_scene if _launch_scene != "" else _scene_path()
	if target == "":
		return [false, "No scene to restart"]
	_release_all()
	if args.get("fresh", false):
		_reset_autoloads()
	var old := get_tree().current_scene
	var old_id := old.get_instance_id() if old else 0  # (the scene itself is freed: never capture it)
	get_tree().paused = false
	var err := get_tree().change_scene_to_file(target)
	if err != OK:
		return [false, "Couldn't open %s (error %d)" % [target, err], 500]
	_changing = true
	var started := _now()
	var state := {"seen": 0}
	_pending.append({"id": id, "op": "session", "tick": func(now: float) -> bool:
		var cur := get_tree().current_scene
		if cur != null and cur.get_instance_id() != old_id and cur.is_inside_tree():
			state.seen += 1
			if state.seen >= 2:  # a frame after it is in: its _ready has run
				respond(id, {"running": true, "scene": cur.scene_file_path})
				return true
		if now - started > 30.0:
			_changing = false; respond_error(id, "Timed out reopening the scene", 500)
			return true
		return false})
	return null


## Put the game's autoloads' script variables back to the values a fresh instance starts with (session restart with
## "fresh": true). Their nodes stay (scripts hold them by name); what their _ready() set isn't redone.
func _reset_autoloads() -> void:
	for p in ProjectSettings.get_property_list():
		var key: String = p.name
		if not key.begins_with("autoload/"):
			continue
		var name := key.substr(9)
		if name == "AutopilotPlaytest":
			continue
		var node := get_tree().root.get_node_or_null(name)
		if node == null or not (node.get_script() is Script):
			continue
		var fresh = node.get_script().new()
		for prop in node.get_script().get_script_property_list():
			if prop.usage & PROPERTY_USAGE_SCRIPT_VARIABLE:
				node.set(prop.name, fresh.get(prop.name))
		if fresh is Node:
			fresh.free()


## `timestep`: `fixed` makes each frame's game time 1/fps (as Unreal's fixed time step): Godot has no fixed frame time
## at run time (only the --fixed-fps command line), so the frame rate is capped at fps and every frame sets
## Engine.time_scale so that the frame's game time is 1/fps of the wall time the frame before took (a frame much
## slower than the one before it still runs a little long); the game's own time scale is kept under it. Off: both
## back as they were.
func timestep(args: Dictionary):
	if args.get("fixed", false):
		if _timestep_saved == null:
			_timestep_saved = [Engine.max_fps, Engine.time_scale]
			_fixed_scale = Engine.time_scale
		var fps := maxi(1, int(args.get("fps", 30)))
		Engine.max_fps = fps
		_fixed_fps = float(fps)
		_fixed_set = -1.0
		return [true, {"note": "each frame's game time is set to 1/%d s through Engine.time_scale (from the frame before's wall time), the frame rate capped at %d" % [fps, fps]}]
	if _timestep_saved != null:
		Engine.max_fps = _timestep_saved[0]
		Engine.time_scale = _fixed_scale
		_timestep_saved = null
	_fixed_fps = 0.0
	return [true, {}]


func _fix_step(real_dt: float) -> void:
	if _fixed_set >= 0.0 and not is_equal_approx(Engine.time_scale, _fixed_set):
		_fixed_scale = Engine.time_scale  # (the game set its own: kept under ours)
	if real_dt <= 0.0:
		return
	_fixed_set = clampf(_fixed_scale * (1.0 / _fixed_fps) / real_dt, 0.01, 100.0)
	Engine.time_scale = _fixed_set


# --- the player's hands -----------------------------------------------------------------------------

## Autopilot's event to the game, as the operating system's are: Input.parse_input_event, flushed at once (headless,
## nothing else flushes them; windowed, they land this frame), and let through the run's gate (lib/isolation.gd).
func _deliver(ev: InputEvent) -> void:
	isolation.deliver(ev)


func _crosshair() -> Vector2:
	return get_viewport().get_visible_rect().size / 2.0


## Where the player's mouse is, in the root viewport's own pixels (from its top left, y down): where `pointer` put it,
## else the middle of the view (the crosshair, where a game that never shows its cursor aims).
func _mouse_at() -> Vector2:
	return _pointer if _pointer != null else _crosshair()


## A place in the root viewport's pixels as the window's: what an event given to Input.parse_input_event carries (the
## viewport takes the window's stretch back off it, as it does for the real mouse).
func _to_window(p: Vector2) -> Vector2:
	return get_viewport().get_final_transform() * p


## The mouse buttons down now, with `button` down or up as `pressed` says (a mouse event's button_mask).
func _buttons_with(button: int, pressed: bool) -> int:
	var mask := Input.get_mouse_button_mask()
	if button >= MOUSE_BUTTON_LEFT and button <= MOUSE_BUTTON_XBUTTON2:
		var bit := 1 << (button - 1)
		mask = (mask | bit) if pressed else (mask & ~bit)
	return mask


## `pointer` (as Unity's): the mouse goes to a place on screen without moving across to it (a motion event there, with
## no `relative`: a game turning its view by the mouse doesn't turn). {"screen_x", "screen_y"}: -1 to 1 from the middle
## of the view, y up (the numbers `view` gives a thing); {"world": a kit point} or {"object": a path}: where the camera
## shows it (a node's drawn box's middle; a Control's middle). The buttons pressed with `key` then press there (a GUI
## control, or what the game picks in the world under it: `_input_event`, a ray from the mouse), and holding a button
## while pointing again drags. Answers {"screen_x", "screen_y", "x", "y": the viewport's pixels from its top left,
## "on_screen"}.
func pointer_op(args: Dictionary) -> Array:
	var vp := get_viewport()
	var size := vp.get_visible_rect().size
	var w := maxf(1.0, size.x)
	var h := maxf(1.0, size.y)
	var px := Vector2.ZERO
	var at_world = args.get("world", null)
	var object_path := str(args.get("object", "")) if args.get("object") != null else ""
	if typeof(at_world) == TYPE_DICTIONARY or object_path != "":
		var node = null
		if object_path != "":
			node = rc.resolve(object_path)
			if not (node is Node):
				return [false, "Object %s does not exist" % object_path, 404]
		if node is Control:
			px = node.get_global_transform_with_canvas() * (node.size / 2.0)
		elif node is CanvasItem or (node == null and world.dimension() == "2d"):
			if node == null:
				px = vp.get_canvas_transform() * Vector2(float(at_world.get("X", 0.0)), -float(at_world.get("Z", 0.0)))
			else:
				px = node.get_canvas_transform() * World.bounds2d(node).get_center()
		else:
			var cam: Camera3D = world.camera3d()
			if cam == null:
				return [false, "No camera"]
			var at := Vector3.ZERO
			if node == null:
				at = Frame.from_kit(at_world)
			elif node is Node3D:
				var box: AABB = World.bounds3d(node)
				at = box.get_center() if box.size.length() > 0.0 else node.global_position
			else:
				return [false, "%s is neither in the world nor on the screen" % object_path]
			if cam.is_position_behind(at):
				return [false, "That is behind the camera"]
			px = cam.unproject_position(at)
	elif args.has("screen_x") or args.has("screen_y"):
		px = Vector2((float(args.get("screen_x", 0.0)) + 1.0) / 2.0 * w, (1.0 - float(args.get("screen_y", 0.0))) / 2.0 * h)
	else:
		return [false, "pointer needs screen_x and screen_y, world or object"]
	_set_pointer(px)
	return [true, {"screen_x": snappedf(px.x / w * 2.0 - 1.0, 0.001), "screen_y": snappedf(1.0 - px.y / h * 2.0, 0.001),
		"x": roundf(px.x), "y": roundf(h - px.y), "on_screen": px.x >= 0.0 and px.x <= w and px.y >= 0.0 and px.y <= h}]


## The mouse there (the viewport's pixels): a motion event with no `relative`, carrying the buttons held (a drag), so
## the GUI, the game's input handlers and Input's own mouse position all follow it.
func _set_pointer(px: Vector2) -> void:
	_pointer = px
	var ev := InputEventMouseMotion.new()
	var at := _to_window(px)
	ev.position = at
	ev.global_position = at
	ev.button_mask = Input.get_mouse_button_mask()
	_deliver(ev)


func _event(spec: Dictionary, pressed: bool, value := 1.0) -> InputEvent:
	match spec.kind:
		"key":
			var ev := InputEventKey.new()
			ev.keycode = spec.keycode
			ev.physical_keycode = spec.keycode
			ev.key_label = spec.keycode
			ev.pressed = pressed
			var s := OS.get_keycode_string(spec.keycode)
			if pressed and s.length() == 1:
				ev.unicode = s.to_lower().unicode_at(0)
			elif pressed and spec.keycode == KEY_SPACE:
				ev.unicode = 32
			return ev
		"mouse_button", "wheel":
			var ev := InputEventMouseButton.new()
			ev.button_index = spec.button
			ev.pressed = pressed
			ev.position = _to_window(_mouse_at())  # (where `pointer` put the mouse, else the crosshair)
			ev.global_position = ev.position
			ev.button_mask = _buttons_with(spec.button, pressed)
			if spec.kind == "wheel":
				ev.factor = absf(value)
			return ev
		"mouse_axis":
			var ev := InputEventMouseMotion.new()
			# Unreal's MouseY is positive up, Godot's relative.y positive down
			var rel := Vector2(value, 0.0) if spec.axis == "MouseX" else Vector2(0.0, -value)
			ev.relative = rel
			ev.screen_relative = rel
			ev.position = _to_window(_mouse_at())
			ev.global_position = ev.position
			ev.button_mask = Input.get_mouse_button_mask()
			return ev
		"wheel_axis":
			var ev := InputEventMouseButton.new()
			ev.button_index = MOUSE_BUTTON_WHEEL_UP if value > 0 else MOUSE_BUTTON_WHEEL_DOWN
			ev.pressed = pressed
			ev.factor = absf(value)
			ev.position = _to_window(_mouse_at())
			ev.global_position = ev.position
			return ev
		"joy_button":
			var ev := InputEventJoypadButton.new()
			ev.button_index = spec.button
			ev.pressed = pressed
			ev.pressure = 1.0 if pressed else 0.0
			return ev
		"joy_axis":
			var ev := InputEventJoypadMotion.new()
			ev.axis = spec.axis
			ev.axis_value = clampf(value * spec.sign, -1.0, 1.0) if pressed else 0.0
			return ev
		"joy_trigger":
			var ev := InputEventJoypadMotion.new()
			ev.axis = spec.axis
			ev.axis_value = 1.0 if pressed else 0.0
			return ev
		"action":
			var ev := InputEventAction.new()
			ev.action = spec.action
			ev.pressed = pressed
			ev.strength = clampf(absf(value), 0.0, 1.0) if pressed else 0.0
			return ev
	return null


func _send_key(spec: Dictionary, pressed: bool, value := 1.0) -> void:
	var ev := _event(spec, pressed, value)
	if ev:
		_deliver(ev)
	if spec.kind == "wheel" and pressed:
		_deliver(_event(spec, false, value))


func key_op(id, args: Dictionary):
	var mode := str(args.get("mode", "press"))
	if args.has("lease") and float(args.lease) > 0.0:
		_lease = clampf(float(args.lease), 0.2, 10.0)
	if mode == "release_all":
		_release_all()
		return [true, {}]
	var name := str(args.get("key", ""))
	var spec := Keys.spec(name)
	if spec.is_empty():
		return [false, "Unknown key %s" % name]
	var value := float(args.get("value", 1.0))
	var rate := bool(args.get("rate", false))
	var axis := Keys.is_axis(spec)
	if mode == "release":
		if _held.has(name):
			if not axis:
				_send_key(spec, false)
			elif spec.kind == "joy_axis":
				_send_key(spec, false)
			_held.erase(name)
		return [true, {}]
	if mode == "hold":
		if not _held.has(name):
			_held[name] = {"spec": spec}
			if not axis:
				_send_key(spec, true, value)
		elif spec.kind == "action" and _held[name].value != value:
			_send_key(spec, true, value)
		_held[name].value = value
		_held[name].rate = rate
		_held[name].renewed = _now()
		# a timed hold ("seconds"): let go after that much game time, without renewal, however late the brain is
		var seconds := float(args.get("seconds", 0.0))
		_held[name].until = game_time + seconds if seconds > 0.0 else 0.0
		return [true, {}]
	# a tap: an axis moves once; a key goes down, and up again a moment later, as a quick press does
	if axis:
		_send_key(spec, true, value)
		if spec.kind == "joy_axis":
			_send_key(spec, false)
		return [true, {}]
	_send_key(spec, true, value)
	var started := _now()
	var frame := Engine.get_process_frames()
	_pending.append({"id": id, "op": "key", "tick": func(now: float) -> bool:
		if now - started < KEY_TAP_SECONDS or Engine.get_process_frames() < frame + KEY_TAP_FRAMES:
			return false
		if not _held.has(name):
			_send_key(spec, false)
		respond(id, {})
		return true})
	return null


func _tick_keys(now: float, dt: float) -> void:
	for name in _held.keys():
		var h: Dictionary = _held[name]
		var release_at := float(h.get("until", 0.0))
		if (game_time >= release_at) if release_at > 0.0 else (now - h.renewed > _lease):
			if not Keys.is_axis(h.spec) or h.spec.kind == "joy_axis":
				_send_key(h.spec, false)
			_held.erase(name)
		elif h.spec.kind == "mouse_axis" or h.spec.kind == "wheel_axis":
			_send_key(h.spec, true, h.value * dt if h.rate else h.value)
		elif h.spec.kind == "joy_axis":
			_send_key(h.spec, true, h.value)


func _release_all() -> void:
	for name in _held:
		var h: Dictionary = _held[name]
		if not Keys.is_axis(h.spec) or h.spec.kind == "joy_axis":
			_send_key(h.spec, false)
	_held.clear()
	for a in _pressed_actions:
		if InputMap.has_action(a):
			Input.action_release(a)
	_pressed_actions.clear()


## The actions move presses, pressed again (a joypad event matched by one of them, or the window losing focus, let go
## of them).
func _reassert_actions() -> void:
	for a in _pressed_actions:
		if InputMap.has_action(a):
			Input.action_press(a, float(_pressed_actions[a]))


## Everything Autopilot holds, down again: its held keys, buttons and joypad controls, and the actions move presses.
func _reassert_held() -> void:
	for name in _held:
		var h: Dictionary = _held[name]
		if not Keys.is_axis(h.spec) or h.spec.kind == "joy_axis":
			_send_key(h.spec, true, float(h.get("value", 1.0)))
	_reassert_actions()


## `input`: an InputMap action by name ({"action": "jump", "value"?: {"X"..} or a number, "mode"}). press: down,
## then up a moment later; hold: down until release (no lease, as Unreal's continuous injection).
func input_op(id, args: Dictionary):
	var action := str(args.get("action", ""))
	if not InputMap.has_action(action):
		return [false, "No input action named %s" % action]
	var mode := str(args.get("mode", "press"))
	var v = args.get("value", 1.0)
	var strength := 1.0
	if typeof(v) == TYPE_DICTIONARY:
		strength = absf(float(v.get("X", v.get("x", 1.0))))
	elif typeof(v) in [TYPE_FLOAT, TYPE_INT]:
		strength = absf(float(v))
	var spec := {"kind": "action", "action": action}
	if mode == "release":
		_send_key(spec, false)
		return [true, {}]
	_send_key(spec, true, strength)
	if mode == "hold":
		return [true, {}]
	var started := _now()
	var frame := Engine.get_process_frames()
	_pending.append({"id": id, "op": "input", "tick": func(now: float) -> bool:
		if now - started < KEY_TAP_SECONDS or Engine.get_process_frames() < frame + KEY_TAP_FRAMES:
			return false
		_send_key(spec, false)
		respond(id, {})
		return true})
	return null


## The game's movement actions: [forward, back, left, right], the actions W, S, A, D are bound to (the arrow keys
## when those aren't bound), "" where none.
func _move_actions() -> Array:
	var out := []
	for pair in [["W", "Up"], ["S", "Down"], ["A", "Left"], ["D", "Right"]]:
		var a := Keys.action_for_key(pair[0])
		if a == "":
			a = Keys.action_for_key(pair[1])
		out.append(a)
	return out


## `move`: walk the player in a kit-frame direction for `seconds`, as its movement keys would (the actions bound to
## W/A/S/D, pressed with analog strengths that point the camera-relative input that way; the keys themselves when the
## game has no such actions). Answers {"moved": units across the floor}.
func move_op(id, args: Dictionary):
	var player: Node = world.player()
	if player == null:
		return [false, "No player"]
	var is3d := player is Node3D
	var d = args.get("direction", {})
	var scale := clampf(float(args.get("scale", 1.0)), -1.0, 1.0)
	var seconds := float(args.get("seconds", 0.35))
	var actions := _move_actions()
	# how (as Unity's `via`): "stick" the movement actions pressed with analog strengths (any direction), "keys" the
	# keys W/A/S/D themselves (8 directions), "controller" the body moved directly (move_and_collide at its walk speed:
	# a game whose input can't be reached), "auto" the actions when the game has them, else the keys
	var asked := str(args.get("via", "auto"))
	var has_actions := actions.any(func(a): return a != "")
	var via := asked
	match asked:
		"auto":
			via = "stick" if has_actions else "keys"
		"stick":
			if not has_actions:
				return [false, "move can't go via stick here (no move action bound to W/A/S/D or the arrows)"]
		"keys", "controller":
			pass
		_:
			return [false, "move can't go via %s (stick, keys, controller or auto)" % asked]
	if via == "controller" and not (player is PhysicsBody3D or player is PhysicsBody2D):
		return [false, "move can't go via controller here (the player isn't a physics body)"]
	if via == "keys":
		actions = ["", "", "", ""]  # (the keys themselves, pressed digitally)
	var walk_speed := 5.0 if is3d else 300.0
	if via == "controller":
		var mv: Array = world.movement()
		if mv[0] and mv[1].walk_speed != null:
			walk_speed = float(mv[1].walk_speed) / (Frame.SCALE if is3d else 1.0)
	var start: Vector3 = player.global_position if is3d else Vector3(player.global_position.x, 0, player.global_position.y)
	var started := _now()
	var keys_down := {}
	var pid := player.get_instance_id()  # (never capture a node: it may be freed meanwhile)
	_pending.append({"id": id, "op": "move", "tick": func(now: float) -> bool:
		var p = instance_from_id(pid)
		var alive: bool = p != null and is_instance_valid(p) and p.is_inside_tree()
		if alive and now - started < seconds:
			var f := 0.0
			var r := 0.0
			if is3d:
				var want := Frame.from_kit_dir(d)
				want.y = 0.0
				if want.length() > 0.0001:
					want = want.normalized()
				var cam = world.camera3d()
				var basis: Basis = cam.global_basis if cam else p.global_basis
				var fwd := -basis.z
				fwd.y = 0.0
				var right := basis.x
				right.y = 0.0
				f = want.dot(fwd.normalized()) * scale
				r = want.dot(right.normalized()) * scale
				if via == "controller":
					p.move_and_collide(want * scale * walk_speed * get_process_delta_time())
					return false
			else:
				# 2D: kit X is screen right, kit Z screen up
				r = clampf(float(d.get("X", 0.0)), -1.0, 1.0) * scale
				f = clampf(float(d.get("Z", 0.0)), -1.0, 1.0) * scale
				if via == "controller":
					p.move_and_collide(Vector2(r, -f) * walk_speed * get_process_delta_time())
					return false
			_drive([f, -f, -r, r], actions, keys_down)
			return false
		_drive([0.0, 0.0, 0.0, 0.0], actions, keys_down)
		var end: Vector3 = start
		if alive:
			end = p.global_position if is3d else Vector3(p.global_position.x, 0, p.global_position.y)
		var moved := Vector2(end.x - start.x, end.z - start.z).length() * (Frame.SCALE if is3d else 1.0)
		respond(id, {"moved": snappedf(moved, 0.1), "via": via})
		return true})
	return null


func _drive(strengths: Array, actions: Array, keys_down: Dictionary) -> void:
	var key_names := ["W", "S", "A", "D"]
	for i in 4:
		var s: float = maxf(0.0, strengths[i])
		var a: String = actions[i]
		if a != "":
			if s > 0.01:
				Input.action_press(a, s)
				_pressed_actions[a] = s
			elif _pressed_actions.has(a):
				Input.action_release(a)
				_pressed_actions.erase(a)
		else:
			var down := s > 0.38
			if down != keys_down.get(i, false):
				keys_down[i] = down
				_send_key(Keys.spec(key_names[i]), down)


func watch_op(id, args: Dictionary):
	var st: Dictionary = sight.watch_start(args, rc.resolve)
	var seconds := float(args.get("seconds", 0.6))
	var started := _now()
	sight.watch_tick(st, game_time)
	_pending.append({"id": id, "op": "watch", "tick": func(now: float) -> bool:
		sight.watch_tick(st, game_time)
		if now - started < seconds:
			return false
		respond(id, sight.watch_finish(st))
		return true})
	return null


# --- codex (off the game thread) --------------------------------------------------------------------

var _summary_path := ""


func _summary_file() -> String:
	return _summary_path


func _tick_codex() -> void:
	if _codex_thread != null:
		if _codex_thread.is_alive():
			return
		var done: Dictionary = _codex_thread.wait_to_finish()
		_codex_thread = null
		if done.has("summary"):
			_summary_path = done.summary
		respond(done.id, done.result)
	if _codex_queue.is_empty():
		return
	var job: Dictionary = _codex_queue.pop_front()
	var project := str(ProjectSettings.get_setting("application/config/name", ""))
	var summary := _summary_path
	_codex_thread = Thread.new()
	if job.op == "codex_manifest":
		var skip := str(job.args.get("export", "auto")) == "skip"
		_codex_thread.start(func() -> Dictionary:
			var s := summary if skip else Codex.write_summary()
			return {"id": job.id, "summary": s, "result": Codex.manifest(project, s, not skip)})
	else:
		_codex_thread.start(func() -> Dictionary:
			return {"id": job.id, "result": Codex.read(job.args.get("paths", []), int(job.args.get("max_bytes", Codex.DEFAULT_READ)), summary)})
