--!strict
-- The dock's Settings page (as Godot's ui/settings_page.gd): its header (Settings, Back to runs), then at most 640 px
-- wide: the Account plate (how runs go: Aura, BYOK, signed out or not set up, from the agent service's account frame;
-- with Aura, what Autopilot billed this period and Aura's words when the team is out of credits; the agent service's
-- line; Rerun setup, Sign out, Sign in again), this place's runs and their bills, Share runs to improve Autopilot, Bug
-- detectors, the Research agent, the Design brief, Show Policy Log, and Autopilot in Claude Code & Codex (the commands
-- that set it up).
-- Uninstall isn't here: a Roblox plugin can't run programs, and the agent service takes no such request.
local M = require(script.Parent.RunModel)
local Settings = require(script.Parent.Settings)
local T = require(script.Parent.Theme)

local C = T.C

local SettingsPage = {}

local MAX_WIDTH = 640
local SHARING = "Enabling this sends recordings, screenshots, and agent traces and memories to the developers of "
	.. "Autopilot. For runs from your own Claude Code or Codex, the agent traces are its conversation during the run."
local DETECTORS = "During gameplay, Autopilot watches for potential bugs and reports them. You can enable/disable this "
	.. "per-project."
local ASSISTANTS_TITLE = "Autopilot in Claude Code & Codex"
local ASSISTANTS_INTRO = "One-time CLI setup to run Autopilot through Claude Code or Codex using your Claude or "
	.. "ChatGPT subscriptions, respectively."
local ASSISTANTS_COMMANDS = { "autopilot connect claude", "autopilot connect codex" }
local BRIEF_TEXT = "How the game is meant to work: its design, the rules players are told (e.g. “the player has three "
	.. "lives; each coin is worth 10 points”). Explorations report what differs from it as a bug. Sent with every run."
local POLICY_TEXT = "The Policy log tab: every step the agent took in the game (the action it chose, how likely each "
	.. "was, what changed). Asked of the agent service at once."
local RESEARCH = "Enabling the research agent allows Autopilot to achieve more difficult goals with fewer attempts, "
	.. "but at a higher cost."
local AGENT_NAMES = { claude = "Claude Code", codex = "Codex", opencode = "OpenCode" }
local LLM_NAMES = { anthropic = "Anthropic", openai = "OpenAI", opencode = "OpenCode Zen", openrouter = "OpenRouter",
	deepseek = "DeepSeek", google = "Gemini" }
local ACCOUNT_TAGS = { aura = { "Aura", "ok" }, own_keys = { "BYOK", "mut" }, signed_out = { "Signed out", "bad" },
	none = { "Not set up", "warn" } }

SettingsPage.AGENT_NAMES = AGENT_NAMES
SettingsPage.LLM_NAMES = LLM_NAMES

-- What the Account plate shows, from the agent service's account frame: "aura" | "own_keys" | "signed_out" | "none".
function SettingsPage.accountKind(acc: any): string
	local mode = acc.mode
	if mode == "aura" then
		return if acc.signed_in == true then "aura" else "signed_out"
	elseif mode == "own_keys" then
		return "own_keys"
	end
	return "none"
end

-- A setting's plate: its title (mono caps), what it does, then the control that changes it.
local function settingCard(parent: Instance, title: string, subtitle: string): Frame
	local v = T.plate(parent, { gap = 6, name = title })
	T.label(v, title, "caps", { wrap = false })
	T.label(v, subtitle, "small")
	local gap = Instance.new("Frame")
	gap.BackgroundTransparency = 1
	gap.Size = UDim2.new(1, 0, 0, 2)
	gap.LayoutOrder = T.nextOrder()
	gap.Parent = v
	return v
end

-- A button that changes the account: not while a run is going (it would lose its connection).
local function whileIdle(d: any, b: TextButton)
	if d.active ~= nil or d.mirror then -- (the mirror of a playtest: once it stops)
		T.disable(b, true)
	end
end

local function rerun(d: any, parent: Instance, variant: string)
	local b = T.button(parent, "Rerun setup", variant, function()
		d:openSetup()
	end, true)
	whileIdle(d, b)
end

-- The agent service's line under the account: a status dot, its state and its address.
local function serviceLine(d: any, v: Instance)
	local ed = d.editor
	local row = T.row(v, 8, true)
	if ed:connected() then
		T.dot(row, C.success)
		T.label(row, "Agent service: running", "small", { wrap = false })
		T.label(row, tostring(ed.link.url), "monoDim", { wrap = false })
	else
		T.dot(row, C.muted)
		T.label(row, "Agent service: run " .. d.SERVE_COMMAND .. " in a terminal", "small", { wrap = false,
			color = C.muted })
	end
end

local function signInInPlace(d: any, v: Instance, si: any)
	local row = T.row(v, 6, true)
	local state = si.state
	if state == "preparing" then
		local h = T.row(v, 10)
		T.spinner(h)
		T.label(h, "Starting the sign-in…", "strong", { wrap = false })
	elseif state == "waiting" then
		local h = T.row(v, 10)
		T.spinner(h)
		T.label(h, "Finish signing in in your browser…", "title", { wrap = false })
		T.label(v, "If no page opened, open this address in your browser:", "small")
		T.selectable(v, tostring(si.url or ""), { multiline = true, size = 11 })
	elseif state == "no_service" then
		T.label(v, "Signing in goes through the agent service. Run this in a terminal, then try again:", "body")
		T.selectable(v, d.SERVE_COMMAND, { name = "Command", color = C.accent })
		T.button(row, "Try again", "primary", function()
			d:beginSignIn()
		end, true)
	else
		T.label(v, tostring(si.message or ""), "body", { color = C.red })
		T.button(row, "Try again", "primary", function()
			d:beginSignIn()
		end, true)
	end
	if state == "error" then
		rerun(d, row, "default")
	else
		T.button(row, "Cancel", "quiet", function()
			d:cancelSignIn()
		end, true)
	end
end

local function accountCard(d: any, parent: Instance)
	local ed = d.editor
	local acc = ed.account or {}
	local kind = SettingsPage.accountKind(acc)
	local email = if type(acc.email) == "string" then acc.email else ""
	local confirm = kind == "aura" and d.confirmSignOut
	local v = T.plate(parent, { border = if confirm then C.redLine else C.rule, name = "Account" })
	local head = T.row(v, 8, true)
	T.label(head, "Account", "caps", { wrap = false })
	local tag = ACCOUNT_TAGS[kind] or ACCOUNT_TAGS.none
	T.tag(head, tag[1], tag[2])
	if confirm then
		T.label(v, string.format("Sign out %s on this computer?", if email ~= "" then email else "of Aura"), "title")
		T.label(v, "Every editor on this computer stops using Aura until you sign in again.", "dim")
		local b = T.row(v, 6, true)
		T.button(b, "Sign out", "dangerFill", function()
			d.confirmSignOut = false
			if ed:connected() then
				ed:signOut()
			end
			d:renderPages()
		end, true)
		T.button(b, "Cancel", "quiet", function()
			d.confirmSignOut = false
			d:renderPages()
		end, true)
		return
	end
	local si = d.signIn
	if (kind == "signed_out" or kind == "none") and si and si.state ~= "signed_in" then
		signInInPlace(d, v, si)
		return
	end
	local buttons = T.row(nil, 6, true)
	if kind == "aura" then
		if email ~= "" then
			T.label(v, email, "title")
		end
		if type(acc.bill) == "table" and type(acc.bill.usd) == "number" then
			local line = M.periodLine(acc.bill)
			local at = string.find(line, ": %$") or string.find(line, ": <") or #line
			local f = T.row(v, 4, true)
			T.label(f, string.sub(line, 1, at), "body", { wrap = false })
			T.label(f, string.sub(line, at + 2), "mono", { wrap = false, size = 13, color = C.text2 })
		end
		local credits = d:outOfCredits()
		if credits then
			T.label(v, credits.message, "body", { color = C.red })
			if credits.url ~= "" and not string.find(credits.message, credits.url, 1, true) then
				T.selectable(v, credits.url, { height = 24, size = 11 })
			end
		end
		serviceLine(d, v)
		rerun(d, buttons, "default")
		local so = T.button(buttons, "Sign out", "danger", function()
			d.confirmSignOut = true
			d:renderPages()
		end, true)
		if not ed:connected() or d.mirror then
			T.disable(so, true)
		end
	elseif kind == "own_keys" then
		T.label(v, "Your own keys", "title")
		local keys = if type(acc.keys) == "table" then acc.keys else {}
		local parts = {}
		if type(keys.decision) == "string" then
			table.insert(parts, "Decision provider " .. keys.decision)
		end
		if type(keys.llm) == "string" then
			local agent = tostring(acc.coding_agent or "opencode")
			local provider = tostring(acc.llm_provider or "anthropic")
			table.insert(parts, string.format("%s (%s) %s", AGENT_NAMES[agent] or agent, LLM_NAMES[provider] or provider,
				keys.llm))
		end
		if #parts > 0 then
			T.label(v, "Saved on this computer: " .. table.concat(parts, " · "), "mono")
		end
		serviceLine(d, v)
		rerun(d, buttons, "default")
	elseif kind == "signed_out" then
		if email ~= "" then
			T.label(v, email, "title")
		end
		T.label(v, "Your Aura sign-in on this computer ended.", "body", { color = C.red })
		serviceLine(d, v)
		whileIdle(d, T.button(buttons, "Sign in again", "primary", function()
			d:beginSignIn()
		end, true))
		rerun(d, buttons, "quiet")
	else
		T.label(v, if ed:connected() then "Choose how to run Autopilot" else "Connect to the agent service to see how "
			.. "runs go", "title")
		serviceLine(d, v)
		rerun(d, buttons, "primary")
	end
	-- (the buttons under a rule)
	local foot = T.col(v, 10)
	T.rule(foot)
	buttons.Parent = foot
	buttons.LayoutOrder = T.nextOrder()
end

-- This place's runs: "This project · N runs", what Aura billed for them, then each run, newest first.
local function runsList(d: any, parent: Instance)
	local with = {}
	for _, r in ipairs(d.runs) do
		if (type(r.usage) == "table" and #r.usage > 0) or M.billed(r.bill) >= 0 then
			table.insert(with, r)
		end
	end
	if #with == 0 then
		local e = T.plate(parent, { gap = 6, name = "Runs" })
		T.label(e, "This project", "caps", { wrap = false })
		T.label(e, "No usage yet.", "dim")
		return
	end
	local p = T.plate(parent, { pad = { 0, 0, 0, 0 }, gap = 0, name = "Runs" })
	local head = T.row(p, 10)
	T.pad(head, 14, 9, 14, 9)
	T.label(head, string.format("This project · %d run%s", #with, if #with == 1 then "" else "s"), "caps",
		{ wrap = false, fill = true })
	local total, n = 0, 0
	for _, r in ipairs(with) do
		local b = M.billed(r.bill)
		if b >= 0 then
			total += b
			n += 1
		end
	end
	if n > 0 then
		T.label(head, "Billed by Aura", "body", { wrap = false, size = 12, color = C.text })
		T.label(head, M.money(total), "mono", { wrap = false, size = 12, color = C.text })
	end
	T.rule(p)
	local body = T.col(p, 0)
	T.pad(body, 8, 4, 8, 8)
	local function gridRow(a: string, b: string, c: string, title: boolean, billedNow: boolean?)
		local h = T.row(body, 8)
		T.pad(h, 8, if title then 6 else 7, 8, if title then 6 else 7)
		local style = if title then "caps" else "mono"
		local l1 = T.label(h, a, style, { wrap = false, truncate = true })
		l1.Size = UDim2.fromOffset(100, 16)
		local l2 = T.label(h, b, if title then "caps" else "body", { wrap = false, fill = true, size = if title then nil
			else 13, color = if title then nil else C.text })
		local _ = l2
		local l3 = T.label(h, c, style, { wrap = false, truncate = true, align = "right",
			color = if title then nil elseif billedNow then C.text else C.muted })
		l3.Size = UDim2.fromOffset(90, 16)
	end
	gridRow("Time", "Prompt", "Billed", true)
	for _, r in ipairs(with) do
		local b = M.billed(r.bill)
		gridRow(M.when(r.started), r:shownPrompt(), if b >= 0 then M.money(b) else "—", false, b >= 0)
	end
end

function SettingsPage.render(d: any, frame: Frame)
	if T.focused and T.focused:IsDescendantOf(frame) then
		-- (built again once the box is left)
		if not d.settingsDirty then
			d.settingsDirty = true
			local box = T.focused :: TextBox
			box.FocusLost:Once(function()
				task.defer(function()
					if d.settingsDirty and d.page == "settings" then
						SettingsPage.render(d, frame)
					end
				end)
			end)
		end
		return
	end
	d.settingsDirty = false
	local keep = if frame:FindFirstChild("Scroll") then (frame:FindFirstChild("Scroll") :: ScrollingFrame).CanvasPosition
		else nil
	T.clear(frame)
	if frame:FindFirstChildOfClass("UIListLayout") == nil then
		T.list(frame, 0)
	end
	local _, hi = T.bar(frame, { pad = { 16, 12, 16, 12 }, horizontal = true, name = "Head" })
	T.label(hi, "Settings", "title", { wrap = false, fill = true })
	T.button(hi, "Back to runs", "quiet", function()
		d.settingsOpen = false
		d:render()
	end, true)
	local sc = T.scroll(frame, "Scroll")
	sc.Size = UDim2.new(1, 0, 0, 0)
	T.fill(sc)
	T.list(sc, 0)
	local v = T.column(sc, MAX_WIDTH, { 20, 18, 20, 18 }, 12)
	accountCard(d, v)
	runsList(d, v)
	-- Share runs to improve Autopilot (the agent service's preferences.json share_data)
	local share = settingCard(v, "Share runs to improve Autopilot", SHARING)
	T.switch(share, Settings.get(d.plugin, "share") ~= false, "Data sharing on", "Data sharing off", function(on)
		if not d.editor:setSharing(on) then
			SettingsPage.render(d, frame) -- (not saved: the switch shows the setting as it is)
		end
	end, { disabled = d.mirror })
	-- Research agent: the hello's `research`, once flipped (until then the switch shows the agent service's own
	-- default, on)
	local chosen = Settings.get(d.plugin, "research")
	local research = if type(chosen) == "boolean" then chosen else true
	local rc = settingCard(v, "Research agent", RESEARCH .. " Applies from the next run.")
	T.switch(rc, research, "Research agent on", "Research agent off", function()
		Settings.set(d.plugin, "research", not research)
		d.editor:helloChanged()
		research = not research
	end, { disabled = d.mirror })
	-- Bug detectors: this project's (memory_state's "detectors", sent as {"type": "detectors"}); as last told, and not
	-- changed, while not connected. (After Research agent, as in the other plugins.)
	local dc = settingCard(v, "Bug detectors", DETECTORS)
	T.switch(dc, d.editor.detectors ~= false, "Bug detectors on", "Bug detectors off", function(on)
		if not d.editor:setDetectors(on) then
			SettingsPage.render(d, frame) -- (not sent: the switch shows the setting as it is)
		end
	end, { disabled = d.mirror or not d.editor:connected() })
	-- Design brief: sent with every run the dock starts (the start's `design_brief`)
	local bc = settingCard(v, "Design brief", BRIEF_TEXT)
	local brief = T.input(bc, { multiline = true, height = 84, name = "DesignBrief", placeholder = "Optional",
		text = Settings.get(d.plugin, "design_brief") or "" })
	brief.TextEditable = not d.mirror
	brief:GetPropertyChangedSignal("Text"):Connect(function()
		Settings.set(d.plugin, "design_brief", brief.Text)
	end)
	-- Show Policy Log: the Policy log tab, and the hello's `inspect`
	local policy = Settings.get(d.plugin, "show_policy_log") ~= false
	local pc = settingCard(v, "Show Policy Log", POLICY_TEXT)
	T.switch(pc, policy, "Policy log shown", "Policy log hidden", function()
		Settings.set(d.plugin, "show_policy_log", not policy)
		d.editor:helloChanged()
		policy = not policy
	end, { disabled = d.mirror })
	-- Autopilot in Claude Code & Codex: the commands that set it up (to select and copy)
	local ac = settingCard(v, ASSISTANTS_TITLE, ASSISTANTS_INTRO)
	for _, command in ipairs(ASSISTANTS_COMMANDS) do
		local s = T.selectable(ac, command, { height = 28, color = C.text })
		local _ = s
	end
	if keep then
		task.defer(function()
			sc.CanvasPosition = keep
		end)
	end
end

return SettingsPage
