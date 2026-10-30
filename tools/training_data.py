"""Read Autopilot's shared run data (docs/run-data.md): a project's folder as stored, or as it would be sent
(python -m autopilot.dataset memory/<project> <out>).

    python tools/training_data.py <project folder>            what is there, run by run
    python tools/training_data.py <project folder> --check    also check every run arrived whole and reads cleanly

From Python:

    from tools.training_data import runs, manifest, llm_calls, decisions, frames, frame_at, video_time, codex
    for run in runs("data/MyGame"):              # the runs that arrived whole (their run.json is there)
        conversation = llm_calls(run)[-1]       # the last call holds the whole conversation
        for d in decisions(run):                 # every decision with what it led to
            picture = frame_at(run, d["wall"])    # what the player saw then (a numpy array; needs PyAV)
            at = video_time(run, d["wall"])       # where that is: (episode n, seconds into episodes/<n>/recording.mp4)
        game = codex(run)                        # {path: text}: the game's code as the run saw it ({}: not sent)
"""

import argparse
import base64
import bisect
import glob
import gzip
import hashlib
import json
import os
import sys


def _lines(path):
    """The JSON lines of path (gzipped when it ends in .gz)."""
    if not os.path.exists(path):
        return []
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def runs(project, whole=True):
    """The run folders of a project folder, oldest first; with whole, only those that arrived whole (run.json is sent
    last)."""
    found = (p for p in glob.glob(os.path.join(project, "runs", "*")) if os.path.isdir(p))
    return sorted(p for p in found if not whole or os.path.isfile(os.path.join(p, "run.json")))


def manifest(run):
    return _json(os.path.join(run, "run.json"), {})


def report(run):
    return _json(os.path.join(run, "report.json"), {})


def llm_calls(run, messages_only=True):
    """The language model's calls, in order (with messages_only, those that asked for an answer)."""
    calls = _lines(os.path.join(run, "agent", "llm.jsonl.gz"))
    if messages_only:
        calls = [c for c in calls if c.get("path", "").rstrip("/").endswith(("/messages", "/chat/completions",
                                                                              "/responses"))]
    return calls


def episodes(run):
    """The episode numbers of a run."""
    return sorted(int(os.path.basename(p)) for p in glob.glob(os.path.join(run, "episodes", "*"))
                  if os.path.basename(p).isdigit())


def trace(run, n):
    return _lines(os.path.join(run, "episodes", str(n), "trace.jsonl.gz"))


def outcomes(run, n):
    """{call: what the option it started did} for episode n: the option_end that ended the option a decision started
    (the runtime traces a decision just after the option_start it caused)."""
    out, started = {}, {}
    for e in trace(run, n):
        if e["kind"] == "decision" and e.get("action") in ("started", "switched") and e.get("call") is not None:
            started[e.get("chosen")] = e["call"]
        elif e["kind"] == "option_end" and e.get("option") in started:
            out[started.pop(e["option"])] = {k: e.get(k) for k in ("option", "ok", "effect", "why", "seconds",
                                                                    "interrupted")}
    return out


def decisions(run):
    """Every decision of every episode, each with `outcome`: what the option it started did (None when it started
    none)."""
    out = []
    for n in episodes(run):
        led = outcomes(run, n)
        for d in _lines(os.path.join(run, "episodes", str(n), "decisions.jsonl.gz")):
            d["outcome"] = led.get(d.get("call")) if d.get("action") in ("started", "switched") else None
            out.append(d)
    return out


def frames(run):
    """The recording's frame lines in wall time order, each with its "episode" (n: episodes/<n>/recording.mp4); a run
    from before videos per episode has the whole run's media/recording.mp4 (its lines' "episode" is None)."""
    lines = []
    for folder in sorted(glob.glob(os.path.join(run, "episodes", "*"))):
        name = os.path.basename(folder)
        if name.isdigit():
            lines += [{**x, "episode": int(name)} for x in _lines(os.path.join(folder, "frames.jsonl.gz"))]
    if not lines:
        lines = [{**x, "episode": None} for x in _lines(os.path.join(run, "media", "frames.jsonl.gz"))]
    return sorted(lines, key=lambda x: x.get("wall") or 0)


def codex(run):
    """{path: text}: the game's code and data as text, as the run saw it, for a run sent with its codex (before
    2026-10-19; since then the codex is never sent, and run.json's codex_id names it); else {}."""
    name = manifest(run).get("codex")
    if not name:
        return {}
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(run))), *name.split("/"))
    return {line["path"]: line["text"] for line in _lines(path)}


def frame_line_at(run, wall, _cache={}):
    """The recording's frame showing what the player saw at wall time `wall` (Unix seconds, the clock every file
    shares): in the episode video nearest it (video_time), the last frame captured at or before it, else that video's
    first (an episode's first moments come a little before its first frame); None without frames. Sync by wall, not by
    the game time t, which starts again when a level restarts."""
    lines = _cache.get(run) or _cache.setdefault(run, frames(run))
    where = video_time(run, wall)
    if where is not None and where[0] is not None:
        lines = [x for x in lines if x.get("episode") == where[0]]
    i = bisect.bisect_right([x.get("wall") or 0 for x in lines], wall) - 1
    return lines[max(i, 0)] if lines else None


def video_time(run, wall):
    """Where wall time `wall` is in the recording: (episode n, seconds into episodes/<n>/recording.mp4: its time 0 is
    run.json's recording.episodes[].wall0, its first frame's wall time), the episode whose video's span holds it, else
    the nearest one (between episodes nothing is recorded; an episode's first events come a little before its first
    frame: negative seconds); (None, seconds into media/recording.mp4) for a run from before videos per episode; None
    without a recording."""
    recording = manifest(run).get("recording") or {}
    clips = [c for c in recording.get("episodes") or [] if c.get("wall0") is not None]
    if clips:
        def away(c):
            end = c.get("wall_end") if c.get("wall_end") is not None else c["wall0"]
            return max(c["wall0"] - wall, wall - end, 0.0)

        clip = min(clips, key=away)
        return clip["episode"], wall - clip["wall0"]
    wall0 = recording.get("wall0")
    if wall0 is None:
        first = next(iter(frames(run)), None)
        wall0 = first.get("wall") if first else None
    return None if wall0 is None else (None, wall - wall0)


def calls_between(run, wall_from, wall_to):
    """What the agent and the decision model did between two wall times: (llm calls that overlap it, decisions asked
    in it)."""
    llm = [c for c in llm_calls(run, False)
           if (c.get("ended") or 0) >= wall_from and (c.get("started") or 0) <= wall_to]
    dec = [d for d in decisions(run) if wall_from <= (d.get("wall") or 0) <= wall_to]
    return llm, dec


def frame_at(run, wall):
    """The picture the player saw at wall time `wall` (frame_line_at), as an RGB numpy array; None when there is
    none. Needs PyAV (and numpy). A decision's picture: frame_at(run, decision["wall"])."""
    line = frame_line_at(run, wall)
    if line is None:
        return None
    import av

    n = line.get("episode")
    folder = os.path.join(run, "episodes", str(n)) if n is not None else os.path.join(run, "media")
    if line.get("file"):
        picture = os.path.join(folder, "frames", line["file"]) if n is not None else os.path.join(folder, line["file"])
        with av.open(picture) as c:
            return next(c.decode(video=0)).to_ndarray(format="rgb24")
    with av.open(os.path.join(folder, "recording.mp4")) as c:
        stream = c.streams.video[0]
        target = line["pts_ms"]
        c.seek(int(target / 1000 / stream.time_base), stream=stream, backward=True)
        for f in c.decode(stream):
            if f.pts is not None and round(f.pts * stream.time_base * 1000) >= target:
                return f.to_ndarray(format="rgb24")
    return None


def _md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return base64.b64encode(h.digest()).decode()


def problems(run):
    """What is wrong with a run as stored: files run.json lists that are missing or not as listed, and JSON lines
    that don't read. [] when it arrived whole."""
    m = manifest(run)
    if not m:
        return ["no run.json: the run hasn't arrived whole"]
    out = []
    for f in m.get("files") or []:
        path = os.path.join(run, *f["path"].split("/"))
        if not os.path.isfile(path):
            out.append(f"{f['path']}: missing")
        elif _md5(path) != f.get("md5"):
            out.append(f"{f['path']}: not as listed (MD5)")
        elif path.endswith(".jsonl.gz"):
            try:
                _lines(path)
            except (OSError, ValueError, EOFError) as e:
                out.append(f"{f['path']}: {type(e).__name__}: {e}")
    return out


def describe(project, check=False):
    for run in runs(project, whole=False):
        m = manifest(run)
        outcome = (m.get("outcome") or {}).get("outcome") or "?"
        print(f"{os.path.basename(run)}  {outcome:<13} {(m.get('prompt') or '')[:70]}")
        if not m:
            print("    (no run.json: not arrived whole)")
            continue
        usage = {u["kind"]: u for u in m.get("usage") or []}
        llm, dec = usage.get("llm") or {}, usage.get("decision") or {}
        print(f"    llm {llm.get('model')}: {llm.get('calls', 0)} calls; decision {dec.get('model')}: "
              f"{dec.get('calls', 0)} calls; episodes {episodes(run)}; recording {m.get('recording')}; "
              f"redacted {(m.get('redaction') or {}).get('counts') or 'nothing'}")
        if check:
            bad = problems(run)
            n = (len(llm_calls(run, False)), len(decisions(run)), len(frames(run)), len(codex(run)))
            print(f"    read: {n[0]} llm calls, {n[1]} decisions, {n[2]} frames, {n[3]} codex files"
                  + ("" if not bad else "; PROBLEMS: " + "; ".join(bad)))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("project")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    describe(args.project, args.check)
    return 0


if __name__ == "__main__":
    sys.exit(main())
