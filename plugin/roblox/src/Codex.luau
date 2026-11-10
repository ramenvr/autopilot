--!strict
-- The game's code for the brain, from the place open in Studio (the edit DataModel): `codex_manifest`, `codex_read`.
--
-- Every script of the place (Script, LocalScript, ModuleScript) as a file, at its place in the DataModel the way Rojo
-- lays a project out: src/<Service>/<...>/<Name>.server.luau (a Script that runs on the server), .client.luau (a
-- LocalScript, or a Script whose RunContext is Client), .luau (a ModuleScript); a script's own children go in a folder
-- of its name beside it. Plus the plugin's export of the rest of the place (.autopilot/): summary.txt (the place, its
-- services' settings, the player's settings, every script with what kind it is) and tree/<Service>.txt (each
-- service's instances with their class, the properties that tell what they are, attributes, tags and values).
-- Paths follow the protocol's rules (relative, forward slashes, no "..", no ":"). A manifest holds at most MAX_FILES.
local CollectionService = game:GetService("CollectionService")
local Players = game:GetService("Players")
local StarterPlayer = game:GetService("StarterPlayer")

local Json = require(script.Parent.Json)
local Paths = require(script.Parent.Paths)

local Codex = {}

Codex.SERVICES = { "Workspace", "ReplicatedFirst", "ReplicatedStorage", "ServerScriptService", "ServerStorage",
	"StarterGui", "StarterPack", "StarterPlayer", "Lighting", "SoundService", "Teams", "TextChatService", "Chat",
	"Players" }
Codex.EXPORT = ".autopilot/"
Codex.MAX_FILES = 20000
Codex.DEFAULT_READ = 1000000
Codex.MAX_READ = 4 * 1024 * 1024
local TREE_LINES = 20000 -- per service

local files: { [string]: () -> string } = {} -- path -> its text, from the latest manifest

-- A name as a path part: no slashes, colons, backslashes or control characters; never "." or "..".
local function clean(name: string): string
	local s = string.gsub(name, "[/\\:%c%*%?\"<>|]", "_")
	if s == "" or s == "." or s == ".." then
		s = "_" .. s
	end
	return s
end

-- A djb2 hash of a text, as hex (equal texts, equal hashes; the brain fetches only files whose hash changed).
function Codex.hash(text: string): string
	local b = buffer.fromstring(text)
	local h = 5381
	for i = 0, buffer.len(b) - 1 do
		h = (h * 33 + buffer.readu8(b, i)) % 4294967296
	end
	return string.format("%08x%x", h, buffer.len(b))
end

local function kindOf(s: LuaSourceContainer): string
	if s:IsA("ModuleScript") then
		return ""
	elseif s:IsA("LocalScript") then
		return ".client"
	elseif s:IsA("Script") then
		local ok, rc = pcall(function()
			return (s :: Script).RunContext
		end)
		if ok and rc == Enum.RunContext.Client then
			return ".client"
		end
		return ".server"
	end
	return ""
end

local function sourceOf(s: LuaSourceContainer): string
	local ok, src = pcall(function()
		return (s :: any).Source
	end)
	return if ok and type(src) == "string" then src else ""
end

-- The file path of a script: its ancestors' names as folders (a script above it by its name), then its own.
local function scriptPath(s: LuaSourceContainer): string
	local parts = {}
	local x: Instance? = s.Parent
	while x and x ~= game do
		table.insert(parts, 1, clean(x.Name))
		x = x.Parent
	end
	return "src/" .. table.concat(parts, "/") .. (if #parts > 0 then "/" else "") .. clean(s.Name) .. kindOf(s) .. ".luau"
end

local function isOurs(inst: Instance): boolean
	return inst.Name == "AutopilotPlaytest" or inst:GetAttribute("AutopilotPlaytest") ~= nil
end

local function scripts(): { LuaSourceContainer }
	local out = {}
	for _, name in ipairs(Codex.SERVICES) do
		local svc = game:FindFirstChild(name)
		if svc then
			for _, d in ipairs(svc:GetDescendants()) do
				if d:IsA("LuaSourceContainer") and not isOurs(d) then
					table.insert(out, d)
				end
			end
		end
	end
	return out
end

local function fmt(x: number): string
	return string.format("%.4g", x)
end

local function vec(v: Vector3): string
	return string.format("(%s, %s, %s)", fmt(v.X), fmt(v.Y), fmt(v.Z))
end

local function valueText(v: any): string
	local t = typeof(v)
	if t == "Vector3" then
		return vec(v)
	elseif t == "Instance" then
		return Paths.of(v)
	elseif t == "string" then
		return string.format("%q", string.sub(v, 1, 80))
	elseif t == "number" then
		return fmt(v)
	end
	return tostring(v)
end

-- What tells an instance apart, in a few words: a part's size, place and how it collides; a value's value; a GUI's
-- text; a humanoid's numbers; a sound or animation's id; attributes and tags.
local function describe(inst: Instance): string
	local bits = {}
	if inst:IsA("BasePart") then
		local p = inst :: BasePart
		table.insert(bits, "at " .. vec(p.Position) .. " size " .. vec(p.Size))
		if p.Anchored then
			table.insert(bits, "anchored")
		end
		if not p.CanCollide then
			table.insert(bits, "no collide")
		end
		if p.Transparency >= 0.99 then
			table.insert(bits, "invisible")
		end
	elseif inst:IsA("Model") then
		local m = inst :: Model
		local ok, cf, size = pcall(function()
			return m:GetBoundingBox()
		end)
		if ok then
			table.insert(bits, "at " .. vec(cf.Position) .. " size " .. vec(size))
		end
	elseif inst:IsA("ValueBase") then
		table.insert(bits, "= " .. valueText((inst :: any).Value))
	elseif inst:IsA("TextLabel") or inst:IsA("TextButton") or inst:IsA("TextBox") then
		local text = (inst :: any).Text
		if text ~= "" then
			table.insert(bits, "text " .. valueText(text))
		end
	elseif inst:IsA("Humanoid") then
		local h = inst :: Humanoid
		table.insert(bits, string.format("health %s/%s walk %s jump %s", fmt(h.Health), fmt(h.MaxHealth), fmt(h.WalkSpeed),
			fmt(if h.UseJumpPower then h.JumpPower else h.JumpHeight)))
	elseif inst:IsA("Sound") then
		table.insert(bits, "sound " .. (inst :: Sound).SoundId)
	elseif inst:IsA("Animation") then
		table.insert(bits, "animation " .. (inst :: Animation).AnimationId)
	elseif inst:IsA("RemoteEvent") or inst:IsA("RemoteFunction") or inst:IsA("BindableEvent")
		or inst:IsA("BindableFunction") then
		table.insert(bits, "(a channel between scripts)")
	end
	local attrs = {}
	for k, v in pairs(inst:GetAttributes()) do
		table.insert(attrs, k .. "=" .. valueText(v))
	end
	table.sort(attrs)
	if #attrs > 0 then
		table.insert(bits, "attributes {" .. table.concat(attrs, ", ") .. "}")
	end
	local tags = CollectionService:GetTags(inst)
	if #tags > 0 then
		table.insert(bits, "tags [" .. table.concat(tags, ", ") .. "]")
	end
	return table.concat(bits, " ")
end

-- A service's instances, one per line (indented by depth): name, class, what tells it apart. Runs of siblings that
-- are the same (same name and class, no scripts in them) are told once with their count.
local function tree(svc: Instance): string
	local lines = { "# " .. svc.Name .. " (" .. svc.ClassName .. "): its instances, as paths go: " .. Paths.of(svc) }
	local count = 0
	local function walk(inst: Instance, depth: number)
		local kids = inst:GetChildren()
		local i = 1
		while i <= #kids do
			if count >= TREE_LINES then
				return
			end
			local c = kids[i]
			if isOurs(c) or c:IsA("Terrain") and depth > 0 then
				i += 1
				continue
			end
			local same = 1
			while i + same <= #kids and kids[i + same].Name == c.Name and kids[i + same].ClassName == c.ClassName
				and #kids[i + same]:GetChildren() == #c:GetChildren() do
				same += 1
			end
			local line = string.rep("  ", depth) .. c.Name .. " (" .. c.ClassName .. ")"
			local d = describe(c)
			if d ~= "" then
				line ..= " " .. d
			end
			if same > 2 and c:FindFirstChildWhichIsA("LuaSourceContainer", true) == nil then
				line ..= string.format("  x%d (like this one)", same)
				table.insert(lines, line)
				count += 1
				walk(c, depth + 1)
				i += same
			else
				table.insert(lines, line)
				count += 1
				walk(c, depth + 1)
				i += 1
			end
		end
	end
	walk(svc, 0)
	if count >= TREE_LINES then
		table.insert(lines, string.format("... (more: the listing stops at %d lines)", TREE_LINES))
	end
	return table.concat(lines, "\n") .. "\n"
end

-- The place's summary: what it is, its services' settings, the player's settings, and its scripts by kind.
function Codex.summary(): string
	local lines = {}
	local function add(s: string)
		table.insert(lines, s)
	end
	add(string.format("Roblox place: %s (place id %d, universe id %d; Studio %s)", game.Name, game.PlaceId, game.GameId,
		version()))
	add("")
	add("How a Roblox game runs: Scripts run on the server, LocalScripts (and Scripts with RunContext Client) on each")
	add("player's client; ModuleScripts run where they are required. ServerScriptService and ServerStorage exist only on")
	add("the server; ReplicatedStorage, the Workspace and its parts are on both; StarterGui is copied into each player's")
	add("PlayerGui, StarterPlayerScripts into PlayerScripts and StarterCharacterScripts into the character when it")
	add("spawns.")
	add("")
	add("Workspace: gravity " .. fmt(workspace.Gravity) .. " studs/s^2, FallenPartsDestroyHeight "
		.. fmt(workspace.FallenPartsDestroyHeight) .. ", StreamingEnabled " .. tostring(workspace.StreamingEnabled))
	local sp = StarterPlayer
	add(string.format("StarterPlayer: CharacterWalkSpeed %s, %s %s, CharacterMaxSlopeAngle %s, AutoJumpEnabled %s, "
		.. "CameraMode %s, DevComputerMovementMode %s, DevComputerCameraMovementMode %s, CameraMaxZoomDistance %s, "
		.. "EnableMouseLockOption %s", fmt(sp.CharacterWalkSpeed),
		if sp.CharacterUseJumpPower then "CharacterJumpPower" else "CharacterJumpHeight",
		fmt(if sp.CharacterUseJumpPower then sp.CharacterJumpPower else sp.CharacterJumpHeight),
		fmt(sp.CharacterMaxSlopeAngle), tostring(sp.AutoJumpEnabled), sp.CameraMode.Name,
		sp.DevComputerMovementMode.Name, sp.DevComputerCameraMovementMode.Name, fmt(sp.CameraMaxZoomDistance),
		tostring(sp.EnableMouseLockOption)))
	add(string.format("Players: MaxPlayers %d, CharacterAutoLoads %s, RespawnTime %s", Players.MaxPlayers,
		tostring(Players.CharacterAutoLoads), fmt(Players.RespawnTime)))
	local custom = StarterPlayer:FindFirstChild("StarterCharacter")
	if custom then
		add("A custom character: StarterPlayer.StarterCharacter (" .. custom.ClassName .. ")")
	end
	local teams = game:FindFirstChild("Teams")
	if teams and #teams:GetChildren() > 0 then
		local names = {}
		for _, t in ipairs(teams:GetChildren()) do
			table.insert(names, t.Name)
		end
		add("Teams: " .. table.concat(names, ", "))
	end
	local tags = {}
	for _, tag in ipairs(CollectionService:GetAllTags()) do
		local n = #CollectionService:GetTagged(tag)
		if n > 0 then
			table.insert(tags, string.format("%s (%d)", tag, n))
		end
	end
	if #tags > 0 then
		table.sort(tags)
		add("CollectionService tags (find them with game.actors(\"tag:<name>\")): " .. table.concat(tags, ", "))
	end
	add("")
	add("Scripts (the codex has each under src/, at its place in the DataModel):")
	for _, s in ipairs(scripts()) do
		local kind = if s:IsA("ModuleScript") then "module" elseif kindOf(s) == ".client" then "client" else "server"
		local disabled = ""
		if s:IsA("BaseScript") and not (s :: BaseScript).Enabled then
			disabled = " (disabled)"
		end
		add(string.format("  %s  %s%s  -> %s", kind, Paths.of(s), disabled, scriptPath(s)))
	end
	add("")
	add("The rest of the place, service by service: tree/<Service>.txt")
	return table.concat(lines, "\n") .. "\n"
end

-- `codex_manifest`: every file with its size and hash. export "skip" leaves the export (summary, trees) as it was.
function Codex.manifest(args: any): any
	local started = os.clock()
	table.clear(files)
	local list = {}
	local taken = {}
	local truncated = false
	for _, s in ipairs(scripts()) do
		if #list >= Codex.MAX_FILES then
			truncated = true
			break
		end
		local path = scriptPath(s)
		if taken[path] then
			-- (two scripts at one path: same-named siblings)
			local n = 2
			while taken[string.gsub(path, "%.luau$", "~" .. n .. ".luau")] do
				n += 1
			end
			path = string.gsub(path, "%.luau$", "~" .. n .. ".luau")
		end
		taken[path] = true
		local src = sourceOf(s)
		files[path] = function()
			return sourceOf(s)
		end
		table.insert(list, { path = path, size = #src, hash = Codex.hash(src) })
	end
	local export = tostring(args and args.export or "auto") ~= "skip"
	local texts: { [string]: string } = {}
	texts[Codex.EXPORT .. "summary.txt"] = Codex.summary()
	for _, name in ipairs(Codex.SERVICES) do
		local svc = game:FindFirstChild(name)
		if svc then
			texts[Codex.EXPORT .. "tree/" .. name .. ".txt"] = tree(svc)
		end
	end
	for path, text in pairs(texts) do
		files[path] = function()
			return text
		end
		table.insert(list, { path = path, size = #text, hash = Codex.hash(text) })
	end
	return { project = game.Name, files = list, truncated = truncated,
		export = { ran = export, seconds = math.round((os.clock() - started) * 1000) / 1000, exit_code = 0,
			reason = "refresh" } }
end

-- `codex_read`: {"paths", "max_bytes"} -> {"files": {path: text}, "missing", "too_big"}
function Codex.read(args: any): any
	if next(files) == nil then
		Codex.manifest({ export = "auto" })
	end
	local maxBytes = math.clamp(tonumber(args.max_bytes) or Codex.DEFAULT_READ, 1, Codex.MAX_READ)
	local out = Json.object()
	local missing, tooBig = {}, {}
	local used = 0
	for _, x in ipairs(args.paths or {}) do
		local asked = tostring(x)
		local get = files[asked]
		if get == nil then
			table.insert(missing, asked)
			continue
		end
		local text = get()
		if #text > maxBytes then
			table.insert(tooBig, asked)
			continue
		end
		if used + #text > maxBytes then
			break -- (this and the rest go unanswered: the brain asks again)
		end
		out[asked] = text
		used += #text
	end
	return { files = out, missing = missing, too_big = tooBig }
end

return Codex
