"""Autopilot: automated playtesting for Unreal, Unity and Godot.

An agent researches a game's code and writes a harness; Jev, a fast decision model, plays inside it. See
docs/architecture.md.
"""

__version__ = "0.1.0"


def build():
    """The commit this Autopilot was built from: build.json, which tools/package_release.py writes into the wheels it
    builds (the downloads' agent-service.json names the same commit). "" for a checkout or another build."""
    import json
    import os

    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "build.json"), encoding="utf-8") as f:
            return str(json.load(f).get("commit") or "")
    except (OSError, ValueError, AttributeError):
        return ""
