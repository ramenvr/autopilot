"""The research helpers probes get (autopilot/research.py): the codex's data tables and grep on a made-up codex, and
the game helpers (nearest, teleport, fight_trial, call_any) on a fake Unreal arena over the real Game, plus the
Unity/Godot teleport paths on duck-typed games. No live game."""

import math
import threading
import time

import pytest
from conftest import wall_clock

from autopilot import engines, research
from autopilot.kit.game import Game, GameError
from autopilot.kit.geom import Vec

ENEMIES = ('---,Health,Damage,Drops,Spawn,Leader\n'
           '"Goblin",30,5,"((Item=""Sword"",Chance=0.5),(Item=""Gold"",Chance=1))","(X=100,Y=200,Z=0)",False\n'
           '"Orc Chief",120,20,"()","(X=1000,Y=-50,Z=0)",True\n'
           '"Wolf",45,8,"((Item=""Pelt"",Chance=0.25))","(X=0,Y=0,Z=0)",False\n')


@pytest.fixture
def codex(tmp_path):
    root = tmp_path / "memory" / "Arena" / "codex"
    (root / "data").mkdir(parents=True)
    (root / "blueprints").mkdir()
    (root / "data" / "DT_Enemies.csv").write_text(ENEMIES, encoding="utf-8")
    (root / "data" / "DT_Enemies.rowstruct.txt").write_text(
        "DataTable /Game/Data/DT_Enemies rows are /Game/Data/S_Enemy.S_Enemy\n", encoding="utf-8")
    (root / "data" / "CT_Scaling.csv").write_text("---,1,2\nHealth,1.0,1.5\n", encoding="utf-8")
    (root / "blueprints" / "BP_Goblin.txt").write_text(
        "Blueprint /Game/BP_Goblin (parent: Character)\nVariables:\n  Health: float = 30\n"
        "Graph EventGraph:\n  Event AnyDamage(Damage):\n    Set Health(Health = Subtract(Health, Damage))\n",
        encoding="utf-8")
    (root / "README.md").write_text("# Codex of Arena\n", encoding="utf-8")
    return str(root)


# -- the codex ---------------------------------------------------------------------------------------------

def test_tables_rows_and_row(codex):
    found = research.tables(codex)
    assert [(t["name"], t["rows"], t["struct"]) for t in found] == [
        ("CT_Scaling", 1, None), ("DT_Enemies", 3, "/Game/Data/S_Enemy.S_Enemy")]
    assert found[1]["path"] == "data/DT_Enemies.csv"
    all_rows = research.rows("DT_Enemies", root=codex)
    assert [r["Name"] for r in all_rows] == ["Goblin", "Orc Chief", "Wolf"]
    assert all_rows[0]["Health"] == 30 and all_rows[0]["Leader"] is False and all_rows[1]["Leader"] is True
    assert all_rows[0]["Spawn"] == "(X=100,Y=200,Z=0)"  # (a struct stays text)
    assert [r["Name"] for r in research.rows("DT_Enemies", where="l", root=codex)] == ["Goblin", "Wolf"]
    assert [r["Name"] for r in research.rows("DT_Enemies", where="^Orc", root=codex)] == ["Orc Chief"]
    assert [r["Name"] for r in research.rows("dt_enemies", where={"Drops": "Gold"}, root=codex)] == ["Goblin"]
    assert [r["Name"] for r in research.rows("data/DT_Enemies.csv", where="(", root=codex)] == []  # a bad regex: text
    assert research.row("DT_Enemies", "wolf", root=codex)["Damage"] == 8
    with pytest.raises(KeyError, match="close: Orc Chief"):
        research.row("DT_Enemies", "Orc", root=codex)
    with pytest.raises(FileNotFoundError, match="CT_Scaling, DT_Enemies"):
        research.rows("DT_Nothing", root=codex)


def test_cell_reads_nested_structs(codex):
    goblin = research.row("DT_Enemies", "Goblin", root=codex)
    assert research.cell(goblin["Spawn"], "Y") == 200 and research.cell(goblin["Spawn"], "x") == 100
    assert research.cell(goblin["Drops"], "0.Item") == "Sword" and research.cell(goblin["Drops"], "1.Chance") == 1
    assert research.cell(goblin["Drops"]) == [{"Item": "Sword", "Chance": 0.5}, {"Item": "Gold", "Chance": 1}]
    assert research.cell(research.row("DT_Enemies", "Orc Chief", root=codex)["Drops"]) == []
    assert research.cell(goblin["Health"], None) == 30  # (not a struct: as it is)
    text = '(Name="Big \\"Chief\\"",Text=NSLOCTEXT("ns","k","Hi, there"),Ref=None,Tags=("a","b"),Nested=(A=(B=2)))'
    assert research.cell(text, "Name") == 'Big "Chief"'
    assert research.cell(text, "Text") == 'NSLOCTEXT("ns","k","Hi, there")'
    assert research.cell(text, "Ref") is None and research.cell(text, "Tags") == ["a", "b"]
    assert research.cell(text, "Nested.A.B") == 2
    with pytest.raises(KeyError, match="no Zed"):
        research.cell(text, "Nested.Zed")


def test_grep_across_the_codex(codex):
    hits = research.grep(r"Health", root=codex)
    assert "blueprints/BP_Goblin.txt:3:   Health: float = 30" in hits
    assert "data/DT_Enemies.csv:1: ---,Health,Damage,Drops,Spawn,Leader" in hits
    assert research.grep(r"Health", where="blueprints", root=codex) == [
        "blueprints/BP_Goblin.txt:3:   Health: float = 30",
        "blueprints/BP_Goblin.txt:6:     Set Health(Health = Subtract(Health, Damage))"]
    short = research.grep(r"Health", root=codex, limit=2)
    assert len(short) == 3 and short[-1].startswith("... ") and "more matches" in short[-1]
    assert research.grep(r"(?i)goblin", where="data/DT_Enemies.csv", root=codex) == [
        'data/DT_Enemies.csv:2: "Goblin",30,5,"((Item=""Sword"",Chance=0.5),(Item=""Gold"",Chance=1))",'
        '"(X=100,Y=200,Z=0)",False']
    with pytest.raises(FileNotFoundError):
        research.grep("x", where="nowhere", root=codex)


def test_the_root_comes_from_the_game_or_names_for(codex, monkeypatch):
    class FakeCodex:
        root = codex

    game = type("G", (), {"codex": FakeCodex()})()
    assert research.tables(game)[0]["name"] == "CT_Scaling"
    assert research.tables(FakeCodex())[1]["name"] == "DT_Enemies"
    bound = research.names_for(game)
    assert bound["rows"]("DT_Enemies", "Wolf")[0]["Health"] == 45 and bound["nearest"] is research.nearest
    assert set(research.NAMES) == {"tables", "rows", "row", "cell", "grep", "nearest", "teleport", "fight_trial",
                                   "enemy_profile", "call_any"}
    assert research.names_for(None) == research.NAMES
    monkeypatch.setattr(research, "ROOT", None)
    with pytest.raises(ValueError, match="no codex folder"):
        research.rows("DT_Enemies")
    monkeypatch.setattr(research, "ROOT", codex)
    assert research.row("DT_Enemies", "Goblin")["Health"] == 30
    for name in ("tables", "rows", "row", "grep", "nearest", "teleport", "fight_trial", "enemy_profile", "call_any",
                 "cell"):
        assert f"`{name}(" in research.DOC


# -- the live game (a fake Unreal arena) --------------------------------------------------------------------

LEVEL = "/Game/Arena.Arena:PersistentLevel"
HERO, GOB1, GOB2, CRATE = f"{LEVEL}.Hero_C_0", f"{LEVEL}.BP_Goblin_C_0", f"{LEVEL}.BP_Goblin_C_1", f"{LEVEL}.Crate_C_0"


class ArenaLink:
    """A hero and two goblins: a LeftMouseButton press within 200 (across the floor) of the nearest goblin while facing
    it (within 10 degrees) and standing within 50 of its height (its box bottom against the hero's feet) takes 10 off
    its Health, and the goblin bites back for 3; at 0 the goblin is destroyed; at 0 the hero has no pawn. The mouse
    turns the view 0.1 degree per count; W held walks the hero at 200/s the way it looks; the ground is a slope
    `slope` rising toward the first goblin (0: flat), and the hero always stands on it. K2_TeleportTo moves an actor;
    SetControlRotation turns the view; Health is written back (unless readonly_health); MaxHealth and the hero's
    CharacterMovement MaxWalkSpeed (600) read. `enemy` makes a goblin come at the hero at a speed and bite from a reach
    every period for a damage, its health regenerating (see _advance)."""

    def __init__(self):
        self.hello = {"project": "Arena", "engine": "unreal"}
        self.pos = {HERO: Vec(0, 0, 90), GOB1: Vec(600, 0, 90), GOB2: Vec(-900, 300, 90), CRATE: Vec(0, 400, 50)}
        self.health = {HERO: 100, GOB1: 30, GOB2: 30}
        self.gone = set()
        self.yaw, self.pitch = 0.0, 0.0
        self.held = {}
        self.slope = 0.0
        self.readonly_health = False
        self.enemy = None  # {"actor", "speed", "reach", "period", "damage", "regen", "max"}
        self._bit_at = -1e9
        self.game_time, self._wall = 0.0, time.monotonic()
        self.lock = threading.Lock()
        self.calls = []

    def post(self, op, args=None):
        self.request(op, args)

    def request_at(self, op, args=None, timeout=None):
        return self.request(op, args, timeout), self.game_time

    def ground(self, x, y):
        return self.slope * (x - 600)

    def _facing(self, a, b):
        want = math.degrees(math.atan2(b.y - a.y, b.x - a.x))
        return abs((self.yaw - want + 180) % 360 - 180) < 10

    def _advance(self):
        now = time.monotonic()
        dt, self._wall = now - self._wall, now
        self.game_time += dt
        me = self.pos[HERO]
        if "W" in self.held and self.health[HERO] > 0:
            f = Vec(math.cos(math.radians(self.yaw)), math.sin(math.radians(self.yaw)), 0)
            me = me + f * (200 * dt)
        self.pos[HERO] = Vec(me.x, me.y, self.ground(me.x, me.y) + 90)
        e = self.enemy
        if e and e["actor"] not in self.gone and self.health[HERO] > 0:
            g = self.pos[e["actor"]]
            d = g.dist2d(self.pos[HERO])
            if d > e["reach"] and e["speed"]:
                step = min(e["speed"] * dt, d - e["reach"])
                to = (self.pos[HERO] - g).flat.unit
                g = g + to * step
                self.pos[e["actor"]] = Vec(g.x, g.y, self.ground(g.x, g.y) + 90)
            elif d <= e["reach"] and self.game_time - self._bit_at >= e["period"]:
                self.health[HERO] -= e["damage"]
                self._bit_at = self.game_time
            if e.get("regen"):
                self.health[e["actor"]] = min(e["max"], self.health[e["actor"]] + e["regen"] * dt)

    def request(self, op, args=None, timeout=None):
        from autopilot.bridge import BridgeError

        args = args or {}
        with self.lock:
            self._advance()
            if op == "context":
                return {"world": "/Game/Arena.Arena", "character": HERO if self.health[HERO] > 0 else "",
                        "controller": f"{LEVEL}.PC_0", "camera_manager": f"{LEVEL}.Cam_0",
                        "game_time": self.game_time}
            if op in ("pause", "view", "session"):
                return {}
            if op == "key":
                key, mode = args.get("key"), args.get("mode")
                if mode == "release_all":
                    self.held.clear()
                elif mode == "hold":
                    self.held[key] = args.get("value")
                elif mode == "release":
                    self.held.pop(key, None)
                elif key == "MouseX" and mode == "press":
                    self.yaw = (self.yaw + 0.1 * args.get("value", 0)) % 360
                elif key == "MouseY" and mode == "press":
                    self.pitch = max(-89, min(89, self.pitch + 0.1 * args.get("value", 0)))
                elif key == "LeftMouseButton" and mode == "press":
                    me = self.pos[HERO]
                    live = [g for g in (GOB1, GOB2) if g not in self.gone]
                    if live:
                        g = min(live, key=lambda g: self.pos[g].dist(me))
                        height = (self.pos[g].z - 90) - (me.z - 90)  # (its box bottom against the hero's feet)
                        if self.pos[g].dist2d(me) <= 200 and abs(height) < 50 and self._facing(me, self.pos[g]):
                            self.health[g] -= 10
                            self.health[HERO] -= 3
                            if self.health[g] <= 0:
                                self.gone.add(g)
                return {}
            if op != "rc":
                return {}
            body, verb = args["body"], args["verb"]
            obj, fn = body.get("objectPath"), body.get("functionName")
            if verb == "describe":
                if obj in self.pos or obj == "/Script/Engine.Actor" or obj.endswith("BP_Goblin_C"):
                    return {"Name": obj, "Class": "x", "Properties": [{"Name": "Health", "Type": "float"}],
                            "Functions": []}
                raise BridgeError(400, f"Object {obj} does not exist")
            if verb == "property":
                name = body.get("propertyName")
                if body.get("access") == "WRITE_ACCESS":
                    if obj in self.health and name == "Health" and not self.readonly_health:
                        self.health[obj] = body["propertyValue"][name]
                        return {}
                    raise BridgeError(400, f"Property {name} is read-only on object {obj}")
                if obj in self.health and name == "Health":
                    return {"Health": self.health[obj]}
                if obj == HERO and name == "MaxHealth":
                    return {"MaxHealth": 100}
                if obj == f"{HERO}.CharacterMovement" and name == "MaxWalkSpeed":
                    return {"MaxWalkSpeed": 600}
                if obj in self.pos and name == "bActorIsBeingDestroyed":
                    return {name: obj in self.gone}
                raise BridgeError(400, f"Property {name} does not exist on object {obj}")
            self.calls.append((obj, fn, body.get("parameters") or {}))
            params = body.get("parameters") or {}
            if fn == "GetAllActorsOfClass":
                cls = params["ActorClass"]
                if "Goblin" in cls:
                    return {"OutActors": [g for g in (GOB1, GOB2) if g not in self.gone]}
                if cls == "/Script/Engine.Actor":
                    return {"OutActors": [a for a in self.pos if a not in self.gone]}
                return {"OutActors": []}
            if obj in self.pos:
                if fn == "K2_GetActorLocation":
                    return {"ReturnValue": self.pos[obj].ue()}
                if fn == "GetActorBounds":
                    return {"Origin": self.pos[obj].ue(), "BoxExtent": {"X": 40, "Y": 40, "Z": 90}}
                if fn == "GetVelocity":
                    return {"ReturnValue": {"X": 0, "Y": 0, "Z": 0}}
                if fn == "K2_TeleportTo":
                    self.pos[obj] = Vec.of(params["DestLocation"])
                    return {"ReturnValue": True}
                if fn == "AddItem":
                    return {"ReturnValue": True, "Added": params.get("Amount", 1)}
            if fn == "GetControlRotation":
                return {"ReturnValue": {"Yaw": self.yaw, "Pitch": self.pitch % 360, "Roll": 0}}
            if fn == "SetControlRotation":
                self.yaw = params["NewRotation"]["Yaw"] % 360
                return {}
            if fn == "GetCameraLocation":
                return {"ReturnValue": (self.pos[HERO] + Vec(0, 0, 60)).ue()}
            raise BridgeError(400, f"Function {fn} does not exist on object {obj}")


@pytest.fixture
def arena():
    link = ArenaLink()
    game = Game(link)
    game.look = {"x": 0.1, "y": 0.1}
    return game, link


def test_nearest_sorts_by_distance_from_the_player(arena):
    game, link = arena
    found = research.nearest(game, "/Game/BP_Goblin.BP_Goblin_C")
    assert [(a, d) for a, _, d in found] == [(GOB1, 600.0), (GOB2, round(math.hypot(900, 300), 1))]
    assert found[0][1] == Vec(600, 0, 90)
    assert research.nearest(game, "/Game/BP_Goblin.BP_Goblin_C", within=700) == [(GOB1, Vec(600, 0, 90), 600.0)]
    assert [a for a, _, _ in research.nearest(game, [CRATE, GOB1, HERO], n=1)] == [CRATE]
    assert research.nearest(game, "/Game/Nothing.Nothing_C") == []
    with pytest.raises(GameError, match="nearest.*no class named Nothing"):
        research.nearest(game, "Nothing")


def test_teleport_goes_through_the_games_call_and_is_reported(arena):
    game, link = arena
    out = research.teleport(game, Vec(100, 200, 90), why="measure the gap")
    assert out == {"actor": HERO, "asked": [100, 200, 90], "at": [100, 200, 90], "landed": True}
    (moved,) = [c for c in link.calls if c[1] == "K2_TeleportTo"]
    assert moved[0] == HERO and moved[2]["DestLocation"] == {"X": 100, "Y": 200, "Z": 90}
    assert [(w["call"], w.get("why")) for w in game.writes] == [("K2_TeleportTo", "measure the gap")]
    # next to an actor: 150 short of it on the player's side (the hero stands at (100, 200) now), facing it (the
    # view too); asked with the feet at the goblin's box bottom, the hero's half height up (the game then stands it
    # on the ground: 90)
    out = research.teleport(game, GOB1)
    side = (Vec(100, 200, 0) - Vec(600, 0, 0)).unit
    expected = Vec(600, 0, 0) + side * 150
    assert out["asked"] == [round(expected.x), round(expected.y), 95]
    assert out["landed"] and out["at"] == [round(expected.x), round(expected.y), 90]
    assert link.yaw == pytest.approx(math.degrees(math.atan2(-expected.y, 600 - expected.x)) % 360, abs=0.01)
    out = research.teleport(game, {"near": GOB2, "distance": 300}, actor=CRATE, why="clear the way")
    at = link.pos[CRATE]
    assert abs(at.dist2d(link.pos[GOB2]) - 300) < 1 and at.z == 90 - 90 + 90 + 5
    assert game.writes[-1]["object"] == CRATE and game.writes[-1]["why"] == "clear the way"
    assert game.writes[-1]["parameters"]["DestRotation"]["Yaw"] == pytest.approx(
        math.degrees(math.atan2(link.pos[GOB2].y - at.y, link.pos[GOB2].x - at.x)))
    with pytest.raises(GameError, match="teleport"):
        research.teleport(game, "/Game/Arena.Arena:PersistentLevel.Nobody_0")


def test_teleport_on_the_other_engines():
    class Duck:
        def __init__(self, engine):
            self.engine = engines.get(engine)
            self.pawn = "Main:/Player" if engine == "unity" else "/root/Main/Player"
            self.writes = []
            self.at = Vec(0, 0, 0)

        def set(self, obj, prop, value, why=""):
            self.writes.append((obj, prop, value, why))
            self.at = self.engine.to_kit(value)

        def loc(self, actor):
            return self.at

        def to_native(self, v):
            return self.engine.to_native(v)

    unity = Duck("unity")
    out = research.teleport(unity, Vec(100, 200, 300), why="check")
    assert unity.writes == [("Main:/Player", "position", {"x": 2.0, "y": 3.0, "z": 1.0}, "check")] and out["landed"]
    godot = Duck("godot")
    research.teleport(godot, {"X": 100, "Y": 200, "Z": 300})
    assert godot.writes[0][:3] == ("/root/Main/Player", "global_position", {"x": 2.0, "y": 3.0, "z": -1.0})
    other = Duck("unity")
    other.engine = type("Eng", (), {"name": "other"})()
    with pytest.raises(GameError, match="no way to move an actor in a other game"):
        research.teleport(other, Vec(1, 2, 3))
    unity.pawn = ""
    with pytest.raises(GameError, match="no player pawn"):
        research.teleport(unity, Vec(1, 2, 3))


@wall_clock  # (the stance's distance is where the walk stopped: a tick's worth of wall time on a loaded runner)
def test_fight_trial_measures_hits_and_says_whether_it_can_be_won(arena):
    game, link = arena
    r = research.fight_trial(game, "/Game/BP_Goblin.BP_Goblin_C", seconds=6, every=0.05)
    assert r["target"] == GOB1 and r["killed"] and r["hits"] == 3 and r["damage_per_hit"] == 10
    assert r["target_health"] == [30, 0] and r["player_health"] == [100, 91] and r["damage_taken"] == 9
    assert r["health_props"] == {"target": "Health", "player": "Health"} and r["aim"] == "on target"
    assert r["verdict"].startswith("killed: 3 hits of 3 swings") and "lost 9 health" in r["verdict"]
    assert r["landed"] and r["walked"] == "arrived" and r["stopped"] == "target down"
    # teleported 200 beyond the reach, walked the rest: standing about the reach away, on the (flat) ground
    assert 90 <= r["stance"]["distance"] <= 160 and r["stance"]["height"] == 0 and r["closer"] is None
    assert game.writes[0]["call"] == "K2_TeleportTo" and "fight trial" in game.writes[0]["why"]
    assert abs(game.writes[0]["parameters"]["DestLocation"]["X"] - 280) < 1
    # healed: the player's health written back to what it was, reported like every write
    assert r["healed"] is True and r["player_health_max"] == 100 and not r["player_dead"] and link.health[HERO] == 100
    assert game.writes[-1]["property"] == "Health" and game.writes[-1]["value"] == 100
    assert game.writes[-1]["why"] == "research: undo the trial's damage"
    assert "the player's health was written back to 100" in r["verdict"]
    # a target out of reach: no hit, and the verdict says so (too short a trial to step closer)
    link.pos[GOB2] = Vec(-900, 300, 90)
    link.health[GOB2] = 30
    r = research.fight_trial(game, GOB2, seconds=0.3, every=0.05, reach=400)
    assert r["hits"] == 0 and not r["killed"] and "no swing" in r["verdict"] and "out of reach" in r["verdict"]
    assert r["stopped"] == "time up" and r["closer"] is None and 370 <= r["stance"]["distance"] <= 440
    assert f"from {r['stance']['distance']} units and" in r["verdict"]
    assert r["healed"] is None  # (nothing to heal)


@wall_clock  # (where the walk stops on the slope is a tick's worth of wall time)
def test_fight_trial_on_a_slope_reports_the_stance(arena):
    game, link = arena
    link.slope = 0.25  # (the ground rises toward the goblin: about 35 up over the last 130)
    r = research.fight_trial(game, GOB1, seconds=6, every=0.05)
    assert r["killed"] and r["hits"] == 3 and r["closer"] is None
    assert 100 <= r["stance"]["distance"] <= 160 and 20 <= r["stance"]["height"] <= 45, r["stance"]
    assert link.pos[HERO].z - 90 == pytest.approx(link.ground(link.pos[HERO].x, 0))  # (on the game's ground)


@wall_clock  # (where the walk stops on the slope is a tick's worth of wall time)
def test_fight_trial_steps_closer_once_when_nothing_lands(arena):
    game, link = arena
    link.slope = 0.6  # (from the reach the goblin stands 64-72 higher than the hero's feet: no hit; from 0.6 of it,
    # 35-43: hits land)
    r = research.fight_trial(game, GOB1, seconds=6, every=0.05)
    assert r["closer"] is not None and r["closer"]["distance"] < r["stance"]["distance"], r
    assert r["stance"]["height"] >= 50 > r["closer"]["height"] > 0, r
    assert r["killed"] and r["hits"] == 3 and r["swings"] >= research.CLOSER_AFTER + 3
    assert r["verdict"].startswith("killed")
    # too steep from closer as well: the verdict says both stances
    link = ArenaLink()
    link.slope = 1.0
    game = Game(link)
    game.look = {"x": 0.1, "y": 0.1}
    r = research.fight_trial(game, GOB1, seconds=1.0, every=0.05)
    assert r["hits"] == 0 and r["closer"] is not None and r["stopped"] == "time up"
    s, c = r["stance"], r["closer"]
    assert r["verdict"].startswith(f"no hit from {s['distance']} units and {s['height']} height; closer "
                                   f"({c['distance']}) neither")
    assert r["seconds"] < 3.0  # (the step closer didn't stretch the trial: the swings' second, plus the walk)


def test_fight_trial_heals_the_player_or_says_it_died(arena):
    game, link = arena
    link.health[HERO] = 5  # (two bites kill the hero: 5 -> 2 -> -1)
    r = research.fight_trial(game, GOB1, seconds=6, every=0.05)
    assert r["player_dead"] and r["healed"] is None and not r["killed"] and r["stopped"] == "the player died"
    assert r["player_health"] == [5, -1] and link.health[HERO] == -1 and not game.pawn
    assert "the player died: the level must be restarted" in r["verdict"] and "run_harness" in r["verdict"]
    assert not [w for w in game.writes if "property" in w]  # (no heal, no respawn)
    # the health can't be written back: said so, the player left hurt
    link = ArenaLink()
    link.readonly_health = True
    game = Game(link)
    game.look = {"x": 0.1, "y": 0.1}
    r = research.fight_trial(game, GOB1, seconds=6, every=0.05)
    assert r["killed"] and r["healed"] is False and link.health[HERO] == 91
    assert "the player is left at 91 health (its Health couldn't be written back)" in r["verdict"]
    # heal=False: left as the fight left it, without a word
    link = ArenaLink()
    game = Game(link)
    game.look = {"x": 0.1, "y": 0.1}
    r = research.fight_trial(game, GOB1, seconds=6, every=0.05, heal=False)
    assert r["healed"] is None and link.health[HERO] == 91 and r["verdict"].startswith("killed")


def test_fight_trial_verdict_projects_the_fight():
    base = {"killed": False, "hits": 2, "swings": 4, "damage_per_hit": 10.0, "seconds": 2.0, "aim": "on target",
            "target_health": [100, 80], "player_health": [100, 60], "damage_taken": 40.0,
            "health_props": {"target": "Health", "player": "Health"}, "stance": {"distance": 118, "height": 3}}
    words = research._verdict(base, 0.6)
    assert words.startswith("8 more hits (10 each) would kill it, about 8 s more")
    assert "the player would die first, in about 3 s" in words
    tough = dict(base, damage_taken=4.0)
    assert "the player would outlast it" in research._verdict(tough, 0.6)
    assert "took no damage" in research._verdict(dict(base, damage_taken=0), 0.6)
    assert "no health property" in research._verdict(
        dict(base, hits=0, health_props={"target": None, "player": "Health"}), 0.6)
    assert research._verdict(dict(base, hits=0), 0.6).startswith(
        "no swing of 4 changed its health from 118 units and 3 height: out of reach")
    closer = research._verdict(dict(base, hits=0, closer={"distance": 70, "height": 2}), 0.6)
    assert closer.startswith("no hit from 118 units and 3 height; closer (70) neither") and "reach under 70" in closer
    assert research._verdict(dict(base, hits=0, stance=None), 0.6).startswith(
        "no swing of 4 changed its health from an unread distance")
    assert research._verdict(dict(base, player_dead=True), 0.6).endswith("(run_harness restarts it by default)")


def test_enemy_profile_measures_an_enemy_without_fighting_it(arena):
    game, link = arena
    # a guard that comes at the hero at 400/s and bites for 25 every 0.3 s from 110 away, its health regenerating 2/s
    link.enemy = {"actor": GOB1, "speed": 400, "reach": 110, "period": 0.3, "damage": 25, "regen": 2.0, "max": 30}
    link.health[GOB1] = 20
    r = research.enemy_profile(game, "/Game/BP_Goblin.BP_Goblin_C", seconds=6, safe_distance=600)
    assert r["target"] == GOB1 and r["comes"] and 480 <= r["start_distance"] <= 610 and r["min_distance"] <= 115
    assert 300 <= r["approach_speed"] <= 500 and 300 <= r["speed"] <= 500 and r["player_speed"] == 600
    assert r["hits"] == 3 and r["damage_per_hit"] == 25 and r["damage_taken"] == 75
    assert 100 <= r["attack_range"] <= 125 and 0.2 <= r["attack_period"] <= 0.45, r
    assert 20 <= r["target_health"][0] < 21 and 1.0 <= r["regen_per_s"] <= 3.0, r  # (regenerating from 20)
    # kept alive: at 25 (under 40 of 100) the watch stopped and the hero went back to 600 away, then was healed
    assert r["player_health"] == [100, 25] and r["player_health_max"] == 100 and not r["player_dead"]
    assert r["stopped"].startswith("the player's health fell under 40% (25)") and r["seconds"] < 4
    assert link.pos[HERO].dist2d(link.pos[GOB1]) >= 450  # (600 when it was moved; the guard comes on after)
    assert r["healed"] is True and link.health[HERO] == 100
    assert [w["why"] for w in game.writes if w.get("why")] == [f"enemy profile of {GOB1}",
                                                                f"enemy profile of {GOB1}: back to safety",
                                                                "research: undo the profile's damage"]
    summary = r["summary"]
    assert summary.startswith(f"comes at the player at {r['approach_speed']}/s (the player walks 600): can be out-run")
    assert f"hits for 25 every {r['attack_period']:g} s from {r['attack_range']} away" in summary
    assert f"health regenerates {r['regen_per_s']:g}/s" in summary and "stopped early" in summary
    assert r["health_props"] == {"target": "Health", "player": "Health"}
    # a passive one: stands there, never attacks, no regeneration
    link = ArenaLink()
    link.enemy = {"actor": GOB2, "speed": 0, "reach": 110, "period": 0.3, "damage": 25}
    game = Game(link)
    game.look = {"x": 0.1, "y": 0.1}
    r = research.enemy_profile(game, GOB2, seconds=0.6, safe_distance=500)
    assert not r["comes"] and r["speed"] == 0 and r["hits"] == 0 and r["attack_range"] is None
    assert r["regen_per_s"] == 0 and r["stopped"] == "time up" and r["healed"] is None
    assert r["summary"].startswith(f"doesn't come at the player in {r['seconds']:g} s from 500 away (passive until "
                                   "hit?); moves at 0/s at most (the player walks 600); never attacked (nearest ")
    assert r["summary"].endswith("; health doesn't regenerate on its own")


def test_enemy_profile_summary_in_words():
    base = {"comes": True, "approach_speed": 400, "speed": 420, "player_speed": 300, "seconds": 20.0,
            "start_distance": 1200, "min_distance": 100, "hits": 2, "damage_per_hit": 25.0, "attack_period": 1.6,
            "attack_range": 110, "regen_per_s": 1.0, "stopped": "time up"}
    assert research._profile_summary(base) == ("comes at the player at 400/s (the player walks 300): can't be out-run;"
                                               " hits for 25 every 1.6 s from 110 away; health regenerates 1/s")
    words = research._profile_summary(dict(base, player_speed=None, attack_period=None, regen_per_s=None))
    assert words == "comes at the player at 400/s; hits for 25 from 110 away; no health property found on it"
    assert research._profile_summary(dict(base, player_dead=True)).endswith(
        "; the player died: the level must be restarted (run_harness restarts it by default)")


def test_call_any_takes_the_first_name_the_game_accepts(arena):
    game, link = arena
    out = research.call_any(game, HERO, ["GiveItem", "AddItem"], Item="Sword", Amount=2)
    assert out == {"called": "AddItem", "result": {"ReturnValue": True, "Added": 2}}
    with pytest.raises(GameError, match="none accepted: Nope: .*Function Nope.*; Nor: "):
        research.call_any(game, HERO, ["Nope", "Nor"])
