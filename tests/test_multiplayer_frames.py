"""Several players read in one game frame (kit.together, Game.each_player, Game.poses).

The plugins answer every request that came in since the last frame in that frame (Unreal: PlaytestSubsystem Tick),
so reads sent together cost one frame and reads sent one after another cost a frame each. A harness that steps player
1 and then player 2 waits for every one of player 1's reads before player 2's start: twice the frames a tick, half the
ticks a second. tests/framesim.py is a link that answers in frames and logs which frame answered each request.

Reads sent together from several threads can straddle a frame's edge by chance (one thread starts a little late), as
they can in a real game. So a check that reads share one frame takes the best of three tries: reads sent one after
another never share a frame, on any try. Wait counts don't depend on timing and are checked on every try.
"""

import os
import threading

import pytest
from framesim import FrameLink

from autopilot.kit import Game

TRIES = 3


def warm(*games):
    """Read each player's context now (cached for half a second): the reads that follow are the pose's alone."""
    for g in games:
        g.context(fresh=True)


def in_time(fn, seconds=2.0, on_timeout=None):
    """fn() in a thread; its result, or a failure when it hasn't finished in `seconds` (a starved pool never does).
    on_timeout: what lets the stuck threads end first (else pytest would hang at exit, waiting for them)."""
    box = {}

    def go():
        try:
            box["value"] = fn()
        except BaseException as e:  # (raised in the test's thread below)
            box["error"] = e

    t = threading.Thread(target=go, daemon=True)
    t.start()
    t.join(seconds)
    if t.is_alive() and on_timeout:
        on_timeout()
    assert not t.is_alive(), f"didn't finish in {seconds} s"
    if "error" in box:
        raise box["error"]
    return box["value"]


def drive(gen, limit=50):
    """Step a generator to its end, as the runtime steps an executor: (steps it yielded, what it returned)."""
    steps = 0
    try:
        while steps < limit:
            next(gen)
            steps += 1
    except StopIteration as s:
        return steps, s.value
    raise AssertionError(f"still going after {limit} steps")


def reader(game, reads=3, steps=2):
    """An executor that reads its player's position `reads` times one after another each step."""
    for _ in range(steps):
        for _ in range(reads):
            game.me()
        yield
    return game.index


# -- the cause, as the fake game shows it (these pass before the fix) -----------------------------------------------

def test_reads_sent_one_after_another_take_a_frame_each_and_reads_sent_together_share_one():
    """The root cause in small: player 1's pose, then player 2's, is two frames and two waits; their reads in one
    game.batch share a frame."""
    with FrameLink(players=2) as link:
        game = Game(link)
        two = game.player(2)
        warm(game, two)
        m, w = link.mark(), game.waits()
        game.pose()
        two.pose()
        assert game.waits() - w == 2
        assert len(link.frames(link.since(m))) == 2
        shared = []
        for _ in range(TRIES):
            m = link.mark()
            game.batch([lambda: game.me(), lambda: two.me()])
            answers = link.since(m)
            assert {a.player for a in answers} == {1, 2}
            shared.append(len(link.frames(answers)))
        assert min(shared) == 1, shared


# -- each_player / poses ----------------------------------------------------------------------------------------------

def test_each_player_reads_every_players_pose_in_one_frame_and_one_wait():
    with FrameLink(players=2) as link:
        game = Game(link)
        two = game.player(2)
        frames = []
        for _ in range(TRIES):
            warm(game, two)
            m, w = link.mark(), game.waits()
            poses = game.each_player(lambda p: p.pose())
            assert game.waits() - w == 1
            assert sorted(poses) == [1, 2]
            assert poses[1]["at"].x == 100 and poses[2]["at"].x == 200 and poses[2]["yaw"] == 180
            answers = link.since(m)
            assert len(answers) == 8 and {a.player for a in answers} == {1, 2}  # (4 reads a pose)
            frames.append(len(link.frames(answers)))
        assert min(frames) == 1, frames
        # poses() is each_player(lambda p: p.pose()); players= picks some of them
        warm(game, two)
        w = game.waits()
        poses = game.poses()
        assert sorted(poses) == [1, 2] and poses[2]["at"].x == 200 and game.waits() - w == 1
        assert sorted(game.poses(players=[2])) == [2]
        assert game.each_player(lambda p: p.index, players=[2, 1]) == {2: 2, 1: 1}


def test_each_player_inside_each_player_never_starves_the_pool():
    """A pose() is a batch itself, run from the batch each_player makes: four players' poses still go out in one
    frame, and twenty players' (more than the game's pool has threads) still finish."""
    with FrameLink(players=4) as link:
        game = Game(link)
        bodies = [game.player(n) for n in range(1, 5)]
        frames = []
        for _ in range(TRIES):
            warm(*bodies)
            m = link.mark()
            poses = in_time(lambda: game.each_player(lambda p: p.pose()))
            assert sorted(poses) == [1, 2, 3, 4]
            answers = link.since(m)
            assert len(answers) == 16
            frames.append(len(link.frames(answers)))
        assert min(frames) == 1, frames
    with FrameLink(players=20) as link:
        game = Game(link)
        # (contexts not read yet: each pose reads its own first. Stuck: the game's queued reads are cancelled)
        poses = in_time(lambda: game.poses(), on_timeout=lambda: game._pool.shutdown(wait=False, cancel_futures=True))
        assert sorted(poses) == list(range(1, 21))
        assert poses[20]["at"].x == 2000


def test_one_player_costs_what_it_did():
    """A single-player game: pose() is one wait, an empty batch none, and each_player plays player 1 alone."""
    with FrameLink(players=1, features=()) as link:
        game = Game(link)
        frames = []
        for _ in range(TRIES):
            warm(game)
            m, w = link.mark(), game.waits()
            game.pose()
            assert game.waits() - w == 1
            frames.append(len(link.frames(link.since(m))))
        assert min(frames) == 1, frames
        w = game.waits()
        assert game.batch([]) == [] and game.waits() == w
        warm(game)
        w = game.waits()
        poses = game.each_player(lambda p: p.pose())
        assert sorted(poses) == [1] and poses[1]["at"].x == 100 and game.waits() - w == 1


# -- together ---------------------------------------------------------------------------------------------------------

def test_together_steps_every_player_at_once():
    """Two executors that read three times one after another each step: stepped together, the step waits three
    times, not six, and their reads share three frames."""
    from autopilot.kit import together

    with FrameLink(players=2) as link:
        game = Game(link)
        two = game.player(2)
        frames = []
        for _ in range(TRIES):
            warm(game, two)
            gen = together([reader(game), reader(two)])
            m, w = link.mark(), game.waits()
            next(gen)
            assert game.waits() - w == 3
            answers = link.since(m)
            assert len(answers) == 6 and {a.player for a in answers} == {1, 2}
            frames.append(len(link.frames(answers)))
            gen.close()
        assert min(frames) == 3, frames


def test_together_returns_every_result_in_order_and_steps_until_the_longest_ends():
    from autopilot.kit import together

    def finishes(game, after, value, log):
        for _ in range(after):
            log.append(value)
            yield
        log.append(value + " ends")
        return value

    with FrameLink(players=2) as link:
        game = Game(link)
        log = []
        steps, out = drive(together([finishes(game, 3, "long", log), finishes(game.player(2), 1, "short", log)]))
        assert out == ["long", "short"]
        assert steps == 3  # (as many steps as the longest yields)
        assert log.count("short") == 1 and log.count("short ends") == 1
        assert log.count("long") == 3 and log.count("long ends") == 1
        # inside an executor: yield from
        def executor(game):
            return (yield from together([reader(game, reads=1, steps=2), reader(game.player(2), reads=1, steps=1)]))
        steps, out = drive(executor(game))
        assert out == [1, 2] and steps == 2
        assert drive(together([])) == (0, [])


def test_together_raises_what_one_executor_raised():
    from autopilot.kit import together

    def falls(game, after):
        for _ in range(after):
            yield
        raise ValueError("player 2 fell off the deck")

    with FrameLink(players=2) as link:
        game = Game(link)
        with pytest.raises(ValueError, match="fell off the deck"):
            drive(together([reader(game, reads=1, steps=5), falls(game.player(2), 1)]))


# -- what the agent is told ----------------------------------------------------------------------------------------

def test_the_agent_is_told_to_drive_and_read_players_together():
    """KIT.md and the multiplayer note of a game with two players name together and each_player; a one-player
    game's note doesn't."""
    from autopilot.agents import session

    kit = os.path.join(os.path.dirname(session.__file__), "..", "KIT.md")
    with open(kit, encoding="utf-8") as f:
        text = f.read()
    assert "together(" in text and "each_player(" in text
    for net in ({"mode": "listen_server", "players": 2, "player": 1},
                {"mode": "client", "server_world": "/Game/UEDPIE_0_Map.Map", "clients": 2, "players": 2}):
        note = session.network_note(net)
        assert "together(" in note and "each_player(" in note, note
    for net in ({"mode": "listen_server"}, {"mode": "listen_server", "players": 1}):
        assert "together(" not in session.network_note(net)


def kit_example(name):
    """The code of KIT.md's example `def name(...)` (indented under its paragraph), dedented."""
    import textwrap

    from autopilot.agents import session

    with open(os.path.join(os.path.dirname(session.__file__), "..", "KIT.md"), encoding="utf-8") as f:
        lines = f.read().splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip().startswith(f"def {name}("))
    indent = len(lines[start]) - len(lines[start].lstrip())
    block = [lines[start]]
    for line in lines[start + 1:]:
        if line.strip() and len(line) - len(line.lstrip()) <= indent:
            break
        block.append(line)
    return textwrap.dedent("\n".join(block))


def run_meet(game):
    """KIT.md's `meet` example run on game with a walk_to that records its target: (steps, Result, targets)."""
    import autopilot.kit as kit
    from autopilot.kit import Result, Vec

    targets = {}

    def walk_to(game, target, within=120.0, **_):
        targets[game.index] = Vec.of(target)
        yield
        return Result(True, "there")

    scope = {**vars(kit), "walk_to": walk_to}
    exec(kit_example("meet"), scope)
    steps, out = drive(scope["meet"](game))
    return steps, out, targets


def test_kit_md_meet_example_walks_both_players_to_one_point():
    """Both players walk to the same point between them: walking each to the other's starting spot (a Vec target
    is fixed) only swaps them, and both would report there."""
    with FrameLink(players=2) as link:
        steps, out, targets = run_meet(Game(link))
        assert out and out.why == "met" and steps == 1
        assert sorted(targets) == [1, 2]
        assert targets[1].dist(targets[2]) < 1.0, targets  # (one point: players 1 and 2 stand at x = 100 and 200)
        assert 100.0 < targets[1].x < 200.0, targets


def test_kit_md_meet_example_says_when_a_players_pose_failed():
    """A player whose pose failed (no pawn, respawning) comes back from poses() as its GameError, as in batch: the
    example ends with a failed Result saying so, not a TypeError from indexing the error."""
    from autopilot.kit import GameError

    def no_pawn():
        raise GameError("pose: player 2 has no pawn")

    with FrameLink(players=2) as link:
        game = Game(link)
        game.player(2).pose = no_pawn
        steps, out, targets = run_meet(game)
        assert not out and "no pawn" in out.why and targets == {}


def test_a_players_sequence_holds_are_its_own():
    """Each player's sequences and the keys they hold are its own: player 1's release(key) doesn't let go of a key
    player 2's sequence holds (nor post a release for player 1), and player 2's release_all doesn't drop player 1's
    sequences (player 1 still renews it). Shared, the players' steps in together's threads raced on one dict."""
    with FrameLink(players=2, features=("players", "sequence")) as link:
        game = Game(link)
        two = game.player(2)
        mine = game.sequence([{"key": "D", "mode": "hold"}])
        theirs = two.sequence([{"key": "W", "mode": "hold"}])
        assert mine and theirs and mine != theirs
        n = len(link.posts)
        game.release("W")  # (player 1 holds no W)
        assert link.posts[n:] == []
        two.release_all()
        n = len(link.posts)
        game.renew()
        renewed = [a["ids"] for op, a in link.posts[n:] if op == "sequence" and a.get("action") == "renew"
                   and "player" not in a]
        assert renewed == [[mine]], link.posts[n:]
