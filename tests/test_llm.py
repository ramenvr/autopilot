"""llm.Model's model: through Aura the run's own (Aura's relay serves no other), standalone the provider's cheaper
helper; a helper the provider refuses gives way to the run's own model."""

import pytest
import requests

from autopilot import llm

HELPER = llm.HELPER["openrouter"]
OWN = "deepseek/deepseek-v4.1-flash"
REFUSED = f'{{"error":"Model not available through Aura for this run: {HELPER}"}}'


class Reply:
    def __init__(self, status, text="", body=None):
        self.status_code = status
        self.text = text
        self.body = body

    def json(self):
        return self.body


def answered(model):
    return Reply(200, body={"model": model, "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                            "usage": {"prompt_tokens": 10, "completion_tokens": 2}})


@pytest.fixture
def relay(monkeypatch):
    """requests.post as Aura's relay for an openrouter run: refuse(model) says how it answers a call for model (a
    Reply), else an answer. asked: the models asked, in order."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.delenv("OPENROUTER_BASE_URL", raising=False)
    monkeypatch.delenv("AUTOPILOT_HELPER_MODEL", raising=False)
    monkeypatch.delenv("AUTOPILOT_VISION_MODEL", raising=False)
    monkeypatch.delenv("AUTOPILOT_AURA_URL", raising=False)  # (standalone unless a test says through Aura)
    state = {"asked": [], "refuse": lambda model: None}

    def post(url, headers=None, json=None, timeout=None):
        state["asked"].append(json["model"])
        return state["refuse"](json["model"]) or answered(json["model"])

    monkeypatch.setattr(requests, "post", post)
    return state


def test_refused_helper_falls_back_to_the_runs_own_model(relay):
    relay["refuse"] = lambda model: Reply(400, REFUSED) if model == HELPER else None
    model = llm.Model("openrouter", agent_model=OWN)
    assert model.name == HELPER

    assert model.ask("what does this do?") == "ok"
    assert model.name == OWN  # (the route switched for the rest of the run)
    assert model.ask("and this?") == "ok"

    assert relay["asked"] == [HELPER, OWN, OWN]  # (one refused request, then the run's model only)
    assert set(model.tokens) == {OWN} and model.tokens[OWN]["calls"] == 2  # (reading_usage keys them by model)


def test_server_error_on_the_helper_still_raises(relay):
    relay["refuse"] = lambda model: Reply(500, "upstream model overloaded") if model == HELPER else None
    model = llm.Model("openrouter", agent_model=OWN)
    with pytest.raises(RuntimeError, match="HTTP 500"):
        model.ask("what does this do?")
    assert relay["asked"] == [HELPER] and model.name == HELPER


def test_bad_request_that_does_not_name_the_model_still_raises(relay):
    relay["refuse"] = lambda model: Reply(400, '{"error":"max_tokens is too large"}') if model == HELPER else None
    model = llm.Model("openrouter", agent_model=OWN)
    with pytest.raises(RuntimeError, match="HTTP 400"):
        model.ask("what does this do?")
    assert relay["asked"] == [HELPER] and model.name == HELPER


AURA = "https://aura.example"


def test_through_aura_reading_asks_the_runs_own_model_first(relay, monkeypatch):
    monkeypatch.setenv("AUTOPILOT_AURA_URL", AURA)
    relay["refuse"] = lambda model: Reply(400, REFUSED) if model != OWN else None
    model = llm.Model("openrouter", agent_model=OWN)
    assert model.name == OWN

    assert model.ask("what does this do?") == "ok"
    assert relay["asked"] == [OWN]  # (no refused call for the helper)
    assert set(model.tokens) == {OWN}


def test_through_aura_a_claude_run_reads_with_its_own_model(monkeypatch):
    monkeypatch.setenv("AUTOPILOT_AURA_URL", AURA)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.delenv("AUTOPILOT_HELPER_MODEL", raising=False)
    assert llm.Model("anthropic", agent_model="claude-opus-5-5").name == "claude-opus-5-5"


def test_through_aura_the_helper_model_setting_is_ignored(relay, monkeypatch):
    monkeypatch.setenv("AUTOPILOT_AURA_URL", AURA)
    monkeypatch.setenv("AUTOPILOT_HELPER_MODEL", "openai/gpt-5-mini")
    assert llm.Model("openrouter", agent_model=OWN).name == OWN


def test_standalone_reading_keeps_the_cheaper_helper(relay, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    assert llm.Model("openrouter", agent_model=OWN).name == HELPER
    assert llm.Model("anthropic", agent_model="claude-opus-5-5").name == llm.HELPER["anthropic"]
    monkeypatch.setenv("AUTOPILOT_HELPER_MODEL", "openai/gpt-5-mini")
    assert llm.Model("openrouter", agent_model=OWN).name == "openai/gpt-5-mini"  # (standalone, the setting counts)
