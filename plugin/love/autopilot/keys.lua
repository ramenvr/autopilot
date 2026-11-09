-- The brain's key names (Unreal's FKey names, whatever the engine: W, SpaceBar, LeftShift, LeftMouseButton...) as
-- LÖVE's: a keyboard KeyConstant ("w", "space", "lshift"), a mouse button (1 left, 2 right, 3 middle) or the wheel.
-- LÖVE's own names are taken as they are ("a", "space", "return").

local keys = {}

local NAMES = {
  SpaceBar = "space", Space = "space", Enter = "return", Return = "return", Escape = "escape", Tab = "tab",
  BackSpace = "backspace", Delete = "delete", Insert = "insert", Home = "home", End = "end", PageUp = "pageup",
  PageDown = "pagedown", Left = "left", Right = "right", Up = "up", Down = "down", LeftShift = "lshift",
  RightShift = "rshift", LeftControl = "lctrl", RightControl = "rctrl", LeftAlt = "lalt", RightAlt = "ralt",
  LeftCommand = "lgui", RightCommand = "rgui", CapsLock = "capslock", Comma = ",", Period = ".", Slash = "/",
  Semicolon = ";", Apostrophe = "'", LeftBracket = "[", RightBracket = "]", Backslash = "\\", Hyphen = "-",
  Equals = "=", Tilde = "`", Zero = "0", One = "1", Two = "2", Three = "3", Four = "4", Five = "5", Six = "6",
  Seven = "7", Eight = "8", Nine = "9",
  NumPadZero = "kp0", NumPadOne = "kp1", NumPadTwo = "kp2", NumPadThree = "kp3", NumPadFour = "kp4",
  NumPadFive = "kp5", NumPadSix = "kp6", NumPadSeven = "kp7", NumPadEight = "kp8", NumPadNine = "kp9",
}
for i = 1, 12 do NAMES["F" .. i] = "f" .. i end

local MOUSE = {LeftMouseButton = 1, RightMouseButton = 2, MiddleMouseButton = 3, ThumbMouseButton = 4,
  ThumbMouseButton2 = 5}
local WHEEL = {MouseScrollUp = 1, MouseScrollDown = -1, MouseWheelAxis = 0}
local AXES = {MouseX = true, MouseY = true}

-- {kind = "key", key = "w"} | {kind = "mouse", button = 1} | {kind = "wheel", dir = 1} | {kind = "axis", axis =
-- "MouseX"} | nil for a name that is none of them.
function keys.spec(name)
  name = tostring(name or "")
  if name == "" then return nil end
  if MOUSE[name] then return {kind = "mouse", button = MOUSE[name]} end
  if WHEEL[name] then return {kind = "wheel", dir = WHEEL[name]} end
  if AXES[name] then return {kind = "axis", axis = name} end
  local key = NAMES[name]
  if not key and #name == 1 then key = name:lower() end
  if not key then
    -- LÖVE's own name ("lshift", "return", "kp5"), if LÖVE knows it
    local ok, sc = pcall(love.keyboard.getScancodeFromKey, name)
    if ok and sc and sc ~= "unknown" then key = name end
  end
  if not key then return nil end
  local ok, sc = pcall(love.keyboard.getScancodeFromKey, key)
  return {kind = "key", key = key, scancode = ok and sc or key}
end

-- The brain's name for a LÖVE key ("w" -> "W", "space" -> "SpaceBar"), for what the game's code reads.
local BACK = {}
for unreal, love_name in pairs(NAMES) do
  if not BACK[love_name] or #unreal > #BACK[love_name] then BACK[love_name] = unreal end
end
BACK["space"], BACK["return"] = "SpaceBar", "Enter"

function keys.unreal_name(love_key)
  if BACK[love_key] then return BACK[love_key] end
  if #love_key == 1 and love_key:match("%a") then return love_key:upper() end
  return love_key
end

return keys
