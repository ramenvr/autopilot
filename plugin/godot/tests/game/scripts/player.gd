extends CharacterBody3D

const SPEED := 5.0
const JUMP_VELOCITY := 4.5
const LOOK := 0.003
const REACH := 3.0

var gravity: float = ProjectSettings.get_setting("physics/3d/default_gravity")
var spawn := Vector3.ZERO
@onready var head: Node3D = $Head
var _want_jump := false


func _ready() -> void:
	spawn = global_position


func _unhandled_input(event: InputEvent) -> void:
	if event is InputEventMouseMotion:
		rotate_y(-event.relative.x * LOOK)
		head.rotate_x(-event.relative.y * LOOK)
		head.rotation.x = clampf(head.rotation.x, -1.5, 1.5)
	elif event.is_action_pressed("jump"):
		_want_jump = true
	elif event.is_action_pressed("interact"):
		interact()


func _physics_process(delta: float) -> void:
	if not is_on_floor():
		velocity.y -= gravity * delta
	elif _want_jump:
		velocity.y = JUMP_VELOCITY
	_want_jump = false
	var input := Input.get_vector("move_left", "move_right", "move_forward", "move_back")
	var dir := transform.basis * Vector3(input.x, 0.0, input.y)
	var speed := SPEED * (1.6 if Input.is_action_pressed("sprint") else 1.0)
	velocity.x = dir.x * speed
	velocity.z = dir.z * speed
	move_and_slide()
	if global_position.y < -20.0:
		respawn()


## Use the nearest thing in the "interactable" group within REACH (on the ground plane).
func interact() -> void:
	var best: Node3D = null
	var best_d := REACH
	for n in get_tree().get_nodes_in_group("interactable"):
		var d := Vector2(n.global_position.x - global_position.x, n.global_position.z - global_position.z).length()
		if d < best_d:
			best = n
			best_d = d
	if best:
		best.use(self)


func respawn() -> void:
	global_position = spawn
	velocity = Vector3.ZERO
