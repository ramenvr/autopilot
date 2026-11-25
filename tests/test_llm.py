"""llm.Model's model: through Aura the run's own (Aura's relay serves no other), standalone the provider's cheaper
helper; a helper the provider refuses gives way to the run's own model."""

import types

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


# -- reading on the agent's provider, not the frame describer's ------------------------------------------------------

def test_a_gemini_route_has_no_reading_model_instead_of_crashing(monkeypatch):
    """Google's route is Gemini's own API, which Model doesn't speak: no route, a reason, no TypeError."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.delenv("GOOGLE_BASE_URL", raising=False)
    model = llm.Model("google", agent_model="gemini-3.1-pro")
    assert model.route is None and model.name is None
    assert "gemini" in model.unavailable.lower()
    with pytest.raises(RuntimeError, match="no model to read with"):
        model.ask("what does this do?")


class FakeSession:
    """What session.start_reading uses of a session: its notes, no index, no sandbox, a plugin whose hello turns the
    researcher off (reading only)."""

    def __init__(self, tmp_path):
        self.root = str(tmp_path)
        self.run_dir = str(tmp_path / "run")
        self.notes = []
        self.sandbox = None
        self.reader = self.researcher = None
        self.game = types.SimpleNamespace(link=types.SimpleNamespace(hello={"research": False}))

    def note(self, text):
        self.notes.append(text)

    def indexer(self):
        return None


def test_through_aura_reading_is_on_the_agents_provider_not_the_describers(relay, monkeypatch, tmp_path):
    """A text-only run's frames are described by Google (Aura names Gemini): reading stays on the agent's provider
    (openrouter) with the run's model, never on Gemini."""
    from autopilot import cli
    from autopilot.agents import session as sessionmod

    monkeypatch.setenv("AUTOPILOT_AURA_URL", AURA)
    monkeypatch.setenv("AUTOPILOT_MODEL", OWN)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    describer = cli.reading_on(types.SimpleNamespace(provider="google", usage=None, proxied=None), "openrouter")
    s = FakeSession(tmp_path)
    sessionmod.start_reading(s, describer)
    assert not any(n.startswith("reading: no model") for n in s.notes), s.notes
    assert s.reader.model.provider == "openrouter" and s.reader.model.name == OWN
    assert s.reader.model.ask("what does this do?") == "ok"
    assert relay["asked"] == [OWN]


def test_a_describer_without_the_agents_provider_reads_on_its_own(relay, monkeypatch, tmp_path):
    """The user's own agent (agents/external.py) keeps today's reading: on the describer's provider."""
    from autopilot.agents import session as sessionmod

    monkeypatch.setenv("AUTOPILOT_MODEL", OWN)
    s = FakeSession(tmp_path)
    sessionmod.start_reading(s, types.SimpleNamespace(provider="openrouter", usage=None, proxied=None))
    assert s.reader.model.provider == "openrouter"


def test_a_gemini_reading_route_is_noted_not_raised(monkeypatch, tmp_path):
    """Reading whose provider is Google says so in the run log and starts no reader, no TypeError."""
    from autopilot.agents import session as sessionmod

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.delenv("GOOGLE_BASE_URL", raising=False)
    s = FakeSession(tmp_path)
    sessionmod.start_reading(s, types.SimpleNamespace(provider="google", usage=None, proxied=None))
    assert s.reader is None
    assert any(n.startswith("reading: no model (") and "gemini" in n.lower() for n in s.notes), s.notes


def test_the_runs_research_choice_on_the_describer_wins_over_the_hello(relay, monkeypatch, tmp_path):
    """start_reading asks researcher.enabled with the run's choice (the describer's `research`, from the start
    frame): False turns the researcher off whatever the hello says."""
    from autopilot.agents import researcher
    from autopilot.agents import session as sessionmod

    monkeypatch.delenv("AUTOPILOT_RESEARCHER", raising=False)
    monkeypatch.setenv("AUTOPILOT_MODEL", OWN)
    asked = []
    monkeypatch.setattr(researcher, "enabled", lambda hello=None, chosen=None: asked.append((hello, chosen)) or False)
    s = FakeSession(tmp_path)
    s.game.link.hello = {"research": True}
    describer = types.SimpleNamespace(provider="openrouter", usage=None, proxied=None, research=False)
    sessionmod.start_reading(s, describer)
    assert asked == [({"research": True}, False)]
    assert s.researcher is None and any(n.startswith("researcher: off") for n in s.notes), s.notes
