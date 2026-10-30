"""Signing in to Aura on its website, for an Autopilot that runs without the Aura app (autopilot/aura.py, docs/aura.md).

    autopilot login     sign in: the browser opens Aura's sign-in (<Aura server>/auth/cli); once the user allows
    Autopilot
                    on this computer, the session comes back here and is kept for later runs
    autopilot logout    forget it (and end it on Aura's side)

The flow (Aura's /auth/cli page and /api/auth/cli routes): Autopilot listens on http://127.0.0.1:<port>/callback and
opens <server>/auth/cli?port=<port>&state=<state>&code_challenge=<S256 of a secret only Autopilot knows>. Once the user
signs in and allows it, the page sends the browser back to the callback with a one-time code (valid two minutes), and
Autopilot exchanges it, with the secret, for a session of its own (POST /api/auth/cli/token): the browser's session
isn't shared, and a code seen by anyone else is useless without the secret.

The session is kept in Autopilot/aura-session.json in the user settings folder (readable by the user only where the
system allows), per Aura server, and refreshed when it is about to expire (POST /api/refreshSessionToken: refresh
tokens rotate, so each new one is saved at once).
"""

import base64
import hashlib
import html
import http.server
import json
import os
import secrets
import socketserver
import sys
import threading
import time
import urllib.parse
import webbrowser

import requests

from . import bridge

SESSION_FILE = "aura-session.json"
SIGN_IN_TIMEOUT = 300.0  # seconds the user has to sign in in the browser
REFRESH_MARGIN = 120.0  # seconds before the access token expires that it is refreshed
TIMEOUT = 20.0
_lock = threading.Lock()


class SignInError(Exception):
    pass


def _b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def pkce():
    """(code verifier, its S256 challenge)."""
    verifier = _b64url(secrets.token_bytes(48))
    return verifier, _b64url(hashlib.sha256(verifier.encode()).digest())


def token_expiry(token):
    """When a JWT access token expires (Unix seconds), from its payload; 0 when it can't be read."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return float(json.loads(base64.urlsafe_b64decode(payload)).get("exp") or 0)
    except (IndexError, ValueError, AttributeError):
        return 0.0


# -- the kept session ---------------------------------------------------------------------------------------------

def _path():
    return bridge.brain_file(SESSION_FILE)


def _read_all():
    try:
        with open(_path(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_all(data):
    path = _path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    if os.name != "nt":
        os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def saved(server):
    """The session kept for this Aura server: {access_token, refresh_token, expires_at, email}, or None."""
    entry = _read_all().get(server.rstrip("/"))
    return entry if isinstance(entry, dict) and entry.get("refresh_token") else None


def servers():
    """The Aura servers a session is kept for."""
    return [server for server, entry in _read_all().items() if isinstance(entry, dict) and entry.get("refresh_token")]


def save(server, session):
    with _lock:
        data = _read_all()
        data[server.rstrip("/")] = session
        _write_all(data)


def forget(server):
    with _lock:
        data = _read_all()
        entry = data.pop(server.rstrip("/"), None)
        _write_all(data)
    return entry


def _session_of(answer, email=None):
    """The kept form of an answer from /api/auth/cli/token or /api/refreshSessionToken."""
    access = answer.get("access_token") or answer.get("session_token")
    refresh = answer.get("refresh_token")
    if not access or not refresh:
        raise SignInError("Aura's answer had no session in it")
    user = answer.get("user") or {}
    return {"access_token": access, "refresh_token": refresh,
            "expires_at": float(answer.get("expires_at") or token_expiry(access) or time.time() + 3000),
            "email": user.get("email") or email}


def access_token(server, http=requests):
    """A current access token from the kept session, refreshed when it is about to expire; None when there is no
    kept session or Aura no longer takes it (it is forgotten then: the user signs in again)."""
    with _lock:
        entry = saved(server)
        if not entry:
            return None
        if float(entry.get("expires_at") or 0) - time.time() > REFRESH_MARGIN:
            return entry["access_token"]
        try:
            r = http.post(f"{server.rstrip('/')}/api/refreshSessionToken",
                          json={"refresh_token": entry["refresh_token"]}, timeout=TIMEOUT)
        except requests.RequestException as e:
            print(f"Couldn't refresh the Aura session ({type(e).__name__}); trying it as it is")
            return entry["access_token"]
        body = {}
        try:
            body = r.json() or {}
        except ValueError:
            pass
        renewed = body.get("session_token") or body.get("access_token")
        if r.status_code == 200 and body.get("success", True) and renewed:
            fresh = _session_of(body, entry.get("email"))
            data = _read_all()
            data[server.rstrip("/")] = fresh
            _write_all(data)
            return fresh["access_token"]
        if r.status_code in (400, 401, 403):
            print("The Aura session has ended: sign in again")
            data = _read_all()
            data.pop(server.rstrip("/"), None)
            _write_all(data)
            return None
        return entry["access_token"]  # (Aura unwell: the token may still do until it expires)


# -- signing in ---------------------------------------------------------------------------------------------------

# The page the browser lands on, in Aura's look (its Flatness theme, as the plugins have it: the near-black ground, a
# flat 8 px plate with a 1 px rule, IBM Plex Sans and a Plex Mono micro-label, the amber mark, no shadow or gradient;
# the title green when signed in and red when it went wrong, like the plugins' "Uninstallation successful" plate)
DONE_PAGE = """<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Autopilot</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500&display=swap">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@500&display=swap">
<style>
html{{min-height:100%;background:#121316}}
body{{margin:0;min-height:100vh;display:grid;place-items:center;padding:16px;box-sizing:border-box;color:#e6e6e8;
background:#121316;font:400 13px/1.55 "IBM Plex Sans",ui-sans-serif,system-ui,sans-serif}}
main{{max-width:540px;width:100%;box-sizing:border-box;padding:12px 14px;display:flex;flex-direction:column;gap:10px;
background:#181a1e;border:1px solid #24262b;border-radius:8px}}
.mark{{display:flex;align-items:center;gap:10px}}
.ml{{font:500 10px/1.4 "IBM Plex Mono",ui-monospace,monospace;letter-spacing:.1em;text-transform:uppercase;
color:#d3d5da}}
svg{{display:block;flex:none}}
h1{{margin:0;font-size:15px;font-weight:500}}
.ok{{color:#b6ddb9}}.no{{color:#e26a62}}
p{{margin:0;font-size:12px;color:#a4a8b0}}
</style>
<main><div class="mark"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#f0873a" stroke-width="1.9"
stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M7 7H17L19.5 7.6L21.2 9.2L22 11.5L22.8 15.5
L22.4 17.2L21 18L19.4 17.8L18 16.6L16.6 15.5H7.4L6 16.6L4.6 17.8L3 18L1.6 17.2L1.2 15.5L2 11.5L2.8 9.2L4.5 7.6Z"/>
<path d="M7 9.6V13.4M5.1 11.5H8.9"/><circle cx="16" cy="10.4" r="1.15" fill="#f0873a" stroke="none"/>
<circle cx="18.2" cy="12.6" r="1.15" fill="#f0873a" stroke="none"/></svg><span class="ml">Autopilot</span></div>
<h1 class="{tone}">{title}</h1><p>{text}</p></main></html>"""


class _Callback(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        if url.path != "/callback":
            self.send_error(404)
            return
        q = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
        server = self.server
        if not secrets.compare_digest(q.get("state", ""), server.state):
            tone, title, text = "no", "That sign-in wasn’t for this Autopilot.", "Start it again from Autopilot."
        elif q.get("error"):
            server.result = {"error": q["error"]}
            tone, title, text = "", "Sign-in cancelled.", "You can close this tab."
        elif q.get("code"):
            server.result = {"code": q["code"]}
            tone, title = "ok", "Autopilot is signed in to Aura."
            text = "You can close this tab and go back to Autopilot."
        else:
            tone, title, text = "no", "Nothing came back from Aura.", "Start the sign-in again from Autopilot."
        page = DONE_PAGE.format(tone=tone, title=html.escape(title), text=html.escape(text)).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(page)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(page)


class _Server(socketserver.TCPServer):
    allow_reuse_address = False

    def __init__(self, state):
        super().__init__(("127.0.0.1", 0), _Callback)
        self.state = state
        self.result = None


def sign_in(server, open_browser=webbrowser.open, say=print, timeout=SIGN_IN_TIMEOUT, http=requests,
            cancelled=lambda: False):
    """Sign in on Aura's website and keep the session; returns it. say(text) tells the user what to do (the browser
    may not open by itself: the URL is in the text). Raises SignInError (cancelled, timed out, refused)."""
    server = server.rstrip("/")
    verifier, challenge = pkce()
    state = _b64url(secrets.token_bytes(24))
    callback = _Server(state)
    callback.timeout = 0.5
    port = callback.server_address[1]
    url = f"{server}/auth/cli?" + urllib.parse.urlencode({"port": port, "state": state, "code_challenge": challenge})
    try:
        opened = False
        try:
            opened = bool(open_browser(url))
        except Exception:
            pass
        say("Sign in to Aura in your browser" + ("" if opened else f": {url}"))
        print(f"Aura sign-in: {url}")
        deadline = time.time() + timeout
        while callback.result is None:
            if cancelled():
                raise SignInError("Sign-in cancelled.")
            if time.time() > deadline:
                raise SignInError("Sign-in timed out: nothing came back from the browser.")
            callback.handle_request()
    finally:
        callback.server_close()
    if callback.result.get("error"):
        raise SignInError("Sign-in cancelled in the browser." if callback.result["error"] == "access_denied"
                          else f"Aura refused the sign-in ({callback.result['error']}).")
    try:
        r = http.post(f"{server}/api/auth/cli/token", json={"code": callback.result["code"],
                                                              "code_verifier": verifier}, timeout=TIMEOUT)
    except requests.RequestException as e:
        raise SignInError(f"Aura couldn't be reached ({type(e).__name__}).") from None
    if r.status_code != 200:
        raise SignInError(f"Aura refused the sign-in (HTTP {r.status_code}). Try again.")
    session = _session_of(r.json())
    save(server, session)
    say("Signed in to Aura" + (f" as {session['email']}" if session.get("email") else ""))
    return session


def sign_out(server, http=requests):
    """Forget the kept session and end it on Aura's side. Returns whether there was one."""
    entry = forget(server)
    if not entry:
        return False
    try:
        http.post(f"{server.rstrip('/')}/api/logout", json={"session_token": entry.get("access_token")},
                  timeout=TIMEOUT)
    except requests.RequestException:
        pass
    return True


def main_login(server):
    try:
        session = sign_in(server)
    except SignInError as e:
        print(e, file=sys.stderr)
        return 1
    print(f"Signed in to Aura ({server})" + (f" as {session['email']}" if session.get("email") else ""))
    return 0


def main_logout(server):
    print("Signed out of Aura" if sign_out(server) else "Not signed in to Aura")
    return 0
