"""What the user reads, in the plugins' words (no architecture words in anything a user sees)."""

import json
import re


def plain_words(text):
    """Text for the user, in the plugin's words: the player is "the agent" (never Jev). (A harness is called a
    harness.) The agent is asked to write so; this catches what it didn't."""
    if not isinstance(text, str):
        return text
    text = re.sub(r"\bJev (decisions?|choices?)\b", r"\1", text)  # ("7 Jev decisions": 7 decisions)
    text = re.sub(r"\bJev(?:'s|’s)", "the agent's", text)
    text = re.sub(r"\bJev-", "agent-", text)
    text = re.sub(r"\bJev\b", "the agent", text)
    text = re.sub(r"\b[Tt]he the agent", "the agent", text)
    # A sentence, line or list item that starts with "the agent" gets a capital
    return re.sub(r"(^|[.!?]\s+|\n[ \t]*(?:[-*•]\s+)?)the agent", lambda m: m.group(1) + "The agent", text)


REPORT_TEXTS = ("summary", "evidence", "findings")  # a report's fields in the agent's own words


def report_words(report):
    """A report (an agent's report, or report.json) with its summary, evidence and findings in plain_words: what it
    is shown as everywhere (the plugins, `autopilot play`'s output, report.json, a regression, a later briefing)."""
    out = dict(report)
    for key in REPORT_TEXTS:
        value = out.get(key)
        if isinstance(value, list):
            out[key] = [plain_words(v) for v in value]
        elif isinstance(value, str):
            out[key] = plain_words(value)
    return out


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
    if change.get("why"):
        text += f" ({plain_words(change['why'])})"
    return text
