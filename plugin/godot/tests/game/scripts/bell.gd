## A bell hanging in the orchard: a click on it (the mouse's left button, picked in the world: _input_event) rings it.
extends StaticBody3D


func _input_event(_camera: Camera3D, event: InputEvent, _at: Vector3, _normal: Vector3, _shape: int) -> void:
	if event is InputEventMouseButton and event.button_index == MOUSE_BUTTON_LEFT and event.pressed:
		GameState.bell_rings += 1
