"""Looking at the game's picture (autopilot/vision.py): the recording's latest frames kept, an episode's frames sent
with the agent's questions (never the request) to a vision model of the agent's provider, the description back as
text, and a claim about how something looks settled only on one; the user alerted when the picture can't be looked
at."""

import base64
import http.server
import json
import os
import threading

import pytest

from autopilot import cli, media, telemetry, vision
from autopilot.agents import session


@pytest.fixture
def game():
    from test_autopilot import FakeLink

    from autopilot.kit.game import Game

    return Game(FakeLink())


class Upstream:
    """A vision model's API on this machine: Anthropic's Messages (/v1/messages) and OpenAI's chat completions
    (/chat/completions, /openai/chat/completions). refuse: models answered 400 as Aura's relay answers an unpriced
    one."""

    def __init__(self, refuse=()):
        self.seen = []  # (path, headers, body)
        up = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
                up.seen.append((self.path, {k.lower(): v for k, v in self.headers.items()}, body))
                if body.get("model") in refuse:
                    self.answer(400, {"type": "error", "error": {"type": "invalid_request_error",
                                                                 "message": f"Model not available through Aura: "
                                                                            f"{body['model']}"}})
                elif self.path.endswith("/v1/messages"):
                    self.answer(200, {"id": "msg_1", "type": "message", "role": "assistant", "model": body["model"],
                                      "content": [{"type": "text", "text": "1. The arm swings down."}],
                                      "stop_reason": "end_turn", "stop_sequence": None,
                                      "usage": {"input_tokens": 900, "output_tokens": 40}})
                else:
                    self.answer(200, {"id": "c1", "model": body["model"],
                                      "choices": [{"message": {"role": "assistant", "content": "1. A door opens."}}],
                                      "usage": {"prompt_tokens": 800, "completion_tokens": 30}})

            def answer(self, status, data):
                raw = json.dumps(data).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

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


class FakeRecorder:
    """A media.Recorder's face to the describer: its frames, whether the plugin captures, its fps."""

    def __init__(self, features=("media",), received=True):
        self.link = type("L", (), {"hello": {"features": list(features)}})()
        self.frames = vision.Frames()
        self.started = bool(received)
        self.received = 10 if received else 0
        self.problem = None if received else "recording couldn't start: no picture: the game's window is minimized"
        self.fps = media.FPS
        self.asked = []

    def set_fps(self, fps):
        self.asked.append(fps)
        self.fps = fps

    def missing(self):
        return self.problem


def frames(rec, n=20, wall0=100.0, t0=5.0, step=0.2):
    for i in range(n):
        rec.frames.add(f"jpeg{i}".encode(), t0 + i * step, wall0 + i * step, 960, 540)


def keys(monkeypatch, **env):
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "OPENAI_API_KEY", "CODEX_API_KEY",
                 "OPENAI_BASE_URL", "OPENROUTER_API_KEY", "OPENROUTER_BASE_URL", "AUTOPILOT_VISION_MODEL",
                 "GEMINI_API_KEY", "GOOGLE_BASE_URL", "GOOGLE_API_KEY", "GOOGLE_GENERATIVE_AI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)


# -- the frames ----------------------------------------------------------------------------------------------------

def test_the_latest_frames_are_kept_up_to_their_limit():
    kept = vision.Frames(limit=30)
    for i in range(5):
        kept.add(b"0123456789", t=float(i), wall=100.0 + i)
    assert [f["t"] for f in kept.items] == [2.0, 3.0, 4.0] and kept.bytes == 30
    assert kept.dropped_before == 101.0
    assert [f["t"] for f in kept.between(102.5, 104.0)] == [3.0, 4.0]


def test_the_frames_described_are_spread_from_first_to_last():
    assert vision.pick(list(range(10)), 4) == [0, 3, 6, 9]
    assert vision.pick(list(range(3)), 8) == [0, 1, 2]
    assert vision.pick(list(range(9)), 1) == [4]


def test_the_recorder_keeps_its_frames_and_films_closer_when_asked(tmp_path):
    from test_telemetry import SendLink, jpeg

    link = SendLink({"project": "fake", "features": ["media"]})
    with media.Recorder(link, str(tmp_path)) as rec:
        for n in range(3):
            link.frame_listeners[0]({"n": n, "t": 1.0 + n, "wall": 50.0 + n, "width": 64, "height": 48,
                                     "jpeg": base64.b64encode(jpeg()).decode()})
        d = vision.Describer(rec, "anthropic")
        with d.filming(40) as changed:  # (more than the most: the most)
            assert changed and rec.fps == vision.FILM_FPS[1]
        assert rec.fps == media.FPS
    starts = [a["fps"] for op, a in link.requests if op == "record" and a["action"] == "start"]
    assert starts == [media.FPS, vision.FILM_FPS[1], media.FPS]  # (a start while recording: the new settings)
    assert [f["t"] for f in rec.frames.items] == [1.0, 2.0, 3.0] and rec.frames.items[0]["width"] == 64


# -- which model looks ------------------------------------------------------------------------------------------------

def test_each_provider_has_its_vision_model_or_says_why_not(monkeypatch):
    keys(monkeypatch, ANTHROPIC_API_KEY="sk-a", OPENAI_API_KEY="sk-o", OPENROUTER_API_KEY="sk-r")
    (wire, root, key, model, _), why = vision.route("anthropic", "claude-opus-5-5")
    assert (wire, root, key, model, why) == ("anthropic", "https://api.anthropic.com", "sk-a", vision.MODEL, None)
    route, _ = vision.route("openai", "gpt-6.1-sol")
    assert route[0] == "openai" and route[3] == "gpt-6.1-sol"  # (the agent's own model looks)
    assert vision.route("openrouter")[0][3] == vision.OPENROUTER_MODEL
    assert vision.route("deepseek") == (None, vision.NO_VISION["deepseek"])
    assert vision.route("google")[0] is None and "no google key" in vision.route("google")[1]
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:9/anthropic/")  # (the run's proxy, Aura's relay)
    monkeypatch.setenv("AUTOPILOT_VISION_MODEL", "claude-other")
    route, _ = vision.route("anthropic")
    assert route[1] == "http://127.0.0.1:9/anthropic" and route[3] == "claude-other"


def test_what_cant_look_is_known_as_the_run_starts(monkeypatch):
    keys(monkeypatch, ANTHROPIC_API_KEY="sk-a")
    assert vision.Describer(FakeRecorder(), "anthropic").cannot() is None
    headless = vision.Describer(FakeRecorder(features=()), "anthropic")
    assert "headless" in headless.cannot() and "window shown" in headless.alert(headless.cannot())
    blind = vision.Describer(FakeRecorder(), "deepseek")
    assert "DeepSeek" in blind.cannot() and "choose an LLM provider" in blind.alert(blind.cannot())
    hidden = vision.Describer(FakeRecorder(received=False), "anthropic")
    assert hidden.cannot() is None and "minimized" in hidden.unavailable()


# -- describing ---------------------------------------------------------------------------------------------------

def test_frames_are_described_with_the_questions_only(upstream, monkeypatch, tmp_path):
    keys(monkeypatch, ANTHROPIC_API_KEY="sk-a", ANTHROPIC_BASE_URL=upstream.url)
    rec = FakeRecorder()
    frames(rec)
    usage = telemetry.Usage()
    d = vision.Describer(rec, "anthropic", "claude-opus-5-5", usage=usage, log=str(tmp_path))
    text, ok = d.describe(100.0, 104.0, ["What does the left arm do?"], count=4, t0=6.0, t1=8.0, label="episode 2")
    assert ok and "The arm swings down" in text and "episode 2" in text
    path, headers, body = upstream.seen[0]
    assert path == "/v1/messages" and headers["x-api-key"] == "sk-a" and body["model"] == vision.MODEL
    assert body["output_config"] == {"effort": vision.EFFORT} and body["system"] == vision.SYSTEM
    parts = body["messages"][0]["content"]
    images = [p for p in parts if p["type"] == "image"]
    assert len(images) == 4 and base64.b64decode(images[0]["source"]["data"]) == b"jpeg5"  # (t 6.0: frame 5)
    assert base64.b64decode(images[-1]["source"]["data"]) == b"jpeg15"  # (t 8.0)
    asked = parts[-1]["text"]
    assert "1. What does the left arm do?" in asked and vision.SWEEP in asked
    assert "Frame 2: game time 6.60 s (0.60 s after the one before)" in [p.get("text") for p in parts]
    assert usage.totals()["llm"]["input"] == 900  # (not through the run's proxy: counted here)
    line = json.loads((tmp_path / vision.LOG).read_text(encoding="utf-8"))
    assert line["questions"] == ["What does the left arm do?"] and [f["t"] for f in line["frames"]][0] == 6.0
    assert "jpeg" not in json.dumps(line["frames"])


def test_a_model_auras_relay_refuses_falls_back_to_the_agents(monkeypatch):
    up = Upstream(refuse=("claude-unpriced",))
    try:
        keys(monkeypatch, ANTHROPIC_API_KEY="run-token", ANTHROPIC_BASE_URL=up.url,
             AUTOPILOT_VISION_MODEL="claude-unpriced")
        rec = FakeRecorder()
        frames(rec)
        d = vision.Describer(rec, "anthropic", "claude-opus-5-5")
        assert d.describe(100.0, 104.0, ["What moves?"])[1]
        assert d.describe(100.0, 104.0, ["What moves?"])[1]
        assert [b["model"] for _, _, b in up.seen] == ["claude-unpriced", vision.FALLBACK, vision.FALLBACK]
        refused = Upstream(refuse=(vision.FALLBACK,))  # (the fallback itself refused: the agent is told)
        try:
            monkeypatch.setenv("ANTHROPIC_BASE_URL", refused.url)
            monkeypatch.delenv("AUTOPILOT_VISION_MODEL")
            text, ok = vision.Describer(rec, "anthropic").describe(100.0, 104.0, ["?"])
            assert not ok and "couldn't be described" in text and len(refused.seen) == 1
        finally:
            refused.close()
    finally:
        up.close()


def test_openai_compatible_providers_get_data_urls(upstream, monkeypatch):
    keys(monkeypatch, OPENAI_API_KEY="sk-o", OPENAI_BASE_URL=upstream.url, GEMINI_API_KEY="g",
         GOOGLE_BASE_URL=upstream.url)
    rec = FakeRecorder()
    frames(rec, n=3)
    text, ok = vision.Describer(rec, "openai", "gpt-6.1-sol").describe(0, 1e12, ["What opens?"])
    assert ok and "A door opens" in text
    path, headers, body = upstream.seen[0]
    assert path == "/chat/completions" and headers["authorization"] == "Bearer sk-o"
    assert body["model"] == "gpt-6.1-sol" and body["max_completion_tokens"] == vision.MAX_TOKENS
    pictures = [p for p in body["messages"][1]["content"] if p["type"] == "image_url"]
    assert len(pictures) == 3 and pictures[0]["image_url"]["url"] == "data:image/jpeg;base64," + base64.b64encode(
        b"jpeg0").decode()
    assert vision.Describer(rec, "google", "gemini-4-argon").describe(0, 1e12, ["?"])[1]
    assert upstream.seen[1][0] == "/openai/chat/completions" and "max_tokens" in upstream.seen[1][2]


def test_what_isnt_there_is_said_not_described(monkeypatch):
    keys(monkeypatch, ANTHROPIC_API_KEY="sk-a", ANTHROPIC_BASE_URL="http://127.0.0.1:9")
    rec = FakeRecorder()
    frames(rec)
    d = vision.Describer(rec, "anthropic")
    text, ok = d.describe(100.0, 104.0, ["?"], t0=50, t1=60, label="episode 1")
    assert not ok and "show game times 5.00 to 8.80" in text
    text, ok = d.describe(500.0, 600.0, ["?"], label="episode 9")
    assert not ok and "No frames of episode 9" in text
    rec.frames.dropped_before = 700.0
    assert "aren't kept any more" in d.describe(500.0, 600.0, ["?"])[0]
    assert "Can't look" in vision.Describer(FakeRecorder(features=()), "anthropic").describe(0, 1, ["?"])[0]


def test_the_call_log_keeps_no_pictures():
    request = {"messages": [{"role": "user", "content": [
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "QUJD" * 10}},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + "QUJD" * 10}},
        {"type": "text", "text": "go"}]}]}
    kept = telemetry.without_pictures(request)["messages"][0]["content"]
    assert kept[0]["source"]["data"] == kept[1]["image_url"]["url"] == "<image/jpeg: 30 bytes>"
    assert kept[2] == {"type": "text", "text": "go"}
    assert request["messages"][0]["content"][0]["source"]["data"] == "QUJD" * 10  # (the request sent is unchanged)


# -- the agent's tool, the report and the user's alert ----------------------------------------------------------------

def trace(run_dir, n, wall0, wall1):
    with open(os.path.join(run_dir, f"trace{n}.jsonl"), "w", encoding="utf-8") as f:
        f.write(json.dumps({"kind": "start", "wall": wall0}) + "\n")
        f.write(json.dumps({"kind": "end", "wall": wall1, "t": 9.0, "reason": "time"}) + "\n")


def played(game, tmp_path, monkeypatch, describer):
    from autopilot import codex

    monkeypatch.setattr(codex, "MEMORY", str(tmp_path))
    s = session.Session(game, "fake", "Verify that the guard waves", "r1")
    s.describer = describer
    s.episodes, s.played, s.most_decisions = 2, 2, 5
    trace(s.run_dir, 1, 10.0, 20.0)
    trace(s.run_dir, 2, 100.0, 104.0)
    return s


def test_describe_frames_looks_at_the_episode_asked_for(game, tmp_path, monkeypatch, upstream):
    keys(monkeypatch, ANTHROPIC_API_KEY="sk-a", ANTHROPIC_BASE_URL=upstream.url)
    rec = FakeRecorder()
    frames(rec)  # (episode 2's frames)
    s = played(game, tmp_path, monkeypatch, vision.Describer(rec, "anthropic"))
    assert "Not reported" in s.do_report({"outcome": "CONFIRMED", "summary": "x", "evidence": [], "visual": True})
    text = s.describe_frames(["How does the guard's hand move?"], start=6.0, end=7.0)
    assert "frames of 6 (episode 2), at game times 6.00, " in text and s.described == 1
    # Times or episodes the agent got wrong are the agent's to fix: no alert
    wrong_times = s.describe_frames(["?"], start=50, end=60)
    assert wrong_times == "No frames of episode 2 show game times 50 to 60 (its frames show game times 5.00 to 8.80 s)."
    assert "No episode 5" in s.describe_frames(["?"], episode=5)
    assert not s.alerts
    assert "No frames of episode 1" in s.describe_frames(["?"], episode=1)
    assert "Reported" in s.do_report({"outcome": "CONFIRMED", "summary": "x", "evidence": [], "visual": True})
    # Episode 1 had no picture at all (its window minimized or hidden while it played): the user is told
    assert len(s.alerts) == 1 and "minimized or hidden" in s.alerts[0] and "window shown" in s.alerts[0]


def test_a_run_that_cant_look_alerts_the_user_and_stays_inconclusive(game, tmp_path, monkeypatch):
    keys(monkeypatch, ANTHROPIC_API_KEY="sk-a")
    s = played(game, tmp_path, monkeypatch, vision.Describer(FakeRecorder(features=()), "anthropic"))
    assert "headless" in s.looking_note() and "INCONCLUSIVE" in s.looking_note()
    assert "Can't look" in s.describe_frames(["What moves?"])
    refused = s.do_report({"outcome": "DENIED", "summary": "x", "evidence": [], "visual": True})
    assert "Not reported" in refused and "Report INCONCLUSIVE" in refused
    assert "Reported" in s.do_report({"outcome": "INCONCLUSIVE", "summary": "x", "evidence": [], "visual": True})
    assert len(s.alerts) == 1 and s.alerts[0].startswith("The recording could not be analyzed: ")
    report = session.finish(s, s.started, None, "")
    assert report["alerts"] == s.alerts
    frame = cli.result_frame(report, "Verify that the guard waves")
    assert frame["verdict"] == "INCONCLUSIVE" and frame["alerts"] == s.alerts
    assert "alerts" not in cli.result_frame({"outcome": "CONFIRMED", "summary": "x"}, "Verify that x")


def test_a_provider_without_vision_alerts_too(game, tmp_path, monkeypatch):
    keys(monkeypatch)
    s = played(game, tmp_path, monkeypatch, vision.Describer(FakeRecorder(), "deepseek"))
    assert "Reported" in s.do_report({"outcome": "INCONCLUSIVE", "summary": "x", "evidence": [], "visual": True})
    assert "DeepSeek" in s.alerts[0] and "choose an LLM provider" in s.alerts[0]


def test_an_episode_is_filmed_closer_when_asked(game, tmp_path, monkeypatch):
    rec = FakeRecorder()
    s = played(game, tmp_path, monkeypatch, vision.Describer(rec, "anthropic"))
    monkeypatch.setattr(session.Session, "_run_harness", lambda self, *a: f"played at {rec.fps}")
    assert s.run_harness(10, frames_fps=15) == "played at 15.0" and rec.fps == media.FPS
    assert s.run_harness(10) == f"played at {media.FPS}" and rec.asked == [15.0, media.FPS]


def test_every_agent_has_the_tool():
    from autopilot.agents import toolserver

    names = [n for n, _, _ in session.TOOL_SPECS]
    assert "describe_frames" in names and set(toolserver.TOOLS) == set(names)
    report = dict((n, s) for n, _, s in session.TOOL_SPECS)["report"]
    assert "visual" in report["properties"] and set(session.REPORT_FIELDS) == set(report["properties"])
    assert "describe_frames" in session.SYSTEM
