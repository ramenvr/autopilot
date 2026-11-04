## A game script that reads and sets the mouse mode every way a game can (the addon routes these, lib/mouse_reads.gd).
extends Node


func read_property() -> int:
	return Input.mouse_mode


func read_getter() -> int:
	return Input.get_mouse_mode()


func read_display() -> int:
	return DisplayServer.mouse_get_mode()


func set_property(mode: Input.MouseMode) -> void:
	Input.mouse_mode = mode


func set_call(mode: Input.MouseMode) -> void:
	Input.set_mouse_mode(mode)


func set_display(mode: DisplayServer.MouseMode) -> void:
	DisplayServer.mouse_set_mode(mode)


func display_captured() -> bool:
	return DisplayServer.mouse_get_mode() == DisplayServer.MOUSE_MODE_CAPTURED


func input_captured() -> bool:
	return Input.mouse_mode == Input.MOUSE_MODE_CAPTURED


func held_in_typed_var() -> int:
	var mode: Input.MouseMode = Input.mouse_mode
	return mode
