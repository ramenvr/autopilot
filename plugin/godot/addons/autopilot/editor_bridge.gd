## The editor's link to the brain (hello host "editor"). The editor and the game are separate processes in Godot, so:
##  - this holds the one WebSocket to the brain (the brain expects one link per run, whatever the game does);
##  - the brain's `session` requests start, stop and restart the play session here (EditorInterface.play_main_scene /
##    play_custom_scene / stop_playing_scene); `codex_manifest`/`codex_read` are answered here, from the project's files;
##  - every other request goes to the running game's autoload over the editor's debugger connection (debugger.gd), and
##    the game's answers and events come back the same way, passed on to the brain unchanged;
##  - with no game running, `context` and `ping` are answered here (session_running false) and the rest get an error;
##  - `record` is followed here: a recording goes on in the next play session of the same connection (each is a new
##    game process) unless the brain stopped it, `stop` is answered here, and the game's pictures come over the debugger
##    as JPEG bytes ("media" messages) that are made into `frame` messages and `capture` answers here;
##  - the brain's `usage` (its token counts for the run) goes to the dock (usage_received), and its `bill` (what Autopilot
##    was billed for a run through Aura, after the run and before its result) too (bill_received);
##  - the account (docs/protocol.md "Account"): the brain's `account` frames (how runs go: Aura, BYOK, none) are kept
##    in `account`, its `aura_sign_in` frames go to the dock (sign_in_frame); the dock asks for a sign-in (aura_sign_in)
##    and the rest; a sign-in asked on a connection that closed is asked again on the next (_resume_sign_in). The addon
##    never sees Aura's tokens: the agent service signs in.
## The dock (dock.gd) drives runs through it: start / stop / clear_memory, and shows status, findings and results.
@tool
extends Node

signal status_changed(text: String)
signal server_status(text: String)  # a status line the agent server sent (not this plugin's own notices); before status_changed
signal activity_received(frame: Dictionary)  # an activity frame: one event of the run's activity feed (docs/protocol.md, The activity feed)
signal connection_changed(connected: bool)
signal finding_received(finding: Dictionary)
signal result_received(result: Dictionary, dir: String)
signal run_changed(running: bool)
## A run_started frame: a run the tab didn't start (the user's own Claude Code or Codex started it through `autopilot
## mcp`), its fields a start frame's and `by`; before run_changed(true). Not sent while a run is going.
signal run_started_elsewhere(run: Dictionary)
## A run_kind frame: the kind of run the agent service read the words as (verify, goal or explore; docs/protocol.md,
## "One prompt box"), before the run plays; tested: an exploration asked for as a test of the game.
signal run_kind_received(kind: String, tested: bool)
signal policy_step(frame: Dictionary)  # a "jev" frame: one of the agent's policy steps (only from an agent server on this machine)
signal memory_changed  # a memory_state came: memory_known / memory_runs / memory_notes / memory_lib changed
signal memory_cleared(ok: bool, message: String)  # a memory_cleared frame: Reset memory done, or not and why
signal usage_received(models: Array, final: bool)  # a usage frame: the run's token counts so far, per model
signal bill_received(frame: Dictionary)  # a bill frame: what Autopilot was billed for the run through Aura that just finished
signal account_changed  # an account frame came (account)
signal sign_in_frame(frame: Dictionary)  # an aura_sign_in frame: {"state": "waiting"|"signed_in"|"error", ...}

const Link = preload("lib/link.gd")
const Codex = preload("lib/codex.gd")
const Keys = preload("lib/keys.gd")
const Prefs = preload("lib/prefs.gd")
const Media = preload("lib/media.gd")
const SESSION_TIMEOUT := 60.0

var debugger  # debugger.gd
var link = Link.new()
var run_active := false
var brain_steers := false  # the welcome's features had "steer": the user can steer a run while it plays
## What the agent server remembers about this project (its memory_state on this connection; memory_known false until
## one came, and again once the connection closes): the earlier runs it keeps, whether it has notes and code of its own
## for the game. Nothing at all: the next run is cold.
var memory_known := false
var memory_runs := 0
var memory_notes := false
var memory_lib := false
var memory_serial := -1  # the connection (connection_serial) the last memory_state came on
## The account (docs/protocol.md "Account"): brain_account, the welcome's features had "account" (it can sign in to
## Aura); account, its last account frame ({} until one came; kept after the connection closes).
var brain_account := false
var account := {}
var connection_serial := 0  # one more at every welcome
var _sign_in_on := -1  # the connection a sign-in still going was asked on (-1: none)

var _game_session := -1
var _game_ready := false
var _game_gen := 0
var _attach_at := 0.0
var _attach_until := 0.0
var _forwarded := {}  # request ids in flight to the game
var _pending := []  # session waits: {"tick": Callable(now) -> bool}
var _heartbeat_at := 0.0
var _detector_config := {}
var _codex_thread: Thread = null
var _codex_queue := []
var _summary_path := ""
var _last_played := ""
var _auto_started := false
var _record = null  # the brain's `record` start args while it records (kept across play sessions; null: not recording)
var _record_frames := 0  # frames passed on since that start
var _record_next := 0  # the next frame's number (a new play session goes on from it)
var _record_starts := {}  # request id -> args of the `record` starts forwarded to the game, until it answers


func _ready() -> void:
	link.on_open = _send_hello
	debugger.game_message.connect(_on_game_message)
	debugger.session_started.connect(_on_session_started)
	debugger.session_stopped.connect(_on_session_stopped)
	debugger.session_breaked.connect(_on_session_breaked)


func _exit_tree() -> void:
	link.close()
	if _codex_thread and _codex_thread.is_started():
		_codex_thread.wait_to_finish()


func _now() -> float:
	return Time.get_ticks_msec() / 1000.0


func status(text: String) -> void:
	status_changed.emit(text)


func is_connected_to_brain() -> bool:
	return link.connected and link.role != ""


func connect_to_brain(url: String, token: String, with_aura := false) -> void:
	link.open(url, token, with_aura)
	status("Connecting to the agent service…" if with_aura else "Connecting to %s…" % link.url)


func disconnect_from_brain() -> void:
	link.close()
	link.problem = ""  # (Disconnected by the user: no stale alert on the card)
	link.problem_code = 0
	run_active = false
	_detach()
	_forget_memory()
	status("Disconnected")
	connection_changed.emit(false)


## The connection closed: its memory_state isn't true any more (the agent service removed from this computer, say).
## The next connection's says again.
func _forget_memory() -> void:
	if not memory_known:
		return
	memory_known = false
	memory_runs = 0
	memory_notes = false
	memory_lib = false
	memory_changed.emit()


func _send_hello() -> void:
	var cfg := ConfigFile.new()
	var version := "1.0.0"
	if cfg.load("res://addons/autopilot/plugin.cfg") == OK:
		version = str(cfg.get_value("plugin", "version", version))
	var h := {"type": "hello", "protocol": Link.PROTOCOL, "plugin_version": version, "engine": "godot",
		"engine_version": Engine.get_version_info().string, "project": str(ProjectSettings.get_setting("application/config/name", "")),
		"host": "editor", "inspect": true,
		# the games it starts can be captured (a headless one answers record and capture with 409); `levels`, and
		# `session` playing the `map` asked for
		"features": ["media", "levels"],
		# whether the user shares runs' data (Autopilot/preferences.json; read again at every connection)
		"share": Prefs.share_data(Prefs.load_preferences())}
	var pid := str(ProjectSettings.get_setting("autopilot/project_id", ""))
	if pid != "":
		h.project_id = pid
	# The dock holds a run going (or one whose connection dropped): ask for the activity events it hasn't got
	# (docs/protocol.md, The activity feed; the agent service sends them after its welcome)
	var since: int = activity_since.call() if activity_since.is_valid() else -1
	if since >= 0:
		h.activity_since = since
		print("Autopilot: asking for activity since %d" % since)
	link.send(h)


## Set by the dock: the activity id its current run holds (0: none yet), or -1 to ask for no replay (a finished run's
## feed is there already). Each hello asks the agent service for the feed's events after it (`activity_since`).
var activity_since: Callable


func start_run(run: Dictionary) -> void:
	if not is_connected_to_brain():
		status("Not connected: connect first")
		return
	link.send({"type": "start", "run": run})
	run_active = true
	run_changed.emit(true)
	status("Run started")


func stop_run() -> void:
	link.send({"type": "stop"})
	status("Stopping…")


## Whether the user can steer the run going now (the agent server takes `steer`: its welcome said so).
func can_steer() -> bool:
	return is_connected_to_brain() and run_active and brain_steers


## What the user types to steer the run going now: it goes on from where the game is (see docs/protocol.md).
func steer_run(text: String) -> void:
	var said := text.strip_edges()
	if not can_steer() or said == "":
		return
	link.send({"type": "steer", "text": said})


func clear_memory() -> void:
	if not is_connected_to_brain():
		status("Not connected: connect first")
		return
	if run_active:
		status("A run is going: reset the memory once it is done")
		return
	link.send({"type": "clear_memory"})
	status("Resetting memory…")


# --- the account -----------------------------------------------------------------------------------

## Whether the agent service connected can sign in to Aura (its welcome said "account").
func can_sign_in() -> bool:
	return is_connected_to_brain() and brain_account


## Ask the agent service to sign in to Aura (it opens the browser; aura_sign_in frames follow). Whether it was sent.
func aura_sign_in() -> bool:
	if not (can_sign_in() and link.send({"type": "aura_sign_in"})):
		return false
	_sign_in_on = connection_serial
	return true


func aura_sign_in_cancel() -> bool:
	_sign_in_on = -1
	return can_sign_in() and link.send({"type": "aura_sign_in_cancel"})


## The dock let the sign-in go without cancelling it (the setup opened or finished): it isn't asked again.
func forget_sign_in() -> void:
	_sign_in_on = -1


## A sign-in asked on a connection that has closed since: its answer may have gone with it. The agent service keeps it
## going (asked again, it says where it is; started again, it starts one), or it ended while away: a kept sign-in says so.
func _resume_sign_in(frame: Dictionary) -> void:
	_sign_in_on = connection_serial
	var email = frame.get("email")
	if frame.get("signed_in") == true and typeof(email) == TYPE_STRING and email != "":  # (signed_in alone: maybe only the Aura app running)
		_sign_in_on = -1
		sign_in_frame.emit({"type": "aura_sign_in", "state": "signed_in", "email": email})
	else:
		link.send({"type": "aura_sign_in"})


## Forget the Aura sign-in on this computer (every editor here): an account frame follows.
func aura_sign_out() -> bool:
	return can_sign_in() and link.send({"type": "aura_sign_out"})


func request_account() -> bool:
	return can_sign_in() and link.send({"type": "account"})


func feedback(signature: String, verdict: String) -> void:
	link.send({"type": "feedback", "signature": signature, "verdict": verdict})


# --- the frame --------------------------------------------------------------------------------------

func _process(_delta: float) -> void:
	var now := _now()
	for frame in link.poll():
		_on_brain_frame(frame)
	# attach to a game that started, until it answers (its autoload may not be listening yet)
	if _game_session >= 0 and not _game_ready and now < _attach_until and now >= _attach_at:
		_attach_at = now + 0.5
		var cfg := {"detectors": _detector_config}
		if _record != null:
			cfg.record = _record.merged({"n": _record_next}, true)  # (the recording goes on in this play session)
		debugger.send(_game_session, "attach", [JSON.stringify(cfg)])
	var i := 0
	while i < _pending.size():
		if _pending[i].tick.call(now):
			_pending.remove_at(i)
		else:
			i += 1
	_tick_codex()
	if is_connected_to_brain() and not _game_ready and now - _heartbeat_at >= 1.0:
		_heartbeat_at = now
		link.send({"type": "events", "events": [{"kind": "heartbeat", "t": null, "fps": Engine.get_frames_per_second(), "paused": false}], "t": null})


func _on_brain_frame(frame: Dictionary) -> void:
	match str(frame.get("type", "")):
		"request":
			_on_request(frame)
		"welcome":
			link.role = str(frame.get("role", ""))
			connection_serial += 1
			brain_steers = false
			brain_account = false
			_record = null
			var features = frame.get("features", [])
			if typeof(features) == TYPE_ARRAY:
				for feature in features:
					if typeof(feature) == TYPE_STRING and feature == "steer":
						brain_steers = true
					elif typeof(feature) == TYPE_STRING and feature == "account":
						brain_account = true
			if link.role == "cli":
				run_active = true
				run_changed.emit(true)
			status("Connected to the agent service")
			connection_changed.emit(true)
			if brain_account:
				request_account()  # (every connection: what the dock shows of the account may be stale)
			# AUTOPILOT_PLAYTEST_START (a run as JSON, e.g. {"kind": "verify", "claim": "..."}): start it once connected, as
			# the dock's buttons would (an editor run without anyone at it: automation, a headless editor)
			var auto := OS.get_environment("AUTOPILOT_PLAYTEST_START")
			if auto != "" and not _auto_started and link.role == "service":
				var run = JSON.parse_string(auto)
				if typeof(run) == TYPE_DICTIONARY:
					_auto_started = true
					start_run.call_deferred(run)
			if _game_session >= 0:
				# a game already playing: attach to it again, for this brain
				_game_ready = false
				_attach_at = 0.0
				_attach_until = _now() + SESSION_TIMEOUT
		"run_started":
			# (docs/protocol.md, Runs: a run started from the user's own coding agent, shown as one the tab started)
			var run = frame.get("run", {})
			if not run_active and typeof(run) == TYPE_DICTIONARY:
				run_active = true
				run_started_elsewhere.emit(run)
				run_changed.emit(true)
		"run_kind":
			run_kind_received.emit(str(frame.get("kind", "")), frame.get("tested") == true)
		"status":
			server_status.emit(str(frame.get("text", "")))
			status(str(frame.get("text", "")))
		"activity":
			activity_received.emit(frame)  # (one event of the run's activity feed: the dock lists it under its stage)
		"jev":
			policy_step.emit(frame)
		"finding":
			var f = frame.get("finding", {})
			if typeof(f) == TYPE_DICTIONARY:
				finding_received.emit(f)
				status("Finding: %s" % f.get("summary", ""))
		"result":
			_on_result(frame)
		"memory_state":
			# What the agent server remembers about this project (docs/protocol.md): the Memory line the dock shows
			var n = frame.get("runs", 0)
			memory_runs = maxi(0, int(n)) if typeof(n) in [TYPE_INT, TYPE_FLOAT] else 0
			var notes = frame.get("notes", false)
			var lib = frame.get("lib", false)
			memory_notes = typeof(notes) == TYPE_BOOL and notes
			memory_lib = typeof(lib) == TYPE_BOOL and lib
			memory_known = true
			memory_serial = connection_serial
			memory_changed.emit()
		"usage":
			var models = frame.get("models", [])
			if typeof(models) == TYPE_ARRAY:
				usage_received.emit(models, frame.get("final", false) == true)
		"bill":
			bill_received.emit(frame)
		"account":
			account = frame
			if _sign_in_on >= 0 and _sign_in_on != connection_serial:
				_resume_sign_in(frame)
			account_changed.emit()
		"aura_sign_in":
			if str(frame.get("state", "")) in ["signed_in", "error"]:
				_sign_in_on = -1
			sign_in_frame.emit(frame)
		"memory_cleared":
			var ok: bool = frame.get("ok", false) == true
			if ok:
				status("Memory reset successful")
				# (nothing remembered any more: the Memory row says empty until the next memory_state)
				memory_known = true
				memory_runs = 0
				memory_notes = false
				memory_lib = false
				memory_serial = connection_serial
				memory_changed.emit()
			else:
				status("Memory not reset: %s" % frame.get("message", ""))
			memory_cleared.emit(ok, str(frame.get("message", "")))
		"error":
			link.problem = str(frame.get("message", ""))
			link.problem_code = int(frame.get("code", 0))
			status(link.problem)
			link.close()
			_forget_memory()
			connection_changed.emit(false)
		"_closed":
			run_active = false
			run_changed.emit(false)
			_record = null  # (a recording never outlives its connection)
			_record_starts.clear()
			_detach()
			_forget_memory()
			if frame.get("was_connected", false):
				connection_changed.emit(false)
			status(link.problem)


func _on_result(frame: Dictionary) -> void:
	run_active = false
	run_changed.emit(false)
	var result: Dictionary = frame.get("result", {}) if typeof(frame.get("result")) == TYPE_DICTIONARY else {}
	# (what the run left in the agent's memory and its recording, or why it has none: kept with the result, whichever of
	# the two carries them)
	for key in ["memory", "recording"]:
		if typeof(frame.get(key)) == TYPE_DICTIONARY and not result.has(key):
			result[key] = frame[key]
	if typeof(frame.get("no_recording")) == TYPE_STRING and not result.has("no_recording"):
		result["no_recording"] = frame["no_recording"]
	var stamp := Time.get_datetime_string_from_system().replace(":", "").replace("-", "").replace("T", "-")
	var dir := ProjectSettings.globalize_path("user://playtest/" + stamp)
	DirAccess.make_dir_recursive_absolute(dir)
	var files = frame.get("files", {})
	if typeof(files) == TYPE_DICTIONARY:
		for name in files:
			var clean := str(name).get_file()
			if clean == "" or clean == "." or clean == "..":
				continue
			var f := FileAccess.open(dir.path_join(clean), FileAccess.WRITE)
			if f:
				f.store_string(str(files[name]))
	var rf := FileAccess.open(dir.path_join("result.json"), FileAccess.WRITE)
	if rf:
		rf.store_string(JSON.stringify(result, "  "))
	status("Run finished. Report: %s" % dir)
	result_received.emit(result, dir)


# --- requests from the brain ------------------------------------------------------------------------

func _respond(id, result) -> void:
	link.send({"type": "response", "id": id, "ok": true, "result": result, "t": null})


func _respond_error(id, error: String, code := 400) -> void:
	link.send({"type": "response", "id": id, "ok": false, "status": code, "error": error, "t": null})


func _on_request(frame: Dictionary) -> void:
	var id = frame.get("id")
	if typeof(id) == TYPE_FLOAT:
		id = int(id)
	var op := str(frame.get("op", ""))
	var args = frame.get("args", {})
	if typeof(args) != TYPE_DICTIONARY:
		args = {}
	match op:
		"session":
			_session(id, args)
			return
		"levels":
			_respond(id, _levels())
			return
		"codex_manifest", "codex_read":
			_codex_queue.append({"id": id, "op": op, "args": args})
			return
		"detectors":
			_detector_config = args
		"record":
			var action := str(args.get("action", ""))
			if action == "stop":
				# (always succeeds; answered here, the recording spanning play sessions)
				_respond(id, {"recording": false, "frames": _record_frames if _record != null else 0})
				_record = null
				if _game_ready:
					debugger.send(_game_session, "record_stop", [])
				return
			if action != "start":
				_respond_error(id, "record wants action start or stop")
				return
			if not _game_ready:
				_respond_error(id, "no picture: no game is running", 409)
				return
			_record_starts[id] = args
		"capture":
			if not _game_ready:
				_respond_error(id, "no picture: no game is running", 409)
				return
	if _game_ready and debugger.send(_game_session, "request", [JSON.stringify(frame)]):
		_forwarded[id] = true
		return
	match op:
		"context":
			_respond(id, _editor_context())
		"ping":
			_respond(id, {"game_time": null, "wall_time": Time.get_unix_time_from_system()})
		"detectors":
			_respond(id, {})
		_:
			_respond_error(id, "No game is running (start one with session start)", 409)


func _editor_context() -> Dictionary:
	var controls := []
	for p in ProjectSettings.get_property_list():
		var n: String = p.name
		if not n.begins_with("input/") or Keys.is_default_ui(n.substr(6)):
			continue
		var v = ProjectSettings.get_setting(n)
		var keys := []
		if typeof(v) == TYPE_DICTIONARY:
			for ev in v.get("events", []):
				if ev is InputEvent:
					var k := Keys.unreal_name(ev)
					if k != "" and not keys.has(k):
						keys.append(k)
		controls.append({"name": n.substr(6), "action": n.substr(6), "keys": keys, "digital": true})
	return {"world": "", "scene": "", "character": "", "controller": "", "input_subsystem": "", "camera_manager": "", "camera": "",
		"game_name": str(ProjectSettings.get_setting("application/config/name", "")), "host": "editor", "engine": "godot",
		"session_running": false, "paused": false, "game_time": 0.0, "project_dir": ProjectSettings.globalize_path("res://"),
		"controls": controls}


# --- the play session -------------------------------------------------------------------------------

func _find_scene(name: String) -> String:
	if name.begins_with("res://") or name.begins_with("uid://"):
		return name if ResourceLoader.exists(name) else ""
	var want := name.get_file()
	if not want.ends_with(".tscn") and not want.ends_with(".scn"):
		want += ".tscn"
	var found := []
	Codex._walk("res://", "", found)
	for f in found:
		if str(f).get_file().to_lower() == want.to_lower():
			return "res://" + f
	return ""


## `levels` (docs/protocol.md): the project's scenes, what a start without a map plays (the main scene) and the one
## playing.
func _levels() -> Dictionary:
	var found := []
	Codex._walk("res://", "", found)
	var levels := []
	for f in found:
		var path := "res://" + str(f)
		if path.get_extension().to_lower() in ["tscn", "scn"]:
			levels.append(path)
	levels.sort()
	var playing := EditorInterface.get_playing_scene() if EditorInterface.is_playing_scene() else ""
	return {"levels": levels, "open": str(ProjectSettings.get_setting("application/run/main_scene", "")), "playing": playing}


func _play(scene: String) -> void:
	var main := str(ProjectSettings.get_setting("application/run/main_scene", ""))
	if scene == "" or scene == main:
		EditorInterface.play_main_scene()
	else:
		EditorInterface.play_custom_scene(scene)


func _session(id, args: Dictionary) -> void:
	var action := str(args.get("action", "status"))
	var scene := ""
	if str(args.get("map", "")) != "":
		scene = _find_scene(str(args.map))
		if scene == "":
			_respond_error(id, "No scene %s" % args.map)
			return
	match action:
		"start":
			if _game_ready:
				_respond(id, {"running": true})
				return
			if not EditorInterface.is_playing_scene():
				_play(scene)
				_last_played = scene  # (what a restart without a map plays again)
			_wait_ready(id, _game_gen)
		"stop":
			if not EditorInterface.is_playing_scene():
				_respond(id, {"running": false})
				return
			EditorInterface.stop_playing_scene()
			var started := _now()
			_pending.append({"tick": func(now: float) -> bool:
				if not EditorInterface.is_playing_scene():
					_respond(id, {"running": false})
					return true
				if now - started > SESSION_TIMEOUT:
					_respond_error(id, "Timed out waiting for the play session to stop", 500)
					return true
				return false})
		"restart":
			# As Play In Editor's restart: a fresh game process (autoloads and all), with the scene played before
			# (or the one asked for)
			var target := scene
			if target == "":
				target = EditorInterface.get_playing_scene() if EditorInterface.is_playing_scene() else _last_played
			_last_played = target
			var gen := _game_gen
			var phase := {"stopping": EditorInterface.is_playing_scene()}
			if phase.stopping:
				EditorInterface.stop_playing_scene()
			else:
				_play(target)
			var started := _now()
			_pending.append({"tick": func(now: float) -> bool:
				if phase.stopping:
					if not EditorInterface.is_playing_scene():
						phase.stopping = false
						_play(target)
					elif now - started > SESSION_TIMEOUT:
						_respond_error(id, "Timed out waiting for the play session to stop", 500)
						return true
					return false
				if _game_ready and _game_gen > gen:
					_respond(id, {"running": true})
					return true
				if now - started > SESSION_TIMEOUT:
					_respond_error(id, "Timed out waiting for the play session", 500)
					return true
				return false})
		_:
			_respond(id, {"running": _game_ready})


func _wait_ready(id, gen: int) -> void:
	var started := _now()
	_pending.append({"tick": func(now: float) -> bool:
		if _game_ready:
			_respond(id, {"running": true})
			return true
		if now - started > SESSION_TIMEOUT:
			_respond_error(id, "Timed out waiting for the play session", 500)
			return true
		return false})


# --- the game (over the debugger) -------------------------------------------------------------------

func _on_session_started(session_id: int) -> void:
	_game_session = session_id
	_game_ready = false
	_attach_at = 0.0
	_attach_until = _now() + SESSION_TIMEOUT
	if EditorInterface.is_playing_scene():
		_last_played = EditorInterface.get_playing_scene()


func _on_session_stopped(session_id: int) -> void:
	if session_id != _game_session:
		return
	var was := _game_ready
	_game_ready = false
	_game_session = -1
	for id in _forwarded:
		_respond_error(id, "the game stopped", 500)
	_forwarded.clear()
	if was and is_connected_to_brain():
		link.send({"type": "events", "events": [{"kind": "session", "t": null, "running": false}], "t": null})


func _on_session_breaked(session_id: int) -> void:
	# A script error stopped the game in the debugger. While the brain plays, it goes on (the error is reported to the
	# brain as a log event and a log_error finding); otherwise it stays stopped for the developer.
	if is_connected_to_brain() and session_id == _game_session:
		debugger.resume(session_id)


func _detach() -> void:
	if _game_session >= 0:
		debugger.send(_game_session, "detach", [])


func _on_game_message(message: String, data: Array, session_id: int) -> void:
	if session_id != _game_session:
		return
	match message:
		"ready":
			if not _game_ready:
				_game_ready = true
				_game_gen += 1
				if is_connected_to_brain():
					link.send({"type": "events", "events": [{"kind": "session", "t": 0.0, "running": true}], "t": 0.0})
				else:
					_detach()  # nobody to play for: let the game be
		"frame":
			if data.is_empty():
				return
			var text := str(data[0])
			var frame = JSON.parse_string(text)
			if typeof(frame) == TYPE_DICTIONARY:
				if frame.get("type") == "response":
					var rid = frame.get("id")
					rid = int(rid) if typeof(rid) == TYPE_FLOAT else rid
					_forwarded.erase(rid)
					if _record_starts.has(rid):
						if frame.get("ok", false):
							_record = _record_starts[rid]
							_record_frames = 0
							_record_next = 0
						_record_starts.erase(rid)
				elif frame.get("type") == "events":
					for e in frame.get("events", []):
						if typeof(e) == TYPE_DICTIONARY and e.get("kind") == "finding" and typeof(e.get("finding")) == TYPE_DICTIONARY:
							finding_received.emit(e.finding)
			if is_connected_to_brain():
				link.send_text(text)
		"media":
			_on_game_media(data)


## A picture from the game: [head as JSON, the JPEG's bytes]. A recording's frame becomes a `frame` message, a still
## the answer to its `capture` (the base64 and the text are made here, in the editor, not in the game).
func _on_game_media(data: Array) -> void:
	if data.size() < 2 or typeof(data[1]) != TYPE_PACKED_BYTE_ARRAY or not is_connected_to_brain():
		return
	var head = JSON.parse_string(str(data[0]))
	if typeof(head) != TYPE_DICTIONARY:
		return
	var b64 := Marshalls.raw_to_base64(data[1])
	match str(head.get("kind", "")):
		"frame":
			if _record == null:
				return  # (stopped meanwhile)
			var n := int(head.get("n", 0))
			_record_frames += 1
			_record_next = maxi(_record_next, n + 1)
			link.send_text(Media.frame_text({"n": n, "t": head.get("t"), "wall": head.get("wall", 0.0),
				"width": int(head.get("width", 0)), "height": int(head.get("height", 0))}, b64))
		"still":
			var id = head.get("id")
			id = int(id) if typeof(id) == TYPE_FLOAT else id
			_forwarded.erase(id)
			link.send_text(Media.response_text(id, {"width": int(head.get("width", 0)), "height": int(head.get("height", 0)),
				"t": head.get("t")}, b64, head.get("t")))


# --- codex (off the editor's main thread) -----------------------------------------------------------

func _tick_codex() -> void:
	if _codex_thread != null:
		if _codex_thread.is_alive():
			return
		var done: Dictionary = _codex_thread.wait_to_finish()
		_codex_thread = null
		if done.has("summary"):
			_summary_path = done.summary
		_respond(done.id, done.result)
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
