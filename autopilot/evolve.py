"""`autopilot evolve`: an autoresearch loop over Autopilot's own text and code.

A locked bench suite is the measure. Each experiment changes one editable target, runs the suite (in parallel), and
keeps the change only when the pass rate rises, or, at an equal pass rate, when time or tokens fall (by min_gain,
without the other rising by as much); otherwise the target is put back as it was. Every experiment is appended to the
experiments log (experiments.jsonl, experiments.md, the diff in diffs/) with its metrics and the decision.

Targets (one per experiment), as `kind:selector[@file]`:

    prompt:<n | first words>[@file[#VAR]]   a paragraph of the agent's system prompt (agents/session.py's SYSTEM)
    kit:<heading>[@file]                     a section of KIT.md, its heading and body up to the next heading as high
    const:<module.NAME> | const:NAME@file    a module-level constant (kit.control.SONAR_EVERY); the value a literal
    lib:<function>[@file]                    a function of the project's lib.py (memory/<project>/lib.py)

The suite is locked: its hash is kept in <out>/suite.lock on the first run, and a later run on a changed suite is
refused (--relock starts a new lock). No target may be the suite, the log, the bench, the tests or a regression.
With regressions (autopilot/regress.py), a change that makes one fail that passed before is reverted too.

The loop is Karpathy's autoresearch: a fixed evaluation, one change at a time, keep or revert, log everything. The
changes come from a candidates file, or from a model (--propose N), shown the target, the log of what was tried on it,
the suite's misses grouped by how they went wrong, and the maintainers' notes on where Autopilot fails (--direction).

Small suites are noisy, so a win must be real: the baseline is measured several times (--noise) and a change must beat
that spread; a change that wins is measured again and must win again (--confirm); and a held-out suite the proposals
never see (--holdout) must not get worse. Prompts start cold (a fresh memory), or warm from a frozen memory folder
(--memory), or both (--cold-and-warm), reported apart: what failed runs teach shows in the warm numbers.
"""

import ast
import datetime
import difflib
import hashlib
import json
import os
import re
import string
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

PACKAGE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(PACKAGE)
SESSION_FILE = os.path.join(PACKAGE, "agents", "session.py")
KIT_FILE = os.path.join(PACKAGE, "KIT.md")
MIN_GAIN = 0.02  # the share by which time or tokens must fall, at an equal pass rate, for a change to be kept
LOCKED_DIRS = ("bench", "tests", "regressions")  # (of the repository) no target may be in these
PENDING = "pending.json"  # an experiment's file as it was before, while the experiment runs (recover())


class TargetError(ValueError):
    """A target that can't be found, or a change to it that can't be made."""


class SuiteChanged(RuntimeError):
    """The locked suite isn't what it was when it was locked."""


# -- text positions --------------------------------------------------------------------------------------------------

def _offset(text, lineno, col):
    """The character offset of (lineno 1-based, col: UTF-8 bytes) in text, as ast gives positions."""
    lines = text.splitlines(keepends=True)
    before = sum(len(x) for x in lines[:lineno - 1])
    line = lines[lineno - 1] if lineno - 1 < len(lines) else ""
    return before + len(line.encode("utf-8")[:col].decode("utf-8", errors="ignore"))


def _line_start(text, lineno):
    return sum(len(x) for x in text.splitlines(keepends=True)[:lineno - 1])


def _module_assign(tree, name):
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id == name:
            return node
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name \
                and node.value is not None:
            return node
    return None


def _pick(names, selector, what):
    """The index of the one entry of names that selector means (a 1-based number, else the entry it starts)."""
    if re.fullmatch(r"\d+", selector.strip()):
        i = int(selector) - 1
        if not 0 <= i < len(names):
            raise TargetError(f"{what} {selector}: there are {len(names)}")
        return i
    want = " ".join(selector.split()).lower()
    hits = [i for i, n in enumerate(names) if " ".join(n.split()).lower().startswith(want)]
    if len(hits) == 1:
        return hits[0]
    exact = [i for i in hits if " ".join(names[i].split()).lower() == want]
    if len(exact) == 1:
        return exact[0]
    if not hits:
        raise TargetError(f"no {what} starts with {selector!r}")
    raise TargetError(f"{len(hits)} {what}s start with {selector!r}: say more of it")


# -- targets -----------------------------------------------------------------------------------------------------

@dataclass
class Target:
    """One editable piece of one file. span(text) finds it; check(new_file, new_piece) says why a change can't be."""

    kind: str
    selector: str
    path: str
    var: Optional[str] = None
    spec: str = ""

    def raw(self):
        """The file's text exactly as it is on disk (what a revert writes back)."""
        with open(self.path, encoding="utf-8", newline="") as f:
            return f.read()

    def read(self):
        """The file's text with \n line endings (a Windows checkout may have \r\n: render() writes those back)."""
        return self.raw().replace("\r\n", "\n")

    def span(self, text):
        return {"prompt": self._prompt_span, "kit": self._kit_span, "const": self._const_span,
                "lib": self._lib_span}[self.kind](text)

    def current(self):
        text = self.read()
        a, b = self.span(text)
        return text[a:b]

    def render(self, new):
        """The file's text with the target's piece replaced by new (checked)."""
        text = self.read()
        a, b = self.span(text)
        old = text[a:b]
        if self.kind == "kit":  # (the section keeps the blank lines that end it)
            tail = old[len(old.rstrip("\n")):]
            new = new.rstrip("\n") + (tail or "\n")
        elif self.kind == "lib":
            new = new.rstrip("\n") + "\n"
        elif self.kind == "prompt":
            new = new.strip("\n")
        elif self.kind == "const":
            new = new.strip()
        out = text[:a] + new + text[b:]
        self.check(text, out, new)
        return out.replace("\n", "\r\n") if "\r\n" in self.raw() else out

    # prompt: a paragraph of a module-level triple-quoted string

    def _literal(self, text):
        try:
            tree = ast.parse(text)
        except SyntaxError as e:
            raise TargetError(f"{self.path} doesn't parse: {e}") from None
        node = _module_assign(tree, self.var or "SYSTEM")
        if node is None or not isinstance(node.value, ast.Constant) or not isinstance(node.value.value, str):
            raise TargetError(f"{self.path} has no string {self.var or 'SYSTEM'}")
        v = node.value
        a = _offset(text, v.lineno, v.col_offset)
        b = _offset(text, v.end_lineno, v.end_col_offset)
        src = text[a:b]
        m = re.match(r"[rRuU]?(\"\"\"|''')", src)
        if not m or not src.endswith(m.group(1)):
            raise TargetError(f"{self.var or 'SYSTEM'} in {self.path} isn't one triple-quoted string")
        return a + m.end(), b - 3, m.group(1), v.value

    def paragraphs(self, text):
        """[(start, end)] of the prompt's paragraphs (split at blank lines), as offsets in the file."""
        a, b, _, _ = self._literal(text)
        inner = text[a:b]
        out, pos = [], 0
        for m in re.finditer(r"\n[ \t]*\n", inner):
            if inner[pos:m.start()].strip():
                out.append((a + pos, a + m.start()))
            pos = m.end()
        if inner[pos:].strip():
            out.append((a + pos, a + len(inner.rstrip("\n"))))
        return out

    def _prompt_span(self, text):
        paras = self.paragraphs(text)
        a0 = [text[a:b].lstrip("\n") for a, b in paras]
        i = _pick(a0, self.selector, "paragraph")
        a, b = paras[i]
        while text[a] == "\n":
            a += 1
        return a, b

    # kit: a markdown section

    def headings(self, text):
        """[(offset, level, title)] of the markdown headings outside code fences."""
        out, pos, fence = [], 0, False
        for line in text.splitlines(keepends=True):
            if line.lstrip().startswith("```"):
                fence = not fence
            elif not fence:
                m = re.match(r"(#{1,6})\s+(.*?)\s*$", line)
                if m:
                    out.append((pos, len(m.group(1)), m.group(2)))
            pos += len(line)
        return out

    def _kit_span(self, text):
        heads = self.headings(text)
        i = _pick([h[2] for h in heads], self.selector, "heading")
        start, level, _ = heads[i]
        end = next((h[0] for h in heads[i + 1:] if h[1] <= level), len(text))
        return start, end

    # const: a module-level constant's value

    def _const_span(self, text):
        try:
            tree = ast.parse(text)
        except SyntaxError as e:
            raise TargetError(f"{self.path} doesn't parse: {e}") from None
        node = _module_assign(tree, self.selector)
        if node is None:
            raise TargetError(f"{self.path} has no module-level {self.selector}")
        v = node.value
        return _offset(text, v.lineno, v.col_offset), _offset(text, v.end_lineno, v.end_col_offset)

    # lib: a top-level function, with its decorators

    def _lib_span(self, text):
        try:
            tree = ast.parse(text)
        except SyntaxError as e:
            raise TargetError(f"{self.path} doesn't parse: {e}") from None
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == self.selector:
                first = min([node.lineno] + [d.lineno for d in node.decorator_list])
                return _line_start(text, first), _line_start(text, node.end_lineno + 1)
        raise TargetError(f"{self.path} has no function {self.selector}")

    # the checks

    def check(self, old_file, new_file, new):
        if self.kind == "prompt":
            _, _, quote, before = self._literal(old_file)
            if quote in new:
                raise TargetError(f"the paragraph can't hold {quote}")
            _, _, _, after = self._literal(new_file)
            if fields(before) != fields(after):
                raise TargetError(f"the prompt's format fields would change: {sorted(fields(before))} -> "
                                  f"{sorted(fields(after))}")
        elif self.kind == "kit":
            heads = self.headings(new.rstrip("\n") + "\n")
            level = self.headings(old_file)[_pick([h[2] for h in self.headings(old_file)], self.selector,
                                                  "heading")][1]
            if not heads or heads[0][0] != 0 or heads[0][1] != level:
                raise TargetError(f"the section must start with its heading ({'#' * level} ...)")
            if any(h[1] <= level for h in heads[1:]):
                raise TargetError("the section can't hold another heading as high as its own")
        elif self.kind == "const":
            try:
                ast.literal_eval(new)
            except (ValueError, SyntaxError):
                raise TargetError(f"{self.selector}'s value must be a Python literal: {new!r}") from None
        elif self.kind == "lib":
            try:
                tree = ast.parse(new)
            except SyntaxError as e:
                raise TargetError(f"the function doesn't parse: {e}") from None
            if len(tree.body) != 1 or not isinstance(tree.body[0], (ast.FunctionDef, ast.AsyncFunctionDef)) \
                    or tree.body[0].name != self.selector:
                raise TargetError(f"the change must be one top-level function named {self.selector}")
        if self.path.endswith(".py"):
            try:
                compile(new_file, self.path, "exec")
            except SyntaxError as e:
                raise TargetError(f"{os.path.basename(self.path)} wouldn't compile: {e}") from None


def fields(template):
    """The format fields of a str.format template."""
    try:
        return {f for _, f, _, _ in string.Formatter().parse(template) if f is not None}
    except ValueError:
        return {"<unbalanced braces>"}


def parse_target(spec, lib=None, repo=REPO):
    """A Target from `kind:selector[@file[#VAR]]` (the module's docstring). lib: the project's lib.py, for lib:fn."""
    if ":" not in spec:
        raise TargetError(f"{spec!r}: a target is kind:selector (prompt, kit, const or lib)")
    kind, rest = spec.split(":", 1)
    kind = kind.strip().lower()
    path = var = None
    if "@" in rest:
        rest, path = rest.rsplit("@", 1)
        if "#" in path:
            path, var = path.split("#", 1)
        path = os.path.abspath(os.path.join(repo, path)) if not os.path.isabs(path) else path
    selector = rest.strip()
    if not selector:
        raise TargetError(f"{spec!r}: say which {kind}")
    if kind == "prompt":
        path = path or SESSION_FILE
    elif kind == "kit":
        path = path or KIT_FILE
    elif kind == "const":
        if not path:
            if "." not in selector:
                raise TargetError(f"{spec!r}: const:module.NAME (e.g. kit.control.SONAR_EVERY), or const:NAME@file")
            module, selector = selector.rsplit(".", 1)
            parts = module.split(".")
            if parts[0] == "autopilot":
                parts = parts[1:]
            path = os.path.join(PACKAGE, *parts) + ".py"
    elif kind == "lib":
        path = path or lib
        if not path:
            raise TargetError(f"{spec!r}: which lib.py? Give --project (its memory's lib.py) or lib:fn@file")
    else:
        raise TargetError(f"{spec!r}: no target kind {kind!r} (prompt, kit, const or lib)")
    if not os.path.exists(path):
        raise TargetError(f"{spec!r}: {path} doesn't exist")
    t = Target(kind, selector, os.path.abspath(path), var, spec)
    t.span(t.read())  # (the selector must find its piece now)
    return t


def _rel(path, base):
    """path relative to base when it is inside it, else None (also across Windows drives, where relpath raises)."""
    try:
        rel = os.path.relpath(path, base)
    except ValueError:
        return None
    return None if rel == ".." or rel.startswith(".." + os.sep) else rel


def locked(path, also=(), repo=REPO):
    """Why path may not be a target (the bench, the tests, a regression, the suite, the log), or None."""
    real = os.path.realpath(path)
    for p in also:
        if not p:
            continue
        rp = os.path.realpath(p)
        if real == rp or real.startswith(rp.rstrip(os.sep) + os.sep):
            return f"{path} is locked (the suite or the experiments log)"
    rel = _rel(real, os.path.realpath(repo))
    if rel is not None:
        first = rel.split(os.sep)[0]
        if first in LOCKED_DIRS:
            return f"{path} is locked ({first}/ is the measure, not a target)"
    if "regressions" in real.split(os.sep):
        return f"{path} is locked (a regression)"
    return None


# -- the suite and its lock -----------------------------------------------------------------------------------------

def suite_hash(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def load_suite(path):
    with open(path, encoding="utf-8") as f:
        spec = json.load(f)
    return [{"id": c["id"], "prompt": c["prompt"], "expected": c.get("expected")} for c in spec.get("prompts", [])]


def lock_suite(suite, out, relock=False, name="suite.lock"):
    """Lock the suite (its hash in out/<name>), or check it against the lock: SuiteChanged when it differs."""
    lock = os.path.join(out, name)
    digest = suite_hash(suite)
    if os.path.exists(lock) and not relock:
        with open(lock, encoding="utf-8") as f:
            held = json.load(f)
        if held.get("sha256") != digest:
            raise SuiteChanged(f"the suite {suite} changed since it was locked ({held.get('locked')}): an experiment "
                               "log measures one suite. Use another --out, or --relock to start a new lock here.")
        return held
    held = {"suite": os.path.abspath(suite), "sha256": digest, "locked": _now()}
    with open(lock, "w", encoding="utf-8") as f:
        json.dump(held, f, indent=1)
    return held


# -- metrics and the decision ------------------------------------------------------------------------------------

def tokens_of(value):
    """A run's tokens as one number (a report's tokens: a number, or {input, output, reasoning, cache...})."""
    if isinstance(value, bool) or value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, dict):
        return sum(tokens_of(v) for v in value.values())
    return 0


def _summary(items):
    """One suite run's pass rate, seconds and tokens."""
    scored = [i for i in items if i.get("correct") is not None]
    return {"rate": round(sum(1 for i in scored if i["correct"]) / len(scored), 4) if scored else 0.0,
            "seconds": round(sum(float(i.get("seconds") or 0) for i in items), 2),
            "tokens": sum(tokens_of(i.get("tokens")) for i in items)}


def noise_of(per_run):
    """How much the unchanged suite moves from one run to the next (per_run: _summary() of each run): the spread
    (max - min) of the pass rate, and of time and tokens as a share of their mean. A change must beat it to count."""
    if len(per_run) < 2:
        return None

    def spread(key, relative):
        values = [r[key] for r in per_run]
        width = max(values) - min(values)
        mean = sum(values) / len(values)
        return round(width / mean, 4) if relative and mean else round(width, 4)

    return {"runs": len(per_run), "rate": spread("rate", False), "seconds": spread("seconds", True),
            "tokens": spread("tokens", True)}


def metrics(runs, regressions=None):
    """The suite's measure from its results (runs: one list of item results per repeat; an item result has id,
    correct (True/False/None: unscored), seconds, tokens, and a condition when the suite runs under several, e.g.
    cold and warm): pass rate, mean seconds and tokens per suite run, each run's own (per_run), and each
    condition's."""
    runs = [r for r in runs if r is not None]
    n = max(1, len(runs))
    scored = [i for r in runs for i in r if i.get("correct") is not None]
    passed = sum(1 for i in scored if i["correct"])
    m = {
        "passed": round(passed / n, 3), "total": round(len(scored) / n, 3),
        "rate": round(passed / len(scored), 4) if scored else 0.0,
        "seconds": round(sum(float(i.get("seconds") or 0) for r in runs for i in r) / n, 2),
        "tokens": round(sum(tokens_of(i.get("tokens")) for r in runs for i in r) / n),
        "items": {},
    }
    for r in runs:
        for i in r:
            it = m["items"].setdefault(i["id"], {"passed": 0, "runs": 0, "got": []})
            it["runs"] += 1
            it["passed"] += 1 if i.get("correct") else 0
            it["got"].append(i.get("got"))
            for k in ("expected", "prompt", "summary", "evidence", "condition"):
                if i.get(k) is not None:
                    it[k] = i[k]
    m["per_run"] = [_summary(r) for r in runs]
    conditions = sorted({i["condition"] for r in runs for i in r if i.get("condition")})
    if conditions:
        m["conditions"] = {}
        for c in conditions:
            sub = metrics([[{k: v for k, v in i.items() if k != "condition"} for i in r if i.get("condition") == c]
                           for r in runs])
            m["conditions"][c] = {k: sub[k] for k in ("passed", "total", "rate", "seconds", "tokens")}
    if regressions is not None:
        m["regressions"] = {"passed": regressions["passed"], "total": regressions["total"],
                            "failed": list(regressions["failed"])}
    return m


def _gain(before, after):
    return (before - after) / before if before else 0.0


def decide(base, cand, min_gain=MIN_GAIN, noise=None):
    """("keep" | "revert", why) for a candidate's metrics against the baseline's. noise (noise_of(), default the
    baseline's own): a pass rate must rise by more than its spread to count, and time or tokens must fall by more than
    theirs (and by min_gain at least); a pass rate that moved within its spread counts as equal, but never a fall."""
    noise = noise if noise is not None else (base.get("noise") or {})
    nr = noise.get("rate", 0.0)
    broke = sorted(set((cand.get("regressions") or {}).get("failed") or [])
                   - set((base.get("regressions") or {}).get("failed") or []))
    if broke:
        return "revert", f"breaks regression(s) {', '.join(broke)}"
    rates = f"pass rate {base['rate']:.0%} -> {cand['rate']:.0%}"
    if cand["rate"] > base["rate"] + nr + 1e-9:
        return "keep", rates + (f" (beyond its noise, {nr:.0%})" if nr else "")
    if cand["rate"] < base["rate"] - 1e-9:
        return "revert", rates + (" (within its noise, but a fall)" if cand["rate"] >= base["rate"] - nr - 1e-9
                                  else "")
    equal = "an equal pass rate" if cand["rate"] <= base["rate"] + 1e-9 else f"a pass rate within its noise ({rates})"
    ts, tt = max(min_gain, noise.get("seconds", 0.0)), max(min_gain, noise.get("tokens", 0.0))
    sg, tg = _gain(base["seconds"], cand["seconds"]), _gain(base["tokens"], cand["tokens"])
    ds, dt = 0.0 - sg, 0.0 - tg  # (the changes, as signed shares: never -0%)
    if sg >= ts and tg >= -tt:
        return "keep", f"time -{sg:.0%} at {equal}" + (f" (tokens {dt:+.0%})" if base["tokens"] else "")
    if tg >= tt and sg >= -ts:
        return "keep", f"tokens -{tg:.0%} at {equal} (time {ds:+.0%})"
    beyond = f" beyond the noise (time ±{ts:.0%}, tokens ±{tt:.0%})" if noise else ""
    return "revert", f"no gain{beyond} at {equal} (time {ds:+.0%}, tokens {dt:+.0%})"


# -- the log -------------------------------------------------------------------------------------------------------

MD_HEAD = ("# Experiments\n\nOne row per experiment (autopilot evolve: CONTRIBUTING.md, Evolve). pass: the suite's "
           "prompts that gave their expected answer; time and tokens: per suite run.\n\n"
           "| # | when | target | change | pass | time (s) | tokens | regressions | decision | why |\n"
           "|---|---|---|---|---|---|---|---|---|---|\n")


def _now():
    return datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _cell(text, limit=90):
    text = " ".join(str(text or "").split()).replace("|", "\\|")
    return text if len(text) <= limit else text[:limit - 1] + "…"


class Log:
    """The experiments log in a folder: experiments.jsonl (a record per line), experiments.md (a table row each) and
    diffs/<n>.diff."""

    def __init__(self, out):
        self.out = out
        os.makedirs(os.path.join(out, "diffs"), exist_ok=True)
        self.jsonl = os.path.join(out, "experiments.jsonl")
        self.md = os.path.join(out, "experiments.md")

    def records(self):
        out = []
        try:
            with open(self.jsonl, encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        out.append(json.loads(line))
        except OSError:
            pass
        return out

    def next_n(self):
        return max([r.get("n", 0) for r in self.records()] + [0]) + 1

    def add(self, record):
        record = dict(record, n=self.next_n())
        if record.get("diff"):
            name = f"{record['n']:04d}.diff"
            with open(os.path.join(self.out, "diffs", name), "w", encoding="utf-8") as f:
                f.write(record["diff"])
            record["diff_file"] = f"diffs/{name}"
        with open(self.jsonl, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        new = not os.path.exists(self.md)
        with open(self.md, "a", encoding="utf-8") as f:
            if new:
                f.write(MD_HEAD)
            f.write(self.row(record))
        return record

    @staticmethod
    def row(r):
        def pas(m):
            return f"{m['passed']:g}/{m['total']:g}" if m else "-"

        base, cand = r.get("baseline"), r.get("metrics")
        if r["kind"] == "baseline":
            p, s, t = pas(cand), f"{cand['seconds']:g}", f"{cand['tokens']:g}"
        elif cand and base:
            p = f"{pas(base)} → {pas(cand)}"
            s = f"{base['seconds']:g} → {cand['seconds']:g}"
            t = f"{base['tokens']:g} → {cand['tokens']:g}"
        else:
            p = s = t = "-"
        regs = (cand or {}).get("regressions")
        reg = f"{regs['passed']}/{regs['total']}" if regs else "-"
        change = _cell(r.get("note") or "", 60)
        if r.get("diff_file"):
            change = (change + " " if change else "") + f"[diff]({r['diff_file']})"
        return (f"| {r['n']} | {r['when']} | {_cell(r.get('target') or '-', 40)} | {change or '-'} | {p} | {s} | {t} "
                f"| {reg} | **{r['decision']}** | {_cell(r.get('why'))} |\n")


# -- runners -------------------------------------------------------------------------------------------------------

@dataclass
class BenchRunner:
    """The suite through bench/run_autopilot.py (each prompt's own brain and headless game): a list of item results.
    lib: a lib.py each prompt's memory starts with."""

    project: str
    suite: str
    parallel: int = 2
    minutes: float = 30
    model: Optional[str] = None
    agent: Optional[str] = None
    codex: Optional[str] = None
    lib: Optional[str] = None
    engine: Optional[str] = None
    memory: Optional[str] = None  # a project's memory folder every prompt starts as a copy of (warm); None: cold
    python: str = sys.executable
    bench: str = os.path.join(REPO, "bench", "run_autopilot.py")

    def __call__(self, out):
        cmd = [self.python, self.bench, "--project", self.project, "--suite", self.suite, "--parallel",
               str(self.parallel), "--minutes", str(self.minutes), "--out", out, "--quiet"]
        for flag, value in (("--model", self.model), ("--agent", self.agent), ("--codex", self.codex),
                            ("--lib", self.lib), ("--engine", self.engine), ("--memory", self.memory)):
            if value:
                cmd += [flag, value]
        subprocess.run(cmd, cwd=REPO, check=False)
        with open(os.path.join(out, "summary.json"), encoding="utf-8") as f:
            return json.load(f)["results"]


@dataclass
class Conditions:
    """Several runners as one suite run, each a condition (e.g. cold: a fresh memory; warm: a frozen memory's copy):
    their items, ids prefixed with the condition's name ("cold:A", "warm:A") and marked with it."""

    runners: list  # [(name, runner)]

    def __call__(self, out):
        items = []
        for name, run in self.runners:
            here = os.path.join(out, name)
            os.makedirs(here, exist_ok=True)
            items += [dict(i, id=f"{name}:{i['id']}", condition=name) for i in run(here)]
        return items


@dataclass
class RegressionRunner:
    """The regressions through `autopilot regress` (its own brain and headless game): regress.summary()'s dict."""

    project: str
    regressions: str
    engine: Optional[str] = None
    minutes: float = 30  # (a game that never connects would leave `autopilot regress` waiting: the measure fails)
    python: str = sys.executable

    def __call__(self, out):
        result = os.path.join(out, "regressions.json")
        import socket

        with socket.socket() as s:  # (a port of its own: an Autopilot may be serving on the default one)
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        cmd = [self.python, "-m", "autopilot", "regress", "--project", self.project, "--regressions",
               self.regressions, "--json", result, "--port", str(port)]
        if self.engine:
            cmd += ["--engine", self.engine]
        subprocess.run(cmd, cwd=REPO, check=False, timeout=self.minutes * 60)
        with open(result, encoding="utf-8") as f:
            return json.load(f)


# -- proposals -----------------------------------------------------------------------------------------------------

PROPOSE_SYSTEM = """You improve one piece of Autopilot, an agent that playtests games: it writes a harness for a fast
decision model and runs it against the live game. A fixed bench suite measures it: the share of prompts that end with
their expected answer, then the time and tokens they take. You change one piece at a time; the change is kept only if
the suite does better, else reverted. Propose one change to the piece you are shown. Small, specific changes that
address the suite's misses beat rewrites: start from the biggest group of misses, and from the maintainers' notes on
where Autopilot fails when there are some; don't repeat a change the log shows was reverted. Answer with one line
`NOTE: <what the change does and why>`, then the whole new piece in one fenced block (```), nothing after it."""


def proposal_prompt(target, current, history, base, direction=None):
    kinds = {"prompt": "a paragraph of the agent's system prompt (a Python str.format template: keep its {fields} "
                       "and double braces as they are)",
             "kit": "a section of KIT.md, the kit's reference the agent reads (keep its heading)",
             "const": "the value of a constant of the kit's controllers (a Python literal)",
             "lib": "a function of the project's lib.py, harness code earlier runs kept (keep its name and signature)"}
    lines = [f"The piece: {target.spec} ({kinds[target.kind]}).", "", "It is now:", "```", current.rstrip("\n"), "```",
             "", f"The suite now: {base['passed']:g}/{base['total']:g} passed, {base['seconds']:g} s, "
                 f"{base['tokens']:g} tokens a run."]
    for c, cm in (base.get("conditions") or {}).items():
        lines.append(f"- {c}: {cm['passed']:g}/{cm['total']:g} passed, {cm['seconds']:g} s, {cm['tokens']:g} tokens")
    if direction and direction.strip():
        lines += ["", "Where to look: the maintainers' own notes on where Autopilot fails. They come first: a change "
                      "that follows them beats one that only chases the numbers.", direction.strip()]
    misses = [(k, v) for k, v in base.get("items", {}).items() if v.get("passed", 0) < v.get("runs", 0)]
    if misses:
        groups = {}  # (what was expected, what came instead) -> the misses that went that way
        for k, v in misses:
            wrong = sorted({str(g) for g in v.get("got") or []} - {str(v.get("expected"))})
            groups.setdefault((str(v.get("expected")), ", ".join(wrong) or "?"), []).append((k, v))
        lines.append("Its misses, grouped by how they went wrong (biggest group first):")
        for (expected, got), group in sorted(groups.items(), key=lambda g: -len(g[1])):
            lines.append(f"- expected {expected}, got {got} ({len(group)}):")
            for k, v in group:
                lines.append(f"  - {k}: {v.get('prompt', '')!r}"
                             + (f": {_cell(v.get('summary'), 300)}" if v.get("summary") else ""))
                for e in (v.get("evidence") or [])[:2]:
                    lines.append(f"    - {_cell(e, 200)}")
    tried = [r for r in history if r.get("target") == target.spec and r["kind"] == "experiment"]
    if tried:
        lines += ["", "Tried on this piece before:"]
        for r in tried[-8:]:
            lines.append(f"- {r['decision']} ({r.get('why')}): {r.get('note') or ''}\n" + _cut(r.get("diff"), 1500))
    return "\n".join(lines)


def _cut(text, n):
    text = text or ""
    return text if len(text) <= n else text[:n] + "\n..."


def parse_proposal(answer):
    """(text, note) out of a model's answer: its NOTE line and its last fenced block."""
    note = ""
    m = re.search(r"(?im)^\s*NOTE:\s*(.+)$", answer)
    if m:
        note = m.group(1).strip()
    blocks = re.findall(r"```[^\n]*\n(.*?)```", answer, re.S)
    if not blocks:
        raise TargetError("the proposal has no fenced block")
    return blocks[-1], note


class ModelProposer:
    """Changes from a language model (llm.Model's route, the agent's provider), or from ask(prompt, system) given."""

    def __init__(self, ask=None, provider=None, direction=None):
        self.direction = direction
        if ask is None:
            from . import llm
            from .agents import session

            provider = provider or (os.environ.get("AUTOPILOT_PROVIDER") or "anthropic").strip()
            model = llm.Model(provider, agent_model=session.model(provider))
            ask = model.ask
        self.ask = ask

    def __call__(self, target, current, history, base):
        return parse_proposal(self.ask(proposal_prompt(target, current, history, base, self.direction),
                                       system=PROPOSE_SYSTEM))


# -- the loop ------------------------------------------------------------------------------------------------------

@dataclass
class Experiment:
    """One change to try: text (the target's new piece) or propose(target, current, history, baseline) -> (text,
    note)."""

    target: Target
    text: Optional[str] = None
    note: str = ""
    propose: Optional[Callable] = None


@dataclass
class Evolve:
    """The loop. run_suite(out) -> item results (one suite run); run_regressions(out) -> regress.summary() or None."""

    suite: str
    out: str
    run_suite: Callable
    run_regressions: Optional[Callable] = None
    repeats: int = 1
    min_gain: float = MIN_GAIN
    relock: bool = False
    say: Callable = print
    baseline: Optional[dict] = field(default=None)
    noise_runs: int = 0  # the baseline is measured this many times (2+) and its spread is the bar a change must clear
    confirm: bool = False  # a change that wins is measured again, and kept only if it wins again
    holdout: Optional[str] = None  # a second suite (locked too) evolve never proposes from: a kept change can't...
    run_holdout: Optional[Callable] = None  # ... do worse on it (run_holdout(out) -> item results)

    def __post_init__(self):
        os.makedirs(self.out, exist_ok=True)
        self.log = Log(self.out)
        self.lock = None
        self.holdout_base = None
        self.recover()

    # -- safety

    def recover(self):
        """Put back a file an experiment had changed when the loop was killed mid-experiment."""
        pending = os.path.join(self.out, PENDING)
        if not os.path.exists(pending):
            return None
        with open(pending, encoding="utf-8") as f:
            held = json.load(f)
        _write(held["path"], held["original"])
        os.remove(pending)
        self.say(f"Put back {held['path']} as it was before experiment {held.get('n')} (the loop was stopped in it)")
        return held["path"]

    def _pending(self, target, original, n):
        with open(os.path.join(self.out, PENDING), "w", encoding="utf-8") as f:
            json.dump({"path": target.path, "original": original, "n": n, "since": _now()}, f)

    def _done_pending(self):
        try:
            os.remove(os.path.join(self.out, PENDING))
        except OSError:
            pass

    def check_lock(self):
        first = self.lock is None
        self.lock = lock_suite(self.suite, self.out, relock=self.relock and first)
        if self.holdout:
            lock_suite(self.holdout, self.out, relock=self.relock and first, name="holdout.lock")

    # -- measuring

    def measure(self, label, times=None, runner=None, regressions=True):
        """The suite (or runner's) metrics over `times` runs (default --repeats), with the regressions replayed. The
        runs' items are kept under "_runs" (for merging measures; never logged)."""
        folder = os.path.join(self.out, "runs", f"{time.strftime('%Y%m%d-%H%M%S')}-{label}")
        times = max(1, times or self.repeats)
        runs = []
        for k in range(times):
            here = os.path.join(folder, f"r{k + 1}") if times > 1 else folder
            os.makedirs(here, exist_ok=True)
            runs.append((runner or self.run_suite)(here))
        regs = None
        if regressions and self.run_regressions is not None:
            os.makedirs(folder, exist_ok=True)
            regs = self.run_regressions(folder)
        m = metrics(runs, regs)
        m["runs_dir"] = os.path.relpath(folder, self.out)
        m["_runs"] = runs
        return m

    # -- the experiments

    def start(self):
        self.check_lock()
        if self.baseline is None:
            times = max(self.repeats, self.noise_runs if self.noise_runs >= 2 else 0)
            self.say("Measuring the baseline" + (f" ({times} runs, for the noise floor)" if times > 1 else ""))
            self.baseline = self.measure("baseline", times=times)
            noise = noise_of(self.baseline["per_run"]) if self.noise_runs >= 2 else None
            if noise:
                self.baseline["noise"] = noise
            why = "the suite as the code is now"
            if noise:
                why += (f"; noise over {noise['runs']} runs: pass rate ±{noise['rate']:.0%}, "
                        f"time ±{noise['seconds']:.0%}, tokens ±{noise['tokens']:.0%}")
            record = {"kind": "baseline", "when": _now(), "suite_sha256": self.lock["sha256"],
                      "metrics": _public(self.baseline), "decision": "baseline", "why": why}
            if self.run_holdout is not None:
                self.holdout_base = self.measure("holdout-baseline", runner=self.run_holdout, regressions=False)
                record["holdout"] = _public(self.holdout_base)
            self.log.add(record)
            self.say(self._line(self.baseline))
        return self.baseline

    @staticmethod
    def _line(m):
        regs = m.get("regressions")
        return (f"  {m['passed']:g}/{m['total']:g} passed, {m['seconds']:g} s, {m['tokens']:g} tokens"
                + (f", regressions {regs['passed']}/{regs['total']}" if regs else ""))

    def try_one(self, exp):
        """Run one experiment: its log record."""
        base = self.start()
        t = exp.target
        record = {"kind": "experiment", "when": _now(), "target": t.spec or f"{t.kind}:{t.selector}",
                  "file": (_rel(t.path, REPO) or t.path).replace(os.sep, "/"), "note": exp.note,
                  "baseline": _short(base), "suite_sha256": self.lock["sha256"]}
        why_locked = locked(t.path, also=(self.suite, self.out, self.holdout))
        if why_locked:
            return self.log.add(dict(record, decision="refused", why=why_locked))
        original = t.raw()
        current = t.current()
        text, note = exp.text, exp.note
        if text is None and exp.propose is not None:
            try:
                text, note = exp.propose(t, current, self.log.records(), base)
            except Exception as e:
                return self.log.add(dict(record, decision="error", why=f"no proposal: {type(e).__name__}: {e}"))
        record["note"] = note
        if text is None or text.strip() == current.strip():
            return self.log.add(dict(record, decision="skipped", why="no change to the target"))
        try:
            new_file = t.render(text)
        except TargetError as e:
            return self.log.add(dict(record, decision="invalid", why=str(e)))
        rel = record["file"]
        record["diff"] = "".join(difflib.unified_diff(t.read().splitlines(keepends=True),
                                                      new_file.replace("\r\n", "\n").splitlines(keepends=True),
                                                      f"a/{rel}", f"b/{rel}"))
        n = self.log.next_n()
        self.say(f"Experiment {n}: {record['target']}" + (f" ({note})" if note else ""))
        self._pending(t, original, n)
        decision, why, cand = "revert", "", None
        try:
            _write(t.path, new_file)
            cand = self.measure(f"exp{n}")
            decision, why = decide(base, cand, self.min_gain)
            if decision == "keep" and self.confirm:  # (a win must win again: one lucky run isn't a change)
                again = self.measure(f"exp{n}-confirm")
                record["confirm"] = _public(again)
                second, why2 = decide(base, again, self.min_gain)
                if second != "keep":
                    decision, why = "revert", f"not confirmed: {why2} (the first run: {why})"
                else:
                    cand = metrics(cand["_runs"] + again["_runs"], again.get("regressions"))
                    cand["_runs"] = []
                    why += f"; confirmed ({why2})"
            if decision == "keep" and self.run_holdout is not None:  # (out of sample: never worse there)
                hold = self.measure(f"exp{n}-holdout", runner=self.run_holdout, regressions=False)
                record["holdout"] = _public(hold)
                hb, nr = self.holdout_base, (base.get("noise") or {}).get("rate", 0.0)
                if hold["rate"] < hb["rate"] - nr - 1e-9:
                    decision, why = "revert", (f"worse on the held-out suite ({hb['rate']:.0%} -> {hold['rate']:.0%})"
                                               f", though {why}")
                else:
                    self.holdout_base = hold
                    why += f"; held-out suite {hb['rate']:.0%} -> {hold['rate']:.0%}"
        except KeyboardInterrupt:
            _write(t.path, original)
            self._done_pending()
            raise
        except Exception as e:
            decision, why = "error", f"the suite failed: {type(e).__name__}: {e}"
        finally:
            if decision != "keep":
                _write(t.path, original)
            self._done_pending()
        self.check_lock()  # (the measure itself must not have moved)
        if decision == "keep":
            if base.get("noise"):
                cand["noise"] = base["noise"]  # (the suite's noise is the suite's, whatever the code)
            self.baseline = cand
        self.say(f"  {decision}: {why}")
        return self.log.add(dict(record, metrics=_public(cand), decision=decision, why=why))

    def run(self, experiments):
        """Every experiment in turn (an iterable: a proposer's may depend on the log so far); their records."""
        self.start()
        return [self.try_one(e) for e in experiments]


def _public(m):
    """Metrics as logged: without the runs' raw items."""
    return {k: v for k, v in m.items() if k != "_runs"} if m else m


def _short(m):
    return {k: v for k, v in (m or {}).items() if k in ("passed", "total", "rate", "seconds", "tokens", "regressions")}


def _write(path, text):
    """Write text to path in one step (a temporary file beside it, then a rename)."""
    folder = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".evolve-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        os.replace(tmp, path)
        if path.endswith(".py"):  # (bytecode is checked by mtime in seconds and size: a quick same-size edit
            import importlib.util  # could run the old code)

            try:
                os.remove(importlib.util.cache_from_source(path))
            except OSError:
                pass
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


# -- the command ---------------------------------------------------------------------------------------------------

def load_candidates(path, lib=None):
    """Experiments from a candidates file: {"experiments": [{"target", "text" | "file", "note"}]} (or the list)."""
    with open(path, encoding="utf-8") as f:
        spec = json.load(f)
    items = spec.get("experiments", []) if isinstance(spec, dict) else spec
    out = []
    for c in items:
        text = c.get("text")
        if text is None and c.get("file"):
            with open(os.path.join(os.path.dirname(os.path.abspath(path)), c["file"]), encoding="utf-8") as f:
                text = f.read()
        out.append(Experiment(parse_target(c["target"], lib=lib), text=text, note=c.get("note", "")))
    return out


def project_lib(project):
    """The project's lib.py in memory (its memory folder named as the engine names the project)."""
    if not project:
        return None
    from . import codex, engines

    eng, path = engines.find_project(project)
    if not eng:
        return None
    return os.path.join(codex.project_dir(eng.project_name(path)), "lib.py")


def main(args):
    lib = args.lib or project_lib(args.project)
    if args.show:
        try:
            t = parse_target(args.show, lib=lib)
        except TargetError as e:
            print(e)
            return 1
        print(f"# {t.spec} in {t.path}")
        print(t.current())
        return 0
    if not args.suite:
        print("--suite: the bench suite the experiments are measured by")
        return 1
    try:
        experiments = load_candidates(args.candidates, lib=lib) if args.candidates else []
        direction = None
        if args.direction:
            with open(args.direction, encoding="utf-8") as f:
                direction = f.read()
        if args.target:
            proposer = ModelProposer(direction=direction)
            for spec in args.target:
                t = parse_target(spec, lib=lib)
                experiments += [Experiment(t, propose=proposer) for _ in range(max(1, args.propose))]
    except (TargetError, OSError, ValueError, KeyError) as e:
        print(f"Can't start: {e}")
        return 1
    if not experiments:
        print("No experiments: give --candidates <file>, or --target <spec> (with --propose N) for a model's")
        return 1
    if not args.project:
        print("--project: the game the suite runs on")
        return 1
    if args.regressions:
        import importlib.util

        if importlib.util.find_spec("autopilot.regress") is None:  # (autopilot regress: a change of its own)
            print("--regressions replays them with `autopilot regress`, which this Autopilot doesn't have")
            return 1
    if args.cold_and_warm and not args.memory:
        print("--cold-and-warm: give --memory, the frozen memory folder the warm runs start from")
        return 1
    if args.memory and not os.path.isdir(args.memory):
        print(f"--memory: {args.memory} isn't a folder")
        return 1
    out = args.out or os.path.join(REPO, "bench", "evolve")

    def runner_for(suite):
        def bench(memory):
            return BenchRunner(args.project, suite, parallel=args.parallel, minutes=args.minutes, model=args.model,
                               agent=args.agent, codex=args.codex, lib=lib if lib and os.path.exists(lib) else None,
                               engine=args.engine, memory=memory)

        if args.cold_and_warm:
            return Conditions([("cold", bench(None)), ("warm", bench(args.memory))])
        return bench(args.memory)

    regs = RegressionRunner(args.project, args.regressions, engine=args.engine) if args.regressions else None
    try:
        loop = Evolve(args.suite, out, runner_for(args.suite), regs, repeats=args.repeats, min_gain=args.min_gain,
                      relock=args.relock, noise_runs=args.noise, confirm=not args.no_confirm, holdout=args.holdout,
                      run_holdout=runner_for(args.holdout) if args.holdout else None)
        records = loop.run(experiments)
    except SuiteChanged as e:
        print(e)
        return 1
    kept = sum(1 for r in records if r["decision"] == "keep")
    print(f"{len(records)} experiment(s), {kept} kept. The log: {os.path.join(out, 'experiments.md')}")
    return 0


def add_parser(sub):
    e = sub.add_parser("evolve", help="improve Autopilot's prompt, kit, constants or lib.py against a locked bench "
                                      "suite: keep each change only if the suite does better (CONTRIBUTING.md, Evolve)")
    e.add_argument("--suite", help="the bench suite (JSON) that measures each experiment; locked on the first run")
    e.add_argument("--project", help="the game's project the suite runs on")
    e.add_argument("--target", action="append", help="a target for a model's proposals: prompt:<n|words>, "
                                                     "kit:<heading>, const:<module.NAME>, lib:<function> "
                                                     "([@file]; repeatable)")
    e.add_argument("--propose", type=int, default=1, help="proposals per --target (default 1)")
    e.add_argument("--candidates", help="a JSON file of experiments: {\"experiments\": [{\"target\", \"text\" | "
                                        "\"file\", \"note\"}]}")
    e.add_argument("--show", metavar="TARGET", help="print a target's current text and exit")
    e.add_argument("--lib", help="the lib.py for lib: targets (default: the project's, in memory/)")
    e.add_argument("--parallel", type=int, default=2, help="suite prompts at a time (default 2)")
    e.add_argument("--repeats", type=int, default=1, help="suite runs per measure, averaged (default 1)")
    e.add_argument("--min-gain", type=float, default=MIN_GAIN,
                   help=f"the share time or tokens must fall by at an equal pass rate (default {MIN_GAIN})")
    e.add_argument("--regressions", help="a regressions folder (autopilot regress) also replayed per measure: a "
                                         "change that breaks one is reverted")
    e.add_argument("--noise", type=int, default=3, metavar="N",
                   help="measure the baseline N times; a change must beat that spread to count (default 3; 0: off)")
    e.add_argument("--no-confirm", action="store_true",
                   help="keep a change that wins once (default: it is measured again and must win again)")
    e.add_argument("--holdout", metavar="SUITE",
                   help="a second suite (locked too) proposals never see: a kept change can't do worse on it")
    e.add_argument("--memory", metavar="DIR",
                   help="a project's memory folder (memory/<project>) every prompt starts as a copy of: warm runs, "
                        "from the same frozen memory each time")
    e.add_argument("--cold-and-warm", action="store_true",
                   help="measure both: each suite run plays every prompt cold (a fresh memory) and warm (--memory)")
    e.add_argument("--direction", metavar="FILE",
                   help="your notes on where Autopilot fails, given to the model's proposals before the numbers")
    e.add_argument("--out", help="the experiments log's folder (default bench/evolve)")
    e.add_argument("--relock", action="store_true", help="lock the suite anew (it changed)")
    e.add_argument("--minutes", type=float, default=30, help="each prompt's time (the bench's --minutes)")
    e.add_argument("--model", help="the agent's model (the bench's --model)")
    e.add_argument("--agent", choices=["opencode", "claude", "codex"], help="the agent (the bench's --agent)")
    e.add_argument("--codex", help="a codex folder each prompt's memory starts with (the bench's --codex)")
    e.add_argument("--engine", help="the game's executable (the bench's --engine)")
    return e
