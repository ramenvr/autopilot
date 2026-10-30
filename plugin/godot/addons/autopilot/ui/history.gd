## The run history kept between editor sessions, without UI: a run (ui/model.gd Run) as JSON and back, one file per
## run in a folder per project (.godot/editor/autopilot_runs/, beside the usage file: never committed), the newest
## KEEP_RUNS kept (as Unreal's Saved/Autopilot/Runs/). A saved run's policy steps aren't kept, only how many came, so
## a run from an earlier session has no Policy log.
@tool
extends RefCounted

const Model = preload("model.gd")
const Usage = preload("usage.gd")
const Prefs = preload("../lib/prefs.gd")

const DIR_NAME := "autopilot_runs"
const KEEP_RUNS := 50  # the most runs kept (the oldest go first)
## Where Reset memory puts the files of the runs it takes off the history: <dir>/.reset-backup/<time>/ (as the agent
## service keeps the memory it resets: autopilot/memory.py), never read again; removing Autopilot deletes it with dir.
const RESET_BACKUP := ".reset-backup"
const VERSION := 1
## A run live when the editor closed: why it has no result, and its feed's last line (as in the other engines).
const CLOSED_REASON := "The editor closed before the result came."
const CLOSED_LINE := "Editor closed: no result"


static func _num(d: Dictionary, key: String, default := 0.0) -> float:
	var v = d.get(key)
	return float(v) if typeof(v) in [TYPE_INT, TYPE_FLOAT] else default


static func _str(d: Dictionary, key: String) -> String:
	var v = d.get(key)
	return v if typeof(v) == TYPE_STRING else ""


static func _dict(d: Dictionary, key: String) -> Dictionary:
	var v = d.get(key)
	return v if typeof(v) == TYPE_DICTIONARY else {}


## A value read back from JSON with its whole numbers whole again (JSON has only floats here): the run frame sent again
## (Run again) says "minutes": 10, as it was sent.
static func _whole(v):
	if typeof(v) == TYPE_FLOAT and is_equal_approx(v, round(v)) and absf(v) < 1e15:
		return int(v)
	if typeof(v) == TYPE_DICTIONARY:
		var out := {}
		for k in v:
			out[k] = _whole(v[k])
		return out
	if typeof(v) == TYPE_ARRAY:
		return v.map(func(x): return _whole(x))
	return v


static func _dicts(d: Dictionary, key: String) -> Array:
	var v = d.get(key)
	return v.filter(func(x): return typeof(x) == TYPE_DICTIONARY) if typeof(v) == TYPE_ARRAY else []


## The run as saved: what the history, its result, feed and report show, and its usage and bill.
static func to_dict(r) -> Dictionary:
	var stage_secs := {}
	for k in r.stage_secs:
		stage_secs[str(k)] = float(r.stage_secs[k])
	return {"version": VERSION, "id": r.id, "kind": r.kind, "prompt": r.prompt, "request": r.request,
		"started_unix": r.started_unix, "elapsed": r.elapsed(), "done": not r.live(), "stage": r.stage,
		"stage_since": r.stage_since, "stage_secs": stage_secs, "attempts": r.attempts, "now": r.now_text,
		"feed": r.feed, "findings": r.findings, "not_a_bug": r.not_a_bug.keys(), "result": r.result,
		"report_dir": r.report_dir, "step_count": r.step_count, "stop_asked": r.stop_asked, "ended_why": r.ended_why,
		"usage": r.usage, "final": r.usage_final, "bill": r.bill, "origin": r.origin, "last_activity": r.last_activity_id}


## A saved run (to_dict()) as a Run, ended; null when it isn't one (no id or start). A run live when it was saved (the
## editor closed before its result came) ends with no result: CLOSED_REASON, and CLOSED_LINE in its feed.
static func from_dict(d):
	if typeof(d) != TYPE_DICTIONARY or _str(d, "id") == "" or not typeof(d.get("started_unix")) in [TYPE_INT, TYPE_FLOAT]:
		return null
	var r := Model.Run.new()
	r.id = _str(d, "id")
	r.kind = _str(d, "kind")
	r.prompt = _str(d, "prompt")
	r.request = _whole(_dict(d, "request"))
	r.started_unix = float(d.started_unix)
	# The clock of a saved run: it started at 0 and ended at what it took
	r.started_ms = 0
	r.ended_ms = int(round(maxf(0.0, _num(d, "elapsed")) * 1000.0))
	r.stage = clampi(int(_num(d, "stage")), 0, Model.STAGE_REPORT)
	r.stage_since = _num(d, "stage_since")
	var secs := _dict(d, "stage_secs")
	for k in secs:
		if str(k).is_valid_int() and typeof(secs[k]) in [TYPE_INT, TYPE_FLOAT]:
			r.stage_secs[int(k)] = float(secs[k])
	r.attempts = int(_num(d, "attempts"))
	r.now_text = _str(d, "now")
	r.feed = _dicts(d, "feed")
	r.findings = _dicts(d, "findings")
	var nab = d.get("not_a_bug")
	if typeof(nab) == TYPE_ARRAY:
		for sig in nab:
			r.not_a_bug[str(sig)] = true
	r.result = _dict(d, "result")
	r.report_dir = _str(d, "report_dir")
	r.step_count = int(_num(d, "step_count"))
	r.stop_asked = d.get("stop_asked") == true
	r.ended_why = _str(d, "ended_why")
	r.origin = _str(d, "origin")  # (who started it, when the tab didn't: "Started from Claude Code" stays after a restart)
	r.last_activity_id = int(_num(d, "last_activity"))  # (0 in runs kept before the activity feed)
	r.usage = Usage.parse(d.get("usage", []))
	r.usage_final = d.get("final") == true
	r.bill = Usage.parse_bill(d.get("bill"))
	# Its recording's copy in the run's folder, when there is one: the editor may have closed before the copy was kept
	var rec = r.result.get("recording")
	var copied := r.report_dir.path_join("recording") if r.report_dir != "" else ""
	if typeof(rec) == TYPE_DICTIONARY and copied != "" and str(rec.get("frames", "")) != copied and _has_frames(copied):
		rec["frames"] = copied
	if d.get("done") != true:
		r.ended_why = CLOSED_REASON
		r.feed.append({"t": r.elapsed(), "text": CLOSED_LINE, "kind": "info"})
	return r


static func _has_frames(dir: String) -> bool:
	if not DirAccess.dir_exists_absolute(dir):
		return false
	for name in DirAccess.get_files_at(dir):
		if name.get_extension().to_lower() == "jpg":
			return true
	return false


## The runs saved in dir, oldest first, each with its history_file; the newest KEEP_RUNS (the files of older ones
## deleted). A file that doesn't read as a run is left out; a run live when it was saved is saved again ended.
static func load_runs(dir: String) -> Array:
	if dir == "" or not DirAccess.dir_exists_absolute(dir):
		return []
	var loaded := []
	for name in DirAccess.get_files_at(dir):
		if name.get_extension() != "json":
			continue
		var d = Prefs.parse_json(FileAccess.get_file_as_string(dir.path_join(name)))
		var r = from_dict(d)
		if r == null:
			continue
		r.history_file = name
		if d.get("done") != true:
			save_run(dir, r)  # (with its NO RESULT)
		loaded.append(r)
	loaded.sort_custom(func(a, b): return a.started_unix < b.started_unix)
	while loaded.size() > KEEP_RUNS:
		DirAccess.remove_absolute(dir.path_join(loaded[0].history_file))
		loaded.remove_at(0)
	return loaded


## The file a run is saved in: its id (when it started), with -2, -3… when another of runs has that name.
static func file_for(r, runs: Array) -> String:
	var taken := {}
	for o in runs:
		if o != r and o.history_file != "":
			taken[o.history_file] = true
	var name := "%s.json" % r.id
	var n := 2
	while taken.has(name):
		name = "%s-%d.json" % [r.id, n]
		n += 1
	return name


## Write the run to its file in dir (atomically). OK or an error.
static func save_run(dir: String, r) -> Error:
	if dir == "" or r.history_file == "":
		return ERR_FILE_BAD_PATH
	return Prefs.write_atomic(dir.path_join(r.history_file), JSON.stringify(to_dict(r), "", false, true) + "\n")


## The files of runs moved out of dir, to a new folder in its RESET_BACKUP named for now (-1, -2… after another this
## second): kept, never loaded again. The folder ("" when no file moved).
static func set_aside(dir: String, moved: Array) -> String:
	if dir == "":
		return ""
	var stamp := Time.get_datetime_string_from_system().replace("-", "").replace(":", "").replace("T", "-")
	var backup := dir.path_join(RESET_BACKUP).path_join(stamp)
	var n := 1
	while DirAccess.dir_exists_absolute(backup):  # (two resets in one second)
		backup = dir.path_join(RESET_BACKUP).path_join("%s-%d" % [stamp, n])
		n += 1
	var any := false
	for r in moved:
		var f: String = r.history_file
		if f == "" or not FileAccess.file_exists(dir.path_join(f)):
			continue
		if not any:
			DirAccess.make_dir_recursive_absolute(backup)
			any = true
		DirAccess.rename_absolute(dir.path_join(f), backup.path_join(f))
	return backup if any else ""


## The files of the runs before the newest KEEP_RUNS deleted (the runs stay listed until the editor closes).
static func drop_older(dir: String, runs: Array) -> void:
	for i in range(0, runs.size() - KEEP_RUNS):
		var f: String = runs[i].history_file
		if dir != "" and f != "" and FileAccess.file_exists(dir.path_join(f)):
			DirAccess.remove_absolute(dir.path_join(f))
