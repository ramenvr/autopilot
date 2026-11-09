-- Coin Field: the LÖVE module's test game. A square walks a field (arrows or W/A/S/D), takes coins (+10 each), and a
-- slime patrols between two posts. Space jumps (a hop: the square rises and lands). E rings the bell, whose script has
-- a planted bug (it calls a method on nil: a log_error for the detectors). The HUD shows the score, and the lives the
-- way a terminal-styled game does it: one glyph at a time. Some of its state is in main.lua's locals, as many games
-- keep it: a flock of birds (in a "_private" field) and a sprite atlas's frames (data with an x and a y, not things).

require("autopilot")

player = {x = 100, y = 300, w = 32, h = 32, speed = 180, jumpForce = 420, gravity = 1200, vy = 0, onGround = true,
  ground = 300}
coins = {}
slimes = {}
bell = {x = 600, y = 100, w = 24, h = 24, rung = 0}
score = 0
gameState = {level = 1, paused = false}
local flock = {_birds = {}}
local atlas = {}
for i = 1, 200 do atlas[i] = {x = (i % 20) * 40, y = math.floor(i / 20) * 40, w = 40, h = 40} end

local function reset()
  coins = {}
  for i = 1, 5 do coins[i] = {x = 160 + i * 80, y = 316, r = 8, type = "Coin", value = 10} end
  slimes = {{x = 500, y = 316, r = 14, kind = "Slime", vx = 60, left = 420, right = 700}}
  for i = 1, 3 do flock._birds[i] = {x = 100 + i * 150, y = 60, radius = 6, kind = "Bird", vx = 40} end
end

function love.load()
  reset()
end

local function overlaps(p, c)
  local cx, cy = p.x + p.w / 2, p.y + p.h / 2
  return math.abs(cx - c.x) < p.w / 2 + c.r and math.abs(cy - c.y) < p.h / 2 + c.r
end

function bell.ring(self, times)
  self.rung = self.rung + (times or 1)
  return self.rung
end

function love.update(dt)
  local dx = 0
  if love.keyboard.isDown("right", "d") then dx = dx + 1 end
  if love.keyboard.isDown("left", "a") then dx = dx - 1 end
  player.x = math.max(0, math.min(800 - player.w, player.x + dx * player.speed * dt))
  if not player.onGround then
    player.vy = player.vy + player.gravity * dt
    player.y = player.y + player.vy * dt
    if player.y >= player.ground then
      player.y, player.vy, player.onGround = player.ground, 0, true
    end
  end
  for i = #coins, 1, -1 do
    if overlaps(player, coins[i]) then
      score = score + coins[i].value
      table.remove(coins, i)
    end
  end
  for _, s in ipairs(slimes) do
    s.x = s.x + s.vx * dt
    if s.x > s.right or s.x < s.left then s.vx = -s.vx end
  end
  for _, b in ipairs(flock._birds) do b.x = (b.x + b.vx * dt) % 800 end
end

function love.keypressed(key)
  if key == "space" and player.onGround then
    player.vy, player.onGround = -player.jumpForce, false
  elseif key == "e" then
    local clapper = nil
    clapper:swing()  -- the planted bug
  elseif key == "r" then
    reset()
  end
end

function love.draw()
  love.graphics.setColor(0.3, 0.6, 0.3)
  love.graphics.rectangle("fill", 0, 332, 800, 268)
  love.graphics.setColor(1, 0.85, 0.2)
  for _, c in ipairs(coins) do love.graphics.circle("fill", c.x, c.y, c.r) end
  love.graphics.setColor(0.4, 0.4, 1)
  for _, s in ipairs(slimes) do love.graphics.circle("fill", s.x, s.y, s.r) end
  love.graphics.setColor(0.8, 0.8, 0.8)
  love.graphics.rectangle("fill", bell.x, bell.y, bell.w, bell.h)
  love.graphics.setColor(1, 1, 1)
  love.graphics.rectangle("fill", player.x, player.y, player.w, player.h)
  love.graphics.print("Score: " .. score, 10, 10)
  local glyph = love.graphics.getFont():getWidth("M")
  local lives = "Lives: 3"
  for i = 1, #lives do
    local ch = lives:sub(i, i)
    if ch ~= " " then love.graphics.print(ch, 10 + (i - 1) * glyph, 30) end
  end
  love.graphics.setColor(0, 0, 0)
  love.graphics.print("Coin Field", 361, 11)  -- (a shadow, under the title)
  love.graphics.setColor(1, 1, 1)
  love.graphics.print("Coin Field", 360, 10)
  for _, b in ipairs(flock._birds) do love.graphics.circle("fill", b.x, b.y, b.radius) end
  local _ = atlas[1]  -- (the atlas is one of love.draw's locals, as a sprite batch's frames would be)
end
