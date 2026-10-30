"""A stand-in for `autopilot serve --port 0 --exit-with PID`, for AgentServiceLiveTests: it says where it "listens" in
brain.json in the folder AUTOPILOT_TEST_FOLDER names (never the user's), with its own process id as autopilot/bridge.py
does, prints a line for the log, and waits (up to a minute, or until the test ends it).

    python fake_autopilot.py serve --port 0 --exit-with 1234
"""

import json
import os
import socket
import sys
import time


def main():
    args = sys.argv[1:]
    assert args[:3] == ["serve", "--port", "0"] and args[3] == "--exit-with" and int(args[4]) > 0, args
    folder = os.environ["AUTOPILOT_TEST_FOLDER"]
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    os.makedirs(folder, exist_ok=True)
    tmp = os.path.join(folder, f"brain.json.{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"url": f"ws://127.0.0.1:{port}/v1/bridge", "pid": os.getpid(), "started": time.time()}, f)
    os.replace(tmp, os.path.join(folder, "brain.json"))
    print(f"fake agent service on port {port}, ending with {args[4]} ✓ ready")
    time.sleep(60)


if __name__ == "__main__":
    main()
