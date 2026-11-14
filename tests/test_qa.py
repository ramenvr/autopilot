"""Autopilot QA (autopilot/qa.py): the window that replays a game's regressions on a build of it. Its model and the
command it runs need no display; the window itself is driven where Tk can open one."""

import json
import os
import queue
import sys
import time
import types

import pytest

from autopilot import bridge, qa


@pytest.fixture(autouse=True)
def settings_here(tmp_path, monkeypatch):
    """qa.json goes to a folder of the test's, never this machine's settings folder."""
    monkeypatch.setattr(bridge, "brain_file", lambda name=bridge.BRAIN_FILE: str(tmp_path / "settings" / name))


def regression(folder, rid, prompt):
    os.makedirs(os.path.join(folder, rid))
    with open(os.path.join(folder, rid, "regression.json"), "w", encoding="utf-8") as f:
        json.dump({"id": rid, "prompt": prompt, "summary": f"{prompt}: yes", "saved": "2026-11-13T10:00:00"}, f)


def unity_build(tmp_path):
    exe = tmp_path / "build" / "MyGame.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"MZ")
    (tmp_path / "build" / "MyGame_Data").mkdir()
    return str(exe)


def test_the_command_is_autopilot_regress_on_the_build():
    cmd = qa.command("C:/b/Game.exe", "C:/g/Autopilot/regressions", only=["r1", "r2"], watch=True, retries=1,
                     python="py")
    assert cmd == ["py", "-m", "autopilot", "regress", "--build", "C:/b/Game.exe", "--events", "--retries", "1",
                   "--regressions", "C:/g/Autopilot/regressions", "--watch", "--only", "r1,r2"]
    cmd = qa.command("Game.exe", None, watch=False, retries=0, python="py")
    assert cmd == ["py", "-m", "autopilot", "regress", "--build", "Game.exe", "--events", "--retries", "0"]
    from autopilot import cli

    args = cli.parser().parse_args(qa.command("Game.exe", "regs", only=["a"], python="py")[3:])
    assert args.build == "Game.exe" and args.regressions == "regs" and args.only == ["a"] and args.watch
    assert args.events and args.retries == 1


def test_what_the_choices_say(tmp_path):
    ok, words = qa.describe_build(unity_build(tmp_path))
    assert ok and words.startswith("Unity build") and "Development build" in words
    assert qa.describe_build("") == (False, "Choose the game's build: its executable (a Development build).")
    assert qa.describe_build(str(tmp_path / "nope.exe"))[1] == "There's no file there."
    (tmp_path / "tool.exe").write_bytes(b"MZ")
    assert qa.describe_build(str(tmp_path / "tool.exe"))[1].startswith("Not a build Autopilot knows")

    regs = str(tmp_path / "Autopilot" / "regressions")
    regression(regs, "r2", "Verify the door opens")
    regression(regs, "r1", "Verify the coin counts")
    found = qa.regressions_in(regs)
    assert [r["id"] for r in found] == ["r1", "r2"] and found[1]["prompt"] == "Verify the door opens"
    # (the game's Autopilot folder, the game's folder or the regressions folder: the same regressions)
    folder = str(tmp_path / "Autopilot")
    assert qa.regressions_in(folder) == found == qa.regressions_in(str(tmp_path))
    assert qa.describe_folder(regs, found).startswith("2 regressions; no code index (a Testing run then plays "
                                                      "without the game's code")
    assert qa.describe_folder("", []).startswith("No folder: this machine's Autopilot memory")
    assert qa.describe_folder(str(tmp_path / "x"), []) == "There's no folder there."
    assert qa.regressions_in("") == [] and qa.regressions_in(str(tmp_path / "none")) == []
    # with an exported code index and memory
    os.makedirs(os.path.join(folder, "codex", "source"))
    open(os.path.join(folder, "codex", "source", "A.cpp"), "w").close()
    os.makedirs(os.path.join(folder, "memory"))
    open(os.path.join(folder, "memory", "notes.md"), "w").close()
    with open(os.path.join(folder, "export.json"), "w") as f:
        json.dump({"exported": "2026-11-13T19:00:00"}, f)
    assert qa.describe_folder(folder, found) == ("2 regressions; code index: 1 files, exported 2026-11-13T19:00:00; "
                                                 "notes.md.")


def test_the_window_remembers_its_choices(tmp_path):
    assert qa.load_settings() == {}
    qa.save_settings({"build": "Game.exe", "watch": False})
    assert qa.load_settings() == {"build": "Game.exe", "watch": False}
    assert os.path.exists(tmp_path / "settings" / "qa.json")


def events_of_a_replay():
    """What `autopilot regress --build ... --events` prints for two regressions, one failing then passing (flaky)."""
    return [
        ("line", "Starting Unreal Engine build Game.exe (in a window; its log: x.log)"),
        ("event", {"event": "launching", "engine": "unreal", "build": "Game.exe", "watch": True}),
        ("event", {"event": "connected", "project": "MyGame", "host": "game", "engine": "unreal"}),
        ("event", {"event": "regressions", "out": "OUT", "items": [
            {"id": "r1", "prompt": "Verify the coin counts", "dir": "regs/r1"},
            {"id": "r2", "prompt": "Verify the door opens", "dir": "regs/r2"}]}),
        ("event", {"event": "replaying", "id": "r1", "try": 1}),
        ("event", {"event": "result", "id": "r1", "passed": True, "why": "as recorded", "seconds": 12.2,
                   "trace": "OUT/r1.jsonl", "evidence": {"coins": 1}}),
        ("event", {"event": "replaying", "id": "r2", "try": 1}),
        ("event", {"event": "replaying", "id": "r2", "try": 2, "why": "evidence differs"}),
        ("event", {"event": "result", "id": "r2", "passed": True, "flaky": True, "seconds": 30.0,
                   "why": "passed on try 2; the first: evidence differs: open: True then, False now"}),
        ("event", {"event": "summary", "passed": 2, "total": 2, "failed": [], "flaky": ["r2"], "out": "OUT"}),
        ("exit", 0),
    ]


def events_of_a_test():
    """What `autopilot play ... --build --events` prints for a Testing run that reports two findings."""
    return [
        ("event", {"event": "launching", "engine": "unreal", "watch": False}),
        ("event", {"event": "connected", "project": "MyGame", "host": "game"}),
        ("event", {"event": "status", "text": "Reading the game's code"}),
        ("event", {"event": "activity", "t": 9.7, "stage": "analyze", "kind": "read", "text": "Read Door.cpp"}),
        ("line", "Took the code index from G (2026-11-13T19:00:00)"),
        ("event", {"event": "report", "run": "r9", "prompt": "Test the level", "outcome": "explored",
                   "summary": "Played the level.", "findings": ["The deer can't be damaged.",
                                                                "Logs fall out of the world\nat the north wall."],
                   "episodes": 1, "seconds": 362.6, "cost_usd": 5.49, "visual": False, "run_dir": "R",
                   "exit_code": 0}),
        ("exit", 0),
    ]


def test_the_test_model_follows_a_testing_run():
    m = qa.TestModel()
    m.start()
    assert m.running and m.tag() == ("info", "TESTING")
    seen = []
    for kind, data in events_of_a_test():
        m.apply(kind, data)
        seen.append(m.status)
    assert "Starting the build (headless)..." in seen and "Reading the game's code..." in seen
    assert m.activity == ["    10s  Read Door.cpp"] and m.log == ["Took the code index from G (2026-11-13T19:00:00)"]
    assert m.status == "2 findings, 1 episode, 6 min, $5.49." and m.tag() == ("amber", "2 FINDINGS") and m.out == "R"
    summary = m.details()
    assert summary.startswith("Test the level") and "SUMMARY\nPlayed the level." in summary
    assert "PICTURE\nNone: the game ran headless" in summary and "RUN\nR" in summary
    assert m.details(1).startswith("Finding 2") and "north wall" in m.details(1)
    # a run that ends without reporting, or never connected
    m.start()
    m.apply("line", "Waiting for the plugin ...")
    m.apply("exit", 1)
    assert m.status.startswith("The run ended before it reported (exit code 1)") and m.tag() == ("red", "ERROR")
    m.start()
    m.apply("exit", None)
    assert m.status == "Stopped." and m.tag() == ("neutral", "STOPPED")
    m.start()
    m.apply("event", {"event": "report", "findings": []})
    assert m.tag() == ("success", "NO FINDINGS")


def test_the_test_command_is_autopilot_play_on_the_build(tmp_path):
    folder = tmp_path / "Autopilot"
    (folder / "codex").mkdir(parents=True)
    cmd = qa.test_command("Game.exe", str(tmp_path), "Test the level", minutes=20, watch=True, python="py")
    assert cmd == ["py", "-m", "autopilot", "play", "Test the level", "--build", "Game.exe", "--events",
                   "--time-limit", "20", "--game-folder", str(folder), "--watch"]
    assert qa.test_command("Game.exe", None, "x", python="py", watch=False) == [
        "py", "-m", "autopilot", "play", "x", "--build", "Game.exe", "--events"]
    from autopilot import cli

    args = cli.parser().parse_args(cmd[3:])
    assert args.cmd == "play" and args.build == "Game.exe" and args.time_limit == 20 and args.game_folder == str(folder)


def test_the_model_follows_a_replay():
    m = qa.Model()
    m.show([{"id": "r1", "prompt": "Verify the coin counts", "dir": "regs/r1"},
            {"id": "r2", "prompt": "Verify the door opens", "dir": "regs/r2"}])
    m.start(["r1", "r2"])
    assert m.running and m.status == "Starting the build..."
    seen = []
    for kind, data in events_of_a_replay():
        m.apply(kind, data)
        seen.append(m.status)
        if kind == "event" and data.get("id") == "r2" and data.get("try") == 2:
            assert m.status == "Replaying 2 of 2 (try 2)..." and m.rows["r2"]["status"] == qa.REPLAYING
    assert "Starting the build (in a window)..." in seen and "Connected to MyGame." in seen
    assert "Replaying 2 regressions, without a model..." in seen and "Replaying 1 of 2..." in seen
    assert [m.rows[r]["status"] for r in ("r1", "r2")] == [qa.PASSED, qa.FLAKY]
    assert m.status == "2/2 passed, 1 flaky (passed only on a second try)." and not m.running and m.out == "OUT"
    assert m.tone() == "good" and m.tag() == ("success", "PASSED 2/2")
    assert m.log == ["Starting Unreal Engine build Game.exe (in a window; its log: x.log)"]
    text = m.details("r1")
    assert text.startswith("Verify the coin counts") and "REPLAY\nOK  as recorded (12s)" in text
    assert '"coins": 1' in text and "TRACE\nOUT/r1.jsonl" in text and "id r1" in text
    parts = m.detail_parts("r2")
    assert parts[0] == ("title", "Verify the door opens\n") and ("amber", "FLAKY") in parts
    assert "passed on try 2" in m.details("r2")


def test_a_stopped_or_broken_replay_shows_what_wasnt_run():
    m = qa.Model()
    m.show([{"id": "r1", "prompt": "a"}, {"id": "r2", "prompt": "b"}, {"id": "r3", "prompt": "c"}])
    m.start(["r1", "r2"])  # (replay selected: r3 keeps what it showed)
    for kind, data in events_of_a_replay()[3:5]:
        m.apply(kind, data)
    m.apply("exit", None)
    assert m.status == "Stopped." and [m.rows[r]["status"] for r in ("r1", "r2", "r3")] == [
        qa.NOT_RUN, qa.NOT_RUN, qa.WAITING]
    # a build that never connected: the command's own words
    m.start(["r1"])
    m.apply("event", {"event": "error", "message": "The game didn't connect within 180s. A build connects only ..."})
    m.apply("exit", 1)
    assert m.status.startswith("The game didn't connect within 180s") and m.tone() == "bad"
    assert m.tag() == ("red", "ERROR")
    # a command that died without a word of its own: its last line
    m.start(["r1"])
    m.apply("line", "SystemExit: Give --build or --project, not both")
    m.apply("exit", 2)
    assert m.status == "The replay ended before it finished (exit code 2). SystemExit: Give --build or --project, " \
                       "not both"


def test_the_replay_command_is_read_as_it_goes(tmp_path):
    """Replay runs the command and reads its lines on a thread: JSON events as events, the rest as log lines, then
    its exit code; stop() ends it (and what it started) and says it was stopped."""
    script = tmp_path / "fake.py"
    script.write_text("import json, sys\n"
                      "print('Starting the build')\n"
                      "print(json.dumps({'event': 'connected', 'project': 'MyGame'}))\n"
                      "print('{not json')\n"
                      "sys.exit(1)\n")
    r = qa.Replay([sys.executable, str(script)])
    got = []
    deadline = time.time() + 30
    while time.time() < deadline and (not got or got[-1][0] != "exit"):
        try:
            got.append(r.events.get(timeout=1))
        except queue.Empty:
            pass
    assert got == [("line", "Starting the build"), ("event", {"event": "connected", "project": "MyGame"}),
                   ("line", "{not json"), ("exit", 1)]

    slow = tmp_path / "slow.py"
    slow.write_text("import time\nprint('going', flush=True)\ntime.sleep(60)\n")
    r = qa.Replay([sys.executable, str(slow)])
    assert r.events.get(timeout=30) == ("line", "going") and r.running()
    r.stop()
    deadline = time.time() + 30
    last = None
    while time.time() < deadline and last is None:
        try:
            kind, data = r.events.get(timeout=1)
            if kind == "exit":
                last = (kind, data)
        except queue.Empty:
            pass
    assert last == ("exit", None) and not r.running()


def test_the_window(tmp_path, monkeypatch):
    """The window on Tk (skipped where none can open): the build and folder it remembers, the list of the folder's
    regressions, Replay all running the command (a stand-in here) and the rows filling in from its events."""
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError as e:
        pytest.skip(f"no display: {e}")
    root.withdraw()
    regs = str(tmp_path / "Autopilot" / "regressions")
    regression(regs, "r1", "Verify the coin counts")
    regression(regs, "r2", "Verify the door opens")
    build = unity_build(tmp_path)
    qa.save_settings({"build": build, "regressions": regs, "watch": False, "retry": True})
    started = []

    class FakeReplay:
        def __init__(self, cmd):
            started.append(cmd)
            self.events = queue.Queue()
            for item in (events_of_a_test() if "play" in cmd else events_of_a_replay()):
                self.events.put(item)
            self.done = False

        def running(self):
            return not self.done

        def stop(self):
            self.done = True

    monkeypatch.setattr(qa, "Replay", FakeReplay)
    try:
        w = qa.Window(root)
        assert w.build.get() == build and w.folder.get() == regs and w.watch.get() is False
        assert w.build_note.get().startswith("Unity build") and w.folder_note.get().startswith("2 regressions;")
        assert list(w.tree.get_children()) == ["r1", "r2"]
        assert "!disabled" not in w.stop_button.state() and "disabled" not in w.all_button.state()
        w.replay_all()
        assert started[0][3:] == ["regress", "--build", build, "--events", "--retries", "1", "--regressions", regs]
        assert "disabled" in w.all_button.state()
        w.replay.done = True
        w.poll()
        assert [w.tree.set(r, "status") for r in ("r1", "r2")] == ["OK", "FLAKY"]
        assert w.tag_label.cget("text") == "PASSED 2/2" and w.tag_label.winfo_manager() == "pack"
        assert w.status.get() == "2/2 passed, 1 flaky (passed only on a second try)."
        w.tree.selection_set(["r2"])
        w.show_details()
        assert "passed on try 2" in w.details.get("1.0", "end")
        assert "Starting Unreal Engine build" in w.log.get("1.0", "end")
        w.replay_selected()
        assert started[1][-2:] == ["--only", "r2"]
        w.replay.done = True
        w.poll()
        # the TEST page: a Testing run on the same build, its findings as they come
        w.pages("TEST")
        assert w.page == "TEST" and w.status.get() == "Ready." and not w.tag_label.winfo_ismapped()
        w.minutes.set("15")
        w.run_test()
        assert started[2][3:6] == ["play", qa.DEFAULT_PROMPT, "--build"] and "--time-limit" in started[2]
        assert started[2][started[2].index("--time-limit") + 1] == "15" and "--game-folder" in started[2]
        assert "disabled" in w.test_button.state() and "disabled" in w.all_button.state()
        w.replay.done = True
        w.poll()
        assert [w.findings.set(i, "finding") for i in w.findings.get_children()] == [
            "The deer can't be damaged.", "Logs fall out of the world at the north wall."]
        assert w.tag_label.cget("text") == "2 FINDINGS" and w.status.get() == "2 findings, 1 episode, 6 min, $5.49."
        assert "Read Door.cpp" in w.activity_box.get("1.0", "end")
        assert "SUMMARY" in w.finding_box.get("1.0", "end")
        w.findings.selection_set(["1"])
        w.show_finding()
        assert "Finding 2" in w.finding_box.get("1.0", "end")
        w.pages("REPLAY")
        assert w.tag_label.cget("text") == "PASSED 2/2"
        w.build.set(str(tmp_path / "tool.exe"))
        assert "disabled" in w.all_button.state()
        w.close()
        assert qa.load_settings()["build"] == str(tmp_path / "tool.exe")
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass


def test_autopilot_qa_without_a_display_says_how_to_replay_instead(monkeypatch, capsys):
    tk = pytest.importorskip("tkinter")

    def no_display():
        raise tk.TclError("no display name")

    monkeypatch.setattr(tk, "Tk", no_display)
    assert qa.main(types.SimpleNamespace(build=None, regressions=None)) == 1
    assert "autopilot regress --build" in capsys.readouterr().out
