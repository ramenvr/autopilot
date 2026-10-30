import json
import os
import pathlib
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time

import pytest

from autopilot import aura_login, dataset, training, uninstall

LISTING = uninstall.listing  # (the real one, before the fixture below replaces it)
ERASE = uninstall.erase


@pytest.fixture(autouse=True)
def nothing_running(monkeypatch):
    """Tests never list or stop this machine's processes, nor touch its temporary folder, nor ask the Aura app on this
    machine for its sign-in, nor erase anything kept with Aura (the tests of that put erase back, with a fake Aura)."""
    monkeypatch.setattr(uninstall, "listing", lambda: {})
    monkeypatch.setattr(uninstall, "end", lambda pid: pytest.fail(f"stopped {pid}"))
    monkeypatch.setattr(uninstall, "temporary_leftovers", lambda temp=None: [])
    monkeypatch.setattr(uninstall.aura, "client_session", lambda *a, **k: (None, None))
    monkeypatch.setattr(uninstall, "erase", lambda *a, **k: pytest.fail("erased the memory kept with Aura"))
    monkeypatch.delenv("AUTOPILOT_SHARE", raising=False)
    monkeypatch.delenv("AUTOPILOT_MEMORY_URL", raising=False)


def settings(tmp_path):
    """A settings folder as an installed Autopilot leaves it."""
    folder = tmp_path / "Autopilot"
    (folder / "logs").mkdir(parents=True)
    (folder / "logs" / "agent-service-20261019-120000.log").write_text("log")
    for name, text in {"preferences.json": '{"onboarded": true}', "autopilot.env": "TYPESAFE_API_KEY=ts-0001\n",
                       "install.json": "{}", "agent-service.json": "{}", "brain.json": "{}",
                       "aura-brain.json": "{}"}.items():
        (folder / name).write_text(text)
    return folder


def shared_run(folder, project, run_id, through_aura=False):
    run = folder / "memory" / project / "runs" / run_id
    (run / dataset.TELEMETRY).mkdir(parents=True)
    (run / dataset.TELEMETRY / "run.json").write_text(json.dumps({"run_id": run_id, "through_aura": through_aura}))
    (folder / "memory" / project / "notes.md").write_text("what it learned")
    return run


def sent_all(folder, project):
    """What a fake shared data upload does: marks every shared run of the project not through Aura as sent."""
    state = training._read(folder)
    for run_dir, m in dataset.shared_runs(folder):
        if not m.get("through_aura"):
            state["runs"][os.path.basename(run_dir)] = dataset.run_prefix(run_dir, m)
    training._write(folder, state)
    return 1


def test_uninstall_removes_the_settings_folder_but_the_aura_apps_file(tmp_path):
    folder = settings(tmp_path)
    (folder / "logs" / "read-only.log").write_text("x")
    os.chmod(folder / "logs" / "read-only.log", stat.S_IREAD)
    said = []
    assert uninstall.uninstall(str(folder), exe=None, say=said.append) == 0
    assert sorted(os.listdir(folder)) == ["aura-brain.json"]
    assert said[-1].startswith("Autopilot is removed from this computer.")


def test_shared_runs_not_sent_yet_are_sent_before_the_memory_goes(tmp_path):
    folder = settings(tmp_path)
    shared_run(folder, "Orchard", "20261019-120000")
    (folder / "memory" / "Orchard" / "project.json").write_text('{"project_id": "abc-123"}')
    uploads = []

    def upload(project_folder, key):
        uploads.append((os.path.basename(project_folder), key))
        return sent_all(project_folder, key)

    assert uninstall.uninstall(str(folder), exe=None, say=lambda _: None, upload=upload) == 0
    assert uploads == [("Orchard", "abc-123")]  # (stored under the project's key, as a run sends it)
    assert not (folder / "memory").exists()


def test_shared_runs_that_cant_be_sent_go_with_the_memory(tmp_path):
    """A run through Aura is sent only with a run through Aura: never with the install token, and removed with the rest
    of the memory (its codex, runs, notes), saying so."""
    folder = settings(tmp_path)
    run = shared_run(folder, "Orchard", "20261019-120000", through_aura=True)
    (run / "harness.py").write_text("harness")
    (folder / "memory" / "Orchard" / "codex").mkdir()
    (folder / "memory" / "Orchard" / "codex" / "README.md").write_text("codex")
    said = []
    code = uninstall.uninstall(str(folder), exe=None, say=said.append,
                               upload=lambda *a: pytest.fail("a run through Aura sent with the install token"))
    assert code == 0 and sorted(os.listdir(folder)) == ["aura-brain.json"]
    assert any("Removing 1 shared run of Orchard not sent yet" in line for line in said)
    assert said[-1].startswith("Autopilot is removed from this computer.")


def test_with_sharing_off_unsent_runs_arent_sent(tmp_path, monkeypatch):
    folder = settings(tmp_path)
    shared_run(folder, "Orchard", "20261019-120000")
    monkeypatch.setenv("AUTOPILOT_SHARE", "0")
    said = []
    code = uninstall.uninstall(str(folder), exe=None, say=said.append,
                               upload=lambda *a: pytest.fail("sent with sharing off"))
    assert code == 0 and not (folder / "memory").exists()
    assert any("not sent yet (sharing is off)" in line for line in said)


def test_keep_memory_keeps_it_and_sends_nothing(tmp_path):
    folder = settings(tmp_path)
    shared_run(folder, "Orchard", "20261019-120000")
    code = uninstall.uninstall(str(folder), keep_memory=True, exe=None, say=lambda _: None,
                               upload=lambda *a: pytest.fail("sent"))
    assert code == 0 and sorted(os.listdir(folder)) == ["aura-brain.json", "memory"]


class FakeAura:
    """Aura's memory endpoint: answers each erase from `answers` (status, body), in turn (the last one again after),
    and keeps what was asked: (url, Authorization, body)."""

    def __init__(self, *answers):
        self.answers = list(answers) or [(200, {"deleted": 3})]
        self.asked = []

    def post(self, url, json=None, headers=None, timeout=None):
        import requests

        self.asked.append((url, (headers or {}).get("Authorization"), json))
        status, body = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        r = requests.Response()
        r.status_code = status
        r._content = __import__("json").dumps(body).encode()
        return r


def kept_with_aura(tmp_path, monkeypatch, folder):
    """Two games with memory here (one with a project id), a session kept for Aura, and this install's token."""
    for name, info in (("Orchard", {"project_id": "abc-123"}), ("Farm", None)):
        (folder / "memory" / name / "runs" / "r1").mkdir(parents=True)
        (folder / "memory" / name / "notes.md").write_text("what it learned")
        for synced in (".aura-sync.json", ".install-sync.json"):  # (kept with the team and for this install)
            (folder / "memory" / name / synced).write_text("{}")
        if info:
            (folder / "memory" / name / "project.json").write_text(json.dumps(info))
    (folder / "install.json").write_text(json.dumps({"token": "gti1.install", "url": "https://aura.test/api/autopilot/training"}))
    aura_login.save("https://aura.test",
                    {"access_token": "session", "refresh_token": "r", "expires_at": time.time() + 3600})
    monkeypatch.setattr(uninstall, "erase", ERASE)


def test_deleting_the_memory_deletes_each_games_copy_kept_with_the_team_and_this_install(tmp_path, monkeypatch):
    """Delete Autopilot's memories: each game's memory kept with Aura goes too (the team's with the Aura sign-in, this
    install's with its token), before the sign-out; shared run data is never asked about."""
    folder = settings(tmp_path)
    kept_with_aura(tmp_path, monkeypatch, folder)
    aura = FakeAura()
    order = []
    monkeypatch.setattr(aura_login, "sign_out", lambda server: order.append("sign-out") or True)
    real = uninstall.erase
    monkeypatch.setattr(uninstall, "erase", lambda *a, **k: order.append("erase") or real(*a, **k))
    said = []
    assert uninstall.uninstall(str(folder), exe=None, say=said.append, http=aura) == 0
    team, own = "https://aura.test/api/autopilot/memory", "https://aura.test/api/autopilot/memory"
    assert aura.asked == [
        (team, "Bearer session", {"op": "erase", "project": "Farm"}),
        (own, "Bearer gti1.install", {"op": "erase", "project": "Farm"}),
        (team, "Bearer session", {"op": "erase", "project": "abc-123"}),
        (own, "Bearer gti1.install", {"op": "erase", "project": "abc-123"}),
    ]
    assert order == ["erase"] * 4 + ["sign-out"]
    assert "Deleted Orchard's memory kept with your Aura team (3 files)" in said
    assert "Deleted Orchard's memory kept with Aura for this computer (3 files)" in said
    assert said[-2] == "Shared run data already sent stays where it is."
    assert not (folder / "memory").exists()


def test_keeping_the_memory_keeps_the_copies_kept_with_aura(tmp_path, monkeypatch):
    folder = settings(tmp_path)
    kept_with_aura(tmp_path, monkeypatch, folder)
    monkeypatch.setattr(aura_login, "sign_out", lambda server: True)
    said = []
    assert uninstall.uninstall(str(folder), keep_memory=True, exe=None, say=said.append,
                               http=FakeAura((500, {"error": "asked"}))) == 0
    assert not any("Deleted" in line for line in said)
    assert "Shared run data already sent and the memory kept with Aura stay where they are." in said


def test_an_erase_aura_is_unwell_for_is_tried_again_and_one_it_refuses_is_named(tmp_path, monkeypatch):
    folder = settings(tmp_path)
    kept_with_aura(tmp_path, monkeypatch, folder)
    monkeypatch.setattr(aura_login, "sign_out", lambda server: True)
    monkeypatch.setattr(uninstall.retry, "pause", lambda seconds: None)
    aura = FakeAura((503, {"error": "not fully deleted"}), (200, {"deleted": 2}), (200, {"deleted": 0}),
                    (403, {"error": "Your account is not part of an Aura team."}), (200, {"deleted": 1}))
    said = []
    assert uninstall.uninstall(str(folder), exe=None, say=said.append, http=aura) == uninstall.KEPT
    assert len(aura.asked) == 5 and aura.asked[0] == aura.asked[1]  # (the 503 tried again)
    assert "Deleted Farm's memory kept with your Aura team (2 files)" in said
    assert any(line.startswith("Couldn't delete Orchard's memory kept with your Aura team: HTTP 403: Your account is "
                               "not part of an Aura team.") for line in said)
    assert "Orchard's memory kept with your Aura team" in said[-1]  # (named in the summary)
    assert not (folder / "memory").exists()  # (this computer's copy goes all the same)


def test_a_team_copy_nobody_here_is_signed_in_for_is_named_and_stays(tmp_path, monkeypatch):
    folder = settings(tmp_path)
    (folder / "memory" / "Orchard").mkdir(parents=True)
    (folder / "memory" / "Orchard" / "notes.md").write_text("what it learned")
    (folder / "memory" / "Orchard" / ".aura-sync.json").write_text("{}")  # (kept with Aura by a run through Aura)
    monkeypatch.setattr(uninstall, "erase", ERASE)
    said = []
    assert uninstall.uninstall(str(folder), exe=None, say=said.append, http=FakeAura()) == uninstall.KEPT
    assert "Orchard's memory kept with your Aura team stays: this computer isn't signed in to Aura" in said


def test_the_aura_apps_sign_in_reaches_its_servers_copy(tmp_path, monkeypatch):
    folder = settings(tmp_path)
    (folder / "memory" / "Orchard").mkdir(parents=True)
    (folder / "memory" / "Orchard" / ".aura-sync.json").write_text("{}")
    monkeypatch.setattr(uninstall, "erase", ERASE)
    monkeypatch.setattr(uninstall.aura, "client_session", lambda *a, **k: ("http://127.0.0.1:41200", "app-session"))
    monkeypatch.setenv("AUTOPILOT_AURA_SERVER", "https://staging.aura.test")
    monkeypatch.delenv("AUTOPILOT_AURA_URL", raising=False)  # (runs through Aura not chosen here)
    aura = FakeAura()
    assert uninstall.uninstall(str(folder), exe=None, say=lambda _: None, http=aura) == 0
    assert aura.asked == [("https://staging.aura.test/api/autopilot/memory", "Bearer app-session",
                           {"op": "erase", "project": "Orchard"})]


def test_only_what_this_computer_kept_with_aura_is_erased(tmp_path, monkeypatch):
    """A game never synced with Aura from here asks Aura nothing, and its team copy (if teammates keep one) stays;
    one synced only for this install reaches only this install's copy, without asking for a sign-in."""
    folder = settings(tmp_path)
    kept_with_aura(tmp_path, monkeypatch, folder)
    os.remove(folder / "memory" / "Farm" / ".aura-sync.json")
    os.remove(folder / "memory" / "Farm" / ".install-sync.json")
    os.remove(folder / "memory" / "Orchard" / ".aura-sync.json")
    monkeypatch.setattr(uninstall, "aura_sessions", lambda *a, **k: pytest.fail("asked for a sign-in"))
    monkeypatch.setattr(aura_login, "sign_out", lambda server: True)
    aura = FakeAura()
    assert uninstall.uninstall(str(folder), exe=None, say=lambda _: None, http=aura) == 0
    assert aura.asked == [("https://aura.test/api/autopilot/memory", "Bearer gti1.install",
                           {"op": "erase", "project": "abc-123"})]


def plugin_project(tmp_path, folder, name, engine, made, data):
    """A game project memory/ says where it is, with the files its plugin left there (`data`) beside the game's own."""
    project = tmp_path / "Games" / name
    for rel in made + data:
        (project / rel).parent.mkdir(parents=True, exist_ok=True)
        (project / rel).write_text("x")
    remembered = project / made[0] if engine == "unreal" else project
    (folder / "memory" / name).mkdir(parents=True, exist_ok=True)
    (folder / "memory" / name / "project.json").write_text(json.dumps({"project": str(remembered), "engine": engine}))
    return project


def test_uninstall_removes_what_the_plugins_keep_in_each_project_with_the_memory(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    monkeypatch.setattr(os.path, "expanduser", lambda p: str(tmp_path / "home") if p == "~" else p)
    folder = settings(tmp_path)
    unreal = plugin_project(tmp_path, folder, "Orchard", "unreal", ["Orchard.uproject", "Saved/Config/Editor.ini"],
                            ["Saved/Autopilot/Runs/1.json", "Saved/Playtest/20261019-120000/recording.mp4",
                             "Saved/AutopilotCodex/maps.txt", "Saved/Logs/AutopilotCodexExport.log"])
    unity = plugin_project(tmp_path, folder, "Harbor", "unity",
                           ["ProjectSettings/ProjectVersion.txt", "Logs/Editor.log",
                            "UserSettings/AutopilotPlaytest.json"],
                           ["Logs/Playtest/20261019-120000/result.json", "Library/AutopilotCodex/scripts.txt",
                            "Logs/AutopilotCodexExport.log", "UserSettings/AutopilotUsage.json",
                            "UserSettings/AutopilotRuns/20261019-120000.json"])
    godot = plugin_project(tmp_path, folder, "Meadow", "godot", ["project.godot", ".godot/editor/project_metadata.cfg"],
                           [".godot/autopilot_codex/summary.txt", ".godot/editor/autopilot_runs/20261019-120000.json",
                            ".godot/editor/autopilot_usage.json"])
    (godot / "project.godot").write_text('[application]\nconfig/name="Meadow Game"\n')
    user = uninstall.engines.get("godot").user_folder(str(godot))
    os.makedirs(os.path.join(user, "playtest", "20261019-120000"))
    os.makedirs(os.path.join(user, "keep-me"))
    said = []
    assert uninstall.uninstall(str(folder), exe=None, say=said.append) == 0
    left = sorted(p.relative_to(tmp_path / "Games").as_posix() for p in (tmp_path / "Games").rglob("*") if p.is_file())
    assert left == ["Harbor/Logs/Editor.log", "Harbor/ProjectSettings/ProjectVersion.txt",
                    "Harbor/UserSettings/AutopilotPlaytest.json", "Meadow/.godot/editor/project_metadata.cfg",
                    "Meadow/project.godot", "Orchard/Orchard.uproject", "Orchard/Saved/Config/Editor.ini"]
    assert sorted(os.listdir(user)) == ["keep-me"]
    assert any(line == f"Removed {unreal / 'Saved' / 'Playtest'}" for line in said)
    assert not (folder / "memory").exists() and unity.exists()


def test_claude_codes_logs_of_the_memorys_projects_go_with_it(tmp_path, monkeypatch):
    folder = settings(tmp_path)
    shared_run(folder, "Orchard Walk", "20261019-120000")
    cache = pathlib.Path(tempfile.mkdtemp(prefix="cc"))  # (short: the folder names are a whole path, Windows' limit)
    monkeypatch.setattr(uninstall, "claude_cache", lambda: str(cache))
    named = re.sub(r"[^A-Za-z0-9]", "-", str(folder / "memory" / "Orchard Walk"))
    (cache / named / "mcp-logs-game").mkdir(parents=True)
    (cache / named / "mcp-logs-game" / "1.jsonl").write_text("{}")
    (cache / "C--Users-me-my-own-project").mkdir()  # (the user's own Claude Code use: never touched)
    said = []
    assert uninstall.uninstall(str(folder), exe=None, say=said.append, upload=sent_all) == 0
    assert sorted(os.listdir(cache)) == ["C--Users-me-my-own-project"]
    folder = settings(tmp_path)
    shared_run(folder, "Orchard Walk", "20261019-120000")
    (cache / named).mkdir()
    said = []
    assert uninstall.uninstall(str(folder), keep_memory=True, exe=None, say=said.append) == 0
    assert (cache / named).exists(), "kept with the memory"
    assert f"Removed what is in {folder} but memory/ (kept)" in said
    shutil.rmtree(cache)


def test_keep_memory_keeps_what_the_plugins_keep_in_the_projects_too(tmp_path):
    folder = settings(tmp_path)
    project = plugin_project(tmp_path, folder, "Orchard", "unreal", ["Orchard.uproject"],
                             ["Saved/Autopilot/Runs/1.json"])
    assert uninstall.uninstall(str(folder), keep_memory=True, exe=None, say=lambda _: None) == 0
    assert (project / "Saved" / "Autopilot" / "Runs" / "1.json").exists()


def test_uninstall_signs_out_of_aura(tmp_path, monkeypatch):
    folder = settings(tmp_path)
    aura_login.save("https://aura.test", {"access_token": "a", "refresh_token": "r"})
    ended = []
    monkeypatch.setattr(aura_login, "sign_out", lambda server: ended.append(server) or True)
    assert uninstall.uninstall(str(folder), exe=None, say=lambda _: None) == 0
    assert ended == ["https://aura.test"]


def test_what_is_stopped_is_autopilots_never_this_process_or_what_started_it(tmp_path):
    folder = settings(tmp_path)
    (folder / "brain.json").write_text(json.dumps({"pid": 40, "url": "ws://127.0.0.1:1/v1/bridge"}))
    tool = os.path.join("C:", "uv", "tools", "autopilot")
    me, parent = os.getpid(), 7
    found = {
        me: (parent, f"{tool}/python autopilot uninstall"),  # (this process, run from the command)
        parent: (1, os.path.join("C:", "bin", "autopilot.exe") + " uninstall"),  # (its launcher)
        40: (1, "python -m autopilot serve --port 0"),  # (the agent service brain.json names)
        41: (1, f"{tool}/python autopilot serve --port 0 --exit-with 9"),  # (another editor's)
        42: (1, "C:/Aura/python -m autopilot serve"),  # (the Aura app's: in aura-brain.json, not brain.json)
        43: (41, f"{tool}/python autopilot serve --port 0 --exit-with 9"),  # (the Python 41 runs: ends with it)
    }
    places = [tool, os.path.join("C:", "bin", "autopilot.exe")]
    assert uninstall.to_stop(found, str(folder), places) == [40, 41]
    found[40] = (1, "notepad.exe")  # (brain.json's pid now another program's)
    assert uninstall.to_stop(found, str(folder), places) == [41]


def test_the_listing_has_this_process_and_what_started_it():
    found = LISTING()
    assert found[os.getpid()][0] == os.getppid() and "python" in found[os.getpid()][1].lower()
    assert uninstall.lineage(found)[:2] == [os.getpid(), os.getppid()]


def test_installed_reads_uvs_tools(monkeypatch):
    answers = {("tool", "list"): "autopilot v0.1.0\n- autopilot\nruff v0.9\n- ruff\n",
               ("tool", "dir"): "/home/me/.local/share/uv/tools\n", ("tool", "dir", "--bin"): "/home/me/.local/bin\n"}
    monkeypatch.setattr(uninstall, "_uv", lambda exe, *args: answers.get(args))
    tools = uninstall.installed("uv")
    assert list(tools) == ["autopilot"]
    assert tools["autopilot"][0] == os.path.join("/home/me/.local/share/uv/tools", "autopilot")


def test_main_asks_first_and_removes_nothing_without_an_answer(tmp_path, monkeypatch, capsys):
    folder = settings(tmp_path)
    monkeypatch.setattr(uninstall, "settings_folder", lambda: str(folder))
    monkeypatch.setattr(uninstall, "uv", lambda: None)
    monkeypatch.setattr(sys, "stdin", None)
    cwd = os.getcwd()
    try:
        args = type("Args", (), {"yes": False, "keep_memory": False, "log": None})()
        assert uninstall.main(args) == 1
    finally:
        os.chdir(cwd)
    assert (folder / "autopilot.env").exists() and "--yes" in capsys.readouterr().out


def test_the_commands_removal_after_this_has_ended_compiles():
    compile(uninstall.WAIT, "wait", "exec")


@pytest.mark.skipif(sys.platform != "win32", reason="the removal waits for this process only on Windows")
def test_the_commands_removal_waits_for_the_processes_then_uninstalls(tmp_path):
    marker, log = tmp_path / "uninstalled.txt", tmp_path / "uninstall.log"
    fake_uv = tmp_path / "uv.bat"
    fake_uv.write_text(f'@echo %* > "{marker}"\r\n')
    sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(2)"])
    started = time.time()
    subprocess.run([sys.executable, "-I", "-c", uninstall.WAIT, str(fake_uv), str(sleeper.pid), "autopilot",
                    str(log), "30"], check=True, timeout=60)
    assert time.time() - started >= 1.0 and sleeper.poll() is not None
    assert marker.read_text().split() == ["tool", "uninstall", "autopilot"]
    assert "Removed the autopilot command" in log.read_text()
