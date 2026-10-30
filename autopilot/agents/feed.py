"""The activity feed's words: what one agent tool call is, said for a game developer (docs/protocol.md, `activity`).

Every agent (Claude Code, OpenCode, Codex, the user's own through `autopilot mcp`) reads, searches and writes files
with tools of its own names (Read / read / read_file...), and calls the game's tools (probe, run_harness...). This
turns a tool call into the feed's (kind, text, detail): the kind of row the tab shows, one short line in plain words,
and what opens under it (the path, the code, the questions). What the call came back with is a second event the tool's
body sends (autopilot/agents/session.py, Session.activity).
"""

import json
import os

READ = ("Read", "read", "read_file")
GLOB = ("Glob", "glob", "list_files")
GREP = ("Grep", "grep", "search")
WRITE = ("Write", "write", "write_file")
EDIT = ("Edit", "edit", "edit_file", "patch")
RESEARCH = ("Agent",)  # Claude Code's subagent tool: a researcher's question
# The game tools whose call the feed shows (their result is a second event, ended by the first's id). restart_level
# and report are one event each, sent by their bodies once they have happened; snapshot is a plain log line
CALLS = ("probe", "run_harness", "describe_frames", "frames")


def rel_path(path, root):
    """path as the feed shows it: relative to the game's folder when inside it, with /."""
    if not isinstance(path, str) or not path.strip():
        return ""
    path = path.strip().replace("\\", "/")
    if root:
        full = path if os.path.isabs(path) else os.path.join(root, *path.split("/"))
        try:
            rel = os.path.relpath(os.path.realpath(full), os.path.realpath(root))
        except ValueError:  # (another drive)
            return path
        if not rel.startswith(".."):
            return rel.replace(os.sep, "/")
    return path


def head(text, lines=30):
    """The first `lines` lines of text, cut on a line boundary, saying how many more there were (for a preview;
    an event's detail carries the whole, cut only at the size a tool result holds)."""
    if not isinstance(text, str):
        return text
    parts = text.splitlines()
    if len(parts) <= lines:
        return text
    return "\n".join(parts[:lines]) + f"\n... ({len(parts) - lines} more lines)"


def first_line(text):
    """The first non-empty line of text ("" when none)."""
    for line in str(text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def _args(args):
    if isinstance(args, str):  # (OpenCode's host sends a tool's input as text: JSON cut at 600 characters)
        try:
            args = json.loads(args)
        except ValueError:
            return {}
    return args if isinstance(args, dict) else {}


def describe_tool(name, args, root=None):
    """(kind, text, detail) for a tool call, or (None, None, None) when the feed has no row for it (the call is a
    log line only). name: the tool's name as the agent calls it (Claude Code's `mcp__game__probe` and `Read`,
    OpenCode's `read`, the tool server's `read_file`); args: its arguments; root: the game's folder (paths are shown
    relative to it)."""
    name = str(name or "")
    if name.startswith("mcp__game__"):
        name = name[len("mcp__game__"):]
    a = _args(args)
    path = rel_path(a.get("file_path") or a.get("filePath") or a.get("path"), root)
    pattern = str(a.get("pattern") or "")
    if name in READ:
        return "read", f"Read {path}", path
    if name in GLOB:
        return "search", f"Looked for files matching {pattern}" + (f" in {path}" if path else ""), pattern
    if name in GREP:
        return "search", f"Searched the game's code for {pattern}" + (f" in {path}" if path else ""), pattern
    if name in WRITE:
        return "write", f"Wrote {path}", a.get("content")
    if name in EDIT:
        new = a.get("new_string") if "new_string" in a else a.get("newString") if "newString" in a else a.get("new")
        return "write", f"Edited {path}", new if isinstance(new, str) else None
    if name in RESEARCH:
        question = a.get("prompt") or a.get("description") or ""
        return "search", f"Asked a researcher: {first_line(question)}", str(question) if question else None
    if name == "probe":
        code = str(a.get("code") or "")
        n = len([line for line in code.splitlines() if line.strip()])
        return "probe", f"Tried {n} line{'s' if n != 1 else ''} in the game", code
    if name == "run_harness":
        try:
            seconds = float(a.get("seconds", 30))
        except (TypeError, ValueError):
            seconds = 30.0
        where = "" if a.get("restart", True) in (True, None, "true", "True") else ", going on from where the game is"
        return "episode", f"Playing the harness for {seconds:.0f} s{where}", None
    if name in ("describe_frames", "frames"):
        which = f"episode {a['episode']}" if a.get("episode") else "the last episode"
        questions = a.get("questions")
        if isinstance(questions, str):
            questions = [questions]
        detail = "\n".join(str(q) for q in questions) if isinstance(questions, list) and questions else None
        verb = "Looking at" if name == "describe_frames" else "Taking"
        return "look", f"{verb} {which}'s frames", detail
    return None, None, None
