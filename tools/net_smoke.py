"""Multiplayer games played in the editor, live: a test game made from the engine's third-person template, played
the way dedicated-server games are tested (Play As Client: a dedicated server and its clients in one editor), with
Autopilot in its Plugins/, driven through the bridge the way a brain drives it. One editor per net mode (the editor
reads its Play settings when Play In Editor starts, and a run must not change them):

    client      Play As Client, one client, one process: the player is the client's (input, view, screen, frames),
                the server's copies are named, pausing reaches the server, restarts come back to the client
    clients2    two clients: player 1 is the first one's, player 2 the second's ("player": 2), each moving alone
    separate    Run Under One Process off: the server is another process; play works, pausing says it can't
    listen      Play As Listen Server: the player hosts the game
    listen2     a listen server and a client: player 1 hosts, player 2 is the client, each driven apart (keys, moves,
                Enhanced Input, the mouse's turn, view, screen, context), a third player is a 404, the pointer is
                player 1's, and another number of players is refused
    standalone  Play Standalone: as before

    python tools/net_smoke.py --engine "C:/Program Files/Epic Games/UE_5.4" --work C:/Users/me/nettest
        [--plugin <an Autopilot build>] [--template <a TP_ThirdPersonBP folder>] [--modes client,listen] [--port 8791]
        [--rendered]

Without --plugin, the test game is the C++ template with tools/net_smoke/Source in it (a training dummy only its
server changes, and an attack, F, that the server carries out), built with this checkout's plugin source. With
--plugin (a BuildPlugin package, e.g. a release zip's Autopilot/), the Blueprint template is played instead (no
compiler needed) and the dummy's checks are skipped; --template gives the Blueprint template for an engine installed
without templates. The editor runs headless (-nullrhi: stills answer that there is no picture); --rendered draws its
window and checks the frames (a rendered editor beside other work can take a machine's GPU driver down: only on
purpose). Exits 1 when a check fails.
"""

import argparse
import base64
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from engine_smoke import Checks, location, yaw  # noqa: E402

from autopilot import bridge  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
PLUGIN_SOURCE = os.path.join(REPO, "plugin", "unreal", "Autopilot")
GAMEPLAY = "/Script/Engine.Default__GameplayStatics"
PLAY_SETTINGS = "/Script/UnrealEd.Default__LevelEditorPlaySettings"
DUMMY = "/Script/TP_ThirdPerson.NetDummy"
MOVE = "/Game/ThirdPerson/Input/Actions/IA_Move.IA_Move"
MODES = {  # mode: (PlayNetMode, clients, RunUnderOneProcess)
    "client": ("PIE_Client", 1, True),
    "clients2": ("PIE_Client", 2, True),
    "separate": ("PIE_Client", 1, False),
    "listen": ("PIE_ListenServer", 1, True),
    "listen2": ("PIE_ListenServer", 2, True),
    "standalone": ("PIE_Standalone", 1, True),
}


def copy_template(template, engine, project):
    shutil.copytree(template, project, ignore=shutil.ignore_patterns("Media", "TemplateDefs.ini", "config.ini"))
    defs = os.path.join(template, "Config", "TemplateDefs.ini")
    for line in open(defs, encoding="utf-8-sig") if os.path.exists(defs) else []:
        if line.strip().startswith("SharedContentPacks="):
            mount = line.split('MountName="')[1].split('"')[0]
            level = line.split('DetailLevels=("')[1].split('"')[0]
            for root in (engine, os.path.dirname(os.path.dirname(template))):
                pack = os.path.join(root, "Templates", "TemplateResources", level, mount, "Content")
                if os.path.isdir(pack):
                    shutil.copytree(pack, os.path.join(project, "Content", mount), dirs_exist_ok=True)
                    break


def make_project(engine, work, plugin, template):
    """The test game in work/NetTest (made afresh): returns its .uproject and whether it is the C++ one."""
    project = os.path.join(work, "NetTest")
    shutil.rmtree(project, ignore_errors=True)
    cpp = not plugin
    template = template or os.path.join(engine, "Templates", "TP_ThirdPerson" if cpp else "TP_ThirdPersonBP")
    copy_template(template, engine, project)
    plugins = os.path.join(project, "Plugins", "Autopilot")
    if cpp:
        shutil.copytree(os.path.join(HERE, "net_smoke", "Source"), os.path.join(project, "Source"), dirs_exist_ok=True)
        shutil.copytree(PLUGIN_SOURCE, plugins,
                        ignore=shutil.ignore_patterns("Binaries", "Intermediate", "HostProject"))
    else:
        shutil.copytree(plugin, plugins, ignore=shutil.ignore_patterns("HostProject", "Intermediate", "AgentService"))
    name = "TP_ThirdPerson" if cpp else "TP_ThirdPersonBP"
    uproject = os.path.join(project, f"{name}.uproject")
    with open(uproject, encoding="utf-8-sig") as f:
        descriptor = json.load(f)
    # (the template's own plugins stay: a newer template's code needs them, e.g. StateTree)
    ours = ("Autopilot", "EnhancedInput")
    descriptor["Plugins"] = [p for p in descriptor.get("Plugins") or [] if p.get("Name") not in ours]
    descriptor["Plugins"] += [{"Name": "Autopilot", "Enabled": True}, {"Name": "EnhancedInput", "Enabled": True}]
    with open(uproject, "w", encoding="utf-8") as f:
        json.dump(descriptor, f, indent="\t")
    if cpp:
        # (a template of another engine version, --template: its targets build with this engine's settings, as an
        # installed engine's shared build environment requires)
        for target in glob.glob(os.path.join(project, "Source", "*.Target.cs")):
            with open(target, encoding="utf-8-sig") as f:
                text = f.read()
            text = re.sub(r"BuildSettingsVersion\.\w+", "BuildSettingsVersion.Latest", text)
            text = re.sub(r"EngineIncludeOrderVersion\.\w+", "EngineIncludeOrderVersion.Latest", text)
            with open(target, "w", encoding="utf-8") as f:
                f.write(text)
        build = os.path.join(engine, "Engine", "Build", "BatchFiles", "Build.bat")
        log = os.path.join(work, "build.log")
        print(f"building the test game ({log})", flush=True)
        with open(log, "w", encoding="utf-8", errors="replace") as f:
            done = subprocess.run([build, "TP_ThirdPersonEditor", "Win64", "Development", f"-Project={uproject}",
                                   "-WaitMutex"], stdout=f, stderr=subprocess.STDOUT)
        if done.returncode != 0:
            sys.exit(f"the test game didn't build ({done.returncode}): see {log}")
    return uproject, cpp


def set_play(uproject, mode):
    """The editor's Play settings for a mode, as the project's defaults (the user's saved ones taken away)."""
    project = os.path.dirname(uproject)
    shutil.rmtree(os.path.join(project, "Saved", "Config"), ignore_errors=True)
    net, clients, one = MODES[mode]
    with open(os.path.join(project, "Config", "DefaultEditorPerProjectUserSettings.ini"), "w", encoding="utf-8") as f:
        f.write("[/Script/UnrealEd.LevelEditorPlaySettings]\n"
                f"PlayNetMode={net}\nPlayNumberOfClients={clients}\nRunUnderOneProcess={one}\n"
                "bLaunchSeparateServer=False\n\n"
                # (an editor in the background would run at a few frames a second)
                "[/Script/UnrealEd.EditorPerformanceSettings]\nbThrottleCPUWhenNotForeground=False\n")


class Game:
    def __init__(self, link):
        self.link = link

    def __call__(self, op, args=None, timeout=30):
        return self.link.request(op, args or {}, timeout=timeout)

    def context(self, player=None):
        return self("context", {"player": player} if player else None)

    def call(self, obj, function, **params):
        return self("rc", {"verb": "call", "body": {"objectPath": obj, "functionName": function,
                                                    "parameters": params}}) or {}

    def get(self, obj, prop):
        return self("rc", {"verb": "property", "body": {"objectPath": obj, "propertyName": prop}})[prop]

    def time_of(self, world):
        return self.call(GAMEPLAY, "GetTimeSeconds", WorldContextObject=world)["ReturnValue"]

    def actors(self, world, cls):
        return self.call(GAMEPLAY, "GetAllActorsOfClass", WorldContextObject=world, ActorClass=cls)["OutActors"]

    def until(self, what, seconds=60, step=0.5):
        deadline = time.time() + seconds
        while True:
            try:
                got = what()
            except Exception:
                got = None
            if got or time.time() > deadline:
                return got
            time.sleep(step)


def moved(a, b):
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def check_play(game, check, ctx, mode):
    """The player's own play: walking, jumping, turning, an Enhanced Input action, what it sees, the picture."""
    character, controller = ctx["character"], ctx["controller"]
    server = (ctx.get("net") or {}).get("server_character")
    server_start = server and location(game.link, server)
    walked = game("move", {"direction": {"X": 1, "Y": 0, "Z": 0}, "scale": 1.0, "seconds": 0.8}, timeout=10)["moved"]
    check("move: the player walked", walked > 50, f"{walked:.0f} units")
    if server:
        time.sleep(0.5)  # (the server has it a moment later)
        there = moved(server_start, location(game.link, server))
        check("move: the server's copy of the player walked too", there > 50, f"{there:.0f} units")
    z0 = location(game.link, character)[2]
    game("key", {"key": "SpaceBar", "mode": "press"})
    peak = z0
    for _ in range(12):
        time.sleep(0.05)
        peak = max(peak, location(game.link, character)[2])
    check("key: SpaceBar jumped", peak - z0 > 20, f"rose {peak - z0:.0f}")
    time.sleep(1.2)
    yaw0 = yaw(game.link, controller)
    game("key", {"key": "MouseX", "mode": "hold", "value": 4.0, "lease": 0.3})
    time.sleep(0.6)
    turned = abs(((yaw(game.link, controller) - yaw0) + 180) % 360 - 180)
    check("key: MouseX turned the view", turned > 2, f"{turned:.0f} degrees")
    before = location(game.link, character)
    game("input", {"action": MOVE, "value": {"X": 0, "Y": 1, "Z": 0}, "mode": "hold"})
    time.sleep(0.6)
    game("input", {"action": MOVE, "mode": "release"})
    walked = moved(before, location(game.link, character))
    check("input: the Enhanced Input move action walked", walked > 50, f"{walked:.0f} units")
    view = game("view", {"max_count": 8})
    check("view: answers", isinstance(view, dict) and "camera" in view, ", ".join(list(view)[:6]))
    screen = game("screen")
    check("screen: answers", isinstance(screen.get("texts"), dict))
    if "media" not in (game.link.hello.get("features") or []):
        try:
            game("capture", {"max_dim": 640, "quality": 80}, timeout=20)
            check("capture: a headless game says it has no picture", False, "a picture came")
        except bridge.BridgeError as e:
            check("capture: a headless game says it has no picture", "no picture" in e.error, e.error)
        return
    try:
        still = game("capture", {"max_dim": 640, "quality": 80}, timeout=20)
        jpeg = base64.b64decode(still["jpeg"])
        check("capture: a picture of the player's view", len(jpeg) > 8000 and still["width"] > 100,
              f"{still['width']}x{still['height']}, {len(jpeg)} bytes")
    except Exception as e:
        check("capture: a picture of the player's view", False, e)


def check_attack(game, check, ctx):
    """F, the test game's attack: the client asks its server, which counts it (the controller's Hits)."""
    controller = ctx["controller"]
    hits = game.get(controller, "Hits")
    game("key", {"key": "F", "mode": "press"})
    got = game.until(lambda: game.get(controller, "Hits") > hits, seconds=5, step=0.2)
    check("key: F reached the server (an attack it counted)", bool(got),
          f"Hits {hits} -> {game.get(controller, 'Hits')}")


def check_pause(game, check, ctx, cpp):
    """Paused: the server's clock stops, and its dummy with it; the player's clock too. Then it runs again."""
    net = ctx.get("net") or {}
    world = net.get("server_world") or ctx["world"]
    dummy = cpp and (game.actors(world, DUMMY) or [None])[0]
    answer = game("pause", {"paused": True})
    check("pause: answered paused", answer.get("paused") is True, answer)
    time.sleep(0.5)
    t0, mine0, at0 = game.time_of(world), game.context()["game_time"], dummy and location(game.link, dummy)
    time.sleep(1.0)
    t1, mine1, at1 = game.time_of(world), game.context()["game_time"], dummy and location(game.link, dummy)
    check("pause: the server's clock stopped", abs(t1 - t0) < 0.01, f"{t0:.2f} -> {t1:.2f}")
    check("pause: the player's clock stopped", abs(mine1 - mine0) < 0.01, f"{mine0:.2f} -> {mine1:.2f}")
    if dummy:
        check("pause: the server's dummy stopped walking", moved(at0, at1) < 1, f"{moved(at0, at1):.0f} units")
    answer = game("pause", {"paused": False})
    check("unpause: answered running", answer.get("paused") is False, answer)
    time.sleep(0.5)
    t2 = game.time_of(world)
    time.sleep(1.0)
    t3 = game.time_of(world)
    check("unpause: the server's clock runs again", t3 - t2 > 0.5, f"{t2:.2f} -> {t3:.2f}")
    if dummy:
        a, b = location(game.link, dummy), (time.sleep(0.5), location(game.link, dummy))[1]
        check("unpause: the dummy walks again", moved(a, b) > 5, f"{moved(a, b):.0f} units")


def check_players(game, check, ctx, mode):
    """Two players in one editor: player 2 ("player": 2) has its own world, pawn, controller, keys, moves, Enhanced
    Input, turn, view and screen, and what one player is told to do moves it alone; a third player is a 404, the pointer
    is player 1's only, and a restart asking for another number of players is refused (the Play settings' stand)."""
    check("hello: says it plays several players", "players" in (game.link.hello.get("features") or []),
          game.link.hello.get("features"))
    listed = ctx.get("players") or []
    check("context: both players listed, player 1 the run's",
          [p.get("player") for p in listed] == [1, 2] and listed[0].get("you") is True and ctx.get("you") == 1, listed)
    two = game.until(lambda: (lambda c: c if c.get("character") else None)(game.context(2)), seconds=30)
    if not two:
        check("context: player 2's pawn", False, game.context(2))
        return
    first = "UEDPIE_2_" if mode == "clients2" else "UEDPIE_1_"  # (after a dedicated server and the first client)
    check("context: player 2's own world, pawn and controller",
          first in two["world"] and two["character"] != ctx["character"] and first in two["character"]
          and two.get("you") == 2 and bool(two["controller"]) and bool(two["input_subsystem"]),
          f"{two['world']} {two['character']} {two['controller']}")
    net2 = two.get("net") or {}
    check("context: player 2 is a client of the game, of 2 players",
          net2.get("mode") == "client" and net2.get("players") == 2 and net2.get("player") == 2, net2)
    if mode == "listen2":
        check("context: player 2's server copy is in player 1's world",
              "UEDPIE_0_" in net2.get("server_character", ""), net2.get("server_character"))
    pawns = {1: ctx["character"], 2: two["character"]}

    def where():
        return location(game.link, pawns[1]), location(game.link, pawns[2])

    def only(name, mover, act, settle=0.5):
        """act() moves player `mover` (1 or 2): it walks, the other stands."""
        a1, a2 = where()
        act()
        time.sleep(settle)
        b1, b2 = where()
        walked, stood = (moved(a2, b2), moved(a1, b1)) if mover == 2 else (moved(a1, b1), moved(a2, b2))
        check(f"{name}: player {mover} walked, the other stood", walked > 50 and stood < 5,
              f"walked {walked:.0f}, other {stood:.0f}")

    time.sleep(2.0)  # (the client's pawn just spawned may still be dropping onto the floor)
    forward = {"direction": {"X": 1, "Y": 0, "Z": 0}, "scale": 1.0, "seconds": 0.8}
    only("move", 2, lambda: game("move", {**forward, "player": 2}, timeout=10))
    only("move", 1, lambda: game("move", forward, timeout=10))

    def hold_w(player):
        whose = {"player": player} if player else {}
        game("key", {"key": "W", "mode": "hold", "lease": 2.0, **whose})
        time.sleep(0.8)
        game("key", {"key": "W", "mode": "release", **whose})

    only("key: W held", 2, lambda: hold_w(2))
    only("key: W held", 1, lambda: hold_w(None))

    def action(player):
        game("input", {"action": MOVE, "value": {"X": 0, "Y": 1, "Z": 0}, "mode": "hold", "player": player})
        time.sleep(0.6)
        game("input", {"action": MOVE, "mode": "release", "player": player})

    only("input: the Enhanced Input move action", 2, lambda: action(2))
    z1, z2 = location(game.link, pawns[1])[2], location(game.link, pawns[2])[2]
    game("key", {"key": "SpaceBar", "mode": "press", "player": 2})
    peak1, peak2 = z1, z2
    for _ in range(12):
        time.sleep(0.05)
        peak1, peak2 = max(peak1, location(game.link, pawns[1])[2]), max(peak2, location(game.link, pawns[2])[2])
    check("key: player 2's SpaceBar jumped player 2 alone", peak2 - z2 > 20 and peak1 - z1 < 5,
          f"player 2 rose {peak2 - z2:.0f}, player 1 {peak1 - z1:.0f}")
    time.sleep(1.2)
    yaw1, yaw2 = yaw(game.link, ctx["controller"]), yaw(game.link, two["controller"])
    game("key", {"key": "MouseX", "mode": "hold", "value": 4.0, "lease": 0.3, "player": 2})
    time.sleep(0.6)
    turned1 = abs(((yaw(game.link, ctx["controller"]) - yaw1) + 180) % 360 - 180)
    turned2 = abs(((yaw(game.link, two["controller"]) - yaw2) + 180) % 360 - 180)
    check("key: player 2's MouseX turned player 2's view alone", turned2 > 2 and turned1 < 0.5,
          f"player 2 {turned2:.0f} degrees, player 1 {turned1:.1f}")
    view1, view2 = game("view", {"max_count": 4}), game("view", {"max_count": 4, "player": 2})
    check("view: player 2's own camera", isinstance(view2, dict) and view2.get("camera") != view1.get("camera"),
          f"{view1.get('camera')} / {view2.get('camera')}")
    check("screen: player 2's answers", isinstance(game("screen", {"player": 2}).get("texts"), dict))
    for name, op, args, status, words in (
            ("a third player is a 404 that counts the players", "context", {"player": 3}, 404, "2 players"),
            ("the pointer is player 1's only", "pointer", {"screen_x": 0, "screen_y": 0, "player": 2}, None,
             "player 1's only"),
            ("another number of players is refused", "session", {"action": "restart", "players": 3}, 400,
             "can't be changed")):
        try:
            game(op, args)
            check(name, False, "it went through")
        except bridge.BridgeError as e:
            check(name, words in e.error and (status is None or e.status == status), f"{e.status}: {e.error}")
    check("session: still playing after the refused restart", game.context().get("session_running") is True)
    restarted = game("session", {"action": "restart", "players": 2}, timeout=180)
    again = game.context()
    check("session: a restart with the same players plays both again at once",
          restarted.get("running") is True and [p.get("player") for p in again.get("players") or []] == [1, 2],
          again.get("players"))
    two = game.until(lambda: (lambda c: c if c.get("character") else None)(game.context(2)), seconds=30)
    if not two:
        check("session: player 2's pawn after the restart", False, game.context(2))
        return
    pawns.update({1: again["character"], 2: two["character"]})
    time.sleep(2.0)
    only("move after the restart", 2, lambda: game("move", {**forward, "player": 2}, timeout=10))


def check_mode(game, check, mode, cpp):
    started = game("session", {"action": "start"}, timeout=120)
    check("session: started", started.get("running") is True, started)
    ctx = game.until(lambda: (lambda c: c if c.get("character") else None)(game.context()), seconds=60)
    if not ctx:
        check("context: a player", False, game.context())
        return
    net = ctx.get("net") or {}
    world = ctx["world"]
    if mode in ("client", "clients2", "separate"):
        # (the editor's first client is PIE instance 1 after an in-process server, 0 when the server is elsewhere)
        first = "UEDPIE_0_" if mode == "separate" else "UEDPIE_1_"
        check("context: the client's world", first in world and net.get("mode") == "client", f"{world} {net}")
        if mode == "separate":
            check("context: the server is in another process", net.get("server_world") == "", net)
        else:
            check("context: the server's world and copies named",
                  "UEDPIE_0_" in net.get("server_world", "") and "UEDPIE_0_" in net.get("server_character", "")
                  and "UEDPIE_0_" in net.get("server_controller", ""), net)
            check("context: clients counted", net.get("clients") == MODES[mode][1], net.get("clients"))
    else:
        want = "listen_server" if mode.startswith("listen") else mode
        check(f"context: {mode}", net.get("mode") == want and "UEDPIE_0_" in world, f"{world} {net}")
    check("context: the local player's controller and input", bool(ctx["controller"] and ctx["input_subsystem"]),
          f"{ctx['controller']} {ctx['input_subsystem']}")

    if mode == "clients2":
        # Only the first client's player moves (the server sees the others stand)
        server = net["server_world"]
        others = [p for p in game.actors(server, "/Script/Engine.Character") if p != net["server_character"]]
        before = {p: location(game.link, p) for p in others}
        game("move", {"direction": {"X": 1, "Y": 0, "Z": 0}, "scale": 1.0, "seconds": 0.8}, timeout=10)
        time.sleep(0.5)
        walked = {p.rsplit(".", 1)[-1]: round(moved(before[p], location(game.link, p))) for p in others}
        check("move: the other client's player stood still", bool(others) and max(walked.values()) < 5, walked)
    if mode in ("clients2", "listen2"):
        check_players(game, check, ctx, mode)
        return

    check_play(game, check, ctx, mode)
    if cpp:
        check_attack(game, check, ctx)
    if mode == "separate":
        answer = game("pause", {"paused": True})
        check("pause: says the server can't be paused from here",
              answer.get("paused") is False and "another process" in str(answer.get("why")), answer)
    else:
        check_pause(game, check, ctx, cpp)

    try:
        game("rc", {"verb": "property", "body": {"objectPath": PLAY_SETTINGS, "propertyName": "PlayNetMode",
                                                 "propertyValue": {"PlayNetMode": "PIE_Standalone"},
                                                 "access": "WRITE_ACCESS"}})
        check("rc: the editor's Play settings can't be changed", False, "the write went through")
    except bridge.BridgeError as e:
        check("rc: the editor's Play settings can't be changed", "Play settings" in e.error, e.error)
    try:  # (Enhanced Input's FInputActionValue has no field a remote call can set: it would inject zero)
        game.call(ctx["input_subsystem"], "InjectInputForAction", Action=MOVE, RawValue={"X": 0, "Y": 1, "Z": 0},
                  Modifiers=[], Triggers=[])
        check("rc: an input value it can't set is refused", False, "the call went through")
    except bridge.BridgeError as e:
        check("rc: an input value it can't set is refused", "InjectInputVectorForAction" in e.error, e.error)
    net_mode = game.get(PLAY_SETTINGS, "PlayNetMode")
    check("rc: the Play settings still read", "Standalone" not in net_mode or mode == "standalone", net_mode)

    if mode == "client":
        restarted = game("session", {"action": "restart"}, timeout=120)
        ctx = game.until(lambda: (lambda c: c if c.get("character") else None)(game.context()), seconds=60)
        check("session: restarted into the client's world",
              restarted.get("running") is True and ctx and "UEDPIE_1_" in ctx["world"], ctx and ctx["world"])
        if ctx:
            time.sleep(2.0)  # (a pawn just spawned drops onto the floor first: walking in the air covers little)
            walked = game("move", {"direction": {"X": 1, "Y": 0, "Z": 0}, "scale": 1.0, "seconds": 0.8},
                          timeout=10)["moved"]
            check("session: the player walks after the restart", walked > 50, f"{walked:.0f} units")


def run_mode(engine, uproject, mode, port, cpp, check, rendered=False):
    set_play(uproject, mode)
    log = os.path.join(os.path.dirname(uproject), f"editor-{mode}.log")
    exe = os.path.join(engine, "Engine", "Binaries", "Win64", "UnrealEditor.exe")
    url = f"ws://127.0.0.1:{port}/v1/bridge"
    print(f"== {mode} ({log})", flush=True)
    editor = subprocess.Popen([exe, uproject, "-unattended", "-nosplash", "-nosound", "-log", "-Playtest=goal",
                               "-PlaytestStayOpen", f"-PlaytestUrl={url}", "-PlaytestShare=0", f"-abslog={log}"]
                              + ([] if rendered else ["-nullrhi"]),
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    link = None
    try:
        link = bridge.serve_local(port=port, wait=1200)
        check("connected", True, f"{mode}: engine {link.hello.get('engine_version')}")
        check_mode(Game(link), check, mode, cpp)
        with_stop = Game(link)("session", {"action": "stop"}, timeout=60)
        check("session: stopped", with_stop.get("running") is False, with_stop)
    except Exception as e:
        check(f"{mode}", False, f"{type(e).__name__}: {e}")
    finally:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(editor.pid)], capture_output=True)
        editor.wait(timeout=60)
        if link is not None:
            link.server.shutdown()  # (the next mode listens on the same port)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", required=True)
    ap.add_argument("--work", required=True, help="a short folder for the test game (Windows paths: keep it short)")
    ap.add_argument("--plugin", help="an Autopilot build to play with (the Blueprint template)")
    ap.add_argument("--template", help="the Blueprint template to use (an engine installed without templates)")
    ap.add_argument("--modes", default=",".join(MODES))
    ap.add_argument("--port", type=int, default=8791)
    ap.add_argument("--reuse", action="store_true", help="play the test game already in --work (built before)")
    ap.add_argument("--rendered", action="store_true",
                    help="draw the editor's window (frames checked): on purpose only")
    args = ap.parse_args()
    os.makedirs(args.work, exist_ok=True)
    if args.reuse:
        names = [n for n in os.listdir(os.path.join(args.work, "NetTest")) if n.endswith(".uproject")]
        uproject = os.path.join(args.work, "NetTest", names[0])
        cpp = names[0] == "TP_ThirdPerson.uproject"
    else:
        uproject, cpp = make_project(args.engine, args.work, args.plugin, args.template)
    global MOVE  # (a newer template keeps its input actions in /Game/Input)
    if os.path.exists(os.path.join(os.path.dirname(uproject), "Content", "Input", "Actions", "IA_Move.uasset")):
        MOVE = "/Game/Input/Actions/IA_Move.IA_Move"
    check = Checks()
    for mode in args.modes.split(","):
        run_mode(args.engine, uproject, mode, args.port, cpp, check, rendered=args.rendered)
    verdict = "all checks passed" if not check.failed else "FAILED: " + ", ".join(check.failed)
    print(f"{os.path.basename(args.engine)}: {verdict}")
    sys.exit(1 if check.failed else 0)


if __name__ == "__main__":
    main()
