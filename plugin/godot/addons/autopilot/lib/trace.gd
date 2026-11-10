## `trace` (as Unreal's PlaytestOpsTrace.cpp): the first thing a line, or a ball swept along it, meets between two points
## of the played world, by the 3D physics. What a thing's box can't tell: a big mesh's box is many times what is solid of
## it, a ring or an arch is open through the middle of its box.
##
## {"start", "end": kit {"X","Y","Z"}, "radius"?: cm (0: a line), "channel"?: "body" (the default: the player's
## collision_mask, what stops it as it moves) | "sight" (every layer)} -> {"channel", "hit": null | {"location" (where the
## line stops: the ball's center when it touches), "point" (where it touches), "normal", "distance" (cm, start to
## location), "actor" (the collider's node path), "name", "component" (its class), "inside" (start was already in it)}}.
## Areas never stop it, nor the player's own body and what is under it.
extends RefCounted

const Frame = preload("frame.gd")

const MAX_RADIUS := 100.0  # metres


static func _rids(p: Node) -> Array:
	var rids := []
	if p == null:
		return rids
	var stack: Array = [p]
	while stack.size() > 0:
		var c: Node = stack.pop_back()
		if c is CollisionObject3D:
			rids.append(c.get_rid())
		stack.append_array(c.get_children())
	return rids


static func _kit_normal(n: Vector3) -> Dictionary:
	return {"X": snappedf(-n.z, 0.001), "Y": snappedf(n.x, 0.001), "Z": snappedf(n.y, 0.001)}


## [true, answer] | [false, error]. world: the addon's World (its player and viewport).
static func trace(world, args: Dictionary) -> Array:
	if typeof(args.get("start")) != TYPE_DICTIONARY or typeof(args.get("end")) != TYPE_DICTIONARY:
		return [false, "trace needs a start and an end ({\"X\", \"Y\", \"Z\"})"]
	if world.dimension() == "2d":
		return [false, "trace: a 2D scene (trace works in 3D)"]
	var vp: Viewport = world.host.get_viewport()
	var w3d: World3D = vp.find_world_3d() if vp else null
	if w3d == null:
		return [false, "trace: no 3D world"]
	var space := w3d.direct_space_state
	var start: Vector3 = Frame.from_kit(args.start)
	var end: Vector3 = Frame.from_kit(args.end)
	var radius := clampf(float(args.get("radius", 0.0)) / Frame.SCALE, 0.0, MAX_RADIUS)
	var sight := str(args.get("channel", "")) == "sight"
	var p: Node = world.player()
	var mask := 0xFFFFFFFF
	if not sight and p is CollisionObject3D:
		mask = p.collision_mask
	var exclude := _rids(p)
	var channel := "sight" if sight else "body"
	var d := end - start
	var dist: float = d.length()
	var dir: Vector3 = d / dist if dist > 1e-6 else Vector3.FORWARD
	if radius <= 0.0:
		var q := PhysicsRayQueryParameters3D.create(start, end, mask, exclude)
		q.collide_with_areas = false
		q.hit_from_inside = true
		var r: Dictionary = space.intersect_ray(q)
		if r.is_empty():
			return [true, {"channel": channel, "hit": null}]
		var inside: bool = r.normal == Vector3.ZERO
		var at: Vector3 = r.position
		return [true, {"channel": channel, "hit": _hit(at, at, -dir if inside else r.normal, start.distance_to(at),
			r.get("collider"), inside)}]
	var shape := SphereShape3D.new()
	shape.radius = radius
	var sq := PhysicsShapeQueryParameters3D.new()
	sq.shape = shape
	sq.transform = Transform3D(Basis.IDENTITY, start)
	sq.motion = d
	sq.collision_mask = mask
	sq.exclude = exclude
	sq.collide_with_areas = false
	# (a ball that starts in something: inside it, where cast_motion goes on through)
	sq.motion = Vector3.ZERO
	var first := space.intersect_shape(sq, 1)
	if not first.is_empty():
		return [true, {"channel": channel, "hit": _hit(start, start, -dir, 0.0, first[0].get("collider"), true)}]
	sq.motion = d
	var frac := space.cast_motion(sq)
	if frac.size() < 2 or frac[1] >= 1.0:
		return [true, {"channel": channel, "hit": null}]
	# What it touches: its rest contact where the cast first collides. Where it stops: the ball's center touching there
	# (the contact point out along the face's normal by the radius, taken onto the line), cast_motion's safe fraction
	# stopping short by its margin
	var at := start + d * frac[0]
	sq.transform = Transform3D(Basis.IDENTITY, start + d * frac[1])
	sq.motion = Vector3.ZERO
	var rest := space.get_rest_info(sq)
	var collider = instance_from_id(int(rest.collider_id)) if rest.has("collider_id") else null
	var point: Vector3 = rest.get("point", at)
	var normal: Vector3 = rest.get("normal", -dir)
	if rest.has("point") and rest.has("normal"):
		at = start + dir * clampf((point + normal * radius - start).dot(dir), 0.0, dist)
	return [true, {"channel": channel, "hit": _hit(at, point, normal, start.distance_to(at), collider, false)}]


static func _hit(at: Vector3, point: Vector3, normal: Vector3, dist_m: float, collider, inside: bool) -> Dictionary:
	var h := {"location": Frame.kit(at), "point": Frame.kit(point), "normal": _kit_normal(normal),
		"distance": snappedf(dist_m * Frame.SCALE, 0.1), "inside": inside}
	if collider is Node:
		h.actor = str(collider.get_path())
		h.name = str(collider.name)
		h.component = collider.get_class()
	return h
