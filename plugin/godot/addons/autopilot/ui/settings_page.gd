## The dock's Settings page: its header (Settings, Back to runs), then at most 640 px wide: the Account plate (how runs
## go: Aura, the Aura app, BYOK, or not set up, from the agent service's account frame and preferences.json; with Aura,
## what Autopilot billed the account this period and Aura's words when the team is out of credits; the agent service's
## line; Add credits, Rerun setup, Sign out, Sign in again), the
## project's runs ("This project · N runs", what Aura billed for them, and each run: its time, prompt and bill), the
## Data sharing choice (preferences.json share_data), Claude Code and Codex (running Autopilot from the user's own
## coding agent: the commands that set it up, to copy) and, last, Remove Autopilot (from this computer, confirmed in
## place). The data comes from the dock (show_data()); the numbers from ui/usage.gd.
@tool
extends VBoxContainer

signal back
signal share_toggled(on: bool)
## The Account plate's buttons: "setup" (Rerun setup: the first-run setup from its first step), "sign_out" (confirmed),
## "sign_in", "cancel_sign_in", and on the agent service's line "install", "open_log"
signal account_action(action: String)
## Remove Autopilot from this computer, confirmed (keep_memory: Delete Autopilot's memories left unticked)
signal remove_asked(keep_memory: bool)

const HT = preload("autopilot_theme.gd")
const W = preload("widgets.gd")
const Usage = preload("usage.gd")
const Model = preload("model.gd")
const Prefs = preload("../lib/prefs.gd")

const MAX_WIDTH := 640.0  # the content's width at most (centred in a wider dock)
## The sharing choice's text (the same as the setup's Privacy settings step)
const SHARING := ("Enabling this sends recordings, screenshots, and agent traces and memories to the developers of Autopilot."
	+ " For runs from your own Claude Code or Codex, the agent traces are its conversation during the run.")
## The Autopilot in Claude Code & Codex plate: running Autopilot from the user's own coding agent (`autopilot connect`)
const ASSISTANTS_TITLE := "Autopilot in Claude Code & Codex"
const ASSISTANTS_INTRO := ("One-time CLI setup to run Autopilot through Claude Code or Codex using your Claude or ChatGPT "
	+ "subscriptions, respectively.")
const ASSISTANTS_COMMANDS := ["autopilot connect claude", "autopilot connect codex"]
const SHARING_TITLE := "Share runs to improve Autopilot"
const SHARING_ON := "Data sharing on"  # (the switch's words, as it stands)
const SHARING_OFF := "Data sharing off"
## The setting plates' spacing (Sharing, Claude Code & Codex): title to subtitle, subtitle to its control,
## between the command rows
const TITLE_GAP := 6.0
const CONTROL_GAP := 10.0
const ROW_GAP := 6.0
## The removal's confirmation, under its question
const REMOVE_TEXT := ("Removes the agent service, your keys, your Aura sign-in and your setup choices, for every project.")
const DELETE_MEMORY := ("[Optional] Delete Autopilot’s memories for all projects in this machine across all editors. "
	+ "I understand that this action is destructive and deletes Autopilot’s memories for my entire team.")

var _scroll: ScrollContainer
var _v: VBoxContainer
var confirming_sign_out := false
var confirming_removal := false
var delete_memory := false  # (the removal's Delete Autopilot's memories box, unticked whenever it asks)
var _busy := false  # a run is going: the buttons that change the account are disabled
var _last := []  # show_data's arguments (built again when Sign out asks to confirm)
## What Copy does with a command (the clipboard; checks put a stand-in here: a headless Godot has no clipboard)
var copy_text := func(text: String) -> void: DisplayServer.clipboard_set(text)


func _init() -> void:
	add_theme_constant_override("separation", 0)
	size_flags_vertical = Control.SIZE_EXPAND_FILL
	size_flags_horizontal = Control.SIZE_EXPAND_FILL
	# the header: Settings, Back to runs, over a rule
	var head := PanelContainer.new()
	head.theme_type_variation = "AutopilotHead"
	add_child(head)
	var h := HBoxContainer.new()
	h.add_theme_constant_override("separation", HT.px(8))
	head.add_child(h)
	var title := _label("Settings", "AutopilotTitle", false)
	title.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	title.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	h.add_child(title)
	h.add_child(HT.button("Back to runs", "AutopilotQuiet", func(): back.emit(), true))
	_scroll = ScrollContainer.new()
	_scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	_scroll.size_flags_vertical = Control.SIZE_EXPAND_FILL
	add_child(_scroll)


static func _caps(text: String, color := HT.MUTED) -> Label:
	var l := Label.new()
	l.theme_type_variation = "AutopilotCaps"
	l.text = text.to_upper()
	if color != HT.MUTED:
		l.add_theme_color_override("font_color", color)
	l.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	return l


static func _label(text: String, variation := "AutopilotBody", wrap := true) -> Label:
	var l := Label.new()
	l.theme_type_variation = variation
	l.text = text
	if wrap:
		l.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	return l


## A small button (24 tall), sentence case.
static func _button(text: String, variation: String, action: Callable) -> Button:
	return HT.button(text, variation, action, true)


## A plate (radius 8, a 1 px border: RULE, or border) holding a column (sep apart) or a row; returns [plate, box].
func _plate(row := false, sep := 10.0, border := HT.RULE, margins := Vector4(14, 12, 14, 12)) -> Array:
	var box: BoxContainer = HBoxContainer.new() if row else VBoxContainer.new()
	box.add_theme_constant_override("separation", HT.px(sep))
	var p := PanelContainer.new()
	p.add_theme_stylebox_override("panel", HT.plate_box(margins, border))
	p.add_child(box)
	return [p, box]


## The page's plates, built again. entries: the runs with usage or a bill ({"id", "started_unix", "kind", "prompt",
## "verdict", "usage", "bill"}, oldest first, listed newest first); share:
## preferences.json's share_data; note: a line under the sharing choice ("" for none); account: the Account plate
## (account_view()).
func show_data(entries: Array, share: bool, note := "", account := {}) -> void:
	_last = [entries, share, note, account]
	_busy = account.get("busy", false) == true
	for c in _scroll.get_children():
		_scroll.remove_child(c)
		c.queue_free()
	_v = VBoxContainer.new()
	_v.add_theme_constant_override("separation", HT.px(12))
	var col := W.Column.new(MAX_WIDTH, Vector4(20, 18, 20, 18))
	col.add_child(_v)
	_scroll.add_child(col)
	if not account.is_empty():
		_v.add_child(_account_card(account))
	_v.add_child(_runs_list(entries))
	_v.add_child(_sharing_card(share, note))
	_v.add_child(_assistants_card())
	_v.add_child(_remove_card())


## A setting's plate, as every one is laid out: its title (mono caps), what it does (as the sharing text is), then the
## control that changes it.
func _setting_card(title: String, subtitle: String, control: Control) -> PanelContainer:
	var pv := _plate(false, TITLE_GAP)
	var v: VBoxContainer = pv[1]
	v.add_child(_caps(title))
	v.add_child(_label(subtitle, "AutopilotSmall"))
	v.add_child(_margin(control, 0, CONTROL_GAP - TITLE_GAP, 0, 0))
	return pv[0]


## Share runs to improve Autopilot: the switch (preferences.json share_data) with "Data sharing on/off" to its right (its
## own text: pressing the words flips it too), then the note under it when there is one.
func _sharing_card(share: bool, note: String) -> PanelContainer:
	var box := VBoxContainer.new()
	box.add_theme_constant_override("separation", HT.px(ROW_GAP))
	var share_box := CheckBox.new()
	share_box.theme_type_variation = "AutopilotSwitch"
	share_box.tooltip_text = SHARING_TITLE
	share_box.add_theme_constant_override("h_separation", HT.px(8))
	share_box.size_flags_horizontal = Control.SIZE_SHRINK_BEGIN
	share_box.button_pressed = share
	share_box.text = SHARING_ON if share else SHARING_OFF
	share_box.toggled.connect(func(on: bool) -> void:
		share_box.text = SHARING_ON if on else SHARING_OFF
		share_toggled.emit(on))
	box.add_child(share_box)
	if note != "":
		var n := _label(note, "AutopilotMonoDim")
		n.add_theme_color_override("font_color", HT.RED)
		box.add_child(n)
	return _setting_card(SHARING_TITLE, SHARING, box)


## Autopilot in Claude Code & Codex: running Autopilot from the user's own coding agent, set up once with a command
## (each with Copy, to the clipboard).
func _assistants_card() -> PanelContainer:
	var v := VBoxContainer.new()
	v.add_theme_constant_override("separation", HT.px(ROW_GAP))
	for command in ASSISTANTS_COMMANDS:
		var row := HBoxContainer.new()
		row.add_theme_constant_override("separation", HT.px(8))
		var c := _label(command, "AutopilotMono", false)
		c.add_theme_color_override("font_color", HT.TEXT)
		c.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		c.size_flags_vertical = Control.SIZE_SHRINK_CENTER
		c.clip_text = true
		row.add_child(c)
		var copy := _button("Copy", "Button", func() -> void: copy_text.call(command))
		copy.tooltip_text = "Copy the command"
		row.add_child(copy)
		v.add_child(row)
	return _setting_card(ASSISTANTS_TITLE, ASSISTANTS_INTRO, v)


## This project's runs: "This project · N runs", what Aura billed for them, then each run, newest first (its time,
## prompt and bill: a list, nothing to pick).
func _runs_list(entries: Array) -> PanelContainer:
	var with_usage := entries.filter(func(e):
		return not (e.get("usage", []) as Array).is_empty() or Usage.billed(e.get("bill")) >= 0.0)
	var pv := _plate(false, 0, HT.RULE, Vector4(0, 0, 0, 0))
	var v: VBoxContainer = pv[1]
	var head := HBoxContainer.new()
	head.add_theme_constant_override("separation", HT.px(10))
	var t := _caps("This project · %d run%s" % [with_usage.size(), "" if with_usage.size() == 1 else "s"])
	t.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	t.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART  # (a narrow dock: onto two lines beside the bill)
	head.add_child(t)
	var project_bill := Usage.bill_total(with_usage)
	if project_bill[1] > 0:
		var bh := HBoxContainer.new()
		bh.add_theme_constant_override("separation", HT.px(4))
		var bl := _label("Billed by Aura", "AutopilotBody", false)
		bl.add_theme_font_size_override("font_size", HT.px(12))
		bl.add_theme_color_override("font_color", HT.TEXT)
		bh.add_child(bl)
		var amount := _label(Usage.money(project_bill[0]), "AutopilotMono", false)
		amount.add_theme_font_size_override("font_size", HT.px(12))
		amount.add_theme_color_override("font_color", HT.TEXT)
		bh.add_child(amount)
		head.add_child(bh)
	v.add_child(_margin(head, 14, 9, 14, 9))
	v.add_child(HT.rule())
	var body := VBoxContainer.new()
	body.add_theme_constant_override("separation", 0)
	v.add_child(_margin(body, 8, 4, 8, 8))
	if with_usage.is_empty():
		# (no runs to list: the plate says so, under its micro-label)
		var e := _plate(false, 6)
		e[1].add_child(_caps("This project"))
		e[1].add_child(_label("No usage yet.", "AutopilotDim"))
		pv[0].free()
		return e[0]
	body.add_child(_grid_row([_caps("Time"), _caps("Prompt"), _caps("Billed")], Vector4(8, 6, 8, 6)))
	for i in range(with_usage.size() - 1, -1, -1):
		body.add_child(_run_row(with_usage[i]))
	return pv[0]


## A row of the runs' table: 96 px, the rest, 90 px (right-aligned), 8 apart.
func _grid_row(cells: Array, margins: Vector4) -> MarginContainer:
	var h := HBoxContainer.new()
	h.add_theme_constant_override("separation", HT.px(8))
	h.mouse_filter = Control.MOUSE_FILTER_IGNORE
	cells[0].custom_minimum_size.x = HT.px(96)
	cells[1].size_flags_horizontal = Control.SIZE_EXPAND_FILL
	cells[1].clip_text = true
	cells[2].custom_minimum_size.x = HT.px(90)
	cells[2].horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT
	for c in cells:
		c.mouse_filter = Control.MOUSE_FILTER_IGNORE
		h.add_child(c)
	var m := _margin(h, margins.x, margins.y, margins.z, margins.w)
	m.mouse_filter = Control.MOUSE_FILTER_IGNORE
	return m


static func _margin(child: Control, l: float, t: float, r: float, b: float) -> MarginContainer:
	var m := MarginContainer.new()
	m.add_theme_constant_override("margin_left", HT.px(l))
	m.add_theme_constant_override("margin_top", HT.px(t))
	m.add_theme_constant_override("margin_right", HT.px(r))
	m.add_theme_constant_override("margin_bottom", HT.px(b))
	m.add_child(child)
	return m


## A run of the list: its time, prompt and bill (not a button: nothing to pick, no hover).
func _run_row(e: Dictionary) -> Control:
	var p := PanelContainer.new()
	p.add_theme_stylebox_override("panel", HT.box(Color.TRANSPARENT, 0, Color.TRANSPARENT, 0, Vector4(8, 7, 8, 7)))
	p.mouse_filter = Control.MOUSE_FILTER_IGNORE
	var when := _label(Model.when(float(e.get("started_unix", 0.0))), "AutopilotMono", false)
	var prompt := str(e.get("prompt", ""))
	var pl := _label(Model.prompt_shown(prompt, str(e.get("kind", ""))), "AutopilotBody", false)
	pl.add_theme_font_size_override("font_size", HT.px(12.5))
	pl.add_theme_color_override("font_color", HT.TEXT)
	pl.clip_text = true
	pl.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	var bill := Usage.billed(e.get("bill"))
	var bl := _label(Usage.money(bill) if bill >= 0.0 else "—", "AutopilotMono", false)
	bl.add_theme_color_override("font_color", HT.TEXT if bill >= 0.0 else HT.MUTED)  # (not billed: a muted dash)
	var row := _grid_row([when, pl, bl], Vector4(0, 0, 0, 0))
	p.add_child(row)
	return p


## Remove Autopilot: from this computer, for every project and editor (the dock runs `autopilot uninstall`), confirmed
## in place with Delete Autopilot's memories (an acknowledgement: unticked each time it asks).
func _remove_card() -> PanelContainer:
	var confirm := confirming_removal and not _busy
	if not confirm:
		# (one row: its name, then the button; the button under it in a narrow dock)
		var pv := _plate(false, 6)
		var f := _flow()
		var title := _caps("Uninstall Autopilot")
		title.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		f.add_child(title)
		f.add_child(_while_idle(_button("Uninstall", "AutopilotDanger", func():
			confirming_removal = true
			delete_memory = false
			_rebuild())))
		pv[1].add_child(f)
		return pv[0]
	var cv := _plate(false, 8, HT.RED_LINE)
	var v: VBoxContainer = cv[1]
	v.add_child(_caps("Uninstall Autopilot"))
	v.add_child(_label("Uninstall Autopilot from this computer?", "AutopilotTitle"))
	v.add_child(_label(REMOVE_TEXT, "AutopilotSmall"))
	var delete := CheckBox.new()
	delete.text = DELETE_MEMORY
	delete.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	delete.button_pressed = delete_memory
	delete.toggled.connect(func(on: bool) -> void: delete_memory = on)
	v.add_child(delete)
	var buttons := _flow()
	buttons.add_child(_button("Uninstall", "AutopilotDangerFill", func():
		confirming_removal = false
		remove_asked.emit(not delete_memory)))
	buttons.add_child(_button("Cancel", "AutopilotQuiet", func():
		confirming_removal = false
		_rebuild()))
	v.add_child(_margin(buttons, 0, 2, 0, 0))
	return cv[0]


static func _kind_word(kind: String) -> String:
	match kind:
		"verify": return "Verify"
		"goal": return "Goal"
		"explore": return "Explore"
	return "Run"


# --- the Account plate -------------------------------------------------------------------------------

## What the Account plate shows: {"kind": "aura" | "aura_app" | "own_keys" | "none" | "signed_out", "email",
## "keys": [decision, llm] (masked endings, null when not set), "coding_agent": the Coding Agent BYOK runs use (the
## frame's, "opencode" when it has none: an older agent service), "llm_provider": the LLM provider of that key (the
## frame's; when it has none, the one the Coding Agent takes, "anthropic" for OpenCode), "service": the agent
## service's line ({"text", "url", "action": "install" | "open_log" | ""}, {} for none), "sign_in": the dock's sign-in,
## "bill": what Autopilot billed the Aura account this period (ui/usage.gd parse_account_bill(), {} for none: not Aura, or Aura couldn't say),
## "credits": Aura's words when the team is out of credits (ui/usage.gd parse_out_of_credits(), {} while it has some),
## "busy": a run is going (the sign-in can't change until it is over: the dock sets it)}.
## frame: the agent service's last account frame ({} when none came); access: preferences.json's; with_aura: Use with
## Aura (the Aura app's agent service).
static func account_view(frame: Dictionary, access, with_aura: bool, service_line := {}, sign_in := {}) -> Dictionary:
	var view := {"kind": "none", "email": "", "keys": [null, null], "coding_agent": Prefs.DEFAULT_AGENT,
		"llm_provider": "anthropic", "service": service_line, "sign_in": sign_in, "bill": {}, "credits": {}, "busy": false}
	if with_aura:
		view.kind = "aura_app"
		view["bill"] = Usage.parse_account_bill(frame.get("bill"))  # (what the Aura app's account was billed, when it says)
		view["credits"] = Usage.parse_out_of_credits(frame.get("out_of_credits"))
		return view
	var mode = frame.get("mode")
	var keys = frame.get("keys", {})
	if typeof(keys) == TYPE_DICTIONARY:
		view["keys"] = [keys.get("decision") if typeof(keys.get("decision")) == TYPE_STRING else null,
			keys.get("llm") if typeof(keys.get("llm")) == TYPE_STRING else null]
	if typeof(frame.get("email")) == TYPE_STRING:
		view.email = frame.email
	if typeof(frame.get("coding_agent")) == TYPE_STRING and frame.coding_agent.strip_edges() != "":
		view.coding_agent = frame.coding_agent.strip_edges()
	if typeof(frame.get("llm_provider")) == TYPE_STRING and frame.llm_provider.strip_edges() != "":
		view.llm_provider = frame.llm_provider.strip_edges()
	else:
		view.llm_provider = Prefs.AGENT_PROVIDERS.get(view.coding_agent.to_lower(), "anthropic")
	if mode == "aura":
		view.kind = "aura" if frame.get("signed_in") == true else "signed_out"
		if view.kind == "aura":
			view["bill"] = Usage.parse_account_bill(frame.get("bill"))
			view["credits"] = Usage.parse_out_of_credits(frame.get("out_of_credits"))
	elif mode == "own_keys":
		view.kind = "own_keys"
	elif mode == "none":
		view.kind = "none"
	elif access == "aura":
		view.kind = "aura"  # (no account frame yet: as the setup chose)
	elif access == "own_keys":
		view.kind = "own_keys"
	return view


## The Account plate's tag for the kind: [text, tag kind].
const ACCOUNT_TAGS := {"aura": ["Aura", "ok"], "aura_app": ["Aura app", "ok"], "own_keys": ["BYOK", "mut"],
	"signed_out": ["Signed out", "bad"], "none": ["Not set up", "warn"]}


func _account_card(a: Dictionary) -> PanelContainer:
	var kind := str(a.get("kind", "none"))
	var email := str(a.get("email", ""))
	_busy = a.get("busy", false) == true
	var confirm := kind == "aura" and confirming_sign_out
	var pv := _plate(false, 10, HT.RED_LINE if confirm else HT.RULE)
	var v: VBoxContainer = pv[1]
	var head := HFlowContainer.new()
	head.add_theme_constant_override("h_separation", HT.px(8))
	head.add_child(_caps("Account"))
	var tag: Array = ACCOUNT_TAGS.get(kind, ACCOUNT_TAGS.none)
	head.add_child(HT.tag(tag[0], tag[1]))
	v.add_child(head)
	var si: Dictionary = a.get("sign_in", {})
	var signing := str(si.get("state", ""))
	var buttons := _flow()
	if confirm:
		v.add_child(_label("Sign out %s on this computer?" % (email if email != "" else "of Aura"), "AutopilotTitle"))
		v.add_child(_label("Every editor on this computer stops using Aura until you sign in again.", "AutopilotDim"))
		buttons.add_child(_button("Sign out", "AutopilotDangerFill", func():
			confirming_sign_out = false
			account_action.emit("sign_out")))
		buttons.add_child(_button("Cancel", "AutopilotQuiet", func():
			confirming_sign_out = false
			_rebuild()))
		v.add_child(_margin(buttons, 0, 2, 0, 0))
		return pv[0]
	if kind in ["signed_out", "none"] and signing in ["preparing", "waiting", "error", "no_installer"]:
		_sign_in_in_place(v, si)
		return pv[0]
	match kind:
		"aura":
			if email != "":
				_email(v, email)
			_period(v, a.get("bill", {}))
			_credits(v, buttons, a.get("credits", {}))
			_service(v, a.get("service", {}))
			buttons.add_child(_rerun_button(false))
			var sign_out := _button("Sign out", "AutopilotDanger", func():
				confirming_sign_out = true
				_rebuild())
			if a.get("connected", true) == false:
				# (signing out is the agent service's: not without it)
				sign_out.disabled = true
				sign_out.tooltip_text = "Connect to the agent service to sign out"
				sign_out.mouse_filter = Control.MOUSE_FILTER_PASS
			buttons.add_child(sign_out)
		"aura_app":
			v.add_child(_label("The Aura app’s agent service", "AutopilotTitle"))
			_period(v, a.get("bill", {}))
			_credits(v, buttons, a.get("credits", {}))
			buttons.add_child(_rerun_button(false))
		"own_keys":
			v.add_child(_label("Your own keys", "AutopilotTitle"))
			var keys: Array = a.get("keys", [null, null])
			var parts := []
			if keys[0] != null:
				parts.append("Decision provider " + str(keys[0]))
			if keys[1] != null:
				parts.append("%s (%s) %s" % [Prefs.agent_name(a.get("coding_agent")), Prefs.llm_name(a.get("llm_provider")), keys[1]])
			if not parts.is_empty():
				var k := _label("Saved on this computer: " + " · ".join(parts), "AutopilotMono")
				k.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
				v.add_child(k)
			_service(v, a.get("service", {}))
			buttons.add_child(_rerun_button(false))
		"signed_out":
			if email != "":
				_email(v, email)
			var ended := _label("Your Aura sign-in on this computer ended.")
			ended.add_theme_color_override("font_color", HT.RED)
			v.add_child(ended)
			_service(v, a.get("service", {}))
			buttons.add_child(_button("Sign in again", "AutopilotPrimary", func(): account_action.emit("sign_in")))
			buttons.add_child(_rerun_button(false, true))
		_:
			v.add_child(_label("Choose how to run Autopilot", "AutopilotTitle"))
			_service(v, a.get("service", {}))
			buttons.add_child(_rerun_button(true))
	# (the buttons under a rule)
	var foot := VBoxContainer.new()
	foot.add_theme_constant_override("separation", HT.px(10))
	foot.add_child(HT.rule())
	foot.add_child(buttons)
	v.add_child(foot)
	return pv[0]


func _email(v: VBoxContainer, email: String) -> void:
	var e := _label(email, "AutopilotTitle")
	e.autowrap_mode = TextServer.AUTOWRAP_ARBITRARY
	v.add_child(e)


## What Autopilot billed the Aura account this period: "Billing period (Oct 1 – Oct 31): $12.34 USD", the amount in mono
## (nothing when Aura couldn't say).
func _period(v: VBoxContainer, bill) -> void:
	if typeof(bill) != TYPE_DICTIONARY or bill.is_empty():
		return
	var line := Usage.period_line(bill)
	var at := line.rfind(": ")
	var f := HFlowContainer.new()
	f.add_theme_constant_override("h_separation", HT.px(4))
	f.add_child(_label(line.substr(0, at + 1), "AutopilotBody", false))
	f.add_child(_label(line.substr(at + 2), "AutopilotMono", false))
	(f.get_child(1) as Label).add_theme_font_size_override("font_size", HT.px(13))
	(f.get_child(1) as Label).add_theme_color_override("font_color", HT.TEXT2)
	v.add_child(f)


## Aura's words when the team is out of credits, in red under the billing period, and Add credits (the page Aura names)
## first in the card's buttons (nothing while it has credits).
func _credits(v: VBoxContainer, buttons: HFlowContainer, credits) -> void:
	if typeof(credits) != TYPE_DICTIONARY or credits.is_empty():
		return
	var l := _label(str(credits.get("message", "")))
	l.add_theme_color_override("font_color", HT.RED)
	v.add_child(l)
	var url := str(credits.get("url", ""))
	if url != "":
		buttons.add_child(_button("Add credits", "Button", func(): OS.shell_open(url)))


## The agent service's line under the account: a status dot (a spinner while it starts or installs), its state, its
## address, and Install or View logs (small, on the same line) when it needs one.
func _service(v: VBoxContainer, line) -> void:
	if typeof(line) != TYPE_DICTIONARY or str(line.get("text", "")) == "":
		return
	var action := str(line.get("action", ""))
	var text := str(line.text)
	var row := HFlowContainer.new()
	row.add_theme_constant_override("h_separation", HT.px(8))
	row.add_theme_constant_override("v_separation", HT.px(4))
	var color := HT.MUTED
	var text_color := HT.TEXT3
	if text.ends_with(": running"):
		color = HT.SUCCESS
	elif action == "open_log" or text.begins_with("The agent service didn"):
		color = HT.RED
		text_color = HT.RED
	elif text.ends_with("not installed"):
		color = HT.AMBER
		text_color = HT.AMBER
	if line.get("busy", false) == true:
		row.add_child(W.Spinner.new())
	else:
		row.add_child(HT.dot(color))
	var t := _label(text, "AutopilotSmall", false)
	t.add_theme_color_override("font_color", text_color)
	t.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	row.add_child(t)
	if str(line.get("url", "")) != "":
		var u := _label(str(line.url), "AutopilotMonoDim", false)
		u.size_flags_vertical = Control.SIZE_SHRINK_CENTER
		row.add_child(u)
	match action:
		"install":
			row.add_child(_button("Install", "Button", func(): account_action.emit("install")))
		"open_log":
			row.add_child(_button("View logs", "Button", func(): account_action.emit("open_log")))
	v.add_child(row)


static func _flow() -> HFlowContainer:
	var f := HFlowContainer.new()
	f.add_theme_constant_override("h_separation", HT.px(6))
	f.add_theme_constant_override("v_separation", HT.px(6))
	return f


## Rerun setup: the first-run setup again, from its first step (Aura, the Aura app or BYOK, the agent service, privacy).
func _rerun_button(primary: bool, quiet := false) -> Button:
	var variation := "AutopilotPrimary" if primary else ("AutopilotQuiet" if quiet else "Button")
	var b := _while_idle(_button("Rerun setup", variation, func(): account_action.emit("setup")))
	if not b.disabled:
		b.tooltip_text = "The first-run setup again: your account (Aura or BYOK), the agent service, your privacy settings"
	return b


## A button that changes the account: disabled while a run is going (it would lose its connection), saying why.
func _while_idle(b: Button) -> Button:
	if _busy:
		b.disabled = true
		b.tooltip_text = "Finish or stop the run first"
		b.mouse_filter = Control.MOUSE_FILTER_PASS
	return b


## Sign in again, in the Account plate: preparing the agent service, waiting for the browser, or what went wrong.
func _sign_in_in_place(v: VBoxContainer, si: Dictionary) -> void:
	var row := _flow()
	var state := str(si.get("state", ""))
	match state:
		"preparing":
			var h := HBoxContainer.new()
			h.add_theme_constant_override("separation", HT.px(10))
			h.add_child(W.Spinner.new())
			h.add_child(_label(str(si.get("progress", "Starting the agent service…")), "AutopilotStrong"))
			v.add_child(h)
		"waiting":
			var h := HBoxContainer.new()
			h.add_theme_constant_override("separation", HT.px(10))
			h.add_child(W.Spinner.new())
			h.add_child(_label("Finish signing in in your browser…", "AutopilotTitle"))
			v.add_child(h)
			var url := str(si.get("url", ""))
			var u := _label(url, "AutopilotMono")
			u.autowrap_mode = TextServer.AUTOWRAP_ARBITRARY
			v.add_child(u)
			row.add_child(_button("Open again", "Button", func(): OS.shell_open(url)))
			row.add_child(_button("Copy link", "AutopilotQuiet", func(): DisplayServer.clipboard_set(url)))
		"no_installer":
			var l := _label("No agent service found. Set Agent service command under Advanced, or run autopilot serve.")
			l.add_theme_color_override("font_color", HT.AMBER)
			v.add_child(l)
		_:
			var l := _label(str(si.get("message", "")))
			l.add_theme_color_override("font_color", HT.RED)
			v.add_child(l)
			row.add_child(_button("Try again", "AutopilotPrimary", func(): account_action.emit("sign_in")))
	if state == "error":
		row.add_child(_rerun_button(false))
	else:
		row.add_child(_button("Cancel", "AutopilotQuiet", func(): account_action.emit("cancel_sign_in")))
	v.add_child(row)


func _dim(text: String) -> Label:
	return _label(text, "AutopilotDim")


func _rebuild() -> void:
	if _last.size() == 4:
		show_data(_last[0], _last[1], _last[2], _last[3])
