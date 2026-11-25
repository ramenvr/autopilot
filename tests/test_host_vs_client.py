"""Comparing host and client: the same object read on every world in one game frame (Game.read_worlds), and the same
object found in another world (Game.counterpart).

Each Play In Editor world (the listen server's, each client's) has its own copy of a replicated actor, under its own
path (the world's PIE prefix), so a harness can't just read one path on every player. And reads sent one after another
come from different frames, so a gap between host and client can be sampling, not desync. Unreal's plugin answers
`counterpart` (the actor's path in another world) and `read_worlds` (the same fields of an actor on every world, in one
game-thread call, with the engine frame it ran in); its hello's features say "worlds". A plugin without them: the kit
reads every player together (each_player: one wait, likely one frame, not promised) and says so (same_frame None).

tests/framesim.py is a link that answers in frames; the link here adds the two ops and gives each player its own world.
"""

import os

import pytest
from framesim import FrameLink

from autopilot.kit import Game, GameError, Vec

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def world(n):
    """Player n's PIE world (player 1: the listen server's)."""
    return f"/Game/UEDPIE_{n - 1}_Sim.Sim"


DOOR = f"{world(1)}:PersistentLevel.Door_3"  # a replicated actor, as the server sees it
GHOST = f"{world(1)}:PersistentLevel.Ghost_1"  # an actor that isn't replicated: no counterpart anywhere


class WorldsLink(FrameLink):
    """A FrameLink whose players each play their own world, and (features with "worlds") a plugin that answers
    `counterpart` and `read_worlds`. Numbers come back as JSON numbers may (2.0 for 2)."""

    def __init__(self, players=2, features=("players", "worlds")):
        super().__init__(players=players, features=features)

    def _answer(self, op, args):
        from autopilot.bridge import BridgeError

        n = int(args.get("player", 1))
        if op == "context":
            ctx = super()._answer(op, args)
            level = f"{world(n)}:PersistentLevel."
            ctx.update(world=world(n), character=f"{level}Hero_C_{n}", controller=f"{level}PC_{n}",
                       camera_manager=f"{level}PCM_{n}")
            return ctx
        if op in ("counterpart", "read_worlds") and "worlds" not in self.hello["features"]:
            raise BridgeError(400, f"Unknown op {op}")
        if op == "counterpart":
            if "Ghost" in args["path"]:
                return {"path": "", "how": "none", "why": "not replicated"}
            to = args["to"]
            n = 1 if to == "server" else int(to)
            return {"path": args["path"].replace(world(1), world(n)), "how": "guid"}
        if op == "read_worlds":
            out = []
            for w in args.get("worlds") or [*range(1, self.players + 1), "server"]:
                k = 1 if w == "server" else int(w)
                values = {}
                for f in args["fields"]:
                    values[f] = {"X": 10.0 * k, "Y": 0.0, "Z": 0.0} if f == "location" else 100.0 - k
                if "Ghost" in args["path"] and k > 1:  # (no copy on a client: the plugin says why, values empty)
                    out.append({"world": float(w), "net_mode": "client", "time": self.game_time, "path": "",
                                "how": "none", "values": {}, "error": "Ghost_1 isn't replicated: no copy there"})
                    continue
                out.append({"world": w if w == "server" else float(w),
                            "net_mode": "client" if k > 1 else "listen_server", "time": self.game_time,
                            "path": args["path"].replace(world(1), world(k)), "how": "guid", "values": values})
            return {"frame": self.frame, "worlds": out}
        return super()._answer(op, args)


def warm(*games):
    """Read each player's context now (cached for half a second): the reads that follow are the test's alone."""
    for g in games:
        g.context(fresh=True)


# -- counterpart: the same actor in another world -------------------------------------------------------------------

def test_counterpart_asks_the_plugin_for_the_actor_in_another_world_and_returns_its_path():
    with WorldsLink() as link:
        game = Game(link)
        mark = link.mark()
        assert game.counterpart(DOOR, player=2) == f"{world(2)}:PersistentLevel.Door_3"
        assert game.counterpart(DOOR, player="server") == DOOR
        sent = link.since(mark)
        assert [(a.op, a.args) for a in sent] == [("counterpart", {"path": DOOR, "to": 2}),
                                                  ("counterpart", {"path": DOOR, "to": "server"})]


def test_an_actor_with_no_counterpart_is_none():
    """`how: "none"` (not replicated, not replicated to that client yet, destroyed): None, never another object."""
    with WorldsLink() as link:
        game = Game(link)
        assert game.counterpart(GHOST, player=2) is None


# -- read_worlds: the same fields on every world in one call ----------------------------------------------------------

def test_read_worlds_is_one_request_answered_in_one_frame_for_every_world():
    with WorldsLink() as link:
        game = Game(link)
        warm(game)
        mark, waited = link.mark(), game.waits()
        out = game.read_worlds(DOOR, ["location", "Health"], worlds=[1, 2, "server"])
        sent = link.since(mark)
        assert [(a.op, a.args) for a in sent] == [
            ("read_worlds", {"path": DOOR, "fields": ["location", "Health"], "worlds": [1, 2, "server"]})]
        assert game.waits() - waited == 1
        # The frame the plugin read every world in, and that it promises they share it
        assert out["frame"] == sent[0].frame and out["same_frame"] is True
        by = out["by_world"]
        assert set(by) == {1, 2, "server"}  # (2.0 on the wire: the kit's key is 2)
        assert by[2]["path"] == f"{world(2)}:PersistentLevel.Door_3" and by[2]["how"] == "guid"
        assert by[2]["net_mode"] == "client" and by["server"]["net_mode"] == "listen_server"
        # Positions in the kit's frame (a Vec), other fields as the plugin read them
        assert by[2]["values"]["location"] == Vec(20.0, 0.0, 0.0) and by[2]["values"]["Health"] == 98.0
        assert by[1]["values"]["location"] == Vec(10.0, 0.0, 0.0)


def test_a_world_the_plugin_couldnt_read_holds_its_game_error():
    """The plugin answers a world it couldn't read (no copy there, a property the actor hasn't) with its "error": that
    world's entry is a GameError with the plugin's words (as KIT.md says), the other worlds read as usual."""
    with WorldsLink() as link:
        game = Game(link)
        by = game.read_worlds(GHOST, ["location"], worlds=[1, 2])["by_world"]
        assert isinstance(by[2], GameError) and "isn't replicated" in str(by[2])
        assert by[1]["values"]["location"] == Vec(10.0, 0.0, 0.0)


def test_a_plugin_without_worlds_reads_every_player_together_and_promises_no_frame():
    """An older plugin (no "worlds" in its features): no read_worlds request; each player reads its own copy (the same
    name in its own world) and the players read together (each_player: one wait). Likely one frame, not promised:
    same_frame None, frame None."""
    with WorldsLink(features=("players",)) as link:
        game = Game(link)
        two = game.player(2)
        warm(game, two)
        mark, waited = link.mark(), game.waits()
        out = game.read_worlds(DOOR, ["location", "Health"])
        sent = link.since(mark)
        assert "read_worlds" not in {a.op for a in sent}
        assert game.waits() - waited == 1
        assert out["same_frame"] is None and out["frame"] is None
        by = out["by_world"]
        assert set(by) == {1, 2}  # (every player: the server isn't one a player reads from)
        assert by[2]["path"] == f"{world(2)}:PersistentLevel.Door_3" and by[2]["how"] == "name"
        assert by[2]["values"]["location"] == Vec(200.0, 0.0, 90.0) and by[1]["values"]["location"] == Vec(
            100.0, 0.0, 90.0)
        # Player 2 read its own world's copy, as player 2
        read = {(a.player, (a.args.get("body") or {}).get("objectPath")) for a in sent if a.op == "rc"}
        assert (2, f"{world(2)}:PersistentLevel.Door_3") in read and (1, DOOR) in read


def test_a_plugin_without_worlds_cant_find_counterparts_or_read_the_server():
    """What only the plugin can do says so (update the plugin), rather than guess."""
    with WorldsLink(features=("players",)) as link:
        game = Game(link)
        mark = link.mark()
        with pytest.raises(GameError, match="worlds"):
            game.counterpart(DOOR, player=2)
        with pytest.raises(GameError, match="worlds"):
            game.read_worlds(DOOR, ["location"], worlds=[1, "server"])
        assert link.since(mark) == []


# -- other engines ----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("engine, title", [("unity", "Unity"), ("godot", "Godot"), ("roblox", "Roblox"),
                                           ("roblox-mcp", "Roblox")])
def test_other_engines_say_they_cant_compare_worlds_yet(engine, title):
    """Unreal only for now: another engine's game raises a GameError that names the engine, and sends nothing."""
    with WorldsLink() as link:
        link.hello["engine"] = engine
        game = Game(link)
        mark = link.mark()
        for ask in (lambda: game.counterpart(DOOR, player=2), lambda: game.read_worlds(DOOR, ["location"])):
            with pytest.raises(GameError, match=title):
                ask()
        assert link.since(mark) == []


# -- the docs ---------------------------------------------------------------------------------------------------------

def test_kit_md_teaches_comparing_host_and_client():
    kit = open(os.path.join(ROOT, "autopilot", "KIT.md"), encoding="utf-8").read()
    assert "## Comparing host and client" in kit
    section = kit[kit.index("## Comparing host and client"):]
    section = section[:section.index("\n## ", 1)] if "\n## " in section[1:] else section
    for word in ("game.read_worlds(", "game.counterpart(", "same_frame", "lag", "desync"):
        assert word in section, word


def test_the_multiplayer_note_points_at_read_worlds_when_there_is_a_client_to_compare():
    """The opening's network line names read_worlds when the editor plays a host and a client (a listen server with
    players, a client with its server's world here); not for one player, nor for a server in another process."""
    from autopilot.agents import session

    for net in ({"mode": "listen_server", "players": 2, "player": 1},
                {"mode": "client", "server_world": "/Game/UEDPIE_0_Map.Map", "clients": 2, "players": 2}):
        assert "game.read_worlds" in session.network_note(net), net
    for net in ({"mode": "listen_server"}, {"mode": "client", "server_world": ""},
                {"mode": "client", "rc_side": "server", "clients": 2}):
        assert "read_worlds" not in session.network_note(net), net
