"""The Unity adapter (autopilot/engines/unity.py) without Unity: the plugin's paths, the codex rules, the project's
name, the headless launch, the kit frame of rc's rotations, and the implementer's prompt for a Unity game."""

import os
import types

from autopilot import engines

UNITY = engines.get("unity")


def _project(tmp_path, product="Space Miner"):
    p = tmp_path / "MinerProject"
    (p / "ProjectSettings").mkdir(parents=True)
    (p / "Assets" / "Scripts").mkdir(parents=True)
    (p / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 6000.6.4f1\n")
    (p / "ProjectSettings" / "ProjectSettings.asset").write_text(
        "PlayerSettings:\n  companyName: Co\n  productName: " + product
        + "\n  productGUID: 0123456789abcdef0123456789abcdef\n")
    return p


def test_name_of_follows_the_plugins_paths():
    assert UNITY.name_of("Main:/Level/Coins/Coin@2") == "Coin@2"
    assert UNITY.name_of("Main:/Level/Coins/Coin") == "Coin"
    assert UNITY.name_of("Main:/Player|PlayerMover") == "Player"
    assert UNITY.name_of("Main:/Player|PlayerMover@1") == "Player"
    assert UNITY.name_of("/Level/Door") == "Door"
    # Escaped / | @ in names stay escaped (as the plugin writes them, and view's names)
    assert UNITY.name_of(r"Main:/Level/Door 1\/2|Hinge") == r"Door 1\/2"
    assert UNITY.name_of(r"Main:/Pipe\|Valve") == r"Pipe\|Valve"
    assert UNITY.name_of(r"Main:/A\@B@1") == r"A\@B@1"


def test_codex_rules():
    ok = {
        "Assets/Scripts/Player.cs": "Assets/Scripts/Player.cs",
        "Assets/Input/Controls.inputactions": "Assets/Input/Controls.inputactions",
        "Assets/Scripts/Game.asmdef": "Assets/Scripts/Game.asmdef",
        "Packages/com.studio.tools/Runtime/Tool.cs": "Packages/com.studio.tools/Runtime/Tool.cs",
        "ProjectSettings/ProjectSettings.asset": "ProjectSettings/ProjectSettings.asset",
        "ProjectSettings/ProjectVersion.txt": "ProjectSettings/ProjectVersion.txt",
        "Packages/manifest.json": "Packages/manifest.json",
        "Library/AutopilotCodex/summary.txt": "summary.txt",
        "Library/AutopilotCodex/scenes/Scenes/Main.unity.txt": "scenes/Scenes/Main.unity.txt",
    }
    for rel, dest in ok.items():
        assert UNITY.codex_path(rel) == dest, rel
    for bad in ("Packages/com.ramenvr.autopilot/Runtime/Ops.cs",  # (the plugin's own code isn't the game's)
                "Assets/Samples~/X.cs", "Assets/.hidden/X.cs", "Packages/packages-lock.json", "Packages/x.cs",
                "Library/ScriptAssemblies/Game.dll", "Temp/AutopilotCodex/summary.txt",
                "Library/AutopilotCodex/README.md",
                "Library/AutopilotCodex/level.txt", "Assets/X.cs.meta", "Assets/../X.cs", "C:/Assets/X.cs", None):
        assert UNITY.codex_path(bad) is None, bad


def test_local_codex_walks_into_the_export_only(tmp_path):
    p = _project(tmp_path)
    (p / "Assets" / "Scripts" / "Miner.cs").write_text("class Miner {}")
    (p / "Library" / "AutopilotCodex" / "scenes").mkdir(parents=True)
    (p / "Library" / "AutopilotCodex" / "summary.txt").write_text("summary")
    (p / "Library" / "ScriptAssemblies").mkdir(parents=True)
    (p / "Library" / "ScriptAssemblies" / "x.cs").write_text("no")
    (p / "Packages" / "com.ramenvr.autopilot").mkdir(parents=True)
    (p / "Packages" / "com.ramenvr.autopilot" / "Ops.cs").write_text("no")
    root = tmp_path / "codex"
    root.mkdir()
    UNITY.copy_source(str(p), str(root))
    files = sorted(os.path.relpath(os.path.join(b, f), root).replace(os.sep, "/")
                   for b, _, fs in os.walk(root) for f in fs)
    assert files == ["Assets/Scripts/Miner.cs", "ProjectSettings/ProjectSettings.asset",
                     "ProjectSettings/ProjectVersion.txt", "summary.txt"]
    UNITY.write_index(str(root), "Space Miner")
    readme = (root / "README.md").read_text(encoding="utf-8")
    assert "Unity" in readme and "scenes/<path>.txt" in readme and "activeInputHandler" in readme


def test_project_name_is_the_product_name(tmp_path):
    p = _project(tmp_path)
    assert UNITY.project_at(str(p / "Assets")) == str(p)
    assert UNITY.project_name(str(p)) == "Space Miner"
    (p / "ProjectSettings" / "ProjectSettings.asset").write_text("PlayerSettings:\n  companyName: Co\n")
    assert UNITY.project_name(str(p)) == "MinerProject"


def test_headless_launch_player_and_editor(tmp_path):
    p = _project(tmp_path)
    url = "ws://127.0.0.1:9999/v1/bridge"
    player = UNITY.launch_command(str(p), url, "g.log", map="Level2", exe="C:/Builds/Miner.exe")
    assert player[0] == "C:/Builds/Miner.exe" and "-batchmode" in player and "-nographics" in player
    assert f"-PlaytestUrl={url}" in player and "-PlaytestMap=Level2" in player and "-Playtest=goal" in player
    editor = UNITY.launch_command(str(p), url, "g.log")
    assert editor[0].lower().endswith("unity.exe") and editor[editor.index("-projectPath") + 1] == str(p)
    assert editor[editor.index("-executeMethod") + 1] == "Autopilot.Playtest.Editor.Headless.Run"
    assert "-quit" not in editor and "-batchmode" in editor and "-nographics" in editor


def test_rotations_from_rc():
    assert UNITY.rotation_to_kit({"x": 10, "y": 270, "z": 0}) == (-90, -10)
    # A Quaternion as the plugin sends it: x, y, z, w and its euler angles
    assert UNITY.rotation_to_kit({"x": 0, "y": 0.7071, "z": 0, "w": 0.7071,
                                  "euler": {"x": 0, "y": 90, "z": 0}}) == (90, 0)
    yaw, pitch = 45.0, 20.0
    assert UNITY.rotation_to_kit(UNITY.rotation_to_native(yaw, pitch)) == (yaw, pitch)


def test_all_actors_asks_for_things():
    asked = []

    def request(op, args=None, timeout=None):
        asked.append((op, args))
        return {"objects": ["Main:/Door"]}

    game = types.SimpleNamespace(request=request)
    assert UNITY.all_actors(game) == ["Main:/Door"]
    assert asked == [("find", {"things": True})]


def test_the_prompt_for_a_unity_game():
    from autopilot.agents import session

    text = session.system_prompt(UNITY, "runs/r")
    assert "a video game made in Unity." in text and "## The engine: Unity" in text
    for unity_fact in ("Main:/Player|PlayerMover", "game.to_kit", "UnityEngine.Input", "Player/Jump", "play mode",
                       "DontDestroyOnLoad", "omitted"):
        assert unity_fact in text, unity_fact
    assert "## The engine: Unreal Engine" not in text and "{kit}" not in text and "{engine}" not in text


def test_a_player_build_stands_for_its_project_and_is_launched(tmp_path):
    from autopilot.engines import unity as unitymod

    p = _project(tmp_path)
    exe = tmp_path / "Build" / "Miner.exe"
    (tmp_path / "Build" / "Miner_Data").mkdir(parents=True)
    exe.write_bytes(b"MZ")
    (tmp_path / "Build" / "Miner_Data" / "level0").write_bytes(
        b"\x00\x00junk" + str(p).encode() + b"\x00\x00\x00" + b"C:/Program Files/Unity/Editor/Unity.exe\x00")
    eng = unitymod.Unity()
    found, project = engines.find_project(str(exe), "unity")[0], eng.project_at(str(exe))
    assert found.name == "unity" and os.path.samefile(project, p)
    cmd = eng.launch_command(project, "ws://127.0.0.1:1/v1/bridge", "g.log")
    assert cmd[0] == str(exe) and "-batchmode" in cmd
    assert eng.project_at(str(tmp_path / "Nothing.exe")) is None
