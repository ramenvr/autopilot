## The Autopilot dock: connected to the agent service by itself (the connect card shows only while it isn't: connecting,
## starting or installing it, what went wrong, or Disconnected by the user), compose a run (one prompt box: Verify,
## Explore or a goal, as its words say), follow it live (stages, the current action, the harness attempts, the feed) and
## read its result (with the run's recording, played as a flipbook); the runs in a run history (a rail when the dock is
## wide, a dropdown when narrow; the newest 50 kept between editor sessions, ui/history.gd), the gear beside New run (the Settings page), the agent's memory
## of the game (Reset memory) and the Policy log.
## The URL, token and Agent service command are kept per user and project (EditorSettings project metadata, in .godot/:
## never committed).
## Also the Settings page (ui/settings_page.gd: the account and Rerun setup, the tokens the agent's models used, per run
## and for the project, and the Data sharing choice) and, until Autopilot/preferences.json says the user went through it,
## the first-run setup (ui/setup_page.gd) in place of everything else, ending with a first Explore run offered when the
## game's memory is empty.
## The agent service on this computer is started by the dock (lib/service.gd) when it connects to the default address
## (never in a headless editor), and installed from the installer the addon carries; the Aura sign-in is asked of it
## (begin_sign_in: the agent service opens the browser). Set up before but with the agent service gone (not installed,
## none running), the setup opens at its step 2 at start.
## The Settings page's Uninstall removes Autopilot from this computer (lib/service.gd uninstall()):
## until Set up again (or the editor's restart) the removal's page is shown in place of everything else, and nothing
## starts, installs or connects to the agent service.
@tool
extends PanelContainer

const HT = preload("ui/autopilot_theme.gd")
const Model = preload("ui/model.gd")
const W = preload("ui/widgets.gd")
const PolicyLog = preload("ui/policy_log.gd")
const Frame = preload("lib/frame.gd")
const Link = preload("lib/link.gd")
const Prefs = preload("lib/prefs.gd")
const Usage = preload("ui/usage.gd")
const History = preload("ui/history.gd")
const SettingsPage = preload("ui/settings_page.gd")
const SetupPage = preload("ui/setup_page.gd")
const Service = preload("lib/service.gd")
const USAGE_SAVE_EVERY := 5.0  # seconds between writes of the usage file while a run goes on
const HISTORY_SAVE_EVERY := 5.0  # seconds between writes of a run's history file while it changes

const WIDE_AT := 700.0
const RAIL_WIDTH := 280.0
const RUN_GOING := "A run is going: wait for its result, or stop it"
## The Memory row's tooltips
const MEMORY_TIP := ("What the agent remembers about this game, kept between runs: its earlier runs, its notes on how the "
	+ "game works, what it built to play it. It is what makes later runs better: they are faster and play better with it. "
	+ "“Empty”: the next run starts as on a new game.")
const RESET_TIP := "Reset memory: the agent forgets what it has learned about this game"
const RESET_TEXT := "The agent forgets what it has learned about this game."
const RESET_BUSY := "A run is going: reset memory once it is done"  # (the button's tooltip while it is disabled)
## What is left to remove by hand once Autopilot is removed from this computer: the addon, in each project.
const REMOVED_NEXT := ("To finish, disable Autopilot in Project > Project Settings > Plugins and delete addons/autopilot/ "
	+ "from each project that has it.")
const COMPOSER_WIDTH := 620.0  # the composer's column: at most this wide, centred
const CONNECT_WIDTH := 540.0  # the connect page's and the removal page's
const FEED_TAIL := 10  # (the Live page's feed: its latest lines; the Feed tab shows them all)
const STATUS_FOR := 8.0  # seconds a status line message stays

var bridge  # editor_bridge.gd
var badge: Button  # the toolbar badge (the plugin puts it in the editor's toolbar)

var runs := []  # Model.Run, oldest first (from earlier editor sessions too: ui/history.gd)
## Where the run history is kept for this project ("" outside the editor: nothing kept): .godot/editor/autopilot_runs/.
var history_dir := ""
var _history_dirty := []  # the runs changed since they were last saved
var _history_saved_at := 0.0
var active = null  # the run going on
var shown = null  # the run shown
var composing := false
var tab := "main"  # main (live or result) | feed | policy
var wide := false

var _conn := ""
var _tick := 0.0

# layout
var _body: HBoxContainer
var _rail: PanelContainer
var _rail_list: VBoxContainer
var _rail_footer_slot: VBoxContainer
var _main: VBoxContainer
var _top: HBoxContainer
var _hist_btn: Button
var _hist_tag_slot: HBoxContainer
var _hist_meta: Label
var _hist_menu: PopupPanel  # the narrow dock's history list (its rows, then the Memory row)
var _hist_list: VBoxContainer
var _hist_footer_slot: VBoxContainer
var _new_btn: Button
var _gears := []  # the Settings buttons (a gear): the rail's head, the narrow top bar's
var _strip: PanelContainer  # "not connected" strip above a run
var _strip_text: Label
var _strip_btn: Button
var _pages: Control
var _footer: PanelContainer  # the Memory row (the rail's foot; the narrow history list's)
var _footer_slot: VBoxContainer
var _save_label: Label
var _reset_btn: Button  # Reset memory… (the footer, after what the agent remembers)
var _link_label: Label  # the composer's foot: the link's state, its address and Disconnect
var _link_url: Label
var _link_btn: Button
var _status: Label  # the status line: the latest message (the main column's last row)
var _status_bar: PanelContainer
var _status_until := 0.0  # (when the message shown goes, in seconds of Time.get_ticks_msec())
var _mem_bar: VBoxContainer  # the Memory row and, under it, how a reset went
var _mem_ask: PanelContainer  # Reset memory's confirmation, in the row's place
var _reset_line: HBoxContainer
var _reset_spin: Control
var _reset_text: Label
var _reset := ""  # Reset memory: "" | "ask" (confirming) | "working" | "done" | "failed"
var _reset_why := ""  # (why it failed, as the agent service said)
var _reset_until := 0.0  # (when how the reset went stops being shown: STATUS_FOR seconds after)

# connect page (shown while not connected)
var _connect_page: ScrollContainer
var _cp_where_own: Control  # "Start the agent service with autopilot serve" (or, with the Aura app, _cp_where_aura)
var _cp_where_aura: Label
var _cp_box: PanelContainer  # the state's line (and a detail under it), in the state's tone
var _cp_spin: Control  # (the info tone's spinner)
var _cp_back: Button  # Back to the last run (Disconnected, a run shown before)
var _last_run = null  # the run shown before the connect page was asked for
var _cp_title: Label
var _cp_text: Label
var _cp_buttons: HFlowContainer
var _cp_key := []  # the buttons shown now: [[label, variation, action]]
var _url: LineEdit
var _token: LineEdit

# composer
var _composer: VBoxContainer
var _c_prompt: TextEdit
var _c_row: HBoxContainer  # under the words: the Time limit, then Run
var _c_time_row: HBoxContainer
var _c_time_limit: SpinBox  # the run's minutes in all (an exploration's minutes of play); kept per project as it changes
var _c_run: Button
var _c_note: Label
var _c_head: PanelContainer
var _c_credits: PanelContainer  # over the box: Aura's words when the team is out of credits, and Add credits
var _c_credits_text: Label
var _c_credits_btn: Button
var _c_credits_url := ""  # the page Add credits opens

# run page
var _run_page: VBoxContainer
var _tabbar: PanelContainer
var _tab_main: Button
var _tab_feed: Button
var _tab_policy: Button
var _tab_link: Label
var _tab_local: Label  # "● LOCAL AGENT": a live run's agent service is on this computer
var _live: VBoxContainer
var _result: ScrollContainer
var _r_banner = null  # the result's verdict (W.Banner), above the reason
var _r_player = null  # the recording's player (W.Flipbook), kept while the same run's result is shown again
var _r_player_run = null  # the run it plays
var _r_no_rec: Label = null  # in its place when the run has no recording: why ("No recording: ..."), else null
var _r_alerts: Array[Label] = []  # the result's alerts, above its verdict (Model.Run.alerts)
var _copies := []  # recordings being copied into their run's folder: {"run", "task", "out", "dst", "dir"}
var _feed_page: ScrollContainer  # the Feed tab's transcript: _feed_rows in a scroll container
var _feed_rows: VBoxContainer  # its rows: a heading per stage, a row per line (_append_feed_row)
var _feed_by_id := {}  # activity id -> its row (a call's result folds into it: _refresh_feed_row)
var _feed_group := -1  # the stage of the rows' last heading (-1: none yet)
var _feed_heading: Control = null  # that heading (its count label in its "count" meta)
var _feed_updated_id := 0  # an activity line changed by its result since the Feed tab last drew (0: none)
var _open_folds := {}  # "<activity id>:c|r" -> true while that fold is open on the Feed tab
var _activity_icons := {}  # icon name -> Texture2D, as drawn (_activity_icon)
var _replay_skipped := 0  # activity events that came again (shown already) since the last new one: said in the log
var _feed_box: Control  # (the feed's plate, with the page's padding)
var _feed_area: VBoxContainer  # the Feed tab: the plate, or "Nothing yet." (no plate) while the feed is empty
var _feed_empty: Control
var _l_elapsed_cap: Label
var _feed_steer_slot: VBoxContainer  # (where the steer box goes on the Feed tab)
var _policy: PolicyLog

# live page parts
var _l_prompt: Label
var _l_origin: Label  # "Started from Claude Code", under the request (a run the tab didn't start)
var _l_elapsed: Label
var _l_stop: Button
var _l_steer_row: Control  # steer: the user's words to the live run while it plays
var _l_steer: LineEdit
var _l_steer_btn: Button
var _l_steer_box: BoxContainer  # the box and its button: side by side, or the button under the box in a narrow dock
var _l_track
var _l_now_title: Label
var _l_now: Label
var _l_squares  # the harness attempts: their squares and count (W.Attempts)
var _l_attempts_row: HBoxContainer  # the micro-label, the squares, their count and the policy steps: one row
var _l_steps: Label  # "48 policy steps"
var _l_feed: RichTextLabel
var _feed_count := 0
var _live_meta: Label = null  # the live run's line in the rail
var _slow := 0

# usage, first-run setup, Aura
var settings_open := false  # the Settings page is shown
var onboarding := false  # the first-run setup is shown (in place of everything else)
var _settings_page: SettingsPage
var _usage_runs := []  # the runs with usage, kept between editor sessions (ui/usage.gd), oldest first
var _usage_saved_at := 0.0
var _usage_dirty := false
var _usage_note := ""
var _pending_usage = null  # [models, final] that came with no run to keep them on (given to the next run)
var _pending_bill := {}  # a bill that came with no run to keep it on (given to the run its result makes)
var _setup: SetupPage

# the agent service and the Aura sign-in
var service = Service.new()  # lib/service.gd
var auto_start_allowed := false  # the dock may start the agent service (not in a headless editor: automation)
## The Aura sign-in asked for here: {} (none), {"state": "preparing" | "waiting" | "signed_in" | "error" |
## "no_installer", "url", "email", "message", "log"}
var sign_in := {}
var _sign_in_send := false  # aura_sign_in is still to be sent (once the agent service is connected)
var _sign_in_installed := false  # this sign-in ran the installer already
var _connect_when_ready := false  # connect once the agent service started is ready
var _ensure_at := 0.0
var _meta_local := {}  # the settings outside the editor (checks)
var _svc_cmd: LineEdit
var _removal_page: ScrollContainer  # Removing Autopilot from this computer, and how it went (in place of everything else)
var _removal_key := []


func _ei():
	# (the dock also runs outside the editor in checks: no EditorInterface there)
	return Engine.get_singleton("EditorInterface") if Engine.is_editor_hint() and Engine.has_singleton("EditorInterface") else null


func _meta(key: String, default):
	var ei = _ei()
	if ei == null:
		return _meta_local.get(key, default)
	return ei.get_editor_settings().get_project_metadata("autopilot", key, default)


func _set_meta(key: String, value) -> void:
	var ei = _ei()
	if ei:
		ei.get_editor_settings().set_project_metadata("autopilot", key, value)
	else:
		_meta_local[key] = value


func _ready() -> void:
	name = "Autopilot"
	var ei = _ei()
	theme = HT.theme(ei.get_editor_scale() if ei else 1.0)
	add_theme_stylebox_override("panel", HT.box(HT.GROUND))
	size_flags_vertical = Control.SIZE_EXPAND_FILL
	custom_minimum_size = Vector2(HT.px(220), HT.px(240))
	auto_start_allowed = DisplayServer.get_name() != "headless"
	service.command_setting = service_command()
	# (a connection open is left alone: the agent service it reaches is never replaced under it)
	service.in_use = func() -> bool: return bridge != null and bridge.is_connected_to_brain()
	service.changed.connect(_on_service_changed)
	_build()
	resized.connect(_on_resized)

	bridge.status_changed.connect(_on_status)
	bridge.server_status.connect(_on_server_status)
	bridge.activity_received.connect(_on_activity)
	bridge.activity_since = _activity_since  # (each hello asks for the feed from where the run's is)
	bridge.connection_changed.connect(func(_on): _render())
	bridge.finding_received.connect(_on_finding)
	bridge.result_received.connect(_on_result)
	bridge.run_started_elsewhere.connect(_on_run_started_elsewhere)
	bridge.run_kind_received.connect(_on_run_kind)
	bridge.run_changed.connect(_on_run_changed)
	bridge.policy_step.connect(_on_policy_step)
	bridge.memory_changed.connect(_render_footer)
	bridge.memory_cleared.connect(_on_memory_cleared)
	bridge.usage_received.connect(_on_usage)
	bridge.bill_received.connect(_on_bill)
	bridge.account_changed.connect(_on_account)
	bridge.sign_in_frame.connect(_on_sign_in_frame)
	_usage_runs = Usage.load_runs(_usage_path())
	if history_dir == "" and ei:
		history_dir = ei.get_editor_paths().get_project_settings_dir().path_join(History.DIR_NAME)
	load_history()
	var env_url := OS.get_environment("AUTOPILOT_PLAYTEST_URL")
	if _url.text == "" and env_url != "":
		_url.text = env_url
	# The first-run setup, until the user went through it (never in a headless editor: automation)
	var prefs := Prefs.load_preferences()
	if not Prefs.onboarded(prefs) and DisplayServer.get_name() != "headless":
		_open_setup()
	elif service_setup_needed():
		_open_setup_at_service()
	if connects_by_itself():
		_connect.call_deferred()
	if HT.fonts_missing:
		_set_status("A bundled font couldn’t be read: using the editor’s font")
	_render()


## Whether the dock connects by itself when it starts: always, but in a headless editor (automation, unless
## AUTOPILOT_PLAYTEST_AUTOCONNECT says so), while the first-run setup is shown (it connects when finished) and once
## Autopilot was removed from this computer.
func connects_by_itself() -> bool:
	if removed():
		return false
	return (not onboarding and auto_start_allowed) or OS.get_environment("AUTOPILOT_PLAYTEST_AUTOCONNECT") == "1"


# --- building -----------------------------------------------------------------------------------------

func _caps(text: String, color := HT.MUTED) -> Label:
	var l := Label.new()
	l.theme_type_variation = "AutopilotCaps"
	l.text = text.to_upper()
	if color != HT.MUTED:
		l.add_theme_color_override("font_color", color)
	l.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	return l


func _label(text: String, variation := "AutopilotBody", wrap := true) -> Label:
	var l := Label.new()
	l.theme_type_variation = variation
	l.text = text
	if wrap:
		l.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	return l


## A button (sentence case, 28 tall; small: 24).
func _button(text: String, variation: String, action: Callable, small := false) -> Button:
	return HT.button(text, variation, action, small)


## A button that is only an icon (one of HT.GLYPHS, tinted by the button's icon colors), named by its tooltip.
func _icon_button(glyph: String, tip: String, variation: String, action: Callable, small := false) -> Button:
	return HT.icon_button(glyph, tip, variation, action, small)


func _spacer() -> Control:
	var c := Control.new()
	c.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	c.mouse_filter = Control.MOUSE_FILTER_IGNORE
	return c


func _margin(child: Control, l: float, t: float, r: float, b: float) -> MarginContainer:
	var m := MarginContainer.new()
	m.add_theme_constant_override("margin_left", HT.px(l))
	m.add_theme_constant_override("margin_top", HT.px(t))
	m.add_theme_constant_override("margin_right", HT.px(r))
	m.add_theme_constant_override("margin_bottom", HT.px(b))
	m.add_child(child)
	return m


## A panel with this box around child.
func _panel(child: Control, box: StyleBox) -> PanelContainer:
	var p := PanelContainer.new()
	p.add_theme_stylebox_override("panel", box)
	p.add_child(child)
	return p


## A Settings button: the gear (quiet; pressed as the gear "on" while the Settings page is open: _render_gears). small:
## the rail's (24 square), else the narrow top bar's (28).
func _gear(small := false) -> Button:
	var b := _icon_button("gear", "Settings", "AutopilotQuiet", toggle_settings, small)
	b.toggle_mode = true
	b.focus_mode = Control.FOCUS_NONE
	b.set_meta("small", small)
	_gears.append(b)
	return b


## The gear: the Settings page opened (asking the agent service for the account), or closed again.
func toggle_settings() -> void:
	settings_open = not settings_open
	if settings_open:
		ask_account()
	_render()


func _render_gears() -> void:
	for b in _gears:
		b.set_pressed_no_signal(settings_open)
		b.theme_type_variation = ("AutopilotOn" if settings_open else "AutopilotQuiet") + ("Sm" if b.get_meta("small", false) else "")


## A row of buttons that goes on onto more lines when the dock is narrow.
func _flow(sep := 6.0) -> HFlowContainer:
	var f := HFlowContainer.new()
	f.add_theme_constant_override("h_separation", HT.px(sep))
	f.add_theme_constant_override("v_separation", HT.px(6))
	return f


## A page's content: at most max_width wide, centred in a wider dock, with padding (left, top, right, bottom) around.
func _column(child: Control, pad: Vector4, max_width := COMPOSER_WIDTH) -> Control:
	var col := W.Column.new(max_width, pad)
	col.add_child(child)
	return col


## A page's header bar: its title (15/500) and what goes at its right, over a 1 px rule.
func _page_head(title: String) -> Array:
	var p := PanelContainer.new()
	p.theme_type_variation = "AutopilotHead"
	var h := HBoxContainer.new()
	h.add_theme_constant_override("separation", HT.px(8))
	p.add_child(h)
	var t := _label(title, "AutopilotTitle", false)
	t.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	t.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	h.add_child(t)
	return [p, h]


func _build() -> void:
	_body = HBoxContainer.new()
	_body.add_theme_constant_override("separation", 0)
	add_child(_body)

	# the rail (wide): the run history and the Memory row
	_rail = PanelContainer.new()
	_rail.theme_type_variation = "AutopilotRail"
	_rail.custom_minimum_size = Vector2(HT.px(RAIL_WIDTH), 0)
	_body.add_child(_rail)
	var rv := VBoxContainer.new()
	rv.add_theme_constant_override("separation", 0)
	_rail.add_child(rv)
	var rh := HBoxContainer.new()
	rh.add_theme_constant_override("separation", HT.px(8))
	var logo := TextureRect.new()
	logo.texture = HT.icon(14)
	logo.stretch_mode = TextureRect.STRETCH_KEEP_CENTERED
	logo.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	rh.add_child(logo)
	var rt := _label("History", "AutopilotStrong", false)
	rt.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	rt.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	rt.clip_text = true  # (the rail's width is its own: the head never widens it)
	rh.add_child(rt)
	rh.add_child(_gear(true))
	rh.add_child(_button("New run", "AutopilotPrimary", _on_new, true))
	rv.add_child(_panel(rh, HT.edge_box(HT.RAIL, HT.RULE, "bottom", 1, Vector4(14, 12, 14, 12))))
	var rs := ScrollContainer.new()
	rs.size_flags_vertical = Control.SIZE_EXPAND_FILL
	rs.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	rv.add_child(rs)
	_rail_list = VBoxContainer.new()
	_rail_list.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_rail_list.add_theme_constant_override("separation", 0)
	rs.add_child(_rail_list)
	_rail_footer_slot = VBoxContainer.new()
	_rail_footer_slot.add_theme_constant_override("separation", 0)
	rv.add_child(_rail_footer_slot)
	var edge := ColorRect.new()
	edge.color = HT.RULE
	edge.custom_minimum_size = Vector2(1, 0)
	edge.name = "RailEdge"
	_body.add_child(edge)

	_main = VBoxContainer.new()
	_main.add_theme_constant_override("separation", 0)
	_main.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_body.add_child(_main)

	# the top (narrow): the history button (the run shown, its list under it), the gear and New run
	_top = HBoxContainer.new()
	_top.add_theme_constant_override("separation", HT.px(6))
	_main.add_child(_panel(_top, HT.edge_box(HT.GROUND, HT.RULE, "bottom", 1, Vector4(10, 8, 10, 8))))
	_hist_btn = Button.new()
	_hist_btn.theme_type_variation = "AutopilotDrop"
	_hist_btn.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_hist_btn.custom_minimum_size = Vector2(0, HT.px(28))
	_hist_btn.tooltip_text = "History"
	_hist_btn.pressed.connect(_open_history_menu)
	_top.add_child(_hist_btn)
	var hb := HBoxContainer.new()
	hb.mouse_filter = Control.MOUSE_FILTER_IGNORE
	hb.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	hb.offset_left = HT.px(8)
	hb.offset_right = -HT.px(8)
	hb.add_theme_constant_override("separation", HT.px(8))
	_hist_btn.add_child(hb)
	_hist_tag_slot = HBoxContainer.new()
	_hist_tag_slot.mouse_filter = Control.MOUSE_FILTER_IGNORE
	hb.add_child(_hist_tag_slot)
	_hist_meta = _label("", "AutopilotMonoDim", false)
	_hist_meta.add_theme_color_override("font_color", HT.TEXT3)
	_hist_meta.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	_hist_meta.clip_text = true
	_hist_meta.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	_hist_meta.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_hist_meta.mouse_filter = Control.MOUSE_FILTER_IGNORE
	hb.add_child(_hist_meta)
	var chev := TextureRect.new()
	chev.texture = HT.glyph("chevron", 10, HT.TEXT3)
	chev.stretch_mode = TextureRect.STRETCH_KEEP_CENTERED
	chev.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	chev.mouse_filter = Control.MOUSE_FILTER_IGNORE
	hb.add_child(chev)
	_top.add_child(_gear())
	_new_btn = _button("New run", "AutopilotPrimary", _on_new)
	_top.add_child(_new_btn)
	_hist_menu = PopupPanel.new()
	var hv := VBoxContainer.new()
	hv.add_theme_constant_override("separation", 0)
	_hist_menu.add_child(hv)
	var hs := ScrollContainer.new()
	hs.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	hs.size_flags_vertical = Control.SIZE_EXPAND_FILL
	hv.add_child(hs)
	_hist_list = VBoxContainer.new()
	_hist_list.add_theme_constant_override("separation", 0)
	_hist_list.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	hs.add_child(_hist_list)
	_hist_footer_slot = VBoxContainer.new()
	hv.add_child(_hist_footer_slot)
	add_child(_hist_menu)

	# the notice shown above a run while not connected
	var sh := HBoxContainer.new()
	sh.add_theme_constant_override("separation", HT.px(8))
	_strip_text = _label("", "AutopilotSmall")
	_strip_text.add_theme_color_override("font_color", HT.AMBER_FG)
	_strip_text.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_strip_text.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	sh.add_child(_strip_text)
	var strip_action := func() -> void:
		if _conn in ["connecting", "unreachable", "service"]:
			_select_run(null)
		else:
			_connect()
	_strip_btn = _button("Details", "Button", strip_action, true)
	sh.add_child(_strip_btn)
	_strip = _panel(sh, HT.edge_box(HT.AMBER_FILL, HT.AMBER_LINE, "bottom", 1, Vector4(10, 7, 10, 7)))
	_main.add_child(_strip)

	_pages = VBoxContainer.new()
	_pages.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_pages.add_theme_constant_override("separation", 0)
	_main.add_child(_pages)
	_build_connect_page()
	_build_composer()
	_build_run_page()
	_settings_page = SettingsPage.new()
	_settings_page.back.connect(func():
		settings_open = false
		_render())
	_settings_page.share_toggled.connect(_on_share_toggled)
	_settings_page.account_action.connect(_on_account_action)
	_settings_page.remove_asked.connect(remove_from_computer)
	_pages.add_child(_settings_page)

	_footer_slot = VBoxContainer.new()
	_footer_slot.add_theme_constant_override("separation", 0)
	_main.add_child(_footer_slot)
	_build_footer()


	# the first-run setup: over everything else while shown
	_setup = SetupPage.new()
	_setup.host = self
	_setup.visible = false
	_setup.finished.connect(_on_setup_finished)
	add_child(_setup)

	_removal_page = ScrollContainer.new()
	_removal_page.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	_removal_page.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_removal_page.visible = false
	add_child(_removal_page)


## A field: its micro-label, then the control (5 apart).
func _field(caption: String, control: Control) -> VBoxContainer:
	var v := VBoxContainer.new()
	v.add_theme_constant_override("separation", HT.px(5))
	v.add_child(_caps(caption))
	v.add_child(control)
	control.custom_minimum_size.y = maxf(control.custom_minimum_size.y, HT.px(28))
	return v


func _build_connect_page() -> void:
	_connect_page = ScrollContainer.new()
	_connect_page.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	_connect_page.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_pages.add_child(_connect_page)
	var v := VBoxContainer.new()
	v.add_theme_constant_override("separation", HT.px(10))
	_connect_page.add_child(_column(v, Vector4(20, 24, 20, 24), CONNECT_WIDTH))
	var card := PanelContainer.new()
	card.add_theme_stylebox_override("panel", HT.plate_box(Vector4(0, 0, 0, 0)))
	v.add_child(card)
	# under the card, while disconnected: back to the run shown before
	_cp_back = _button("Back to the last run", "AutopilotQuiet", func(): _select_run(back_run()), true)
	_cp_back.size_flags_horizontal = Control.SIZE_SHRINK_BEGIN
	v.add_child(_cp_back)
	var outer := VBoxContainer.new()
	outer.add_theme_constant_override("separation", 0)
	card.add_child(outer)
	var cv := VBoxContainer.new()
	cv.add_theme_constant_override("separation", HT.px(10))
	outer.add_child(_margin(cv, 14, 12, 14, 12))
	# its head: what it is, and where it comes from
	var top := VBoxContainer.new()
	top.add_theme_constant_override("separation", HT.px(4))
	cv.add_child(top)
	var head := HFlowContainer.new()
	head.add_theme_constant_override("h_separation", HT.px(8))
	head.add_theme_constant_override("v_separation", HT.px(4))
	var title := _label("Agent service", "AutopilotTitle", false)
	head.add_child(title)
	head.add_child(HT.tag("Not connected", "mut"))
	top.add_child(head)
	var where := HFlowContainer.new()
	where.add_theme_constant_override("h_separation", HT.px(4))
	var wl := _label("Start the agent service with", "AutopilotSmall", false)
	wl.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	where.add_child(wl)
	var cl := _label("autopilot serve", "AutopilotMono", false)
	cl.add_theme_font_size_override("font_size", HT.px(11.5))
	cl.add_theme_color_override("font_color", HT.ACCENT)
	cl.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	where.add_child(cl)
	_cp_where_own = where
	top.add_child(where)
	_cp_where_aura = _label("Start the Aura app: it runs the agent service.", "AutopilotSmall")
	top.add_child(_cp_where_aura)
	# the state: an alert (its line, and a detail) in its tone, then its buttons
	_cp_box = PanelContainer.new()
	cv.add_child(_cp_box)
	var bh := HBoxContainer.new()
	bh.add_theme_constant_override("separation", HT.px(10))
	_cp_box.add_child(bh)
	_cp_spin = W.Spinner.new()
	bh.add_child(_cp_spin)
	var bv := VBoxContainer.new()
	bv.add_theme_constant_override("separation", HT.px(2))
	bv.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	bh.add_child(bv)
	_cp_title = _label("")
	bv.add_child(_cp_title)
	_cp_text = _label("", "AutopilotMonoDim")
	_cp_text.add_theme_font_size_override("font_size", HT.px(10.5))
	_cp_text.add_theme_color_override("font_color", HT.NEUTRAL_FG)
	_cp_text.autowrap_mode = TextServer.AUTOWRAP_ARBITRARY
	bv.add_child(_cp_text)
	_cp_buttons = _flow(6)
	cv.add_child(_cp_buttons)

	# Advanced: under a rule, folded
	outer.add_child(HT.rule())
	var fold := FoldableContainer.new()
	fold.title = "ADVANCED"
	fold.folded = true
	outer.add_child(_margin(fold, 14, 9, 14, 12))
	var av := VBoxContainer.new()
	av.add_theme_constant_override("separation", HT.px(10))
	fold.add_child(av)
	_url = LineEdit.new()
	_url.theme_type_variation = "AutopilotMonoField"
	_url.text = str(_meta("url", ""))
	_url.tooltip_text = "Empty: ws://127.0.0.1:8765/v1/bridge, the agent service on this computer. You may use your own endpoint."
	av.add_child(_field("Agent service URL", _url))
	_token = LineEdit.new()
	_token.secret = true
	_token.text = str(_meta("token", ""))
	av.add_child(_field("Access token · optional", _token))
	_svc_cmd = LineEdit.new()
	_svc_cmd.placeholder_text = "Detected automatically"
	_svc_cmd.tooltip_text = "The command that runs the agent service, without “serve”: e.g. C:\\autopilot\\.venv\\Scripts\\python.exe -m autopilot"
	_svc_cmd.text = service_command()
	_svc_cmd.text_changed.connect(func(t: String) -> void: set_service_command(t))
	av.add_child(_field("Agent service command · optional", _svc_cmd))
	for e in [_url, _token]:
		e.text_submitted.connect(func(_t: String) -> void: _apply_address())
		e.focus_exited.connect(_apply_address)


## The address or token changed under Advanced (Enter, or the field left): kept, and connected again with them.
func _apply_address() -> void:
	var url := _url.text.strip_edges()
	var token := _token.text.strip_edges()
	if url == str(_meta("url", "")) and token == str(_meta("token", "")):
		return
	_set_meta("url", url)
	_set_meta("token", token)
	if removed() or active != null:
		return  # (the run going on keeps its connection: the next connect uses them)
	bridge.disconnect_from_brain()
	_connect()


## The prompt box's placeholder: any words do (the agent service reads which kind of run they ask for).
const PROMPT_PLACEHOLDER := "Describe what to verify, reach or explore"
## The steer box's placeholder (at every stage), and the Steer button's tooltip before the game plays (the Rollout stage).
const STEER_HINT := "Send a steering prompt to Autopilot"
const STEER_WAIT := "You can steer once the game is playing"
const RUN_NOTE := "A run is going: stop it or wait for its result."


func _build_composer() -> void:
	_composer = VBoxContainer.new()
	_composer.add_theme_constant_override("separation", 0)
	_composer.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_pages.add_child(_composer)
	var head := _page_head("New run")
	_composer.add_child(head[0])
	var scroll := ScrollContainer.new()
	scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	scroll.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_composer.add_child(scroll)
	var v := VBoxContainer.new()
	v.add_theme_constant_override("separation", HT.px(10))
	v.size_flags_vertical = Control.SIZE_EXPAND_FILL
	var col := _column(v, Vector4(20, 28, 20, 20))
	col.size_flags_vertical = Control.SIZE_EXPAND_FILL
	col.name = "ComposerColumn"
	scroll.add_child(col)
	_c_head = head[0]
	# out of Aura credits: Aura's words and Add credits, in red over the box (Run stays: Aura checks again as a run starts)
	var ch := HBoxContainer.new()
	ch.add_theme_constant_override("separation", HT.px(10))
	_c_credits_text = _label("")
	_c_credits_text.add_theme_color_override("font_color", HT.RED_FG)
	_c_credits_text.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_c_credits_text.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	ch.add_child(_c_credits_text)
	_c_credits_btn = _button("Add credits", "Button", func(): OS.shell_open(_c_credits_url), true)
	_c_credits_btn.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	ch.add_child(_c_credits_btn)
	_c_credits = _panel(ch, HT.box(HT.RED_FILL, 0, HT.RED_LINE, 1, Vector4(11, 9, 11, 9)))
	_c_credits.visible = false
	v.add_child(_c_credits)
	# the prompt box: the words, then (one row, at the right) the Time limit and Run
	var bv := VBoxContainer.new()
	bv.add_theme_constant_override("separation", HT.px(8))
	v.add_child(_panel(bv, HT.box(HT.WELL, HT.R_PLATE, HT.LINE, 1, Vector4(12, 11, 12, 9))))
	_c_prompt = TextEdit.new()
	_c_prompt.theme_type_variation = "AutopilotBare"
	_c_prompt.custom_minimum_size = Vector2(0, HT.px(100))
	_c_prompt.wrap_mode = TextEdit.LINE_WRAPPING_BOUNDARY
	_c_prompt.placeholder_text = PROMPT_PLACEHOLDER
	_c_prompt.text_changed.connect(_render_composer)
	bv.add_child(_c_prompt)
	_c_row = HBoxContainer.new()
	_c_row.add_theme_constant_override("separation", HT.px(8))
	bv.add_child(_c_row)
	_c_row.add_child(_spacer())
	_c_time_row = HBoxContainer.new()
	_c_time_row.add_theme_constant_override("separation", HT.px(8))
	_c_time_row.add_child(_caps("Time limit"))
	_c_time_limit = SpinBox.new()
	_c_time_limit.min_value = Model.TIME_LIMIT_MIN
	_c_time_limit.max_value = Model.TIME_LIMIT_MAX
	_c_time_limit.value = time_limit()
	_number_field(_c_time_limit, 52)
	_c_time_limit.value_changed.connect(_on_time_limit_changed)
	_c_time_row.add_child(_c_time_limit)
	_c_row.add_child(_c_time_row)
	_c_run = _button("Run", "AutopilotPrimary", _on_run)
	_c_run.custom_minimum_size.x = HT.font("display").get_string_size("Run", HORIZONTAL_ALIGNMENT_LEFT, -1, HT.px(12)).x + HT.px(36)
	_c_row.add_child(_c_run)
	_c_note = _label("", "AutopilotSmall")
	_c_note.add_theme_color_override("font_color", HT.MUTED)
	v.add_child(_c_note)
	var fill := Control.new()
	fill.size_flags_vertical = Control.SIZE_EXPAND_FILL
	fill.mouse_filter = Control.MOUSE_FILTER_IGNORE
	v.add_child(fill)
	# the link: its state, its address and Disconnect, at the right over a rule
	var foot := VBoxContainer.new()
	foot.add_theme_constant_override("separation", HT.px(10))
	foot.add_child(HT.rule())
	var link := HBoxContainer.new()
	link.add_theme_constant_override("separation", HT.px(10))
	link.alignment = BoxContainer.ALIGNMENT_END
	_link_label = _label("", "AutopilotCaps", false)
	_link_label.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	link.add_child(_link_label)
	_link_url = _label("", "AutopilotMonoDim", false)
	_link_url.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	_link_url.clip_text = true
	_link_url.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	_link_url.size_flags_horizontal = Control.SIZE_SHRINK_END | Control.SIZE_EXPAND
	_link_url.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT
	link.add_child(_link_url)
	var toggle_link := func() -> void:
		if bridge.link.want or bridge.is_connected_to_brain() or _connect_when_ready:
			_disconnect()
		else:
			_connect()
	_link_btn = _button("Disconnect", "AutopilotQuiet", toggle_link, true)
	link.add_child(_link_btn)
	foot.add_child(link)
	v.add_child(foot)
	_render_composer()


## A SpinBox as a plain number field: width px wide, mono 12, its number at the right (no arrows: the theme's).
func _number_field(spin: SpinBox, width: float) -> void:
	spin.custom_minimum_size = Vector2(HT.px(width), HT.px(28))
	spin.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	spin.rounded = true
	spin.alignment = HORIZONTAL_ALIGNMENT_RIGHT
	var e := spin.get_line_edit()
	e.theme_type_variation = "AutopilotMonoField"
	e.alignment = HORIZONTAL_ALIGNMENT_RIGHT


func _build_run_page() -> void:
	_run_page = VBoxContainer.new()
	_run_page.add_theme_constant_override("separation", 0)
	_run_page.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_pages.add_child(_run_page)
	_tabbar = PanelContainer.new()
	_tabbar.theme_type_variation = "AutopilotBar"
	_run_page.add_child(_tabbar)
	var th := HBoxContainer.new()
	th.add_theme_constant_override("separation", HT.px(20))
	_tabbar.add_child(th)
	var g := ButtonGroup.new()
	_tab_main = _tab_button("Result", g, "main")
	th.add_child(_tab_main)
	_tab_feed = _tab_button("Feed", g, "feed")
	th.add_child(_tab_feed)
	_tab_policy = _tab_button("Policy log", g, "policy")
	th.add_child(_tab_policy)
	_tab_link = _label("", "AutopilotCaps", false)  # (the run's id line; cut short, never widening the dock)
	_tab_link.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	_tab_link.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_tab_link.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT
	_tab_link.clip_text = true
	_tab_link.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	th.add_child(_tab_link)
	_tab_local = _caps("● Local agent", HT.SUCCESS)
	th.add_child(_tab_local)

	_build_live()
	_result = ScrollContainer.new()
	_result.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	_result.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_result.resized.connect(_fit_result)
	_run_page.add_child(_result)
	# (the transcript: rows in a column that scrolls, following the end while the run goes on)
	_feed_page = ScrollContainer.new()
	_feed_page.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	_feed_page.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_feed_rows = VBoxContainer.new()
	_feed_rows.add_theme_constant_override("separation", 0)
	_feed_rows.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_feed_page.add_child(_feed_rows)
	_feed_page.get_v_scroll_bar().changed.connect(func() -> void:
		if shown != null and shown.live() and _feed_area.visible:
			_feed_page.scroll_vertical = int(_feed_page.get_v_scroll_bar().max_value))
	_feed_area = VBoxContainer.new()
	_feed_area.add_theme_constant_override("separation", 0)
	_feed_area.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_run_page.add_child(_feed_area)
	var nothing := _label("Nothing yet.", "AutopilotSmall")
	nothing.add_theme_color_override("font_color", HT.MUTED)
	_feed_empty = _margin(nothing, 16, 16, 16, 16)
	_feed_area.add_child(_feed_empty)
	_feed_box = _margin(_panel(_feed_page, HT.plate_box(Vector4(11, 6, 11, 8))), 16, 16, 16, 16)
	_feed_box.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_feed_area.add_child(_feed_box)
	# (the Feed tab's foot: the Live page's steer box, moved here while the Feed tab is shown: one box, one text)
	_feed_steer_slot = VBoxContainer.new()
	_feed_steer_slot.add_theme_constant_override("separation", 0)
	_run_page.add_child(_feed_steer_slot)
	_policy = PolicyLog.new()
	_policy.copied.connect(func(): _set_status("Copied"))
	_run_page.add_child(_policy)


func _tab_button(text: String, group: ButtonGroup, key: String) -> Button:
	var b := Button.new()
	b.theme_type_variation = "AutopilotTab"
	b.toggle_mode = true
	b.button_group = group
	b.text = text
	b.focus_mode = Control.FOCUS_NONE
	b.toggled.connect(func(on: bool) -> void:
		if on and tab != key:
			tab = key
			_render_run())
	return b


func _build_live() -> void:
	_live = VBoxContainer.new()
	_live.add_theme_constant_override("separation", 0)
	_live.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_run_page.add_child(_live)
	var head := PanelContainer.new()
	head.theme_type_variation = "AutopilotHead"
	_live.add_child(head)
	var hh := HBoxContainer.new()
	hh.add_theme_constant_override("separation", HT.px(12))
	head.add_child(hh)
	hh.add_child(_caps("● Live", HT.ACCENT))
	_l_prompt = _label("", "AutopilotTitle", false)
	_l_prompt.clip_text = true
	_l_prompt.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	_l_prompt.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	# (the request, and under it where a run the tab didn't start came from)
	var pv := VBoxContainer.new()
	pv.add_theme_constant_override("separation", 0)
	pv.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	pv.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	pv.add_child(_l_prompt)
	_l_origin = _label("", "AutopilotSmall", false)
	_l_origin.clip_text = true
	_l_origin.visible = false
	pv.add_child(_l_origin)
	hh.add_child(pv)
	_l_elapsed_cap = _caps("Elapsed")
	hh.add_child(_l_elapsed_cap)
	_l_elapsed = _label("00:00", "AutopilotMonoBig", false)
	_l_elapsed.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	hh.add_child(_l_elapsed)
	_l_stop = _button("Stop", "Button", _on_stop)
	hh.add_child(_l_stop)

	# (in a scroll container: a dock too short for all of it scrolls; a taller one gives the feed the room)
	var ls := ScrollContainer.new()
	ls.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	ls.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_live.add_child(ls)
	var left := VBoxContainer.new()
	left.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	left.size_flags_vertical = Control.SIZE_EXPAND_FILL
	left.add_theme_constant_override("separation", HT.px(16))
	ls.add_child(_margin(left, 16, 18, 16, 18))
	left.get_parent().size_flags_horizontal = Control.SIZE_EXPAND_FILL
	left.get_parent().size_flags_vertical = Control.SIZE_EXPAND_FILL
	_l_track = W.StageTrack.new()
	left.add_child(_l_track)
	# now: what the agent is doing, in a plate
	var nv := VBoxContainer.new()
	nv.add_theme_constant_override("separation", HT.px(3))
	left.add_child(_panel(nv, HT.plate_box(Vector4(11, 9, 11, 9))))
	_l_now_title = _caps("Now")
	nv.add_child(_l_now_title)
	var nh := HBoxContainer.new()
	nh.add_theme_constant_override("separation", HT.px(8))
	nv.add_child(nh)
	nh.add_child(HT.dot(HT.ACCENT))
	_l_now = _label("", "AutopilotStrong")
	_l_now.add_theme_font_size_override("font_size", HT.px(14))
	_l_now.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	nh.add_child(_l_now)
	# the harness attempts: a square each (filled when finished, outlined the current one), their count, the policy steps
	# (one row at every width: the squares never go onto another line, as many shown as fit, the count says how many)
	var ah := HBoxContainer.new()
	ah.add_theme_constant_override("separation", HT.px(4))
	_l_attempts_row = ah
	left.add_child(ah)
	ah.add_child(_caps("Harness attempts"))
	var gap := Control.new()
	gap.custom_minimum_size = Vector2(HT.px(4), 0)
	ah.add_child(gap)
	_l_squares = W.Attempts.new()
	ah.add_child(_l_squares)
	_l_steps = _label("", "AutopilotMonoDim", false)
	_l_steps.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT
	_l_steps.size_flags_vertical = Control.SIZE_SHRINK_CENTER  # (at the right: the squares take the room between)
	ah.add_child(_l_steps)
	# the feed, in a plate under its caption
	var fv := VBoxContainer.new()
	fv.add_theme_constant_override("separation", 0)
	var fp := _panel(fv, HT.plate_box(Vector4(0, 0, 0, 0)))
	fp.size_flags_vertical = Control.SIZE_EXPAND_FILL
	left.add_child(fp)
	var fc := _label("Feed", "AutopilotStrong", false)
	fc.add_theme_font_size_override("font_size", HT.px(12))
	fv.add_child(_margin(fc, 11, 9, 11, 9))
	fv.add_child(HT.rule())
	_l_feed = RichTextLabel.new()
	_l_feed.add_theme_constant_override("line_separation", HT.px(10))
	_l_feed.scroll_following = true
	_l_feed.selection_enabled = true
	_l_feed.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_l_feed.custom_minimum_size = Vector2(0, HT.px(90))
	var fm := _margin(_l_feed, 11, 4, 11, 8)
	fm.size_flags_vertical = Control.SIZE_EXPAND_FILL
	fv.add_child(fm)

	# steer: the user's words to the run while it plays (it goes on from where the game is), in a box at the page's
	# foot; the words can be typed at any stage, the Steer button sends them once the game plays. The button goes under
	# the words when the box beside it would be too narrow for its placeholder (_fit_steer_row).
	_l_steer_box = BoxContainer.new()
	var sr := _l_steer_box
	sr.add_theme_constant_override("separation", HT.px(8))
	_l_steer = LineEdit.new()
	_l_steer.theme_type_variation = "AutopilotBareLine"
	_l_steer.placeholder_text = STEER_HINT
	_l_steer.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_l_steer.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	_l_steer.text_changed.connect(func(_t: String) -> void: _update_steer_button())
	_l_steer.text_submitted.connect(func(_t: String) -> void: _on_steer())
	sr.add_child(_l_steer)
	_l_steer_btn = _button("Steer", "AutopilotPrimary", _on_steer)
	_l_steer_btn.size_flags_horizontal = Control.SIZE_SHRINK_END  # (under the words: at their right)
	sr.add_child(_l_steer_btn)
	var well := _panel(sr, HT.box(HT.WELL, HT.R_PLATE, HT.LINE, 1, Vector4(12, 6, 6, 6)))
	_l_steer_row = _panel(well, HT.edge_box(HT.GROUND, HT.RULE, "top", 1, Vector4(16, 10, 16, 12)))
	_l_steer_row.visible = false
	_l_steer_row.resized.connect(_fit_steer_row)
	_live.add_child(_l_steer_row)


func _build_footer() -> void:
	# the Memory row: what is remembered takes the room left and is cut short (…) before it reaches Reset memory, kept whole
	var save := HBoxContainer.new()
	save.add_theme_constant_override("separation", HT.px(8))
	save.add_child(_caps("Memory"))
	_save_label = _label("", "AutopilotMono", false)
	_save_label.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	_save_label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_save_label.clip_text = true
	_save_label.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	_save_label.tooltip_text = MEMORY_TIP
	_save_label.mouse_filter = Control.MOUSE_FILTER_PASS
	save.add_child(_save_label)
	_reset_btn = _button("Reset memory…", "AutopilotDanger", _on_reset_memory, true)
	_reset_btn.tooltip_text = RESET_TIP
	_reset_btn.mouse_filter = Control.MOUSE_FILTER_PASS  # (its tooltip says why while it is disabled)
	save.add_child(_reset_btn)
	# under it, how the reset went: Resetting memory… (a spinner), done, or not
	_mem_bar = VBoxContainer.new()
	_mem_bar.add_theme_constant_override("separation", HT.px(6))
	_mem_bar.add_child(save)
	_reset_line = HBoxContainer.new()
	_reset_line.add_theme_constant_override("separation", HT.px(8))
	_reset_spin = W.Spinner.new()
	_reset_line.add_child(_reset_spin)
	_reset_text = _label("", "AutopilotSmall")
	_reset_text.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_reset_line.add_child(_reset_text)
	_mem_bar.add_child(_reset_line)
	# the confirmation, in place of the row: a plate with a red border, the question, what it does, Reset memory, Cancel
	var av := VBoxContainer.new()
	av.add_theme_constant_override("separation", HT.px(6))
	av.add_child(_label("Reset memory?", "AutopilotStrong"))
	av.add_child(_label(RESET_TEXT, "AutopilotSmall"))
	var ab := HBoxContainer.new()
	ab.add_theme_constant_override("separation", HT.px(6))
	ab.add_child(_button("Reset memory", "AutopilotDangerFill", _confirm_reset, true))
	var cancel := func() -> void:
		_reset = ""
		_render_footer()
	ab.add_child(_button("Cancel", "AutopilotQuiet", cancel, true))
	av.add_child(_margin(ab, 0, 2, 0, 0))
	_mem_ask = _panel(av, HT.plate_box(Vector4(12, 11, 12, 11), HT.RED_LINE))
	var fv := VBoxContainer.new()
	fv.add_theme_constant_override("separation", 0)
	fv.add_child(_mem_bar)
	fv.add_child(_mem_ask)
	_footer = PanelContainer.new()
	_footer.theme_type_variation = "AutopilotFooter"
	_footer.add_child(fv)
	_rail_footer_slot.add_child(_footer)
	# the status line: the main column's last row on every page (wide or narrow), over a rule; one line, cut short (its
	# whole text in its tooltip); hidden while there is no message, and STATUS_FOR seconds after the latest
	_status = _label("", "AutopilotMono", false)
	_status.add_theme_color_override("font_color", HT.MUTED)
	_status.clip_text = true
	_status.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	_status.mouse_filter = Control.MOUSE_FILTER_PASS
	_status_bar = _panel(_status, HT.edge_box(HT.GROUND, HT.RULE, "top", 1, Vector4(16, 6, 16, 6)))
	_status_bar.visible = false
	_footer_slot.add_child(_status_bar)


# --- layout -------------------------------------------------------------------------------------------

func _on_resized() -> void:
	var w := size.x >= WIDE_AT * HT.s
	if w != wide:
		wide = w
		_render()


func _apply_layout() -> void:
	_rail.visible = wide
	_body.get_node("RailEdge").visible = wide
	_top.get_parent().visible = not wide  # (its gear and New run: always there in a narrow dock)
	# the Memory row: the rail's foot, or the narrow history list's
	_move_to(_footer, _rail_footer_slot if wide else _hist_footer_slot, 0)
	# the composer: 28 × 20 around its column (16 × 12 in a narrow dock); its header 12 × 16 (10 × 12)
	var col: W.Column = _composer.find_child("ComposerColumn", true, false)
	col.pad = Vector4(20, 28, 20, 20) if wide else Vector4(12, 16, 12, 12)
	col.queue_sort()
	_c_head.add_theme_stylebox_override("panel", HT.edge_box(HT.GROUND, HT.RULE, "bottom", 1,
		Vector4(16, 12, 16, 12) if wide else Vector4(12, 10, 12, 10)))
	# the link's row: at the right when wide; the address taking the room left (cut short) when narrow
	_link_url.get_parent().alignment = BoxContainer.ALIGNMENT_END if wide else BoxContainer.ALIGNMENT_BEGIN
	_link_url.size_flags_horizontal = (Control.SIZE_SHRINK_END | Control.SIZE_EXPAND) if wide else Control.SIZE_EXPAND_FILL
	_link_url.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT if wide else HORIZONTAL_ALIGNMENT_LEFT
	_tab_link.visible = wide
	_tab_local.visible = wide and shown != null and shown.live() and local_agent()
	if _policy.wide != wide:
		_policy.set_wide(wide)


func _move_to(node: Control, parent: Control, at := -1) -> void:
	if node.get_parent() != parent:
		node.get_parent().remove_child(node)
		parent.add_child(node)
	if at >= 0:
		parent.move_child(node, at)


# --- rendering ----------------------------------------------------------------------------------------

func _conn_state() -> String:
	var l = bridge.link
	if bridge.is_connected_to_brain():
		return "connected"
	if _connect_when_ready and service.busy():
		return "service"
	if l.want:
		if l.ws == null and l.problem != "":
			return "unreachable"
		return "connecting"
	if l.problem_code != 0:
		return "refused"
	return "idle"


func _render() -> void:
	if _body == null:
		return
	_conn = _conn_state()
	_apply_layout()
	_render_history()
	_render_connect_page()
	_render_footer()
	_body.visible = not onboarding and not removed()
	_setup.visible = onboarding and not removed()
	_removal_page.visible = removed()
	if removed():
		_render_removal()
	var online := _conn == "connected"
	var page := "run"
	if shown == null or composing:
		page = "composer" if online else "connect"
	if settings_open:
		page = "settings"
	_connect_page.visible = page == "connect"
	_composer.visible = page == "composer"
	_run_page.visible = page == "run"
	_settings_page.visible = page == "settings"
	_strip.visible = page == "run" and not online and shown != null and not shown.live()
	var trying := _conn in ["connecting", "unreachable", "service"]
	_strip_text.text = "Reconnecting to the agent service…" if trying else "Not connected to the agent service"
	_strip_btn.text = "Details" if trying else "Reconnect"
	_strip.add_theme_stylebox_override("panel", HT.edge_box(HT.AMBER_FILL, HT.AMBER_LINE, "bottom", 1,
		Vector4(16 if wide else 10, 7, 16 if wide else 10, 7)))
	if page == "composer":
		_render_composer()
	if page == "run":
		_render_run()
	if page == "settings":
		_render_usage()
	_render_gears()


## The connect card's state now: {"tone": "info" | "amber" | "red" | "" (no line), "text", "detail", "buttons":
## [[label, variation, action]]} (the actions: _on_connect_action).
func connect_state() -> Dictionary:
	var l = bridge.link
	var stop_trying := ["Stop", "AutopilotQuiet", "stop"]
	if _with_aura():
		match _conn:
			"connecting":
				return {"tone": "info", "text": "Connecting to the agent service…", "detail": "",
					"buttons": [["Stop", "AutopilotQuiet", "stop"]]}
			"unreachable":
				var b := []
				if not Prefs.aura_installed():
					b.append(["Get Aura", "Button", "get_aura"])  # (its download page)
				elif Prefs.aura_service_url() == "":
					b.append(["Start Aura", "Button", "start_aura"])
				b.append(["Try now", "AutopilotPrimary", "try_now"])
				b.append(stop_trying)
				return _aura_problem(l, b)
			"refused":
				return {"tone": "red", "text": Model.scrub(l.problem), "detail": "", "buttons": [["Try again", "AutopilotPrimary", "connect"]]}
		return _disconnected(l)
	if _auto_service() and _conn != "connected":
		match service.state:
			"starting", "installing":
				if _connect_when_ready:
					return {"tone": "info", "detail": "", "buttons": [stop_trying],
						"text": "Installing the agent service…" if service.state == "installing" else "Starting the agent service…"}
			"stopped", "failed", "install_failed":
				var b := [["Try again", "AutopilotPrimary", "retry"]]
				if service.log_path != "":
					b.append(["View logs", "Button", "open_log"])
				b.append(stop_trying)
				# (installing it failing, or it stopping: the same Failed state; what the agent service said under it)
				return {"tone": "red", "text": "The agent service didn’t start.", "detail": service.message, "buttons": b}
			"not_installed":
				if service.has_installer():
					return {"tone": "amber", "text": "The agent service isn’t installed on this computer.", "detail": "",
						"buttons": [["Install", "AutopilotPrimary", "install"], stop_trying]}
				return {"tone": "amber", "text": "No agent service found.",
					"detail": "Set Agent service command under Advanced, or run autopilot serve.",
					"buttons": [["Try again", "AutopilotPrimary", "retry"], stop_trying]}
	match _conn:
		"connecting":
			return {"tone": "info", "text": "Connecting to the agent service…", "detail": "", "buttons": [["Stop", "AutopilotQuiet", "stop"]]}
		"unreachable":
			return {"tone": "amber", "text": "Can’t reach the agent service, retrying", "detail": str(l.url),
				"buttons": [["Try now", "AutopilotPrimary", "try_now"], stop_trying]}
		"refused":
			return {"tone": "red", "text": Model.scrub(l.problem), "detail": "", "buttons": [["Try again", "AutopilotPrimary", "connect"]]}
	return _disconnected(l)


## Can't reach the Aura app's agent service: Aura isn't running (its line and what happens next, as two), or what went
## wrong.
func _aura_problem(l, buttons: Array) -> Dictionary:
	var problem := Model.scrub(l.problem)
	if problem.begins_with("Aura isn’t running on this machine."):
		return {"tone": "amber", "text": "Aura isn’t running on this machine.", "detail": "Autopilot connects once it starts.",
			"buttons": buttons}
	if problem.begins_with("Aura isn’t installed on this machine."):
		return {"tone": "amber", "text": "Aura isn’t installed on this machine.", "detail": "", "buttons": buttons}
	return {"tone": "amber", "text": problem, "detail": "", "buttons": buttons}


## Disconnected (Disconnect, or Stop): Reconnect, and what went wrong last if anything did.
func _disconnected(l) -> Dictionary:
	var problem: String = Model.scrub(l.problem) if l.problem != "" else ""
	return {"tone": "amber" if problem != "" else "", "text": problem, "detail": "",
		"buttons": [["Reconnect", "AutopilotPrimary", "connect"]]}


func _render_connect_page() -> void:
	var st := connect_state()
	_cp_back.visible = _conn == "idle" and back_run() != null
	_cp_where_own.visible = not _with_aura()
	_cp_where_aura.visible = _with_aura()
	var tone := str(st.tone)
	_cp_box.visible = tone != ""
	if tone != "":
		# (an alert: square, its tone's surface and border; info with the spinner, its line in the tone's text color)
		var line: Color = {"info": HT.INFO_LINE, "amber": HT.AMBER_LINE, "red": HT.RED_LINE}[tone]
		var fill: Color = {"info": HT.INFO_FILL, "amber": HT.AMBER_FILL, "red": HT.RED_FILL}[tone]
		_cp_box.add_theme_stylebox_override("panel", HT.box(fill, 0, line, 1, Vector4(11, 9, 11, 9)))
		_cp_spin.visible = tone == "info"
		_cp_title.text = str(st.text)
		_cp_title.theme_type_variation = "AutopilotBody" if tone == "info" else "AutopilotStrong"
		_cp_title.add_theme_color_override("font_color", {"info": HT.INFO_FG, "amber": HT.AMBER_FG, "red": HT.RED_FG}[tone])
		_cp_text.text = str(st.detail)
		_cp_text.visible = _cp_text.text != ""
	if st.buttons == _cp_key:
		return  # (unchanged: the buttons stay, a click on them isn't lost to a rebuild)
	_cp_key = st.buttons.duplicate(true)
	for b in _cp_buttons.get_children():
		_cp_buttons.remove_child(b)
		b.queue_free()
	for spec in st.buttons:
		var action: String = spec[2]
		_cp_buttons.add_child(_button(spec[0], spec[1], func(): _on_connect_action(action)))


## The run Back to the last run goes to: the one the composer was opened over, else the one shown before the connect
## page was asked for (null when none, or it is gone).
func back_run():
	var r = shown if shown != null else _last_run
	return r if r != null and runs.has(r) else null


## The connect card's buttons as shown now (for the checks).
func connect_buttons() -> Array:
	return _cp_buttons.get_children().map(func(b): return b.text)


## The Memory line: how many runs the agent service remembers of this game (its memory_state), "empty", or nothing from
## one that doesn't say.
func _save_text() -> String:
	if not bridge.memory_known:
		return ""
	var n: int = bridge.memory_runs
	if n == 0 and not bridge.memory_notes and not bridge.memory_lib:
		return "empty"
	return "%d run%s" % [n, "" if n == 1 else "s"]


## What the agent service said it remembers about this game on the connection open now (its memory_state): "unknown"
## (not connected, or none came on this connection yet), "empty" (no past runs, notes nor code of its own) or "some".
func memory_now() -> String:
	if not bridge.is_connected_to_brain() or not bridge.memory_known or bridge.memory_serial != bridge.connection_serial:
		return "unknown"
	if bridge.memory_runs == 0 and not bridge.memory_notes and not bridge.memory_lib:
		return "empty"
	return "some"


## Rerun setup (the Settings page's): the first-run setup from its first step (not while a run is going: switching
## accounts reconnects to another agent service, and the run going on would lose its connection).
func rerun_setup() -> void:
	if active != null:
		_set_status(RUN_GOING)
		return
	_open_setup()
	_render()


func _render_footer() -> void:
	var saved := _save_text()
	_save_label.text = ("· " + saved) if saved != "" else ""
	# Reset memory: asked in place, then how it went; disabled while a run goes (or resetting)
	if _reset == "ask" and (active != null or not bridge.is_connected_to_brain()):
		_reset = ""
	var asking := _reset == "ask"
	_mem_ask.visible = asking
	_mem_bar.visible = not asking
	_footer.add_theme_stylebox_override("panel", HT.edge_box(HT.RAIL, HT.RULE, "top", 1,
		Vector4(10, 10, 10, 10) if asking else (Vector4(14, 9, 14, 9) if wide else Vector4(12, 8, 12, 8))))
	_reset_btn.disabled = _reset == "working"
	HT.grey(_reset_btn, active != null or not bridge.is_connected_to_brain())  # (pressed: the status line says why)
	_reset_btn.tooltip_text = RESET_BUSY if active != null else RESET_TIP
	_reset_line.visible = _reset in ["working", "done", "failed"]
	_reset_spin.visible = _reset == "working"
	match _reset:
		"working":
			_reset_text.text = "Resetting memory…"
			_reset_text.add_theme_color_override("font_color", HT.TEXT3)
		"done":
			_reset_text.text = "Memory reset successful"
			_reset_text.add_theme_color_override("font_color", HT.SUCCESS)
		"failed":
			_reset_text.text = "Memory not reset: %s" % _reset_why
			_reset_text.add_theme_color_override("font_color", HT.RED)
	# (the composer's foot: the composer shows only while connected)
	_link_url.text = str(bridge.link.url) if _conn == "connected" else ""
	_link_label.text = "● CONNECTED"
	_link_label.add_theme_color_override("font_color", HT.SUCCESS)
	_link_btn.text = "Disconnect"


func _render_history() -> void:
	_live_meta = null
	for c in _rail_list.get_children():
		_rail_list.remove_child(c)
		c.queue_free()
	var odd := false
	for i in range(runs.size() - 1, -1, -1):
		_rail_list.add_child(_history_row(runs[i], odd))
		odd = not odd
	if runs.is_empty():
		var e := _label("No runs yet.", "AutopilotSmall")
		e.add_theme_color_override("font_color", HT.MUTED)
		_rail_list.add_child(_margin(e, 14, 10, 14, 10))
	# the history button (narrow)
	for c in _hist_tag_slot.get_children():
		_hist_tag_slot.remove_child(c)
		c.queue_free()
	var r = shown if shown != null else (runs[-1] if not runs.is_empty() else null)
	_hist_btn.disabled = runs.is_empty()
	if r == null or composing:
		_hist_tag_slot.add_child(HT.tag("New", "mut"))
		_hist_meta.text = ("%d run%s" % [runs.size(), "" if runs.size() == 1 else "s"]) if not runs.is_empty() else "No runs yet"
	else:
		_hist_tag_slot.add_child(_verdict_tag(r))
		_hist_meta.text = _row_meta(r)
	_hist_btn.modulate = Color(1, 1, 1, 0.6 if runs.is_empty() else 1.0)
	if _hist_menu.visible:
		_fill_history_menu()


## A run's verdict as a tag, in the verdict's colors (the same whether its row is selected or not).
func _verdict_tag(r) -> PanelContainer:
	var v: String = r.verdict()
	return HT.tag("Live" if v == "LIVE" else v, HT.verdict_colors(v)[2])


## A run in the history: its verdict tag and line, then its prompt. Selected: ROW_ACTIVE with a 2 px accent rule at
## its left (its text as usual); hovered, HOVER; else the rail's ground and GROUND in turn (odd). In the narrow
## history list (popup) the rows sit on the popup's own ground.
func _history_row(r, odd := false, popup := false) -> Control:
	var sel: bool = r == shown and not composing
	var p := PanelContainer.new()
	var m := Vector4(10, 9, 12, 9) if popup else Vector4(12, 9, 14, 9)
	var rest := HT.edge_box(Color.TRANSPARENT, Color.TRANSPARENT, "left", 2, m) if popup else HT.row_box("", odd, m)
	p.add_theme_stylebox_override("panel", HT.row_box("selected", odd, m) if sel else rest)
	p.mouse_filter = Control.MOUSE_FILTER_STOP
	p.mouse_default_cursor_shape = Control.CURSOR_POINTING_HAND
	p.gui_input.connect(func(e: InputEvent) -> void:
		if e is InputEventMouseButton and e.pressed and e.button_index == MOUSE_BUTTON_LEFT:
			if popup:
				_hist_menu.hide()
			_select_run(r))
	if not sel:
		p.mouse_entered.connect(func(): p.add_theme_stylebox_override("panel", HT.row_box("hover", odd, m)))
		p.mouse_exited.connect(func(): p.add_theme_stylebox_override("panel", rest))
	var v := VBoxContainer.new()
	v.add_theme_constant_override("separation", HT.px(3))
	v.mouse_filter = Control.MOUSE_FILTER_IGNORE
	p.add_child(v)
	var h := HBoxContainer.new()
	h.add_theme_constant_override("separation", HT.px(8))
	h.mouse_filter = Control.MOUSE_FILTER_IGNORE
	v.add_child(h)
	h.add_child(_verdict_tag(r))
	var meta := _label(_row_meta(r), "AutopilotMonoDim", false)
	meta.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	meta.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	meta.clip_text = true  # (cut short in the rail's width, never widening it)
	meta.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	meta.mouse_filter = Control.MOUSE_FILTER_IGNORE
	if r.live() and not popup:
		_live_meta = meta
	h.add_child(meta)
	# (the prompt wraps, cut at 140 characters)
	var words: String = r.shown_prompt()
	var t := _label(words if words.length() <= 140 else words.substr(0, 139).strip_edges() + "…", "AutopilotBody")
	t.add_theme_font_size_override("font_size", HT.px(12.5))
	if sel:
		t.add_theme_color_override("font_color", HT.TEXT)
	t.mouse_filter = Control.MOUSE_FILTER_IGNORE
	v.add_child(t)
	return p


## A run's line in the history: "Oct 19 13:25 · 03:26 · 2 attempts" (how long it took, its harness attempts).
func _row_meta(r) -> String:
	return "%s · %s · %s" % [Model.when(r.started_unix), Model.clock(r.took() if not r.live() else r.elapsed()),
		Model.attempts_text(r.attempt_count())]


## The narrow dock's history list (the History button): the runs, newest first, then the Memory row; under the
## button, as wide as the top bar's inside.
func _open_history_menu() -> void:
	_fill_history_menu()
	var top: Control = _top.get_parent()
	var at := top.get_screen_position() + Vector2(HT.px(10), _hist_btn.get_global_rect().end.y - top.get_global_rect().position.y + HT.px(8))
	var w := int(top.size.x - HT.px(20))
	var rows := mini(runs.size(), 8)
	var h := 0
	_hist_menu.reset_size()
	for c in _hist_list.get_children():
		if rows <= 0:
			break
		h += int((c as Control).get_combined_minimum_size().y)
		rows -= 1
	_hist_list.get_parent().custom_minimum_size = Vector2(0, h)
	_hist_menu.popup(Rect2i(Vector2i(at), Vector2i(w, 0)))


func _fill_history_menu() -> void:
	for c in _hist_list.get_children():
		_hist_list.remove_child(c)
		c.queue_free()
	for i in range(runs.size() - 1, -1, -1):
		_hist_list.add_child(_history_row(runs[i], false, true))


## The runs in the narrow history list, as shown (for the checks): each row's prompt.
func history_menu_prompts() -> Array:
	var out := []
	for row in _hist_list.get_children():
		var v: Node = row.get_child(0)
		out.append((v.get_child(1) as Label).text)
	return out


func _select_run(r) -> void:
	if r == null and shown != null:
		_last_run = shown  # (Back to the last run, from the connect page)
	shown = r
	composing = false
	settings_open = false
	tab = "main"
	_render()


func _set_tab(key: String) -> void:
	tab = key
	_render_run()


## The composer as its words are now: Run once there are words (connected, no run going). No kind is read from them
## here: the agent service reads it once the run starts.
func _render_composer() -> void:
	if _c_run == null:
		return
	var text := _c_prompt.text.strip_edges()
	_c_run.disabled = text == ""  # (no words: nothing to run)
	HT.grey(_c_run, text != "" and (active != null or not bridge.is_connected_to_brain()))  # (pressed: the status line says why)
	_c_note.text = RUN_NOTE if active != null else ""
	_c_note.visible = _c_note.text != ""
	var credits := out_of_credits()
	_c_credits.visible = not credits.is_empty()
	_c_credits_text.text = str(credits.get("message", ""))
	_c_credits_url = str(credits.get("url", ""))
	_c_credits_btn.visible = _c_credits_url != ""


## Aura's out-of-credits notice from the agent service's last account frame (ui/usage.gd parse_out_of_credits(): {}
## while the team has credits, when not with Aura, or from an older agent service).
func out_of_credits() -> Dictionary:
	if bridge.account.get("mode") != "aura":
		return {}
	return Usage.parse_out_of_credits(bridge.account.get("out_of_credits"))


# --- the run page -------------------------------------------------------------------------------------

func _render_run() -> void:
	var r = shown
	if r == null:
		return
	var has_steps: bool = not r.steps.is_empty()
	if tab == "policy" and not has_steps:
		tab = "main"
	_tab_main.text = "Live" if r.live() else "Result"
	_tab_feed.text = "Feed"
	_tab_policy.text = "Policy log · %d" % r.steps.size()
	_tab_policy.visible = has_steps
	_tab_link.text = _tab_meta(r)
	_tab_local.visible = wide and r.live() and local_agent()
	var btn: Button = {"main": _tab_main, "feed": _tab_feed, "policy": _tab_policy}[tab]
	if not btn.button_pressed:
		btn.set_pressed_no_signal(true)
	_live.visible = tab == "main" and r.live()
	_result.visible = tab == "main" and not r.live()
	_feed_area.visible = tab == "feed"
	_policy.visible = tab == "policy"
	# the steer box: the Live page's foot, or the Feed tab's (the same box, so the same words and state in both)
	_move_to(_l_steer_row, _feed_steer_slot if tab == "feed" else _live)
	_feed_steer_slot.visible = tab == "feed"
	if tab == "feed":
		_l_steer_row.visible = _can_steer_shown()
		_update_steer_box()
	if _live.visible:
		_render_live()
	elif _result.visible:
		_render_result()
	elif tab == "feed":
		_render_feed()
	elif tab == "policy":
		if _policy.run != r:
			_policy.set_run(r)


## Whether the agent service connected is on this computer (a loopback address).
func local_agent() -> bool:
	if not bridge.is_connected_to_brain():
		return false
	var host := str(bridge.link.url).trim_prefix("ws://").trim_prefix("wss://").get_slice("/", 0)
	host = host.substr(0, host.rfind(":")) if host.count(":") == 1 else host.trim_prefix("[").get_slice("]", 0)
	return host in ["127.0.0.1", "localhost", "::1"] or host.begins_with("127.")


## The tab bar's line for the run (wide): "RUN 20261020-132501 // GOAL // DURATION 03:26 // 2 ATTEMPTS".
func _tab_meta(r) -> String:
	return ("RUN %s // %s // DURATION %s // %s" % [r.id, r.kind_word(), Model.clock(r.took() if not r.live() else r.elapsed()),
		Model.attempts_text(r.attempt_count())]).to_upper()


func _render_live() -> void:
	var r = shown
	_l_prompt.text = r.shown_prompt()
	_l_prompt.tooltip_text = r.shown_prompt()
	_l_origin.text = r.origin_line()
	_l_origin.visible = _l_origin.text != ""
	# (Stop only while connected, for the run going on, not already stopping)
	_l_stop.disabled = r.stop_asked or active != r or not bridge.is_connected_to_brain()
	_l_stop.text = "Stopping…" if r.stop_asked else "Stop"
	_l_steer_row.visible = _can_steer_shown()
	_update_steer_box()
	_update_live_clock()
	_l_now_title.text = ("NOW · HARNESS %d" % r.attempts) if r.attempts > 0 else "NOW"
	_l_now.text = r.now_text if r.now_text != "" else _latest_line(r)
	# (the harnesses before the current one finished; the current one goes on while the run does)
	_l_squares.set_attempts(r.attempts - 1 if r.live() and r.attempts > 0 else r.attempts, r.live() and r.attempts > 0)
	_l_steps.text = ("%d policy step%s" % [r.steps.size(), "" if r.steps.size() == 1 else "s"]) if not r.steps.is_empty() else ""
	_render_live_feed()


func _latest_line(r) -> String:
	for i in range(r.feed.size() - 1, -1, -1):
		if r.feed[i].kind != "engine":
			return r.feed[i].text
	return "Starting…"


func _update_live_clock() -> void:
	var r = shown
	if r == null:
		return
	_l_elapsed.text = Model.clock(r.elapsed())
	# (Elapsed hidden when the run's area is narrower than 520 px)
	var roomy: bool = _live.size.x <= 0.0 or _live.size.x >= HT.px(520)
	_l_elapsed.visible = roomy
	_l_elapsed_cap.visible = roomy
	_l_track.set_state(r.stage, r.attempts, r.stage_times(), not r.live())


## The stage lines of the feed (TEXT; other status lines in row text).
const STAGE_LINES := ["Launching the environment", "Analyzing the project", "Probing the environment", "Building the harness",
	"Processing results", "Saving what it learned to memory"]


## A feed line (Feed board): its time (m:ss, muted, in a column of its own), then its text in mono: status lines in row
## text, stage and result lines in TEXT, a harness line in TEXT at 500 (the current one in the accent), Now lines in
## TEXT2, the user's steer in TEXT at 500; engine, agent and memory lines after their tag (ENGINE in its severity's
## colors, AGENT red, MEMORY green; RichTextLabel draws no border round it).
func _feed_line(rtl: RichTextLabel, e: Dictionary, _bright := false, current := false) -> void:
	rtl.push_font(HT.font("mono"), HT.px(11.5))
	rtl.push_color(HT.MUTED)
	rtl.add_text(Model.short_clock(float(e.get("t", 0.0))).rpad(7))
	rtl.pop()
	if str(e.get("kind", "")) == "activity":
		# An activity event: its kind's icon (12 px, in its kind's colour, its title as the tooltip), its words in TEXT,
		# then a short one-line detail the words don't say (a file's path) and its result, muted, on the same line. Its
		# folds are the Feed tab's rows' (_fold), not here
		var akind := str(e.get("akind", ""))
		var icon := _activity_icon(akind)
		if icon != null:
			rtl.add_image(icon, HT.px(12), HT.px(12), _mark_color(akind), INLINE_ALIGNMENT_CENTER, Rect2(), null, false, Model.activity_title(akind))
			rtl.add_text(" ")
		else:
			rtl.push_font(HT.font("mono_bold"), HT.px(11.5))
			rtl.push_color(_mark_color(akind))
			rtl.add_text(Model.activity_glyph(akind).rpad(2))
			rtl.pop()
			rtl.pop()
		rtl.push_color(HT.TEXT)
		rtl.add_text(str(e.get("text", "")))
		rtl.pop()
		var detail := str(e.get("detail", ""))
		var quiet := ""
		if detail != "" and not Model.folds(detail) and not str(e.get("text", "")).contains(detail):
			quiet += "  " + detail
		if str(e.get("result", "")) != "":
			quiet += "  " + str(e.get("result", ""))
		if quiet != "":
			rtl.push_color(HT.MUTED)
			rtl.add_text(quiet)
			rtl.pop()
		rtl.pop()
		rtl.newline()
		return
	var text := str(e.text)
	var color := HT.TEXT3
	var bold := false
	match e.kind:
		"attempt":
			color = HT.ACCENT if current else HT.TEXT
			bold = true
		"engine":
			_feed_tag(rtl, "Engine", str(e.get("tone", "warn")))
			color = HT.TEXT2
		"agent":
			_feed_tag(rtl, "Agent", "bad")
			color = HT.TEXT2
		"memory":
			# (4 px above it: a transparent strut taller than the line)
			rtl.add_image(_strut(), 1, HT.font("mono").get_height(HT.px(11.5)) + HT.px(4), Color.WHITE, INLINE_ALIGNMENT_BOTTOM)
			_feed_tag(rtl, "Memory", "ok")
			color = HT.SUCCESS
		"info":
			color = HT.TEXT
		"now":
			color = HT.TEXT2
		_:
			if text.begins_with("You: "):
				color = HT.TEXT
				bold = true
			elif STAGE_LINES.has(text):
				color = HT.TEXT
	if bold:
		rtl.push_font(HT.font("mono_bold"), HT.px(11.5))
	rtl.push_color(color)
	rtl.add_text(text)
	rtl.pop()
	if bold:
		rtl.pop()
	rtl.pop()
	rtl.newline()


static var _strut_tex: Texture2D = null


static func _strut() -> Texture2D:
	if _strut_tex == null:
		_strut_tex = ImageTexture.create_from_image(Image.create_empty(1, 1, false, Image.FORMAT_RGBA8))
	return _strut_tex


## A tag inside a feed line: upper case mono 500 on its kind's surface, in its color.
func _feed_tag(rtl: RichTextLabel, word: String, kind: String) -> void:
	var c: Array = HT.TAG_COLORS.get(kind, HT.TAG_COLORS.mut)
	rtl.push_font(HT.font("tag"), HT.px(10))
	rtl.push_bgcolor(c[0])
	rtl.push_color(c[1])
	rtl.add_text(" %s " % word.to_upper())
	rtl.pop()
	rtl.pop()
	rtl.pop()
	rtl.add_text("  ")


## The index of the run's latest harness line while it goes on (-1: none, or the run is over).
func _current_attempt_line(r) -> int:
	if not r.live():
		return -1
	for i in range(r.feed.size() - 1, -1, -1):
		if r.feed[i].kind == "attempt":
			return i
	return -1


func _render_live_feed() -> void:
	var r = shown
	_l_feed.clear()
	var cur := _current_attempt_line(r)
	var start: int = max(0, r.feed.size() - FEED_TAIL)
	for i in range(start, r.feed.size()):
		_feed_line(_l_feed, r.feed[i], i == r.feed.size() - 1, i == cur)


## The Feed tab drawn whole: the run as a transcript, a heading per stretch of one stage's lines (its name and its count),
## a row per line (_append_feed_row), the folds of the activity lines closed unless opened before.
func _render_feed() -> void:
	var r = shown
	for c in _feed_rows.get_children():
		_feed_rows.remove_child(c)
		c.free()
	_feed_by_id = {}
	_feed_group = -1
	_feed_heading = null
	_feed_updated_id = 0
	_feed_empty.visible = r.feed.is_empty()
	_feed_box.visible = not r.feed.is_empty()
	var cur := _current_attempt_line(r)
	for i in r.feed.size():
		_append_feed_row(r.feed[i], i == cur)
	_feed_count = r.feed.size()


## The colour of an activity line's mark, by the event's kind.
## The editor's own icons that mean the same as the feed's (Godot 4.7's EditorIcons): a magnifying glass, a pencil, a
## circular arrow, an eye, a gamepad. The rest (a robot, binoculars, a flask, a speech bubble, a flag, a megaphone, a notebook) are
## the theme's glyphs, as is everything outside the editor (the checks).
const EDITOR_ICONS := {"magnifier": "Search", "pencil": "Edit", "circular-arrow": "Reload", "eye": "GuiVisibilityVisible", "gamepad": "Joypad"}


## An activity kind's icon, white (the line tints it): drawn once per name; null for a kind without one.
func _activity_icon(akind: String) -> Texture2D:
	var name := Model.activity_icon(akind)
	if name == "":
		return null
	if _activity_icons.has(name):
		return _activity_icons[name]
	var tex: Texture2D = null
	var ei = _ei()
	if ei != null and EDITOR_ICONS.has(name):
		var theme: Theme = ei.get_editor_theme()
		if theme != null and theme.has_icon(EDITOR_ICONS[name], "EditorIcons"):
			tex = theme.get_icon(EDITOR_ICONS[name], "EditorIcons")
	if tex == null and HT.GLYPHS.has(name):
		tex = HT.glyph(name, 12.0, Color.WHITE)
	_activity_icons[name] = tex
	return tex


func _mark_color(akind: String) -> Color:
	match akind:
		"alert": return HT.RED
		"report": return HT.SUCCESS
		"probe", "episode": return HT.ACCENT
		"write", "refine": return HT.INFO
		"look": return HT.ACTION_COLORS[4]  # (violet)
		"restart": return HT.AMBER
		"text", "steer": return HT.TEXT2
	return HT.MUTED


## A line added to the Feed tab's rows: a heading first when its stage begins a new stretch (the heading's count grows
## with each line under it), then the row itself (an activity line's remembered by its id, for its result).
func _append_feed_row(e: Dictionary, current := false) -> void:
	var stage := int(e.get("stage", -1))
	if stage >= 0 and stage != _feed_group:
		_feed_group = stage
		_feed_heading = _feed_heading_row(stage)
		_feed_rows.add_child(_feed_heading)
	if _feed_heading != null:
		var count: Label = _feed_heading.get_meta("count")
		count.text = str(int(count.text) + 1)
	var row := VBoxContainer.new()
	row.add_theme_constant_override("separation", 0)
	row.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_fill_feed_row(row, e, current)
	_feed_rows.add_child(row)
	if e.has("aid"):
		_feed_by_id[int(e.aid)] = row


## A stage's heading: its name (mono caps, TEXT2), its count of lines (mono 10, muted), a rule to the right edge.
func _feed_heading_row(stage: int) -> Control:
	var h := HBoxContainer.new()
	h.add_theme_constant_override("separation", HT.px(8))
	var name := _caps(Model.STAGE_HEADINGS[stage] if stage < Model.STAGE_HEADINGS.size() else "", HT.TEXT2)
	h.add_child(name)
	var count := _label("0", "AutopilotMonoDim", false)
	count.add_theme_font_size_override("font_size", HT.px(10))
	h.add_child(count)
	var rule := HT.rule()
	rule.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	rule.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	h.add_child(rule)
	var m := _margin(h, 0, 14, 0, 4)
	m.set_meta("count", count)
	return m


## A row's content: the line as the Live plate draws it (_feed_line, in a RichTextLabel of its own), and under an
## activity line a fold for each of its details (the call's, the result's).
func _fill_feed_row(row: VBoxContainer, e: Dictionary, current := false) -> void:
	for c in row.get_children():
		row.remove_child(c)
		c.free()
	var rtl := RichTextLabel.new()
	rtl.fit_content = true
	rtl.scroll_active = false
	rtl.selection_enabled = true
	rtl.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	rtl.add_theme_constant_override("line_separation", HT.px(10))
	_feed_line(rtl, e, false, current)
	row.add_child(rtl)
	if str(e.get("kind", "")) != "activity":
		return
	var dl := Model.detail_label(e)
	if dl != "":
		row.add_child(_fold(e, dl, str(e.get("detail", "")), false))
	var rl := Model.result_label(e)
	if rl != "":
		row.add_child(_fold(e, rl, str(e.get("rdetail", "")), true))


## A fold under an activity line: "+ inspect the code" (mono 10, muted; "– hide the code" while open) and, open, the
## detail in a read-only mono text box on the well, at most 320 px tall (it scrolls past that), its text selectable.
func _fold(e: Dictionary, label: String, detail: String, result: bool) -> Control:
	var key := "%d:%s" % [int(e.get("aid", 0)), "r" if result else "c"]
	var v := VBoxContainer.new()
	v.add_theme_constant_override("separation", HT.px(4))
	var toggle := Button.new()
	toggle.flat = true
	toggle.focus_mode = Control.FOCUS_NONE
	toggle.alignment = HORIZONTAL_ALIGNMENT_LEFT
	toggle.size_flags_horizontal = Control.SIZE_SHRINK_BEGIN
	toggle.add_theme_font_override("font", HT.font("mono"))
	toggle.add_theme_font_size_override("font_size", HT.px(10))
	toggle.add_theme_color_override("font_color", HT.MUTED)
	toggle.add_theme_color_override("font_hover_color", HT.TEXT)
	var te := TextEdit.new()
	te.editable = false
	te.text = detail
	te.wrap_mode = TextEdit.LINE_WRAPPING_BOUNDARY
	te.scroll_fit_content_height = false
	te.add_theme_font_override("font", HT.font("mono"))
	te.add_theme_font_size_override("font_size", HT.px(11))
	te.add_theme_color_override("font_color", HT.TEXT2)
	te.add_theme_color_override("font_readonly_color", HT.TEXT2)
	te.add_theme_stylebox_override("normal", HT.box(HT.WELL, 4.0, HT.LINE, 1, Vector4(10, 8, 10, 8)))
	te.add_theme_stylebox_override("read_only", HT.box(HT.WELL, 4.0, HT.LINE, 1, Vector4(10, 8, 10, 8)))
	var line_h: float = HT.font("mono").get_height(HT.px(11)) + 4
	te.custom_minimum_size = Vector2(0, clampf(te.get_line_count() * line_h + HT.px(16), line_h + HT.px(16), HT.px(320)))
	te.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	var indent := _margin(te, 44, 0, 0, 2)
	var open: bool = _open_folds.get(key, false)
	toggle.text = Model.fold_label(label, open)
	indent.visible = open
	toggle.pressed.connect(func() -> void:
		var now: bool = not _open_folds.get(key, false)
		_open_folds[key] = now
		toggle.text = Model.fold_label(label, now)
		indent.visible = now)
	v.add_child(_margin(toggle, 44, 0, 0, 0))
	v.add_child(indent)
	return v


## An activity line's row drawn again (its result came: the words after its own, and the result's fold).
func _refresh_feed_row(aid: int) -> void:
	if not _feed_by_id.has(aid) or shown == null:
		return
	var e: Dictionary = shown.find_activity(aid)
	if not e.is_empty():
		_fill_feed_row(_feed_by_id[aid], e, false)


## An evidence line's leading time ("H1 12.4 s", "H2 3:20"), shown in the accent.
static var _evidence_at := RegEx.create_from_string("^(H\\d+\\s+\\d[\\d:.]*(\\s*s)?)\\s+(.*)$")


func _render_result() -> void:
	var r = shown
	if is_instance_valid(_r_player) and _r_player.get_parent() != null:
		_r_player.get_parent().remove_child(_r_player)  # (kept: the same run's recording goes on where it was)
	for c in _result.get_children():
		_result.remove_child(c)
		c.queue_free()
	var v := VBoxContainer.new()
	v.add_theme_constant_override("separation", HT.px(14 if wide else 12))
	var pad := _result_pad()
	_result.add_child(_column(v, Vector4(pad, pad, pad, pad), 100000.0))
	var cw := _result_width()

	# the run's id, its actions (icons, named by their tooltips) at the right of the same row at every width
	var idrow := HBoxContainer.new()
	idrow.add_theme_constant_override("separation", HT.px(6))
	var idl := _label(("RUN %s // %s // %s" % [r.id, r.kind_word(), Model.when(r.started_unix)]).to_upper(), "AutopilotCaps", false)
	idl.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	idl.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	idl.clip_text = true  # (cut short, never wrapped: the icons stay on its row)
	idl.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	idrow.add_child(idl)
	v.add_child(idrow)
	var again := _icon_button("retry", "Run again", "AutopilotPrimary", func(): _run_again(r))
	HT.grey(again, active != null or not bridge.is_connected_to_brain())  # (pressed: the status line says why)
	idrow.add_child(again)
	idrow.add_child(_icon_button("clipboard", "Copy report", "Button", func():
		DisplayServer.clipboard_set(Model.report_markdown(r))
		_set_status("Copied the report")))
	var folder := _icon_button("folder", "Run folder", "Button", func(): OS.shell_open(r.report_dir))
	folder.disabled = r.report_dir == "" or not DirAccess.dir_exists_absolute(r.report_dir)
	idrow.add_child(folder)

	var title := _label(r.shown_prompt(), "AutopilotTitle")
	v.add_child(title)
	if r.origin_line() != "":
		v.add_child(_label(r.origin_line(), "AutopilotSmall"))
	# what the user must know to trust the outcome or to get a better one, as the brain words it, before the verdict
	_r_alerts.clear()
	for a in r.alerts():
		var al := _label(Model.scrub(a), "AutopilotStrong")
		al.add_theme_color_override("font_color", HT.AMBER_FG)
		_r_alerts.append(al)
		v.add_child(_panel(al, HT.box(HT.AMBER_FILL, 0, HT.AMBER_LINE, 1, Vector4(11, 9, 11, 9))))
	var verdict: String = r.verdict()
	# the verdict above the reason: one left edge, the reason as wide as the page
	var banner = W.Banner.new()
	_r_banner = banner
	banner.set_verdict(verdict, HT.verdict_colors(verdict), cw)
	var reason := _label(Model.scrub(r.reason()) if r.reason() != "" else _no_reason(r))
	var vr := VBoxContainer.new()
	vr.add_theme_constant_override("separation", HT.px(8))
	vr.add_child(banner)
	vr.add_child(reason)
	v.add_child(vr)

	# the run's recording; without one, why not (when known: Model.Run.no_recording_line), else nothing
	var player = _player_for(r)
	_r_no_rec = null
	if player != null:
		player.screen.set_height(230.0 if wide else 200.0)
		v.add_child(player)
		if player.count == 0:
			# (it had one, but its frames aren't where they were: the player kept, its buttons and Run folder disabled)
			folder.disabled = true
			var gone := _label("The recording isn’t there any more.", "AutopilotSmall")
			gone.add_theme_color_override("font_color", HT.AMBER)
			v.add_child(gone)
	elif r.no_recording_line() != "":
		_r_no_rec = _label(r.no_recording_line(), "AutopilotSmall")
		_r_no_rec.add_theme_color_override("font_color", HT.MUTED)
		v.add_child(_r_no_rec)

	var ev: Array = r.evidence()
	if not ev.is_empty():
		var eb := VBoxContainer.new()
		eb.add_theme_constant_override("separation", HT.px(6))
		eb.add_child(_caps("Evidence"))
		var rtl := _code_box(eb)
		for i in ev.size():
			if i > 0:
				rtl.newline()
			var line := Model.scrub(ev[i])
			var m := _evidence_at.search(line)
			if m:
				rtl.push_color(HT.ACCENT)
				rtl.add_text(m.get_string(1))
				rtl.pop()
				rtl.add_text("  " + m.get_string(3))
			else:
				rtl.add_text(line)
		rtl.pop()
		rtl.pop()
		v.add_child(eb)

	# What the agent changed in the game directly (a verification's setup): the result was played on a changed game
	var ch: Array = r.changes()
	if not ch.is_empty():
		var cb := VBoxContainer.new()
		cb.add_theme_constant_override("separation", HT.px(6))
		cb.add_child(_caps(("Changed directly · not through play · %d" if wide else "Changed directly · %d") % ch.size(), HT.AMBER))
		var crtl := _code_box(cb)
		for i in ch.size():
			if i > 0:
				crtl.newline()
			crtl.push_color(HT.AMBER)
			crtl.add_text("! ")
			crtl.pop()
			crtl.add_text(Model.scrub(ch[i]))
		crtl.pop()
		crtl.pop()
		v.add_child(cb)

	var agent: Array = r.agent_findings()
	var engine: Array = r.engine_findings()
	if not agent.is_empty() or not engine.is_empty():
		var fv := VBoxContainer.new()
		fv.add_theme_constant_override("separation", HT.px(8))
		v.add_child(fv)
		for a in agent:
			fv.add_child(_agent_card(Model.scrub(a)))
		if not engine.is_empty():
			fv.add_child(_caps("Engine findings · %d" % engine.size()))
			for f in engine:
				fv.add_child(_engine_card(r, f))


## A code box (evidence, what was changed): the well plate, mono (11.5; 11 when narrow) in TEXT2, lines 4 apart; the
## RichTextLabel returned with its font and color pushed (pop both).
func _code_box(parent: Control) -> RichTextLabel:
	var well := PanelContainer.new()
	well.theme_type_variation = "AutopilotWell"
	var rtl := RichTextLabel.new()
	rtl.fit_content = true
	rtl.scroll_active = false
	rtl.selection_enabled = true
	rtl.add_theme_constant_override("line_separation", HT.px(4))
	rtl.push_font(HT.font("mono"), HT.px(11.5 if wide else 11))
	rtl.push_color(HT.TEXT2)
	well.add_child(rtl)
	parent.add_child(well)
	return rtl


## The recording's player for the run (the one already made when it plays the same run), or null when the run has no
## recording (or none of its frames are there).
func _player_for(r):
	if is_instance_valid(_r_player) and _r_player_run == r:
		return _r_player
	if is_instance_valid(_r_player):
		_r_player.queue_free()
	_r_player = null
	_r_player_run = null
	var rec: Dictionary = r.recording()
	if rec.is_empty():
		return null
	var p := W.Flipbook.new()
	if not p.open(rec):
		p.set_gone()  # (its frames aren't there any more)
	# (File Explorer and Finder select the MP4; the frames' folder, when there is no MP4, is opened)
	p.opened.connect(func(path: String) -> void: OS.shell_show_in_file_manager(path))
	_r_player = p
	_r_player_run = r
	return p


## The recording copied into the run's own folder (recording/, off the editor's thread), so that it stays with the run
## (a Reset memory doesn't take it); until then, and if copying fails, it plays from where the agent service wrote it.
func _copy_recording(r, dir: String) -> void:
	var rec: Dictionary = r.recording()
	if rec.is_empty() or dir == "" or not DirAccess.dir_exists_absolute(rec.frames):
		return
	var src: String = rec.frames
	var dst := dir.path_join("recording")
	var out := [false]
	var task := WorkerThreadPool.add_task(func() -> void: out[0] = copy_frames(src, dst), false, "Autopilot recording copy")
	_copies.append({"run": r, "task": task, "out": out, "dst": dst, "dir": dir})


## Copy the JPEG frames of a recording from src to dst (made when missing); whether every frame was copied.
static func copy_frames(src: String, dst: String) -> bool:
	if DirAccess.make_dir_recursive_absolute(dst) != OK:
		return false
	var n := 0
	for name in DirAccess.get_files_at(src):
		if name.get_extension().to_lower() != "jpg":
			continue
		if DirAccess.copy_absolute(src.path_join(name), dst.path_join(name)) != OK:
			return false
		n += 1
	return n > 0


## The copies done: the run plays from its own folder, and its result.json says where (with the frames' fps).
func _poll_copies() -> void:
	var i := 0
	while i < _copies.size():
		var job: Dictionary = _copies[i]
		if not WorkerThreadPool.is_task_completed(job.task):
			i += 1
			continue
		WorkerThreadPool.wait_for_task_completion(job.task)
		_copies.remove_at(i)
		_copy_done(job)


## A copy's end (its task waited for): when every frame was copied, the run plays from it and its result.json says so.
func _copy_done(job: Dictionary) -> void:
	if not job.out[0]:
		return
	var r = job.run
	r.result.recording.frames = job.dst
	_save_result(r, str(job.dir))
	_keep_run(r)
	if is_instance_valid(_r_player) and _r_player_run == r:
		_r_player.relocate(job.dst)


## The run's result written again to its folder's result.json.
static func _save_result(r, dir: String) -> void:
	var f := FileAccess.open(dir.path_join("result.json"), FileAccess.WRITE)
	if f:
		f.store_string(JSON.stringify(r.result, "  "))
		f.close()


func _exit_tree() -> void:
	for job in _copies:
		WorkerThreadPool.wait_for_task_completion(job.task)
		_copy_done(job)  # (the run's result.json still says where its copy is)
	_copies.clear()
	if active != null:
		_keep_run(active)  # (live as the editor closes: it loads with no result)
	_save_history()


## The result's content width now: the page's width less its padding, at most MAX_WIDTH.
## The result page's padding: 16 (12 in a narrow dock).
func _result_pad() -> float:
	return 16.0 if wide else 12.0


## The result's content width now: the page's width less its padding.
func _result_width() -> float:
	var avail := _result.size.x
	if avail <= 0.0:
		avail = size.x - ((_rail.size.x + 1.0) if wide else 0.0)
	return avail - 2 * HT.px(_result_pad())


## The result fitted to the dock's width again (resized): the verdict's size.
func _fit_result() -> void:
	if not _result.is_visible_in_tree() or shown == null or not is_instance_valid(_r_banner):
		return
	var verdict: String = shown.verdict()
	_r_banner.set_verdict(verdict, HT.verdict_colors(verdict), _result_width())


## A finding's severity as a tag's kind: error, critical, fatal, bug red; warning, warn amber; anything else (or none)
## info, blue.
static func severity_kind(severity: String) -> String:
	match severity.strip_edges().to_lower():
		"error", "critical", "fatal", "bug":
			return "bad"
		"warning", "warn":
			return "warn"
	return "info"


func _no_reason(r) -> String:
	match r.verdict():
		"STOPPED": return "Stopped before a result."
		"NO RESULT": return "The connection to the agent service closed before the result came."
	return ""


## A finding's plate (12 × 11, 6 between its lines).
func _finding_plate() -> Array:
	var v := VBoxContainer.new()
	v.add_theme_constant_override("separation", HT.px(6))
	return [_panel(v, HT.plate_box(Vector4(12, 11, 12, 11))), v]


func _agent_card(text: String) -> PanelContainer:
	var pv := _finding_plate()
	var v: VBoxContainer = pv[1]
	var h := HBoxContainer.new()
	h.add_theme_constant_override("separation", HT.px(8))
	h.add_child(HT.tag("Found", "bad"))
	h.add_child(_caps("Agent finding"))
	v.add_child(h)
	v.add_child(_label(text, "AutopilotStrong"))
	var b := _flow(6)
	b.add_child(_button("Verify a fix", "Button", func(): _compose("Verify that this is fixed: " + text), true))
	var copy := func() -> void:
		DisplayServer.clipboard_set(text)
		_set_status("Copied")
	b.add_child(_button("Copy", "AutopilotQuiet", copy, true))
	v.add_child(b)
	return pv[0]


func _engine_card(r, f: Dictionary) -> PanelContainer:
	var pv := _finding_plate()
	var v: VBoxContainer = pv[1]
	var h := HBoxContainer.new()
	h.add_theme_constant_override("separation", HT.px(8))
	var sev := str(f.get("severity", "")).strip_edges()
	h.add_child(HT.tag(sev if sev != "" else "info", severity_kind(sev)))
	var det := _label(str(f.get("detector", f.get("source", "engine"))), "AutopilotMonoDim", false)
	det.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	det.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	det.clip_text = true
	h.add_child(det)
	var count := int(f.get("count", 1))
	if count > 1:
		var cl := _label("×%d" % count, "AutopilotMono", false)
		cl.add_theme_font_override("font", HT.font("mono_bold"))
		cl.add_theme_font_size_override("font_size", HT.px(12))
		cl.add_theme_color_override("font_color", HT.TEXT)
		cl.size_flags_vertical = Control.SIZE_SHRINK_CENTER
		h.add_child(cl)
	v.add_child(h)
	v.add_child(_label(Model.scrub(str(f.get("summary", ""))), "AutopilotStrong"))
	var loc := []
	if typeof(f.get("where")) == TYPE_DICTIONARY:
		loc.append("at " + Model.where_text(f.where) + " cm")
	if str(f.get("actor", "")) != "":
		loc.append(str(f.actor))
	if typeof(f.get("t")) in [TYPE_FLOAT, TYPE_INT]:
		loc.append("t=%.1fs" % float(f.t))
	if not loc.is_empty():
		v.add_child(_label(" · ".join(loc), "AutopilotMonoDim"))
	var b := _flow(6)
	if typeof(f.get("where")) == TYPE_DICTIONARY or str(f.get("actor", "")) != "":
		b.add_child(_button("Show in level", "Button", func(): _show_in_level(f), true))
	var sig := str(f.get("signature", ""))
	if sig != "":
		if r.not_a_bug.has(sig):
			var nb := _label("Marked not a bug", "AutopilotSmall", false)
			nb.add_theme_color_override("font_color", HT.MUTED)
			nb.size_flags_vertical = Control.SIZE_SHRINK_CENTER
			b.add_child(nb)
		else:
			var not_a_bug := func() -> void:
				if not bridge.is_connected_to_brain():
					_set_status("Not connected: connect to send feedback")
					return
				bridge.feedback(sig, "not_a_bug")
				r.not_a_bug[sig] = true
				_keep_run(r, true)
				var name := str(f.get("detector", "")).strip_edges()
				_set_status("Told the agent: %s is not a bug" % (name if name != "" else Model.scrub(str(f.get("summary", "")))))
				_render_run()
			var nab := _button("Not a bug", "AutopilotQuiet", not_a_bug, true)
			HT.grey(nab, not bridge.is_connected_to_brain())  # (pressed: the status line says why)
			b.add_child(nab)
	v.add_child(b)
	return pv[0]


# --- actions ------------------------------------------------------------------------------------------

## A connect card's button (connect_state()'s actions).
func _on_connect_action(action: String) -> void:
	match action:
		"install":
			install_service()
		"retry":
			retry_service()
		"open_log":
			open_log(service.log_path)
		"try_now":
			bridge.link.next_try = 0.0
		"start_aura":
			if not Prefs.launch_aura():
				_set_status("Couldn’t start Aura")
		"get_aura":
			OS.shell_open(Prefs.AURA_DOWNLOAD_URL)
		"stop":
			_disconnect()
		_:
			_connect()


func _connect() -> void:
	if removed():
		return
	var url := _url.text.strip_edges()
	var token := _token.text.strip_edges()
	_set_meta("url", url)
	_set_meta("token", token)
	if _auto_service():
		service.ensure()
		if service.busy():
			# (the agent service starts: connect once it says where it listens)
			_connect_when_ready = true
			if bridge.link.want:
				bridge.link.close()
			_render()
			return
	_connect_when_ready = false
	bridge.connect_to_brain(url, token, _with_aura())
	_render()


func _disconnect() -> void:
	_connect_when_ready = false
	bridge.disconnect_from_brain()
	_render()


func _on_new() -> void:
	_compose("")


## The composer shown with these words in its box ("": what was typed there stays).
func _compose(text: String) -> void:
	composing = true
	settings_open = false
	if text != "" or _c_prompt.text == "":
		_c_prompt.text = text
	_render()
	_c_prompt.grab_focus.call_deferred()


## Run: the words as typed and the Time limit (Model.run_of); the agent service reads which kind of run they ask for and
## says so (run_kind) before it plays. Runs are real time: the game runs on while the agent decides.
func _on_run() -> void:
	var text := _c_prompt.text.strip_edges()
	if text == "":
		return
	var run := Model.run_of(text, int(_c_time_limit.value))
	_start(run, text)


func _run_again(r) -> void:
	if r.request.is_empty():
		_compose(r.prompt)
		return
	_start(r.request.duplicate(true), r.prompt)  # (its own Time limit, as it was sent)


func _start(run: Dictionary, prompt: String) -> void:
	if not bridge.is_connected_to_brain():
		_set_status("Not connected: connect first")
		_render()
		return
	if active != null:
		_set_status("A run is going: wait for its result, or stop it")
		return
	run.erase("turn_based")  # (no turn-based runs any more: a run again of an older request goes real time)
	var r = _new_run(_kind_of_run(run), prompt)
	r.request = run
	composing = false
	bridge.start_run(run)
	_render()


func _new_run(kind: String, prompt: String):
	var r := Model.Run.new()
	r.kind = kind
	r.prompt = prompt
	r.started_unix = Time.get_unix_time_from_system()
	r.started_ms = Time.get_ticks_msec()
	r.id = Time.get_datetime_string_from_system(false, false).replace("-", "").replace(":", "").replace("T", "-")
	runs.append(r)
	active = r
	shown = r
	tab = "main"
	settings_open = false
	_keep_run(r, true)
	return r


func _on_stop() -> void:
	if active:
		active.stop_asked = true
		_keep_run(active)
	bridge.stop_run()
	_render_run()


## The run shown is the live one and the agent service takes the user's words to it.
func _can_steer_shown() -> bool:
	return active != null and shown == active and active.live() and bridge.can_steer()


## ... and its game is playing now (the Rollout stage): the steer box takes words only then.
func _can_steer_now() -> bool:
	return _can_steer_shown() and active.steerable()


## The steer box at the run's stage: the words can be typed at any stage (kept until the game plays, to be sent then);
## the Steer button sends only while the game plays (Rollout), saying when it can be used. Stopping locks both.
func _update_steer_box() -> void:
	var stopping: bool = active != null and active.stop_asked
	var editable := _can_steer_shown() and not stopping
	if _l_steer.editable != editable:
		_l_steer.editable = editable
		if not editable and _l_steer.has_focus():
			_l_steer.release_focus()
	_l_steer.placeholder_text = STEER_HINT
	_l_steer_box.get_parent().modulate = Color(1, 1, 1, 0.55 if stopping else 1.0)  # (the box dimmed once stopping)
	_update_steer_button()


func _update_steer_button() -> void:
	var now: bool = _can_steer_now() and not (active != null and active.stop_asked)
	_l_steer_btn.disabled = not now or _l_steer.text.strip_edges() == ""
	var stopping: bool = active != null and active.stop_asked
	_l_steer_btn.tooltip_text = STEER_WAIT if not _can_steer_now() and not stopping else ""
	_l_steer_btn.mouse_filter = Control.MOUSE_FILTER_PASS  # (its tooltip shows while it is disabled)


## The button beside the box, or under it when the box beside it would be too narrow for its placeholder.
func _fit_steer_row() -> void:
	if _l_steer_box == null:
		return
	var font: Font = _l_steer.get_theme_font("font")
	var need: float = (font.get_string_size(STEER_HINT, HORIZONTAL_ALIGNMENT_LEFT, -1, _l_steer.get_theme_font_size("font_size")).x
		+ _l_steer.get_theme_stylebox("normal").get_minimum_size().x + 2.0)
	# (the room inside the row's padding and the box's: the row's width less both panels' margins)
	var well: Control = _l_steer_box.get_parent()
	var outer := _l_steer_row.get_theme_stylebox("panel").get_minimum_size().x
	var inner := well.get_theme_stylebox("panel").get_minimum_size().x
	var room: float = (_l_steer_row.size.x - outer - inner - _l_steer_box.get_theme_constant("separation")
		- _l_steer_btn.get_combined_minimum_size().x)
	var stack := room < need
	if _l_steer_box.vertical != stack:
		_l_steer_box.vertical = stack


func _on_steer() -> void:
	var said := _l_steer.text.strip_edges()
	if said == "" or not _can_steer_now():
		return
	bridge.steer_run(said)
	active.add_feed("You: %s" % said, "status")
	_l_steer.clear()
	_update_steer_button()
	_live_changed()


## Reset memory…: asks in place (the Memory row's confirmation), when connected and no run is going.
func _on_reset_memory() -> void:
	if not bridge.is_connected_to_brain():
		_set_status("Not connected: connect first to reset the memory")
		return
	if active != null:
		_set_status("A run is going: reset the memory once it is done")
		return
	_reset = "ask"
	_render_footer()


## The confirmation's Reset memory: the agent service asked to forget the game (Resetting memory… until it answers).
func _confirm_reset() -> void:
	if not bridge.is_connected_to_brain():
		_reset = ""
		_set_status("Not connected: connect first to reset the memory")
		_render_footer()
		return
	bridge.clear_memory()
	_reset = "working"
	_render_footer()


## The agent service's answer to Reset memory: done (what it remembers is empty now, and the runs before leave the
## history), or not and why.
func _on_memory_cleared(ok: bool, message: String) -> void:
	_reset = "done" if ok else "failed"
	_reset_why = message
	_reset_until = Time.get_ticks_msec() / 1000.0 + STATUS_FOR
	if ok:
		_set_runs_aside()
	_render_footer()


## The runs ended taken off the history (a run going stays), their files kept aside (ui/history.gd set_aside).
func _set_runs_aside() -> void:
	_save_history()  # (each as it is now)
	var ended := runs.filter(func(r): return not r.live())
	if ended.is_empty():
		return
	History.set_aside(history_dir, ended)
	runs = runs.filter(func(r): return r.live())
	if shown != null and not runs.has(shown):
		shown = null
	if _last_run != null and not runs.has(_last_run):
		_last_run = null
	_render()


# --- show in level ------------------------------------------------------------------------------------

func _show_in_level(f: Dictionary) -> void:
	var ei = _ei()
	if ei == null:
		return
	var root: Node = ei.get_edited_scene_root()
	if root == null:
		_set_status("Open the scene to show this finding in it")
		return
	var target: Node = null
	var actor := str(f.get("actor", ""))
	if actor != "":
		target = root if str(root.name) == actor else root.find_child(actor, true, false)
	var w = f.get("where")
	var flat: bool = root is Node2D or root is Control
	if target == null and typeof(w) == TYPE_DICTIONARY:
		var best := INF
		if flat:
			var p2 := Vector2(float(w.get("X", 0.0)), -float(w.get("Z", 0.0)))
			for n in root.find_children("*", "Node2D", true, true):
				var d: float = n.global_position.distance_squared_to(p2)
				if d < best:
					best = d
					target = n
		else:
			var p3 := Frame.from_kit(w)
			for n in root.find_children("*", "Node3D", true, true):
				var d: float = n.global_position.distance_squared_to(p3)
				if d < best:
					best = d
					target = n
	if target == null:
		_set_status("Nothing in the open scene near that place")
		return
	var sel = ei.get_selection()
	sel.clear()
	sel.add_node(target)
	ei.edit_node(target)
	var is3d := target is Node3D
	ei.set_main_screen_editor("3D" if is3d else "2D")
	_set_status("Showing %s%s" % [target.name, "" if str(target.name) == actor else " (the nearest node to the finding)"])
	await get_tree().process_frame
	await get_tree().process_frame
	_frame_selection(is3d)


## Frames the editor's camera on the selection, as View > Focus Selection (3D) / Frame Selection (2D) does: the
## viewport's View menu item with that shortcut is pressed (found by its shortcut, so whatever the editor's language).
func _frame_selection(is3d: bool) -> void:
	var ei = _ei()
	if ei == null:
		return
	var es = ei.get_editor_settings()
	var key := "spatial_editor/focus_selection" if is3d else "canvas_item_editor/frame_selection"
	if not es.has_shortcut(key):
		return
	var sc: Shortcut = es.get_shortcut(key)
	var n: Node = ei.get_editor_viewport_3d(0) if is3d else ei.get_editor_viewport_2d()
	var owner_class := "Node3DEditorViewport" if is3d else "CanvasItemEditor"
	while n != null and n.get_class() != owner_class:
		n = n.get_parent()
	if n == null:
		return
	var stack: Array = [n]
	while not stack.is_empty():
		var x: Node = stack.pop_back()
		if x is PopupMenu:
			for i in x.item_count:
				if x.get_item_shortcut(i) == sc:
					x.id_pressed.emit(x.get_item_id(i))
					return
		stack.append_array(x.get_children(true))


# --- events from the bridge ---------------------------------------------------------------------------

## A message on the status line (the list in SPEC: what the user's own action did, or why it couldn't): shown until the
## next one, or STATUS_FOR seconds.
func _set_status(text: String) -> void:
	if _status:
		_status.text = text
		_status.tooltip_text = text
		_status_bar.visible = text != ""
		_status_until = Time.get_ticks_msec() / 1000.0 + STATUS_FOR


## The status line hidden once its message is STATUS_FOR seconds old.
func _tick_status() -> void:
	if _status_bar and _status_bar.visible and Time.get_ticks_msec() / 1000.0 >= _status_until:
		_status.text = ""
		_status.tooltip_text = ""
		_status_bar.visible = false


## A status line from the agent service: when no run is going, it may be the run whose connection closed going on.
func _on_server_status(_text: String) -> void:
	if active == null:
		_reopen_lost()


## One event of the run's activity feed (an `activity` frame): a line of the run going (or the one whose connection
## dropped, open again), its words scrubbed; a call's result folded into the call's line (the Feed tab draws that row
## again); nothing for an id seen already.
func _on_activity(frame: Dictionary) -> void:
	if active == null and _reopen_lost() == null:
		return
	var f := frame.duplicate()
	f["text"] = Model.scrub(str(frame.get("text", "")))
	var before: int = active.feed.size()
	var id := int(f.get("id", 0)) if typeof(f.get("id")) in [TYPE_INT, TYPE_FLOAT] else 0
	if not active.on_activity(f):
		if id > 0 and not active.find_activity(id).is_empty():
			# The event came already: the feed replayed after a reconnect. Counted; the log says so once at the start
			# and again, with the count, when the replay moves past what is here
			if _replay_skipped == 0:
				print("Autopilot: a replay of the activity feed is arriving: event %d is shown already" % id)
			_replay_skipped += 1
		return
	if _replay_skipped > 0:
		print("Autopilot: the replay skipped %d events shown already; new events from %d" % [_replay_skipped, id])
		_replay_skipped = 0
	if active.feed.size() == before and typeof(f.get("ends")) in [TYPE_INT, TYPE_FLOAT]:
		_feed_updated_id = int(f.ends)
	_live_changed()


## What the hello asks the activity feed from: the latest run's last event id while it goes on (or lost its connection
## under), else -1 (nothing asked: a finished run's feed is here already).
func _activity_since() -> int:
	var r = active if active != null else (runs[-1] if not runs.is_empty() else null)
	if r != null and (r.live() or r.lost_connection):
		return r.last_activity_id
	return -1


## The latest run, open again when it ended only because the connection closed and the agent service, connected
## again, still sends its events (its status lines, its policy steps, its result): null otherwise. going: false
## when what came is its result (the run is over).
func _reopen_lost(going := true):
	if active != null or runs.is_empty():
		return null
	var r = runs[-1]
	if r.live() or not r.lost_connection:
		return null
	r.lost_connection = false
	r.ended_ms = -1
	r.add_feed("Connected again: the run goes on", "info")
	active = r
	if going:
		bridge.run_active = true  # (stop and steer reach it again)
	_render()
	return r


func _on_status(text: String) -> void:
	if text.begins_with("Run finished. Report: "):
		return  # (the result's own line follows: "Result: …")
	var tr := Model.translate(text)
	# (the agent service's progress lines belong to the Live and Feed pages: never the status line)
	if active and not text.begins_with("Finding: "):
		active.on_status(tr)
		_live_changed()
	elif _connect_page.visible:
		_render_connect_page()  # (connection changes: _process renders the rest)


func _on_finding(f: Dictionary) -> void:
	if active == null:
		return
	var fresh: bool = active.on_finding(f)
	var count := int(f.get("count", 1))
	# (the engine's findings after ENGINE in their severity's colors; the agent's after AGENT, always red)
	var engine := Model.Run.is_engine(f)
	active.add_feed("%s%s" % [Model.scrub(str(f.get("summary", f.get("detector", "")))), (" (×%d)" % count) if count > 1 else ""],
		"engine" if engine else "agent")
	active.feed[-1]["tone"] = severity_kind(str(f.get("severity", ""))) if engine else "bad"
	if fresh or count > 1:
		_live_changed()


func _on_policy_step(frame: Dictionary) -> void:
	if active == null and _reopen_lost() == null:
		return
	var st = active.add_step(frame)
	if shown == active:
		if _policy.run == active:
			_policy.on_step(st)
		_tab_policy.text = "Policy log · %d" % active.steps.size()
		if not _tab_policy.visible:
			_render_run()
		elif _live.visible:
			_l_steps.text = "%d policy step%s" % [active.steps.size(), "" if active.steps.size() == 1 else "s"]


## A run the tab didn't start (the user's own Claude Code or Codex started it): shown as one started from the composer,
## with where it came from under its request. The first-run setup stays where it is while it plays.
func _on_run_started_elsewhere(run: Dictionary) -> void:
	if active != null:
		return
	var r = _new_run(_kind_of_run(run), Model.prompt_of_run(run))
	var request: Dictionary = run.duplicate(true)
	request.erase("by")
	r.request = request  # (Run again starts it from the tab, as any run)
	r.origin = str(run.get("by", "")).strip_edges()
	if r.origin == "":
		r.origin = "agent"
	composing = false
	_render()


## A start frame's kind as its run is shown: none ("") for a run whose kind the agent service reads ("auto"), until its
## run_kind frame says it.
static func _kind_of_run(run: Dictionary) -> String:
	var kind := str(run.get("kind", ""))
	return kind if kind in Model.KINDS else ""


## The run_kind frame: the kind of run the agent service read the words as, shown on the run going from now on.
func _on_run_kind(kind: String, _tested: bool) -> void:
	if active == null or not kind in Model.KINDS:
		return
	active.kind = kind
	_keep_run(active, true)
	_render()


func _on_run_changed(running: bool) -> void:
	if running and active == null:
		var r = _new_run("", "")
		r.prompt = ""
		_render()
	elif not running:
		_end_check.call_deferred()


func _end_check() -> void:
	if active != null and not bridge.run_active:
		active.ended_ms = Time.get_ticks_msec()
		active.lost_connection = active.result.is_empty()  # (the agent service, connected again, may still go on with it)
		if active.lost_connection:
			active.add_feed("Connection closed: no result", "info")
		_keep_usage(active, true)
		_keep_run(active, true)
		active = null
		_render()


func _on_result(result: Dictionary, dir: String) -> void:
	var r = active
	if r == null:
		r = _reopen_lost(false)  # (the run whose connection closed: its result came once connected again)
	if r == null:
		r = _new_run("", str(result.get("claim", result.get("goal", ""))))  # (a run started elsewhere: its result only)
		if _pending_usage != null:
			r.usage = Usage.parse(_pending_usage[0])
			r.usage_final = _pending_usage[1]
		if not _pending_bill.is_empty():
			r.bill = _pending_bill
	_pending_usage = null
	_pending_bill = {}
	r.result = result
	r.report_dir = dir
	if r.prompt == "":
		r.prompt = str(result.get("claim", result.get("goal", "")))
	if r.kind == "":
		r.kind = "verify" if result.has("verdict") else "goal"
	r.enter_stage(Model.STAGE_REPORT)
	r.ended_ms = Time.get_ticks_msec()
	r.add_feed("Result: %s" % Model.sentence(r.verdict()), "info")
	if r.memory_updated():
		r.add_feed(Model.MEMORY_UPDATED, "memory")
	if Model.keep_no_recording(r.result) and dir != "":
		_save_result(r, dir)  # (why it has no recording, kept with it: the report says so again from result.json)
	_copy_recording(r, dir)
	active = null
	if shown == r:
		tab = "main"
	_keep_usage(r, true)
	_keep_run(r, true)
	_render()


func _live_changed() -> void:
	if shown != active or shown == null:
		return
	_tab_feed.text = "Feed"
	if _live.visible:
		_render_live()
	elif _feed_area.visible and _feed_empty.visible:
		_render_feed()  # (its first line: the plate instead of "Nothing yet.")
		_l_steer_row.visible = _can_steer_shown()
		_update_steer_box()
	elif _feed_area.visible:
		# (the new lines appended; a new harness line: drawn whole, so the earlier harness line loses the accent)
		var cur := _current_attempt_line(shown)
		if cur >= _feed_count or shown.feed.size() < _feed_count:
			_render_feed()
		else:
			for i in range(_feed_count, shown.feed.size()):
				_append_feed_row(shown.feed[i], i == cur)
			_feed_count = shown.feed.size()
			if _feed_updated_id > 0:
				_refresh_feed_row(_feed_updated_id)  # (a call's result came: its line says it now)
				_feed_updated_id = 0
		_l_steer_row.visible = _can_steer_shown()
		_update_steer_box()


func _process(delta: float) -> void:
	_tick += delta
	if _tick < 0.25:
		return
	_tick = 0.0
	if bridge == null or _body == null:
		return
	service.poll()
	service.poll_removal()
	_tick_status()
	if _reset in ["done", "failed"] and Time.get_ticks_msec() / 1000.0 >= _reset_until:
		_reset = ""  # (how the reset went: shown STATUS_FOR seconds)
		_render_footer()
	_tick_sign_in()
	_tick_service()
	if not _copies.is_empty():
		_poll_copies()
	var c := _conn_state()
	if c != _conn:
		_render()
	elif c == "unreachable" and _connect_page.visible:
		_render_connect_page()
	_slow += 1
	if _usage_dirty and Time.get_ticks_msec() / 1000.0 - _usage_saved_at >= USAGE_SAVE_EVERY:
		_save_usage()
	if active != null:
		_keep_run(active)  # (its feed, stage and steps go on)
	if not _history_dirty.is_empty() and Time.get_ticks_msec() / 1000.0 - _history_saved_at >= HISTORY_SAVE_EVERY:
		_save_history()
	if active != null:
		if shown == active and _live.visible:
			_update_live_clock()
		if _slow % 4 == 0:
			if is_instance_valid(_live_meta):
				_live_meta.text = _row_meta(active)
			if shown == active:
				_tab_link.text = _tab_meta(active)
			if shown == active and not composing:
				_hist_meta.text = _row_meta(active)


# --- the run history kept ----------------------------------------------------------------------------

## The runs kept from earlier editor sessions (ui/history.gd) put before the ones here (a run begun before they loaded
## stays the newest).
func load_history() -> void:
	runs = History.load_runs(history_dir) + runs


## The run saved in the history: now (now), or within HISTORY_SAVE_EVERY seconds.
func _keep_run(r, now := false) -> void:
	if r == null:
		return
	if not _history_dirty.has(r):
		_history_dirty.append(r)
	if now:
		_save_history()


## The runs changed written to their files (each its own); the files of runs older than the newest History.KEEP_RUNS
## deleted.
func _save_history() -> void:
	_history_saved_at = Time.get_ticks_msec() / 1000.0
	var dirty := _history_dirty
	_history_dirty = []
	if history_dir == "":
		return
	for r in dirty:
		if not runs.has(r):
			continue
		if r.history_file == "":
			r.history_file = History.file_for(r, runs)
		var err := History.save_run(history_dir, r)
		if err != OK:
			_set_status("Couldn’t save the run to %s (error %d)" % [history_dir, err])
	History.drop_older(history_dir, runs)


# --- usage -------------------------------------------------------------------------------------------

## Where the runs' usage is kept for this project ("" outside the editor): .godot/editor/, beside the dock's settings.
func _usage_path() -> String:
	var ei = _ei()
	if ei == null:
		return ""
	return ei.get_editor_paths().get_project_settings_dir().path_join(Usage.FILE_NAME)


## A usage frame: the run going on's token counts so far (each replaces the one before).
func _on_usage(models: Array, final: bool) -> void:
	var r = active if active != null else _reopen_lost()
	if r == null:
		_pending_usage = [models, final]  # (a run started elsewhere: its result comes next)
		return
	r.usage = Usage.parse(models)
	r.usage_final = final
	_keep_usage(r, final)
	if _settings_page.visible:
		_render_usage()


## A bill frame: what Autopilot was billed for the run through Aura that just finished (it comes before the run's result),
## kept on the run with its usage.
func _on_bill(frame: Dictionary) -> void:
	var b := Usage.parse_bill(frame)
	if b.is_empty():
		return
	var r = active if active != null else _reopen_lost()
	if r == null:
		_pending_bill = b  # (a run started elsewhere: its result comes next)
		return
	r.bill = b
	_keep_usage(r, true)
	if _settings_page.visible:
		_render_usage()


## Put the run's usage (and bill) in the kept runs; written now (now) or within USAGE_SAVE_EVERY seconds.
func _keep_usage(r, now: bool) -> void:
	if r == null or (r.usage.is_empty() and r.bill.is_empty()):
		return
	_usage_runs = Usage.upsert(_usage_runs, r.usage_record())
	_usage_dirty = true
	if now:
		_save_usage()


func _save_usage() -> void:
	_usage_dirty = false
	_usage_saved_at = Time.get_ticks_msec() / 1000.0
	var path := _usage_path()
	if path == "":
		return
	var err := Usage.save_runs(path, _usage_runs)
	if err != OK:
		_set_status("Couldn’t save the usage to %s (error %d)" % [path, err])


func _render_usage() -> void:
	# (the live run's verdict and usage as they are now)
	for r in runs:
		if not r.usage.is_empty() or not r.bill.is_empty():
			_usage_runs = Usage.upsert(_usage_runs, r.usage_record())
	var prefs := Prefs.load_preferences()
	_settings_page.show_data(_usage_runs, Prefs.share_data(prefs), _usage_note, account_view(prefs))


## The composer's Time limit as last set (a run's minutes in all), kept per user and project (project metadata
## "time_limit_minutes", default Model.TIME_LIMIT_DEFAULT): the composer starts with it.
func time_limit() -> int:
	return Model.time_limit_of(_meta("time_limit_minutes", Model.TIME_LIMIT_DEFAULT))


func _on_time_limit_changed(minutes: float) -> void:
	_set_meta("time_limit_minutes", Model.time_limit_of(minutes))


## The Settings page's Data sharing box: preferences.json share_data. The hello carries it, so a connection open with no
## run going is made again at once; otherwise it counts from the next connection.
func _on_share_toggled(on: bool) -> void:
	var err := Prefs.save_preferences({"share_data": on})
	if err != OK:
		_usage_note = ""
		_set_status("Couldn’t save the setup to %s (error %d)" % [Prefs.file(Prefs.PREFERENCES), err])
	else:
		# (the page says when it applies: from the next connection, made now when no run is going)
		_usage_note = ""
		if bridge.is_connected_to_brain() and active == null:
			bridge.disconnect_from_brain()
			_connect()
	_render_usage()


# --- first-run setup and Aura ---------------------------------------------------------------------------

func _open_setup() -> void:
	onboarding = true
	sign_in = {}
	_sign_in_send = false
	bridge.forget_sign_in()
	_setup.open(Prefs.share_data(Prefs.load_preferences()))
	ask_account()


## The setup's Finish (step 3's Next or Finish): preferences.json (merged with what is there), Use with Aura as chosen
## (access "aura" with use_aura: the Aura app's agent service), then the dock, connected. When the agent service had
## said it remembers something about this game the setup closes; else it goes on to step 4 (First run), which waits for
## the connection's memory_state and offers an Explore run when the memory is empty.
func _on_setup_finished(access: String, share: bool, use_aura := false) -> void:
	var changes := {"onboarded": true, "share_data": share}
	if access != "":
		changes.access = access
	var err := Prefs.save_preferences(changes)
	if err != OK:
		_set_status("Couldn’t save the setup to %s (error %d)" % [Prefs.file(Prefs.PREFERENCES), err])
	if memory_now() == "some":
		onboarding = false
	else:
		_setup.first_run()
	sign_in = {}
	_sign_in_send = false
	bridge.forget_sign_in()
	if access in ["aura", "own_keys"]:
		_set_use_with_aura(access == "aura" and use_aura, false)
	if not bridge.link.want and not bridge.is_connected_to_brain() and not _connect_when_ready:
		_connect()
	# (the account frame sent at the sign-in came before preferences.json said "aura": ask again)
	ask_account()
	_render()


## The setup closed (step 4's Skip, or the memory found not to be empty): the dock's usual page.
func close_setup() -> void:
	onboarding = false
	_render()


## Step 4's Explore and finish: an Explore run of these minutes (a start frame of its own kind, not read by the agent
## service), the setup closed and the run shown live.
func explore_from_setup(minutes: int) -> void:
	var run := {"kind": "explore", "minutes": maxi(1, minutes)}
	onboarding = false
	_start(run, "")


func _with_aura() -> bool:
	return bool(_meta("use_with_aura", false))


## Use with Aura on or off (kept per user and project); a connection open or being made is made again to the right
## agent service.
func _set_use_with_aura(on: bool, render := true) -> void:
	var changed := on != _with_aura()
	_set_meta("use_with_aura", on)
	if changed and (bridge.link.want or bridge.is_connected_to_brain()):
		bridge.disconnect_from_brain()
		_connect()  # (its welcome asks for the account)
	elif changed:
		ask_account()
	if render:
		_render()


## The toolbar badge (the Autopilot logo), made on first ask.
func get_badge() -> Button:
	if badge != null:
		return badge
	badge = Button.new()
	badge.theme = HT.theme(HT.s)
	badge.theme_type_variation = "AutopilotQuiet"
	# (28 × 24, square: the ground with the control outline, as Entry draws it in Godot's toolbar)
	badge.add_theme_stylebox_override("normal", HT.box(HT.GROUND, 0, HT.LINE, 1, Vector4(6, 2, 6, 2)))
	badge.add_theme_stylebox_override("hover", HT.box(HT.HOVER, 0, HT.LINE, 1, Vector4(6, 2, 6, 2)))
	badge.add_theme_stylebox_override("pressed", HT.box(HT.HOVER, 0, HT.LINE, 1, Vector4(6, 2, 6, 2)))
	badge.custom_minimum_size = Vector2(HT.px(28), HT.px(24))
	badge.icon = HT.icon(14)
	badge.icon_alignment = HORIZONTAL_ALIGNMENT_CENTER
	badge.focus_mode = Control.FOCUS_NONE
	badge.tooltip_text = "Open Autopilot"
	return badge


# --- the agent service ---------------------------------------------------------------------------------

func service_command() -> String:
	return str(_meta("service_command", ""))


func set_service_command(text: String) -> void:
	_set_meta("service_command", text)
	service.command_setting = text
	if _svc_cmd and _svc_cmd.text != text:
		_svc_cmd.text = text


## Whether connecting starts the agent service on this computer: allowed here (not a headless editor), not Use with
## Aura, and the address the default one (an Agent service command changes the command it is started with).
func _auto_service() -> bool:
	var url := _url.text.strip_edges() if _url else ""
	return auto_start_allowed and not _with_aura() and (url == "" or url == Link.DEFAULT_URL)


## The setup's step 2 opened: the agent service found running, or started (connected once ready).
func prepare_service() -> void:
	if removed():
		return
	if not auto_start_allowed:
		service.look()
		return
	service.ensure()
	if service.busy() or (service.state == "ready" and not bridge.link.want):
		_connect_when_ready = true
		_on_service_changed()


func install_service() -> void:
	service.install()
	_connect_when_ready = service.busy()
	_render_pages()


## Try again after a problem: the sign-in going on, else the install or the start.
func retry_service() -> void:
	if str(sign_in.get("state", "")) in ["error", "no_installer"]:
		begin_sign_in()
		return
	if service.state == "install_failed":
		install_service()
		return
	service.reset()
	if onboarding:
		prepare_service()
	else:
		_connect()
	_render_pages()


func open_log(path: String) -> void:
	if path != "":
		OS.shell_open(path)


func _on_service_changed() -> void:
	if removed() or (_removal_page and _removal_page.visible):
		_connect_when_ready = false
		_render()
		return
	if service.state == "ready" and _connect_when_ready:
		_connect_when_ready = false
		if not bridge.is_connected_to_brain():
			bridge.connect_to_brain(_url.text.strip_edges(), _token.text.strip_edges(), false)
	elif not service.busy():
		_connect_when_ready = false
	_render_pages()


## While the dock can't reach the agent service it would start: one announced may have ended (the editor that started
## it closed), so every few seconds another is started when none runs.
func _tick_service() -> void:
	if removed() or _conn != "unreachable" or not _auto_service() or service.busy():
		return
	if service.state in ["stopped", "failed", "install_failed"]:
		return
	var now := Time.get_ticks_msec() / 1000.0
	if now < _ensure_at:
		return
	_ensure_at = now + 5.0
	service.ensure()
	if service.busy():
		_connect_when_ready = true


# --- the Aura sign-in (asked of the agent service) -------------------------------------------------------

## Sign in with Aura in the browser: the agent service (installed and started first when needed) is asked to.
func begin_sign_in() -> void:
	sign_in = {"state": "preparing"}
	_sign_in_send = true
	_sign_in_installed = false
	if _with_aura():
		_set_use_with_aura(false, false)  # (the sign-in is the user's own agent service's)
	if service.state in ["stopped", "failed", "install_failed"]:
		service.reset()
	if not bridge.is_connected_to_brain() and not bridge.link.want and not _connect_when_ready:
		_connect()
	_tick_sign_in()
	_render_pages()


func cancel_sign_in() -> void:
	if _sign_in_asked():
		bridge.aura_sign_in_cancel()
	sign_in = {}
	_sign_in_send = false
	_render_pages()


## Use another account: sign out, then sign in again.
func another_account() -> void:
	bridge.aura_sign_out()
	sign_in = {"state": "preparing"}
	_sign_in_send = true
	_tick_sign_in()
	_render_pages()


## The progress line while the sign-in is prepared (the step the setup's list is at).
func sign_in_progress() -> String:
	if service.state == "installing":
		return "Setting up the agent service…"
	if bridge.is_connected_to_brain():
		return "Finish signing in in your browser…"
	return "Starting the agent service…"


## Whether the agent service was asked for the sign-in and hasn't said how it ended (sent and preparing, or waiting).
func _sign_in_asked() -> bool:
	var state := str(sign_in.get("state", ""))
	return state == "waiting" or (state == "preparing" and not _sign_in_send)


func _tick_sign_in() -> void:
	# (asked, then the agent service gone before it answered: shown as before it was asked while it comes back, or why
	# it can't; once back, the bridge asks it again)
	var away: bool = _sign_in_asked() and not bridge.is_connected_to_brain()
	if removed() or (not away and (sign_in.get("state") != "preparing" or not _sign_in_send)):
		return
	var before := sign_in.duplicate()
	if away:
		sign_in = {"state": "preparing"}  # (its page may have gone with it)
	if bridge.is_connected_to_brain():
		if bridge.brain_account:
			_sign_in_send = not bridge.aura_sign_in()
		else:
			sign_in = {"state": "error", "message": "This agent service can’t sign in to Aura: install it again, then try again."}
	elif service.state in ["stopped", "failed", "install_failed"]:
		sign_in = {"state": "error", "message": service.message, "log": service.log_path}
	elif service.state == "not_installed" and auto_start_allowed:
		if service.has_installer() and not _sign_in_installed:
			_sign_in_installed = true
			install_service()
		elif not service.has_installer():
			sign_in = {"state": "no_installer"}
	elif not bridge.link.want and not _connect_when_ready:
		_connect()
	if sign_in != before:
		_render_pages()


func _on_sign_in_frame(frame: Dictionary) -> void:
	if sign_in.is_empty():
		return  # (not asked here, or cancelled)
	_sign_in_send = false
	match str(frame.get("state", "")):
		"waiting":
			sign_in = {"state": "waiting", "url": str(frame.get("url", ""))}
		"signed_in":
			# (in the setup: Continue; on the Settings page the account frame that follows shows it)
			sign_in = {"state": "signed_in", "email": str(frame.get("email", ""))} if onboarding else {}
		"error":
			sign_in = sign_in_error(frame)
	_render_pages()


## The words for an aura_sign_in error frame ({} for "cancelled": nothing is shown).
static func sign_in_error(frame: Dictionary) -> Dictionary:
	var words := {"denied": "Sign-in was cancelled in the browser.",
		"timeout": "Nothing came back from the browser in 5 minutes.",
		"unreachable": "Couldn’t reach Aura. Check your connection and try again."}
	var reason := str(frame.get("reason", ""))
	if reason == "cancelled":
		return {}
	var message := str(words.get(reason, frame.get("message", "")))
	return {"state": "error", "reason": reason, "message": message if message != "" else "The sign-in didn’t finish. Try again."}


func _on_account() -> void:
	_render_footer()
	_render_composer()  # (out of Aura credits, or not any more)
	if _settings_page and _settings_page.visible:
		_render_usage()


func _on_account_action(action: String) -> void:
	match action:
		"setup":
			rerun_setup()
		"sign_in":
			begin_sign_in()
		"cancel_sign_in":
			cancel_sign_in()
		"sign_out":
			if not bridge.aura_sign_out():
				_render_usage()  # (not connected: Sign out's tooltip says so)
			else:
				ask_account()
		"install":
			install_service()
			_render_usage()
		"open_log":
			open_log(service.log_path)


## The Settings page's Account corner, from the agent service's account frame, preferences.json and Use with Aura.
func account_view(prefs: Dictionary) -> Dictionary:
	var si := sign_in.duplicate()
	if si.get("state") == "preparing":
		si.progress = sign_in_progress()
	var view := SettingsPage.account_view(bridge.account, prefs.get("access"), _with_aura(), _service_line(), si)
	view.busy = active != null
	view.connected = bridge.is_connected_to_brain()
	return view


## The agent service's line on the Settings page: {"text", "url", "action": "install" | "open_log" | ""} ({} with the
## Aura app's agent service).
func _service_line() -> Dictionary:
	if _with_aura():
		return {}
	if bridge.is_connected_to_brain():
		return {"text": "Agent service: running", "url": str(bridge.link.url), "action": ""}
	match service.state:
		"starting": return {"text": "Starting the agent service…", "url": "", "action": "", "busy": true}
		"installing": return {"text": "Installing the agent service…", "url": "", "action": "", "busy": true}
		"not_installed":
			return {"text": "Agent service: not installed", "url": "", "action": "install" if service.has_installer() else ""}
		"stopped", "failed", "install_failed":
			return {"text": "The agent service didn’t start." if service.state == "failed" else service.message, "url": "",
				"action": "open_log" if service.log_path != "" else ""}
	if auto_start_allowed:
		return {"text": "Agent service: starts when Autopilot connects", "url": "", "action": ""}
	return {"text": "Agent service: not running", "url": "", "action": ""}


## Ask the agent service for the account again (the dock's idea of it may be stale: after the setup, a sign-out, Use
## with Aura changed, the Settings page opened). Nothing when not connected: the welcome's ask covers it.
func ask_account() -> void:
	bridge.request_account()


## The setup or the Settings page shown again, after the sign-in or the agent service changed.
func _render_pages() -> void:
	if _setup and _setup.visible:
		_setup.refresh()
	if _settings_page and _settings_page.visible:
		_render_usage()
	if _connect_page and _connect_page.visible:
		_render_connect_page()


# --- removing Autopilot from this computer ------------------------------------------------------------

## Whether Autopilot is being (or was) removed from this computer in this editor session: the removal's page shows, and
## nothing starts, installs or connects to the agent service.
func removed() -> bool:
	return service.removal != ""


## The Settings page's Remove, confirmed: disconnected, then `autopilot uninstall` (lib/service.gd uninstall()); not
## while a run is going.
func remove_from_computer(keep_memory: bool) -> void:
	if active != null:
		_set_status(RUN_GOING)
		return
	settings_open = false
	onboarding = false
	sign_in = {}
	_sign_in_send = false
	_connect_when_ready = false
	bridge.forget_sign_in()
	bridge.disconnect_from_brain()
	if not keep_memory:
		_forget_runs()
	service.uninstall(keep_memory)
	_render()


## Removed without keeping the memories: the runs forgotten, and what the dock keeps about them deleted (the agent
## service's uninstall removes the same for every project it knows: autopilot/engines/godot.py plugin_data): the run
## history, the Settings page's token counts, the codex export, and each run's report files and frames in user://.
func _forget_runs() -> void:
	runs = []
	active = null
	shown = null
	_history_dirty = []
	if history_dir != "" and DirAccess.dir_exists_absolute(history_dir):
		Service.remove_tree(history_dir)
	_usage_runs = []
	var usage := _usage_path()
	if usage != "" and FileAccess.file_exists(usage):
		DirAccess.remove_absolute(usage)
	for dir in ["res://.godot/autopilot_codex", "user://playtest", "user://autopilot_codex"]:
		var path := ProjectSettings.globalize_path(dir)
		if DirAccess.dir_exists_absolute(path):
			Service.remove_tree(path)


## Set up again: the removal's page closed, the first-run setup from its first step.
func setup_again() -> void:
	service.end_removal()
	service.reset()
	_open_setup()
	_render()


## The setup at step 2 (Agent service), at start, when the computer was set up before but its agent service is gone
## (service_setup_needed()).
func _open_setup_at_service() -> void:
	_open_setup()
	var prefs := Prefs.load_preferences()
	_setup.open_at_service(Prefs.share_data(prefs), str(prefs.get("access", "")))


## Whether the setup opens at step 2 at start: set up before (preferences.json), the dock starting the agent service
## itself (_auto_service(): never headless, nor with Use with Aura or another address), no Agent service command set,
## none running (brain.json), and none to start (find()).
func service_setup_needed() -> bool:
	return (not removed() and Prefs.onboarded(Prefs.load_preferences()) and _auto_service()
		and service.command_setting.strip_edges() == "" and Prefs.announced_url(Prefs.OWN_BRAIN_FILE) == ""
		and service.find().is_empty())


## The removal's page: removing, removed (but for some files), or couldn't, with its buttons.
func _render_removal() -> void:
	var key := [service.removal, service.removal_message, service.removal_log, FileAccess.file_exists(service.removal_log)]
	if key == _removal_key:
		return
	_removal_key = key
	for c in _removal_page.get_children():
		_removal_page.remove_child(c)
		c.queue_free()
	var v := VBoxContainer.new()
	v.add_theme_constant_override("separation", HT.px(10))
	_removal_page.add_child(_column(v, Vector4(20, 24, 20, 24), CONNECT_WIDTH))
	var cv := VBoxContainer.new()
	cv.add_theme_constant_override("separation", HT.px(10))
	v.add_child(_panel(cv, HT.plate_box()))
	# its head: the logo and the product's name
	var head := HBoxContainer.new()
	head.add_theme_constant_override("separation", HT.px(10))
	var logo := TextureRect.new()
	logo.texture = HT.icon(20)
	logo.stretch_mode = TextureRect.STRETCH_KEEP_CENTERED
	head.add_child(logo)
	head.add_child(_caps("Autopilot", HT.TEXT2))
	cv.add_child(head)
	var buttons := _flow()
	var log: String = service.removal_log
	var open := func(): open_log(log)
	match service.removal:
		"running":
			var row := HBoxContainer.new()
			row.add_theme_constant_override("separation", HT.px(10))
			row.add_child(W.Spinner.new())
			row.add_child(_label("Uninstalling Autopilot from this computer…", "AutopilotStrong"))
			cv.add_child(row)
		"done":
			cv.add_child(_label("Uninstallation successful", "AutopilotTitle"))
			cv.add_child(_label(REMOVED_NEXT, "AutopilotSmall"))
			buttons.add_child(_button("Set up again", "AutopilotPrimary", setup_again))
			if FileAccess.file_exists(log):
				buttons.add_child(_button("View logs", "AutopilotQuiet", open))
		"kept":
			cv.add_child(_label("Uninstallation successful", "AutopilotTitle"))
			cv.add_child(_label(REMOVED_NEXT, "AutopilotSmall"))
			if FileAccess.file_exists(log):
				buttons.add_child(_button("View logs", "AutopilotPrimary", open))
			buttons.add_child(_button("Set up again", "AutopilotQuiet" if FileAccess.file_exists(log) else "AutopilotPrimary",
				setup_again))
		_:
			var l := _label("Couldn’t uninstall Autopilot from this computer.", "AutopilotTitle")
			l.add_theme_color_override("font_color", HT.RED)
			cv.add_child(l)
			if service.removal_message != "":
				cv.add_child(_label(service.removal_message, "AutopilotSmall"))
			if FileAccess.file_exists(log):
				buttons.add_child(_button("View logs", "Button", open))
			buttons.add_child(_button("Try again", "AutopilotPrimary", func():
				service.uninstall(service.removal_keep)
				_render()))
			buttons.add_child(_button("Cancel", "AutopilotQuiet", func():
				service.end_removal()
				_render()))
	if buttons.get_child_count() > 0:
		# (under a rule)
		var foot := VBoxContainer.new()
		foot.add_theme_constant_override("separation", HT.px(10))
		foot.add_child(HT.rule())
		foot.add_child(buttons)
		cv.add_child(foot)


func _dim(text: String) -> Label:
	return _label(text, "AutopilotDim")
