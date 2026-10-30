"""A game's memory kept with Aura (autopilot/aura_memory.py): saved after a run, restored on a machine without it, a
cleared memory staying cleared, what is never sent, and failures never failing the run. Aura's memory route and the
storage's signed URLs are played by a local HTTP server."""

import base64
import hashlib
import http.server
import json
import os
import threading

import pytest

from autopilot import aura_memory, memory


class Storage:
    """Aura's POST /api/autopilot/memory plus the signed URLs it hands out, on 127.0.0.1."""

    def __init__(self, token="grt1.run"):
        self.token = token
        self.files = {}  # path -> bytes
        self.kept_aside = {}  # path -> bytes: what a reset moved where no run reads it
        self.calls = []
        self.fail_puts = False
        self.fail_gets = set()  # paths whose fetch fails (the storage unreachable mid-restore)
        self.refuse_signs = False  # sign answers 429 (a quota)
        self.busy = 0  # the next this many calls to Aura answer 503 (busy)
        store = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, status, body=b"", ctype="application/json"):
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                assert self.headers["Authorization"] == f"Bearer {store.token}"
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                store.calls.append(body)
                if store.busy > 0:
                    store.busy -= 1
                    return self._send(503, b'{"error": "busy"}')
                base = f"http://127.0.0.1:{self.server.server_port}"
                if body["op"] == "list":
                    out = {"files": [{"path": p, "md5": _md5(b), "size": len(b)} for p, b in store.files.items()]}
                elif body["op"] == "sign" and store.refuse_signs:
                    return self._send(429, b'{"code": "QUOTA_EXCEEDED", "direction": "get"}')
                elif body["op"] == "sign":
                    out = {"put": [{"path": f["path"], "url": f"{base}/obj/{f['path']}",
                                    "headers": {"x-goog-hash": f"md5={f['md5']}"}} for f in body.get("put", [])],
                           "get": [{"path": p, "url": f"{base}/obj/{p}"} for p in body.get("get", [])]}
                elif body["op"] == "reset":  # (the learned files moved aside: notes.md, lib.py, runs/)
                    learned = [p for p in store.files if p in ("notes.md", "lib.py") or p.startswith("runs/")]
                    for p in learned:
                        store.kept_aside[p] = store.files.pop(p)
                    out = {"moved": len(learned)}
                else:
                    for p in body["paths"]:
                        store.files.pop(p, None)
                    out = {"deleted": len(body["paths"])}
                self._send(200, json.dumps(out).encode())

            def do_PUT(self):
                data = self.rfile.read(int(self.headers["Content-Length"]))
                path = self.path[len("/obj/"):]
                if store.fail_puts:
                    if store.fail_puts is not True:
                        store.fail_puts -= 1  # (a count: down only that many times)
                    return self._send(503, b"down")
                assert self.headers["x-goog-hash"] == f"md5={_md5(data)}"  # (as the storage checks it)
                store.files[path] = data
                self._send(200)

            def do_GET(self):
                path = self.path[len("/obj/"):]
                if path in store.fail_gets:
                    return self._send(503)
                if path not in store.files:
                    return self._send(404)
                self._send(200, store.files[path], "application/octet-stream")

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/api/autopilot/memory"

    def sync(self):
        return aura_memory.MemorySync(self.url, "grt1.run")

    def install_sync(self):
        """The copy kept for this install (runs with the user's own keys): its token, and the project named."""
        return aura_memory.MemorySync(self.url, self.token, project="9fe07ce6-6c8f", copy="install",
                                      sync_file=aura_memory.INSTALL_SYNC_FILE)


def _md5(data):
    return base64.b64encode(hashlib.md5(data).digest()).decode()


def write(folder, rel, text):
    full = os.path.join(folder, *rel.split("/"))
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(text)


@pytest.fixture
def storage():
    s = Storage()
    yield s
    s.server.shutdown()


def test_a_run_saves_its_memory_and_a_new_machine_gets_it_back(storage, tmp_path):
    a = str(tmp_path / "machine-a" / "Farm")
    write(a, "notes.md", "the pump needs 3 presses")
    write(a, "lib.py", "def pump(): pass")
    write(a, "codex/source/Pump.cpp", "void Pump() {}")
    write(a, "runs/20261010-090000/report.json", "{}")
    write(a, "project.json", '{"project": "C:/Users/a/Farm/Farm.uproject"}')
    write(a, ".agent/projects/session.jsonl", "a transcript")
    write(a, "runs/20261010-090000/__pycache__/harness.cpython-312.pyc", "bytecode")
    write(a, ".codex-seen.json.gz", "the codex's paths and hashes")  # (the next run's changes count from it)
    assert storage.sync().save(a) == (4, 0)
    assert sorted(storage.files) == [".codex-seen.json.gz", "lib.py", "notes.md",
                                     "runs/20261010-090000/report.json"]  # (never the codex itself)

    b = str(tmp_path / "machine-b" / "Farm")
    os.makedirs(b)
    assert storage.sync().restore(b) == 4
    assert open(os.path.join(b, "notes.md"), encoding="utf-8").read() == "the pump needs 3 presses"
    assert not os.path.exists(os.path.join(b, "project.json"))  # (where the project is on machine a)
    assert memory.state(b) == {"runs": 1, "notes": True, "lib": True}
    assert storage.sync().save(b) == (0, 0)  # (nothing new: nothing sent)


def test_a_machine_with_its_own_memory_or_one_synced_before_is_not_restored_over(storage, tmp_path):
    storage.files["notes.md"] = b"from aura"
    own = str(tmp_path / "own")
    write(own, "notes.md", "mine")
    assert storage.sync().restore(own) == 0
    assert open(os.path.join(own, "notes.md"), encoding="utf-8").read() == "mine"
    synced = str(tmp_path / "synced")
    write(synced, aura_memory.SYNC_FILE, json.dumps({"files": {}}))
    assert storage.sync().restore(synced) == 0 and not os.path.exists(os.path.join(synced, "notes.md"))


def test_a_reset_memory_is_reset_in_aura_too_its_files_kept_aside_never_deleted(storage, tmp_path):
    a = str(tmp_path / "a")
    write(a, "notes.md", "n")
    write(a, "runs/r1/report.json", "{}")
    storage.sync().save(a)
    storage.files["runs/r2/report.json"] = b"{} from machine b"
    assert memory.forget(a) == 2  # (Reset memory in the Autopilot tab)
    assert storage.sync().save(a) == (0, 0)  # (moved aside by Aura, not deleted by this machine)
    assert [c["op"] for c in storage.calls if c["op"] in ("reset", "delete")] == ["reset"]
    assert storage.files == {}
    assert sorted(storage.kept_aside) == ["notes.md", "runs/r1/report.json", "runs/r2/report.json"]
    with open(os.path.join(a, memory.RESET_MARK), encoding="utf-8") as f:
        assert json.load(f)["passed"] == ["aura"]  # (passed on to this copy, once)
    assert storage.sync().restore(a) == 0  # (reset, not new: stays reset)
    write(a, "notes.md", "learned again")
    assert storage.sync().save(a) == (1, 0) and storage.files["notes.md"] == b"learned again"


def test_runs_with_own_keys_keep_their_own_copy_and_a_reset_reaches_both_copies(storage, tmp_path):
    """Runs with the user's own keys keep the memory with Aura for this install (when shared); a Reset memory reaches
    the team's copy and the install's, each at its next save, whichever comes first."""
    install = Storage(token="gti1.install")
    try:
        a = str(tmp_path / "a")
        write(a, "notes.md", "n")
        write(a, "runs/r1/report.json", "{}")
        assert install.install_sync().save(a) == (2, 0)  # (a run with own keys)
        assert all(c.get("project") == "9fe07ce6-6c8f" for c in install.calls)
        assert storage.sync().save(a) == (2, 0)  # (a run through Aura)
        assert "project" not in storage.calls[0]  # (Aura takes the project from the run)
        assert os.path.exists(os.path.join(a, aura_memory.INSTALL_SYNC_FILE))
        assert os.path.exists(os.path.join(a, aura_memory.SYNC_FILE))
        memory.forget(a)
        assert install.install_sync().save(a) == (0, 0) and sorted(install.kept_aside) == ["notes.md",
                                                                                          "runs/r1/report.json"]
        assert install.install_sync().save(a) == (0, 0)  # (passed on once)
        assert [c["op"] for c in install.calls].count("reset") == 1
        assert "notes.md" in storage.files  # (the team's copy: not yet)
        assert storage.sync().save(a) == (0, 0) and "notes.md" in storage.kept_aside
        with open(os.path.join(a, memory.RESET_MARK), encoding="utf-8") as f:
            assert json.load(f)["passed"] == ["install", "aura"]
        memory.forget(a)  # (nothing learned since: nothing to move, no new mark)
        write(a, "notes.md", "again")
        memory.forget(a)  # (a new reset: pending for both copies again)
        with open(os.path.join(a, memory.RESET_MARK), encoding="utf-8") as f:
            assert "passed" not in json.load(f)
    finally:
        install.server.shutdown()


def test_a_reset_aura_couldnt_take_is_passed_on_by_a_later_save(storage, tmp_path, monkeypatch):
    a = str(tmp_path / "a")
    write(a, "notes.md", "n")
    storage.sync().save(a)
    memory.forget(a)
    sync = storage.sync()
    real = sync._call
    monkeypatch.setattr(sync, "_call", lambda body: (_ for _ in ()).throw(aura_memory.SyncError("down"))
                        if body["op"] == "reset" else real(body))
    assert sync.save(a) == (0, 0) and "notes.md" in storage.files
    assert os.path.exists(os.path.join(a, memory.RESET_MARK))
    assert storage.sync().save(a) == (0, 0) and "notes.md" in storage.kept_aside


def test_a_restore_fetches_only_the_recent_runs_and_a_save_never_removes_the_older_ones(storage, tmp_path):
    for i in range(aura_memory.RECENT_RUNS + 3):
        storage.files[f"runs/202610{i:02d}-000000/report.json"] = b"{}"
    fresh = str(tmp_path / "fresh")
    os.makedirs(fresh)
    assert storage.sync().restore(fresh) == aura_memory.RECENT_RUNS
    assert storage.sync().save(fresh) == (0, 0)
    assert len(storage.files) == aura_memory.RECENT_RUNS + 3


def test_changed_files_are_sent_again_and_big_ones_never(storage, tmp_path, monkeypatch):
    a = str(tmp_path / "a")
    write(a, "notes.md", "v1")
    write(a, "runs/r1/big.bin", "x" * 50)
    monkeypatch.setattr(aura_memory, "MAX_FILE_BYTES", 10)
    assert storage.sync().save(a) == (1, 0) and "runs/r1/big.bin" not in storage.files
    write(a, "notes.md", "v2")
    assert storage.sync().save(a) == (1, 0) and storage.files["notes.md"] == b"v2"


def test_a_failed_save_or_restore_is_reported_and_never_raises(storage, tmp_path, capsys):
    a = str(tmp_path / "a")
    write(a, "notes.md", "n")
    storage.fail_puts = True
    assert storage.sync().save(a) == (0, 0)
    assert "Couldn't save the memory to Aura" in capsys.readouterr().out
    assert not os.path.exists(os.path.join(a, aura_memory.SYNC_FILE))  # (nothing recorded as sent)
    down = aura_memory.MemorySync("http://127.0.0.1:1/api/autopilot/memory", "grt1.run")
    assert down.restore(str(tmp_path / "empty")) == 0


def test_a_save_or_restore_that_fails_for_a_passing_reason_is_tried_again(storage, tmp_path, no_retry_waits):
    """Aura busy (503) or the storage down for a moment: tried again after short waits that double, and it arrives;
    each try goes on from what Aura holds already."""
    a = str(tmp_path / "a")
    write(a, "notes.md", "n")
    write(a, "lib.py", "l")
    storage.busy = 2
    assert storage.sync().save(a) == (2, 0)
    assert storage.files == {"notes.md": b"n", "lib.py": b"l"}
    storage.fail_puts = 1
    write(a, "notes.md", "n2")
    assert storage.sync().save(a) == (1, 0) and storage.files["notes.md"] == b"n2"
    assert len(no_retry_waits) == 3
    for wait, first in zip(no_retry_waits, (2, 4, 2)):
        assert 0.8 * first <= wait <= 1.2 * first
    fresh = str(tmp_path / "b")
    storage.busy = 1
    assert storage.sync().restore(fresh) == 2 and read(fresh, "notes.md") == "n2"


def test_a_save_or_restore_refused_or_down_for_good_is_reported_after_the_tries(storage, tmp_path, no_retry_waits,
                                                                                 capsys):
    a = str(tmp_path / "a")
    write(a, "notes.md", "n")
    storage.refuse_signs = True  # (429: the day's quota, never tried again now)
    assert storage.sync().save(a) == (0, 0)
    assert [c["op"] for c in storage.calls] == ["list", "sign"] and no_retry_waits == []
    storage.refuse_signs = False
    storage.calls.clear()
    storage.fail_puts = True  # (down for good: tried 1 + 3 times)
    assert storage.sync().save(a) == (0, 0)
    assert [c["op"] for c in storage.calls].count("sign") == 4 and len(no_retry_waits) == 3
    assert "Couldn't save the memory to Aura" in capsys.readouterr().out


def read(folder, rel):
    with open(os.path.join(folder, *rel.split("/")), encoding="utf-8") as f:
        return f.read()


def team_memory(storage):
    storage.files.update({"notes.md": b"the pump needs 3 presses", "lib.py": b"def pump(): pass",
                          "codex/Pump.cpp": b"void Pump() {}", "runs/20261010-090000/report.json": b"{}"})


def test_a_failed_restore_leaves_the_folder_as_it_was_and_the_next_run_tries_again(storage, tmp_path):
    """A restore that fails partway (a file the storage won't give) writes nothing into the folder: no half memory
    that would stop the next run's restore. The next run, still without anything learned, restores it all."""
    team_memory(storage)
    fresh = str(tmp_path / "memory" / "Farm")
    storage.fail_gets = {"runs/20261010-090000/report.json"}
    assert storage.sync().restore(fresh) == 0
    assert memory.files(fresh).keys() == {aura_memory.SYNC_FILE}  # (the pending mark only)
    assert aura_memory._restore_pending(fresh)
    assert os.listdir(tmp_path / "memory" / aura_memory.RESTORE_STAGING) == []  # (no staging left behind)
    storage.fail_gets = set()
    assert storage.sync().restore(fresh) == 3  # (never the codex an older Autopilot stored)
    assert read(fresh, "notes.md") == "the pump needs 3 presses" and not aura_memory._restore_pending(fresh)
    assert not os.path.exists(os.path.join(fresh, "codex"))
    assert storage.sync().restore(fresh) == 0  # (done: never again)


def test_a_run_after_a_failed_restore_never_overwrites_the_stored_memory_and_gets_it_next_run(storage, tmp_path):
    """The run goes on without the stored memory and learns from scratch. Its save sends only what Aura doesn't hold
    (its new run), never over the stored notes.md and lib.py. The next run's restore brings them in, keeping this
    machine's own aside."""
    team_memory(storage)
    fresh = str(tmp_path / "memory" / "Farm")
    storage.refuse_signs = True  # (a quota)
    assert storage.sync().restore(fresh) == 0
    storage.refuse_signs = False
    write(fresh, "notes.md", "learned from scratch")
    write(fresh, "lib.py", "def other(): pass")
    write(fresh, "codex/Pump.cpp", "void Pump() { rebuilt }")
    write(fresh, "runs/20261014-100000/report.json", '{"new": true}')
    assert storage.sync().save(fresh) == (1, 1)  # (its new run; the codex an older Autopilot stored is removed)
    assert storage.files["notes.md"] == b"the pump needs 3 presses"
    assert storage.files["lib.py"] == b"def pump(): pass"
    assert "codex/Pump.cpp" not in storage.files
    assert storage.files["runs/20261014-100000/report.json"] == b'{"new": true}'
    assert aura_memory._restore_pending(fresh)

    assert storage.sync().restore(fresh) == 3  # (notes.md, lib.py and the old run; never the codex)
    assert read(fresh, "notes.md") == "the pump needs 3 presses" and read(fresh, "lib.py") == "def pump(): pass"
    assert read(fresh, "codex/Pump.cpp") == "void Pump() { rebuilt }"
    assert memory.state(fresh) == {"runs": 2, "notes": True, "lib": True}
    kept = tmp_path / "memory" / aura_memory.RESTORE_BACKUP / "Farm"
    (stamp,) = os.listdir(kept)
    assert read(str(kept / stamp), "notes.md") == "learned from scratch"  # (kept aside, never deleted)
    assert not aura_memory._restore_pending(fresh)
    # from now on, an ordinary save: what changed here is sent
    write(fresh, "notes.md", "the pump needs 3 presses; the gate opens at dusk")
    assert storage.sync().save(fresh) == (1, 0)  # (notes.md; never the codex)
    assert storage.files["notes.md"] == b"the pump needs 3 presses; the gate opens at dusk"


def test_a_pending_restore_with_nothing_stored_any_more_ends(storage, tmp_path):
    fresh = str(tmp_path / "memory" / "Farm")
    aura_memory._write_baseline(fresh, {}, pending=True)
    write(fresh, "notes.md", "mine")
    assert storage.sync().restore(fresh) == 0 and not aura_memory._restore_pending(fresh)
    assert storage.sync().save(fresh) == (1, 0)


def test_a_pending_restore_is_per_copy(storage, tmp_path):
    fresh = str(tmp_path / "memory" / "Farm")
    team_memory(storage)
    storage.refuse_signs = True
    assert storage.install_sync().restore(fresh) == 0
    assert aura_memory._restore_pending(fresh, aura_memory.INSTALL_SYNC_FILE)
    assert not aura_memory._restore_pending(fresh, aura_memory.SYNC_FILE)


def test_a_path_from_aura_cannot_leave_the_memory_folder(tmp_path):
    folder = str(tmp_path / "Farm")
    for bad in ("../escape.txt", "codex/../../escape.txt", ""):
        with pytest.raises(aura_memory.SyncError):
            aura_memory._inside(folder, bad)
    assert aura_memory._inside(folder, "codex/a.txt") == os.path.join(folder, "codex", "a.txt")
