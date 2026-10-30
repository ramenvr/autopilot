import subprocess
import sys

from autopilot import processes


def test_kill_ends_a_running_child_and_ignores_an_ended_one():
    running = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], **processes.GROUP)
    processes.kill(running)
    assert running.wait(timeout=10) is not None
    ended = subprocess.Popen([sys.executable, "-c", "pass"], **processes.GROUP)
    ended.wait(timeout=10)
    processes.kill(ended)  # no error


def test_alive_tells_a_running_process_from_an_ended_one():
    import os

    running = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], **processes.GROUP)
    try:
        assert processes.alive(running.pid) and processes.alive(os.getpid())
    finally:
        processes.kill(running)
        running.wait(timeout=10)
    assert not processes.alive(running.pid) and not processes.alive(0)


def test_a_autopilot_started_with_exit_with_ends_when_its_owner_is_killed(tmp_path):
    import os
    import socket
    import time

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    pid_file = tmp_path / "autopilot.pid"
    # The owner starts Autopilot as a host program would, then is killed without warning
    owner = subprocess.Popen([sys.executable, "-c", (
        "import os, subprocess, sys, time; "
        f"p = subprocess.Popen([sys.executable, '-m', 'autopilot', 'serve', '--port', '{port}', "
        "'--exit-with', str(os.getpid())]); "
        f"open(r'{pid_file}', 'w').write(str(p.pid)); time.sleep(120)")],
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))), **processes.GROUP)
    try:
        for _ in range(100):
            if pid_file.exists() and pid_file.read_text():
                break
            time.sleep(0.1)
        autopilot = int(pid_file.read_text())
        time.sleep(1.0)
        assert processes.alive(autopilot)
        owner.kill()  # (only the owner: Autopilot has to notice by itself)
        owner.wait(timeout=10)
        for _ in range(100):
            if not processes.alive(autopilot):
                break
            time.sleep(0.1)
        assert not processes.alive(autopilot)
    finally:
        processes.kill(owner)


def test_keep_responsive_begins_an_activity_on_macos_and_does_nothing_elsewhere():
    processes.keep_responsive()
    processes.keep_responsive()  # (once is enough: a second call changes nothing)
    if sys.platform == "darwin":
        assert processes._activity is not None
    else:
        assert processes._activity is None


def test_exit_with_finishes_what_must_not_be_cut_short_before_ending(tmp_path):
    """An editor closed while the last run's data is being sent: Autopilot ends once it is sent, not before."""
    import os
    import time

    done = tmp_path / "sent.txt"
    started = tmp_path / "started.txt"
    owner = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"], **processes.GROUP)
    script = (
        "import sys, threading, time; sys.path.insert(0, sys.argv[1]); from autopilot import processes; "
        "t = threading.Thread(target=lambda: (time.sleep(4), open(sys.argv[2], 'w').write('sent'))); t.start(); "
        "processes.exit_with(int(sys.argv[3]), every=0.2, finish=t.join); open(sys.argv[4], 'w').write('up'); "
        "time.sleep(120)")
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    autopilot = subprocess.Popen([sys.executable, "-c", script, repo, str(done), str(owner.pid), str(started)],
                                 **processes.GROUP)
    try:
        for _ in range(100):
            if started.exists():
                break
            time.sleep(0.1)
        processes.kill(owner)
        owner.wait(timeout=10)
        time.sleep(1.5)
        assert processes.alive(autopilot.pid) and not done.exists()  # (its owner gone, still sending)
        autopilot.wait(timeout=30)
        assert done.read_text() == "sent"  # (ended only once it was sent)
    finally:
        processes.kill(owner)
        processes.kill(autopilot)


def test_finish_after_run_waits_for_what_the_runs_left_going(monkeypatch):
    import threading
    import time

    from autopilot import cli

    monkeypatch.setattr(cli, "AFTER_RUN", [])
    said = []
    started = time.time()
    cli.finish_after_run(say=said.append)
    assert said == [] and time.time() - started < 0.5  # (nothing going: at once)
    sending = threading.Thread(target=time.sleep, args=(1.0,))
    sending.start()
    cli.AFTER_RUN.append(sending)
    cli.finish_after_run(say=said.append)
    assert not sending.is_alive() and len(said) == 1
    monkeypatch.setattr(cli, "AFTER_RUN_SECONDS", 0.3)
    stuck = threading.Thread(target=time.sleep, args=(3.0,), daemon=True)
    stuck.start()
    cli.AFTER_RUN.append(stuck)
    started = time.time()
    cli.finish_after_run(say=said.append)
    assert time.time() - started < 1.5  # (up to AFTER_RUN_SECONDS, not for ever)
