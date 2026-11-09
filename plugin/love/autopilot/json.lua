-- JSON for the link to the brain: decode a frame, encode an answer.
--
-- Lua has one table for both arrays and objects, so an empty table is ambiguous: json.array(t) marks a table as an
-- array (an empty one encodes as []), json.object(t) as an object (an empty one encodes as {}). Otherwise a table with
-- a [1] and only integer keys is an array, any other table an object. json.null encodes as null (a nil can't be kept
-- in a table). Decoded arrays and objects come marked, so they encode back the same way.

local json = {}

local ARRAY = {__jsontype = "array"}
local OBJECT = {__jsontype = "object"}
json.null = setmetatable({}, {__tostring = function() return "null" end, __jsontype = "null"})

function json.array(t) return setmetatable(t or {}, ARRAY) end
function json.object(t) return setmetatable(t or {}, OBJECT) end

local function kind(t)
  local mt = getmetatable(t)
  if mt == ARRAY then return "array" end
  if mt == OBJECT then return "object" end
  if t[1] == nil then return next(t) == nil and "object" or "object" end
  local n = 0
  for k in pairs(t) do
    if type(k) ~= "number" or k < 1 or k % 1 ~= 0 then return "object" end
    n = n + 1
  end
  return n == #t and "array" or "object"
end

local escapes = {['"'] = '\\"', ["\\"] = "\\\\", ["\b"] = "\\b", ["\f"] = "\\f", ["\n"] = "\\n", ["\r"] = "\\r",
  ["\t"] = "\\t"}

local function str(s)
  return '"' .. s:gsub('[%c"\\]', function(c)
    return escapes[c] or string.format("\\u%04x", c:byte())
  end) .. '"'
end

local encode

local function number(n)
  if n ~= n or n == math.huge or n == -math.huge then return "null" end  -- (NaN and infinities have no JSON)
  if n % 1 == 0 and n > -1e15 and n < 1e15 then return string.format("%d", n) end
  return string.format("%.14g", n)
end

encode = function(v, out, seen, depth)
  local t = type(v)
  if v == nil or v == json.null then
    out[#out + 1] = "null"
  elseif t == "boolean" then
    out[#out + 1] = v and "true" or "false"
  elseif t == "number" then
    out[#out + 1] = number(v)
  elseif t == "string" then
    out[#out + 1] = str(v)
  elseif t == "table" then
    if seen[v] or depth > 64 then
      out[#out + 1] = '"<cycle>"'
      return
    end
    seen[v] = true
    if kind(v) == "array" then
      out[#out + 1] = "["
      for i = 1, #v do
        if i > 1 then out[#out + 1] = "," end
        encode(v[i], out, seen, depth + 1)
      end
      out[#out + 1] = "]"
    else
      out[#out + 1] = "{"
      local first = true
      for k, x in pairs(v) do
        local tk = type(k)
        if (tk == "string" or tk == "number") and type(x) ~= "function" and type(x) ~= "userdata"
            and type(x) ~= "thread" then
          if not first then out[#out + 1] = "," end
          first = false
          out[#out + 1] = str(tostring(k))
          out[#out + 1] = ":"
          encode(x, out, seen, depth + 1)
        end
      end
      out[#out + 1] = "}"
    end
    seen[v] = nil
  else
    out[#out + 1] = str(tostring(v))  -- (a function or userdata: its name, as a string)
  end
end

function json.encode(v)
  local out = {}
  encode(v, out, {}, 0)
  return table.concat(out)
end

-- decoding ---------------------------------------------------------------------------------------------------------

local function fail(s, i, what)
  error(string.format("JSON: %s at %d near %q", what, i, s:sub(i, i + 20)), 0)
end

local function skip(s, i)
  return s:find("[^ \t\r\n]", i) or #s + 1
end

local decode

local function utf8(cp)
  if cp < 0x80 then return string.char(cp) end
  if cp < 0x800 then return string.char(0xC0 + math.floor(cp / 0x40), 0x80 + cp % 0x40) end
  if cp < 0x10000 then
    return string.char(0xE0 + math.floor(cp / 0x1000), 0x80 + math.floor(cp / 0x40) % 0x40, 0x80 + cp % 0x40)
  end
  return string.char(0xF0 + math.floor(cp / 0x40000), 0x80 + math.floor(cp / 0x1000) % 0x40,
    0x80 + math.floor(cp / 0x40) % 0x40, 0x80 + cp % 0x40)
end

local function decode_string(s, i)
  local out, j = {}, i + 1
  while true do
    local k = s:find('["\\]', j)
    if not k then fail(s, i, "unterminated string") end
    out[#out + 1] = s:sub(j, k - 1)
    if s:sub(k, k) == '"' then return table.concat(out), k + 1 end
    local c = s:sub(k + 1, k + 1)
    if c == "u" then
      local cp = tonumber(s:sub(k + 2, k + 5), 16)
      if not cp then fail(s, k, "bad \\u escape") end
      j = k + 6
      if cp >= 0xD800 and cp < 0xDC00 and s:sub(j, j + 1) == "\\u" then  -- a surrogate pair
        local lo = tonumber(s:sub(j + 2, j + 5), 16)
        if lo and lo >= 0xDC00 and lo < 0xE000 then
          cp = 0x10000 + (cp - 0xD800) * 0x400 + (lo - 0xDC00)
          j = j + 6
        end
      end
      out[#out + 1] = utf8(cp)
    else
      local m = ({b = "\b", f = "\f", n = "\n", r = "\r", t = "\t"})[c] or c
      out[#out + 1] = m
      j = k + 2
    end
  end
end

decode = function(s, i)
  i = skip(s, i)
  local c = s:sub(i, i)
  if c == "{" then
    local obj = json.object({})
    i = skip(s, i + 1)
    if s:sub(i, i) == "}" then return obj, i + 1 end
    while true do
      i = skip(s, i)
      if s:sub(i, i) ~= '"' then fail(s, i, "expected a key") end
      local k
      k, i = decode_string(s, i)
      i = skip(s, i)
      if s:sub(i, i) ~= ":" then fail(s, i, "expected :") end
      local v
      v, i = decode(s, i + 1)
      if v ~= json.null then obj[k] = v end
      i = skip(s, i)
      local d = s:sub(i, i)
      if d == "}" then return obj, i + 1 end
      if d ~= "," then fail(s, i, "expected , or }") end
      i = i + 1
    end
  elseif c == "[" then
    local arr = json.array({})
    i = skip(s, i + 1)
    if s:sub(i, i) == "]" then return arr, i + 1 end
    while true do
      local v
      v, i = decode(s, i)
      arr[#arr + 1] = v
      i = skip(s, i)
      local d = s:sub(i, i)
      if d == "]" then return arr, i + 1 end
      if d ~= "," then fail(s, i, "expected , or ]") end
      i = i + 1
    end
  elseif c == '"' then
    return decode_string(s, i)
  elseif s:sub(i, i + 3) == "true" then
    return true, i + 4
  elseif s:sub(i, i + 4) == "false" then
    return false, i + 5
  elseif s:sub(i, i + 3) == "null" then
    return json.null, i + 4
  end
  local num = s:match("^-?%d+%.?%d*[eE]?[-+]?%d*", i)
  if not num or num == "" then fail(s, i, "unexpected character") end
  return tonumber(num), i + #num
end

function json.decode(s)
  local v, i = decode(s, 1)
  if skip(s, i) <= #s then fail(s, i, "trailing characters") end
  if v == json.null then return nil end
  return v
end

return json
