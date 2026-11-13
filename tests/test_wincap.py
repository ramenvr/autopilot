"""Pictures of the editor's window from Windows itself and the editor kept at full frame rate (autopilot/wincap.py),
and Studio's picture source using them (autopilot/studio_mcp.py)."""

import sys
import types

import pytest

from autopilot import studio_mcp, wincap


def test_two_coarse_pictures_are_told_alike_or_not():
    a = [10 * (i % 16) for i in range(16 * 12)]
    assert wincap.alike(a, [v + 20 for v in a])  # (the game moved a little)
    assert not wincap.alike(a, [0] * len(a))  # (a blank grab is never the game view)
    assert not wincap.alike(a, [255 - v for v in a])
    assert not wincap.alike(a, a[:10])


def test_the_editor_is_told_it_is_active_while_a_game_plays_and_inactive_after():
    told = []
    link = types.SimpleNamespace(session_running=False, closed=True)  # (closed: its thread ends at once)
    awake = wincap.Awake(link, "Roblox Studio", "Place", tell=lambda title, also, on: told.append(on) or 1)
    awake._thread.join(timeout=5)
    told.clear()
    link.closed = False
    awake.step()
    assert told == []  # (no game plays: nothing is told)
    link.session_running = True
    awake.step()
    awake.step()
    assert told == [True, True] and awake.on
    link.session_running = False
    awake.step()
    awake.step()
    assert told == [True, True, False] and not awake.on  # (told once that it is inactive again)


class FakeView:
    def __init__(self, fails=False):
        self.fails, self.shots = fails, 0

    def shot(self, max_dim=None, quality=80):
        self.shots += 1
        if self.fails:
            raise wincap.Gone("minimized")
        return b"window-jpeg", 640, 480


def pictures(monkeypatch, view):
    p = studio_mcp.StudioPictures({"project": "Place"}, ["studio-mcp"])
    made = []
    monkeypatch.setattr(p, "_studio_shot", lambda: made.append(1) or b"\xff\xd8\xff-studio")
    monkeypatch.setattr(studio_mcp, "jpeg_of", lambda data, max_dim=None, quality=80: (data, 800, 600))
    monkeypatch.setattr(wincap, "enabled", lambda: True)
    monkeypatch.setattr(wincap.View, "find", classmethod(lambda cls, title, also, picture: view))
    return p, made


def test_pictures_come_from_the_window_once_it_is_found_and_studios_own_checks_it(monkeypatch):
    view = FakeView()
    p, made = pictures(monkeypatch, view)
    assert p.shot() == (b"\xff\xd8\xff-studio", 800, 600) and p.via == "studio_mcp"  # (the first: Studio's own)
    assert p.shot() == (b"window-jpeg", 640, 480) and p.via == "window"
    assert p.shot()[0] == b"window-jpeg" and len(made) == 1 and view.shots == 2
    p._window_checked -= studio_mcp.WINDOW_CHECK + 1  # (time to check it against a picture of Studio's)
    assert p.shot()[0] == b"\xff\xd8\xff-studio" and len(made) == 2
    assert p.shot()[0] == b"window-jpeg"


def test_a_window_that_cant_be_grabbed_gives_way_to_studios_own_pictures(monkeypatch):
    view = FakeView()
    p, made = pictures(monkeypatch, view)
    p.shot()
    view.fails = True
    assert p.shot()[0] == b"\xff\xd8\xff-studio" and p.via == "studio_mcp"  # (minimized: Studio's own again)
    # no window found: Studio's own, and it isn't looked for again at every picture
    p2, made2 = pictures(monkeypatch, None)
    looked = []
    monkeypatch.setattr(wincap.View, "find", classmethod(lambda cls, t, a, pic: looked.append(1) and None))
    for _ in range(4):
        assert p2.shot()[0] == b"\xff\xd8\xff-studio"
    assert len(looked) == 1


@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_windows_are_listed_and_a_missing_one_is_not_found():
    assert wincap.windows("no window has this title 7f3a9c") == []
    assert wincap.View.find("no window has this title 7f3a9c", None, b"\xff\xd8\xff") is None
    assert wincap.wake("no window has this title 7f3a9c") == 0
