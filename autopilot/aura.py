"""Runs paid through Aura: the brain runs on the user's machine, its model calls go through Aura's relay.

Sign in with Aura (`autopilot serve --aura`, or AUTOPILOT_AURA_URL set to an Aura server: the Aura app sets it for the
Autopilot it runs) and Autopilot needs no model keys of its own: its runs go through Aura, on the user's Aura plan.
Each run:

  1. Gets the user's Aura session token: from the plugin's connection ("Authorization: Bearer", as the Aura
     client's verification relay and a plugin given a token send it), else from the Aura client on this machine
     (its local GET /api/validateSessionToken, the way the plugins sign in), then from a sign-in on Aura's website
     (autopilot/aura_login.py: the plugins' Sign in with browser, `autopilot login`, or the browser opened by a run).
  2. Starts the run with Aura (POST <AUTOPILOT_AURA_URL>/api/autopilot/runs: plan, credits and mode are checked there).
     A session Aura doesn't take (401) gives way to the next one: the Aura app may be signed in to another Aura server
     (the production app beside a preview Autopilot). Any other refusal is the run's result, in Aura's words. With no
     session Aura takes, the user signs in on Aura's website (the browser opens, and the run goes on once signed in),
     never on the Aura app's own pages (they are the app's, blank in a browser).
  3. Plays with Jev and the agent's language model pointed at the relay Aura answered with, the run token as their key
     (TYPESAFE_BASE_URL / TYPESAFE_API_KEY, ANTHROPIC_BASE_URL / ANTHROPIC_API_KEY, for the run only). Aura counts the
     tokens each call used as it relays it. The agent is Claude Code, on Autopilot's own model, unless the run asked
     for the model picked in Aura's chat (the start frame's run `model`) and Aura answered with the `agent` that plays
     it: Claude Code on that Claude model, Codex on an OpenAI one, or OpenCode on an OpenRouter (DeepSeek's, say) or
     Google one, through Aura's relay for that provider (<PROVIDER>_BASE_URL and the provider's key variables,
     AUTOPILOT_OPENCODE_PROVIDER, AUTOPILOT_MODEL). For Auto, Aura answers with the model Auto picked in the chat.
     Without Codex installed (the codex extra), an OpenAI model plays on OpenCode; OpenCode needs Bun, and without it
     the run plays with Claude Code, on Autopilot's own model.
  4. Saves the game's memory to Aura (autopilot/aura_memory.py; fetched back before a run on a machine without it), then
     finishes the run (POST .../api/autopilot/runs/finish): Aura bills it by those tokens. A run never finished (the
     editor closed) is billed by Aura once its token expires.

The connection's mode ("X-Autopilot-Mode: aura_verification", sent by the Aura client for its own tools) is passed on,
so Aura knows where the run came from; it admits and bills every kind of run the same way in either mode.

A brain without it (Autopilot with the user's own model keys) never contacts Aura for its runs: nothing here runs, and
Aura bills nothing.
"""

import json
import os
import time

import requests

DEFAULT_URL = "https://app.tryaura.dev"  # Aura's server, for --aura
# The Aura client's local API, in the order Aura itself tries it: the port it published (AUTOPILOT_AURA_CLIENT_PORT, or
# Aura's plugin's aura_client_port.txt), the installed client's, a development client's
CLIENT_PORTS = (41200, 3002)
CLIENT_TIMEOUT = 3.0  # seconds for one port to answer
START_TIMEOUT = 60.0  # seconds for Aura to start a run (it checks the plan and credits)
FINISH_TRIES = 3
PLAYTEST, AURA_VERIFICATION = "playtest", "aura_verification"
AGENT = "claude"  # a run through Aura's agent (Claude Code) unless Aura names another, whatever AUTOPILOT_AGENT says
# The agents Aura may name for a run (its answer's `agent`), and the providers each plays through Aura's relay for
AGENTS = ("claude", "codex", "opencode")
OPENCODE_PROVIDERS = ("openai", "openrouter", "google")
CODEX_PROVIDERS = ("openai",)
MAX_MODEL = 128  # characters of the model asked for, at most
# Where a brain run for Aura says it listens (bridge.brain_file): what the plugins' Use with Aura reads
BRAIN_FILE = "aura-brain.json"
KINDS = ("verify", "explore", "goal")
# The model settings a run replaces while it plays (and puts back after): every key variable OpenCode may read for a
# provider is the run token's (agents/opencode.py PROVIDER_KEYS), so none of the user's own is used instead
RUN_ENV = ("TYPESAFE_BASE_URL", "TYPESAFE_API_KEY", "ANTHROPIC_BASE_URL", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
           "ANTHROPIC_OAUTH_TOKEN", "AUTOPILOT_MODEL", "AUTOPILOT_OPENCODE_PROVIDER", "OPENAI_BASE_URL",
           "OPENAI_API_KEY", "CODEX_API_KEY", "OPENROUTER_BASE_URL", "OPENROUTER_API_KEY", "GOOGLE_BASE_URL",
           "GEMINI_API_KEY", "GOOGLE_GENERATIVE_AI_API_KEY", "GOOGLE_API_KEY")


def api_url():
    """Aura's API (AUTOPILOT_AURA_URL) without a trailing slash, or "" when runs aren't through Aura."""
    return (os.environ.get("AUTOPILOT_AURA_URL") or "").strip().rstrip("/")


def enabled():
    return bool(api_url())


def default_url():
    """The Aura server runs go through when Aura is chosen (--aura, the first-run setup): Aura's own, unless
    AUTOPILOT_AURA_SERVER names another (a staging or preview one). Unlike AUTOPILOT_AURA_URL, it doesn't choose
    Aura."""
    return (os.environ.get("AUTOPILOT_AURA_SERVER") or "").strip().rstrip("/") or DEFAULT_URL


def use_default():
    """--aura: runs go through Aura (default_url()), unless AUTOPILOT_AURA_URL names a server already."""
    if not api_url():
        os.environ["AUTOPILOT_AURA_URL"] = default_url()


class Refused(Exception):
    """Aura won't start the run: its HTTP status, code and message (for the user)."""

    def __init__(self, status, message, code=""):
        super().__init__(message)
        self.status, self.message, self.code = status, message, code


# -- the user's Aura session ----------------------------------------------------------------------------------------

def client_urls(project_path=None):
    """Where the Aura client may be listening on this machine."""
    ports = []
    if os.environ.get("AUTOPILOT_AURA_CLIENT_PORT"):
        ports.append(os.environ["AUTOPILOT_AURA_CLIENT_PORT"].strip())
    if project_path:
        folder = project_path if os.path.isdir(project_path) else os.path.dirname(project_path)
        try:
            with open(os.path.join(folder, "Plugins", "Aura", "aura_client_port.txt"), encoding="utf-8") as f:
                ports.append(f.read().strip())
        except OSError:
            pass
    ports += [str(p) for p in CLIENT_PORTS]
    return list(dict.fromkeys(f"http://127.0.0.1:{p}" for p in ports if p.isdigit()))


def client_session(project_path=None, http=requests):
    """Ask the Aura client on this machine for its session: (the client's URL, its access token). The URL is None
    when no client answered; the token is None when it answered signed out. Its refresh token stays with it (it
    rotates: one use each)."""
    for base in client_urls(project_path):
        try:
            r = http.get(f"{base}/api/validateSessionToken", timeout=CLIENT_TIMEOUT)
        except requests.RequestException:
            continue
        if r.status_code == 401:
            return base, None
        if r.status_code == 200:
            try:
                token = (r.json() or {}).get("sessionToken")
            except ValueError:
                token = None
            if token:
                return base, token
    return None, None


def session_tokens(link, project_path=None, http=requests, server=None):
    """The Aura sessions to try, in turn, until Aura takes one: the connection's own (only it, when there is one), else
    the Aura client's, then the one kept from a sign-in on Aura's website (autopilot/aura_login.py, for `server`, else
    AUTOPILOT_AURA_URL; refreshed when due). Each is asked for only when the one before was refused: the Aura app on
    this machine may be signed in to another Aura server than this Autopilot's (the production app beside a preview
    Autopilot), and then the website's is the one Aura takes."""
    token = getattr(link, "auth_token", None)
    if token:
        yield token
        return
    app = client_session(project_path, http)[1]
    if app:
        yield app
    from . import aura_login

    token = aura_login.access_token(server or api_url(), http)
    if token and token != app:
        yield token


def session_token(link, project_path=None, http=requests):
    """The first session session_tokens would try. None: signed out."""
    return next(session_tokens(link, project_path, http), None)


def app_running(project_path=None, http=requests):
    """Whether the Aura app answers on this machine (signed in or not)."""
    return client_session(project_path, http)[0] is not None


# -- a run ----------------------------------------------------------------------------------------------------------

def _message(r):
    try:
        body = r.json()
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}
    return str(body.get("error") or body.get("message") or r.text[:300] or f"HTTP {r.status_code}"), \
        str(body.get("code") or "")


TRACE_FIELDS = ("thread_id", "tool_call_id", "tool")


def trace_of(trace):
    """A start frame's run "trace" as Aura takes it: its non-empty string fields (thread_id, tool_call_id, tool), at
    most 200 characters each; None when it has none. Aura only labels the run's analytics with it."""
    if not isinstance(trace, dict):
        return None
    kept = {k: trace[k].strip()[:200] for k in TRACE_FIELDS if isinstance(trace.get(k), str) and trace[k].strip()}
    return kept or None


def model_of(model):
    """A start frame's run "model" as Aura takes it (the model picked in Aura's chat, as Aura's model picker names it:
    "opus-5.5", "deepseek-v4-pro", "auto"): stripped, at most MAX_MODEL characters; None when there is none."""
    if not isinstance(model, str) or not model.strip():
        return None
    return model.strip()[:MAX_MODEL]


def agent_of(agent, relay):
    """Aura's answer's `agent` (the agent and model that play the model asked for: {name, provider, model, required?})
    when it is one this brain can play through Aura's relay, else None (Claude Code on Autopilot's own model).
    required: no other may play the run (a run on the team's free allowance plays Aura's free-tier model alone)."""
    if not isinstance(agent, dict):
        return None
    name, provider, model = agent.get("name"), agent.get("provider"), agent.get("model")
    if name not in AGENTS or not isinstance(model, str) or not model.strip() or not isinstance(provider, str):
        return None
    if name == "claude" and provider != "anthropic":
        return None
    if name != "claude" and (provider not in (CODEX_PROVIDERS if name == "codex" else OPENCODE_PROVIDERS)
                             or not (relay or {}).get(provider)):
        return None
    required = {"required": True} if agent.get("required") is True else {}
    return {"name": name, "provider": provider, "model": model.strip(), **required}


def bun_ready():
    """Whether OpenCode can run here: it needs Bun (autopilot/agents/opencode.py)."""
    from .agents import opencode

    try:
        opencode.bun()
    except RuntimeError:
        return False
    return True


def codex_ready():
    """Whether Codex can run here: the codex extra (openai-codex and Codex itself) is installed."""
    try:
        import openai_codex  # noqa: F401
    except ImportError:
        return False
    return True


def playable(agent, say):
    """`agent` (agent_of's), or what plays its model on this machine instead: OpenCode for an OpenAI model when Codex
    isn't installed; None (Claude Code on Autopilot's own model, and the user is told why) when OpenCode would play
    and there is no Bun. A required agent that can't play here raises Refused (nothing else may play the run)."""
    if agent and agent["name"] == "codex" and not codex_ready():
        agent = dict(agent, name="opencode")
    if agent and agent["name"] == "opencode" and not bun_ready():
        if agent.get("required"):
            raise Refused(503, "Autopilot can't play this run here: Bun isn't installed on this machine. Run "
                          "Autopilot's installer again (or restart the Aura app), then start the run again.",
                          "AGENT_UNAVAILABLE")
        say(f"Playing with Claude instead of {agent['model']}: that model needs Bun, which isn't installed on this "
            "machine")
        return None
    return agent


class Run:
    """One run through Aura. Use as `with aura.Run(...).start() as run:` around the play: inside, Jev and the agent
    use Aura's relay; on leaving, the run is finished (and billed) and the model settings are put back."""

    def __init__(self, token, kind, mode=PLAYTEST, project=None, http=None, say=None, project_key=None, trace=None,
                 model=None, disable_premium_models=False):
        self.token = token
        self.project_key = project_key or project  # (which project's memory: the Unreal ProjectID when there is one)
        # Where in Aura the run was asked for (the start frame's run "trace": the chat thread, tool call and tool), so
        # Aura files the run's model calls under that chat in its analytics; None for a run the Autopilot tab started
        self.trace = trace_of(trace)
        # The model picked in Aura's chat (the start frame's run "model"); None for a run the Autopilot tab started
        self.model = model_of(model)
        # Aura's chat's "Disable Premium Models" (the start frame's run "disable_premium_models", sent with its model):
        # Auto in a chat it has picked nothing for yet then plays the user's free-tier model
        self.disable_premium_models = disable_premium_models is True and self.model is not None
        self.agent = None  # the agent and model Aura answered with for it (agent_of), once started
        self.kind = kind if kind in KINDS else "goal"
        self.mode = AURA_VERIFICATION if mode == AURA_VERIFICATION else PLAYTEST
        self.project = project
        self.http = http or requests.Session()
        self.say = say or print
        self.run_id = self.run_token = self.relay = self.finish_url = None
        self.memory = None  # the project's memory kept with Aura (aura_memory.MemorySync), when Aura offers it
        # Where the run's shared data goes (autopilot/training.py): Aura answers it only when the user's account
        # allows sharing (its Privacy setting); None otherwise
        self.training = None
        self.memory_folder = None  # the folder it is kept in on this machine (set by whoever plays the run)
        self._saved = None
        self.finished = None  # Aura's answer to the finish, once given

    def start(self):
        """Ask Aura to start the run. Raises Refused (Aura said no, or couldn't be asked)."""
        if not self.token:
            raise Refused(401, "Sign in to Aura, then start the run again.", "UNAUTHENTICATED")
        body = {"kind": self.kind, "mode": self.mode, **({"project": self.project} if self.project else {}),
                **({"project_key": self.project_key} if self.project_key else {}),
                **({"trace": self.trace} if self.trace else {}), **({"model": self.model} if self.model else {}),
                **({"disable_premium_models": True} if self.disable_premium_models else {})}
        try:
            r = self.http.post(f"{api_url()}/api/autopilot/runs", json=body, timeout=START_TIMEOUT,
                               headers={"Authorization": f"Bearer {self.token}"})
        except requests.RequestException as e:
            raise Refused(503, f"Aura couldn't be reached ({type(e).__name__}). Please try again.", "UNAVAILABLE")
        if r.status_code != 200:
            message, code = _message(r)
            raise Refused(r.status_code, message, code)
        answer = r.json()
        self.run_id, self.run_token = answer["run_id"], answer["token"]
        self.relay, self.finish_url = answer["relay"], answer["finish"]
        self.training = answer.get("training") or None
        if answer.get("memory"):
            from .aura_memory import MemorySync

            self.memory = MemorySync(answer["memory"], self.run_token, say=self.say)
        try:
            self.agent = playable(agent_of(answer.get("agent"), self.relay), self.say)
        except Refused:
            self.finish()  # (nothing was played: Aura has nothing to bill)
            raise
        return self

    @property
    def agent_name(self):
        """The agent that plays the run (agents.select)."""
        return self.agent["name"] if self.agent else AGENT

    def restore_memory(self, folder):
        """Keep the project's memory in `folder` from now on, fetching Aura's copy first if this machine has none."""
        self.memory_folder = folder
        if self.memory:
            self.memory.restore(folder)

    def __enter__(self):
        self._saved = {k: os.environ.get(k) for k in RUN_ENV}
        for k in ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_OAUTH_TOKEN"):
            os.environ.pop(k, None)  # (else a client may prefer the user's own sign-in to the run's key)
        os.environ.update({"TYPESAFE_BASE_URL": self.relay["typesafe"], "ANTHROPIC_BASE_URL": self.relay["anthropic"]})
        self._agent_env()
        return self

    def _agent_env(self):
        """The run's key for Jev and Claude, and the agent's model, its API root and key (its API roots for Jev and
        Claude are set once, on entering: a proxy may stand in for them since)."""
        os.environ.update({"TYPESAFE_API_KEY": self.run_token, "ANTHROPIC_API_KEY": self.run_token})
        if self.agent:  # (the model picked in Aura's chat; else Autopilot's own, as AUTOPILOT_MODEL says)
            os.environ["AUTOPILOT_MODEL"] = self.agent["model"]
            if self.agent["name"] == "opencode":
                from .agents.opencode import PROVIDER_KEYS

                provider = self.agent["provider"]
                os.environ.update({
                    "AUTOPILOT_OPENCODE_PROVIDER": provider,
                    f"{provider.upper()}_BASE_URL": self.relay[provider],
                    **{k: self.run_token for k in PROVIDER_KEYS.get(provider, (f"{provider.upper()}_API_KEY",))},
                })
            elif self.agent["name"] == "codex":  # (agents/codex.py: its key and API root, OpenAI's)
                os.environ.update({"OPENAI_BASE_URL": self.relay["openai"], "OPENAI_API_KEY": self.run_token,
                                   "CODEX_API_KEY": self.run_token})

    def go_on_free(self):
        """Inside the run, once its agent stopped without a report: ask Aura whether the run goes on on the team's free
        allowance (POST /api/autopilot/runs/free: Aura says yes only when the run's premium credits stopped it). Yes:
        the run's key is its free part's and its agent the one Aura names (Auto's free-tier model, required), set for
        the agents from now on, and True. No: None when Aura said nothing for it (not stopped by its credits, an Aura
        without the route, not reachable); raises Refused when the team has no free allowance left either (NO_CREDITS,
        in Aura's words), or the agent can't play here (AGENT_UNAVAILABLE)."""
        if not self.run_token or self.relay is None:
            return None
        try:
            r = self.http.post(f"{api_url()}/api/autopilot/runs/free", timeout=START_TIMEOUT,
                               headers={"Authorization": f"Bearer {self.run_token}"})
        except requests.RequestException:
            return None
        if r.status_code == 402:
            message, code = _message(r)
            raise Refused(402, message, code or "NO_CREDITS")
        if r.status_code != 200:
            return None
        try:
            answer = r.json()
            token = answer["token"]
        except (ValueError, KeyError, TypeError):
            return None
        agent = agent_of(answer.get("agent"), self.relay)
        if not isinstance(token, str) or not token or agent is None:
            return None
        self.agent = playable(agent, self.say)  # (required: raises Refused when it can't play here)
        self.run_token = token
        self._agent_env()
        return True

    def __exit__(self, *exc):
        for k, v in (self._saved or {}).items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        if self.memory and self.memory_folder:
            self.memory.save(self.memory_folder)  # (before the finish: the run's token still opens the memory)
        self.finish()
        return False

    def finish(self):
        """Tell Aura the run has ended, so it bills it now (retried a few times; Aura bills it later otherwise)."""
        if not self.finish_url or self.finished is not None:
            return self.finished
        for attempt in range(FINISH_TRIES):
            try:
                r = self.http.post(self.finish_url, timeout=START_TIMEOUT,
                                   headers={"Authorization": f"Bearer {self.run_token}"})
                if r.status_code < 500:  # (202: ended, and Aura bills it later by itself)
                    try:
                        self.finished = r.json()
                    except ValueError:
                        self.finished = {"status": r.status_code}
                    print(f"Aura finished run {self.run_id}: {json.dumps(self.finished)}")
                    return self.finished
                print(f"Aura couldn't finish run {self.run_id} (HTTP {r.status_code}); retrying")
            except requests.RequestException as e:
                print(f"Couldn't tell Aura run {self.run_id} finished ({e}); retrying")
            time.sleep(2 * (attempt + 1))
        print(f"Aura bills run {self.run_id} when its token expires")
        self.finished = {}
        return self.finished


def refusal_words(e):
    """What the user reads when Aura refused a run."""
    if e.status == 401:
        return "Sign in to Aura, then start the run again."
    return e.message
