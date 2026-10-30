extends StaticBody3D

@export var gate_path := NodePath("../Gate")


func _ready() -> void:
	add_to_group("interactable")


func use(_by: Node) -> void:
	var gate := get_node_or_null(gate_path)
	if gate:
		gate.open()
