--!strict
-- Keys for a game view that takes no virtual input. Studio's virtual input reaches the game whether or not its game
-- view is the focused window, so this is a safety net, used only after a key sent down didn't reach the game
-- (Client.luau): the keys Roblox's own controls read then go in the way those controls would put them, so the player
-- still walks, jumps and uses prompts: the movement keys (W/A/S/D and the arrows) as the player's Move, relative to the
-- camera (what the default ControlModule does with them), SpaceBar as the Humanoid's Jump, and a ProximityPrompt's key
-- as that prompt's input (InputHoldBegin / InputHoldEnd on the nearest prompt in reach that the key triggers).
local Players = game:GetService("Players")
local RunService = game:GetService("RunService")

local World = require(script.Parent.World)

local Fallback = {}

local MOVE: { [string]: Vector3 } = {
	W = Vector3.new(0, 0, -1), Up = Vector3.new(0, 0, -1), S = Vector3.new(0, 0, 1), Down = Vector3.new(0, 0, 1),
	A = Vector3.new(-1, 0, 0), Left = Vector3.new(-1, 0, 0), D = Vector3.new(1, 0, 0), Right = Vector3.new(1, 0, 0),
}
local JUMP = { SpaceBar = true, Space = true }

local held: { [string]: boolean } = {}
local prompts: { [string]: ProximityPrompt } = {}
local stepName = "AutopilotFallbackMove"
local stepping = false

local function moveVector(): Vector3
	local v = Vector3.zero
	for name in pairs(held) do
		local d = MOVE[name]
		if d then
			v += d
		end
	end
	return v
end

local function step()
	local lp = Players.LocalPlayer
	local v = moveVector()
	if v.Magnitude > 1e-3 and lp then
		pcall(function()
			lp:Move(v.Unit, true)
		end)
	end
	for name in pairs(held) do
		if JUMP[name] then
			local h = World.humanoid()
			if h then
				h.Jump = true
			end
		end
	end
end

local function ensureStepping()
	local any = next(held) ~= nil
	if any and not stepping then
		stepping = true
		RunService:BindToRenderStep(stepName, Enum.RenderPriority.Input.Value + 1, step)
	elseif not any and stepping then
		stepping = false
		RunService:UnbindFromRenderStep(stepName)
	end
end

-- The prompt in reach that a key triggers: enabled, its KeyboardKeyCode that key, the nearest within its
-- MaxActivationDistance of the player.
local function promptFor(code: Enum.KeyCode?): ProximityPrompt?
	local root = World.root()
	if root == nil or code == nil then
		return nil
	end
	local best: ProximityPrompt? = nil
	local bestD = math.huge
	for _, d in ipairs(workspace:GetDescendants()) do
		if d:IsA("ProximityPrompt") and d.Enabled and d.KeyboardKeyCode == code then
			local parent = d.Parent
			local at = if parent and parent:IsA("BasePart") then parent.Position
				elseif parent and parent:IsA("Attachment") then parent.WorldPosition
				elseif parent and parent:IsA("Model") then parent:GetPivot().Position else nil
			if at then
				local dist = (at - root.Position).Magnitude
				if dist <= d.MaxActivationDistance and dist < bestD then
					best, bestD = d, dist
				end
			end
		end
	end
	return best
end

-- Whether a key has a way in without focus (now).
function Fallback.handles(spec: any): boolean
	if spec.kind ~= "key" then
		return false
	end
	return MOVE[spec.name] ~= nil or JUMP[spec.name] ~= nil or promptFor(spec.code) ~= nil
end

function Fallback.down(spec: any)
	if spec.kind ~= "key" then
		return
	end
	if MOVE[spec.name] or JUMP[spec.name] then
		held[spec.name] = true
		ensureStepping()
		return
	end
	local p = promptFor(spec.code)
	if p and not prompts[spec.name] then
		prompts[spec.name] = p
		pcall(function()
			p:InputHoldBegin()
		end)
	end
end

function Fallback.up(spec: any)
	held[spec.name] = nil
	ensureStepping()
	local p = prompts[spec.name]
	if p then
		prompts[spec.name] = nil
		pcall(function()
			p:InputHoldEnd()
		end)
	end
end

-- A quick press: down, and up again once a prompt's hold is done (yields).
function Fallback.tap(spec: any)
	Fallback.down(spec)
	local p = prompts[spec.name]
	local wait = if p then p.HoldDuration + 0.05 else 0.05
	local t0 = os.clock()
	repeat
		RunService.Heartbeat:Wait()
	until os.clock() - t0 >= wait
	Fallback.up(spec)
end

function Fallback.releaseAll()
	for name in pairs(held) do
		held[name] = nil
	end
	for name, p in pairs(prompts) do
		prompts[name] = nil
		pcall(function()
			p:InputHoldEnd()
		end)
	end
	ensureStepping()
end

return Fallback
