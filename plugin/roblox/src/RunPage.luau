--!strict
-- The dock's run page (as Godot's): its tab bar (Live or Result, Feed, Policy log · N, and at the right the run's id
-- line), the Live page (the request, Elapsed and Stop; the stage track, what the agent does now, the harness attempts,
-- the feed's latest lines; the steer box at its foot), the Result (the verdict and its reason, the recording, the
-- evidence, what was changed directly, the findings) and the Feed tab (the run as a transcript, a heading per stage,
-- each activity line's details folded under it).
local Selection = game:GetService("Selection")

local Frame = require(script.Parent.Frame)
local M = require(script.Parent.RunModel)
local PolicyLog = require(script.Parent.PolicyLog)
local Settings = require(script.Parent.Settings)
local T = require(script.Parent.Theme)

local C = T.C

local RunPage = {}
RunPage.__index = RunPage

local FEED_TAIL = 10 -- the Live page's feed: its latest lines (the Feed tab shows them all)
local FEED_ROWS = 600 -- the Feed tab's rows drawn at most (the latest)
local STEER_HINT = "Send a steering prompt to Autopilot"
local STEER_WAIT = "You can steer once the game is playing"
local HANDS_OFF = "Hands off the game view while Autopilot plays: your clicks and keys there reach the game too."

function RunPage.new(d: any, frame: Frame): any
	local self = setmetatable({}, RunPage) :: any
	self.d = d
	self.frame = frame
	T.list(frame, 0)
	-- the tab bar
	local _, th = T.bar(frame, { pad = { 16, 0, 16, 0 }, horizontal = true, gap = 20, name = "Tabs" })
	self.tabInner = th
	self.tabMain = self:_tab(th, "Result", "main")
	self.tabFeed = self:_tab(th, "Feed", "feed")
	self.tabPolicy = self:_tab(th, "Policy log", "policy")
	self.tabMeta = T.label(th, "", "caps", { wrap = false, fill = true, align = "right" })
	self.tabLocal = T.label(th, "● Local agent", "caps", { wrap = false, color = C.success })
	local area = Instance.new("Frame")
	area.Name = "Area"
	area.BackgroundTransparency = 1
	area.Size = UDim2.new(1, 0, 0, 0)
	area.LayoutOrder = T.nextOrder()
	area.ClipsDescendants = true
	T.fill(area)
	area.Parent = frame
	self.area = area
	self:_buildLive()
	self:_buildFeed()
	self.result = T.scroll(area, "Result")
	self.result.Visible = false
	local pf = Instance.new("Frame")
	pf.Name = "Policy"
	pf.BackgroundTransparency = 1
	pf.Size = UDim2.fromScale(1, 1)
	pf.Visible = false
	pf.Parent = area
	self.policyFrame = pf
	self.policy = PolicyLog.new(d, pf)
	return self
end

function RunPage:_tab(parent: Instance, text: string, key: string): TextButton
	local b = Instance.new("TextButton")
	b.Name = "Tab" .. key
	b.Text = text
	b.AutoButtonColor = false
	b.BackgroundTransparency = 1
	b.FontFace = T.F.medium
	b.TextSize = 13
	b.TextColor3 = C.muted
	b.Size = UDim2.new(0, 0, 0, 40)
	b.AutomaticSize = Enum.AutomaticSize.X
	b.LayoutOrder = T.nextOrder()
	local u = Instance.new("Frame")
	u.Name = "Underline"
	u.BorderSizePixel = 0
	u.BackgroundColor3 = C.accent
	u.AnchorPoint = Vector2.new(0, 1)
	u.Position = UDim2.new(0, 0, 1, 0)
	u.Size = UDim2.new(1, 0, 0, 2)
	u.Visible = false
	u.Parent = b
	b.MouseEnter:Connect(function()
		if not u.Visible then
			b.TextColor3 = C.text
		end
	end)
	b.MouseLeave:Connect(function()
		if not u.Visible then
			b.TextColor3 = C.muted
		end
	end)
	b.MouseButton1Click:Connect(function()
		if self.d.tab ~= key then
			self.d:setTab(key)
		end
	end)
	b.Parent = parent
	return b
end

-- --- the Live page --------------------------------------------------------------------------------------------------

function RunPage:_buildLive()
	local live = Instance.new("Frame")
	live.Name = "Live"
	live.BackgroundTransparency = 1
	live.Size = UDim2.fromScale(1, 1)
	live.Parent = self.area
	T.list(live, 0)
	self.live = live
	local _, hh = T.bar(live, { pad = { 16, 12, 16, 12 }, horizontal = true, gap = 12, name = "Head" })
	T.label(hh, "● Live", "caps", { wrap = false, color = C.accent })
	local pv = T.col(hh, 0)
	pv.Size = UDim2.new(0, 0, 0, 0)
	T.fill(pv)
	self.lPrompt = T.label(pv, "", "title", { wrap = false, truncate = true })
	self.lPrompt.Size = UDim2.new(1, 0, 0, 20)
	self.lOrigin = T.label(pv, "", "small", { wrap = false, truncate = true })
	self.lOrigin.Size = UDim2.new(1, 0, 0, 17)
	self.lElapsedCap = T.label(hh, "Elapsed", "caps", { wrap = false })
	self.lElapsed = T.label(hh, "00:00", "monoBig", { wrap = false })
	self.lStop = T.button(hh, "Stop", "default", function()
		self.d:stopRun()
	end)
	local ls = T.scroll(live, "Body")
	ls.Size = UDim2.new(1, 0, 0, 0)
	T.fill(ls)
	T.list(ls, 0)
	local left = T.col(ls, 16, "Left")
	T.pad(left, 16, 18, 16, 18)
	self.lTrack = T.col(left, 8, "Track")
	-- now: what the agent is doing, in a plate
	local nv = T.plate(left, { pad = { 11, 9, 11, 9 }, gap = 3, name = "Now" })
	self.lNowTitle = T.label(nv, "Now", "caps", { wrap = false })
	local nh = T.row(nv, 8)
	T.dot(nh, C.accent)
	self.lNow = T.label(nh, "", "strong", { size = 15 })
	self.lNow.Size = UDim2.new(1, -14, 0, 0)
	self.lHands = T.label(left, HANDS_OFF, "small", { color = C.muted })
	-- the harness attempts: a square each (filled when finished, outlined the current one), their count, the steps
	local ah = T.row(left, 4, false, "Attempts")
	T.label(ah, "Harness attempts", "caps", { wrap = false })
	local sq = T.row(ah, 4, false, "Squares")
	sq.Size = UDim2.new(0, 0, 0, 14)
	sq.AutomaticSize = Enum.AutomaticSize.X
	T.pad(sq, 8, 0, 6, 0)
	self.lSquares = sq
	self.lCount = T.label(ah, "0", "mono", { wrap = false, font = T.F.monoMedium, size = 13, color = C.text })
	self.lSteps = T.label(ah, "", "monoDim", { wrap = false, fill = true, align = "right" })
	-- the feed, in a plate under its caption
	local fp = T.plate(left, { pad = { 0, 0, 0, 0 }, gap = 0, name = "Feed" })
	local fc = T.col(fp, 0)
	T.pad(fc, 11, 9, 11, 9)
	T.label(fc, "Feed", "strong", { size = 12, wrap = false })
	T.rule(fp)
	local lines = T.col(fp, 6, "Lines")
	T.pad(lines, 11, 8, 11, 10)
	self.lFeed = lines
	self:_buildSteer()
end

function RunPage:_buildSteer()
	local row, inner = T.bar(self.live, { side = "top", pad = { 16, 10, 16, 12 }, name = "Steer" })
	local well = T.frame(inner, "Well", C.well)
	T.corner(well, T.R_PLATE)
	T.stroke(well, C.line)
	T.pad(well, 12, 6, 6, 6)
	T.list(well, 8, true)
	self.steerBox = T.input(well, { bare = true, fill = true, placeholder = STEER_HINT, name = "SteerBox" })
	self.steerBox:GetPropertyChangedSignal("Text"):Connect(function()
		self:_updateSteer()
	end)
	self.steerBox.FocusLost:Connect(function(enter)
		if enter then
			self:_onSteer()
		end
	end)
	self.steerBtn = T.button(well, "Steer", "primary", function()
		self:_onSteer()
	end)
	self.steerNote = T.label(inner, "", "small", { color = C.muted })
	self.steerRow = row
	row.Visible = false
end

function RunPage:_canSteerShown(): boolean
	local d = self.d
	local a = d.active
	return a ~= nil and d.shown == a and a:live() and d.editor:canSteer()
end

function RunPage:_canSteerNow(): boolean
	return self:_canSteerShown() and self.d.active:steerable()
end

function RunPage:_updateSteer()
	local a = self.d.active
	local stopping = a ~= nil and a.stopAsked
	local editable = self:_canSteerShown() and not stopping
	self.steerBox.TextEditable = editable
	self.steerRow.BackgroundTransparency = 0
	local now = self:_canSteerNow() and not stopping
	T.disable(self.steerBtn, not now or M.trim(self.steerBox.Text) == "")
	self.steerNote.Text = if not now and not stopping and M.trim(self.steerBox.Text) ~= "" then STEER_WAIT else ""
	self.steerNote.Visible = self.steerNote.Text ~= ""
end

function RunPage:_onSteer()
	local said = M.trim(self.steerBox.Text)
	if said == "" or not self:_canSteerNow() then
		return
	end
	self.d.editor:steer(said)
	self.d.active:addFeed("You: " .. said, "status")
	self.steerBox.Text = ""
	self:_updateSteer()
	self:liveChanged()
end

-- The stage track: a 2 px progress rule (the accent up to the middle of the current stage), each stage's name and time
-- under it in a column of its own (done in TEXT2, the current one in the accent, the next ones muted); in a dock too
-- narrow for the names' columns, the names follow each other in rows.
function RunPage:_renderTrack(r: any)
	local tr = self.lTrack
	local done = not r:live()
	local n = #M.STAGES
	local times = r:stageTimes()
	local width = self.d:mainWidth() - 32
	-- (drawn again only when something shown changed: a stage, an attempt, a second, the width)
	local key = { r.id, r.stage, r.attempts, if done then 1 else 0, math.floor(width) }
	for i = 0, n - 1 do
		table.insert(key, if times[i] then math.floor(times[i]) else -1)
	end
	local k = table.concat(key :: { any }, ",")
	if k == self.trackKey and #tr:GetChildren() > 1 then
		return
	end
	self.trackKey = k
	T.clear(tr)
	local bar = T.frame(tr, "Bar", C.rule)
	bar.AutomaticSize = Enum.AutomaticSize.None
	bar.Size = UDim2.new(1, 0, 0, 2)
	local prog = Instance.new("Frame")
	prog.BorderSizePixel = 0
	prog.BackgroundColor3 = C.accent
	prog.Size = UDim2.new(if done then 1 else math.clamp((r.stage + 0.5) / n, 0, 1), 0, 1, 0)
	prog.Parent = bar
	local col = width / n - 4
	local columns = col >= 7 * 6.8 + 2
	local function name(i: number): string
		local s = string.upper(M.STAGES[i + 1])
		if i == M.STAGE_ROLLOUT and r.attempts > 0 then
			s ..= " · H" .. tostring(r.attempts)
		end
		return s
	end
	local function colorOf(i: number): Color3
		local now = i == r.stage and not done
		return if (i < r.stage or (done and i <= r.stage)) then C.text2 elseif now then C.accent else C.muted
	end
	if columns then
		local row = T.frame(tr, "Names")
		T.list(row, 0, true).VerticalAlignment = Enum.VerticalAlignment.Top
		for i = 0, n - 1 do
			local c = T.col(row, 2)
			c.Size = UDim2.new(1 / n, 0, 0, 0)
			local now = i == r.stage and not done
			local l = T.label(c, name(i), "caps", { color = colorOf(i), font = if now then T.F.monoMedium else nil })
			l.Size = UDim2.new(1, -4, 0, 0)
			if times[i] then
				T.label(c, M.shortClock(times[i]), "monoDim", { wrap = false, color = if now then C.accent else C.muted })
			end
		end
	else
		local row = T.row(tr, 12, true, "Names")
		for i = 0, n - 1 do
			local text = T.rt(name(i), colorOf(i))
			if times[i] then
				text ..= "  " .. T.rt(M.shortClock(times[i]), if i == r.stage and not done then C.accent else C.muted)
			end
			T.label(row, text, "caps", { wrap = false, rich = true })
		end
	end
end

-- The harness attempts as squares: the finished ones filled, the current one (a live run's) outlined, as many as fit.
function RunPage:_renderAttempts(r: any)
	local sq = self.lSquares
	T.clear(sq)
	local live = r:live()
	local finished = if live and r.attempts > 0 then r.attempts - 1 else r:attemptCount()
	local current = live and r.attempts > 0
	local total = finished + (if current then 1 else 0)
	local room = math.max(1, math.floor((self.d:mainWidth() - 32 - 260) / 14))
	local shown = math.min(total, 24, room)
	local filled = math.max(0, shown - (if current then 1 else 0))
	for i = 1, shown do
		local f = Instance.new("Frame")
		f.BorderSizePixel = 0
		f.LayoutOrder = i
		if i <= filled then
			f.Size = UDim2.fromOffset(10, 10)
			f.BackgroundColor3 = C.accent
		else
			f.Size = UDim2.fromOffset(6, 6)
			f.BackgroundTransparency = 1
			T.stroke(f, C.accent, 2)
		end
		f.Parent = sq
	end
	sq.Visible = shown > 0
	self.lCount.Text = tostring(total)
	local n = #r.steps
	self.lSteps.Text = if n > 0 then string.format("%d policy step%s", n, if n == 1 then "" else "s") else ""
end

-- The stage lines' colors and the rest, as Godot's feed: a line as rich text (mono), its time first.
local function tagText(word: string, kind: string): string
	local c = T.TAGS[kind] or T.TAGS.mut
	return '<mark color="' .. T.hex(c[1]) .. '"><font color="' .. T.hex(c[2]) .. '"> ' .. string.upper(word)
		.. " </font></mark>  "
end

-- A feed line: its time, then its words. (No icon before an activity line's words, as the other docks have: a Roblox
-- plugin can only draw a picture of its own in the edit session, and the dock is drawn in the playtest's client while
-- a run plays.)
function RunPage.feedLine(e: any, current: boolean?): string
	local t = M.shortClock(tonumber(e.t) or 0)
	local s = T.rt(t .. string.rep(" ", math.max(1, 7 - #t)), C.muted)
	local text = tostring(e.text or "")
	if e.kind == "activity" then
		s ..= T.rt(text, C.text)
		local quiet = ""
		local detail = tostring(e.detail or "")
		if detail ~= "" and not M.folds(detail) and not string.find(text, detail, 1, true) then
			quiet ..= "  " .. detail
		end
		if tostring(e.result or "") ~= "" then
			quiet ..= "  " .. tostring(e.result)
		end
		if quiet ~= "" then
			s ..= T.rt(quiet, C.muted)
		end
		return s
	end
	local color, bold = C.text3, false
	if e.kind == "attempt" then
		color = if current then C.accent else C.text
		bold = true
	elseif e.kind == "engine" then
		s ..= tagText("Engine", e.tone or "warn")
		color = C.text2
	elseif e.kind == "agent" then
		s ..= tagText("Agent", "bad")
		color = C.text2
	elseif e.kind == "memory" then
		s ..= tagText("Memory", "ok")
		color = C.success
	elseif e.kind == "info" then
		color = C.text
	elseif e.kind == "now" then
		color = C.text2
	elseif string.sub(text, 1, 5) == "You: " then
		color = C.text
		bold = true
	elseif M.STAGE_LINES[text] then
		color = C.text
	end
	local body = T.rt(text, color)
	return s .. (if bold then "<b>" .. body .. "</b>" else body)
end

local function currentAttemptLine(r: any): number
	if not r:live() then
		return -1
	end
	for i = #r.feed, 1, -1 do
		if r.feed[i].kind == "attempt" then
			return i
		end
	end
	return -1
end

local function latestLine(r: any): string
	for i = #r.feed, 1, -1 do
		if r.feed[i].kind ~= "engine" then
			return r.feed[i].text
		end
	end
	return "Starting…"
end

function RunPage:_nowLine(r: any): string
	if r:live() and r.stage <= 1 and self.d:waiting() then
		return self.d.WAITING_FOR_GAME
	end
	return if r.nowText ~= "" then r.nowText else latestLine(r)
end

function RunPage:_renderLive()
	local d = self.d
	local r = d.shown
	self.lPrompt.Text = r:shownPrompt()
	self.lOrigin.Text = r:originLine()
	self.lOrigin.Visible = self.lOrigin.Text ~= ""
	T.disable(self.lStop, r.stopAsked or d.active ~= r or not d.editor:connected())
	self.lStop.Text = T.text(if r.stopAsked then "Stopping…" else "Stop", T.F.medium)
	self.steerRow.Visible = self:_canSteerShown()
	self:_updateSteer()
	self:tick()
	self.lNowTitle.Text = if r.attempts > 0 then string.format("NOW · HARNESS %d", r.attempts) else "NOW"
	self:_renderAttempts(r)
	T.clear(self.lFeed)
	local cur = currentAttemptLine(r)
	local start = math.max(1, #r.feed - FEED_TAIL + 1)
	for i = start, #r.feed do
		T.label(self.lFeed, RunPage.feedLine(r.feed[i], i == cur), "mono", { rich = true })
	end
	if #r.feed == 0 then
		T.label(self.lFeed, "Nothing yet.", "small", { color = C.muted })
	end
end

-- Every quarter second while the run shown goes on: its clock, its stage track, its now line.
function RunPage:tick()
	local d = self.d
	local r = d.shown
	if r == nil or not self.live.Visible then
		return
	end
	self.lElapsed.Text = M.clock(r:elapsed())
	local roomy = d:mainWidth() >= 520
	self.lElapsed.Visible = roomy
	self.lElapsedCap.Visible = roomy
	self:_renderTrack(r)
	local now = self:_nowLine(r)
	if self.lNow.Text ~= now then
		self.lNow.Text = now
	end
	self.lNow.TextColor3 = if now == d.WAITING_FOR_GAME then C.amber else C.text
end

-- --- the Feed tab ---------------------------------------------------------------------------------------------------

function RunPage:_buildFeed()
	local fa = Instance.new("Frame")
	fa.Name = "FeedArea"
	fa.BackgroundTransparency = 1
	fa.Size = UDim2.fromScale(1, 1)
	fa.Visible = false
	fa.Parent = self.area
	T.list(fa, 0)
	self.feedArea = fa
	local holder = Instance.new("Frame")
	holder.Name = "Holder"
	holder.BackgroundTransparency = 1
	holder.Size = UDim2.new(1, 0, 0, 0)
	holder.LayoutOrder = 1
	T.fill(holder)
	T.pad(holder, 16, 16, 16, 16)
	holder.Parent = fa
	self.feedEmpty = T.label(holder, "Nothing yet.", "small", { color = C.muted })
	local plate = Instance.new("Frame")
	plate.Name = "Plate"
	plate.BackgroundColor3 = C.plate
	plate.BorderSizePixel = 0
	plate.Size = UDim2.fromScale(1, 1)
	T.corner(plate, T.R_PLATE)
	T.stroke(plate, C.rule)
	plate.Parent = holder
	self.feedPlate = plate
	local sc = T.scroll(plate, "Rows")
	T.pad(sc, 11, 6, 11, 8)
	T.list(sc, 0)
	self.feedRows = sc
	self.feedSteerSlot = T.col(fa, 0, "SteerSlot")
	self.feedSteerSlot.LayoutOrder = 2
	self.openFolds = {}
end

function RunPage:_atBottom(): boolean
	local sc = self.feedRows
	return sc.CanvasPosition.Y + sc.AbsoluteWindowSize.Y >= sc.AbsoluteCanvasSize.Y - 12
end

function RunPage:_toBottom()
	local sc = self.feedRows
	task.defer(function()
		sc.CanvasPosition = Vector2.new(0, math.max(0, sc.AbsoluteCanvasSize.Y - sc.AbsoluteWindowSize.Y))
	end)
end

function RunPage:_heading(stage: number): any
	local h = T.row(self.feedRows, 8)
	T.pad(h, 0, 14, 0, 4)
	T.label(h, M.STAGE_HEADINGS[stage] or "", "caps", { wrap = false, color = C.text2 })
	local count = T.label(h, "0", "monoDim", { wrap = false })
	local rule = T.rule(h)
	rule.Size = UDim2.new(0, 0, 0, 1)
	T.fill(rule)
	return count
end

-- A fold under an activity line: "+ inspect the code" and, open, the detail in a read-only box.
function RunPage:_fold(parent: Instance, e: any, label: string, detail: string, result: boolean)
	local key = tostring(e.aid or 0) .. ":" .. (if result then "r" else "c")
	local v = T.col(parent, 4)
	local b = Instance.new("TextButton")
	b.BackgroundTransparency = 1
	b.AutoButtonColor = false
	b.FontFace = T.F.mono
	b.TextSize = 11
	b.TextColor3 = C.muted
	b.TextXAlignment = Enum.TextXAlignment.Left
	b.Size = UDim2.new(1, 0, 0, 18)
	b.LayoutOrder = T.nextOrder()
	T.pad(b, 44, 0, 0, 0)
	b.Parent = v
	local box = T.selectable(v, M.shownDetail(detail), { multiline = true, size = 11, radius = 4 })
	local indent = Instance.new("UISizeConstraint")
	indent.MaxSize = Vector2.new(math.huge, 320)
	indent.Parent = box
	local wrap = T.col(v, 0)
	T.pad(wrap, 44, 0, 0, 2)
	box.Parent = wrap
	local function show()
		local open = self.openFolds[key] == true
		b.Text = M.foldLabel(label, open)
		wrap.Visible = open
	end
	show()
	b.MouseEnter:Connect(function()
		b.TextColor3 = C.text
	end)
	b.MouseLeave:Connect(function()
		b.TextColor3 = C.muted
	end)
	b.MouseButton1Click:Connect(function()
		self.openFolds[key] = not self.openFolds[key]
		show()
	end)
end

function RunPage:_feedRow(e: any, current: boolean): Frame
	local row = T.col(self.feedRows, 0)
	T.pad(row, 0, 3, 0, 3)
	T.label(row, RunPage.feedLine(e, current), "mono", { rich = true })
	if e.kind == "activity" then
		local detail = tostring(e.detail or "")
		if M.folds(detail) then
			self:_fold(row, e, M.DETAIL_LABELS[tostring(e.akind or "")] or "the whole message", detail, false)
		end
		local rd = tostring(e.rdetail or "")
		if M.folds(rd) then
			self:_fold(row, e, M.RESULT_LABELS[tostring(e.akind or "")] or "what came back", rd, true)
		end
	end
	return row
end

function RunPage:_appendFeed(e: any, current: boolean)
	local stage = tonumber(e.stage) or -1
	if stage >= 0 and stage ~= self.feedGroup then
		self.feedGroup = stage
		self.feedHeading = self:_heading(stage)
	end
	if self.feedHeading then
		self.feedHeading.Text = tostring((tonumber(self.feedHeading.Text) or 0) + 1)
	end
	local row = self:_feedRow(e, current)
	if e.aid then
		self.feedById[e.aid] = row
	end
end

-- The Feed tab drawn whole.
function RunPage:_renderFeed()
	local r = self.d.shown
	T.clear(self.feedRows)
	self.feedById = {}
	self.feedGroup = -1
	self.feedHeading = nil
	self.feedEmpty.Visible = #r.feed == 0
	self.feedPlate.Visible = #r.feed > 0
	local cur = currentAttemptLine(r)
	for i = math.max(1, #r.feed - FEED_ROWS + 1), #r.feed do
		self:_appendFeed(r.feed[i], i == cur)
	end
	self.feedTotal = r.feedTotal or #r.feed
	self.feedRun = r
	if r:live() then
		self:_toBottom()
	end
end

-- --- the Result -----------------------------------------------------------------------------------------------------

function RunPage:_renderResult()
	local d = self.d
	local r = d.shown
	local sc = self.result
	T.clear(sc)
	if sc:FindFirstChildOfClass("UIListLayout") == nil then
		T.list(sc, 0)
	end
	local pad = if d.wide then 16 else 12
	local v = T.col(sc, if d.wide then 14 else 12, "Result")
	T.pad(v, pad, pad, pad, pad)
	-- the run's id, its actions at the right of the same row
	local idrow = T.row(v, 6)
	T.label(idrow, string.format("RUN %s // %s // %s", r.id, r:kindWord(), string.upper(M.when(r.started))), "caps",
		{ wrap = false, fill = true })
	local again = T.button(idrow, "Run again", "primary", function()
		d:runAgain(r)
	end, true)
	T.grey(again, d.active ~= nil or not d.editor:connected())
	T.button(idrow, if self.showReport then "Hide report" else "Report", "default", function()
		self.showReport = not self.showReport
		self:_renderResult()
	end, true)
	if self.showReport then
		-- (Studio gives a plugin no clipboard: the report to select and copy)
		local rb = T.col(v, 6)
		T.label(rb, "Report · markdown", "caps", { wrap = false })
		T.selectable(rb, M.reportMarkdown(r), { multiline = true, size = 11, radius = T.R_PLATE })
	end
	T.label(v, r:shownPrompt(), "title")
	if r:originLine() ~= "" then
		T.label(v, r:originLine(), "small")
	end
	-- what the user must know to trust the outcome or to get a better one, before the verdict
	for _, a in ipairs(r:alerts()) do
		local box = T.frame(v, "Alert", C.amberFill)
		T.stroke(box, C.amberLine)
		T.pad(box, 11, 9, 11, 9)
		T.label(box, M.scrub(a), "strong", { color = C.amberFg })
	end
	local verdict = r:verdict()
	local vc = T.verdictColors(verdict)
	local vr = T.col(v, 8)
	local banner = T.frame(vr, "Verdict", vc[1])
	banner.Size = UDim2.new(0, 0, 0, 0)
	banner.AutomaticSize = Enum.AutomaticSize.XY
	T.pad(banner, 10, 3, 10, 3)
	local vl = T.label(banner, verdict, "title", { wrap = false, font = T.F.semibold, size = 16, color = vc[2] })
	vl.Size = UDim2.new(0, 0, 0, 22)
	local reason = r:reason()
	if reason == "" then
		reason = if verdict == "STOPPED" then "Stopped before a result."
			elseif verdict == "NO RESULT" then "The connection to the agent service closed before the result came."
			else ""
	end
	if reason ~= "" then
		T.label(vr, M.scrub(reason), "body")
	end
	-- the run's recording: where it is (Studio can't play it in the dock); without one, why not
	local rec = r:recording()
	if rec then
		local rb = T.col(v, 6)
		local cap = "Recording"
		if rec.seconds > 0 then
			cap ..= " · " .. M.clock(rec.seconds)
		end
		if rec.episode then
			cap ..= " · H" .. tostring(rec.episode)
		end
		T.label(rb, cap, "caps", { wrap = false })
		if rec.video ~= "" then
			T.selectable(rb, rec.video, { height = 28 })
		end
		if rec.frames ~= "" then
			T.label(rb, string.format("Frames%s: %s", if rec.fps > 0 then string.format(" at %g fps", rec.fps) else "",
				rec.frames), "monoDim")
		end
	elseif r:noRecordingLine() ~= "" then
		T.label(v, r:noRecordingLine(), "small", { color = C.muted })
	end
	local ev = r:evidence()
	if #ev > 0 then
		local eb = T.col(v, 6)
		T.label(eb, "Evidence", "caps", { wrap = false })
		local lines = {}
		for _, e in ipairs(ev) do
			local line = M.scrub(e)
			local at, rest = string.match(line, "^(H%d+%s+%d[%d:.]*%s*s?)%s+(.*)$")
			if at then
				table.insert(lines, T.rt(at, C.accent) .. "  " .. T.rt(rest, C.text2))
			else
				table.insert(lines, T.rt(line, C.text2))
			end
		end
		self:_codeBox(eb, table.concat(lines, "\n"))
	end
	local ch = r:changes()
	if #ch > 0 then
		local cb = T.col(v, 6)
		T.label(cb, string.format(if d.wide then "Changed directly · not through play · %d" else "Changed directly · %d",
			#ch), "caps", { wrap = false, color = C.amber })
		local lines = {}
		for _, c in ipairs(ch) do
			table.insert(lines, T.rt("! ", C.amber) .. T.rt(M.scrub(c), C.text2))
		end
		self:_codeBox(cb, table.concat(lines, "\n"))
	end
	local agent = r:agentFindings()
	local engine = r:engineFindings()
	if #agent > 0 or #engine > 0 then
		local fv = T.col(v, 8)
		for _, a in ipairs(agent) do
			self:_agentCard(fv, M.scrub(a))
		end
		if #engine > 0 then
			T.label(fv, string.format("Engine findings · %d", #engine), "caps", { wrap = false })
			for _, f in ipairs(engine) do
				self:_engineCard(fv, r, f)
			end
		end
	end
end

-- A code box (evidence, what was changed): the well plate, mono, lines apart.
function RunPage:_codeBox(parent: Instance, rich: string)
	local well = T.plate(parent, { bg = C.well, pad = { 11, 9, 11, 9 }, gap = 0, name = "Well" })
	local l = T.label(well, rich, "mono", { rich = true, size = if self.d.wide then 12 else 11, color = C.text2 })
	l.LineHeight = 1.25
end

function RunPage:_agentCard(parent: Instance, text: string)
	local v = T.plate(parent, { pad = { 12, 11, 12, 11 }, gap = 6, name = "AgentFinding" })
	local h = T.row(v, 8)
	T.tag(h, "Found", "bad")
	T.label(h, "Agent finding", "caps", { wrap = false })
	T.label(v, text, "strong")
	local b = T.row(v, 6, true)
	T.button(b, "Verify a fix", "default", function()
		self.d:compose("Verify that this is fixed: " .. text)
	end, true)
end

-- The nearest part to a place in the kit frame (cm), in the place open.
local function nearest(w: any): Instance?
	local ok, p = pcall(Frame.fromKit, w)
	if not ok then
		return nil
	end
	local best, bestD = nil, math.huge
	for _, d in ipairs(workspace:GetDescendants()) do
		if d:IsA("BasePart") and not d:IsA("Terrain") then
			local dist = ((d :: BasePart).Position - p).Magnitude
			if dist < bestD then
				best, bestD = d, dist
			end
		end
	end
	return best
end

function RunPage:_engineCard(parent: Instance, r: any, f: any)
	local d = self.d
	local v = T.plate(parent, { pad = { 12, 11, 12, 11 }, gap = 6, name = "EngineFinding" })
	local h = T.row(v, 8)
	local sev = M.trim(f.severity)
	T.tag(h, if sev ~= "" then sev else "info", T.severityKind(sev))
	T.label(h, tostring(f.detector or f.source or "engine"), "monoDim", { wrap = false, fill = true })
	local count = tonumber(f.count) or 1
	if count > 1 then
		T.label(h, string.format("×%d", count), "mono", { wrap = false, font = T.F.monoMedium, color = C.text })
	end
	T.label(v, M.scrub(tostring(f.summary or "")), "strong")
	local loc = {}
	if type(f.where) == "table" then
		table.insert(loc, "at " .. M.whereText(f.where) .. " cm")
	end
	if tostring(f.actor or "") ~= "" then
		table.insert(loc, tostring(f.actor))
	end
	if type(f.t) == "number" then
		table.insert(loc, string.format("t=%.1fs", f.t))
	end
	if #loc > 0 then
		T.label(v, table.concat(loc, " · "), "monoDim")
	end
	local b = T.row(v, 6, true)
	if type(f.where) == "table" or tostring(f.actor or "") ~= "" then
		T.button(b, "Show in level", "default", function()
			local actor = tostring(f.actor or "")
			local found = if actor ~= "" then workspace:FindFirstChild(actor, true) else nil
			local near = false
			if found == nil and type(f.where) == "table" then
				found = nearest(f.where)
				near = found ~= nil
			end
			if found == nil then
				d:status("Nothing in the place near that")
				return
			end
			Selection:Set({ found })
			d:status("Showing " .. found.Name .. (if near then " (the nearest part to the finding)" else ""))
		end, true)
	end
	local sig = tostring(f.signature or "")
	if sig ~= "" then
		if r.notABug[sig] then
			T.label(b, "Marked not a bug", "small", { wrap = false, color = C.muted })
		else
			local nab = T.button(b, "Not a bug", "quiet", function()
				if not d.editor:connected() then
					d:status("Not connected: connect to send feedback")
					return
				end
				d.editor:feedback(sig, "not_a_bug")
				r.notABug[sig] = true
				d:save()
				local name = M.trim(f.detector)
				d:status("Told the agent: " .. (if name ~= "" then name else M.scrub(tostring(f.summary or "")))
					.. " is not a bug")
				self:render()
			end, true)
			T.grey(nab, not d.editor:connected())
		end
	end
end

-- --- the page -------------------------------------------------------------------------------------------------------

-- Whether the agent service connected is on this computer (a loopback address).
function RunPage:_localAgent(): boolean
	local ed = self.d.editor
	if not ed:connected() then
		return false
	end
	local host = string.match(tostring(ed.link.url), "^%a+://%[?([^/%]]+)") or ""
	host = string.match(host, "^(.-):%d+$") or host
	return host == "127.0.0.1" or host == "localhost" or host == "::1" or string.sub(host, 1, 4) == "127."
end

function RunPage:_tabMeta(r: any): string
	return string.upper(string.format("RUN %s // %s // DURATION %s // %s", r.id, r:kindWord(),
		M.clock(if r:live() then r:elapsed() else r:took()), M.attemptsText(r:attemptCount())))
end

function RunPage:render()
	local d = self.d
	local r = d.shown
	if r == nil then
		return
	end
	local hasSteps = #r.steps > 0
	local showPolicy = Settings.get(d.plugin, "show_policy_log") ~= false
	if d.tab == "policy" and (not hasSteps or not showPolicy) then
		d.tab = "main"
	end
	self.tabMain.Text = if r:live() then "Live" else "Result"
	self.tabPolicy.Text = string.format("Policy log · %d", #r.steps)
	self.tabPolicy.Visible = hasSteps and showPolicy
	self.tabMeta.Text = self:_tabMeta(r)
	self.tabMeta.Visible = d.wide
	self.tabLocal.Visible = d.wide and r:live() and self:_localAgent()
	local inner = self.tabInner
	T.pad(inner, if d.wide then 16 else 12, 0, if d.wide then 16 else 12, 0)
	for key, b in pairs({ main = self.tabMain, feed = self.tabFeed, policy = self.tabPolicy }) do
		local on = d.tab == key
		b.TextColor3 = if on then C.text else C.muted;
		(b:FindFirstChild("Underline") :: Frame).Visible = on
	end
	self.live.Visible = d.tab == "main" and r:live()
	self.result.Visible = d.tab == "main" and not r:live()
	self.feedArea.Visible = d.tab == "feed"
	self.policyFrame.Visible = d.tab == "policy"
	-- the steer box: the Live page's foot, or the Feed tab's (the same box, the same words)
	self.steerRow.Parent = if d.tab == "feed" then self.feedSteerSlot else self.live
	self.steerRow.LayoutOrder = 1000000
	self.steerRow.Visible = self:_canSteerShown()
	self:_updateSteer()
	if self.live.Visible then
		self:_renderLive()
	elseif self.result.Visible then
		if self.resultRun ~= r then
			self.showReport = false
			self.resultRun = r
		end
		self:_renderResult()
	elseif d.tab == "feed" then
		self:_renderFeed()
	elseif d.tab == "policy" then
		self.policy:setRun(r)
	end
end

-- The run going on changed: the page it shows drawn again (the Feed tab: the new lines appended).
function RunPage:liveChanged()
	local d = self.d
	local r = d.shown
	if r == nil then
		return
	end
	if self.live.Visible then
		self:_renderLive()
	elseif self.feedArea.Visible then
		local cur = currentAttemptLine(r)
		local total = r.feedTotal or #r.feed
		local fresh = total - (self.feedTotal or 0) -- (lines added since the tab was drawn)
		if self.feedRun ~= r or fresh < 0 or fresh > #r.feed or (cur > 0 and cur > #r.feed - fresh) or d.feedUpdated then
			self:_renderFeed()
			d.feedUpdated = nil
		else
			local bottom = self:_atBottom()
			self.feedEmpty.Visible = #r.feed == 0
			self.feedPlate.Visible = #r.feed > 0
			for i = #r.feed - fresh + 1, #r.feed do
				self:_appendFeed(r.feed[i], i == cur)
			end
			self.feedTotal = total
			if bottom then
				self:_toBottom()
			end
		end
		self.steerRow.Visible = self:_canSteerShown()
		self:_updateSteer()
	end
	self.tabMain.Text = if r:live() then "Live" else "Result"
	local hasSteps = #r.steps > 0
	self.tabPolicy.Text = string.format("Policy log · %d", #r.steps)
	if (hasSteps and Settings.get(d.plugin, "show_policy_log") ~= false) ~= self.tabPolicy.Visible then
		self:render()
	end
end

-- A policy step of the run shown.
function RunPage:onStep(st: any)
	local d = self.d
	local r = d.shown
	self.tabPolicy.Text = string.format("Policy log · %d", #r.steps)
	if not self.tabPolicy.Visible and Settings.get(d.plugin, "show_policy_log") ~= false then
		self:render()
	elseif self.policyFrame.Visible then
		self.policy:onStep(st)
	elseif self.live.Visible then
		local n = #r.steps
		self.lSteps.Text = string.format("%d policy step%s", n, if n == 1 then "" else "s")
	end
end

function RunPage:resized()
	local d = self.d
	if d.shown == nil then
		return
	end
	if self.live.Visible then
		self:_renderLive()
	elseif self.policyFrame.Visible then
		self.policy:resized()
	end
end

return RunPage
