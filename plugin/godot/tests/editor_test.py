"""The editor path: a headless Godot editor with the addon's dock connected to this brain (hello host "editor"), which
starts, restarts and stops play sessions through it and plays the game through the editor's debugger relay.

    .venv\\Scripts\\python.exe plugin\\godot\\tests\\editor_test.py [--port 18960]

The test project runs its games headless (editor/run/main_run_args="--headless" and the display driver setting); this
test also checks every game process the editor starts has --headless on its command line, and kills one that doesn't.
"""

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(__file__))
from harness import Brain, Checks, descendants, project, start_godot, stop  # noqa: E402

from autopilot.bridge import BridgeError  # noqa: E402


def until(fn, timeout=5.0, every=0.05):
    end = time.monotonic() + timeout
    last = None
    while time.monotonic() < end:
        last = fn()
        if last:
            return last
        time.sleep(every)
    return last


class Guard(threading.Thread):
    """Watches the editor's children: a game without --headless is killed at once (and noted)."""

    def __init__(self, pid):
        super().__init__(daemon=True)
        self.pid, self.seen, self.bad, self.running = pid, {}, [], True

    def run(self):
        import subprocess

        # the console executable runs the editor as its child; the editor's games are its children
        while self.running:
            for cpid, cmd in descendants(self.pid):
                if cpid in self.seen or "Godot" not in cmd:
                    continue
                self.seen[cpid] = cmd
                if "--headless" not in cmd:
                    subprocess.run(["taskkill", "/T", "/F", "/PID", str(cpid)], capture_output=True)
                    self.bad.append(cmd)
            time.sleep(0.1)

    def games(self):
        return {p: c for p, c in self.seen.items() if not re.search(r"--editor(\s|$)", c)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=18960)
    ap.add_argument("--log", default=os.path.join(os.environ.get("TEMP", "."), "godot_editor_test.log"))
    a = ap.parse_args()
    c = Checks()
    brain = Brain(a.port)
    # a user settings folder of its own (Autopilot/preferences.json: sharing and the research agent off), never the user's
    settings = tempfile.mkdtemp(prefix="godot_editor_settings_")
    os.makedirs(os.path.join(settings, "Autopilot"))
    with open(os.path.join(settings, "Autopilot", "preferences.json"), "w", encoding="utf-8") as f:
        json.dump({"version": 1, "onboarded": False, "share_data": False, "research_agent": False}, f)
    # Godot's editor settings of its own, with the editor as slow between frames as an unfocused one (100 ms a frame,
    # its interface/editor/unfocused_low_processor_mode_sleep_usec): the relay must not wait on it while a run goes
    os.makedirs(os.path.join(settings, "Godot"))
    with open(os.path.join(settings, "Godot", "editor_settings-4.7.tres"), "w", encoding="utf-8") as f:
        f.write('[gd_resource type="EditorSettings" format=3]\n\n[resource]\ninterface/editor/low_processor_mode_sleep_usec = 100000\n')
    editor = start_godot(["--editor", "--path", project()], log=a.log,
                         env={"AUTOPILOT_PLAYTEST_URL": brain.url, "AUTOPILOT_PLAYTEST_AUTOCONNECT": "1", "AUTOPILOT_PLAYTEST_TOKEN": "editor-token",
                              "LOCALAPPDATA": settings, "XDG_CONFIG_HOME": settings,
                              # (Godot's own editor settings too: never the user's, which the dock's settings show in)
                              "APPDATA": settings, "XDG_DATA_HOME": settings})
    guard = Guard(editor.pid)
    guard.start()
    try:
        link = brain.wait(120)
        run(c, brain, link, guard)
    except Exception as e:
        import traceback

        traceback.print_exc()
        c.check("no exception", False, repr(e))
    finally:
        guard.running = False
        stop(editor)
        brain.close()
        shutil.rmtree(settings, ignore_errors=True)
    c.check("every game the editor started was headless", not guard.bad, str(guard.bad))
    print(f"editor log: {a.log}; processes seen: {list(guard.seen.values())}")
    sys.exit(0 if c.summary() else 1)


def run(c, brain, link, guard):
    req = link.request
    h = link.hello
    c.check("editor hello", h.get("engine") == "godot" and h.get("host") == "editor" and len(h.get("project_id", "").replace("-", "")) == 32, str(h))
    auth = brain.headers[-1].get("Authorization") or brain.headers[-1].get("authorization")
    c.check("editor sends the Bearer token", auth == "Bearer editor-token", auth)
    c.check("editor hello: media, levels, share and research from preferences.json (connected though not onboarded: headless)",
            {"media", "levels"} <= set(h.get("features", [])) and h.get("share") is False and h.get("research") is False,
            str({k: h.get(k) for k in ("features", "share", "research")}))
    c.check("editor hello: timed_hold and pointer relayed to the game, and input", {"timed_hold", "pointer"} <= set(h.get("features", []))
            and h.get("input", {}).get("pointer") is True and isinstance(h.get("input", {}).get("notes"), list), str(h.get("input")))
    lv = req("levels")
    c.check("levels: the project's scenes, the main scene open, none playing",
            {"res://scenes/menu.tscn", "res://scenes/level.tscn", "res://scenes/level2d.tscn"} <= set(lv["levels"])
            and lv["levels"] == sorted(lv["levels"]) and not any("addons/" in p for p in lv["levels"])
            and lv["open"] == "res://scenes/menu.tscn" and lv["playing"] == "", str(lv))
    try:
        req("session", {"action": "start", "map": "nowhere"}, timeout=30)
        c.check("start in a scene the project hasn't: refused, nothing started", False)
    except BridgeError as e:
        st = req("session", {"action": "status"})
        c.check("start in a scene the project hasn't: refused (404), nothing started",
                "No scene nowhere" in e.error and e.status == 404 and st.get("running") is False and st.get("loading") is False, f"{e.error} {st}")
    for op, args in [("record", {"action": "start"}), ("capture", {})]:
        try:
            req(op, args)
            c.check(f"no game: {op} 409", False)
        except BridgeError as e:
            c.check(f"no game: {op} 409", e.status == 409 and e.error.startswith("no picture: "), e.error)
    c.check("no game: record stop succeeds", req("record", {"action": "stop"}) == {"recording": False, "frames": 0})
    link.send({"type": "usage", "final": False, "models": [{"kind": "llm", "model": "test-llm", "calls": 1, "input": 2, "output": 3}]})
    ctx = req("context")
    c.check("no game: context from the editor", ctx["session_running"] is False and ctx["host"] == "editor" and ctx["project_dir"]
            and any(x["name"] == "jump" and x["keys"] == ["SpaceBar"] for x in ctx["controls"]), str({k: ctx[k] for k in ("session_running", "host")}))
    c.check("no game: ping (game time 0, as Unreal's)", req("ping")["game_time"] == 0)
    c.check("no game: screen, ui, timestep answer empty", req("screen") == {"texts": {}} and req("ui") == {"elements": []}
            and req("timestep", {"fixed": False}) == {})
    try:
        req("view")
        c.check("no game: other ops error", False)
    except BridgeError as e:
        c.check("no game: other ops error", "No game is running" in e.error, e.error)
    c.check("no required op missing (unknown op)", link.missing_ops() == [], str(link.missing_ops()))
    m = req("codex_manifest", {}, timeout=120)
    c.check("codex_manifest from the editor", "scripts/player.gd" in [f["path"] for f in m["files"]] and ".godot/autopilot_codex/summary.txt" in [f["path"] for f in m["files"]])
    c.check("codex_read from the editor", "class_name Fruit" in req("codex_read", {"paths": ["scripts/fruit.gd"]})["files"].get("scripts/fruit.gd", ""))

    t0 = time.monotonic()
    r = req("session", {"action": "start"}, timeout=90)
    c.check("session start plays the main scene", r == {"running": True, "scene": "res://scenes/menu.tscn"}, f"{r} in {time.monotonic() - t0:.1f}s")
    c.check("session event says the scene", until(lambda: any(e.get("kind") == "session" and e.get("scene") == "res://scenes/menu.tscn"
            for e in link.events), 3))
    ctx = req("context")
    c.check("relayed context", ctx["session_running"] and ctx["world"] == "/root/Menu" and ctx["host"] == "editor", str(ctx["world"]))
    for op, args in [("record", {"action": "start", "fps": 5}), ("capture", {"max_dim": 1920})]:
        try:
            req(op, args)
            c.check(f"headless game: relayed {op} 409", False)
        except BridgeError as e:
            c.check(f"headless game: relayed {op} 409", e.status == 409 and "headless" in e.error, e.error)
    c.check("headless game: record stop", req("record", {"action": "stop"}) == {"recording": False, "frames": 0})
    games = until(lambda: guard.games(), 5)
    c.check("the game is the editor's child, headless", games and all("--headless" in cmd for cmd in games.values()), str(list((games or {}).values()))[:400])
    req("ui_act", {"id": "Menu.PlayButton"})
    ctx = until(lambda: (lambda x: x if x["world"] == "/root/Level" else None)(req("context")), 10)
    c.check("relayed ui_act Play", ctx and ctx["world"] == "/root/Level")
    time.sleep(0.3)
    p0 = req("pose")
    req("key", {"key": "MouseX", "mode": "press", "value": 100})
    p1 = until(lambda: (lambda p: p if abs(p["yaw"] - p0["yaw"]) > 1 else None)(req("pose")), 3)
    c.check("relayed mouse look", p1 and abs(p1["yaw"] - 17.19) < 0.3, str(p1 and p1["yaw"]))
    req("key", {"key": "MouseX", "mode": "press", "value": -100})
    r = req("move", {"direction": {"X": 1, "Y": 0, "Z": 0}, "seconds": 0.5})
    c.check("relayed move", r["moved"] > 150, str(r))
    v = req("view", {"sonar": True})
    c.check("relayed view", len(v["visible"]) > 0 and len(v["body"]["sonar"]["ways"]) == 10)
    # the editor relays every request: kept awake while a run goes (its low-processor sleep, 100 ms a frame unfocused,
    # would add that to every answer)
    rtts = []
    for _ in range(30):
        t0 = time.perf_counter()
        req("ping")
        rtts.append((time.perf_counter() - t0) * 1000.0)
    rtts.sort()
    print(f"relayed ping round trip: median {rtts[15]:.1f} ms, worst {rtts[-1]:.1f} ms")
    c.check("relayed answers come fast while a run goes (the editor kept awake)", rtts[15] < 60, f"median {rtts[15]:.1f} ms")
    d = req("rc", {"verb": "describe", "body": {"objectPath": "/root/Level/Player"}})
    c.check("large answer through the debugger (describe)", len(d["Functions"]) > 100, f"{len(d['Functions'])} functions")
    c.check("heartbeats relayed", until(lambda: sum(1 for e in link.events if e.get("kind") == "heartbeat" and e.get("t") is not None) >= 2, 4))
    req("pause", {"paused": True})
    t1 = req("ping")["game_time"]
    time.sleep(0.3)
    c.check("relayed pause", req("ping")["game_time"] == t1 and req("context")["paused"] is True)
    req("pause", {"paused": False})
    # a run is going (this agent server's welcome, role "cli"): the editor told the game, which takes Autopilot's input
    # alone; the run's result ends it there too, and a run started from elsewhere (run_started) starts it again
    notes = lambda: " ".join(req("context")["input"]["notes"])
    c.check("in a run the game takes Autopilot's input alone (told by the editor)", "A run is going" in notes(), notes()[:160])
    link.send({"type": "result", "result": {"kind": "verify", "verdict": "CONFIRMED", "reason": "test"}})
    c.check("the run's result: the game takes the person's input again", until(lambda: "A run is going" not in notes(), 5))
    link.send({"type": "run_started", "run": {"kind": "explore", "by": "test"}})
    c.check("a run started elsewhere: Autopilot's input alone again", until(lambda: "A run is going" in notes(), 5))
    r = req("move", {"direction": {"X": -1, "Y": 0, "Z": 0}, "seconds": 0.5})
    c.check("relayed move in the run", r["moved"] > 150, str(r))

    # the mailbox's script error stops the game in the debugger; the editor lets it go on while the brain plays
    req("rc", {"verb": "property", "body": {"objectPath": "/root/Level/Player", "propertyName": "position",
                                            "propertyValue": {"position": {"x": -5.5, "y": 0, "z": -2.5}}, "access": "WRITE_ACCESS"}})
    time.sleep(0.2)
    req("key", {"key": "E", "mode": "press"}, timeout=20)
    errs = until(lambda: [f for f in link.findings if f.get("detector") == "log_error" and "mailbox.gd" in f.get("signature", "")], 5)
    c.check("script error: finding relayed", bool(errs), str(errs and errs[0]))
    try:
        ok = req("ping", timeout=5)["game_time"] is not None
    except Exception as e:
        ok = False
        print(e)
    c.check("script error: the game goes on (debugger break resumed)", ok)

    # restart: a fresh game process (autoloads too), as Play In Editor's restart
    req("rc", {"verb": "property", "body": {"objectPath": "/root/GameState", "propertyName": "apples", "propertyValue": {"apples": 4}, "access": "WRITE_ACCESS"}})
    before = set(guard.games())
    changes = link.session_changes
    r = req("session", {"action": "restart"}, timeout=90)
    time.sleep(0.3)
    gs = req("rc", {"verb": "property", "body": {"objectPath": "/root/GameState", "propertyName": "apples"}})["apples"]
    new = until(lambda: set(guard.games()) - before, 5)
    c.check("restart: a new game process, fresh state", r.get("running") is True and new and gs == 0
            and link.session_changes >= changes + 2, f"{r} new={new} apples={gs}")
    c.check("restart replays the scene played (main)", req("context")["world"] == "/root/Menu")
    c.check("a restarted game is told at its attach that the run goes", "A run is going" in notes(), notes()[:160])
    r = req("session", {"action": "restart", "map": "level2d"}, timeout=90)
    c.check("restart with a map plays that scene (the answer says it)", r == {"running": True, "scene": "res://scenes/level2d.tscn"}
            and req("context")["world"] == "/root/Level2D", str(r))
    lv = req("levels")
    c.check("levels: the scene playing", lv["playing"] == "res://scenes/level2d.tscn" and lv["open"] == "res://scenes/menu.tscn", str(lv))
    r = req("session", {"action": "stop"}, timeout=60)
    r = req("session", {"action": "start", "map": "res://scenes/level2d.tscn"}, timeout=90)
    c.check("start with a map plays that scene, not the main one", r.get("running") is True and req("context")["world"] == "/root/Level2D")
    r = req("session", {"action": "restart"}, timeout=90)
    c.check("a restart after it replays that scene", r.get("running") is True and req("context")["world"] == "/root/Level2D")
    r = req("session", {"action": "start", "map": "level"}, timeout=90)
    c.check("start with another map while a game plays switches to it", r.get("running") is True and r.get("scene") == "res://scenes/level.tscn"
            and req("context")["world"] == "/root/Level", str(r))
    r = req("session", {"action": "stop"}, timeout=60)
    c.check("session stop", r == {"running": False} and req("context")["session_running"] is False, str(r))
    c.check("status after stop", req("session", {"action": "status"}) == {"running": False, "loading": False, "scene": ""})


if __name__ == "__main__":
    main()
