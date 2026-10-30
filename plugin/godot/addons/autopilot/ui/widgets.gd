## Autopilot's drawn controls: the verdict banner, the stage track, stacked action-probability bars, the replay strip,
## the harness attempts, the spinner and the recording's flipbook player.
@tool
extends RefCounted

const HT = preload("autopilot_theme.gd")
const Model = preload("model.gd")


## A page's column: its content at most max_width wide (before the editor's scale), centred when there is more room,
## with padding around it. Its minimum size is only its content's (plus the padding), so a page shown wide never keeps
## the dock from being made narrow again (a MarginContainer whose margins grow with the width would).
class Column:
	extends Container
	var max_width := 560.0
	var pad := Vector4(16, 16, 16, 16)  # left, top, right, bottom (before the editor's scale)

	func _init(max_w := 560.0, padding := Vector4(16, 16, 16, 16)) -> void:
		max_width = max_w
		pad = padding
		size_flags_horizontal = Control.SIZE_EXPAND_FILL
		mouse_filter = Control.MOUSE_FILTER_PASS

	func _get_minimum_size() -> Vector2:
		var m := Vector2.ZERO
		for c in get_children():
			if c is Control and c.visible and not c.top_level:
				m = m.max(c.get_combined_minimum_size())
		return m + Vector2(HT.px(pad.x) + HT.px(pad.z), HT.px(pad.y) + HT.px(pad.w))

	func _notification(what: int) -> void:
		if what != NOTIFICATION_SORT_CHILDREN:
			return
		var avail := size.x - HT.px(pad.x) - HT.px(pad.z)
		var w := minf(avail, HT.px(max_width))
		var x := HT.px(pad.x) + floorf((avail - w) * 0.5)
		for c in get_children():
			if c is Control and c.visible and not c.top_level:
				fit_child_in_rect(c, Rect2(x, HT.px(pad.y), w, size.y - HT.px(pad.y) - HT.px(pad.w)))


## The verdict: a solid block in the verdict's color, the word on it in ink (16 px, 600, upper case, letter-spaced),
## above the reason at its left edge.
class Banner:
	extends Control
	var text := ""
	var fill := HT.SUCCESS
	var ink := HT.ON_ACCENT
	var font_size := 16.0
	var _box := StyleBoxFlat.new()

	func _init() -> void:
		size_flags_horizontal = Control.SIZE_SHRINK_BEGIN
		mouse_filter = Control.MOUSE_FILTER_IGNORE

	## max_width (> 0): the type shrinks until the block fits it.
	func set_verdict(word: String, colors: Array, max_width := 0.0) -> void:
		text = word
		fill = colors[0]
		ink = colors[1]
		font_size = 16.0
		while max_width > 0.0 and font_size > 11.0 and _get_minimum_size().x > max_width:
			font_size -= 1.0
		update_minimum_size()
		queue_redraw()

	func _font() -> Font:
		return HT.font("verdict")

	func _get_minimum_size() -> Vector2:
		var fs := HT.px(font_size)
		var w := _font().get_string_size(text, HORIZONTAL_ALIGNMENT_LEFT, -1, fs).x
		return Vector2(w + HT.px(20), _font().get_height(fs) + HT.px(4))

	func _draw() -> void:
		var fs := HT.px(font_size)
		_box.bg_color = fill
		draw_style_box(_box, Rect2(Vector2.ZERO, size))
		var f := _font()
		var w := f.get_string_size(text, HORIZONTAL_ALIGNMENT_LEFT, -1, fs).x
		var base := (size.y - f.get_height(fs)) * 0.5 + f.get_ascent(fs)
		draw_string(f, Vector2((size.x - w) * 0.5, base), text, HORIZONTAL_ALIGNMENT_LEFT, -1, fs, ink)


## LAUNCH · ANALYZE · PROBE ENV · BUILD HARNESS · ROLLOUT · PROCESS RESULT · REPORT: a 2 px progress rule (the accent up
## to the middle of the current stage), then each stage's name and time under it, at the left of its column (done
## stages in TEXT2, the current one in the accent, the next ones muted). A name never gets clipped: it wraps onto more
## lines within its column, and when a word of one doesn't fit its column (a narrow dock) the names go on rows of their
## own under the rule, wrapping. layout() is what is drawn (the tests measure it).
class StageTrack:
	extends Control
	var stage := 0
	var attempt := 0
	var times := {}  # stage -> seconds spent (or so far)
	var done := false

	func _init() -> void:
		mouse_filter = Control.MOUSE_FILTER_IGNORE

	func _notification(what: int) -> void:
		if what == NOTIFICATION_RESIZED:
			update_minimum_size()  # (its height follows its width: the names' lines)

	func set_state(stage_: int, attempt_: int, times_: Dictionary, done_: bool) -> void:
		var relayout := attempt_ != attempt or times_.keys() != times.keys()
		stage = stage_
		attempt = attempt_
		times = times_
		done = done_
		if relayout:
			update_minimum_size()
		queue_redraw()

	## The stage names as shown (upper case; the rollout's with its harness).
	func labels() -> Array:
		var out := []
		for i in Model.STAGES.size():
			var label: String = Model.STAGES[i].to_upper()
			if i == Model.STAGE_ROLLOUT and attempt > 0:
				label += " · H%d" % attempt
			out.append(label)
		return out

	## How far the progress rule is filled (0 to 1): to the middle of the current stage, all of it once done.
	func progress() -> float:
		if done:
			return 1.0
		return clampf((stage + 0.5) / Model.STAGES.size(), 0.0, 1.0)

	static func _caps_w(text: String) -> float:
		return HT.font("caps").get_string_size(text, HORIZONTAL_ALIGNMENT_LEFT, -1, HT.px(10)).x

	static func _mono_w(text: String) -> float:
		return HT.font("mono").get_string_size(text, HORIZONTAL_ALIGNMENT_LEFT, -1, HT.px(10)).x

	## Words to lines at most room wide (a word wider than room alone on its line: layout() never lets that happen).
	static func wrap_words(text: String, room: float) -> PackedStringArray:
		var lines := PackedStringArray()
		var line := ""
		for word in text.split(" ", false):
			var next: String = word if line == "" else line + " " + word
			if line != "" and _caps_w(next) > room:
				lines.append(line)
				line = word
			else:
				line = next
		if line != "":
			lines.append(line)
		return lines

	const TOP := 10.0  # the names' top: under the 2 px rule and an 8 px gap (before the editor's scale)

	## Where each stage's name goes at width w: {"columns": bool, "height", "items": [{"lines", "xs" (each line's left),
	## "ys" (each line's baseline), "rooms" (the width each line was given), "time" (where its time goes), "time_w"}]}.
	## In columns, each name is at the left of its column, wrapped within it, its time under it; otherwise (a word too
	## wide for its column: a narrow dock) the names follow each other in rows under the rule, each with its time after it.
	func layout(w: float) -> Dictionary:
		var n := Model.STAGES.size()
		var names := labels()
		var cf := HT.font("caps")
		var cs := HT.px(10)
		var mf := HT.font("mono")
		var ms := HT.px(10)
		var lh := cf.get_height(cs)
		var col := w / n
		var room := col - HT.px(4)
		var columns := room > 0.0
		for label in names:
			for word in label.split(" ", false):
				if _caps_w(word) > room:
					columns = false
		var items := []
		var top := float(HT.px(TOP))
		var bottom := top
		if columns:
			for i in n:
				var lines := wrap_words(names[i], room)
				var it := {"lines": lines, "xs": [], "ys": [], "rooms": [], "time_w": -1}
				for k in lines.size():
					it.xs.append(col * i)
					it.ys.append(top + cf.get_ascent(cs) + lh * k)
					it.rooms.append(room)
				it.time = Vector2(col * i, it.ys[-1] + cf.get_descent(cs) + HT.px(2) + mf.get_ascent(ms))
				bottom = maxf(bottom, (it.time.y + mf.get_descent(ms)) if times.has(i) else (it.ys[-1] + cf.get_descent(cs)))
				items.append(it)
			return {"columns": true, "height": bottom + HT.px(2), "items": items}
		# rows: "LAUNCH 0:03   ANALYZE 0:12   ..." wrapping at w
		var gap := HT.px(12)
		var row_h := maxf(lh, mf.get_height(ms)) + HT.px(2)
		var x := 0.0
		var row := 0
		for i in n:
			var tw: float = (_mono_w(Model.short_clock(times[i])) + HT.px(5)) if times.has(i) else 0.0
			var lines := PackedStringArray([names[i]])
			if _caps_w(names[i]) + tw > w:
				lines = wrap_words(names[i], w)  # (wider than the dock itself: on lines of its own)
			if x > 0.0 and (lines.size() > 1 or x + _caps_w(names[i]) + tw > w):
				row += 1
				x = 0.0
			var it := {"lines": lines, "xs": [], "ys": [], "rooms": [], "time_w": -1}
			for k in lines.size():
				it.xs.append(x if k == 0 else 0.0)
				it.ys.append(top + (row + k) * row_h + cf.get_ascent(cs))
				it.rooms.append(w - it.xs[k])
			row += lines.size() - 1
			var end: float = it.xs[-1] + _caps_w(lines[-1])
			it.time = Vector2(end + HT.px(5), it.ys[-1])
			items.append(it)
			x = end + tw + gap
			bottom = top + (row + 1) * row_h
		return {"columns": false, "height": bottom + HT.px(2), "items": items}

	func _get_minimum_size() -> Vector2:
		return Vector2(HT.px(160), layout(size.x if size.x > 0.0 else HT.px(160)).height)

	func _draw() -> void:
		var n := Model.STAGES.size()
		var bar := float(maxi(1, HT.px(2)))
		draw_rect(Rect2(0, 0, size.x, bar), HT.RULE)
		draw_rect(Rect2(0, 0, roundf(size.x * progress()), bar), HT.ACCENT)
		var cs := HT.px(10)
		var mf := HT.font("mono")
		var ms := HT.px(10)
		var lay := layout(size.x)
		for i in n:
			var now := i == stage and not done
			var colr := HT.TEXT2 if (i < stage or (done and i <= stage)) else (HT.ACCENT if now else HT.MUTED)
			var f := HT.font("tag" if now else "caps")
			var it: Dictionary = lay.items[i]
			for k in it.lines.size():
				draw_string(f, Vector2(it.xs[k], it.ys[k]), it.lines[k], HORIZONTAL_ALIGNMENT_LEFT, -1, cs, colr)
			if times.has(i):
				draw_string(mf, it.time, Model.short_clock(times[i]), HORIZONTAL_ALIGNMENT_LEFT, -1, ms,
					HT.ACCENT if now else HT.MUTED)


## The harness attempts as squares (10 px, 4 apart, one row), then their count (Plex Mono 500, 13 px, 10 px after the
## last square): each finished one filled in the accent, the current one (a live run's) a 2 px accent outline. It takes
## the room its row gives it (it expands) and draws as many squares as fit there (the last ones: the current one kept,
## at least one, at most MAX); its minimum is one square and the count, so it never widens the dock nor wraps.
class Attempts:
	extends Control
	var finished := 0
	var current := false
	const MAX := 24  # (the squares drawn at most; the count after them says how many)
	const GAP := 4.0  # between squares (before the editor's scale)
	const COUNT_GAP := 10.0  # before the count

	func _init() -> void:
		mouse_filter = Control.MOUSE_FILTER_IGNORE
		size_flags_vertical = Control.SIZE_SHRINK_CENTER
		size_flags_horizontal = Control.SIZE_EXPAND_FILL

	func _notification(what: int) -> void:
		if what == NOTIFICATION_RESIZED:
			queue_redraw()

	func set_attempts(finished_: int, current_: bool) -> void:
		finished = maxi(0, finished_)
		current = current_
		update_minimum_size()
		queue_redraw()

	func total() -> int:
		return finished + (1 if current else 0)

	func count_text() -> String:
		return str(total())

	func _count_w() -> float:
		return HT.font("mono_bold").get_string_size(count_text(), HORIZONTAL_ALIGNMENT_LEFT, -1, HT.px(13)).x

	## The squares drawn at this width: as many as fit before the count (at least one when there are any).
	func count() -> int:
		var n := mini(total(), MAX)
		if n == 0:
			return 0
		var room := size.x - _count_w() - HT.px(COUNT_GAP)
		return clampi(floori((room + HT.px(GAP)) / float(HT.px(10) + HT.px(GAP))), 1, n)

	## How many of the squares drawn are filled: when not all fit (or past MAX) the newest ones are shown, so the last one
	## drawn is still the current (outlined) one; the older ones are left out.
	func filled_shown() -> int:
		return maxi(0, count() - (1 if current else 0))

	## Where each square is drawn: one row, left to right, vertically centred.
	func squares() -> Array:
		var out := []
		var side := float(HT.px(10))
		var y := floorf((size.y - side) * 0.5)
		for i in count():
			out.append(Rect2(i * (side + HT.px(GAP)), y, side, side))
		return out

	func _get_minimum_size() -> Vector2:
		var f := HT.font("mono_bold")
		var sq := (HT.px(10) + HT.px(COUNT_GAP)) if total() > 0 else 0
		return Vector2(sq + _count_w(), maxf(HT.px(10), f.get_height(HT.px(13))))

	func _draw() -> void:
		var rects := squares()
		var filled := filled_shown()
		for i in rects.size():
			var r: Rect2 = rects[i]
			if i < filled:
				draw_rect(r, HT.ACCENT)
			else:
				var w := float(maxi(1, HT.px(2)))
				draw_rect(r.grow(-w * 0.5), HT.ACCENT, false, w)
		var f := HT.font("mono_bold")
		var fs := HT.px(13)
		var x: float = (rects[-1].end.x + HT.px(COUNT_GAP)) if not rects.is_empty() else 0.0
		draw_string(f, Vector2(x, (size.y - f.get_height(fs)) * 0.5 + f.get_ascent(fs)), count_text(), HORIZONTAL_ALIGNMENT_LEFT, -1, fs, HT.TEXT)


## Something going on: a 12 px square, its 2 px outline in INFO_LINE with its top side in INFO, turning.
class Spinner:
	extends Control
	var angle := 0.0

	func _init() -> void:
		custom_minimum_size = Vector2(HT.px(12), HT.px(12))
		size_flags_vertical = Control.SIZE_SHRINK_CENTER
		size_flags_horizontal = Control.SIZE_SHRINK_BEGIN
		mouse_filter = Control.MOUSE_FILTER_IGNORE

	func _process(delta: float) -> void:
		if is_visible_in_tree():
			angle = fposmod(angle + delta * TAU, TAU)
			queue_redraw()

	func _draw() -> void:
		var side := float(HT.px(12))
		var w := float(maxi(1, HT.px(2)))
		var c := size * 0.5
		draw_set_transform(c, angle, Vector2.ONE)
		var r := Rect2(-side * 0.5, -side * 0.5, side, side)
		draw_rect(r.grow(-w * 0.5), HT.INFO_LINE, false, w)
		draw_rect(Rect2(r.position, Vector2(side, w)), HT.INFO)
		draw_set_transform(Vector2.ZERO, 0.0, Vector2.ONE)


## A stacked bar of action probabilities: [[name, p], ...] with the run's color for each name.
class ProbBar:
	extends Control
	var parts := []  # [[name, p, color]]
	var bar_height := 8.0

	func _init() -> void:
		mouse_filter = Control.MOUSE_FILTER_PASS
		size_flags_horizontal = Control.SIZE_EXPAND_FILL
		size_flags_vertical = Control.SIZE_SHRINK_CENTER

	func set_parts(p: Array, h := 8.0) -> void:
		parts = p
		bar_height = h
		update_minimum_size()
		queue_redraw()

	func _get_minimum_size() -> Vector2:
		return Vector2(HT.px(40), HT.px(bar_height))

	func _draw() -> void:
		draw_bar(self, Rect2(Vector2.ZERO, size), parts)

	static func draw_bar(ci: CanvasItem, rect: Rect2, parts_: Array) -> void:
		ci.draw_rect(rect, HT.HOVER)
		var x := rect.position.x
		for p in parts_:
			var w := rect.size.x * clampf(float(p[1]), 0.0, 1.0)
			if w <= 0.0:
				continue
			ci.draw_rect(Rect2(x, rect.position.y, minf(w, rect.end.x - x), rect.size.y), p[2])
			x += w
			if x >= rect.end.x:
				break


## What ran over game time in one attempt: "H2 · RUNNING", the action running as colored spans on a lane; "POLICY
## STEPS", a tick per step (a diamond per switch, a square per held one); the first and last step's game time; and a
## legend of the actions (with held and switch). compact (a narrow dock): the lane and the times only.
class Replay:
	extends Control
	var run = null  # Model.Run
	var attempt := 1
	var selected = null  # Model.Step
	var compact := false:
		set(v):
			compact = v
			update_minimum_size()
			queue_redraw()

	func _init() -> void:
		mouse_filter = Control.MOUSE_FILTER_IGNORE
		size_flags_horizontal = Control.SIZE_EXPAND_FILL

	func _notification(what: int) -> void:
		if what == NOTIFICATION_RESIZED:
			update_minimum_size()

	func show_attempt(run_, attempt_: int, selected_ = null) -> void:
		run = run_
		attempt = attempt_
		selected = selected_
		update_minimum_size()
		queue_redraw()

	func _steps() -> Array:
		var steps := []
		if run != null:
			for st in run.steps:
				if st.attempt == attempt:
					steps.append(st)
		return steps

	func _label_w() -> float:
		return float(HT.px(84 if compact else 120))

	## The actions in the legend, in order of first appearance in the attempt.
	func _names(steps: Array) -> Array:
		var out := []
		for st in steps:
			var n := _running(st)
			if n != "" and not out.has(n):
				out.append(n)
		return out

	static func _running(st) -> String:
		return st.chosen if st.tag in ["START", "SWITCH"] else (st.current if st.current != "" else st.chosen)

	## The legend's items: [[x, y (top), text, kind ("action" | "held" | "switch"), color]] at width w.
	func _legend(w: float, top: float) -> Array:
		var mf := HT.font("mono")
		var ms := HT.px(10)
		var items := []
		var entries := []
		for n in _names(_steps()).slice(0, 8):  # (at most 8 actions)
			entries.append([n, "action", run.color_of(n)])
		entries.append(["held", "held", HT.AMBER])
		entries.append(["switch", "switch", HT.ACCENT])
		var x0 := _label_w()
		var x := x0
		var y := top
		var row_h := float(HT.px(14))
		for e in entries:
			var iw := HT.px(8) + HT.px(6) + mf.get_string_size(e[0], HORIZONTAL_ALIGNMENT_LEFT, -1, ms).x
			if x > x0 and x + iw > w:
				x = x0
				y += row_h + HT.px(4)
			items.append([x, y, e[0], e[1], e[2]])
			x += iw + HT.px(14)
		return items

	func _get_minimum_size() -> Vector2:
		var h := float(HT.px(14 if compact else 16)) + HT.px(3)
		if not compact:
			h += HT.px(10) + HT.px(3)
		h += HT.px(14)  # (the times)
		if not compact and run != null:
			var w := size.x if size.x > 0.0 else 400.0
			var lg := _legend(w, h + HT.px(2))
			if not lg.is_empty():
				h = lg[-1][1] + HT.px(14)
		return Vector2(HT.px(200), h)

	func _draw() -> void:
		if run == null:
			return
		var steps := _steps()
		var cf := HT.font("caps")
		var cs := HT.px(10)
		var mf := HT.font("mono")
		var ms := HT.px(10)
		var lw := _label_w()
		var lane_w := size.x - lw
		var lane_h := float(HT.px(14 if compact else 16))
		var mid := func(y: float, h: float) -> float: return y + (h - cf.get_height(cs)) * 0.5 + cf.get_ascent(cs)
		draw_string(cf, Vector2(0, mid.call(0.0, lane_h)), ("H%d · RUNNING" % attempt), HORIZONTAL_ALIGNMENT_LEFT, lw - HT.px(4), cs, HT.MUTED)
		var ty := lane_h + HT.px(3)
		var tick_h := float(HT.px(10))
		if not compact:
			draw_string(cf, Vector2(0, mid.call(ty, tick_h)), "POLICY STEPS", HORIZONTAL_ALIGNMENT_LEFT, lw - HT.px(4), cs, HT.MUTED)
		draw_rect(Rect2(lw, 0, lane_w, lane_h), HT.HOVER)
		if steps.is_empty() or lane_w <= 10:
			return
		var t0: float = steps[0].t
		var t1: float = steps[-1].t
		var span := maxf(t1 - t0, 0.001)
		var fx := func(t: float) -> float: return lw + (t - t0) / span * lane_w
		var sel_x := -1.0
		for i in steps.size():
			var st = steps[i]
			var a: float = fx.call(st.t)
			var b: float = fx.call(steps[i + 1].t) if i + 1 < steps.size() else lw + lane_w
			var running := _running(st)
			if running != "":
				draw_rect(Rect2(a, 0, maxf(1.0, b - a), lane_h), run.color_of(running))
			if not compact:
				if st.tag == "SWITCH":
					Tags.diamond(self, Vector2(a, ty + tick_h * 0.5), HT.px(4.5), HT.ACCENT)
				elif st.tag == "HELD":
					draw_rect(Rect2(a - HT.px(3), ty + HT.px(2), HT.px(6), HT.px(6)), HT.AMBER)
				else:
					draw_rect(Rect2(a, ty, maxf(1.0, HT.px(1.5)), tick_h), HT.LINE_HI)
			if st == selected:
				sel_x = a
		if sel_x >= 0.0:
			var bottom := (ty + tick_h + HT.px(3)) if not compact else lane_h + HT.px(3)
			draw_rect(Rect2(sel_x - HT.px(1), -HT.px(3), maxf(1.0, HT.px(2)), bottom + HT.px(3)), HT.TEXT)
		var yt := (ty + tick_h + HT.px(3)) if not compact else ty
		var base := yt + mf.get_ascent(ms)
		draw_string(mf, Vector2(lw, base), "%.1fs" % t0, HORIZONTAL_ALIGNMENT_LEFT, -1, ms, HT.MUTED)
		draw_string(mf, Vector2(lw, base), "%.1fs" % t1, HORIZONTAL_ALIGNMENT_RIGHT, lane_w, ms, HT.MUTED)
		if compact:
			return
		for it in _legend(size.x, yt + HT.px(14) + HT.px(2)):
			var x: float = it[0]
			var y: float = it[1]
			var sq := float(HT.px(8))
			var cy := y + HT.px(7)
			match it[3]:
				"switch":
					Tags.diamond(self, Vector2(x + sq * 0.5, cy), HT.px(4.5), it[4])
				_:
					draw_rect(Rect2(x, cy - sq * 0.5, sq, sq), it[4])
			draw_string(mf, Vector2(x + sq + HT.px(6), cy - mf.get_height(ms) * 0.5 + mf.get_ascent(ms)), it[2], HORIZONTAL_ALIGNMENT_LEFT,
				-1, ms, HT.TEXT3 if it[3] == "action" else HT.MUTED)


## The action tags (START, KEEP, SWITCH, HELD, DROPPED), drawn as the tags are (square, letter-spaced mono).
class Tags:
	## A policy tag's kind (HT.TAG_COLORS): SWITCH the accent, START info, HELD warning, DROPPED red, KEEP neutral.
	static func kind(tag: String) -> String:
		match tag:
			"SWITCH": return "acc"
			"START": return "info"
			"HELD": return "warn"
			"DROPPED", "FAILED": return "bad"
		return "mut"

	## [surface, border, text]: SWITCH filled in the accent (ink on it), the others their kind's tag colors.
	static func colors(tag: String) -> Array:
		if tag == "SWITCH":
			return [HT.ACCENT, HT.ACCENT, HT.ON_ACCENT]
		var c: Array = HT.TAG_COLORS[kind(tag)]
		return [c[0], c[2], c[1]]

	static func diamond(ci: CanvasItem, c: Vector2, r: float, fill: Color, outline := Color.TRANSPARENT, width := 0.0) -> void:
		var pts := PackedVector2Array([c + Vector2(0, -r), c + Vector2(r, 0), c + Vector2(0, r), c + Vector2(-r, 0)])
		if fill.a > 0.0:
			ci.draw_colored_polygon(pts, fill)
		if outline.a > 0.0 and width > 0.0:
			pts.append(pts[0])
			ci.draw_polyline(pts, outline, width, true)

	static func draw_tag(ci: CanvasItem, rect: Rect2, tag: String, word := "") -> void:
		var c := colors(tag)
		ci.draw_style_box(HT.box(c[0], 0, c[1], 1), rect)
		var f := HT.font("tag")
		var fs := HT.px(9.5)
		var text := word if word != "" else tag
		var base := rect.position.y + (rect.size.y - f.get_height(fs)) * 0.5 + f.get_ascent(fs)
		ci.draw_string(f, Vector2(rect.position.x, base), text, HORIZONTAL_ALIGNMENT_CENTER, rect.size.x, fs, c[2])


## The run's recording, played as a flipbook of its JPEG frames (Godot plays no MP4): the picture as wide as the page
## (FlipbookScreen, letterboxed), then play/pause, a 2 px scrub bar (a frame per step), the time and its length, and Show in
## File Explorer (Finder, folder) with the folder icon. Frames are
## read from the folder as they are shown, a few around the playhead kept; nothing plays until Play is pressed.
class Flipbook:
	extends VBoxContainer
	const KEEP_BEHIND := 2  # frames kept read before the playhead
	const KEEP_AHEAD := 3  # and after it
	signal opened(path: String)  # Show in folder: the MP4 when there is one, else the frames' folder

	var folder := ""  # the frames: 000000.jpg, 000001.jpg ...
	var fps := 2.0
	var seconds := 0.0  # the recording's length as the agent service said (0: count / fps)
	var video := ""  # the MP4 the agent service wrote ("" for none)
	var count := 0
	var index := 0
	var playing := false
	var cache := {}  # frame index -> Texture2D
	var screen: FlipbookScreen
	var play_button: Button
	var scrub: HSlider
	var time_label: Label
	var open_button: Button
	var _timer: Timer
	var _icons := []  # [play, pause]

	func _init() -> void:
		add_theme_constant_override("separation", HT.px(8))
		size_flags_horizontal = Control.SIZE_EXPAND_FILL
		screen = FlipbookScreen.new()
		screen.gui_input.connect(func(e: InputEvent) -> void:
			if e is InputEventMouseButton and e.pressed and e.button_index == MOUSE_BUTTON_LEFT:
				toggle())
		add_child(screen)
		var row := HBoxContainer.new()
		row.add_theme_constant_override("separation", HT.px(10))
		add_child(row)
		play_button = HT.icon_button("play", "Play", "Button", toggle)
		play_button.focus_mode = Control.FOCUS_NONE
		row.add_child(play_button)
		scrub = HSlider.new()
		scrub.min_value = 0
		scrub.step = 1
		scrub.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		scrub.size_flags_vertical = Control.SIZE_SHRINK_CENTER
		scrub.custom_minimum_size = Vector2(HT.px(40), 0)
		scrub.focus_mode = Control.FOCUS_NONE
		scrub.value_changed.connect(func(v: float) -> void: show_frame(int(v)))
		row.add_child(scrub)
		time_label = Label.new()
		time_label.theme_type_variation = "AutopilotMono"
		time_label.size_flags_vertical = Control.SIZE_SHRINK_CENTER
		row.add_child(time_label)
		open_button = HT.icon_button("folder", show_name(), "AutopilotQuiet", func(): opened.emit(open_path()))
		row.add_child(open_button)
		_timer = Timer.new()
		_timer.timeout.connect(step)
		add_child(_timer)

	## The frames in a folder (000000.jpg up), 0 when it has none or isn't there.
	static func frame_count(dir: String) -> int:
		if dir == "" or not DirAccess.dir_exists_absolute(dir):
			return 0
		var n := 0
		while FileAccess.file_exists(dir.path_join("%06d.jpg" % n)):
			n += 1
		return n

	## Show this recording (Model.Run.recording()); whether it has any frame.
	func open(rec: Dictionary) -> bool:
		folder = str(rec.get("frames", ""))
		fps = maxf(0.1, float(rec.get("fps", 2.0)))
		seconds = float(rec.get("seconds", 0.0))
		video = str(rec.get("video", ""))
		count = frame_count(folder)
		cache.clear()
		_timer.wait_time = 1.0 / fps
		scrub.max_value = maxi(0, count - 1)
		open_button.tooltip_text = show_name()
		pause()
		show_frame(0)
		return count > 0

	## The frames' folder moved (copied into the run's folder): the same frame shown from there.
	func relocate(dir: String) -> void:
		folder = dir
		cache.clear()
		show_frame(index)

	func _has_video() -> bool:
		return video != "" and FileAccess.file_exists(video)

	## The recording's frames aren't there any more: the player stays, Play, its scrub bar and Show in File Explorer
	## disabled.
	func set_gone() -> void:
		pause()
		play_button.disabled = true
		scrub.editable = false
		open_button.disabled = true
		screen.set_unplayable(true)

	func open_path() -> String:
		return video if _has_video() else folder

	## The folder button's name: what the computer calls its file manager.
	static func show_name() -> String:
		match OS.get_name():
			"Windows":
				return "Show in File Explorer"
			"macOS":
				return "Show in Finder"
		return "Show in folder"

	## The recording's length in seconds.
	func length() -> float:
		return seconds if seconds > 0.0 else count / fps

	func texture(i: int) -> Texture2D:
		if cache.has(i):
			return cache[i]
		var path := folder.path_join("%06d.jpg" % i)
		var img := Image.load_from_file(path) if FileAccess.file_exists(path) else null
		var tex: Texture2D = ImageTexture.create_from_image(img) if img != null and not img.is_empty() else null
		for k in cache.keys():
			if k < i - KEEP_BEHIND or k > i + KEEP_AHEAD:
				cache.erase(k)
		cache[i] = tex
		return tex

	func show_frame(i: int) -> void:
		index = clampi(i, 0, maxi(0, count - 1))
		screen.picture.texture = texture(index) if count > 0 else null
		if int(scrub.value) != index:
			scrub.set_value_no_signal(index)
		time_label.text = "%s / %s" % [Model.short_clock(index / fps), Model.clock(length())]
		_show_button()

	func _show_button() -> void:
		if _icons.is_empty():
			_icons = [HT.glyph("play", 12), HT.glyph("pause", 12)]
		play_button.icon = _icons[1] if playing else _icons[0]
		play_button.tooltip_text = "Pause" if playing else "Play"
		screen.set_paused(not playing)

	func play() -> void:
		if count <= 1:
			return
		if index >= count - 1:
			show_frame(0)  # (at the end: from the start again)
		if not _timer.is_inside_tree():
			return
		playing = true
		_timer.start()
		_show_button()

	func pause() -> void:
		playing = false
		if _timer.is_inside_tree():
			_timer.stop()
		_show_button()

	func toggle() -> void:
		if playing:
			pause()
		else:
			play()

	## The next frame (the timer, at fps); the end stops it.
	func step() -> void:
		if index >= count - 1:
			pause()
			return
		show_frame(index + 1)
		if index >= count - 1:
			pause()

	func _notification(what: int) -> void:
		if what == NOTIFICATION_VISIBILITY_CHANGED and not is_visible_in_tree() and playing:
			pause()


## The flipbook's box: a well plate (radius 8, a 1 px rule) as wide as the page and height tall (230 px; 200 in a
## narrow dock, but never taller than 16:9 of its width), the frame drawn in it letterboxed, a play mark over it while
## it is paused (a warning mark, amber, once it can't play).
class FlipbookScreen:
	extends Control
	var picture: TextureRect
	var height := 230.0  # (before the editor's scale)
	var paused := true
	var unplayable := false  # (its frames are gone: the warning mark in place of the play mark)
	var _box := HT.box(HT.WELL, HT.R_PLATE, HT.RULE, 1)
	var _mark: Texture2D = null

	func _init() -> void:
		size_flags_horizontal = Control.SIZE_EXPAND_FILL
		mouse_filter = Control.MOUSE_FILTER_PASS
		clip_contents = true
		picture = TextureRect.new()
		picture.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
		picture.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
		picture.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
		var inset := maxi(1, HT.px(1))
		picture.offset_left = inset
		picture.offset_top = inset
		picture.offset_right = -inset
		picture.offset_bottom = -inset
		picture.mouse_filter = Control.MOUSE_FILTER_IGNORE
		picture.show_behind_parent = false
		add_child(picture)
		var mark := Control.new()  # (the play mark, over the picture)
		mark.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
		mark.mouse_filter = Control.MOUSE_FILTER_IGNORE
		mark.draw.connect(func() -> void:
			if paused or unplayable:
				if _mark == null:
					var side := 30 if height >= 230.0 else 26
					_mark = HT.glyph("warning", side, HT.AMBER) if unplayable else HT.glyph("play_big", side, HT.TEXT)
				if _mark:
					mark.draw_texture(_mark, ((mark.size - _mark.get_size()) * 0.5).floor()))
		add_child(mark)

	func set_paused(on: bool) -> void:
		paused = on
		for c in get_children():
			if c != picture:
				c.queue_redraw()

	func set_unplayable(on: bool) -> void:
		if on != unplayable:
			unplayable = on
			_mark = null
			set_paused(paused)

	func set_height(h: float) -> void:
		if h != height:
			height = h
			_mark = null
			update_minimum_size()

	func _notification(what: int) -> void:
		if what == NOTIFICATION_RESIZED:
			update_minimum_size()

	func _get_minimum_size() -> Vector2:
		var h := float(HT.px(height))
		if size.x > 0.0:
			h = minf(h, floorf(size.x * 9.0 / 16.0))
		return Vector2(HT.px(120), h)

	func _draw() -> void:
		draw_style_box(_box, Rect2(Vector2.ZERO, size))
