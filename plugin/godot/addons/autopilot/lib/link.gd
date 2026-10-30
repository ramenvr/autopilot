## The connection out to the brain: one WebSocket (WebSocketPeer) carrying JSON text frames (docs/protocol.md).
## Polled every frame by its owner; reconnects with a backoff (1 s doubling to 15 s) while wanted.
## The address is looked up again at every attempt when it isn't given (the agent service running on this machine
## announces where it listens, Autopilot/brain.json: one that restarts may have another port), and with Aura (Use with
## Aura) it is always the agent service the Aura app announces (Autopilot/aura-brain.json), never brain.json's.
extends RefCounted

const Prefs = preload("prefs.gd")
const PROTOCOL := 1
const DEFAULT_URL := "ws://127.0.0.1:8765/v1/bridge"

var url := ""
var token := ""
var ws: WebSocketPeer = null
var want := false
var connected := false  # the socket is open
var role := ""  # the brain's role from its welcome ("" until welcomed)
var problem := ""  # what went wrong last, in words (shown to the user)
var problem_code := 0  # the code of the error frame the brain refused with (0: none)
var backoff := 1.0
var next_try := 0.0
var on_open: Callable  # called when the socket opens (send the hello)
var with_aura := false  # Use with Aura: connect to the agent service the Aura app runs
var auto_url := false  # no address given: the agent service this machine announces, else DEFAULT_URL


## The bearer token to send: the given one (the dock's setting, or --playtest-token), else AUTOPILOT_PLAYTEST_TOKEN, else
## "" (connect without an Authorization header).
static func find_token(given: String) -> String:
	if given != "":
		return given
	return OS.get_environment("AUTOPILOT_PLAYTEST_TOKEN")


func open(new_url: String, new_token: String, aura := false) -> void:
	with_aura = aura
	auto_url = not aura and (new_url == "" or new_url == DEFAULT_URL)
	url = new_url if new_url != "" else DEFAULT_URL
	token = new_token
	want = true
	backoff = 1.0
	problem = ""
	problem_code = 0
	_connect()


func _connect() -> void:
	close_socket()
	if with_aura:
		url = Prefs.aura_service_url()
		if url == "":  # (Aura's agent service isn't running: try again soon, it may be starting)
			problem = ("Aura isn’t running on this machine. Autopilot connects once it starts."
				if Prefs.aura_installed() else "Aura isn’t installed on this machine.")
			problem_code = 0
			next_try = Time.get_ticks_msec() / 1000.0 + 2.0
			return
	elif auto_url:
		var announced := Prefs.announced_url(Prefs.OWN_BRAIN_FILE)
		url = announced if announced != "" else DEFAULT_URL
	ws = WebSocketPeer.new()
	ws.inbound_buffer_size = 16 * 1024 * 1024
	ws.outbound_buffer_size = 64 * 1024 * 1024
	ws.max_queued_packets = 16384
	var headers := PackedStringArray()
	var t := find_token(token)
	if t != "":
		headers.append("Authorization: Bearer " + t)
	ws.handshake_headers = headers
	var err := ws.connect_to_url(url)
	next_try = INF
	if err != OK:
		problem = "Can’t connect to %s (error %d)" % [url, err]
		_schedule()


func close_socket() -> void:
	if ws:
		ws.close()
	ws = null
	connected = false
	role = ""


func close() -> void:
	want = false
	close_socket()


func _schedule() -> void:
	ws = null
	connected = false
	role = ""
	if want:
		next_try = Time.get_ticks_msec() / 1000.0 + backoff
		backoff = minf(backoff * 2.0, 15.0)


## Poll the socket; returns the frames that came in (parsed dictionaries).
func poll() -> Array:
	var out := []
	if ws == null:
		if want and Time.get_ticks_msec() / 1000.0 >= next_try:
			_connect()
		return out
	ws.poll()
	var state := ws.get_ready_state()
	if state == WebSocketPeer.STATE_OPEN and not connected:
		connected = true
		if problem_code == 0:
			problem = ""
		if on_open.is_valid():
			on_open.call()
	# Every frame that came in, whatever the state now: a brain that sends its last frame (a run's result) and closes
	# at once has both arrive in one poll, and the frame must not be lost with the connection
	while ws.get_available_packet_count() > 0:
		var text := ws.get_packet().get_string_from_utf8()
		var frame = JSON.parse_string(text)
		if typeof(frame) == TYPE_DICTIONARY:
			if frame.get("type") == "welcome":
				# (not on open: a brain that takes the connection and closes it unwelcomed, refusing it, is tried
				# again with the backoff growing, not every second)
				backoff = 1.0
			out.append(frame)
	if state == WebSocketPeer.STATE_CLOSED:
		var was := connected
		if not was and problem_code == 0:
			problem = "Can’t reach the agent at %s. Trying again…" % url
		elif was:
			problem = "Connection closed (%d %s)" % [ws.get_close_code(), ws.get_close_reason()]
		_schedule()
		out.append({"type": "_closed", "was_connected": was})
	return out


func send(frame: Dictionary) -> bool:
	if ws == null or not connected:
		return false
	return ws.send_text(JSON.stringify(frame)) == OK


func send_text(text: String) -> bool:
	if ws == null or not connected:
		return false
	return ws.send_text(text) == OK
