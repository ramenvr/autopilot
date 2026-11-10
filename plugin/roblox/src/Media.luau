--!strict
-- The game's picture from Studio itself, for `capture` and `record`: StudioCaptureService, Studio's screenshots for
-- plugins (the play session's 3D view and its UI, as PNG bytes a plugin can read). Studios of today answer "Feature not
-- supported yet" to its permission (Media.available() is then false, and the plugin's hello doesn't say "media"): the
-- agent service then takes the pictures itself, through Studio's MCP server (autopilot/studio_mcp.py). The pictures go
-- to the brain as PNGs ({"png": base64, "width", "height"}); the agent service makes them JPEGs.
--
--   capture   {"max_dim"} -> {"png", "width", "height", "t"}
--   record    {"action": "start", "fps", "max_dim"}: a `frame` {"n", "t", "png", "width", "height"} to the brain at most
--             fps times a second (a picture still on its way skips the next), until {"action": "stop"} or the session's
--             end
local RunService = game:GetService("RunService")

local World = require(script.Parent.World)

local Media = {}

local FPS_MAX = 10
local READY_SECONDS = 5

local function service(): any
	local ok, svc = pcall(function()
		return game:GetService("StudioCaptureService" :: any)
	end)
	return if ok then svc else nil
end

local known: boolean? = nil

-- Whether Studio gives this plugin pictures (asked once: the first time, Studio may ask the user to allow it; yields).
function Media.available(): boolean
	if known ~= nil then
		return known :: boolean
	end
	local svc = service()
	local ok, granted = false, false
	if svc then
		ok, granted = pcall(function()
			return svc:RequestScreenshotPermissionAsync()
		end)
	end
	known = ok and granted == true
	return known :: boolean
end

local CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
local ALPHABET = buffer.fromstring(CHARS)

-- A buffer's bytes as base64.
function Media.base64(b: buffer): string
	local n = buffer.len(b)
	local out = buffer.create(math.ceil(n / 3) * 4)
	local o = 0
	for i = 0, n - 1, 3 do
		local a = buffer.readu8(b, i)
		local c1 = if i + 1 < n then buffer.readu8(b, i + 1) else 0
		local c2 = if i + 2 < n then buffer.readu8(b, i + 2) else 0
		local v = bit32.bor(bit32.lshift(a, 16), bit32.lshift(c1, 8), c2)
		buffer.writeu8(out, o, buffer.readu8(ALPHABET, bit32.extract(v, 18, 6)))
		buffer.writeu8(out, o + 1, buffer.readu8(ALPHABET, bit32.extract(v, 12, 6)))
		buffer.writeu8(out, o + 2, if i + 1 < n then buffer.readu8(ALPHABET, bit32.extract(v, 6, 6)) else 61)
		buffer.writeu8(out, o + 3, if i + 2 < n then buffer.readu8(ALPHABET, bit32.extract(v, 0, 6)) else 61)
		o += 4
	end
	return buffer.tostring(out)
end

-- The game view now as a PNG: (base64, width, height) or (nil, why). Yields until Studio has made it.
function Media.shot(maxDim: number?): (string?, any, number?)
	local svc = service()
	local cam = workspace.CurrentCamera
	if svc == nil or cam == nil then
		return nil, "no picture: this Studio gives a plugin no picture of the game"
	end
	local vp = cam.ViewportSize
	local scale = math.min(1, (maxDim or 960) / math.max(vp.X, vp.Y, 1))
	local size = Vector2.new(math.max(2, math.floor(vp.X * scale)), math.max(2, math.floor(vp.Y * scale)))
	local ok, cap = pcall(function()
		return svc:CaptureScreenshot({ Position = Vector2.zero, CaptureSize = vp, OutputSize = size,
			ResampleMode = Enum.ResamplerMode.Default, Format = Enum.StudioCaptureScreenshotFormat.PNG,
			UICaptureMode = Enum.UICaptureMode.All })
	end)
	if not ok or cap == nil then
		return nil, "no picture: " .. tostring(if ok then "Studio made none" else cap)
	end
	local started = os.clock()
	while cap.BufferStatus == Enum.StudioCaptureBufferStatus.NotStarted
		or cap.BufferStatus == Enum.StudioCaptureBufferStatus.Pending do
		if os.clock() - started > READY_SECONDS then
			return nil, "no picture: Studio didn't finish the screenshot"
		end
		RunService.Heartbeat:Wait()
	end
	if cap.BufferStatus ~= Enum.StudioCaptureBufferStatus.Ready then
		local okE, errors = pcall(function()
			return cap:GetErrors()
		end)
		local why = if okE and type(errors) == "table" then table.concat(errors :: any, "; ") else "an error"
		return nil, "no picture: " .. why
	end
	local okB, b, res = pcall(function()
		return cap:GetBuffer(), cap.Resolution
	end)
	if not okB then
		return nil, "no picture: " .. tostring(b)
	end
	return Media.base64(b), math.round(res.X), math.round(res.Y)
end

function Media.capture(args: any): (boolean, any, number?)
	if not Media.available() then
		return false, "no picture: this Studio gives a plugin no picture of the game", 409
	end
	local png, w, h = Media.shot(tonumber(args.max_dim))
	if png == nil then
		return false, w, 409
	end
	return true, { png = png, width = w, height = h, t = math.round(World.gameTime() * 100) / 100 }
end

local recording: { on: boolean, n: number }? = nil

function Media.stop()
	if recording then
		recording.on = false
		recording = nil
	end
end

function Media.record(args: any, host: any): (boolean, any, number?)
	local action = tostring(args.action or "start")
	if action == "stop" then
		local n = if recording then recording.n else 0
		Media.stop()
		return true, { recording = false, frames = n }
	end
	if not Media.available() then
		return false, "no picture: this Studio gives a plugin no picture of the game", 409
	end
	Media.stop()
	local rec = { on = true, n = 0 }
	recording = rec
	local fps = math.clamp(tonumber(args.fps) or 5, 0.2, FPS_MAX)
	local maxDim = tonumber(args.max_dim)
	task.spawn(function()
		while rec.on do
			local began = os.clock()
			local ok, png, w, h = pcall(Media.shot, maxDim)
			if not ok then
				png = nil
				task.wait(1)
			end
			if not rec.on then
				break
			end
			if png then
				rec.n += 1
				host:send({ type = "frame", n = rec.n, t = host:t(), png = png, width = w, height = h })
			end
			task.wait(math.max(0, 1 / fps - (os.clock() - began)))
		end
	end)
	return true, { recording = true, fps = fps, via = "studio_capture_service" }
end

return Media
