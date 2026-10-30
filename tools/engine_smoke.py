"""The Unreal plugin on one engine, live: the engine's own third-person template played headless (-nullrhi: nothing
is drawn) with an Autopilot build in its Plugins/, driven through the bridge the way a brain drives it. Checks the ops
whose code differs between engine versions: `rc` reads (sets and maps), `key` (the input path), an Enhanced Input
action, `move`, `view` and `screen`.

    python tools/engine_smoke.py --engine "C:/Program Files/Epic Games/UE_5.3" --plugin <an Autopilot build>
        [--port 8790]

The build must keep its runtime module (a BuildPlugin package; not the editor-only one Aura ships). Exits 1 when a
check fails.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autopilot import bridge  # noqa: E402

TEMPLATE = os.path.join("Templates", "TP_ThirdPersonBP")


def make_project(engine, plugin, root):
    project = os.path.join(root, "Smoke")
    template = os.path.join(engine, TEMPLATE)
    shutil.copytree(os.path.join(template, "Content"), os.path.join(project, "Content"))
    shutil.copytree(os.path.join(template, "Config"), os.path.join(project, "Config"))
    # From 5.6 a template takes shared content packs (its input actions among them), mounted at /Game/<MountName>
    # when a project is made from it
    defs = os.path.join(template, "Config", "TemplateDefs.ini")
    if os.path.exists(defs):
        for line in open(defs, encoding="utf-8-sig"):
            m = re.match(r'\s*SharedContentPacks=\(MountName="([^"]+)",DetailLevels=\("([^"]+)"', line)
            pack = m and os.path.join(engine, "Templates", "TemplateResources", m.group(2), m.group(1), "Content")
            if pack and os.path.isdir(pack):
                shutil.copytree(pack, os.path.join(project, "Content", m.group(1)), dirs_exist_ok=True)
    shutil.copytree(plugin, os.path.join(project, "Plugins", "Autopilot"),
                    ignore=shutil.ignore_patterns("HostProject", "Intermediate"))
    with open(os.path.join(project, "Smoke.uproject"), "w", encoding="utf-8") as f:
        json.dump({"FileVersion": 3, "Plugins": [{"Name": "Autopilot", "Enabled": True},
                                                  {"Name": "EnhancedInput", "Enabled": True}]}, f)
    actions = {}  # the template's IA_Jump and IA_Move, by object path
    for folder, _, files in os.walk(os.path.join(project, "Content")):
        for name in files:
            if name.lower() in ("ia_jump.uasset", "ia_move.uasset"):
                rel = os.path.relpath(os.path.join(folder, name[:-7]), os.path.join(project, "Content"))
                path = "/Game/" + rel.replace(os.sep, "/")
                actions[name[3:-7].lower()] = path + "." + os.path.basename(path)
    return os.path.join(project, "Smoke.uproject"), actions


class Checks:
    def __init__(self):
        self.failed = []

    def __call__(self, name, ok, detail=""):
        print(f"  {'ok  ' if ok else 'FAIL'} {name}{': ' + str(detail) if detail else ''}", flush=True)
        if not ok:
            self.failed.append(name)


def location(link, actor):
    r = link.request("rc", {"verb": "call", "body": {"objectPath": actor, "functionName": "K2_GetActorLocation"}})
    v = r["ReturnValue"]
    return v["X"], v["Y"], v["Z"]


def yaw(link, controller):
    r = link.request("rc", {"verb": "call", "body": {"objectPath": controller, "functionName": "GetControlRotation"}})
    return r["ReturnValue"]["Yaw"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", required=True)
    ap.add_argument("--plugin", required=True)
    ap.add_argument("--port", type=int, default=8790)
    ap.add_argument("--keep", action="store_true", help="keep the project folder")
    args = ap.parse_args()
    root = tempfile.mkdtemp(prefix="rg-smoke-")
    uproject, actions = make_project(args.engine, args.plugin, root)
    jump = actions.get("jump")
    log = os.path.join(root, "game.log")
    exe = os.path.join(args.engine, "Engine", "Binaries", *(
        ["Mac", "UnrealEditor.app", "Contents", "MacOS", "UnrealEditor"] if sys.platform == "darwin"
        else ["Win64", "UnrealEditor.exe"]))
    url = f"ws://127.0.0.1:{args.port}/v1/bridge"
    # (60 frames a second: with nothing drawn, a fast machine's frames come so quickly that game time runs ahead of
    # real time and the character doesn't walk)
    cmd = [exe, uproject, "-game", "-nullrhi", "-unattended", "-nosplash", "-nosound", "-log", "-ExecCmds=t.MaxFPS 60",
           "-Playtest=goal", "-PlaytestStayOpen", f"-PlaytestUrl={url}", f"-abslog={log}"]
    print(f"{os.path.basename(args.engine)}: {uproject}", flush=True)
    game = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    check = Checks()
    try:
        link = bridge.serve_local(port=args.port, wait=600)
        check("connected", True, f"engine {link.hello.get('engine_version') or link.hello.get('engine')}")
        ctx = link.request("context")
        character = ctx.get("character")
        check("context: a character", bool(character), character)
        deadline = time.time() + 30
        while not character and time.time() < deadline:
            time.sleep(1)
            character = link.request("context").get("character")
        if not character:
            raise RuntimeError("no character to drive")
        controller = link.request("context")["controller"]

        props = link.request("rc", {"verb": "property", "body": {"objectPath": character}})
        check("rc: read every property of the character", isinstance(props, dict) and len(props) > 20, len(props))
        # A set and a map written, then read back element by element (engine settings that have one on every version)
        for path, name, written in [
            ("/Script/Engine.Default__AssetManagerSettings", "MetaDataTagsForAssetRegistry", ["SmokeA", "SmokeB"]),
            ("/Script/Engine.Default__UserInterfaceSettings", "SoftwareCursors",
             {"Hand": "/Game/Smoke/Cursor.Cursor_C"}),
        ]:
            link.request("rc", {"verb": "property", "body": {"objectPath": path, "propertyName": name,
                                                             "propertyValue": {name: written},
                                                             "access": "WRITE_ACCESS"}})
            read = link.request("rc", {"verb": "property", "body": {"objectPath": path, "propertyName": name}})[name]
            if isinstance(written, list):
                same = sorted(read) == sorted(written)
            else:  # (a soft class path reads back as its struct: {"AssetPath": {"PackageName", "AssetName"}, ...})
                value = read.get("Hand") if isinstance(read, dict) else None
                package = value.get("AssetPath", {}).get("PackageName") if isinstance(value, dict) else value
                same = list(read) == ["Hand"] and str(package).startswith("/Game/Smoke/Cursor")
            check(f"rc: a {'set' if isinstance(written, list) else 'map'} written and read back", same, read)

        start = location(link, character)
        moved = link.request("move", {"direction": {"X": 1, "Y": 0, "Z": 0}, "scale": 1.0, "seconds": 0.6},
                             timeout=10)["moved"]
        check("move: the pawn walked", moved > 10, f"{moved:.0f} units")

        def peak_after(send):
            z0 = location(link, character)[2]
            send()
            peak = z0
            for _ in range(10):
                time.sleep(0.05)
                peak = max(peak, location(link, character)[2])
            time.sleep(1.5)  # (landed again)
            return peak - z0

        rise = peak_after(lambda: link.request("key", {"key": "SpaceBar", "mode": "press"}))
        check("key: SpaceBar made the character jump", rise > 20, f"rose {rise:.0f}")
        yaw0 = yaw(link, controller)
        link.request("key", {"key": "MouseX", "mode": "hold", "value": 4.0, "lease": 0.3})
        time.sleep(0.5)
        yaw1 = yaw(link, controller)
        turned = abs(((yaw1 - yaw0) + 180) % 360 - 180)
        check("key: MouseX turned the view", turned > 2, f"{turned:.0f} degrees")
        if jump:
            rise = peak_after(lambda: link.request("input", {"action": jump, "value": {"X": 1, "Y": 0, "Z": 0},
                                                             "mode": "press"}))
            check("input: the Enhanced Input jump action", rise > 20, f"{jump}: rose {rise:.0f}")
        else:
            print("  (the template has no IA_Jump: no input-action check)")
        if actions.get("move"):
            # A held action is injected every frame until released (before 5.3 the plugin does it itself)
            before = location(link, character)
            link.request("input", {"action": actions["move"], "value": {"X": 0, "Y": 1, "Z": 0}, "mode": "hold"})
            time.sleep(0.6)
            link.request("input", {"action": actions["move"], "mode": "release"})
            held = location(link, character)
            time.sleep(0.5)
            after = location(link, character)
            walked = ((held[0] - before[0]) ** 2 + (held[1] - before[1]) ** 2) ** 0.5
            coasted = ((after[0] - held[0]) ** 2 + (after[1] - held[1]) ** 2) ** 0.5
            check("input: the move action held, then released", walked > 50 and coasted < 150,  # (held: ~250)
                  f"walked {walked:.0f} while held, {coasted:.0f} after the release")
        view = link.request("view", {"max_count": 8})
        check("view: answers", isinstance(view, dict) and len(view) > 0, ", ".join(list(view)[:6]))
        screen = link.request("screen")
        check("screen: answers", isinstance(screen.get("texts"), dict))
        start2 = location(link, character)
        check("the character is where moving left it", abs(start2[0] - start[0]) > 10)
    except Exception as e:
        check("smoke", False, f"{type(e).__name__}: {e}")
    finally:
        game.kill()
        game.wait(timeout=30)
        if not args.keep:
            shutil.rmtree(root, ignore_errors=True)
    verdict = "all checks passed" if not check.failed else "FAILED: " + ", ".join(check.failed)
    print(f"{os.path.basename(args.engine)}: {verdict}")
    sys.exit(1 if check.failed else 0)


if __name__ == "__main__":
    main()
