--!strict
-- What the Autopilot dock shows, without any UI (as Godot's ui/model.gd): a run of the history (its feed, stages,
-- harness attempts, findings, policy steps and result), the agent service's status lines translated into stages, the
-- policy steps (its "jev" frames) parsed into attempts and action tags, the run as it is kept in the plugin's settings,
-- and the run report as markdown.
local HttpService = game:GetService("HttpService")

local M = {}

-- After a rollout the results are processed until the next rollout or the report.
M.STAGES = { "Launch", "Analyze", "Probe env", "Build harness", "Rollout", "Process result", "Report" }
M.STAGE_ROLLOUT = 4
M.STAGE_RESULTS = 5
M.STAGE_REPORT = 6
-- The Feed tab's heading for each stage (0-based like the stages)
M.STAGE_HEADINGS = { [0] = "Launch", "Analyze", "Probe", "Build harness", "Play", "Results", "Report" }
M.MAX_FEED = 2000
-- An activity frame's `stage` -> the stage
M.ACTIVITY_STAGES = { analyze = 1, probe = 2, build = 3, play = 4, results = 5, report = 6 }
M.DETAIL_LABELS = { probe = "the code", write = "what was written", episode = "what was asked", look = "the questions",
	report = "the summary", read = "the path", search = "the pattern", refine = "the edit and its evidence" }
M.RESULT_LABELS = { probe = "the output", episode = "the summary", look = "the description" }
M.TAGS = { started = "START", kept = "KEEP", switched = "SWITCH", kept_running = "HELD", dropped = "DROPPED" }
M.TAG_ORDER = { "SWITCH", "START", "KEEP", "HELD", "DROPPED", "FAILED" }
M.TAG_PAST = { START = "Started", KEEP = "Kept", SWITCH = "Switched", HELD = "Held", DROPPED = "Dropped",
	FAILED = "Failed" }
M.KINDS = { verify = true, goal = true, explore = true }
M.MEMORY_UPDATED = "Memory updated"
M.STAGE_LINES = { ["Launching the environment"] = true, ["Analyzing the project"] = true,
	["Probing the environment"] = true, ["Building the harness"] = true, ["Processing results"] = true,
	["Saving what it learned to memory"] = true }
M.TIME_LIMIT_DEFAULT = 30

local MONTHS = { "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec" }

function M.clock(seconds: number): string
	local s = math.max(0, math.floor(seconds or 0))
	if s >= 3600 then
		return string.format("%d:%02d:%02d", s // 3600, (s // 60) % 60, s % 60)
	end
	return string.format("%02d:%02d", s // 60, s % 60)
end

function M.shortClock(seconds: number): string
	local s = math.max(0, math.floor(seconds or 0))
	if s >= 3600 then
		return string.format("%d:%02d:%02d", s // 3600, (s // 60) % 60, s % 60)
	end
	return string.format("%d:%02d", s // 60, s % 60)
end

-- When a run started, as shown everywhere: "Oct 08 13:25" (local time).
function M.when(unix: number): string
	local d = os.date("*t", math.floor(unix or 0)) :: any
	return string.format("%s %02d %02d:%02d", MONTHS[math.clamp(d.month, 1, 12)], d.day, d.hour, d.min)
end

function M.wallTime(unix: number): string
	local d = os.date("*t", math.floor(unix)) :: any
	return string.format("%02d:%02d:%02d.%03d", d.hour, d.min, d.sec, math.floor((unix % 1) * 1000))
end

function M.attemptsText(n: number): string
	return string.format("%d attempt%s", n, if n == 1 then "" else "s")
end

function M.sentence(word: string): string
	return string.upper(string.sub(word, 1, 1)) .. string.lower(string.sub(word, 2))
end

function M.trim(s: any): string
	return string.match(tostring(s or ""), "^%s*(.-)%s*$") or ""
end

-- Words the dock never shows, in any text it didn't write itself.
function M.scrub(text: string): string
	local t = tostring(text or "")
	t = string.gsub(t, "playtest service", "agent service")
	t = string.gsub(t, "Playtest service", "Agent service")
	t = string.gsub(t, "the brain", "the agent service")
	t = string.gsub(t, "The brain", "The agent service")
	return t
end

function M.num(v: any): string
	if type(v) == "number" then
		if v == math.floor(v) and math.abs(v) < 1e9 then
			return tostring(math.floor(v))
		end
		return tostring(math.floor(v * 100 + 0.5) / 100)
	elseif type(v) == "string" then
		return v
	elseif v == nil then
		return "—"
	end
	local ok, s = pcall(function()
		return HttpService:JSONEncode(v)
	end)
	return if ok then s else tostring(v)
end

function M.prob(p: number): string
	local s = string.format("%.2f", math.clamp(p, 0, 1))
	return (string.gsub(s, "^0", ""))
end

function M.secs(v: any): string
	if type(v) == "number" then
		return string.format("%.1f s", v)
	end
	return "—"
end

-- Whether a detail is better folded than shown: more than one line, or long.
function M.folds(detail: string): boolean
	return detail ~= "" and (string.find(detail, "\n", 1, true) ~= nil or #detail > 80)
end

function M.foldLabel(label: string, open: boolean): string
	return (if open then "– hide " else "+ inspect ") .. label
end

-- A status line as shown: {text, stage (-1: none), attempt (0: none), now}.
function M.translate(line: string): any
	local out = { text = line, stage = -1, attempt = 0, now = false }
	local map = {
		["Saving what it learned to memory"] = { line, M.STAGE_REPORT },
		["Starting the game"] = { "Launching the environment", 0 },
		["Reading the game's code"] = { "Analyzing the project", 1 },
		["Trying things in the game"] = { "Probing the environment", 2 },
		["Writing the harness"] = { "Building the harness", 3 },
		["Looking at the results"] = { "Processing results", M.STAGE_RESULTS },
	}
	local m = map[line]
	if m then
		out.text, out.stage = m[1], m[2]
		return out
	end
	local n = string.match(line, "^Trying the harness %(run (%d+)%)$")
	if n then
		out.attempt = tonumber(n) or 0
		out.text = "Harness " .. n
		out.stage = M.STAGE_ROLLOUT
		return out
	end
	if string.sub(line, 1, 5) == "Now: " then
		out.now = true
		return out
	end
	out.text = M.scrub(line)
	return out
end

-- --- the run ----------------------------------------------------------------------------------------------------------

local Run = {}
Run.__index = Run
M.Run = Run

function M.newRun(kind: string, prompt: string): any
	local r = setmetatable({}, Run) :: any
	r.id = os.date("%Y%m%d-%H%M%S") :: string
	r.kind = kind or ""
	r.prompt = prompt or ""
	r.request = nil -- the start frame sent (Run again)
	r.started = os.time()
	r.clock0 = os.clock()
	r.ended = nil -- os.clock() when it ended (nil: live)
	r.seconds = nil -- how long it took (a run kept from before)
	r.feed = {}
	r.stage = 0
	r.stageSince = 0
	r.stageSecs = {}
	r.attempts = 0
	r.nowText = ""
	r.findings = {}
	r.notABug = {}
	r.result = nil
	r.steps = {}
	r.stepCount = 0
	r.stopAsked = false
	r.lostConnection = false
	r.usage = nil
	r.bill = nil
	r.origin = ""
	r.lastActivityId = 0
	r.colors = {}
	r.colorCount = 0
	r.project = nil
	return r
end

function Run:live(): boolean
	return self.ended == nil and self.seconds == nil
end

function Run:elapsed(): number
	if self.seconds ~= nil and self.ended == nil then
		return self.seconds
	end
	return (self.ended or os.clock()) - (self.clock0 or os.clock())
end

function Run:took(): number
	local r = self.result
	if type(r) == "table" and type(r.seconds) == "number" then
		return r.seconds
	end
	return self:elapsed()
end

function Run:attemptCount(): number
	local r = self.result
	if type(r) == "table" and type(r.attempts) == "number" then
		return r.attempts
	end
	return self.attempts
end

function Run:steerable(): boolean
	return self:live() and self.stage == M.STAGE_ROLLOUT
end

function Run:verdict(): string
	if self:live() then
		return "LIVE"
	end
	local r = self.result
	if type(r) ~= "table" then
		return if self.stopAsked then "STOPPED" else "NO RESULT"
	end
	if r.verdict ~= nil then
		return string.upper(tostring(r.verdict))
	end
	if r.error ~= nil and r.reached == nil then
		return "ERROR"
	end
	if r.tested == true or self.tested then
		return "GAME TESTED"
	end
	if self.kind == "explore" then
		return "EXPLORED"
	end
	if r.reached ~= nil then
		return if r.reached then "GOAL HIT" else "GOAL MISSED"
	end
	if r.achieved ~= nil then
		return if r.achieved then "GOAL HIT" else "GOAL MISSED"
	end
	return "DONE"
end

function Run:shownPrompt(): string
	if M.trim(self.prompt) ~= "" then
		return self.prompt
	end
	if self.kind == "explore" then
		return "Explore"
	elseif self.kind == "goal" then
		return "Play a goal"
	elseif self.kind == "verify" then
		return "Verify"
	end
	return "Run"
end

function Run:kindWord(): string
	if self.kind == "verify" then
		return "VERIFY"
	elseif self.kind == "goal" then
		return "GOAL"
	elseif self.kind == "explore" then
		return "EXPLORE"
	end
	return "RUN"
end

function Run:originLine(): string
	if self.origin == "" or self.origin == nil then
		return ""
	elseif self.origin == "claude-code" then
		return "Started from Claude Code"
	elseif self.origin == "codex" then
		return "Started from Codex"
	end
	return "Started from your coding agent"
end

function Run:reason(): string
	local r = self.result
	if type(r) ~= "table" then
		return ""
	end
	for _, k in ipairs({ "reason", "summary" }) do
		if type(r[k]) == "string" and r[k] ~= "" then
			return r[k]
		end
	end
	if type(r.notes) == "table" and r.notes[1] ~= nil then
		return tostring(r.notes[1])
	end
	if type(r.error) == "string" then
		return r.error
	end
	return ""
end

function Run:evidence(): { string }
	local r = self.result
	local out = {}
	if type(r) ~= "table" then
		return out
	end
	if type(r.evidence) == "table" then
		for _, e in ipairs(r.evidence) do
			table.insert(out, tostring(e))
		end
		return out
	elseif type(r.evidence) == "string" and r.evidence ~= "" then
		for line in string.gmatch(r.evidence, "[^\n]+") do
			table.insert(out, line)
		end
		return out
	end
	if self.kind ~= "explore" and type(r.notes) == "table" then
		for i = 2, #r.notes do
			table.insert(out, tostring(r.notes[i]))
		end
	end
	return out
end

function Run:changes(): { string }
	local out = {}
	local r = self.result
	if type(r) == "table" and type(r.changes) == "table" then
		for _, c in ipairs(r.changes) do
			if type(c) == "table" and M.trim(c.text) ~= "" then
				table.insert(out, M.trim(c.text))
			end
		end
	end
	return out
end

function Run:alerts(): { string }
	local out = {}
	local r = self.result
	if type(r) == "table" and type(r.alerts) == "table" then
		for _, a in ipairs(r.alerts) do
			if type(a) == "string" and M.trim(a) ~= "" then
				table.insert(out, M.trim(a))
			end
		end
	end
	return out
end

function Run:memoryUpdated(): boolean
	local r = self.result
	return type(r) == "table" and type(r.memory) == "table" and r.memory.updated == true
end

-- The run's recording ({video, seconds, episode, frames, fps}), nil when it has none.
function Run:recording(): any
	local r = self.result
	if type(r) ~= "table" or type(r.recording) ~= "table" then
		return nil
	end
	local rec = r.recording
	local video = if type(rec.video) == "string" then rec.video else ""
	local frames = if type(rec.frames) == "string" then rec.frames else ""
	if video == "" and frames == "" then
		return nil
	end
	return { video = video, frames = frames, seconds = tonumber(rec.seconds) or 0,
		fps = tonumber(rec.frames_fps) or 0, episode = tonumber(rec.episode) }
end

function Run:noRecordingLine(): string
	local r = self.result
	if self:live() or type(r) ~= "table" or type(r.recording) == "table" then
		return ""
	end
	local why = r.no_recording
	if type(why) ~= "string" or M.trim(why) == "" then
		return ""
	end
	return "No recording: " .. M.trim(why)
end

function M.isEngine(f: any): boolean
	local source = tostring(f.source or "")
	return source == "engine" or (source == "" and f.detector ~= nil)
end

function Run:agentFindings(): { string }
	local out = {}
	local r = self.result
	if type(r) == "table" then
		if self.kind == "explore" and type(r.notes) == "table" then
			for i = 2, #r.notes do
				table.insert(out, tostring(r.notes[i]))
			end
		end
		if type(r.findings) == "table" then
			for _, f in ipairs(r.findings) do
				table.insert(out, if type(f) == "table" then tostring(f.summary or "") else tostring(f))
			end
		end
	end
	for _, f in ipairs(self.findings) do
		if not M.isEngine(f) and not table.find(out, tostring(f.summary or "")) then
			table.insert(out, tostring(f.summary or ""))
		end
	end
	return out
end

function Run:engineFindings(): { any }
	local out = {}
	for _, f in ipairs(self.findings) do
		if M.isEngine(f) then
			table.insert(out, f)
		end
	end
	return out
end

function Run:addFeed(text: string, kind: string)
	self:addLine({ t = self:elapsed(), text = text, kind = kind })
end

function Run:addLine(e: any)
	if e.stage == nil then
		e.stage = self.stage
	end
	table.insert(self.feed, e)
	self.feedTotal = (self.feedTotal or 0) + 1 -- (every line added: the feed keeps only the latest)
	while #self.feed > M.MAX_FEED do
		table.remove(self.feed, 1)
	end
end

function Run:findActivity(id: number): any
	if id <= 0 then
		return nil
	end
	for i = #self.feed, 1, -1 do
		local e = self.feed[i]
		if e.aid == id or e.rid == id then
			return e
		end
	end
	return nil
end

function Run:enterStage(s: number)
	local t = self:elapsed()
	self.stageSecs[self.stage] = (self.stageSecs[self.stage] or 0) + t - (self.stageSince or 0)
	self.stage = s
	self.stageSince = t
end

function Run:stageTimes(): { [number]: number }
	local out = {}
	for k, v in pairs(self.stageSecs) do
		out[k] = v
	end
	if self:live() then
		out[self.stage] = (out[self.stage] or 0) + self:elapsed() - (self.stageSince or 0)
	end
	return out
end

-- One event of the activity feed (an `activity` frame): a line, or, when it ends a call (`ends`), that call's result
-- folded into its line. Whether anything changed (an id seen already changes nothing).
function Run:onActivity(frame: any): boolean
	local id = tonumber(frame.id) or 0
	if id <= 0 or self:findActivity(id) ~= nil then
		return false
	end
	local text = M.trim(M.scrub(tostring(frame.text or "")))
	local akind = tostring(frame.kind or "")
	local detail = if type(frame.detail) == "string" then M.scrub(frame.detail) else ""
	local ends = tonumber(frame.ends) or 0
	self.lastActivityId = math.max(self.lastActivityId or 0, id)
	local s = M.ACTIVITY_STAGES[tostring(frame.stage or "")] or -1
	if self:live() and s >= 0 and s ~= self.stage then
		self:enterStage(s)
	end
	if ends > 0 then
		local call = self:findActivity(ends)
		if call ~= nil then
			if (call.rid or 0) == 0 then
				call.result = text
				call.rdetail = detail
				call.rid = id
			end
			return true
		end
	end
	self:addLine({ t = self:elapsed(), text = text, kind = "activity", stage = if s >= 0 then s else self.stage,
		aid = id, akind = akind, detail = detail })
	return true
end

-- A status line from the agent service (translated).
function Run:onStatus(tr: any)
	if tr.stage >= 0 then
		self:enterStage(tr.stage)
	end
	if tr.stage == M.STAGE_RESULTS then
		self.nowText = ""
	end
	if tr.attempt > 0 then
		self.attempts += 1
		self:addFeed(tr.text, "attempt")
		return
	end
	if tr.now then
		self.nowText = string.sub(tr.text, 6)
		if self.stage < M.STAGE_ROLLOUT then
			self:enterStage(M.STAGE_ROLLOUT)
		end
		self:addFeed(tr.text, "now")
		return
	end
	self:addFeed(tr.text, "status")
end

function Run:onFinding(f: any): boolean
	local sig = tostring(f.signature or f.summary or "")
	for i, g in ipairs(self.findings) do
		if tostring(g.signature or g.summary or "") == sig then
			self.findings[i] = f
			return false
		end
	end
	table.insert(self.findings, f)
	return true
end

function Run:colorOf(action: string, palette: { Color3 }): Color3
	if self.colors[action] == nil then
		self.colorCount = (self.colorCount or 0) + 1
		self.colors[action] = palette[(self.colorCount - 1) % #palette + 1]
	end
	return self.colors[action]
end

-- A policy step (the agent service's "jev" frame): {frame, attempt, n, tag, chosen, current, plan, t, wall, ms, probs,
-- state, error}.
function Run:addStep(frame: any, palette: { Color3 }, keep: number): any
	local st: any = { frame = frame }
	st.n = tonumber(frame.call) or tonumber(frame.step) or (#self.steps + 1)
	local prev = self.steps[#self.steps]
	st.attempt = if prev == nil then 1 elseif st.n < prev.n then prev.attempt + 1 else prev.attempt
	st.t = tonumber(frame.t) or 0
	st.wall = tonumber(frame.wall) or 0
	local secs = frame.seconds
	st.ms = if type(secs) == "table" and tonumber(secs.jev) then tonumber(secs.jev) * 1000 else 0
	local decided = if type(frame.decided) == "table" then frame.decided else {}
	local summary = tostring(decided.summary or "")
	st.chosen = ""
	if type(decided.chosen) == "table" and decided.chosen[1] ~= nil then
		st.chosen = tostring(decided.chosen[1])
	end
	local action, rest = string.match(summary, "^(.-): (.*)$")
	action = action or summary
	st.tag = M.TAGS[action] or string.upper(action)
	if st.chosen == "" and rest and rest ~= "None" then
		st.chosen = rest
	end
	local input = frame.input
	st.state = if type(input) == "table" and type(input.state) == "table" then input.state else {}
	local nd = st.state.now_doing
	st.current = if type(nd) == "table" then tostring(nd.option or "") else ""
	st.plan = if type(frame.plan_step) == "string" then frame.plan_step else ""
	local out = frame.output
	st.probs = if type(out) == "table" and type(out.answers) == "table" and type(out.answers.pick) == "table"
		then out.answers.pick else {}
	if st.current ~= "" then
		self:colorOf(st.current, palette)
	end
	if st.chosen ~= "" then
		self:colorOf(st.chosen, palette)
	end
	for _, pair in ipairs(M.sortedProbs(st)) do
		self:colorOf(pair[1], palette)
	end
	st.error = if type(frame.error) == "string" then frame.error else ""
	if st.error ~= "" then
		st.tag = "FAILED"
	end
	table.insert(self.steps, st)
	self.stepCount = (self.stepCount or 0) + 1
	while #self.steps > keep do
		table.remove(self.steps, 1)
	end
	return st
end

function M.sortedProbs(st: any): { any }
	local out = {}
	for k, v in pairs(st.probs or {}) do
		table.insert(out, { tostring(k), tonumber(v) or 0 })
	end
	table.sort(out, function(a, b)
		return a[2] > b[2]
	end)
	return out
end

function M.stepLabel(st: any): string
	return string.format("H%d #%d", st.attempt, st.n)
end

function Run:previousStep(st: any): any
	local i = table.find(self.steps, st)
	if i and i > 1 and self.steps[i - 1].attempt == st.attempt then
		return self.steps[i - 1]
	end
	return nil
end

function Run:stepAttempts(): number
	local last = self.steps[#self.steps]
	return if last then last.attempt else 0
end

-- --- kept in the plugin's settings ------------------------------------------------------------------------------------

-- The run as the history keeps it (no policy steps, the feed without its details: plugin settings are kept small).
function Run:saved(): any
	local feed = {}
	for i = math.max(1, #self.feed - 400 + 1), #self.feed do -- (the latest lines: plugin settings are kept small)
		local e = self.feed[i]
		table.insert(feed, { t = e.t, text = e.text, kind = e.kind, stage = e.stage, akind = e.akind, aid = e.aid,
			result = e.result, tone = e.tone })
	end
	local stageSecs = {}
	for k, v in pairs(self.stageSecs) do
		stageSecs[tostring(k)] = v -- (string keys: a plugin setting keeps no table keyed 0..6)
	end
	return { id = self.id, kind = self.kind, prompt = self.prompt, request = self.request, started = self.started,
		seconds = math.floor(self:elapsed() * 10) / 10, result = self.result, findings = self.findings,
		notABug = self.notABug, feed = feed, attempts = self.attempts, stage = self.stage, stageSecs = stageSecs,
		stepCount = self.stepCount, stopAsked = self.stopAsked, usage = self.usage, bill = self.bill,
		origin = self.origin, project = self.project, tested = self.tested, lostConnection = self.lostConnection }
end

-- A run from the history (this dock's, or an older dock's: {prompt, kind, tested, started, seconds, result, findings,
-- events, usage}).
function M.fromSaved(t: any): any
	local r = M.newRun(tostring(t.kind or ""), tostring(t.prompt or ""))
	r.id = if type(t.id) == "string" then t.id else os.date("%Y%m%d-%H%M%S", math.floor(tonumber(t.started) or 0)) :: string
	r.request = if type(t.request) == "table" then t.request else nil
	r.started = tonumber(t.started) or 0
	r.seconds = tonumber(t.seconds) or 0
	r.result = if type(t.result) == "table" then t.result else nil
	r.findings = if type(t.findings) == "table" then t.findings else {}
	r.notABug = if type(t.notABug) == "table" then t.notABug else {}
	r.attempts = tonumber(t.attempts) or 0
	r.stage = tonumber(t.stage) or M.STAGE_REPORT
	r.stageSecs = {}
	if type(t.stageSecs) == "table" then
		for k, v in pairs(t.stageSecs) do
			r.stageSecs[tonumber(k) or 0] = tonumber(v) or 0
		end
	end
	r.stepCount = tonumber(t.stepCount) or 0
	r.stopAsked = t.stopAsked == true
	r.usage = t.usage
	r.bill = t.bill
	r.origin = if type(t.origin) == "string" then t.origin else ""
	r.project = t.project
	r.tested = t.tested == true
	r.lostConnection = t.lostConnection == true
	if type(t.feed) == "table" then
		r.feed = t.feed
	elseif type(t.events) == "table" then
		for _, e in ipairs(t.events) do
			if not e.ends then
				table.insert(r.feed, { t = tonumber(e.t) or 0, text = tostring(e.text or ""), kind = "activity",
					akind = e.kind, aid = e.id, stage = M.ACTIVITY_STAGES[tostring(e.stage or "")] or 0 })
			end
		end
	end
	return r
end

-- --- the report -------------------------------------------------------------------------------------------------------

function M.whereText(w: any): string
	if type(w) ~= "table" then
		return ""
	end
	return string.format("%s, %s, %s", M.num(tonumber(w.X) or 0), M.num(tonumber(w.Y) or 0), M.num(tonumber(w.Z) or 0))
end

-- The run as a markdown report (the same structure in every engine).
function M.reportMarkdown(r: any): string
	local lines = { "# Autopilot run " .. r.id, "", "**Prompt:** " .. r:shownPrompt(), "",
		"**Verdict:** " .. r:verdict() .. "  ",
		string.format("**Duration:** %s · **Harnesses:** %d · **Policy steps:** %d · **Alerts:** %d", M.clock(r:took()),
			r:attemptCount(), math.max(r.stepCount or 0, #r.steps), #r:engineFindings()) }
	for _, a in ipairs(r:alerts()) do
		table.insert(lines, "")
		table.insert(lines, "> " .. M.scrub(a))
	end
	if r:reason() ~= "" then
		for _, l in ipairs({ "", "## Reason", "", M.scrub(r:reason()) }) do
			table.insert(lines, l)
		end
	end
	local ev = r:evidence()
	if #ev > 0 then
		for _, l in ipairs({ "", "## Evidence", "" }) do
			table.insert(lines, l)
		end
		for _, e in ipairs(ev) do
			table.insert(lines, "- " .. e)
		end
	end
	local ch = r:changes()
	if #ch > 0 then
		for _, l in ipairs({ "", "## Changed directly (not through play)", "" }) do
			table.insert(lines, l)
		end
		for _, c in ipairs(ch) do
			table.insert(lines, "- " .. c)
		end
	end
	local ag = r:agentFindings()
	local eng = r:engineFindings()
	if #ag > 0 or #eng > 0 then
		for _, l in ipairs({ "", "## Findings", "" }) do
			table.insert(lines, l)
		end
		for _, a in ipairs(ag) do
			table.insert(lines, "- [agent] " .. a)
		end
		for _, f in ipairs(eng) do
			local loc = M.whereText(f.where)
			local count = tonumber(f.count) or 1
			table.insert(lines, string.format("- [engine · %s · %s] %s%s%s%s", tostring(f.severity or ""),
				tostring(f.detector or ""), M.scrub(tostring(f.summary or "")),
				if count > 1 then string.format(" (×%d)", count) else "",
				if loc ~= "" then " at " .. loc .. " cm" else "",
				if r.notABug[tostring(f.signature or "")] then " (not a bug)" else ""))
		end
	end
	return table.concat(lines, "\n") .. "\n"
end

-- --- usage and bills --------------------------------------------------------------------------------------------------

-- A usage frame's models, whole numbers only.
function M.usageRows(models: any): { any }
	local rows = {}
	if type(models) ~= "table" then
		return rows
	end
	for _, m in ipairs(models) do
		if type(m) == "table" then
			local row: any = { kind = if m.kind == "decision" then "decision" else "llm", model = tostring(m.model or "") }
			for _, k in ipairs({ "calls", "input", "output", "cache_read", "cache_write" }) do
				row[k] = math.max(0, math.floor(tonumber(m[k]) or 0))
			end
			table.insert(rows, row)
		end
	end
	return rows
end

-- The amount a run's bill shows (-1: none, or one Aura didn't bill).
function M.billed(b: any): number
	if type(b) ~= "table" or type(b.usd) ~= "number" or b.billed ~= true then
		return -1
	end
	return math.max(0, b.usd)
end

function M.thousands(n: number): string
	local s = tostring(math.floor(math.abs(n)))
	local out = ""
	while #s > 3 do
		out = "," .. string.sub(s, -3) .. out
		s = string.sub(s, 1, -4)
	end
	return (if n < 0 then "-" else "") .. s .. out
end

function M.money(usd: number): string
	if usd > 0 and usd < 0.01 then
		return "< $0.01"
	end
	local cents = math.floor(math.max(0, usd) * 100 + 0.5)
	return string.format("$%s.%02d", M.thousands(cents // 100), cents % 100)
end

local function date(s: any): { number }?
	local y, mo, d = string.match(tostring(s or ""), "^(%d%d%d%d)%-(%d%d)%-(%d%d)")
	if not y then
		return nil
	end
	local m, dd = tonumber(mo) :: number, tonumber(d) :: number
	if m < 1 or m > 12 or dd < 1 or dd > 31 then
		return nil
	end
	return { tonumber(y) :: number, m, dd }
end

-- "Billing period (Oct 1 – Oct 31): $12.34 USD".
function M.periodLine(b: any): string
	local a, z = date(b.from), date(b.to)
	local dates = ""
	if a and z then
		if a[1] ~= z[1] then
			dates = string.format("%s %d, %d – %s %d, %d", MONTHS[a[2]], a[3], a[1], MONTHS[z[2]], z[3], z[1])
		else
			dates = string.format("%s %d – %s %d", MONTHS[a[2]], a[3], MONTHS[z[2]], z[3])
		end
	end
	return string.format("Billing period%s: %s USD", if dates ~= "" then " (" .. dates .. ")" else "",
		M.money(tonumber(b.usd) or 0))
end

-- The account frame's out_of_credits: {message, url}; nil while the team has credits.
function M.outOfCredits(o: any): any
	if type(o) ~= "table" or type(o.message) ~= "string" or M.trim(o.message) == "" then
		return nil
	end
	local url = if type(o.url) == "string" then M.trim(o.url) else ""
	if not (string.sub(url, 1, 8) == "https://" or string.sub(url, 1, 7) == "http://") then
		url = ""
	end
	return { message = M.trim(o.message), url = url }
end

return M
