--!strict
-- The Policy log (as Godot's ui/policy_log.gd): every policy step of a run (newest at the bottom, following live until a
-- row is picked), filters by harness attempt and action tag and an action's letters, the replay of the attempt (which
-- action ran when), and the step picked: its tag, π(a | s) and the Observation, readable (in plates) or as raw JSON
-- (always without the questions' instructions).
local M = require(script.Parent.RunModel)
local T = require(script.Parent.Theme)

local C = T.C

local PolicyLog = {}
PolicyLog.__index = PolicyLog

local WIDE_COLS = { "wall", "t", "n", "current", "decision", "bar", "ms" }
local NARROW_COLS = { "n", "decision", "bar", "ms" }
local COL_TITLES = { wall = "Wall clock", t = "Game t", n = "Step", current = "Current action", decision = "Decision",
	bar = "Action probabilities", ms = "ms" }
local COL_W = { wall = 92, t = 56, n = 56, current = 104, decision = 176, ms = 34 }
local COL_W_NARROW = { decision = 150, ms = 30 }

-- A policy tag's kind: SWITCH the accent, START info, HELD warning, DROPPED red, KEEP neutral.
local function tagKind(tag: string): string
	if tag == "SWITCH" then
		return "acc"
	elseif tag == "START" then
		return "info"
	elseif tag == "HELD" then
		return "warn"
	elseif tag == "DROPPED" or tag == "FAILED" then
		return "bad"
	end
	return "mut"
end

-- {surface, border, text}: SWITCH filled in the accent (ink on it), the others their kind's tag colors.
local function tagColors(tag: string): { Color3 }
	if tag == "SWITCH" then
		return { C.accent, C.accent, C.onAccent }
	end
	local c = T.TAGS[tagKind(tag)]
	return { c[1], c[3], c[2] }
end

local function policyTag(parent: Instance, tag: string, word: string?, width: number?, big: boolean?): Frame
	local c = tagColors(tag)
	local f = T.tag(parent, word or tag, nil, { bg = c[1], border = c[2], color = c[3], size = if big then 11 else 10,
		height = if big then 22 else 18, padX = if big then 10 else 4 })
	if width then
		f.AutomaticSize = Enum.AutomaticSize.None
		f.Size = UDim2.fromOffset(width, 18)
		local l = f:FindFirstChildOfClass("TextLabel") :: TextLabel
		l.Size = UDim2.fromScale(1, 1)
		l.TextXAlignment = Enum.TextXAlignment.Center
	end
	return f
end

-- JSON as shown (the step, readable): two spaces a level, keys in order.
local function pretty(v: any, indent: string?): string
	local ind = indent or ""
	local t = type(v)
	if t == "table" then
		local n = #v
		local isArray = n > 0 or next(v) == nil
		if isArray then
			for k in pairs(v) do
				if type(k) ~= "number" then
					isArray = false
					break
				end
			end
		end
		if next(v) == nil then
			return if getmetatable(v) ~= nil then "{}" else "[]"
		end
		local inner = ind .. "  "
		local parts = {}
		if isArray then
			for _, x in ipairs(v) do
				table.insert(parts, inner .. pretty(x, inner))
			end
			return "[\n" .. table.concat(parts, ",\n") .. "\n" .. ind .. "]"
		end
		local keys = {}
		for k in pairs(v) do
			table.insert(keys, tostring(k))
		end
		table.sort(keys)
		for _, k in ipairs(keys) do
			local x = v[k]
			if x == nil then
				x = v[tonumber(k) or k]
			end
			table.insert(parts, inner .. string.format("%q", k) .. ": " .. pretty(x, inner))
		end
		return "{\n" .. table.concat(parts, ",\n") .. "\n" .. ind .. "}"
	elseif t == "string" then
		return (string.gsub(string.format("%q", v), "\\\n", "\\n"))
	elseif t == "number" or t == "boolean" then
		return tostring(v)
	end
	return "null"
end

-- The frame as shown raw: the step without its questions' instructions, named as a policy step.
local function redact(frame: any): any
	local function copy(x: any): any
		if type(x) ~= "table" then
			return x
		end
		local out = {}
		for k, y in pairs(x) do
			out[k] = copy(y)
		end
		return out
	end
	local f = copy(frame)
	for k in pairs(f) do
		if string.sub(tostring(k), 1, 1) == "_" then
			f[k] = nil
		end
	end
	if f.type ~= nil then
		f.type = "policy_step"
	end
	if type(f.seconds) == "table" and f.seconds.jev ~= nil then
		f.seconds.policy = f.seconds.jev
		f.seconds.jev = nil
	end
	if type(f.input) == "table" and type(f.input.questions) == "table" then
		for _, q in pairs(f.input.questions) do
			if type(q) == "table" then
				q.instructions = nil
			end
		end
	end
	return f
end

function PolicyLog.new(d: any, frame: Frame): any
	local self = setmetatable({}, PolicyLog) :: any
	self.d = d
	self.frame = frame
	self.run = nil
	self.follow = true
	self.selected = nil
	self.attemptFilter = 0
	self.tagOn = { SWITCH = true, START = true, KEEP = true, HELD = true, DROPPED = true, FAILED = true }
	self.raw = false
	self.find = ""
	self.rows = {}
	T.list(frame, 0)
	local _, fi = T.bar(frame, { pad = { 16, 8, 16, 8 }, horizontal = true, wrap = true, gap = 6, name = "Filters" })
	self.filters = fi
	local rb, ri = T.bar(frame, { bg = C.plate, pad = { 16, 10, 16, 8 }, gap = 3, name = "Replay" })
	self.replayBox = rb
	self.replay = ri
	local split = Instance.new("Frame")
	split.Name = "Split"
	split.BackgroundTransparency = 1
	split.Size = UDim2.new(1, 0, 0, 0)
	split.LayoutOrder = T.nextOrder()
	T.fill(split)
	split.Parent = frame
	self.split = split
	self.splitLayout = T.list(split, 0)
	-- the table: its titles, its rows (scrolled), "No policy steps yet.", its summary
	local left = Instance.new("Frame")
	left.Name = "Table"
	left.BackgroundColor3 = C.ground
	left.BorderSizePixel = 0
	left.LayoutOrder = 1
	left.Parent = split
	T.list(left, 0)
	self.left = left
	local leftFlex = Instance.new("UIFlexItem")
	leftFlex.FlexMode = Enum.UIFlexMode.Custom
	leftFlex.GrowRatio = 1.5
	leftFlex.Parent = left
	self.titles = T.frame(left, "Titles")
	self.list = T.scroll(left, "Rows")
	self.list.Size = UDim2.new(1, 0, 0, 0)
	T.fill(self.list)
	T.list(self.list, 0)
	local e = T.col(left, 0, "Empty")
	T.pad(e, 16, 10, 16, 10)
	T.label(e, "No policy steps yet.", "small", { color = C.muted })
	self.empty = e
	local sb, si = T.bar(left, { side = "top", pad = { 16, 7, 16, 7 }, name = "Summary" })
	self.summaryBox = sb
	self.summary = T.label(si, "", "monoDim")
	-- the step: beside the table when wide, under it when narrow
	local detail = Instance.new("Frame")
	detail.Name = "Detail"
	detail.BackgroundColor3 = C.plate
	detail.BorderSizePixel = 0
	detail.LayoutOrder = 3
	detail.Parent = split
	local detailFlex = Instance.new("UIFlexItem")
	detailFlex.FlexMode = Enum.UIFlexMode.Custom
	detailFlex.GrowRatio = 1
	detailFlex.Parent = detail
	local edge = Instance.new("Frame")
	edge.Name = "Edge"
	edge.BorderSizePixel = 0
	edge.BackgroundColor3 = C.rule
	edge.LayoutOrder = 2
	edge.Parent = split
	self.edge = edge
	self.detail = detail
	self.detailScroll = T.scroll(detail, "Scroll")
	T.list(self.detailScroll, 0)
	self:_layout()
	return self
end

function PolicyLog:_layout()
	local wide = self.d.wide
	self.wide = wide
	self.cols = if wide then WIDE_COLS else NARROW_COLS
	self.splitLayout.FillDirection = if wide then Enum.FillDirection.Horizontal else Enum.FillDirection.Vertical
	if wide then
		self.left.Size = UDim2.new(0, 0, 1, 0)
		self.detail.Size = UDim2.new(0, 0, 1, 0)
		self.edge.Size = UDim2.new(0, 1, 1, 0)
	else
		self.left.Size = UDim2.new(1, 0, 0, 0)
		self.detail.Size = UDim2.new(1, 0, 0, 0)
		self.edge.Size = UDim2.new(1, 0, 0, 1)
	end
	local side = if wide then 16 else 10
	T.pad(self.filters, side, 8, side, 8)
	T.pad(self.replay, if wide then 16 else 10, if wide then 10 else 8, if wide then 16 else 10, if wide then 8 else 6)
	T.pad(self.summaryBox:FindFirstChild("Inner") :: Instance, side, 7, side, 7)
end

function PolicyLog:resized()
	if self.d.wide ~= self.wide then
		self:_layout()
		self:_rebuild()
	end
end

function PolicyLog:setRun(r: any)
	if r ~= self.run then
		self.selected = nil
		self.follow = true
		self.attemptFilter = 0
	end
	self.run = r
	if self.d.wide ~= self.wide then
		self:_layout()
	end
	self:_rebuild()
end

function PolicyLog:_passes(st: any): boolean
	if self.find ~= "" then
		local f = string.lower(self.find)
		if not (string.find(string.lower(st.chosen), f, 1, true) or string.find(string.lower(st.current), f, 1, true)) then
			return false
		end
	end
	return (self.attemptFilter == 0 or st.attempt == self.attemptFilter) and self.tagOn[st.tag] ~= false
end

function PolicyLog:shownSteps(): { any }
	local out = {}
	if self.run then
		for _, st in ipairs(self.run.steps) do
			if self:_passes(st) then
				table.insert(out, st)
			end
		end
	end
	return out
end

-- A filter chip (24 tall, mono): on in the accent tag's colors.
function PolicyLog:_chip(parent: Instance, text: string, on: boolean, onClick: () -> ()): TextButton
	local b = Instance.new("TextButton")
	b.Name = "Chip"
	b.Text = text
	b.AutoButtonColor = false
	b.BorderSizePixel = 0
	b.FontFace = T.F.monoMedium
	b.TextSize = 11
	b.Size = UDim2.new(0, 0, 0, 24)
	b.AutomaticSize = Enum.AutomaticSize.X
	b.LayoutOrder = T.nextOrder()
	T.pad(b, 9, 0, 9, 0)
	local s = T.stroke(b, if on then C.stepDone else C.line)
	b.BackgroundColor3 = if on then C.rowActive else C.ground
	b.BackgroundTransparency = if on then 0 else 1
	b.TextColor3 = if on then C.accent else C.muted
	b.MouseEnter:Connect(function()
		if not on then
			b.TextColor3 = C.text
			b.BackgroundColor3 = C.hover
			b.BackgroundTransparency = 0
		end
	end)
	b.MouseLeave:Connect(function()
		if not on then
			b.TextColor3 = C.muted
			b.BackgroundTransparency = 1
		end
	end)
	b.MouseButton1Click:Connect(onClick)
	b.Parent = parent
	local _ = s
	return b
end

function PolicyLog:_renderFilters()
	local fi = self.filters
	local run = self.run
	if T.focused and T.focused:IsDescendantOf(fi) then
		return -- (the Find box being typed in)
	end
	T.clear(fi)
	T.label(fi, "Policy", "caps", { wrap = false })
	local per, counts = {}, {}
	for _, st in ipairs(run.steps) do
		per[st.attempt] = (per[st.attempt] or 0) + 1
		if self.attemptFilter == 0 or st.attempt == self.attemptFilter then
			counts[st.tag] = (counts[st.tag] or 0) + 1
		end
	end
	self:_chip(fi, string.format("All · %d", #run.steps), self.attemptFilter == 0, function()
		self.attemptFilter = 0
		self:_rebuild()
	end)
	for a = 1, run:stepAttempts() do
		if per[a] then
			self:_chip(fi, string.format("H%d · %d", a, per[a]), self.attemptFilter == a, function()
				self.attemptFilter = a
				self:_rebuild()
			end)
		end
	end
	if self.wide then
		local div = Instance.new("Frame")
		div.BorderSizePixel = 0
		div.BackgroundColor3 = C.line
		div.Size = UDim2.fromOffset(1, 18)
		div.LayoutOrder = T.nextOrder()
		div.Parent = fi
	end
	T.label(fi, "Show", "caps", { wrap = false })
	for _, tag in ipairs(M.TAG_ORDER) do
		if tag ~= "FAILED" or (counts.FAILED or 0) > 0 then
			self:_chip(fi, string.format("%s %d", tag, counts[tag] or 0), self.tagOn[tag] ~= false, function()
				self.tagOn[tag] = not (self.tagOn[tag] ~= false)
				self:_rebuild()
			end)
		end
	end
	local find = T.input(fi, { mono = true, size = 11, height = 24, width = if self.wide then 200 else 150,
		placeholder = "Find an action…", text = self.find, name = "Find" })
	find:GetPropertyChangedSignal("Text"):Connect(function()
		local now = M.trim(find.Text)
		if now ~= self.find then
			self.find = now
			self:_rebuildRows()
			self:_showDetail()
			self:_summary()
		end
	end)
	self.followLabel = T.label(fi, "", "caps", { wrap = false })
	self.followBtn = T.button(fi, "Follow live", "default", function()
		self:setFollow(true)
	end, true)
	self:_updateFollow()
end

function PolicyLog:setFollow(on: boolean)
	self.follow = on
	if on then
		self.selected = nil
	end
	self:_rebuildRows()
	self:_showDetail()
	self:_updateFollow()
	self:_replay()
	self:_summary()
end

function PolicyLog:_updateFollow()
	if self.followLabel == nil or self.followLabel.Parent == nil then
		return
	end
	if self.follow then
		self.followLabel.Text = "● FOLLOWING"
		self.followLabel.TextColor3 = C.accent
	else
		self.followLabel.Text = if self.selected then "PAUSED ON " .. M.stepLabel(self.selected) else "PAUSED"
		self.followLabel.TextColor3 = C.amber
	end
	self.followBtn.Visible = not self.follow
end

-- The table's cells for a step (or its titles when st is nil).
function PolicyLog:_cells(parent: Instance, st: any)
	local run = self.run
	for _, key in ipairs(self.cols) do
		local w = if self.wide then COL_W[key] else (COL_W_NARROW[key] or COL_W[key])
		local cell = Instance.new("Frame")
		cell.Name = key
		cell.BackgroundTransparency = 1
		cell.ClipsDescendants = true
		cell.LayoutOrder = T.nextOrder()
		cell.Size = UDim2.new(0, w or 0, 1, 0)
		if w == nil then
			T.fill(cell)
		end
		cell.Parent = parent
		if st == nil then
			local l = T.label(cell, COL_TITLES[key], "caps", { wrap = false, truncate = true,
				align = if key == "ms" then "right" else nil })
			l.Size = UDim2.fromScale(1, 1)
		elseif key == "decision" then
			T.list(cell, 8, true)
			policyTag(cell, st.tag, nil, 62)
			local l = T.label(cell, if st.chosen ~= "" then st.chosen else "—", "mono", { wrap = false, truncate = true,
				size = 11, font = if st.tag == "SWITCH" then T.F.monoMedium else nil,
				color = if st.chosen ~= "" then run:colorOf(st.chosen, T.ACTION_COLORS) else C.muted })
			l.Size = UDim2.new(1, -70, 1, 0)
		elseif key == "bar" then
			local probs = M.sortedProbs(st)
			local parts, rest = {}, 0
			for i, pr in ipairs(probs) do
				if i <= 2 then
					table.insert(parts, { pr[2], run:colorOf(pr[1], T.ACTION_COLORS) })
				else
					rest += pr[2]
				end
			end
			if rest > 0 then
				table.insert(parts, { rest, C.lineHi })
			end
			PolicyLog.bar(cell, parts, 9)
		else
			local text, color = "", C.text2
			if key == "wall" then
				text, color = if st.wall > 0 then M.wallTime(st.wall) else "—", C.muted
			elseif key == "t" then
				text = string.format("%.2fs", st.t)
			elseif key == "n" then
				text, color = M.stepLabel(st), C.muted
			elseif key == "current" then
				text = if st.current ~= "" then st.current else "—"
				color = if st.current ~= "" then run:colorOf(st.current, T.ACTION_COLORS) else C.muted
			elseif key == "ms" then
				text, color = if st.ms > 0 then tostring(math.floor(st.ms)) else "—", C.muted
			end
			local l = T.label(cell, text, "mono", { wrap = false, truncate = true, size = 11, color = color,
				align = if key == "ms" then "right" else nil })
			l.Size = UDim2.fromScale(1, 1)
		end
	end
end

-- A stacked bar of probabilities: {{p, color}} on HOVER, height tall, centred in parent.
function PolicyLog.bar(parent: Instance, parts: { any }, height: number): Frame
	local b = Instance.new("Frame")
	b.Name = "Bar"
	b.BorderSizePixel = 0
	b.BackgroundColor3 = C.hover
	b.AnchorPoint = Vector2.new(0, 0.5)
	b.Position = UDim2.new(0, 4, 0.5, 0)
	b.Size = UDim2.new(1, -8, 0, height)
	b.LayoutOrder = T.nextOrder()
	local x = 0
	for _, p in ipairs(parts) do
		local w = math.clamp(p[1], 0, 1 - x)
		if w > 0 then
			local s = Instance.new("Frame")
			s.BorderSizePixel = 0
			s.BackgroundColor3 = p[2]
			s.Position = UDim2.fromScale(x, 0)
			s.Size = UDim2.fromScale(w, 1)
			s.Parent = b
			x += w
		end
	end
	b.Parent = parent
	return b
end

function PolicyLog:_row(st: any): TextButton
	local sel = st == self.selected
	local b = Instance.new("TextButton")
	b.Name = "Step"
	b.Text = ""
	b.AutoButtonColor = false
	b.BorderSizePixel = 0
	b.BackgroundColor3 = if sel then C.rowActive else C.ground
	b.Size = UDim2.new(1, 0, 0, 28)
	b.LayoutOrder = T.nextOrder()
	local inner = Instance.new("Frame")
	inner.BackgroundTransparency = 1
	inner.Size = UDim2.new(1, 0, 1, -1)
	inner.Parent = b
	T.list(inner, 8, true)
	T.pad(inner, 10, 0, 10, 0)
	self:_cells(inner, st)
	local guide = Instance.new("Frame")
	guide.BorderSizePixel = 0
	guide.BackgroundColor3 = C.hover
	guide.AnchorPoint = Vector2.new(0, 1)
	guide.Position = UDim2.fromScale(0, 1)
	guide.Size = UDim2.new(1, 0, 0, 1)
	guide.Parent = b
	if sel then
		T.leftEdge(b, C.accent)
	end
	b.MouseEnter:Connect(function()
		if st ~= self.selected then
			b.BackgroundColor3 = C.hover
		end
	end)
	b.MouseLeave:Connect(function()
		if st ~= self.selected then
			b.BackgroundColor3 = C.ground
		end
	end)
	b.MouseButton1Click:Connect(function()
		self.selected = st
		self.follow = false
		self:_rebuildRows()
		self:_showDetail()
		self:_updateFollow()
		self:_replay()
		self:_summary()
	end)
	b.Parent = self.list
	return b
end

function PolicyLog:_toBottom()
	local sc = self.list
	task.defer(function()
		sc.CanvasPosition = Vector2.new(0, math.max(0, sc.AbsoluteCanvasSize.Y - sc.AbsoluteWindowSize.Y))
	end)
end

function PolicyLog:_rebuildRows()
	T.clear(self.list)
	self.rows = {}
	local rows = self:shownSteps()
	local first = math.max(1, #rows - self.d.POLICY_SHOWN + 1)
	if self.selected then
		local at = table.find(rows, self.selected)
		if at and at < first then
			first = at
		end
	end
	for i = first, math.min(#rows, first + self.d.POLICY_SHOWN - 1) do
		table.insert(self.rows, self:_row(rows[i]))
	end
	if self.follow then
		self:_toBottom()
	end
end

function PolicyLog:_rebuild()
	local run = self.run
	if run == nil then
		return
	end
	T.clear(self.titles)
	local ti = Instance.new("Frame")
	ti.BackgroundTransparency = 1
	ti.Size = UDim2.new(1, 0, 0, 30)
	ti.Parent = self.titles
	T.list(ti, 8, true)
	T.pad(ti, 10, 0, 10, 0)
	self:_cells(ti, nil)
	T.rule(self.titles)
	self:_renderFilters()
	self:_rebuildRows()
	local none = #run.steps == 0
	self.empty.Visible = none
	self.replayBox.Visible = not none
	self:_replay()
	self:_showDetail()
	self:_summary()
	self:_updateFollow()
end

-- A new step of the run shown (already added to it).
function PolicyLog:onStep(st: any)
	if self.run == nil then
		return
	end
	self:_renderFilters()
	self.empty.Visible = false
	self.replayBox.Visible = true
	if self:_passes(st) then
		local atBottom = self.list.CanvasPosition.Y + self.list.AbsoluteWindowSize.Y >= self.list.AbsoluteCanvasSize.Y - 8
		table.insert(self.rows, self:_row(st))
		while #self.rows > self.d.POLICY_SHOWN do
			table.remove(self.rows, 1):Destroy()
		end
		if self.follow or atBottom then
			self:_toBottom()
		end
	end
	if self.follow then
		self:_showDetail()
	end
	self:_replay()
	self:_summary()
end

function PolicyLog:_replayAttempt(): number
	if self.attemptFilter > 0 then
		return self.attemptFilter
	end
	if self.selected then
		return self.selected.attempt
	end
	return if self.run then self.run:stepAttempts() else 1
end

local function running(st: any): string
	if st.tag == "START" or st.tag == "SWITCH" then
		return st.chosen
	end
	return if st.current ~= "" then st.current else st.chosen
end

-- What ran over game time in one attempt: "H2 · RUNNING" and the action running as colored spans on a lane, the first
-- and last step's game time, and (wide) a legend of the actions.
function PolicyLog:_replay()
	local box = self.replay
	T.clear(box)
	local run = self.run
	if run == nil or #run.steps == 0 then
		return
	end
	local a = self:_replayAttempt()
	local steps = {}
	for _, st in ipairs(run.steps) do
		if st.attempt == a then
			table.insert(steps, st)
		end
	end
	local lw = if self.wide then 120 else 84
	local lane = T.row(box, 0)
	local cap = T.label(lane, string.format("H%d · Running", a), "caps", { wrap = false, truncate = true })
	cap.Size = UDim2.fromOffset(lw, 16)
	local track = Instance.new("Frame")
	track.Name = "Lane"
	track.BorderSizePixel = 0
	track.BackgroundColor3 = C.hover
	track.Size = UDim2.new(1, -lw, 0, if self.wide then 16 else 14)
	track.LayoutOrder = T.nextOrder()
	track.ClipsDescendants = true
	track.Parent = lane
	if #steps == 0 then
		return
	end
	local t0, t1 = steps[1].t, steps[#steps].t
	local span = math.max(t1 - t0, 0.001)
	local names = {}
	for i, st in ipairs(steps) do
		local x0 = (st.t - t0) / span
		local x1 = if i < #steps then (steps[i + 1].t - t0) / span else 1
		local name = running(st)
		if name ~= "" then
			if not table.find(names, name) then
				table.insert(names, name)
			end
			local s = Instance.new("Frame")
			s.BorderSizePixel = 0
			s.BackgroundColor3 = run:colorOf(name, T.ACTION_COLORS)
			s.Position = UDim2.fromScale(x0, 0)
			s.Size = UDim2.new(math.max(0, x1 - x0), 1, 1, 0)
			s.Parent = track
		end
		if st == self.selected then
			local m = Instance.new("Frame")
			m.BorderSizePixel = 0
			m.BackgroundColor3 = C.text
			m.Position = UDim2.new(x0, -1, 0, 0)
			m.Size = UDim2.new(0, 2, 1, 0)
			m.ZIndex = 3
			m.Parent = track
		end
	end
	local times = T.row(box, 0)
	local pad = Instance.new("Frame")
	pad.BackgroundTransparency = 1
	pad.Size = UDim2.fromOffset(lw, 14)
	pad.LayoutOrder = 1
	pad.Parent = times
	T.label(times, string.format("%.1fs", t0), "monoDim", { wrap = false, fill = true })
	T.label(times, string.format("%.1fs", t1), "monoDim", { wrap = false })
	if self.wide then
		local lg = T.row(box, 14, true)
		T.pad(lg, lw, 2, 0, 0)
		for i, n in ipairs(names) do
			if i > 8 then
				break
			end
			local it = T.row(lg, 6)
			it.Size = UDim2.new(0, 0, 0, 14)
			it.AutomaticSize = Enum.AutomaticSize.X
			T.dot(it, run:colorOf(n, T.ACTION_COLORS), 8)
			T.label(it, n, "monoDim", { wrap = false, color = C.text3 })
		end
	end
end

local function count(n: number, one: string, many: string): string
	return string.format("%d %s", n, if n == 1 then one else many)
end

function PolicyLog:_summary()
	local run = self.run
	if run == nil or #run.steps == 0 then
		self.summaryBox.Visible = false
		return
	end
	self.summaryBox.Visible = true
	local h = if self.attemptFilter > 0 then self.attemptFilter elseif self.selected then self.selected.attempt else 0
	local steps, ms, counts = {}, {}, {}
	for _, st in ipairs(run.steps) do
		if h == 0 or st.attempt == h then
			table.insert(steps, st)
			if st.ms > 0 then
				table.insert(ms, st.ms)
			end
			counts[st.tag] = (counts[st.tag] or 0) + 1
		end
	end
	table.sort(ms)
	local parts = { string.format("%s: %s", if h > 0 then "H" .. h else "All harnesses", count(#steps, "step", "steps")) }
	if #ms > 0 then
		table.insert(parts, string.format("median %d ms · max %d ms", math.floor(ms[#ms // 2 + 1]), math.floor(ms[#ms])))
	end
	table.insert(parts, count(counts.SWITCH or 0, "switch", "switches"))
	table.insert(parts, count(counts.START or 0, "start", "starts"))
	table.insert(parts, string.format("%d held", counts.HELD or 0))
	table.insert(parts, string.format("%d dropped", counts.DROPPED or 0))
	if (counts.FAILED or 0) > 0 then
		table.insert(parts, string.format("%d failed", counts.FAILED))
	end
	if (run.stepCount or 0) > #run.steps then
		table.insert(parts, string.format("the oldest %d no longer kept", run.stepCount - #run.steps))
	end
	self.summary.Text = table.concat(parts, " · ")
end

-- --- the step shown -------------------------------------------------------------------------------------------------

function PolicyLog:_shownStep(): any
	if self.selected then
		return self.selected
	end
	local run = self.run
	if run == nil then
		return nil
	end
	for i = #run.steps, 1, -1 do
		if self:_passes(run.steps[i]) then
			return run.steps[i]
		end
	end
	return nil
end

function PolicyLog:stepBy(delta: number)
	local rows = self:shownSteps()
	if #rows == 0 then
		return
	end
	local at = table.find(rows, self:_shownStep()) or #rows
	self.selected = rows[math.clamp(at + delta, 1, #rows)]
	self.follow = false
	self:_rebuildRows()
	self:_showDetail()
	self:_updateFollow()
	self:_replay()
	self:_summary()
end

-- A section of the Observation: a plate on the ground, its title (and an aside) over its lines.
local function section(parent: Instance, title: string, aside: string?, asideColor: Color3?): Frame
	local v = T.plate(parent, { bg = C.ground, pad = { 12, 10, 12, 10 }, gap = 4, name = "Section" })
	local h = T.row(v, 8, true)
	T.label(h, title, "caps", { wrap = false, fill = true })
	if aside and aside ~= "" then
		T.label(h, aside, "monoDim", { wrap = false, color = asideColor })
	end
	return v
end

-- A row of mono cells: {text, color, width (0: the rest), "body"?}.
local function cells(parent: Instance, list: { any }, size: number?)
	local h = T.row(parent, 10)
	for _, c in ipairs(list) do
		local w = c[3]
		local l
		if w > 0 then
			l = T.label(h, tostring(c[1]), "mono", { wrap = false, truncate = true, size = size or 11, color = c[2] })
			l.Size = UDim2.fromOffset(w, 16)
		else
			l = T.label(h, tostring(c[1]), if c[4] == "body" then "small" else "mono", { size = if c[4] == "body"
				then 12 else (size or 11), color = c[2] })
			l.Size = UDim2.new(0, 0, 0, 0)
			l.AutomaticSize = Enum.AutomaticSize.Y
			T.fill(l)
		end
	end
end

local function criteriaOf(st: any): any
	local input = st.frame.input
	if type(input) == "table" and type(input.questions) == "table" then
		local pick = input.questions.pick
		if type(pick) == "table" and type(pick.criteria) == "table" then
			return pick.criteria
		end
	end
	return {}
end

function PolicyLog:_showDetail()
	local box = self.detailScroll
	T.clear(box)
	local run = self.run
	local st = self:_shownStep()
	local dv = T.col(box, 12, "Content")
	if self.wide then
		T.pad(dv, 18, 14, 18, 14)
	else
		T.pad(dv, 12, 12, 12, 12)
	end
	if st == nil then
		T.label(dv, if run == nil or #run.steps == 0 then "No policy steps yet."
			else "Pick a step to see what the policy observed and chose.", "monoDim")
		return
	end
	local top = T.col(dv, 7)
	T.label(top, string.format("%s · t=%.1fs%s%s", string.gsub(M.stepLabel(st), " ", " · "), st.t,
		if st.wall > 0 then " · " .. M.wallTime(st.wall) else "",
		if st.ms > 0 then string.format(" · %d ms", math.floor(st.ms)) else ""), "monoDim")
	local row = T.row(top, 8)
	policyTag(row, st.tag, M.TAG_PAST[st.tag] or st.tag, nil, true)
	T.label(row, if st.current ~= "" then st.current else "nothing running", "mono", { wrap = false, truncate = true,
		color = if st.current ~= "" then run:colorOf(st.current, T.ACTION_COLORS) else C.muted }).Size =
		UDim2.new(0, 120, 0, 16)
	T.label(row, "→", "mono", { wrap = false, color = C.muted })
	T.label(row, if st.chosen ~= "" then st.chosen else "no action", "mono", { wrap = false, fill = true,
		font = T.F.monoMedium, color = if st.chosen ~= "" then run:colorOf(st.chosen, T.ACTION_COLORS) else C.muted })
	local criteria = criteriaOf(st)
	local nd = st.state.now_doing
	if type(nd) == "table" then
		local lbl = tostring(criteria[st.current] or "")
		T.label(top, string.format("Action: \"%s\" (%s)", if lbl ~= "" then lbl else st.current, M.secs(nd.for_seconds)),
			"small")
	else
		T.label(top, "No action was running.", "small")
	end
	if st.error ~= "" then
		T.label(top, "This step failed: " .. M.scrub(st.error), "small", { color = C.red })
	end
	-- π(a | s): every action, the chosen one first
	local pi = T.col(dv, 6)
	local cap = T.label(pi, "POLICY OUTPUT · π(a | s)", "caps", { wrap = false })
	cap.Text = "POLICY OUTPUT · π(a | s)" -- (lowercase kept)
	local nw = if self.wide then 160 else 130
	for _, pr in ipairs(M.sortedProbs(st)) do
		local action = pr[1]
		local chosen = action == st.chosen
		local r = T.row(pi, 10)
		local l = T.label(r, action .. (if action == st.current then " · current" else ""), "mono", { wrap = false,
			truncate = true, size = 12, font = if chosen then T.F.monoMedium else nil,
			color = if chosen then run:colorOf(action, T.ACTION_COLORS) else C.text2 })
		l.Size = UDim2.fromOffset(nw, 16)
		local holder = Instance.new("Frame")
		holder.BackgroundTransparency = 1
		holder.Size = UDim2.new(0, 0, 0, 16)
		holder.LayoutOrder = T.nextOrder()
		T.fill(holder)
		holder.Parent = r
		PolicyLog.bar(holder, { { pr[2], run:colorOf(action, T.ACTION_COLORS) } }, 10)
		local pv = T.label(r, M.prob(pr[2]), "mono", { wrap = false, size = 12, color = C.text, align = "right" })
		pv.AutomaticSize = Enum.AutomaticSize.None
		pv.Size = UDim2.fromOffset(34, 16)
	end
	local toggles = T.row(dv, 6, true)
	T.label(toggles, string.format("Observation · #%d", st.n), "caps", { wrap = false, color = C.accent })
	self:_chip(toggles, "Readable", not self.raw, function()
		self.raw = false
		self:_showDetail()
	end)
	self:_chip(toggles, "Raw JSON", self.raw, function()
		self.raw = true
		self:_showDetail()
	end)
	local rows = self:shownSteps()
	local at = table.find(rows, st)
	local prev = T.iconButton(toggles, "‹", "default", function()
		self:stepBy(-1)
	end, true, "Previous step")
	T.disable(prev, at == nil or at <= 1)
	local nextb = T.iconButton(toggles, "›", "default", function()
		self:stepBy(1)
	end, true, "Next step")
	T.disable(nextb, at == nil or at >= #rows)
	if self.raw then
		T.selectable(dv, pretty(redact(st.frame)), { multiline = true, size = 11, radius = T.R_PLATE })
		return
	end
	self:_observation(dv, st, criteria)
end

function PolicyLog:_observation(parent: Instance, st: any, criteria: any)
	local run = self.run
	local s = st.state
	local prev = run:previousStep(st)
	local prevSit = if prev then prev.state.situation else nil
	-- Task
	local task = T.plate(parent, { bg = C.ground, pad = { 12, 10, 12, 10 }, gap = 12, horizontal = true, name = "Task" })
	local tk = T.label(task, "Task", "caps", { wrap = false })
	tk.Size = UDim2.fromOffset(50, 16)
	local goal = T.label(task, tostring(s.goal or st.frame.goal or ""), "body")
	goal.Size = UDim2.new(1, -62, 0, 0)
	-- the environment state, what changed since the step before in the accent
	local sit = s.situation
	local changed = {}
	if type(sit) == "table" and type(prevSit) == "table" then
		for k, v in pairs(sit) do
			if M.num(prevSit[k]) ~= M.num(v) then
				changed[k] = true
			end
		end
	end
	local nChanged = 0
	for _ in pairs(changed) do
		nChanged += 1
	end
	local since = ""
	if prev and nChanged > 0 then
		since = if self.wide then string.format("● %d changed since #%d", nChanged, prev.n)
			else string.format("● %d changed", nChanged)
	end
	local env = section(parent, "Observation · environment state", since, C.accent)
	if type(sit) == "table" and next(sit) ~= nil then
		local keys = {}
		for k in pairs(sit) do
			table.insert(keys, tostring(k))
		end
		table.sort(keys)
		for _, k in ipairs(keys) do
			local ch = changed[k] == true
			local shown = M.num(sit[k])
			cells(env, { { k, C.muted, 130 }, { if ch then "● " .. shown else shown, if ch then C.accent else C.text2, 0 } })
		end
	elseif sit ~= nil then
		cells(env, { { M.num(sit), C.text2, 0 } })
	else
		cells(env, { { "—", C.muted, 0 } })
	end
	-- the current action
	local cur = section(parent, "Current action")
	local nd = s.now_doing
	if type(nd) == "table" then
		local action = tostring(nd.option or "")
		cells(cur, { { string.format("%s for %s", action, M.secs(nd.for_seconds)), run:colorOf(action, T.ACTION_COLORS),
			0 } }, 12)
	else
		cells(cur, { { "none", C.muted, 0 } }, 12)
	end
	-- what the recent actions did
	local rec = section(parent, "Recent actions · outcomes")
	if type(s.recent) == "table" and #s.recent > 0 then
		for _, r in ipairs(s.recent) do
			if type(r) == "table" then
				local action = tostring(r.option or "")
				local worked = r.worked
				cells(rec, { { action, run:colorOf(action, T.ACTION_COLORS), 110 }, { M.secs(r.seconds), C.text2, 44 },
					{ if worked == true then "success" elseif worked == false then "failure" else "unknown",
						if worked == true then C.success elseif worked == false then C.red else C.muted, 56 },
					{ M.scrub(tostring(r.how or "")), C.text3, 0, "body" } })
			end
		end
	else
		cells(rec, { { "none yet", C.muted, 0 } })
	end
	-- the action space: an entry per action, the chosen one selected
	local opts = s.options
	if type(opts) == "table" then
		local names = {}
		for k in pairs(opts) do
			table.insert(names, tostring(k))
		end
		table.sort(names)
		local sp = section(parent, string.format("Action space · %d", #names), "π(a | s)", C.muted)
		for _, action in ipairs(names) do
			local p = tonumber(st.probs[action]) or 0
			local chosen = action == st.chosen
			local b = T.frame(sp, "Action", if chosen then C.rowActive else nil)
			local inner = T.row(b, 10)
			T.pad(inner, 10, 7, 10, 7)
			if chosen then
				T.leftEdge(b, C.accent)
			end
			local col = T.col(inner, 1)
			col.Size = UDim2.new(0, 0, 0, 0)
			T.fill(col)
			T.label(col, action .. (if action == st.current then " · current" else ""), "mono", { size = 12,
				font = if chosen then T.F.monoMedium else nil, color = run:colorOf(action, T.ACTION_COLORS) })
			if tostring(criteria[action] or "") ~= "" then
				T.label(col, tostring(criteria[action]), "small")
			end
			local facts = opts[action]
			if type(facts) == "table" and next(facts) ~= nil then
				local bits = {}
				for k, v in pairs(facts) do
					table.insert(bits, string.format("%s: %s", tostring(k), M.num(v)))
				end
				table.sort(bits)
				T.label(col, table.concat(bits, " · "), "monoDim")
			elseif facts ~= nil and type(facts) ~= "table" then
				T.label(col, M.num(facts), "monoDim")
			end
			T.label(inner, M.prob(p), "mono", { wrap = false, size = 14, font = if chosen then T.F.monoMedium else nil,
				color = if chosen then C.accent else C.text3 })
		end
	end
	-- what else the policy answered
	local out = st.frame.output
	local answers = if type(out) == "table" and type(out.answers) == "table" then out.answers else {}
	local others = {}
	for k in pairs(answers) do
		if k ~= "pick" then
			table.insert(others, tostring(k))
		end
	end
	if #others > 0 then
		table.sort(others)
		local oth = section(parent, "Other outputs")
		for _, k in ipairs(others) do
			cells(oth, { { k, C.muted, 130 }, { M.num(answers[k]), C.text2, 0 } })
		end
	end
end

return PolicyLog
