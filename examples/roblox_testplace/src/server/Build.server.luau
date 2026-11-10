--!strict
-- Builds the test place's gameplay objects from Config and tags them, so the place is all in text (Rojo) and the agent
-- finds everything by tag: Checkpoint, Coin, Door, Lever, Hazard. Runs first on the server (ServerScriptService).

local CollectionService = game:GetService("CollectionService")
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local Config = require(ReplicatedStorage:WaitForChild("Config"))

local function part(name: string, parent: Instance, position: Vector3, size: Vector3, color: Color3): Part
	local p = Instance.new("Part")
	p.Name = name
	p.Anchored = true
	p.Size = size
	p.Position = position
	p.Color = color
	p.TopSurface = Enum.SurfaceType.Smooth
	p.Parent = parent
	return p
end

local obby = Instance.new("Folder")
obby.Name = "Obby"
obby.Parent = workspace

for _, c in ipairs(Config.Checkpoints) do
	local cp = part("Checkpoint", obby, c.position, c.size, Color3.fromRGB(90, 140, 230))
	cp:SetAttribute("Stage", c.stage)
	CollectionService:AddTag(cp, Config.Tags.Checkpoint)
end

local lava = part("Lava", obby, Config.Lava.position, Config.Lava.size, Color3.fromRGB(255, 80, 20))
lava.Material = Enum.Material.Neon
lava.CanCollide = false
CollectionService:AddTag(lava, Config.Tags.Hazard)

local coin = part("Coin", workspace, Config.Coin.position, Vector3.new(2, 2, 0.4), Color3.fromRGB(255, 210, 40))
coin.Shape = Enum.PartType.Cylinder
coin.CanCollide = false
coin:SetAttribute("Value", Config.Coin.value)
CollectionService:AddTag(coin, Config.Tags.Coin)

local door = part("Door", workspace, Config.Door.position, Config.Door.size, Color3.fromRGB(120, 80, 50))
door:SetAttribute("Open", false)
CollectionService:AddTag(door, Config.Tags.Door)

local lever = Instance.new("Model")
lever.Name = "Lever"
local base = part("Base", lever, Config.Lever.position, Vector3.new(2, 1, 2), Color3.fromRGB(70, 70, 70))
local handle = part("Handle", lever, Config.Lever.position + Vector3.new(0, 1.5, 0), Vector3.new(0.4, 2, 0.4),
	Color3.fromRGB(200, 30, 30))
lever.PrimaryPart = base
lever:SetAttribute("Pulled", false)
local prompt = Instance.new("ProximityPrompt")
prompt.ActionText = "Pull"
prompt.ObjectText = "Lever"
prompt.KeyboardKeyCode = Enum.KeyCode.E
prompt.HoldDuration = Config.Lever.holdSeconds
prompt.MaxActivationDistance = Config.Lever.maxDistance
prompt.Parent = handle
lever.Parent = workspace
CollectionService:AddTag(lever, Config.Tags.Lever)
