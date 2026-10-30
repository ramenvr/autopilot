"""Protocol smoke test of the Unity plugin against a headless copy of the test game (no window, no rendering).

    python plugin/unity/com.ramenvr.autopilot/Tests~/smoke.py player|editor|ci [--exe <Build>/OrbCourse.exe] [--project <dir>]

Without --exe / --project it makes the test game first (testgame.py: TestCourse/ as a throwaway Unity project, its
scenes and a Development player built headless) and deletes it afterwards (--keep keeps it). It plays the brain's side
with the repo's autopilot.bridge.Link (read-only use), on its own port (18900+, never 8765), and checks every op against
that game: a menu scene (uGUI Start button, toggle, slider, dropdown and name field; a UI Toolkit label and button)
and a level with a first-person CharacterController player, four spinning orbs under "Orbs", a gate, a block, a wall
and a HUD counting the orbs. Exit code 0 when every check passed.

`player`: the Development player run -batchmode -nographics. `editor`: the editor in batch mode
(-executeMethod Autopilot.Playtest.Editor.Headless.Run), which enters play mode as the brain asks and reconnects after
each script reload (hello "resume"). `ci`: -Playtest=verify against a stub brain that answers with a result: the
report files and the exit code; a refused token (exit 1); and no brain at all with -PlaytestConnectTimeout (exit 1
and result.json with the error), for the player and the editor.
"""

import argparse
import json
import math
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
sys.path.insert(0, REPO)
from autopilot import bridge  # noqa: E402

sys.path.insert(0, HERE)
import testgame  # noqa: E402

# The Unity editor for the editor and ci modes: AUTOPILOT_UNITY, else Unity Hub's usual place for 6000.6.5f1
UNITY = testgame.UNITY
TOKEN = "smoke-token-7f3a"

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""), flush=True)
    return ok


# -- the brain's side: bridge.Link behind a server that keeps the session across a reconnect ----------------------------

class Brain:
    """Listens like bridge.serve_local, and also: records each connection's Authorization header, keeps the Link when
    the plugin closes with 1012 (a script reload in the editor) and rebinds it when the plugin says hello with
    {"resume": <session>}, so requests waiting across the reload get their answers."""

    def __init__(self, port, require_token=None, on_hello=None):
        from websockets.sync.server import serve

        self.port = port
        self.require_token = require_token
        self.on_hello = on_hello
        self.link = None
        self.session = f"smoke-{int(time.time())}"
        self.auth = []
        self.hellos = []
        self.ready = queue.Queue()
        self.resumes = 0
        self.closes = []
        self.server = serve(self.handler, "127.0.0.1", port, max_size=None)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def url(self):
        return f"ws://127.0.0.1:{self.port}/v1/bridge"

    def handler(self, ws):
        auth = ws.request.headers.get("Authorization", "")
        self.auth.append(auth)
        try:
            hello = json.loads(ws.recv(timeout=15))
        except Exception:
            return
        self.hellos.append(hello)
        if self.require_token and auth != f"Bearer {self.require_token}":
            ws.send(json.dumps({"type": "error", "message": "This token isn't accepted here", "code": 401}))
            return
        resumed = hello.get("resume") == self.session and self.link is not None and not self.link.closed
        if resumed:
            link = self.link
            self.resumes += 1
            with link._send_lock:
                link._send = lambda frame, ws=ws: ws.send(json.dumps(frame))
        else:
            link = bridge.Link(lambda frame, ws=ws: ws.send(json.dumps(frame)))
            link.loopback = True
            self.link = link
        link.feed(hello)
        ws.send(json.dumps({"type": "welcome", "role": "cli", "session": self.session}))
        if not resumed:
            self.ready.put(link)
        if self.on_hello:
            threading.Thread(target=self.on_hello, args=(link, hello), daemon=True).start()
        code = None
        try:
            for message in ws:
                try:
                    link.feed(json.loads(message))
                except ValueError:
                    continue
        except Exception as e:
            code = getattr(getattr(e, "rcvd", None), "code", None)
        if code is None:
            code = getattr(ws, "close_code", None)
        self.closes.append(code)
        if code == 1012:
            return  # (a script reload: the plugin comes back with "resume")
        if self.link is link:
            link.close("the plugin disconnected")

    def wait(self, timeout):
        return self.ready.get(timeout=timeout)

    def close(self):
        self.server.shutdown()


class Game:
    """Requests to the live link (the Brain's current one)."""

    def __init__(self, brain):
        self.brain = brain

    @property
    def link(self):
        return self.brain.link

    def req(self, op, args=None, timeout=None):
        return self.link.request(op, args or {}, timeout=timeout)

    def try_req(self, op, args=None, timeout=None):
        try:
            return self.req(op, args, timeout), None
        except bridge.BridgeError as e:
            return None, e.error

    def rc(self, verb, **body):
        return self.req("rc", {"verb": verb, "body": body})

    def get(self, path, prop):
        r = self.rc("property", objectPath=path, propertyName=prop)
        return r.get(prop) if isinstance(r, dict) else r

    def set(self, path, prop, value):
        return self.rc("property", objectPath=path, propertyName=prop, propertyValue={prop: value}, access="WRITE_ACCESS")

    def call(self, path, fn, **params):
        return self.rc("call", objectPath=path, functionName=fn, parameters=params)

    def pose(self):
        return self.req("pose")

    def wait_for(self, what, fn, timeout=15.0, every=0.2):
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            try:
                last = fn()
                if last:
                    return last
            except Exception as e:  # noqa
                last = e
            time.sleep(every)
        print(f"   (waited for {what}: last {last})")
        return None


# -- the checks ---------------------------------------------------------------------------------------------------

def kit_of(native):
    return {"X": 100 * native["z"], "Y": 100 * native["x"], "Z": 100 * native["y"]}


def close_to(a, b, tol):
    return all(abs(a[k] - b[k]) <= tol for k in ("X", "Y", "Z"))


def teleport(g, player, native):
    """Put the player somewhere (a CharacterController needs disabling while its transform is written)."""
    g.set(player + "|CharacterController", "enabled", False)
    g.set(player, "position", native)
    g.set(player + "|CharacterController", "enabled", True)


def test_hello(brain, link, host):
    h = link.hello
    check("hello: engine unity", h.get("engine") == "unity", h.get("engine"))
    check("hello: engine_version", re.match(r"^6000\.\d+\.\d+", h.get("engine_version", "")), h.get("engine_version"))
    pid = (h.get("project_id") or "").replace("-", "")
    check("hello: project_id is a GUID", re.fullmatch(r"[0-9a-f]{32}", pid or "") is not None, h.get("project_id"))
    check("hello: project name", h.get("project") == "OrbCourse", h.get("project"))
    check(f"hello: host {host}", h.get("host") == host, h.get("host"))
    check("hello: protocol 1", h.get("protocol") == 1)
    check("hello: input capabilities", (h.get("input") or {}).get("keys") is True, json.dumps(h.get("input")))
    check("Bearer token reached the server", f"Bearer {TOKEN}" in brain.auth, brain.auth[-1] if brain.auth else None)
    missing = link.missing_ops()
    check("no required op missing (bridge.required_ops)", not missing, missing)


def test_menu(g):
    ctx = g.req("context")
    check("context: project_dir", os.path.isdir(os.path.join(ctx.get("project_dir", ""), "Assets")), ctx.get("project_dir"))
    check("context: world is the menu", ctx.get("world", "").endswith("Menu.unity"), ctx.get("world"))
    texts = g.req("screen")["texts"]
    check("screen: title text", texts.get("MenuCanvas.TitleText") == "Orb Course", json.dumps(texts)[:300])
    els = {e["id"]: e for e in g.req("ui")["elements"]}
    want = {"MenuCanvas.StartButton": "button", "MenuCanvas.HardModeToggle": "checkbox", "MenuCanvas.VolumeSlider": "slider",
            "MenuCanvas.QualityDropdown": "combo", "MenuCanvas.NameField": "text_input"}
    check("ui: every control kind", all(els.get(k, {}).get("kind") == v for k, v in want.items()),
          {k: els.get(k, {}).get("kind") for k in want})
    check("ui: button label", els.get("MenuCanvas.StartButton", {}).get("label") == "Start", els.get("MenuCanvas.StartButton"))
    menu = "Menu:/MenuCanvas|MainMenu"
    g.req("ui_act", {"id": "MenuCanvas.HardModeToggle"})
    check("ui_act: toggle click (game's listener ran)", g.get(menu, "hardMode") is True)
    check("ui: toggle now checked", next(e for e in g.req("ui")["elements"] if e["id"] == "MenuCanvas.HardModeToggle")["checked"] is True)
    g.req("ui_act", {"id": "MenuCanvas.VolumeSlider", "action": "set", "value": 0.8})
    check("ui_act: slider set", abs(g.get(menu, "volume") - 0.8) < 1e-4, g.get(menu, "volume"))
    g.req("ui_act", {"id": "MenuCanvas.QualityDropdown", "option": "High"})
    dd = next(e for e in g.req("ui")["elements"] if e["id"] == "MenuCanvas.QualityDropdown")
    check("ui_act: dropdown option", dd.get("selected") == "High", dd)
    g.req("ui_act", {"id": "MenuCanvas.NameField", "action": "set", "text": "Nyx"})
    check("ui_act: text field", g.get(menu, "playerName") == "Nyx", g.get(menu, "playerName"))
    # UI Toolkit
    check("screen: a UI Toolkit label", texts.get("MenuDoc.VersionLabel") == "Version 1.0", {k: v for k, v in texts.items() if k.startswith("MenuDoc")})
    check("ui: a UI Toolkit button", els.get("MenuDoc.CreditsButton", {}).get("kind") == "button", els.get("MenuDoc.CreditsButton"))
    g.req("ui_act", {"id": "MenuDoc.CreditsButton"})
    check("ui_act: UI Toolkit button click (its clicked handler ran once)", g.get("Menu:/MenuDoc|MenuToolkit", "creditsClicks") == 1,
          g.get("Menu:/MenuDoc|MenuToolkit", "creditsClicks"))
    _, err = g.try_req("ui_act", {"id": "MenuCanvas.Nope"})
    check("ui_act: missing element error", err and "no element" in err, err)
    changes = g.link.session_changes
    g.req("ui_act", {"id": "MenuCanvas.StartButton"})
    ok = g.wait_for("the level", lambda: g.req("context").get("world", "").endswith("Main.unity"), 15)
    check("ui_act: Start button opens the level", ok)
    check("session event on the scene change", g.wait_for("session event", lambda: g.link.session_changes > changes, 5), g.link.session_changes)


def test_level(g):
    orbs = g.req("find", {"class": "Collectible"})["objects"]
    check("find: class Collectible -> 4 orbs", len(orbs) == 4, orbs)
    check("find: duplicate names numbered", orbs == ["Main:/Orbs/Orb", "Main:/Orbs/Orb@1", "Main:/Orbs/Orb@2", "Main:/Orbs/Orb@3"], orbs)
    gates = g.req("find", {"name": "gat"})["objects"]
    check("find: name substring", "Main:/Gate" in gates, gates)
    colliders = g.req("find", {"class": "Collider"})["objects"]
    check("find: base class (Collider) includes subclasses", "Main:/Orbs/Orb" in colliders and "Main:/Level/Ground" in colliders, len(colliders))
    allobj = g.req("find", {})["objects"]
    check("find: all objects", len(allobj) >= 15, len(allobj))
    _, err = g.try_req("find", {"class": "NoSuchType"})
    check("find: unknown class is an error", err is not None, err)

    # transforms vs native rc values: kit (X, Y, Z) = 100 * (z, x, y)
    tr = g.req("transforms", {"objects": orbs + ["Main:/Nope"]})["objects"]
    check("transforms: null for a missing path", tr.get("Main:/Nope", 1) is None)
    ok = True
    worst = 0.0
    for orb in orbs:
        native = g.get(orb, "position")
        kit = tr[orb]["location"]
        want = kit_of(native)
        worst = max(worst, max(abs(kit[k] - want[k]) for k in "XYZ"))
        ok = ok and close_to(kit, want, 0.2)
    check("transforms: location = 100*(z, x, y) of rc's position", ok, f"max error {worst:.3f} cm")
    orb0 = tr[orbs[0]]
    check("transforms: orb 0 at kit (500, 0, 100)", close_to(orb0["location"], {"X": 500, "Y": 0, "Z": 100}, 0.5), orb0["location"])
    # (the box of a spinning sphere's mesh bounds: 25 cm up, 25 to 35.4 cm across as it turns)
    e = orb0["extent"]
    check("transforms: extent is half the orb's size", abs(e["Z"] - 25) < 0.5 and 24.5 <= e["X"] <= 35.5 and 24.5 <= e["Y"] <= 35.5, e)
    gate = g.req("transforms", {"objects": ["Main:/Gate"]})["objects"]["Main:/Gate"]
    check("transforms: gate box from its children (center)", close_to(gate["center"], {"X": 60, "Y": 600, "Z": 125}, 1), gate["center"])

    # rotation: kit yaw = euler y, kit pitch = -euler x
    g.set("Main:/Level/Block", "eulerAngles", {"x": 20, "y": 30, "z": 0})
    rot = g.req("transforms", {"objects": ["Main:/Level/Block"]})["objects"]["Main:/Level/Block"]["rotation"]
    check("transforms: yaw = euler y, pitch = -euler x", abs(rot["Yaw"] - 30) < 0.05 and abs(rot["Pitch"] + 20) < 0.05, rot)

    p = g.pose()
    player = p.get("player")
    check("pose: the player", player == "Main:/Player", player)
    native = g.get(player, "position")
    check("pose: at is the body's middle (CharacterController centre)", close_to(p["at"], kit_of(native), 0.5), (p["at"], kit_of(native)))
    check("pose: eye 60 cm above", abs(p["eye"]["Z"] - p["at"]["Z"] - 60) < 0.5, (p["eye"], p["at"]))
    check("pose: on_ground", g.wait_for("on ground", lambda: g.pose().get("on_ground") is True, 5) is not None)
    m = g.req("movement")
    check("movement: walk_speed 400 cm/s", m.get("walk_speed") == 400, m)
    check("movement: jump_z = sqrt(2 g h)", abs((m.get("jump_z") or 0) - 100 * math.sqrt(2 * 20 * 1.2)) < 0.5, m.get("jump_z"))
    check("movement: gravity 2000 cm/s2 (the script's)", m.get("gravity") == 2000, m.get("gravity"))
    check("movement: capsule (100, 40), step 30, slope 45",
          (m.get("half_height"), m.get("radius"), m.get("step"), m.get("max_slope")) == (100, 40, 30, 45),
          (m.get("half_height"), m.get("radius"), m.get("step"), m.get("max_slope")))
    return orbs


def test_rc(g):
    pc = "Main:/Player|PlayerController"
    d = g.rc("describe", objectPath=pc)
    props = {x["Name"] for x in d["Properties"]}
    funcs = {x["Name"] for x in d["Functions"]}
    check("rc describe: fields (public and private)", {"moveSpeed", "jumps", "verticalSpeed", "controller"} <= props, sorted(props)[:20])
    check("rc describe: methods", {"Respawn", "Interact", "Update"} <= funcs, sorted(funcs))
    check("rc read: a private field", isinstance(g.get(pc, "verticalSpeed"), (int, float)))
    allp = g.rc("property", objectPath=pc)
    check("rc read: all properties", "moveSpeed" in allp and "cameraPivot" in allp and allp["cameraPivot"] == "Main:/Player/CameraPivot", {k: allp[k] for k in list(allp)[:6]})
    g.set(pc, "moveSpeed", 5)
    check("rc write: moveSpeed 5", g.get(pc, "moveSpeed") == 5)
    g.set(pc, "moveSpeed", 4)
    check("rc read via the GameObject (component member)", g.get("Main:/Player", "moveSpeed") == 4)
    v = g.get("Main:/Player|CharacterController", "center")
    check("rc: vectors native lowercase metres", isinstance(v, dict) and set(v) == {"x", "y", "z"}, v)
    g.call("Main:/Gate|Gate", "Toggle")
    check("rc call: Gate.Toggle", g.get("Main:/Gate|Gate", "isOpen") is True)
    g.call("Main:/Gate", "Toggle")
    check("rc call on the GameObject finds the component's method", g.get("Main:/Gate|Gate", "isOpen") is False)
    r = g.call("type:UnityEngine.Mathf", "Max", a=2.5, b=7.0)
    check("rc call: static method on a type path", r.get("ReturnValue") == 7.0, r)
    r = g.rc("property", objectPath="type:UnityEngine.Time", propertyName="timeScale")
    check("rc read: static property", r.get("timeScale") == 1.0, r)
    try:
        g.get("Main:/Nope", "x")
        err = None
    except bridge.BridgeError as e:
        err = e.error
    check("rc: missing object wording", err == "Object Main:/Nope does not exist", err)
    try:
        g.call(pc, "NoSuchMethod")
        err = None
    except bridge.BridgeError as e:
        err = e.error
    check("rc: missing function error", err and "does not exist" in err, err)
    r = g.rc("property", objectPath="Main:/Player")
    check("rc read: a GameObject's own state", r.get("tag") == "Player" and "Main:/Player|PlayerController" in r.get("components", []), r.get("components"))


def test_legacy(g):
    """The game's own UnityEngine.Input reads (LegacyProbe) see the bridge's keys, buttons, axes, wheel, gamepad and
    pointer: the package's IL post-processor routes them through LegacyInput."""
    probe = "Main:/LegacyProbe|LegacyProbe"
    inp = g.req("context").get("input") or {}
    check("legacy: context counts the routed UnityEngine.Input reads", inp.get("legacy_input_manager_injectable") is True
          and inp.get("legacy_input_reads", 0) >= 10, json.dumps(inp)[:400])
    # The player back at the start, looking level along kit +X (at the orbs): 10 mouse counts a degree
    teleport(g, "Main:/Player", {"x": 0, "y": 1.08, "z": 0})
    pose = g.pose()
    g.req("key", {"key": "MouseX", "mode": "press", "value": round(-((pose["yaw"] + 180) % 360 - 180) * 10, 1)})
    g.req("key", {"key": "MouseY", "mode": "press", "value": round(-pose["pitch"] * 10, 1)})
    time.sleep(0.3)
    g.set(probe, "enabled", True)
    time.sleep(0.3)

    def p(name):
        return g.get(probe, name)

    w0, u0 = p("wDowns"), p("wUps")
    g.req("key", {"key": "W", "mode": "press"})
    time.sleep(0.3)
    check("legacy: a W press is one GetKeyDown and one GetKeyUp", p("wDowns") == w0 + 1 and p("wUps") == u0 + 1,
          (p("wDowns") - w0, p("wUps") - u0))
    g.req("key", {"key": "W", "mode": "hold", "lease": 2})
    time.sleep(0.3)
    check("legacy: a held W is GetKey(KeyCode.W) and GetKey(\"w\")", p("wHeld") is True and p("wHeldByName") is True and p("anyKey") is True)
    g.req("key", {"key": "W", "mode": "release"})
    time.sleep(0.3)
    check("legacy: let go, W is up again", p("wHeld") is False)
    # An axis by the project's Input Manager axes: Horizontal's d moves it toward 1 (sensitivity 3), gravity back
    g.req("key", {"key": "D", "mode": "hold", "lease": 3})
    time.sleep(0.15)
    early = p("horizontal")
    time.sleep(0.6)
    late, raw = p("horizontal"), p("horizontalRaw")
    check("legacy: GetAxis(Horizontal) rises toward 1 with D held, GetAxisRaw is 1", 0 < early < 1 and late > 0.95 and raw == 1,
          (early, late, raw))
    g.req("key", {"key": "D", "mode": "release"})
    time.sleep(0.6)
    check("legacy: Horizontal falls back to 0 when D is let go", abs(p("horizontal")) < 0.05, p("horizontal"))
    j0, f0 = p("jumpDowns"), p("fireDowns")
    g.req("key", {"key": "SpaceBar", "mode": "press"})
    g.req("key", {"key": "LeftControl", "mode": "press"})
    time.sleep(0.3)
    check("legacy: GetButtonDown(Jump) by space, GetButtonDown(Fire1) by left ctrl", p("jumpDowns") == j0 + 1 and p("fireDowns") == f0 + 1,
          (p("jumpDowns") - j0, p("fireDowns") - f0))
    # The pointer, then clicks there
    r = g.req("pointer", {"screen_x": 0.5, "screen_y": -0.5})
    time.sleep(0.2)
    m = p("mouse")
    check("legacy: pointer puts Input.mousePosition there (screen_x 0.5, screen_y -0.5: 3/4 across, 1/4 up)",
          r.get("on_screen") is True and abs(m["x"] - r["x"]) <= 1 and abs(m["y"] - r["y"]) <= 1 and r["x"] > r["y"] > 0, (r, m))
    l0, r0 = p("leftClicks"), p("rightClicks")
    g.req("key", {"key": "LeftMouseButton", "mode": "press"})
    g.req("key", {"key": "RightMouseButton", "mode": "press"})
    time.sleep(0.3)
    check("legacy: LeftMouseButton and RightMouseButton are GetMouseButtonDown(0) and (1)",
          p("leftClicks") == l0 + 1 and p("rightClicks") == r0 + 1, (p("leftClicks") - l0, p("rightClicks") - r0))
    seen = (g.req("view", {"max_count": 8}) or {}).get("visible") or []
    if check("pointer: view sees something to point at", seen, len(seen)):
        r = g.req("pointer", {"object": seen[0]["path"]})
        check("pointer: at an object, where view shows it on screen",
              abs(r["screen_x"] - seen[0]["screen_x"]) < 0.05 and abs(r["screen_y"] - seen[0]["screen_y"]) < 0.05, (r, seen[0]))
    _, err = g.try_req("pointer", {"object": "Main:/NoSuchThing"})
    check("pointer: an object that isn't there is an error", err and "does not exist" in err, err)
    pose = g.pose()
    yaw, pitch = math.radians(pose["yaw"]), math.radians(pose["pitch"])
    eye = pose["eye"]
    ahead = {"X": eye["X"] + 500 * math.cos(pitch) * math.cos(yaw), "Y": eye["Y"] + 500 * math.cos(pitch) * math.sin(yaw),
             "Z": eye["Z"] + 500 * math.sin(pitch)}
    r = g.req("pointer", {"world": ahead})
    check("pointer: at a kit point 5 m along the view, the middle of the screen",
          r.get("on_screen") is True and abs(r["screen_x"]) < 0.02 and abs(r["screen_y"]) < 0.02, r)
    _, err = g.try_req("pointer", {})
    check("pointer: nothing to point at is an error", err and "pointer needs" in err, err)
    # Mouse movement and the wheel through their axes (sensitivity 0.1)
    g.req("key", {"key": "MouseX", "mode": "press", "value": 20})
    g.req("key", {"key": "MouseWheelAxis", "mode": "press", "value": 1})
    time.sleep(0.3)
    check("legacy: MouseX 20 is GetAxis(Mouse X) 2, the wheel's notch 0.1 and mouseScrollDelta 1",
          abs(p("lastMouseX") - 2) < 1e-3 and abs(p("lastWheel") - 0.1) < 1e-3 and p("lastScrollY") == 1,
          (p("lastMouseX"), p("lastWheel"), p("lastScrollY")))
    g.req("key", {"key": "Gamepad_FaceButton_Bottom", "mode": "press"})
    g.req("key", {"key": "H", "mode": "press"})
    g.req("key", {"key": "I", "mode": "press"})
    time.sleep(0.3)
    check("legacy: the pad's A is JoystickButton0, typed keys reach inputString",
          p("padADowns") >= 1 and p("typed").endswith("hi"), (p("padADowns"), p("typed")[-10:]))
    check("legacy: no UnityEngine.Input exception while the bridge is connected", p("errors") == 0, p("errors"))
    g.set(probe, "enabled", False)
    g.req("key", {"mode": "release_all"})


def test_input(g, orbs):
    player = "Main:/Player"
    pc = player + "|PlayerController"
    teleport(g, player, {"x": 0, "y": 1.08, "z": 0})
    time.sleep(0.4)
    # Mouse look: 100 counts at 0.1 degrees each turn the view 10 degrees right
    y0 = g.pose()["yaw"]
    g.req("key", {"key": "MouseX", "mode": "press", "value": 100})
    time.sleep(0.3)
    y1 = g.pose()["yaw"]
    turned = (y1 - y0 + 540) % 360 - 180
    check("mouse look: MouseX 100 turns the view 10 degrees right", abs(turned - 10) < 0.5, f"{y0:.2f} -> {y1:.2f}")
    p0 = g.pose()["pitch"]
    g.req("key", {"key": "MouseY", "mode": "press", "value": 50})
    time.sleep(0.3)
    p1 = g.pose()["pitch"]
    check("mouse look: MouseY 50 looks 5 degrees up (kit pitch +)", abs(p1 - p0 - 5) < 0.5, f"{p0:.2f} -> {p1:.2f}")
    g.req("key", {"key": "MouseY", "mode": "press", "value": -50})
    # A key press: jump
    j0 = g.get(pc, "jumps")
    g.wait_for("on ground", lambda: g.pose().get("on_ground"), 5)
    g.req("key", {"key": "SpaceBar", "mode": "press"})
    time.sleep(0.2)
    check("key press SpaceBar: the game jumped", g.get(pc, "jumps") == j0 + 1, g.get(pc, "jumps"))
    g.wait_for("landing", lambda: g.pose().get("on_ground"), 5)
    # A held key: W walks forward (the way the view faces), renewed like the kit's lease
    yaw = g.pose()["yaw"]
    a = g.pose()["at"]
    for _ in range(8):
        g.req("key", {"key": "W", "mode": "hold", "lease": 0.6})
        time.sleep(0.1)
    g.req("key", {"key": "W", "mode": "release"})
    b = g.pose()["at"]
    dx, dy = b["X"] - a["X"], b["Y"] - a["Y"]
    heading = math.degrees(math.atan2(dy, dx))
    dist = math.hypot(dx, dy)
    check("key hold W: walks forward along the kit yaw", dist > 150 and abs((heading - yaw + 540) % 360 - 180) < 3,
          f"moved {dist:.0f} cm heading {heading:.1f} vs yaw {yaw:.1f}")
    # A held key lets go by itself when not renewed (the lease)
    g.req("key", {"key": "S", "mode": "hold", "lease": 0.3})
    time.sleep(0.9)
    c = g.pose()["at"]
    time.sleep(0.4)
    d = g.pose()["at"]
    check("key lease: a held key not renewed is let go", math.hypot(d["X"] - c["X"], d["Y"] - c["Y"]) < 2, (c, d))
    _, err = g.try_req("key", {"key": "NoSuchKey", "mode": "press"})
    check("key: unknown key error", err and "Unknown key" in err, err)
    # Input System actions by name
    j0 = g.get(pc, "jumps")
    g.wait_for("on ground", lambda: g.pose().get("on_ground"), 5)
    g.req("input", {"action": "Player/Jump", "mode": "press"})
    time.sleep(0.2)
    check("input: Player/Jump press", g.get(pc, "jumps") == j0 + 1, g.get(pc, "jumps"))
    g.wait_for("landing", lambda: g.pose().get("on_ground"), 5)
    a = g.pose()["at"]
    g.req("input", {"action": "Move", "value": {"X": 1, "Y": 0}, "mode": "hold"})
    time.sleep(0.6)
    g.req("input", {"action": "Move", "mode": "release"})
    b = g.pose()["at"]
    yaw = g.pose()["yaw"]
    right = math.radians(yaw + 90)
    along = (b["X"] - a["X"]) * math.cos(right) + (b["Y"] - a["Y"]) * math.sin(right)
    check("input: Move (1, 0) strafes right", along > 100, f"{along:.0f} cm to the right")
    _, err = g.try_req("input", {"action": "Player/Nope"})
    check("input: unknown action error", err and "does not exist" in err, err)

    # move: a direction in the kit frame
    for via in ("auto", "keys", "controller"):
        teleport(g, player, {"x": 0, "y": 1.08, "z": 0})
        time.sleep(0.3)
        yaw = 37.0
        direction = {"X": math.cos(math.radians(yaw)), "Y": math.sin(math.radians(yaw)), "Z": 0}
        a = g.pose()["at"]
        r = g.req("move", {"direction": direction, "seconds": 0.6, "via": via})
        b = g.pose()["at"]
        heading = math.degrees(math.atan2(b["Y"] - a["Y"], b["X"] - a["X"]))
        tol = 25 if r.get("via") == "keys" else 3  # (keys walk 8 ways)
        check(f"move via {via} ({r.get('via')}): goes the kit direction (yaw 37)", r.get("moved", 0) > 100 and abs(heading - yaw) < tol,
              f"moved {r.get('moved')} cm, heading {heading:.1f}")
    teleport(g, player, {"x": 0, "y": 1.08, "z": 0})


def test_world(g, orbs):
    player = "Main:/Player"
    pc = player + "|PlayerController"
    # Face the orbs (+z: kit yaw 0) and look
    g.set(player, "eulerAngles", {"x": 0, "y": 0, "z": 0})
    time.sleep(0.3)
    v = g.req("view", {})
    names = [t["name"] for t in v["visible"]]
    check("view: the orbs in front are visible", {"Orb", "Orb@1", "Orb@2"} <= set(names), names)
    orb = next((t for t in v["visible"] if t["name"] == "Orb"), {})
    check("view: the middle orb is near the centre of the screen", abs(orb.get("screen_x", 9)) < 0.05, orb.get("screen_x"))
    check("view: distance ~ 5 m", abs(orb.get("distance", 0) - 475) < 40, orb.get("distance"))
    check("view: the wall behind is not visible", "Wall" not in names, names)
    check("view: camera fov and yaw", v["camera"].get("fov", 0) > 60 and abs(v["camera"]["yaw"]) < 0.5, v["camera"])
    check("view: body on the ground", v["body"].get("on_ground") is True and v["body"].get("standing_on") == "Ground", v["body"])
    only = g.req("view", {"only": ["Orb@1"]})
    check("view only", [t["name"] for t in only["visible"]] == ["Orb@1"], [t["name"] for t in only["visible"]])

    # The gate: walk up, press E (Interact), watch its panel swing
    teleport(g, player, {"x": 4.5, "y": 1.08, "z": 0})
    time.sleep(0.3)
    i0 = g.get(pc, "interactions")
    g.req("input", {"action": "Player/Interact", "mode": "press"})
    w = g.req("watch", {"objects": ["Main:/Gate/Panel", "Main:/Orbs/Orb@3", "Main:/Level/Block"], "seconds": 1.0})
    what = {(c["object"], c["what"]) for c in w["changes"]}
    check("input: Interact opens the gate", g.get(pc, "interactions") == i0 + 1 and g.get("Main:/Gate|Gate", "isOpen") is True)
    check("watch: the gate panel turned, the spinning orb turned, the block didn't",
          ("Main:/Gate/Panel", "turned") in what and ("Main:/Orbs/Orb@3", "turned") in what and not any(o == "Main:/Level/Block" for o, _ in what), sorted(what))

    # Pause: game time stands still
    g.req("pause", {"paused": True})
    t0 = g.req("ping")["game_time"]
    time.sleep(0.5)
    t1 = g.req("ping")["game_time"]
    check("pause: game time stops", abs(t1 - t0) < 0.05 and g.req("context")["paused"] is True, (t0, t1))
    g.req("pause", {"paused": False})
    time.sleep(0.3)
    check("unpause: game time runs", g.req("ping")["game_time"] > t1 + 0.1)
    g.req("timestep", {"fixed": True, "fps": 30})
    dt = g.rc("property", objectPath="type:UnityEngine.Time", propertyName="captureFramerate")
    check("timestep fixed 30", dt.get("captureFramerate") == 30, dt)
    g.req("timestep", {"fixed": False})
    g.req("detectors", {"enabled": True, "hud_every": 0.5})

    # Collect every orb: the HUD counts them
    before = len(g.link.findings)
    collected = []
    for orb in list(orbs):
        pos = g.get(orb, "position")
        teleport(g, player, {"x": pos["x"], "y": 1.08, "z": pos["z"] - 0.2})
        time.sleep(0.35)
        tr = g.req("transforms", {"objects": [orb]})["objects"]
        collected.append(tr.get(orb) is None)
        if orb == orbs[0]:
            later = g.req("transforms", {"objects": orbs[1:]})["objects"]
            check("paths stay with their objects after a sibling is gone", all(later.get(x) for x in orbs[1:]), list(later))
    check("all four orbs collected (gone)", all(collected), collected)
    texts = g.req("screen")["texts"]
    check("screen: HUD shows Orbs: 4/4", texts.get("HUD.OrbsText") == "Orbs: 4/4", texts)
    check("screen: TextMeshPro text (tags stripped)", texts.get("HUD.HintText") == "Find the orbs", texts.get("HUD.HintText"))
    time.sleep(1.2)
    check("detector hud_fraction: quiet while the counter is right", not [x for x in g.link.findings[before:] if x.get("detector") == "hud_fraction"])
    # (the course counts right: the test writes a counter past its total, as a buggy game would show it)
    g.set("Main:/HUD/OrbsText|Text", "text", "Orbs: 5/4")
    f = g.wait_for("hud_fraction finding", lambda: [x for x in g.link.findings[before:] if x.get("detector") == "hud_fraction"], 5)
    check("detector hud_fraction: a counter past its total (5/4)", f and f[0]["signature"] == "hud_fraction:HUD.OrbsText", f[0]["summary"] if f else None)
    # An error in the log
    g.call("type:UnityEngine.Debug", "LogError", message="smoke test error 12")
    f = g.wait_for("log_error finding", lambda: [x for x in g.link.findings if x.get("detector") == "log_error" and "smoke test error" in x.get("summary", "")], 5)
    check("detector log_error (and a log event)", f and "smoke test error" in f[0]["summary"] and any("smoke test error" in l.get("message", "") for l in g.link.logs),
          f[0]["summary"] if f else None)
    # Falling out of the world
    teleport(g, player, {"x": 30, "y": 1.08, "z": 30})
    f = g.wait_for("long_fall/out_of_world", lambda: [x for x in g.link.findings if x.get("detector") in ("out_of_world", "long_fall")], 15)
    check("detector out_of_world / long_fall when the player falls off the level", bool(f), [x["summary"] for x in (f or [])])


def test_sessions(g, editor):
    # restart: the launch scene again; with map: the level
    g.req("session", {"action": "restart"}, timeout=60)
    ctx = g.req("context")
    launch = "Menu.unity" if not editor else "Main.unity"
    check(f"session restart: the launch scene ({launch})", ctx["world"].endswith(launch), ctx["world"])
    g.req("session", {"action": "restart", "map": "Main"}, timeout=60)
    orbs = g.req("find", {"class": "Collectible"})["objects"]
    hud = g.req("screen")["texts"].get("HUD.OrbsText")
    check("session restart map=Main: the level afresh", len(orbs) == 4 and hud == "Orbs: 0/4", (len(orbs), hud))
    check("session status running", g.req("session", {"action": "status"})["running"] is True)


def test_levels_editor(g):
    """The editor plays the scene asked for (`session` `map`, through playModeStartScene) while the scene open in it
    stays open, and its own Play plays that one again afterwards (docs/protocol.md, The level a run plays)."""
    check("hello: levels", "levels" in g.link.hello.get("features", []), g.link.hello.get("features"))
    lv = g.req("levels")
    check("levels: the project's scenes, Main open and playing",
          {"Assets/Scenes/Menu.unity", "Assets/Scenes/Main.unity"} <= set(lv["levels"])
          and lv["open"].endswith("Main.unity") and lv["playing"].endswith("Main.unity"), json.dumps(lv)[:300])
    _, err = g.try_req("session", {"action": "restart", "map": "Nowhere"}, timeout=30)
    check("an unknown scene is refused, the game goes on", err and "No scene Nowhere" in err
          and g.req("context")["world"].endswith("Main.unity"), err)
    g.req("session", {"action": "stop"}, timeout=60)
    r = g.req("session", {"action": "start", "map": "Menu"}, timeout=120)
    ctx = g.req("context")
    check("session start map=Menu plays Menu, not the open scene", r.get("running") and ctx["world"].endswith("Menu.unity"), ctx["world"])
    lv = g.req("levels")
    check("levels: Main still open in the editor, Menu playing", lv["open"].endswith("Main.unity") and lv["playing"].endswith("Menu.unity"),
          json.dumps({k: lv[k] for k in ("open", "playing")}))
    g.req("session", {"action": "restart"}, timeout=60)
    ctx = g.req("context")
    check("a restart replays the scene asked for", ctx["world"].endswith("Menu.unity"), ctx["world"])
    g.req("session", {"action": "restart", "map": "Assets/Scenes/Main.unity"}, timeout=60)
    ctx = g.req("context")
    check("restart with a scene's path in play mode", ctx["world"].endswith("Main.unity"), ctx["world"])
    g.req("session", {"action": "stop"}, timeout=60)
    r = g.req("session", {"action": "start"}, timeout=120)
    ctx = g.req("context")
    check("after play mode ends, a start without a map plays the open scene again (start scene set back)",
          r.get("running") and ctx["world"].endswith("Main.unity"), ctx["world"])


def test_codex(g, export_mode="auto"):
    t = time.time()
    m = g.req("codex_manifest", {"export": export_mode}, timeout=900)
    secs = time.time() - t
    paths = [f["path"] for f in m["files"]]
    check("codex_manifest: export ran or was fresh", m["export"].get("error") is None, json.dumps(m["export"]))
    want = ["Packages/manifest.json", "ProjectSettings/ProjectSettings.asset", "Assets/Scripts/PlayerController.cs",
            "Assets/Input/Controls.inputactions", "Library/AutopilotCodex/summary.txt", "Library/AutopilotCodex/scenes/Scenes/Main.unity.txt",
            "Library/AutopilotCodex/input/Input/Controls.inputactions.txt", "Library/AutopilotCodex/build_scenes.txt"]
    check("codex_manifest: the code, settings and export", all(p in paths for p in want), [p for p in want if p not in paths])
    check("codex_manifest: nothing outside the rules", not any(p.startswith(("Library/Script", "Temp/", "Logs/")) or p.endswith(".meta") for p in paths),
          len(paths))
    check("codex_manifest: md5 hashes", all(re.fullmatch(r"[0-9a-f]{32}", f["hash"]) for f in m["files"]))
    print(f"   manifest: {len(paths)} files in {secs:.1f}s, export {m['export']}")
    r = g.req("codex_read", {"paths": ["Library/AutopilotCodex/scenes/Scenes/Main.unity.txt", "Assets/Scripts/OrbCounter.cs",
                                       "../secret.txt", "C:/Windows/win.ini", "Library/ScriptAssemblies/x.dll", "Temp/x.cs",
                                       "Assets/Scripts/PlayerController.cs.meta"]})
    scene = r["files"].get("Library/AutopilotCodex/scenes/Scenes/Main.unity.txt", "")
    check("codex_read: the scene export names objects by bridge paths, with values", "Main:/Orbs/Orb@3" in scene and "totalOrbs = 4" in scene
          and "PlayerController" in scene, scene[:200])
    check("codex_read: a script", "totalOrbs" in r["files"].get("Assets/Scripts/OrbCounter.cs", ""))
    check("codex_read: unsafe paths refused", set(r["missing"]) == {"../secret.txt", "C:/Windows/win.ini", "Library/ScriptAssemblies/x.dll",
                                                                    "Temp/x.cs", "Assets/Scripts/PlayerController.cs.meta"}, r["missing"])
    many = [p for p in paths if p.endswith(".cs")][:6]
    r = g.req("codex_read", {"paths": many, "max_bytes": 1000000})
    check("codex_read: several files (off the main thread)", len(r["files"]) == len(many), len(r["files"]))
    menu = g.req("codex_read", {"paths": ["Library/AutopilotCodex/scenes/Scenes/Menu.unity.txt"]})["files"].get("Library/AutopilotCodex/scenes/Scenes/Menu.unity.txt", "")
    check("codex export: a button's onClick target", "MainMenu.StartGame" in menu, re.findall(r".*onClick.*", menu)[:2])
    return m


def run_events(g):
    hb = g.link.last_heartbeat
    check("heartbeat events", hb is not None and time.time() - hb < 3, hb)
    p = g.req("ping")
    check("ping", "game_time" in p and abs(p["wall_time"] - time.time()) < 5, p)


# -- launching -----------------------------------------------------------------------------------------------------

class Proc:
    def __init__(self, cmd, log):
        print("launch:", " ".join(cmd), flush=True)
        self.p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.log = log

    def wait(self, timeout):
        try:
            return self.p.wait(timeout)
        except subprocess.TimeoutExpired:
            return None

    def kill(self):
        if self.p.poll() is None:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(self.p.pid)], capture_output=True)
            try:
                self.p.wait(10)
            except Exception:
                pass


def player_cmd(exe, url, log, *extra):
    return [exe, "-batchmode", "-nographics", "-logFile", log, f"-PlaytestUrl={url}", *extra]


def editor_cmd(project, url, log, *extra):
    return [UNITY, "-batchmode", "-nographics", "-projectPath", project, "-executeMethod", "Autopilot.Playtest.Editor.Headless.Run",
            "-logFile", log, f"-PlaytestUrl={url}", *extra]


def run_player(args):
    brain = Brain(args.port)
    log = os.path.join(args.work, "player.log")
    proc = Proc(player_cmd(args.exe, brain.url, log, "-PlaytestConnect", f"-PlaytestToken={TOKEN}"), log)
    try:
        t = time.time()
        link = brain.wait(60)
        print(f"   connected in {time.time() - t:.1f}s")
        g = Game(brain)
        test_hello(brain, link, "game")
        test_menu(g)
        orbs = test_level(g)
        test_rc(g)
        test_input(g, orbs)
        test_world(g, orbs)
        test_legacy(g)  # (its keys reach the player too: after the checks that need it where test_input left it)
        test_sessions(g, editor=False)
        run_events(g)
        if not args.skip_codex:
            test_codex(g)
    finally:
        proc.kill()
        brain.close()


def run_editor(args):
    brain = Brain(args.port)
    log = os.path.join(args.work, "editor.log")
    proc = Proc(editor_cmd(args.project, brain.url, log, f"-PlaytestToken={TOKEN}", "-PlaytestMap=Main"), log)
    try:
        t = time.time()
        link = brain.wait(300)
        print(f"   connected in {time.time() - t:.1f}s")
        g = Game(brain)
        test_hello(brain, link, "editor")
        check("editor: in play mode on connect", g.req("session", {"action": "status"})["running"] is True)
        # Out of play mode and in again: the scripts reload, the plugin resumes the session, the start is answered
        r = g.req("session", {"action": "stop"}, timeout=60)
        check("editor: session stop leaves play mode", r.get("running") is False and g.req("context")["session_running"] is False, r)
        t = time.time()
        r = g.req("session", {"action": "start"}, timeout=120)
        check("editor: session start enters play mode (answered across the script reload)", r.get("running") is True,
              f"{time.time() - t:.1f}s, resumes {brain.resumes}, closes {brain.closes}")
        check("editor: the plugin resumed the same session (1012, then hello resume)", brain.resumes >= 1 and 1012 in brain.closes, brain.closes)
        ctx = g.req("context")
        check("editor: back in the level", ctx["world"].endswith("Main.unity"), ctx["world"])
        orbs = test_level(g)
        test_rc(g)
        test_input(g, orbs)
        test_world(g, orbs)
        test_legacy(g)  # (its keys reach the player too: after the checks that need it where test_input left it)
        t = time.time()
        r = g.req("session", {"action": "restart", "fresh": True}, timeout=120)
        check("editor: fresh restart (out of play mode and in)", r.get("running") is True and len(g.req("find", {"class": "Collectible"})["objects"]) == 4,
              f"{time.time() - t:.1f}s")
        run_events(g)
        test_levels_editor(g)
        if not args.skip_codex:
            test_codex(g, "refresh")
    finally:
        proc.kill()
        brain.close()


def run_ci(args):
    """-Playtest=verify against a stub brain: it asks the game a few things and sends a result; the game writes the
    report files and exits with the verdict's code. Then a refused token: exit 1."""
    outcomes = []

    def stub(link, hello):
        run = hello.get("run") or {}
        outcomes.append(run)
        time.sleep(1.0)
        try:
            link.status("Reading the game")
            ctx = link.request("context", {}, timeout=60)
            link.request("screen", {}, timeout=30)
            outcomes.append(ctx.get("world"))
        except Exception as e:
            outcomes.append(f"error {e}")
        report = {"verdict": "DENIED", "reason": "stub brain", "claim": run.get("claim"), "exit_code": 2}
        link.send({"type": "result", "result": report, "files": {"report.json": json.dumps(report),
                                                                  "junit.xml": "<testsuite/>", "../escape.txt": "no"}})

    targets = []
    if args.exe:
        targets.append(("player", lambda url, log, *x: player_cmd(args.exe, url, log, *x)))
    if args.project:
        targets.append(("editor", lambda url, log, *x: editor_cmd(args.project, url, log, *x)))
    for name, cmd in targets:
        brain = Brain(args.port, on_hello=stub)
        report_dir = os.path.join(args.work, f"report-{name}")
        log = os.path.join(args.work, f"ci-{name}.log")
        proc = Proc(cmd(brain.url, log, "-Playtest=verify", "-PlaytestClaim=Verify that the orb counter never passes its total",
                        f"-PlaytestReportDir={report_dir}", f"-PlaytestToken={TOKEN}"), log)
        t = time.time()
        code = proc.wait(400)
        proc.kill()
        brain.close()
        files = sorted(os.listdir(report_dir)) if os.path.isdir(report_dir) else []
        check(f"ci {name}: hello carried the run", outcomes and isinstance(outcomes[0], dict) and outcomes[0].get("kind") == "verify", outcomes[:1])
        check(f"ci {name}: exit code 2 for DENIED", code == 2, f"{code} after {time.time() - t:.0f}s")
        check(f"ci {name}: report files written (and none outside the folder)",
              files == ["escape.txt", "junit.xml", "report.json", "result.json"]
              and not os.path.exists(os.path.join(args.work, "escape.txt")), files)
        outcomes.clear()
        # A refused token: the brain answers the hello with an error, the game exits 1
        brain = Brain(args.port + 1, require_token="the-right-token")
        log = os.path.join(args.work, f"ci-{name}-refused.log")
        proc = Proc(cmd(brain.url, log, "-Playtest=explore", "-PlaytestMinutes=1", f"-PlaytestReportDir={report_dir}-refused",
                        "-PlaytestToken=a-wrong-token"), log)
        code = proc.wait(300)
        proc.kill()
        brain.close()
        check(f"ci {name}: refused token -> exit 1", code == 1, f"{code}; auth seen {brain.auth}")
        # No brain at all: with -PlaytestConnectTimeout the game gives up, writes result.json with the error, exits 1
        nowhere = f"ws://127.0.0.1:{args.port + 2}/v1/bridge"
        timeout_dir = f"{report_dir}-timeout"
        log = os.path.join(args.work, f"ci-{name}-timeout.log")
        t = time.time()
        proc = Proc(cmd(nowhere, log, "-Playtest=verify", "-PlaytestClaim=Verify that nothing answers",
                        f"-PlaytestReportDir={timeout_dir}", "-PlaytestConnectTimeout=5"), log)
        code = proc.wait(300)
        proc.kill()
        result = {}
        try:
            with open(os.path.join(timeout_dir, "result.json"), encoding="utf-8") as f:
                result = json.load(f)
        except (OSError, ValueError):
            pass
        check(f"ci {name}: no brain, -PlaytestConnectTimeout=5 -> exit 1", code == 1, f"{code} after {time.time() - t:.0f}s")
        check(f"ci {name}: ... and result.json says why", "within 5 s" in result.get("error", "") and result.get("exit_code") == 1, result)


def ensure_game(args, player=True):
    """The game from --exe / --project, else the test game made now (testgame.py). Returns the TestGame made, or None."""
    if args.exe or args.project:
        return None
    game = testgame.make(player=player)
    args.exe, args.project = game.exe, game.project
    return game


def finish(game, work, args):
    """Delete what this run made, unless --keep."""
    if args.keep:
        if game:
            print(f"kept the test game: {game.root}")
        print(f"kept the work folder: {work}")
        return
    if game:
        game.remove()
    if not args.work_given:
        testgame.remove(work)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["player", "editor", "ci"])
    ap.add_argument("--exe", help="a Development player build of the test game (default: make the test game)")
    ap.add_argument("--project", help="the test game's Unity project folder (default: make the test game)")
    ap.add_argument("--port", type=int, default=18901)
    ap.add_argument("--work", help="where logs and reports go (default: a new temporary folder, deleted at the end)")
    ap.add_argument("--skip-codex", action="store_true")
    ap.add_argument("--keep", action="store_true", help="keep the test game this made (and the work folder)")
    args = ap.parse_args()
    args.work_given = bool(args.work)
    started = time.time()
    work = args.work or tempfile.mkdtemp(prefix="rgsmoke-")
    args.work = work
    os.makedirs(work, exist_ok=True)
    game = None
    try:
        game = ensure_game(args, player=args.mode != "editor")
        {"player": run_player, "editor": run_editor, "ci": run_ci}[args.mode](args)
    except Exception as e:
        import traceback

        traceback.print_exc()
        check(f"{args.mode}: ran to the end", False, repr(e))
    finally:
        finish(game, work, args)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\n{args.mode}: {passed}/{len(RESULTS)} checks passed in {time.time() - started:.0f}s")
    for name, ok, detail in RESULTS:
        if not ok:
            print(f"  FAILED: {name}  {detail}")
    sys.exit(0 if passed == len(RESULTS) else 1)


if __name__ == "__main__":
    main()
