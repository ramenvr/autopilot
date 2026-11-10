--!strict
-- A ModuleScript's table as the game's own scripts have it. A plugin's require() runs a module again for the plugin
-- alone (its own copy, not the game's state), so on each side of a play session the plugin puts a helper script of its
-- own in the game (a Script in ServerScriptService, a LocalScript in the player's PlayerScripts, named
-- AutopilotPlaytest) that requires modules as the game's scripts do and answers through a BindableFunction: reads of
-- the table's fields, writes, calls of its functions, its keys. Values come back copied (functions as "<function>").
-- Without the helper (the edit DataModel, before it is in), the plugin's own require is used.
local Players = game:GetService("Players")
local RunService = game:GetService("RunService")

local Modules = {}

local NAME = "AutopilotPlaytest"

-- The helper's code (runs in the game, as the game's scripts do).
local HELPER = [==[
-- Autopilot's helper (put in by the Autopilot plugin while it playtests this session; never saved with the place):
-- reads and calls the game's modules as the game's scripts have them.
local call = script:WaitForChild("Call")
local MAX_DEPTH, MAX_ITEMS = 6, 500

local function copy(v, depth, seen)
	local t = typeof(v)
	if t == "function" then
		return "<function>"
	elseif t == "thread" then
		return "<thread>"
	elseif t == "table" then
		if seen[v] then
			return "<cycle>"
		end
		if depth >= MAX_DEPTH then
			return "<table>"
		end
		seen[v] = true
		local n, count, array = #v, 0, true
		for k in pairs(v) do
			count += 1
			if type(k) ~= "number" or k < 1 or k > n or k % 1 ~= 0 then
				array = false
			end
		end
		local out = {}
		if array and count == n then
			for i = 1, math.min(n, MAX_ITEMS) do
				out[i] = copy(v[i], depth + 1, seen)
			end
		else
			local i = 0
			for k, x in pairs(v) do
				i += 1
				if i > MAX_ITEMS then
					out["..."] = (count - MAX_ITEMS) .. " more"
					break
				end
				out[tostring(k)] = copy(x, depth + 1, seen)
			end
		end
		seen[v] = nil
		return out
	end
	return v
end

local function field(m, name)
	local v = m
	for part in string.gmatch(name, "[^%.]+") do
		if type(v) ~= "table" then
			return nil
		end
		v = v[part]
	end
	return v
end

call.OnInvoke = function(op, module, name, value, args, withSelf)
	local ok, m = pcall(require, module)
	if not ok then
		return false, "require(" .. module:GetFullName() .. ") failed: " .. tostring(m)
	end
	if op == "get" then
		if type(m) ~= "table" then
			return true, nil, false
		end
		local v = field(m, name)
		return true, copy(v, 0, {}), v ~= nil, typeof(v)
	elseif op == "all" then
		return true, copy(m, 0, {})
	elseif op == "set" then
		if type(m) ~= "table" then
			return false, "the module's value isn't a table"
		end
		local parent, key = m, name
		local head, last = string.match(name, "^(.*)%.([^%.]+)$")
		if head then
			parent, key = field(m, head), last
		end
		if type(parent) ~= "table" then
			return false, "no table at " .. tostring(head)
		end
		parent[key] = value
		return true
	elseif op == "call" then
		local fn = if type(m) == "table" then field(m, name) else (if name == "" then m else nil)
		if type(fn) ~= "function" then
			return false, "Function: " .. name .. " does not exist"
		end
		local a = args or {}
		local results = table.pack(pcall(function()
			if withSelf then
				return fn(m, table.unpack(a, 1, a.n or #a))
			end
			return fn(table.unpack(a, 1, a.n or #a))
		end))
		if not results[1] then
			return false, tostring(results[2])
		end
		local out = {}
		for i = 2, results.n do
			out[i - 1] = copy(results[i], 0, {})
		end
		return true, out, results.n - 1
	elseif op == "keys" then
		local out = {}
		if type(m) == "table" then
			for k, v in pairs(m) do
				local arity, variadic = nil, false
				if type(v) == "function" then
					arity, variadic = debug.info(v, "a")
				end
				table.insert(out, { name = tostring(k), type = typeof(v), arity = arity, variadic = variadic })
			end
		end
		return true, out, typeof(m)
	end
	return false, "unknown op " .. tostring(op)
end
]==]

local helper: Instance? = nil
local bindable: BindableFunction? = nil

-- Put the helper in this side of the play session (once).
function Modules.install()
	if bindable and bindable.Parent and helper and helper.Parent then
		return
	end
	local parent: Instance? = nil
	local s: Instance
	if RunService:IsServer() then
		parent = game:GetService("ServerScriptService")
		s = Instance.new("Script")
	else
		local lp = Players.LocalPlayer
		parent = lp and lp:FindFirstChild("PlayerScripts")
		s = Instance.new("LocalScript")
	end
	if parent == nil then
		return
	end
	s.Name = NAME
	s:SetAttribute(NAME, true)
	local b = Instance.new("BindableFunction")
	b.Name = "Call"
	b.Parent = s;
	(s :: any).Source = HELPER
	s.Parent = parent
	helper, bindable = s, b
end

function Modules.remove()
	if helper then
		pcall(function()
			(helper :: Instance):Destroy()
		end)
	end
	helper, bindable = nil, nil
end

local function invoke(...: any): ...any
	local b = bindable
	if b == nil or b.Parent == nil then
		return nil
	end
	local results = table.pack(pcall(b.Invoke, b, ...))
	if not results[1] then
		return false, tostring(results[2])
	end
	return table.unpack(results, 2, results.n)
end

local function localRequire(m: ModuleScript): (boolean, any)
	return pcall(require, m)
end

local function field(t: any, name: string): any
	local v = t
	for part in string.gmatch(name, "[^%.]+") do
		if type(v) ~= "table" then
			return nil
		end
		v = v[part]
	end
	return v
end

-- (ok, value, found): a field of the module's table (a dotted name goes into nested tables).
function Modules.get(m: ModuleScript, name: string): (boolean, any, boolean)
	if bindable then
		local ok, v, found = invoke("get", m, name)
		if ok ~= nil then
			return ok, v, found == true
		end
	end
	local ok, t = localRequire(m)
	if not ok then
		return false, t, false
	end
	if type(t) ~= "table" then
		return true, nil, false
	end
	local v = field(t, name)
	return true, v, v ~= nil
end

function Modules.all(m: ModuleScript): (boolean, any)
	if bindable then
		local ok, v = invoke("all", m)
		if ok ~= nil then
			return ok, v
		end
	end
	return localRequire(m)
end

function Modules.set(m: ModuleScript, name: string, value: any): (boolean, string?)
	if bindable then
		local ok, err = invoke("set", m, name, value)
		if ok ~= nil then
			return ok, err
		end
	end
	local ok, t = localRequire(m)
	if not ok or type(t) ~= "table" then
		return false, "the module's value isn't a table"
	end
	t[name] = value
	return true, nil
end

-- (ok, {results...} or error, count)
function Modules.call(m: ModuleScript, name: string, args: { any }, withSelf: boolean): (boolean, any, number)
	if bindable then
		local ok, results, n = invoke("call", m, name, nil, args, withSelf)
		if ok ~= nil then
			return ok, results, n or 0
		end
	end
	local ok, t = localRequire(m)
	if not ok then
		return false, t, 0
	end
	local fn = if type(t) == "table" then field(t, name) else (if name == "" then t else nil)
	if type(fn) ~= "function" then
		return false, "Function: " .. name .. " does not exist", 0
	end
	local results = table.pack(pcall(function()
		if withSelf then
			return fn(t, table.unpack(args, 1, (args :: any).n or #args))
		end
		return fn(table.unpack(args, 1, (args :: any).n or #args))
	end))
	if not results[1] then
		return false, tostring(results[2]), 0
	end
	return true, { table.unpack(results, 2, results.n) }, results.n - 1
end

-- (ok, {{name, type, arity, variadic}...})
function Modules.keys(m: ModuleScript): (boolean, any)
	if bindable then
		local ok, keys = invoke("keys", m)
		if ok ~= nil then
			return ok, keys
		end
	end
	local ok, t = localRequire(m)
	if not ok then
		return false, t
	end
	local out = {}
	if type(t) == "table" then
		for k, v in pairs(t) do
			local arity, variadic = nil, false
			if type(v) == "function" then
				arity, variadic = debug.info(v, "a")
			end
			table.insert(out, { name = tostring(k), type = typeof(v), arity = arity, variadic = variadic })
		end
	end
	return true, out
end

return Modules
