--!strict
-- What the player sees and feels (`view`), and things watched over time (`watch`), in the kit frame.
--
-- view: the Workspace's things (World.things: models and parts, a character being one) with a drawn part inside the
-- camera's view that a line of sight from the camera reaches (the box's centre, top or nearest point), nearest
-- first; the player, what it holds and the ground it stands on left out. Each with how it is drawn (look: parts,
-- lights, animation, sounds, effects, texts, highlighted, limp), whether the body is against it (blocks_player), how it
-- is tipped (tilt, its own box) and for a character where its body shows (body_center). Plus what the player holds
-- (`held`), the sun (`scene.sun`), the camera, the body (on_ground, falling, standing_on, touching, floor slope, the
-- Humanoid's state and health, overhead; with openings or ways_up asked, under) and, asked for, `openings` / `free`
-- and `ways_up` (Reach.luau) and `sonar` (how far the body could walk each way: sweeps of a box the body's size above
-- its step height, rays down for drops), and each thing's top surface (top_slope_degrees; for a walkable slope
-- low_end, high_end, lane, uphill: Reach.surface).
local Lighting = game:GetService("Lighting")
local Players = game:GetService("Players")

local Frame = require(script.Parent.Frame)
local Json = require(script.Parent.Json)
local Paths = require(script.Parent.Paths)
local Reach = require(script.Parent.Reach)
local World = require(script.Parent.World)

local Sight = {}

local r2 = Frame.r2
local S = Frame.SCALE

Sight.OMITTED = {}

-- Whether a thing has a part a player can see (not fully transparent), and its parts (at most `limit`).
local function drawnParts(thing: Instance, limit: number): { BasePart }
	local out = {}
	if thing:IsA("BasePart") then
		if thing.Transparency < 0.99 then
			table.insert(out, thing)
		end
		return out
	end
	for _, d in ipairs(thing:GetDescendants()) do
		if d:IsA("BasePart") and d.Transparency < 0.99 then
			table.insert(out, d)
			if #out >= limit then
				break
			end
		end
	end
	return out
end

-- The two parts a joint holds together (a Weld, Motor6D, WeldConstraint or RigidConstraint), or nil.
local function jointParts(j: Instance): (Instance?, Instance?)
	if j:IsA("JointInstance") or j:IsA("WeldConstraint") then
		return (j :: any).Part0, (j :: any).Part1
	elseif j:IsA("RigidConstraint") then
		local a0, a1 = (j :: RigidConstraint).Attachment0, (j :: RigidConstraint).Attachment1
		return a0 and a0.Parent, a1 and a1.Parent
	end
	return nil, nil
end

-- The hand an equipped Tool is gripped in (the character's RightGrip weld), else the right hand's name.
local function gripHand(char: Model): string
	for _, d in ipairs(char:GetDescendants()) do
		if d:IsA("JointInstance") and d.Name == "RightGrip" then
			local p0 = (d :: JointInstance).Part0
			if p0 then
				return p0.Name
			end
		end
	end
	return if char:FindFirstChild("RightHand") then "RightHand" else "Right Arm"
end

-- What the player holds, as Unreal's `held`: an equipped Tool in the character (`at` the hand gripping it), the things
-- outside the character welded onto it (a Weld, WeldConstraint, Motor6D or RigidConstraint: a thing carried; `at` the
-- character's part it is fixed to), and the camera's children (a first-person viewmodel). Not a seat the player sits
-- in, nothing anchored, not the character's Accessories. Only those with something drawn. {{root, at, parts}}.
function Sight.heldThings(): { any }
	local out = {}
	local seen = {}
	local function add(root: Instance, at: string)
		if seen[root] then
			return
		end
		seen[root] = true
		local parts = drawnParts(root, 64)
		if #parts > 0 then
			table.insert(out, { root = root, at = at, parts = parts })
		end
	end
	local cam = workspace.CurrentCamera
	if cam then
		for _, c in ipairs(cam:GetChildren()) do
			if (c:IsA("Model") or c:IsA("BasePart")) and not World.isOurs(c) then
				add(c, "")
			end
		end
	end
	local char = World.character()
	if char == nil then
		return out
	end
	for _, c in ipairs(char:GetChildren()) do
		if c:IsA("Tool") then
			add(c, gripHand(char))
		end
	end
	local root = World.root()
	if root == nil then
		return out
	end
	local ok, connected = pcall(function()
		return root:GetConnectedParts(true)
	end)
	if not ok then
		return out
	end
	local groups, order = {}, {}
	for _, part in ipairs(connected :: { BasePart }) do
		if part:IsDescendantOf(char) or part:IsA("Terrain") then
			continue
		end
		local thing = World.thingOf(part) or part
		if thing == char or char:IsDescendantOf(thing) then
			continue
		end
		if groups[thing] == nil then
			groups[thing] = { carried = true }
			table.insert(order, thing)
		end
		if part.Anchored or part:IsA("Seat") or part:IsA("VehicleSeat") then
			groups[thing].carried = false -- (the player sits in it, or is fixed to the world)
		end
	end
	for _, thing in ipairs(order) do
		if not groups[thing].carried then
			continue
		end
		-- the character's part it is fixed to: a joint in it or in the character between the two
		local at = ""
		local joints = thing:GetDescendants()
		table.insert(joints, thing)
		for _, d in ipairs(char:GetDescendants()) do
			table.insert(joints, d)
		end
		for _, j in ipairs(joints) do
			local p0, p1 = jointParts(j)
			if p0 and p1 then
				local in0, in1 = p0:IsDescendantOf(char), p1:IsDescendantOf(char)
				local of0 = p0 == thing or p0:IsDescendantOf(thing)
				local of1 = p1 == thing or p1:IsDescendantOf(thing)
				if in0 and of1 then
					at = p0.Name
					break
				elseif in1 and of0 then
					at = p1.Name
					break
				end
			end
		end
		add(thing, at)
	end
	return out
end

-- Raycast params that leave out the player's character, what it holds (heldThings, or `held`) and the camera; with
-- `respectCollide`, as the body collides (CanCollide, the character's CollisionGroup).
function Sight.params(respectCollide: boolean?, held: { any }?): RaycastParams
	local p = RaycastParams.new()
	p.FilterType = Enum.RaycastFilterType.Exclude
	local ignore = {}
	local char = World.character()
	if char then
		table.insert(ignore, char)
	end
	local cam = workspace.CurrentCamera
	if cam then
		table.insert(ignore, cam)
	end
	for _, h in ipairs(held or Sight.heldThings()) do
		table.insert(ignore, h.root)
	end
	p.FilterDescendantsInstances = ignore
	p.RespectCanCollide = respectCollide == true
	p.IgnoreWater = true
	if respectCollide then
		local root = World.root()
		if root then
			p.CollisionGroup = root.CollisionGroup
		end
	end
	return p
end

local function humanoidOf(thing: Instance): Humanoid?
	if thing:IsA("Model") then
		return thing:FindFirstChildOfClass("Humanoid")
	end
	return nil
end

-- Whether a Highlight or an adornment (SelectionBox, a handle adornment) shows now, and what it is drawn on.
local function adornment(d: Instance): Instance?
	if d:IsA("Highlight") then
		local h = d :: Highlight
		if h.Enabled and (h.FillTransparency < 1 or h.OutlineTransparency < 1) then
			return h.Adornee or h.Parent
		end
	elseif d:IsA("PVAdornment") or d:IsA("InstanceAdornment") then
		-- (handle adornments are PVAdornments; SelectionBox and SelectionSphere are InstanceAdornments)
		local a = d :: any
		if a.Visible and a.Transparency < 1 then
			return a.Adornee
		end
	end
	return nil
end

-- What the Highlights and adornments outside the Workspace's things are drawn on (the player's PlayerGui, the camera,
-- the Workspace's top): {instance}.
function Sight.highlightedOutside(): { Instance }
	local out = {}
	local places: { Instance } = {}
	local lp = Players.LocalPlayer
	local gui = lp and lp:FindFirstChildOfClass("PlayerGui")
	if gui then
		table.insert(places, gui)
	end
	if workspace.CurrentCamera then
		table.insert(places, workspace.CurrentCamera)
	end
	for _, place in ipairs(places) do
		for _, d in ipairs(place:GetDescendants()) do
			local on = adornment(d)
			if on then
				table.insert(out, on)
			end
		end
	end
	for _, d in ipairs(workspace:GetChildren()) do
		local on = adornment(d)
		if on then
			table.insert(out, on)
		end
	end
	return out
end

local function within(inst: Instance, thing: Instance): boolean
	return inst == thing or inst:IsDescendantOf(thing)
end

-- An effect's size: the scale of the model it is in (Model:ScaleTo scales its effects too), else 1.
local function scaleOf(d: Instance): number
	local m = d:FindFirstAncestorWhichIsA("Model")
	if m and m ~= workspace then
		local ok, s = pcall(function()
			return (m :: Model):GetScale()
		end)
		if ok and type(s) == "number" then
			return r2(s)
		end
	end
	return 1
end

local LIMP_STATES = { [Enum.HumanoidStateType.Physics] = true, [Enum.HumanoidStateType.Ragdoll] = true,
	[Enum.HumanoidStateType.FallingDown] = true }

-- How a thing is drawn: its visible parts' names, the lights on, the animations playing, its texts (SurfaceGui,
-- BillboardGui), sounds playing, effects emitting ({"asset", "scale"}, by name), whether a Highlight or an adornment is
-- on it (`highlighted`; `extra`: what those outside it are on) and whether it went limp (a ragdoll: its Humanoid in
-- the Physics, Ragdoll or FallingDown state, or most of its Motor6Ds off with BallSocketConstraints on).
function Sight.lookOf(thing: Instance, extra: { Instance }?): any
	local parts, lights, brightest, anims, texts, sounds, effects = {}, 0, 0, {}, {}, {}, {}
	local highlighted = false
	local motors, motorsOff, sockets = 0, 0, 0
	local list = if thing:IsA("BasePart") then { thing } else thing:GetDescendants()
	if thing:IsA("BasePart") then
		for _, d in ipairs(thing:GetDescendants()) do
			table.insert(list, d)
		end
	end
	for _, d in ipairs(list) do
		if d:IsA("BasePart") then
			if d.Transparency < 0.99 then
				parts[d.Name] = true
			end
		elseif d:IsA("Light") then
			if d.Enabled and d.Brightness > 0 then
				lights += 1
				brightest = math.max(brightest, d.Brightness)
			end
		elseif d:IsA("TextLabel") or d:IsA("TextButton") or d:IsA("TextBox") then
			local gui = d:FindFirstAncestorWhichIsA("LayerCollector")
			if d.Visible and d.Text ~= "" and gui and gui.Enabled and #texts < 4 then
				table.insert(texts, string.sub(d.Text, 1, 40))
			end
		elseif d:IsA("Sound") then
			if d.IsPlaying and #sounds < 4 then
				table.insert(sounds, d.Name)
			end
		elseif d:IsA("ParticleEmitter") or d:IsA("Fire") or d:IsA("Smoke") or d:IsA("Sparkles") or d:IsA("Beam")
			or d:IsA("Trail") then
			if (d :: any).Enabled then
				effects[d.Name] = math.max(effects[d.Name] or 0, scaleOf(d))
			end
		elseif d:IsA("Animator") then
			local ok, tracks = pcall(function()
				return (d :: Animator):GetPlayingAnimationTracks()
			end)
			if ok then
				for _, t in ipairs(tracks) do
					local a = t.Animation
					table.insert(anims, if a then a.Name else t.Name)
				end
			end
		elseif d:IsA("Motor6D") then
			motors += 1
			if not (d :: Motor6D).Enabled then
				motorsOff += 1
			end
		elseif d:IsA("BallSocketConstraint") then
			if (d :: BallSocketConstraint).Enabled then
				sockets += 1
			end
		elseif not highlighted then
			local on = adornment(d)
			if on and within(on, thing) then
				highlighted = true
			end
		end
	end
	if not highlighted and extra then
		for _, on in ipairs(extra) do
			if within(on, thing) then
				highlighted = true
				break
			end
		end
	end
	local hum = humanoidOf(thing)
	local limp = (hum ~= nil and LIMP_STATES[hum:GetState()] == true)
		or (motors > 0 and motorsOff * 2 >= motors and sockets > 0)
	local look: any = {}
	local names = {}
	for n in pairs(parts) do
		table.insert(names, n)
	end
	table.sort(names)
	if #names > 0 then
		look.parts = { table.unpack(names, 1, math.min(8, #names)) }
	end
	if lights > 0 then
		look.lights = { on = lights, brightness = r2(brightest) }
	end
	if #anims > 0 then
		look.animation = anims[1]
		if #anims > 1 then
			look.animations = anims
		end
	end
	if #texts > 0 then
		look.texts = texts
	end
	if #sounds > 0 then
		look.sounds = sounds
	end
	local effectNames = {}
	for e in pairs(effects) do
		table.insert(effectNames, e)
	end
	table.sort(effectNames)
	if #effectNames > 0 then
		look.effects = {}
		for _, e in ipairs(effectNames) do
			table.insert(look.effects, { asset = e, scale = effects[e] })
		end
	end
	if highlighted then
		look.highlighted = true
	end
	if limp then
		look.limp = true
	end
	return look
end

local function classOf(thing: Instance): string
	return thing.ClassName
end

-- The floor under the player: (thing, normal, distance below the feet) or nil.
local function floorUnder(center: Vector3, half: number, params: RaycastParams): (Instance?, Vector3?, number?)
	local hit = workspace:Raycast(center, Vector3.new(0, -(half + 6), 0), params)
	if hit then
		return World.thingOf(hit.Instance) or hit.Instance, hit.Normal, (center.Y - half) - hit.Position.Y
	end
	return nil, nil, nil
end

-- The body (its numbers, what it stands on and is against), and the set of things it is against.
function Sight.body(center: Vector3, half: number, viewYaw: number, held: { any }?): (any, { [Instance]: boolean })
	local root = World.root()
	local h = World.humanoid()
	local params = Sight.params(true, held)
	local vel = if root then root.AssemblyLinearVelocity else Vector3.zero
	local body: any = { velocity = Frame.kit(vel), feet_z = r2((center.Y - half) * S), crouched = false }
	local floorThing, normal = floorUnder(center, half, params)
	local touched: { [Instance]: boolean } = {}
	if h then
		local state = h:GetState()
		body.state = state.Name
		body.on_ground = h.FloorMaterial ~= Enum.Material.Air
		body.falling = state == Enum.HumanoidStateType.Freefall
		body.health = r2(h.Health)
		body.max_health = r2(h.MaxHealth)
	end
	if floorThing and body.on_ground ~= false then
		body.standing_on = (floorThing :: Instance).Name
		if normal then
			body.floor_slope_degrees = r2(math.deg(math.acos(math.clamp(normal.Y, -1, 1))))
		end
	end
	-- Touching: what overlaps a box a little larger than the body, but the floor
	if root then
		local op = OverlapParams.new()
		op.FilterType = Enum.RaycastFilterType.Exclude
		op.FilterDescendantsInstances = params.FilterDescendantsInstances
		op.RespectCanCollide = true
		op.CollisionGroup = params.CollisionGroup
		local size = Vector3.new(root.Size.X + 0.6, half * 2 - 0.4, root.Size.Z + 0.6)
		local box = CFrame.new(center + Vector3.new(0, 0.2, 0)) * root.CFrame.Rotation
		local ok, parts = pcall(function()
			return workspace:GetPartBoundsInBox(box, size, op)
		end)
		if ok then
			local touching = {}
			for _, p in ipairs(parts) do
				local t = World.thingOf(p) or p
				if t == floorThing or touched[t] then
					continue
				end
				touched[t] = true
				if #touching >= 8 then
					continue
				end
				local away = (p.Position - center) * Vector3.new(1, 0, 1)
				local way = if away.Magnitude > 1e-3 then Frame.yawOfDir(away.Unit) else viewYaw
				table.insert(touching, { thing = t.Name, path = Paths.of(t), yaw = r2(way),
					turn = r2(Frame.wrap180(way - viewYaw)) })
			end
			if #touching > 0 then
				body.touching = touching
			end
		end
	end
	return body, touched
end

local function onScreen(cam: Camera, p: Vector3): (boolean, Vector3)
	local sp, visible = cam:WorldToViewportPoint(p)
	return visible, sp
end

-- A ray from `from` along `dir` that passes through fully transparent parts (an invisible trigger zone hides nothing).
local function sightRay(from: Vector3, dir: Vector3, ignore: { Instance }): RaycastResult?
	local list = table.clone(ignore)
	for _ = 1, 5 do
		local p = RaycastParams.new()
		p.FilterType = Enum.RaycastFilterType.Exclude
		p.FilterDescendantsInstances = list
		p.IgnoreWater = true
		local r = workspace:Raycast(from, dir, p)
		if r == nil or r.Instance:IsA("Terrain") or r.Instance.Transparency < 0.99 then
			return r
		end
		table.insert(list, r.Instance)
	end
	return nil
end

-- The middle of a character's body as seen from the camera (Unreal's body_center): rays through a 7 by 7 grid over its
-- box; the middle of where those that reach a visible part of it unhidden hit, and how much of it shows (`body_shown`,
-- when something drawn hides part of it). Its box's middle is often off its body (a tool held out).
local function bodyCenter(item: any, thing: Instance, c: Vector3, size: Vector3, camCf: CFrame, ignore: { Instance })
	local camPos = camCf.Position
	local right, up = camCf.RightVector, camCf.UpVector
	local halfW = math.max(size.X, size.Z) * 0.5
	local far = (camPos - c).Magnitude + size.Magnitude * 2 + 1
	local sum, hits, shown = Vector3.zero, 0, 0
	for i = 0, 6 do
		for j = 0, 6 do
			local through = c + right * ((i / 6 - 0.5) * 2 * halfW) + up * ((j / 6 - 0.5) * size.Y)
			local d = through - camPos
			if d.Magnitude < 1e-3 then
				continue
			end
			d = d.Unit * far
			local list = table.clone(ignore)
			local hidden = false
			for _ = 1, 5 do
				local p = RaycastParams.new()
				p.FilterType = Enum.RaycastFilterType.Exclude
				p.FilterDescendantsInstances = list
				p.IgnoreWater = true
				local r = workspace:Raycast(camPos, d, p)
				if r == nil then
					break
				end
				local inst = r.Instance
				local drawn = inst:IsA("Terrain") or inst.Transparency < 0.99
				if within(inst, thing) then
					if drawn then
						hits += 1
						if not hidden then
							shown += 1
							sum += r.Position
						end
						break
					end
				elseif drawn then
					hidden = true -- (something drawn in front of it there)
				end
				table.insert(list, inst)
			end
		end
	end
	if shown > 0 then
		item.body_center = Frame.kit(sum / shown)
		if shown < hits then
			item.body_shown = r2(shown / hits)
		end
	end
end

-- `held` as the answer has it: each thing's name, path, class, `look.parts` (its drawn parts, biggest first),
-- `attached_at`, where its drawn parts are (center, size), how far the camera is from them, where on screen, whether
-- any of it is in view; nearest the camera first.
local function heldJson(held: { any }, cam: Camera): { any }
	local out = {}
	local camPos = cam.CFrame.Position
	local vp = cam.ViewportSize
	for _, h in ipairs(held) do
		local lo, hi = Vector3.one * math.huge, -Vector3.one * math.huge
		local sizes = {}
		for _, part in ipairs(h.parts) do
			local c, s = World.aabb(part.CFrame, part.Size)
			lo = lo:Min(c - s / 2)
			hi = hi:Max(c + s / 2)
			sizes[part.Name] = math.max(sizes[part.Name] or 0, part.Size.Magnitude)
		end
		local names = {}
		for n in pairs(sizes) do
			table.insert(names, n)
		end
		table.sort(names, function(a, b)
			if sizes[a] ~= sizes[b] then
				return sizes[a] > sizes[b]
			end
			return a < b
		end)
		local c = (lo + hi) / 2
		local nearest = Vector3.new(math.clamp(camPos.X, lo.X, hi.X), math.clamp(camPos.Y, lo.Y, hi.Y),
			math.clamp(camPos.Z, lo.Z, hi.Z))
		local item: any = { name = Paths.nameOf(h.root), path = Paths.of(h.root), class = classOf(h.root),
			look = { parts = names }, center = Frame.kit(c), size = Frame.kitSize(hi - lo),
			distance = r2((nearest - camPos).Magnitude * S) }
		if h.at ~= "" then
			item.attached_at = h.at
		end
		local inView, sp = onScreen(cam, c)
		if sp.Z > 0 then
			item.screen_x = r2(sp.X / vp.X * 2 - 1)
			item.screen_y = r2(1 - sp.Y / vp.Y * 2)
		end
		for i = 0, 7 do
			if inView then
				break
			end
			inView = onScreen(cam, Vector3.new(if i % 2 == 0 then lo.X else hi.X, if (i // 2) % 2 == 0 then lo.Y else hi.Y,
				if i // 4 == 0 then lo.Z else hi.Z))
		end
		item.in_view = inView
		table.insert(out, item)
	end
	table.sort(out, function(a, b)
		if a.distance ~= b.distance then
			return a.distance < b.distance
		end
		return a.name < b.name
	end)
	return out
end

-- The name of the thing an instance is part of, as the room round the body tells it ("" for none and the terrain).
local function thingName(inst: Instance?): string
	if inst == nil or inst:IsA("Terrain") then
		return ""
	end
	local t = World.thingOf(inst) or inst
	return t.Name
end

local function round1(x: number): number
	return math.round(x * 10) / 10
end

local function ofCharacter(part: Instance): boolean
	local t = World.thingOf(part)
	return t ~= nil and humanoidOf(t) ~= nil
end

-- What Reach.surface reads a listed thing's top by: its own collision (rays down that hit only it; its own box's top
-- face when nothing of it collides), a body's room over a place (what overlaps a box there that stops the body and is
-- drawn, the thing and characters left out), the floor beside it, and the eyes.
function Sight.surfaceOf(e: any, ignore: { Instance }, camK: Vector3, viewYaw: number, cam: Camera, center: Vector3,
	half: number, radius: number, h: Humanoid?, root: BasePart?, sees: (Vector3, number) -> boolean): any
	local thing: Instance = e.thing
	local collides = thing:IsA("BasePart") and thing.CanCollide
	if not collides then
		for _, d in ipairs(thing:GetDescendants()) do
			if d:IsA("BasePart") and d.CanCollide then
				collides = true
				break
			end
		end
	end
	local group = if root then root.CollisionGroup else "Default"
	local own = RaycastParams.new()
	own.FilterType = Enum.RaycastFilterType.Include
	own.FilterDescendantsInstances = { thing }
	own.RespectCanCollide = true
	local beside = table.clone(ignore)
	table.insert(beside, thing)
	local besideRay = RaycastParams.new()
	besideRay.FilterType = Enum.RaycastFilterType.Exclude
	besideRay.FilterDescendantsInstances = beside
	besideRay.RespectCanCollide = true
	besideRay.CollisionGroup = group
	besideRay.IgnoreWater = true
	local op = OverlapParams.new()
	op.FilterType = Enum.RaycastFilterType.Exclude
	op.FilterDescendantsInstances = beside
	op.RespectCanCollide = true
	op.CollisionGroup = group
	local above = (e.center.Y + e.size.Y / 2) * S + 50
	local below = (e.center.Y - e.size.Y / 2) * S - 50
	local bodyHeight = half * 2 * S
	local meK = Reach.K(center)
	local feetK = (center.Y - half) * S
	local room = math.max(1, (bodyHeight - 2) * 0.5)
	return { box = e.own, tipped = e.item.tilt ~= nil, radius = radius * S,
		stepUp = math.max(if h then math.max(h.HipHeight, 0.5) * 0.9 * S else 0, 10), camYaw = viewYaw,
		heightAt = function(p: Vector3): number
			if not collides then
				return Reach.topFaceAt(e.own, p)
			end
			local r = workspace:Raycast(Reach.G(Vector3.new(p.X, p.Y, above)), Vector3.new(0, -(above - below) / S, 0), own)
			return if r then r.Position.Y * S else p.Z
		end,
		isOpen = function(p: Vector3): boolean
			local ok, parts = pcall(function()
				return workspace:GetPartBoundsInBox(CFrame.new(Reach.G(p + Vector3.new(0, 0, 2 + room))),
					Vector3.new(10, 2 * room, 10) / S, op)
			end)
			if not ok then
				return true
			end
			for _, part in ipairs(parts) do
				if (part:IsA("Terrain") or part.Transparency < 0.99) and not ofCharacter(part) then
					return false
				end
			end
			return true
		end,
		floorBeside = function(start: Vector3, depth: number): (boolean, number)
			local r = workspace:Raycast(Reach.G(start), Vector3.new(0, -depth / S, 0), besideRay)
			if r then
				return true, r.Position.Y * S
			end
			return false, 0
		end,
		seesPoint = function(p: Vector3): boolean
			local up = p + Vector3.new(0, 0, 5)
			return onScreen(cam, Reach.G(up)) and sees(up, 10)
		end,
		place = function(p: Vector3): any
			local bearing = math.deg(math.atan2(p.Y - camK.Y, p.X - camK.X))
			return { at = Reach.vec(p), turn = math.round(Frame.wrap180(bearing - viewYaw)),
				distance = math.round(Reach.dist2d(p, meK)), height_above_feet = math.round(p.Z - feetK) }
		end }
end

function Sight.view(args: any): (boolean, any)
	local cam = workspace.CurrentCamera
	if cam == nil then
		return false, "No camera"
	end
	local maxDist = (tonumber(args.max_distance) or 5000) / S
	local maxCount = tonumber(args.max_count) or 24
	local floorSize = (tonumber(args.floor_size) or 4000) / S -- wider than this and flat: the ground, left out
	local overheadReach = tonumber(args.overhead_reach) or 2000
	-- Asked for: the openings round the player (`openings`), the ways up it sees (`ways_up`), within `openings_reach`
	-- (Reach.addOpenings); a game that never asks pays nothing
	local wantOpenings = args.openings == true
	local wantWaysUp = args.ways_up == true
	-- (at most 25000: Roblox's Blockcast goes no farther than 1024 studs)
	local openingsReach = math.clamp(tonumber(args.openings_reach) or 3000, 100, 25000)
	local only = args.only
	local isOnly = type(only) == "table"
	local onlySet: { [string]: boolean }? = nil
	if isOnly then
		onlySet = {}
		for _, x in ipairs(only) do
			(onlySet :: any)[tostring(x)] = true
		end
	end
	local camCf = cam.CFrame
	local camPos = camCf.Position
	local viewYaw = Frame.yawOf(camCf)
	local vp = cam.ViewportSize
	local vfov = cam.FieldOfView
	local hfov = math.deg(2 * math.atan(math.tan(math.rad(vfov) / 2) * vp.X / math.max(vp.Y, 1)))
	local char = World.character()
	local center, half, radius = World.body()
	local feetY = if center then center.Y - half else camPos.Y
	-- What the player holds: its own, as its body is, to every sense
	local held = Sight.heldThings()
	local heldSet = {}
	for _, h in ipairs(held) do
		heldSet[h.root] = true
	end
	local body: any = Json.object()
	local touched: { [Instance]: boolean } = {}
	if center then
		body, touched = Sight.body(center, half, viewYaw, held)
	end
	local params = Sight.params(false, held)
	local ignore = params.FilterDescendantsInstances
	local standing = body.standing_on
	-- What the crosshair (the view's middle) is on, and which part of a character
	local crosshair: Instance? = nil
	local crosshairPart = ""
	local ray = cam:ViewportPointToRay(vp.X / 2, vp.Y / 2)
	local hit = workspace:Raycast(ray.Origin, ray.Direction * maxDist, params)
	if hit then
		crosshair = World.thingOf(hit.Instance) or hit.Instance
		if crosshair ~= hit.Instance and humanoidOf(crosshair :: Instance) then
			crosshairPart = hit.Instance.Name
		end
	end
	local outside = if isOnly then {} else Sight.highlightedOutside()
	local out = {}
	for _, thing in ipairs(World.things()) do
		if thing == char or (char and thing:IsDescendantOf(char)) or heldSet[thing] then
			continue
		end
		local name = Paths.nameOf(thing)
		if onlySet and not (onlySet[name] or onlySet[thing.Name] or onlySet[Paths.of(thing)]) then
			continue
		end
		local bc, size, ownCf, ownSize = World.box(thing)
		if bc == nil or size == nil then
			continue
		end
		local boxCenter, boxSize = bc :: Vector3, size :: Vector3
		local lo, hiv = boxCenter - boxSize / 2, boxCenter + boxSize / 2
		local under = center ~= nil and center.X >= lo.X and center.X <= hiv.X and center.Z >= lo.Z and center.Z <= hiv.Z
		if boxSize.X > 30 and boxSize.Z > 30 and center and hiv.Y <= feetY + 0.5 and (thing.Name == standing or under) then
			continue -- (the ground the player is on, or the large floor under the spawn it stands on)
		end
		if math.max(boxSize.X, boxSize.Z) > floorSize and boxSize.Y < floorSize * 0.25 then
			continue -- (the ground: wider than floor_size and flat)
		end
		local nearest = Vector3.new(math.clamp(camPos.X, lo.X, hiv.X), math.clamp(camPos.Y, lo.Y, hiv.Y),
			math.clamp(camPos.Z, lo.Z, hiv.Z))
		local dist = (nearest - camPos).Magnitude
		if dist > maxDist then
			continue
		end
		local centerIn, sp = onScreen(cam, boxCenter)
		local anyIn = centerIn
		if not anyIn then
			for i = 0, 7 do
				local corner = Vector3.new(if i % 2 == 0 then lo.X else hiv.X, if (i // 2) % 2 == 0 then lo.Y else hiv.Y,
					if i // 4 == 0 then lo.Z else hiv.Z)
				if onScreen(cam, corner) then
					anyIn = true
					break
				end
			end
		end
		if not anyIn then
			continue
		end
		if #drawnParts(thing, 1) == 0 then
			continue
		end
		local seen = false
		for _, target in ipairs({ boxCenter, Vector3.new(boxCenter.X, hiv.Y - 0.05, boxCenter.Z), nearest }) do
			local d = target - camPos
			if d.Magnitude < 0.05 then
				seen = true
				break
			end
			local r = workspace:Raycast(camPos, d, params)
			if r == nil or r.Instance == thing or r.Instance:IsDescendantOf(thing) or (r.Position - target).Magnitude < 0.2 then
				seen = true
				break
			end
		end
		if not seen then
			continue
		end
		local pivot = World.pivot(thing)
		local item: any = { name = name, path = Paths.of(thing), class = classOf(thing), center = Frame.kit(boxCenter),
			size = Frame.kitSize(boxSize), yaw = if pivot then Frame.yawOf(pivot) else 0, distance = r2(dist * S),
			top_above_feet = r2((hiv.Y - feetY) * S), bottom_above_feet = r2((lo.Y - feetY) * S),
			crosshair_on = thing == crosshair }
		if thing == crosshair and crosshairPart ~= "" then
			item.crosshair_part = crosshairPart -- (the body part the crosshair is on)
		end
		if sp.Z > 0 then
			item.screen_x = r2(sp.X / vp.X * 2 - 1)
			item.screen_y = r2(1 - sp.Y / vp.Y * 2)
		end
		if not centerIn then
			item.partly_in_view = true
		end
		local v = World.velocity(thing)
		if v.Magnitude > 0.05 then
			item.speed = r2(v.Magnitude * S)
		end
		if touched[thing] then
			item.blocks_player = true -- (the player's body is against it)
		end
		local own = nil
		if ownCf and ownSize then
			local rot = Frame.kitRot(ownCf)
			if math.abs(rot.Pitch) >= 1 or math.abs(rot.Roll) >= 1 then
				item.tilt = { pitch = math.round(rot.Pitch), roll = math.round(rot.Roll) } -- (e.g. a ramp: tipped over)
			end
			-- its own box, when that isn't the box around it (it is turned): its middle, its size along its own axes
			-- (kit X its back-to-front, Y its right, Z its up) and how it is turned
			local ks, ko = Frame.kitSize(boxSize), Frame.kitSize(ownSize)
			if math.abs(ks.X - ko.X) > 1 or math.abs(ks.Y - ko.Y) > 1 or math.abs(ks.Z - ko.Z) > 1 then
				item.box = { center = Reach.vec(Reach.K(ownCf.Position)), size = { X = math.round(ko.X),
					Y = math.round(ko.Y), Z = math.round(ko.Z) }, rotation = { pitch = round1(rot.Pitch),
					yaw = round1(rot.Yaw), roll = round1(rot.Roll) } }
			end
			own = Reach.ownBox(ownCf, ownSize)
		end
		local tags = thing:GetTags()
		if #tags > 0 then
			item.tags = tags
		end
		local hum = humanoidOf(thing)
		if hum then
			item.health = r2(hum.Health)
			item.max_health = r2(hum.MaxHealth)
			if Players:GetPlayerFromCharacter(thing :: Model) then
				item.player = true
			end
		end
		local look = Sight.lookOf(thing, outside)
		if next(look) ~= nil then
			item.look = look
			if look.texts then
				item.label = look.texts[1]
			end
		end
		table.insert(out, { item = item, thing = thing, own = own, center = boxCenter, size = boxSize })
	end
	table.sort(out, function(a, b)
		return a.item.distance < b.item.distance
	end)
	while #out > maxCount do
		table.remove(out)
	end
	local camK = Reach.K(camPos)
	-- A place in sight (Unreal's Sees for a point): a line from the camera reaches it, nothing in the way short of it by
	-- more than `slack` units
	local function sees(at: Vector3, slack: number): boolean
		local r = sightRay(camPos, Reach.G(at) - camPos, ignore)
		return r == nil or (Reach.K(r.Position) - camK).Magnitude >= (at - camK).Magnitude - slack
	end
	local h = World.humanoid()
	local root = World.root()
	local visible, listed, holders = {}, {}, {}
	for _, e in ipairs(out) do
		table.insert(visible, e.item)
		listed[e.thing] = true
		if e.own then
			table.insert(holders, { e.thing.Name, e.own })
		end
		if isOnly then
			continue
		end
		if humanoidOf(e.thing) then
			bodyCenter(e.item, e.thing, e.center, e.size, camCf, ignore)
		elseif e.own and center then
			Reach.surface(e.item, Sight.surfaceOf(e, ignore, camK, viewYaw, cam, center :: Vector3, half, radius, h,
				root, sees))
		end
	end
	local result: any = { visible = visible, camera = { location = Frame.kit(camPos), yaw = viewYaw,
		pitch = Frame.pitchOf(camCf), fov = r2(hfov), vfov = r2(vfov), type = cam.CameraType.Name },
		crosshair_tested = true, oriented = true, effects_play = true, body = body, omitted = Sight.OMITTED }
	if isOnly then
		result.only = only
	else
		result.held = heldJson(held, cam)
		-- the sun: the Lighting's brightness while the sun is up
		result.scene = { sun = if Lighting:GetSunDirection().Y > 0 then r2(Lighting.Brightness) else 0 }
	end
	if center and not isOnly then
		-- What is overhead: the first thing straight above the head, and the room up to it; told only when the player
		-- perceives it (in view, against the body, or the head within 10 units of it). With openings or ways up asked:
		-- `under`, whatever is in view (a ceiling over a player inside a house)
		local headPart = char and char:FindFirstChild("Head")
		local headY = if headPart and headPart:IsA("BasePart") then headPart.Position.Y + headPart.Size.Y / 2
			else (center :: Vector3).Y + half
		local head = Vector3.new((center :: Vector3).X, headY, (center :: Vector3).Z)
		local up = if overheadReach > 0
			then workspace:Raycast(head, Vector3.new(0, overheadReach / S, 0), Sight.params(true, held))
			else nil
		if up then
			local above = World.thingOf(up.Instance) or up.Instance
			local clearance = (up.Position.Y - head.Y) * S
			local told = { thing = above.Name, path = Paths.of(above), clearance = math.round(clearance) }
			if listed[above] or touched[above] or clearance <= 10 then
				body.overhead = told
			end
			if wantOpenings or wantWaysUp then
				body.under = { thing = above.Name, path = Paths.of(above), clearance = math.round(clearance) }
			end
		end
	end
	if args.sonar and center then
		body.sonar = Sight.sonar(center :: Vector3, half, args, viewYaw, held)
	end
	if (wantOpenings or wantWaysUp) and center and h and not isOnly then
		local probe = Reach.Probe.new(Sight.params(true, held))
		probe:setBody(center :: Vector3, half, radius, (h :: Humanoid).HipHeight, (h :: Humanoid).MaxSlopeAngle)
		Reach.addOpenings(body, probe, camK, viewYaw, hfov, openingsReach, wantOpenings, wantWaysUp, holders, sees,
			thingName)
	end
	return true, result
end

-- How far the body could walk each way: a box the body's width, from its step height to its head, swept forward a
-- stretch at a time (a walkable slope or a ledge lower than a step is gone up), and a ray down after each stretch for
-- a drop. Each way: {"turn", "clear", "by": "wall"|"drop"|"reach", "thing"?, "normal"?, "drop"?, "rise"?}.
function Sight.sonar(center: Vector3, half: number, args: any, viewYaw: number, held: { any }?): any
	local reach = math.clamp(tonumber(args.sonar_reach) or 1500, 50, 5000) / S
	local turns = args.sonar_turns or { 0, -30, 30, -60, 60, -90, 90, -135, 135, 180 }
	local h = World.humanoid()
	local root = World.root()
	local step = if h then math.max(h.HipHeight, 0.5) * 0.9 else 1.0
	local maxSlope = math.rad(if h then h.MaxSlopeAngle else 89)
	local width = if root then math.max(root.Size.X, root.Size.Z) else 2
	local height = math.max(half * 2 - step, 1)
	local size = Vector3.new(width, height, width * 0.5)
	local params = Sight.params(true, held)
	local stretch = 1.5
	local dropAt = 2.0 -- studs below the feet that count as a drop
	local ways = {}
	for i, turn in ipairs(turns) do
		if i > 16 then
			break
		end
		local yaw = viewYaw + (tonumber(turn) or 0)
		local dir = Frame.dirOfYaw(yaw)
		local feet = center - Vector3.new(0, half, 0)
		local startY = feet.Y
		local travelled = 0
		local way: any = { turn = tonumber(turn) or 0, by = "reach" }
		local guard = 0
		while travelled < reach and guard < 80 do
			guard += 1
			local len = math.min(stretch, reach - travelled)
			local boxCenter = feet + Vector3.new(0, step + height / 2, 0)
			local cf = CFrame.lookAt(boxCenter, boxCenter + dir)
			local hit = workspace:Blockcast(cf, size, dir * len, params)
			if hit then
				local slope = math.acos(math.clamp(hit.Normal.Y, -1, 1))
				if slope <= maxSlope and guard < 79 then
					feet += Vector3.new(0, step * 0.5, 0) -- (a walkable rise: up it)
					continue
				end
				travelled += hit.Distance
				way.by = "wall"
				local t = World.thingOf(hit.Instance) or hit.Instance
				way.thing = t.Name
				way.normal = { X = r2(-hit.Normal.Z), Y = r2(hit.Normal.X) }
				break
			end
			local moved = feet + dir * len
			travelled += len
			local down = workspace:Raycast(moved + Vector3.new(0, step + 0.1, 0), Vector3.new(0, -(step + 40), 0), params)
			local below = if down then moved.Y - down.Position.Y else 41
			if below > dropAt then
				way.by = "drop"
				way.drop = if below <= 40 then r2(below * S) else Json.null
				-- the edge, to within a 32nd of the stretch: where the floor under the body's middle ends
				local lo, hi = 0, len
				for _ = 1, 5 do
					local mid = (lo + hi) / 2
					local at = feet + dir * mid + Vector3.new(0, step + 0.1, 0)
					if workspace:Raycast(at, Vector3.new(0, -(step + 0.1 + dropAt), 0), params) then
						lo = mid
					else
						hi = mid
					end
				end
				travelled -= len - lo
				break
			end
			feet = Vector3.new(moved.X, if down then down.Position.Y else moved.Y, moved.Z)
		end
		way.clear = r2(math.max(travelled, 0) * S)
		local rise = feet.Y - startY
		if math.abs(rise) > 0.2 then
			way.rise = r2(rise * S)
		end
		table.insert(ways, way)
	end
	return { ways = ways, reach = r2(reach * S) }
end

-- --- watch --------------------------------------------------------------------------------------------------------

-- The state a watch starts from: each object's pivot and size (and with `drawn`, its parts' places in its own frame
-- and how it is drawn).
function Sight.watchStart(args: any, resolve: (string) -> Instance?): any
	local drawn = args.drawn == true
	local partsCount = math.clamp(tonumber(args.parts) or 3, 0, 16)
	local entries, looks = {}, {}
	for _, x in ipairs(args.objects or {}) do
		local path = tostring(x)
		local inst = resolve(path)
		if inst == nil then
			continue
		end
		table.insert(entries, { inst = inst, thing = nil, path = path, found = {} })
		if drawn and (inst:IsA("Model") or inst:IsA("BasePart")) then
			local parts = {}
			for _, d in ipairs(inst:GetDescendants()) do
				if d:IsA("BasePart") then
					table.insert(parts, d)
				end
			end
			table.sort(parts, function(a, b)
				return a.Size.Magnitude > b.Size.Magnitude
			end)
			for i = 1, math.min(partsCount, #parts) do
				table.insert(entries, { inst = parts[i], thing = inst, path = path .. "/" .. parts[i].Name, found = {} })
			end
			table.insert(looks, { inst = inst, path = path, found = {} })
		end
	end
	return { entries = entries, looks = looks, drawn = drawn, turned = tonumber(args.turned) or 1,
		moved = tonumber(args.moved) or 2, resized = tonumber(args.resized) or 0.02 }
end

local function sample(e: any): (CFrame?, Vector3?)
	local inst = e.inst
	if inst.Parent == nil then
		return nil, nil
	end
	if inst:IsA("GuiObject") then
		local g = inst :: GuiObject
		local at = CFrame.new(g.AbsolutePosition.X / S, -g.AbsolutePosition.Y / S, 0)
		return at * CFrame.Angles(0, 0, math.rad(-g.Rotation)),
			Vector3.new(g.AbsoluteSize.X, g.AbsoluteSize.Y, 1)
	end
	local cf = World.pivot(inst)
	if cf == nil then
		return nil, nil
	end
	local size = if inst:IsA("BasePart") then inst.Size else (select(2, World.box(inst)) or Vector3.one)
	if e.thing then
		local tcf = World.pivot(e.thing)
		if tcf == nil then
			return nil, nil
		end
		cf = tcf:ToObjectSpace(cf :: CFrame)
	end
	return cf, size
end

local function lookState(inst: Instance): any
	local look = Sight.lookOf(inst)
	local effects = {}
	for _, e in ipairs(look.effects or {}) do
		table.insert(effects, e.asset)
	end
	return { parts = look.parts or {}, lights = if look.lights then look.lights.on else 0, anims = look.animations
		or (if look.animation then { look.animation } else {}), sounds = look.sounds or {}, effects = effects,
		limp = look.limp == true }
end

local function has(list: { any }, x: any): boolean
	return table.find(list, x) ~= nil
end

function Sight.watchTick(st: any, t: number)
	for _, e in ipairs(st.entries) do
		local cf, size = sample(e)
		if cf == nil then
			continue
		end
		if e.first == nil then
			e.first = { cf, size }
			continue
		end
		local f0, s0 = e.first[1], e.first[2]
		local rel = f0:ToObjectSpace(cf :: CFrame)
		local _, angle = rel:ToAxisAngle()
		if not e.found.turned and math.deg(math.abs(angle)) > st.turned then
			e.found.turned = t
		end
		if not e.found.moved and (cf.Position - f0.Position).Magnitude * S > st.moved then
			e.found.moved = t
		end
		if not e.found["changed size"] and s0 and size then
			local base = Vector3.new(math.max(math.abs(s0.X), 0.001), math.max(math.abs(s0.Y), 0.001),
				math.max(math.abs(s0.Z), 0.001))
			local d = (size - s0)
			if math.max(math.abs(d.X) / base.X, math.abs(d.Y) / base.Y, math.abs(d.Z) / base.Z) > st.resized then
				e.found["changed size"] = t
			end
		end
	end
	for _, l in ipairs(st.looks) do
		if l.inst.Parent == nil then
			continue
		end
		local now = lookState(l.inst)
		local last = l.last
		if last then
			for _, x in ipairs(now.parts) do
				if not has(last.parts, x) then
					table.insert(l.found, { t = t, what = "part shown", asset = x })
				end
			end
			for _, x in ipairs(last.parts) do
				if not has(now.parts, x) then
					table.insert(l.found, { t = t, what = "part hidden", asset = x })
				end
			end
			if now.lights ~= last.lights then
				table.insert(l.found, { t = t, what = if now.lights > last.lights then "light on" else "light off", asset = "" })
			end
			for _, note in ipairs({ { "anims", "animation", "ended" }, { "sounds", "sound", "ended" },
				{ "effects", "effect", "stopped" } }) do
				local key, word, gone = note[1], note[2], note[3]
				for _, x in ipairs(now[key]) do
					if not has(last[key], x) then
						table.insert(l.found, { t = t, what = word .. " started", asset = x })
					end
				end
				for _, x in ipairs(last[key]) do
					if not has(now[key], x) then
						table.insert(l.found, { t = t, what = word .. " " .. gone, asset = x })
					end
				end
			end
			if now.limp and not last.limp then
				table.insert(l.found, { t = t, what = "went limp", asset = "" })
			end
		end
		l.last = now
	end
end

function Sight.watchFinish(st: any): any
	local changes = {}
	local seen = {}
	for _, e in ipairs(st.entries) do
		for what, t in pairs(e.found) do
			local key = e.path .. "|" .. what
			if not seen[key] then
				seen[key] = true
				table.insert(changes, { t = r2(t), object = e.path, what = what })
			end
		end
	end
	for _, l in ipairs(st.looks) do
		for _, f in ipairs(l.found) do
			table.insert(changes, { t = r2(f.t), object = l.path, what = f.what, asset = f.asset })
		end
	end
	table.sort(changes, function(a, b)
		return a.t < b.t
	end)
	local out: any = { changes = changes }
	if st.drawn then
		out.drawn = true
	end
	return out
end

return Sight
