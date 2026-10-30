"""Signing in to Aura on its website (autopilot/aura_login.py): the browser round trip with a one-time code bound to a
secret only Autopilot knows, the session kept and refreshed, and a run through Aura signing in when there is no Aura
app."""

import base64
import hashlib
import json
import threading
import time
import types
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

import pytest

from autopilot import aura, aura_login, cli

SERVER = "https://aura.test"


class Answer:
    def __init__(self, status, body):
        self.status_code, self._body, self.text = status, body, json.dumps(body)

    def json(self):
        return self._body


def jwt(exp):
    payload = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).rstrip(b"=").decode()
    return f"h.{payload}.s"


class FakeAura:
    """Aura's /api/auth/cli/token and /api/refreshSessionToken, as requests sees them."""

    def __init__(self):
        self.calls = []
        self.challenge = None
        self.refresh = Answer(200, {"success": True, "session_token": jwt(time.time() + 3600),
                                    "refresh_token": "r-2"})

    def post(self, url, json=None, timeout=None, headers=None):
        self.calls.append((url, json))
        if url.endswith("/api/auth/cli/token"):
            digest = base64.urlsafe_b64encode(hashlib.sha256(json["code_verifier"].encode()).digest()).rstrip(b"=")
            if json["code"] != "one-time" or digest.decode() != self.challenge:
                return Answer(400, {"error": "invalid_grant"})
            return Answer(200, {"access_token": jwt(time.time() + 3600), "refresh_token": "r-1",
                                "expires_at": time.time() + 3600, "user": {"id": "u1", "email": "dev@game.test"}})
        if url.endswith("/api/refreshSessionToken"):
            return self.refresh
        return Answer(200, {})


def browser(fake, answer="code=one-time"):
    """A browser that, like Aura's /auth/cli page once the user allows it, goes back to the callback."""
    def open_browser(url):
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        assert url.startswith(f"{SERVER}/auth/cli?") and len(q["code_challenge"]) == 43
        fake.challenge = q["code_challenge"]
        back = f"http://127.0.0.1:{q['port']}/callback?{answer}&state={q['state']}"

        def go_back():
            for attempt in range(5):  # (a hosted Windows runner once refused the first connection: 10061)
                try:
                    return urllib.request.urlopen(back, timeout=10).read()
                except urllib.error.URLError:
                    time.sleep(0.2 * (attempt + 1))

        threading.Thread(target=go_back, daemon=True).start()
        return True
    return open_browser


def test_pkce_challenge_is_the_s256_of_the_secret():
    verifier, challenge = aura_login.pkce()
    assert challenge == base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert len(challenge) == 43 and len(verifier) >= 43


def test_signing_in_keeps_a_session_of_its_own(no_kept_aura_session):
    fake, said = FakeAura(), []
    session = aura_login.sign_in(SERVER, open_browser=browser(fake), say=said.append, timeout=10, http=fake)
    assert session["email"] == "dev@game.test" and session["refresh_token"] == "r-1"
    assert aura_login.saved(SERVER)["refresh_token"] == "r-1"
    assert said[-1] == "Signed in to Aura as dev@game.test"
    assert fake.calls[0][0] == f"{SERVER}/api/auth/cli/token"


def test_a_cancelled_or_foreign_sign_in_keeps_nothing(no_kept_aura_session):
    fake = FakeAura()
    with pytest.raises(aura_login.SignInError, match="cancelled"):
        aura_login.sign_in(SERVER, open_browser=browser(fake, "error=access_denied"), say=lambda t: None,
                           timeout=10, http=fake)
    with pytest.raises(aura_login.SignInError, match="timed out"):  # (a callback with another state is ignored)
        aura_login.sign_in(SERVER, open_browser=browser(fake, "code=one-time&state=forged"), say=lambda t: None,
                           timeout=2, http=fake)
    assert aura_login.saved(SERVER) is None and not fake.calls


def test_a_code_without_the_secret_is_refused(no_kept_aura_session):
    fake = FakeAura()

    def open_browser(url):
        browser(fake)(url)
        fake.challenge = "not-the-challenge-for-this-secret"
        return True

    with pytest.raises(aura_login.SignInError, match="refused"):
        aura_login.sign_in(SERVER, open_browser=open_browser, say=lambda t: None, timeout=10, http=fake)


def test_the_kept_session_is_used_then_refreshed_when_due_and_forgotten_when_ended(no_kept_aura_session):
    fake = FakeAura()
    fresh = jwt(time.time() + 3600)
    aura_login.save(SERVER, {"access_token": fresh, "refresh_token": "r-1", "expires_at": time.time() + 3600})
    assert aura_login.access_token(SERVER, http=fake) == fresh and not fake.calls
    aura_login.save(SERVER, {"access_token": "old", "refresh_token": "r-1", "expires_at": time.time() + 10})
    renewed = aura_login.access_token(SERVER, http=fake)
    assert renewed != "old" and aura_login.saved(SERVER)["refresh_token"] == "r-2"  # (rotated: kept at once)
    assert fake.calls[-1] == (f"{SERVER}/api/refreshSessionToken", {"refresh_token": "r-1"})
    aura_login.save(SERVER, {"access_token": "old", "refresh_token": "r-2", "expires_at": time.time() + 10})
    fake.refresh = Answer(401, {"error": "refresh_token_already_used"})
    assert aura_login.access_token(SERVER, http=fake) is None and aura_login.saved(SERVER) is None


def test_sessions_are_kept_per_server(no_kept_aura_session):
    aura_login.save(SERVER, {"access_token": "a", "refresh_token": "r", "expires_at": time.time() + 3600})
    assert aura_login.saved("https://staging.aura.test") is None
    assert aura_login.sign_out(SERVER, http=FakeAura()) and aura_login.saved(SERVER) is None


def test_a_run_uses_the_kept_session_when_there_is_no_aura_app(monkeypatch, no_kept_aura_session):
    monkeypatch.setenv("AUTOPILOT_AURA_URL", SERVER)
    monkeypatch.setattr(aura, "client_session", lambda *a, **k: (None, None))
    aura_login.save(SERVER, {"access_token": "web-token", "refresh_token": "r", "expires_at": time.time() + 3600})
    assert aura.session_token(types.SimpleNamespace(auth_token=None)) == "web-token"


class Link:
    def __init__(self, host="editor"):
        self.hello = {"project": "Game", "host": host}
        self.auth_token = None
        self.mode = None
        self.said = []

    def status(self, text):
        self.said.append(text)


def test_a_signed_out_run_signs_in_on_the_website_then_goes_on(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOPILOT_AURA_URL", SERVER)
    monkeypatch.setenv("AUTOPILOT_MEMORY", str(tmp_path))
    monkeypatch.setattr(aura, "client_session", lambda *a, **k: (None, None))
    tokens = []

    class Run:
        def __init__(self, token, *a, **k):
            self.token = token

        def start(self):
            tokens.append(self.token)
            if not self.token:
                raise aura.Refused(401, "Sign in", "UNAUTHENTICATED")
            return self

        def restore_memory(self, folder):
            pass

    def sign_in(server, say=print, **k):
        say("Sign in to Aura in your browser")
        session = {"access_token": "web-token", "refresh_token": "r", "expires_at": time.time() + 3600}
        aura_login.save(server, session)
        return session

    monkeypatch.setattr(aura, "Run", Run)
    monkeypatch.setattr(aura_login, "sign_in", sign_in)
    monkeypatch.setattr(cli.codexmod, "MEMORY", str(tmp_path))
    link = Link()
    run = cli.start_aura_run(link, "verify", "Game")
    assert tokens == ["web-token"] and run.token == "web-token"  # (no session: Aura is asked once signed in)
    assert "Sign in to Aura in your browser" in link.said


class AuraTakes:
    """aura.Run against an Aura server that takes only the sessions given (a preview server: the production Aura
    app's session is a 401 there); records the session each start was asked with."""

    def __init__(self, *good):
        self.good, self.tokens = set(good), []
        outer = self

        class Run:
            def __init__(self, token, *a, **k):
                self.token = token

            def start(self):
                outer.tokens.append(self.token)
                if self.token not in outer.good:
                    raise aura.Refused(401, "Invalid or expired session", "UNAUTHENTICATED")
                return self

            def restore_memory(self, folder):
                pass

        self.Run = Run


@pytest.fixture
def aura_app_signed_in_elsewhere(monkeypatch, tmp_path, no_kept_aura_session):
    """The Aura app runs here, signed in to production; this Autopilot's runs go through another Aura server; no
    browser ever opens on the Aura app's own pages (blank outside the app)."""
    monkeypatch.setenv("AUTOPILOT_AURA_URL", SERVER)
    monkeypatch.setattr(cli.codexmod, "MEMORY", str(tmp_path))
    monkeypatch.setattr(aura, "client_session", lambda *a, **k: ("http://127.0.0.1:41200", "prod-app-token"))
    monkeypatch.setattr(webbrowser, "open", lambda url, *a, **k: pytest.fail(f"a browser opened on {url}"))


def test_a_run_uses_the_website_sign_in_when_aura_refuses_the_aura_apps_session(monkeypatch,
                                                                                 aura_app_signed_in_elsewhere):
    """The bug: Sign in with browser, then a run with the production Aura app running sent the app's session, got a
    401 and opened the app's 127.0.0.1:41200/sign-in, a blank page, instead of using the website sign-in."""
    aura_login.save(SERVER, {"access_token": "web-token", "refresh_token": "r", "expires_at": time.time() + 3600})
    aura_server = AuraTakes("web-token")
    monkeypatch.setattr(aura, "Run", aura_server.Run)
    monkeypatch.setattr(aura_login, "sign_in", lambda *a, **k: pytest.fail("already signed in with the browser"))
    run = cli.start_aura_run(Link(), "verify", "Game")
    assert aura_server.tokens == ["prod-app-token", "web-token"] and run.token == "web-token"
    assert aura_login.saved(SERVER)["access_token"] == "web-token"


def test_a_run_aura_takes_no_session_for_signs_in_on_the_website_even_with_the_aura_app(
        monkeypatch, aura_app_signed_in_elsewhere):
    aura_server = AuraTakes("new-web-token")
    monkeypatch.setattr(aura, "Run", aura_server.Run)
    signed_in = []

    def sign_in(server, say=print, **k):
        signed_in.append(server)
        say("Sign in to Aura in your browser")
        session = {"access_token": "new-web-token", "refresh_token": "r", "expires_at": time.time() + 3600}
        aura_login.save(server, session)
        return session

    monkeypatch.setattr(aura_login, "sign_in", sign_in)
    link = Link()
    run = cli.start_aura_run(link, "verify", "Game")
    assert signed_in == [SERVER] and run.token == "new-web-token"
    assert aura_server.tokens == ["prod-app-token", "new-web-token"]  # (not the app's again after signing in)
    assert "Sign in to Aura in your browser" in link.said


def test_a_session_the_connection_brought_is_the_only_one_tried(monkeypatch, aura_app_signed_in_elsewhere):
    """The Aura app's own tools connect with their session: refused, the run is refused (no other session, no
    browser)."""
    aura_login.save(SERVER, {"access_token": "web-token", "refresh_token": "r", "expires_at": time.time() + 3600})
    aura_server = AuraTakes("web-token")
    monkeypatch.setattr(aura, "Run", aura_server.Run)
    monkeypatch.setattr(aura_login, "sign_in", lambda *a, **k: pytest.fail("no browser for the app's own runs"))
    link = Link()
    link.auth_token = "connection-token"
    with pytest.raises(aura.Refused, match="Sign in to Aura, then start the run again"):
        cli.start_aura_run(link, "verify", "Game")
    assert aura_server.tokens == ["connection-token"]


def test_a_refusal_other_than_signed_out_is_the_runs_result(monkeypatch, aura_app_signed_in_elsewhere):
    aura_login.save(SERVER, {"access_token": "web-token", "refresh_token": "r", "expires_at": time.time() + 3600})
    tokens = []

    class Run(AuraTakes().Run):
        def start(self):
            tokens.append(self.token)
            raise aura.Refused(402, "Out of Aura credits.", "NO_CREDITS")

    monkeypatch.setattr(aura, "Run", Run)
    with pytest.raises(aura.Refused, match="Out of Aura credits"):
        cli.start_aura_run(Link(), "verify", "Game")
    assert tokens == ["prod-app-token"]


def test_the_sessions_are_asked_for_only_as_needed(monkeypatch, no_kept_aura_session):
    monkeypatch.setenv("AUTOPILOT_AURA_URL", SERVER)
    monkeypatch.setattr(aura, "client_session", lambda *a, **k: ("http://127.0.0.1:41200", "same"))
    aura_login.save(SERVER, {"access_token": "same", "refresh_token": "r", "expires_at": time.time() + 3600})
    assert list(aura.session_tokens(types.SimpleNamespace(auth_token=None))) == ["same"]  # (never twice)
    asked = []
    monkeypatch.setattr(aura_login, "access_token", lambda *a, **k: asked.append(1) or "web")
    tokens = aura.session_tokens(types.SimpleNamespace(auth_token=None))
    assert next(tokens) == "same" and not asked  # (the website's refreshed only when the app's was refused)
    assert next(tokens) == "web" and asked


def test_a_game_run_on_its_own_is_told_to_sign_in_first(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOPILOT_AURA_URL", SERVER)
    monkeypatch.setattr(aura, "client_session", lambda *a, **k: (None, None))
    monkeypatch.setattr(cli.codexmod, "MEMORY", str(tmp_path))

    class Run:
        def __init__(self, *a, **k):
            pass

        def start(self):
            raise aura.Refused(401, "Sign in", "UNAUTHENTICATED")

    monkeypatch.setattr(aura, "Run", Run)
    monkeypatch.setattr(aura_login, "sign_in", lambda *a, **k: pytest.fail("no browser for a game run"))
    with pytest.raises(aura.Refused, match="autopilot login"):
        cli.start_aura_run(Link(host="game"), "verify", "Game")


def test_choosing_aura_in_the_first_run_setup_sends_runs_through_aura(monkeypatch, own_preferences):
    from autopilot import account

    prefs = own_preferences
    monkeypatch.delenv("AUTOPILOT_AURA_URL", raising=False)
    assert not account.chose_aura()
    prefs.write_text(json.dumps({"version": 1, "onboarded": True, "access": "aura"}))
    assert account.chose_aura()
    account.follow_preferences()
    assert aura.api_url() == aura.DEFAULT_URL
    prefs.write_text(json.dumps({"access": "own_keys"}))  # (changed in the setup while Autopilot runs: the next run)
    account.follow_preferences()
    assert not aura.enabled()
