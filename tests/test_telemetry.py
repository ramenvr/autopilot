"""A run's usage and data (autopilot/telemetry.py, autopilot/media.py, autopilot/training.py): tokens counted per model,
the language model's calls passed through the proxy unchanged and kept whole, each decision kept whole, the recording
encoded, and the shared data sent."""

import base64
import gzip
import http.server
import io
import json
import os
import threading
import time
import types

import pytest
import requests

from autopilot import aura_memory, media, runtime, telemetry, training


@pytest.fixture
def game():
    from test_autopilot import FakeLink

    from autopilot.kit.game import Game

    g = Game(FakeLink())
    g.look = {"x": 0.1, "y": 0.1}
    return g

# -- tokens ---------------------------------------------------------------------------------------------------------


def test_usage_sums_per_model_and_lists_the_language_model_first():
    u = telemetry.Usage()
    u.add("decision", "dm-1", input=100, output=2)
    u.add("llm", "claude-opus-5-5", input=10, output=5, cache_read=1000, cache_write=50)
    u.add("decision", "dm-1", input=120, output=3)
    snap = u.snapshot()
    assert [m["kind"] for m in snap] == ["llm", "decision"]
    assert snap[1] == {"kind": "decision", "model": "dm-1", "calls": 2, "input": 220, "output": 5, "cache_read": 0,
                       "cache_write": 0}
    assert u.totals()["llm"]["cache_read"] == 1000


def test_counting_jev_counts_each_answer_and_passes_the_rest_through():
    class Client:
        models = "the model list"

        def system_one(self, state, questions):
            return types.SimpleNamespace(model="dm-2", usage=types.SimpleNamespace(input_tokens=40, output_tokens=1))

    u = telemetry.Usage()
    jev = telemetry.CountingJev(Client(), u)
    jev.system_one(state={}, questions={})
    jev.system_one(state={}, questions={})
    assert jev.models == "the model list"
    assert u.snapshot() == [{"kind": "decision", "model": "dm-2", "calls": 2, "input": 80, "output": 2,
                             "cache_read": 0, "cache_write": 0}]


def test_llm_usage_reads_anthropic_and_openai_answers():
    model, usage = telemetry.llm_usage({"type": "message", "model": "claude-x", "usage": {
        "input_tokens": 12, "output_tokens": 7, "cache_read_input_tokens": 900, "cache_creation_input_tokens": 30}})
    assert model == "claude-x" and usage == {"input": 12, "output": 7, "cache_read": 900, "cache_write": 30}
    model, usage = telemetry.llm_usage({"model": "gpt-x", "usage": {
        "prompt_tokens": 100, "completion_tokens": 9, "prompt_tokens_details": {"cached_tokens": 60}}})
    assert usage == {"input": 40, "output": 9, "cache_read": 60, "cache_write": 0}
    assert telemetry.llm_usage({"input_tokens": 5})[1] is None  # (count_tokens: nothing used)


ANTHROPIC_STREAM = [
    {"type": "message_start", "message": {"id": "m1", "type": "message", "role": "assistant", "model": "claude-x",
                                          "content": [], "usage": {"input_tokens": 11, "cache_read_input_tokens": 500,
                                                                   "cache_creation_input_tokens": 20,
                                                                   "output_tokens": 1}}},
    {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}},
    {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "Let me "}},
    {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "look."}},
    {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "sig"}},
    {"type": "content_block_stop", "index": 0},
    {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}},
    {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "Probing."}},
    {"type": "content_block_stop", "index": 1},
    {"type": "content_block_start", "index": 2,
     "content_block": {"type": "tool_use", "id": "t1", "name": "mcp__game__probe", "input": {}}},
    {"type": "content_block_delta", "index": 2, "delta": {"type": "input_json_delta", "partial_json": '{"code": '}},
    {"type": "content_block_delta", "index": 2, "delta": {"type": "input_json_delta", "partial_json": '"game.t"}'}},
    {"type": "content_block_stop", "index": 2},
    {"type": "message_delta", "delta": {"stop_reason": "tool_use", "stop_sequence": None},
     "usage": {"output_tokens": 42}},
    {"type": "message_stop"},
]


def sse_bytes(events):
    return b"".join(b"event: %s\ndata: %s\n\n" % (e["type"].encode(), json.dumps(e).encode()) for e in events)


def test_an_anthropic_stream_is_put_back_together_as_its_message():
    sse = telemetry.SSE()
    data = sse_bytes(ANTHROPIC_STREAM)
    for i in range(0, len(data), 7):  # (in pieces that cut events anywhere)
        sse.feed(data[i:i + 7])
    m = sse.result()
    assert m["stop_reason"] == "tool_use"
    assert m["content"][0] == {"type": "thinking", "thinking": "Let me look.", "signature": "sig"}
    assert m["content"][1] == {"type": "text", "text": "Probing."}
    assert m["content"][2]["input"] == {"code": "game.t"}
    assert m["usage"]["output_tokens"] == 42 and m["usage"]["cache_read_input_tokens"] == 500


# -- the proxy --------------------------------------------------------------------------------------------------------

class Upstream:
    """A stand-in for a model API: /v1/messages answers a stream when asked to, else JSON."""

    def __init__(self):
        self.seen = []
        upstream = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                upstream.seen.append((self.path, dict(self.headers), body))
                if body.get("stream"):
                    data = sse_bytes(ANTHROPIC_STREAM)
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                else:
                    data = json.dumps({"type": "message", "model": "claude-x", "content": [{"type": "text",
                                                                                         "text": "hi"}],
                                       "usage": {"input_tokens": 3, "output_tokens": 2}}).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.server.shutdown()


@pytest.fixture
def upstream():
    u = Upstream()
    yield u
    u.close()


def test_the_proxy_passes_calls_through_and_keeps_them_whole_without_the_keys(upstream, tmp_path):
    usage = telemetry.Usage()
    log = tmp_path / "llm.jsonl"
    proxy = telemetry.LLMProxy(usage, str(log))
    proxy.route("anthropic", upstream.url)
    try:
        url = proxy.url("anthropic") + "/v1/messages"
        r = requests.post(url, json={"model": "claude-x", "stream": True, "messages": [{"role": "user",
                                                                                         "content": "go"}]},
                          headers={"x-api-key": "sk-secret-123456"})
        assert r.status_code == 200 and r.content == sse_bytes(ANTHROPIC_STREAM)
        r = requests.post(url, json={"model": "claude-x", "messages": []}, headers={"x-api-key": "sk-secret-123456"})
        assert r.json()["content"][0]["text"] == "hi"
    finally:
        proxy.close()
    assert upstream.seen[0][0] == "/v1/messages" and upstream.seen[0][1]["x-api-key"] == "sk-secret-123456"
    lines = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()]
    assert [x["call"] for x in lines] == [1, 2]
    assert lines[0]["request"]["messages"][0]["content"] == "go"
    assert lines[0]["response"]["content"][2]["name"] == "mcp__game__probe" and lines[0]["stream"]
    assert lines[1]["response"]["content"][0]["text"] == "hi" and not lines[1]["stream"]
    assert "sk-secret" not in log.read_text(encoding="utf-8")
    snap = usage.snapshot()
    assert snap == [{"kind": "llm", "model": "claude-x", "calls": 2, "input": 14, "output": 44, "cache_read": 500,
                     "cache_write": 20}]


def test_the_proxy_counts_without_keeping_when_the_data_isnt_shared(upstream, tmp_path):
    usage = telemetry.Usage()
    proxy = telemetry.LLMProxy(usage, None)
    proxy.route("anthropic", upstream.url)
    try:
        requests.post(proxy.url("anthropic") + "/v1/messages", json={"model": "claude-x", "messages": []})
    finally:
        proxy.close()
    assert usage.totals()["llm"]["calls"] == 1
    assert not list(tmp_path.iterdir())


# -- a run ------------------------------------------------------------------------------------------------------------

class SendLink:
    def __init__(self, hello=None):
        self.sent = []
        self.hello = hello or {"project": "fake", "engine": "unity", "plugin_version": "1.2"}
        self.frame_listeners = []
        self.requests = []

    def send(self, frame):
        self.sent.append(frame)

    def request(self, op, args=None, timeout=None):
        self.requests.append((op, args))
        return {"recording": op == "record" and (args or {}).get("action") == "start"}


def test_sharing_follows_the_plugin_and_the_environment(monkeypatch):
    monkeypatch.delenv("AUTOPILOT_SHARE", raising=False)
    assert telemetry.sharing({}) and telemetry.sharing({"share": True}) and telemetry.sharing(None)
    assert not telemetry.sharing({"share": False})
    monkeypatch.setenv("AUTOPILOT_SHARE", "0")
    assert not telemetry.sharing({"share": True})


def test_a_shared_run_points_the_agent_at_the_proxy_then_finishes_its_data(upstream, tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", upstream.url)
    link = SendLink()
    run_dir = tmp_path / "runs" / "r1"
    (tmp_path / "notes.md").write_text("before")
    with telemetry.RunTelemetry(link, str(run_dir), "r1", "fake", "Verify that x", "verify", True) as tel:
        (tmp_path / "notes.md").write_text("after")
        assert os.environ["ANTHROPIC_BASE_URL"] == tel.proxy.url("anthropic")
        requests.post(os.environ["ANTHROPIC_BASE_URL"] + "/v1/messages", json={"model": "claude-x", "messages": []})
        tel.report = {"outcome": "CONFIRMED", "agent": "claude", "model": "claude-x"}
    assert os.environ["ANTHROPIC_BASE_URL"] == upstream.url
    final = [f for f in link.sent if f["type"] == "usage"][-1]
    assert final["final"] and final["models"][0]["model"] == "claude-x"
    folder = run_dir / "telemetry"
    manifest = json.loads((folder / "run.json").read_text(encoding="utf-8"))
    assert manifest["outcome"]["outcome"] == "CONFIRMED" and manifest["engine"]["engine"] == "unity"
    assert manifest["usage"][0]["calls"] == 1
    with gzip.open(folder / "llm.jsonl.gz", "rt", encoding="utf-8") as f:
        assert json.loads(f.readline())["response"]["content"][0]["text"] == "hi"
    assert not (folder / "llm.jsonl").exists()
    assert (folder / "memory_before" / "notes.md").read_text() == "before"
    assert (folder / "memory_after" / "notes.md").read_text() == "after"


def test_an_unshared_run_keeps_only_its_recording_and_still_tells_the_usage(upstream, tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", upstream.url)
    link = SendLink({"project": "fake", "features": ["media"]})
    run_dir = tmp_path / "runs" / "r2"
    with telemetry.RunTelemetry(link, str(run_dir), "r2", "fake", "Explore", "explore", False) as tel:
        requests.post(os.environ["ANTHROPIC_BASE_URL"] + "/v1/messages", json={"model": "claude-x", "messages": []})
        tel.recorder.begin(1)
        for n in range(3):
            link.frame_listeners[0]({"n": n, "t": n, "wall": 10 + n, "jpeg": base64.b64encode(jpeg()).decode()})
        tel.recorder.end(1)
    assert sorted(os.listdir(run_dir)) == ["media"]  # (no telemetry/: nothing to send)
    assert link.requests[0][0] == "record"  # (recorded for the plugin's report)
    shown = tel.recording()
    assert shown["video"] == os.path.abspath(run_dir / "media" / "recording-1.mp4") and shown["episode"] == 1
    assert shown["seconds"] == 2.0 + media.HOLD_MS / 1000  # (its last picture held)
    assert "frames" not in shown  # (only Godot's report plays a flipbook)
    assert [f for f in link.sent if f["type"] == "usage"][-1]["models"][0]["calls"] == 1
    from autopilot import aura_memory

    assert aura_memory.is_telemetry("runs/r2/media/recording-1.mp4")  # (never synced)
    assert not aura_memory.is_telemetry("runs/r2/harness.py")


# -- the recording ------------------------------------------------------------------------------------------------

def jpeg(width=64, height=48, shade=128):
    import av

    codec = av.CodecContext.create("mjpeg", "w")
    codec.width, codec.height, codec.pix_fmt = width, height, "yuvj420p"
    codec.time_base = __import__("fractions").Fraction(1, 25)
    frame = av.VideoFrame(width, height, "rgb24")
    frame.planes[0].update(bytes([shade]) * (frame.planes[0].buffer_size))
    data = b"".join(bytes(p) for p in codec.encode(frame.reformat(format="yuvj420p")))
    data += b"".join(bytes(p) for p in codec.encode(None))
    return data


def test_the_recording_becomes_an_mp4_with_each_frames_time(tmp_path):
    import av

    link = SendLink({"project": "fake", "features": ["media"]})
    with media.Recorder(link, str(tmp_path)) as rec:
        assert link.requests[0] == ("record", {"action": "start", "fps": 5, "max_dim": 960, "quality": 75})
        rec.begin(1)
        for n in range(10):
            frame = {"type": "frame", "n": n, "t": 1.0 + n * 0.2, "wall": 1000.0 + n * 0.2, "width": 64,
                     "height": 48, "jpeg": base64.b64encode(jpeg(shade=20 * n)).decode()}
            for listener in link.frame_listeners:
                listener(frame)
        rec.end(1)
    assert link.requests[-1] == ("record", {"action": "stop"})
    assert rec.kept == 10 and not link.frame_listeners
    lines = [json.loads(x) for x in (tmp_path / "media" / "frames-1.jsonl").read_text().splitlines()]
    assert [x["frame"] for x in lines] == list(range(10)) and lines[3]["t"] == pytest.approx(1.6)
    assert lines[5]["pts_ms"] == 1000
    with av.open(str(tmp_path / "media" / "recording-1.mp4")) as video:
        frames = list(video.decode(video=0))
    # its 10 frames, then the last one again a second later: the end stays on screen (an MP4's last frame lasts no
    # time at all)
    assert len(frames) == 11 and (frames[0].width, frames[0].height) == (64, 48)
    assert round(frames[-1].time * 1000) == 1800 + media.HOLD_MS
    assert rec.summary()["episodes"] == [{"episode": 1, "frames": 10, "wall0": 1000.0, "wall_end": 1001.8,
                                          "seconds": 2.8}]


class CaptureLink(SendLink):
    """A plugin that also answers `capture` with a picture."""

    def request(self, op, args=None, timeout=None):
        if op == "capture":
            self.requests.append((op, args))
            return {"jpeg": base64.b64encode(jpeg(shade=250)).decode(), "t": 99.0, "width": 64, "height": 48}
        return super().request(op, args, timeout)


def test_each_episode_is_a_video_of_its_own_and_the_report_shows_the_last(tmp_path):
    import av

    link = CaptureLink({"project": "fake", "features": ["media"]})
    now = time.time() - 60  # (the frames' wall times on the computer's clock, as the end's picture is stamped)
    with media.Recorder(link, str(tmp_path)) as rec:
        send = link.frame_listeners[0]
        send({"n": 0, "t": 0, "wall": now, "jpeg": base64.b64encode(jpeg()).decode()})  # (no episode: not kept)
        rec.begin(1)
        for n in range(1, 4):
            send({"n": n, "t": n, "wall": now + n * 0.2, "jpeg": base64.b64encode(jpeg()).decode()})
        rec.end(1)
        send({"n": 4, "t": 4, "wall": now + 10, "jpeg": base64.b64encode(jpeg()).decode()})  # (between: not kept)
        rec.begin(2)
        for n in range(5, 7):
            send({"n": n, "t": n, "wall": now + 20 + n * 0.2, "jpeg": base64.b64encode(jpeg()).decode()})
        rec.end(2)
    files = sorted(os.listdir(tmp_path / "media"))
    assert files == ["frames-1.jsonl", "frames-2.jsonl", "recording-1.mp4", "recording-2.mp4"]
    assert rec.outside == 2 and rec.kept == 3 + 1 + 2 + 1  # (each episode's frames and its last picture)
    assert len(rec.frames.items) == 7  # (describe_frames still sees every frame the game sent, in an episode or not)
    shown = rec.shown()
    assert shown["episode"] == 2 and shown["video"] == os.path.abspath(tmp_path / "media" / "recording-2.mp4")
    # Each episode ends on the picture taken as it ended (the capture), the last line of its index, held on screen
    lines = [json.loads(x) for x in (tmp_path / "media" / "frames-1.jsonl").read_text().splitlines()]
    assert [x.get("final", False) for x in lines] == [False, False, False, True] and lines[-1]["t"] == 99.0
    assert [op for op, _ in link.requests].count("capture") == 2
    with av.open(str(tmp_path / "media" / "recording-1.mp4")) as video:
        stream = video.streams.video[0]
        packets = [p for p in video.demux(stream) if p.pts is not None]
        length = float(stream.duration * stream.time_base)
    with av.open(str(tmp_path / "media" / "recording-1.mp4")) as video:
        frames = list(video.decode(video=0))
    assert len(frames) == 5 and bytes(frames[-1].planes[0])[0] > 200  # (the end picture (bright), held)
    # (every frame decoded at its own time, so the video's length runs past its last picture: a player shows it)
    assert all(p.dts == p.pts for p in packets) and length > float(frames[-1].pts * frames[-1].time_base)
    assert [c["episode"] for c in rec.summary()["episodes"]] == [1, 2]


def test_godots_recording_is_also_a_flipbook_at_a_fixed_rate(tmp_path):
    link = SendLink({"project": "fake", "features": ["media"], "engine": "godot"})
    with media.Recorder(link, str(tmp_path), flipbook=True) as rec:
        rec.begin(1)
        # 0 s, 0.2 s, then the game paused for 1.4 s, then 1.8 s
        for n, wall in enumerate((100.0, 100.2, 101.8)):
            link.frame_listeners[0]({"n": n, "t": n, "wall": wall, "width": 1280, "height": 720,
                                     "jpeg": base64.b64encode(jpeg(1280, 720, 40 * n)).decode()})
        rec.end(1)
    shown = rec.shown()
    assert shown["frames_fps"] == 2 and shown["seconds"] == 1.8 + media.HOLD_MS / 1000
    folder = shown["frames"]
    assert os.path.basename(folder) == "flipbook-1"
    names = sorted(os.listdir(folder))
    # frame i shows time i / 2: 0 s the first picture; 0.5, 1 and 1.5 s the second (the newest then: the game was
    # paused); from 2 s the last picture, held to 2.8 s (and a frame of its own at the end)
    assert names == [f"00000{i}.jpg" for i in range(7)]
    data = [open(os.path.join(folder, name), "rb").read() for name in names]
    assert data[1] == data[2] == data[3] and data[4] == data[5] == data[6] and len({data[0], data[1], data[4]}) == 3
    import av

    with av.open(os.path.join(folder, names[0])) as image:
        picture = next(image.decode(video=0))
    assert (picture.width, picture.height) == (640, 360)  # (FLIPBOOK_DIM)


def test_without_an_encoder_the_frames_are_kept_as_jpegs(tmp_path, monkeypatch):
    def broken(*a, **k):
        raise ImportError("no av")

    monkeypatch.setattr(media, "Encoder", broken)
    link = SendLink({"project": "fake", "features": ["media"]})
    with media.Recorder(link, str(tmp_path)) as rec:
        rec.begin(1)
        for n in range(3):
            link.frame_listeners[0]({"n": n, "t": n, "wall": 10 + n, "jpeg": base64.b64encode(jpeg()).decode()})
        rec.end(1)
    assert sorted(os.listdir(tmp_path / "media" / "frames-1")) == ["000000.jpg", "000001.jpg", "000002.jpg"]


def test_a_plugin_that_cant_capture_isnt_asked_to(tmp_path):
    link = SendLink({"project": "fake"})
    with media.Recorder(link, str(tmp_path)) as rec:
        pass
    assert media.still(link, str(tmp_path), "report") is None
    assert not link.requests
    assert rec.shown() is None and rec.missing().startswith("the game can't be recorded here")


def frame_at(n):
    return {"n": n, "t": n, "wall": 10 + n * 0.2, "jpeg": base64.b64encode(jpeg()).decode()}


def test_a_run_without_a_video_says_why(tmp_path, monkeypatch):
    monkeypatch.setattr(media, "FOLLOW_EVERY", 0.02)
    monkeypatch.setattr(media, "RETRY_START_EVERY", 0.05)

    class Refusing(SendLink):
        refuse = True

        def request(self, op, args=None, timeout=None):
            self.requests.append((op, args))
            if op == "record" and args["action"] == "start" and self.refuse:
                raise RuntimeError("no picture: the game's window is minimized or hidden")
            return {"recording": True}

    # The plugin refuses to start (no picture yet): asked again now and then, the recording starts once it can
    link = Refusing({"project": "fake", "features": ["media"]})
    with media.Recorder(link, str(tmp_path / "a")) as rec:
        assert not rec.started and "window is minimized or hidden" in rec.missing()
        link.refuse = False
        deadline = time.monotonic() + 5
        while not rec.started and time.monotonic() < deadline:
            time.sleep(0.02)
        assert rec.started
        rec.begin(1)
        for n in range(3):
            link.frame_listeners[0](frame_at(n))
        rec.end(1)
    assert rec.shown()["seconds"] == pytest.approx(0.4 + media.HOLD_MS / 1000) and rec.missing() is None
    # Never started
    link = Refusing({"project": "fake", "features": ["media"]})
    with media.Recorder(link, str(tmp_path / "b")) as rec:
        pass
    assert rec.missing() == "recording couldn't start: no picture: the game's window is minimized or hidden"
    assert ("record", {"action": "stop"}) not in link.requests
    # Started, but no picture came (the game never ran)
    with media.Recorder(SendLink({"project": "fake", "features": ["media"]}), str(tmp_path / "c")) as rec:
        pass
    assert rec.missing() == "no picture came from the game while it played"
    # Pictures, but no harness played (no episode): nothing kept, and said so
    link = SendLink({"project": "fake", "features": ["media"]})
    with media.Recorder(link, str(tmp_path / "e")) as rec:
        link.frame_listeners[0](frame_at(0))
    assert rec.missing() == "no harness played the game, so there was nothing to record"
    # Pictures, but no encoder: kept as JPEGs, and said so
    monkeypatch.setattr(media, "Encoder", lambda *a, **k: (_ for _ in ()).throw(ImportError("no av")))
    link = SendLink({"project": "fake", "features": ["media"]})
    with media.Recorder(link, str(tmp_path / "d")) as rec:
        rec.begin(1)
        link.frame_listeners[0](frame_at(0))
        rec.end(1)
    assert rec.missing().startswith("the video couldn't be made (ImportError: no av): its pictures are in ")


def test_the_recording_goes_on_when_the_plugin_reconnects(tmp_path, monkeypatch):
    monkeypatch.setattr(media, "FOLLOW_EVERY", 0.02)

    class Live:
        """A LiveLink: the plugin's newest connection."""

        def __init__(self):
            self.current = SendLink({"project": "fake", "features": ["media"]})
            self.hello = self.current.hello

        def request(self, op, args=None, timeout=None):
            return self.current.request(op, args, timeout)

    link = Live()
    first = link.current
    with media.Recorder(link, str(tmp_path)) as rec:
        rec.begin(1)
        first.frame_listeners[0](frame_at(0))
        link.current = second = SendLink(first.hello)  # (Connect pressed again: the plugin ended its recording)
        deadline = time.monotonic() + 5
        while not second.requests and time.monotonic() < deadline:
            time.sleep(0.02)
        assert second.requests[0][1]["action"] == "start" and not first.frame_listeners
        second.frame_listeners[0](frame_at(1))
        second.resumes = 1  # (the same connection, resumed: Unity's script reload)
        deadline = time.monotonic() + 5
        while len(second.requests) < 2 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert second.requests[1][1]["action"] == "start" and len(second.frame_listeners) == 1
        second.frame_listeners[0](frame_at(2))
        rec.end(1)
    assert rec.kept == 3 and rec.shown()["seconds"] == pytest.approx(0.4 + media.HOLD_MS / 1000)


def test_a_still_is_saved_with_its_line(tmp_path):
    class Link(SendLink):
        def request(self, op, args=None, timeout=None):
            return {"width": 64, "height": 48, "t": 3.5, "jpeg": base64.b64encode(jpeg()).decode()}

    rel = media.still(Link({"features": ["media"]}), str(tmp_path), "finding", {"signature": "hud:x"})
    assert rel.startswith("media/stills/") and (tmp_path / rel).exists()
    line = json.loads((tmp_path / "media" / "stills.jsonl").read_text())
    assert line["reason"] == "finding" and line["signature"] == "hud:x" and line["t"] == 3.5


# -- each decision ------------------------------------------------------------------------------------------------

def test_a_shared_episode_keeps_each_decision_whole(game, tmp_path):
    from test_autopilot import CrateHarness, FakeJev

    class RawJev(FakeJev):
        def system_one(self, state, questions):
            r = super().system_one(state, questions)
            r.raw = {"model": "dm-1", "usage": {"input_tokens": 50, "output_tokens": 1},
                     "answers": {"pick": {"type": "choice", "choice": r.choices["pick"].choice}}}
            return r

    folder = tmp_path / "telemetry"
    rt = runtime.Runtime(game, CrateHarness(), str(tmp_path / "trace1.jsonl"), client=RawJev("open_crate"),
                         max_seconds=20, telemetry=str(folder))
    assert rt.run()["status"]["state"] == "done"
    calls = [json.loads(x) for x in (folder / "decisions1.jsonl").read_text(encoding="utf-8").splitlines()]
    assert calls and calls[0]["call"] == 1 and calls[0]["episode"] == "trace1"
    first = calls[0]
    assert first["request"]["state"]["goal"] == "Open the crate"
    assert "criteria" in first["request"]["questions"]["pick"]
    assert first["response"]["model"] == "dm-1" and first["chosen"] and first["action"]
    trace = [json.loads(x) for x in (tmp_path / "trace1.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [e["call"] for e in trace if e["kind"] == "decision"][0] == 1


def test_an_unshared_episode_keeps_no_decisions(game, tmp_path):
    from test_autopilot import CrateHarness, FakeJev

    runtime.Runtime(game, CrateHarness(), str(tmp_path / "trace1.jsonl"), client=FakeJev("open_crate"),
                    max_seconds=20).run()
    assert sorted(os.listdir(tmp_path)) == ["trace1.jsonl"]


# -- memory sync keeps the telemetry out ----------------------------------------------------------------------------

def test_aura_memory_never_syncs_a_runs_telemetry(tmp_path):
    for rel in ("notes.md", "runs/r1/trace1.jsonl", "runs/r1/telemetry/llm.jsonl.gz",
                "runs/r1/telemetry/media/recording.mp4", ".training-sync.json"):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")
    assert sorted(aura_memory.synced_files(str(tmp_path))) == ["notes.md", "runs/r1/trace1.jsonl"]


# -- shared data uploads --------------------------------------------------------------------------------------------

class Store:
    """The shared data's endpoint and its storage, as requests sees them."""

    def __init__(self, stored=None):
        self.stored = dict(stored or {})
        self.calls = []
        self.puts = {}

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append((url, json, headers))
        op = json["op"]
        if op == "register":
            return Answer(200, {"install_id": "i-1", "token": "gti1.abc.def"})
        if op == "list":
            return Answer(200, {"files": [{"path": p, "md5": m} for p, m in self.stored.items()]})
        if op == "sign":
            return Answer(200, {"put": [{"path": f["path"], "url": f"https://gcs.test/{f['path']}",
                                         "headers": {"Content-Type": f["content_type"]}} for f in json["put"]]})
        return Answer(400, {})

    def put(self, url, data=None, headers=None, timeout=None):
        body = data.read() if hasattr(data, "read") else data
        self.puts[url.split("gcs.test/")[1]] = (body, headers)
        return Answer(200, {})


class Answer:
    def __init__(self, status, body):
        self.status_code, self._body, self.text = status, body, json.dumps(body)

    def json(self):
        return self._body


def project_folder(tmp_path):
    """A project with a shared run with the user's own keys (r1), one through Aura (r2), and one whose data wasn't
    shared (r3: no telemetry/run.json)."""
    from autopilot import dataset

    root = tmp_path / "memory" / "fake"
    files = {"notes.md": "notes", "project.json": '{"project": "C:/Users/someone/fake"}', "codex/a.txt": "code",
             "runs/r1/prompt.txt": "Verify that x", "runs/r1/trace1.jsonl": '{"kind": "start", "wall": 1}\n',
             "runs/r1/.agent/x": "secret transcript", "runs/r2/prompt.txt": "Explore",
             "runs/r3/prompt.txt": "Verify that y (not shared)", "runs/r3/trace1.jsonl": '{"kind": "start"}\n'}
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    cid = dataset.codex_id(str(root))
    for run, aura in (("r1", False), ("r2", True)):
        (root / "runs" / run / "telemetry").mkdir(parents=True)
        (root / "runs" / run / "telemetry" / "run.json").write_text(json.dumps(
            {"schema": 1, "run": run, "run_uid": f"uid{run}", "codex": cid, "through_aura": aura}))
    return root


def test_an_upload_sends_the_shared_runs_that_went_its_way_cleaned_and_their_manifest_last(tmp_path, monkeypatch):
    root = project_folder(tmp_path)
    store = Store()
    monkeypatch.setattr(training.requests, "put", store.put)
    job = training.Upload("https://aura.test/api/autopilot/training", "gti1.abc.def", "fake", http=store)
    assert job.send(str(root), through_aura=False) == 3
    sent = list(store.puts)
    assert sent[-1] == "runs/r1-uidr1/run.json"  # (last: the run has arrived whole)
    assert set(sent[:-1]) == {"runs/r1-uidr1/prompt.txt", "runs/r1-uidr1/episodes/1/trace.jsonl.gz"}
    # never: the codex, the run through Aura (not with an install token), the unshared run, notes.md, project.json,
    # .agent/
    assert store.puts["runs/r1-uidr1/run.json"][1]["Content-Type"] == "application/json"
    assert all(c[1]["project"] == "fake" and c[2]["Authorization"] == "Bearer gti1.abc.def" for c in store.calls)
    manifest = json.loads(store.puts["runs/r1-uidr1/run.json"][0])
    assert manifest["schema"] == 2 and manifest["channel"] == "own_keys"
    assert manifest["codex_id"] == json.loads((root / "runs/r1/telemetry/run.json").read_text())["codex"]
    assert {f["path"] for f in manifest["files"]} == {"prompt.txt", "episodes/1/trace.jsonl.gz"}
    store.puts.clear()
    assert job.send(str(root), through_aura=False) == 0  # (sent: never again)
    assert job.send(str(root), through_aura=True) == 2  # (the run through Aura, its own way)
    assert sorted(store.puts) == ["runs/r2-uidr2/prompt.txt", "runs/r2-uidr2/run.json"]


def test_an_upload_cut_short_is_finished_by_the_next(tmp_path, monkeypatch):
    root = project_folder(tmp_path)
    store = Store()

    def flaky(url, data=None, headers=None, timeout=None):
        if url.endswith("run.json"):
            return Answer(503, {})
        return store.put(url, data, headers, timeout)

    monkeypatch.setattr(training.requests, "put", flaky)
    job = training.Upload("https://aura.test/api/autopilot/training", "gti1.abc.def", "fake", http=store)
    with pytest.raises(training.UploadError):
        job.send(str(root), through_aura=False)
    assert "runs/r1-uidr1/run.json" not in store.puts  # (not arrived whole)
    store.stored.update({p: hashlib_md5(body) for p, (body, _) in store.puts.items()})
    store.puts.clear()
    monkeypatch.setattr(training.requests, "put", store.put)
    assert job.send(str(root), through_aura=False) == 1 and list(store.puts) == ["runs/r1-uidr1/run.json"]


def hashlib_md5(data):
    import hashlib

    return base64.b64encode(hashlib.md5(data).digest()).decode()


def test_this_machine_registers_once(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr("os.path.expanduser", lambda p: str(tmp_path) if p == "~" else p)
    store = Store()
    assert training.install_token(store) == "gti1.abc.def"
    assert training.install_token(store) == "gti1.abc.def"
    assert [c[1]["op"] for c in store.calls] == ["register"]


def test_a_run_through_aura_uploads_only_when_aura_allows_training(tmp_path, monkeypatch):
    root = project_folder(tmp_path)
    store = Store()
    monkeypatch.setattr(training.requests, "put", store.put)
    refused = types.SimpleNamespace(training=None, run_token="grt1.x")
    assert training.upload(str(root), "fake", refused, http=store) == 0 and not store.calls
    allowed = types.SimpleNamespace(training="https://aura.test/api/autopilot/training", run_token="grt1.x")
    assert training.upload(str(root), "fake", allowed, http=store) == 2  # (r2's prompt and run.json)
    assert store.calls[0][2]["Authorization"] == "Bearer grt1.x"
    assert all(p.startswith("runs/r2-") for p in store.puts)  # (only the run through Aura)


def test_a_failed_upload_is_left_for_the_next_run(tmp_path, monkeypatch):
    root = project_folder(tmp_path)
    tries, waits = [], []

    class Down(Store):
        def post(self, *a, **k):
            tries.append(1)
            raise requests.ConnectionError("offline")

    assert training.upload(str(root), "fake", types.SimpleNamespace(training="https://x", run_token="t"),
                           http=Down(), sleep=waits.append) == 0
    # (tried again RETRIES times, each wait about twice the one before)
    assert len(tries) == training.RETRIES + 1 and len(waits) == training.RETRIES
    for i, w in enumerate(waits):
        assert 0.8 * training.RETRY_SECONDS * 2 ** i <= w <= 1.2 * training.RETRY_SECONDS * 2 ** i


AURA_RUN = types.SimpleNamespace(training="https://aura.test/api/autopilot/training", run_token="grt1.x")


def test_an_upload_that_fails_for_a_passing_reason_is_tried_again_and_goes_on_from_what_is_stored(tmp_path,
                                                                                                monkeypatch):
    root = project_folder(tmp_path)
    store = Store()
    answers = iter([503, 502])  # (the endpoint busy twice when signing, then fine)
    puts = iter([Answer(500, {})])  # (and the storage once)

    class Busy(Store):
        def post(self, url, json=None, headers=None, timeout=None):
            if json["op"] == "sign":
                status = next(answers, 200)
                if status != 200:
                    return Answer(status, {"error": "busy"})
            return store.post(url, json, headers, timeout)

    def put(url, data=None, headers=None, timeout=None):
        failed = next(puts, None)
        return failed if failed is not None else store.put(url, data, headers, timeout)

    monkeypatch.setattr(training.requests, "put", put)
    waits = []
    assert training.upload(str(root), "fake", AURA_RUN, http=Busy(), sleep=waits.append) == 2
    assert len(waits) == 3 and sorted(store.puts) == ["runs/r2-uidr2/prompt.txt", "runs/r2-uidr2/run.json"]


@pytest.mark.parametrize("status", [400, 401, 403, 404, 429])
def test_an_upload_refused_isnt_tried_again(tmp_path, status):
    root = project_folder(tmp_path)
    tries, waits = [], []

    class Refused(Store):
        def post(self, url, json=None, headers=None, timeout=None):
            tries.append(json["op"])
            return Answer(status, {"error": "no"})

    assert training.upload(str(root), "fake", AURA_RUN, http=Refused(), sleep=waits.append) == 0
    assert tries == ["list"] and waits == []  # (consent, a bad token, the day's quota: left for the next run)


def test_gzip_compress_keeps_the_lines(tmp_path):
    (tmp_path / "a.jsonl").write_text('{"x": 1}\n')
    (tmp_path / "run.json").write_text("{}")
    telemetry.compress(str(tmp_path))
    assert sorted(os.listdir(tmp_path)) == ["a.jsonl.gz", "run.json"]
    with gzip.open(tmp_path / "a.jsonl.gz", "rt") as f:
        assert json.load(io.StringIO(f.read())) == {"x": 1}


def test_the_reader_pairs_each_decision_with_what_it_led_to(game, tmp_path):
    import sys

    from test_autopilot import CrateHarness, FakeJev

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from autopilot import dataset
    from tools import training_data

    project = tmp_path / "memory" / "fake"
    folder = project / "runs" / "r1" / "telemetry"
    runtime.Runtime(game, CrateHarness(), str(folder.parent / "trace1.jsonl"), client=FakeJev("open_crate"),
                    max_seconds=20, telemetry=str(folder)).run()
    telemetry.compress(str(folder))
    (folder / "run.json").write_text(json.dumps({"run": "r1", "run_uid": "u1",
                                                 "episodes": telemetry.episode_spans(str(folder.parent))}))
    dataset.export_project(str(project), str(tmp_path / "out"))
    run = tmp_path / "out" / "runs" / "r1-u1"
    found = training_data.decisions(str(run))
    assert found and any(d["outcome"] and d["outcome"]["option"] == d["chosen"] for d in found)
    assert training_data.runs(str(tmp_path / "out")) == [str(run)] and training_data.problems(str(run)) == []
    assert training_data.manifest(str(run))["episodes"][0]["n"] == 1


def test_everything_syncs_by_the_wall_clock(game, tmp_path):
    """The video, each decision and each LLM call share one clock (wall): a decision's picture is the frame captured
    at or before it, and its place in its episode's video (episodes/<n>/recording.mp4) is wall - that video's wall0."""
    import sys

    from test_autopilot import CrateHarness, FakeJev

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from autopilot import dataset
    from tools import training_data

    project = tmp_path / "memory" / "fake"
    local = project / "runs" / "r1"
    folder = local / "telemetry"
    runtime.Runtime(game, CrateHarness(), str(local / "trace1.jsonl"), client=FakeJev("open_crate"), max_seconds=20,
                    telemetry=str(folder)).run()
    with open(folder / "decisions1.jsonl", encoding="utf-8") as f:
        first = json.loads(f.readline())["wall"]
    media = folder / "media"
    media.mkdir()
    (media / "frames-1.jsonl").write_text("\n".join(json.dumps(
        {"frame": i, "t": 99.0 - i, "wall": first - 1 + i * 0.2, "pts_ms": i * 200}) for i in range(20)))
    (folder / "llm.jsonl").write_text(json.dumps({"call": 1, "path": "/v1/messages", "started": first - 5,
                                                  "ended": first - 0.5}) + "\n")
    (media / "frames-2.jsonl").write_text("\n".join(json.dumps(
        {"frame": i, "t": float(i), "wall": first + 10 + i * 0.2, "pts_ms": i * 200}) for i in range(5)))
    (folder / "run.json").write_text(json.dumps({"run": "r1", "run_uid": "u1", "recording": {"episodes": [
        {"episode": 1, "frames": 20, "wall0": first - 1, "wall_end": first + 2.8, "seconds": 3.8},
        {"episode": 2, "frames": 5, "wall0": first + 10, "wall_end": first + 10.8, "seconds": 1.8}]}}))
    dataset.export_project(str(project), str(tmp_path / "out"))
    run = tmp_path / "out" / "runs" / "r1-u1"
    found = training_data.decisions(str(run))
    assert found and all(d["applied_wall"] >= d["wall"] for d in found) and found[0]["wall"] == first
    line = training_data.frame_line_at(str(run), first)
    assert line["frame"] == 5 and line["episode"] == 1  # (by wall: t runs backwards here, as after a restart)
    episode, at = training_data.video_time(str(run), first)
    assert episode == 1 and at == pytest.approx(1.0)  # (in episodes/1/recording.mp4)
    # an episode's first events come a little before its first frame: they belong to its video, not the one before
    episode, at = training_data.video_time(str(run), first + 9.85)
    assert episode == 2 and at == pytest.approx(-0.15)
    line = training_data.frame_line_at(str(run), first + 9.85)
    assert line["episode"] == 2 and line["frame"] == 0
    assert [training_data.video_time(str(run), first + s)[0] for s in (4, 10.5)] == [1, 2]
    llm, dec = training_data.calls_between(str(run), first - 1, first + 0.01)
    assert [c["call"] for c in llm] == [1] and dec and dec[0]["call"] == found[0]["call"]
    spans = telemetry.episode_spans(str(local))
    assert spans[0]["episode"] == "trace1" and spans[0]["wall_start"] <= first <= spans[0]["wall_end"]
    assert spans[0]["t_start"] is not None  # (the first event with a game time: the start event has none)
