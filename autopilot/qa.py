"""Autopilot QA: a window that replays a game's regressions on a build of it, without a model (`autopilot qa`).

For QA engineers: no editor, no project, no sign-in. A developer's Autopilot keeps every CONFIRMED verification as a
regression in the game's own folder (Autopilot/regressions, autopilot/regress.py), which goes into source control with
the game. Here QA picks a build of the game (a Development build: Unreal packaged, Unity player, Godot export) and that
folder, and presses Replay: the window runs `autopilot regress --build <exe> --regressions <folder> --events`, which
starts the build (in a window to watch, or headless), replays the regressions with their recorded decisions and ends
the build; the window lists each one's result as it comes (ok, FAIL with why, FLAKY: passed only on a second try), and
keeps the results and the replays' traces in a folder of their own (Open results).

The command it runs is the one CI runs, so what QA sees here is what a pipeline gets. What the window remembers (the
build, the folder, the options) is in qa.json in Autopilot's settings folder.

Model (what the window shows, from the command's events) and Replay (the command, read on a thread) need no display:
tests drive them; Window is the tkinter front of them.
"""

import json
import os
import queue
import subprocess
import sys
import threading

from . import bridge, engines, gamefolder, processes, regress

SETTINGS_FILE = "qa.json"
TITLE = "Autopilot QA"

# A row's status; its words in the list (the theme's tags: upper case) and its tone (qa_theme.TONES)
WAITING, REPLAYING, PASSED, FAILED, FLAKY, NOT_RUN = "", "replaying", "ok", "FAIL", "FLAKY", "not run"
WORDS = {WAITING: "", REPLAYING: "REPLAYING", PASSED: "OK", FAILED: "FAIL", FLAKY: "FLAKY", NOT_RUN: "NOT RUN"}
TONE = {WAITING: "neutral", REPLAYING: "info", PASSED: "success", FAILED: "red", FLAKY: "amber", NOT_RUN: "neutral"}


# -- what the window remembers ------------------------------------------------------------------------------------

def settings_path():
    return os.path.join(os.path.dirname(bridge.brain_file()), SETTINGS_FILE)


def load_settings():
    try:
        with open(settings_path(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(data):
    try:
        os.makedirs(os.path.dirname(settings_path()), exist_ok=True)
        with open(settings_path(), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1)
    except OSError:
        pass  # (only a convenience: the next start asks again)


# -- what a choice means ------------------------------------------------------------------------------------------

def describe_build(path):
    """(ok, words) for the build chosen: which engine's build it is, or why it can't be replayed on."""
    path = (path or "").strip()
    if not path:
        return False, "Choose the game's build: its executable (a Development build)."
    if not os.path.exists(path):
        return False, "There's no file there."
    eng, exe = engines.find_build(path)
    if not eng:
        return False, ("Not a build Autopilot knows: choose an Unreal packaged game (<Game>.exe beside its Engine "
                       "folder), a Unity player (<Game>.exe beside <Game>_Data) or a Godot export.")
    return True, f"{eng.title or eng.name} build. It must be a Development build, which carries the Autopilot plugin."


def regressions_folder(folder):
    """The regressions folder of what was chosen: the game's Autopilot folder's regressions/, else the folder itself
    (a regressions folder, or any folder holding regressions)."""
    folder = (folder or "").strip()
    if not folder:
        return None
    found = gamefolder.resolve(folder)
    return (gamefolder.regressions(found) if found else None) or folder


def regressions_in(folder):
    """What the list shows of the regressions of what was chosen (regress.describe of each), or [] for none."""
    folder = regressions_folder(folder)
    if not folder or not os.path.isdir(folder):
        return []
    return [regress.describe(r) for r in regress.listing(folder)]


def code_index(folder):
    """What the chosen game's Autopilot folder holds for a Testing run: {"files", "exported", "memory"}, or None."""
    found = gamefolder.resolve((folder or "").strip()) if folder else None
    codex = os.path.join(found, gamefolder.CODEX) if found else None
    if not codex or not os.path.isdir(codex):
        return None
    try:
        with open(os.path.join(found, gamefolder.EXPORT), encoding="utf-8") as f:
            info = json.load(f)
    except (OSError, ValueError):
        info = {}
    files = sum(len(names) for _, _, names in os.walk(codex))
    memory = [n for n in gamefolder.LEARNED if os.path.isfile(os.path.join(found, gamefolder.MEMORY, n))]
    return {"files": files, "exported": info.get("exported"), "memory": memory}


def describe_folder(folder, found):
    folder = (folder or "").strip()
    if not folder:
        return ("No folder: this machine's Autopilot memory of the game is used (where a developer's own runs are "
                "kept). On a QA machine, choose the game's Autopilot folder (<game>/Autopilot).")
    if not os.path.isdir(folder):
        return "There's no folder there."
    parts = [f"{len(found)} regression{'s' if len(found) != 1 else ''}" if found else "no regressions"]
    index = code_index(folder)
    if index:
        parts.append(f"code index: {index['files']} files" + (f", exported {index['exported']}"
                                                                if index.get("exported") else ""))
        if index["memory"]:
            parts.append(" and ".join(index["memory"]))
    else:
        parts.append("no code index (a Testing run then plays without the game's code: `autopilot codex --project "
                     "<the game's source> --out <this folder>` makes one)")
    return "; ".join(parts)[:1].upper() + "; ".join(parts)[1:] + "."


DEFAULT_PROMPT = ("Go through the level and tell me everything that looks wrong: bugs, places the player can fall out "
                  "of the world or get stuck, and anything that doesn't work as it should.")


def test_command(build, folder, prompt, minutes=None, watch=True, python=None):
    """The `autopilot play` a Testing run of the window is."""
    cmd = [python or sys.executable, "-m", "autopilot", "play", prompt, "--build", build, "--events"]
    if minutes:
        cmd += ["--time-limit", f"{float(minutes):g}"]
    found = gamefolder.resolve(folder) if folder else None
    if found:
        cmd += ["--game-folder", found]
    if watch:
        cmd.append("--watch")
    return cmd


def command(build, regressions=None, only=None, watch=True, retries=1, python=None):
    """The `autopilot regress` the window runs."""
    cmd = [python or sys.executable, "-m", "autopilot", "regress", "--build", build, "--events",
           "--retries", str(max(0, int(retries)))]
    if regressions:
        cmd += ["--regressions", regressions]
    if watch:
        cmd.append("--watch")
    if only:
        cmd += ["--only", ",".join(only)]
    return cmd


# -- what the window shows ----------------------------------------------------------------------------------------

class Model:
    """The rows, the status line and the log of one replay, from the events of `autopilot regress --events`
    (cli.regress_event) and its other lines. apply() returns what changed: "rows", "status", "log"."""

    def __init__(self):
        self.order = []  # row ids, in the list's order
        self.rows = {}  # id -> {"id", "prompt", "summary", "status", "why", "seconds", "trace", "evidence", "dir"}
        self.status = "Ready."
        self.log = []
        self.out = None  # the results folder
        self.summary = None
        self.running = False
        self.error = None
        self.pending = []  # the rows the replay going is to play

    def show(self, items):
        """List these regressions (regress.describe dicts), none run yet."""
        self.order, self.rows = [], {}
        for item in items:
            rid = item["id"]
            self.order.append(rid)
            self.rows[rid] = {**item, "status": WAITING, "why": "", "seconds": None, "trace": None,
                              "evidence": None}

    def start(self, ids):
        """A replay of these rows begins (the others keep what they showed)."""
        self.running, self.summary, self.error, self.out = True, None, None, None
        self.log = []
        self.pending = [rid for rid in ids if rid in self.rows]
        for rid in ids:
            if rid in self.rows:
                self.rows[rid].update(status=WAITING, why="", seconds=None, trace=None, evidence=None)
        self.status = "Starting the build..."

    def apply(self, kind, data):
        if kind == "line":
            self.log.append(data)
            return {"log"}
        if kind == "exit":
            return self.finish(data)
        event = data.get("event")
        if event == "launching":
            self.status = f"Starting the build ({'in a window' if data.get('watch') else 'headless'})..."
            return {"status"}
        if event == "connected":
            self.status = f"Connected to {data.get('project') or 'the game'}."
            return {"status"}
        if event == "regressions":
            self.out = data.get("out")
            items = data.get("items") or []
            known = {i["id"] for i in items}
            for item in items:
                if item["id"] not in self.rows:
                    self.order.append(item["id"])
                    self.rows[item["id"]] = {**item, "status": WAITING, "why": "", "seconds": None, "trace": None,
                                             "evidence": None}
            self.pending = [i for i in self.order if i in known]
            self.status = (f"Replaying {len(items)} regression{'s' if len(items) != 1 else ''}, without a model..."
                           if items else "No regressions to replay in that folder.")
            return {"rows", "status"}
        if event == "replaying":
            row = self.rows.get(data.get("id"))
            if row:
                row["status"] = REPLAYING
                tries = f" (try {data['try']})" if (data.get("try") or 1) > 1 else ""
                done = sum(1 for r in self.rows.values() if r["status"] in (PASSED, FAILED, FLAKY))
                self.status = f"Replaying {done + 1} of {len(self.pending or self.order)}{tries}..."
            return {"rows", "status"}
        if event == "result":
            row = self.rows.get(data.get("id"))
            if row:
                row.update(status=FLAKY if data.get("flaky") else PASSED if data.get("passed") else FAILED,
                           why=data.get("why") or "", seconds=data.get("seconds"), trace=data.get("trace"),
                           evidence=data.get("evidence"))
            return {"rows"}
        if event == "summary":
            self.summary = data
            self.out = data.get("out") or self.out
            flaky = len(data.get("flaky") or [])
            self.status = (f"{data.get('passed', 0)}/{data.get('total', 0)} passed"
                           + (f", {flaky} flaky (passed only on a second try)" if flaky else "") + ".")
            return {"status"}
        if event == "error":
            self.error = data.get("message") or "the replay failed"
            self.status = self.error
            return {"status"}
        return set()

    def finish(self, code):
        """The command ended (exit code; None: stopped). Rows it never reached show they weren't run."""
        self.running = False
        for row in self.rows.values():
            if row["status"] == REPLAYING or (row["status"] == WAITING and row["id"] in self.pending):
                row["status"] = NOT_RUN
        if code is None:
            self.status = "Stopped."
        elif self.summary is None and self.error is None:
            last = next((line for line in reversed(self.log) if line.strip()), "")
            self.status = f"The replay ended before it finished (exit code {code}). {last}".strip()
        self.pending = []
        return {"rows", "status"}

    def tone(self):
        """How the status line reads: "good" (every one passed), "bad" (one failed, or the replay couldn't go on),
        else None."""
        if self.error or (self.summary is None and not self.running and self.status.startswith("The replay ended")):
            return "bad"
        if self.summary and self.summary.get("total"):
            return "bad" if self.summary.get("failed") else "good"
        return None

    def details(self, rid):
        """The text the details pane shows for a row."""
        row = self.rows.get(rid)
        if not row:
            return ""
        return "".join(text for _, text in self.detail_parts(rid))

    def detail_parts(self, rid):
        """The details pane's text for a row as (style, text) runs: "title", "label" (a micro-label), "text", "mono",
        and a result's tone ("success", "red", "amber", "info", "neutral")."""
        row = self.rows.get(rid)
        if not row:
            return []
        # (the replay's result first: what the pane is opened for)
        out = [("title", f"{row.get('prompt') or rid}\n")]
        done = row["status"] in (PASSED, FAILED, FLAKY)
        if done:
            secs = f" ({row['seconds']:.0f}s)" if isinstance(row.get("seconds"), (int, float)) else ""
            out += [("label", "\nREPLAY\n"), (TONE[row["status"]], WORDS[row["status"]]),
                    ("text", f"  {row['why']}{secs}\n")]
        elif row["status"]:
            out += [("label", "\nREPLAY\n"), (TONE[row["status"]], f"{WORDS[row['status']]}\n")]
        if row.get("summary"):
            out += [("label", "\nWHEN IT WAS VERIFIED\n"), ("text", f"{row['summary']}\n")]
        if done and row.get("evidence") is not None:
            out += [("label", "\nWHAT THE REPLAY ENDED ON\n"), ("mono", json.dumps(row["evidence"], indent=1) + "\n")]
        if done and row.get("trace"):
            out += [("label", "\nTRACE\n"), ("mono", f"{row['trace']}\n")]
        out += [("label", "\nREGRESSION\n"), ("mono", f"{row.get('dir') or ''}  (id {rid})\n")]
        return out

    def tag(self):
        """The status line's tag, (tone, words), or None: the verdict once a replay ended, else what is going on."""
        if self.running:
            return "info", "RUNNING"
        if self.summary and self.summary.get("total"):
            failed = len(self.summary.get("failed") or [])
            if failed:
                return "red", f"FAILED {failed}/{self.summary['total']}"
            return "success", f"PASSED {self.summary['passed']}/{self.summary['total']}"
        if self.tone() == "bad":
            return "red", "ERROR"
        if self.status == "Stopped.":
            return "neutral", "STOPPED"
        return None


class TestModel:
    """What the Test page shows of one Testing run (`autopilot play ... --build --events`): its status, the activity
    as it goes, then the report's findings and summary. apply() returns what changed: "rows", "status", "log",
    "activity"."""

    def __init__(self):
        self.status = "Ready."
        self.findings, self.activity, self.log = [], [], []
        self.report = None
        self.running = False
        self.error = None
        self.out = None  # the run's folder

    def start(self):
        self.__init__()
        self.running = True
        self.status = "Starting the build..."

    def apply(self, kind, data):
        if kind == "line":
            self.log.append(data)
            return {"log"}
        if kind == "exit":
            return self.finish(data)
        event = data.get("event")
        if event == "launching":
            self.status = f"Starting the build ({'in a window' if data.get('watch') else 'headless'})..."
            return {"status"}
        if event == "connected":
            self.status = f"Connected to {data.get('project') or 'the game'}. Reading the game..."
            return {"status"}
        if event == "status" and data.get("text"):
            self.status = f"{data['text']}..."
            return {"status"}
        if event == "activity":
            t = data.get("t")
            self.activity.append(f"{t:>6.0f}s  {data.get('text') or ''}" if isinstance(t, (int, float))
                                 else str(data.get("text") or ""))
            return {"activity"}
        if event == "report":
            self.report = data
            self.out = data.get("run_dir")
            self.findings = [str(f) for f in data.get("findings") or []]
            n = len(self.findings)
            bits = [f"{n} finding{'s' if n != 1 else ''}"]
            if data.get("episodes"):
                bits.append(f"{data['episodes']} episode{'s' if data['episodes'] != 1 else ''}")
            if isinstance(data.get("seconds"), (int, float)):
                bits.append(f"{data['seconds'] / 60:.0f} min")
            if isinstance(data.get("cost_usd"), (int, float)):
                bits.append(f"${data['cost_usd']:.2f}")
            self.status = ", ".join(bits) + "."
            return {"rows", "status"}
        if event == "error":
            self.error = data.get("message") or "the run failed"
            self.status = self.error
            return {"status"}
        return set()

    def finish(self, code):
        self.running = False
        if code is None:
            self.status = "Stopped."
        elif self.report is None and self.error is None:
            last = next((line for line in reversed(self.log) if line.strip()), "")
            self.status = f"The run ended before it reported (exit code {code}). {last}".strip()
        return {"rows", "status"}

    def tone(self):
        if self.error or (self.report is None and not self.running and self.status.startswith("The run ended")):
            return "bad"
        return None

    def tag(self):
        if self.report is not None:  # (as soon as it reports: the build is only closing after)
            n = len(self.findings)
            return ("amber", f"{n} FINDING{'S' if n != 1 else ''}") if n else ("success", "NO FINDINGS")
        if self.running:
            return "info", "TESTING"
        if self.tone() == "bad":
            return "red", "ERROR"
        if self.status == "Stopped.":
            return "neutral", "STOPPED"
        return None

    def detail_parts(self, index=None):
        """The details pane for a finding (its index), else the run's summary."""
        if index is not None and 0 <= index < len(self.findings):
            return [("title", f"Finding {index + 1}\n"), ("text", f"\n{self.findings[index]}\n")]
        if self.report is None:
            return [("label", "Write what to test, then Run test. The findings come here when the run reports.")]
        r = self.report
        out = [("title", f"{r.get('prompt') or 'The run'}\n")]
        if r.get("summary"):
            out += [("label", "\nSUMMARY\n"), ("text", f"{r['summary']}\n")]
        for alert in r.get("alerts") or []:
            out += [("label", "\nNOTE\n"), ("amber", " ! "), ("text", f"  {alert}\n")]
        if r.get("visual") is False:
            out += [("label", "\nPICTURE\n"), ("text", "None: the game ran headless, so nothing visual was checked "
                                                       "(tick Watch the game to give it one).\n")]
        if r.get("run_dir"):
            out += [("label", "\nRUN\n"), ("mono", f"{r['run_dir']}\n")]
        return out

    def details(self, index=None):
        return "".join(text for _, text in self.detail_parts(index))


# -- the command, read on a thread ----------------------------------------------------------------------------------

class Replay:
    """One `autopilot regress` the window started: its output, read on a thread into `events` as ("event", dict),
    ("line", text) and last ("exit", code). stop() ends it and the build it started."""

    def __init__(self, cmd):
        env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
        self.events = queue.Queue()
        self.stopped = False
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                     text=True, encoding="utf-8", errors="replace", env=env, creationflags=flags,
                                     **processes.GROUP)
        processes.track(self.proc)
        threading.Thread(target=self._read, daemon=True, name="qa-replay").start()

    def _read(self):
        for line in self.proc.stdout:
            line = line.rstrip("\r\n")
            event = None
            if line.startswith("{"):
                try:
                    event = json.loads(line)
                except ValueError:
                    event = None
            if isinstance(event, dict) and event.get("event"):
                self.events.put(("event", event))
            else:
                self.events.put(("line", line))
        code = self.proc.wait()
        self.events.put(("exit", None if self.stopped else code))

    def stop(self):
        self.stopped = True
        processes.kill(self.proc)

    def running(self):
        return self.proc.poll() is None


def open_folder(path):
    """Show a folder (or a file's folder) in Explorer / Finder / the file manager."""
    if not path:
        return
    if os.path.isfile(path):
        path = os.path.dirname(path)
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606 (a folder the user asked to see)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


# -- the window -----------------------------------------------------------------------------------------------------

class Window:
    """Autopilot QA's window, in the editor tab's theme (autopilot/qa_theme.py): the build and the game's Autopilot
    folder on a plate, then two pages. REPLAY: Replay all / Replay selected / Stop, the regressions with each one's
    result, its details and the command's log. TEST: a Testing prompt and its time limit, Run test / Stop, the run's
    findings, its summary, its activity as it goes and the command's log. One command runs at a time; the status line
    at the bottom, with its tag, is the page's."""

    def __init__(self, root, build=None, regressions=None):
        import tkinter as tk
        from tkinter import ttk

        from . import qa_theme as theme

        self.tk, self.ttk, self.root, self.theme = tk, ttk, root, theme
        self.model = Model()
        self.test = TestModel()
        self.replay = None  # the command running (a Replay), and which page's it is
        self.running_page = None
        self.page = "REPLAY"
        saved = load_settings()
        root.title(TITLE)
        root.geometry("980x820")
        root.minsize(700, 520)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.fonts = fonts = theme.apply(root)
        theme.dark_title_bar(root)

        self.build = tk.StringVar(value=build or saved.get("build") or "")
        self.folder = tk.StringVar(value=regressions or saved.get("folder") or saved.get("regressions") or "")
        self.watch = tk.BooleanVar(value=saved.get("watch", True))
        self.retry = tk.BooleanVar(value=saved.get("retry", True))
        self.minutes = tk.StringVar(value=str(saved.get("minutes") or 20))
        self.build_note = tk.StringVar()
        self.folder_note = tk.StringVar()
        self.status = tk.StringVar(value=self.model.status)
        G, P = theme.GROUND, theme.PLATE

        head = tk.Frame(root, bg=G)
        head.pack(fill="x", padx=16, pady=(14, 10))
        tk.Label(head, text="AUTOPILOT", bg=G, fg=theme.ACCENT, font=fonts.label(11)).pack(side="left")
        tk.Label(head, text="QA", bg=G, fg=theme.MUTED, font=fonts.label(11)).pack(side="left", padx=(6, 0))
        tk.Label(head, text="Replays and tests a game on a build of it", bg=G, fg=theme.MUTED,
                 font=fonts.body(12)).pack(side="right")

        # (the choices, on a plate)
        plate = tk.Frame(root, bg=P, highlightthickness=1, highlightbackground=theme.RULE)
        plate.pack(fill="x", padx=16)
        form = tk.Frame(plate, bg=P)
        form.pack(fill="x", padx=14, pady=(12, 12))
        form.columnconfigure(0, weight=1)
        self.field(form, 0, "BUILD", self.build, self.build_note, self.pick_build)
        self.field(form, 3, "AUTOPILOT FOLDER", self.folder, self.folder_note, self.pick_folder)
        opts = tk.Frame(form, bg=P)
        opts.grid(row=6, column=0, columnspan=2, sticky="w", pady=(12, 0))
        ttk.Checkbutton(opts, text="Watch the game (show its window)", variable=self.watch,
                        style="Plate.Autopilot.TCheckbutton").pack(side="left")
        ttk.Checkbutton(opts, text="Replay a failed one once more (flaky check)", variable=self.retry,
                        style="Plate.Autopilot.TCheckbutton").pack(side="left", padx=(20, 0))

        # (the status line first, at the bottom: packed after the pages, a small window would squeeze it out)
        foot = tk.Frame(root, bg=G)
        foot.pack(side="bottom", fill="x")
        tk.Frame(foot, bg=theme.RULE, height=1).pack(fill="x")
        line = tk.Frame(foot, bg=G)
        line.pack(fill="x", padx=16, pady=8)
        self.tag_label = tk.Label(line, text="", font=fonts.label(10), padx=7, pady=1, highlightthickness=1)
        self.status_label = tk.Label(line, textvariable=self.status, bg=G, fg=theme.TEXT3, font=fonts.mono_font(12),
                                     anchor="w")
        self.status_label.pack(side="left", fill="x", expand=True)

        # (the pages: REPLAY and TEST, as the tab's tabs)
        modes = tk.Frame(root, bg=G)
        modes.pack(fill="x", padx=16, pady=(14, 0))
        tk.Frame(root, bg=theme.RULE, height=1).pack(fill="x", padx=16)
        body = tk.Frame(root, bg=G)
        body.pack(fill="both", expand=True, padx=16, pady=(10, 10))
        replay_page, test_page = tk.Frame(body, bg=G), tk.Frame(body, bg=G)
        self.pages = self.tabs(modes, [("REPLAY", replay_page), ("TEST", test_page)], px=11, on_show=self.show_page)
        self.build_replay(replay_page)
        self.build_test(test_page)

        self.build.trace_add("write", lambda *a: self.check_build())
        self.folder.trace_add("write", lambda *a: self.load_folder())
        self.check_build()
        self.load_folder()
        self.pages("REPLAY")
        root.after(100, self.poll)

    # (building)

    def field(self, form, row, label, var, note, pick):
        tk, ttk, theme, fonts, P = self.tk, self.ttk, self.theme, self.fonts, self.theme.PLATE
        tk.Label(form, text=label, bg=P, fg=theme.MUTED, font=fonts.label(10)).grid(
            row=row, column=0, columnspan=2, sticky="w", pady=(0 if row == 0 else 10, 4))
        self.entry(form, var).grid(row=row + 1, column=0, sticky="ew", ipady=5, ipadx=6)
        ttk.Button(form, text="Browse...", style="Autopilot.TButton", command=pick).grid(
            row=row + 1, column=1, padx=(8, 0), sticky="ns")
        tk.Label(form, textvariable=note, bg=P, fg=theme.TEXT3, font=fonts.body(12), anchor="w", justify="left",
                 wraplength=780).grid(row=row + 2, column=0, columnspan=2, sticky="w", pady=(4, 0))

    def entry(self, parent, var, width=None):
        theme = self.theme
        return self.tk.Entry(parent, textvariable=var, bg=theme.WELL, fg=theme.TEXT, insertbackground=theme.TEXT,
                             relief="flat", font=self.fonts.body(13), highlightthickness=1,
                             highlightbackground=theme.LINE, highlightcolor=theme.FOCUS,
                             selectbackground=theme.ROW_ACTIVE, selectforeground=theme.TEXT,
                             **({"width": width} if width else {}))

    def text_box(self, parent, mono, height=10):
        theme, fonts = self.theme, self.fonts
        box = self.tk.Text(parent, height=height, wrap="none" if mono else "word", relief="flat", padx=12, pady=10,
                           bg=theme.WELL, fg=theme.TEXT3 if mono else theme.TEXT2, insertbackground=theme.TEXT,
                           font=fonts.mono_font(12) if mono else fonts.body(13), highlightthickness=1,
                           highlightbackground=theme.RULE, highlightcolor=theme.FOCUS,
                           selectbackground=theme.ROW_ACTIVE, selectforeground=theme.TEXT, spacing1=1, spacing3=1)
        box.tag_configure("title", foreground=theme.TEXT, font=fonts.medium(14))
        box.tag_configure("label", foreground=theme.MUTED, font=fonts.label(10), spacing1=4)
        box.tag_configure("text", foreground=theme.TEXT2)
        box.tag_configure("mono", foreground=theme.TEXT3, font=fonts.mono_font(12))
        for tone, (solid, fore, fill, line) in theme.TONES.items():
            box.tag_configure(tone, foreground=fore, background=fill, font=fonts.label(11))
        return box

    def tabs(self, bar, named, px=10, on_show=None):
        """The tab's text tabs in bar for (name, widget) pairs, the picked one underlined in the accent: a function
        that shows one by name."""
        tk, theme = self.tk, self.theme
        cells = {}

        def show(name):
            for other, (label, under, widget) in cells.items():
                on = other == name
                label.configure(fg=theme.TEXT if on else theme.MUTED)
                under.configure(bg=theme.ACCENT if on else theme.GROUND)
                if on:
                    widget.pack(fill="both", expand=True)
                else:
                    widget.pack_forget()
            if on_show:
                on_show(name)

        for name, widget in named:
            cell = tk.Frame(bar, bg=theme.GROUND)
            cell.pack(side="left", padx=(0, 20))
            label = tk.Label(cell, text=name, bg=theme.GROUND, fg=theme.MUTED, font=self.fonts.label(px),
                             cursor="hand2", pady=4)
            label.pack()
            under = tk.Frame(cell, bg=theme.GROUND, height=2)
            under.pack(fill="x")
            label.bind("<Button-1>", lambda e, n=name: show(n))
            cells[name] = (label, under, widget)
        show.cells = cells
        return show

    def lower_tabs(self, parent, named):
        """Under a list: a row of tabs, a rule, and their pages."""
        tk, G = self.tk, self.theme.GROUND
        lower = tk.Frame(parent, bg=G)
        bar = tk.Frame(lower, bg=G)
        bar.pack(fill="x", pady=(4, 0))
        tk.Frame(lower, bg=self.theme.RULE, height=1).pack(fill="x")
        pages = tk.Frame(lower, bg=G)
        pages.pack(fill="both", expand=True, pady=(8, 0))
        widgets = [(name, make(pages)) for name, make in named]
        show = self.tabs(bar, widgets)
        show(widgets[0][0])
        return lower, dict(widgets), show

    def listing(self, parent, columns, show="tree headings"):
        tk, ttk, theme = self.tk, self.ttk, self.theme
        frame = tk.Frame(parent, bg=theme.GROUND, highlightthickness=1, highlightbackground=theme.RULE)
        tree = ttk.Treeview(frame, columns=[c for c, *_ in columns], show=show, selectmode="extended",
                            style="Autopilot.Treeview")
        if "tree" in show:
            tree.heading("#0", text="")
            tree.column("#0", width=30, minwidth=30, stretch=False, anchor="center")
        for col, text, width, stretch in columns:
            tree.heading(col, text=text, anchor="w")
            tree.column(col, width=width, stretch=stretch, anchor="w")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview, style="Autopilot.Vertical.TScrollbar")
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        return frame, tree

    def build_replay(self, page):
        tk, ttk, theme = self.tk, self.ttk, self.theme
        bar = tk.Frame(page, bg=theme.GROUND)
        bar.pack(fill="x", pady=(0, 8))
        self.all_button = ttk.Button(bar, text="Replay all", style="Primary.Autopilot.TButton",
                                     command=self.replay_all)
        self.all_button.pack(side="left")
        self.some_button = ttk.Button(bar, text="Replay selected", style="Autopilot.TButton",
                                      command=self.replay_selected)
        self.some_button.pack(side="left", padx=(8, 0))
        self.stop_button = ttk.Button(bar, text="Stop", style="Danger.Autopilot.TButton", command=self.stop)
        self.stop_button.pack(side="left", padx=(8, 0))
        self.results_button = ttk.Button(bar, text="Open results", style="Autopilot.TButton",
                                         command=lambda: open_folder(self.model.out))
        self.results_button.pack(side="right")
        panes = ttk.Panedwindow(page, orient="vertical", style="Autopilot.TPanedwindow")
        panes.pack(fill="both", expand=True)
        # (a row's words stay the reading color, as the tab's history; its result is a dot of its tone beside them)
        frame, self.tree = self.listing(panes, (("status", "RESULT", 100, False), ("claim", "CLAIM", 640, True),
                                                ("time", "TIME", 70, False)))
        self.dots = {status: theme.dot(self.root, theme.TONES[tone][0] if status else theme.LINE)
                     for status, tone in TONE.items()}
        self.tree.tag_configure(FAILED, foreground=theme.TONES["red"][1])
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.show_details())
        panes.add(frame, weight=1)
        lower, boxes, self.show_tab = self.lower_tabs(panes, [("DETAILS", lambda p: self.text_box(p, False)),
                                                              ("LOG", lambda p: self.text_box(p, True))])
        self.details, self.log = boxes["DETAILS"], boxes["LOG"]
        for box in boxes.values():
            box.configure(state="disabled")
        panes.add(lower, weight=1)

    def build_test(self, page):
        tk, ttk, theme, fonts, G = self.tk, self.ttk, self.theme, self.fonts, self.theme.GROUND
        tk.Label(page, text="WHAT TO TEST", bg=G, fg=theme.MUTED, font=fonts.label(10)).pack(anchor="w")
        self.prompt = self.text_box(page, False, height=3)
        self.prompt.configure(bg=theme.WELL, highlightbackground=theme.LINE)
        self.prompt.insert("1.0", load_settings().get("prompt") or DEFAULT_PROMPT)
        self.prompt.pack(fill="x", pady=(4, 8))
        bar = tk.Frame(page, bg=G)
        bar.pack(fill="x", pady=(0, 4))
        self.test_button = ttk.Button(bar, text="Run test", style="Primary.Autopilot.TButton", command=self.run_test)
        self.test_button.pack(side="left")
        self.test_stop_button = ttk.Button(bar, text="Stop", style="Danger.Autopilot.TButton", command=self.stop)
        self.test_stop_button.pack(side="left", padx=(8, 0))
        tk.Label(bar, text="TIME LIMIT", bg=G, fg=theme.MUTED, font=fonts.label(10)).pack(side="left", padx=(20, 6))
        self.entry(bar, self.minutes, width=5).pack(side="left", ipady=4, ipadx=4)
        tk.Label(bar, text="min", bg=G, fg=theme.TEXT3, font=fonts.body(12)).pack(side="left", padx=(6, 0))
        self.run_button = ttk.Button(bar, text="Open run folder", style="Autopilot.TButton",
                                     command=lambda: open_folder(self.test.out))
        self.run_button.pack(side="right")
        tk.Label(page, text="A Testing run plays the build with Autopilot's models: it uses your Autopilot sign-in "
                            "(or keys) and costs credits.", bg=G, fg=theme.MUTED, font=fonts.body(12),
                 anchor="w").pack(fill="x", pady=(0, 8))
        panes = ttk.Panedwindow(page, orient="vertical", style="Autopilot.TPanedwindow")
        panes.pack(fill="both", expand=True)
        frame, self.findings = self.listing(panes, (("n", "#", 40, False), ("finding", "FINDING", 760, True)),
                                            show="headings")
        self.findings.bind("<<TreeviewSelect>>", lambda e: self.show_finding())
        panes.add(frame, weight=1)
        lower, boxes, self.show_test_tab = self.lower_tabs(panes, [
            ("DETAILS", lambda p: self.text_box(p, False)), ("ACTIVITY", lambda p: self.text_box(p, True)),
            ("LOG", lambda p: self.text_box(p, True))])
        self.finding_box, self.activity_box, self.test_log = boxes["DETAILS"], boxes["ACTIVITY"], boxes["LOG"]
        for box in boxes.values():
            box.configure(state="disabled")
        panes.add(lower, weight=1)
        self.show_finding()

    def show_page(self, name):
        self.page = name
        if hasattr(self, "all_button"):
            self.buttons()

    # (choices)

    def pick_build(self):
        from tkinter import filedialog

        kinds = [("Games", "*.exe"), ("All files", "*")] if sys.platform == "win32" else [("All files", "*")]
        start = os.path.dirname(self.build.get()) if self.build.get() else None
        path = filedialog.askopenfilename(title="The game's build", filetypes=kinds, initialdir=start)
        if path:
            self.build.set(os.path.normpath(path))

    def pick_folder(self):
        from tkinter import filedialog

        path = filedialog.askdirectory(title="The game's Autopilot folder (<game>/Autopilot)",
                                       initialdir=self.folder.get() or None, mustexist=True)
        if path:
            self.folder.set(os.path.normpath(path))

    def check_build(self):
        ok, words = describe_build(self.build.get())
        self.build_ok = ok
        self.build_note.set(words)
        self.buttons()

    def load_folder(self):
        if self.busy():
            return
        found = regressions_in(self.folder.get())
        self.folder_note.set(describe_folder(self.folder.get(), found))
        self.model.show(found)
        self.render_rows()
        self.buttons()

    def remember(self):
        save_settings({"build": self.build.get().strip(), "folder": self.folder.get().strip(),
                       "watch": bool(self.watch.get()), "retry": bool(self.retry.get()),
                       "minutes": self.minutes.get().strip(), "prompt": self.prompt_text()})

    def prompt_text(self):
        return self.prompt.get("1.0", "end").strip() if hasattr(self, "prompt") else ""

    # (a command)

    def busy(self):
        return self.replay is not None and self.replay.running()

    def launch(self, cmd, page, log):
        self.remember()
        self.write(log, "> " + subprocess.list2cmdline(cmd) + "\n", replace=True)
        try:
            self.replay = Replay(cmd)
            self.running_page = page
        except OSError as e:
            model = self.model if page == "REPLAY" else self.test
            model.running = False
            model.status = f"Couldn't start: {e}"
        self.refresh()

    def replay_all(self):
        self.start(None)

    def replay_selected(self):
        ids = [i for i in self.tree.selection() if i in self.model.rows]
        if ids:
            self.start(ids)

    def start(self, only):
        if not getattr(self, "build_ok", False) or self.busy():
            return
        cmd = command(self.build.get().strip(), regressions_folder(self.folder.get()), only=only,
                      watch=self.watch.get(), retries=1 if self.retry.get() else 0)
        self.model.start(only or list(self.model.order))
        self.render_rows()
        self.launch(cmd, "REPLAY", self.log)

    def run_test(self):
        prompt = self.prompt_text()
        if not getattr(self, "build_ok", False) or self.busy() or not prompt:
            return
        try:
            minutes = float(self.minutes.get())
        except ValueError:
            minutes = None
        cmd = test_command(self.build.get().strip(), self.folder.get().strip() or None, prompt,
                           minutes=minutes if minutes and minutes > 0 else None, watch=self.watch.get())
        self.test.start()
        for box in (self.activity_box, self.test_log):
            self.write(box, "", replace=True)
        self.render_findings()
        self.launch(cmd, "TEST", self.test_log)

    def stop(self):
        if self.busy():
            self.status.set("Stopping...")
            self.replay.stop()

    def poll(self):
        """Apply what the command said since the last look (every 100 ms, on Tk's thread)."""
        changed = set()
        replay = self.replay
        model = self.model if self.running_page == "REPLAY" else self.test
        while replay is not None:
            try:
                kind, data = replay.events.get_nowait()
            except queue.Empty:
                break
            changed |= model.apply(kind, data)
            if kind == "line":
                self.write(self.log if self.running_page == "REPLAY" else self.test_log, data + "\n")
            elif kind == "event" and data.get("event") == "activity" and model is self.test:
                self.write(self.activity_box, model.activity[-1] + "\n")
        if "rows" in changed:
            if model is self.model:
                self.render_rows()
                self.show_details()
            else:
                self.render_findings()
                self.show_finding()
        if changed & {"status", "rows"}:
            self.refresh()
        try:
            self.root.after(100, self.poll)
        except self.tk.TclError:
            pass  # (closed)

    # (drawing)

    def render_rows(self):
        selected = set(self.tree.selection())
        self.tree.delete(*self.tree.get_children())
        for rid in self.model.order:
            row = self.model.rows[rid]
            secs = f"{row['seconds']:.0f}s" if isinstance(row.get("seconds"), (int, float)) else ""
            claim = row.get("prompt") or rid
            if row["status"] in (FAILED, FLAKY) and row.get("why"):
                claim = f"{claim}  ·  {row['why']}"
            self.tree.insert("", "end", iid=rid, image=self.dots[row["status"]],
                             values=(WORDS[row["status"]] or "·", claim, secs), tags=(row["status"] or "waiting",))
        keep = [rid for rid in selected if rid in self.model.rows]
        if keep:
            self.tree.selection_set(keep)

    def render_findings(self):
        self.findings.delete(*self.findings.get_children())
        for i, text in enumerate(self.test.findings):
            first = " ".join(text.split())
            self.findings.insert("", "end", iid=str(i), values=(i + 1, first))

    def fill(self, box, parts):
        box.configure(state="normal")
        box.delete("1.0", "end")
        for style, text in parts:
            box.insert("end", text, (style,))
        box.configure(state="disabled")

    def show_details(self):
        sel = self.tree.selection()
        self.fill(self.details, self.model.detail_parts(sel[0]) if sel else [
            ("label", "Pick a regression to see what it verified and how its replay went.")])

    def show_finding(self):
        sel = self.findings.selection()
        self.fill(self.finding_box, self.test.detail_parts(int(sel[0]) if sel else None))

    def write(self, widget, text, replace=False):
        widget.configure(state="normal")
        if replace:
            widget.delete("1.0", "end")
        widget.insert("end", text)
        if widget is not self.details and widget is not getattr(self, "finding_box", None):
            widget.see("end")
        widget.configure(state="disabled")

    def refresh(self):
        self.buttons()

    def buttons(self):
        theme = self.theme
        running = self.busy()
        ready = getattr(self, "build_ok", False) and not running
        self.all_button.state(["!disabled"] if ready else ["disabled"])
        self.some_button.state(["!disabled"] if ready and self.model.order else ["disabled"])
        self.stop_button.state(["!disabled"] if running and self.running_page == "REPLAY" else ["disabled"])
        self.results_button.state(["!disabled"] if self.model.out and os.path.isdir(self.model.out)
                                  else ["disabled"])
        if hasattr(self, "test_button"):
            self.test_button.state(["!disabled"] if ready else ["disabled"])
            self.test_stop_button.state(["!disabled"] if running and self.running_page == "TEST" else ["disabled"])
            self.run_button.state(["!disabled"] if self.test.out and os.path.isdir(self.test.out) else ["disabled"])
        model = self.model if self.page == "REPLAY" else self.test
        self.status.set(model.status)
        tone = model.tone()
        self.status_label.configure(fg={"good": theme.TONES["success"][1], "bad": theme.TONES["red"][1]}.get(
            tone, theme.TEXT3))
        tag = model.tag()
        if tag:
            solid, fore, fill, line = theme.TONES[tag[0]]
            self.tag_label.configure(text=tag[1], fg=fore, bg=fill, highlightbackground=line, highlightcolor=line)
            if not self.tag_label.winfo_ismapped():
                self.tag_label.pack(side="left", padx=(0, 10), before=self.status_label)
        else:
            self.tag_label.pack_forget()

    def close(self):
        if self.busy():
            self.replay.stop()
        self.remember()
        self.root.destroy()


def main(args=None):
    """`autopilot qa`: the window (--build, --regressions: what it starts with)."""
    try:
        import tkinter as tk
    except ImportError:
        print("Autopilot QA needs Tk (Python's tkinter), which this Python doesn't have. The same replay from the "
              "command line: autopilot regress --build <the game's executable> --regressions <its folder>")
        return 1
    try:
        root = tk.Tk()
    except tk.TclError as e:
        print(f"Autopilot QA can't open a window here ({e}). The same replay from the command line: "
              "autopilot regress --build <the game's executable> --regressions <its folder>")
        return 1
    Window(root, build=getattr(args, "build", None), regressions=getattr(args, "regressions", None))
    root.mainloop()
    return 0
