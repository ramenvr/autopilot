"""Every live test of the Unity package, one after another, on one test game made for them:

    python plugin/unity/com.ramenvr.autopilot/Tests~/run_all.py [--keep] [--only smoke-player,kit ...]

It makes the test game (testgame.py: a throwaway Unity project and a Development player, built headless with
AUTOPILOT_UNITY), runs smoke.py ci, player and editor, kit_live.py and sonar_live.py against it (one Unity process at a
time), prints a summary and deletes the game (--keep keeps it). Exit code 0 when every test passed.
"""

import argparse
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import testgame  # noqa: E402


def tests(game):
    smoke = os.path.join(HERE, "smoke.py")
    return [
        ("smoke-ci", [smoke, "ci", "--exe", game.exe, "--project", game.project]),
        ("smoke-player", [smoke, "player", "--exe", game.exe]),
        ("smoke-editor", [smoke, "editor", "--project", game.project]),
        ("kit", [os.path.join(HERE, "kit_live.py"), "--exe", game.exe]),
        ("sonar", [os.path.join(HERE, "sonar_live.py"), "--exe", game.exe]),
    ]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="keep the test game")
    ap.add_argument("--dir", help="make the test game here (default: a new temporary folder)")
    ap.add_argument("--only", help="comma-separated: smoke-ci, smoke-player, smoke-editor, kit, sonar")
    args = ap.parse_args()
    only = set(args.only.split(",")) if args.only else None
    started = time.time()
    game = testgame.make(args.dir)
    built = time.time() - started
    results = []
    try:
        for name, cmd in tests(game):
            if only and name not in only:
                continue
            print(f"\n=== {name} ===", flush=True)
            t = time.time()
            p = subprocess.run([sys.executable, *cmd], capture_output=True, text=True, encoding="utf-8", errors="replace")
            lines = p.stdout.splitlines()
            summary = next((line for line in reversed(lines) if "checks passed" in line), "no summary")
            for line in lines:
                if line.startswith("[FAIL]") or line.startswith("  FAILED") or "Traceback" in line:
                    print(line)
            if p.returncode != 0 and p.stderr.strip():
                print(p.stderr.strip()[-3000:])
            print(f"{summary.strip()} ({time.time() - t:.0f}s)", flush=True)
            results.append((name, p.returncode == 0, summary.strip(), time.time() - t))
    finally:
        if args.keep:
            print(f"\nkept the test game: {game.root}")
        else:
            game.remove()
    print(f"\ntest game made in {built:.0f}s")
    for name, ok, summary, secs in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {name:13} {summary}  ({secs:.0f}s)")
    print(f"total {time.time() - started:.0f}s")
    sys.exit(0 if results and all(ok for _, ok, _, _ in results) else 1)


if __name__ == "__main__":
    main()
