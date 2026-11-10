--!strict
-- The screen's texts (`screen`), its interactive controls (`ui`, `ui_act`), the mouse's spot (`pointer`) and a finger on
-- the screen (`touch`).
--
-- Keys and ids are "<ScreenGui>.<Name>" (the ScreenGui the element is in, under the player's PlayerGui), repeated keys
-- getting "#2", "#3". An element counts when it and every GuiObject above it are Visible, its ScreenGui is Enabled, it
-- is on the screen and its text isn't fully transparent. ui_act acts as the player's mouse does: the mouse is moved to
-- the element's middle and pressed there (so whatever is on top gets the click, as for a player); the answer says
-- whether the button's Activated came ("fired"). Kinds: `button` (a GuiButton) and `text_input` (a TextBox: `text`,
-- `placeholder`); Roblox has no checkbox, slider or combo of its own (a game builds them from buttons and frames), so
-- there are no such kinds: they list as their buttons.
local GuiService = game:GetService("GuiService")
local Players = game:GetService("Players")
local RunService = game:GetService("RunService")
local UserInputService = game:GetService("UserInputService")

local Frame = require(script.Parent.Frame)
local Json = require(script.Parent.Json)
local Keys = require(script.Parent.Keys)
local Paths = require(script.Parent.Paths)

local Ui = {}

local last: { [string]: GuiObject } = {} -- id -> element, from the latest listing

local function playerGui(): PlayerGui?
	local lp = Players.LocalPlayer
	return lp and lp:FindFirstChildOfClass("PlayerGui")
end

local function screenGuiOf(g: Instance): LayerCollector?
	return g:FindFirstAncestorWhichIsA("LayerCollector")
end

-- Whether a ScreenGui is offset by the top bar's inset (its elements' AbsolutePosition then starts under it).
local function inset(gui: LayerCollector?): Vector2
	if gui and gui:IsA("ScreenGui") then
		local sg = gui :: ScreenGui
		local ignores = sg.IgnoreGuiInset
		local ok, insets = pcall(function()
			return (sg :: any).ScreenInsets
		end)
		if ok and insets == Enum.ScreenInsets.None then
			ignores = true
		end
		if not ignores then
			local ok2, v = pcall(function()
				return GuiService:GetGuiInset()
			end)
			return if ok2 then v else Vector2.zero
		end
	end
	return Vector2.zero
end

-- An element's middle in viewport pixels (where the mouse goes to click it).
function Ui.middle(g: GuiObject): Vector2
	return g.AbsolutePosition + g.AbsoluteSize / 2 + inset(screenGuiOf(g))
end

local function shown(g: GuiObject): boolean
	local gui = screenGuiOf(g)
	if gui == nil or not gui.Enabled then
		return false
	end
	local x: Instance? = g
	while x and x ~= gui do
		if x:IsA("GuiObject") and not (x :: GuiObject).Visible then
			return false
		end
		x = x.Parent
	end
	local cam = workspace.CurrentCamera
	local vp = if cam then cam.ViewportSize else Vector2.new(1e5, 1e5)
	local at, size = g.AbsolutePosition + inset(gui), g.AbsoluteSize
	if at.X > vp.X or at.Y > vp.Y or at.X + size.X < 0 or at.Y + size.Y < 0 then
		return false
	end
	return true
end

-- The elements on screen, in drawing order (the player's ScreenGuis, by DisplayOrder).
local function elements(): { GuiObject }
	local pg = playerGui()
	if pg == nil then
		return {}
	end
	local guis = {}
	for _, c in ipairs(pg:GetChildren()) do
		if c:IsA("ScreenGui") and c.Enabled then
			table.insert(guis, c)
		end
	end
	table.sort(guis, function(a, b)
		return a.DisplayOrder < b.DisplayOrder
	end)
	local out = {}
	for _, gui in ipairs(guis) do
		for _, d in ipairs(gui:GetDescendants()) do
			if d:IsA("GuiObject") and shown(d) then
				table.insert(out, d)
			end
		end
	end
	return out
end

function Ui.textOf(g: GuiObject): string
	if g:IsA("TextLabel") or g:IsA("TextButton") or g:IsA("TextBox") then
		local t = g :: any
		if t.TextTransparency >= 0.99 then
			return ""
		end
		local ok, content = pcall(function()
			return t.ContentText
		end)
		return if ok and type(content) == "string" then content else t.Text
	end
	return ""
end

local function keyed(list: { GuiObject }): { { any } }
	local counts = {}
	local out = {}
	for _, g in ipairs(list) do
		local gui = screenGuiOf(g)
		local base = (if gui then gui.Name else "Screen") .. "." .. g.Name
		counts[base] = (counts[base] or 0) + 1
		local key = if counts[base] == 1 then base else string.format("%s#%d", base, counts[base])
		table.insert(out, { key, g })
	end
	return out
end

function Ui.screen(args: any): any
	local texts = Json.object()
	local withText = {}
	for _, g in ipairs(elements()) do
		if string.match(Ui.textOf(g), "%S") then
			table.insert(withText, g)
		end
	end
	for _, kg in ipairs(keyed(withText)) do
		texts[kg[1]] = Ui.textOf(kg[2])
	end
	local out: any = { texts = texts }
	if args and args.ui then
		out.ui = Ui.list()
	end
	return out
end

local function kindOf(g: GuiObject): string
	if g:IsA("TextBox") then
		return "text_input"
	elseif g:IsA("GuiButton") then
		return "button"
	end
	return ""
end

function Ui.list(): { any }
	local interactive = {}
	for _, g in ipairs(elements()) do
		if kindOf(g) ~= "" then
			table.insert(interactive, g)
		end
	end
	table.clear(last)
	local out = {}
	for _, kg in ipairs(keyed(interactive)) do
		local id, g = kg[1], kg[2] :: GuiObject
		last[id] = g
		local label = Ui.textOf(g)
		if label == "" then
			for _, d in ipairs(g:GetDescendants()) do
				if d:IsA("TextLabel") and d.Visible and d.Text ~= "" then
					label = Ui.textOf(d)
					break
				end
			end
		end
		if label == "" and g:IsA("TextBox") then
			label = (g :: TextBox).PlaceholderText
		end
		-- (Interactable decides whether it takes input; Active only whether it sinks input from the world behind it)
		local enabled = true
		local ok, interactable = pcall(function()
			return (g :: any).Interactable
		end)
		if ok and interactable == false then
			enabled = false
		end
		if g:IsA("TextBox") and not (g :: TextBox).TextEditable then
			enabled = false
		end
		local m = Ui.middle(g)
		local gui = screenGuiOf(g)
		local item: any = { id = id, kind = kindOf(g), name = g.Name, owner = if gui then gui.Name else "Screen",
			label = label, enabled = enabled, path = Paths.of(g), class = g.ClassName,
			screen = { x = math.round(m.X), y = math.round(m.Y) } }
		if g:IsA("TextBox") then
			-- (`text` as the other plugins' text_input; `value` kept for older brains)
			item.text = (g :: TextBox).Text
			item.value = item.text
			if (g :: TextBox).PlaceholderText ~= "" then
				item.placeholder = (g :: TextBox).PlaceholderText
			end
		end
		table.insert(out, item)
	end
	return out
end

local function find(id: string): GuiObject?
	if string.sub(id, 1, 1) == "/" then
		local inst = Paths.resolve(id)
		return if inst and inst:IsA("GuiObject") then inst else nil
	end
	local g = last[id]
	if g == nil or g.Parent == nil then
		Ui.list()
		g = last[id]
	end
	return if g and g.Parent then g else nil
end

local function click(at: Vector2, button: Enum.UserInputType, count: number?): (boolean, string?)
	local ok, why = Keys.pointAt(at)
	if not ok then
		return false, why
	end
	task.wait() -- (the cursor there first, as a player's mouse arrives before it clicks)
	for i = 1, count or 1 do
		ok, why = Keys.button(at, button, true, i)
		if not ok then
			return false, why
		end
		Keys.button(at, button, false, i)
	end
	return true, nil
end

-- [ok, result or error]; a click's answer comes a few frames later (yields).
function Ui.act(args: any): (boolean, any)
	local id = tostring(args.id or "")
	local action = tostring(args.action or "click")
	local g = find(id)
	if g == nil then
		return false, "no element " .. id .. " on screen"
	end
	local el = g :: GuiObject
	if action == "click" or action == "right_click" or action == "double_click" then
		local fired = false
		local conns = {}
		if el:IsA("GuiButton") then
			local b = el :: GuiButton
			table.insert(conns, b.Activated:Connect(function()
				fired = true
			end))
			if action == "right_click" then
				table.insert(conns, b.MouseButton2Click:Connect(function()
					fired = true
				end))
			end
		elseif el:IsA("TextBox") then
			table.insert(conns, (el :: TextBox).Focused:Connect(function()
				fired = true
			end))
		end
		local button = if action == "right_click" then Enum.UserInputType.MouseButton2 else Enum.UserInputType.MouseButton1
		local ok, why = click(Ui.middle(el), button, if action == "double_click" then 2 else 1)
		if not ok then
			for _, c in ipairs(conns) do
				c:Disconnect()
			end
			return false, why
		end
		for _ = 1, 6 do
			if fired then
				break
			end
			task.wait()
		end
		for _, c in ipairs(conns) do
			c:Disconnect()
		end
		local out: any = { via = if UserInputService.TouchEnabled and button == Enum.UserInputType.MouseButton1
			then "touch" else "mouse", fired = fired }
		if not fired then
			out.note = "the element didn't take the click (something on top of it, or it isn't Interactable)"
		end
		return true, out
	elseif action == "set" then
		if el:IsA("TextBox") then
			local box = el :: TextBox
			local text = tostring(args.text or args.value or "")
			click(Ui.middle(box), Enum.UserInputType.MouseButton1)
			-- (the click focuses the box a frame or two later, clearing it when ClearTextOnFocus: the text goes in
			-- once it has focus, never before, or the focus would come after and take it away)
			for _ = 1, 10 do
				if box:IsFocused() then
					break
				end
				task.wait()
			end
			if not box:IsFocused() then
				pcall(function()
					box:CaptureFocus()
				end)
				task.wait()
			end
			box.Text = text
			task.wait()
			-- (submitted, as Enter would: FocusLost(enterPressed = true))
			pcall(function()
				box:ReleaseFocus(args.submit ~= false)
			end)
			return true, Json.object()
		end
		return false, id .. " takes no value"
	elseif action == "drag" then
		local to = find(tostring(args.to or ""))
		if to == nil then
			return false, "no element " .. tostring(args.to) .. " on screen to drop on"
		end
		local a, b = Ui.middle(el), Ui.middle(to :: GuiObject)
		Keys.pointAt(a)
		local ok, why = Keys.button(a, Enum.UserInputType.MouseButton1, true)
		if not ok then
			return false, why
		end
		for i = 1, 4 do
			task.wait()
			Keys.pointAt(a:Lerp(b, i / 4))
		end
		task.wait()
		Keys.button(b, Enum.UserInputType.MouseButton1, false)
		return true, Json.object()
	end
	return false, "unknown action " .. action
end

-- A spot on the screen, from {"screen_x", "screen_y"} (-1..1 from the middle, y up), {"x", "y"} (viewport pixels, the
-- top bar's inset included, as `ui` gives an element's `screen`), {"world": kit point}, {"object": path} (where the
-- camera shows it; a GUI element's middle) or {"ui": an id from `ui`}: (the viewport point, whether it is in view) or
-- (nil, why).
function Ui.spot(args: any): (Vector2?, any)
	local cam = workspace.CurrentCamera
	if cam == nil then
		return nil, "No camera"
	end
	local vp = cam.ViewportSize
	local at: Vector2
	local visible = true
	if args.screen_x ~= nil or args.screen_y ~= nil then
		local sx, sy = tonumber(args.screen_x) or 0, tonumber(args.screen_y) or 0
		at = Vector2.new((sx + 1) / 2 * vp.X, (1 - sy) / 2 * vp.Y)
	elseif tonumber(args.x) and tonumber(args.y) then
		at = Vector2.new(tonumber(args.x) :: number, tonumber(args.y) :: number)
	elseif args.ui ~= nil then
		local g = find(tostring(args.ui))
		if g == nil then
			return nil, "no element " .. tostring(args.ui) .. " on screen"
		end
		at = Ui.middle(g :: GuiObject)
	else
		local p: Vector3? = nil
		if type(args.world) == "table" then
			p = Frame.fromKit(args.world)
		elseif args.object then
			local inst = Paths.resolve(tostring(args.object))
			if inst == nil then
				return nil, "Object " .. tostring(args.object) .. " does not exist"
			end
			if inst:IsA("GuiObject") then
				at = Ui.middle(inst :: GuiObject)
			else
				local cf = if inst:IsA("BasePart") then (inst :: BasePart).CFrame
					elseif inst:IsA("PVInstance") then (inst :: PVInstance):GetPivot() else nil
				if cf == nil then
					return nil, tostring(args.object) .. " has no place in the world"
				end
				p = cf.Position
			end
		else
			return nil, "a spot wants screen_x/screen_y, x/y, world, object or ui"
		end
		if p then
			local sp, on = cam:WorldToViewportPoint(p)
			visible = on
			at = Vector2.new(sp.X, sp.Y)
		end
	end
	return Vector2.new(math.clamp(at.X, 0, vp.X - 1), math.clamp(at.Y, 0, vp.Y - 1)), visible
end

local function where(at: Vector2): any
	local cam = workspace.CurrentCamera
	local vp = if cam then cam.ViewportSize else Vector2.one
	return { x = math.round(at.X), y = math.round(at.Y), screen_x = Frame.r2(at.X / vp.X * 2 - 1),
		screen_y = Frame.r2(1 - at.Y / vp.Y * 2) }
end

-- How input reaches the game (context's and the hello's `input`, in the other plugins' shape: `handling`, `keys`,
-- `actions`, `pointer`, `move` (what `move`'s "auto" does) and `notes`, what an agent should know here), with Roblox's
-- own fields beside them: `keys_reach` (the keys Studio's virtual input sends reach the game: false after one didn't),
-- `needs_focus` (false: they reach it whether or not Studio is in front), `keyboard`, `mouse`, `gamepad`, `touch` (the
-- device Studio simulates is a touch device) and `pad` (the sticks and triggers held, during play). o: {playing,
-- keys_reach, pad}.
function Ui.inputCaps(o: any?): any
	local opts = o or {}
	local playing = opts.playing == true
	local reach = opts.keys_reach ~= false
	local touch = UserInputService.TouchEnabled
	local notes = {}
	local cam = workspace.CurrentCamera
	local scriptable = playing and cam ~= nil and cam.CameraType == Enum.CameraType.Scriptable
	local locked = playing and UserInputService.MouseBehavior ~= Enum.MouseBehavior.Default
	if scriptable then
		table.insert(notes, "The camera is Scriptable: the game's own code moves it, so MouseX/MouseY and the right "
			.. "stick turn the view only if that code reads them; read the game's camera script.")
	elseif locked then
		table.insert(notes, "The game locks the mouse (MouseBehavior " .. UserInputService.MouseBehavior.Name
			.. ": first person, shift lock or its own camera): the view turns with MouseX/MouseY alone.")
	else
		table.insert(notes, "Roblox's default camera turns only while the right mouse button is held (a player "
			.. "drags): MouseX/MouseY (game.turn, look_at) hold it for the turn by themselves; the mouse moved "
			.. "without it (pointer) doesn't turn the view.")
	end
	table.insert(notes, "Point before clicking (pointer): the mouse buttons press where the pointer is (the middle of "
		.. "the view until it is set); ui_act points at an element's middle by itself.")
	table.insert(notes, "Escape, and ButtonB, ButtonStart and DPadUp on a console, are Roblox's own (its menus): a "
		.. "press of one is refused (409).  So are One to Nine while Roblox's backpack hotbar shows: equip a Tool with "
		.. "rc call on the character's Humanoid, EquipTool {tool: its path in the player's Backpack}, and put it away "
		.. "with UnequipTools.")
	if touch then
		table.insert(notes, "Studio simulates a touch device: the game gets touches (the touch request: tap, swipe, "
			.. "a drag on Roblox's on-screen thumbstick walks), the camera turns by a finger's drag on the right half "
			.. "of the screen, not by MouseX; keys still reach the game.")
	end
	if not reach then
		table.insert(notes, "A key didn't reach the game from Studio's virtual input lately: walking, jumping and "
			.. "prompts go in as Roblox's own controls take them meanwhile (W/A/S/D, SpaceBar, a prompt's key); other "
			.. "keys and the game's own bindings don't get them. move with \"via\": \"stick\" still walks.")
	end
	local out: any = { handling = "roblox", keys = true, actions = true, pointer = true, move = "stick", notes = notes,
		keys_reach = reach, needs_focus = false, keyboard = true, mouse = true,
		gamepad = "buttons as KeyCodes; sticks and triggers as Roblox's gamepad controls take them", touch = touch }
	if opts.pad ~= nil then
		out.pad = opts.pad
	end
	return out
end

-- `pointer`: put the mouse at a spot (Ui.spot), without moving it across. -> {"screen_x", "screen_y", "x", "y",
-- "on_screen"}
function Ui.pointer(args: any): (boolean, any)
	local at, visible = Ui.spot(args)
	if at == nil then
		return false, visible
	end
	local ok, why = Keys.pointAt(at)
	if not ok then
		return false, why
	end
	local out = where(at)
	out.on_screen = visible
	return true, out
end

-- `touch`: a finger on the screen. {"action": "tap" (the default) | "double_tap" | "long_press" | "swipe" | "down" |
-- "move" | "up", a spot (Ui.spot), "to": another spot (a swipe's end), "seconds" (a long press's hold, a swipe's
-- length)}. While Studio simulates a touch device (`device`: a phone or tablet; UserInputService.TouchEnabled) Studio
-- makes the virtual mouse's left button the finger: the game gets real touches (TouchStarted, TouchTap, TouchMoved,
-- TouchLongPress, TouchSwipe, a Touch InputObject; the on-screen thumbstick and jump button take them). Otherwise the
-- finger is the mouse's left button, and the answer's `as` says "mouse". "down" leaves the finger down (until "up", or
-- `seconds`, else 10 s) and "move" drags it. -> {"as", "x", "y", "screen_x", "screen_y"}
local finger: { down: boolean, at: Vector2, token: number } = { down = false, at = Vector2.zero, token = 0 }
local LEFT = Enum.UserInputType.MouseButton1

local function fingerDown(at: Vector2): (boolean, string?)
	local ok, why = Keys.pointAt(at)
	if not ok then
		return false, why
	end
	ok, why = Keys.button(at, LEFT, true)
	if not ok then
		return false, why
	end
	finger.down, finger.at = true, at
	finger.token += 1
	return true, nil
end

local function fingerUp()
	if finger.down then
		finger.down = false
		Keys.button(finger.at, LEFT, false)
	end
end

local function fingerTo(to: Vector2, seconds: number)
	local from = finger.at
	local started = os.clock()
	repeat
		RunService.Heartbeat:Wait()
		local k = if seconds > 0 then math.min(1, (os.clock() - started) / seconds) else 1
		local at = from:Lerp(to, k)
		Keys.pointAt(at)
		finger.at = at
	until k >= 1
end

function Ui.touch(args: any): (boolean, any)
	local action = tostring(args.action or "tap")
	local seconds = tonumber(args.seconds)
	if action == "up" then
		fingerUp()
		return true, { as = if UserInputService.TouchEnabled then "touch" else "mouse" }
	end
	local at: Vector2? = finger.at
	if action ~= "move" or args.screen_x ~= nil or args.screen_y ~= nil or args.x ~= nil or args.y ~= nil
		or args.world ~= nil or args.object ~= nil or args.ui ~= nil then
		local spot, why = Ui.spot(args)
		if spot == nil then
			return false, why
		end
		at = spot
	end
	local p = at :: Vector2
	if action == "tap" or action == "double_tap" then
		for i = 1, if action == "double_tap" then 2 else 1 do
			local ok, why = fingerDown(p)
			if not ok then
				return false, why
			end
			for _ = 1, 3 do
				RunService.Heartbeat:Wait()
			end
			fingerUp()
			if i == 1 and action == "double_tap" then
				task.wait(0.08)
			end
		end
	elseif action == "long_press" then
		local ok, why = fingerDown(p)
		if not ok then
			return false, why
		end
		task.wait(seconds or 0.8)
		fingerUp()
	elseif action == "swipe" then
		local to, why = Ui.spot(args.to or {})
		if to == nil then
			return false, "a swipe wants `to`: " .. tostring(why)
		end
		local ok, why2 = fingerDown(p)
		if not ok then
			return false, why2
		end
		fingerTo(to :: Vector2, seconds or 0.25)
		RunService.Heartbeat:Wait()
		fingerUp()
		p = to :: Vector2
	elseif action == "down" then
		local ok, why = fingerDown(p)
		if not ok then
			return false, why
		end
		local token = finger.token
		task.delay(seconds or 10, function()
			if finger.down and finger.token == token then
				fingerUp()
			end
		end)
	elseif action == "move" then
		if not finger.down then
			return false, "no finger is down (touch down first)"
		end
		fingerTo(p, seconds or 0)
	else
		return false, "unknown touch action " .. action
	end
	local out = where(p)
	out.as = if UserInputService.TouchEnabled then "touch" else "mouse"
	if out.as == "mouse" then
		out.note = "Studio isn't simulating a touch device, so the finger went in as the mouse's left button: set a "
			.. "phone or tablet (the `device` request) for touches"
	end
	return true, out
end

function Ui.releaseTouch()
	fingerUp()
end

return Ui
