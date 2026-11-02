"""The agent on OpenCode (opencode.ai), the default. AUTOPILOT_AGENT=opencode picks it.

It gets the same request, instructions, briefing and game tools as Claude Code (autopilot/agents/claude_code.py); only
the agent differs. OpenCode runs embedded (its SDK, under Bun) in a small host program, opencode_host/host.ts: one
session on this game's folder, the game's tools added by a plugin that calls this process over localhost
(toolserver.py), where the live game and the Session are. The host speaks JSON lines on its stdin and stdout: the
run's config and the prompts go in; what the model says, its tool calls, its cost and its turn's end come out.

OpenCode's own tools are cut to reading, searching and editing files, and the agent's permissions (permissions())
hold them to this game's folder: it may read the game's folder and the kit, and write only this run's folder,
notes.md and lib.py; no shell, web, subagents or questions. Every run gets a temporary home for OpenCode (its config,
data, cache), so the user's own OpenCode setup (config, agents, plugins, skills, instructions) is never loaded and a
run doesn't depend on what earlier runs left.
"""

import contextlib
import hashlib
import json
import os
import queue
import shutil
import stat
import subprocess
import tempfile
import threading
import time
import traceback

from .. import engines, processes
from . import session as sessionmod
from .toolserver import ToolServer

HERE = os.path.dirname(os.path.abspath(__file__))
HOST_DIR = os.path.join(HERE, "opencode_host")
HOST = os.path.join(HOST_DIR, "host.ts")

DEFAULT_PROVIDER = "anthropic"  # OpenCode's provider when AUTOPILOT_OPENCODE_PROVIDER names none (current_provider())
# The environment variables a provider's key may be in, the one the plugins' first-run setup saves first (the rest of
# the secrets are kept from OpenCode); for a provider not listed, those named after it (MISTRAL_API_KEY for mistral).
# The setup offers these six; "opencode" is OpenCode Zen, OpenCode's own gateway, and "google" is Gemini
PROVIDER_KEYS = {"anthropic": ("ANTHROPIC_API_KEY",), "openai": ("OPENAI_API_KEY",),
                 "opencode": ("OPENCODE_API_KEY",), "openrouter": ("OPENROUTER_API_KEY",),
                 "deepseek": ("DEEPSEEK_API_KEY",),
                 "google": ("GEMINI_API_KEY", "GOOGLE_GENERATIVE_AI_API_KEY", "GOOGLE_API_KEY")}
# OpenCode's own tools the model keeps; the rest (shell, web fetch and search, subagents, questions, skills...) go
KEEP_TOOLS = ("read", "glob", "grep", "edit", "write", "patch")

OPENCODE_TOOLS = """## Your tools here

Read files with `read`, search with `grep` and `glob` (several in one turn); write {run}/harness.py, notes.md and
lib.py with `write`, `edit` or `patch`: nothing else may be changed (not codex/, not earlier runs, nothing outside this
game's folder). Reading is held to this game's folder and the autopilot package's kit (autopilot/KIT.md,
autopilot/kit/). There is no shell and no web: everything you need is in this folder and the live game. The game's tools
(probe, run_harness, snapshot, describe_frames, restart_level, report) each return their result, then the time used
of your budget. Call them one at a time: the game runs only inside a call. Once report says "Reported", stop.
"""


def current_provider():
    """OpenCode's provider for this run: AUTOPILOT_OPENCODE_PROVIDER, read before each run (the plugins' first-run setup
    saves the one picked in autopilot.env, which the agent service loads before each run), else Anthropic."""
    return (os.environ.get("AUTOPILOT_OPENCODE_PROVIDER") or "").strip().lower() or DEFAULT_PROVIDER


def bun():
    """Bun's executable: AUTOPILOT_BUN, else bun on PATH (the real bun.exe behind npm's bun.cmd on Windows), else Bun's
    installer's place. Raises RuntimeError, saying how to install it, when there is none."""
    if os.environ.get("AUTOPILOT_BUN"):
        return os.environ["AUTOPILOT_BUN"]
    found = shutil.which("bun")
    if found and found.lower().endswith((".cmd", ".ps1")):
        # (npm's launcher would run Bun through cmd.exe: run what it runs)
        exe = os.path.join(os.path.dirname(found), "node_modules", "bun", "bin", "bun.exe")
        found = exe if os.path.exists(exe) else found
    if found:
        return found
    exe = os.path.join(os.path.expanduser("~"), ".bun", "bin", "bun.exe" if os.name == "nt" else "bun")
    if os.path.exists(exe):
        return exe
    raise RuntimeError("The OpenCode agent needs Bun (https://bun.sh): install it (Windows: "
                       "`powershell -c \"irm bun.sh/install.ps1 | iex\"`; macOS/Linux: "
                       "`curl -fsSL https://bun.sh/install | bash`), or set AUTOPILOT_BUN to its executable, or pick "
                       "another agent with AUTOPILOT_AGENT.")


INSTALL_TIMEOUT = 15 * 60  # seconds an install of the host's packages may take, waiting for another one included


def _lock_digest(host_dir):
    with open(os.path.join(host_dir, "bun.lock"), "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _stamp(host_dir):
    """The file an install writes once it has finished: the lockfile's digest it installed."""
    return os.path.join(host_dir, "node_modules", ".autopilot-installed.json")


def installed(host_dir=HOST_DIR):
    """Whether the host's packages are all in place: an install of this lockfile finished here. Their folders alone
    don't say so: an install cut short (killed, out of disk, a file it couldn't write) leaves some packages, or parts
    of one, and the host then ends at its start on a module it can't find."""
    try:
        with open(_stamp(host_dir), encoding="utf-8") as f:
            return json.load(f).get("lock") == _lock_digest(host_dir)
    except (OSError, ValueError, AttributeError):
        return False


@contextlib.contextmanager
def _install_lock(host_dir, timeout=INSTALL_TIMEOUT):
    """One install at a time in host_dir, across processes (two at once in one folder leave it half written)."""
    f = open(os.path.join(host_dir, ".install.lock"), "a+b")
    deadline = time.time() + timeout
    try:
        while True:
            try:
                if os.name == "nt":
                    import msvcrt
                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.time() > deadline:
                    raise RuntimeError(f"another install of OpenCode's packages in {host_dir} is still running")
                time.sleep(1)
        try:
            yield
        finally:
            try:
                if os.name == "nt":
                    import msvcrt
                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(f, fcntl.LOCK_UN)
            except OSError:
                pass
    finally:
        f.close()


def ensure_installed(exe, host_dir=HOST_DIR):
    """Install the host's packages (the exact versions in its lockfile) unless an install of this lockfile finished
    here (installed()). Anything less is installed again from scratch (--force): Bun's own check passes a package
    whose folder is there however few of its files are, so it would never repair an install cut short."""
    if installed(host_dir):
        return
    with _install_lock(host_dir):
        if installed(host_dir):  # (another process finished it while this one waited)
            return
        try:
            os.remove(_stamp(host_dir))
        except OSError:
            pass
        done = subprocess.run([exe, "install", "--frozen-lockfile", "--force"], cwd=host_dir, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=INSTALL_TIMEOUT)
        if done.returncode != 0:
            raise RuntimeError(f"`bun install` in {host_dir} failed ({done.returncode}):\n"
                               f"{(done.stdout + done.stderr)[-2000:]}")
        with open(_stamp(host_dir), "w", encoding="utf-8") as f:
            json.dump({"lock": _lock_digest(host_dir), "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}, f)


def _slashes(path):
    return os.path.realpath(path).replace("\\", "/").rstrip("/")


def permissions(root, run_rel, package=sessionmod.PACKAGE):
    """The agent's permission rules (OpenCode's: the last rule that matches wins; anything not allowed is denied).
    Read and search this game's folder, and read the kit; write only the run's folder, notes.md and lib.py; the game's
    tools. OpenCode names a path inside the game's folder, or inside the git checkout the folder is in (the default
    memory/ is in Autopilot's), relative to the game's folder, and others absolute (with / on every system)."""
    try:
        kit_rel = os.path.relpath(package, root).replace("\\", "/")  # (the kit as a path relative to the game's)
    except ValueError:  # (another drive)
        kit_rel = None
    root, package = _slashes(root), _slashes(package)
    rule = lambda action, resource, effect: {"action": action, "resource": resource, "effect": effect}
    rules = [rule("*", "*", "deny")]
    rules += [rule(name, "*", "allow") for name, _, _ in sessionmod.TOOL_SPECS]
    rules += [rule("glob", "*", "allow"), rule("grep", "*", "allow"), rule("read", "*", "allow")]
    # Nothing outside the game's folder (absolute paths, or relative ones that climb out of it), but the kit
    rules += [rule("read", "/*", "deny"), rule("read", "?:*", "deny"), rule("read", "..*", "deny"),
              rule("read", f"{root}/*", "allow")]
    for kit in (package, kit_rel) if kit_rel else (package,):
        rules += [rule("read", f"{kit}/KIT.md", "allow"), rule("read", f"{kit}/kit/*", "allow")]
    rules += [rule("read", "*.env", "deny"), rule("read", "*.env.*", "deny")]
    rules += [rule("external_directory", f"{package}/*", "allow")]
    for rel in (f"{run_rel}/*", "notes.md", "lib.py"):
        rules += [rule("edit", rel, "allow"), rule("edit", f"{root}/{rel}", "allow")]
    return rules


def system_text(engine, run_rel):
    return sessionmod.system_prompt(engine, run_rel) + "\n" + OPENCODE_TOOLS.replace("{run}", run_rel)


def host_config(session, tools_url, model, system, provider=None, fast=None):
    """The host's first stdin line (see opencode_host/host.ts). fast: fast mode for an Opus model (the provider's
    "-fast" model, when it offers one); by default session.fast_mode() (on unless AUTOPILOT_FAST=0)."""
    fast = sessionmod.fast_mode() if fast is None else fast
    provider = provider or current_provider()
    return {
        "directory": os.path.realpath(session.root),
        "provider": provider,
        "model": model,
        "variant": sessionmod.EFFORT,  # (the model's variant of that name, when it has one: its thinking effort)
        "fast": bool(fast and "opus" in model.lower()),
        "system": system,
        "permissions": permissions(session.root, session.run_rel),
        "keep_tools": list(KEEP_TOOLS),
        "tools": [{"name": n, "description": d, "input": s} for n, d, s in sessionmod.TOOL_SPECS],
        "tools_url": tools_url,
        "base_url": base_url(provider),
    }


def base_url(provider):
    """The provider's API root when the environment moves it (<PROVIDER>_BASE_URL, e.g. a proxy or gateway in front
    of the provider), as OpenCode wants it; None otherwise. Anthropic's SDKs take it without the /v1 that OpenCode's
    Anthropic provider takes it with."""
    url = (os.environ.get(provider.upper().replace("-", "_") + "_BASE_URL") or "").strip().rstrip("/")
    if not url:
        return None
    return url + "/v1" if provider == "anthropic" and not url.endswith("/v1") else url


def host_env(work, provider=None):
    """The host's environment: no secrets but the provider's key, nothing of the user's OpenCode settings, and
    OpenCode's config, data, cache, state, home and temporary files in this run's folder `work`."""
    provider = provider or current_provider()
    keys = PROVIDER_KEYS.get(provider) or ()
    prefix = provider.upper().replace("-", "_") + "_"
    env = {k: v for k, v in os.environ.items()
           if k in keys or (not k.upper().startswith("OPENCODE_")  # (OpenCode Zen's key is OPENCODE_API_KEY)
                            and (not sessionmod.SECRET.search(k) or k.upper().startswith(prefix)))}
    dirs = {name: os.path.join(work, name) for name in ("config", "data", "cache", "state", "home", "tmp")}
    for d in dirs.values():
        os.makedirs(d, exist_ok=True)
    env.update(XDG_CONFIG_HOME=dirs["config"], XDG_DATA_HOME=dirs["data"], XDG_CACHE_HOME=dirs["cache"],
               XDG_STATE_HOME=dirs["state"], OPENCODE_TEST_HOME=dirs["home"],
               OPENCODE_CONFIG_DIR=os.path.join(dirs["config"], "opencode"), OPENCODE_DISABLE_PROJECT_CONFIG="1",
               TEMP=dirs["tmp"], TMP=dirs["tmp"], TMPDIR=dirs["tmp"], DO_NOT_TRACK="1")
    env.pop("CLAUDECODE", None)
    return env


class Events:
    """What the host's JSON lines say: the implementer log, the status line, the cost, tokens and turns, the model's
    last words, and whether its turn has ended (idle) or the host failed (fatal)."""

    READ = ("read", "glob", "grep")
    WRITE = ("edit", "write", "patch")

    def __init__(self, session, say=None):
        self.session = session
        self.say = say or (lambda text: None)
        self.model = None
        self.cost = 0.0
        self.tokens = {}
        self.turns = 0
        self.last_text = ""
        self.idle = None  # the last idle event, until taken
        self.fatal = None

    def line(self, raw):
        try:
            event = json.loads(raw)
        except ValueError:
            return
        if isinstance(event, dict):
            self.event(event)

    def event(self, ev):
        kind, note = ev.get("type"), self.session.note
        if kind == "ready":
            self.model = ev.get("model")
            note(f"OpenCode ready: session {ev.get('session')}, model {self.model} ({ev.get('variant')})")
        elif kind == "text":
            self.last_text = str(ev.get("text") or "")
            self.session.said(self.last_text)
        elif kind == "tool":
            name = ev.get("name")
            if name not in dict.fromkeys(n for n, _, _ in sessionmod.TOOL_SPECS):  # (the tool server logs those)
                self.session.tool_call(name, ev.get("input"), log=f"{name}: {str(ev.get('input'))[:600]}")
            if name in self.READ:
                self.say(sessionmod.READING)
            elif name in self.WRITE:
                self.say(sessionmod.WRITING)
        elif kind == "tool_failed":
            note(f"{ev.get('name')} failed: {str(ev.get('error'))[:1000]}")
        elif kind == "step":
            self.turns += 1
        elif kind in ("usage", "idle"):
            self.usage(ev)
            if kind == "idle":
                self.idle = ev
                note(f"turn ended: {ev.get('outcome')}" + (f" ({ev['error']})" if ev.get("error") else ""))
        elif kind == "retry":
            note(f"retrying: {str(ev.get('info'))[:600]}")
        elif kind == "error":
            note(f"OpenCode error: {str(ev.get('message'))[:2000]}")
            if ev.get("fatal"):
                self.fatal = str(ev.get("message") or "failed").strip().splitlines()[0][:500]

    def usage(self, ev):
        """The session's totals so far (OpenCode counts them: its catalog prices the models)."""
        if ev.get("cost") is not None:
            self.cost = float(ev["cost"] or 0.0)
        t = ev.get("tokens") or {}
        if t:
            cache = t.get("cache") or {}
            self.tokens = {"input": int(t.get("input") or 0), "output": int(t.get("output") or 0),
                           "reasoning": int(t.get("reasoning") or 0), "cacheRead": int(cache.get("read") or 0),
                           "cacheWrite": int(cache.get("write") or 0)}

    def take_idle(self):
        idle, self.idle = self.idle, None
        return idle


def remove_tree(path):
    """Delete a folder, read-only files too (on Windows rmtree can't delete those as they are)."""
    def retry(fn, p, _exc):
        try:
            os.chmod(p, stat.S_IWRITE)
            fn(p)
        except OSError:
            pass

    shutil.rmtree(path, onexc=retry)


ERR_TAIL = 600  # characters of the host's last words (its stderr, opencode.log) a run's reason quotes, at most


def exit_code(code):
    """A process's exit code as Windows names a crash's (3221226505 is 0xC0000409), else as it is."""
    if code is None:
        return "still running"
    return f"{code} (0x{code & 0xFFFFFFFF:08X})" if abs(code) >= 0x10000 else str(code)


def err_tail(err, limit=ERR_TAIL):
    """The last of what the host wrote to its stderr (the file `err`), on one line; "" when there is none or it can't
    be read (err isn't a file of its own)."""
    try:
        err.flush()
        with open(err.name, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - limit * 4))
            text = f.read().decode("utf-8", "replace")
    except (AttributeError, OSError, TypeError, ValueError):
        return ""
    words = " | ".join(line.strip() for line in text.splitlines() if line.strip())
    return words if len(words) <= limit else "..." + words[-limit:]


def host_ended(proc, lines, events, err, session, writing=None):
    """Why the host ended by itself: what its fatal error said, else its exit code and the last of its stderr. writing:
    the error that sending it a line met (it had ended, or was ending, while it was sent the request), else None. The
    lines it wrote before it ended are read first: a fatal error among them is the reason."""
    try:
        code = proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        code = None
    stop_at = time.time() + 2  # (its stdout's last lines, which the reader thread may still be passing on)
    while not events.fatal and time.time() < stop_at:
        try:
            raw = lines.get(timeout=0.2)
        except queue.Empty:
            continue
        if raw is None:
            break
        events.line(raw)
    if events.fatal:
        return f"OpenCode failed: {events.fatal}"
    tail = err_tail(err)
    note = f"OpenCode's host ended: exit code {exit_code(code)}"
    if writing:
        note += f", while it was sent the request ({writing})"
    note += f"; its stderr ends: {err_tail(err, 4000)}" if tail else "; it wrote nothing to stderr"
    session.note(note)
    why = f"OpenCode exited ({exit_code(code)})"
    if writing:
        why += f" before it took the request ({writing})"
    return why + (f": {tail}" if tail else "")


def run_host(cmd, config, message, env, events, session, deadline, err, cwd=HOST_DIR):
    """Run the host on the request until the model's turn ends with nothing more to say to it, stop(), the deadline or
    the host's end. The user's words that came after the last tool result go to it as a new message once its turn
    ends. Returns None when it ended by itself, else why it was cut short."""
    proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=err,
                            **processes.GROUP)
    processes.track(proc)
    lines = queue.Queue()

    def read():
        for raw in iter(proc.stdout.readline, b""):
            lines.put(raw.decode("utf-8", "replace"))
        lines.put(None)

    def send(obj):
        proc.stdin.write((json.dumps(obj) + "\n").encode("utf-8"))
        proc.stdin.flush()

    threading.Thread(target=read, daemon=True).start()
    idle_now = False  # (the session is idle: the host can close by itself)
    try:
        send(config)
        send({"type": "prompt", "text": message})
        while True:
            if session.stop():
                return "stopped"
            if time.time() > deadline:
                return "timed out"
            try:
                raw = lines.get(timeout=0.5)
            except queue.Empty:
                continue
            if raw is None:
                lines.put(None)  # (host_ended reads to here)
                return host_ended(proc, lines, events, err, session)
            events.line(raw)
            if events.fatal:
                return f"OpenCode failed: {events.fatal}"
            idle = events.take_idle()
            if idle is None:
                continue
            idle_now = True
            if idle.get("outcome") not in (None, "succeeded"):
                return f"the model's turn {idle.get('outcome')}: {idle.get('error') or 'no reason given'}"
            # (the user steered after the last tool result, with no tool call left to carry it: on as asked)
            steer = session.steer_note().strip()
            if not steer:
                return None
            idle_now = False
            send({"type": "prompt", "text": steer})
    except OSError as e:  # (the host's stdin closed: it ended, or is ending. Windows says Broken pipe or No space
        # left on device: what the host said, its exit code and stderr say why)
        return host_ended(proc, lines, events, err, session, writing=e)
    finally:
        if idle_now:  # (else it is mid-turn, stopped or out of time: ended at once below)
            try:
                send({"type": "close"})
                proc.stdin.close()
                proc.wait(timeout=10)
            except Exception:
                pass
        processes.kill(proc)


def implement(game, project, prompt, run_id=None, say=None, client=None, model=None, timeout=None, stop=None,
              steering=None, play_seconds=None, describer=None, activity=None):
    """Research, write, run and revise a harness for prompt on the live game; returns the report. The same arguments
    and report as autopilot.agents.claude_code.implement (the report says "agent": "opencode")."""
    provider = current_provider()
    model = model or sessionmod.model(provider)
    exe = bun()
    ensure_installed(exe)
    session = sessionmod.open_session(game, project, prompt, run_id, say=say, client=client, timeout=timeout,
                                      stop=stop, steering=steering, play_seconds=play_seconds,
                                      describer=describer, activity=activity)
    session.note(f"agent: OpenCode (Bun {exe}), {provider} {model}")
    # (OpenCode's own home for this run: not in the game's memory)
    work = tempfile.mkdtemp(prefix="autopilot-opencode-")
    server = ToolServer(session, say=session.say)
    events = Events(session, say=session.say)
    config = host_config(session, server.url, model, system_text(engines.of(game), session.run_rel), provider)
    message = sessionmod.first_message(session)

    done = sessionmod.watch_steering(session)
    t0 = time.time()
    stopped = None
    try:
        game.pause(True)  # (in the try: a game that doesn't answer still gets cleaned up and reported)
        with open(os.path.join(session.run_dir, "opencode.log"), "wb") as err:
            stopped = run_host([exe, "run", HOST], config, message, host_env(work, provider), events, session,
                               t0 + session.budget, err)
    except Exception as e:
        stopped = f"{type(e).__name__}: {e}"
        session.note(traceback.format_exc())
    finally:
        done.set()
        server.close()
        if session.sandbox:
            if stopped == "stopped":
                session.sandbox.stop()
            session.sandbox.close()
        remove_tree(work)
        try:
            game.release_all()
            game.pause(False)
        except Exception:
            pass
    used = events.model or model
    return sessionmod.finish(session, t0, stopped, events.last_text, agent="opencode", provider=provider, model=used,
                             fast=used.endswith("-fast"), cost_usd=round(events.cost, 4), tokens=events.tokens,
                             turns=events.turns)
