--!strict
-- A connection out to the brain (or to a pipe through it), carrying JSON frames (autopilot/bridge.py). It is a
-- WebSocket (HttpService:CreateWebStreamClient) when the DataModel has one to spare, else HTTP in the editor/server
-- (the bridge's HTTP_PATH: long polls for what the brain sends, POSTs for what the plugin sends, at httpUrl or the next port):
-- Studio lets each DataModel have four WebSockets, every plugin's counted, so another plugin may have taken them. While
-- wanted it reconnects with a backoff (1 s doubling to 15 s).
--   local link = Link.new(url, token, httpUrl)
--   link.onOpen = function() ... end; link.onFrame = function(frame) ... end; link.onClose = function(wasOpen) ... end
--   link:open(); link:send(frame); link:close()
local HttpService = game:GetService("HttpService")
local RunService = game:GetService("RunService")

local Json = require(script.Parent.Json)

local Link = {}
Link.__index = Link

Link.PROTOCOL = 1
Link.DEFAULT_URL = "ws://127.0.0.1:8765/v1/bridge"
Link.CONNECT_SECONDS = 6
Link.HTTP_PATH = "/v1/http/"
-- What the links of this DataModel did lately, for a look from Studio's command bar (_G.AutopilotLinkLog)
local log = {}
;(_G :: any).AutopilotLinkLog = log
local function note(text: string)
	table.insert(log, string.format("%.2f %s", os.clock(), text))
	if #log > 80 then
		table.remove(log, 1)
	end
end

export type Link = {
	url: string,
	token: string,
	connected: boolean,
	role: string,
	problem: string,
	want: boolean,
	transport: string,
	onOpen: (() -> ())?,
	onFrame: ((any) -> ())?,
	onClose: ((boolean) -> ())?,
	[string]: any,
}

function Link.new(url: string?, token: string?, httpUrl: string?): Link
	local self = setmetatable({}, Link) :: any
	self.url = if url and url ~= "" then url else Link.DEFAULT_URL
	self.token = token or ""
	self.httpUrl = httpUrl or ""
	self.connected = false
	self.role = "" -- the brain's role from its welcome ("" until welcomed)
	self.problem = "" -- what went wrong last, in words
	self.want = false
	self.backoff = 1.0
	self.client = nil
	self.generation = 0
	self.transport = "" -- "websocket" or "http", once connected
	self.useHttp = false -- no WebSocket to spare in this DataModel: HTTP from now on
	return self
end

-- The HTTP transport's address and the WebSocket path it stands in for: ws://h:8765/v1/pipe/k?dm=x ->
-- http://h:8766/v1/http/, /v1/pipe/k?dm=x
function Link.httpOf(url: string): (string, string)
	local scheme, host, port, path = string.match(url, "^(wss?)://([^/:]+):?(%d*)(.*)$")
	if scheme == nil then
		return "", "/v1/bridge"
	end
	local p = tonumber(port) or (if scheme == "wss" then 443 else 80)
	return (if scheme == "wss" then "https" else "http") .. "://" .. host .. ":" .. tostring(p + 1) .. Link.HTTP_PATH,
		(if path == "" then "/v1/bridge" else path)
end

function Link:open()
	self.want = true
	self.backoff = 1.0
	self.problem = ""
	self:_connect()
end

function Link:_headers(): { [string]: string }
	local headers = {}
	if self.token ~= "" then
		headers["Authorization"] = "Bearer " .. self.token
	end
	return headers
end

function Link:_frame(frame: any)
	if type(frame) == "table" then
		if frame.type == "welcome" then
			self.backoff = 1.0 -- (only once welcomed: a brain that refuses is tried with the backoff growing)
		end
		if self.onFrame then
			self.onFrame(frame)
		end
	end
end

function Link:_opened()
	self.connected = true
	if self.problem ~= "" and not self.refused then
		self.problem = ""
	end
	if self.onOpen then
		self.onOpen()
	end
end

-- The connection (generation gen) ended: once.
function Link:_ended(gen: number, was: boolean, why: string)
	if gen ~= self.generation then
		return
	end
	self.generation += 1
	self:_drop()
	if not was and not self.refused then
		self.problem = "Can't reach the agent service at " .. self.url .. ". Trying again…"
	elseif was and not self.refused then
		self.problem = "Connection closed" .. (if why ~= "" then " (" .. why .. ")" else "")
	end
	if self.onClose then
		self.onClose(was)
	end
	self:_retry(self.generation)
end

function Link:_connect()
	self:_drop()
	self.generation += 1
	if self.useHttp then
		self:_connectHttp(self.generation)
		return
	end
	local gen = self.generation
	-- (Headers only with something in them: an empty table is refused, and a refused socket still takes its slot)
	local options: any = { Url = self.url }
	if self.token ~= "" then
		options.Headers = self:_headers()
	end
	local ok, client = pcall(function()
		return HttpService:CreateWebStreamClient(Enum.WebStreamClientType.WebSocket, options)
	end)
	note("ws " .. self.url .. " -> " .. (if ok then "made" else tostring(client)))
	if not ok then
		if string.find(tostring(client), "Too many", 1, true) then
			if not RunService:IsEdit() and RunService:IsClient() then
				-- Roblox forbids RequestAsync in a Play client, including a plugin's. Retry WebSocket so
				-- freeing a slot recovers automatically instead of switching permanently to unusable HTTP.
				local problem = "Studio's play client has no WebSocket slot available and cannot use HTTP. "
					.. "Close an unused plugin connection to free a slot; Autopilot will retry."
				if self.problem ~= problem then
					warn("[Autopilot] " .. problem)
				end
				self.problem = problem
				self:_retry(gen)
				return
			end
			-- (the DataModel's WebSockets are taken, by other plugins too: HTTP instead, from now on)
			self.useHttp = true
			self:_connectHttp(gen)
			return
		end
		self.problem = "Can't connect to " .. self.url .. ": " .. tostring(client)
		self:_retry(gen)
		return
	end
	self.client = client
	local opened = false
	client.Opened:Connect(function()
		if gen == self.generation then
			opened = true
			note("ws open " .. self.url)
			self.transport = "websocket"
			self:_opened()
		end
	end)
	client.MessageReceived:Connect(function(text: string)
		if gen == self.generation then
			self:_frame(Json.decode(text))
		end
	end)
	client.Closed:Connect(function()
		note("ws closed " .. self.url)
		self:_ended(gen, opened, "")
	end)
	pcall(function()
		(client :: any).Error:Connect(function(code: any, message: any)
			note("ws error " .. self.url .. " " .. tostring(code) .. " " .. tostring(message))
			self:_ended(gen, opened, tostring(message or code or ""))
		end)
	end)
	-- (a socket to an address where nothing listens may never say so: given up on after CONNECT_SECONDS)
	task.delay(Link.CONNECT_SECONDS, function()
		if gen == self.generation and not opened then
			self:_ended(gen, false, "no answer")
		end
	end)
end

function Link:_connectHttp(gen: number)
	local base, path = Link.httpOf(self.url)
	if self.httpUrl ~= "" then
		base = string.gsub(self.httpUrl, "/+$", "") .. "/"
	end
	self.http = { base = base, peer = nil, outbox = {}, sending = false }
	local http = self.http
	task.spawn(function()
		local ok, r = pcall(function()
			local request: any = { Url = base .. "open?path=" .. HttpService:UrlEncode(path), Method = "POST", Body = "" }
			if self.token ~= "" then
				request.Headers = self:_headers()
			end
			return HttpService:RequestAsync(request)
		end)
		if gen ~= self.generation then
			return
		end
		local answer = if ok and r.Success then Json.decode(r.Body) else nil
		if type(answer) ~= "table" or type(answer.peer) ~= "string" then
			self:_ended(gen, false, "")
			return
		end
		http.peer = answer.peer
		self.transport = "http"
		self:_opened()
		self:_flush(gen)
		while gen == self.generation do
			local okp, rp = pcall(function()
				return HttpService:RequestAsync({ Url = base .. "poll?peer=" .. http.peer, Method = "GET" })
			end)
			if gen ~= self.generation then
				return
			end
			if not okp or not rp.Success then
				self:_ended(gen, true, if okp then tostring(rp.StatusCode) else "")
				return
			end
			local frames = Json.decode(rp.Body)
			if type(frames) == "table" then
				for _, f in ipairs(frames) do
					if gen ~= self.generation then
						return
					end
					self:_frame(f)
				end
			end
		end
	end)
end

-- Send what waits, in order: one POST at a time, what comes meanwhile goes in the next.
function Link:_flush(gen: number)
	local http = self.http
	if http == nil or http.sending or http.peer == nil or #http.outbox == 0 then
		return
	end
	http.sending = true
	task.spawn(function()
		while gen == self.generation and #http.outbox > 0 do
			local batch = http.outbox
			http.outbox = {}
			local ok, r = pcall(function()
				return HttpService:RequestAsync({ Url = http.base .. "send?peer=" .. http.peer, Method = "POST",
					Headers = { ["Content-Type"] = "application/json" }, Body = "[" .. table.concat(batch, ",") .. "]" })
			end)
			if not ok or not r.Success then
				http.sending = false
				self:_ended(gen, true, if ok then tostring(r.StatusCode) else "")
				return
			end
		end
		http.sending = false
	end)
end

function Link:_retry(gen: number)
	if not self.want then
		return
	end
	local wait = self.backoff
	self.backoff = math.min(self.backoff * 2, 15)
	task.delay(wait, function()
		if self.want and gen == self.generation and not self.connected then
			self:_connect()
		end
	end)
end

function Link:_drop(code: number?)
	local c = self.client
	local http = self.http
	self.client = nil
	self.http = nil
	self.connected = false
	self.role = ""
	if c then
		-- (a socket, open or failed, takes one of the DataModel's few until it is closed)
		pcall(function()
			c:Close()
		end)
	end
	if http and http.peer then
		task.spawn(function()
			pcall(function()
				HttpService:RequestAsync({ Url = http.base .. "close?peer=" .. http.peer .. "&code=" .. tostring(code or 1000),
					Method = "POST", Body = "" })
			end)
		end)
	end
end

-- Stop connecting and close the connection.
function Link:close()
	self.want = false
	self.generation += 1
	self:_drop()
end

-- Connect again now (a changed address, the user's Reconnect).
function Link:reconnect(url: string?, token: string?, httpUrl: string?)
	if url then
		self.url = if url ~= "" then url else Link.DEFAULT_URL
	end
	if token then
		self.token = token
	end
	if httpUrl ~= nil then
		self.httpUrl = httpUrl
	end
	self.refused = false
	self:open()
end

function Link:sendText(text: string): boolean
	if not self.connected then
		return false
	end
	local http = self.http
	if http then
		table.insert(http.outbox, text)
		self:_flush(self.generation)
		return true
	end
	local c = self.client
	if c == nil then
		return false
	end
	local ok = pcall(function()
		c:Send(text)
	end)
	return ok
end

function Link:send(frame: any): boolean
	return self:sendText(Json.encode(frame))
end

return Link
