"""Processes a run starts end with it.

Autopilot starts other processes: the agent (OpenCode or Claude Code), the sandbox, a headless game for the
bench, the Unreal commandlet that exports a codex. They must never outlive what started them, even when it crashes or is
killed:

    contain()  called once by an entry point. On Windows it puts this process in a job object that closes when the
               process ends in any way (exit, crash, being killed); closing it ends every process in it, and
               everything they started. (On Windows `.venv\\Scripts\\python.exe` is a launcher that starts the real
               Python: killing only the launcher would leave that running.) Elsewhere, children are started in their
               own process group (see GROUP) and the groups still running are ended at exit.
    kill(p)    end a child and everything it started now (e.g. at a time limit), not only the child itself.
    exit_with(pid)  end this process (and so everything it started) once the process `pid` has ended: for an Autopilot
               another program starts and owns (an editor, a launcher), which may itself be killed without warning.
               What the last run left going (its data being saved and sent: `finish`) is waited for first.
    keep_responsive()  on macOS, ask the system not to treat this process as one that can wait (App Nap, timer
               coalescing): the runtime steps every 50 ms or so, and a process started in the background by another
               app (the Aura client) could otherwise get its timers stretched.
"""

import atexit
import ctypes
import os
import signal
import subprocess
import sys
import threading
import time

WINDOWS = sys.platform == "win32"
# Popen/create_subprocess_exec arguments that let kill() end a child's whole tree where there are no job
# objects: the child leads its own process group
GROUP = {} if WINDOWS else {"start_new_session": True}

_job = None  # this process's job object (Windows), once contain() made it
_started = []  # children to end at exit, where there are no job objects


def contain():
    """Make every process started from now on (and what those start) end when this one does."""
    global _job
    if _job is not None:
        return
    if not WINDOWS:
        _job = True
        atexit.register(_end_started)
        return
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = ctypes.c_void_p
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    kernel32.SetInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
    kernel32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

    class Basic(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", ctypes.c_uint32), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", ctypes.c_uint32),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", ctypes.c_uint32),
                    ("SchedulingClass", ctypes.c_uint32)]

    class Extended(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", ctypes.c_uint64 * 6),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    KILL_ON_JOB_CLOSE, EXTENDED_LIMIT_INFORMATION = 0x2000, 9
    job = kernel32.CreateJobObjectW(None, None)  # its handle isn't inherited: only this process holds it
    info = Extended()
    info.BasicLimitInformation.LimitFlags = KILL_ON_JOB_CLOSE
    if not job or not kernel32.SetInformationJobObject(job, EXTENDED_LIMIT_INFORMATION, ctypes.byref(info),
                                                       ctypes.sizeof(info)):
        return  # best effort: kill() still ends trees
    # This process joins the job, so every process it starts from now on is in it too (jobs nest on Windows 8+)
    if kernel32.AssignProcessToJobObject(job, kernel32.GetCurrentProcess()):
        _job = job


def track(proc):
    """Remember a child to end at exit, where there are no job objects. Returns it."""
    if not WINDOWS:
        _started.append(proc)
    return proc


def kill(proc):
    """End a child (a Popen or asyncio process) and everything it started. No-op if it has ended."""
    if not _running(proc):
        return
    if WINDOWS:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)  # started with GROUP: it leads its group
        except (ProcessLookupError, PermissionError):
            pass
    try:
        proc.kill()  # in case the tree kill missed it
    except (ProcessLookupError, OSError):
        pass


def _running(proc):
    poll = getattr(proc, "poll", None)  # a Popen; an asyncio process only has returncode
    return (poll() if poll else proc.returncode) is None


def _end_started():
    for proc in _started:
        kill(proc)


def alive(pid):
    """Whether the process `pid` is running (an unknown or ended one isn't)."""
    if pid <= 0:
        return False
    if WINDOWS:
        # (not os.kill(pid, 0): on Windows that terminates the process)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.get_last_error() == 5  # (access denied: it exists, it's someone else's)
        try:
            code = ctypes.c_uint32()
            return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def exit_with(pid, every=2.0, why=print, finish=None):
    """End this process soon after the process `pid` ends (checked every `every` seconds, on a daemon thread): what it
    started ends with it (contain()). finish: called before the end, once what it started has ended and where it listens
    is taken back (a new owner starts an Autopilot of its own): what must not be cut short (a run's data being sent)."""

    def watch():
        while alive(pid):
            time.sleep(every)
        why(f"The process that started Autopilot ({pid}) has ended: Autopilot ends too")
        atexit._run_exitfuncs()  # (what exiting does: children ended, files it announced taken back)
        _end_started()
        if finish is not None:
            try:
                finish()
            except Exception as e:  # noqa: BLE001 - ending all the same
                why(f"note: couldn't finish before ending: {type(e).__name__}: {e}")
        os._exit(0)  # (the job object, on Windows, ends what's left)

    threading.Thread(target=watch, daemon=True, name="exit-with").start()


_activity = None  # (the NSProcessInfo activity keep_responsive began: held for the life of the process)


def keep_responsive():
    """macOS only (a no-op elsewhere): begin an NSProcessInfo activity that is latency critical and user initiated,
    so App Nap and timer coalescing leave this process's timers as they are. Best effort: never raises."""
    global _activity
    if sys.platform != "darwin" or _activity is not None:
        return
    try:
        objc = ctypes.CDLL("/usr/lib/libobjc.dylib")
        ctypes.CDLL("/System/Library/Frameworks/Foundation.framework/Foundation")
        objc.objc_getClass.restype = ctypes.c_void_p
        objc.objc_getClass.argtypes = [ctypes.c_char_p]
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.sel_registerName.argtypes = [ctypes.c_char_p]
        address = ctypes.cast(objc.objc_msgSend, ctypes.c_void_p).value
        ptr = ctypes.c_void_p
        call = ctypes.CFUNCTYPE(ptr, ptr, ptr)(address)
        call_text = ctypes.CFUNCTYPE(ptr, ptr, ptr, ctypes.c_char_p)(address)
        call_begin = ctypes.CFUNCTYPE(ptr, ptr, ptr, ctypes.c_uint64, ptr)(address)
        info = call(objc.objc_getClass(b"NSProcessInfo"), objc.sel_registerName(b"processInfo"))
        reason = call_text(objc.objc_getClass(b"NSString"), objc.sel_registerName(b"stringWithUTF8String:"),
                           b"Autopilot is playing a game")
        # NSActivityUserInitiated (0x00FFFFFF) | NSActivityLatencyCritical (0xFF00000000)
        activity = call_begin(info, objc.sel_registerName(b"beginActivityWithOptions:reason:"),
                              0x00FFFFFF | 0xFF00000000, reason)
        if activity:
            _activity = call(activity, objc.sel_registerName(b"retain"))
    except Exception:  # noqa: BLE001 - only a hint to the system
        pass
