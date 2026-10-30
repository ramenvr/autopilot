## What the Autopilot dock shows, without any UI: the runs in its history (Run; kept between editor sessions by
## ui/history.gd), the start frame for the composer's words (run_of), the agent server's status lines translated into
## stages, the policy steps (the server's "jev" frames, docs/protocol.md "Inspection") parsed into attempts and action
## tags, the redaction of a step for display, and the run report as markdown.
@tool
extends RefCounted

const HT = preload("autopilot_theme.gd")

## After a rollout the results are processed until the next rollout or the report (the track follows the lines: an
## older agent service went back to the stage the agent went on with).
const STAGES := ["Launch", "Analyze", "Probe env", "Build harness", "Rollout", "Process result", "Report"]
const STAGE_ROLLOUT := 4
const STAGE_RESULTS := 5
const STAGE_REPORT := 6
## The Feed tab's heading for each stage.
const STAGE_HEADINGS := ["Launch", "Analyze", "Probe", "Build harness", "Play", "Results", "Report"]
## The most lines a run's feed keeps (the oldest let go).
const MAX_FEED := 2000
## An activity frame's `stage` -> the stage (docs/protocol.md, The activity feed; any other name: -1).
const ACTIVITY_STAGES := {"analyze": 1, "probe": 2, "build": 3, "play": STAGE_ROLLOUT, "results": STAGE_RESULTS, "report": STAGE_REPORT}
## The icon before an activity line's words, by the event's kind, named as every Autopilot UI names them (the dock draws
## it: the editor's own icon where it has one of the meaning, the theme's glyph otherwise): a robot for the agent's
## words, a magnifying glass for a read, binoculars for a search, a pencil for a write, a flask for a probe (tried in
## the game), a gamepad for an episode, an eye for a look, a circular arrow for a restart, a speech bubble for the user's
## steer, a flag for the report, a megaphone for an alert, a notebook for the refiner's edit to the game's memory.
const ACTIVITY_ICONS := {"text": "robot", "read": "magnifier", "search": "binoculars", "write": "pencil", "probe": "flask",
	"episode": "gamepad", "look": "eye", "restart": "circular-arrow", "steer": "speech-bubble", "report": "flag", "alert": "megaphone",
	"refine": "notebook"}
## What each icon stands for: the line's tooltip.
const ACTIVITY_TITLES := {"text": "What the agent said", "read": "Read a file", "search": "Searched the code", "write": "Wrote a file",
	"probe": "Tried something in the game", "episode": "Played the harness", "look": "Looked at the picture",
	"restart": "Restarted the level", "steer": "You steered", "report": "Reported", "alert": "Announcement",
	"refine": "Refined the game's memory"}
## The one letter shown in the icon's place when no icon can be drawn (? the agent's words, R read, S searched, W wrote,
## P tried in the game, E episode, L looked, ! report or alert, > steer, * restart, M memory refined).
const ACTIVITY_GLYPHS := {"text": "?", "read": "R", "search": "S", "write": "W", "probe": "P", "episode": "E", "look": "L",
	"report": "!", "alert": "!", "steer": ">", "restart": "*", "refine": "M"}
## The fold's word for an activity event's detail, by its kind (any other: "the whole message"); and for the detail of
## the event that ended a call, by the call's kind (any other: "what came back").
const DETAIL_LABELS := {"probe": "the code", "write": "what was written", "episode": "what was asked", "look": "the questions",
	"report": "the summary", "read": "the path", "search": "the pattern", "refine": "the edit and its evidence"}
const RESULT_LABELS := {"probe": "the output", "episode": "the summary", "look": "the description"}


## The stage an activity frame's `stage` names (-1: none).
static func stage_of(name: String) -> int:
	return int(ACTIVITY_STAGES.get(name, -1))


## The icon's name for an event's kind ("" for a kind this build doesn't know).
static func activity_icon(kind: String) -> String:
	return str(ACTIVITY_ICONS.get(kind, ""))


## The icon's title for an event's kind ("" for a kind this build doesn't know).
static func activity_title(kind: String) -> String:
	return str(ACTIVITY_TITLES.get(kind, ""))


static func activity_glyph(kind: String) -> String:
	return str(ACTIVITY_GLYPHS.get(kind, "·"))


## The fold control's words: "+ inspect the code" while closed, "– hide the code" while open (the detail's name stays,
## so a call's fold and its result's can be told apart).
static func fold_label(label: String, open: bool) -> String:
	return ("– hide " if open else "+ inspect ") + label


## Whether a detail is better folded than shown: more than one line, or long.
static func folds(detail: String) -> bool:
	return detail != "" and ("\n" in detail or detail.length() > 80)


## The fold's word for an activity line's detail ("" when it has none to fold).
static func detail_label(e: Dictionary) -> String:
	if not folds(str(e.get("detail", ""))):
		return ""
	return str(DETAIL_LABELS.get(str(e.get("akind", "")), "the whole message"))


## The fold's word for an activity line's result detail ("" when none).
static func result_label(e: Dictionary) -> String:
	if not folds(str(e.get("rdetail", ""))):
		return ""
	return str(RESULT_LABELS.get(str(e.get("akind", "")), "what came back"))


## The Feed tab's items for a run's feed: its lines in order, a heading ({"kind": "heading", "text": the stage's name,
## "count": its lines, "stage"}) before each stretch of lines of one stage (lines before any stage have none). A stage
## that comes again (Play, then Results, then Play for the next harness) gets a heading each time.
static func group_feed(feed: Array) -> Array:
	var items := []
	var current := -1
	var heading := {}
	for e in feed:
		if typeof(e) != TYPE_DICTIONARY:
			continue
		var stage := int(e.get("stage", -1))
		if stage >= 0 and stage != current:
			current = stage
			heading = {"kind": "heading", "stage": stage, "count": 0, "t": e.get("t", 0.0),
				"text": STAGE_HEADINGS[stage] if stage < STAGE_HEADINGS.size() else ""}
			items.append(heading)
		if not heading.is_empty():
			heading.count += 1
		items.append(e)
	return items

## decided.summary's "<action>: <name>" -> the tag shown, and its past tense for the step detail.
const TAGS := {"started": "START", "kept": "KEEP", "switched": "SWITCH", "kept_running": "HELD", "dropped": "DROPPED"}
const TAG_ORDER := ["SWITCH", "START", "KEEP", "HELD", "DROPPED", "FAILED"]
const TAG_PAST := {"START": "Started", "KEEP": "Kept", "SWITCH": "Switched", "HELD": "Held", "DROPPED": "Dropped", "FAILED": "Failed"}

static var _attempt_re := RegEx.create_from_string("^Trying the harness \\(run (\\d+)\\)$")

## The feed's last line when the run's result says it added to or changed what is remembered about the game
## (result.memory.updated).
const MEMORY_UPDATED := "Memory updated"

## Why a run has no recording when its result has neither a `recording` nor a `memory`: every agent service new
## enough to record every run sends `memory`, so an older one ran it.
const NO_RECORDING_OLDER := "an older agent service ran this."


## Why the run of this result has no recording, decided when the result arrives: "" when it has one (`recording`);
## else the agent service's `no_recording` as sent; else, with no `memory` either, NO_RECORDING_OLDER; else "".
static func no_recording_why(result: Dictionary) -> String:
	if typeof(result.get("recording")) == TYPE_DICTIONARY:
		return ""
	var why = result.get("no_recording")
	if typeof(why) == TYPE_STRING and why.strip_edges() != "":
		return why.strip_edges()
	if not result.has("memory"):
		return NO_RECORDING_OLDER
	return ""


## The result with the decision kept in it (saved with it in result.json): `no_recording` set to no_recording_why(),
## or taken out when that is "". Whether it changed.
static func keep_no_recording(result: Dictionary) -> bool:
	var why := no_recording_why(result)
	var before = result.get("no_recording")
	if why == "":
		return result.erase("no_recording")
	result["no_recording"] = why
	return typeof(before) != TYPE_STRING or before != why

## The composer's Time limit (kept per project as it changes), in minutes: the default, and what it may be set to.
const TIME_LIMIT_DEFAULT := 30
const TIME_LIMIT_MIN := 1
const TIME_LIMIT_MAX := 600


## A saved Time limit as the dock uses it: whole minutes within TIME_LIMIT_MIN..TIME_LIMIT_MAX, the default when it is
## missing or not a number.
static func time_limit_of(value) -> int:
	if value is int or value is float:
		return clampi(int(value), TIME_LIMIT_MIN, TIME_LIMIT_MAX)
	if value is String and value.strip_edges().is_valid_float():
		return clampi(int(float(value)), TIME_LIMIT_MIN, TIME_LIMIT_MAX)
	return TIME_LIMIT_DEFAULT


## The words a run_started frame's run is shown with: its claim or goal; an exploration as the composer words one
## ("Explore" and its brief); a run whose kind the agent service reads (kind "auto"), its words as typed.
static func prompt_of_run(run: Dictionary) -> String:
	match str(run.get("kind", "")):
		"verify":
			return str(run.get("claim", "")).strip_edges()
		"goal":
			return str(run.get("goal", "")).strip_edges()
		"explore":
			var brief := str(run.get("brief", "")).strip_edges()
			return ("Explore " + brief) if brief != "" else "Explore"
		"auto":
			return str(run.get("prompt", "")).strip_edges()
	return str(run.get("claim", run.get("goal", run.get("brief", "")))).strip_edges()


## The kinds a run is played as (a start frame's, a run_kind frame's): anything else is shown as no kind until the
## agent service says which ("auto": it reads the words first).
const KINDS := ["verify", "goal", "explore"]


## The start frame's run for the composer's words (docs/protocol.md, "One prompt box"): the words as typed, the agent
## service reads which kind of run they ask for (and tells it with a run_kind frame); time_limit: the composer's Time
## limit, the run's minutes in all (an exploration's minutes of play).
static func run_of(text: String, time_limit: int) -> Dictionary:
	return {"kind": "auto", "prompt": text.strip_edges(), "time_limit_minutes": time_limit}


## A status line as shown: {"text", "stage" (-1: none), "attempt" (0: none), "now" (bool)}.
static func translate(line: String) -> Dictionary:
	var out := {"text": line, "stage": -1, "attempt": 0, "now": false}
	match line:
		"Saving what it learned to memory":
			out.stage = STAGE_REPORT  # (after the agent finished, before the result: part of the report)
			return out
		"Starting the game":
			out.text = "Launching the environment"
			out.stage = 0
			return out
		"Reading the game's code":
			out.text = "Analyzing the project"
			out.stage = 1
			return out
		"Trying things in the game":
			out.text = "Probing the environment"
			out.stage = 2
			return out
		"Writing the harness":
			out.text = "Building the harness"
			out.stage = 3
			return out
		"Looking at the results":
			out.text = "Processing results"
			out.stage = STAGE_RESULTS
			return out
	var m := _attempt_re.search(line)
	if m:
		out.attempt = int(m.get_string(1))
		out.text = "Harness %d" % out.attempt
		out.stage = STAGE_ROLLOUT
		return out
	if line.begins_with("Now: "):
		out.now = true
		return out
	out.text = scrub(line)
	return out


## Words the dock never shows, in any text it didn't write itself (status lines, connection problems).
static func scrub(text: String) -> String:
	var t := text
	t = t.replace("playtest service", "agent service").replace("Playtest service", "Agent service")
	t = t.replace("the brain", "the agent service").replace("The brain", "The agent service")
	return t


static func clock(seconds: float) -> String:
	var sec := int(max(0.0, seconds))
	if sec >= 3600:
		return "%d:%02d:%02d" % [sec / 3600, (sec / 60) % 60, sec % 60]
	return "%02d:%02d" % [sec / 60, sec % 60]


## A time in a feed line or the player's position: "0:48" (m:ss), "1:02:03" from an hour.
static func short_clock(seconds: float) -> String:
	var sec := int(max(0.0, seconds))
	if sec >= 3600:
		return "%d:%02d:%02d" % [sec / 3600, (sec / 60) % 60, sec % 60]
	return "%d:%02d" % [sec / 60, sec % 60]


## A run's prompt as shown: the typed words, or when none, by its kind: "Explore", "Play a goal", "Verify", "Run".
static func prompt_shown(prompt: String, kind: String) -> String:
	if prompt.strip_edges() != "":
		return prompt
	match kind:
		"explore": return "Explore"
		"goal": return "Play a goal"
		"verify": return "Verify"
	return "Run"


## A verdict (or any upper-case word) in sentence case, as a line says it: "GOAL HIT" -> "Goal hit".
static func sentence(word: String) -> String:
	return word.substr(0, 1).to_upper() + word.substr(1).to_lower()


## A run's harness attempts as the run history and the tab bar say them: "1 attempt", "2 attempts".
static func attempts_text(n: int) -> String:
	return "%d attempt%s" % [n, "" if n == 1 else "s"]


## Unix seconds as local wall-clock time "HH:MM:SS.mmm".
static func wall_time(unix: float) -> String:
	var bias := int(Time.get_time_zone_from_system().get("bias", 0)) * 60
	var d := Time.get_datetime_dict_from_unix_time(int(floor(unix)) + bias)
	return "%02d:%02d:%02d.%03d" % [d.hour, d.minute, d.second, int(fposmod(unix, 1.0) * 1000.0)]


## When a run started, as shown everywhere: unix seconds as local "Oct 08 13:25".
static func when(unix: float) -> String:
	var bias := int(Time.get_time_zone_from_system().get("bias", 0)) * 60
	var d := Time.get_datetime_dict_from_unix_time(int(floor(unix)) + bias)
	var months := ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
	return "%s %02d %02d:%02d" % [months[clampi(int(d.month) - 1, 0, 11)], d.day, d.hour, d.minute]


static func num(v) -> String:
	if typeof(v) == TYPE_FLOAT:
		if is_equal_approx(v, round(v)) and absf(v) < 1e9:
			return str(int(v))
		return str(snappedf(v, 0.01))
	if typeof(v) == TYPE_STRING:
		return v
	if v == null:
		return "—"
	return JSON.stringify(v)


## A probability as ".34" (two decimals, the leading zero dropped; 1 as "1.00").
static func prob(p: float) -> String:
	return ("%.2f" % clampf(p, 0.0, 1.0)).trim_prefix("0")


## Seconds as shown: one decimal and a space, "4.0 s" ("—" when missing).
static func secs(v) -> String:
	if typeof(v) in [TYPE_INT, TYPE_FLOAT]:
		return "%.1f s" % float(v)
	return "—"


## The frame as shown raw (and copied): the step with every question's instructions taken out, and named as a policy
## step (the wire's frame type and timing key are internal names).
static func redact(frame: Dictionary) -> Dictionary:
	var f: Dictionary = frame.duplicate(true)
	for k in f.keys():
		if str(k).begins_with("_"):
			f.erase(k)
	if f.has("type"):
		f.type = "policy_step"
	var secs = f.get("seconds")
	if typeof(secs) == TYPE_DICTIONARY:
		var named := {}
		for k in secs:
			named["policy" if str(k) == "jev" else k] = secs[k]
		f.seconds = named
	var input = f.get("input")
	if typeof(input) == TYPE_DICTIONARY and typeof(input.get("questions")) == TYPE_DICTIONARY:
		for q in input.questions.values():
			if typeof(q) == TYPE_DICTIONARY:
				q.erase("instructions")
	return f


## The engine findings' location as text ("X, Y, Z cm" in the kit frame).
static func where_text(w) -> String:
	if typeof(w) != TYPE_DICTIONARY:
		return ""
	return "%s, %s, %s" % [num(float(w.get("X", 0.0))), num(float(w.get("Y", 0.0))), num(float(w.get("Z", 0.0)))]


class Step:
	extends RefCounted
	var frame: Dictionary
	var attempt := 1
	var n := 0  # the step's number in its attempt (the frame's `call`)
	var tag := ""
	var chosen := ""
	var current := ""
	var plan := ""
	var t := 0.0
	var wall := 0.0
	var ms := 0.0
	var probs := {}  # action -> p
	var state := {}  # input.state
	var error := ""  # the step failed: why, as the agent service said ("" for none; its tag is FAILED)

	func label() -> String:
		return "H%d #%d" % [attempt, n]

	func sorted_probs() -> Array:
		var out := []
		for k in probs:
			out.append([str(k), float(probs[k])])
		out.sort_custom(func(a, b): return a[1] > b[1])
		return out


class Run:
	extends RefCounted
	var id := ""
	var kind := ""  # verify | goal | explore | "" (not known yet: the agent service's run_kind says it; started elsewhere)
	var prompt := ""
	var request := {}  # the run frame sent (for Run again)
	var started_unix := 0.0
	var started_ms := 0
	var ended_ms := -1
	var feed := []  # {"t": seconds since start, "text", "kind": status|attempt|now|engine|agent|info|memory}
	var stage := 0
	var stage_since := 0.0  # seconds since start the current stage began
	var stage_secs := {}  # stage -> seconds spent in it before (a stage comes again for each harness)
	var attempts := 0
	var now_text := ""
	var findings := []  # engine findings, one per signature (the latest), in order of first arrival
	var not_a_bug := {}  # signature -> true
	var result := {}
	var report_dir := ""
	var steps := []  # Step
	var step_count := 0  # the policy steps that came (all kept: steps.size(), unless older ones were let go)
	var stop_asked := false
	var lost_connection := false  # ended (NO RESULT) only because the connection closed: the run may still go on
	var colors := {}  # action -> Color (by first appearance)
	var usage := []  # the brain's latest `usage` models for this run (cumulative: each replaces the one before)
	var usage_final := false
	var bill := {}  # what Autopilot was billed for this run through Aura (the brain's `bill`, ui/usage.gd parse_bill(); {} for none)
	var ended_why := ""  # why it has no result, when the dock knows ("" else): a saved run live when the editor closed
	var history_file := ""  # its file in the saved history (ui/history.gd; "" until it is first saved)
	## Who started the run when the tab didn't: the run_started frame's `by` (the user's own Claude Code or Codex, through
	## `autopilot mcp`); "" for a run the tab started.
	var origin := ""

	## The line under the request for a run the user's own coding agent started ("" for one the tab started).
	func origin_line() -> String:
		match origin:
			"":
				return ""
			"claude-code":
				return "Started from Claude Code"
			"codex":
				return "Started from Codex"
		return "Started from your coding agent"

	## The run as the Settings page keeps it between editor sessions (ui/usage.gd).
	func usage_record() -> Dictionary:
		return {"id": id, "started_unix": started_unix, "kind": kind, "prompt": prompt, "verdict": verdict(),
			"usage": usage, "final": usage_final, "bill": bill}

	func elapsed() -> float:
		var end := ended_ms if ended_ms >= 0 else Time.get_ticks_msec()
		return (end - started_ms) / 1000.0

	func live() -> bool:
		return ended_ms < 0

	## Whether the user's words can steer the run now: only while the game plays (the Rollout stage, from "Trying the
	## harness (run N)" and its "Now: ..." lines until the next stage), never once the run is over.
	func steerable() -> bool:
		return live() and stage == STAGE_ROLLOUT

	func took() -> float:
		if typeof(result.get("seconds")) in [TYPE_FLOAT, TYPE_INT]:
			return float(result.seconds)
		return elapsed()

	func attempt_count() -> int:
		if typeof(result.get("attempts")) in [TYPE_FLOAT, TYPE_INT]:
			return int(result.attempts)
		return attempts

	func verdict() -> String:
		if live():
			return "LIVE"
		if result.is_empty():
			return "STOPPED" if stop_asked else "NO RESULT"
		if result.has("verdict"):
			return str(result.verdict).to_upper()
		if result.get("tested") == true:
			return "GAME TESTED"  # (an exploration asked for as a test of the game, in the user's own words)
		if kind == "explore":
			return "EXPLORED"
		if result.has("reached"):
			return "GOAL HIT" if result.reached else "GOAL MISSED"
		if result.has("achieved"):
			return "GOAL HIT" if result.achieved else "GOAL MISSED"
		return "DONE"

	## The prompt as shown (the history, the masthead, the Settings page's runs, the report): the typed words, or
	## when none: "Explore", "Play a goal", "Verify", "Run".
	func shown_prompt() -> String:
		if prompt.strip_edges() != "":
			return prompt
		match kind:
			"explore": return "Explore"
			"goal": return "Play a goal"
			"verify": return "Verify"
		return "Run"

	func kind_word() -> String:
		match kind:
			"verify": return "VERIFY"
			"goal": return "GOAL"
			"explore": return "EXPLORE"
		return "RUN"

	func reason() -> String:
		if result.is_empty() and ended_why != "":
			return ended_why
		for k in ["reason", "summary"]:
			if typeof(result.get(k)) == TYPE_STRING and result[k] != "":
				return result[k]
		var notes = result.get("notes")
		if typeof(notes) == TYPE_ARRAY and notes.size() > 0:
			return str(notes[0])
		return ""

	## The evidence lines: result.evidence (a list or a text), or a goal's notes after the first (the summary).
	func evidence() -> Array:
		var ev = result.get("evidence")
		if typeof(ev) == TYPE_ARRAY:
			return ev.map(func(x): return str(x))
		if typeof(ev) == TYPE_STRING and ev != "":
			return Array(ev.split("\n", false))
		var notes = result.get("notes")
		if kind != "explore" and typeof(notes) == TYPE_ARRAY and notes.size() > 1:
			return notes.slice(1).map(func(x): return str(x))
		return []

	## What the agent changed in the game directly, not through play (a verification's setup): result.changes, each as
	## the brain words it (docs/protocol.md, Runs).
	func changes() -> Array:
		var out := []
		var cs = result.get("changes")
		if typeof(cs) == TYPE_ARRAY:
			for c in cs:
				if typeof(c) == TYPE_DICTIONARY and str(c.get("text", "")).strip_edges() != "":
					out.append(str(c["text"]).strip_edges())
		return out

	## What the user must know to trust the outcome or to get a better one (e.g. the game's picture couldn't be looked
	## at): result.alerts, each as the brain words it (docs/protocol.md, Runs); kept with the result in result.json.
	func alerts() -> Array:
		var out := []
		var al = result.get("alerts")
		if typeof(al) == TYPE_ARRAY:
			for a in al:
				if typeof(a) == TYPE_STRING and a.strip_edges() != "":
					out.append(a.strip_edges())
		return out

	## Whether the run added to or changed what is remembered about the game (result.memory.updated).
	func memory_updated() -> bool:
		var m = result.get("memory")
		return typeof(m) == TYPE_DICTIONARY and m.get("updated") == true

	## The run's recording as a flipbook: {"frames": the folder of its JPEG frames (000000.jpg, ...), "fps", "seconds",
	## "video": the MP4 the agent service wrote ("" for none)}; {} when it has none (result.recording, docs/protocol.md).
	func recording() -> Dictionary:
		var rec = result.get("recording")
		if typeof(rec) != TYPE_DICTIONARY or typeof(rec.get("frames")) != TYPE_STRING or str(rec.frames) == "":
			return {}
		var fps = rec.get("frames_fps")
		var secs = rec.get("seconds")
		return {"frames": str(rec.frames), "fps": float(fps) if typeof(fps) in [TYPE_INT, TYPE_FLOAT] and float(fps) > 0.0 else 2.0,
			"seconds": float(secs) if typeof(secs) in [TYPE_INT, TYPE_FLOAT] else 0.0,
			"video": str(rec.get("video", "")) if typeof(rec.get("video")) == TYPE_STRING else ""}

	## The report's line where the recording's player would go, when the run has no recording: "No recording: <why>",
	## why as kept with the result when it arrived (keep_no_recording(); saved in result.json); "" while the run goes
	## on, when it has a recording, or for a result kept before this was decided (no such field).
	func no_recording_line() -> String:
		if live() or typeof(result.get("recording")) == TYPE_DICTIONARY:
			return ""
		var why = result.get("no_recording")
		if typeof(why) != TYPE_STRING or why.strip_edges() == "":
			return ""
		return "No recording: " + why.strip_edges()

	## The agent's own findings: an explore run's notes after the first, and any `findings` list in the result.
	func agent_findings() -> Array:
		var out := []
		var notes = result.get("notes")
		if kind == "explore" and typeof(notes) == TYPE_ARRAY and notes.size() > 1:
			for n in notes.slice(1):
				out.append(str(n))
		var fs = result.get("findings")
		if typeof(fs) == TYPE_ARRAY:
			for f in fs:
				out.append(str(f.get("summary", f)) if typeof(f) == TYPE_DICTIONARY else str(f))
		for f in findings:
			if not is_engine(f) and not out.has(str(f.get("summary", ""))):
				out.append(str(f.get("summary", "")))
		return out

	## The engine's findings streamed during the run (the agent's go with agent_findings()).
	func engine_findings() -> Array:
		return findings.filter(func(f): return is_engine(f))

	## Whether a streamed finding is the engine's (its source, or, without one, a detector), else the agent's.
	static func is_engine(f: Dictionary) -> bool:
		var source := str(f.get("source", ""))
		return source == "engine" or (source == "" and f.has("detector"))

	## The highest activity event id received (0: none): what the next hello asks the feed from (`activity_since`).
	var last_activity_id := 0

	## A line of the feed, under the run's stage (the Feed tab groups by it); the oldest let go past MAX_FEED.
	func add_feed(text: String, kind_: String) -> void:
		add_line({"t": elapsed(), "text": text, "kind": kind_})

	func add_line(e: Dictionary) -> void:
		if not e.has("stage"):
			e["stage"] = stage
		feed.append(e)
		if feed.size() > MAX_FEED:
			feed = feed.slice(feed.size() - MAX_FEED)

	## The line that is activity event id, or the call it ended ("rid"); {} when the run has none.
	func find_activity(id: int) -> Dictionary:
		if id <= 0:
			return {}
		for i in range(feed.size() - 1, -1, -1):
			if int(feed[i].get("aid", 0)) == id or int(feed[i].get("rid", 0)) == id:
				return feed[i]
		return {}

	## One event of the activity feed (an `activity` frame; docs/protocol.md): a line of the feed ({"kind": "activity",
	## "aid", "akind", "t", "text", "detail", "stage"}), or, when it ends a call (`ends`), that call's result folded into
	## its line ("result", "rdetail", "rid"); its stage moves the run's (the status lines still do, for an older agent
	## service). An id seen already (the feed sent again after a reconnect) changes nothing. Whether anything changed.
	func on_activity(frame: Dictionary) -> bool:
		var id := int(frame.get("id", 0)) if typeof(frame.get("id")) in [TYPE_INT, TYPE_FLOAT] else 0
		if id <= 0 or not find_activity(id).is_empty():
			return false
		var text := str(frame.get("text", "")).strip_edges()  # (the dock scrubs it first)
		var akind := str(frame.get("akind", frame.get("kind", "")))
		var detail := str(frame.get("detail", "")) if typeof(frame.get("detail")) == TYPE_STRING else ""
		# The line's time is the run's clock now (what every other line shows: one clock per feed); the frame's `t`, the
		# agent service's own count, is kept apart (agent_t) for ordering and replay. A replayed event gets the time it
		# arrives too
		var agent_t := float(frame.get("t")) if typeof(frame.get("t")) in [TYPE_INT, TYPE_FLOAT] else -1.0
		var ends := int(frame.get("ends", 0)) if typeof(frame.get("ends")) in [TYPE_INT, TYPE_FLOAT] else 0
		last_activity_id = maxi(last_activity_id, id)
		var s := int(ACTIVITY_STAGES.get(str(frame.get("stage", "")), -1))
		if live() and s >= 0 and s != stage:
			enter_stage(s)
		if ends > 0:
			var call := find_activity(ends)
			if not call.is_empty():
				if int(call.get("rid", 0)) == 0:
					# the call and what came back: one line (its words, then the result's, quieter; two folds)
					call["result"] = text
					call["rdetail"] = detail
					call["rid"] = id
				return true
		add_line({"t": elapsed(), "agent_t": agent_t, "text": text, "kind": "activity", "stage": s if s >= 0 else stage, "aid": id,
			"akind": akind, "detail": detail})
		return true

	func enter_stage(s: int) -> void:
		var t := elapsed()
		stage_secs[stage] = float(stage_secs.get(stage, 0.0)) + t - stage_since
		stage = s
		stage_since = t

	## stage -> seconds spent in it, the current one's so far.
	func stage_times() -> Dictionary:
		var out := stage_secs.duplicate()
		out[stage] = float(out.get(stage, 0.0)) + elapsed() - stage_since
		return out

	## A status line from the agent server (already translated).
	func on_status(tr: Dictionary) -> void:
		if tr.stage >= 0:
			enter_stage(tr.stage)
		if tr.stage == STAGE_RESULTS:
			now_text = ""  # (the rollout's last action is over)
		if tr.attempt > 0:
			attempts += 1
			add_feed(tr.text, "attempt")
			return
		if tr.now:
			now_text = str(tr.text).trim_prefix("Now: ")
			if stage < STAGE_ROLLOUT:
				enter_stage(STAGE_ROLLOUT)
			add_feed(tr.text, "now")
			return
		add_feed(tr.text, "status")

	func on_finding(f: Dictionary) -> bool:
		var sig := str(f.get("signature", f.get("summary", "")))
		for i in findings.size():
			if str(findings[i].get("signature", findings[i].get("summary", ""))) == sig:
				findings[i] = f
				return false
		findings.append(f)
		return true

	func color_of(action: String) -> Color:
		if not colors.has(action):
			var pal: Array = HT.ACTION_COLORS
			colors[action] = pal[colors.size() % pal.size()]
		return colors[action]

	func add_step(frame: Dictionary) -> Step:
		var st := Step.new()
		st.frame = frame
		st.n = int(frame.get("call", frame.get("step", steps.size() + 1)))
		var prev: Step = steps[-1] if steps.size() > 0 else null
		st.attempt = 1 if prev == null else (prev.attempt + 1 if st.n < prev.n else prev.attempt)
		st.t = float(frame.get("t", 0.0)) if typeof(frame.get("t")) in [TYPE_FLOAT, TYPE_INT] else 0.0
		st.wall = float(frame.get("wall", 0.0)) if typeof(frame.get("wall")) in [TYPE_FLOAT, TYPE_INT] else 0.0
		var secs = frame.get("seconds", {})
		if typeof(secs) == TYPE_DICTIONARY and typeof(secs.get("jev")) in [TYPE_FLOAT, TYPE_INT]:
			st.ms = float(secs.jev) * 1000.0
		var decided = frame.get("decided", {})
		var summary := ""
		if typeof(decided) == TYPE_DICTIONARY:
			summary = str(decided.get("summary", ""))
			var chosen = decided.get("chosen", [])
			if typeof(chosen) == TYPE_ARRAY and chosen.size() > 0:
				st.chosen = str(chosen[0])
		var parts := summary.split(": ", true, 1)
		st.tag = TAGS.get(parts[0], parts[0].to_upper())
		if st.chosen == "" and parts.size() > 1 and parts[1] != "None":
			st.chosen = parts[1]
		var input = frame.get("input", {})
		if typeof(input) == TYPE_DICTIONARY and typeof(input.get("state")) == TYPE_DICTIONARY:
			st.state = input.state
		var nd = st.state.get("now_doing")
		if typeof(nd) == TYPE_DICTIONARY:
			st.current = str(nd.get("option", ""))
		st.plan = str(frame.get("plan_step", "")) if frame.get("plan_step") != null else ""
		var out = frame.get("output", {})
		if typeof(out) == TYPE_DICTIONARY and typeof(out.get("answers")) == TYPE_DICTIONARY:
			var pick = out.answers.get("pick")
			if typeof(pick) == TYPE_DICTIONARY:
				st.probs = pick
		# (the colors given out in order of first appearance: the action running, the one chosen, then the picks by
		# probability; the 8 colors in turn)
		if st.current != "":
			color_of(st.current)
		if st.chosen != "":
			color_of(st.chosen)
		for pair in st.sorted_probs():
			color_of(pair[0])
		st.error = str(frame.get("error", "")) if typeof(frame.get("error")) == TYPE_STRING else ""
		if st.error != "":
			st.tag = "FAILED"
		steps.append(st)
		step_count += 1
		return st

	func previous_step(st: Step) -> Step:
		var i := steps.find(st)
		if i > 0 and steps[i - 1].attempt == st.attempt:
			return steps[i - 1]
		return null

	func step_attempts() -> int:
		return 0 if steps.is_empty() else steps[-1].attempt


## The run as a markdown report (Copy report; the same structure in every engine): its id, the prompt, the verdict, the
## duration, harnesses, policy steps and alerts, the result's alerts as quotes, the reason, the evidence, what was
## changed directly, then the findings (the agent's, then the engine's with their severity and detector).
static func report_markdown(r) -> String:
	var lines := ["# Autopilot run %s" % r.id, "", "**Prompt:** %s" % r.shown_prompt(), "",
		"**Verdict:** %s  " % r.verdict(),
		"**Duration:** %s · **Harnesses:** %d · **Policy steps:** %d · **Alerts:** %d" % [clock(r.took()), r.attempt_count(), maxi(r.step_count, r.steps.size()), r.engine_findings().size()]]
	for a in r.alerts():
		lines += ["", "> " + scrub(a)]
	if r.reason() != "":
		lines += ["", "## Reason", "", scrub(r.reason())]
	var ev: Array = r.evidence()
	if not ev.is_empty():
		lines += ["", "## Evidence", ""]
		for e in ev:
			lines.append("- " + e)
	var ch: Array = r.changes()
	if not ch.is_empty():
		lines += ["", "## Changed directly (not through play)", ""]
		for c in ch:
			lines.append("- " + c)
	var ag: Array = r.agent_findings()
	if not ag.is_empty() or not r.engine_findings().is_empty():
		lines += ["", "## Findings", ""]
		for a in ag:
			lines.append("- [agent] " + a)
		for f in r.engine_findings():
			var loc := where_text(f.get("where"))
			var count := int(f.get("count", 1))
			lines.append("- [engine · %s · %s] %s%s%s%s" % [str(f.get("severity", "")), str(f.get("detector", "")),
				scrub(str(f.get("summary", ""))), (" (×%d)" % count) if count > 1 else "", (" at %s cm" % loc) if loc != "" else "",
				" (not a bug)" if r.not_a_bug.has(str(f.get("signature", ""))) else ""])
	return "\n".join(lines) + "\n"
