--!strict
-- The kit frame: every op but `rc` speaks Unreal's frame, so the brain's geometry is the same in every engine.
--
-- Roblox: x right, y up, -z forward (a CFrame's LookVector at rest), studs, right-handed. The kit: X forward, Y right,
-- Z up, centimetres, with 1 stud = 28 cm (Roblox's own conversion):
--     kit (X, Y, Z) = 28 * (-z, x, y)
-- Yaw: degrees about Z from +X toward +Y (turning right is positive) = -deg(ry) of CFrame:ToEulerAnglesYXZ() (0 looking
-- along -z); pitch positive up = deg(rx); roll = -deg(rz).
local Frame = {}

Frame.SCALE = 28.0 -- centimetres per stud

function Frame.r2(x: number): number
	if x ~= x or x == math.huge or x == -math.huge then
		return x
	end
	return math.round(x * 100) / 100
end

local r2 = Frame.r2
local S = Frame.SCALE

function Frame.kit(v: Vector3): { [string]: number }
	return { X = r2(-v.Z * S), Y = r2(v.X * S), Z = r2(v.Y * S) }
end

-- A direction (or any vector) on the kit's axes, unscaled.
function Frame.kitDir(v: Vector3): { [string]: number }
	return { X = r2(-v.Z), Y = r2(v.X), Z = r2(v.Y) }
end

-- A box's half size, on the kit's axes, in centimetres.
function Frame.kitExtent(size: Vector3): { [string]: number }
	return { X = r2(math.abs(size.Z) * S * 0.5), Y = r2(math.abs(size.X) * S * 0.5), Z = r2(math.abs(size.Y) * S * 0.5) }
end

-- A box's full size, on the kit's axes, in centimetres.
function Frame.kitSize(size: Vector3): { [string]: number }
	return { X = r2(math.abs(size.Z) * S), Y = r2(math.abs(size.X) * S), Z = r2(math.abs(size.Y) * S) }
end

local function num(t: any, key: string, alt: string): number
	if type(t) ~= "table" then
		return 0
	end
	local v = t[key]
	if v == nil then
		v = t[alt]
	end
	return tonumber(v) or 0
end

-- A kit vector ({"X","Y","Z"}) on Roblox's axes, unscaled (a direction).
function Frame.fromKitDir(d: any): Vector3
	return Vector3.new(num(d, "Y", "y"), num(d, "Z", "z"), -num(d, "X", "x"))
end

-- A kit location (centimetres) as a Roblox point (studs).
function Frame.fromKit(d: any): Vector3
	return Frame.fromKitDir(d) / S
end

-- Kit yaw of a Roblox direction (degrees, -180..180).
function Frame.yawOfDir(d: Vector3): number
	return math.deg(math.atan2(d.X, -d.Z))
end

-- Kit pitch of a Roblox direction (degrees, up positive).
function Frame.pitchOfDir(d: Vector3): number
	local m = d.Magnitude
	if m < 1e-6 then
		return 0
	end
	return math.deg(math.asin(math.clamp(d.Y / m, -1, 1)))
end

-- A Roblox horizontal direction from a kit yaw.
function Frame.dirOfYaw(yaw: number): Vector3
	local a = math.rad(yaw)
	return Vector3.new(math.sin(a), 0, -math.cos(a))
end

function Frame.kitRot(cf: CFrame): { [string]: number }
	local rx, ry, rz = cf:ToEulerAnglesYXZ()
	return { Pitch = r2(math.deg(rx)), Yaw = r2(-math.deg(ry)), Roll = r2(-math.deg(rz)) }
end

function Frame.yawOf(cf: CFrame): number
	return r2(Frame.yawOfDir(cf.LookVector))
end

function Frame.pitchOf(cf: CFrame): number
	return r2(Frame.pitchOfDir(cf.LookVector))
end

function Frame.wrap180(a: number): number
	return ((a + 180) % 360) - 180
end

function Frame.finite(v: Vector3): boolean
	return v.X == v.X and v.Y == v.Y and v.Z == v.Z and math.abs(v.X) ~= math.huge and math.abs(v.Y) ~= math.huge
		and math.abs(v.Z) ~= math.huge
end

return Frame
