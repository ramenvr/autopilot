"""The export streamed into a run while it works (codex.Indexer), what reaches the agent with its tool results
(Session.news), `codex`/`llm`/`llm_map` in probes, and the background researcher's findings (agents/researcher.py)."""

import io
import os
import threading
import time
import types

import pytest
from test_autopilot import FakeJev, FakeLink, crate_session

from autopilot import cli, codex, llm
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
    def __init__(self, batches, running=True, done=5, total=9):
        self.batches = batches
        self.running = running
        self.done, self.total = done, total
        self.listeners = []

    def status(self):
        return {"running": self.running, "done": self.done, "total": self.total, "files": 40,
                "batches": len(self.batches),
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


def test_the_researchers_search_says_what_isnt_indexed_yet(tmp_path, monkeypatch):
    # (while the export runs, "no match" only means "not indexed so far": the researcher must not read it as "not in
    # the game")
    s = open_run(tmp_path, monkeypatch)
    write_codex(s, "blueprints/BP_Boss.txt", BOSS)
    s.game.codex.indexer = StubIndexer([], running=True)
    r = researchermod.Researcher(s, FakeModel())
    r.stop()
    nothing = r._grep("Phase3")
    assert "(no match)" not in nothing.splitlines()
    assert "indexed so far" in nothing and "5 of 9 written" in nothing
    found = r._grep("Enrage").splitlines()
    assert found[0].startswith("codex/blueprints/BP_Boss.txt:")
    assert sum("indexed so far" in line for line in found) == 1
    assert "indexed so far" in r._list("codex/data/*")
    missing = r._tool("read_file", {"path": "codex/blueprints/BP_Gate.txt"}, [("playing", 1, None)], [])
    assert "not in the codex yet" in missing and "5 of 9 written" in missing


def test_the_researchers_search_reads_as_before_once_the_export_is_in(tmp_path, monkeypatch):
    s = open_run(tmp_path, monkeypatch)
    write_codex(s, "blueprints/BP_Boss.txt", BOSS)
    s.game.codex.indexer = StubIndexer([], running=False)
    r = researchermod.Researcher(s, FakeModel())
    r.stop()
    assert r._grep("Phase3") == "(no match)"
    assert r._list("codex/data/*") == "(none)"
    assert "indexed so far" not in r._grep("Enrage")
    missing = r._tool("read_file", {"path": "codex/blueprints/BP_Gate.txt"}, [("playing", 1, None)], [])
    assert missing.startswith("read_file failed: FileNotFoundError")
    s.game.codex.indexer = None  # (no streamed export at all)
    assert r._grep("Phase3") == "(no match)"


def test_the_researchers_pass_is_told_the_export_is_still_running(tmp_path, monkeypatch):
    s = open_run(tmp_path, monkeypatch)
    write_codex(s, "blueprints/BP_Boss.txt", BOSS)
    for running in (True, False):
        s.game.codex.indexer = StubIndexer([], running=running)
        model = FakeModel()
        r = researchermod.Researcher(s, model)
        r.playing(1)
        deadline = time.time() + 5
        while not model.prompts and time.time() < deadline:
            time.sleep(0.02)
        r.stop()
        assert model.prompts
        if running:
            assert "still being indexed" in model.prompts[0] and "5 of 9 written" in model.prompts[0]
        else:
            assert "still being indexed" not in model.prompts[0]


def test_the_briefing_says_not_in_the_codex_yet_while_indexing(tmp_path, monkeypatch):
    # (the request's words in no file: while the export runs they may still come, so the line says "yet")
    s = open_run(tmp_path, monkeypatch, prompt="Kill the boss in the vault")
    write_codex(s, "blueprints/BP_Boss.txt", BOSS)
    s.game.codex.indexer = StubIndexer([], running=True)
    text = session.briefing(s)
    assert "(not in the codex yet: kill, vault;" in text and "5 of 9 written, it may still come)" in text
    for indexer in (StubIndexer([], running=False), None):
        s.game.codex.indexer = indexer
        text = session.briefing(s)
        assert "(not in the codex: kill, vault)" in text
        assert "not in the codex yet" not in text


def test_before_the_export_has_counted_its_files_the_lines_say_it_is_starting(tmp_path, monkeypatch):
    # (no total yet: "the export is starting", not "the export starting, nothing written yet")
    s = open_run(tmp_path, monkeypatch, prompt="Kill the boss in the vault")
    write_codex(s, "blueprints/BP_Boss.txt", BOSS)
    s.game.codex.indexer = StubIndexer([], running=True, done=0, total=0)
    line = next(line for line in session.briefing(s).splitlines() if line.startswith("(not in the codex yet:"))
    assert line == "(not in the codex yet: kill, vault; the export is starting, it may still come)"
    r = researchermod.Researcher(s, FakeModel())
    r.stop()
    missing = r._tool("read_file", {"path": "codex/blueprints/BP_Gate.txt"}, [("playing", 1, None)], [])
    assert missing == "codex/blueprints/BP_Gate.txt is not in the codex yet: the export is starting; it may still come."
    for answer in (r._grep("Phase3"), r._grep("Enrage"), r._list("codex/data/*"), r._list("**/*")):
        assert "the export is starting" in answer and "the export starting" not in answer


def test_before_the_export_has_counted_its_files_the_news_says_it_is_starting(tmp_path, monkeypatch):
    # (no total yet: "the export is starting", not "the export starting, nothing written yet"; with a total, as before)
    s = open_run(tmp_path, monkeypatch, prompt="Kill the boss in the vault")
    s.game.codex.indexer = StubIndexer([], running=True, done=0, total=0)
    news = s.news()
    line = next(line for line in news.splitlines() if line.startswith("[indexing the game's files:"))
    assert line == "[indexing the game's files: the export is starting; 40 files in the codex now]"
    assert "the export starting" not in news


def test_before_the_export_has_counted_its_files_the_probe_grep_says_it_is_starting(tmp_path, monkeypatch):
    # (grep in probes reads the indexing from disk, codex.View: no total yet, "the export is starting"; else as before)
    s = open_run(tmp_path, monkeypatch, prompt="Kill the boss in the vault")
    write_codex(s, "blueprints/BP_Boss.txt", BOSS)
    folder = os.path.join(s.root, codex.INDEXING)
    os.makedirs(folder, exist_ok=True)
    grep = s._namespace(io.StringIO())["grep"]
    for status, note in (
            ('{"running": true, "done": 0, "total": 0, "files": 1, "batches": 0}',
             "(searched what is indexed so far: the export is starting; what is still coming may match too)"),
            ('{"running": true, "done": 3, "total": 9, "files": 1, "batches": 1}',
             "(searched what is indexed so far: the export 3 of 9 written; what is still coming may match too)")):
        with open(os.path.join(folder, "status.json"), "w", encoding="utf-8") as f:
            f.write(status)
        found = grep("Enrage")
        assert found[0].startswith("blueprints/BP_Boss.txt:") and found[-1] == note
        assert "the export starting" not in " ".join(found)


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


PARTIAL_ALERT = ("Harness 1, the last one played, was written while the game's files were still being indexed (5 of 9 "
                 "exported). Files exported after that were not read for it.")


def crate_run(tmp_path, monkeypatch, running):
    """The crate game's session (tests/test_autopilot.py) with a codex whose export is still running (running=True),
    is all in (False), or never streamed (None: no indexer)."""
    game = Game(FakeLink())
    game.look = {"x": 0.1, "y": 0.1}
    s = crate_session(game, tmp_path, monkeypatch)
    s.game.codex = codex.Codex.for_project("fake")
    s.game.codex.indexer = None if running is None else StubIndexer([], running=running)
    return s


def test_the_report_says_the_last_harness_was_built_on_a_partial_index(tmp_path, monkeypatch):
    s = crate_run(tmp_path, monkeypatch, running=True)
    s.run_harness(15)
    s.game.codex.indexer.batches.append({"n": 1, "at": time.time() + 1,  # (a file exported after it started)
                                         "files": [{"path": "data/DT_Late.csv", "kind": "new", "line": "x"}]})
    s.game.codex.indexer.running = False  # (the export ends before the report: the harness still had only part)
    report = session.finish(s, time.time(), None, "")
    assert report["alerts"] == [PARTIAL_ALERT]
    assert report["indexing"]["last_harness"]["running"] is True and report["indexing"]["last_harness"]["n"] == 1
    assert cli.result_frame(report, s.prompt)["alerts"] == [PARTIAL_ALERT]
    # an export that hadn't counted its files when the harness started says so in the bracket
    s.harness_index[1] = dict(s.harness_index[1], done=0, total=0)
    assert "(the export hadn't counted its files yet)" in session.index_alert(s)


def test_the_report_has_no_index_alert_when_the_harness_had_the_whole_codex(tmp_path, monkeypatch):
    s = crate_run(tmp_path, monkeypatch, running=False)
    s.run_harness(15)
    report = session.finish(s, time.time(), None, "")
    assert not any("indexed" in a for a in report.get("alerts", []))
    # no indexer (the codex was whole when the run started): no alert and no `indexing`, as before
    s = crate_run(tmp_path / "whole", monkeypatch, running=None)
    s.run_harness(15)
    report = session.finish(s, time.time(), None, "")
    assert "alerts" not in report and "indexing" not in report
    # no harness played: nothing was built on the partial codex
    s = crate_run(tmp_path / "none", monkeypatch, running=True)
    report = session.finish(s, time.time(), None, "")
    assert not any("indexed" in a for a in report.get("alerts", []))


def test_no_partial_index_alert_when_nothing_was_exported_after_the_harness_started(tmp_path, monkeypatch):
    # (a re-export at 950 of 983 that brings nothing new: the harness had all there was to read)
    s = crate_run(tmp_path, monkeypatch, running=True)
    idx = s.game.codex.indexer
    write_codex(s, "blueprints/BP_Boss.txt", BOSS)
    idx.batches.append({"n": 1, "at": time.time() - 1,
                        "files": [{"path": "blueprints/BP_Boss.txt", "kind": "new", "line": "x"}]})
    s.run_harness(15)
    # After it started only the level's snapshot came (it isn't one of the game's files, as the codex's count isn't)
    idx.batches.append({"n": 2, "at": time.time() + 1,
                        "files": [{"path": "level.txt", "kind": "changed", "line": "x"}]})
    idx.running = False
    assert session.index_alert(s) == ""
    report = session.finish(s, time.time(), None, "")
    assert not any("indexed" in a for a in report.get("alerts", []))
    assert report["read"]["after"] == {"files": 0, "kinds": {}}
    assert report["read"]["lines"][2] == "Exported after it started, so not read for it: none."
    # One of the game's files after it started: the alert
    idx.batches.append({"n": 3, "at": time.time() + 1,
                        "files": [{"path": "data/DT_Late.csv", "kind": "new", "line": "x"}]})
    assert session.index_alert(s) == PARTIAL_ALERT
    # A batch from before the harness started is never "after"
    idx.batches[:] = idx.batches[:1]
    assert session.index_alert(s) == ""


def test_with_no_indexer_the_report_says_whole_codex_only_when_this_run_got_it(tmp_path, monkeypatch):
    def line(s):
        return session.read_summary(s)["lines"][0]

    head = "Harness 1, the last one played, was written with "
    tail = " Read for it: nothing by the agent; the background researcher was off."
    # A plugin with no codex ops and no project folder: no codex at all
    s = crate_run(tmp_path, monkeypatch, running=None)
    s.run_harness(15)
    assert line(s) == head + "no codex of the game's files (the plugin sent none)." + tail
    assert session.read_summary(s)["index"] == {"complete": False, "files": 0, "fetched": False}
    # The fetch failed: said, with why
    s.game.codex.fetch_error = "the game didn't answer codex_manifest within 900s"
    assert line(s) == (head + "no codex of the game's files (this run couldn't fetch them: the game didn't answer "
                       "codex_manifest within 900s)." + tail)
    assert session.read_summary(s)["index"]["error"] == "the game didn't answer codex_manifest within 900s"
    # An earlier run's codex is there, but this run got nothing new: it may be stale
    write_codex(s, "blueprints/BP_Boss.txt", BOSS)
    write_codex(s, "data/DT_Loot.csv", "---,Item\nA,Sword\n")
    write_codex(s, "README.md", "# Codex")  # (the codex's own index isn't one of the game's files)
    assert line(s) == (head + "the codex an earlier run left (2 files; this run couldn't fetch the game's files: the "
                       "game didn't answer codex_manifest within 900s)." + tail)
    s.game.codex.fetch_error = None
    assert line(s) == head + "the codex an earlier run left (2 files; the plugin sent none this run)." + tail
    assert session.read_summary(s)["index"] == {"complete": False, "files": 2, "fetched": False}
    # This run fetched it from the plugin, or read the project's own files: whole, and said how it got it
    for source, how in (("plugin", "fetched by this run"), ("project", "read from the project's folder this run")):
        s.game.codex.source = source
        assert line(s) == head + f"the game's whole codex ({how})." + tail
        assert session.read_summary(s)["index"] == {"complete": True}
    # The fetch failed and the project's own files were read instead (build_local: its source, and only what an
    # earlier export left of its Blueprints and data): not whole, and the error is kept
    s.game.codex.source = "project"
    s.game.codex.fetch_error = "the game didn't answer codex_manifest within 900s"
    assert line(s) == (head + "the project's own files, read from its folder (2 files; this run couldn't fetch the "
                       "game's files from the plugin: the game didn't answer codex_manifest within 900s, so the "
                       "Blueprints and data may be an earlier export's, or missing)." + tail)
    assert session.read_summary(s)["index"] == {"complete": False, "files": 2, "fetched": False, "from": "project",
                                                "error": "the game didn't answer codex_manifest within 900s"}


def test_the_codex_says_how_this_run_got_it(tmp_path, monkeypatch):
    from autopilot.kit.game import GameError

    monkeypatch.setattr(codex, "MEMORY", str(tmp_path / "mem"))
    monkeypatch.setattr(codex, "snapshot_level", lambda game, root: None)
    assert codex.Codex.for_project("New").source is None  # (not built: nothing fetched this run)

    class NoOps:
        def request(self, op, args, timeout=None):
            raise GameError("unknown op: " + op)

    class Gone:
        def request(self, op, args, timeout=None):
            raise TimeoutError("the game didn't answer codex_manifest within 900s")

    cx = codex.build("NoOps", game=NoOps(), stream=True)
    assert cx.source is None and cx.fetch_error is None and cx.indexer is None
    cx = codex.build("Gone", game=Gone(), stream=True)
    assert cx.source is None and cx.fetch_error == "the game didn't answer codex_manifest within 900s"
    plugin = ExportingPlugin({"Saved/AutopilotCodex/blueprints/G/BP_Boss.txt": BOSS}, [])
    cx = codex.build("Streamed", game=plugin, stream=True)
    assert cx.source == "plugin" and cx.fetch_error is None
    cx = codex.build("Fetched", game=ExportingPlugin({"Saved/AutopilotCodex/blueprints/G/BP_Boss.txt": BOSS}, []))
    assert cx.source == "plugin"


def background_researcher(read=(), passes=0, failed=0):
    """A stand-in for the run's background researcher: what it read ([(time.time(), path)]) and its passes."""
    return types.SimpleNamespace(read=list(read), passes=passes, failed=failed, stop=lambda: None, arrived=None,
                                 playing=lambda n: None, episode=lambda n, summary: None)


def test_the_report_says_what_the_last_harness_had_read_while_indexing(tmp_path, monkeypatch):
    s = crate_run(tmp_path, monkeypatch, running=True)
    idx = s.game.codex.indexer
    write_codex(s, "blueprints/BP_Boss.txt", BOSS)
    write_codex(s, "data/DT_Loot.csv", "---,Item\nA,Sword\n")
    idx.batches.append({"n": 1, "at": time.time() - 1,
                        "files": [{"path": "blueprints/BP_Boss.txt", "kind": "new", "line": "x"}]})
    s.activity("read", "Read codex/blueprints/BP_Boss.txt", "codex/blueprints/BP_Boss.txt")
    s.activity("read", "Read codex/blueprints/BP_Boss.txt", "codex/blueprints/BP_Boss.txt")  # (one file, read twice)
    s.activity("read", "Read notes.md", "notes.md")  # (not one of the game's files)
    s.activity("search", "Searched the game's code for Boss", "Boss")
    s.add_finding("Boss enrages at half health", [], "from files the export brought")
    s.researcher = background_researcher([(time.time(), "codex/data/DT_Loot.csv")], passes=2)
    s.run_harness(15)
    # What came after the harness started was not read for it
    idx.batches.append({"n": 2, "at": time.time() + 1,
                        "files": [{"path": "data/DT_Late.csv", "kind": "new", "line": "x"}]})
    s.activity("read", "Read codex/data/DT_Late.csv", "codex/data/DT_Late.csv")
    s.researcher.read.append((time.time() + 1, "codex/data/DT_Late.csv"))
    report = session.finish(s, time.time(), None, "")
    read = report["read"]
    assert read["harness"] == 1
    assert read["index"] == {"complete": False, "done": 5, "total": 9, "files": 40}
    assert read["had"] == {"blueprints": 1, "data": 1}
    assert read["agent"] == {"files": 1, "searches": 1}
    assert read["researcher"] == {"files": 1, "findings": 1, "passes": 2, "failed": 0}
    assert read["after"] == {"files": 1, "kinds": {"data": 1}}
    assert read["lines"] == [
        "Harness 1, the last one played, was written while the game's files were still being indexed: 5 of 9 "
        "exported, 2 files in the codex (1 Blueprint, 1 data file).",
        "Read for it: 1 of the game's files and 1 search by the agent; 1 file and 1 finding by the background "
        "researcher.",
        "Exported after it started, so not read for it: 1 file (1 data file)."]
    assert cli.result_frame(report, s.prompt)["read"] == read["lines"]
    assert report["alerts"] == [PARTIAL_ALERT]  # (the partial-index alert stays as it was)
    with open(os.path.join(s.run_dir, "report.json"), encoding="utf-8") as f:
        assert '"read"' in f.read()


def test_the_report_says_in_one_line_what_was_read_when_the_index_was_complete(tmp_path, monkeypatch):
    s = crate_run(tmp_path, monkeypatch, running=False)
    s.game.codex.indexer.done = 9  # (an export that ended well wrote it all)
    s.activity("read", "Read codex/blueprints/BP_Boss.txt", "codex/blueprints/BP_Boss.txt")
    s.run_harness(15)
    read = session.finish(s, time.time(), None, "")["read"]
    assert read["lines"] == [
        "Harness 1, the last one played, was written with all of the game's files indexed (9 of 9 exported). "
        "Read for it: 1 of the game's files and 0 searches by the agent; the background researcher was off."]
    assert read["index"] == {"complete": True, "done": 9, "total": 9, "files": 40}
    assert "had" not in read and "after" not in read and read["researcher"] is None
    # An export that didn't run (nothing changed since the last): the codex's files are counted
    s.harness_index[1] = dict(s.harness_index[1], done=0, total=0)
    assert session.read_summary(s)["lines"][0].startswith(
        "Harness 1, the last one played, was written with all of the game's files indexed (40 files in the codex).")
    # An indexer that gave up (the plugin stopped answering) never says all the files were indexed
    s.harness_index[1] = dict(s.harness_index[1], done=5, total=9, error="the plugin stopped answering")
    line = session.read_summary(s)["lines"][0]
    assert line.startswith("Harness 1, the last one played, was written after the indexing of the game's files "
                           "stopped early (5 of 9 exported). Read for it: ")
    assert session.read_summary(s)["index"]["complete"] is False
    # The background researcher's passes that failed are said
    s.harness_index[1] = dict(s.harness_index[1], done=9, total=9, error=None)
    s.researcher = background_researcher(passes=3, failed=3)
    assert session.read_summary(s)["lines"][0].endswith(
        "; nothing by the background researcher (its 3 passes failed).")
    s.researcher = background_researcher([(0.0, "codex/data/DT_Loot.csv")], passes=7, failed=3)
    assert session.read_summary(s)["lines"][0].endswith(
        "; 1 file and 0 findings by the background researcher (3 of 7 passes failed).")
    s.researcher = background_researcher(passes=2)
    assert session.read_summary(s)["lines"][0].endswith("; nothing by the background researcher.")
    # No indexer: the codex was whole when the run started (this run fetched it, and it has the game's files)
    s = crate_run(tmp_path / "whole", monkeypatch, running=None)
    s.game.codex.source = "plugin"
    write_codex(s, "blueprints/BP_Boss.txt", BOSS)
    s.run_harness(15)
    read = session.finish(s, time.time(), None, "")["read"]
    assert read["index"] == {"complete": True}
    assert read["lines"] == ["Harness 1, the last one played, was written with the game's whole codex (fetched "
                             "by this run). Read for it: nothing by the agent; the background researcher was off."]
    # No harness played: what was read in the whole run
    s = crate_run(tmp_path / "none", monkeypatch, running=True)
    s.activity("search", "Searched the game's code for Boss", "Boss")
    read = session.finish(s, time.time(), None, "")["read"]
    assert read["harness"] is None
    assert read["lines"] == ["No harness was played. Read: 0 of the game's files and 1 search by the agent; the "
                             "background researcher was off."]


def test_the_researcher_counts_its_reads_and_failed_passes(tmp_path, monkeypatch):
    s = open_run(tmp_path, monkeypatch)
    write_codex(s, "blueprints/BP_Boss.txt", BOSS)
    r = researchermod.Researcher(s, FakeModel())
    assert r.read == [] and r.failed == 0
    r._tool("read_file", {"path": "codex/blueprints/BP_Boss.txt"}, [("playing", 1, None)], [])
    r._tool("read_file", {"path": "./codex/blueprints/BP_Boss.txt"}, [("playing", 1, None)], [])
    assert [path for _, path in r.read] == ["codex/blueprints/BP_Boss.txt"] * 2
    r._tool("read_file", {"path": "codex/blueprints/BP_Gate.txt"}, [("playing", 1, None)], [])  # (no such file)
    assert len(r.read) == 2

    def boom(events):
        raise RuntimeError("HTTP 400: the model isn't available")

    monkeypatch.setattr(r, "_pass", boom)
    r.playing(1)
    deadline = time.time() + 5
    while r.failed < 1 and time.time() < deadline:
        time.sleep(0.02)
    r.stop()
    assert r.failed == 1


def test_the_indexers_seconds_stop_when_the_export_ends(tmp_path, monkeypatch):
    plugin = ExportingPlugin({}, [{"Saved/AutopilotCodex/a.txt": "a"}, {"Saved/AutopilotCodex/b.txt": "b"}])
    idx = make_indexer(tmp_path, plugin, monkeypatch)
    idx.start()
    wait_for(idx)
    a = idx.status()["seconds"]
    time.sleep(0.2)
    assert idx.status()["seconds"] == a


def test_waiting_for_the_index_is_off_unless_chosen(monkeypatch):
    monkeypatch.delenv("AUTOPILOT_WAIT_FOR_INDEX", raising=False)
    assert codex.wait_for_index(None) is False and codex.wait_for_index({}) is False
    assert codex.wait_for_index({"wait_for_index": True}) is True
    assert codex.wait_for_index({"wait_for_index": False}) is False
    assert codex.wait_for_index({"wait_for_index": "yes"}) is False  # (only a true/false the plugin sent)
    # AUTOPILOT_WAIT_FOR_INDEX wins over the plugin's switch (a bench or an eval host forces one mode)
    monkeypatch.setenv("AUTOPILOT_WAIT_FOR_INDEX", "1")
    assert codex.wait_for_index(None) is True and codex.wait_for_index({"wait_for_index": False}) is True
    monkeypatch.setenv("AUTOPILOT_WAIT_FOR_INDEX", "0")
    assert codex.wait_for_index({"wait_for_index": True}) is False


def test_the_indexer_can_be_waited_for(tmp_path, monkeypatch):
    plugin = ExportingPlugin({}, [{"Saved/AutopilotCodex/a.txt": "a"}, {"Saved/AutopilotCodex/b.txt": "b"},
                                  {"Saved/AutopilotCodex/c.txt": "c"}])
    idx = make_indexer(tmp_path, plugin, monkeypatch)
    monkeypatch.setattr(codex, "WAIT_TICK_SECONDS", 0.0)
    idx.start()
    said = []
    assert idx.wait(timeout=5, tick=said.append) is True
    assert idx.status()["finished"] and not idx.status()["running"]
    assert all(os.path.exists(os.path.join(idx.root, name)) for name in ("a.txt", "b.txt", "c.txt"))
    assert said and said[0].endswith("of 3 written")
    # An export that doesn't end in time: the wait gives up, the export goes on (the run starts on what is there)
    slow = ExportingPlugin({}, [{"Saved/AutopilotCodex/a.txt": "a"}, {"Saved/AutopilotCodex/b.txt": "b"}])
    idx = make_indexer(tmp_path / "slow", slow, monkeypatch)
    monkeypatch.setattr(codex, "POLL_SECONDS", 30.0)
    idx.start()
    t0 = time.time()
    assert idx.wait(timeout=0.05) is False and time.time() - t0 < 2
    assert idx.status()["running"] and idx.thread.is_alive()
    # until() true (the user pressed Stop, the plugin went): the wait ends at once, the export goes on
    t0 = time.time()
    assert idx.wait(timeout=60, until=lambda: True) is False and time.time() - t0 < 2
    assert idx.status()["running"]
    idx.stop()
    idx.thread.join(5)
    assert not idx.thread.is_alive()


def test_files_that_came_while_the_run_waited_for_the_export_arent_news(tmp_path, monkeypatch):
    # (the user chose to wait for the export, cli.prepare: what it brought meanwhile is the codex the run starts on)
    monkeypatch.setattr(codex, "MEMORY", str(tmp_path))
    game = Game(FakeLink())
    game.codex = codex.Codex.for_project("fake")
    idx = StubIndexer([{"n": 1, "at": time.time(), "files": [
        {"path": "blueprints/BP_Boss.txt", "kind": "new", "line": "Blueprint"}]}], running=False)
    idx.waited_batches = 1
    game.codex.indexer = idx
    s = session.Session(game, "fake", "Kill the boss", "r1", activity=lambda f: None, client=FakeJev("go"))
    assert "BP_Boss" not in s.news()
    idx.batches.append({"n": 2, "at": time.time(), "files": [
        {"path": "blueprints/BP_Gate.txt", "kind": "new", "line": "Blueprint"}]})
    assert "codex/blueprints/BP_Gate.txt" in s.news()  # (one after the run started is news, as ever)
