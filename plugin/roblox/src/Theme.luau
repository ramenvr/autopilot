--!strict
-- Autopilot's look in Roblox Studio, as the other plugins' (Aura's "Flatness, dark"): flat surfaces, 1 px rules, an
-- 8 px radius on plates and square controls, mono upper-case micro-labels, one amber accent for what is interactive or
-- current. The palette and the type scale follow Godot's ui/autopilot_theme.gd; the pieces the dock is built of
-- (labels, buttons, fields, plates, tags, switches, spinners) are made here.
-- Fonts: IBM Plex Sans is not one of Studio's fonts; its Japanese cut, IBM Plex Sans JP (whose Latin letters are IBM Plex
-- Sans's), is in Roblox's font library and stands in for it. IBM Plex Mono isn't there: Roboto Mono stands in. Should
-- the library's font not load, Builder Sans (Studio's own) is used.
local RunService = game:GetService("RunService")
local TextService = game:GetService("TextService")

local Theme = {}

-- Surfaces and lines
local C = {
	ground = Color3.fromHex("121316"), -- the dock's ground
	rail = Color3.fromHex("0f1013"), -- the history rail
	plate = Color3.fromHex("181a1e"), -- cards, the feed, findings
	popover = Color3.fromHex("15171b"), -- menus, the narrow history list
	hover = Color3.fromHex("1d2026"), -- hovered, the gear while Settings is open, the neutral tag
	rowActive = Color3.fromHex("2a1b10"), -- the selected row (with a 2 px accent rule at its left)
	well = Color3.fromHex("0d0e11"), -- the prompt box, inputs, evidence
	rule = Color3.fromHex("24262b"), -- 1 px dividers
	line = Color3.fromHex("2c2f36"), -- control outlines
	lineHi = Color3.fromHex("3a3e47"),
	text = Color3.fromHex("e6e6e8"),
	text2 = Color3.fromHex("d3d5da"), -- reading text
	text3 = Color3.fromHex("a4a8b0"), -- row text, feed lines
	muted = Color3.fromHex("8b8f98"), -- muted text, micro-labels
	accent = Color3.fromHex("f0873a"),
	accentHi = Color3.fromHex("f59b57"),
	accentRest = Color3.fromHex("c4762f"),
	onAccent = Color3.fromHex("141517"), -- ink on any fill
	focus = Color3.fromHex("ffb066"),
	stepDone = Color3.fromHex("6b4122"),
	success = Color3.fromHex("74b078"),
	successFg = Color3.fromHex("b6ddb9"),
	successFill = Color3.fromHex("16281a"),
	successLine = Color3.fromHex("3d6b42"),
	red = Color3.fromHex("e26a62"),
	redFg = Color3.fromHex("f0b2ac"),
	redFill = Color3.fromHex("2c1715"),
	redLine = Color3.fromHex("7a3b36"),
	redHi = Color3.fromHex("ea8079"),
	amber = Color3.fromHex("e8a94e"),
	amberFg = Color3.fromHex("f6dcae"),
	amberFill = Color3.fromHex("2a2114"),
	amberLine = Color3.fromHex("7a5c2a"),
	info = Color3.fromHex("7aa5d8"),
	infoFg = Color3.fromHex("bcd4ee"),
	infoFill = Color3.fromHex("161f2b"),
	infoLine = Color3.fromHex("3a5578"),
	neutralFg = Color3.fromHex("c9ccd2"),
	knob = Color3.fromHex("ffffff"),
}
Theme.C = C

-- Colors for actions, given out in order of first appearance in a run
Theme.ACTION_COLORS = { C.accent, C.info, C.amber, C.success, Color3.fromHex("a08ad8"), C.red, Color3.fromHex("5fb3b3"),
	C.neutralFg }

-- The tags' colors by kind: {surface, text, border}
Theme.TAGS = {
	ok = { C.successFill, C.success, C.successLine }, bad = { C.redFill, C.red, C.redLine },
	warn = { C.amberFill, C.amber, C.amberLine }, info = { C.infoFill, C.info, C.infoLine },
	acc = { C.rowActive, C.accent, C.stepDone }, mut = { C.hover, C.neutralFg, C.line },
}

Theme.R_PLATE = 8

-- Fonts by role
local SANS_ID = 12187364147 -- IBM Plex Sans JP, in Roblox's font library
local function sans(weight: Enum.FontWeight): Font
	return Font.fromId(SANS_ID, weight)
end
local MONO_FAMILY = "rbxasset://fonts/families/RobotoMono.json"
local F = {
	body = sans(Enum.FontWeight.Regular),
	medium = sans(Enum.FontWeight.Medium),
	semibold = sans(Enum.FontWeight.SemiBold),
	mono = Font.new(MONO_FAMILY, Enum.FontWeight.Regular),
	monoMedium = Font.new(MONO_FAMILY, Enum.FontWeight.Medium),
}
Theme.F = F

-- Whether the library's font loaded (else Builder Sans stands in): checked once, then onReady is called.
Theme.fontsChecked = false
function Theme.checkFonts(onReady: () -> ())
	if Theme.fontsChecked then
		onReady()
		return
	end
	task.spawn(function()
		local ok = pcall(function()
			local p = Instance.new("GetTextBoundsParams")
			p.Text = "Autopilot"
			p.Font = F.body
			p.Size = 14
			p.Width = 1000
			TextService:GetTextBoundsAsync(p)
		end)
		if not ok then
			local builder = "rbxasset://fonts/families/BuilderSans.json"
			F.body = Font.new(builder, Enum.FontWeight.Regular)
			F.medium = Font.new(builder, Enum.FontWeight.Medium)
			F.semibold = Font.new(builder, Enum.FontWeight.SemiBold)
		end
		Theme.fontsChecked = true
		Theme.fontsFromLibrary = ok
		onReady()
	end)
end

-- The label styles: {font role, size, color}
local STYLES: { [string]: { any } } = {
	caps = { "mono", 11, C.muted }, -- micro-labels (upper case)
	mono = { "mono", 12, C.text3 },
	monoDim = { "mono", 11, C.muted },
	monoBig = { "monoMedium", 19, C.text },
	title = { "medium", 15, C.text },
	heading = { "semibold", 19, C.text },
	body = { "body", 14, C.text2 },
	dim = { "body", 14, C.text3 },
	small = { "body", 13, C.text3 },
	strong = { "medium", 14, C.text },
	tag = { "monoMedium", 11, C.text },
}
Theme.STYLES = STYLES

-- --- layout helpers -------------------------------------------------------------------------------------------------

local order = 0
local function nextOrder(): number
	order += 1
	return order
end
Theme.nextOrder = nextOrder

function Theme.pad(g: Instance, l: number, t: number?, r: number?, b: number?)
	local p = g:FindFirstChildOfClass("UIPadding") or Instance.new("UIPadding")
	p.PaddingLeft = UDim.new(0, l)
	p.PaddingTop = UDim.new(0, t or l)
	p.PaddingRight = UDim.new(0, r or l)
	p.PaddingBottom = UDim.new(0, b or t or l)
	p.Parent = g
end

function Theme.corner(g: Instance, r: number)
	local c = Instance.new("UICorner")
	c.CornerRadius = UDim.new(0, r)
	c.Parent = g
end

function Theme.stroke(g: Instance, color: Color3, thickness: number?): UIStroke
	local s = Instance.new("UIStroke")
	s.ApplyStrokeMode = Enum.ApplyStrokeMode.Border
	s.Color = color
	s.Thickness = thickness or 1
	s.Parent = g
	return s
end

-- The child fills the room its list leaves (UIFlexItem).
function Theme.fill(g: Instance, mode: Enum.UIFlexMode?)
	local f = Instance.new("UIFlexItem")
	f.FlexMode = mode or Enum.UIFlexMode.Fill
	f.Parent = g
end

function Theme.list(parent: Instance, gap: number?, horizontal: boolean?, wrap: boolean?): UIListLayout
	local l = Instance.new("UIListLayout")
	l.SortOrder = Enum.SortOrder.LayoutOrder
	l.Padding = UDim.new(0, gap or 0)
	if horizontal then
		l.FillDirection = Enum.FillDirection.Horizontal
		l.VerticalAlignment = Enum.VerticalAlignment.Center
		l.Wraps = wrap == true
	end
	l.Parent = parent
	return l
end

-- A frame: transparent unless bg is given, as wide as its parent and as tall as its content.
function Theme.frame(parent: Instance?, name: string?, bg: Color3?): Frame
	local f = Instance.new("Frame")
	f.Name = name or "Frame"
	f.BorderSizePixel = 0
	f.BackgroundColor3 = bg or C.ground
	f.BackgroundTransparency = if bg then 0 else 1
	f.Size = UDim2.new(1, 0, 0, 0)
	f.AutomaticSize = Enum.AutomaticSize.Y
	f.LayoutOrder = nextOrder()
	f.Parent = parent
	return f
end

-- A column (its children one under another, gap apart).
function Theme.col(parent: Instance?, gap: number?, name: string?): Frame
	local f = Theme.frame(parent, name or "Col")
	Theme.list(f, gap or 0)
	return f
end

-- A row (its children side by side, gap apart, centred vertically); wrap: onto more lines when narrow.
function Theme.row(parent: Instance?, gap: number?, wrap: boolean?, name: string?): Frame
	local f = Theme.frame(parent, name or "Row")
	local l = Theme.list(f, gap or 6, true, wrap)
	if wrap then
		l.Padding = UDim.new(0, gap or 6)
	end
	return f
end

-- A plate: PLATE (or bg), radius 8, a 1 px border (RULE or border), padding inside, its children gap apart.
function Theme.plate(parent: Instance?, opts: any?): Frame
	local o = opts or {}
	local f = Theme.frame(parent, o.name or "Plate", o.bg or C.plate)
	if (o.radius or Theme.R_PLATE) > 0 then
		Theme.corner(f, o.radius or Theme.R_PLATE)
	end
	Theme.stroke(f, o.border or C.rule)
	local p = o.pad or { 14, 12, 14, 12 }
	Theme.pad(f, p[1], p[2], p[3], p[4])
	if o.horizontal then
		Theme.list(f, o.gap or 10, true, o.wrap)
	else
		Theme.list(f, o.gap or 10)
	end
	return f
end

-- A bar with a 1 px rule on one side (a header's under it, a footer's above it): {outer, inner}. inner holds the
-- content, padded (pad: {l, t, r, b}), its children in a row (horizontal) or a column, gap apart.
function Theme.bar(parent: Instance?, opts: any?): (Frame, Frame)
	local o = opts or {}
	local outer = Theme.frame(parent, o.name or "Bar", o.bg or C.ground)
	Theme.list(outer, 0)
	local top = if o.side == "top" then Theme.rule(outer, o.edge) else nil
	local inner = Theme.frame(outer, "Inner")
	local p = o.pad or { 16, 12, 16, 12 }
	Theme.pad(inner, p[1], p[2], p[3], p[4])
	if o.horizontal then
		Theme.list(inner, o.gap or 8, true, o.wrap)
	else
		Theme.list(inner, o.gap or 0)
	end
	if o.side ~= "top" and o.side ~= "none" then
		Theme.rule(outer, o.edge)
	end
	if top then
		top.LayoutOrder = 0
	end
	return outer, inner
end

-- A selected row's accent rule: 2 px down its left side (g is laid out by no list: its content is in a child).
function Theme.leftEdge(g: Instance, color: Color3): Frame
	local e = Instance.new("Frame")
	e.Name = "LeftEdge"
	e.BorderSizePixel = 0
	e.BackgroundColor3 = color
	e.Size = UDim2.new(0, 2, 1, 0)
	e.ZIndex = 2
	e.Parent = g
	return e
end

function Theme.clear(f: Instance)
	for _, c in ipairs(f:GetChildren()) do
		if not (c:IsA("UIListLayout") or c:IsA("UIPadding") or c:IsA("UICorner") or c:IsA("UIStroke")
			or c:IsA("UISizeConstraint") or c:IsA("UIFlexItem") or c:IsA("UIGridLayout")) then
			c:Destroy()
		end
	end
end

-- --- text -----------------------------------------------------------------------------------------------------------

-- A label in a style (STYLES). opts: color, size, wrap (default true; false: one line as wide as its text, or cut
-- short with "…" when it has a width), rich, fill (takes the room its row leaves), truncate, align ("right",
-- "center"), name, font.
function Theme.label(parent: Instance?, text: string, style: string?, opts: any?): TextLabel
	local o = opts or {}
	local st = STYLES[style or "body"] or STYLES.body
	local l = Instance.new("TextLabel")
	l.Name = o.name or "Label"
	l.BackgroundTransparency = 1
	l.BorderSizePixel = 0
	l.Text = if (style == "caps" or style == "tag") and o.rich ~= true then string.upper(text) else text
	l.FontFace = o.font or (F :: any)[st[1]]
	l.TextSize = o.size or st[2]
	l.TextColor3 = o.color or st[3]
	l.RichText = o.rich == true
	l.TextXAlignment = if o.align == "right" then Enum.TextXAlignment.Right
		elseif o.align == "center" then Enum.TextXAlignment.Center else Enum.TextXAlignment.Left
	l.TextYAlignment = Enum.TextYAlignment.Center
	l.LayoutOrder = nextOrder()
	local wrap = o.wrap ~= false
	if wrap then
		l.TextWrapped = true
		l.Size = UDim2.new(1, 0, 0, 0)
		l.AutomaticSize = Enum.AutomaticSize.Y
		l.TextYAlignment = Enum.TextYAlignment.Top
	elseif o.fill or o.truncate then
		l.TextWrapped = false
		l.TextTruncate = Enum.TextTruncate.AtEnd
		l.Size = UDim2.new(0, 0, 0, math.ceil(l.TextSize * 1.35))
		if o.fill then
			Theme.fill(l)
		end
	else
		l.TextWrapped = false
		l.Size = UDim2.new(0, 0, 0, math.ceil(l.TextSize * 1.35))
		l.AutomaticSize = Enum.AutomaticSize.X
	end
	l.Parent = parent
	return l
end

function Theme.esc(s: string): string
	return (string.gsub(string.gsub(string.gsub(s, "&", "&amp;"), "<", "&lt;"), ">", "&gt;"))
end

function Theme.hex(c: Color3): string
	return "#" .. c:ToHex()
end

-- Rich text in a color.
function Theme.rt(text: string, c: Color3): string
	return '<font color="' .. Theme.hex(c) .. '">' .. Theme.esc(text) .. "</font>"
end

-- --- controls -------------------------------------------------------------------------------------------------------

-- The buttons: {fill, hovered fill, text, hovered text, border} (nil fill: transparent; nil border: none)
local BUTTONS: { [string]: { any } } = {
	default = { nil, C.hover, C.text, C.text, C.line },
	primary = { C.accent, C.accentHi, C.onAccent, C.onAccent, C.accent },
	quiet = { nil, C.hover, C.muted, C.text, nil },
	danger = { nil, C.redFill, C.red, C.red, nil },
	dangerFill = { C.red, C.redHi, C.onAccent, C.onAccent, C.red },
	on = { C.hover, C.hover, C.accent, C.accent, C.line }, -- (the gear while the Settings page is open)
	drop = { C.rail, C.hover, C.text, C.text, C.line }, -- (the narrow dock's history button)
	field = { C.well, C.well, C.text, C.text, C.line }, -- (a dropdown: a field)
}
Theme.BUTTONS = BUTTONS

local function paint(b: TextButton, variant: string, hovered: boolean)
	local s = BUTTONS[variant] or BUTTONS.default
	-- (a disabled or greyed button: drawn at 45% and never hovered)
	local dim = b:GetAttribute("disabled") == true or b:GetAttribute("greyed") == true
	if dim then
		hovered = false
	end
	local fill = if hovered then s[2] else s[1]
	b.BackgroundColor3 = fill or C.ground
	b.BackgroundTransparency = if fill then (if dim then 0.55 else 0) else 1
	b.TextColor3 = if hovered then s[4] else s[3]
	b.TextTransparency = if dim then 0.55 else 0
	local stroke = b:FindFirstChild("Outline") :: UIStroke?
	if stroke then
		stroke.Enabled = s[5] ~= nil
		stroke.Color = s[5] or C.line
		stroke.Transparency = if dim then 0.55 else 0
	end
end

-- A button: sentence case, 28 tall (small: 24), square, in a variant (BUTTONS). Pressing it calls onClick (its
-- errors warned, never thrown). A greyed button (Theme.grey) still calls it: the dock says why on the status line.
function Theme.button(parent: Instance?, text: string, variant: string?, onClick: (() -> ())?, small: boolean?,
	opts: any?): TextButton
	local o = opts or {}
	local v = variant or "default"
	local b = Instance.new("TextButton")
	b.Name = o.name or text
	b.Text = text
	b.FontFace = F.medium
	b.TextSize = if small then 12 else 13
	b.AutoButtonColor = false
	b.BorderSizePixel = 0
	b.Size = UDim2.new(0, o.width or 0, 0, if small then 24 else 28)
	b.AutomaticSize = if o.width then Enum.AutomaticSize.None else Enum.AutomaticSize.X
	b.LayoutOrder = nextOrder()
	Theme.pad(b, if small then 9 else 12, 0, if small then 9 else 12, 0)
	local stroke = Theme.stroke(b, C.line)
	stroke.Name = "Outline"
	b:SetAttribute("variant", v)
	paint(b, v, false)
	b.MouseEnter:Connect(function()
		if not b:GetAttribute("disabled") then
			paint(b, b:GetAttribute("variant") :: string, true)
		end
	end)
	b.MouseLeave:Connect(function()
		paint(b, b:GetAttribute("variant") :: string, false)
	end)
	b.MouseButton1Click:Connect(function()
		if b:GetAttribute("disabled") or onClick == nil then
			return
		end
		local ok, err = pcall(onClick :: () -> ())
		if not ok then
			warn("[Autopilot] " .. tostring(err))
		end
	end)
	b.Parent = parent
	return b
end

function Theme.setVariant(b: TextButton, variant: string)
	b:SetAttribute("variant", variant)
	paint(b, variant, false)
end

-- A button that can't act now because of state (not connected, a run going): greyed (as a disabled one) but still
-- pressable, so that pressing it can say why.
function Theme.grey(b: TextButton, on: boolean)
	b:SetAttribute("greyed", on)
	paint(b, (b:GetAttribute("variant") or "default") :: string, false)
end

-- A button that can't be pressed now.
function Theme.disable(b: TextButton, on: boolean)
	b:SetAttribute("disabled", on)
	paint(b, (b:GetAttribute("variant") or "default") :: string, false)
end

-- A square button that is only a glyph (an icon in the other plugins).
function Theme.iconButton(parent: Instance?, glyph: string, variant: string?, onClick: (() -> ())?, small: boolean?,
	name: string?): TextButton
	local side = if small then 24 else 28
	local b = Theme.button(parent, glyph, variant, onClick, small, { width = side, name = name or glyph })
	Theme.pad(b, 0, 0, 0, 0)
	b.FontFace = F.monoMedium
	b.TextSize = 14
	return b
end

-- The ⋮ button: three 3 px squares (no font Studio has draws the glyph square and centred).
function Theme.moreButton(parent: Instance?, onClick: () -> (), small: boolean?): TextButton
	local b = Theme.iconButton(parent, "", "quiet", onClick, small, "More")
	for i = -1, 1 do
		local d = Instance.new("Frame")
		d.BorderSizePixel = 0
		d.Size = UDim2.fromOffset(3, 3)
		d.AnchorPoint = Vector2.new(0.5, 0.5)
		d.Position = UDim2.new(0.5, 0, 0.5, i * 5)
		d.BackgroundColor3 = C.muted
		d.Name = "Dot"
		d.Parent = b
	end
	local function tint()
		for _, d in ipairs(b:GetChildren()) do
			if d.Name == "Dot" then
				(d :: Frame).BackgroundColor3 = b.TextColor3
			end
		end
	end
	b:GetPropertyChangedSignal("TextColor3"):Connect(tint)
	tint()
	return b
end

-- The gear (Settings), drawn (Studio's fonts have no gear glyph): a solid body with six square teeth round it (three
-- bars through the centre, 60° apart, reaching past the body) and a hole in the button's own colour. (The dock lays
-- out by Sibling: a later sibling with a higher ZIndex draws over an earlier one; nothing here has a stroke, which
-- Studio draws outside a frame and would cover the teeth.)
local GEAR_BODY = 11 -- the body's diameter
local GEAR_SPAN = 17 -- tip to tip across the teeth
local GEAR_TOOTH = 4 -- a tooth's width
local GEAR_HOLE = 5 -- the hole's diameter
function Theme.gearButton(parent: Instance?, onClick: () -> (), small: boolean?): TextButton
	local b = Theme.iconButton(parent, "", "quiet", onClick, small, "Settings")
	local function part(name: string, w: number, h: number, z: number, round: boolean, rotation: number?): Frame
		local f = Instance.new("Frame")
		f.Name = name
		f.BorderSizePixel = 0
		f.Size = UDim2.fromOffset(w, h)
		f.AnchorPoint = Vector2.new(0.5, 0.5)
		f.Position = UDim2.fromScale(0.5, 0.5)
		f.Rotation = rotation or 0
		f.ZIndex = z
		if round then
			Theme.corner(f, math.ceil(w / 2))
		end
		f.Parent = b
		return f
	end
	local inks = {}
	for i = 0, 2 do
		table.insert(inks, part("Tooth", GEAR_SPAN, GEAR_TOOTH, 1, false, i * 60))
	end
	table.insert(inks, part("Body", GEAR_BODY, GEAR_BODY, 2, true))
	local hole = part("Hole", GEAR_HOLE, GEAR_HOLE, 3, true)
	-- (the hole shows what is behind the button: its own colour while hovered or on, else the nearest opaque ancestor's,
	-- the rail or the top bar)
	local function behind(): Color3
		local g: Instance? = b
		while g ~= nil and g:IsA("GuiObject") do
			if (g :: GuiObject).BackgroundTransparency < 1 then
				return (g :: GuiObject).BackgroundColor3
			end
			g = g.Parent
		end
		return C.ground
	end
	local function tint()
		for _, f in ipairs(inks) do
			f.BackgroundColor3 = b.TextColor3
		end
		hole.BackgroundColor3 = behind()
	end
	b.AncestryChanged:Connect(tint)
	b:GetPropertyChangedSignal("TextColor3"):Connect(tint)
	b:GetPropertyChangedSignal("BackgroundColor3"):Connect(tint)
	b:GetPropertyChangedSignal("BackgroundTransparency"):Connect(tint)
	tint()
	return b
end

-- The text box that has the focus now (nil: none): pages aren't built again under the caret.
Theme.focused = nil :: TextBox?

-- A field: a square box on the well, the control outline (FOCUS while typing). opts: text, placeholder, mono,
-- multiline, height, width, fill, name, secret (shown as dots only while not focused), size, bare (no box: inside
-- a plate that draws it).
function Theme.input(parent: Instance?, opts: any?): TextBox
	local o = opts or {}
	local t = Instance.new("TextBox")
	t.Name = o.name or "Input"
	t.Text = o.text or ""
	t.PlaceholderText = o.placeholder or ""
	t.PlaceholderColor3 = C.muted
	t.TextColor3 = C.text
	t.FontFace = if o.mono then F.mono else F.body
	t.TextSize = o.size or (if o.mono then 12 else 14)
	t.ClearTextOnFocus = false
	t.MultiLine = o.multiline == true
	t.TextWrapped = o.multiline == true
	t.TextXAlignment = Enum.TextXAlignment.Left
	t.TextYAlignment = if o.multiline then Enum.TextYAlignment.Top else Enum.TextYAlignment.Center
	t.BorderSizePixel = 0
	t.ClipsDescendants = true
	t.LayoutOrder = nextOrder()
	local h = o.height or 28
	t.Size = if o.width then UDim2.new(0, o.width, 0, h) else UDim2.new(1, 0, 0, h)
	if o.fill then
		t.Size = UDim2.new(0, 0, 0, h)
		Theme.fill(t)
	end
	local stroke: UIStroke? = nil
	if o.bare then
		t.BackgroundTransparency = 1
		Theme.pad(t, 0, 0, 0, 0)
	else
		t.BackgroundColor3 = C.well
		stroke = Theme.stroke(t, C.line)
		Theme.pad(t, 9, if o.multiline then 6 else 0, 9, if o.multiline then 6 else 0)
	end
	t.Focused:Connect(function()
		Theme.focused = t
		if stroke then
			stroke.Color = C.focus
		end
	end)
	t.FocusLost:Connect(function()
		if Theme.focused == t then
			Theme.focused = nil
		end
		if stroke then
			stroke.Color = C.line
		end
	end)
	t.Parent = parent
	return t
end

-- Text to select and copy (Studio gives a plugin no clipboard): a read-only box, mono, on the well.
function Theme.selectable(parent: Instance?, text: string, opts: any?): TextBox
	local o = opts or {}
	local t = Theme.input(parent, { text = text, mono = true, multiline = o.multiline, name = o.name or "Selectable",
		size = o.size or 12, height = o.height })
	t.TextEditable = false
	t.TextColor3 = o.color or C.text2
	if o.multiline then
		t.Size = UDim2.new(1, 0, 0, 0)
		t.AutomaticSize = Enum.AutomaticSize.Y
	end
	if o.radius then
		Theme.corner(t, o.radius)
	end
	t.Focused:Connect(function()
		t.SelectionStart = 1
		t.CursorPosition = #t.Text + 1
	end)
	return t
end

-- A tag: upper case mono 500, 18 tall, square, on its kind's surface with its border (TAGS).
function Theme.tag(parent: Instance?, text: string, kind: string?, opts: any?): Frame
	local o = opts or {}
	local c = Theme.TAGS[kind or "mut"] or Theme.TAGS.mut
	local f = Instance.new("Frame")
	f.Name = "Tag"
	f.BorderSizePixel = 0
	f.BackgroundColor3 = o.bg or c[1]
	f.Size = UDim2.new(0, 0, 0, o.height or 18)
	f.AutomaticSize = Enum.AutomaticSize.X
	f.LayoutOrder = nextOrder()
	Theme.stroke(f, o.border or c[3])
	Theme.pad(f, o.padX or 7, 0, o.padX or 7, 0)
	local l = Theme.label(f, text, "tag", { wrap = false, color = o.color or c[2], size = o.size })
	l.Size = UDim2.new(0, 0, 1, 0)
	f.Parent = parent
	return f
end

-- A 1 px rule across.
function Theme.rule(parent: Instance?, color: Color3?): Frame
	local r = Instance.new("Frame")
	r.Name = "Rule"
	r.BorderSizePixel = 0
	r.BackgroundColor3 = color or C.rule
	r.Size = UDim2.new(1, 0, 0, 1)
	r.LayoutOrder = nextOrder()
	r.Parent = parent
	return r
end

-- A small solid square (a status dot).
function Theme.dot(parent: Instance?, color: Color3, size: number?): Frame
	local d = Instance.new("Frame")
	d.Name = "Dot"
	d.BorderSizePixel = 0
	d.BackgroundColor3 = color
	d.Size = UDim2.fromOffset(size or 6, size or 6)
	d.LayoutOrder = nextOrder()
	d.Parent = parent
	return d
end

-- Something going on: a 12 px square, its 2 px outline in INFO_LINE with its top side in INFO, turning.
local spinners = {} :: { [Frame]: boolean }
local spinning: RBXScriptConnection? = nil
function Theme.spinner(parent: Instance?): Frame
	local holder = Instance.new("Frame")
	holder.Name = "Spinner"
	holder.BackgroundTransparency = 1
	holder.Size = UDim2.fromOffset(14, 14)
	holder.LayoutOrder = nextOrder()
	local sq = Instance.new("Frame")
	sq.Name = "Square"
	sq.BackgroundTransparency = 1
	sq.Size = UDim2.fromOffset(10, 10)
	sq.AnchorPoint = Vector2.new(0.5, 0.5)
	sq.Position = UDim2.fromScale(0.5, 0.5)
	Theme.stroke(sq, C.infoLine, 2)
	local top = Instance.new("Frame")
	top.BorderSizePixel = 0
	top.BackgroundColor3 = C.info
	top.Size = UDim2.new(1, 4, 0, 2)
	top.Position = UDim2.fromOffset(-2, -2)
	top.Parent = sq
	sq.Parent = holder
	holder.Parent = parent
	spinners[sq] = true
	if spinning == nil then
		spinning = RunService.Heartbeat:Connect(function(dt)
			for s in pairs(spinners) do
				if s.Parent and s.Parent.Parent then
					s.Rotation = (s.Rotation + dt * 360) % 360
				elseif s.Parent == nil or s.Parent.Parent == nil then
					spinners[s] = nil -- (gone with its page)
				end
			end
			if next(spinners) == nil and spinning then
				spinning:Disconnect()
				spinning = nil
			end
		end)
	end
	return holder
end

-- A switch: the pill (30 × 16; the accent when on) and its words beside it (pressing either flips it).
function Theme.switch(parent: Instance?, on: boolean, onWords: string, offWords: string, onToggle: (boolean) -> (),
	opts: any?): TextButton
	local o = opts or {}
	local b = Instance.new("TextButton")
	b.Name = o.name or "Switch"
	b.Text = ""
	b.AutoButtonColor = false
	b.BackgroundTransparency = 1
	b.Size = UDim2.new(0, 0, 0, 24)
	b.AutomaticSize = Enum.AutomaticSize.X
	b.LayoutOrder = nextOrder()
	Theme.list(b, 10, true)
	local pill = Instance.new("Frame")
	pill.Name = "Pill"
	pill.BorderSizePixel = 0
	pill.Size = UDim2.fromOffset(30, 16)
	pill.LayoutOrder = 1
	Theme.corner(pill, 8)
	local knob = Instance.new("Frame")
	knob.BorderSizePixel = 0
	knob.BackgroundColor3 = C.knob
	knob.Size = UDim2.fromOffset(12, 12)
	knob.AnchorPoint = Vector2.new(0.5, 0.5)
	Theme.corner(knob, 6)
	knob.Parent = pill
	pill.Parent = b
	local words = Theme.label(b, "", "strong", { wrap = o.wrap == true })
	words.LayoutOrder = 2
	if o.wrap then
		words.Size = UDim2.new(1, -40, 0, 0)
	end
	local state = on
	local function show()
		pill.BackgroundColor3 = if state then C.accent else C.line
		knob.Position = UDim2.new(0, if state then 22 else 8, 0.5, 0)
		words.Text = if state then onWords else offWords
	end
	show()
	b.MouseButton1Click:Connect(function()
		state = not state
		show()
		local ok, err = pcall(onToggle, state)
		if not ok then
			warn("[Autopilot] " .. tostring(err))
		end
	end)
	b.Parent = parent
	return b
end

-- A scrolling page: the room it is given, its content as tall as it is (thin square scroll bar).
function Theme.scroll(parent: Instance?, name: string?): ScrollingFrame
	local s = Instance.new("ScrollingFrame")
	s.Name = name or "Scroll"
	s.BackgroundTransparency = 1
	s.BorderSizePixel = 0
	s.Size = UDim2.fromScale(1, 1)
	s.CanvasSize = UDim2.new(0, 0, 0, 0)
	s.AutomaticCanvasSize = Enum.AutomaticSize.Y
	s.ScrollingDirection = Enum.ScrollingDirection.Y
	s.ScrollBarThickness = 6
	s.ScrollBarImageColor3 = C.line
	s.ScrollBarImageTransparency = 0
	s.VerticalScrollBarInset = Enum.ScrollBarInset.ScrollBar
	s.TopImage = "rbxasset://textures/ui/Scroll/scroll-middle.png"
	s.BottomImage = "rbxasset://textures/ui/Scroll/scroll-middle.png"
	s.LayoutOrder = nextOrder()
	s.Parent = parent
	return s
end

-- A page's content column: at most maxWidth wide, centred, with padding around (left, top, right, bottom).
function Theme.column(parent: Instance, maxWidth: number, padding: { number }, gap: number?): Frame
	local outer = Theme.frame(parent, "Column")
	Theme.pad(outer, padding[1], padding[2], padding[3], padding[4])
	local l = Theme.list(outer, 0)
	l.HorizontalAlignment = Enum.HorizontalAlignment.Center
	local inner = Theme.col(outer, gap or 10, "Content")
	local c = Instance.new("UISizeConstraint")
	c.MaxSize = Vector2.new(maxWidth, math.huge)
	c.Parent = inner
	return inner
end

-- The colors of a verdict: {block fill, ink on it, tag kind}.
function Theme.verdictColors(verdict: string): { any }
	local kind, fill = "mut", C.muted
	if verdict == "CONFIRMED" or verdict == "GOAL HIT" then
		kind, fill = "ok", C.success
	elseif verdict == "DENIED" or verdict == "GOAL MISSED" then
		kind, fill = "bad", C.red
	elseif verdict == "INCONCLUSIVE" then
		kind, fill = "warn", C.amber
	elseif verdict == "EXPLORED" or verdict == "GAME TESTED" then
		kind, fill = "info", C.info
	elseif verdict == "LIVE" then
		kind, fill = "acc", C.accent
	end
	return { fill, C.onAccent, kind }
end

-- A finding's severity as a tag's kind: error, critical, fatal, bug red; warning amber; anything else info.
function Theme.severityKind(severity: string): string
	local s = string.lower(string.match(severity or "", "^%s*(.-)%s*$") or "")
	if s == "error" or s == "critical" or s == "fatal" or s == "bug" then
		return "bad"
	elseif s == "warning" or s == "warn" then
		return "warn"
	end
	return "info"
end

return Theme
