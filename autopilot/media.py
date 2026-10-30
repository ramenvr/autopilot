"""What the game showed during a run: a recording and stills, kept with the run's data (docs/run-data.md).

The plugin captures the game's view (docs/protocol.md, "Recording, stills and usage"): `record` makes it send a `frame`
(a JPEG with the game time it shows) a few times a second while the game runs; `capture` answers one still.

    Recorder   the run's recording, one video per harness: while episode n plays (begin(n) to end(n), the
               session's run_harness) its frames go into an H.264 MP4 (PyAV), media/recording-<n>.mp4, and
               media/frames-<n>.jsonl says which game time and wall time each video frame shows (one line per frame,
               in order). Frames outside every episode (the game running while the agent probes it) aren't kept.
               An episode's video ends on a picture taken as it ended, held on screen for HOLD_MS. Without PyAV the
               JPEGs are kept as they came, media/frames-<n>/<k>.jpg. For a plugin that can't play an MP4 (Godot),
               also media/flipbook-<n>/: small JPEGs at a fixed rate of the video's time. The latest frames, in an
               episode or not, are kept in memory too, for describe_frames (autopilot/vision.py). The run's report
               shows the last episode's video: the one whose play settled the result.
    still()    one still: media/stills/<seq>-<reason>.jpg, with a line in media/stills.jsonl.

Both only when the plugin says it can capture (hello `features` has "media"). Nothing here may slow the game or the
run: the recording is encoded in a thread of its own, frames that pile up are dropped, and every failure is logged and
otherwise ignored.
"""

import base64
import itertools
import json
import os
import queue
import threading
import time
from fractions import Fraction

FPS = 5  # frames a second the plugin is asked for
MAX_DIM = 960  # the recording's longer side, at most
QUALITY = 75  # the plugin's JPEG quality, 1-100
STILL_DIM = 1920
STILL_QUALITY = 90
QUEUE = 64  # frames waiting to be encoded, at most (more are dropped: the encoder fell behind)
FOLLOW_EVERY = 1.0  # seconds between looks at the plugin's connection while recording (a reconnect: asked again)
RETRY_START_EVERY = 5.0  # seconds between asks when the plugin refused to start recording (no picture yet)
CRF = 28  # x264's quality (lower: better, bigger)
HOLD_MS = 1000  # how long an episode's last picture stays on screen at the end of its video
MEDIA = "media"
# The recording as JPEG frames, for a plugin that can't play an MP4 (Godot's player takes only Ogg Theora): frames a
# second of the video's time, their longer side at most, JPEG quality (2-31: lower is better)
FLIPBOOK = "flipbook"
FLIPBOOK_FPS = 2
FLIPBOOK_DIM = 640
FLIPBOOK_QSCALE = 6


def can_capture(link):
    """Whether the plugin says it can capture the game's picture."""
    hello = getattr(link, "hello", None) or {}
    return "media" in (hello.get("features") or [])


def _even(n):
    return max(2, int(n) // 2 * 2)


class Encoder:
    """Frames (JPEG bytes, wall time) into an H.264 MP4 whose frame times follow the wall clock (variable frame
    rate). The first frame's size is the video's: later frames of another size are scaled to it."""

    def __init__(self, path, fps=FPS, flipbook=None):
        import av

        self.av = av
        self.container = av.open(path, mode="w")
        self.stream = None
        self.fps = fps
        self.decoder = av.CodecContext.create("mjpeg", "r")
        self.wall0 = None
        self.last_pts = -1
        self.size = None
        self.frames = 0
        self.picture = None  # (the last picture encoded, for hold())
        self.flipbook = flipbook  # (a Flipbook: the video's pictures as JPEGs too)

    def _open(self, width, height):
        self.size = (_even(width), _even(height))
        s = self.container.add_stream("libx264", rate=self.fps)
        s.width, s.height = self.size
        s.pix_fmt = "yuv420p"
        s.time_base = Fraction(1, 1000)
        s.codec_context.time_base = Fraction(1, 1000)
        # (no B-frames: with them each frame is decoded before its time, and the MP4's media length, counted in decode
        # times, comes out frames short of its last picture: a player going by it never shows the video's end)
        s.options = {"preset": "veryfast", "crf": str(CRF), "g": str(self.fps * 2), "bf": "0"}
        self.stream = s

    def add(self, jpeg, wall):
        """Encode one frame. Returns whether it went into the video (False: it couldn't be decoded, or its time isn't
        after the last frame's)."""
        pictures = self.decoder.decode(self.av.Packet(jpeg))  # (a JPEG is a whole picture: one packet)
        if not pictures:
            return False
        picture = pictures[-1]
        if self.stream is None:
            self._open(picture.width, picture.height)
            self.wall0 = wall
        pts = int(round((wall - self.wall0) * 1000))
        if pts <= self.last_pts:
            return False
        self._put(picture, pts)
        self.frames += 1
        return True

    def _put(self, picture, pts):
        frame = picture.reformat(width=self.size[0], height=self.size[1], format="yuv420p")
        frame.pts, frame.time_base = pts, Fraction(1, 1000)
        for packet in self.stream.encode(frame):
            self.container.mux(packet)
        self.last_pts = pts
        self.picture = picture
        if self.flipbook is not None:
            try:
                self.flipbook.add(picture, pts)
            except Exception as e:  # noqa: BLE001 - the video goes on without it
                print(f"note: no more flipbook frames ({type(e).__name__}: {e})")
                self.flipbook = None

    def hold(self, ms):
        """The last picture on screen ms longer: the same picture again ms after it. (An MP4's last frame lasts no time
        at all: without this, the video's last moment, the episode's end, would flash by unseen.)"""
        if self.picture is not None and ms > 0:
            self._put(self.picture, self.last_pts + int(ms))

    def close(self):
        try:
            if self.flipbook is not None:
                self.flipbook.close()
        except Exception as e:  # noqa: BLE001
            print(f"note: the flipbook's last frames are missing ({type(e).__name__}: {e})")
        try:
            if self.stream is not None:
                for packet in self.stream.encode(None):
                    self.container.mux(packet)
        finally:
            self.container.close()


class Flipbook:
    """The recording's pictures as small JPEGs, folder/000000.jpg, 000001.jpg...: frame i shows the video's time
    i / fps seconds: the newest picture by then (a time with no new picture, the game paused, shows the last one).
    The last picture gets a frame of its own."""

    def __init__(self, folder, fps=FLIPBOOK_FPS, max_dim=FLIPBOOK_DIM):
        import av

        self.av = av
        self.folder, self.fps, self.max_dim = folder, fps, max_dim
        os.makedirs(folder, exist_ok=True)
        self.encoder = None
        self.size = None
        self.next = 0  # the next frame's number
        self.newest = None  # the newest picture (scaled), and its time in ms: shown by the frames up to the next one's
        self.newest_pts = 0
        self.jpeg = None  # (the newest picture as a JPEG, once encoded)

    def _open(self, width, height):
        scale = min(1.0, self.max_dim / max(width, height))
        self.size = (_even(width * scale), _even(height * scale))
        c = self.av.CodecContext.create("mjpeg", "w")
        c.width, c.height = self.size
        c.pix_fmt = "yuvj420p"
        c.time_base = Fraction(1, self.fps)
        c.qscale = FLIPBOOK_QSCALE
        self.encoder = c

    def _write_newest(self, upto):
        """The newest picture as frames next..upto-1."""
        if self.newest is None or upto <= self.next:
            return
        if self.jpeg is None:
            self.newest.pts = self.next
            self.jpeg = b"".join(bytes(p) for p in self.encoder.encode(self.newest))
        while self.next < upto:
            with open(os.path.join(self.folder, f"{self.next:06d}.jpg"), "wb") as f:
                f.write(self.jpeg)
            self.next += 1

    def add(self, picture, pts_ms):
        """A picture of the video at pts_ms (after the last one's): the frames before its time get the one before."""
        if self.encoder is None:
            self._open(picture.width, picture.height)
        self._write_newest(-(-pts_ms * self.fps // 1000))  # (the frames whose time is before pts_ms)
        self.newest = picture.reformat(width=self.size[0], height=self.size[1], format="yuvj420p")
        self.newest_pts, self.jpeg = pts_ms, None

    def close(self):
        """The newest picture's frames: up to its time, and one at least."""
        self._write_newest(max(self.newest_pts * self.fps // 1000, self.next) + 1)


class Clip:
    """One episode's video (Recorder): media/recording-<n>.mp4 and media/frames-<n>.jsonl, made at its first frame;
    the JPEGs as they came, media/frames-<n>/, when there is no encoder."""

    def __init__(self, folder, n, fps, flipbook):
        self.folder, self.n, self.fps, self.flipbook = folder, n, fps, flipbook
        self.index = None  # (frames-<n>.jsonl, opened at the first frame: an episode with none leaves no file)
        self.encoder = None
        self.tried = False  # (whether the encoder was tried: once, at the first frame)
        self.problem = None  # (why it is JPEGs, not a video)
        self.kept = 0
        self.wall0 = self.wall_end = None  # (its first and last frame's wall time: the video's time 0 is wall0)

    @property
    def video(self):
        return os.path.join(self.folder, f"recording-{self.n}.mp4")

    def keep(self, jpeg, line):
        """A frame of the episode (line: its game and wall time, as frames-<n>.jsonl says it)."""
        wall = line["wall"]
        if not self.tried:
            self.tried = True
            try:
                flipbook = None
                if self.flipbook:
                    try:
                        flipbook = Flipbook(os.path.join(self.folder, f"{FLIPBOOK}-{self.n}"))
                    except Exception as e:  # noqa: BLE001 - the video without it
                        print(f"note: no flipbook ({type(e).__name__}: {e})")
                self.encoder = Encoder(self.video, self.fps, flipbook)
            except Exception as e:  # noqa: BLE001 - no PyAV, or no H.264 encoder: the JPEGs are kept as they are
                print(f"note: recording as JPEG frames ({type(e).__name__}: {e})")
                self.problem = (f"the video couldn't be made ({type(e).__name__}: {e}): its pictures are in "
                                f"{os.path.join(self.folder, f'frames-{self.n}')}")
        line = {"frame": self.kept, **line}
        if self.encoder is not None:
            if not self.encoder.add(jpeg, wall):
                return False
            line["pts_ms"] = self.encoder.last_pts
            self.wall0 = self.encoder.wall0
        else:
            frames = os.path.join(self.folder, f"frames-{self.n}")
            os.makedirs(frames, exist_ok=True)
            name = f"{self.kept:06d}.jpg"
            with open(os.path.join(frames, name), "wb") as f:
                f.write(jpeg)
            line["file"] = name  # (in the episode's frames folder: media/frames-<n>/, episodes/<n>/frames/ as sent)
            self.wall0 = self.wall0 if self.wall0 is not None else wall
        self.wall_end = wall
        if self.index is None:
            self.index = open(os.path.join(self.folder, f"frames-{self.n}.jsonl"), "a", encoding="utf-8")
        self.index.write(json.dumps(line) + "\n")
        self.index.flush()
        self.kept += 1
        return True

    def close(self):
        try:
            if self.encoder is not None:
                if self.encoder.frames:
                    self.encoder.hold(HOLD_MS)
                self.encoder.close()
        finally:
            if self.index is not None:
                self.index.close()

    def seconds(self):
        return round(max(self.encoder.last_pts, 0) / 1000, 3) if self.encoder is not None else None

    def summary(self):
        """What run.json says of it: its frames, its first and last frame's wall time (the video's time 0 is wall0) and
        its length (None: JPEGs, not a video)."""
        made = self.encoder is not None and self.encoder.frames
        return {"episode": self.n, "frames": self.kept, "wall0": self.wall0, "wall_end": self.wall_end,
                "seconds": self.seconds() if made else None}


class Recorder:
    """The run's recording, from `record start` to stop(), kept episode by episode (begin(n) to end(n)). Use as
    `with Recorder(link, folder):`. flipbook: keep its pictures as JPEGs too (media/flipbook-<n>/, Flipbook), for a
    plugin that can't play the MP4."""

    def __init__(self, link, folder, fps=FPS, max_dim=MAX_DIM, quality=QUALITY, flipbook=False):
        from .vision import Frames

        self.link = link
        self.folder = os.path.join(folder, MEDIA)
        self.fps, self.max_dim, self.quality = fps, max_dim, quality
        self.flipbook = flipbook
        self.frames = Frames()  # (the latest frames, for describe_frames: autopilot/vision.py)
        self.queue = queue.Queue(maxsize=QUEUE)
        self.thread = None
        self.clip = None  # the episode being recorded (the encoder's thread's: begin and end reach it in order)
        self.clips = []  # the episodes recorded, in order
        self.received = self.dropped = self.kept = 0
        self.outside = 0  # frames that came while no episode played (not kept but for describe_frames)
        self.started = False
        self._listening = None
        self.problem = None  # why the game isn't recorded (the start refused, no encoder...), for missing()
        self._stopping = threading.Event()
        self._follower = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()
        return False

    def start(self):
        if not can_capture(self.link):
            self.problem = "the game can't be recorded here: the plugin can't capture its picture (no rendering)"
            return False
        os.makedirs(self.folder, exist_ok=True)
        self._listen()
        self.thread = threading.Thread(target=self._encode, name="recorder", daemon=True)
        self.thread.start()
        self._ask_start()
        self._follower = threading.Thread(target=self._follow, name="recorder-follow", daemon=True)
        self._follower.start()
        return self.started

    def _connection(self):
        """The plugin's connection now (a LiveLink's newest) and how often it has resumed."""
        current = getattr(self.link, "current", self.link)
        return current, getattr(current, "resumes", 0)

    def _listen(self):
        """Take the frames of the plugin's connection now (and no other's)."""
        listeners = getattr(self._connection()[0], "frame_listeners", None)
        if listeners is self._listening:
            return
        if self._listening is not None and self.on_frame in self._listening:
            self._listening.remove(self.on_frame)
        self._listening = None
        if isinstance(listeners, list):
            listeners.append(self.on_frame)
            self._listening = listeners

    def _ask_start(self, quiet=False):
        """`record start`; True once the plugin records."""
        try:
            answer = self.link.request("record", {"action": "start", "fps": self.fps, "max_dim": self.max_dim,
                                                  "quality": self.quality})
        except Exception as e:  # (no picture: a headless game, a hidden window; an old plugin)
            if not quiet:
                print(f"note: the game isn't recorded: {e}")
            if not self.started:
                self.problem = f"recording couldn't start: {e}"
            return False
        self.started = True
        self.problem = None
        print(f"Recording the game: {answer}")
        return True

    def _follow(self):
        """Record on through the run: a plugin ends its recording with its connection (a reconnect, Unity's script
        reload), so it is asked again on the new one; and a start the plugin refused (no picture yet: the game not
        running, its window hidden) is asked again now and then."""
        seen = self._connection()
        tried = time.monotonic()
        while not self._stopping.wait(FOLLOW_EVERY):
            now = self._connection()
            if now[0] is not seen[0] or now[1] != seen[1]:
                seen = now
                self._listen()
                if self._ask_start(quiet=True):
                    print("Recording again: the plugin reconnected")
            elif not self.started and time.monotonic() - tried >= RETRY_START_EVERY:
                tried = time.monotonic()
                self._ask_start(quiet=True)

    def set_fps(self, fps):
        """Record at fps frames a second from now (a `record start` while recording restarts it with the new settings);
        the reconnects after this ask for it too."""
        self.fps = fps
        if self.started:
            self._ask_start(quiet=True)

    def on_frame(self, frame):
        """A `frame` from the plugin (the bridge's thread: never blocks)."""
        self.received += 1
        try:
            self.queue.put_nowait(frame)
        except queue.Full:
            self.dropped += 1

    def begin(self, n):
        """Episode n starts playing: its frames from now on go into its own video (the session's run_harness)."""
        if self.thread is not None:
            self._mark(("begin", n))

    def end(self, n):
        """Episode n has ended: its video ends on a picture of the game taken now, as the episode left it (the last
        frame sent may be up to a frame's time before the end), held on screen for HOLD_MS."""
        if self.thread is None:
            return
        last = None
        if self.started:
            try:
                asked = time.time()  # (stamped as frames are: when the picture was asked for)
                shot = self.link.request("capture", {"max_dim": self.max_dim, "quality": self.quality}, timeout=10)
                if shot.get("jpeg"):
                    last = {**shot, "n": "end", "wall": asked}
            except Exception as e:  # noqa: BLE001 - the video ends on its last frame
                print(f"note: no picture of episode {n}'s end: {type(e).__name__}: {e}")
        self._mark(("end", n, last))

    def _mark(self, mark):
        try:
            self.queue.put(mark, timeout=10)  # (never dropped: the encoder's thread takes them in order)
        except queue.Full:
            print(f"note: the recording missed episode {mark[1]}'s {mark[0]} (the encoder fell behind)")

    def _encode(self):
        while True:
            item = self.queue.get()
            if item is None:
                break
            try:
                if isinstance(item, tuple):
                    self._episode(*item)
                else:
                    self._keep(item)
            except Exception as e:  # noqa: BLE001 - one bad frame never ends the recording
                print(f"note: a frame couldn't be recorded: {type(e).__name__}: {e}")
        self._close_clip()

    def _episode(self, what, n, last=None):
        if what == "begin":
            self._close_clip()  # (one whose end never came)
            self.clip = Clip(self.folder, n, self.fps, self.flipbook)
        elif self.clip is not None and self.clip.n == n:
            if last is not None:
                self._keep(last, final=True)
            self._close_clip()

    def _close_clip(self):
        clip, self.clip = self.clip, None
        if clip is None:
            return
        try:
            clip.close()
        except Exception as e:  # noqa: BLE001
            print(f"note: episode {clip.n}'s video didn't close cleanly: {type(e).__name__}: {e}")
        self.clips.append(clip)
        if clip.problem:
            self.problem = clip.problem

    def _keep(self, frame, final=False):
        jpeg = base64.b64decode(frame.get("jpeg") or "")
        if not jpeg:
            return
        wall = float(frame.get("wall") or time.time())
        t = frame.get("t")
        if not final:
            self.frames.add(jpeg, float(t) if isinstance(t, (int, float)) else None, wall, frame.get("width"),
                            frame.get("height"))
        if self.clip is None:
            self.outside += 1
            return
        line = {"n": frame.get("n"), "t": t, "wall": round(wall, 3), "width": frame.get("width"),
                "height": frame.get("height")}
        if final:
            line["final"] = True  # (the picture taken as the episode ended)
        if self.clip.keep(jpeg, line):
            self.kept += 1

    def stop(self):
        self._stopping.set()
        if self._follower is not None:
            self._follower.join(timeout=30)
        if self.started:
            try:
                self.link.request("record", {"action": "stop"}, timeout=10)
            except Exception:
                pass
        if self._listening is not None and self.on_frame in self._listening:
            self._listening.remove(self.on_frame)
        if self.thread is not None:
            self.queue.put(None)
            self.thread.join(timeout=60)
            print(f"Recording: {self.kept} frames kept in {len(self.clips)} episode videos, of {self.received} "
                  f"received ({self.outside} outside the episodes, {self.dropped} dropped)")

    def last_clip(self):
        """The last episode with a video: the one whose play settled the run's result (None: none has one)."""
        return next((c for c in reversed(self.clips) if c.encoder is not None and c.encoder.frames), None)

    def summary(self):
        """What run.json says of the recording: its counts and each episode's video."""
        return {"frames": self.kept, "received": self.received, "dropped": self.dropped, "outside": self.outside,
                "episodes": [c.summary() for c in self.clips if c.kept]}

    def shown(self):
        """The recording as the plugin's report shows it (the result's `recording`, docs/protocol.md), once stopped: the
        last episode's video, {"video": its path, "seconds", "episode": its number, and with a flipbook "frames": its
        folder, "frames_fps"}; None when there is no video."""
        clip = self.last_clip()
        if clip is None:
            return None
        encoder = clip.encoder
        shown = {"video": os.path.abspath(clip.video), "seconds": clip.seconds(), "episode": clip.n}
        if encoder.flipbook is not None and encoder.flipbook.next:
            shown.update(frames=os.path.abspath(encoder.flipbook.folder), frames_fps=encoder.flipbook.fps)
        return shown

    def missing(self):
        """Why the run has no video (the result's `no_recording`), once stopped; None when it has one."""
        if self.shown() is not None:
            return None
        if self.problem:
            return self.problem
        if not self.received:
            return "no picture came from the game while it played"
        if not self.clips:
            return "no harness played the game, so there was nothing to record"
        return f"none of the game's {self.received} pictures could be recorded"


_seq = itertools.count(1)
_seq_lock = threading.Lock()


def still(link, folder, reason, extra=None, t=None):
    """One still of the game's view now, saved in folder/media/stills/. Returns its path relative to folder, or None
    (no picture, or the plugin can't capture). Never raises."""
    if not can_capture(link):
        return None
    try:
        asked = time.time()  # (the plugin captures the frame drawn next: the still's time, as frames are stamped)
        shot = link.request("capture", {"max_dim": STILL_DIM, "quality": STILL_QUALITY}, timeout=15)
        jpeg = base64.b64decode(shot.get("jpeg") or "")
        if not jpeg:
            return None
        with _seq_lock:
            n = next(_seq)
        stills = os.path.join(folder, MEDIA, "stills")
        os.makedirs(stills, exist_ok=True)
        safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in str(reason))[:40]
        name = f"{time.strftime('%H%M%S')}-{n:04d}-{safe}.jpg"
        with open(os.path.join(stills, name), "wb") as f:
            f.write(jpeg)
        line = {"file": f"stills/{name}", "reason": reason, "t": shot.get("t", t), "wall": round(asked, 3),
                "width": shot.get("width"), "height": shot.get("height"), **(extra or {})}
        with _seq_lock, open(os.path.join(folder, MEDIA, "stills.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(line, default=str) + "\n")
        return f"{MEDIA}/stills/{name}"
    except Exception as e:  # noqa: BLE001
        print(f"note: no still ({reason}): {type(e).__name__}: {e}")
        return None


def still_later(link, folder, reason, extra=None, t=None):
    """still() in a thread of its own: the caller (an episode playing) never waits for it."""
    threading.Thread(target=still, args=(link, folder, reason, extra, t), name="still", daemon=True).start()

