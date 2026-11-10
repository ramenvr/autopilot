--!strict
-- Instances by path, the way the brain names things (plugin/roblox/README.md, "Paths").
--
-- A path is "/" + the names from the DataModel down, its first the service's: /Workspace/Map/Door,
-- /Players/Player1/PlayerGui/HUD/Score, /ReplicatedStorage/Modules/Config. Siblings that share a name are told apart
-- by a number: Coin@617854, Coin@617860 (the number of the instance's debug id: the same on the server and the client,
-- never given to another while Studio runs), so a path stays that instance or is gone; the plain name of twins is the
-- one with the lowest number. "\" escapes "/", "@" and "\" in a name. Also taken: a dotted full name
-- (Workspace.Map.Door, game.Workspace.Map.Door, as GetFullName says it), a path without the leading "/"
-- (Workspace/Map/Door), a service's name (Lighting), and LocalPlayer, Character, Humanoid, Camera for the local
-- player's own (on the server: the first player's). A "server:" or "client:" in front says which side of the play
-- session to read it on (Editor.route); it isn't part of the instance's path.
local Players = game:GetService("Players")

local Paths = {}

local function escape(name: string): string
	return (string.gsub(name, "[\\/@]", "\\%0"))
end

-- An instance's number among same-named siblings: the number of its debug id, which a replicated instance has the
-- same on the server and the client (0_617854 and 1_617854), so both sides name it alike; stable while it lives.
local function numberOf(inst: Instance): number
	local ok, id = pcall(function()
		return inst:GetDebugId()
	end)
	local n = ok and tonumber(string.match(id, "(%d+)$")) or nil
	return n or 0
end
Paths.numberOf = numberOf

local function hasTwin(inst: Instance, parent: Instance): boolean
	local name = inst.Name
	for _, sib in ipairs(parent:GetChildren()) do
		if sib ~= inst and sib.Name == name then
			return true
		end
	end
	return false
end

-- One step of a path: the instance's name, escaped, with its number when a sibling shares the name.
local function segment(inst: Instance, parent: Instance): string
	local s = escape(inst.Name)
	if hasTwin(inst, parent) then
		s ..= "@" .. tostring(numberOf(inst))
	end
	return s
end

-- The path of an instance ("" for nil or one outside the DataModel).
function Paths.of(inst: Instance?): string
	if inst == nil then
		return ""
	end
	if inst == game then
		return "/"
	end
	local parts = {}
	local x: Instance? = inst
	while x and x ~= game do
		local parent = x.Parent
		if parent == nil then
			return ""
		end
		table.insert(parts, 1, segment(x, parent))
		x = parent
	end
	if x ~= game then
		return ""
	end
	return "/" .. table.concat(parts, "/")
end

-- The last part of a path, as the brain's name_of reads it: the instance's name (with its @number).
function Paths.nameOf(inst: Instance): string
	local parent = inst.Parent
	if parent == nil then
		return escape(inst.Name)
	end
	return segment(inst, parent)
end

-- The parts of a path: {name, number or nil}, unescaped.
local function split(path: string): { { any } }
	local out = {}
	local name, num = {}, nil :: string?
	local i, n = 1, #path
	local inNumber = false
	local function flush()
		if #name > 0 or num then
			table.insert(out, { table.concat(name), num and tonumber(num) or nil })
		end
		name, num, inNumber = {}, nil, false
	end
	while i <= n do
		local c = string.sub(path, i, i)
		if c == "\\" and i < n then
			table.insert(name, string.sub(path, i + 1, i + 1))
			i += 2
			continue
		end
		if c == "/" then
			flush()
		elseif c == "@" and not inNumber then
			inNumber = true
			num = ""
		elseif inNumber then
			num = (num or "") .. c
		else
			table.insert(name, c)
		end
		i += 1
	end
	flush()
	return out
end

local function child(parent: Instance, name: string, num: number?): Instance?
	-- (plain: the only one of that name, else the one with the lowest number; name@n: that one)
	local best: Instance? = nil
	local bestN = math.huge
	for _, c in ipairs(parent:GetChildren()) do
		if c.Name == name then
			local n = numberOf(c)
			if num ~= nil then
				if n == num then
					return c
				end
			elseif n < bestN then
				best, bestN = c, n
			end
		end
	end
	return best
end

local function service(name: string): Instance?
	local found = game:FindFirstChild(name)
	if found then
		return found
	end
	local ok, s = pcall(function()
		return game:GetService(name)
	end)
	return if ok then s else nil
end

-- A player's number in the play session: Studio's test players are Player1, Player2... (UserId -1, -2...); the one
-- player of a play session that isn't a multiplayer test (the user's own account) is 1.
function Paths.playerIndex(p: Player): number
	return if p.UserId < 0 then -p.UserId else 1
end

-- Player n (1 the player Autopilot plays as), or nil.
function Paths.player(n: number): Player?
	for _, p in ipairs(Players:GetPlayers()) do
		if Paths.playerIndex(p) == n then
			return p
		end
	end
	return if n <= 1 then (Players.LocalPlayer or Players:GetPlayers()[1]) else nil
end

-- A path whose first part is a short name (LocalPlayer, Character, Humanoid) as player n's own: on the server, which
-- has every player, a request for another player than the first names that player's things.
function Paths.forPlayer(path: string, n: number): string
	if n <= 1 then
		return path
	end
	local p = Paths.player(n)
	if p == nil then
		return path
	end
	local prefix, rest = string.match(path, "^(%a+:)(.*)$")
	if prefix ~= "server:" and prefix ~= "client:" then
		prefix, rest = "", path
	end
	local head, tail = string.match(rest :: string, "^/?([^/%.]+)(.*)$")
	if head == nil then
		return path
	end
	local char = p.Character
	local target: Instance? = if head == "LocalPlayer" then p elseif head == "Character" then char
		elseif head == "Humanoid" then (char and char:FindFirstChildOfClass("Humanoid")) else nil
	if target == nil then
		return path
	end
	if string.sub(tail, 1, 1) == "." then
		tail = (string.gsub(tail, "%.", "/"))
	end
	return (prefix :: string) .. Paths.of(target) .. tail
end

-- The local player's own things by a short name (LocalPlayer, Character, Humanoid, Camera), or nil.
local function shortcut(name: string): Instance?
	local lp = Players.LocalPlayer or Paths.player(1)
	if name == "LocalPlayer" then
		return lp
	elseif name == "Character" then
		return lp and lp.Character
	elseif name == "Humanoid" then
		local c = lp and lp.Character
		return c and c:FindFirstChildOfClass("Humanoid")
	elseif name == "Camera" then
		return workspace.CurrentCamera
	end
	return nil
end

-- Which side a path asks for ("server", "client" or nil) and the path without it.
function Paths.side(path: string): (string?, string)
	local side, rest = string.match(path, "^(%a+):(.*)$")
	if side == "server" or side == "client" then
		return side, rest :: string
	end
	return nil, path
end

-- The instance a path names, or nil.
function Paths.resolve(path: string): Instance?
	if type(path) ~= "string" or path == "" then
		return nil
	end
	local _, p = Paths.side(path)
	if p == "/" or p == "game" then
		return game
	end
	if string.sub(p, 1, 1) ~= "/" and not string.find(p, "/", 1, true) and string.find(p, ".", 1, true) then
		-- A dotted full name (Workspace.Map.Door), as GetFullName says it
		local parts = string.split(p, ".")
		local x: Instance? = if parts[1] == "game" then game else nil
		local first = if x then 2 else 1
		x = x or shortcut(parts[first]) or service(parts[first])
		for i = first + (if x == game then 0 else 1), #parts do
			if x == nil then
				return nil
			end
			x = (x :: Instance):FindFirstChild(parts[i])
		end
		return x
	end
	local parts = split(p)
	if #parts == 0 then
		return nil
	end
	local head = parts[1][1]
	local x: Instance? = nil
	if parts[1][2] == nil then
		x = shortcut(head) or service(head)
	end
	if x == nil then
		x = child(game, head, parts[1][2])
	end
	for i = 2, #parts do
		if x == nil then
			return nil
		end
		x = child(x :: Instance, parts[i][1], parts[i][2])
	end
	return x
end

return Paths
