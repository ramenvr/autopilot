## What the player sees and feels (`view`), and things watched over time (`watch`), in the kit frame.
##
## view (3D): things with a drawn body (VisualInstance3D) in the camera's frustum that a line of sight from the camera
## reaches (the box's center, top or nearest point), nearest first, grouped into things (a node's nearest physics body
## or area, else the root of the scene instance it belongs to, else the mesh itself); the player, what it holds and the
## large floor it stands on left out. Each with how it is drawn (look: parts, lights, animation, texts, effects,
## highlighted, limp), whether the body is against it (blocks_player), and its top surface (top_slope_degrees; for a
## walkable slope low_end, high_end, lane, uphill: reach.gd). Plus what the player holds (`held`), the sun
## (`scene.sun`), the camera, the body (on_ground, falling, standing_on, touching, floor slope, overhead; with
## openings or ways_up asked, under) and, asked for, `openings`/`free` and `ways_up` (reach.gd) and `sonar` (how far
## the body could walk each way: test_move sweeps of its own shape, rays down for drops).
## view (2D): drawn CanvasItems in the camera's rect, by thing, nearest the player first; 1 px = 1 unit (frame.gd).
extends RefCounted

const Frame = preload("frame.gd")
const World = preload("world.gd")
const Reach = preload("reach.gd")

var host: Node
var world


func _init(owner_node: Node, world_module) -> void:
	host = owner_node
	world = world_module


## What `view` doesn't tell, listed in its answer's `omitted`: nothing in 3D; in 2D what is the 3D body's (its room
## round it, slopes, what it holds) and how things are drawn beyond where they are.
const OMITTED_3D := []
const OMITTED_2D := ["held", "openings", "ways_up", "lane", "top_slope_degrees", "low_end", "high_end", "uphill",
	"body.under", "body.overhead", "look", "blocks_player", "sonar"]


## The thing a node is part of: its nearest physics body or area (itself included), else the nearest root of a scene
## instance, else itself. Never above the current scene's root.
func thing_of(n: Node) -> Node:
	var s: Node = world.scene()
	var x := n
	while x and x != s and x != host.get_tree().root:
		if x is CollisionObject3D or x is CollisionObject2D:
			return x
		if x.scene_file_path != "" and x != n:
			return x
		x = x.get_parent()
	return n


static func class_of(n: Node) -> String:
	var s = n.get_script()
	if s is Script:
		var g := str(s.get_global_name())
		if g != "":
			return g
	return n.get_class()


func view(args: Dictionary) -> Array:
	match world.dimension():
		"3d":
			return view3d(args)
		"2d":
			return view2d(args)
	return [true, {"visible": [], "camera": null, "body": null, "effects_play": false, "omitted": OMITTED_2D}]


func _player_rids(p: Node) -> Array:
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


func _belongs(collider: Object, thing: Node) -> bool:
	return collider == thing or (collider is Node and thing.is_ancestor_of(collider))


func view3d(args: Dictionary) -> Array:
	var cam: Camera3D = world.camera3d()
	if cam == null:
		return [false, "No camera"]
	var p: Node = world.player()
	var max_m := float(args.get("max_distance", 5000.0)) / Frame.SCALE
	var max_count := int(args.get("max_count", 24))
	var overhead_reach := float(args.get("overhead_reach", 2000.0))
	var floor_m := float(args.get("floor_size", 4000.0)) / Frame.SCALE  # wider than this and flat: the ground, left out
	# Asked for: the openings the player sees round it (`openings`), the ways up it sees (`ways_up`), within
	# `openings_reach` (see Reach.add_openings); a game that never asks pays nothing
	var want_openings: bool = args.get("openings", false) == true
	var want_ways_up: bool = args.get("ways_up", false) == true
	var openings_reach := maxf(100.0, float(args.get("openings_reach", 3000.0)))
	var only = args.get("only", null)
	var only_set := {}
	if typeof(only) == TYPE_ARRAY:
		for x in only:
			only_set[str(x)] = true
	var is_only := typeof(only) == TYPE_ARRAY
	var space := cam.get_world_3d().direct_space_state
	# What the player holds (see held_things): its own, as the player's body is, to every sense
	var held := [] if is_only else held_things(p, cam)
	var exclude := _player_rids(p)
	for h in held:
		for rid in _player_rids(h.root):
			if not exclude.has(rid):
				exclude.append(rid)
	var cam_pos := cam.global_position
	var vp_size := cam.get_viewport().get_visible_rect().size
	var feet_y := cam_pos.y
	var standing_on: Node = null
	var touching := []
	var touched := {}  # thing -> true: what the body is against
	var body := {}
	var view_yaw := Frame.yaw_of(cam.global_basis)
	var center_g := cam_pos
	var half_h := 0.9
	if p is Node3D:
		var bc: Array = world.body_center(p)
		center_g = bc[0]
		half_h = bc[1] if bc[1] > 0.0 else 0.9
		feet_y = bc[0].y - bc[1]
		var vel := World.velocity_of(p)
		body = {"velocity": Frame.kit(vel), "feet_z": Frame.r2(feet_y * Frame.SCALE), "crouched": false}
		if p is CharacterBody3D:
			body.on_ground = p.is_on_floor()
			body.falling = not p.is_on_floor()
			if p.is_on_floor():
				body.floor_slope_degrees = Frame.r2(rad_to_deg(p.get_floor_angle()))
			var seen := {}
			var add_contact := func(col, nrm: Vector3, way: float) -> void:
				if not (col is Node):
					return
				var t := thing_of(col)
				touched[t] = true
				if seen.has(t):
					return
				seen[t] = true
				touching.append({"thing": str(t.name), "path": str(t.get_path()), "yaw": Frame.r2(way),
					"turn": Frame.r2(Frame.wrap180(way - view_yaw)), "normal": Frame.kit_dir(nrm)})
			for i in p.get_slide_collision_count():
				var c: KinematicCollision3D = p.get_slide_collision(i)
				var col = c.get_collider()
				if not (col is Node):
					continue
				var nrm := c.get_normal()
				if nrm.angle_to(Vector3.UP) <= p.floor_max_angle + 0.01:
					standing_on = thing_of(col)
					continue
				add_contact.call(col, nrm, Frame.yaw_of_dir(-nrm))
			if not is_only:
				# What the body is against now: its own shape swept a hair (10 units) out all round, 8 ways, a little
				# off the floor (as Unreal's): what stops it, not the floor it stands on
				var from: Transform3D = p.global_transform
				from.origin += Vector3(0.0, 0.02, 0.0)
				for i in 8:
					var way := i * 45.0
					var col := KinematicCollision3D.new()
					if p.test_move(from, Frame.dir_of_yaw(way) * 0.11, col, 0.001, false):
						var nrm := col.get_normal()
						if nrm.angle_to(Vector3.UP) > p.floor_max_angle + 0.01:
							add_contact.call(col.get_collider(), nrm, way)
			if standing_on:
				body.standing_on = str(standing_on.name)
			if touching.size() > 0:
				body.touching = touching
		else:
			body.on_ground = null
			body.falling = null
	var me_k := Reach.K(center_g)
	var feet_k := feet_y * Frame.SCALE
	var cam_k := Reach.K(cam_pos)
	# Things: drawn parts grouped by thing
	var things := {}
	var s: Node = world.scene()
	if s == null:
		return [false, "No scene"]
	var stack: Array = [s]
	while stack.size() > 0:
		var n: Node = stack.pop_back()
		if n == p or n == cam or world.is_ours(n):
			continue
		stack.append_array(n.get_children())
		if not (n is GeometryInstance3D) or not n.is_visible_in_tree():
			continue
		var b: AABB = n.global_transform * n.get_aabb()
		if b.size.length() <= 0.0:
			continue
		var t := thing_of(n)
		if p != null and (t == p or p.is_ancestor_of(t)):
			continue
		if not only_set.is_empty() and not (only_set.has(str(t.name)) or only_set.has(str(t.get_path()))):
			continue
		if things.has(t):
			things[t].box = things[t].box.merge(b)
			things[t].parts.append(n)
		else:
			things[t] = {"box": b, "parts": [n]}
	# What the crosshair is on
	var crosshair: Node = null
	var fwd := -cam.global_basis.z
	var q := PhysicsRayQueryParameters3D.create(cam_pos, cam_pos + fwd * max_m, 0xFFFFFFFF, exclude)
	q.collide_with_areas = true
	var hit := space.intersect_ray(q)
	var crosshair_part := ""
	if hit and hit.collider is Node:
		crosshair = thing_of(hit.collider)
		crosshair_part = _bone_of(hit.collider, crosshair)
	# A place in sight (Unreal's Sees for a point): a line from the camera reaches it, nothing in the way short of it by
	# more than `slack` units
	var sees := func(at: Vector3, slack: float) -> bool:
		var rq := PhysicsRayQueryParameters3D.create(cam_pos, Reach.G(at), 0xFFFFFFFF, exclude)
		rq.collide_with_areas = false
		var r := space.intersect_ray(rq)
		return r.is_empty() or Reach.K(r.position).distance_to(cam_k) >= cam_k.distance_to(at) - slack
	# A place on a thing's top in view: on screen and in sight, just above the surface
	var sees_point := func(at: Vector3) -> bool:
		var above := at + Vector3(0.0, 0.0, 5.0)
		return cam.is_position_in_frustum(Reach.G(above)) and sees.call(above, 10.0)
	var place := func(at: Vector3) -> Dictionary:
		var bearing := rad_to_deg(atan2(at.y - cam_k.y, at.x - cam_k.x))
		return {"at": Reach.vec(at), "turn": roundf(Frame.wrap180(bearing - view_yaw)), "distance": roundf(Reach.dist2d(at, me_k)),
			"height_above_feet": roundf(at.z - feet_k)}
	var shows_cache := {}
	var shows := func(col) -> bool:
		if not (col is Node):
			return false
		var t := thing_of(col)
		if not shows_cache.has(t):
			var any := false
			for g in t.find_children("*", "GeometryInstance3D", true, false):
				if g.is_visible_in_tree():
					any = true
					break
			shows_cache[t] = any or (t is GeometryInstance3D and t.is_visible_in_tree())
		return shows_cache[t]
	var of_character := func(col) -> bool:
		var x = col
		var depth := 0
		while x is Node and depth < 8:
			if x is CharacterBody3D or x is PhysicalBone3D:
				return true
			x = x.get_parent()
			depth += 1
		return false
	var probe: Reach.Probe = _probe(p, space, exclude, me_k, feet_k)
	var radius_k: float = probe.radius if probe else 40.0
	var out := []
	var holders := []
	for t in things:
		var box: AABB = things[t].box
		if t == standing_on and box.size.x > 10.0 and box.size.z > 10.0:
			continue  # the ground
		if maxf(box.size.x, box.size.z) > floor_m and box.size.y < floor_m * 0.25:
			continue  # the ground (wider than floor_size and flat)
		var center := box.get_center()
		var nearest := Vector3(clampf(cam_pos.x, box.position.x, box.end.x), clampf(cam_pos.y, box.position.y, box.end.y), clampf(cam_pos.z, box.position.z, box.end.z))
		var dist := cam_pos.distance_to(nearest)
		if dist > max_m:
			continue
		var center_in := cam.is_position_in_frustum(center)
		var any_in := center_in
		if not any_in:
			for i in 8:
				if cam.is_position_in_frustum(box.get_endpoint(i)):
					any_in = true
					break
		if not any_in:
			continue
		var seen := false
		for target in [center, Vector3(center.x, box.end.y - 0.01, center.z), nearest]:
			var rq := PhysicsRayQueryParameters3D.create(cam_pos, target, 0xFFFFFFFF, exclude)
			rq.collide_with_areas = false
			var r := space.intersect_ray(rq)
			if r.is_empty() or _belongs(r.collider, t) or (r.position.distance_to(target) < 0.05):
				seen = true
				break
		if not seen:
			continue
		var item := {"name": str(t.name), "path": str(t.get_path()), "class": class_of(t), "center": Frame.kit(center),
			"size": {"X": Frame.r2(box.size.z * Frame.SCALE), "Y": Frame.r2(box.size.x * Frame.SCALE), "Z": Frame.r2(box.size.y * Frame.SCALE)},
			"yaw": Frame.r2(Frame.yaw_of(t.global_basis)) if t is Node3D else 0.0, "distance": Frame.r2(dist * Frame.SCALE),
			"top_above_feet": Frame.r2((box.end.y - feet_y) * Frame.SCALE), "bottom_above_feet": Frame.r2((box.position.y - feet_y) * Frame.SCALE),
			"crosshair_on": t == crosshair}
		if t == crosshair and crosshair_part != "":
			item.crosshair_part = crosshair_part  # the bone the crosshair is on (a ragdoll's or a hitbox's)
		if not cam.is_position_behind(center):
			var sp := cam.unproject_position(center)
			item.screen_x = Frame.r2(sp.x / vp_size.x * 2.0 - 1.0)
			item.screen_y = Frame.r2(1.0 - sp.y / vp_size.y * 2.0)
		if not center_in:
			item.partly_in_view = true
		var v := World.velocity_of(t)
		if v.length() > 0.01:
			item.speed = Frame.r2(v.length() * Frame.SCALE)
		if touched.has(t):
			item.blocks_player = true  # the player's body is against it: it stops the body
		var own := {}
		if t is Node3D:
			var rot := Frame.kit_rot(t.global_basis)
			if absf(rot.Pitch) >= 1.0 or absf(rot.Roll) >= 1.0:
				item.tilt = {"pitch": roundf(rot.Pitch), "roll": roundf(rot.Roll)}  # e.g. a ramp: a box tipped over
			own = Reach.own_box(t, things[t].parts)
			if not own.is_empty():
				holders.append([t, own])
				# its own box, when that isn't the box around it (it is turned): its middle, its size along its own axes
				# (kit X its forward -z, Y its x, Z its y) and how it is turned
				var world_c := Reach.K(box.get_center())
				var h: Vector3 = own.half
				var round_size := Vector3(box.size.z, box.size.x, box.size.y) * Frame.SCALE * 0.5
				if world_c.distance_to(own.center) > 1.0 or absf(h.z - round_size.x) > 0.5 or absf(h.x - round_size.y) > 0.5 \
						or absf(h.y - round_size.z) > 0.5:
					item.box = {"center": Reach.vec(own.center), "size": {"X": roundf(h.z * 2.0), "Y": roundf(h.x * 2.0), "Z": roundf(h.y * 2.0)},
						"rotation": {"pitch": snappedf(rot.Pitch, 0.1), "yaw": snappedf(rot.Yaw, 0.1), "roll": snappedf(rot.Roll, 0.1)}}
			if not is_only and (t is CharacterBody3D or not t.find_children("*", "Skeleton3D", true, false).is_empty()):
				_body_center(item, t, box, cam, space, exclude)
		var look := look_of(t)
		if not look.is_empty():
			item.look = look
			if look.has("texts"):
				item.label = look.texts[0]
		item["_t"] = t
		item["_own"] = own
		out.append(item)
	out.sort_custom(func(a, b): return a.distance < b.distance)
	if out.size() > max_count:
		out.resize(max_count)
	var listed := {}
	for item in out:
		var t: Node = item._t
		var own: Dictionary = item._own
		item.erase("_t")
		item.erase("_own")
		listed[t] = true
		if is_only or own.is_empty() or t is CharacterBody3D or t is PhysicalBone3D:
			continue
		var thing_rids := _player_rids(t)
		var box: AABB = things[t].box
		Reach.surface(item, {"space": space, "mask": probe.mask if probe else 0xFFFFFFFF, "beside": exclude + thing_rids,
			"thing_rids": thing_rids, "box": own, "world_top": box.end.y * Frame.SCALE, "world_bottom": box.position.y * Frame.SCALE,
			"tipped": item.has("tilt"), "sees_point": sees_point, "place": place, "shows": shows, "of_character": of_character,
			"body_height": half_h * 2.0 * Frame.SCALE, "step_up": maxf(probe.step if probe else 0.0, 10.0), "radius": radius_k,
			"cam_yaw": view_yaw})
	var hfov := cam.fov
	if cam.keep_aspect == Camera3D.KEEP_HEIGHT and vp_size.y > 0:
		hfov = rad_to_deg(2.0 * atan(tan(deg_to_rad(cam.fov) / 2.0) * vp_size.x / vp_size.y))
	var result := {"visible": out, "camera": {"location": Frame.kit(cam_pos), "yaw": Frame.r2(view_yaw),
		"pitch": Frame.r2(Frame.pitch_of(cam.global_basis)), "fov": Frame.r2(hfov), "vfov": Frame.r2(cam.fov)},
		"crosshair_tested": true, "oriented": true, "effects_play": DisplayServer.get_name() != "headless",
		"body": body, "omitted": OMITTED_3D}
	if is_only:
		result.only = only
	else:
		result.held = held_json(held, cam, vp_size)
		var sun := -1.0
		for light in s.find_children("*", "DirectionalLight3D", true, false):
			if light.is_visible_in_tree() and light.sky_mode != DirectionalLight3D.SKY_MODE_SKY_ONLY:
				sun = maxf(sun, light.light_energy)
		result.scene = {"sun": Frame.r2(sun)} if sun >= 0.0 else {}
	if p is Node3D and not is_only:
		# What is overhead: the first thing straight above the head, and the room up to it; told only when the player
		# perceives it (in view, against the body, or the head against it). With openings or ways up asked: `under`,
		# whatever is in view (a ceiling over a player inside a house)
		var head := center_g + Vector3(0.0, half_h, 0.0)
		var uq := PhysicsRayQueryParameters3D.create(head, head + Vector3(0.0, overhead_reach / Frame.SCALE, 0.0), 0xFFFFFFFF, exclude)
		uq.collide_with_areas = false
		var up_hit := space.intersect_ray(uq) if overhead_reach > 0.0 else {}
		if not up_hit.is_empty() and up_hit.collider is Node:
			var above := thing_of(up_hit.collider)
			var clearance: float = (up_hit.position.y - head.y) * Frame.SCALE
			if listed.has(above) or touched.has(above) or clearance <= 10.0:
				body.overhead = {"thing": str(above.name), "path": str(above.get_path()), "clearance": roundf(clearance)}
			if want_openings or want_ways_up:
				body.under = {"thing": str(above.name), "path": str(above.get_path()), "clearance": roundf(clearance)}
	if args.get("sonar", false) and p is PhysicsBody3D:
		body.sonar = sonar(p, args, view_yaw, space, exclude)
	if (want_openings or want_ways_up) and probe and not is_only:
		var name_of := func(col) -> String:
			return str(thing_of(col).name) if col is Node else ""
		Reach.add_openings(body, probe, cam_k, view_yaw, hfov, openings_reach, want_openings, want_ways_up, holders, sees, name_of)
	return [true, result]


## The bone a collider is (a ragdoll's PhysicalBone3D) or hangs from (a hitbox under a BoneAttachment3D), up to its
## thing; "" for none.
static func _bone_of(col: Node, thing: Node) -> String:
	var x: Node = col
	while x and x != thing:
		if x is PhysicalBone3D:
			return str(x.bone_name)
		if x is BoneAttachment3D:
			return str(x.bone_name)
		x = x.get_parent()
	return ""


## The middle of a character's body as seen from the camera (Unreal's body_center): rays through a 7 by 7 grid over its
## box, against its own shapes; the middle of where those that reach it unhidden hit, and how much of it shows
## (`body_shown`, when something hides part of it). Its box's middle is often off its body (a weapon held out).
func _body_center(item: Dictionary, t: Node, box: AABB, cam: Camera3D, space: PhysicsDirectSpaceState3D, exclude: Array) -> void:
	var own := _player_rids(t)
	if own.is_empty():
		return
	var right := cam.global_basis.x
	var up := cam.global_basis.y
	var half_w := maxf(box.size.x, box.size.z) * 0.5
	var c := box.get_center()
	var sum := Vector3.ZERO
	var hits := 0
	var shown := 0
	for i in 7:
		for j in 7:
			var through := c + right * ((i / 6.0 - 0.5) * 2.0 * half_w) + up * ((j / 6.0 - 0.5) * box.size.y)
			var to := cam.global_position + (through - cam.global_position).normalized() * (cam.global_position.distance_to(c) + box.size.length() * 2.0 + 1.0)
			var ex := exclude.duplicate()
			var hidden := false
			for k in 5:
				var q := PhysicsRayQueryParameters3D.create(cam.global_position, to, 0xFFFFFFFF, ex)
				q.collide_with_areas = true
				var r := space.intersect_ray(q)
				if r.is_empty():
					break
				if own.has(r.rid):
					hits += 1
					if not hidden:
						shown += 1
						sum += r.position
					break
				if not (r.collider is Area3D):
					hidden = true  # (something solid in front of it there)
				ex.append(r.rid)
	if shown > 0:
		item.body_center = Frame.kit(sum / shown)
		if shown < hits:
			item.body_shown = snappedf(float(shown) / hits, 0.01)


## The player's body as its own probes see it (Reach.Probe): its shape's size, what stops it, how steep a floor and how
## high a lip it walks up. null without a CharacterBody3D.
func _probe(p: Node, space: PhysicsDirectSpaceState3D, exclude: Array, me_k: Vector3, feet_k: float):
	if not (p is CharacterBody3D):
		return null
	var probe := Reach.Probe.new()
	probe.space = space
	probe.exclude = exclude
	probe.mask = p.collision_mask
	probe.me = me_k
	probe.feet = feet_k
	var cs := World.body_shape(p)
	var scale: float = cs.global_basis.get_scale().y if cs else 1.0
	var size: Array = World.shape_size(cs.shape) if cs else [null, null]
	if size[0] != null:
		probe.half_height = size[0] * scale * Frame.SCALE
		probe.radius = size[1] * scale * Frame.SCALE
	var slope: float = p.floor_max_angle
	probe.walkable_z = cos(slope)
	probe.tan_slope = tan(minf(slope, deg_to_rad(80.0)))
	# what its rounded bottom rides up at once: where a lip meets the round no steeper than the floor it walks
	if cs and (cs.shape is CapsuleShape3D or cs.shape is SphereShape3D):
		probe.step = probe.radius * (1.0 - cos(slope))
	return probe


## What the player holds, as Unreal's `held`: what hangs from the camera (a first-person weapon or tool: each of the
## camera's children), what is fixed onto a bone of its body (a BoneAttachment3D's children: `attached_at` the bone),
## and physics bodies carried under it (an object picked up). Only those with something drawn now. [{"root", "at"}].
func held_things(p: Node, cam: Camera3D) -> Array:
	var out := []
	if cam:
		for c in cam.get_children():
			if c is Node3D and not world.is_ours(c):
				out.append({"root": c, "at": ""})
	if p is Node3D:
		var stack: Array = p.get_children()
		while stack.size() > 0:
			var n: Node = stack.pop_back()
			if n == cam:
				continue
			if n is BoneAttachment3D:
				for c in n.get_children():
					if c is Node3D:
						out.append({"root": c, "at": str(n.bone_name)})
				continue
			if n is CollisionObject3D:
				out.append({"root": n, "at": ""})
				continue
			stack.append_array(n.get_children())
	var drawn := []
	for h in out:
		var parts := []
		for g in [h.root] + h.root.find_children("*", "GeometryInstance3D", true, false):
			if g is GeometryInstance3D and g.is_visible_in_tree():
				parts.append(g)
		if parts.size() > 0:
			h.parts = parts
			drawn.append(h)
	return drawn


## held_things as the answer has them: each thing's name, path, class, `look.parts` (its meshes, biggest first),
## `attached_at`, where its drawn parts are (center, size), how far the camera is from them, where on screen, and
## whether any of it is in the frustum; nearest the camera first.
func held_json(held: Array, cam: Camera3D, vp_size: Vector2) -> Array:
	var out := []
	for h in held:
		var box := AABB()
		var have := false
		var sizes := {}
		for g in h.parts:
			var b: AABB = g.global_transform * g.get_aabb()
			box = b if not have else box.merge(b)
			have = true
			var name := _part_name(g)
			if name != "":
				sizes[name] = maxf(sizes.get(name, 0.0), b.size.length())
		var names := sizes.keys()
		names.sort_custom(func(a, b): return sizes[a] > sizes[b] if sizes[a] != sizes[b] else a < b)
		var cam_pos := cam.global_position
		var nearest := Vector3(clampf(cam_pos.x, box.position.x, box.end.x), clampf(cam_pos.y, box.position.y, box.end.y), clampf(cam_pos.z, box.position.z, box.end.z))
		var item := {"name": str(h.root.name), "path": str(h.root.get_path()), "class": class_of(h.root),
			"look": {"parts": names}, "center": Frame.kit(box.get_center()),
			"size": {"X": Frame.r2(box.size.z * Frame.SCALE), "Y": Frame.r2(box.size.x * Frame.SCALE), "Z": Frame.r2(box.size.y * Frame.SCALE)},
			"distance": Frame.r2(cam_pos.distance_to(nearest) * Frame.SCALE)}
		if h.at != "":
			item.attached_at = h.at
		var c := box.get_center()
		if not cam.is_position_behind(c):
			var sp := cam.unproject_position(c)
			item.screen_x = Frame.r2(sp.x / vp_size.x * 2.0 - 1.0)
			item.screen_y = Frame.r2(1.0 - sp.y / vp_size.y * 2.0)
		var in_view := cam.is_position_in_frustum(c)
		for i in 8:
			if in_view:
				break
			in_view = cam.is_position_in_frustum(box.get_endpoint(i))
		item.in_view = in_view
		out.append(item)
	out.sort_custom(func(a, b): return a.distance < b.distance if a.distance != b.distance else a.name < b.name)
	return out


## A drawn part's name, as `look.parts` has it: its mesh's file name, else the mesh's own name, else its class.
static func _part_name(g: Node) -> String:
	if g is MeshInstance3D and g.mesh:
		var m: Mesh = g.mesh
		if m.resource_path != "" and not ("::" in m.resource_path):
			return m.resource_path.get_file().get_basename()
		return m.resource_name if m.resource_name != "" else m.get_class()
	return str(g.name)


## How a thing is drawn now, whole (what `look` and `watch` tell from): {"parts": its visible meshes' (2D: sprites')
## names, sorted; "lights": how many are on, "brightness"; "anims": the animations playing (AnimationPlayer, an
## AnimationTree's state machine, an AnimatedSprite); "texts": its 3D texts; "effects": {name: scale} of its particles
## emitting; "highlighted": a mesh drawn with an overlay material (an outline or a glow over it); "limp": a ragdoll
## (most of its physical bones simulating)}.
func drawn_of(t: Node) -> Dictionary:
	var parts := {}
	var lights := 0
	var brightest := 0.0
	var anims := []
	var texts := []
	var effects := {}
	var highlighted := false
	var limp := false
	var stack: Array = [t]
	while stack.size() > 0:
		var n: Node = stack.pop_back()
		stack.append_array(n.get_children())
		var shown: bool = n.is_visible_in_tree() if (n is Node3D or n is CanvasItem) else true
		if not shown:
			continue
		if (n is GPUParticles3D or n is CPUParticles3D or n is GPUParticles2D or n is CPUParticles2D):
			if n.emitting:
				var e := _effect_name(n)
				var sc: Vector2 = Vector2(n.global_basis.get_scale().abs()[n.global_basis.get_scale().abs().max_axis_index()], 0.0) \
					if n is Node3D else n.global_scale.abs()
				effects[e] = maxf(effects.get(e, 0.0), Frame.r2(maxf(sc.x, sc.y)))
			continue
		if n is GeometryInstance3D and n.material_overlay != null:
			highlighted = true
		if n is MeshInstance3D and n.mesh:
			parts[_part_name(n)] = true
		elif n is Sprite2D or n is Sprite3D:
			parts[n.texture.resource_path.get_file().get_basename() if n.texture and n.texture.resource_path != "" \
				and not ("::" in n.texture.resource_path) else str(n.name)] = true
		elif n is AnimatedSprite2D or n is AnimatedSprite3D:
			parts[str(n.name)] = true
			if n.is_playing():
				anims.append(str(n.animation))
		elif n is Polygon2D or n is MeshInstance2D:
			parts[str(n.name)] = true
		elif (n is Light3D and n.light_energy > 0.0) or (n is Light2D and n.enabled and n.energy > 0.0):
			lights += 1
			brightest = maxf(brightest, n.light_energy if n is Light3D else n.energy)
		elif n is AnimationPlayer and n.is_playing():
			anims.append(str(n.current_animation))
		elif n is AnimationTree and n.active:
			var playback = n.get("parameters/playback")
			if playback is AnimationNodeStateMachinePlayback and str(playback.get_current_node()) != "":
				anims.append(str(playback.get_current_node()))
		elif n is Label3D and n.text != "" and texts.size() < 4:
			texts.append(n.text.substr(0, 40))
		elif n is PhysicalBoneSimulator3D and n.active:
			var bones := n.find_children("*", "PhysicalBone3D", true, false)
			var simulating := 0
			for pb in bones:
				if pb.is_simulating_physics():
					simulating += 1
			if bones.size() > 0 and simulating * 2 >= bones.size():
				limp = true
	var names := parts.keys()
	names.sort()
	return {"parts": names, "lights": lights, "brightness": brightest, "anims": anims, "texts": texts, "effects": effects,
		"highlighted": highlighted, "limp": limp}


## How a thing is drawn, as `view` tells it (`look`): its visible meshes' names, its lights on, the animation playing,
## its 3D texts, its effects playing ({"asset", "scale"}), `highlighted` and `limp` when so.
func look_of(t: Node) -> Dictionary:
	var d := drawn_of(t)
	var look := {}
	if d.parts.size() > 0:
		look.parts = d.parts.slice(0, 8)
	if d.lights > 0:
		look.lights = {"on": d.lights, "brightness": Frame.r2(d.brightness)}
	if d.anims.size() > 0:
		look.animation = d.anims[0]
	if d.texts.size() > 0:
		look.texts = d.texts
	if d.effects.size() > 0:
		var names: Array = d.effects.keys()
		names.sort()
		var list := []
		for e in names:
			list.append({"asset": e, "scale": d.effects[e]})
		look.effects = list
	if d.highlighted:
		look.highlighted = true
	if d.limp:
		look.limp = true
	return look


## An effect's name: the scene it was saved as (an effect made as its own scene), else the node's name.
static func _effect_name(n: Node) -> String:
	if n.scene_file_path != "":
		return n.scene_file_path.get_file().get_basename()
	if n.owner and n.owner.scene_file_path != "" and n.owner != n.get_tree().current_scene:
		return n.owner.scene_file_path.get_file().get_basename()
	return str(n.name)


func sonar(p: PhysicsBody3D, args: Dictionary, view_yaw: float, space: PhysicsDirectSpaceState3D, exclude: Array) -> Dictionary:
	# (a CharacterBody3D walks floors up to its floor_max_angle; another body, a rigid one, 45 degrees)
	var max_angle: float = p.floor_max_angle if p is CharacterBody3D else deg_to_rad(45.0)
	var reach := clampf(float(args.get("sonar_reach", 1500.0)), 50.0, 5000.0) / Frame.SCALE
	var turns: Array = args.get("sonar_turns", [0, -30, 30, -60, 60, -90, 90, -135, 135, 180])
	var bc: Array = world.body_center(p)
	var hh: float = bc[1]
	var shape_offset: Vector3 = bc[0] - p.global_position
	var ways := []
	var step := 0.5
	for turn in turns.slice(0, 16):
		var yaw := view_yaw + float(turn)
		var dir := Frame.dir_of_yaw(yaw)
		var t := p.global_transform
		t.origin += Vector3(0, 0.1, 0)
		var travelled := 0.0
		var way := {"turn": float(turn), "by": "reach"}
		var rise := 0.0
		var start_y := t.origin.y
		var guard := 0
		while travelled < reach and guard < 64:
			guard += 1
			var col := KinematicCollision3D.new()
			var move := dir * minf(step, reach - travelled)
			if p.test_move(t, move, col, 0.001, false):
				var n := col.get_normal()
				if n.angle_to(Vector3.UP) <= max_angle and guard < 63:
					t.origin += Vector3(0, 0.25, 0)  # walkable rise: go up it
					continue
				travelled += col.get_travel().length()
				way.by = "wall"
				var c = col.get_collider()
				if c is Node:
					way.thing = str(thing_of(c).name)
				way.normal = {"X": Frame.r2(-n.z), "Y": Frame.r2(n.x)}
				break
			t.origin += move
			travelled += move.length()
			var center := t.origin + shape_offset
			var rq := PhysicsRayQueryParameters3D.create(center, center - Vector3(0, hh + 30.0, 0), 0xFFFFFFFF, exclude)
			var r := space.intersect_ray(rq)
			var below: float = (center.y - hh) - r.position.y if r else 31.0
			if below > 0.6:
				way.by = "drop"
				way.drop = Frame.r2(below * Frame.SCALE) if below < 30.0 else null
				# the edge, to within a 32nd of a step: where the floor under the body's middle ends
				var lo := 0.0
				var hi := move.length()
				var back := center - move
				for _i in 5:
					var mid := (lo + hi) / 2.0
					var at := back + dir * mid
					var mq := PhysicsRayQueryParameters3D.create(at, at - Vector3(0, hh + 0.6, 0), 0xFFFFFFFF, exclude)
					if space.intersect_ray(mq):
						lo = mid
					else:
						hi = mid
				travelled -= move.length() - lo
				break
			t.origin.y = r.position.y + hh - shape_offset.y + 0.1
		way.clear = Frame.r2(travelled * Frame.SCALE)
		rise = t.origin.y - start_y
		if absf(rise) > 0.05:
			way.rise = Frame.r2(rise * Frame.SCALE)
		ways.append(way)
	return {"ways": ways, "reach": Frame.r2(reach * Frame.SCALE)}


func view2d(args: Dictionary) -> Array:
	var p: Node = world.player()
	var vp: Viewport = host.get_viewport()
	var canvas := vp.get_canvas_transform()
	var screen := vp.get_visible_rect()
	var world_rect := canvas.affine_inverse() * screen
	var max_count := int(args.get("max_count", 24))
	var max_d := float(args.get("max_distance", 5000.0))
	var only = args.get("only", null)
	var only_set := {}
	if typeof(only) == TYPE_ARRAY:
		for x in only:
			only_set[str(x)] = true
	var origin: Vector2 = world.body_center(p)[0] if p is Node2D else world_rect.get_center()
	var things := {}
	var s: Node = world.scene()
	var stack: Array = [s]
	while stack.size() > 0:
		var n: Node = stack.pop_back()
		if n == p or world.is_ours(n) or n is CanvasLayer:
			continue
		stack.append_array(n.get_children())
		if not (n is Node2D) or not n.is_visible_in_tree():
			continue
		if not (n is Sprite2D or n is AnimatedSprite2D or n is Polygon2D or n is MeshInstance2D or n is Line2D):
			continue
		var t := thing_of(n)
		if p != null and (t == p or p.is_ancestor_of(t)):
			continue
		if not only_set.is_empty() and not (only_set.has(str(t.name)) or only_set.has(str(t.get_path()))):
			continue
		things[t] = true
	var out := []
	for t in things:
		var r: Rect2 = World.bounds2d(t)
		if not world_rect.intersects(r, true) and not world_rect.has_point(r.position):
			continue
		var c := r.get_center()
		var d := origin.distance_to(c)
		if d > max_d:
			continue
		var sp := canvas * c
		var item := {"name": str(t.name), "path": str(t.get_path()), "class": class_of(t), "center": Frame.kit2d(c),
			"size": {"X": Frame.r2(r.size.x), "Y": 0.0, "Z": Frame.r2(r.size.y)}, "distance": Frame.r2(d),
			"screen_x": Frame.r2(sp.x / screen.size.x * 2.0 - 1.0), "screen_y": Frame.r2(1.0 - sp.y / screen.size.y * 2.0)}
		if not world_rect.encloses(r):
			item.partly_in_view = true
		out.append(item)
	out.sort_custom(func(a, b): return a.distance < b.distance)
	if out.size() > max_count:
		out.resize(max_count)
	var body := {}
	if p is Node2D:
		body = {"velocity": Frame.kit2d(World.velocity2d_of(p)), "crouched": false}
		if p is CharacterBody2D:
			body.on_ground = p.is_on_floor()
			body.falling = not p.is_on_floor()
	var result := {"visible": out, "camera": {"location": Frame.kit2d(world_rect.get_center()), "yaw": 0.0, "pitch": 0.0, "fov": null},
		"effects_play": DisplayServer.get_name() != "headless", "body": body, "omitted": OMITTED_2D}
	var sun := -1.0
	for light in s.find_children("*", "DirectionalLight2D", true, false):
		if light.is_visible_in_tree():
			sun = maxf(sun, light.energy)
	result.scene = {"sun": Frame.r2(sun)} if sun >= 0.0 else {}
	if typeof(only) == TYPE_ARRAY:
		result.only = only
	return [true, result]


# --- watch ------------------------------------------------------------------------------------------

## The state a watch starts from: per object its local transform (and with `drawn`, its parts' transforms in its own
## frame and how it is drawn). tick() samples; finish() gives the changes.
func watch_start(args: Dictionary, resolve: Callable) -> Dictionary:
	var drawn: bool = args.get("drawn", false)
	var parts_count := clampi(int(args.get("parts", 3)), 0, 16)
	var entries := []
	var looks := []
	for x in args.get("objects", []):
		var path := str(x)
		var n = resolve.call(path)
		if not (n is Node3D or n is Node2D or n is Control):
			continue
		entries.append({"node": n, "thing": null, "path": path, "first": null, "found": {}})
		if drawn and n is Node2D:
			looks.append({"node": n, "path": path, "last": null, "found": []})
		if drawn and n is Node3D:
			var meshes := []
			for m in n.find_children("*", "GeometryInstance3D", true, false):
				if m.is_visible_in_tree():
					meshes.append(m)
			meshes.sort_custom(func(a, b): return a.get_aabb().size.length() > b.get_aabb().size.length())
			for m in meshes.slice(0, parts_count):
				entries.append({"node": m, "thing": n, "path": path + "/" + str(n.get_path_to(m)), "first": null, "found": {}})
			looks.append({"node": n, "path": path, "last": null, "found": []})
	return {"entries": entries, "looks": looks, "drawn": drawn, "turned": float(args.get("turned", 1.0)),
		"moved": float(args.get("moved", 2.0)), "resized": float(args.get("resized", 0.02))}


func _sample(e: Dictionary) -> Array:
	var n = e.node
	if not is_instance_valid(n):
		return []
	if n is Node3D:
		var tr: Transform3D = n.transform
		if e.thing != null:
			if not is_instance_valid(e.thing):
				return []
			tr = e.thing.global_transform.affine_inverse() * n.global_transform
		return [tr.basis.get_rotation_quaternion(), tr.origin * Frame.SCALE, tr.basis.get_scale()]
	if n is Node2D:
		return [Quaternion(Vector3.FORWARD, n.rotation), Vector3(n.position.x, n.position.y, 0), Vector3(n.scale.x, n.scale.y, 1)]
	if n is Control:
		return [Quaternion(Vector3.FORWARD, n.rotation), Vector3(n.position.x, n.position.y, 0), Vector3(n.scale.x, n.scale.y, 1)]
	return []


func watch_tick(st: Dictionary, t: float) -> void:
	for e in st.entries:
		var s: Array = _sample(e)
		if s.is_empty():
			continue
		if e.first == null:
			e.first = s
			continue
		var f: Array = e.first
		if not e.found.has("turned") and rad_to_deg(f[0].angle_to(s[0])) > st.turned:
			e.found["turned"] = t
		if not e.found.has("moved") and f[1].distance_to(s[1]) > st.moved:
			e.found["moved"] = t
		if not e.found.has("changed size"):
			var base: Vector3 = f[2].abs().max(Vector3(0.001, 0.001, 0.001))
			var rel: Vector3 = (s[2] - f[2]).abs() / base
			if maxf(rel.x, maxf(rel.y, rel.z)) > st.resized:
				e.found["changed size"] = t
	for l in st.looks:
		if not is_instance_valid(l.node):
			continue
		var cur := drawn_of(l.node)
		if l.last != null:
			var notes := [["parts", "part shown", "part hidden"], ["effects", "effect started", "effect stopped"],
				["anims", "animation started", "animation ended"]]
			for note in notes:
				var now_has = cur[note[0]]
				var had = l.last[note[0]]
				for x in now_has:
					if not had.has(x):
						l.found.append({"t": t, "what": note[1], "asset": x})
				for x in had:
					if not now_has.has(x):
						l.found.append({"t": t, "what": note[2], "asset": x})
			if cur.lights != l.last.lights:
				l.found.append({"t": t, "what": "light on" if cur.lights > l.last.lights else "light off", "asset": ""})
			if cur.limp and not l.last.limp:
				l.found.append({"t": t, "what": "went limp", "asset": ""})
		l.last = cur


func watch_finish(st: Dictionary) -> Dictionary:
	var changes := []
	var seen := {}
	for e in st.entries:
		for what in e.found:
			var key: String = e.path + "|" + what
			if seen.has(key):
				continue
			seen[key] = true
			changes.append({"t": Frame.r2(e.found[what]), "object": e.path, "what": what})
	for l in st.looks:
		for f in l.found:
			changes.append({"t": Frame.r2(f.t), "object": l.path, "what": f.what, "asset": f.asset})
	changes.sort_custom(func(a, b): return a.t < b.t)
	var out := {"changes": changes}
	if st.drawn:
		out.drawn = true
	return out
