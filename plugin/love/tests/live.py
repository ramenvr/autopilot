"""The LÖVE module on its test game, Coin Field, against the brain's own bridge and kit (no model, no keys): every op
the brain needs answers, the kit reads the game (things, the player, the HUD's text), keys move the player, rc reads,
writes and calls the game's Lua, the codex lists the game's files, a planted bug comes back as a log_error finding, and
a restart starts the game over on the same session.

    python plugin/love/tests/live.py [--port 18960]

Needs LÖVE 11.5 (`love` on PATH, or AUTOPILOT_LOVE) and, on Linux without a display, xvfb-run. The game and the
module are copied into a temporary folder for the run.
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, ROOT)

from autopilot import bridge, engines  # noqa: E402
from autopilot.kit.game import Game  # noqa: E402
from autopilot.kit.geom import Vec  # noqa: E402


class Checks:
    def __init__(self):
        self.failed, self.passed = [], 0

    def check(self, name, ok, detail=""):
        print(f"{'PASS' if ok else 'FAIL'} {name}" + (f": {detail}" if detail and not ok else ""))
        if ok:
            self.passed += 1
        else:
            self.failed.append(name)

    def summary(self):
        print(f"\n{self.passed} passed, {len(self.failed)} failed" + (f": {', '.join(self.failed)}" if self.failed else ""))
        return not self.failed


def assemble():
    """The test game with the module beside it, in a temporary folder."""
    dest = tempfile.mkdtemp(prefix="coin_field_")
    shutil.copytree(os.path.join(HERE, "game"), dest, dirs_exist_ok=True)
    shutil.copytree(os.path.join(HERE, "..", "autopilot"), os.path.join(dest, "autopilot"))
    return dest


def start_love(project, url, log):
    eng = engines.get("love")
    cmd = [eng.default_exe, project, f"--playtest-url={url}", f"--playtest-log={log}"]
    if sys.platform.startswith("linux") and not os.environ.get("DISPLAY") and shutil.which("xvfb-run"):
        cmd = ["xvfb-run", "-a"] + cmd
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def stop(proc):
    try:
        os.killpg(proc.pid, 9)
    except (OSError, AttributeError):
        proc.kill()


def wait_for(fn, seconds=5.0, every=0.05):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        v = fn()
        if v:
            return v
        time.sleep(every)
    return fn()


def run(c, link, project):
    game = Game(link)
    c.check("hello says the engine", link.hello.get("engine") == "love" and link.hello.get("host") == "game",
            str(link.hello))
    c.check("the game's name", link.hello.get("project") == "Coin Field", link.hello.get("project"))
    missing = link.missing_ops()
    c.check("every op the brain needs answers", missing == [], str(missing))

    ctx = game.context(fresh=True)
    c.check("context: the player found", ctx.get("character") == "/player", ctx.get("character"))
    c.check("context: 2D, LÖVE", ctx.get("dimension") == "2d" and ctx.get("engine") == "love")
    c.check("context: the project's folder", os.path.samefile(ctx.get("project_dir"), project), ctx.get("project_dir"))
    keys = [k["action"] for k in ctx.get("controls", [])]
    c.check("context: the keys the code reads", {"right", "d", "space", "e"} <= set(keys), str(keys))

    # things
    c.check("actors by type field", len(game.actors("Coin")) == 5, str(game.actors("Coin")))
    c.check("actors by the table they are in", game.actors("slime") == ["/slimes/1"], str(game.actors("slime")))
    try:
        game.actors("Dragon")
        c.check("an unknown class is an error", False)
    except Exception:
        c.check("an unknown class is an error", True)
    things = game.all_actors()
    c.check("all things", {"/player", "/bell", "/slimes/1"} <= set(things), str(things))
    loc = game.loc("/coins/1")
    c.check("a coin's place in the kit frame", loc == Vec(240, 0, -316), str(loc))
    c.check("the bell's middle (x, y with a size: its top left)", game.loc("/bell") == Vec(612, 0, -112),
            str(game.loc("/bell")))
    pose = game.pose()
    c.check("pose: the player's middle", pose["at"] == Vec(116, 0, -316), str(pose["at"]))
    m = engines.of(game).movement(game)
    c.check("movement from the player's fields", m.get("walk_speed") == 180 and m.get("jump_z") == 420
            and m.get("gravity") == 1200 and m.get("half_height") == 16, str(m))

    # main.lua's locals (the callbacks' upvalues), a "_private" field, and data with an x and a y
    c.check("context: main.lua's locals", {"flock", "atlas"} <= set(ctx.get("locals", [])), str(ctx.get("locals")))
    birds = game.actors("bird")
    c.check("things in main.lua's locals", birds == [f"/@local/flock/_birds/{i}" for i in (1, 2, 3)], str(birds))
    c.check("a local's thing by path", game.loc("/@local/flock/_birds/2") == Vec(400, 0, -60)
            or abs(game.loc("/@local/flock/_birds/2").z + 60) < 1e-6, str(game.loc("/@local/flock/_birds/2")))
    things = game.all_actors()
    first_data = min(i for i, t in enumerate(things) if t.startswith("/@local/atlas/"))
    c.check("live things before data", all(things.index(t) < first_data for t in ("/player", "/slimes/1")
                                                 + tuple(birds)), str(things[:12]))
    c.check("data with an x and a y is still found", len([t for t in things if t.startswith("/@local/atlas/")]) == 200)

    # what is on screen
    texts = wait_for(lambda: game.screen())
    c.check("the HUD's text", "Score: 0" in texts.values(), str(texts))
    c.check("glyphs drawn one by one read as their text", "Lives: 3" in texts.values(), str(texts))
    c.check("a shadow isn't the text twice", list(texts.values()).count("Coin Field") == 1, str(texts))
    view = game.view()
    names = [v["path"] for v in view.get("visible", [])]
    props = [v["path"] for v in view.get("visible", []) if v.get("prop")]
    c.check("view: the live things first, then the props, nearest first", "/player" not in names
            and names.index("/slimes/1") < names.index("/coins/1") and props[:1] == ["/coins/1"], str(names))
    c.check("view: a sprite atlas's frames aren't on screen", not any(n.startswith("/@local/atlas/") for n in names)
            and "/@local/flock/_birds/1" in names, str(names))

    # keys
    x0 = game.loc("/player").x
    game.hold("Right")
    time.sleep(0.6)
    game.release("Right")
    x1 = game.loc("/player").x
    c.check("holding Right walks right", x1 - x0 > 60, f"{x0} -> {x1}")
    c.check("walking takes coins", "Score: 0" not in game.screen().values() or game.get("/", "score") > 0,
            str(game.screen()))
    r = game.request("move", {"direction": {"X": -1, "Y": 0, "Z": 0}, "seconds": 0.4})
    c.check("move left", r.get("via") == "keys" and r.get("moved", 0) > 30 and game.loc("/player").x < x1, str(r))
    game.press("SpaceBar")
    up = wait_for(lambda: game.get("/player", "onGround") is False, 1.0)
    c.check("SpaceBar jumps (love.keypressed)", up is True or game.loc("/player").z > -316)
    wait_for(lambda: game.get("/player", "onGround") is True, 3.0)

    # rc
    c.check("rc reads a field", game.get("/player", "speed") == 180)
    c.check("rc reads a nested field", game.get("/", "gameState.level") == 1)
    game.set("/player", "speed", 240, why="test")
    c.check("rc writes", game.get("/player", "speed") == 240)
    c.check("rc calls a method by its parameters' names", game.ret("/bell", "ring", times=3) == 3)
    d = game.describe("/bell")
    c.check("describe lists fields and functions", any(p["Name"] == "rung" for p in d["Properties"])
            and any(f["Name"] == "ring" and f["Arguments"] == [{"Name": "times"}] for f in d["Functions"]), str(d))
    game.set("/player", "position", {"x": 300, "y": 316}, why="test")
    c.check("position writes a thing's place", game.loc("/player") == Vec(300, 0, -316), str(game.loc("/player")))

    # codex
    man = game.request("codex_manifest", {})
    paths = [f["path"] for f in man["files"]]
    c.check("codex: the game's Lua files, not the module's", "main.lua" in paths and "conf.lua" in paths
            and not any(p.startswith("autopilot/") for p in paths), str(paths))
    read = game.request("codex_read", {"paths": ["main.lua", "../etc/passwd", "autopilot/init.lua"]})
    c.check("codex: reads a file, refuses others", "Coin Field" in read["files"].get("main.lua", "")
            and len(read["missing"]) == 2, str(read["missing"]))

    # the planted bug
    before = len(link.findings)
    game.press("E")
    got = wait_for(lambda: [f for f in link.findings[before:] if f.get("detector") == "log_error"], 3.0)
    c.check("a Lua error comes back as a log_error", bool(got), str(link.findings[before:]))
    c.check("the game goes on after it", wait_for(lambda: game.request("ping")["game_time"] > 0, 1.0))

    # pointer
    p = game.pointer(object="/bell")
    c.check("pointer at a thing", p.get("on_screen") is True and abs(p["x"] - 612) <= 1, str(p))

    # pictures: PNGs from the game, JPEGs in the brain
    pictures(c, game, link)

    # restart: the game starts over (LÖVE restarts in the same process) on the same session
    session = link.session
    game.restart()
    c.check("restart: the same session goes on", link.resumes == 1 and link.session == session, f"{link.resumes}")
    c.check("restart: the game started over", game.get("/", "score") == 0 and game.get("/player", "speed") == 180
            and game.loc("/player") == Vec(116, 0, -316), str(game.loc("/player")))


def pixel(frame, x, y):
    rgb = frame.reformat(format="rgb24").planes[0]
    at = y * rgb.line_size + x * 3
    return tuple(bytes(rgb)[at:at + 3])


def pictures(c, game, link):
    """`capture` and `record`: the game's picture as PNGs, which the brain's media keeps as JPEGs (a still, an MP4)."""
    import av

    from autopilot import media

    c.check("hello says the game has a picture", media.can_capture(link), str(link.hello.get("features")))
    started = time.monotonic()
    shot = game.request("capture", {"max_dim": 640, "quality": 80}, timeout=15)
    took = time.monotonic() - started
    c.check("capture: a PNG, scaled to max_dim", "png" in shot and "jpeg" not in shot
            and (shot.get("width"), shot.get("height")) == (640, 480), f"{shot.get('width')}x{shot.get('height')}")
    jpeg = media.jpeg_of(shot, 80)
    frames = av.CodecContext.create("mjpeg", "r").decode(av.Packet(jpeg)) if jpeg else []
    c.check("capture: the brain makes it a JPEG", bool(frames) and (frames[-1].width, frames[-1].height) == (640, 480))
    if frames:
        ground = pixel(frames[-1], 320, 440)  # (the field: green, 0.3 0.6 0.3)
        c.check("capture: it is the game's picture", ground[1] > ground[0] + 40 and ground[1] > ground[2] + 40,
                str(ground))
    c.check("capture: answered quickly", took < 2.0, f"{took:.2f} s")
    folder = tempfile.mkdtemp(prefix="love_media_")
    try:
        rel = media.still(link, folder, "look")
        c.check("a still saved as a JPEG", bool(rel) and open(os.path.join(folder, rel), "rb").read(2) == b"\xff\xd8",
                str(rel))
        frame_times = []
        fps_before = game.request("ping")
        with media.Recorder(link, folder) as rec:
            rec.begin(1)
            game.hold("Right")
            for _ in range(10):
                time.sleep(0.2)
                frame_times.append(game.request("ping")["game_time"])
            game.release("Right")
            rec.end(1)
        video = os.path.join(folder, "media", "recording-1.mp4")
        c.check("record: an MP4 of the episode", os.path.exists(video) and rec.kept >= 6,
                f"kept {rec.kept}, received {rec.received}, dropped {rec.dropped}, {rec.problem}")
        if os.path.exists(video):
            with av.open(video) as v:
                decoded = list(v.decode(video=0))
            c.check("record: the window's size (under max_dim 960)", (decoded[0].width, decoded[0].height) == (800, 600),
                    f"{decoded[0].width}x{decoded[0].height}")
        c.check("record: the game ran on while it recorded", frame_times[-1] - frame_times[0] > 1.0
                and fps_before is not None, str(frame_times))
        stats = game.request("record", {"action": "stop"})
        c.check("record: stopped", stats.get("recording") is False)
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def cli_run(c, port):
    """A run asked on the game's command line, against a stand-in brain: the hello carries the run, the result's
    files land in the report folder with result.json, and the game exits with the result's code (DENIED: 2)."""
    import json

    from websockets.sync.server import serve

    hellos = []

    def handler(ws):
        hello = json.loads(ws.recv(timeout=10))
        hellos.append(hello)
        ws.send(json.dumps({"type": "welcome", "role": "service", "session": "s1", "features": []}))
        ws.send(json.dumps({"type": "result", "result": {"verdict": "DENIED", "reason": "no"},
                            "files": {"report.json": "{\"ok\": false}", "../escape.txt": "x"}}))
        try:
            for _ in ws:
                pass
        except Exception:
            pass

    project = assemble()
    report = os.path.join(project, "report")
    server = serve(handler, "127.0.0.1", port)
    import threading

    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        cmd = [engines.get("love").default_exe, project, "--playtest=verify", "--playtest-claim=Verify that it works",
               f"--playtest-url=ws://127.0.0.1:{port}/v1/bridge", f"--playtest-report-dir={report}",
               "--playtest-share=0"]
        if sys.platform.startswith("linux") and not os.environ.get("DISPLAY") and shutil.which("xvfb-run"):
            cmd = ["xvfb-run", "-a"] + cmd
        code = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60).returncode
        run = (hellos[0] if hellos else {}).get("run", {})
        c.check("cli: the hello carries the run", run.get("kind") == "verify" and run.get("claim") == "Verify that it works",
                str(hellos))
        c.check("cli: the hello says not to share", (hellos[0] if hellos else {}).get("share") is False)
        c.check("cli: DENIED exits 2", code == 2, str(code))
        got = sorted(os.listdir(report)) if os.path.isdir(report) else []
        c.check("cli: the report's files and result.json", got == ["escape.txt", "report.json", "result.json"], str(got))
        result = json.load(open(os.path.join(report, "result.json"))) if "result.json" in got else {}
        c.check("cli: result.json is the result", result.get("verdict") == "DENIED")
        # no brain at all: the game gives up after its connect timeout, with exit code 1 and the error in result.json
        cmd2 = [x for x in cmd if not x.startswith("--playtest-url")] + [
            f"--playtest-url=ws://127.0.0.1:{port + 1}/v1/bridge", "--playtest-connect-timeout=3"]
        code = subprocess.run(cmd2, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60).returncode
        result = json.load(open(os.path.join(report, "result.json")))
        c.check("cli: no brain exits 1 after the connect timeout", code == 1 and "no welcome" in result.get("error", ""),
                f"{code} {result}")
    finally:
        server.shutdown()
        shutil.rmtree(project, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=18960)
    a = ap.parse_args()
    c = Checks()
    try:
        cli_run(c, a.port + 10)
    except Exception as e:
        import traceback

        traceback.print_exc()
        c.check("cli: no exception", False, repr(e))
    project = assemble()
    log = os.path.join(project, "love.log")
    url = f"ws://127.0.0.1:{a.port}/v1/bridge"
    proc = None
    try:
        proc = start_love(project, url, log)
        link = bridge.serve_local(port=a.port, wait=60)
        run(c, link, project)
    except Exception as e:
        import traceback

        traceback.print_exc()
        c.check("no exception", False, repr(e))
    finally:
        if proc:
            stop(proc)
        if os.path.exists(log):
            tail = open(log, encoding="utf-8", errors="replace").read().splitlines()[-15:]
            print("--- love.log (last lines)\n" + "\n".join(tail))
        shutil.rmtree(project, ignore_errors=True)
    sys.exit(0 if c.summary() else 1)


if __name__ == "__main__":
    main()
