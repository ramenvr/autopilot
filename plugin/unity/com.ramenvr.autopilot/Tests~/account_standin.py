"""The account and Aura sign-in frames (docs/protocol.md, Account) between this package's PlaytestClient and a stand-in
agent service: a websockets server that answers as autopilot/account.py does (no Aura, no browser), while a batch-mode
editor runs the EditMode test AccountLiveTests against it.

    python plugin/unity/com.ramenvr.autopilot/Tests~/account_standin.py --unity <Unity.exe> --project <a project using the package>

The project needs the package as testable ("testables": ["com.ramenvr.autopilot"] in Packages/manifest.json). Nothing is
started but the stand-in and the batch-mode editor; the user's Autopilot folder isn't touched.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time

from websockets.sync.server import serve

EMAIL = "dev@studio.test"
URL = "https://aura.test/auth/cli?code=AB12"


def standin(received):
    state = {"signed_in": False}

    def account():
        aura = state["signed_in"] or state.get("was")
        return {"type": "account", "mode": "aura" if aura else "none", "email": EMAIL if aura else None,
                "signed_in": state["signed_in"], "keys": {"decision": None, "llm": "••••3c2e"},
                "coding_agent": "codex", "llm_provider": "openai"}

    def handler(ws):
        cancel = threading.Event()
        for text in ws:
            frame = json.loads(text)
            kind = frame.get("type")
            if kind != "events":
                received.append(kind)
            if kind == "hello":
                ws.send(json.dumps({"type": "welcome", "role": "service", "session": "s1", "features": ["account"]}))
                ws.send(json.dumps(account()))
            elif kind == "account":
                ws.send(json.dumps(account()))
            elif kind == "aura_sign_in":
                cancel.clear()
                ws.send(json.dumps({"type": "aura_sign_in", "state": "waiting", "url": URL}))

                def finish():
                    time.sleep(1.0)
                    if cancel.is_set():
                        ws.send(json.dumps({"type": "aura_sign_in", "state": "error", "reason": "cancelled",
                                            "message": "Sign-in cancelled"}))
                        return
                    state["signed_in"] = state["was"] = True
                    ws.send(json.dumps({"type": "aura_sign_in", "state": "signed_in", "email": EMAIL}))
                    ws.send(json.dumps(account()))

                threading.Thread(target=finish, daemon=True).start()
            elif kind == "aura_sign_in_cancel":
                cancel.set()
            elif kind == "aura_sign_out":
                state["signed_in"] = False
                ws.send(json.dumps(account()))

    return handler


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--unity", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--results", default=os.path.join(tempfile.gettempdir(), "autopilot-account-live.xml"))
    args = ap.parse_args()

    received = []
    server = serve(standin(received), "127.0.0.1", 0)
    port = server.socket.getsockname()[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"ws://127.0.0.1:{port}/v1/bridge"
    print(f"stand-in on {url}")

    log = args.results.replace(".xml", ".log")
    env = dict(os.environ, RG_STANDIN_URL=url)
    code = subprocess.call([args.unity, "-batchmode", "-nographics", "-projectPath", args.project, "-runTests",
                            "-testPlatform", "EditMode", "-testFilter", "Autopilot.Playtest.Tests.AccountLiveTests",
                            "-testResults", args.results, "-logFile", log], env=env)
    server.shutdown()
    print("frames received:", [k for k in received if k != "events"])
    text = open(args.results, encoding="utf-8").read() if os.path.exists(args.results) else ""
    m = re.search(r'<test-run [^>]*total="(\d+)" passed="(\d+)" failed="(\d+)"[^>]*skipped="(\d+)"', text)
    print("unity exit", code, "results", m.groups() if m else None)
    want = ["hello", "aura_sign_in", "aura_sign_out", "aura_sign_in", "aura_sign_in_cancel", "account"]
    ok = code == 0 and m and m.group(1) == m.group(2) != "0" and all(k in received for k in want)
    print("OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
