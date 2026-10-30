"""The test game the live tests play: a throwaway Unity project made from TestCourse/ next to this file.

    python plugin/unity/com.ramenvr.autopilot/Tests~/testgame.py [--dir <folder>] [--no-player] [--input-handling 2]

makes it and prints where (delete the folder when done); the test scripts call make() themselves when they aren't
given a game. It writes <dir>/OrbCourse/ (the course's scripts and input actions, and Packages/manifest.json with this
package referenced from disk, and TextMeshPro's essential resources), then runs the Unity editor (AUTOPILOT_UNITY)
headless twice: TestCourseBuilder.Prepare (input handling, product name) and TestCourseBuilder.Build (the scenes, the project index and, unless
--no-player, a Development player at <dir>/Build/OrbCourse.exe). About 3 to 5 minutes the first time.
"""

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PACKAGE = os.path.dirname(HERE)
COURSE = os.path.join(HERE, "TestCourse")
UNITY = os.environ.get("AUTOPILOT_UNITY") or r"C:\Program Files\Unity\Hub\Editor\6000.6.5f1\Editor\Unity.exe"
EXE = "OrbCourse.exe" if os.name == "nt" else "OrbCourse"


class TestGame:
    def __init__(self, root, project, exe):
        self.root = root
        self.project = project
        self.exe = exe

    def remove(self):
        remove(self.root)


def remove(path):
    """Delete a folder (a just-closed Unity can hold files for a moment)."""
    for _ in range(10):
        if not os.path.exists(path):
            return
        shutil.rmtree(path, ignore_errors=True)
        if not os.path.exists(path):
            return
        time.sleep(1.0)


def _editor_data(unity):
    if sys.platform == "darwin":
        return os.path.join(os.path.dirname(os.path.dirname(unity)), "Resources", "PackageManager")  # (Unity.app/Contents)
    return os.path.join(os.path.dirname(unity), "Data", "Resources", "PackageManager")


def _versions(unity):
    """The Input System bundled with this editor and its built-in uGUI, so no download is needed."""
    pm = _editor_data(unity)
    inputsystem = "1.11.2"
    found = sorted(glob.glob(os.path.join(pm, "Editor", "com.unity.inputsystem-*.tgz")))
    if found:
        inputsystem = os.path.basename(found[-1])[len("com.unity.inputsystem-"):-len(".tgz")]
    ugui = "2.0.0"
    try:
        with open(os.path.join(pm, "BuiltInPackages", "com.unity.ugui", "package.json"), encoding="utf-8") as f:
            ugui = json.load(f)["version"]
    except (OSError, ValueError, KeyError):
        pass
    return inputsystem, ugui


def _tmp_essentials(unity, project):
    """Unpack TextMeshPro's essential resources (its settings, a font, shaders) into the project, as Window > TextMeshPro
    > Import TMP Essential Resources does. (AssetDatabase.ImportPackage doesn't finish in batch mode.)"""
    package = os.path.join(_editor_data(unity), "BuiltInPackages", "com.unity.ugui", "Package Resources",
                           "TMP Essential Resources.unitypackage")
    if not os.path.isfile(package):
        return False
    entries = {}
    with tarfile.open(package, "r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            guid, _, name = member.name.lstrip("./").partition("/")
            entries.setdefault(guid, {})[name] = tar.extractfile(member).read()
    for files in entries.values():
        path = files.get("pathname", b"").decode("utf-8").splitlines()[0].strip() if files.get("pathname") else ""
        parts = path.split("/")
        if not path.startswith("Assets/") or ".." in parts or "asset" not in files:
            continue
        target = os.path.join(project, *parts)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as f:
            f.write(files["asset"])
        if "asset.meta" in files:
            with open(target + ".meta", "wb") as f:
                f.write(files["asset.meta"])
    return True


def _run_unity(args, log, timeout):
    cmd = [UNITY, "-batchmode", "-nographics", *args, "-logFile", log]
    t = time.time()
    p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        code = p.wait(timeout)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)
        else:
            p.kill()
        p.wait(30)
        code = None
    text = ""
    try:
        with open(log, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        pass
    errors = [line for line in text.splitlines() if "error CS" in line or "[TestCourse] build failed" in line]
    if code != 0 or errors:
        raise RuntimeError(f"Unity {' '.join(args[-3:])} exited {code} after {time.time() - t:.0f}s (log {log}):\n"
                           + "\n".join((errors or text.splitlines()[-40:])[:40]))
    return time.time() - t


def make(root=None, player=True, log=print, handling=None):
    """Make the test game in root (default: a new temporary folder). Returns a TestGame. handling: the Active Input
    Handling (0 the legacy Input Manager only; 1 the Input System, the default; 2 both)."""
    if not os.path.isfile(UNITY):
        raise RuntimeError(f"No Unity editor at {UNITY}: set AUTOPILOT_UNITY to Unity 6's Unity.exe")
    root = os.path.abspath(root or tempfile.mkdtemp(prefix="rgtest-"))
    project = os.path.join(root, "OrbCourse")
    os.makedirs(project, exist_ok=True)
    shutil.copytree(os.path.join(COURSE, "Assets"), os.path.join(project, "Assets"), dirs_exist_ok=True)
    inputsystem, ugui = _versions(UNITY)
    os.makedirs(os.path.join(project, "Packages"), exist_ok=True)
    manifest = {"dependencies": {
        "com.ramenvr.autopilot": "file:" + PACKAGE.replace("\\", "/"),
        "com.unity.inputsystem": inputsystem,
        "com.unity.ugui": ugui,
        "com.unity.modules.audio": "1.0.0",
        "com.unity.modules.imgui": "1.0.0",
        "com.unity.modules.jsonserialize": "1.0.0",
        "com.unity.modules.physics": "1.0.0",
        "com.unity.modules.ui": "1.0.0",
        "com.unity.modules.uielements": "1.0.0",
    }}
    with open(os.path.join(project, "Packages", "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    # (a ProjectVersion.txt: Unity opens it as an existing project and keeps this small manifest, instead of a new one
    # it adds its template's packages to)
    os.makedirs(os.path.join(project, "ProjectSettings"), exist_ok=True)
    version = re.search(r"(\d{4}\.\d+\.\d+[abfp]\d+)", UNITY)
    stamp = os.path.join(project, "ProjectSettings", "ProjectVersion.txt")
    if version and not os.path.exists(stamp):
        with open(stamp, "w", encoding="utf-8") as f:
            f.write(f"m_EditorVersion: {version.group(1)}\n")
    _tmp_essentials(UNITY, project)
    log(f"test game: making {project} (Unity headless: prepare, then build)")
    secs = _run_unity(["-quit", "-projectPath", project, "-executeMethod", "TestCourseBuilder.Prepare",
                       *(["-inputHandling", str(handling)] if handling is not None else [])],
                      os.path.join(root, "prepare.log"), 900)
    log(f"test game: prepared in {secs:.0f}s")
    exe = os.path.join(root, "Build", EXE) if player else None
    secs = _run_unity(["-projectPath", project, "-executeMethod", "TestCourseBuilder.Build", *(["-buildPlayer", exe] if exe else [])],
                      os.path.join(root, "build.log"), 1200)
    log(f"test game: built in {secs:.0f}s" + (f" (player {exe})" if exe else ""))
    if exe and not os.path.isfile(exe):
        raise RuntimeError(f"the player build left no {exe}")
    return TestGame(root, project, exe)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", help="the folder to make it in (default: a new temporary folder)")
    ap.add_argument("--no-player", action="store_true", help="skip the player build")
    ap.add_argument("--input-handling", type=int, choices=(0, 1, 2), help="0 the legacy Input Manager, 1 the Input System (default), 2 both")
    args = ap.parse_args()
    game = make(args.dir, player=not args.no_player, handling=args.input_handling)
    print(f"project: {game.project}\nplayer:  {game.exe}\ndelete {game.root} when done")


if __name__ == "__main__":
    main()
