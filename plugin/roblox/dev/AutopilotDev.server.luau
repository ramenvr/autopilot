--!nocheck
-- The Autopilot plugin's development loader (never shipped): put it in Studio's Plugins folder in place of the built
-- plugin (`python tools/roblox_dev.py install`), and run `python tools/roblox_dev.py serve`. In every DataModel (edit,
-- server, client) it fetches the plugin's modules from that dev server, builds them as ModuleScripts and starts the
-- plugin; when a source file changes, the dev server sends them again and the plugin restarts in place (no place
-- reopen). It also forwards the DataModel's output to the dev server, and runs code the dev server sends (`eval`): a
-- way into the edit DataModel during play. It talks HTTP (long polls), never a WebSocket: Studio lets a DataModel have
-- only four of those, other plugins' counted, and the plugin needs its own; a play session's client can make no HTTP
-- requests, so the edit DataModel's loader relays for it through the plugin's settings.
local HttpService = game:GetService("HttpService")
local LogService = game:GetService("LogService")
local RunService = game:GetService("RunService")

local DEV_URL = "http://127.0.0.1:8797"
local dm = RunService:IsEdit() and "edit" or (RunService:IsServer() and "server" or "client")
local instance = HttpService:GenerateGUID(false) -- (this DataModel's loader, as the dev server tells them apart)

local running = nil -- the started plugin's handle ({stop = fn}) or nil
local folder = nil
local alive = true
local version = ""
local outbox = {}

local function getSetting(key)
	local ok, v = pcall(function()
		return plugin:GetSetting(key)
	end)
	return if ok then v else nil
end

local function setSetting(key, v)
	pcall(function()
		plugin:SetSetting(key, v)
	end)
end


local function post(t)
	table.insert(outbox, t)
end

task.spawn(function()
	while alive do
		if #outbox > 0 and dm ~= "client" then
			local batch = outbox
			outbox = {}
			pcall(function()
				HttpService:RequestAsync({ Url = DEV_URL .. "/up?dm=" .. dm .. "&id=" .. instance, Method = "POST",
					Headers = { ["Content-Type"] = "application/json" }, Body = HttpService:JSONEncode(batch) })
			end)
		end
		task.wait(0.2)
	end
end)

local function stopPlugin()
	if running and running.stop then
		local ok, err = pcall(running.stop)
		if not ok then
			warn("[AutopilotDev] stop failed: " .. tostring(err))
		end
	end
	running = nil
	if folder then
		folder:Destroy()
		folder = nil
	end
end

local function build(modules, entry)
	stopPlugin()
	folder = Instance.new("Folder")
	folder.Name = "Autopilot"
	local byName = {}
	for name, source in pairs(modules) do
		local m = Instance.new("ModuleScript")
		m.Name = name
		m.Source = source
		byName[name] = m
	end
	for _, m in pairs(byName) do
		m.Parent = folder
	end
	local ok, mod = pcall(require, byName[entry])
	if not ok then
		warn("[AutopilotDev] require failed: " .. tostring(mod))
		post({ type = "loaded", dm = dm, ok = false, error = tostring(mod) })
		return
	end
	local ok2, handle = pcall(mod.start, plugin, { dev = true, root = folder })
	running = ok2 and handle or nil
	post({ type = "loaded", dm = dm, ok = ok2, error = (not ok2) and tostring(handle) or nil })
	if not ok2 then
		warn("[AutopilotDev] start failed: " .. tostring(handle))
	end
end

local function eval(id, code)
	local m = Instance.new("ModuleScript")
	m.Name = "AutopilotDevEval"
	m.Source = "return function(plugin, root)\n" .. code .. "\nend"
	task.spawn(function()
		local ok, fn = pcall(require, m)
		local result, err
		if ok then
			local ok2, r = pcall(fn, plugin, folder)
			if ok2 then
				result = r
			else
				err = tostring(r)
			end
		else
			err = tostring(fn)
		end
		local okj = pcall(function()
			HttpService:JSONEncode({ result })
		end)
		post({ type = "eval", id = id, dm = dm, ok = err == nil, result = okj and result or tostring(result), error = err })
		m:Destroy()
	end)
end

local logConn = LogService.MessageOut:Connect(function(message, kind)
	if not string.find(message, "AutopilotDev", 1, true) or kind ~= Enum.MessageType.MessageOutput then
		post({ type = "log", dm = dm, kind = kind.Name, text = message })
	end
end)

plugin.Unloading:Connect(function()
	alive = false
	logConn:Disconnect()
	stopPlugin()
end)

local function handle(msgs)
	for _, msg in ipairs(msgs) do
		if msg.type == "bundle" then
			version = msg.version
			build(msg.modules, msg.entry)
		elseif msg.type == "eval" then
			eval(msg.id, msg.code)
		elseif msg.type == "stop" then
			stopPlugin()
		end
	end
end

-- The client can't make HTTP requests and has no WebSocket to spare (the plugin's own needs one): it is fed through the
-- plugin's settings, which every DataModel shares. In the edit DataModel the plugin's dev relay (plugin/roblox/dev/
-- DevRelay.luau, in the dev server's bundle only) writes the bundle there (AutopilotDev_bundle) and what the dev server
-- has for the client (AutopilotDev_in: [{seq, msg}]); the client writes what it sends (AutopilotDev_out: {session,
-- items: [{seq, msg}]}), which the relay posts on. Each key has one writer.
if dm == "client" then
	local seen, outSeq, out = 0, 0, {}
	local okSeq = getSetting("AutopilotDev_in")
	if type(okSeq) == "table" and #okSeq > 0 then
		seen = okSeq[#okSeq].seq -- (what came before this session isn't ours)
	end
	task.spawn(function()
		while alive do
			local b = getSetting("AutopilotDev_bundle")
			if type(b) == "table" and b.version and b.version ~= version then
				version = b.version
				build(b.modules, b.entry)
			end
			local inbox = getSetting("AutopilotDev_in")
			if type(inbox) == "table" then
				for _, e in ipairs(inbox) do
					if e.seq > seen then
						seen = e.seq
						handle({ e.msg })
					end
				end
			end
			if #outbox > 0 then
				for _, m in ipairs(outbox) do
					outSeq += 1
					table.insert(out, { seq = outSeq, msg = m, at = os.time() })
				end
				outbox = {}
				while #out > 200 do
					table.remove(out, 1)
				end
				setSetting("AutopilotDev_out", { session = instance, items = out })
			end
			task.wait(0.25)
		end
	end)
	return
end

-- Long polls: the dev server answers when it has something (a new bundle, code to run) or after a while with nothing
task.spawn(function()
	while alive do
		local ok, r = pcall(function()
			return HttpService:RequestAsync({ Url = DEV_URL .. "/poll?dm=" .. dm .. "&id=" .. instance .. "&have="
				.. version, Method = "GET" })
		end)
		if not ok or not r.Success then
			task.wait(2)
			continue
		end
		local okd, msgs = pcall(function()
			return HttpService:JSONDecode(r.Body)
		end)
		if okd and type(msgs) == "table" then
			handle(msgs)
		end
	end
end)
