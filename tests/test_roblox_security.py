"""Security and lifecycle regressions using fake plugins, scratch settings and loopback test servers."""

import json
import threading
import types
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer

import pytest
import requests
from websockets.exceptions import InvalidStatus
from websockets.sync.client import connect

from autopilot import account, aura, bridge, telemetry


@pytest.fixture
def local_bridge(monkeypatch):
    import websockets.sync.server

    listening = threading.Event()
    servers = {}
    serve = websockets.sync.server.serve
    serve_http = bridge.serve_http

    def websocket(*args, **kwargs):
        servers['ws'] = serve(*args, **kwargs)
        return servers['ws']

    def http(*args, **kwargs):
        servers['http'] = serve_http(*args, **kwargs)
        listening.set()
        return servers['http']

    monkeypatch.setattr(websockets.sync.server, 'serve', websocket)
    monkeypatch.setattr(bridge, 'serve_http', http)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(bridge.serve_local, port=0, http_port=0, wait=5, announce_as=None)
        assert listening.wait(5)
        url = f"ws://127.0.0.1:{servers['ws'].socket.getsockname()[1]}"
        base = f"http://127.0.0.1:{servers['http'].server_port}/v1/http/"
        with connect(url + '/v1/bridge', proxy=None) as native:
            native.send(json.dumps({'type': 'hello', 'protocol': 1, 'engine': 'roblox'}))
            assert json.loads(native.recv(timeout=3))['type'] == 'welcome'
            link = future.result(timeout=5)
            try:
                yield url, base, link
            finally:
                link.server.shutdown()


def test_browser_origins_cannot_take_over_bridge_or_join_pipe(local_bridge):
    url, _, link = local_bridge
    for origin in ('https://untrusted.invalid', 'null', ''):
        for path in ('/v1/bridge', '/v1/pipe/' + link.session):
            with pytest.raises(InvalidStatus) as error:
                connect(url + path, origin=origin, proxy=None)
            assert error.value.response.status_code == 403
            assert link.server_state['current'] is link


def test_http_rejects_browser_requests_before_creating_or_using_peers(local_bridge):
    _, base, link = local_bridge
    for origin in ('https://untrusted.invalid', 'null', ''):
        for route in ('open', 'send?peer=missing', 'close?peer=missing'):
            response = requests.post(base + route, data='{}', headers={'Origin': origin}, timeout=3)
            assert response.status_code == 403
        assert requests.get(base + 'poll?peer=missing', headers={'Origin': origin}, timeout=3).status_code == 403
    assert not link.server_state['peers']
    peer = requests.post(base + 'open', timeout=3).json()['peer']
    try:
        assert requests.post(base + 'send?peer=' + peer,
                             json=[{'type': 'hello', 'protocol': 1}], timeout=3).status_code == 200
        welcome = requests.get(base + 'poll?peer=' + peer, timeout=3).json()
        assert welcome[0]['type'] == 'welcome'
    finally:
        requests.post(base + 'close?peer=' + peer, timeout=3)


def test_dev_control_rejects_origins_and_simple_forms_without_evaluating():
    from tools.roblox_dev import handler_for

    calls = []
    dev = types.SimpleNamespace(eval=lambda *args: calls.append(args) or {'ok': True})
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(dev))
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    url = f'http://127.0.0.1:{server.server_port}/ctl'
    try:
        for origin in ('https://untrusted.invalid', 'null', ''):
            r = requests.post(url, data='{"code":"return 1","padding":"="}\r\n',
                              headers={'Origin': origin, 'Content-Type': 'text/plain'}, timeout=3)
            assert r.status_code == 403
            assert requests.post(url, json={'code': 'return 1'}, headers={'Origin': origin},
                                 timeout=3).status_code == 403
        assert requests.post(url, data='{"code":"return 1"}', timeout=3).status_code == 415
        assert calls == []
        assert requests.post(url, json={'code': 'return 1'}, timeout=3).json() == {'ok': True}
        assert calls == [('edit', 'return 1', 30.0)]
    finally:
        server.shutdown()
        thread.join(3)
        server.server_close()


def test_setup_and_account_reporting_do_not_change_active_run_environment(monkeypatch, tmp_path, own_preferences):
    monkeypatch.setattr(aura, 'client_session', lambda *a, **k: (None, None))
    own_preferences.write_text(json.dumps({'access': 'aura', 'share_data': False}))
    monkeypatch.setattr(account, '_from_preferences', True)
    monkeypatch.setenv('AUTOPILOT_AURA_URL', 'https://relay.invalid')
    monkeypatch.setenv('TYPESAFE_API_KEY', 'dummy-active-run-token')
    monkeypatch.setenv('ANTHROPIC_BASE_URL', 'https://relay.invalid/anthropic')
    monkeypatch.setattr(account, '_user_env_path', lambda: str(tmp_path / 'autopilot.env'))
    sent = []
    link = types.SimpleNamespace(loopback=True, send=sent.append, hello={'engine': 'roblox', 'share': False})
    before = dict(account.os.environ)
    account.handle(link, {'type': 'save_setup', 'access': 'own_keys', 'coding_agent': 'claude',
                          'keys': {'decision': 'dummy-next-run-key'}}, None)
    assert sent[0] == {'type': 'setup_saved', 'ok': True}
    assert sent[-1]['mode'] == 'own_keys'
    assert dict(account.os.environ) == before
    account.load_user_settings()
    account.follow_preferences()
    assert account.os.environ['TYPESAFE_API_KEY'] == 'dummy-next-run-key'
    assert 'AUTOPILOT_AURA_URL' not in account.os.environ


def test_roblox_opt_out_is_authoritative_and_explicit_changes_persist(monkeypatch, own_preferences):
    monkeypatch.delenv('AUTOPILOT_SHARE', raising=False)
    monkeypatch.setattr(aura, 'client_session', lambda *a, **k: (None, None))
    own_preferences.write_text(json.dumps({'access': 'own_keys', 'share_data': False, 'unrelated': 1}))
    hello = {'engine': 'roblox', 'share': True}
    assert not telemetry.sharing(hello)
    assert account.state()['share_data'] is False
    sent = []
    link = types.SimpleNamespace(loopback=True, hello=hello, send=sent.append)
    account.handle(link, {'type': 'save_preferences', 'share': True}, None)
    assert sent[0] == {'type': 'preferences_saved', 'ok': True}
    assert telemetry.sharing(hello)
    assert account.preferences() == {'access': 'own_keys', 'share_data': True, 'unrelated': 1}
    account.handle(link, {'type': 'save_preferences', 'share': False}, None)
    assert hello['share'] is False and not telemetry.sharing(hello)
    account.handle(link, {'type': 'save_preferences', 'share': True}, None)
    monkeypatch.setenv('AUTOPILOT_SHARE', '0')
    assert not telemetry.sharing(hello)
    for peer in (None, False):
        link.loopback = peer
        account.handle(link, {'type': 'save_preferences', 'share': False}, None)
        assert account.preferences()['share_data'] is True


def test_last_pipe_member_leaving_cannot_remove_a_new_joiner():
    empty, release, joined, stop, trying = (threading.Event() for _ in range(5))

    class Members(list):
        def __bool__(self):
            if len(self) == 0 and not empty.is_set():
                empty.set()
                assert release.wait(3)
            return len(self) != 0

    class Peer:
        remote_address = ('127.0.0.1', 1)
        def __init__(self, wait=False):
            self.wait = wait
            self.sent = []
        def __iter__(self):
            if self.wait:
                joined.set()
                assert stop.wait(3)
            return iter(())
        def send(self, text):
            self.sent.append(text)

    state = {'pipes': {'key': Members()}, 'sessions': {}, 'pipes_lock': threading.Lock()}
    old, new = Peer(), Peer(True)
    leaving = threading.Thread(target=bridge.pipe, args=(old, 'key', state))
    def join():
        trying.set()
        bridge.pipe(new, 'key', state)
    joining = threading.Thread(target=join)
    leaving.start()
    try:
        assert empty.wait(3)
        joining.start()
        assert trying.wait(3)
        release.set()
        leaving.join(3)
        assert joined.wait(3)
        bridge.pipe_send(state, 'key', 'still reachable')
        assert new.sent == ['still reachable']
    finally:
        release.set()
        stop.set()
        leaving.join(3)
        if joining.ident:
            joining.join(3)
