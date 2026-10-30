extends StaticBody3D

var letter = null  # planted bug (for the log_error detector): nothing ever puts a letter in


func _ready() -> void:
	add_to_group("interactable")


func use(_by: Node) -> void:
	print("The letter reads: " + letter.strip_edges())
