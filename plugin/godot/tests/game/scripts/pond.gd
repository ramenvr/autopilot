extends Area3D


func _ready() -> void:
	body_entered.connect(func(body: Node):
		if body.has_method("respawn"):
			body.respawn())
