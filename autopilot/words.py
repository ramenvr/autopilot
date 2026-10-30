"""What the user reads, in the plugins' words (no architecture words in anything a user sees)."""

import json
import re


def plain_words(text):
    """Text for the user, in the plugin's words: the player is "the agent" (never Jev). (A harness is called a
    harness.) The agent is asked to write so; this catches what it didn't."""
    if not isinstance(text, str):
        return text
    text = re.sub(r"\bJev(?:'s|’s)", "the agent's", text)
    text = re.sub(r"\bJev-", "agent-", text)
    text = re.sub(r"\bJev\b", "the agent", text)
    text = re.sub(r"\b[Tt]he the agent", "the agent", text)
    # A sentence, line or list item that starts with "the agent" gets a capital
    return re.sub(r"(^|[.!?]\s+|\n[ \t]*(?:[-*•]\s+)?)the agent", lambda m: m.group(1) + "The agent", text)


def change_text(change):
    """One direct change to the game (Game.writes) as the plugins list it: "Set Wolf_2.bHidden to true (it blocks
    the agent's path)", "Called Wolf_3.K2_DestroyActor"."""
    name = change.get("name") or change.get("object")
    if "call" in change:
        params = change.get("parameters") or {}
        text = f"Called {name}.{change['call']}"
        if params:
            text += f" with {json.dumps(params, default=str)}"
    else:
        text = f"Set {name}.{change.get('property')} to {json.dumps(change.get('value'), default=str)}"
    if len(text) > 200:
        text = text[:197] + "..."
    if change.get("why"):
        text += f" ({plain_words(change['why'])})"
    return text
