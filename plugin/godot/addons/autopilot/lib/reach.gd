## What a player sees of the room round it and finds by its own body, for `view` in 3D (as Unreal's PlaytestOpsView.cpp
## and PlaytestOpsOpenings.cpp): a thing's own box and its top surface (`top_slope_degrees`, a slope's `low_end`,
## `high_end`, `lane` and `uphill`), the `openings` through walls and the ways up (`ways_up`) round the body, all in the
## kit frame (X forward, Y right, Z up, centimetres) and found as the player's own body would find them: lines from it,
## its own capsule swept, lines down onto the floor. Never the game's navigation data.
##
## Godot's CharacterBody3D doesn't step up ledges: a body climbs what its shape rides over (a capsule's or a sphere's
## rounded bottom a few centimetres, see Probe.step) and slopes no steeper than its floor_max_angle. So where Unreal's
## body may step up `MaxStepHeight` from one place to the next, here the floor may rise by that step plus what a slope
## it stands on rises over the way between them, and a rise more than the step is felt again at quarters of the way
## (a stair's riser rises all at once there: not walked up).
extends RefCounted

const Frame = preload("frame.gd")


## A Godot point (metres) in the kit frame (centimetres).
static func K(v: Vector3) -> Vector3:
	return Vector3(-v.z, v.x, v.y) * Frame.SCALE


## A kit point as a Godot one.
static func G(k: Vector3) -> Vector3:
	return Vector3(k.y, k.z, -k.x) / Frame.SCALE


## A Godot direction in the kit frame (unscaled).
static func KD(v: Vector3) -> Vector3:
	return Vector3(-v.z, v.x, v.y)


## A kit vector as the answer has it: whole units, as Unreal's.
static func vec(k: Vector3) -> Dictionary:
	return {"X": roundf(k.x), "Y": roundf(k.y), "Z": roundf(k.z)}


static func dist2d(a: Vector3, b: Vector3) -> float:
	return Vector2(a.x - b.x, a.y - b.y).length()


static func flat(v: Vector3) -> Vector3:
	return Vector3(v.x, v.y, 0.0)


static func wrap180(a: float) -> float:
	return fposmod(a + 180.0, 360.0) - 180.0


static func dir_of(degrees: float) -> Vector3:
	var r := deg_to_rad(degrees)
	return Vector3(cos(r), sin(r), 0.0)


## Distance from a point to a segment, across the floor (z ignored).
static func dist_to_segment2d(p: Vector3, a: Vector3, b: Vector3) -> float:
	var ab := Vector2(b.x - a.x, b.y - a.y)
	var ap := Vector2(p.x - a.x, p.y - a.y)
	var len2 := ab.length_squared()
	var t := clampf(ap.dot(ab) / len2, 0.0, 1.0) if len2 > 0.0 else 0.0
	return (ap - ab * t).length()


# --- a thing's own box ------------------------------------------------------------------------------

## A thing's box in its own frame (as Unreal's FPlaytestShape), in the kit frame: {"center": Vector3, "axis": [3 unit
## Vector3], "half": Vector3 (half its size along each of its axes)}; from its drawn parts (GeometryInstance3D), else
## its collision shapes. Empty when it has neither.
static func own_box(t: Node3D, parts: Array) -> Dictionary:
	var inv := t.global_transform.affine_inverse()
	var box := AABB()
	var have := false
	for n in parts:
		if not is_instance_valid(n):
			continue
		var local: AABB = (inv * n.global_transform) * n.get_aabb()
		box = local if not have else box.merge(local)
		have = true
	if not have:
		return {}
	var basis := t.global_basis
	var scale := basis.get_scale()
	var axes := [KD(basis.x.normalized()), KD(basis.y.normalized()), KD(basis.z.normalized())]
	var half := Vector3(absf(box.size.x * scale.x), absf(box.size.y * scale.y), absf(box.size.z * scale.z)) * 0.5 * Frame.SCALE
	return {"center": K(t.global_transform * box.get_center()), "axis": axes, "half": half}


static func up_index(b: Dictionary) -> int:
	var best := 0
	for i in range(1, 3):
		if absf(b.axis[i].z) > absf(b.axis[best].z):
			best = i
	return best


static func top_normal(b: Dictionary) -> Vector3:
	var k := up_index(b)
	return b.axis[k] * (1.0 if b.axis[k].z >= 0.0 else -1.0)


static func box_distance(b: Dictionary, p: Vector3) -> float:
	var d: Vector3 = p - b.center
	var sum := 0.0
	for i in 3:
		var out := maxf(0.0, absf(d.dot(b.axis[i])) - b.half[i])
		sum += out * out
	return sqrt(sum)


# --- the body's own probes --------------------------------------------------------------------------

## What the player's body finds by itself (Unreal's FBodyProbe): lines stopped by what stops it, its own capsule swept,
## the floor under a place. Kit units in and out.
class Probe:
	# (an inner class doesn't reach its script's functions: the conversions again)
	static func K(v: Vector3) -> Vector3:
		return Vector3(-v.z, v.x, v.y) * 100.0

	static func G(k: Vector3) -> Vector3:
		return Vector3(k.y, k.z, -k.x) / 100.0

	static func KD(v: Vector3) -> Vector3:
		return Vector3(-v.z, v.x, v.y)

	static func dist2d(a: Vector3, b: Vector3) -> float:
		return Vector2(a.x - b.x, a.y - b.y).length()

	var space: PhysicsDirectSpaceState3D
	var exclude: Array = []  # the player's own bodies and what it holds
	var mask := 0xFFFFFFFF  # what stops the body: its collision mask
	var radius := 40.0
	var half_height := 90.0
	var step := 0.0  # what it rides up at once: its shape's rounded bottom (a CharacterBody doesn't step up)
	var tan_slope := 1.0  # how steep a floor it walks up, as rise over run
	var walkable_z := 0.7071  # a floor's normal at least this upright is one it stands on
	var feet := 0.0
	var me := Vector3.ZERO  # the body's middle

	## The first thing along a line that stops the body: {"at", "distance", "collider", "normal"}, {} for none.
	func line(a: Vector3, b: Vector3) -> Dictionary:
		var q := PhysicsRayQueryParameters3D.create(G(a), G(b), mask, exclude)
		q.collide_with_areas = false
		var r := space.intersect_ray(q)
		if r.is_empty():
			return {}
		var at := K(r.position)
		return {"at": at, "distance": a.distance_to(at), "collider": r.collider, "normal": KD(r.normal)}

	## The floor under a place, straight down from `from` by `depth`: [found, its height, whether a body stands on it].
	func floor_at(from: Vector3, depth: float) -> Array:
		var hit := line(from, from - Vector3(0.0, 0.0, depth))
		if hit.is_empty():
			return [false, 0.0, false]
		return [true, hit.at.z, hit.normal.z >= walkable_z]

	## How much higher than a place the floor may be `run` units on, for a body walking there: what it rides up at once
	## and what the steepest floor it walks rises over that run.
	func climb(run: float) -> float:
		return step + run * tan_slope

	## Whether a body walks from a floor `from` (its height in .z) to the floor at `to` (z: that floor's height), `run`
	## units on: no more of a rise or fall than climb(run); a rise or fall more than the step felt again at quarters of
	## the way (a stair's riser is all in one quarter: no slope, not walked).
	func walks(from: Vector3, to: Vector3) -> bool:
		var run := dist2d(from, to)
		var change := absf(to.z - from.z)
		if change <= step + 1.0:
			return true
		if change > climb(run) + 1.0:
			return false
		var z := from.z
		var top := maxf(from.z, to.z)
		for q in [0.25, 0.5, 0.75, 1.0]:
			var zq := to.z
			if q < 1.0:
				var at := from.lerp(to, q)
				var f := floor_at(Vector3(at.x, at.y, top + climb(run) + 5.0), climb(run) * 2.0 + 20.0)
				if not f[0]:
					return false
				zq = f[1]
			if absf(zq - z) > climb(run * 0.25) + 1.0:
				return false
			z = zq
		return true

	## Its own capsule, a hair thinner and lifted a little off the feet, swept from one place for the feet to another:
	## {"at", "collider"} of what stops it on the way, {} when nothing does.
	func sweep(from_feet: Vector3, to_feet: Vector3) -> Dictionary:
		var r := maxf(1.0, radius - 2.0)
		var lift_by := maxf(step, 5.0)
		var half := maxf(r, half_height - lift_by * 0.5 - 2.0)
		var lift := Vector3(0.0, 0.0, lift_by + 2.0 + half)
		var shape := CapsuleShape3D.new()
		shape.radius = r / Frame.SCALE
		shape.height = 2.0 * half / Frame.SCALE
		var q := PhysicsShapeQueryParameters3D.new()
		q.shape = shape
		q.transform = Transform3D(Basis.IDENTITY, G(from_feet + lift))
		q.motion = G(to_feet + lift) - G(from_feet + lift)
		q.collision_mask = mask
		q.exclude = exclude
		q.collide_with_areas = false
		var frac := space.cast_motion(q)
		if frac.size() < 2 or frac[1] >= 1.0:
			return {}
		q.transform = Transform3D(Basis.IDENTITY, G(from_feet + lift) + q.motion * frac[1])
		q.motion = Vector3.ZERO
		var rest := space.get_rest_info(q)
		var at := from_feet.lerp(to_feet, frac[1])
		var collider = instance_from_id(int(rest.get("collider_id", 0))) if rest.has("collider_id") else null
		return {"at": K(rest.point) if rest.has("point") else at, "collider": collider}


# --- openings and ways up ---------------------------------------------------------------------------

const OPENING_RAY_DEGREES := 1.0
const OPENING_DEEPER := 150.0
const OPENING_DEEPER_SHARE := 0.3
const OPENING_SECTOR := 15.0
const OPENING_SAME_HEIGHT := 60.0
const OPENING_FLAT := 8.0
const OPENING_SEEN_SHARE := 1.2
const OPENING_ASIDE := 12.0
const MAX_OPENINGS := 5
const OPENINGS_TRIED := 8
const OPENING_EDGE_STEPS := 3
const WAY_UP_EVERY := 5.0
const WAY_UP_PACE := 40.0
const WAY_UP_REACH := 1500.0
const WAY_UP_RISE := 100.0
const WAY_UP_FOOT_APART := 150.0
const MAX_WAYS_UP := 3
const WAY_UP_PROBE := 40.0
const WAY_UP_SIDE_PROBES := 2
const WAY_UP_LANE_STATIONS := [1.0 / 3.0, 2.0 / 3.0]
const WAY_UP_LANE_STEP := 20.0
const WAY_UP_LANE_REACH := 600.0
const WAY_UP_LANE_DEEP := 3000.0
const WAY_UP_LANE_BELOW := 1000.0
const WAY_UP_LANE_HALVINGS := 3


## view's `openings` (and `free`) and `ways_up` (Unreal's AddOpenings): the body's room across the camera's field of
## view (`yaw`, `fov` degrees wide) out to `reach`. sees(p): whether a line of sight from the camera reaches p (a kit
## point); holders: [[thing, own box]] of what is in view (what an opening leads into); name_of(collider): the name of
## the thing a collider is part of.
static func add_openings(body: Dictionary, p: Probe, cam: Vector3, yaw: float, fov: float, reach: float, openings: bool,
		ways_up: bool, holders: Array, sees: Callable, name_of: Callable) -> void:
	var height := p.half_height * 2.0
	var knee := maxf(p.step + 5.0, height * 0.28)
	var chest := maxf(knee + 10.0, height * 0.62)
	var n := clampi(floori(fov / OPENING_RAY_DEGREES) + 1, 3, 361)
	var left := yaw - fov * 0.5
	var me := p.me
	var turn_to := func(from: Vector3, to: Vector3) -> float:
		return roundf(wrap180(rad_to_deg(atan2(to.y - from.y, to.x - from.x)) - yaw))
	# how far a line at that height goes from the body before something stops it ([distance, what stopped it])
	var depth := func(degrees: float, above: float) -> Array:
		var from := Vector3(me.x, me.y, p.feet + above)
		var hit := p.line(from, from + dir_of(degrees) * reach)
		if hit.is_empty():
			return [reach, null]
		return [hit.distance, hit.collider]
	var rays := []
	for i in n:
		var ray_yaw := minf(left + i * OPENING_RAY_DEGREES, yaw + fov * 0.5)
		var k: Array = depth.call(ray_yaw, knee)
		var c: Array = depth.call(ray_yaw, chest)
		rays.append({"yaw": ray_yaw, "knee": k[0], "chest": c[0], "thing": c[1] if c[1] != null else k[1]})
	var open_of := func(r: Dictionary) -> float:
		return minf(r.knee, r.chest)
	var deeper := func(near: float) -> float:
		return maxf(OPENING_DEEPER, OPENING_DEEPER_SHARE * near)
	var standing := func(r: Dictionary) -> bool:
		return r.knee < reach and r.chest < reach and absf(r.knee - r.chest) <= OPENING_SAME_HEIGHT
	var point_of := func(degrees: float, distance: float) -> Vector3:
		return Vector3(me.x, me.y, 0.0) + dir_of(degrees) * distance

	if openings:
		var gaps := []
		var wall_way := func(side: int, step_dir: int) -> Vector3:
			var a := side + step_dir
			var b := side + 2 * step_dir
			if a < 0 or b < 0 or a >= n or b >= n or not standing.call(rays[a]) or not standing.call(rays[b]):
				return Vector3.ZERO
			var p0: Vector3 = point_of.call(rays[side].yaw, open_of.call(rays[side]))
			var p1: Vector3 = point_of.call(rays[a].yaw, open_of.call(rays[a]))
			var p2: Vector3 = point_of.call(rays[b].yaw, open_of.call(rays[b]))
			var run := p2 - p0
			var length := Vector2(run.x, run.y).length()
			if length < 10.0:
				return Vector3.ZERO
			var u := run / length
			var d := p1 - p0
			if absf(u.x * d.y - u.y * d.x) > OPENING_FLAT:
				return Vector3.ZERO
			return u
		var refine := func(near_yaw: float, far_yaw: float, near_depth: float) -> Vector3:
			for _s in OPENING_EDGE_STEPS:
				var mid := (near_yaw + far_yaw) * 0.5
				var o := minf(depth.call(mid, knee)[0], depth.call(mid, chest)[0])
				if o <= near_depth + deeper.call(near_depth) * 0.5:
					near_yaw = mid
					near_depth = o
				else:
					far_yaw = mid
			return point_of.call(near_yaw, near_depth)
		var i := 0
		while i + 1 < n:
			if not standing.call(rays[i]) or open_of.call(rays[i + 1]) - open_of.call(rays[i]) < deeper.call(open_of.call(rays[i])):
				i += 1
				continue
			var next_i := i + 1
			for k in range(i + 2, n):
				if not standing.call(rays[k]) or open_of.call(rays[k - 1]) - open_of.call(rays[k]) < deeper.call(open_of.call(rays[k])):
					continue
				var l: Vector3 = point_of.call(rays[i].yaw, open_of.call(rays[i]))
				var r: Vector3 = point_of.call(rays[k].yaw, open_of.call(rays[k]))
				var across := r - l
				var chord := Vector2(across.x, across.y).length()
				if chord < 1.0:
					break
				var out := Vector3(-across.y / chord, across.x / chord, 0.0)
				var middle := (l + r) * 0.5
				if out.dot(middle - flat(me)) < 0.0:
					out = -out
				var beyond := INF
				for j in range(i + 1, k):
					beyond = minf(beyond, (point_of.call(rays[j].yaw, open_of.call(rays[j])) - l).dot(out))
				if beyond < p.radius or chord < OPENING_SEEN_SHARE * p.radius:
					break
				var near: Vector3 = refine.call(rays[i].yaw, rays[i + 1].yaw, open_of.call(rays[i]))
				var far: Vector3 = refine.call(rays[k].yaw, rays[k - 1].yaw, open_of.call(rays[k]))
				var along: Vector3 = wall_way.call(i, -1)
				if along != Vector3.ZERO:
					far = near + along * (far - near).dot(along)
				else:
					along = wall_way.call(k, 1)
					if along != Vector3.ZERO:
						near = far + along * (near - far).dot(along)
				across = far - near
				var width := Vector2(across.x, across.y).length()
				if width >= OPENING_SEEN_SHARE * p.radius:
					var gap := {"along": across / width, "width": width, "beyond": beyond,
						"wall": rays[i].thing if rays[i].thing != null else rays[k].thing, "near": rays[i].thing, "far": rays[k].thing}
					gap.out = Vector3(-gap.along.y, gap.along.x, 0.0)
					gap.middle = (near + far) * 0.5
					if gap.out.dot(gap.middle - flat(me)) < 0.0:
						gap.out = -gap.out
					gap.distance = dist2d(gap.middle, me)
					gaps.append(gap)
				next_i = k  # (on past its far side)
				break
			i = next_i
		gaps.sort_custom(func(a, b): return a.distance < b.distance)
		var listed := []
		var g := 0
		while g < gaps.size() and g < OPENINGS_TRIED and listed.size() < MAX_OPENINGS:
			var gap: Dictionary = gaps[g]
			g += 1
			var out: Vector3 = gap.out
			var middle: Vector3 = gap.middle
			var top := p.feet + chest
			var down := chest + 600.0
			var before_out := p.radius + 40.0
			var after_out := p.radius + 60.0
			var before0 := middle - out * before_out
			var after0 := middle + out * after_out
			var fb := p.floor_at(Vector3(before0.x, before0.y, top), down)
			var fi := p.floor_at(Vector3(middle.x, middle.y, top), down)
			var fa := p.floor_at(Vector3(after0.x, after0.y, top), down)
			var has_floor: bool = fb[0] and fi[0] and fa[0]
			var z_before: float = fb[1] if has_floor else p.feet
			var z_in: float = fi[1] if has_floor else p.feet
			var z_after: float = fa[1] if has_floor else p.feet
			if not sees.call(Vector3(middle.x, middle.y, (z_in if has_floor else p.feet) + chest), 30.0):
				continue  # hidden from the eyes
			var step_by := maxf(absf(z_in - z_before), absf(z_after - z_in)) if has_floor else -1.0
			var floor_ok: bool = has_floor and fb[2] and fi[2] and fa[2] \
				and p.walks(Vector3(before0.x, before0.y, z_before), Vector3(middle.x, middle.y, z_in)) \
				and p.walks(Vector3(middle.x, middle.y, z_in), Vector3(after0.x, after0.y, z_after))
			var passes := false
			for aside in [0.0, OPENING_ASIDE, -OPENING_ASIDE, 2.0 * OPENING_ASIDE, -2.0 * OPENING_ASIDE]:
				var on_line: Vector3 = gap.middle + gap.along * aside
				var from := on_line - out * before_out
				var to := on_line + out * after_out
				if floor_ok and p.sweep(Vector3(from.x, from.y, z_before), Vector3(to.x, to.y, z_after)).is_empty():
					passes = true
					middle = on_line
					break
			var after := middle + out * after_out
			var blocked := p.sweep(Vector3(me.x, me.y, p.feet), Vector3(after.x, after.y, z_after if has_floor else p.feet))
			var o := {"at": vec(Vector3(middle.x, middle.y, z_in if has_floor else p.feet)), "turn": turn_to.call(me, middle),
				"distance": roundf(dist2d(middle, me)), "width": roundf(gap.width),
				"yaw": roundf(rad_to_deg(atan2(out.y, out.x))), "depth_beyond": roundf(minf(gap.beyond, reach))}
			if has_floor:
				o.floor_step = roundf(step_by)
				o.floor_above_feet = roundf(z_in - p.feet)
			o.reachable_straight = blocked.is_empty()
			if not blocked.is_empty():
				o.blocked_at = roundf(dist2d(blocked.at, me))
				var by: String = name_of.call(blocked.collider)
				if by != "":
					o.blocked_by = by
			o.passable = passes
			# What it leads into: what is overhead just past it, else the smallest thing in view whose own box holds the
			# place a body's width in from its sides
			var inside := Vector3(after.x, after.y, (z_after if has_floor else p.feet) + height)
			var over := p.line(inside, inside + Vector3(0.0, 0.0, 2000.0))
			var into := ""
			if not over.is_empty():
				into = name_of.call(over.collider)
			if into == "":
				var smallest := INF
				for h in holders:
					var s: Dictionary = h[1]
					var d: Vector3 = inside - Vector3(0.0, 0.0, height * 0.5) - s.center
					var up := up_index(s)
					var held_in: bool = s.half[up] * 2.0 >= height * 2.0
					for a in 3:
						if not held_in:
							break
						held_in = absf(d.dot(s.axis[a])) <= s.half[a] + (10.0 if a == up else -2.0 * p.radius)
					var volume: float = s.half.x * s.half.y * s.half.z
					if held_in and volume < smallest:
						smallest = volume
						into = str(h[0].name)
			if into != "":
				o.into = into
			var wall_name: String = name_of.call(gap.wall) if gap.wall != null else ""
			if wall_name != "":
				o.wall = wall_name
			if gap.near != null and gap.far != null:
				o.sides = [name_of.call(gap.near), name_of.call(gap.far)]
			listed.append(o)
		body.openings = listed
		var free := []
		var from := 0.0
		while from < fov - 0.5:
			var at_knee := reach
			var at_chest := reach
			for r in rays:
				var off: float = r.yaw - left
				if off >= from - 0.01 and off < from + OPENING_SECTOR - 0.01:
					at_knee = minf(at_knee, r.knee)
					at_chest = minf(at_chest, r.chest)
			free.append({"turn": roundf((from + minf(from + OPENING_SECTOR, fov)) * 0.5 - fov * 0.5),
				"knee": roundf(at_knee / 10.0) * 10.0, "chest": roundf(at_chest / 10.0) * 10.0})
			from += OPENING_SECTOR
		body.free = free

	if ways_up:
		body.ways_up = _ways_up(p, cam, yaw, fov, reach, knee, chest, height, rays, sees, name_of)


## The floor under a place as a body walking from a floor `from_z` high finds it: [found and stood on, its height].
static func _floor_from(p: Probe, at: Vector3, from_z: float, run: float) -> Array:
	var f := p.floor_at(Vector3(at.x, at.y, from_z + p.climb(run) + 5.0), p.climb(run) * 2.0 + 65.0)
	return [f[0] and f[2], f[1]]


## From a tread along `way` (sign 1: while the floor rises; -1: while it falls), a pace at a time, no wall at the
## knees, until it levels off: where it ends (going down, the floor where it levelled off, the foot; going up, the last
## tread up, the top).
static func _march(p: Probe, from: Vector3, from_z: float, way: Vector3, sign_of: float, knee: float) -> Vector3:
	var last := Vector3(from.x, from.y, from_z)
	var end := last
	var z := from_z
	var level := 0
	var gone := WAY_UP_PACE
	while gone <= WAY_UP_REACH and level < 2:
		var next := flat(from) + way * gone
		gone += WAY_UP_PACE
		if not p.line(Vector3(last.x, last.y, z + knee), Vector3(next.x, next.y, z + knee)).is_empty():
			break
		var f := _floor_from(p, next, z, WAY_UP_PACE)
		if not f[0] or not p.walks(last, Vector3(next.x, next.y, f[1])):
			break
		if (f[1] - z) * sign_of > 3.0:
			level = 0
			end = Vector3(next.x, next.y, f[1])
		else:
			level += 1
			if level == 1 and sign_of < 0.0:
				end = Vector3(next.x, next.y, f[1])
		z = f[1]
		last = Vector3(next.x, next.y, f[1])
	return end


static func _ways_up(p: Probe, cam: Vector3, yaw: float, fov: float, reach: float, knee: float, chest: float,
		height: float, rays: Array, sees: Callable, name_of: Callable) -> Array:
	var me := p.me
	var n := rays.size()
	var left := yaw - fov * 0.5
	var found := []
	# Seen from the side: a staircase or ramp rising across the view (see Unreal's SideWayUp)
	var side_way_up := func(degrees: float, sight_to: float) -> Dictionary:
		var dir := dir_of(degrees)
		var to := minf(minf(WAY_UP_REACH, reach), sight_to - p.radius)
		var probes := 0
		var out_by := WAY_UP_PACE
		while out_by <= to and probes < WAY_UP_SIDE_PROBES:
			var at := flat(me) + dir * out_by
			out_by += WAY_UP_PACE
			var f := p.floor_at(Vector3(at.x, at.y, cam.z), cam.z - p.feet + p.climb(WAY_UP_PACE) + 5.0)
			if not f[0] or not f[2] or f[1] < p.feet + p.climb(WAY_UP_PACE) + 5.0:
				continue
			var z: float = f[1]
			var tread := Vector3(at.x, at.y, z)
			for k in found:
				if z <= k.top.z + 5.0 and dist_to_segment2d(tread, k.foot, k.top) <= WAY_UP_FOOT_APART:
					return {}
			if not sees.call(tread + Vector3(0.0, 0.0, 5.0), 20.0):
				continue
			probes += 1
			var best := {"by": 0.0, "yaw": 0.0}  # (a lambda changes what it holds, never a local it captured)
			var try_way := func(toward: float) -> void:
				var w := dir_of(toward)
				var fu := _floor_from(p, tread + w * WAY_UP_PROBE, z, WAY_UP_PROBE)
				var fd := _floor_from(p, tread - w * WAY_UP_PROBE, z, WAY_UP_PROBE)
				if fu[0] and fd[0]:
					var rise: float = fu[1] - z
					var drop: float = z - fd[1]
					if rise > 3.0 and rise <= p.climb(WAY_UP_PROBE) + 1.0 and drop > 3.0 and drop <= p.climb(WAY_UP_PROBE) + 1.0 \
							and minf(rise, drop) > best.by:
						best.by = minf(rise, drop)
						best.yaw = toward
			for d in 8:
				try_way.call(d * 45.0)
			if best.by <= 0.0:
				continue
			var coarse: float = best.yaw
			try_way.call(coarse - 22.5)
			try_way.call(coarse + 22.5)
			var up := dir_of(best.yaw)
			var foot := _march(p, tread, z, -up, -1.0, knee)
			var top := _march(p, tread, z, up, 1.0, knee)
			if absf(foot.z - p.feet) > p.climb(WAY_UP_PACE) + 5.0 or top.z - foot.z < WAY_UP_RISE:
				continue
			return {"foot": foot, "top": top, "rise": top.z - foot.z, "foot_distance": dist2d(foot, me),
				"top_distance": dist2d(top, me), "degrees": rad_to_deg(atan2(foot.y - me.y, foot.x - me.x)), "side": true,
				"reach": p.sweep(Vector3(me.x, me.y, p.feet), foot).is_empty()}
		return {}
	var off := 0.0
	while off <= fov + 0.01:
		var degrees := left + off
		var dir := dir_of(degrees)
		var nearest := clampi(roundi(off / OPENING_RAY_DEGREES), 0, n - 1)
		var flat_open: float = minf(rays[nearest].knee, rays[nearest].chest)
		var z := p.feet
		var foot_z := p.feet
		var foot_distance := -1.0
		var last := Vector3(me.x, me.y, p.feet)
		var foot := last
		var rise := 0.0
		var top_distance := 0.0
		var top_at := last
		var level := 0
		var out_by := WAY_UP_PACE
		while out_by <= minf(WAY_UP_REACH, reach):
			var next := flat(me) + dir * out_by
			var clear := z <= p.feet + 5.0 and z >= p.feet - 5.0 and out_by <= flat_open - p.radius
			if not clear and (not p.line(Vector3(last.x, last.y, z + knee), Vector3(next.x, next.y, z + knee)).is_empty()
					or not p.line(Vector3(last.x, last.y, z + chest), Vector3(next.x, next.y, z + chest)).is_empty()):
				break  # a wall at the knees or the chest, from the floor reached
			var f := _floor_from(p, next, z, WAY_UP_PACE)
			if not f[0] or not p.walks(last, Vector3(next.x, next.y, f[1])):
				break  # a drop, a floor too steep to stand on, a step too high
			var floor_z: float = f[1]
			if floor_z > z + 3.0:
				if foot_distance < 0.0:
					foot_distance = out_by - WAY_UP_PACE
					foot = Vector3(last.x, last.y, z)
					foot_z = z
				level = 0
			elif foot_distance >= 0.0:
				level += 1
				if level >= 2 and z - foot_z >= WAY_UP_RISE:
					break  # on top: level again
				if level >= 3 and z - foot_z < WAY_UP_RISE:
					foot_distance = -1.0  # a small rise that levelled off (a kerb, a sill): not a way up
					level = 0
			z = floor_z
			last = Vector3(next.x, next.y, z)
			if foot_distance >= 0.0 and z - foot_z > rise:
				rise = z - foot_z
				top_distance = out_by
				top_at = last
			out_by += WAY_UP_PACE
		off += WAY_UP_EVERY
		if foot_distance < 0.0 or rise < WAY_UP_RISE:
			var side: Dictionary = side_way_up.call(degrees, rays[nearest].chest)
			if not side.is_empty():
				found.append(side)
			continue
		if not sees.call(foot + Vector3(0.0, 0.0, 30.0), 30.0):
			continue
		found.append({"foot": foot, "top": top_at, "rise": rise, "foot_distance": foot_distance,
			"top_distance": top_distance, "degrees": degrees, "side": false, "reach": true})
	found.sort_custom(func(a, b): return a.foot_distance < b.foot_distance)
	var kept := []
	for way in found:
		var same := -1
		for i in kept.size():
			if dist2d(kept[i].foot, way.foot) <= WAY_UP_FOOT_APART:
				same = i
				break
		if same < 0:
			if kept.size() < MAX_WAYS_UP:
				kept.append(way)
		elif way.rise > kept[same].rise:
			kept[same] = way  # (one foot: the line up it that rose most)
	for way in kept:
		_lane(p, cam, way, knee, sees)
	var out := []
	for way in kept:
		var o := {"turn": roundf(wrap180(way.degrees - yaw)), "distance_to_foot": roundf(way.foot_distance),
			"rises_by": roundf(way.rise), "top_distance": roundf(way.top_distance), "foot": vec(way.foot), "top": vec(way.top)}
		if way.side:
			o.seen_from = "side"
			o.reachable_straight = way.reach
		if way.get("lane", false):
			var side_of := func(kind: int, deep: float) -> Dictionary:
				var told := {"by": "drop" if kind == 2 else ("wall" if kind == 1 else "open")}
				if kind == 2:
					told.depth = roundf(deep)
				return told
			o.lane = {"width": roundf(way.width), "right": side_of.call(way.right_by, way.right_depth),
				"left": side_of.call(way.left_by, way.left_depth),
				"below": {"clear": roundf(way.clear), "by": ["reach", "wall", "drop", "unseen"][way.below_by]}}
		# What it is inside: what is overhead at its foot
		var head: Vector3 = way.foot + Vector3(0.0, 0.0, height)
		var over := p.line(head, head + Vector3(0.0, 0.0, 2000.0))
		if not over.is_empty():
			var inside: String = name_of.call(over.collider)
			if inside != "":
				o.inside = inside
		out.append(o)
	return out


## A way up's lane (Unreal's Lanes): across it at WAY_UP_LANE_STATIONS of the way from its foot to its top, the floor a
## body walks across it each side, to a wall, a drop or open floor; its middle line through the two runs' middles, its
## foot and top marched again along that line, its width the narrowest run, and how far the floor goes on below its
## foot as far as the eyes see. Kept as found (no `lane`) where a run isn't found or the line's foot isn't on the body's
## floor or its top doesn't rise WAY_UP_RISE.
static func _lane(p: Probe, cam: Vector3, way: Dictionary, knee: float, sees: Callable) -> void:
	var seen_floor := func(at: Vector3) -> bool:
		return sees.call(at + Vector3(0.0, 0.0, 5.0), 20.0)
	# from a tread out along dir: [how far a body walks on (at most `most`), what stopped it (0 nothing, 1 a wall, 2 a
	# drop, 3 a place the eyes don't see), how deep a drop]
	var across := func(at: Vector3, from_z: float, dir: Vector3, most: float, sight: bool) -> Array:
		var z := from_z
		var good := 0.0
		var try_out := func(out_by: float, z_now: float, good_now: float) -> Array:  # [ok, new z, why, fall]
			var from2 := flat(at) + dir * good_now
			var to2 := flat(at) + dir * out_by
			if not p.line(Vector3(from2.x, from2.y, z_now + knee), Vector3(to2.x, to2.y, z_now + knee)).is_empty():
				return [false, z_now, 1, 0.0]
			var f := p.floor_at(Vector3(to2.x, to2.y, z_now + p.climb(WAY_UP_LANE_STEP) + 5.0), p.climb(WAY_UP_LANE_STEP) * 2.0 + 10.0)
			if f[0] and f[1] >= z_now - p.climb(out_by - good_now) - 1.0:
				if not f[2] or not p.walks(Vector3(from2.x, from2.y, z_now), Vector3(to2.x, to2.y, f[1])):
					return [false, z_now, 1, 0.0]
				if sight and not seen_floor.call(Vector3(to2.x, to2.y, f[1])):
					return [false, z_now, 3, 0.0]
				return [true, f[1], 0, 0.0]
			var why := 3 if sight and not seen_floor.call(Vector3(to2.x, to2.y, z_now)) else 2
			var low := p.floor_at(Vector3(to2.x, to2.y, z_now), WAY_UP_LANE_DEEP)
			return [false, z_now, why, (z_now - low[1]) if low[0] else WAY_UP_LANE_DEEP]
		var out_by := WAY_UP_LANE_STEP
		while out_by <= most + 0.01:
			var r: Array = try_out.call(out_by, z, good)
			if not r[0]:
				var hi := out_by
				for _h in WAY_UP_LANE_HALVINGS:
					var mid := (good + hi) * 0.5
					var m: Array = try_out.call(mid, z, good)
					if m[0]:
						good = mid
						z = m[1]
					else:
						hi = mid
				return [good, r[2], r[3]]
			good = out_by
			z = r[1]
			out_by += WAY_UP_LANE_STEP
		return [most, 0, 0.0]
	var rough := flat(way.top - way.foot)
	if rough.length() < 0.001:
		return
	rough = rough.normalized()
	var side := Vector3(-rough.y, rough.x, 0.0)  # (its right, facing up it the way it was found)
	var middles := [Vector3.ZERO, Vector3.ZERO]
	var zs := [0.0, 0.0]
	var chords := [0.0, 0.0]
	var bys := [[0, 0], [0, 0]]
	var depths := [[0.0, 0.0], [0.0, 0.0]]
	for k in 2:
		var t: float = WAY_UP_LANE_STATIONS[k]
		var at: Vector3 = way.foot.lerp(way.top, t)
		var f := _floor_from(p, at, lerpf(way.foot.z, way.top.z, t), WAY_UP_PACE)
		if not f[0]:
			return
		var o: Array = across.call(at, f[1], side, WAY_UP_LANE_REACH, false)
		var b: Array = across.call(at, f[1], -side, WAY_UP_LANE_REACH, false)
		bys[k] = [o[1], b[1]]
		depths[k] = [o[2], b[2]]
		middles[k] = flat(at) + side * ((o[0] - b[0]) * 0.5)
		chords[k] = o[0] + b[0]
		zs[k] = f[1]
	var up2: Vector3 = flat(middles[1] - middles[0])
	up2 = rough if up2.length() < p.radius else up2.normalized()
	var square := absf(side.x * up2.y - side.y * up2.x)
	var knee_h := knee
	var foot := _march(p, Vector3(middles[0].x, middles[0].y, zs[0]), zs[0], -up2, -1.0, knee_h)
	var top := _march(p, Vector3(middles[1].x, middles[1].y, zs[1]), zs[1], up2, 1.0, knee_h)
	if absf(foot.z - way.foot.z) > p.climb(WAY_UP_PACE) + 5.0 or top.z - foot.z < WAY_UP_RISE:
		return
	way.foot = foot
	way.top = top
	way.rise = top.z - foot.z
	way.foot_distance = dist2d(foot, p.me)
	way.top_distance = dist2d(top, p.me)
	way.degrees = rad_to_deg(atan2(foot.y - p.me.y, foot.x - p.me.x))
	way.lane = true
	way.width = minf(chords[0], chords[1]) * square
	var side_right := side.x * -up2.y + side.y * up2.x >= 0.0  # (side lies to the right facing up)
	for j in 2:
		var worst := 0
		var deepest := 0.0
		for k in 2:
			if (bys[k][j] == 2 and (worst != 2 or depths[k][j] > deepest)) or (bys[k][j] == 1 and worst == 0):
				worst = bys[k][j]
				deepest = depths[k][j]
		var right := (j == 0) == side_right
		way["right_by" if right else "left_by"] = worst
		way["right_depth" if right else "left_depth"] = deepest
	var below: Array = across.call(foot, foot.z, -up2, WAY_UP_LANE_BELOW, true)
	way.clear = below[0]
	way.below_by = below[1]


# --- a thing's top surface --------------------------------------------------------------------------

const SLOPE_MIN := 5.0
const SLOPE_MAX := 50.0
const SLOPE_END_INSET := 15.0
const SLOPE_END_CLEAR := 2.0
const SLOPE_END_PROBE := 5.0
const SLOPE_END_STRIDE := 20.0
const SLOPE_END_STRIDES := 128
const SLOPE_END_HALVINGS := 5
const SLOPE_LANE_STATIONS := [0.25, 0.5, 0.75]
const SLOPE_LANE_STEP := 20.0
const SLOPE_LANE_DEEP := 3000.0
const SLOPE_LANE_BELOW := 1000.0
const SLOPE_LANE_SAMPLES := 64
const SLOPE_LANE_HALVINGS := 3


## The height of a thing's surface straight down through a place (kit), on its own collision shapes (a ray from
## `above` to `below` that passes through anything else); without shapes of its own, its own box's top face.
static func height_at(ctx: Dictionary, p: Vector3) -> float:
	var rids: Array = ctx.thing_rids
	if rids.is_empty():
		var top: Vector3 = ctx.top
		var nrm: Vector3 = ctx.top_normal
		if absf(nrm.z) < 0.01:
			return p.z
		return top.z - (nrm.x * (p.x - top.x) + nrm.y * (p.y - top.y)) / nrm.z
	var ex := []
	for _i in 8:
		var q := PhysicsRayQueryParameters3D.create(G(Vector3(p.x, p.y, ctx.above)), G(Vector3(p.x, p.y, ctx.below)), 0xFFFFFFFF, ex)
		q.collide_with_areas = true
		var r: Dictionary = ctx.space.intersect_ray(q)
		if r.is_empty():
			return p.z
		if rids.has(r.rid):
			return K(r.position).z
		ex.append(r.rid)
	return p.z


## Whether a body's room is clear just over a place on the slope (Unreal's Open): a column SLOPE_END_PROBE units either
## side, from SLOPE_END_CLEAR over the place up to the body's height, overlapping nothing that stops the body but the
## slope itself, the player and characters, and nothing nobody sees (an invisible blocking volume).
static func is_open(ctx: Dictionary, p: Vector3) -> bool:
	var room := maxf(1.0, (ctx.body_height - SLOPE_END_CLEAR) * 0.5)
	var shape := BoxShape3D.new()
	shape.size = Vector3(2.0 * SLOPE_END_PROBE, 2.0 * room, 2.0 * SLOPE_END_PROBE) / Frame.SCALE
	var q := PhysicsShapeQueryParameters3D.new()
	q.shape = shape
	q.transform = Transform3D(Basis.IDENTITY, G(p + Vector3(0.0, 0.0, SLOPE_END_CLEAR + room)))
	q.collision_mask = ctx.mask
	q.exclude = ctx.beside
	q.collide_with_areas = false
	for r in ctx.space.intersect_shape(q, 16):
		if ctx.shows.call(r.collider) and not ctx.of_character.call(r.collider):
			return false
	return true


## The floor straight down from `start` by `depth`, by what stops the body (the slope itself left out): [found, z].
static func _floor_beside(ctx: Dictionary, start: Vector3, depth: float) -> Array:
	var q := PhysicsRayQueryParameters3D.create(G(start), G(start - Vector3(0.0, 0.0, depth)), ctx.mask, ctx.beside)
	q.collide_with_areas = false
	var r: Dictionary = ctx.space.intersect_ray(q)
	return [false, 0.0] if r.is_empty() else [true, K(r.position).z]


## A thing's top surface (Unreal's Surface), into `item`: `top_slope_degrees` along the way its own box's top face rises
## most; for a slope a body could walk (5 to 50 degrees) its `low_end`, `high_end` (each only where seen), its `lane`
## and `uphill`. ctx: "space", "mask" (what stops the body), "beside" (RIDs left out: the player, what it holds, the
## thing), "thing_rids" (the thing's own collision objects), "box" (own_box), "world_top", "world_bottom" (its box
## round it, kit Z), "tipped", "sees_point": Callable(kit point) -> bool (on screen and in sight), "place":
## Callable(kit point) -> Dictionary, "shows", "of_character": Callable(collider) -> bool, "body_height", "step_up",
## "radius", "cam_yaw".
static func surface(item: Dictionary, ctx: Dictionary) -> void:
	var b: Dictionary = ctx.box
	var k := up_index(b)
	var top: Vector3 = b.center + top_normal(b) * b.half[k]
	ctx.top = top
	ctx.top_normal = top_normal(b)
	ctx.above = ctx.world_top + 50.0
	ctx.below = ctx.world_bottom - 50.0
	var found := false
	var steepest := 0.0
	var along := 0
	var low_end := top
	var high_end := top
	for i in 3:
		if i == k:
			continue
		var reach_by := maxf(0.0, b.half[i] - minf(SLOPE_END_INSET, b.half[i] * 0.25))
		var one: Vector3 = top + b.axis[i] * reach_by
		var other: Vector3 = top - b.axis[i] * reach_by
		one.z = height_at(ctx, one)
		other.z = height_at(ctx, other)
		var run := dist2d(one, other)
		if run < 1.0:
			continue
		var slope := rad_to_deg(atan2(absf(one.z - other.z), run))
		if not found or slope > steepest:
			found = true
			steepest = slope
			along = i
			low_end = one if one.z <= other.z else other
			high_end = other if one.z <= other.z else one
	if not found or (steepest < 1.0 and not ctx.tipped):
		return  # level on top: nothing to tell
	item.top_slope_degrees = roundf(steepest)
	if steepest < SLOPE_MIN or steepest > SLOPE_MAX or high_end.z - low_end.z < 10.0:
		return
	var across_i := 3 - k - along
	var at_t := func(from: Vector3, to: Vector3, t: float) -> Vector3:
		var p := from.lerp(to, t)
		p.z = height_at(ctx, p)
		return p
	var first_open := func(from: Vector3, to: Vector3) -> Variant:
		var strides := clampi(ceili(dist2d(from, to) / SLOPE_END_STRIDE), 1, SLOPE_END_STRIDES)
		var covered := 0.0
		for i in strides + 1:
			var t := float(i) / strides
			if not is_open(ctx, at_t.call(from, to, t)):
				covered = t
				continue
			var hi := t
			if i > 0:
				for _h in SLOPE_END_HALVINGS:
					var mid := (covered + hi) * 0.5
					if is_open(ctx, at_t.call(from, to, mid)):
						hi = mid
					else:
						covered = mid
			return at_t.call(from, to, hi)
		return null
	var end_at := func(edge: Vector3, other: Vector3) -> Variant:
		for share in [0.0, 1.0 / 3.0, -1.0 / 3.0, 2.0 / 3.0, -2.0 / 3.0]:
			var side: Vector3 = b.axis[across_i] * (b.half[across_i] * share)
			var p = first_open.call(edge + side, other + side)
			if p != null and ctx.sees_point.call(p):
				return p
		return null
	var e = end_at.call(low_end, high_end)
	if e != null:
		item.low_end = ctx.place.call(e)
	e = end_at.call(high_end, low_end)
	if e != null:
		item.high_end = ctx.place.call(e)
	# The lane a body walks up it (see Unreal's Surface)
	var step_up: float = ctx.step_up
	var body_radius: float = ctx.radius
	var up2 := flat(high_end - low_end).normalized()
	var right2 := Vector3(-up2.y, up2.x, 0.0)
	var right_sign := 1.0 if b.axis[across_i].dot(right2) >= 0.0 else -1.0
	var edge_q := maxf(0.0, b.half[across_i] - minf(SLOPE_END_INSET, b.half[across_i] * 0.25))
	var samples := clampi(ceili(2.0 * edge_q / SLOPE_LANE_STEP), 2, SLOPE_LANE_SAMPLES)
	var worse := func(kept: Dictionary, here: Dictionary) -> void:
		if (here.by == 2 and (kept.by != 2 or here.depth > kept.depth)) or (here.by == 1 and kept.by == 0):
			kept.by = here.by
			kept.depth = here.depth
	var past := func(middle: Vector3, q: float, sign_of: float) -> Dictionary:
		var p: Vector3 = middle + b.axis[across_i] * q
		var z := height_at(ctx, p)
		p = middle + b.axis[across_i] * (sign_of * (b.half[across_i] + body_radius))
		p.z = z
		if not is_open(ctx, p):
			return {"by": 1, "depth": 0.0}
		var f := _floor_beside(ctx, Vector3(p.x, p.y, z + step_up), step_up + SLOPE_LANE_DEEP)
		if not f[0]:
			return {"by": 2, "depth": SLOPE_LANE_DEEP}
		var fall: float = z - f[1]
		return {"by": 2, "depth": fall} if fall > step_up else {"by": 0, "depth": 0.0}
	var sides := [{"by": 0, "depth": 0.0}, {"by": 0, "depth": 0.0}]
	var narrowest := 0.0
	var offsets := 0.0
	var runs := 0
	var seen_lane := false
	for t in SLOPE_LANE_STATIONS:
		var middle := low_end.lerp(high_end, t)
		var at_q := func(q: float) -> Vector3:
			var p: Vector3 = middle + b.axis[across_i] * q
			p.z = height_at(ctx, p)
			return p
		var q_of := func(i: int) -> float:
			return -edge_q + 2.0 * edge_q * i / samples
		var free := []
		for i in samples + 1:
			free.append(is_open(ctx, at_q.call(q_of.call(i))))
		var run_from := -1
		var run_to := -1
		var i := 0
		while i <= samples:
			if not free[i]:
				i += 1
				continue
			var j := i
			while j < samples and free[j + 1]:
				j += 1
			if run_from < 0 or j - i > run_to - run_from:
				run_from = i
				run_to = j
			i = j + 1
		if run_from < 0:
			continue
		var halve := func(open_q: float, covered_q: float) -> float:
			for _h in SLOPE_LANE_HALVINGS:
				var mid := (open_q + covered_q) * 0.5
				if is_open(ctx, at_q.call(mid)):
					open_q = mid
				else:
					covered_q = mid
			return open_q
		var lo: float = halve.call(q_of.call(run_from), q_of.call(run_from - 1)) if run_from > 0 else -edge_q
		var hi: float = halve.call(q_of.call(run_to), q_of.call(run_to + 1)) if run_to < samples else edge_q
		narrowest = hi - lo if runs == 0 else minf(narrowest, hi - lo)
		offsets += (lo + hi) * 0.5
		runs += 1
		var wall := {"by": 1, "depth": 0.0}
		worse.call(sides[0], wall if run_to < samples else past.call(middle, edge_q, 1.0))
		worse.call(sides[1], wall if run_from > 0 else past.call(middle, -edge_q, -1.0))
		seen_lane = seen_lane or ctx.sees_point.call(at_q.call((lo + hi) * 0.5))
	if runs > 0 and seen_lane:
		var shift: Vector3 = b.axis[across_i] * (offsets / runs)
		var lane := {"width": roundf(narrowest)}
		var halfway := low_end.lerp(high_end, 0.5) + shift
		halfway.z = height_at(ctx, halfway)
		lane.middle = vec(halfway)
		var foot = first_open.call(low_end + shift, high_end + shift)
		var has_foot: bool = foot != null and ctx.sees_point.call(foot)
		if has_foot:
			lane.foot = ctx.place.call(foot)
		var top_p = first_open.call(high_end + shift, low_end + shift)
		if top_p != null and ctx.sees_point.call(top_p):
			lane.top = ctx.place.call(top_p)
		var side_of := func(kind: Dictionary) -> Dictionary:
			var o := {"by": "drop" if kind.by == 2 else ("wall" if kind.by == 1 else "open")}
			if kind.by == 2:
				o.depth = roundf(kind.depth)
			return o
		lane.right = side_of.call(sides[0] if right_sign > 0.0 else sides[1])
		lane.left = side_of.call(sides[1] if right_sign > 0.0 else sides[0])
		if has_foot:
			var z: float = foot.z
			var clear := SLOPE_LANE_BELOW
			var by := "reach"
			var d := SLOPE_LANE_STEP
			while d <= SLOPE_LANE_BELOW:
				var p: Vector3 = foot - up2 * d
				var f := _floor_beside(ctx, Vector3(p.x, p.y, z + step_up), 2.0 * step_up + 1.0)
				if not f[0]:
					p.z = z
					clear = d - SLOPE_LANE_STEP
					by = "wall" if not is_open(ctx, p) else ("drop" if ctx.sees_point.call(p) else "unseen")
					break
				p.z = f[1]
				if not is_open(ctx, p):
					clear = d - SLOPE_LANE_STEP
					by = "wall"
					break
				if not ctx.sees_point.call(p):
					clear = d - SLOPE_LANE_STEP
					by = "unseen"
					break
				z = p.z
				d += SLOPE_LANE_STEP
			lane.below = {"clear": roundf(clear), "by": by}
		item.lane = lane
	var yaw := rad_to_deg(atan2(high_end.y - low_end.y, high_end.x - low_end.x))
	item.uphill = {"yaw": roundf(yaw), "turn": roundf(wrap180(yaw - ctx.cam_yaw))}
