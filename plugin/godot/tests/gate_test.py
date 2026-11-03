"""The detectors work only while a run uses the game: the test game logs an error twice a second (GODOT_TEST_ERRORS), and
what the addon sends of it is counted.

    .venv\\Scripts\\python.exe plugin\\godot\\tests\\gate_test.py [--port 18970]

Connected with no run nothing goes out, a request turns them on for half a minute, a run's result turns them off, and
through the editor's dock they stay on while a run goes though nothing is asked (the agent thinking), because the dock
tells the game (the `run` message), and go off with its result.
"""

import argparse
import json
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import Brain, Checks, project, start_godot, stop  # noqa: E402

RUN_USE_SECONDS = 30  # playtest.gd's
ENV = {"GODOT_TEST_ERRORS": "1"}


def logs(link):
    return sum(1 for e in list(link.events) if e.get("kind") == "log" and "planted error" in str(e.get("message")))


def count(link, seconds):
    """How many of the planted errors the addon sends in the next `seconds`."""
    before = logs(link)
    time.sleep(seconds)
    return logs(link) - before


def direct(c, port):
    """No dock: the game's own connection to a brain that hasn't said a run is going."""
    brain = Brain(port, role="service")
    game = start_godot(["--path", project(), "--", f"--playtest-url={brain.url}"], env=ENV,
                       log=os.path.join(tempfile.gettempdir(), "godot_gate_direct.log"))
    try:
        link = brain.wait(60)
        time.sleep(3)  # (the game settles)
        n = count(link, 5)
        c.check("connected with no run: no log events", n == 0, str(n))
        link.request("ping")
        n = count(link, 5)
        c.check("a request turns them on", n >= 5, str(n))
        time.sleep(RUN_USE_SECONDS - 5 + 2)
        n = count(link, 5)
        c.check("half a minute after the last request they are off again", n == 0, str(n))
    finally:
        stop(game)
        brain.close()


def command_line(c, port):
    """A run asked on the command line: on from the first frame, off with its result (with the game staying open)."""
    brain = Brain(port, role="service")
    report = tempfile.mkdtemp(prefix="godot_gate_")
    game = start_godot(["--path", project(), "--", f"--playtest-url={brain.url}", "--playtest=explore", "--playtest-stay-open",
                        f"--playtest-report-dir={report}"], env=ENV,
                       log=os.path.join(tempfile.gettempdir(), "godot_gate_cli.log"))
    try:
        link = brain.wait(60)
        time.sleep(3)
        n = count(link, 5)
        c.check("a command-line run: log events with nothing asked", n >= 5, str(n))
        link.send({"type": "result", "result": {"kind": "explore", "summary": "stub"}})
        time.sleep(1)
        n = count(link, 5)
        c.check("its result turns them off", n == 0, str(n))
    finally:
        stop(game)
        brain.close()
        shutil.rmtree(report, ignore_errors=True)


def editor(c, port):
    """Through the dock: the game hears of the run from the editor."""
    brain = Brain(port, role="service")
    settings = tempfile.mkdtemp(prefix="godot_gate_settings_")
    os.makedirs(os.path.join(settings, "Autopilot"))
    with open(os.path.join(settings, "Autopilot", "preferences.json"), "w", encoding="utf-8") as f:
        json.dump({"version": 1, "onboarded": False, "share_data": False, "research_agent": False}, f)
    ed = start_godot(["--editor", "--path", project()],
                     env={**ENV, "AUTOPILOT_PLAYTEST_URL": brain.url, "AUTOPILOT_PLAYTEST_AUTOCONNECT": "1",
                          "LOCALAPPDATA": settings, "XDG_CONFIG_HOME": settings, "APPDATA": settings, "XDG_DATA_HOME": settings},
                     log=os.path.join(tempfile.gettempdir(), "godot_gate_editor.log"))
    try:
        link = brain.wait(120)
        link.request("session", {"action": "start"}, timeout=90)
        link.send({"type": "run_started", "run": {"kind": "explore", "prompt": "gate test", "by": "gate_test"}})
        time.sleep(RUN_USE_SECONDS + 3)  # (the session's start no longer counts: only the run keeps them on)
        n = count(link, 5)
        c.check("through the dock: on while a run goes with nothing asked", n >= 5, str(n))
        link.send({"type": "result", "result": {"kind": "explore", "summary": "stub"}})
        time.sleep(1)
        n = count(link, 5)
        c.check("through the dock: its result turns them off", n == 0, str(n))
    finally:
        stop(ed)
        brain.close()
        shutil.rmtree(settings, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=18970)
    a = ap.parse_args()
    c = Checks()
    for i, case in enumerate((direct, command_line, editor)):
        try:
            case(c, a.port + i)
        except Exception as e:
            import traceback

            traceback.print_exc()
            c.check(f"{case.__name__}: no exception", False, repr(e))
    sys.exit(0 if c.summary() else 1)


if __name__ == "__main__":
    main()
