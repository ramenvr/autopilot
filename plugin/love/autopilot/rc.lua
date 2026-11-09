-- `rc`: reads, writes and calls on the game's Lua values, by path (world.resolve): the brain's game.get, game.set,
-- game.call and game.describe.
--
-- A property is a field, a nested one with "." or ":" (stats.hp). "position" is the place of a thing ({x, y}: what
-- its x and y fields hold, or its body's position), written too. Functions take their parameters by name (the names
-- in their definition, from the debug library) or as a list; a function whose first parameter is `self` is called as
-- a method.

local json = require("autopilot.json")
local world = require("autopilot.world")

local rc = {}

local MAX_FIELDS = 200

-- A Lua value as JSON: numbers, strings, booleans as they are; a thing as its path; a table as its fields (a level
-- or two deep); a LÖVE object as its type ("Body", "Image"); a function as "<function>".
function rc.to_json(v, depth)
  depth = depth or 0
  local t = type(v)
  if t == "number" or t == "string" or t == "boolean" then return v end
  if v == nil then return json.null end
  if t == "function" then return "<function>" end
  if t == "userdata" then
    local ok, name = pcall(function() return v:type() end)
    return ok and ("<" .. tostring(name) .. ">") or tostring(v)
  end
  if t == "table" then
    if v == json.null then return v end
    if depth > 0 then
      local path = world.path_of(v)
      if path then return path end
    end
    if depth >= 2 then return "<table>" end
    local out, n = {}, 0
    local array = #v > 0
    for k, x in pairs(v) do
      n = n + 1
      if n > MAX_FIELDS then break end
      if type(x) ~= "function" then
        if array and type(k) == "number" then out[k] = rc.to_json(x, depth + 1)
        elseif type(k) == "string" or type(k) == "number" then
          array = false
          out[tostring(k)] = rc.to_json(x, depth + 1)
        end
      end
    end
    return array and json.array(out) or json.object(out)
  end
  return tostring(v)
end

local function missing(path)
  return "Object " .. tostring(path) .. " does not exist"
end

local function parts(prop)
  local out = {}
  for p in tostring(prop):gmatch("[^%.:]+") do out[#out + 1] = tonumber(p) or p end
  return out
end

local function get_field(obj, prop)
  if prop == "position" then
    local pl = world.place(obj)
    if pl and pl.body then return {x = pl.x, y = pl.y} end
    local x, y = rawget(obj, "x"), rawget(obj, "y")
    if type(x) == "number" then return {x = x, y = y} end
  end
  local cur = obj
  for _, p in ipairs(parts(prop)) do
    if type(cur) ~= "table" and type(cur) ~= "userdata" then return nil, false end
    local ok, nxt = pcall(function() return cur[p] end)
    if not ok then return nil, false end
    cur = nxt
  end
  return cur, true
end

local function set_field(obj, prop, value)
  if value == json.null then value = nil end
  if prop == "position" and type(value) == "table" then
    local x, y = tonumber(value.x or value.X), tonumber(value.y or value.Y)
    local pl = world.place(obj)
    if pl and pl.body then
      pl.body:setPosition(x, y)
      return true
    end
    if pl and pl.w > 0 and type(rawget(obj, "w") or rawget(obj, "width")) == "number" and not (world.tracked[obj] or {}).center then
      x, y = x - pl.w / 2, y - pl.h / 2  -- (a table's x, y with a width: its top left)
    end
    obj.x, obj.y = x, y
    return true
  end
  local ps = parts(prop)
  local cur = obj
  for i = 1, #ps - 1 do
    cur = cur[ps[i]]
    if type(cur) ~= "table" then return false, "Property " .. tostring(prop) .. " does not exist" end
  end
  cur[ps[#ps]] = value
  return true
end

-- The names of a Lua function's parameters ({"self", "x", "y"}), and whether it takes more (...).
function rc.params(f)
  local names = {}
  if type(f) ~= "function" then return names, false end
  local info = debug.getinfo(f, "u")
  for i = 1, (info.nparams or 0) do
    names[#names + 1] = debug.getlocal(f, i)
  end
  return names, info.isvararg
end

local function find_function(obj, name)
  local ok, f = pcall(function() return obj[name] end)
  if ok and type(f) == "function" then return f end
end

local function do_call(body)
  local path = body.objectPath
  local obj = world.resolve(path)
  if obj == nil then return false, missing(path), 404 end
  local fname = tostring(body.functionName or "")
  local f = find_function(obj, fname)
  if not f then return false, "Function: " .. fname .. " does not exist on object: " .. tostring(path), 404 end
  local names = rc.params(f)
  local params = body.parameters
  if params == json.null then params = nil end
  local argv, n = {}, 0
  local method = names[1] == "self"
  if method then
    n = 1
    argv[1] = obj
  end
  if type(params) == "table" and getmetatable(params) == getmetatable(json.array({})) then
    for i = 1, #params do argv[n + i] = params[i] end
    n = n + #params
  elseif type(params) == "table" then
    local used = {}
    for i = (method and 2 or 1), #names do
      argv[i] = params[names[i]]
      used[names[i]] = true
      n = i
    end
    for k in pairs(params) do
      if not used[k] then
        return false, fname .. " on " .. tostring(path) .. " has no parameter " .. tostring(k) .. " (it takes "
          .. table.concat(names, ", ") .. ")"
      end
    end
  end
  for i = 1, n do
    if argv[i] == json.null then argv[i] = nil end
  end
  local results = {pcall(f, unpack(argv, 1, n))}
  if not results[1] then return false, fname .. " failed: " .. tostring(results[2]), 500 end
  local out = {ReturnValue = rc.to_json(results[2])}
  for i = 3, #results do out["Return" .. (i - 1)] = rc.to_json(results[i]) end
  return true, out
end

local function do_property(body)
  local path = body.objectPath
  local obj = world.resolve(path)
  if obj == nil then return false, missing(path), 404 end
  local prop = body.propertyName
  local value = body.propertyValue
  local write = type(value) == "table" or tostring(body.access or ""):sub(1, 5) == "WRITE"
  if write then
    if prop == nil then return false, "A write needs a propertyName" end
    if type(obj) ~= "table" then return false, tostring(path) .. " can't be written (" .. type(obj) .. ")" end
    local v = type(value) == "table" and value[prop] or nil
    local ok, why = set_field(obj, prop, v)
    if not ok then return false, why end
    return true, {}
  end
  if prop == nil or prop == json.null then
    local all = rc.to_json(obj)
    if type(all) ~= "table" then all = {value = all} end
    if getmetatable(all) == getmetatable(json.array({})) then all = {items = all} end
    return true, all
  end
  local v, found = get_field(obj, prop)
  if v == nil and not found then
    return false, "Property " .. tostring(prop) .. " does not exist on " .. tostring(path), 404
  end
  return true, {[prop] = rc.to_json(v)}
end

local function describe_functions(obj, into, seen, depth)
  if type(obj) ~= "table" or depth > 6 then return end
  for k, v in pairs(obj) do
    if type(k) == "string" and type(v) == "function" and not seen[k] then
      seen[k] = true
      local names = rc.params(v)
      local args = {}
      for i, n in ipairs(names) do
        if not (i == 1 and n == "self") then args[#args + 1] = {Name = n} end
      end
      into[#into + 1] = {Name = k, Description = names[1] == "self" and "method" or "function",
        Arguments = json.array(args)}
    end
  end
  local mt = getmetatable(obj)
  if type(mt) == "table" then
    local index = rawget(mt, "__index")
    if type(index) == "table" and index ~= obj then describe_functions(index, into, seen, depth + 1) end
  end
end

local function do_describe(body)
  local path = body.objectPath
  local obj = world.resolve(path)
  if obj == nil then return false, missing(path), 404 end
  local props, funcs = {}, {}
  if type(obj) == "table" then
    local n = 0
    for k, v in pairs(obj) do
      n = n + 1
      if n > MAX_FIELDS then break end
      if (type(k) == "string" or type(k) == "number") and type(v) ~= "function" then
        props[#props + 1] = {Name = tostring(k), Description = "field", Type = type(v)}
      end
    end
    table.sort(props, function(a, b) return a.Name < b.Name end)
    describe_functions(obj, funcs, {}, 0)
    table.sort(funcs, function(a, b) return a.Name < b.Name end)
  end
  local th = type(obj) == "table" and world.things().by_obj[obj]
  return true, {Name = tostring(path):match("[^/%.]+$") or tostring(path),
    Class = th and th.classes[1] or type(obj), Properties = json.array(props), Functions = json.array(funcs)}
end

function rc.handle(verb, body)
  if verb == "probe" then return true, {} end
  if type(body) ~= "table" then return false, "rc needs a body" end
  if verb == "call" then return do_call(body) end
  if verb == "property" then return do_property(body) end
  if verb == "describe" then return do_describe(body) end
  return false, "unknown rc verb " .. tostring(verb)
end

return rc
