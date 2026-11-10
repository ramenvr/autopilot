"""A Roblox place for the tests, as Studio's MCP server would serve it (engines/roblox_mcp.FakeStudio's handlers): no
Studio, no Luau. execute_luau's chunk is the engine's prelude and a JSON request (engines/roblox.luau's REQ); this
answers its ops from a small place model in Python, in Roblox's own units and axes (studs, x right, y up, -z forward),
as the prelude would. What it checks is the engine's side (framing, batching, conversion, the session, inputs); the
prelude's Luau itself runs only in a real Studio.

The place: a character standing on a Baseplate, a coin, a door and a lever (tagged), a leaderstats Coins counter,
a HUD label, and a console that logs an error once play starts. Keys held move the character (W forward at WalkSpeed
along the camera's look), and the `turn` op turns the camera. The tools answer as the live server's do (their
arguments, the input actions' JSON, get_studio_state's lines, script_read's line numbers): see docs/studio_tools.txt.
"""

import json
import math
import re
import threading
import time

from autopilot.engines import roblox_mcp as mcp

REQ = re.compile(r"JSONDecode\((\[(=*)\[.*?\]\2\])\)\)\s*$", re.S)


def req_of(code):
    """The REQ table a chunk carries (its JSON, out of the long string)."""
    m = REQ.search(code)
    if not m:
        return None
    lit, eq = m.group(1), m.group(2)
    return json.loads(lit[len(eq) + 2:-(len(eq) + 2)])


class Place:
    def __init__(self):
        self.lock = threading.Lock()
        self.running = False
        self.t0 = time.monotonic()
        self.reset()
        self.calls = []  # (tool, datamodel or None, ops or None)
        self.mode = "return"  # "return": the chunk's value comes back; "print": only its printed output does
        self.console = []

    def reset(self):
        self.at = [0.0, 3.0, 0.0]  # HumanoidRootPart (studs)
        self.look = [0.0, 0.0, -1.0]  # the camera's LookVector
        self.held = set()
        self.coins = 0
        self.door_open = False
        self.last = time.monotonic()
        self.things = {
            "Workspace/Baseplate": {"class": "Part", "at": [0, -0.5, 0], "size": [512, 1, 512], "tags": []},
            "Workspace/Coin": {"class": "Part", "at": [10, 2, -20], "size": [2, 2, 0.5], "tags": ["Coin"]},
            "Workspace/Door": {"class": "Part", "at": [0, 5, -40], "size": [8, 10, 1], "tags": ["Door"]},
            "Workspace/Lever": {"class": "Model", "at": [-6, 2, -36], "size": [1, 3, 1], "tags": ["Lever"]},
            "Workspace/Checkpoint@1": {"class": "SpawnLocation", "at": [0, 0, -60], "size": [6, 1, 6],
                                       "tags": ["Checkpoint"]},
        }

    # -- time and movement ------------------------------------------------------------------------------------

    def tick(self):
        now = time.monotonic()
        dt, self.last = now - self.last, now
        if not self.running:
            return
        flat = [self.look[0], 0.0, self.look[2]]
        n = math.hypot(flat[0], flat[2]) or 1.0
        f = [flat[0] / n, 0.0, flat[2] / n]
        r = [-f[2], 0.0, f[0]]  # (right of forward: Roblox's x is right when looking down -z)
        move = [0.0, 0.0, 0.0]
        for key, d, s in (("W", f, 1), ("S", f, -1), ("D", r, 1), ("A", r, -1)):
            if key in self.held:
                move = [move[i] + s * d[i] for i in range(3)]
        m = math.hypot(move[0], move[2])
        if m > 0:
            self.at = [self.at[0] + 16 * dt * move[0] / m, self.at[1], self.at[2] + 16 * dt * move[2] / m]

    def game_time(self):
        return round(time.monotonic() - self.t0, 3) if self.running else 0.0

    def turn(self, degrees):
        """Turn the camera right by `degrees` (about Roblox's up axis)."""
        a = -math.radians(degrees)
        x, z = self.look[0], self.look[2]
        self.look = [x * math.cos(a) + z * math.sin(a), self.look[1], -x * math.sin(a) + z * math.cos(a)]

    # -- the ops (as roblox.luau answers them) ------------------------------------------------------------------

    def op(self, name, a, datamodel):
        if name == "turn":
            if datamodel != "Client":
                raise RuntimeError("turn: the camera is the client's")
            self.turn(float(a.get("yaw") or 0.0))
            return {"look": dict(zip("xyz", self.look))}
        if name == "context":
            client = datamodel == "Client"
            return {"character": "Workspace/Hero" if self.running else "", "player": "Hero" if self.running else "",
                    "camera": "Workspace/Camera", "place": "TestPlace", "place_id": 0,
                    "viewport": {"x": 1280, "y": 720}, "running": self.running, "client": client,
                    "tags": ["Coin", "Door", "Lever", "Checkpoint"]}
        if name == "find":
            if a.get("class"):
                cls = a["class"][4:] if a["class"].startswith("tag:") else a["class"]
                objs = [p for p, t in self.things.items() if cls in t["tags"] or t["class"] == cls
                        or (cls == "BasePart" and t["class"] in ("Part", "SpawnLocation"))]
            else:
                objs = list(self.things) + (["Workspace/Hero"] if self.running and not a.get("things") else [])
            if a.get("name"):
                objs = [o for o in objs if a["name"].lower() in o.rsplit("/", 1)[-1].lower()]
            return {"objects": objs[: a.get("limit") or None]}
        if name == "transforms":
            out = {}
            for p in a.get("objects") or []:
                t = self.things.get(p)
                if p == "Workspace/Hero" and self.running:
                    t = {"at": self.at, "size": [2, 5, 1]}
                if not t:
                    out[p] = False
                else:
                    v = {"x": t["at"][0], "y": t["at"][1], "z": t["at"][2]}
                    out[p] = {"location": v, "rotation": {"x": 0, "y": 90 if p == "Workspace/Door" else 0, "z": 0},
                              "velocity": {"x": 0, "y": 0, "z": 0}, "center": v,
                              "size": {"x": t["size"][0], "y": t["size"][1], "z": t["size"][2]}, "active": True}
            return {"objects": out}
        if name == "pose":
            if not self.running:
                return {"character": ""}
            eye = [self.at[0], self.at[1] + 1.5, self.at[2]]
            return {"character": "Workspace/Hero", "at": dict(zip("xyz", self.at)), "eye": dict(zip("xyz", eye)),
                    "look": dict(zip("xyz", self.look)), "facing": dict(zip("xyz", self.look)),
                    "velocity": {"x": 0, "y": 0, "z": 0}, "on_ground": True, "state": "Running", "health": 100,
                    "max_health": 100, "fallen": False}
        if name == "movement":
            if not self.running:
                raise RuntimeError("movement: no Humanoid in the player's character")
            return {"walk_speed": 16, "use_jump_power": False, "jump_power": 50, "jump_height": 7.2,
                    "hip_height": 2, "root_half": 1, "radius": 1, "max_slope": 89, "gravity": 196.2, "rig": "R15"}
        if name == "get":
            if a["path"] == "Players/Hero/leaderstats" and a["prop"] == "Coins":
                return {"value": self.coins}
            if a["path"] == "Workspace/Door" and a["prop"] == "Open":
                return {"value": self.door_open}
            if a["path"] == "Workspace/Door" and a["prop"] == "Position":
                at = self.things["Workspace/Door"]["at"]
                return {"value": {"x": at[0], "y": at[1], "z": at[2]}}
            if a["path"] in self.things:
                raise RuntimeError(f"Property {a['prop']} not found on game.{a['path'].replace('/', '.')}")
            raise RuntimeError(f"Object {a['path']} does not exist")
        if name == "set":
            if a["path"] == "Workspace/Door" and a["prop"] == "Open":
                if datamodel != "Server":
                    raise RuntimeError("a write on the client's copy: it doesn't replicate")
                self.door_open = bool(a["value"])
                return {"set": "attribute"}
            raise RuntimeError(f"Object {a['path']} does not exist")
        if name == "call":
            if a["path"] == "ServerScriptService/Debug" and a["fn"] == "GiveCoin":
                self.coins += int((a.get("args") or [1])[0])
                return {"ReturnValue": self.coins, "returns": [self.coins]}
            raise RuntimeError(f"Function {a['fn']} not found")
        if name == "describe":
            if a["path"] in self.things or a["path"].startswith(("ServerScriptService/", "Players/")):
                return {"Name": a["path"].rsplit("/", 1)[-1], "Class": "Part", "Path": a["path"]}
            raise RuntimeError(f"Object {a['path']} does not exist")
        if name == "screen":
            return {"texts": {"Hud.Coins": f"Coins: {self.coins}"}} if self.running else {"texts": []}
        if name == "ui":
            return {"elements": [{"id": "Players/Hero/PlayerGui/Hud/Reset", "kind": "button", "name": "Reset",
                                  "owner": "Hud", "label": "Reset", "enabled": True, "x": 100, "y": 50}]}
        if name == "view":
            return {"visible": [{"name": "Coin", "path": "Workspace/Coin", "class": "Part", "tags": ["Coin"],
                                 "center": {"x": 10, "y": 2, "z": -20}, "size": {"x": 2, "y": 2, "z": 0.5},
                                 "distance": 22.4, "screen_x": 0.2, "screen_y": 0.0, "crosshair_on": False}],
                    "body": {"on_ground": True, "velocity": {"x": 0, "y": 0, "z": 0},
                             "sonar": {"ways": [{"turn": 0, "clear": 20, "by": "wall", "normal": {"x": 0, "y": 0,
                                                                                                   "z": 1}}]}
                             if a.get("sonar") else None},
                    "camera": {"fov": 100, "location": dict(zip("xyz", self.at)), "look": dict(zip("xyz", self.look))},
                    "omitted": ["held"]}
        if name == "point":
            return {"x": 800, "y": 300, "on_screen": True, "viewport": {"x": 1280, "y": 720}}
        if name == "scripts":
            src = {"ServerScriptService/Debug": "return {GiveCoin = function(n) end}",
                   "ServerScriptService/Main": "print('main')",
                   "StarterPlayer/StarterPlayerScripts/Hud": "local x = 1"}
            cls = {"ServerScriptService/Debug": "ModuleScript", "ServerScriptService/Main": "Script",
                   "StarterPlayer/StarterPlayerScripts/Hud": "LocalScript"}
            return {"scripts": [{"path": p, "class": cls[p], "hash": f"h{len(s)}", "size": len(s),
                                 **({"source": s} if a.get("source") else {})} for p, s in src.items()]}
        if name == "tags":
            return {"tags": {t: [{"path": p, "class": x["class"], "at": dict(zip("xyz", x["at"])), "attributes": []}
                                 for p, x in self.things.items() if t in x["tags"]]
                             for t in ("Coin", "Door", "Lever", "Checkpoint")}}
        raise RuntimeError(f"unknown op {name}")

    # -- the tools ----------------------------------------------------------------------------------------------

    def execute_luau(self, args):
        code = args.get("code") or ""
        datamodel = args.get("datamodel_type") or "Edit"
        req = req_of(code)
        with self.lock:
            self.tick()
            if req is None:  # (raw Luau: a probe of whether the client data model is there)
                self.calls.append((mcp.EXECUTE_LUAU, datamodel, None))
                if datamodel != "Edit" and not self.running:
                    return {"content": [{"type": "text", "text": f"{datamodel} data model is not available"}],
                            "isError": True}
                return "1"
            if datamodel != "Edit" and not self.running:
                return {"content": [{"type": "text", "text": f"{datamodel} data model is not available"}],
                        "isError": True}
            self.calls.append((mcp.EXECUTE_LUAU, datamodel, [o[0] for o in req["ops"]]))
            answers = []
            for name, a in req["ops"]:
                try:
                    answers.append({"ok": True, "v": self.op(name, a, datamodel)})
                except Exception as e:
                    answers.append({"ok": False, "e": str(e)})
            out = json.dumps({"t": self.game_time(), "r": answers, "dm": datamodel})
        if req.get("echo"):
            printed = f"{req['nonce']}{out}{req['nonce']}"
            return printed if self.mode == "print" else f"{printed}\nReturned: {out}"
        return "(printed nothing)" if self.mode == "print" else out

    def start_stop_play(self, args):
        with self.lock:
            self.calls.append((mcp.START_STOP_PLAY, None, args.get("is_start")))
            if args.get("is_start"):
                self.running = True
                self.t0 = time.monotonic()
                self.console.append("Hero joined the game")
                self.console.append("ServerScriptService.Broken:3: attempt to index nil with 'Value'")
            else:
                self.running = False
                self.reset()
        return "ok"

    def studio_state(self, args):
        if self.running:
            return ("- Current Studio Mode: Play\n- Available DataModels: Client, Server\n"
                    "- Focused DataModel in the viewport: Client")
        return "- Current Studio Mode: Edit\n- Available DataModels: Edit\n- Focused DataModel in the viewport: Edit"

    def console_output(self, args):
        return "\n".join(self.console)

    def keyboard(self, args):
        with self.lock:
            self.tick()
            self.calls.append((mcp.USER_KEYBOARD_INPUT, args.get("datamodel_type"), args.get("actions")))
            for act in args.get("actions") or []:
                if act["action"] == "keyDown":
                    self.held.add(act["key_code"])
                elif act["action"] == "keyUp":
                    self.held.discard(act["key_code"])
        return "Success"

    def mouse(self, args):
        with self.lock:
            self.calls.append((mcp.USER_MOUSE_INPUT, args.get("datamodel_type"), args.get("actions")))
            placed = False
            for act in args.get("actions") or []:
                placed = placed or "instance_path" in act or ("x" in act and "y" in act)
                if act["action"] != "wait" and not placed:  # (as the live server: a position first)
                    return {"content": [{"type": "text", "text": "Either x and y, instance_path, or a prior action "
                                                                 "that establishes mouse position is required"}],
                            "isError": True}
                if "mouse_button" in act and act["mouse_button"] not in mcp.MOUSE:
                    return {"content": [{"type": "text", "text": "mouse_button: left or right"}], "isError": True}
        return "Success"

    def navigate(self, args):
        with self.lock:
            self.calls.append((mcp.CHARACTER_NAVIGATION, None, args))
            if "x" in args and "z" in args:
                self.at = [args["x"], self.at[1], args["z"]]
        return "Success"

    def script_read(self, args):
        return "     1→return {GiveCoin = function(n) end}"

    def screen_capture(self, args):
        return PNG_2X2

    def server(self, **kw):
        return mcp.FakeStudio({
            mcp.EXECUTE_LUAU: self.execute_luau, mcp.START_STOP_PLAY: self.start_stop_play,
            mcp.GET_STUDIO_STATE: self.studio_state, mcp.GET_CONSOLE_OUTPUT: self.console_output,
            mcp.USER_KEYBOARD_INPUT: self.keyboard, mcp.USER_MOUSE_INPUT: self.mouse,
            mcp.CHARACTER_NAVIGATION: self.navigate, mcp.SCRIPT_READ: self.script_read,
            mcp.SCREEN_CAPTURE: self.screen_capture,
            mcp.SEARCH_GAME_TREE: lambda a: json.dumps([{"path": "game.Workspace.Coin", "class": "Part"}]),
        }, **kw)

    def luau_calls(self):
        return [c for c in self.calls if c[0] == mcp.EXECUTE_LUAU and c[2] is not None]


# A 2x2 PNG (red, green / blue, white), for screen_capture
PNG_2X2 = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000002000000020802000000fdd49a730000001449444154789c63f8cfc0c000c20cffffffff0f"
    "001fee05fbb0b897c90000000049454e44ae426082")
