extends Node

const BASKET_SIZE := 10

var apples := 0
@export_range(0, 100) var stamina := 100
var health := 50
var max_health := 100
var gate_open := false
var sound := true
var brightness := 0.5
var pace := "Steady"
var nickname := ""
var bell_rings := 0
var swatch_clicks := 0
var picked := ""
var mouse_at := Vector2.ZERO


func _ready() -> void:
	if OS.get_environment("GODOT_TEST_ERRORS") != "":  # (tests/gate_test.py counts what the addon sends of these)
		var timer := Timer.new()
		timer.wait_time = 0.5
		timer.autostart = true
		timer.timeout.connect(func() -> void: push_error("planted error"))
		add_child(timer)


func add_apple() -> void:
	apples = mini(apples + 1, BASKET_SIZE)


func basket_full() -> bool:
	return apples >= BASKET_SIZE


func tire(amount: int) -> void:
	stamina -= amount


## Leave n nodes behind in the scene (a leak, for the actor_growth detector).
func leak(n: int) -> void:
	for i in n:
		var litter := Area3D.new()
		litter.name = "Litter%d" % i
		get_tree().current_scene.add_child(litter)
