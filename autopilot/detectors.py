"""A project's settings for the game's own detectors (docs/protocol.md, Findings), kept in
``memory/<project>/detectors.json``:

    {"mute": ["out_of_world:BP_Tree", "hitch"], "max_distance": 5000000}

- ``mute``: detector names (``out_of_world``: all of that detector's findings) or signatures (``out_of_world:BP_Tree``:
  one of them) the plugin never reports. The editor's "Not a bug" button on a finding adds its signature here.
- ``enabled``: false turns the game's detectors off for the project. The editor's Bug detectors switch (the Autopilot
  tab's Settings) sets it: a `detectors` message from the plugin, and memory_state's ``detectors`` says how it is.
- any other key the plugins' ``detectors`` op takes, as a number or true/false (``max_distance``, ``kill_y``,
  ``teleport_min``, ``hitch_seconds``...): sent as written; a key a plugin doesn't know is ignored by it.

The file is the user's, not something runs learned: Reset memory leaves it (memory.LEARNED doesn't name it). Every run
sends it to the game once the game is up (cli.prepare), and again whenever the plugin comes back from a reload.
"""

import json
import os
import sys

from . import memory

FILE = "detectors.json"


def path(folder):
    """The project's detectors.json (folder: its memory folder)."""
    return os.path.join(folder, FILE)


def is_muted(finding, muted):
    """Whether a finding is one the mute list names: its detector, its signature, or, for a teleport right after the
    agent's input ("teleport:<kind> after <key>", told as info), that kind's teleports ("teleport:<kind>"), as every
    plugin drops them."""
    signature, detector = finding.get("signature"), finding.get("detector")
    if signature in muted or detector in muted:
        return True
    return detector == "teleport" and " after " in str(signature) and str(signature).rsplit(" after ", 1)[0] in muted


def _names(value):
    """The mute list as given, cleaned: strings only, stripped, empty ones and repeats dropped, in order."""
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(s.strip() for s in value if isinstance(s, str) and s.strip()))


def load(folder):
    """What detectors.json says ({} when there is none, or it can't be read: a note says why)."""
    try:
        with open(path(folder), encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        print(f"note: {path(folder)} can't be read, the detectors run as they are: {e}", file=sys.stderr)
        return {}
    if not isinstance(data, dict):
        print(f"note: {path(folder)} isn't a JSON object, the detectors run as they are", file=sys.stderr)
        return {}
    return data


def config(folder):
    """The `detectors` op's arguments for the project: its mute list (always: an emptied list un-mutes what was muted)
    and the other settings that are numbers or true/false."""
    data = load(folder)
    out = {k: v for k, v in data.items()
           if k != "mute" and isinstance(k, str) and isinstance(v, (bool, int, float))}
    out["mute"] = _names(data.get("mute"))
    return out


def mute(folder, signature):
    """Add a finding's signature (or a detector's name) to the project's mute list; whether it was added (False: it
    was there already, or is empty). The file's other keys are kept as they are."""
    signature = str(signature or "").strip()
    if not signature:
        return False
    data = load(folder)
    names = _names(data.get("mute"))
    if signature in names:
        return False
    data["mute"] = names + [signature]
    # (in one step, a temporary file of its own: another agent service on this memory folder, or a memory sync, never
    # reads half of it nor shares the temporary file; memory.write_atomic)
    memory.write_atomic(path(folder), json.dumps(data, indent=2) + "\n")
    return True


def enabled(folder):
    """Whether the game's detectors run for the project (detectors.json's "enabled"; on unless it says false): what the
    editor's Bug detectors switch shows."""
    return load(folder).get("enabled") is not False


def set_enabled(folder, on):
    """The editor's Bug detectors switch: detectors.json's "enabled" set to `on`, its other keys kept as they are."""
    data = load(folder)
    data["enabled"] = bool(on)
    memory.write_atomic(path(folder), json.dumps(data, indent=2) + "\n")


def send(link, folder, wait=True):
    """Tell the game's detectors the project's settings (the `detectors` op); the arguments sent, or None when the game
    didn't take them (a plugin without the op: its findings are still filtered here, Link.muted). wait=False posts
    them without waiting for the answer (from a thread that must not block on the game)."""
    args = config(folder)
    target = getattr(link, "current", link)  # (cli.LiveLink: the link as it is now)
    target.muted = frozenset(args["mute"])
    try:
        if wait:
            link.request("detectors", args, timeout=15)
        else:
            link.post("detectors", args)
    except Exception as e:  # noqa: BLE001 - the run goes on with the plugin's defaults
        print(f"note: the game's detectors didn't take the project's settings: {e}", file=sys.stderr)
        return None
    return args


def watch(link, folder):
    """Send the settings again each time the plugin comes back from a reload (bridge.Link.rebind; a Unity domain
    reload starts its detectors afresh). One watcher per link, following the project of the latest run."""
    target = getattr(link, "current", link)
    rebound = getattr(target, "rebound", None)
    if not isinstance(rebound, list):
        return
    for fn in rebound:
        if getattr(fn, "detectors_folder", None) is not None:
            fn.detectors_folder = folder
            return

    def again():
        send(target, again.detectors_folder, wait=False)
    again.detectors_folder = folder
    rebound.append(again)
