--!strict
-- The play session's client (the player's DataModel): what the player sees and does. Answers view, screen, ui, ui_act,
-- pointer, touch, key, input, move, watch, find, transforms, trace, pose, movement, device, context, ping, capture,
-- record and rc on the client's own instances (the editor sends rc to the server unless the path is the client's:
-- Editor.route).
-- Runs the client's detectors. Holds are leased as in every plugin: a held key not renewed within its lease is let go.
local Players = game:GetService("Players")
local RunService = game:GetService("RunService")
local UserInputService = game:GetService("UserInputService")

local Detectors = require(script.Parent.Detectors)
local Device = require(script.Parent.Device)
local Fallback = require(script.Parent.Fallback)
local Frame = require(script.Parent.Frame)
local Host = require(script.Parent.Host)
local Json = require(script.Parent.Json)
local Keys = require(script.Parent.Keys)
local Media = require(script.Parent.Media)
local Modules = require(script.Parent.Modules)
local Pad = require(script.Parent.Pad)
local Paths = require(script.Parent.Paths)
local Rc = require(script.Parent.Rc)
local Sight = require(script.Parent.Sight)
local Ui = require(script.Parent.Ui)
local World = require(script.Parent.World)

local Client = {}

local KEY_TAP_SECONDS = 0.05
local KEY_TAP_FRAMES = 2

function Client.start(plugin: Plugin)
	Device.init(plugin)
	local held: { [string]: any } = {}
	local unreachedAt = -1e9 -- when a key last didn't reach the game (told at most every 15 s)
	local lease = 1.0
	local host: any = nil
	local detectors: any = nil

	local function frames(n: number)
		for _ = 1, n do
			RunService.Heartbeat:Wait()
		end
	end

	local sequencesRef: { any } = {} -- (set below: the sequences table, cleared with the keys)
	local function releaseAll()
		for _, h in pairs(held) do
			if not Keys.isAxis(h.spec) then
				pcall(Keys.send, h.spec, false)
			end
		end
		table.clear(held)
		if sequencesRef[1] then
			table.clear(sequencesRef[1])
		end
		Keys.releaseTurn()
		Fallback.releaseAll()
		Pad.releaseAll()
		Ui.releaseTouch()
	end

	-- A key sent down that the game didn't get. Studio's virtual input reaches the game whether or not Studio's game
	-- view is the focused window, so this is a game view that takes no input at all; then the keys Roblox's own controls
	-- read go in as those controls put them (Fallback.luau) until a key reaches the game again. Told to the brain as a
	-- finding (the run's report says it) and in Studio's output.
	local unfocused = false
	local function reached(spec: any): boolean
		local r = Keys.reached(spec)
		if r == true then
			unfocused = false
		elseif r == false then
			unfocused = true
			if os.clock() - unreachedAt >= 15 then
				unreachedAt = os.clock()
				local text = "The game didn't get the key " .. spec.name .. " from Studio's virtual input. Walking, "
					.. "jumping and prompts go in as Roblox's own controls take them meanwhile; other keys don't."
				warn("[Autopilot] " .. text)
				if host then
					host:emit({ kind = "finding", t = host:t(), finding = { detector = "no_input", severity = "warning",
						summary = text, signature = "no_input", source = "engine", t = host:t() } })
					host:emit({ kind = "log", t = host:t(), verbosity = "Warning", category = "Autopilot", message = text })
				end
			end
		end
		return r ~= false
	end

	local function controls(): { any }
		local out = {}
		for name, inputs in pairs(Keys.boundActions()) do
			local keys = {}
			for _, x in ipairs(inputs) do
				table.insert(keys, Keys.unrealName(x))
			end
			table.insert(out, { name = name, action = name, keys = keys, digital = true })
		end
		-- (Roblox's own controls, the PlayerModule's: always there unless the game replaced them)
		table.insert(out, { name = "Move (Roblox's default controls)", action = "", keys = { "W", "A", "S", "D", "Up",
			"Down", "Left", "Right" }, digital = true })
		table.insert(out, { name = "Jump (Roblox's default controls)", action = "", keys = { "SpaceBar" }, digital = true })
		table.insert(out, { name = "Turn the camera (Roblox's default camera: right mouse button held)", action = "",
			keys = { "MouseX", "MouseY", "RightMouseButton" }, digital = false })
		table.insert(out, { name = "Zoom (Roblox's default camera)", action = "", keys = { "MouseScrollUp",
			"MouseScrollDown", "I", "O" }, digital = true })
		table.insert(out, { name = "Move and turn with a gamepad (Roblox's default controls)", action = "",
			keys = { "Gamepad_LeftX", "Gamepad_LeftY", "Gamepad_RightX", "Gamepad_RightY" }, digital = false })
		table.insert(out, { name = "Jump with a gamepad (Roblox's default controls)", action = "",
			keys = { "Gamepad_FaceButton_Bottom" }, digital = true })
		return out
	end

	-- The play session's players, by number (1 is the player Autopilot plays as; 2, 3... the others of a multiplayer
	-- test, each played from its own client: requests with `player`)
	local function players(): { any }
		local out = {}
		for _, p in ipairs(Players:GetPlayers()) do
			table.insert(out, { player = Paths.playerIndex(p), name = p.Name, path = Paths.of(p),
				character = Paths.of(p.Character), you = p == Players.LocalPlayer })
		end
		table.sort(out, function(a, b)
			return a.player < b.player
		end)
		return out
	end

	-- How the game is networked (the other plugins' context `net`): a Roblox play session is always a client of the
	-- game's server (Studio runs both), and `rc` reads and changes the server's copy (`rc_side`: the agent service's
	-- network note says so); `clients`: the players of a multiplayer test.
	local function net(): any
		local out: any = { mode = "client", rc_side = "server" }
		local n = #Players:GetPlayers()
		if n > 1 then
			out.clients = n
		end
		return out
	end

	local function context(): any
		local char = World.character()
		local cam = workspace.CurrentCamera
		local lp = Players.LocalPlayer
		return { you = if lp then Paths.playerIndex(lp) else 1, players = players(), world = "/Workspace", scene = game.Name, character = Paths.of(char), controller = Paths.of(lp),
			input_subsystem = "", camera_manager = Paths.of(cam), camera = Paths.of(cam), dimension = "3d",
			game_name = game.Name, host = "editor", engine = "roblox", session_running = true, paused = false,
			game_time = Frame.r2(World.gameTime()), project_dir = "", controls = controls(),
			player = Paths.of(lp), humanoid = Paths.of(World.humanoid()),
			camera_type = if cam then cam.CameraType.Name else "",
			mouse_behavior = UserInputService.MouseBehavior.Name,
			place_id = game.PlaceId, universe_id = game.GameId,
			-- (what the player's input is: the keys reach the game, focused or not; the device Studio simulates)
			input = Ui.inputCaps({ playing = true, keys_reach = not unfocused, pad = Pad.state() }),
			net = net(),
			device = Device.describe() }
	end

	-- A stick's axis or an analog trigger (Pad.luau): a hold keeps its value (renewed as any hold, or for `seconds`), a
	-- release puts it back to rest, a press pushes it for a moment (0.15 s).
	local function padOp(name: string, spec: any, mode: string, value: number, args: any): (boolean, any, number?)
		if mode == "release" then
			held[name] = nil
			Pad.set(spec, 0)
			return true, Json.object()
		end
		local ok, why = Pad.set(spec, value)
		if not ok then
			return false, why, 409
		end
		if mode == "hold" then
			local h = held[name] or { spec = spec }
			held[name] = h
			h.value = value
			h.renewed = os.clock()
			local seconds = tonumber(args.seconds) or 0
			h["until"] = if seconds > 0 then World.gameTime() + seconds else 0
			return true, Json.object()
		end
		task.wait(0.15)
		if not held[name] then
			Pad.set(spec, 0)
		end
		return true, Json.object()
	end

	local function keyOp(args: any, _id: any): (boolean, any, number?)
		local mode = tostring(args.mode or "press")
		if tonumber(args.lease) and tonumber(args.lease) > 0 then
			lease = math.clamp(tonumber(args.lease) :: number, 0.2, 10)
		end
		if mode == "release_all" then
			releaseAll()
			return true, Json.object()
		end
		local name = tostring(args.key or "")
		local spec = Keys.spec(name)
		if spec == nil then
			return false, "Unknown key " .. name
		end
		local value = tonumber(args.value) or 1
		if Keys.isPad(spec) then
			return padOp(name, spec, mode, value, args)
		end
		local axis = Keys.isAxis(spec)
		if mode == "release" then
			local h = held[name]
			if h then
				if not axis then
					Keys.send(spec, false)
				end
				if h.fallback then
					Fallback.up(spec)
				end
				held[name] = nil
			end
			return true, Json.object()
		end
		if mode == "hold" then
			if not held[name] then
				local h = { spec = spec }
				if not axis then
					local sent, why = Keys.send(spec, true, value)
					if not sent then
						return false, why, 409
					end
				end
				held[name] = h
				if not axis then
					if unfocused and Fallback.handles(spec) then
						h.fallback = true
						Fallback.down(spec)
					end
					task.spawn(function()
						-- (a few frames for the key to show: a multiplayer test's client, its own process, can be slow)
						local seen = false
						for _ = 1, 8 do
							RunService.Heartbeat:Wait()
							if held[name] ~= h or Keys.reached(spec) ~= false then
								seen = true
								break
							end
						end
						if held[name] == h and not h.fallback and not seen and not reached(spec)
							and Fallback.handles(spec) then
							h.fallback = true
							Fallback.down(spec)
						end
					end)
				end
			end
			local h = held[name]
			h.value = value
			h.rate = args.rate == true
			h.renewed = os.clock()
			local seconds = tonumber(args.seconds) or 0
			h["until"] = if seconds > 0 then World.gameTime() + seconds else 0
			return true, Json.object()
		end
		-- a tap: an axis moves once; a key goes down, and up again a moment later, as a quick press does
		if axis then
			Keys.send(spec, true, value)
			return true, Json.object()
		end
		local sent, why = Keys.send(spec, true, value)
		if not sent then
			return false, why, 409
		end
		local started = os.clock()
		frames(KEY_TAP_FRAMES)
		-- (down at least KEY_TAP_SECONDS, and until the game shows it, a few frames at most)
		local seen = Keys.reached(spec) ~= false
		local waited = KEY_TAP_FRAMES
		while os.clock() - started < KEY_TAP_SECONDS or (not seen and waited < 8) do
			RunService.Heartbeat:Wait()
			waited += 1
			seen = seen or Keys.reached(spec) ~= false
		end
		local got = seen or reached(spec)
		if not held[name] then
			Keys.send(spec, false)
		end
		if not got and Fallback.handles(spec) then
			Fallback.tap(spec)
			return true, { via = "fallback" }
		end
		return true, Json.object()
	end

	local function tickKeys(dt: number)
		Keys.tick()
		local now = os.clock()
		local t = World.gameTime()
		for name, h in pairs(held) do
			local releaseAt = h["until"] or 0
			local expired = if releaseAt > 0 then t >= releaseAt else (now - h.renewed > lease)
			if expired then
				if Keys.isPad(h.spec) then
					Pad.set(h.spec, 0)
				elseif not Keys.isAxis(h.spec) then
					Keys.send(h.spec, false)
				end
				if h.fallback then
					Fallback.up(h.spec)
				end
				held[name] = nil
			elseif h.spec.kind == "mouse_axis" or h.spec.kind == "wheel_axis" then
				Keys.send(h.spec, true, if h.rate then h.value * dt else h.value)
			end
		end
	end

	-- `sequence`: inputs the game runs itself, frame by frame on its own clock (the kit's Game.sequence), so a move
	-- timed to the game (a jump at an edge) lands on the same frame whatever the brain's delay. Each step fires at its
	-- game time (`at` seconds after the start, or `after` the step before fired) once its `when` holds, read every
	-- Heartbeat: {near = {X, Y, Z (kit, cm), within (cm)}} the player within that far across the floor of the point,
	-- {past = {X, Y, Z, dX, dY, dZ}} the player past the plane through the point facing the direction, {on_ground},
	-- {falling}, {vz_below}, {vz_above} (its vertical speed, cm/s), {t} (game time); all given must hold. A step whose
	-- condition doesn't come within `timeout` (3 s) of the step before is skipped. Holds a sequence leaves down are
	-- leased from then, as any hold (the brain renews them). {action = "start" | "status" | "cancel", id, steps}
	local sequences: { [string]: any } = {}
	sequencesRef[1] = sequences

	local function seqHolds(when: any, dt: number): boolean
		if type(when) ~= "table" then
			return true
		end
		local root, hum = World.root(), World.humanoid()
		local needsRoot = when.near ~= nil or when.past ~= nil or when.on_ground ~= nil or when.falling ~= nil
			or when.vz_below ~= nil or when.vz_above ~= nil
		if needsRoot and root == nil then
			return false
		end
		local rootPart = root :: BasePart
		-- (where the body will be by the next frame: a press made this frame acts on the next one, and at a low frame
		-- rate the body moves a long way between two checks; judged on where it is, the press comes a frame late)
		local ahead = rootPart.Position + rootPart.AssemblyLinearVelocity * math.clamp(dt, 0, 0.1)
		if type(when.near) == "table" then
			local d = (ahead - Frame.fromKit(when.near)) * Vector3.new(1, 0, 1)
			if d.Magnitude * Frame.SCALE > (tonumber(when.near.within) or 60) then
				return false
			end
		end
		if type(when.past) == "table" then
			local dir = Frame.fromKitDir({ X = when.past.dX, Y = when.past.dY, Z = when.past.dZ })
			if (ahead - Frame.fromKit(when.past)):Dot(dir) < 0 then
				return false
			end
		end
		if type(when.past_of) == "table" then
			-- a plane carried by a part (a moving platform's edge): through the part's position moved `along` cm
			-- in the kit direction, facing it; a part that is gone fails the condition
			local inst = Paths.resolve(tostring(when.past_of.path or ""))
			if inst == nil or not inst:IsA("BasePart") then
				return false
			end
			local dir = Frame.fromKitDir({ X = when.past_of.dX, Y = when.past_of.dY, Z = when.past_of.dZ })
			local point = (inst :: BasePart).Position + dir * ((tonumber(when.past_of.along) or 0) / Frame.SCALE)
			-- (the body's motion relative to the part: a deck drifting back brings its edge to the body)
			local rel = ahead - (inst :: BasePart).AssemblyLinearVelocity * math.clamp(dt, 0, 0.1)
			if (rel - point):Dot(dir) < 0 then
				return false
			end
		end
		if when.on_ground ~= nil then
			local on = hum ~= nil and hum.FloorMaterial ~= Enum.Material.Air
			if on ~= (when.on_ground == true) then
				return false
			end
		end
		if when.falling ~= nil then
			local falling = hum ~= nil and hum:GetState() == Enum.HumanoidStateType.Freefall
			if falling ~= (when.falling == true) then
				return false
			end
		end
		local vz = rootPart and rootPart.AssemblyLinearVelocity.Y * Frame.SCALE or 0
		if when.vz_below ~= nil and not (vz < (tonumber(when.vz_below) or 0)) then
			return false
		end
		if when.vz_above ~= nil and not (vz > (tonumber(when.vz_above) or 0)) then
			return false
		end
		if when.t ~= nil and World.gameTime() < (tonumber(when.t) or 0) then
			return false
		end
		return true
	end

	local function sequenceOp(args: any, _id: any): (boolean, any, number?)
		local action = tostring(args.action or "start")
		local id = tostring(args.id or "")
		if action == "status" then
			local q = sequences[id]
			if q == nil then
				return true, { done = true, fired = 0, skipped = {}, t = Frame.r2(World.gameTime()), unknown = true }
			end
			return true, { done = q.done, fired = q.fired, skipped = q.skipped, t = Frame.r2(World.gameTime()) }
		elseif action == "cancel" then
			sequences[id] = nil
			return true, Json.object()
		end
		if type(args.steps) ~= "table" or #args.steps == 0 then
			return false, "sequence: steps (a list) are needed"
		end
		if tonumber(args.lease) and tonumber(args.lease) > 0 then
			lease = math.clamp(tonumber(args.lease) :: number, 0.2, 10)
		end
		local t0 = World.gameTime()
		sequences[id] = { steps = args.steps, i = 1, t0 = t0, last = t0, fired = 0, skipped = {}, done = false,
			endedAt = 0 }
		return true, { id = id, t0 = Frame.r2(t0) }
	end

	local function tickSequences(dt: number)
		local t = World.gameTime()
		for id, q in pairs(sequences) do
			local guard = 0
			while not q.done and guard < 8 do
				guard += 1
				local s = q.steps[q.i]
				if s == nil then
					q.done = true
					q.endedAt = t
					break
				end
				local due = if s.at ~= nil then q.t0 + (tonumber(s.at) or 0) else q.last + (tonumber(s.after) or 0)
				if t < due then
					break
				end
				if type(s.when) == "table" and not seqHolds(s.when, dt) then
					if t - due > (tonumber(s.timeout) or 3) then
						table.insert(q.skipped, q.i - 1) -- (0-based, as the kit numbers steps)
						q.i += 1
						q.last = t
					else
						break
					end
				else
					local step = s
					task.spawn(function()
						keyOp({ key = step.key, mode = step.mode or "press", value = step.value }, nil)
					end)
					q.fired += 1
					q.i += 1
					q.last = t
				end
			end
			if q.done and t - q.endedAt > 30 then
				sequences[id] = nil -- (its status was there to be asked for a while)
			end
		end
	end

	-- `input`: an action the game bound with ContextActionService, by name (its first key), as `key` presses it.
	local function inputOp(args: any, id: any): (boolean, any, number?)
		local action = tostring(args.action or "")
		local spec = Keys.spec(action)
		if action == "" or spec == nil or spec.action == nil then
			return false, "No input action named " .. action
		end
		local mode = tostring(args.mode or "press")
		return keyOp({ key = action, mode = if mode == "hold" then "hold" else mode, seconds = args.seconds,
			value = args.value }, id)
	end

	-- `move`: walk the player in a kit-frame direction for `seconds`. `via` (as the other plugins'): "stick" (as
	-- Roblox's own controls do with an analog stick: the direction goes to the player's Move every frame, after the
	-- PlayerModule's; "humanoid" is the same), "keys" (the W/A/S/D keys toward it, 8 ways), "controller" (the
	-- character's Humanoid:Move itself, every frame: a game whose controls don't go through the Player), "auto" (the
	-- default: "stick"). `scale` -1..1 (negative: the other way). -> {"moved", "via"}
	local VIAS = { auto = "stick", stick = "stick", humanoid = "stick", keys = "keys", controller = "controller" }
	local function moveOp(args: any, _id: any): (boolean, any, number?)
		local asked = tostring(args.via or "auto")
		local via = VIAS[asked]
		if via == nil then
			return false, "move: unknown via " .. asked .. " (auto, stick, keys or controller)"
		end
		local root = World.root()
		if root == nil then
			return false, "No player character"
		end
		local lp = Players.LocalPlayer
		local hum = World.humanoid()
		if via == "controller" and hum == nil then
			return false, "move via controller: the player's character has no Humanoid", 409
		end
		local want = Frame.fromKitDir(args.direction or {})
		want = Vector3.new(want.X, 0, want.Z)
		if want.Magnitude > 1e-4 then
			want = want.Unit
		end
		local scale = math.clamp(tonumber(args.scale) or 1, -1, 1)
		local seconds = tonumber(args.seconds) or 0.35
		local start = root.Position
		local started = os.clock()
		if via == "keys" then
			local down = {}
			local names = { "W", "S", "A", "D" }
			while os.clock() - started < seconds and root.Parent do
				local cam = workspace.CurrentCamera
				local cf = if cam then cam.CFrame else root.CFrame
				local fwd = (cf.LookVector * Vector3.new(1, 0, 1))
				local right = (cf.RightVector * Vector3.new(1, 0, 1))
				fwd = if fwd.Magnitude > 1e-4 then fwd.Unit else Vector3.new(0, 0, -1)
				right = if right.Magnitude > 1e-4 then right.Unit else Vector3.new(1, 0, 0)
				local f, r = want:Dot(fwd) * scale, want:Dot(right) * scale
				local strengths = { f, -f, -r, r }
				for i = 1, 4 do
					local d = strengths[i] > 0.38
					if d ~= (down[i] == true) then
						down[i] = d
						Keys.send(Keys.spec(names[i]) :: any, d)
					end
				end
				RunService.Heartbeat:Wait()
			end
			for i = 1, 4 do
				if down[i] then
					Keys.send(Keys.spec(names[i]) :: any, false)
				end
			end
		else
			local name = "AutopilotMove" .. tostring(math.random(1, 1e9))
			-- (stick: the Player's Move, as the PlayerModule's controls call it; controller: the Humanoid's own)
			local function push(v: Vector3)
				pcall(function()
					if via == "controller" then
						(hum :: Humanoid):Move(v, false)
					else
						lp:Move(v, false)
					end
				end)
			end
			RunService:BindToRenderStep(name, Enum.RenderPriority.Input.Value + 1, function()
				push(want * scale)
			end)
			while os.clock() - started < seconds and root.Parent do
				RunService.Heartbeat:Wait()
			end
			RunService:UnbindFromRenderStep(name)
			push(Vector3.zero)
		end
		local moved = ((root.Position - start) * Vector3.new(1, 0, 1)).Magnitude * Frame.SCALE
		return true, { moved = Frame.r2(moved), via = via }
	end

	local function watchOp(args: any, _id: any): (boolean, any, number?)
		local st = Sight.watchStart(args, Paths.resolve)
		local seconds = tonumber(args.seconds) or 0.6
		local started = os.clock()
		Sight.watchTick(st, World.gameTime())
		while os.clock() - started < seconds do
			RunService.Heartbeat:Wait()
			Sight.watchTick(st, World.gameTime())
		end
		return true, Sight.watchFinish(st)
	end

	local ops: { [string]: any } = {
		context = function()
			return true, context()
		end,
		ping = function()
			return true, { game_time = Frame.r2(World.gameTime()), wall_time = DateTime.now().UnixTimestampMillis / 1000 }
		end,
		rc = function(args)
			return Rc.handle(tostring(args.verb or ""), args.body)
		end,
		key = keyOp,
		input = inputOp,
		sequence = sequenceOp,
		move = moveOp,
		watch = watchOp,
		view = function(args)
			return Sight.view(args)
		end,
		screen = function(args)
			return true, Ui.screen(args)
		end,
		ui = function()
			return true, { elements = Ui.list() }
		end,
		ui_act = function(args)
			return Ui.act(args)
		end,
		pointer = function(args)
			return Ui.pointer(args)
		end,
		touch = function(args)
			return Ui.touch(args)
		end,
		-- `device`: the device Studio simulates (Device.luau), switched while the game plays
		device = function(args)
			return Device.handle(args)
		end,
		-- `capture`, `record`: the game's picture, when Studio gives the plugin one (Media.luau)
		capture = function(args)
			return Media.capture(args)
		end,
		record = function(args, _id, h)
			return Media.record(args, h)
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
		trace = function(args)
			return Sight.trace(args)
		end,
		pose = function()
			return World.pose()
		end,
		movement = function()
			return World.movement()
		end,
		detectors = function(args)
			if detectors then
				detectors:configure(args)
			end
			return true, Json.object()
		end,
		-- `pause`: this side's simulation (the editor pauses the server too): physics and the game's scripts stop, the
		-- plugin goes on answering
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
			if want then
				releaseAll()
			end
			if detectors then
				detectors.paused = want
			end
			return true, { paused = want }
		end,
		timestep = function(args)
			return true, { note = "Roblox runs physics at its own fixed 240 Hz and scripts every frame: nothing to change" }
		end,
	}

	-- (player 1's client answers as "client", the others of a multiplayer test as "client2", "client3"...)
	local me = Players.LocalPlayer
	while me == nil do
		task.wait(0.1)
		me = Players.LocalPlayer
	end
	local index = Paths.playerIndex(me)
	host = Host.new(plugin, Host.clientSide(index), ops, index)
	host.onAttach = function(frame)
		Modules.install()
		task.spawn(Keys.toVirtual, Vector2.zero) -- (where virtual input's points are, measured now: not in a first turn)
		if detectors == nil then
			detectors = Detectors.new(function(e)
				host:emit(e)
			end, World.gameTime, function()
				return Ui.screen({}).texts
			end)
		end
		if type(frame.detectors) == "table" then
			detectors:configure(frame.detectors)
		end
	end
	host.onDetach = function()
		releaseAll()
		Media.stop()
		Modules.remove()
		if detectors then
			detectors:stop()
			detectors = nil
		end
	end
	table.insert(host.tickers, tickKeys)
	table.insert(host.tickers, tickSequences)
	table.insert(host.tickers, function(dt)
		if detectors then
			detectors:setActive(host:runUsingGame())
			detectors:tick(dt)
		end
	end)
	-- The player's character respawning is a new body: told as the game's session event (the brain re-reads the pawn)
	local lp = Players.LocalPlayer
	if lp then
		table.insert(host.conns, lp.CharacterAdded:Connect(function()
			releaseAll()
			host:emit({ kind = "character", t = host:t(), character = Paths.of(lp.Character) })
		end))
	end
	return {
		host = host,
		stop = function()
			releaseAll()
			host:stop()
		end,
	}
end

return Client
