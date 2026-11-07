"""A Roblox game's pictures through Studio's MCP server (autopilot/studio_mcp.py), with a stand-in for it
(tests/fake_studio_mcp.py): the MCP client, picking the place's Studio, PNG to JPEG, and a Roblox plugin's Link
answering `capture` and `record` itself (Link.local_ops) while its plugin's own pictures (PNGs) become JPEGs."""

import base64
import json
import os
import sys
import time

import pytest

from autopilot import bridge, media, studio_mcp

FAKE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake_studio_mcp.py")
sys.path.insert(0, os.path.dirname(FAKE))
import fake_studio_mcp  # noqa: E402


def argv(studios=None, fail=False, mode=None):
    studios = [{"id": "s1", "name": "Place1"}] if studios is None else studios
    return [sys.executable, FAKE, json.dumps(studios)] + (["fail"] if fail else [mode] if mode else [])


def roblox_hello(**extra):
    return {"type": "hello", "protocol": 1, "engine": "roblox", "host": "editor", "project": "Sky Farm",
            "place_id": 0, "features": ["levels", "timed_hold"], **extra}


def test_jpeg_of_a_png_scales_it():
    jpeg, w, h = studio_mcp.jpeg_of(fake_studio_mcp.png(64, 48), max_dim=32, quality=80)
    assert jpeg[:3] == b"\xff\xd8\xff" and (w, h) == (32, 24)
    same, w2, h2 = studio_mcp.jpeg_of(jpeg, max_dim=100)
    assert same is jpeg and (w2, h2) == (32, 24)  # (a JPEG that fits stays as it is)
    assert studio_mcp._qscale(100) == 2 and studio_mcp._qscale(1) == 31


def test_the_command_line(monkeypatch):
    monkeypatch.setenv("AUTOPILOT_STUDIO_MCP", "off")
    assert studio_mcp.command() is None
    monkeypatch.setenv("AUTOPILOT_STUDIO_MCP", "/opt/Studio MCP/StudioMCP --stdio" if os.name == "nt" else
                       "'/opt/Studio MCP/StudioMCP' --stdio")
    assert studio_mcp.command()[-1] == "--stdio"


def test_pictures_of_the_places_studio(monkeypatch):
    monkeypatch.setattr(studio_mcp, "STUDIO_WAIT", 0.2)
    studios = [{"id": "a", "name": "Other Place"}, {"id": "b", "name": "Sky Farm"}]
    pictures = studio_mcp.StudioPictures({"project": "Sky Farm"}, argv(studios))
    try:
        jpeg, w, h = pictures.shot(max_dim=960)
        assert jpeg[:3] == b"\xff\xd8\xff" and (w, h) == (64, 48)
        assert pictures._studio == "b"
        assert pictures._client.server["name"] == "StudioMCP stand-in"
    finally:
        pictures.close()
    lost = studio_mcp.StudioPictures({"project": "Nowhere"}, argv(studios))
    with pytest.raises(studio_mcp.McpError, match="none has the place"):
        lost.check()
    lost.close()
    nothing = studio_mcp.StudioPictures({"project": "x"}, argv([]))
    with pytest.raises(studio_mcp.McpError, match="Enable Studio as MCP server"):
        nothing.check()
    nothing.close()
    with pytest.raises(studio_mcp.McpError, match="couldn't start"):
        studio_mcp.StudioPictures({}, ["/no/such/StudioMCP"]).check()


def test_a_roblox_link_answers_capture_and_record_itself(monkeypatch):
    monkeypatch.setattr(studio_mcp, "command", lambda: argv([{"id": "s1", "name": "Sky Farm"}]))
    sent = []
    link = bridge.Link(sent.append)
    link.feed(roblox_hello())
    try:
        assert "media" in link.hello["features"] and link.hello["media_via"] == "studio_mcp"
        assert media.can_capture(link)
        with pytest.raises(bridge.BridgeError, match="no game is running") as e:
            link.request("capture", {"max_dim": 32})
        assert e.value.status == 409
        link.feed({"type": "events", "events": [{"kind": "session", "running": True, "t": 1.5}], "t": 1.5})
        shot = link.request("capture", {"max_dim": 32, "quality": 90})
        assert shot["via"] == "studio_mcp" and (shot["width"], shot["height"]) == (32, 24)
        assert base64.b64decode(shot["jpeg"])[:3] == b"\xff\xd8\xff" and shot["t"] >= 1.5
        frames = []
        link.frame_listeners.append(frames.append)
        assert link.request("record", {"action": "start", "fps": 10, "max_dim": 32})["recording"] is True
        deadline = time.time() + 10
        while len(frames) < 3 and time.time() < deadline:
            time.sleep(0.05)
        assert len(frames) >= 3 and [f["n"] for f in frames[:3]] == [1, 2, 3]
        assert all(f["wall"] and f["width"] == 32 for f in frames)
        # (no game: no frames, and the recording goes on into the next one)
        link.feed({"type": "events", "events": [{"kind": "session", "running": False}]})
        time.sleep(0.3)
        n = len(frames)
        time.sleep(0.4)
        assert len(frames) == n
        assert link.request("record", {"action": "stop"})["recording"] is False
        assert not sent  # (nothing went to the plugin)
    finally:
        link.close("test")
    assert link.media._thread is None and link.media.pictures._client is None


def test_a_failing_studio_says_why(monkeypatch):
    monkeypatch.setattr(studio_mcp, "command", lambda: argv(fail=True))
    link = bridge.Link(lambda f: None)
    link.feed(roblox_hello())
    link.feed({"type": "events", "events": [{"kind": "session", "running": True}]})
    try:
        with pytest.raises(bridge.BridgeError, match="no picture: Studio isn't responding"):
            link.request("capture", {})
    finally:
        link.close("test")


def test_no_studio_mcp_no_pictures(monkeypatch):
    monkeypatch.setattr(studio_mcp, "command", lambda: None)
    link = bridge.Link(lambda f: None)
    link.feed(roblox_hello())
    assert "media" not in link.hello["features"] and not link.local_ops and not media.can_capture(link)
    # (a plugin with pictures of its own keeps them: the requests go to it)
    monkeypatch.setattr(studio_mcp, "command", lambda: argv())
    own = bridge.Link(lambda f: None)
    own.feed(roblox_hello(features=["levels", "media"]))
    assert own.media is None and "record" not in own.local_ops
    # (another engine: never)
    other = bridge.Link(lambda f: None)
    other.feed({"type": "hello", "protocol": 1, "engine": "godot", "features": []})
    assert other.media is None


def test_a_plugins_png_pictures_become_jpegs():
    png = base64.b64encode(fake_studio_mcp.png(64, 48)).decode("ascii")
    sent = []
    link = bridge.Link(sent.append)
    link.feed({"type": "hello", "protocol": 1, "engine": "godot", "features": ["media"]})
    frames = []
    link.frame_listeners.append(frames.append)
    link.feed({"type": "frame", "n": 1, "t": 2.0, "png": png})
    assert frames[0]["n"] == 1 and base64.b64decode(frames[0]["jpeg"])[:3] == b"\xff\xd8\xff"
    assert "png" not in frames[0] and (frames[0]["width"], frames[0]["height"]) == (64, 48)
    link.feed({"type": "frame", "n": 2, "png": "bm90IGEgcG5n"})  # (unreadable: dropped)
    assert len(frames) == 1
    import threading

    def answer():
        while not sent:
            time.sleep(0.01)
        link.feed({"type": "response", "id": sent[0]["id"], "ok": True, "result": {"png": png}, "t": 3.0})

    threading.Thread(target=answer, daemon=True).start()
    shot = link.request("capture", {"max_dim": 32})
    assert (shot["width"], shot["height"]) == (32, 24) and "png" not in shot


def test_a_multiplayer_tests_pictures_are_player_ones(monkeypatch):
    """A multiplayer test's server and clients are Studios of their own (unnamed): the pictures are player 1's client's,
    found again when a play session starts or ends."""
    studios = [{"id": "edit", "name": "Sky Farm"}, {"id": "srv", "name": None}, {"id": "p2", "name": None, "player": 2},
               {"id": "p1", "name": None, "player": 1}]
    pictures = studio_mcp.StudioPictures({"project": "Sky Farm"}, argv(studios))
    try:
        pictures.shot(max_dim=64)
        assert pictures._studio == "p1"
        pictures.forget()
        assert pictures._studio is None
    finally:
        pictures.close()
    # (no test playing: the place's own Studio)
    single = studio_mcp.StudioPictures({"project": "Sky Farm"}, argv([{"id": "a", "name": "Other"},
                                                                       {"id": "edit", "name": "Sky Farm"}]))
    try:
        single.check()
        assert single._studio == "edit"
    finally:
        single.close()
    # (the link's media picks again after a session event)
    monkeypatch.setattr(studio_mcp, "command", lambda: argv(studios))
    link = bridge.Link(lambda f: None)
    link.feed(roblox_hello())
    try:
        link.feed({"type": "events", "events": [{"kind": "session", "running": True}]})
        link.request("capture", {"max_dim": 32})
        assert link.media.pictures._studio == "p1"
        link.media.pictures._studio = "edit"
        link.feed({"type": "events", "events": [{"kind": "session", "running": True}]})
        link.request("capture", {"max_dim": 32})
        assert link.media.pictures._studio == "p1"
    finally:
        link.close("test")


def test_studio_gets_a_moment_to_join_a_server_that_just_started(monkeypatch):
    """The first lists of a fresh server can be empty: that isn't Studio's MCP server being off."""
    monkeypatch.setattr(studio_mcp, "STUDIO_LOOK", 0.01)
    pictures = studio_mcp.StudioPictures({"project": "Place1"}, argv(mode="late:3"))
    try:
        pictures.check()
        assert pictures._studio == "s1"
        assert pictures.shot(max_dim=32)[0][:3] == b"\xff\xd8\xff"
    finally:
        pictures.close()


def test_a_studio_that_never_joins_is_said_after_the_wait(monkeypatch):
    monkeypatch.setattr(studio_mcp, "STUDIO_WAIT", 0.3)
    monkeypatch.setattr(studio_mcp, "STUDIO_LOOK", 0.05)
    pictures = studio_mcp.StudioPictures({"project": "Place1"}, argv(mode="late:1000"))
    try:
        t0 = time.monotonic()
        with pytest.raises(studio_mcp.NoStudio, match="Settings, MCP Servers"):
            pictures.check()
        assert 0.3 <= time.monotonic() - t0 < 5
    finally:
        pictures.close()


def test_a_picture_that_hangs_starts_the_server_over(monkeypatch, tmp_path):
    """A screenshot Studio's MCP server leaves unanswered costs SHOT_TIMEOUT, not the recording: the server is started
    over and the next picture comes."""
    monkeypatch.setattr(studio_mcp, "SHOT_TIMEOUT", 1.0)
    pictures = studio_mcp.StudioPictures({"project": "Place1"}, argv(mode=f"hang:{tmp_path / 'hung'}"))
    try:
        t0 = time.monotonic()
        with pytest.raises(studio_mcp.McpError, match="didn't answer"):
            pictures.shot(max_dim=32)
        assert time.monotonic() - t0 < 5 and pictures._client is None
        jpeg, w, h = pictures.shot(max_dim=32)
        assert jpeg[:3] == b"\xff\xd8\xff"
    finally:
        pictures.close()
