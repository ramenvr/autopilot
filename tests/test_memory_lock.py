"""One memory folder used by more than one agent service at once (autopilot/memory.py): files written whole in one
step, and a project's lock that syncs and resets hold, across threads and processes, with stale locks taken over."""

import json
import os
import socket
import subprocess
import sys
import textwrap
import threading
import time

import pytest

from autopilot import aura_memory, cli, memory
from autopilot.agents import files as filesmod

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def leftovers(folder):
    return [n for n in os.listdir(folder) if n.endswith(memory.TEMP_SUFFIX)]


# -- writing whole --------------------------------------------------------------------------------------------------

def test_write_atomic_writes_text_and_bytes_whole_and_leaves_no_temporary_file(tmp_path):
    path = str(tmp_path / "game" / "notes.md")
    memory.write_atomic(path, "line one\nline two\n")
    assert open(path, "rb").read() == b"line one\nline two\n"  # (as given: no newline translation)
    memory.write_atomic(path, b"\x00\x01")
    assert open(path, "rb").read() == b"\x00\x01"
    assert leftovers(tmp_path / "game") == []


def test_a_write_that_fails_leaves_the_old_file_and_no_temporary_file(tmp_path, monkeypatch):
    path = str(tmp_path / "notes.md")
    memory.write_atomic(path, "old\n")

    def refuse(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(memory.os, "replace", refuse)
    with pytest.raises(OSError):
        memory.write_atomic(path, "new\n")
    assert open(path, encoding="utf-8").read() == "old\n" and leftovers(tmp_path) == []


def test_a_rename_refused_for_a_moment_is_tried_again(tmp_path, monkeypatch):
    """(Windows refuses a rename over a file another process is reading this moment.)"""
    path = str(tmp_path / "lib.py")
    real, refused = os.replace, []

    def busy_twice(src, dst):
        if len(refused) < 2:
            refused.append(dst)
            raise PermissionError(13, "in use")
        return real(src, dst)

    monkeypatch.setattr(memory.os, "replace", busy_twice)
    memory.write_atomic(path, "x = 1\n", sleep=lambda s: None)
    assert len(refused) == 2 and open(path, encoding="utf-8").read() == "x = 1\n"


def test_readers_never_see_half_a_file_while_others_write_it(tmp_path):
    path = str(tmp_path / "notes.md")
    versions = [("%d " % i) * 20000 for i in range(8)]  # (big enough that a plain write is seen half done)
    memory.write_atomic(path, versions[0])
    stop, seen, errors = threading.Event(), set(), []

    def writer(k):
        try:
            for n in range(25):
                memory.write_atomic(path, versions[(k + n) % len(versions)])
        except Exception as e:  # noqa: BLE001 - reported below
            errors.append(e)

    def reader():
        while not stop.is_set():
            try:
                with open(path, encoding="utf-8") as f:
                    seen.add(f.read())
            except PermissionError:  # (Windows: the moment of a rename)
                pass
            time.sleep(0.001)

    r = threading.Thread(target=reader)
    r.start()
    writers = [threading.Thread(target=writer, args=(k,)) for k in range(3)]
    for w in writers:
        w.start()
    for w in writers:
        w.join()
    stop.set()
    r.join()
    assert not errors and seen and seen <= set(versions)
    assert leftovers(tmp_path) == []


def test_an_agents_write_file_is_written_in_one_step(tmp_path, monkeypatch):
    """notes.md and lib.py written through the file tools (agents with no write tool of their own)."""
    game = tmp_path / "Farm"
    (game / "runs" / "r1").mkdir(parents=True)
    tools = filesmod.FileTools(str(game), "runs/r1", os.path.join(REPO, "autopilot"))
    calls = []
    real = memory.write_atomic
    monkeypatch.setattr(memory, "write_atomic", lambda path, data, **k: calls.append(path) or real(path, data, **k))
    tools.write_file("notes.md", "- jump is Space\n")
    tools.edit_file("notes.md", "Space", "Space (held: higher)")
    assert open(game / "notes.md", encoding="utf-8").read() == "- jump is Space (held: higher)\n"
    assert len(calls) == 2 and leftovers(game) == []


def test_the_lock_and_temporary_files_are_never_memory(tmp_path):
    game = tmp_path / "Farm"
    game.mkdir()
    (game / "notes.md").write_text("n")
    (game / memory.LOCK).write_text("{}")
    (game / (".notes.md.abc" + memory.TEMP_SUFFIX)).write_text("half")
    assert list(memory.files(str(game))) == ["notes.md"]
    assert not aura_memory.is_synced(memory.LOCK)


# -- each run its own folder ----------------------------------------------------------------------------------------

def test_runs_that_start_in_the_same_second_get_folders_of_their_own(tmp_path):
    """Two agent services on one memory folder whose runs start in the same second: before, both wrote runs/<second>/
    (one's harness.py replaced the other's, their traces interleaved)."""
    now = time.mktime((2026, 11, 10, 17, 24, 22, 0, 0, -1))
    game = str(tmp_path / "Farm")
    assert memory.new_run(game, now) == "20261110-172422"
    assert memory.new_run(game, now) == "20261110-172422-2"
    # (a folder of that name already there, as one from another machine through a memory sync)
    os.makedirs(os.path.join(game, "runs", "20261110-172423"))
    assert memory.new_run(game, now + 1) == "20261110-172423-2"
    # many at once, from threads: each its own
    got, start = [], threading.Barrier(8)

    def one():
        start.wait()
        got.append(memory.new_run(game, now + 5))

    threads = [threading.Thread(target=one) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(got) == sorted(["20261110-172427"] + [f"20261110-172427-{n}" for n in range(2, 9)])
    assert all(os.path.isdir(os.path.join(game, "runs", g)) for g in got)


# -- the project's lock ---------------------------------------------------------------------------------------------

def test_the_lock_is_held_for_the_block_and_gone_after_it_even_on_an_error(tmp_path):
    game = str(tmp_path / "Farm")
    with memory.locked(game, "saving the memory"):
        said = json.load(open(os.path.join(game, memory.LOCK), encoding="utf-8"))
        assert said["pid"] == os.getpid() and said["what"] == "saving the memory"
    assert not os.path.exists(os.path.join(game, memory.LOCK))
    with pytest.raises(ValueError):
        with memory.locked(game):
            raise ValueError("boom")
    assert not os.path.exists(os.path.join(game, memory.LOCK))


def test_a_second_holder_in_the_same_process_waits_for_the_first(tmp_path):
    """(One agent service saves a run's memory while its next run fetches it.)"""
    game = str(tmp_path / "Farm")
    order, inside = [], threading.Event()

    def second():
        inside.wait()
        with memory.locked(game, wait=5):
            order.append("second")

    t = threading.Thread(target=second)
    t.start()
    with memory.locked(game):
        inside.set()
        time.sleep(0.5)
        order.append("first")
    t.join()
    assert order == ["first", "second"]


def test_one_that_waits_too_long_is_told_who_holds_it(tmp_path):
    game = str(tmp_path / "Farm")
    with memory.locked(game, "saving the memory"):
        with pytest.raises(memory.Busy) as busy:
            with memory.locked(game, wait=0.3):
                pass
    assert busy.value.holder["what"] == "saving the memory" and "Farm" in str(busy.value)


def write_lock(game, **said):
    os.makedirs(game, exist_ok=True)
    path = os.path.join(game, memory.LOCK)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(said, f)
    return path


def test_a_lock_whose_process_ended_is_taken_over(tmp_path):
    game = str(tmp_path / "Farm")
    write_lock(game, pid=999999, host=socket.gethostname(), id="gone")
    with memory.locked(game, wait=0.5, alive=lambda pid: False):
        assert json.load(open(os.path.join(game, memory.LOCK), encoding="utf-8"))["pid"] == os.getpid()


def test_a_lock_held_on_another_machine_or_being_written_waits_until_it_is_old(tmp_path, monkeypatch):
    game = str(tmp_path / "Farm")
    path = write_lock(game, pid=1, host="another-machine", id="theirs")
    with pytest.raises(memory.Busy):
        with memory.locked(game, wait=0.3, alive=lambda pid: False):
            pass
    with open(path, "w", encoding="utf-8"):
        pass  # (empty: its holder is writing it this moment)
    with pytest.raises(memory.Busy):
        with memory.locked(game, wait=0.3, alive=lambda pid: False):
            pass
    old = time.time() - memory.LOCK_STALE_SECONDS - 5
    os.utime(path, (old, old))
    with memory.locked(game, wait=0.3, alive=lambda pid: True):
        pass
    assert not os.path.exists(path)


def test_a_lock_another_holder_took_over_is_not_removed_by_the_one_it_replaced(tmp_path):
    game = str(tmp_path / "Farm")
    with memory.locked(game, wait=0.3):
        write_lock(game, pid=os.getpid(), host=socket.gethostname(), id="someone-else")
    assert json.load(open(os.path.join(game, memory.LOCK), encoding="utf-8"))["id"] == "someone-else"


HOLD = textwrap.dedent("""
    import sys, time
    sys.path.insert(0, sys.argv[1])
    from autopilot import memory
    with memory.locked(sys.argv[2], "saving the memory"):
        print("held", flush=True)
        time.sleep(float(sys.argv[3]))
""")


def test_the_lock_holds_across_processes_and_a_killed_holders_lock_is_taken_over(tmp_path):
    """(Two agent services on one memory folder.)"""
    game = str(tmp_path / "Farm")
    other = subprocess.Popen([sys.executable, "-c", HOLD, REPO, game, "1.5"], stdout=subprocess.PIPE, text=True)
    try:
        assert other.stdout.readline().strip() == "held"
        with pytest.raises(memory.Busy) as busy:
            with memory.locked(game, wait=0.2):
                pass
        # (not other.pid itself on Windows, where the venv's python.exe is a launcher that starts the real Python)
        assert busy.value.holder["pid"] != os.getpid() and busy.value.holder["what"] == "saving the memory"
        started = time.monotonic()
        with memory.locked(game, wait=10):  # (waits for the other to finish)
            assert time.monotonic() - started > 0.5
    finally:
        other.wait(10)
    killed = subprocess.Popen([sys.executable, "-c", HOLD, REPO, game, "60"], stdout=subprocess.PIPE, text=True)
    try:
        assert killed.stdout.readline().strip() == "held"
        killed.kill()
        killed.wait(10)
        with memory.locked(game, wait=5):  # (its process is gone: the lock it left is taken over)
            pass
    finally:
        if killed.poll() is None:
            killed.kill()


def test_a_reset_while_a_sync_holds_the_lock_moves_nothing_and_says_why(tmp_path, monkeypatch):
    game = str(tmp_path / "memory" / "Farm")
    os.makedirs(os.path.join(game, "runs", "r1"))
    open(os.path.join(game, "notes.md"), "w").close()
    with memory.locked(game, "saving the memory"):
        with pytest.raises(memory.InUse) as e:
            memory.forget(game, wait=0.2)
        assert e.value.path == memory.LOCK and os.path.exists(os.path.join(game, "notes.md"))

        monkeypatch.setattr(cli.codexmod, "project_dir", lambda project: game)
        monkeypatch.setattr(cli.codexmod, "project_of", lambda hello: "Farm")
        real = memory.forget
        monkeypatch.setattr(cli.memory, "forget", lambda folder: real(folder, wait=0.05))
        link = type("Link", (), {"hello": {"project": "Farm"}, "status": lambda self, text: None})()
        answer = cli.reset_memory(link, sleep=lambda s: None)
        assert answer["ok"] is False and "another run is saving" in answer["message"]
    assert memory.forget(game) == 1 and not os.path.exists(os.path.join(game, "notes.md"))  # (once it is free)
