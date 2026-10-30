extends Node

const BASKET_SIZE := 10

var apples := 0
var stamina := 100
var gate_open := false
var sound := true
var brightness := 0.5
var pace := "Steady"
var nickname := ""


func add_apple() -> void:
	apples = mini(apples + 1, BASKET_SIZE)


func basket_full() -> bool:
	return apples >= BASKET_SIZE


func tire(amount: int) -> void:
	stamina -= amount
