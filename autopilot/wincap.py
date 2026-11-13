"""Pictures of a window straight from Windows, for an editor that hands out its game view slowly.

Roblox Studio's MCP server takes about a second for one `screen_capture` (autopilot/studio_mcp.py), so a recording
asked for at 5 frames a second gets fewer than one, and a look at the last seconds of play (autopilot/watch.py) sees
three or four pictures where a death takes a tenth of a second. Windows itself draws any window into a bitmap in a
few hundredths of a second (PrintWindow with PW_RENDERFULLCONTENT: the composited picture, also of a window behind
others; not of a minimized one), with no work for the editor's own threads. This module finds the editor's window,
the child window that is the game view (the one the size of a picture the editor's own tool gave), and grabs it.

Windows only (ctypes, no other dependency); everywhere else, and whenever anything here fails, callers keep the
editor's own pictures. AUTOPILOT_WINDOW_SHOTS=0 turns it off.
"""

import ctypes
import os
import sys
from fractions import Fraction

PW_RENDERFULLCONTENT = 2  # (the composited picture: what a GPU-drawn view shows)
SAME_SIZE = 3  # pixels a child window's size may differ from the editor's own picture's and still be the game view
SAME_SHAPE = 0.012  # ... or, when its pictures are scaled, how far its width / height may be off
GRID = (16, 12)  # the coarse picture two shots are compared on
ALIKE = 48.0  # mean difference (0-255) on it under which they show the same view (the game moves between them)


def enabled():
    return sys.platform == "win32" and (os.environ.get("AUTOPILOT_WINDOW_SHOTS") or "1").strip() != "0"


def _api():
    from ctypes import wintypes as wt

    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    user32.GetDC.restype = wt.HDC
    user32.GetDC.argtypes = [wt.HWND]
    user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
    user32.PrintWindow.argtypes = [wt.HWND, wt.HDC, wt.UINT]
    user32.IsWindow.argtypes = user32.IsIconic.argtypes = user32.IsWindowVisible.argtypes = [wt.HWND]
    gdi32.CreateCompatibleDC.restype = wt.HDC
    gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
    gdi32.CreateCompatibleBitmap.restype = wt.HBITMAP
    gdi32.CreateCompatibleBitmap.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
    gdi32.SelectObject.restype = wt.HGDIOBJ
    gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
    gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
    gdi32.DeleteDC.argtypes = [wt.HDC]
    gdi32.GetDIBits.argtypes = [wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT, ctypes.c_void_p, ctypes.c_void_p, wt.UINT]
    return user32, gdi32, wt


def _aware(user32):
    """This thread measures windows in real pixels (a scaled display otherwise reports them smaller than drawn)."""
    try:
        user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))  # (per monitor, v2)
    except (AttributeError, OSError):
        pass


def _text(user32, hwnd):
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def windows(title_has, also=None):
    """The visible top-level windows whose title has title_has (and `also`, when given): their handles."""
    user32, _, wt = _api()
    found = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def each(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            title = _text(user32, hwnd)
            if title_has in title and (not also or also.lower() in title.lower()):
                found.append(hwnd)
        return True

    user32.EnumWindows(each, 0)
    return found


def _client_origin(user32, wt, hwnd):
    p = wt.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(p))
    return p.x, p.y


def _client_size(user32, wt, hwnd):
    r = wt.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(r))
    return r.right, r.bottom


def children(top):
    """The visible child windows of a top-level window: [(handle, x, y, width, height)], x and y from the top
    window's client corner, in real pixels; the deepest (a view inside its frames) last."""
    user32, _, wt = _api()
    _aware(user32)
    ox, oy = _client_origin(user32, wt, top)
    out = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def each(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            w, h = _client_size(user32, wt, hwnd)
            x, y = _client_origin(user32, wt, hwnd)
            if w > 0 and h > 0:
                out.append((hwnd, x - ox, y - oy, w, h))
        return True

    user32.EnumChildWindows(top, each, 0)
    return out


def view_of(top, width, height):
    """The child window of `top` that is a view of this size (the size of a picture the editor's own tool gave of the
    game view), else of this shape (pictures scaled by the display's scaling), the largest then; the last such in the
    window's own order (the view inside its frames). None when no child fits."""
    kids = children(top)
    same = [k for k in kids if abs(k[3] - width) <= SAME_SIZE and abs(k[4] - height) <= SAME_SIZE]
    if same:
        return same[-1][0]
    shape = width / float(height)
    like = [k for k in kids if abs(k[3] / float(k[4]) - shape) <= SAME_SHAPE and k[3] * k[4] >= 200 * 200]
    if not like:
        return None
    # (a view sits in frames a few pixels larger and of nearly its shape: the innermost of the largest ones)
    area = max(k[3] * k[4] for k in like)
    return [k for k in like if k[3] * k[4] >= 0.96 * area][-1][0]


class Gone(Exception):
    """The window can't be drawn now: closed, minimized, hidden or without a size."""


def grab(top, view=None):
    """(BGRA bytes, width, height) of a top-level window as Windows composites it, cut to its child `view` when given.
    Raises Gone."""
    user32, gdi32, wt = _api()
    _aware(user32)
    if not user32.IsWindow(top) or user32.IsIconic(top) or not user32.IsWindowVisible(top):
        raise Gone("the window is closed, hidden or minimized")
    w, h = _client_size(user32, wt, top)
    if w <= 0 or h <= 0:
        raise Gone("the window has no size")
    cut = (0, 0, w, h)
    if view is not None:
        if not user32.IsWindow(view) or not user32.IsWindowVisible(view):
            raise Gone("the view is gone")
        ox, oy = _client_origin(user32, wt, top)
        vx, vy = _client_origin(user32, wt, view)
        vw, vh = _client_size(user32, wt, view)
        x0, y0 = max(0, vx - ox), max(0, vy - oy)
        x1, y1 = min(w, vx - ox + vw), min(h, vy - oy + vh)
        if x1 - x0 < 16 or y1 - y0 < 16:
            raise Gone("the view has no size")
        cut = (x0, y0, x1 - x0, y1 - y0)

    class Header(ctypes.Structure):
        _fields_ = [("size", wt.DWORD), ("width", wt.LONG), ("height", wt.LONG), ("planes", wt.WORD),
                    ("bits", wt.WORD), ("compression", wt.DWORD), ("image", wt.DWORD), ("xppm", wt.LONG),
                    ("yppm", wt.LONG), ("used", wt.DWORD), ("important", wt.DWORD)]

    dc = user32.GetDC(top)
    mem = gdi32.CreateCompatibleDC(dc)
    bitmap = gdi32.CreateCompatibleBitmap(dc, w, h)
    old = gdi32.SelectObject(mem, bitmap)
    try:
        # (flag 1, client only, added: the title bar and borders are never the game)
        if not user32.PrintWindow(top, mem, PW_RENDERFULLCONTENT | 1):
            raise Gone("Windows couldn't draw the window")
        x, y, cw, ch = cut
        head = Header()
        head.size, head.width, head.height, head.planes, head.bits = ctypes.sizeof(Header), w, -h, 1, 32
        rows = ctypes.create_string_buffer(w * ch * 4)
        gdi32.SelectObject(mem, old)  # (GetDIBits wants the bitmap out of any device context)
        # (rows count from the bottom for GetDIBits' start line even in a top-down bitmap)
        got = gdi32.GetDIBits(mem, bitmap, h - y - ch, ch, rows, ctypes.byref(head), 0)
        if got != ch:
            raise Gone("Windows gave no pixels")
        raw = rows.raw
        if cw != w:
            raw = b"".join(raw[r * w * 4 + x * 4:r * w * 4 + (x + cw) * 4] for r in range(ch))
        return raw, cw, ch
    finally:
        gdi32.SelectObject(mem, old)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(top, dc)


def _frame(bgra, width, height):
    import av

    frame = av.VideoFrame(width, height, "bgra")
    plane = frame.planes[0]
    if plane.line_size == width * 4:
        plane.update(bgra)
    else:  # (a padded line: row by row)
        pad = b"\0" * (plane.line_size - width * 4)
        plane.update(b"".join(bgra[r * width * 4:(r + 1) * width * 4] + pad for r in range(height)))
    return frame


def _even(n):
    return max(2, int(n) // 2 * 2)


def jpeg(bgra, width, height, max_dim=None, quality=80):
    """BGRA pixels as (JPEG bytes, width, height), the longer side at most max_dim."""
    import av

    scale = min(1.0, float(max_dim) / max(width, height)) if max_dim else 1.0
    size = (_even(width * scale), _even(height * scale))
    enc = av.CodecContext.create("mjpeg", "w")
    enc.width, enc.height = size
    enc.pix_fmt = "yuvj420p"
    enc.time_base = Fraction(1, 25)
    enc.qscale = max(2, min(31, round(31 - (max(1, min(100, quality)) / 100.0) * 29)))
    out = _frame(bgra, width, height).reformat(width=size[0], height=size[1], format="yuvj420p")
    out.pts = 0
    packets = list(enc.encode(out)) or list(enc.encode(None))
    return b"".join(bytes(p) for p in packets), size[0], size[1]


def coarse(frame):
    """A decoded picture (an av.VideoFrame) as GRID's grey values, for telling whether two show the same view."""
    small = frame.reformat(width=GRID[0], height=GRID[1], format="gray")
    plane = small.planes[0]
    data = bytes(plane)
    return [data[r * plane.line_size + c] for r in range(GRID[1]) for c in range(GRID[0])]


def alike(a, b):
    """Whether two coarse pictures show the same view: close on average, and neither blank where the other isn't."""
    if not a or len(a) != len(b):
        return False
    if (max(a) - min(a) < 12) != (max(b) - min(b) < 12):
        return False
    return sum(abs(x - y) for x, y in zip(a, b)) / float(len(a)) <= ALIKE


class View:
    """The game view of an editor's window, grabbed from Windows: found from one picture the editor's own tool gave
    (its size names the child window; the first grab must show the same view as it), then grabbed many times a second.
    `find` returns None when it can't be told for sure (no such window, several, no child of the picture's size, a
    grab that shows something else): the editor's own pictures stay."""

    def __init__(self, top, view):
        self.top, self.view = top, view

    @classmethod
    def find(cls, title_has, also, picture):
        """picture: the editor's own picture of the game view, as JPEG or PNG bytes."""
        if not enabled():
            return None
        try:
            import av

            tops = windows(title_has, also)
            if len(tops) != 1:
                return None
            is_jpeg = picture[:3] == b"\xff\xd8\xff"
            decoded = av.CodecContext.create("mjpeg" if is_jpeg else "png", "r").decode(av.Packet(picture))
            if not decoded:
                return None
            theirs = decoded[-1]
            view = view_of(tops[0], theirs.width, theirs.height)
            if view is None:
                return None
            found = cls(tops[0], view)
            raw, w, h = grab(found.top, found.view)
            return found if alike(coarse(theirs), coarse(_frame(raw, w, h))) else None
        except Exception:  # noqa: BLE001 - any doubt: the editor's own pictures
            return None

    def shot(self, max_dim=None, quality=80):
        """(JPEG bytes, width, height) now. Raises Gone (the caller goes back to the editor's own pictures)."""
        raw, w, h = grab(self.top, self.view)
        return jpeg(raw, w, h, max_dim, quality)


# -- an editor that slows down behind other windows ----------------------------------------------------------------
#
# Roblox Studio draws 60 frames a second while its window is the one in front and 15 while it isn't (measured: a
# frame's work was 9 ms either way), and a game played at 15 frames a second moves a third of a metre between two
# frames: jumps land late, timed hazards are met a frame off. The player of an Autopilot run is not the user, whose
# own window stays in front, so the editor is told it is active (the messages Windows sends a window that comes to the
# front) without being brought there: it then draws at full rate behind the user's window. Told again every second
# while a game plays (a real switch of windows undoes it), and told it is inactive when play ends.
# AUTOPILOT_STUDIO_AWAKE=0 turns it off.

WM_ACTIVATE, WM_ACTIVATEAPP, WM_NCACTIVATE = 0x0006, 0x001C, 0x0086


def awake_enabled():
    return sys.platform == "win32" and (os.environ.get("AUTOPILOT_STUDIO_AWAKE") or "1").strip() != "0"


def wake(title_has, also=None, on=True):
    """Tell the windows of that title they are active (on) or not, unless one is the window in front already (it
    knows). Returns how many were told."""
    user32, _, wt = _api()
    user32.GetForegroundWindow.restype = wt.HWND
    user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
    front = user32.GetForegroundWindow()
    told = 0
    for hwnd in windows(title_has, also):
        if hwnd == front:
            continue
        for message in (WM_NCACTIVATE, WM_ACTIVATEAPP, WM_ACTIVATE):
            user32.PostMessageW(hwnd, message, 1 if on else 0, 0)
        told += 1
    return told


class Awake:
    """Keeps an editor's window drawing at full rate while a game plays on a link (a thread; ends with the link)."""

    EVERY = 1.0

    def __init__(self, link, title_has, also=None, tell=wake):
        import threading

        self.link, self.title_has, self.also, self.tell = link, title_has, also, tell
        self.on = False
        self.told = 0
        self._thread = threading.Thread(target=self._loop, name="editor-awake", daemon=True)
        self._thread.start()

    def step(self):
        playing = bool(getattr(self.link, "session_running", False)) and not getattr(self.link, "closed", False)
        if playing:
            self.told += self.tell(self.title_has, self.also, True)
            self.on = True
        elif self.on:
            self.tell(self.title_has, self.also, False)
            self.on = False

    def _loop(self):
        import time

        while not getattr(self.link, "closed", False):
            try:
                self.step()
            except Exception:  # noqa: BLE001 - never the run's problem
                pass
            time.sleep(self.EVERY)
        try:
            self.step()  # (the link closed: the editor is told it is inactive again)
        except Exception:  # noqa: BLE001
            pass
