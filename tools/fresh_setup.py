"""Make this computer look new to Autopilot, to try its first-run setup, and put it back after.

    python tools/fresh_setup.py reset      move the setup's files aside: the plugins show their first-run setup again
    python tools/fresh_setup.py restore    put them back (what a reset moved; files made since are kept aside)
    python tools/fresh_setup.py status     what is there now

The setup's files are in Autopilot/ in the user settings folder (autopilot/bridge.py brain_file): preferences.json (the
setup's choices), autopilot.env (keys and settings for the agent service), aura-session.json (the Aura sign-in),
install.json (this machine's upload id). reset moves them to Autopilot/fresh-setup-backup/<time>/; brain.json
and aura-brain.json (where running agent services are) and memory/ stay. It never uninstalls the `autopilot` command: do
that with `uv tool uninstall autopilot` to try the setup's Install (`autopilot uninstall` removes all of Autopilot).

    python tools/fresh_setup.py reset --aura-server https://<a preview>   also starts autopilot.env with
                                                                          AUTOPILOT_AURA_SERVER, so Aura sign-ins and
                                                                          runs go to that server
"""

import argparse
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from autopilot import bridge  # noqa: E402

FILES = ("preferences.json", "autopilot.env", "aura-session.json", "install.json")
BACKUPS = "fresh-setup-backup"


def folder():
    return os.path.dirname(bridge.brain_file())


def reset(aura_server=None):
    here = folder()
    backup = os.path.join(here, BACKUPS, time.strftime("%Y%m%d-%H%M%S"))
    moved = []
    for name in FILES:
        path = os.path.join(here, name)
        if os.path.exists(path):
            os.makedirs(backup, exist_ok=True)
            shutil.move(path, os.path.join(backup, name))
            moved.append(name)
    print(f"Moved aside to {backup}: {', '.join(moved)}" if moved else "Nothing to move: the setup is fresh already")
    if aura_server:
        os.makedirs(here, exist_ok=True)
        with open(os.path.join(here, "autopilot.env"), "w", encoding="utf-8") as f:
            f.write(f"AUTOPILOT_AURA_SERVER={aura_server.rstrip('/')}\n")
        print(f"autopilot.env: AUTOPILOT_AURA_SERVER={aura_server.rstrip('/')}")
    print("Restart the agent service and reopen the Autopilot tab (or the editor) to see the first-run setup.")


def restore():
    here = folder()
    root = os.path.join(here, BACKUPS)
    backups = sorted(os.listdir(root)) if os.path.isdir(root) else []
    if not backups:
        print("No backup to restore")
        return 1
    latest = os.path.join(root, backups[-1])
    aside = os.path.join(root, f"{backups[-1]}-replaced")
    for name in FILES:  # (what the setup made since the reset: kept aside, never lost)
        path = os.path.join(here, name)
        if os.path.exists(path):
            os.makedirs(aside, exist_ok=True)
            shutil.move(path, os.path.join(aside, name))
    for name in os.listdir(latest):
        shutil.move(os.path.join(latest, name), os.path.join(here, name))
    os.rmdir(latest)
    print(f"Restored from {latest}" + (f"; the files made since are in {aside}" if os.path.isdir(aside) else ""))
    return 0


def status():
    here = folder()
    print(here)
    for name in FILES + ("brain.json", "aura-brain.json"):
        print(f"  {'x' if os.path.exists(os.path.join(here, name)) else ' '} {name}")
    autopilot = shutil.which("autopilot")
    tool = os.path.join(os.path.expanduser("~"), ".local", "bin", "autopilot.exe" if os.name == "nt" else "autopilot")
    print(f"autopilot command: {autopilot or (tool if os.path.exists(tool) else 'not installed')}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Try Autopilot's first-run setup as on a new computer.")
    ap.add_argument("action", choices=("reset", "restore", "status"))
    ap.add_argument("--aura-server", help="reset: start autopilot.env with AUTOPILOT_AURA_SERVER=<this>")
    args = ap.parse_args(argv)
    if args.action == "reset":
        reset(args.aura_server)
        return 0
    return restore() if args.action == "restore" else status()


if __name__ == "__main__":
    sys.exit(main())
