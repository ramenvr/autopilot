extends CharacterBody2D

const SPEED := 300.0
const JUMP_VELOCITY := -400.0

var gravity: float = ProjectSettings.get_setting("physics/2d/default_gravity")
var _want_jump := false


func _unhandled_input(event: InputEvent) -> void:
	if event.is_action_pressed("jump"):
		_want_jump = true


func _physics_process(delta: float) -> void:
	if not is_on_floor():
		velocity.y += gravity * delta
	elif _want_jump:
		velocity.y = JUMP_VELOCITY
	_want_jump = false
	velocity.x = Input.get_axis("move_left", "move_right") * SPEED
	move_and_slide()
