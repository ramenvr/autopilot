"""Remove Autopilot from this computer: `autopilot uninstall` (each plugin's Remove Autopilot from this computer, on its
Settings page, runs it).

    autopilot uninstall [--keep-memory] [--yes]

What it removes, in this order:
1. the agent services running: the one brain.json names and any other started from the installed `autopilot` command
   (never the Aura app's, which says where it is in aura-brain.json, nor this process or what started it);
2. the Aura sign-in Autopilot kept (aura-session.json), ended on Aura's side too;
3. the settings folder (Autopilot/ in the user settings folder: autopilot/bridge.py brain_file): the first-run setup's
   choices, the keys, the logs, this machine's upload id, and what Autopilot learned about each game (memory/, unless
   --keep-memory);
4. with the memory, what each game's plugin keeps in its project about the runs (the run history, report files,
   recordings and the codex export: autopilot/engines, plugin_data), for every project memory/ says where it is, and
   Claude Code's logs of those runs in its cache folder (agent_logs); and each of those games' memory kept with Aura
   (erase_stored_memory): the team's, which goes for everyone on the team (the plugins' Delete Autopilot's memories box
   says so), and this install's own (runs with the user's own keys), each with its reset copies;
5. what runs that were cut short left in the temporary folder, and what `autopilot connect` set up in the user's own
   Claude Code and Codex (their autopilot MCP server, skill and frame describer: autopilot/connect.py; files of the
   user's own by those names stay);
6. the `autopilot` command (`uv tool uninstall autopilot`). On Windows,
   when this process runs from that command, after it has ended: a program's files can't be removed while it runs.

Shared run data is never removed: what was sent stays. With --keep-memory the memory kept with Aura stays too (a later
install gets it back); without it, it is deleted with the memory, while this computer is still signed in to Aura (an
Aura sign-in kept here, or the Aura app's): a game whose team copy can't be reached (not signed in) is named, and its
copy stays. Shared runs not sent yet (autopilot/training.py) are sent first, while sharing is on, as the next run would
send them. One that can't be sent now (a run through Aura is sent only with a run through Aura) is removed with the
rest of the memory folder, and this says so.

Never removed either: the Aura app's own file (aura-brain.json), a repository checkout's own files (its memory/, .env
and .venv: delete the checkout for those, or AUTOPILOT_MEMORY's folder), and uv and Bun, which other programs may use
(this says how to remove them). The plugins themselves and their settings in each project stay until the plugin is
removed from it (each plugin's README says what they are).

Exit codes: 0 all of it removed (or, the command, being removed once this ends), 3 removed but for what it names
(files in use, the command), 1 nothing removed (not confirmed).
"""

import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time

import requests

from . import account, aura, aura_login, aura_memory, bridge, codex, engines, processes, retry, training

KEPT = 3
WINDOWS = sys.platform == "win32"
TOOLS = ("autopilot",)  # (uv tools: Autopilot's)
AURA_FILE = "aura-brain.json"  # (the Aura app's: it writes it again whenever its agent service starts)
MEMORY = "memory"
# What runs leave in the temporary folder, removed when they end; a run that was killed leaves them
TEMP_PREFIXES = ("autopilot-opencode-", "autopilot-codex-", "autopilot-system-", "autopilot-selftest-",
                 "autopilot-upload-", "autopilot_codex.lock")
WAIT_SECONDS = 120.0  # how long the command's removal waits for the processes running it to end
ERASE_TIMEOUT = 130  # seconds for Aura to erase a game's memory (it stops starting deletes after 90 s)


def settings_folder():
    return os.path.dirname(bridge.brain_file())


# -- shared run data -------------------------------------------------------------------------------------------------

def sharing():
    """Whether runs are shared now (the first-run setup's Privacy choice, and AUTOPILOT_SHARE)."""
    if (os.environ.get("AUTOPILOT_SHARE") or "").strip() == "0":
        return False
    return account.preferences().get("share_data", True) is not False


def projects(memory):
    """The project folders in a memory folder (not .reset-backup and the like)."""
    if not os.path.isdir(memory):
        return []
    return [os.path.join(memory, name) for name in sorted(os.listdir(memory))
            if not name.startswith(".") and os.path.isdir(os.path.join(memory, name))]


def project_key(folder):
    """The key a project's data is stored under with this machine's install token, as a run gives it
    (autopilot/cli.py): its id, else its name."""
    try:
        with open(os.path.join(folder, "project.json"), encoding="utf-8") as f:
            info = json.load(f)
        if isinstance(info, dict) and info.get("project_id"):
            return str(info["project_id"])
    except (OSError, ValueError):
        pass
    return os.path.basename(folder)


def send_unsent(memory, say=print, upload=training.upload):
    """Send each project's shared runs not sent yet, with this machine's install token, while sharing is on (a run
    through Aura goes only with a run through Aura). Returns {project folder: how many shared runs are still unsent}
    for the projects that have some."""
    left = {}
    for folder in projects(memory):
        unsent = training.unsent(folder)
        if not unsent:
            continue
        if sharing() and any(not m.get("through_aura") for _, m in unsent):
            say(f"Sending {os.path.basename(folder)}'s shared runs not sent yet")
            upload(folder, project_key(folder))
            unsent = training.unsent(folder)
        if unsent:
            left[folder] = len(unsent)
    return left


# -- the memory kept with Aura -------------------------------------------------------------------------------------

class EraseError(Exception):
    """An erase Aura refused or failed. passing: whether trying again soon may work (retry.STATUSES)."""

    def __init__(self, message, passing=False):
        super().__init__(message)
        self.passing = passing


def erase(url, token, project, http=requests):
    """Aura's memory endpoint deletes a game's memory and its reset copies (op erase): whose copy, the token's (the
    user's Aura session: their team's; an install token: the install's). Returns how many files it deleted; raises
    EraseError."""
    r = http.post(url, json={"op": "erase", "project": project}, headers={"Authorization": f"Bearer {token}"},
                  timeout=ERASE_TIMEOUT)
    if r.status_code != 200:
        try:
            message = (r.json() or {}).get("error") or r.text[:200]
        except ValueError:
            message = r.text[:200]
        raise EraseError(f"HTTP {r.status_code}: {message}", passing=r.status_code in retry.STATUSES)
    return int((r.json() or {}).get("deleted") or 0)


def aura_sessions(memory, http=requests):
    """This computer's Aura sign-ins, as [(Aura's memory endpoint, an access token)]: each kept here (aura_login), and
    the Aura app's when it answers (for its server)."""
    found = {}
    for server in aura_login.servers():
        token = aura_login.access_token(server, http)
        if token:
            found.setdefault(token, server)
    places = [_project_path(folder) for folder in projects(memory)]
    _, token = aura.client_session(next((p for p in places if p), None), http)
    if token:
        found.setdefault(token, aura.api_url() or aura.default_url())
    return [(f"{server.rstrip('/')}/api/autopilot/memory", token) for token, server in found.items()]


def install_copy(folder):
    """This install's own memory copy (runs with the user's own keys, aura_memory.install_memory): (its memory
    endpoint, the install token), or None when this computer never registered one."""
    try:
        with open(os.path.join(folder, training.INSTALL_FILE), encoding="utf-8") as f:
            saved = json.load(f)
    except (OSError, ValueError):
        return None
    token = saved.get("token") if isinstance(saved, dict) else None
    if not token:
        return None
    url = (os.environ.get("AUTOPILOT_MEMORY_URL") or "").strip()
    return url or (saved.get("url") or training.endpoint()).rsplit("/", 1)[0] + "/memory", token


def erase_stored_memory(folder, memory, say=print, http=requests, sleep=None):
    """Delete each game's memory this computer kept with Aura (memory/'s games, by their keys): the team's (the game
    synced by runs through Aura: aura_memory.SYNC_FILE) with each Aura sign-in on this computer, and this install's own
    (aura_memory.INSTALL_SYNC_FILE) with its install token, each with its reset copies; shared run data is never
    touched. Returns what is left, for the summary: a game whose copy couldn't be deleted, or whose team copy can't be
    reached here (nobody is signed in)."""
    def synced(game, sync_file):
        return os.path.exists(os.path.join(game, sync_file))

    games = [g for g in projects(memory)
             if synced(g, aura_memory.SYNC_FILE) or synced(g, aura_memory.INSTALL_SYNC_FILE)]
    if not games:
        return []
    teams = aura_sessions(memory, http) if any(synced(g, aura_memory.SYNC_FILE) for g in games) else []
    own = install_copy(folder)
    left = []
    for game in games:
        name = os.path.basename(game)
        copies = [(url, token, "your Aura team") for url, token in teams] if synced(game, aura_memory.SYNC_FILE) else []
        if own and synced(game, aura_memory.INSTALL_SYNC_FILE):
            copies.append((own[0], own[1], "Aura for this computer"))
        for url, token, whose in copies:
            try:
                deleted = retry.call(lambda url=url, token=token, key=project_key(game): erase(url, token, key, http),
                                     f"deleting {name}'s memory kept with {whose}", 2, sleep=sleep, say=say)
            except Exception as e:  # noqa: BLE001 - said, and named in the summary
                say(f"Couldn't delete {name}'s memory kept with {whose}: {e}")
                left.append(f"{name}'s memory kept with {whose}")
                continue
            if deleted:
                say(f"Deleted {name}'s memory kept with {whose} ({deleted} file{'s' if deleted != 1 else ''})")
        if not teams and synced(game, aura_memory.SYNC_FILE):
            say(f"{name}'s memory kept with your Aura team stays: this computer isn't signed in to Aura")
            left.append(f"{name}'s memory kept with your Aura team (not signed in)")
    return left


def _project_path(folder):
    """Where a memory folder's project is on this machine (its project.json), or None."""
    try:
        with open(os.path.join(folder, "project.json"), encoding="utf-8") as f:
            info = json.load(f)
        return info.get("project") if isinstance(info, dict) else None
    except (OSError, ValueError):
        return None


def project_data(memory):
    """What the plugins keep about the runs in each project memory/ says where it is (its project.json's project and
    engine, as a run remembers them): [paths that are there]."""
    found = []
    for folder in projects(memory):
        try:
            with open(os.path.join(folder, "project.json"), encoding="utf-8") as f:
                info = json.load(f)
            project = info.get("project") if isinstance(info, dict) else None
        except (OSError, ValueError):
            continue
        if not project or not os.path.exists(project):
            continue
        found += [p for p in engines.get(info.get("engine")).plugin_data(project) if os.path.lexists(p)]
    return found


# -- processes -------------------------------------------------------------------------------------------------------

def listing():
    """{pid: (parent pid, what it runs: its program and command line)} of the processes running (best effort: {} when
    they can't be listed)."""
    found = {}
    try:
        if WINDOWS:
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,ExecutablePath,CommandLine"
                 " | ConvertTo-Json -Compress"],
                capture_output=True, text=True, timeout=60, creationflags=subprocess.CREATE_NO_WINDOW).stdout
            rows = json.loads(out or "[]")
            for row in rows if isinstance(rows, list) else [rows]:
                found[int(row["ProcessId"])] = (int(row.get("ParentProcessId") or 0),
                                                f"{row.get('ExecutablePath') or ''} {row.get('CommandLine') or ''}")
        else:
            out = subprocess.run(["ps", "-eo", "pid=,ppid=,args="], capture_output=True, text=True,
                                 timeout=60).stdout
            for line in out.splitlines():
                parts = line.split(None, 2)
                if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
                    found[int(parts[0])] = (int(parts[1]), parts[2] if len(parts) > 2 else "")
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        pass
    return found


def lineage(found, pid=None):
    """This process and those that started it (the `autopilot` command's launcher, a plugin's shell, the editor)."""
    pid = os.getpid() if pid is None else pid
    chain = []
    while pid and pid not in chain:
        chain.append(pid)
        pid = found.get(pid, (0, ""))[0]
    return chain


def announced_pid(folder):
    """The pid of the agent service brain.json in `folder` names, or 0."""
    try:
        with open(os.path.join(folder, bridge.BRAIN_FILE), encoding="utf-8") as f:
            return int(json.load(f).get("pid") or 0)
    except (OSError, ValueError, TypeError, AttributeError):
        return 0


def to_stop(found, folder, places):
    """The pids of the agent services to stop: the one brain.json names (when what runs under its pid is an
    Autopilot: a pid can be reused) and those running from `places` (the installed command's files), but never this
    process or what started it."""
    mine = set(lineage(found))
    places = [os.path.normcase(p) for p in places if p]
    pids = []
    announced = announced_pid(folder)
    if announced in found and "autopilot" in found[announced][1].lower():
        pids.append(announced)
    for pid, (_, text) in found.items():
        text = os.path.normcase(text)
        if pid not in pids and any(p in text for p in places):
            pids.append(pid)
    pids = [pid for pid in pids if pid not in mine]
    # (one started by another of them, e.g. the Python the command's launcher runs, ends with it)
    return [pid for pid in pids if not set(lineage(found, pid)[1:]) & set(pids)]


def end(pid):
    """End a process and what it started."""
    if WINDOWS:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True,
                       creationflags=subprocess.CREATE_NO_WINDOW)
        return
    try:
        if os.getpgid(pid) == pid:  # (one a plugin started leads its group: its children end with it)
            os.killpg(pid, signal.SIGTERM)
        else:
            os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    deadline = time.time() + 5
    while processes.alive(pid) and time.time() < deadline:
        time.sleep(0.1)
    if processes.alive(pid):
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


# -- the command -----------------------------------------------------------------------------------------------------

def uv():
    """uv, on PATH or where its installer puts it; None when it isn't on this computer."""
    found = shutil.which("uv")
    if found:
        return found
    exe = os.path.join(os.path.expanduser("~"), ".local", "bin", "uv.exe" if WINDOWS else "uv")
    return exe if os.path.isfile(exe) else None


def _uv(exe, *args):
    try:
        r = subprocess.run([exe, *args], capture_output=True, text=True, timeout=120,
                           creationflags=subprocess.CREATE_NO_WINDOW if WINDOWS else 0)
        return r.stdout if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def installed(exe):
    """{tool: [its environment's folder, its command]} for the uv tools of TOOLS that are installed."""
    listed = _uv(exe, "tool", "list") or ""
    names = [name for name in TOOLS if any(line.startswith(f"{name} ") for line in listed.splitlines())]
    if not names:
        return {}
    envs = (_uv(exe, "tool", "dir") or "").strip()
    bins = (_uv(exe, "tool", "dir", "--bin") or "").strip() or os.path.join(os.path.expanduser("~"), ".local", "bin")
    return {name: [os.path.join(envs, name) if envs else "", os.path.join(bins, name + (".exe" if WINDOWS else ""))]
            for name in names}


# The command's removal once the processes running it have ended (Windows can't remove a program's files while it
# runs): run by the Python Autopilot's environment was made from, which isn't removed with it. argv: uv, the pids to
# wait for, the tools, the log
WAIT = r"""
import ctypes, subprocess, sys, time
uv, pids, tools, log = sys.argv[1], [int(p) for p in sys.argv[2].split(",") if p], sys.argv[3].split(","), sys.argv[4]
def alive(pid):
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.OpenProcess.restype = ctypes.c_void_p
    h = k.OpenProcess(0x1000, False, pid)
    if not h:
        return False
    code = ctypes.c_uint32()
    ok = k.GetExitCodeProcess(ctypes.c_void_p(h), ctypes.byref(code))
    k.CloseHandle(ctypes.c_void_p(h))
    return bool(ok) and code.value == 259
deadline = time.time() + float(sys.argv[5])
while any(alive(p) for p in pids) and time.time() < deadline:
    time.sleep(0.25)
with open(log, "a", encoding="utf-8") as f:
    for attempt in range(5):
        r = subprocess.run([uv, "tool", "uninstall", *tools], capture_output=True, text=True,
                           creationflags=0x08000000)
        if r.returncode == 0 or "is not installed" in r.stderr:
            f.write("Removed the autopilot command\n")
            break
        time.sleep(2)
    else:
        f.write(f"Couldn't remove the autopilot command: {r.stderr.strip()}"
                f" (run: uv tool uninstall {' '.join(tools)})\n")
"""


def remove_tools(exe, tools, found, log=None, say=print):
    """Uninstall the uv tools `tools` ({name: [environment, command]}). Returns "removed", "later" (Windows, this
    process running from one: removed once it has ended, by a process of its own) or "failed"."""
    names = list(tools)
    envs = [os.path.normcase(env) for env, _ in tools.values() if env]
    if WINDOWS and any(os.path.normcase(sys.prefix).startswith(env) for env in envs):
        running = [pid for pid in lineage(found)
                   if any(os.path.normcase(p) in os.path.normcase(found.get(pid, (0, ""))[1])
                          for p in [e for e, _ in tools.values()] + [c for _, c in tools.values()] if p)]
        python = getattr(sys, "_base_executable", None) or sys.executable
        log = log or os.path.join(tempfile.gettempdir(), "autopilot-uninstall.log")
        argv = [python, "-I", "-c", WAIT, exe, ",".join(map(str, running or [os.getpid()])), ",".join(names), log,
                str(WAIT_SECONDS)]
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        for extra in (0x01000000, 0):  # (CREATE_BREAKAWAY_FROM_JOB: outlive a job what started this is in, if allowed)
            try:
                subprocess.Popen(argv, cwd=tempfile.gettempdir(), creationflags=flags | extra, close_fds=True,
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                say(f"The autopilot command is removed once this has ended (what happens is in {log})")
                return "later"
            except OSError:
                continue
        return "failed"
    try:
        r = subprocess.run([exe, "tool", "uninstall", *names], capture_output=True, text=True, timeout=300,
                           creationflags=subprocess.CREATE_NO_WINDOW if WINDOWS else 0)
    except (OSError, subprocess.SubprocessError) as e:
        say(f"Couldn't remove the autopilot command: {e} (run: uv tool uninstall {' '.join(names)})")
        return "failed"
    if r.returncode != 0:
        say(f"Couldn't remove the autopilot command: {r.stderr.strip()} (run: uv tool uninstall {' '.join(names)})")
        return "failed"
    say("Removed the autopilot command")
    return "removed"


# -- files -----------------------------------------------------------------------------------------------------------

def _remove(path):
    """Remove a file or folder, read-only files too (on Windows rmtree can't remove those as they are). Returns
    whether it is gone."""
    def retry(func, p, _):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except OSError:
            pass

    try:
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path, onexc=retry)
        else:
            os.chmod(path, stat.S_IWRITE)
            os.remove(path)
    except OSError:
        pass
    return not os.path.lexists(path)


def remove_folder(folder, keep=()):
    """Remove what is in `folder` but the names in `keep`, then the folder itself if nothing is left in it. Returns
    the paths that couldn't be removed (in use)."""
    if not os.path.isdir(folder):
        return []
    failed = [os.path.join(folder, name) for name in sorted(os.listdir(folder))
              if name not in keep and not _remove(os.path.join(folder, name))]
    if not os.listdir(folder):
        try:
            os.rmdir(folder)
        except OSError:
            failed.append(folder)
    return failed


def claude_cache():
    """Claude Code's cache folder (the Coding Agent's: it logs each project's tool calls there, in a folder named after
    the project's memory folder)."""
    if WINDOWS:
        base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
        return os.path.join(base, "claude-cli-nodejs", "Cache")
    if sys.platform == "darwin":
        return os.path.join(os.path.expanduser("~"), "Library", "Caches", "claude-cli-nodejs")
    return os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache"),
                        "claude-cli-nodejs")


def agent_logs(memory, cache=None):
    """Claude Code's logs of the runs in memory/: its cache's folders named after a project folder in it (the path with
    every character but letters and digits made "-")."""
    cache = cache or claude_cache()
    named = re.sub(r"[^A-Za-z0-9]", "-", os.path.abspath(memory)) + "-"
    try:
        return [os.path.join(cache, n) for n in sorted(os.listdir(cache)) if n.startswith(named)]
    except OSError:
        return []


def temporary_leftovers(temp=None):
    temp = temp or tempfile.gettempdir()
    try:
        return [os.path.join(temp, name) for name in sorted(os.listdir(temp)) if name.startswith(TEMP_PREFIXES)]
    except OSError:
        return []


# -- uninstall -------------------------------------------------------------------------------------------------------

def plan(folder, exe):
    """What uninstalling would remove, as lines to show (and the tools it would uninstall)."""
    tools = installed(exe) if exe else {}
    lines = []
    if os.path.isdir(folder):
        lines.append(f"{folder}: your keys, Aura sign-in, first-run setup choices and logs, and what Autopilot learned "
                     "about each game")
    if tools:
        lines.append("The autopilot command (" + ", ".join(env for env, _ in tools.values() if env) + ")")
    return lines, tools


def uninstall(folder=None, keep_memory=False, say=print, exe=None, log=None, upload=training.upload, http=requests):
    """Remove Autopilot from this computer (the module's docstring says what goes and what stays). Returns the exit
    code."""
    folder = folder or settings_folder()
    exe = exe if exe is not None else uv()
    tools = installed(exe) if exe else {}
    kept = []

    found = listing()
    places = [p for paths in tools.values() for p in paths]
    for pid in to_stop(found, folder, places):
        say(f"Stopping the agent service (pid {pid})")
        end(pid)

    keep = {AURA_FILE}
    memory = os.path.join(folder, MEMORY)
    in_projects = []
    if keep_memory:
        keep.add(MEMORY)
        say(f"Kept what Autopilot learned about your games in {memory}")
    else:
        why = "they can't be sent now" if sharing() else "sharing is off"
        for project, count in send_unsent(memory, say=say, upload=upload).items():
            say(f"Removing {count} shared run{'s' if count != 1 else ''} of {os.path.basename(project)} not sent yet"
                f" ({why})")
        in_projects = project_data(memory)  # (read before memory/ goes: its project.json files say where they are)
        in_projects += agent_logs(memory)
        # (before the sign-out and the settings folder go: the sign-ins and the install token are what reach it)
        kept += erase_stored_memory(folder, memory, say=say, http=http)

    for server in aura_login.servers():
        if aura_login.sign_out(server):
            say(f"Signed out of Aura ({server})")

    if os.path.isdir(folder):
        failed = remove_folder(folder, keep)
        kept += failed
        if failed:
            say(f"Removed {folder}, but for what is in use: {', '.join(failed)}")
        else:
            say(f"Removed {folder}" if not os.path.isdir(folder) else f"Removed what is in {folder} but memory/ (kept)")
    for path in in_projects:
        if _remove(path):
            say(f"Removed {path}")
        else:
            say(f"Couldn't remove {path} (in use)")
            kept.append(path)
    for path in temporary_leftovers():
        _remove(path)
    from . import connect

    if not connect.remove_all(say=say):
        kept.append("what `autopilot connect` set up in Claude Code or Codex (see above)")

    if tools:
        if remove_tools(exe, tools, found, log=log, say=say) == "failed":
            kept.append("the autopilot command")
    elif exe is None:
        say("uv isn't on this computer: if Autopilot was installed with pip, uninstall it the same way")

    say("Not removed (other programs may use them): uv (uv's own uninstall: https://docs.astral.sh/uv/) and Bun "
        "(https://bun.sh/docs/installation#uninstall)")
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if codex.default_memory(here) != bridge.brain_file(MEMORY):
        say(f"Not removed: this repository checkout ({here}); delete it to remove it, its memory/ and .env with it")
    elif os.environ.get("AUTOPILOT_MEMORY") and os.path.isdir(os.environ["AUTOPILOT_MEMORY"]):
        say(f"Not removed: AUTOPILOT_MEMORY's folder ({os.environ['AUTOPILOT_MEMORY']})")
    say("Shared run data already sent and the memory kept with Aura stay where they are." if keep_memory else
        "Shared run data already sent stays where it is.")
    if kept:
        say("Autopilot is removed from this computer, but for: " + "; ".join(kept))
        return KEPT
    say("Autopilot is removed from this computer. To remove it from a project too, remove its plugin there.")
    return 0


def main(args):
    os.chdir(tempfile.gettempdir())  # (a folder that is the current one can't be removed on Windows)
    account.load_user_settings()  # (the Aura server autopilot.env names: where its sign-in and shared run data go)
    folder = settings_folder()
    exe = uv()
    lines, _ = plan(folder, exe)
    if not lines:
        print("Autopilot isn't installed on this computer: nothing to remove.")
        return 0
    if not args.yes:
        print("This removes Autopilot from this computer:")
        for line in lines:
            print(f"  - {line}")
        if args.keep_memory:
            print("  What Autopilot learned about your games is kept (--keep-memory).")
        else:
            print("  What Autopilot learned about your games, for all projects on this computer across all editors, "
                  "here and kept with Aura: memories are shared by your team, so deleting them deletes them for your "
                  "entire team (keep them: --keep-memory).")
        print("Shared runs not sent yet are sent first; shared run data already sent stays where it is.")
        if not sys.stdin or not sys.stdin.isatty():
            print("Run it with --yes to remove without asking.")
            return 1
        if input("Remove? [y/N] ").strip().lower() not in ("y", "yes"):
            print("Nothing removed.")
            return 1
    return uninstall(folder, keep_memory=args.keep_memory, exe=exe, log=args.log)
