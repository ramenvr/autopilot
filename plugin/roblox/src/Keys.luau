--!strict
-- The player's keyboard, mouse and gamepad: Unreal key names (as every plugin takes them) as Roblox input, sent through
-- Studio's virtual input (UserInputService:CreateVirtualInput), so the game's UserInputService, ContextActionService and
-- the default PlayerModule (walking, jumping, the camera) all see it as a player's. It goes in whether or not Studio's
-- game view is the focused window.
--
-- Key names: FKey names (W, SpaceBar, LeftShift, One, F5, Enter, LeftMouseButton, MouseX, MouseY, MouseScrollUp,
-- Gamepad_FaceButton_Bottom, Gamepad_LeftX...), or a Roblox KeyCode's name (E, Space, Return, KeypadOne, ButtonA,
-- DPadUp...), or the name of an action the game bound with ContextActionService (its first key is pressed).
-- MouseX/MouseY move the mouse by that many pixels (MouseY positive up, as Unreal's); with the mouse free (Roblox's
-- default camera, not locked to the middle) the camera only turns while the right button is held, so the plugin holds it
-- for the move, as a player does.
--
-- The gamepad: virtual input has keys, not a pad, so a gamepad button goes in as its KeyCode (ButtonA, ButtonR2,
-- DPadLeft...): InputBegan and InputEnded with that KeyCode (UserInputType Keyboard), ContextActionService actions bound
-- to it, IsKeyDown. A few buttons Roblox keeps for its own menus (ButtonB, ButtonStart and DPadUp on a console, as
-- Escape) can't be pressed this way: an error says so. The sticks and analog triggers are Pad.luau's: the left stick
-- walks the player as Roblox's gamepad controls do, the right stick turns the camera, a trigger past half way is its
-- button.
--
-- Points (the mouse's spot, a touch's) are in viewport pixels as GetMouseLocation gives them (the top bar's inset
-- included); virtual input takes the screen's, which Studio's device simulator moves (Keys.toVirtual).
local ContextActionService = game:GetService("ContextActionService")
local RunService = game:GetService("RunService")
local UserInputService = game:GetService("UserInputService")
local GuiService = game:GetService("GuiService")

local Keys = {}

local FKEYS: { [string]: string } = {
	SpaceBar = "Space", Enter = "Return", BackSpace = "Backspace", Tab = "Tab", Escape = "Escape",
	LeftShift = "LeftShift", RightShift = "RightShift", LeftControl = "LeftControl", RightControl = "RightControl",
	LeftAlt = "LeftAlt", RightAlt = "RightAlt", LeftCommand = "LeftMeta", RightCommand = "RightMeta",
	CapsLock = "CapsLock", Delete = "Delete", Insert = "Insert", Home = "Home", End = "End", PageUp = "PageUp",
	PageDown = "PageDown", Up = "Up", Down = "Down", Left = "Left", Right = "Right", Semicolon = "Semicolon",
	Equals = "Equals", Comma = "Comma", Hyphen = "Minus", Underscore = "Underscore", Period = "Period",
	Slash = "Slash", Tilde = "Backquote", LeftBracket = "LeftBracket", Backslash = "BackSlash",
	RightBracket = "RightBracket", Apostrophe = "Quote", Quote = "QuotedDouble", Add = "KeypadPlus",
	Subtract = "KeypadMinus", Multiply = "KeypadMultiply", Divide = "KeypadDivide", Decimal = "KeypadPeriod",
	Zero = "Zero", One = "One", Two = "Two", Three = "Three", Four = "Four", Five = "Five", Six = "Six",
	Seven = "Seven", Eight = "Eight", Nine = "Nine", NumPadZero = "KeypadZero", NumPadOne = "KeypadOne",
	NumPadTwo = "KeypadTwo", NumPadThree = "KeypadThree", NumPadFour = "KeypadFour", NumPadFive = "KeypadFive",
	NumPadSix = "KeypadSix", NumPadSeven = "KeypadSeven", NumPadEight = "KeypadEight", NumPadNine = "KeypadNine",
}

-- The gamepad's buttons, as Unreal names them (Roblox's own names, ButtonA..., work too)
local GAMEPAD: { [string]: string } = {
	Gamepad_FaceButton_Bottom = "ButtonA", Gamepad_FaceButton_Right = "ButtonB", Gamepad_FaceButton_Left = "ButtonX",
	Gamepad_FaceButton_Top = "ButtonY", Gamepad_LeftShoulder = "ButtonL1", Gamepad_RightShoulder = "ButtonR1",
	Gamepad_LeftTrigger = "ButtonL2", Gamepad_RightTrigger = "ButtonR2", Gamepad_LeftThumbstick = "ButtonL3",
	Gamepad_RightThumbstick = "ButtonR3", Gamepad_Special_Right = "ButtonStart", Gamepad_Special_Left = "ButtonSelect",
	Gamepad_DPad_Up = "DPadUp", Gamepad_DPad_Down = "DPadDown", Gamepad_DPad_Left = "DPadLeft",
	Gamepad_DPad_Right = "DPadRight",
}
-- Its analog parts (Pad.luau): a stick's axis (-1..1; y up/forward positive) or a trigger (0..1)
local PAD_AXES: { [string]: { field: string, trigger: string? } } = {
	Gamepad_LeftX = { field = "lx" }, Gamepad_LeftY = { field = "ly" }, Gamepad_RightX = { field = "rx" },
	Gamepad_RightY = { field = "ry" }, Gamepad_LeftTriggerAxis = { field = "lt", trigger = "ButtonL2" },
	Gamepad_RightTriggerAxis = { field = "rt", trigger = "ButtonR2" },
}

local MOUSE: { [string]: Enum.UserInputType } = {
	LeftMouseButton = Enum.UserInputType.MouseButton1,
	RightMouseButton = Enum.UserInputType.MouseButton2,
	MiddleMouseButton = Enum.UserInputType.MouseButton3,
}

-- Roblox KeyCodes as Unreal key names (for the controls listing): the inverse of FKEYS, letters and F keys as they are
local UNREAL: { [string]: string } = {}
for fkey, code in pairs(FKEYS) do
	UNREAL[code] = fkey
end
for fkey, code in pairs(GAMEPAD) do
	UNREAL[code] = fkey
end
UNREAL.Thumbstick1 = "Gamepad_LeftX"
UNREAL.Thumbstick2 = "Gamepad_RightX"

local keyCodes: { [string]: Enum.KeyCode } = {}
for _, item in ipairs(Enum.KeyCode:GetEnumItems()) do
	keyCodes[item.Name] = item
end

local virtual: any = nil
local function vi(): any
	if virtual == nil then
		virtual = UserInputService:CreateVirtualInput()
	end
	return virtual
end
Keys.virtual = vi

export type Spec = { kind: string, code: Enum.KeyCode?, input: Enum.UserInputType?, axis: string?, dir: number?,
	action: string?, name: string, field: string? }

-- Whether a KeyCode is a gamepad's (ButtonA, DPadUp, Thumbstick1...).
function Keys.isGamepadCode(code: Enum.KeyCode?): boolean
	if code == nil then
		return false
	end
	local n = code.Name
	return string.sub(n, 1, 6) == "Button" or string.sub(n, 1, 4) == "DPad" or string.sub(n, 1, 10) == "Thumbstick"
end

-- The Unreal name of a Roblox KeyCode or mouse input (W, SpaceBar, LeftMouseButton...).
function Keys.unrealName(input: any): string
	if typeof(input) == "EnumItem" then
		if input.EnumType == Enum.KeyCode then
			return UNREAL[input.Name] or input.Name
		end
		for name, t in pairs(MOUSE) do
			if t == input then
				return name
			end
		end
		return input.Name
	end
	return tostring(input)
end

-- Actions Roblox's own scripts bind (its emote menu, UI navigation), never the game's
local CORE_ACTIONS = { EmotesMenuToggleAction = true, ScrollSelectedElement = true, EnableKeyboardUINavigation = true }

-- The actions the game bound with ContextActionService: {name -> {KeyCode or UserInputType...}}.
function Keys.boundActions(): { [string]: { EnumItem } }
	local out = {}
	local ok, all = pcall(function()
		return ContextActionService:GetAllBoundActionInfo()
	end)
	if ok and type(all) == "table" then
		for name, info in pairs(all) do
			if CORE_ACTIONS[name] then
				continue
			end
			local inputs = {}
			for _, x in ipairs((info :: any).inputTypes or {}) do
				if typeof(x) == "EnumItem" then
					table.insert(inputs, x)
				end
			end
			out[name] = inputs
		end
	end
	return out
end

-- How a key name is sent ({kind = "key" | "mouse_button" | "mouse_axis" | "wheel" | "wheel_axis"}), or nil.
function Keys.spec(name: string): Spec?
	if name == "" then
		return nil
	end
	local code = FKEYS[name]
	if code then
		return { kind = "key", code = keyCodes[code], name = name }
	end
	if GAMEPAD[name] then
		return { kind = "key", code = keyCodes[GAMEPAD[name]], name = name }
	end
	local pad = PAD_AXES[name]
	if pad then
		return { kind = if pad.trigger then "pad_trigger" else "pad_axis", field = pad.field,
			code = if pad.trigger then keyCodes[pad.trigger] else nil, name = name }
	end
	if name == "Thumbstick1" or name == "Thumbstick2" then
		return nil -- (a stick is two axes: Gamepad_LeftX / Gamepad_LeftY, Gamepad_RightX / Gamepad_RightY)
	end
	if MOUSE[name] then
		return { kind = "mouse_button", input = MOUSE[name], name = name }
	end
	if name == "MouseX" or name == "MouseY" then
		return { kind = "mouse_axis", axis = name, name = name }
	end
	if name == "MouseScrollUp" or name == "MouseWheelUp" then
		return { kind = "wheel", dir = 1, name = name }
	end
	if name == "MouseScrollDown" or name == "MouseWheelDown" then
		return { kind = "wheel", dir = -1, name = name }
	end
	if name == "MouseWheelAxis" then
		return { kind = "wheel_axis", name = name }
	end
	if keyCodes[name] and name ~= "Unknown" then
		return { kind = "key", code = keyCodes[name], name = name }
	end
	-- An action the game bound (ContextActionService): its first key or mouse button
	local bound = Keys.boundActions()[name]
	if bound then
		for _, x in ipairs(bound) do
			if x.EnumType == Enum.KeyCode then
				return { kind = "key", code = x :: Enum.KeyCode, action = name, name = name }
			elseif x.EnumType == Enum.UserInputType and (x == Enum.UserInputType.MouseButton1
				or x == Enum.UserInputType.MouseButton2 or x == Enum.UserInputType.MouseButton3) then
				return { kind = "mouse_button", input = x :: Enum.UserInputType, action = name, name = name }
			end
		end
	end
	return nil
end

function Keys.isAxis(spec: Spec): boolean
	return spec.kind == "mouse_axis" or spec.kind == "wheel_axis"
end

-- Whether a key is the gamepad's analog part (a stick's axis, a trigger): Pad.luau holds its value.
function Keys.isPad(spec: Spec): boolean
	return spec.kind == "pad_axis" or spec.kind == "pad_trigger"
end

-- Where the mouse is (viewport pixels, as virtual input takes them): the pointer op's spot, else the middle.
Keys.pointer = nil :: Vector2?

function Keys.middle(): Vector2
	local cam = workspace.CurrentCamera
	local size = if cam then cam.ViewportSize else Vector2.new(800, 600)
	return size / 2
end

function Keys.mouseAt(): Vector2
	return Keys.pointer or Keys.middle()
end

-- Virtual input's points are the screen's; the viewport's (GetMouseLocation's, an element's middle) are offset from them
-- by Studio's device simulator (a phone's frame in the game view). Measured once per viewport size and device: a
-- wheel's nudge too small to zoom (1e-4 of a notch) at the middle, and where the game says it came (its InputObject's
-- Position, the top bar's inset added back). The mouse's position can't tell: a touch device doesn't follow it. Yields a
-- frame or two, the first time.
local offset = Vector2.zero
local offsetFor = ""
local measuring = false

function Keys.toVirtual(at: Vector2): Vector2
	local cam = workspace.CurrentCamera
	local vp = if cam then cam.ViewportSize else Vector2.zero
	local key = string.format("%.0fx%.0f/%s", vp.X, vp.Y, tostring(UserInputService.TouchEnabled))
	if key ~= offsetFor and measuring then
		for _ = 1, 10 do -- (another caller is measuring: its answer, not a stale one)
			if not measuring then
				break
			end
			RunService.Heartbeat:Wait()
		end
	end
	if key ~= offsetFor and not measuring then
		measuring = true
		local probe = Vector2.new(math.floor(vp.X / 2), math.floor(vp.Y / 2))
		local got: Vector3? = nil
		local conn = UserInputService.InputChanged:Connect(function(input)
			if input.UserInputType == Enum.UserInputType.MouseWheel then
				got = input.Position
			end
		end)
		local ok = pcall(function()
			vi():SendPointerAction(probe, { Wheel = 1e-4 })
		end)
		for _ = 1, 4 do
			if got or not ok then
				break
			end
			RunService.Heartbeat:Wait()
		end
		conn:Disconnect()
		if got then
			local d = probe - (Vector2.new(got.X, got.Y) + Keys.guiInset())
			offset = if d.Magnitude < 2 then Vector2.zero else Vector2.new(math.round(d.X), math.round(d.Y))
			offsetFor = key -- (only a measurement that came is kept: one that didn't is tried again next time)
		end
		measuring = false
	end
	return at + offset
end

-- The offset Keys.toVirtual found (for a look while testing).
function Keys.offset(): Vector2
	return offset
end

-- Virtual input's error for a point or key it won't take, said plainly.
function Keys.why(err: any): string
	local e = tostring(err)
	if string.find(e, "CoreGUI core action", 1, true) then
		return "Roblox keeps that key for its own menus: a plugin can't press it"
	elseif string.find(e, "hits CoreGUI", 1, true) then
		return "that spot is on Roblox's own top bar: a plugin can't press there"
	end
	return (string.gsub(e, "^.-VirtualInput::", "VirtualInput::"))
end

Keys.onPadButton = nil :: ((Enum.KeyCode, boolean) -> ())?

local rightHeld = false -- the right button is down (held by the game's player or for a turn)

-- Send one change of a key: pressed or let go; value for an axis (pixels of mouse movement, wheel notches). Returns
-- false and why when virtual input refused it (a key Roblox keeps for its menus, a spot on its top bar).
function Keys.send(spec: Spec, pressed: boolean, value: number?): (boolean, string?)
	local v = vi()
	local ok, err = pcall(function()
		if spec.kind == "key" then
			v:SendKey(pressed, spec.code, false)
			if Keys.onPadButton and Keys.isGamepadCode(spec.code) then
				Keys.onPadButton(spec.code, pressed) -- (Pad.luau: ButtonA jumps, as Roblox's gamepad controls do)
			end
		elseif spec.kind == "mouse_button" then
			if spec.input == Enum.UserInputType.MouseButton2 then
				rightHeld = pressed
			elseif pressed then
				Keys.endTurn()
			end
			-- (the cursor over the game first: a press where the real mouse is, outside the game view, goes nowhere)
			local at = Keys.toVirtual(Keys.mouseAt())
			if pressed then
				v:SendMousePosition(at)
			end
			v:SendMouseButton(at, spec.input, pressed, 1)
		elseif spec.kind == "mouse_axis" and pressed then
			local amount = value or 0
			local delta = if spec.axis == "MouseX" then Vector2.new(amount, 0) else Vector2.new(0, -amount)
			Keys.moveMouse(delta)
		elseif (spec.kind == "wheel" or spec.kind == "wheel_axis") and pressed then
			local notches = if spec.kind == "wheel" then (spec.dir or 1) else (value or 0)
			v:SendPointerAction(Keys.toVirtual(Keys.mouseAt()), { Wheel = notches })
		end
	end)
	if not ok then
		return false, Keys.why(err)
	end
	return true, nil
end

-- Move the mouse by delta pixels. With the mouse locked (first person, shift lock, a game's own camera) it moves alone;
-- with it free (Roblox's default camera turns only while the right button is held, locking the mouse meanwhile) the
-- right button is held for the turn and let go a second after the last one (Keys.tick), as a player drags. Yields a
-- frame or two when it presses the button. Returns whether the move went in.
local turning = { held = false, releaseAt = 0, releasedAt = -math.huge }
local settle: () -> ()
-- (how long the button stays down after the last turn: a second, so turns that come one after another, as the kit's
-- calibrate_look and look_at send them, find it down and turn at once, not a few frames late while it is pressed again)
local TURN_HOLD = 1.0

function Keys.moveMouse(delta: Vector2): boolean
	local v = vi()
	if not turning.held then
		settle() -- (a turn just let go of, e.g. by release_all: the camera frees the mouse before it is taken again)
	end
	turning.releaseAt = os.clock() + TURN_HOLD -- (first: the button pressed below isn't let go while it locks the mouse)
	if UserInputService.MouseBehavior == Enum.MouseBehavior.Default and not rightHeld and not turning.held then
		turning.held = true -- (first: a caller meanwhile mustn't press the button again)
		local at = Keys.toVirtual(Keys.mouseAt())
		v:SendMousePosition(at) -- (the cursor over the game first, as in send)
		RunService.Heartbeat:Wait()
		v:SendMouseButton(at, Enum.UserInputType.MouseButton2, true, 1)
		for _ = 1, 6 do
			if UserInputService.MouseBehavior ~= Enum.MouseBehavior.Default then
				break
			end
			RunService.Heartbeat:Wait()
		end
	end
	turning.releaseAt = os.clock() + TURN_HOLD
	local ok = pcall(function()
		v:SendMouseDelta(delta)
	end)
	return ok
end

-- Wait (a few frames at most) while the camera still holds the mouse after the turning button was let go of just now:
-- a click or a new turn meanwhile would be lost.
settle = function()
	if rightHeld or os.clock() - turning.releasedAt > 0.5 then
		return
	end
	-- (a few frames since the button went up, whatever MouseBehavior says at once: the camera puts the mouse back where
	-- it held it meanwhile, and a pointer moved before that is moved back)
	while os.clock() - turning.releasedAt < 0.05 do
		RunService.Heartbeat:Wait()
	end
	for _ = 1, 8 do
		if UserInputService.MouseBehavior == Enum.MouseBehavior.Default then
			break
		end
		RunService.Heartbeat:Wait()
	end
	RunService.Heartbeat:Wait()
end

-- Let go of the right button held for turning, without waiting (also called on detach: ticking stops when the host
-- disconnects). Answers whether it sent the button up.
function Keys.releaseTurn(): boolean
	if turning.held then
		turning.held = false
		if not rightHeld then
			pcall(function()
				vi():SendMouseButton(Keys.toVirtual(Keys.mouseAt()), Enum.UserInputType.MouseButton2, false, 1)
			end)
			turning.releasedAt = os.clock()
			return true
		end
	end
	return false
end

-- Let go of the right button held for turning now (before a click or a pointing: while it is down the camera holds
-- the mouse where it is, and a click would land there), and wait until the camera has let go of the mouse (yields a
-- few frames, only when the button was let go of just now: here, or before without waiting, as release_all does).
function Keys.endTurn()
	Keys.releaseTurn()
	settle()
end

-- Once a frame: let go of the right button held for turning once the turns have stopped.
function Keys.tick()
	if turning.held and os.clock() > turning.releaseAt then
		Keys.releaseTurn()
	end
end

-- Whether a key sent down reached the game (a game view that took no input at all: the fallback, Fallback.luau).
function Keys.reached(spec: Spec): boolean?
	if spec.kind == "key" and spec.code then
		return UserInputService:IsKeyDown(spec.code)
	elseif spec.kind == "mouse_button" and spec.input and not UserInputService.TouchEnabled then
		return UserInputService:IsMouseButtonPressed(spec.input) -- (on a touch device the left button is a finger)
	end
	return nil
end

-- Put the mouse at a viewport point without moving it across (no delta). Returns false and why when virtual input
-- refused the spot.
function Keys.pointAt(at: Vector2): (boolean, string?)
	Keys.endTurn()
	Keys.pointer = at
	-- (sent again each frame until the mouse is there, a few frames at most: while the camera still holds the mouse
	-- after a turn, a few frames after its button went up, a move is dropped; a touch device's mouse doesn't follow)
	for _ = 1, 15 do
		local ok, err = pcall(function()
			vi():SendMousePosition(Keys.toVirtual(at))
		end)
		if not ok then
			return false, Keys.why(err)
		end
		if UserInputService.TouchEnabled then
			break
		end
		RunService.Heartbeat:Wait()
		if (UserInputService:GetMouseLocation() - at).Magnitude < 2 then
			break
		end
	end
	return true, nil
end

-- The left button (a finger, while Studio simulates a touch device: Studio makes the mouse's left button the touch)
-- down or up at a viewport point. Returns false and why when refused.
function Keys.button(at: Vector2, button: Enum.UserInputType, down: boolean, count: number?): (boolean, string?)
	if down and button ~= Enum.UserInputType.MouseButton2 then
		Keys.endTurn()
	end
	local ok, err = pcall(function()
		vi():SendMouseButton(Keys.toVirtual(at), button, down, count or 1)
	end)
	if not ok then
		return false, Keys.why(err)
	end
	return true, nil
end

function Keys.typeText(text: string)
	vi():SendTextInput(text)
end

-- The GUI inset: a ScreenGui that doesn't ignore it starts this far below the viewport's top.
function Keys.guiInset(): Vector2
	local ok, inset = pcall(function()
		return GuiService:GetGuiInset()
	end)
	return if ok then inset else Vector2.zero
end

return Keys
