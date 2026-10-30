"""What Autopilot remembers about a game, and forgetting it (autopilot/memory.py)."""

import gc
import os
import sys
import threading
import types

import pytest

from autopilot import cli, memory
from autopilot.agents import session as sessionmod


def test_reset_moves_what_runs_learned_aside_and_keeps_the_codex(tmp_path):
    game = tmp_path / "memory" / "Game"
    for rel in ("notes.md", "lib.py", "runs/r1/report.json", "runs/r2/harness.py", "codex/README.md",
                "project.json"):
        (game / rel).parent.mkdir(parents=True, exist_ok=True)
        (game / rel).write_text("x")
    assert memory.state(str(game)) == {"runs": 2, "notes": True, "lib": True}
    assert memory.forget(str(game)) == 4
    assert memory.state(str(game)) == {"runs": 0, "notes": False, "lib": False}
    assert (game / "codex" / "README.md").exists() and (game / "project.json").exists()
    kept = list((tmp_path / "memory" / memory.RESET_BACKUP / "Game").iterdir())
    assert len(kept) == 1 and (kept[0] / "runs" / "r2" / "harness.py").exists() and (kept[0] / "notes.md").exists()
    assert (game / memory.RESET_MARK).exists()  # (for the memory kept with Aura)
    assert memory.files(str(game)).keys() >= {memory.RESET_MARK}
    assert memory.forget(str(game)) == 0  # (nothing left to reset)


def test_a_missing_folder_is_empty(tmp_path):
    assert memory.state(str(tmp_path / "none")) == {"runs": 0, "notes": False, "lib": False}
    assert memory.forget(str(tmp_path / "none")) == 0


def learned(game):
    for rel in ("notes.md", "lib.py", "runs/r1/implementer.log", "runs/r1/telemetry/media/recording-1.mp4"):
        (game / rel).parent.mkdir(parents=True, exist_ok=True)
        (game / rel).write_text("x")
    return sorted(memory.files(str(game)))


def test_a_reset_that_cant_move_everything_moves_nothing(tmp_path, monkeypatch):
    # runs/ can't move (on Windows: a file in it is open): notes.md and lib.py, moved first, are put back, and no
    # reset is noted for the memory kept with Aura
    game = tmp_path / "memory" / "Game"
    before = learned(game)
    rename = os.rename

    def busy(src, dst):
        if os.path.basename(src) == "runs" and os.path.dirname(src) == str(game):
            raise PermissionError(32, "The process cannot access the file because it is being used by another process")
        rename(src, dst)

    monkeypatch.setattr(memory.os, "rename", busy)
    with pytest.raises(memory.InUse) as e:
        memory.forget(str(game))
    assert e.value.path == "runs"
    assert sorted(memory.files(str(game))) == before and not (game / memory.RESET_MARK).exists()
    assert list((tmp_path / "memory" / memory.RESET_BACKUP / "Game").iterdir()) == []


@pytest.mark.skipif(sys.platform != "win32", reason="only Windows keeps a folder with an open file from moving")
def test_a_file_open_in_a_run_leaves_the_memory_as_it_was(tmp_path):
    game = tmp_path / "memory" / "Game"
    before = learned(game)
    with open(game / "runs" / "r1" / "implementer.log", "rb"):  # (as an upload reading it)
        with pytest.raises(memory.InUse):
            memory.forget(str(game))
        assert sorted(memory.files(str(game))) == before and not (game / memory.RESET_MARK).exists()
    assert memory.forget(str(game)) == 4
    assert memory.state(str(game)) == {"runs": 0, "notes": False, "lib": False}


def test_two_resets_in_one_second_keep_both(tmp_path, monkeypatch):
    game = tmp_path / "memory" / "Game"
    monkeypatch.setattr(memory.time, "strftime", lambda fmt: "20261020-154732")
    learned(game)
    assert memory.forget(str(game)) == 4
    learned(game)
    assert memory.forget(str(game)) == 4
    kept = tmp_path / "memory" / memory.RESET_BACKUP / "Game"
    assert sorted(p.name for p in kept.iterdir()) == ["20261020-154732", "20261020-154732-1"]


def test_a_finished_run_leaves_no_file_open(tmp_path, monkeypatch):
    # A run's Session refers to itself (its stop), so nothing frees it until a garbage collection: its log must not
    # be held open meanwhile, or a Reset memory after the run can't move runs/ on Windows
    monkeypatch.setattr(sessionmod.codexmod, "MEMORY", str(tmp_path))
    monkeypatch.setenv("AUTOPILOT_SANDBOX", "off")
    session = sessionmod.open_session(types.SimpleNamespace(link=None, writes=[]), "Game", "Explore", "r1")
    session.note("played")
    session.note("reported")
    root = session.root
    del session
    gc.disable()
    try:
        assert memory.forget(root) == 1
    finally:
        gc.enable()
    log = tmp_path / memory.RESET_BACKUP / "Game"
    assert [p.read_text(encoding="utf-8").count("\n") for p in log.glob("*/runs/r1/implementer.log")] == [2]


class Plugin:
    """Enough of a link for the reset: what it was sent."""

    def __init__(self, messages=()):
        self.hello = {"project": "Game"}
        self.sent = []
        self.messages = list(messages)
        self.current = self

    def send(self, frame):
        self.sent.append(frame)

    def status(self, text):
        self.sent.append({"type": "status", "text": text})

    def next_message(self, timeout=None):
        if not self.messages:
            raise KeyboardInterrupt  # (the end of the test)
        return self.messages.pop(0)


@pytest.fixture
def game(tmp_path, monkeypatch):
    monkeypatch.setattr(cli.codexmod, "MEMORY", str(tmp_path))
    monkeypatch.setattr(cli, "AFTER_RUN", [])
    learned(tmp_path / "Game")
    return tmp_path / "Game"


def test_a_reset_waits_for_what_the_last_run_left_going(game):
    # (the run's memory being saved with Aura, its data being sent: they read the run's files)
    reading = threading.Event()

    def upload():
        with open(game / "runs" / "r1" / "implementer.log", "rb"):
            reading.wait(0.5)

    sending = threading.Thread(target=upload)
    sending.start()
    cli.AFTER_RUN.append(sending)
    link = Plugin()
    assert cli.reset_memory(link, sleep=lambda s: None) == {"type": "memory_cleared", "ok": True, "files": 4}
    assert not sending.is_alive() and link.sent[0]["text"] == "Resetting memory…"


def test_a_reset_that_stays_blocked_says_so_and_moves_nothing(game, monkeypatch):
    tries = []

    def busy(folder):
        tries.append(folder)
        raise memory.InUse("runs", PermissionError(32, "in use"))

    monkeypatch.setattr(cli.memory, "forget", busy)
    answer = cli.reset_memory(Plugin(), sleep=lambda s: None)
    assert len(tries) == cli.RESET_TRIES and answer["ok"] is False and "(runs)" in answer["message"]
    # it gets through once the file is free
    free_after = len(tries) + 2
    monkeypatch.setattr(cli.memory, "forget", lambda folder: 3 if len(tries) >= free_after else busy(folder))
    assert cli.reset_memory(Plugin(), sleep=lambda s: None) == {"type": "memory_cleared", "ok": True, "files": 3}


def test_the_service_answers_a_reset_that_failed_and_goes_on(game, monkeypatch):
    # (before: memory.forget's error ended the service's loop, and the plugin waited on "Resetting memory…")
    monkeypatch.setattr(cli.memory, "forget", lambda folder: (_ for _ in ()).throw(OSError("disk gone")))
    monkeypatch.setattr(cli.account, "send_state", lambda *a, **k: None)
    monkeypatch.setattr(cli.account, "SignIn", lambda link: None)
    link = Plugin([{"type": "clear_memory"}, {"type": "clear_memory"}])
    with pytest.raises(KeyboardInterrupt):
        cli.cmd_serve(link, types.SimpleNamespace(path=None, model=None))
    answers = [f for f in link.sent if f["type"] == "memory_cleared"]
    assert len(answers) == 2 and not answers[0]["ok"] and "disk gone" in answers[0]["message"]
    assert [f for f in link.sent if f["type"] == "memory_state"][-1]["runs"] == 1  # (as it was)


def test_a_session_that_goes_on_keeps_the_runs_episodes_and_reads_them_first(tmp_path, monkeypatch):
    monkeypatch.setattr(sessionmod.codexmod, "MEMORY", str(tmp_path))
    monkeypatch.setenv("AUTOPILOT_SANDBOX", "off")
    monkeypatch.setattr(sessionmod.Session, "opening", lambda self: "(the game)")
    monkeypatch.setattr(sessionmod, "briefing", lambda session, opening: "(the briefing)")
    game = types.SimpleNamespace(link=None, writes=[])
    first = sessionmod.open_session(game, "Game", "Open the gate", "r1")
    assert not first.going_on and first.episodes == 0
    assert sessionmod.GOING_ON.split(":")[0] not in sessionmod.first_message(first)
    sessionmod.go_on(first.run_dir, {"episodes": 2, "summary": "No report (...)"})
    later = sessionmod.open_session(game, "Game", "Open the gate", "r1")
    assert later.going_on and later.episodes == 2  # (its next episode is the run's third: harness3.py)
    assert not os.path.exists(os.path.join(later.run_dir, sessionmod.GOING_ON_FILE))  # (once)
    message = sessionmod.first_message(later)
    assert "already under way" in message and "runs/r1/" in message and "implementer.log" in message
    assert "credit" not in message.lower() and "deepseek" not in message.lower()
    assert sessionmod.cut_off({"summary": "No report (the agent ended without one). Last words: ..."})
    assert not sessionmod.cut_off({"summary": "No report (stopped)", "stopped": "stopped"})
    assert not sessionmod.cut_off({"summary": "The gate opened.", "outcome": "achieved"})


def test_the_relays_spending_limit_words_are_logged_never_shown(tmp_path, monkeypatch):
    monkeypatch.setattr(sessionmod.codexmod, "MEMORY", str(tmp_path))
    monkeypatch.setenv("AUTOPILOT_SANDBOX", "off")
    shown = []
    session = sessionmod.open_session(types.SimpleNamespace(link=None, writes=[]), "Game", "Open the gate", "r1",
                                      activity=lambda frame: shown.append(frame))
    # (what Claude Code says on the relay's 402, as it said it against a stand-in relay)
    session.said("API Error: 402 This run has reached its spending limit (the credits it could use, or the most one "
                 "run may spend). Its result so far stands; start a new run to go on.")
    session.said("The gate is open.")
    assert [f.get("text") for f in shown if f.get("kind") == "text"] == ["The gate is open."]
    with open(session.log, encoding="utf-8") as f:
        assert "spending limit" in f.read()
