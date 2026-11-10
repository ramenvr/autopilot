--!strict
-- The plugin's settings, kept by Studio for the user (plugin:SetSetting: per user and computer, never in the place, so
-- nothing of Autopilot's is saved with the game). Every DataModel of a play session reads the same settings, which is
-- how the play session's sides learn where the editor's pipe is.
local Settings = {}

local DEFAULTS = {
	url = "", -- the agent service's address ("": ws://127.0.0.1:8765/v1/bridge, `autopilot serve` on this computer)
	token = "", -- a bearer token, for an agent service that asks for one
	http_url = "", -- optional HTTP fallback base URL, including /v1/http/
	share = false, -- share runs' data to improve Autopilot (the hello's `share`)
	time_limit = 30, -- the composer's Time limit, in minutes
	autoconnect = true, -- connect when a place opens
	design_brief = "", -- how the game is meant to work, sent with every run the dock starts (the start's design_brief)
	show_policy_log = true, -- the dock's Policy log and the policy steps asked of the agent service (hello `inspect`)
	-- research: nil until the user flips the Research agent switch, then a boolean (the hello's `research`; left out,
	-- the agent service decides)
}

local function key(name: string): string
	return "Autopilot_" .. name
end

function Settings.get(plugin: Plugin, name: string): any
	local ok, v = pcall(function()
		return plugin:GetSetting(key(name))
	end)
	if ok and v ~= nil then
		return v
	end
	return (DEFAULTS :: any)[name]
end

function Settings.set(plugin: Plugin, name: string, value: any)
	pcall(function()
		plugin:SetSetting(key(name), value)
	end)
end

-- Where the editor's pipe is ({url, key}), or nil while the editor isn't connected.
function Settings.pipe(plugin: Plugin): any
	local p = Settings.get(plugin, "pipe")
	if type(p) == "table" and type(p.url) == "string" and type(p.key) == "string" and p.key ~= "" then
		return p
	end
	return nil
end

return Settings
