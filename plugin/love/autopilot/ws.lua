-- A WebSocket client (RFC 6455) over LÖVE's luasocket, never blocking the game: connect, the handshake, reading and
-- writing all go on a little each frame from poll().
--
--   local link = ws.connect("ws://127.0.0.1:8765/v1/bridge", {Authorization = "Bearer x"})
--   each frame: for _, m in ipairs(link:poll()) do ... end   -- m: {text = "..."} or {closed = true, why, code}
--   link:send(text); link:close(code)
--
-- What the client sends is masked (as clients must); pings are answered with pongs; fragmented messages are joined.

local socket = require("socket")
local ffi = require("ffi")
local bit = require("bit")

local ws = {}
ws.__index = ws

local GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

local function base64(s) return love.data.encode("string", "base64", s) end

local function random_bytes(n)
  local t = {}
  for i = 1, n do t[i] = string.char(love.math.random(0, 255)) end
  return table.concat(t)
end

-- The payload XORed with the 4-byte key (masking and unmasking are the same), through a C buffer: fast for the
-- megabytes of a codex read.
local function mask(payload, key)
  local n = #payload
  if n == 0 then return payload end
  local buf = ffi.new("uint8_t[?]", n)
  ffi.copy(buf, payload, n)
  local k0, k1, k2, k3 = key:byte(1, 4)
  local k = {[0] = k0, k1, k2, k3}
  for i = 0, n - 1 do
    buf[i] = bit.bxor(buf[i], k[i % 4])
  end
  return ffi.string(buf, n)
end

local function u16(n) return string.char(math.floor(n / 256) % 256, n % 256) end

local function u64(n)
  local hi, lo = math.floor(n / 4294967296), n % 4294967296
  return string.char(math.floor(hi / 16777216) % 256, math.floor(hi / 65536) % 256, math.floor(hi / 256) % 256,
    hi % 256, math.floor(lo / 16777216) % 256, math.floor(lo / 65536) % 256, math.floor(lo / 256) % 256, lo % 256)
end

local function frame(opcode, payload)
  local n = #payload
  local head = string.char(0x80 + opcode)
  if n < 126 then
    head = head .. string.char(0x80 + n)
  elseif n < 65536 then
    head = head .. string.char(0x80 + 126) .. u16(n)
  else
    head = head .. string.char(0x80 + 127) .. u64(n)
  end
  local key = random_bytes(4)
  return head .. key .. mask(payload, key)
end

local function parse_url(url)
  local scheme, host, port, path = url:match("^(wss?)://([^:/]+):?(%d*)(.*)$")
  if not scheme then return nil, "not a ws:// address: " .. tostring(url) end
  if scheme == "wss" then return nil, "wss:// isn't supported yet (LÖVE has no TLS): use ws://" end
  return {host = host, port = tonumber(port) or 80, path = path ~= "" and path or "/"}
end

-- A new link, connecting. headers: more HTTP headers for the handshake ({Authorization = "Bearer ..."}).
function ws.connect(url, headers)
  local self = setmetatable({url = url, state = "connecting", inbuf = "", outbuf = {}, outpos = 1, parts = nil,
    was_connected = false}, ws)
  local where, err = parse_url(url)
  if not where then
    self.state, self.why = "closed", err
    return self
  end
  self.where = where
  self.headers = headers or {}
  self.tcp = socket.tcp()
  self.tcp:settimeout(0)
  self.tcp:setoption("tcp-nodelay", true)
  self.tcp:connect(where.host, where.port)
  self.started = socket.gettime()
  return self
end

function ws:_end(why, code, out)
  if self.state == "closed" then return end
  self.state, self.why, self.code = "closed", why, code
  if self.tcp then self.tcp:close() end
  out[#out + 1] = {closed = true, why = why, code = code, was_connected = self.was_connected}
end

function ws:_write(data)
  self.outbuf[#self.outbuf + 1] = data
end

function ws:_flush()
  while #self.outbuf > 0 do
    local data = self.outbuf[1]
    local last, err, partial = self.tcp:send(data, self.outpos)
    last = last or partial
    if last and last >= #data then
      table.remove(self.outbuf, 1)
      self.outpos = 1
    else
      if last then self.outpos = last + 1 end
      if err and err ~= "timeout" then return err end
      return nil
    end
  end
end

function ws:_read()
  while true do
    local data, err, partial = self.tcp:receive(65536)
    local got = data or partial
    if got and #got > 0 then self.inbuf = self.inbuf .. got end
    if err == "closed" then return "closed" end
    if not data then return nil end
  end
end

-- What came since the last poll: a list of {text = ...} and, once, {closed = true, ...}.
function ws:poll()
  local out = {}
  if self.state == "closed" then return out end
  if self.state == "connecting" then
    local ok, err = self.tcp:connect(self.where.host, self.where.port)
    if ok or err == "already connected" then
      self.key = base64(random_bytes(16))
      local lines = {"GET " .. self.where.path .. " HTTP/1.1", "Host: " .. self.where.host .. ":" .. self.where.port,
        "Upgrade: websocket", "Connection: Upgrade", "Sec-WebSocket-Key: " .. self.key, "Sec-WebSocket-Version: 13"}
      for k, v in pairs(self.headers) do lines[#lines + 1] = k .. ": " .. v end
      self:_write(table.concat(lines, "\r\n") .. "\r\n\r\n")
      self.state = "handshake"
    elseif err == "timeout" or err == "Operation already in progress" then
      if socket.gettime() - self.started > 10 then self:_end("couldn't connect (timed out)", nil, out) end
      return out
    else
      self:_end("couldn't connect: " .. tostring(err), nil, out)
      return out
    end
  end
  local werr = self:_flush()
  if werr then
    self:_end("connection lost: " .. werr, nil, out)
    return out
  end
  local rerr = self:_read()
  if self.state == "handshake" then
    local e = self.inbuf:find("\r\n\r\n", 1, true)
    if not e then
      if rerr then self:_end("the server closed the connection during the handshake", nil, out) end
      return out
    end
    local head = self.inbuf:sub(1, e - 1)
    self.inbuf = self.inbuf:sub(e + 4)
    local accept = head:match("[Ss]ec%-[Ww]eb[Ss]ocket%-[Aa]ccept:%s*([^\r\n]+)")
    if not head:match("^HTTP/1.1 101") or accept ~= base64(love.data.hash("sha1", self.key .. GUID)) then
      self:_end("the server refused the WebSocket: " .. (head:match("^[^\r\n]*") or ""), nil, out)
      return out
    end
    self.state, self.was_connected = "open", true
  end
  self:_frames(out)
  if rerr and self.state ~= "closed" then self:_end("the server closed the connection", self.close_code, out) end
  return out
end

function ws:_frames(out)
  while self.state == "open" or self.state == "closing" do
    local buf = self.inbuf
    if #buf < 2 then return end
    local b1, b2 = buf:byte(1, 2)
    local fin, opcode = bit.band(b1, 0x80) ~= 0, bit.band(b1, 0x0f)
    local masked, n = bit.band(b2, 0x80) ~= 0, bit.band(b2, 0x7f)
    local i = 3
    if n == 126 then
      if #buf < 4 then return end
      local a, b = buf:byte(3, 4)
      n, i = a * 256 + b, 5
    elseif n == 127 then
      if #buf < 10 then return end
      n = 0
      for j = 3, 10 do n = n * 256 + buf:byte(j) end
      i = 11
    end
    local key
    if masked then
      if #buf < i + 3 then return end
      key, i = buf:sub(i, i + 3), i + 4
    end
    if #buf < i + n - 1 then return end
    local payload = buf:sub(i, i + n - 1)
    self.inbuf = buf:sub(i + n)
    if key then payload = mask(payload, key) end
    if opcode == 0x8 then  -- close: answer it, and the connection ends
      if #payload >= 2 then self.close_code = payload:byte(1) * 256 + payload:byte(2) end
      if self.state == "open" then self:_write(frame(0x8, payload:sub(1, 2))) end
      self:_flush()
      self:_end("the server closed the connection", self.close_code, out)
      return
    elseif opcode == 0x9 then
      self:_write(frame(0xA, payload))
    elseif opcode == 0xA then
      -- (a pong: nothing to do)
    elseif opcode == 0x0 then
      if self.parts then self.parts[#self.parts + 1] = payload end
      if fin and self.parts then
        out[#out + 1] = {text = table.concat(self.parts)}
        self.parts = nil
      end
    elseif opcode == 0x1 or opcode == 0x2 then
      if fin then
        out[#out + 1] = {text = payload}
      else
        self.parts = {payload}
      end
    end
  end
end

-- Send what is waiting to go (send() sends at once what the socket takes; the rest goes here, or at the next poll).
function ws:flush()
  if self.state == "open" or self.state == "handshake" then self:_flush() end
end

function ws:send(text)
  if self.state ~= "open" then return false end
  self:_write(frame(0x1, text))
  local err = self:_flush()
  return err == nil
end

-- Close with a code (1000: done; 1012: coming back, as the brain reads it). What is still to send goes first, briefly.
function ws:close(code)
  if self.state == "closed" then return end
  if self.state == "open" then
    self:_write(frame(0x8, u16(code or 1000)))
    self.tcp:settimeout(1)
    self:_flush()
  end
  self.state = "closed"
  self.tcp:close()
end

return ws
