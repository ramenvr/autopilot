--!nocheck
-- Autopilot's reads of a Roblox place, run through Studio's execute_luau (autopilot/engines/roblox_connector.py). The brain sends
-- this prelude with a request (REQ, appended after it: {ops = {{op, args}...}, player, nonce, echo}); each op is one
-- function of O below, run under pcall, and the answers come back as one JSON string, the chunk's return value (and
-- printed between the nonce markers when REQ.echo is set). Values are Roblox's own (studs, Roblox's axes): the brain
-- turns them into the kit's frame. Instance paths are "Service/Child/Grandchild", a name's "/" "@" "\" escaped with
-- "\", and a child that shares its name with earlier siblings numbered "Name@1", "Name@2"... (in GetChildren order).

local HttpService = game:GetService("HttpService")
local Players = game:GetService("Players")
local RunService = game:GetService("RunService")
local CollectionService = game:GetService("CollectionService")

local A = {}
local O = {}
local OVERRIDE = nil -- (set from REQ.player below: the instance path of the character to play, when one was named)

-- -- paths ----------------------------------------------------------------------------------------------------------

local function esc(name)
	name = string.gsub(name, "\\", "\\\\")
	name = string.gsub(name, "/", "\\/")
	name = string.gsub(name, "@", "\\@")
	return name
end

local function unesc(s)
	return (string.gsub(s, "\\(.)", "%1"))
end

local function seg(inst)
	local s = esc(inst.Name)
	local p = inst.Parent
	if p and p ~= game then
		local k = 0
		for _, c in ipairs(p:GetChildren()) do
			if c == inst then
				break
			end
			if c.Name == inst.Name then
				k += 1
			end
		end
		if k > 0 then
			s = s .. "@" .. k
		end
	end
	return s
end

function A.pathOf(inst)
	if typeof(inst) ~= "Instance" then
		return ""
	end
	local parts = {}
	local cur = inst
	while cur and cur ~= game do
		table.insert(parts, 1, seg(cur))
		cur = cur.Parent
	end
	if cur ~= game then
		return "" -- (not in the data model: destroyed, or never parented)
	end
	return table.concat(parts, "/")
end

local function splitPath(path)
	local parts, cur, i, n = {}, {}, 1, #path
	while i <= n do
		local c = string.sub(path, i, i)
		if c == "\\" and i < n then
			table.insert(cur, string.sub(path, i, i + 1))
			i += 2
		elseif c == "/" then
			table.insert(parts, table.concat(cur))
			cur = {}
			i += 1
		else
			table.insert(cur, c)
			i += 1
		end
	end
	table.insert(parts, table.concat(cur))
	return parts
end

local function parseSeg(s)
	local at, i = nil, 1
	while i <= #s do
		local c = string.sub(s, i, i)
		if c == "\\" then
			i += 2
		else
			if c == "@" then
				at = i
			end
			i += 1
		end
	end
	if at and string.match(string.sub(s, at + 1), "^%d+$") then
		return unesc(string.sub(s, 1, at - 1)), tonumber(string.sub(s, at + 1))
	end
	return unesc(s), 0
end

function A.resolve(path)
	if typeof(path) == "Instance" then
		return path
	end
	if type(path) ~= "string" or path == "" then
		return nil
	end
	if path == "game" then
		return game
	end
	local cur = nil
	for i, s in ipairs(splitPath(path)) do
		local name, k = parseSeg(s)
		if i == 1 then
			cur = game:FindFirstChild(name)
			if not cur then
				local ok, svc = pcall(game.GetService, game, name)
				cur = ok and svc or nil
			end
		else
			local found, n = nil, 0
			for _, c in ipairs(cur:GetChildren()) do
				if c.Name == name then
					if n == k then
						found = c
						break
					end
					n += 1
				end
			end
			cur = found
		end
		if not cur then
			return nil
		end
	end
	return cur
end

function A.need(path)
	local inst = A.resolve(path)
	if not inst then
		error("Object " .. tostring(path) .. " does not exist", 0)
	end
	return inst
end

-- -- values ---------------------------------------------------------------------------------------------------------

local function num(x)
	if x ~= x or x == math.huge or x == -math.huge then
		return nil
	end
	return x
end

local function v3(v)
	return { x = num(v.X), y = num(v.Y), z = num(v.Z) }
end
A.v3 = v3

local DEG = 180 / math.pi

function A.enc(v, depth)
	depth = depth or 0
	local t = typeof(v)
	if t == "number" then
		return num(v)
	elseif t == "string" or t == "boolean" or t == "nil" then
		return v
	elseif t == "Vector3" then
		return v3(v)
	elseif t == "Vector2" then
		return { x = num(v.X), y = num(v.Y) }
	elseif t == "CFrame" then
		local rx, ry, rz = v:ToOrientation()
		return { x = num(v.X), y = num(v.Y), z = num(v.Z), orientation = { x = rx * DEG, y = ry * DEG, z = rz * DEG },
			look = v3(v.LookVector) }
	elseif t == "Color3" then
		return { r = v.R, g = v.G, b = v.B }
	elseif t == "BrickColor" then
		return v.Name
	elseif t == "EnumItem" then
		return v.Name
	elseif t == "Instance" then
		return A.pathOf(v)
	elseif t == "UDim2" then
		return { x_scale = v.X.Scale, x_offset = v.X.Offset, y_scale = v.Y.Scale, y_offset = v.Y.Offset }
	elseif t == "UDim" then
		return { scale = v.Scale, offset = v.Offset }
	elseif t == "NumberRange" then
		return { min = v.Min, max = v.Max }
	elseif t == "table" then
		if depth > 4 then
			return "(table)"
		end
		local n = #v
		local out = {}
		if n > 0 then
			for i = 1, n do
				out[i] = A.enc(v[i], depth + 1)
				if out[i] == nil then
					out[i] = false
				end
			end
			return out
		end
		local count = 0
		for k, x in pairs(v) do
			count += 1
			if count > 200 then
				break
			end
			out[tostring(k)] = A.enc(x, depth + 1)
		end
		return out
	elseif t == "function" then
		return "(function)"
	end
	return tostring(v)
end

-- A JSON value from the brain as Roblox's: {x, y, z} -> Vector3, {path = "..."} -> the Instance, lists element-wise
function A.dec(v)
	if type(v) ~= "table" then
		return v
	end
	local keys = 0
	for _ in pairs(v) do
		keys += 1
	end
	if keys == 3 and type(v.x) == "number" and type(v.y) == "number" and type(v.z) == "number" then
		return Vector3.new(v.x, v.y, v.z)
	end
	if keys == 1 and type(v.path) == "string" then
		return A.need(v.path)
	end
	local out = {}
	for k, x in pairs(v) do
		out[k] = A.dec(x)
	end
	return out
end

-- -- the player -----------------------------------------------------------------------------------------------------

function A.isClient()
	return Players.LocalPlayer ~= nil
end

function A.player()
	if OVERRIDE then
		local ch = A.resolve(OVERRIDE)
		local pl = ch and Players:GetPlayerFromCharacter(ch)
		if pl then
			return pl
		end
	end
	return Players.LocalPlayer or Players:GetPlayers()[1]
end

function A.character()
	if OVERRIDE then
		local ch = A.resolve(OVERRIDE)
		if ch then
			return ch
		end
	end
	local pl = A.player()
	return pl and pl.Character or nil
end

function A.root(ch)
	ch = ch or A.character()
	if not ch then
		return nil
	end
	return ch:FindFirstChild("HumanoidRootPart") or (ch:IsA("Model") and ch.PrimaryPart) or nil
end

function A.humanoid(ch)
	ch = ch or A.character()
	return ch and ch:FindFirstChildOfClass("Humanoid") or nil
end

local function excludeCharacter()
	local params = RaycastParams.new()
	params.FilterType = Enum.RaycastFilterType.Exclude
	local ch = A.character()
	params.FilterDescendantsInstances = ch and { ch } or {}
	return params
end

-- -- where things are -----------------------------------------------------------------------------------------------

-- location, orientation (degrees), velocity, box center, box size of a thing with a place in the world, else nil
function A.geom(inst)
	if inst:IsA("BasePart") then
		return inst.Position, inst.Orientation, inst.AssemblyLinearVelocity, inst.Position, inst.Size
	elseif inst:IsA("Model") then
		local pivot = inst:GetPivot()
		local ok, box, size = pcall(inst.GetBoundingBox, inst)
		if not ok then
			box, size = pivot, Vector3.zero
		end
		local pp = inst.PrimaryPart or inst:FindFirstChild("HumanoidRootPart")
		local rx, ry, rz = pivot:ToOrientation()
		return pivot.Position, Vector3.new(rx * DEG, ry * DEG, rz * DEG),
			(pp and pp:IsA("BasePart")) and pp.AssemblyLinearVelocity or Vector3.zero, box.Position, size
	elseif inst:IsA("Attachment") then
		return inst.WorldPosition, inst.WorldOrientation, Vector3.zero, inst.WorldPosition, Vector3.zero
	end
	return nil
end

-- The place's things: Workspace's children (through Folders), and what is tagged in it (not every part of a model)
function A.things()
	local out, seen = {}, {}
	local function add(inst)
		if not seen[inst] then
			seen[inst] = true
			table.insert(out, inst)
		end
	end
	local function walk(parent)
		for _, c in ipairs(parent:GetChildren()) do
			if c:IsA("Folder") then
				walk(c)
			elseif not (c:IsA("Terrain") or c:IsA("Camera")) and (c:IsA("BasePart") or c:IsA("Model")) then
				add(c)
			end
		end
	end
	walk(workspace)
	for _, tag in ipairs(CollectionService:GetAllTags()) do
		for _, inst in ipairs(CollectionService:GetTagged(tag)) do
			if inst:IsDescendantOf(workspace) then
				add(inst)
			end
		end
	end
	return out
end

local function isA(inst, cls)
	local ok, r = pcall(inst.IsA, inst, cls)
	return ok and r
end

local function tagsOf(inst)
	local t = CollectionService:GetTags(inst)
	return #t > 0 and t or nil
end

-- -- the ops --------------------------------------------------------------------------------------------------------

function O.context(a)
	local pl, ch, cam = A.player(), A.character(), workspace.CurrentCamera
	return {
		character = ch and A.pathOf(ch) or "",
		player = pl and pl.Name or "",
		camera = cam and A.pathOf(cam) or "",
		place = game.Name,
		place_id = game.PlaceId,
		viewport = cam and A.enc(cam.ViewportSize) or nil,
		running = RunService:IsRunning(),
		client = A.isClient(),
		tags = CollectionService:GetAllTags(),
	}
end

function O.find(a)
	local out, seen, limit = {}, {}, a.limit or 0
	local function add(inst)
		if seen[inst] or (limit > 0 and #out >= limit) then
			return
		end
		seen[inst] = true
		table.insert(out, A.pathOf(inst))
	end
	local cls = a.class
	local name = a.name and string.lower(a.name) or nil
	local function named(inst)
		return not name or string.find(string.lower(inst.Name), name, 1, true) ~= nil
	end
	if cls and string.sub(cls, 1, 4) == "tag:" then
		for _, inst in ipairs(CollectionService:GetTagged(string.sub(cls, 5))) do
			if named(inst) then
				add(inst)
			end
		end
	elseif cls then
		-- a class (IsA: subclasses too) or a CollectionService tag of that name
		for _, inst in ipairs(CollectionService:GetTagged(cls)) do
			if named(inst) then
				add(inst)
			end
		end
		for _, inst in ipairs(workspace:GetDescendants()) do
			if isA(inst, cls) and named(inst) then
				add(inst)
			end
		end
	else
		for _, inst in ipairs(a.things and A.things() or workspace:GetDescendants()) do
			if named(inst) then
				add(inst)
			end
		end
	end
	return { objects = out }
end

function O.transforms(a)
	local out = {}
	for _, path in ipairs(a.objects or {}) do
		local inst = A.resolve(path)
		if not inst then
			out[path] = false
		else
			local at, rot, vel, center, size = A.geom(inst)
			if at then
				out[path] = { location = v3(at), rotation = v3(rot), velocity = v3(vel), center = v3(center),
					size = v3(size), active = inst:IsDescendantOf(workspace) }
			else
				out[path] = { active = inst:IsDescendantOf(game) }
			end
		end
	end
	return { objects = out }
end

function O.pose(a)
	local ch = A.character()
	local root, hum = A.root(ch), A.humanoid(ch)
	if not root then
		return { character = ch and A.pathOf(ch) or "" }
	end
	local cam = workspace.CurrentCamera
	local look, eye = root.CFrame.LookVector, root.Position
	if A.isClient() and cam then
		look, eye = cam.CFrame.LookVector, cam.CFrame.Position
	else
		local head = ch:FindFirstChild("Head")
		if head and head:IsA("BasePart") then
			eye = head.Position
		end
	end
	return {
		character = A.pathOf(ch),
		at = v3(root.Position),
		eye = v3(eye),
		look = v3(look),
		facing = v3(root.CFrame.LookVector),
		velocity = v3(root.AssemblyLinearVelocity),
		on_ground = hum and hum.FloorMaterial ~= Enum.Material.Air or nil,
		state = hum and hum:GetState().Name or nil,
		health = hum and num(hum.Health) or nil,
		max_health = hum and num(hum.MaxHealth) or nil,
		fallen = root.Position.Y < workspace.FallenPartsDestroyHeight,
	}
end

function O.movement(a)
	local ch = A.character()
	local root, hum = A.root(ch), A.humanoid(ch)
	if not hum then
		error("movement: no Humanoid in the player's character", 0)
	end
	return {
		walk_speed = hum.WalkSpeed,
		use_jump_power = hum.UseJumpPower,
		jump_power = hum.JumpPower,
		jump_height = hum.JumpHeight,
		hip_height = hum.HipHeight,
		root_half = root and root.Size.Y / 2 or nil,
		radius = root and math.max(root.Size.X, root.Size.Z) / 2 or nil,
		max_slope = hum.MaxSlopeAngle,
		gravity = workspace.Gravity,
		rig = hum.RigType.Name,
	}
end

-- A property of an instance; else its attribute; else the Value of a child value object of that name (leaderstats)
local function read(inst, prop)
	local ok, v = pcall(function()
		return inst[prop]
	end)
	if ok and typeof(v) ~= "Instance" then
		return v, true
	end
	local attr = inst:GetAttribute(prop)
	if attr ~= nil then
		return attr, true
	end
	if ok then
		local child = v
		if child:IsA("ValueBase") then
			return child.Value, true
		end
		return child, true
	end
	local child = inst:FindFirstChild(prop)
	if child and child:IsA("ValueBase") then
		return child.Value, true
	end
	return nil, false
end

local COMMON = { "Name", "ClassName", "Parent", "Position", "Orientation", "Size", "Anchored", "CanCollide", "CanTouch",
	"Transparency", "Color", "Material", "Enabled", "Visible", "Text", "Value", "Health", "MaxHealth", "WalkSpeed",
	"JumpHeight", "JumpPower", "HipHeight", "ActionText", "ObjectText", "KeyboardKeyCode", "MaxActivationDistance",
	"HoldDuration", "RequiresLineOfSight", "Team", "Neutral", "PrimaryPart" }

function O.props(a)
	local inst = A.need(a.path)
	local out = {}
	for _, p in ipairs(COMMON) do
		local ok, v = pcall(function()
			return inst[p]
		end)
		if ok and v ~= nil then
			out[p] = A.enc(v)
		end
	end
	for k, v in pairs(inst:GetAttributes()) do
		out[k] = A.enc(v)
	end
	for _, c in ipairs(inst:GetChildren()) do
		if c:IsA("ValueBase") and out[c.Name] == nil then
			out[c.Name] = A.enc(c.Value)
		end
	end
	return out
end

function O.get(a)
	local inst = A.need(a.path)
	if not a.prop or a.prop == "" then
		return { value = O.props(a) }
	end
	local v, found = read(inst, a.prop)
	if not found then
		error("Property " .. tostring(a.prop) .. " not found on " .. inst:GetFullName(), 0)
	end
	return { value = A.enc(v) }
end

function O.set(a)
	local inst = A.need(a.path)
	local value = A.dec(a.value)
	if inst:GetAttribute(a.prop) ~= nil then
		inst:SetAttribute(a.prop, value)
		return { set = "attribute" }
	end
	local ok, err = pcall(function()
		inst[a.prop] = value
	end)
	if ok then
		return { set = "property" }
	end
	local child = inst:FindFirstChild(a.prop)
	if child and child:IsA("ValueBase") then
		child.Value = value
		return { set = "value" }
	end
	if a.attribute then
		inst:SetAttribute(a.prop, value)
		return { set = "attribute" }
	end
	error(tostring(err), 0)
end

-- A ModuleScript's function (require: the module's table), or an instance's method (inst:Method(...))
function O.call(a)
	local inst = A.need(a.path)
	local args = {}
	for i, v in ipairs(a.args or {}) do
		args[i] = A.dec(v)
	end
	local results
	if inst:IsA("ModuleScript") then
		local mod = require(inst)
		local f = type(mod) == "table" and mod[a.fn] or nil
		if type(f) ~= "function" then
			error("Function " .. tostring(a.fn) .. " not found in module " .. inst:GetFullName(), 0)
		end
		results = table.pack(f(table.unpack(args, 1, #(a.args or {}))))
	else
		local ok, f = pcall(function()
			return inst[a.fn]
		end)
		if not ok or type(f) ~= "function" then
			error("Function " .. tostring(a.fn) .. " not found on " .. inst:GetFullName(), 0)
		end
		results = table.pack(f(inst, table.unpack(args, 1, #(a.args or {}))))
	end
	local out = {}
	for i = 1, results.n do
		out[i] = A.enc(results[i])
		if out[i] == nil then
			out[i] = false
		end
	end
	return { ReturnValue = out[1], returns = out }
end

function O.describe(a)
	local inst = A.need(a.path)
	local children = {}
	for i, c in ipairs(inst:GetChildren()) do
		if i > 100 then
			break
		end
		table.insert(children, seg(c) .. " (" .. c.ClassName .. ")")
	end
	local functions = {}
	if inst:IsA("ModuleScript") then
		local ok, mod = pcall(require, inst)
		if ok and type(mod) == "table" then
			for k, v in pairs(mod) do
				if type(v) == "function" then
					table.insert(functions, tostring(k))
				end
			end
			table.sort(functions)
		end
	end
	local props = {}
	for k in pairs(O.props(a)) do
		table.insert(props, k)
	end
	table.sort(props)
	return { Name = inst.Name, Class = inst.ClassName, Path = A.pathOf(inst), Properties = props,
		Attributes = A.enc(inst:GetAttributes()), Tags = CollectionService:GetTags(inst), Children = children,
		Functions = functions }
end

-- -- the screen -----------------------------------------------------------------------------------------------------

local function guiShown(g)
	local cur = g
	while cur do
		if cur:IsA("GuiObject") and not cur.Visible then
			return false
		end
		if cur:IsA("LayerCollector") then
			return cur.Enabled
		end
		cur = cur.Parent
	end
	return false
end

local function playerGui()
	local pl = A.player()
	return pl and pl:FindFirstChildOfClass("PlayerGui") or nil
end

function O.screen(a)
	local texts, pg = {}, playerGui()
	if pg then
		for _, g in ipairs(pg:GetDescendants()) do
			if (g:IsA("TextLabel") or g:IsA("TextButton") or g:IsA("TextBox")) and g.Text ~= "" and guiShown(g) then
				local layer = g:FindFirstAncestorWhichIsA("LayerCollector")
				local key = (layer and layer.Name or "?") .. "." .. g.Name
				if texts[key] ~= nil then
					local n = 2
					while texts[key .. "#" .. n] ~= nil do
						n += 1
					end
					key = key .. "#" .. n
				end
				texts[key] = g.Text
			end
		end
	end
	return { texts = texts }
end

function O.ui(a)
	local out, pg = {}, playerGui()
	if pg then
		for _, g in ipairs(pg:GetDescendants()) do
			local kind = (g:IsA("GuiButton") and "button") or (g:IsA("TextBox") and "text_input") or nil
			if kind and guiShown(g) then
				local layer = g:FindFirstAncestorWhichIsA("LayerCollector")
				local label = (g:IsA("TextButton") or g:IsA("TextBox")) and g.Text or ""
				if label == "" then
					for _, d in ipairs(g:GetDescendants()) do
						if d:IsA("TextLabel") and d.Text ~= "" then
							label = d.Text
							break
						end
					end
				end
				local center = g.AbsolutePosition + g.AbsoluteSize / 2
				table.insert(out, { id = A.pathOf(g), kind = kind, name = g.Name, owner = layer and layer.Name or "",
					label = label, enabled = g.Active, value = g:IsA("TextBox") and g.Text or nil,
					placeholder = g:IsA("TextBox") and g.PlaceholderText or nil, x = center.X, y = center.Y })
			end
		end
	end
	return { elements = out }
end

-- Turn the camera by degrees (yaw: right, pitch: up), in the player's client: its look is set, and Roblox's camera
-- scripts carry on from it (they read the camera's look every frame), as after a right-drag. Pitch stays within 80
-- degrees of level, as Roblox's default camera keeps it.
function O.turn(a)
	local cam = workspace.CurrentCamera
	if not cam then
		error("turn: no camera", 0)
	end
	local cf = cam.CFrame
	local l = cf.LookVector
	local yaw = math.atan2(-l.X, -l.Z) - (tonumber(a.yaw) or 0) / DEG
	local limit = 80 / DEG
	local pitch = math.clamp(math.asin(math.clamp(l.Y, -1, 1)) + (tonumber(a.pitch) or 0) / DEG, -limit, limit)
	local look = Vector3.new(-math.sin(yaw) * math.cos(pitch), math.sin(pitch), -math.cos(yaw) * math.cos(pitch))
	cam.CFrame = CFrame.lookAt(cf.Position, cf.Position + look)
	return { look = v3(look), yaw = num(-yaw * DEG), pitch = num(pitch * DEG) }
end

-- Where a world point or an instance is on the viewport: pixels from its top left
function O.point(a)
	local cam = workspace.CurrentCamera
	local target = a.world and Vector3.new(a.world.x, a.world.y, a.world.z) or nil
	if a.object then
		local inst = A.need(a.object)
		if inst:IsA("GuiObject") then
			local c = inst.AbsolutePosition + inst.AbsoluteSize / 2
			return { x = c.X, y = c.Y, on_screen = guiShown(inst), viewport = A.enc(cam.ViewportSize), gui = true }
		end
		local _, _, _, center = A.geom(inst)
		target = center
	end
	if not target then
		error("point: a world point or an object", 0)
	end
	local p, on = cam:WorldToViewportPoint(target)
	return { x = p.X, y = p.Y, on_screen = on and p.Z > 0, viewport = A.enc(cam.ViewportSize) }
end

-- -- what the player sees -------------------------------------------------------------------------------------------

local function promptOf(inst)
	local pp = inst:IsA("ProximityPrompt") and inst or inst:FindFirstChildWhichIsA("ProximityPrompt", true)
	if not pp then
		return nil
	end
	return { action = pp.ActionText, object = pp.ObjectText, key = pp.KeyboardKeyCode.Name, enabled = pp.Enabled,
		max_distance = pp.MaxActivationDistance, hold = pp.HoldDuration, path = A.pathOf(pp) }
end

local function labelOf(inst)
	for _, d in ipairs(inst:GetDescendants()) do
		if d:IsA("TextLabel") and d.Text ~= "" then
			return d.Text
		end
	end
	return nil
end

local function sonar(a, root, hum, look)
	local params = excludeCharacter()
	local flat = Vector3.new(look.X, 0, look.Z)
	if flat.Magnitude < 1e-3 then
		flat = Vector3.new(0, 0, -1)
	end
	flat = flat.Unit
	local radius = math.max(0.5, math.max(root.Size.X, root.Size.Z) / 2 * 0.9)
	local feet = root.Position.Y - (hum and hum.HipHeight or 2) - root.Size.Y / 2
	local reach = a.sonar_reach or 54
	local maxDrop = a.sonar_drop or 40
	local ways = {}
	for _, turn in ipairs(a.sonar_turns or { 0, -30, 30, -60, 60, -90, 90, 180 }) do
		-- (a turn right in the kit is a negative rotation about Roblox's up axis)
		local dir = CFrame.Angles(0, -math.rad(turn), 0) * flat
		local hit = workspace:Spherecast(root.Position, radius, dir * reach, params)
		local clear = hit and hit.Distance or reach
		local by = hit and "wall" or "reach"
		local drop = nil
		local step = 2
		local d = step
		while d <= clear do
			local probe = root.Position + dir * d
			local down = workspace:Raycast(probe, Vector3.new(0, -((root.Position.Y - feet) + maxDrop), 0), params)
			local floor = down and down.Position.Y or nil
			if not floor or feet - floor > 3 then
				clear, by, drop = math.max(0, d - step), "drop", floor and (feet - floor) or nil
				break
			end
			d += step
		end
		table.insert(ways, { turn = turn, clear = clear, by = by, drop = drop,
			normal = hit and v3(hit.Normal) or nil, hit = hit and A.pathOf(hit.Instance) or nil })
	end
	return { ways = ways }
end

function O.view(a)
	local cam = workspace.CurrentCamera
	if not cam then
		error("view: no camera", 0)
	end
	local ch = A.character()
	local root, hum = A.root(ch), A.humanoid(ch)
	local eye, vp = cam.CFrame.Position, cam.ViewportSize
	local params = excludeCharacter()
	local maxDist = a.max_distance or 1000
	local maxCount = a.max_count or 32
	local only = nil
	if a.only then
		only = {}
		for _, n in ipairs(a.only) do
			only[n] = true
		end
	end
	local centerRay = cam:ViewportPointToRay(vp.X / 2, vp.Y / 2)
	local crossHit = workspace:Raycast(centerRay.Origin, centerRay.Direction * maxDist, params)
	local visible = {}
	for _, inst in ipairs(A.things()) do
		if not (ch and (inst == ch or inst:IsDescendantOf(ch))) then
			local name = seg(inst)
			if not only or only[name] then
				local _, _, _, center, size = A.geom(inst)
				if center then
					local dist = (center - eye).Magnitude
					local p, on = cam:WorldToViewportPoint(center)
					if dist <= maxDist and p.Z > 0 and on and vp.X > 0 and vp.Y > 0 then
						local hit = workspace:Raycast(eye, center - eye, params)
						if not hit or hit.Instance == inst or hit.Instance:IsDescendantOf(inst) then
							local crossOn = crossHit ~= nil
								and (crossHit.Instance == inst or crossHit.Instance:IsDescendantOf(inst))
							table.insert(visible, { name = name, path = A.pathOf(inst), class = inst.ClassName,
								tags = tagsOf(inst), center = v3(center), size = v3(size), distance = dist,
								screen_x = p.X / vp.X * 2 - 1, screen_y = 1 - p.Y / vp.Y * 2, crosshair_on = crossOn,
								label = labelOf(inst), prompt = promptOf(inst) })
						end
					end
				end
			end
		end
	end
	table.sort(visible, function(x, y)
		return x.distance < y.distance
	end)
	while #visible > maxCount do
		table.remove(visible)
	end
	local body = {}
	if root then
		local down = workspace:Raycast(root.Position, Vector3.new(0, -((hum and hum.HipHeight or 2) + root.Size.Y), 0),
			params)
		local state = hum and hum:GetState() or nil
		body = { on_ground = hum and hum.FloorMaterial ~= Enum.Material.Air or nil,
			falling = state == Enum.HumanoidStateType.Freefall, velocity = v3(root.AssemblyLinearVelocity),
			standing_on = down and A.pathOf(down.Instance) or nil, health = hum and num(hum.Health) or nil }
		if a.sonar then
			body.sonar = sonar(a, root, hum, cam.CFrame.LookVector)
		end
	end
	local aspect = vp.Y > 0 and vp.X / vp.Y or 16 / 9
	local hfov = 2 * math.deg(math.atan(math.tan(math.rad(cam.FieldOfView) / 2) * aspect))
	return { visible = visible, body = body,
		camera = { fov = hfov, location = v3(eye), look = v3(cam.CFrame.LookVector), viewport = A.enc(vp) },
		omitted = { "held", "openings", "ways_up", "look", "effects", "overhead", "under", "blocks_player", "lanes" } }
end

-- -- the codex ------------------------------------------------------------------------------------------------------

local SERVICES = { "Workspace", "ReplicatedFirst", "ReplicatedStorage", "ServerScriptService", "ServerStorage",
	"StarterGui", "StarterPack", "StarterPlayer", "Lighting", "SoundService", "Teams", "TextChatService", "Chat" }

function O.scripts(a)
	local out = {}
	for _, name in ipairs(SERVICES) do
		local svc = game:FindFirstChild(name)
		if svc then
			for _, s in ipairs(svc:GetDescendants()) do
				if s:IsA("LuaSourceContainer") then
					local item = { path = A.pathOf(s), class = s.ClassName }
					if s:IsA("Script") then
						item.run_context = s.RunContext.Name
						item.enabled = s.Enabled
					end
					if a.source or a.hash then
						local ok, src = pcall(function()
							return s.Source
						end)
						if ok and type(src) == "string" then
							if a.source then
								item.source = src
							end
							if a.hash then
								-- (FNV-1a over the bytes: the codex fetches a script again only when it changed)
								local h = 2166136261
								for i = 1, #src do
									h = bit32.bxor(h, string.byte(src, i))
									-- (* 16777619 = 2^24 + 403, exact in doubles: h * 403 < 2^41)
									h = (bit32.lshift(h, 24) + h * 403) % 4294967296
								end
								item.hash = string.format("%08x-%d", h, #src)
								item.size = #src
							end
						end
					end
					table.insert(out, item)
				end
			end
		end
	end
	return { scripts = out }
end

-- Every tagged instance with its attributes and where it is (the place's gameplay objects, by tag)
function O.tags(a)
	local out = {}
	for _, tag in ipairs(CollectionService:GetAllTags()) do
		local items = {}
		for i, inst in ipairs(CollectionService:GetTagged(tag)) do
			if i > (a.limit or 200) then
				break
			end
			local at = A.geom(inst)
			table.insert(items, { path = A.pathOf(inst), class = inst.ClassName, at = at and v3(at) or nil,
				attributes = A.enc(inst:GetAttributes()) })
		end
		out[tag] = items
	end
	return { tags = out }
end

-- -- the request ----------------------------------------------------------------------------------------------------

-- RUN(REQ): every op of the request under pcall, in order, in this one call (one frame of the data model); the answers
-- as JSON: {t = game time, r = {{ok, v} or {ok = false, e = message}...}}
local function RUN(REQ)
	OVERRIDE = (REQ.player ~= nil and REQ.player ~= "") and REQ.player or nil
	local answers = {}
	for i, item in ipairs(REQ.ops or {}) do
		local f = O[item[1]]
		if not f then
			answers[i] = { ok = false, e = "unknown op " .. tostring(item[1]) }
		else
			local ok, v = pcall(f, item[2] or {})
			if ok then
				answers[i] = { ok = true, v = v }
			else
				answers[i] = { ok = false, e = tostring(v) }
			end
		end
	end
	local ok, out = pcall(HttpService.JSONEncode, HttpService, { t = workspace.DistributedGameTime, r = answers,
		dm = A.isClient() and "Client" or (RunService:IsRunning() and "Server" or "Edit") })
	if not ok then
		out = HttpService:JSONEncode({ t = workspace.DistributedGameTime, error = "JSONEncode: " .. tostring(out) })
	end
	if REQ.echo then
		print(REQ.nonce .. out .. REQ.nonce)
	end
	return out
end
