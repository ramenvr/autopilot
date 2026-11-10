--!strict
-- The test place's rules, on the server: leaderstats (Coins, Stage), checkpoints (a character that touches a later
-- checkpoint's platform reaches that stage and respawns there), the coin pickup, the lever that opens the door, and
-- lava that kills. Everything is found by its CollectionService tag (see Build.server.luau).

local CollectionService = game:GetService("CollectionService")
local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local TweenService = game:GetService("TweenService")

local Config = require(ReplicatedStorage:WaitForChild("Config"))
local Remotes = ReplicatedStorage:WaitForChild("Remotes")
local StageReached = Remotes:WaitForChild("StageReached") :: RemoteEvent

local function stats(player: Player)
	local ls = Instance.new("Folder")
	ls.Name = "leaderstats"
	local coins = Instance.new("IntValue")
	coins.Name = "Coins"
	coins.Parent = ls
	local stage = Instance.new("IntValue")
	stage.Name = "Stage"
	stage.Value = 0
	stage.Parent = ls
	ls.Parent = player
end

local function playerOf(hit: BasePart): Player?
	local model = hit:FindFirstAncestorOfClass("Model")
	if not model then
		return nil
	end
	local hum = model:FindFirstChildOfClass("Humanoid")
	if not hum or hum.Health <= 0 then
		return nil
	end
	return Players:GetPlayerFromCharacter(model)
end

-- -- checkpoints ----------------------------------------------------------------------------------------------------

local function checkpointOf(stage: number): BasePart?
	for _, cp in ipairs(CollectionService:GetTagged(Config.Tags.Checkpoint)) do
		if cp:GetAttribute("Stage") == stage then
			return cp :: BasePart
		end
	end
	return nil
end

local function setStage(player: Player, stage: number)
	local ls = player:FindFirstChild("leaderstats")
	local value = ls and ls:FindFirstChild("Stage") :: IntValue?
	if value and stage > value.Value then
		value.Value = stage
		StageReached:FireClient(player, stage, stage >= Config.FinalStage)
	end
end

local function watchCheckpoint(cp: BasePart)
	cp.Touched:Connect(function(hit)
		local player = playerOf(hit)
		if player then
			setStage(player, cp:GetAttribute("Stage") :: number)
		end
	end)
end

-- A character spawns on its last checkpoint (or the start, at stage 0)
local function onCharacter(player: Player, character: Model)
	local ls = player:WaitForChild("leaderstats")
	local stage = (ls:WaitForChild("Stage") :: IntValue).Value
	local cp = stage > 0 and checkpointOf(stage) or nil
	if cp then
		local root = character:WaitForChild("HumanoidRootPart") :: BasePart
		task.defer(function()
			character:PivotTo(CFrame.new(cp.Position + Vector3.new(0, cp.Size.Y / 2 + 3, 0)))
			root.AssemblyLinearVelocity = Vector3.zero
		end)
	end
end

-- -- the coin -------------------------------------------------------------------------------------------------------

local function watchCoin(coin: BasePart)
	local taken = false
	coin.Touched:Connect(function(hit)
		local player = playerOf(hit)
		if taken or not player then
			return
		end
		taken = true
		local coins = player.leaderstats.Coins :: IntValue
		coins.Value += (coin:GetAttribute("Value") :: number?) or 1
		coin:Destroy()
	end)
end

-- -- the lever and the door -----------------------------------------------------------------------------------------

local function setDoor(open: boolean)
	for _, door in ipairs(CollectionService:GetTagged(Config.Tags.Door)) do
		local d = door :: BasePart
		if d:GetAttribute("Open") ~= open then
			d:SetAttribute("Open", open)
			d.CanCollide = not open
			local goal = { Transparency = open and 0.8 or 0 }
			TweenService:Create(d, TweenInfo.new(Config.Door.openSeconds), goal):Play()
		end
	end
end

local function watchLever(lever: Model)
	local prompt = lever:FindFirstChildWhichIsA("ProximityPrompt", true)
	if not prompt then
		warn("Lever without a ProximityPrompt: " .. lever:GetFullName())
		return
	end
	prompt.Triggered:Connect(function(player)
		local pulled = not lever:GetAttribute("Pulled")
		lever:SetAttribute("Pulled", pulled)
		setDoor(pulled)
	end)
end

-- -- lava -----------------------------------------------------------------------------------------------------------

local function watchHazard(hazard: BasePart)
	hazard.Touched:Connect(function(hit)
		local model = hit:FindFirstAncestorOfClass("Model")
		local hum = model and model:FindFirstChildOfClass("Humanoid")
		if hum then
			hum.Health = 0
		end
	end)
end

-- -- start ----------------------------------------------------------------------------------------------------------

local function each(tag: string, fn: (any) -> ())
	for _, inst in ipairs(CollectionService:GetTagged(tag)) do
		fn(inst)
	end
	CollectionService:GetInstanceAddedSignal(tag):Connect(fn)
end

each(Config.Tags.Checkpoint, watchCheckpoint)
each(Config.Tags.Coin, watchCoin)
each(Config.Tags.Lever, watchLever)
each(Config.Tags.Hazard, watchHazard)

Players.PlayerAdded:Connect(function(player)
	stats(player)
	player.CharacterAdded:Connect(function(character)
		onCharacter(player, character)
	end)
end)
for _, player in ipairs(Players:GetPlayers()) do
	stats(player)
end
