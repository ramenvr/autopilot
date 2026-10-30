"""Protocol smoke test of the Godot addon against the test game (headless, the game connects out to this brain).

    .venv\\Scripts\\python.exe plugin\\godot\\tests\\smoke.py [--port 18951]

Exercises every op over the wire: hello fields and the Bearer token, context, find/transforms/pose/movement, rc,
key/input (press, hold, mouse look), move, screen/ui/ui_act, session restart, pause, watch, view, detectors (the
test game's planted bugs), codex_manifest/codex_read, and a 2D scene. Exit code 0 when every check passes.
"""

import argparse
import math
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(__file__))
from harness import Brain, Checks, project, start_godot, stop  # noqa: E402

from autopilot.bridge import BridgeError  # noqa: E402

TOKEN = "test-token-123"


def yaw_of(dx, dy):
    return math.degrees(math.atan2(dy, dx))


def wrap(a):
    return (a + 180.0) % 360.0 - 180.0


def until(fn, timeout=5.0, every=0.05):
    end = time.monotonic() + timeout
    last = None
    while time.monotonic() < end:
        last = fn()
        if last:
            return last
        time.sleep(every)
    return last


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=18951)
    ap.add_argument("--log", default=os.path.join(os.environ.get("TEMP", "."), "godot_smoke_game.log"))
    a = ap.parse_args()
    c = Checks()
    brain = Brain(a.port)
    game = start_godot(["--path", project(), "--", f"--playtest-url={brain.url}", f"--playtest-token={TOKEN}"], log=a.log)
    t0 = time.monotonic()
    try:
        link = brain.wait(60)
        c.check("connects", True, f"{time.monotonic() - t0:.1f}s after launch")
        run(c, brain, link)
    except Exception as e:
        import traceback

        traceback.print_exc()
        c.check("no exception", False, repr(e))
    finally:
        stop(game)
        brain.close()
    print(f"game log: {a.log}")
    sys.exit(0 if c.summary() else 1)


def run(c, brain, link):
    req = link.request
    h = link.hello
    c.check("hello engine/host/project", h.get("engine") == "godot" and h.get("host") == "game"
            and h.get("project") == "Orchard Walk" and h.get("protocol") == 1, str({k: h.get(k) for k in ("engine", "host", "project")}))
    c.check("hello engine_version", str(h.get("engine_version", "")).startswith("4.7"), h.get("engine_version"))
    c.check("hello project_id GUID", len(str(h.get("project_id", "")).replace("-", "")) == 32, h.get("project_id"))
    c.check("hello plugin_version", bool(h.get("plugin_version")), h.get("plugin_version"))
    auth = brain.headers[-1].get("Authorization") or brain.headers[-1].get("authorization")
    c.check("Bearer token reaches the server", auth == f"Bearer {TOKEN}", auth)
    c.check("no required op missing", link.missing_ops() == [], str(link.missing_ops()))
    ctx = until(lambda: (lambda x: x if x.get("world") == "/root/Menu" else None)(req("context")), 10)
    c.check("context in the menu", ctx and ctx["world"] == "/root/Menu" and ctx["character"] == "", str(ctx and ctx.get("world")))
    c.check("context project_dir", ctx and os.path.normcase(os.path.abspath(ctx["project_dir"])) == os.path.normcase(project()),
            ctx and ctx.get("project_dir"))
    controls = {x["name"]: x for x in ctx["controls"]}
    c.check("context controls: the game's actions (no built-in ui_*)", sorted(controls) == ["interact", "jump", "move_back", "move_forward",
            "move_left", "move_right", "pause_menu", "sprint"], str(sorted(controls)))
    c.check("context controls (Unreal key names)", controls.get("move_forward", {}).get("keys") == ["W", "Up"]
            and controls.get("jump", {}).get("keys") == ["SpaceBar"], str(controls.get("move_forward")))

    # --- menu: screen, ui, ui_act ----------------------------------------------------------------
    texts = req("screen")["texts"]
    c.check("screen texts", texts.get("Menu.Title") == "Orchard Walk" and texts.get("Menu.PlayButton") == "Play", str(texts))
    els = {e["id"]: e for e in req("ui")["elements"]}
    c.check("ui lists kinds", els.get("Menu.PlayButton", {}).get("kind") == "button" and els.get("Menu.SoundToggle", {}).get("kind") == "checkbox"
            and els.get("Menu.Brightness", {}).get("kind") == "slider" and els.get("Menu.Pace", {}).get("kind") == "combo"
            and els.get("Menu.Nickname", {}).get("kind") == "text_input", str(sorted(els)))
    c.check("ui state", els["Menu.SoundToggle"].get("checked") is True and abs(els["Menu.Brightness"]["value"] - 0.5) < 1e-6
            and els["Menu.Pace"].get("selected") == "Steady", str(els["Menu.Pace"]))
    r = req("ui_act", {"id": "Menu.SoundToggle", "action": "click"})
    gs = lambda prop: req("rc", {"verb": "property", "body": {"objectPath": "/root/GameState", "propertyName": prop}})[prop]
    c.check("ui_act click checkbox (mouse)", r.get("via") == "mouse" and gs("sound") is False, str(r))
    req("ui_act", {"id": "Menu.Brightness", "action": "set", "value": 0.9})
    c.check("ui_act set slider", abs(gs("brightness") - 0.9) < 1e-6, str(gs("brightness")))
    req("ui_act", {"id": "Menu.Pace", "action": "click", "option": "Brisk"})
    c.check("ui_act combo option", gs("pace") == "Brisk", gs("pace"))
    req("ui_act", {"id": "Menu.Nickname", "action": "set", "text": "Robin"})
    c.check("ui_act text input", gs("nickname") == "Robin", gs("nickname"))
    try:
        req("ui_act", {"id": "Menu.Nope"})
        c.check("ui_act unknown id errors", False)
    except BridgeError as e:
        c.check("ui_act unknown id errors", "no element" in e.error, e.error)
    r = req("ui_act", {"id": "Menu.PlayButton", "action": "click"})
    ctx = until(lambda: (lambda x: x if x.get("world") == "/root/Level" else None)(req("context")), 10)
    c.check("ui_act click Play opens the level", r.get("via") == "mouse" and ctx and ctx["world"] == "/root/Level", str(r))
    c.check("session event on scene change", until(lambda: any(e.get("kind") == "session" for e in link.events), 2))
    c.check("context character/camera", ctx["character"] == "/root/Level/Player" and ctx["camera"] == "/root/Level/Player/Head/Camera3D"
            and ctx.get("dimension") == "3d", f"{ctx['character']} {ctx['camera']}")
    time.sleep(0.3)

    # --- find, transforms, pose, movement --------------------------------------------------------
    f = req("find", {"class": "Fruit"})["objects"]
    c.check("find by script class_name", len(f) == 6 and "/root/Level/Apple1" in f, str(f))
    c.check("find by Godot class (subclasses)", set(f) | {"/root/Level/Pond"} <= set(req("find", {"class": "Area3D"})["objects"]))
    byname = req("find", {"name": "apple"})["objects"]
    c.check("find by name (substring, any case)", set(f) <= set(byname) and "/root/Level/HUD/AppleLabel" in byname, str(byname))
    c.check("find by group", len(req("find", {"class": "group:fruit"})["objects"]) == 6
            and req("find", {"class": "group:player"})["objects"] == ["/root/Level/Player"])
    c.check("find limit", len(req("find", {"class": "Node", "limit": 3})["objects"]) == 3)
    tr = req("transforms", {"objects": ["/root/Level/Apple1", "/root/Level/Apple4", "/root/Level/Gate", "/root/Nope"]})["objects"]
    a1, a4, gate = tr["/root/Level/Apple1"], tr["/root/Level/Apple4"], tr["/root/Level/Gate"]
    # Godot (0, 0.6, -4) -> kit (400, 0, 60); (3, 0.6, -6) -> (600, 300, 60): +Y is the player's right (handedness)
    c.check("transforms kit frame location", _near(a1["location"], (400, 0, 60)) and _near(a4["location"], (600, 300, 60)),
            f"{a1['location']} {a4['location']}")
    c.check("transforms gate extent (half size, kit axes)", _near(gate["extent"], (15, 150, 150), 0.6) and _near(gate["center"], (1600, 0, 150)),
            f"{gate['extent']} {gate['center']}")
    c.check("transforms missing -> null", tr["/root/Nope"] is None)
    pose = req("pose")
    c.check("pose at = body middle", _near(pose["at"], (0, 0, 90), 2) and _near(pose["feet"], (0, 0, 0), 2) and _near(pose["eye"], (0, 0, 160), 2),
            f"at {pose['at']} feet {pose['feet']} eye {pose['eye']}")
    c.check("pose yaw/pitch/on_ground", abs(pose["yaw"]) < 0.01 and abs(pose["pitch"]) < 0.01 and pose["on_ground"] is True, str(pose))
    mv = req("movement")
    c.check("movement", mv["walk_speed"] == 500 and mv["jump_z"] == 450 and abs(mv["gravity"] - 980) < 1 and mv["half_height"] == 90
            and mv["radius"] == 40 and abs(mv["max_slope"] - 45) < 0.01 and mv["step"] is None, str(mv))

    # --- rc ------------------------------------------------------------------------------------------
    rc = lambda verb, **body: req("rc", {"verb": verb, "body": body})
    v = rc("property", objectPath="/root/Level/Player", propertyName="velocity")["velocity"]
    c.check("rc read native vector", set(v) == {"x", "y", "z"}, str(v))
    c.check("rc read nested a:b", abs(rc("property", objectPath="/root/Level/Apple5", propertyName="position:z")["position:z"] + 10) < 1e-6)
    c.check("rc read script constant", rc("property", objectPath="/root/Level/Player", propertyName="SPEED")["SPEED"] == 5.0)
    allp = rc("property", objectPath="/root/GameState")
    c.check("rc read all properties", allp.get("apples") == 0 and allp.get("BASKET_SIZE") == 10, str({k: allp[k] for k in list(allp)[:6]}))
    rc("property", objectPath="/root/GameState", propertyName="stamina", propertyValue={"stamina": 77}, access="WRITE_ACCESS")
    c.check("rc write int", gs("stamina") == 77 and isinstance(gs("stamina"), int), str(gs("stamina")))
    rc("property", objectPath="/root/Level/Apple6", propertyName="position:y", propertyValue={"position:y": 0.7}, access="WRITE_ACCESS")
    c.check("rc write nested", abs(rc("property", objectPath="/root/Level/Apple6", propertyName="position")["position"]["y"] - 0.7) < 1e-6)
    rc("property", objectPath="/root/Level/Apple6", propertyName="position", propertyValue={"position": {"x": 2, "y": 0.6, "z": -12}}, access="WRITE_ACCESS")
    c.check("rc write vector", rc("property", objectPath="/root/Level/Apple6", propertyName="position")["position"] == {"x": 2.0, "y": 0.6000000238418579, "z": -12.0}
            or abs(rc("property", objectPath="/root/Level/Apple6", propertyName="position")["position"]["y"] - 0.6) < 1e-5)
    c.check("rc call script function", rc("call", objectPath="/root/GameState", functionName="basket_full", parameters={})["ReturnValue"] is False)
    c.check("rc call with named params", rc("call", objectPath="/root/GameState", functionName="tire", parameters={"amount": 7}) == {"ReturnValue": None}
            and gs("stamina") == 70)
    rc("property", objectPath="/root/GameState", propertyName="stamina", propertyValue={"stamina": 100}, access="WRITE_ACCESS")
    c.check("rc call built-in", rc("call", objectPath="/root/Level/Player", functionName="is_on_floor", parameters={})["ReturnValue"] is True)
    c.check("rc call singleton", rc("call", objectPath="ProjectSettings", functionName="get_setting", parameters={"name": "application/config/name"})["ReturnValue"] == "Orchard Walk")
    c.check("rc call autoload by name", rc("call", objectPath="GameState", functionName="basket_full", parameters={})["ReturnValue"] is False)
    c.check("rc call default args", isinstance(rc("call", objectPath="/root/Level/Player", functionName="get_node_or_null", parameters={"path": "Head"})["ReturnValue"], str))
    d = rc("describe", objectPath="/root/Level/Player")
    names = [p["Name"] for p in d["Properties"]]
    fn = [f["Name"] for f in d["Functions"]]
    c.check("rc describe (script members first)", d["Class"].startswith("res://scripts/player.gd") and names[:3] == ["gravity", "spawn", "head"]
            and "SPEED" in names and fn[0] in ("_ready", "_unhandled_input", "_physics_process", "interact", "respawn") and "interact" in fn and "move_and_slide" in fn,
            f"{d['Class']} {names[:6]} {fn[:6]}")
    c.check("rc describe resource", rc("describe", objectPath="res://scenes/fruit.tscn")["Class"] == "PackedScene")
    c.check("rc read resource path", rc("property", objectPath="/root/Level/Player/Body", propertyName="mesh")["mesh"].startswith("res://scenes/level.tscn::"))
    for path, needle in (("/root/Nope", "Object /root/Nope does not exist"),):
        try:
            rc("property", objectPath=path, propertyName="x")
            c.check("rc missing object error", False)
        except BridgeError as e:
            c.check("rc missing object error", e.error == needle, e.error)
    try:
        rc("call", objectPath="/root/GameState", functionName="nope", parameters={})
        c.check("rc missing function error", False)
    except BridgeError as e:
        c.check("rc missing function error", "does not exist" in e.error, e.error)

    # --- mouse look, keys, move ------------------------------------------------------------------
    p0 = req("pose")
    link.post("key", {"key": "MouseX", "mode": "press", "value": 100})
    p1 = until(lambda: (lambda p: p if abs(p["yaw"] - p0["yaw"]) > 1 else None)(req("pose")), 3)
    # the test game turns 0.003 rad per count: 100 counts right = +17.19 degrees of kit yaw
    c.check("mouse look turns right (yaw +)", p1 and abs(wrap(p1["yaw"] - p0["yaw"]) - 17.19) < 0.2, f"{p0['yaw']} -> {p1 and p1['yaw']}")
    link.post("key", {"key": "MouseY", "mode": "press", "value": 50})
    p2 = until(lambda: (lambda p: p if abs(p["pitch"] - p1["pitch"]) > 1 else None)(req("pose")), 3)
    c.check("mouse look up (pitch +)", p2 and abs(p2["pitch"] - 8.59) < 0.2, f"pitch {p2 and p2['pitch']}")
    req("key", {"key": "MouseY", "mode": "press", "value": -50})
    req("key", {"key": "MouseX", "mode": "press", "value": -100})
    time.sleep(0.1)
    p3 = req("pose")
    c.check("mouse look back", abs(p3["yaw"]) < 0.05 and abs(p3["pitch"]) < 0.05, f"{p3['yaw']} {p3['pitch']}")
    # turn 90 right, then move "forward in the kit frame along the view": location must change along the yaw
    req("key", {"key": "MouseX", "mode": "press", "value": round(math.radians(90) / 0.003, 1)})
    time.sleep(0.1)
    p = req("pose")
    yaw = p["yaw"]
    fwd = {"X": math.cos(math.radians(yaw)), "Y": math.sin(math.radians(yaw)), "Z": 0}
    a0 = p["at"]
    r = req("move", {"direction": fwd, "seconds": 0.5})
    a1 = req("pose")["at"]
    dyaw = yaw_of(a1["X"] - a0["X"], a1["Y"] - a0["Y"])
    c.check("move along the view (kit frame)", r["moved"] > 150 and abs(wrap(dyaw - yaw)) < 5 and abs(yaw - 90) < 1,
            f"yaw {yaw:.1f}, moved {r['moved']}, went toward {dyaw:.1f}")
    # a world direction other than the view: kit -X (backward from spawn) while facing +Y
    a0 = a1
    r = req("move", {"direction": {"X": -1, "Y": 0, "Z": 0}, "seconds": 0.4})
    a1 = req("pose")["at"]
    c.check("move in a world direction (camera-relative keys)", r["moved"] > 120 and abs(wrap(yaw_of(a1["X"] - a0["X"], a1["Y"] - a0["Y"]) - 180)) < 6,
            f"moved {r['moved']} toward {yaw_of(a1['X'] - a0['X'], a1['Y'] - a0['Y']):.1f}")
    req("key", {"key": "MouseX", "mode": "press", "value": -round(math.radians(90) / 0.003, 1)})
    # key hold W (leased) and release
    a0 = req("pose")["at"]
    req("key", {"key": "W", "mode": "hold", "lease": 0.6})
    time.sleep(0.25)
    req("key", {"key": "W", "mode": "hold", "lease": 0.6})
    time.sleep(0.25)
    req("key", {"key": "W", "mode": "release"})
    time.sleep(0.15)
    a1 = req("pose")["at"]
    c.check("key hold W moves forward (kit +X)", a1["X"] - a0["X"] > 150 and abs(a1["Y"] - a0["Y"]) < 20, f"{a0} -> {a1}")
    a0 = a1
    req("key", {"key": "D", "mode": "hold", "lease": 0.3})
    time.sleep(1.2)  # not renewed: the lease lets go
    a1 = req("pose")["at"]
    c.check("key lease expires", 50 < a1["Y"] - a0["Y"] < 400, f"went {a1['Y'] - a0['Y']:.0f} right")
    req("key", {"key": "SpaceBar", "mode": "press"})
    jumped = until(lambda: req("pose")["on_ground"] is False, 1.0, 0.02)
    c.check("key press SpaceBar jumps", jumped)
    until(lambda: req("pose")["on_ground"], 3)
    req("input", {"action": "sprint", "mode": "hold"})
    c.check("input hold action", rc("call", objectPath="Input", functionName="is_action_pressed", parameters={"action": "sprint"})["ReturnValue"] is True)
    req("input", {"action": "sprint", "mode": "release"})
    c.check("input release action", rc("call", objectPath="Input", functionName="is_action_pressed", parameters={"action": "sprint"})["ReturnValue"] is False)
    try:
        req("key", {"key": "NoSuchKey", "mode": "press"})
        c.check("unknown key errors", False)
    except BridgeError as e:
        c.check("unknown key errors", "Unknown key" in e.error, e.error)

    # --- view (headless: raycasts, unproject, frustum) ---------------------------------------------
    req("session", {"action": "restart", "map": "level"})
    time.sleep(0.2)
    view = req("view", {"sonar": True, "sonar_reach": 3000})
    vis = {x["name"]: x for x in view["visible"]}
    a1v = vis.get("Apple1", {})
    c.check("view sees things ahead (frustum + line of sight)", {"Apple1", "Apple2", "Gate"} <= set(vis) and "Floor" not in vis, str(sorted(vis)))
    c.check("view screen position (unproject)", abs(a1v.get("screen_x", 9)) < 0.05 and -0.6 < a1v.get("screen_y", 9) < 0, str({k: a1v.get(k) for k in ("screen_x", "screen_y", "distance")}))
    c.check("view leaves out what is behind", "Pond" not in vis or vis["Pond"].get("screen_x", 0) > 0.3)
    c.check("view camera", _near(view["camera"]["location"], (0, 0, 160), 2) and view["camera"]["fov"] > 75 and view["body"]["on_ground"] is True,
            str(view["camera"]))
    sonar = view["body"].get("sonar", {}).get("ways", [])
    ahead = next((w for w in sonar if w["turn"] == 0), {})
    c.check("view sonar (gate ahead)", len(sonar) == 10 and ahead.get("by") == "wall" and ahead.get("thing") == "Gate" and 1400 < ahead.get("clear", 0) < 1600,
            str(ahead))
    behind = next((w for w in sonar if w["turn"] == 180), {})
    c.check("view sonar drop behind (floor edge)", behind.get("by") == "drop" and 1900 < behind.get("clear", 0) <= 2010, str(behind))
    only = req("view", {"only": ["Gate"]})
    c.check("view only", [x["name"] for x in only["visible"]] == ["Gate"] and only.get("only") == ["Gate"])
    req("key", {"key": "MouseY", "mode": "press", "value": -81})  # look down at the first apple (14 degrees)
    time.sleep(0.1)
    v2 = req("view", {"max_count": 3})
    c.check("view crosshair test", any(x.get("crosshair_on") for x in v2["visible"]), str([(x["name"], x.get("crosshair_on")) for x in v2["visible"]]))
    req("key", {"key": "MouseY", "mode": "press", "value": 81})

    # --- watch -----------------------------------------------------------------------------------------
    w = req("watch", {"objects": ["/root/Level/Apple2"], "drawn": True, "seconds": 0.5})
    c.check("watch drawn sees the apple's mesh turn", w.get("drawn") is True and any(ch["what"] == "turned" and ch["object"].endswith("Apple2/Mesh") for ch in w["changes"]),
            str(w))
    w = req("watch", {"objects": ["/root/Level/Gate"], "seconds": 0.3})
    c.check("watch still gate: no change", w["changes"] == [], str(w))

    # --- play: pick up the six apples, a HUD value past its maximum, pull the lever (planted bug: the gate sinks) --
    for path in [f"/root/Level/Apple{i}" for i in (1, 3, 2, 4, 5, 6)]:
        walk_to(req, path)
    count = gs("apples")
    c.check("move picks up all six apples", count == 6, f"apples {count}")
    texts = until(lambda: (lambda t: t if t.get("HUD.AppleLabel") == "Basket 6 / 10" else None)(req("screen")["texts"]), 2)
    c.check("HUD shows the basket", texts and texts.get("HUD.AppleLabel") == "Basket 6 / 10", str(texts))
    c.check("no hud_fraction finding while the HUD is right", not [f for f in link.findings if f.get("detector") == "hud_fraction"])
    rc("property", objectPath="/root/GameState", propertyName="apples", propertyValue={"apples": 12}, access="WRITE_ACCESS")
    hud = until(lambda: [f for f in link.findings if f.get("detector") == "hud_fraction"], 3)
    c.check("detector hud_fraction (12 / 10 written into the HUD's value)", hud and hud[0]["signature"] == "hud_fraction:HUD.AppleLabel"
            and hud[0]["source"] == "engine" and hud[0]["details"]["value"] == 12 and hud[0]["details"]["max"] == 10, str(hud and hud[0]))
    rc("property", objectPath="/root/GameState", propertyName="apples", propertyValue={"apples": 6}, access="WRITE_ACCESS")
    walk_to(req, "/root/Level/Lever", stop_short=150)
    result = {}
    th = threading.Thread(target=lambda: result.update(req("watch", {"objects": ["/root/Level/Gate"], "seconds": 0.6})))
    th.start()
    time.sleep(0.1)
    req("input", {"action": "interact", "mode": "press"})
    th.join()
    c.check("input interact pulls the lever; watch sees the gate move", gs("gate_open") is True and any(ch["what"] == "moved" for ch in result.get("changes", [])),
            str(result))
    texts = req("screen")["texts"]
    c.check("screen shows the newly visible label", texts.get("HUD.GateLabel") == "The orchard gate is open", str(texts))
    oow = until(lambda: [f for f in link.findings if f.get("detector") == "out_of_world"], 4)
    c.check("detector out_of_world finds the planted bug (the gate sinks)", oow and oow[0]["signature"] == "out_of_world:Gate"
            and oow[0]["source"] == "engine", str(oow and oow[0]))

    # --- the mailbox's script error: log event + log_error finding -------------------------------------------
    walk_to(req, "/root/Level/Mailbox", stop_short=150)
    req("key", {"key": "E", "mode": "press"})
    errs = until(lambda: [f for f in link.findings if f.get("detector") == "log_error" and "mailbox.gd" in f["signature"]], 3)
    c.check("detector log_error (script error)", bool(errs), str(errs and errs[0]))
    ours = [f for f in link.findings if "autopilot" in f.get("signature", "")]
    c.check("no errors from the addon itself", not ours, str(ours[:2]))
    c.check("log event", any(e.get("kind") == "log" and e.get("verbosity") == "Error" for e in link.events))
    c.check("heartbeats", sum(1 for e in link.events if e.get("kind") == "heartbeat") >= 3)

    # --- pause -------------------------------------------------------------------------------------------
    r = req("pause", {"paused": True})
    t1 = req("ping")["game_time"]
    time.sleep(0.4)
    t2 = req("ping")["game_time"]
    ctxp = req("context")
    c.check("pause: answered while paused, game time stops", r == {"paused": True} and t1 == t2 and ctxp["paused"] is True, f"{t1} {t2}")
    req("pause", {"paused": False})
    time.sleep(0.2)
    c.check("unpause: time goes on", req("ping")["game_time"] > t2)

    # --- detectors op, timestep -----------------------------------------------------------------------------
    c.check("detectors op", req("detectors", {"enabled": True, "hitch_seconds": 1.0}) == {})
    c.check("timestep op", "note" in req("timestep", {"fixed": True, "fps": 30}) and req("timestep", {"fixed": False}) == {})

    # --- session restart ------------------------------------------------------------------------------------
    changes = link.session_changes
    r = req("session", {"action": "restart"}, timeout=40)
    ctx = req("context")
    c.check("session restart reopens the launch scene", r.get("running") and ctx["world"] == "/root/Menu" and link.session_changes > changes, str(r))
    c.check("autoload state kept (as Unreal's GameInstance)", gs("apples") == 6)
    r = req("session", {"action": "restart", "map": "level", "fresh": True}, timeout=40)
    c.check("session restart map + fresh resets autoload variables", req("context")["world"] == "/root/Level" and gs("apples") == 0, str(r))
    c.check("session status", req("session", {"action": "status"}) == {"running": True})

    # --- codex ------------------------------------------------------------------------------------------------
    m = req("codex_manifest", {}, timeout=120)
    paths = [f["path"] for f in m["files"]]
    c.check("codex_manifest lists the project's code", paths[0] == "project.godot" and "scripts/player.gd" in paths and "scenes/level.tscn" in paths
            and ".godot/autopilot_codex/summary.txt" in paths, str(paths))
    c.check("codex_manifest skips the addon and .godot", not any(p.startswith("addons/autopilot") or (p.startswith(".godot") and not p.endswith("summary.txt")) for p in paths))
    c.check("codex_manifest hashes", all(len(f["hash"]) == 32 and f["size"] > 0 for f in m["files"]))
    rd = req("codex_read", {"paths": ["scripts/game_state.gd", "project.godot"]})
    c.check("codex_read", "func add_apple" in rd["files"].get("scripts/game_state.gd", "") and "[input]" in rd["files"].get("project.godot", ""))
    rd = req("codex_read", {"paths": ["scripts/player.gd", "scripts/gate.gd", "scenes/menu.tscn", "scripts\\hud.gd", ".godot/autopilot_codex/summary.txt"]})
    summary = rd["files"].get(".godot/autopilot_codex/summary.txt", "")
    c.check("codex_read off thread (5 files) + summary", len(rd["files"]) == 5 and "Main scene: res://scenes/menu.tscn" in summary and "/root/GameState" in summary
            and "Player (CharacterBody3D)" in summary and "ui_accept" not in summary and "move_forward: W" in summary, summary[:300])
    bad = ["../secret.txt", "C:/Windows/win.ini", "/etc/passwd", ".godot/editor/x.cfg", "addons/autopilot/playtest.gd", ".env",
           "scripts/../project.godot", "scripts/notes.txt", "nope.gd"]
    rd = req("codex_read", {"paths": bad})
    c.check("codex_read path safety", rd["files"] == {} and sorted(rd["missing"]) == sorted(bad), str(rd))
    rd = req("codex_read", {"paths": ["scenes/level.tscn"], "max_bytes": 100})
    c.check("codex_read too_big", rd["too_big"] == ["scenes/level.tscn"], str(rd))

    # --- 2D ------------------------------------------------------------------------------------------------------
    req("session", {"action": "restart", "map": "res://scenes/level2d.tscn"}, timeout=40)
    time.sleep(0.4)
    ctx = req("context")
    c.check("2D context", ctx["dimension"] == "2d" and ctx["character"] == "/root/Level2D/Player2D", str(ctx["dimension"]))
    tr = req("transforms", {"objects": ["/root/Level2D/Berry", "/root/Level2D/Player2D"]})["objects"]
    c.check("2D transforms (x right, y down -> X, -Z)", _near(tr["/root/Level2D/Berry"]["location"], (200, 0, -250)) and _near(tr["/root/Level2D/Berry"]["extent"], (12, 0, 12)),
            str(tr["/root/Level2D/Berry"]))
    p = req("pose")
    c.check("2D pose", p["on_ground"] is True and abs(p["at"]["Z"] + 248) < 3 and abs(p["feet"]["Z"] + 280) < 3, str(p))
    mv = req("movement")
    c.check("2D movement", mv["walk_speed"] == 300 and mv["jump_z"] == 400 and mv["gravity"] == 980 and mv["half_height"] == 32, str(mv))
    x0 = p["at"]["X"]
    r = req("move", {"direction": {"X": 1, "Y": 0, "Z": 0}, "seconds": 0.4})
    c.check("2D move right", r["moved"] > 60 and req("pose")["at"]["X"] > x0 + 60, str(r))
    v = req("view")
    c.check("2D view", "Berry" in [x["name"] for x in v["visible"]], str([x["name"] for x in v["visible"]]))
    c.check("2D find/rc", req("find", {"class": "CharacterBody2D"})["objects"] == ["/root/Level2D/Player2D"]
            and rc("property", objectPath="/root/Level2D/Player2D", propertyName="position")["position"].keys() == {"x", "y"})
    req("key", {"key": "SpaceBar", "mode": "press"})
    c.check("2D key jump", until(lambda: req("pose")["on_ground"] is False, 1.0, 0.02))


def walk_to(req, path, stop_short=0.0, max_steps=8):
    """Walk the player to a thing with `move` (kit directions), until it is reached or gone."""
    for _ in range(max_steps):
        tr = req("transforms", {"objects": [path]})["objects"].get(path)
        if tr is None:
            return True
        at = req("pose")["at"]
        dx, dy = tr["location"]["X"] - at["X"], tr["location"]["Y"] - at["Y"]
        dist = math.hypot(dx, dy)
        if dist <= stop_short + 30:
            return True
        go = dist - stop_short
        req("move", {"direction": {"X": dx / dist, "Y": dy / dist, "Z": 0}, "seconds": min(1.5, max(0.08, go / 500.0))})
        time.sleep(0.05)
    return False


def _near(v, xyz, tol=1.0):
    return all(abs(v[k] - x) <= tol for k, x in zip(("X", "Y", "Z"), xyz))


if __name__ == "__main__":
    main()
