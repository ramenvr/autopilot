"""The Claude Code agent's research subagents (autopilot/agents/claude_code.py): the researcher's definition, the
PreToolUse guard's decisions for the Agent tool and for a subagent's own calls, what the main agent is told, and the
log lines a researcher's messages make. The SDK is never started: the hook and the message notes are called directly."""

import asyncio
import os
import types

from autopilot.agents import claude_code as cc


def _session(tmp_path):
    root = tmp_path / "memory" / "Farm"
    (root / "runs" / "r1").mkdir(parents=True)
    (root / "codex" / "data").mkdir(parents=True)
    (root / "codex" / "data" / "DT_Enemies.csv").write_text("---,Health\nGoblin,30\n", encoding="utf-8")
    return types.SimpleNamespace(root=str(root), run_dir=str(root / "runs" / "r1"), run_rel="runs/r1",
                                 research_budget=600.0, research_over=lambda: False)


def _check(session, name, args, **context):
    hooks = cc._guard(session)
    (matcher,) = hooks["PreToolUse"]
    data = {"hook_event_name": "PreToolUse", "tool_name": name, "tool_input": args, **context}
    out = asyncio.run(matcher.hooks[0](data, "toolu_x", {}))
    return out.get("hookSpecificOutput", {}).get("permissionDecision", "allow"), out.get(
        "hookSpecificOutput", {}).get("permissionDecisionReason", "")


def test_the_researcher_reads_the_codex_and_nothing_else():
    game_tools = [f"mcp__game__{n}" for n in ("probe", "run_harness", "report")]
    d = cc.researcher("claude-opus-5-5", game_tools)
    assert d.tools == ["Read", "Grep", "Glob"]
    assert not any(t.startswith("mcp__game__") or t in ("Write", "Edit", "Agent") for t in d.tools)
    assert set(d.disallowedTools) >= {"Write", "Edit", "Agent", *game_tools}
    assert d.model == "claude-opus-5-5" and d.maxTurns == cc.RESEARCH_TURNS and d.effort == "high"
    assert "codex/" in d.prompt and "can't run or touch" in d.prompt and "Cite the file" in d.prompt
    assert "Codex-only" in d.description


def test_the_guard_allows_only_the_researcher_subagent(tmp_path):
    s = _session(tmp_path)
    assert _check(s, "Agent", {"subagent_type": "researcher", "prompt": "what kills a goblin?"}) == ("allow", "")
    decision, why = _check(s, "Agent", {"subagent_type": "general-purpose", "prompt": "do it all"})
    assert decision == "deny" and "researcher" in why
    decision, why = _check(s, "Agent", {"prompt": "no type"})
    assert decision == "deny"


def test_a_subagents_calls_keep_the_file_rules_and_lose_the_game_and_the_pen(tmp_path):
    s = _session(tmp_path)
    inside = {"agent_id": "agent-1", "agent_type": "researcher"}
    assert _check(s, "Read", {"file_path": os.path.join(s.root, "codex", "data", "DT_Enemies.csv")}, **inside) == (
        "allow", "")
    assert _check(s, "Grep", {"pattern": "Health", "path": os.path.join(s.root, "codex")}, **inside) == ("allow", "")
    decision, why = _check(s, "Read", {"file_path": str(tmp_path / "elsewhere.txt")}, **inside)
    assert decision == "deny" and "this game's folder" in why
    decision, why = _check(s, "mcp__game__probe", {"code": "game.press('E')"}, **inside)
    assert decision == "deny" and "can't act on the game" in why
    decision, why = _check(s, "Write", {"file_path": os.path.join(s.run_dir, "harness.py"), "content": ""}, **inside)
    assert decision == "deny" and "only reads" in why
    decision, why = _check(s, "Agent", {"subagent_type": "researcher", "prompt": "more"}, **inside)
    assert decision == "deny" and "subagents" in why
    # the main agent's own calls as before
    assert _check(s, "mcp__game__probe", {"code": "1"}) == ("allow", "")
    assert _check(s, "Write", {"file_path": os.path.join(s.run_dir, "harness.py"), "content": ""}) == ("allow", "")
    assert _check(s, "Write", {"file_path": os.path.join(s.root, "codex", "x.txt"), "content": ""})[0] == "deny"
    assert _check(s, "Bash", {"command": "ls"})[0] == "deny"


def test_a_researchers_messages_are_logged_with_a_prefix():
    from claude_agent_sdk import AssistantMessage, TextBlock, ToolUseBlock

    seen = {}
    m1 = AssistantMessage(content=[ToolUseBlock(id="t1", name="Grep", input={"pattern": "Health", "path": "codex"})],
                          model="m", parent_tool_use_id="toolu_A")
    m2 = AssistantMessage(content=[TextBlock(text="  ")], model="m", parent_tool_use_id="toolu_B")
    m3 = AssistantMessage(content=[TextBlock(text="- Goblin Health 30: codex/data/DT_Enemies.csv row Goblin")],
                          model="m", parent_tool_use_id="toolu_A")
    assert cc.subagent_notes(m1, seen) == ['researcher 1 tool Grep: {"pattern": "Health", "path": "codex"}']
    assert cc.subagent_notes(m2, seen) == []  # (blank text: nothing to log, though the subagent is numbered)
    assert cc.subagent_notes(m3, seen) == [
        "researcher 1 says: - Goblin Health 30: codex/data/DT_Enemies.csv row Goblin"]
    assert seen == {"toolu_A": 1, "toolu_B": 2}


def test_what_the_main_agent_is_told_and_how_many_it_may_run(monkeypatch):
    monkeypatch.delenv("AUTOPILOT_REVIEWER", raising=False)
    assert cc.agents_note(0, False) == ""
    note = cc.agents_note(4, False)
    assert note.startswith("Researchers:") and "\n" not in note and 'subagent_type "researcher"' in note
    assert f"up to {min(4, cc.research_calls())} run at once" in note and "leads" in note
    assert "can't act on the game" in note
    assert cc.AGENTS_NOTE == cc.agents_note(cc.RESEARCHERS, True)
    assert isinstance(cc.RESEARCHERS, int)
    monkeypatch.setenv("AUTOPILOT_RESEARCHERS", "2")
    assert cc.researchers() == 2 and "up to 2 run" in cc.agents_note()
    monkeypatch.setenv("AUTOPILOT_RESEARCHERS", "0")
    assert cc.researchers() == 0 and cc.agents_note().startswith("Reviewer:")  # (the reviewer is on by default)
    monkeypatch.setenv("AUTOPILOT_REVIEWER", "0")
    assert not cc.reviewers() and cc.agents_note() == ""
    monkeypatch.setenv("AUTOPILOT_RESEARCHERS", "many")
    assert cc.researchers() == cc.RESEARCHERS
    monkeypatch.delenv("AUTOPILOT_RESEARCHERS")
    assert cc.researchers() == cc.RESEARCHERS


def test_the_reviewer_reads_the_run_and_may_describe_frames_only(tmp_path, monkeypatch):
    monkeypatch.delenv("AUTOPILOT_REVIEWER", raising=False)
    game_tools = [f"mcp__game__{n}" for n in ("probe", "run_harness", "describe_frames", "report")]
    d = cc.reviewer("claude-opus-5-5", game_tools)
    assert d.tools == ["Read", "Grep", "Glob", "mcp__game__describe_frames"]
    assert "mcp__game__probe" in d.disallowedTools and "mcp__game__describe_frames" not in d.disallowedTools
    assert "one change" in d.prompt and "picture wins" in d.prompt and d.maxTurns == cc.REVIEW_TURNS
    agents = cc.subagents("claude-opus-5-5", game_tools)
    assert set(agents) == {"reviewer"}  # (no researchers by default)
    monkeypatch.setenv("AUTOPILOT_RESEARCHERS", "2")
    assert set(cc.subagents("claude-opus-5-5", game_tools)) == {"researcher", "reviewer"}
    # the guard: the main agent may start a reviewer; a reviewer may describe frames and nothing else of the game
    s = _session(tmp_path)
    assert _check(s, "Agent", {"subagent_type": "reviewer", "prompt": "episode 2"}) == ("allow", "")
    inside = {"agent_id": "agent-2", "agent_type": "reviewer"}
    assert _check(s, "mcp__game__describe_frames", {"questions": ["what moves?"]}, **inside) == ("allow", "")
    decision, why = _check(s, "mcp__game__probe", {"code": "1"}, **inside)
    assert decision == "deny" and "can't act on the game" in why
    decision, why = _check(s, "Write", {"file_path": os.path.join(s.run_dir, "harness.py"), "content": ""}, **inside)
    assert decision == "deny"
    # a researcher still may not describe frames
    decision, _ = _check(s, "mcp__game__describe_frames", {"questions": ["x"]}, agent_id="a", agent_type="researcher")
    assert decision == "deny"
    # the reviewer may be started once the time for reading and probing is up (an episode has played by then)
    s.research_over = lambda: True
    assert _check(s, "Agent", {"subagent_type": "reviewer", "prompt": "episode 3"}) == ("allow", "")
    monkeypatch.setenv("AUTOPILOT_REVIEWER", "0")
    s2 = _session(tmp_path / "another")
    decision, why = _check(s2, "Agent", {"subagent_type": "reviewer", "prompt": "episode 3"})
    assert decision == "deny"


def test_a_run_has_only_so_many_researcher_questions(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOPILOT_RESEARCH_CALLS", "2")
    s = _session(tmp_path)
    hook = cc._guard(s)["PreToolUse"][0].hooks[0]  # (one guard: the budget is the run's)

    def ask(name, args):
        out = asyncio.run(hook({"tool_name": name, "tool_input": args}, "toolu_x", {}))
        spec = out.get("hookSpecificOutput") or {}
        return spec.get("permissionDecision", "allow"), spec.get("permissionDecisionReason", "")

    ask_r = lambda: ask("Agent", {"subagent_type": "researcher", "prompt": "where is the lever?"})
    assert ask_r() == ("allow", "") and ask_r() == ("allow", "")
    decision, why = ask_r()
    assert decision == "deny" and "2 researcher questions are used" in why
    assert ask("Read", {"file_path": os.path.join(s.root, "codex", "x.txt")}) == ("allow", "")  # (reading goes on)
    assert "this run has 2 questions in all" in cc.agents_note(4)


def test_a_game_tool_refuses_an_argument_name_it_doesnt_take(monkeypatch):
    import claude_agent_sdk

    made = {}
    monkeypatch.setattr(claude_agent_sdk, "create_sdk_mcp_server", lambda name, tools: made.update(tools=tools) or {})
    calls = []
    session = types.SimpleNamespace(tool_seconds=0.0, steer_note=lambda: "", news=lambda: "", clock=lambda: "[clock]",
                                    result=lambda text: text + "\n[clock]",
                                    run_harness=lambda *a, **k: calls.append((a, k)) or "episode 1")
    cc._tools(session)
    run_harness = next(t for t in made["tools"] if t.name == "run_harness")
    refused = asyncio.run(run_harness.handler({"seconds": 10, "fps": 30}))
    assert refused["isError"] and not calls
    assert refused["content"][0]["text"].startswith("run_harness: unknown argument(s) fps; it takes seconds, restart, "
                                                    "frames_fps.")
    done = asyncio.run(run_harness.handler({"seconds": 10, "frames_fps": 30}))
    assert done["content"][0]["text"].startswith("episode 1") and calls == [((10, True), {"frames_fps": 30})]
