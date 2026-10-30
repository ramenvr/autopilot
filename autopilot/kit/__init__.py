"""The toolkit harness code is written with: `from autopilot.kit import *`."""

from typesafe_sdk import Choice, Noul, Score  # noqa: F401 - for harnesses' extra questions

from . import control  # noqa: F401
from .control import (  # noqa: F401
    Result,
    aim_and_strike,
    face_and_press,
    go_and_press,
    hold_for,
    jump_to,
    keep_away,
    look_at,
    melee,
    movement,
    point_of,
    press,
    run,
    sonar,
    strike_and_retreat,
    track,
    until,
    wait,
    walk_to,
)
from .game import GAMEPLAY, Game, GameError, name_of  # noqa: F401
from .geom import Vec, aim_error, bearing, clock, forward, pitch_of, right_of, wrap, yaw_of  # noqa: F401
from .harness import Harness, Option, Status, Step  # noqa: F401


def where(pose, point):
    """Facts about a point as a player sees it from pose (Game.pose()): distance across the floor, which way
    (ahead/left/behind...), how much higher, and the exact bearing in degrees (right +)."""
    point = Vec.of(point)
    b = bearing(pose["at"], pose["yaw"], point)
    return {"distance": round(point.dist2d(pose["at"])), "direction": clock(b), "bearing": round(b),
            "height": round(point.z - pose["at"].z)}
