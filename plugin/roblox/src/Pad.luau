--!strict
-- The gamepad's analog parts. Studio's virtual input has keys and a mouse but no pad, so the sticks and triggers go in as
-- Roblox's own gamepad controls would put them:
--  - the left stick walks the player: its direction relative to the camera to the player's Move every frame, after the
--    PlayerModule's (as Roblox's gamepad controls do, a dead zone of 0.2), so a game that reads the Humanoid's
--    MoveDirection or the ControlModule's move vector sees the walk;
--  - the right stick turns the camera at up to TURN degrees a second (about the default gamepad camera's), through the
--    mouse as `MouseX`/`MouseY` do;
--  - a trigger (Gamepad_LeftTriggerAxis, Gamepad_RightTriggerAxis) past half way is its button (ButtonL2, ButtonR2);
--  - ButtonA (Gamepad_FaceButton_Bottom) jumps while held, as Roblox's gamepad controls do, unless the game bound
--    ButtonA to an action of its own (ContextActionService), which then gets it alone.
-- On a touch device (Studio simulating a phone) the camera turns by a finger's drag, not the mouse: turn it with `touch`.
-- A game that reads the sticks themselves (InputChanged with Thumbstick1, GetGamepadState) doesn't see them: use its
-- move action, or `move`.
local Players = game:GetService("Players")
local RunService = game:GetService("RunService")

local Keys = require(script.Parent.Keys)

local Pad = {}

local DEAD = 0.2
local TURN = 240 -- degrees a second, the right stick full over
local DEGREES_PER_PIXEL = 0.48 -- the default camera's turn per pixel of mouse movement (MouseSensitivity 1)

local state: { [string]: number } = { lx = 0, ly = 0, rx = 0, ry = 0, lt = 0, rt = 0 }
local triggerDown: { [string]: boolean } = {}
local jumping = false
-- The PlayerModule's own actions (its keyboard and gamepad controls), not the game's
local PLAYER_MODULE = { jumpAction = true, moveForwardAction = true, moveBackwardAction = true, moveLeftAction = true,
	moveRightAction = true }
local stepName = "AutopilotPad"
local stepping = false

local function step(dt: number)
	local lp = Players.LocalPlayer
	local move = Vector3.new(state.lx, 0, -state.ly)
	if move.Magnitude > DEAD and lp then
		pcall(function()
			lp:Move(if move.Magnitude > 1 then move.Unit else move, true)
		end)
	end
	if jumping then
		local char = lp and lp.Character
		local h = char and char:FindFirstChildOfClass("Humanoid")
		if h then
			h.Jump = true
		end
	end
	local look = Vector2.new(state.rx, -state.ry)
	if look.Magnitude > DEAD then
		local pixels = look * (TURN / DEGREES_PER_PIXEL) * dt
		task.spawn(Keys.moveMouse, pixels) -- (it may yield a frame to hold the right button: not in a render step)
	end
end

local function ensureStepping()
	local any = jumping
	for _, v in pairs(state) do
		if v ~= 0 then
			any = true
		end
	end
	if any and not stepping then
		stepping = true
		RunService:BindToRenderStep(stepName, Enum.RenderPriority.Input.Value + 1, step)
	elseif not any and stepping then
		stepping = false
		RunService:UnbindFromRenderStep(stepName)
	end
end

-- Set a stick's axis (-1..1, y up/forward positive) or a trigger (0..1). Returns false and why when a trigger's button
-- was refused.
function Pad.set(spec: any, value: number): (boolean, string?)
	local field = spec.field :: string
	if spec.kind == "pad_trigger" then
		value = math.clamp(value, 0, 1)
		state[field] = value
		local down = value > 0.5
		if down ~= (triggerDown[field] == true) then
			local ok, why = Keys.send({ kind = "key", code = spec.code, name = spec.name }, down)
			if not ok then
				ensureStepping()
				return false, why
			end
			triggerDown[field] = down
		end
	else
		state[field] = math.clamp(value, -1, 1)
	end
	ensureStepping()
	return true, nil
end

-- ButtonA down or up (Keys.send calls this for every gamepad button): a jump, held, unless the game took ButtonA.
function Pad.button(code: Enum.KeyCode, down: boolean)
	if code ~= Enum.KeyCode.ButtonA then
		return
	end
	if down then
		for name, inputs in pairs(Keys.boundActions()) do
			if PLAYER_MODULE[name] then
				continue -- (Roblox's own gamepad controls: the jump this is)
			end
			for _, x in ipairs(inputs) do
				if x == Enum.KeyCode.ButtonA then
					return -- (the game's own ButtonA)
				end
			end
		end
	end
	jumping = down
	ensureStepping()
end
Keys.onPadButton = Pad.button

-- What the pad holds now, for context: {left = {x, y}, right = {x, y}, left_trigger, right_trigger}.
function Pad.state(): any
	return { left = { x = state.lx, y = state.ly }, right = { x = state.rx, y = state.ry }, left_trigger = state.lt,
		right_trigger = state.rt }
end

function Pad.releaseAll()
	for field, down in pairs(triggerDown) do
		if down then
			Keys.send({ kind = "key", code = if field == "lt" then Enum.KeyCode.ButtonL2 else Enum.KeyCode.ButtonR2,
				name = field }, false)
		end
	end
	table.clear(triggerDown)
	jumping = false
	for k in pairs(state) do
		state[k] = 0
	end
	ensureStepping()
end

return Pad
