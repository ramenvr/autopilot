"""Helpers for the Godot addon's tests: a brain-side server built on the repo's autopilot.bridge.Link (it also records the
upgrade request's headers, to check the Bearer token), and Godot processes started headless and always stopped.

The test game is tests/game (Orchard Walk, made up for these tests). project() copies it and the addon into a temporary
folder, imports it headless (so its class_name cache exists) and deletes the folder when the test ends.

Environment variables:
    AUTOPILOT_GODOT          the Godot 4.7+ console executable (Godot_v4.x_win64_console.exe on Windows); required
    GODOT_TEST_PROJECT   optional: an already assembled and imported copy of the test game to use as is (run_all.py
                         assembles one and passes it to every test)
"""

import atexit
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from autopilot.bridge import PROTOCOL, Link, is_loopback  # noqa: E402  (the repo's bridge, read only)

GODOT = os.environ.get("AUTOPILOT_GODOT", "")
GAME = os.path.join(HERE, "game")
ADDON = os.path.join(HERE, "..", "addons", "autopilot")
_project = os.path.abspath(os.environ["GODOT_TEST_PROJECT"]) if os.environ.get("GODOT_TEST_PROJECT") else ""


def require_godot():
    if not GODOT or not os.path.isfile(GODOT):
        sys.exit("Can't run the Godot addon's tests: AUTOPILOT_GODOT must be the path of the Godot 4.7+ console "
                 f"executable (now: {GODOT!r})")


def assemble(dest):
    """Copy the test game and the addon into dest (a new folder) and import the project headless."""
    require_godot()
    skip = shutil.ignore_patterns(".godot", "addons", "__pycache__")
    shutil.copytree(GAME, dest, ignore=skip)
    shutil.copytree(ADDON, os.path.join(dest, "addons", "autopilot"))
    env = {k: v for k, v in os.environ.items() if not k.startswith("AUTOPILOT_PLAYTEST_")}  # (no dock autoconnect)
    r = subprocess.run([GODOT, "--headless", "--path", dest, "--import"], capture_output=True, text=True,
                       errors="replace", timeout=300, env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if not os.path.isfile(os.path.join(dest, ".godot", "global_script_class_cache.cfg")):
        sys.exit(f"Importing the test game failed (exit {r.returncode}):\n{r.stdout[-3000:]}")
    return dest


def project():
    """The test game's folder: GODOT_TEST_PROJECT, or a fresh copy assembled for this process (deleted at exit)."""
    global _project
    if not _project:
        dest = os.path.join(tempfile.mkdtemp(prefix="godot_test_game_"), "game")
        atexit.register(shutil.rmtree, os.path.dirname(dest), True)
        print(f"Assembling the test game in {dest} ...", flush=True)
        _project = assemble(dest)
    return _project


def require_setup():
    """Stop with a clear message unless AUTOPILOT_GODOT points at Godot and the test game is there."""
    require_godot()
    p = project()
    if not os.path.isfile(os.path.join(p, "addons", "autopilot", "plugin.cfg")):
        sys.exit(f"Can't run the Godot addon's tests: {p} has no addons/autopilot")


class Brain:
    """Listens like autopilot.bridge.serve_local, keeping each connection's headers. `script(link, hello)` (optional) runs
    on the connection's thread after the welcome (a stub brain's run). `refuse` (an error frame, optional): answer every
    hello with it in place of the welcome and close at once, before the plugin can read it (WebSocketPeer drops a frame
    that arrives in the same poll as the close); `hellos` counts the hellos."""

    def __init__(self, port, role="cli", script=None, refuse=None):
        from websockets.sync.server import serve

        self.links = queue.Queue()
        self.headers = []
        self.hellos = 0
        self.current = None
        self.role = role
        self.script = script
        self.refuse = refuse
        self.server = serve(self._handler, "127.0.0.1", port, max_size=None)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"ws://127.0.0.1:{port}/v1/bridge"

    def _handler(self, ws):
        self.headers.append(dict(ws.request.headers))
        link = Link(lambda frame: ws.send(json.dumps(frame)))
        link.loopback = is_loopback(ws.remote_address)
        link.raw = ws
        try:
            first = json.loads(ws.recv(timeout=20))
        except Exception:
            return
        link.feed(first)
        if first.get("type") != "hello" or first.get("protocol") != PROTOCOL:
            ws.send(json.dumps({"type": "error", "message": "expected hello"}))
            return
        self.hellos += 1
        if self.refuse:
            ws.send(json.dumps(self.refuse))
            ws.close()
            return
        ws.send(json.dumps({"type": "welcome", "role": self.role, "session": f"test-{int(time.time())}"}))
        link.events = []
        link.listeners.append(link.events.append)
        self.current = link
        self.links.put(link)
        if self.script:
            threading.Thread(target=self.script, args=(link, first), daemon=True).start()
        try:
            for message in ws:
                try:
                    link.feed(json.loads(message))
                except ValueError:
                    continue
        except Exception as e:
            link.close(str(e) or "connection lost")
            return
        link.close("the plugin disconnected")

    def wait(self, timeout=60):
        return self.links.get(timeout=timeout)

    def close(self):
        self.server.shutdown()


def start_godot(args, env=None, log=None):
    """Start Godot (always --headless) with args; its output goes to `log` (a path) or is discarded."""
    require_setup()
    full = [GODOT, "--headless"] + list(args)
    out = open(log, "w", encoding="utf-8", errors="replace") if log else subprocess.DEVNULL
    proc = subprocess.Popen(full, stdout=out, stderr=subprocess.STDOUT, env={**os.environ, **(env or {})},
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    proc.log_file = out if log else None
    return proc


def descendants(pid):
    """[(pid, command line)] of every process under pid (one query for all processes, Windows)."""
    cmd = ["powershell", "-NoProfile", "-Command",
           "Get-CimInstance Win32_Process | ForEach-Object { \"$($_.ProcessId)`t$($_.ParentProcessId)`t$($_.CommandLine)\" }"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30).stdout
    except Exception:
        return []
    kids = {}
    for line in out.splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
            kids.setdefault(int(parts[1]), []).append((int(parts[0]), parts[2]))
    found, todo = [], [pid]
    while todo:
        for child in kids.get(todo.pop(), []):
            if child[0] != pid and child not in found:
                found.append(child)
                todo.append(child[0])
    return found


def stop(proc):
    """End a process we started and everything it started."""
    if proc is None:
        return
    if proc.poll() is None:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            pass
    if getattr(proc, "log_file", None):
        proc.log_file.close()


class Checks:
    def __init__(self):
        self.results = []

    def check(self, name, ok, detail=""):
        self.results.append((name, bool(ok), detail))
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""), flush=True)
        return ok

    def summary(self):
        failed = [r for r in self.results if not r[1]]
        print(f"\n{len(self.results) - len(failed)}/{len(self.results)} checks passed")
        for name, _, detail in failed:
            print(f"  FAILED: {name} {detail}")
        return not failed
