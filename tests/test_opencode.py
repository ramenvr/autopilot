"""The OpenCode agent (autopilot/agents/opencode.py): the game tools over localhost, the host's event lines, its config,
permissions and environment. No OpenCode, no Bun, no models: a fake host stands in where one runs."""

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest
from test_autopilot import FakeLink

from autopilot import codex, engines
from autopilot.agents import opencode
from autopilot.agents import session as sessionmod
from autopilot.agents.toolserver import ToolServer
from autopilot.kit.game import Game


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setattr(codex, "MEMORY", str(tmp_path / "memory"))
    game = Game(FakeLink())
    game.look = {"x": 0.1, "y": 0.1}
    return sessionmod.Session(game, "fake", "Open the crate", "r1")


def post(url, args):
    req = urllib.request.Request(url, data=json.dumps(args).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8")


def test_the_host_reaches_the_game_tools_over_localhost(session):
    said = []
    server = ToolServer(session, say=said.append)
    try:
        out = post(server.url + "/probe", {"code": "print(len(game.all_actors()))\ngame.loc(game.actors('Crate')[0])"})
        assert "2" in out and "800" in out and "min used]" in out
        assert said == ["Trying things in the game"] and session.game.link.paused
        assert "no run_harness episode" in post(server.url + "/report", {"outcome": "achieved", "summary": "Opened.",
                                                                         "evidence": ["trace1"]})
        assert session.report is None
        assert "doesn't exist yet" in post(server.url + "/run_harness", {"seconds": 10})
        session.steering.add("open the other one")
        assert '"open the other one"' in post(server.url + "/snapshot", {}) and session.steered
        assert "No tool 'shell'" in post(server.url + "/shell", {})
        with pytest.raises(urllib.error.HTTPError):  # (only who has the url's secret can call)
            post(server.url.rsplit("/", 1)[0] + "/wrong/snapshot", {})
    finally:
        server.close()


def decide(rules, action, resource):
    """OpenCode's rule matching: whole-value wildcards (* any characters, ? one), case-insensitive on Windows, the
    last matching rule wins, and no match asks (which a run without a client can't answer: counted as deny)."""
    def match(pattern, value):
        rx = "".join(".*" if c == "*" else "." if c == "?" else re.escape(c) for c in pattern)
        return re.fullmatch(rx, value, re.I if os.name == "nt" else 0) is not None

    effect = "ask"
    for r in rules:
        if match(r["action"], action) and match(r["resource"], resource):
            effect = r["effect"]
    return effect


def test_the_permissions_hold_the_agent_to_the_games_folder(session):
    rules = opencode.permissions(session.root, session.run_rel)
    root = os.path.realpath(session.root).replace("\\", "/")
    package = os.path.realpath(sessionmod.PACKAGE).replace("\\", "/")
    try:
        kit_rel = os.path.relpath(sessionmod.PACKAGE, session.root).replace("\\", "/")
    except ValueError:  # (the game's folder on another drive: the kit is named only by its absolute path)
        kit_rel = None
    allowed = [("read", "codex/README.md"), ("read", "runs/r0/report.json"), ("read", f"{root}/notes.md"),
               ("read", f"{package}/KIT.md"), ("read", f"{package}/kit/control.py"),
               ("glob", "codex/**/*.txt"), ("grep", "Crate"), ("external_directory", f"{package}/kit/*"),
               ("edit", "runs/r1/harness.py"), ("edit", "notes.md"), ("edit", "lib.py"), ("edit", f"{root}/lib.py"),
               ("edit", f"{root}/runs/r1/harness.py")] + [(n, "*") for n, _, _ in sessionmod.TOOL_SPECS]
    denied = [("read", "../../README.md"), ("read", "C:/Windows/win.ini"), ("read", "/etc/passwd"),
              ("read", f"{package}/agents/session.py"), ("read", ".env"),
              ("read", "codex/.env.local"), ("edit", "codex/README.md"), ("edit", "runs/r0/harness.py"),
              ("edit", "../lib.py"), ("edit", f"{package}/kit/control.py"), ("external_directory", "C:/Windows/*"),
              ("shell", "ls"), ("webfetch", "https://example.com"), ("websearch", "x"), ("subagent", "general"),
              ("question", "*"), ("skill", "x"), ("execute", "*")]
    if kit_rel:
        allowed.append(("read", f"{kit_rel}/KIT.md"))
        denied.append(("read", f"{kit_rel}/agents/session.py"))
    assert [a for a in allowed if decide(rules, *a) != "allow"] == []
    assert [d for d in denied if decide(rules, *d) != "deny"] == []


def test_the_host_config_carries_the_tools_instructions_and_model(session, monkeypatch):
    system = opencode.system_text(engines.of(session.game), session.run_rel)
    assert "runs/r1/harness.py" in system and "no shell and no web" in system and "{run}" not in system
    cfg = opencode.host_config(session, "http://127.0.0.1:1/t", "claude-opus-5-5", system, fast=True)
    assert cfg["directory"] == os.path.realpath(session.root) and cfg["provider"] == "anthropic"
    assert cfg["fast"] is True and cfg["variant"] == "high" and cfg["system"] == system
    assert [t["name"] for t in cfg["tools"]] == ["probe", "run_harness", "snapshot", "describe_frames",
                                                 "restart_level", "report"]
    assert cfg["tools"][0]["input"]["required"] == ["code"] and "shell" not in cfg["keep_tools"]
    assert opencode.host_config(session, "u", "claude-sonnet-5-5", system, fast=True)["fast"] is False
    assert opencode.host_config(session, "u", "claude-opus-5-5", system, fast=False)["fast"] is False
    # (by default, as AUTOPILOT_FAST says at the run: on unless it is 0)
    monkeypatch.delenv("AUTOPILOT_FAST", raising=False)
    assert opencode.host_config(session, "u", "claude-opus-5-5", system)["fast"] is True
    monkeypatch.setenv("AUTOPILOT_FAST", "0")
    assert opencode.host_config(session, "u", "claude-opus-5-5", system)["fast"] is False
    json.dumps(cfg)  # (one JSON line)


def test_the_provider_and_its_model_are_the_ones_set_before_the_run(session, monkeypatch, tmp_path):
    """The provider picked in the plugins' first-run setup (autopilot.env, loaded before each run) once took effect only
    once the agent service restarted, and its model never: an OpenAI key went to Anthropic's API, and every call
    got a 401."""
    system = opencode.system_text(engines.of(session.game), session.run_rel)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-anthropic")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-openai")
    assert opencode.current_provider() == "anthropic" and sessionmod.model("anthropic") == "claude-opus-5-5"
    monkeypatch.setenv("AUTOPILOT_OPENCODE_PROVIDER", " OpenAI ")
    assert opencode.current_provider() == "openai" and sessionmod.model("openai") == "gpt-6.1-sol"
    assert opencode.host_config(session, "u", sessionmod.model("openai"), system)["provider"] == "openai"
    env = opencode.host_env(str(tmp_path))
    assert env["OPENAI_API_KEY"] == "sk-test-openai" and "ANTHROPIC_API_KEY" not in env
    monkeypatch.setenv("AUTOPILOT_MODEL", "gpt-6-sol")
    assert sessionmod.model("openai") == "gpt-6-sol"


def test_the_coding_agent_named_runs_whatever_the_provider(monkeypatch):
    """The setup's Coding Agent is the one that runs: Claude Code once gave way to OpenCode when OpenCode's provider
    wasn't Anthropic, which a picker of agents must not do."""
    from autopilot import agents
    from autopilot.agents import claude_code

    monkeypatch.setenv("AUTOPILOT_AGENT", "claude")
    monkeypatch.setenv("AUTOPILOT_OPENCODE_PROVIDER", "openrouter")
    assert agents.select() is claude_code
    monkeypatch.setenv("AUTOPILOT_AGENT", "opencode")
    assert agents.select() is opencode and agents.select("claude") is claude_code  # (a run through Aura names it)


@pytest.mark.parametrize("provider,key,model", [
    ("opencode", "OPENCODE_API_KEY", "claude-opus-5-5"),
    ("openrouter", "OPENROUTER_API_KEY", "anthropic/claude-opus-5.5"),
    ("deepseek", "DEEPSEEK_API_KEY", "deepseek-v4-pro"),
    ("google", "GEMINI_API_KEY", "gemini-4-argon"),
])
def test_each_provider_the_setup_offers_gets_its_key_and_model(session, monkeypatch, tmp_path, provider, key, model):
    """OpenCode Zen, OpenRouter, DeepSeek and Gemini: the key the setup saves reaches OpenCode, and no other; Zen's
    (OPENCODE_API_KEY) too, though the rest of OPENCODE_* (the user's own OpenCode settings) never does."""
    for other in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENCODE_API_KEY", "OPENROUTER_API_KEY", "DEEPSEEK_API_KEY",
                  "GEMINI_API_KEY"):
        monkeypatch.setenv(other, f"test-{other.lower()}")
    monkeypatch.setenv("OPENCODE_CONFIG", "C:/the/users/opencode.json")
    monkeypatch.setenv("AUTOPILOT_OPENCODE_PROVIDER", provider)
    assert opencode.current_provider() == provider and sessionmod.model(provider) == model
    assert opencode.PROVIDER_KEYS[provider][0] == key
    system = opencode.system_text(engines.of(session.game), session.run_rel)
    assert opencode.host_config(session, "u", model, system)["provider"] == provider
    env = opencode.host_env(str(tmp_path))
    assert env[key] == f"test-{key.lower()}" and "OPENCODE_CONFIG" not in env
    assert not [k for k in env if k.endswith("_API_KEY") and k != key]


def test_the_host_config_carries_a_moved_provider_api_root(session, monkeypatch):
    system = opencode.system_text(engines.of(session.game), session.run_rel)
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    assert opencode.base_url("anthropic") is None
    assert opencode.host_config(session, "u", "claude-opus-5-5", system, provider="anthropic")["base_url"] is None
    # (Anthropic's SDKs take the root without /v1; OpenCode's Anthropic provider wants it with)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://gateway.test/anthropic/")
    assert opencode.base_url("anthropic") == "https://gateway.test/anthropic/v1"
    cfg = opencode.host_config(session, "u", "claude-opus-5-5", system, provider="anthropic")
    assert cfg["base_url"] == "https://gateway.test/anthropic/v1"
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://gateway.test/v1")
    assert opencode.base_url("anthropic") == "https://gateway.test/v1"
    monkeypatch.setenv("OPENAI_BASE_URL", "https://proxy.test/v1")
    assert opencode.base_url("openai") == "https://proxy.test/v1"
    monkeypatch.setenv("GOOGLE_VERTEX_BASE_URL", "https://vertex.test")
    assert opencode.base_url("google-vertex") == "https://vertex.test"


def test_the_host_gets_the_providers_key_and_none_of_the_users_opencode_setup(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-anthropic")
    monkeypatch.setenv("TYPESAFE_API_KEY", "ts-test-key")
    monkeypatch.setenv("GITHUB_TOKEN", "gh-test-token")
    monkeypatch.setenv("OPENCODE_CONFIG", "C:/somewhere/opencode.json")
    env = opencode.host_env(str(tmp_path), provider="anthropic")
    assert env["ANTHROPIC_API_KEY"] == "sk-test-anthropic"
    assert "TYPESAFE_API_KEY" not in env and "GITHUB_TOKEN" not in env and "OPENCODE_CONFIG" not in env
    for name in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME", "OPENCODE_TEST_HOME", "TEMP"):
        assert env[name].startswith(str(tmp_path)) and os.path.isdir(env[name])
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test")
    assert opencode.host_env(str(tmp_path), provider="openrouter")["OPENROUTER_API_KEY"] == "or-test"


def test_bun_is_found_or_said_how_to_get(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOPILOT_BUN", "C:/tools/bun.exe")
    assert opencode.bun() == "C:/tools/bun.exe"
    monkeypatch.delenv("AUTOPILOT_BUN")
    monkeypatch.setattr(opencode.shutil, "which", lambda name: None)
    monkeypatch.setattr(opencode.os.path, "expanduser", lambda p: str(tmp_path))
    with pytest.raises(RuntimeError, match="needs Bun"):
        opencode.bun()


def test_the_events_say_what_the_model_does(session):
    said = []
    events = opencode.Events(session, say=said.append)
    for ev in [{"type": "ready", "session": "ses_1", "model": "claude-opus-5-5-fast", "variant": "medium"},
               {"type": "tool", "name": "grep", "input": '{"pattern":"Crate"}'},
               {"type": "tool", "name": "probe", "input": '{"code":"1"}'},
               {"type": "tool", "name": "write", "input": '{"path":"runs/r1/harness.py"}'},
               {"type": "tool_failed", "name": "read", "error": "Permission denied: read"},
               {"type": "step", "cost": 0.01, "tokens": {}}, {"type": "step", "cost": 0.02, "tokens": {}},
               {"type": "text", "text": "Reported."},
               {"type": "usage", "cost": 0.5, "tokens": {"input": 10, "output": 20, "reasoning": 5,
                                                         "cache": {"read": 1000, "write": 100}}}]:
        events.line(json.dumps(ev))
    events.line("not json")
    assert said == ["Reading the game's code", "Writing the harness"]
    assert events.model == "claude-opus-5-5-fast" and events.turns == 2 and events.last_text == "Reported."
    assert events.cost == 0.5 and events.tokens == {"input": 10, "output": 20, "reasoning": 5, "cacheRead": 1000,
                                                    "cacheWrite": 100}
    assert events.take_idle() is None
    events.line(json.dumps({"type": "idle", "outcome": "succeeded", "cost": 0.75, "tokens": {"input": 11}}))
    assert events.take_idle()["outcome"] == "succeeded" and events.cost == 0.75 and events.take_idle() is None
    events.line(json.dumps({"type": "error", "fatal": True, "message": "Error: no model anthropic/x\n  at ..."}))
    assert events.fatal == "Error: no model anthropic/x"
    with open(os.path.join(session.run_dir, "implementer.log"), encoding="utf-8") as f:
        log = f.read()
    assert "grep: " in log and "read failed: Permission denied" in log and "probe:" not in log


FAKE_HOST = r'''
import json, sys
config = json.loads(sys.stdin.readline())
with open(sys.argv[1], "w") as f:
    json.dump(config, f)
def say(ev):
    print(json.dumps(ev), flush=True)
say({"type": "ready", "session": "ses_1", "model": config["model"], "variant": config.get("variant")})
for line in sys.stdin:
    command = json.loads(line)
    if command["type"] == "close":
        break
    if command["type"] == "prompt":
        say({"type": "tool", "name": "read", "input": "{}"})
        say({"type": "step", "cost": 0.1, "tokens": {}})
        say({"type": "text", "text": "got: " + command["text"][:40]})
        say({"type": "idle", "outcome": "succeeded", "cost": 0.25, "tokens": {"input": 5}})
'''


def test_a_run_ends_when_the_turn_does_and_steering_after_it_is_sent_on(session, tmp_path):
    fake = tmp_path / "fake_host.py"
    fake.write_text(FAKE_HOST, encoding="utf-8")
    seen = tmp_path / "config.json"
    events = opencode.Events(session)
    session.steering.add("open the other one")  # (typed after the last tool result: goes as a message of its own)
    with open(tmp_path / "err.log", "wb") as err:
        why = opencode.run_host([sys.executable, str(fake), str(seen)], {"model": "m", "variant": "medium"},
                                "Request: open the crate", dict(os.environ), events, session, time.time() + 60, err,
                                cwd=str(tmp_path))
    assert why is None and events.model == "m" and events.turns == 2
    assert events.last_text.startswith("got: The user, watching the game, steers")
    assert events.cost == 0.25 and json.loads(seen.read_text())["model"] == "m"


def test_a_failed_turn_a_stop_and_the_deadline_end_the_run(session, tmp_path):
    failing = tmp_path / "failing_host.py"
    failing.write_text("import json, sys\nsys.stdin.readline(); sys.stdin.readline()\n"
                       "print(json.dumps({'type': 'idle', 'outcome': 'failed', 'error': 'invalid x-api-key'}),"
                       " flush=True)\nsys.stdin.read()\n", encoding="utf-8")
    slow = tmp_path / "slow_host.py"
    slow.write_text("import sys, time\nsys.stdin.readline()\ntime.sleep(600)\n", encoding="utf-8")
    crashing = tmp_path / "crashing_host.py"
    crashing.write_text("import json, sys\nsys.stdin.readline()\nprint(json.dumps({'type': 'error', 'fatal': True, "
                        "'message': 'Error: no model anthropic/x'}), flush=True)\nsys.exit(1)\n", encoding="utf-8")

    def run(script, stop=False, seconds=60):
        session.stop = lambda: stop
        with open(tmp_path / "err.log", "ab") as err:
            return opencode.run_host([sys.executable, str(script)], {}, "hi", dict(os.environ),
                                     opencode.Events(session), session, time.time() + seconds, err, cwd=str(tmp_path))

    assert run(failing) == "the model's turn failed: invalid x-api-key"
    assert run(slow, stop=True) == "stopped"
    assert run(slow, seconds=1) == "timed out"
    assert run(crashing) == "OpenCode failed: Error: no model anthropic/x"


def test_a_host_that_dies_says_why_not_broken_pipe(session, tmp_path):
    # (the host ends before it reads its config: the write of a config bigger than the pipe's buffer fails, Broken
    # pipe on Windows and Linux alike; the reason is its exit code and stderr, which go to opencode.log)
    dying = tmp_path / "dying_host.py"
    dying.write_text("import sys\nsys.stderr.write('error: Cannot find package \"@opencode/sdk\"\\n')\nsys.exit(3)\n",
                     encoding="utf-8")
    exiting = tmp_path / "exiting_host.py"
    exiting.write_text("import sys\nsys.stdin.readline()\nsys.stderr.write('panic: oops\\nsecond line\\n')\n"
                       "sys.exit(1)\n", encoding="utf-8")
    notes = []
    session.note = notes.append
    big = {"system": "x" * 2_000_000}  # (far past any pipe's buffer: the write waits for a reader that never comes)

    def run(script, config):
        with open(tmp_path / f"{script.stem}.log", "wb") as err:
            return opencode.run_host([sys.executable, str(script)], config, "hi", dict(os.environ),
                                     opencode.Events(session), session, time.time() + 60, err, cwd=str(tmp_path))

    why = run(dying, big)
    assert why.startswith("OpenCode exited (3) before it took the request (") and "Errno" in why
    assert why.endswith(': error: Cannot find package "@opencode/sdk"')
    assert any("exit code 3" in n and "@opencode/sdk" in n for n in notes)
    assert run(exiting, {}) == "OpenCode exited (1): panic: oops | second line"


def test_packages_are_installed_again_until_an_install_finishes(tmp_path, monkeypatch):
    host = tmp_path / "host"
    (host / "node_modules" / "@opencode" / "sdk").mkdir(parents=True)  # (left by an install cut short)
    (host / "bun.lock").write_text("lock v1", encoding="utf-8")
    calls = []
    result = {"code": 0}

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, result["code"], stdout="out", stderr="error: disk full")

    monkeypatch.setattr(opencode.subprocess, "run", fake_run)
    assert not opencode.installed(str(host))  # (its folders are there, but no install finished)
    opencode.ensure_installed("bun", str(host))
    assert calls == [["bun", "install", "--frozen-lockfile", "--force"]] and opencode.installed(str(host))
    opencode.ensure_installed("bun", str(host))
    assert len(calls) == 1  # (finished for this lockfile: nothing to do)
    (host / "bun.lock").write_text("lock v2", encoding="utf-8")  # (a new lockfile: installed again)
    result["code"] = 1
    with pytest.raises(RuntimeError, match="disk full"):
        opencode.ensure_installed("bun", str(host))
    assert len(calls) == 2 and not opencode.installed(str(host))
    result["code"] = 0
    opencode.ensure_installed("bun", str(host))
    assert len(calls) == 3 and opencode.installed(str(host))


def test_exit_codes_and_stderr_tails_read_plainly(tmp_path):
    assert opencode.exit_code(1) == "1" and opencode.exit_code(None) == "still running"
    assert opencode.exit_code(3221226505) == "3221226505 (0xC0000409)"
    log = tmp_path / "opencode.log"
    log.write_bytes(b"\n".join(b"line %d" % i for i in range(1000)) + b"\n")
    with open(log, "ab") as err:
        tail = opencode.err_tail(err, limit=40)
    assert tail.startswith("...") and tail.endswith("line 998 | line 999") and len(tail) == 43
    assert opencode.err_tail(object()) == ""


def test_autopilot_agent_picks_opencode_by_default(monkeypatch):
    from autopilot import agents
    from autopilot.agents import claude_code

    monkeypatch.delenv("AUTOPILOT_AGENT", raising=False)
    assert agents.select() is opencode and agents.NAMES[0] == "opencode"
    monkeypatch.setenv("AUTOPILOT_AGENT", "claude")
    assert agents.select() is claude_code
    monkeypatch.setenv("AUTOPILOT_AGENT", "nonexistent")
    with pytest.raises(SystemExit):
        agents.select()
