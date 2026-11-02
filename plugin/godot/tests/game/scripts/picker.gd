## Clicking in the world as many games do it: a ray from the camera through where the mouse is, on a left click; what
## it hits and where the viewport says the mouse is are kept in GameState (picked, mouse_at).
extends Node


func _unhandled_input(event: InputEvent) -> void:
	if not (event is InputEventMouseButton and event.button_index == MOUSE_BUTTON_LEFT and event.pressed):
		return
	var cam := get_viewport().get_camera_3d()
	if cam == null:
		return
	GameState.mouse_at = get_viewport().get_mouse_position()
	var from := cam.project_ray_origin(event.position)
	var q := PhysicsRayQueryParameters3D.create(from, from + cam.project_ray_normal(event.position) * 100.0)
	var hit := cam.get_world_3d().direct_space_state.intersect_ray(q)
	GameState.picked = str(hit.collider.name) if hit else ""
