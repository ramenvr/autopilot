"""The export streamed into a run while it works (codex.Indexer), what reaches the agent with its tool results
(Session.news), `codex`/`llm`/`llm_map` in probes, and the background researcher's findings (agents/researcher.py)."""

import os
import threading
import time

import pytest
from test_autopilot import FakeJev, FakeLink

from autopilot import codex, llm
from autopilot.agents import researcher as researchermod
from autopilot.agents import session
from autopilot.kit.game import Game

BOSS = """Blueprint /Game/G/BP_Boss  (Blueprint, type Normal)
Parent: /Game/G/BP_EnemyBase.BP_EnemyBase_C
Generated class: /Game/G/BP_Boss.BP_Boss_C
Graph EventGraph:
  Event BeginPlay():
    Set Health = 1000
  Custom Event Enrage():
    Set Damage = 40
Function ActivatePhase2():
Function TakeHit(Amount: float):
"""


class ExportingPlugin:
    """A plugin whose export writes `steps` (lists of {path: text}) one per manifest asked without waiting; with
    wait (an older plugin, or the brain waiting), the whole export first."""

    def __init__(self, before, steps):
        self.files = dict(before)  # what Saved/ etc. hold now
        self.steps = list(steps)
        self.total = sum(len(s) for s in steps)
        self.done = 0
        self.asked = []

    def request(self, op, args, timeout=None):
        if op == "codex_manifest":
            self.asked.append(dict(args))
            if args.get("wait", True):
                while self.steps:
                    self._step()
            elif self.steps:
                self._step()
            running = bool(self.steps)
            export = {"ran": True, "reason": "missing", "running": running, "done": self.done, "total": self.total}
            return {"files": [{"path": p, "size": len(t), "hash": str(hash(t))} for p, t in self.files.items()],
                    "export": export}
        return {"files": {p: self.files[p] for p in args["paths"] if p in self.files}}

    def _step(self):
        step = self.steps.pop(0)
        self.files.update(step)
        self.done += len(step)


def make_indexer(tmp_path, plugin, monkeypatch):
    monkeypatch.setattr(codex, "POLL_SECONDS", 0.01)
    root = str(tmp_path / "proj" / "codex")
    os.makedirs(root)
    return codex.Indexer(plugin, root, first_map="/Game/Maps/Arena.Arena")


def wait_for(indexer):
    indexer.thread.join(5) if indexer.thread else None
    assert indexer.status()["finished"]


def test_the_run_starts_on_what_is_there_and_the_rest_streams_in(tmp_path, monkeypatch):
    plugin = ExportingPlugin({"Source/G/A.cpp": "class AHero {};"},
                             [{"Saved/AutopilotCodex/blueprints/G/BP_Boss.txt": BOSS},
                              {"Saved/AutopilotCodex/data/G/DT_Loot.csv": "---,Item,Chance\nA,Sword,0.5\n"},
                              {"Saved/AutopilotCodex/summary.txt": "AutopilotCodex export\n"}])
    idx = make_indexer(tmp_path, plugin, monkeypatch)
    first = idx.start()
    assert plugin.asked[0] == {"export": "auto", "wait": False, "first_map": "/Game/Maps/Arena.Arena"}
    assert first["export"]["running"] and first["files"] == 2  # (the C++ file and the first Blueprint)
    wait_for(idx)
    root = idx.root
    assert os.path.exists(os.path.join(root, "data", "G", "DT_Loot.csv"))
    assert os.path.exists(os.path.join(root, "summary.txt"))
    # What came after the start is news, in batches, each file with a line on what it is
    lines = [f["line"] for b in idx.batches for f in b["files"]]
    assert "data table, 1 rows; columns: Item, Chance" in lines
    assert all(b["files"][0]["kind"] == "new" for b in idx.batches)
    assert os.path.exists(os.path.join(os.path.dirname(root), codex.INDEXING, "batch-1.txt"))


def test_files_the_running_export_hasnt_written_again_stay_until_it_ends(tmp_path, monkeypatch):
    plugin = ExportingPlugin({"Saved/AutopilotCodex/blueprints/G/BP_Old.txt": "old"}, [])
    root = str(tmp_path / "codex")
    os.makedirs(root)
    codex.fetch_from_plugin(plugin, root)  # (the last run's export)
    plugin.files = {}  # (the new export hasn't written it yet)
    plugin.steps, plugin.total, plugin.done = [{"Saved/AutopilotCodex/maps.txt": "Map /Game/M\n"}, {}], 2, 0
    res = codex.fetch_from_plugin(plugin, root, wait=False)
    assert res["export"]["running"] and res["removed"] == 0
    assert os.path.exists(os.path.join(root, "blueprints", "G", "BP_Old.txt"))
    res = codex.fetch_from_plugin(plugin, root, wait=False)  # (done: what it didn't write is gone)
    assert not res["export"]["running"] and res["removed"] == 1


def test_digest_names_everything_a_blueprint_has(tmp_path):
    os.makedirs(tmp_path / "blueprints")
    (tmp_path / "blueprints" / "BP_Boss.txt").write_text(BOSS)
    line = codex.digest(str(tmp_path), "blueprints/BP_Boss.txt")
    assert line == ("Blueprint, type Normal; parent BP_EnemyBase; events: BeginPlay, Enrage; functions: "
                    "ActivatePhase2, TakeHit")


def open_run(tmp_path, monkeypatch, prompt="Kill the boss"):
    monkeypatch.setattr(codex, "MEMORY", str(tmp_path))
    game = Game(FakeLink())
    game.codex = codex.Codex.for_project("fake")
    return session.Session(game, "fake", prompt, "r1", activity=lambda f: None, client=FakeJev("go"))


class StubIndexer:
    def __init__(self, batches, running=True):
        self.batches = batches
        self.running = running
        self.listeners = []

    def status(self):
        return {"running": self.running, "done": 5, "total": 9, "files": 40, "batches": len(self.batches),
                "seconds": 1.0, "finished": not self.running, "error": None}

    def since(self, n):
        return list(self.batches[n:])


def write_codex(s, rel, text):
    full = os.path.join(s.root, "codex", *rel.split("/"))
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(text)


def test_news_lists_every_arrival_once_with_the_requests_words_first(tmp_path, monkeypatch):
    s = open_run(tmp_path, monkeypatch)
    write_codex(s, "blueprints/BP_Boss.txt", BOSS + "boss boss")
    write_codex(s, "blueprints/BP_Door.txt", "Blueprint door")
    idx = StubIndexer([{"n": 1, "at": time.time(), "files": [
        {"path": "blueprints/BP_Door.txt", "kind": "new", "line": "Blueprint"},
        {"path": "blueprints/BP_Boss.txt", "kind": "changed", "line": "Blueprint; parent BP_EnemyBase"}]}])
    s.game.codex.indexer = idx
    news = s.news()
    assert news.index("codex/blueprints/BP_Boss.txt") < news.index("codex/blueprints/BP_Door.txt")
    assert "[request words: boss: 5]" in news and "~ codex/blueprints/BP_Boss.txt" in news
    assert "2: 1 new, 1 changed" in news
    assert "codex/blueprints/BP_Door.txt" not in s.news()  # (told once; the indexing line stays while it runs)
    assert "[indexing the game's files: the export 5 of 9 written" in s.news()


def test_news_too_long_for_a_tool_result_goes_whole_to_a_file(tmp_path, monkeypatch):
    s = open_run(tmp_path, monkeypatch, prompt="Open the vault")
    monkeypatch.setattr(session, "NEWS_INLINE", 400)
    files = [{"path": f"data/DT_{i}.csv", "kind": "new", "line": f"data table {i}"} for i in range(50)]
    s.game.codex.indexer = StubIndexer([{"n": 1, "at": time.time(), "files": files}])
    news = s.news()
    path = os.path.join(s.root, "runs", "r1", "news", "arrivals-1.txt")
    assert "All 50 are in runs/r1/news/arrivals-1.txt" in news
    kept = open(path, encoding="utf-8").read()
    assert all(f"codex/data/DT_{i}.csv" in kept for i in range(50))


def test_the_end_of_the_export_is_told_once_with_what_arrived_after_the_agent_wrote(tmp_path, monkeypatch):
    s = open_run(tmp_path, monkeypatch)
    s.index_was_running = True
    with open(os.path.join(s.run_dir, "harness.py"), "w") as f:
        f.write("# uses BP_Boss and BP_Gate\n")
    later = time.time() + 5
    idx = StubIndexer([{"n": 1, "at": later, "files": [{"path": "blueprints/BP_Boss.txt", "kind": "changed",
                                                         "line": "Blueprint"},
                                                        {"path": "blueprints/BP_Other.txt", "kind": "new",
                                                         "line": "Blueprint"}]}], running=False)
    s.game.codex.indexer = idx
    idx.batches_told = s.news_batches = 1  # (the arrivals themselves were told)
    news = s.news()
    assert "The export is in: 40 files" in news
    assert "codex/blueprints/BP_Boss.txt" in news and "BP_Other" not in news
    assert "no need to read again" in news
    assert "The export is in" not in s.news()


def test_findings_are_told_once_and_say_which_harness_played_without_them(tmp_path, monkeypatch):
    s = open_run(tmp_path, monkeypatch)
    f1 = s.add_finding("BP_Boss enrages below 50% health: Damage 40", ["codex/blueprints/BP_Boss.txt"],
                       "from episode 1")
    s.harness_started[2] = f1["at"] + 1  # (harness 2 started before the agent heard of it)
    news = s.news()
    assert "Finding 1 (from episode 1) (came in before harness 2 started, too late for it" in news
    assert "files: codex/blueprints/BP_Boss.txt" in news
    assert "Finding 1" not in s.news()
    assert "BP_Boss enrages" in open(os.path.join(s.run_dir, "findings.md")).read()


def test_codex_in_probes_reads_the_indexing_from_disk(tmp_path):
    project = tmp_path / "proj"
    folder = project / codex.INDEXING
    os.makedirs(folder)
    (folder / "status.json").write_text('{"running": true, "done": 3, "total": 9, "files": 12, "batches": 1}')
    (folder / "batch-1.txt").write_text(codex.batch_text({"n": 1, "files": [
        {"path": "blueprints/My Folder/BP_A.txt", "kind": "new", "line": "Blueprint; events: Hit"}]}))
    view = codex.View(str(project))
    assert view.arrivals() == [{"n": 1, "files": [{"path": "blueprints/My Folder/BP_A.txt", "kind": "new",
                                                   "line": "Blueprint; events: Hit"}]}]
    assert "the export: 3 of 9 written" in repr(view)
    assert codex.View(str(tmp_path / "other")).status()["finished"]


def test_parts_keep_every_character():
    text = "".join(f"line {i}\n" for i in range(5000))
    chunks = llm.parts(text, size=1000)
    assert "".join(chunks) == text and all(len(c) <= 1000 for c in chunks)


class FakeModel:
    """llm.Model's surface: ask() answers; turn() plays a script of tool calls."""

    name = "fake"
    route = ("anthropic", "", "", "fake", None)

    def __init__(self, script=()):
        self.script = list(script)
        self.prompts = []

    def ask(self, prompt, system=None, max_tokens=None):
        self.prompts.append(prompt)
        return f"answer {len(self.prompts)}"

    def turn(self, messages, system=None, tools=None, max_tokens=None):
        self.prompts.append(messages[0]["content"])
        calls = self.script.pop(0) if self.script else []
        return {"text": "", "calls": calls, "message": {"role": "assistant", "content": ""}}


def test_llm_reads_only_the_games_folder_and_llm_map_answers_in_the_background(tmp_path):
    (tmp_path / "game").mkdir()
    (tmp_path / "game" / "a.txt").write_text("A")
    (tmp_path / "secret.env").write_text("KEY=1")
    reader = llm.Reader(FakeModel(), str(tmp_path / "game"), str(tmp_path / "game" / "results"))
    assert reader.ask("what?", "a.txt") == "answer 1"
    with pytest.raises(ValueError):
        reader.ask("what?", str(tmp_path / "secret.env"))
    job = reader.map("what?", ["a.txt", "a.txt"])
    assert job.result(timeout=5) and job.done()
    assert "=== a.txt ===" in open(job.file, encoding="utf-8").read()
    reader.close()


def test_the_researcher_checks_a_playing_harness_and_its_findings_reach_the_agent(tmp_path, monkeypatch):
    s = open_run(tmp_path, monkeypatch)
    write_codex(s, "blueprints/BP_Boss.txt", BOSS)
    with open(os.path.join(s.run_dir, "harness.py"), "w") as f:
        f.write("# melee BP_Boss\n")
    model = FakeModel([
        [{"id": "1", "name": "grep", "args": {"pattern": "Damage", "path": "codex"}}],
        [{"id": "2", "name": "read_file", "args": {"path": "../outside.txt"}},
         {"id": "3", "name": "add_finding", "args": {"text": "Enrage sets Damage 40", "files": ["codex/x"]}}],
        [],
    ])
    r = researchermod.Researcher(s, model)
    r.playing(1)
    deadline = time.time() + 5
    while not s.findings and time.time() < deadline:
        time.sleep(0.02)
    r.stop()
    assert s.findings[0]["source"] == "while harness 1 played"
    assert "# melee BP_Boss" in model.prompts[0] and "Harness 1" in model.prompts[0]
    assert "Finding 1 (while harness 1 played)" in s.news()


def test_the_researcher_only_wakes_for_files_that_bear_on_the_request(tmp_path, monkeypatch):
    s = open_run(tmp_path, monkeypatch)
    r = researchermod.Researcher(s, FakeModel())
    seen = threading.Event()
    r._pass = lambda events: seen.set()
    r.arrived({"n": 1, "files": [{"path": "data/DT_Grass.csv", "kind": "new", "line": "data table"}]})
    assert not seen.wait(0.2)
    r.arrived({"n": 2, "files": [{"path": "blueprints/BP_Boss.txt", "kind": "new", "line": "Blueprint"}]})
    assert seen.wait(2)
    r.stop()


def test_the_researcher_is_the_users_choice_else_off_in_the_aura_apps_autopilot(monkeypatch):
    from autopilot import aura

    monkeypatch.delenv("AUTOPILOT_RESEARCHER", raising=False)
    monkeypatch.setattr(aura, "_app", False)
    # Nothing chosen (an older plugin, or the switch never flipped), the user's own Autopilot: on, also when its runs go
    # through Aura (signed in to Aura in the plugin)
    monkeypatch.setenv("AUTOPILOT_AURA_URL", "https://aura.example")
    assert researchermod.enabled(None) and researchermod.enabled({"share": True})
    # The Aura app's Autopilot (what plugins set to Use with Aura reach): off
    aura.mark_app(True)
    assert not researchermod.enabled({"share": True})
    # The Settings page's switch, said in the hello, wins either way
    assert researchermod.enabled({"research": True})
    aura.mark_app(False)
    assert not researchermod.enabled({"research": False})
    monkeypatch.setenv("AUTOPILOT_RESEARCHER", "0")
    assert not researchermod.enabled({"research": True})


def test_the_level_snapshot_is_taken_aside_and_arrives_as_a_batch(tmp_path, monkeypatch):
    idx = make_indexer(tmp_path, ExportingPlugin({}, []), monkeypatch)
    idx.start()
    done = threading.Event()
    seen = []
    idx.listeners.append(lambda batch: (seen.append(batch), done.set()))

    def snapshot():
        assert idx.status()["working"] == ["level snapshot"]
        with open(os.path.join(idx.root, "level.txt"), "w") as f:
            f.write("# Actors in the running level (2)\n")

    idx.aside(["level.txt", "controls.txt"], snapshot, name="level snapshot")
    assert done.wait(5)
    assert [(f["path"], f["kind"]) for f in seen[0]["files"]] == [("level.txt", "new")]  # (no controls were written)
    assert idx.status()["working"] == []


def test_background_work_is_tried_again_when_the_game_is_too_busy(tmp_path, monkeypatch):
    monkeypatch.setattr(codex, "ASIDE_RETRY_SECONDS", 0.01)
    idx = make_indexer(tmp_path, ExportingPlugin({}, []), monkeypatch)
    idx.start()
    done = threading.Event()
    idx.listeners.append(lambda batch: done.set())
    tries = []

    def snapshot():
        tries.append(1)
        if len(tries) < 2:
            raise TimeoutError("the game didn't answer rc within 30s")
        with open(os.path.join(idx.root, "level.txt"), "w") as f:
            f.write("# Actors\n")

    idx.aside(["level.txt"], snapshot, name="level snapshot")
    assert done.wait(5) and len(tries) == 2


def test_only_the_asks_after_the_first_follow_the_export(tmp_path, monkeypatch):
    # (a plugin starts an export for a first ask, never for a followed one: an export the user stopped stays stopped
    # while the run finishes up, and the next run's first ask starts it again)
    plugin = ExportingPlugin({}, [{"Saved/AutopilotCodex/a.txt": "a"}, {"Saved/AutopilotCodex/b.txt": "b"}])
    idx = make_indexer(tmp_path, plugin, monkeypatch)
    idx.start()
    wait_for(idx)
    assert [a.get("follow", False) for a in plugin.asked] == [False] + [True] * (len(plugin.asked) - 1)
    assert len(plugin.asked) > 1

    next_run = ExportingPlugin({}, [{"Saved/AutopilotCodex/a.txt": "a"}])
    second = codex.Indexer(next_run, idx.root)
    second.start()
    assert "follow" not in next_run.asked[0]


def test_an_export_that_ended_well_counts_as_all_written(tmp_path, monkeypatch):
    plugin = ExportingPlugin({}, [{"Saved/AutopilotCodex/a.txt": "a"}, {"Saved/AutopilotCodex/b.txt": "b"}])
    idx = make_indexer(tmp_path, plugin, monkeypatch)
    idx.start()
    wait_for(idx)
    assert idx.status()["done"] == idx.status()["total"] == 2
