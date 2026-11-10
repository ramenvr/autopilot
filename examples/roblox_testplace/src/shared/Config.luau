--!strict
-- The test place's numbers, shared by the server and the client. Distances are studs.

local Config = {}

-- The tags the place's gameplay objects carry (CollectionService); the server builds each kind in Build.server.luau
Config.Tags = {
	Checkpoint = "Checkpoint",
	Coin = "Coin",
	Door = "Door",
	Lever = "Lever",
	Hazard = "Hazard",
}

-- The obby: checkpoints in order along -z, each a platform; gaps between them must be jumped
-- (a Humanoid's default JumpHeight, 7.2 studs, clears each gap's rise; its WalkSpeed, 16, its length)
Config.Checkpoints = {
	{ stage = 1, position = Vector3.new(0, 1, -24), size = Vector3.new(10, 2, 10) },
	{ stage = 2, position = Vector3.new(0, 4, -40), size = Vector3.new(8, 2, 8) },
	{ stage = 3, position = Vector3.new(0, 7, -56), size = Vector3.new(8, 2, 8) },
}
Config.FinalStage = 3

-- The coin: touched by a character, it adds CoinValue to the player's leaderstats.Coins and is gone for good
Config.Coin = { position = Vector3.new(14, 2, -10), value = 1 }

-- The lever opens the door: its ProximityPrompt (E, held for HoldSeconds, within MaxDistance) toggles the door
Config.Lever = { position = Vector3.new(-14, 2, -86), holdSeconds = 0.5, maxDistance = 10 }
Config.Door = { position = Vector3.new(0, 6, -96), size = Vector3.new(12, 12, 1), openSeconds = 0.6 }

-- A lava strip between checkpoints 2 and 3: touching it kills the character, who respawns at its last checkpoint
Config.Lava = { position = Vector3.new(0, 0.6, -48), size = Vector3.new(24, 0.2, 6) }

return Config
