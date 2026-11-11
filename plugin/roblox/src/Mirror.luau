--!strict
-- The Autopilot dock kept in sight during a playtest. Studio stops drawing the edit DataModel's dock widget while a play
-- session runs (the dock goes on running, unseen), so the play session's client draws the same dock (Dock.luau, the
-- same modules) in a widget of its own with the same id, and the edit dock is hidden until play stops.
--
--  - The client (player 1's only, Mirror.client) makes its widget as soon as it starts and says so in the plugin's
--    settings, which every DataModel of Studio's process shares: dock_mirror {id, at, started, widget, restored}, again
--    every second while it runs, and cleared when it stops.
--  - The edit side (Mirror.edit) looks at it four times a second: a fresh one (said in the last FRESH_MS) hides the edit
--    widget (Dock:setPlayHidden), none or a stale one shows it again as it was. So the edit dock comes back however play
--    ends: stopped, the client failing, Studio's client gone without a word. A dock hidden when Studio itself went away
--    is shown again at the next start (dock_hidden).
--  - The mirror's dock is driven by a mirror editor (MirrorEditor below): it never connects to the agent service and
--    writes no setting nor keeps a run. Its state comes from the edit dock: a snapshot through the settings
--    (dock_snapshot, for its id: there with or without a connection), then, once the client joins the editor's pipe
--    (Host.luau), a snapshot with the run's latest policy steps ({"to": "client", "type": "dock_snapshot"}) and the
--    editor's events as the edit dock hears them, batched every FLUSH seconds ({"to": "client", "type": "dock",
--    "events": [{n, a}], "ed": the editor's state}).
--  - What the mirror's dock asks of its editor (Run, Run again, Stop, Steer, Not a bug) goes back to the edit side
--    ({"to": "edit", "type": "dock_action", "action": ...}), whose editor and dock do it; what follows comes back as
--    events. What makes no sense mid-play (signing in or out, the setup, Reset memory, Connect or Disconnect, the
--    settings) is disabled in the mirror's dock (Dock.mirror).
local HttpService = game:GetService("HttpService")
local Players = game:GetService("Players")
local RunService = game:GetService("RunService")

local Dock = require(script.Parent.Dock)
local Paths = require(script.Parent.Paths)
local Settings = require(script.Parent.Settings)

local Mirror = {}

Mirror.FRESH_MS = 3000 -- a mirror not heard of for this long is gone: the edit dock shows again
Mirror.FLUSH = 0.1 -- seconds between the batches of events to the mirror
Mirror.STEPS = 60 -- the run's latest policy steps in the pipe's snapshot
-- The editor's events the dock hears (Dock:_listen), forwarded to the mirror
Mirror.EVENTS = { "status", "server_status", "connection", "run", "run_started", "run_kind", "activity", "finding",
	"result", "memory", "memory_cleared", "account", "sign_in", "setup_saved", "bill", "playing", "policy", "usage" }

local function nowMs(): number
	return DateTime.now().UnixTimestampMillis
end

-- Whether a dock_mirror setting says a mirror is drawing now.
function Mirror.fresh(m: any, now: number?): boolean
	return type(m) == "table" and type(m.at) == "number" and (now or nowMs()) - m.at < Mirror.FRESH_MS
end

-- --- the edit side --------------------------------------------------------------------------------------------------

function Mirror.edit(plugin: Plugin, editor: any, dock: any): any
	local self: any = { dead = false, queue = {}, flushAt = 0, pollAt = 0, snapFor = nil, conns = {}, sent = 0 }
	local function clientReady(): boolean
		local s = editor.sides and editor.sides.client
		return s ~= nil and s.ready == true and editor:connected()
	end
	for _, name in ipairs(Mirror.EVENTS) do
		editor:on(name, function(...)
			if self.dead or not clientReady() then
				return
			end
			table.insert(self.queue, { n = name, a = { ... } })
		end)
	end
	-- (the client joined the pipe: the whole state, the run's latest policy steps with it; what was queued is in it)
	editor:on("side_ready", function(side)
		if self.dead or side ~= "client" then
			return
		end
		self.queue = {}
		editor:_pipeSend({ to = "client", type = "dock_snapshot", state = dock:snapshot(Mirror.STEPS) })
	end)
	editor:on("dock_action", function(f)
		if not self.dead then
			Mirror.act(editor, dock, f)
		end
	end)
	function self.poll()
		local m = Settings.get(plugin, "dock_mirror")
		if Mirror.fresh(m) then
			if m.id ~= self.snapFor then
				-- (a new mirror: its first state, through the settings)
				self.snapFor = m.id
				self.seenAt = nowMs()
				Settings.set(plugin, "dock_snapshot", { id = m.id, state = dock:snapshot(0) })
			end
			dock:setPlayHidden(true)
		elseif dock.playHidden then
			dock:setPlayHidden(false)
		elseif Settings.get(plugin, "dock_hidden") ~= nil then
			-- (hidden for a playtest when Studio went away: shown again)
			dock.playHidden = true
			dock:setPlayHidden(false)
		end
	end
	self.poll()
	table.insert(self.conns, RunService.Heartbeat:Connect(function()
		local now = os.clock()
		if #self.queue > 0 and now - self.flushAt >= Mirror.FLUSH then
			self.flushAt = now
			local batch = self.queue
			self.queue = {}
			self.sent += #batch
			editor:_pipeSend({ to = "client", type = "dock", events = batch, ed = Dock.editorState(editor) })
		end
		if now - self.pollAt >= 0.25 then
			self.pollAt = now
			local ok, err = pcall(self.poll)
			if not ok then
				warn("[Autopilot] the dock's playtest hand-off: " .. tostring(err))
			end
		end
	end))
	function self.stop()
		self.dead = true
		for _, c in ipairs(self.conns) do
			c:Disconnect()
		end
		pcall(function()
			dock:setPlayHidden(false)
		end)
	end
	return self
end

-- An action of the mirror's dock, done by the edit side's editor and dock.
function Mirror.act(editor: any, dock: any, f: any)
	local a = tostring(f.action or "")
	if a == "start" and type(f.run) == "table" then
		dock:startRun(f.run, tostring(f.prompt or ""))
	elseif a == "stop" then
		dock:stopRun()
	elseif a == "steer" then
		local text = tostring(f.text or "")
		if editor:canSteer() and text ~= "" then
			local redirect = f.redirect == true and editor:canRedirect()
			editor:steer(text, redirect)
			if dock.active then
				dock.active:addFeed((if redirect then "You (redirect): " else "You: ") .. text, "status")
				dock:liveChanged()
			end
		end
	elseif a == "feedback" then
		local sig = tostring(f.signature or "")
		editor:feedback(sig, tostring(f.verdict or "not_a_bug"))
		for _, r in ipairs(dock.runs) do
			for _, g in ipairs(r.findings) do
				if tostring(g.signature or "") == sig then
					r.notABug[sig] = true
				end
			end
		end
		dock:save()
	end
end

-- --- the client side: the mirror editor -----------------------------------------------------------------------------

local MirrorEditor = {}
MirrorEditor.__index = MirrorEditor

function MirrorEditor.new(mirror: any): any
	local self = setmetatable({}, MirrorEditor) :: any
	self.mirror = mirror
	self.listeners = {}
	self.link = { url = "", problem = "", want = true, refused = false, connected = false, role = "" }
	self.account = {}
	self.memory = { known = false, runs = 0, notes = false, lib = false }
	self.detectors = true
	self.runActive = false
	self.starting = false
	self.playing = true
	self.brainAccount = false
	self.brainSetup = false
	self.brainSteers = false
	self.brainSteerModes = false
	self.sides = {}
	return self
end

function MirrorEditor:on(name: string, fn: (...any) -> ())
	self.listeners[name] = self.listeners[name] or {}
	table.insert(self.listeners[name], fn)
end

function MirrorEditor:_fire(name: string, ...: any)
	for _, fn in ipairs(self.listeners[name] or {}) do
		local ok, err = pcall(fn, ...)
		if not ok then
			warn("[Autopilot] mirror " .. name .. ": " .. tostring(err))
		end
	end
end

function MirrorEditor:status(text: string)
	self:_fire("status", text)
end

-- The edit side's editor state (Dock.editorState).
function MirrorEditor:applyState(s: any)
	if type(s) ~= "table" then
		return
	end
	self.link.connected = s.connected == true
	self.link.role = if s.connected then "service" else ""
	self.link.url = tostring(s.url or "")
	self.link.problem = tostring(s.problem or "")
	self.link.want = s.want ~= false
	self.link.refused = s.refused == true
	self.runActive = s.runActive == true
	self.starting = s.starting == true
	self.playing = s.playing ~= false
	self.brainAccount = s.brainAccount == true
	self.brainSetup = s.brainSetup == true
	self.brainSteers = s.brainSteers == true
	self.brainSteerModes = s.brainSteerModes == true
	if type(s.memory) == "table" then
		self.memory = { known = s.memory.known == true, runs = tonumber(s.memory.runs) or 0, notes = s.memory.notes == true,
			lib = s.memory.lib == true }
	end
	self.detectors = s.detectors ~= false
end

function MirrorEditor:connected(): boolean
	return self.link.connected and self.link.role ~= ""
end

function MirrorEditor:projectId(): string?
	if game.GameId ~= 0 then
		return "roblox-universe-" .. tostring(game.GameId)
	end
	return nil
end

function MirrorEditor:projectName(): string
	return (string.gsub(game.Name, "%.rbxlx?$", ""))
end

-- An action for the edit side (false when the client isn't on the editor's pipe).
function MirrorEditor:_act(frame: any): boolean
	local host = self.mirror.host
	if host == nil or host.link == nil or not host.attached then
		self:status("Not connected: connect first")
		return false
	end
	frame.to = "edit"
	frame.type = "dock_action"
	host:send(frame)
	return true
end

function MirrorEditor:startRun(run: any): boolean
	return self:_act({ action = "start", run = run, prompt = run.prompt or run.claim or run.goal or "" })
end

function MirrorEditor:stopRun()
	self:_act({ action = "stop" })
end

function MirrorEditor:canSteer(): boolean
	return self:connected() and self.runActive and self.brainSteers
end

function MirrorEditor:canRedirect(): boolean
	return self:canSteer() and self.brainSteerModes
end

function MirrorEditor:steer(text: string, redirect: boolean?)
	self:_act({ action = "steer", text = text, redirect = redirect == true })
end

function MirrorEditor:feedback(signature: string, verdict: string)
	self:_act({ action = "feedback", signature = signature, verdict = verdict })
end

-- (what makes no sense mid-play: nothing; the dock disables it)
function MirrorEditor:connect() end
function MirrorEditor:disconnect() end
function MirrorEditor:clearMemory() end
function MirrorEditor:askAccount() end
function MirrorEditor:helloChanged() end
function MirrorEditor:signInCancel() end
function MirrorEditor:signOut() end
function MirrorEditor:signIn(_anotherAccount: boolean?): boolean
	return false
end
function MirrorEditor:saveSetup(): boolean
	return false
end
function MirrorEditor:setSharing(): boolean
	return false
end
function MirrorEditor:setDetectors(_on: boolean): boolean
	return false
end

Mirror.MirrorEditor = MirrorEditor

-- --- the client side ------------------------------------------------------------------------------------------------

-- The client's widget, kept for the plugin's life (a restart of the dev loader's code makes no second one: Studio keeps
-- a widget's id taken).
local function widgetOf(plugin: Plugin, open: boolean): DockWidgetPluginGui?
	local kept = (_G :: any).AutopilotMirrorKept
	if kept == nil then
		kept = setmetatable({}, { __mode = "k" })
		;(_G :: any).AutopilotMirrorKept = kept
	end
	if kept[plugin] then
		return kept[plugin]
	end
	-- (the edit dock's id and place: Studio puts it where that dock is in its play layout)
	local info = DockWidgetPluginGuiInfo.new(Enum.InitialDockState.Right, open, true, 420, 640, 300, 360)
	local ok, w = pcall(function()
		return plugin:CreateDockWidgetPluginGuiAsync("AutopilotDock", info)
	end)
	if not ok then
		warn("[Autopilot] the dock can't be drawn during play: " .. tostring(w))
		return nil
	end
	kept[plugin] = w
	return w
end

-- The mirror in a play session's client: {stop, attach(host)}. Draws only in player 1's client.
function Mirror.client(plugin: Plugin): any
	local started = nowMs()
	local self: any = { conns = {}, alive = true, stats = { batches = 0, events = 0, ms = 0, maxMs = 0 } }
	local function none()
		self.stop = function() end
		self.attach = function() end
		return self
	end
	local me = Players.LocalPlayer
	local waited = 0
	while me == nil and waited < 10 do
		waited += task.wait(0.03)
		me = Players.LocalPlayer
	end
	if me == nil or Paths.playerIndex(me) ~= 1 then
		return none() -- (another player of a multiplayer test: player 1's client draws it)
	end
	local widget = widgetOf(plugin, Settings.get(plugin, "dock_open") ~= false)
	if widget == nil then
		return none()
	end
	widget.Title = "Autopilot"
	widget.Name = "AutopilotDock"
	for _, c in ipairs(widget:GetChildren()) do
		c:Destroy()
	end
	self.widget = widget
	self.id = HttpService:GenerateGUID(false)
	self.started = started
	self.widgetAt = nowMs()
	local restored = false
	pcall(function()
		restored = (widget :: any).HostWidgetWasRestored
	end)
	local function beat()
		Settings.set(plugin, "dock_mirror", { id = self.id, at = nowMs(), started = started, widget = self.widgetAt,
			restored = restored, place = game.Name })
	end
	beat()
	local editor = MirrorEditor.new(self)
	self.editor = editor
	self.dock = Dock.new(plugin, editor, { mount = widget, mirror = true })
	function self.apply(state: any)
		if type(state) ~= "table" then
			return
		end
		editor:applyState(state.ed)
		editor.account = if type(state.account) == "table" then state.account else {}
		self.dock:applySnapshot(state)
		self.applied = nowMs()
	end
	-- (the first state, through the settings: there with or without a connection)
	task.spawn(function()
		for _ = 1, 100 do
			if not self.alive then
				return
			end
			local s = Settings.get(plugin, "dock_snapshot")
			if type(s) == "table" and s.id == self.id then
				self.apply(s.state)
				return
			end
			task.wait(0.05)
		end
	end)
	local beatAt = os.clock()
	table.insert(self.conns, RunService.Heartbeat:Connect(function()
		if os.clock() - beatAt >= 1 then
			beatAt = os.clock()
			beat()
		end
	end))
	-- The editor's pipe, once the client is on it: its snapshot and its events.
	function self.attach(host: any)
		if host == nil then
			return
		end
		self.host = host
		host.onDock = function(frame: any)
			local t0 = os.clock()
			if frame.type == "dock_snapshot" then
				self.apply(frame.state)
			elseif frame.type == "dock" then
				editor:applyState(frame.ed)
				for _, ev in ipairs(if type(frame.events) == "table" then frame.events else {}) do
					local args = if type(ev.a) == "table" then ev.a else {}
					if ev.n == "account" and type(args[1]) == "table" then
						editor.account = args[1]
					end
					editor:_fire(tostring(ev.n), table.unpack(args))
				end
				self.stats.batches += 1
				self.stats.events += #(frame.events or {})
			end
			local ms = (os.clock() - t0) * 1000
			self.stats.ms += ms
			self.stats.maxMs = math.max(self.stats.maxMs, ms)
		end
	end
	function self.stop()
		self.alive = false
		for _, c in ipairs(self.conns) do
			c:Disconnect()
		end
		if self.host then
			self.host.onDock = nil
		end
		pcall(function()
			self.dock:stop()
		end)
		-- (gone: the edit dock shows again at once)
		local m = Settings.get(plugin, "dock_mirror")
		if type(m) == "table" and m.id == self.id then
			Settings.set(plugin, "dock_mirror", nil)
		end
	end
	;(_G :: any).AutopilotMirror = self -- (a look inside, for the dev loader's eval)
	return self
end

return Mirror
