## The agent service on this computer, started by the addon (docs/protocol.md "Started by the plugin").
## When the addon carries its own (a release download: addons/autopilot/agent_service/ holds the installer and
## agent-service.json naming a commit or a sha256), that one always runs:
##  - found: the "Agent service command" setting (a full command line, e.g. C:\autopilot\.venv\Scripts\python.exe
##    -m autopilot; never updated nor replaced), else the installer's place (%USERPROFILE%\.local\bin\autopilot.exe,
##    ~/.local/bin/autopilot): installed there first when the agent service installed (Autopilot/agent-service.json)
##    isn't the addon's (by sha256), or when it is missing but an `autopilot` is on PATH (that one isn't used); once per
##    editor session: when that fails, the installed one is started anyway. Nothing installed anywhere: not_installed
##    (the first-run setup's Install installs it);
##  - one Autopilot/brain.json names, running, is used when its "commit" is the addon's agent-service.json's; another
##    (or one that names none: from before) is stopped, when its program is an agent service's (autopilot, or uv's
##    python) and it is the process that wrote brain.json (it started no later than brain.json's "started" + 5 s: a pid
##    reused by another program is never stopped), and the addon's started instead: once per editor session, never
##    while the dock is connected, never with the setting set.
## Without one (a repository checkout): the setting, else `autopilot` on PATH, else the installer's place; one brain.json
## names, running, is used whatever it is.
## Either way:
##  - started as `<autopilot> serve --port 0 --exit-with <this editor's pid>`, no window, its output appended to
##    Autopilot/logs/agent-service-<yyyymmdd-hhmmss>.log (the 5 newest kept), unless one runs already (several editors
##    share one; it ends with the editor that started it);
##  - ready once brain.json names the process started (polled every 250 ms, up to 30 s);
##  - installed by the installer the addon carries (install.ps1 run by PowerShell on Windows, install.sh by sh
##    elsewhere), its output in Autopilot/logs/install-*.log.
## Removing Autopilot from this computer (the Settings page's Uninstall, uninstall()): the command
## find() gives runs `<autopilot> uninstall --yes [--keep-memory] --log <log>` (autopilot/uninstall.py), in the
## temporary folder (never in a folder it removes), its output in <temp>/autopilot-uninstall-<yyyymmdd-hhmmss>.log;
## with none found (not installed) the addon empties the settings folder itself, but for memory/ (its shared runs not
## sent yet are sent by the agent service: shared run data is never removed) and the Aura app's aura-brain.json.
## The decisions are the static plan_start(), on_announced(), stoppable() and owner_check(). The dock owns one and polls it; the process
## launcher and the checks on processes can be replaced (tests).
@tool
extends RefCounted

signal changed

const Prefs = preload("prefs.gd")
const LOGS := "logs"
const KEEP_LOGS := 5
const START_TIMEOUT := 30.0
const STOP_WAIT := 5.0  # (s: an older agent service stopped has ended within it)
const OWNER_SLACK := 5.0  # (s: the process announced started at most this long after brain.json's "started": it wrote it)
const POLL_EVERY := 0.25
const INSTALLER_DIR := "res://addons/autopilot/agent_service"
const STAMP := "agent-service.json"  # which agent service: beside the installer, and the installed one's in Autopilot/
const SPECIAL := " \t&()[]{}^=;!'+,`~<>|%"

## idle | not_installed | installing | install_failed | starting | ready | stopped | failed
var state := "idle"
var url := ""  # ready: where it listens
var message := ""  # install_failed, stopped, failed: what happened, in words
var log_path := ""  # the log of the last start or install
var pid := 0  # the process started last (the shell running the agent service or the installer)

## Replaceable (tests): the "Agent service command" setting; the places looked at for autopilot ([]: default_candidates());
## the folder holding install.ps1/install.sh ("": the addon's agent_service/); the home folder the installer puts autopilot
## under ("": the user's); how processes are started and checked.
var command_setting := ""
var candidates: Array = []
var installer_dir := ""
var home_dir := ""
var launcher := func(program: String, args: PackedStringArray) -> int: return OS.create_process(program, args)
var running := func(p: int) -> bool: return OS.is_process_running(p)
var exit_code := func(p: int) -> int: return OS.get_process_exit_code(p)
var editor_pid := OS.get_process_id()
var os_name := OS.get_name()
## Replaceable (tests): the program a process runs ("" when there is no such process: process_image()), how one is
## stopped (stop_process()), and whether the dock is connected to the agent service (then it is never replaced).
var image_of := func(p: int) -> String: return process_image(p, OS.get_name())
var stopper := func(p: int) -> bool: return stop_process(p, OS.get_name())
var started_of := func(p: int) -> float: return process_started(p, OS.get_name())
var in_use := func() -> bool: return false

var _launched_unix := 0.0
var _stale_pid := 0  # the process brain.json named when this one was started (one that had ended)
var _poll_at := 0.0
var _update_tried := false  # (the addon's agent service installed over an older one, once per editor session)
var _updating := false  # (the install running is that update)
var _replace_tried := false  # (an older agent service running replaced by the addon's, once per editor session)
var _kept_said := false  # (that another one than the addon's runs, and is used, said once)

const REMOVE_LOG := "autopilot-uninstall"
const REMOVE_TIMEOUT := 600.0  # (s: shared runs not sent yet are sent first, which may take a while)
const UNINSTALL_KEPT := 3  # autopilot uninstall's exit code: removed, but for what its log names
const MEMORY := "memory"
## Removing Autopilot from this computer (uninstall()): "" (not asked), running, done, kept (done but for what the log
## names), failed. While it isn't "", nothing starts, installs or connects to the agent service (the dock).
var removal := ""
var removal_log := ""
var removal_message := ""  # failed: why, when the log can't say ("" when it can)
var removal_keep := true  # what Autopilot learned kept (--keep-memory)
var _removal_pid := 0
var _removal_at := 0.0


# --- finding it --------------------------------------------------------------------------------------

## A command line split into its words: spaces separate them, except inside double quotes (which are dropped).
static func split_command(line: String) -> PackedStringArray:
	var out := PackedStringArray()
	var word := ""
	var quoted := false
	var has := false
	for c in line.strip_edges():
		if c == "\"":
			quoted = not quoted
			has = true
		elif (c == " " or c == "\t") and not quoted:
			if has:
				out.append(word)
			word = ""
			has = false
		else:
			word += c
			has = true
	if has:
		out.append(word)
	return out


## Where autopilot may be, in order: each folder on PATH, then where the installer puts it (uv's tool folder).
static func default_candidates(system := OS.get_name(), path_env := OS.get_environment("PATH"), home := "") -> Array:
	var windows := system == "Windows"
	if home == "":
		home = OS.get_environment("USERPROFILE") if windows else ""
		if home == "":
			home = OS.get_environment("HOME")
	var out := []
	for folder in path_env.split(";" if windows else ":", false):
		var f := folder.strip_edges().trim_prefix("\"").trim_suffix("\"")
		if f == "":
			continue
		if windows:
			for name in ["autopilot.exe", "autopilot.cmd", "autopilot.bat"]:
				out.append(f.path_join(name))
		else:
			out.append(f.path_join("autopilot"))
	if home != "":
		out.append(home.path_join(".local").path_join("bin").path_join("autopilot.exe" if windows else "autopilot"))
	return out


## The command that runs autopilot (its words, before "serve"): the setting's, else the first candidate that exists; empty
## when there is none (not installed).
static func find_command(setting: String, places: Array) -> PackedStringArray:
	if setting.strip_edges() != "":
		return split_command(setting)
	for p in places:
		if FileAccess.file_exists(str(p)):
			return PackedStringArray([str(p)])
	return PackedStringArray()


## The command to start (its words before "serve"; empty: none to start): plan_start()'s with this computer's files.
func find() -> PackedStringArray:
	return _plan().command


## plan_start() with this computer's files: the addon's stamp, the installed one, the search, the installer's place.
func _plan() -> Dictionary:
	var shipped := shipped_stamp()
	var own := own_place(home_folder(), os_name == "Windows")
	return plan_start(command_setting, carries(shipped, has_installer()), shipped, Prefs.read_json(Prefs.file(STAMP)),
		find_command(command_setting, candidates if not candidates.is_empty() else default_candidates(os_name)), own,
		own != "" and FileAccess.file_exists(own))


## The addon's agent_service/agent-service.json ({} when it has none).
func shipped_stamp() -> Dictionary:
	return Prefs.read_json(installer_folder().path_join(STAMP))


## Whether the addon carries its own agent service: its agent-service.json (shipped) names a commit or a sha256, and the
## installer is there (installer). A repository checkout carries none.
static func carries(shipped: Dictionary, installer: bool) -> bool:
	return installer and (_word(shipped, "commit") != "" or _word(shipped, "sha256") != "")


static func _word(d: Dictionary, key: String) -> String:
	var v = d.get(key)
	return str(v).strip_edges() if typeof(v) == TYPE_STRING else ""


## Where the installer puts autopilot under home (.local/bin/autopilot[.exe]; "" without a home).
static func own_place(home: String, windows: bool) -> String:
	return home.path_join(".local").path_join("bin").path_join("autopilot.exe" if windows else "autopilot") if home != "" else ""


## Whether the agent service installed (installed: Autopilot/agent-service.json, {} when none) is the one the addon carries
## (shipped): the same sha256 (the same commit when the addon's names no sha256).
static func same_stamp(shipped: Dictionary, installed: Dictionary) -> bool:
	var key := "sha256" if _word(shipped, "sha256") != "" else "commit"
	return _word(shipped, key) != "" and _word(installed, key) == _word(shipped, key)


## What starts the agent service when none runs: {"command": its words before "serve" (empty: nothing to start), "install":
## whether the addon's installer runs first}. setting: the "Agent service command" setting; carried: carries(); shipped,
## installed: the stamps (same_stamp()); search: what was found without the addon's (find_command(): the setting, else the
## first autopilot on PATH, else the installer's place); own: the installer's place, own_exists: whether it is there.
##  - the setting: as it is, never installed over;
##  - the addon carries its own: the installer's place, the addon's installed first when it is another one, or when it is
##    missing but an autopilot was found elsewhere (on PATH: that one would have run; it never does). Nothing installed
##    anywhere: nothing to start and nothing installed here (not installed: the first-run setup's Install does it);
##  - else: the search's, nothing installed.
static func plan_start(setting: String, carried: bool, shipped: Dictionary, installed: Dictionary, search: PackedStringArray,
		own: String, own_exists: bool) -> Dictionary:
	if setting.strip_edges() != "":
		return {"command": split_command(setting), "install": false}
	if carried and own != "":
		if own_exists:
			return {"command": PackedStringArray([own]), "install": not same_stamp(shipped, installed)}
		return {"command": PackedStringArray(), "install": not search.is_empty()}
	return {"command": search, "install": false}


## What to do with the agent service brain.json announces (said; it runs): "use" it, or "replace" it (stop it, start the
## addon's): replaced only when the addon carries its own (carried) whose agent-service.json (shipped) names a commit, the
## one running names another or none (from before), the setting isn't set (from_setting) and the dock isn't connected
## to it (in_use: a connection open is left alone).
static func on_announced(carried: bool, shipped: Dictionary, said: Dictionary, from_setting: bool, in_use: bool) -> String:
	var want := _word(shipped, "commit")
	if not carried or from_setting or in_use or want == "":
		return "use"
	return "use" if _word(said, "commit") == want else "replace"


## Whether a process running image (its program, a path or a name) may be stopped as an agent service: autopilot, or a
## Python (uv's tool runs Autopilot under its own: python, pythonw, python3, python3.N), .exe or not.
static func stoppable(image: String) -> bool:
	var name := image.strip_edges().replace("\\", "/").get_file().to_lower()
	if name.ends_with(".exe"):
		name = name.trim_suffix(".exe")
	return name == "autopilot" or RegEx.create_from_string("^python(w|3(\\.\\d+)?)?$").search(name) != null


## Why the process announced in brain.json (said) may not be the one that announced, from when it started (unix seconds;
## < 0: it couldn't be read); "" when it is (it may be stopped). It is when it was running already when brain.json was
## written: started no later than said's "started" + OWNER_SLACK. One started later is another program that got its pid
## after the agent service ended. Without "started", or without the start time, it can't be told: never stopped.
static func owner_check(process_started: float, said: Dictionary) -> String:
	var when = said.get("started")
	if typeof(when) not in [TYPE_INT, TYPE_FLOAT] or float(when) <= 0.0:
		return "brain.json doesn't say when it was written"
	if process_started <= 0.0:
		return "when the process started couldn't be read"
	if process_started > float(when) + OWNER_SLACK:
		return "the process started after brain.json was written (another program has its pid)"
	return ""


## Whether the process announced in brain.json (said), started at process_started (unix seconds; < 0: unknown), is
## the one that announced it, so that it may be stopped (owner_check()).
static func may_stop(process_started: float, said: Dictionary) -> bool:
	return owner_check(process_started, said) == ""


## Seconds from `ps -o etime=` ([[dd-]hh:]mm:ss); -1 when it isn't that.
static func parse_etime(text: String) -> int:
	var t := text.strip_edges()
	var days := 0
	if "-" in t:
		if not t.get_slice("-", 0).is_valid_int():
			return -1
		days = t.get_slice("-", 0).to_int()
		t = t.get_slice("-", 1)
	var parts := t.split(":")
	if parts.size() < 2 or parts.size() > 3:
		return -1
	var secs := 0
	for part in parts:
		if not part.is_valid_int() or part.to_int() < 0:
			return -1
		secs = secs * 60 + part.to_int()
	return days * 86400 + secs


## When the process pid started (unix seconds; -1 when it can't be read): PowerShell's Get-Process on Windows, ps's
## elapsed time elsewhere.
static func process_started(p: int, system: String) -> float:
	if p <= 0:
		return -1.0
	var out := []
	if system == "Windows":
		var script := "[DateTimeOffset]::new((Get-Process -Id %d -ErrorAction Stop).StartTime).ToUnixTimeMilliseconds()" % p
		if OS.execute("powershell", ["-NoProfile", "-NonInteractive", "-Command", script], out) != 0 or out.is_empty():
			return -1.0
		var ms := str(out[0]).strip_edges()
		return ms.to_float() / 1000.0 if ms.is_valid_int() and ms.to_int() > 0 else -1.0
	if OS.execute("/bin/sh", ["-c", "ps -o etime= -p %d 2>/dev/null" % p], out) != 0 or out.is_empty():
		return -1.0
	var elapsed := parse_etime(str(out[0]))
	return Time.get_unix_time_from_system() - elapsed if elapsed >= 0 else -1.0


## The program the process pid runs ("" when there is no such process): tasklist on Windows, ps elsewhere.
static func process_image(p: int, system: String) -> String:
	if p <= 0:
		return ""
	var out := []
	if system == "Windows":
		if OS.execute("tasklist", ["/FI", "PID eq %d" % p, "/NH", "/FO", "CSV"], out) != 0 or out.is_empty():
			return ""
		for line in str(out[0]).split("\n"):
			var cells := line.strip_edges().split("\",\"")
			if cells.size() >= 2 and cells[1] == str(p):
				return cells[0].trim_prefix("\"")
		return ""
	if OS.execute("/bin/sh", ["-c", "ps -p %d -o comm= 2>/dev/null" % p], out) != 0 or out.is_empty():
		return ""
	return str(out[0]).strip_edges()


## Stop the process pid (and, on Windows, what it started); whether that was asked of the system without an error.
static func stop_process(p: int, system: String) -> bool:
	if p <= 0:
		return false
	var out := []
	if system == "Windows":
		return OS.execute("taskkill", ["/PID", str(p), "/T", "/F"], out) == 0
	return OS.execute("/bin/sh", ["-c", "kill %d" % p], out) == 0


## The folder holding the installer the addon carries (absolute).
func installer_folder() -> String:
	return installer_dir if installer_dir != "" else ProjectSettings.globalize_path(INSTALLER_DIR)


## Whether the addon carries the installer (the release zips put it in; a repository checkout has none).
func has_installer() -> bool:
	var dir := installer_folder()
	return FileAccess.file_exists(dir.path_join("install.ps1" if os_name == "Windows" else "install.sh"))


## The folder the installer puts autopilot under (.local/bin in it): home_dir, else USERPROFILE on Windows, else HOME.
func home_folder() -> String:
	if home_dir != "":
		return home_dir
	var h := OS.get_environment("USERPROFILE") if os_name == "Windows" else ""
	return h if h != "" else OS.get_environment("HOME")


## The agent service brain.json announces (running) replaced by the addon's when on_announced() says so: stopped (only
## when its program is an agent service's), brain.json removed once it has ended. Whether it was (then none runs); false:
## use it. Once per editor session.
func _replace_announced() -> bool:
	var said := Prefs.read_json(Prefs.file(Prefs.OWN_BRAIN_FILE))
	var shipped := shipped_stamp()
	if on_announced(carries(shipped, has_installer()), shipped, said, command_setting.strip_edges() != "", in_use.call()) != "replace":
		return false
	var p := int(said.get("pid", 0))
	if _replace_tried:
		if not _kept_said:
			_kept_said = true
			print("[Autopilot] The agent service running (pid %d) isn’t this plugin’s, and was replaced once already this session: using it" % p)
		return false
	_replace_tried = true
	var image: String = image_of.call(p)
	if image == "":
		_forget_announcement(p)  # (it ended meanwhile)
		return true
	if not stoppable(image):
		print("[Autopilot] The agent service announced (pid %d) isn’t this plugin’s, but its program (%s) isn’t an agent service’s: leaving it running and using it" % [p, image.get_file()])
		return false
	var why := owner_check(float(started_of.call(p)), said)
	if why != "":
		print("[Autopilot] The agent service announced (pid %d) isn’t this plugin’s, but it may not be the process that announced it (%s): leaving it running and using it" % [p, why])
		return false
	print("[Autopilot] An older agent service was running (pid %d): replacing it with this plugin’s" % p)
	stopper.call(p)
	var until := Time.get_ticks_msec() + int(STOP_WAIT * 1000.0)
	while str(image_of.call(p)) != "" and Time.get_ticks_msec() < until:
		OS.delay_msec(100)
	if str(image_of.call(p)) != "":
		print("[Autopilot] Couldn’t stop the older agent service (pid %d): using it" % p)
		return false
	_forget_announcement(p)
	return true


## brain.json removed when it still names the process p (ended), and p no longer thought running.
static func _forget_announcement(p: int) -> void:
	Prefs._alive_cache.erase(p)
	var path := Prefs.file(Prefs.OWN_BRAIN_FILE)
	var said := Prefs.read_json(path)
	if typeof(said.get("pid")) in [TYPE_INT, TYPE_FLOAT] and int(said.pid) == p:
		DirAccess.remove_absolute(path)


# --- command lines -----------------------------------------------------------------------------------

## A word for cmd.exe: in double quotes when it has a space or a character cmd treats specially.
static func cmd_quote(word: String) -> String:
	if word == "":
		return "\"\""
	for c in SPECIAL:
		if c in word:
			return "\"%s\"" % word.replace("\"", "")
	return word


## [program, args] that run argv with no window, its output (and errors) appended to log: through cmd.exe on Windows
## (/s /c: cmd keeps the line as given once Godot has put it in quotes), through sh elsewhere (exec: the same process).
## PYTHONUNBUFFERED: the agent service's lines reach the log as they are printed (not lost when it ends).
static func shell_argv(argv: PackedStringArray, log: String, system: String) -> Array:
	if system == "Windows":
		var words := []
		for i in argv.size():
			# (the program's path with Windows' separators: cmd may read "/x" in it as a switch)
			words.append(cmd_quote(argv[i].replace("/", "\\") if i == 0 else argv[i]))
		var line := "set PYTHONUNBUFFERED=1& %s >> %s 2>&1" % [" ".join(words), cmd_quote(log.replace("/", "\\"))]
		return ["cmd.exe", PackedStringArray(["/d", "/s", "/c", line])]
	var args := PackedStringArray(["-c", "log=\"$1\"; shift; PYTHONUNBUFFERED=1 exec \"$@\" >> \"$log\" 2>&1", "sh", log])
	args.append_array(argv)
	return ["/bin/sh", args]


## [program, args] that run argv as shell_argv() does, in the folder cwd first.
static func shell_argv_in(cwd: String, argv: PackedStringArray, log: String, system: String) -> Array:
	if system == "Windows":
		var pa := shell_argv(argv, log, system)
		var line := "cd /d %s& %s" % [cmd_quote(cwd.replace("/", "\\")), pa[1][3]]
		return ["cmd.exe", PackedStringArray(["/d", "/s", "/c", line])]
	var args := PackedStringArray(["-c", "cd \"$1\" || exit 1; log=\"$2\"; shift 2; PYTHONUNBUFFERED=1 exec \"$@\" >> \"$log\" 2>&1",
		"sh", cwd, log])
	args.append_array(argv)
	return ["/bin/sh", args]


## The temporary folder (TEMP or TMP on Windows, TMPDIR elsewhere, else /tmp).
static func temp_folder(system := OS.get_name()) -> String:
	for name in (["TEMP", "TMP"] if system == "Windows" else ["TMPDIR"]):
		var t := OS.get_environment(name).strip_edges()
		if t != "":
			return t.trim_suffix("/").trim_suffix("\\")
	return OS.get_environment("LOCALAPPDATA").path_join("Temp") if system == "Windows" else "/tmp"


## [program, args] removing Autopilot from this computer (command: find()), in cwd, its output (and, on Windows, the
## command's removal once it has ended) to log.
static func uninstall_argv(command: PackedStringArray, keep_memory: bool, log: String, system: String, cwd: String) -> Array:
	var argv := PackedStringArray(command)
	argv.append_array(["uninstall", "--yes"])
	if keep_memory:
		argv.append("--keep-memory")
	argv.append_array(["--log", log])
	return shell_argv_in(cwd, argv, log, system)


## The words that start the agent service for this editor: free port, ends with the editor.
static func serve_argv(command: PackedStringArray, pid_of_editor: int) -> PackedStringArray:
	var argv := PackedStringArray(command)
	argv.append_array(["serve", "--port", "0", "--exit-with", str(pid_of_editor)])
	return argv


## [program, args] starting the agent service (command: find()), its output to log.
static func start_argv(command: PackedStringArray, pid_of_editor: int, log: String, system: String) -> Array:
	return shell_argv(serve_argv(command, pid_of_editor), log, system)


## [program, args] running the installer in folder, its output to log.
static func install_argv(folder: String, log: String, system: String) -> Array:
	if system == "Windows":
		var ps1 := folder.path_join("install.ps1").replace("/", "\\")
		return shell_argv(PackedStringArray(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps1]), log, system)
	return shell_argv(PackedStringArray(["sh", folder.path_join("install.sh")]), log, system)


# --- logs --------------------------------------------------------------------------------------------

static func logs_folder() -> String:
	return Prefs.file(LOGS)


## <prefix>-<yyyymmdd-hhmmss>.log for the time given (Time.get_datetime_dict_from_system()).
static func log_name(prefix: String, t: Dictionary) -> String:
	return "%s-%04d%02d%02d-%02d%02d%02d.log" % [prefix, t.year, t.month, t.day, t.hour, t.minute, t.second]


## Remove all but the keep newest <prefix>-*.log in folder (by name: the time is in it).
static func prune_logs(folder: String, prefix: String, keep: int) -> void:
	var mine := []
	for f in DirAccess.get_files_at(folder):
		if f.begins_with(prefix + "-") and f.ends_with(".log"):
			mine.append(f)
	mine.sort()
	for i in range(maxi(0, mine.size() - keep)):
		DirAccess.remove_absolute(folder.path_join(mine[i]))


func _new_log(prefix: String) -> String:
	var folder := logs_folder()
	DirAccess.make_dir_recursive_absolute(folder)
	prune_logs(folder, prefix, KEEP_LOGS - 1)
	return folder.path_join(log_name(prefix, Time.get_datetime_dict_from_system()))


# --- brain.json --------------------------------------------------------------------------------------

## Whether brain.json (said) names the agent service started: its pid is the process started (sh exec's), or (Windows:
## the shell and uv's launcher stand between) it was announced since the start, by another process than the one named
## before it.
static func announced_by(said: Dictionary, started_pid: int, started_unix: float, stale_pid := 0) -> bool:
	var p = said.get("pid")
	if not Prefs.local_url(said.get("url")) or typeof(p) not in [TYPE_INT, TYPE_FLOAT] or int(p) <= 0:
		return false
	if int(p) == started_pid:
		return true
	var when = said.get("started")
	return typeof(when) in [TYPE_INT, TYPE_FLOAT] and float(when) >= started_unix - 1.0 and int(p) != stale_pid


# --- doing it ----------------------------------------------------------------------------------------

func _to(new_state: String, words := "") -> void:
	state = new_state
	message = words
	changed.emit()


func busy() -> bool:
	return state in ["starting", "installing"]


## Make sure an agent service runs: one announced already (ready at once), else start the one found, else
## not_installed. Returns the state.
func ensure() -> String:
	if busy():
		return state
	var announced := Prefs.announced_url(Prefs.OWN_BRAIN_FILE)
	if announced != "" and not _replace_announced():
		url = announced
		if state != "ready":
			_to("ready")
		return state
	var plan := _plan()
	var command: PackedStringArray = plan.command
	if plan.install and not _update_tried:
		_update_tried = true
		print("[Autopilot] Installing this plugin’s agent service" if command.is_empty()
			else "[Autopilot] The agent service installed is older than this plugin’s: installing this one")
		_updating = true
		install()
		if state == "installing":
			return state
		_updating = false  # (the installer didn't start: the installed one, then)
		if command.is_empty():
			return state  # (install_failed: there's nothing else to start)
	if command.is_empty():
		url = ""
		_to("not_installed")
		return state
	start(command)
	return state


## Without starting anything: ready when brain.json names one running, else back to idle (Start the agent service
## automatically off). Returns the state.
func look() -> String:
	if busy():
		return state
	var announced := Prefs.announced_url(Prefs.OWN_BRAIN_FILE)
	url = announced
	if announced != "" and state != "ready":
		_to("ready")
	elif announced == "" and state != "idle":
		_to("idle")
	return state


## Forget a failure (Try again).
func reset() -> void:
	if not busy() and state != "idle":
		_to("idle")


## Start the agent service with command (its words before "serve").
func start(command: PackedStringArray) -> void:
	log_path = _new_log("agent-service")
	var said := Prefs.read_json(Prefs.file(Prefs.OWN_BRAIN_FILE))
	_stale_pid = int(said.get("pid", 0)) if typeof(said.get("pid")) in [TYPE_INT, TYPE_FLOAT] else 0
	_launched_unix = Time.get_unix_time_from_system()
	var pa := start_argv(command, editor_pid, log_path, os_name)
	pid = launcher.call(pa[0], pa[1])
	url = ""
	_poll_at = 0.0
	if pid <= 0:
		_to("failed", "Couldn’t start the agent service (%s)." % " ".join(command))
	else:
		_to("starting")


## Run the installer the addon carries (not_installed, with a message, when it carries none).
func install() -> void:
	if busy():
		return
	if not has_installer():
		_to("not_installed")
		return
	log_path = _new_log("install")
	var pa := install_argv(installer_folder(), log_path, os_name)
	pid = launcher.call(pa[0], pa[1])
	_launched_unix = Time.get_unix_time_from_system()
	if pid <= 0:
		_to("install_failed", "Couldn’t install the agent service (see %s)" % log_path)
	else:
		_to("installing")


## Follow a start or an install (call it often: it looks every POLL_EVERY seconds).
func poll() -> void:
	if not busy():
		return
	var now := Time.get_ticks_msec() / 1000.0
	if now < _poll_at:
		return
	_poll_at = now + POLL_EVERY
	if state == "installing":
		if running.call(pid):
			return
		var code: int = exit_code.call(pid)
		var updating := _updating
		_updating = false
		if code == 0 and not find().is_empty():
			start(find())
		elif updating and not find().is_empty():
			# (an update that failed leaves the installed agent service: it runs, never nothing)
			print("[Autopilot] Couldn’t update the agent service (see %s): starting the installed one" % log_path)
			start(find())
		else:
			_to("install_failed", "Couldn’t install the agent service (see %s)" % log_path)
		return
	var said := Prefs.read_json(Prefs.file(Prefs.OWN_BRAIN_FILE))
	if announced_by(said, pid, _launched_unix, _stale_pid):
		url = str(said.url)
		_to("ready")
	elif not running.call(pid):
		_to("stopped", "The agent service stopped (see %s)" % log_path)
	elif Time.get_unix_time_from_system() - _launched_unix > START_TIMEOUT:
		_to("failed", "The agent service didn’t start within %d s (see %s)" % [int(START_TIMEOUT), log_path])


# --- removing Autopilot from this computer -------------------------------------------------------------

## Remove Autopilot from this computer (keep_memory: what it learned about each game kept): `autopilot uninstall` run
## with the command find() gives, else (not installed) the settings folder emptied here (remove_here()).
func uninstall(keep_memory: bool) -> void:
	removal_keep = keep_memory
	removal_message = ""
	removal_log = temp_folder(os_name).path_join(log_name(REMOVE_LOG, Time.get_datetime_dict_from_system()))
	url = ""
	state = "idle"  # (the agent service is stopped by it)
	var command := find()
	if command.is_empty():
		remove_here()
		changed.emit()
		return
	var pa := uninstall_argv(command, keep_memory, removal_log, os_name, temp_folder(os_name))
	_removal_pid = launcher.call(pa[0], pa[1])
	_removal_at = Time.get_ticks_msec() / 1000.0
	if _removal_pid <= 0:
		removal = "failed"
		removal_message = "Couldn’t start %s." % " ".join(command)
	else:
		removal = "running"
	changed.emit()


## Follow the removal running (call it often).
func poll_removal() -> void:
	if removal != "running":
		return
	if running.call(_removal_pid):
		if Time.get_ticks_msec() / 1000.0 - _removal_at > REMOVE_TIMEOUT:
			stopper.call(_removal_pid)
			removal = "failed"
			removal_message = "It didn’t finish within %d minutes." % int(REMOVE_TIMEOUT / 60.0)
			changed.emit()
		return
	var code: int = exit_code.call(_removal_pid)
	removal = "done" if code == 0 else "kept" if code == UNINSTALL_KEPT else "failed"
	changed.emit()


## Back to the usual (Set up again, or Cancel after a failure).
func end_removal() -> void:
	removal = ""
	removal_message = ""
	changed.emit()


## Without the agent service: the settings folder emptied but for aura-brain.json (and memory/ when keeping it was
## asked), what was done written to the log; kept when a file was in use.
func remove_here() -> void:
	var done := remove_settings(Prefs.settings_base(), [MEMORY, Prefs.AURA_BRAIN_FILE] if removal_keep else [Prefs.AURA_BRAIN_FILE])
	var lines := ["The agent service isn’t installed on this computer: Autopilot’s settings are removed here."]
	for p in done.removed:
		lines.append("Removed " + p)
	for p in done.failed:
		lines.append("Couldn’t remove %s (in use)" % p)
	for p in done.kept.filter(func(p: String) -> bool: return p.get_file() == MEMORY):
		lines.append("Kept what Autopilot learned about your games in " + p)
	var f := FileAccess.open(removal_log, FileAccess.WRITE)
	if f != null:
		f.store_string("\n".join(lines) + "\n")
		f.close()
	removal = "kept" if not done.failed.is_empty() else "done"


## Autopilot's settings folder under base emptied but for the names in keep, and removed when nothing is left in it.
## Returns {"removed": [paths], "failed": [paths], "kept": [paths]}.
static func remove_settings(base: String, keep: Array) -> Dictionary:
	var out := {"removed": [], "failed": [], "kept": []}
	var folder := base.path_join(Prefs.FOLDER)
	var names := _names(folder)
	if names.is_empty() and not DirAccess.dir_exists_absolute(folder):
		return out
	for name in names:
		var path: String = folder.path_join(name)
		if name in keep:
			out.kept.append(path)
		elif remove_tree(path):
			out.removed.append(path)
		else:
			out.failed.append(path)
	if _names(folder).is_empty():
		DirAccess.remove_absolute(folder)
	return out


## The files and folders in folder, hidden ones too ([] when it isn't there).
static func _names(folder: String) -> Array:
	var d := DirAccess.open(folder)
	if d == null:
		return []
	d.include_hidden = true
	return Array(d.get_files()) + Array(d.get_directories())


## Remove a file or a folder and all in it (a link: the link only); whether it is gone.
static func remove_tree(path: String) -> bool:
	var parent := DirAccess.open(path.get_base_dir())
	var link := parent != null and parent.is_link(path.get_file())
	var d := DirAccess.open(path) if not link else null
	if d != null:
		d.include_hidden = true
		for f in d.get_files():
			FileAccess.set_read_only_attribute(path.path_join(f), false)
			DirAccess.remove_absolute(path.path_join(f))
		for sub in d.get_directories():
			remove_tree(path.path_join(sub))
	elif FileAccess.file_exists(path):
		FileAccess.set_read_only_attribute(path, false)
	DirAccess.remove_absolute(path)
	return not FileAccess.file_exists(path) and not DirAccess.dir_exists_absolute(path)
