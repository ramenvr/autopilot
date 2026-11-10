--!strict
-- Debug helpers for testing the place (ServerScriptService/Debug, a ModuleScript on the server). An agent calls them
-- to set up a verification: game.call("ServerScriptService/Debug", "GiveCoin", args=[name, 1]). They change the game
-- directly, as no player could: never part of playing it.

local CollectionService = game:GetService("CollectionService")
local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local Config = require(ReplicatedStorage:WaitForChild("Config"))

local Debug = {}

local function player(name: string?): Player
	local p = name and Players:FindFirstChild(name) or Players:GetPlayers()[1]
	assert(p and p:IsA("Player"), "no player " .. tostring(name))
	return p :: Player
end

-- Adds coins to a player's leaderstats.Coins; returns the new count
function Debug.GiveCoin(name: string?, amount: number?): number
	local coins = player(name).leaderstats.Coins :: IntValue
	coins.Value += amount or 1
	return coins.Value
end

-- Moves a player's character to a position (studs); returns where its root is
function Debug.SetPosition(name: string?, x: number, y: number, z: number): Vector3
	local character = player(name).Character
	assert(character, "the player has no character")
	character:PivotTo(CFrame.new(x, y, z))
	local root = character:FindFirstChild("HumanoidRootPart") :: BasePart
	root.AssemblyLinearVelocity = Vector3.zero
	return root.Position
end

-- Sets a player's stage (0: the start); their next spawn is on that checkpoint
function Debug.SetStage(name: string?, stage: number): number
	local value = player(name).leaderstats.Stage :: IntValue
	value.Value = math.clamp(stage, 0, Config.FinalStage)
	return value.Value
end

-- Puts the place back as it starts: coins and stage 0, the door shut and the lever back, the character respawned
-- (a fresh play session does this too: game.restart())
function Debug.Reset(name: string?): boolean
	local p = player(name)
	p.leaderstats.Coins.Value = 0
	p.leaderstats.Stage.Value = 0
	for _, lever in ipairs(CollectionService:GetTagged(Config.Tags.Lever)) do
		lever:SetAttribute("Pulled", false)
	end
	for _, door in ipairs(CollectionService:GetTagged(Config.Tags.Door)) do
		local d = door :: BasePart
		d:SetAttribute("Open", false)
		d.CanCollide = true
		d.Transparency = 0
	end
	p:LoadCharacter()
	return true
end

return Debug
