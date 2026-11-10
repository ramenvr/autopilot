"""Helpers for the Roblox plugin's tests: a brain on autopilot.bridge.serve_local (the plugin connects to it by itself,
at its default address), checks that print PASS / FAIL, and a way into Studio's DataModels for a test's setup (the
dev loader's eval, tools/roblox_dev.py: only when the plugin runs from it).

The tests need Roblox Studio open on the test game: tests/LanternGrove.rbxl (the place, saved), or an empty Baseplate
with tests/testgame.luau run in Studio's command bar (it builds the same game). Also the plugin in Studio (the built Autopilot.rbxmx, or the dev loader), and nothing else on
port 8765 (no `autopilot serve`). Studio needn't be the window in front: the plugin's input reaches the game either way.
For the game's pictures, Studio's MCP server must be on (Assistant, ..., Settings, MCP Servers).
"""

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
for p in (REPO, os.path.join(REPO, "tools")):
    if p not in sys.path:
        sys.path.insert(0, p)

from autopilot import bridge  # noqa: E402  (the repo's bridge, read only)
from autopilot.bridge import BridgeError  # noqa: E402,F401

PORT = int(os.environ.get("AUTOPILOT_ROBLOX_TEST_PORT") or 8765)
RESULTS = []


def brain(wait=60):
    """The plugin's link, once it has connected and said hello (it retries with a backoff of up to 15 s)."""
    print(f"Waiting for the Roblox plugin on ws://127.0.0.1:{PORT}/v1/bridge ...", flush=True)
    link = bridge.serve_local(port=PORT, wait=wait, role="service", announce_as=None, http_port=PORT + 1)
    link.events = []
    link.listeners.append(link.events.append)
    print(f"Connected: {link.hello.get('project')} ({link.hello.get('engine')} {link.hello.get('engine_version')}, "
          f"{link.hello.get('transport') or 'websocket'})")
    return link


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok)))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail and not ok else ""), flush=True)
    return bool(ok)


def expect(name, fn, test=lambda r: True):
    """Run fn(); PASS when it returns without raising and test(result) holds. Returns the result (None on failure)."""
    try:
        r = fn()
    except Exception as e:
        check(name, False, f"{type(e).__name__}: {e}")
        return None
    ok = False
    try:
        ok = test(r)
    except Exception as e:
        check(name, False, f"test raised {type(e).__name__}: {e}; result {str(r)[:400]}")
        return r
    check(name, ok, f"result {str(r)[:600]}")
    return r


def until(fn, seconds=10.0, every=0.2):
    """fn()'s first truthy value within `seconds` (None if none came)."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            v = fn()
            if v:
                return v
        except Exception:
            pass
        time.sleep(every)
    return None


def studio(code, dm="edit", timeout=30):
    """Run Luau in one of Studio's DataModels through the dev loader (a test's setup only); its result."""
    import roblox_dev

    answer = roblox_dev.ctl({"dm": dm, "code": code, "timeout": timeout}, timeout=timeout + 5)
    if not answer.get("ok"):
        raise RuntimeError(f"studio eval in {dm}: {answer.get('error')}")
    return answer.get("result")


def summary():
    failed = [n for n, ok in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed" + (f"; failed: {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0
