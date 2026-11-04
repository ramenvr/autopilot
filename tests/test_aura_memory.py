"""A game's memory kept with Aura (autopilot/aura_memory.py): the same in every folder that syncs it, both ways, a
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
        self.truncated = False  # a listing cut short
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
                    out = {"files": [{"path": p, "md5": _md5(b), "size": len(b)} for p, b in store.files.items()],
                           "truncated": store.truncated}
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


def test_a_folder_with_its_own_memory_or_one_synced_before_takes_part_in_every_sync(storage, tmp_path):
    """A restore used to run once per folder, so two folders drifted apart for good: now neither state stops one."""
    storage.files["notes.md"] = b"from aura\n"
    own = str(tmp_path / "own")
    write(own, "notes.md", "mine\n")
    assert storage.sync().restore(own) == 1
    assert read(own, "notes.md") == "from aura\nmine\n"
    synced = str(tmp_path / "synced")
    write(synced, aura_memory.SYNC_FILE, json.dumps({"files": {}}))
    assert storage.sync().restore(synced) == 1 and read(synced, "notes.md") == "from aura\n"


def test_a_reset_memory_is_reset_in_aura_too_its_files_kept_aside_never_deleted(storage, tmp_path):
    a = str(tmp_path / "a")
    write(a, "notes.md", "n")
    write(a, "runs/r1/report.json", "{}")
    storage.sync().save(a)
    storage.files["runs/r2/report.json"] = b"{} from machine b"
    assert memory.forget(a) == 2  # (Reset memory in the Autopilot tab)
    assert storage.sync().save(a) == (0, 0)  # (moved aside by Aura, not deleted by this machine)
    assert [c["op"] for c in storage.calls if c["op"] in ("reset", "delete")] == ["reset"]
    assert list(storage.files) == [aura_memory.RESET_ID]
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
    """A restore that fails partway (a file the storage won't give) writes nothing into the folder. The next run
    restores it all."""
    team_memory(storage)
    fresh = str(tmp_path / "memory" / "Farm")
    os.makedirs(fresh)
    storage.fail_gets = {"runs/20261010-090000/report.json"}
    assert storage.sync().restore(fresh) == 0
    assert memory.files(fresh) == {}
    assert os.listdir(tmp_path / "memory" / aura_memory.RESTORE_STAGING) == []  # (no staging left behind)
    storage.fail_gets = set()
    assert storage.sync().restore(fresh) == 3  # (never the codex an older Autopilot stored)
    assert read(fresh, "notes.md") == "the pump needs 3 presses"
    assert not os.path.exists(os.path.join(fresh, "codex"))
    assert storage.sync().restore(fresh) == 0  # (done: nothing new)


def test_a_run_after_a_failed_restore_keeps_what_it_learned_beside_the_stored_memory(storage, tmp_path):
    """The run goes on without the stored memory and learns from scratch. Its save brings the stored memory in rather
    than sending over it, so what it learned and what was stored both survive, here and in Aura."""
    team_memory(storage)
    fresh = str(tmp_path / "memory" / "Farm")
    storage.refuse_signs = True  # (a quota)
    assert storage.sync().restore(fresh) == 0
    storage.refuse_signs = False
    write(fresh, "notes.md", "learned from scratch\n")
    write(fresh, "lib.py", "def other(): pass\n")
    write(fresh, "codex/Pump.cpp", "void Pump() { rebuilt }")
    write(fresh, "runs/20261014-100000/report.json", '{"new": true}')
    # (notes.md, the kept lib.py and its prompt.txt, its run; the codex an older Autopilot stored is removed)
    assert storage.sync().save(fresh) == (4, 1)
    assert storage.files["notes.md"] == b"the pump needs 3 presses\nlearned from scratch\n"
    assert storage.files["lib.py"] == b"def pump(): pass"  # (other folders build on it)
    assert read(fresh, "lib.py") == "def pump(): pass"
    kept = [p for p in storage.files if p.startswith("runs/conflict-") and p.endswith("/lib.py")]
    assert len(kept) == 1 and read(fresh, kept[0]) == "def other(): pass\n"
    assert "codex/Pump.cpp" not in storage.files
    assert read(fresh, "codex/Pump.cpp") == "void Pump() { rebuilt }"
    assert storage.files["runs/20261014-100000/report.json"] == b'{"new": true}'
    assert storage.sync().restore(fresh) == 0 and storage.sync().save(fresh) == (0, 0)


def test_a_sync_file_left_pending_by_an_older_autopilot_is_read_as_one_with_no_agreed_copy(storage, tmp_path):
    fresh = str(tmp_path / "memory" / "Farm")
    write(fresh, aura_memory.SYNC_FILE, json.dumps({"files": {}, "restore": "pending"}))
    write(fresh, "notes.md", "mine\n")
    assert storage.sync().restore(fresh) == 0
    assert storage.sync().save(fresh) == (1, 0)


# -- two folders on one stored copy -----------------------------------------------------------------------------------

@pytest.fixture
def folders(tmp_path):
    return str(tmp_path / "one" / "memory" / "Farm"), str(tmp_path / "two" / "memory" / "Farm")


def play(storage, folder, note, run_id):
    """One run: fetch, learn, save."""
    storage.sync().restore(folder)
    before = read(folder, "notes.md") if os.path.exists(os.path.join(folder, "notes.md")) else ""
    write(folder, "notes.md", before + note + "\n")
    write(folder, f"runs/{run_id}/report.json", json.dumps({"run": run_id}))
    storage.sync().save(folder)


def learned(folder):
    return {rel: open(full, "rb").read() for rel, full in aura_memory.synced_files(folder).items()}


def puts(storage):
    return sum(len(c.get("put", [])) for c in storage.calls if c["op"] == "sign")


def test_each_folder_sees_what_the_other_saved_in_both_directions(storage, folders):
    a, b = folders
    play(storage, a, "first", "r1")
    play(storage, b, "second", "r2")
    play(storage, a, "third", "r3")
    play(storage, b, "fourth", "r4")
    storage.sync().restore(a)
    assert read(a, "notes.md") == read(b, "notes.md") == "first\nsecond\nthird\nfourth\n"
    assert memory.state(a) == memory.state(b) == {"runs": 4, "notes": True, "lib": False}
    assert learned(a) == learned(b) == storage.files


def test_notes_edited_in_both_folders_between_syncs_keep_both_and_repeated_syncs_do_nothing(storage, folders):
    a, b = folders
    play(storage, a, "shared", "r1")
    storage.sync().restore(b)
    write(a, "notes.md", "shared\nfrom a\n")
    write(b, "notes.md", "shared\nfrom b\n")
    storage.sync().save(a)
    storage.sync().save(b)  # (never synced since a's save: merged, not overwritten)
    storage.sync().restore(a)
    for notes in (read(a, "notes.md"), read(b, "notes.md"), storage.files["notes.md"].decode()):
        assert sorted(notes.split()) == sorted("shared from a from b".split()) and notes.count("from a") == 1
    assert read(a, "notes.md") == read(b, "notes.md") == storage.files["notes.md"].decode()

    calls = puts(storage)
    for folder in (a, b, a, b):
        assert storage.sync().restore(folder) == 0
        assert storage.sync().save(folder) == (0, 0)
    assert puts(storage) == calls


def test_two_folders_full_of_their_own_memory_converge(storage, folders):
    """Every folder today: memory of its own, never synced with the other."""
    a, b = folders
    write(a, "notes.md", "# Farm\nthe pump needs 3 presses\n")
    write(a, "lib.py", "def pump(): pass\n")
    write(a, "runs/r1/report.json", "{}")
    storage.sync().save(a)
    write(b, "notes.md", "# Farm\nthe gate opens at dusk\n")
    write(b, "lib.py", "def gate(): pass\n")
    write(b, "runs/r2/report.json", "{}")
    storage.sync().restore(b)
    storage.sync().save(b)
    storage.sync().restore(a)
    assert read(a, "notes.md") == read(b, "notes.md")
    assert "the pump needs 3 presses" in read(a, "notes.md") and "the gate opens at dusk" in read(a, "notes.md")
    assert read(a, "lib.py") == read(b, "lib.py") == "def pump(): pass\n"  # (stored: other folders build on it)
    kept = [p for p in learned(a) if p.startswith("runs/conflict-") and p.endswith("/lib.py")]
    assert len(kept) == 1 and read(a, kept[0]) == read(b, kept[0]) == "def gate(): pass\n"  # (kept and synced)
    assert read(a, kept[0][:-len("lib.py")] + "prompt.txt")  # (so the next run is told of it)
    assert learned(a) == learned(b) == storage.files


def test_a_folder_an_older_autopilot_synced_merges_rather_than_trusting_its_baseline(storage, folders):
    """An older save overwrote the stored notes.md, so a folder's record of what it sent isn't a copy both agreed on:
    taken as one, the other folder's notes would replace this folder's and what it learned would be gone."""
    a, b = folders
    write(a, "notes.md", "from a\n")
    write(a, aura_memory.SYNC_FILE, json.dumps({"files": {"notes.md": _md5(b"from a\n")}}))
    storage.files["notes.md"] = b"from b\n"  # (the other folder's save, over a's)
    storage.sync().restore(a)
    assert read(a, "notes.md") == "from b\nfrom a\n"
    storage.sync().save(a)
    assert storage.files["notes.md"] == b"from b\nfrom a\n"


def test_a_reset_in_either_folder_reaches_the_other_and_stays_reset(storage, folders, tmp_path):
    for first, second in (folders, reversed(folders)):
        play(storage, first, "old", "r1")
        storage.sync().restore(second)
        write(second, "notes.md", "old\nedited while stale\n")
        memory.forget(first)
        storage.sync().save(first)
        assert not any(aura_memory.is_learned(p) for p in storage.files)

        storage.sync().restore(second)  # (stale: what it had synced goes aside, its edit too, and nothing comes back)
        assert memory.state(second) == {"runs": 0, "notes": False, "lib": False}
        assert storage.sync().save(second) == (0, 0)
        assert not any(aura_memory.is_learned(p) for p in storage.files)
        backups = tmp_path.glob(f"*/memory/{memory.RESET_BACKUP}/Farm/*/notes.md")
        assert any("edited while stale" in p.read_text(encoding="utf-8") for p in backups)

        play(storage, second, "learned again", "r2")  # (what is learned after the reset goes everywhere)
        storage.sync().restore(first)
        assert read(first, "notes.md") == "learned again\n" and memory.state(first)["runs"] == 1
        memory.forget(first)
        storage.sync().save(first)
        storage.sync().restore(second)
        assert memory.state(second)["runs"] == 0


def test_a_run_going_on_during_a_reset_elsewhere_sends_only_what_it_learned_after(storage, folders):
    a, b = folders
    play(storage, a, "old", "r1")
    storage.sync().restore(b)
    memory.forget(a)
    storage.sync().save(a)
    write(b, "notes.md", "old\nmid run\n")  # (b's run is still going)
    write(b, "runs/r2/report.json", "{}")
    storage.sync().save(b)
    assert "notes.md" not in storage.files and "runs/r1/report.json" not in storage.files
    assert storage.files["runs/r2/report.json"] == b"{}"


def test_a_reset_that_arrives_through_one_copy_reaches_the_folders_other_copy(storage, folders, tmp_path):
    a, b = folders
    install = Storage(token="gti1.install")
    try:
        play(storage, a, "old", "r1")
        storage.sync().restore(b)
        install.install_sync().save(b)
        assert "notes.md" in install.files
        memory.forget(a)
        storage.sync().save(a)
        storage.sync().restore(b)
        assert install.install_sync().restore(b) == 0
        assert "notes.md" not in install.files and "notes.md" in install.kept_aside
    finally:
        install.server.shutdown()


def test_a_removal_goes_both_ways_and_a_file_never_fetched_is_no_removal(storage, folders):
    a, b = folders
    write(a, "runs/r1/report.json", "{}")
    write(a, "runs/r2/report.json", "{}")
    write(a, "notes.md", "stays\n")
    storage.sync().save(a)
    storage.sync().restore(b)
    os.remove(os.path.join(a, "runs", "r1", "report.json"))
    assert storage.sync().save(a) == (0, 1)
    storage.sync().restore(b)
    assert not os.path.exists(os.path.join(b, "runs", "r1", "report.json"))
    os.remove(os.path.join(b, "runs", "r2", "report.json"))
    assert storage.sync().save(b) == (0, 1)
    storage.sync().restore(a)
    assert list(aura_memory.synced_files(a)) == ["notes.md"]

    write(a, "runs/r3/report.json", "{}")
    storage.sync().save(a)
    assert storage.sync().save(b) == (0, 0) and "runs/r3/report.json" in storage.files  # (b never had it)


def test_a_file_changed_on_one_side_and_removed_on_the_other_is_kept(storage, folders):
    a, b = folders
    write(a, "notes.md", "one\n")
    storage.sync().save(a)
    storage.sync().restore(b)
    write(a, "notes.md", "one\ntwo\n")
    os.remove(os.path.join(b, "notes.md"))
    storage.sync().save(a)
    assert storage.sync().save(b) == (0, 0) and read(b, "notes.md") == "one\ntwo\n"


def test_a_failed_pull_changes_nothing_in_a_folder_that_holds_memory(storage, folders):
    a, b = folders
    play(storage, a, "one", "r1")
    storage.sync().restore(b)
    play(storage, a, "two", "r2")
    before = learned(b)
    storage.fail_gets = {"runs/r2/report.json"}
    assert storage.sync().restore(b) == 0 and learned(b) == before
    storage.fail_gets = set()
    assert storage.sync().restore(b) == 2 and learned(b) == storage.files


def test_a_save_that_catches_another_folders_save_merges_instead_of_overwriting(storage, folders):
    a, b = folders
    play(storage, a, "one", "r1")
    storage.sync().restore(b)
    write(a, "notes.md", "one\nfrom a\n")
    storage.sync().save(a)
    write(b, "notes.md", "one\nfrom b\n")  # (b started before a's save)
    storage.sync().save(b)
    assert sorted(storage.files["notes.md"].decode().split()) == sorted("one from a from b".split())


def test_a_send_another_folder_overwrote_at_that_moment_is_merged_by_the_next_sync(storage, folders):
    """The storage has no conditional write: what a save can do is notice afterwards."""
    a, _ = folders
    write(a, "notes.md", "one\n")
    storage.sync().save(a)
    write(a, "notes.md", "one\nfrom a\n")
    sync = storage.sync()
    send = sync._put_all

    def raced(items):
        send(items)
        storage.files["notes.md"] = b"one\nfrom elsewhere\n"

    sync._put_all = raced
    sync.save(a)
    storage.sync().restore(a)
    storage.sync().save(a)
    assert sorted(storage.files["notes.md"].decode().split()) == sorted("one from a from elsewhere".split())


def test_a_store_with_nothing_in_it_is_no_reset(storage, folders):
    """Erased, or the run's account has another store: the folder's memory is sent to it, never moved aside."""
    a, _ = folders
    play(storage, a, "one", "r1")
    storage.files.clear()
    assert storage.sync().restore(a) == 0 and read(a, "notes.md") == "one\n"
    assert storage.sync().save(a) == (2, 0) and "notes.md" in storage.files


def test_a_listing_cut_short_removes_nothing(storage, folders):
    a, _ = folders
    play(storage, a, "one", "r1")
    del storage.files["runs/r1/report.json"]
    storage.truncated = True
    storage.sync().restore(a)
    assert os.path.exists(os.path.join(a, "runs", "r1", "report.json"))


def test_nothing_new_costs_one_listing_and_reads_no_file(storage, folders, monkeypatch):
    a, b = folders
    play(storage, a, "one", "r1")
    storage.sync().restore(b)
    storage.calls.clear()
    monkeypatch.setattr(aura_memory, "md5_of", lambda path: pytest.fail(f"read {path}"))
    assert storage.sync().restore(b) == 0
    assert [c["op"] for c in storage.calls] == ["list"]


def test_a_pull_never_brings_in_a_stored_codex_or_a_reset_id(storage, folders):
    a, b = folders
    play(storage, a, "one", "r1")
    storage.files["codex/Pump.cpp"] = b"void Pump() {}"
    storage.files[aura_memory.RESET_ID] = b"{}"
    storage.sync().restore(b)
    assert not os.path.exists(os.path.join(b, "codex")) and not os.path.exists(os.path.join(b, aura_memory.RESET_ID))


def test_a_pull_fetches_only_the_recent_runs_and_a_newer_run_arrives(storage, folders):
    a, b = folders
    for i in range(aura_memory.RECENT_RUNS + 2):
        storage.files[f"runs/202610{i:02d}-000000/report.json"] = b"{}"
    assert storage.sync().restore(b) == aura_memory.RECENT_RUNS
    storage.files["runs/20261101-000000/report.json"] = b"{}"
    assert storage.sync().restore(b) == 1


def test_merge_notes_keeps_every_line_once_in_a_stable_order_and_is_idempotent():
    merge = aura_memory.merge_notes
    stored, local = b"# Farm\n\npump\ngate\n", b"# Farm\n\npump\nmine\n\nblock\ngate\nlast\n"
    merged = merge(stored, local)
    assert merged == b"# Farm\n\npump\nmine\n\nblock\ngate\nlast\n"
    assert merge(merged, local) == merged and merge(merged, stored) == merged and merge(stored, stored) == stored
    assert merge(b"one\n", b"two\n") == b"one\ntwo\n"  # (nothing in common: after what is stored)
    assert merge(b"one\r\n", b"one\n") == b"one\r\n"  # (the same line)
    assert merge(b"one", b"one\ntwo") == b"one\ntwo\n"
    assert merge(b"\xff\xfe", b"text") is None


def test_a_path_from_aura_cannot_leave_the_memory_folder(tmp_path):
    folder = str(tmp_path / "Farm")
    for bad in ("../escape.txt", "codex/../../escape.txt", ""):
        with pytest.raises(aura_memory.SyncError):
            aura_memory._inside(folder, bad)
    assert aura_memory._inside(folder, "codex/a.txt") == os.path.join(folder, "codex", "a.txt")
