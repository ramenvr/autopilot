## The addon's pure logic, without a game: preferences.json and autopilot.env (lib/prefs.gd), the Settings page's numbers, the
## Autopilot bill (bill frames, the account's bill, money and dates as shown) and its file (ui/usage.gd), and the picture's scaling, encoding and message text (lib/media.gd). Run by units.py:
##     godot --headless --path <test game> --script res://units.gd
## with LOCALAPPDATA / XDG_CONFIG_HOME / HOME pointing at an empty temporary folder (UNITS_DIR, the same folder):
## prints PASS / FAIL per check and exits 0 when all pass.
extends SceneTree

const Prefs = preload("res://addons/autopilot/lib/prefs.gd")
const Usage = preload("res://addons/autopilot/ui/usage.gd")
const History = preload("res://addons/autopilot/ui/history.gd")
const Media = preload("res://addons/autopilot/lib/media.gd")
const Service = preload("res://addons/autopilot/lib/service.gd")
const SettingsPage = preload("res://addons/autopilot/ui/settings_page.gd")
const Model = preload("res://addons/autopilot/ui/model.gd")
const W = preload("res://addons/autopilot/ui/widgets.gd")
const HT = preload("res://addons/autopilot/ui/autopilot_theme.gd")

var failed := 0
var passed := 0


func check(name: String, ok: bool, detail = "") -> void:
	if ok:
		passed += 1
		print("PASS  %s" % name)
	else:
		failed += 1
		print("FAIL  %s  [%s]" % [name, str(detail)])


func _init() -> void:
	var dir := OS.get_environment("UNITS_DIR")
	if dir == "":
		print("FAIL  UNITS_DIR is not set (units.py sets it)")
		quit(1)
		return
	prefs_tests(dir)
	env_tests(dir)
	announced_tests(dir)
	usage_tests(dir)
	media_tests()
	service_tests(dir)
	uninstall_live_tests(dir)
	stage_tests()
	kind_tests()
	track_tests()
	attempts_tests()
	no_recording_tests(dir)
	alerts_tests(dir)
	history_tests(dir)
	look_tests()
	print("\n%d/%d checks passed" % [passed, passed + failed])
	quit(0 if failed == 0 else 1)


## A finished run as the dock keeps it (ui/model.gd Run): started at unix, its result, feed and findings.
func _history_run(id: String, unix: float, result := {"verdict": "confirmed", "reason": "The door opens"}):
	var r := Model.Run.new()
	r.id = id
	r.kind = "verify"
	r.prompt = "Verify the door opens"
	r.request = {"kind": "verify", "claim": "Verify the door opens", "time_limit_minutes": 30}
	r.started_unix = unix
	r.started_ms = 1000
	r.ended_ms = 1000 + 206000  # (03:26)
	r.stage = Model.STAGE_REPORT
	r.stage_since = 200.0
	r.stage_secs = {0: 4.5, Model.STAGE_ROLLOUT: 150.25}
	r.attempts = 2
	r.feed = [{"t": 1.0, "text": "Launching the environment", "kind": "status"}, {"t": 200.0, "text": "Result: Confirmed", "kind": "info"}]
	r.findings = [{"signature": "s1", "summary": "Fell through the floor", "severity": "error", "detector": "fall", "count": 2,
		"where": {"X": 1.5, "Y": 2.0, "Z": -3.0}, "source": "engine"}]
	r.not_a_bug = {"s1": true}
	r.result = result.duplicate(true)
	r.report_dir = ""
	r.step_count = 48
	r.usage = Usage.parse([{"kind": "llm", "model": "m", "calls": 3, "input": 1200, "output": 300}])
	r.usage_final = true
	r.bill = {"usd": 0.42, "billed": true}
	return r


func _write_text(path: String, text: String) -> void:
	var f := FileAccess.open(path, FileAccess.WRITE)
	f.store_string(text)
	f.close()


## The run history kept between editor sessions (ui/history.gd): a run saved and loaded back as it was (its whole
## numbers whole again), the newest 50 kept and in order, a run live when the editor closed loaded with no result
## (saved again so), the recording's copy in the run's folder preferred, files that aren't runs left out.
func history_tests(base: String) -> void:
	var dir := base.path_join("history")
	var r = _history_run("20261019-132500", 1760880300.25)
	r.history_file = History.file_for(r, [])
	check("history: a run's file is named by its id", r.history_file == "20261019-132500.json", r.history_file)
	check("history: saved", History.save_run(dir, r) == OK and FileAccess.file_exists(dir.path_join(r.history_file)))
	var loaded := History.load_runs(dir)
	check("history: loaded back, one run", loaded.size() == 1, loaded.size())
	var b = loaded[0] if loaded.size() == 1 else Model.Run.new()
	check("history: its id, kind, prompt, start (exact), file", b.id == r.id and b.kind == "verify" and b.prompt == r.prompt
		and b.started_unix == r.started_unix and b.history_file == r.history_file, [b.id, b.kind, b.prompt, b.started_unix])
	check("history: ended, what it took, its verdict and reason", not b.live() and is_equal_approx(b.took(), 206.0)
		and b.verdict() == "CONFIRMED" and b.reason() == "The door opens", [b.live(), b.took(), b.verdict(), b.reason()])
	check("history: Run again's frame, its whole numbers whole", b.request == r.request and typeof(b.request.time_limit_minutes) == TYPE_INT,
		b.request)
	check("history: stage, its times (by stage number), attempts", b.stage == Model.STAGE_REPORT and b.stage_secs.get(Model.STAGE_ROLLOUT) == 150.25
		and b.stage_secs.get(0) == 4.5 and b.attempt_count() == 2, [b.stage, b.stage_secs, b.attempts])
	check("history: the feed and findings, not a bug kept", b.feed.size() == 2 and b.feed[1].text == "Result: Confirmed"
		and b.findings.size() == 1 and b.findings[0].summary == "Fell through the floor" and b.not_a_bug.has("s1")
		and b.engine_findings().size() == 1, [b.feed, b.findings, b.not_a_bug])
	check("history: usage and bill", b.usage == r.usage and b.usage_final and Usage.billed(b.bill) == 0.42, [b.usage, b.bill])
	check("history: no policy steps kept, how many came is", b.steps.is_empty() and b.step_count == 48
		and "**Policy steps:** 48" in Model.report_markdown(b), b.step_count)
	check("history: the same report as before it was saved", Model.report_markdown(b) == Model.report_markdown(r),
		[Model.report_markdown(b), Model.report_markdown(r)])

	# files that aren't runs: left out
	_write_text(dir.path_join("broken.json"), "{")
	_write_text(dir.path_join("noid.json"), JSON.stringify({"started_unix": 1.0}))
	_write_text(dir.path_join("20261019-132500.json.123.tmp"), JSON.stringify(History.to_dict(r)))
	check("history: a broken file, one without an id, a temporary file: left out", History.load_runs(dir).size() == 1,
		History.load_runs(dir).size())

	# a run live when the editor closed: no result, and saved again so
	var live = _history_run("20261019-140000", 1760882400.0, {})
	live.ended_ms = -1
	live.started_ms = Time.get_ticks_msec() - 5000
	live.history_file = History.file_for(live, [r])
	History.save_run(dir, live)
	check("history: a live run saved as not done", Prefs.read_json(dir.path_join(live.history_file)).get("done") == false)
	loaded = History.load_runs(dir)
	var c = loaded[-1] if loaded.size() == 2 else Model.Run.new()
	check("history: loaded in order of their start, the live one newest", loaded.size() == 2 and loaded[0].id == r.id and c.id == live.id,
		loaded.map(func(x): return x.id))
	check("history: the live one ended, NO RESULT, the editor closed", not c.live() and c.verdict() == "NO RESULT"
		and c.reason() == History.CLOSED_REASON and c.reason() == "The editor closed before the result came."
		and c.feed[-1].text == "Editor closed: no result" and c.feed[-1].kind == "info", [c.verdict(), c.reason(), c.feed])
	check("history: and saved again so (done), its line once", Prefs.read_json(dir.path_join(live.history_file)).get("done") == true
		and History.load_runs(dir)[-1].feed.filter(func(l): return l.text == History.CLOSED_LINE).size() == 1)
	check("history: its report says why", "The editor closed before the result came." in Model.report_markdown(c))

	# the recording: its copy in the run's folder, when there is one
	var run_dir := base.path_join("history_run")
	DirAccess.make_dir_recursive_absolute(run_dir.path_join("recording"))
	_write_text(run_dir.path_join("recording").path_join("000000.jpg"), "jpg")
	var rec = _history_run("20261019-150000", 1760886000.0, {"verdict": "confirmed",
		"recording": {"frames": base.path_join("agent_frames"), "frames_fps": 2, "seconds": 4.0}})
	rec.report_dir = run_dir
	var back = History.from_dict(JSON.parse_string(JSON.stringify(History.to_dict(rec))))
	check("history: the recording plays from the run's own folder", back.recording().get("frames") == run_dir.path_join("recording"),
		back.recording())
	rec.report_dir = base.path_join("no_such_run")
	back = History.from_dict(JSON.parse_string(JSON.stringify(History.to_dict(rec))))
	check("history: no copy: from where the agent service wrote it", back.recording().get("frames") == base.path_join("agent_frames"),
		back.recording())
	# who started it, when the tab didn't (run_started's by): its line is there after a restart too
	rec.origin = "claude-code"
	back = History.from_dict(JSON.parse_string(JSON.stringify(History.to_dict(rec))))
	check("history: a run from Claude Code still says so", back.origin_line() == "Started from Claude Code", back.origin)
	rec.origin = ""
	back = History.from_dict(JSON.parse_string(JSON.stringify(History.to_dict(rec))))
	check("history: a run the tab started has no such line", back.origin_line() == "", back.origin)

	# the newest 50 kept, the files of the older ones deleted
	var many := base.path_join("history_many")
	var runs := []
	for i in 55:
		var m = _history_run("20261001-%06d" % (54 - i), 1759276800.0 + ((i * 3 + 7) % 55) * 60.0)  # (names out of order)
		m.history_file = History.file_for(m, runs)
		runs.append(m)
		History.save_run(many, m)
	check("history: 55 saved", DirAccess.get_files_at(many).size() == 55, DirAccess.get_files_at(many).size())
	var kept := History.load_runs(many)
	var ordered := true
	for i in range(1, kept.size()):
		ordered = ordered and kept[i - 1].started_unix < kept[i].started_unix
	check("history: 50 loaded, oldest first, the newest", kept.size() == 50 and ordered
		and kept[0].started_unix == 1759276800.0 + 5 * 60.0, [kept.size(), ordered, kept[0].started_unix if kept.size() else 0])
	check("history: the 5 oldest's files deleted", DirAccess.get_files_at(many).size() == 50, DirAccess.get_files_at(many).size())
	# while the editor is open: more runs listed, the files of the oldest deleted
	var listed := kept.duplicate()
	for i in 2:
		var m = _history_run("20261002-00000%d" % i, 1759363200.0 + i)
		m.history_file = History.file_for(m, listed)
		listed.append(m)
		History.save_run(many, m)
	History.drop_older(many, listed)
	check("history: 52 listed, 50 files: the 2 oldest's gone", DirAccess.get_files_at(many).size() == 50
		and not FileAccess.file_exists(many.path_join(listed[0].history_file)) and FileAccess.file_exists(many.path_join(listed[2].history_file)))
	# two runs started in the same second: a file each
	var twin = _history_run(runs[0].id, 1.0)
	check("history: the same id twice, a file each", History.file_for(twin, runs) == runs[0].id + "-2.json", History.file_for(twin, runs))


## Aura's look (The Flatness, dark): its fonts read from the addon's files (IBM Plex Sans 400/500/600, IBM Plex Mono
## 400/500, JetBrains Mono for the symbols), its palette, 8 px plates and square controls, the switch's pill, the shadow
## only on floating panels, the 2 px accent marks, the verdict block square.
func look_tests() -> void:
	HT.release()
	HT.fonts_missing = false
	var roles := ["body", "body_medium", "body_semibold", "display", "display_bold", "verdict", "caps", "tag", "mono", "mono_bold"]
	for role in roles:
		HT.font(role)
	check("look: every bundled font read", not HT.fonts_missing)
	for file in ["IBMPlexSans-Regular.ttf", "IBMPlexSans-Medium.ttf", "IBMPlexSans-SemiBold.ttf", "OFL-IBMPlexSans.txt",
			"IBMPlexMono-Regular.ttf", "IBMPlexMono-Medium.ttf", "OFL-IBMPlexMono.txt", "JetBrainsMono-Regular.ttf", "OFL-JetBrainsMono.txt"]:
		check("look: fonts/%s bundled" % file, FileAccess.file_exists("res://addons/autopilot/fonts/" + file))
	check("look: fonts/.gdignore kept (the editor never imports them)", FileAccess.file_exists("res://addons/autopilot/fonts/.gdignore"))
	for old in ["SpaceGrotesk-Regular.ttf", "SpaceGrotesk-Medium.ttf", "SpaceGrotesk-Bold.ttf", "OFL-SpaceGrotesk.txt",
			"Barlow-Regular.ttf", "ChakraPetch-SemiBold.ttf", "JetBrainsMono-Variable.ttf"]:
		check("look: fonts/%s gone" % old, not FileAccess.file_exists("res://addons/autopilot/fonts/" + old))
	var weights := {"body": "IBMPlexSans-Regular.ttf", "body_medium": "IBMPlexSans-Medium.ttf", "body_semibold": "IBMPlexSans-SemiBold.ttf",
		"display": "IBMPlexSans-Medium.ttf", "display_bold": "IBMPlexSans-SemiBold.ttf", "mono": "IBMPlexMono-Regular.ttf",
		"mono_bold": "IBMPlexMono-Medium.ttf"}
	for role in weights:
		var f: FontFile = HT.font(role)
		check("look: %s is %s" % [role, weights[role]], f.data == FileAccess.get_file_as_bytes("res://addons/autopilot/fonts/" + weights[role]))
	for role in ["caps", "tag", "verdict"]:
		check("look: %s letter-spaced" % role, (HT.font(role) as FontVariation).spacing_glyph >= 1)
	# (every glyph the dock shows: the symbols from the JetBrains Mono fallback, never tofu)
	for role in roles:
		var missing := []
		for ch in "●▶◀▾✓○▸◂■…’–—→×π·•":
			if not HT.font(role).has_char(ch.unicode_at(0)):
				missing.append(ch)
		check("look: %s has every symbol" % role, missing.is_empty(), missing)
	check("look: The Flatness's palette", HT.GROUND == Color("#121316") and HT.RAIL == Color("#0f1013") and HT.PLATE == Color("#181a1e")
		and HT.POPOVER == Color("#15171b") and HT.HOVER == Color("#1d2026") and HT.ROW_ACTIVE == Color("#2a1b10")
		and HT.WELL == Color("#0d0e11") and HT.RULE == Color("#24262b") and HT.LINE == Color("#2c2f36"))
	check("look: text, accent, focus", HT.TEXT == Color("#e6e6e8") and HT.TEXT2 == Color("#d3d5da") and HT.TEXT3 == Color("#a4a8b0")
		and HT.MUTED == Color("#8b8f98") and HT.ACCENT == Color("#f0873a") and HT.ACCENT_HI == Color("#f59b57")
		and HT.ON_ACCENT == Color("#141517") and HT.FOCUS == Color("#ffb066") and HT.STEP_DONE == Color("#6b4122"))
	check("look: verdicts and states", HT.SUCCESS == Color("#74b078") and HT.SUCCESS_FILL == Color("#16281a") and HT.SUCCESS_LINE == Color("#3d6b42")
		and HT.RED == Color("#e26a62") and HT.RED_FILL == Color("#2c1715") and HT.RED_LINE == Color("#7a3b36")
		and HT.AMBER == Color("#e8a94e") and HT.AMBER_FILL == Color("#2a2114") and HT.AMBER_LINE == Color("#7a5c2a")
		and HT.INFO == Color("#7aa5d8") and HT.INFO_FILL == Color("#161f2b") and HT.INFO_LINE == Color("#3a5578")
		and HT.NEUTRAL_FG == Color("#c9ccd2"))
	check("look: the action colors, in order", HT.ACTION_COLORS == [Color("#f0873a"), Color("#7aa5d8"), Color("#e8a94e"), Color("#74b078"),
		Color("#a08ad8"), Color("#e26a62"), Color("#5fb3b3"), Color("#c9ccd2")])
	var b: StyleBoxFlat = HT.plate_box()
	check("look: a plate's four corners 8, a 1 px rule, no shadow", b.corner_radius_top_left == 8 and b.corner_radius_top_right == 8
		and b.corner_radius_bottom_left == 8 and b.corner_radius_bottom_right == 8 and b.border_width_top == 1
		and b.border_color == HT.RULE and b.bg_color == HT.PLATE and b.shadow_size == 0)
	var t: Theme = HT.theme(1.0)
	var square := []
	for type in ["Button", "ButtonSm", "AutopilotPrimary", "AutopilotPrimarySm", "AutopilotQuiet", "AutopilotQuietSm", "AutopilotDanger",
			"AutopilotDangerSm", "AutopilotDangerFill", "AutopilotOn", "AutopilotTab", "AutopilotChip", "AutopilotDrop", "OptionButton", "LineEdit", "TextEdit"]:
		for st in ["normal", "hover", "pressed", "focus"]:
			var sb = t.get_stylebox(st, type)
			if sb is StyleBoxFlat and (sb.corner_radius_top_left != 0 or sb.shadow_size != 0):
				square.append("%s %s" % [type, st])
	check("look: every control square, no shadow", square.is_empty(), square)
	var primary: StyleBoxFlat = t.get_stylebox("normal", "AutopilotPrimary")
	check("look: the primary button: the accent, ink on it, square", primary.bg_color == HT.ACCENT and primary.corner_radius_top_left == 0
		and t.get_color("font_color", "AutopilotPrimary") == HT.ON_ACCENT
		and (t.get_stylebox("hover", "AutopilotPrimary") as StyleBoxFlat).bg_color == HT.ACCENT_HI)
	var plain: StyleBoxFlat = t.get_stylebox("normal", "Button")
	check("look: a button outlined in LINE on nothing", plain.border_color == HT.LINE and plain.border_width_left == 1
		and plain.bg_color.a == 0.0 and (t.get_stylebox("hover", "Button") as StyleBoxFlat).bg_color == HT.HOVER)
	check("look: the red buttons", t.get_color("font_color", "AutopilotDanger") == HT.RED
		and (t.get_stylebox("normal", "AutopilotDangerFill") as StyleBoxFlat).bg_color == HT.RED)
	var focus: StyleBoxFlat = t.get_stylebox("focus", "Button")
	check("look: the focus ring 2 px FOCUS, outside", focus.border_color == HT.FOCUS and focus.border_width_top == 2
		and not focus.draw_center and focus.expand_margin_top == 4)
	check("look: buttons in IBM Plex Sans 500, 12 px (small 11.5)", t.get_font("font", "Button") == HT.font("display")
		and t.get_font_size("font_size", "Button") == 12 and t.get_font_size("font_size", "ButtonSm") == 12)
	var tab: StyleBoxFlat = t.get_stylebox("pressed", "AutopilotTab")
	check("look: the tab on: a 2 px accent rule under it", tab.border_color == HT.ACCENT and tab.border_width_bottom == 2)
	check("look: the micro-label mono 10, muted", t.get_font("font", "AutopilotCaps") == HT.font("caps")
		and t.get_font_size("font_size", "AutopilotCaps") == 10 and t.get_color("font_color", "AutopilotCaps") == HT.MUTED)
	var pop: StyleBoxFlat = t.get_stylebox("panel", "PopupPanel")
	check("look: a floating panel: POPOVER, 8, outlined, the only shadow", pop.bg_color == HT.POPOVER and pop.corner_radius_top_left == 8
		and pop.border_color == HT.LINE and pop.shadow_size > 0 and pop.shadow_color == HT.SHADOW)
	var on: Image = t.get_icon("checked", "AutopilotSwitch").get_image()
	var off: Image = t.get_icon("unchecked", "AutopilotSwitch").get_image()
	check("look: the switch's pill and knob", on.get_size() == Vector2i(30, 16) and on.get_pixel(3, 8).is_equal_approx(HT.ACCENT)
		and on.get_pixel(22, 8).is_equal_approx(HT.KNOB) and off.get_pixel(26, 8).is_equal_approx(HT.LINE) and on.get_pixel(0, 0).a < 0.5,
		[on.get_size(), on.get_pixel(3, 8), on.get_pixel(22, 8), off.get_pixel(26, 8), on.get_pixel(0, 0)])
	check("look: verdicts' colors", HT.verdict_colors("CONFIRMED")[0] == HT.SUCCESS and HT.verdict_colors("GOAL HIT")[2] == "ok"
		and HT.verdict_colors("DENIED")[0] == HT.RED and HT.verdict_colors("INCONCLUSIVE")[0] == HT.AMBER
		and HT.verdict_colors("EXPLORED")[0] == HT.INFO and HT.verdict_colors("STOPPED")[0] == HT.MUTED
		and HT.verdict_colors("LIVE")[3] == HT.ACCENT and HT.verdict_colors("NO RESULT")[2] == "mut")
	check("look: SWITCH tagged filled in the accent, ink on it; the others their kind's tag", W.Tags.colors("SWITCH") == [HT.ACCENT, HT.ACCENT, HT.ON_ACCENT]
		and W.Tags.colors("START") == [HT.INFO_FILL, HT.INFO_LINE, HT.INFO] and W.Tags.colors("KEEP") == [HT.HOVER, HT.LINE, HT.NEUTRAL_FG])
	check("look: the selected row: ROW_ACTIVE, a 2 px accent rule at its left", HT.selected_box().bg_color == HT.ROW_ACTIVE
		and HT.selected_box().border_color == HT.ACCENT and HT.selected_box().border_width_left == 2)
	check("look: history rows alternate RAIL and GROUND", HT.row_box("", false).bg_color == HT.RAIL and HT.row_box("", true).bg_color == HT.GROUND
		and HT.row_box("hover").bg_color == HT.HOVER)
	var tg := HT.tag("Goal hit", "ok")
	var tb: StyleBoxFlat = tg.get_theme_stylebox("panel")
	check("look: a tag: square, its surface and border, upper case mono", tb.corner_radius_top_left == 0 and tb.bg_color == HT.SUCCESS_FILL
		and tb.border_color == HT.SUCCESS_LINE and (tg.get_child(0) as Label).text == "GOAL HIT"
		and (tg.get_child(0) as Label).theme_type_variation == "AutopilotTag")
	tg.free()
	var banner = W.Banner.new()
	banner.set_verdict("CONFIRMED", HT.verdict_colors("CONFIRMED"))
	check("look: the verdict block square, 16 px, ink on it", banner._box.corner_radius_top_left == 0 and banner._box.skew == Vector2.ZERO
		and banner.font_size == 16.0 and banner.ink == HT.ON_ACCENT)
	banner.free()
	check("look: the icon drawn in the accent", FileAccess.get_file_as_string("res://addons/autopilot/icons/autopilot.svg").contains("#f0873a"))


## The stage track: after a rollout the results are processed; the track follows the lines (an older agent service
## went back to the stage the agent went on with).
func stage_tests() -> void:
	var r := Model.Run.new()
	r.started_ms = Time.get_ticks_msec()
	for line in ["Starting the game", "Reading the game's code", "Writing the harness", "Trying the harness (run 1)",
			"Now: walking to the crate"]:
		r.on_status(Model.translate(line))
	check("stage: rollout", r.stage == Model.STAGE_ROLLOUT and r.now_text == "walking to the crate", [r.stage, r.now_text])
	var tr := Model.translate("Looking at the results")
	check("stage: results line", tr.text == "Processing results" and tr.stage == Model.STAGE_RESULTS, tr)
	r.on_status(tr)
	check("stage: process result", r.stage == Model.STAGE_RESULTS and r.now_text == "", [r.stage, r.now_text])
	check("stage: its name", Model.STAGES[Model.STAGE_RESULTS] == "Process result", Model.STAGES)
	r.on_status(Model.translate("Writing the harness"))
	check("stage: back for another harness", r.stage == 3, r.stage)
	var times := r.stage_times()
	check("stage: times kept", times.has(Model.STAGE_ROLLOUT) and times.has(Model.STAGE_RESULTS) and times.has(3), times)
	# the memory being saved (after the agent finished, before the result): the Report stage, never back
	tr = Model.translate("Saving what it learned to memory")
	check("stage: saving to memory is the report", tr.stage == Model.STAGE_REPORT and tr.text == "Saving what it learned to memory"
		and tr.attempt == 0 and not tr.now, tr)
	r.on_status(Model.translate("Looking at the results"))
	r.on_status(tr)
	check("stage: process result, then saving to memory: report", r.stage == Model.STAGE_REPORT, r.stage)
	r.on_status(tr)
	check("stage: saving to memory again: still the report", r.stage == Model.STAGE_REPORT and r.feed[-1].text == tr.text, r.stage)
	steer_tests()


## The steer box takes words only while the game plays: the Rollout stage, from "Trying the harness (run N)" and
## through its "Now: ..." lines, until the next stage; never once the run is done.
func steer_tests() -> void:
	var r := Model.Run.new()
	r.started_ms = Time.get_ticks_msec()
	check("steer: not at the start (Launch)", not r.steerable() and r.stage == 0)
	var want := {"Starting the game": false, "Reading the game's code": false, "Trying things in the game": false,
		"Writing the harness": false, "Trying the harness (run 1)": true, "Now: walking to the crate": true,
		"Looking at the results": false, "Now: a late action line": false, "Trying the harness (run 2)": true,
		"Saving what it learned to memory": false}
	var got := {}
	for line in want:
		r.on_status(Model.translate(line))
		got[line] = r.steerable()
	check("steer: only at Rollout (Launch, Analyze, Probe, Build, Process result, Report: no)", got == want, got)
	var s := Model.Run.new()
	s.started_ms = Time.get_ticks_msec()
	s.on_status(Model.translate("Now: walking to the crate"))  # (an action line before any harness line: Rollout)
	check("steer: a Now line before the harness line is Rollout", s.steerable(), s.stage)
	r.on_status(Model.translate("Trying the harness (run 3)"))
	r.ended_ms = Time.get_ticks_msec()
	check("steer: never once the run is done (even ended at Rollout)", not r.steerable() and r.stage == Model.STAGE_ROLLOUT)


## Why a run has no recording: decided when its result arrives (Model.keep_no_recording), kept in its result.json, and
## shown again from it; never while the run goes on, never with a recording, never for a result kept before.
func no_recording_tests(dir: String) -> void:
	var why := "the game can't be recorded here: it runs without rendering"
	var older := "No recording: " + Model.NO_RECORDING_OLDER
	var cases := [
		["the agent service's reason", {"verdict": "confirmed", "no_recording": why, "memory": {"updated": false}}, "No recording: " + why],
		["its reason, no memory either", {"verdict": "confirmed", "no_recording": why}, "No recording: " + why],
		["neither recording nor memory: an older agent service", {"verdict": "confirmed"}, older],
		["memory, no reason: nothing", {"verdict": "confirmed", "memory": {"updated": true}}, ""],
		["a recording: nothing", {"verdict": "confirmed", "recording": {"video": "C:/r.mp4", "seconds": 3.0}}, ""],
		["a recording and a reason: nothing", {"verdict": "confirmed", "recording": {"video": "C:/r.mp4"}, "no_recording": why}, ""],
		["an empty reason, memory: nothing", {"verdict": "confirmed", "no_recording": "  ", "memory": {}}, ""],
	]
	for c in cases:
		var r := Model.Run.new()
		r.started_ms = Time.get_ticks_msec()
		r.result = c[1].duplicate(true)
		Model.keep_no_recording(r.result)
		check("no recording, live: no line (%s)" % c[0], r.no_recording_line() == "", r.no_recording_line())
		r.ended_ms = r.started_ms + 1000
		check("no recording: %s" % c[0], r.no_recording_line() == c[2], r.no_recording_line())
		# saved with the run (result.json) and shown again from it
		var path := dir.path_join("no_recording_result.json")
		write(path, JSON.stringify(r.result, "  "))
		var back := Model.Run.new()
		back.result = JSON.parse_string(FileAccess.get_file_as_string(path))
		back.ended_ms = 0  # (a run loaded back is over)
		check("no recording, saved and loaded: %s" % c[0], back.no_recording_line() == c[2], [back.no_recording_line(), back.result])
	# a result already decided, decided again: unchanged
	var again := {"verdict": "denied"}
	Model.keep_no_recording(again)
	check("no recording: decided once, the same when decided again", not Model.keep_no_recording(again)
		and again.no_recording == Model.NO_RECORDING_OLDER, again)
	# a run kept before this was decided (no such field, no memory): nothing
	var old := Model.Run.new()
	old.ended_ms = 0
	old.result = {"verdict": "confirmed", "reason": "It opens."}
	check("no recording: a result kept before (no field): no line", old.no_recording_line() == "")
	check("no recording: the words", older == "No recording: an older agent service ran this.", older)


## The result's alerts (docs/protocol.md, Runs): each as sent, trimmed, empty ones and non-texts dropped; kept in its
## result.json and read back from it; none when the result has none; first in the copied report.
func alerts_tests(dir: String) -> void:
	var why := "Autopilot couldn't look at the game's picture: the game runs without a picture (headless: nothing is rendered)."
	var r := Model.Run.new()
	r.id = "a1"
	r.ended_ms = 0
	r.result = {"verdict": "INCONCLUSIVE", "reason": "Can't tell.", "alerts": ["  " + why + " ", "", 7]}
	check("alerts: as sent, trimmed, only texts", r.alerts() == [why], r.alerts())
	var path := dir.path_join("alerts_result.json")
	write(path, JSON.stringify(r.result, "  "))
	var back := Model.Run.new()
	back.result = JSON.parse_string(FileAccess.get_file_as_string(path))
	back.ended_ms = 0  # (a run loaded back is over)
	check("alerts: saved and loaded", back.alerts() == [why], back.alerts())
	var md := Model.report_markdown(r)
	check("alerts: in the copied report, before the reason", md.find("> " + why) >= 0
		and md.find("> " + why) < md.find("## Reason"), md)
	var none := Model.Run.new()
	none.result = {"verdict": "CONFIRMED", "reason": "It opens."}
	check("alerts: none without the field", none.alerts().is_empty() and Model.report_markdown(none).find("> ") < 0)
	none.result["alerts"] = "not a list"
	check("alerts: none when it isn't a list", none.alerts().is_empty())


## The composer's start frame (the words as typed, the agent service reads their kind: docs/protocol.md, "One prompt
## box"), a run_started frame's words, and an exploration asked for as a test shown GAME TESTED (kept so).
func kind_tests() -> void:
	check("run: the words as typed, kind auto, the Time limit", Model.run_of(" Verify that doors open ", 45)
		== {"kind": "auto", "prompt": "Verify that doors open", "time_limit_minutes": 45}, Model.run_of(" Verify that doors open ", 45))
	check("run: no kind read from the words (explore, a question, a goal: all auto)", Model.run_of("Explore the farm", 10).kind == "auto"
		and Model.run_of("Can the player swim?", 10).kind == "auto" and Model.run_of("Reach the castle gate", 10).kind == "auto"
		and not Model.run_of("Explore the farm", 10).has("minutes") and not Model.run_of("Explore the farm", 10).has("brief"))
	check("run_started: an auto run's words as typed", Model.prompt_of_run({"kind": "auto", "prompt": " Lvl_HordeArea test this game "})
		== "Lvl_HordeArea test this game")
	check("run_started: a run of its own kind as before", Model.prompt_of_run({"kind": "verify", "claim": "Verify it"}) == "Verify it"
		and Model.prompt_of_run({"kind": "explore", "brief": "the farm"}) == "Explore the farm")
	var r := Model.Run.new()
	r.id = "20261028-101500"
	r.prompt = "Lvl_HordeArea test this game and find all the bugs"
	r.request = Model.run_of(r.prompt, 30)
	r.started_ms = 1000
	r.ended_ms = 61000
	r.kind = "explore"  # (the run_kind frame's)
	r.result = {"kind": "goal", "reached": true, "notes": ["Two bugs found"], "tested": true}
	check("verdict: an exploration asked for as a test is GAME TESTED", r.verdict() == "GAME TESTED", r.verdict())
	check("verdict: GAME TESTED in EXPLORED's colors", HT.verdict_colors("GAME TESTED") == HT.verdict_colors("EXPLORED"))
	var back = History.from_dict(JSON.parse_string(JSON.stringify(History.to_dict(r))))
	check("history: GAME TESTED kept", back.verdict() == "GAME TESTED" and back.kind == "explore", [back.verdict(), back.kind])
	r.result.erase("tested")
	check("verdict: an exploration not asked as a test stays EXPLORED", r.verdict() == "EXPLORED", r.verdict())
	check("time limit: the default when missing or not a number", Model.time_limit_of(null) == 30 and Model.time_limit_of("soon") == 30)
	check("time limit: kept to 1-600 minutes", Model.time_limit_of(0) == 1 and Model.time_limit_of(9999) == 600
		and Model.time_limit_of(45.7) == 45 and Model.time_limit_of("90") == 90)


## The live page's stage track: at every width, every stage's name is shown whole (wrapped, never cut), each line within
## the room it is given and inside the track; its height holds them all.
func track_tests() -> void:
	HT.theme(1.0)
	var t = W.StageTrack.new()
	var bad := []
	var widths := 0
	var modes := {}
	for attempt in [0, 2, 12]:
		for with_times in [false, true]:
			var times := {}
			if with_times:
				for i in Model.STAGES.size():
					times[i] = 125.0 + i
			t.set_state(Model.STAGE_ROLLOUT, attempt, times, false)
			var names: Array = t.labels()
			for w in range(160, 1401, 7):
				widths += 1
				var lay: Dictionary = t.layout(float(w))
				modes[lay.columns] = true
				var bottom := 0.0
				for i in names.size():
					var it: Dictionary = lay.items[i]
					if " ".join(it.lines) != names[i]:
						bad.append("%d: %s shown as %s" % [w, names[i], it.lines])
					for k in it.lines.size():
						var lw: float = W.StageTrack._caps_w(it.lines[k])
						if lw > it.rooms[k] + 0.5 or it.xs[k] < -0.5 or it.xs[k] + lw > w + 0.5:
							bad.append("%d: \"%s\" %d wide in %d at %d" % [w, it.lines[k], lw, it.rooms[k], it.xs[k]])
						if lay.columns and (it.xs[k] < float(w) / names.size() * i - 0.5 or it.xs[k] + lw > float(w) / names.size() * (i + 1) + 0.5):
							bad.append("%d: \"%s\" outside its column" % [w, it.lines[k]])
						bottom = maxf(bottom, it.ys[k])
					if times.has(i):
						var tw: float = W.StageTrack._mono_w(Model.short_clock(times[i]))
						var tx: float = it.time.x + ((it.time_w - tw) * 0.5 if it.time_w > 0 else 0.0)
						if tx < -0.5 or tx + tw > w + 0.5:
							bad.append("%d: the time of %s at %d" % [w, names[i], tx])
						bottom = maxf(bottom, it.time.y)
				if bottom > lay.height:
					bad.append("%d: %d tall for lines down to %d" % [w, lay.height, bottom])
	check("track: every stage name whole, within its room, at %d widths" % widths, bad.is_empty(), bad.slice(0, 6))
	check("track: names in columns when wide, in rows when narrow", modes.has(true) and modes.has(false), modes)
	t.set_state(Model.STAGE_ROLLOUT, 2, {}, false)
	var narrow: Dictionary = t.layout(300.0)
	check("track: a narrow track (300 px) shows BUILD HARNESS, ROLLOUT · H2, PROCESS RESULT whole",
		not narrow.columns and " ".join(narrow.items[3].lines) == "BUILD HARNESS" and " ".join(narrow.items[4].lines) == "ROLLOUT · H2"
		and " ".join(narrow.items[5].lines) == "PROCESS RESULT", narrow.items)
	t.free()


## The harness attempts' squares: one row (left to right, one top), 10 px each, 4 apart, the finished ones filled and the
## current one outlined, the count after them; narrower, only as many as fit (at least one), never a second line, and
## its minimum one square and the count (it never widens the dock).
func attempts_tests() -> void:
	HT.theme(1.0)
	var a = W.Attempts.new()
	a.set_attempts(4, true)
	var cw: float = HT.font("mono_bold").get_string_size("5", HORIZONTAL_ALIGNMENT_LEFT, -1, 13).x
	a.size = Vector2(400, a.get_minimum_size().y)
	var rects: Array = a.squares()
	var row := rects.size() == 5
	for i in rects.size():
		row = row and is_equal_approx(rects[i].position.y, rects[0].position.y) and is_equal_approx(rects[i].position.x, i * 14.0) and rects[i].size == Vector2(10, 10)
	check("attempts: 5 squares in one row, 10 px, 4 apart, then 5", row and a.count_text() == "5", [rects, a.count_text()])
	check("attempts: its minimum one square and the count", is_equal_approx(a.get_minimum_size().x, 20 + cw), a.get_minimum_size())
	a.size = Vector2(38 + 10 + cw, a.size.y)
	check("attempts: room for 3: 3 of them (one row still)", a.count() == 3, a.count())
	check("attempts: room for 3 of 5: the newest (2 filled, the current outlined last), the count still 5",
		a.filled_shown() == 2 and a.count_text() == "5", [a.filled_shown(), a.count_text()])
	a.size = Vector2(5, a.size.y)
	check("attempts: at least one: the current one", a.count() == 1 and a.filled_shown() == 0, [a.count(), a.filled_shown()])
	a.size = Vector2(2000, a.size.y)
	a.set_attempts(30, false)
	check("attempts: at most MAX, the count says all", a.count() == W.Attempts.MAX and a.count_text() == "30"
		and a.filled_shown() == W.Attempts.MAX, a.count())
	a.set_attempts(29, true)
	check("attempts: past MAX while live: MAX shown, the last the current (outlined), the count 30",
		a.count() == W.Attempts.MAX and a.filled_shown() == W.Attempts.MAX - 1 and a.count_text() == "30", [a.count(), a.filled_shown()])
	a.size = Vector2(4 * 14 + 10 + 10 + HT.font("mono_bold").get_string_size("30", HORIZONTAL_ALIGNMENT_LEFT, -1, 13).x, a.size.y)
	check("attempts: 30, room for 5: the 5 newest (4 filled, the current last), in one row", a.count() == 5
		and a.filled_shown() == 4 and a.squares().all(func(q): return is_equal_approx(q.position.y, a.squares()[0].position.y)),
		[a.count(), a.filled_shown()])
	a.free()


## The removal's command line run for real (OS.create_process, as the dock does) with a stand-in autopilot that writes
## where it ran and what it was given, then exits 3: in the temporary folder, its words, its exit code, its output in
## the log.
func uninstall_live_tests(dir: String) -> void:
	var windows := OS.get_name() == "Windows"
	var bin := dir.path_join("live bin")
	var said := dir.path_join("said.txt")
	var exe := bin.path_join("autopilot.cmd" if windows else "autopilot")
	if windows:
		write(exe, "@echo off\r\necho %%CD%%> \"%s\"\r\necho %%*>> \"%s\"\r\necho removing\r\nexit /b 3\r\n" % [said.replace("/", "\\"), said.replace("/", "\\")])
	else:
		write(exe, "#!/bin/sh\npwd > \"%s\"\necho \"$@\" >> \"%s\"\necho removing\nexit 3\n" % [said, said])
		OS.execute("chmod", ["+x", exe])
	var cwd := dir.path_join("live temp")
	DirAccess.make_dir_recursive_absolute(cwd)
	var log := cwd.path_join("autopilot-uninstall-1.log")
	var pa := Service.uninstall_argv(PackedStringArray([exe]), true, log, OS.get_name(), cwd)
	var p := OS.create_process(pa[0], pa[1])
	var until := Time.get_ticks_msec() + 20000
	while p > 0 and OS.is_process_running(p) and Time.get_ticks_msec() < until:
		OS.delay_msec(50)
	var code := OS.get_process_exit_code(p)
	var lines := FileAccess.get_file_as_string(said).strip_edges().replace("\r", "").split("\n")
	var here := lines[0].replace("\\", "/").trim_suffix("/").to_lower() if lines.size() > 0 else ""
	check("uninstall, run: in the temporary folder, its words, exit code 3, its output in the log", p > 0 and code == 3
		and here == cwd.replace("\\", "/").to_lower() and lines.size() > 1 and lines[1].strip_edges() == "uninstall --yes --keep-memory --log %s" % (("\"%s\"" % log) if windows else log)
		and "removing" in FileAccess.get_file_as_string(log), [p, code, lines, FileAccess.get_file_as_string(log)])


func write(path: String, text: String) -> void:
	DirAccess.make_dir_recursive_absolute(path.get_base_dir())
	var f := FileAccess.open(path, FileAccess.WRITE)
	f.store_string(text)
	f.close()


func prefs_tests(dir: String) -> void:
	var path := Prefs.file(Prefs.PREFERENCES)
	check("settings folder is Autopilot/ in the user settings folder (redirected here)",
		path.replace("\\", "/").begins_with(dir.replace("\\", "/")) and path.ends_with("Autopilot/preferences.json"), path)
	check("no preferences.json: not onboarded, sharing on", not Prefs.onboarded(Prefs.load_preferences()) and Prefs.share_data(Prefs.load_preferences()))
	check("no preferences.json: share for a run is on", Prefs.share_for_run(""))
	write(path, "{not json")
	check("corrupt preferences.json: the defaults", Prefs.load_preferences() == {} and Prefs.share_data(Prefs.load_preferences()))
	write(path, JSON.stringify({"version": 1, "share_data": "no", "other_engine": {"x": 1}}))
	check("share_data not a boolean: sharing on", Prefs.share_data(Prefs.load_preferences()))
	check("save_preferences: OK", Prefs.save_preferences({"onboarded": true, "share_data": false, "access": "own_keys"}) == OK)
	var p := Prefs.load_preferences()
	check("save_preferences merges (other keys kept, version 1)", typeof(p.get("other_engine")) == TYPE_DICTIONARY and p.other_engine.get("x") == 1 and p.version == 1
		and p.onboarded == true and p.share_data == false and p.access == "own_keys", p)
	check("no temporary file left", DirAccess.get_files_at(path.get_base_dir()).size() == 1, DirAccess.get_files_at(path.get_base_dir()))
	check("share_data false: a run doesn't share", not Prefs.share_for_run(""))
	check("-PlaytestShare=1 overrides the file", Prefs.share_for_run("1"))
	Prefs.save_preferences({"share_data": true})
	check("-PlaytestShare=0 overrides the file", not Prefs.share_for_run("0") and Prefs.share_for_run(""))
	check("merged: null removes a key", not Prefs.merged({"access": "aura", "a": 1}, {"access": null}).has("access"))


func env_tests(dir: String) -> void:
	var text := "# my keys\nOTHER=1\nexport ANTHROPIC_API_KEY=old\nTYPESAFE_API_KEY=old2\nTYPESAFE_API_KEY=dup\n\nLAST=x"
	var out := Prefs.merge_env(text, {"ANTHROPIC_API_KEY": "sk-new", "TYPESAFE_API_KEY": ""})
	check("merge_env replaces the key's line, keeps the rest", out == "# my keys\nOTHER=1\nANTHROPIC_API_KEY=sk-new\nTYPESAFE_API_KEY=old2\nTYPESAFE_API_KEY=dup\n\nLAST=x\n", out.c_escape())
	out = Prefs.merge_env("A=1\r\nTYPESAFE_API_KEY=a\r\nTYPESAFE_API_KEY=b\r\n", {"TYPESAFE_API_KEY": " ts ", "ANTHROPIC_API_KEY": "an"})
	check("merge_env: one line per key, new keys added at the end", out == "A=1\nTYPESAFE_API_KEY=ts\nANTHROPIC_API_KEY=an\n", out.c_escape())
	check("merge_env of nothing", Prefs.merge_env("", {"TYPESAFE_API_KEY": "x"}) == "TYPESAFE_API_KEY=x\n")
	var path := Prefs.file(Prefs.ENV_FILE)
	DirAccess.remove_absolute(path)
	check("save_env_keys with no keys writes nothing", Prefs.save_env_keys({"TYPESAFE_API_KEY": "", "ANTHROPIC_API_KEY": " "}) == OK
		and not FileAccess.file_exists(path))
	write(path, "KEEP=me\nANTHROPIC_API_KEY=old\n")
	check("save_env_keys: OK", Prefs.save_env_keys({"TYPESAFE_API_KEY": "ts-1", "ANTHROPIC_API_KEY": "an-1"}) == OK)
	check("autopilot.env merged", FileAccess.get_file_as_string(path) == "KEEP=me\nANTHROPIC_API_KEY=an-1\nTYPESAFE_API_KEY=ts-1\n",
		FileAccess.get_file_as_string(path).c_escape())
	llm_provider_tests(path)


## The BYOK Coding Agent and LLM provider: autopilot.env's AUTOPILOT_AGENT, AUTOPILOT_OPENCODE_PROVIDER and the key's name, the
## names shown, the account frame's coding_agent and llm_provider on the Settings page's Account corner.
func llm_provider_tests(path: String) -> void:
	check("llm_provider: one of the six ids, anything else anthropic", Prefs.llm_provider("openai") == "openai"
		and Prefs.llm_provider(" OpenAI ") == "openai" and Prefs.llm_provider("anthropic") == "anthropic"
		and Prefs.llm_provider("opencode") == "opencode" and Prefs.llm_provider("openrouter") == "openrouter"
		and Prefs.llm_provider("deepseek") == "deepseek" and Prefs.llm_provider("google") == "google"
		and Prefs.llm_provider("gemini") == "anthropic" and Prefs.llm_provider("") == "anthropic" and Prefs.llm_provider(null) == "anthropic")
	check("llm_name: the six names, another id as it is, none: Anthropic", Prefs.llm_name("anthropic") == "Anthropic"
		and Prefs.llm_name("openai") == "OpenAI" and Prefs.llm_name("opencode") == "OpenCode Zen" and Prefs.llm_name("openrouter") == "OpenRouter"
		and Prefs.llm_name("deepseek") == "DeepSeek" and Prefs.llm_name("google") == "Gemini"
		and Prefs.llm_name("mistral") == "mistral" and Prefs.llm_name(null) == "Anthropic" and Prefs.llm_name("") == "Anthropic")
	check("each provider's key name", Prefs.LLM_PROVIDERS.map(func(p): return Prefs.LLM_KEY_NAMES[p]) == ["ANTHROPIC_API_KEY",
		"OPENAI_API_KEY", "OPENCODE_API_KEY", "OPENROUTER_API_KEY", "DEEPSEEK_API_KEY", "GEMINI_API_KEY"])
	check("coding_agent: claude, codex or opencode, anything else opencode", Prefs.coding_agent("claude") == "claude"
		and Prefs.coding_agent(" Codex ") == "codex" and Prefs.coding_agent("opencode") == "opencode"
		and Prefs.coding_agent("cursor") == "opencode" and Prefs.coding_agent("") == "opencode" and Prefs.coding_agent(null) == "opencode")
	check("agent_name: Claude Code, Codex, OpenCode, another id as it is, none: OpenCode", Prefs.agent_name("claude") == "Claude Code"
		and Prefs.agent_name("codex") == "Codex" and Prefs.agent_name("opencode") == "OpenCode" and Prefs.agent_name("cursor") == "cursor"
		and Prefs.agent_name(null) == "OpenCode" and Prefs.agent_name("") == "OpenCode")
	check("agent_provider: Anthropic for Claude Code, OpenAI for Codex, OpenCode's own", Prefs.agent_provider("claude", "openrouter") == "anthropic"
		and Prefs.agent_provider("codex", "deepseek") == "openai" and Prefs.agent_provider("opencode", "openrouter") == "openrouter"
		and Prefs.agent_provider("opencode", "gemini") == "anthropic" and Prefs.agent_provider("cursor", "google") == "google")
	check("env_value: the last line setting it, export allowed, comments not", Prefs.env_value(
		"# AUTOPILOT_OPENCODE_PROVIDER=x\nAUTOPILOT_OPENCODE_PROVIDER=anthropic\r\nexport AUTOPILOT_OPENCODE_PROVIDER = openai \nA=1", "AUTOPILOT_OPENCODE_PROVIDER") == "openai"
		and Prefs.env_value("A=1", "AUTOPILOT_OPENCODE_PROVIDER") == "" and Prefs.env_value("K=a=b", "K") == "a=b")
	write(path, "KEEP=me\nANTHROPIC_API_KEY=an-old\nTYPESAFE_API_KEY=ts-old\n")
	check("saved_llm_provider: none named, anthropic", Prefs.saved_llm_provider() == "anthropic")
	check("saved_coding_agent: none named, opencode", Prefs.saved_coding_agent() == "opencode")
	check("save_byok OpenCode with OpenAI: OK", Prefs.save_byok("", "opencode", "openai", "sk-oa") == OK)
	var text := FileAccess.get_file_as_string(path)
	check("save_byok OpenCode with OpenAI: the agent, the provider and OPENAI_API_KEY written, the Anthropic key and the decision key kept", text
		== "KEEP=me\nANTHROPIC_API_KEY=an-old\nTYPESAFE_API_KEY=ts-old\nAUTOPILOT_AGENT=opencode\nAUTOPILOT_OPENCODE_PROVIDER=openai\nOPENAI_API_KEY=sk-oa\n", text.c_escape())
	check("saved_llm_provider: openai", Prefs.saved_llm_provider() == "openai")
	check("save_byok OpenCode with Anthropic, the keys empty: OK", Prefs.save_byok(" ", "opencode", "anthropic", "") == OK)
	text = FileAccess.get_file_as_string(path)
	check("save_byok with empty keys: still the agent and the provider, every key line kept", text
		== "KEEP=me\nANTHROPIC_API_KEY=an-old\nTYPESAFE_API_KEY=ts-old\nAUTOPILOT_AGENT=opencode\nAUTOPILOT_OPENCODE_PROVIDER=anthropic\nOPENAI_API_KEY=sk-oa\n", text.c_escape())
	Prefs.save_byok("ts-2", "opencode", "anthropic", "an-2")
	text = FileAccess.get_file_as_string(path)
	check("save_byok OpenCode with Anthropic: ANTHROPIC_API_KEY replaced, the OpenAI key kept", text
		== "KEEP=me\nANTHROPIC_API_KEY=an-2\nTYPESAFE_API_KEY=ts-2\nAUTOPILOT_AGENT=opencode\nAUTOPILOT_OPENCODE_PROVIDER=anthropic\nOPENAI_API_KEY=sk-oa\n", text.c_escape())
	Prefs.save_byok("", "opencode", "google", "gm-1")
	text = FileAccess.get_file_as_string(path)
	check("save_byok OpenCode with Gemini: AUTOPILOT_OPENCODE_PROVIDER=google, GEMINI_API_KEY", text
		== "KEEP=me\nANTHROPIC_API_KEY=an-2\nTYPESAFE_API_KEY=ts-2\nAUTOPILOT_AGENT=opencode\nAUTOPILOT_OPENCODE_PROVIDER=google\nOPENAI_API_KEY=sk-oa\nGEMINI_API_KEY=gm-1\n", text.c_escape())
	Prefs.save_byok("", "claude", "google", "an-3")
	text = FileAccess.get_file_as_string(path)
	check("save_byok Claude Code: AUTOPILOT_AGENT=claude, the key under ANTHROPIC_API_KEY, OpenCode's provider line kept", text
		== "KEEP=me\nANTHROPIC_API_KEY=an-3\nTYPESAFE_API_KEY=ts-2\nAUTOPILOT_AGENT=claude\nAUTOPILOT_OPENCODE_PROVIDER=google\nOPENAI_API_KEY=sk-oa\nGEMINI_API_KEY=gm-1\n", text.c_escape())
	check("saved_coding_agent: claude; saved_llm_provider still OpenCode's", Prefs.saved_coding_agent() == "claude" and Prefs.saved_llm_provider() == "google")
	Prefs.save_byok("", "codex", "anthropic", "")
	text = FileAccess.get_file_as_string(path)
	check("save_byok Codex with the key empty: AUTOPILOT_AGENT=codex, every key line kept", text
		== "KEEP=me\nANTHROPIC_API_KEY=an-3\nTYPESAFE_API_KEY=ts-2\nAUTOPILOT_AGENT=codex\nAUTOPILOT_OPENCODE_PROVIDER=google\nOPENAI_API_KEY=sk-oa\nGEMINI_API_KEY=gm-1\n", text.c_escape())
	DirAccess.remove_absolute(path)
	check("save_byok OpenCode with OpenAI, no autopilot.env and no keys: the agent and the provider written", Prefs.save_byok("", "opencode", "openai", "") == OK
		and FileAccess.get_file_as_string(path) == "AUTOPILOT_AGENT=opencode\nAUTOPILOT_OPENCODE_PROVIDER=openai\n", FileAccess.get_file_as_string(path).c_escape())
	DirAccess.remove_absolute(path)
	check("save_byok Codex, no autopilot.env: the agent and OPENAI_API_KEY, no provider line", Prefs.save_byok("", "codex", "", "sk-cx") == OK
		and FileAccess.get_file_as_string(path) == "AUTOPILOT_AGENT=codex\nOPENAI_API_KEY=sk-cx\n", FileAccess.get_file_as_string(path).c_escape())
	DirAccess.remove_absolute(path)
	check("save_byok of an unknown agent and provider: OpenCode with Anthropic", Prefs.save_byok("", "cursor", "gemini", "k-1") == OK
		and FileAccess.get_file_as_string(path) == "AUTOPILOT_AGENT=opencode\nAUTOPILOT_OPENCODE_PROVIDER=anthropic\nANTHROPIC_API_KEY=k-1\n",
		FileAccess.get_file_as_string(path).c_escape())
	DirAccess.remove_absolute(path)
	check("saved_llm_provider and saved_coding_agent: no autopilot.env, anthropic and opencode", Prefs.saved_llm_provider() == "anthropic"
		and Prefs.saved_coding_agent() == "opencode")
	# the account frame's coding_agent and llm_provider, as the Settings page's Account corner keeps them
	var keys := {"decision": "••••a91f", "llm": "••••3c2e"}
	var v := SettingsPage.account_view({"type": "account", "mode": "own_keys", "keys": keys, "coding_agent": "opencode", "llm_provider": "openai"}, null, false)
	check("account frame: OpenCode, llm_provider openai", v.kind == "own_keys" and v.coding_agent == "opencode" and v.llm_provider == "openai"
		and v["keys"] == ["••••a91f", "••••3c2e"], v)
	v = SettingsPage.account_view({"type": "account", "mode": "own_keys", "keys": keys}, null, false)
	check("account frame without coding_agent or llm_provider (an older agent service): OpenCode, anthropic", v.coding_agent == "opencode"
		and v.llm_provider == "anthropic", v)
	v = SettingsPage.account_view({"type": "account", "mode": "own_keys", "keys": keys, "coding_agent": "codex"}, null, false)
	check("account frame: Codex without llm_provider: openai", v.coding_agent == "codex" and v.llm_provider == "openai", v)
	v = SettingsPage.account_view({"type": "account", "mode": "own_keys", "keys": keys, "coding_agent": "cursor", "llm_provider": "mistral"}, null, false)
	check("account frame: another coding_agent and llm_provider kept as they are", v.coding_agent == "cursor" and v.llm_provider == "mistral", v)
	v = SettingsPage.account_view({"type": "account", "mode": "own_keys", "keys": keys, "coding_agent": 1, "llm_provider": 3}, null, false)
	check("account frame: coding_agent and llm_provider not strings, opencode and anthropic", v.coding_agent == "opencode"
		and v.llm_provider == "anthropic", v)


func announced_tests(dir: String) -> void:
	var path := Prefs.file(Prefs.AURA_BRAIN_FILE)
	write(path, JSON.stringify({"url": "ws://127.0.0.1:9123/v1/bridge", "pid": OS.get_process_id()}))
	check("aura-brain.json of a running process: its url", Prefs.aura_service_url() == "ws://127.0.0.1:9123/v1/bridge")
	write(path, JSON.stringify({"url": "ws://example.com:9123/v1/bridge", "pid": OS.get_process_id()}))
	check("an address not on this machine is ignored", Prefs.aura_service_url() == "")
	write(path, JSON.stringify({"url": "ws://127.0.0.1:9123/v1/bridge", "pid": 4000000}))
	check("a process that ended is ignored", Prefs.aura_service_url() == "")
	check("brain.json missing: no url", Prefs.announced_url(Prefs.OWN_BRAIN_FILE) == "")
	DirAccess.remove_absolute(path)


func usage_tests(dir: String) -> void:
	var a := [{"kind": "llm", "model": "claude-opus-5-5", "calls": 12, "input": 30120, "output": 8211, "cache_read": 912000, "cache_write": 41000},
		{"kind": "decision", "model": "dm-1", "calls": 2310, "input": 1450000, "output": 4620}, "junk"]
	var b := [{"kind": "decision", "model": "dm-1", "calls": 10.0, "input": 50, "output": 5},
		{"kind": "llm", "model": "claude-opus-5-5", "calls": 1, "input": 10, "output": 1, "cache_read": -3, "cache_write": null},
		{"kind": "llm", "model": "other", "calls": 1, "input": 1, "output": 1}]
	var pa := Usage.parse(a)
	check("parse: objects only, missing cache counts are 0", pa.size() == 2 and pa[1].cache_read == 0 and pa[1].cache_write == 0, pa)
	var t := Usage.total([a, b])
	check("total: per kind and model, LLM rows first", t.size() == 3 and t[0].model == "claude-opus-5-5" and t[1].model == "other"
		and t[2].kind == "decision", t)
	check("total: sums", t[0].calls == 13 and t[0].input == 30130 and t[0].cache_read == 912000 and t[2].calls == 2320 and t[2].input == 1450050, t)
	check("tokens", Usage.tokens(a, "llm") == [30120, 8211] and Usage.tokens(b, "decision") == [50, 5])
	check("labels: the model's id only", Usage.label(t[0]) == "claude-opus-5-5" and Usage.label(t[2]) == "dm-1")
	check("thousands", Usage.thousands(0) == "0" and Usage.thousands(999) == "999" and Usage.thousands(1000) == "1,000"
		and Usage.thousands(1450000) == "1,450,000" and Usage.thousands(-12345) == "-12,345")
	check("compact", Usage.compact(950) == "950" and Usage.compact(1234) == "1.2k" and Usage.compact(3400000) == "3.4M"
		and Usage.compact(123456) == "123k", [Usage.compact(1234), Usage.compact(3400000), Usage.compact(123456)])
	var path := dir.path_join("usage").path_join(Usage.FILE_NAME)
	check("no file: no runs", Usage.load_runs(path) == [])
	write(path, "[[[")
	check("corrupt file: no runs", Usage.load_runs(path) == [])
	write(path, JSON.stringify({"version": 1, "runs": [{"id": "old", "started_unix": 5, "kind": "verify", "prompt": "p"}, {"no": "id"}]}))
	var old := Usage.load_runs(path)
	check("a saved run without usage still loads", old.size() == 1 and old[0].usage == [] and old[0].started_unix == 5.0, old)
	var runs := Usage.upsert(old, {"id": "r1", "started_unix": 10.0, "kind": "goal", "prompt": "x", "verdict": "LIVE", "usage": Usage.parse(a), "final": false})
	runs = Usage.upsert(runs, {"id": "r1", "started_unix": 10.0, "kind": "goal", "prompt": "x", "verdict": "GOAL HIT", "usage": Usage.parse(b), "final": true})
	check("upsert replaces the run of the same id", runs.size() == 2 and runs[1].verdict == "GOAL HIT" and runs[1].usage.size() == 3)
	check("save_runs: OK", Usage.save_runs(path, runs) == OK)
	var back := Usage.load_runs(path)
	check("saved runs load back", back.size() == 2 and back[1].id == "r1" and back[1].final and Usage.total([back[1].usage]).size() == 3, back)
	check("a saved run from before bills loads with no bill", back[0].bill == {} and back[1].bill == {}, back)
	bill_tests(dir)


## The Autopilot bill (Aura only): the bill frame, the account frame's bill, the money and the dates as shown, kept with the
## runs' usage.
func bill_tests(dir: String) -> void:
	var b := Usage.parse_bill({"type": "bill", "usd": 0.593177, "billed": true})
	check("parse_bill", b == {"usd": 0.593177, "billed": true}, b)
	check("parse_bill: billed false keeps its reason", Usage.parse_bill({"usd": 1, "billed": false, "reason": "later"})
		== {"usd": 1.0, "billed": false, "reason": "later"})
	check("parse_bill: no amount, not an object, or a negative", Usage.parse_bill({"billed": true}) == {} and Usage.parse_bill(null) == {}
		and Usage.parse_bill({"usd": "1"}) == {} and Usage.parse_bill({"usd": -2, "billed": true}).usd == 0.0)
	check("billed: the amount of a billed bill, -1 otherwise", Usage.billed(b) == 0.593177 and Usage.billed({}) < 0.0
		and Usage.billed({"usd": 3, "billed": false}) < 0.0 and Usage.billed(null) < 0.0)
	check("money: 2 decimals", Usage.money(0.593177) == "$0.59" and Usage.money(12.346) == "$12.35" and Usage.money(1.0) == "$1.00"
		and Usage.money(0.01) == "$0.01" and Usage.money(1234567.891) == "$1,234,567.89",
		[Usage.money(0.593177), Usage.money(12.346), Usage.money(1234567.891)])
	check("money: below a cent", Usage.money(0.004) == "< $0.01" and Usage.money(0.0099) == "< $0.01" and Usage.money(0.0) == "$0.00")
	var entries := [{"bill": {"usd": 0.5, "billed": true}}, {"bill": {"usd": 9.0, "billed": false}}, {"bill": {}}, {},
		{"bill": {"usd": 0.25, "billed": true}}, "junk"]
	var tot := Usage.bill_total(entries)
	check("bill_total: billed bills only, and how many", absf(tot[0] - 0.75) < 1e-9 and tot[1] == 2, tot)
	check("bill_total: none", Usage.bill_total([{}, {"bill": {}}]) == [0.0, 0])
	var ab := Usage.parse_account_bill({"usd": 12.34, "runs": 38, "from": "2026-10-01", "to": "2026-10-31"})
	check("parse_account_bill", ab == {"usd": 12.34, "runs": 38, "from": "2026-10-01", "to": "2026-10-31"}, ab)
	check("parse_account_bill: null or no amount", Usage.parse_account_bill(null) == {} and Usage.parse_account_bill({"runs": 3}) == {})
	var oc := Usage.parse_out_of_credits({"message": "Your team is out of Aura credits. Add credits at https://aura.example/account/pricing to keep playtesting.",
		"url": "https://aura.example/account/pricing"})
	check("parse_out_of_credits: Aura's words and the page", oc == {"message": "Your team is out of Aura credits. Add credits at https://aura.example/account/pricing to keep playtesting.",
		"url": "https://aura.example/account/pricing"}, oc)
	oc = Usage.parse_out_of_credits({"message": "Your team is out of Aura credits.", "url": null})
	check("parse_out_of_credits: url null: no page", oc == {"message": "Your team is out of Aura credits.", "url": ""}, oc)
	oc = Usage.parse_out_of_credits({"message": "Out.", "url": "file:///C:/x.exe"})
	check("parse_out_of_credits: not a web page: not opened", oc == {"message": "Out.", "url": ""}, oc)
	check("parse_out_of_credits: null, absent (an older agent service) or no words: none", Usage.parse_out_of_credits(null) == {}
		and Usage.parse_out_of_credits({}.get("out_of_credits")) == {} and Usage.parse_out_of_credits({"url": "https://aura.example"}) == {}
		and Usage.parse_out_of_credits({"message": "  "}) == {} and Usage.parse_out_of_credits("out") == {})
	check("period_line: the billing period's dates and amount", Usage.period_line(ab) == "Billing period (Oct 1 – Oct 31): $12.34 USD"
		and Usage.period_line({"usd": 0.001, "runs": 1}) == "Billing period: < $0.01 USD", Usage.period_line(ab))
	check("period_dates", Usage.period_dates("2026-10-01", "2026-10-31") == "Oct 1 – Oct 31"
		and Usage.period_dates("2026-12-15", "2027-01-14") == "Dec 15, 2026 – Jan 14, 2027"
		and Usage.period_dates("2026-10-01T00:00:00Z", "2026-10-31") == "Oct 1 – Oct 31"
		and Usage.period_dates("", "2026-10-31") == "" and Usage.period_dates("2026-13-01", "2026-10-31") == "",
		[Usage.period_dates("2026-10-01", "2026-10-31"), Usage.period_dates("2026-12-15", "2027-01-14")])
	# kept with the usage
	var path := dir.path_join("usage-bill").path_join(Usage.FILE_NAME)
	var runs := Usage.upsert([], {"id": "a", "started_unix": 1.0, "kind": "goal", "prompt": "x", "verdict": "GOAL HIT",
		"usage": [], "final": true, "bill": b})
	runs = Usage.upsert(runs, {"id": "k", "started_unix": 2.0, "kind": "goal", "prompt": "y", "verdict": "GOAL HIT",
		"usage": Usage.parse([{"kind": "llm", "model": "m", "calls": 1, "input": 1, "output": 1}]), "final": true, "bill": {}})
	check("save_runs with bills: OK", Usage.save_runs(path, runs) == OK)
	var back := Usage.load_runs(path)
	check("bills load back (none for a BYOK run)", back.size() == 2 and back[0].bill == b and back[1].bill == {}, back)
	write(path, JSON.stringify({"version": 1, "runs": [{"id": "x", "bill": {"usd": "oops"}}, {"id": "y", "bill": "junk"}]}))
	back = Usage.load_runs(path)
	check("a bill that doesn't parse loads as none", back.size() == 2 and back[0].bill == {} and back[1].bill == {}, back)


func media_tests() -> void:
	check("headless: no picture", Media.no_picture() != "" and not Media.can_capture(), Media.no_picture())
	check("fit: never up, longer side capped", Media.fit(Vector2i(1920, 1080), 960) == Vector2i(960, 540)
		and Media.fit(Vector2i(640, 480), 960) == Vector2i(640, 480) and Media.fit(Vector2i(1080, 1920), 960) == Vector2i(540, 960))
	var ft = JSON.parse_string(Media.frame_text({"n": 3, "t": 1.25, "wall": 1790000000.123, "width": 2, "height": 1}, "QUJD"))
	check("frame text", typeof(ft) == TYPE_DICTIONARY and ft.type == "frame" and ft.n == 3 and ft.jpeg == "QUJD" and ft.width == 2, ft)
	var rt = JSON.parse_string(Media.response_text(17, {"width": 2, "height": 1, "t": null}, "QUJD", null))
	check("capture response text", typeof(rt) == TYPE_DICTIONARY and rt.type == "response" and rt.id == 17 and rt.ok == true
		and rt.result.jpeg == "QUJD" and rt.result.width == 2 and rt.t == null, rt)
	# a read-back RGBA8 picture, encoded on a worker as the recording does: scaled, a JPEG, the frame's text
	var img := Image.create(1280, 720, false, Image.FORMAT_RGBA8)
	img.fill(Color(0.2, 0.6, 0.9))
	var job := Media.Job.new()
	job.w = 1280
	job.h = 720
	job.image_format = Image.FORMAT_RGBA8
	job.data = img.get_data()
	job.max_dim = 640
	job.quality = 70
	job.n = 4
	job.t = 2.5
	job.as_text = true
	var task := WorkerThreadPool.add_task(Media._encode.bind(job))
	WorkerThreadPool.wait_for_task_completion(task)
	var decoded := Image.new()
	var ok := job.failed == "" and decoded.load_jpg_from_buffer(job.jpeg) == OK
	check("encode on a worker: a JPEG scaled to max_dim", ok and decoded.get_size() == Vector2i(640, 360), [job.failed, decoded.get_size()])
	var c := decoded.get_pixel(320, 180)
	check("encode keeps the colours", absf(c.r - 0.2) < 0.05 and absf(c.g - 0.6) < 0.05 and absf(c.b - 0.9) < 0.05, c)
	var fr = JSON.parse_string(job.text)
	check("encode makes the frame's text", typeof(fr) == TYPE_DICTIONARY and fr.n == 4 and fr.width == 640
		and Marshalls.base64_to_raw(fr.jpeg) == job.jpeg, job.text.substr(0, 80))
	# a half-float (HDR 2D) picture: linear, made sRGB
	var h := Image.create(64, 64, false, Image.FORMAT_RGBAH)
	h.fill(Color(0.2, 0.2, 0.2))
	var job2 := Media.Job.new()
	job2.image = h
	job2.linear = true
	job2.max_dim = 960
	job2.quality = 90
	job2.still = true
	job2.id = 9
	job2.as_text = true
	Media._encode(job2)
	var d2 := Image.new()
	check("a linear picture is made sRGB", job2.failed == "" and d2.load_jpg_from_buffer(job2.jpeg) == OK
		and absf(d2.get_pixel(32, 32).r - 0.48) < 0.06, d2.get_pixel(32, 32) if not d2.is_empty() else job2.failed)
	var rr = JSON.parse_string(job2.text)
	check("a still's response text", typeof(rr) == TYPE_DICTIONARY and rr.id == 9 and rr.result.width == 64, job2.text.substr(0, 80))
	# a job read back, moved on as the game thread does: its encode goes to a worker, and it is over once that is done
	var media := Media.new(null)
	var job4 := Media.Job.new()
	job4.w = 320
	job4.h = 200
	job4.image_format = Image.FORMAT_RGBA8
	job4.max_dim = 960
	job4.as_text = true
	job4.started = Time.get_ticks_msec() / 1000.0
	job4.set_read(Image.create(320, 200, false, Image.FORMAT_RGBA8).get_data())
	var over: bool = media._advance(job4, job4.started)
	var started_task := job4.task >= 0
	var tries := 0
	while not over and tries < 500:
		OS.delay_msec(2)
		over = media._advance(job4, job4.started)
		tries += 1
	check("a read-back job is encoded on a worker, then over", started_task and over and job4.failed == ""
		and job4.width == 320 and str((JSON.parse_string(job4.text) as Dictionary).get("type")) == "frame",
		[started_task, over, job4.failed, job4.width])
	var job5 := Media.Job.new()
	job5.started = 0.0
	check("a readback that never comes is dropped", media._advance(job5, Media.STALE_SECONDS + 1.0) and job5.failed != "")
	var job3 := Media.Job.new()
	job3.w = 100
	job3.h = 100
	job3.image_format = Image.FORMAT_RGBA8
	job3.data = PackedByteArray([1, 2, 3])
	Media._encode(job3)
	check("a short read-back is a failure, not a crash", job3.failed != "" and job3.jpeg.is_empty(), job3.failed)


## The agent service the addon starts (lib/service.gd): finding autopilot, the command lines (never run here: the launcher
## is a stand-in that keeps them), brain.json naming the process started, the logs, the installer.
func service_tests(dir: String) -> void:
	check("split_command: quotes keep spaces", Service.split_command("  \"C:\\Program Files\\py\\python.exe\" -m  autopilot ")
		== PackedStringArray(["C:\\Program Files\\py\\python.exe", "-m", "autopilot"]), Service.split_command("\"C:\\Program Files\\py\\python.exe\" -m autopilot"))
	check("split_command: empty", Service.split_command("   ").is_empty())
	var wc := Service.default_candidates("Windows", "C:\\a;;\"C:\\b c\"", "C:\\Users\\u")
	check("candidates on Windows: PATH folders, then the installer's place", wc.size() == 7 and wc[0] == "C:\\a/autopilot.exe"
		and wc[3] == "C:\\b c/autopilot.exe" and wc[-1].replace("\\", "/") == "C:/Users/u/.local/bin/autopilot.exe", wc)
	var lc := Service.default_candidates("Linux", "/usr/bin:/opt/x", "/home/u")
	check("candidates elsewhere", lc == ["/usr/bin/autopilot", "/opt/x/autopilot", "/home/u/.local/bin/autopilot"], lc)
	var bin1 := dir.path_join("bin1")
	var bin2 := dir.path_join("bin2")
	write(bin2.path_join("autopilot.exe"), "")
	write(dir.path_join("home/.local/bin/autopilot.exe"), "")
	var places := [bin1.path_join("autopilot.exe"), bin2.path_join("autopilot.exe"), dir.path_join("home/.local/bin/autopilot.exe")]
	check("find: the first that exists", Service.find_command("", places) == PackedStringArray([bin2.path_join("autopilot.exe")]))
	check("find: the setting comes first", Service.find_command("py -m autopilot", places) == PackedStringArray(["py", "-m", "autopilot"]))
	check("find: none is not installed", Service.find_command("", [bin1.path_join("autopilot.exe")]).is_empty())

	var w := Service.start_argv(PackedStringArray(["C:\\Program Files\\autopilot\\autopilot.exe"]), 4242, "C:/Users/u/AppData/Local/Autopilot/logs/agent-service-1.log", "Windows")
	check("start on Windows: cmd /s /c, serve on a free port, ends with the editor, output to the log",
		w[0] == "cmd.exe" and w[1] == PackedStringArray(["/d", "/s", "/c",
		"set PYTHONUNBUFFERED=1& \"C:\\Program Files\\autopilot\\autopilot.exe\" serve --port 0 --exit-with 4242 >> C:\\Users\\u\\AppData\\Local\\Autopilot\\logs\\agent-service-1.log 2>&1"]), w)
	var w2 := Service.start_argv(PackedStringArray(["C:\\py.exe", "-m", "autopilot"]), 7, "C:/a b/x.log", "Windows")
	check("start on Windows: the setting's command, a log path with a space quoted",
		w2[1][3] == "set PYTHONUNBUFFERED=1& C:\\py.exe -m autopilot serve --port 0 --exit-with 7 >> \"C:\\a b\\x.log\" 2>&1", w2)
	var u := Service.start_argv(PackedStringArray(["/home/u/.local/bin/autopilot"]), 4242, "/home/u/.config/Autopilot/logs/a b.log", "macOS")
	check("start elsewhere: sh, exec (the same process), output to the log", u[0] == "/bin/sh" and u[1] == PackedStringArray(["-c",
		"log=\"$1\"; shift; PYTHONUNBUFFERED=1 exec \"$@\" >> \"$log\" 2>&1", "sh", "/home/u/.config/Autopilot/logs/a b.log", "/home/u/.local/bin/autopilot",
		"serve", "--port", "0", "--exit-with", "4242"]), u)
	var iw := Service.install_argv("C:/game/addons/autopilot/agent_service", "C:/l/install-1.log", "Windows")
	check("install on Windows: PowerShell runs install.ps1", iw[0] == "cmd.exe" and iw[1][3] ==
		"set PYTHONUNBUFFERED=1& powershell -NoProfile -ExecutionPolicy Bypass -File C:\\game\\addons\\autopilot\\agent_service\\install.ps1 >> C:\\l\\install-1.log 2>&1", iw)
	var iu := Service.install_argv("/g/addons/autopilot/agent_service", "/l/install-1.log", "Linux")
	check("install elsewhere: sh install.sh", iu[0] == "/bin/sh" and iu[1].slice(3) == PackedStringArray(["/l/install-1.log", "sh",
		"/g/addons/autopilot/agent_service/install.sh"]), iu)
	var rw := Service.uninstall_argv(PackedStringArray(["C:\\Users\\u\\.local\\bin\\autopilot.exe"]), true, "C:/T/autopilot-uninstall-1.log", "Windows", "C:/Users/u/AppData/Local/Temp")
	check("uninstall on Windows: in the temporary folder, --yes --keep-memory, the log for the command's removal too", rw[0] == "cmd.exe"
		and rw[1] == PackedStringArray(["/d", "/s", "/c", "cd /d C:\\Users\\u\\AppData\\Local\\Temp& set PYTHONUNBUFFERED=1& "
		+ "C:\\Users\\u\\.local\\bin\\autopilot.exe uninstall --yes --keep-memory --log C:/T/autopilot-uninstall-1.log >> C:\\T\\autopilot-uninstall-1.log 2>&1"]), rw)
	var rw2 := Service.uninstall_argv(PackedStringArray(["C:\\py.exe", "-m", "autopilot"]), false, "C:/a b/u.log", "Windows", "C:/Temp dir")
	check("uninstall on Windows: everything learned removed too, paths with spaces quoted", rw2[1][3] == "cd /d \"C:\\Temp dir\"& set PYTHONUNBUFFERED=1& "
		+ "C:\\py.exe -m autopilot uninstall --yes --log \"C:/a b/u.log\" >> \"C:\\a b\\u.log\" 2>&1", rw2)
	var ru := Service.uninstall_argv(PackedStringArray(["/home/u/.local/bin/autopilot"]), false, "/tmp/u.log", "Linux", "/tmp")
	check("uninstall elsewhere: sh, cd first, exec", ru[0] == "/bin/sh" and ru[1] == PackedStringArray(["-c",
		"cd \"$1\" || exit 1; log=\"$2\"; shift 2; PYTHONUNBUFFERED=1 exec \"$@\" >> \"$log\" 2>&1", "sh", "/tmp", "/tmp/u.log",
		"/home/u/.local/bin/autopilot", "uninstall", "--yes", "--log", "/tmp/u.log"]), ru)
	var base := dir.path_join("settings_base")
	for name in ["Autopilot/preferences.json", "Autopilot/autopilot.env", "Autopilot/.hidden", "Autopilot/logs/a.log",
			"Autopilot/memory/Orchard/notes.md", "Autopilot/aura-brain.json"]:
		write(base.path_join(name), "x")
	var gone := Service.remove_settings(base, ["memory", "aura-brain.json"])
	check("removed here: all but memory/ and aura-brain.json", Service._names(base.path_join("Autopilot")) == ["aura-brain.json", "memory"]
		and FileAccess.file_exists(base.path_join("Autopilot/memory/Orchard/notes.md"))
		and gone.failed.is_empty() and gone.kept.size() == 2 and gone.removed.size() == 4, [gone, Service._names(base.path_join("Autopilot"))])
	gone = Service.remove_settings(base, [])
	check("removed here, nothing kept: the folder too", not DirAccess.dir_exists_absolute(base.path_join("Autopilot"))
		and gone.failed.is_empty(), gone)

	var url := "ws://127.0.0.1:50123/v1/bridge"
	check("brain.json: the pid started", Service.announced_by({"url": url, "pid": 900}, 900, 1000.0))
	check("brain.json: another pid announced since the start (the shell between)", Service.announced_by({"url": url, "pid": 901, "started": 1000.5}, 900, 1000.0))
	check("brain.json: announced before the start is not it", not Service.announced_by({"url": url, "pid": 901, "started": 990.0}, 900, 1000.0))
	check("brain.json: the stale pid is not it", not Service.announced_by({"url": url, "pid": 55, "started": 1001.0}, 900, 1000.0, 55))
	check("brain.json: not on this machine", not Service.announced_by({"url": "ws://example.com:1/v1/bridge", "pid": 900}, 900, 1000.0))
	check("pid_alive: this process, not an ended one", Prefs.pid_alive(OS.get_process_id()) and not Prefs.pid_alive(4000001))

	check("log name", Service.log_name("install", {"year": 2026, "month": 10, "day": 3, "hour": 4, "minute": 5, "second": 6}) == "install-20261003-040506.log")
	var logs := dir.path_join("logs-test")
	for i in 7:
		write(logs.path_join("agent-service-2026010%d-000000.log" % i), "")
	write(logs.path_join("install-20260101-000000.log"), "")
	Service.prune_logs(logs, "agent-service", 5)
	var left := Array(DirAccess.get_files_at(logs))
	check("logs: the 5 newest kept, other kinds untouched", left.size() == 6 and not left.has("agent-service-20260100-000000.log")
		and left.has("agent-service-20260106-000000.log") and left.has("install-20260101-000000.log"), left)

	# a start, with a stand-in launcher (nothing is run)
	var launched := []
	var alive := {"yes": true}
	var s = Service.new()
	s.candidates = places
	s.editor_pid = 4242
	s.launcher = func(program: String, args: PackedStringArray) -> int:
		launched.append([program, args])
		return 777
	s.running = func(_p: int) -> bool: return alive.yes
	s.exit_code = func(_p: int) -> int: return 0
	DirAccess.remove_absolute(Prefs.file(Prefs.OWN_BRAIN_FILE))
	check("ensure: nothing announced, starts the one found", s.ensure() == "starting" and launched.size() == 1
		and " ".join(launched[0][1]).contains("serve --port 0 --exit-with 4242"), launched)
	check("the start's log in Autopilot/logs", s.log_path.get_file().begins_with("agent-service-") and s.log_path.get_base_dir() == Service.logs_folder(), s.log_path)
	s._poll_at = 0.0
	s.poll()
	check("polling: not announced yet, still starting", s.state == "starting")
	write(Prefs.file(Prefs.OWN_BRAIN_FILE), JSON.stringify({"url": url, "pid": 777, "started": Time.get_unix_time_from_system()}))
	s._poll_at = 0.0
	s.poll()
	check("polling: announced by it, ready", s.state == "ready" and s.url == url, [s.state, s.url])
	write(Prefs.file(Prefs.OWN_BRAIN_FILE), JSON.stringify({"url": url, "pid": OS.get_process_id()}))
	launched.clear()
	var s2 = Service.new()
	s2.candidates = places
	s2.launcher = s.launcher
	check("ensure: one announced and running is used, none started", s2.ensure() == "ready" and launched.is_empty() and s2.url == url)
	DirAccess.remove_absolute(Prefs.file(Prefs.OWN_BRAIN_FILE))
	s2.state = "idle"
	s2.ensure()
	alive.yes = false
	s2._poll_at = 0.0
	s2.running = s.running
	s2.poll()
	check("the process ended before announcing: stopped, with its log", s2.state == "stopped" and s2.log_path in s2.message
		and s2.message.begins_with("The agent service stopped (see "), s2.message)
	var s3 = Service.new()
	s3.candidates = [bin1.path_join("autopilot.exe")]
	s3.installer_dir = dir.path_join("no-installer")
	check("nothing found: not installed, no installer here", s3.ensure() == "not_installed" and not s3.has_installer())
	s3.install()
	check("install without the installer: stays not installed", s3.state == "not_installed")
	s3.installer_dir = dir.path_join("agent_service")
	write(s3.installer_dir.path_join("install.ps1"), "")
	write(s3.installer_dir.path_join("install.sh"), "")
	launched.clear()
	alive.yes = true
	s3.launcher = s.launcher
	s3.running = s.running
	var code := {"c": 1}
	s3.exit_code = func(_p: int) -> int: return code.c
	s3.install()
	check("install: the installer run, its log", s3.state == "installing" and launched.size() == 1 and s3.log_path.get_file().begins_with("install-"), launched)
	alive.yes = false
	s3._poll_at = 0.0
	s3.poll()
	check("install failed: its words and log", s3.state == "install_failed" and s3.message == "Couldn’t install the agent service (see %s)" % s3.log_path, s3.message)
	code.c = 0
	alive.yes = true
	s3.install()
	s3.candidates = places  # (the installer put autopilot in its place)
	alive.yes = false
	s3._poll_at = 0.0
	launched.clear()
	s3.poll()
	check("installed: found again and started", s3.state == "starting" and launched.size() == 1, [s3.state, launched])
	s3.reset()
	check("starting is not reset (busy)", s3.state == "starting")

	# the decisions (pure): which command starts, whether the addon's is installed first, the one running kept or replaced
	var ship := {"sha256": "aaa", "commit": "c1", "wheel": "autopilot-0.1.0-py3-none-any.whl"}
	var own_p := "C:/Users/u/.local/bin/autopilot.exe"
	var on_path := PackedStringArray(["C:/dev/.venv/Scripts/autopilot.exe"])
	check("carries: a stamp with a commit or a sha256, and the installer", Service.carries(ship, true)
		and Service.carries({"commit": "c1"}, true) and Service.carries({"sha256": "aaa"}, true)
		and not Service.carries(ship, false) and not Service.carries({}, true) and not Service.carries({"commit": ""}, true))
	check("own place: the installer's", Service.own_place("C:/Users/u", true) == own_p
		and Service.own_place("/home/u", false) == "/home/u/.local/bin/autopilot" and Service.own_place("", true) == "")
	var p := Service.plan_start("py -m autopilot", true, ship, {"sha256": "old"}, on_path, own_p, false)
	check("plan: the setting, as it is: nothing installed", p.command == PackedStringArray(["py", "-m", "autopilot"]) and not p.install, p)
	p = Service.plan_start("", true, ship, {}, on_path, own_p, false)
	check("plan: carried, nothing installed, autopilot on PATH: the addon's installed, PATH not used", p.command.is_empty() and p.install, p)
	p = Service.plan_start("", true, ship, {}, PackedStringArray(), own_p, false)
	check("plan: carried, nothing installed anywhere: not installed (the setup's Install does it), nothing installed here",
		p.command.is_empty() and not p.install, p)
	p = Service.plan_start("", true, ship, {"sha256": "bbb", "commit": "c0"}, PackedStringArray([own_p]), own_p, true)
	check("plan: carried, another one installed: the addon's installed, then the installer's place", p.command == PackedStringArray([own_p]) and p.install, p)
	p = Service.plan_start("", true, ship, {}, PackedStringArray([own_p]), own_p, true)
	check("plan: carried, the installed one unknown (an install from before): installed", p.install, p)
	p = Service.plan_start("", true, ship, {"sha256": "aaa", "commit": "c1"}, on_path, own_p, true)
	check("plan: carried, the same one installed: the installer's place started (PATH not used)", p.command == PackedStringArray([own_p]) and not p.install, p)
	check("plan: by commit when the addon's names no sha256", not Service.plan_start("", true, {"commit": "c1"}, {"commit": "c1"}, on_path, own_p, true).install
		and Service.plan_start("", true, {"commit": "c1"}, {"commit": "c0"}, on_path, own_p, true).install)
	p = Service.plan_start("", false, {}, {"sha256": "bbb"}, on_path, own_p, true)
	check("plan: not carried: the search's (PATH first), nothing installed", p.command == on_path and not p.install, p)
	p = Service.plan_start("", false, {}, {}, PackedStringArray(), own_p, false)
	check("plan: not carried, nothing found: nothing to start", p.command.is_empty() and not p.install, p)
	check("announced: the same commit, used", Service.on_announced(true, ship, {"url": url, "pid": 9, "commit": "c1"}, false, false) == "use")
	check("announced: another commit, replaced", Service.on_announced(true, ship, {"url": url, "pid": 9, "commit": "c0"}, false, false) == "replace")
	check("announced: no commit (from before), or \"\" (a checkout), replaced", Service.on_announced(true, ship, {"url": url, "pid": 9}, false, false) == "replace"
		and Service.on_announced(true, ship, {"url": url, "pid": 9, "commit": ""}, false, false) == "replace")
	check("announced: the setting set, used whatever it is", Service.on_announced(true, ship, {"url": url, "pid": 9}, true, false) == "use")
	check("announced: connected to it, used", Service.on_announced(true, ship, {"url": url, "pid": 9}, false, true) == "use")
	check("announced: not carried, used whatever it is", Service.on_announced(false, {}, {"url": url, "pid": 9}, false, false) == "use"
		and Service.on_announced(false, {"commit": "c1"}, {"url": url, "pid": 9, "commit": "c0"}, false, false) == "use")
	check("announced: the addon's names no commit, used", Service.on_announced(true, {"sha256": "aaa"}, {"url": url, "pid": 9}, false, false) == "use")
	for image in ["autopilot.exe", "C:\\Users\\u\\.local\\bin\\autopilot.exe", "/home/u/.local/bin/autopilot", "python.exe", "pythonw.exe",
			"python3", "python3.12", "Python.EXE", "/usr/bin/python"]:
		check("stoppable: %s" % image, Service.stoppable(image))
	for image in ["Godot_v4.7.2-stable_win64.exe", "explorer.exe", "game.exe", "pythonista", "autopilot-helper.exe", ""]:
		check("not stoppable: %s" % image, not Service.stoppable(image))
	# the process announced is the one that announced: it started before brain.json was written (a reused pid: after)
	var said_at := {"url": url, "pid": 9, "started": 1000000.0}
	check("owner: started before the announcement, may stop", Service.may_stop(999000.0, said_at) and Service.owner_check(999000.0, said_at) == "")
	check("owner: started within the slack after it (uv's launcher, the clock), may stop", Service.may_stop(1000004.5, said_at)
		and Service.may_stop(1000005.0, said_at))
	check("owner: started after it, past the slack (another program got the pid), never", not Service.may_stop(1000005.5, said_at)
		and not Service.may_stop(1003600.0, said_at) and "started after" in Service.owner_check(1003600.0, said_at))
	check("owner: brain.json without started, never", not Service.may_stop(999000.0, {"url": url, "pid": 9})
		and not Service.may_stop(999000.0, {"url": url, "pid": 9, "started": "yesterday"})
		and not Service.may_stop(999000.0, {"url": url, "pid": 9, "started": 0}))
	check("owner: the start time unreadable, never", not Service.may_stop(-1.0, said_at) and not Service.may_stop(0.0, said_at)
		and "couldn't be read" in Service.owner_check(-1.0, said_at))
	check("owner: an int started is fine", Service.may_stop(999000.0, {"url": url, "pid": 9, "started": 1000000}))
	check("parse_etime: mm:ss, hh:mm:ss, dd-hh:mm:ss", Service.parse_etime(" 05:07\n") == 307 and Service.parse_etime("02:05:07") == 7507
		and Service.parse_etime("3-02:05:07") == 3 * 86400 + 7507, [Service.parse_etime("05:07"), Service.parse_etime("02:05:07"), Service.parse_etime("3-02:05:07")])
	check("parse_etime: anything else, -1", Service.parse_etime("") == -1 and Service.parse_etime("abc") == -1 and Service.parse_etime("12") == -1
		and Service.parse_etime("x-01:02") == -1)
	var mine := Service.process_started(OS.get_process_id(), OS.get_name())
	var now_s := Time.get_unix_time_from_system()
	check("process_started: this process's, in the last hour; none for an ended one", mine > now_s - 3600.0 and mine <= now_s + 1.0
		and Service.process_started(4000001, OS.get_name()) < 0.0, [mine, now_s])
	var home := dir.path_join("home")
	var own := home.path_join(".local").path_join("bin").path_join("autopilot.exe" if OS.get_name() == "Windows" else "autopilot")
	write(own, "")
	var s4 = Service.new()
	s4.candidates = [own]
	s4.home_dir = home
	s4.installer_dir = s3.installer_dir
	write(s4.installer_dir.path_join(Service.STAMP), JSON.stringify(ship))
	write(Prefs.file(Service.STAMP), JSON.stringify({"sha256": "old"}))
	s4.launcher = s.launcher
	s4.running = s.running
	s4.exit_code = func(_p: int) -> int: return code.c
	DirAccess.remove_absolute(Prefs.file(Prefs.OWN_BRAIN_FILE))
	launched.clear()
	alive.yes = true
	check("update: the addon's installed first", s4.ensure() == "installing" and launched.size() == 1 and s4.log_path.get_file().begins_with("install-"), [s4.state, launched])
	code.c = 1
	alive.yes = false
	s4._poll_at = 0.0
	s4.poll()
	check("update failed: the installed one started anyway", s4.state == "starting" and launched.size() == 2
		and " ".join(launched[1][1]).contains("serve --port 0"), [s4.state, s4.message])
	s4.state = "idle"
	launched.clear()
	check("update tried once a session: started as it is", s4.ensure() == "starting" and launched.size() == 1, [s4.state, launched])
	write(Prefs.file(Service.STAMP), JSON.stringify(ship))
	var s5 = Service.new()
	s5.candidates = [own]
	s5.home_dir = home
	s5.installer_dir = s3.installer_dir
	s5.launcher = s.launcher
	launched.clear()
	check("installed the addon's: started, nothing installed", s5.ensure() == "starting" and launched.size() == 1
		and " ".join(launched[0][1]).contains("serve --port 0"), [s5.state, launched])
	check("process_image: this process's program, none for an ended one",
		Service.process_image(OS.get_process_id(), OS.get_name()).to_lower().contains("godot")
		and Service.process_image(4000001, OS.get_name()) == "", Service.process_image(OS.get_process_id(), OS.get_name()))

	# carried, nothing installed anywhere (no autopilot): not installed, nothing run until Install
	var empty_home := dir.path_join("empty-home")
	var s8 = Service.new()
	s8.candidates = [bin1.path_join("autopilot.exe")]  # (PATH's: none there)
	s8.home_dir = empty_home
	s8.installer_dir = s3.installer_dir
	s8.launcher = s.launcher
	s8.running = s.running
	s8.exit_code = func(_p: int) -> int: return 0
	launched.clear()
	alive.yes = true
	check("carried, nothing installed anywhere: not installed, nothing run (the setup's Install does it)",
		s8.ensure() == "not_installed" and launched.is_empty() and s8.has_installer(), [s8.state, launched])
	check("carried, nothing installed anywhere: still not installed when asked again", s8.ensure() == "not_installed" and launched.is_empty(), [s8.state, launched])
	s8.install()
	check("Install: the addon's installer run", s8.state == "installing" and launched.size() == 1, [s8.state, launched])
	write(Service.own_place(empty_home, OS.get_name() == "Windows"), "")  # (the installer put it in its place)
	alive.yes = false
	s8._poll_at = 0.0
	s8.poll()
	check("Install done: the installer's place started", s8.state == "starting" and launched.size() == 2
		and " ".join(launched[1][1]).replace("\\", "/").replace("\"", "").contains(Service.own_place(empty_home, OS.get_name() == "Windows").replace("\\", "/") + " serve"),
		[s8.state, launched])

	# carried, nothing installed, an autopilot on PATH: the addon's installed, PATH's never started
	var bare_home := dir.path_join("bare-home")
	var s7 = Service.new()
	s7.candidates = [bin2.path_join("autopilot.exe")]  # (PATH's: exists)
	s7.home_dir = bare_home
	s7.installer_dir = s3.installer_dir
	s7.launcher = s.launcher
	s7.running = s.running
	s7.exit_code = func(_p: int) -> int: return 0
	launched.clear()
	alive.yes = true
	check("carried, nothing installed: the addon's installed first", s7.ensure() == "installing" and launched.size() == 1
		and s7.log_path.get_file().begins_with("install-"), [s7.state, launched])
	write(Service.own_place(bare_home, OS.get_name() == "Windows"), "")  # (the installer put it in its place)
	alive.yes = false
	s7._poll_at = 0.0
	s7.poll()
	var started := " ".join(launched[-1][1]).replace("\\", "/").replace("\"", "")
	check("then the installer's place started, not PATH's", s7.state == "starting" and launched.size() == 2
		and started.contains(Service.own_place(bare_home, OS.get_name() == "Windows").replace("\\", "/") + " serve")
		and not started.contains("bin2"), started)

	# an agent service running (announced by this process, so that it runs), stood in for: nothing real is stopped
	var me := OS.get_process_id()
	var stopped := []
	var images := {}
	var starts := {me: Time.get_unix_time_from_system() - 60.0}  # (when the stand-in started: before it announced)
	var fake := func(svc) -> void:
		svc.candidates = [own]
		svc.home_dir = home
		svc.installer_dir = s3.installer_dir
		svc.launcher = s.launcher
		svc.running = s.running
		svc.image_of = func(q: int) -> String: return str(images.get(q, ""))
		svc.started_of = func(q: int) -> float: return float(starts.get(q, -1.0))
		svc.stopper = func(q: int) -> bool:
			stopped.append(q)
			if images.get("dies", true):
				images.erase(q)
			return true
	var announce := func(said: Dictionary) -> void:
		said.merge({"url": url, "pid": me, "started": Time.get_unix_time_from_system()})
		write(Prefs.file(Prefs.OWN_BRAIN_FILE), JSON.stringify(said))
	write(Prefs.file(Service.STAMP), JSON.stringify(ship))  # (the addon's installed: nothing to install)
	var r1 = Service.new()
	fake.call(r1)
	images[me] = "python.exe"
	announce.call({"commit": "c1"})
	launched.clear()
	check("running, the same commit: used, nothing stopped nor started", r1.ensure() == "ready" and r1.url == url
		and stopped.is_empty() and launched.is_empty(), [r1.state, stopped, launched])
	announce.call({"commit": "c0"})
	r1.state = "idle"
	check("running, another commit: stopped, brain.json gone, the addon's started", r1.ensure() == "starting"
		and stopped == [me] and not FileAccess.file_exists(Prefs.file(Prefs.OWN_BRAIN_FILE)) and launched.size() == 1
		and " ".join(launched[0][1]).contains("serve --port 0"), [r1.state, stopped, launched])
	images[me] = "python.exe"
	announce.call({"commit": "c0"})  # (it came back)
	r1.state = "idle"
	stopped.clear()
	launched.clear()
	check("replaced once a session: the one back is used", r1.ensure() == "ready" and stopped.is_empty() and launched.is_empty(), [r1.state, stopped])
	var r2 = Service.new()
	fake.call(r2)
	announce.call({})
	launched.clear()
	check("running, no commit (from before): replaced", r2.ensure() == "starting" and stopped == [me] and launched.size() == 1, [r2.state, stopped])
	var r3 = Service.new()
	fake.call(r3)
	images[me] = "Godot_v4.7.2-stable_win64.exe"
	announce.call({"commit": "c0"})
	stopped.clear()
	launched.clear()
	check("running, another commit, but not an agent service's program: left running, used", r3.ensure() == "ready"
		and stopped.is_empty() and launched.is_empty() and FileAccess.file_exists(Prefs.file(Prefs.OWN_BRAIN_FILE)), [r3.state, stopped])
	var r4 = Service.new()
	fake.call(r4)
	images[me] = "autopilot.exe"
	images["dies"] = false
	launched.clear()
	check("running, another commit, it won't stop: used", r4.ensure() == "ready" and stopped == [me] and launched.is_empty()
		and FileAccess.file_exists(Prefs.file(Prefs.OWN_BRAIN_FILE)), [r4.state, stopped])
	images["dies"] = true
	var r5 = Service.new()
	fake.call(r5)
	r5.command_setting = "py -m autopilot"
	stopped.clear()
	check("running, another commit, the setting set: used", r5.ensure() == "ready" and stopped.is_empty(), [r5.state, stopped])
	var r6 = Service.new()
	fake.call(r6)
	r6.in_use = func() -> bool: return true
	check("running, another commit, connected to it: used", r6.ensure() == "ready" and stopped.is_empty(), [r6.state, stopped])
	var r7 = Service.new()
	fake.call(r7)
	r7.installer_dir = dir.path_join("no-installer")
	check("running, another commit, the addon carries none: used (as before)", r7.ensure() == "ready" and stopped.is_empty(), [r7.state, stopped])
	# the pid announced now another program's (started after brain.json was written), or it can't be told: never stopped
	images[me] = "python.exe"
	announce.call({"commit": "c0"})
	var r8 = Service.new()
	fake.call(r8)
	starts[me] = Time.get_unix_time_from_system() + 30.0
	stopped.clear()
	launched.clear()
	check("running, another commit, a python started after the announcement (a reused pid): left running, used", r8.ensure() == "ready"
		and stopped.is_empty() and launched.is_empty() and FileAccess.file_exists(Prefs.file(Prefs.OWN_BRAIN_FILE)), [r8.state, stopped])
	var r9 = Service.new()
	fake.call(r9)
	starts.erase(me)
	check("running, another commit, its start time unreadable: left running, used", r9.ensure() == "ready" and stopped.is_empty()
		and launched.is_empty(), [r9.state, stopped])
	starts[me] = Time.get_unix_time_from_system() - 60.0
	write(Prefs.file(Prefs.OWN_BRAIN_FILE), JSON.stringify({"url": url, "pid": me, "commit": "c0"}))
	var r10 = Service.new()
	fake.call(r10)
	check("running, another commit, brain.json without started: left running, used", r10.ensure() == "ready" and stopped.is_empty()
		and launched.is_empty(), [r10.state, stopped])
	announce.call({"commit": "c0"})
	var r11 = Service.new()
	fake.call(r11)
	check("running, another commit, started before the announcement: stopped (the owner check passes)", r11.ensure() == "starting"
		and stopped == [me], [r11.state, stopped])
	DirAccess.remove_absolute(Prefs.file(Prefs.OWN_BRAIN_FILE))
	DirAccess.remove_absolute(Prefs.file(Service.STAMP))
