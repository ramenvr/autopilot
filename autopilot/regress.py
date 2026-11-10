"""Regression replay: every CONFIRMED verification is kept as a harness that plays again without a language model.

When a run reports a claim CONFIRMED (agents/session.finish), the harness of the episode that showed it is saved under
the project's regressions folder with what that episode ended on:

    <regressions>/<project>/<run id>/
        harness.py          the episode's harness (the run's harness<n>.py)
        lib.py              the project's lib.py as the run left it (a snapshot: replay uses the live one by default)
        regression.json     the claim, the verdict, the episode's end (status state and evidence), Jev's picks in order

<regressions> is AUTOPILOT_REGRESSIONS, else the project's memory folder (memory/<project>/regressions/<run id>).

A replay (`autopilot regress`, or replay() here) restarts the level and runs the saved harness through the runtime as
an episode did, but its decisions come from ReplayJev: the recorded picks, in order, where they are offered, else the
option the recording picked most. No language model and no Jev: the same harness, the same game, deterministic
choices. It passes when the episode ends in the state it ended in (done) with the same evidence: booleans, strings and
None equal, numbers within `tolerance` (relative, regression.json; default 0.25). When and where in the episode
something happened isn't compared (a key `t` or ending in `_t`, a time; ending in `_at`, a position such as
`player_at`): a replay's picks land a little earlier or later than the recording's, so it ends a little elsewhere, and
neither is what was verified. A duration is compared. A replay that fails says why; one that fails is played again
(retries), and one that passes then is flaky.

Only an episode that started from a restarted level is saved (one that went on from where an earlier episode left
play needs that state, which a replay can't rebuild): the latest such episode that ended done, else none.
"""

import json
import os
import shutil
import sys
import time
import types

from . import runtime

FILE = "regression.json"
TOLERANCE = 0.25
RETRIES = 1  # times a failed replay is played again before it counts as failed (a pass then: flaky)
LOOKAHEAD = 5  # recorded picks a replay may skip to find one offered now (timings differ between runs)
MIN_SECONDS = 20.0  # a replay's episode lasts at least this, and 1.5x the recorded one


def root():
    """Where regressions are kept: AUTOPILOT_REGRESSIONS, else None (each project's memory folder)."""
    return (os.environ.get("AUTOPILOT_REGRESSIONS") or "").strip() or None


def folder(project, project_root=None):
    """A project's regressions folder."""
    if root():
        return os.path.join(root(), project)
    if project_root is None:
        from . import codex

        project_root = codex.project_dir(project)
    return os.path.join(project_root, "regressions")


# -- reading an episode's trace --------------------------------------------------------------------------------------

def read_trace(path):
    events = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    events.append(json.loads(line))
                except ValueError:
                    pass
    except OSError:
        pass
    return events


def episode_of(events):
    """What a replay needs of one episode's trace: {"continued", "end": {state, note, evidence}, "picks", "extras",
    "t"} (picks: the options Jev's answers chose, in order; extras: its other answers, by decision)."""
    picks, extras, end, start_t, continued = [], [], None, None, False
    for e in events:
        kind = e.get("kind") or e.get("event")
        if kind == "continued":
            continued = True
        elif kind == "decision" and e.get("chosen"):
            picks.append(e["chosen"])
            extras.append(e.get("extra") or {})
            if start_t is None:
                start_t = e.get("t")
        elif kind == "end":
            end = e
    status = (end or {}).get("status") or {}
    t = ((end or {}).get("progress") or {}).get("t")
    return {"continued": continued, "ended": end is not None, "reason": (end or {}).get("reason"),
            "end": {"state": status.get("state"), "note": status.get("note"), "evidence": status.get("evidence") or {}},
            "picks": picks, "extras": extras, "t": t}


def confirming_episode(run_dir):
    """(n, episode) of the run's latest episode that started from a restarted level and ended done, else None."""
    ns = []
    for name in os.listdir(run_dir):
        if name.startswith("trace") and name.endswith(".jsonl") and name[5:-6].isdigit():
            ns.append(int(name[5:-6]))
    for n in sorted(ns, reverse=True):
        if not os.path.exists(os.path.join(run_dir, f"harness{n}.py")):
            continue
        ep = episode_of(read_trace(os.path.join(run_dir, f"trace{n}.jsonl")))
        if ep["ended"] and not ep["continued"] and ep["end"]["state"] == "done":
            return n, ep
    return None


# -- saving --------------------------------------------------------------------------------------------------------

def save(run_dir, report, project, project_root=None, dest=None):
    """Keep a CONFIRMED verification's harness as a regression; its folder, or None (not a confirmed claim, or no
    episode a replay can play: see the module's docstring)."""
    if str(report.get("outcome") or "").strip().upper() != "CONFIRMED":
        return None
    found = confirming_episode(run_dir)
    if not found:
        return None
    n, ep = found
    rid = str(report.get("run") or os.path.basename(os.path.normpath(run_dir)))
    dest = dest or os.path.join(folder(project, project_root), rid)
    os.makedirs(dest, exist_ok=True)
    shutil.copyfile(os.path.join(run_dir, f"harness{n}.py"), os.path.join(dest, "harness.py"))
    lib = os.path.join(project_root, "lib.py") if project_root else None
    if lib and os.path.exists(lib):
        shutil.copyfile(lib, os.path.join(dest, "lib.py"))
    spec = {
        "id": rid, "project": project, "prompt": report.get("prompt"), "outcome": "CONFIRMED",
        "summary": report.get("summary"), "evidence": report.get("evidence") or [],
        "saved": time.strftime("%Y-%m-%dT%H:%M:%S"), "episode": n,
        "expect": ep["end"], "picks": ep["picks"], "extras": ep["extras"], "played_seconds": ep["t"],
        "tolerance": TOLERANCE,
    }
    with open(os.path.join(dest, FILE), "w", encoding="utf-8") as f:
        json.dump(runtime.jsonable(spec), f, indent=1)
    return dest


def save_from_session(session, report):
    """agents/session.finish's hook: the run's regression when it CONFIRMED a claim (never raises)."""
    try:
        return save(session.run_dir, report, session.project, project_root=session.root)
    except Exception as e:
        try:
            session.note(f"couldn't save the regression: {type(e).__name__}: {e}")
        except Exception:
            pass
        return None


def listing(base):
    """The regression folders under base (a project's regressions folder, one regression's, or a tree of them)."""
    if os.path.exists(os.path.join(base, FILE)):
        return [base]
    found = []
    for here, _, files in os.walk(base):
        if FILE in files:
            found.append(here)
    return sorted(found)


def load(reg):
    with open(os.path.join(reg, FILE), encoding="utf-8") as f:
        return json.load(f)


# -- replaying -----------------------------------------------------------------------------------------------------

class ReplayJev:
    """Jev's stand-in for a replay: the recorded picks in order (the next one offered within LOOKAHEAD), else the
    option offered that the recording picked most, else the first offered. Other questions get the recorded answers
    of the same decision, else neutral ones. No model is called."""

    def __init__(self, picks, extras=None):
        self.picks = list(picks or [])
        self.extras = list(extras or [])
        self.at = 0
        self.calls = 0
        self.counts = {}
        for p in self.picks:
            self.counts[p] = self.counts.get(p, 0) + 1

    def pick(self, names):
        for i in range(self.at, min(len(self.picks), self.at + LOOKAHEAD)):
            if self.picks[i] in names:
                self.at = i + 1
                return self.picks[i], i
        ranked = sorted(names, key=lambda n: -self.counts.get(n, 0))
        return (ranked[0] if ranked else None), None

    def system_one(self, state, questions):
        self.calls += 1
        ns = types.SimpleNamespace
        names = list(getattr(questions.get("pick"), "criteria", None) or {})
        chosen, i = self.pick(names)
        recorded = self.extras[i] if i is not None and i < len(self.extras) else {}
        choices, nouls, scores = {}, {}, {}
        if chosen is not None:
            rest = (0.1 / (len(names) - 1)) if len(names) > 1 else 0.0
            choices["pick"] = ns(choice=chosen, probabilities={n: 0.9 if n == chosen else rest for n in names},
                                 confidence=0.9)
        for k, q in questions.items():
            if k == "pick":
                continue
            kind = type(q).__name__
            if kind == "Noul":
                v = recorded.get(k)
                nouls[k] = ns(noul=v if isinstance(v, (int, float)) else 0.5)
            elif kind == "Score":
                v = recorded.get(k)
                scores[k] = ns(score=v if isinstance(v, (int, float)) else 0.5)
            else:
                crit = list(getattr(q, "criteria", None) or {})
                v = recorded.get(k)
                probs = dict(v) if isinstance(v, dict) else {c: 1.0 / len(crit) for c in crit}
                if probs:
                    choices[k] = ns(choice=max(probs, key=probs.get), probabilities=probs, confidence=0.5)
        return ns(choices=choices, nouls=nouls, scores=scores, usage=None)


def incidental(key):
    """Whether an evidence key names when (`t`, `pulled_at_t`) or where (`player_at`) in the episode something
    happened: details of one run, which a replay's timing moves, so not compared."""
    return isinstance(key, str) and (key == "t" or key.endswith(("_t", "_at")))


def same(expected, got, tolerance=TOLERANCE, ignore=()):
    """Why got differs from expected (a regression's evidence), or None: booleans, strings and None equal; numbers
    within tolerance (relative, at least of 1); lists and dicts element by element, a dict's incidental keys skipped,
    and the keys in ignore (a regression's own "ignore")."""
    if isinstance(expected, bool) or expected is None or isinstance(expected, str):
        return None if got == expected else f"{expected!r} then, {got!r} now"
    if isinstance(expected, (int, float)):
        if isinstance(got, bool) or not isinstance(got, (int, float)):
            return f"{expected!r} then, {got!r} now"
        return None if abs(got - expected) <= tolerance * max(1.0, abs(expected)) else f"{expected!r} then, {got!r} now"
    if isinstance(expected, dict):
        if not isinstance(got, dict):
            return f"{expected!r} then, {got!r} now"
        for k, v in expected.items():
            if incidental(k) or k in ignore:
                continue
            if k not in got:
                return f"{k}: missing now"
            why = same(v, got[k], tolerance, ignore)
            if why:
                return f"{k}: {why}"
        return None
    if isinstance(expected, list):
        if not isinstance(got, list) or len(got) != len(expected):
            return f"{expected!r} then, {got!r} now"
        for i, (a, b) in enumerate(zip(expected, got)):
            why = same(a, b, tolerance, ignore)
            if why:
                return f"[{i}]: {why}"
    return None


def _load_harness(reg, lib_dir):
    """The regression's harness, its `import lib` the one in lib_dir."""
    sys.modules.pop("lib", None)
    sys.path.insert(0, lib_dir)
    try:
        return runtime.load_harness(os.path.join(reg, "harness.py"))
    finally:
        try:
            sys.path.remove(lib_dir)
        except ValueError:
            pass


def replay(game, reg, project_root=None, frozen_lib=False, seconds=None, restart=True):
    """Play regression reg (its folder) again on game, without a model: {"id", "passed", "why", "state", "evidence",
    "decisions", "seconds", "trace"}. lib: the project's live lib.py (project_root) unless frozen_lib, or there is
    none: then the snapshot saved with it."""
    spec = load(reg)
    started = time.time()
    out = {"id": spec.get("id") or os.path.basename(reg), "prompt": spec.get("prompt"), "passed": False, "why": "",
           "state": None, "evidence": None, "decisions": 0, "seconds": 0.0, "dir": reg}
    lib_dir = reg
    if not frozen_lib and project_root and os.path.exists(os.path.join(project_root, "lib.py")):
        lib_dir = project_root
    try:
        harness = _load_harness(reg, lib_dir)
    except Exception as e:
        out["why"] = f"the harness doesn't load: {type(e).__name__}: {e}"
        out["seconds"] = round(time.time() - started, 2)
        return out
    expect = spec.get("expect") or {}
    played = spec.get("played_seconds") or 0
    limit = seconds or max(MIN_SECONDS, 1.5 * float(played))
    trace = os.path.join(reg, "replay.jsonl")
    try:
        if restart:
            game.restart(map=getattr(harness, "start_map", None))
        game.pause(False)
        rt = runtime.Runtime(game, harness, trace, client=ReplayJev(spec.get("picks"), spec.get("extras")),
                             max_seconds=limit)
        result = rt.run()
    except Exception as e:
        out["why"] = f"the replay failed: {type(e).__name__}: {e}"
        out["seconds"] = round(time.time() - started, 2)
        return out
    finally:
        try:
            game.release_all()
        except Exception:
            pass
    status = result.get("status") or {}
    out.update(state=status.get("state"), evidence=status.get("evidence"), decisions=result.get("decisions") or 0,
               seconds=round(time.time() - started, 2), trace=trace)
    if status.get("state") != expect.get("state"):
        out["why"] = (f"ended {status.get('state') or 'without a status'} ({result.get('reason') or ''}"
                      f"{': ' + status['note'] if status.get('note') else ''}), not {expect.get('state')}")
        return out
    why = same(expect.get("evidence") or {}, status.get("evidence") or {}, float(spec.get("tolerance", TOLERANCE)),
               set(spec.get("ignore") or ()))
    if why:
        out["why"] = f"evidence differs: {why}"
        return out
    out["passed"] = True
    out["why"] = "as recorded"
    return out


def replay_all(game, regs, project_root=None, frozen_lib=False, seconds=None, say=print, retries=RETRIES):
    """replay() of each regression folder in regs, one after another (one game): their results. One that fails is
    replayed again, up to `retries` times (a replay costs no model): one that then passes is FLAKY (its "flaky": it
    passed, but not every time: its harness reads the game too tightly, or the game itself varies)."""
    results = []
    for reg in regs:
        r = replay(game, reg, project_root=project_root, frozen_lib=frozen_lib, seconds=seconds)
        first = r
        for k in range(max(0, retries)):
            if r["passed"]:
                break
            r = replay(game, reg, project_root=project_root, frozen_lib=frozen_lib, seconds=seconds)
            if r["passed"]:
                r = dict(r, flaky=True, why=f"passed on try {k + 2}; the first: {first['why']}")
        results.append(r)
        if say:
            mark = "FLAKY" if r.get("flaky") else "ok   " if r["passed"] else "FAIL "
            say(f"  {mark} {r['id']}: {r['prompt'] or ''}  ({r['why']}, {r['seconds']:.0f}s)")
    return results


def summary(results):
    return {"passed": sum(1 for r in results if r["passed"]), "total": len(results),
            "failed": sorted(r["id"] for r in results if not r["passed"]),
            "flaky": sorted(r["id"] for r in results if r.get("flaky")), "results": results}
