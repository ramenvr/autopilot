"""The Godot adapter (autopilot/engines/godot.py) without Godot: project.godot reading, the codex's index, the project's
name, the headless launch, the game backend's things, and the Godot section of the implementer's prompt."""

import os
import re

from autopilot import engines
from autopilot.engines import godot as godotmod
from autopilot.kit.game import Game
from autopilot.kit.geom import Vec

PROJECT_GODOT = """; Engine configuration file.

config_version=5

[application]

config/name="Sky Garden"
run/main_scene="res://scenes/title.tscn"

[autoload]

Score="*res://scripts/score.gd"
AutopilotPlaytest="*res://addons/autopilot/playtest.gd"

[autopilot]

project_id="0f8fad5b-d9cb-469f-a165-70867728950e"

[input]

walk_up={
"deadzone": 0.2,
"events": [Object(InputEventKey,"resource_local_to_scene":false,"device":-1,"keycode":0,"physical_keycode":87,"key_label":0,"unicode":119,"echo":false,"script":null)
, Object(InputEventKey,"resource_local_to_scene":false,"device":-1,"keycode":0,"physical_keycode":4194320,"key_label":0,"unicode":0,"echo":false,"script":null)
]
}
hop={
"deadzone": 0.2,
"events": [Object(InputEventKey,"resource_local_to_scene":false,"device":-1,"keycode":32,"physical_keycode":0,"key_label":0,"unicode":32,"echo":false,"script":null)
]
}
shoot={
"deadzone": 0.2,
"events": [Object(InputEventMouseButton,"resource_local_to_scene":false,"device":-1,"button_mask":0,"position":Vector2(0, 0),"button_index":1,"canceled":false,"pressed":false,"double_click":false,"script":null)
, Object(InputEventKey,"resource_local_to_scene":false,"device":-1,"keycode":0,"physical_keycode":4194332,"key_label":0,"unicode":0,"echo":false,"script":null)
]
}
"""  # noqa: E501 (project.godot as Godot writes it: one line per input event)


def _project(tmp_path):
    d = tmp_path / "skygarden"
    (d / "scripts").mkdir(parents=True)
    (d / "project.godot").write_text(PROJECT_GODOT, encoding="utf-8")
    (d / "scripts" / "score.gd").write_text("extends Node\nvar points := 0\n")
    return d


def test_reads_project_godot(tmp_path):
    s = godotmod.read_project_godot(str(_project(tmp_path) / "project.godot"))
    assert s["application"]["run/main_scene"] == "res://scenes/title.tscn"
    assert s["application"]["config/name"] == "Sky Garden"
    assert s["autoload"]["Score"] == "*res://scripts/score.gd"
    assert s["input"] == {"walk_up": ["W", "Up"], "hop": ["SpaceBar"], "shoot": ["LeftMouseButton", "F1"]}
    assert godotmod.read_project_godot(str(tmp_path / "nope.godot")) == {}


def test_project_name_is_config_name(tmp_path):
    g = engines.get("godot")
    d = _project(tmp_path)
    assert g.project_name(str(d)) == "Sky Garden"  # (what the addon's hello says)
    (d / "project.godot").write_text("config_version=5\n", encoding="utf-8")
    assert g.project_name(str(d)) == "skygarden"


def test_codex_index_says_where_to_start(tmp_path):
    g = engines.get("godot")
    root = tmp_path / "codex"
    (root / "scripts").mkdir(parents=True)
    (root / "project.godot").write_text(PROJECT_GODOT, encoding="utf-8")
    (root / "scripts" / "score.gd").write_text("extends Node\n")
    (root / "summary.txt").write_text("Godot project")
    g.write_index(str(root), "Sky Garden")
    text = (root / "README.md").read_text(encoding="utf-8")
    assert "Main scene (the game starts there): res://scenes/title.tscn (in the codex as scenes/title.tscn)" in text
    assert "/root/Score = res://scripts/score.gd" in text and "AutopilotPlaytest" not in text
    assert "walk_up (W, Up)" in text and "hop (SpaceBar)" in text
    assert "summary.txt" in text


def test_launch_command_reads_as_the_addon_does(tmp_path):
    g = engines.get("godot")
    d = _project(tmp_path)
    url = "ws://127.0.0.1:9999/v1/bridge"
    cmd = g.launch_command(str(d), url, "game.log", map="res://scenes/level.tscn", exe="godot.exe")
    assert cmd[:5] == ["godot.exe", "--headless", "--path", str(d), "--log-file"]
    user = cmd[cmd.index("--") + 1:]
    # the addon reads these (either spelling): a goal run, the brain's URL, the scene to start in
    assert user == ["-Playtest=goal", "-PlaytestGoal=autopilot", f"-PlaytestUrl={url}", "-PlaytestMap=res://scenes/level.tscn"]
    assert "-PlaytestMap" not in " ".join(g.launch_command(str(d), url, "game.log"))


def test_addon_reads_the_launch_arguments():
    """The addon's command-line parser accepts the names launch_command uses (checked on its source)."""
    src = open(os.path.join(os.path.dirname(__file__), "..", "plugin", "godot", "addons", "autopilot",
                            "playtest.gd"),
               encoding="utf-8").read()
    for key in ("playtest", "playtestgoal", "playtesturl", "playtestmap", "playtestclaim", "playtestreportdir"):
        assert f'"{key}":' in src, key


class ThingsLink:
    def __init__(self):
        self.hello = {"project": "Sky Garden", "engine": "godot"}
        self.asked = []

    def request(self, op, args=None, timeout=None):
        self.asked.append((op, args))
        if op == "find":
            if args.get("things"):
                return {"objects": ["/root/Garden", "/root/Garden/Player", "/root/Garden/Bird2"]}
            return {"objects": ["/root/Garden", "/root/Garden/Player", "/root/Garden/Player/Mesh",
                                "/root/Garden/Bird2"]}
        if op == "transforms":
            return {"objects": {o: {"location": {"X": 1, "Y": 2, "Z": 3}} for o in args["objects"]}}
        raise AssertionError(op)


def test_all_actors_are_the_scenes_things():
    link = ThingsLink()
    game = Game(link)
    assert game.all_actors() == ["/root/Garden", "/root/Garden/Player", "/root/Garden/Bird2"]
    assert link.asked[-1] == ("find", {"things": True})
    assert game.named("bird") == ["/root/Garden/Bird2"]
    assert game.name_of("/root/Garden/Bird2") == "Bird2" and game.name_of("/root/Garden/Player:position") == "Player"
    assert game.locs(["/root/Garden/Bird2"]) == {"/root/Garden/Bird2": Vec(1, 2, 3)}


def test_godot_frame_values():
    g = engines.get("godot")
    assert g.to_kit({"x": 3.0, "y": 0.5, "z": -6.0}) == Vec(600, 300, 50)  # forward 6 m, right 3 m, up 0.5 m
    assert g.to_native(Vec(600, 300, 50)) == {"x": 3.0, "y": 0.5, "z": -6.0}
    yaw, pitch = g.rotation_to_kit({"x": 0.1, "y": -1.5707963, "z": 0})  # turned right a quarter turn, looking up
    assert abs(yaw - 90) < 1e-4 and abs(pitch - 5.7296) < 1e-3


def test_prompt_godot_section_is_general():
    from autopilot.agents import session

    text = session.system_prompt(engines.get("godot"), "runs/r")
    section = text[text.index("## The engine: Godot"):]
    for needle in ("/root/", "class_name", "group:", "to_kit", "to_native", "InputMap",
                   "game.restart(map=", "doesn't step up", "omitted", "2D games", "In the editor"):
        assert needle in section, needle
    # nothing of a particular game (the test game's names stay out of the brain)
    for word in ("Orchard", "Apple", "apple", "Fruit", "GameState", "Lever", "Gate", "Mailbox", "Pond", "Basket"):
        assert not re.search(rf"\b{word}\b", section), word
