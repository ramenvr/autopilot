"""What every engine's plugin does alike, as the brain sees it (one place, so a gap shows as a failing test)."""

from autopilot import engines


def test_every_engine_sends_the_games_errors():
    # Unreal, Unity, Godot and LÖVE all send the errors the game logs in play (`log` events: Godot's addon from its
    # Logger, lib/detectors.gd; LÖVE's module from the Lua errors its callbacks raise), so an episode without any can
    # say "no errors logged"
    for name in ("unreal", "unity", "godot", "love"):
        assert engines.get(name).error_log, name


class PointerLink:
    """A plugin that answers `pointer` (Unreal's, Unity's and Godot's all do): records what it was asked."""

    hello = {"project": "fake", "features": ["timed_hold", "pointer"]}

    def __init__(self):
        self.asked = []

    def request(self, op, args=None, timeout=None):
        self.asked.append((op, args))
        if op == "pointer":
            return {"screen_x": 0.5, "screen_y": -0.25, "x": 1440, "y": 405, "on_screen": True}
        return {}

    def request_at(self, op, args=None, timeout=None):
        return self.request(op, args, timeout), 0.0

    def post(self, op, args=None):
        self.request(op, args)


def test_pointer_goes_to_a_screen_place_a_world_point_or_an_object():
    from autopilot.kit.game import Game
    from autopilot.kit.geom import Vec

    link = PointerLink()
    game = Game(link)
    assert game.pointer(0.5, -0.25)["on_screen"] is True
    game.pointer(world=Vec(100, -50, 20))
    game.pointer(object="/Game/Maps/Main.Main:PersistentLevel.Barracks_1")
    asked = [args for op, args in link.asked if op == "pointer"]
    assert asked == [{"screen_x": 0.5, "screen_y": -0.25}, {"world": {"X": 100, "Y": -50, "Z": 20}},
                     {"object": "/Game/Maps/Main.Main:PersistentLevel.Barracks_1"}]


def _unity_project(root, manifest='{"dependencies": {"com.ramenvr.autopilot": "file:../x"}}'):
    import os

    os.makedirs(os.path.join(root, "ProjectSettings"))
    os.makedirs(os.path.join(root, "Packages"))
    os.makedirs(os.path.join(root, "Assets"))
    with open(os.path.join(root, "ProjectSettings", "ProjectVersion.txt"), "w") as f:
        f.write("m_EditorVersion: 6000.6.5f1\n")
    with open(os.path.join(root, "Packages", "manifest.json"), "w") as f:
        f.write(manifest)
    with open(os.path.join(root, "Assets", "Player.cs"), "w") as f:
        f.write("class Player {}\n")
    return str(root)


def test_a_unity_index_is_made_without_the_editor_open(tmp_path, monkeypatch):
    # As Unreal's codex runs its commandlet: a project not open in an editor gets its scene/asset export from a
    # batch-mode editor (CodexExport.Run); an open one (its editor makes it), one without the package, or a codex
    # that has it already, don't
    import os

    from autopilot.engines import unity as unitymod

    made = []

    def export(folder, out, timeout=1800):
        made.append(folder)
        with open(os.path.join(out, "summary.txt"), "w") as f:
            f.write("scenes: 1\n")
        return 0

    monkeypatch.setattr(unitymod, "export_project", export)
    eng = unitymod.Unity()
    project = _unity_project(tmp_path / "Game")
    codex = tmp_path / "codex"
    codex.mkdir()
    eng.build_local(project, str(codex))
    assert made == [project] and eng.has_export(str(codex)) and (codex / "Assets" / "Player.cs").exists()
    eng.build_local(project, str(codex))
    assert made == [project]  # (the export there already)

    open_one = _unity_project(tmp_path / "Open")
    os.makedirs(os.path.join(open_one, "Temp"))
    open(os.path.join(open_one, "Temp", "UnityLockfile"), "w").close()
    monkeypatch.setattr(unitymod, "project_open", lambda folder: folder == open_one)
    codex2 = tmp_path / "codex2"
    codex2.mkdir()
    eng.build_local(open_one, str(codex2))
    bare = _unity_project(tmp_path / "Bare", manifest='{"dependencies": {}}')
    eng.build_local(bare, str(codex2))
    eng.build_local(project, str(tmp_path / "codex3"), export=False)
    assert made == [project]


def test_an_unlocked_unity_lockfile_is_not_an_open_project(tmp_path):
    import os

    from autopilot.engines import unity as unitymod

    project = _unity_project(tmp_path / "Game")
    assert not unitymod.project_open(project)
    os.makedirs(os.path.join(project, "Temp"))
    open(os.path.join(project, "Temp", "UnityLockfile"), "w").close()
    assert not unitymod.project_open(project)  # (left by an editor that closed: nobody holds it)


def test_every_engine_runs_a_built_game_headless():
    # Unity runs a player build; Unreal a packaged build (no project, no -game); Godot an exported build (no --path)
    from autopilot.engines import godot as godotmod
    from autopilot.engines import unreal as unrealmod

    url, log = "ws://127.0.0.1:1/v1/bridge", "game.log"
    ue = unrealmod.Unreal()
    editor = ue.launch_command("C:/G/Game.uproject", url, log, exe="C:/UE/UnrealEditor.exe")
    assert editor[:2] == ["C:/UE/UnrealEditor.exe", "C:/G/Game.uproject"] and "-game" in editor
    packaged = ue.launch_command("C:/G/Game.uproject", url, log, map="/Game/Maps/Arena", exe="C:/Build/Game.exe")
    assert packaged[:2] == ["C:/Build/Game.exe", "/Game/Maps/Arena"] and "-game" not in packaged
    assert "C:/G/Game.uproject" not in packaged and f"-PlaytestUrl={url}" in packaged and "-nullrhi" in packaged

    gd = godotmod.Godot()
    engine = gd.launch_command("C:/G", url, log, exe="C:/Godot/Godot_v4.7.2-stable_win64_console.exe")
    assert "--path" in engine and "--headless" in engine
    exported = gd.launch_command("C:/G", url, log, exe="C:/Build/Orchard.exe")
    assert "--path" not in exported and exported[:2] == ["C:/Build/Orchard.exe", "--headless"]
    assert f"-PlaytestUrl={url}" in exported
