"""A shared run as it is sent: one self-contained folder per run, the same layout every time, cleaned of
secrets and personal details (autopilot/redact.py). docs/run-data.md says what each file holds;
tools/training_data.py reads it.

    runs/<run id>-<uid>/
        run.json                       the manifest: what the run was, its outcome and usage, the id of the codex it
                                       saw, how much was redacted, and every other file with its size and MD5 (sent
                                       last: a run without it hasn't arrived whole)
        prompt.txt, report.json        the request and the agent's report
        harness.py                     the agent's final harness
        agent/llm.jsonl.gz             every call to the agent's language model, whole (but for pictures' data)
        agent/describe.jsonl.gz        every describe_frames description: its questions, its frames (by time) and
                                       what the vision model said
        agent/transcript.jsonl.gz      the user's own agent's conversation during the run, as its client (Claude Code,
                                       Codex) kept it (but for pictures' data): runs through `autopilot mcp`, whose
                                       model calls never pass through Autopilot (autopilot/transcripts.py)
        agent/log.txt                  the agent's log
        episodes/<n>/trace.jsonl.gz    episode n as it played
        episodes/<n>/decisions.jsonl.gz  every decision model call of episode n, whole
        episodes/<n>/harness.py        the harness episode n ran
        episodes/<n>/summary.txt       the summary of episode n the agent read
        episodes/<n>/recording.mp4     what the game showed while episode n played, to its end (held a second)
        episodes/<n>/frames.jsonl.gz   each of its video frames' game and wall time (episodes/<n>/frames/: the
                                       pictures as JPEGs, for a run that couldn't make a video)
        memory/before/, memory/after/  notes.md and lib.py as the run found and left them
        media/stills/, media/stills.jsonl.gz   stills (and, from runs before videos per episode, the whole run's
                                       media/recording.mp4 and media/frames.jsonl.gz)
        extra/                         any other text the run left (logs), cleaned the same way

The codex itself (the game's code as text) is never part of it: whole codebases aren't stored. The parts the agent read
are in its calls (agent/llm.jsonl.gz).

    export_run(run_dir, out, redactor)   one run into out/ (text files written there, binaries left where they are)
    codex_id(project)                    the id of the project's codex as it is now
    python -m autopilot.dataset <memory/project> <out>   every shared run of a project, as it would be sent
"""

import base64
import gzip
import hashlib
import io
import json
import os
import re
import sys
import time

from . import memory
from .redact import VERSION as REDACTION
from .redact import Redactor

SCHEMA = 2
TELEMETRY = "telemetry"
TEXT = {".txt", ".log", ".md", ".py", ".json", ".jsonl", ".csv", ".ini", ".cfg", ".yaml", ".yml", ".toml", ".xml"}
SKIP = (".agent", "__pycache__")


def md5_of(path):
    """The file's MD5, base64 (as the storage reports it)."""
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return base64.b64encode(h.digest()).decode()


def _json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _read_lines(path):
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8", errors="replace") as f:
        yield from f


def _gzip_bytes(text):
    """text gzipped the same way every time (no name, no time in the header): the same run exports the same bytes."""
    buf = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=buf, compresslevel=6, mtime=0) as gz:
        gz.write(text.encode("utf-8"))
    return buf.getvalue()


def run_uid(manifest, run_id):
    """The run's unique id: run.json's run_uid, else one made from its id and start (runs from before it was kept)."""
    uid = (manifest or {}).get("run_uid")
    if uid:
        return str(uid)
    return hashlib.sha256(f"{run_id}:{(manifest or {}).get('started')}".encode()).hexdigest()[:12]


def run_prefix(run_dir, manifest=None):
    """Where a run is stored: runs/<run id>-<uid>/."""
    run_id = os.path.basename(os.path.normpath(run_dir))
    manifest = manifest if manifest is not None else (_json(os.path.join(run_dir, TELEMETRY, "run.json")) or {})
    return f"runs/{run_id}-{run_uid(manifest, run_id)}/"


def shared_runs(project):
    """The project's runs whose data was shared (they have telemetry/run.json), oldest first: [(run dir, manifest)]."""
    runs = os.path.join(project, "runs")
    out = []
    for name in sorted(os.listdir(runs)) if os.path.isdir(runs) else []:
        manifest = _json(os.path.join(runs, name, TELEMETRY, "run.json"))
        if isinstance(manifest, dict):
            out.append((os.path.join(runs, name), manifest))
    return out


# -- the codex ------------------------------------------------------------------------------------------------------

def codex_files(project):
    """{relative path: full path} of the project's codex (its text files)."""
    folder = os.path.join(project, "codex")
    return {rel: full for rel, full in memory.files(folder).items()} if os.path.isdir(folder) else {}


def codex_id(project):
    """The id of the project's codex as it is now (from its files' paths and contents); None when it has none."""
    found = codex_files(project)
    if not found:
        return None
    h = hashlib.sha256()
    for rel in sorted(found):
        h.update(rel.encode("utf-8") + b"\0" + md5_of(found[rel]).encode() + b"\n")
    return h.hexdigest()[:16]


# -- a run ----------------------------------------------------------------------------------------------------------

def plan(run_dir):
    """[(source path, path in the export, how)] for a run folder; how: text, json, jsonl or copy."""
    tel = os.path.join(run_dir, TELEMETRY)
    out, used = [], set()

    def take(src, dst, how):
        if os.path.isfile(src) and os.path.getsize(src) > 0:
            out.append((src, dst, how))
            used.add(os.path.normcase(os.path.abspath(src)))

    take(os.path.join(run_dir, "prompt.txt"), "prompt.txt", "text")
    take(os.path.join(run_dir, "report.json"), "report.json", "json")
    take(os.path.join(run_dir, "harness.py"), "harness.py", "text")
    take(os.path.join(run_dir, "implementer.log"), "agent/log.txt", "text")
    for name in ("llm.jsonl", "llm.jsonl.gz"):
        take(os.path.join(tel, name), "agent/llm.jsonl.gz", "jsonl")
    for name in ("describe.jsonl", "describe.jsonl.gz"):
        take(os.path.join(tel, name), "agent/describe.jsonl.gz", "jsonl")
    for name in ("transcript.jsonl", "transcript.jsonl.gz"):
        take(os.path.join(tel, name), "agent/transcript.jsonl.gz", "jsonl")
    for name in sorted(os.listdir(run_dir)):
        m = re.fullmatch(r"(trace|harness)(\d+)(_summary\.txt|\.jsonl|\.py)", name)
        if m:
            n, what = int(m.group(2)), (m.group(1), m.group(3))
            dst, how = {("trace", ".jsonl"): ("trace.jsonl.gz", "jsonl"), ("trace", "_summary.txt"): ("summary.txt",
                        "text"), ("harness", ".py"): ("harness.py", "text")}.get(what, (None, None))
            if dst:
                take(os.path.join(run_dir, name), f"episodes/{n}/{dst}", how)
    for name in sorted(os.listdir(tel)) if os.path.isdir(tel) else []:
        m = re.fullmatch(r"decisions(\d+)\.jsonl(\.gz)?", name)
        if m:
            take(os.path.join(tel, name), f"episodes/{int(m.group(1))}/decisions.jsonl.gz", "jsonl")
    for when in ("before", "after"):
        for name in ("notes.md", "lib.py"):
            take(os.path.join(tel, f"memory_{when}", name), f"memory/{when}/{name}", "text")
    media = os.path.join(tel, "media")
    # each episode's video (media.Recorder), beside its trace
    for name in sorted(os.listdir(media)) if os.path.isdir(media) else []:
        m = re.fullmatch(r"(recording|frames)-(\d+)(\.mp4|\.jsonl)?", name)
        if not m:
            continue
        n, what = int(m.group(2)), (m.group(1), m.group(3))
        if what == ("recording", ".mp4"):
            take(os.path.join(media, name), f"episodes/{n}/recording.mp4", "copy")
        elif what == ("frames", ".jsonl"):
            take(os.path.join(media, name), f"episodes/{n}/frames.jsonl.gz", "jsonl")
        elif what == ("frames", None) and os.path.isdir(os.path.join(media, name)):
            for picture in sorted(os.listdir(os.path.join(media, name))):
                if picture.lower().endswith((".jpg", ".jpeg", ".png")):
                    take(os.path.join(media, name, picture), f"episodes/{n}/frames/{picture}", "copy")
    # (the whole run's recording, from runs made before videos per episode)
    take(os.path.join(media, "recording.mp4"), "media/recording.mp4", "copy")
    for name in ("frames.jsonl", "stills.jsonl"):
        take(os.path.join(media, name), f"media/{name}.gz", "jsonl")
    for sub in ("stills", "frames"):
        folder = os.path.join(media, sub)
        for name in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
            if name.lower().endswith((".jpg", ".jpeg", ".png")):
                take(os.path.join(folder, name), f"media/{sub}/{name}", "copy")
    # anything else the run left that is text (logs the harness or the agent wrote), cleaned the same way
    for rel, full in sorted(memory.files(run_dir).items()):
        if rel.split("/")[0] in SKIP or rel == f"{TELEMETRY}/run.json" or rel.startswith(f"{TELEMETRY}/media/") or \
                os.path.normcase(os.path.abspath(full)) in used:
            continue
        ext = os.path.splitext(rel[:-3] if rel.endswith(".gz") else rel)[1].lower()
        if ext in TEXT and os.path.getsize(full) > 0:
            how = "jsonl" if ext == ".jsonl" else "json" if ext == ".json" else "text"
            dst = "extra/" + (rel[:-3] if rel.endswith(".gz") else rel) + (".gz" if how == "jsonl" else "")
            out.append((full, dst, how))
    return out


def _clean(src, how, redactor):
    """The cleaned bytes of a text, JSON or JSON lines file (JSON lines gzipped), and how many lines were dropped
    (a line that isn't JSON)."""
    if how == "jsonl":
        lines, dropped = [], 0
        for line in _read_lines(src):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except ValueError:
                dropped += 1
                continue
            lines.append(json.dumps(redactor.data(value), ensure_ascii=False))
        return _gzip_bytes("\n".join(lines) + ("\n" if lines else "")), dropped
    with open(src, encoding="utf-8", errors="replace") as f:
        text = f.read()
    if how == "json":
        try:
            return (json.dumps(redactor.data(json.loads(text)), ensure_ascii=False, indent=1) + "\n").encode(), 0
        except ValueError:
            pass
    return redactor.text(text).encode("utf-8"), 0


def project_path(run_dir):
    """Where a run's project is on this machine (its memory folder's project.json), or None."""
    info = _json(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(run_dir))), "project.json"))
    path = info.get("project") if isinstance(info, dict) else None
    return path if isinstance(path, str) else None


def export_run(run_dir, out, redactor=None):
    """A shared run (one with telemetry/run.json) cleaned into out/: its text files written there, its pictures and
    video left where they are. Returns the manifest (also written to out/run.json), whose `files` list every file
    with its `source` on this machine (dropped from run.json). redactor: by default, this machine's, knowing where the
    project is (its project.json)."""
    redactor = redactor or Redactor(project=project_path(run_dir))
    local = _json(os.path.join(run_dir, TELEMETRY, "run.json"))
    if not isinstance(local, dict):
        raise ValueError(f"{run_dir}: not a shared run (no {TELEMETRY}/run.json)")
    run_id = os.path.basename(os.path.normpath(run_dir))
    files, dropped, names = [], 0, {}
    for src, dst, how in plan(run_dir):
        local_name = os.path.relpath(src, run_dir).replace(os.sep, "/")
        if local_name != dst:
            names[f"runs/{run_id}/{local_name}"] = dst
        if how == "copy":
            files.append({"path": dst, "bytes": os.path.getsize(src), "md5": md5_of(src), "source": src})
            continue
        data, lost = _clean(src, how, redactor)
        dropped += lost
        target = os.path.join(out, *dst.split("/"))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as f:
            f.write(data)
        files.append({"path": dst, "bytes": len(data), "md5": md5_of(target), "source": target})

    episodes = []
    for span in local.get("episodes") or []:
        name = str(span.get("episode") or "")
        n = int(name[5:]) if name.startswith("trace") and name[5:].isdigit() else None
        episodes.append({"n": n, **{k: v for k, v in span.items() if k != "episode"}})
    agent = dict(local.get("agent") or {})
    manifest = {
        "schema": SCHEMA, "dataset": "autopilot.run",
        "run": run_id, "run_uid": run_uid(local, run_id),
        "project": local.get("project"), "kind": local.get("kind"), "prompt": local.get("prompt"),
        "channel": "aura" if local.get("through_aura") else "own_keys", "aura_run": local.get("aura_run"),
        "started": local.get("started"), "ended": local.get("ended"),
        "autopilot": local.get("autopilot"), "engine": local.get("engine"), "agent": agent,
        "outcome": local.get("outcome"), "usage": local.get("usage"), "recording": local.get("recording"),
        "episodes": episodes, "clock": local.get("clock"),
        "codex_id": local.get("codex"),  # (the codex itself is never sent)
        # (the agent's report and conversation name the run's files as they were on the user's machine)
        "local_names": dict(sorted(names.items())),
    }
    manifest = redactor.data(manifest)
    counts = dict(sorted(redactor.counts.items()))
    if dropped:
        counts["unreadable_lines"] = dropped
    manifest["redaction"] = {"version": REDACTION, "counts": counts}
    manifest["files"] = sorted(files, key=lambda f: f["path"])
    manifest["exported"] = round(time.time(), 3)
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "run.json"), "w", encoding="utf-8") as f:
        json.dump({**manifest, "files": [{k: v for k, v in x.items() if k != "source"} for x in manifest["files"]]},
                  f, ensure_ascii=False, indent=1)
    return manifest


def export_project(project, out):
    """Every shared run of a project, as it would be sent, into out/ (runs/...). Returns the manifests."""
    import shutil

    manifests = []
    for run_dir, local in shared_runs(project):
        target = os.path.join(out, *run_prefix(run_dir, local).strip("/").split("/"))
        m = export_run(run_dir, target)
        for f in m["files"]:
            dst = os.path.join(target, *f["path"].split("/"))
            if os.path.abspath(f["source"]) != os.path.abspath(dst):
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copyfile(f["source"], dst)
        manifests.append(m)
    return manifests


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2:
        print("usage: python -m autopilot.dataset <memory/project folder> <out folder>")
        return 2
    for m in export_project(argv[0], argv[1]):
        print(f"{m['run']}-{m['run_uid']}: {len(m['files'])} files, redacted {m['redaction']['counts'] or 'nothing'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
