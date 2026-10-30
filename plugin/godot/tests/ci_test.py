"""The CI path end to end: the test game started headless with a run on its command line connects to a stub brain,
which answers a few requests and sends the result; the game writes the report files and exits with the run's code.

    .venv\\Scripts\\python.exe plugin\\godot\\tests\\ci_test.py [--port 18952]
"""

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(__file__))
from harness import Brain, Checks, project, start_godot, stop  # noqa: E402

from autopilot.bridge import BridgeError  # noqa: E402


def stub(verdict=None, error=None, seen=None, close=None):
    """A brain that plays nothing: reads the context, then reports. error: send an error frame instead (the brain
    turning the run down). close: "after_result" closes the socket the moment the result is sent (a brain that doesn't
    wait for the game to close first), "no_result" closes without one."""

    def script(link, hello):
        seen["hello"] = hello
        seen["hellos"] = seen.get("hellos", 0) + 1
        if close == "no_result":
            link.request("context")
            link.raw.close()
            return
        if error:
            link.send({"type": "error", "message": error, "code": 403})
            return
        seen["context"] = link.request("context")
        seen["screen"] = link.request("screen")
        link.status("Now: reading the menu")
        result = {"kind": hello.get("run", {}).get("kind"), "summary": "stub"}
        if verdict:
            result["verdict"] = verdict
        files = {"report.json": json.dumps(result), "junit.xml": "<testsuite/>",
                 "../escape.txt": "must stay in the report folder"}
        link.send({"type": "result", "result": result, "files": files})
        if close == "after_result":
            link.raw.close()

    return script


def case(c, port, name, args, verdict=None, error=None, want_code=0, env=None, close=None):
    seen = {}
    brain = Brain(port, role="service", script=stub(verdict, error, seen, close))
    report = tempfile.mkdtemp(prefix="godot_ci_")
    log = os.path.join(tempfile.gettempdir(), "godot_ci_" + re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_") + ".log")
    game = start_godot(["--path", project(), "--"] + [a.format(url=brain.url, report=report) for a in args], env=env, log=log)
    try:
        code = game.wait(90)
    except Exception:
        code = None
    finally:
        stop(game)
        brain.close()
    run = seen.get("hello", {}).get("run", {})
    h = brain.headers[-1] if brain.headers else {}
    seen["auth"] = h.get("Authorization") or h.get("authorization")
    c.check(f"{name}: exit code {want_code}", code == want_code, f"got {code}")
    c.check(f"{name}: hello carries the run", run.get("kind") in ("verify", "explore", "goal"), str(run))
    c.check(f"{name}: the run was asked once (one hello)", seen.get("hellos") == 1, str(seen.get("hellos")))
    if close or error:
        result = read_result(report)
        c.check(f"{name}: result.json has the error", bool(result.get("error")) and result.get("exit_code") == 1, str(result))
    else:
        c.check(f"{name}: brain read the game", seen.get("context", {}).get("world") == "/root/Menu")
        files = sorted(os.listdir(report))
        c.check(f"{name}: report files written, nothing outside", files == ["escape.txt", "junit.xml", "report.json", "result.json"]
                and not os.path.exists(os.path.join(os.path.dirname(report), "escape.txt")), str(files))
    shutil.rmtree(report, ignore_errors=True)
    return run, seen


def refused_at_once(c, port):
    """A brain that refuses with an error frame and closes at once: both arrive in one poll and Godot's WebSocketPeer
    drops the frame, so the game, never welcomed, would connect again every second or so for as long as it ran. A
    connection closed without a welcome is a refusal: the game exits 1 and asks once."""
    brain = Brain(port, role="service", refuse={"type": "error", "message": "Invalid token", "code": 401})
    log = os.path.join(tempfile.gettempdir(), "godot_ci_refused_at_once.log")
    report = tempfile.mkdtemp(prefix="godot_ci_")
    t0 = time.monotonic()
    game = start_godot(["--path", project(), "--", "--playtest=verify", "--playtest-claim=x", f"--playtest-url={brain.url}",
                        "--playtest-token=bad", f"--playtest-report-dir={report}"], log=log)
    try:
        code = game.wait(90)
    except Exception:
        code = None
    finally:
        stop(game)
        brain.close()
    took = time.monotonic() - t0
    c.check("refused and closed at once: exit code 1", code == 1, f"got {code}")
    c.check("refused and closed at once: asked once, quit at once", brain.hellos == 1 and took < 30,
            f"{brain.hellos} hello(s), {took:.0f}s")
    result = read_result(report, remove=True)
    c.check("refused and closed at once: result.json has the error", "without taking the run" in result.get("error", ""), str(result))


def connect_timeout(c, port):
    """Nothing listens: the game waits -PlaytestConnectTimeout seconds for a welcome (Unreal-style spelling here), then
    writes result.json with the error and exits 1."""
    report = tempfile.mkdtemp(prefix="godot_ci_")
    log = os.path.join(tempfile.gettempdir(), "godot_ci_connect_timeout.log")
    t0 = time.monotonic()
    game = start_godot(["--path", project(), "--", "-Playtest=verify", "-PlaytestClaim=x", f"-PlaytestUrl=ws://127.0.0.1:{port}/v1/bridge",
                        "-PlaytestConnectTimeout=3", f"-PlaytestReportDir={report}"], log=log)
    try:
        code = game.wait(90)
    except Exception:
        code = None
    finally:
        stop(game)
    took = time.monotonic() - t0
    result = read_result(report, remove=True)
    c.check("connect timeout: exit code 1 after the timeout", code == 1 and 3 <= took < 30, f"got {code} after {took:.0f}s")
    c.check("connect timeout: result.json has the error", "no welcome" in result.get("error", "") and "within 3 s" in result["error"]
            and result.get("kind") == "verify", str(result))


USAGE = {"type": "usage", "final": False, "models": [
    {"kind": "llm", "model": "test-llm", "calls": 2, "input": 300, "output": 40, "cache_read": 1000, "cache_write": 10},
    {"kind": "decision", "model": "test-decision", "calls": 50, "input": 5000, "output": 50}]}


def media_and_share(c, port, name, args, prefs, want_share):
    """A headless game: hello says `share` (preferences.json in a user settings folder of its own, -PlaytestShare
    over it) and no "media" (nothing is drawn headless); record and capture answer 409, record stop succeeds, and
    usage frames are taken without trouble."""
    seen = {}

    def script(link, hello):
        seen["hello"] = hello
        for op, a in [("record", {"action": "start", "fps": 5, "max_dim": 960, "quality": 75}), ("capture", {"max_dim": 1920})]:
            try:
                seen[op] = link.request(op, a, timeout=10)
            except BridgeError as e:
                seen[op] = (e.status, e.error)
            except Exception as e:  # noqa: BLE001
                seen[op] = repr(e)
        try:
            seen["stop"] = link.request("record", {"action": "stop"}, timeout=10)
            link.send(USAGE)
            link.send({**USAGE, "final": True})
            seen["ping"] = link.request("ping", timeout=10)
        except Exception as e:  # noqa: BLE001
            seen["error"] = repr(e)
        link.send({"type": "result", "result": {"kind": "verify", "verdict": "CONFIRMED"}, "files": {}})

    folder = tempfile.mkdtemp(prefix="godot_ci_settings_")
    if prefs is not None:
        os.makedirs(os.path.join(folder, "Autopilot"))
        with open(os.path.join(folder, "Autopilot", "preferences.json"), "w", encoding="utf-8") as f:
            json.dump(prefs, f)
    brain = Brain(port, role="service", script=script)
    report = tempfile.mkdtemp(prefix="godot_ci_")
    log = os.path.join(tempfile.gettempdir(), "godot_ci_" + re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_") + ".log")
    env = {"LOCALAPPDATA": folder, "XDG_CONFIG_HOME": folder}
    game = start_godot(["--path", project(), "--", "--playtest=verify", "--playtest-claim=x", f"--playtest-url={brain.url}",
                        f"--playtest-report-dir={report}"] + args, env=env, log=log)
    try:
        code = game.wait(90)
    except Exception:
        code = None
    finally:
        stop(game)
        brain.close()
        shutil.rmtree(report, ignore_errors=True)
        shutil.rmtree(folder, ignore_errors=True)
    hello = seen.get("hello", {})
    c.check(f"{name}: exit code 0", code == 0, f"got {code}")
    c.check(f"{name}: hello share {want_share}", hello.get("share") is want_share, str(hello.get("share")))
    if prefs is None:
        c.check("headless hello: features without media", "media" not in hello.get("features", []) and "timed_hold" in hello.get("features", []),
                str(hello.get("features")))
        for op in ("record", "capture"):
            r = seen.get(op)
            c.check(f"headless {op}: 409 no picture", isinstance(r, tuple) and r[0] == 409 and r[1].startswith("no picture: "), str(r))
        c.check("headless record stop succeeds", seen.get("stop") == {"recording": False, "frames": 0}, str(seen.get("stop")))
        c.check("usage frames taken (the game answers after them)", isinstance(seen.get("ping"), dict) and "error" not in seen,
                str(seen.get("error") or seen.get("ping")))


def read_result(report, remove=False):
    """The report folder's result.json ({} if none); remove: delete the folder after."""
    try:
        with open(os.path.join(report, "result.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}
    finally:
        if remove:
            shutil.rmtree(report, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=18952)
    a = ap.parse_args()
    c = Checks()
    t0 = time.monotonic()
    run, seen = case(c, a.port, "verify DENIED (Godot-style args)",
                     ["--playtest=verify", "--playtest-claim=Verify that pulling the lever raises the gate",
                      "--playtest-url={url}", "--playtest-report-dir={report}", "--playtest-token=ci-token"],
                     verdict="DENIED", want_code=2)
    c.check("claim passed through", run.get("claim") == "Verify that pulling the lever raises the gate", str(run))
    c.check("hello host game", seen.get("hello", {}).get("host") == "game")
    c.check("token from --playtest-token", seen.get("auth") == "Bearer ci-token", str(seen.get("auth")))
    run, seen = case(c, a.port + 1, "goal CONFIRMED (Unreal-style args)",
                     ["-Playtest=goal", "-PlaytestGoal=Open the orchard gate", "-PlaytestSteps=50", "-PlaytestUrl={url}",
                      "-PlaytestReportDir={report}"], verdict="CONFIRMED", want_code=0, env={"AUTOPILOT_PLAYTEST_TOKEN": ""})
    c.check("goal and steps passed through", run.get("goal") == "Open the orchard gate" and run.get("steps") == 50, str(run))
    c.check("no token: no Authorization header", seen.get("auth") is None, str(seen.get("auth")))
    case(c, a.port + 2, "verify INCONCLUSIVE", ["--playtest=verify", "--playtest-claim=x", "--playtest-url={url}",
                                                 "--playtest-report-dir={report}"], verdict="INCONCLUSIVE", want_code=3)
    run, seen = case(c, a.port + 3, "explore minutes, token from the environment",
                  ["--playtest=explore", "--playtest-minutes=2", "--playtest-url={url}", "--playtest-report-dir={report}"],
                  verdict=None, want_code=0, env={"AUTOPILOT_PLAYTEST_TOKEN": "env-token"})
    c.check("minutes passed through", run.get("minutes") == 2, str(run))
    c.check("token from AUTOPILOT_PLAYTEST_TOKEN", seen.get("auth") == "Bearer env-token", str(seen.get("auth")))
    # Godot's WebSocketPeer drops what is buffered when a close completes in the same poll: a brain that closes the
    # moment it sends the result costs the game the result (`autopilot serve` and `autopilot play` let the game close first).
    # The run must still never be asked again on a new connection
    case(c, a.port + 5, "result and an immediate close: no second run", ["--playtest=verify", "--playtest-claim=x",
         "--playtest-url={url}", "--playtest-report-dir={report}"], verdict="DENIED", want_code=1, close="after_result")
    case(c, a.port + 6, "the brain closes before a result: no second run", ["--playtest=verify", "--playtest-claim=x",
         "--playtest-url={url}", "--playtest-report-dir={report}"], want_code=1, close="no_result")
    case(c, a.port + 4, "an error frame from the brain", ["--playtest=verify", "--playtest-claim=x", "--playtest-url={url}",
         "--playtest-report-dir={report}"], error="This brain doesn't take verify runs", want_code=1)
    refused_at_once(c, a.port + 7)
    connect_timeout(c, a.port + 8)
    media_and_share(c, a.port + 9, "no preferences.json", [], None, True)
    media_and_share(c, a.port + 10, "share_data false", [], {"version": 1, "onboarded": True, "share_data": False}, False)
    media_and_share(c, a.port + 11, "-PlaytestShare=1 over share_data false", ["-PlaytestShare=1"],
                    {"version": 1, "share_data": False}, True)
    print(f"({time.monotonic() - t0:.0f}s)")
    sys.exit(0 if c.summary() else 1)


if __name__ == "__main__":
    main()
