--!strict
-- `rc`: Remote Control compatible reads, writes, calls and descriptions over Roblox's reflection
-- (plugin/roblox/README.md, "Paths and rc"). Run on the side of the play session the request went to (the server by
-- default: Router.luau).
--
-- objectPath: an instance's path (Paths.luau), a service by name, LocalPlayer / Character / Humanoid / Camera.
-- A property name is, in turn: a property of the instance's class, one of its attributes, the Value of a child value
-- object of that name (an IntValue "Coins"), on a player (or its character) the Value of its leaderstats entry; or
-- `position` / `pivot` (where a model or part is: read and moved as one, PivotTo), `attributes`, `tags`, `children`.
-- On a ModuleScript: the fields of the table it returns, as the game's scripts on that side have it (Modules.luau),
-- and a call runs the module's function. Values are Roblox's own (Values.luau): studs, Roblox's axes.
local CollectionService = game:GetService("CollectionService")
local ReflectionService = game:GetService("ReflectionService")

local Json = require(script.Parent.Json)
local Modules = require(script.Parent.Modules)
local Paths = require(script.Parent.Paths)
local Values = require(script.Parent.Values)

local Rc = {}

local function missing(path: string): string
	return "Object " .. path .. " does not exist"
end

-- {name -> member info} of a class's properties and methods, from ReflectionService (cached).
local propCache: { [string]: { [string]: any } } = {}
local methodCache: { [string]: { [string]: any } } = {}

local function members(kind: string, className: string): { [string]: any }
	local cache = if kind == "property" then propCache else methodCache
	local got = cache[className]
	if got then
		return got
	end
	got = {}
	local ok, list = pcall(function()
		if kind == "property" then
			return ReflectionService:GetPropertiesOfClass(className)
		end
		return ReflectionService:GetMethodsOfClass(className)
	end)
	if ok and type(list) == "table" then
		for _, m in ipairs(list) do
			if type(m) == "table" and m.Name then
				got[m.Name] = m
			end
		end
	end
	cache[className] = got
	return got
end

local function scriptType(info: any): (string?, Enum?)
	local t = info and info.Type
	if type(t) ~= "table" then
		return nil, nil
	end
	local st = t.ScriptType
	local enum = nil
	if t.EnumType then
		local ok, e = pcall(function()
			return (Enum :: any)[t.EnumType]
		end)
		enum = if ok then e else nil
	end
	if st == "EnumItem" then
		return "EnumItem", enum
	end
	return if type(st) == "string" then st else (t.EngineType or nil), enum
end

local function typeLabel(info: any): string
	local t = info and info.Type
	if type(t) ~= "table" then
		return ""
	end
	if t.EnumType then
		return "Enum." .. tostring(t.EnumType)
	end
	if t.InstanceType and t.ScriptType == "Instance" then
		return tostring(t.InstanceType)
	end
	return tostring(t.ScriptType or t.EngineType or "")
end

local function hidden(info: any): boolean
	local d = info and info.Display
	if type(d) == "table" and (d.Deprecated or d.Hidden) then
		return true
	end
	return false
end

local function leaderstat(inst: Instance, name: string): ValueBase?
	local player: Instance? = nil
	if inst:IsA("Player") then
		player = inst
	elseif inst:IsA("Model") then
		local ok, p = pcall(function()
			return game:GetService("Players"):GetPlayerFromCharacter(inst :: Model)
		end)
		player = if ok then p else nil
	end
	local stats = player and player:FindFirstChild("leaderstats")
	local v = stats and stats:FindFirstChild(name)
	return if v and v:IsA("ValueBase") then v else nil
end

local function childValue(inst: Instance, name: string): ValueBase?
	local v = inst:FindFirstChild(name)
	if v and v:IsA("ValueBase") then
		return v
	end
	return leaderstat(inst, name)
end

local function isProperty(inst: Instance, name: string): boolean
	return members("property", inst.ClassName)[name] ~= nil
end

local function readProperty(inst: Instance, name: string): (boolean, any)
	return pcall(function()
		return (inst :: any)[name]
	end)
end

local function attributesOf(inst: Instance): any
	local out = Json.object()
	for k, v in pairs(inst:GetAttributes()) do
		out[k] = Values.toJson(v)
	end
	return out
end

local function childrenOf(inst: Instance): { string }
	local out = {}
	for _, c in ipairs(inst:GetChildren()) do
		table.insert(out, Paths.of(c))
		if #out >= 200 then
			break
		end
	end
	return out
end

-- One named value of an instance (see the header): (found, value as JSON).
local function readName(inst: Instance, name: string): (boolean, any)
	if inst:IsA("ModuleScript") then
		local ok, v, found = Modules.get(inst :: ModuleScript, name)
		if not ok then
			error(tostring(v), 0)
		end
		if found then
			return true, Values.toJson(v)
		end
	end
	if isProperty(inst, name) then
		local ok, v = readProperty(inst, name)
		if ok then
			return true, Values.toJson(v)
		end
	end
	local attr = inst:GetAttribute(name)
	if attr ~= nil then
		return true, Values.toJson(attr)
	end
	local cv = childValue(inst, name)
	if cv then
		return true, Values.toJson((cv :: any).Value)
	end
	local lower = string.lower(name)
	if lower == "position" or lower == "pivot" or lower == "cframe" then
		if inst:IsA("PVInstance") then
			local cf = (inst :: PVInstance):GetPivot()
			return true, Values.toJson(if lower == "position" then cf.Position else cf)
		end
	elseif lower == "attributes" then
		return true, attributesOf(inst)
	elseif lower == "tags" then
		return true, CollectionService:GetTags(inst)
	elseif lower == "children" then
		return true, childrenOf(inst)
	end
	-- (a property the reflection doesn't list, but the instance has)
	local ok, v = readProperty(inst, name)
	if ok and typeof(v) ~= "Instance" and typeof(v) ~= "RBXScriptSignal" and type(v) ~= "function" then
		return true, Values.toJson(v)
	end
	return false, nil
end

-- A dotted name into a value ("Config.Speed": the Speed field of what Config is), as far as it goes.
local function readDotted(inst: Instance, name: string): (boolean, any)
	local first, rest = string.match(name, "^([^%.]+)%.(.+)$")
	if not first then
		return false, nil
	end
	local v: any = nil
	if isProperty(inst, first) then
		local ok, x = readProperty(inst, first)
		v = if ok then x else nil
	else
		v = inst:GetAttribute(first)
	end
	if v == nil then
		local c = inst:FindFirstChild(first)
		if c then
			return Rc.read(c, rest)
		end
		return false, nil
	end
	for part in string.gmatch(rest, "[^%.]+") do
		local t = typeof(v)
		if t == "table" then
			v = v[part]
		elseif t ~= "nil" then
			local ok, x = pcall(function()
				return (v :: any)[part]
			end)
			v = if ok then x else nil
		end
		if v == nil then
			return false, nil
		end
	end
	return true, Values.toJson(v)
end

function Rc.read(inst: Instance, name: string): (boolean, any)
	local found, v = readName(inst, name)
	if found then
		return true, v
	end
	return readDotted(inst, name)
end

-- Every value of an instance: its class's properties, its attributes and child values (a module: its table's fields).
function Rc.readAll(inst: Instance): any
	local out = Json.object()
	if inst:IsA("ModuleScript") then
		local ok, m = Modules.all(inst :: ModuleScript)
		if ok and type(m) == "table" then
			for k, v in pairs(m) do
				out[tostring(k)] = Values.toJson(v)
			end
		elseif ok and m ~= nil then
			out.value = Values.toJson(m)
		end
	end
	for name, info in pairs(members("property", inst.ClassName)) do
		if hidden(info) or out[name] ~= nil then
			continue
		end
		local ok, v = readProperty(inst, name)
		if ok and typeof(v) ~= "RBXScriptSignal" and type(v) ~= "function" then
			out[name] = Values.toJson(v)
		end
	end
	for k, v in pairs(inst:GetAttributes()) do
		if out[k] == nil then
			out[k] = Values.toJson(v)
		end
	end
	for _, c in ipairs(inst:GetChildren()) do
		if c:IsA("ValueBase") and out[c.Name] == nil then
			out[c.Name] = Values.toJson((c :: any).Value)
		end
	end
	local stats = inst:IsA("Player") and inst:FindFirstChild("leaderstats")
	if stats then
		for _, c in ipairs(stats:GetChildren()) do
			if c:IsA("ValueBase") and out[c.Name] == nil then
				out[c.Name] = Values.toJson((c :: any).Value)
			end
		end
	end
	if inst:IsA("PVInstance") then
		out.position = Values.toJson((inst :: PVInstance):GetPivot().Position)
	end
	return out
end

function Rc.write(inst: Instance, name: string, value: any): (boolean, string?)
	if inst:IsA("ModuleScript") then
		local ok, current, found = Modules.get(inst :: ModuleScript, name)
		if ok and found then
			return Modules.set(inst :: ModuleScript, name, Values.fromJson(value, current))
		end
	end
	local info = members("property", inst.ClassName)[name]
	if info then
		local okr, current = readProperty(inst, name)
		local declared, enum = scriptType(info)
		local v = Values.fromJson(value, if okr then current else nil, declared, enum)
		local ok, err = pcall(function()
			(inst :: any)[name] = v
		end)
		return ok, if ok then nil else tostring(err)
	end
	local attr = inst:GetAttribute(name)
	if attr ~= nil then
		local ok, err = pcall(function()
			inst:SetAttribute(name, Values.fromJson(value, attr))
		end)
		return ok, if ok then nil else tostring(err)
	end
	local cv = childValue(inst, name)
	if cv then
		local ok, err = pcall(function()
			(cv :: any).Value = Values.fromJson(value, (cv :: any).Value)
		end)
		return ok, if ok then nil else tostring(err)
	end
	local lower = string.lower(name)
	if (lower == "position" or lower == "pivot" or lower == "cframe") and inst:IsA("PVInstance") then
		local pv = inst :: PVInstance
		local cf = pv:GetPivot()
		local target = if lower == "position" then CFrame.new(Values.fromJson(value, cf.Position)) * cf.Rotation
			else Values.fromJson(value, cf)
		local ok, err = pcall(function()
			pv:PivotTo(target)
		end)
		return ok, if ok then nil else tostring(err)
	end
	-- (a new attribute: what a script would SetAttribute)
	if type(value) ~= "table" or not Json.isObject(value) then
		local ok, err = pcall(function()
			inst:SetAttribute(name, Values.infer(value))
		end)
		return ok, if ok then nil else ("Property " .. name .. " does not exist on object " .. Paths.of(inst) .. ": "
			.. tostring(err))
	end
	return false, "Property " .. name .. " does not exist on object " .. Paths.of(inst)
end

-- The arguments of a call, in order: `parameters` as a list, or by name (the reflection's parameter names for an
-- instance's method; "args" a list, or "1", "2"... for a module's function).
local function argv(params: any, info: any): { any }
	if type(params) ~= "table" then
		return {}
	end
	if #params > 0 then
		local out = {}
		for i, v in ipairs(params) do
			out[i] = Values.infer(v)
		end
		return out
	end
	if type(params.args) == "table" then
		local out = {}
		for i, v in ipairs(params.args) do
			out[i] = Values.infer(v)
		end
		return out
	end
	local out = {}
	local n = 0
	if info and type(info.Parameters) == "table" then
		for i, p in ipairs(info.Parameters) do
			local v = params[p.Name]
			if v ~= nil then
				local declared, enum = scriptType(p)
				out[i] = Values.fromJson(v, nil, declared, enum)
				n = i
			end
		end
	end
	for k, v in pairs(params) do
		local i = tonumber(k)
		if i and i >= 1 then
			out[i] = Values.infer(v)
			n = math.max(n, i)
		end
	end
	out.n = n
	return out
end

local function call(body: any): (boolean, any)
	local path = tostring(body.objectPath or "")
	local inst = Paths.resolve(path)
	if inst == nil then
		return false, missing(path)
	end
	local fname = tostring(body.functionName or "")
	local params = body.parameters
	local results
	if inst:IsA("ModuleScript") then
		local a = argv(params, nil)
		;(a :: any).n = (a :: any).n or #a
		local withSelf = type(params) == "table" and params.self == true
		local ok, got, n = Modules.call(inst :: ModuleScript, fname, a, withSelf)
		if not ok then
			return false, fname .. " on " .. path .. " failed: " .. tostring(got)
		end
		results = { true, table.unpack(got, 1, n) }
		results.n = n + 1
	else
		local info = members("method", inst.ClassName)[fname]
		local okf, fn = pcall(function()
			return (inst :: any)[fname]
		end)
		if not okf or type(fn) ~= "function" then
			return false, "Function: " .. fname .. " does not exist on object: " .. path
		end
		local a = argv(params, info)
		local n = (a :: any).n or #a
		results = table.pack(pcall(function()
			return fn(inst, table.unpack(a, 1, n))
		end))
	end
	if not results[1] then
		return false, fname .. " on " .. path .. " failed: " .. tostring(results[2])
	end
	local out: any = { ReturnValue = Values.toJson(results[2]) }
	if results.n > 2 then
		local all = {}
		for i = 2, results.n do
			table.insert(all, Values.toJson(results[i]))
		end
		out.ReturnValues = all
	end
	return true, out
end

local function property(body: any): (boolean, any)
	local path = tostring(body.objectPath or "")
	local inst = Paths.resolve(path)
	if inst == nil then
		return false, missing(path)
	end
	local name = tostring(body.propertyName or "")
	local newValue = body.propertyValue
	local write = type(newValue) == "table" or string.sub(tostring(body.access or ""), 1, 5) == "WRITE"
	if name == "" then
		if write then
			return false, "A write needs propertyName"
		end
		return true, Rc.readAll(inst)
	end
	if write then
		if type(newValue) ~= "table" then
			return false, "No value given for " .. name
		end
		local value = newValue[name]
		if value == nil then
			local only, count = nil, 0
			for _, v in pairs(newValue) do
				only = v
				count += 1
			end
			if count ~= 1 then
				return false, "No value given for " .. name
			end
			value = only
		end
		local ok, err = Rc.write(inst, name, value)
		if not ok then
			return false, err
		end
		return true, Json.object()
	end
	local found, v = Rc.read(inst, name)
	if not found then
		return false, "Property " .. name .. " does not exist on object " .. path
	end
	return true, { [name] = v }
end

local function describe(body: any): (boolean, any)
	local path = tostring(body.objectPath or "")
	local inst = Paths.resolve(path)
	if inst == nil then
		return false, missing(path)
	end
	local props, funcs = {}, {}
	if inst:IsA("ModuleScript") then
		local ok, keys = Modules.keys(inst :: ModuleScript)
		if ok and type(keys) == "table" then
			for _, k in ipairs(keys) do
				if k.type == "function" then
					local args = {}
					for i = 1, (k.arity or 0) do
						table.insert(args, { Name = "arg" .. i, Type = "any" })
					end
					table.insert(funcs, { Name = k.name, Arguments = args,
						Description = "module function (call with args=[...])" .. (if k.variadic then ", takes more" else "") })
				else
					table.insert(props, { Name = k.name, Description = "module field", Type = k.type })
				end
			end
		end
	end
	for k, v in pairs(inst:GetAttributes()) do
		table.insert(props, { Name = k, Description = "attribute", Type = typeof(v) })
	end
	for _, c in ipairs(inst:GetChildren()) do
		if c:IsA("ValueBase") then
			table.insert(props, { Name = c.Name, Description = "child " .. c.ClassName, Type = c.ClassName })
		end
	end
	local names = {}
	for name, info in pairs(members("property", inst.ClassName)) do
		if not hidden(info) then
			table.insert(names, name)
		end
	end
	table.sort(names)
	for _, name in ipairs(names) do
		table.insert(props, { Name = name, Description = "", Type = typeLabel(members("property", inst.ClassName)[name]) })
	end
	local mnames = {}
	for name, info in pairs(members("method", inst.ClassName)) do
		if not hidden(info) and string.sub(name, 1, 1) == string.upper(string.sub(name, 1, 1)) then
			table.insert(mnames, name)
		end
	end
	table.sort(mnames)
	for _, name in ipairs(mnames) do
		local info = members("method", inst.ClassName)[name]
		local args = {}
		for _, p in ipairs(info.Parameters or {}) do
			table.insert(args, { Name = p.Name, Type = typeLabel(p) })
		end
		local f: any = { Name = name, Description = "", Arguments = args }
		local rt = info.ReturnType
		if type(rt) == "table" and rt.ScriptType and rt.ScriptType ~= "null" then
			f.Returns = tostring(rt.ScriptType)
		end
		table.insert(funcs, f)
	end
	return true, { Name = inst.Name, Class = inst.ClassName, Path = Paths.of(inst), Properties = props, Functions = funcs,
		Tags = CollectionService:GetTags(inst), Children = childrenOf(inst) }
end

-- [ok, result or error]
function Rc.handle(verb: string, body: any): (boolean, any)
	if verb == "probe" then
		return true, Json.object()
	end
	if type(body) ~= "table" then
		return false, "rc needs a body"
	end
	local ok, a, b = pcall(function()
		if verb == "call" then
			return call(body)
		elseif verb == "property" then
			return property(body)
		elseif verb == "describe" then
			return describe(body)
		end
		return false, "unknown rc verb " .. verb
	end)
	if not ok then
		return false, tostring(a)
	end
	return a, b
end

return Rc
