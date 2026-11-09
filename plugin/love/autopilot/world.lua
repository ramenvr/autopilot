-- The game's things, as the brain asks for them (find, transforms, pose, movement, view, watch).
--
-- LÖVE has no scene tree, so a "thing" is one of the game's tables that has a place:
--   - what the game names itself: autopilot.track(obj, class), autopilot.player(obj), autopilot.camera(obj or fn);
--   - the bodies of love.physics worlds (their userData table when they have one);
--   - any table reachable from the globals with numeric x and y (enemies[3], world.entities[12]...).
-- A thing's path is its keys from the global table, "/"-separated: /player, /enemies/3. Many games keep their state
-- in main.lua's locals instead (local state, world...): those the love.* callbacks use are searched too, under
-- /@local/<name> (/@local/state/level/entities/2). A thing reached no other way has a path of its own: /@track/2, /@body/5.
--
-- Places are LÖVE's world pixels (x right, y down) given in the kit frame: (X, Y, Z) = (x, 0, -y).

local json = require("autopilot.json")

local world = {}

local SKIP_GLOBALS = {_G = true, package = true, love = true, string = true, math = true, table = true, io = true,
  os = true, debug = true, coroutine = true, bit = true, jit = true, ffi = true, socket = true, utf8 = true,
  arg = true, _AUTOPILOT = true}
local MAX_DEPTH = 8
local MAX_ENTRIES = 4000  -- per table walked: past that, a table is data, not things
local MAX_THINGS = 10000
local MAX_VISIT = 60000   -- tables walked in all, a search (the shallowest first)
local COLLECTION = 8      -- a table with more entries than this is a collection (its members one kind of thing)
-- LÖVE's callbacks, whose locals (upvalues) are searched too
local CALLBACKS = {"load", "update", "draw", "keypressed", "keyreleased", "textinput", "mousepressed",
  "mousereleased", "mousemoved", "wheelmoved", "resize", "focus", "quit", "gamepadpressed", "joystickpressed"}

world.tracked = {}       -- obj -> {class = ..., n = ...}
world.tracked_list = {}  -- in order
world.chosen = nil       -- the player the game (or the brain) named
world.camera_spec = nil  -- what autopilot.camera() was given
world.physics = setmetatable({}, {__mode = "k"})  -- love.physics worlds made (hooked)

local function num(v) return type(v) == "number" and v == v end

-- kit frame -------------------------------------------------------------------------------------------------------

local function r2(v) return math.floor(v * 100 + 0.5) / 100 end
world.r2 = r2

function world.kit(x, y)
  return {X = r2(x), Y = 0, Z = r2(-y)}
end

function world.from_kit(v)
  if type(v) ~= "table" then return nil end
  return tonumber(v.X or v.x) or 0, -(tonumber(v.Z or v.z) or 0)
end

-- what a table is -------------------------------------------------------------------------------------------------

local function body_of(t)
  local b = rawget(t, "body") or rawget(t, "collider")
  if type(b) == "userdata" and type(b.getPosition) == "function" then
    local ok = pcall(b.getPosition, b)
    if ok then return b end
  end
end

local function vec_field(t, names)
  for _, k in ipairs(names) do
    local v = rawget(t, k)
    if type(v) == "table" and num(rawget(v, "x")) and num(rawget(v, "y")) then return v end
  end
end

-- Where a table is: {x, y (its middle), w, h (0 when not known), r (rotation), body?} or nil for a table with no place.
function world.place(t)
  if type(t) ~= "table" then return nil end
  if t == json.null then return nil end
  local b = body_of(t)
  if b then
    local x, y = b:getPosition()
    local w, h = tonumber(rawget(t, "w") or rawget(t, "width")) or 0, tonumber(rawget(t, "h") or rawget(t, "height")) or 0
    return {x = x, y = y, w = w, h = h, r = b:getAngle(), body = b}
  end
  local x, y = rawget(t, "x"), rawget(t, "y")
  if not (num(x) and num(y)) then
    local p = vec_field(t, {"pos", "position"})
    if not p then return nil end
    x, y = p.x, p.y
  end
  local w = rawget(t, "w") or rawget(t, "width")
  local h = rawget(t, "h") or rawget(t, "height")
  local r = rawget(t, "angle") or rawget(t, "rotation") or rawget(t, "rot")
  r = num(r) and r or 0
  local info = world.tracked[t]
  if num(w) and num(h) then
    if info and info.center then return {x = x, y = y, w = w, h = h, r = r} end
    return {x = x + w / 2, y = y + h / 2, w = w, h = h, r = r}  -- (x, y with a width and height: the top left)
  end
  -- a circle ({x, y, r} or a radius): x, y its middle; a square of a size: x, y its middle too
  local radius = rawget(t, "radius") or rawget(t, "r")
  if num(radius) then return {x = x, y = y, w = 2 * radius, h = 2 * radius, r = r} end
  local size = rawget(t, "size")
  if num(size) then return {x = x, y = y, w = size, h = size, r = r} end
  return {x = x, y = y, w = 0, h = 0, r = r}
end

-- A thing's velocity in world pixels a second, from its fields or its body (nil: not told).
function world.velocity_fields(t, place)
  if place and place.body then return place.body:getLinearVelocity() end
  for _, pair in ipairs({{"vx", "vy"}, {"dx", "dy"}, {"xv", "yv"}, {"velX", "velY"}, {"xVel", "yVel"},
    {"speedX", "speedY"}, {"xspeed", "yspeed"}}) do
    local a, b = rawget(t, pair[1]), rawget(t, pair[2])
    if num(a) and num(b) then return a, b end
  end
  local v = vec_field(t, {"velocity", "vel", "v"})
  if v then return v.x, v.y end
end

local function class_name(t, key)
  local info = world.tracked[t]
  local names = {}
  if info and info.class then names[#names + 1] = info.class end
  local c = rawget(t, "class")
  if type(c) == "table" and type(rawget(c, "name")) == "string" then names[#names + 1] = c.name end  -- middleclass
  if type(c) == "string" then names[#names + 1] = c end
  for _, k in ipairs({"type", "kind", "tag", "className", "classname", "faction", "team", "side"}) do
    local v = rawget(t, k)
    if type(v) == "string" and v ~= "" then names[#names + 1] = v end
  end
  local mt = getmetatable(t)
  if type(mt) == "table" then
    for _, k in ipairs({"__name", "name", "className", "__type"}) do
      local v = rawget(mt, k)
      if type(v) == "string" and v ~= "" then names[#names + 1] = v end
    end
  end
  if type(key) == "string" then
    names[#names + 1] = key
    local single = key:gsub("ies$", "y"):gsub("([^s])s$", "%1")
    if single ~= key then names[#names + 1] = single end
  end
  if #names == 0 then names[1] = "table" end
  return names
end

-- the things ------------------------------------------------------------------------------------------------------

-- The tables main.lua's locals hold, as the love.* callbacks see them (their upvalues): {name = table}. The first
-- callback to name a local wins; locals that aren't tables (a number, a function) aren't roots.
function world.locals()
  local out = {}
  for _, cb in ipairs(CALLBACKS) do
    local f = rawget(love, cb)
    if type(f) == "function" then
      local i = 1
      while true do
        local name, value = debug.getupvalue(f, i)
        if not name then break end
        if type(value) == "table" and name ~= "" and name:sub(1, 1) ~= "_" and out[name] == nil
            and value ~= _G and value ~= love and not (name:match("^[%u_]+$")) then
          out[name] = value
        end
        i = i + 1
      end
    end
  end
  return out
end

world._cache, world._cache_frame = nil, -1
world.frame = 0

-- Whether a table looks alive (a game object, not data that happens to have x and y: a map marker, a sprite's
-- rectangle): it has a life (hp, health, alive, dead, state), a velocity, or a physics body, or the game named it.
local LIFE = {"hp", "HP", "health", "life", "alive", "dead", "isDead", "state", "vx", "vy", "dx", "dy", "velocity",
  "vel", "speed", "body", "collider"}
local function alive_like(t, place)
  if place.body or world.tracked[t] then return true end
  for _, k in ipairs(LIFE) do
    if rawget(t, k) ~= nil then return true end
  end
  return false
end

local MAX_DATA = 2000  -- things that don't look alive, kept a search (live ones first, up to MAX_THINGS)

local function add(out, seen, t, path, key, pattern)
  if seen[t] then return end
  local place = world.place(t)
  if not place then return end
  local live = alive_like(t, place)
  if live and #out.live >= MAX_THINGS or not live and #out.data >= MAX_DATA then return end
  seen[t] = true
  local thing = {obj = t, path = path, classes = class_name(t, key), name = tostring(key or path:match("[^/]+$")),
    live = live, collection = pattern or path}
  if live then out.live[#out.live + 1] = thing else out.data[#out.data + 1] = thing end
  out.by_path[path] = thing
  out.by_obj[t] = thing
end

-- Every thing now, with paths: {list (the live ones first), live, data, by_path, by_obj}. Found once a frame at
-- most.
function world.things()
  if world._cache and world._cache_frame == world.frame then return world._cache end
  local out = {list = {}, live = {}, data = {}, by_path = {}, by_obj = {}}
  local seen = {}
  local cam = world.camera_object()
  if cam then seen[cam] = true end
  -- the globals, breadth first: shallower paths are the better names
  local visited = {[_G] = true}
  local queue = {}
  for k, v in pairs(_G) do
    if type(k) == "string" and type(v) == "table" and not SKIP_GLOBALS[k] and k:sub(1, 1) ~= "_" then
      queue[#queue + 1] = {v, "/" .. k, k, 1, "/" .. k}
    end
  end
  local roots = {}
  for name, t in pairs(world.locals()) do roots[#roots + 1] = {t, "/@local/" .. name, name, 1, "/@local/" .. name} end
  table.sort(roots, function(a, b) return a[2] < b[2] end)
  table.sort(queue, function(a, b) return a[2] < b[2] end)
  for _, r in ipairs(roots) do queue[#queue + 1] = r end
  local qi = 1
  local walked = 0
  while qi <= #queue and walked < MAX_VISIT do
    local t, path, key, depth, pattern = queue[qi][1], queue[qi][2], queue[qi][3], queue[qi][4], queue[qi][5]
    qi = qi + 1
    if not visited[t] then
      visited[t] = true
      walked = walked + 1
      add(out, seen, t, path, key, pattern)
      if depth < MAX_DEPTH then
        local n = 0
        local parent_key = key
        -- a table of many entries (an array, or a map by id) is a collection: its members share one pattern
        local many = 0
        for _ in pairs(t) do
          many = many + 1
          if many > COLLECTION then break end
        end
        for k, v in pairs(t) do
          n = n + 1
          if n > MAX_ENTRIES then break end
          if type(v) == "table" and not visited[v] and (type(k) == "string" or type(k) == "number") then
            local ks = tostring(k)
            if ks:sub(1, 2) ~= "__" then  -- (a "_private" field is often the state itself: _items, _list)
              -- an array member is named by its array (enemies/3 is an "enemies" thing)
              local member = (type(k) == "number" or many > COLLECTION) and "*" or ks
              queue[#queue + 1] = {v, path .. "/" .. ks, type(k) == "number" and parent_key or ks, depth + 1,
                pattern .. "/" .. member}
            end
          end
        end
      end
    end
  end
  -- tracked things the globals don't reach
  for i, t in ipairs(world.tracked_list) do
    if world.tracked[t] and not seen[t] then add(out, seen, t, "/@track/" .. i, world.tracked[t].class) end
  end
  -- love.physics bodies not already found through their userData
  local nb = 0
  for w in pairs(world.physics) do
    local ok, bodies = pcall(w.getBodies, w)
    if ok and bodies then
      for _, b in ipairs(bodies) do
        nb = nb + 1
        local ud = b:getUserData()
        if type(ud) == "table" and not seen[ud] then
          add(out, seen, ud, "/@body/" .. nb, nil)
        end
      end
    end
  end
  for _, th in ipairs(out.live) do out.list[#out.list + 1] = th end
  for _, th in ipairs(out.data) do out.list[#out.list + 1] = th end
  world._cache, world._cache_frame = out, world.frame
  return out
end

-- An object (any Lua value) by path: /player, /enemies/3, player, enemies.3, /@track/2. nil, why for a missing one.
function world.resolve(path)
  path = tostring(path or "")
  if path == "" or path == "/" then return _G end
  local th = world.things().by_path[path]
  if th then return th.obj end
  local cur = _G
  local rest = path
  local name, after = path:match("^/?@local/([^/%.]+)(.*)$")
  if name then
    cur = world.locals()[name]
    if cur == nil then return nil end
    rest = after
  end
  for part in rest:gmatch("[^/%.]+") do
    if type(cur) ~= "table" and type(cur) ~= "userdata" then return nil end
    local nxt
    local ok = pcall(function() nxt = cur[part] end)
    if (not ok or nxt == nil) and tonumber(part) then pcall(function() nxt = cur[tonumber(part)] end) end
    if nxt == nil then return nil end
    cur = nxt
  end
  return cur
end

function world.path_of(obj)
  local th = world.things().by_obj[obj]
  return th and th.path
end

-- the player and the camera ---------------------------------------------------------------------------------------

world.found_by = ""

function world.player()
  if world.chosen then
    local p = type(world.chosen) == "string" and world.resolve(world.chosen) or world.chosen
    if world.place(p) then world.found_by = "named" return p end
  end
  for _, k in ipairs({"player", "Player", "hero", "Hero", "p1", "ship", "Ship"}) do
    local p = rawget(_G, k)
    if world.place(p) then world.found_by = "the global " .. k return p end
  end
  for _, th in ipairs(world.things().live) do
    for _, c in ipairs(th.classes) do
      local l = c:lower()
      if l == "player" or l == "hero" or l:find("player", 1, true) then
        world.found_by = "its class " .. c
        return th.obj
      end
    end
  end
  world.found_by = ""
  return nil
end

function world.camera_object()
  local c = world.camera_spec
  if type(c) == "table" then return c end
  if c then return nil end
  for _, k in ipairs({"camera", "cam", "Camera"}) do
    local t = rawget(_G, k)
    if type(t) == "table" and num(rawget(t, "x")) and num(rawget(t, "y")) then return t end
  end
end

-- The part of the world on screen: {x, y (top left), w, h, scale}, world pixels.
function world.view_rect()
  local sw, sh = love.graphics.getDimensions()
  local c = world.camera_spec
  if type(c) == "function" then
    local ok, cx, cy, scale = pcall(c)
    if ok and num(cx) and num(cy) then
      scale = num(scale) and scale > 0 and scale or 1
      return {x = cx - sw / scale / 2, y = cy - sh / scale / 2, w = sw / scale, h = sh / scale, scale = scale}
    end
  end
  local cam = world.camera_object()
  if cam then
    local scale = rawget(cam, "scale") or rawget(cam, "zoom")
    scale = num(scale) and scale > 0 and scale or 1
    if type(cam.attach) == "function" or type(cam.getPosition) == "function" then
      -- hump.camera, gamera: x, y is the middle of the view
      return {x = cam.x - sw / scale / 2, y = cam.y - sh / scale / 2, w = sw / scale, h = sh / scale, scale = scale}
    end
    -- a camera of the game's own: love.graphics.translate(-camera.x, -camera.y)
    return {x = cam.x, y = cam.y, w = sw / scale, h = sh / scale, scale = scale}
  end
  return {x = 0, y = 0, w = sw, h = sh, scale = 1}
end

-- A world point on screen: -1..1 from the middle, y up (as `view` gives it), and its window pixels.
function world.to_screen(x, y)
  local v = world.view_rect()
  local sx, sy = (x - v.x) / v.w, (y - v.y) / v.h
  return sx * 2 - 1, 1 - sy * 2, sx * love.graphics.getWidth(), sy * love.graphics.getHeight()
end

-- velocity by sampling -------------------------------------------------------------------------------------------

local samples = setmetatable({}, {__mode = "k"})  -- obj -> {x, y, t, vx, vy}
world.watched = setmetatable({}, {__mode = "k"})  -- obj -> last time the brain asked about it

-- Called once a frame: the positions of the things the brain follows, for their velocities.
function world.tick(now)
  world.frame = world.frame + 1
  local p = world.player()
  if p then world.watched[p] = now end
  for obj, at in pairs(world.watched) do
    if now - at > 10 then
      world.watched[obj] = nil
    else
      local pl = world.place(obj)
      if pl then
        local s = samples[obj]
        if s and now > s.t then
          local dt = now - s.t
          s.vx, s.vy = (pl.x - s.x) / dt, (pl.y - s.y) / dt
          s.x, s.y, s.t = pl.x, pl.y, now
        elseif not s then
          samples[obj] = {x = pl.x, y = pl.y, t = now, vx = 0, vy = 0}
        end
      end
    end
  end
end

function world.velocity(obj, place)
  local vx, vy = world.velocity_fields(obj, place)
  if num(vx) and num(vy) then return vx, vy end
  local s = samples[obj]
  if s then return s.vx, s.vy end
  return 0, 0
end

local function on_ground(t)
  for _, k in ipairs({"on_ground", "onGround", "grounded", "isGrounded", "is_grounded", "onFloor", "on_floor"}) do
    local v = rawget(t, k)
    if type(v) == "boolean" then return v end
  end
  return json.null
end

-- ops ------------------------------------------------------------------------------------------------------------

local function matches(th, cls, name)
  if name and name ~= "" and not th.name:lower():find(name:lower(), 1, true)
      and not th.path:lower():find(name:lower(), 1, true) then
    return false
  end
  if cls and cls ~= "" then
    local want = cls:lower()
    for _, c in ipairs(th.classes) do
      if c:lower() == want then return true end
    end
    return false
  end
  return true
end

function world.find(args)
  local cls, name = args.class, args.name
  local limit = tonumber(args.limit) or 10000
  local out = json.array({})
  local any = false
  for _, th in ipairs(world.things().list) do
    if matches(th, cls, name) then
      any = true
      if #out < limit then out[#out + 1] = th.path end
    end
  end
  if cls and cls ~= "" and not any then
    return false, "No thing of class " .. tostring(cls) .. " (a LÖVE thing's class is what autopilot.track named, "
      .. "its class's name, its type/kind field or the table it is in)", 400
  end
  return true, {objects = out}
end

local function transform_of(obj, now)
  local pl = world.place(obj)
  if not pl then
    return {location = json.null, rotation = json.null, velocity = json.null, center = json.null,
      extent = json.null, active = true}
  end
  world.watched[obj] = now
  local vx, vy = world.velocity(obj, pl)
  local active = true
  for _, k in ipairs({"dead", "removed", "destroyed", "isDead"}) do
    if rawget(obj, k) == true then active = false end
  end
  if rawget(obj, "alive") == false or rawget(obj, "active") == false or rawget(obj, "visible") == false then
    active = false
  end
  return {location = world.kit(pl.x, pl.y), rotation = {Pitch = r2(-math.deg(pl.r)), Yaw = 0, Roll = 0},
    velocity = world.kit(vx, vy), center = world.kit(pl.x, pl.y), extent = {X = r2(pl.w / 2), Y = 0, Z = r2(pl.h / 2)},
    active = active}
end

function world.transforms(args, now)
  local out = json.object({})
  for _, path in ipairs(args.objects or {}) do
    local obj = world.resolve(path)
    out[tostring(path)] = (type(obj) == "table") and transform_of(obj, now) or json.null
  end
  return true, {objects = out}
end

function world.pose(now)
  local p = world.player()
  if not p then return true, {} end
  local pl = world.place(p)
  local vx, vy = world.velocity(p, pl)
  return true, {at = world.kit(pl.x, pl.y), feet = world.kit(pl.x, pl.y + pl.h / 2), eye = world.kit(pl.x, pl.y),
    yaw = 0, pitch = 0, velocity = world.kit(vx, vy), on_ground = on_ground(p), path = world.path_of(p) or ""}
end

local function first_number(t, names)
  for _, k in ipairs(names) do
    local v = rawget(t, k)
    if num(v) then return v, k end
  end
end

function world.movement()
  local p = world.player()
  if not p then return false, "No player: name it with autopilot.player(obj), or context {player = path}" end
  local pl = world.place(p)
  local sources = {}
  local speed, ks = first_number(p, {"speed", "walkSpeed", "walk_speed", "moveSpeed", "move_speed", "SPEED",
    "maxSpeed", "max_speed", "runSpeed"})
  if speed then sources.walk_speed = ks end
  local g, kg = first_number(p, {"gravity", "GRAVITY", "g"})
  if not g then
    g, kg = first_number(_G, {"gravity", "GRAVITY"})
    if kg then kg = "global " .. kg end
  end
  if not g and pl.body then
    local _, gy = pl.body:getWorld():getGravity()
    g, kg = gy, "the physics world's gravity"
  end
  if g then sources.gravity = kg end
  local jump, kj = first_number(p, {"jumpForce", "jump_force", "jumpSpeed", "jump_speed", "jumpVelocity",
    "jump_velocity", "jumpPower", "jump_power", "jumpStrength", "JUMP_VELOCITY", "JUMP_FORCE"})
  if jump then
    jump = math.abs(jump)
    sources.jump_z = kj
  else
    local height, kh = first_number(p, {"jumpHeight", "jump_height"})
    if height and g then
      jump, sources.jump_z = math.sqrt(2 * math.abs(g) * height), kh .. " as sqrt(2 g h)"
    end
  end
  return true, {walk_speed = speed or json.null, jump_z = jump or json.null, gravity = g and math.abs(g) or json.null,
    half_height = pl.h / 2, radius = pl.w / 2, step = json.null, max_slope = json.null, air_control = json.null,
    sources = sources}
end

local OMITTED_2D = {"held", "openings", "ways_up", "lane", "top_slope_degrees", "low_end", "high_end", "uphill",
  "body.under", "body.overhead", "look", "blocks_player", "sonar"}

function world.view(args)
  local p = world.player()
  local pp = p and world.place(p)
  local v = world.view_rect()
  local ox, oy = v.x + v.w / 2, v.y + v.h / 2
  if pp then ox, oy = pp.x, pp.y end
  local max_count = tonumber(args.max_count) or 24
  local max_d = tonumber(args.max_distance) or 5000
  local only
  if type(args.only) == "table" then
    only = {}
    for _, x in ipairs(args.only) do only[tostring(x)] = true end
  end
  local out = {}
  local all = world.things()
  local function inside(pl)
    return pl.x + pl.w / 2 >= v.x and pl.x - pl.w / 2 <= v.x + v.w and pl.y + pl.h / 2 >= v.y
      and pl.y - pl.h / 2 <= v.y + v.h
  end
  -- The live things, and the others (no life of their own: a coin, a door) from collections with only a few of them
  -- in the view. A collection is the things whose paths match once each table of many entries is a "*" (/coins/*,
  -- /maps/*/markers/*, /items/*/icon): one of hundreds is data with an x and a y (a sprite atlas's frames, every
  -- item's icon rectangle, the maps' markers), nothing on screen.
  local shown = {}
  for _, th in ipairs(all.live) do shown[#shown + 1] = th end
  local groups, order = {}, {}
  for _, th in ipairs(all.data) do
    if inside(world.place(th.obj)) then
      local g = th.collection
      if not groups[g] then groups[g], order[#order + 1] = {}, g end
      table.insert(groups[g], th)
    end
  end
  for _, g in ipairs(order) do
    if #groups[g] <= 32 then
      for _, th in ipairs(groups[g]) do shown[#shown + 1] = th end
    end
  end
  for _, th in ipairs(shown) do
    local t = th.obj
    if t ~= p and (not only or only[th.name] or only[th.path]) then
      local pl = world.place(t)
      local d = math.sqrt((pl.x - ox) ^ 2 + (pl.y - oy) ^ 2)
      if inside(pl) and d <= max_d and rawget(t, "visible") ~= false then
        local sx, sy = world.to_screen(pl.x, pl.y)
        local vx, vy = world.velocity(t, pl)
        local item = {name = th.name, path = th.path, class = th.classes[1], center = world.kit(pl.x, pl.y),
          size = {X = r2(pl.w), Y = 0, Z = r2(pl.h)}, distance = r2(d), screen_x = r2(sx), screen_y = r2(sy),
          speed = r2(math.sqrt(vx * vx + vy * vy)), live = th.live}
        if pl.x - pl.w / 2 < v.x or pl.x + pl.w / 2 > v.x + v.w or pl.y - pl.h / 2 < v.y or pl.y + pl.h / 2 > v.y + v.h then
          item.partly_in_view = true
        end
        out[#out + 1] = item
      end
    end
  end
  -- the live things first, nearest first; then the others, nearest first
  table.sort(out, function(a, b)
    if a.live ~= b.live then return a.live end
    return a.distance < b.distance
  end)
  for _, item in ipairs(out) do
    if not item.live then item.prop = true end  -- (no life of its own: a coin, a door, a marker)
    item.live = nil
  end
  while #out > max_count do table.remove(out) end
  local body = json.object({})
  if p then
    local vx, vy = world.velocity(p, pp)
    body = {velocity = world.kit(vx, vy), crouched = false, on_ground = on_ground(p)}
    if type(body.on_ground) == "boolean" then body.falling = not body.on_ground end
  end
  local result = {visible = json.array(out),
    camera = {location = world.kit(v.x + v.w / 2, v.y + v.h / 2), yaw = 0, pitch = 0, fov = json.null},
    effects_play = true, body = body, omitted = json.array(OMITTED_2D), scene = json.object({})}
  if type(args.only) == "table" then result.only = args.only end
  return true, result
end

-- watch: what changes on some things over a few seconds -----------------------------------------------------------

function world.watch_start(args)
  local entries = {}
  for _, path in ipairs(args.objects or {}) do
    local obj = world.resolve(path)
    if type(obj) == "table" and world.place(obj) then
      entries[#entries + 1] = {obj = obj, path = tostring(path), first = nil, found = {}}
    end
  end
  return {entries = entries, turned = tonumber(args.turned) or 1.0, moved = tonumber(args.moved) or 2.0,
    resized = tonumber(args.resized) or 0.02, drawn = args.drawn == true}
end

function world.watch_tick(st, t)
  for _, e in ipairs(st.entries) do
    local pl = world.place(e.obj)
    if pl then
      if not e.first then
        e.first = pl
      else
        local f = e.first
        if not e.found.turned and math.abs(math.deg(pl.r - f.r)) > st.turned then e.found.turned = t end
        if not e.found.moved and math.sqrt((pl.x - f.x) ^ 2 + (pl.y - f.y) ^ 2) > st.moved then e.found.moved = t end
        if not e.found["changed size"] then
          local bw, bh = math.max(math.abs(f.w), 0.001), math.max(math.abs(f.h), 0.001)
          if math.abs(pl.w - f.w) / bw > st.resized or math.abs(pl.h - f.h) / bh > st.resized then
            e.found["changed size"] = t
          end
        end
      end
      local hidden = rawget(e.obj, "visible") == false
      if e.hidden ~= nil and hidden ~= e.hidden then
        e.found[hidden and "part hidden" or "part shown"] = t
      end
      e.hidden = hidden
    elseif e.first and not e.found.gone then
      e.found.gone = t
    end
  end
end

function world.watch_finish(st)
  local changes = {}
  for _, e in ipairs(st.entries) do
    for what, t in pairs(e.found) do
      changes[#changes + 1] = {t = r2(t), object = e.path, what = what}
    end
  end
  table.sort(changes, function(a, b) return a.t < b.t end)
  local out = {changes = json.array(changes)}
  if st.drawn then out.drawn = true end
  return out
end

-- love.physics: the worlds the game makes, for their bodies
function world.hook_physics()
  if not love.physics or world._physics_hooked then return end
  world._physics_hooked = true
  local new_world = love.physics.newWorld
  love.physics.newWorld = function(...)
    local w = new_world(...)
    world.physics[w] = true
    return w
  end
end

return world
