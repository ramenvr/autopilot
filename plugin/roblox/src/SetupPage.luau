--!strict
-- The first-run setup (as Godot's ui/setup_page.gd), shown in the dock in place of everything else when the agent
-- service says this computer isn't set up (its account frame's mode "none") and the dock hasn't been through it, and
-- again from Rerun setup; a stepper: 1 ACCOUNT, 2 AGENT SERVICE, 3 PRIVACY SETTINGS, and 4 FIRST RUN unless the agent
-- service remembers something about this place.
--  1. Account: Sign in with Aura (the agent service signs in, in the browser) or Create Account (Aura's sign-up page:
--     its address to open, a Roblox plugin can't open a browser); or Bring your own keys (BYOK: the Coding Agent, the
--     LLM provider and the keys, sent to the agent service with the setup's other choices at the end).
--  2. Agent service: a Roblox plugin can't start or install it: ready once connected, else what to run (autopilot serve)
--     and a quiet wait for it (not an alert).
--  3. Privacy settings: whether to share runs, and a recap. Next (Finish when the memory has something) saves the
--     choices through the agent service (save_setup), then the setup closes or goes on to step 4.
--  4. First run: with the place's memory empty, an Explore run is offered (Explore and finish, Skip and finish).
local M = require(script.Parent.RunModel)
local Settings = require(script.Parent.Settings)
local T = require(script.Parent.Theme)

local C = T.C

local SetupPage = {}

local MAX_WIDTH = 600
local DISCLOSURE = "Enabling this sends recordings, screenshots, and agent traces and memories to the developers of "
	.. "Autopilot. For runs from your own Claude Code or Codex, the agent traces are its conversation during the run."
local AURA_BILLED = "Autopilot is maintained by the developers behind Aura, the multiagent AI solution to Unreal and "
	.. "Unity game development. You can use your Aura subscription to run Autopilot. New users get free Aura credits."
local SIGN_UP_URL = "https://app.tryaura.dev/auth/signup?src=autopilot"
local FIRST_RUN_TEXT = "You’ve never used Autopilot with this game before. To help Autopilot understand your game, use "
	.. "this first exploration run and watch it improve. Autopilot still works if you skip this step."
local AGENTS = { "claude", "codex", "opencode" }
local PROVIDERS = { "anthropic", "openai", "opencode", "openrouter", "deepseek", "google" }
local AGENT_NAMES = { claude = "Claude Code", codex = "Codex", opencode = "OpenCode" }
local LLM_NAMES = { anthropic = "Anthropic", openai = "OpenAI", opencode = "OpenCode Zen", openrouter = "OpenRouter",
	deepseek = "DeepSeek", google = "Gemini" }

-- Start over at step 1, the sharing switch as the agent service says.
function SetupPage.open(d: any)
	d.setup = { step = 1, access = "", share = Settings.get(d.plugin, "share") ~= false, email = "", keysShown = false,
		creating = false, agent = "", provider = "", decisionKey = "", llmKey = "", visited2 = false }
end

local function state(d: any): any
	if d.setup == nil then
		SetupPage.open(d)
	end
	return d.setup
end

local function go(d: any, n: number)
	local s = state(d)
	s.step = n
	if n == 2 then
		s.visited2 = true
	end
	SetupPage.render(d, d.setupFrame)
end

-- The Coding Agent picked now, and the LLM provider it uses (Claude Code: Anthropic, Codex: OpenAI, OpenCode: its pick).
local function agentNow(d: any): string
	local s = state(d)
	if s.agent ~= "" then
		return s.agent
	end
	local said = tostring((d.editor.account or {}).coding_agent or "")
	return if AGENT_NAMES[said] then said else "opencode"
end

local function providerNow(d: any): string
	local agent = agentNow(d)
	if agent == "claude" then
		return "anthropic"
	elseif agent == "codex" then
		return "openai"
	end
	local s = state(d)
	if s.provider ~= "" then
		return s.provider
	end
	local said = tostring((d.editor.account or {}).llm_provider or "")
	return if LLM_NAMES[said] then said else "anthropic"
end

-- A dropdown: a field (the value and a chevron) that opens a list of choices under it (in the dock's popup layer).
local function dropdown(d: any, parent: Instance, value: string, choices: { string }, names: any, enabled: boolean,
	onPick: (string) -> (), width: number?): TextButton
	local b = T.button(parent, "", "field", nil, false, { width = width, name = "Dropdown" })
	if width == nil then
		b.AutomaticSize = Enum.AutomaticSize.None
		b.Size = UDim2.new(1, 0, 0, 28)
	end
	T.pad(b, 9, 0, 6, 0)
	local row = Instance.new("Frame")
	row.BackgroundTransparency = 1
	row.Size = UDim2.fromScale(1, 1)
	row.Parent = b
	T.list(row, 4, true)
	T.label(row, names[value] or value, "body", { wrap = false, fill = true, color = if enabled then C.text else C.text3 })
	T.chevron(row, C.text3)
	if not enabled then
		b:SetAttribute("disabled", true)
		b.BackgroundColor3 = C.plate
		b.BackgroundTransparency = 0
		return b
	end
	b.MouseButton1Click:Connect(function()
		local rootPos = d.root.AbsolutePosition
		local p = d:_popup(b.AbsolutePosition.X - rootPos.X, b.AbsolutePosition.Y + b.AbsoluteSize.Y - rootPos.Y + 2,
			b.AbsoluteSize.X)
		T.pad(p, 4, 4, 4, 4)
		for _, c in ipairs(choices) do
			local it = Instance.new("TextButton")
			it.AutoButtonColor = false
			it.BorderSizePixel = 0
			it.BackgroundColor3 = C.hover
			it.BackgroundTransparency = 1
			it.Text = (if c == value then "✓  " else "     ") .. (names[c] or c)
			it.FontFace = T.F.body
			it.TextSize = 14
			it.TextColor3 = C.text2
			it.TextXAlignment = Enum.TextXAlignment.Left
			it.Size = UDim2.new(1, 0, 0, 28)
			it.ZIndex = 61
			it.LayoutOrder = T.nextOrder()
			T.pad(it, 8, 0, 8, 0)
			it.Parent = p
			it.MouseEnter:Connect(function()
				it.BackgroundTransparency = 0
			end)
			it.MouseLeave:Connect(function()
				it.BackgroundTransparency = 1
			end)
			it.MouseButton1Click:Connect(function()
				d:closePopup()
				onPick(c)
			end)
		end
	end)
	return b
end

-- --- pieces ---------------------------------------------------------------------------------------------------------

local function title(v: Instance, text: string)
	local h = T.row(v, 10)
	local mark = T.frame(h, "Mark", C.accent)
	mark.AutomaticSize = Enum.AutomaticSize.None
	mark.Size = UDim2.fromOffset(8, 8)
	T.label(h, text, "heading", { wrap = false })
end

local function head(parent: Instance, text: string, tag: string?, kind: string?)
	local h = T.row(parent, 8, true)
	T.label(h, text, "caps", { wrap = false })
	if tag then
		T.tag(h, tag, kind or "ok")
	end
end

local function card(v: Instance, edge: Color3?, bg: Color3?): Frame
	return T.plate(v, { border = edge, bg = bg })
end

local function spinning(parent: Instance, text: string, style: string?)
	local h = T.row(parent, 10)
	T.spinner(h)
	T.label(h, text, style or "strong", { wrap = false, fill = true })
end

-- A step's foot: under a rule, its buttons (left: before; right: after the room).
local function nav(v: Instance, left: { (Instance) -> () }, right: { (Instance) -> () }, rule: boolean?)
	local f = T.col(v, 12)
	if rule ~= false then
		T.rule(f)
	end
	local h = T.row(f, 6)
	for _, mk in ipairs(left) do
		mk(h)
	end
	local sp = Instance.new("Frame")
	sp.BackgroundTransparency = 1
	sp.Size = UDim2.new(0, 0, 0, 1)
	sp.LayoutOrder = T.nextOrder()
	T.fill(sp)
	sp.Parent = h
	for _, mk in ipairs(right) do
		mk(h)
	end
end

local function btn(text: string, variant: string, onClick: () -> (), disabled: boolean?): (Instance) -> ()
	return function(parent: Instance)
		local b = T.button(parent, text, variant, onClick, false)
		if disabled then
			T.disable(b, true)
		end
	end
end

local function accountWord(s: any): string
	if s.access == "aura" then
		return "AURA"
	elseif s.access == "own_keys" then
		return "BYOK"
	end
	return ""
end

-- The stepper: a 2 px bar per step (the accent: this one; STEP_DONE: done; RULE: to come), its micro-label under it.
local function stepper(d: any, v: Instance)
	local s = state(d)
	local word = accountWord(s)
	local labels = { "1 ACCOUNT" .. (if word ~= "" and s.step > 1 then " · " .. word else ""), "2 AGENT SERVICE",
		"3 PRIVACY SETTINGS" }
	if s.step == 4 or d:memoryNow() ~= "some" then
		table.insert(labels, "4 FIRST RUN")
	end
	local h = T.frame(v, "Stepper")
	T.list(h, 8, true).VerticalAlignment = Enum.VerticalAlignment.Top
	for i, text in ipairs(labels) do
		local lit, now = i <= s.step, i == s.step
		local col = T.col(h, 6)
		col.Size = UDim2.new(1 / #labels, -8 * (#labels - 1) / #labels, 0, 0)
		local bar = T.frame(col, "Bar", if now then C.accent elseif lit then C.stepDone else C.rule)
		bar.AutomaticSize = Enum.AutomaticSize.None
		bar.Size = UDim2.new(1, 0, 0, 2)
		local l = T.label(col, text, "caps", { color = if now then C.accent elseif lit then C.text3 else C.muted })
		l.Text = text
	end
end

-- --- step 1: account ------------------------------------------------------------------------------------------------

local function auraCard(d: any, v: Instance)
	local s = state(d)
	local si = d.signIn or {}
	local st = si.state
	if st == "preparing" or st == "waiting" then
		local a = card(v, C.infoLine)
		if st == "preparing" then
			head(a, "Sign in with Aura", "Getting ready", "info")
			spinning(a, "Starting the sign-in…")
		else
			head(a, "Sign in with Aura", "In your browser", "info")
			spinning(a, "Finish signing in in your browser…", "title")
			T.label(a, "If no page opened, open this address in your browser:", "small")
			T.selectable(a, tostring(si.url or ""), { multiline = true, size = 11 })
		end
		local r = T.row(a, 6, true)
		T.button(r, "Cancel", "quiet", function()
			d:cancelSignIn()
		end)
	elseif st == "no_service" then
		local a = card(v)
		head(a, "Sign in with Aura")
		T.label(a, "Signing in goes through the agent service. Run this in a terminal, then try again:", "small")
		T.selectable(a, d.SERVE_COMMAND, { name = "Command", color = C.accent })
		local r = T.row(a, 6, true)
		T.button(r, "Try again", "primary", function()
			d:beginSignIn()
		end)
		T.button(r, "Cancel", "quiet", function()
			d:cancelSignIn()
		end)
	elseif st == "signed_in" then
		local a = card(v, C.successLine)
		head(a, "Sign in with Aura", "Signed in")
		s.email = tostring(si.email or "")
		local who = T.row(a, 8, true)
		T.label(who, "Signed in as", "small", { wrap = false, color = C.muted })
		T.label(who, if s.email ~= "" then s.email else "your Aura account", "title", { wrap = false })
		local r = T.row(a, 6, true)
		T.button(r, "Continue", "primary", function()
			s.access = "aura"
			go(d, 2)
		end)
		T.button(r, "Use another account", "quiet", function()
			d:anotherAccount()
		end)
	elseif st == "error" then
		local a = card(v, C.redLine)
		head(a, "Sign in with Aura", "Not signed in", "bad")
		T.label(a, tostring(si.message or ""), "dim", { color = C.red })
		local r = T.row(a, 6, true)
		T.button(r, "Try again", "primary", function()
			d:beginSignIn()
		end)
	elseif s.creating then
		-- (Create Account: Aura's sign-up page to open; once the account is made, Sign in)
		local a = card(v, C.infoLine)
		head(a, "Sign in with Aura", "In your browser", "info")
		T.label(a, "Finish creating your Aura account", "title")
		T.label(a, "Open this address in your browser to create your account:", "dim")
		T.selectable(a, SIGN_UP_URL, { height = 28 })
		T.label(a, "Once you have created your account, sign in and start using Autopilot", "dim")
		local r = T.row(a, 6, true)
		T.button(r, "Sign in", "primary", function()
			s.creating = false
			d:beginSignIn()
		end)
	else
		local a = card(v)
		head(a, "Sign in with Aura")
		T.label(a, AURA_BILLED, "dim")
		local r = T.row(a, 6, true)
		T.button(r, "Create Account", "primary", function()
			s.creating = true
			SetupPage.render(d, d.setupFrame)
		end)
		T.button(r, "Sign in", "quiet", function()
			d:beginSignIn()
		end)
	end
end

local function keysCard(d: any, v: Instance)
	local s = state(d)
	local st = (d.signIn or {}).state
	if st == "preparing" or st == "waiting" or st == "no_service" then
		return
	end
	if not s.keysShown then
		local k = T.plate(v, { horizontal = true, gap = 8, name = "Keys" })
		local l = T.label(k, "Bring your own keys (BYOK)", "caps", { wrap = false, fill = true })
		if st == "signed_in" then
			l.TextTransparency = 0.45
		end
		T.button(k, "Show", "quiet", function()
			s.keysShown = true
			SetupPage.render(d, d.setupFrame)
		end, true)
		return
	end
	local k = card(v)
	local h = T.row(k, 8)
	T.label(h, "Bring your own keys (BYOK)", "caps", { wrap = false, fill = true })
	T.button(h, "Hide", "quiet", function()
		s.keysShown = false
		SetupPage.render(d, d.setupFrame)
	end, true)
	local function field(caption: string): Frame
		local f = T.col(k, 5)
		T.label(f, caption, "caps", { wrap = false })
		return f
	end
	local decision = T.input(field("Decision provider API key"), { mono = true, placeholder = "Optional",
		text = s.decisionKey, name = "DecisionKey" })
	decision:GetPropertyChangedSignal("Text"):Connect(function()
		s.decisionKey = decision.Text
	end)
	dropdown(d, field("Coding Agent"), agentNow(d), AGENTS, AGENT_NAMES, true, function(a)
		if providerNow(d) ~= (if a == "claude" then "anthropic" elseif a == "codex" then "openai" else providerNow(d)) then
			s.llmKey = "" -- (a key typed for one LLM provider is never saved as another's)
		end
		s.agent = a
		SetupPage.render(d, d.setupFrame)
	end)
	local lf = field("LLM provider API key")
	local row = T.row(lf, 6)
	dropdown(d, row, providerNow(d), PROVIDERS, LLM_NAMES, agentNow(d) == "opencode", function(p)
		if p ~= providerNow(d) then
			s.llmKey = ""
		end
		s.provider = p
		SetupPage.render(d, d.setupFrame)
	end, 140)
	local llm = T.input(row, { mono = true, placeholder = "Optional", text = s.llmKey, fill = true, name = "LlmKey" })
	llm:GetPropertyChangedSignal("Text"):Connect(function()
		s.llmKey = llm.Text
	end)
	local r = T.row(k, 6, true)
	T.button(r, "Use my keys", "default", function()
		s.access = "own_keys"
		go(d, 2)
	end)
end

-- --- step 2: agent service ------------------------------------------------------------------------------------------

local function buildService(d: any, v: Instance)
	title(v, "Set up the agent service")
	local ed = d.editor
	local back = btn("Back", "quiet", function()
		go(d, 1)
	end)
	if ed:connected() then
		local c = T.plate(v, { border = C.successLine, bg = C.successFill, horizontal = true, gap = 12, name = "Ready" })
		T.label(c, "✓", "mono", { wrap = false, size = 18, color = C.success, font = T.F.monoMedium })
		local tv = T.col(c, 0)
		tv.Size = UDim2.new(0, 0, 0, 0)
		T.fill(tv)
		T.label(tv, "Agent service ready", "strong", { color = C.successFg })
		T.label(tv, "Runs while autopilot serve is open", "small")
		T.label(c, tostring(ed.link.url), "monoDim", { wrap = false })
		nav(v, { back }, { btn("Continue", "primary", function()
			go(d, 3)
		end) })
	else
		-- (a Roblox plugin can't start it: the user runs it, Continue once it is connected; not running yet isn't an
		-- error, so no alert)
		local c = card(v)
		T.label(c, "Run this in a terminal and leave it open while you use Autopilot:", "small")
		T.selectable(c, d.SERVE_COMMAND, { name = "Command", color = C.accent })
		local h = T.row(c, 8, true)
		T.spinner(h)
		T.label(h, "Waiting for the agent service…", "small", { wrap = false, color = C.muted })
		nav(v, { back }, { btn("I’ll do it later", "quiet", function()
			go(d, 3)
		end), btn("Continue", "primary", function() end, true) })
	end
end

-- --- step 3: privacy settings ---------------------------------------------------------------------------------------

local function recap(d: any): { string }
	local s = state(d)
	local who = "Not chosen yet"
	if s.access == "aura" then
		who = if s.email ~= "" then "Aura · " .. s.email else "Aura"
	elseif s.access == "own_keys" then
		who = "BYOK"
	end
	return { who, if d.editor:connected() then "Ready" else "Not running yet" }
end

-- Next / Finish: the choices saved through the agent service (save_setup: preferences.json and, for BYOK,
-- autopilot.env), then the setup closes, or goes on to step 4.
local function finish(d: any)
	local s = state(d)
	local ed = d.editor
	if s.access ~= "" then
		local setup: any = { access = s.access, share = s.share }
		if s.access == "own_keys" then
			local keys = d.Json.object()
			if M.trim(s.decisionKey) ~= "" then
				keys.decision = M.trim(s.decisionKey)
			end
			if M.trim(s.llmKey) ~= "" then
				keys.llm = M.trim(s.llmKey)
			end
			setup.coding_agent = agentNow(d)
			setup.llm_provider = providerNow(d)
			setup.keys = keys
		end
		if not ed:saveSetup(setup) then
			d:status("Not saved: connect to the agent service first (autopilot serve)")
			return
		end
		s.decisionKey, s.llmKey = "", ""
	elseif ed:connected() then
		ed:setSharing(s.share)
	end
	Settings.set(d.plugin, "onboarded", true)
	d.signIn = nil
	if d:memoryNow() == "some" then
		d:closeSetup()
	else
		go(d, 4)
	end
end

local function buildPrivacy(d: any, v: Instance)
	local s = state(d)
	title(v, "Privacy settings")
	local c = card(v)
	T.switch(c, s.share, "Share my runs to improve Autopilot", "Share my runs to improve Autopilot", function(on)
		s.share = on
	end)
	T.label(c, DISCLOSURE, "dim")
	local r = recap(d)
	local kv = T.col(v, 0)
	T.rule(kv)
	for i, pair in ipairs({ { "Account", r[1] }, { "Agent service", r[2] } }) do
		if i > 1 then
			T.rule(kv)
		end
		local h = T.row(kv, 10)
		T.pad(h, 0, 8, 0, 8)
		local k = T.label(h, pair[1], "caps", { wrap = false })
		k.AutomaticSize = Enum.AutomaticSize.None
		k.Size = UDim2.fromOffset(130, 16)
		T.label(h, pair[2], "body", { wrap = false, fill = true, color = C.text })
	end
	T.rule(kv)
	local known = d:memoryNow() == "some"
	nav(v, { btn("Back", "quiet", function()
		go(d, if s.visited2 then 2 else 1)
	end) }, { btn(if known then "Finish" else "Next", "primary", function()
		finish(d)
	end) }, false)
end

-- --- step 4: first run ----------------------------------------------------------------------------------------------

local function buildFirstRun(d: any, v: Instance)
	title(v, "Let Autopilot learn your game")
	local ed = d.editor
	local memory = d:memoryNow()
	if memory ~= "empty" then
		-- (waiting for the connection and its memory_state; with something remembered, the setup closes)
		local c = card(v)
		spinning(c, if ed:connected() then "Checking what Autopilot remembers about this game…"
			else "Waiting for the agent service…")
		if not ed:connected() then
			T.label(c, "Run this in a terminal and leave it open while you use Autopilot:", "small")
			T.selectable(c, d.SERVE_COMMAND, { name = "Command", color = C.accent })
		end
		local r = T.row(v, 6, true)
		T.button(r, "Skip", "quiet", function()
			d:closeSetup()
		end)
		return
	end
	local c = card(v)
	head(c, "Explore your game", "Recommended")
	T.label(c, FIRST_RUN_TEXT, "dim")
	local m = T.row(c, 10)
	T.label(m, "Minutes", "caps", { wrap = false })
	local minutes = T.input(m, { mono = true, width = 56, text = tostring(Settings.get(d.plugin, "explore_minutes") or 10),
		name = "ExploreMinutes" })
	minutes.TextXAlignment = Enum.TextXAlignment.Right
	minutes.FocusLost:Connect(function()
		local n = math.clamp(math.floor(tonumber(minutes.Text) or 10), 1, 600)
		minutes.Text = tostring(n)
		Settings.set(d.plugin, "explore_minutes", n)
	end)
	local foot = T.col(c, 10)
	T.rule(foot)
	local r = T.row(foot, 6, true)
	local ready = ed:connected() and d.active == nil
	local goBtn = T.button(r, "Explore and finish", "primary", function()
		local n = math.clamp(math.floor(tonumber(minutes.Text) or 10), 1, 600)
		Settings.set(d.plugin, "explore_minutes", n)
		d:exploreFromSetup(n)
	end)
	if not ready then
		T.disable(goBtn, true)
	end
	T.button(r, "Skip and finish", "quiet", function()
		d:closeSetup()
	end)
end

-- --- the page -------------------------------------------------------------------------------------------------------

function SetupPage.render(d: any, frame: Frame)
	if T.focused and T.focused:IsDescendantOf(frame) then
		-- (not built again under the caret: once the box is left)
		if not d.setupWaits then
			d.setupWaits = true
			local box = T.focused :: TextBox
			box.FocusLost:Once(function()
				d.setupWaits = false
				task.defer(function()
					if d.setupOpen then
						SetupPage.render(d, d.setupFrame)
					end
				end)
			end)
		end
		return
	end
	local s = state(d)
	if s.step == 4 and d:memoryNow() == "some" then
		d.setupOpen = false
		d:render()
		return
	end
	T.clear(frame)
	local sc = T.scroll(frame, "Scroll")
	T.list(sc, 0)
	local v = T.column(sc, MAX_WIDTH, { 20, 28, 20, 20 }, 18)
	stepper(d, v)
	if s.step == 1 then
		title(v, "Welcome to Autopilot")
		auraCard(d, v)
		keysCard(d, v)
	elseif s.step == 2 then
		buildService(d, v)
	elseif s.step == 3 then
		buildPrivacy(d, v)
	else
		buildFirstRun(d, v)
	end
end

return SetupPage
