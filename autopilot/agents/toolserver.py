"""The game's tools over localhost, for an agent that runs in another process (OpenCode, Codex). With files=True, the
file tools of autopilot/agents/files.py too (for Codex, whose own are off), held to the game's folder.

POST <url>/<tool> with the arguments as JSON; the answer is the tool's text, plus what the user said meanwhile and the
clock (as Claude Code's tools give). One call at a time. The url carries a secret, so only who was given it can call.
"""

import http.server
import json
import secrets
import threading
import time
import traceback

from .files import FILE_TOOLS
from .session import PROBING, READING, REPORT_FIELDS, WRITING

TOOLS = ("probe", "run_harness", "snapshot", "describe_frames", "restart_level", "report")


class ToolServer:
    """Session's tools (autopilot/agents/session.py) at self.url until close(); files: the file tools too."""

    def __init__(self, session, say=None, files=False):
        self.session = session
        self.say = say or (lambda text: None)
        self.lock = threading.Lock()
        self.files = None
        if files:
            from .files import FileTools
            from .session import PACKAGE

            self.files = FileTools(session.root, session.run_rel, PACKAGE)
        token = secrets.token_urlsafe(24)
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                parts = self.path.strip("/").split("/")
                if len(parts) != 2 or not secrets.compare_digest(parts[0], token):
                    self.send_error(404)
                    return
                try:
                    args = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                except ValueError:
                    args = None
                if not isinstance(args, dict):
                    self.send_error(400)
                    return
                body = server.call(parts[1], args).encode("utf-8")
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except OSError:
                    pass  # (the caller gave up waiting: the agent was interrupted)

            def log_message(self, *a):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/{token}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def call(self, tool, args):
        s = self.session
        tools = {
            "probe": lambda: s.probe(str(args["code"])),
            "run_harness": lambda: s.run_harness(args.get("seconds", 30), args.get("restart", True),
                                                 frames_fps=args.get("frames_fps")),
            "snapshot": s.snapshot,
            "describe_frames": lambda: s.describe_frames(args.get("questions") or [], args.get("episode"),
                                                         args.get("start"), args.get("end"), args.get("frames")),
            "restart_level": lambda: s.restart_level(args.get("level")),
            "report": lambda: s.do_report({k: args[k] for k in REPORT_FIELDS if k in args}),
        }
        if self.files is not None and tool in FILE_TOOLS:
            s.tool_call(tool, args, log=f"{tool}: {json.dumps(args)[:600]}")
            self.say(WRITING if tool in ("write_file", "edit_file") else READING)
            return self.files.call(tool, args)  # (no clock: file tools take no game time)
        if tool not in tools:
            names = list(tools) + (list(FILE_TOOLS) if self.files is not None else [])
            return f"No tool {tool!r}: the tools are {', '.join(names)}."
        with self.lock:
            s.tool_call(tool, args, log=f"tool {tool}: {json.dumps(args)[:600]}")
            if tool in ("probe", "describe_frames"):
                self.say(PROBING)
            t0 = time.time()
            try:
                text = tools[tool]()
            except Exception:
                text = "Tool failed:\n" + traceback.format_exc(limit=5)
            s.tool_seconds += time.time() - t0
            return text + s.steer_note() + "\n" + s.clock()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
