"""The hit-and-run tactic (kit.control.strike_and_retreat, aim_and_strike) on a fake world with a wolf in it."""

import pytest
from conftest import wall_clock
from test_autopilot import FakeLink

from autopilot.kit import Game, control
from autopilot.kit.geom import Vec

# (the wolf bites by the wall clock: a second within reach; hosted macOS runners' timers stretch that)
pytestmark = wall_clock

WOLF = "/Game/M.M:PersistentLevel.Wolf_C_0"
HERO = "/Game/M.M:PersistentLevel.Hero_C_0"


class WolfLink(FakeLink):
    """FakeLink's flat world with a wolf in it: a LeftMouseButton press within 150 of it takes 10 off its Health; it
    bites the player for 20 whenever the player has stayed within 120 of it for over a second. Dead (Health 0) it is
    being destroyed: it no longer describes."""

    def __init__(self, wolf_health=30, player_health=100):
        super().__init__()
        self.wolf = Vec(600, 0, 100)
        self.wolf_health = wolf_health
        self.player_health = player_health
        self.near_for = 0.0
        self.bites = 0
        self.strikes = 0

    def _advance(self):
        before = self.game_time
        super()._advance()
        if self.paused or self.wolf_health <= 0:
            return
        if self.pos.dist2d(self.wolf) < 120:
            self.near_for += self.game_time - before
            if self.near_for > 1.0:
                self.player_health = max(0, self.player_health - 20)
                self.bites += 1
                self.near_for = 0.0
        else:
            self.near_for = 0.0

    def request(self, op, args=None, timeout=None):
        from autopilot.bridge import BridgeError

        args = args or {}
        if op == "key":
            r = super().request(op, args, timeout)
            if args.get("key") == "LeftMouseButton" and args.get("mode") == "press":
                self.strikes += 1
                if self.wolf_health > 0 and self.pos.dist2d(self.wolf) < 150:
                    self.wolf_health = max(0, self.wolf_health - 10)
            return r
        if op == "rc":
            body = args["body"]
            obj, fn, verb = body.get("objectPath") or "", body.get("functionName"), args["verb"]
            if "Wolf" in obj:
                with self.lock:
                    self._advance()
                if verb == "describe":
                    if self.wolf_health > 0:
                        return {"Name": obj, "Class": "x"}
                    raise BridgeError(400, f"Object {obj} does not exist")
                if verb == "property":
                    prop = body.get("propertyName")
                    if prop == "Health":
                        return {"Health": self.wolf_health}
                    if prop == "bActorIsBeingDestroyed":
                        return {"bActorIsBeingDestroyed": self.wolf_health <= 0}
                    raise BridgeError(400, "no property")
                if fn == "K2_GetActorLocation":
                    return {"ReturnValue": self.wolf.ue()}
                if fn == "GetActorBounds":
                    return {"Origin": self.wolf.ue(), "BoxExtent": {"X": 40, "Y": 40, "Z": 40}}
                if fn == "GetVelocity":
                    return {"ReturnValue": {"X": 0, "Y": 0, "Z": 0}}
                raise BridgeError(400, f"no function {fn}")
            if verb == "property" and "Hero" in obj and body.get("propertyName") == "Health":
                with self.lock:
                    self._advance()
                return {"Health": self.player_health}
            if fn == "GetAllActorsOfClass" and "Wolf" in body["parameters"]["ActorClass"]:
                return {"OutActors": [WOLF]}
        return super().request(op, args, timeout)


def make_game(**kw):
    g = Game(WolfLink(**kw))
    g.look = {"x": 0.1, "y": 0.1}
    g.logged = []
    g.log = lambda kind, **fields: g.logged.append((kind, fields))
    return g


@pytest.fixture
def game():
    return make_game()


def tactics_logged(game):
    return [f for kind, f in game.logged if kind == "tactic"]


def player_health(game):
    return lambda: game.get(HERO, "Health")


def test_hit_and_run_wins_untouched(game):
    """Three approaches, one strike each, out of reach before the wolf's bite lands each time."""
    gen = control.strike_and_retreat(game, WOLF, "LeftMouseButton", reach=150, strikes=1, cooldown=0.5, recover=0.5,
                                     health=player_health(game), timeout=30)
    r = control.run(gen, timeout=30, game=game)
    assert r.ok and r.why == "target down", r
    assert r.data["approaches"] == 3 and r.data["strikes"] == 3 and r.data["hits"] == 3, r.data
    assert r.data["damage"] == 0 and r.data["seconds"] > 0, r.data
    assert game.link.player_health >= 60 and game.link.bites == 0
    assert game.link.wolf_health == 0 and not game.exists(WOLF)
    assert game.held() == {}  # (nothing left held)
    logged = tactics_logged(game)
    assert [e["approach"] for e in logged] == [1, 2, 3]
    assert logged[0]["target_health"] == 30 and logged[0]["health"] == 100 and logged[0]["strikes"] == 1
    assert logged[0]["retreat"] >= 450 and not logged[0]["hit"]


def test_too_short_a_retreat_takes_hits(game):
    """Backing off less than the wolf's reach and waiting there is the straight fight again: it bites."""
    gen = control.strike_and_retreat(game, WOLF, "LeftMouseButton", reach=150, retreat=100, recover=1.5,
                                     health=player_health(game), timeout=30)
    r = control.run(gen, timeout=30, game=game)
    assert r.ok, r
    assert game.link.bites >= 1 and game.link.player_health < 100
    assert r.data["damage"] == 100 - game.link.player_health and r.data["damage"] >= 20
    assert any(e["hit"] for e in tactics_logged(game))


def test_backs_off_when_hit_mid_volley(game):
    """A volley long enough for the bite to land: the strikes left are skipped and the player backs off."""
    gen = control.strike_and_retreat(game, WOLF, "LeftMouseButton", reach=150, strikes=4, cooldown=0.6, recover=0.5,
                                     health=player_health(game), timeout=30)
    r = control.run(gen, timeout=30, game=game)
    assert r.ok, r
    first = tactics_logged(game)[0]
    assert first["hit"] and 1 <= first["strikes"] < 4, first
    assert r.data["approaches"] >= 2 and r.data["damage"] >= 20


def test_alive_false_ends_it():
    """alive() is the harness's own notion: false ends the tactic as a win at once, mid-fight."""
    game = make_game()
    gen = control.strike_and_retreat(game, WOLF, "LeftMouseButton", recover=0.5, timeout=30,
                                     alive=lambda: game.link.wolf_health > 10)
    r = control.run(gen, timeout=30, game=game)
    assert r.ok and r.why == "target down", r
    assert r.data["approaches"] == 2 and r.data["strikes"] == 2 and r.data["hits"] == 2, r.data
    assert "damage" not in r.data  # (no health given)
    assert game.link.wolf_health == 10


def test_timeout_is_falsy_with_why():
    game = make_game(wolf_health=1000)
    gen = control.strike_and_retreat(game, WOLF, "LeftMouseButton", recover=0.5, timeout=2)
    r = control.run(gen, timeout=15, game=game)
    assert not r.ok and r.why == "timeout", r
    assert r.data["approaches"] >= 1 and r.data["strikes"] >= 1 and r.data["seconds"] >= 2


def test_player_dead_is_falsy():
    game = make_game(player_health=20)
    gen = control.strike_and_retreat(game, WOLF, "LeftMouseButton", retreat=100, recover=1.5,
                                     health=player_health(game), timeout=30)
    r = control.run(gen, timeout=30, game=game)
    assert not r.ok and r.why == "player dead", r
    assert r.data["damage"] == 20 and game.link.player_health == 0


def test_walk_failure_is_falsy():
    game = make_game()
    game.link.wolf = Vec(60000, 0, 100)  # (too far to reach in the walk's time)
    gen = control.strike_and_retreat(game, WOLF, "LeftMouseButton", timeout=3)
    r = control.run(gen, timeout=15, game=game)
    assert not r.ok and r.why.startswith("can't close in: "), r
    assert r.data["strikes"] == 0 and r.data["approaches"] == 1


def test_aim_and_strike_is_one_volley():
    game = make_game()
    game.link.pos = Vec(500, 0, 100)  # (in reach, facing it)
    r = control.run(control.aim_and_strike(game, WOLF, "LeftMouseButton", strikes=2, cooldown=0.2), timeout=5,
                    game=game)
    assert r.ok and r.data == {"strikes": 2, "aimed": True}, r
    assert game.link.wolf_health == 10 and game.link.strikes == 2
    stopped = control.run(control.aim_and_strike(game, WOLF, "LeftMouseButton", strikes=3, stop=lambda: True),
                          timeout=5, game=game)
    assert not stopped.ok and stopped.data["strikes"] == 0
