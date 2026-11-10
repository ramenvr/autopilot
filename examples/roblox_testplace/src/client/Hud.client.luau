--!strict
-- The player's HUD on the client (a LocalScript in StarterPlayerScripts): the coin count and stage from leaderstats,
-- and a message when the server says a stage was reached (the StageReached RemoteEvent).

local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local player = Players.LocalPlayer
local gui = player:WaitForChild("PlayerGui"):WaitForChild("Hud") :: ScreenGui
local coinsLabel = gui:WaitForChild("Coins") :: TextLabel
local stageLabel = gui:WaitForChild("Stage") :: TextLabel
local message = gui:WaitForChild("Message") :: TextLabel

local ls = player:WaitForChild("leaderstats")
local coins = ls:WaitForChild("Coins") :: IntValue
local stage = ls:WaitForChild("Stage") :: IntValue

local function refresh()
	coinsLabel.Text = "Coins: " .. coins.Value
	stageLabel.Text = "Stage: " .. stage.Value
end
coins.Changed:Connect(refresh)
stage.Changed:Connect(refresh)
refresh()

local StageReached = ReplicatedStorage:WaitForChild("Remotes"):WaitForChild("StageReached") :: RemoteEvent
StageReached.OnClientEvent:Connect(function(reached: number, final: boolean)
	message.Text = final and "You finished the obby!" or ("Checkpoint " .. reached .. " reached")
	message.Visible = true
	task.delay(2, function()
		message.Visible = false
	end)
end)
