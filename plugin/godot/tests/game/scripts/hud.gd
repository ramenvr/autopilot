extends CanvasLayer


func _process(_delta: float) -> void:
	$AppleLabel.text = "Basket %d / %d" % [GameState.apples, GameState.BASKET_SIZE]
	$GateLabel.visible = GameState.gate_open
