"""A game's Autopilot folder (autopilot/gamefolder.py): the codex and what runs learned, exported with the game and
brought into another machine's memory before a run on a build."""

import json
import os
import time
import types

from autopilot import cli, gamefolder


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def dev_memory(root):
    write(os.path.join(root, "codex", "source", "Door.cpp"), "void Open();")
    write(os.path.join(root, "codex", "level.txt"), "this machine's level now")
    write(os.path.join(root, "codex", ".fetched.json"), "{}")
    write(os.path.join(root, "notes.md"), "doors open with E")
    write(os.path.join(root, "lib.py"), "def door(): pass\n")
    write(os.path.join(root, "runs", "r1", "trace1.jsonl"), "{}")
    return root


def test_export_writes_the_codex_and_what_was_learned(tmp_path):
    dev = dev_memory(str(tmp_path / "dev" / "MyGame"))
    game = tmp_path / "game"
    game.mkdir()
    (game / "project.godot").write_text("")
    dest = str(game / "Autopilot")
    info = gamefolder.export(dev, dest, "MyGame", source=str(game))
    assert read(os.path.join(dest, "codex", "source", "Door.cpp")) == "void Open();"
    assert not os.path.exists(os.path.join(dest, "codex", "level.txt"))  # (this machine's, of the moment)
    assert not os.path.exists(os.path.join(dest, "codex", ".fetched.json")) and not os.path.exists(
        os.path.join(dest, "runs"))
    assert read(os.path.join(dest, "memory", "notes.md")) == "doors open with E"
    assert info["codex_files"] == 1 and info["memory"] == ["notes.md", "lib.py"] and info["project"] == "MyGame"
    assert json.loads(read(os.path.join(dest, "export.json")))["source"] == str(game)
    assert (game / "Autopilot" / ".gdignore").exists()
    # a second export replaces the codex whole (a file gone from the source goes from the export)
    os.remove(os.path.join(dev, "codex", "source", "Door.cpp"))
    write(os.path.join(dev, "codex", "source", "Gate.cpp"), "void Lift();")
    gamefolder.export(dev, dest, "MyGame")
    assert os.listdir(os.path.join(dest, "codex", "source")) == ["Gate.cpp"]


def test_export_copies_the_regressions_saved_in_the_memory(tmp_path, monkeypatch):
    """Regressions are saved in the memory (never into the game's folder by themselves); an export copies them into
    the game's Autopilot/regressions, replacing a run's copy there, and leaves the others there alone."""
    monkeypatch.delenv("AUTOPILOT_REGRESSIONS", raising=False)
    dev = dev_memory(str(tmp_path / "dev" / "MyGame"))
    write(os.path.join(dev, "regressions", "r1", "regression.json"), '{"id": "r1"}')
    write(os.path.join(dev, "regressions", "r1", "harness.py"), "# first\n")
    dest = str(tmp_path / "game" / "Autopilot")
    write(os.path.join(dest, "regressions", "teammate", "regression.json"), '{"id": "teammate"}')
    info = gamefolder.export(dev, dest, "MyGame")
    assert info["regressions"] == 1
    assert read(os.path.join(dest, "regressions", "r1", "harness.py")) == "# first\n"
    assert sorted(os.listdir(os.path.join(dest, "regressions"))) == ["r1", "teammate"]
    write(os.path.join(dev, "regressions", "r1", "harness.py"), "# second\n")
    gamefolder.export(dev, dest, "MyGame")
    assert read(os.path.join(dest, "regressions", "r1", "harness.py")) == "# second\n"
    assert os.path.exists(os.path.join(dest, "regressions", "teammate", "regression.json"))
    # no regressions saved: none copied
    assert gamefolder.export(dev_memory(str(tmp_path / "other" / "MyGame")), dest, "MyGame")["regressions"] == 0


def test_resolve(tmp_path):
    folder = tmp_path / "game" / "Autopilot"
    (folder / "regressions").mkdir(parents=True)
    assert gamefolder.resolve(str(folder)) == str(folder)
    assert gamefolder.resolve(str(tmp_path / "game")) == str(folder)
    assert gamefolder.resolve(str(folder / "regressions")) == str(folder)
    assert gamefolder.regressions(str(folder)) == str(folder / "regressions")
    assert gamefolder.resolve(str(tmp_path / "nothing")) is None and gamefolder.resolve("") is None


def test_seed_brings_the_codex_and_never_overwrites_what_was_learned_here(tmp_path):
    dest = str(tmp_path / "game" / "Autopilot")
    gamefolder.export(dev_memory(str(tmp_path / "dev" / "MyGame")), dest, "MyGame")
    qa = str(tmp_path / "qa" / "MyGame")
    said = gamefolder.seed(qa, dest)  # (a QA machine with no memory of the game)
    assert read(os.path.join(qa, "codex", "source", "Door.cpp")) == "void Open();"
    assert read(os.path.join(qa, "notes.md")) == "doors open with E" and len(said) == 3
    assert gamefolder.seed(qa, dest) == []  # (nothing new)

    # runs here learn: the folder's unchanged copy never overwrites it
    write(os.path.join(qa, "notes.md"), "doors open with E; the deer can't be hurt")
    assert gamefolder.seed(qa, dest) == []
    assert "deer" in read(os.path.join(qa, "notes.md"))
    # the developer's lib.py changes and this machine's didn't: taken
    write(os.path.join(dest, "memory", "lib.py"), "def door(): return 1\n")
    assert gamefolder.seed(qa, dest) == [f"lib.py from {dest}"]
    assert "return 1" in read(os.path.join(qa, "lib.py"))
    # both changed: this machine's is kept, and the run is told
    write(os.path.join(dest, "memory", "notes.md"), "doors open with E, gates with G")
    said = gamefolder.seed(qa, dest)
    assert "deer" in read(os.path.join(qa, "notes.md")) and said[0].startswith("kept this machine's notes.md")
    # a newer export replaces the codex; an older one doesn't replace a newer codex here
    write(os.path.join(qa, "codex", "source", "Door.cpp"), "void Open(int);")
    assert not any("code index" in line for line in gamefolder.seed(qa, dest))
    assert read(os.path.join(qa, "codex", "source", "Door.cpp")) == "void Open(int);"
    time.sleep(0.05)
    gamefolder.export(dev_memory(str(tmp_path / "dev2" / "MyGame")), dest, "MyGame")
    assert any("code index" in line for line in gamefolder.seed(qa, dest))
    assert read(os.path.join(qa, "codex", "source", "Door.cpp")) == "void Open();"


def test_the_commands_take_the_game_folder(tmp_path, monkeypatch, capsys):
    args = cli.parser().parse_args(["play", "Test the level", "--build", "Game.exe", "--watch", "--game-folder", "G",
                                    "--time-limit", "15", "--events"])
    assert (args.build, args.watch, args.game_folder, args.time_limit, args.events, args.port) == (
        "Game.exe", True, "G", 15.0, True, None)
    assert cli.parser().parse_args(["regress", "--game-folder", "G"]).game_folder == "G"
    # autopilot codex --out exports after building
    dev = dev_memory(str(tmp_path / "memory" / "MyGame"))
    monkeypatch.setattr(cli.codexmod, "project_dir", lambda project: dev)
    monkeypatch.setattr(cli.codexmod, "build", lambda project, path=None, engine=None: types.SimpleNamespace(
        root=os.path.join(dev, "codex")))
    monkeypatch.setattr(cli, "remember_project", lambda *a: None)
    project = tmp_path / "MyGame"
    project.mkdir()
    (project / "MyGame.uproject").write_text("{}")
    out = str(project / "Autopilot")
    assert cli.cmd_codex(cli.parser().parse_args(["codex", "--project", str(project), "--out", out])) == 0
    assert os.path.exists(os.path.join(out, "codex", "source", "Door.cpp")) and "Exported to" in capsys.readouterr().out


def test_play_tells_the_run_as_events(monkeypatch, capsys, tmp_path):
    """play --events: the status lines and activity the run sends, then its report (summary, findings, cost, where
    its folder is), each a line of JSON; the build it started is ended after."""
    sent = []

    class Current:
        closed = False

        def send(self, frame):
            sent.append(frame)

        def status(self, text):
            sent.append({"type": "status", "text": text})

    current = Current()
    link = types.SimpleNamespace(hello={"project": "MyGame", "host": "game"}, current=current, closed=False,
                                 _state={})

    def run_prompt(link, prompt, *a, **kw):
        link.status("Reading the game's code")
        link.send({"type": "activity", "t": 1.0, "stage": "analyze", "kind": "read", "text": "Read Door.cpp"})
        cli.LAST_REPORT.clear()
        cli.LAST_REPORT.update({"run": "r9", "project": "MyGame", "outcome": "explored", "summary": "ok",
                                "findings": ["the deer can't be hurt"], "cost_usd": 5.5, "other": 1})
        return 0

    monkeypatch.setattr(cli, "run_prompt", run_prompt)
    monkeypatch.setattr(cli.codexmod, "project_dir", lambda project: str(tmp_path / project))
    killed = []
    monkeypatch.setattr(cli.processes, "kill", killed.append)
    args = cli.parser().parse_args(["play", "Test the level", "--events"])
    args.game_process = "game"
    assert cli.cmd_play(link, args) == 0
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]
    assert [e["event"] for e in events] == ["status", "activity", "report"]
    assert events[1]["text"] == "Read Door.cpp" and events[1]["stage"] == "analyze"
    report = events[2]
    assert report["findings"] == ["the deer can't be hurt"] and report["cost_usd"] == 5.5 and "other" not in report
    assert report["run_dir"] == os.path.join(str(tmp_path / "MyGame"), "runs", "r9")
    assert sent[0] == {"type": "status", "text": "Reading the game's code"} and killed == ["game"]
