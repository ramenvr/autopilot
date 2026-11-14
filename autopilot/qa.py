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

from . import bridge, engines, processes, regress

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


def regressions_in(folder):
    """What the list shows of the regressions in a folder (regress.describe of each), or [] for none."""
    folder = (folder or "").strip()
    if not folder or not os.path.isdir(folder):
        return []
    return [regress.describe(r) for r in regress.listing(folder)]


def describe_folder(folder, found):
    folder = (folder or "").strip()
    if not folder:
        return ("No folder: this machine's Autopilot memory of the game is used (where a developer's own runs are "
                "kept). On a QA machine, choose the game's Autopilot/regressions folder.")
    if not os.path.isdir(folder):
        return "There's no folder there."
    if not found:
        return "No regressions in this folder (each is a folder holding regression.json)."
    return f"{len(found)} regression{'s' if len(found) != 1 else ''}."


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
    """Autopilot QA's window, in the editor tab's theme (autopilot/qa_theme.py): the build and the regressions folder
    on a plate, Replay all / Replay selected / Stop, the list of regressions with each one's result, its details and
    the command's log (the tab's underlined tabs), and the status line with the verdict's tag."""

    def __init__(self, root, build=None, regressions=None):
        import tkinter as tk
        from tkinter import ttk

        from . import qa_theme as theme

        self.tk, self.ttk, self.root, self.theme = tk, ttk, root, theme
        self.model = Model()
        self.replay = None
        saved = load_settings()
        root.title(TITLE)
        root.geometry("960x780")
        root.minsize(680, 480)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.fonts = fonts = theme.apply(root)
        theme.dark_title_bar(root)

        self.build = tk.StringVar(value=build or saved.get("build") or "")
        self.folder = tk.StringVar(value=regressions or saved.get("regressions") or "")
        self.watch = tk.BooleanVar(value=saved.get("watch", True))
        self.retry = tk.BooleanVar(value=saved.get("retry", True))
        self.build_note = tk.StringVar()
        self.folder_note = tk.StringVar()
        self.status = tk.StringVar(value=self.model.status)
        G, P = theme.GROUND, theme.PLATE

        # (the header: the product's micro-label, a rule under it)
        head = tk.Frame(root, bg=G)
        head.pack(fill="x", padx=16, pady=(14, 10))
        tk.Label(head, text="AUTOPILOT", bg=G, fg=theme.ACCENT, font=fonts.label(11)).pack(side="left")
        tk.Label(head, text="QA", bg=G, fg=theme.MUTED, font=fonts.label(11)).pack(side="left", padx=(6, 0))
        tk.Label(head, text="Replays a game's confirmed verifications on a build, without a model", bg=G,
                 fg=theme.MUTED, font=fonts.body(12)).pack(side="right")

        # (the choices, on a plate)
        plate = tk.Frame(root, bg=P, highlightthickness=1, highlightbackground=theme.RULE)
        plate.pack(fill="x", padx=16)
        form = tk.Frame(plate, bg=P)
        form.pack(fill="x", padx=14, pady=(12, 12))
        form.columnconfigure(0, weight=1)

        def field(row, label, var, note, pick):
            tk.Label(form, text=label, bg=P, fg=theme.MUTED, font=fonts.label(10)).grid(
                row=row, column=0, columnspan=2, sticky="w", pady=(0 if row == 0 else 10, 4))
            entry = tk.Entry(form, textvariable=var, bg=theme.WELL, fg=theme.TEXT, insertbackground=theme.TEXT,
                             relief="flat", font=fonts.body(13), highlightthickness=1,
                             highlightbackground=theme.LINE, highlightcolor=theme.FOCUS,
                             selectbackground=theme.ROW_ACTIVE, selectforeground=theme.TEXT)
            entry.grid(row=row + 1, column=0, sticky="ew", ipady=5, ipadx=6)
            ttk.Button(form, text="Browse...", style="Autopilot.TButton", command=pick).grid(
                row=row + 1, column=1, padx=(8, 0), sticky="ns")
            tk.Label(form, textvariable=note, bg=P, fg=theme.TEXT3, font=fonts.body(12), anchor="w", justify="left",
                     wraplength=760).grid(row=row + 2, column=0, columnspan=2, sticky="w", pady=(4, 0))
            return entry

        field(0, "BUILD", self.build, self.build_note, self.pick_build)
        field(3, "REGRESSIONS", self.folder, self.folder_note, self.pick_folder)
        opts = tk.Frame(form, bg=P)
        opts.grid(row=6, column=0, columnspan=2, sticky="w", pady=(12, 0))
        ttk.Checkbutton(opts, text="Watch the game (show its window)", variable=self.watch,
                        style="Plate.Autopilot.TCheckbutton").pack(side="left")
        ttk.Checkbutton(opts, text="Replay a failed one once more (flaky check)", variable=self.retry,
                        style="Plate.Autopilot.TCheckbutton").pack(side="left", padx=(20, 0))

        # (the actions)
        bar = tk.Frame(root, bg=G)
        bar.pack(fill="x", padx=16, pady=(12, 8))
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

        # (the status line first, at the bottom: packed after the panes, a small window would squeeze it out)
        foot = tk.Frame(root, bg=G, highlightthickness=0)
        foot.pack(side="bottom", fill="x")
        tk.Frame(foot, bg=theme.RULE, height=1).pack(fill="x")
        line = tk.Frame(foot, bg=G)
        line.pack(fill="x", padx=16, pady=8)
        self.tag_label = tk.Label(line, text="", font=fonts.label(10), padx=7, pady=1, highlightthickness=1)
        self.status_label = tk.Label(line, textvariable=self.status, bg=G, fg=theme.TEXT3, font=fonts.mono_font(12),
                                     anchor="w")
        self.status_label.pack(side="left", fill="x", expand=True)

        panes = ttk.Panedwindow(root, orient="vertical", style="Autopilot.TPanedwindow")
        panes.pack(fill="both", expand=True, padx=16, pady=(0, 10))
        listing = tk.Frame(panes, bg=G, highlightthickness=1, highlightbackground=theme.RULE)
        # (a row's words stay the reading color, as the tab's history; its result is a dot of its tone beside them)
        self.tree = ttk.Treeview(listing, columns=("status", "claim", "time"), show="tree headings",
                                 selectmode="extended", style="Autopilot.Treeview")
        self.tree.heading("#0", text="")
        self.tree.column("#0", width=30, minwidth=30, stretch=False, anchor="center")
        for col, text, width, stretch in (("status", "RESULT", 100, False), ("claim", "CLAIM", 640, True),
                                          ("time", "TIME", 70, False)):
            self.tree.heading(col, text=text, anchor="w")
            self.tree.column(col, width=width, stretch=stretch, anchor="w")
        self.dots = {status: theme.dot(root, theme.TONES[tone][0] if status else theme.LINE)
                     for status, tone in TONE.items()}
        self.tree.tag_configure(FAILED, foreground=theme.TONES["red"][1])
        scroll = ttk.Scrollbar(listing, orient="vertical", command=self.tree.yview,
                               style="Autopilot.Vertical.TScrollbar")
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.show_details())
        panes.add(listing, weight=1)

        # (Details and Log: the tab's text tabs, the picked one underlined in the accent)
        lower = tk.Frame(panes, bg=G)
        tabs = tk.Frame(lower, bg=G)
        tabs.pack(fill="x", pady=(4, 0))
        tk.Frame(lower, bg=theme.RULE, height=1).pack(fill="x")
        pages = tk.Frame(lower, bg=G)
        pages.pack(fill="both", expand=True, pady=(8, 0))

        def text_box(mono):
            return tk.Text(pages, height=10, wrap="none" if mono else "word", relief="flat", padx=12, pady=10,
                           bg=theme.WELL, fg=theme.TEXT2 if not mono else theme.TEXT3, insertbackground=theme.TEXT,
                           font=fonts.mono_font(12) if mono else fonts.body(13), highlightthickness=1,
                           highlightbackground=theme.RULE, highlightcolor=theme.RULE,
                           selectbackground=theme.ROW_ACTIVE, selectforeground=theme.TEXT, spacing1=1, spacing3=1)

        self.details = text_box(False)
        self.log = text_box(True)
        self.details.tag_configure("title", foreground=theme.TEXT, font=fonts.medium(14))
        self.details.tag_configure("label", foreground=theme.MUTED, font=fonts.label(10), spacing1=4)
        self.details.tag_configure("text", foreground=theme.TEXT2)
        self.details.tag_configure("mono", foreground=theme.TEXT3, font=fonts.mono_font(12))
        for tone, (solid, fore, fill, line_) in theme.TONES.items():
            self.details.tag_configure(tone, foreground=fore, background=fill, font=fonts.label(11))
        for text in (self.details, self.log):
            text.configure(state="disabled")
        self.tab_buttons = {}
        for name, widget in (("DETAILS", self.details), ("LOG", self.log)):
            cell = tk.Frame(tabs, bg=G)
            cell.pack(side="left", padx=(0, 18))
            label = tk.Label(cell, text=name, bg=G, fg=theme.MUTED, font=fonts.label(10), cursor="hand2", pady=4)
            label.pack()
            under = tk.Frame(cell, bg=G, height=2)
            under.pack(fill="x")
            label.bind("<Button-1>", lambda e, n=name: self.show_tab(n))
            self.tab_buttons[name] = (label, under, widget)
        self.show_tab("DETAILS")
        panes.add(lower, weight=1)

        self.build.trace_add("write", lambda *a: self.check_build())
        self.folder.trace_add("write", lambda *a: self.load_folder())
        self.check_build()
        self.load_folder()
        self.buttons()
        root.after(100, self.poll)

    def show_tab(self, name):
        for other, (label, under, widget) in self.tab_buttons.items():
            on = other == name
            label.configure(fg=self.theme.TEXT if on else self.theme.MUTED)
            under.configure(bg=self.theme.ACCENT if on else self.theme.GROUND)
            if on:
                widget.pack(fill="both", expand=True)
            else:
                widget.pack_forget()

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

        path = filedialog.askdirectory(title="The game's Autopilot/regressions folder",
                                       initialdir=self.folder.get() or None, mustexist=True)
        if path:
            self.folder.set(os.path.normpath(path))

    def check_build(self):
        ok, words = describe_build(self.build.get())
        self.build_ok = ok
        self.build_note.set(words)
        self.buttons()

    def load_folder(self):
        if self.replay is not None and self.replay.running():
            return
        found = regressions_in(self.folder.get())
        self.folder_note.set(describe_folder(self.folder.get(), found))
        self.model.show(found)
        self.render_rows()
        self.buttons()

    def remember(self):
        save_settings({"build": self.build.get().strip(), "regressions": self.folder.get().strip(),
                       "watch": bool(self.watch.get()), "retry": bool(self.retry.get())})

    # (a replay)

    def replay_all(self):
        self.start(None)

    def replay_selected(self):
        ids = [i for i in self.tree.selection() if i in self.model.rows]
        if ids:
            self.start(ids)

    def start(self, only):
        if not getattr(self, "build_ok", False) or (self.replay is not None and self.replay.running()):
            return
        self.remember()
        folder = self.folder.get().strip() or None
        cmd = command(self.build.get().strip(), folder, only=only, watch=self.watch.get(),
                      retries=1 if self.retry.get() else 0)
        self.model.start(only or list(self.model.order))
        self.write(self.log, "> " + subprocess.list2cmdline(cmd) + "\n", replace=True)
        try:
            self.replay = Replay(cmd)
        except OSError as e:
            self.model.running = False
            self.model.status = f"Couldn't start the replay: {e}"
        self.render_rows()
        self.status.set(self.model.status)
        self.buttons()

    def stop(self):
        if self.replay is not None and self.replay.running():
            self.status.set("Stopping...")
            self.replay.stop()

    def poll(self):
        """Apply what the command said since the last look (every 100 ms, on Tk's thread)."""
        changed = set()
        replay = self.replay
        while replay is not None:
            try:
                kind, data = replay.events.get_nowait()
            except queue.Empty:
                break
            changed |= self.model.apply(kind, data)
            if kind == "line":
                self.write(self.log, data + "\n")
        if "rows" in changed:
            self.render_rows()
            self.show_details()
        if changed & {"status", "rows"}:
            self.status.set(self.model.status)
            self.buttons()
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

    def show_details(self):
        sel = self.tree.selection()
        widget = self.details
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        for style, text in (self.model.detail_parts(sel[0]) if sel else []):
            widget.insert("end", text, (style,))
        if not sel:
            widget.insert("end", "Pick a regression to see what it verified and how its replay went.", ("label",))
        widget.configure(state="disabled")

    def write(self, widget, text, replace=False):
        widget.configure(state="normal")
        if replace:
            widget.delete("1.0", "end")
        widget.insert("end", text)
        if widget is self.log:
            widget.see("end")
        widget.configure(state="disabled")

    def buttons(self):
        theme = self.theme
        running = self.replay is not None and self.replay.running()
        ready = getattr(self, "build_ok", False) and not running
        self.all_button.state(["!disabled"] if ready else ["disabled"])
        self.some_button.state(["!disabled"] if ready and self.model.order else ["disabled"])
        self.stop_button.state(["!disabled"] if running else ["disabled"])
        self.results_button.state(["!disabled"] if self.model.out and os.path.isdir(self.model.out)
                                  else ["disabled"])
        tone = self.model.tone()
        self.status_label.configure(fg={"good": theme.TONES["success"][1], "bad": theme.TONES["red"][1]}.get(
            tone, theme.TEXT3))
        tag = self.model.tag()
        if tag:
            solid, fore, fill, line = theme.TONES[tag[0]]
            self.tag_label.configure(text=tag[1], fg=fore, bg=fill, highlightbackground=line, highlightcolor=line)
            if not self.tag_label.winfo_ismapped():
                self.tag_label.pack(side="left", padx=(0, 10), before=self.status_label)
        else:
            self.tag_label.pack_forget()

    def close(self):
        if self.replay is not None and self.replay.running():
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
