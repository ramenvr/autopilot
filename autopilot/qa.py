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

# A row's status, as the list shows it
WAITING, REPLAYING, PASSED, FAILED, FLAKY, NOT_RUN = "", "replaying", "ok", "FAIL", "FLAKY", "not run"


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
        parts = [row.get("prompt") or rid, ""]
        if row.get("summary"):
            parts += [f"When it was verified: {row['summary']}", ""]
        if row["status"] in (PASSED, FAILED, FLAKY):
            parts.append(f"Replay: {row['status']} - {row['why']}"
                         + (f" ({row['seconds']:.0f}s)" if isinstance(row.get("seconds"), (int, float)) else ""))
            if row.get("evidence") is not None:
                parts += ["", "What the replay ended on:", json.dumps(row["evidence"], indent=1)]
            if row.get("trace"):
                parts += ["", f"Trace: {row['trace']}"]
        elif row["status"]:
            parts.append(f"Replay: {row['status']}")
        parts += ["", f"Regression: {row.get('dir') or ''}  (id {rid})"]
        return "\n".join(parts)


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
    """Autopilot QA's window: the build and the regressions folder, Replay all / Replay selected / Stop, the list of
    regressions with each one's result, its details and the command's log."""

    COLORS = {PASSED: "#1a7f37", FAILED: "#cf222e", FLAKY: "#bc4c00", REPLAYING: "#0969da", NOT_RUN: "#6e7781"}

    def __init__(self, root, build=None, regressions=None):
        import tkinter as tk
        from tkinter import ttk

        self.tk, self.ttk, self.root = tk, ttk, root
        self.model = Model()
        self.replay = None
        saved = load_settings()
        root.title(TITLE)
        root.geometry("920x640")
        root.minsize(640, 420)
        root.protocol("WM_DELETE_WINDOW", self.close)

        self.build = tk.StringVar(value=build or saved.get("build") or "")
        self.folder = tk.StringVar(value=regressions or saved.get("regressions") or "")
        self.watch = tk.BooleanVar(value=saved.get("watch", True))
        self.retry = tk.BooleanVar(value=saved.get("retry", True))
        self.build_note = tk.StringVar()
        self.folder_note = tk.StringVar()
        self.status = tk.StringVar(value=self.model.status)

        top = ttk.Frame(root, padding=(12, 12, 12, 4))
        top.pack(fill="x")
        top.columnconfigure(1, weight=1)
        ttk.Label(top, text="Build").grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Entry(top, textvariable=self.build).grid(row=0, column=1, sticky="ew")
        ttk.Button(top, text="Browse...", command=self.pick_build).grid(row=0, column=2, padx=(8, 0))
        ttk.Label(top, textvariable=self.build_note, foreground="#57606a").grid(row=1, column=1, sticky="w",
                                                                                pady=(2, 8))
        ttk.Label(top, text="Regressions").grid(row=2, column=0, sticky="w", padx=(0, 8))
        ttk.Entry(top, textvariable=self.folder).grid(row=2, column=1, sticky="ew")
        ttk.Button(top, text="Browse...", command=self.pick_folder).grid(row=2, column=2, padx=(8, 0))
        ttk.Label(top, textvariable=self.folder_note, foreground="#57606a", wraplength=640,
                  justify="left").grid(row=3, column=1, sticky="w", pady=(2, 8))
        opts = ttk.Frame(top)
        opts.grid(row=4, column=1, sticky="w")
        ttk.Checkbutton(opts, text="Watch the game (show its window)", variable=self.watch).pack(side="left")
        ttk.Checkbutton(opts, text="Replay a failed one once more (flaky check)",
                        variable=self.retry).pack(side="left", padx=(16, 0))

        bar = ttk.Frame(root, padding=(12, 4))
        bar.pack(fill="x")
        self.all_button = ttk.Button(bar, text="Replay all", command=self.replay_all)
        self.all_button.pack(side="left")
        self.some_button = ttk.Button(bar, text="Replay selected", command=self.replay_selected)
        self.some_button.pack(side="left", padx=(8, 0))
        self.stop_button = ttk.Button(bar, text="Stop", command=self.stop)
        self.stop_button.pack(side="left", padx=(8, 0))
        self.results_button = ttk.Button(bar, text="Open results", command=lambda: open_folder(self.model.out))
        self.results_button.pack(side="right")

        # (the status line first, at the bottom: packed after the panes, a small window would squeeze it out)
        self.status_label = ttk.Label(root, textvariable=self.status, padding=(12, 6), anchor="w")
        self.status_label.pack(side="bottom", fill="x")
        panes = ttk.PanedWindow(root, orient="vertical")
        panes.pack(fill="both", expand=True, padx=12, pady=(4, 0))
        listing = ttk.Frame(panes)
        self.tree = ttk.Treeview(listing, columns=("status", "claim", "time"), show="headings", selectmode="extended")
        for col, text, width, stretch in (("status", "Result", 90, False), ("claim", "Claim", 600, True),
                                          ("time", "Time", 70, False)):
            self.tree.heading(col, text=text, anchor="w")
            self.tree.column(col, width=width, stretch=stretch, anchor="w")
        for status, color in self.COLORS.items():
            self.tree.tag_configure(status, foreground=color)
        scroll = ttk.Scrollbar(listing, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.show_details())
        panes.add(listing, weight=3)

        tabs = ttk.Notebook(panes)
        self.details = tk.Text(tabs, height=10, wrap="word", relief="flat", padx=8, pady=6, font="TkDefaultFont")
        self.log = tk.Text(tabs, height=10, wrap="none", relief="flat", padx=8, pady=6)
        for text in (self.details, self.log):
            text.configure(state="disabled")
        tabs.add(self.details, text="Details")
        tabs.add(self.log, text="Log")
        panes.add(tabs, weight=2)

        self.build.trace_add("write", lambda *a: self.check_build())
        self.folder.trace_add("write", lambda *a: self.load_folder())
        self.check_build()
        self.load_folder()
        self.buttons()
        root.after(100, self.poll)

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
                claim = f"{claim}  -  {row['why']}"
            self.tree.insert("", "end", iid=rid, values=(row["status"] or "-", claim, secs), tags=(row["status"],))
        keep = [rid for rid in selected if rid in self.model.rows]
        if keep:
            self.tree.selection_set(keep)

    def show_details(self):
        sel = self.tree.selection()
        self.write(self.details, self.model.details(sel[0]) if sel else "", replace=True)

    def write(self, widget, text, replace=False):
        widget.configure(state="normal")
        if replace:
            widget.delete("1.0", "end")
        widget.insert("end", text)
        if widget is self.log:
            widget.see("end")
        widget.configure(state="disabled")

    def buttons(self):
        running = self.replay is not None and self.replay.running()
        ready = getattr(self, "build_ok", False) and not running
        self.all_button.state(["!disabled"] if ready else ["disabled"])
        self.some_button.state(["!disabled"] if ready and self.model.order else ["disabled"])
        self.stop_button.state(["!disabled"] if running else ["disabled"])
        self.results_button.state(["!disabled"] if self.model.out and os.path.isdir(self.model.out)
                                  else ["disabled"])
        tone = self.model.tone()
        self.status_label.configure(foreground={"good": self.COLORS[PASSED], "bad": self.COLORS[FAILED]}.get(tone, ""))

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
