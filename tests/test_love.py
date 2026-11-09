"""The LÖVE adapter (autopilot/engines/love.py) without LÖVE: the project, its name and codex, the index's notes, the
kit frame, the launch, the game backend's things, and the LÖVE section of the implementer's prompt."""

import os
import re
import types

from autopilot import engines
from autopilot.engines import love as lovemod
from autopilot.kit.game import Game
from autopilot.kit.geom import Vec

CONF = """function love.conf(t)
  t.identity = "sky_hopper"
  t.window.title = "Sky Hopper"
  t.window.width = 800
  t.console = false
end
"""

MAIN = """local bump = require("lib.bump")
player = {x = 0, y = 0, speed = 120}

function love.load() end
love.update = function(dt)
  if love.keyboard.isDown("left", "a") then player.x = player.x - player.speed * dt end
end
function love.keypressed(key)
  if key == "space" then jump() end
end
function love.draw() end
"""


def _project(tmp_path, conf=CONF):
    d = tmp_path / "skyhopper"
    for sub in ("lib", "shaders", "autopilot", ".git", "assets"):
        (d / sub).mkdir(parents=True)
    (d / "main.lua").write_text(MAIN, encoding="utf-8")
    if conf is not None:
        (d / "conf.lua").write_text(conf, encoding="utf-8")
    (d / "lib" / "bump.lua").write_text("return {}\n")
    (d / "shaders" / "glow.frag").write_text("vec4 effect() {}\n")
    (d / "autopilot" / "init.lua").write_text("-- the module\n")
    (d / ".git" / "x.lua").write_text("-- not the game's\n")
    (d / "assets" / "hero.png").write_bytes(b"\x89PNG")
    return d


def test_engine_from_hello():
    assert engines.for_hello({"engine": "love"}).name == "love"
    link = types.SimpleNamespace(hello={"project": "Sky Hopper", "engine": "love"})
    game = Game(link)
    assert game.engine.name == "love" and game.keys["jump"] == "SpaceBar"


def test_project_is_the_folder_with_main_lua(tmp_path):
    d = _project(tmp_path)
    for p in (d, d / "main.lua"):
        eng, path = engines.find_project(str(p))
        assert eng.name == "love" and os.path.samefile(path, d), p
    assert engines.get("love").project_at(str(d / "conf.lua")) is None
    assert engines.get("love").project_at(str(tmp_path)) is None


def test_project_name_is_what_the_module_says(tmp_path):
    g = engines.get("love")
    d = _project(tmp_path)
    assert g.project_name(str(d)) == "Sky Hopper"  # (the window's title)
    (d / "conf.lua").write_text('function love.conf(t)\n  t.identity = "sky_hopper"\nend\n', encoding="utf-8")
    assert g.project_name(str(d)) == "sky_hopper"
    (d / "conf.lua").write_text('function love.conf(t)\n  t.window.title = "Untitled"\nend\n', encoding="utf-8")
    assert g.project_name(str(d)) == "skyhopper"  # (LÖVE's default title: the folder's name, as its identity)


def test_reads_conf_lua(tmp_path):
    d = _project(tmp_path)
    conf = lovemod.read_conf(str(d / "conf.lua"))
    assert conf == {"identity": "sky_hopper", "window.title": "Sky Hopper", "window.width": 800, "console": False}
    assert lovemod.read_conf(str(d / "nope.lua")) == {}


def test_codex_is_the_games_lua_and_shaders(tmp_path):
    g = engines.get("love")
    d = _project(tmp_path)
    root = tmp_path / "codex"
    n = g.copy_source(str(d), str(root))
    got = sorted(os.path.relpath(os.path.join(b, f), root).replace(os.sep, "/")
                 for b, _, fs in os.walk(root) for f in fs)
    assert got == ["conf.lua", "lib/bump.lua", "main.lua", "shaders/glow.frag"] and n == 4
    for rel in ("autopilot/init.lua", ".git/x.lua", "../main.lua", "/etc/x.lua", "assets/hero.png"):
        assert g.codex_path(rel) is None, rel


def test_codex_index_says_where_to_start(tmp_path):
    g = engines.get("love")
    d = _project(tmp_path)
    root = tmp_path / "codex"
    g.copy_source(str(d), str(root))
    g.write_index(str(root), "Sky Hopper")
    text = (root / "README.md").read_text(encoding="utf-8")
    assert "love.load, love.update, love.draw, love.keypressed" in text
    assert "window.title = Sky Hopper" in text
    assert "bump.lua" in text
    assert "Keys the code reads (love.keyboard.isDown, or a key compared in a key callback): a, left, space" in text


def test_love_frame_values():
    g = engines.get("love")
    assert g.to_kit({"x": 120, "y": 300}) == Vec(120, 0, -300)  # (right 120 px, down 300 px)
    assert g.to_kit([5, 6]) == Vec(5, 0, -6)
    assert g.to_native(Vec(120, 0, -300)) == {"x": 120, "y": 300}
    yaw, pitch = g.rotation_to_kit(1.5707963)  # a quarter turn clockwise on screen
    assert yaw == 0 and abs(pitch + 90) < 1e-4
    assert abs(g.rotation_to_native(0, -90) - 1.5707963) < 1e-6


def test_launch_command_reads_as_the_module_does(tmp_path, monkeypatch):
    monkeypatch.setenv("DISPLAY", ":0")  # (no xvfb-run in front of it)
    g = engines.get("love")
    d = _project(tmp_path)
    url = "ws://127.0.0.1:9999/v1/bridge"
    cmd = g.launch_command(str(d), url, "game.log", exe="love")
    assert cmd == ["love", str(d), "--playtest=goal", "--playtest-goal=autopilot", f"--playtest-url={url}",
                   "--playtest-log=game.log"]
    fused = g.launch_command(str(d), url, "game.log", exe="/games/SkyHopper")
    assert fused[:2] == ["/games/SkyHopper", "--playtest=goal"]  # (a fused game needs no folder)


def test_module_reads_the_launch_arguments():
    """The module's command-line parser takes the names launch_command uses (checked on its source)."""
    src = open(os.path.join(os.path.dirname(__file__), "..", "plugin", "love", "autopilot", "init.lua"),
               encoding="utf-8").read()
    for key in ("playtest", "playtestgoal", "playtesturl", "playtestlog", "playtestclaim", "playtestreportdir"):
        assert f"args.{key}" in src, key


class ThingsLink:
    def __init__(self):
        self.hello = {"project": "Sky Hopper", "engine": "love"}
        self.asked = []

    def request(self, op, args=None, timeout=None):
        self.asked.append((op, args))
        if op == "find":
            return {"objects": ["/player", "/enemies/1", "/enemies/2", "/bell"]}
        if op == "transforms":
            return {"objects": {o: {"location": {"X": 1, "Y": 0, "Z": -3}} for o in args["objects"]}}
        raise AssertionError(op)


def test_things_by_path():
    link = ThingsLink()
    game = Game(link)
    assert game.all_actors() == ["/player", "/enemies/1", "/enemies/2", "/bell"]
    assert link.asked[-1] == ("find", {})
    assert game.named("enem") == []  # (named() matches the last part of a path: "1", "2")
    assert game.named("bell") == ["/bell"]
    assert game.name_of("/enemies/2") == "2"
    assert game.locs(["/bell"]) == {"/bell": Vec(1, 0, -3)}


def test_prompt_love_section_is_general():
    from autopilot.agents import session

    text = session.system_prompt(engines.get("love"), "runs/r")
    section = text[text.index("## The engine: LÖVE"):]
    for needle in ("No scene tree", "/player", "autopilot.track", "to_kit", "to_native", "love.keyboard.isDown",
                   "game.restart()", "LeftMouseButton", "game.screen()", "409"):
        assert needle in section, needle
    # nothing of a particular game (the test game's names stay out of the brain)
    for word in ("Coin", "coin", "Slime", "Bell", "Field"):
        assert not re.search(rf"\b{word}\b", section), word
