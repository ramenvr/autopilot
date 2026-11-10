--!strict
-- JSON for the protocol (autopilot/bridge.py): HttpService decodes, this encodes. Roblox's own encoder can't tell an
-- empty object from an empty array (both are {}), stops at NaN, and turns a sparse table into an object; here a table
-- made by Json.object() is always an object, a table with keys 1..n only is an array, NaN and infinities are null, and
-- Json.null stands for an explicit null (a key whose value must be there, e.g. the game time before the game runs).
local HttpService = game:GetService("HttpService")

local Json = {}

local OBJECT = {}
Json.null = setmetatable({}, {
	__tostring = function()
		return "null"
	end,
})

-- An empty table that encodes as {} (an object), with these fields if given.
function Json.object(t: { [any]: any }?): { [any]: any }
	return setmetatable(t or {}, OBJECT)
end

local function isArray(t: { [any]: any }): (boolean, number)
	if getmetatable(t) == OBJECT then
		return false, 0
	end
	local n = #t
	local count = 0
	for k in pairs(t) do
		if type(k) ~= "number" or k < 1 or k > n or k % 1 ~= 0 then
			return false, 0
		end
		count += 1
	end
	return count == n, n
end

local function number(x: number): string
	if x ~= x or x == math.huge or x == -math.huge then
		return "null"
	end
	if x % 1 == 0 and math.abs(x) < 1e15 then
		return string.format("%d", x)
	end
	return string.format("%.14g", x)
end

local encode: (any, { string }, { [any]: boolean }) -> ()
encode = function(v: any, out: { string }, seen: { [any]: boolean })
	local t = type(v)
	if v == nil or v == Json.null then
		table.insert(out, "null")
	elseif t == "boolean" then
		table.insert(out, v and "true" or "false")
	elseif t == "number" then
		table.insert(out, number(v))
	elseif t == "string" then
		table.insert(out, HttpService:JSONEncode(v))
	elseif t == "table" then
		if seen[v] then
			table.insert(out, "null")
			return
		end
		seen[v] = true
		local array, n = isArray(v)
		if array and n > 0 then
			table.insert(out, "[")
			for i = 1, n do
				if i > 1 then
					table.insert(out, ",")
				end
				encode(v[i], out, seen)
			end
			table.insert(out, "]")
		elseif array then
			-- (an empty table not made by Json.object: an empty list, as Roblox's encoder says it)
			table.insert(out, "[]")
		else
			table.insert(out, "{")
			local first = true
			for k, x in pairs(v) do
				if x ~= nil then
					if not first then
						table.insert(out, ",")
					end
					first = false
					table.insert(out, HttpService:JSONEncode(tostring(k)))
					table.insert(out, ":")
					encode(x, out, seen)
				end
			end
			table.insert(out, "}")
		end
		seen[v] = nil
	else
		-- (a Roblox value that wasn't converted: its text)
		table.insert(out, HttpService:JSONEncode(tostring(v)))
	end
end

function Json.encode(v: any): string
	local out = {}
	encode(v, out, {})
	return table.concat(out)
end

function Json.decode(text: string): any
	local ok, v = pcall(function()
		return HttpService:JSONDecode(text)
	end)
	if ok then
		return v
	end
	return nil
end

-- Whether a decoded value is a JSON object (a table that isn't a list).
function Json.isObject(v: any): boolean
	if type(v) ~= "table" then
		return false
	end
	local array, n = isArray(v)
	return not array or (n == 0 and next(v) == nil)
end

return Json
