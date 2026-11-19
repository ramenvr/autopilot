"""`autopilot mcp`: Autopilot as an MCP server (stdio) for the user's own Claude Code or Codex, which then works on the
game on the user's own plan (autopilot/agents/external.py).

    claude mcp add autopilot -- autopilot mcp
    codex mcp add autopilot -- autopilot mcp

It is a front for the agent service the editor's plugin started: it finds it by brain.json (bridge.brain_file: its
"control" address) and passes every call on to it. The game must be open in the editor with the Autopilot tab
connected. A call that takes longer than WAIT_SECONDS (an episode, the game starting: a client gives up on a slow
call, Codex after 60 s) answers "still going", and `wait` gives its result.

Several runs may go at once, each in a game of its own (Unreal's editor plays them side by side: start_run's
`parallel`): start_run answers with the run's id, and every other call names its run (`run`). A client whose subagents
share this server (Claude Code's) works on several runs through it; a call that names no run is the only run's. While
several runs started here go, a call on the game answers "still going" after SHARED_WAIT_SECONDS: a client that makes
its calls one at a time (Claude Code, for calls that aren't read-only) then has every run's episode playing at once.

Several verifications may also play one after another in one game (start_run's keep_game_open): each run reports
its own verdict, and the game stays open, paused where the run left it, for the next; the runs' restarts reopen the
level in the game playing. And a run may play headless (start_run's headless): this server then starts an agent
service of its own (`autopilot serve --headless`), which starts the project's game with no window and no editor, and
plays every headless run in that one game until this server ends (or no run has come for a while).

A program that runs an agent service of its own and sets this server up for its own agent says which service to use:
AUTOPILOT_MCP_BRAIN names a brain file (what that service announced: its "control" address), and then no other service
on the machine is looked for. It may also say where the runs its agent starts were asked for: AUTOPILOT_MCP_TRACE, a
JSON object (a start frame's run "trace": thread_id, tool_call_id, tool), goes with every start_run.

Nothing but the protocol goes to stdout.
"""

import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

PROTOCOL = "2025-06-18"
WAIT_SECONDS = float(os.environ.get("AUTOPILOT_MCP_WAIT") or 40)  # (under Codex's 60 s for one call)
# How long a call on the game waits for its result while several runs started here go: Claude Code makes an agent's
# calls on the game one after another, even those it sends together, so each answers "still going" soon and the next
# run's episode starts while this one's plays (wait, which a client may make side by side, gives the results)
SHARED_WAIT_SECONDS = float(os.environ.get("AUTOPILOT_MCP_WAIT_SHARED") or 3)
# How long a headless game may take to start and connect (an engine importing the project first takes minutes)
HEADLESS_START_SECONDS = float(os.environ.get("AUTOPILOT_MCP_HEADLESS_START") or 900)
HEADLESS_NOTE = ("\n\nThis game plays headless: no window, and it draws no picture, so `frames` and describe_frames "
                 "have nothing to show. Settle the request from what the game's state shows (positions, values, "
                 "events); a claim about how something looks on screen can only be INCONCLUSIVE here: say so.")

INSTRUCTIONS = ("Autopilot playtests the game open in the user's game editor (Unreal, Unity, Godot or Roblox Studio): "
                "call start_run with the user's request (explore it, test it, verify a claim, reach a goal), then "
                "follow the INSTRUCTIONS.md it names. A long call answers \"still going\": call wait for its result.")

NOT_RUNNING = ("Autopilot's agent service isn't running: open the game's project in Unreal, Unity, Godot or Roblox "
               "Studio with the Autopilot plugin, and let the Autopilot tab connect (Roblox Studio's plugin can't "
               "start the agent service: run `autopilot serve` in a terminal first). Then call start_run again.")
TOO_OLD = ("The Autopilot agent service running now is older than this way of using it: update it (the Autopilot "
           "tab's Settings, or reinstall the plugin's agent service), then call start_run again.")

START_SPEC = (
    "start_run",
    "Start an Autopilot run on the game open in the user's editor: Autopilot's decision model plays it, in a harness "
    "you write. request: what the user asks for, in their words (\"Verify that the door opens with the key\", not "
    "\"Use Autopilot to ...\"). kind: verify for a claim to check (\"the door opens with "
    "the key\": CONFIRMED, DENIED or INCONCLUSIVE), goal for something to make happen in play (\"sell three crops\"), "
    "explore to look for bugs (\"the market\") or to test the game (\"go through the level and tell me everything "
    "that looks wrong\"). minutes: an exploration's minutes of play (default 10). "
    "time_limit_minutes: the run's time limit (default 30). level: the level to play, when you know its path or "
    "name (default: the one open in the editor; the briefing lists the project's levels, and restart_level moves to "
    "the one the request means). keep_game_open (verifications only): how to work through several requests (a list, "
    "a spreadsheet), by default: one run after another in ONE game: when this run has reported, the game stays open, "
    "paused where it left it, for your next start_run (which plays on in it: its first run_harness may go on from "
    "there with restart=False); every restart of these runs reopens the level in the game playing, never closing it. "
    "Pass it on every run of the batch but the last; "
    "the game closes when the last one reports (or when no run has come for 10 minutes). parallel: only when the user "
    "asks for requests to run at the same time (in parallel, side by side, several windows): how many runs you keep "
    "going at once (default 1; up to 3, as the agent service allows): the first start_run of a batch says it, and the "
    "editor then plays that many games side by side (a window each), one per run; a start_run while that many go is "
    "refused until one reports. headless: play the game "
    "with no window and no editor: Autopilot starts the project's game itself (Unreal, Unity, Godot; not Roblox, "
    "whose games play only in Roblox Studio) and every headless run plays in that one game, one after another; it "
    "draws no picture, so claims about how something looks can't be settled. project: with headless, the game's "
    "project (a .uproject, a Unity project folder, a project.godot; default: the one in your working folder); "
    "engine: with headless, the engine's executable (default: the project's own). "
    "Answers with the run's id (pass it as run with every other call), the briefing and the "
    "instructions to follow; the game is paused between calls.",
    {"type": "object", "properties": {
        "request": {"type": "string"}, "kind": {"type": "string", "enum": ["verify", "goal", "explore"]},
        "minutes": {"type": "number"}, "time_limit_minutes": {"type": "number"}, "level": {"type": "string"},
        "parallel": {"type": "integer", "minimum": 1}, "keep_game_open": {"type": "boolean"},
        "headless": {"type": "boolean"}, "project": {"type": "string"}, "engine": {"type": "string"}},
     "required": ["request", "kind"]})
WAIT_SPEC = (
    "wait",
    "Wait for the call still going (an episode, the game starting) and give its result. If it is still going after "
    f"about {WAIT_SECONDS:.0f} s, it says so: call wait again.",
    {"type": "object", "properties": {}})
RUN_PROPERTY = {"type": "string",
                "description": "the run's id, as start_run answered it (needed while several runs go)"}
DESCRIBE_HERE = ("Fallback only (use your describer subagent and `frames`, as INSTRUCTIONS.md says): Autopilot's own "
                 "vision model describes an episode's frames; it works only where the user has a key or Aura for "
                 "one. ")
READ_ONLY = {"wait", "snapshot", "frames", "describe_frames", "read_file", "list_files", "search"}
FILE_TOOLS = {"read_file", "list_files", "search", "write_file", "edit_file"}  # (quick, and take no game time: never
# held up by a call still going)


def tools():
    """The tools as MCP lists them."""
    from .agents.external import FRAMES_SPEC
    from .agents.files import FILE_TOOL_SPECS
    from .agents.session import TOOL_SPECS

    specs = [START_SPEC, WAIT_SPEC]
    for name, description, schema in TOOL_SPECS:
        specs.append((name, DESCRIBE_HERE + description if name == "describe_frames" else description, schema))
    specs += [FRAMES_SPEC, *FILE_TOOL_SPECS]
    return [{"name": n, "description": d, "inputSchema": s if n == "start_run" else with_run(s),
             "annotations": {"readOnlyHint": n in READ_ONLY, "destructiveHint": False, "openWorldHint": False}}
            for n, d, s in specs]


def with_run(schema):
    """A tool's schema with `run`, the run the call is for."""
    return {**schema, "properties": {**(schema.get("properties") or {}), "run": RUN_PROPERTY}}


def client_name(info):
    """The client, as a run records it: claude-code, codex, or the name it gave."""
    name = str((info or {}).get("name") or "").strip()
    low = name.lower()
    return "claude-code" if "claude" in low else "codex" if "codex" in low else (name or None)


def pinned_brain():
    """The one brain file this server was told to use (AUTOPILOT_MCP_BRAIN), or None: every service is looked for."""
    return (os.environ.get("AUTOPILOT_MCP_BRAIN") or "").strip() or None


def run_trace():
    """Where the runs this server starts were asked for (AUTOPILOT_MCP_TRACE: a JSON object), or None."""
    try:
        trace = json.loads(os.environ.get("AUTOPILOT_MCP_TRACE") or "null")
    except ValueError:
        return None
    return trace if isinstance(trace, dict) and trace else None


def control_urls():
    """The running agent services' control addresses, the newest first (brain.json: the editor's; aura-brain.json: the
    Aura app's brain, which may run beside it with no editor connected), or ([], why). With AUTOPILOT_MCP_BRAIN, the
    service that file names and no other."""
    from . import aura, bridge

    pinned = pinned_brain()
    found = []
    for path in ([pinned] if pinned else [bridge.brain_file(name) for name in (bridge.BRAIN_FILE, aura.BRAIN_FILE)]):
        try:
            with open(path, encoding="utf-8") as f:
                info = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(info, dict) and info.get("url"):
            found.append(info)
    if not found:
        return [], NOT_RUNNING
    with_control = [i for i in found if i.get("control")]
    if not with_control:
        return [], TOO_OLD
    return [i["control"] for i in sorted(with_control, key=lambda i: i.get("started") or 0, reverse=True)], None


def text(t, error=False):
    return {"content": [{"type": "text", "text": t}], "isError": bool(error)}


# A service's answer that passes the call on to the next one: its flag (set false), and the words that begin the same
# answer from an agent service older than the flag
PASSES_ON = {"connected": "Autopilot isn't connected to the editor", "going": "No run is going"}


def passes_on(answer, flag):
    """Whether the answer says this service has no editor connected (flag "connected") or no run going ("going")."""
    if answer.get(flag) is False:
        return True
    return bool(answer.get("isError")) and any(str(c.get("text", "")).startswith(PASSES_ON[flag])
                                               for c in answer.get("content") or [])


def plain(answer):
    """The answer as the client gets it: without the flags meant for this front."""
    for flag in (*PASSES_ON, "reached"):
        answer.pop(flag, None)
    return answer


def find_headless_project(asked=None, cwd=None):
    """(engine, project path) of the game a headless run plays: the project asked for, else the one in the working
    folder (or a folder above it); or (None, why not)."""
    from . import engines

    if asked:
        eng, path = engines.find_project(os.path.abspath(os.path.expanduser(str(asked))))
        return (eng, path) if eng else (None, f"No Unreal, Unity or Godot project at {asked}: pass project, the path "
                                              "of the game's .uproject, Unity project folder or project.godot.")
    here = os.path.abspath(cwd or os.getcwd())
    while True:
        eng, path = engines.find_project(here)
        if eng:
            return eng, path
        if os.path.dirname(here) == here:
            return None, ("No game project in the working folder: pass project, the path of the game's .uproject, "
                          "Unity project folder or project.godot.")
        here = os.path.dirname(here)


class Headless:
    """The agent service this server started for headless runs (`autopilot serve --headless`): it starts the project's
    game with no window and plays the runs in it; it ends with this server (--exit-with), or once idle."""

    def __init__(self, project, engine=None):
        from . import bridge

        self.project, self.engine = project, engine
        self.brain = bridge.brain_file(f"headless-brain-{os.getpid()}.json")
        logs = os.path.join(os.path.dirname(self.brain), "logs")
        os.makedirs(logs, exist_ok=True)
        self.log = os.path.join(logs, f"headless-{time.strftime('%Y%m%d-%H%M%S')}.log")
        try:
            os.remove(self.brain)
        except OSError:
            pass
        cmd = [sys.executable, "-m", "autopilot", "serve", "--headless", "--project", project, "--port", "0",
               "--exit-with", str(os.getpid()), "--brain-file", self.brain] + (["--engine", engine] if engine else [])
        with open(self.log, "ab") as out:
            self.proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                                         env={**os.environ, "PYTHONUNBUFFERED": "1"})

    def alive(self):
        return self.proc.poll() is None

    def control(self):
        """Its control address, once it has said where it is; else None."""
        try:
            with open(self.brain, encoding="utf-8") as f:
                return json.load(f).get("control")
        except (OSError, ValueError, AttributeError):
            return None

    def why_ended(self):
        """Why it ended: the end of its log."""
        try:
            with open(self.log, encoding="utf-8", errors="replace") as f:
                lines = [line.rstrip() for line in f.read().splitlines() if line.strip()]
        except OSError:
            lines = []
        tail = "\n".join(lines[-12:]) or "(nothing logged)"
        return f"The headless game's agent service ended (log: {self.log}):\n{tail}"


class Server:
    def __init__(self, out=None, find=control_urls):
        self.out = out or sys.stdout
        self.find = find
        self.send_lock = threading.Lock()
        self.lock = threading.Lock()
        self.client = None
        self.control = None  # the address the latest run was started at
        self.run = None  # its marker
        self.runs = {}  # every run started here: its marker -> the address it was started at
        self.ended = set()  # ... the ones reported
        self.starts = 0  # start_run calls so far (one still going waits as "start-<n>")
        self.pending = {}  # the calls still going, by the run they are for ("": one that named none): (thread, box,
        # tool, started)
        self.headless = None  # the agent service started for headless runs (Headless)
        self.headless_lock = threading.Lock()

    def send(self, message):
        with self.send_lock:
            self.out.write(json.dumps(message) + "\n")
            self.out.flush()

    def post(self, url, body):
        req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=None) as r:  # (an episode may take minutes)
                return json.loads(r.read().decode("utf-8", "replace"))
        except (urllib.error.URLError, OSError, ValueError) as e:
            return {**text(f"Autopilot's agent service couldn't be reached ({e}). {NOT_RUNNING}", error=True),
                    "reached": False}

    def first_taker(self, path, body, flag):
        """The call made on each agent service running, the newest first, until one takes it: one with no editor
        connected, or no run going (flag), passes it on (the Aura app's brain runs beside the editor's service, and
        may have started after it). (its address, its answer), else (None, why none took it)."""
        urls, why = self.find()
        if not urls:
            return None, text(why, error=True)
        refused = unreached = None
        for url in urls:
            answer = self.post(f"{url}/{path}", body)
            if answer.get("reached") is False:
                unreached = unreached or answer
            elif passes_on(answer, flag):
                refused = refused or answer
            else:
                return url, answer
        return None, refused or unreached

    def start_headless(self, body):
        """A headless start_run: made on the agent service this server started for the project's game (started now
        when there is none for it, and waited for while its game starts). (its address, its answer)."""
        if pinned_brain():
            return None, text("Headless runs aren't available here: this Autopilot plays the game in the editor "
                              "(start_run without headless).", error=True)
        if int(body.get("parallel") or 1) > 1:
            return None, text("A headless game plays one run at a time: start_run without parallel.", error=True)
        eng, project = find_headless_project(body.get("project"))
        if eng is None:
            return None, text(project, error=True)
        if eng.name.startswith("roblox"):
            return None, text("Roblox has no headless game: its games play only in Roblox Studio, in its window. "
                              "Start the run without headless (the place open in Studio, its Autopilot plugin "
                              "connected).", error=True)
        engine = str(body.get("engine") or "").strip() or None
        with self.headless_lock:
            service = self.headless
            if service is None or not service.alive() or os.path.normcase(service.project) != os.path.normcase(
                    project) or (engine and engine != service.engine):
                if service is not None and service.alive():
                    service.proc.terminate()
                service = self.headless = Headless(project, engine)
        body = {k: v for k, v in body.items() if k not in ("headless", "project", "engine")}
        started = time.time()
        while True:
            control = service.control()
            if control:
                answer = self.post(f"{control}/start", body)
                if answer.get("connected") is not False and answer.get("reached") is not False:
                    return control, answer
            if not service.alive():
                return None, text(service.why_ended(), error=True)
            if time.time() - started > HEADLESS_START_SECONDS:
                return None, text(f"The headless game didn't connect within {HEADLESS_START_SECONDS:.0f} s (log: "
                                  f"{service.log}).", error=True)
            time.sleep(2.0)

    def forward(self, name, args):
        """The call, made on the agent service (blocking)."""
        if name == "start_run":
            trace = run_trace()
            body = {**(args or {}), "client": self.client, **({"trace": trace} if trace else {})}
            headless = body.get("headless") is True
            if headless:
                url, answer = self.start_headless(body)
            else:
                body = {k: v for k, v in body.items() if k not in ("headless", "project", "engine")}
                url, answer = self.first_taker("start", body, "connected")
            if url is not None and not answer.get("isError"):
                self.control = url
                first = next((c.get("text", "") for c in answer.get("content") or [] if c.get("type") == "text"), "")
                self.run = first.split()[2] if first.startswith("Autopilot run ") else None
                if self.run:
                    self.runs[self.run] = url
                if headless:
                    for part in answer.get("content") or []:
                        if part.get("type") == "text":
                            part["text"] = str(part.get("text") or "") + HEADLESS_NOTE
                            break
            return plain(answer)
        args = dict(args or {})
        run = str(args.pop("run", "") or "").strip() or None
        # (no run named: the one run started here, else the agent service's only run going; several started here, the
        # service decides, and refuses while several go)
        run = run or (self.run if len(self.runs) <= 1 else None)
        body = {"run": run, "args": args}
        control = self.runs.get(run) if run else (self.control if len(self.runs) <= 1 else None)
        if control is None and run is None and self.runs:
            control = self.control
        if control is not None:
            answer = plain(self.post(f"{control}/call/{name}", body))
            if name == "report" and run and not answer.get("isError") and any(
                    str(c.get("text", "")).startswith("Reported") for c in answer.get("content") or []):
                self.ended.add(run)
            return answer
        # (a front that started no run works on the run named, or the run going: Codex starts one of these for each of
        # its subagents, the frames' describer among them)
        return plain(self.first_taker(f"call/{name}", body, "going")[1])

    def call(self, name, args):
        """The call's result, or "still going" once it has taken WAIT_SECONDS (wait gives it then)."""
        from .agents.session import unknown_args

        refused = unknown_args(name, args, [(t["name"], t["description"], t["inputSchema"]) for t in tools()])
        if refused:
            return text(refused, error=True)
        if name in FILE_TOOLS:
            return self.forward(name, args)
        key = str((args or {}).get("run") or "").strip()  # (each run's calls wait apart)
        with self.lock:
            if name == "wait":
                if not key and len(self.pending) > 1:
                    return text(f"Calls of {len(self.pending)} runs are going ({', '.join(sorted(self.pending))}): "
                                "pass run=\"<its id>\" to wait for one.", error=True)
                if not key and self.pending:
                    key = next(iter(self.pending))
                pending = self.pending.get(key)
                if pending is None:
                    return text("Nothing is going: no call is waiting for its result.")
            elif name != "start_run" and key in self.pending:
                pending = self.pending[key]
                return text(f"{pending[2]} is still going ({time.time() - pending[3]:.0f} s): call wait for its "
                            "result first.", error=True)
            else:
                if name == "start_run":  # (each start waits apart: several agents may start runs at once)
                    self.starts += 1
                    key = f"start-{self.starts}"
                box = {}
                thread = threading.Thread(target=lambda: box.update(result=self.forward(name, args)), daemon=True)
                thread.start()
                pending = self.pending[key] = (thread, box, name, time.time())
        thread, box, tool, started = pending
        shared = name not in ("wait", "start_run") and self.side_by_side()
        thread.join(SHARED_WAIT_SECONDS if shared else WAIT_SECONDS)
        with self.lock:
            if thread.is_alive():
                return text(f"{tool} is still going ({time.time() - started:.0f} s): call wait for its result"
                            + (f" (run=\"{key}\")" if key else "")
                            + ("; it plays on meanwhile: make the other runs' calls first, then wait for each (the "
                               "waits side by side)." if shared else "."))
            if self.pending.get(key) is pending:
                self.pending.pop(key, None)
            return box.get("result") or text(f"{tool} gave no result.", error=True)

    def side_by_side(self):
        """Whether several runs started here go (not reported yet)."""
        return len([run for run in self.runs if run not in self.ended]) > 1

    def handle(self, msg):
        method, mid = msg.get("method"), msg.get("id")
        if mid is None:
            return  # (a notification: initialized, cancelled)
        if method == "initialize":
            params = msg.get("params") or {}
            self.client = client_name(params.get("clientInfo"))
            self.send({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": params.get("protocolVersion") or PROTOCOL,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "autopilot", "version": _version()}, "instructions": INSTRUCTIONS}})
        elif method == "tools/list":
            self.send({"jsonrpc": "2.0", "id": mid, "result": {"tools": tools()}})
        elif method == "tools/call":
            params = msg.get("params") or {}

            def run():
                try:
                    result = self.call(params.get("name"), params.get("arguments"))
                except Exception as e:  # noqa: BLE001 - the client is told
                    result = text(f"Autopilot failed: {type(e).__name__}: {e}", error=True)
                self.send({"jsonrpc": "2.0", "id": mid, "result": result})

            threading.Thread(target=run, daemon=True).start()
        elif method == "ping":
            self.send({"jsonrpc": "2.0", "id": mid, "result": {}})
        else:
            self.send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"No method {method}"}})

    def serve(self, lines):
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if isinstance(msg, dict):
                self.handle(msg)


def _version():
    from . import __version__

    return __version__


def main():
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    out, sys.stdout = sys.stdout, sys.stderr  # (whatever else prints goes to stderr: stdout is the protocol's)
    Server(out=out).serve(sys.stdin)  # (ends when the client closes stdin)
    return 0
