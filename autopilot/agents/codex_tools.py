"""The game's tools (and the file tools) as an MCP server on stdio, for Codex (autopilot/agents/codex.py): Codex starts
it as `<python> -I codex_tools.py <spec file>`, and it passes each call on to the run's ToolServer over localhost
(autopilot/agents/toolserver.py), where the session and the rules are. The spec file (JSON, in the run's own Codex
home): {"url": the ToolServer's url (it carries the secret), "tools": [{"name", "description", "inputSchema"}]}.

Standard library only, and no import of autopilot: it runs as a file, isolated (-I), in Codex's environment.
"""

import json
import sys
import threading
import urllib.error
import urllib.request

PROTOCOL = "2025-06-18"


class Server:
    def __init__(self, spec, out=None):
        self.url = spec["url"]
        self.tools = spec["tools"]
        self.names = {t["name"] for t in self.tools}
        self.out = out or sys.stdout
        self.lock = threading.Lock()

    def send(self, message):
        with self.lock:
            self.out.write(json.dumps(message) + "\n")
            self.out.flush()

    def call(self, name, args):
        """(text, is_error) of a call, passed on to the ToolServer."""
        if name not in self.names:
            return f"No tool {name!r}.", True
        req = urllib.request.Request(f"{self.url}/{name}", data=json.dumps(args or {}).encode("utf-8"),
                                     method="POST", headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=None) as r:  # (an episode may take minutes)
                return r.read().decode("utf-8", "replace"), False
        except (urllib.error.URLError, OSError) as e:
            return f"The game's tools couldn't be reached: {e}", True

    def handle(self, msg):
        method, mid = msg.get("method"), msg.get("id")
        if mid is None:
            return  # (a notification: initialized, cancelled)
        if method == "initialize":
            asked = (msg.get("params") or {}).get("protocolVersion") or PROTOCOL
            self.send({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": asked, "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "autopilot", "version": "1"}}})
        elif method == "tools/list":
            self.send({"jsonrpc": "2.0", "id": mid, "result": {"tools": self.tools}})
        elif method == "tools/call":
            params = msg.get("params") or {}

            def run():
                text, failed = self.call(params.get("name"), params.get("arguments"))
                self.send({"jsonrpc": "2.0", "id": mid,
                           "result": {"content": [{"type": "text", "text": text}], "isError": failed}})

            threading.Thread(target=run, daemon=True).start()
        elif method == "ping":
            self.send({"jsonrpc": "2.0", "id": mid, "result": {}})
        else:
            self.send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"No method {method}"}})

    def serve(self, lines):
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if isinstance(msg, dict):
                self.handle(msg)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    with open(argv[0], encoding="utf-8") as f:
        spec = json.load(f)
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    Server(spec).serve(sys.stdin)  # (ends when Codex closes stdin)


if __name__ == "__main__":
    main()
