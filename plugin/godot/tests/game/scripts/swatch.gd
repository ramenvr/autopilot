## A colour swatch that takes the mouse itself (its own _gui_input, no Button): a click counts in GameState.
extends ColorRect


func _gui_input(event: InputEvent) -> void:
	if event is InputEventMouseButton and event.button_index == MOUSE_BUTTON_LEFT and event.pressed:
		GameState.swatch_clicks += 1
		accept_event()
