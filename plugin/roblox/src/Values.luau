--!strict
-- Roblox values to and from JSON, for `rc` (plugin/roblox/README.md, "Values"). Values are Roblox's own: studs, its
-- axes (x right, y up, -z forward); Vector3 as {"x","y","z"}, CFrame as {"position", "look", "up", "euler"} (euler:
-- ToEulerAnglesYXZ, radians), Color3 as {"r","g","b"} (0..1), an EnumItem as its name, an Instance as its path, a
-- BrickColor as its name; Luau tables (a ModuleScript's state) as objects and lists. Writes convert to the type the
-- value has now (or the property's declared type).
local Json = require(script.Parent.Json)
local Paths = require(script.Parent.Paths)

local Values = {}

local MAX_DEPTH = 6
local MAX_ITEMS = 500

local function r(x: number): number
	if x ~= x then
		return x
	end
	return math.round(x * 10000) / 10000
end

local function vec3(v: Vector3)
	return { x = r(v.X), y = r(v.Y), z = r(v.Z) }
end

local toJson: (any, number?) -> any
toJson = function(v: any, depth: number?): any
	local d = depth or 0
	local t = typeof(v)
	if v == nil then
		return Json.null
	elseif t == "boolean" or t == "string" then
		return v
	elseif t == "number" then
		if v ~= v or v == math.huge or v == -math.huge then
			return Json.null
		end
		return v
	elseif t == "Vector3" then
		return vec3(v)
	elseif t == "Vector2" then
		return { x = r(v.X), y = r(v.Y) }
	elseif t == "Vector3int16" then
		return { x = v.X, y = v.Y, z = v.Z }
	elseif t == "Vector2int16" then
		return { x = v.X, y = v.Y }
	elseif t == "CFrame" then
		local rx, ry, rz = v:ToEulerAnglesYXZ()
		return { position = vec3(v.Position), look = vec3(v.LookVector), up = vec3(v.UpVector),
			euler = { x = r(rx), y = r(ry), z = r(rz) } }
	elseif t == "Color3" then
		return { r = r(v.R), g = r(v.G), b = r(v.B), hex = v:ToHex() }
	elseif t == "BrickColor" then
		return v.Name
	elseif t == "EnumItem" then
		return v.Name
	elseif t == "Enum" then
		return tostring(v)
	elseif t == "Instance" then
		return Paths.of(v)
	elseif t == "UDim" then
		return { scale = r(v.Scale), offset = v.Offset }
	elseif t == "UDim2" then
		return { x = { scale = r(v.X.Scale), offset = v.X.Offset }, y = { scale = r(v.Y.Scale), offset = v.Y.Offset } }
	elseif t == "NumberRange" then
		return { min = r(v.Min), max = r(v.Max) }
	elseif t == "Rect" then
		return { min = { x = r(v.Min.X), y = r(v.Min.Y) }, max = { x = r(v.Max.X), y = r(v.Max.Y) } }
	elseif t == "NumberSequence" then
		local out = {}
		for _, k in ipairs(v.Keypoints) do
			table.insert(out, { time = r(k.Time), value = r(k.Value) })
		end
		return out
	elseif t == "ColorSequence" then
		local out = {}
		for _, k in ipairs(v.Keypoints) do
			table.insert(out, { time = r(k.Time), color = k.Value:ToHex() })
		end
		return out
	elseif t == "Ray" then
		return { origin = vec3(v.Origin), direction = vec3(v.Direction) }
	elseif t == "Region3" then
		return { cframe = toJson(v.CFrame), size = vec3(v.Size) }
	elseif t == "Font" then
		return { family = v.Family, weight = v.Weight.Name, style = v.Style.Name }
	elseif t == "DateTime" then
		return v:ToIsoDate()
	elseif t == "PhysicalProperties" then
		return { density = r(v.Density), friction = r(v.Friction), elasticity = r(v.Elasticity) }
	elseif t == "Content" then
		local ok, uri = pcall(function()
			return (v :: any).Uri
		end)
		return if ok and uri then uri else tostring(v)
	elseif t == "table" then
		if d >= MAX_DEPTH then
			return "<table>"
		end
		local n = #v
		local count = 0
		local array = true
		for k in pairs(v) do
			count += 1
			if type(k) ~= "number" or k < 1 or k > n or k % 1 ~= 0 then
				array = false
			end
		end
		if array and count == n and n > 0 then
			local out = {}
			for i = 1, math.min(n, MAX_ITEMS) do
				out[i] = toJson(v[i], d + 1)
			end
			return out
		end
		local out = Json.object()
		local i = 0
		for k, x in pairs(v) do
			i += 1
			if i > MAX_ITEMS then
				out["..."] = string.format("%d more", count - MAX_ITEMS)
				break
			end
			out[tostring(k)] = toJson(x, d + 1)
		end
		return out
	elseif t == "function" then
		return "<function>"
	elseif t == "RBXScriptSignal" then
		return "<event>"
	end
	return tostring(v)
end
Values.toJson = toJson

local function num(x: any, default: number): number
	local n = tonumber(x)
	return if n ~= nil then n else default
end

local function field(t: any, ...: string): any
	if type(t) ~= "table" then
		return nil
	end
	for _, k in ipairs({ ... }) do
		if t[k] ~= nil then
			return t[k]
		end
	end
	return nil
end

local function toVector3(v: any, current: Vector3?): Vector3?
	if typeof(v) == "Vector3" then
		return v
	end
	if type(v) == "table" then
		local base = current or Vector3.zero
		if #v == 3 then
			return Vector3.new(num(v[1], 0), num(v[2], 0), num(v[3], 0))
		end
		return Vector3.new(num(field(v, "x", "X"), base.X), num(field(v, "y", "Y"), base.Y), num(field(v, "z", "Z"), base.Z))
	end
	return nil
end

local function toColor(v: any): Color3?
	if type(v) == "string" then
		local hex = string.match(v, "^#?(%x%x%x%x%x%x)$")
		if hex then
			return Color3.fromHex(hex)
		end
		local ok, bc = pcall(BrickColor.new, v)
		return if ok then bc.Color else nil
	end
	if type(v) == "table" then
		local rr, g, b = num(field(v, "r", "R"), 0), num(field(v, "g", "G"), 0), num(field(v, "b", "B"), 0)
		if rr > 1 or g > 1 or b > 1 then
			return Color3.fromRGB(rr, g, b)
		end
		return Color3.new(rr, g, b)
	end
	return nil
end

local function toCFrame(v: any, current: CFrame?): CFrame?
	if typeof(v) == "CFrame" then
		return v
	end
	if type(v) ~= "table" then
		return nil
	end
	local base = current or CFrame.identity
	local pos = toVector3(field(v, "position", "Position", "p") or (field(v, "x", "X") ~= nil and v or nil), base.Position)
		or base.Position
	local look = toVector3(field(v, "look", "LookVector"))
	local euler = field(v, "euler")
	if look and look.Magnitude > 1e-6 then
		local up = toVector3(field(v, "up", "UpVector")) or base.UpVector
		return CFrame.lookAt(pos, pos + look, up)
	elseif type(euler) == "table" then
		return CFrame.new(pos) * CFrame.fromEulerAnglesYXZ(num(euler.x, 0), num(euler.y, 0), num(euler.z, 0))
	end
	return base.Rotation + pos
end

-- An EnumItem of an Enum by name or number.
local function toEnum(v: any, enum: Enum?): EnumItem?
	if typeof(v) == "EnumItem" then
		return v
	end
	if enum == nil then
		return nil
	end
	for _, item in ipairs((enum :: any):GetEnumItems()) do
		if (type(v) == "string" and (item.Name == v or tostring(item) == v)) or (type(v) == "number" and item.Value == v) then
			return item
		end
	end
	return nil
end

-- A JSON value as the Roblox value a property holds: like `current` (the value there now) or of the declared type
-- (a ReflectionService ScriptType such as "Vector3", and the Enum for an enum property).
function Values.fromJson(v: any, current: any, declared: string?, enum: Enum?): any
	local t = if current ~= nil then typeof(current) else (declared or "")
	if v == Json.null then
		return nil
	end
	if t == "Vector3" then
		return toVector3(v, current) or v
	elseif t == "Vector2" and type(v) == "table" then
		return Vector2.new(num(field(v, "x", "X"), 0), num(field(v, "y", "Y"), 0))
	elseif t == "CFrame" then
		return toCFrame(v, current) or v
	elseif t == "Color3" then
		return toColor(v) or v
	elseif t == "BrickColor" then
		if type(v) == "string" then
			return BrickColor.new(v)
		end
		local c = toColor(v)
		return if c then BrickColor.new(c) else v
	elseif t == "EnumItem" then
		return toEnum(v, enum or (current and (current :: any).EnumType)) or v
	elseif t == "Instance" or t == "Object" then
		if type(v) == "string" then
			return Paths.resolve(v)
		end
		return v
	elseif t == "number" or t == "float" or t == "double" or t == "int" or t == "int64" then
		return tonumber(v) or v
	elseif t == "boolean" or t == "bool" then
		if type(v) == "string" then
			return v == "true" or v == "1"
		end
		return v and true or false
	elseif t == "string" then
		return if type(v) == "string" then v else tostring(v)
	elseif t == "UDim2" and type(v) == "table" then
		local x, y = v.x or v.X or {}, v.y or v.Y or {}
		return UDim2.new(num(x.scale, 0), num(x.offset, 0), num(y.scale, 0), num(y.offset, 0))
	elseif t == "UDim" and type(v) == "table" then
		return UDim.new(num(v.scale, 0), num(v.offset, 0))
	elseif t == "NumberRange" and type(v) == "table" then
		return NumberRange.new(num(v.min, 0), num(v.max, num(v.min, 0)))
	end
	return Values.infer(v)
end

-- A JSON value with no type to follow: a path that names an instance is that instance; the rest as JSON has it.
function Values.infer(v: any): any
	if type(v) == "string" and string.sub(v, 1, 1) == "/" then
		local inst = Paths.resolve(v)
		if inst then
			return inst
		end
	end
	if type(v) == "table" and Json.isObject(v) then
		local x, y, z = field(v, "x", "X"), field(v, "y", "Y"), field(v, "z", "Z")
		local keys = 0
		for _ in pairs(v) do
			keys += 1
		end
		if keys == 3 and tonumber(x) and tonumber(y) and tonumber(z) then
			return Vector3.new(tonumber(x) :: number, tonumber(y) :: number, tonumber(z) :: number)
		end
	end
	return v
end

return Values
