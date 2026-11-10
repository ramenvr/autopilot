--!strict
-- In-game detectors (docs/protocol.md "Findings"), run on both sides of a play session (each sees its own log and its
-- own physics): log_error (errors the game's scripts and the engine print: LogService), nan (a part's position not a
-- number), out_of_world (an unanchored part falling past the lowest drawn point of the level at its start, or past the
-- Workspace's FallenPartsDestroyHeight), long_fall (the player falling for 8 s), hitch (a frame over 0.75 s, not in the
-- session's first 3 s), on the client hud_fraction (a "n / m" on screen with n over m), and, as Unreal's and Godot's,
-- on the server (it sees every character and the game's own numbers, once): teleport (a character moved farther in
-- one frame than its speed allows), actor_growth (the Workspace's things of one kind kept growing for growth_seconds)
-- and over_max (a number attribute or value object above its sibling maximum). Roblox has no declared ranges, so no
-- clamp. Warnings the game prints go to the brain as `log` events too.
local CollectionService = game:GetService("CollectionService")
local LogService = game:GetService("LogService")
local Players = game:GetService("Players")
local RunService = game:GetService("RunService")

local Frame = require(script.Parent.Frame)
local World = require(script.Parent.World)

local Detectors = {}
Detectors.__index = Detectors

-- Lines that are Studio's or Autopilot's own, never the game's
local NOISE = { "Autopilot", "AutopilotDev", "Plugin_", "CorePackages", "CoreGui", "RobloxGui", "MCP-Freeway",
	"Freeway", "Studio", "ExperienceChat" }

local S = Frame.SCALE
local GROWTH_EVERY = 10 -- s of game time between actor_growth's counts
local SWEEP_EVERY = 10 -- s between over_max's walks for number pairs (new instances are looked at as they come)
local SWEEP_BUDGET = 500 -- instances over_max looks at a frame
local QUEUE_MAX = 200000

-- Where over_max looks: the game's instances that hold its numbers (players' leaderstats and attributes too)
local function propertyRoots(): { Instance }
	local out: { Instance } = { workspace, Players }
	for _, name in ipairs({ "ReplicatedStorage", "ServerStorage" }) do
		local ok, svc = pcall(game.GetService, game, name)
		if ok and svc then
			table.insert(out, svc)
		end
	end
	return out
end

function Detectors.new(emit: (any) -> (), now: () -> number, screen: (() -> { [string]: string })?)
	local self = setmetatable({}, Detectors) :: any
	self.emit = emit
	self.now = now -- the game time
	self.screen = screen
	self.enabled = true
	self.active = false -- a run uses the game (the host's runUsingGame): only then do the detectors work
	self.counts = {}
	self.config = { long_fall_seconds = 8.0, hitch_seconds = 0.75, hud_every = 0.25, scan_every = 0.5,
		out_of_world_margin = 50.0, teleport_factor = 3.0, teleport_min = 1000.0, growth_seconds = 120.0,
		property_every = 0.5 }
	self.server = RunService:IsServer() -- (teleport, actor_growth and over_max: the server's)
	self.started = os.clock()
	self.falling = 0
	self.hudAt = 0
	self.scanAt = 0
	self.killY = nil
	self.flagged = setmetatable({}, { __mode = "k" })
	self.humanoids = setmetatable({}, { __mode = "k" }) -- the Workspace's Humanoids (teleport)
	self.bodies = setmetatable({}, { __mode = "k" }) -- character model -> {root, at, frames, speed}
	self.growth = {} -- things counted by kind, one sample every 10 s of game time
	self.growthAt = 0
	self.propertyAt = 0
	self.attrPairs = setmetatable({}, { __mode = "k" }) -- instance -> {{attribute, its max attribute}...}
	self.valuePairs = setmetatable({}, { __mode = "k" }) -- value object -> its sibling max value object
	self.queue = {} -- instances over_max is yet to look at
	self.queueHead = 1
	self.sweepAt = 0
	self.conns = {}
	table.insert(self.conns, LogService.MessageOut:Connect(function(message: string, kind: Enum.MessageType)
		self:_log(message, kind)
	end))
	if self.server then
		for _, d in ipairs(workspace:GetDescendants()) do
			if d:IsA("Humanoid") then
				self.humanoids[d] = true
			end
		end
		for _, root in ipairs(propertyRoots()) do
			table.insert(self.conns, root.DescendantAdded:Connect(function(d: Instance)
				self:_added(d)
			end))
		end
	end
	return self
end

-- On while a run uses the game, off otherwise. Back on, they start afresh: what was seen before isn't this run's, and
-- the session's first seconds count again as its time to settle.
function Detectors:setActive(on: boolean)
	if on and not self.active then
		self.counts = {}
		self.started = os.clock()
		self.falling = 0
		self.hudAt = 0
		self.scanAt = 0
		self.flagged = setmetatable({}, { __mode = "k" })
		self.bodies = setmetatable({}, { __mode = "k" })
		self.growth = {}
		self.growthAt = self.now()
		self.propertyAt = 0
		self.sweepAt = 0
	end
	self.active = on
end

function Detectors:stop()
	for _, c in ipairs(self.conns) do
		c:Disconnect()
	end
	self.conns = {}
end

function Detectors:configure(args: any)
	if type(args) ~= "table" then
		return
	end
	if args.enabled ~= nil then
		self.enabled = args.enabled == true
	end
	for k in pairs(self.config) do
		if tonumber(args[k]) then
			self.config[k] = tonumber(args[k])
		end
	end
end

local function noise(message: string): boolean
	for _, n in ipairs(NOISE) do
		if string.find(message, n, 1, true) then
			return true
		end
	end
	return false
end

local function normalize(s: string): string
	return string.sub((string.gsub(s, "%d+", "#")), 1, 160)
end

function Detectors:_finding(f: any)
	local sig = f.signature
	local n = (self.counts[sig] or 0) + 1
	self.counts[sig] = n
	if not (n == 1 or n == 10 or n == 100 or n == 1000) then
		return
	end
	local t = Frame.r2(self.now())
	f.t = t
	f.count = n
	f.source = "engine"
	self.emit({ kind = "finding", t = t, finding = f })
end

function Detectors:_log(message: string, kind: Enum.MessageType)
	if not self.active then
		return
	end
	if kind ~= Enum.MessageType.MessageError and kind ~= Enum.MessageType.MessageWarning then
		return
	end
	if noise(message) then
		return
	end
	local side = if RunService:IsServer() then "Server" else "Client"
	local error = kind == Enum.MessageType.MessageError
	local t = Frame.r2(self.now())
	self.emit({ kind = "log", t = t, verbosity = if error then "Error" else "Warning", category = side,
		message = string.sub(message, 1, 2000) })
	if error and self.enabled then
		-- "Workspace.Door.Script:12: attempt to index nil with 'Open'": the place is the script and line in front
		local where = string.match(message, "^([%w_%.%s]+:%d+):") or ""
		self:_finding({ detector = "log_error", severity = "error",
			summary = side .. " script error: " .. string.sub(message, 1, 300),
			signature = "log_error:" .. normalize(message) .. "@" .. where,
			details = { message = message, where = where, side = string.lower(side) } })
	end
end

-- Once a frame: real_dt, the frame's wall time.
function Detectors:tick(realDt: number)
	if not self.active or not self.enabled or self.paused then
		return
	end
	local loading = os.clock() - self.started < 3
	if not loading and realDt > self.config.hitch_seconds then
		self:_finding({ detector = "hitch", severity = "warning", summary = string.format("A frame took %.2f s", realDt),
			signature = "hitch", details = { seconds = Frame.r2(realDt) } })
	end
	local h = World.humanoid()
	if h and RunService:IsClient() then
		if h:GetState() == Enum.HumanoidStateType.Freefall then
			self.falling += realDt
		else
			self.falling = 0
		end
		if self.falling > self.config.long_fall_seconds then
			self.falling = -1e9 -- (once per fall)
			local char = World.character()
			local root = World.root()
			self:_finding({ detector = "long_fall", severity = "warning",
				summary = string.format("%s kept falling for %d s", if char then char.Name else "The player",
					self.config.long_fall_seconds),
				signature = "long_fall:" .. (if char then char.Name else ""), actor = if char then char.Name else "",
				where = if root then Frame.kit(root.Position) else nil })
		end
	end
	local now = os.clock()
	if self.screen and now - self.hudAt >= self.config.hud_every then
		self.hudAt = now
		self:_hud()
	end
	if now - self.scanAt >= self.config.scan_every and not loading then
		self.scanAt = now
		self:_scan()
	end
	if self.server then
		self:_teleports(realDt)
		local t = self.now()
		if t >= self.growthAt then
			self.growthAt = t + GROWTH_EVERY
			self:_countKinds()
		end
		self:_sweep(now)
		if now - self.propertyAt >= self.config.property_every and not loading then
			self.propertyAt = now
			self:_properties()
		end
	end
end

function Detectors:_hud()
	local texts = (self.screen :: any)()
	for key, text in pairs(texts) do
		for a, b in string.gmatch(text, "(%d+)%s*/%s*(%d+)") do
			local x, y = tonumber(a) :: number, tonumber(b) :: number
			if y > 0 and x > y then
				self:_finding({ detector = "hud_fraction", severity = "warning",
					summary = string.format("HUD text %s shows %d/%d: more than its stated maximum", key, x, y),
					signature = "hud_fraction:" .. key, details = { key = key, text = text, value = x, max = y } })
			end
		end
	end
end

-- NaN positions, and unanchored parts below the level (its lowest drawn point at the start, less a margin; or the
-- Workspace's FallenPartsDestroyHeight, where Roblox removes them).
function Detectors:_scan()
	local parts = {}
	for _, d in ipairs(workspace:GetDescendants()) do
		if d:IsA("BasePart") and not d:IsA("Terrain") then
			table.insert(parts, d)
			if #parts >= 20000 then
				break
			end
		end
	end
	if self.killY == nil then
		local low = math.huge
		for _, p in ipairs(parts) do
			if p.Anchored and p.Transparency < 1 then
				low = math.min(low, p.Position.Y - p.Size.Y / 2)
			end
		end
		local margin = self.config.out_of_world_margin * 100 / Frame.SCALE -- (metres, as the other engines take it)
		self.killY = math.max((if low ~= math.huge then low else 0) - margin, workspace.FallenPartsDestroyHeight + 5)
	end
	for _, p in ipairs(parts) do
		if self.flagged[p] then
			continue
		end
		local pos = p.Position
		if not Frame.finite(pos) then
			self.flagged[p] = true
			self:_finding({ detector = "nan", severity = "error", summary = p.Name .. " has a NaN or infinite position",
				signature = "nan:" .. p.Name, actor = p.Name })
		elseif not p.Anchored and pos.Y < self.killY then
			local thing = World.thingOf(p) or p
			if self.flagged[thing] then
				continue
			end
			self.flagged[p] = true
			self.flagged[thing] = true
			self:_finding({ detector = "out_of_world", severity = "warning",
				summary = string.format("%s fell out of the world (below %.0f studs)", thing.Name, self.killY),
				signature = "out_of_world:" .. thing.Name, actor = thing.Name, where = Frame.kit(pos) })
		end
	end
end

-- --- as Unreal's and Godot's: teleport, actor_growth, over_max (the server's) ------------------------------------

-- What an instance is, as a finding names its kind: a player (its Player, or its character), else its first
-- CollectionService tag, else its name with its digits as # (Coin12 and Coin13 are Coin#), else its class.
function Detectors.kindOf(inst: Instance): string
	if inst:IsA("Player") or (inst:IsA("Model") and Players:GetPlayerFromCharacter(inst :: Model)) then
		return "Player"
	end
	local tags = CollectionService:GetTags(inst)
	if #tags > 0 then
		table.sort(tags)
		return tags[1]
	end
	local name = string.gsub(inst.Name, "%d+", "#")
	return if name ~= "" then name else inst.ClassName
end

-- The name of n's maximum among its siblings (has(name): whether a number of that name is there): max_<n>, <n>_max,
-- Max<N>, <N>Max (Godot's), Max_<n>, <n>_Max. nil for a name that is itself a maximum, or with none.
function Detectors.maxNameOf(n: string, has: (string) -> boolean): string?
	local lower = string.lower(n)
	if string.sub(lower, 1, 3) == "max" or string.sub(lower, -3) == "max" then
		return nil
	end
	local pascal = string.gsub(n, "_*([^_]+)", function(w: string): string
		return string.upper(string.sub(w, 1, 1)) .. string.sub(w, 2)
	end)
	for _, other in ipairs({ "max_" .. n, n .. "_max", "Max" .. pascal, n .. "Max", "Max_" .. n, n .. "_Max" }) do
		if other ~= n and has(other) then
			return other
		end
	end
	return nil
end

local function fmt(v: number): string
	if v == math.floor(v) and math.abs(v) < 1e15 then
		return string.format("%d", v)
	end
	return tostring(math.round(v * 1000) / 1000)
end

-- A point in the kit frame, to 0.1
local function kit1(v: Vector3): { [string]: number }
	local function r1(x: number): number
		return math.round(x * 10) / 10
	end
	return { X = r1(-v.Z * S), Y = r1(v.X * S), Z = r1(v.Y * S) }
end

-- Where an instance is, for a finding: its thing's (or its player's character's) pivot. nil when it has no place.
local function whereOf(inst: Instance): any
	local at: Instance? = World.thingOf(inst)
	if at == nil then
		local p = if inst:IsA("Player") then inst else inst.Parent
		at = if p and p:IsA("Player") then p else nil
	end
	local cf = if at then World.pivot(at) else nil
	return if cf and Frame.finite(cf.Position) then Frame.kit(cf.Position) else nil
end

local function isNumberValue(inst: Instance?): boolean
	return inst ~= nil and (inst:IsA("IntValue") or inst:IsA("NumberValue"))
end

function Detectors:_added(d: Instance)
	if d:IsA("Humanoid") and d:IsDescendantOf(workspace) then
		self.humanoids[d] = true
	end
	if self.active and #self.queue < QUEUE_MAX then
		table.insert(self.queue, d) -- (looked at in a later frame: its attributes set by then)
	end
end

-- teleport: a character (a Model with a Humanoid and a HumanoidRootPart: the players' and NPCs') moved farther in one
-- frame than teleport_factor times its speed allows (at least teleport_min kit units), after 5 frames of following it:
-- a respawn, a bad snap, a body pushed out of a wall, or a move by the brain (as Godot's, told too). A suspicion.
function Detectors:_teleports(dt: number)
	local factor, least = self.config.teleport_factor, self.config.teleport_min
	for h in pairs(self.humanoids) do
		local model = h.Parent
		if model == nil or not h:IsDescendantOf(workspace) then
			self.humanoids[h] = nil -- (back in the Workspace, DescendantAdded tells it again)
			continue
		end
		local root = model:FindFirstChild("HumanoidRootPart")
		if not (model:IsA("Model") and root and root:IsA("BasePart")) then
			self.bodies[model] = nil
			continue
		end
		local part = root :: BasePart
		local pos = part.Position
		if not Frame.finite(pos) then
			continue
		end
		local speed = part.AssemblyLinearVelocity.Magnitude * S
		local track = self.bodies[model]
		local clock = os.clock()
		-- (new, respawned (a new root part), or not looked at for a while: off, paused, a hitch: tracked afresh)
		if track == nil or track.root ~= part or clock - (track.seen or clock) > 0.5 then
			self.bodies[model] = { root = part, at = pos, frames = 1, speed = speed, seen = clock }
			continue
		end
		track.seen = clock
		if track.frames > 5 then
			local allowed = math.max(least, factor * math.max(speed, track.speed) * dt)
			local moved = (pos - track.at).Magnitude * S
			if moved > allowed then
				local kind = Detectors.kindOf(model)
				self:_finding({ detector = "teleport", severity = "suspicion",
					summary = string.format("%s moved %.0f units in one frame (at most %.0f expected)", kind, moved,
						allowed),
					signature = "teleport:" .. kind, actor = model.Name, where = kit1(pos),
					details = { from = kit1(track.at), to = kit1(pos), moved = math.round(moved),
						allowed = math.round(allowed) } })
			end
		end
		track.at = pos
		track.frames += 1
		track.speed = speed
	end
	for model in pairs(self.bodies) do
		if model.Parent == nil then
			self.bodies[model] = nil
		end
	end
end

-- actor_growth: every 10 s of game time, how many of the Workspace's things (World.things) of each kind (kindOf) there
-- are; a kind that kept growing over growth_seconds, to at least twice as many and 50 more, is a possible leak. (The
-- server's: a client's counts rise and fall with streaming.)
function Detectors:_countKinds()
	local counts = {}
	for _, th in ipairs(World.things()) do
		local k = Detectors.kindOf(th)
		counts[k] = (counts[k] or 0) + 1
	end
	local growth = self.growth
	table.insert(growth, counts)
	local needed = math.max(2, math.round(self.config.growth_seconds / GROWTH_EVERY) + 1)
	while #growth > needed do
		table.remove(growth, 1)
	end
	if #growth < needed then
		return
	end
	for k, last in pairs(growth[#growth]) do
		local first = growth[1][k] or 0
		if last < 2 * first or last - first < 50 then
			continue
		end
		local rising = true
		for i = 2, #growth do
			if (growth[i][k] or 0) < (growth[i - 1][k] or 0) then
				rising = false
				break
			end
		end
		if rising then
			self:_finding({ detector = "actor_growth", severity = "warning",
				summary = string.format("%s actors kept growing from %d to %d in %.0f s (a possible leak)", k, first,
					last, self.config.growth_seconds),
				signature = "actor_growth:" .. k,
				details = { from = first, to = last, seconds = self.config.growth_seconds } })
		end
	end
end

-- The number pairs of one instance: its number attributes with a sibling maximum attribute, and, for an IntValue or
-- NumberValue, its sibling maximum value object (Coins and MaxCoins in a player's leaderstats).
function Detectors:_examine(d: Instance)
	if d.Parent == nil or World.isOurs(d) then
		return
	end
	if isNumberValue(d) then
		local holder = d.Parent :: Instance
		local m = Detectors.maxNameOf(d.Name, function(name: string): boolean
			return isNumberValue(holder:FindFirstChild(name))
		end)
		self.valuePairs[d] = if m then holder:FindFirstChild(m) else nil
	end
	local attrs = d:GetAttributes()
	if next(attrs) == nil then
		self.attrPairs[d] = nil
		return
	end
	local found = {}
	for name, v in pairs(attrs) do
		if type(v) == "number" then
			local m = Detectors.maxNameOf(name, function(other: string): boolean
				return type(attrs[other]) == "number"
			end)
			if m then
				table.insert(found, { name, m })
			end
		end
	end
	self.attrPairs[d] = if #found > 0 then found else nil
end

-- over_max's pairs kept current at a bounded cost: what comes in is looked at as it comes (a few hundred a frame),
-- and every 10 s everything again (an attribute set on an instance already there tells nothing).
function Detectors:_sweep(now: number)
	local q = self.queue
	if self.queueHead > #q and now >= self.sweepAt then
		self.sweepAt = now + SWEEP_EVERY
		q = {}
		for _, root in ipairs(propertyRoots()) do
			local all = root:GetDescendants()
			table.move(all, 1, #all, #q + 1, q)
		end
		self.queue = q
		self.queueHead = 1
	end
	local last = math.min(#q, self.queueHead + SWEEP_BUDGET - 1)
	for i = self.queueHead, last do
		self:_examine(q[i])
	end
	self.queueHead = last + 1
	if self.queueHead > #q then
		self.queue = {}
		self.queueHead = 1
	end
end

function Detectors:_overMax(holder: Instance, property: string, v: number, maxProperty: string, mx: number)
	if not (mx > 0 and v > mx + 1e-4) then
		return
	end
	local label = Detectors.kindOf(holder)
	local actor = holder.Name
	local owner = holder.Parent
	if holder.Name == "leaderstats" and owner and owner:IsA("Player") then
		actor = owner.Name
	end
	self:_finding({ detector = "over_max", severity = "warning",
		summary = string.format("%s %s is %s, above %s %s", label, property, fmt(v), maxProperty, fmt(mx)),
		signature = "over_max:" .. label .. "." .. property, actor = actor, where = whereOf(holder),
		details = { property = property, value = v, max_property = maxProperty, max = mx } })
end

-- over_max: a number above its sibling maximum when that is above 0, every property_every s. (Not a Humanoid's Health
-- and MaxHealth: properties, which the engine clamps.)
function Detectors:_properties()
	for inst, list in pairs(self.attrPairs) do
		if not inst:IsDescendantOf(game) then
			self.attrPairs[inst] = nil
			continue
		end
		for _, pair in ipairs(list) do
			local v, mx = inst:GetAttribute(pair[1]), inst:GetAttribute(pair[2])
			if type(v) == "number" and type(mx) == "number" then
				self:_overMax(inst, pair[1], v, pair[2], mx)
			end
		end
	end
	for value, mxValue in pairs(self.valuePairs) do
		local holder = value.Parent
		if holder == nil or mxValue.Parent ~= holder or not value:IsDescendantOf(game) then
			self.valuePairs[value] = nil
			continue
		end
		self:_overMax(holder, value.Name, (value :: any).Value, mxValue.Name, (mxValue :: any).Value)
	end
end

return Detectors
