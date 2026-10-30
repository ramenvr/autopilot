extends Control


func _ready() -> void:
	$Box/PlayButton.pressed.connect(func(): get_tree().change_scene_to_file("res://scenes/level.tscn"))
	$Box/SoundToggle.toggled.connect(func(on: bool): GameState.sound = on)
	$Box/Brightness.value_changed.connect(func(v: float): GameState.brightness = v)
	$Box/Pace.item_selected.connect(func(i: int): GameState.pace = $Box/Pace.get_item_text(i))
	$Box/Nickname.text_changed.connect(func(t: String): GameState.nickname = t)
