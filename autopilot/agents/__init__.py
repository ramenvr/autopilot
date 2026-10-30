"""The agents that research a game and write the harness Jev plays in.

Each agent module has the same entry point, ``implement(game, project, prompt, ...) -> report``:

- ``opencode``: OpenCode, embedded through its SDK (the default)
- ``claude``: Claude Code, through the Claude Agent SDK
- ``codex``: Codex, through OpenAI's Codex SDK (OpenAI only; the ``codex`` extra)

``AUTOPILOT_AGENT`` picks one. What they share (the instructions, the briefing, the game tools) is in ``session``;
OpenCode and Codex, which run in other processes, reach the game tools through ``toolserver``.
"""

import os

DEFAULT = "opencode"
NAMES = ("opencode", "claude", "codex")


def select(name=None):
    """The agent module ``name`` names (default: AUTOPILOT_AGENT, the Coding Agent the plugins' first-run setup saves in
    autopilot.env, else DEFAULT)."""
    name = (name or os.environ.get("AUTOPILOT_AGENT") or DEFAULT).strip().lower()
    if name == "opencode":
        from . import opencode

        return opencode
    if name == "claude":
        from . import claude_code

        return claude_code
    if name == "codex":
        from . import codex

        return codex
    raise SystemExit(f"AUTOPILOT_AGENT={name!r}: use one of {', '.join(NAMES)}")
