"""Workflows: how a run of each kind goes, step by step, as the agent is told it in its briefing.

A workflow is a short playbook for one kind of request (goal, verify, explore): the order of work, what to read
after each episode and what each reading leads to, when to continue play and when to start over, when to report.
The defaults are the package's (autopilot/workflows/<kind>.md); a project's own (<memory>/<project>/workflows/
<kind>.md) replaces the default for that project, and <memory>/<project>/workflows/all.md is added to every kind's:
a studio writes down how its games are to be tested (always check the HUD after a death; the boss fight is the last
step; never use the debug menu in play) once, and every run follows it. The briefing names the file in use, so the
agent knows what to edit.

    text(kind, root)   the workflow for a run of `kind` on the project whose memory folder is root, as the briefing
                       shows it ("" when there is none); a kind that has no file falls back on "goal"
"""

import os

HERE = os.path.dirname(os.path.abspath(__file__))
FOLDER = os.path.join(HERE, "workflows")
KINDS = ("goal", "verify", "explore")
PROJECT_FOLDER = "workflows"  # (in a project's memory folder)
ALL = "all.md"


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def files(kind, root=None):
    """The files a run of `kind` reads its workflow from, in order: the project's own for the kind (else the
    package's), then the project's all.md when there is one. [] when the kind has none."""
    kind = kind if kind in KINDS else "goal"
    out = []
    own = os.path.join(root, PROJECT_FOLDER, f"{kind}.md") if root else None
    if own and os.path.isfile(own):
        out.append(own)
    elif os.path.isfile(os.path.join(FOLDER, f"{kind}.md")):
        out.append(os.path.join(FOLDER, f"{kind}.md"))
    everyone = os.path.join(root, PROJECT_FOLDER, ALL) if root else None
    if everyone and os.path.isfile(everyone):
        out.append(everyone)
    return out


def text(kind, root=None):
    """The workflow for the briefing: its heading, where it comes from (so a project can write its own), and the
    files' text. "" when there is none."""
    paths = files(kind, root)
    if not paths:
        return ""
    parts = []
    for path in paths:
        body = _read(path)
        if not body:
            continue
        own = root and os.path.abspath(path).startswith(os.path.abspath(root))
        where = (f"this game's {PROJECT_FOLDER}/{os.path.basename(path)}" if own else
                 f"Autopilot's default for a {kind}; this game's own goes in {PROJECT_FOLDER}/{os.path.basename(path)}")
        parts.append(f"({where})\n{body}")
    if not parts:
        return ""
    return "## The workflow for this request\nFollow it in order; it says what each tool result leads to.\n\n" + \
        "\n\n".join(parts)
