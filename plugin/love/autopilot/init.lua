-- Autopilot for LÖVE 11: lets the Autopilot agent play a LÖVE game.
--
-- Put this folder in the game as autopilot/ and require it first thing in main.lua:
--
--     require("autopilot")
--
-- Until the game is started with playtest arguments it does nothing (no socket, no hooks):
--
--     love . --playtest=verify --playtest-claim="Verify that the score goes up when a coin is taken"
--     love . --playtest-url=ws://127.0.0.1:8765/v1/bridge      (only connect: the agent drives the game)
--
-- Then it connects out to the agent service (`autopilot serve`) over a WebSocket, answers its requests (what is on
-- screen, where things are, the game's Lua values), presses keys and the mouse as a player would, sends the errors the
-- game raises, writes the run's report and quits with its exit code. See plugin/love/README.md.
--
-- The game can help it find its things (optional):
--     local autopilot = require("autopilot")
--     autopilot.player(player)               -- the table the player is
--     autopilot.track(enemy, "Enemy")         -- a thing of a class
--     autopilot.camera(function() return cam.x, cam.y, cam.scale end)  -- the middle of the view, and its zoom

local autopilot = {version = "0.1.0"}
_AUTOPILOT = autopilot

local PROTOCOL = 1
local KEY_TAP_SECONDS, KEY_TAP_FRAMES = 0.05, 2
local RUN_USE_SECONDS = 30
local RESUME_FILE = "autopilot_resume.json"
local RELOAD_CLOSE = 1012

-- the command line ------------------------------------------------------------------------------------------------

-- Every argument whose name, ignoring dashes, underscores and case, starts with "playtest" (Godot's and Unreal's
-- spellings both): {playtesturl = "ws://...", playtest = "verify", ...}.
local function playtest_args()
  local out = {}
  for i = -5, #(arg or {}) do
    local a = arg and arg[i]
    if type(a) == "string" then
      local name, value = a:match("^%-+([%w_%-]+)=(.*)$")
      if not name then name, value = a:match("^%-+([%w_%-]+)$"), "" end
      if name then
        local key = name:gsub("[-_]", ""):lower()
        if key:sub(1, 8) == "playtest" then out[key] = value end
      end
    end
  end
  return out
end

local args = playtest_args()
local enabled = next(args) ~= nil or os.getenv("AUTOPILOT_PLAYTEST_AUTOCONNECT") == "1"

-- The game's own calls, whether or not a run is going (inert until a run makes them mean something).
local world_stub = {}
function autopilot.track(obj, class, opts) if autopilot._world then autopilot._world.track(obj, class, opts) end
  world_stub[#world_stub + 1] = {obj, class, opts} end
function autopilot.untrack(obj) if autopilot._world then autopilot._world.tracked[obj] = nil end end
function autopilot.player(obj) if autopilot._world then autopilot._world.chosen = obj else world_stub.player = obj end end
function autopilot.camera(c) if autopilot._world then autopilot._world.camera_spec = c else world_stub.camera = c end end

if not enabled then
  return autopilot
end

local socket = require("socket")
local json = require("autopilot.json")
local ws = require("autopilot.ws")
local keys = require("autopilot.keys")
local world = require("autopilot.world")
local rc = require("autopilot.rc")
local codex = require("autopilot.codex")
local media = require("autopilot.media")

autopilot._world = world
function world.track(obj, class, opts)
  if type(obj) ~= "table" then return end
  if not world.tracked[obj] then world.tracked_list[#world.tracked_list + 1] = obj end
  world.tracked[obj] = {class = class, center = opts and opts.center}
end
for _, t in ipairs(world_stub) do world.track(t[1], t[2], t[3]) end
world.chosen = world_stub.player
world.camera_spec = world_stub.camera
world.hook_physics()

local function now() return socket.gettime() end

-- the log: what the module prints, also into --playtest-log's file --------------------------------------------------

do
  local path = args.playtestlog
  if path and path ~= "" then
    local f = io.open(path, "a")
    if f then
      local orig = print
      print = function(...)  -- luacheck: ignore
        orig(...)
        local parts = {}
        for i = 1, select("#", ...) do parts[#parts + 1] = tostring(select(i, ...)) end
        f:write(table.concat(parts, "\t"), "\n")
        f:flush()
      end
    end
  end
end

local function say(s) print("AutopilotPlaytest: " .. s) end

-- where the agent service is ---------------------------------------------------------------------------------------

local function settings_dir()
  if love.system.getOS() == "Windows" then
    return (os.getenv("LOCALAPPDATA") or ((os.getenv("USERPROFILE") or "") .. "\\AppData\\Local")) .. "\\Autopilot"
  elseif love.system.getOS() == "OS X" then
    return (os.getenv("HOME") or "") .. "/Library/Application Support/Autopilot"
  end
  return (os.getenv("XDG_CONFIG_HOME") or ((os.getenv("HOME") or "") .. "/.config")) .. "/Autopilot"
end

local function read_json_file(path)
  local f = io.open(path, "r")
  if not f then return nil end
  local text = f:read("*a")
  f:close()
  local ok, v = pcall(json.decode, text)
  return ok and v or nil
end

local sep = love.system.getOS() == "Windows" and "\\" or "/"
local prefs = read_json_file(settings_dir() .. sep .. "preferences.json") or {}

local function brain_url()
  if args.playtesturl and args.playtesturl ~= "" then return args.playtesturl end
  local env = os.getenv("AUTOPILOT_PLAYTEST_URL")
  if env and env ~= "" then return env end
  local brain = read_json_file(settings_dir() .. sep .. "brain.json")
  if brain and type(brain.url) == "string" then return brain.url end
  return "ws://127.0.0.1:8765/v1/bridge"
end

local function flag(v, default)
  if v == nil then return default end
  return v ~= "0" and v:lower() ~= "false"
end

-- the run asked on the command line ---------------------------------------------------------------------------------

local cli_run = {}
do
  local kind = (args.playtest or ""):lower()
  if kind ~= "" then
    cli_run = {kind = kind}
    if args.playtestclaim then cli_run.claim = args.playtestclaim end
    if args.playtestgoal then cli_run.goal = args.playtestgoal end
    if args.playtestminutes then cli_run.minutes = tonumber(args.playtestminutes) end
    if args.playtesttimelimit then cli_run.time_limit_minutes = tonumber(args.playtesttimelimit) end
    if args.playteststeps then cli_run.steps = tonumber(args.playteststeps) end
    if args.playtestattempts then cli_run.attempts = tonumber(args.playtestattempts) end
    if args.playtestbrief then cli_run.brief = args.playtestbrief end
    if args.playtestbrieffile then
      local f = io.open(args.playtestbrieffile, "r")
      if f then cli_run.brief = f:read("*a"):match("^%s*(.-)%s*$") f:close() end
    end
  end
end
local has_run = next(cli_run) ~= nil
local stay_open = args.playteststayopen ~= nil
local connect_timeout = tonumber(args.playtestconnecttimeout) or 120

-- the state -------------------------------------------------------------------------------------------------------

local S = {
  link = nil, active = false, welcomed = false, run_active = false, run_asked = false, exiting = false,
  session = nil, last_request_at = -1, game_time = 0, frame = 0, events = {}, pending = {},
  held = {}, lease = 1.0, pointer = nil, paused = false, fixed_dt = nil, heartbeat_at = 0,
  texts = {}, texts_now = {}, retry_at = 0, backoff = 1, deadline = now() + connect_timeout,
  resume = nil, started = now(), errors_seen = {}, nan_told = {},
}

-- What a restart (session restart: LÖVE starts the game over in the same process) left for the new game: the
-- session to resume and the request to answer.
do
  local text = love.filesystem.read(RESUME_FILE)
  if text then
    love.filesystem.remove(RESUME_FILE)
    local ok, r = pcall(json.decode, text)
    if ok and type(r) == "table" and r.session then
      S.resume = r
      S.run_asked = r.run_asked == true
    end
  end
end

local function t_value() return world.r2(S.game_time) end

local function project_name()
  local title = love.window and love.window.getTitle and love.window.getTitle() or ""
  if title ~= "" and title ~= "Untitled" then return title end
  return love.filesystem.getIdentity()
end

local function project_dir()
  local src = love.filesystem.getSource()
  if src:sub(1, 1) ~= "/" and not src:match("^%a:[\\/]") then
    src = love.filesystem.getWorkingDirectory() .. "/" .. src
  end
  return (src:gsub("/%./", "/"):gsub("/%.$", ""))
end

-- sending ---------------------------------------------------------------------------------------------------------

local function send(frame)
  if S.link then S.link:send(json.encode(frame)) end
end

local function send_text(text)
  if S.link then S.link:send(text) end
end

local function respond(id, result)
  send({type = "response", id = id, ok = true, result = result == nil and json.object({}) or result, t = t_value()})
end

local function respond_error(id, err, status)
  send({type = "response", id = id, ok = false, status = status or 400, error = tostring(err), t = t_value()})
end

local function emit(event)
  if S.active then S.events[#S.events + 1] = event end
end

local function flush_events()
  if #S.events > 0 and S.active then
    local batch = S.events
    S.events = {}
    send({type = "events", events = json.array(batch), t = t_value()})
  end
end

-- the player's hands ----------------------------------------------------------------------------------------------

local held_keys, held_scancodes, held_buttons = {}, {}, {}

local function mouse_at()
  if S.pointer then return S.pointer[1], S.pointer[2] end
  return love.graphics.getWidth() / 2, love.graphics.getHeight() / 2
end

local function deliver(spec, pressed, value)
  value = value or 1
  if spec.kind == "key" then
    held_keys[spec.key] = pressed or nil
    held_scancodes[spec.scancode] = pressed or nil
    love.event.push(pressed and "keypressed" or "keyreleased", spec.key, spec.scancode, false)
    if pressed and #spec.key == 1 then love.event.push("textinput", spec.key) end
    if pressed and spec.key == "space" then love.event.push("textinput", " ") end
  elseif spec.kind == "mouse" then
    held_buttons[spec.button] = pressed or nil
    local x, y = mouse_at()
    love.event.push(pressed and "mousepressed" or "mousereleased", x, y, spec.button, false, 1)
  elseif spec.kind == "wheel" then
    if pressed then love.event.push("wheelmoved", 0, spec.dir ~= 0 and spec.dir or (value > 0 and 1 or -1)) end
  elseif spec.kind == "axis" then
    -- (the brain's MouseY is positive up, LÖVE's dy positive down)
    local dx, dy = 0, 0
    if spec.axis == "MouseX" then dx = value else dy = -value end
    local x, y = mouse_at()
    S.pointer = {x + dx, y + dy}
    love.event.push("mousemoved", x + dx, y + dy, dx, dy, false)
  end
end

local function release_all()
  for name, h in pairs(S.held) do
    if h.spec.kind ~= "axis" then deliver(h.spec, false) end
  end
  S.held = {}
end

-- What the game reads as the keyboard and mouse: the real ones, and what the agent holds.
do
  local kb, mouse = love.keyboard, love.mouse
  local is_down, is_scancode_down = kb.isDown, kb.isScancodeDown
  kb.isDown = function(...)
    for i = 1, select("#", ...) do
      if held_keys[select(i, ...)] then return true end
    end
    return is_down(...)
  end
  kb.isScancodeDown = function(...)
    for i = 1, select("#", ...) do
      if held_scancodes[select(i, ...)] then return true end
    end
    return is_scancode_down(...)
  end
  if mouse then
    local m_down, get_pos, get_x, get_y = mouse.isDown, mouse.getPosition, mouse.getX, mouse.getY
    mouse.isDown = function(...)
      for i = 1, select("#", ...) do
        if held_buttons[select(i, ...)] then return true end
      end
      return m_down(...)
    end
    mouse.getPosition = function() if S.pointer then return S.pointer[1], S.pointer[2] end return get_pos() end
    mouse.getX = function() if S.pointer then return S.pointer[1] end return get_x() end
    mouse.getY = function() if S.pointer then return S.pointer[2] end return get_y() end
  end
end

-- The texts the game draws (love.graphics.print / printf), where they land on screen: `screen`. Each piece is kept
-- with its place and width on screen; screen_texts() joins the pieces that sit side by side on a line (a terminal-like
-- game that prints one glyph at a time reads as its words, not as letters).
do
  local g = love.graphics
  local print_, printf_ = g.print, g.printf
  local function text_of(t)
    if type(t) == "table" then  -- coloredtext: {color, "text", color, "text"...}
      local out = {}
      for _, x in ipairs(t) do
        if type(x) == "string" then out[#out + 1] = x end
      end
      return table.concat(out)
    end
    return tostring(t)
  end
  -- one line of text at (x, y) in the current transform, `width` wide in it (nil: as the font measures it)
  local function note(text, font, x, y, width)
    if text:match("^%s*$") then return end
    font = font or g.getFont()
    local ok, w = pcall(font.getWidth, font, text)
    w = width or (ok and w or 0)
    local h = font:getHeight()
    local sx, sy = g.transformPoint(x, y)
    local ex, ey = g.transformPoint(x + w, y + h)
    -- (a glyph cell, as a terminal lays text out: the font's widest common glyph, on screen)
    local cx = g.transformPoint(x + font:getWidth("M"), y)
    S.texts_now[#S.texts_now + 1] = {text = text, x = sx, y = sy, ex = ex, h = math.max(1, ey - sy), n = #text,
      cell = math.max(1, cx - sx)}
  end
  local function on_print(t, font, x, y)
    if not S.active then return end
    x, y = tonumber(x) or 0, tonumber(y) or 0
    local line = 0
    font = font or g.getFont()
    for piece in (text_of(t) .. "\n"):gmatch("(.-)\n") do
      note(piece, font, x, y + line * font:getHeight() * font:getLineHeight())
      line = line + 1
    end
  end
  local function on_printf(t, font, x, y, limit, align)
    if not S.active then return end
    x, y, limit = tonumber(x) or 0, tonumber(y) or 0, tonumber(limit) or 0
    font = font or g.getFont()
    local ok, _, lines = pcall(font.getWrap, font, text_of(t), limit)
    if not ok then return end
    for i, piece in ipairs(lines) do
      local w = font:getWidth(piece)
      local lx = x
      if align == "center" then lx = x + (limit - w) / 2 elseif align == "right" then lx = x + limit - w end
      note(piece, font, lx, y + (i - 1) * font:getHeight() * font:getLineHeight(), w)
    end
  end
  -- (print(text, x, y, ...) and print(text, font, x, y, ...); printf(text, x, y, limit, align, ...) and with a font)
  g.print = function(t, a, b, c, ...)
    if type(a) == "userdata" then on_print(t, a, b, c) else on_print(t, nil, a, b) end
    return print_(t, a, b, c, ...)
  end
  g.printf = function(t, a, b, c, d, e, ...)
    if type(a) == "userdata" then on_printf(t, a, b, c, d, e) else on_printf(t, nil, a, b, c, d) end
    return printf_(t, a, b, c, d, e, ...)
  end
end

-- The texts of the last frame drawn, as {"Text@x,y" = text}: pieces on one line (their tops within a quarter of their
-- height) that follow each other closely are one text. Glyphs drawn one at a time are laid out in cells, as a terminal
-- does: one cell from the glyph before is the next letter, each cell more a space. Longer pieces are joined when less
-- than two cells apart, with a space between. A piece drawn over the one before it (a shadow, an outline) is dropped.
local function screen_texts(pieces)
  local sorted = {}
  for i, p in ipairs(pieces) do sorted[i] = p end
  table.sort(sorted, function(a, b)
    if math.abs(a.y - b.y) > math.min(a.h, b.h) / 4 then return a.y < b.y end
    return a.x < b.x
  end)
  local out, run = {}, nil
  local function close()
    if run then
      local key = string.format("Text@%d,%d", math.floor(run.x + 0.5), math.floor(run.y + 0.5))
      out[key] = table.concat(run.parts)
    end
  end
  for _, p in ipairs(sorted) do
    local same_line = run and math.abs(p.y - run.y) <= math.min(p.h, run.h) / 4
    local cell = p.cell
    if same_line and p.x < run.last_x + cell / 2 and p.text == run.last_text then
      -- (the same text over itself, a pixel or two off: a shadow or an outline)
    elseif same_line and run.last_n == 1 and p.n == 1 and p.x - run.last_x <= 3.5 * cell then
      local cells = math.floor((p.x - run.last_x) / cell + 0.5)
      if cells > 1 then run.parts[#run.parts + 1] = string.rep(" ", cells - 1) end
      run.parts[#run.parts + 1] = p.text
    elseif same_line and p.x - run.ex <= 2 * cell then
      if p.x - run.ex > cell / 4 then run.parts[#run.parts + 1] = " " end
      run.parts[#run.parts + 1] = p.text
    else
      close()
      run = {x = p.x, y = p.y, h = p.h, ex = p.ex, parts = {p.text}}
    end
    if run.parts[#run.parts] == p.text or #run.parts == 1 then
      run.last_x, run.last_n, run.last_text = p.x, p.n, p.text
      run.ex = math.max(run.ex, p.ex)
    end
  end
  close()
  return out
end

-- Fixed time steps (`timestep`): every frame's dt is 1/fps, whatever the wall time.
do
  local step, get_delta = love.timer.step, love.timer.getDelta
  love.timer.step = function() local dt = step() if S.fixed_dt then return S.fixed_dt end return dt end
  love.timer.getDelta = function() if S.fixed_dt then return S.fixed_dt end return get_delta() end
end

-- the ops ---------------------------------------------------------------------------------------------------------

local DEFERRED = {}

local controls_cache
local function controls()
  if not controls_cache then
    controls_cache = {}
    for _, k in ipairs(codex.keys_read()) do
      controls_cache[#controls_cache + 1] = {action = k, keys = json.array({keys.unreal_name(k)})}
    end
  end
  return json.array(controls_cache)
end

local function move_keys()
  local read = {}
  for _, c in ipairs(controls()) do read[c.action] = true end
  if read.w or read.a or read.s or read.d then return {right = "d", left = "a", up = "w", down = "s"} end
  return {right = "right", left = "left", up = "up", down = "down"}
end

local function input_caps()
  local keyset = move_keys()
  return {handling = "love", keys = true, actions = false, pointer = true, move = "keys",
    notes = json.array({
      "LÖVE has no input actions: press the keys the game's code reads (context's controls; move presses "
        .. (keyset.up == "w" and "W/A/S/D" or "the arrow keys") .. ").",
      "Keys reach the game both as events (love.keypressed) and as state (love.keyboard.isDown); the mouse buttons "
        .. "press where the pointer is (the middle of the window until it is set), and love.mouse.getPosition "
        .. "reports the pointer.",
    })}
end

-- The names of main.lua's locals the module searches (/@local/<name>), for the agent to start from.
local function locals_of_main()
  local names = {}
  for name in pairs(world.locals()) do names[#names + 1] = name end
  table.sort(names)
  return json.array(names)
end

local function context()
  local p = world.player()
  local cam = world.camera_object()
  return {world = "main", scene = "main", character = p and world.path_of(p) or "", controller = "",
    input_subsystem = "", camera_manager = cam and world.path_of(cam) or "", camera = cam and world.path_of(cam) or "",
    dimension = "2d", game_name = project_name(), host = "game", engine = "love", session_running = true,
    paused = S.paused, game_time = t_value(), project_dir = project_dir(), controls = controls(),
    input = input_caps(), net = {mode = "standalone"}, player_found_by = world.found_by, locals = locals_of_main()}
end

local function session_op(id, a)
  local action = tostring(a.action or "status")
  local map = a.map
  if map ~= nil and map ~= json.null and map ~= "" and map ~= "main" then
    return false, "No scene " .. tostring(map) .. " (a LÖVE game has no scenes: restart starts the game over)", 404
  end
  if action ~= "restart" then return true, {running = true, loading = false, scene = "main"} end
  -- LÖVE starts the game over in this process (love.event.quit("restart")): this connection closes as coming
  -- back, and the new game answers this request once it has connected again
  release_all()
  love.filesystem.write(RESUME_FILE, json.encode({session = S.session, id = id, run_asked = S.run_asked,
    run_active = S.run_active}))
  S.restarting = true
  if S.link then S.link:close(RELOAD_CLOSE) end
  S.link = nil
  love.event.quit("restart")
  return DEFERRED
end

local function key_op(id, a)
  local mode = tostring(a.mode or "press")
  if tonumber(a.lease) and tonumber(a.lease) > 0 then S.lease = math.max(0.2, math.min(10, tonumber(a.lease))) end
  if mode == "release_all" then
    release_all()
    return true, {}
  end
  local name = tostring(a.key or "")
  local spec = keys.spec(name)
  if not spec then return false, "Unknown key " .. name end
  local value = tonumber(a.value) or 1
  if mode == "release" then
    local h = S.held[name]
    if h then
      if spec.kind ~= "axis" then deliver(spec, false) end
      S.held[name] = nil
    end
    return true, {}
  end
  if mode == "hold" then
    if not S.held[name] then
      S.held[name] = {spec = spec}
      if spec.kind ~= "axis" then deliver(spec, true, value) end
    end
    local h = S.held[name]
    h.value, h.rate, h.renewed = value, a.rate == true, now()
    local seconds = tonumber(a.seconds) or 0
    h.until_t = seconds > 0 and S.game_time + seconds or nil
    return true, {}
  end
  -- a tap: down, and up again a moment (and two frames) later
  if spec.kind == "axis" or spec.kind == "wheel" then
    deliver(spec, true, value)
    return true, {}
  end
  deliver(spec, true, value)
  local started, frame = now(), S.frame
  S.pending[#S.pending + 1] = function(t)
    if t - started < KEY_TAP_SECONDS or S.frame < frame + KEY_TAP_FRAMES then return false end
    if not S.held[name] then deliver(spec, false) end
    respond(id, {})
    return true
  end
  return DEFERRED
end

local function tick_keys(t, dt)
  for name, h in pairs(S.held) do
    local expired
    if h.until_t then expired = S.game_time >= h.until_t else expired = t - h.renewed > S.lease end
    if expired then
      if h.spec.kind ~= "axis" then deliver(h.spec, false) end
      S.held[name] = nil
    elseif h.spec.kind == "axis" then
      deliver(h.spec, true, h.rate and h.value * dt or h.value)
    end
  end
end

local function pointer_op(a)
  local w, h = love.graphics.getDimensions()
  local px, py
  if a.object ~= nil and a.object ~= json.null and a.object ~= "" then
    local obj = world.resolve(a.object)
    local pl = world.place(obj)
    if not pl then return false, "Object " .. tostring(a.object) .. " does not exist", 404 end
    local _, _, x, y = world.to_screen(pl.x, pl.y)
    px, py = x, y
  elseif type(a.world) == "table" then
    local x, y = world.from_kit(a.world)
    local _, _, sx, sy = world.to_screen(x, y)
    px, py = sx, sy
  elseif a.screen_x ~= nil or a.screen_y ~= nil then
    px = ((tonumber(a.screen_x) or 0) + 1) / 2 * w
    py = (1 - (tonumber(a.screen_y) or 0)) / 2 * h
  else
    return false, "pointer needs screen_x and screen_y, world or object"
  end
  local ox, oy = mouse_at()
  S.pointer = {px, py}
  love.event.push("mousemoved", px, py, px - ox, py - oy, false)
  return true, {screen_x = world.r2(px / w * 2 - 1), screen_y = world.r2(1 - py / h * 2), x = math.floor(px + 0.5),
    y = math.floor(h - py + 0.5), on_screen = px >= 0 and px <= w and py >= 0 and py <= h}
end

local function move_op(id, a)
  local via = tostring(a.via or "auto")
  local dir = a.direction or {}
  local scale = tonumber(a.scale) or 1
  local dx, dz = (tonumber(dir.X or dir.x) or 0) * scale, (tonumber(dir.Z or dir.z) or 0) * scale
  local len = math.sqrt(dx * dx + dz * dz)
  local seconds = tonumber(a.seconds) or 0.5
  local p = world.player()
  local start = p and world.place(p)
  if via == "stick" then return false, "move via stick: LÖVE has no input actions (via keys or controller)" end
  if via == "controller" and not start then return false, "move via controller: no player" end
  local started = now()
  local specs = {}
  if via ~= "controller" and len > 0 then
    local ks = move_keys()
    if dx > 0.38 * len then specs[#specs + 1] = keys.spec(ks.right) end
    if dx < -0.38 * len then specs[#specs + 1] = keys.spec(ks.left) end
    if dz > 0.38 * len then specs[#specs + 1] = keys.spec(ks.up) end
    if dz < -0.38 * len then specs[#specs + 1] = keys.spec(ks.down) end
    for _, s in ipairs(specs) do deliver(s, true) end
  end
  local speed
  if via == "controller" then
    local ok, m = world.movement()
    speed = ok and tonumber(m.walk_speed) or 200
  end
  local last = now()
  S.pending[#S.pending + 1] = function(t)
    if via == "controller" and len > 0 and p then
      local step = speed * (t - last) / len
      local pl = world.place(p)
      if pl and pl.body then pl.body:setPosition(pl.x + dx * step, pl.y - dz * step)
      elseif pl then p.x, p.y = p.x + dx * step, p.y - dz * step end
    end
    last = t
    if t - started < seconds then return false end
    for _, s in ipairs(specs) do deliver(s, false) end
    local moved = 0
    local fin = p and world.place(p)
    if start and fin then moved = math.sqrt((fin.x - start.x) ^ 2 + (fin.y - start.y) ^ 2) end
    respond(id, {moved = world.r2(moved), via = via == "controller" and "controller" or "keys"})
    return true
  end
  return DEFERRED
end

local function watch_op(id, a)
  local st = world.watch_start(a)
  local seconds = tonumber(a.seconds) or 0.6
  local started = now()
  world.watch_tick(st, S.game_time)
  S.pending[#S.pending + 1] = function(t)
    world.watch_tick(st, S.game_time)
    if t - started < seconds then return false end
    respond(id, world.watch_finish(st))
    return true
  end
  return DEFERRED
end

local OPS = {
  context = function(_, a)
    if a.player ~= nil then world.chosen = (a.player ~= "" and a.player ~= json.null) and a.player or nil end
    return true, context()
  end,
  session = session_op,
  levels = function() return true, {levels = json.array({"main"}), open = "main", playing = "main"} end,
  ping = function() return true, {game_time = t_value(), wall_time = now()} end,
  rc = function(_, a) return rc.handle(tostring(a.verb or ""), a.body) end,
  key = key_op,
  input = function(_, a)
    return false, "No input action named " .. tostring(a.action) .. " (LÖVE has no input actions: press the keys)"
  end,
  pointer = function(_, a) return pointer_op(a) end,
  move = move_op,
  watch = watch_op,
  view = function(_, a) return world.view(a) end,
  screen = function()
    local texts = json.object({})
    for k, v in pairs(screen_texts(S.texts)) do texts[k] = v end
    return true, {texts = texts}
  end,
  ui = function() return true, {elements = json.array({})} end,
  ui_act = function()
    return false, "No UI controls: a LÖVE game draws its own (point at it, then press LeftMouseButton)", 404
  end,
  pause = function(_, a)
    S.paused = a.paused == true
    return true, {paused = S.paused}
  end,
  timestep = function(_, a)
    if a.fixed then
      local fps = math.max(1, tonumber(a.fps) or 30)
      S.fixed_dt = 1 / fps
      return true, {note = string.format("each frame's dt is 1/%d s (love.timer.step), whatever the wall time", fps)}
    end
    S.fixed_dt = nil
    return true, {}
  end,
  detectors = function() return true, {} end,
  record = function(_, a)
    if a.action == "stop" then return media.stop() end
    return media.start(a)
  end,
  capture = function(id, a)
    local ok, err, status = media.capture(id, a, t_value(), now())
    if ok == nil then return DEFERRED end
    return ok, err, status
  end,
  codex_manifest = function() return true, codex.manifest(project_name()) end,
  codex_read = function(_, a) return true, codex.read(a.paths, a.max_bytes) end,
  find = function(_, a) return world.find(a) end,
  transforms = function(_, a) return world.transforms(a, now()) end,
  pose = function() return world.pose(now()) end,
  movement = function() return world.movement() end,
}

local function handle_request(frame)
  S.last_request_at = now()
  local id, op = frame.id, tostring(frame.op or "")
  local a = type(frame.args) == "table" and frame.args or {}
  local fn = OPS[op]
  if not fn then return respond_error(id, "unknown op " .. op, 400) end
  local res = {xpcall(fn, debug.traceback, id, a)}
  if not res[1] then return respond_error(id, "the LÖVE module failed on " .. op .. ": " .. tostring(res[2]), 500) end
  if res[2] == DEFERRED then return end
  if res[2] then respond(id, res[3]) else respond_error(id, res[3], res[4]) end
end

-- the run's end ---------------------------------------------------------------------------------------------------

local function mkdirs(dir)
  if love.system.getOS() == "Windows" then
    os.execute('mkdir "' .. dir:gsub("/", "\\") .. '" 2>nul')
  else
    os.execute("mkdir -p '" .. dir:gsub("'", "'\\''") .. "'")
  end
end

local function write_report(result, files)
  local dir = args.playtestreportdir
  if not dir or dir == "" then
    local rel = "playtest/" .. os.date("%Y%m%d-%H%M%S")
    love.filesystem.createDirectory(rel)
    dir = love.filesystem.getSaveDirectory() .. "/" .. rel
  else
    mkdirs(dir)
  end
  local function put(name, text)
    local f = io.open(dir .. "/" .. name, "wb")
    if f then f:write(text) f:close() end
  end
  if type(files) == "table" then
    for name, text in pairs(files) do
      local clean = tostring(name):match("[^/\\]+$")
      if clean and clean ~= "." and clean ~= ".." then put(clean, tostring(text)) end
    end
  end
  put("result.json", json.encode(result))
  return dir
end

local function exit(code)
  if S.exiting then return end
  S.exiting = true
  say("exiting with code " .. code)
  release_all()
  if S.link then S.link:close(1000) end
  love.event.quit(code)
end

local function fail_run(message)
  say("run failed: " .. message)
  local dir = write_report({kind = cli_run.kind or "", error = message, exit_code = 1}, {})
  say("Report: " .. dir)
  exit(1)
end

local function handle_result(frame)
  S.run_active = false
  local result = type(frame.result) == "table" and frame.result or {}
  local dir = write_report(result, frame.files)
  say("run finished. Report: " .. dir)
  if not has_run or stay_open then return end
  local code = 0
  if result.exit_code then
    code = tonumber(result.exit_code) or 1
  elseif result.verdict then
    code = ({CONFIRMED = 0, DENIED = 2})[tostring(result.verdict)] or 3
  elseif type(result.bugs) == "table" and #result.bugs > 0 then
    code = 2
  end
  exit(code)
end

-- the link --------------------------------------------------------------------------------------------------------

local function hello()
  local h = {type = "hello", protocol = PROTOCOL, plugin_version = autopilot.version, engine = "love",
    engine_version = string.format("%d.%d.%d", love.getVersion()), project = project_name(), host = "game",
    features = json.array(media.available() and {"timed_hold", "pointer", "media"} or {"timed_hold", "pointer"}),
    share = flag(args.playtestshare, prefs.share_data ~= false), input = input_caps()}
  local research = args.playtestresearch
  if research then h.research = flag(research, true) elseif type(prefs.research_agent) == "boolean" then
    h.research = prefs.research_agent
  end
  if S.resume then h.resume = S.resume.session end
  if has_run and not S.run_asked then h.run = cli_run end
  return h
end

local function connect()
  local headers = {}
  local token = args.playtesttoken or os.getenv("AUTOPILOT_PLAYTEST_TOKEN")
  if token and token ~= "" then headers.Authorization = "Bearer " .. token end
  S.url = brain_url()
  S.link = ws.connect(S.url, headers)
  S.sent_hello = false
end

local function on_frame(frame)
  local kind = frame.type
  if kind == "request" then
    handle_request(frame)
  elseif kind == "welcome" then
    S.active, S.welcomed, S.session = true, true, frame.session
    S.last_request_at = -1
    S.backoff = 1
    if has_run then S.run_asked = true end
    S.run_active = has_run or frame.role == "cli" or (S.resume and S.resume.run_active) or false
    say("connected (" .. tostring(frame.role) .. ")")
    emit({kind = "session", t = t_value(), running = true, scene = "main"})
    if S.resume and S.resume.id then respond(S.resume.id, {running = true, scene = "main"}) end
    S.resume = nil
  elseif kind == "status" then
    say(tostring(frame.text or ""))
  elseif kind == "finding" then
    say("finding: " .. tostring((frame.finding or {}).summary or ""))
  elseif kind == "result" then
    handle_result(frame)
  elseif kind == "error" then
    say("the brain sent an error: " .. tostring(frame.message))
    if S.link then S.link:close(1000) end
    S.link, S.active = nil, false
    if has_run and not stay_open then fail_run("the brain sent an error: " .. tostring(frame.message)) end
  end
end

local function poll_link(t)
  if S.exiting or S.restarting then return end
  -- a run asked on the command line waits for the brain's welcome so long (it may still be starting), then fails
  if has_run and not S.welcomed and not S.run_asked and t > S.deadline and not stay_open then
    return fail_run(string.format("no welcome from the brain at %s within %d s", tostring(S.url or brain_url()),
      connect_timeout))
  end
  if not S.link then
    if t >= S.retry_at then connect() end
    return
  end
  if S.link.state == "open" and not S.sent_hello then
    S.link:send(json.encode(hello()))
    S.sent_hello = true
  end
  for _, m in ipairs(S.link:poll()) do
    if m.closed then
      local welcomed = S.welcomed
      S.link, S.active, S.welcomed, S.run_active = nil, false, false, false
      release_all()
      media.stop()  -- (a recording ends with its connection: the brain asks again on the next)
      if S.run_asked and not stay_open then
        return fail_run("the connection to the brain closed before the run's result")
      elseif has_run and m.was_connected and not welcomed and not stay_open then
        return fail_run("the brain closed the connection without taking the run (" .. tostring(m.why) .. ")")
      end
      if welcomed then say("disconnected: " .. tostring(m.why)) end
      S.retry_at = t + S.backoff
      S.backoff = math.min(15, S.backoff * 2)
      return
    end
    local ok, frame = pcall(json.decode, m.text)
    if ok and type(frame) == "table" then on_frame(frame) end
    if not S.link then return end
    if S.link.state == "open" and not S.sent_hello then
      S.link:send(json.encode(hello()))
      S.sent_hello = true
    end
  end
end

-- the detectors (only while a run uses the game) --------------------------------------------------------------------

local function finding(f)
  emit({kind = "finding", t = t_value(), finding = f})
end

local function lua_error(msg)
  local key = tostring(msg):match("^[^\n]*")
  local t = now()
  emit({kind = "log", t = t_value(), verbosity = "Error", category = "Lua", message = tostring(msg):sub(1, 2000)})
  if (S.errors_seen[key] or -100) + 5 > t then return end
  S.errors_seen[key] = t
  finding({detector = "log_error", severity = "error", summary = "Lua error: " .. key:sub(1, 300),
    details = {message = tostring(msg):sub(1, 2000)}})
end

local function detect(real_dt)
  if real_dt > 0.75 and now() - S.started > 3 then
    finding({detector = "hitch", severity = "warning", summary = string.format("A frame took %.2f s", real_dt)})
  end
  local p = world.player()
  local pl = p and world.place(p)
  if pl and (pl.x ~= pl.x or pl.y ~= pl.y or math.abs(pl.x) == math.huge or math.abs(pl.y) == math.huge) then
    if not S.nan_told[p] then
      S.nan_told[p] = true
      finding({detector = "nan", severity = "error", summary = "The player has a NaN or infinite position"})
    end
  end
end

local function run_using_game(t)
  return S.active and (S.run_active or (S.last_request_at >= 0 and t - S.last_request_at < RUN_USE_SECONDS))
end

-- the frame -------------------------------------------------------------------------------------------------------

local function noop() end
local last_real = now()

local function frame(step)
  local t = now()
  local real_dt = t - last_real
  last_real = t
  S.frame = S.frame + 1
  world.tick(t)
  poll_link(t)
  if S.active then
    tick_keys(t, real_dt)
    local i = 1
    while i <= #S.pending do
      local ok, done = xpcall(S.pending[i], debug.traceback, t)
      if not ok then
        say("a deferred answer failed: " .. tostring(done))
        done = true
      end
      if done then table.remove(S.pending, i) else i = i + 1 end
    end
    media.tick(t, S.paused, t_value())
  end
  local real_update = love.update
  if S.paused then love.update = noop end
  S.texts_now = {}
  local result
  if S.active then
    local ok, r = xpcall(step, debug.traceback)
    if ok then
      result = r
    else
      lua_error(r)  -- (the game goes on: the agent sees the error, as an editor's debugger would let it continue)
      if love.graphics and love.graphics.isActive() then pcall(love.graphics.present) end
    end
  else
    result = step()
  end
  if love.update == noop then love.update = real_update end
  if #S.texts_now > 0 then S.texts = S.texts_now end
  if not S.paused then S.game_time = S.game_time + (S.fixed_dt or love.timer.getDelta()) end
  if run_using_game(t) then detect(real_dt) end
  if S.active and t - S.heartbeat_at >= 1 then
    S.heartbeat_at = t
    emit({kind = "heartbeat", t = t_value(), fps = love.timer.getFPS(), paused = S.paused})
  end
  if S.active then media.collect(t, send_text, respond_error, t_value()) end
  flush_events()
  if S.link then S.link:flush() end  -- (sends what this frame answered)
  -- LÖVE quits or restarts after this frame: the pictures' thread must be gone first (a thread still holding LÖVE's
  -- modules fails the restart)
  if result ~= nil then media.stop_worker() end
  return result
end

-- The frame hook: LÖVE's main loop (love.run, the game's own if it defines one after this module) returns a function
-- called every frame; each call goes through frame() above. love.run is kept behind the love table's metatable so a
-- love.run the game defines later is wrapped too.
do
  local base_run = rawget(love, "run")
  local game_run
  local function wrapped()
    local step = (game_run or base_run)()
    if type(step) ~= "function" then return step end
    return function() return frame(step) end
  end
  rawset(love, "run", nil)
  local mt = getmetatable(love) or {}
  local index, newindex = mt.__index, mt.__newindex
  mt.__index = function(t, k)
    if k == "run" then return wrapped end
    if type(index) == "function" then return index(t, k) end
    if type(index) == "table" then return index[k] end
  end
  mt.__newindex = function(t, k, v)
    if k == "run" then game_run = v return end
    if newindex then return newindex(t, k, v) end
    rawset(t, k, v)
  end
  setmetatable(love, mt)
end

say("on (" .. (has_run and ("a " .. cli_run.kind .. " run") or "connect only") .. ", agent service " .. brain_url() .. ")")

return autopilot
