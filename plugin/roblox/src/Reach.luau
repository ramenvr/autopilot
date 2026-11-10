--!strict
-- What the player finds of the room round it by its own body, for `view` (the Godot addon's lib/reach.gd, Unreal's
-- PlaytestOpsView.cpp and PlaytestOpsOpenings.cpp): the `openings` through what stands round it, how far it goes each
-- way (`free`), the ways up it sees (`ways_up`, with their `lane` and what they are `inside`) and a thing's top surface
-- (`top_slope_degrees`; for a slope a body walks `low_end`, `high_end`, `lane`, `uphill`). All in the kit frame (X
-- forward, Y right, Z up, centimetres: Reach.K) and found as the player's own body finds them: rays that stop where
-- it stops (its CollisionGroup, CanCollide respected, the character and what it holds left out), a box its size swept
-- (Blockcast), rays down onto the floor. Never the game's navigation data.
--
-- A Humanoid steps up a ledge lower than its step (about 0.9 x HipHeight) and walks slopes up to its MaxSlopeAngle
-- (Probe.new takes at most 60 degrees: Roblox's default, 89, would make a wall a slope). So the floor may rise from one
-- place to the next by the step at once, or by what the steepest slope rises over the way between them; a rise more
-- than the step is felt again at quarters of the way. Stairs whose risers are lower than the step are a way up.
-- Terrain is a floor and a wall here like any part, but it is never named as a thing.
local Frame = require(script.Parent.Frame)

local Reach = {}

local S = Frame.SCALE

-- A Roblox point (studs) in the kit frame (centimetres), as a Vector3 on the kit's axes.
function Reach.K(v: Vector3): Vector3
	return Vector3.new(-v.Z, v.X, v.Y) * S
end

-- A kit point as a Roblox one.
function Reach.G(k: Vector3): Vector3
	return Vector3.new(k.Y, k.Z, -k.X) / S
end

-- A Roblox direction on the kit's axes (unscaled).
function Reach.KD(v: Vector3): Vector3
	return Vector3.new(-v.Z, v.X, v.Y)
end

local K, G, KD = Reach.K, Reach.G, Reach.KD

-- A kit vector as the answer has it: whole units, as Unreal's.
function Reach.vec(k: Vector3): any
	return { X = math.round(k.X), Y = math.round(k.Y), Z = math.round(k.Z) }
end

local vec = Reach.vec

local function dist2d(a: Vector3, b: Vector3): number
	local dx, dy = a.X - b.X, a.Y - b.Y
	return math.sqrt(dx * dx + dy * dy)
end
Reach.dist2d = dist2d

local function flat(v: Vector3): Vector3
	return Vector3.new(v.X, v.Y, 0)
end

local function len2d(v: Vector3): number
	return math.sqrt(v.X * v.X + v.Y * v.Y)
end

local wrap180 = Frame.wrap180

local function dirOf(degrees: number): Vector3
	local r = math.rad(degrees)
	return Vector3.new(math.cos(r), math.sin(r), 0)
end

local function lerp(a: number, b: number, t: number): number
	return a + (b - a) * t
end

-- Distance from a point to a segment, across the floor (z ignored).
local function distToSegment2d(p: Vector3, a: Vector3, b: Vector3): number
	local abx, aby = b.X - a.X, b.Y - a.Y
	local apx, apy = p.X - a.X, p.Y - a.Y
	local len2 = abx * abx + aby * aby
	local t = if len2 > 0 then math.clamp((apx * abx + apy * aby) / len2, 0, 1) else 0
	local dx, dy = apx - abx * t, apy - aby * t
	return math.sqrt(dx * dx + dy * dy)
end

-- --- a thing's own box -------------------------------------------------------------------------------------------

-- A thing's box in its own frame, in the kit frame: {"center", "axis": {3 unit Vector3s}, "half": {3 numbers}} from
-- its oriented box (a part's CFrame and Size, a model's GetBoundingBox).
function Reach.ownBox(cf: CFrame, size: Vector3): any
	return { center = K(cf.Position), axis = { KD(cf.XVector), KD(cf.YVector), KD(cf.ZVector) },
		half = { math.abs(size.X) * S * 0.5, math.abs(size.Y) * S * 0.5, math.abs(size.Z) * S * 0.5 } }
end

local function upIndex(b: any): number
	local best = 1
	for i = 2, 3 do
		if math.abs(b.axis[i].Z) > math.abs(b.axis[best].Z) then
			best = i
		end
	end
	return best
end

local function topNormal(b: any): Vector3
	local k = upIndex(b)
	return b.axis[k] * (if b.axis[k].Z >= 0 then 1 else -1)
end

-- --- the body's own probes ---------------------------------------------------------------------------------------

-- What the player's body finds by itself (Unreal's FBodyProbe): lines stopped by what stops it, a box its size swept,
-- the floor under a place. Kit units in and out. `cast(a, b)` and `sweepCast(from, to)` stand in for the Workspace's
-- (a test's world).
local Probe = {}
Probe.__index = Probe
Reach.Probe = Probe

function Probe.new(params: RaycastParams?): any
	local p: any = setmetatable({}, Probe)
	p.params = params
	p.radius = 28
	p.halfHeight = 84
	p.step = 50 -- what it steps up at once
	p.tanSlope = 1 -- how steep a floor it walks up, as rise over run
	p.walkableZ = 0.7071 -- a floor's normal at least this upright is one it stands on
	p.feet = 0
	p.me = Vector3.zero -- the body's middle
	p.cast = nil
	p.sweepCast = nil
	return p
end

-- The body's numbers from its Humanoid: radius and half height (studs), step 0.9 x HipHeight, slopes to
-- min(MaxSlopeAngle, 60) degrees.
function Probe.setBody(self: any, center: Vector3, half: number, radius: number, hipHeight: number, maxSlope: number)
	self.me = K(center)
	self.feet = (center.Y - half) * S
	self.halfHeight = half * S
	self.radius = math.max(radius, 0.5) * S
	self.step = math.max(hipHeight, 0.5) * 0.9 * S
	local slope = math.rad(math.clamp(maxSlope, 0, 60))
	self.walkableZ = math.cos(slope)
	self.tanSlope = math.tan(slope)
end

-- The first thing along a line that stops the body: {"at", "distance", "instance", "normal"}, nil for none.
function Probe.line(self: any, a: Vector3, b: Vector3): any
	if self.cast then
		return self.cast(a, b)
	end
	local o = G(a)
	local d = G(b) - o
	if d.Magnitude < 1e-4 then
		return nil
	end
	local r = workspace:Raycast(o, d, self.params)
	if r == nil then
		return nil
	end
	local at = K(r.Position)
	return { at = at, distance = (at - a).Magnitude, instance = r.Instance, normal = KD(r.Normal) }
end

-- The floor under a place, straight down from `from` by `depth`: found, its height, whether a body stands on it.
function Probe.floorAt(self: any, from: Vector3, depth: number): (boolean, number, boolean)
	local hit = self:line(from, from - Vector3.new(0, 0, depth))
	if hit == nil then
		return false, 0, false
	end
	return true, hit.at.Z, hit.normal.Z >= self.walkableZ
end

-- How much higher than a place the floor may be `run` units on: the step at once, and what the steepest floor it
-- walks rises over that run.
function Probe.climb(self: any, run: number): number
	return self.step + run * self.tanSlope
end

-- Whether a body walks from a floor `from` (its height in .Z) to the floor at `to`: no more of a rise or fall than
-- climb(run); a change more than the step felt again at quarters of the way (a ledge higher than the step rises all at
-- once in one quarter: not walked).
function Probe.walks(self: any, from: Vector3, to: Vector3): boolean
	local run = dist2d(from, to)
	local change = math.abs(to.Z - from.Z)
	if change <= self.step + 1 then
		return true
	end
	if change > self:climb(run) + 1 then
		return false
	end
	local z = from.Z
	local top = math.max(from.Z, to.Z)
	for _, q in ipairs({ 0.25, 0.5, 0.75, 1.0 }) do
		local zq = to.Z
		if q < 1 then
			local at = from:Lerp(to, q)
			local found, fz = self:floorAt(Vector3.new(at.X, at.Y, top + self:climb(run) + 5), self:climb(run) * 2 + 20)
			if not found then
				return false
			end
			zq = fz
		end
		if math.abs(zq - z) > self:climb(run * 0.25) + 1 then
			return false
		end
		z = zq
	end
	return true
end

-- A box the body's width, from a little over its step to its head, swept from one place for the feet to another:
-- {"at", "instance"} of what stops it on the way, nil when nothing does.
function Probe.sweep(self: any, fromFeet: Vector3, toFeet: Vector3): any
	if self.sweepCast then
		return self.sweepCast(fromFeet, toFeet)
	end
	local lift = math.max(self.step, 5) + 2
	local width = math.max(2, self.radius * 2 - 4)
	local height = math.max(width, self.halfHeight * 2 - lift - 4)
	local up = Vector3.new(0, 0, lift + height / 2)
	local o = G(fromFeet + up)
	local d = G(toFeet + up) - o
	if d.Magnitude < 1e-3 then
		return nil
	end
	local level = Vector3.new(d.X, 0, d.Z)
	local cf = if level.Magnitude > 1e-3 then CFrame.lookAt(o, o + level) else CFrame.new(o)
	-- (Blockcast goes at most 1024 studs: a longer sweep is cast in pieces, so a far wall isn't taken for nothing)
	local total = d.Magnitude
	local dir = d.Unit
	local done = 0
	local r = nil
	while done < total do
		local len = math.min(1000, total - done)
		local ok, hit = pcall(function()
			return workspace:Blockcast(cf + dir * done, Vector3.new(width, height, width) / S, dir * len, self.params)
		end)
		if ok and hit then
			r = hit
			break
		end
		done += len
	end
	if r == nil then
		return nil
	end
	return { at = K(r.Position), instance = r.Instance }
end

-- --- openings and ways up ----------------------------------------------------------------------------------------

local OPENING_RAY_DEGREES = 1.0
local OPENING_DEEPER = 150.0
local OPENING_DEEPER_SHARE = 0.3
local OPENING_SECTOR = 15.0
local OPENING_SAME_HEIGHT = 60.0
local OPENING_FLAT = 8.0
local OPENING_SEEN_SHARE = 1.2
local OPENING_ASIDE = 12.0
local MAX_OPENINGS = 5
local OPENINGS_TRIED = 8
local OPENING_EDGE_STEPS = 3
local WAY_UP_EVERY = 5.0
local WAY_UP_PACE = 40.0
local WAY_UP_REACH = 1500.0
local WAY_UP_RISE = 100.0
local WAY_UP_FOOT_APART = 150.0
local MAX_WAYS_UP = 3
local WAY_UP_PROBE = 40.0
local WAY_UP_SIDE_PROBES = 2
local WAY_UP_LANE_STATIONS = { 1.0 / 3.0, 2.0 / 3.0 }
local WAY_UP_LANE_STEP = 20.0
local WAY_UP_LANE_REACH = 600.0
local WAY_UP_LANE_DEEP = 3000.0
local WAY_UP_LANE_BELOW = 1000.0
local WAY_UP_LANE_HALVINGS = 3

-- The floor under a place as a body walking from a floor `fromZ` high finds it: found and stood on, its height.
local function floorFrom(p: any, at: Vector3, fromZ: number, run: number): (boolean, number)
	local found, z, stands = p:floorAt(Vector3.new(at.X, at.Y, fromZ + p:climb(run) + 5), p:climb(run) * 2 + 65)
	return found and stands, z
end

-- From a tread along `way` (sign 1: while the floor rises; -1: while it falls), a pace at a time, no wall at the
-- knees, until it levels off: where it ends (going down, the floor where it levelled off, the foot; going up, the last
-- tread up, the top).
local function march(p: any, from: Vector3, fromZ: number, way: Vector3, sign: number, knee: number): Vector3
	local last = Vector3.new(from.X, from.Y, fromZ)
	local finish = last
	local z = fromZ
	local level = 0
	local gone = WAY_UP_PACE
	while gone <= WAY_UP_REACH and level < 2 do
		local nxt = flat(from) + way * gone
		gone += WAY_UP_PACE
		if p:line(Vector3.new(last.X, last.Y, z + knee), Vector3.new(nxt.X, nxt.Y, z + knee)) then
			break
		end
		local ok, fz = floorFrom(p, nxt, z, WAY_UP_PACE)
		if not ok or not p:walks(last, Vector3.new(nxt.X, nxt.Y, fz)) then
			break
		end
		if (fz - z) * sign > 3 then
			level = 0
			finish = Vector3.new(nxt.X, nxt.Y, fz)
		else
			level += 1
			if level == 1 and sign < 0 then
				finish = Vector3.new(nxt.X, nxt.Y, fz)
			end
		end
		z = fz
		last = Vector3.new(nxt.X, nxt.Y, fz)
	end
	return finish
end

-- A way up's lane (Unreal's Lanes): across it at WAY_UP_LANE_STATIONS of the way from its foot to its top, the floor a
-- body walks across it each side, to a wall, a drop or open floor; its middle line through the two runs' middles, its
-- foot and top marched again along that line, its width the narrowest run, and how far the floor goes on below its
-- foot as far as the eyes see. Kept as found (no `lane`) where a run isn't found or the line's foot isn't on the body's
-- floor or its top doesn't rise WAY_UP_RISE.
local function laneOf(p: any, way: any, knee: number, sees: (Vector3, number) -> boolean)
	local function seenFloor(at: Vector3): boolean
		return sees(at + Vector3.new(0, 0, 5), 20)
	end
	-- from a tread out along dir: how far a body walks on (at most `most`), what stopped it (0 nothing, 1 a wall, 2 a
	-- drop, 3 a place the eyes don't see), how deep a drop
	local function across(at: Vector3, fromZ: number, dir: Vector3, most: number, sight: boolean): (number, number, number)
		local z = fromZ
		local good = 0
		local function tryOut(outBy: number, zNow: number, goodNow: number): (boolean, number, number, number)
			local from2 = flat(at) + dir * goodNow
			local to2 = flat(at) + dir * outBy
			if p:line(Vector3.new(from2.X, from2.Y, zNow + knee), Vector3.new(to2.X, to2.Y, zNow + knee)) then
				return false, zNow, 1, 0
			end
			local c = p:climb(WAY_UP_LANE_STEP)
			local found, fz, stands = p:floorAt(Vector3.new(to2.X, to2.Y, zNow + c + 5), c * 2 + 10)
			if found and fz >= zNow - p:climb(outBy - goodNow) - 1 then
				if not stands or not p:walks(Vector3.new(from2.X, from2.Y, zNow), Vector3.new(to2.X, to2.Y, fz)) then
					return false, zNow, 1, 0
				end
				if sight and not seenFloor(Vector3.new(to2.X, to2.Y, fz)) then
					return false, zNow, 3, 0
				end
				return true, fz, 0, 0
			end
			local why = if sight and not seenFloor(Vector3.new(to2.X, to2.Y, zNow)) then 3 else 2
			local lowFound, lowZ = p:floorAt(Vector3.new(to2.X, to2.Y, zNow), WAY_UP_LANE_DEEP)
			return false, zNow, why, if lowFound then zNow - lowZ else WAY_UP_LANE_DEEP
		end
		local outBy = WAY_UP_LANE_STEP
		while outBy <= most + 0.01 do
			local ok, nz, why, fall = tryOut(outBy, z, good)
			if not ok then
				local hi = outBy
				for _ = 1, WAY_UP_LANE_HALVINGS do
					local mid = (good + hi) * 0.5
					local mok, mz = tryOut(mid, z, good)
					if mok then
						good = mid
						z = mz
					else
						hi = mid
					end
				end
				return good, why, fall
			end
			good = outBy
			z = nz
			outBy += WAY_UP_LANE_STEP
		end
		return most, 0, 0
	end
	local rough = flat(way.top - way.foot)
	if rough.Magnitude < 0.001 then
		return
	end
	rough = rough.Unit
	local side = Vector3.new(-rough.Y, rough.X, 0) -- (its right, facing up it the way it was found)
	local middles = { Vector3.zero, Vector3.zero }
	local zs = { 0, 0 }
	local chords = { 0, 0 }
	local bys = { { 0, 0 }, { 0, 0 } }
	local depths = { { 0, 0 }, { 0, 0 } }
	for k = 1, 2 do
		local t = WAY_UP_LANE_STATIONS[k]
		local at = way.foot:Lerp(way.top, t)
		local ok, fz = floorFrom(p, at, lerp(way.foot.Z, way.top.Z, t), WAY_UP_PACE)
		if not ok then
			return
		end
		local og, oby, odep = across(at, fz, side, WAY_UP_LANE_REACH, false)
		local bg, bby, bdep = across(at, fz, -side, WAY_UP_LANE_REACH, false)
		bys[k] = { oby, bby }
		depths[k] = { odep, bdep }
		middles[k] = flat(at) + side * ((og - bg) * 0.5)
		chords[k] = og + bg
		zs[k] = fz
	end
	local up2 = flat(middles[2] - middles[1])
	up2 = if up2.Magnitude < p.radius then rough else up2.Unit
	local square = math.abs(side.X * up2.Y - side.Y * up2.X)
	local foot = march(p, Vector3.new(middles[1].X, middles[1].Y, zs[1]), zs[1], -up2, -1, knee)
	local top = march(p, Vector3.new(middles[2].X, middles[2].Y, zs[2]), zs[2], up2, 1, knee)
	if math.abs(foot.Z - way.foot.Z) > p:climb(WAY_UP_PACE) + 5 or top.Z - foot.Z < WAY_UP_RISE then
		return
	end
	way.foot = foot
	way.top = top
	way.rise = top.Z - foot.Z
	way.footDistance = dist2d(foot, p.me)
	way.topDistance = dist2d(top, p.me)
	way.degrees = math.deg(math.atan2(foot.Y - p.me.Y, foot.X - p.me.X))
	way.lane = true
	way.width = math.min(chords[1], chords[2]) * square
	local sideRight = side.X * -up2.Y + side.Y * up2.X >= 0 -- (side lies to the right facing up)
	for j = 1, 2 do
		local worst, deepest = 0, 0
		for k = 1, 2 do
			if (bys[k][j] == 2 and (worst ~= 2 or depths[k][j] > deepest)) or (bys[k][j] == 1 and worst == 0) then
				worst = bys[k][j]
				deepest = depths[k][j]
			end
		end
		if (j == 1) == sideRight then
			way.rightBy, way.rightDepth = worst, deepest
		else
			way.leftBy, way.leftDepth = worst, deepest
		end
	end
	local clear, belowBy = across(foot, foot.Z, -up2, WAY_UP_LANE_BELOW, true)
	way.clear = clear
	way.belowBy = belowBy
end

local function waysUp(p: any, cam: Vector3, yaw: number, fov: number, reach: number, knee: number, chest: number,
	height: number, rays: { any }, sees: (Vector3, number) -> boolean, nameOf: (Instance?) -> string): { any }
	local me = p.me
	local n = #rays
	local left = yaw - fov * 0.5
	local found = {}
	-- Seen from the side: a staircase or ramp rising across the view (see Unreal's SideWayUp)
	local function sideWayUp(degrees: number, sightTo: number): any
		local dir = dirOf(degrees)
		local to = math.min(WAY_UP_REACH, reach, sightTo - p.radius)
		local probes = 0
		local outBy = WAY_UP_PACE
		while outBy <= to and probes < WAY_UP_SIDE_PROBES do
			local at = flat(me) + dir * outBy
			outBy += WAY_UP_PACE
			local ok, z, stands = p:floorAt(Vector3.new(at.X, at.Y, cam.Z), cam.Z - p.feet + p:climb(WAY_UP_PACE) + 5)
			if not ok or not stands or z < p.feet + p:climb(WAY_UP_PACE) + 5 then
				continue
			end
			local tread = Vector3.new(at.X, at.Y, z)
			for _, k in ipairs(found) do
				if z <= k.top.Z + 5 and distToSegment2d(tread, k.foot, k.top) <= WAY_UP_FOOT_APART then
					return nil
				end
			end
			if not sees(tread + Vector3.new(0, 0, 5), 20) then
				continue
			end
			probes += 1
			local bestBy, bestYaw = 0, 0
			local function tryWay(toward: number)
				local w = dirOf(toward)
				local okU, zu = floorFrom(p, tread + w * WAY_UP_PROBE, z, WAY_UP_PROBE)
				local okD, zd = floorFrom(p, tread - w * WAY_UP_PROBE, z, WAY_UP_PROBE)
				if okU and okD then
					local rise, drop = zu - z, z - zd
					local most = p:climb(WAY_UP_PROBE) + 1
					if rise > 3 and rise <= most and drop > 3 and drop <= most and math.min(rise, drop) > bestBy then
						bestBy = math.min(rise, drop)
						bestYaw = toward
					end
				end
			end
			for d = 0, 7 do
				tryWay(d * 45)
			end
			if bestBy <= 0 then
				continue
			end
			local coarse = bestYaw
			tryWay(coarse - 22.5)
			tryWay(coarse + 22.5)
			local up = dirOf(bestYaw)
			local foot = march(p, tread, z, -up, -1, knee)
			local top = march(p, tread, z, up, 1, knee)
			if math.abs(foot.Z - p.feet) > p:climb(WAY_UP_PACE) + 5 or top.Z - foot.Z < WAY_UP_RISE then
				continue
			end
			return { foot = foot, top = top, rise = top.Z - foot.Z, footDistance = dist2d(foot, me),
				topDistance = dist2d(top, me), degrees = math.deg(math.atan2(foot.Y - me.Y, foot.X - me.X)), side = true,
				reach = p:sweep(Vector3.new(me.X, me.Y, p.feet), foot) == nil }
		end
		return nil
	end
	local off = 0
	while off <= fov + 0.01 do
		local degrees = left + off
		local dir = dirOf(degrees)
		local nearest = math.clamp(math.round(off / OPENING_RAY_DEGREES), 0, n - 1) + 1
		local flatOpen = math.min(rays[nearest].knee, rays[nearest].chest)
		local z = p.feet
		local footZ = p.feet
		local footDistance = -1
		local last = Vector3.new(me.X, me.Y, p.feet)
		local foot = last
		local rise = 0
		local topDistance = 0
		local topAt = last
		local level = 0
		local outBy = WAY_UP_PACE
		while outBy <= math.min(WAY_UP_REACH, reach) do
			local nxt = flat(me) + dir * outBy
			local clear = z <= p.feet + 5 and z >= p.feet - 5 and outBy <= flatOpen - p.radius
			if not clear and (p:line(Vector3.new(last.X, last.Y, z + knee), Vector3.new(nxt.X, nxt.Y, z + knee)) ~= nil
				or p:line(Vector3.new(last.X, last.Y, z + chest), Vector3.new(nxt.X, nxt.Y, z + chest)) ~= nil) then
				break -- a wall at the knees or the chest, from the floor reached
			end
			local ok, floorZ = floorFrom(p, nxt, z, WAY_UP_PACE)
			if not ok or not p:walks(last, Vector3.new(nxt.X, nxt.Y, floorZ)) then
				break -- a drop, a floor too steep to stand on, a step too high
			end
			if floorZ > z + 3 then
				if footDistance < 0 then
					footDistance = outBy - WAY_UP_PACE
					foot = Vector3.new(last.X, last.Y, z)
					footZ = z
				end
				level = 0
			elseif footDistance >= 0 then
				level += 1
				if level >= 2 and z - footZ >= WAY_UP_RISE then
					break -- on top: level again
				end
				if level >= 3 and z - footZ < WAY_UP_RISE then
					footDistance = -1 -- a small rise that levelled off (a kerb, a sill): not a way up
					level = 0
				end
			end
			z = floorZ
			last = Vector3.new(nxt.X, nxt.Y, z)
			if footDistance >= 0 and z - footZ > rise then
				rise = z - footZ
				topDistance = outBy
				topAt = last
			end
			outBy += WAY_UP_PACE
		end
		off += WAY_UP_EVERY
		if footDistance < 0 or rise < WAY_UP_RISE then
			local side = sideWayUp(degrees, rays[nearest].chest)
			if side then
				table.insert(found, side)
			end
			continue
		end
		if not sees(foot + Vector3.new(0, 0, 30), 30) then
			continue
		end
		table.insert(found, { foot = foot, top = topAt, rise = rise, footDistance = footDistance,
			topDistance = topDistance, degrees = degrees, side = false, reach = true })
	end
	table.sort(found, function(a, b)
		return a.footDistance < b.footDistance
	end)
	local kept = {}
	for _, way in ipairs(found) do
		local same = 0
		for i, k in ipairs(kept) do
			if dist2d(k.foot, way.foot) <= WAY_UP_FOOT_APART then
				same = i
				break
			end
		end
		if same == 0 then
			if #kept < MAX_WAYS_UP then
				table.insert(kept, way)
			end
		elseif way.rise > kept[same].rise then
			kept[same] = way -- (one foot: the line up it that rose most)
		end
	end
	for _, way in ipairs(kept) do
		laneOf(p, way, knee, sees)
	end
	local kinds = { [0] = "open", [1] = "wall", [2] = "drop" }
	local function sideOf(kind: number, deep: number): any
		local told: any = { by = kinds[kind] or "open" }
		if kind == 2 then
			told.depth = math.round(deep)
		end
		return told
	end
	local out = {}
	for _, way in ipairs(kept) do
		local o: any = { turn = math.round(wrap180(way.degrees - yaw)), distance_to_foot = math.round(way.footDistance),
			rises_by = math.round(way.rise), top_distance = math.round(way.topDistance), foot = vec(way.foot),
			top = vec(way.top) }
		if way.side then
			o.seen_from = "side"
			o.reachable_straight = way.reach
		end
		if way.lane then
			o.lane = { width = math.round(way.width), right = sideOf(way.rightBy, way.rightDepth),
				left = sideOf(way.leftBy, way.leftDepth),
				below = { clear = math.round(way.clear), by = ({ "reach", "wall", "drop", "unseen" })[way.belowBy + 1] } }
		end
		-- What it is inside: what is overhead at its foot
		local head = way.foot + Vector3.new(0, 0, height)
		local over = p:line(head, head + Vector3.new(0, 0, 2000))
		if over then
			local inside = nameOf(over.instance)
			if inside ~= "" then
				o.inside = inside
			end
		end
		table.insert(out, o)
	end
	return out
end

-- view's `openings` (and `free`) and `ways_up` (Unreal's AddOpenings) into `body`: the body's room across the camera's
-- field of view (`yaw`, `fov` degrees wide) out to `reach`. cam: the camera (kit); sees(p, slack): whether a line of
-- sight from the camera reaches kit point p; holders: {{name, own box}} of what is in view (what an opening leads into);
-- nameOf(instance): the name of the thing it is part of ("" for none, or the terrain).
function Reach.addOpenings(body: any, p: any, cam: Vector3, yaw: number, fov: number, reach: number, openings: boolean,
	wantWaysUp: boolean, holders: { any }, sees: (Vector3, number) -> boolean, nameOf: (Instance?) -> string)
	local height = p.halfHeight * 2
	local knee = math.max(p.step + 5, height * 0.28)
	local chest = math.max(knee + 10, height * 0.62)
	local n = math.clamp(math.floor(fov / OPENING_RAY_DEGREES) + 1, 3, 361)
	local left = yaw - fov * 0.5
	local me = p.me
	local function turnTo(from: Vector3, to: Vector3): number
		return math.round(wrap180(math.deg(math.atan2(to.Y - from.Y, to.X - from.X)) - yaw))
	end
	-- how far a line at that height goes from the body before something stops it (and what stopped it)
	local function depth(degrees: number, above: number): (number, Instance?)
		local from = Vector3.new(me.X, me.Y, p.feet + above)
		local hit = p:line(from, from + dirOf(degrees) * reach)
		if hit == nil then
			return reach, nil
		end
		return hit.distance, hit.instance
	end
	local rays = {}
	for i = 1, n do
		local rayYaw = math.min(left + (i - 1) * OPENING_RAY_DEGREES, yaw + fov * 0.5)
		local kd, kt = depth(rayYaw, knee)
		local cd, ct = depth(rayYaw, chest)
		table.insert(rays, { yaw = rayYaw, knee = kd, chest = cd, thing = ct or kt })
	end
	local function openOf(r: any): number
		return math.min(r.knee, r.chest)
	end
	local function deeper(near: number): number
		return math.max(OPENING_DEEPER, OPENING_DEEPER_SHARE * near)
	end
	local function standing(r: any): boolean
		return r.knee < reach and r.chest < reach and math.abs(r.knee - r.chest) <= OPENING_SAME_HEIGHT
	end
	local function pointOf(degrees: number, distance: number): Vector3
		return Vector3.new(me.X, me.Y, 0) + dirOf(degrees) * distance
	end

	if openings then
		local gaps = {}
		-- the way along a flat wall beside a gap's side (from its ray to the next two), zero when it isn't one
		local function wallWay(sideI: number, stepDir: number): Vector3
			local a, b = sideI + stepDir, sideI + 2 * stepDir
			if a < 1 or b < 1 or a > n or b > n or not standing(rays[a]) or not standing(rays[b]) then
				return Vector3.zero
			end
			local p0 = pointOf(rays[sideI].yaw, openOf(rays[sideI]))
			local p1 = pointOf(rays[a].yaw, openOf(rays[a]))
			local p2 = pointOf(rays[b].yaw, openOf(rays[b]))
			local run = p2 - p0
			local length = len2d(run)
			if length < 10 then
				return Vector3.zero
			end
			local u = run / length
			local d = p1 - p0
			if math.abs(u.X * d.Y - u.Y * d.X) > OPENING_FLAT then
				return Vector3.zero
			end
			return u
		end
		local function refine(nearYaw: number, farYaw: number, nearDepth: number): Vector3
			for _ = 1, OPENING_EDGE_STEPS do
				local mid = (nearYaw + farYaw) * 0.5
				local o = math.min((depth(mid, knee)), (depth(mid, chest)))
				if o <= nearDepth + deeper(nearDepth) * 0.5 then
					nearYaw = mid
					nearDepth = o
				else
					farYaw = mid
				end
			end
			return pointOf(nearYaw, nearDepth)
		end
		local i = 1
		while i < n do
			if not standing(rays[i]) or openOf(rays[i + 1]) - openOf(rays[i]) < deeper(openOf(rays[i])) then
				i += 1
				continue
			end
			local nextI = i + 1
			for k = i + 2, n do
				if not standing(rays[k]) or openOf(rays[k - 1]) - openOf(rays[k]) < deeper(openOf(rays[k])) then
					continue
				end
				local l = pointOf(rays[i].yaw, openOf(rays[i]))
				local r = pointOf(rays[k].yaw, openOf(rays[k]))
				local acrossV = r - l
				local chord = len2d(acrossV)
				if chord < 1 then
					break
				end
				local out = Vector3.new(-acrossV.Y / chord, acrossV.X / chord, 0)
				local middle = (l + r) * 0.5
				if out:Dot(middle - flat(me)) < 0 then
					out = -out
				end
				local beyond = math.huge
				for j = i + 1, k - 1 do
					beyond = math.min(beyond, (pointOf(rays[j].yaw, openOf(rays[j])) - l):Dot(out))
				end
				if beyond < p.radius or chord < OPENING_SEEN_SHARE * p.radius then
					break
				end
				local near = refine(rays[i].yaw, rays[i + 1].yaw, openOf(rays[i]))
				local far = refine(rays[k].yaw, rays[k - 1].yaw, openOf(rays[k]))
				local along = wallWay(i, -1)
				if along ~= Vector3.zero then
					far = near + along * (far - near):Dot(along)
				else
					along = wallWay(k, 1)
					if along ~= Vector3.zero then
						near = far + along * (near - far):Dot(along)
					end
				end
				acrossV = far - near
				local width = len2d(acrossV)
				if width >= OPENING_SEEN_SHARE * p.radius then
					local gap: any = { along = acrossV / width, width = width, beyond = beyond,
						wall = rays[i].thing or rays[k].thing, near = rays[i].thing, far = rays[k].thing }
					gap.out = Vector3.new(-gap.along.Y, gap.along.X, 0)
					gap.middle = (near + far) * 0.5
					if gap.out:Dot(gap.middle - flat(me)) < 0 then
						gap.out = -gap.out
					end
					gap.distance = dist2d(gap.middle, me)
					table.insert(gaps, gap)
				end
				nextI = k -- (on past its far side)
				break
			end
			i = nextI
		end
		table.sort(gaps, function(a, b)
			return a.distance < b.distance
		end)
		local listed = {}
		local g = 0
		while g < #gaps and g < OPENINGS_TRIED and #listed < MAX_OPENINGS do
			g += 1
			local gap = gaps[g]
			local out = gap.out
			local middle = gap.middle
			local top = p.feet + chest
			local down = chest + 600
			local beforeOut = p.radius + 40
			local afterOut = p.radius + 60
			local before0 = middle - out * beforeOut
			local after0 = middle + out * afterOut
			local fb, zb, sb = p:floorAt(Vector3.new(before0.X, before0.Y, top), down)
			local fi, zi, si = p:floorAt(Vector3.new(middle.X, middle.Y, top), down)
			local fa, za, sa = p:floorAt(Vector3.new(after0.X, after0.Y, top), down)
			local hasFloor = fb and fi and fa
			local zBefore = if hasFloor then zb else p.feet
			local zIn = if hasFloor then zi else p.feet
			local zAfter = if hasFloor then za else p.feet
			if not sees(Vector3.new(middle.X, middle.Y, zIn + chest), 30) then
				continue -- hidden from the eyes
			end
			local stepBy = if hasFloor then math.max(math.abs(zIn - zBefore), math.abs(zAfter - zIn)) else -1
			local floorOk = hasFloor and sb and si and sa
				and p:walks(Vector3.new(before0.X, before0.Y, zBefore), Vector3.new(middle.X, middle.Y, zIn))
				and p:walks(Vector3.new(middle.X, middle.Y, zIn), Vector3.new(after0.X, after0.Y, zAfter))
			local passes = false
			for _, aside in ipairs({ 0, OPENING_ASIDE, -OPENING_ASIDE, 2 * OPENING_ASIDE, -2 * OPENING_ASIDE }) do
				local onLine = gap.middle + gap.along * aside
				local from = onLine - out * beforeOut
				local to = onLine + out * afterOut
				if floorOk and p:sweep(Vector3.new(from.X, from.Y, zBefore), Vector3.new(to.X, to.Y, zAfter)) == nil then
					passes = true
					middle = onLine
					break
				end
			end
			local after = middle + out * afterOut
			local blocked = p:sweep(Vector3.new(me.X, me.Y, p.feet), Vector3.new(after.X, after.Y, zAfter))
			local o: any = { at = vec(Vector3.new(middle.X, middle.Y, zIn)), turn = turnTo(me, middle),
				distance = math.round(dist2d(middle, me)), width = math.round(gap.width),
				yaw = math.round(math.deg(math.atan2(out.Y, out.X))), depth_beyond = math.round(math.min(gap.beyond, reach)) }
			if hasFloor then
				o.floor_step = math.round(stepBy)
				o.floor_above_feet = math.round(zIn - p.feet)
			end
			o.reachable_straight = blocked == nil
			if blocked then
				o.blocked_at = math.round(dist2d(blocked.at, me))
				local by = nameOf(blocked.instance)
				if by ~= "" then
					o.blocked_by = by
				end
			end
			o.passable = passes
			-- What it leads into: what is overhead just past it, else the smallest thing in view whose own box holds the
			-- place a body's width in from its sides
			local inside = Vector3.new(after.X, after.Y, zAfter + height)
			local over = p:line(inside, inside + Vector3.new(0, 0, 2000))
			local into = if over then nameOf(over.instance) else ""
			if into == "" then
				local smallest = math.huge
				for _, h in ipairs(holders) do
					local s = h[2]
					local d = inside - Vector3.new(0, 0, height * 0.5) - s.center
					local up = upIndex(s)
					local heldIn = s.half[up] * 2 >= height * 2
					for a = 1, 3 do
						if not heldIn then
							break
						end
						heldIn = math.abs(d:Dot(s.axis[a])) <= s.half[a] + (if a == up then 10 else -2 * p.radius)
					end
					local volume = s.half[1] * s.half[2] * s.half[3]
					if heldIn and volume < smallest then
						smallest = volume
						into = h[1]
					end
				end
			end
			if into ~= "" then
				o.into = into
			end
			local wallName = nameOf(gap.wall)
			if wallName ~= "" then
				o.wall = wallName
			end
			if gap.near and gap.far then
				o.sides = { nameOf(gap.near), nameOf(gap.far) }
			end
			table.insert(listed, o)
		end
		body.openings = listed
		local free = {}
		local from = 0
		while from < fov - 0.5 do
			local atKnee, atChest = reach, reach
			for _, r in ipairs(rays) do
				local off = r.yaw - left
				if off >= from - 0.01 and off < from + OPENING_SECTOR - 0.01 then
					atKnee = math.min(atKnee, r.knee)
					atChest = math.min(atChest, r.chest)
				end
			end
			table.insert(free, { turn = math.round((from + math.min(from + OPENING_SECTOR, fov)) * 0.5 - fov * 0.5),
				knee = math.round(atKnee / 10) * 10, chest = math.round(atChest / 10) * 10 })
			from += OPENING_SECTOR
		end
		body.free = free
	end

	if wantWaysUp then
		body.ways_up = waysUp(p, cam, yaw, fov, reach, knee, chest, height, rays, sees, nameOf)
	end
end

-- --- a thing's top surface ---------------------------------------------------------------------------------------

local SLOPE_MIN = 5.0
local SLOPE_MAX = 50.0
local SLOPE_END_INSET = 15.0
local SLOPE_END_STRIDE = 20.0
local SLOPE_END_STRIDES = 128
local SLOPE_END_HALVINGS = 5
local SLOPE_LANE_STATIONS = { 0.25, 0.5, 0.75 }
local SLOPE_LANE_STEP = 20.0
local SLOPE_LANE_DEEP = 3000.0
local SLOPE_LANE_BELOW = 1000.0
local SLOPE_LANE_SAMPLES = 64
local SLOPE_LANE_HALVINGS = 3

local function withZ(p: Vector3, z: number): Vector3
	return Vector3.new(p.X, p.Y, z)
end

-- The height of a thing's own box's top face over a place (kit), for a thing with nothing to collide with.
function Reach.topFaceAt(b: any, p: Vector3): number
	local k = upIndex(b)
	local nrm = topNormal(b)
	local top = b.center + nrm * b.half[k]
	if math.abs(nrm.Z) < 0.01 then
		return p.Z
	end
	return top.Z - (nrm.X * (p.X - top.X) + nrm.Y * (p.Y - top.Y)) / nrm.Z
end

-- A thing's top surface (Unreal's Surface, the Godot addon's reach.gd surface), into `item`: `top_slope_degrees`
-- along the way its own box's top face rises most (on its own collision: a WedgePart's slope too); for a slope a body
-- could walk (5 to 50 degrees) its `low_end`, `high_end` (each only where seen), its `lane` and `uphill`. ctx: "box"
-- (Reach.ownBox), "tipped", "heightAt"(p) (its surface straight down through a kit place), "isOpen"(p) (a body's room
-- is clear just over a place on it: nothing drawn that stops the body there but the thing and characters),
-- "floorBeside"(start, depth) (found, z: the floor straight down, the thing left out), "seesPoint"(p) (on screen and
-- in sight), "place"(p) ({"at", "turn", "distance", "height_above_feet"}), "radius", "stepUp", "camYaw".
function Reach.surface(item: any, ctx: any)
	local b = ctx.box
	local k = upIndex(b)
	local top = b.center + topNormal(b) * b.half[k]
	local heightAt, isOpen, seesPoint, place = ctx.heightAt, ctx.isOpen, ctx.seesPoint, ctx.place
	local found = false
	local steepest = 0
	local along = 0
	local lowEnd, highEnd = top, top
	for i = 1, 3 do
		if i == k then
			continue
		end
		local reachBy = math.max(0, b.half[i] - math.min(SLOPE_END_INSET, b.half[i] * 0.25))
		local one = top + b.axis[i] * reachBy
		local other = top - b.axis[i] * reachBy
		one = withZ(one, heightAt(one))
		other = withZ(other, heightAt(other))
		local run = dist2d(one, other)
		if run < 1 then
			continue
		end
		local slope = math.deg(math.atan2(math.abs(one.Z - other.Z), run))
		if not found or slope > steepest then
			found = true
			steepest = slope
			along = i
			lowEnd = if one.Z <= other.Z then one else other
			highEnd = if one.Z <= other.Z then other else one
		end
	end
	if not found or (steepest < 1 and not ctx.tipped) then
		return -- level on top: nothing to tell
	end
	item.top_slope_degrees = math.round(steepest)
	if steepest < SLOPE_MIN or steepest > SLOPE_MAX or highEnd.Z - lowEnd.Z < 10 then
		return
	end
	local acrossI = 6 - k - along
	local axisA, halfA = b.axis[acrossI], b.half[acrossI]
	local function atT(from: Vector3, to: Vector3, t: number): Vector3
		local p = from:Lerp(to, t)
		return withZ(p, heightAt(p))
	end
	-- the first place from `from` toward `to` where a body's room over it is clear
	local function firstOpen(from: Vector3, to: Vector3): Vector3?
		local strides = math.clamp(math.ceil(dist2d(from, to) / SLOPE_END_STRIDE), 1, SLOPE_END_STRIDES)
		local covered = 0
		for i = 0, strides do
			local t = i / strides
			if not isOpen(atT(from, to, t)) then
				covered = t
				continue
			end
			local hi = t
			if i > 0 then
				for _ = 1, SLOPE_END_HALVINGS do
					local mid = (covered + hi) * 0.5
					if isOpen(atT(from, to, mid)) then
						hi = mid
					else
						covered = mid
					end
				end
			end
			return atT(from, to, hi)
		end
		return nil
	end
	local function endAt(edge: Vector3, other: Vector3): Vector3?
		for _, share in ipairs({ 0, 1 / 3, -1 / 3, 2 / 3, -2 / 3 }) do
			local side = axisA * (halfA * share)
			local p = firstOpen(edge + side, other + side)
			if p and seesPoint(p) then
				return p
			end
		end
		return nil
	end
	local e = endAt(lowEnd, highEnd)
	if e then
		item.low_end = place(e)
	end
	e = endAt(highEnd, lowEnd)
	if e then
		item.high_end = place(e)
	end
	-- The lane a body walks up it (see Unreal's Surface)
	local stepUp, bodyRadius = ctx.stepUp, ctx.radius
	local up2 = flat(highEnd - lowEnd).Unit
	local right2 = Vector3.new(-up2.Y, up2.X, 0)
	local rightSign = if axisA:Dot(right2) >= 0 then 1 else -1
	local edgeQ = math.max(0, halfA - math.min(SLOPE_END_INSET, halfA * 0.25))
	local samples = math.clamp(math.ceil(2 * edgeQ / SLOPE_LANE_STEP), 2, SLOPE_LANE_SAMPLES)
	local function worse(kept: any, here: any)
		if (here.by == 2 and (kept.by ~= 2 or here.depth > kept.depth)) or (here.by == 1 and kept.by == 0) then
			kept.by = here.by
			kept.depth = here.depth
		end
	end
	local function past(middle: Vector3, q: number, sign: number): any
		local z = heightAt(middle + axisA * q)
		local p = withZ(middle + axisA * (sign * (halfA + bodyRadius)), z)
		if not isOpen(p) then
			return { by = 1, depth = 0 }
		end
		local ok, fz = ctx.floorBeside(Vector3.new(p.X, p.Y, z + stepUp), stepUp + SLOPE_LANE_DEEP)
		if not ok then
			return { by = 2, depth = SLOPE_LANE_DEEP }
		end
		local fall = z - fz
		return if fall > stepUp then { by = 2, depth = fall } else { by = 0, depth = 0 }
	end
	local sides = { { by = 0, depth = 0 }, { by = 0, depth = 0 } }
	local narrowest, offsets, runs = 0, 0, 0
	local seenLane = false
	for _, t in ipairs(SLOPE_LANE_STATIONS) do
		local middle = lowEnd:Lerp(highEnd, t)
		local function atQ(q: number): Vector3
			local p = middle + axisA * q
			return withZ(p, heightAt(p))
		end
		local function qOf(i: number): number
			return -edgeQ + 2 * edgeQ * i / samples
		end
		local free = {}
		for i = 0, samples do
			free[i] = isOpen(atQ(qOf(i)))
		end
		local runFrom, runTo = -1, -1
		local i = 0
		while i <= samples do
			if not free[i] then
				i += 1
				continue
			end
			local j = i
			while j < samples and free[j + 1] do
				j += 1
			end
			if runFrom < 0 or j - i > runTo - runFrom then
				runFrom, runTo = i, j
			end
			i = j + 1
		end
		if runFrom < 0 then
			continue
		end
		local function halve(openQ: number, coveredQ: number): number
			for _ = 1, SLOPE_LANE_HALVINGS do
				local mid = (openQ + coveredQ) * 0.5
				if isOpen(atQ(mid)) then
					openQ = mid
				else
					coveredQ = mid
				end
			end
			return openQ
		end
		local lo = if runFrom > 0 then halve(qOf(runFrom), qOf(runFrom - 1)) else -edgeQ
		local hi = if runTo < samples then halve(qOf(runTo), qOf(runTo + 1)) else edgeQ
		narrowest = if runs == 0 then hi - lo else math.min(narrowest, hi - lo)
		offsets += (lo + hi) * 0.5
		runs += 1
		local wall = { by = 1, depth = 0 }
		worse(sides[1], if runTo < samples then wall else past(middle, edgeQ, 1))
		worse(sides[2], if runFrom > 0 then wall else past(middle, -edgeQ, -1))
		seenLane = seenLane or seesPoint(atQ((lo + hi) * 0.5))
	end
	if runs > 0 and seenLane then
		local shift = axisA * (offsets / runs)
		local lane: any = { width = math.round(narrowest) }
		local halfway = lowEnd:Lerp(highEnd, 0.5) + shift
		lane.middle = vec(withZ(halfway, heightAt(halfway)))
		local foot = firstOpen(lowEnd + shift, highEnd + shift)
		local hasFoot = foot ~= nil and seesPoint(foot)
		if hasFoot then
			lane.foot = place(foot)
		end
		local topP = firstOpen(highEnd + shift, lowEnd + shift)
		if topP and seesPoint(topP) then
			lane.top = place(topP)
		end
		local function sideOf(kind: any): any
			local o: any = { by = if kind.by == 2 then "drop" elseif kind.by == 1 then "wall" else "open" }
			if kind.by == 2 then
				o.depth = math.round(kind.depth)
			end
			return o
		end
		lane.right = sideOf(if rightSign > 0 then sides[1] else sides[2])
		lane.left = sideOf(if rightSign > 0 then sides[2] else sides[1])
		if hasFoot and foot then
			local z = foot.Z
			local clear = SLOPE_LANE_BELOW
			local by = "reach"
			local d = SLOPE_LANE_STEP
			while d <= SLOPE_LANE_BELOW do
				local p = foot - up2 * d
				local ok, fz = ctx.floorBeside(Vector3.new(p.X, p.Y, z + stepUp), 2 * stepUp + 1)
				if not ok then
					p = withZ(p, z)
					clear = d - SLOPE_LANE_STEP
					by = if not isOpen(p) then "wall" elseif seesPoint(p) then "drop" else "unseen"
					break
				end
				p = withZ(p, fz)
				if not isOpen(p) then
					clear = d - SLOPE_LANE_STEP
					by = "wall"
					break
				end
				if not seesPoint(p) then
					clear = d - SLOPE_LANE_STEP
					by = "unseen"
					break
				end
				z = p.Z
				d += SLOPE_LANE_STEP
			end
			lane.below = { clear = math.round(clear), by = by }
		end
		item.lane = lane
	end
	local yaw = math.deg(math.atan2(highEnd.Y - lowEnd.Y, highEnd.X - lowEnd.X))
	item.uphill = { yaw = math.round(yaw), turn = math.round(wrap180(yaw - ctx.camYaw)) }
end

return Reach
