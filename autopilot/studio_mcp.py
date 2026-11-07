"""Pictures of a Roblox game through Roblox Studio's own MCP server (StudioMCP, installed with Studio:
create.roblox.com/docs/studio/mcp).

A Studio plugin can't read the game's pixels today: CaptureService's screenshots are textures a script can show but
never read back (an EditableImage refuses them), and StudioCaptureService, which hands a plugin the pixels, says
"Feature not supported yet" in current Studios (the plugin uses it once Studio turns it on:
plugin/roblox/src/Media.luau, and its PNG pictures are made JPEGs here: jpeg_of). Studio's MCP server can: its
`screen_capture` tool answers the game view as an image (the 3D view and the game's own UI; not Roblox's top bar),
whether or not Studio is the window in front. The agent service runs on the computer Studio runs on, so it starts
StudioMCP as any MCP client does (over stdio) and answers the brain's picture requests for the Roblox plugin itself
(Link.local_ops, autopilot/bridge.py):

    capture   one picture now: {"jpeg", "width", "height", "t", "via"}; 409 "no picture: ..." while no game plays
    record    {"action": "start", "fps", "max_dim", "quality"}: `frame`s a few times a second while a game plays, fed to
              the Link as a plugin's own frames are (autopilot/media.py), across play sessions as Godot's go on, until
              {"action": "stop"} or the connection's end

It needs Studio's MCP server turned on (in Studio: Assistant, ..., Settings, MCP Servers, "Enable Studio as MCP
server"). AUTOPILOT_STUDIO_MCP is the command line to start it (else, as Roblox's docs give it,
/Applications/RobloxStudio.app/Contents/MacOS/StudioMCP on macOS and cmd.exe /c %LOCALAPPDATA%\\Roblox\\mcp.bat on
Windows); AUTOPILOT_STUDIO_MCP=off leaves the game without pictures.
"""

import base64
import itertools
import json
import os
import shlex
import subprocess
import sys
import threading
import time
from fractions import Fraction

import autopilot

MCP_PROTOCOL = "2025-06-18"
START_TIMEOUT = 20.0  # seconds for StudioMCP to start and answer `initialize`
CALL_TIMEOUT = 20.0  # seconds for one tool call
# seconds for a picture (one takes well under one): a call that hangs (Studio's MCP server sometimes leaves one
# unanswered) costs a recording this much, and the server is started over after it
SHOT_TIMEOUT = 5.0
# seconds Studio gets to join a server that has just started (it takes a few: 1.6-3.7 seen), and how often to look
STUDIO_WAIT = 15.0
STUDIO_LOOK = 0.25
FPS = (0.2, 10.0)  # the frames a second a recording may ask for
MAX_DIM = 960
QUALITY = 75
OFF = ("off", "0", "no", "false", "none")
# Asked of each unnamed Studio (a multiplayer test's server and clients are Studios of their own, unnamed): whether it
# is player 1's client, the player Autopilot plays (Studio's test players are Player1, Player2...: UserId -1, -2...)
PLAYER_ONE = ('local p = game:GetService("Players").LocalPlayer '
              'return if p and p.UserId == -1 then "player 1" else "other"')
ENABLE_HINT = ("turn on Studio's MCP server (in Studio: Assistant, ..., Settings, MCP Servers, Enable Studio as MCP "
               "server)")


def command():
    """The command line that starts Studio's MCP server here, or None (not installed, or turned off)."""
    v = os.environ.get("AUTOPILOT_STUDIO_MCP", "").strip()
    if v.lower() in OFF:
        return None
    if v:
        return shlex.split(v, posix=os.name != "nt")
    if sys.platform == "darwin":
        for app in ("/Applications", os.path.expanduser("~/Applications")):
            exe = os.path.join(app, "RobloxStudio.app", "Contents", "MacOS", "StudioMCP")
            if os.path.exists(exe):
                return [exe]
        return None
    if os.name == "nt":
        bat = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Roblox", "mcp.bat")
        return ["cmd.exe", "/c", bat] if os.path.exists(bat) else None
    return None


class McpError(Exception):
    """Studio's MCP server couldn't do it (or isn't there)."""


class NoStudio(McpError):
    """The server is up and no Studio is connected to it (yet)."""


class McpClient:
    """An MCP client of one server over stdio: JSON-RPC 2.0, a message a line. call() waits for its answer; what the
    server asks of the client (ping) is answered as an empty result."""

    def __init__(self, argv, start_timeout=START_TIMEOUT):
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        try:
            self.proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.DEVNULL, creationflags=flags)
        except OSError as e:
            raise McpError(f"couldn't start Studio's MCP server ({argv[0]}): {e}") from e
        self._ids = itertools.count(1)
        self._pending = {}  # id -> [threading.Event, message or None]
        self._lock = threading.Lock()
        self._write = threading.Lock()
        self.closed = None  # why it ended, once it has
        threading.Thread(target=self._read, name="studio-mcp", daemon=True).start()
        try:
            init = self.call("initialize", {"protocolVersion": MCP_PROTOCOL, "capabilities": {},
                                            "clientInfo": {"name": "autopilot",
                                                           "version": getattr(autopilot, "__version__", "0")}},
                             timeout=start_timeout)
        except McpError:
            self.close()
            raise
        self.server = (init or {}).get("serverInfo") or {}
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

    def _send(self, message):
        line = (json.dumps(message) + "\n").encode("utf-8")
        with self._write:
            try:
                self.proc.stdin.write(line)
                self.proc.stdin.flush()
            except (OSError, ValueError) as e:
                raise McpError(f"Studio's MCP server is gone: {e}") from e

    def _read(self):
        try:
            for raw in self.proc.stdout:
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue  # (a line of its own logging)
                if not isinstance(msg, dict):
                    continue
                if "method" in msg:
                    if "id" in msg:  # (a request to the client: ping, or one this client doesn't serve)
                        if msg["method"] == "ping":
                            reply = {"jsonrpc": "2.0", "id": msg["id"], "result": {}}
                        else:
                            reply = {"jsonrpc": "2.0", "id": msg["id"],
                                     "error": {"code": -32601, "message": "not supported"}}
                        try:
                            self._send(reply)
                        except McpError:
                            break
                    continue
                with self._lock:
                    entry = self._pending.get(msg.get("id"))
                if entry:
                    entry[1] = msg
                    entry[0].set()
        except (OSError, ValueError):
            pass
        code = self.proc.poll()
        self._end(f"Studio's MCP server ended{'' if code is None else f' (exit code {code})'}")

    def _end(self, why):
        if self.closed is None:
            self.closed = why
        with self._lock:
            pending = list(self._pending.values())
        for entry in pending:
            entry[0].set()

    def call(self, method, params=None, timeout=CALL_TIMEOUT):
        if self.closed:
            raise McpError(self.closed)
        rid = next(self._ids)
        entry = [threading.Event(), None]
        with self._lock:
            self._pending[rid] = entry
        try:
            self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
            if not entry[0].wait(timeout):
                raise McpError(f"Studio's MCP server didn't answer {method} within {timeout:.0f}s")
        finally:
            with self._lock:
                self._pending.pop(rid, None)
        msg = entry[1]
        if msg is None:
            raise McpError(self.closed or "Studio's MCP server is gone")
        if "error" in msg:
            err = msg["error"] or {}
            raise McpError(str(err.get("message") or err))
        return msg.get("result") or {}

    def tool(self, name, arguments=None, timeout=CALL_TIMEOUT):
        """A tool's content (a list of {"type": "text"|"image", ...}); its error as McpError."""
        result = self.call("tools/call", {"name": name, "arguments": arguments or {}}, timeout=timeout)
        content = result.get("content") or []
        if result.get("isError"):
            raise McpError(text_of(content) or f"{name} failed")
        return content

    def close(self):
        self._end("closed")
        try:
            self.proc.stdin.close()
        except (OSError, ValueError):
            pass
        try:
            self.proc.terminate()
            self.proc.wait(timeout=5)
        except Exception:  # noqa: BLE001 - it is going anyway
            try:
                self.proc.kill()
            except Exception:  # noqa: BLE001
                pass


def _number(v, default):
    """A request's number, else default (a missing or unreadable one)."""
    try:
        return float(v) if v not in (None, "") else default
    except (TypeError, ValueError):
        return default


def text_of(content):
    return "\n".join(str(c.get("text") or "") for c in content or [] if isinstance(c, dict) and c.get("type") == "text")


def _even(n):
    return max(2, int(n) // 2 * 2)


def _qscale(quality):
    """JPEG quality 1-100 as FFmpeg's MJPEG qscale (2 best .. 31 worst)."""
    q = max(1, min(100, int(quality or QUALITY)))
    return int(round(2 + (100 - q) * 29 / 99))


def jpeg_of(data, max_dim=None, quality=QUALITY):
    """A PNG's or JPEG's bytes as (JPEG bytes, width, height), its longer side at most max_dim. A JPEG that fits is
    kept as it is."""
    import av

    is_jpeg = data[:3] == b"\xff\xd8\xff"
    decoder = av.CodecContext.create("mjpeg" if is_jpeg else "png", "r")
    pictures = decoder.decode(av.Packet(data))
    if not pictures:
        raise ValueError("the picture couldn't be decoded")
    picture = pictures[-1]
    w, h = picture.width, picture.height
    scale = min(1.0, float(max_dim) / max(w, h)) if max_dim else 1.0
    if is_jpeg and scale >= 1.0:
        return data, w, h
    size = (_even(w * scale), _even(h * scale))
    enc = av.CodecContext.create("mjpeg", "w")
    enc.width, enc.height = size
    enc.pix_fmt = "yuvj420p"
    enc.time_base = Fraction(1, 25)
    enc.qscale = _qscale(quality)
    out = picture.reformat(width=size[0], height=size[1], format="yuvj420p")
    out.pts = 0
    packets = list(enc.encode(out)) or list(enc.encode(None))
    return b"".join(bytes(p) for p in packets), size[0], size[1]


def png_to_jpeg(answer, max_dim=None, quality=QUALITY):
    """A plugin's picture sent as a PNG ({"png": base64, ...}: StudioCaptureService's) made the JPEG every picture is
    ({"jpeg": base64, "width", "height"}), in place. Returns the answer."""
    if isinstance(answer, dict) and answer.get("png") and not answer.get("jpeg"):
        jpeg, w, h = jpeg_of(base64.b64decode(answer.pop("png")), max_dim, quality)
        answer.update(jpeg=base64.b64encode(jpeg).decode("ascii"), width=w, height=h)
    return answer


class StudioPictures:
    """The game view of one Studio (the place a plugin's hello names) through Studio's MCP server, started at the first
    picture and again if it ends."""

    def __init__(self, hello, argv, client=McpClient):
        self.place = str((hello or {}).get("project") or "")
        self.place_id = (hello or {}).get("place_id") or 0
        self.argv = argv
        self._client_type = client
        self._client = None
        self._studio = None
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    def _pick(self, content):
        text = text_of(content)
        try:
            studios = json.loads(text).get("studios") or []
        except (ValueError, AttributeError):
            raise McpError(f"Studio's MCP server listed no Studio ({text[:200]!r})") from None
        studios = [s for s in studios if isinstance(s, dict) and s.get("id")]
        if not studios:
            raise NoStudio(f"Studio's MCP server sees no Studio: {ENABLE_HINT}")
        if len(studios) == 1:
            return studios[0]["id"]
        # (a multiplayer test plays: the pictures are player 1's client's, a Studio of its own)
        for s in studios:
            if s.get("name"):
                continue
            try:
                said = text_of(self._client.tool("execute_luau", {"studio_id": s["id"], "datamodel_type": "Client",
                                                                  "code": PLAYER_ONE}, timeout=10))
            except McpError:
                continue  # (a test's server, or a Studio still starting)
            if "player 1" in said:
                return s["id"]
        studios = [s for s in studios if s.get("name")] or studios
        name = self.place.lower()
        pid = str(self.place_id) if self.place_id else None
        for s in studios:
            sname = str(s.get("name") or "")
            if sname.lower() == name or (pid and pid in sname):
                return s["id"]
        for s in studios:
            if name and str(s.get("name") or "").lower().startswith(name):
                return s["id"]
        raise McpError(f"{len(studios)} Studios are open and none has the place {self.place!r} open")

    def _connect(self):
        if self._client is None or self._client.closed:
            self._client = self._client_type(self.argv)
            self._studio = None
        deadline = time.monotonic() + STUDIO_WAIT
        while self._studio is None:
            try:
                self._studio = self._pick(self._client.tool("list_roblox_studios", {}))
            except NoStudio:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(STUDIO_LOOK)
        return self._client, self._studio

    def forget(self):
        """Pick the Studio again at the next picture (a play session started or ended: a multiplayer test's player 1
        is another Studio each time)."""
        self._studio = None

    def check(self):
        """Whether pictures can be had (Studio's MCP server answers and sees the place's Studio); McpError if not."""
        with self._lock:
            self._connect()

    def shot(self, max_dim=MAX_DIM, quality=QUALITY):
        """The game view now: (JPEG bytes, width, height)."""
        with self._lock:
            for attempt in range(2):
                client, studio = self._connect()
                try:
                    content = client.tool("screen_capture", {"capture_id": f"Autopilot_{next(self._ids)}",
                                                             "studio_id": studio}, timeout=SHOT_TIMEOUT)
                    break
                except McpError as e:
                    if "didn't answer" in str(e):
                        # (a call left hanging: the next ones would wait behind it; a fresh server instead)
                        client.close()
                        self._client = None
                        raise
                    if attempt or client.closed:
                        raise
                    self._studio = None  # (Studio restarted: its id changed; listed again once)
        for c in content:
            if isinstance(c, dict) and c.get("type") == "image" and c.get("data"):
                return jpeg_of(base64.b64decode(c["data"]), max_dim, quality)
        raise McpError(f"Studio's MCP server answered no picture ({text_of(content)[:200]!r})")

    def close(self):
        with self._lock:
            if self._client is not None:
                self._client.close()
                self._client = None


class RobloxMedia:
    """`capture` and `record` for a Roblox plugin's Link, answered here (Link.local_ops) from StudioPictures."""

    def __init__(self, link, pictures):
        self.link = link
        self.pictures = pictures
        self._stop = threading.Event()
        self._thread = None
        self.settings = {"fps": 5.0, "max_dim": MAX_DIM, "quality": QUALITY}
        self.frames = 0
        self.problem = None  # the latest reason a frame couldn't be had
        self._sessions = link.session_changes  # (a play session started or ended since: the Studio picked again)

    def _no_picture(self, why):
        from .bridge import BridgeError

        return BridgeError(409, f"no picture: {why}")

    def _game_time(self):
        t, at = self.link.game_time, self.link.game_time_at
        if not isinstance(t, (int, float)) or at is None:
            return None
        return round(float(t) + max(0.0, time.time() - at), 2)

    def _follow_sessions(self):
        if self.link.session_changes != self._sessions:
            self._sessions = self.link.session_changes
            self.pictures.forget()

    def capture(self, args):
        self._follow_sessions()
        if not self.link.session_running:
            raise self._no_picture("no game is running (start one with session start)")
        try:
            jpeg, w, h = self.pictures.shot(_number(args.get("max_dim"), MAX_DIM),
                                            _number(args.get("quality"), QUALITY))
        except (McpError, ValueError, ImportError, OSError) as e:
            raise self._no_picture(str(e)) from e
        return {"jpeg": base64.b64encode(jpeg).decode("ascii"), "width": w, "height": h, "t": self._game_time(),
                "via": "studio_mcp"}

    def record(self, args):
        action = str(args.get("action") or "start")
        if action == "stop":
            self.stop()
            return {"recording": False, "frames": self.frames}
        if action != "start":
            from .bridge import BridgeError

            raise BridgeError(400, f"unknown record action {action}")
        try:
            self.pictures.check()
        except McpError as e:
            raise self._no_picture(str(e)) from e
        fps = _number(args.get("fps"), 5.0)
        self.settings = {"fps": min(FPS[1], max(FPS[0], fps)), "max_dim": _number(args.get("max_dim"), MAX_DIM),
                         "quality": _number(args.get("quality"), QUALITY)}
        self.stop()
        self.frames = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, args=(self._stop,), name="roblox-record", daemon=True)
        self._thread.start()
        return {"recording": True, "fps": self.settings["fps"], "via": "studio_mcp"}

    def _loop(self, stop):
        n = 0
        while not stop.is_set() and not self.link.closed:
            began = time.monotonic()
            self._follow_sessions()
            if self.link.session_running:
                asked = time.time()  # (stamped as a plugin's frames are: when the picture was asked for)
                try:
                    jpeg, w, h = self.pictures.shot(self.settings["max_dim"], self.settings["quality"])
                    n += 1
                    self.frames += 1
                    self.problem = None
                    self.link.feed({"type": "frame", "n": n, "t": self._game_time(), "wall": asked,
                                    "jpeg": base64.b64encode(jpeg).decode("ascii"), "width": w, "height": h})
                except Exception as e:  # noqa: BLE001 - a picture missed, the recording goes on
                    if str(e) != self.problem:
                        print(f"note: no picture of the game: {e}", file=sys.stderr)
                    self.problem = str(e)
                    stop.wait(2.0)
                    continue
            stop.wait(max(0.0, 1.0 / self.settings["fps"] - (time.monotonic() - began)))

    def stop(self):
        self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=CALL_TIMEOUT + 5)
        self._thread = None

    def close(self):
        self.stop()
        self.pictures.close()


def attach(link, argv=None, client=McpClient):
    """Answer `capture` and `record` for a Roblox plugin's Link from Studio's MCP server, when one is installed here and
    the plugin has no picture of its own: its hello's features get "media" (what autopilot/media.py goes by) and
    "media_via": "studio_mcp". Returns the RobloxMedia, or None."""
    hello = link.hello if isinstance(link.hello, dict) else None
    if hello is None or str(hello.get("engine") or "").lower() != "roblox":
        return None
    features = hello.get("features")
    if not isinstance(features, list):
        features = hello["features"] = []
    if "media" in features:
        return None  # (the plugin has pictures of its own: StudioCaptureService)
    argv = argv or command()
    if not argv:
        return None
    media = RobloxMedia(link, StudioPictures(hello, argv, client))
    link.local_ops["capture"] = media.capture
    link.local_ops["record"] = media.record
    features.append("media")
    hello["media_via"] = "studio_mcp"
    return media
