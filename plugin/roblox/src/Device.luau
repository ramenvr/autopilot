--!strict
-- The device Studio simulates (StudioDeviceSimulatorService: the Device Simulator, Studio's "Current device" menu over
-- the game view), for the `device` request and a session's start. A phone or tablet makes Studio a touch device: the
-- game sees UserInputService.TouchEnabled, Roblox's on-screen thumbstick and jump button, and the virtual mouse's left
-- button as a finger (Ui.touch). A console gives Roblox's ten-foot interface (its gamepad buttons go in as KeyCodes:
-- Keys.luau). The screen is the device's (the viewport's size, its orientation, a phone's safe area).
--
-- Studio lets a plugin switch it in the edit DataModel and in a play session's client (not its server). The device the
-- user had before Autopilot first switched it is kept in the plugin's settings (every DataModel's), and put back by
-- Device.restore when the run ends.
local GuiService = game:GetService("GuiService")
local UserInputService = game:GetService("UserInputService")

local Settings = require(script.Parent.Settings)

local Device = {}

-- What `device` takes as a kind: the DeviceForm, and the device to take first when Studio has it
local KINDS: { [string]: { form: string, first: { string } } } = {
	phone = { form = "Phone", first = { "iphone_16_pro", "iphone_14", "iphone_13" } },
	mobile = { form = "Phone", first = { "iphone_16_pro", "iphone_14", "iphone_13" } },
	tablet = { form = "Tablet", first = { "ipad_10th_generation", "ipad_air_5th_generation" } },
	console = { form = "Console", first = { "xbox", "ps5" } },
	vr = { form = "VR", first = { "meta_quest_3", "meta_quest_2" } },
}
local NONE = { [""] = true, default = true, none = true, desktop = true, pc = true, computer = true, studio = true }

local plugin: Plugin? = nil

function Device.init(p: Plugin)
	plugin = p
end

local function service(): any
	local ok, svc = pcall(function()
		return game:GetService("StudioDeviceSimulatorService" :: any)
	end)
	return if ok then svc else nil
end

function Device.available(): boolean
	return service() ~= nil
end

local function info(svc: any, id: string): any
	local ok, i = pcall(function()
		return svc:GetDeviceInfoAsync(id)
	end)
	return if ok and type(i) == "table" then i else nil
end

local function formName(form: any): string
	return if typeof(form) == "EnumItem" then form.Name else tostring(form or "")
end

-- Every device Studio has: {id, name, form, width, height}.
function Device.list(): { any }
	local svc = service()
	local out = {}
	if svc == nil then
		return out
	end
	local ok, ids = pcall(function()
		return svc:GetDeviceListAsync()
	end)
	for _, id in ipairs(if ok and type(ids) == "table" then ids else {}) do
		local i = info(svc, id)
		if i then
			table.insert(out, { id = id, name = i.Name, form = formName(i.DeviceForm), width = i.Width,
				height = i.Height })
		end
	end
	return out
end

-- A device id from what the brain said: a kind (phone, tablet, console, vr; desktop or "" for none), an id or a
-- device's name. Returns "default" for none, or nil and why.
function Device.resolve(want: string): (string?, string?)
	local w = string.lower((string.gsub(want, "^%s*(.-)%s*$", "%1")))
	if NONE[w] then
		return "default", nil
	end
	local all = Device.list()
	for _, d in ipairs(all) do
		if string.lower(d.id) == w or string.lower(d.name) == w then
			return d.id, nil
		end
	end
	local kind = KINDS[w]
	if kind then
		for _, id in ipairs(kind.first) do
			for _, d in ipairs(all) do
				if d.id == id then
					return id, nil
				end
			end
		end
		for _, d in ipairs(all) do
			if d.form == kind.form then
				return d.id, nil
			end
		end
	end
	for _, d in ipairs(all) do
		if string.find(string.lower(d.name), w, 1, true) then
			return d.id, nil
		end
	end
	return nil, "No device " .. want .. " in Studio's Device Simulator (phone, tablet, console, vr, desktop, or a "
		.. "name from device list)"
end

-- The device now and the input it gives the game.
function Device.describe(): any
	local cam = workspace.CurrentCamera
	local vp = if cam then cam.ViewportSize else Vector2.zero
	local out: any = { id = "default", simulated = false, touch = UserInputService.TouchEnabled,
		keyboard = UserInputService.KeyboardEnabled, mouse = UserInputService.MouseEnabled,
		viewport = { width = math.round(vp.X), height = math.round(vp.Y) } }
	local okTen, ten = pcall(function()
		return GuiService:IsTenFootInterface()
	end)
	out.ten_foot = okTen and ten == true
	local svc = service()
	if svc == nil then
		out.simulator = false
		return out
	end
	local ok, id = pcall(function()
		return svc:GetDeviceAsync()
	end)
	if ok and type(id) == "string" and id ~= "default" then
		out.id, out.simulated = id, true
		local i = info(svc, id)
		if i then
			out.name, out.form = i.Name, formName(i.DeviceForm)
		end
		local okO, o = pcall(function()
			return svc:GetOrientationAsync()
		end)
		if okO and typeof(o) == "EnumItem" then
			out.orientation = o.Name
		end
	end
	return out
end

-- Switch to a device (Device.resolve's), and an orientation ("portrait", "landscape"). Yields while Studio resizes the
-- view. -> (true, Device.describe()) or (false, why, status)
function Device.set(want: string, orientation: string?): (boolean, any, number?)
	local svc = service()
	if svc == nil then
		return false, "This Studio has no Device Simulator a plugin can use", 501
	end
	local id, why = Device.resolve(want)
	if id == nil then
		return false, why, 400
	end
	-- (a phone's or tablet's orientation: the one asked for, else the game's own, as a player's phone turns to it:
	-- StarterGui.ScreenOrientation; only those turn)
	local i = if id ~= "default" then info(svc, id) else nil
	local form = if i then formName(i.DeviceForm) else ""
	local turns = form == "Phone" or form == "Tablet"
	if orientation and orientation ~= "" and not turns then
		return false, "Only a phone or tablet turns (orientation): " .. tostring(if i then i.Name else "no device"), 400
	end
	if plugin and Settings.get(plugin :: Plugin, "device_before") == nil then
		local ok, cur = pcall(function()
			return svc:GetDeviceAsync()
		end)
		local okO, o = pcall(function()
			return svc:GetOrientationAsync()
		end)
		Settings.set(plugin :: Plugin, "device_before", if ok and type(cur) == "string" then cur else "default")
		Settings.set(plugin :: Plugin, "orientation_before", if okO and typeof(o) == "EnumItem" then o.Name else "")
	end
	local ok, err = pcall(function()
		if id == "default" then
			svc:StopSimulationAsync()
		else
			svc:SetDeviceAsync(id)
		end
	end)
	if not ok then
		return false, "Studio didn't switch the device: " .. tostring(err), 409
	end
	if turns then
		local o = string.lower(orientation or "")
		if o == "" then
			local okG, game0 = pcall(function()
				return game:GetService("StarterGui").ScreenOrientation
			end)
			o = if okG and game0 == Enum.ScreenOrientation.Portrait then "portrait" else "landscape"
		end
		local okO, errO = pcall(function()
			svc:SetOrientationAsync(if o == "portrait" then Enum.ScreenOrientation.Portrait
				elseif o == "landscape_right" or o == "landscaperight" then Enum.ScreenOrientation.LandscapeRight
				else Enum.ScreenOrientation.LandscapeLeft)
		end)
		if not okO then
			return false, "Studio didn't turn the device: " .. tostring(errO), 409
		end
	end
	task.wait(0.3) -- (Studio resizes the view a moment later)
	return true, Device.describe()
end

-- Put back the device the user had before Autopilot switched it (nothing when it never did). Returns whether it could.
function Device.restore(): boolean
	if plugin == nil then
		return true
	end
	local before = Settings.get(plugin :: Plugin, "device_before")
	if before == nil then
		return true
	end
	local o = tostring(Settings.get(plugin :: Plugin, "orientation_before") or "")
	local ok = Device.set(tostring(before), if o == "" or before == "default" then nil else o)
	if not ok then
		ok = Device.set(tostring(before)) -- (a device that doesn't turn)
	end
	if ok then
		Settings.set(plugin :: Plugin, "device_before", nil)
		Settings.set(plugin :: Plugin, "orientation_before", nil)
	end
	return ok
end

-- `device`: {"action": "get" (the default without `device`) | "set" (with `device`) | "list" | "restore", "device",
-- "orientation"}.
function Device.handle(args: any): (boolean, any, number?)
	local action = tostring(args.action or (if args.device ~= nil then "set" else "get"))
	if action == "list" then
		return true, { devices = Device.list(), current = Device.describe() }
	elseif action == "get" then
		return true, Device.describe()
	elseif action == "set" then
		return Device.set(tostring(args.device or ""), if args.orientation then tostring(args.orientation) else nil)
	elseif action == "restore" then
		Device.restore()
		return true, Device.describe()
	end
	return false, "unknown device action " .. action
end

return Device
