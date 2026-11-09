-- `codex_manifest` and `codex_read`: the game's own files for the brain to read (its Lua code and shaders), as they
-- are in its folder (or its .love). The save directory LÖVE mounts beside the game is left out, and so is this module.

local json = require("autopilot.json")

local codex = {}

local EXT = {lua = true, glsl = true, frag = true, vert = true}
local MAX_FILES = 5000
local MAX_TOTAL = 64 * 1024 * 1024
codex.DEFAULT_READ = 4 * 1024 * 1024
local MAX_READ = 32 * 1024 * 1024

local function hex(s) return love.data.encode("string", "hex", s) end

-- Whether a path is the game's own (not the save directory's).
local function own(path)
  local real = love.filesystem.getRealDirectory(path)
  return real == nil or real == love.filesystem.getSource()
end

local function walk(dir, out)
  for _, item in ipairs(love.filesystem.getDirectoryItems(dir)) do
    local path = dir == "" and item or dir .. "/" .. item
    local info = love.filesystem.getInfo(path)
    if info and info.type == "directory" then
      if item:sub(1, 1) ~= "." and item ~= "node_modules" and path ~= "autopilot" and own(path) then
        walk(path, out)
      end
    elseif info and info.type == "file" then
      local ext = item:match("%.(%w+)$")
      if ext and EXT[ext:lower()] and own(path) then out[#out + 1] = {path = path, size = info.size or 0} end
    end
  end
end

-- The game's files: {path, size, hash} each.
function codex.manifest(project)
  local found = {}
  walk("", found)
  table.sort(found, function(a, b) return a.path < b.path end)
  local files, total, truncated = {}, 0, false
  for _, f in ipairs(found) do
    if #files >= MAX_FILES then
      truncated = true
      break
    end
    if total + f.size > MAX_TOTAL then
      truncated = true
    else
      local data = love.filesystem.read(f.path)
      if data then
        total = total + #data
        files[#files + 1] = {path = f.path, size = #data, hash = hex(love.data.hash("md5", data))}
      end
    end
  end
  return {project = project, files = json.array(files), truncated = truncated}
end

-- Whether a path the brain asks for is one this may answer (relative, no "..", no drive, not the module's).
local function safe(path)
  if type(path) ~= "string" or path == "" or path:sub(1, 1) == "/" or path:find("\\", 1, true)
      or path:find(":", 1, true) then
    return false
  end
  for part in path:gmatch("[^/]+") do
    if part == ".." or part == "." or part:sub(1, 1) == "." then return false end
  end
  return not path:match("^autopilot/") and own(path)
end

function codex.read(paths, max_bytes)
  max_bytes = math.max(1, math.min(tonumber(max_bytes) or codex.DEFAULT_READ, MAX_READ))
  local out, missing, too_big, used = json.object({}), json.array({}), json.array({}), 0
  for _, asked in ipairs(paths or {}) do
    asked = tostring(asked)
    local info = safe(asked) and love.filesystem.getInfo(asked)
    if not info or info.type ~= "file" then
      missing[#missing + 1] = asked
    elseif (info.size or 0) > max_bytes then
      too_big[#too_big + 1] = asked
    elseif used + (info.size or 0) > max_bytes then
      break  -- (this and the rest go unanswered: the brain asks again)
    else
      local data = love.filesystem.read(asked)
      if data then
        out[asked] = data
        used = used + #data
      else
        missing[#missing + 1] = asked
      end
    end
  end
  return {files = out, missing = missing, too_big = too_big}
end

-- The keys the game's code reads (love.keyboard.isDown("a", ...), key == "a"), for `context`'s controls and for
-- `move` (which keys walk: W/A/S/D or the arrows).
function codex.keys_read()
  local found, order = {}, {}
  local files = {}
  walk("", files)
  for _, f in ipairs(files) do
    if f.path:match("%.lua$") then
      local src = love.filesystem.read(f.path) or ""
      for args in src:gmatch("isDown%s*(%b())") do
        for k in args:gmatch("[\"']([%w ]+)[\"']") do
          if not found[k] then found[k], order[#order + 1] = true, k end
        end
      end
      for k in src:gmatch("[%w_]*key%s*==%s*[\"']([%w ]+)[\"']") do
        if not found[k] then found[k], order[#order + 1] = true, k end
      end
    end
  end
  table.sort(order)
  return order
end

return codex
