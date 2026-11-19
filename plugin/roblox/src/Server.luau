--!strict
-- The play session's server: the game's authoritative state. Answers rc (reads, writes and calls on the server's
-- instances and modules: what the game's Scripts see and change; LocalPlayer, Character and Humanoid are the request's
-- `player`'s), find and transforms on the server's instances, pause, adds players to a multiplayer test
-- (`add_players`), respawns the players (`respawn`: a restart in place) and ends the play session (`end_test`,
-- StudioTestService:EndTest). Runs the server's detectors.
local RunService = game:GetService("RunService")

local Detectors = require(script.Parent.Detectors)
local Frame = require(script.Parent.Frame)
local Host = require(script.Parent.Host)
local Json = require(script.Parent.Json)
local Modules = require(script.Parent.Modules)
local Paths = require(script.Parent.Paths)
local Rc = require(script.Parent.Rc)
local World = require(script.Parent.World)

local Server = {}

function Server.start(plugin: Plugin)
	local host: any = nil
	local detectors: any = nil
	local paused = false

	local ops: { [string]: any } = {
		rc = function(args)
			local n = tonumber(args.player) or 1
			if n > 1 and type(args.body) == "table" and args.body.objectPath ~= nil then
				args.body.objectPath = Paths.forPlayer(tostring(args.body.objectPath), n)
			end
			return Rc.handle(tostring(args.verb or ""), args.body)
		end,
		-- `add_players`: more players in a multiplayer test (Studio starts each its own client, as its Server &
		-- Clients test does): the editor asks when fewer joined than it asked for
		add_players = function(args)
			local n = math.clamp(tonumber(args.count) or 1, 1, 7)
			local ok, err = pcall(function()
				game:GetService("StudioTestService"):AddPlayers(n)
			end)
			if not ok then
				return false, "Studio didn't add players: " .. tostring(err), 409
			end
			return true, { asked = n }
		end,
		find = function(args)
			local out, why = World.find(args)
			if why then
				return false, why, 400
			end
			return true, out
		end,
		transforms = function(args)
			return true, World.transforms(args, Paths.resolve)
		end,
		ping = function()
			return true, { game_time = Frame.r2(World.gameTime()) }
		end,
		detectors = function(args)
			if detectors then
				detectors:configure(args)
			end
			return true, Json.object()
		end,
		-- `pause`: the whole simulation (physics and every script, both sides), as Studio's Pause button does
		pause = function(args)
			local want = args.paused == true
			local ok, err = pcall(function()
				if want then
					RunService:Pause()
				else
					RunService:Run()
				end
			end)
			if not ok then
				return false, "Roblox Studio didn't pause the play session: " .. tostring(err), 409
			end
			paused = want
			if detectors then
				detectors.paused = want
			end
			return true, { paused = paused }
		end,
		-- `respawn`: every player's character loaded afresh where the game spawns it (Player:LoadCharacter), the play
		-- session going on: the restart in place of runs one after another in one game (the editor's session restart
		-- "in_place"). A place can't reopen its world without stopping play: what the world changed stays
		respawn = function()
			if paused then
				pcall(function()
					RunService:Run()
				end)
				paused = false
				if detectors then
					detectors.paused = false
				end
			end
			local players = game:GetService("Players"):GetPlayers()
			if #players == 0 then
				return false, "No player is in the game to respawn", 409
			end
			for _, player in players do
				local ok, err = pcall(function()
					player:LoadCharacter()
				end)
				if not ok then
					return false, "Couldn't respawn " .. player.Name .. ": " .. tostring(err), 500
				end
			end
			local deadline = os.clock() + 20
			while os.clock() < deadline do
				local all = true
				for _, player in players do
					local character = player.Character
					if not (character and character:FindFirstChildOfClass("Humanoid")
						and character:FindFirstChild("HumanoidRootPart")) then
						all = false
					end
				end
				if all then
					break
				end
				task.wait(0.1)
			end
			return true, { running = true, scene = game.Name, players = #players, respawned = true }
		end,
		end_test = function()
			local ok, err = pcall(function()
				game:GetService("StudioTestService"):EndTest(Json.object())
			end)
			if not ok then
				return false, "couldn't end the play session: " .. tostring(err), 500
			end
			return true, { running = false }
		end,
	}

	host = Host.new(plugin, "server", ops)
	host.onAttach = function(frame)
		Modules.install()
		if detectors == nil then
			detectors = Detectors.new(function(e)
				host:emit(e)
			end, World.gameTime, nil)
		end
		if type(frame.detectors) == "table" then
			detectors:configure(frame.detectors)
		end
	end
	host.onActed = function(frame)
		if detectors then
			detectors:noteActed(frame)
		end
	end
	host.onDetach = function()
		Modules.remove()
		if detectors then
			detectors:stop()
			detectors = nil
		end
	end
	table.insert(host.tickers, function(dt)
		if detectors then
			detectors:setActive(host:runUsingGame())
			detectors:tick(dt)
		end
	end)
	return {
		stop = function()
			host:stop()
		end,
	}
end

return Server
