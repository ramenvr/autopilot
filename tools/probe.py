"""A hand-held brain for looking into a running game: waits for the plugin to connect, then
forwards bridge requests (docs/protocol.md) sent to a local HTTP endpoint, so they can be made one at a time
from scripts or the shell while the game keeps running.

    python tools/probe.py [--port 8770]            # the plugin connects to ws://127.0.0.1:<port>/v1/bridge
    curl -s localhost:<port+1> -d '{"op": "context"}'
    curl -s localhost:<port+1> -d '{"op": "rc", "args": {...}}'

Start the game with -Playtest=goal -PlaytestStayOpen -PlaytestUrl=ws://127.0.0.1:<port>/v1/bridge.
"""

import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from autopilot import bridge  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8770)
    args = parser.parse_args()
    print(f"Waiting for the plugin on ws://127.0.0.1:{args.port}/v1/bridge ...", flush=True)
    link = bridge.serve_local(port=args.port)
    print("Connected:", json.dumps(link.hello)[:500], flush=True)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            try:
                out = {"ok": True,
                       "result": link.request(body["op"], body.get("args"), timeout=body.get("timeout", 60))}
            except Exception as e:  # report the failure to the caller and keep serving
                out = {"ok": False, "error": f"{type(e).__name__}: {e}"}
            data = json.dumps(out, default=str).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    print(f"Forwarding requests from http://127.0.0.1:{args.port + 1}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", args.port + 1), Handler).serve_forever()


if __name__ == "__main__":
    main()
