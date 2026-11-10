--!nocheck
-- The dev loader's relay for a play session's client (plugin/roblox/dev/AutopilotDev.server.luau: the client can make
-- no HTTP requests and has no WebSocket to spare). Started by Main in the edit DataModel when the plugin runs from the
-- dev loader; the dev server adds it to the bundle, the built plugin has none. Through the plugin's settings, which
-- every DataModel shares: the bundle (AutopilotDev_bundle), what the dev server has for the client (AutopilotDev_in),
-- what the client sends (AutopilotDev_out), posted on to the dev server.
local HttpService = game:GetService("HttpService")

local DEV_URL = "http://127.0.0.1:8797"

local DevRelay = {}

function DevRelay.start(plugin, root)
	local alive = true
	local function setSetting(key, v)
		pcall(function()
			plugin:SetSetting(key, v)
		end)
	end
	local function getSetting(key)
		local ok, v = pcall(function()
			return plugin:GetSetting(key)
		end)
		return if ok then v else nil
	end
	-- The bundle, as the modules this plugin runs (the dev server's own version, so the client loads each change once)
	local modules = {}
	for _, m in ipairs(root:GetChildren()) do
		if m:IsA("ModuleScript") then
			modules[m.Name] = m.Source
		end
	end
	local version = tostring(os.clock()) -- (each start of the plugin's code: the client starts it again too)
	setSetting("AutopilotDev_bundle", { type = "bundle", modules = modules, entry = "Main", version = version })
	local inSeq, inbox = 0, {}
	local had = getSetting("AutopilotDev_in")
	if type(had) == "table" and #had > 0 then
		inSeq = had[#had].seq
		inbox = had
	end
	local lastOut = { session = "", seq = 0 }
	task.spawn(function()
		while alive do
			local ok, r = pcall(function()
				return HttpService:RequestAsync({ Url = DEV_URL .. "/poll?dm=client&nobundle=1", Method = "GET" })
			end)
			if not alive then
				return
			end
			if ok and r.Success then
				local okd, msgs = pcall(function()
					return HttpService:JSONDecode(r.Body)
				end)
				if okd and type(msgs) == "table" then
					local added = false
					for _, m in ipairs(msgs) do
						if m.type ~= "bundle" then
							inSeq += 1
							table.insert(inbox, { seq = inSeq, msg = m })
							added = true
						end
					end
					while #inbox > 50 do
						table.remove(inbox, 1)
					end
					if added then
						setSetting("AutopilotDev_in", inbox)
					end
				end
			else
				task.wait(2)
			end
		end
	end)
	task.spawn(function()
		while alive do
			local o = getSetting("AutopilotDev_out")
			if type(o) == "table" and type(o.items) == "table" then
				if o.session ~= lastOut.session then
					lastOut = { session = o.session, seq = 0 }
				end
				local batch = {}
				for _, e in ipairs(o.items) do
					if e.seq > lastOut.seq then
						lastOut.seq = e.seq
						table.insert(batch, e.msg)
					end
				end
				if #batch > 0 then
					pcall(function()
						HttpService:RequestAsync({ Url = DEV_URL .. "/up?dm=client", Method = "POST",
							Headers = { ["Content-Type"] = "application/json" }, Body = HttpService:JSONEncode(batch) })
					end)
				end
			end
			task.wait(0.25)
		end
	end)
	return {
		stop = function()
			alive = false
		end,
	}
end

return DevRelay
