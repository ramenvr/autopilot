"""autopilot/kit/harness.py's check_harness: a harness file a run would refuse, caught before an episode pays for it."""

import textwrap

from autopilot.kit import check_harness


def write(tmp_path, source, name="harness.py"):
    path = tmp_path / name
    path.write_text(textwrap.dedent(source), encoding="utf-8")
    return str(path)


GOOD = """
    from autopilot.kit import Harness, Option, Status

    def go(game, obs):
        yield

    class Go(Harness):
        goal = "walk to the thing"
        tempo = 2.0
        tick_hz = 15.0

        def observe(self, game):
            return {}

        def options(self, obs):
            return [Option("go", "walk", go)]

        def status(self, obs):
            return Status()
"""


def test_a_harness_the_runtime_takes_has_no_problems(tmp_path):
    ok, problems = check_harness(write(tmp_path, GOOD))
    assert ok and problems == []


def test_a_missing_file_is_one_problem_not_a_crash(tmp_path):
    ok, problems = check_harness(str(tmp_path / "nope.py"))
    assert not ok and len(problems) == 1 and "no such file" in problems[0]


def test_a_syntax_error_points_at_its_line(tmp_path):
    ok, problems = check_harness(write(tmp_path, "class Broken(\n"))
    assert not ok and problems[0].startswith("line ")


def test_a_file_without_a_harness_subclass_says_so(tmp_path):
    ok, problems = check_harness(write(tmp_path, "VALUE = 1\n"))
    assert not ok and "no Harness subclass" in problems[0]


def test_an_empty_goal_and_a_stopped_clock_are_reported(tmp_path):
    broken = GOOD.replace('goal = "walk to the thing"', 'goal = "" ').replace("tempo = 2.0", "tempo = 0.0").replace(
        "tick_hz = 15.0", "tick_hz = 0.0")
    ok, problems = check_harness(write(tmp_path, broken))
    assert not ok
    assert any("empty goal" in p for p in problems)
    assert any("tempo and tick_hz" in p for p in problems)


def test_a_harness_importing_its_neighbour_finds_it(tmp_path):
    write(tmp_path, "TICKS = 3\n", name="tuning.py")
    body = textwrap.dedent(GOOD).replace("    def go(game, obs):\n    yield\n",
                                         "    def go(game, obs):\n        for _ in range(tuning.TICKS):\n            yield\n")
    ok, problems = check_harness(write(tmp_path, "import tuning\n\n" + body))
    assert ok and problems == [], problems


def test_the_check_leaves_sys_path_as_it_found_it(tmp_path):
    import sys

    before = list(sys.path)
    check_harness(write(tmp_path, GOOD))
    assert sys.path == before
