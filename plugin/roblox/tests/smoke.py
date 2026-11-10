"""Every request against the test game (tests/testgame.luau) in Roblox Studio: the editor's own answers, the play
session started and stopped, reads, writes and calls on both sides, the kit-frame ops, the player's input, the UI, the
detectors, the codex, the gamepad, touches on a simulated phone, and the game's pictures (when Studio's MCP server is
on). Run it with Studio open on the test game (see harness.py); Studio needn't be in front:

    python plugin/roblox/tests/smoke.py
"""

import sys
import time

from harness import BridgeError, brain, check, expect, summary, until

STUD = 28.0  # kit units (cm) in a stud

def main():
    link = brain()
    req = link.request
    h = link.hello
    check("hello: engine roblox, host editor, protocol 1", h.get("engine") == "roblox" and h.get("host") == "editor"
          and h.get("protocol") == 1, str(h))
    check("hello: features levels, timed_hold, touch, gamepad, device", {"levels", "timed_hold", "touch", "gamepad",
          "device"} <= set(h.get("features") or []), str(h.get("features")))
    media = "media" in (h.get("features") or [])
    print(f"  pictures: {h.get('media_via') or ('the plugin' if media else 'none (Studio MCP server off?)')}")

    # -- the editor, no play session ------------------------------------------------------------------------------
    req("session", {"action": "stop"}, timeout=60)
    expect("context without a game: session_running false", lambda: req("context"),
           lambda r: r["session_running"] is False and r["engine"] == "roblox")
    expect("ping without a game: game_time null", lambda: req("ping"), lambda r: r.get("game_time") is None)
    expect("levels: the place", lambda: req("levels"), lambda r: r["levels"] == [r["open"]] and r["playing"] == "")
    try:
        req("view", {})
        check("view without a game is 409", False)
    except BridgeError as e:
        check("view without a game is 409", e.status == 409, str(e))
    try:
        req("capture", {})
        check("capture is 409 no picture", False)
    except BridgeError as e:
        check("capture is 409 no picture", e.status == 409 and "no picture" in e.error, str(e))
    missing = link.missing_ops()
    check("no op the brain needs is missing", not missing, str(missing))

    # -- the codex --------------------------------------------------------------------------------------------------
    m = expect("codex_manifest lists the scripts and the export", lambda: req("codex_manifest", {"export": "auto"},
                                                                             timeout=120),
               lambda r: any(f["path"] == "src/ServerScriptService/GroveService.server.luau" for f in r["files"])
               and any(f["path"] == ".autopilot/summary.txt" for f in r["files"])
               and any(f["path"] == ".autopilot/tree/Workspace.txt" for f in r["files"]))
    if m:
        paths = [f["path"] for f in m["files"]]
        check("codex: a LocalScript is .client.luau, a module .luau",
              "src/StarterGui/HUD/HudScript.client.luau" in paths and "src/ReplicatedStorage/GameState.luau" in paths,
              str(paths[:20]))
        check("codex: no plugin of ours in it", not any("AutopilotPlaytest" in p for p in paths))
        r = expect("codex_read reads them", lambda: req("codex_read", {"paths": [
            "src/ReplicatedStorage/GameState.luau", ".autopilot/summary.txt", ".autopilot/tree/Workspace.txt",
            "src/Nope.luau"]}), lambda r: "addLantern" in r["files"]["src/ReplicatedStorage/GameState.luau"]
            and "src/Nope.luau" in r["missing"])
        if r:
            check("codex summary names the scripts and StarterPlayer", "GroveService" in
                  r["files"][".autopilot/summary.txt"] and "CharacterWalkSpeed" in r["files"][".autopilot/summary.txt"])
            check("codex tree lists the lanterns once with their count", "Lantern (Part)" in
                  r["files"][".autopilot/tree/Workspace.txt"] and "x6" in r["files"][".autopilot/tree/Workspace.txt"],
                  r["files"][".autopilot/tree/Workspace.txt"][:800])

    # -- the play session -------------------------------------------------------------------------------------------
    t0 = time.time()
    started = expect("session start plays the place", lambda: req("session", {"action": "start"}, timeout=90),
                     lambda r: r["running"] is True)
    print(f"  (play started in {time.time() - t0:.1f} s)")
    if not started:
        return summary()
    ctx = until(lambda: (c := req("context")) and c.get("character") and c, 30)
    check("context in play: the character and the camera", bool(ctx) and ctx["character"].startswith("/Workspace/")
          and ctx["camera"] == "/Workspace/Camera", str(ctx))
    check("context: session event came", any(e.get("kind") == "session" and e.get("running") for e in link.events))
    expect("levels while playing", lambda: req("levels"), lambda r: r["playing"] == r["open"])
    expect("ping in play: game time", lambda: req("ping"), lambda r: isinstance(r.get("game_time"), (int, float)))
    check("heartbeats come from the client", until(lambda: any(e.get("kind") == "heartbeat" and e.get("t") is not None
                                                                 for e in link.events), 5))

    # find, transforms, pose, movement
    expect("find things: the grove's things", lambda: req("find", {"things": True}),
                    lambda r: any(p.endswith("/Lever") for p in r["objects"]))
    lanterns = expect("find tag:Lantern: six", lambda: req("find", {"class": "tag:Lantern"})["objects"],
                      lambda r: len(r) == 6)
    check("same-named lanterns have distinct numbered paths", bool(lanterns) and len(set(lanterns)) == 6
          and all("@" in p for p in lanterns), str(lanterns))
    expect("find class Model by name", lambda: req("find", {"class": "Model", "name": "lev"})["objects"],
           lambda r: r == ["/Workspace/Grove/Lever"])
    expect("find a plain tag name works", lambda: req("find", {"class": "Lantern"})["objects"], lambda r: len(r) == 6)
    expect("transforms in the kit frame", lambda: req("transforms", {"objects": [
        "/Workspace/Grove/Crate", "/Workspace/Nope"]})["objects"],
        lambda r: r["/Workspace/Nope"] is None and abs(r["/Workspace/Grove/Crate"]["location"]["X"] - 6 * 28) < 1
        and abs(r["/Workspace/Grove/Crate"]["location"]["Y"] + 16 * 28) < 1
        and abs(r["/Workspace/Grove/Crate"]["extent"]["Z"] - 2 * 28) < 1)
    pose = expect("pose: the player", lambda: req("pose"), lambda r: r["path"] == ctx["character"]
                  and r["on_ground"] is True and r["feet"]["Z"] < r["at"]["Z"])
    if pose:
        check("pose: feet on the spawn (1 stud up: 28)", abs(pose["feet"]["Z"] - 28) < 20, str(pose["feet"]))
    mv = expect("movement: the Humanoid's numbers", lambda: req("movement"),
                lambda r: abs(r["walk_speed"] - 16 * 28) < 1 and r["gravity"] > 5000 and r["jump_z"] > 0
                and r["half_height"] > 0)
    print(f"  movement: {mv}")

    # rc: the server's view, the client's, modules, writes, calls
    expect("rc read a part's property (server)", lambda: req("rc", {"verb": "property", "body": {
        "objectPath": "/Workspace/Grove/Crate", "propertyName": "Size"}}), lambda r: r["Size"] == {"x": 6, "y": 4, "z": 6})
    expect("rc read an attribute", lambda: req("rc", {"verb": "property", "body": {
        "objectPath": "/Workspace/Grove/Gate", "propertyName": "Open"}}), lambda r: r == {"Open": False})
    expect("rc read the module's state as the game has it", lambda: req("rc", {"verb": "property", "body": {
        "objectPath": "/ReplicatedStorage/GameState", "propertyName": "total"}}), lambda r: r == {"total": 6})
    expect("rc read the player's leaderstats through its character", lambda: req("rc", {"verb": "property", "body": {
        "objectPath": ctx["character"], "propertyName": "Lanterns"}}), lambda r: r == {"Lanterns": 0})
    expect("rc read a server-only script", lambda: req("rc", {"verb": "property", "body": {
        "objectPath": "/ServerScriptService/GroveService", "propertyName": "Enabled"}}), lambda r: r == {"Enabled": True})
    expect("rc read the client's GUI", lambda: req("rc", {"verb": "property", "body": {
        "objectPath": f"{ctx['controller']}/PlayerGui/HUD/LanternCount", "propertyName": "Text"}}),
        lambda r: r == {"Text": "Lanterns 0 / 6"})
    expect("rc call a module function", lambda: req("rc", {"verb": "call", "body": {
        "objectPath": "/ReplicatedStorage/GameState", "functionName": "summary", "parameters": {"self": True}}}),
        lambda r: r["ReturnValue"] == "0/6 lanterns, gate shut")
    expect("rc write a module field", lambda: req("rc", {"verb": "property", "body": {
        "objectPath": "/ReplicatedStorage/GameState", "propertyName": "ringer", "propertyValue": {"ringer": "probe"},
        "access": "WRITE_ACCESS"}}), lambda r: r == {})
    expect("... read back", lambda: req("rc", {"verb": "property", "body": {
        "objectPath": "/ReplicatedStorage/GameState", "propertyName": "ringer"}}), lambda r: r == {"ringer": "probe"})
    expect("rc call an instance method by argument name", lambda: req("rc", {"verb": "call", "body": {
        "objectPath": "/Workspace/Grove/Crate", "functionName": "GetAttribute", "parameters": {"attribute": "Nope"}}}),
        lambda r: r == {"ReturnValue": None})
    expect("rc describe a module", lambda: req("rc", {"verb": "describe", "body": {
        "objectPath": "/ReplicatedStorage/GameState"}}), lambda r: any(f["Name"] == "addLantern" for f in r["Functions"])
        and any(p["Name"] == "collected" for p in r["Properties"]))
    expect("rc describe a part", lambda: req("rc", {"verb": "describe", "body": {"objectPath": "/Workspace/Grove/Crate"}}),
           lambda r: r["Class"] == "Part" and any(p["Name"] == "Anchored" for p in r["Properties"]))
    expect("rc all of an instance's values", lambda: req("rc", {"verb": "property", "body": {
        "objectPath": "/Workspace/Grove/Gate"}}), lambda r: r.get("Open") is False and "position" in r)
    try:
        req("rc", {"verb": "property", "body": {"objectPath": "/Workspace/Gone", "propertyName": "Size"}})
        check("rc on a missing instance: does not exist", False)
    except BridgeError as e:
        check("rc on a missing instance: does not exist", "does not exist" in e.error, e.error)

    # view and sonar
    v = expect("view: visible things, camera, body", lambda: req("view", {"max_count": 30}),
               lambda r: r["camera"]["fov"] > 0 and r["body"]["on_ground"] is True and len(r["visible"]) > 0)
    if v:
        names = [t["name"] for t in v["visible"]]
        print(f"  visible: {names}")
        check("view leaves out the player", not any(t["path"] == ctx["character"] for t in v["visible"]))
        check("view: lanterns are seen ahead", any(n.startswith("Lantern") for n in names), str(names))
    s = expect("view sonar: ways", lambda: req("view", {"max_count": 1, "sonar": True, "sonar_turns": [0, 90, 180, -90]}),
               lambda r: len(r["body"]["sonar"]["ways"]) == 4)
    if s:
        print(f"  sonar: {s['body']['sonar']['ways']}")

    # screen and UI
    expect("screen: the HUD's text", lambda: req("screen"), lambda r: r["texts"].get("HUD.LanternCount")
                == "Lanterns 0 / 6")
    expect("ui: the menu's button and box", lambda: req("ui")["elements"],
                lambda r: {"Menu.RingButton", "Menu.NameBox"} <= {e["id"] for e in r})
    expect("ui_act set: types into the box", lambda: req("ui_act", {"id": "Menu.NameBox", "action": "set",
                                                                    "text": "Tester"}), lambda r: True)
    expect("ui_act click: the button takes it", lambda: req("ui_act", {"id": "Menu.RingButton"}),
           lambda r: r["fired"] is True)
    expect("... the game got it (the ringer is the name typed)", lambda: until(lambda: req("rc", {
        "verb": "property", "body": {"objectPath": "/ReplicatedStorage/GameState", "propertyName": "ringer"}})["ringer"]
        == "Tester", 3), lambda r: r)
    expect("pointer: at the middle", lambda: req("pointer", {"screen_x": 0, "screen_y": 0}),
           lambda r: abs(r["screen_x"]) < 0.05 and abs(r["screen_y"]) < 0.05)

    # input
    before = req("pose")
    req("key", {"key": "W", "mode": "hold", "lease": 0.6})
    speeds = []
    for _ in range(5):
        time.sleep(0.3)
        req("key", {"key": "W", "mode": "hold", "lease": 0.6})
        v = req("pose")["velocity"]
        speeds.append(round((v["X"] ** 2 + v["Y"] ** 2) ** 0.5))
    req("key", {"key": "W", "mode": "release"})
    after = req("pose")
    moved = ((after["at"]["X"] - before["at"]["X"]) ** 2 + (after["at"]["Y"] - before["at"]["Y"]) ** 2) ** 0.5
    check("key W held walks the player", moved > 200, f"moved {moved:.0f} cm, speeds {speeds} cm/s, input "
          f"{req('context').get('input')}, a text box focused: {req('screen', {'ui': True}).get('ui')}")
    y0 = req("pose")["yaw"]
    req("key", {"key": "MouseX", "mode": "press", "value": 60})
    time.sleep(0.6)
    y1 = req("pose")["yaw"]
    check("MouseX turns the view right", 5 < ((y1 - y0 + 180) % 360 - 180) < 120, f"{y0} -> {y1}")
    expect("move: walks a kit direction", lambda: req("move", {"direction": {"X": 1, "Y": 0, "Z": 0}, "seconds": 0.5}),
           lambda r: r["moved"] > 100)
    def waves():
        return req("rc", {"verb": "property", "body": {"objectPath": ctx["controller"], "propertyName": "Waves"}})["Waves"]

    before_waves = waves()
    req("key", {"key": "Wave", "mode": "press"})
    expect("a ContextActionService action by name (Wave: Q)", lambda: until(lambda: waves() == before_waves + 1, 3),
           lambda r: r)
    try:
        req("key", {"key": "Escape", "mode": "press"})
        check("Escape (Roblox's menu) is refused", False)
    except BridgeError as e:
        check("Escape (Roblox's menu) is refused", e.status == 409 and "menus" in e.error, e.error)

    # the gamepad: ButtonA jumps (Roblox's gamepad controls), the left stick walks, the right stick turns
    z0 = req("pose")["feet"]["Z"]
    req("key", {"key": "Gamepad_FaceButton_Bottom", "mode": "hold", "seconds": 0.2})
    top = z0
    for _ in range(10):
        time.sleep(0.08)
        top = max(top, req("pose")["feet"]["Z"])
    check("Gamepad_FaceButton_Bottom (ButtonA) jumps", top - z0 > 80, f"{z0} -> {top}")
    time.sleep(1.0)
    # (in the open, away from the grove's things: its speed while held)
    req("rc", {"verb": "property", "body": {"objectPath": "Character", "propertyName": "position", "propertyValue": {
        "position": {"x": 40, "y": 4, "z": 40}}, "access": "WRITE_ACCESS"}})
    time.sleep(1.0)
    req("key", {"key": "Gamepad_LeftY", "mode": "hold", "value": 1, "seconds": 1.0})
    time.sleep(0.5)
    v = req("pose")["velocity"]
    speed = (v["X"] ** 2 + v["Y"] ** 2) ** 0.5
    time.sleep(0.7)
    check("Gamepad_LeftY held walks the player", speed > 200, f"{speed:.0f} cm/s")
    y0 = req("pose")["yaw"]
    req("key", {"key": "Gamepad_RightX", "mode": "hold", "value": 1, "seconds": 0.3})
    time.sleep(0.8)
    y1 = req("pose")["yaw"]
    check("Gamepad_RightX turns the view right", 20 < ((y1 - y0 + 180) % 360 - 180) < 150, f"{y0} -> {y1}")
    expect("context: input and device", lambda: req("context"), lambda r: r["input"]["needs_focus"] is False
           and r["device"]["touch"] is False and "pad" in r["input"])

    # view: what it tells beyond the things (nothing omitted), the ways up, a tipped thing's top, what is held, what
    # the body is against
    v = expect("view: nothing omitted, the sun, nothing held", lambda: req("view", {"max_count": 30}),
               lambda r: r["omitted"] == [] and isinstance(r["scene"]["sun"], (int, float)) and r["held"] == [])
    _put(req, (-24, 3, 0))  # (on the ground east of the Lookout's ramp, whose foot is at x -34)
    check("turned to face the lookout", _face(req, "/Workspace/Grove/Lookout"))
    v = expect("view ways_up: the ramp up to the lookout (foot at x -34, top 9 studs up)", lambda: req("view", {
        "max_count": 30, "ways_up": True, "openings": True}), lambda r: any(
        abs(w["top"]["Z"] - 9 * STUD) < 40 and abs(w["foot"]["Y"] + 34 * STUD) < 150 and abs(w["foot"]["X"]) < 150
        and abs(w["foot"]["Z"]) < 20 for w in r["body"]["ways_up"]))
    if v:
        print(f"  ways_up: {v['body']['ways_up']}")
        check("view openings: openings and free asked, told", isinstance(v["body"].get("openings"), list)
              and len(v["body"].get("free") or []) > 0, str({k: v["body"].get(k) for k in ("openings", "free")}))
        ramp = next((t for t in v["visible"] if t["name"] == "Ramp"), None)
        check("view: the ramp is tipped (tilt, its own box) about 27 degrees", ramp is not None
              and abs(abs(ramp.get("tilt", {}).get("roll", 0)) - 27) <= 2 and "box" in ramp, str(ramp))
        check("view: the ramp's top slopes 27 degrees, uphill to the west (kit yaw -90)", ramp is not None
              and abs(ramp.get("top_slope_degrees", 0) - 27) <= 3 and abs(ramp.get("uphill", {}).get("yaw", 0) + 90) <= 5,
              str(ramp))
    # (One to Nine are the backpack hotbar's, Roblox's own: refused, as Escape; a Tool is equipped through rc)
    try:
        req("key", {"key": "One", "mode": "press"})
        check("One (the backpack hotbar's) is refused", False)
    except BridgeError as e:
        check("One (the backpack hotbar's) is refused", e.status == 409, e.error)
    humanoid = ctx["character"] + "/Humanoid"
    req("rc", {"verb": "call", "body": {"objectPath": humanoid, "functionName": "EquipTool",
                                        "parameters": {"tool": ctx["controller"] + "/Backpack/Torch"}}})
    held = until(lambda: req("view", {"max_count": 30})["held"], 3)
    check("view held: the Torch equipped, in a hand", bool(held) and held[0]["name"] == "Torch"
          and "Hand" in held[0].get("attached_at", "") and held[0]["look"]["parts"] == ["Handle"], str(held))
    check("view: what is held isn't among the things seen", not any(t["name"] == "Torch" for t in req("view", {
        "max_count": 30})["visible"]))
    req("rc", {"verb": "call", "body": {"objectPath": humanoid, "functionName": "UnequipTools", "parameters": {}}})
    _put(req, (-58, 3, 9))  # (beside the Lookout's south wall, z 6)
    req("move", {"direction": {"X": 1, "Y": 0, "Z": 0}, "seconds": 0.8})  # (kit +X is Roblox -z: into the wall)
    _face(req, "/Workspace/Grove/Lookout")
    req("key", {"key": "W", "mode": "hold", "lease": 0.4})
    v = req("view", {"max_count": 30})
    req("key", {"key": "W", "mode": "release"})
    lookout = next((t for t in v["visible"] if t["name"] == "Lookout"), None)
    check("view: the body against the lookout (touching, blocks_player)", any(t["thing"] == "Lookout" for t in
          v["body"].get("touching", [])) and lookout is not None and lookout.get("blocks_player") is True,
          str((v["body"].get("touching"), lookout)))

    # pictures
    if media:
        shot = expect("capture: the game's picture", lambda: req("capture", {"max_dim": 640}, timeout=30),
                      lambda r: r["jpeg"] and 0 < r["width"] <= 640)
        frames = []
        link.frame_listeners.append(frames.append)
        expect("record start", lambda: req("record", {"action": "start", "fps": 4, "max_dim": 480}),
               lambda r: r["recording"] is True)
        time.sleep(3.0)
        req("record", {"action": "stop"})
        check("record: frames come while the game plays", len(frames) >= 3, f"{len(frames)} frames")
        if shot:
            import base64
            import os
            import tempfile
            out = os.path.join(tempfile.gettempdir(), "autopilot-roblox-capture.jpg")
            with open(out, "wb") as f:
                f.write(base64.b64decode(shot["jpeg"]))
            print(f"  (the picture: {out})")

    # watch
    expect("watch: the player walking moves", lambda: _watch_walk(req, ctx["character"]),
               lambda r: any(c["what"] == "moved" for c in r["changes"]))

    # pause
    expect("pause", lambda: req("pause", {"paused": True}), lambda r: r["paused"] is True)
    t1 = req("ping")["game_time"]
    time.sleep(1.0)
    t2 = req("ping")["game_time"]
    check("paused: game time stands still", abs(t2 - t1) < 0.05, f"{t1} -> {t2}")
    req("pause", {"paused": False})
    time.sleep(0.5)
    check("resumed: game time goes on", req("ping")["game_time"] > t2)

    # the lever (ProximityPrompt with E), the gate's planted bug, the mailbox's
    req("detectors", {"enabled": True})
    _go_to(req, "/Workspace/Grove/Lever")
    req("key", {"key": "E", "mode": "press"})
    expect("E at the lever opens the gate (ProximityPrompt)", lambda: until(lambda: req("rc", {"verb": "property",
           "body": {"objectPath": "/Workspace/Grove/Gate", "propertyName": "Open"}})["Open"], 3), lambda r: r is True)
    found = until(lambda: [e for e in link.events if e.get("kind") == "finding"
                           and e["finding"].get("detector") == "out_of_world"], 20)
    check("detector out_of_world: the gate's door fell", bool(found), str([e for e in link.events
                                                                          if e.get("kind") == "finding"]))
    _go_to(req, "/Workspace/Grove/Mailbox")
    on = _face(req, "/Workspace/Grove/Mailbox")
    check("turned to face the mailbox", on, "")
    req("pointer", {"object": "/Workspace/Grove/Mailbox"})
    time.sleep(0.2)
    req("key", {"key": "LeftMouseButton", "mode": "press"})
    found = until(lambda: [e for e in link.events if e.get("kind") == "finding"
                           and e["finding"].get("detector") == "log_error"], 6)
    check("detector log_error: the mailbox's script error", bool(found), "")
    if found:
        print(f"  log_error: {found[0]['finding']['summary']}")

    # a phone: Studio's Device Simulator, real touches
    phone = expect("session restart on a phone", lambda: req("session", {"action": "restart", "device": "phone"},
                                                             timeout=120), lambda r: r["running"] is True)
    if phone:
        until(lambda: (c := req("context")) and c.get("character") and c, 30)
        time.sleep(1.5)
        expect("device: a phone, touch on", lambda: req("device", {"action": "get"}),
               lambda r: r["simulated"] and r["form"] == "Phone" and r["touch"] is True)
        expect("touch tap on the menu's button: a real touch, the button takes it", lambda: req("touch", {
            "action": "tap", "ui": "Menu.RingButton"}), lambda r: r["as"] == "touch")
        before = req("pose")
        req("touch", {"action": "down", "screen_x": -0.7, "screen_y": -0.6})
        req("touch", {"action": "move", "screen_x": -0.7, "screen_y": -0.25, "seconds": 0.15})
        time.sleep(1.0)
        req("touch", {"action": "up"})
        after = req("pose")
        moved = ((after["at"]["X"] - before["at"]["X"]) ** 2 + (after["at"]["Y"] - before["at"]["Y"]) ** 2) ** 0.5
        check("a finger on Roblox's thumbstick walks the player", moved > 200, f"moved {moved:.0f} cm")
        expect("back to the desktop", lambda: req("session", {"action": "restart", "device": "desktop"}, timeout=120),
               lambda r: r["running"] is True)

    # restart and stop
    t0 = time.time()
    expect("session restart: a fresh session", lambda: req("session", {"action": "restart"}, timeout=120),
           lambda r: r["running"] is True)
    print(f"  (restarted in {time.time() - t0:.1f} s)")
    ctx2 = until(lambda: (c := req("context")) and c.get("character") and c, 30)
    expect("after restart the gate is shut again", lambda: req("rc", {"verb": "property", "body": {
        "objectPath": "/Workspace/Grove/Gate", "propertyName": "Open"}}), lambda r: r == {"Open": False})
    check("after restart: the character is there", bool(ctx2))
    expect("session stop", lambda: req("session", {"action": "stop"}, timeout=60), lambda r: r["running"] is False)
    expect("context after stop", lambda: req("context"), lambda r: r["session_running"] is False)
    link.server.shutdown()
    return summary()


def _watch_walk(req, character):
    import threading

    def walk():
        time.sleep(0.1)
        req("move", {"direction": {"X": 0, "Y": 1, "Z": 0}, "seconds": 0.6})

    threading.Thread(target=walk, daemon=True).start()
    return req("watch", {"objects": [character], "seconds": 1.0})


def _face(req, path, tries=6):
    """Turn the view onto a thing with the mouse (as look_at does, roughly); whether it is near the middle."""
    import math

    for _ in range(tries):
        at = req("transforms", {"objects": [path]})["objects"][path]["location"]
        p = req("pose")
        dx, dy = at["X"] - p["eye"]["X"], at["Y"] - p["eye"]["Y"]
        bearing = (math.degrees(math.atan2(dy, dx)) - p["yaw"] + 180) % 360 - 180
        if abs(bearing) < 10:
            return True
        req("key", {"key": "MouseX", "mode": "press", "value": round(bearing / 0.5, 1)})
        time.sleep(0.6)
    return False


def _put(req, at):
    """Put the player at a spot (Roblox's x, y, z): a test's setup, a direct change."""
    req("rc", {"verb": "property", "body": {"objectPath": "Character", "propertyName": "position", "propertyValue": {
        "position": {"x": at[0], "y": at[1], "z": at[2]}}, "access": "WRITE_ACCESS"}})
    time.sleep(1.0)


def _go_to(req, path):
    """Put the player next to a thing (a test's setup: a direct change)."""
    at = req("transforms", {"objects": [path]})["objects"][path]["location"]
    req("rc", {"verb": "property", "body": {"objectPath": "Character", "propertyName": "position", "propertyValue": {
        "position": {"x": at["Y"] / 28 + 3, "y": at["Z"] / 28 + 4, "z": -at["X"] / 28}}, "access": "WRITE_ACCESS"}})
    time.sleep(1.0)


if __name__ == "__main__":
    sys.exit(main())
