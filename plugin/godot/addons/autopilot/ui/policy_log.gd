## The Policy log: every policy step of a run (newest at the bottom, following live until a row is selected), filters by
## attempt and action tag, the replay of the attempt (what ran when), and the selected step: its tag, π(a | s) and the
## Observation, readable (in plates) or as raw JSON (always without the questions' instructions).
@tool
extends VBoxContainer

## Copy step JSON copied the step (the dock says so on its status line)
signal copied

const HT = preload("autopilot_theme.gd")
const Model = preload("model.gd")
const W = preload("widgets.gd")

const WIDE_COLS := ["wall", "t", "n", "current", "decision", "bar", "ms"]
const NARROW_COLS := ["n", "decision", "bar", "ms"]
const COL_TITLES := {"wall": "Wall clock", "t": "Game t", "n": "Step", "current": "Current action", "decision": "Decision",
	"bar": "Action probabilities", "ms": "ms"}
## The fixed columns' widths (wide; narrow where it differs), before the editor's scale
const COL_W := {"wall": 92, "t": 52, "n": 50, "current": 104, "decision": 176, "ms": 30}
const COL_W_NARROW := {"decision": 150, "ms": 26}

var run = null  # Model.Run
var wide := false
var follow := true
var selected = null  # Model.Step
var attempt_filter := 0  # 0: every attempt
var tag_on := {"SWITCH": true, "START": true, "KEEP": true, "HELD": true, "DROPPED": true}
var raw := false

var _cols: Array = NARROW_COLS
var _bar: PanelContainer
var _filters: HFlowContainer  # the attempt chips, then the tag chips, then following: one flow (more lines when narrow)
var _attempt_items := []  # the attempt filter's caption and chips, at the start of _filters
var _tag_chips := {}
var _follow_label: Label
var _follow_btn: Button
var _replay
var _replay_box: PanelContainer
var _split: SplitContainer
var _tree: Tree
var _root: TreeItem
var _summary: Label
var _summary_box: PanelContainer
var _detail: PanelContainer
var _d_head: Label
var _d_row: HBoxContainer
var _d_then: Label
var _d_fail: Label  # "This step failed: <error>"
var _pi_box: VBoxContainer
var _pi_grid: GridContainer
var _obs_head: Label
var _readable: VBoxContainer  # the Observation in plates (Task, environment state, current action, recent, action space)
var _raw: TextEdit
var _readable_chip: Button
var _raw_chip: Button
var _toggles: HFlowContainer
var _find: LineEdit
var _prev_btn: Button
var _next_btn: Button
## Find an action's letters ("": every row)
var find_text := ""
var _empty: Label


func _init() -> void:
	add_theme_constant_override("separation", 0)
	size_flags_vertical = Control.SIZE_EXPAND_FILL

	# the filters: Policy (all, each harness), a rule, Show (each tag), then following or paused
	_filters = HFlowContainer.new()
	_filters.add_theme_constant_override("h_separation", HT.px(6))
	_filters.add_theme_constant_override("v_separation", HT.px(6))
	_bar = _panel(_filters, HT.edge_box(HT.GROUND, HT.RULE, "bottom", 1, Vector4(16, 8, 16, 8)))
	add_child(_bar)
	var divider := ColorRect.new()
	divider.color = HT.LINE
	divider.custom_minimum_size = Vector2(1, HT.px(18))
	divider.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	divider.name = "Divider"
	_filters.add_child(divider)
	_filters.add_child(_caps("Show"))
	for tag in Model.TAG_ORDER:
		var c := _chip(tag, true)
		c.toggled.connect(func(on: bool) -> void:
			tag_on[tag] = on
			_rebuild())
		_filters.add_child(c)
		_tag_chips[tag] = c
	var room := Control.new()
	room.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	room.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_filters.add_child(room)
	# Find an action: the rows whose action (chosen or running) has these letters
	_find = LineEdit.new()
	_find.theme_type_variation = "AutopilotMonoField"
	_find.placeholder_text = "Find an action…"
	_find.add_theme_font_size_override("font_size", HT.px(11))
	_find.custom_minimum_size = Vector2(HT.px(200), HT.px(24))
	_find.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	_find.clear_button_enabled = false
	_find.text_changed.connect(func(t: String) -> void:
		find_text = t.strip_edges()
		_rebuild())
	_filters.add_child(_find)
	_follow_label = Label.new()
	_follow_label.theme_type_variation = "AutopilotCaps"
	_follow_label.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	_filters.add_child(_follow_label)
	_follow_btn = HT.button("Follow live", "Button", func(): set_follow(true), true)
	_filters.add_child(_follow_btn)

	# the replay of the attempt: on the plate's ground, over a rule
	_replay = W.Replay.new()
	_replay_box = _panel(_replay, HT.edge_box(HT.PLATE, HT.RULE, "bottom", 1, Vector4(16, 10, 16, 8)))
	add_child(_replay_box)

	_split = SplitContainer.new()
	_split.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_split.add_theme_constant_override("separation", 0)
	add_child(_split)
	var left := VBoxContainer.new()
	left.add_theme_constant_override("separation", 0)
	left.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	left.size_flags_vertical = Control.SIZE_EXPAND_FILL
	left.size_flags_stretch_ratio = 1.5
	_split.add_child(left)
	_tree = Tree.new()
	_tree.hide_root = true
	_tree.select_mode = Tree.SELECT_ROW
	_tree.column_titles_visible = true
	_tree.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_tree.custom_minimum_size = Vector2(0, HT.px(60))
	_tree.item_selected.connect(_on_select)
	left.add_child(_tree)
	_empty = _label("No policy steps yet.", "AutopilotSmall")
	_empty.add_theme_color_override("font_color", HT.MUTED)
	left.add_child(_margin(_empty, 16, 10, 16, 10))
	_summary = _label("", "AutopilotMonoDim")
	_summary_box = _panel(_summary, HT.edge_box(HT.GROUND, HT.RULE, "top", 1, Vector4(16, 7, 16, 7)))
	left.add_child(_summary_box)

	# the step: in a plate-colored panel (beside the table when wide, under it when narrow), scrolled as a whole
	_detail = PanelContainer.new()
	_detail.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_detail.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_split.add_child(_detail)
	var ds := ScrollContainer.new()
	ds.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	_detail.add_child(ds)
	var dv := VBoxContainer.new()
	dv.add_theme_constant_override("separation", HT.px(12))
	dv.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	ds.add_child(dv)
	var top := VBoxContainer.new()
	top.add_theme_constant_override("separation", HT.px(7))
	dv.add_child(top)
	_d_head = _label("", "AutopilotMonoDim")
	top.add_child(_d_head)
	_d_row = HBoxContainer.new()
	_d_row.add_theme_constant_override("separation", HT.px(8))
	top.add_child(_d_row)
	_d_then = _label("", "AutopilotSmall")
	top.add_child(_d_then)
	_d_fail = _label("", "AutopilotSmall")
	_d_fail.add_theme_color_override("font_color", HT.RED)
	top.add_child(_d_fail)
	_pi_box = VBoxContainer.new()
	_pi_box.add_theme_constant_override("separation", HT.px(6))
	dv.add_child(_pi_box)
	var pi_cap := _caps("Policy output · ")
	pi_cap.text += "π(a | s)"  # (lowercase: never upper-cased)
	_pi_box.add_child(_margin(pi_cap, 0, 0, 0, 3))
	_pi_grid = GridContainer.new()
	_pi_grid.columns = 3
	_pi_grid.add_theme_constant_override("h_separation", HT.px(10))
	_pi_grid.add_theme_constant_override("v_separation", HT.px(6))
	_pi_box.add_child(_pi_grid)
	_toggles = HFlowContainer.new()
	_toggles.add_theme_constant_override("h_separation", HT.px(6))
	_toggles.add_theme_constant_override("v_separation", HT.px(6))
	dv.add_child(_toggles)
	_obs_head = _caps("Observation", HT.ACCENT)
	_obs_head.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_toggles.add_child(_obs_head)
	var group := ButtonGroup.new()
	_readable_chip = _chip("Readable", true)
	_readable_chip.button_group = group
	_readable_chip.toggled.connect(func(on: bool) -> void:
		if on:
			_set_raw(false))
	_toggles.add_child(_readable_chip)
	_raw_chip = _chip("Raw JSON", false)
	_raw_chip.button_group = group
	_raw_chip.toggled.connect(func(on: bool) -> void:
		if on:
			_set_raw(true))
	_toggles.add_child(_raw_chip)
	_toggles.add_child(HT.button("Copy step JSON", "Button", _copy_step, true))
	_prev_btn = HT.icon_button("prev", "Previous step", "Button", func(): step_by(-1), true)
	_toggles.add_child(_prev_btn)
	_next_btn = HT.icon_button("next", "Next step", "Button", func(): step_by(1), true)
	_toggles.add_child(_next_btn)
	_readable = VBoxContainer.new()
	_readable.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_readable.add_theme_constant_override("separation", HT.px(10))
	dv.add_child(_readable)
	_raw = TextEdit.new()
	_raw.theme_type_variation = "AutopilotCode"
	_raw.editable = false
	_raw.add_theme_stylebox_override("read_only", HT.box(HT.WELL, HT.R_PLATE, HT.RULE, 1, Vector4(11, 9, 11, 9)))
	_raw.add_theme_font_size_override("font_size", HT.px(11))
	_raw.custom_minimum_size = Vector2(0, HT.px(260))
	_raw.wrap_mode = TextEdit.LINE_WRAPPING_BOUNDARY
	_raw.visible = false
	dv.add_child(_raw)
	set_wide(false)


static func _panel(child: Control, box: StyleBox) -> PanelContainer:
	var p := PanelContainer.new()
	p.add_theme_stylebox_override("panel", box)
	p.add_child(child)
	return p


static func _margin(child: Control, l: float, t: float, r: float, b: float) -> MarginContainer:
	var m := MarginContainer.new()
	m.add_theme_constant_override("margin_left", HT.px(l))
	m.add_theme_constant_override("margin_top", HT.px(t))
	m.add_theme_constant_override("margin_right", HT.px(r))
	m.add_theme_constant_override("margin_bottom", HT.px(b))
	m.add_child(child)
	return m


static func _label(text: String, variation: String) -> Label:
	var l := Label.new()
	l.theme_type_variation = variation
	l.text = text
	l.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	return l


func _caps(text: String, color := HT.MUTED) -> Label:
	var l := Label.new()
	l.theme_type_variation = "AutopilotCaps"
	l.text = text.to_upper()
	if color != HT.MUTED:
		l.add_theme_color_override("font_color", color)
	l.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	return l


## A filter chip (24 tall, mono): on in the accent tag's colors.
func _chip(text: String, on: bool) -> Button:
	var b := Button.new()
	b.theme_type_variation = "AutopilotChip"
	b.toggle_mode = true
	b.button_pressed = on
	b.text = text
	b.custom_minimum_size = Vector2(0, HT.px(24))
	b.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	b.focus_mode = Control.FOCUS_NONE
	return b


func set_wide(on: bool) -> void:
	wide = on
	_split.vertical = not on
	_filters.get_node("Divider").visible = on  # (no divider in a narrow dock)
	_cols = WIDE_COLS if on else NARROW_COLS
	var side := 16 if on else 10
	_bar.add_theme_stylebox_override("panel", HT.edge_box(HT.GROUND, HT.RULE, "bottom", 1, Vector4(side, 8, side, 8)))
	_replay_box.add_theme_stylebox_override("panel", HT.edge_box(HT.PLATE, HT.RULE, "bottom", 1,
		Vector4(16, 10, 16, 8) if on else Vector4(10, 8, 10, 6)))
	_summary_box.add_theme_stylebox_override("panel", HT.edge_box(HT.GROUND, HT.RULE, "top", 1, Vector4(side, 7 if on else 6, side, 7 if on else 6)))
	_detail.add_theme_stylebox_override("panel", HT.edge_box(HT.PLATE, HT.RULE, "left", 1, Vector4(18, 14, 18, 14)) if on
		else HT.edge_box(HT.PLATE, HT.RULE, "top", 1, Vector4(12, 12, 12, 12)))
	_replay.compact = not on
	_replay_box.visible = run != null and not run.steps.is_empty()
	_tree.custom_minimum_size = Vector2(0, HT.px(60))
	_rebuild()


func set_run(r) -> void:
	if r != run:
		selected = null
		follow = true
		attempt_filter = 0
	run = r
	_rebuild_attempts()
	_rebuild()


func set_follow(on: bool) -> void:
	follow = on
	if on:
		selected = null
		_tree.deselect_all()
		var last := _last_item()
		if last:
			_tree.scroll_to_item(last)
	_show_detail()
	_update_follow()
	_replay.show_attempt(run, _replay_attempt(), selected)
	_update_summary()


func _update_follow() -> void:
	var live: bool = run != null and run.live()
	if follow:
		_follow_label.text = "● FOLLOWING"
		_follow_label.add_theme_color_override("font_color", HT.ACCENT)
	else:
		_follow_label.text = ("PAUSED ON %s" % selected.label()) if selected else "PAUSED"
		_follow_label.add_theme_color_override("font_color", HT.AMBER)
	_follow_btn.visible = not follow


## A new step of the shown run (already added to it).
func on_step(st) -> void:
	if run == null:
		return
	if _attempt_items.size() - 2 != run.step_attempts():
		_rebuild_attempts()
	else:
		_update_counts()
	_empty.get_parent().visible = false
	_replay_box.visible = true
	if _passes(st):
		var it := _add_item(st)
		if follow:
			_tree.scroll_to_item(it)
	if follow:
		_show_detail()
	_replay.show_attempt(run, _replay_attempt(), selected)
	_update_summary()


func _replay_attempt() -> int:
	if attempt_filter > 0:
		return attempt_filter
	if selected:
		return selected.attempt
	return run.step_attempts() if run else 1


func _passes(st) -> bool:
	if find_text != "":
		var f := find_text.to_lower()
		if not (f in str(st.chosen).to_lower() or f in str(st.current).to_lower()):
			return false
	return (attempt_filter == 0 or st.attempt == attempt_filter) and tag_on.get(st.tag, true)


## The rows shown now (the filters', in order).
func shown_steps() -> Array:
	var out := []
	if run:
		for st in run.steps:
			if _passes(st):
				out.append(st)
	return out


## ◀ ▶: the step before or after the one shown, among the rows shown (picked: no longer following).
func step_by(d: int) -> void:
	var rows := shown_steps()
	if rows.is_empty():
		return
	var at := rows.find(_shown_step())
	var to := clampi((at if at >= 0 else rows.size() - 1) + d, 0, rows.size() - 1)
	selected = rows[to]
	follow = false
	if _root == null:
		return
	for it in _root.get_children():
		if it.get_metadata(0) == selected:
			it.select(0)
			_tree.scroll_to_item(it)
	_replay.show_attempt(run, _replay_attempt(), selected)
	_show_detail()
	_update_follow()
	_update_summary()


func _rebuild_attempts() -> void:
	for c in _attempt_items:
		c.queue_free()
		_filters.remove_child(c)
	_attempt_items = [_caps("Policy")]
	var n: int = run.step_attempts() if run else 0
	var group := ButtonGroup.new()
	var counts := _attempt_counts()
	var all := _chip("All · %d" % (run.steps.size() if run else 0), attempt_filter == 0)
	all.button_group = group
	all.toggled.connect(func(on: bool) -> void:
		if on:
			_set_attempt(0))
	_attempt_items.append(all)
	for a in range(1, n + 1):
		var c := _chip("H%d · %d" % [a, counts.get(a, 0)], attempt_filter == a)
		c.button_group = group
		c.toggled.connect(func(on: bool) -> void:
			if on:
				_set_attempt(a))
		_attempt_items.append(c)
	for i in _attempt_items.size():
		_filters.add_child(_attempt_items[i])
		_filters.move_child(_attempt_items[i], i)
	_update_counts()


func _attempt_counts() -> Dictionary:
	var out := {}
	if run:
		for st in run.steps:
			out[st.attempt] = out.get(st.attempt, 0) + 1
	return out


func _update_counts() -> void:
	var counts := {}
	var per := _attempt_counts()
	if run:
		for st in run.steps:
			if attempt_filter == 0 or st.attempt == attempt_filter:
				counts[st.tag] = counts.get(st.tag, 0) + 1
	for tag in _tag_chips:
		_tag_chips[tag].text = "%s %d" % [tag, counts.get(tag, 0)]
	if _tag_chips.has("FAILED"):
		var any := false
		if run:
			for st in run.steps:
				any = any or st.tag == "FAILED"
		_tag_chips.FAILED.visible = any  # (only when a step failed)
	var kids := _attempt_items
	if kids.size() >= 2 and run:
		kids[1].text = "All · %d" % run.steps.size()
		for i in range(2, kids.size()):
			kids[i].text = "H%d · %d" % [i - 1, per.get(i - 1, 0)]


func _set_attempt(a: int) -> void:
	attempt_filter = a
	_update_counts()
	_rebuild()


func _set_raw(on: bool) -> void:
	raw = on
	_show_detail()


func _last_item() -> TreeItem:
	if _root == null:
		return null
	var n := _root.get_child_count()
	return _root.get_child(n - 1) if n > 0 else null


func _rebuild() -> void:
	if _tree == null:
		return
	_tree.clear()
	_tree.columns = _cols.size()
	for c in _cols.size():
		var key: String = _cols[c]
		_tree.set_column_title(c, COL_TITLES[key].to_upper())
		_tree.set_column_title_alignment(c, HORIZONTAL_ALIGNMENT_RIGHT if key == "ms" else HORIZONTAL_ALIGNMENT_LEFT)
		_tree.set_column_clip_content(c, true)
		var w = COL_W_NARROW.get(key, COL_W.get(key)) if not wide else COL_W.get(key)
		if w != null:
			_tree.set_column_expand(c, false)
			_tree.set_column_custom_minimum_width(c, HT.px(w))
		else:
			_tree.set_column_expand(c, true)
			_tree.set_column_custom_minimum_width(c, HT.px(40))
	_root = _tree.create_item()
	var sel_item: TreeItem = null
	if run:
		for st in run.steps:
			if _passes(st):
				var it := _add_item(st)
				if st == selected:
					sel_item = it
	var none: bool = run == null or run.steps.is_empty()
	_empty.get_parent().visible = none
	_replay_box.visible = not none
	if sel_item:
		sel_item.select(0)
		_tree.scroll_to_item(sel_item)
	elif follow:
		var last := _last_item()
		if last:
			_tree.scroll_to_item(last)
	_replay.show_attempt(run, _replay_attempt(), selected)
	_show_detail()
	_update_summary()
	_update_follow()


func _add_item(st) -> TreeItem:
	var it := _tree.create_item(_root)
	it.set_metadata(0, st)
	it.set_custom_minimum_height(HT.px(28) - _tree.get_theme_constant("v_separation"))
	for c in _cols.size():
		match _cols[c]:
			"wall":
				it.set_text(c, Model.wall_time(st.wall) if st.wall > 0.0 else "—")
				it.set_custom_color(c, HT.MUTED)
			"t":
				it.set_text(c, "%.2fs" % st.t)
			"n":
				it.set_text(c, st.label())
				it.set_custom_color(c, HT.MUTED)
			"current":
				it.set_text(c, st.current if st.current != "" else "—")
				it.set_custom_color(c, run.color_of(st.current) if st.current != "" else HT.MUTED)
			"decision":
				it.set_cell_mode(c, TreeItem.CELL_MODE_CUSTOM)
				it.set_custom_draw_callback(c, _draw_decision_cell)
				it.set_tooltip_text(c, "%s %s" % [st.tag, st.chosen])
			"bar":
				it.set_cell_mode(c, TreeItem.CELL_MODE_CUSTOM)
				it.set_custom_draw_callback(c, _draw_bar_cell)
			"ms":
				it.set_text(c, str(int(st.ms)) if st.ms > 0.0 else "—")
				it.set_text_alignment(c, HORIZONTAL_ALIGNMENT_RIGHT)
				it.set_custom_color(c, HT.MUTED)
	return it


## The Decision cell: the step's tag (62 × 18), then the action chosen in its color (500 when it switched).
func _draw_decision_cell(item: TreeItem, rect: Rect2) -> void:
	var st = item.get_metadata(0)
	if st == null:
		return
	var h := float(HT.px(18))
	var tw := float(HT.px(62))
	var x := rect.position.x + HT.px(2)
	W.Tags.draw_tag(_tree, Rect2(x, rect.position.y + (rect.size.y - h) * 0.5, tw, h), st.tag)
	x += tw + HT.px(8)
	var f := HT.font("mono_bold" if st.tag == "SWITCH" else "mono")
	var fs := HT.px(11)
	var base := rect.position.y + (rect.size.y - f.get_height(fs)) * 0.5 + f.get_ascent(fs)
	var room := rect.end.x - x - HT.px(4)
	if room > 4.0:
		var name: String = st.chosen if st.chosen != "" else "—"
		_tree.draw_string(f, Vector2(x, base), name, HORIZONTAL_ALIGNMENT_LEFT, room, fs,
			run.color_of(st.chosen) if st.chosen != "" else HT.MUTED)


func _draw_bar_cell(item: TreeItem, rect: Rect2) -> void:
	var st = item.get_metadata(0)
	if st == null or run == null:
		return
	# (the two likeliest actions in their colors, the rest as one segment)
	var parts := []
	var rest := 0.0
	var probs: Array = st.sorted_probs()
	for i in probs.size():
		if i < 2:
			parts.append([probs[i][0], probs[i][1], run.color_of(probs[i][0])])
		else:
			rest += float(probs[i][1])
	if rest > 0.0:
		parts.append(["", rest, HT.LINE_HI])
	var bh := float(HT.px(9))
	W.ProbBar.draw_bar(_tree, Rect2(rect.position.x + HT.px(4), rect.position.y + (rect.size.y - bh) * 0.5, rect.size.x - HT.px(8), bh), parts)


func _on_select() -> void:
	var it := _tree.get_selected()
	if it == null:
		return
	selected = it.get_metadata(0)
	follow = false
	_replay.show_attempt(run, _replay_attempt(), selected)
	_show_detail()
	_update_follow()
	_update_summary()


func _shown_step():
	if selected:
		return selected
	if run == null or run.steps.is_empty():
		return null
	for i in range(run.steps.size() - 1, -1, -1):
		if _passes(run.steps[i]):
			return run.steps[i]
	return null  # (no row shown: "Pick a step …")


static func _count(n: int, one: String, many: String) -> String:
	return "%d %s" % [n, one if n == 1 else many]


func _update_summary() -> void:
	if run == null or run.steps.is_empty():
		_summary.text = ""
		_summary_box.visible = false
		return
	_summary_box.visible = true
	# (the harness shown: the filter's, else the picked step's; "All harnesses" only with All and no step picked)
	var h: int = attempt_filter if attempt_filter > 0 else (selected.attempt if selected != null else 0)
	var steps := []
	for st in run.steps:
		if h == 0 or st.attempt == h:
			steps.append(st)
	var ms := []
	var counts := {}
	for st in steps:
		if st.ms > 0.0:
			ms.append(st.ms)
		counts[st.tag] = counts.get(st.tag, 0) + 1
	ms.sort()
	var who := "H%d" % h if h > 0 else "All harnesses"
	var parts := ["%s: %s" % [who, _count(steps.size(), "step", "steps")]]
	if not ms.is_empty():
		parts.append("median %d ms · max %d ms" % [int(ms[ms.size() / 2]), int(ms[-1])])
	parts.append(_count(counts.get("SWITCH", 0), "switch", "switches"))
	parts.append(_count(counts.get("START", 0), "start", "starts"))
	parts.append("%d held" % counts.get("HELD", 0))
	parts.append("%d dropped" % counts.get("DROPPED", 0))
	if counts.get("FAILED", 0) > 0:
		parts.append("%d failed" % counts.FAILED)
	if run.step_count > run.steps.size():
		parts.append("the oldest %d no longer kept" % (run.step_count - run.steps.size()))
	_summary.text = " · ".join(parts)


# --- the selected step ------------------------------------------------------------------------------

func _clear(c: Node) -> void:
	for k in c.get_children():
		c.remove_child(k)
		k.queue_free()


func _show_detail() -> void:
	var st = _shown_step()
	_clear(_d_row)
	_clear(_pi_grid)
	_clear(_readable)
	_toggles.visible = st != null
	_pi_box.visible = st != null
	if st == null:
		_d_head.text = "No policy steps yet." if run == null or run.steps.is_empty() else "Pick a step to see what the policy observed and chose."
		_d_then.text = ""
		_d_fail.visible = false
		_raw.visible = false
		_readable.visible = false
		return
	var rows := shown_steps()
	var at := rows.find(st)
	_prev_btn.disabled = at <= 0
	_next_btn.disabled = at < 0 or at >= rows.size() - 1
	_d_head.text = "%s · t=%.1fs%s%s" % [st.label().replace(" ", " · "), st.t,
		(" · " + Model.wall_time(st.wall)) if st.wall > 0.0 else "", (" · %d ms" % int(st.ms)) if st.ms > 0.0 else ""]
	var tag := HT.tag(Model.TAG_PAST.get(st.tag, st.tag), W.Tags.kind(st.tag))
	var tc: Array = W.Tags.colors(st.tag)
	tag.add_theme_stylebox_override("panel", HT.box(tc[0], 0, tc[1], 1, Vector4(10, 4, 10, 4)))
	(tag.get_child(0) as Label).add_theme_color_override("font_color", tc[2])
	(tag.get_child(0) as Label).add_theme_font_size_override("font_size", HT.px(10.5))
	_d_row.add_child(tag)
	var cur := _mono(st.current if st.current != "" else "nothing running", run.color_of(st.current) if st.current != "" else HT.MUTED)
	_d_row.add_child(cur)
	_d_row.add_child(_mono("→", HT.MUTED))
	var ch := _mono(st.chosen if st.chosen != "" else "no action", run.color_of(st.chosen) if st.chosen != "" else HT.MUTED, true)
	_d_row.add_child(ch)
	for l in [cur, ch]:
		# (long action names share the row, cut short with an ellipsis: the whole name is in the tooltip)
		l.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		l.clip_text = true
		l.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
		l.tooltip_text = l.text
		l.mouse_filter = Control.MOUSE_FILTER_PASS
	var criteria := _criteria(st)
	var nd = st.state.get("now_doing")
	if typeof(nd) == TYPE_DICTIONARY:
		var lbl := str(criteria.get(st.current, ""))
		_d_then.text = "Action: \"%s\" (%s)" % [lbl if lbl != "" else st.current, Model.secs(nd.get("for_seconds"))]
	else:
		_d_then.text = "No action was running."
	_d_fail.text = ("This step failed: %s" % Model.scrub(st.error)) if st.error != "" else ""
	_d_fail.visible = st.error != ""
	_obs_head.text = "OBSERVATION · #%d" % st.n
	# π(a | s): every action, the chosen one first
	var nw := HT.px(160 if wide else 130)
	for pr in st.sorted_probs():
		var action: String = pr[0]
		var is_chosen: bool = action == st.chosen
		var l := _mono(action + (" · current" if action == st.current else ""), run.color_of(action) if is_chosen else HT.TEXT2, is_chosen, 11.5)
		l.custom_minimum_size = Vector2(nw, 0)
		l.clip_text = true
		l.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
		l.tooltip_text = action
		l.mouse_filter = Control.MOUSE_FILTER_PASS
		_pi_grid.add_child(l)
		var b = W.ProbBar.new()
		b.set_parts([[action, pr[1], run.color_of(action)]], 10)
		_pi_grid.add_child(b)
		var v := _mono(Model.prob(pr[1]), HT.TEXT, false, 11.5)
		v.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT
		v.custom_minimum_size = Vector2(HT.px(34), 0)
		_pi_grid.add_child(v)
	_raw.visible = raw
	_readable.visible = not raw
	if raw:
		_raw.text = JSON.stringify(Model.redact(st.frame), "  ")
		return
	_observation(st, criteria)


func _mono(text: String, color: Color, bold := false, font_size := 12.0) -> Label:
	var l := Label.new()
	l.text = text
	l.add_theme_font_override("font", HT.font("mono_bold" if bold else "mono"))
	l.add_theme_font_size_override("font_size", HT.px(font_size))
	l.add_theme_color_override("font_color", color)
	l.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	return l


func _criteria(st) -> Dictionary:
	var input = st.frame.get("input", {})
	if typeof(input) == TYPE_DICTIONARY and typeof(input.get("questions")) == TYPE_DICTIONARY:
		var pick = input.questions.get("pick")
		if typeof(pick) == TYPE_DICTIONARY and typeof(pick.get("criteria")) == TYPE_DICTIONARY:
			return pick.criteria
	return {}


## A section of the Observation: a plate on the ground (radius 8, a 1 px rule), its lines 4 apart; returns its column.
func _section(title: String, aside := "", aside_color := HT.MUTED) -> VBoxContainer:
	var v := VBoxContainer.new()
	v.add_theme_constant_override("separation", HT.px(4))
	_readable.add_child(_panel(v, HT.plate_box(Vector4(12, 10, 12, 10), HT.RULE, HT.GROUND)))
	var h := HFlowContainer.new()  # (the aside under the title in a narrow dock)
	var t := _caps(title)
	t.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	h.add_child(t)
	if aside != "":
		var a := _mono(aside, aside_color, false, 10)
		a.size_flags_horizontal = Control.SIZE_EXPAND | Control.SIZE_SHRINK_END
		h.add_child(a)
	v.add_child(_margin(h, 0, 0, 0, 3))
	return v


## A row of mono cells: [text, color, width (0: the rest), font] each.
func _cells(parent: Control, cells: Array, font_size := 11.0) -> void:
	var h := HBoxContainer.new()
	h.add_theme_constant_override("separation", HT.px(10))
	for c in cells:
		var l := _mono(str(c[0]), c[1], false, font_size)
		if c.size() > 3 and c[3] == "body":
			l.add_theme_font_override("font", HT.font("body"))
			l.add_theme_font_size_override("font_size", HT.px(12))
		if float(c[2]) > 0.0:
			l.custom_minimum_size = Vector2(HT.px(c[2]), 0)
			l.clip_text = true
			l.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
		else:
			l.size_flags_horizontal = Control.SIZE_EXPAND_FILL
			l.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
		h.add_child(l)
	parent.add_child(h)


func _observation(st, criteria: Dictionary) -> void:
	var s: Dictionary = st.state
	var prev = run.previous_step(st)
	var prev_sit = prev.state.get("situation") if prev else null
	# Task
	var task := HBoxContainer.new()
	task.add_theme_constant_override("separation", HT.px(12))
	var tk := _caps("Task")
	tk.custom_minimum_size = Vector2(HT.px(50), 0)
	tk.size_flags_vertical = Control.SIZE_SHRINK_BEGIN
	task.add_child(tk)
	var goal := _label(str(s.get("goal", st.frame.get("goal", ""))), "AutopilotBody")
	goal.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	task.add_child(goal)
	_readable.add_child(_panel(task, HT.plate_box(Vector4(12, 10, 12, 10), HT.RULE, HT.GROUND)))
	# the environment state, what changed since the step before in the accent
	var sit = s.get("situation")
	var changed := []
	if typeof(sit) == TYPE_DICTIONARY and typeof(prev_sit) == TYPE_DICTIONARY:
		for k in sit:
			if not prev_sit.has(k) or JSON.stringify(prev_sit[k]) != JSON.stringify(sit[k]):
				changed.append(k)
	var since := ""
	if prev and not changed.is_empty():
		since = ("● %d changed since #%d" % [changed.size(), prev.n]) if wide else ("● %d changed" % changed.size())
	var env := _section("Observation · environment state", since, HT.ACCENT)
	if typeof(sit) == TYPE_DICTIONARY and not sit.is_empty():
		for k in sit:
			var ch: bool = changed.has(k)
			var shown := Model.num(sit[k])  # (the new value only, highlighted when it changed)
			_cells(env, [[k, HT.MUTED, 130], [("● " + shown) if ch else shown, HT.ACCENT if ch else HT.TEXT2, 0]])
	elif sit != null:
		_cells(env, [[Model.num(sit), HT.TEXT2, 0]])
	else:
		_cells(env, [["—", HT.MUTED, 0]])
	# the current action
	var cur := _section("Current action")
	var nd = s.get("now_doing")
	if typeof(nd) == TYPE_DICTIONARY:
		var action := str(nd.get("option", ""))
		_cells(cur, [["%s for %s" % [action, Model.secs(nd.get("for_seconds"))], run.color_of(action), 0]], 11.5)
	else:
		_cells(cur, [["none", HT.MUTED, 0]], 11.5)
	# what the recent actions did
	var rec := _section("Recent actions · outcomes")
	var recent = s.get("recent")
	if typeof(recent) == TYPE_ARRAY and not recent.is_empty():
		for r in recent:
			if typeof(r) != TYPE_DICTIONARY:
				continue
			var action := str(r.get("option", ""))
			var worked = r.get("worked")
			var how := Model.scrub(str(r.get("how", "")))
			_cells(rec, [[action, run.color_of(action), 110], [Model.secs(r.get("seconds")), HT.TEXT2, 44],
				["success" if worked == true else ("failure" if worked == false else "unknown"),
					HT.SUCCESS if worked == true else (HT.RED if worked == false else HT.MUTED), 52],
				[how, HT.TEXT3, 0, "body"]])
	else:
		_cells(rec, [["none yet", HT.MUTED, 0]])
	# the action space: an entry per action, the chosen one selected
	var opts = s.get("options")
	if typeof(opts) == TYPE_DICTIONARY:
		var sp := _section("Action space · %d" % opts.size(), "π(a | s)", HT.MUTED)
		for action in opts:
			var p := float(st.probs.get(action, 0.0))
			var chosen: bool = action == st.chosen
			var row := HBoxContainer.new()
			row.add_theme_constant_override("separation", HT.px(10))
			var col := VBoxContainer.new()
			col.add_theme_constant_override("separation", HT.px(1))
			col.size_flags_horizontal = Control.SIZE_EXPAND_FILL
			row.add_child(col)
			col.add_child(_mono(str(action) + (" · current" if action == st.current else ""), run.color_of(str(action)), chosen, 11.5))
			if str(criteria.get(action, "")) != "":
				col.add_child(_label(str(criteria[action]), "AutopilotSmall"))
			var facts = opts[action]
			if typeof(facts) == TYPE_DICTIONARY and not facts.is_empty():
				var bits := []
				for k in facts:
					bits.append("%s: %s" % [k, Model.num(facts[k])])
				var fl := _mono(" · ".join(bits), HT.MUTED, false, 10)
				fl.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
				col.add_child(fl)
			elif facts != null and typeof(facts) != TYPE_DICTIONARY:
				col.add_child(_mono(Model.num(facts), HT.MUTED, false, 10))
			var pl := _mono(Model.prob(p), HT.ACCENT if chosen else HT.TEXT3, chosen, 14)
			pl.size_flags_vertical = Control.SIZE_SHRINK_BEGIN
			row.add_child(pl)
			var m := Vector4(10, 7, 10, 7)
			sp.add_child(_panel(row, HT.selected_box(m) if chosen else HT.edge_box(Color.TRANSPARENT, Color.TRANSPARENT, "left", 2, m)))
	# what else the policy answered
	var out = st.frame.get("output", {})
	var answers = out.get("answers", {}) if typeof(out) == TYPE_DICTIONARY else {}
	if typeof(answers) == TYPE_DICTIONARY and answers.size() > (1 if answers.has("pick") else 0):
		var oth := _section("Other outputs")
		for k in answers:
			if k == "pick":
				continue
			_cells(oth, [[str(k), HT.MUTED, 130], [Model.num(answers[k]), HT.TEXT2, 0]])


func _copy_step() -> void:
	var st = _shown_step()
	if st:
		DisplayServer.clipboard_set(JSON.stringify(Model.redact(st.frame), "  "))
		copied.emit()
