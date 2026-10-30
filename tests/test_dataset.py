"""A run's data as it is shared (autopilot/dataset.py, autopilot/redact.py): one clean folder per run, the
same layout every time, with no secret, home folder, user or computer name or email address left in it."""

import gzip
import json
import os

from autopilot import dataset, redact

HOME = r"C:\Users\jdoe"
KEY = "sk-ant-api03-" + "A1b2C3d4" * 6
SECRETS = (KEY, "gti1.0123456789ab.abcdefghij0123", "AKIAABCDEFGHIJKLMNOP", "hunter2hunter2",
           "9f8e7d6c5b4a39281706f5e4d3c2b1a0", "jdoe@example.com", "jdoe", "JDOE-PC", "ghp_" + "x" * 36)


def redactor():
    return redact.Redactor(env={"MY_API_KEY": "hunter2hunter2", "PATH": "x"}, user="jdoe", host="JDOE-PC", home=HOME)


def test_text_loses_secrets_homes_names_and_emails_and_keeps_the_rest():
    r = redactor()
    text = (f"key {KEY} in C:\\Users\\jdoe\\Documents\\Game and /home/jdoe/x; token=hunter2hunter2; "
            f"SecurityToken=9f8e7d6c5b4a39281706f5e4d3c2b1a0; mail jdoe@example.com from JDOE-PC by jdoe; "
            f"Authorization: Bearer abcdefghijklmnopqrstuvwxyz; max_tokens=4096; input_tokens: 123456789")
    out = r.text(text)
    for s in SECRETS:
        assert s not in out
    assert "~\\Documents\\Game" in out and "~/x" in out and "Bearer [secret]" in out
    assert "SecurityToken=[secret]" in out and "max_tokens=4096" in out and "input_tokens: 123456789" in out
    assert "[email]" in out and "[host]" in out and "[user]" in out
    assert r.counts["secret"] >= 4 and r.counts["home"] == 2


def test_json_stays_json_with_secret_keys_replaced_and_token_counts_kept():
    r = redactor()
    value = {"api_key": "abcdefgh12345678", "password": "hunter2hunter2", "usage": {"input_tokens": 12, "max_tokens":
             4096, "cache_read_input_tokens": 99999999}, "path": "C:\\\\Users\\\\jdoe\\\\Game", "tokenizer": "x",
             "messages": [{"content": f"the key is {KEY}"}]}
    out = r.data(json.loads(json.dumps(value)))
    assert out["api_key"] == "[secret]" and out["password"] == "[secret]"
    assert out["usage"] == value["usage"] and out["tokenizer"] == "x"
    assert out["path"] == "~\\\\Game" and KEY not in json.dumps(out)


def test_common_user_names_are_left_in_text():
    r = redact.Redactor(env={}, user="dev", host="game", home="/home/dev")
    assert r.text("dev build of the game at /home/dev/x") == "dev build of the game at ~/x"


def test_the_project_folder_becomes_a_placeholder_in_every_spelling():
    r = redact.Redactor(env={}, user="jdoe", host="JDOE-PC", home=HOME, project=r"E:\Projects\Studio\Game")
    assert r.text(r"E:\Projects\Studio\Game\Assets\Scripts\Player.cs(12,4): error") == \
        r"<project>\Assets\Scripts\Player.cs(12,4): error"
    assert r.text("file:e:/projects/studio/game/Packages/com.x") == "file:<project>/Packages/com.x"
    assert r.text(json.dumps(r"E:\Projects\Studio\Game\Assets\x.cs")) == r'"<project>\\Assets\\x.cs"'
    assert r.text(json.dumps(json.dumps(r"E:\Projects\Studio\Game\x.cs"))) == r'"\"<project>\\\\x.cs\""'
    assert r.text(r"E:\Projects\Studio\GameOld\x.txt") == r"[path]\x.txt"  # (not the project's: another folder)
    assert r.counts["path"] == 5
    # Unreal's project is its .uproject; a project in the home folder is the project, not ~
    r = redact.Redactor(env={}, home=HOME, project=HOME + r"\Farm\Farm.uproject")
    assert r.text(HOME + r"\Farm\Config\DefaultEngine.ini and " + HOME + r"\Other") == \
        r"<project>\Config\DefaultEngine.ini and ~\Other"
    r = redact.Redactor(env={}, home="/Users/jdoe", project="/Volumes/Work/Game")
    assert r.text("/Volumes/Work/Game/Assets/a.cs") == "<project>/Assets/a.cs"


def test_other_absolute_paths_keep_only_their_last_part():
    r = redact.Redactor(env={}, user="jdoe", host="JDOE-PC", home=HOME, project=r"E:\Projects\Studio\Game")
    manifest = {"dependencies": {"com.example.package": "file:E:/Projects/Studio/Shared/com.example.package",
                                 "com.unity.inputsystem": "1.11.2"}}
    llm = {"request": {"messages": [{"role": "user", "content": [{"type": "tool_result", "content": json.dumps(
        manifest, indent=2)}]}]}}
    out = json.dumps(r.data(json.loads(json.dumps(llm))))
    assert "Studio" not in out and "file:[path]/com.example.package" in out and "1.11.2" in out
    for path, kept in ((r"D:\Work\Client\Big Game\Source\Foo.cpp:42", r"[path]\Foo.cpp:42"),
                       ("D:/Work/Client/x.json", "[path]/x.json"),
                       (json.dumps(r"D:\Work\Client\x.json"), r'"[path]\\x.json"'),
                       (r"\\nas\share\Studio\build.log", r"[path]\build.log"),
                       (json.dumps(r"\\nas\share\Studio\build.log"), r'"[path]\\build.log"'),
                       ("/Volumes/Work/Studio/a.cs and /mnt/data/studio/b.txt", "[path]/a.cs and [path]/b.txt"),
                       ("file:///D:/Studio/c.txt", "file:///[path]/c.txt"),
                       (r"see D:\Studio\ for details", r"see [path]\Studio\ for details"),
                       # an engine installed anywhere keeps its own folders
                       (r"D:\Epic\UE_5.4\Engine\Source\x.cpp", r"[path]\UE_5.4\Engine\Source\x.cpp"),
                       (r"D:\Unity\6000.0.23f1\Editor\Unity.exe", r"[path]\6000.0.23f1\Editor\Unity.exe")):
        assert r.text(path) == kept, path
    # system paths, an engine where it installs itself, relative paths and URLs stay as they are
    for path in (r"C:\Windows\System32\kernel32.dll", r"C:\Program Files\Unity\Hub\Editor\6000.0.23f1\Editor",
                 r"C:\Program Files\Epic Games\UE_5.4\Engine\Binaries", r"D:\Program Files (x86)\Steam\x",
                 "/usr/lib/x.so", "/Applications/Unity/Hub/Editor/6000.0.23f1", "Assets/Scripts/Player.cs",
                 "https://example.com/data/x", "Packages/data/x", "C:/ is the drive"):
        assert r.text(path) == path, path
    # home folders still become ~, once
    assert r.text(r"C:\Users\jdoe\Documents\x.cs and /home/jdoe/y") == r"~\Documents\x.cs and ~/y"
    assert r.counts["home"] == 2


def write(root, rel, text):
    p = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    opener = gzip.open if rel.endswith(".gz") else open
    with opener(p, "wt", encoding="utf-8") as f:
        f.write(text)


def a_project(tmp_path):
    """A project's memory with one shared run full of what must not be sent."""
    root = str(tmp_path / "memory" / "Farm")
    run = os.path.join(root, "runs", "20261014-101500")
    write(root, "codex/config/DefaultEngine.ini", "[RemoteControl]\nSecurityToken=9f8e7d6c5b4a39281706f5e4d3c2b1a0\n")
    write(root, "codex/source/Pump.cpp", "// by jdoe@example.com\nvoid Pump() {}\n")
    write(root, "project.json", json.dumps({"project": f"{HOME}\\Farm\\Farm.uproject"}))
    write(run, "prompt.txt", "Verify that the pump works")
    write(run, "report.json", json.dumps({"outcome": "CONFIRMED",
                                          "evidence": [f"{HOME}\\autopilot\\runs\\x\\trace1.jsonl"]}))
    write(run, "harness.py", "def main(): pass\n")
    write(run, "harness1.py", "def main(): pass\n")
    write(run, "implementer.log", f"read {HOME}\\Farm\\Config\\DefaultEngine.ini\n")
    write(run, "trace1.jsonl", '{"kind": "start", "wall": 1.0}\n{"kind": "obs", "t": 0.5, "wall": 1.5}\nnot json\n')
    write(run, "trace1_summary.txt", "episode 1 went fine")
    write(run, "drive_log.txt", "MY_API_KEY=hunter2hunter2\n")
    write(run, ".agent/projects/session.jsonl", "the agent's own transcript")
    write(run, "telemetry/llm.jsonl.gz", json.dumps({"call": 1, "request": {"system": f"cwd {HOME}\\Farm", "messages": [
        {"role": "user", "content": f"key {KEY}"},
        {"role": "user", "content": '"com.farm.tools": "file:C:/Users/jdoe/Farm/Packages/com.farm.tools",\n'
                                    '"com.studio.shared": "file:E:/Studio/Secret Game/Packages/com.studio.shared"'}]},
        "response": {"usage": {"input_tokens": 5}}}) + "\n")
    write(run, "telemetry/decisions1.jsonl.gz", json.dumps({"call": 1, "wall": 1.2}) + "\n")
    write(run, "telemetry/describe.jsonl", json.dumps({"questions": [f"what is at {HOME}?"], "frames": [{"t": 0.5}],
                                                      "description": "a pump"}) + "\n")
    write(run, "telemetry/memory_after/notes.md", "the pump needs 3 presses")
    write(run, "telemetry/media/frames-1.jsonl", json.dumps({"frame": 0, "wall": 1.1, "pts_ms": 0}) + "\n")
    with open(os.path.join(run, "telemetry", "media", "recording-1.mp4"), "wb") as f:
        f.write(b"\x00\x00\x00\x18ftypmp42 video")
    write(run, "telemetry/media/flipbook-1/000000.jpg", "the report's flipbook: this machine's only")
    write(run, "telemetry/run.json", json.dumps({"schema": 1, "run": "20261014-101500", "run_uid": "abc123",
                                                 "prompt": "Verify that the pump works", "through_aura": False,
                                                 "codex": dataset.codex_id(root), "episodes": [
                                                     {"episode": "trace1", "wall_start": 1.0, "wall_end": 1.5}]}))
    write(root, "runs/20261014-090000/prompt.txt", "a run whose data wasn't shared")
    return root


def test_a_run_exports_to_one_clean_folder_in_the_same_layout_every_time(tmp_path, monkeypatch):
    monkeypatch.setattr(redact, "_user", lambda: "jdoe")
    monkeypatch.setattr(redact.platform, "node", lambda: "JDOE-PC")
    monkeypatch.setenv("MY_API_KEY", "hunter2hunter2")
    monkeypatch.setattr(redact.os.path, "expanduser", lambda p: HOME if p == "~" else p)
    root = a_project(tmp_path)
    out = str(tmp_path / "out")
    (m,) = dataset.export_project(root, out)
    run = os.path.join(out, "runs", "20261014-101500-abc123")
    assert sorted(f["path"] for f in m["files"]) == [
        "agent/describe.jsonl.gz", "agent/llm.jsonl.gz", "agent/log.txt", "episodes/1/decisions.jsonl.gz",
        "episodes/1/frames.jsonl.gz", "episodes/1/harness.py", "episodes/1/recording.mp4", "episodes/1/summary.txt",
        "episodes/1/trace.jsonl.gz", "extra/drive_log.txt", "harness.py", "memory/after/notes.md", "prompt.txt",
        "report.json"]  # (never the report's flipbook: this machine's only)
    assert m["schema"] == 2 and m["channel"] == "own_keys" and m["episodes"][0]["n"] == 1
    assert m["codex_id"] == dataset.codex_id(root) and "codex" not in m
    assert os.listdir(out) == ["runs"]  # (the codex itself is never sent)
    assert m["local_names"]["runs/20261014-101500/trace1.jsonl"] == "episodes/1/trace.jsonl.gz"
    assert m["redaction"]["counts"]["unreadable_lines"] == 1 and m["redaction"]["counts"]["secret"] >= 2
    # nothing that must not be sent, anywhere (gzipped files read too)
    everything = ""
    for folder, _, names in os.walk(out):
        for name in names:
            path = os.path.join(folder, name)
            data = gzip.open(path).read() if name.endswith(".gz") else open(path, "rb").read()
            everything += data.decode("utf-8", "replace") + "\n"
    for s in SECRETS + ("Users\\jdoe", "Users\\\\jdoe", "the agent's own transcript", "wasn't shared", "void Pump",
                        "Studio", "Secret Game"):
        assert s not in everything, s
    # (the project's own files by where they are in it; another folder's by their name)
    with gzip.open(os.path.join(run, "agent", "llm.jsonl.gz"), "rt", encoding="utf-8") as f:
        llm = f.read()
    assert "file:<project>/Packages/com.farm.tools" in llm and "file:[path]/com.studio.shared" in llm
    assert m["redaction"]["version"] == 2 and m["redaction"]["counts"]["path"] >= 3
    # every JSON lines file reads, and the same run exports the same bytes
    with gzip.open(os.path.join(run, "episodes", "1", "trace.jsonl.gz"), "rt", encoding="utf-8") as f:
        assert [json.loads(line)["kind"] for line in f] == ["start", "obs"]
    before = {f["path"]: f["md5"] for f in m["files"]}
    (again,) = dataset.export_project(root, str(tmp_path / "again"))
    assert {f["path"]: f["md5"] for f in again["files"]} == before


def test_a_run_from_before_uids_gets_a_steady_one(tmp_path):
    assert dataset.run_uid({"started": 12.5}, "r1") == dataset.run_uid({"started": 12.5}, "r1")
    assert dataset.run_uid({"started": 12.5}, "r1") != dataset.run_uid({"started": 13.5}, "r1")
    assert dataset.run_uid({"run_uid": "abc"}, "r1") == "abc"


def test_only_shared_runs_are_listed(tmp_path):
    root = a_project(tmp_path)
    assert [os.path.basename(r) for r, _ in dataset.shared_runs(root)] == ["20261014-101500"]
