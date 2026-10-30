## The first-run setup, shown in the dock in place of everything else until preferences.json says "onboarded" (and
## again from Rerun setup on the Settings page), with a stepper: 1 ACCOUNT, 2 AGENT SERVICE, 3 PRIVACY
## SETTINGS, and 4 FIRST RUN unless the agent service said it remembers something about this game. On a computer set up
## before whose agent service is gone (not installed, none running), it opens at step 2 (open_at_service()).
##  1. Account: Create Account (Aura's account creation page in the browser, then Sign in) or Sign in with Aura (the
##     agent service signs in, in the browser: the dock starts it and asks); or Bring your own keys (BYOK,
##     written to Autopilot/autopilot.env with the Coding Agent picked and the LLM provider beside its key); or Decide
##     later (to step 3).
##  2. Agent service: installed (once) and started by the dock; ready, or what went wrong.
##  3. Privacy settings: whether to share runs, and a recap. Next (Finish when the memory has something) emits
##     finished(access, share, use_aura): the dock writes preferences.json, sets Use with Aura and connects, then closes
##     the setup or goes on to step 4.
##  4. First run: once connected, with the game's memory empty, an Explore run is offered (recommended, skippable:
##     Explore and finish, Skip and finish); with something remembered the setup closes by itself.
## What it shows comes from the dock (host): its sign-in (host.sign_in), its agent service (host.service) and the
## connection; it is built again when any of it changes (refresh(), and a look every 0.25 s).
@tool
extends ScrollContainer

signal finished(access: String, share: bool, use_aura: bool)  # access: "aura", "own_keys", "" (Decide later)

const HT = preload("autopilot_theme.gd")
const W = preload("widgets.gd")
const Prefs = preload("../lib/prefs.gd")
const Model = preload("model.gd")

const DISCLOSURE := ("Enabling this sends recordings, screenshots, and agent traces and memories to the developers of Autopilot."
	+ " For runs from your own Claude Code or Codex, the agent traces are its conversation during the run.")
## The Aura card's line, with the Aura app or without
const AURA_BILLED := ("Autopilot is maintained by the developers behind Aura, the multiagent AI solution to Unreal and "
	+ "Unity game development. You can use your Aura subscription to run Autopilot. New users get free Aura credits.")

var host  # dock.gd
var access := ""  # "aura", "own_keys", "" (not chosen)
var use_aura := false  # the Aura app's agent service (Use with Aura)
var share := true
var email := ""  # signed in to Aura as
var step := 1
var _visited2 := false
var _keys_shown := false  # the keys card opened (Show; Hide folds it again)
var creating_account := false  # Create Account opened Aura's account creation page: the Aura card says Sign in next
var open_url := func(url: String): OS.shell_open(url)  # (the tests' own: no browser opened from a test)
var _v: VBoxContainer
var _typesafe: LineEdit
var _llm_key: LineEdit
var _agent: OptionButton
var _provider: OptionButton
var coding_agent := ""  # the Coding Agent picked in its dropdown ("" until one is: preselected_agent())
var llm_provider := ""  # the LLM provider last picked for OpenCode ("" until one is: preselected_provider())
var _llm_key_for := ""  # the LLM provider the LLM key's field is for ("" until one is shown)
var _keys_note: Label
var _command: LineEdit
var _key := []
var _check_at := 0.0
const MAX_WIDTH := 600.0  # the content's width at most (centred in a wider dock)


func _init() -> void:
	horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	# (the scroll bar's room kept on every step, scrolled or not: each step's column as wide as the others)
	vertical_scroll_mode = ScrollContainer.SCROLL_MODE_RESERVE
	size_flags_vertical = Control.SIZE_EXPAND_FILL
	size_flags_horizontal = Control.SIZE_EXPAND_FILL


## Start over at step 1, the sharing box set as preferences.json says.
func open(share_now: bool) -> void:
	access = ""
	use_aura = false
	email = ""
	share = share_now
	step = 1
	_visited2 = false
	_keys_shown = false
	creating_account = false
	coding_agent = ""
	llm_provider = ""
	_llm_key_for = ""
	for e in [_typesafe, _llm_key]:
		if is_instance_valid(e):
			e.text = ""
	_key = []
	refresh()


## Start at step 2 (Agent service), for a computer set up before whose agent service is gone: the account as chosen
## then (access_now, preferences.json's), the sharing box as preferences.json says.
func open_at_service(share_now: bool, access_now: String) -> void:
	open(share_now)
	access = access_now if access_now in ["aura", "own_keys"] else ""
	_go(2)


func _process(delta: float) -> void:
	if not is_visible_in_tree():
		return
	_check_at -= delta
	if _check_at > 0.0:
		return
	_check_at = 0.25
	refresh()


## Build the page again when what it shows changed (else its buttons and fields stay as they are).
func refresh() -> void:
	if step == 4 and host and host.memory_now() == "some":
		host.close_setup()  # (the game is known already: no first run to offer)
		return
	_follow_provider()  # (an account frame or autopilot.env may have changed it)
	var k := _state_key()
	if k == _key:
		return
	_key = k
	_build()


func _state_key() -> Array:
	var s = host.service if host else null
	var si: Dictionary = host.sign_in if host else {}
	return [step, Prefs.aura_installed(), si.duplicate(), host.sign_in_progress() if host else "",
		s.state if s else "", s.message if s else "", s.url if s else "", s.has_installer() if s else false, _keys_shown,
		host.bridge.is_connected_to_brain() if host and host.bridge else false, _frame_says("coding_agent"),
		_frame_says("llm_provider"), host.memory_now() if host else "unknown", host.active != null if host else false,
		host.bridge.link.problem if host and host.bridge else "", creating_account]
# --- pieces ------------------------------------------------------------------------------------------

static func _label(text: String, variation := "AutopilotBody", wrap := true) -> Label:
	var l := Label.new()
	l.theme_type_variation = variation
	l.text = text
	if wrap:
		l.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	return l


static func _caps(text: String, color := HT.MUTED) -> Label:
	var l := Label.new()
	l.theme_type_variation = "AutopilotCaps"
	l.text = text.to_upper()
	l.add_theme_color_override("font_color", color)
	l.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	return l


## A button: sentence case, 28 tall (small: 24); wide: 20 px at its sides (the boards' main action).
static func _button(text: String, variation: String, action: Callable, small := false, wide := false) -> Button:
	var b := HT.button(text, variation, action, small)
	if wide:
		b.custom_minimum_size.x = HT.font("display").get_string_size(text, HORIZONTAL_ALIGNMENT_LEFT, -1, HT.px(12)).x + HT.px(40)
	return b


static func _margin(child: Control, l: float, t: float, r: float, b: float) -> MarginContainer:
	var m := MarginContainer.new()
	m.add_theme_constant_override("margin_left", HT.px(l))
	m.add_theme_constant_override("margin_top", HT.px(t))
	m.add_theme_constant_override("margin_right", HT.px(r))
	m.add_theme_constant_override("margin_bottom", HT.px(b))
	m.add_child(child)
	return m


static func _spacer() -> Control:
	var c := Control.new()
	c.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	c.mouse_filter = Control.MOUSE_FILTER_IGNORE
	return c


static func _row(sep := 6.0) -> HFlowContainer:
	# (a flow: the buttons go to a second line in a narrow dock)
	var r := HFlowContainer.new()
	r.add_theme_constant_override("h_separation", HT.px(sep))
	r.add_theme_constant_override("v_separation", HT.px(6))
	return r


## A plate (radius 8, a 1 px border: RULE or the state's, on PLATE or bg) in the page; its column (10 apart) returned.
func _card(edge := HT.RULE, bg := HT.PLATE) -> VBoxContainer:
	var card := PanelContainer.new()
	card.add_theme_stylebox_override("panel", HT.plate_box(Vector4(14, 12, 14, 12), edge, bg))
	_v.add_child(card)
	var cv := VBoxContainer.new()
	cv.add_theme_constant_override("separation", HT.px(10))
	card.add_child(cv)
	return cv


## A plate whose content is one row (a spinner and a line, say): its row returned.
func _card_row(edge := HT.RULE, bg := HT.PLATE) -> HBoxContainer:
	var card := PanelContainer.new()
	card.add_theme_stylebox_override("panel", HT.plate_box(Vector4(14, 12, 14, 12), edge, bg))
	_v.add_child(card)
	var h := HBoxContainer.new()
	h.add_theme_constant_override("separation", HT.px(10))
	card.add_child(h)
	return h


## A plate's head: its micro-label, and a tag (text, kind) when given.
func _head(parent: Control, title: String, tag := "", kind := "ok") -> void:
	var h := HFlowContainer.new()
	h.add_theme_constant_override("h_separation", HT.px(8))
	h.add_child(_caps(title))
	if tag != "":
		h.add_child(HT.tag(tag, kind))
	parent.add_child(h)


## A step's heading: the logo (20 px), then the title (18 px, 600).
func _title(text: String) -> void:
	var h := HBoxContainer.new()
	h.add_theme_constant_override("separation", HT.px(10))
	var ic := TextureRect.new()
	ic.texture = HT.icon(20)
	ic.stretch_mode = TextureRect.STRETCH_KEEP_CENTERED
	ic.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	h.add_child(ic)
	var l := _label(text, "AutopilotHeading")
	l.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	h.add_child(l)
	_v.add_child(h)


func _dim_text(text: String, color := HT.TEXT3) -> Label:
	var l := _label(text, "AutopilotDim")
	if color != HT.TEXT3:
		l.add_theme_color_override("font_color", color)
	return l


## A line with a spinner before it (something going on).
func _spinning(parent: Control, text: String, variation := "AutopilotStrong") -> void:
	var h := HBoxContainer.new()
	h.add_theme_constant_override("separation", HT.px(10))
	h.add_child(W.Spinner.new())
	var l := _label(text, variation)
	l.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	h.add_child(l)
	parent.add_child(h)


## A status line: a 7 px square dot, then the line (500).
func _dot_line(parent: Control, text: String, color := HT.AMBER) -> void:
	var h := HBoxContainer.new()
	h.add_theme_constant_override("separation", HT.px(10))
	h.add_child(HT.dot(color, 7))
	var l := _label(text, "AutopilotStrong")
	l.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	h.add_child(l)
	parent.add_child(h)


## A step's foot: under a rule, its buttons (left: before; right: after the room).
func _nav(left: Array, right: Array, rule := true) -> void:
	var v := VBoxContainer.new()
	v.add_theme_constant_override("separation", HT.px(12))
	if rule:
		v.add_child(HT.rule())
	var h := HBoxContainer.new()
	h.add_theme_constant_override("separation", HT.px(6))
	for b in left:
		h.add_child(b)
	h.add_child(_spacer())
	for b in right:
		h.add_child(b)
	v.add_child(h)
	_v.add_child(v)


## A mono line in a well (an address, a command), cut short, with a small Copy button when copy.
func _well(text: String, copy := "") -> PanelContainer:
	var w := PanelContainer.new()
	w.add_theme_stylebox_override("panel", HT.box(HT.WELL, 0, HT.RULE, 1, Vector4(10, 4, 4, 4)))
	var h := HBoxContainer.new()
	h.add_theme_constant_override("separation", HT.px(8))
	w.add_child(h)
	var l := _label(text, "AutopilotMono", false)
	l.clip_text = true
	l.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	l.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	l.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	l.custom_minimum_size.y = HT.px(24)
	l.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
	l.tooltip_text = text
	l.mouse_filter = Control.MOUSE_FILTER_PASS
	h.add_child(l)
	if copy != "":
		h.add_child(_button(copy, "AutopilotQuiet", func(): DisplayServer.clipboard_set(text), true))
	return w


# --- the stepper -------------------------------------------------------------------------------------

## What step 1's chosen account is called in the stepper ("" while not chosen).
func account_word() -> String:
	if access == "aura" and use_aura:
		return "AURA APP"
	if access == "aura":
		return "AURA"
	if access == "own_keys":
		return "BYOK"
	return ""


## The stepper's labels as shown now (for the tests too): a 4th, FIRST RUN, unless the agent service said it remembers
## something about this game (then there is no first run to offer).
func stepper_labels() -> Array:
	var word := account_word()
	var labels := ["1 ACCOUNT" + (" · " + word if word != "" and step > 1 else ""), "2 AGENT SERVICE", "3 PRIVACY SETTINGS"]
	if step == 4 or not host or host.memory_now() != "some":
		labels.append("4 FIRST RUN")
	return labels


## The stepper: a 2 px bar per step (the accent: this one; STEP_DONE: done; RULE: to come), its micro-label under it.
func _stepper() -> void:
	var h := HBoxContainer.new()
	h.add_theme_constant_override("separation", HT.px(8))
	var labels := stepper_labels()
	for i in labels.size():
		var n := i + 1
		var lit := n <= step
		var now := n == step
		var col := VBoxContainer.new()
		col.add_theme_constant_override("separation", HT.px(6))
		col.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		col.size_flags_stretch_ratio = 1.0
		var bar := ColorRect.new()
		bar.color = HT.ACCENT if now else (HT.STEP_DONE if lit else HT.RULE)
		bar.custom_minimum_size = Vector2(0, maxi(1, HT.px(2)))
		col.add_child(bar)
		var l := _caps(labels[i], HT.ACCENT if now else (HT.TEXT3 if lit else HT.MUTED))
		l.text = labels[i]
		l.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
		col.add_child(l)
		h.add_child(col)
	_v.add_child(h)


# --- building ----------------------------------------------------------------------------------------

func _build() -> void:
	# (the keys typed so far stay in the fields built again)
	var typed := [_typesafe.text if is_instance_valid(_typesafe) else "", _llm_key.text if is_instance_valid(_llm_key) else ""]
	for c in get_children():
		remove_child(c)
		c.queue_free()
	_typesafe = null
	_llm_key = null
	_agent = null
	_provider = null
	_command = null
	_v = VBoxContainer.new()
	_v.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_v.add_theme_constant_override("separation", HT.px(18))
	var col := W.Column.new(MAX_WIDTH, Vector4(20, 28, 20, 20))
	col.add_child(_v)
	add_child(col)
	_stepper()
	match step:
		1: _build_account()
		2: _build_service()
		3: _build_privacy()
		_: _build_first_run()
	if _typesafe and _llm_key:
		_typesafe.text = typed[0]
		_llm_key.text = typed[1]


func _go(n: int) -> void:
	step = n
	if n == 2:
		_visited2 = true
		if host:
			host.prepare_service()
	_key = []
	refresh()


# --- step 1: account ---------------------------------------------------------------------------------

func _signing() -> String:
	return str(host.sign_in.get("state", "")) if host else ""


func _build_account() -> void:
	_title("Welcome to Autopilot")
	_aura_card()
	_keys_card()


func _aura_card() -> void:
	var si: Dictionary = host.sign_in if host else {}
	match _signing():
		"preparing", "waiting":
			var a := _card(HT.INFO_LINE)
			if _signing() == "preparing":
				_head(a, "Sign in with Aura", "Getting ready", "info")
				_checklist(a)
			else:
				_head(a, "Sign in with Aura", "In your browser", "info")
				_spinning(a, "Finish signing in in your browser…", "AutopilotTitle")
				var url := str(si.get("url", ""))
				a.add_child(_well(url, "Copy link"))
				var r := _row()
				r.add_child(_button("Open again", "Button", func(): OS.shell_open(url)))
				r.add_child(_button("Cancel", "AutopilotQuiet", func(): host.cancel_sign_in()))
				a.add_child(r)
				return
			var c := _row()
			c.add_child(_button("Cancel", "AutopilotQuiet", func(): host.cancel_sign_in()))
			a.add_child(c)
		"no_installer":
			var a := _card(HT.AMBER_LINE)
			_head(a, "Sign in with Aura", "Agent service needed", "warn")
			_no_installer(a)
			var c := _row()
			c.add_child(_button("Try again", "AutopilotPrimary", func(): host.begin_sign_in()))
			c.add_child(_button("Cancel", "AutopilotQuiet", func(): host.cancel_sign_in()))
			a.add_child(c)
		"signed_in":
			var a := _card(HT.SUCCESS_LINE)
			_head(a, "Sign in with Aura", "Signed in")
			email = str(si.get("email", ""))
			var who := HFlowContainer.new()
			who.add_theme_constant_override("h_separation", HT.px(8))
			var as_l := _label("Signed in as", "AutopilotSmall", false)
			as_l.add_theme_color_override("font_color", HT.MUTED)
			as_l.size_flags_vertical = Control.SIZE_SHRINK_CENTER
			who.add_child(as_l)
			var e := _label(email if email != "" else "your Aura account", "AutopilotTitle")
			e.autowrap_mode = TextServer.AUTOWRAP_ARBITRARY  # (a long address wraps in a narrow dock)
			e.size_flags_horizontal = Control.SIZE_EXPAND_FILL
			who.add_child(e)
			a.add_child(who)
			var r := _row()
			r.add_child(_button("Continue", "AutopilotPrimary", func():
				access = "aura"
				use_aura = false
				_go(2)))
			r.add_child(_button("Use another account", "AutopilotQuiet", func(): host.another_account()))
			a.add_child(r)
		"error":
			var a := _card(HT.RED_LINE)
			_head(a, "Sign in with Aura", "Not signed in", "bad")
			a.add_child(_dim_text(str(si.get("message", "")), HT.RED))
			var r := _row()
			r.add_child(_button("Try again", "AutopilotPrimary", func(): host.begin_sign_in()))
			if str(si.get("log", "")) != "":
				var log := str(si.log)
				r.add_child(_button("View logs", "Button", func(): host.open_log(log)))
			a.add_child(r)
		_ when creating_account:
			# (Create Account opened Aura's account creation page: once the account is made, Sign in as below)
			var a := _card(HT.INFO_LINE)
			_head(a, "Sign in with Aura", "In your browser", "info")
			a.add_child(_label("Finish creating your Aura account", "AutopilotTitle"))
			a.add_child(_dim_text("Once you have created your account, sign in and start using Autopilot"))
			var r := _row()
			r.add_child(_button("Sign in", "AutopilotPrimary", _sign_in))
			a.add_child(r)
		_:
			# (both in the browser, the Aura app installed or not: Aura's account creation page first, its sign-in
			# second; the Aura app's own choice, _use_aura_app, isn't offered)
			var a := _card()
			_head(a, "Sign in with Aura")
			a.add_child(_dim_text(AURA_BILLED))
			var r := _row()
			r.add_child(_button("Create Account", "AutopilotPrimary", _create_account))
			r.add_child(_button("Sign in", "AutopilotQuiet", _sign_in))
			a.add_child(r)


## Aura's account creation page in the browser, and the Aura card's next page: Sign in once the account is made.
func _create_account() -> void:
	open_url.call(Prefs.sign_up_url(host.service.installer_folder() if host and host.service else ""))
	creating_account = true
	refresh()


func _sign_in() -> void:
	creating_account = false
	if host:
		host.begin_sign_in()


## Use the Aura app: it runs the agent service, so no step 2.
func _use_aura_app() -> void:
	access = "aura"
	use_aura = true
	_go(3)


## The sign-in getting ready: setting up the agent service (when it is installed now), starting it, then the browser.
func _checklist(parent: Control) -> void:
	var s = host.service if host else null
	var st: String = s.state if s else ""
	var items := []  # [text, state: "done" | "now" | "next"]
	var installing: bool = st == "installing" or (host != null and host._sign_in_installed)
	var connected: bool = host != null and host.bridge != null and host.bridge.is_connected_to_brain()
	if installing:
		items.append(["Setting up the agent service…", "now" if st == "installing" else "done"])
	var started := connected or st == "ready"
	items.append(["Starting the agent service…", "done" if started else ("next" if st == "installing" else "now")])
	items.append(["Finish signing in in your browser…", "now" if started else "next"])
	for it in items:
		var row := HBoxContainer.new()
		row.add_theme_constant_override("separation", HT.px(10))
		var mark: Control
		if it[1] == "now":
			mark = W.Spinner.new()
		else:
			mark = _label({"done": "✓", "next": "○"}[it[1]], "AutopilotMono", false)
			mark.add_theme_color_override("font_color", HT.SUCCESS if it[1] == "done" else HT.MUTED)
			mark.size_flags_vertical = Control.SIZE_SHRINK_CENTER
		row.add_child(mark)
		var t := _label(it[0], "AutopilotStrong" if it[1] == "now" else "AutopilotBody")
		t.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		if it[1] != "now":
			t.add_theme_color_override("font_color", HT.TEXT3 if it[1] == "done" else HT.MUTED)
		row.add_child(t)
		if it[1] == "done":
			var d := _label("done", "AutopilotMonoDim", false)
			d.size_flags_vertical = Control.SIZE_SHRINK_CENTER
			row.add_child(d)
		parent.add_child(row)


func _keys_card() -> void:
	if _signing() in ["preparing", "waiting", "no_installer"]:
		return  # (while the sign-in gets ready, waits or needs the agent service: no keys card; Cancel brings it back)
	if not _keys_shown:
		# (the keys plate folded, dimmed once signed in; Show opens it)
		var card := PanelContainer.new()
		card.add_theme_stylebox_override("panel", HT.plate_box())
		if _signing() == "signed_in":
			card.modulate = Color(1, 1, 1, 0.55)
		_v.add_child(card)
		var h := HBoxContainer.new()
		h.add_theme_constant_override("separation", HT.px(8))
		card.add_child(h)
		var name_l := _caps("Bring your own keys (BYOK)")
		name_l.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		name_l.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART  # (a narrow dock: the title wraps beside Show)
		h.add_child(name_l)
		var open_keys := func() -> void:
			_keys_shown = true
			_key = []
			refresh()
		h.add_child(_button("Show", "AutopilotQuiet", open_keys, true))
		return
	var k := _card()
	var head := HBoxContainer.new()
	head.add_theme_constant_override("separation", HT.px(8))
	var title := _caps("Bring your own keys (BYOK)")
	title.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	title.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	head.add_child(title)
	head.add_child(_button("Hide", "AutopilotQuiet", func():
		_keys_shown = false
		_key = []
		refresh(), true))
	k.add_child(head)
	_typesafe = _key_field(k, "Decision provider API key", "Optional")
	_agent = OptionButton.new()
	for a in Prefs.AGENTS:
		_agent.add_item(Prefs.AGENT_NAMES[a])
	_agent.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_agent.custom_minimum_size.y = HT.px(28)
	_agent.clip_text = true
	_agent.fit_to_longest_item = false
	_agent.select(Prefs.AGENTS.find(agent_now()))
	_field(k, "Coding Agent", _agent)
	_provider = OptionButton.new()
	for p in Prefs.LLM_PROVIDERS:
		_provider.add_item(Prefs.LLM_NAMES[p])
	_provider.tooltip_text = "LLM provider"
	_provider.custom_minimum_size = Vector2(HT.px(140), HT.px(28))
	_provider.clip_text = true  # (its width fixed: the key's field takes the rest)
	_provider.fit_to_longest_item = false
	_show_provider()
	_llm_key = _key_field(k, "LLM provider API key", "Optional", _provider)  # (the dropdown names the provider)
	var row: Control = _llm_key.get_parent()
	var picker := _provider
	row.resized.connect(func() -> void:
		# (140 px, but never wider than the key's field: in a dock narrower than 360 px, half the row)
		var half := floorf((row.size.x - HT.px(6)) / 2.0)
		picker.custom_minimum_size.x = minf(HT.px(140), half) if half > 0.0 else HT.px(140))
	_agent.item_selected.connect(func(i: int) -> void:
		coding_agent = Prefs.AGENTS[i]
		_show_provider()
		_follow_provider())
	_provider.item_selected.connect(func(i: int) -> void:
		llm_provider = Prefs.LLM_PROVIDERS[i]
		_follow_provider())
	var r := _row()
	r.add_child(_button("Use my keys", "Button", _use_own_keys))
	k.add_child(r)
	_keys_note = _label("", "AutopilotMonoDim")
	_keys_note.add_theme_color_override("font_color", HT.RED)
	_keys_note.visible = false
	k.add_child(_keys_note)


## A field: its micro-label, then the control (the plate's width), 5 apart.
func _field(parent: Control, caption: String, control: Control) -> void:
	var col := VBoxContainer.new()
	col.add_theme_constant_override("separation", HT.px(5))
	col.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	col.add_child(_caps(caption))
	col.add_child(control)
	parent.add_child(col)


## A key's field: its micro-label, then the input (mono, hidden; after picker on the same row, when given).
func _key_field(parent: Control, caption: String, hint: String, picker: Control = null) -> LineEdit:
	var e := LineEdit.new()
	e.theme_type_variation = "AutopilotMonoField"
	e.secret = true
	e.placeholder_text = hint
	e.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	e.custom_minimum_size.y = HT.px(28)
	if picker:
		var row := HBoxContainer.new()
		row.add_theme_constant_override("separation", HT.px(6))
		row.add_child(picker)
		row.add_child(e)
		_field(parent, caption, row)
	else:
		_field(parent, caption, e)
	return e



## What the agent service's last account frame says for name ("coding_agent", "llm_provider"; "" when none came or it
## has none).
func _frame_says(name: String) -> String:
	var frame: Dictionary = host.bridge.account if host and host.bridge else {}
	return str(frame[name]).strip_edges() if typeof(frame.get(name)) == TYPE_STRING else ""


## The Coding Agent the dropdown shows first: the agent service's (its account frame), else autopilot.env's, else OpenCode.
func preselected_agent() -> String:
	var said := _frame_says("coding_agent")
	return Prefs.coding_agent(said) if said != "" else Prefs.saved_coding_agent()


## The LLM provider OpenCode shows first: the agent service's when its OpenCode uses one (its account frame; one without
## coding_agent is an older agent service's, OpenCode's), else autopilot.env's, else Anthropic.
func preselected_provider() -> String:
	var said := _frame_says("llm_provider")
	if said != "" and Prefs.coding_agent(_frame_says("coding_agent")) == "opencode":
		return Prefs.llm_provider(said)
	return Prefs.saved_llm_provider()


## The Coding Agent picked now (one of Prefs.AGENTS).
func agent_now() -> String:
	return coding_agent if coding_agent != "" else preselected_agent()


## The LLM provider shown now (one of Prefs.LLM_PROVIDERS): the one Claude Code or Codex takes, or OpenCode's pick.
func provider_now() -> String:
	return Prefs.agent_provider(agent_now(), llm_provider if llm_provider != "" else preselected_provider())


## The LLM key's field emptied when the LLM provider changes (a key typed for one LLM provider is never saved as
## another's).
func _follow_provider() -> void:
	var now := provider_now()
	if _llm_key_for != "" and now != _llm_key_for and is_instance_valid(_llm_key):
		_llm_key.text = ""
	_llm_key_for = now


## The provider dropdown as the Coding Agent wants it: its provider shown, locked but for OpenCode.
func _show_provider() -> void:
	if not is_instance_valid(_provider):
		return
	_provider.select(Prefs.LLM_PROVIDERS.find(provider_now()))
	_provider.disabled = agent_now() != "opencode"


func _use_own_keys() -> void:
	var err := Prefs.save_byok(_typesafe.text.strip_edges(), agent_now(), provider_now(), _llm_key.text.strip_edges())
	if err != OK:
		_keys_note.text = "Couldn’t save the keys to %s (error %d)" % [Prefs.file(Prefs.ENV_FILE), err]
		_keys_note.visible = true
		return
	_typesafe.text = ""
	_llm_key.text = ""
	access = "own_keys"
	use_aura = false
	_go(2)


## No installer in the addon (a repository checkout): the Agent service command setting (kept with the dock's settings,
## Advanced on the connect page), or autopilot serve by hand.
func _no_installer(parent: Control) -> void:
	_dot_line(parent, "No agent service found")
	_command = LineEdit.new()
	_command.theme_type_variation = "AutopilotMonoField"
	_command.placeholder_text = "e.g. C:\\autopilot\\.venv\\Scripts\\python.exe -m autopilot"
	_command.text = host.service_command() if host else ""
	_command.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_command.custom_minimum_size.y = HT.px(28)
	var h := HBoxContainer.new()
	h.add_theme_constant_override("separation", HT.px(6))
	h.add_child(_command)
	var field := _command
	h.add_child(_button("Start", "Button", func():
		host.set_service_command(field.text.strip_edges())
		host.retry_service()))
	_field(parent, "Agent service command", h)
	parent.add_child(_serve_line())


## "Or run autopilot serve" (the command in the accent), with a small Copy button.
func _serve_line() -> Control:
	var h := HFlowContainer.new()
	h.add_theme_constant_override("h_separation", HT.px(8))
	var l := _dim_text("Or run")
	l.autowrap_mode = TextServer.AUTOWRAP_OFF
	l.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	h.add_child(l)
	var c := _label("autopilot serve", "AutopilotMono", false)
	c.add_theme_font_size_override("font_size", HT.px(12))
	c.add_theme_color_override("font_color", HT.ACCENT)
	c.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	h.add_child(c)
	h.add_child(_button("Copy", "AutopilotQuiet", func(): DisplayServer.clipboard_set("autopilot serve"), true))
	return h


# --- step 2: agent service ---------------------------------------------------------------------------

func _build_service() -> void:
	var s = host.service if host else null
	var st: String = s.state if s else "idle"
	_title("Set up the agent service")
	var back := _button("Back", "AutopilotQuiet", func(): _go(1))
	var later := func() -> Button: return _button("I’ll do it later", "AutopilotQuiet", func(): _go(3))
	match st:
		"not_installed":
			var c := _card()
			if s.has_installer():
				_dot_line(c, "Not installed on this computer")
				c.add_child(_dim_text("One-time installation takes approximately 1 minute."))
				_nav([back], [later.call(), _button("Install", "AutopilotPrimary", func(): host.install_service(), false, true)])
			else:
				_no_installer(c)
				_nav([back], [later.call()])
		"installing", "starting":
			var c := _card_row(HT.INFO_LINE)
			c.add_child(W.Spinner.new())
			c.add_child(_label("Installing the agent service…" if st == "installing" else "Starting the agent service…",
				"AutopilotStrong"))
			var wait := _button("Continue", "AutopilotPrimary", func(): pass, false, true)
			wait.disabled = true
			_nav([back], [wait])
		"ready":
			var c := _card_row(HT.SUCCESS_LINE, HT.SUCCESS_FILL)
			c.add_theme_constant_override("separation", HT.px(12))
			var ok := TextureRect.new()
			ok.texture = HT.glyph("check", 18, HT.SUCCESS)
			ok.stretch_mode = TextureRect.STRETCH_KEEP_CENTERED
			ok.size_flags_vertical = Control.SIZE_SHRINK_CENTER
			c.add_child(ok)
			var tv := VBoxContainer.new()
			tv.add_theme_constant_override("separation", 0)
			tv.size_flags_horizontal = Control.SIZE_EXPAND_FILL
			var l := _label("Agent service ready", "AutopilotStrong")
			l.add_theme_color_override("font_color", HT.SUCCESS_FG)
			tv.add_child(l)
			tv.add_child(_label("Starts with your editor", "AutopilotSmall"))
			c.add_child(tv)
			var u := _label(s.url, "AutopilotMonoDim", false)
			u.add_theme_font_size_override("font_size", HT.px(10.5))
			u.size_flags_vertical = Control.SIZE_SHRINK_CENTER
			u.clip_text = true
			u.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
			u.size_flags_horizontal = Control.SIZE_SHRINK_END | Control.SIZE_EXPAND
			c.add_child(u)
			_nav([back], [_button("Continue", "AutopilotPrimary", func(): _go(3), false, true)])
		"install_failed", "stopped", "failed":
			var c := _card(HT.RED_LINE)
			c.add_child(_dim_text("The agent service didn’t start.", HT.RED))
			if str(s.message) != "":
				var why := _label(str(s.message), "AutopilotMonoDim")
				why.autowrap_mode = TextServer.AUTOWRAP_ARBITRARY
				c.add_child(why)
			var r := _row()
			var log: String = s.log_path
			if log != "":
				r.add_child(_button("View logs", "Button", func(): host.open_log(log), true))
			r.add_child(_button("Try again", "AutopilotPrimary", func(): host.retry_service(), true))
			c.add_child(r)
			_nav([back], [later.call()])
		_:
			# (not started by the dock: a headless editor; the user starts it, Continue once it is connected)
			var c := _card(HT.AMBER_LINE)
			_dot_line(c, "Waiting for the agent service: start it with autopilot serve.")
			var go := _button("Continue", "AutopilotPrimary", func(): _go(3), false, true)
			go.disabled = not (host != null and host.bridge != null and host.bridge.is_connected_to_brain())
			_nav([back], [go])


# --- step 3: privacy settings ------------------------------------------------------------------------

## The recap's lines: [account, agent service].
func recap() -> Array:
	var who := "Not chosen yet"
	if access == "aura" and use_aura:
		who = "Aura app"
	elif access == "aura":
		who = "Aura · " + email if email != "" else "Aura"
	elif access == "own_keys":
		who = "BYOK"
	var s = host.service if host else null
	var service := "Not set up yet"
	if use_aura:
		service = "The Aura app runs it"
	elif s and s.state == "ready":
		service = "Ready"
	elif s and s.state == "installing":
		service = "Installing…"
	elif s and s.busy():
		service = "Starting…"
	elif s and s.state == "not_installed":
		service = "Not installed yet"
	return [who, service]


func _build_privacy() -> void:
	_title("Privacy settings")
	var c := _card()
	var box := CheckBox.new()
	box.theme_type_variation = "AutopilotSwitch"
	box.text = "Share my runs to improve Autopilot"
	box.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	box.button_pressed = share
	box.toggled.connect(func(on: bool) -> void: share = on)
	c.add_child(box)
	c.add_child(_dim_text(DISCLOSURE))
	# the recap: a row per item between rules
	var r := recap()
	var kv := VBoxContainer.new()
	kv.add_theme_constant_override("separation", 0)
	kv.add_child(HT.rule())
	var first := true
	for pair in [["Account", r[0]], ["Agent service", r[1]]]:
		if not first:
			kv.add_child(HT.rule())
		first = false
		var h := HBoxContainer.new()
		h.add_theme_constant_override("separation", HT.px(10))
		var k := _caps(pair[0])
		k.custom_minimum_size.x = HT.px(130)
		k.size_flags_vertical = Control.SIZE_SHRINK_BEGIN
		h.add_child(_margin(k, 0, 3, 0, 0))
		var v := _label(pair[1], "AutopilotBody")
		v.add_theme_color_override("font_color", HT.TEXT)
		v.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		h.add_child(v)
		kv.add_child(_margin(h, 0, 8, 0, 8))
	kv.add_child(HT.rule())
	_v.add_child(kv)
	# (Next when a first run may be offered: step 4; Finish when the game is known already)
	var known: bool = host != null and host.memory_now() == "some"
	_nav([_button("Back", "AutopilotQuiet", func(): _go(2 if _visited2 else 1))],
		[_button("Finish" if known else "Next", "AutopilotPrimary", func(): finished.emit(access, share, use_aura), false, true)], false)


# --- step 4: first run -------------------------------------------------------------------------------

## Step 4, after the dock saved the setup and connected (_on_setup_finished).
func first_run() -> void:
	step = 4
	_key = []
	refresh()


const FIRST_RUN_TEXT := ("You’ve never used Autopilot with this game before. To help Autopilot understand your game, use this "
	+ "first exploration run and watch it improve. Autopilot still works if you skip this step.")


func _build_first_run() -> void:
	_title("Let Autopilot learn your game")
	var memory: String = host.memory_now() if host else "unknown"
	if memory != "empty":
		# (waiting for the connection and its memory_state; with something remembered, refresh() closes the setup)
		var wait := VBoxContainer.new()
		wait.add_theme_constant_override("separation", HT.px(12))
		var keep := _v
		_v = wait
		var c := _card()
		_v = keep
		var connected: bool = host != null and host.bridge != null and host.bridge.is_connected_to_brain()
		_spinning(c, "Checking what Autopilot remembers about this game…" if connected else "Connecting to the agent service…")
		var problem: String = host.bridge.link.problem if host and host.bridge and not connected else ""
		if problem != "":
			c.add_child(_dim_text(Model.scrub(problem), HT.AMBER))
		var r := _row()
		r.add_child(_button("Skip", "AutopilotQuiet", func(): host.close_setup()))
		wait.add_child(r)
		_v.add_child(wait)
		return
	var c := _card()
	_head(c, "Explore your game", "Recommended")
	c.add_child(_dim_text(FIRST_RUN_TEXT))
	var m := HBoxContainer.new()
	m.add_theme_constant_override("separation", HT.px(10))
	m.add_child(_caps("Minutes"))
	var minutes := SpinBox.new()
	minutes.min_value = 1
	minutes.max_value = 600
	minutes.rounded = true
	minutes.alignment = HORIZONTAL_ALIGNMENT_RIGHT
	minutes.custom_minimum_size = Vector2(HT.px(56), HT.px(28))
	minutes.get_line_edit().theme_type_variation = "AutopilotMonoField"
	minutes.value = 10  # (an exploration's minutes of play, its own: the composer's Time limit is every run's)
	m.add_child(minutes)
	c.add_child(m)
	var foot := VBoxContainer.new()
	foot.add_theme_constant_override("separation", HT.px(10))
	foot.add_child(HT.rule())
	var r := _row()
	var go := _button("Explore and finish", "AutopilotPrimary", func(): host.explore_from_setup(int(minutes.value)), false, true)
	var ready: bool = host != null and host.bridge.is_connected_to_brain() and host.active == null
	go.disabled = not ready
	if not ready:
		go.tooltip_text = "A run is going: wait for its result, or stop it" if host and host.active != null else "Not connected: connect first"
	r.add_child(go)
	r.add_child(_button("Skip and finish", "AutopilotQuiet", func(): host.close_setup()))
	foot.add_child(r)
	c.add_child(foot)
