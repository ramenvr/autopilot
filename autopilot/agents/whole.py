"""Nothing an agent or the user is given is cut. What is longer than a real budget (what one tool result holds, what a
part of the briefing quotes, what a plugin's tab shows of a detail) is written whole to a file, and what is given
says so: where all of it is, the lines from the start that fit (whole lines), and from which line to read on.

Every tool result's text goes through here (Session.deliver, the file tools), so a long one reads the same in every
agent: a note first, then the lines that fit.
"""

import os
import re

# The most characters one tool result carries in all: the tool's answer, what the user typed since, the news and the
# clock. Claude Code refuses an MCP tool result over 25,000 tokens (MAX_MCP_OUTPUT_TOKENS), and plain text takes at
# least about a character a token, so a result of this size gets through whatever it holds (a briefing of ~52,000
# characters did not). The agents Autopilot runs itself are held to the same, so a result reads the same in all
RESULT_CHARS = 24000
# ... of which a tool's own answer: the rest is room for what the user typed, the news and the clock beside it
ANSWER_CHARS = RESULT_CHARS - 4000
FOLDER = "whole"  # in a run's folder: what was longer than where it was to be shown, whole


def next_name(folder, stem, ext=".txt"):
    """<stem>-<k><ext>, k one past the highest such file in folder (from 1)."""
    rx = re.compile(rf"{re.escape(stem)}-(\d+){re.escape(ext)}")
    try:
        names = os.listdir(folder)
    except OSError:
        names = []
    return f"{stem}-{1 + max((int(m.group(1)) for m in map(rx.fullmatch, names) if m), default=0)}{ext}"


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def kept(run_dir, run_rel, stem, text, ext=".txt"):
    """Writes text whole to <run>/whole/<stem>-<k><ext>; returns (its full path, its path in the game's folder)."""
    folder = os.path.join(run_dir, FOLDER)
    name = next_name(folder, stem, ext)
    write(os.path.join(folder, name), text)
    return os.path.join(folder, name), f"{run_rel}/{FOLDER}/{name}"


def head(lines, budget):
    """How many of lines, from the first, fit in budget characters joined by newlines."""
    size, n = -1, 0
    for line in lines:
        size += len(line) + 1
        if size > budget:
            break
        n += 1
    return n


def split(text, budget, where, what="This", ui=False, holds=None):
    """(note, shown) for text longer than budget, whose whole is at `where` line for line (a path in the game's folder
    for an agent; for the user (ui), a full path): the note says where all of it is and from which line to read on;
    shown is the lines from the start that fit beside it (note, newline, shown stay within budget), or, when not even
    the first line fits, the part of it that does, the note saying so. ("", text) when text fits. holds: what the
    budget is ("one tool result holds"; for the user "this holds")."""
    text = str(text)
    if len(text) <= budget:
        return "", text
    lines = text.splitlines()
    holds = holds or ("this holds" if ui else "one tool result holds")
    said = f"[{what} is {len(text):,} characters, more than {holds}: all of it ({len(lines)} lines) is in {where}."

    def note(n):
        return said + (f" Lines 1-{n} follow.]" if ui else f" Lines 1-{n} follow; read on from line {n + 1} of that "
                       "file.]")

    n = head(lines, budget - len(note(len(lines))) - 1)
    if n:
        return note(n), "\n".join(lines[:n])
    # Not even the first line fits: the part of it that does
    first = lines[0] if lines else ""
    rest = (f" Its first line is {len(first):,} characters: the first {{k:,}} of them follow" + (".]" if ui else
            "; read on from line 1, column {c:,} of that file.]"))
    k = max(0, budget - len(said + rest.format(k=len(first), c=len(first))) - 1)
    return said + rest.format(k=k, c=k + 1), first[:k]


def deliver(text, budget, where, what="This", ui=False, holds=None):
    """text when it fits budget; else split()'s note and, under it, the lines that fit."""
    said, shown = split(text, budget, where, what, ui, holds)
    return f"{said}\n{shown}" if said and shown else said or shown


def listed(items, budget, latest=False):
    """How many of items fit in budget characters, one a line: from the first, or (latest) from the last."""
    return head(list(reversed(items)) if latest else items, budget)
