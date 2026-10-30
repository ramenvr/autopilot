"""The addon's logic without a game: tests/units.gd (preferences.json and autopilot.env, the Settings page's numbers and file,
the picture's scaling, encoding and message text) and tests/dock_units.gd (the dock's first-run setup and Settings page,
built and driven headless). Godot runs headless with the user settings folder pointed at an empty temporary folder, so
the user's own Autopilot/ files are never read or written.

    .venv\\Scripts\\python.exe plugin\\godot\\tests\\units.py
"""

import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import GODOT, project, require_setup  # noqa: E402

SCRIPTS = ["units.gd", "dock_units.gd"]


def settings_env(folder):
    """The environment that points the user settings folder (LOCALAPPDATA, XDG_CONFIG_HOME, HOME) at folder."""
    return {"LOCALAPPDATA": folder, "XDG_CONFIG_HOME": folder, "HOME": folder, "UNITS_DIR": folder}


def main():
    sys.stdout.reconfigure(errors="replace")  # (the checks' text has characters a piped Windows console can't encode)
    require_setup()
    results = []
    for name in SCRIPTS:
        folder = tempfile.mkdtemp(prefix="godot_units_")  # (each script starts with an empty settings folder)
        try:
            script = os.path.join(os.path.dirname(os.path.abspath(__file__)), name)
            r = subprocess.run([GODOT, "--headless", "--path", project(), "--script", script], capture_output=True, text=True,
                               errors="replace", timeout=120, env={**os.environ, **settings_env(folder)},
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        finally:
            shutil.rmtree(folder, ignore_errors=True)
        out = r.stdout + r.stderr
        lines = [line for line in out.splitlines() if line.strip()]
        print(f"--- {name}")
        print("\n".join(lines))
        errors = [line for line in lines if "SCRIPT ERROR" in line or "Parse Error" in line or line.startswith("ERROR:")]
        ok = r.returncode == 0 and "checks passed" in out and not errors
        print(f"{name}: " + ("PASS" if ok else f"FAIL (exit {r.returncode}{', errors in the output' if errors else ''})"), flush=True)
        results.append(ok)
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
