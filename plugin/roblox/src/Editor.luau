--!strict
-- The editor's link to the brain (hello host "editor"): the plugin in Studio's edit DataModel holds the one WebSocket
-- to the agent service, as Godot's editor does, because a play session's client and server are other DataModels that
-- come and go with each Play:
--  - `session` start / stop / restart play the place (StudioTestService:ExecutePlayModeAsync, as Studio's Play button
--    does: a server and one player's client) and end it (the server's StudioTestService:EndTest); with `players` above
--    1, a multiplayer test as Studio's Server & Clients test plays it (ExecuteMultiplayerTestAsync: the server and each
--    player's client are Studio processes of their own, each with the plugin);
--  - the players: Autopilot plays player 1 (its client answers as "client"); a request with `player` n goes to player
--    n's client ("client<n>": its view, its keys...), and its rc's LocalPlayer, Character and Humanoid are player n's:
--    one brain with several bodies (kit: game.player(n));
--  - `levels`, `codex_manifest` and `codex_read` are answered here, from the place open in Studio (Codex.luau);
--  - every other request goes to a side of the play session over the pipe (Host.luau): `rc` to the server unless its
--    path is the client's (the player's PlayerGui, PlayerScripts, camera; Route below), the player's ops (view, key,
--    move, ui...) to the client; their answers and events come back the same way and go on to the brain;
--  - with no play session, `context`, `ping`, `screen` (no texts), `ui` (no elements) and `timestep` are answered here
--    (session_running false), the rest get 409;
--  - `device` switches the device Studio simulates (Device.luau: a phone or tablet makes the game a touch device) here
--    with no play session, in its client during one; a session's start takes `device` and `orientation` too, and the
--    user's own device comes back when the run ends;
--  - `record` and `capture` go to the client when Studio gives the plugin pictures (StudioCaptureService: Media.luau,
--    the hello then says "media"; a recording asked for between play sessions starts with the next one); else they get
--    409, and the agent service answers them itself from Studio's MCP server (autopilot/studio_mcp.py).
-- The dock (Dock.luau) drives runs through it (start, stop, steer, clear_memory, the account) and shows what comes.
local RunService = game:GetService("RunService")
local StudioTestService = game:GetService("StudioTestService")

local Codex = require(script.Parent.Codex)
local Device = require(script.Parent.Device)
local Json = require(script.Parent.Json)
local Link = require(script.Parent.Link)
local Media = require(script.Parent.Media)
local Paths = require(script.Parent.Paths)
local Settings = require(script.Parent.Settings)
local Ui = require(script.Parent.Ui)

local Editor = {}
Editor.__index = Editor

Editor.VERSION = "1.0.0"
local SESSION_TIMEOUT = 60
local PLAYERS_TIMEOUT = 40 -- seconds for a multiplayer test's other players to join (then asked for again, once)
local MAX_PLAYERS = 7 -- (the pipe takes 8 parts: the server and 7 clients)
local Host = require(script.Parent.Host)

-- Which side of the play session an rc request goes to: "server", "client", or "server" with "client" if the server
-- has no such instance (fallback). A path's "server:" / "client:" says so itself.
function Editor.route(path: string): (string, boolean)
	local side, p = Paths.side(path)
	if side then
		return side, false
	end
	local head = string.match(p, "^/?([^/%.]+)") or ""
	if head == "LocalPlayer" or head == "Camera" or head == "Character" or head == "Humanoid" then
		if head == "Camera" then
			return "client", false
		end
	end
	if head == "ServerScriptService" or head == "ServerStorage" then
		return "server", false
	end
	if string.find(p, "/PlayerGui", 1, true) or string.find(p, "/PlayerScripts", 1, true)
		or string.find(p, "%.PlayerGui") or string.find(p, "^/?Workspace/Camera")
		or string.find(p, "^/?LocalPlayer/PlayerGui") then
		return "client", false
	end
	return "server", true
end

local function now(): number
	return os.clock()
end

function Editor.new(plugin: Plugin)
	local self = setmetatable({}, Editor) :: any
	self.plugin = plugin
	self.link = Link.new(Settings.get(plugin, "url"), Settings.get(plugin, "token"), Settings.get(plugin, "http_url"))
	self.session = nil -- the brain's session for this connection: the pipe's key
	self.runActive = false
	self.brainSteers = false
	self.brainAccount = false
	self.brainSetup = false
	self.privacyReady = false
	self.memory = { known = false, runs = 0, notes = false, lib = false }
	self.account = {}
	self.sides = {} -- side -> {ready = bool, seen = os.clock()}
	self.playing = false -- a play session runs (its client is attached)
	self.starting = false -- we asked Studio to play and it hasn't ended
	self.joining = false -- a multiplayer test's other players are being waited for
	self.forwarded = {} -- brain request id -> when it went to the play session
	self.detectorConfig = {}
	self.media = false -- Studio gives the plugin pictures (StudioCaptureService), known a moment after the start
	self.recordArgs = nil -- a recording the brain asked for (started in each play session's client)
	self.listeners = {} -- event name -> {fn}
	self.alive = true
	self.connection = 0
	self.heartbeatAt = 0
	self.link.onOpen = function()
		self:_hello()
	end
	self.link.onFrame = function(frame)
		self:_onBrainFrame(frame)
	end
	self.link.onClose = function(was)
		self:_onClosed(was)
	end
	self.heartbeat = RunService.Heartbeat:Connect(function()
		self:_tick()
	end)
	Device.init(plugin)
	task.spawn(function()
		self.media = Media.available() -- (may ask the user once: not in the hello's way; a reconnect says it)
	end)
	return self
end

-- --- events for the dock -------------------------------------------------------------------------------------------

function Editor:on(name: string, fn: (...any) -> ())
	self.listeners[name] = self.listeners[name] or {}
	table.insert(self.listeners[name], fn)
end

function Editor:_fire(name: string, ...: any)
	for _, fn in ipairs(self.listeners[name] or {}) do
		local ok, err = pcall(fn, ...)
		if not ok then
			warn("[Autopilot] " .. name .. " listener failed: " .. tostring(err))
		end
	end
end

function Editor:status(text: string)
	self:_fire("status", text)
end

-- --- the connection ------------------------------------------------------------------------------------------------

function Editor:connected(): boolean
	return self.link.connected and self.link.role ~= ""
end

function Editor:connect()
	self.privacyReady = false
	self.link:reconnect(Settings.get(self.plugin, "url"), Settings.get(self.plugin, "token"),
		Settings.get(self.plugin, "http_url"))
	self:status("Connecting to " .. self.link.url .. "…")
end

function Editor:disconnect()
	self.privacyReady = false
	self.link:close()
	self.link.problem = ""
	self.runActive = false
	self:_endPipe()
	self:status("Disconnected")
	self:_fire("connection", false)
	self:_fire("run", false)
end

function Editor:projectId(): string?
	if game.GameId ~= 0 then
		return "roblox-universe-" .. tostring(game.GameId)
	end
	return nil
end

function Editor:projectName(): string
	local name = game.Name
	name = string.gsub(name, "%.rbxlx?$", "")
	return name
end

function Editor:_hello()
	local h: any = { type = "hello", protocol = Link.PROTOCOL, plugin_version = Editor.VERSION, engine = "roblox",
		engine_version = version(), project = self:projectName(), host = "editor",
		inspect = Settings.get(self.plugin, "show_policy_log") ~= false,
		-- `levels`, `timed_hold`, `pointer`, `touch`, `gamepad`, `device`, `players` (multiplayer), `trace`; `media`
		-- when Studio gives the plugin pictures
		features = { "levels", "timed_hold", "pointer", "touch", "gamepad", "device", "players", "trace" },
		-- (how input reaches the games it plays, as the other plugins' hellos say: the client's context says it for
		-- the game playing)
		input = Ui.inputCaps({ playing = false }),
		share = self.privacyReady and Settings.get(self.plugin, "share") ~= false,
		place_id = game.PlaceId, universe_id = game.GameId, transport = self.link.transport }
	if self.media then
		table.insert(h.features, "media")
	end
	local pid = self:projectId()
	if pid then
		h.project_id = pid
	end
	-- whether the agent service runs its research agent, only once the user chose (the dock's Research agent switch);
	-- left out, the agent service decides (off for the Aura app's, on for its own)
	local research = Settings.get(self.plugin, "research")
	if type(research) == "boolean" then
		h.research = research
	end
	if self.activitySince then
		local since = self.activitySince()
		if since and since >= 0 then
			h.activity_since = since
		end
	end
	self.link:send(h)
end

function Editor:_send(frame: any)
	self.link:send(frame)
end

function Editor:_respond(id: any, result: any)
	self:_send({ type = "response", id = id, ok = true, result = if result == nil then Json.object() else result,
		t = Json.null })
end

function Editor:_respondError(id: any, err: string, status: number?)
	self:_send({ type = "response", id = id, ok = false, status = status or 400, error = err, t = Json.null })
end

function Editor:_onClosed(was: boolean)
	self.privacyReady = false
	self.recordArgs = nil
	self:_restoreDevice()
	self.runActive = false
	self:_fire("run", false)
	self:_endPipe()
	self.memory.known = false
	self:_fire("memory")
	if was then
		self:_fire("connection", false)
	end
	self:status(self.link.problem)
end

-- --- runs (the dock's) ---------------------------------------------------------------------------------------------

function Editor:startRun(run: any): boolean
	if not self:connected() then
		self:status("Not connected: connect first")
		return false
	end
	if not self.privacyReady then
		self:status("Waiting for the agent service’s privacy settings; update the service if this persists")
		return false
	end
	self:_send({ type = "start", run = run })
	self.runActive = true
	self:_fire("run", true)
	self:status("Run started")
	return true
end

function Editor:stopRun()
	self:_send({ type = "stop" })
	self:status("Stopping…")
end

function Editor:canSteer(): boolean
	return self:connected() and self.runActive and self.brainSteers
end

function Editor:steer(text: string)
	local said = string.gsub(text, "^%s+", "")
	said = string.gsub(said, "%s+$", "")
	if self:canSteer() and said ~= "" then
		self:_send({ type = "steer", text = said })
	end
end

function Editor:clearMemory()
	if not self:connected() then
		self:status("Not connected: connect first")
		return
	end
	if self.runActive then
		self:status("A run is going: reset the memory once it is done")
		return
	end
	self:_send({ type = "clear_memory" })
	self:status("Resetting memory…")
end

function Editor:feedback(signature: string, verdict: string)
	self:_send({ type = "feedback", signature = signature, verdict = verdict })
end

function Editor:askAccount()
	if self:connected() and self.brainAccount then
		self:_send({ type = "account" })
	end
end

-- anotherAccount: "Use another account", Aura's page asked to let the user pick the account (an older agent service
-- ignores it).
function Editor:signIn(anotherAccount: boolean?): boolean
	if not (self:connected() and self.brainAccount) then
		return false
	end
	if anotherAccount then
		return self.link:send({ type = "aura_sign_in", another_account = true })
	end
	return self.link:send({ type = "aura_sign_in" })
end

function Editor:signInCancel()
	self:_send({ type = "aura_sign_in_cancel" })
end

function Editor:signOut()
	self:_send({ type = "aura_sign_out" })
end

-- The first-run setup's choices, for an agent service that saves them for the plugin (its welcome's "setup"):
-- {"access": "aura"|"own_keys", "share", "keys": {...}, "coding_agent", "llm_provider"}.
function Editor:saveSetup(setup: any): boolean
	if not (self:connected() and self.brainSetup and self.privacyReady) then
		return false
	end
	setup.type = "save_setup"
	return self.link:send(setup)
end

function Editor:setSharing(share: boolean): boolean
	if not (self:connected() and self.privacyReady) then
		self:status("Connect to synchronize privacy settings first")
		return false
	end
	return self.link:send({ type = "save_preferences", share = share })
end

-- A dock setting the hello carries changed (Show Policy Log's `inspect`, Research agent's `research`): the hello again,
-- as on a privacy change, without interrupting a run (`inspect` applies at once, `research` from the next run).
function Editor:helloChanged()
	if self:connected() then
		self:_hello()
	end
end

-- --- frames from the brain -----------------------------------------------------------------------------------------

function Editor:_onBrainFrame(frame: any)
	local kind = tostring(frame.type or "")
	if kind == "request" then
		self:_onRequest(frame)
	elseif kind == "welcome" then
		self.link.role = tostring(frame.role or "")
		self.connection += 1
		self.brainSteers, self.brainAccount, self.brainSetup = false, false, false
		for _, f in ipairs(frame.features or {}) do
			if f == "steer" then
				self.brainSteers = true
			elseif f == "account" then
				self.brainAccount = true
			elseif f == "setup" then
				self.brainSetup = true
			end
		end
		if self.link.role == "cli" then
			self.runActive = true
			self:_fire("run", true)
		end
		self:_startPipe(tostring(frame.session or ""))
		self:status("Connected to the agent service")
		self:_fire("connection", true)
		self:askAccount()
	elseif kind == "pipe" then
		self:_onPipeFrame(frame.frame)
	elseif kind == "run_started" then
		if not self.runActive and type(frame.run) == "table" then
			self.runActive = true
			self:_fire("run_started", frame.run)
			self:_fire("run", true)
		end
	elseif kind == "run_kind" then
		self:_fire("run_kind", tostring(frame.kind or ""), frame.tested == true)
	elseif kind == "status" then
		self:_fire("server_status", tostring(frame.text or ""))
		self:status(tostring(frame.text or ""))
	elseif kind == "activity" then
		self:_fire("activity", frame)
	elseif kind == "jev" then
		self:_fire("policy", frame)
	elseif kind == "finding" then
		if type(frame.finding) == "table" then
			self:_fire("finding", frame.finding)
			self:status("Finding: " .. tostring(frame.finding.summary or ""))
		end
	elseif kind == "result" then
		self.runActive = false
		local result = if type(frame.result) == "table" then frame.result else {}
		for _, k in ipairs({ "memory", "recording", "no_recording" }) do
			if frame[k] ~= nil and result[k] == nil then
				result[k] = frame[k]
			end
		end
		self:_fire("run", false)
		self:_fire("result", result, frame.files)
		self:status("Run finished")
		self:_restoreDevice()
	elseif kind == "memory_state" then
		self.memory = { known = true, runs = tonumber(frame.runs) or 0, notes = frame.notes == true, lib = frame.lib == true }
		self:_fire("memory")
	elseif kind == "memory_cleared" then
		if frame.ok == true then
			self.memory = { known = true, runs = 0, notes = false, lib = false }
			self:status("Memory reset successful")
			self:_fire("memory")
		else
			self:status("Memory not reset: " .. tostring(frame.message or ""))
		end
		self:_fire("memory_cleared", frame.ok == true, tostring(frame.message or ""))
	elseif kind == "usage" then
		self:_fire("usage", frame.models or {}, frame.final == true)
	elseif kind == "bill" then
		self:_fire("bill", frame)
	elseif kind == "account" then
		self.account = frame
		if type(frame.share_data) == "boolean" then
			local changed = not self.privacyReady or Settings.get(self.plugin, "share") ~= frame.share_data
			Settings.set(self.plugin, "share", frame.share_data)
			self.privacyReady = true
			if changed then
				self:_hello() -- update the hello without interrupting an active run
			end
		end
		self:_fire("account", frame)
	elseif kind == "aura_sign_in" then
		self.signInFrame = frame -- (the latest: what the dock shows, and what is asked again)
		self:_fire("sign_in", frame)
	elseif kind == "preferences_saved" then
		if not frame.ok then
			self:status("Privacy setting not saved: " .. tostring(frame.message or ""))
		end
	elseif kind == "setup_saved" then
		self:_fire("setup_saved", frame)
	elseif kind == "error" then
		self.link.problem = tostring(frame.message or "")
		self.link.refused = true
		self:status(self.link.problem)
		self:_fire("connection", false)
	end
end

-- --- requests ------------------------------------------------------------------------------------------------------

function Editor:_editorContext(): any
	return { world = "", scene = game.Name, character = "", controller = "", input_subsystem = "", camera_manager = "",
		camera = "", game_name = game.Name, host = "editor", engine = "roblox", session_running = false, paused = false,
		players = {}, you = 1,
		game_time = 0, project_dir = "", controls = {}, place_id = game.PlaceId, universe_id = game.GameId,
		input = Ui.inputCaps({ playing = false }), net = { mode = "standalone" } }
end

function Editor:_levels(): any
	local name = game.Name
	return { levels = { name }, open = name, playing = if self.playing then name else "" }
end

-- Send a brain request to a side of the play session (the answer comes back on the pipe: _onPipeFrame).
-- Send a brain request to a side of the play session: the side answers the brain itself. fallback: the server passes
-- an rc request for an instance it hasn't on to the client.
function Editor:_forward(frame: any, side: string, fallback: boolean)
	self.forwarded[frame.id] = now()
	self:_pipeSend({ to = side, type = "request", id = frame.id, op = frame.op, args = frame.args or Json.object(),
		fallback = fallback or nil })
end

function Editor:_onRequest(frame: any)
	local id = frame.id
	local op = tostring(frame.op or "")
	local args = if type(frame.args) == "table" then frame.args else {}
	if op == "session" then
		task.spawn(function()
			self:_session(id, args)
		end)
		return
	elseif op == "levels" then
		self:_respond(id, self:_levels())
		return
	elseif op == "codex_manifest" or op == "codex_read" then
		task.spawn(function()
			local ok, result = pcall(function()
				return if op == "codex_manifest" then Codex.manifest(args) else Codex.read(args)
			end)
			if ok then
				self:_respond(id, result)
			else
				self:_respondError(id, tostring(result), 500)
			end
		end)
		return
	elseif op == "record" then
		local stop = tostring(args.action or "") == "stop"
		if not self.media then
			if stop then
				self:_respond(id, { recording = false, frames = 0 })
			else
				self:_respondError(id, "no picture: this Studio gives a plugin no picture of the game", 409)
			end
			return
		end
		self.recordArgs = if stop then nil else args
		if self.playing then
			self:_forward(frame, "client", false)
		else
			self:_respond(id, if stop then { recording = false, frames = 0 } else { recording = true, waiting = true })
		end
		return
	elseif op == "capture" then
		if not self.media then
			self:_respondError(id, "no picture: this Studio gives a plugin no picture of the game", 409)
		elseif not self.playing then
			self:_respondError(id, "no picture: no game is running (start one with session start)", 409)
		else
			self:_forward(frame, "client", false)
		end
		return
	elseif op == "device" and not self.playing then
		task.spawn(function()
			local ok, a, b = Device.handle(args)
			if ok then
				self:_respond(id, a)
			else
				self:_respondError(id, tostring(a), b)
			end
		end)
		return
	elseif op == "detectors" then
		self.detectorConfig = args
		if self.playing then
			for side in pairs(self.sides) do
				self:_pipeSend({ to = side, type = "request", id = "detectors-" .. side, op = "detectors", args = args })
			end
		end
		self:_respond(id, Json.object())
		return
	end
	if not self.playing then
		if op == "context" then
			self:_respond(id, self:_editorContext())
		elseif op == "ping" then
			self:_respond(id, { game_time = Json.null, wall_time = DateTime.now().UnixTimestampMillis / 1000 })
		elseif op == "pause" then
			self:_respond(id, { paused = false })
		elseif op == "screen" then
			-- (no game: nothing on screen, as the other plugins answer)
			self:_respond(id, { texts = Json.object() })
		elseif op == "ui" then
			self:_respond(id, { elements = {} })
		elseif op == "timestep" then
			self:_respond(id, Json.object())
		else
			self:_respondError(id, "No game is running (start one with session start)", 409)
		end
		return
	end
	-- (player n's client: the player a request is for, 1 when it says none)
	local n = math.floor(tonumber(args.player) or 1)
	local client = Host.clientSide(n)
	if n > 1 and self.sides[client] == nil then
		self:_respondError(id, "No player " .. tostring(n) .. " in the game: " .. self:_playersSaid(), 404)
		return
	end
	if op == "rc" then
		local body = if type(args.body) == "table" then args.body else {}
		if tostring(args.verb or "") == "probe" then
			self:_respond(id, Json.object())
			return
		end
		local side, fallback = Editor.route(tostring(body.objectPath or ""))
		self:_forward(frame, if side == "client" then client else side, fallback)
	elseif op == "pause" then
		-- (each side runs its own simulation: the server's pause leaves the player's character moving)
		for side in pairs(self.sides) do
			if side ~= "client" then
				self:_pipeSend({ to = side, type = "request", id = "pause-" .. side, op = "pause", args = args })
			end
		end
		self:_forward(frame, "client", false)
	else
		self:_forward(frame, client, false)
	end
end

-- The players there are, said for an error.
function Editor:_playersSaid(): string
	local n = 0
	for side in pairs(self.sides) do
		if string.sub(side, 1, 6) == "client" then
			n += 1
		end
	end
	return string.format("%d player%s (start a session with \"players\": %d for more)", n, if n == 1 then "" else "s",
		n + 1)
end

-- --- the play session ----------------------------------------------------------------------------------------------

function Editor:_waitFor(cond: () -> boolean, seconds: number): boolean
	local t0 = now()
	while not cond() do
		if now() - t0 > seconds then
			return false
		end
		task.wait(0.1)
	end
	return true
end

-- Play the place, as Studio's Play button does; returns once the client is attached (or why not).
-- How many players' clients are attached.
function Editor:_clientsHere(): number
	local n = 0
	for side, s in pairs(self.sides) do
		if string.sub(side, 1, 6) == "client" and s.ready then
			n += 1
		end
	end
	return n
end

-- Play the place, as Studio's Play button does (one player) or its Server & Clients test (players > 1); returns once
-- player 1's client is attached, and the others' (or why not).
function Editor:play(players: number?): (boolean, string?)
	local want = math.clamp(math.floor(players or 1), 1, MAX_PLAYERS)
	if self.playing then
		return true, nil
	end
	self.playError = nil
	if not self.starting then
		self.starting = true
		task.spawn(function()
			local pipe = Settings.pipe(self.plugin)
			local testArgs: any = { autopilot = if pipe then { url = pipe.url, key = pipe.key, http_url = pipe.http_url,
				players = want } else nil }
			local ok, err = pcall(function()
				if want > 1 then
					return StudioTestService:ExecuteMultiplayerTestAsync(want, testArgs)
				end
				return StudioTestService:ExecutePlayModeAsync(testArgs)
			end)
			self.starting = false
			if not ok then
				if string.find(tostring(err), "in progress", 1, true) then
					-- (a play session runs already, one the user started or from before this connection: its sides
					-- join this connection's pipe by themselves)
					self:_pipeSend({ to = "play", type = "who" })
					return
				end
				self.playError = tostring(err)
				warn("[Autopilot] couldn't play: " .. tostring(err))
			end
			self:_playEnded()
		end)
	end
	local ok = self:_waitFor(function()
		return self.playing or self.playError ~= nil
	end, SESSION_TIMEOUT)
	if self.playError then
		return false, self.playError
	end
	if not ok then
		return false, "Timed out waiting for the play session"
	end
	if want > 1 then
		-- (Studio starts each player's client in a process of its own, one after another: a client that doesn't come
		-- is asked for again, once, through the server)
		self.joining = true
		local all = self:_waitFor(function()
			return self:_clientsHere() >= want
		end, PLAYERS_TIMEOUT)
		if not all and self.playing then
			self:_pipeSend({ to = "server", type = "request", id = "add-players", op = "add_players",
				args = { count = want - self:_clientsHere() } })
			all = self:_waitFor(function()
				return self:_clientsHere() >= want
			end, PLAYERS_TIMEOUT)
		end
		self.joining = false
		if not all then
			return false, string.format("Only %d of %d players joined the multiplayer test", self:_clientsHere(), want)
		end
	end
	return true, nil
end

-- Whether a play session is starting and not ready yet (session status `loading`: the agent service waits it out):
-- asked for and its client not attached, a side attached and player 1's client not ready, or a multiplayer test's
-- other players still joining. (self.starting stays true for the whole play session: not alone.)
function Editor:loading(): boolean
	if self.joining then
		return true
	end
	if self.playing then
		return false
	end
	if self.starting then
		return true
	end
	local t = now()
	for _, s in pairs(self.sides) do
		if t - s.seen < 8 then -- (a side that went quiet is a play session's leftover, not one starting)
			return true
		end
	end
	return false
end

-- End the play session (its server ends the test); returns once it is gone.
function Editor:stopPlaying(): (boolean, string?)
	if not self.playing and not self.starting then
		return true, nil
	end
	-- (asked again every 2 s: a play session from before this connection has its server join this pipe only after
	-- a second or two, and a request sent before then reaches no one)
	local askedAt = -math.huge
	local ok = self:_waitFor(function()
		if now() - askedAt >= 2 then
			askedAt = now()
			self:_pipeSend({ to = "server", type = "request", id = "end_test", op = "end_test", args = Json.object() })
		end
		return not self.playing and not self.starting
	end, 30)
	if not ok then
		return false, "Timed out waiting for the play session to stop (stop it with Studio's Stop button)"
	end
	return true, nil
end

-- The device a session starts on (its `device` and `orientation`): set while no game plays. Returns why not, or nil.
function Editor:_startDevice(args: any): string?
	if args.device == nil and args.orientation == nil then
		return nil
	end
	local ok, why = Device.set(tostring(args.device or Device.describe().id),
		if args.orientation then tostring(args.orientation) else nil)
	return if ok then nil else tostring(why)
end

-- The user's own device back (Device.restore), by the client while a game plays.
function Editor:_restoreDevice()
	if Settings.get(self.plugin, "device_before") == nil then
		return
	end
	if self.playing and self.link.connected then
		self:_pipeSend({ to = "client", type = "request", id = "device-restore", op = "device",
			args = { action = "restore" } })
	elseif self.playing then
		self.restorePending = true -- (no way to the client now: once its play session ends)
	else
		task.spawn(Device.restore)
	end
end

function Editor:_session(id: any, args: any)
	local action = tostring(args.action or "status")
	local map = tostring(args.map or "")
	if map ~= "" and map ~= game.Name and map ~= self:projectName() then
		-- (before anything changes)
		self:_respondError(id, "No level " .. map .. ": a Roblox place is one level (" .. game.Name .. ")", 404)
		return
	end
	if args.players ~= nil then
		self.players = math.clamp(math.floor(tonumber(args.players) or 1), 1, MAX_PLAYERS)
	end
	local players = self.players or 1
	if players > 1 and args.device ~= nil and tostring(args.device) ~= "desktop" then
		self:_respondError(id, "A device is for a game of one player (a multiplayer test's players are Studio "
			.. "windows of their own)", 400)
		return
	end
	if action == "start" and self.playing and (args.device ~= nil or args.orientation ~= nil
		or (args.players ~= nil and players ~= self:_clientsHere())) then
		action = "restart" -- (a device, or the players, are set between play sessions: the game starts over)
	end
	if action == "start" then
		local why = self:_startDevice(args)
		if why then
			self:_respondError(id, why, 409)
			return
		end
		local ok, err = self:play(players)
		if ok then
			self:_respond(id, { running = true, scene = game.Name, players = self:_clientsHere() })
		else
			self:_respondError(id, err or "couldn't play", 500)
		end
	elseif action == "stop" then
		local ok, err = self:stopPlaying()
		if ok then
			self:_respond(id, { running = false })
		else
			self:_respondError(id, err or "couldn't stop", 500)
		end
	elseif action == "restart" then
		-- As Studio's Stop then Play: a fresh server and client (the game's scripts start over)
		local ok, err = self:stopPlaying()
		if ok then
			err = self:_startDevice(args)
			ok = err == nil
		end
		if ok then
			ok, err = self:play(players)
		end
		if ok then
			self:_respond(id, { running = true, scene = game.Name, players = self:_clientsHere() })
		else
			self:_respondError(id, err or "couldn't restart", 500)
		end
	else
		self:_respond(id, { running = self.playing, loading = self:loading(), scene = game.Name,
			players = self:_clientsHere() })
	end
end

function Editor:_playEnded()
	if self.restorePending then
		self.restorePending = false
		task.spawn(Device.restore)
	end
	if not self.playing and not self.sides.client then
		return
	end
	self.playing = false
	self.sides = {}
	-- (the requests sent on lately that may not be answered: the brain takes an error only for one still waiting)
	local t = now()
	for rid, at in pairs(self.forwarded) do
		if t - at < 120 then
			self:_respondError(rid, "the game stopped", 500)
		end
	end
	table.clear(self.forwarded)
	if self:connected() then
		self:_send({ type = "events", events = { { kind = "session", t = Json.null, running = false, scene = game.Name } },
			t = Json.null })
	end
	self:_fire("playing", false)
end

-- --- the pipe to the play session ----------------------------------------------------------------------------------

-- The play session's sides join the brain's pipe for this connection (its session's key, left in the plugin's
-- settings); the editor reaches them through its own connection ({"type": "pipe"} frames: autopilot/bridge.py), so it
-- holds one socket in all (Studio lets a DataModel have only a few, its other plugins' counted).
function Editor:_startPipe(session: string)
	self:_endPipe()
	self.session = session
	Settings.set(self.plugin, "pipe", { url = self.link.url, key = session, http_url = self.link.httpUrl })
	-- (sides already playing say so when asked)
	self:_pipeSend({ to = "play", type = "who" })
end

function Editor:_endPipe()
	if self.session then
		self:_pipeSend({ to = "play", type = "detach" })
		self.session = nil
	end
	if self.playing then
		self.playing = false
		self.sides = {}
		self:_fire("playing", false)
	end
	if self.restorePending then
		-- (the connection went with the game playing: tried from here, else the next run's end puts it back)
		self.restorePending = false
		task.spawn(Device.restore)
	end
end

function Editor:_pipeSend(frame: any)
	if self.link.connected then
		self.link:send({ type = "pipe", frame = frame })
	end
end

function Editor:_onPipeFrame(frame: any)
	if type(frame) ~= "table" or frame.to ~= "edit" then
		return
	end
	local side = tostring(frame.from or "")
	local kind = frame.type
	local s = self.sides[side]
	if s then
		s.seen = now()
	end
	if kind == "here" then
		-- A side of a play session joined: attach it (its detectors on, its events to the brain)
		self.sides[side] = { ready = false, seen = now() }
		self:_pipeSend({ to = side, type = "attach", detectors = self.detectorConfig, run_active = self.runActive })
	elseif kind == "ready" then
		self.sides[side] = { ready = true, seen = now() }
		if side == "client" and self.recordArgs then
			-- (the brain's recording goes on in this play session)
			self:_pipeSend({ to = "client", type = "request", id = "record-resume", op = "record",
				args = self.recordArgs })
		end
		if side == "client" and not self.playing then
			self.playing = true
			if self:connected() then
				self:_send({ type = "events", events = { { kind = "session", t = frame.t or 0, running = true,
					scene = game.Name } }, t = frame.t or 0 })
			end
			self:_fire("playing", true)
		end
		self:_fire("side_ready", side) -- (the dock's mirror in the client: Mirror.luau)
	elseif kind == "alive" then
		if s == nil then
			-- (attached before this editor's state was lost, a plugin reload: attach it again)
			self.sides[side] = { ready = false, seen = now() }
			self:_pipeSend({ to = side, type = "attach", detectors = self.detectorConfig, run_active = self.runActive })
		end
	elseif kind == "bye" then
		self.sides[side] = nil
		if side == "client" then
			self:_playEnded()
		end
	elseif kind == "dock_action" and side == "client" then
		self:_fire("dock_action", frame) -- (what the dock's mirror in the client asks: Mirror.luau)
	elseif kind == "findings" then
		for _, f in ipairs(frame.findings or {}) do
			if type(f) == "table" then
				self:_fire("finding", f)
			end
		end
	end
end

-- Once a frame: a heartbeat to the brain while no game plays (the client's own heartbeats go on during play), and a
-- play session whose client went quiet is ended.
function Editor:_tick()
	local t = now()
	if self:connected() and not self.playing and t - self.heartbeatAt >= 1 then
		self.heartbeatAt = t
		self:_send({ type = "events", events = { { kind = "heartbeat", t = Json.null, fps = 60, paused = false } },
			t = Json.null })
	end
	-- the play session's sides hear when a run starts or ends: their detectors work while one uses the game
	if self.runActive ~= self.runTold then
		self.runTold = self.runActive
		self:_pipeSend({ to = "play", type = "run", active = self.runActive })
	end
	if self.playing then
		if t - (self.whoAt or 0) >= 2 then
			self.whoAt = t
			self:_pipeSend({ to = "play", type = "who" })
		end
		local c = self.sides.client
		if c and t - c.seen > 8 and not self.starting then
			self:_playEnded()
		end
	end
end

function Editor:stop()
	self.alive = false
	self.heartbeat:Disconnect()
	self:_endPipe()
	self.link:close()
end

return Editor
