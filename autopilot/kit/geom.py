"""Vectors and angles in Unreal's frame: X forward, Y right, Z up, centimetres; yaw turns right (degrees), pitch up."""

import math
from dataclasses import dataclass


class Num(float):
    """A number that can also be called (returns itself): v.length and v.length() both work."""

    def __call__(self):
        return float(self)


@dataclass(frozen=True)
class Vec:
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0

    @staticmethod
    def of(v):
        """A Vec from what Unreal answers ({"X","Y","Z"}), a sequence, or a Vec. None stays None."""
        if v is None or isinstance(v, Vec):
            return v
        if isinstance(v, dict):
            return Vec(float(v.get("X", v.get("x", 0.0))), float(v.get("Y", v.get("y", 0.0))),
                       float(v.get("Z", v.get("z", 0.0))))
        x, y, *z = v
        return Vec(float(x), float(y), float(z[0]) if z else 0.0)

    def ue(self):
        return {"X": self.x, "Y": self.y, "Z": self.z}

    def __add__(self, o):
        return Vec(self.x + o.x, self.y + o.y, self.z + o.z)

    def __sub__(self, o):
        return Vec(self.x - o.x, self.y - o.y, self.z - o.z)

    def __mul__(self, k):
        return Vec(self.x * k, self.y * k, self.z * k)

    __rmul__ = __mul__

    def __truediv__(self, k):
        return Vec(self.x / k, self.y / k, self.z / k)

    def dot(self, o):
        return self.x * o.x + self.y * o.y + self.z * o.z

    # length, length2d, flat and unit are properties that can also be called: v.length and v.length() both work
    @property
    def length(self):
        return Num(math.sqrt(self.dot(self)))

    @property
    def length2d(self):
        return Num(math.hypot(self.x, self.y))

    @property
    def flat(self):
        return Vec(self.x, self.y, 0.0)

    @property
    def unit(self):
        n = float(self.length)
        return self / n if n > 1e-9 else Vec()

    def __call__(self):
        return self  # (see above: v.flat() and v.unit() give the Vec too)

    def dist(self, o):
        return Num((self - o).length)

    def dist2d(self, o):
        return Num((self - o).length2d)

    def rounded(self, nd=0):
        return [round(self.x, nd), round(self.y, nd), round(self.z, nd)]

    def __repr__(self):
        return f"Vec({self.x:.0f}, {self.y:.0f}, {self.z:.0f})"


def wrap(deg):
    """An angle in (-180, 180]."""
    deg = math.fmod(deg, 360.0)
    if deg > 180:
        deg -= 360
    elif deg <= -180:
        deg += 360
    return deg


def yaw_of(v):
    """The yaw (degrees) a direction points at."""
    return math.degrees(math.atan2(v.y, v.x))


def pitch_of(v):
    return math.degrees(math.atan2(v.z, float(v.length2d)))


def forward(yaw, pitch=0.0):
    cy, sy = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
    cp, sp = math.cos(math.radians(pitch)), math.sin(math.radians(pitch))
    return Vec(cp * cy, cp * sy, sp)


def right_of(yaw):
    return forward(yaw + 90.0)


def bearing(from_point, yaw, to_point):
    """How far right (degrees, -180..180) to turn from facing `yaw` at from_point to face to_point."""
    return wrap(yaw_of(to_point - from_point) - yaw)


def aim_error(eye, yaw, pitch, target):
    """(right, up) degrees to turn the view from (yaw, pitch) at eye to look straight at target."""
    d = target - eye
    return wrap(yaw_of(d) - yaw), pitch_of(d) - wrap(pitch)


def clock(bearing_deg):
    """A bearing in words a player would use: ahead, ahead-left, left, behind-left, behind..."""
    b = wrap(bearing_deg)
    a = abs(b)
    side = "right" if b > 0 else "left"
    if a <= 15:
        return "ahead"
    if a <= 60:
        return f"ahead-{side}"
    if a <= 120:
        return side
    if a <= 165:
        return f"behind-{side}"
    return "behind"
