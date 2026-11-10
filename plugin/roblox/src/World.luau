--!strict
-- The played world in the kit frame (Frame.luau): the player, finding things, their transforms and boxes, the
-- player's pose and movement numbers. Ops: find, transforms, pose, movement.
local CollectionService = game:GetService("CollectionService")
local Players = game:GetService("Players")
local RunService = game:GetService("RunService")
local StarterPlayer = game:GetService("StarterPlayer")

local Frame = require(script.Parent.Frame)
local Json = require(script.Parent.Json)
local Paths = require(script.Parent.Paths)

local World = {}

local r2 = Frame.r2
local S = Frame.SCALE

-- The services a search walks (the rest of the DataModel is Studio's and Roblox's own)
World.SERVICES = { "Workspace", "Players", "Lighting", "ReplicatedFirst", "ReplicatedStorage", "ServerScriptService",
	"ServerStorage", "StarterGui", "StarterPack", "StarterPlayer", "SoundService", "Teams", "TextChatService", "Chat" }

-- The local player (the client), else the first player (the server), else nil.
function World.player(): Player?
	local lp = Players.LocalPlayer
	if lp then
		return lp
	end
	return Players:GetPlayers()[1]
end

function World.character(): Model?
	local p = World.player()
	return p and p.Character
end

function World.humanoid(): Humanoid?
	local c = World.character()
	return c and c:FindFirstChildOfClass("Humanoid")
end

function World.root(): BasePart?
	local c = World.character()
	if c == nil then
		return nil
	end
	local h = c:FindFirstChildOfClass("Humanoid")
	local root = (h and h.RootPart) or c:FindFirstChild("HumanoidRootPart") or c.PrimaryPart
	return if root and root:IsA("BasePart") then root else nil
end

-- The player's body: its middle (the root part's centre), its half height (the feet are that far below) and radius,
-- in studs. nil without a character.
function World.body(): (Vector3?, number, number)
	local root = World.root()
	if root == nil then
		return nil, 0, 0
	end
	local h = World.humanoid()
	local half = root.Size.Y / 2
	if h then
		if h.RigType == Enum.HumanoidRigType.R6 and h.HipHeight < 0.1 then
			half += 2 -- (an R6 body stands on its 2-stud legs, HipHeight 0)
		else
			half += h.HipHeight
		end
	end
	return root.Position, half, math.max(root.Size.X, root.Size.Z) / 2
end

function World.isOurs(inst: Instance): boolean
	return inst.Name == "AutopilotPlaytest" or inst:GetAttribute("AutopilotPlaytest") ~= nil
end

-- Whether an instance is a plain group, whose children are things of their own: a Folder, or a Model that only groups
-- other models (it holds a Model, and has no Humanoid and no PrimaryPart: a level's "Map" model, a "Houses" model).
function World.plainGroup(inst: Instance): boolean
	if inst:IsA("Folder") or inst:IsA("WorldModel") or inst:IsA("Configuration") then
		return true
	end
	if inst:IsA("Model") and not inst:IsA("Workspace") then
		local m = inst :: Model
		if m.PrimaryPart or m:FindFirstChildOfClass("Humanoid") then
			return false
		end
		return m:FindFirstChildWhichIsA("Model") ~= nil
	end
	return false
end
local plainGroup = World.plainGroup

-- The thing an instance is part of: going down from the Workspace, the first Model or part on its way that isn't a
-- plain group (a character is one thing; a door model with its parts is one thing). nil outside the Workspace.
function World.thingOf(inst: Instance): Instance?
	local chain = {}
	local x: Instance? = inst
	while x and x ~= workspace do
		table.insert(chain, x)
		x = x.Parent
	end
	if x ~= workspace then
		return nil
	end
	for i = #chain, 1, -1 do
		local c = chain[i]
		if not plainGroup(c) then
			return if c:IsA("Model") or c:IsA("BasePart") then c else nil
		end
	end
	return nil
end

-- The Workspace's things, as Unreal's actors: its Models and parts at the top or under plain groups (Folders, models
-- of models), not the parts inside a model; the terrain and the camera left out.
function World.things(): { Instance }
	local out = {}
	local function walk(parent: Instance)
		for _, c in ipairs(parent:GetChildren()) do
			if c:IsA("Terrain") or c:IsA("Camera") or World.isOurs(c) then
				continue
			end
			if plainGroup(c) then
				walk(c)
			elseif c:IsA("Model") or c:IsA("BasePart") then
				table.insert(out, c)
			end
		end
	end
	walk(workspace)
	return out
end

-- Whether an instance is of a class: a Roblox class (subclasses too), a CollectionService tag ("tag:Enemy", or the
-- plain name when no class has it), or a Model holding a script of that name (Door for a model with a Door script).
function World.isOf(inst: Instance, cls: string): boolean
	if string.sub(cls, 1, 4) == "tag:" then
		return inst:HasTag(string.sub(cls, 5))
	end
	local ok, isa = pcall(function()
		return inst:IsA(cls)
	end)
	if ok and isa then
		return true
	end
	if inst:HasTag(cls) then
		return true
	end
	if inst:IsA("Model") or inst:IsA("BasePart") then
		local s = inst:FindFirstChild(cls)
		if s and s:IsA("LuaSourceContainer") then
			return true
		end
	end
	return false
end

-- Whether a name is a Roblox class's (ReflectionService:GetClass: nil for a name no class has; IsA never says, it
-- answers false). Where ReflectionService can't tell, every name may be one.
local function isClassName(cls: string): boolean
	local ok, c = pcall(function()
		return (game:GetService("ReflectionService") :: any):GetClass(cls)
	end)
	return not ok or c ~= nil
end

-- Kinds that a find has matched this session (a tag, a script's name, a class): one that matches nothing now (every
-- lantern collected, a streamed-out part) is still a kind, and finds nothing rather than a 400
local seenKinds: { [string]: boolean } = {}

-- `find`: {"class"?, "name"?, "limit"? (10000), "things"?} -> {"objects": [paths]}; also why, for a class nothing is
-- (no Roblox class, tag or script of that name, nor one seen before: the op answers 400).
function World.find(args: any): (any, string?)
	local cls = tostring(args.class or "")
	local name = string.lower(tostring(args.name or ""))
	local limit = tonumber(args.limit) or 10000
	local asked = cls
	local known = cls == "" or string.sub(cls, 1, 4) == "tag:" or seenKinds[cls] == true or isClassName(cls)
	if not known then
		local ok, tags = pcall(function()
			return CollectionService:GetAllTags()
		end)
		if ok and table.find(tags, cls) then
			known = true
		end
	end
	local out = {}
	local candidates: { Instance }
	if string.sub(cls, 1, 4) == "tag:" then
		candidates = CollectionService:GetTagged(string.sub(cls, 5))
		cls = ""
	elseif args.things then
		candidates = World.things()
	elseif not known and #CollectionService:GetTagged(cls) > 0 then
		candidates = CollectionService:GetTagged(cls)
		cls = ""
		known = true
	else
		candidates = {}
		for _, s in ipairs(World.SERVICES) do
			local svc = game:FindFirstChild(s)
			if svc then
				table.insert(candidates, svc)
				for _, d in ipairs(svc:GetDescendants()) do
					table.insert(candidates, d)
				end
			end
		end
	end
	for _, inst in ipairs(candidates) do
		if #out >= limit then
			break
		end
		if World.isOurs(inst) then
			continue
		end
		if cls ~= "" and not World.isOf(inst, cls) then
			continue
		end
		if name ~= "" and not string.find(string.lower(inst.Name), name, 1, true) then
			continue
		end
		local p = Paths.of(inst)
		if p ~= "" then
			table.insert(out, p)
		end
	end
	if #out > 0 and asked ~= "" then
		seenKinds[asked] = true
	end
	if #out == 0 and not known then
		-- (no class and no tag of that name: a script of that name, anywhere the search walks, makes it a kind)
		for _, s in ipairs(World.SERVICES) do
			local svc = game:FindFirstChild(s)
			if svc then
				for _, d in ipairs(svc:GetDescendants()) do
					if d.Name == cls and d:IsA("LuaSourceContainer") then
						seenKinds[asked] = true
						return { objects = out }, nil
					end
				end
			end
		end
		return { objects = out }, "find: no class, tag or script named " .. cls
			.. " (a Roblox class such as BasePart or Model, tag:<name>, or a script's name)"
	end
	return { objects = out }, nil
end

-- The axis-aligned box (centre, size, studs) around an oriented box.
function World.aabb(cf: CFrame, size: Vector3): (Vector3, Vector3)
	local r, u, b = cf.RightVector, cf.UpVector, -cf.LookVector
	local hx, hy, hz = size.X / 2, size.Y / 2, size.Z / 2
	local ex = math.abs(r.X) * hx + math.abs(u.X) * hy + math.abs(b.X) * hz
	local ey = math.abs(r.Y) * hx + math.abs(u.Y) * hy + math.abs(b.Y) * hz
	local ez = math.abs(r.Z) * hx + math.abs(u.Z) * hy + math.abs(b.Z) * hz
	return cf.Position, Vector3.new(ex * 2, ey * 2, ez * 2)
end

-- The world box of a thing (centre and size, studs, world-aligned), or nil: a model's bounding box, a part's box, an
-- attachment's point; a GUI object has none. Then its own box, as it is turned: its CFrame and its size along its own
-- axes (a part's CFrame and Size, a model's GetBoundingBox; nil for an attachment).
function World.box(inst: Instance): (Vector3?, Vector3?, CFrame?, Vector3?)
	if inst:IsA("BasePart") then
		local c, s = World.aabb(inst.CFrame, inst.Size)
		return c, s, inst.CFrame, inst.Size
	elseif inst:IsA("Model") then
		local ok, cf, size = pcall(function()
			return (inst :: Model):GetBoundingBox()
		end)
		if ok then
			local c, s = World.aabb(cf, size)
			return c, s, cf, size
		end
	elseif inst:IsA("Attachment") then
		return inst.WorldPosition, Vector3.zero, nil, nil
	end
	return nil, nil, nil, nil
end

-- The pivot (where it is and which way it faces) of a thing, or nil.
function World.pivot(inst: Instance): CFrame?
	if inst:IsA("PVInstance") then
		local ok, cf = pcall(function()
			return (inst :: PVInstance):GetPivot()
		end)
		if ok then
			return cf
		end
	elseif inst:IsA("Attachment") then
		return inst.WorldCFrame
	elseif inst:IsA("Humanoid") or inst:IsA("Player") then
		local c = if inst:IsA("Player") then inst.Character else inst.Parent
		if c and c:IsA("Model") then
			return c:GetPivot()
		end
	end
	return nil
end

function World.velocity(inst: Instance): Vector3
	local part: Instance? = inst
	if inst:IsA("Model") then
		local h = inst:FindFirstChildOfClass("Humanoid")
		part = (h and h.RootPart) or inst.PrimaryPart or inst:FindFirstChildWhichIsA("BasePart")
	end
	if part and part:IsA("BasePart") then
		return part.AssemblyLinearVelocity
	end
	return Vector3.zero
end

-- Whether an instance takes part in the game now (transforms' `active`, as the other plugins'): in the DataModel; a
-- part not fully transparent; a script, light, effect, prompt or anything else with an Enabled property enabled; a GUI
-- object visible, with every GuiObject above it, in an enabled ScreenGui (or SurfaceGui, BillboardGui) that is shown
-- (a ScreenGui only in a player's PlayerGui).
function World.activeOf(inst: Instance): boolean
	if inst.Parent == nil or not inst:IsDescendantOf(game) then
		return false
	end
	if inst:IsA("BasePart") and (inst :: BasePart).Transparency >= 1 then
		return false
	end
	local okE, enabled = pcall(function()
		return (inst :: any).Enabled
	end)
	if okE and enabled == false then
		return false
	end
	if inst:IsA("GuiObject") then
		local x: Instance? = inst
		while x and x:IsA("GuiObject") do
			if not (x :: GuiObject).Visible then
				return false
			end
			x = x.Parent
		end
		local gui = inst:FindFirstAncestorWhichIsA("LayerCollector")
		if gui == nil or not gui.Enabled then
			return false
		end
		if gui:IsA("ScreenGui") and gui:FindFirstAncestorWhichIsA("PlayerGui") == nil then
			return false -- (a StarterGui template: copied to each player, never shown itself)
		end
	end
	return true
end

function World.transformOf(inst: Instance): any
	local active = World.activeOf(inst)
	local cf = World.pivot(inst)
	if cf == nil then
		-- (no place in the world: a script, a value, a GUI object, a service; still there)
		return { location = Json.null, rotation = Json.null, velocity = Json.null, center = Json.null,
			extent = Json.null, active = active }
	end
	local center, size = World.box(inst)
	center = center or cf.Position
	size = size or Vector3.zero
	return { location = Frame.kit(cf.Position), rotation = Frame.kitRot(cf), velocity = Frame.kit(World.velocity(inst)),
		center = Frame.kit(center), extent = Frame.kitExtent(size), active = active }
end

-- `transforms`: {"objects": [paths]} -> {"objects": {path: {..., "active"} | null (no such instance)}}
function World.transforms(args: any, resolve: (string) -> Instance?): any
	local out = Json.object()
	for _, p in ipairs(args.objects or {}) do
		local inst = resolve(tostring(p))
		out[tostring(p)] = if inst then World.transformOf(inst) else Json.null
	end
	return { objects = out }
end

function World.onGround(): any
	local h = World.humanoid()
	if h == nil then
		return Json.null
	end
	return h.FloorMaterial ~= Enum.Material.Air
end

-- `pose`: the player's {"at", "feet", "eye", "yaw", "pitch", "velocity", "on_ground", "path"}.
function World.pose(): (boolean, any)
	local center, half = World.body()
	local char = World.character()
	if center == nil or char == nil then
		return false, "No player character (dead, or not spawned yet)"
	end
	local cam = workspace.CurrentCamera
	local cf = if cam then cam.CFrame else char:GetPivot()
	local root = World.root()
	return true, { path = Paths.of(char), at = Frame.kit(center), feet = Frame.kit(center - Vector3.new(0, half, 0)),
		eye = Frame.kit(cf.Position), yaw = Frame.yawOf(cf), pitch = Frame.pitchOf(cf),
		velocity = Frame.kit(if root then root.AssemblyLinearVelocity else Vector3.zero), on_ground = World.onGround() }
end

-- `movement`: the player's numbers in kit units: walk speed and jump from its Humanoid, gravity from the Workspace,
-- its half height and radius from its body, step and slope as Roblox's Humanoid takes them. `sources` says where each
-- came from.
function World.movement(): (boolean, any)
	local h = World.humanoid()
	local center, half, radius = World.body()
	local out: any = { sources = Json.object() }
	local g = workspace.Gravity
	out.gravity = r2(g * S)
	out.sources.gravity = "Workspace.Gravity"
	if h then
		out.walk_speed = r2(h.WalkSpeed * S)
		out.sources.walk_speed = "Humanoid.WalkSpeed"
		local v
		if h.UseJumpPower then
			v = h.JumpPower
			out.sources.jump_z = "Humanoid.JumpPower"
		else
			v = math.sqrt(2 * g * math.max(h.JumpHeight, 0))
			out.sources.jump_z = "Humanoid.JumpHeight (as sqrt(2 g h))"
		end
		out.jump_z = r2(v * S)
		out.max_slope = r2(h.MaxSlopeAngle)
		out.sources.max_slope = "Humanoid.MaxSlopeAngle"
		-- A Humanoid walks up a ledge lower than about its hip height without jumping (Roblox's step-up)
		out.step = r2(math.max(h.HipHeight, 0.5) * 0.9 * S)
		out.sources.step = "about 0.9 x Humanoid.HipHeight (Roblox's step-up)"
	else
		-- (no character yet: the game's StarterPlayer settings)
		out.walk_speed = r2(StarterPlayer.CharacterWalkSpeed * S)
		out.sources.walk_speed = "StarterPlayer.CharacterWalkSpeed"
		out.jump_z = r2((if StarterPlayer.CharacterUseJumpPower then StarterPlayer.CharacterJumpPower
			else math.sqrt(2 * g * StarterPlayer.CharacterJumpHeight)) * S)
		out.sources.jump_z = "StarterPlayer"
	end
	if center then
		out.half_height = r2(half * S)
		out.radius = r2(radius * S)
		out.sources.half_height = "HumanoidRootPart.Size.Y / 2 + Humanoid.HipHeight"
	else
		out.half_height = Json.null
		out.radius = Json.null
	end
	out.air_control = 1.0
	out.sources.air_control = "a Humanoid steers fully in the air"
	return true, out
end

-- Seconds of game time: the Workspace's simulation time (DistributedGameTime: it stops while the game is paused).
function World.gameTime(): number
	return workspace.DistributedGameTime
end

function World.running(): boolean
	return RunService:IsRunning()
end

return World
