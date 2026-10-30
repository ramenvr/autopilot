## The dock's new pages built and driven without an editor or a game (headless, run by units.py): the first-run setup
## (its stepper; BYOK keys to autopilot.env; the agent service started, installed or missing, with a stand-in launcher that
## runs nothing; the Aura sign-in driven by aura_sign_in frames; the Aura app; Finish to preferences.json), usage and bill frames
## kept on the run going on, the Settings page with its Account corner (account frames, the Aura bill, out of Aura credits) and Data sharing
## box, the footer, and the settings. The link to the agent service is a stand-in that keeps what is sent (no socket). The editor bridge is the
## real one, over a stand-in debugger; the user settings folder is
## UNITS_DIR (a temporary folder).
extends SceneTree

const Prefs = preload("res://addons/autopilot/lib/prefs.gd")
const EditorBridge = preload("res://addons/autopilot/editor_bridge.gd")
const Dock = preload("res://addons/autopilot/dock.gd")
const Link = preload("res://addons/autopilot/lib/link.gd")
const SettingsPage = preload("res://addons/autopilot/ui/settings_page.gd")
const Model = preload("res://addons/autopilot/ui/model.gd")
const History = preload("res://addons/autopilot/ui/history.gd")

var failed := 0
var passed := 0
## The data sharing text, in the setup's Privacy settings and on the Settings page alike
const CONSENT := ("Enabling this sends recordings, screenshots, and agent traces and memories to the developers of Autopilot."
	+ " For runs from your own Claude Code or Codex, the agent traces are its conversation during the run.")


class FakeDebugger:
	extends RefCounted
	signal game_message(message: String, data: Array, session_id: int)
	signal session_started(session_id: int)
	signal session_stopped(session_id: int)
	signal session_breaked(session_id: int)

	func send(_session_id: int, _message: String, _data: Array) -> bool:
		return false

	func resume(_session_id: int) -> void:
		pass


func check(name: String, ok: bool, detail = "") -> void:
	if ok:
		passed += 1
		print("PASS  %s" % name)
	else:
		failed += 1
		print("FAIL  %s  [%s]" % [name, str(detail)])


func _count(node: Node, cls: String) -> int:
	var n := 0
	for c in node.find_children("*", cls, true, false):
		if c.is_visible_in_tree():
			n += 1
	return n


## The child of parent that node is under (or is).
func _holding(parent: Node, node: Node) -> Node:
	while node != null and node.get_parent() != parent:
		node = node.get_parent()
	return node


## Whether the Settings plate titled `title` (mono caps) is laid out as every setting is: the title, then `subtitle`
## (in the sharing text's style), then the control (a `control_class` under it), TITLE_GAP and CONTROL_GAP apart.
func _setting_plate(page: Node, title: String, subtitle: String, control_class: String) -> bool:
	for l in page.find_children("*", "Label", true, false):
		if l.text != title.to_upper() or l.theme_type_variation != "AutopilotCaps":
			continue
		var v: Node = l.get_parent()
		if not v is VBoxContainer or v.get_child_count() != 3 or v.get_child(0) != l:
			return false
		var sub: Node = v.get_child(1)
		var control: Node = v.get_child(2)
		return (sub is Label and sub.text == subtitle and sub.theme_type_variation == "AutopilotSmall"
			and v.get_theme_constant("separation") == HT.px(SettingsPage.TITLE_GAP)
			and control is MarginContainer
			and control.get_theme_constant("margin_top") == HT.px(SettingsPage.CONTROL_GAP - SettingsPage.TITLE_GAP)
			and not control.find_children("*", control_class, true, false).is_empty())
	return false


func _texts(node: Node) -> String:
	var out := []
	for c in node.find_children("*", "Label", true, false):
		out.append(c.text)
	for c in node.find_children("*", "Button", true, false):
		out.append(c.text)
	return "\n".join(out)


## Whether a label or button under node names the BYOK mode "Own keys" (a button saying it anywhere, a label starting
## with it): the mode is BYOK everywhere (a sentence may still say "your own keys").
func _says_own_keys(node: Node) -> bool:
	for c in node.find_children("*", "Button", true, false):
		if "own keys" in c.text.to_lower():
			return true
	for c in node.find_children("*", "Label", true, false):
		if c.text.strip_edges().to_lower().begins_with("own keys"):
			return true
	return false


var bridge
var dock
var frames := 0


func _initialize() -> void:
	bridge = EditorBridge.new()
	bridge.debugger = FakeDebugger.new()
	root.add_child(bridge)
	dock = Dock.new()
	dock.bridge = bridge
	dock.size = Vector2(420, 700)
	root.add_child(dock)


## The checks run on the second frame (the nodes are ready by then); a script error stops them, and the watchdog
## ends the run as failed.
func _process(_delta: float) -> bool:
	frames += 1
	if frames == 2:
		run_checks()
	elif frames > 6000:
		print("FAIL  the checks didn't finish (a script error above)")
		quit(1)
	return false


## A stand-in for the link to the agent service: keeps what the dock sends, opens nothing.
class FakeLink:
	extends "res://addons/autopilot/lib/link.gd"
	var sent := []
	var opened := []

	func open(new_url: String, new_token: String, aura := false) -> void:
		want = true
		with_aura = aura
		url = new_url if new_url != "" else DEFAULT_URL
		token = new_token
		opened.append(url)

	func close() -> void:
		want = false
		connected = false
		role = ""

	func poll() -> Array:
		return []

	func send(frame: Dictionary) -> bool:
		if not connected:
			return false
		sent.append(frame)
		return true

	func types() -> Array:
		return sent.map(func(f): return f.get("type"))


## Press the dock's gear (the Settings button) shown now; whether there was one.
func press_gear() -> bool:
	for b in dock._gears:
		if b.is_visible_in_tree():
			b.pressed.emit()
			return true
	return false


## The setup shows no keys card at all (while the sign-in gets ready, waits or needs the agent service): neither its
## name nor its Show, nor its fields.
func _no_keys_card() -> bool:
	for b in dock._setup.find_children("*", "Button", true, false):
		if b.text in ["Show", "Use my keys"] and b.is_visible_in_tree():
			return false
	return not "bring your own keys" in setup_text().to_lower()


## Step 1 left without choosing an account (step 1 has no button for it any more): on to step 3, access unchanged.
func skip_account() -> void:
	dock._setup.access = ""
	dock._setup.use_aura = false
	dock._setup._go(3)


## Press the visible button with this text (as shown) under node; whether there was one.
func press(node: Node, text: String) -> bool:
	for b in node.find_children("*", "Button", true, false):
		if b.text == text and b.is_visible_in_tree() and not b.disabled:
			b.pressed.emit()
			return true
	return false


## Press the button with this text under node that is shown there (node's own descendants visible, the node itself
## maybe not in view: the Memory row in a narrow dock's closed history list).
func _press_shown(node: Node, text: String) -> bool:
	var b := _button_in(node, text)
	if b == null or b.disabled:
		return false
	b.pressed.emit()
	return true


func _button_in(node: Node, text: String) -> Button:
	for c in node.find_children("*", "Button", true, false):
		if c.text != text:
			continue
		var shown := true
		var n: Node = c
		while n != null and n != node:
			if n is CanvasItem and not n.visible:
				shown = false
			n = n.get_parent()
		if shown:
			return c
	return null


## The Settings page's Delete Autopilot's memories box (the removal asking), or null.
func _delete_box() -> CheckBox:
	for c in dock._settings_page.find_children("*", "CheckBox", true, false):
		if c.text == SettingsPage.DELETE_MEMORY:
			return c
	return null


## Connected to an agent service (the stand-in link), welcomed with these features; returns the types of the frames
## the welcome made the dock send (then forgotten: the checks after it see only their own).
func welcome(features: Array) -> Array:
	bridge.link.connected = true
	var before: int = bridge.link.sent.size() if "sent" in bridge.link else 0
	bridge._on_brain_frame({"type": "welcome", "role": "service", "features": features})
	if not "sent" in bridge.link:
		return []
	var types: Array = bridge.link.sent.slice(before).map(func(f): return f.get("type"))
	bridge.link.sent.clear()
	return types


func unwelcome() -> void:
	bridge.link.close()
	bridge._on_brain_frame({"type": "_closed", "was_connected": true})


func setup_text() -> String:
	dock._setup.refresh()
	return _texts(dock._setup)


## The setup's Finish and, when it went on to step 4 (First run: the memory not known to have something), Skip: the
## dock's usual page.
func finish(access := "", share := true, use_aura := false) -> void:
	dock._on_setup_finished(access, share, use_aura)
	if dock.onboarding:
		dock.close_setup()


func run_checks() -> void:
	var real_link = bridge.link
	var fake := FakeLink.new()
	bridge.link = fake  # (no socket is opened by any check below)

	check("headless: no first-run setup", not dock.onboarding and dock._body.visible)
	check("headless: the agent service isn't started", not dock.auto_start_allowed)
	check("headless: not connected by itself", not dock.connects_by_itself() and not fake.want)
	dock.auto_start_allowed = true
	check("an editor with a window: connected by itself, no Connect to press", dock.connects_by_itself())
	dock.auto_start_allowed = false
	var adv := _texts(dock._connect_page)
	check("Advanced: the address, the token and the command, no more", "AGENT SERVICE URL" in adv and "ACCESS TOKEN · OPTIONAL" in adv
		and "AGENT SERVICE COMMAND · OPTIONAL" in adv and not "Connect when the editor starts" in adv
		and not "Start the agent service automatically" in adv and not "Use with Aura" in adv and not "EXPORT" in adv.to_upper()
		and dock._svc_cmd != null and dock.service_command() == "" and dock._svc_cmd.placeholder_text == "Detected automatically", adv)
	dock._svc_cmd.text = "py -m autopilot"
	dock._svc_cmd.text_changed.emit("py -m autopilot")
	check("the command setting reaches the agent service", dock.service.command_setting == "py -m autopilot" and dock.service_command() == "py -m autopilot")
	dock.set_service_command("")

	dock._open_setup()
	dock._render()
	check("setup shown in place of the rest", dock._setup.visible and not dock._body.visible)
	var t := setup_text()
	check("setup step 1: the Aura card, the BYOK card folded, no Decide later", "Welcome to Autopilot" in t
		and "SIGN IN WITH AURA" in t and "Create Account" in t and "Sign in" in t
		and "Autopilot is maintained by the developers behind Aura, the multiagent AI solution to Unreal and Unity game development. You can use your Aura subscription to run Autopilot. New users get free Aura credits." in t
		and not "Billed to your Aura plan." in t and "BRING YOUR OWN KEYS (BYOK)" in t and "Show" in t
		and not "DECISION PROVIDER API KEY" in t and not "Use my keys" in t and not "Decide later" in t, t)
	check("Show opens the BYOK card", press(dock._setup, "Show"))
	t = setup_text()
	check("setup step 1", "Welcome to Autopilot" in t and "DECISION PROVIDER API KEY" in t and "CODING AGENT" in t
		and "LLM PROVIDER API KEY" in t and "Use my keys" in t and "Hide" in t and not "Decide later" in t
		and not "Not sure yet" in t and not "Choose how to run it" in t and not "learns your game" in t
		and not "Leave empty" in t, t)
	check("step 1: Bring your own keys (BYOK), no \"own API keys\"", "BRING YOUR OWN KEYS (BYOK)" in t and not "OWN API KEYS" in t.to_upper() and not _says_own_keys(dock._setup), t)
	check("stepper: 1 ACCOUNT, 2 AGENT SERVICE, 3 PRIVACY SETTINGS", "1 ACCOUNT\n2 AGENT SERVICE\n3 PRIVACY SETTINGS" in t, t)
	check("key placeholders", dock._setup._typesafe.placeholder_text == "Optional"
		and dock._setup._llm_key.placeholder_text == "Optional")
	check("the LLM provider dropdown, Anthropic picked", dock._setup._provider != null and dock._setup._provider.is_visible_in_tree()
		and dock._setup._provider.selected == 0 and dock._setup._provider.get_item_text(0) == "Anthropic" and not dock._setup._provider.disabled)
	check("the Coding Agent dropdown, OpenCode picked", dock._setup._agent != null and dock._setup._agent.is_visible_in_tree()
		and dock._setup._agent.get_item_text(dock._setup._agent.selected) == "OpenCode" and not dock._setup._agent.disabled)
	check("the order: decision key, Coding Agent, LLM key", "DECISION PROVIDER API KEY\nCODING AGENT\nLLM PROVIDER API KEY" in t, t)
	check("no design notes in the setup", not ("TIMED OUT" in t or "VARIANT" in t or "you@studio.com" in t or "STEP 1" in t
		or "ONE PER CODING AGENT" in t or "LIST OPEN" in t or "ANY OF SIX" in t), t)
	dock._setup._typesafe.text = "ts-key"
	dock._setup._llm_key.text = ""
	check("Use my keys", press(dock._setup, "Use my keys"))
	check("BYOK: autopilot.env written (non-empty keys only, the agent and the provider)", FileAccess.get_file_as_string(Prefs.file(Prefs.ENV_FILE))
		== "TYPESAFE_API_KEY=ts-key\nAUTOPILOT_AGENT=opencode\nAUTOPILOT_OPENCODE_PROVIDER=anthropic\n",
		FileAccess.get_file_as_string(Prefs.file(Prefs.ENV_FILE)).c_escape())
	check("BYOK: step 2", dock._setup.step == 2 and dock._setup.access == "own_keys")
	t = setup_text()
	check("stepper: 1 ACCOUNT · BYOK", "1 ACCOUNT · BYOK" in t, t)
	check("step 2, the agent service not started here (headless): waiting for it, Continue disabled until connected", "Set up the agent service" in t
		and "Waiting for the agent service: start it with autopilot serve." in t and not "runs the agent on this computer" in t
		and _button_in(dock._setup, "Continue") != null and _button_in(dock._setup, "Continue").disabled, t)

	# step 2 with the agent service started (a stand-in launcher: nothing runs)
	var launched := []
	var alive := {"yes": true}
	var autopilot_exe := OS.get_environment("UNITS_DIR").path_join("bin").path_join("autopilot.exe")
	DirAccess.make_dir_recursive_absolute(autopilot_exe.get_base_dir())
	FileAccess.open(autopilot_exe, FileAccess.WRITE).close()
	dock.auto_start_allowed = true
	dock.service.candidates = [autopilot_exe]
	dock.service.installer_dir = OS.get_environment("UNITS_DIR").path_join("agent_service")
	dock.service.launcher = func(program: String, args: PackedStringArray) -> int:
		launched.append([program, args])
		return 4321
	dock.service.running = func(_p: int) -> bool: return alive.yes
	dock.service.exit_code = func(_p: int) -> int: return 0
	dock._setup._go(2)
	t = setup_text()
	check("step 2: starting", dock.service.state == "starting" and "Starting the agent service…" in t and launched.size() == 1, [dock.service.state, t])
	check("the start command: autopilot serve --port 0 --exit-with this editor", " ".join(launched[0][1]).contains(autopilot_exe.replace("/", "\\") if OS.get_name() == "Windows" else autopilot_exe)
		and " ".join(launched[0][1]).contains("serve --port 0 --exit-with %d" % OS.get_process_id()), launched)
	check("no captions around the progress line", not ("ONCE PER COMPUTER" in t or "A FEW SECONDS" in t or "THEN · STARTING" in t), t)
	var url := "ws://127.0.0.1:1/v1/bridge"
	var bj := Prefs.file(Prefs.OWN_BRAIN_FILE)
	DirAccess.make_dir_recursive_absolute(bj.get_base_dir())
	var f := FileAccess.open(bj, FileAccess.WRITE)
	# (announced since the start by another process than the shell started: this one, so that it is running)
	f.store_string(JSON.stringify({"url": url, "pid": OS.get_process_id(), "started": Time.get_unix_time_from_system()}))
	f.close()
	dock.service._poll_at = 0.0
	dock.service.poll()
	t = setup_text()
	check("step 2: ready, with its address", "Agent service ready" in t and "Starts with your editor" in t
		and not "It uses the keys" in t and url in t and "Continue" in t, t)
	check("ready: the dock connects to it", fake.opened.size() == 1 and fake.opened[0] == Link.DEFAULT_URL and fake.want, fake.opened)
	check("Continue", press(dock._setup, "Continue") and dock._setup.step == 3)
	t = setup_text()
	check("step 3: privacy settings, the recap", "PRIVACY SETTINGS" in t and "Share my runs to improve Autopilot" in t
		and "Enabling this sends recordings, screenshots, and agent traces and memories to the developers of Autopilot." in t
		and not "code export" in t and not "text export" in t and not "Ramen VR" in t and not "train" in t
		and dock._setup.recap()[0] == "BYOK" and not "own API keys" in t and "\nReady\n" in t and "Back" in t, t)
	check("step 3, the memory not known: Next, not Finish; the stepper's 4 FIRST RUN", "Next" in t and not "Finish" in t
		and dock._setup.stepper_labels() == ["1 ACCOUNT · BYOK", "2 AGENT SERVICE", "3 PRIVACY SETTINGS", "4 FIRST RUN"], [t, dock._setup.stepper_labels()])
	check("Back from step 3 goes to step 2", press(dock._setup, "Back") and dock._setup.step == 2)
	press(dock._setup, "Continue")
	dock._setup.share = false
	check("Next", press(dock._setup, "Next"))
	var p := Prefs.load_preferences()
	check("Next writes preferences.json", p.get("onboarded") == true and p.get("share_data") == false and p.get("access") == "own_keys"
		and p.get("version") == 1, p)
	t = setup_text()
	check("Next: step 4 waits for the agent service, Skip", dock.onboarding and dock._setup.step == 4 and dock._setup.visible
		and "Let Autopilot learn your game" in t and ("Connecting to the agent service…" in t or "Checking what Autopilot remembers about this game…" in t)
		and "Skip" in t and not "Explore and finish" in t and not "Back" in t, t)
	check("Skip shows the dock", press(dock._setup, "Skip") and not dock.onboarding and dock._body.visible and not dock._setup.visible)
	fake.close()

	# the agent service stopped before it was ready; not installed, with and without the installer
	var announced := FileAccess.get_file_as_string(bj)
	DirAccess.remove_absolute(bj)
	dock.service.state = "idle"
	dock._connect()
	check("Connect starts the agent service, the connect card says so", dock.service.state == "starting" and dock._conn_state() == "service"
		and not fake.want and dock._cp_title.text == "Starting the agent service…" and dock.connect_buttons() == ["Stop"],
		[dock.service.state, dock._cp_title.text, dock.connect_buttons()])
	var head := _texts(dock._connect_page)
	check("the connect card: Agent service, Not connected, how to start it; no intro, no Aura line", "Agent service" in head
		and "NOT CONNECTED" in head and "Start the agent service with" in head and "autopilot serve" in head
		and not "An agent that plays your game" in head and not "USE WITH AURA" in head and not "Using Autopilot with Aura" in head, head)
	alive.yes = false
	dock.service._poll_at = 0.0
	dock.service.poll()
	dock._render()
	check("stopped: its words, Try again, View logs, Stop", dock._cp_title.text == "The agent service didn’t start." and dock.service.log_path in dock._cp_text.text
		and dock.connect_buttons() == ["Try again", "View logs", "Stop"], [dock._cp_title.text, dock._cp_text.text, dock.connect_buttons()])
	dock.service.candidates = [autopilot_exe + ".missing"]
	dock.service.reset()
	dock._connect()
	dock._render()
	check("not installed, no installer (a repository checkout): set the command or run autopilot serve", dock.service.state == "not_installed"
		and dock._cp_title.text == "No agent service found." and "Set Agent service command under Advanced" in dock._cp_text.text
		and "autopilot serve" in dock._cp_text.text and dock.connect_buttons() == ["Try again", "Stop"], dock._cp_text.text)
	DirAccess.make_dir_recursive_absolute(dock.service.installer_dir)
	for name in ["install.ps1", "install.sh"]:
		FileAccess.open(dock.service.installer_dir.path_join(name), FileAccess.WRITE).close()
	dock._render()
	check("not installed, the installer here: Install", dock.connect_buttons() == ["Install", "Stop"]
		and dock._cp_title.text == "The agent service isn’t installed on this computer.", [dock.connect_buttons(), dock._cp_title.text])
	launched.clear()
	alive.yes = true
	check("Install pressed", press(dock._connect_page, "Install"))
	check("Install runs the installer", dock.service.state == "installing" and launched.size() == 1
		and " ".join(launched[0][1]).contains("install.ps1" if OS.get_name() == "Windows" else "install.sh"), launched)
	dock._render()
	check("installing: the connect card says so", dock._cp_title.text == "Installing the agent service…", dock._cp_title.text)
	dock.service.candidates = [autopilot_exe]
	alive.yes = false
	dock.service._poll_at = 0.0
	dock.service.poll()
	check("installed: started", dock.service.state == "starting" and launched.size() == 2)
	dock._disconnect()
	dock._render()
	check("Disconnect: the card says Disconnected's Reconnect, no state line (the composer's foot only ever ● CONNECTED)",
		dock.connect_buttons() == ["Reconnect"] and not dock._cp_box.visible and dock._link_label.text == "● CONNECTED",
		[dock.connect_buttons(), dock._link_btn.text])
	check("Disconnected: not connected by itself again (only Reconnect does)", not fake.want and not dock._connect_when_ready)

	# Sign in with Aura: the agent service is asked (aura_sign_in frames drive the card)
	alive.yes = true
	dock.service.state = "idle"
	f = FileAccess.open(bj, FileAccess.WRITE)
	f.store_string(announced)
	f.close()
	dock._open_setup()
	dock._render()
	var asked := welcome(["steer", "usage", "account"])
	check("welcome with account: can sign in, and the dock asks for the account", bridge.can_sign_in() and asked == ["account"], asked)
	check("Sign in", press(dock._setup, "Sign in"))
	check("aura_sign_in sent", fake.sent == [{"type": "aura_sign_in"}], fake.sent)
	t = setup_text()
	check("preparing: getting ready (the agent service started, the browser next), no keys card",
		"GETTING READY" in t and "Starting the agent service…" in t and "Finish signing in in your browser…" in t
		and not "The first time" in t and _no_keys_card() and not "Available after you cancel" in t
		and not "Use my keys" in t, t)
	bridge._on_brain_frame({"type": "aura_sign_in", "state": "waiting", "url": "https://aura.example/auth/cli?c=1"})
	t = setup_text()
	check("waiting: finish in the browser, the url, Open again, Copy link, Cancel", "Finish signing in in your browser…" in t
		and "https://aura.example/auth/cli?c=1" in t and "Open again" in t and "Copy link" in t and "Cancel" in t and "IN YOUR BROWSER" in t
		and not "then choose Allow" in t and _no_keys_card() and not "Use my keys" in t, t)
	check("Cancel", press(dock._setup, "Cancel") and fake.types()[-1] == "aura_sign_in_cancel" and dock.sign_in.is_empty())
	bridge._on_brain_frame({"type": "aura_sign_in", "state": "error", "reason": "cancelled", "message": "x"})
	t = setup_text()
	check("cancelled: nothing shown, the choices back (the keys card too, folded)", dock.sign_in.is_empty()
		and "Create Account" in t and "BRING YOUR OWN KEYS (BYOK)" in t and "Show" in t, t)
	# Create Account: Aura's account creation page in the browser (not the agent service's), then its next page: Sign in
	fake.sent.clear()
	var opened_urls := []
	dock._setup.open_url = func(u: String): opened_urls.append(u)
	check("Create Account: Aura's account creation page opened, nothing asked of the agent service",
		press(dock._setup, "Create Account") and opened_urls.size() == 1 and opened_urls[0].ends_with("/auth/signup") and fake.sent.is_empty(),
		[opened_urls, fake.sent])
	t = setup_text()
	var sign_in_buttons := []
	for b in dock._setup.find_children("*", "Button", true, false):
		if b.text == "Sign in" and b.is_visible_in_tree():
			sign_in_buttons.append(str(b.theme_type_variation))
	check("Create Account's next page: finish creating, then Sign in (the primary); no Create Account",
		"Finish creating your Aura account" in t and "Once you have created your account, sign in and start using Autopilot" in t
		and "IN YOUR BROWSER" in t and not "Create Account" in t and sign_in_buttons == ["AutopilotPrimary"], [sign_in_buttons, t])
	check("its Sign in: the sign-in as ever", press(dock._setup, "Sign in") and fake.sent == [{"type": "aura_sign_in"}]
		and not dock._setup.creating_account, fake.sent)
	dock.cancel_sign_in()
	bridge._on_brain_frame({"type": "aura_sign_in", "state": "error", "reason": "cancelled", "message": "x"})
	check("cancelled after it: the two choices again", "Create Account" in setup_text(), setup_text())
	# the account creation page's server: autopilot.env's, else the download's settings.env, else Aura's
	var signup_dir := OS.get_user_data_dir().path_join("signup_url_test")
	DirAccess.make_dir_recursive_absolute(signup_dir)
	var signup_env := signup_dir.path_join("autopilot.env")
	var server_said := [Prefs.sign_up_url(signup_dir, signup_env)]
	FileAccess.open(signup_dir.path_join("settings.env"), FileAccess.WRITE).store_string("AUTOPILOT_AURA_SERVER=https://beta.example/\n")
	server_said.append(Prefs.sign_up_url(signup_dir, signup_env))
	FileAccess.open(signup_env, FileAccess.WRITE).store_string("AUTOPILOT_AURA_SERVER=https://own.example\n")
	server_said.append(Prefs.sign_up_url(signup_dir, signup_env))
	check("the account creation page: Aura's, the download's, autopilot.env's", server_said == ["https://app.tryaura.dev/auth/signup",
		"https://beta.example/auth/signup", "https://own.example/auth/signup"], server_said)
	for signup_file in ["autopilot.env", "settings.env"]:
		DirAccess.remove_absolute(signup_dir.path_join(signup_file))
	DirAccess.remove_absolute(signup_dir)
	dock.sign_in = {"state": "no_installer"}
	dock._setup.refresh()
	t = setup_text()
	check("agent service needed: no keys card, Try again, Cancel", "AGENT SERVICE NEEDED" in t and _no_keys_card()
		and "Try again" in t and "Cancel" in t, t)
	dock.sign_in = {}
	dock._setup.refresh()
	for case in [["denied", "Sign-in was cancelled in the browser."], ["timeout", "Nothing came back from the browser in 5 minutes."],
			["unreachable", "Couldn’t reach Aura. Check your connection and try again."], ["refused", "Aura refused: your plan has ended."]]:
		dock.begin_sign_in()
		bridge._on_brain_frame({"type": "aura_sign_in", "state": "error", "reason": case[0], "message": "Aura refused: your plan has ended."})
		t = setup_text()
		check("error %s: one plain line, Try again" % case[0], case[1] in t and "Try again" in t and "NOT SIGNED IN" in t, t)
	fake.sent.clear()
	check("Try again asks again", press(dock._setup, "Try again") and fake.types() == ["aura_sign_in"], fake.sent)
	bridge._on_brain_frame({"type": "aura_sign_in", "state": "signed_in", "email": "dev@example.org"})
	t = setup_text()
	check("signed in: as who, Continue, Use another account", "Signed in as" in t and "dev@example.org" in t and "Continue" in t
		and "Use another account" in t and "SIGNED IN" in t and not "Runs use" in t, t)
	check("signed in: the keys card folded, Bring your own keys (BYOK) [Show]", "BRING YOUR OWN KEYS (BYOK)" in t
		and "Show" in t and not "Use my keys" in t, t)
	fake.sent.clear()
	check("Use another account: signed out, then signed in again", press(dock._setup, "Use another account")
		and fake.types() == ["aura_sign_out", "aura_sign_in"], fake.sent)
	bridge._on_brain_frame({"type": "aura_sign_in", "state": "signed_in", "email": "dev@example.org"})
	dock.service.state = "ready"
	dock.service.url = url
	press(dock._setup, "Continue")
	t = setup_text()
	check("Continue: step 2, Aura", dock._setup.step == 2 and dock._setup.access == "aura" and not dock._setup.use_aura and "1 ACCOUNT · AURA" in t, t)
	check("step 2 ready: starts with the editor", "Starts with your editor" in t and not "It uses your Aura sign-in" in t, t)
	press(dock._setup, "Continue")
	check("recap: Aura and the email", "Aura · dev@example.org" in setup_text(), setup_text())
	# (the account frame sent at the sign-in, before Finish wrote access "aura": the agent service said "none")
	bridge._on_brain_frame({"type": "account", "mode": "none", "signed_in": false, "keys": {}})
	fake.sent.clear()
	press(dock._setup, "Next")
	check("Aura in the browser: access aura, Use with Aura off", Prefs.load_preferences().get("access") == "aura" and not dock._with_aura(), Prefs.load_preferences())
	check("Next asks for the account again (the one sent at the sign-in is stale)", "account" in fake.types(), fake.sent)
	press(dock._setup, "Skip")
	bridge._on_brain_frame({"type": "account", "mode": "aura", "email": "dev@example.org", "signed_in": true, "keys": {}})
	fake.sent.clear()
	check("the gear opens the Settings page and asks for the account; it shows on", press_gear() and dock._settings_page.visible
		and fake.types() == ["account"] and dock._gears.all(func(b): return b.button_pressed and b.theme_type_variation.begins_with("AutopilotOn")),
		fake.sent)
	check("the gear: an icon, no text, its tooltip Settings", dock._gears.size() == 2
		and dock._gears.all(func(g): return g.text == "" and g.icon != null and g.tooltip_text == "Settings"))
	t = _texts(dock._settings_page)
	check("after Finish the Settings page shows the account signed in", "AURA" in t and "dev@example.org" in t
		and not "billed to your Aura plan" in t and not "NOT SET UP" in t and not "SIGNED OUT" in t, t)
	check("the gear again: the Settings page closed, the gear off", press_gear() and not dock._settings_page.visible
		and dock._gears.all(func(b): return not b.button_pressed and b.theme_type_variation.begins_with("AutopilotQuiet")))
	t = _texts(dock._footer)
	check("the footer: no Rerun setup nor Settings (the gear, and the Settings page's Rerun setup), no account chip",
		not "Rerun setup" in t and not "SETTINGS" in t
		and "Reset memory…" in t and not "dev@example.org" in t and not _says_own_keys(dock._footer) and not "SIGN IN" in t.to_upper(), t)
	fake.sent.clear()
	dock._open_setup()
	check("Rerun setup asks for the account", fake.types() == ["account"], fake.sent)
	finish("", true)
	fake.sent.clear()

	# the connection lost during a sign-in: the agent service coming back shown meanwhile, the sign-in asked again once
	# back (the agent service keeps it going), or signed in while away (the account frame says so)
	dock._open_setup()
	dock._render()
	fake.sent.clear()
	press(dock._setup, "Sign in")
	bridge._on_brain_frame({"type": "aura_sign_in", "state": "waiting", "url": "https://aura.example/auth/cli?c=2"})
	bridge._on_brain_frame({"type": "account", "mode": "none", "signed_in": false, "keys": {}})
	check("an account frame on the same connection: the sign-in not asked again", fake.types() == ["aura_sign_in"], fake.sent)
	unwelcome()
	dock._tick_sign_in()
	t = setup_text()
	check("lost mid-sign-in: getting ready again, not the gone page", dock.sign_in.get("state") == "preparing" and "GETTING READY" in t
		and not "https://aura.example/auth/cli?c=2" in t and not "Open again" in t, [dock.sign_in, t])
	welcome(["steer", "usage", "account"])
	dock._tick_sign_in()
	check("back, before its account frame: nothing asked yet", fake.sent.is_empty(), fake.sent)
	bridge._on_brain_frame({"type": "account", "mode": "aura", "signed_in": true, "keys": {}})  # (the Aura app running: no email)
	check("back: the sign-in asked again", fake.types() == ["aura_sign_in"], fake.sent)
	bridge._on_brain_frame({"type": "account", "mode": "aura", "signed_in": true, "keys": {}})
	check("asked once", fake.types() == ["aura_sign_in"], fake.sent)
	bridge._on_brain_frame({"type": "aura_sign_in", "state": "waiting", "url": "https://aura.example/auth/cli?c=2"})
	check("its page shown again", "https://aura.example/auth/cli?c=2" in setup_text(), setup_text())
	unwelcome()
	dock._tick_sign_in()
	welcome(["steer", "usage", "account"])
	bridge._on_brain_frame({"type": "account", "mode": "aura", "email": "dev@example.org", "signed_in": true, "keys": {}})
	check("signed in while away: signed in, nothing asked", fake.sent.is_empty() and dock.sign_in.get("state") == "signed_in"
		and dock.sign_in.get("email") == "dev@example.org" and "Signed in as" in setup_text(), [fake.sent, dock.sign_in])
	dock.begin_sign_in()
	check("asked again", fake.types() == ["aura_sign_in"], fake.sent)
	fake.sent.clear()
	dock._open_setup()  # (the sign-in let go)
	unwelcome()
	welcome(["steer", "usage", "account"])
	bridge._on_brain_frame({"type": "account", "mode": "none", "signed_in": false, "keys": {}})
	check("a sign-in let go isn't asked again", not "aura_sign_in" in fake.types(), fake.sent)
	bridge.account = {"type": "account", "mode": "aura", "email": "dev@example.org", "signed_in": true, "keys": {}}
	finish("", true)
	fake.sent.clear()

	# Reset memory (was Delete save): the footer's button, the confirmation, the line when done; the frames unchanged
	t = _texts(dock)
	check("Reset memory…: the footer's button", "Reset memory…" in t and not "DELETE SAVE" in t and not "SAVE DATA" in t, t)
	# (confirmed in place, in the Memory row: no dialog)
	check("Reset memory: no dialog", dock.find_children("*", "ConfirmationDialog", true, false).is_empty())
	check("Reset memory…: asks in place", _press_shown(dock._footer, "Reset memory…") and dock._mem_ask.visible and not dock._mem_bar.visible
		and fake.sent.is_empty(), fake.sent)
	t = _texts(dock._mem_ask)
	check("the confirmation: Reset memory?, what it does, Reset memory (filled red) and Cancel", "Reset memory?" in t
		and "The agent forgets what it has learned about this game." in t and "Reset memory
Cancel" in t
		and _button_in(dock._mem_ask, "Reset memory").theme_type_variation == "AutopilotDangerFillSm"
		and (dock._mem_ask.get_theme_stylebox("panel") as StyleBoxFlat).border_color == HT.RED_LINE, t)
	check("Cancel: the Memory row again, nothing sent", _press_shown(dock._footer, "Cancel") and not dock._mem_ask.visible
		and dock._mem_bar.visible and fake.sent.is_empty(), fake.sent)
	_press_shown(dock._footer, "Reset memory…")
	check("Reset memory sends clear_memory, Resetting memory… (the button off)", _press_shown(dock._footer, "Reset memory")
		and fake.types() == ["clear_memory"] and dock._reset_line.visible and dock._reset_spin.visible
		and dock._reset_text.text == "Resetting memory…" and dock._reset_btn.disabled, fake.sent)
	bridge._on_brain_frame({"type": "memory_cleared", "ok": true, "files": 3})
	check("Reset memory: done, in the row; what is remembered: empty", dock._reset_text.text == "Memory reset successful"
		and not dock._reset_spin.visible and dock._save_label.text == "· empty" and not dock._reset_btn.disabled
		and dock._reset_text.get_theme_color("font_color") == HT.SUCCESS, [dock._reset_text.text, dock._save_label.text])
	check("not on the status line", not "Memory reset" in dock._status.text, dock._status.text)
	bridge._on_brain_frame({"type": "memory_cleared", "ok": false, "message": "the agent service couldn’t reach this game’s memory."})
	check("Reset memory: not reset, and why", dock._reset_text.text == "Memory not reset: the agent service couldn’t reach this game’s memory."
		and dock._reset_text.get_theme_color("font_color") == HT.RED, dock._reset_text.text)
	fake.sent.clear()

	# runs are real time: no timing choice, no turn_based in the start frame (not even a run again of an older one)
	dock._compose("Verify that the gate opens")
	t = _texts(dock._composer)
	check("composer: no timing choice", not ("TURN BASED" in t or "REAL TIME" in t or "TIMING" in t), t)
	press(dock._composer, "Run")
	var starts: Array = fake.sent.filter(func(f): return f.get("type") == "start")
	check("the start frame has no turn_based", starts.size() == 1 and not starts[0].run.has("turn_based") and starts[0].run.kind == "auto", fake.sent)
	var old_run = dock.runs[-1]
	bridge.run_active = false
	dock._on_result({"verdict": "confirmed", "reason": "It opens."}, "")  # (as the bridge's result does: no files written)
	fake.sent.clear()
	old_run.request = {"kind": "verify", "claim": "Verify that the gate opens", "turn_based": true, "time_limit_minutes": 12}
	dock._c_time_limit.value = 25
	dock._run_again(old_run)
	starts = fake.sent.filter(func(f): return f.get("type") == "start")
	check("a run again of an older turn-based request goes real time", starts.size() == 1 and not starts[0].run.has("turn_based"), fake.sent)
	check("a run again of a verify run: its own Time limit, not the composer's", starts.size() == 1
		and starts[0].run.get("time_limit_minutes") == 12, fake.sent)
	bridge.run_active = false
	dock._on_result({"verdict": "confirmed", "reason": "It opens."}, "")  # (as the bridge's result does: no files written)
	fake.sent.clear()

	# an agent service too old to sign in
	unwelcome()
	dock._open_setup()
	dock._render()
	welcome(["steer"])
	fake.sent.clear()
	press(dock._setup, "Sign in")
	check("an agent service without account: update it", "can’t sign in to Aura" in setup_text() and not "aura_sign_in" in fake.types(), setup_text())
	unwelcome()

	# the Aura app installed or not: Create Account the primary, then Sign in (quiet); no Use Aura app
	var app := Prefs.settings_base().path_join("Programs").path_join("aura-client").path_join("Aura.exe")
	for installed in ([false, true] if OS.get_name() == "Windows" else [false]):
		if installed:
			DirAccess.make_dir_recursive_absolute(app.get_base_dir())
			FileAccess.open(app, FileAccess.WRITE).close()
		dock._open_setup()
		dock._render()
		t = setup_text()
		check("the Aura card (Aura app %s)" % installed, "Create Account" in t and "Sign in" in t and not "Use Aura app" in t
			and not "AURA APP FOUND" in t and "New users get free Aura credits." in t, t)
		var aura_buttons := []
		for b in dock._setup.find_children("*", "Button", true, false):
			if b.text in ["Create Account", "Sign in", "Use Aura app", "Sign in with browser"]:
				aura_buttons.append([b.text, str(b.theme_type_variation)])
		check("Create Account the primary, then Sign in (quiet) (Aura app %s)" % installed, aura_buttons
			== [["Create Account", "AutopilotPrimary"], ["Sign in", "AutopilotQuiet"]], aura_buttons)
		if installed:
			DirAccess.remove_absolute(app)

	# no account chosen at step 1 (skip_account): access unchanged
	dock._open_setup()
	dock._render()
	skip_account()
	check("no account chosen: step 3", dock._setup.step == 3 and "Not chosen yet" in setup_text())
	press(dock._setup, "Back")
	check("Back from step 3 without step 2: step 1", dock._setup.step == 1)
	skip_account()
	press(dock._setup, "Next")
	check("no account chosen keeps the access", Prefs.load_preferences().get("access") == "aura")
	press(dock._setup, "Skip")

	# step 4, First run: an Explore run offered when the game's memory is empty (Explore and finish, Skip and finish),
	# none when it has something (Finish on step 3, or the setup closing by itself once the memory_state comes)
	check("the composer's Time limit: the one kept for the project", dock._c_time_limit.value == dock.time_limit(), dock._c_time_limit.value)
	welcome(["steer", "usage", "account"])
	bridge._on_brain_frame({"type": "memory_state", "runs": 0, "notes": false, "lib": false})
	dock._open_setup()
	dock._render()
	skip_account()
	t = setup_text()
	check("memory empty: step 3 says Next, the stepper 4 FIRST RUN", "Next" in t and not "Finish" in t
		and dock._setup.stepper_labels().size() == 4 and dock._setup.stepper_labels()[3] == "4 FIRST RUN", [t, dock._setup.stepper_labels()])
	press(dock._setup, "Next")
	t = setup_text()
	check("memory empty: step 4 offers an Explore run", dock.onboarding and dock._setup.step == 4 and "Let Autopilot learn your game" in t
		and "You’ve never used Autopilot with this game before. To help Autopilot understand your game, use this first exploration run and watch it improve. Autopilot still works if you skip this step." in t
		and "EXPLORE YOUR GAME" in t and "RECOMMENDED" in t and "MINUTES" in t
		and not "Plays for the minutes below" in t and "Explore and finish" in t and "Skip and finish" in t
		and not "Recommended, not required" in t and not "Back" in t and not "Checking what Autopilot remembers" in t, t)
	check("step 4: the text in the card, after its head", t.find("EXPLORE YOUR GAME") < t.find("You’ve never used Autopilot"), t)
	var spins: Array = dock._setup.find_children("*", "SpinBox", true, false)
	check("step 4: Minutes, 10 (its own, not the composer's Time limit)", spins.size() == 1 and spins[0].value == 10, spins.map(func(s): return s.value))
	var order4 := []
	for b in dock._setup.find_children("*", "Button", true, false):
		if b.text in ["Explore and finish", "Skip and finish"]:
			order4.append([b.text, str(b.theme_type_variation)])
	check("step 4: Explore and finish the primary, then Skip and finish", order4
		== [["Explore and finish", "AutopilotPrimary"], ["Skip and finish", "AutopilotQuiet"]], order4)
	check("step 4: Skip and finish closes the setup", press(dock._setup, "Skip and finish") and not dock.onboarding
		and dock._body.visible and not dock._setup.visible and dock.active == null)
	# Explore and finish: the composer's start frame for "Explore", the setup closed, the run shown live
	dock._open_setup()
	dock._render()
	skip_account()
	press(dock._setup, "Next")
	var going4 = dock._new_run("goal", "Reach the gate")
	dock._setup.refresh()
	check("step 4: a run going, Explore and finish disabled", "Explore and finish" in setup_text() and not press(dock._setup, "Explore and finish"))
	bridge.run_active = false
	dock._on_result({"kind": "goal", "reached": true}, "")
	dock.onboarding = true  # (the result's render kept the setup)
	dock._render()
	dock._setup.refresh()  # (as its look every 0.25 s does: the run over, Explore and finish enabled)
	fake.sent.clear()
	check("Explore and finish", press(dock._setup, "Explore and finish"), setup_text())
	var starts4: Array = fake.sent.filter(func(f): return f.get("type") == "start")
	check("Explore and finish: the start frame, Explore for 10 minutes", starts4.size() == 1 and starts4[0].run == {"kind": "explore", "minutes": 10}, fake.sent)
	check("Explore and finish: the setup closed, the run shown live", not dock.onboarding and dock._body.visible and dock.active != null
		and dock.shown == dock.active and dock.active.kind == "explore" and dock.active.prompt == "" and dock.active.shown_prompt() == "Explore" and dock._run_page.visible,
		[dock.onboarding, dock.active.prompt if dock.active else null])
	var explore4 = dock.active
	bridge.run_active = false
	dock._on_result({"kind": "explore"}, "")
	# the memory with something: Finish on step 3, no step 4
	bridge._on_brain_frame({"type": "memory_state", "runs": 2, "notes": true, "lib": false})
	dock._open_setup()
	dock._render()
	skip_account()
	t = setup_text()
	check("memory with something: step 3 says Finish, three steps", "Finish" in t and not "Next" in t and dock._setup.stepper_labels().size() == 3,
		[t, dock._setup.stepper_labels()])
	check("memory with something: Finish closes the setup", press(dock._setup, "Finish") and not dock.onboarding and dock._body.visible)
	check("memory with something: the Memory line says its runs", dock._save_text() == "2 runs", dock._save_text())
	# not connected at Next: step 4 waits; a memory_state of an earlier connection doesn't count; with something
	# remembered, the setup closes by itself
	unwelcome()
	check("the connection closed: the Memory line says nothing of the runs it had (removed from this computer, say)",
		not bridge.memory_known and bridge.memory_runs == 0 and dock._save_text() == "", dock._save_text())
	dock._open_setup()
	dock._render()
	skip_account()
	check("not connected: step 3 says Next, the stepper 4 FIRST RUN", "Next" in setup_text() and dock._setup.stepper_labels().size() == 4, setup_text())
	press(dock._setup, "Next")
	t = setup_text()
	check("step 4, not connected: connecting, Skip", dock._setup.step == 4 and "Connecting to the agent service…" in t and "Skip" in t
		and not "Explore and finish" in t and dock._setup.stepper_labels()[3] == "4 FIRST RUN", t)
	welcome(["steer", "usage", "account"])
	t = setup_text()
	check("step 4, connected, no memory_state on this connection yet: checking", dock.onboarding
		and "Checking what Autopilot remembers about this game…" in t and not "Explore and finish" in t, t)
	bridge._on_brain_frame({"type": "memory_state", "runs": 3, "notes": false, "lib": true})
	dock._setup.refresh()
	check("step 4, the memory has something: the setup closed by itself", not dock.onboarding and dock._body.visible and not dock._setup.visible)
	# Skip while waiting
	unwelcome()
	dock._open_setup()
	dock._render()
	skip_account()
	press(dock._setup, "Next")
	check("step 4 waiting: Skip closes the setup", press(dock._setup, "Skip") and not dock.onboarding and dock._body.visible)
	dock.runs.erase(going4)
	dock.runs.erase(explore4)
	dock.shown = null
	dock.active = null
	dock._render()
	fake.sent.clear()

	# BYOK's LLM provider: the dropdown preselected (the account frame's, else autopilot.env's, else Anthropic), the key's
	# placeholder following it, the pick written to autopilot.env with the key under its provider's name
	var env := Prefs.file(Prefs.ENV_FILE)
	bridge.account = {}
	Prefs.save_byok("", "opencode", "openai", "")
	dock._open_setup()
	dock._render()
	var su = dock._setup
	press(su, "Show")  # (the BYOK card opened: it opens folded)
	check("provider: no account frame, autopilot.env says openai: OpenAI", su._provider.selected == 1, su._provider.selected)
	bridge.account = {"type": "account", "mode": "own_keys", "signed_in": false, "keys": {}, "llm_provider": "anthropic"}
	su.refresh()
	check("provider: the account frame's wins over autopilot.env", su._provider.selected == 0, su._provider.selected)
	bridge.account = {"type": "account", "mode": "own_keys", "signed_in": false, "keys": {}, "llm_provider": "openai"}
	su.refresh()
	check("provider: an account frame saying openai: OpenAI", su._provider.selected == 1, su._provider.selected)
	bridge.account = {"type": "account", "mode": "own_keys", "signed_in": false, "keys": {}, "llm_provider": "gemini"}
	su.refresh()
	check("provider: an unknown one: Anthropic", su._provider.selected == 0, su._provider.selected)
	bridge.account = {"type": "account", "mode": "own_keys", "signed_in": false, "keys": {}}
	su.refresh()
	check("provider: an account frame without one (an older agent service): autopilot.env's", su._provider.selected == 1, su._provider.selected)
	DirAccess.remove_absolute(env)
	bridge.account = {}
	dock._open_setup()
	dock._render()
	su = dock._setup
	press(su, "Show")  # (the BYOK card opened: it opens folded)
	check("provider: nothing says: Anthropic", su._provider.selected == 0, su._provider.selected)
	check("provider dropdown: the six providers in order, its tooltip", range(su._provider.item_count).map(func(i): return su._provider.get_item_text(i))
		== ["Anthropic", "OpenAI", "OpenCode Zen", "OpenRouter", "DeepSeek", "Gemini"] and su._provider.tooltip_text == "LLM provider")
	check("agent dropdown: Claude Code, Codex, OpenCode", range(su._agent.item_count).map(func(i): return su._agent.get_item_text(i))
		== ["Claude Code", "Codex", "OpenCode"])
	check("the decision key's placeholder unchanged", su._typesafe.placeholder_text == "Optional")
	su._provider.select(1)
	su._provider.item_selected.emit(1)
	check("picking OpenAI: picked, the key's placeholder just Optional", su.llm_provider == "openai" and su._llm_key.placeholder_text == "Optional",
		su._llm_key.placeholder_text)
	su._llm_key.text = "sk-typed"
	bridge.account = {"type": "account", "mode": "own_keys", "signed_in": false, "keys": {}, "llm_provider": "anthropic"}
	su.refresh()
	check("the pick and the key typed stay when the page is built again", su._provider.selected == 1 and su._llm_key.text == "sk-typed",
		su._provider.selected)
	su._provider.select(0)
	su._provider.item_selected.emit(0)
	check("picking Anthropic again: the key's placeholder still Optional", su.llm_provider == "anthropic" and su._llm_key.placeholder_text == "Optional")
	su._provider.select(1)
	su._provider.item_selected.emit(1)
	Prefs.write_atomic(env, "KEEP=me\nANTHROPIC_API_KEY=an-old\nTYPESAFE_API_KEY=ts-old\n")
	su._typesafe.text = ""
	su._llm_key.text = " sk-oa "
	check("Use my keys with OpenAI", press(su, "Use my keys") and su.step == 2 and su.access == "own_keys")
	check("OpenAI: AUTOPILOT_AGENT=opencode, AUTOPILOT_OPENCODE_PROVIDER=openai and OPENAI_API_KEY written, the Anthropic and decision keys kept",
		FileAccess.get_file_as_string(env) == "KEEP=me\nANTHROPIC_API_KEY=an-old\nTYPESAFE_API_KEY=ts-old\nAUTOPILOT_AGENT=opencode\nAUTOPILOT_OPENCODE_PROVIDER=openai\nOPENAI_API_KEY=sk-oa\n",
		FileAccess.get_file_as_string(env).c_escape())
	check("Use my keys clears the fields", su.step == 2 and dock._setup._llm_key == null)
	press(su, "Back")
	check("Back: step 1, the fields empty, OpenAI still picked", su.step == 1 and su._llm_key.text == "" and su._typesafe.text == ""
		and su._provider.selected == 1, [su.step, su._provider.selected])
	su._provider.select(0)
	su._provider.item_selected.emit(0)
	check("Anthropic with the key empty: the provider written all the same, the keys kept", press(su, "Use my keys")
		and FileAccess.get_file_as_string(env) == "KEEP=me\nANTHROPIC_API_KEY=an-old\nTYPESAFE_API_KEY=ts-old\nAUTOPILOT_AGENT=opencode\nAUTOPILOT_OPENCODE_PROVIDER=anthropic\nOPENAI_API_KEY=sk-oa\n",
		FileAccess.get_file_as_string(env).c_escape())
	dock._open_setup()
	press(dock._setup, "Show")  # (the BYOK card opened: it opens folded)
	check("Setup… again: the pick forgotten, preselected (the account frame's)", dock._setup.llm_provider == "" and dock._setup.coding_agent == ""
		and dock._setup.step == 1 and dock._setup._provider.selected == 0)

	# BYOK's Coding Agent: preselected (the account frame's, else autopilot.env's, else OpenCode); Claude Code and Codex lock
	# the provider to theirs, OpenCode gets its own back; AUTOPILOT_AGENT written, OpenCode's provider only with OpenCode
	DirAccess.remove_absolute(env)
	Prefs.write_atomic(env, "AUTOPILOT_AGENT=codex\nAUTOPILOT_OPENCODE_PROVIDER=openrouter\n")
	bridge.account = {}
	dock._open_setup()
	dock._render()
	su = dock._setup
	press(su, "Show")  # (the BYOK card opened: it opens folded)
	check("agent: no account frame, autopilot.env says codex: Codex, OpenAI locked", su._agent.selected == 1 and su._provider.selected == 1
		and su._provider.disabled, [su._agent.selected, su._provider.selected, su._provider.disabled])
	bridge.account = {"type": "account", "mode": "own_keys", "signed_in": false, "keys": {}, "coding_agent": "claude", "llm_provider": "anthropic"}
	su.refresh()
	check("agent: the account frame's wins: Claude Code, Anthropic locked", su._agent.selected == 0 and su._provider.selected == 0
		and su._provider.disabled and su._provider.get_item_text(su._provider.selected) == "Anthropic", [su._agent.selected, su._provider.selected])
	bridge.account = {"type": "account", "mode": "own_keys", "signed_in": false, "keys": {}, "coding_agent": "cursor", "llm_provider": "deepseek"}
	su.refresh()
	check("agent: an unknown one: OpenCode, the frame's provider, unlocked", su._agent.selected == 2 and su._provider.selected == 4
		and not su._provider.disabled, [su._agent.selected, su._provider.selected])
	bridge.account = {"type": "account", "mode": "own_keys", "signed_in": false, "keys": {}, "coding_agent": "codex", "llm_provider": "openai"}
	su.refresh()
	check("agent: an account frame saying codex: Codex, OpenAI locked", su._agent.selected == 1 and su._provider.selected == 1
		and su._provider.disabled, [su._agent.selected, su._provider.selected])
	su._agent.select(2)
	su._agent.item_selected.emit(2)
	check("switching to OpenCode: OpenCode's provider from autopilot.env (OpenRouter), unlocked", su.coding_agent == "opencode"
		and su._provider.selected == 3 and not su._provider.disabled and su.provider_now() == "openrouter",
		[su._provider.selected, su.provider_now()])
	su._provider.select(4)
	su._provider.item_selected.emit(4)
	su._agent.select(0)
	su._agent.item_selected.emit(0)
	check("switching to Claude Code: Anthropic, locked", su.coding_agent == "claude" and su._provider.selected == 0 and su._provider.disabled
		and su.provider_now() == "anthropic")
	su._llm_key.text = "an-typed"
	su._key = []  # (built again)
	su.refresh()
	check("the agent picked and the key typed stay when the page is built again", su._agent.selected == 0 and su._provider.selected == 0
		and su._provider.disabled and su._llm_key.text == "an-typed", [su._agent.selected, su._provider.selected])
	su._agent.select(1)
	su._agent.item_selected.emit(1)
	check("switching to Codex: OpenAI, locked", su._provider.selected == 1 and su._provider.disabled and su.provider_now() == "openai")
	su._agent.select(2)
	su._agent.item_selected.emit(2)
	check("back to OpenCode: the provider last picked for it (DeepSeek)", su._provider.selected == 4 and not su._provider.disabled
		and su.provider_now() == "deepseek")
	check("the key's placeholder still Optional", su._llm_key.placeholder_text == "Optional")
	# a key typed for one LLM provider is never saved as another's: the field empties when the provider changes
	su._llm_key.text = "ds-typed"
	su._agent.select(2)
	su._agent.item_selected.emit(2)
	su._provider.select(4)
	su._provider.item_selected.emit(4)
	su.refresh()
	check("the same provider picked again: the key kept", su._llm_key.text == "ds-typed", su._llm_key.text)
	su._provider.select(3)
	su._provider.item_selected.emit(3)
	check("another provider picked: the key emptied", su._llm_key.text == "" and su.provider_now() == "openrouter", su._llm_key.text)
	su._llm_key.text = "or-typed"
	su._agent.select(0)
	su._agent.item_selected.emit(0)
	check("another Coding Agent (Claude Code: Anthropic): the key emptied", su._llm_key.text == "", su._llm_key.text)
	su._llm_key.text = "an-typed"
	su._key = []  # (built again)
	su.refresh()
	check("built again, the provider the same: the key kept", su._llm_key.text == "an-typed", su._llm_key.text)
	su._agent.select(0)
	su._agent.item_selected.emit(0)
	Prefs.write_atomic(env, "KEEP=me\nAUTOPILOT_AGENT=codex\nAUTOPILOT_OPENCODE_PROVIDER=openrouter\nOPENAI_API_KEY=oa-old\n")
	su._typesafe.text = ""
	su._llm_key.text = "an-new"
	check("Use my keys with Claude Code", press(su, "Use my keys") and su.step == 2 and su.access == "own_keys")
	check("Claude Code: AUTOPILOT_AGENT=claude, ANTHROPIC_API_KEY written, OpenCode's provider and the OpenAI key kept",
		FileAccess.get_file_as_string(env) == "KEEP=me\nAUTOPILOT_AGENT=claude\nAUTOPILOT_OPENCODE_PROVIDER=openrouter\nOPENAI_API_KEY=oa-old\nANTHROPIC_API_KEY=an-new\n",
		FileAccess.get_file_as_string(env).c_escape())
	press(su, "Back")
	check("Back: Claude Code still picked, Anthropic locked", su._agent.selected == 0 and su._provider.selected == 0 and su._provider.disabled)
	su._agent.select(1)
	su._agent.item_selected.emit(1)
	check("Codex with the key empty: AUTOPILOT_AGENT=codex, the keys and OpenCode's provider kept", press(su, "Use my keys")
		and FileAccess.get_file_as_string(env) == "KEEP=me\nAUTOPILOT_AGENT=codex\nAUTOPILOT_OPENCODE_PROVIDER=openrouter\nOPENAI_API_KEY=oa-old\nANTHROPIC_API_KEY=an-new\n",
		FileAccess.get_file_as_string(env).c_escape())
	press(su, "Back")
	su._agent.select(2)
	su._agent.item_selected.emit(2)
	su._provider.select(2)
	su._provider.item_selected.emit(2)
	su._llm_key.text = "oc-zen"
	check("OpenCode with OpenCode Zen: AUTOPILOT_AGENT=opencode, AUTOPILOT_OPENCODE_PROVIDER=opencode, OPENCODE_API_KEY", press(su, "Use my keys")
		and FileAccess.get_file_as_string(env) == "KEEP=me\nAUTOPILOT_AGENT=opencode\nAUTOPILOT_OPENCODE_PROVIDER=opencode\nOPENAI_API_KEY=oa-old\nANTHROPIC_API_KEY=an-new\nOPENCODE_API_KEY=oc-zen\n",
		FileAccess.get_file_as_string(env).c_escape())
	dock._open_setup()
	press(dock._setup, "Show")  # (the BYOK card opened: it opens folded)
	check("Setup… again: the agent pick forgotten, preselected (the account frame's: Codex)", dock._setup.coding_agent == ""
		and dock._setup._agent.selected == 1 and dock._setup._provider.disabled)
	su._llm_key.text = "oa-typed"
	bridge.account = {"type": "account", "mode": "own_keys", "signed_in": false, "keys": {}, "coding_agent": "claude", "llm_provider": "anthropic"}
	su.refresh()
	check("an account frame changing the provider (Codex's OpenAI to Claude Code's Anthropic): the key emptied",
		su._llm_key.text == "" and su.provider_now() == "anthropic", [su._llm_key.text, su.provider_now()])
	bridge.account = {}
	DirAccess.remove_absolute(env)
	finish("", true)
	dock._set_use_with_aura(false, false)
	finish("own_keys", false)
	fake.close()

	# usage: a frame with no run waits for the run its result makes; one during a run is kept on it
	bridge.usage_received.emit([{"kind": "llm", "model": "m1", "calls": 1, "input": 10, "output": 2}], false)
	check("usage with no run is held", dock._pending_usage != null)
	var r = dock._new_run("verify", "Verify that the gate opens")
	bridge.usage_received.emit([{"kind": "llm", "model": "m1", "calls": 3, "input": 1200, "output": 30, "cache_read": 5000},
		{"kind": "decision", "model": "d1", "calls": 40, "input": 4000, "output": 40}], false)
	check("usage kept on the run going on (replaced)", r.usage.size() == 2 and r.usage[0].input == 1200 and not r.usage_final, r.usage)
	bridge.usage_received.emit([{"kind": "llm", "model": "m1", "calls": 4, "input": 1500, "output": 35}], true)
	check("final usage", r.usage.size() == 1 and r.usage_final)
	check("no bill before the bill frame", r.bill.is_empty())
	bridge._on_brain_frame({"type": "bill", "usd": 0.593177, "billed": true})
	check("bill: kept on the run going on", r.bill.get("billed") == true and absf(float(r.bill.get("usd", 0.0)) - 0.593177) < 1e-9, r.bill)
	check("bill: in the run's usage record", r.usage_record().get("bill") == r.bill, r.usage_record())
	bridge._on_brain_frame({"type": "bill"})
	check("a bill without an amount is ignored", absf(float(r.bill.get("usd", 0.0)) - 0.593177) < 1e-9, r.bill)
	dock.settings_open = true
	dock._render()
	check("Settings page shown", dock._settings_page.visible and not dock._run_page.visible)
	t = _texts(dock._settings_page)
	check("Settings page: its head, Back to runs", t.begins_with("Settings\n") and "Back to runs" in t and not "‹ BACK" in t, t)
	check("Settings page: this project's runs (no selected run, no token tables), data sharing", not "SELECTED RUN" in t and not "THIS RUN" in t
		and "THIS PROJECT · 1 RUN" in t and "TIME\nPROMPT\nBILLED" in t and "Verify that the gate opens" in t
		and not "m1" in t and not "1,500" in t and not "CALLS" in t and "SHARE RUNS TO IMPROVE AUTOPILOT" in t
		and not "Ramen" in t and not "code export" in t
		and "Enabling this sends recordings, screenshots, and agent traces and memories to the developers of Autopilot." in t
		and SettingsPage.SHARING == CONSENT
		and not "A change applies from the next connection" in t, t)
	check("Settings page: Billed by Aura $0.59 for the project, $0.59 on the run's row",
		t.count("$0.59") == 2 and "Billed by Aura\n$0.59" in t, t)
	var box: CheckBox = dock._settings_page.find_children("*", "CheckBox", true, false)[0]
	check("sharing box shows preferences.json", box.button_pressed == false)
	check("sharing box: the switch says Data sharing off, 8 px to its right, its tooltip the plate's title",
		box.text == "Data sharing off" and box.get_theme_constant("h_separation") == HT.px(8)
		and box.tooltip_text == "Share runs to improve Autopilot", [box.text, box.tooltip_text])
	check("Settings: Sharing, Claude Code & Codex plates: title, subtitle, then the control",
		_setting_plate(dock._settings_page, "Share runs to improve Autopilot", SettingsPage.SHARING, "CheckBox")
		and _setting_plate(dock._settings_page, SettingsPage.ASSISTANTS_TITLE, SettingsPage.ASSISTANTS_INTRO, "Button"))
	box.button_pressed = true
	check("sharing box: toggled on, it says Data sharing on", box.text == "Data sharing on", box.text)
	check("sharing box writes share_data", Prefs.load_preferences().get("share_data") == true and Prefs.load_preferences().get("access") == "own_keys")
	check("Settings page: no Time limit (the composer's is every run's)", not "TIME LIMIT" in t
		and not "Set the default time limit for runs." in t and not "\nmin\n" in t
		and dock._settings_page.find_children("*", "SpinBox", true, false).is_empty(), t)
	dock._set_meta("time_limit_minutes", 25)
	check("Time limit: the one kept before (the Settings page's) still read", dock.time_limit() == 25)
	dock._c_time_limit.value = 45
	check("Time limit: the composer's, kept for this project as it changes", dock.time_limit() == 45
		and dock._meta("time_limit_minutes", 0) == 45)
	dock._c_time_limit.value = 9999
	check("Time limit: at most 600 minutes", dock._c_time_limit.value == 600 and dock.time_limit() == 600)
	dock._set_meta("time_limit_minutes", "a while")
	check("Time limit: one saved that isn't a number reads as the default", dock.time_limit() == 30)
	dock._set_meta("time_limit_minutes", 45)

	# the Account corner, from the account frames
	bridge.account = {}  # (none came yet from this agent service)
	dock._render_usage()
	t = _texts(dock._settings_page)
	check("account corner: BYOK from preferences, no frame yet: just the tag and the buttons", "BYOK" in t and not "OWN KEYS" in t and not _says_own_keys(dock._settings_page)
		and not "Keys are read" in t and not "Saved on this computer" in t and "Rerun setup" in t, t)
	welcome(["account"])
	bridge._on_brain_frame({"type": "account", "mode": "own_keys", "signed_in": false, "keys": {"decision": "••••a91f", "llm": "••••3c2e"}})
	t = _texts(dock._settings_page)
	check("account corner: BYOK, their endings", "BYOK" in t and not "own API keys" in t and not "Billing period" in t
		and "Saved on this computer: Decision provider ••••a91f · OpenCode (Anthropic) ••••3c2e" in t and "Your own keys" in t and "Rerun setup" in t
		and not "Sign out" in t and not "LLM provider" in t, t)
	for case in [["opencode", "anthropic", "OpenCode (Anthropic)"], ["opencode", "openai", "OpenCode (OpenAI)"],
			["opencode", "opencode", "OpenCode (OpenCode Zen)"], ["opencode", "openrouter", "OpenCode (OpenRouter)"],
			["opencode", "deepseek", "OpenCode (DeepSeek)"], ["opencode", "google", "OpenCode (Gemini)"],
			["opencode", "mistral", "OpenCode (mistral)"], ["claude", "anthropic", "Claude Code (Anthropic)"],
			["codex", "openai", "Codex (OpenAI)"], [null, "openai", "OpenCode (OpenAI)"], ["codex", null, "Codex (OpenAI)"]]:
		var af := {"type": "account", "mode": "own_keys", "signed_in": false, "keys": {"decision": "••••a91f", "llm": "••••3c2e"}}
		if case[0] != null:
			af.coding_agent = case[0]
		if case[1] != null:
			af.llm_provider = case[1]
		bridge._on_brain_frame(af)
		t = _texts(dock._settings_page)
		check("account corner: BYOK with coding_agent %s, llm_provider %s" % [case[0], case[1]],
			("Saved on this computer: Decision provider ••••a91f · %s ••••3c2e" % case[2]) in t, t)
	bridge._on_brain_frame({"type": "account", "mode": "own_keys", "signed_in": false, "keys": {"decision": null, "llm": "••••9b7d"},
		"coding_agent": "codex", "llm_provider": "openai"})
	t = _texts(dock._settings_page)
	check("account corner: BYOK, no decision key: the LLM key alone", "Saved on this computer: Codex (OpenAI) ••••9b7d" in t
		and not "Decision provider" in t, t)
	bridge._on_brain_frame({"type": "account", "mode": "own_keys", "signed_in": false, "keys": {"decision": "••••a91f", "llm": null},
		"coding_agent": "opencode", "llm_provider": "openai"})
	t = _texts(dock._settings_page)
	check("account corner: BYOK, no LLM key: no agent shown", "Saved on this computer: Decision provider ••••a91f" in t
		and not "Coding Agent" in t, t)
	bridge._on_brain_frame({"type": "account", "mode": "aura", "email": "dev@example.org", "signed_in": true, "keys": {"decision": null, "llm": null}})
	t = _texts(dock._settings_page)
	check("account corner: Aura, the email, the agent service, Sign out (no billed note, no bill: null)", "AURA" in t and "dev@example.org" in t
		and not "billed to your Aura plan" in t and not "Billing period" in t and "\nAgent service: running\n" in t and not "started by" in t
		and "Sign out" in t and "Rerun setup" in t, t)
	bridge._on_brain_frame({"type": "account", "mode": "aura", "email": "dev@example.org", "signed_in": true, "keys": {},
		"bill": {"usd": 12.34, "runs": 38, "from": "2026-10-01", "to": "2026-10-31"}})
	t = _texts(dock._settings_page)
	check("account corner: the billing period, its dates and amount (in mono)", "Billing period (Oct 1 – Oct 31):\n$12.34 USD" in t
		and not "38 runs" in t, t)
	bridge._on_brain_frame({"type": "account", "mode": "aura", "email": "dev@example.org", "signed_in": true, "keys": {}, "bill": null})
	check("account corner: bill null, no line", not "Billing period" in _texts(dock._settings_page))
	# out of Aura credits: Aura's words over the composer's box and on the account card, Add credits when Aura names a page
	check("no out_of_credits (an older agent service): no notice over the box", not dock._c_credits.visible and dock.out_of_credits().is_empty())
	var broke := "Your team is out of Aura credits. Add credits at https://aura.example/account/pricing to keep playtesting."
	bridge._on_brain_frame({"type": "account", "mode": "aura", "email": "dev@example.org", "signed_in": true, "keys": {},
		"bill": {"usd": 12.34, "runs": 38, "from": "2026-10-01", "to": "2026-10-31"},
		"out_of_credits": {"message": broke, "url": "https://aura.example/account/pricing"}})
	var prompt_box: Control = dock._c_prompt.get_parent().get_parent()
	check("out of credits: Aura's words in red, directly over the box, Add credits", dock._c_credits.visible
		and dock._c_credits_text.text == broke and dock._c_credits_text.get_theme_color("font_color") == HT.RED_FG
		and dock._c_credits.get_parent() == prompt_box.get_parent() and dock._c_credits.get_index() == prompt_box.get_index() - 1
		and _button_in(dock._c_credits, "Add credits") != null and dock._c_credits_url == "https://aura.example/account/pricing",
		[dock._c_credits.visible, dock._c_credits_text.text, dock._c_credits_url])
	check("out of credits: (red box) no other words over the box", _texts(dock._c_credits) == broke + "\nAdd credits"
		and (dock._c_credits.get_theme_stylebox("panel") as StyleBoxFlat).border_color == HT.RED_LINE, _texts(dock._c_credits))
	var typed: String = dock._c_prompt.text
	type_prompt("Verify that the gate opens")
	check("out of credits: Run stays enabled (Aura checks again as the run starts)", not dock._c_run.disabled)
	type_prompt(typed)
	t = _texts(dock._settings_page)
	check("account corner: out of credits, Aura's words under the billing period", ("$12.34 USD\n%s\n" % broke) in t, t)
	var add := _button_in(dock._settings_page, "Add credits")
	var rerun := _button_in(dock._settings_page, "Rerun setup")
	check("account corner: Add credits in the buttons, beside Rerun setup and Sign out", add != null and rerun != null
		and add.get_parent() == rerun.get_parent() and _button_in(dock._settings_page, "Sign out").get_parent() == rerun.get_parent(), t)
	var red_words: Array = dock._settings_page.find_children("*", "Label", true, false).filter(func(l): return l.text == broke)
	check("account corner: the words in red", red_words.size() == 1 and red_words[0].get_theme_color("font_color") == HT.RED, red_words.size())
	bridge._on_brain_frame({"type": "account", "mode": "aura", "email": "dev@example.org", "signed_in": true, "keys": {},
		"out_of_credits": {"message": "Your team is out of Aura credits.", "url": null}})
	t = _texts(dock._settings_page)
	check("out of credits, no page named: the words, no Add credits (over the box and on the card)", dock._c_credits.visible
		and dock._c_credits_text.text == "Your team is out of Aura credits." and not dock._c_credits_btn.visible
		and _button_in(dock._c_credits, "Add credits") == null and "Your team is out of Aura credits." in t and not "Add credits" in t, t)
	bridge._on_brain_frame({"type": "account", "mode": "aura", "email": "dev@example.org", "signed_in": true, "keys": {}, "out_of_credits": null})
	t = _texts(dock._settings_page)
	check("credits again (out_of_credits null): the notice gone, over the box and on the card", not dock._c_credits.visible
		and not "out of Aura credits" in t and not "Add credits" in t, t)
	bridge._on_brain_frame({"type": "account", "mode": "own_keys", "signed_in": false, "keys": {},
		"out_of_credits": {"message": broke, "url": "https://aura.example/account/pricing"}})
	check("no out-of-credits notice for BYOK", not dock._c_credits.visible and not "out of Aura credits" in _texts(dock._settings_page))
	bridge._on_brain_frame({"type": "account", "mode": "own_keys", "signed_in": false, "keys": {},
		"bill": {"usd": 1.0, "runs": 1, "from": "2026-10-01", "to": "2026-10-31"}})
	check("account corner: no Autopilot bill for BYOK", not "Billing period" in _texts(dock._settings_page))
	bridge._on_brain_frame({"type": "account", "mode": "aura", "email": "dev@example.org", "signed_in": true, "keys": {}})
	press(dock._settings_page, "Sign out")
	t = _texts(dock._settings_page)
	check("Sign out confirms in place", "Sign out dev@example.org on this computer?" in t
		and "Every editor on this computer stops using Aura until you sign in again." in t
		and "Cancel" in t, t)
	fake.sent.clear()
	press(dock._settings_page, "Sign out")
	check("Sign out confirmed: aura_sign_out sent, then the account asked", fake.types() == ["aura_sign_out", "account"], fake.sent)
	bridge._on_brain_frame({"type": "account", "mode": "aura", "email": "dev@example.org", "signed_in": false, "keys": {}})
	t = _texts(dock._settings_page)
	check("account corner: signed out, Sign in again", "SIGNED OUT" in t and "Sign in again" in t and "Your Aura sign-in on this computer ended." in t
		and not "Sign in again to keep running with Aura." in t, t)
	fake.sent.clear()
	press(dock._settings_page, "Sign in again")
	check("Sign in again: aura_sign_in sent", fake.types() == ["aura_sign_in"], fake.sent)
	bridge._on_brain_frame({"type": "aura_sign_in", "state": "waiting", "url": "https://aura.example/auth/cli"})
	t = _texts(dock._settings_page)
	check("Sign in again in place: waiting", "Finish signing in in your browser…" in t and "https://aura.example/auth/cli" in t and "Cancel" in t
		and not "then choose Allow" in t, t)
	bridge._on_brain_frame({"type": "aura_sign_in", "state": "signed_in", "email": "dev@example.org"})
	bridge._on_brain_frame({"type": "account", "mode": "aura", "email": "dev@example.org", "signed_in": true, "keys": {}})
	check("signed in again: Aura", "Sign out" in _texts(dock._settings_page) and not "SIGNED OUT" in _texts(dock._settings_page) and dock.sign_in.is_empty())
	bridge._on_brain_frame({"type": "account", "mode": "none", "signed_in": false, "keys": {}})
	t = _texts(dock._settings_page)
	check("account corner: not set up, Rerun setup", "NOT SET UP" in t and "Choose how to run Autopilot" in t and "Rerun setup" in t
		and not "before your first run" in t, t)
	check("no design notes in the Settings page", not ("you@studio.com" in t or "CONFIRM IN PLACE" in t or "AURA SIGN-IN ENDED" in t), t)
	dock._set_use_with_aura(true, false)
	dock._render_usage()
	t = _texts(dock._settings_page)
	check("account corner: the Aura app", "AURA APP" in t and "The Aura app’s agent service" in t
		and not "Sign out in the Aura app." in t and not "billed to your Aura plan" in t and not "Sign out" in t, t)
	bridge._on_brain_frame({"type": "account", "mode": "aura", "email": "dev@example.org", "signed_in": true, "keys": {},
		"bill": {"usd": 3.5, "runs": 4, "from": "2026-10-01", "to": "2026-10-31"},
		"out_of_credits": {"message": "Your team is out of Aura credits.", "url": "https://aura.example/account/pricing"}})
	t = _texts(dock._settings_page)
	add = _button_in(dock._settings_page, "Add credits")
	rerun = _button_in(dock._settings_page, "Rerun setup")
	check("account corner: the Aura app out of credits: the words under its billing period, Add credits beside Rerun setup",
		"$3.50 USD\nYour team is out of Aura credits.\n" in t and add != null and rerun != null and add.get_parent() == rerun.get_parent()
		and dock._c_credits.visible, t)
	dock._set_use_with_aura(false, false)
	bridge._on_brain_frame({"type": "account", "mode": "aura", "email": "dev@example.org", "signed_in": true, "keys": {}})
	dock._render_usage()
	var idle_bad := []
	for b in dock._settings_page.find_children("*", "Button", true, false):
		if b.text == "Rerun setup" and (not b.disabled or b.tooltip_text != "Finish or stop the run first"):
			idle_bad.append(b.text)
	check("a run going: Rerun setup disabled, saying why", idle_bad.is_empty() and "Rerun setup" in _texts(dock._settings_page)
		and not press(dock._settings_page, "Rerun setup") and not dock.onboarding, idle_bad)
	var going = dock.active
	dock.active = null
	dock._render_usage()
	check("Rerun setup opens the setup", press(dock._settings_page, "Rerun setup") and dock.onboarding)
	dock.active = going
	finish("own_keys", true)
	unwelcome()
	check("account view without a frame or a choice: not set up", SettingsPage.account_view({}, null, false).kind == "none")

	dock.settings_open = true
	dock._render()
	dock._settings_page.back.emit()
	check("Back leaves the Settings page", not dock._settings_page.visible)
	bridge.usage_received.emit([], true)
	check("an empty usage keeps the run's list empty, no crash", r.usage.is_empty())
	dock.settings_open = true
	dock._render()
	check("Settings page empty state", "No usage yet." in _texts(dock._settings_page) or "THIS PROJECT · 1 RUN" in _texts(dock._settings_page))

	# a bill with no run going on waits for the run its result makes; billed false shows nothing
	dock.active = null
	bridge.run_active = false
	bridge._on_brain_frame({"type": "bill", "usd": 0.004, "billed": true})
	check("a bill with no run is held", dock._pending_bill.get("usd") == 0.004, dock._pending_bill)
	dock._on_result({"claim": "Started elsewhere", "verdict": "confirmed", "reason": "ok"}, "")
	var r2 = dock.runs[-1]
	check("the held bill goes to the run its result makes", r2.bill.get("usd") == 0.004 and dock._pending_bill.is_empty(), r2.bill)
	dock.settings_open = true
	dock._render()
	t = _texts(dock._settings_page)
	check("below a cent: < $0.01 on its row", "< $0.01" in t, t)
	r2.bill = {"usd": 0.5, "billed": false, "reason": "billed later"}
	dock._render_usage()
	t = _texts(dock._settings_page)
	check("billed false: no bill on its row", not "$0.50" in t and not "< $0.01" in t, t)
	dock.settings_open = false

	# Use with Aura: the connect page's line and the link's address (a real link: Aura's service isn't running)
	bridge.link = real_link
	dock._set_use_with_aura(true)
	bridge.link.open("", "", true)
	bridge.link.poll()
	check("with Aura and no Aura service: no socket, a problem in words", bridge.link.ws == null and "Aura" in bridge.link.problem, bridge.link.problem)
	bridge.link.close()
	dock._set_use_with_aura(false, false)

	await dogfood_checks()
	steer_checks()
	bridge.link = FakeLink.new()
	removal_checks()
	flatness_checks()
	round2_checks()
	round3_checks()
	fixes_checks()
	assistants_checks()
	history_checks()
	await layout_checks()

	dock.queue_free()
	bridge.queue_free()
	print("\n%d/%d checks passed" % [passed, passed + failed])
	quit(0 if failed == 0 else 1)


## The run history kept between editor sessions (ui/history.gd) as the dock keeps it: a run saved as it starts (not
## done) and again when its result comes, one live as the dock closes saved not done; loaded back in the next session
## before a run begun meanwhile, the live one with no result ("The editor closed before the result came.") and no
## Policy log; the history's words without "this session" ("No runs yet.", "3 runs").
func history_checks() -> void:
	var fake := FakeLink.new()
	bridge.link = fake
	welcome(["steer", "usage", "account"])
	_plain_view()
	var before: Array = dock.runs
	var dir := OS.get_environment("UNITS_DIR").path_join("dock_history")
	dock.history_dir = dir
	dock.runs = []
	dock.active = null
	dock.shown = null
	bridge.run_active = false
	dock._render()
	var rail := _texts(dock._rail_list)
	check("history: none yet, \"No runs yet.\" (not this session's)", "No runs yet." in rail and not "session" in rail, rail)
	check("history: the narrow button, \"No runs yet\"", dock._hist_meta.text == "No runs yet", dock._hist_meta.text)

	dock._compose("Verify the door opens")
	press(dock._composer, "Run")
	var r = dock.active
	var path: String = dir.path_join(r.history_file) if r != null else ""
	check("history: a run saved as it starts, not done", r != null and r.history_file != "" and Prefs.read_json(path).get("done") == false,
		Prefs.read_json(path))
	bridge.run_active = false
	dock._on_result({"verdict": "confirmed", "reason": "It opens"}, "")
	var saved := Prefs.read_json(path)
	check("history: saved again with its result", saved.get("done") == true and saved.get("result", {}).get("verdict") == "confirmed",
		saved)
	dock._compose("Reach the castle gate")
	press(dock._composer, "Run")
	var live = dock.active
	dock._exit_tree()  # (the dock closing with the run going on)
	check("history: the run going on as the dock closes, saved not done", live != null and live != r
		and Prefs.read_json(dir.path_join(live.history_file)).get("done") == false)
	dock.active = null
	bridge.run_active = false

	# the next editor session: the runs kept, loaded before a run begun meanwhile
	var meanwhile := Model.Run.new()
	meanwhile.id = "meanwhile"
	meanwhile.prompt = "Explore"
	meanwhile.kind = "explore"
	meanwhile.started_unix = Time.get_unix_time_from_system() + 60.0
	meanwhile.started_ms = Time.get_ticks_msec()
	meanwhile.ended_ms = meanwhile.started_ms
	dock.runs = [meanwhile]
	dock.shown = null
	dock.load_history()
	var prompts: Array = dock.runs.map(func(x): return x.prompt)
	check("history: loaded, oldest first, before the run begun meanwhile", prompts == ["Verify the door opens", "Reach the castle gate", "Explore"]
		and dock.runs[2] == meanwhile, prompts)
	if dock.runs.size() == 3:
		var done = dock.runs[0]
		var closed = dock.runs[1]
		check("history: the finished run as it was", done.verdict() == "CONFIRMED" and done.reason() == "It opens" and not done.live()
			and done.request.get("time_limit_minutes") is int, [done.verdict(), done.request])
		check("history: the run live as the editor closed: NO RESULT", closed.verdict() == "NO RESULT" and not closed.live()
			and closed.feed[-1].text == "Editor closed: no result", [closed.verdict(), closed.feed])
		dock.composing = true  # (New run: New, and how many runs)
		dock._render()
		check("history: the narrow button counts them, \"3 runs\"", dock._hist_meta.text == "3 runs", dock._hist_meta.text)
		dock._select_run(closed)
		var page := _texts(dock._result)
		check("history: its result says the editor closed, no Policy log", "The editor closed before the result came." in page
			and not "The connection to the agent service closed" in page and not dock._tab_policy.visible, page)

		# Reset memory done: the runs ended leave the history, their files kept aside (never loaded again); a run going
		# stays, its file too
		var going := Model.Run.new()
		going.id = "going"
		going.prompt = "Reach the tower"
		going.kind = "goal"
		going.started_unix = Time.get_unix_time_from_system() + 120.0
		going.started_ms = Time.get_ticks_msec()
		dock.runs.append(going)
		dock._keep_run(going, true)
		var set_aside: Array = [done.history_file, closed.history_file]
		bridge._on_brain_frame({"type": "memory_cleared", "ok": true, "files": 3})
		check("history: after Reset memory, only the run going is listed", dock.runs == [going] and dock.shown == null,
			dock.runs.map(func(x): return x.prompt))
		check("history: its file the only one left in the history's folder", Array(DirAccess.get_files_at(dir)) == [going.history_file],
			DirAccess.get_files_at(dir))
		var aside := dir.path_join(History.RESET_BACKUP)
		var folders := DirAccess.get_directories_at(aside) if DirAccess.dir_exists_absolute(aside) else PackedStringArray()
		var kept: Array = Array(DirAccess.get_files_at(aside.path_join(folders[0]))) if folders.size() == 1 else []
		kept.sort()
		set_aside.sort()
		check("history: the files of the runs ended kept in .reset-backup/<time>/", kept == set_aside, [folders, kept])
		var reloaded: Array = History.load_runs(dir).map(func(x): return x.prompt)
		check("history: the next editor session loads only the run going", reloaded == ["Reach the tower"], reloaded)
		bridge._on_brain_frame({"type": "memory_cleared", "ok": false, "message": "in use"})
		check("history: a reset that failed lists the runs as they were", dock.runs == [going], dock.runs.size())
	dock.history_dir = ""
	dock.runs = before
	dock.active = null
	dock.shown = null
	dock._render()


# --- removing Autopilot from this computer -------------------------------------------------------------------

## The Settings page's Remove Autopilot (confirmed in place, Delete Autopilot's memories off each time), `autopilot
## uninstall` run with a stand-in launcher, its page by exit code (0, 3, anything else, too long) and its buttons;
## nothing started or connected meanwhile, the setup not shown with preferences.json gone; without an agent service the
## settings folder emptied here but for memory/; and, at start, the setup at step 2 when the agent service is gone.
func removal_checks() -> void:
	var fake = bridge.link
	var units := OS.get_environment("UNITS_DIR")
	var exe := units.path_join("bin").path_join("autopilot.exe")
	DirAccess.make_dir_recursive_absolute(exe.get_base_dir())
	FileAccess.open(exe, FileAccess.WRITE).close()
	var launched := []
	var alive := {"yes": true, "code": 0}
	var stopped := []
	var svc = dock.service
	var was := [svc.candidates, svc.installer_dir, svc.launcher, svc.running, svc.exit_code, svc.stopper, dock.auto_start_allowed]
	svc.candidates = [exe]
	svc.installer_dir = units.path_join("no_installer")
	svc.launcher = func(program: String, args: PackedStringArray) -> int:
		launched.append([program, args])
		return 5150
	svc.running = func(_p: int) -> bool: return alive.yes
	svc.exit_code = func(_p: int) -> int: return alive.code
	svc.stopper = func(p: int) -> bool:
		stopped.append(p)
		return true
	# (never the real autopilot: no home of the user's own to find an installed one in, no command setting, and the
	# stand-in launcher proven in place before anything below can ask for the removal)
	var was_home: String = svc.home_dir
	var was_command: String = svc.command_setting
	svc.home_dir = units.path_join("removal_home")
	svc.command_setting = ""
	var probe: int = svc.launcher.call("probe", PackedStringArray())
	var safe: bool = probe == 5150 and launched.size() == 1 and svc.find() == PackedStringArray([exe])
	check("removal: only the stand-in autopilot (in the temporary folder) and the stand-in launcher", safe, [probe, launched, svc.find()])
	if not safe:
		svc.launcher = was[2]
		svc.home_dir = was_home
		svc.command_setting = was_command
		return
	launched.clear()
	_plain_view()
	dock.settings_open = true
	dock._render()
	var t := _texts(dock._settings_page)
	check("Settings: Remove Autopilot, last, no paragraph", "UNINSTALL AUTOPILOT" in t and not SettingsPage.REMOVE_TEXT in t
		and not "training data" in t.to_lower() and "Uninstall" in t
		and t.rfind("UNINSTALL AUTOPILOT") > t.rfind(SettingsPage.SHARING), t)
	dock._settings_page.show_data([], true, "", {"kind": "none", "busy": true})
	var b: Button = null
	for c in dock._settings_page.find_children("*", "Button", true, false):
		if c.text == "Uninstall":
			b = c
	check("Uninstall disabled while a run is going", b != null and b.disabled and b.tooltip_text == "Finish or stop the run first")
	dock._render()
	check("Uninstall asks first", press(dock._settings_page, "Uninstall"))
	t = _texts(dock._settings_page)
	var delete := _delete_box()
	check("confirm in place: the question, what it removes, Delete Autopilot's memories (off), Remove and Cancel",
		"Uninstall Autopilot from this computer?" in t and delete != null and not delete.button_pressed and "Uninstall" in t and "Cancel" in t
		and SettingsPage.REMOVE_TEXT in t and not "already sent" in t
		and SettingsPage.DELETE_MEMORY == ("[Optional] Delete Autopilot’s memories for all projects in this machine across "
			+ "all editors. I understand that this action is destructive and deletes Autopilot’s memories for my "
			+ "entire team."), t)
	delete.button_pressed = true
	check("Delete Autopilot's memories ticked", dock._settings_page.delete_memory)
	check("Cancel", press(dock._settings_page, "Cancel") and not dock._settings_page.confirming_removal and launched.is_empty())
	press(dock._settings_page, "Uninstall")
	delete = _delete_box()
	check("asked again: Delete Autopilot's memories unticked", delete != null and not delete.button_pressed
		and not dock._settings_page.delete_memory)
	var asked := []
	var on_asked := func(keep: bool) -> void: asked.append(keep)
	dock._settings_page.remove_asked.disconnect(dock.remove_from_computer)
	dock._settings_page.remove_asked.connect(on_asked)
	delete.button_pressed = true
	press(dock._settings_page, "Uninstall")
	check("ticked: Remove asks to delete the memories (keep_memory false)", asked == [false], asked)
	dock._settings_page.remove_asked.disconnect(on_asked)
	dock._settings_page.remove_asked.connect(dock.remove_from_computer)
	dock._render()

	fake.open("", "", false)
	fake.connected = true
	press(dock._settings_page, "Uninstall")
	check("Uninstall (confirmed)", press(dock._settings_page, "Uninstall"))
	var line := " ".join(launched[0][1]) if launched.size() == 1 else ""
	var temp: String = svc.temp_folder()
	check("Remove runs autopilot uninstall --yes --keep-memory, its output to a log in the temporary folder, from there",
		launched.size() == 1 and line.contains("uninstall --yes --keep-memory --log ") and svc.removal_log.begins_with(temp)
		and svc.removal_log.get_file().begins_with("autopilot-uninstall-") and (line.contains("cd /d") if OS.get_name() == "Windows" else launched[0][1][3] == temp), [line, svc.removal_log])
	check("Remove: disconnected first", not fake.want and not fake.connected)
	t = _texts(dock._removal_page)
	check("removing: its page in place of everything else", dock._removal_page.visible and not dock._body.visible and not dock._setup.visible
		and "Uninstalling Autopilot from this computer…" in t, t)
	dock._connect()
	dock._tick_service()
	dock.prepare_service()
	check("removing: nothing connects, starts or installs", not fake.want and launched.size() == 1 and svc.state == "idle", [fake.want, launched.size(), svc.state])
	DirAccess.remove_absolute(Prefs.file(Prefs.PREFERENCES))
	alive.yes = false
	svc.poll_removal()
	dock._render()
	t = _texts(dock._removal_page)
	check("removed (0): its words, what is left to do, Set up again and no setup though preferences.json is gone",
		svc.removal == "done" and "Uninstallation successful" in t and dock.REMOVED_NEXT in t and "Set up again" in t
		and not dock._setup.visible and not dock.onboarding, t)
	alive.yes = true
	svc.uninstall(false)
	check("Keep off: no --keep-memory", launched.size() == 2 and not " ".join(launched[1][1]).contains("--keep-memory"), launched)
	alive.yes = false
	alive.code = 3
	svc.poll_removal()
	FileAccess.open(svc.removal_log, FileAccess.WRITE).close()  # (as the shell makes it)
	dock._render()
	t = _texts(dock._removal_page)
	check("removed but for some files (3): what is left to do, View logs, Set up again", svc.removal == "kept"
		and "Uninstallation successful" in t and dock.REMOVED_NEXT in t and not "the log says which" in t
		and "View logs\nSet up again" in t and not "OPEN LOG" in t, t)
	DirAccess.remove_absolute(svc.removal_log)
	alive.yes = true
	svc.uninstall(true)
	alive.yes = false
	alive.code = 1
	svc.poll_removal()
	dock._render()
	t = _texts(dock._removal_page)
	check("couldn't (1): Try again and Cancel", svc.removal == "failed" and "Couldn’t uninstall Autopilot from this computer." in t
		and "Try again" in t and "Cancel" in t, t)
	alive.yes = true
	check("Try again: the same choice", press(dock._removal_page, "Try again") and svc.removal == "running"
		and " ".join(launched[-1][1]).contains("--keep-memory"), launched[-1])
	svc._removal_at -= svc.REMOVE_TIMEOUT + 1.0
	svc.poll_removal()
	dock._render()
	t = _texts(dock._removal_page)
	check("too long: stopped, couldn't, in words", svc.removal == "failed" and stopped == [5150] and "It didn’t finish within 10 minutes." in t, t)
	check("Cancel: the usual page again", press(dock._removal_page, "Cancel") and svc.removal == "" and dock._body.visible and not dock._removal_page.visible)
	alive.yes = true
	svc.uninstall(true)
	alive.yes = false
	alive.code = 0
	svc.poll_removal()
	dock._render()
	check("Set up again: the first-run setup from its first step", press(dock._removal_page, "Set up again") and svc.removal == ""
		and dock.onboarding and dock._setup.visible and dock._setup.step == 1 and not dock._removal_page.visible)
	_plain_view()

	# Without an agent service: the settings folder emptied here, memory/ too unless kept
	svc.candidates = [exe + ".missing"]
	var folder := Prefs.settings_base().path_join(Prefs.FOLDER)
	for name in ["preferences.json", "autopilot.env", "logs/agent-service-1.log", "memory/Orchard/notes.md", "aura-brain.json"]:
		DirAccess.make_dir_recursive_absolute(folder.path_join(name).get_base_dir())
		FileAccess.open(folder.path_join(name), FileAccess.WRITE).close()
	var count := launched.size()
	dock.remove_from_computer(false)
	var log := FileAccess.get_file_as_string(svc.removal_log)
	check("not installed: removed here, memory/ too, aura-brain.json kept", launched.size() == count
		and svc.removal == "done" and not DirAccess.dir_exists_absolute(folder.path_join("memory")) and FileAccess.file_exists(folder.path_join("aura-brain.json"))
		and not FileAccess.file_exists(folder.path_join("autopilot.env")) and not DirAccess.dir_exists_absolute(folder.path_join("logs")), [svc.removal, log])
	DirAccess.remove_absolute(svc.removal_log)
	svc.end_removal()
	DirAccess.make_dir_recursive_absolute(folder.path_join("memory/Orchard"))
	FileAccess.open(folder.path_join("memory/Orchard/notes.md"), FileAccess.WRITE).close()
	dock.remove_from_computer(true)
	log = FileAccess.get_file_as_string(svc.removal_log)
	check("not installed, Keep on: removed, memory/ kept", svc.removal == "done" and FileAccess.file_exists(folder.path_join("memory/Orchard/notes.md"))
		and "Kept what Autopilot learned about your games in" in log, [svc.removal, log])
	DirAccess.remove_absolute(svc.removal_log)
	svc.end_removal()

	# At start: set up before, the agent service gone: the setup at step 2
	_plain_view()
	var bj := Prefs.file(Prefs.OWN_BRAIN_FILE)
	DirAccess.remove_absolute(bj)
	Prefs.save_preferences({"onboarded": true, "access": "own_keys"})
	dock.auto_start_allowed = true
	check("set up, the agent service gone: the setup at step 2 at start", dock.service_setup_needed())
	dock._open_setup_at_service()
	dock._render()
	t = setup_text()
	check("step 2: not installed, the account as chosen", dock.onboarding and dock._setup.step == 2 and dock._setup.access == "own_keys"
		and "Set up the agent service" in t and "No agent service found" in t and "AGENT SERVICE COMMAND" in t, t)
	_plain_view()
	svc.candidates = [exe]
	check("the agent service there: not", not dock.service_setup_needed())
	svc.candidates = [exe + ".missing"]
	dock.set_service_command("py -m autopilot")
	check("the Agent service command set: not", not dock.service_setup_needed())
	dock.set_service_command("")
	dock._set_use_with_aura(true, false)
	check("Use with Aura: not", not dock.service_setup_needed())
	dock._set_use_with_aura(false, false)
	Prefs.save_preferences({"onboarded": false})
	check("not set up: not (the setup from step 1)", not dock.service_setup_needed())
	Prefs.save_preferences({"onboarded": true})
	dock.auto_start_allowed = false
	check("headless: not", not dock.service_setup_needed())
	svc.candidates = was[0]
	svc.installer_dir = was[1]
	svc.launcher = was[2]
	svc.running = was[3]
	svc.exit_code = was[4]
	svc.stopper = was[5]
	svc.home_dir = was_home
	svc.command_setting = was_command
	dock.auto_start_allowed = was[6]
	svc.state = "idle"


# --- the dogfooding fixes -------------------------------------------------------------------------------------

## The steer box: shown while a run goes (connected, the agent service takes steer), the words typed at any stage;
## the Steer button sends only at the Rollout stage, at the other stages disabled, saying when it can be used (the words
## kept, nothing sent).
func steer_checks() -> void:
	var fake := FakeLink.new()
	bridge.link = fake
	welcome(["steer", "usage", "account"])
	_plain_view()
	var r = dock._new_run("goal", "Reach the gate")
	bridge.run_active = true
	dock._render()
	var box: LineEdit = dock._l_steer
	var btn: Button = dock._l_steer_btn
	var wait := "You can steer once the game is playing"
	var hint := "Send a steering prompt to Autopilot"
	var states := []
	for line in ["Starting the game", "Reading the game's code", "Trying things in the game", "Writing the harness"]:
		dock._on_status(line)
		states.append([line, dock._l_steer_row.visible, box.editable, box.placeholder_text, btn.disabled, btn.tooltip_text])
	var all_off := true
	for s in states:
		all_off = all_off and s[1] and s[2] and s[3] == hint and s[4] and s[5] == wait
	check("steer: before the game plays, shown and editable, the button disabled, saying when", all_off, states)
	# words typed (as at an earlier Rollout) are kept, never sent outside it: Enter, the button, the dock's own path
	box.text = "go round the rocks to the left first"
	box.text_submitted.emit(box.text)
	btn.pressed.emit()
	dock._on_steer()
	check("steer: nothing sent before Rollout, the words kept", not "steer" in fake.types() and box.text == "go round the rocks to the left first"
		and btn.disabled, fake.types())
	dock._on_status("Trying the harness (run 1)")
	check("steer: at Rollout, editable with its hint, the button enabled", box.editable and box.placeholder_text == hint and not btn.disabled
		and btn.tooltip_text == "" and r.steerable(), [box.editable, box.placeholder_text, btn.disabled])
	dock._on_status("Now: walking to the gate")
	check("steer: the Now lines are still Rollout", box.editable and not btn.disabled)
	box.text_submitted.emit(box.text)
	check("steer: sent at Rollout (Enter)", fake.sent.size() == 1 and fake.sent[0].get("type") == "steer"
		and fake.sent[0].get("text") == "go round the rocks to the left first" and box.text == "", fake.sent)
	box.text = "now jump on the hay bale"
	dock._on_status("Looking at the results")
	dock._on_steer()
	check("steer: Process result: the button disabled again, the words kept, nothing sent", box.editable and btn.disabled
		and btn.tooltip_text == wait and box.placeholder_text == hint and box.text == "now jump on the hay bale" and fake.sent.size() == 1, fake.sent)
	dock._on_status("Writing the harness")
	dock._on_status("Trying the harness (run 2)")
	check("steer: the next Rollout, enabled with the words kept", box.editable and not btn.disabled and box.text == "now jump on the hay bale")
	dock._on_status("Saving what it learned to memory")
	check("steer: Report: the button disabled", btn.disabled and dock._l_steer_row.visible)
	dock._on_stop()
	check("steer: stopping locks the box and the button", not box.editable and btn.disabled and dock._l_stop.text == "Stopping…"
		and dock._l_stop.disabled)
	# without steer in the welcome (or no run going) the row stays hidden, as before
	bridge.brain_steers = false
	dock._render()
	check("steer: hidden when the agent service doesn't take it", not dock._l_steer_row.visible)
	bridge.brain_steers = true
	bridge.run_active = false
	dock._on_result({"kind": "goal", "reached": true}, "")
	check("steer: the run done, not steerable", not r.steerable())
	box.text = ""
	dock.active = null


## The Flatness's copy (the same in the three plugins): the history's heading and the narrow button's name, the id line's
## DURATION, the tabs, the steer box, the tooltips, the report's Duration, the setup's and Settings' lines.
func flatness_checks() -> void:
	_plain_view()
	var rail_texts := _texts(dock._rail)
	check("flatness: the rail's heading History (no Run history), the narrow button named History",
		"History" in rail_texts.split("\n") and not "RUN HISTORY" in rail_texts.to_upper() and dock._hist_btn.tooltip_text == "History", rail_texts)
	var r = _make_run(false)
	dock.shown = r
	dock.tab = "main"
	dock._render()
	check("flatness: the id line says DURATION", dock._tab_meta(r) == "RUN %s // VERIFY // DURATION 03:04 // 2 ATTEMPTS" % r.id, dock._tab_meta(r))
	check("flatness: the tabs Result, Feed, Policy log · N", dock._tab_main.text == "Result" and dock._tab_feed.text == "Feed"
		and dock._tab_policy.text == "Policy log · 8", [dock._tab_main.text, dock._tab_feed.text, dock._tab_policy.text])
	check("flatness: the report's Duration", "**Duration:** 03:04" in Model.report_markdown(r) and not "Took" in Model.report_markdown(r))
	check("flatness: the steer box's placeholder and the button's wait", dock.STEER_HINT == "Send a steering prompt to Autopilot"
		and dock.STEER_WAIT == "You can steer once the game is playing")
	check("flatness: the Memory row's tooltips", dock._save_label.tooltip_text == dock.MEMORY_TIP
		and dock._reset_btn.tooltip_text == "Reset memory: the agent forgets what it has learned about this game"
		and dock.MEMORY_TIP.ends_with("“Empty”: the next run starts as on a new game."))
	check("flatness: Advanced's placeholder and tooltip", dock._svc_cmd.placeholder_text == "Detected automatically"
		and dock._url.tooltip_text == "Empty: ws://127.0.0.1:8765/v1/bridge, the agent service on this computer. You may use your own endpoint."
		and "without “serve”" in dock._svc_cmd.tooltip_text)
	var setup := FileAccess.get_file_as_string("res://addons/autopilot/ui/setup_page.gd")
	check("flatness: setup step 2's line, step 3's text", "One-time installation takes approximately 1 minute." in setup
		and dock._setup.DISCLOSURE == CONSENT)
	var source := FileAccess.get_file_as_string("res://addons/autopilot/dock.gd") + FileAccess.get_file_as_string("res://addons/autopilot/ui/settings_page.gd")
	check("flatness: no Stop trying, no Remove from this computer…, no (started by Autopilot)", not "Stop trying" in source
		and not "Remove from this computer…" in source and not "(started " in source)
	# the result's toolbar: Run again filled, Copy report and Run folder outlined, Run folder's icon the recording's
	var tips := {}
	for b in dock._result.find_children("*", "Button", true, false):
		if b.tooltip_text in ["Run again", "Copy report", "Run folder"]:
			tips[b.tooltip_text] = str(b.theme_type_variation)
	check("flatness: Run again (the accent), Copy report and Run folder (outlined)", tips == {"Run again": "AutopilotPrimary",
		"Copy report": "Button", "Run folder": "Button"}, tips)
	dock.shown = null
	dock.runs.erase(r)
	_plain_view()


## The user's round 2: the shared status line (the main column's last row on every page, over a rule, mono 11 muted,
## one line with its whole text as the tooltip, hidden when empty and 8 s after; never the agent service's progress
## lines), and the Live page without a policy column.
func round2_checks() -> void:
	var fake := FakeLink.new()
	bridge.link = fake
	_plain_view()
	_online()
	dock.shown = null
	dock._render()
	dock._set_status("")
	dock._tick_status()
	check("status line: hidden when empty", not dock._status_bar.visible)
	dock._set_status("Copied the report")
	var box := dock._status_bar.get_theme_stylebox("panel") as StyleBoxFlat
	check("status line: the main column's last row, over a 1 px rule, 6 × 16, mono 11 muted, one line, its tooltip",
		dock._status_bar.visible and dock._status_bar.get_parent() == dock._footer_slot and dock._main.get_children()[-1] == dock._footer_slot
		and box.border_width_top == 1 and box.border_color == HT.RULE and box.content_margin_left == HT.px(16) and box.content_margin_top == HT.px(6)
		and dock._status.clip_text and dock._status.autowrap_mode == TextServer.AUTOWRAP_OFF and dock._status.tooltip_text == "Copied the report"
		and dock._status.get_theme_font_size("font_size") == HT.px(11) and dock._status.get_theme_color("font_color") == HT.MUTED,
		[dock._status_bar.get_parent(), box.content_margin_left, dock._status.get_theme_font_size("font_size")])
	dock.settings_open = true
	dock._render()
	check("status line: on the Settings page too", dock._status_bar.is_visible_in_tree())
	dock.settings_open = false
	dock._render()
	dock._status_until = 0.0
	dock._tick_status()
	check("status line: gone after 8 s", not dock._status_bar.visible and dock._status.text == "" and dock.STATUS_FOR == 8.0)
	var r = dock._new_run("goal", "Reach the gate")
	bridge.run_active = true
	dock._render()
	for line in ["Starting the game", "Trying the harness (run 1)", "Now: walking to the gate", "Connected to the agent service"]:
		dock._on_status(line)
	check("status line: never the agent service's progress lines", not dock._status_bar.visible and dock._status.text == "", dock._status.text)
	# the Live page: the board's (no policy column)
	var t := _texts(dock._live)
	check("Live: no policy column (no Full log, Policy · live, Following, Observation changes)", not "Full log" in t
		and not "POLICY · LIVE" in t and not "FOLLOWING" in t and not "OBSERVATION CHANGES" in t
		and not "Waiting for the first policy step" in t, t)
	check("Live: the live rows are gone from the code", not "LiveRows" in FileAccess.get_file_as_string("res://addons/autopilot/ui/widgets.gd")
		and not "_render_why" in FileAccess.get_file_as_string("res://addons/autopilot/dock.gd"))
	bridge.run_active = false
	dock._on_result({"kind": "goal", "reached": true}, "")
	dock.shown = null
	dock.runs.erase(r)
	dock.active = null
	_plain_view()


## The user's round 3: the Policy log's Find an action and ◀ ▶, Get Aura, Back to the last run, and the steer box on the
## Feed tab (the Live page's own box: one text and state). (Reset memory in place: run_checks.)
func round3_checks() -> void:
	var fake := FakeLink.new()
	bridge.link = fake
	_plain_view()
	_online()
	var r = _make_run(false)
	dock.shown = r
	dock.tab = "policy"
	dock._render()
	var log = dock._policy
	check("policy log: Find an action…, a 200 px field in the filters", log._find.placeholder_text == "Find an action…"
		and log._find.custom_minimum_size.x == HT.px(200) and log._find.get_parent() == log._filters)
	log._find.text = "jump"
	log._find.text_changed.emit("jump")
	var found: Array = log.shown_steps()
	var all_jump: bool = not found.is_empty() and found.size() < r.steps.size()
	for st in found:
		all_jump = all_jump and "jump" in st.chosen
	check("policy log: Find filters the rows by the action's name", all_jump and log._root.get_child_count() == found.size(),
		[found.size(), r.steps.size(), log._root.get_child_count()])
	log._find.text = ""
	log._find.text_changed.emit("")
	check("policy log: Find emptied, every row", log.shown_steps().size() == r.steps.size())
	log.set_follow(true)
	check("policy log: ◀ ▶ beside Copy step JSON (Previous step, Next step); ▶ off on the latest", log._prev_btn.tooltip_text == "Previous step"
		and log._next_btn.tooltip_text == "Next step" and log._prev_btn.get_parent() == log._toggles and log._next_btn.disabled and not log._prev_btn.disabled)
	log.step_by(-1)
	check("policy log: ◀ picks the step before, paused on it, Follow live", log.selected == r.steps[-2] and not log.follow
		and log._follow_label.text == "PAUSED ON %s" % r.steps[-2].label() and log._follow_btn.visible and log._follow_btn.text == "Follow live"
		and not log._next_btn.disabled, [log._follow_label.text])
	log.step_by(1)
	check("policy log: ▶ the next", log.selected == r.steps[-1] and log._next_btn.disabled)
	var picked_h: int = r.steps[-1].attempt
	check("policy log footer: a step picked, its harness (H%d: …)" % picked_h, log._summary.text.begins_with("H%d: " % picked_h),
		log._summary.text)
	press(log, "Follow live")
	check("policy log: Follow live: following again", log.follow and not log._follow_btn.visible, log._follow_label.text)
	check("policy log footer: All, following: All harnesses: …", log._summary.text.begins_with("All harnesses: "), log._summary.text)
	log._set_attempt(1)
	check("policy log footer: the H1 filter: H1: …", log._summary.text.begins_with("H1: "), log._summary.text)
	log._set_attempt(0)
	dock._set_status("")
	press(log, "Copy step JSON")
	check("policy log: Copy step JSON says Copied", dock._status.text == "Copied", dock._status.text)

	# buttons that can't act now (not connected, a run going): greyed, still pressable, the status line saying why
	dock.tab = "main"
	dock._render()
	var again_of := func() -> Button:
		for b in dock._result.find_children("*", "Button", true, false):
			if b.tooltip_text == "Run again":
				return b
		return null
	check("greyed: online, no run going: Run again and Reset memory… as usual", not HT.greyed(again_of.call())
		and not HT.greyed(dock._reset_btn))
	unwelcome()
	dock._render()
	var again: Button = again_of.call()
	check("greyed: not connected: Run again greyed, not disabled", again != null and HT.greyed(again) and not again.disabled
		and is_equal_approx(again.modulate.a, 0.45))
	var n_runs: int = dock.runs.size()
	var request: Dictionary = r.request
	r.request = {"kind": "goal", "goal": "Reach the gate"}
	again.pressed.emit()
	r.request = request
	check("greyed: Run again pressed, not connected: no run, the status line says why", dock.runs.size() == n_runs
		and dock._status.text == "Not connected: connect first", dock._status.text)
	check("greyed: Reset memory… greyed, not disabled", HT.greyed(dock._reset_btn) and not dock._reset_btn.disabled)
	dock._reset_btn.pressed.emit()
	check("greyed: Reset memory… pressed, not connected: no confirmation, the status line says why", dock._reset != "ask"
		and dock._status.text == "Not connected: connect first to reset the memory", dock._status.text)
	var card = dock._engine_card(r, {"signature": "fell_out", "summary": "The player fell below the level"})
	var nab: Button = null
	for b in card.find_children("*", "Button", true, false):
		if b.text == "Not a bug":
			nab = b
	check("greyed: Not a bug, not connected: greyed, not disabled", nab != null and HT.greyed(nab) and not nab.disabled)
	nab.pressed.emit()
	check("greyed: Not a bug pressed, not connected: the status line says why", dock._status.text == "Not connected: connect to send feedback"
		and not r.not_a_bug.has("fell_out"), dock._status.text)
	card.free()

	# Back to the last run: the connect page (Disconnected) with the run shown before
	fake.problem = ""
	fake.problem_code = 0
	dock._select_run(null)
	check("Disconnected: Back to the last run (a small quiet button under the card)", dock._connect_page.visible and dock._cp_back.visible
		and dock._cp_back.theme_type_variation == "AutopilotQuietSm" and dock.connect_buttons() == ["Reconnect"], dock.connect_buttons())
	check("Back to the last run: that run's page", press(dock._connect_page, "Back to the last run") and dock.shown == r
		and dock._run_page.visible)
	fake.want = true
	dock._render()
	dock._select_run(null)
	check("connecting: no Back to the last run", not dock._cp_back.visible)
	fake.want = false
	dock.runs.erase(r)
	dock._render()
	check("the run gone: no Back to the last run", not dock._cp_back.visible)

	# Get Aura: the Aura app's agent service wanted, Aura not installed
	dock._set_use_with_aura(true, false)
	fake.want = true
	fake.with_aura = true
	fake.ws = null
	fake.problem = "Aura isn’t installed on this machine."
	dock._render()
	var st: Dictionary = dock.connect_state()
	if Prefs.aura_installed():
		check("Get Aura: (Aura is installed on this computer: not checked)", true)
	else:
		check("Aura not installed: its line, Get Aura, Try now, Stop", st.text == "Aura isn’t installed on this machine."
			and st.buttons.map(func(b): return b[0]) == ["Get Aura", "Try now", "Stop"] and st.buttons[0][1] == "Button"
			and st.buttons[0][2] == "get_aura" and Prefs.AURA_DOWNLOAD_URL.begins_with("https://"), st)
	fake.close()
	fake.problem = ""
	dock._set_use_with_aura(false, false)

	# the steer box on the Feed tab: the Live page's box, moved (the same words and state)
	_online()
	_plain_view()
	var lr = dock._new_run("goal", "Reach the gate")
	bridge.run_active = true
	dock.tab = "main"
	dock._render()
	dock._l_steer.text = "go round the rocks"
	dock.tab = "feed"
	dock._render()
	check("Feed tab, live: the steer box at its foot, the words typed on the Live page in it", dock._l_steer_row.get_parent() == dock._feed_steer_slot
		and dock._l_steer_row.is_visible_in_tree() and dock._l_steer.text == "go round the rocks" and dock._l_steer_btn.disabled)
	dock._on_status("Trying the harness (run 1)")
	check("Feed tab: at Rollout the Steer button on", not dock._l_steer_btn.disabled)
	dock._l_steer_btn.pressed.emit()
	check("Feed tab: Steer sends, the box emptied (on the Live page too)", fake.types().has("steer") and dock._l_steer.text == "", fake.sent)
	dock.tab = "main"
	dock._render()
	check("Live again: the box back at the Live page's foot", dock._l_steer_row.get_parent() == dock._live and dock._l_steer_row.is_visible_in_tree())
	bridge.run_active = false
	dock._on_result({"kind": "goal", "reached": true}, "")
	dock.tab = "feed"
	dock._render()
	check("Feed tab, the run done: no steer box", not dock._l_steer_row.is_visible_in_tree())
	dock.tab = "main"
	dock.shown = null
	dock.runs.erase(lr)
	dock.active = null
	_plain_view()


## The run going ended by its result (the agent service's result frame), so the next check starts with none.
func _end_run(claim := "") -> void:
	if dock.active != null or bridge.run_active:
		bridge._on_brain_frame({"type": "result", "result": {"verdict": "CONFIRMED", "reason": "ok", "claim": claim}})


## Runs from the user's own Claude Code or Codex (run_started), the Settings page's Claude Code and Codex plate, and the
## data sharing text that says what such a run sends.
func assistants_checks() -> void:
	var fake := FakeLink.new()
	bridge.link = fake
	_plain_view()
	_online()
	dock.wide = true
	dock.size = Vector2(1100, 800)
	_end_run()
	var before: int = dock.runs.size()
	fake.sent.clear()

	# a run started from Claude Code: shown as one the tab started, with where it came from
	bridge._on_brain_frame({"type": "run_started", "run": {"kind": "verify", "claim": "Verify that the crate turns green",
		"time_limit_minutes": 15, "by": "claude-code"}})
	var r = dock.active
	check("run_started: a run going, shown live", r != null and dock.runs.size() == before + 1 and dock.shown == r
		and bridge.run_active and dock._run_page.visible and dock._live.visible and r.live(), [r, dock.runs.size()])
	check("run_started: its claim as the request, its kind", r != null and r.prompt == "Verify that the crate turns green"
		and r.kind == "verify" and r.request.get("claim") == "Verify that the crate turns green"
		and r.request.get("time_limit_minutes") == 15 and not r.request.has("by"), r.request if r else null)
	check("run_started: \"Started from Claude Code\" under the request", dock._l_origin.visible
		and dock._l_origin.text == "Started from Claude Code" and dock._l_prompt.text == "Verify that the crate turns green",
		dock._l_origin.text)
	check("run_started: the tab sent no start of its own", not fake.types().has("start"), fake.sent)
	check("run_started: Stop works on it", not dock._l_stop.disabled)
	check("run_started: it can be steered (the agent service takes steer)", dock._can_steer_shown())
	bridge._on_brain_frame({"type": "status", "text": "Reading the game's code"})
	check("its status lines go on it", r.stage == 1 and r.feed.size() > 0, [r.stage, r.feed])
	dock._on_stop()
	check("Stop: sent for it", fake.types().has("stop"), fake.sent)

	# another run_started while it goes: ignored
	bridge._on_brain_frame({"type": "run_started", "run": {"kind": "goal", "goal": "Light a lantern", "by": "codex"}})
	check("run_started while a run is going: ignored", dock.active == r and dock.runs.size() == before + 1)

	bridge._on_brain_frame({"type": "result", "result": {"verdict": "CONFIRMED", "reason": "It turned green.",
		"claim": "Verify that the crate turns green"}})
	dock._render()
	check("its result: shown and kept as any run's", dock.active == null and r.verdict() == "CONFIRMED" and not r.live()
		and dock._result.visible)
	check("its result: \"Started from Claude Code\" under the request", "Verify that the crate turns green\nStarted from Claude Code"
		in _texts(dock._result), _texts(dock._result))
	check("Run again: from the tab, as any run (no origin sent)", r.request.get("kind") == "verify" and not r.request.has("by"))

	# Codex, another coding agent, none named; an exploration as the composer words one
	for case in [["codex", "Started from Codex"], ["cursor", "Started from your coding agent"],
			[null, "Started from your coding agent"]]:
		var run := {"kind": "goal", "goal": "Light a lantern"}
		if case[0] != null:
			run.by = case[0]
		bridge._on_brain_frame({"type": "run_started", "run": run})
		check("run_started by %s: \"%s\"" % [case[0], case[1]], dock.active != null and dock.active.origin_line() == case[1]
			and dock._l_origin.text == case[1] and dock.active.prompt == "Light a lantern", dock._l_origin.text)
		_end_run("Light a lantern")
	bridge._on_brain_frame({"type": "run_started", "run": {"kind": "explore", "brief": "the market stall", "minutes": 5,
		"by": "codex"}})
	check("run_started explore: shown as the composer words one", dock.active != null and dock.active.kind == "explore"
		and dock.active.prompt == "Explore the market stall" and dock.active.request.get("minutes") == 5,
		dock.active.prompt if dock.active else null)
	_end_run()
	bridge._on_brain_frame({"type": "run_started", "run": {"kind": "auto", "prompt": "Reach the lighthouse",
		"time_limit_minutes": 20, "by": "claude-code"}})
	check("run_started auto: its words as typed, its kind blank", dock.active != null and dock.active.prompt == "Reach the lighthouse"
		and dock.active.kind == "" and dock.active.request.get("kind") == "auto", dock.active.kind if dock.active else null)
	bridge._on_brain_frame({"type": "run_kind", "kind": "goal", "tested": false})
	check("run_started auto: its kind once run_kind says it", dock.active != null and dock.active.kind == "goal")
	_end_run("Reach the lighthouse")
	var tab_run = Model.Run.new()  # (a run of the tab's own)
	check("a run the tab started says nothing of where it came from", tab_run.origin_line() == "")

	# the first-run setup open: the run goes on behind it, the setup stays
	dock._open_setup()
	dock._render()
	bridge._on_brain_frame({"type": "run_started", "run": {"kind": "verify", "claim": "Verify that the gate opens",
		"by": "codex"}})
	check("run_started with the first-run setup open: the setup stays", dock.onboarding and dock._setup.visible
		and not dock._body.visible and dock.active != null and dock.active.origin_line() == "Started from Codex")
	finish("own_keys", true)
	dock._render()
	check("the setup done: the run shown", dock._run_page.visible and dock.active != null and dock.shown == dock.active)
	_end_run("Verify that the gate opens")

	# the Settings page: Autopilot in Claude Code & Codex, after Data sharing, before Uninstall
	_plain_view()
	dock.settings_open = true
	dock._render()
	var copied := []
	dock._settings_page.copy_text = func(text: String) -> void: copied.append(text)
	dock._render_usage()
	var t := _texts(dock._settings_page)
	var intro := ("One-time CLI setup to run Autopilot through Claude Code or Codex using your Claude or ChatGPT "
		+ "subscriptions, respectively.")
	check("Settings: Autopilot in Claude Code & Codex, its words", "AUTOPILOT IN CLAUDE CODE & CODEX" in t and intro in t
		and "autopilot connect claude" in t and "autopilot connect codex" in t, t)
	check("Settings: nothing under the commands", not "Then ask it" in t and not "Runs still use" in t, t)
	check("Settings: after Data sharing, before Uninstall", t.find(CONSENT) >= 0
		and t.find(CONSENT) < t.find("AUTOPILOT IN CLAUDE CODE & CODEX")
		and t.find("AUTOPILOT IN CLAUDE CODE & CODEX") < t.find("UNINSTALL AUTOPILOT"), t)
	check("Settings: no other words about it (no MCP, no Jev)", not "MCP" in t and not "Jev" in t)
	var copies := []
	for b in dock._settings_page.find_children("*", "Button", true, false):
		if b.text == "Copy" and b.is_visible_in_tree():
			copies.append(b)
	check("Settings: a Copy button per command", copies.size() == 2, copies.size())
	for b in copies:
		b.pressed.emit()
	check("Copy: the command to the clipboard", copied == ["autopilot connect claude", "autopilot connect codex"], copied)
	# the data sharing text says what such a run sends, on the Settings page and in the setup's Privacy settings
	check("Settings: the data sharing text names the coding agent's conversation", CONSENT in t
		and SettingsPage.SHARING.ends_with(" For runs from your own Claude Code or Codex, the agent traces are its conversation during the run."))
	dock.settings_open = false
	dock._open_setup()
	dock._setup._go(3)
	check("setup's Privacy settings: the same text", CONSENT in setup_text(), setup_text())
	finish("own_keys", true)
	dock._render()


## The cross-engine fixes (the consistency review's): the feed's lines (Run started, one Stopping…, no Run finished, AGENT
## and ENGINE by severity, the Live page's last 10, Nothing yet.), ● LOCAL AGENT, Stop's rule, the prompt kept, the
## history's prompt cut at 140, the report's one format, the Policy log's words (● FOLLOWING, FAILED, the empty states,
## π(a | s) lowercase, ".55", "1.00", "4.0 s"), the times (m:ss, h:mm:ss), the setup's recap, curly apostrophes.
func fixes_checks() -> void:
	var fake := FakeLink.new()
	bridge.link = fake
	_plain_view()
	_online()
	dock.wide = true
	dock.size = Vector2(1100, 800)
	# the feed's lines from the bridge
	dock._compose("Reach the gate")
	check("Run: the run started", press(dock._composer, "Run") and dock.active != null)
	var r = dock.active
	check("the prompt stays in the box after Run", dock._c_prompt.text == "Reach the gate", dock._c_prompt.text)
	check("feed: Run started (not \"Started goal run\")", r.feed.map(func(e): return e.text).has("Run started"),
		r.feed.map(func(e): return e.text))
	fake.url = "ws://127.0.0.1:8765/v1/bridge"
	dock._render()
	check("● LOCAL AGENT: a live run, the agent service on this computer", dock._tab_local.visible and dock._tab_local.text == "● LOCAL AGENT")
	fake.url = "ws://10.1.2.3:8765/v1/bridge"
	dock._render()
	check("● LOCAL AGENT: not with an agent service elsewhere", not dock._tab_local.visible)
	fake.url = "ws://localhost:8765/v1/bridge"
	check("local_agent: localhost, 127.x, [::1]", dock.local_agent())
	dock._on_finding({"source": "agent", "summary": "The gate opens with three orbs"})
	check("feed: an agent finding after AGENT, red", r.feed[-1].kind == "agent" and r.feed[-1].tone == "bad", r.feed[-1])
	dock._on_finding({"detector": "nan", "severity": "critical", "summary": "NaN transform", "signature": "n1"})
	check("feed: an engine finding (critical) after ENGINE, red", r.feed[-1].kind == "engine" and r.feed[-1].tone == "bad", r.feed[-1])
	dock._on_finding({"detector": "fall", "severity": "warn", "summary": "fell", "signature": "f1"})
	check("feed: warn amber", r.feed[-1].tone == "warn")
	dock._on_finding({"detector": "slow", "summary": "slow frame", "signature": "s1"})
	check("feed: no severity: info", r.feed[-1].tone == "info")
	check("severity: error, critical, fatal, bug red; warning, warn amber; else info",
		["error", "critical", "fatal", "bug"].all(func(x): return dock.severity_kind(x) == "bad")
		and dock.severity_kind("Warning") == "warn" and dock.severity_kind("warn") == "warn" and dock.severity_kind("") == "info"
		and dock.severity_kind("note") == "info")
	var card = dock._engine_card(r, {"detector": "slow", "summary": "slow frame", "signature": "s1"})
	check("engine card: no severity: an INFO tag (blue)", "INFO" in _texts(card) and not "WARNING" in _texts(card) and not "ALERT" in _texts(card),
		_texts(card))
	card.free()
	for i in 14:
		r.add_feed("Status line %d" % i, "status")
	dock.tab = "main"
	dock._render()
	var lines: PackedStringArray = dock._l_feed.get_parsed_text().strip_edges().split("\n")
	check("Live: the feed plate shows the last 10 lines", lines.size() == 10 and lines[-1].ends_with("Status line 13"), lines)
	fake.connected = false
	dock._render_live()
	check("Stop: not while not connected", dock._l_stop.disabled)
	fake.connected = true
	dock._render_live()
	check("Stop: connected, the run going, not stopping", not dock._l_stop.disabled)
	dock._on_stop()
	check("Stop: Stopping…, once in the feed, Stop disabled", r.feed.filter(func(e): return e.text == "Stopping…").size() == 1
		and dock._l_stop.disabled and dock._l_stop.text == "Stopping…")
	check("steer: no tooltip while stopping", dock._l_steer_btn.tooltip_text == "")
	dock._on_status("Run finished. Report: C:/somewhere")
	check("feed: no Run finished line before the result", not r.feed.map(func(e): return e.text).has("Run finished"))
	bridge.run_active = false
	dock._on_result({"kind": "goal", "reached": false, "reason": "Never got past the rocks"}, "")
	check("feed: Result: Goal missed", r.feed[-1].text == "Result: Goal missed" or r.feed[-2].text == "Result: Goal missed")
	# the Feed tab: "Nothing yet." with no plate
	var empty = dock._new_run("goal", "")
	dock.active = null
	empty.ended_ms = empty.started_ms
	dock.shown = empty
	dock.tab = "feed"
	dock._render()
	check("Feed tab: Nothing yet. (12 px, muted), no plate", dock._feed_empty.visible and not dock._feed_box.visible
		and "Nothing yet." in _texts(dock._feed_empty))
	check("prompt fallbacks: Play a goal, Explore, Verify, Run", empty.shown_prompt() == "Play a goal"
		and Model.prompt_shown("", "explore") == "Explore" and Model.prompt_shown("", "verify") == "Verify"
		and Model.prompt_shown("", "") == "Run" and Model.prompt_shown("Find bugs", "explore") == "Find bugs")
	dock.tab = "main"
	# the history's prompt: wrapped, cut at 140
	var long_run = dock._new_run("goal", "word ".repeat(60))
	dock.active = null
	long_run.ended_ms = long_run.started_ms
	var row: Control = dock._history_row(long_run)
	var pl: Label = null
	for l in row.find_children("*", "Label", true, false):
		if l.text.begins_with("word"):
			pl = l
	check("history: the prompt wraps, cut at 140 characters with …", pl != null and pl.autowrap_mode != TextServer.AUTOWRAP_OFF
		and pl.text.length() <= 140 and pl.text.ends_with("…") and not pl.clip_text, pl.text if pl else null)
	row.free()
	# the report: one format
	var rr = _make_run(false)
	rr.findings.append({"source": "agent", "summary": "The fruit floats"})
	rr.not_a_bug["sig1"] = true
	var md: String = Model.report_markdown(rr)
	check("report: Prompt, Verdict, Duration · Harnesses · Policy steps · Alerts, Reason, Findings ([agent], [engine · …]), not a bug",
		md.begins_with("# Autopilot run ") and "**Prompt:** " in md and "**Verdict:** INCONCLUSIVE  \n**Duration:** 03:04 · **Harnesses:** 2" in md
		and "**Alerts:** 1" in md and "## Reason" in md and "## Findings" in md and "- [agent] The fruit floats" in md
		and "- [agent] The fruit can be picked up" in md and "- [engine · error · script_error_detector_with_a_long_name] " in md
		and " (×3) at 1200.5, -30.25, 455 cm (not a bug)" in md and not "## Agent findings" in md and not "## Engine findings" in md, md)
	# the times and numbers
	check("times: mm:ss, h:mm:ss; feed and player position m:ss, h:mm:ss", Model.clock(83) == "01:23" and Model.clock(3725) == "1:02:05"
		and Model.short_clock(48) == "0:48" and Model.short_clock(3725) == "1:02:05")
	check("numbers: .55, 1.00, 4.0 s, —", Model.prob(0.55) == ".55" and Model.prob(1.0) == "1.00" and Model.prob(0.999) == "1.00"
		and Model.secs(4) == "4.0 s" and Model.secs(null) == "—")
	# the Policy log's words
	dock.shown = rr
	dock.tab = "policy"
	dock._render()
	var log = dock._policy
	log.set_follow(true)
	check("policy log: following a finished run: ● FOLLOWING (never ● LATEST)", log._follow_label.text == "● FOLLOWING")
	check("policy log: no tooltip on Find an action", log._find.tooltip_text == "")
	var tl := _texts(log)
	check("policy log: π(a | s) lowercase", "POLICY OUTPUT · π(a | s)" in tl and not "Π(A | S)" in tl, tl)
	check("policy log: no FAILED chip while no step failed", not log._tag_chips.FAILED.visible)
	rr.add_step({"type": "jev", "call": 9, "t": 14.0, "error": "the model timed out", "decided": {"summary": "kept: wait"}})
	log.on_step(rr.steps[-1])
	log._rebuild()
	log.step_by(1)
	check("policy log: a failed step: FAILED, its chip, This step failed: …, · 1 failed",
		rr.steps[-1].tag == "FAILED" and log._tag_chips.FAILED.visible and log._d_fail.visible
		and log._d_fail.text == "This step failed: the model timed out" and " · 1 failed" in log._summary.text, [log._summary.text, log._d_fail.text])
	var none_run = dock._new_run("goal", "")
	dock.active = null
	none_run.ended_ms = none_run.started_ms
	log.set_run(none_run)
	check("policy log: no steps yet: No policy steps yet.", log._d_head.text == "No policy steps yet." and "No policy steps yet." in _texts(log))
	log.set_run(rr)
	log.attempt_filter = 0
	log.set_follow(true)
	log.find_text = "no such action"
	log._rebuild()
	check("policy log: nothing picked or shown: Pick a step …", log._d_head.text == "Pick a step to see what the policy observed and chose.",
		log._d_head.text)
	log.find_text = ""
	log._rebuild()
	# the setup's recap and step 2 waiting
	dock._setup.access = "aura"
	dock._setup.use_aura = true
	check("recap: Aura app, The Aura app runs it", dock._setup.recap() == ["Aura app", "The Aura app runs it"], dock._setup.recap())
	dock._setup.use_aura = false
	dock._setup.access = ""
	# curly apostrophes and the one ellipsis
	check("copy: curly apostrophes, the one-character ellipsis", SettingsPage.DELETE_MEMORY.contains("Autopilot’s")
		and not FileAccess.get_file_as_string("res://addons/autopilot/editor_bridge.gd").contains("Stopping...")
		and dock.sign_in_error({"reason": "x"}).message == "The sign-in didn’t finish. Try again.")
	for x in [r, empty, long_run, rr, none_run]:
		dock.runs.erase(x)
	dock.shown = null
	dock.active = null
	dock.tab = "main"
	_plain_view()


## Types these words in the composer's box (as the user would: text_changed follows).
func type_prompt(text: String) -> void:
	dock._c_prompt.text = text
	dock._c_prompt.text_changed.emit()


## A recording's frames as the agent service writes them (000000.jpg up), made once: the folder.
func frames_dir(n := 5) -> String:
	var dir := OS.get_environment("UNITS_DIR").path_join("agent_recording").path_join("frames")
	if FileAccess.file_exists(dir.path_join("%06d.jpg" % (n - 1))):
		return dir
	DirAccess.make_dir_recursive_absolute(dir)
	for i in n:
		var img := Image.create(64, 36, false, Image.FORMAT_RGB8)
		img.fill(Color(0.2 * i, 0.6, 0.3))
		img.save_jpg(dir.path_join("%06d.jpg" % i))
	return dir


## One prompt box (its words as typed, the kind read by the agent service), Rerun setup (the footer's and the Settings page's), the run history's selection,
## memory at the end of a run (the feed, the stage, the welcome and composer notes) and the recording's player.
func dogfood_checks() -> void:
	var fake := FakeLink.new()
	bridge.link = fake
	welcome(["steer", "usage", "account"])
	_plain_view()
	dock.active = null
	bridge.run_active = false
	var t := ""

	# one composer: no tabs, no kind read from the words as they are typed, the Time limit for every run, Run once there are words
	dock.shown = null
	dock._c_prompt.text = ""
	dock._compose("")
	t = _texts(dock._composer)
	check("composer: one prompt box, no Verify / Explore / Play a goal tabs, no design brief field", dock._composer.visible
		and _count(dock._composer, "TextEdit") == 1 and _count(dock._composer, "LineEdit") <= 1 and not "PLAY A GOAL" in t
		and not "VERIFY
" in t and not "brief" in t.to_lower(), t)
	check("composer: its placeholder promises no kind from the first words", dock._c_prompt.placeholder_text
		== "Describe what to verify, reach or explore" and not "start with" in dock._c_prompt.placeholder_text.to_lower(),
		dock._c_prompt.placeholder_text)
	var cancels: Array = dock._composer.find_children("*", "Button", true, false).filter(func(b): return b.text == "Cancel")
	check("composer: no Cancel in the New run header (a history row goes back to a run)", cancels.is_empty(), cancels.size())
	check("composer empty: Run (in the box) disabled, the Time limit shown", dock._c_run.disabled
		and dock._c_run.get_parent() == dock._c_row and dock._c_time_row.is_visible_in_tree(), dock._c_run.disabled)
	check("composer: no explainers under the box", not "Autopilot remembers what each run learns" in t and not "RUNS AS" in t
		and not "Start with Verify… to check a claim" in t, t)
	for words in ["Verify that doors open", "explore the game for bugs: farm area", "Reach the castle gate"]:
		type_prompt(words)
		var tags := _texts(dock._composer)
		check("composer: \"%s\" typed, no kind tag, the Time limit, Run enabled" % words, not "
VERIFY
" in tags
			and not "
EXPLORE
" in tags and not "
GOAL
" in tags and dock._c_time_row.is_visible_in_tree()
			and not dock._c_run.disabled, tags)
	check("composer: TIME LIMIT at the right of the box's last row, then Run", dock._c_time_row.get_parent() == dock._c_row
		and dock._c_time_row.get_index() == dock._c_row.get_child_count() - 2 and dock._c_run.get_index() == dock._c_row.get_child_count() - 1
		and "TIME LIMIT" in _texts(dock._c_time_row) and not "DURATION" in _texts(dock._composer) and not "MINUTES" in _texts(dock._composer),
		_texts(dock._c_time_row))
	dock._c_time_limit.value = 40
	check("composer: its Time limit kept for the project as it is edited", dock.time_limit() == 40
		and dock._meta("time_limit_minutes", 0) == 40, dock._meta("time_limit_minutes", 0))
	dock._compose("")
	check("composer: opened again, the Time limit as last set", dock._c_time_limit.value == 40, dock._c_time_limit.value)
	dock._c_time_limit.value = 45
	type_prompt("   ")
	check("composer: only spaces, Run disabled", dock._c_run.disabled)
	# every run: the words as typed (spaces around them gone) and the Time limit; the kind blank until run_kind says it
	for words in ["Verify that doors open", "  Can the player swim? ", "Sell three crops", "Find bugs in the shop", "Explore"]:
		dock._compose(words)
		dock._c_time_limit.value = 7
		fake.sent.clear()
		var pressed := press(dock._composer, "Run")
		var sent: Array = fake.sent.filter(func(f): return f.get("type") == "start")
		var want := {"kind": "auto", "prompt": words.strip_edges(), "time_limit_minutes": 7}
		check("Run \"%s\": the start frame %s" % [words, JSON.stringify(want)], pressed and sent.size() == 1 and sent[0].run == want
			and dock.runs[-1].prompt == words.strip_edges() and dock.runs[-1].kind == "" and dock.runs[-1].request == want,
			[fake.sent, dock.runs[-1].prompt, dock.runs[-1].kind])
		dock._compose(words)
		check("Run \"%s\": a run going, Run greyed (not disabled)" % words, dock.active != null and not dock._c_run.disabled
			and HT.greyed(dock._c_run))
		var n_runs: int = dock.runs.size()
		dock._c_run.pressed.emit()
		check("Run \"%s\": pressed while a run goes, the status line says why, no run" % words, dock.runs.size() == n_runs
			and dock._status.text == "A run is going: wait for its result, or stop it" and dock._status_bar.visible, dock._status.text)
		dock.composing = false
		bridge.run_active = false
		dock._on_result({"kind": "goal", "reached": true}, "")
	# run_kind: the kind the agent service read, on the run going from then on; a result asked as a test: GAME TESTED
	dock._compose("Lvl_HordeArea test this game and find all the bugs with animation")
	press(dock._composer, "Run")
	var asked = dock.active
	check("run_kind: none yet, the run's kind blank", asked != null and asked.kind == "" and asked.kind_word() == "RUN")
	bridge._on_brain_frame({"type": "run_kind", "kind": "sideways", "tested": false})
	check("run_kind: a kind that isn't one changes nothing", asked.kind == "")
	bridge._on_brain_frame({"type": "run_kind", "kind": "explore", "tested": true})
	check("run_kind: the run shown as that kind", asked.kind == "explore" and asked.kind_word() == "EXPLORE"
		and asked.request.kind == "auto", [asked.kind, asked.request])
	bridge.run_active = false
	bridge._on_brain_frame({"type": "result", "result": {"kind": "goal", "reached": true, "notes": ["Three bugs found"], "tested": true}})
	dock.shown = asked
	dock._render()
	check("result tested: GAME TESTED, in EXPLORED's colors", dock.active == null and asked.verdict() == "GAME TESTED"
		and dock._r_banner.text == "GAME TESTED" and dock._r_banner.fill == HT.verdict_colors("EXPLORED")[0],
		[asked.verdict(), dock._r_banner.text])
	dock._compose("Explore the farm")
	press(dock._composer, "Run")
	bridge._on_brain_frame({"type": "run_kind", "kind": "explore", "tested": false})
	bridge.run_active = false
	bridge._on_brain_frame({"type": "result", "result": {"kind": "goal", "reached": true, "notes": ["Nothing found"]}})
	check("result not tested: EXPLORED", dock.runs[-1].verdict() == "EXPLORED", dock.runs[-1].verdict())
	dock.composing = false
	var card = dock._agent_card("The gate sinks into the ground")
	for b in card.find_children("*", "Button", true, false):
		if b.text == "Verify a fix":
			b.pressed.emit()
	card.free()
	check("Verify a fix fills the box", dock.composing and dock._composer.visible and dock._c_prompt.text == "Verify that this is fixed: The gate sinks into the ground",
		dock._c_prompt.text)
	dock.composing = false

	# Rerun setup: on the Settings page only (not in the footer any more); the first-run setup from its first step
	dock._render()
	check("Rerun setup: not in the footer", not "Rerun setup" in _texts(dock._footer), _texts(dock._footer))
	bridge._on_brain_frame({"type": "account", "mode": "aura", "email": "dev@example.org", "signed_in": true, "keys": {}})
	dock.settings_open = true
	dock._render()
	var rerun: Button = null
	for b in dock._settings_page.find_children("*", "Button", true, false):
		if b.text == "Rerun setup":
			rerun = b
	check("Rerun setup: its tooltip says what it does, BYOK named so", rerun != null and "first-run setup" in rerun.tooltip_text
		and "BYOK" in rerun.tooltip_text and not "own keys" in rerun.tooltip_text.to_lower(), rerun.tooltip_text if rerun else "")
	dock._setup.step = 3
	rerun.pressed.emit()
	check("Rerun setup: opens the setup at its first step", dock.onboarding and dock._setup.step == 1
		and "Welcome to Autopilot" in setup_text() and "Create Account" in setup_text(), dock._setup.step)
	check("welcome: no intro, no callout", not "Autopilot learns your game as it plays" in setup_text()
		and not "Choose how to run it" in setup_text(), setup_text())
	finish("", true)
	dock.settings_open = false
	dock._new_run("goal", "Reach the gate")
	dock._render()
	dock.rerun_setup()
	check("Rerun setup: a run going, the setup doesn't open", not dock.onboarding)
	dock.settings_open = true
	dock._render()
	var again: Button = null
	for b in dock._settings_page.find_children("*", "Button", true, false):
		if b.text == "Rerun setup":
			again = b
	check("Settings' Rerun setup: a run going, disabled, saying why", again != null and again.disabled and again.tooltip_text == "Finish or stop the run first")
	bridge.run_active = false
	dock._on_result({"kind": "goal", "reached": true}, "")
	dock.settings_open = true
	dock._render()
	again = null
	for b in dock._settings_page.find_children("*", "Button", true, false):
		if b.text == "Rerun setup":
			again = b
	check("Rerun setup: the run over, enabled", again != null and not again.disabled and again.tooltip_text != "Finish or stop the run first")
	for case in [["aura", {"mode": "aura", "email": "dev@example.org", "signed_in": true}], ["BYOK", {"mode": "own_keys", "signed_in": false}],
			["signed out", {"mode": "aura", "email": "dev@example.org", "signed_in": false}], ["not set up", {"mode": "none", "signed_in": false}]]:
		bridge._on_brain_frame(case[1].merged({"type": "account", "keys": {}}))
		dock.settings_open = true
		dock._render()
		t = _texts(dock._settings_page)
		var n := 0
		for b in dock._settings_page.find_children("*", "Button", true, false):
			if b.text == "Rerun setup" and b.is_visible_in_tree():
				n += 1
		check("Settings, %s: one Rerun setup, no Change sign-in nor Setup…, no Own keys" % case[0], n == 1 and not "CHANGE SIGN-IN" in t
			and not "SETUP…" in t and not "RUN SETUP AGAIN" in t and not _says_own_keys(dock._settings_page), [n, t])
	bridge._on_brain_frame({"type": "account", "mode": "aura", "email": "dev@example.org", "signed_in": true, "keys": {}})
	dock.settings_open = true
	dock._render()
	var order := []
	for b in dock._settings_page.find_children("*", "Button", true, false):
		if b.text in ["Rerun setup", "Sign out"]:
			order.append(b.text)
	check("Settings, aura: Rerun setup, then Sign out", order == ["Rerun setup", "Sign out"], order)
	dock._setup.step = 3
	check("Settings' Rerun setup: opens the setup at its first step", press(dock._settings_page, "Rerun setup") and dock.onboarding
		and dock._setup.step == 1 and dock._setup.visible)
	finish("", true)
	dock.settings_open = false

	# the run history: the selected run filled the accent at plate weight with an accent bar, its text as usual; hover a neutral fill
	dock._new_run("verify", "Verify that the gate opens")
	bridge.run_active = false
	dock._on_result({"verdict": "confirmed", "reason": "It opens."}, "")
	var newer = dock._new_run("verify", "Verify that the lever moves")
	bridge.run_active = false
	dock._on_result({"verdict": "confirmed", "reason": "It moves."}, "")
	dock.shown = newer
	dock.composing = false
	dock._render()
	var rows: Array = dock._rail_list.get_children()
	var sel_box: StyleBoxFlat = rows[0].get_theme_stylebox("panel")
	var bg: Color = sel_box.bg_color
	check("history: the selected row, ROW_ACTIVE and a 2 px accent rule on its left", bg.is_equal_approx(HT.ROW_ACTIVE)
		and sel_box.draw_center and sel_box.border_width_left == 2
		and sel_box.border_width_right == 0 and sel_box.border_color.is_equal_approx(HT.ACCENT), [bg, sel_box.border_width_left])
	var labels: Array = rows[0].find_children("*", "Label", true, false)
	var other_labels: Array = rows[1].find_children("*", "Label", true, false)
	check("history: the selected row's prompt in the text color, its verdict as on the others",
		labels[-1].text == "Verify that the lever moves" and labels[-1].get_theme_color("font_color").is_equal_approx(HT.TEXT)
		and labels[0].get_theme_color("font_color").is_equal_approx(other_labels[0].get_theme_color("font_color"))
		and labels[0].get_theme_color("font_color").is_equal_approx(HT.verdict_colors("CONFIRMED")[3]),
		[labels[-1].get_theme_color("font_color"), labels[0].get_theme_color("font_color")])
	check("history: a run's line says its attempts", labels[1].text.ends_with(" · 0 attempts") and Model.attempts_text(1) == "1 attempt"
		and Model.attempts_text(12) == "12 attempts" and not "POL" in labels[1].text, labels[1].text)
	var plain_box: StyleBoxFlat = rows[1].get_theme_stylebox("panel")
	rows[1].mouse_entered.emit()
	var hover_box: StyleBoxFlat = rows[1].get_theme_stylebox("panel")
	rows[1].mouse_exited.emit()
	check("history: other rows on the rail's ground (RAIL, GROUND in turn), hovered HOVER, the 2 px rule transparent",
		plain_box.bg_color.is_equal_approx(HT.GROUND) and (rows[0].get_theme_stylebox("panel") as StyleBoxFlat).border_color.is_equal_approx(HT.ACCENT)
		and hover_box.bg_color.is_equal_approx(HT.HOVER) and hover_box.border_color.a == 0.0, [plain_box.bg_color, hover_box.bg_color])
	var drop_tag: Label = dock._hist_tag_slot.find_children("*", "Label", true, false)[0]
	check("history dropdown: the run's verdict in its own colors (no accent fill)", drop_tag.get_theme_color("font_color").is_equal_approx(HT.verdict_colors("CONFIRMED")[3]))
	var menu_panel: StyleBoxFlat = dock._hist_menu.get_theme_stylebox("panel")
	dock._fill_history_menu()
	check("history dropdown: a floating panel (POPOVER, 8, the only shadow), the runs' rows in it", menu_panel.bg_color.is_equal_approx(HT.POPOVER)
		and menu_panel.corner_radius_top_left == 8 and menu_panel.shadow_size > 0
		and dock.history_menu_prompts() == ["Verify that the lever moves", "Verify that the gate opens"].slice(0, 2) + dock.history_menu_prompts().slice(2),
		dock.history_menu_prompts())
	var srow: Control = dock._settings_page._run_row({"id": "x", "started_unix": 1791900000.0, "kind": "verify", "prompt": "p", "usage": []})
	var sbox: StyleBoxFlat = srow.get_theme_stylebox("panel")
	check("Settings, runs: a list, not buttons (no fill, no rule, no click, no hover, the default cursor)",
		not sbox.draw_center and sbox.border_width_left == 0 and srow.mouse_filter == Control.MOUSE_FILTER_IGNORE
		and srow.mouse_default_cursor_shape == Control.CURSOR_ARROW and srow.gui_input.get_connections().is_empty()
		and srow.mouse_entered.get_connections().is_empty() and not dock._settings_page.has_signal("run_picked"),
		[sbox.draw_center, sbox.border_width_left, srow.mouse_filter, srow.mouse_default_cursor_shape])
	srow.free()

	# memory at the end of a run: the status line is the Report stage; the result says it learned: the feed's last line
	var mr = dock._new_run("goal", "Reach the gate")
	dock._on_status("Writing the harness")
	dock._on_status("Looking at the results")
	dock._on_status("Saving what it learned to memory")
	check("memory: saving is the Report stage", mr.stage == 6 and mr.feed[-1].text == "Saving what it learned to memory", mr.stage)
	bridge.run_active = false
	dock._on_result({"kind": "goal", "reached": true, "goal": "Reach the gate", "memory": {"updated": true, "notes": true, "lib": false}}, "")
	check("memory: the feed's last line", mr.feed[-1].kind == "memory" and mr.feed[-1].text == "Memory updated"
		and mr.feed[-2].text == "Result: Goal hit", mr.feed.slice(-2))
	dock.shown = mr
	dock.tab = "feed"
	dock._render()
	var parsed: String = _feed_rows_text()
	check("memory: shown in the feed, tagged", parsed.strip_edges().ends_with("MEMORY   Memory updated"), parsed.right(300))
	var nr = dock._new_run("goal", "Reach the gate")
	bridge.run_active = false
	dock._on_result({"kind": "goal", "reached": false, "memory": {"updated": false, "notes": true, "lib": true}}, "")
	check("memory: nothing new, no line", nr.feed[-1].kind != "memory")
	activity_checks()



	# the recording: copied into the run's folder (off the editor's thread), played as a flipbook under the verdict
	var src := frames_dir()
	var base := OS.get_environment("UNITS_DIR")
	var run_dir := base.path_join("run_with_recording")
	DirAccess.make_dir_recursive_absolute(run_dir)
	var mp4 := src.get_base_dir().path_join("recording.mp4")
	var rr = dock._new_run("verify", "Verify that the gate opens")
	bridge.run_active = false
	dock._on_result({"verdict": "confirmed", "reason": "It opens.", "recording": {"video": mp4, "seconds": 2.4, "frames": src,
		"frames_fps": 2}}, run_dir)
	check("recording: copied off the editor's thread, played from the agent service's folder meanwhile", dock._copies.size() == 1
		and rr.recording().frames == src)
	for i in 600:
		if dock._copies.is_empty():
			break
		await process_frame
		dock._poll_copies()
	var local := run_dir.path_join("recording")
	check("recording: copied into the run's folder", dock._copies.is_empty() and FileAccess.file_exists(local.path_join("000004.jpg"))
		and rr.recording().frames == local and rr.recording().fps == 2.0, rr.result.get("recording"))
	var saved = JSON.parse_string(FileAccess.get_file_as_string(run_dir.path_join("result.json")))
	check("recording: result.json says where, with the fps", typeof(saved) == TYPE_DICTIONARY and saved.recording.frames == local
		and int(saved.recording.frames_fps) == 2, saved)
	dock.shown = rr
	dock.tab = "main"
	dock._render()
	await process_frame
	await process_frame
	var pl = dock._r_player
	check("recording: the player under the verdict, paused on the first frame", pl != null and pl.is_visible_in_tree()
		and pl.get_parent().is_ancestor_of(dock._r_banner) and pl.get_index() > _holding(pl.get_parent(), dock._r_banner).get_index() and pl.count == 5
		and pl.scrub.max_value == 4.0 and not pl.playing and pl.index == 0 and pl.screen.picture.texture != null and pl.folder == local
		and pl.time_label.text == "0:00 / 00:02" and pl.play_button.text == "" and pl.play_button.icon != null
		and pl.play_button.tooltip_text == "Play" and pl.open_button.text == "" and pl.open_button.icon != null,
		[pl.count if pl else -1, pl.time_label.text if pl else ""])
	var rt := _texts(dock._result)
	check("recording: no readouts under it (took, harnesses, policy steps, alerts)", not "TOOK" in rt and not "HARNESSES" in rt
		and not "POLICY STEPS" in rt and dock._result.find_children("*", "GridContainer", true, false).is_empty(), rt)
	check("recording: as wide as the page, 230 tall (200 narrow; never more than 16:9)", pl != null
		and absf(pl.screen.size.y - minf(HT.px(230 if dock.wide else 200), floorf(pl.screen.size.x * 9.0 / 16.0))) <= 1.0
		and pl.screen.size.x > 200.0, pl.screen.size if pl else null)
	check("recording: Show in folder, no MP4: the frames' folder", pl.open_path() == local)
	FileAccess.open(mp4, FileAccess.WRITE).close()
	check("recording: Show in folder, the MP4 there: it", pl.open_path() == mp4)
	pl.open(rr.recording().merged({"video": mp4}))
	var shown: String = {"Windows": "Show in File Explorer", "macOS": "Show in Finder"}.get(OS.get_name(), "Show in folder")
	check("recording: the folder button named for this system's file manager", pl.open_button.tooltip_text == shown,
		pl.open_button.tooltip_text)
	check("recording: its button shows the folder (Run folder's icon), no box-with-an-arrow glyph left",
		pl.open_button.icon.get_image().get_data() == HT.glyph("folder", 14).get_image().get_data() and not HT.GLYPHS.has("external"))
	pl.play()
	check("recording: Play, the button then Pause", pl.playing and pl.play_button.tooltip_text == "Pause" and pl.play_button.icon == pl._icons[1]
		and pl._timer.time_left > 0.0 and is_equal_approx(pl._timer.wait_time, 0.5))
	for i in 4:
		pl.step()
	check("recording: to the end, then paused there", pl.index == 4 and not pl.playing and pl.time_label.text == "0:02 / 00:02"
		and pl.scrub.value == 4.0 and pl.cache.size() <= 1 + pl.KEEP_BEHIND + pl.KEEP_AHEAD, [pl.index, pl.time_label.text, pl.cache.size()])
	pl.scrub.value = 1
	check("recording: the scrub bar picks the frame", pl.index == 1 and pl.screen.picture.texture == pl.cache[1] and not pl.playing)
	pl.play()
	dock._render()
	check("recording: shown again, the same player, where it was", dock._r_player == pl and pl.is_inside_tree() and pl.index == 1 and pl.playing)
	dock.tab = "feed"
	dock._render()
	check("recording: out of sight, paused", not pl.playing)
	dock.tab = "main"
	dock.shown = nr
	dock._render()
	check("recording: a run without one shows no player", dock._r_player == null
		and dock._result.find_children("*", "", true, false).filter(func(n): return n is W.Flipbook).is_empty())
	var gone = dock._new_run("verify", "Verify that the gate opens")
	bridge.run_active = false
	dock._on_result({"verdict": "confirmed", "recording": {"video": "", "frames": base.path_join("no_such_folder"), "frames_fps": 2}}, run_dir)
	dock.shown = gone
	dock._render()
	check("recording: its frames gone, the player kept (Play, Show in File Explorer and Run folder disabled), its line, nothing copied",
		dock._r_player != null and dock._r_player.count == 0 and dock._r_player.play_button.disabled and dock._r_player.open_button.disabled
		and dock._result.find_children("*", "Button", true, false).filter(func(x): return x.tooltip_text == "Run folder")[0].disabled
		and "The recording isn’t there any more." in _texts(dock._result) and dock._copies.is_empty(),
		_texts(dock._result))
	check("recording: no folder, no frames", W.Flipbook.frame_count("") == 0)
	check("no recording: with a recording (its frames gone), no line either", dock._r_no_rec == null)
	await no_recording_checks(base)
	await alerts_checks(base)
	dock.shown = null
	bridge.link.close()
	_plain_view()


## The result's alerts, from the result as it came through the bridge and from its result.json: each in an amber strip
## above the verdict, its text as sent; none without them.
func alerts_checks(base: String) -> void:
	dock.tab = "main"
	var why := "Autopilot couldn't look at the game's picture: the game runs without a picture (headless: nothing is rendered)."
	var lr = dock._new_run("verify", "Verify that the guard waves")
	dock.shown = lr
	dock._render()
	check("alerts: none while the run goes on", dock._r_alerts.is_empty())
	bridge._on_result({"type": "result", "result": {"verdict": "INCONCLUSIVE", "reason": "Can't tell.", "alerts": [why]},
		"memory": {"updated": false}})
	dock._render()
	await process_frame
	check("alerts: shown as sent", dock._r_alerts.size() == 1 and dock._r_alerts[0].text == why
		and dock._r_alerts[0].is_visible_in_tree() and dock._r_alerts[0].autowrap_mode != TextServer.AUTOWRAP_OFF,
		dock._r_alerts.map(func(l): return l.text))
	var col: Node = _holding(dock._r_alerts[0].get_parent().get_parent(), dock._r_banner)
	var alert_box: StyleBoxFlat = dock._r_alerts[0].get_parent().get_theme_stylebox("panel")
	check("alerts: above the verdict, a warning alert (square, its surface and border, its text 500)", dock._r_alerts.size() == 1
		and dock._r_alerts[0].get_parent().get_index() < col.get_index()
		and dock._r_alerts[0].get_theme_color("font_color") == HT.AMBER_FG and dock._r_alerts[0].theme_type_variation == "AutopilotStrong"
		and alert_box.bg_color == HT.AMBER_FILL and alert_box.border_color == HT.AMBER_LINE and alert_box.corner_radius_top_left == 0)
	var saved = JSON.parse_string(FileAccess.get_file_as_string(lr.report_dir.path_join("result.json")))
	check("alerts: saved in result.json", typeof(saved) == TYPE_DICTIONARY and saved.get("alerts") == [why], saved)
	var back = Model.Run.new()
	back.result = saved
	back.ended_ms = 0  # (a run loaded back is over)
	dock.shown = back
	dock._render()
	check("alerts: shown again from result.json", dock._r_alerts.size() == 1 and dock._r_alerts[0].text == why)
	var plain = dock._new_run("verify", "Verify that the gate opens")
	bridge.run_active = false
	dock._on_result({"verdict": "CONFIRMED", "reason": "It opens.", "memory": {"updated": false}}, "")
	dock.shown = plain
	dock._render()
	check("alerts: none without them", dock._r_alerts.is_empty())


## A run without a recording says why where the player would go, from the result as it came (through the bridge: the
## frame's `no_recording` kept with the result) and from its result.json; nothing while it goes, nothing when the agent
## service said nothing more (memory sent).
func no_recording_checks(base: String) -> void:
	dock.tab = "main"
	var why := "recording couldn't start: no picture: the game's window is minimized or hidden"
	var lr = dock._new_run("verify", "Verify that the gate opens")
	dock.shown = lr
	dock._render()
	check("no recording: none while the run goes on", dock._r_no_rec == null and lr.no_recording_line() == "")
	# through the bridge: the result frame carries the reason beside the result, as it does memory and recording
	bridge._on_result({"type": "result", "result": {"verdict": "confirmed", "reason": "It opens."}, "no_recording": why,
		"memory": {"updated": false}})
	dock._render()
	await process_frame
	check("no recording: the agent service's reason, under the verdict", dock._r_no_rec != null
		and dock._r_no_rec.text == "No recording: " + why and dock._r_no_rec.is_visible_in_tree()
		and dock._r_no_rec.get_parent().is_ancestor_of(dock._r_banner)
		and dock._r_no_rec.get_index() > _holding(dock._r_no_rec.get_parent(), dock._r_banner).get_index()
		and dock._r_no_rec.autowrap_mode != TextServer.AUTOWRAP_OFF, dock._r_no_rec.text if dock._r_no_rec else null)
	check("no recording: muted, small", dock._r_no_rec != null and dock._r_no_rec.get_theme_color("font_color") == HT.MUTED
		and dock._r_no_rec.get_theme_font_size("font_size") == HT.px(12))
	check("no recording: no player beside it", dock._r_player == null
		and dock._result.find_children("*", "", true, false).filter(func(n): return n is W.Flipbook).is_empty())
	var saved = JSON.parse_string(FileAccess.get_file_as_string(lr.report_dir.path_join("result.json")))
	check("no recording: saved in result.json", typeof(saved) == TYPE_DICTIONARY and saved.get("no_recording") == why, saved)
	# an older agent service: neither recording nor memory; decided now, written into the run's result.json
	var od = dock._new_run("verify", "Verify that the gate opens")
	bridge.run_active = false
	var odir := base.path_join("run_older_service")
	DirAccess.make_dir_recursive_absolute(odir)
	dock._on_result({"verdict": "denied", "reason": "It stays shut."}, odir)
	dock.shown = od
	dock._render()
	var line := "No recording: an older agent service ran this."
	check("no recording: an older agent service", dock._r_no_rec != null and dock._r_no_rec.text == line,
		dock._r_no_rec.text if dock._r_no_rec else null)
	saved = JSON.parse_string(FileAccess.get_file_as_string(odir.path_join("result.json")))
	check("no recording: the older agent service's line saved in result.json", typeof(saved) == TYPE_DICTIONARY
		and saved.get("no_recording") == Model.NO_RECORDING_OLDER, saved)
	var back = Model.Run.new()
	back.result = saved
	back.ended_ms = 0  # (a run loaded back is over)
	check("no recording: the same line from result.json", back.no_recording_line() == line, back.no_recording_line())
	# memory sent, no reason: nothing
	var mm = dock._new_run("verify", "Verify that the gate opens")
	bridge.run_active = false
	dock._on_result({"verdict": "confirmed", "memory": {"updated": false}}, "")
	dock.shown = mm
	dock._render()
	check("no recording: memory sent and no reason: nothing", dock._r_no_rec == null and not mm.result.has("no_recording"))


# --- the layout at several dock sizes ------------------------------------------------------------------------
# Godot lays Controls out without drawing anything: each page is put in the dock at each size, and nothing it shows
# may stick out past the dock's right edge (or past the scroll container holding it: no sideways scrolling), nor make
# the dock wider or taller than it is (a page taller than the dock scrolls). Wide, the setup, connect, composer, result
# and Settings pages keep their content at most 560 px wide, centred.

const LAYOUT_SIZES := [Vector2(320, 900), Vector2(360, 640), Vector2(560, 480), Vector2(900, 400), Vector2(1400, 900)]
const LONG_URL := "https://app.tryaura.dev/auth/cli?code=8f3a9c2e71b04d5f9a6e3c1b2d7f8e9a0b1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e&state=editor"
const LONG_EMAIL := "someone.with.a.rather.long.name@a-studio-with-a-long-domain-name.example.org"
const LONG_LOG := "C:/Users/someone/AppData/Local/Autopilot/logs/agent-service-20261013-141630.log"
const LONG_PROMPT := ("Verify that the player can pick up the fruit by the pond, carry it across the bridge to the mailbox "
	+ "and that the counter in the top left corner goes up by one")
const LONG_WHY := ("the video couldn't be made (the encoder stopped: Invalid data found when processing input): its frames are in "
	+ "C:/Users/someone/AppData/Local/Autopilot/memory/8f3a9c2e-71b0-4d5f-9a6e-3c1b2d7f8e9a/runs/20261015-101500/media/frames")
const W = preload("res://addons/autopilot/ui/widgets.gd")
const HT = preload("res://addons/autopilot/ui/autopilot_theme.gd")

var _lay_run = null  # the run the run pages show
var _pickers_checked := 0  # the layouts where the BYOK card's Coding Agent and LLM provider row were checked
var _steers_checked := 0  # the layouts where the live page's steer box was checked
var _no_recs_checked := 0  # the layouts where the report's no-recording line was checked
var _findings_checked := 0  # the layouts where the report's findings headers were checked
var _gears_checked := 0  # the layouts where the gear (Settings) was checked
var _resets_checked := 0  # the layouts where the footer's Reset memory was checked
var _heads_checked := 0  # the layouts where the result's run id row (and its icon buttons) was checked
var _links_checked := 0  # the layouts where the footer's link text and button were checked
var _input_widths := {}  # the dock's size -> [decision key field, LLM key field] widths (printed at the end)
var _setup_widths := {}  # the dock's size -> {setup state: its column's content width}


func _plain_view() -> void:
	dock.service.removal = ""
	if dock.onboarding:
		finish("", true)
	dock.settings_open = false
	dock.composing = false
	dock.sign_in = {}
	dock._sign_in_send = false
	dock.service.state = "idle"


## keys: step 1's BYOK card opened (Show), so its fields are laid out too; it opens folded.
func _setup_view(step: int, si: Dictionary, svc := "idle", installer := true, access := "own_keys", keys := true) -> void:
	dock.settings_open = false
	if not dock.onboarding:
		dock._open_setup()
	dock.sign_in = si
	dock._sign_in_send = false
	dock.service.state = svc
	dock.service.url = "ws://127.0.0.1:51734/v1/bridge"
	dock.service.message = ("Couldn't install the agent service (see %s)" if svc == "install_failed" else "The agent service stopped (see %s)") % LONG_LOG
	dock.service.log_path = LONG_LOG
	dock.service._poll_at = 1e12
	dock.service.installer_dir = OS.get_environment("UNITS_DIR").path_join("agent_service" if installer else "no_installer")
	dock._setup.step = step
	dock._setup.access = access
	dock._setup.email = LONG_EMAIL if access == "aura" else ""
	dock._setup._keys_shown = keys
	dock._setup._key = []
	dock._render()
	dock._setup.refresh()


## Step 1 with this Coding Agent picked (and this LLM provider for OpenCode, "" for none).
func _setup_agent(agent: String, provider: String) -> void:
	_setup_view(1, {})
	dock._setup.coding_agent = agent
	dock._setup.llm_provider = provider
	dock._setup._key = []
	dock._setup.refresh()


## Step 4 (First run): waiting for the agent service (not connected), or the Explore run offered (connected, the
## memory empty).
func _first_run_view(empty: bool) -> void:
	_setup_view(4, {}, "ready", true, "aura")
	if empty:
		_online()
		bridge._on_brain_frame({"type": "memory_state", "runs": 0, "notes": false, "lib": false})
	elif bridge.is_connected_to_brain():
		unwelcome()
	bridge.link.problem = "" if empty else "Couldn't reach the agent service at " + LONG_URL
	dock._setup._key = []
	dock._render()
	dock._setup.refresh()
	var t := _texts(dock._setup)
	check("layout: step 4 shown (%s)" % ("the Explore run offered" if empty else "waiting"), dock.onboarding and dock._setup.visible
		and dock._setup.step == 4 and ("Explore and finish" in t) == empty and ("Connecting to the agent service…" in t) == not empty, t)


func _connect_view(svc := "idle", installer := true) -> void:
	_plain_view()
	bridge.link.close()
	bridge.link.connected = false
	dock.shown = null
	dock.service.state = svc
	dock.service.message = "The agent service stopped (see %s)" % LONG_LOG
	dock.service.log_path = LONG_LOG
	dock.service.installer_dir = OS.get_environment("UNITS_DIR").path_join("agent_service" if installer else "no_installer")
	dock._cp_key = []
	for f in dock._connect_page.find_children("*", "FoldableContainer", true, false):
		f.folded = false
	dock._render()


func _online() -> void:
	if not bridge.is_connected_to_brain():
		welcome(["steer", "usage", "account"])


func _composer_view(text := "Explore the orchard for bugs: " + LONG_PROMPT) -> void:
	_plain_view()
	_online()
	dock.shown = null
	dock._c_prompt.text = ""
	dock._compose(text)


func _make_run(live: bool):
	var r = dock._new_run("verify", LONG_PROMPT)
	for i in 12:
		r.add_feed("Status line %d: the agent walks to the far gate and looks at the counter in the corner" % i, "status")
	r.add_feed("script_error: Invalid access to property 'count' on a base object of type 'Nil' in res://scripts/mailbox.gd:42", "engine")
	for i in 8:
		r.add_step({"type": "jev", "call": i + 1, "t": 1.5 * i, "seconds": {"jev": 0.12},
			"decided": {"summary": "switched: walk_to_the_far_gate_by_the_pond" if i % 3 == 0 else "kept: jump_over_the_fence",
				"chosen": ["walk_to_the_far_gate_by_the_pond" if i % 3 == 0 else "jump_over_the_fence"]},
			"input": {"state": {"situation": {"player_x": i * 1.5, "counter": i, "holding_fruit": i > 3}}},
			"output": {"answers": {"pick": {"walk_to_the_far_gate_by_the_pond": 0.55, "jump_over_the_fence": 0.3, "wait": 0.15}}}})
	r.attempts = 2
	r.stage = 4
	r.now_text = "Carrying the fruit across the bridge to the mailbox on the far side of the pond"
	r.findings.append({"severity": "error", "detector": "script_error_detector_with_a_long_name", "count": 3,
		"summary": "Invalid access to property 'count' on a base object of type 'Nil' in res://scripts/mailbox.gd:42",
		"actor": "MailboxWithAVeryLongNodeName", "where": {"X": 1200.5, "Y": -30.25, "Z": 455.0}, "t": 12.5, "signature": "sig1"})
	if not live:
		r.result = {"verdict": "inconclusive", "reason": "The counter went up by one, but only after the second try: the first "
			+ "pick-up didn't register until the player jumped.", "evidence": [LONG_PROMPT, "counter: 0 → 1 at t=31.2s"],
			"changes": [{"text": "Moved the player next to the pond (the walk from the start takes two minutes)"}],
			"findings": ["The fruit can be picked up through the fence from the wrong side"], "seconds": 184.0, "attempts": 2}
		r.ended_ms = r.started_ms + 184000
		r.report_dir = OS.get_environment("UNITS_DIR")
		dock.active = null
	return r


func _run_view(t: String, live: bool) -> void:
	_plain_view()
	_online()
	if _lay_run == null or _lay_run.live() != live:
		_lay_run = _make_run(live)
	dock.shown = _lay_run
	dock.active = _lay_run if live else null
	bridge.run_active = live  # (the steer row shown while it goes)
	dock.tab = t
	dock._render()


func _usage_view(account: Dictionary) -> void:
	_plain_view()
	dock.settings_open = true
	dock._render()
	var entries := []
	var bills := [{"usd": 98765.4321, "billed": true}, {"usd": 12345.678, "billed": true}, {}]  # (the last: a BYOK run)
	for i in 3:
		entries.append({"id": "run%d" % i, "started_unix": 1791900000.0 + i * 600, "kind": "verify", "prompt": LONG_PROMPT,
			"verdict": "CONFIRMED", "bill": bills[i], "usage": [
				{"kind": "llm", "model": "claude-some-long-model-name-20261001", "calls": 123, "input": 12345678, "output": 234567,
					"cache_read": 98765432, "cache_write": 1234567},
				{"kind": "decision", "model": "typesafe-decision-model-v2-large", "calls": 4567, "input": 4567890, "output": 45678}]})
	dock._settings_page.confirming_sign_out = account.get("confirm", false)
	dock._settings_page.confirming_removal = account.get("remove", false)
	dock._settings_page.show_data(entries, true, "Saved. It applies from the next connection (after this run).", account)


## The removal's page in this state (its log there, so that Open log shows).
func _removal_view(state: String, message := "") -> void:
	_plain_view()
	dock.service.removal = state
	dock.service.removal_message = message
	dock.service.removal_log = OS.get_environment("UNITS_DIR").path_join("autopilot-uninstall-20261019-120000.log")
	FileAccess.open(dock.service.removal_log, FileAccess.WRITE).close()
	dock._render()


func _layout_states() -> Array:
	var states := [
		["setup 1: choose", func(): _setup_view(1, {})],
		["setup 1: BYOK folded", func(): _setup_view(1, {}, "idle", true, "own_keys", false)],
		["setup 1: Claude Code (the provider locked)", func(): _setup_agent("claude", "")],
		["setup 1: OpenCode with OpenCode Zen", func(): _setup_agent("opencode", "opencode")],
		["setup 1: preparing", func(): _setup_view(1, {"state": "preparing"}, "installing")],
		["setup 1: waiting", func(): _setup_view(1, {"state": "waiting", "url": LONG_URL})],
		["setup 1: signed in", func(): _setup_view(1, {"state": "signed_in", "email": LONG_EMAIL}, "idle", true, "own_keys", false)],
		["setup 1: error", func(): _setup_view(1, {"state": "error", "message": "Couldn’t reach Aura. Check your connection and try again.", "log": LONG_LOG})],
		["setup 1: no installer", func(): _setup_view(1, {"state": "no_installer"}, "not_installed", false)],
		["setup 2: not installed", func(): _setup_view(2, {}, "not_installed")],
		["setup 2: no installer", func(): _setup_view(2, {}, "not_installed", false)],
		["setup 2: installing", func(): _setup_view(2, {}, "installing")],
		["setup 2: ready", func(): _setup_view(2, {}, "ready", true, "aura")],
		["setup 2: install failed", func(): _setup_view(2, {}, "install_failed")],
		["setup 2: stopped", func(): _setup_view(2, {}, "stopped")],
		["setup 2: start it yourself", func(): _setup_view(2, {}, "idle")],
		["setup 3: privacy", func(): _setup_view(3, {}, "ready", true, "aura")],
		["setup 4: waiting, a problem", func(): _first_run_view(false)],
		["setup 4: first run", func(): _first_run_view(true)],
		["connect", func(): _connect_view()],
		["connect: stopped", func(): _connect_view("stopped")],
		["connect: no installer", func(): _connect_view("not_installed", false)],
		["composer", func(): _composer_view()],
		["composer: empty", func(): _composer_view("")],
		["composer: a claim", func(): _composer_view(LONG_PROMPT)],
		["composer: a long memory line", func():
			_composer_view()
			dock.bridge.memory_known = true
			dock.bridge.memory_runs = 123456
			dock.bridge.memory_notes = true
			dock._render_footer()],
		["live", func(): _run_view("main", true)],
		["live: harness 12, every stage's time", func():
			_run_view("main", true)
			_lay_run.attempts = 12
			for i in 7:
				_lay_run.stage_secs[i] = 3725.0
			dock._render()],
		["live: before the game plays (steer disabled)", func():
			_run_view("main", true)
			_lay_run.stage = 1
			dock._render()],
		["live: playing again", func():
			_run_view("main", true)
			_lay_run.stage = 4
			dock._render()],
		["feed", func(): _run_view("feed", true)],
		["policy", func(): _run_view("policy", true)],
		["result", func(): _run_view("main", false)],
		["result: the recording", func():
			_run_view("main", false)
			_lay_run.result.recording = {"video": "", "seconds": 184.0, "frames": frames_dir(), "frames_fps": 2}
			dock._render()],
		["result: no recording, why", func():
			_lay_run = _make_run(false)
			_lay_run.result.no_recording = LONG_WHY
			_run_view("main", false)],
		["result: no findings, an older agent service", func():
			_lay_run = _make_run(false)
			_lay_run.findings.clear()
			_lay_run.result.erase("findings")
			_lay_run.result.no_recording = Model.NO_RECORDING_OLDER
			_run_view("main", false)],
		["usage: Aura", func(): _usage_view({"kind": "aura", "email": LONG_EMAIL, "keys": [null, null],
			"service": {"text": "Agent service: running", "url": LONG_URL, "action": ""}})],
		["usage: Aura, the bill", func(): _usage_view({"kind": "aura", "email": LONG_EMAIL, "keys": [null, null],
			"bill": {"usd": 123456.78, "runs": 123456, "from": "2026-12-15", "to": "2027-01-14"},
			"service": {"text": "Agent service: running", "url": LONG_URL, "action": ""}})],
		["usage: sign out?", func(): _usage_view({"kind": "aura", "email": LONG_EMAIL, "keys": [null, null], "confirm": true})],
		["usage: BYOK", func(): _usage_view({"kind": "own_keys", "keys": ["••••a91f", "••••3c2e"], "coding_agent": "opencode", "llm_provider": "openrouter",
			"service": {"text": "Agent service: not installed", "url": "", "action": "install"}})],
		["usage: stopped", func(): _usage_view({"kind": "own_keys", "keys": [null, null],
			"service": {"text": "The agent service stopped (see %s)" % LONG_LOG, "url": "", "action": "open_log"}})],
		["usage: Aura app", func(): _usage_view({"kind": "aura_app"})],
		["usage: not set up", func(): _usage_view({"kind": "none"})],
		["usage: signed out", func(): _usage_view({"kind": "signed_out", "email": LONG_EMAIL})],
		["usage: signing in", func(): _usage_view({"kind": "signed_out", "email": LONG_EMAIL, "sign_in": {"state": "waiting", "url": LONG_URL}})],
		["usage: no installer", func(): _usage_view({"kind": "none", "sign_in": {"state": "no_installer"}})],
		["usage: sign-in error", func(): _usage_view({"kind": "signed_out", "sign_in": {"state": "error", "message": "Couldn’t reach Aura. Check your connection and try again."}})],
		["usage: remove from this computer?", func(): _usage_view({"kind": "own_keys", "keys": [null, null], "remove": true})],
		["removing", func(): _removal_view("running")],
		["removed", func(): _removal_view("done")],
		["removed, but for some files", func(): _removal_view("kept")],
		["couldn't remove", func(): _removal_view("failed", "Couldn't start C:/Users/someone/AppData/Roaming/uv/tools/autopilot/Scripts/autopilot.exe.")],
	]
	if OS.get_name() == "Windows":
		var app := Prefs.settings_base().path_join("Programs").path_join("aura-client").path_join("Aura.exe")
		states.insert(1, ["setup 1: Aura app found", func():
			DirAccess.make_dir_recursive_absolute(app.get_base_dir())
			FileAccess.open(app, FileAccess.WRITE).close()
			_setup_view(1, {})])
		states.insert(2, ["setup 1 (the Aura app gone again)", func():
			DirAccess.remove_absolute(app)
			_setup_view(1, {})])
	return states


## What sticks out at size sz: the visible controls past the dock's left or right edge, or past the scroll container
## they are in.
func _sticking_out(sz: Vector2) -> Array:
	var out := []
	var left: float = dock.global_position.x
	var right: float = left + sz.x
	for c in dock.find_children("*", "Control", true, false):
		if not c.is_visible_in_tree() or c.size.x <= 0.0:
			continue
		var l: float = left
		var r: float = right
		var p = c.get_parent()
		while p != null and p != dock:
			if p is ScrollContainer:
				l = maxf(l, p.global_position.x)
				r = minf(r, p.global_position.x + p.size.x)
				break
			p = p.get_parent()
		var g: Rect2 = c.get_global_rect()
		if g.position.x < l - 0.5 or g.end.x > r + 0.5:
			out.append("%s %s (%s)" % [str(dock.get_path_to(c)).right(70), g, (c.text if "text" in c else "")])
	return out


## The BYOK card's fields, when shown: stacked, each the card's width (the decision key, the Coding Agent dropdown, the
## LLM key's row); the LLM provider dropdown (140 px at the editor's scale, half the row when that is less) and the
## key's field after it on the same row, the field the wider; each dropdown's longest name in it, locked or not; each field's placeholder fits in it.
## What is wrong ([] when nothing).
func _picker_row() -> Array:
	var su = dock._setup
	if not su.is_visible_in_tree() or not is_instance_valid(su._provider) or not su._provider.is_visible_in_tree():
		return []
	_pickers_checked += 1
	var bad := []
	var o: OptionButton = su._provider
	var e: LineEdit = su._llm_key
	var og := o.get_global_rect()
	var eg := e.get_global_rect()
	if absf(og.size.x - minf(HT.px(140), floorf((eg.end.x - og.position.x - HT.px(6)) / 2.0))) > 0.5:
		bad.append("dropdown %d wide" % og.size.x)
	if absf(og.position.y - eg.position.y) > 0.5 or eg.position.x < og.end.x:
		bad.append("not on one row: %s, %s" % [og, eg])
	if eg.size.x < og.size.x:
		bad.append("the key's field narrower than the dropdown: %d" % eg.size.x)
	var tg: Rect2 = su._typesafe.get_global_rect()
	if tg.end.y > og.position.y or absf(tg.position.x - og.position.x) > 0.5 or absf(tg.end.x - eg.end.x) > 0.5:
		bad.append("the key fields not stacked, each the full width: %s; %s, %s" % [tg, og, eg])
	for f in [su._typesafe, su._llm_key]:
		var room: float = f.size.x - f.get_theme_stylebox("normal").get_minimum_size().x
		var text_w: float = f.get_theme_font("font").get_string_size(f.placeholder_text, HORIZONTAL_ALIGNMENT_LEFT, -1,
			f.get_theme_font_size("font_size")).x
		if text_w > room + 0.5:
			bad.append("\"%s\" doesn't fit its field (%d in %d)" % [f.placeholder_text, text_w, room])
	var ag: Rect2 = su._agent.get_global_rect() if is_instance_valid(su._agent) and su._agent.is_visible_in_tree() else Rect2()
	if ag.size.x <= 0.0:
		bad.append("no Coding Agent dropdown")
	elif ag.position.y < tg.end.y or ag.end.y > og.position.y or absf(ag.position.x - tg.position.x) > 0.5 or absf(ag.end.x - tg.end.x) > 0.5:
		bad.append("the Coding Agent dropdown not between the keys, the full width: %s; %s, %s" % [ag, tg, og])
	_input_widths[str(dock.size)] = [roundi(tg.size.x), roundi(eg.size.x)]
	for d in [o, su._agent]:
		if not is_instance_valid(d):
			continue
		var font: Font = d.get_theme_font("font")
		var fs: int = d.get_theme_font_size("font_size")
		var box: StyleBox = d.get_theme_stylebox("disabled" if d.disabled else "normal")
		var arrow: Texture2D = d.get_theme_icon("arrow")
		for i in d.item_count:
			var need: float = (font.get_string_size(d.get_item_text(i), HORIZONTAL_ALIGNMENT_LEFT, -1, fs).x + box.get_minimum_size().x
				+ (arrow.get_width() if arrow else 0) + d.get_theme_constant("arrow_margin"))
			if need > d.size.x + 0.5:
				bad.append("%s doesn't fit (%d in %d)" % [d.get_item_text(i), need, d.size.x])
	return bad


## The composer's box, empty: its placeholder fits it on one line (in a dock as wide as the narrow board, 400 px, or
## wider: at 13 px it can't in 320). What is wrong ([] when nothing).
func _placeholder_fits() -> Array:
	var e: TextEdit = dock._c_prompt
	if not e.is_visible_in_tree() or e.text != "" or dock.size.x < 400.0 * HT.s:
		return []
	var room: float = e.size.x - e.get_theme_stylebox("normal").get_minimum_size().x
	var text_w: float = e.get_theme_font("font").get_string_size(e.placeholder_text, HORIZONTAL_ALIGNMENT_LEFT, -1,
		e.get_theme_font_size("font_size")).x
	return [] if text_w <= room + 0.5 else ["\"%s\" doesn't fit the box (%d in %d)" % [e.placeholder_text, text_w, room]]


## The live page's steer box, when shown: its placeholder ("Send a steering prompt to Autopilot", at every stage) fits it
## on one line (the box keeps its place). What is wrong ([] when nothing).
func _steer_fits() -> Array:
	var e: LineEdit = dock._l_steer
	if not e.is_visible_in_tree():
		return []
	_steers_checked += 1
	var bad := []
	var hint := "Send a steering prompt to Autopilot"
	var room: float = e.size.x - e.get_theme_stylebox("normal").get_minimum_size().x
	var text_w: float = e.get_theme_font("font").get_string_size(hint, HORIZONTAL_ALIGNMENT_LEFT, -1,
		e.get_theme_font_size("font_size")).x
	if text_w > room + 0.5:
		bad.append("\"%s\" doesn't fit the steer box (%d in %d)" % [hint, text_w, room])
	if e.placeholder_text != hint:
		bad.append("the steer box says \"%s\"" % e.placeholder_text)
	return bad


## The live page's stage track: every stage's name whole and within the room it is given, inside the track, the track
## as tall as its lines (nothing clipped, nothing ellipsized). What is wrong ([] when nothing).
func _track_fits() -> Array:
	var tr = dock._l_track
	if not tr.is_visible_in_tree():
		return []
	var bad := []
	var lay: Dictionary = tr.layout(tr.size.x)
	var names: Array = tr.labels()
	for i in names.size():
		var it: Dictionary = lay.items[i]
		if " ".join(it.lines) != names[i]:
			bad.append("%s shown as %s" % [names[i], it.lines])
		for k in it.lines.size():
			var lw: float = W.StageTrack._caps_w(it.lines[k])
			if lw > it.rooms[k] + 0.5 or it.xs[k] < -0.5 or it.xs[k] + lw > tr.size.x + 0.5:
				bad.append("\"%s\" %d wide in %d at %d (track %d)" % [it.lines[k], lw, it.rooms[k], it.xs[k], tr.size.x])
	if tr.size.y + 0.5 < lay.height:
		bad.append("the track %d tall for %d of lines" % [tr.size.y, lay.height])
	return bad


## The live page's harness attempts, when shown: the micro-label, the squares and their count on one line, inside the
## row (the squares never wrap, never past it). What is wrong ([] when nothing).
func _attempts_row_fits() -> Array:
	var sq = dock._l_squares
	if not sq.is_visible_in_tree():
		return []
	var row: Control = dock._l_attempts_row
	var bad := []
	if not row is HBoxContainer:
		bad.append("the attempts' row isn't one box")
	var mid := row.get_global_rect().get_center().y
	for c in [row.get_child(0), sq, dock._l_steps]:
		var r: Rect2 = (c as Control).get_global_rect()
		if absf(r.get_center().y - mid) > 1.5:
			bad.append("%s off the row's line" % c)
		if r.end.x > row.get_global_rect().end.x + 0.5:
			bad.append("%s past the row" % c)
	var rects: Array = sq.squares()
	if rects.is_empty() or rects.any(func(q): return not is_equal_approx(q.position.y, rects[0].position.y)):
		bad.append("the squares not on one line: %s" % [rects])
	if rects.size() != mini(sq.total(), W.Attempts.MAX) and rects.size() * (HT.px(10) + HT.px(4)) + HT.px(10) < sq.size.x - sq._count_w() - HT.px(10):
		bad.append("%d of %d squares drawn with room for more" % [rects.size(), sq.total()])
	if rects.size() > 0 and rects[-1].end.x + HT.px(10) + sq._count_w() > sq.size.x + 0.5:
		bad.append("the count past the squares' room")
	if row.size.y > maxf(sq.get_combined_minimum_size().y, HT.px(18)) + 0.5:
		bad.append("the row %d tall" % row.size.y)
	return bad


## The report's "No recording: ..." line, when shown: as wide as the page, wrapped (onto more lines in the narrowest
## dock) and every line shown. What is wrong ([] when nothing).
func _no_rec_fits(sz: Vector2) -> Array:
	var l: Label = dock._r_no_rec
	if not is_instance_valid(l) or not l.is_visible_in_tree():
		return []
	_no_recs_checked += 1
	var bad := []
	var p: Control = l.get_parent()
	if l.autowrap_mode == TextServer.AUTOWRAP_OFF or l.clip_text or l.text_overrun_behavior != TextServer.OVERRUN_NO_TRIMMING:
		bad.append("the no-recording line doesn't wrap")
	if absf(l.size.x - p.size.x) > 0.5:
		bad.append("the no-recording line %d wide in %d" % [l.size.x, p.size.x])
	if l.get_visible_line_count() < l.get_line_count() or l.size.y + 0.5 < l.get_minimum_size().y:
		bad.append("the no-recording line cut: %d of %d lines" % [l.get_visible_line_count(), l.get_line_count()])
	if sz.x <= LAYOUT_SIZES[0].x and l.text.length() > 80 and l.get_line_count() < 2:
		bad.append("the no-recording line on one line at %d" % sz.x)
	return bad


## The gear (Settings), when shown: one at a time (the rail's head when wide, the top bar's when narrow), its icon
## whole (as wide as it asks at least), inside the dock, beside New run. What is wrong ([] when nothing).
func _gear_fits(sz: Vector2) -> Array:
	var shown_gears: Array = dock._gears.filter(func(b): return b.is_visible_in_tree())
	if dock._body == null or not dock._body.is_visible_in_tree():
		return []
	_gears_checked += 1
	if shown_gears.size() != 1:
		return ["%d gears shown" % shown_gears.size()]
	var b: Button = shown_gears[0]
	var bad := []
	var r := b.get_global_rect()
	var left: float = dock.global_position.x
	if b.size.x + 0.5 < b.get_minimum_size().x:
		bad.append("the gear cut: %d wide for %d" % [b.size.x, b.get_minimum_size().x])
	if r.position.x < left - 0.5 or r.end.x > left + sz.x + 0.5:
		bad.append("the gear past the dock's edge: %s" % r)
	var next: Control = b.get_parent().get_child(b.get_index() + 1) if b.get_index() + 1 < b.get_parent().get_child_count() else null
	if not (next is Button) or next.text != "New run":
		bad.append("the gear not beside New run")
	return bad


## The footer's Reset memory, when shown: its label whole, inside the dock, on the memory line's row and clear of what
## the agent remembers (cut short before it, never under it). What is wrong ([] when nothing).
func _reset_fits(sz: Vector2) -> Array:
	var b: Button = dock._reset_btn
	if not is_instance_valid(b) or not b.is_visible_in_tree():
		return []
	_resets_checked += 1
	var bad := []
	var r := b.get_global_rect()
	var t: Rect2 = dock._save_label.get_global_rect()
	var left: float = dock.global_position.x
	if b.size.x + 0.5 < b.get_minimum_size().x or b.clip_text:
		bad.append("Reset memory cut: %d wide for %d" % [b.size.x, b.get_minimum_size().x])
	if r.position.x < left - 0.5 or r.end.x > left + sz.x + 0.5:
		bad.append("Reset memory past the dock's edge: %s" % r)
	if t.end.x > r.position.x + 0.5 or absf(t.get_center().y - r.get_center().y) > 1.0:
		bad.append("the memory line runs into Reset memory: %s, %s" % [t, r])
	var font: Font = dock._save_label.get_theme_font("font")
	var w: float = font.get_string_size(dock._save_label.text, HORIZONTAL_ALIGNMENT_LEFT, -1, dock._save_label.get_theme_font_size("font_size")).x
	if w > t.size.x + 0.5 and dock._save_label.text_overrun_behavior != TextServer.OVERRUN_TRIM_ELLIPSIS:
		bad.append("the memory line doesn't fit and isn't cut short: %d in %d" % [w, t.size.x])
	return bad


## The result's head, when shown: the run's id (RUN <id> // ..., taking the room left, cut short rather than wrapped)
## and after it, on its row and at its right end, three icon buttons named by their tooltips (Run again in the accent,
## Copy report, Run folder: disabled without a folder), no text on them and no text buttons for them left anywhere;
## the prompt close under the row. What is wrong ([] when nothing).
func _result_head_fits() -> Array:
	if not dock._result.is_visible_in_tree():
		return []
	var idl: Label = null
	for c in dock._result.find_children("*", "Label", true, false):
		if c.is_visible_in_tree() and c.text.begins_with("RUN %s // " % shown_id()):
			idl = c as Label
	if idl == null:
		return ["no run id line"]
	_heads_checked += 1
	var bad := []
	var row: Control = idl.get_parent()
	if not (row is HBoxContainer) or idl.get_index() != 0:
		return ["the run id not first in a row of its own: %s" % row.get_class()]
	if idl.size_flags_horizontal != Control.SIZE_EXPAND_FILL or idl.autowrap_mode != TextServer.AUTOWRAP_OFF or not idl.clip_text:
		bad.append("the run id doesn't take the room left, cut short")
	var btns: Array = row.get_children().slice(1)
	var tips: Array = btns.map(func(b): return b.tooltip_text if b is Button else "")
	if tips != ["Run again", "Copy report", "Run folder"]:
		bad.append("after the run id: %s" % [tips])
	var ig := idl.get_global_rect()
	var x := ig.end.x
	for b in btns:
		if not (b is Button):
			continue
		var g: Rect2 = b.get_global_rect()
		if b.text != "" or b.icon == null or b.icon_alignment != HORIZONTAL_ALIGNMENT_CENTER:
			bad.append("%s not an icon button: \"%s\"" % [b.tooltip_text, b.text])
		if g.position.x < x - 0.5 or absf(g.get_center().y - ig.get_center().y) > 1.0 or b.size.x + 0.5 < b.get_minimum_size().x:
			bad.append("%s not after the run id on its row: %s, %s" % [b.tooltip_text, g, ig])
		x = g.end.x
	if absf(x - row.get_global_rect().end.x) > 0.5:
		bad.append("the icons not at the row's right end: %d of %d" % [x, row.get_global_rect().end.x])
	if btns.size() == 3:
		if btns[0].theme_type_variation != "AutopilotPrimary":
			bad.append("Run again not in the accent")
		if btns[2].disabled != (shown_dir() == ""):
			bad.append("Run folder enabled %s with the folder \"%s\"" % [not btns[2].disabled, shown_dir()])
	for b in dock._result.find_children("*", "Button", true, false):
		if b.text.trim_prefix("▶ ") in ["Run again", "Copy report", "Run folder"]:
			bad.append("the text button %s still there" % b.text)
	var head: Control = row.get_parent()
	var title: Control = head.get_child(row.get_index() + 1) if row.get_index() + 1 < head.get_child_count() else null
	if not (title is Label) or title.get_global_rect().position.y - row.get_global_rect().end.y > HT.px(14 if dock.wide else 12) + 0.5:
		bad.append("the prompt not under the run id's row (14 apart; 12 narrow)")
	return bad


func shown_id() -> String:
	return str(dock.shown.id) if dock.shown != null else ""


func shown_dir() -> String:
	return str(dock.shown.report_dir) if dock.shown != null else ""


## The composer's link row (its state "● CONNECTED", "○ DISCONNECTED"..., the address, and its button: Disconnect, Stop,
## Reconnect), when shown: the three on one row of their own (a box, never a flow), the state whole on one line, the
## button at the row's right end; wide, all three at the right (the state beside the address). What is wrong ([] when
## nothing).
func _link_row_fits() -> Array:
	var l: Label = dock._link_label
	var b: Button = dock._link_btn
	if not b.is_visible_in_tree():
		return []
	_links_checked += 1
	var bad := []
	var row: Control = b.get_parent()
	if l.get_parent() != row or not (row is HBoxContainer) or row.get_child_count() != 3 or l.get_index() != 0 or b.get_index() != 2:
		bad.append("the link text, its address and %s not together in a box: %s" % [b.text, row.get_class()])
	var lg := l.get_global_rect()
	var bg := b.get_global_rect()
	var rg := row.get_global_rect()
	if absf(lg.get_center().y - bg.get_center().y) > 1.0 or lg.end.x > bg.position.x + 0.5:
		bad.append("not on one row: %s, %s" % [lg, bg])
	if l.autowrap_mode != TextServer.AUTOWRAP_OFF or l.size.x + 0.5 < l.get_minimum_size().x:
		bad.append("the link text cut or wrapped: %d for %d" % [l.size.x, l.get_minimum_size().x])
	if absf(bg.end.x - rg.end.x) > 0.5 or lg.position.x < rg.position.x - 0.5:
		bad.append("not at the right: %s, %s in %s" % [lg, bg, rg])
	var ug: Rect2 = dock._link_url.get_global_rect()
	if dock.wide and dock._link_url.text != "" and absf(ug.end.x + HT.px(10) - bg.position.x) > 1.0:
		bad.append("the address not beside %s: %s, %s" % [b.text, ug, bg])
	return bad


## The report's findings headers: shown with findings ("result"), none at all without ("result: no findings").
func _findings_shown(state: String) -> Array:
	if state != "result" and not state.begins_with("result: no findings"):
		return []
	var heads := []
	for c in dock._result.find_children("*", "Label", true, false):
		if c.is_visible_in_tree() and (c.text.begins_with("FINDINGS") or c.text.begins_with("ENGINE ") or c.text == "AGENT FINDING"):
			heads.append(c.text)
	if state == "result":
		_findings_checked += 1
		return [] if heads == ["AGENT FINDING", "ENGINE FINDINGS · 1"] else ["the findings headers: %s" % [heads]]
	_findings_checked += 1
	return [] if heads.is_empty() else ["no findings, yet headers: %s" % [heads]]


## The columns shown (620 composer, 640 Settings, 600 setup, 540 connect and removal): each one's content at most that
## wide and centred.
func _columns_ok() -> Array:
	var bad := []
	for col in dock.find_children("*", "Container", true, false):
		if not (col is W.Column) or not col.is_visible_in_tree():
			continue
		for c in col.get_children():
			if c is Control and c.visible:
				var gap_l: float = c.position.x
				var gap_r: float = col.size.x - c.position.x - c.size.x
				if c.size.x > HT.px(col.max_width) + 0.5 or absf(gap_l - gap_r) > 1.5:
					bad.append("%s: %s in %s" % [dock.get_path_to(col), Rect2(c.position, c.size), col.size])
	return bad


## Why a control is as wide (axis 0) or tall (axis 1) as it is: the chain of children with the largest minimum size.
func _widest_chain(node: Control, axis: int) -> String:
	var out := []
	var n: Control = node
	while n != null:
		var m: Vector2 = n.get_combined_minimum_size()
		out.append("%s%s %d" % [n.get_class(), ("(%s)" % n.text.left(24)) if "text" in n and n.text is String else "", m[axis]])
		var best: Control = null
		for c in n.get_children():
			if c is Control and c.visible and (best == null or c.get_combined_minimum_size()[axis] > best.get_combined_minimum_size()[axis]):
				best = c
		n = best
	return " > ".join(out.slice(maxi(0, out.size() - 9)))


## The setup's column now: [its content's width, the width it should be (the scroll's room less the padding, at most
## 560 px)], or [] when the setup isn't shown.
func _setup_column() -> Array:
	var su = dock._setup
	if not su.is_visible_in_tree():
		return []
	var col: Control = su.get_child(0) if su.get_child_count() > 0 else null
	if not (col is W.Column):
		return []
	var room: float = su.size.x - su.get_v_scroll_bar().get_combined_minimum_size().x - HT.px(col.pad.x) - HT.px(col.pad.z)
	return [col.get_child(0).size.x, minf(room, HT.px(col.max_width))]


## The states, each shown once and then the dock resized through the sizes (back to the narrowest at the end: a page
## shown wide must let the dock narrow again).
func layout_checks() -> void:
	var sizes: Array = LAYOUT_SIZES + [LAYOUT_SIZES[0]]
	_pickers_checked = 0
	_steers_checked = 0
	_no_recs_checked = 0
	_findings_checked = 0
	_gears_checked = 0
	_resets_checked = 0
	_heads_checked = 0
	_links_checked = 0
	var memory := [dock.bridge.memory_known, dock.bridge.memory_runs, dock.bridge.memory_notes]
	for st in _layout_states():
		for k in sizes.size():
			var sz: Vector2 = sizes[k]
			dock.size = sz
			await process_frame
			if k == 0:
				st[1].call()
			for i in 4:
				dock.size = sz  # (as the editor's dock slot does at each layout: the size it gives, once the minimum allows)
				await process_frame
			if st[0].begins_with("setup 1 ("):
				continue
			var out := _sticking_out(sz)
			var min_size: Vector2 = dock.get_combined_minimum_size()
			var cols := _columns_ok()
			var picker := _picker_row() + _placeholder_fits() + _track_fits() + _steer_fits() + _no_rec_fits(sz) + _findings_shown(st[0]) + _gear_fits(sz) + _reset_fits(sz)
			picker += _result_head_fits() + _link_row_fits() + _attempts_row_fits()
			var sc := _setup_column()
			if st[0].begins_with("setup ") and not sc.is_empty():
				if not _setup_widths.has(sz):
					_setup_widths[sz] = {}
				_setup_widths[sz][st[0]] = sc
			var why := []
			if min_size.x > sz.x + 0.5:
				why.append("wide: " + _widest_chain(dock, 0))
			if min_size.y > sz.y + 0.5:
				why.append("tall: " + _widest_chain(dock, 1))
			check("layout %s at %dx%d%s: fits (min %dx%d)" % [st[0], sz.x, sz.y, " again" if k > 0 and sizes.find(sz) < k else "", min_size.x, min_size.y],
				out.is_empty() and why.is_empty() and dock.size == sz and cols.is_empty() and picker.is_empty(),
				[dock.size, why, out.slice(0, 4), cols, picker])
	dock.bridge.memory_known = memory[0]
	dock.bridge.memory_runs = memory[1]
	dock.bridge.memory_notes = memory[2]
	dock._render_footer()
	_plain_view()
	print("key field widths (decision, LLM) by dock size: %s" % _input_widths)
	# the setup's steps 1-4 in one column: as wide at each step as at the others, at each size (narrow and wide), scrolled
	# or not (the scroll bar's room kept), whatever the step shows
	for sz in [LAYOUT_SIZES[0], LAYOUT_SIZES[2], LAYOUT_SIZES[-1]]:
		var ws: Dictionary = _setup_widths.get(sz, {})
		var steps := {}
		var odd := []
		for k in ws:
			steps[str(k).substr(6, 1)] = true
			if absf(ws[k][0] - ws[k][1]) > 0.5:
				odd.append("%s: %d" % [k, ws[k][0]])
		check("layout: the setup's column as wide at steps 1-4 at %dx%d (%d)" % [sz.x, sz.y, ws.values()[0][1] if not ws.is_empty() else 0],
			steps.keys().size() == 4 and odd.is_empty(), [steps.keys(), odd])
	check("layout: the LLM provider row checked in the setup's step 1 at every size", _pickers_checked >= 3 * LAYOUT_SIZES.size(), _pickers_checked)
	check("layout: the steer box checked on the live page at every size", _steers_checked >= 4 * LAYOUT_SIZES.size(), _steers_checked)
	check("layout: the no-recording line checked on the result page at every size", _no_recs_checked >= 2 * LAYOUT_SIZES.size(), _no_recs_checked)
	check("layout: the findings headers checked with and without findings at every size", _findings_checked >= 2 * LAYOUT_SIZES.size(), _findings_checked)
	check("layout: the gear checked at every size", _gears_checked >= 4 * LAYOUT_SIZES.size(), _gears_checked)
	check("layout: the footer's Reset memory checked at every size", _resets_checked >= 4 * LAYOUT_SIZES.size(), _resets_checked)
	check("layout: the result's run id row and icons checked at every size", _heads_checked >= 4 * LAYOUT_SIZES.size(), _heads_checked)
	check("layout: the footer's link text and button checked at every size", _links_checked >= 4 * LAYOUT_SIZES.size(), _links_checked)


## The Feed tab's rows as text, one row a line (the RichTextLabels under _feed_rows, in order).
func _feed_rows_text() -> String:
	var parts := []
	for n in dock._feed_rows.find_children("*", "RichTextLabel", true, false):
		parts.append(n.get_parsed_text().strip_edges())
	return "\n".join(parts)


## The activity feed (docs/protocol.md, `activity`): each frame a line of the run's feed under its stage (which drives the
## stage track), a call and its result one line (the result's words after the call's; two folds), a frame seen already
## (the feed sent again after a reconnect) changing nothing, a result whose call isn't here a line of its own; the Feed
## tab's rows: a heading per stretch of one stage with its count, a fold's toggle; what the hello asks for
## (activity_since); a run saved and read back with its events.
func activity_checks() -> void:
	bridge.run_active = true
	var r = dock._new_run("goal", "Reach the gate")
	dock._on_activity({"type": "activity", "id": 1, "t": 2.0, "stage": "analyze", "kind": "read", "text": "Read codex/README.md",
		"detail": "codex/README.md", "ends": null})
	check("activity: a read, one line at Analyze", r.feed.size() == 1 and r.stage == 1 and r.feed[-1].kind == "activity"
		and int(r.feed[-1].aid) == 1 and int(r.feed[-1].stage) == 1, r.feed)
	# one clock per feed: the line's time is the run's (just started here: about 0), the frame's t kept apart
	check("activity: the line's time is the run's clock, the frame's t kept apart", float(r.feed[-1].t) >= 0.0 and float(r.feed[-1].t) < 30.0
		and is_equal_approx(float(r.feed[-1].agent_t), 2.0), r.feed[-1])
	check("activity: a magnifying glass, a short path folds nothing", Model.activity_icon("read") == "magnifier"
		and Model.activity_title("read") == "Read a file" and Model.detail_label(r.feed[-1]) == "")
	check("activity: an icon per kind", Model.activity_icon("text") == "robot" and Model.activity_icon("search") == "binoculars"
		and Model.activity_icon("write") == "pencil" and Model.activity_icon("probe") == "flask" and Model.activity_icon("episode") == "gamepad"
		and Model.activity_icon("look") == "eye" and Model.activity_icon("restart") == "circular-arrow" and Model.activity_icon("steer") == "speech-bubble"
		and Model.activity_icon("report") == "flag" and Model.activity_icon("alert") == "megaphone" and Model.activity_icon("refine") == "notebook"
		and Model.activity_icon("dream") == "")
	check("activity: a letter per kind", Model.activity_glyph("text") == "?" and Model.activity_glyph("read") == "R" and Model.activity_glyph("search") == "S"
		and Model.activity_glyph("write") == "W" and Model.activity_glyph("probe") == "P" and Model.activity_glyph("episode") == "E"
		and Model.activity_glyph("look") == "L" and Model.activity_glyph("report") == "!" and Model.activity_glyph("alert") == "!"
		and Model.activity_glyph("steer") == ">" and Model.activity_glyph("restart") == "*" and Model.activity_glyph("refine") == "M"
		and Model.activity_glyph("dream") == "·")
	check("activity: a title per kind, none for an unknown one", Model.ACTIVITY_ICONS.keys().all(func(k): return Model.activity_title(k) != "")
		and Model.activity_title("refine") == "Refined the game's memory" and Model.activity_title("dream") == "")
	var drawn := Model.ACTIVITY_ICONS.keys().filter(func(k): return dock._activity_icon(k) != null)
	check("activity: every icon draws (the theme's glyphs outside the editor)", drawn.size() == Model.ACTIVITY_ICONS.size(), drawn)
	check("activity: the fold's words", Model.fold_label("the code", false) == "+ inspect the code" and Model.fold_label("the code", true) == "– hide the code"
		and Model.fold_label("the output", false) == "+ inspect the output")
	dock._on_activity({"type": "activity", "id": 2, "t": 83.4, "stage": "probe", "kind": "probe", "text": "Tried 12 lines in the game",
		"detail": "print(1)\nprint(2)", "ends": null})
	dock._on_activity({"type": "activity", "id": 3, "t": 84.6, "stage": "probe", "kind": "probe", "text": "Got 3 lines back in 1.2 s",
		"detail": "1\n2\n3", "ends": 2})
	var probe: Dictionary = r.find_activity(2)
	check("activity: a probe and its result, one line at Probe", r.feed.size() == 2 and r.stage == 2 and probe.get("result") == "Got 3 lines back in 1.2 s"
		and int(probe.get("rid", 0)) == 3 and probe.get("rdetail") == "1\n2\n3" and r.find_activity(3) == probe, probe)
	check("activity: the folds' words", Model.detail_label(probe) == "the code" and Model.result_label(probe) == "the output")
	check("activity: the last id", r.last_activity_id == 3)
	for again in [{"id": 2, "t": 83.4, "stage": "probe", "kind": "probe", "text": "Tried 12 lines in the game", "detail": "print(1)\nprint(2)", "ends": null},
			{"id": 3, "t": 84.6, "stage": "probe", "kind": "probe", "text": "Got 3 lines back in 1.2 s", "detail": "1\n2\n3", "ends": 2}]:
		dock._on_activity(again)
	check("activity: sent again, nothing changes; the 2 skipped counted for the log", r.feed.size() == 2 and r.stage == 2 and dock._replay_skipped == 2)
	dock._on_activity({"id": 5, "t": 90.0, "stage": "play", "kind": "episode", "text": "Episode 1: 60 s of play (status done)",
		"detail": "The agent walked to the gate.\nIt opened.", "ends": 4})
	check("activity: an orphan result, its own line; play: the Rollout, steerable", r.feed.size() == 3 and r.stage == Model.STAGE_ROLLOUT
		and r.steerable() and Model.detail_label(r.feed[-1]) == "what was asked", r.feed[-1])
	check("activity: a new event after a replay: the skipped count said and cleared", dock._replay_skipped == 0)
	check("activity_since: the live run's last id", dock._activity_since() == 5)
	dock._on_status("Looking at the results")
	check("activity: a status line still moves the stage, under it", r.stage == Model.STAGE_RESULTS and int(r.feed[-1].stage) == Model.STAGE_RESULTS)
	var items: Array = Model.group_feed(r.feed)
	check("group_feed: a heading per stretch of one stage, with its count", items.size() == 8 and items[0].kind == "heading" and items[0].text == "Analyze"
		and items[0].count == 1 and items[2].text == "Probe" and items[4].text == "Play" and items[6].text == "Results" and items[7] == r.feed[-1],
		items.map(func(e): return e.get("text")))
	dock._on_activity({"id": 6, "t": 95.0, "stage": "results", "kind": "text", "text": "The gate opened on the second try",
		"detail": "The gate opened on the second try.\nThe first press came too early.", "ends": null})
	items = Model.group_feed(r.feed)
	check("group_feed: two lines of one stage, one heading", items.size() == 9 and items[6].count == 2 and Model.activity_icon("text") == "robot"
		and Model.detail_label(items[8]) == "the whole message", items.size())
	# the Feed tab: the headings' rows, the probe's folds (closed), its result on its line
	dock.shown = r
	dock.tab = "feed"
	dock._render()
	var text := _feed_rows_text()
	check("Feed tab: the probe line says its result", "Tried 12 lines in the game  Got 3 lines back in 1.2 s" in text, text)
	var toggles: Array = dock._feed_rows.find_children("*", "Button", true, false).map(func(b): return b.text)
	check("Feed tab: the folds, closed", toggles.has("+ inspect the code") and toggles.has("+ inspect the output") and toggles.has("+ inspect the whole message"), toggles)
	var counts := []
	for m in dock._feed_rows.get_children():
		if m.has_meta("count"):
			counts.append(int(m.get_meta("count").text))
	check("Feed tab: a heading per stretch with its count", counts == [1, 1, 1, 2], counts)
	var fold_button: Button = dock._feed_rows.find_children("*", "Button", true, false).filter(func(b): return b.text == "+ inspect the code")[0]
	fold_button.pressed.emit()
	check("Feed tab: a fold opens (– hide the code), its text box shown", fold_button.text == "– hide the code"
		and dock._feed_rows.find_children("*", "TextEdit", true, false).any(func(t): return t.visible and t.text == "print(1)\nprint(2)"))
	# the refiner's edit to the game's memory: a notebook in the write's colour, its fold the edit and its evidence
	dock._on_activity({"id": 7, "t": 97.0, "stage": "results", "kind": "refine", "text": "Added to advice.md after episode 2: Open the crate straight away",
		"detail": "+ Open the crate straight away\nEvidence: episode 2 reached the gate at 41 s", "ends": null})
	check("activity: a refine line, a notebook, its fold the edit and its evidence", r.feed[-1].get("akind") == "refine"
		and Model.activity_icon("refine") == "notebook" and dock._activity_icon("refine") != null and dock._mark_color("refine") == HT.INFO
		and Model.detail_label(r.feed[-1]) == "the edit and its evidence" and Model.result_label(r.feed[-1]) == "", r.feed[-1])
	# saved and read back with its events; nothing asked for a finished run
	bridge.run_active = false
	dock._on_result({"kind": "goal", "reached": true, "goal": "Reach the gate"}, "")
	check("activity_since: nothing for a finished run", dock._activity_since() == -1)
	var back = History.from_dict(History.to_dict(r))
	check("history: the events read back", back != null and back.feed.size() == r.feed.size() and back.last_activity_id == 7
		and back.find_activity(2).get("result") == "Got 3 lines back in 1.2 s" and int(back.find_activity(2).get("stage", -1)) == 2,
		back.feed.size() if back != null else null)
