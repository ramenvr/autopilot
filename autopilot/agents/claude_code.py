"""The agent on Claude Code (the Claude Agent SDK). AUTOPILOT_AGENT=claude picks it.

The game's tools (probe, run_harness, snapshot, restart_level, report) are an in-process MCP server whose bodies are
Session's (autopilot/agents/session.py). Claude Code's own tools are limited to reading, searching and writing files,
and a hook holds them to this game's folder: it may read the game's folder and the kit, and write only this run's
folder, notes.md and lib.py.

Subagents, defined per run (the SDK's Agent tool, ClaudeAgentOptions.agents): `researcher` subagents answer one codex
question each from the game's files alone, up to RESEARCHERS at once (AUTOPILOT_RESEARCHERS; off by default, 0); a
`reviewer` (AUTOPILOT_REVIEWER, on by default) reviews one episode (its trace summary, what the picture showed, the
harness) and proposes the one change for the next harness, looking at the frames itself (describe_frames) where the
summary leaves a death's cause unclear. A researcher gets Read, Grep and Glob only, a reviewer those and
describe_frames; the same hook runs for their calls (the CLI marks them with agent_id / agent_type) and refuses them
the other game tools, writes and subagents of their own. The other agents (OpenCode, Codex) have no subagents.
"""

import asyncio
import json
import os
import subprocess
import tempfile
import time
import traceback

from .. import engines
from . import whole
from .session import (
    PACKAGE,
    PROBING,
    READING,
    SECRET,
    TOOL_SPECS,
    WRITING,
    fast_mode,
    finish,
    first_message,
    open_session,
    system_prompt,
    unknown_args,
    watch_steering,
)
from .session import effort as effort_for
from .session import model as model_for


def max_turns():
    """A limit on the agent's turns only when AUTOPILOT_TURNS asks for one, as the environment says it now
    (autopilot.env is loaded before every run, after this module is imported): a run is held to its time limit, and a
    limit on turns, which the agent isn't told of, cuts it off with time left and before it reports."""
    return int(os.environ.get("AUTOPILOT_TURNS") or 0) or None


# The longest system prompt (as quoted on Windows) passed on Claude Code's command line: Windows caps the whole line at
# 32767 characters, and the rest of it (the CLI's path, tools, settings) takes about 1000; a longer one goes as a file
PROMPT_ON_COMMAND_LINE = 30000


def fast_settings(model):
    """The Claude Code settings that turn fast mode on for this model (an Opus one, unless AUTOPILOT_FAST=0:
    session.fast_mode), or None."""
    return '{"fastMode": true}' if fast_mode() and "opus" in str(model).lower() else None


def prompt_option(system, windows=os.name == "nt"):
    """The system prompt as Claude Code is given it: (its option, a file to delete once Claude Code has ended, or None).
    On its command line, as ever; but Windows caps a command line at 32767 characters (Claude Code's start then fails,
    "Claude Code not found"), so there a longer prompt goes as a file: written as it is (LF line ends, as on the
    command line), outside the project's memory (later runs read that)."""
    if not windows or len(subprocess.list2cmdline([system])) <= PROMPT_ON_COMMAND_LINE:
        return system, None
    fd, path = tempfile.mkstemp(prefix="autopilot-system-", suffix=".md")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        f.write(system)
    return {"type": "file", "path": path}, path


def _tools(session):
    from claude_agent_sdk import create_sdk_mcp_server, tool

    def wrap(name, fn):
        async def inner(args):
            refused = unknown_args(name, args)
            if refused:
                return {"content": [{"type": "text", "text": refused}], "isError": True}
            t0 = time.time()
            try:
                text = await asyncio.to_thread(fn, args)
            except Exception:
                text = "Tool failed:\n" + traceback.format_exc(limit=5)
            session.tool_seconds += time.time() - t0
            return {"content": [{"type": "text", "text": session.result(text)}]}
        return inner

    bodies = {
        "probe": lambda a: session.probe(a["code"]),
        "run_harness": lambda a: session.run_harness(a.get("seconds", 30), a.get("restart", True),
                                                     frames_fps=a.get("frames_fps")),
        "snapshot": lambda a: session.snapshot(),
        "describe_frames": lambda a: session.describe_frames(a.get("questions") or [], a.get("episode"),
                                                             a.get("start"), a.get("end"), a.get("frames")),
        "restart_level": lambda a: session.restart_level(a.get("level")),
        "report": lambda a: session.do_report(a),
    }
    tools = [tool(name, description, schema)(wrap(name, bodies[name])) for name, description, schema in TOOL_SPECS]
    server = create_sdk_mcp_server("game", tools=tools)
    names = [f"mcp__game__{name}" for name, _, _ in TOOL_SPECS]
    return server, names


LOOKUP = ["Read", "Grep", "Glob"]
WRITE = ["Write", "Edit"]
AGENT = "Agent"  # Claude Code's subagent tool
RESEARCHER = "researcher"  # the subagent_type that reads the codex for one question
REVIEWER = "reviewer"  # the subagent_type that reviews one episode
DESCRIBE = "mcp__game__describe_frames"  # (the one game tool a reviewer may call: it only looks)
# Whether runs have the reviewer (AUTOPILOT_REVIEWER=0 turns it off), read when a run starts (reviewers())
REVIEW_TURNS = 20  # a review's turns: the summary, the harness, a few looks at the frames, the proposal

# How many researchers the main agent may run at once (the prompt says so): AUTOPILOT_RESEARCHERS, 0 for none. Off by
# default: on the goal suites researchers cost about 1.6x per run and won nothing the probes didn't (their one gain,
# where the targets and dangers are, the research helpers find in seconds). Read here as the environment is at
# import, and again by researchers() when a run starts (autopilot.env is loaded before each run)
RESEARCHERS = int(os.environ.get("AUTOPILOT_RESEARCHERS") or 0)
RESEARCH_TURNS = 25  # a researcher's turns: a few greps and reads answer a question; more is reading the codex
# How many researcher questions one run may ask in all (AUTOPILOT_RESEARCH_CALLS): on the eval suites researchers
# were a third of a run's cost, 13-16 questions a suite, and the later ones asked what a probe answers in seconds
RESEARCH_CALLS = int(os.environ.get("AUTOPILOT_RESEARCH_CALLS") or 3)


def research_calls():
    """How many researcher questions this run may ask: AUTOPILOT_RESEARCH_CALLS as the environment says now."""
    try:
        return max(0, int(os.environ.get("AUTOPILOT_RESEARCH_CALLS") or RESEARCH_CALLS))
    except ValueError:
        return RESEARCH_CALLS


def researchers():
    """How many researchers this run may use: AUTOPILOT_RESEARCHERS as the environment says now, else RESEARCHERS."""
    try:
        return max(0, int(os.environ.get("AUTOPILOT_RESEARCHERS") or RESEARCHERS))
    except ValueError:
        return RESEARCHERS


RESEARCHER_PROMPT = """You answer one question about this game for the agent writing its harness, from the game's code
and data alone. Your working directory is the game's folder: codex/ holds the game's code and data as text
(codex/README.md says what is where: Blueprints as pseudo-code, C++ source, input mappings, data tables as CSV, the
level's actors); notes.md and lib.py hold what earlier runs on this game verified and kept; runs/ holds earlier runs'
harnesses and reports.

Find the exact facts: numbers, names, code paths, which input drives what, what a thing needs (range, a held item, a
mode, a cooldown, a state), what counts as done, what can hurt or stop the player. Cite the file and the line or row
for each fact (codex/blueprints/BP_Door.txt:120; codex/data/DT_Enemies.csv row Goblin). Say what you could not find
rather than guess, and say so when two places disagree. Be targeted: Grep (and Glob) first, then Read the lines
around the hits, several files in one turn; don't read whole data tables or the whole codex. You can't run or touch
the game, and you don't see what the asker has read: what you find is a lead the asker checks in the live game.
Answer in a short structured list (fact: where), no prose, no preamble, under 40 lines."""

RESEARCHER_DESCRIPTION = ("Answers one question about this game's mechanics from its code and data (the codex, "
                          "notes.md, lib.py, earlier runs), citing the file and line of each fact. Codex-only: it "
                          "can't act on the game or write.")


REVIEWER_PROMPT = """You review one episode of play for the engineer writing the harness (a program that makes a
decision model play this game), and propose the one change to make next. Your working directory is the game's folder:
runs/<run>/trace<n>_summary.txt is the episode's summary (how each option fared, the timeline, where the plan got,
"What the picture showed": each death's cause as a model saw it in the frames, with a sentence; and the executors'
step times), harness<n>.py the harness that played it, findings.md what a reader found in the code, notes.md and
lib.py what earlier runs learned. The game is paused: you can't act on it. You may call describe_frames (episode n,
start and end game times, open questions about what moves and what is there) where the summary leaves a death's or a
stall's cause unclear, or the numbers and the picture disagree: the picture wins.

Answer in under 40 lines, no preamble:
1. How the episode ended and where the plan stood.
2. Each death or stall: its cause, from the picture and the numbers together (say which you went by).
3. THE one change to make first, as a concrete edit: the function, the condition or number, what it should read
   instead, and why that fixes the cause (a wait for a hazard's clear moment, a jump the game times, another way).
4. What to check in a probe before relying on it.
Say what you could not find rather than guess."""

REVIEWER_DESCRIPTION = ("Reviews one episode of play (its trace summary, what the picture showed, the harness) and "
                        "proposes the one change for the next harness. Give it the episode's number. It reads the "
                        "game's folder and may describe frames; it can't act on the game or write.")


def agents_note(n=None, review=None):
    """What the main agent is told about its subagents (a paragraph for session.system_prompt's `agents`, placed in
    step 1 of the instructions); "" when the run has none. n: how many researchers may run at once (researchers());
    review: whether the run has the reviewer (reviewers())."""
    n = researchers() if n is None else n
    review = reviewers() if review is None else review
    parts = []
    if n > 0:
        calls = research_calls()
        parts.append(
            f"Researchers: the Agent tool with subagent_type \"{RESEARCHER}\" answers one question about this game "
            f"from its code and data alone (codex/, notes.md, lib.py, earlier runs), citing the file and line of each "
            f"fact; "
            f"up to {min(n, calls)} run at once (one Agent call each, in the same turn), and this run has {calls} "
            "questions in all: spend them at the start, on what the codex answers better than a probe (the mechanics' "
            "code paths and numbers: which input drives what and what it needs; where the targets are placed; the UI "
            "flow; what can hurt the player), all in one turn, while you probe the live game yourself. A researcher "
            "reads only the files here: it can't act on the game, write, or see what you have read. Its answers are "
            "leads: check each in a probe before the harness relies on it.")
    if review:
        parts.append(
            f"Reviewer: the Agent tool with subagent_type \"{REVIEWER}\" reviews one episode (give it the episode's "
            "number): it reads the trace summary, what the picture showed and the harness, looks at the frames where "
            "a death's cause is unclear, and proposes the one change to make next as a concrete edit. Use it after "
            "an episode with deaths, failed options or a stall whose causes the summary doesn't settle, in the same "
            "turn as you start reading the summary yourself; its proposal is checked against the code (and a probe) "
            "before the harness takes it. It can't act on the game or write.")
    return " ".join(parts)


def reviewers():
    """Whether runs have the reviewer: AUTOPILOT_REVIEWER as the environment says now (0: off), else on."""
    return (os.environ.get("AUTOPILOT_REVIEWER") or "1").strip() != "0"


AGENTS_NOTE = agents_note(RESEARCHERS, True)


def researcher(model, game_tools=()):
    """The researcher's definition (claude_agent_sdk.AgentDefinition): the run's model and effort, codex-only tools."""
    from claude_agent_sdk import AgentDefinition

    return AgentDefinition(description=RESEARCHER_DESCRIPTION, prompt=RESEARCHER_PROMPT, tools=list(LOOKUP),
                           disallowedTools=WRITE + [AGENT] + list(game_tools), model=model, maxTurns=RESEARCH_TURNS,
                           effort=effort_for(model))


def reviewer(model, game_tools=()):
    """The reviewer's definition: the run's model and effort, the codex's tools and describe_frames (the one game
    tool that only looks), nothing else of the game's."""
    from claude_agent_sdk import AgentDefinition

    return AgentDefinition(description=REVIEWER_DESCRIPTION, prompt=REVIEWER_PROMPT, tools=list(LOOKUP) + [DESCRIBE],
                           disallowedTools=WRITE + [AGENT] + [t for t in game_tools if t != DESCRIBE], model=model,
                           maxTurns=REVIEW_TURNS, effort=effort_for(model))


def subagents(model, game_tools=()):
    """The run's subagents, as the SDK takes them ({type: AgentDefinition}): the researcher when the run has
    researchers, the reviewer unless turned off. {} when it has none."""
    out = {}
    if researchers() > 0:
        out[RESEARCHER] = researcher(model, game_tools)
    if reviewers():
        out[REVIEWER] = reviewer(model, game_tools)
    return out


def _guard(session):
    from claude_agent_sdk import HookMatcher

    root = os.path.realpath(session.root)
    writable = [os.path.realpath(session.run_dir), os.path.realpath(os.path.join(root, "notes.md")),
                os.path.realpath(os.path.join(root, "lib.py"))]
    kit_dir = os.path.realpath(PACKAGE)
    building = writable + [kit_dir]  # (what may be read once the time for reading and probing is up)
    budget = {"calls": research_calls()}  # (researcher questions this run may still ask)
    # (the researcher is always a type the guard knows: the prompt offers it only when the run has researchers)
    types = [RESEARCHER] + ([REVIEWER] if reviewers() else [])

    def inside(p, base):
        p = os.path.realpath(os.path.join(root, p))
        return p == base or p.startswith(base + os.sep)

    async def check(data, tool_use_id, context):
        name, args = data.get("tool_name"), data.get("tool_input") or {}
        # The hook runs for a subagent's calls too: the CLI then sets agent_id (and agent_type) on the hook's input
        # (claude_agent_sdk.types, PreToolUseHookInput). A subagent only reads (a reviewer may describe frames): no
        # other game tools, writes or subagents, whatever its definition's tool list lets through
        subagent = bool(data.get("agent_id") or data.get("agent_type"))
        kind = str(data.get("agent_type") or "")
        paths = [args[k] for k in ("file_path", "path", "notebook_path") if isinstance(args.get(k), str)]
        bad = None
        if name.startswith("mcp__game__"):
            if subagent and not (name == DESCRIBE and kind in ("", REVIEWER)):
                bad = "a subagent can't act on the game: answer from the files" + (
                    " and describe_frames" if kind == REVIEWER else "")
        elif (name in LOOKUP or name == AGENT) and session.research_over() and not (
                name in LOOKUP and paths and all(any(inside(p, b) for b in building) for p in paths)) and not (
                name == AGENT and args.get("subagent_type") == REVIEWER):
            # (the time for reading and probing is up: what the harness is built from can still be read, and an
            # episode reviewed)
            bad = (f"the {session.research_budget / 60:g} minutes of reading and probing before the first episode are "
                   f"up: write {session.run_rel}/harness.py from what you know now and call run_harness "
                   f"({session.run_rel}/, notes.md, lib.py and the kit can still be read)")
        elif name == AGENT:
            asked = args.get("subagent_type")
            if subagent:
                bad = "a subagent can't start subagents"
            elif asked not in types:
                bad = (f"only these subagents are available here (subagent_type): {', '.join(types)}" if types else
                       "no subagents are available here")
            elif asked == RESEARCHER and budget["calls"] <= 0:
                bad = (f"this run's {research_calls()} researcher questions are used: find the rest in the live game "
                       "(a probe answers in seconds what a researcher reads for minutes)")
            elif asked == RESEARCHER:
                budget["calls"] -= 1
        elif name not in LOOKUP + WRITE:
            bad = f"{name} isn't available here"
        elif any(not (inside(p, root) or (name in LOOKUP and inside(p, kit_dir))) for p in paths):
            bad = "only this game's folder (and the autopilot package's kit, to read) can be used"
        elif name == "Glob" and (os.path.isabs(args.get("pattern", "")) or ".." in args.get("pattern", "")):
            bad = "relative patterns only"
        elif name in WRITE and subagent:
            bad = "a subagent only reads: put what you found in your answer"
        elif name in WRITE and not any(inside(p, w) for p in paths for w in writable):
            bad = f"only {session.run_rel}/, notes.md and lib.py can be written"
        if not bad:
            return {}
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                       "permissionDecisionReason": bad}}

    async def told(data, tool_use_id, context):
        # Claude Code's own file tools' results carry the news too (the game tools' add it themselves): the files the
        # export brought, how far it is, the background researcher's findings. Not a researcher's: it isn't the one
        # who uses them
        if data.get("agent_id") or data.get("agent_type") or str(data.get("tool_name")).startswith("mcp__game__"):
            return {}
        news = session.news_within(session.news(), whole.ANSWER_CHARS).strip()  # (longer: whole in a file)
        if not news:
            return {}
        return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": news}}

    return {"PreToolUse": [HookMatcher(matcher=None, hooks=[check])],
            "PostToolUse": [HookMatcher(matcher=None, hooks=[told])]}


def subagent_notes(message, seen, kinds=None):
    """The implementer.log lines for a subagent's message: the SDK gives a subagent's messages with parent_tool_use_id
    (the Agent call that started it; its text only with forward_subagent_text), so each is logged as "researcher N
    says: ..." / "reviewer N tool Grep: {...}", N numbering the subagents of the run in `seen` (id -> N); kinds: the
    Agent calls' subagent_type by tool use id (a kind not known is "researcher", as before)."""
    from claude_agent_sdk import TextBlock, ToolUseBlock

    n = seen.setdefault(message.parent_tool_use_id, len(seen) + 1)
    kind = (kinds or {}).get(message.parent_tool_use_id) or RESEARCHER
    lines = []
    for b in message.content:
        if isinstance(b, TextBlock) and b.text.strip():
            lines.append(f"{kind} {n} says: {b.text[:1500]}")
        elif isinstance(b, ToolUseBlock):
            lines.append(f"{kind} {n} tool {b.name}: {json.dumps(b.input)[:300]}")
    return lines


def _env(root):
    """Claude Code's environment: every secret blanked but the Anthropic key; its config kept in the game's folder."""
    env = {k: "" for k in os.environ if SECRET.search(k) and k != "ANTHROPIC_API_KEY"}
    env["CLAUDECODE"] = ""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        env["ANTHROPIC_API_KEY"] = key
        env["CLAUDE_CONFIG_DIR"] = os.path.join(root, ".agent")
    return env


def implement(game, project, prompt, run_id=None, say=None, client=None, model=None, timeout=None, stop=None,
              steering=None, play_seconds=None, describer=None, activity=None):
    """Research, write, run and revise a harness for prompt on the live game; returns the report (a dict: outcome,
    summary, evidence, findings?) plus the run's facts.

    client: Jev's client (autopilot.runtime.jev_client()); model: the language model (default session.model());
    timeout: the whole budget in seconds (default session.default_timeout()); stop(): true once the run must end (the
    user pressed Stop); steering: a session.Steering the user's steer frames go into while the run plays;
    play_seconds: an exploration's minutes of play, in seconds; describer: the run's describe_frames
    (session.open_session).
    """
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        ClaudeSDKClient,
        ResultMessage,
        TextBlock,
        ToolUseBlock,
    )

    model = model or model_for("anthropic")
    session = open_session(game, project, prompt, run_id, say=say, client=client, timeout=timeout, stop=stop,
                           steering=steering, play_seconds=play_seconds, describer=describer, activity=activity)
    n_researchers = researchers()
    system, prompt_file = prompt_option(system_prompt(engines.of(game), session.run_rel,
                                                      agents=agents_note(n_researchers, reviewers())))
    server, tool_names = _tools(session)
    agents = subagents(model, tool_names)
    builtin = LOOKUP + WRITE + ([AGENT] if agents else [])
    options = ClaudeAgentOptions(
        model=model,
        effort=effort_for(model),
        settings=fast_settings(model),
        system_prompt=system,
        cwd=session.root,
        tools=builtin,
        allowed_tools=builtin + tool_names,
        mcp_servers={"game": server},
        agents=agents or None,
        forward_subagent_text=True,  # (a subagent's answer and reading reach the message loop: the log)
        permission_mode="acceptEdits",
        setting_sources=[],
        max_turns=max_turns(),
        hooks=_guard(session),
        env=_env(session.root),
    )
    if agents:
        session.note(f"subagents: {', '.join(agents)}" + (f" (up to {n_researchers} researchers at once)"
                                                           if n_researchers else ""))
    text = first_message(session)
    last_text = ""
    result_msg = None
    t0 = time.time()
    seen = {}  # parent_tool_use_id -> subagent number, for the log
    kinds = {}  # tool use id of an Agent call -> its subagent_type

    def say_tool(b):
        if b.name in LOOKUP or b.name == AGENT:
            session.say(READING)
        elif b.name in ("mcp__game__probe", "mcp__game__describe_frames"):
            session.say(PROBING)
        elif b.name in WRITE:
            session.say(WRITING)

    async def go():
        nonlocal last_text, result_msg
        async with ClaudeSDKClient(options) as agent:
            message = text
            while message:
                await agent.query(message)
                async for m in agent.receive_response():
                    if isinstance(m, AssistantMessage) and m.parent_tool_use_id:
                        # a subagent's words and reads (never the run's last words); the tab stays at reading
                        for line in subagent_notes(m, seen, kinds):
                            session.note(line)
                        session.say(READING)
                    elif isinstance(m, AssistantMessage):
                        for b in m.content:
                            if isinstance(b, TextBlock) and b.text.strip():
                                last_text = b.text
                                session.said(b.text)
                            elif isinstance(b, ToolUseBlock):
                                if b.name == AGENT and isinstance(b.input, dict):
                                    kinds[b.id] = str(b.input.get("subagent_type") or "")
                                session.tool_call(b.name, b.input, log=f"tool {b.name}: {json.dumps(b.input)[:600]}")
                                say_tool(b)
                    elif isinstance(m, ResultMessage):
                        result_msg = m
                # (the user steered after the last tool result, with no tool call left to carry it: on as asked)
                message = session.next_message()

    async def until_stopped():
        """go(), ended early when the session's stop() says so."""
        task = asyncio.ensure_future(go())
        while not task.done():
            if session.stop():
                if session.sandbox:
                    session.sandbox.stop()
                task.cancel()
                break
            await asyncio.sleep(0.5)
        return await task

    done = watch_steering(session)
    try:
        game.pause(True)  # (in the try: a game that doesn't answer still gets cleaned up and reported)
        asyncio.run(asyncio.wait_for(until_stopped(), session.budget))
        stopped = None
    except asyncio.TimeoutError:
        stopped = "timed out"
    except asyncio.CancelledError:
        stopped = "stopped"
    except Exception as e:
        stopped = f"{type(e).__name__}: {e}"
        session.note(traceback.format_exc())
    finally:
        done.set()
        if session.sandbox:
            session.sandbox.close()
        if prompt_file:
            try:
                os.remove(prompt_file)
            except OSError:
                pass
        try:
            game.release_all()
            game.pause(False)
        except Exception:
            pass
    return finish(session, t0, stopped, last_text, agent="claude", model=model, fast=bool(fast_settings(model)),
                  cost_usd=getattr(result_msg, "total_cost_usd", None), turns=getattr(result_msg, "num_turns", None))
