-- The Roblox plugin's test game, Lantern Grove (made up for these tests). LanternGrove.rbxl, beside this, is the game
-- saved as a place: open it in Studio. This is its source: run it in Studio's command bar (or
-- `python tools/roblox_dev.py eval - < plugin/roblox/tests/testgame.luau`) on an empty Baseplate, and after changing
-- it, run it in LanternGrove.rbxl and save the place. It builds, again each time (what it built before is replaced):
--  - Workspace/Grove: six Lanterns (a "Lantern" tag; touching one collects it), a Lever whose ProximityPrompt (E)
--    opens the Gate, a Crate to jump on, a Pond, a Mailbox with a ClickDetector, a row of three same-named Posts, and
--    for two players a Basket and a Cart: a player picks the basket up (E at it), its carrier hands it to another player
--    (G at them: a "HandOver" prompt on every player), and the cart takes it (E at the cart) only from a player it was
--    handed to (the Cart's Loaded and LoadedBy; a refusal says why in its Refused); and away to the west the Lookout,
--    a platform higher than a jump whose only way up is a Ramp, with a Beacon on top;
--  - ReplicatedStorage/GameState (a ModuleScript: collected, gateOpen, total, addLantern, basketCarrier, handoffs,
--    basketLoaded) and Remotes (Ring, Wave, BasketChanged);
--  - ServerScriptService/GroveService: leaderstats.Lanterns, collecting, the lever, the remotes, the basket;
--  - StarterGui/HUD ("Lanterns n / 6", "Basket: ...") and Menu (a Ring button, a Name box);
--  - StarterPack/Torch: a Tool (the hotbar's first);
--  - StarterPlayerScripts/GroveControls: Q waves (ContextActionService action "Wave"); a player's own HandOver prompt
--    is off for that player.
-- Three planted bugs: the Mailbox's script indexes nil when clicked (log_error), the Gate's door, once opened, falls
-- through the ground (out_of_world), and (two players) the basket's news goes only to the player who first picked it up,
-- so the HUD of a player it was handed to still says "Basket: on the ground". The game's own scripts don't say so: the
-- agent reads them (the codex), and a test of it must not hand it the answers.
local CollectionService = game:GetService("CollectionService")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local ServerScriptService = game:GetService("ServerScriptService")
local StarterGui = game:GetService("StarterGui")
local StarterPack = game:GetService("StarterPack")
local StarterPlayer = game:GetService("StarterPlayer")

for _, old in pairs({ workspace:FindFirstChild("Grove"), ReplicatedStorage:FindFirstChild("GameState"),
	ReplicatedStorage:FindFirstChild("Remotes"), ServerScriptService:FindFirstChild("GroveService"),
	StarterGui:FindFirstChild("HUD"), StarterGui:FindFirstChild("Menu"),
	StarterPlayer.StarterPlayerScripts:FindFirstChild("GroveControls"), StarterPack:FindFirstChild("Torch") }) do
	old:Destroy()
end

local function part(props)
	local p = Instance.new("Part")
	for k, v in pairs(props) do
		if k ~= "Parent" then
			p[k] = v
		end
	end
	p.Anchored = if props.Anchored == nil then true else props.Anchored
	p.Parent = props.Parent
	return p
end

local grove = Instance.new("Folder")
grove.Name = "Grove"

local lanterns = Instance.new("Folder")
lanterns.Name = "Lanterns"
lanterns.Parent = grove
for i = 1, 6 do
	local l = part({ Name = "Lantern", Size = Vector3.new(1, 2, 1), Position = Vector3.new(-12 + (i - 1) * 5, 2, -24),
		Color = Color3.fromRGB(255, 200, 80), Material = Enum.Material.Neon, CanCollide = false, Parent = lanterns })
	CollectionService:AddTag(l, "Lantern")
	local light = Instance.new("PointLight")
	light.Brightness = 2
	light.Parent = l
end

local lever = Instance.new("Model")
lever.Name = "Lever"
local base = part({ Name = "Base", Size = Vector3.new(2, 1, 2), Position = Vector3.new(14, 0.5, -10), Parent = lever })
local handle = part({ Name = "Handle", Size = Vector3.new(0.4, 3, 0.4), Position = Vector3.new(14, 2.5, -10),
	Color = Color3.fromRGB(120, 80, 40), Parent = lever })
lever.PrimaryPart = base
local prompt = Instance.new("ProximityPrompt")
prompt.ActionText = "Pull"
prompt.ObjectText = "Lever"
prompt.KeyboardKeyCode = Enum.KeyCode.E
prompt.MaxActivationDistance = 10
prompt.RequiresLineOfSight = false
prompt.Parent = handle
lever.Parent = grove

local gate = Instance.new("Model")
gate.Name = "Gate"
part({ Name = "PostLeft", Size = Vector3.new(1, 8, 1), Position = Vector3.new(-5, 4, -40), Parent = gate })
part({ Name = "PostRight", Size = Vector3.new(1, 8, 1), Position = Vector3.new(5, 4, -40), Parent = gate })
part({ Name = "Door", Size = Vector3.new(9, 7, 1), Position = Vector3.new(0, 3.5, -40), Color = Color3.fromRGB(90, 60, 30),
	Parent = gate })
gate:SetAttribute("Open", false)
gate.Parent = grove

part({ Name = "Crate", Size = Vector3.new(6, 4, 6), Position = Vector3.new(-16, 2, -6), Color = Color3.fromRGB(160, 120, 70),
	Material = Enum.Material.WoodPlanks, Parent = grove })
part({ Name = "Pond", Size = Vector3.new(14, 0.4, 10), Position = Vector3.new(20, 0.2, 10), Color = Color3.fromRGB(40, 90, 200),
	Transparency = 0.3, CanCollide = false, Parent = grove })

local mailbox = part({ Name = "Mailbox", Size = Vector3.new(2, 4, 2), Position = Vector3.new(-8, 2, 8),
	Color = Color3.fromRGB(200, 40, 40), Parent = grove })
local click = Instance.new("ClickDetector")
click.MaxActivationDistance = 40
click.Parent = mailbox
local mailScript = Instance.new("Script")
mailScript.Name = "MailboxScript"
mailScript.Source = [[
-- The mailbox opens its flap when clicked
local click = script.Parent:WaitForChild("ClickDetector")
click.MouseClick:Connect(function(player)
	local flap = script.Parent:FindFirstChild("Flap")
	flap.Transparency = 1
end)
]]
mailScript.Parent = mailbox

local basket = part({ Name = "Basket", Size = Vector3.new(2, 1.5, 2), Position = Vector3.new(0, 0.75, 14),
	Color = Color3.fromRGB(170, 110, 50), Material = Enum.Material.Fabric, Parent = grove })
basket:SetAttribute("Carrier", "")
basket:SetAttribute("HandedOver", false)
local pick = Instance.new("ProximityPrompt")
pick.Name = "PickUp"
pick.ActionText = "Pick up"
pick.ObjectText = "Basket"
pick.KeyboardKeyCode = Enum.KeyCode.E
pick.MaxActivationDistance = 8
pick.RequiresLineOfSight = false
pick.Parent = basket
local cart = part({ Name = "Cart", Size = Vector3.new(6, 3, 4), Position = Vector3.new(-20, 1.5, 20),
	Color = Color3.fromRGB(110, 110, 120), Material = Enum.Material.Metal, Parent = grove })
cart:SetAttribute("Loaded", false)
cart:SetAttribute("LoadedBy", "")
cart:SetAttribute("Refused", "")
local load = Instance.new("ProximityPrompt")
load.Name = "Load"
load.ActionText = "Load the basket"
load.ObjectText = "Cart"
load.KeyboardKeyCode = Enum.KeyCode.E
load.MaxActivationDistance = 10
load.RequiresLineOfSight = false
load.Parent = cart

local posts = Instance.new("Folder")
posts.Name = "Posts"
posts.Parent = grove
for i = 1, 3 do
	part({ Name = "Post", Size = Vector3.new(1, 3, 1), Position = Vector3.new(8 + i * 3, 1.5, 4), Parent = posts })
end

-- The Lookout: a platform 9 studs high (higher than a jump) at x -64..-52, z -6..6, its only way up a Ramp rising
-- toward -x from its foot at x -34 (18 studs on, 9 up: about 27 degrees), a Beacon on top
part({ Name = "Lookout", Size = Vector3.new(12, 9, 12), Position = Vector3.new(-58, 4.5, 0),
	Color = Color3.fromRGB(120, 120, 110), Material = Enum.Material.Slate, Parent = grove })
local rampTilt = -math.atan2(9, 18)
local rampCf = CFrame.new(-43, 4.5, 0) * CFrame.Angles(0, 0, rampTilt)
part({ Name = "Ramp", Size = Vector3.new(math.sqrt(18 * 18 + 9 * 9), 1, 8), CFrame = rampCf * CFrame.new(0, -0.5, 0),
	Color = Color3.fromRGB(150, 110, 70), Material = Enum.Material.WoodPlanks, Parent = grove })
part({ Name = "Beacon", Size = Vector3.new(1, 3, 1), Position = Vector3.new(-60, 10.5, 0),
	Color = Color3.fromRGB(80, 200, 255), Material = Enum.Material.Neon, CanCollide = false, Parent = grove })

-- A Torch in every player's backpack (the hotbar's first: One equips it)
local torch = Instance.new("Tool")
torch.Name = "Torch"
torch.CanBeDropped = false
part({ Name = "Handle", Size = Vector3.new(0.4, 3, 0.4), Anchored = false, CanCollide = false,
	Color = Color3.fromRGB(110, 70, 40), Parent = torch })
torch.Parent = StarterPack
grove.Parent = workspace

local state = Instance.new("ModuleScript")
state.Name = "GameState"
state.Source = [[
-- The grove's state, shared by the server's scripts
local GameState = { collected = 0, total = 6, gateOpen = false, ringer = "", basketCarrier = "", handoffs = 0,
	basketLoaded = false }

function GameState.addLantern(n)
	GameState.collected += (n or 1)
	return GameState.collected
end

function GameState:summary()
	return string.format("%d/%d lanterns, gate %s", self.collected, self.total, if self.gateOpen then "open" else "shut")
end

return GameState
]]
state.Parent = ReplicatedStorage

local remotes = Instance.new("Folder")
remotes.Name = "Remotes"
for _, name in ipairs({ "Ring", "Wave", "BasketChanged" }) do
	local r = Instance.new("RemoteEvent")
	r.Name = name
	r.Parent = remotes
end
remotes.Parent = ReplicatedStorage

local service = Instance.new("Script")
service.Name = "GroveService"
service.Source = [[
-- The grove's rules: lanterns are collected by touching them, the lever opens the gate, the remotes ring and wave
local CollectionService = game:GetService("CollectionService")
local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local GameState = require(ReplicatedStorage:WaitForChild("GameState"))
local Remotes = ReplicatedStorage:WaitForChild("Remotes")

Players.PlayerAdded:Connect(function(player)
	local stats = Instance.new("Folder")
	stats.Name = "leaderstats"
	local lanterns = Instance.new("IntValue")
	lanterns.Name = "Lanterns"
	lanterns.Parent = stats
	stats.Parent = player
	player:SetAttribute("Waves", 0)
end)

local function collect(lantern)
	local taken = false
	lantern.Touched:Connect(function(hit)
		if taken then
			return
		end
		local player = Players:GetPlayerFromCharacter(hit.Parent)
		if player then
			taken = true
			player.leaderstats.Lanterns.Value += 1
			GameState.addLantern(1)
			lantern:Destroy()
		end
	end)
end
for _, l in ipairs(CollectionService:GetTagged("Lantern")) do
	collect(l)
end

local gate = workspace.Grove.Gate
workspace.Grove.Lever.Handle.ProximityPrompt.Triggered:Connect(function(player)
	GameState.gateOpen = true
	gate:SetAttribute("Open", true)
	workspace.Grove.Lever.Handle.Orientation = Vector3.new(0, 0, 45)
	local door = gate.Door
	door.CanCollide = false
	door.Anchored = false
end)

Remotes.Ring.OnServerEvent:Connect(function(player, name)
	GameState.ringer = tostring(name or player.Name)
end)
Remotes.Wave.OnServerEvent:Connect(function(player)
	player:SetAttribute("Waves", (player:GetAttribute("Waves") or 0) + 1)
end)

-- The basket: picked up (E at it), handed over by its carrier (G at another player), loaded onto the cart (E at it)
-- only by a player it was handed to: two players' work
local basket = workspace.Grove.Basket
local cart = workspace.Grove.Cart
local firstCarrier = nil -- (who picked it up)

local function carrier()
	local name = basket:GetAttribute("Carrier")
	return if name and name ~= "" then Players:FindFirstChild(name) else nil
end

local function tell(text)
	if firstCarrier then
		Remotes.BasketChanged:FireClient(firstCarrier, text)
	end
end

local function unweld()
	for _, w in ipairs(basket:GetChildren()) do
		if w:IsA("WeldConstraint") then
			w:Destroy()
		end
	end
end

local function carry(player)
	local root = player.Character and player.Character:FindFirstChild("HumanoidRootPart")
	if root == nil then
		return false
	end
	unweld()
	basket.Anchored = false
	basket.CanCollide = false
	basket.Massless = true
	basket.CFrame = root.CFrame * CFrame.new(0, 3.5, 0)
	local weld = Instance.new("WeldConstraint")
	weld.Part0, weld.Part1 = root, basket
	weld.Parent = basket
	basket:SetAttribute("Carrier", player.Name)
	GameState.basketCarrier = player.Name
	return true
end

basket.PickUp.Triggered:Connect(function(player)
	if carrier() or GameState.basketLoaded then
		return
	end
	if carry(player) then
		firstCarrier = player
		basket.PickUp.Enabled = false
		tell("Basket: " .. player.Name .. " carries it")
	end
end)

local function handOverPrompt(player, root)
	local p = Instance.new("ProximityPrompt")
	p.Name = "HandOver"
	p.ActionText = "Hand over the basket"
	p.ObjectText = player.Name
	p.KeyboardKeyCode = Enum.KeyCode.G
	p.MaxActivationDistance = 8
	p.RequiresLineOfSight = false
	p.Parent = root
	p.Triggered:Connect(function(giver)
		if giver == player or giver ~= carrier() then
			return
		end
		if carry(player) then
			basket:SetAttribute("HandedOver", true)
			GameState.handoffs += 1
			tell("Basket: " .. player.Name .. " carries it")
		end
	end)
end

local function joined(player)
	local function spawned(char)
		handOverPrompt(player, char:WaitForChild("HumanoidRootPart"))
	end
	player.CharacterAdded:Connect(spawned)
	if player.Character then
		spawned(player.Character)
	end
end
Players.PlayerAdded:Connect(joined)
for _, p in ipairs(Players:GetPlayers()) do
	joined(p)
end

cart.Load.Triggered:Connect(function(player)
	if player ~= carrier() then
		return
	end
	if not basket:GetAttribute("HandedOver") then
		cart:SetAttribute("Refused", "the cart takes a basket that was handed over")
		return
	end
	unweld()
	basket.Anchored = true
	basket.CFrame = cart.CFrame * CFrame.new(0, 2.5, 0)
	basket:SetAttribute("Carrier", "")
	cart:SetAttribute("Loaded", true)
	cart:SetAttribute("LoadedBy", player.Name)
	GameState.basketLoaded = true
	GameState.basketCarrier = ""
	cart.Load.Enabled = false
	tell("Basket: on the cart")
end)
]]
service.Parent = ServerScriptService

local hud = Instance.new("ScreenGui")
hud.Name = "HUD"
hud.ResetOnSpawn = false
local count = Instance.new("TextLabel")
count.Name = "LanternCount"
count.Size = UDim2.fromOffset(220, 40)
count.Position = UDim2.fromOffset(20, 20)
count.BackgroundTransparency = 0.4
count.TextScaled = true
count.Text = "Lanterns 0 / 6"
count.Parent = hud
local basketLabel = Instance.new("TextLabel")
basketLabel.Name = "BasketLabel"
basketLabel.Size = UDim2.fromOffset(260, 32)
basketLabel.Position = UDim2.fromOffset(20, 66)
basketLabel.BackgroundTransparency = 0.4
basketLabel.TextScaled = true
basketLabel.Text = "Basket: on the ground"
basketLabel.Parent = hud
local hudScript = Instance.new("LocalScript")
hudScript.Name = "HudScript"
hudScript.Source = [[
local player = game:GetService("Players").LocalPlayer
local label = script.Parent:WaitForChild("LanternCount")
local value = player:WaitForChild("leaderstats"):WaitForChild("Lanterns")
local function show()
	label.Text = string.format("Lanterns %d / 6", value.Value)
end
value.Changed:Connect(show)
show()
local basketLabel = script.Parent:WaitForChild("BasketLabel")
game:GetService("ReplicatedStorage"):WaitForChild("Remotes"):WaitForChild("BasketChanged").OnClientEvent:Connect(
	function(text)
		basketLabel.Text = text
	end)
]]
hudScript.Parent = hud
hud.Parent = StarterGui

local menu = Instance.new("ScreenGui")
menu.Name = "Menu"
menu.ResetOnSpawn = false
local ring = Instance.new("TextButton")
ring.Name = "RingButton"
ring.Size = UDim2.fromOffset(160, 50)
ring.Position = UDim2.new(1, -180, 0, 20)
ring.Text = "Ring the bell"
ring.Parent = menu
local nameBox = Instance.new("TextBox")
nameBox.Name = "NameBox"
nameBox.Size = UDim2.fromOffset(160, 40)
nameBox.Position = UDim2.new(1, -180, 0, 80)
nameBox.PlaceholderText = "Your name"
nameBox.Text = ""
nameBox.Parent = menu
local menuScript = Instance.new("LocalScript")
menuScript.Name = "MenuScript"
menuScript.Source = [[
local Remotes = game:GetService("ReplicatedStorage"):WaitForChild("Remotes")
local menu = script.Parent
menu:WaitForChild("RingButton").Activated:Connect(function()
	Remotes.Ring:FireServer(menu.NameBox.Text)
	menu.RingButton.Text = "Rang!"
end)
]]
menuScript.Parent = menu
menu.Parent = StarterGui

local controls = Instance.new("LocalScript")
controls.Name = "GroveControls"
controls.Source = [[
-- Q waves (an action of ContextActionService)
local ContextActionService = game:GetService("ContextActionService")
local Remotes = game:GetService("ReplicatedStorage"):WaitForChild("Remotes")
ContextActionService:BindAction("Wave", function(_, state)
	if state == Enum.UserInputState.Begin then
		Remotes.Wave:FireServer()
	end
	return Enum.ContextActionResult.Pass
end, false, Enum.KeyCode.Q)

-- A player's own HandOver prompt is off for that player (handing the basket to yourself isn't a thing, and the nearest
-- G prompt would always be your own)
local player = game:GetService("Players").LocalPlayer
local function spawned(char)
	local prompt = char:WaitForChild("HumanoidRootPart"):WaitForChild("HandOver", 30)
	if prompt then
		prompt.Enabled = false
	end
end
player.CharacterAdded:Connect(spawned)
if player.Character then
	task.spawn(spawned, player.Character)
end
]]
controls.Parent = StarterPlayer.StarterPlayerScripts

return "Lantern Grove is built"
