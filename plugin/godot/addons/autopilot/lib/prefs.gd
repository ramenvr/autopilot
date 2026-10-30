## The user's Autopilot choices, shared by every engine and project on the machine (docs/protocol.md "Sharing a run's
## data"), in Autopilot/ under the user settings folder, as autopilot/bridge.py brain_file and the Unreal and Unity plugins
## name it: %LOCALAPPDATA% on Windows, ~/Library/Application Support on macOS, $XDG_CONFIG_HOME or ~/.config elsewhere.
##  - preferences.json: {"version": 1, "onboarded": bool, "share_data": bool, "access": "aura" | "own_keys"}; a missing
##    or unreadable file is the defaults (not onboarded, sharing on);
##  - autopilot.env: KEY=value lines (the BYOK keys the first-run setup takes, its Coding Agent and OpenCode's LLM
##    provider), merged: other lines and keys are kept.
## Also what a running agent service announces there (brain.json, aura-brain.json: {"url", "pid"}) and the Aura app
## (Use with Aura), as the Unity plugin's Aura.cs and the Unreal plugin's PlaytestAura.cpp read them.
## Plain static functions (no editor API): the game uses them too.
@tool
extends RefCounted

const FOLDER := "Autopilot"
const PREFERENCES := "preferences.json"
const ENV_FILE := "autopilot.env"
const OWN_BRAIN_FILE := "brain.json"
const AURA_BRAIN_FILE := "aura-brain.json"
const AURA_DOWNLOAD_URL := "https://www.tryaura.dev/"
const KEY_NAMES := ["TYPESAFE_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENCODE_API_KEY", "OPENROUTER_API_KEY",
	"DEEPSEEK_API_KEY", "GEMINI_API_KEY"]
## The BYOK Coding Agent (docs/protocol.md "Account"): autopilot.env's AUTOPILOT_AGENT, each one's name, and the LLM provider
## Claude Code and Codex are held to (OpenCode takes any of LLM_PROVIDERS).
const AGENT_KEY := "AUTOPILOT_AGENT"
const AGENTS := ["claude", "codex", "opencode"]  # (the setup's dropdown, in its order)
const DEFAULT_AGENT := "opencode"
const AGENT_NAMES := {"claude": "Claude Code", "codex": "Codex", "opencode": "OpenCode"}
const AGENT_PROVIDERS := {"claude": "anthropic", "codex": "openai"}
## The BYOK LLM provider (docs/protocol.md "Account"): autopilot.env's AUTOPILOT_OPENCODE_PROVIDER (OpenCode's), each one's
## key and name.
const PROVIDER_KEY := "AUTOPILOT_OPENCODE_PROVIDER"
const LLM_PROVIDERS := ["anthropic", "openai", "opencode", "openrouter", "deepseek", "google"]  # (the dropdown's order)
const LLM_KEY_NAMES := {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY", "opencode": "OPENCODE_API_KEY",
	"openrouter": "OPENROUTER_API_KEY", "deepseek": "DEEPSEEK_API_KEY", "google": "GEMINI_API_KEY"}
const LLM_NAMES := {"anthropic": "Anthropic", "openai": "OpenAI", "opencode": "OpenCode Zen",
	"openrouter": "OpenRouter", "deepseek": "DeepSeek", "google": "Gemini"}


static func _home() -> String:
	var h := OS.get_environment("USERPROFILE") if OS.get_name() == "Windows" else ""
	if h == "":
		h = OS.get_environment("HOME")
	return h


## The user settings folder (without Autopilot/).
static func settings_base() -> String:
	match OS.get_name():
		"Windows":
			var local := OS.get_environment("LOCALAPPDATA")
			return local if local != "" else _home().path_join("AppData").path_join("Local")
		"macOS":
			return _home().path_join("Library").path_join("Application Support")
	var xdg := OS.get_environment("XDG_CONFIG_HOME")
	return xdg if xdg != "" else _home().path_join(".config")


## Autopilot/<name> in the user settings folder.
static func file(name: String) -> String:
	return settings_base().path_join(FOLDER).path_join(name)


## JSON text parsed, or null when it isn't JSON (quietly: JSON.parse_string logs an error).
static func parse_json(text: String):
	var j := JSON.new()
	return j.data if j.parse(text) == OK else null


## A JSON object from a file; {} when the file is missing, unreadable or not an object.
static func read_json(path: String) -> Dictionary:
	if not FileAccess.file_exists(path):
		return {}
	var parsed = parse_json(FileAccess.get_file_as_string(path))
	return parsed if typeof(parsed) == TYPE_DICTIONARY else {}


## Write text to path atomically (a temporary file beside it, then renamed over it), making the folder when needed.
## private: readable by the user only (where the system has Unix permissions). OK or an error.
static func write_atomic(path: String, text: String, private := false) -> Error:
	var dir := path.get_base_dir()
	if not DirAccess.dir_exists_absolute(dir):
		var made := DirAccess.make_dir_recursive_absolute(dir)
		if made != OK:
			return made
	var tmp := "%s.%d.tmp" % [path, OS.get_process_id()]
	var f := FileAccess.open(tmp, FileAccess.WRITE)
	if f == null:
		return FileAccess.get_open_error()
	f.store_string(text)
	f.close()
	if private and OS.get_name() != "Windows":
		FileAccess.set_unix_permissions(tmp, FileAccess.UNIX_READ_OWNER | FileAccess.UNIX_WRITE_OWNER)
	var err := DirAccess.rename_absolute(tmp, path)
	if err != OK:
		DirAccess.remove_absolute(tmp)
	return err


# --- preferences.json ---------------------------------------------------------------------------------

## preferences.json as written ({} when missing or unreadable).
static func load_preferences(path := "") -> Dictionary:
	return read_json(path if path != "" else file(PREFERENCES))


## Whether the user finished (or skipped) the first-run setup.
static func onboarded(prefs: Dictionary) -> bool:
	return typeof(prefs.get("onboarded")) == TYPE_BOOL and prefs.onboarded


## Whether the user shares runs' data (absent or not a boolean: yes).
static func share_data(prefs: Dictionary) -> bool:
	return prefs.share_data if typeof(prefs.get("share_data")) == TYPE_BOOL else true


## The prefs with changes merged in (keys not in changes are kept; a null value removes its key), as "version": 1.
static func merged(prefs: Dictionary, changes: Dictionary) -> Dictionary:
	var out := prefs.duplicate(true)
	for k in changes:
		if changes[k] == null:
			out.erase(k)
		else:
			out[k] = changes[k]
	out.version = 1
	return out


## Merge changes into preferences.json (read again just before, so other plugins' keys are kept). OK or an error.
static func save_preferences(changes: Dictionary, path := "") -> Error:
	var p := path if path != "" else file(PREFERENCES)
	return write_atomic(p, JSON.stringify(merged(read_json(p), changes), "  ") + "\n")


## Whether this run shares its data: preferences.json's share_data, unless the command line says (-PlaytestShare=0|1,
## given: "" when it doesn't).
static func share_for_run(given: String, path := "") -> bool:
	var g := given.strip_edges().to_lower()
	if g in ["0", "false", "no", "off"]:
		return false
	if g in ["1", "true", "yes", "on"]:
		return true
	return share_data(load_preferences(path))


# --- autopilot.env ----------------------------------------------------------------------------------------

## The text of an env file with keys set (KEY -> value; empty values are left out): a line setting one of them
## (KEY=..., also "export KEY=...") is replaced, other lines are kept as they are, and keys not there yet are added at
## the end.
static func merge_env(text: String, keys: Dictionary) -> String:
	var want := {}
	for k in keys:
		if str(keys[k]).strip_edges() != "":
			want[str(k)] = str(keys[k]).strip_edges()
	var lines := Array(text.replace("\r\n", "\n").split("\n"))
	if not lines.is_empty() and lines[-1] == "":
		lines.pop_back()
	var done := {}
	var out := []
	for line in lines:
		var s: String = str(line).strip_edges()
		if s.begins_with("export "):
			s = s.substr(7).strip_edges()
		var name := s.get_slice("=", 0).strip_edges() if "=" in s else ""
		if name != "" and want.has(name) and not s.begins_with("#"):
			if done.has(name):
				continue  # (a key set twice: the one line kept)
			out.append("%s=%s" % [name, want[name]])
			done[name] = true
		else:
			out.append(line)
	for k in want:
		if not done.has(k):
			out.append("%s=%s" % [k, want[k]])
	return "\n".join(out) + "\n" if not out.is_empty() else ""


## Write the non-empty keys to autopilot.env, keeping what else is there. OK or an error (never logs the keys).
static func save_env_keys(keys: Dictionary, path := "") -> Error:
	if keys.values().all(func(v): return str(v).strip_edges() == ""):
		return OK  # (nothing to write: the file stays as it is)
	var p := path if path != "" else file(ENV_FILE)
	var text := FileAccess.get_file_as_string(p) if FileAccess.file_exists(p) else ""
	var merged_text := merge_env(text, keys)
	if merged_text == text:
		return OK
	return write_atomic(p, merged_text, true)


## The value an env file's text sets for name (its last line setting it, "export " allowed; "" when none does).
static func env_value(text: String, name: String) -> String:
	var found := ""
	for line in text.replace("\r\n", "\n").split("\n"):
		var s := line.strip_edges()
		if s.begins_with("export "):
			s = s.substr(7).strip_edges()
		if "=" in s and not s.begins_with("#") and s.get_slice("=", 0).strip_edges() == name:
			found = s.substr(s.find("=") + 1).strip_edges()
	return found


## Aura's account creation page (<server>/auth/signup) on the Aura server the sign-in goes to: autopilot.env's
## AUTOPILOT_AURA_SERVER, else the one the download was built for (settings.env in the installer's folder,
## installer_dir), else Aura's own (https://app.tryaura.dev).
static func sign_up_url(installer_dir: String, env_path := "") -> String:
	var server := ""
	for p in [env_path if env_path != "" else file(ENV_FILE), installer_dir.path_join("settings.env") if installer_dir != "" else ""]:
		if server == "" and p != "" and FileAccess.file_exists(p):
			server = env_value(FileAccess.get_file_as_string(p), "AUTOPILOT_AURA_SERVER")
	server = server.strip_edges().rstrip("/")
	return (server if server != "" else "https://app.tryaura.dev") + "/auth/signup"


## One of LLM_PROVIDERS for v (the dropdown's pick): anthropic for anything else.
static func llm_provider(v) -> String:
	var p := str(v).strip_edges().to_lower() if typeof(v) == TYPE_STRING else ""
	return p if p in LLM_PROVIDERS else LLM_PROVIDERS[0]


## A provider as shown ("Anthropic", "OpenAI", "OpenCode Zen", ...; another id as it is; none: Anthropic, the agent
## service's default).
static func llm_name(v) -> String:
	var p := str(v).strip_edges() if typeof(v) == TYPE_STRING else ""
	if p == "":
		return LLM_NAMES.anthropic
	return LLM_NAMES.get(p.to_lower(), p)


## The LLM provider autopilot.env names (AUTOPILOT_OPENCODE_PROVIDER; anthropic when it names none or another).
static func saved_llm_provider(path := "") -> String:
	var p := path if path != "" else file(ENV_FILE)
	return llm_provider(env_value(FileAccess.get_file_as_string(p), PROVIDER_KEY) if FileAccess.file_exists(p) else "")


## One of AGENTS for v (the dropdown's pick): opencode for anything else.
static func coding_agent(v) -> String:
	var a := str(v).strip_edges().to_lower() if typeof(v) == TYPE_STRING else ""
	return a if a in AGENTS else DEFAULT_AGENT


## A Coding Agent as shown ("Claude Code", "Codex", "OpenCode"; another id as it is; none: OpenCode, the agent
## service's default).
static func agent_name(v) -> String:
	var a := str(v).strip_edges() if typeof(v) == TYPE_STRING else ""
	if a == "":
		return AGENT_NAMES[DEFAULT_AGENT]
	return AGENT_NAMES.get(a.to_lower(), a)


## The LLM provider a Coding Agent uses: Anthropic for Claude Code, OpenAI for Codex, opencode_provider (one of
## LLM_PROVIDERS, anthropic for anything else) for OpenCode.
static func agent_provider(agent, opencode_provider) -> String:
	return AGENT_PROVIDERS.get(coding_agent(agent), llm_provider(opencode_provider))


## The Coding Agent autopilot.env names (AUTOPILOT_AGENT; opencode when it names none or another).
static func saved_coding_agent(path := "") -> String:
	var p := path if path != "" else file(ENV_FILE)
	return coding_agent(env_value(FileAccess.get_file_as_string(p), AGENT_KEY) if FileAccess.file_exists(p) else "")


## Write the BYOK choice to autopilot.env: always the Coding Agent, OpenCode's LLM provider when OpenCode is the agent (for
## Claude Code and Codex that line is kept), the LLM key under the name of the provider the agent uses and the decision
## key when typed (an empty one keeps its line; other providers' key lines are kept). OK or an error (never logs the
## keys).
static func save_byok(decision_key: String, agent: String, provider: String, llm_key: String, path := "") -> Error:
	var a := coding_agent(agent)
	var p := agent_provider(a, provider)
	var keys := {"TYPESAFE_API_KEY": decision_key, AGENT_KEY: a}
	if a == "opencode":
		keys[PROVIDER_KEY] = p
	keys[LLM_KEY_NAMES[p]] = llm_key
	return save_env_keys(keys, path)


# --- agent services announced on this machine, and Aura ---------------------------------------------------

static var _alive_cache := {}  # pid -> [alive, when (s)]: the answers of the system's process list, for a few seconds
const ALIVE_FOR := 3.0


## Whether the process pid is running. OS.is_process_running only knows the processes this one started (and itself),
## so for others the system is asked: tasklist on Windows, kill -0 elsewhere (the answer kept for ALIVE_FOR seconds).
static func pid_alive(pid: int) -> bool:
	if pid <= 0:
		return false
	if pid == OS.get_process_id() or OS.is_process_running(pid):
		return true
	var now := Time.get_ticks_msec() / 1000.0
	var kept = _alive_cache.get(pid)
	if kept != null and now - float(kept[1]) < ALIVE_FOR:
		return kept[0]
	var out := []
	var alive := false
	if OS.get_name() == "Windows":
		if OS.execute("tasklist", ["/FI", "PID eq %d" % pid, "/NH", "/FO", "CSV"], out) == 0 and not out.is_empty():
			alive = (",\"%d\"," % pid) in str(out[0])
	else:
		alive = OS.execute("/bin/sh", ["-c", "kill -0 %d 2>/dev/null" % pid], out) == 0
	_alive_cache[pid] = [alive, now]
	return alive


## Whether url is an agent service address on this machine (ws://127.0.0.1:<port>..., ws://localhost:<port>...).
static func local_url(url) -> bool:
	return typeof(url) == TYPE_STRING and (url.begins_with("ws://127.0.0.1:") or url.begins_with("ws://localhost:"))


## The address a running agent service wrote to Autopilot/<name>: "" when none did, the one that did has ended, or
## it names anything but this machine.
static func announced_url(name: String) -> String:
	var said := read_json(file(name))
	var url = said.get("url")
	var pid = said.get("pid")
	if not local_url(url) or typeof(pid) not in [TYPE_INT, TYPE_FLOAT] or int(pid) <= 0:
		return ""
	return url if pid_alive(int(pid)) else ""


## The agent service the Aura app runs ("" when it isn't running).
static func aura_service_url() -> String:
	return announced_url(AURA_BRAIN_FILE)


## The Aura app, where its installer puts it ("" when it isn't there).
static func aura_app_path() -> String:
	if OS.get_name() == "Windows":
		var exe := settings_base().path_join("Programs").path_join("aura-client").path_join("Aura.exe")
		return exe if FileAccess.file_exists(exe) else ""
	if OS.get_name() == "macOS":
		for app in ["/Applications/Aura.app", _home().path_join("Applications").path_join("Aura.app")]:
			if DirAccess.dir_exists_absolute(app):
				return app
	return ""


static func aura_installed() -> bool:
	return aura_app_path() != ""


## Start the Aura app (it starts its agent service); whether it was started.
static func launch_aura() -> bool:
	var app := aura_app_path()
	if app == "":
		return false
	if app.ends_with(".app"):
		return OS.create_process("/usr/bin/open", [app]) > 0
	return OS.create_process(app, []) > 0
