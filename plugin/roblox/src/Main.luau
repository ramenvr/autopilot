--!strict
-- Autopilot for Roblox Studio: the plugin's start. Studio runs a plugin in every DataModel it has: the place being
-- edited, and while a play session goes, that session's server and its player's client. Each starts its own part:
--  - edit: the link to the agent service and the dock (Editor.luau, Dock.luau), hidden while a playtest's client draws
--    it (Mirror.luau);
--  - a play session's client: the dock, drawn there while the playtest runs (Mirror.luau, first: Studio draws no edit
--    widget during play), and the player's eyes and hands (Client.luau);
--  - a play session's server: the game's authoritative state (Server.luau).
-- start(plugin, options) returns {stop = function}: the dev loader (plugin/roblox/dev) stops and starts it again when
-- its sources change; the built plugin starts it once.
local RunService = game:GetService("RunService")

local Main = {}

function Main.side(): string
	if RunService:IsEdit() then
		return "edit"
	elseif RunService:IsClient() then
		return "client"
	end
	return "server"
end

function Main.start(plugin: Plugin, options: any?)
	local side = Main.side()
	local parts = {}
	local function stopAll()
		for i = #parts, 1, -1 do
			pcall(parts[i].stop, parts[i])
		end
		table.clear(parts)
	end
	-- (a part that fails to start leaves none running: a socket left open would count against Studio's limit)
	local ok, err = pcall(function()
		if side == "edit" then
			local Editor = require(script.Parent.Editor)
			local Dock = require(script.Parent.Dock)
			local Settings = require(script.Parent.Settings)
			local editor = Editor.new(plugin)
			table.insert(parts, editor)
			local dock = Dock.new(plugin, editor, options)
			table.insert(parts, dock)
			table.insert(parts, require(script.Parent.Mirror).edit(plugin, editor, dock))
			if Settings.get(plugin, "autoconnect") ~= false then
				editor:connect()
			end
			if options and options.dev then
				(_G :: any).AutopilotEditor = editor -- (for the dev loader's eval: a look inside while developing)
				-- (the dev loader's relay for the play session's client: plugin/roblox/dev, never in the built plugin)
				local relay = script.Parent:FindFirstChild("DevRelay")
				if relay then
					table.insert(parts, (require :: any)(relay).start(plugin, options.root))
				end
			end
		elseif side == "client" then
			local mirror = require(script.Parent.Mirror).client(plugin)
			table.insert(parts, mirror)
			local client = require(script.Parent.Client).start(plugin)
			table.insert(parts, client)
			mirror.attach(client.host)
		else
			table.insert(parts, require(script.Parent.Server).start(plugin))
		end
	end)
	if not ok then
		stopAll()
		error(err, 0)
	end
	local handle = {}
	function handle.stop()
		stopAll()
	end
	plugin.Unloading:Connect(handle.stop)
	return handle
end

return Main
