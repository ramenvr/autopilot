"""The user's own agent's conversation during a run (autopilot/agents/external.py), as its client keeps it on this
computer, kept with a shared run's data (telemetry/transcript.jsonl; agent/transcript.jsonl.gz in the dataset,
docs/run-data.md). Its model calls never pass through Autopilot, so this is what there is of them.

    Claude Code   <CLAUDE_CONFIG_DIR, else ~/.claude>/projects/<project>/<session>.jsonl, and its subagents'
                  <session>/subagents/*.jsonl
    Codex         <CODEX_HOME, else ~/.codex>/sessions/<yyyy>/<mm>/<dd>/rollout-*.jsonl (a subagent has its own)

The run's files are those with its answers in them, by its marker ("<run id>-<nonce>"): the main conversation has the
start call's ("Autopilot run <marker> started on"), a describer subagent's a `frames` one ("(run <marker>)"); a file
that only mentions the marker isn't the run's. The main conversation is kept from the start_run call on (what came
before it in that session is the user's other work), a subagent's whole. Pictures' data is left out (they are in the
run's recording), as in the run's other call logs. Secrets, names and paths are taken out when the run is sent
(autopilot/dataset.py).
"""

import json
import os
import re

FILE = "transcript.jsonl"
DATA_URL = re.compile(r"data:image/[\w.+-]+;base64,[A-Za-z0-9+/=]*")
MAX_BYTES = 64 * 1024 * 1024  # a client's file past this is left alone
BACK_LINES = 40  # how far before the marker the start_run call is looked for
IMAGE = "[image: its data left out]"


def roots():
    """[(the folder a client keeps its conversations in, the client)]."""
    home = os.path.expanduser("~")
    claude = (os.environ.get("CLAUDE_CONFIG_DIR") or "").strip() or os.path.join(home, ".claude")
    codex = (os.environ.get("CODEX_HOME") or "").strip() or os.path.join(home, ".codex")
    return [(os.path.join(claude, "projects"), "claude-code"), (os.path.join(codex, "sessions"), "codex")]


def candidates(since):
    """(path, client) of every conversation file written to since (Unix seconds)."""
    for root, client in roots():
        for folder, _, names in os.walk(root):
            for name in names:
                path = os.path.join(folder, name)
                try:
                    if name.endswith(".jsonl") and os.path.getmtime(path) >= since - 60 and \
                            os.path.getsize(path) <= MAX_BYTES:
                        yield path, client
                except OSError:
                    continue


def no_pictures(value):
    """value with every picture's data replaced by IMAGE (base64 in a content part, or a data: URL, also inside a
    longer text)."""
    if isinstance(value, str):
        return DATA_URL.sub(IMAGE, value) if "data:image/" in value else value
    if isinstance(value, list):
        return [no_pictures(v) for v in value]
    if not isinstance(value, dict):
        return value
    out = {}
    image = str(value.get("type") or "") in ("image", "input_image", "image_url")
    for k, v in value.items():
        if image and k == "data" and isinstance(v, str):
            out[k] = IMAGE
        elif image and k == "source" and isinstance(v, dict) and "data" in v:
            out[k] = {**v, "data": IMAGE}
        else:
            out[k] = no_pictures(v)
    return out


def started(marker):
    """What the start call's answer says (autopilot/agents/external.py START): only the main conversation has it."""
    return f"Autopilot run {marker} started on"


def looked(marker):
    """What every `frames` answer ends with: a describer subagent's conversation has it."""
    return f"(run {marker})"


def _cut(lines, marker):
    """The lines of the main conversation from its start_run call on (the call comes just before its answer, which
    names the marker; a client may name the tool in the answer's line too)."""
    first = next(i for i, line in enumerate(lines) if started(marker) in line)
    for i in range(first, max(-1, first - BACK_LINES), -1):
        if "start_run" in lines[i] and i != first:
            return lines[i:]
    return lines[first:]


def keep(marker, since, out):
    """The run's conversation files (named by marker, written to since) as JSON lines in out: {"client", "file"
    ("main" or "subagent <n>"), "line": its line in the client's file, "entry": the line, pictures left out}.
    Returns what was kept, in words, or None when nothing names the marker."""
    found = []
    for path, client in candidates(since):
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                lines = f.read().splitlines()
        except OSError:
            continue
        main = any(started(marker) in line for line in lines)
        if not main and not any(looked(marker) in line for line in lines):
            continue  # (no answer of this run's in it: at most a mention)
        found.append((not main, path, client, lines))
    if not found:
        return None
    found.sort(key=lambda f: f[0])  # (the main conversation first: it has the start call's answer)
    written = 0
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        sub = 0
        for is_sub, path, client, lines in found:
            if is_sub:
                sub += 1
                start, name = 0, f"subagent {sub}"
            else:
                kept = _cut(lines, marker)
                start, name, lines = len(lines) - len(kept), "main", kept
            for n, line in enumerate(lines, start + 1):
                if not line.strip():
                    continue
                try:
                    entry = no_pictures(json.loads(line))
                except ValueError:
                    entry = no_pictures(line)
                f.write(json.dumps({"client": client, "file": name, "line": n, "entry": entry},
                                   ensure_ascii=False) + "\n")
                written += 1
    return f"{written} lines from {len(found)} file(s): " + ", ".join(f"{c} {os.path.basename(p)}"
                                                                       for _, p, c, _ in found)
