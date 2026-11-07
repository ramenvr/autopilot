--!strict
-- The Autopilot dock: a Studio widget (Plugins tab > Autopilot) to connect to the agent service, set up how runs go,
-- start a run from one prompt box, follow it (its stages, the activity feed, the Policy log, Stop, Steer) and read its
-- result (with its usage); the run history and the memory line. It reads nothing from the prompt's words: the agent
-- service says what kind of run they ask for (its run_kind frame).
local HttpService = game:GetService("HttpService")
local Selection = game:GetService("Selection")
local TextService = game:GetService("TextService")

local Json = require(script.Parent.Json)
local Settings = require(script.Parent.Settings)

local Dock = {}
Dock.__index = Dock

-- Aura's look: near-black grounds, 1 px rules, one amber accent
local C = {
	bg = Color3.fromHex("121212"), panel = Color3.fromHex("1a1a1a"), field = Color3.fromHex("222222"),
	rule = Color3.fromHex("2c2c2c"), text = Color3.fromHex("e8e8e8"), muted = Color3.fromHex("9a9a9a"),
	accent = Color3.fromHex("f0873a"), good = Color3.fromHex("4cc38a"), bad = Color3.fromHex("e5534b"),
	warn = Color3.fromHex("e2b340"), info = Color3.fromHex("5aa9e6"),
}
local FONT = Font.new("rbxasset://fonts/families/SourceSansPro.json")
local BOLD = Font.new("rbxasset://fonts/families/SourceSansPro.json", Enum.FontWeight.SemiBold)
local MONO = Font.new("rbxasset://fonts/families/RobotoMono.json")

local STAGES = { "launch", "analyze", "probe", "build", "play", "results", "report" }
local STAGE_NAMES = { launch = "Launch", analyze = "Analyze", probe = "Probe", build = "Build harness",
	play = "Play", results = "Results", report = "Report" }
local MARKS = { text = "?", read = "R", search = "S", write = "W", probe = "P", episode = "E", look = "L",
	report = "!", alert = "!", steer = ">", restart = "*", memory = "M" }
local HISTORY = 30
local WAITING_FOR_GAME = "Waiting for the game to start"
-- The Policy log: a run's policy steps kept (the oldest go), and the rows shown of them
local POLICY_KEPT = 300
local POLICY_SHOWN = 100
-- decided.summary's "<action>: <name>" -> the tag shown (as Godot's Policy log)
local TAGS = { started = "START", kept = "KEEP", switched = "SWITCH", kept_running = "HELD", dropped = "DROPPED" }
-- The Settings' words (as Godot's Settings page)
local BRIEF_TEXT = "How the game is meant to work: its design, the rules players are told (e.g. “the player has three "
	.. "lives; each coin is worth 10 points”). Explorations report what differs from it as a bug. Sent with every run."
local RESEARCH_TEXT = "Enabling the research agent allows Autopilot to achieve more difficult goals with fewer "
	.. "attempts, but at a higher cost. Applies from the next run."
local POLICY_TEXT = "The Policy log: every step the agent took in the game (the action it chose, how likely it was). "
	.. "Asked of the agent service at once."

local function corner(parent: Instance, r: number?)
	local c = Instance.new("UICorner")
	c.CornerRadius = UDim.new(0, r or 6)
	c.Parent = parent
end

local function pad(parent: Instance, px: number)
	local p = Instance.new("UIPadding")
	p.PaddingLeft, p.PaddingRight = UDim.new(0, px), UDim.new(0, px)
	p.PaddingTop, p.PaddingBottom = UDim.new(0, px), UDim.new(0, px)
	p.Parent = parent
end

local function list(parent: Instance, gap: number?, horizontal: boolean?)
	local l = Instance.new("UIListLayout")
	l.SortOrder = Enum.SortOrder.LayoutOrder
	l.Padding = UDim.new(0, gap or 6)
	if horizontal then
		l.FillDirection = Enum.FillDirection.Horizontal
		l.VerticalAlignment = Enum.VerticalAlignment.Center
		l.Wraps = true
	end
	l.Parent = parent
	return l
end

local order = 0
local function nextOrder(): number
	order += 1
	return order
end

local function frame(parent: Instance, name: string, horizontal: boolean?, color: Color3?): Frame
	local f = Instance.new("Frame")
	f.Name = name
	f.BackgroundColor3 = color or C.panel
	f.BackgroundTransparency = if color then 0 else 1
	f.BorderSizePixel = 0
	f.Size = UDim2.new(1, 0, 0, 0)
	f.AutomaticSize = Enum.AutomaticSize.Y
	f.LayoutOrder = nextOrder()
	list(f, if horizontal then 6 else 4, horizontal)
	f.Parent = parent
	return f
end

local function label(parent: Instance, text: string, opts: any?): TextLabel
	local o = opts or {}
	local l = Instance.new("TextLabel")
	l.Name = o.name or "Label"
	l.BackgroundTransparency = 1
	l.Text = text
	l.TextColor3 = o.color or C.text
	l.FontFace = o.font or FONT
	l.TextSize = o.size or 15
	l.TextWrapped = o.wrap ~= false
	l.TextXAlignment = Enum.TextXAlignment.Left
	l.TextYAlignment = Enum.TextYAlignment.Top
	l.RichText = o.rich == true
	l.Size = o.width and UDim2.new(0, o.width, 0, 0) or UDim2.new(1, 0, 0, 0)
	l.AutomaticSize = if o.width then Enum.AutomaticSize.XY else Enum.AutomaticSize.Y
	if o.width == 0 then
		l.Size = UDim2.new(0, 0, 0, 0)
		l.AutomaticSize = Enum.AutomaticSize.XY
		l.TextWrapped = false
	end
	l.LayoutOrder = nextOrder()
	l.Parent = parent
	return l
end

local function button(parent: Instance, text: string, onClick: () -> (), opts: any?): TextButton
	local o = opts or {}
	local b = Instance.new("TextButton")
	b.Name = o.name or text
	b.Text = text
	b.FontFace = BOLD
	b.TextSize = 15
	b.AutoButtonColor = true
	b.BackgroundColor3 = if o.primary then C.accent else C.field
	b.TextColor3 = if o.primary then Color3.new(0, 0, 0) else (o.color or C.text)
	b.BorderSizePixel = 0
	b.Size = UDim2.new(0, 0, 0, 28)
	b.AutomaticSize = Enum.AutomaticSize.X
	b.LayoutOrder = nextOrder()
	pad(b, 0)
	local p = b:FindFirstChildOfClass("UIPadding") :: UIPadding
	p.PaddingLeft, p.PaddingRight = UDim.new(0, 12), UDim.new(0, 12)
	corner(b, 4)
	b.MouseButton1Click:Connect(function()
		local ok, err = pcall(onClick)
		if not ok then
			warn("[Autopilot] " .. tostring(err))
		end
	end)
	b.Parent = parent
	return b
end

local function input(parent: Instance, placeholder: string, opts: any?): TextBox
	local o = opts or {}
	local t = Instance.new("TextBox")
	t.Name = o.name or "Input"
	t.PlaceholderText = placeholder
	t.PlaceholderColor3 = C.muted
	t.Text = o.text or ""
	t.TextColor3 = C.text
	t.FontFace = if o.mono then MONO else FONT
	t.TextSize = 15
	t.ClearTextOnFocus = false
	t.MultiLine = o.multiline == true
	t.TextWrapped = o.multiline == true
	t.TextXAlignment = Enum.TextXAlignment.Left
	t.TextYAlignment = if o.multiline then Enum.TextYAlignment.Top else Enum.TextYAlignment.Center
	t.BackgroundColor3 = C.field
	t.BorderSizePixel = 0
	t.Size = o.width and UDim2.new(0, o.width, 0, o.height or 28) or UDim2.new(1, 0, 0, o.height or 28)
	t.LayoutOrder = nextOrder()
	pad(t, 6)
	corner(t, 4)
	t.Parent = parent
	return t
end

local function clear(f: Instance)
	for _, c in ipairs(f:GetChildren()) do
		if not c:IsA("UIListLayout") and not c:IsA("UIPadding") and not c:IsA("UICorner") then
			c:Destroy()
		end
	end
end

local function mmss(t: number): string
	t = math.max(0, math.floor(t))
	return string.format("%d:%02d", t // 60, t % 60)
end

local function esc(s: string): string
	return (string.gsub(string.gsub(string.gsub(s, "&", "&amp;"), "<", "&lt;"), ">", "&gt;"))
end

-- A policy step (the agent service's "jev" frame) as the Policy log shows it: {n, t, tag, chosen, p, ms}.
local function policyStep(f: any): any
	local decided = if type(f.decided) == "table" then f.decided else {}
	local summary = tostring(decided.summary or "")
	local action, rest = string.match(summary, "^(.-): (.*)$")
	local chosen = ""
	if type(decided.chosen) == "table" and decided.chosen[1] ~= nil then
		chosen = tostring(decided.chosen[1])
	elseif rest and rest ~= "None" then
		chosen = rest
	end
	local p = nil
	local out = f.output
	if chosen ~= "" and type(out) == "table" and type(out.answers) == "table" and type(out.answers.pick) == "table" then
		p = tonumber(out.answers.pick[chosen])
	end
	local secs = if type(f.seconds) == "table" then tonumber(f.seconds.jev) else nil
	local tag = action or summary
	return { n = tonumber(f.call) or tonumber(f.step) or 0, t = tonumber(f.t) or 0,
		tag = TAGS[tag] or string.upper(tag), chosen = chosen, p = p, ms = if secs then secs * 1000 else nil }
end

-- A usage frame's models (each {kind, model, calls, input, output, cache_read, cache_write}), whole numbers only: small
-- enough to keep with a run in the history.
local function usageRows(models: any): { any }
	local rows = {}
	if type(models) ~= "table" then
		return rows
	end
	for _, m in ipairs(models) do
		if type(m) == "table" then
			local row: any = { kind = if m.kind == "decision" then "decision" else "llm",
				model = tostring(m.model or "") }
			for _, k in ipairs({ "calls", "input", "output", "cache_read", "cache_write" }) do
				row[k] = math.max(0, math.floor(tonumber(m[k]) or 0))
			end
			table.insert(rows, row)
		end
	end
	return rows
end

local function tokens(n: number): string
	if n >= 1e6 then
		return string.format("%.1fM", n / 1e6)
	elseif n >= 1e3 then
		return string.format("%.1fk", n / 1e3)
	end
	return tostring(n)
end

function Dock.new(plugin: Plugin, editor: any, _options: any?)
	local self = setmetatable({}, Dock) :: any
	self.plugin = plugin
	self.editor = editor
	self.conns = {}
	self.run = nil -- the run shown: {id, prompt, kind, tested, started, events, result, ...}
	self.history = {}
	local okh, saved = pcall(function()
		return Settings.get(plugin, "history")
	end)
	if okh and type(saved) == "table" then
		self.history = saved
	end
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
		local info = DockWidgetPluginGuiInfo.new(Enum.InitialDockState.Right, false, false, 420, 640, 320, 360)
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
	self.widget = widget
	if mine.button then
		table.insert(self.conns, mine.button.Click:Connect(function()
			widget.Enabled = not widget.Enabled
		end))
	end
	self:_build()
	editor.activitySince = function()
		if self.run and not self.run.result then
			return self.run.lastEvent or 0
		end
		return -1
	end
	editor:on("status", function(text)
		self:_status(text)
	end)
	editor:on("server_status", function(text)
		self:_serverStatus(text)
	end)
	editor:on("connection", function()
		self:_renderConnection()
		self:_renderAccount()
		self:_renderComposer()
	end)
	editor:on("run", function()
		self:_renderComposer()
		self:_renderLive()
	end)
	editor:on("run_started", function(run)
		self:_newRun(run, run.by)
	end)
	editor:on("run_kind", function(kind, tested)
		if self.run then
			self.run.kind = kind
			self.run.tested = tested
			self:_renderLive()
		end
	end)
	editor:on("activity", function(f)
		self:_activity(f)
	end)
	editor:on("finding", function(f)
		if self.run and not self.run.result then
			table.insert(self.run.findings, f)
		end
	end)
	editor:on("result", function(result)
		self:_result(result)
	end)
	editor:on("memory", function()
		self:_renderMemory()
	end)
	editor:on("account", function(acc)
		if acc.signed_in and self.signIn and self.signIn.state == "waiting" then
			-- (the sign-in finished while the dock was away: what it waited for is done)
			self.signIn = nil
			editor:signInCancel()
			if acc.mode ~= "aura" then
				self:_useAura()
			end
		end
		self:_renderAccount()
		self:_renderSettings()
	end)
	editor:on("sign_in", function(f)
		self.signIn = f
		if f.state == "signed_in" then
			-- (signed in to run with Aura: runs go through it from now on, as the other plugins' setup records)
			self:_useAura()
		end
		self:_renderAccount()
	end)
	editor:on("setup_saved", function(f)
		self:_status(if f.ok then "Saved: applies to the next run" else ("Not saved: " .. tostring(f.message or "")))
		editor:askAccount()
	end)
	editor:on("bill", function(f)
		if self.run then
			self.run.bill = f
		end
	end)
	editor:on("playing", function()
		self:_renderConnection()
	end)
	editor:on("policy", function(f)
		self:_policyStep(f)
	end)
	editor:on("usage", function(models, final)
		self:_usage(models, final)
	end)
	-- (the game coming up changes the live line with no frame from the agent service: Studio's play session starting,
	-- Editor's `starting` and `playing`)
	table.insert(self.conns, game:GetService("RunService").Heartbeat:Connect(function()
		if self.run and not self.run.result and self:_waiting() ~= (self.waitingShown == true) then
			self:_renderLive()
		end
	end))
	self:_renderAll()
	return self
end

-- --- layout -------------------------------------------------------------------------------------------------------

function Dock:_build()
	local root = Instance.new("ScrollingFrame")
	root.Name = "Root"
	root.Size = UDim2.fromScale(1, 1)
	root.BackgroundColor3 = C.bg
	root.BorderSizePixel = 0
	root.CanvasSize = UDim2.new(0, 0, 0, 0)
	root.AutomaticCanvasSize = Enum.AutomaticSize.Y
	root.ScrollBarThickness = 6
	root.ScrollingDirection = Enum.ScrollingDirection.Y
	pad(root, 10)
	list(root, 10)
	root.Parent = self.widget
	self.root = root
	self.header = frame(root, "Header", true)
	self.account = frame(root, "Account", false, C.panel)
	pad(self.account, 8)
	corner(self.account, 8)
	self.memoryRow = frame(root, "Memory", true)
	self.composer = frame(root, "Composer", false, C.panel)
	pad(self.composer, 8)
	corner(self.composer, 8)
	self.live = frame(root, "Live", false, C.panel)
	pad(self.live, 8)
	corner(self.live, 8)
	self.policyBox = frame(root, "PolicyLog", false, C.panel)
	pad(self.policyBox, 8)
	corner(self.policyBox, 8)
	self.resultBox = frame(root, "Result", false, C.panel)
	pad(self.resultBox, 8)
	corner(self.resultBox, 8)
	self.feed = frame(root, "Feed", false, C.panel)
	pad(self.feed, 8)
	corner(self.feed, 8)
	self.historyBox = frame(root, "History", false)
	self.settingsBox = frame(root, "Settings", false, C.panel)
	pad(self.settingsBox, 8)
	corner(self.settingsBox, 8)
	self.statusLine = label(root, "", { color = C.muted, size = 14, name = "Status" })
end

function Dock:_renderAll()
	self:_renderConnection()
	self:_renderAccount()
	self:_renderMemory()
	self:_renderComposer()
	self:_renderLive()
	self:_renderPolicy()
	self:_renderResult()
	self:_renderFeed()
	self:_renderHistory()
	self:_renderSettings()
end

function Dock:_status(text: string)
	self.statusLine.Text = text or ""
	local stamp = os.clock()
	self.statusAt = stamp
	task.delay(8, function()
		if self.statusAt == stamp and self.statusLine.Parent then
			self.statusLine.Text = ""
		end
	end)
	if not self.editor:connected() then
		self:_renderConnection()
	end
end

function Dock:_serverStatus(text: string)
	if self.run and not self.run.result then
		self.run.statusLine = text
		self:_renderLive()
	end
end

function Dock:_renderConnection()
	local h = self.header
	clear(h)
	local ed = self.editor
	local on = ed:connected()
	label(h, "AUTOPILOT", { font = BOLD, size = 16, color = C.accent, width = 0 })
	label(h, if on then "● CONNECTED" else "○ NOT CONNECTED", { color = if on then C.good else C.muted, size = 13,
		width = 0,
		font = MONO })
	if ed.playing then
		label(h, "▶ PLAYING", { color = C.accent, size = 13, width = 0, font = MONO })
	end
	if on then
		button(h, "Disconnect", function()
			ed:disconnect()
		end)
	else
		button(h, "Connect", function()
			ed:connect()
		end, { primary = true })
	end
	if not on then
		local why = ed.link.problem
		label(h, (if why ~= "" then why .. "\n" else "") .. "The agent service runs on this computer: start it with "
			.. "`autopilot serve` in a terminal (or `python -m autopilot serve` in the Autopilot repository). Address: "
			.. ed.link.url, { color = C.muted, size = 13 })
	end
end

function Dock:_renderAccount()
	local a = self.account
	clear(a)
	local ed = self.editor
	label(a, "ACCOUNT", { font = MONO, size = 12, color = C.muted })
	if not ed:connected() then
		label(a, "Connect to the agent service to see how runs go.", { color = C.muted, size = 14 })
		return
	end
	local acc = ed.account or {}
	local mode = acc.mode
	local row = frame(a, "Row", true)
	if mode == "aura" then
		local email = acc.email or ""
		label(row, "Aura" .. (if email ~= "" then " · " .. email else "")
			.. (if acc.signed_in then "" else " · signed out"),
			{ width = 0 })
		local bill = acc.bill
		if type(bill) == "table" and bill.usd then
			label(a, string.format("Billing period: $%.2f USD", tonumber(bill.usd) or 0), { color = C.muted, size = 14 })
		end
		if type(acc.out_of_credits) == "table" then
			label(a, tostring(acc.out_of_credits.message or "Out of credits"), { color = C.bad, size = 14 })
		end
		if acc.signed_in then
			button(row, "Sign out", function()
				ed:signOut()
			end, { color = C.bad })
		end
	elseif mode == "own_keys" then
		local keys = acc.keys or {}
		label(row, string.format("BYOK · Decision provider %s · %s (%s) %s", tostring(keys.decision or "—"),
			tostring(acc.coding_agent or "opencode"), tostring(acc.llm_provider or "anthropic"), tostring(keys.llm or "—")),
			{ width = 0, size = 14 })
	elseif acc.signed_in then
		label(row, "Signed in to Aura" .. (if acc.email then " as " .. tostring(acc.email) else "")
			.. ": runs don't go through it yet", { width = 0, size = 14 })
		button(row, "Run with Aura", function()
			self:_useAura()
		end, { primary = true })
	else
		label(row, "Not set up: choose how to run Autopilot", { width = 0 })
	end
	local si = self.signIn
	if si and si.state == "waiting" then
		label(a, "Finish signing in in your browser. If none opened, copy this address into one:", { color = C.warn,
			size = 14 })
		-- (a box, so the address can be selected and copied: Studio's labels can't be)
		local link = input(a, "", { mono = true, text = tostring(si.url or ""), name = "SignInUrl", multiline = true,
			height = 54 })
		link.TextWrapped = true
		link.Focused:Connect(function()
			link.SelectionStart = 1
			link.CursorPosition = #link.Text + 1
		end)
		button(a, "Cancel", function()
			ed:signInCancel()
			self.signIn = nil
			self:_renderAccount()
		end)
	elseif si and si.state == "error" then
		label(a, "Sign-in failed: " .. tostring(si.message or si.error or ""), { color = C.bad, size = 14 })
	end
	local buttons = frame(a, "Buttons", true)
	if mode ~= "aura" or not acc.signed_in then
		button(buttons, "Sign in with Aura", function()
			if ed:signIn() then
				self:_status("Opening Aura's sign-in in your browser…")
			end
		end, { primary = mode ~= "own_keys" })
	end
	if ed.brainSetup then
		button(buttons, if self.byokOpen then "Hide keys" else "Use my keys (BYOK)", function()
			self.byokOpen = not self.byokOpen
			self:_renderAccount()
		end)
	end
	if self.byokOpen and ed.brainSetup then
		self:_renderByok(a, acc)
	end
end

local AGENTS = { { "opencode", "OpenCode" }, { "claude", "Claude Code" }, { "codex", "Codex" } }
local PROVIDERS = { { "anthropic", "Anthropic" }, { "openai", "OpenAI" }, { "opencode", "OpenCode Zen" },
	{ "openrouter", "OpenRouter" }, { "deepseek", "DeepSeek" }, { "google", "Gemini" } }

function Dock:_renderByok(a: Instance, acc: any)
	self.byok = self.byok or { agent = acc.coding_agent or "opencode", provider = acc.llm_provider or "anthropic" }
	local st = self.byok
	label(a, "Saved on this computer by the agent service (Autopilot/autopilot.env): leave a key empty to keep the one "
		.. "it has.", { color = C.muted, size = 13 })
	local decision = input(a, "Decision provider API key (TypeSafe)", { mono = true })
	local row = frame(a, "Agent", true)
	label(row, "Coding agent", { width = 0, color = C.muted, size = 14 })
	local function nameOf(t, id)
		for _, x in ipairs(t) do
			if x[1] == id then
				return x[2]
			end
		end
		return id
	end
	local function cycle(t, id)
		for i, x in ipairs(t) do
			if x[1] == id then
				return t[i % #t + 1][1]
			end
		end
		return t[1][1]
	end
	button(row, nameOf(AGENTS, st.agent), function()
		st.agent = cycle(AGENTS, st.agent)
		self:_renderAccount()
	end)
	local provider = if st.agent == "claude" then "anthropic" elseif st.agent == "codex" then "openai" else st.provider
	label(row, "LLM provider", { width = 0, color = C.muted, size = 14 })
	button(row, nameOf(PROVIDERS, provider), function()
		if st.agent == "opencode" then
			st.provider = cycle(PROVIDERS, st.provider)
			self:_renderAccount()
		end
	end)
	local llm = input(a, "LLM provider API key", { mono = true })
	button(a, "Use my keys", function()
		local keys = Json.object()
		if decision.Text ~= "" then
			keys.decision = decision.Text
		end
		if llm.Text ~= "" then
			keys.llm = llm.Text
		end
		if self.editor:saveSetup({ access = "own_keys", coding_agent = st.agent, llm_provider = provider, keys = keys,
			share = Settings.get(self.plugin, "share") ~= false }) then
			self.byokOpen = false
			self:_status("Saving your keys…")
		else
			self:_status("This agent service can't save keys for Roblox Studio: put them in Autopilot/autopilot.env")
		end
	end, { primary = true })
end

-- Runs go through Aura from now on (the agent service saves the choice: preferences.json's access).
function Dock:_useAura()
	if self.editor:saveSetup({ access = "aura", share = Settings.get(self.plugin, "share") ~= false }) then
		self:_status("Runs go through Aura")
	else
		self:_status("This agent service can't save the choice for Roblox Studio: start it with `autopilot serve --aura`")
	end
end

function Dock:_renderMemory()
	local m = self.memoryRow
	clear(m)
	local mem = self.editor.memory
	local text = "MEMORY"
	if mem.known then
		text ..= if mem.runs > 0 then string.format(" · %d run%s", mem.runs, if mem.runs == 1 then "" else "s")
			elseif mem.notes or mem.lib then " · notes" else " · empty"
	end
	label(m, text, { font = MONO, size = 12, color = C.muted, width = 0 })
	if self.confirmReset then
		label(m, "Reset memory? The agent forgets what it has learned about this game.", { width = 0, size = 13 })
		button(m, "Reset memory", function()
			self.confirmReset = false
			self.editor:clearMemory()
			self:_renderMemory()
		end, { color = C.bad })
		button(m, "Cancel", function()
			self.confirmReset = false
			self:_renderMemory()
		end)
	else
		button(m, "Reset memory…", function()
			if not self.editor:connected() then
				self:_status("Not connected: connect first to reset the memory")
			elseif self.editor.runActive then
				self:_status("A run is going: reset the memory once it is done")
			else
				self.confirmReset = true
				self:_renderMemory()
			end
		end)
	end
end

function Dock:_renderComposer()
	local c = self.composer
	clear(c)
	local ed = self.editor
	label(c, "NEW RUN", { font = MONO, size = 12, color = C.muted })
	local acc = ed.account or {}
	if type(acc.out_of_credits) == "table" then
		label(c, tostring(acc.out_of_credits.message or ""), { color = C.bad, size = 14 })
	end
	local prompt = input(c, "Describe what to verify, reach or explore", { multiline = true, height = 78,
		text = self.promptText or "", name = "Prompt" })
	prompt:GetPropertyChangedSignal("Text"):Connect(function()
		self.promptText = prompt.Text
	end)
	local row = frame(c, "Row", true)
	label(row, "Time limit (minutes)", { width = 0, color = C.muted, size = 14 })
	local minutes = input(row, "30", { width = 56, text = tostring(Settings.get(self.plugin, "time_limit") or 30),
		name = "Minutes" })
	minutes.FocusLost:Connect(function()
		local n = math.clamp(math.floor(tonumber(minutes.Text) or 30), 1, 600)
		minutes.Text = tostring(n)
		Settings.set(self.plugin, "time_limit", n)
	end)
	local runButton = button(row, "Run", function()
		local words = string.gsub(prompt.Text, "^%s+", "")
		if words == "" then
			return
		end
		if not ed:connected() then
			self:_status("Not connected: connect first")
			return
		end
		if ed.runActive then
			self:_status("A run is going: wait for its result, or stop it")
			return
		end
		local n = math.clamp(math.floor(tonumber(minutes.Text) or 30), 1, 600)
		Settings.set(self.plugin, "time_limit", n)
		local run: any = { kind = "auto", prompt = words, time_limit_minutes = n }
		local brief = self:_brief()
		if brief ~= "" then
			run.design_brief = brief -- (how the game is meant to work: the Settings' Design brief)
		end
		if ed:startRun(run) then
			self:_newRun(run, nil)
		end
	end, { primary = true, name = "Run" })
	if not ed:connected() or ed.runActive then
		runButton.BackgroundColor3 = C.field
		runButton.TextColor3 = C.muted
	end
end

function Dock:_newRun(run: any, by: string?)
	self.run = { id = HttpService:GenerateGUID(false), prompt = run.prompt or run.claim or run.goal or run.brief or "",
		kind = if run.kind ~= "auto" then run.kind else nil, started = os.time(), clock = os.clock(), events = {},
		findings = {}, stage = "launch", by = by, time_limit = run.time_limit_minutes, lastEvent = 0, steps = {} }
	self:_renderLive()
	self:_renderPolicy()
	self:_renderResult()
	self:_renderFeed()
end

-- The Settings' Design brief as typed (kept as it is typed, saved here too should its box lose focus unseen), trimmed.
function Dock:_brief(): string
	if self.briefText ~= nil then
		Settings.set(self.plugin, "design_brief", self.briefText)
	end
	local text = tostring(self.briefText or Settings.get(self.plugin, "design_brief") or "")
	return string.match(text, "^%s*(.-)%s*$") or ""
end

-- A run's play session is starting (Studio asked to play, its client not attached yet).
function Dock:_waiting(): boolean
	local ed = self.editor
	return ed.runActive and ed.starting and not ed.playing
end

function Dock:_activity(f: any)
	local run = self.run
	if run == nil then
		self:_newRun({ prompt = "" }, nil)
		run = self.run
	end
	local id = tonumber(f.id) or 0
	if id > 0 and id <= (run.lastEvent or 0) then
		return -- (sent again: once)
	end
	run.lastEvent = math.max(run.lastEvent or 0, id)
	if f.stage then
		run.stage = f.stage
	end
	table.insert(run.events, f)
	self:_renderLive()
	self:_renderFeed()
end

function Dock:_renderLive()
	local l = self.live
	clear(l)
	local run = self.run
	if run == nil or run.result then
		l.Visible = false
		return
	end
	l.Visible = true
	local ed = self.editor
	local kind = if run.kind then string.upper(run.kind) else "…"
	if run.tested and run.kind == "explore" then
		kind = "TEST"
	end
	label(l, "LIVE // " .. kind .. (if run.by then " // started from " .. tostring(run.by) else ""),
		{ font = MONO, size = 12, color = C.accent })
	label(l, esc(run.prompt or ""), { size = 15, rich = true })
	local track = {}
	local reached = false
	for _, s in ipairs(STAGES) do
		local name = STAGE_NAMES[s]
		if s == run.stage then
			table.insert(track, '<font color="#f0873a"><b>' .. name .. "</b></font>")
			reached = true
		elseif not reached then
			table.insert(track, '<font color="#4cc38a">' .. name .. "</font>")
		else
			table.insert(track, '<font color="#666666">' .. name .. "</font>")
		end
	end
	label(l, table.concat(track, "  ›  "), { rich = true, size = 13 })
	-- (as Godot's: while the run's play session is starting, "Waiting for the game to start"; else the status line)
	self.waitingShown = self:_waiting()
	local now = if self.waitingShown then WAITING_FOR_GAME else run.statusLine
	if now then
		label(l, now, { color = if self.waitingShown then C.warn else C.muted, size = 14, name = "Now" })
	end
	-- (the other plugins keep the person's input out of the game during a run; Roblox gives a plugin no way to tell
	-- the person's input from its own)
	label(l, "Hands off the game view while Autopilot plays: your clicks and keys there reach the game too.",
		{ color = C.muted, size = 12, name = "HandsOff" })
	local row = frame(l, "Row", true)
	button(row, "Stop", function()
		ed:stopRun()
	end, { color = C.bad })
	local steer = input(l, "Send a steering prompt to Autopilot", { text = self.steerText or "", name = "Steer" })
	steer:GetPropertyChangedSignal("Text"):Connect(function()
		self.steerText = steer.Text
	end)
	local canSteer = run.stage == "play" and ed:canSteer()
	local sb = button(l, "Steer", function()
		if not (run.stage == "play" and ed:canSteer()) then
			self:_status("You can steer once the game is playing (Play)")
			return
		end
		ed:steer(steer.Text)
		self.steerText = ""
		steer.Text = ""
	end)
	if not canSteer then
		sb.TextColor3 = C.muted
	end
end

function Dock:_renderFeed()
	local f = self.feed
	clear(f)
	local run = self.run
	label(f, "FEED", { font = MONO, size = 12, color = C.muted })
	if run == nil or #run.events == 0 then
		label(f, "Nothing yet.", { color = C.muted, size = 14 })
		return
	end
	local stage = nil
	local first = math.max(1, #run.events - 150)
	for i = first, #run.events do
		local e = run.events[i]
		if e.stage ~= stage then
			stage = e.stage
			label(f, string.upper(STAGE_NAMES[stage] or tostring(stage)), { font = BOLD, size = 13, color = C.accent })
		end
		local mark = MARKS[e.kind] or "·"
		local text = string.format('<font color="#9a9a9a">%s</font>  <b>%s</b>  %s', mmss(tonumber(e.t) or 0), mark,
			esc(tostring(e.text or "")))
		if e.ends then
			text = string.format('<font color="#9a9a9a">%s   ↳ %s</font>', mmss(tonumber(e.t) or 0),
				esc(tostring(e.text or "")))
		end
		local line = label(f, text, { rich = true, size = 14 })
		if e.detail and e.detail ~= Json.null and tostring(e.detail) ~= "" then
			local open = false
			local box: TextBox? = nil
			local b = Instance.new("TextButton")
			b.Text = "  + the details"
			b.TextColor3 = C.muted
			b.FontFace = FONT
			b.TextSize = 13
			b.BackgroundTransparency = 1
			b.TextXAlignment = Enum.TextXAlignment.Left
			b.Size = UDim2.new(1, 0, 0, 18)
			b.LayoutOrder = line.LayoutOrder
			b.Parent = f
			b.MouseButton1Click:Connect(function()
				open = not open
				if open then
					local t = Instance.new("TextBox")
					t.Text = string.sub(tostring(e.detail), 1, 16000)
					t.TextEditable = false
					t.ClearTextOnFocus = false
					t.MultiLine = true
					t.TextWrapped = true
					t.FontFace = MONO
					t.TextSize = 13
					t.TextColor3 = C.text
					t.TextXAlignment = Enum.TextXAlignment.Left
					t.TextYAlignment = Enum.TextYAlignment.Top
					t.BackgroundColor3 = C.field
					t.Size = UDim2.new(1, 0, 0, 0)
					t.AutomaticSize = Enum.AutomaticSize.Y
					t.LayoutOrder = b.LayoutOrder
					pad(t, 6)
					t.Parent = f
					box = t
					b.Text = "  − the details"
				else
					if box then
						box:Destroy()
					end
					b.Text = "  + the details"
				end
			end)
		end
	end
end

-- --- the Policy log ------------------------------------------------------------------------------------------------

-- A policy step's row: "#12  0:34  SWITCH  JumpGap · 62% · 40 ms".
local function policyRow(parent: Instance, st: any): TextLabel
	local text = string.format('<font color="#9a9a9a">#%d  %s</font>  <b>%s</b>  %s', st.n, mmss(st.t), esc(st.tag),
		esc(st.chosen))
	if st.p then
		text ..= string.format(" · %d%%", math.floor(st.p * 100 + 0.5))
	end
	if st.ms then
		text ..= string.format(' <font color="#9a9a9a">· %d ms</font>', math.floor(st.ms + 0.5))
	end
	return label(parent, text, { rich = true, size = 13, font = MONO })
end

-- The Policy log (Settings' Show Policy Log): the shown run's policy steps, folded under its header (its count), open a
-- list of the last POLICY_SHOWN that follows the newest while scrolled to the bottom.
function Dock:_renderPolicy()
	local box = self.policyBox
	clear(box)
	self.policyList, self.policyRows = nil, {}
	local run = self.run
	local steps = if run then run.steps else nil
	if Settings.get(self.plugin, "show_policy_log") == false or steps == nil or #steps == 0 then
		box.Visible = false
		return
	end
	box.Visible = true
	local head = Instance.new("TextButton")
	head.Name = "Header"
	head.BackgroundTransparency = 1
	head.FontFace = MONO
	head.TextSize = 12
	head.TextColor3 = C.muted
	head.TextXAlignment = Enum.TextXAlignment.Left
	head.Size = UDim2.new(1, 0, 0, 18)
	head.LayoutOrder = nextOrder()
	head.Parent = box
	self.policyHeader = head
	head.MouseButton1Click:Connect(function()
		self.policyOpen = not self.policyOpen
		self:_renderPolicy()
	end)
	self:_policyHeader()
	if not self.policyOpen then
		return
	end
	local sc = Instance.new("ScrollingFrame")
	sc.Name = "Steps"
	sc.BackgroundColor3 = C.field
	sc.BorderSizePixel = 0
	sc.Size = UDim2.new(1, 0, 0, 220)
	sc.CanvasSize = UDim2.new(0, 0, 0, 0)
	sc.AutomaticCanvasSize = Enum.AutomaticSize.Y
	sc.ScrollBarThickness = 6
	sc.ScrollingDirection = Enum.ScrollingDirection.Y
	sc.LayoutOrder = nextOrder()
	pad(sc, 4)
	list(sc, 2)
	sc.Parent = box
	self.policyList = sc
	for i = math.max(1, #steps - POLICY_SHOWN + 1), #steps do
		table.insert(self.policyRows, policyRow(sc, steps[i]))
	end
	task.defer(function()
		sc.CanvasPosition = Vector2.new(0, sc.AbsoluteCanvasSize.Y)
	end)
end

function Dock:_policyHeader()
	local steps = if self.run then self.run.steps or {} else {}
	if self.policyHeader then
		self.policyHeader.Text = string.format("%s POLICY LOG · %d step%s", if self.policyOpen then "▾" else "▸",
			#steps, if #steps == 1 then "" else "s")
	end
end

-- A policy step from the agent service (the Editor's "policy": its "jev" frames, sent while the hello's `inspect`).
function Dock:_policyStep(f: any)
	local run = self.run
	if run == nil or run.result or type(f) ~= "table" then
		return
	end
	run.steps = run.steps or {}
	table.insert(run.steps, policyStep(f))
	if #run.steps > POLICY_KEPT then
		table.remove(run.steps, 1)
	end
	if not self.policyBox.Visible then
		self:_renderPolicy()
		return
	end
	self:_policyHeader()
	local sc = self.policyList
	if sc == nil or sc.Parent == nil then
		return
	end
	local atBottom = sc.CanvasPosition.Y + sc.AbsoluteWindowSize.Y >= sc.AbsoluteCanvasSize.Y - 8
	table.insert(self.policyRows, policyRow(sc, run.steps[#run.steps]))
	while #self.policyRows > POLICY_SHOWN do
		table.remove(self.policyRows, 1):Destroy()
	end
	if atBottom then
		task.defer(function()
			sc.CanvasPosition = Vector2.new(0, sc.AbsoluteCanvasSize.Y)
		end)
	end
end

-- --- usage ---------------------------------------------------------------------------------------------------------

-- A usage frame: the run's token counts so far (each replaces the one before), shown with its result and kept in the
-- history with it. (Not a ledger of many runs, as Godot's Settings page keeps: Studio's plugin settings are small.)
function Dock:_usage(models: any, final: boolean)
	local run = self.run
	if run == nil or (run.result and not run.saved) then
		return -- (no run, or one of the history's shown)
	end
	run.usage = usageRows(models)
	if run.result then
		-- (the final count can come after the result)
		if run.saved then
			run.saved.usage = run.usage
			if final then
				Settings.set(self.plugin, "history", self.history)
			end
		end
		self:_renderResult()
	end
end

-- The report as markdown (the Result's words), for the user to copy from the box.
local function markdown(run: any): string
	local r = run.result or {}
	local lines = { "# Autopilot run", "", "Prompt: " .. tostring(run.prompt or ""), "" }
	if r.verdict then
		table.insert(lines, "Verdict: " .. tostring(r.verdict))
		table.insert(lines, "")
		table.insert(lines, tostring(r.reason or ""))
		for _, e in ipairs(r.evidence or {}) do
			table.insert(lines, "- " .. tostring(e))
		end
	else
		table.insert(lines, (if r.reached then "Reached" else "Not reached") .. ": " .. tostring(r.goal or ""))
		for _, n in ipairs(r.notes or {}) do
			table.insert(lines, "- " .. tostring(n))
		end
	end
	for _, a in ipairs(r.alerts or {}) do
		table.insert(lines, "Alert: " .. tostring(a))
	end
	for _, ch in ipairs(r.changes or {}) do
		table.insert(lines, "Direct change: " .. tostring(ch.text or ""))
	end
	if run.findings and #run.findings > 0 then
		table.insert(lines, "")
		table.insert(lines, "Engine findings:")
		for _, f in ipairs(run.findings) do
			table.insert(lines, "- [" .. tostring(f.severity or "") .. "] " .. tostring(f.summary or ""))
		end
	end
	return table.concat(lines, "\n")
end

function Dock:_result(result: any)
	if self.run == nil then
		self:_newRun({ prompt = result.claim or result.goal or "" }, nil)
	end
	local run = self.run
	run.result = result
	run.seconds = math.floor(os.clock() - (run.clock or os.clock()))
	local saved = { prompt = run.prompt, kind = run.kind, tested = run.tested, started = run.started,
		seconds = run.seconds,
		result = result, findings = run.findings, events = {}, usage = run.usage }
	run.saved = saved
	-- (the feed's lines without their details: plugin settings are kept small)
	for _, e in ipairs(run.events) do
		table.insert(saved.events, { id = e.id, t = e.t, stage = e.stage, kind = e.kind, text = e.text, ends = e.ends })
	end
	table.insert(self.history, 1, saved)
	while #self.history > HISTORY do
		table.remove(self.history)
	end
	Settings.set(self.plugin, "history", self.history)
	self:_renderLive()
	self:_renderResult()
	self:_renderFeed()
	self:_renderHistory()
	self:_renderComposer()
end

function Dock:_renderResult()
	local box = self.resultBox
	clear(box)
	local run = self.run
	if run == nil or run.result == nil then
		box.Visible = false
		return
	end
	box.Visible = true
	local r = run.result
	local verdict, color
	if r.verdict then
		verdict = tostring(r.verdict)
		color = if verdict == "CONFIRMED" then C.good elseif verdict == "DENIED" then C.bad else C.warn
	elseif r.error then
		verdict, color = "ERROR", C.bad
	else
		local explored = run.kind == "explore"
		verdict = if explored then (if run.tested then "GAME TESTED" else "EXPLORED") elseif r.reached then "ACHIEVED"
			else "NOT ACHIEVED"
		color = if r.reached or explored then C.good else C.bad
	end
	local stamp = os.date("%b %d %H:%M", run.started or os.time())
	label(box, string.format("RUN // %s // %s // %s", string.upper(tostring(run.kind or "run")), string.upper(stamp),
		mmss(run.seconds or 0)), { font = MONO, size = 12, color = C.muted })
	label(box, verdict, { font = BOLD, size = 22, color = color })
	label(box, esc(tostring(run.prompt or "")), { color = C.muted, size = 14, rich = true })
	if r.reason and r.reason ~= "" then
		label(box, tostring(r.reason), { size = 15 })
	end
	for _, e in ipairs(r.evidence or {}) do
		label(box, "• " .. tostring(e), { size = 14 })
	end
	for _, n in ipairs(r.notes or {}) do
		label(box, "• " .. tostring(n), { size = 14 })
	end
	for _, a in ipairs(r.alerts or {}) do
		label(box, "⚠ " .. tostring(a), { size = 14, color = C.warn })
	end
	if r.changes and #r.changes > 0 then
		label(box, "DIRECT CHANGES TO THE GAME", { font = MONO, size = 12, color = C.muted })
		for _, ch in ipairs(r.changes) do
			label(box, "• " .. tostring(ch.text or ""), { size = 14 })
		end
	end
	if r.no_recording then
		label(box, "No recording: " .. tostring(r.no_recording), { size = 13, color = C.muted })
	end
	if r.memory and type(r.memory) == "table" and r.memory.updated then
		label(box, "Memory updated", { size = 13, color = C.good })
	end
	if type(run.usage) == "table" and #run.usage > 0 then
		label(box, "USAGE", { font = MONO, size = 12, color = C.muted })
		for _, m in ipairs(run.usage) do
			local cached = (m.cache_read or 0) + (m.cache_write or 0)
			label(box, string.format("%s%s · %d call%s · %s in · %s out%s", if m.kind == "decision" then "Decision · "
				else "", tostring(m.model), m.calls or 0, if m.calls == 1 then "" else "s", tokens(m.input or 0),
				tokens(m.output or 0), if cached > 0 then " · " .. tokens(cached) .. " cached" else ""),
				{ size = 13, color = C.muted })
		end
	end
	local findings = run.findings or {}
	if #findings > 0 then
		label(box, string.format("ENGINE FINDINGS · %d", #findings), { font = MONO, size = 12, color = C.muted })
		for i, f in ipairs(findings) do
			if i > 40 then
				break
			end
			local sev = string.lower(tostring(f.severity or "info"))
			local c = if sev == "error" or sev == "critical" or sev == "fatal" or sev == "bug" then C.bad
				elseif sev == "warning" then C.warn else C.info
			local row = frame(box, "Finding", true)
			label(row, string.upper(sev), { width = 0, color = c, font = MONO, size = 12 })
			label(row, tostring(f.summary or ""), { size = 14, width = 0 })
			if f.signature then
				button(row, "Not a bug", function()
					if self.editor:connected() then
						self.editor:feedback(f.signature, "not_a_bug")
						self:_status("Told the agent: " .. tostring(f.detector or "it") .. " is not a bug")
					else
						self:_status("Not connected: connect to send feedback")
					end
				end)
			end
			if f.actor and f.actor ~= "" then
				button(row, "Show in level", function()
					local found = workspace:FindFirstChild(tostring(f.actor), true)
					if found then
						Selection:Set({ found })
					end
				end)
			end
		end
	end
	local row = frame(box, "Buttons", true)
	button(row, "Run again", function()
		local ed = self.editor
		if not ed:connected() or ed.runActive then
			self:_status(if ed.runActive then "A run is going: wait for its result, or stop it"
				else "Not connected: connect first")
			return
		end
		local again: any = { kind = "auto", prompt = run.prompt, time_limit_minutes = run.time_limit
			or Settings.get(self.plugin, "time_limit") }
		local brief = self:_brief()
		if brief ~= "" then
			again.design_brief = brief
		end
		if ed:startRun(again) then
			self:_newRun(again, nil)
		end
	end, { primary = true })
	button(row, if self.showReport then "Hide report" else "Report (to copy)", function()
		self.showReport = not self.showReport
		self:_renderResult()
	end)
	if #findings > 0 then
		button(row, "Verify a fix", function()
			self.promptText = "Verify that this is fixed: " .. tostring(findings[1].summary or "")
			self:_renderComposer()
		end)
	end
	if self.showReport then
		local t = Instance.new("TextBox")
		t.Text = markdown(run)
		t.TextEditable = false
		t.ClearTextOnFocus = false
		t.MultiLine = true
		t.TextWrapped = true
		t.FontFace = MONO
		t.TextSize = 13
		t.TextColor3 = C.text
		t.BackgroundColor3 = C.field
		t.TextXAlignment = Enum.TextXAlignment.Left
		t.TextYAlignment = Enum.TextYAlignment.Top
		t.Size = UDim2.new(1, 0, 0, 0)
		t.AutomaticSize = Enum.AutomaticSize.Y
		t.LayoutOrder = nextOrder()
		pad(t, 6)
		t.Parent = box
	end
end

function Dock:_renderHistory()
	local h = self.historyBox
	clear(h)
	label(h, string.format("HISTORY · %d run%s", #self.history, if #self.history == 1 then "" else "s"),
		{ font = MONO, size = 12, color = C.muted })
	for i, run in ipairs(self.history) do
		if i > 12 then
			break
		end
		local r = run.result or {}
		local verdict = r.verdict or (if run.kind == "explore" then "EXPLORED" elseif r.reached then "ACHIEVED"
			else "NOT ACHIEVED")
		local b = Instance.new("TextButton")
		b.Name = "Run" .. i
		b.Text = string.format("%s · %s · %s — %s", os.date("%b %d %H:%M", run.started or 0), mmss(run.seconds or 0),
			tostring(verdict), string.sub(tostring(run.prompt or ""), 1, 60))
		b.TextColor3 = C.text
		b.FontFace = FONT
		b.TextSize = 14
		b.TextXAlignment = Enum.TextXAlignment.Left
		b.TextTruncate = Enum.TextTruncate.AtEnd
		b.BackgroundColor3 = if i % 2 == 0 then C.panel else C.bg
		b.BorderSizePixel = 0
		b.Size = UDim2.new(1, 0, 0, 24)
		b.LayoutOrder = nextOrder()
		pad(b, 4)
		b.Parent = h
		b.MouseButton1Click:Connect(function()
			if self.editor.runActive then
				self:_status("A run is going: its live page stays")
				return
			end
			self.run = { prompt = run.prompt, kind = run.kind, tested = run.tested, started = run.started,
				seconds = run.seconds, result = run.result, findings = run.findings or {}, events = run.events or {},
				usage = run.usage }
			self:_renderPolicy()
			self:_renderResult()
			self:_renderFeed()
		end)
	end
end

function Dock:_renderSettings()
	local s = self.settingsBox
	clear(s)
	label(s, "SETTINGS", { font = MONO, size = 12, color = C.muted })
	local url = input(s, "Agent service URL (empty: ws://127.0.0.1:8765/v1/bridge)", { mono = true,
		text = Settings.get(self.plugin, "url") or "" })
	local httpUrl = input(s, "HTTP fallback URL (empty: next port after WebSocket)", { mono = true,
		text = Settings.get(self.plugin, "http_url") or "" })
	local token = input(s, "Access token · optional", { mono = true, text = Settings.get(self.plugin, "token") or "" })
	local function apply()
		local changed = httpUrl.Text ~= (Settings.get(self.plugin, "http_url") or "")
			or url.Text ~= (Settings.get(self.plugin, "url") or "")
			or token.Text ~= (Settings.get(self.plugin, "token") or "")
		Settings.set(self.plugin, "http_url", httpUrl.Text)
		Settings.set(self.plugin, "url", url.Text)
		Settings.set(self.plugin, "token", token.Text)
		if changed and not self.editor.runActive then
			self.editor:connect()
		end
	end
	httpUrl.FocusLost:Connect(apply)
	url.FocusLost:Connect(apply)
	token.FocusLost:Connect(apply)
	-- Design brief: sent with every run the dock starts (the start's `design_brief`)
	label(s, "Design brief", { size = 14 })
	local brief = input(s, "Optional", { multiline = true, height = 78, name = "DesignBrief",
		text = self.briefText or Settings.get(self.plugin, "design_brief") or "" })
	brief:GetPropertyChangedSignal("Text"):Connect(function()
		self.briefText = brief.Text
	end)
	brief.FocusLost:Connect(function()
		Settings.set(self.plugin, "design_brief", brief.Text)
	end)
	label(s, BRIEF_TEXT, { color = C.muted, size = 13 })
	-- Research agent: the hello's `research`, once flipped (until then the switch shows the agent service's own
	-- default, on, as Godot's does without Use with Aura)
	local chosen = Settings.get(self.plugin, "research")
	local research = if type(chosen) == "boolean" then chosen else true
	local rrow = frame(s, "Research", true)
	label(rrow, "Research agent", { width = 0, size = 14 })
	button(rrow, if research then "Research agent on" else "Research agent off", function()
		Settings.set(self.plugin, "research", not research)
		self.editor:helloChanged()
		self:_renderSettings()
	end)
	label(s, RESEARCH_TEXT, { color = C.muted, size = 13 })
	-- Show Policy Log: the Policy log, and the hello's `inspect`
	local policy = Settings.get(self.plugin, "show_policy_log") ~= false
	local prow = frame(s, "PolicyLog", true)
	label(prow, "Show Policy Log", { width = 0, size = 14 })
	button(prow, if policy then "Policy log shown" else "Policy log hidden", function()
		Settings.set(self.plugin, "show_policy_log", not policy)
		self.editor:helloChanged()
		self:_renderPolicy()
		self:_renderSettings()
	end)
	label(s, POLICY_TEXT, { color = C.muted, size = 13 })
	local row = frame(s, "Share", true)
	local share = Settings.get(self.plugin, "share") ~= false
	label(row, "Share runs to improve Autopilot", { width = 0, size = 14 })
	button(row, if share then "Data sharing on" else "Data sharing off", function()
		self.editor:setSharing(not share)
	end)
	label(s, "Enabling this sends agent traces and memories to the developers of Autopilot. Runs are recorded from "
		.. "Studio's MCP server: turn it on in Assistant, ..., Settings, MCP Servers, Enable Studio as MCP server.",
		{ color = C.muted, size = 13 })
	label(s, "Autopilot in Claude Code & Codex: run `autopilot connect claude` or `autopilot connect codex` once, then "
		.. "ask your agent to \"Use Autopilot to ...\" with this place open.", { color = C.muted, size = 13 })
end

function Dock:stop()
	for _, c in ipairs(self.conns) do
		c:Disconnect()
	end
	pcall(function()
		self.root:Destroy()
	end)
end

return Dock
