## The editor plugin (shown as Autopilot): registers the game-side autoload (playtest.gd), gives the project a stable id
## (autopilot/project_id in project.godot, a GUID: what the agent server keeps its memory of the game under), and
## adds the Autopilot dock (dock.gd, in an EditorDock: right side by default, movable anywhere) with the editor's link to
## the agent server (editor_bridge.gd), which reaches the running game over the debugger connection (debugger.gd), and a
## status badge in the editor's toolbar.
@tool
extends EditorPlugin

const AUTOLOAD := "AutopilotPlaytest"
const AUTOLOAD_PATH := "res://addons/autopilot/playtest.gd"
const Debugger = preload("debugger.gd")
const EditorBridge = preload("editor_bridge.gd")
const Dock = preload("dock.gd")
const HT = preload("ui/autopilot_theme.gd")

var debugger
var bridge
var dock
var holder: EditorDock
var badge: Control


func _enable_plugin() -> void:
	add_autoload_singleton(AUTOLOAD, AUTOLOAD_PATH)
	ensure_project_id()


func _disable_plugin() -> void:
	remove_autoload_singleton(AUTOLOAD)


func _enter_tree() -> void:
	ensure_project_id()
	debugger = Debugger.new()
	add_debugger_plugin(debugger)
	bridge = EditorBridge.new()
	bridge.name = "AutopilotPlaytestBridge"
	bridge.debugger = debugger
	add_child(bridge)
	HT.theme(EditorInterface.get_editor_scale())
	dock = Dock.new()
	dock.bridge = bridge
	holder = EditorDock.new()
	holder.name = "Autopilot"
	holder.title = "Autopilot"
	holder.layout_key = "Autopilot"
	holder.default_slot = EditorDock.DOCK_SLOT_RIGHT_UL
	holder.available_layouts = EditorDock.DOCK_LAYOUT_ALL
	var icon := HT.icon(16)
	if icon:
		holder.dock_icon = icon
	holder.add_child(dock)
	badge = dock.get_badge()
	add_dock(holder)
	if badge:
		badge.pressed.connect(func(): holder.make_visible())
		add_control_to_container(CONTAINER_TOOLBAR, badge)


func _exit_tree() -> void:
	if badge:
		remove_control_from_container(CONTAINER_TOOLBAR, badge)
		badge.queue_free()
		badge = null
	if holder:
		remove_dock(holder)
		holder.queue_free()
		holder = null
	if bridge:
		bridge.disconnect_from_brain()
		bridge.queue_free()
	if debugger:
		remove_debugger_plugin(debugger)
	HT.release()


static func new_guid() -> String:
	var b := Crypto.new().generate_random_bytes(16)
	b[6] = (b[6] & 0x0f) | 0x40  # version 4
	b[8] = (b[8] & 0x3f) | 0x80
	var h := b.hex_encode()
	return "%s-%s-%s-%s-%s" % [h.substr(0, 8), h.substr(8, 4), h.substr(12, 4), h.substr(16, 4), h.substr(20, 12)]


static func ensure_project_id() -> void:
	if str(ProjectSettings.get_setting("autopilot/project_id", "")) != "":
		return
	ProjectSettings.set_setting("autopilot/project_id", new_guid())
	ProjectSettings.save()
