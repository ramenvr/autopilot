"""The background researcher: while the agent works (and above all while its harness plays), a model reads the game's
code for it, the agent's own file tools' way (grep, read, list), and writes down what it finds. Its findings reach
the agent with its next tool result (Session.news), so what was learned while episode N played goes into harness
N+1 of the same run.

It never acts on the game (the harness is driving it) and writes nothing but its findings (runs/<id>/findings.md):
the harness, notes.md and lib.py are the agent's. Its time isn't the run's reading and probing time. It works on
events, one pass each (several waiting at once are one pass):

    a harness starts playing   its assumptions held against the code while it plays
    an episode ended           what the episode raised (its summary), answered from the code
    files arrived              the export's new files that bear on the request or the harness (codex.Indexer)

Each pass is a fresh conversation (the request, the harness as it is, notes.md, the findings so far and the event),
of at most TURNS model turns. The user chooses on the plugin's Settings page (the hello's `research`, from
preferences.json research_agent); until they do, runs from the Aura app's Autopilot go without it and runs from the
user's own (the plugin's agent service, signed in to Aura or on their own keys) have it. AUTOPILOT_RESEARCHER=0 turns it
off whatever was chosen.
"""

import fnmatch
import os
import queue
import re
import threading
import time

TURNS = 20  # model turns one pass may take (a pass is a few greps and reads, then the findings)
READ_LINES = 2000  # lines one read_file gives, from `offset` (the rest is read with a later offset)
GREP_FILE = 2_000_000  # files bigger than this aren't grepped (a map's whole actor list)
EVENT_TEXT = 60_000  # characters of an event (an episode's summary, the files that arrived) put in the pass's
                     # request: more goes whole to a file of the run the pass is pointed at (the model's context)

SYSTEM = """You read a video game's code in the background for an engineer who is writing, running and fixing a
harness: a program that plays the game for a request (a goal to reach, a claim to check, an exploration). The engineer
is busy with the live game; you read for them. You can't act on the game. You see the game's folder: codex/ (the
game's code and data as text: blueprints/, data/, input/, source/, config/, maps.txt, level.txt, README.md), notes.md,
lib.py, and runs/ (this run's folder has the harness, each episode's harnessN.py and traceN_summary.txt).

Find what in the code bears on the request and on what the harness does: the mechanics it touches (what an input
does, what a function needs, range, cooldowns, damage, health, conditions to succeed, what ends or blocks the player),
exact names to call or read, and numbers. When an episode ended, explain from the code what it showed: why an option
failed, what the game needed that the harness didn't do. When a harness starts playing, check what it assumes
against the code.

Each finding: add_finding with the fact, what it means for the harness, and the files (codex paths) it rests on. One
fact per finding, exact and checkable. Don't repeat the findings listed, nor what the harness already does right.
Say nothing you didn't read. When there is nothing new, stop without a finding."""

TOOLS = (
    ("grep", "Lines matching a regex in the game's folder (or one file or folder of it, `path`), as path:line: text.",
     {"type": "object", "properties": {"pattern": {"type": "string"}, "path": {"type": "string"}},
      "required": ["pattern"]}),
    ("read_file", f"A file of the game's folder, numbered lines from `offset` (from 1), {READ_LINES} at a time.",
     {"type": "object", "properties": {"path": {"type": "string"}, "offset": {"type": "integer"}},
      "required": ["path"]}),
    ("list_files", "The game's folder's files whose paths match a glob, e.g. codex/blueprints/**/BP_*.txt.",
     {"type": "object", "properties": {"glob": {"type": "string"}}, "required": ["glob"]}),
    ("add_finding", "Write down one finding for the engineer: the fact, what it means for the harness, its files.",
     {"type": "object", "properties": {"text": {"type": "string"},
                                       "files": {"type": "array", "items": {"type": "string"}}},
      "required": ["text"]}),
)


def enabled(hello=None):
    """Whether a run has the researcher: what the user chose in the plugin (hello `research`), else off in the Aura
    app's Autopilot (aura.from_app) and on in the user's own; never with AUTOPILOT_RESEARCHER=0."""
    from .. import aura

    if (os.environ.get("AUTOPILOT_RESEARCHER") or "").strip() == "0":
        return False
    chosen = (hello or {}).get("research")
    if isinstance(chosen, bool):
        return chosen
    return not aura.from_app()


class Researcher:
    """session: the run's Session (its findings, notes and activity); model: an llm.Model."""

    def __init__(self, session, model):
        self.session = session
        self.model = model
        self.root = os.path.realpath(session.root)
        self.events = queue.Queue()
        self.stopped = threading.Event()
        self.passes = 0
        self.thread = threading.Thread(target=self._loop, name="researcher", daemon=True)
        self.thread.start()

    # -- events (any thread) ------------------------------------------------------------------------------------

    def playing(self, n):
        """Harness n starts playing (episode n)."""
        self.events.put(("playing", n, None))

    def episode(self, n, summary):
        """Episode n ended; summary: its trace summary."""
        self.events.put(("episode", n, summary))

    def arrived(self, batch):
        """An Indexer's listener: a batch of files arrived (None: the export is in)."""
        if batch is not None and self._bears(batch):
            self.events.put(("files", batch["n"], batch))

    def stop(self):
        self.stopped.set()
        self.events.put(None)

    # -- the passes ----------------------------------------------------------------------------------------------

    def _bears(self, batch):
        """Whether a batch has files that bear on the request or on what the harness, notes.md or lib.py name: a
        request word or a written name in the file's path or digest line."""
        words = self.session.request_words()
        written = " ".join(self.session.written_now().values())
        for f in batch["files"]:
            low = (f["path"] + " " + f["line"]).lower()
            stem = os.path.splitext(os.path.basename(f["path"]))[0]
            if any(w in low for w in words) or (len(stem) >= 4 and re.search(rf"\b{re.escape(stem)}\b", written)):
                return True
        return False

    def _loop(self):
        while not self.stopped.is_set():
            first = self.events.get()
            if first is None:
                return
            events = [first]
            while True:  # (what waits now is one pass)
                try:
                    more = self.events.get_nowait()
                except queue.Empty:
                    break
                if more is None:
                    return
                events.append(more)
            try:
                self._pass(events)
            except Exception as e:
                self.session.note(f"researcher: the pass failed: {type(e).__name__}: {e}")

    def _event_text(self, events):
        parts = []
        for kind, n, data in events:
            if kind == "playing":
                parts.append(f"Harness {n} (runs/{self.session.run_id}/harness{n}.py, the harness.py above) is "
                             "playing now: check what it assumes against the code.")
            elif kind == "episode":
                parts.append(f"Episode {n} just ended. Its summary (runs/{self.session.run_id}/trace{n}_summary.txt):"
                             f"\n{data}")
            elif kind == "files":
                lines = "".join(f"{'+' if f['kind'] == 'new' else '~'} codex/{f['path']}  {f['line']}\n"
                                for f in data["files"])
                parts.append(f"The export brought these files (+ new, ~ changed), with what each is:\n{lines}")
        text = "\n\n".join(parts)
        if len(text) > EVENT_TEXT:  # (whole, in a file the pass reads as it needs)
            path = os.path.join(self.session.run_dir, "results", f"researcher-event-{time.time_ns()}.txt")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            rel = os.path.relpath(path, self.root).replace(os.sep, "/")
            text = (f"What happened is longer than this message holds: it is whole in {rel} ({len(text)} characters). "
                    "Read it there (read_file, grep).")
        return text

    def _pass(self, events):
        if self.model is None or self.model.route is None:
            return
        self.passes += 1
        n = self.passes
        s = self.session
        written = s.written_now()
        harness = written.get("harness.py") or "(none yet)"
        findings = s.findings_text() or "(none yet)"
        notes, lib = written.get("notes.md") or "(none)", written.get("lib.py") or "(none)"
        prompt = (f"The request: {s.prompt}\n\n## The harness now ({s.run_rel}/harness.py)\n{harness}\n\n"
                  f"## notes.md\n{notes}\n\n## lib.py\n{lib}\n\n## Findings so far\n{findings}\n\n"
                  f"## What happened\n{self._event_text(events)}")
        what = ", ".join(f"{k} {i}" for k, i, _ in events)
        s.note(f"researcher pass {n} ({self.model.name}): {what}")
        messages = [{"role": "user", "content": prompt}]
        found = []
        t0 = time.time()
        for _ in range(TURNS):
            if self.stopped.is_set():
                break
            reply = self.model.turn(messages, system=SYSTEM, tools=TOOLS)
            messages.append(reply["message"])
            if not reply["calls"]:
                break
            for call in reply["calls"]:
                answer = self._tool(call["name"], call.get("args") or {}, events, found)
                messages.append({"role": "tool", "id": call["id"], "content": answer})
        s.note(f"researcher pass {n}: {len(found)} findings in {time.time() - t0:.0f} s")
        if found:
            s.activity("read", f"Read the game's code in the background: {len(found)} "
                       f"thing{'s' if len(found) != 1 else ''} found",
                       detail="\n\n".join(f["text"] for f in found), log=f"researcher pass {n} found {len(found)}")

    def _tool(self, name, args, events, found):
        try:
            if name == "grep":
                return self._grep(str(args.get("pattern") or ""), args.get("path"))
            if name == "read_file":
                return self._read(str(args.get("path") or ""), int(args.get("offset") or 1))
            if name == "list_files":
                return self._list(str(args.get("glob") or "**/*"))
            if name == "add_finding":
                kind, n, _ = events[-1]
                source = {"playing": f"while harness {n} played", "episode": f"from episode {n}",
                          "files": "from files the export brought"}[kind]
                finding = self.session.add_finding(str(args.get("text") or ""), args.get("files") or [], source)
                found.append(finding)
                return f"Written down as finding {finding['n']}."
            return f"No tool {name}."
        except Exception as e:
            return f"{name} failed: {type(e).__name__}: {e}"

    def _path(self, rel):
        full = os.path.realpath(os.path.join(self.root, *str(rel).replace("\\", "/").split("/")))
        if not (full == self.root or full.startswith(self.root + os.sep)):
            raise ValueError("only the game's folder can be read")
        if any(part.startswith(".") and part != ".indexing" for part in os.path.relpath(full, self.root).split(os.sep)
               if part not in (".", "")):
            raise ValueError("the game's folder's hidden files (the agent's settings) aren't for reading")
        return full

    def _files(self, base):
        if os.path.isfile(base):
            return [base]
        return [os.path.join(f, n) for f, dirs, names in os.walk(base) for n in sorted(names)
                if not any(part.startswith(".") for part in os.path.relpath(f, self.root).split(os.sep) if part != ".")]

    def _grep(self, pattern, path=None):
        rx = re.compile(pattern)
        out = []
        for full in self._files(self._path(path or ".")):
            try:
                if os.path.getsize(full) > GREP_FILE:
                    continue
                with open(full, encoding="utf-8", errors="replace") as f:
                    for i, line in enumerate(f, 1):
                        if rx.search(line):
                            out.append(f"{os.path.relpath(full, self.root).replace(os.sep, '/')}:{i}: {line.rstrip()}")
            except OSError:
                continue
        return self._kept("\n".join(out) or "(no match)", "grep")

    def _read(self, rel, offset=1):
        with open(self._path(rel), encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
        start = max(1, offset)
        chunk = lines[start - 1:start - 1 + READ_LINES]
        text = "\n".join(f"{start + i}\t{line}" for i, line in enumerate(chunk))
        if start - 1 + READ_LINES < len(lines):
            text += f"\n({len(lines)} lines: read on with offset={start + READ_LINES})"
        return self._kept(text or "(empty)", "read")

    def _list(self, pattern):
        found = [os.path.relpath(p, self.root).replace(os.sep, "/") for p in self._files(self.root)]
        return self._kept("\n".join(p for p in found if fnmatch.fnmatch(p, pattern)) or "(none)", "list")

    def _kept(self, text, kind):
        """A tool's answer; one too big for the pass's context goes whole to a file it is pointed at."""
        if len(text) <= EVENT_TEXT:
            return text
        path = os.path.join(self.session.run_dir, "results", f"researcher-{kind}-{time.time_ns()}.txt")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        rel = os.path.relpath(path, self.root).replace(os.sep, "/")
        return (f"{len(text)} characters, more than one answer holds: whole in {rel}. Read it in parts (read_file "
                "with offset) or narrow the pattern.")
