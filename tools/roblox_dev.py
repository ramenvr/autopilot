"""The Roblox plugin's development loop: Studio runs plugin/roblox/dev/AutopilotDev.server.luau (copied into its Plugins
folder with `install`), and this serves it the plugin's modules (plugin/roblox/src) in every DataModel, again whenever
a file changes, so the plugin restarts in place without reopening the place.

    python tools/roblox_dev.py install            # the dev loader into Studio's Plugins folder (then reopen the place)
    python tools/roblox_dev.py serve              # serve the modules; the DataModels' output goes to the log
    python tools/roblox_dev.py eval [--dm edit] 'return game.Name'   # run Luau in a DataModel (needs `serve`)
    python tools/roblox_dev.py status             # the DataModels connected now

The loader talks plain HTTP to this (long polls on http://127.0.0.1:8797), not a WebSocket: Studio lets a DataModel have
only four, other plugins' counted, and the plugin needs its own. A play session's client can't make HTTP requests (only
a server can), so there it uses a WebSocket (ws://127.0.0.1:8796/dev), of which the client has enough. The log:
AUTOPILOT_ROBLOX_DEV_LOG, else <temp>/autopilot-roblox-dev.log. Never part of a release: the built plugin
(tools/build_roblox.py) carries the same modules.
"""

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "plugin", "roblox", "src")
DEV = os.path.join(ROOT, "plugin", "roblox", "dev")
LOADER = os.path.join(DEV, "AutopilotDev.server.luau")
PORT = 8797
WS_PORT = 8796
POLL_SECONDS = 20.0
LOG = os.environ.get("AUTOPILOT_ROBLOX_DEV_LOG") or os.path.join(tempfile.gettempdir(), "autopilot-roblox-dev.log")

sys.path.insert(0, HERE)
from build_roblox import ENTRY, modules, plugins_folder  # noqa: E402


class Dev:
    """What the dev server holds: the bundle and its version, each DataModel's queue and when it last polled, the
    answers to evals."""

    def __init__(self):
        self.lock = threading.Condition()
        self.queues = {}  # dm -> [messages]
        self.seen = {}  # dm -> time of its last poll
        self.answers = {}  # eval id -> answer
        self.log = open(LOG, "a", encoding="utf-8", buffering=1)
        self.refresh()

    def write(self, line):
        self.log.write(f"{time.strftime('%H:%M:%S')} {line}\n")

    def refresh(self):
        # (the plugin's modules, and the dev relay the edit DataModel runs for the client: plugin/roblox/dev)
        mods = {**modules(), **modules(DEV)}
        self.version = hashlib.sha1(json.dumps(mods, sort_keys=True).encode()).hexdigest()[:12]
        self.bundle = {"type": "bundle", "modules": mods, "entry": ENTRY, "version": self.version}

    def poll(self, dm, have):
        deadline = time.time() + POLL_SECONDS
        with self.lock:
            while True:
                self.seen[dm] = time.time()
                out = []
                if have != self.version:
                    out.append(self.bundle)
                    have = self.version
                out += self.queues.pop(dm, [])
                if out or time.time() >= deadline:
                    return out
                self.lock.wait(max(0.05, min(5.0, deadline - time.time())))

    def up(self, dm, messages):
        for msg in messages:
            kind = msg.get("type")
            if kind == "loaded":
                self.write(f"[{dm}] loaded ok={msg.get('ok')} {msg.get('error') or ''}")
            elif kind == "log":
                self.write(f"[{dm}] {msg.get('kind')}: {msg.get('text')}")
            elif kind == "eval":
                with self.lock:
                    self.answers[msg.get("id")] = msg
                    self.lock.notify_all()

    def eval(self, dm, code, timeout):
        rid = uuid.uuid4().hex
        with self.lock:
            if dm not in self.status():
                return {"ok": False, "error": f"no {dm} DataModel polling ({self.status()})"}
            self.queues.setdefault(dm, []).append({"type": "eval", "id": rid, "code": code})
            self.lock.notify_all()
            deadline = time.time() + timeout
            while rid not in self.answers and time.time() < deadline:
                self.lock.wait(deadline - time.time())
            return self.answers.pop(rid, {"ok": False, "error": "no answer"})

    def pump(self, dm, ws):
        """A client's loader on a WebSocket: what poll() would answer, sent as it comes; what it sends, as up()."""
        have = ""

        def reader():
            try:
                for message in ws:
                    self.up(dm, json.loads(message))
            except Exception:
                pass

        threading.Thread(target=reader, daemon=True).start()
        try:
            while True:
                out = self.poll(dm, have)
                if out:
                    have = self.version
                    ws.send(json.dumps(out))
        except Exception as e:
            self.write(f"[{dm}] socket closed: {e}")

    def status(self):
        now = time.time()
        return sorted(dm for dm, t in self.seen.items() if now - t < 8)

    def watch(self):
        seen = None
        while True:
            stamp = snapshot()
            if seen is not None and stamp != seen:
                self.write("sources changed: reloading")
                with self.lock:
                    self.refresh()
                    self.lock.notify_all()
            seen = stamp
            time.sleep(0.5)


def snapshot():
    """{path: mtime} of the sources (a change sends the bundle again)."""
    out = {}
    for base, _, files in [*os.walk(SRC), *os.walk(DEV)]:
        for f in files:
            p = os.path.join(base, f)
            out[p] = os.path.getmtime(p)
    return out


def handler_for(dev):
    """Native Studio/CLI HTTP requests only; loopback alone does not exclude web pages."""
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _answer(self, body, code=200):
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _query(self):
            url = urlparse(self.path)
            return url.path, {k: v[0] for k, v in parse_qs(url.query).items()}

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"null")

        def do_GET(self):
            if self.headers.get("Origin") is not None:
                return self._answer({"error": "native clients only"}, 403)
            path, q = self._query()
            if path == "/poll":
                # (nobundle: the edit DataModel's relay for the client, which has the bundle already)
                self._answer(dev.poll(q.get("dm", "?"), dev.version if q.get("nobundle") else q.get("have", "")))
            elif path == "/status":
                self._answer({"dms": dev.status()})
            else:
                self._answer({"error": "not found"}, 404)

        def do_POST(self):
            if self.headers.get("Origin") is not None:
                return self._answer({"error": "native clients only"}, 403)
            path, q = self._query()
            if path == "/up":
                dev.up(q.get("dm", "?"), self._body() or [])
                self._answer({})
            elif path == "/ctl":
                if self.headers.get_content_type() != "application/json":
                    return self._answer({"error": "expected application/json"}, 415)
                req = self._body() or {}
                self._answer(dev.eval(req.get("dm") or "edit", req.get("code", ""), float(req.get("timeout", 30))))
            else:
                self._answer({"error": "not found"}, 404)

    return Handler


def serve():
    dev = Dev()
    from websockets.sync.server import serve as ws_serve

    def socket(ws):
        dm = ws.request.path.split("dm=", 1)[-1] if "dm=" in ws.request.path else "client"
        dev.pump(dm, ws)

    sockets = ws_serve(socket, "127.0.0.1", WS_PORT, max_size=None, origins=[None])
    threading.Thread(target=sockets.serve_forever, daemon=True).start()
    threading.Thread(target=dev.watch, daemon=True).start()
    print(f"Serving {SRC} on http://127.0.0.1:{PORT} and ws://127.0.0.1:{WS_PORT}/dev (log: {LOG})", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), handler_for(dev)).serve_forever()


def ctl(req, timeout=40):
    """Ask the dev server: {"type": "status"}, or an eval ({"dm", "code", "timeout"}); its answer."""
    import requests

    if req.get("type") == "status":
        return requests.get(f"http://127.0.0.1:{PORT}/status", timeout=5).json()
    return requests.post(f"http://127.0.0.1:{PORT}/ctl", json=req, timeout=timeout).json()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("install")
    sub.add_parser("serve")
    sub.add_parser("status")
    e = sub.add_parser("eval")
    e.add_argument("code", help="Luau: a function body (plugin and root are its arguments); `-` reads stdin")
    e.add_argument("--dm", default="edit", choices=("edit", "server", "client"))
    e.add_argument("--timeout", type=float, default=30)
    args = ap.parse_args(argv)
    if args.cmd == "install":
        dest = os.path.join(plugins_folder(), "AutopilotDev.lua")
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copyfile(LOADER, dest)
        print(f"Installed the dev loader: {dest} (reopen the place, or restart Studio, to load it)")
        return 0
    if args.cmd == "serve":
        serve()
        return 0
    if args.cmd == "status":
        print(ctl({"type": "status"}))
        return 0
    code = sys.stdin.read() if args.code == "-" else args.code
    answer = ctl({"dm": args.dm, "code": code, "timeout": args.timeout}, timeout=args.timeout + 5)
    print(json.dumps(answer.get("result") if answer.get("ok") else answer, indent=1))
    return 0 if answer.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
