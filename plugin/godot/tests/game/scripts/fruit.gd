class_name Fruit
extends Area3D


func _ready() -> void:
	body_entered.connect(_on_body_entered)


func _process(delta: float) -> void:
	$Mesh.rotate_y(delta * 2.0)


func _on_body_entered(body: Node) -> void:
	if body.is_in_group("player"):
		GameState.add_apple()
		queue_free()
