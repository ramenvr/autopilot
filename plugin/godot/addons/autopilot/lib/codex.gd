## The game's code for a brain that can't read this machine's disk: `codex_manifest` and `codex_read`.
##
## Read only, the project's own code and data as text, from res:// (the project's folder when run from the editor or a
## project checkout; an exported build's pack holds what its export kept): project.godot, export_presets.cfg (top level
## only), and every .gd .cs .tscn .tres .gdshader file, skipping directories whose name starts with "." (.godot, .git,
## .import...), node_modules, addons/autopilot and any directory holding a .gdignore. Plus the addon's summary of the
## project (main scene, autoloads, input actions, global classes, each scene's nodes), served as
## ".godot/autopilot_codex/summary.txt" (written there, or to user://autopilot_codex/ when res:// is read-only).
## Same limits as Unreal's: 20000 files and 200 MB in a manifest; reads up to max_bytes (default 1 MB, at most 4 MB).
extends RefCounted

const MAX_FILES := 20000
const MAX_TOTAL := 200 * 1024 * 1024
const DEFAULT_READ := 1000000
const MAX_READ := 4 * 1024 * 1024
const EXTENSIONS := ["gd", "cs", "tscn", "tres", "gdshader"]
const TOP_FILES := ["project.godot", "export_presets.cfg"]
const SUMMARY := ".godot/autopilot_codex/summary.txt"
const Keys = preload("keys.gd")


static func skipped_dir(name: String) -> bool:
	return name.begins_with(".") or name == "node_modules"


## The res:// path a relative path stands for, or "" when it isn't one the manifest could list.
static func resolve(asked: String, summary_file: String) -> String:
	var p := asked.replace("\\", "/")
	if p == "" or p.begins_with("/") or ":" in p:
		return ""
	if p == SUMMARY:
		return summary_file
	var parts := p.split("/", true)
	for part in parts:
		if part == "" or part == "." or part == ".." or skipped_dir(part):
			return ""
	if parts.size() >= 2 and parts[0] == "addons" and parts[1] == "autopilot":
		return ""
	if parts.size() == 1 and p in TOP_FILES:
		return "res://" + p
	if not (p.get_extension().to_lower() in EXTENSIONS):
		return ""
	# no directory on the way may hold a .gdignore
	var dir := "res://"
	for i in parts.size() - 1:
		dir += parts[i] + "/"
		if FileAccess.file_exists(dir + ".gdignore"):
			return ""
	return "res://" + p


static func _walk(dir: String, rel: String, out: Array) -> void:
	var d := DirAccess.open(dir)
	if d == null:
		return
	d.include_hidden = false
	var files := d.get_files()
	if files.has(".gdignore"):
		return
	for f in files:
		if f.get_extension().to_lower() in EXTENSIONS:
			out.append(rel + f)
	for sub in d.get_directories():
		if skipped_dir(sub):
			continue
		if rel == "addons/" and sub == "autopilot":
			continue
		_walk(dir.path_join(sub), rel + sub + "/", out)


## The manifest (run off the game thread). summary_file: where the summary was written ("" when it couldn't be).
static func manifest(project_name: String, summary_file: String, wrote := true) -> Dictionary:
	var started := Time.get_ticks_msec()
	var paths := []
	for f in TOP_FILES:
		if FileAccess.file_exists("res://" + f):
			paths.append(f)
	var rest := []
	_walk("res://", "", rest)
	rest.sort()
	paths.append_array(rest)
	var files := []
	var total := 0
	var truncated := false
	for p in paths:
		if files.size() >= MAX_FILES:
			truncated = true
			break
		var full: String = "res://" + p
		var size := _size(full)
		if total + size > MAX_TOTAL:
			truncated = true
			continue
		total += size
		files.append({"path": p, "size": size, "hash": FileAccess.get_md5(full)})
	if summary_file != "" and FileAccess.file_exists(summary_file):
		files.append({"path": SUMMARY, "size": _size(summary_file), "hash": FileAccess.get_md5(summary_file)})
	return {"project": project_name, "files": files, "truncated": truncated,
		"export": _export_info(summary_file, wrote, (Time.get_ticks_msec() - started) / 1000.0)}


static func _size(path: String) -> int:
	var f := FileAccess.open(path, FileAccess.READ)
	if f == null:
		return 0
	return f.get_length()


static func read(paths: Array, max_bytes: int, summary_file: String) -> Dictionary:
	max_bytes = clampi(max_bytes, 1, MAX_READ)
	var out := {}
	var missing := []
	var too_big := []
	var used := 0
	for x in paths:
		var asked := str(x)
		var full := resolve(asked, summary_file)
		if full == "" or not FileAccess.file_exists(full):
			missing.append(asked)
			continue
		var size := _size(full)
		if size > max_bytes:
			too_big.append(asked)
			continue
		if used + size > max_bytes:
			break  # this and the rest go unanswered: the brain asks again
		var f := FileAccess.open(full, FileAccess.READ)
		if f == null:
			missing.append(asked)
			continue
		out[asked] = f.get_as_text()
		used += size
	return {"files": out, "missing": missing, "too_big": too_big}


# --- the summary ------------------------------------------------------------------------------------

## Write the project summary; returns where ("" when nowhere was writable).
static func write_summary() -> String:
	var text := summary_text()
	for dir in ["res://.godot/autopilot_codex", "user://autopilot_codex"]:
		if DirAccess.make_dir_recursive_absolute(dir) != OK and not DirAccess.dir_exists_absolute(dir):
			continue
		var path: String = dir + "/summary.txt"
		var f := FileAccess.open(path, FileAccess.WRITE)
		if f == null:
			continue
		f.store_string(text)
		f.close()
		return path
	return ""


static func summary_text() -> String:
	var lines := PackedStringArray()
	lines.append("Godot project: %s (Godot %s)" % [ProjectSettings.get_setting("application/config/name", ""), Engine.get_version_info().string])
	lines.append("Main scene: %s" % ProjectSettings.get_setting("application/run/main_scene", ""))
	lines.append("")
	lines.append("Autoloads (nodes under /root, created before the main scene, in this order):")
	for p in ProjectSettings.get_property_list():
		var n: String = p.name
		if n.begins_with("autoload/"):
			var name := n.substr(9)
			if name == "AutopilotPlaytest":
				continue
			lines.append("  /root/%s = %s" % [name, str(ProjectSettings.get_setting(n)).trim_prefix("*")])
	lines.append("")
	lines.append("Input actions (InputMap; project.godot [input]):")
	for p in ProjectSettings.get_property_list():
		var n: String = p.name
		if n.begins_with("input/") and not Keys.is_default_ui(n.substr(6)):
			var v = ProjectSettings.get_setting(n)
			var keys := []
			if typeof(v) == TYPE_DICTIONARY:
				for ev in v.get("events", []):
					if ev is InputEvent:
						keys.append(ev.as_text())
			lines.append("  %s: %s" % [n.substr(6), ", ".join(keys)])
	lines.append("")
	lines.append("Global script classes (class_name):")
	for c in ProjectSettings.get_global_class_list():
		lines.append("  %s extends %s: %s" % [c.get("class", ""), c.get("base", ""), c.get("path", "")])
	lines.append("")
	lines.append("Physics: 3D gravity %s m/s^2, 2D gravity %s px/s^2, %s ticks/s" % [ProjectSettings.get_setting("physics/3d/default_gravity", 9.8), ProjectSettings.get_setting("physics/2d/default_gravity", 980), ProjectSettings.get_setting("physics/common/physics_ticks_per_second", 60)])
	lines.append("")
	lines.append("Scenes (node tree: name, type, script, instanced scene, groups):")
	var scenes := []
	_walk("res://", "", scenes)
	for s in scenes:
		if s.get_extension() != "tscn":
			continue
		lines.append("  res://%s" % s)
		lines.append_array(_scene_outline("res://" + s))
	return "\n".join(lines) + "\n"


## A .tscn file's node tree from its text (no loading).
static func _scene_outline(path: String) -> PackedStringArray:
	var out := PackedStringArray()
	var f := FileAccess.open(path, FileAccess.READ)
	if f == null:
		return out
	var ext := {}  # id -> path
	var re_ext := RegEx.create_from_string('^\\[ext_resource [^\\]]*path="([^"]+)"[^\\]]*id="([^"]+)"')
	var re_ext2 := RegEx.create_from_string('^\\[ext_resource [^\\]]*id="([^"]+)"[^\\]]*path="([^"]+)"')
	var re_node := RegEx.create_from_string('^\\[node name="([^"]+)"(.*)\\]')
	var re_attr := RegEx.create_from_string('(\\w+)=("[^"]*"|ExtResource\\("[^"]+"\\)|\\[[^\\]]*\\])')
	var current := -1
	var count := 0
	while not f.eof_reached():
		var line := f.get_line()
		var m := re_ext.search(line)
		if m:
			ext[m.get_string(2)] = m.get_string(1)
			continue
		m = re_ext2.search(line)
		if m:
			ext[m.get_string(1)] = m.get_string(2)
			continue
		m = re_node.search(line)
		if m:
			count += 1
			if count > 400:
				out.append("    ... (more nodes)")
				break
			var attrs := {}
			for a in re_attr.search_all(m.get_string(2)):
				attrs[a.get_string(1)] = a.get_string(2).trim_prefix('"').trim_suffix('"')
			var name := m.get_string(1)
			var parent: String = attrs.get("parent", "")
			var where := name if parent == "" else (name if parent == "." else parent + "/" + name)
			var desc := "    %s" % where
			if attrs.has("type"):
				desc += " (%s)" % attrs.type
			if attrs.has("instance"):
				desc += " instance of %s" % ext.get(_ext_id(attrs.instance), attrs.instance)
			if attrs.has("groups"):
				desc += " groups %s" % attrs.groups
			out.append(desc)
			current = out.size() - 1
			continue
		if current >= 0 and line.begins_with("script = ExtResource("):
			out[current] = out[current] + " script " + ext.get(_ext_id(line.substr(9)), "")
	return out


static func _ext_id(s: String) -> String:
	var a := s.find('"')
	var b := s.find('"', a + 1)
	return s.substr(a + 1, b - a - 1) if a >= 0 and b > a else s


## The manifest's "export" (as Unreal's Blueprint export): here the summary, rewritten at each manifest (it is cheap)
## unless "export": "skip".
static func _export_info(summary_file: String, wrote: bool, seconds: float) -> Dictionary:
	var info := {"ran": wrote and summary_file != "", "seconds": snappedf(seconds, 0.001), "exit_code": null}
	if info.ran:
		info.reason = "refresh"
		info.exit_code = 0
	elif wrote:
		info.error = "the summary couldn’t be written (res:// and user:// read-only)"
	return info
