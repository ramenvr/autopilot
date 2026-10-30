"""Run every test of the Godot addon, one at a time, on one fresh copy of the test game (tests/game with the addon,
assembled and imported headless in a temporary folder, deleted at the end). Needs only AUTOPILOT_GODOT.

    .venv\\Scripts\\python.exe plugin\\godot\\tests\\run_all.py [units smoke ci_test kit_live editor_test]

Exit code 0 when every script passes.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import assemble  # noqa: E402

SCRIPTS = ["units", "smoke", "ci_test", "kit_live", "editor_test"]


def main():
    names = sys.argv[1:] or SCRIPTS
    unknown = [n for n in names if n not in SCRIPTS]
    if unknown:
        sys.exit(f"unknown test(s) {unknown}; choose from {SCRIPTS}")
    root = tempfile.mkdtemp(prefix="godot_test_game_")
    results = []
    try:
        t0 = time.monotonic()
        print(f"Assembling the test game in {root} ...", flush=True)
        project = assemble(os.path.join(root, "game"))
        print(f"  ({time.monotonic() - t0:.0f}s)\n", flush=True)
        env = {**os.environ, "GODOT_TEST_PROJECT": project}
        for name in names:
            print(f"=== {name}", flush=True)
            t0 = time.monotonic()
            code = subprocess.call([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), name + ".py")], env=env)
            results.append((name, code, time.monotonic() - t0))
            print(flush=True)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print("=== summary")
    for name, code, took in results:
        print(f"{'PASS' if code == 0 else 'FAIL'}  {name}  ({took:.0f}s)")
    sys.exit(0 if results and all(code == 0 for _, code, _ in results) else 1)


if __name__ == "__main__":
    main()
