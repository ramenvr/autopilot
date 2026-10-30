"""`autopilot connect claude|codex` (autopilot/connect.py): the user's own Claude Code or Codex set up for Autopilot and
taken out again (also by `autopilot uninstall`), never touching what is the user's own; and the Claude Code plugin in
the repository, as connect writes it. Every test's Claude Code and Codex homes are folders of its own, and `claude`
is never run (conftest.py no_real_assistants)."""

import json
import os
import sys
import tomllib

from autopilot import connect, uninstall

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

USERS_CONFIG = """model = "gpt-6.1-sol"

[desktop]
theme = "dark"

[plugins."browser@openai-bundled"]
enabled = true

[mcp_servers.other]
command = "npx"
args = ["other-mcp"]

[mcp_servers.autopilot]
command = "old-autopilot"

[mcp_servers.autopilot.env]
X = "1"

[profiles.fast]
model = "small"
"""


def test_codex_is_set_up_beside_the_users_own_config_and_taken_out_again(no_real_assistants):
    home = no_real_assistants["home"]
    config = home / "codex" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text(USERS_CONFIG, encoding="utf-8")
    said = []
    assert connect.connect("codex", say=said.append) == 0
    parsed = tomllib.loads(config.read_text(encoding="utf-8"))
    ours = parsed["mcp_servers"]["autopilot"]
    assert ours == {"command": sys.executable, "args": ["-m", "autopilot", "mcp"],
                    "default_tools_approval_mode": "approve"}  # (the old one replaced whole: its env gone too)
    assert parsed["mcp_servers"]["other"]["command"] == "npx" and parsed["desktop"]["theme"] == "dark"
    assert parsed["plugins"]["browser@openai-bundled"]["enabled"] and parsed["profiles"]["fast"]["model"] == "small"
    assert (home / "codex" / "config.toml.before-autopilot").read_text(encoding="utf-8") == USERS_CONFIG
    describer = tomllib.loads((home / "codex" / "agents" / "frame_describer.toml").read_text(encoding="utf-8"))
    assert describer["name"] == "frame_describer" and "never the claim" in describer["description"]
    assert "You describe frames" in describer["developer_instructions"] and "model" not in describer
    skill = (home / "agents" / "skills" / "autopilot" / "SKILL.md").read_text(encoding="utf-8")
    assert skill.startswith("---\nname: autopilot\n") and "start_run" in skill and connect.MARK in skill
    assert any("Codex is set up for Autopilot" in s for s in said)

    assert connect.connect("codex", say=said.append) == 0  # (again: the same, once)
    assert config.read_text(encoding="utf-8").count("[mcp_servers.autopilot]") == 1

    assert connect.disconnect("codex", say=said.append) == 0
    left = tomllib.loads(config.read_text(encoding="utf-8"))
    assert "autopilot" not in left["mcp_servers"] and left["mcp_servers"]["other"]["args"] == ["other-mcp"]
    assert left["profiles"]["fast"]["model"] == "small" and connect.MARK not in config.read_text(encoding="utf-8")
    assert not (home / "codex" / "agents" / "frame_describer.toml").exists()
    assert not (home / "agents" / "skills" / "autopilot").exists()


def test_the_users_own_files_and_servers_of_those_names_are_left_alone(no_real_assistants):
    home = no_real_assistants["home"]
    own = home / "codex" / "agents" / "frame_describer.toml"
    own.parent.mkdir(parents=True)
    own.write_text('name = "frame_describer"\ndescription = "mine"\ndeveloper_instructions = "mine"\n')
    said = []
    assert connect.connect("codex", say=said.append) == 3
    assert "mine" in own.read_text() and any("isn't Autopilot's" in s for s in said)
    connect.disconnect("codex", say=said.append)
    assert own.exists()

    # Claude Code: a server called autopilot that is the user's own stays
    no_real_assistants["answers"]["claude mcp get"] = (0, "autopilot:\n  Command: npx\n  Args: some-other-mcp\n")
    said.clear()
    assert connect.disconnect("claude", say=said.append) == 3
    assert not [c for c in no_real_assistants["ran"] if c[:3] == ["claude", "mcp", "remove"]]
    assert any("isn't Autopilot's" in s for s in said)


def test_claude_code_gets_the_server_the_skill_and_the_describer(no_real_assistants):
    home = no_real_assistants["home"]
    answers = no_real_assistants["answers"]
    answers["claude mcp get"] = (1, 'No MCP server named "autopilot".')
    answers["claude mcp add"] = (0, "Added stdio MCP server autopilot")
    said = []
    assert connect.connect("claude", say=said.append) == 0
    add = [c for c in no_real_assistants["ran"] if c[:3] == ["claude", "mcp", "add"]][0]
    assert add[3:] == ["--scope", "user", "autopilot", "--", sys.executable, "-m", "autopilot", "mcp"]
    agent = (home / "claude" / "agents" / "frame-describer.md").read_text(encoding="utf-8")
    assert "tools: mcp__autopilot__frames\nmodel: opus\n" in agent and 'description: "Describes' in agent
    assert (home / "claude" / "skills" / "autopilot" / "SKILL.md").exists()

    answers["claude mcp get"] = (0, f"autopilot:\n  Command: {sys.executable}\n  Args: -m autopilot mcp\n")
    assert connect.disconnect("claude", say=said.append) == 0
    assert ["claude", "mcp", "remove", "--scope", "user", "autopilot"] in no_real_assistants["ran"]
    assert not (home / "claude" / "agents" / "frame-describer.md").exists()

    answers.clear()  # (no `claude` command on this computer: how to add it by hand)
    said.clear()
    assert connect.connect("claude", say=said.append) == 3
    assert any("claude mcp add --scope user autopilot --" in s for s in said)


def test_connect_says_where_the_decision_models_key_goes(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.delenv("AUTOPILOT_AURA_URL", raising=False)
    said = []
    connect.connect("codex", say=said.append)
    assert said[-1].startswith("Before the first run:") and "TYPESAFE_API_KEY=" in said[-1]
    monkeypatch.setenv("TYPESAFE_API_KEY", "ts-test")
    said.clear()
    connect.connect("codex", say=said.append)
    assert not said[-1].startswith("Before the first run:")


def test_uninstall_takes_out_what_connect_set_up(tmp_path, no_real_assistants):
    home = no_real_assistants["home"]
    connect.connect("codex", say=lambda _: None)
    folder = tmp_path / "Autopilot"
    folder.mkdir()
    said = []
    assert uninstall.uninstall(str(folder), exe=None, say=said.append) == 0
    assert "autopilot" not in tomllib.loads((home / "codex" / "config.toml").read_text()).get("mcp_servers", {})
    assert not (home / "agents" / "skills" / "autopilot").exists()


def test_the_claude_code_plugin_in_the_repository_is_what_connect_writes():
    folder = os.path.join(ROOT, "plugin", "claude-code")
    for rel, text in connect.plugin_files().items():
        with open(os.path.join(folder, *rel.split("/")), encoding="utf-8") as f:
            assert f.read() == text, f"{rel} differs: python -m autopilot.connect plugin plugin/claude-code"
    with open(os.path.join(ROOT, ".claude-plugin", "marketplace.json"), encoding="utf-8") as f:
        market = json.load(f)
    assert market["plugins"][0]["name"] == "autopilot" and market["plugins"][0]["source"] == "./plugin/claude-code"
    files = connect.plugin_files()
    assert json.loads(files[".mcp.json"])["mcpServers"]["autopilot"] == {"command": "autopilot", "args": ["mcp"]}
    assert "tools: mcp__plugin_autopilot_autopilot__frames" in files["agents/frame-describer.md"]
