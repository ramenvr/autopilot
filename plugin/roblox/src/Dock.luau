--!strict
-- The Autopilot dock (Plugins tab > Autopilot), as the Autopilot panel of the Unreal, Unity and Godot plugins: it
-- connects to the agent service by itself once the user has started it with `autopilot serve` (a Roblox plugin can't
-- start programs; the connect card shows only while it isn't connected: what to run, waiting for it, or Connect after
-- the user's Disconnect); compose a run (one prompt box: the agent service reads what kind
-- of run the words ask for), follow it live (its stages, what the agent does now, the harness attempts, the feed, the
-- Policy log) and read its result; the runs in a run history (a rail when the dock is wide, a dropdown when narrow;
-- kept in the plugin's settings), the gear (the Settings page: the account, the runs' bills, data sharing, the research
-- agent, the Design brief, Show Policy Log), the ⋮ menu (Connect or Disconnect, Settings, Rerun setup, Reset memory…),
-- the Memory row (Reset memory) and, until the user went through it, the first-run setup in place of everything else.
-- It is pure UI over the editor's link to the agent service (Editor.luau); the pages are RunPage, PolicyLog,
-- SettingsPage and SetupPage, the look Theme, the runs RunModel.
local HttpService = game:GetService("HttpService")
local RunService = game:GetService("RunService")

local Json = require(script.Parent.Json)
local M = require(script.Parent.RunModel)
local Settings = require(script.Parent.Settings)
local T = require(script.Parent.Theme)

local C = T.C

local Dock = {}
Dock.__index = Dock

local WIDE_AT = 700
local RAIL_WIDTH = 280
local HISTORY = 50 -- the runs kept in the plugin's settings (every place's)
local STATUS_FOR = 8 -- seconds a status line message stays
local COMPOSER_WIDTH = 620
local CONNECT_WIDTH = 540
-- The Policy log: a run's policy steps kept (the oldest go), and the rows shown of them
local POLICY_KEPT = 300
local POLICY_SHOWN = 100
Dock.POLICY_KEPT = POLICY_KEPT
Dock.POLICY_SHOWN = POLICY_SHOWN
Dock.WAITING_FOR_GAME = "Waiting for the game to start"
Dock.RUN_GOING = "A run is going: wait for its result, or stop it"
local PROMPT_PLACEHOLDER = "Describe what to verify, reach or explore"
local RUN_NOTE = "A run is going: stop it or wait for its result."
local RESET_TEXT = "The agent forgets what it has learned about this game."
local SERVE_COMMAND = "autopilot serve"
-- (the dock a play session's client draws, Mirror.luau: what it can't do until the playtest stops)
Dock.DURING_PLAY = "Not during play: once the playtest stops"
Dock.SERVE_COMMAND = SERVE_COMMAND
-- The editor's own status lines the status line doesn't show (the connect card, the Live page and the feed say them)
local QUIET = { "Connecting to ", "Connected to the agent service", "Run started", "Run finished", "Finding: ",
	"Can't reach the agent service", "Connection closed", "Disconnected" }

-- Lazily required pages (they require this module's constants through the dock object, not the module)
local RunPage, SettingsPage, SetupPage

function Dock.new(plugin: Plugin, editor: any, options: any?)
	RunPage = RunPage or require(script.Parent.RunPage)
	SettingsPage = SettingsPage or require(script.Parent.SettingsPage)
	SetupPage = SetupPage or require(script.Parent.SetupPage)
	local o = options or {}
	local self = setmetatable({}, Dock) :: any
	-- (the dock a play session's client draws while the playtest runs, Mirror.luau: the edit dock's state and events
	-- over the pipe, its actions sent back; it writes no setting and keeps no run)
	self.mirror = o.mirror == true
	self.plugin = if self.mirror then Dock.readOnly(plugin) else plugin
	self.editor = editor
	self.conns = {}
	self.preview = o.mount ~= nil -- (mounted elsewhere, e.g. a ScreenGui for a look at it: keeps nothing)
	-- (a mount drawn at a UIScale: the dock's own sizes are its pixels divided by it)
	self.scale = if type(o.scale) == "number" and o.scale > 0 then o.scale else 1
	self.runs = {} -- this place's runs, newest first
	self.others = {} -- the other places' (kept as they are)
	self.active = nil -- the run going on
	self.shown = nil -- the run shown
	self.lastRun = nil -- the run shown before the connect page was asked for
	self.composing = false
	self.tab = "main" -- main (live or result) | feed | policy
	self.wide = false
	self.settingsOpen = false
	self.setupOpen = false
	self.reset = "" -- Reset memory: "" | "ask" | "working" | "done" | "failed"
	self.resetWhy = ""
	self.resetUntil = 0
	self.signIn = nil -- the Aura sign-in asked for here: {state, url, email, message}
	self.statusUntil = 0
	self.pendingUsage = nil
	self.pendingBill = nil
	self:_loadHistory()

	-- the widget (or the mount)
	local host: Instance
	if o.mount then
		host = o.mount
	else
		host = self:_widget()
		self.widget = host
	end
	self.host = host
	pcall(function()
		(host :: any).ZIndexBehavior = Enum.ZIndexBehavior.Sibling
	end)
	self:_build()
	T.checkFonts(function()
		if self.root and self.root.Parent then
			self:_rebuildFonts()
		end
	end)
	if not self.preview then
		editor.activitySince = function()
			local r = self.active or self.runs[1]
			if r ~= nil and (r:live() or r.lostConnection) then
				return r.lastActivityId or 0
			end
			return -1
		end
	end
	self:_listen()
	table.insert(self.conns, self.root:GetPropertyChangedSignal("AbsoluteSize"):Connect(function()
		self:_resized()
	end))
	local tick = 0
	table.insert(self.conns, RunService.Heartbeat:Connect(function(dt)
		tick += dt
		if tick >= 0.25 then
			tick = 0
			self:_tick()
		end
	end))
	self:_resized()
	self:render()
	return self
end

-- The plugin, its settings read only (the mirror's: the edit dock keeps them).
function Dock.readOnly(plugin: any): any
	return {
		GetSetting = function(_, key: string): any
			return plugin:GetSetting(key)
		end,
		SetSetting = function() end,
	}
end

function Dock:_widget(): Instance
	local plugin = self.plugin
	-- (the widget and the toolbar outlive a restart of the plugin's code, the dev loader's: Studio keeps a widget's id
	-- taken for the plugin's life)
	local kept = (_G :: any).AutopilotDockKept
	if kept == nil then
		kept = setmetatable({}, { __mode = "k" })
		;(_G :: any).AutopilotDockKept = kept
	end
	local mine = kept[plugin]
	if mine == nil or mine.widget == nil then
		mine = {}
		local info = DockWidgetPluginGuiInfo.new(Enum.InitialDockState.Right, false, false, 420, 640, 300, 360)
		-- (a widget an earlier copy of the code made, before this was kept, holds its id: the next one is taken)
		for i = 1, 50 do
			local ok, w = pcall(function()
				return plugin:CreateDockWidgetPluginGuiAsync(if i == 1 then "AutopilotDock" else "AutopilotDock" .. i, info)
			end)
			if ok then
				mine.widget = w
				break
			end
		end
		local ok, toolbar = pcall(function()
			return plugin:CreateToolbar("Autopilot")
		end)
		if ok then
			mine.toolbar = toolbar
			local okb, b = pcall(function()
				return toolbar:CreateButton("Autopilot", "Playtest this game with Autopilot", "rbxassetid://4458901886")
			end)
			if okb then
				mine.button = b
				b.ClickableWhenViewportHidden = true
			end
		end
		kept[plugin] = mine
	end
	local widget = mine.widget
	widget.Title = "Autopilot"
	widget.Name = "AutopilotDock"
	for _, c in ipairs(widget:GetChildren()) do
		c:Destroy()
	end
	if mine.button then
		table.insert(self.conns, mine.button.Click:Connect(function()
			widget.Enabled = not widget.Enabled
		end))
	end
	-- (whether the dock is open, for the mirror of a playtest to open as it: not while hidden for play)
	Settings.set(plugin, "dock_open", widget.Enabled)
	table.insert(self.conns, widget:GetPropertyChangedSignal("Enabled"):Connect(function()
		if not self.playHidden then
			Settings.set(plugin, "dock_open", widget.Enabled)
		end
	end))
	return widget
end

-- --- the run history kept -------------------------------------------------------------------------------------------

-- This place, as the history tells places apart.
function Dock:project(): string
	local ed = self.editor
	local id = ed:projectId()
	return id or ("place:" .. tostring(ed:projectName()))
end

function Dock:_loadHistory()
	local ok, saved = pcall(function()
		return Settings.get(self.plugin, "history")
	end)
	if not ok or type(saved) ~= "table" then
		return
	end
	local here = self:project()
	for _, t in ipairs(saved) do
		if type(t) == "table" then
			if t.project == nil or t.project == here then
				local okr, r = pcall(M.fromSaved, t)
				if okr then
					table.insert(self.runs, r)
				end
			else
				table.insert(self.others, t)
			end
		end
	end
end

-- The runs written to the plugin's settings (the ended ones: a run going is kept once it ends).
function Dock:save()
	if self.preview then
		return
	end
	local out = {}
	for _, r in ipairs(self.runs) do
		if not r:live() then
			table.insert(out, r:saved())
		end
	end
	for _, t in ipairs(self.others) do
		table.insert(out, t)
	end
	while #out > HISTORY do
		table.remove(out)
	end
	Settings.set(self.plugin, "history", out)
end

-- --- building -------------------------------------------------------------------------------------------------------

function Dock:_build()
	local root = Instance.new("Frame")
	root.Name = "AutopilotRoot"
	root.Size = UDim2.fromScale(1, 1)
	root.BackgroundColor3 = C.ground
	root.BorderSizePixel = 0
	root.ClipsDescendants = true
	root.Parent = self.host
	self.root = root

	local body = Instance.new("Frame")
	body.Name = "Body"
	body.Size = UDim2.fromScale(1, 1)
	body.BackgroundTransparency = 1
	body.Parent = root
	T.list(body, 0, true).VerticalAlignment = Enum.VerticalAlignment.Top
	self.body = body

	-- the rail (wide): the run history and the Memory row
	local rail = Instance.new("Frame")
	rail.Name = "Rail"
	rail.Size = UDim2.new(0, RAIL_WIDTH, 1, 0)
	rail.BackgroundColor3 = C.rail
	rail.BorderSizePixel = 0
	rail.LayoutOrder = 1
	rail.Parent = body
	T.list(rail, 0)
	self.rail = rail
	local _, rh = T.bar(rail, { bg = C.rail, pad = { 14, 12, 14, 12 }, horizontal = true, gap = 8, name = "Head" })
	T.label(rh, "History", "strong", { wrap = false, fill = true })
	self.railGear = T.gearButton(rh, function()
		self:toggleSettings()
	end, true)
	T.button(rh, "New run", "primary", function()
		self:compose("")
	end, true)
	T.moreButton(rh, function()
		self:openMenu(rh)
	end, true)
	local list = T.scroll(rail, "List")
	list.Size = UDim2.new(1, 0, 0, 0)
	T.fill(list)
	T.list(list, 0)
	self.railList = list
	self.railFoot = T.col(rail, 0, "FooterSlot")
	local edge = Instance.new("Frame")
	edge.Name = "RailEdge"
	edge.BorderSizePixel = 0
	edge.BackgroundColor3 = C.rule
	edge.Size = UDim2.new(0, 1, 1, 0)
	edge.LayoutOrder = 2
	edge.Parent = body
	self.railEdge = edge

	local main = Instance.new("Frame")
	main.Name = "Main"
	main.BackgroundTransparency = 1
	main.Size = UDim2.new(0, 0, 1, 0)
	main.LayoutOrder = 3
	main.ClipsDescendants = true
	T.fill(main)
	main.Parent = body
	T.list(main, 0)
	self.main = main

	-- the top (narrow): the history button (the run shown, its list under it), the gear, New run and ⋮
	local top, th = T.bar(main, { pad = { 10, 8, 10, 8 }, horizontal = true, gap = 6, name = "Top" })
	self.top = top
	self.topInner = th
	local hb = T.button(th, "", "drop", function()
		self:openHistory()
	end, false, { name = "History" })
	hb.AutomaticSize = Enum.AutomaticSize.None
	hb.Size = UDim2.new(0, 0, 0, 28)
	T.fill(hb)
	T.pad(hb, 8, 0, 8, 0)
	local hrow = Instance.new("Frame")
	hrow.BackgroundTransparency = 1
	hrow.Size = UDim2.fromScale(1, 1)
	hrow.Parent = hb
	T.list(hrow, 8, true)
	self.histBtn = hb
	self.histRow = hrow
	self.topGear = T.gearButton(th, function()
		self:toggleSettings()
	end)
	T.button(th, "New run", "primary", function()
		self:compose("")
	end)
	T.moreButton(th, function()
		self:openMenu(th)
	end)

	-- the line shown above a finished run while not connected: what to run for the next one (not an alert), or Connect
	-- after the user's Disconnect
	local strip, si = T.bar(main, { bg = C.plate, edge = C.rule, pad = { 10, 7, 10, 7 }, horizontal = true,
		gap = 8, name = "Strip" })
	self.strip = strip
	self.stripText = T.label(si, "", "small", { color = C.muted, wrap = false, fill = true })
	self.stripBtn = T.button(si, "Connect", "default", function()
		self.editor:connect()
		self:render()
	end, true)

	local pages = Instance.new("Frame")
	pages.Name = "Pages"
	pages.BackgroundTransparency = 1
	pages.Size = UDim2.new(1, 0, 0, 0)
	pages.ClipsDescendants = true
	pages.LayoutOrder = T.nextOrder()
	T.fill(pages)
	pages.Parent = main
	self.pages = pages
	self.connectPage = self:_page("Connect")
	self.composerPage = self:_page("Composer")
	self.runPageFrame = self:_page("Run")
	self.settingsPage = self:_page("Settings")
	self:_buildConnect()
	self:_buildComposer()
	self.runPage = RunPage.new(self, self.runPageFrame)

	-- the status line: the main column's last row, over a rule; one line, cut short
	local sb, sbi = T.bar(main, { side = "top", pad = { 16, 6, 16, 6 }, name = "StatusBar" })
	self.statusBar = sb
	self.statusLabel = T.label(sbi, "", "mono", { color = C.muted, wrap = false, truncate = true })
	self.statusLabel.Size = UDim2.new(1, 0, 0, 16)
	sb.Visible = false

	self:_buildFooter()

	-- the first-run setup: over everything else while shown
	local setup = Instance.new("Frame")
	setup.Name = "Setup"
	setup.Size = UDim2.fromScale(1, 1)
	setup.BackgroundColor3 = C.ground
	setup.BorderSizePixel = 0
	setup.Visible = false
	setup.Parent = root
	self.setupFrame = setup

	-- popups (the ⋮ menu, the narrow history list, dropdowns): over everything, closed by a click outside
	local overlay = Instance.new("Frame")
	overlay.Name = "Overlay"
	overlay.Size = UDim2.fromScale(1, 1)
	overlay.BackgroundTransparency = 1
	overlay.ZIndex = 50
	overlay.Visible = false
	overlay.Parent = root
	local catcher = Instance.new("TextButton")
	catcher.Name = "Catcher"
	catcher.Text = ""
	catcher.AutoButtonColor = false
	catcher.BackgroundTransparency = 1
	catcher.Size = UDim2.fromScale(1, 1)
	catcher.ZIndex = 50
	catcher.Parent = overlay
	catcher.MouseButton1Click:Connect(function()
		self:closePopup()
	end)
	self.overlay = overlay
end

function Dock:_page(name: string): Frame
	local f = Instance.new("Frame")
	f.Name = name
	f.BackgroundTransparency = 1
	f.Size = UDim2.fromScale(1, 1)
	f.Visible = false
	f.Parent = self.pages
	return f
end

-- The fonts known (the library's, or Builder Sans in their place): every text drawn again in them.
function Dock:_rebuildFonts()
	local swap = {}
	for _, role in ipairs({ "body", "medium", "semibold" }) do
		swap[role] = (T.F :: any)[role]
	end
	for _, d in ipairs(self.root:GetDescendants()) do
		if d:IsA("TextLabel") or d:IsA("TextButton") or d:IsA("TextBox") then
			local f = (d :: any).FontFace :: Font
			if f.Family ~= "rbxasset://fonts/families/RobotoMono.json" then
				local w = f.Weight
				;(d :: any).FontFace = if w == Enum.FontWeight.SemiBold then swap.semibold
					elseif w == Enum.FontWeight.Medium then swap.medium else swap.body
			end
		end
	end
end

-- --- the connect page -----------------------------------------------------------------------------------------------

function Dock:_buildConnect()
	local scroll = T.scroll(self.connectPage, "Scroll")
	T.list(scroll, 0)
	local v = T.column(scroll, CONNECT_WIDTH, { 20, 24, 20, 24 }, 10)
	local card = T.plate(v, { pad = { 0, 0, 0, 0 }, gap = 0, name = "Card" })
	local cv = T.col(card, 10, "Main")
	T.pad(cv, 14, 12, 14, 12)
	-- what to do: a Roblox plugin can't start the agent service, so the user runs it (the dock connects once it's up)
	local topc = T.col(cv, 6)
	T.label(topc, "Start the agent service", "title", { wrap = false })
	T.label(topc, "Run this in a terminal and leave it open while you use Autopilot:", "small")
	T.selectable(topc, SERVE_COMMAND, { name = "Command", color = C.accent })
	-- (the first time: the command comes from the download's installer, which the dock can't run)
	T.label(topc, "No autopilot command yet? Install it with the installer in the download: see its README.txt.",
		"small", { color = C.muted })
	-- waiting for it: a quiet spinner line, no alert
	local wait = T.row(cv, 8, true, "Waiting")
	T.spinner(wait)
	T.label(wait, "Waiting for the agent service…", "small", { wrap = false, color = C.muted })
	self.cpWait = wait
	-- the state: an alert (its line, and a detail) in its tone, then its buttons
	local box = T.frame(cv, "State", C.infoFill)
	self.cpStroke = T.stroke(box, C.infoLine)
	T.pad(box, 11, 9, 11, 9)
	T.list(box, 10, true)
	self.cpSpin = T.spinner(box)
	local bv = T.col(box, 2)
	bv.Size = UDim2.new(0, 0, 0, 0)
	T.fill(bv)
	self.cpTitle = T.label(bv, "", "body")
	self.cpText = T.label(bv, "", "monoDim", { color = C.neutralFg })
	self.cpBox = box
	self.cpButtons = T.row(cv, 6, true, "Buttons")
	-- Advanced: under a rule, folded
	T.rule(card)
	local fold = T.col(card, 10, "Advanced")
	T.pad(fold, 14, 9, 14, 12)
	local foldBtn = Instance.new("TextButton")
	foldBtn.Name = "Fold"
	foldBtn.BackgroundTransparency = 1
	foldBtn.AutoButtonColor = false
	foldBtn.FontFace = T.F.mono
	foldBtn.TextSize = 11
	foldBtn.TextColor3 = C.muted
	foldBtn.TextXAlignment = Enum.TextXAlignment.Left
	foldBtn.Size = UDim2.new(1, 0, 0, 18)
	foldBtn.LayoutOrder = T.nextOrder()
	foldBtn.Parent = fold
	local fields = T.col(fold, 10, "Fields")
	fields.Visible = false
	local function showFold()
		foldBtn.Text = (if fields.Visible then "–  " else "+  ") .. "ADVANCED"
	end
	showFold()
	foldBtn.MouseButton1Click:Connect(function()
		fields.Visible = not fields.Visible
		showFold()
	end)
	foldBtn.MouseEnter:Connect(function()
		foldBtn.TextColor3 = C.text
	end)
	foldBtn.MouseLeave:Connect(function()
		foldBtn.TextColor3 = C.muted
	end)
	local function field(caption: string, key: string, placeholder: string): TextBox
		local f = T.col(fields, 5)
		T.label(f, caption, "caps", { wrap = false })
		local box = T.input(f, { mono = true, text = Settings.get(self.plugin, key) or "", placeholder = placeholder,
			name = key })
		box.TextEditable = not self.mirror
		box.FocusLost:Connect(function()
			self:_applyAddress()
		end)
		return box
	end
	self.urlBox = field("Agent service URL", "url", "ws://127.0.0.1:8765/v1/bridge")
	self.httpBox = field("HTTP fallback URL · optional", "http_url", "The next port after the WebSocket's")
	self.tokenBox = field("Access token · optional", "token", "")
	self.cpBack = T.button(v, "Back to the last run", "quiet", function()
		self:selectRun(self:_backRun())
	end, true)
end

-- The address, the HTTP fallback or the token changed under Advanced: kept, and connected again with them.
function Dock:_applyAddress()
	local url = M.trim(self.urlBox.Text)
	local http = M.trim(self.httpBox.Text)
	local token = M.trim(self.tokenBox.Text)
	if url == (Settings.get(self.plugin, "url") or "") and http == (Settings.get(self.plugin, "http_url") or "")
		and token == (Settings.get(self.plugin, "token") or "") then
		return
	end
	Settings.set(self.plugin, "url", url)
	Settings.set(self.plugin, "http_url", http)
	Settings.set(self.plugin, "token", token)
	if self.active == nil then
		self.editor:connect()
		self:render()
	end
end

function Dock:_connState(): string
	local ed = self.editor
	local l = ed.link
	if ed:connected() then
		return "connected"
	end
	if l.refused then
		return "refused"
	end
	if l.want then
		return if l.problem ~= "" then "unreachable" else "connecting"
	end
	return "idle"
end

-- The connect card's state now: {waiting: the quiet "Waiting for the agent service…" line, tone: "red" | "" (no
-- alert), text, detail, buttons: {{label, variant, action}}}. Not running yet isn't an error: the card says what to
-- run and waits (the link keeps trying); only an agent service that turned the dock away (a wrong token) is one.
function Dock:connectState(): any
	local l = self.editor.link
	local c = self.conn
	if c == "connecting" or c == "unreachable" then
		return { waiting = true, tone = "", text = "", detail = "", buttons = {} }
	elseif c == "refused" then
		return { waiting = false, tone = "red", text = M.scrub(l.problem), detail = "",
			buttons = { { "Try again", "primary", "connect" } } }
	end
	return { waiting = false, tone = "", text = "", detail = "", buttons = { { "Connect", "primary", "connect" } } }
end

function Dock:_renderConnect()
	local st = self:connectState()
	self.cpWait.Visible = st.waiting
	self.cpBack.Visible = self:_backRun() ~= nil
	local tone = st.tone
	self.cpBox.Visible = tone ~= ""
	if tone ~= "" then
		local line = ({ info = C.infoLine, amber = C.amberLine, red = C.redLine } :: any)[tone]
		local fill = ({ info = C.infoFill, amber = C.amberFill, red = C.redFill } :: any)[tone]
		local fg = ({ info = C.infoFg, amber = C.amberFg, red = C.redFg } :: any)[tone]
		self.cpBox.BackgroundColor3 = fill
		self.cpStroke.Color = line
		self.cpSpin.Visible = tone == "info"
		self.cpTitle.Text = st.text
		self.cpTitle.FontFace = if tone == "info" then T.F.body else T.F.medium
		self.cpTitle.TextColor3 = fg
		self.cpText.Text = st.detail
		self.cpText.Visible = st.detail ~= ""
	end
	local key = HttpService:JSONEncode(st.buttons)
	if key == self.cpKey then
		return -- (unchanged: the buttons stay, a click on them isn't lost to a rebuild)
	end
	self.cpKey = key
	T.clear(self.cpButtons)
	for _, spec in ipairs(st.buttons) do
		local action = spec[3]
		local cb = T.button(self.cpButtons, spec[1], spec[2], function()
			if action == "stop" then
				self.editor:disconnect()
			else
				self.editor:connect()
			end
			self:render()
		end)
		T.disable(cb, self.mirror)
	end
end

-- The run Back to the last run goes to.
function Dock:_backRun(): any
	local r = self.shown or self.lastRun
	return if r ~= nil and table.find(self.runs, r) then r else nil
end

-- --- the composer ---------------------------------------------------------------------------------------------------

function Dock:_buildComposer()
	local page = self.composerPage
	T.list(page, 0)
	local head, hi = T.bar(page, { pad = { 16, 12, 16, 12 }, horizontal = true, name = "Head" })
	T.label(hi, "New run", "title", { wrap = false, fill = true })
	self.cHead = head
	local bodyf = Instance.new("Frame")
	bodyf.Name = "Body"
	bodyf.BackgroundTransparency = 1
	bodyf.Size = UDim2.new(1, 0, 0, 0)
	bodyf.LayoutOrder = T.nextOrder()
	T.fill(bodyf)
	bodyf.Parent = page
	local bl = T.list(bodyf, 0)
	bl.HorizontalAlignment = Enum.HorizontalAlignment.Center
	self.cBody = bodyf
	local v = Instance.new("Frame")
	v.Name = "Column"
	v.BackgroundTransparency = 1
	v.Size = UDim2.new(1, 0, 1, 0)
	v.Parent = bodyf
	local cons = Instance.new("UISizeConstraint")
	cons.MaxSize = Vector2.new(COMPOSER_WIDTH, math.huge)
	cons.Parent = v
	T.list(v, 10)
	self.cColumn = v
	-- out of Aura credits: Aura's words over the box (Run stays: Aura checks again as a run starts)
	local credits = T.frame(v, "Credits", C.redFill)
	T.stroke(credits, C.redLine)
	T.pad(credits, 11, 9, 11, 9)
	T.list(credits, 6)
	self.cCreditsText = T.label(credits, "", "body", { color = C.redFg })
	self.cCreditsUrl = T.selectable(credits, "", { height = 24, size = 11 })
	self.cCredits = credits
	-- the prompt box: the words, then (one row, at the right) the Time limit and Run
	local well = T.plate(v, { bg = C.well, border = C.line, pad = { 12, 11, 12, 9 }, gap = 8, name = "Prompt" })
	self.cPrompt = T.input(well, { multiline = true, height = 100, bare = true, placeholder = PROMPT_PLACEHOLDER,
		name = "PromptBox" })
	self.cPrompt:GetPropertyChangedSignal("Text"):Connect(function()
		self:_renderComposer()
	end)
	local row = T.row(well, 8)
	local l = row:FindFirstChildOfClass("UIListLayout") :: UIListLayout
	l.HorizontalAlignment = Enum.HorizontalAlignment.Right
	T.label(row, "Time limit", "caps", { wrap = false })
	local minutes = T.input(row, { mono = true, width = 52, text = tostring(Settings.get(self.plugin, "time_limit") or 30),
		name = "Minutes" })
	minutes.TextXAlignment = Enum.TextXAlignment.Right
	minutes.FocusLost:Connect(function()
		local n = math.clamp(math.floor(tonumber(minutes.Text) or M.TIME_LIMIT_DEFAULT), 1, 600)
		minutes.Text = tostring(n)
		Settings.set(self.plugin, "time_limit", n)
	end)
	self.cMinutes = minutes
	self.cRun = T.button(row, "Run", "primary", function()
		self:_onRun()
	end, false, { width = 64, name = "Run" })
	self.cNote = T.label(v, "", "small", { color = C.muted })
	local spacer = Instance.new("Frame")
	spacer.Name = "Spacer"
	spacer.BackgroundTransparency = 1
	spacer.Size = UDim2.new(1, 0, 0, 0)
	spacer.LayoutOrder = T.nextOrder()
	T.fill(spacer)
	spacer.Parent = v
	-- the link: its state, its address and Disconnect, at the right over a rule
	local foot = T.col(v, 10, "Foot")
	T.rule(foot)
	local link = T.row(foot, 10)
	self.cLinkRow = link
	self.cLinkLabel = T.label(link, "● Connected", "caps", { wrap = false, color = C.success })
	self.cLinkUrl = T.label(link, "", "monoDim", { wrap = false, fill = true, align = "right" })
	local disconnect = T.button(link, "Disconnect", "quiet", function()
		self.editor:disconnect()
		self:render()
	end, true)
	disconnect.Visible = not self.mirror
	local pad = Instance.new("UIPadding")
	pad.Parent = v
	self.cPad = pad
end

-- Run: the words as typed and the Time limit; the agent service reads which kind of run they ask for and says so
-- (run_kind) before it plays.
function Dock:_onRun()
	local words = M.trim(self.cPrompt.Text)
	if words == "" then
		return
	end
	local n = math.clamp(math.floor(tonumber(self.cMinutes.Text) or M.TIME_LIMIT_DEFAULT), 1, 600)
	Settings.set(self.plugin, "time_limit", n)
	local run: any = { kind = "auto", prompt = words, time_limit_minutes = n }
	local brief = self:brief()
	if brief ~= "" then
		run.design_brief = brief -- (how the game is meant to work: the Settings' Design brief)
	end
	self:startRun(run, words)
end

function Dock:_renderComposer()
	local text = M.trim(self.cPrompt.Text)
	local ed = self.editor
	T.disable(self.cRun, text == "")
	if text ~= "" then
		T.grey(self.cRun, self.active ~= nil or not ed:connected())
	end
	self.cNote.Text = if self.active ~= nil then RUN_NOTE else ""
	self.cNote.Visible = self.cNote.Text ~= ""
	local credits = self:outOfCredits()
	self.cCredits.Visible = credits ~= nil
	if credits then
		self.cCreditsText.Text = credits.message
		self.cCreditsUrl.Text = credits.url
		self.cCreditsUrl.Visible = credits.url ~= "" and not string.find(credits.message, credits.url, 1, true)
	end
	self.cLinkUrl.Text = if self.conn == "connected" then tostring(ed.link.url) else ""
	-- (28 × 20 around the column; 16 × 12 in a narrow dock)
	local p = if self.wide then { 20, 28, 20, 20 } else { 12, 16, 12, 12 }
	T.pad(self.cColumn, p[1], p[2], p[3], p[4])
	local hp = if self.wide then { 16, 12 } else { 12, 10 }
	T.pad(self.cHead:FindFirstChild("Inner") :: Instance, hp[1], hp[2], hp[1], hp[2])
	local l = self.cLinkRow:FindFirstChildOfClass("UIListLayout") :: UIListLayout
	l.HorizontalAlignment = if self.wide then Enum.HorizontalAlignment.Right else Enum.HorizontalAlignment.Left
end

-- Aura's out-of-credits notice from the agent service's last account frame (nil while the team has credits).
function Dock:outOfCredits(): any
	local acc = self.editor.account or {}
	if acc.mode ~= "aura" then
		return nil
	end
	return M.outOfCredits(acc.out_of_credits)
end

-- The composer shown with these words in its box ("": what was typed there stays).
function Dock:compose(text: string)
	self.composing = true
	self.settingsOpen = false
	self:closePopup()
	if text ~= "" or self.cPrompt.Text == "" then
		self.cPrompt.Text = text
	end
	self:render()
end

-- The Settings' Design brief, trimmed ("" for none): sent with every run the dock starts.
function Dock:brief(): string
	return M.trim(Settings.get(self.plugin, "design_brief") or "")
end

-- --- runs -----------------------------------------------------------------------------------------------------------

local function kindOfRun(run: any): string
	local kind = tostring(run.kind or "")
	return if M.KINDS[kind] then kind else ""
end

-- Start a run (the composer's, Run again's, the setup's Explore): shown live at once.
function Dock:startRun(run: any, prompt: string): boolean
	local ed = self.editor
	if not ed:connected() then
		self:status("Not connected: connect first")
		self:render()
		return false
	end
	if self.active ~= nil then
		self:status(Dock.RUN_GOING)
		return false
	end
	run.turn_based = nil -- (no turn-based runs any more: a run again of an older request goes real time)
	local before = self.shown
	local r = self:_newRun(kindOfRun(run), prompt)
	r.request = run
	self.starting = true
	local ok = ed:startRun(run)
	self.starting = false
	if not ok then
		table.remove(self.runs, table.find(self.runs, r) or 1)
		self.active = nil
		self.shown = before
		self:render()
		return false
	end
	self.composing = false
	self.cPrompt.Text = ""
	self:render()
	return true
end

function Dock:_newRun(kind: string, prompt: string): any
	local r = M.newRun(kind, prompt)
	r.project = self:project()
	table.insert(self.runs, 1, r)
	self.active = r
	self.shown = r
	self.tab = "main"
	self.settingsOpen = false
	return r
end

function Dock:runAgain(r: any)
	local ed = self.editor
	if not ed:connected() or self.active ~= nil then
		self:status(if self.active ~= nil then Dock.RUN_GOING else "Not connected: connect first")
		return
	end
	if r.request == nil then
		self:compose(r.prompt)
		return
	end
	local again = table.clone(r.request)
	local brief = self:brief()
	if brief ~= "" and again.kind ~= "explore" then
		again.design_brief = brief
	end
	self:startRun(again, r.prompt)
end

-- The setup's Explore and finish: an Explore run of these minutes, the setup closed and the run shown live.
function Dock:exploreFromSetup(minutes: number)
	local run: any = { kind = "explore", minutes = math.max(1, minutes) }
	local brief = self:brief()
	if brief ~= "" then
		run.brief = brief
	end
	if self:startRun(run, "") then
		self.setupOpen = false
	end
	self:render()
end

function Dock:stopRun()
	if self.active then
		self.active.stopAsked = true
	end
	self.editor:stopRun()
	self:render()
end

function Dock:selectRun(r: any)
	if r == nil and self.shown ~= nil then
		self.lastRun = self.shown
	end
	self.shown = r
	self.composing = false
	self.settingsOpen = false
	self.tab = "main"
	self:closePopup()
	self:render()
end

function Dock:setTab(key: string)
	self.tab = key
	self.runPage:render()
end

-- A run that ended only because the connection closed, open again when the agent service, connected again, still
-- sends its events (nil otherwise). going: false when what came is its result.
function Dock:_reopenLost(going: boolean?): any
	if self.active ~= nil then
		return nil
	end
	local r = self.runs[1]
	if r == nil or r:live() or not r.lostConnection then
		return nil
	end
	r.lostConnection = false
	r.ended = nil
	r.seconds = nil
	r:addFeed("Connected again: the run goes on", "info")
	self.active = r
	if going ~= false then
		self.editor.runActive = true -- (stop and steer reach it again)
	end
	self:render()
	return r
end

-- --- events from the editor -----------------------------------------------------------------------------------------

function Dock:_listen()
	local ed = self.editor
	-- (a dock stopped hears nothing more: the editor outlives a preview of it)
	local function on(name: string, fn: (...any) -> ())
		ed:on(name, function(...)
			if not self.dead then
				fn(...)
			end
		end)
	end
	on("status", function(text)
		if text == self.serverLine then
			return -- (the agent service's progress lines belong to the Live page and the feed)
		end
		for _, q in ipairs(QUIET) do
			if string.sub(tostring(text), 1, #q) == q then
				return
			end
		end
		if text ~= "" then
			self:status(M.scrub(text))
		end
	end)
	on("server_status", function(text)
		self.serverLine = text
		if string.sub(text, 1, 22) == "Run finished. Report: " then
			return
		end
		local r = self.active
		if r and string.sub(text, 1, 9) ~= "Finding: " then
			r:onStatus(M.translate(text))
			self:liveChanged()
		end
	end)
	on("connection", function()
		self:render()
	end)
	on("run", function(running)
		if running and self.active == nil and not self.starting then
			self:_newRun("", "")
			self:render()
		elseif not running then
			task.defer(function()
				self:_endCheck()
			end)
		end
		self:_renderComposer()
	end)
	on("run_started", function(run)
		if self.active ~= nil and self.active.prompt ~= "" then
			return
		end
		if self.active ~= nil then
			-- (the run frame came after the run began: it is this one)
			table.remove(self.runs, table.find(self.runs, self.active) or 1)
			self.active = nil
		end
		local r = self:_newRun(kindOfRun(run), self:_promptOf(run))
		local request = table.clone(run)
		request.by = nil
		r.request = request
		r.origin = M.trim(run.by)
		if r.origin == "" then
			r.origin = "agent"
		end
		self.composing = false
		self:render()
	end)
	on("run_kind", function(kind, tested)
		local r = self.active
		if r == nil or not M.KINDS[kind] then
			return
		end
		r.kind = kind
		r.tested = tested
		self:render()
	end)
	on("activity", function(f)
		local r = self.active
		if r == nil then
			-- (the run whose connection closed, going on: its feed comes on from where it was)
			local last = self.runs[1]
			if last and last.lostConnection and (tonumber(f.id) or 0) > (last.lastActivityId or 0) then
				r = self:_reopenLost()
			end
		end
		if r == nil then
			return
		end
		local before = #r.feed
		if not r:onActivity(f) then
			return
		end
		self.feedUpdated = if #r.feed == before then tonumber(f.ends) else nil
		self:liveChanged()
	end)
	on("finding", function(f)
		local r = self.active
		if r == nil then
			return
		end
		local fresh = r:onFinding(f)
		local count = tonumber(f.count) or 1
		local engine = M.isEngine(f)
		r:addFeed(M.scrub(tostring(f.summary or f.detector or "")) .. (if count > 1 then string.format(" (×%d)", count)
			else ""), if engine then "engine" else "agent")
		r.feed[#r.feed].tone = if engine then T.severityKind(tostring(f.severity or "")) else "bad"
		if fresh or count > 1 then
			self:liveChanged()
		end
	end)
	on("result", function(result)
		self:_onResult(result)
	end)
	on("memory", function()
		self:_renderFooter()
		if self.setupOpen then
			SetupPage.render(self, self.setupFrame)
		end
	end)
	on("memory_cleared", function(ok, message)
		self.reset = if ok then "done" else "failed"
		self.resetWhy = message
		self.resetUntil = os.clock() + STATUS_FOR
		if ok then
			self:_setRunsAside()
		end
		self:_renderFooter()
	end)
	on("account", function(acc)
		if acc.mode == "none" and not self.setupOpen and Settings.get(self.plugin, "onboarded") ~= true
			and not self.setupChecked and not self.mirror then
			-- (not set up on this computer, and never through this dock's setup: the first-run setup)
			self.setupChecked = true
			self:openSetup()
			return
		end
		self.setupChecked = true
		self:render()
	end)
	on("sign_in", function(f)
		self:_onSignIn(f)
	end)
	on("setup_saved", function(f)
		if not f.ok then
			self:status("Not saved: " .. M.scrub(tostring(f.message or "")))
		end
		ed:askAccount()
	end)
	on("bill", function(f)
		if type(f.usd) ~= "number" then
			return
		end
		local b = { usd = math.max(0, f.usd), billed = f.billed == true }
		local r = self.active
		if r == nil then
			local last = self.runs[1]
			if last and not last:live() and last.result and last.bill == nil then
				last.bill = b
				self:save()
			else
				self.pendingBill = b
			end
			return
		end
		r.bill = b
	end)
	on("playing", function()
		self:liveChanged()
	end)
	on("policy", function(f)
		self:_onPolicyStep(f)
	end)
	on("usage", function(models, final)
		local r = self.active
		if r == nil then
			-- (the final count can come after the result: the run just ended)
			local last = self.runs[1]
			if last and not last:live() and last.result then
				last.usage = M.usageRows(models)
				if final then
					self:save()
				end
			else
				self.pendingUsage = models
			end
			return
		end
		r.usage = M.usageRows(models)
	end)
end

function Dock:_promptOf(run: any): string
	local kind = tostring(run.kind or "")
	if kind == "verify" then
		return M.trim(run.claim)
	elseif kind == "goal" then
		return M.trim(run.goal)
	elseif kind == "explore" then
		local brief = M.trim(run.brief)
		return if brief ~= "" then "Explore " .. brief else "Explore"
	elseif kind == "auto" then
		return M.trim(run.prompt)
	end
	return M.trim(run.claim or run.goal or run.brief or run.prompt)
end

function Dock:_endCheck()
	local r = self.active
	if r ~= nil and not self.editor.runActive then
		r.ended = os.clock()
		r.lostConnection = r.result == nil
		if r.lostConnection then
			r:addFeed("Connection closed: no result", "info")
		end
		self.active = nil
		self:save()
		self:render()
	end
end

function Dock:_onResult(result: any)
	local r = self.active
	if r == nil then
		r = self:_reopenLost(false)
	end
	if r == nil then
		r = self:_newRun("", tostring(result.claim or result.goal or ""))
		if self.pendingUsage then
			r.usage = M.usageRows(self.pendingUsage)
		end
	end
	if self.pendingBill and r.bill == nil then
		r.bill = self.pendingBill
	end
	self.pendingUsage, self.pendingBill = nil, nil
	r.result = result
	if r.prompt == "" then
		r.prompt = tostring(result.claim or result.goal or "")
	end
	if r.kind == "" then
		r.kind = if result.verdict ~= nil then "verify" elseif result.kind == "goal" then "goal" else ""
	end
	if result.tested == true then
		r.tested = true
	end
	r:enterStage(M.STAGE_REPORT)
	r.ended = os.clock()
	r:addFeed("Result: " .. M.sentence(r:verdict()), "info")
	if r:memoryUpdated() then
		r:addFeed(M.MEMORY_UPDATED, "memory")
	end
	self.active = nil
	if self.shown == r then
		self.tab = "main"
	end
	self:save()
	self:render()
end

function Dock:_onPolicyStep(f: any)
	local r = self.active
	if r == nil or type(f) ~= "table" then
		return
	end
	local st = r:addStep(f, T.ACTION_COLORS, POLICY_KEPT)
	if self.shown == r then
		self.runPage:onStep(st)
	end
end

-- The run going on changed (a feed line, a stage, the game coming up): its page drawn again.
function Dock:liveChanged()
	if self.shown ~= nil and self.shown == self.active then
		self.runPage:liveChanged()
	end
end

-- Reset memory done: the runs ended leave the history (a run going stays), kept aside in the plugin's settings.
function Dock:_setRunsAside()
	local ended, kept = {}, {}
	for _, r in ipairs(self.runs) do
		if r:live() then
			table.insert(kept, r)
		else
			table.insert(ended, r:saved())
		end
	end
	if #ended == 0 then
		return
	end
	if not self.preview then
		Settings.set(self.plugin, "history_aside", ended)
	end
	self.runs = kept
	if self.shown ~= nil and not table.find(self.runs, self.shown) then
		self.shown = nil
	end
	if self.lastRun ~= nil and not table.find(self.runs, self.lastRun) then
		self.lastRun = nil
	end
	self:save()
	self:render()
end

-- --- the Aura sign-in (asked of the agent service) ------------------------------------------------------------------

function Dock:beginSignIn()
	local ed = self.editor
	if not ed:connected() then
		self.signIn = { state = "no_service" }
	elseif not ed.brainAccount then
		self.signIn = { state = "error", message = "This agent service can’t sign in to Aura: update it, then try again." }
	elseif ed:signIn() then
		self.signIn = { state = "preparing" }
	else
		self.signIn = { state = "error", message = "The sign-in didn’t start. Try again." }
	end
	self:renderPages()
end

function Dock:cancelSignIn()
	local s = self.signIn
	if s and (s.state == "waiting" or s.state == "preparing") then
		self.editor:signInCancel()
	end
	self.signIn = nil
	self:renderPages()
end

-- Use another account: sign out, then sign in again.
function Dock:anotherAccount()
	self.editor:signOut()
	self.signIn = { state = "preparing" }
	task.delay(0.5, function()
		if self.signIn and self.signIn.state == "preparing" then
			self.editor:signIn()
		end
	end)
	self:renderPages()
end

local SIGN_IN_WORDS = { denied = "Sign-in was cancelled in the browser.",
	timeout = "Nothing came back from the browser in 5 minutes.",
	unreachable = "Couldn’t reach Aura. Check your connection and try again." }

function Dock:_onSignIn(f: any)
	if self.signIn == nil then
		return -- (not asked here, or cancelled)
	end
	local state = tostring(f.state or "")
	if state == "waiting" then
		self.signIn = { state = "waiting", url = tostring(f.url or "") }
	elseif state == "signed_in" then
		-- (in the setup: Continue; on the Settings page the account frame that follows shows it)
		self.signIn = if self.setupOpen then { state = "signed_in", email = tostring(f.email or "") } else nil
		self.editor:askAccount()
	elseif state == "error" then
		local reason = tostring(f.reason or "")
		if reason == "cancelled" then
			self.signIn = nil
		else
			local message = SIGN_IN_WORDS[reason] or M.scrub(tostring(f.message or ""))
			self.signIn = { state = "error", message = if message ~= "" then message
				else "The sign-in didn’t finish. Try again." }
		end
	end
	self:renderPages()
end

-- --- the first-run setup --------------------------------------------------------------------------------------------

function Dock:openSetup()
	if self.mirror then
		self:status(Dock.DURING_PLAY)
		return
	end
	if self.active ~= nil then
		self:status(Dock.RUN_GOING)
		return
	end
	self.setupOpen = true
	self.settingsOpen = false
	self.signIn = nil
	self:closePopup()
	SetupPage.open(self)
	self.editor:askAccount()
	self:render()
end

function Dock:closeSetup()
	self.setupOpen = false
	self:render()
end

-- What the agent service said it remembers about this place on the connection open now: "unknown", "empty" or "some".
function Dock:memoryNow(): string
	local ed = self.editor
	local mem = ed.memory
	if not ed:connected() or not mem.known then
		return "unknown"
	end
	if mem.runs == 0 and not mem.notes and not mem.lib then
		return "empty"
	end
	return "some"
end

-- --- the footer: the Memory row -------------------------------------------------------------------------------------

function Dock:_buildFooter()
	local footer = T.frame(self.railFoot, "Footer", C.rail)
	T.list(footer, 0)
	T.rule(footer)
	local inner = T.col(footer, 0, "Inner")
	self.footerInner = inner
	local bar = T.col(inner, 6, "Bar")
	local save = T.row(bar, 8)
	T.label(save, "Memory", "caps", { wrap = false })
	self.saveLabel = T.label(save, "", "mono", { wrap = false, fill = true })
	self.resetBtn = T.button(save, "Reset memory…", "danger", function()
		self:askReset()
	end, true)
	local line = T.row(bar, 8)
	self.resetSpin = T.spinner(line)
	self.resetText = T.label(line, "", "small", { wrap = false, fill = true })
	self.resetLine = line
	self.memBar = bar
	-- the confirmation, in place of the row: a plate with a red border
	local ask = T.plate(inner, { border = C.redLine, pad = { 12, 11, 12, 11 }, gap = 6, name = "Ask" })
	T.label(ask, "Reset memory?", "strong")
	T.label(ask, RESET_TEXT, "small")
	local ab = T.row(ask, 6)
	T.button(ab, "Reset memory", "dangerFill", function()
		self:_confirmReset()
	end, true)
	T.button(ab, "Cancel", "quiet", function()
		self.reset = ""
		self:_renderFooter()
	end, true)
	self.memAsk = ask
	self.footer = footer
end

-- Reset memory…: asks in place, when connected and no run is going.
function Dock:askReset()
	self.settingsOpen = false
	if self.mirror then
		self:status(Dock.DURING_PLAY)
		return
	end
	if not self.editor:connected() then
		self:status("Not connected: connect first to reset the memory")
		return
	end
	if self.active ~= nil then
		self:status("A run is going: reset the memory once it is done")
		return
	end
	self.reset = "ask"
	if not self.wide then
		self:openHistory()
	end
	self:render()
end

function Dock:_confirmReset()
	if not self.editor:connected() then
		self.reset = ""
		self:status("Not connected: connect first to reset the memory")
		self:_renderFooter()
		return
	end
	self.editor:clearMemory()
	self.reset = "working"
	self:_renderFooter()
end

function Dock:_saveText(): string
	local mem = self.editor.memory
	if not mem.known then
		return ""
	end
	if mem.runs == 0 and not mem.notes and not mem.lib then
		return "empty"
	end
	return string.format("%d run%s", mem.runs, if mem.runs == 1 then "" else "s")
end

function Dock:_renderFooter()
	local saved = self:_saveText()
	self.saveLabel.Text = if saved ~= "" then "· " .. saved else ""
	if self.reset == "ask" and (self.active ~= nil or not self.editor:connected()) then
		self.reset = ""
	end
	local asking = self.reset == "ask"
	self.memAsk.Visible = asking
	self.memBar.Visible = not asking
	local p = if asking then { 10, 10 } elseif self.wide then { 14, 9 } else { 12, 8 }
	T.pad(self.footerInner, p[1], p[2], p[1], p[2])
	T.disable(self.resetBtn, self.reset == "working")
	if self.reset ~= "working" then
		T.grey(self.resetBtn, self.active ~= nil or not self.editor:connected() or self.mirror)
	end
	local r = self.reset
	self.resetLine.Visible = r == "working" or r == "done" or r == "failed"
	self.resetSpin.Visible = r == "working"
	if r == "working" then
		self.resetText.Text = "Resetting memory…"
		self.resetText.TextColor3 = C.text3
	elseif r == "done" then
		self.resetText.Text = "Memory reset successful"
		self.resetText.TextColor3 = C.success
	elseif r == "failed" then
		self.resetText.Text = "Memory not reset: " .. M.scrub(self.resetWhy)
		self.resetText.TextColor3 = C.red
	end
end

-- --- the run history ------------------------------------------------------------------------------------------------

function Dock:verdictTag(parent: Instance, r: any): Frame
	local v = r:verdict()
	return T.tag(parent, if v == "LIVE" then "Live" else v, T.verdictColors(v)[3])
end

-- A run's line in the history: "Oct 19 13:25 · 03:26 · 2 attempts".
function Dock:rowMeta(r: any): string
	return string.format("%s · %s · %s", M.when(r.started), M.clock(if r:live() then r:elapsed() else r:took()),
		M.attemptsText(r:attemptCount()))
end

-- A run in the history: its verdict tag and line, then its prompt. Selected: ROW_ACTIVE with a 2 px accent rule at its
-- left; hovered, HOVER; else the rail's ground and GROUND in turn.
function Dock:_historyRow(parent: Instance, r: any, odd: boolean, popup: boolean): GuiObject
	local sel = r == self.shown and not self.composing
	local rest = if popup then C.popover elseif odd then C.ground else C.rail
	local b = Instance.new("TextButton")
	b.Name = "Run"
	b.Text = ""
	b.AutoButtonColor = false
	b.BorderSizePixel = 0
	b.BackgroundColor3 = if sel then C.rowActive else rest
	b.Size = UDim2.new(1, 0, 0, 0)
	b.AutomaticSize = Enum.AutomaticSize.Y
	b.LayoutOrder = T.nextOrder()
	if sel then
		T.leftEdge(b, C.accent)
	end
	local v = T.col(b, 3)
	T.pad(v, if popup then 10 else 12, 9, if popup then 12 else 14, 9)
	local h = T.row(v, 8)
	self:verdictTag(h, r)
	local meta = T.label(h, self:rowMeta(r), "monoDim", { wrap = false, fill = true })
	if r:live() and not popup then
		self.liveMeta = meta
	end
	local words = r:shownPrompt()
	if #words > 140 then
		words = M.trim(string.sub(words, 1, 139)) .. "…"
	end
	T.label(v, words, "body", { size = 13, color = if sel then C.text else C.text2 })
	b.MouseEnter:Connect(function()
		if not sel then
			b.BackgroundColor3 = C.hover
		end
	end)
	b.MouseLeave:Connect(function()
		if not sel then
			b.BackgroundColor3 = rest
		end
	end)
	b.MouseButton1Click:Connect(function()
		self:selectRun(r)
	end)
	b.Parent = parent
	return b
end

function Dock:_renderHistory()
	self.liveMeta = nil
	T.clear(self.railList)
	for i, r in ipairs(self.runs) do
		self:_historyRow(self.railList, r, i % 2 == 0, false)
	end
	if #self.runs == 0 then
		local e = T.col(self.railList, 0)
		T.pad(e, 14, 10, 14, 10)
		T.label(e, "No runs yet.", "small", { color = C.muted })
	end
	-- the history button (narrow)
	for _, c in ipairs(self.histRow:GetChildren()) do
		if c:IsA("GuiObject") then
			c:Destroy()
		end
	end
	local r = self.shown or self.runs[1]
	if r == nil or self.composing then
		T.tag(self.histRow, "New", "mut")
		self.histMeta = T.label(self.histRow, if #self.runs > 0 then string.format("%d run%s", #self.runs,
			if #self.runs == 1 then "" else "s") else "No runs yet", "monoDim", { wrap = false, fill = true,
			color = C.text3 })
	else
		self:verdictTag(self.histRow, r)
		self.histMeta = T.label(self.histRow, self:rowMeta(r), "monoDim", { wrap = false, fill = true, color = C.text3 })
	end
	T.chevron(self.histRow, C.text3)
	self.histRow.Size = UDim2.fromScale(1, 1)
end

-- --- popups ---------------------------------------------------------------------------------------------------------

function Dock:closePopup()
	-- (the Memory row back where it lives, the rail's foot, before the popup that may hold it goes)
	if self.footer.Parent ~= self.railFoot then
		self.footer.Parent = self.railFoot
		for _, d in ipairs(self.footer:GetDescendants()) do
			if d:IsA("GuiObject") and d.ZIndex >= 61 then
				d.ZIndex = 1
			end
		end
		if self.footer.ZIndex >= 61 then
			self.footer.ZIndex = 1
		end
	end
	if self.popup then
		self.popup:Destroy()
		self.popup = nil
	end
	self.overlay.Visible = false
end

-- A floating panel at (x, y) in the dock, width wide: POPOVER, radius 8, the control outline.
function Dock:_popup(x: number, y: number, width: number): Frame
	self:closePopup()
	local p = Instance.new("Frame")
	p.Name = "Popup"
	p.BackgroundColor3 = C.popover
	p.BorderSizePixel = 0
	p.Position = UDim2.fromOffset(x / self.scale, y / self.scale)
	p.Size = UDim2.new(0, width / self.scale, 0, 0)
	p.AutomaticSize = Enum.AutomaticSize.Y
	p.ZIndex = 60
	T.corner(p, T.R_PLATE)
	T.stroke(p, C.line)
	T.list(p, 0)
	p.Parent = self.overlay
	self.popup = p
	self.overlay.Visible = true
	return p
end

-- The menu's items now: {{label, enabled, checked, action}}.
function Dock:menuItems(): { any }
	local ed = self.editor
	local connected = ed:connected()
	local wants = ed.link.want or connected
	local idle = self.active == nil
	return {
		{ if wants then "Disconnect" else "Connect", not self.setupOpen and not self.mirror, false, function()
			if wants then
				ed:disconnect()
			else
				ed:connect()
			end
			self:render()
		end },
		{ "Settings", not self.setupOpen, self.settingsOpen, function()
			self:toggleSettings()
		end },
		{ "Rerun setup", idle and not self.mirror, false, function()
			self:openSetup()
		end },
		{ "Reset memory…", connected and idle and not self.setupOpen and not self.mirror, false, function()
			self:askReset()
		end },
	}
end

-- The ⋮ menu, under the button's row at the right.
function Dock:openMenu(at: GuiObject)
	local rootPos = self.root.AbsolutePosition
	local width = 190
	local x = at.AbsolutePosition.X + at.AbsoluteSize.X - width - rootPos.X
	local y = at.AbsolutePosition.Y + at.AbsoluteSize.Y - rootPos.Y + 2
	local p = self:_popup(math.max(4, x), y, width)
	T.pad(p, 4, 4, 4, 4)
	for _, item in ipairs(self:menuItems()) do
		local b = Instance.new("TextButton")
		b.Name = item[1]
		b.AutoButtonColor = false
		b.BorderSizePixel = 0
		b.BackgroundTransparency = 1
		b.BackgroundColor3 = C.hover
		b.Text = T.text((if item[3] then "✓  " else "     ") .. item[1], T.F.body)
		b.FontFace = T.F.body
		b.TextSize = 14
		b.TextColor3 = if item[2] then C.text2 else C.muted
		b.TextTransparency = if item[2] then 0 else 0.35
		b.TextXAlignment = Enum.TextXAlignment.Left
		b.Size = UDim2.new(1, 0, 0, 30)
		b.ZIndex = 61
		b.LayoutOrder = T.nextOrder()
		T.pad(b, 8, 0, 8, 0)
		b.Parent = p
		if item[2] then
			b.MouseEnter:Connect(function()
				b.BackgroundTransparency = 0
				b.TextColor3 = C.text
			end)
			b.MouseLeave:Connect(function()
				b.BackgroundTransparency = 1
				b.TextColor3 = C.text2
			end)
			b.MouseButton1Click:Connect(function()
				self:closePopup()
				local ok, err = pcall(item[4])
				if not ok then
					warn("[Autopilot] " .. tostring(err))
				end
			end)
		end
	end
	for _, d in ipairs(p:GetDescendants()) do
		if d:IsA("GuiObject") and d.ZIndex < 61 then
			d.ZIndex = 61
		end
	end
end

-- The narrow dock's history list (the History button): the runs, newest first, then the Memory row; under the button,
-- as wide as the top bar's inside.
function Dock:openHistory()
	local rootPos = self.root.AbsolutePosition
	local top = self.top
	local x = top.AbsolutePosition.X - rootPos.X + 10
	local y = self.histBtn.AbsolutePosition.Y + self.histBtn.AbsoluteSize.Y - rootPos.Y + 8
	local width = top.AbsoluteSize.X - 20
	local p = self:_popup(x, y, width)
	local sc = T.scroll(p, "List")
	sc.Size = UDim2.new(1, 0, 0, 0)
	sc.AutomaticSize = Enum.AutomaticSize.Y
	T.list(sc, 0)
	local cons = Instance.new("UISizeConstraint")
	cons.MaxSize = Vector2.new(math.huge, math.max(120, (self.root.AbsoluteSize.Y - y) / self.scale - 140))
	cons.Parent = sc
	for _, r in ipairs(self.runs) do
		self:_historyRow(sc, r, false, true)
	end
	if #self.runs == 0 then
		local e = T.col(sc, 0)
		T.pad(e, 12, 10, 12, 10)
		T.label(e, "No runs yet.", "small", { color = C.muted })
	end
	self.footer.Parent = p
	self.footer.LayoutOrder = T.nextOrder()
	for _, d in ipairs(p:GetDescendants()) do
		if d:IsA("GuiObject") and d.ZIndex < 61 then
			d.ZIndex = 61
		end
	end
end

-- --- the Settings page ----------------------------------------------------------------------------------------------

function Dock:toggleSettings()
	self.settingsOpen = not self.settingsOpen
	self:closePopup()
	if self.settingsOpen then
		self.editor:askAccount()
	end
	self:render()
end

-- --- the status line ------------------------------------------------------------------------------------------------

-- A message on the status line (what the user's own action did, or why it couldn't): shown until the next one, or
-- STATUS_FOR seconds.
function Dock:status(text: string)
	if self.statusLabel == nil then
		return
	end
	self.statusLabel.Text = text or ""
	self.statusBar.Visible = (text or "") ~= ""
	self.statusUntil = os.clock() + STATUS_FOR
end

-- --- layout and rendering -------------------------------------------------------------------------------------------

function Dock:_resized()
	local w = self.root.AbsoluteSize.X / self.scale >= WIDE_AT
	if w ~= self.wide then
		self.wide = w
		self:closePopup()
		self:render()
	elseif self.page == "run" then
		self.runPage:resized()
	end
end

function Dock:mainWidth(): number
	return self.root.AbsoluteSize.X / self.scale - (if self.wide then RAIL_WIDTH + 1 else 0)
end

function Dock:renderPages()
	if self.setupOpen then
		SetupPage.render(self, self.setupFrame)
	end
	if self.page == "settings" then
		SettingsPage.render(self, self.settingsPage)
	end
end

function Dock:render()
	if self.root == nil then
		return
	end
	self.conn = self:_connState()
	self.rail.Visible = self.wide
	self.railEdge.Visible = self.wide
	self.top.Visible = not self.wide
	self:_renderHistory()
	self:_renderFooter()
	self.body.Visible = not self.setupOpen
	self.setupFrame.Visible = self.setupOpen
	if self.setupOpen then
		SetupPage.render(self, self.setupFrame)
	end
	local online = self.conn == "connected"
	local page = "run"
	if self.shown == nil or self.composing then
		page = if online then "composer" else "connect"
	end
	if self.settingsOpen then
		page = "settings"
	end
	self.page = page
	self.connectPage.Visible = page == "connect"
	self.composerPage.Visible = page == "composer"
	self.runPageFrame.Visible = page == "run"
	self.settingsPage.Visible = page == "settings"
	self.strip.Visible = page == "run" and not online and self.shown ~= nil and not self.shown:live()
	local trying = self.conn == "connecting" or self.conn == "unreachable"
	self.stripText.Text = if trying then "To start a run, run " .. SERVE_COMMAND .. " in a terminal"
		else "Not connected to the agent service"
	self.stripBtn.Visible = not trying and not self.mirror
	if page == "connect" then
		self:_renderConnect()
	elseif page == "composer" then
		self:_renderComposer()
	elseif page == "run" then
		self.runPage:render()
	elseif page == "settings" then
		SettingsPage.render(self, self.settingsPage)
	end
	for _, g in ipairs({ self.railGear, self.topGear }) do
		T.setVariant(g, if self.settingsOpen then "on" else "quiet")
	end
end

function Dock:_tick()
	if self.root == nil or self.root.Parent == nil then
		return
	end
	if self.statusBar.Visible and os.clock() >= self.statusUntil then
		self.statusLabel.Text = ""
		self.statusBar.Visible = false
	end
	if (self.reset == "done" or self.reset == "failed") and os.clock() >= self.resetUntil then
		self.reset = ""
		self:_renderFooter()
	end
	local c = self:_connState()
	if c ~= self.conn then
		self:render()
	end
	local r = self.active
	if r ~= nil then
		if self.shown == r and self.page == "run" then
			self.runPage:tick()
		end
		self.slow = (self.slow or 0) + 1
		if self.slow % 4 == 0 then
			if self.liveMeta and self.liveMeta.Parent then
				self.liveMeta.Text = self:rowMeta(r)
			end
			if self.shown == r and not self.composing and self.histMeta and self.histMeta.Parent then
				self.histMeta.Text = self:rowMeta(r)
			end
		end
	end
end

-- The live run's "now" line waits for the game: a run's play session starting (Studio asked to play, its client not
-- attached yet).
function Dock:waiting(): boolean
	local ed = self.editor
	return ed.runActive and ed.starting and not ed.playing
end

-- --- the playtest: the edit dock hidden while the client's draws it (Mirror.luau) ---------------------------------------

-- Hide the dock while a play session's client draws its mirror (Studio doesn't draw the edit DataModel's widget during
-- play), or show it again as it was. Kept in the plugin's settings (dock_hidden) so that a dock hidden when Studio went
-- away is shown again at the next start.
function Dock:setPlayHidden(on: boolean)
	local w = self.widget
	if w == nil or on == (self.playHidden == true) then
		return
	end
	if on then
		self.playHidden = true
		self.wasEnabled = w.Enabled
		Settings.set(self.plugin, "dock_hidden", { was = w.Enabled })
		w.Enabled = false
	else
		self.playHidden = false
		local saved = Settings.get(self.plugin, "dock_hidden")
		local was = if self.wasEnabled ~= nil then self.wasEnabled
			elseif type(saved) == "table" and saved.was == false then false else true
		w.Enabled = was
		self.wasEnabled = nil
		Settings.set(self.plugin, "dock_hidden", nil)
	end
end

-- The dock's state for its mirror: the editor's (the connection, the account, the memory, the run going), the run
-- going on (as kept, and its latest policy steps when steps), and which run is shown.
function Dock:snapshot(steps: number?): any
	local ed = self.editor
	local s: any = { ed = Dock.editorState(ed), account = ed.account, shown = if self.shown then self.shown.id else nil,
		composing = self.composing, tab = self.tab }
	local r = self.active
	if r ~= nil then
		local a = r:saved()
		a.live = r:live()
		a.nowText = r.nowText
		a.lastActivityId = r.lastActivityId
		a.stageIn = r:elapsed() - (r.stageSince or 0)
		if steps and steps > 0 then
			a.steps = {}
			for i = math.max(1, #r.steps - steps + 1), #r.steps do
				table.insert(a.steps, r.steps[i].frame)
			end
		end
		s.active = a
	end
	return s
end

-- What the mirror's editor needs of the editor's state (sent with each batch of events).
function Dock.editorState(ed: any): any
	local l = ed.link
	return { connected = ed:connected(), url = l.url, problem = l.problem, want = l.want, refused = l.refused == true,
		runActive = ed.runActive, starting = ed.starting, playing = ed.playing, brainAccount = ed.brainAccount,
		brainSetup = ed.brainSetup, brainSteers = ed.brainSteers, memory = ed.memory }
end

-- The mirror's dock as the edit dock is (its snapshot): the run going on, the run shown.
function Dock:applySnapshot(s: any)
	if type(s) ~= "table" then
		return
	end
	local a = s.active
	if type(a) == "table" then
		local r = M.fromSaved(a)
		if a.live then
			r.seconds = nil
			r.ended = nil
			r.clock0 = os.clock() - (tonumber(a.seconds) or 0)
			r.stageSince = r:elapsed() - (tonumber(a.stageIn) or 0)
		end
		r.nowText = tostring(a.nowText or "")
		r.lastActivityId = tonumber(a.lastActivityId) or 0
		for _, f in ipairs(if type(a.steps) == "table" then a.steps else {}) do
			r:addStep(f, T.ACTION_COLORS, POLICY_KEPT)
		end
		for i, old in ipairs(self.runs) do
			if old.id == r.id then
				table.remove(self.runs, i)
				break
			end
		end
		table.insert(self.runs, 1, r)
		self.active = if r:live() then r else nil
	end
	self.shown = nil
	for _, r in ipairs(self.runs) do
		if r.id == s.shown then
			self.shown = r
		end
	end
	self.composing = s.composing == true
	self.tab = if type(s.tab) == "string" then s.tab else "main"
	self:render()
end

function Dock:stop()
	self.dead = true
	for _, c in ipairs(self.conns) do
		c:Disconnect()
	end
	pcall(function()
		self.root:Destroy()
	end)
end

-- (Json kept for the pages that send JSON objects through the editor)
Dock.Json = Json

return Dock
