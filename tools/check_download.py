"""Check an Autopilot download on a computer that has never had Autopilot: install the agent service from the zip as
the plugin's first-run setup does, update it in place while a program runs from it, then play a game through it with
BYOK keys.

    python tools/check_download.py <Autopilot-*-Godot.zip> --godot <Godot 4.7+ console executable> --disposable

1. Unzips the download and runs the installer it carries, as the Godot addon's Install does (lib/service.gd): uv,
   Autopilot, Bun and OpenCode's packages come from the internet.
2. Saves what the first-run setup's BYOK card saves (Autopilot/preferences.json and autopilot.env: OpenAI picked, with
   made-up keys; sharing off), starts the installed `autopilot serve`, and plays the repository's Godot test game
   headless, with the download's addon, from its command line (--playtest=verify).
3. Passes when the run went all the way to OpenAI: OpenCode under Bun, with OpenAI's default model, turned down with
   OpenAI's own "Incorrect API key" (the keys are made up, so nothing is billed), and the game wrote its report.

It changes this computer's Autopilot settings and installs uv, Bun and Autopilot for this user, so it runs only with
--disposable (a CI runner or a throwaway account). Standard library only: the computer needs no other Python packages.
"""

import argparse
import base64
import glob
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import zipfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GAME = os.path.join(HERE, "plugin", "godot", "tests", "game")
WINDOWS = os.name == "nt"
PORT = 18765
CLAIM = "Verify that pulling the lever raises the gate"


def settings_dir():
    """Autopilot's settings folder (autopilot/bridge.py)."""
    if WINDOWS:
        return os.path.join(os.environ["LOCALAPPDATA"], "Autopilot")
    if sys.platform == "darwin":
        return os.path.join(os.path.expanduser("~"), "Library", "Application Support", "Autopilot")
    return os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "Autopilot")


def step(text):
    print(f"\n== {text}", flush=True)


def fail(text):
    sys.exit(f"FAILED: {text}")


def on_path(name):
    found = shutil.which(name)
    print(f"{name}: {found or 'not installed'}")


def install(addon):
    """Run the installer the addon carries, the way its Install button does."""
    folder = os.path.join(addon, "agent_service")
    if WINDOWS:
        cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", os.path.join(folder, "install.ps1")]
    else:
        cmd = ["sh", os.path.join(folder, "install.sh")]
    started = time.time()
    done = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=1200)
    print(done.stdout[-6000:], done.stderr[-3000:], sep="\n")
    print(f"(the installer took {time.time() - started:.0f} s, exit {done.returncode})")
    if done.returncode != 0:
        fail("the installer failed")


def installed_autopilot():
    """Where the installer puts the `autopilot` command (uv's tool bin folder), as the plugins look for it."""
    path = os.path.join(os.path.expanduser("~"), ".local", "bin", "autopilot.exe" if WINDOWS else "autopilot")
    if not os.path.isfile(path):
        fail(f"no autopilot at {path}")
    return path


def tool_python():
    uv = os.path.join(os.path.expanduser("~"), ".local", "bin", "uv.exe" if WINDOWS else "uv")
    uv = uv if os.path.isfile(uv) else shutil.which("uv")  # (one the computer had before)
    tools = subprocess.run([uv, "tool", "dir"], capture_output=True, text=True, check=True).stdout.strip()
    if WINDOWS:
        return os.path.join(tools, "autopilot", "Scripts", "python.exe")
    return os.path.join(tools, "autopilot", "bin", "python")


def rebuild_in_place(folder):
    """The wheel in folder rebuilt where it is, as a plugin updated in place has it: the same name and date (every
    download's files are dated 2026-01-01), one module more (autopilot/update_check.py), its RECORD made again."""
    path = glob.glob(os.path.join(folder, "autopilot-*.whl"))[0]
    dated = os.stat(path).st_mtime
    with zipfile.ZipFile(path) as z:
        files = [(i, z.read(i.filename)) for i in z.infolist()]
    record = next(i for i, _ in files if i.filename.endswith(".dist-info/RECORD"))
    files = [(i, data) for i, data in files if i is not record]
    files.append((zipfile.ZipInfo("autopilot/update_check.py", record.date_time), b"REBUILT = True\n"))
    lines = [f"{i.filename},sha256={base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode()},"
             f"{len(data)}" for i, data in files]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for i, data in files:
            z.writestr(i, data)
        z.writestr(record, "\n".join(lines + [f"{record.filename},,"]) + "\n")
    os.utime(path, (dated, dated))


def save_byok(settings):
    """What the first-run setup saves for BYOK with OpenAI picked (made-up keys; runs not shared)."""
    os.makedirs(settings, exist_ok=True)
    with open(os.path.join(settings, "preferences.json"), "w", encoding="utf-8") as f:
        json.dump({"version": 1, "onboarded": True, "share_data": False, "access": "own_keys"}, f)
    with open(os.path.join(settings, "autopilot.env"), "a", encoding="utf-8") as f:
        f.write("\nTYPESAFE_API_KEY=ts-not-a-real-key-0000\nAUTOPILOT_OPENCODE_PROVIDER=openai\n"
                "OPENAI_API_KEY=sk-proj-not-a-real-key-0000\n")


def assemble(addon, godot, dest):
    """The repository's test game with the download's addon, imported headless."""
    shutil.copytree(GAME, dest, ignore=shutil.ignore_patterns(".godot", "addons"))
    shutil.copytree(addon, os.path.join(dest, "addons", "autopilot"))
    subprocess.run([godot, "--headless", "--path", dest, "--import"], capture_output=True, timeout=600)
    if not os.path.isfile(os.path.join(dest, ".godot", "global_script_class_cache.cfg")):
        fail("importing the test game failed")


def wait_for_port(port, seconds, proc):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if proc.poll() is not None:
            fail(f"autopilot serve exited ({proc.returncode})")
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.5)
    fail(f"autopilot serve wasn't listening on {port} after {seconds} s")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("zip", help="the Godot download (Autopilot-<version>-Godot.zip)")
    ap.add_argument("--godot", required=True, help="the Godot 4.7+ console executable")
    ap.add_argument("--disposable", action="store_true", help="this computer's Autopilot setup may be changed")
    args = ap.parse_args()
    if not args.disposable:
        sys.exit("This installs uv, Bun and Autopilot and changes Autopilot's settings: run it with --disposable, on a "
                 "CI runner or a throwaway account only.")
    work = tempfile.mkdtemp(prefix="rg-download-check-")
    settings = settings_dir()

    step("Before: what this computer has")
    for name in ("uv", "bun", "autopilot"):
        on_path(name)

    step("Unzip the download")
    with zipfile.ZipFile(args.zip) as z:
        z.extractall(os.path.join(work, "download"))
    addon = os.path.join(work, "download", "addons", "autopilot")
    if not os.path.isfile(os.path.join(addon, "agent_service", "install.ps1" if WINDOWS else "install.sh")):
        fail("the download carries no installer")
    if not WINDOWS:
        os.chmod(os.path.join(addon, "agent_service", "install.sh"), 0o755)

    step("Install the agent service (the setup's Install)")
    install(addon)
    autopilot = installed_autopilot()
    print(f"autopilot: {autopilot}")
    ready = subprocess.run([tool_python(), "-I", "-c", "import os; from autopilot.agents import opencode as o; "
                            "print(o.bun()); print(os.path.isdir(os.path.join(o.HOST_DIR, 'node_modules', "
                            "'@opencode', 'sdk')))"], capture_output=True, text=True)
    print(ready.stdout, ready.stderr[-2000:])
    if ready.returncode != 0 or not ready.stdout.strip().endswith("True"):
        fail("OpenCode's packages weren't installed under Bun")
    env_file = os.path.join(settings, "autopilot.env")
    if os.path.isfile(env_file) and "AUTOPILOT_AGENT" in open(env_file, encoding="utf-8").read():
        fail("the installer fell back to Claude Code (AUTOPILOT_AGENT in autopilot.env)")

    step("Update it in place while a program runs from it (another session's `autopilot mcp`)")
    # (on Windows its files can't be deleted: `uv tool install --force` stopped halfway, Lib gone, before the
    # installer moved the installed one aside. And a wheel rebuilt where the last one was, dated alike, was taken
    # from uv's cache: the installer said it installed it, and the build before stayed)
    holder = subprocess.Popen([tool_python(), "-I", "-c", "import autopilot.mcp, time; time.sleep(900)"])
    time.sleep(5)
    try:
        rebuild_in_place(os.path.join(addon, "agent_service"))
        install(addon)
        works = subprocess.run([tool_python(), "-I", "-c", "import autopilot.cli"], capture_output=True, text=True)
        print(works.stderr[-2000:])
        if works.returncode != 0:
            fail("after the update the installed Autopilot doesn't import")
        # (-I here and above: the installed Autopilot, not a checkout's in the folder this runs in)
        rebuilt = subprocess.run([tool_python(), "-I", "-c", "import autopilot.update_check"], capture_output=True,
                                 text=True)
        if rebuilt.returncode != 0:
            fail("the update installed the build before it again (uv's cache), not the wheel rebuilt in its place")
        if holder.poll() is not None:
            fail("the update ended the program that ran from the installed Autopilot")
    finally:
        holder.kill()
        holder.wait()

    step("BYOK with OpenAI, as the first-run setup saves it")
    save_byok(settings)

    step("Assemble the test game with the download's addon")
    game = os.path.join(work, "game")
    assemble(addon, args.godot, game)

    step("Play it through the installed agent service")
    memory = os.path.join(work, "memory")
    env = {k: v for k, v in os.environ.items() if not k.endswith("_API_KEY") and not k.startswith("AUTOPILOT_")}
    env.update(AUTOPILOT_MEMORY=memory, AUTOPILOT_SHARE="0")
    serve_log = open(os.path.join(work, "serve.log"), "w", encoding="utf-8", errors="replace")
    serve = subprocess.Popen([autopilot, "serve", "--port", str(PORT)], stdout=serve_log, stderr=subprocess.STDOUT,
                             env=env)
    report_dir = os.path.join(work, "report")
    try:
        wait_for_port(PORT, 120, serve)
        played = subprocess.run([args.godot, "--headless", "--path", game, "--", "--playtest=verify",
                                 f"--playtest-claim={CLAIM}", f"--playtest-url=ws://127.0.0.1:{PORT}/v1/bridge",
                                 f"--playtest-report-dir={report_dir}"],
                                capture_output=True, text=True, errors="replace", timeout=900, env=env)
        print(f"the game exited {played.returncode}")
    finally:
        serve.terminate()
        try:
            serve.wait(30)
        except subprocess.TimeoutExpired:
            serve.kill()
        serve_log.close()
    print(open(os.path.join(work, "serve.log"), encoding="utf-8", errors="replace").read()[-4000:])

    step("What the run did")
    logs = glob.glob(os.path.join(memory, "*", "runs", "*", "implementer.log"))
    if not logs:
        fail(f"no run in {memory}; the game said:\n{played.stdout[-3000:]}")
    text = open(logs[0], encoding="utf-8", errors="replace").read()
    print(text[-3000:])
    if "OpenCode (Bun" not in text or "openai gpt-6.1-sol" not in text:
        fail("the run didn't use OpenCode with OpenAI's model")
    if "Incorrect API key" not in text:
        fail("the run didn't reach OpenAI (no 'Incorrect API key' from it)")
    result = os.path.join(report_dir, "result.json")
    if not os.path.isfile(result):
        fail(f"the game wrote no {result}")
    print(open(result, encoding="utf-8").read()[:2000])
    print("\nPASSED: installed from the download, and a BYOK run went through OpenCode to OpenAI.")


if __name__ == "__main__":
    main()
