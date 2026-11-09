-- `record` and `capture`: the game's picture, as the player sees it.
--
-- LÖVE 11 encodes no JPEG, so the pictures go as PNGs (`png` where other engines put `jpeg`): the brain makes each a
-- JPEG as it arrives (autopilot/media.py, jpeg_of). The main thread only asks LÖVE for the frame
-- (love.graphics.captureScreenshot, read back as the frame is presented); a thread of its own scales it down to
-- max_dim (a box filter over the pixels, through LuaJIT's ffi), encodes the PNG and its base64, so the game's frames
-- don't wait for any of it. A recording's frame due while the one before is still in the thread is skipped (a gap in
-- n), as in the other engines. No frames while the game is paused (its picture doesn't change).
--
-- The thread must be gone before LÖVE restarts or quits (a thread still holding LÖVE's modules makes the restart
-- fail): stop() waits for it.

local json = require("autopilot.json")

local media = {}

local JOBS, DONE = "autopilot_media_jobs", "autopilot_media_done"
local STILL_WAIT = 5  -- seconds a still may take before it is answered with an error

local WORKER = [[
require("love.image")
require("love.data")
local ffi = require("ffi")
local jobs = love.thread.getChannel("autopilot_media_jobs")
local done = love.thread.getChannel("autopilot_media_done")

-- The picture with its longer side at most max_dim: each pixel the average of those it covers.
local function fit(img, max_dim)
  local w, h = img:getDimensions()
  local longer = math.max(w, h)
  if longer <= max_dim or img:getFormat() ~= "rgba8" then return img, w, h end
  local f = max_dim / longer
  local nw, nh = math.max(1, math.floor(w * f + 0.5)), math.max(1, math.floor(h * f + 0.5))
  local out = love.image.newImageData(nw, nh, "rgba8")
  local src = ffi.cast("uint8_t*", img:getFFIPointer())
  local dst = ffi.cast("uint8_t*", out:getFFIPointer())
  local xs0, xs1 = {}, {}
  for x = 0, nw - 1 do
    xs0[x] = math.floor(x * w / nw)
    xs1[x] = math.max(xs0[x] + 1, math.floor((x + 1) * w / nw))
  end
  for y = 0, nh - 1 do
    local y0 = math.floor(y * h / nh)
    local y1 = math.max(y0 + 1, math.floor((y + 1) * h / nh))
    for x = 0, nw - 1 do
      local x0, x1 = xs0[x], xs1[x]
      local r, g, b, n = 0, 0, 0, 0
      for sy = y0, y1 - 1 do
        local row = sy * w * 4
        for sx = x0, x1 - 1 do
          local i = row + sx * 4
          r, g, b, n = r + src[i], g + src[i + 1], b + src[i + 2], n + 1
        end
      end
      local o = (y * nw + x) * 4
      dst[o], dst[o + 1], dst[o + 2], dst[o + 3] = r / n, g / n, b / n, 255
    end
  end
  return out, nw, nh
end

while true do
  local job = jobs:demand()
  if job == "quit" then return end
  local ok, a, b, c = pcall(function()
    local img, w, h = fit(job.image, job.max_dim)
    return love.data.encode("string", "base64", img:encode("png")), w, h
  end)
  done:push({kind = job.kind, id = job.id, n = job.n, t = job.t, wall = job.wall, ok = ok, png = ok and a or nil,
    error = not ok and tostring(a) or nil, width = b, height = c})
end
]]

local thread, jobs, done
media.recording, media.fps, media.max_dim, media.n, media.sent = false, 5, 960, 0, 0
local next_at, in_flight, stills = 0, nil, {}

-- Why there is no picture now ("" when there is one).
function media.no_picture()
  if not (love.window and love.window.isOpen()) then return "the game has no window" end
  if not (love.graphics and love.graphics.isActive()) then return "the game's graphics aren't active" end
  return ""
end

function media.available() return media.no_picture() == "" end

local function worker()
  if thread then return end
  jobs, done = love.thread.getChannel(JOBS), love.thread.getChannel(DONE)
  jobs:clear()
  done:clear()
  thread = love.thread.newThread(WORKER)
  thread:start()
end

-- The thread gone (before LÖVE restarts or quits), what it was doing dropped.
function media.stop_worker()
  media.recording = false
  if not thread then return end
  jobs:clear()
  jobs:push("quit")
  thread:wait()
  thread = nil
  done:clear()
  in_flight, stills = nil, {}
end

local function clamp_dim(v, default)
  v = tonumber(v)
  if not v then return default end
  return math.max(16, math.min(8192, math.floor(v)))
end

-- Ask LÖVE for the frame being drawn; it comes to the thread as the frame is presented.
local function take(job)
  worker()
  love.graphics.captureScreenshot(function(image)
    job.image = image
    jobs:push(job)
  end)
end

local function fit_size(max_dim)
  local w, h = love.graphics.getDimensions()
  local longer = math.max(w, h)
  if longer <= max_dim then return w, h end
  local f = max_dim / longer
  return math.max(1, math.floor(w * f + 0.5)), math.max(1, math.floor(h * f + 0.5))
end

-- `record` start: ok, result | false, error, status.
function media.start(args)
  local why = media.no_picture()
  if why ~= "" then return false, "no picture: " .. why, 409 end
  local f = tonumber(args.fps)
  media.fps = (f and f > 0) and math.max(0.1, math.min(60, f)) or 5
  media.max_dim = clamp_dim(args.max_dim, 960)
  media.recording, media.n, media.sent, next_at = true, 0, 0, 0
  local w, h = fit_size(media.max_dim)
  return true, {recording = true, width = w, height = h, format = "png"}
end

function media.stop()
  local frames = media.recording and media.sent or 0
  media.recording = false
  return true, {recording = false, frames = frames}
end

-- `capture`: answered later (media.collect) with the still, or now with an error. wall: now, on the wall clock.
function media.capture(id, args, t, wall)
  local why = media.no_picture()
  if why ~= "" then return false, "no picture: " .. why, 409 end
  local job = {kind = "still", id = id, t = t, wall = wall, max_dim = clamp_dim(args.max_dim, 1920)}
  stills[id] = wall
  take(job)
  return nil
end

-- Every frame, before the game's: the recording's next frame when its time comes. now: the wall clock (seconds since
-- 1970, as the brain stamps frames); t: the game time.
function media.tick(now, paused, t)
  if in_flight and now - in_flight > STILL_WAIT then in_flight = nil end  -- (a frame never presented)
  if not media.recording or paused or now < next_at then return end
  local period = 1 / media.fps
  next_at = (next_at > 0 and now - next_at < period) and next_at + period or now + period
  local n = media.n
  media.n = media.n + 1
  if in_flight then return end  -- the one before is still on its way: this one is skipped (a gap in n)
  in_flight = now
  take({kind = "frame", n = n, t = t, wall = now, max_dim = media.max_dim})
end

-- A JSON object with the picture's base64 put in as it is (the encoder would only copy it).
local function with_png(head, b64)
  local text = json.encode(head)
  return text:sub(1, -2) .. ',"png":"' .. b64 .. '"}'
end

-- What the thread has finished: frames and stills sent (send_text: the link's send of a whole message); stills it
-- couldn't make, or that never came, answered with an error (respond_error(id, error, status)).
function media.collect(now, send_text, respond_error, t_now)
  if thread then
    while true do
      local r = done:pop()
      if not r then break end
      if r.kind == "frame" then
        in_flight = nil
        if r.ok and media.recording then
          media.sent = media.sent + 1
          send_text(with_png({type = "frame", n = r.n, t = r.t, wall = r.wall, width = r.width, height = r.height},
            r.png))
        end
      elseif stills[r.id] then
        stills[r.id] = nil
        if r.ok then
          local result = with_png({width = r.width, height = r.height, t = r.t}, r.png)
          send_text(string.format('{"type":"response","id":%s,"ok":true,"result":%s,"t":%s}', json.encode(r.id),
            result, json.encode(t_now)))
        else
          respond_error(r.id, "no picture: " .. tostring(r.error), 500)
        end
      end
    end
  end
  for id, asked in pairs(stills) do
    if now - asked > STILL_WAIT then
      stills[id] = nil
      respond_error(id, "no picture: the game drew no frame to capture", 409)
    end
  end
end

return media
