"""Workflows (autopilot/workflows.py): the playbook for a run's kind in the briefing, the package's by default, a
project's own instead, and a project's all.md added to every kind's."""

import os

from autopilot import workflows


def test_each_kind_has_a_default_and_an_unknown_kind_gets_the_goals():
    for kind in workflows.KINDS:
        text = workflows.text(kind)
        assert text.startswith("## The workflow for this request") and "Autopilot's default" in text, kind
    assert "A goal" in workflows.text("something else")
    goal = workflows.text("goal")
    assert "What the picture showed" in goal and "twice" in goal  # (the revise rule: a cause seen twice is fixed)
    assert "describe" in workflows.text("verify") and "finding" in workflows.text("explore").lower()


def test_a_projects_own_workflow_replaces_the_default_and_all_md_is_added(tmp_path):
    root = str(tmp_path)
    folder = os.path.join(root, workflows.PROJECT_FOLDER)
    os.makedirs(folder)
    with open(os.path.join(folder, "goal.md"), "w", encoding="utf-8") as f:
        f.write("1. Our own goal playbook.\n")
    with open(os.path.join(folder, "all.md"), "w", encoding="utf-8") as f:
        f.write("- Always read the HUD after a death.\n")
    text = workflows.text("goal", root)
    assert "Our own goal playbook" in text and "Autopilot's default" not in text
    assert "this game's workflows/goal.md" in text and "Always read the HUD" in text
    verify = workflows.text("verify", root)
    assert "Autopilot's default for a verify" in verify and "Always read the HUD" in verify
    assert [os.path.basename(p) for p in workflows.files("goal", root)] == ["goal.md", "all.md"]


def test_the_briefing_carries_the_workflow(tmp_path, monkeypatch):
    from test_autopilot import FakeLink

    from autopilot import codex
    from autopilot.agents import session as sess
    from autopilot.kit.game import Game

    monkeypatch.setenv("AUTOPILOT_SANDBOX", "off")
    monkeypatch.setattr(codex, "MEMORY", str(tmp_path))
    s = sess.Session(Game(FakeLink()), "fake", "Open the crate and reach the roof", "r1")
    text = sess.briefing(s)
    assert "## The workflow for this request" in text and "A goal" in text
    s2 = sess.Session(Game(FakeLink()), "fake", "Verify that the crate opens with E", "r2")
    assert "A claim to verify" in sess.briefing(s2)
