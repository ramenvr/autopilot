"""End-to-end check of Remove from this computer on a disposable computer (a CI runner): the agent service installed
with uv from this checkout's wheel, a real `autopilot serve`, and the Godot addon's dock in a headless editor pressing
its own buttons, against a sandbox home (HOME, XDG_*, TMPDIR / USERPROFILE, LOCALAPPDATA, APPDATA, TEMP inside one
folder). The memory and the plugins' files in a Godot, a Unity and an Unreal project are laid out as runs leave them.

    python tools/uninstall_e2e.py --godot <Godot 4.7+ (console) executable> --work <empty folder>

Round 1, Delete Autopilot's memories ticked: everything Autopilot put on the computer goes, but the games' files, the
plugins' settings, a process that only names autopilot on its command line, and the uninstall log; the dock forgets its
runs and shows Uninstallation successful; the setup's sign-in shows no BYOK card until Cancel. Round 2, unticked:
memory/ and the projects' plugin files stay. Exit code 0 when every check passes.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WIN, MAC = os.name == "nt", sys.platform == "darwin"
PORT = 18777
DRIVER = r'''@tool
extends EditorPlugin
const DockScript = preload("res://addons/autopilot/dock.gd")
var dock = null
var done := 0
var waiting := ""
var wait_arg := ""
var started := 0.0
var t := 0.0
var cmd_file := ""
var out_file := ""

func _enter_tree() -> void:
	var base := ProjectSettings.globalize_path("res://").get_base_dir().get_base_dir().path_join("driver")
	DirAccess.make_dir_recursive_absolute(base)
	cmd_file = base.path_join("cmd.txt")
	out_file = base.path_join("out.txt")

func _process(delta: float) -> void:
	t += delta
	if t < 0.25:
		return
	t = 0.0
	if dock == null:
		dock = _find(EditorInterface.get_base_control())
		if dock != null:
			_say("dock found")
		return
	if waiting != "":
		var now := Time.get_ticks_msec() / 1000.0
		if _waited() or now - started > 300.0:
			_say("%s %s" % [waiting, "done" if _waited() else "TIMED OUT"])
			waiting = ""
		return
	var lines := FileAccess.get_file_as_string(cmd_file).split("\n", false)
	if done >= lines.size():
		return
	var line := lines[done].strip_edges()
	done += 1
	var word := line.get_slice(" ", 0)
	var arg := line.substr(word.length()).strip_edges()
	_say("> " + line)
	match word:
		"gear":
			for b in dock._gears:
				if _shown(b):
					b.pressed.emit()
					break
		"press":
			_say("pressed: %s" % _press(arg))
		"delete":
			for b in dock.find_children("*", "CheckBox", true, false):
				if "Delete" in b.text and _shown(b):
					b.button_pressed = arg == "on"
					b.toggled.emit(arg == "on")
		"dump":
			_say("--- dump %s runs=%d removal=%s usage=%d\n%s\n--- end" % [arg, dock.runs.size(), dock.service.removal,
				dock._usage_runs.size(), _texts()])
		"quit":
			get_tree().quit()
		_:
			waiting = word
			wait_arg = arg
			started = Time.get_ticks_msec() / 1000.0

func _waited() -> bool:
	match waiting:
		"wait_connected":
			return dock.bridge.is_connected_to_brain()
		"wait_removal":
			return dock.service.removal not in ["", "running"]
		"sleep":
			return Time.get_ticks_msec() / 1000.0 - started > float(wait_arg)
	return true

func _find(node: Node):
	if node.get_script() == DockScript:
		return node
	for c in node.get_children():
		var f = _find(c)
		if f != null:
			return f
	return null

func _shown(c: Node) -> bool:
	var n := c
	while n != null and n != dock:
		if n is CanvasItem and not n.visible:
			return false
		n = n.get_parent()
	return true

func _press(text: String) -> bool:
	for b in dock.find_children("*", "Button", true, false):
		if b.text == text and _shown(b) and not b.disabled:
			b.pressed.emit()
			return true
	return false

func _texts() -> String:
	var out := []
	for c in dock.find_children("*", "", true, false):
		if (c is Label or c is Button) and _shown(c) and c.text.strip_edges() != "":
			out.append(c.text.replace("\n", " "))
	return "\n".join(out)

func _say(s: String) -> void:
	var f := FileAccess.open(out_file, FileAccess.READ_WRITE if FileAccess.file_exists(out_file) else FileAccess.WRITE)
	f.seek_end()
	f.store_line(s)
	f.close()
'''


class E2E:
    def __init__(self, godot, work):
        self.godot, self.work = godot, os.path.abspath(work)
        self.home = os.path.join(self.work, "home")
        self.projects = os.path.join(self.work, "projects")
        self.failures = []
        if WIN:
            self.local = os.path.join(self.home, "AppData", "Local")
            self.temp = os.path.join(self.local, "Temp")
        else:
            self.temp = os.path.join(self.home, "tmp")
        self.bin = os.path.join(self.home, ".local", "bin")
        self.settings = (os.path.join(self.local, "Autopilot") if WIN else
                         os.path.join(self.home, "Library", "Application Support", "Autopilot") if MAC else
                         os.path.join(self.home, ".config", "Autopilot"))
        self.godot_user = os.path.join(
            os.path.join(self.home, "AppData", "Roaming", "Godot") if WIN else
            os.path.join(self.home, "Library", "Application Support", "Godot") if MAC else
            os.path.join(self.home, ".local", "share", "godot"), "app_userdata", "Orchard Walk")
        self.claude_cache = (os.path.join(self.local, "claude-cli-nodejs", "Cache") if WIN else
                             os.path.join(self.home, "Library", "Caches", "claude-cli-nodejs") if MAC else
                             os.path.join(self.home, ".cache", "claude-cli-nodejs"))
        self.game = os.path.join(self.projects, "godot", "game")
        self.driver = os.path.join(self.projects, "godot", "driver")
        self.procs = []

    # -- the sandbox ------------------------------------------------------------------------------------------------

    def env(self, **extra):
        e = dict(os.environ)
        for k in list(e):
            if k.startswith(("AUTOPILOT_", "XDG_", "UV_TOOL")) or k.endswith("_API_KEY"):
                del e[k]
        real = os.path.expanduser("~")
        # (Rust's toolchain stays the runner's: uv builds a package from source where no wheel fits, e.g. Intel Macs)
        e.setdefault("RUSTUP_HOME", os.path.join(real, ".rustup"))
        e.setdefault("CARGO_HOME", os.path.join(real, ".cargo"))
        e.update(HOME=self.home, TMPDIR=self.temp, TEMP=self.temp, TMP=self.temp, PYTHONUNBUFFERED="1")
        if WIN:
            e.update(USERPROFILE=self.home, LOCALAPPDATA=self.local,
                     APPDATA=os.path.join(self.home, "AppData", "Roaming"))
        # (no other autopilot on PATH: a repository checkout's .venv, say)
        e["PATH"] = os.pathsep.join([self.bin] + [d for d in e["PATH"].split(os.pathsep) if ".venv" not in d])
        e.update(extra)
        return e

    def check(self, what, ok, detail=""):
        print(f"{'PASS' if ok else 'FAIL'}  {what}" + (f"\n      {detail}" if detail and not ok else ""), flush=True)
        if not ok:
            self.failures.append(what)

    def write(self, path, text="x"):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    def install(self):
        wheel_dir = os.path.join(self.work, "dist")
        if not os.path.isdir(wheel_dir):
            subprocess.run(["uv", "build", "--wheel", "-o", wheel_dir, HERE], check=True, capture_output=True)
        wheel = [f for f in os.listdir(wheel_dir) if f.endswith(".whl")][0]
        uri = "file:///" + os.path.join(wheel_dir, wheel).replace("\\", "/").lstrip("/")
        r = subprocess.run(["uv", "tool", "install", "--python", "3.12", f"autopilot[claude] @ {uri}"],
                           env=self.env(), capture_output=True, text=True)
        exe = os.path.join(self.bin, "autopilot.exe" if WIN else "autopilot")
        self.check("the autopilot command installed in the sandbox", r.returncode == 0 and os.path.isfile(exe),
                   r.stderr[-1500:])
        self.exe = exe

    def preferences(self, share=True):
        self.write(os.path.join(self.settings, "preferences.json"),
                   json.dumps({"version": 1, "onboarded": True, "share_data": share, "access": "own_keys"}))
        # (no upload leaves the runner: the shared data's endpoint is a closed port)
        self.write(os.path.join(self.settings, "autopilot.env"),
                   "AUTOPILOT_AGENT=claude\nAUTOPILOT_TRAINING_URL=http://127.0.0.1:9/api/autopilot/training\n")

    def godot_project(self):
        shutil.rmtree(os.path.join(self.projects, "godot"), ignore_errors=True)
        shutil.copytree(os.path.join(HERE, "plugin", "godot", "tests", "game"), self.game,
                        ignore=shutil.ignore_patterns(".godot", "addons"))
        shutil.copytree(os.path.join(HERE, "plugin", "godot", "addons", "autopilot"),
                        os.path.join(self.game, "addons", "autopilot"))
        self.write(os.path.join(self.game, "addons", "livedriver", "driver.gd"), DRIVER)
        self.write(os.path.join(self.game, "addons", "livedriver", "plugin.cfg"),
                   '[plugin]\n\nname="driver"\ndescription=""\nauthor=""\nversion="1"\nscript="driver.gd"\n')
        pg = os.path.join(self.game, "project.godot")
        text = open(pg, encoding="utf-8").read().replace(
            'enabled=PackedStringArray("res://addons/autopilot/plugin.cfg")',
            'enabled=PackedStringArray("res://addons/autopilot/plugin.cfg", "res://addons/livedriver/plugin.cfg")')
        open(pg, "w", encoding="utf-8").write(text)
        subprocess.run([self.godot, "--headless", "--path", self.game, "--import"], env=self.env(), capture_output=True,
                       timeout=600)

    def lay_out(self):
        """What runs leave: memory/ for three projects (each with project.json, notes, codex, a shared run not sent),
        each plugin's files in its project, Claude Code's logs, a killed run's temporary folder."""
        mem = os.path.join(self.settings, "memory")
        unreal = os.path.join(self.projects, "unreal", "ApGame")
        unity = os.path.join(self.projects, "unity", "OrbCourse")
        self.write(os.path.join(unreal, "ApGame.uproject"), "{}")
        self.write(os.path.join(unity, "ProjectSettings", "ProjectVersion.txt"), "m_EditorVersion: 6000.6.5f1\n")
        for name, path, engine in (("Orchard Walk", self.game, "godot"),
                                   ("ApGame", os.path.join(unreal, "ApGame.uproject"), "unreal"),
                                   ("OrbCourse", unity, "unity")):
            p = os.path.join(mem, name)
            self.write(os.path.join(p, "project.json"), json.dumps({"project": path, "engine": engine}))
            self.write(os.path.join(p, "notes.md"), "what it learned")
            self.write(os.path.join(p, "codex", "README.md"), "codex")
            run = os.path.join(p, "runs", "20261019-120000")
            self.write(os.path.join(run, "harness.py"), "harness")
            self.write(os.path.join(run, "telemetry", "run.json"),
                       json.dumps({"run_id": "20261019-120000", "uid": "u1"}))
            self.write(os.path.join(run, "telemetry", "media", "recording.mp4"), "video")
            named = re.sub(r"[^A-Za-z0-9]", "-", os.path.abspath(p))
            self.write(os.path.join(self.claude_cache, named, "mcp-logs-game", "1.jsonl"), "{}")
        self.write(os.path.join(self.claude_cache, "someone-elses-project", "x.jsonl"), "{}")
        for rel in ("Saved/Autopilot/Runs/1.json", "Saved/Playtest/20261019-120000/recording.mp4",
                    "Saved/AutopilotCodex/maps.txt", "Saved/Logs/AutopilotCodexExport.log", "Saved/Config/Editor.ini"):
            self.write(os.path.join(unreal, *rel.split("/")))
        for rel in ("Logs/Playtest/20261019-120000/result.json", "Library/AutopilotCodex/scripts.txt",
                    "Logs/AutopilotCodexExport.log", "UserSettings/AutopilotUsage.json",
                    "UserSettings/AutopilotPlaytest.json", "Assets/Game.cs"):
            self.write(os.path.join(unity, *rel.split("/")))
        self.write(os.path.join(self.game, ".godot", "autopilot_codex", "summary.txt"))
        self.write(os.path.join(self.game, ".godot", "editor", "autopilot_usage.json"), '{"runs": []}')
        self.write(os.path.join(self.godot_user, "playtest", "20261019-120000", "result.json"))
        self.write(os.path.join(self.godot_user, "logs", "godot.log"))
        self.write(os.path.join(self.temp, "autopilot-opencode-abc", "x"))
        self.write(os.path.join(self.temp, "autopilot_codex.lock"))
        project_data = [os.path.join(unreal, "Saved", "Autopilot"), os.path.join(unreal, "Saved", "Playtest"),
                     os.path.join(unreal, "Saved", "AutopilotCodex"),
                     os.path.join(unreal, "Saved", "Logs", "AutopilotCodexExport.log"),
                     os.path.join(unity, "Logs", "Playtest"), os.path.join(unity, "Library", "AutopilotCodex"),
                     os.path.join(unity, "Logs", "AutopilotCodexExport.log"),
                     os.path.join(unity, "UserSettings", "AutopilotUsage.json"),
                     os.path.join(self.game, ".godot", "autopilot_codex"),
                     os.path.join(self.game, ".godot", "editor", "autopilot_usage.json"),
                     os.path.join(self.godot_user, "playtest")]
        memory = [os.path.join(mem, n) for n in ("Orchard Walk", "ApGame", "OrbCourse")] + [
            os.path.join(self.claude_cache, n) for n in os.listdir(self.claude_cache) if n != "someone-elses-project"]
        return {
            "temp": [os.path.join(self.temp, "autopilot-opencode-abc"),
                     os.path.join(self.temp, "autopilot_codex.lock")],
            "project_data": project_data,
            "memory": memory,
            "kept": [os.path.join(unreal, "ApGame.uproject"), os.path.join(unreal, "Saved", "Config", "Editor.ini"),
                     os.path.join(unity, "Assets", "Game.cs"),
                     os.path.join(unity, "UserSettings", "AutopilotPlaytest.json"),
                     os.path.join(self.game, "project.godot"), os.path.join(self.godot_user, "logs", "godot.log"),
                     os.path.join(self.claude_cache, "someone-elses-project")],
        }

    # -- processes --------------------------------------------------------------------------------------------------

    def start(self, cmd, log, **env):
        f = open(os.path.join(self.work, log), "w", encoding="utf-8", errors="replace")
        p = subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT, env=self.env(**env))
        self.procs.append(p)
        return p

    def wait_file(self, path, seconds):
        end = time.time() + seconds
        while time.time() < end and not os.path.exists(path):
            time.sleep(0.5)
        return os.path.exists(path)

    def drive(self, commands, until, seconds=300):
        with open(os.path.join(self.driver, "cmd.txt"), "a", encoding="utf-8") as f:
            f.write("".join(c + "\n" for c in commands))
        out = os.path.join(self.driver, "out.txt")
        end = time.time() + seconds
        while time.time() < end:
            text = open(out, encoding="utf-8", errors="replace").read() if os.path.exists(out) else ""
            if until in text and text.rstrip().endswith("--- end"):
                return text
            time.sleep(1)
        return open(out, encoding="utf-8", errors="replace").read() if os.path.exists(out) else ""

    @staticmethod
    def dump(text, tag):
        m = re.search(rf"--- dump {re.escape(tag)} (.*?)\n(.*?)\n--- end", text, re.S)
        return (m.group(1), m.group(2)) if m else ("", "")

    # -- a round -----------------------------------------------------------------------------------------------------

    def round(self, keep):
        tag = "keep" if keep else "nokeep"
        print(f"\n== Round: Remove from this computer, Delete Autopilot's memories {'unticked' if keep else 'ticked'}",
              flush=True)
        if os.path.isdir(self.driver):
            shutil.rmtree(self.driver)
        os.makedirs(self.driver)
        open(os.path.join(self.driver, "cmd.txt"), "w").close()
        self.install()
        self.preferences()
        paths = self.lay_out()
        serve = self.start([self.exe, "serve", "--port", str(PORT)], f"serve-{tag}.log")
        brain = os.path.join(self.settings, "brain.json")
        self.check("the agent service announced itself (brain.json)", self.wait_file(brain, 120))
        decoy = self.start([sys.executable, "-c", "import time; time.sleep(900)", "autopilot", "serve"],
                           f"decoy-{tag}.log")
        editor = self.start([self.godot, "--headless", "--editor", "--path", self.game], f"editor-{tag}.log",
                            AUTOPILOT_PLAYTEST_AUTOCONNECT="1")
        out = self.drive(["wait_connected", "gear", "press Uninstall",
                          f"delete {'off' if keep else 'on'}", "press Uninstall", "wait_removal", "dump removed",
                          "sleep 20", "dump later"], "dump later", 600)
        state, texts = self.dump(out, "removed")
        self.check("the dock connected and removed (exit 0)", "removal=done" in state, out[-3000:])
        self.check('the dock says "Uninstallation successful", Set up again and View logs',
                   "Uninstallation successful" in texts and "Set up again" in texts and "View logs" in texts, texts)
        self.check("under the title, what is left to do: the addon in each project",
                   "delete addons/autopilot/ from each project that has it" in texts
                   and "the log says which" not in texts, texts)
        time.sleep(5)
        self.check("the agent service stopped", serve.poll() is not None)
        self.check("a process that only names autopilot isn't stopped", decoy.poll() is None)
        listed = subprocess.run(["uv", "tool", "list"], env=self.env(), capture_output=True, text=True).stdout
        self.check("the autopilot command uninstalled", "autopilot" not in listed and not os.path.exists(self.exe),
                   listed)
        logs = [f for f in os.listdir(self.temp) if f.startswith("autopilot-uninstall")]
        self.check("the uninstall log stays in the temporary folder", bool(logs))
        log = "".join(open(os.path.join(self.temp, f), encoding="utf-8", errors="replace").read() for f in logs)
        print("      uninstall log:\n" + "\n".join("        " + line for line in log.splitlines()))
        state, texts = self.dump(out, "later")
        self.check("nothing starts or installs while removed", "removal=done" in state and not os.path.exists(self.exe)
                   and not os.path.exists(brain))
        if keep:
            gone = paths["temp"]
            kept = paths["kept"] + paths["memory"] + paths["project_data"]
            self.check("Keep: the settings folder holds only memory/", sorted(os.listdir(self.settings)) == ["memory"],
                       str(os.listdir(self.settings)))
            self.check("Keep: the log says memory/ is kept", "but memory/ (kept)" in log, log)
        else:
            gone, kept = paths["temp"] + paths["memory"] + paths["project_data"] + [self.settings], paths["kept"]
            self.check("the unsent shared runs are said to go (they can't be sent)", "not sent yet" in log, log)
            self.check("the dock forgot its runs", "runs=0" in state and "usage=0" in state, state)
        left = [p for p in gone if os.path.lexists(p)]
        self.check(f"removed: {len(gone)} places", not left, "left: " + ", ".join(left))
        missing = [p for p in kept if not os.path.lexists(p)]
        self.check(f"kept: {len(kept)} places", not missing, "gone: " + ", ".join(missing))
        if not keep:
            out = self.drive(["press Set up again", "press Sign in", "sleep 3", "dump signing",
                              "press Cancel", "sleep 1", "dump cancelled"], "dump cancelled", 120)
            _, signing = self.dump(out, "signing")
            _, cancelled = self.dump(out, "cancelled")
            self.check("signing in: no BYOK card",
                       signing != "" and "bring your own keys" not in signing.lower(), signing)
            self.check("Cancel: the BYOK card back (folded, Show opens it)",
                       "bring your own keys (byok)" in cancelled.lower() and "Show" in cancelled, cancelled)
        self.drive(["quit"], "never", 15)
        for p in (editor, decoy, serve):
            if p.poll() is None:
                p.kill()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--godot", required=True)
    ap.add_argument("--work", required=True)
    a = ap.parse_args()
    e = E2E(a.godot, a.work)
    os.makedirs(e.temp, exist_ok=True)
    e.godot_project()
    try:
        e.round(keep=False)
        e.round(keep=True)
    finally:
        for p in e.procs:
            if p.poll() is None:
                p.kill()
    print(f"\n{'FAILED: ' + ', '.join(e.failures) if e.failures else 'PASSED: every check'}")
    sys.exit(1 if e.failures else 0)


if __name__ == "__main__":
    main()
