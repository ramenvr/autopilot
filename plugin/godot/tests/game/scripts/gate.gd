extends StaticBody3D

# Planted bug (for the out_of_world detector): the lift has the wrong sign and is far too large, so the opened gate
# sinks through the ground and out of the world instead of rising 3 m
const LIFT := -60.0


func _ready() -> void:
	if GameState.gate_open:
		position.y += LIFT


func open() -> void:
	if GameState.gate_open:
		return
	GameState.gate_open = true
	create_tween().tween_property(self, "position:y", position.y + LIFT, 0.5)
