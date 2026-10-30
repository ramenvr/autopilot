## Autopilot's look: the palette, the bundled fonts (fonts/, SIL OFL) and a Theme built in code. The Theme is set on the
## dock's root Control only (and the toolbar badge), never on the editor.
## The fonts are read from their files (fonts/ holds a .gdignore: the editor doesn't import them and an exported game
## doesn't carry them); if one can't be read, the editor's default font stands in.
@tool
extends RefCounted

const DIR := "res://addons/autopilot/"

## Aura's look (The Flatness, dark): flat surfaces, no shadows (but a floating menu's), no gradients; hierarchy from 1 px
## rules and a mono micro-label scale; one amber accent for what is interactive or current.
## Surfaces and lines
const GROUND := Color("#121316")  # the dock's ground (and every other history row)
const RAIL := Color("#0f1013")  # the history rail (and every other history row)
const PLATE := Color("#181a1e")  # cards, the feed, findings
const POPOVER := Color("#15171b")  # menus, popups (the narrow history list)
const HOVER := Color("#1d2026")  # hovered, the pressed gear, the neutral tag
const ROW_ACTIVE := Color("#2a1b10")  # the selected row (with a 2 px ACCENT rule at its left)
const WELL := Color("#0d0e11")  # the prompt box, inputs, the recording, evidence
const RULE := Color("#24262b")  # 1 px dividers: under headers, around plates, above footers
const LINE := Color("#2c2f36")  # control outlines: buttons, inputs, the composer
const LINE_HI := Color("#3a3e47")  # a checkbox's outline, the switch hovered
## Text, accent, focus
const TEXT := Color("#e6e6e8")
const TEXT2 := Color("#d3d5da")  # reading text (reasons, prompts)
const TEXT3 := Color("#a4a8b0")  # row text, feed lines, secondary lines
const MUTED := Color("#8b8f98")  # muted text, micro-labels
const ACCENT := Color("#f0873a")  # primary buttons, the active tab, progress, live, finished harness attempts
const ACCENT_HI := Color("#f59b57")  # (the accent hovered)
const ACCENT_REST := Color("#c4762f")  # (the accent at rest)
const ON_ACCENT := Color("#141517")  # ink on any fill
const FOCUS := Color("#ffb066")  # the focus ring
const STEP_DONE := Color("#6b4122")  # a setup step done; the accent tag's outline
## Verdicts and states: [the color, fg (text on its surface), surface, border]
const SUCCESS := Color("#74b078")  # CONFIRMED, GOAL HIT, ready, connected
const SUCCESS_FG := Color("#b6ddb9")
const SUCCESS_FILL := Color("#16281a")
const SUCCESS_LINE := Color("#3d6b42")
const RED := Color("#e26a62")  # DENIED, GOAL MISSED, Found, Remove
const RED_TEXT := RED
const RED_FG := Color("#f0b2ac")
const RED_FILL := Color("#2c1715")
const RED_LINE := Color("#7a3b36")
const RED_HI := Color("#ea8079")  # (a filled red button hovered)
const AMBER := Color("#e8a94e")  # INCONCLUSIVE, reconnecting, engine warnings
const AMBER_FG := Color("#f6dcae")
const AMBER_FILL := Color("#2a2114")
const AMBER_LINE := Color("#7a5c2a")
const INFO := Color("#7aa5d8")  # EXPLORED, GAME TESTED, in progress
const INFO_FG := Color("#bcd4ee")
const INFO_FILL := Color("#161f2b")
const INFO_LINE := Color("#3a5578")
const NEUTRAL_FG := Color("#c9ccd2")  # the neutral tag's text (STOPPED, NO RESULT, Not connected, BYOK)
const KNOB := Color("#ffffff")  # the switch's knob
const SHADOW := Color(0, 0, 0, 0.55)  # a floating menu's shadow (nothing else casts one)

## Corner radii: 8 on plates (cards, the feed, the composer and steer box, evidence, the recording, menus and popups);
## every control square (0); the switch keeps its pill.
const R_PLATE := 8.0
const PILL := 99.0

## Colors for actions, given out in order of first appearance in a run (stable per name within it).
const ACTION_COLORS := [Color("#f0873a"), Color("#7aa5d8"), Color("#e8a94e"), Color("#74b078"), Color("#a08ad8"),
	Color("#e26a62"), Color("#5fb3b3"), Color("#c9ccd2")]

## The tags' colors by kind: [surface, text, border]
const TAG_COLORS := {
	"ok": [SUCCESS_FILL, SUCCESS, SUCCESS_LINE], "bad": [RED_FILL, RED, RED_LINE], "warn": [AMBER_FILL, AMBER, AMBER_LINE],
	"info": [INFO_FILL, INFO, INFO_LINE], "acc": [ROW_ACTIVE, ACCENT, STEP_DONE], "mut": [HOVER, NEUTRAL_FG, LINE],
}

const ICON_SVG := "icons/autopilot.svg"
const ICON_COLOR := "#f0873a"  # (the icon file's color, replaced by the one asked for)

static var s := 1.0  # the editor's scale
static var fonts_missing := false
static var _fonts := {}
static var _theme: Theme = null


static func px(v: float) -> int:
	return int(round(v * s))


static func _file_font(file: String, symbols: Font = null) -> Font:
	var bytes := FileAccess.get_file_as_bytes(DIR + "fonts/" + file)
	if bytes.is_empty():
		fonts_missing = true
		return ThemeDB.fallback_font
	var f := FontFile.new()
	f.data = bytes
	f.antialiasing = TextServer.FONT_ANTIALIASING_GRAY
	f.hinting = TextServer.HINTING_LIGHT
	f.subpixel_positioning = TextServer.SUBPIXEL_POSITIONING_AUTO
	if symbols != null:
		f.fallbacks = [symbols]
	return f


static func _variation(base: Font, spacing: int) -> FontVariation:
	var v := FontVariation.new()
	v.base_font = base
	v.spacing_glyph = spacing
	return v


## The fonts by role: IBM Plex Sans body (400), body_medium and display (500: buttons, headings one weight up),
## body_semibold and display_bold (600: the setup's headings); verdict (Plex Sans 600, letter-spaced: the verdict block);
## caps (IBM Plex Mono 400, letter-spaced: the micro-labels, upper case), tag (Plex Mono 500, letter-spaced: tags), mono /
## mono_bold (Plex Mono 400 / 500). Neither family has the symbols the dock uses (● ▶ ◀ ▾ ○ ▸ ◂ ■): JetBrains Mono
## stands in for them in every role.
static func font(role: String) -> Font:
	if _fonts.is_empty():
		var symbols := _file_font("JetBrainsMono-Regular.ttf")
		var regular := _file_font("IBMPlexSans-Regular.ttf", symbols)
		var medium := _file_font("IBMPlexSans-Medium.ttf", symbols)
		var semibold := _file_font("IBMPlexSans-SemiBold.ttf", symbols)
		var mono := _file_font("IBMPlexMono-Regular.ttf", symbols)
		var mono_medium := _file_font("IBMPlexMono-Medium.ttf", symbols)
		var spacing := maxi(1, px(1))  # (0.1 em at 10 px; 0.08 em and 0.06 em round to the same pixel)
		_fonts.body = regular
		_fonts.body_medium = medium
		_fonts.body_semibold = semibold
		_fonts.display = medium
		_fonts.display_bold = semibold
		_fonts.verdict = _variation(semibold, spacing)
		_fonts.caps = _variation(mono, spacing)
		_fonts.tag = _variation(mono_medium, spacing)
		_fonts.mono = mono
		_fonts.mono_bold = mono_medium
	return _fonts.get(role, _fonts.body)


## The controller icon, from icons/autopilot.svg, at the given size in pixels (before the editor's scale).
static func icon(size := 16.0, color := ACCENT) -> Texture2D:
	var text := FileAccess.get_file_as_string(DIR + ICON_SVG)
	if text == "":
		return null
	text = text.replace(ICON_COLOR, "#" + color.to_html(false))
	return _svg(text, size)


static func _svg(text: String, size: float, box := 24.0) -> Texture2D:
	var img := Image.new()
	if img.load_svg_from_string(text, size * s / box) != OK:
		return null
	return ImageTexture.create_from_image(img)


## The dock's drawn icons, in a 24x24 box: [svg body, filled (else stroked), stroke width, line cap]. gear: Settings;
## play, pause: the recording's player (play_big: on the picture while it is paused; warning: there
## instead, amber, once it can't play); folder: the result's Run folder and
## the recording's Show in File Explorer (Finder, the file manager); retry: Run again; clipboard: Copy report; chevron:
## the narrow history button; check: the agent service ready.
const GLYPHS := {
	"gear": ['<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/>', false, 1.8, "round"],
	"play": ['<path d="M6 4v16l14-8z"/>', true, 0.0, ""],
	"play_big": ['<path d="M7 4.5v15l12.5-7.5z"/>', true, 0.0, ""],
	"prev": ['<path d="M15 5l-7 7 7 7"/>', false, 2.2, "round"],
	"next": ['<path d="M9 5l7 7-7 7"/>', false, 2.2, "round"],
	"pause": ['<rect x="5" y="4" width="5" height="16"/><rect x="14" y="4" width="5" height="16"/>', true, 0.0, ""],
	"folder": ['<path d="M3 6h6l2 2h10v11H3z"/>', false, 1.9, "round"],
	"retry": ['<path d="M3 12a9 9 0 1 0 3-6.7"/><path d="M3 4v5h5"/>', false, 2.0, "round"],
	"clipboard": ['<rect x="8" y="3" width="8" height="4"/><path d="M16 5h3v16H5V5h3"/>', false, 1.9, "round"],
	"chevron": ['<path d="M5 9l7 7 7-7"/>', false, 2.4, "butt"],
	"check": ['<path d="M4 12l5 5L20 6"/>', false, 2.4, "square"],
	# The activity feed's kind icons (Lucide's bot, search, binoculars, pencil, flask-conical, gamepad-2, eye, rotate-ccw,
	# message-square, flag, megaphone, notebook-pen): drawn where the editor has no icon of the meaning, and outside the editor
	"robot": ['<path d="M12 8V4H8"/><rect width="16" height="12" x="4" y="8" rx="2"/><path d="M2 14h2"/><path d="M20 14h2"/><path d="M15 13v2"/><path d="M9 13v2"/>', false, 1.9, "round"],
	"magnifier": ['<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>', false, 1.9, "round"],
	"binoculars": ['<path d="M10 10h4"/><path d="M19 7V4a1 1 0 0 0-1-1h-2a1 1 0 0 0-1 1v3"/><path d="M20 21a2 2 0 0 0 2-2v-3.851c0-1.39-2-2.962-2-4.829V8a1 1 0 0 0-1-1h-4a1 1 0 0 0-1 1v11a2 2 0 0 0 2 2z"/><path d="M22 16L2 16"/><path d="M4 21a2 2 0 0 1-2-2v-3.851c0-1.39 2-2.962 2-4.829V8a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v11a2 2 0 0 1-2 2z"/><path d="M9 7V4a1 1 0 0 0-1-1H6a1 1 0 0 0-1 1v3"/>', false, 1.9, "round"],
	"pencil": ['<path d="M21.174 6.812a1 1 0 0 0-3.986-3.987L3.842 16.174a2 2 0 0 0-.5.83l-1.321 4.352a.5.5 0 0 0 .623.622l4.353-1.32a2 2 0 0 0 .83-.497z"/><path d="m15 5 4 4"/>', false, 1.9, "round"],
	"flask": ['<path d="M14 2v6a2 2 0 0 0 .245.96l5.51 10.08A2 2 0 0 1 18 22H6a2 2 0 0 1-1.755-2.96l5.51-10.08A2 2 0 0 0 10 8V2"/><path d="M6.453 15h11.094"/><path d="M8.5 2h7"/>', false, 1.9, "round"],
	"gamepad": ['<line x1="6" x2="10" y1="11" y2="11"/><line x1="8" x2="8" y1="9" y2="13"/><line x1="15" x2="15.01" y1="12" y2="12"/><line x1="18" x2="18.01" y1="10" y2="10"/><path d="M17.32 5H6.68a4 4 0 0 0-3.978 3.59c-.006.052-.01.101-.017.152C2.604 9.416 2 14.456 2 16a3 3 0 0 0 3 3c1 0 1.5-.5 2-1l1.414-1.414A2 2 0 0 1 9.828 16h4.344a2 2 0 0 1 1.414.586L17 18c.5.5 1 1 2 1a3 3 0 0 0 3-3c0-1.545-.604-6.584-.685-7.258-.007-.05-.011-.1-.017-.151A4 4 0 0 0 17.32 5z"/>', false, 1.9, "round"],
	"eye": ['<path d="M2.062 12.348a1 1 0 0 1 0-.696 10.75 10.75 0 0 1 19.876 0 1 1 0 0 1 0 .696 10.75 10.75 0 0 1-19.876 0"/><circle cx="12" cy="12" r="3"/>', false, 1.9, "round"],
	"circular-arrow": ['<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/>', false, 1.9, "round"],
	"speech-bubble": ['<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>', false, 1.9, "round"],
	"flag": ['<path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z"/><line x1="4" x2="4" y1="22" y2="15"/>', false, 1.9, "round"],
	"megaphone": ['<path d="m3 11 18-5v12L3 14v-3z"/><path d="M11.6 16.8a3 3 0 1 1-5.8-1.6"/>', false, 1.9, "round"],
	"notebook": ['<path d="M13.4 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-7.4"/><path d="M2 6h4"/><path d="M2 10h4"/><path d="M2 14h4"/><path d="M2 18h4"/><path d="M21.378 5.626a1 1 0 1 0-3.004-3.004l-5.01 5.012a2 2 0 0 0-.506.854l-.837 2.87a.5.5 0 0 0 .62.62l2.87-.837a2 2 0 0 0 .854-.506z"/>', false, 1.9, "round"],
}


## One of GLYPHS at the given size in pixels (before the editor's scale), in color (white: a button tints it with its
## icon colors).
static func glyph(name: String, size := 14.0, color := Color.WHITE) -> Texture2D:
	var g: Array = GLYPHS[name]
	var c := "#" + color.to_html(false)
	var paint := ('fill="%s" stroke="none"' % c) if g[1] else ('fill="none" stroke="%s" stroke-width="%s" stroke-linecap="%s" stroke-linejoin="round"' % [c, g[2], g[3]])
	return _svg('<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" %s>%s</svg>' % [paint, g[0]], size)


## A flat box, its four corners rounded by radius (R_PLATE for a plate; 0, square, for a control).
static func box(bg: Color, radius := 0.0, border := Color.TRANSPARENT, border_width := 0, margins := Vector4(0, 0, 0, 0)) -> StyleBoxFlat:
	var b := StyleBoxFlat.new()
	b.bg_color = bg
	b.draw_center = bg.a > 0.0
	if radius > 0.0:
		b.set_corner_radius_all(px(radius))
		b.corner_detail = 8
	if border_width > 0:
		b.border_color = border
		b.set_border_width_all(max(1, px(border_width)))
	b.content_margin_left = px(margins.x)
	b.content_margin_top = px(margins.y)
	b.content_margin_right = px(margins.z)
	b.content_margin_bottom = px(margins.w)
	b.anti_aliasing = radius > 0.0
	return b


## A plate: PLATE (or bg), radius 8, a 1 px border (RULE, or the state's border), with margins inside.
static func plate_box(margins := Vector4(14, 12, 14, 12), border := RULE, bg := PLATE) -> StyleBoxFlat:
	return box(bg, R_PLATE, border, 1, margins)


## A floating panel (a menu, a popup, a tooltip): POPOVER, radius 8, the control outline, and the only shadow.
static func popup_box(margins := Vector4(0, 0, 0, 0)) -> StyleBoxFlat:
	var b := box(POPOVER, R_PLATE, LINE, 1, margins)
	b.shadow_color = SHADOW
	b.shadow_size = px(20)
	b.shadow_offset = Vector2(0, px(18))
	return b


## The focus ring: 2 px FOCUS, outside the control (2 px away from it).
static func focus_box(gap := 2.0) -> StyleBoxFlat:
	var b := StyleBoxFlat.new()
	b.draw_center = false
	b.border_color = FOCUS
	b.set_border_width_all(max(1, px(2)))
	b.set_expand_margin_all(px(2 + gap))
	return b


static func edge_box(bg: Color, edge: Color, side: String, width := 1.0, margins := Vector4(0, 0, 0, 0)) -> StyleBoxFlat:
	var b := box(bg, 0.0, Color.TRANSPARENT, 0, margins)
	b.border_color = edge
	match side:
		"left": b.border_width_left = max(1, px(width))
		"bottom": b.border_width_bottom = max(1, px(width))
		"top": b.border_width_top = max(1, px(width))
		"right": b.border_width_right = max(1, px(width))
	return b


## A selected row (the run picked in a list): ROW_ACTIVE and a 2 px accent rule on its left; its text keeps the usual
## colors (never text on an accent fill).
static func selected_box(margins := Vector4(0, 0, 0, 0)) -> StyleBoxFlat:
	return edge_box(ROW_ACTIVE, ACCENT, "left", 2, margins)


## A history row's box: selected (selected_box), hovered (HOVER), or its ground (RAIL and GROUND in turn); a 2 px left
## border always (transparent when not selected: the text never moves).
static func row_box(state: String, odd := false, margins := Vector4(12, 9, 14, 9)) -> StyleBoxFlat:
	if state == "selected":
		return selected_box(margins)
	var bg := HOVER if state == "hover" else (GROUND if odd else RAIL)
	return edge_box(bg, Color.TRANSPARENT, "left", 2, margins)


static func _button_styles(t: Theme, type: String, bg: Color, hover: Color, fg: Color, fg_hover: Color, border: Color,
		border_hover: Color, margins: Vector4, font_size: float) -> void:
	t.set_stylebox("normal", type, box(bg, 0, border, 1, margins))
	t.set_stylebox("hover", type, box(hover, 0, border_hover, 1, margins))
	t.set_stylebox("pressed", type, box(hover, 0, border_hover, 1, margins))
	t.set_stylebox("hover_pressed", type, box(hover, 0, border_hover, 1, margins))
	t.set_stylebox("disabled", type, box(Color(bg, bg.a * 0.45), 0, Color(border, border.a * 0.45), 1, margins))
	t.set_stylebox("focus", type, focus_box())
	t.set_color("font_color", type, fg)
	t.set_color("font_hover_color", type, fg_hover)
	t.set_color("font_pressed_color", type, fg_hover)
	t.set_color("font_hover_pressed_color", type, fg_hover)
	t.set_color("font_focus_color", type, fg)
	t.set_color("font_disabled_color", type, Color(fg, 0.45))
	t.set_color("icon_normal_color", type, fg)
	t.set_color("icon_hover_color", type, fg_hover)
	t.set_color("icon_pressed_color", type, fg_hover)
	t.set_color("icon_hover_pressed_color", type, fg_hover)
	t.set_color("icon_focus_color", type, fg)
	t.set_color("icon_disabled_color", type, Color(fg, 0.45))
	t.set_font("font", type, font("display"))
	t.set_font_size("font_size", type, px(font_size))
	t.set_constant("h_separation", type, px(6))


## The buttons: [variation, fill, hovered fill, text, hovered text, border, hovered border]. Each has a small one
## (variation + "Sm": 24 tall, 11.5 px); a button is 28 tall (button() sets its height).
const BUTTONS := [
	["Button", Color.TRANSPARENT, HOVER, TEXT, TEXT, LINE, LINE],
	["AutopilotPrimary", ACCENT, ACCENT_HI, ON_ACCENT, ON_ACCENT, ACCENT, ACCENT_HI],
	["AutopilotQuiet", Color.TRANSPARENT, HOVER, MUTED, TEXT, Color.TRANSPARENT, Color.TRANSPARENT],
	["AutopilotDanger", Color.TRANSPARENT, RED_FILL, RED, RED, Color.TRANSPARENT, Color.TRANSPARENT],
	["AutopilotDangerFill", RED, RED_HI, ON_ACCENT, ON_ACCENT, RED, RED_HI],
	["AutopilotOn", HOVER, HOVER, ACCENT, ACCENT, LINE, LINE],  # (the gear while the Settings page is open)
]


static func theme(scale: float) -> Theme:
	if _theme != null and is_equal_approx(scale, s):
		return _theme
	s = scale
	_fonts.clear()
	var t := Theme.new()
	t.default_font = font("body")
	t.default_font_size = px(13)

	# Buttons (square, sentence case)
	for spec in BUTTONS:
		for small in [false, true]:
			var type: String = spec[0] + ("Sm" if small else "")
			if type != "Button":
				t.set_type_variation(type, "Button")
			_button_styles(t, type, spec[1], spec[2], spec[3], spec[4], spec[5], spec[6],
				Vector4(9, 3, 9, 4) if small else Vector4(12, 5, 12, 6), 11.5 if small else 12.0)
	# the narrow dock's history button: an outlined field on the rail's ground
	t.set_type_variation("AutopilotDrop", "Button")
	_button_styles(t, "AutopilotDrop", RAIL, HOVER, TEXT, TEXT, LINE, LINE, Vector4(8, 5, 8, 6), 12)
	# filter chips (the Policy log): outlined, mono 10.5 / 500, letter-spaced; on in the accent tag's colors
	t.set_type_variation("AutopilotChip", "Button")
	var chm := Vector4(9, 3, 9, 4)
	_button_styles(t, "AutopilotChip", Color.TRANSPARENT, HOVER, MUTED, TEXT, LINE, LINE, chm, 10.5)
	t.set_stylebox("pressed", "AutopilotChip", box(ROW_ACTIVE, 0, STEP_DONE, 1, chm))
	t.set_stylebox("hover_pressed", "AutopilotChip", box(ROW_ACTIVE, 0, STEP_DONE, 1, chm))
	t.set_color("font_pressed_color", "AutopilotChip", ACCENT)
	t.set_color("font_hover_pressed_color", "AutopilotChip", ACCENT)
	t.set_font("font", "AutopilotChip", font("tag"))
	# panel tabs: sentence case, muted; the one on in TEXT over a 2 px accent rule
	t.set_type_variation("AutopilotTab", "Button")
	var tm := Vector4(0, 12, 0, 10)
	_button_styles(t, "AutopilotTab", Color.TRANSPARENT, Color.TRANSPARENT, MUTED, TEXT, Color.TRANSPARENT, Color.TRANSPARENT, tm, 12)
	for st in ["normal", "hover", "disabled"]:
		t.set_stylebox(st, "AutopilotTab", edge_box(Color.TRANSPARENT, Color.TRANSPARENT, "bottom", 2, tm))
	t.set_stylebox("pressed", "AutopilotTab", edge_box(Color.TRANSPARENT, ACCENT, "bottom", 2, tm))
	t.set_stylebox("hover_pressed", "AutopilotTab", edge_box(Color.TRANSPARENT, ACCENT, "bottom", 2, tm))
	t.set_stylebox("focus", "AutopilotTab", StyleBoxEmpty.new())
	t.set_color("font_pressed_color", "AutopilotTab", TEXT)
	t.set_color("font_hover_pressed_color", "AutopilotTab", TEXT)

	# Labels
	t.set_color("font_color", "Label", TEXT)
	t.set_font("font", "Label", font("body"))
	var labels := {
		"AutopilotCaps": ["caps", 10, MUTED], "AutopilotMono": ["mono", 11, TEXT3], "AutopilotMonoDim": ["mono", 10, MUTED],
		"AutopilotMonoBig": ["mono_bold", 18, TEXT], "AutopilotTitle": ["body_medium", 15, TEXT],
		"AutopilotHeading": ["body_semibold", 18, TEXT], "AutopilotBody": ["body", 13, TEXT2], "AutopilotDim": ["body", 13, TEXT3],
		"AutopilotSmall": ["body", 12, TEXT3], "AutopilotStrong": ["body_medium", 13, TEXT], "AutopilotTag": ["tag", 10, TEXT],
	}
	for v in labels:
		t.set_type_variation(v, "Label")
		t.set_font("font", v, font(labels[v][0]))
		t.set_font_size("font_size", v, px(labels[v][1]))
		t.set_color("font_color", v, labels[v][2])

	# Panels
	t.set_stylebox("panel", "PanelContainer", box(GROUND))
	t.set_stylebox("panel", "Panel", box(GROUND))
	var panels := {
		"AutopilotCard": plate_box(),
		"AutopilotWell": box(WELL, R_PLATE, RULE, 1, Vector4(11, 9, 11, 9)),
		"AutopilotRail": box(RAIL),
		"AutopilotBar": edge_box(GROUND, RULE, "bottom", 1, Vector4(16, 0, 16, 0)),
		"AutopilotHead": edge_box(GROUND, RULE, "bottom", 1, Vector4(16, 12, 16, 12)),
		"AutopilotFooter": edge_box(RAIL, RULE, "top", 1, Vector4(14, 9, 14, 9)),
		"AutopilotDetail": box(GROUND, 0, Color.TRANSPARENT, 0, Vector4(16, 12, 16, 12)),
	}
	for v in panels:
		t.set_type_variation(v, "PanelContainer")
		t.set_stylebox("panel", v, panels[v])

	# Inputs: square fields in the well
	var im := Vector4(9, 5, 9, 6)
	for type in ["LineEdit", "TextEdit"]:
		t.set_stylebox("normal", type, box(WELL, 0, LINE, 1, im))
		t.set_stylebox("focus", type, focus_box(1))
		t.set_stylebox("read_only", type, box(WELL, 0, RULE, 1, im))
		t.set_font("font", type, font("body"))
		t.set_font_size("font_size", type, px(13))
		t.set_color("font_color", type, TEXT)
		t.set_color("font_placeholder_color", type, MUTED)
		t.set_color("font_readonly_color", type, TEXT2)
		t.set_color("font_uneditable_color", type, TEXT3)
		t.set_color("caret_color", type, ACCENT)
		t.set_color("selection_color", type, Color(ACCENT, 0.25))
		t.set_color("font_selected_color", type, TEXT)
		t.set_color("background_color", type, WELL)
	# a field without its own box, inside a plate that draws it (the composer's prompt, the steer box)
	for type in ["AutopilotBare", "AutopilotBareLine"]:
		t.set_type_variation(type, "TextEdit" if type == "AutopilotBare" else "LineEdit")
		for st in ["normal", "focus", "read_only"]:
			t.set_stylebox(st, type, StyleBoxEmpty.new())
		t.set_color("background_color", type, Color.TRANSPARENT)
	t.set_type_variation("AutopilotCode", "TextEdit")
	t.set_font("font", "AutopilotCode", font("mono"))
	t.set_font_size("font_size", "AutopilotCode", px(11.5))
	t.set_color("font_readonly_color", "AutopilotCode", TEXT2)
	t.set_type_variation("AutopilotMonoField", "LineEdit")  # (a number or an address: mono 12)
	t.set_font("font", "AutopilotMonoField", font("mono"))
	t.set_font_size("font_size", "AutopilotMonoField", px(12))
	# number fields (SpinBox): a plain field, no arrows (the wheel and the arrow keys still step it)
	var none := ImageTexture.create_from_image(Image.create_empty(1, 1, false, Image.FORMAT_RGBA8))
	for ic in ["updown", "up", "up_hover", "up_pressed", "up_disabled", "down", "down_hover", "down_pressed", "down_disabled"]:
		t.set_icon(ic, "SpinBox", none)
	for st in ["up_background", "up_background_hovered", "up_background_pressed", "up_background_disabled", "down_background",
			"down_background_hovered", "down_background_pressed", "down_background_disabled", "field_and_buttons_separator",
			"up_down_buttons_separator"]:
		t.set_stylebox(st, "SpinBox", StyleBoxEmpty.new())
	for c in ["buttons_vertical_separation", "field_and_buttons_separation", "buttons_width", "set_min_buttons_width_from_icons"]:
		t.set_constant(c, "SpinBox", 0)
	# dropdowns: fields
	_button_styles(t, "OptionButton", WELL, WELL, TEXT, TEXT, LINE, LINE_HI, Vector4(9, 5, 4, 6), 13)
	t.set_font("font", "OptionButton", font("body"))
	t.set_stylebox("disabled", "OptionButton", box(PLATE, 0, RULE, 1, Vector4(9, 5, 4, 6)))
	t.set_color("font_disabled_color", "OptionButton", TEXT3)
	t.set_icon("arrow", "OptionButton", glyph("chevron", 10, TEXT3))
	t.set_constant("arrow_margin", "OptionButton", px(6))
	t.set_constant("modulate_arrow", "OptionButton", 0)

	# Checkboxes: the removal's box (14 px, square: red when ticked), and the switch (AutopilotSwitch: the pill)
	var unchecked := '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14"><rect x="0.5" y="0.5" width="13" height="13" fill="#0d0e11" stroke="#3a3e47"/></svg>'
	var checked := '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14"><rect x="0" y="0" width="14" height="14" fill="#e26a62"/></svg>'
	t.set_icon("unchecked", "CheckBox", _svg(unchecked, 14, 14))
	t.set_icon("checked", "CheckBox", _svg(checked, 14, 14))
	t.set_icon("unchecked_disabled", "CheckBox", _svg(unchecked.replace('stroke="#3a3e47"', 'stroke="#24262b"'), 14, 14))
	t.set_icon("checked_disabled", "CheckBox", _svg(checked.replace("#e26a62", "#7a3b36"), 14, 14))
	t.set_font("font", "CheckBox", font("body"))
	t.set_font_size("font_size", "CheckBox", px(12.5))
	t.set_constant("h_separation", "CheckBox", px(10))
	t.set_constant("check_v_offset", "CheckBox", 0)
	for c in ["font_color", "font_hover_color", "font_pressed_color", "font_hover_pressed_color", "font_focus_color"]:
		t.set_color(c, "CheckBox", TEXT2)
	t.set_color("font_disabled_color", "CheckBox", Color(TEXT2, 0.45))
	for st in ["normal", "hover", "pressed", "hover_pressed", "disabled"]:
		t.set_stylebox(st, "CheckBox", StyleBoxEmpty.new())
	t.set_stylebox("focus", "CheckBox", focus_box())
	var sw := '<svg xmlns="http://www.w3.org/2000/svg" width="30" height="16"><rect width="30" height="16" rx="8" fill="%s"/><circle cx="%s" cy="8" r="6" fill="#ffffff"/></svg>'
	t.set_type_variation("AutopilotSwitch", "CheckBox")
	t.set_icon("unchecked", "AutopilotSwitch", _svg(sw % ["#2c2f36", "8"], 30, 30))
	t.set_icon("checked", "AutopilotSwitch", _svg(sw % ["#f0873a", "22"], 30, 30))
	t.set_icon("unchecked_disabled", "AutopilotSwitch", _svg(sw % ["#24262b", "8"], 30, 30))
	t.set_icon("checked_disabled", "AutopilotSwitch", _svg(sw % ["#c4762f", "22"], 30, 30))
	t.set_font("font", "AutopilotSwitch", font("body_medium"))
	t.set_font_size("font_size", "AutopilotSwitch", px(13))
	for c in ["font_color", "font_hover_color", "font_pressed_color", "font_hover_pressed_color", "font_focus_color"]:
		t.set_color(c, "AutopilotSwitch", TEXT)
	t.set_constant("h_separation", "AutopilotSwitch", px(10))

	# Rich text (feed, observation, evidence)
	t.set_font("normal_font", "RichTextLabel", font("body"))
	t.set_font("bold_font", "RichTextLabel", font("body_medium"))
	t.set_font("mono_font", "RichTextLabel", font("mono"))
	t.set_font_size("normal_font_size", "RichTextLabel", px(13))
	t.set_font_size("bold_font_size", "RichTextLabel", px(13))
	t.set_font_size("mono_font_size", "RichTextLabel", px(11.5))
	t.set_color("default_color", "RichTextLabel", TEXT2)
	t.set_color("selection_color", "RichTextLabel", Color(ACCENT, 0.25))
	t.set_stylebox("normal", "RichTextLabel", StyleBoxEmpty.new())
	t.set_stylebox("focus", "RichTextLabel", StyleBoxEmpty.new())
	t.set_constant("table_h_separation", "RichTextLabel", px(12))
	t.set_constant("table_v_separation", "RichTextLabel", px(2))
	t.set_constant("line_separation", "RichTextLabel", px(3))

	# The recording's scrub bar: a 2 px rule, the played part in the accent, no grabber
	var track := box(RULE)
	track.content_margin_top = max(1, px(1))
	track.content_margin_bottom = max(1, px(1))
	t.set_stylebox("slider", "HSlider", track)
	t.set_stylebox("grabber_area", "HSlider", box(ACCENT))
	t.set_stylebox("grabber_area_highlight", "HSlider", box(ACCENT_HI))
	for ic in ["grabber", "grabber_highlight", "grabber_disabled", "tick"]:
		t.set_icon(ic, "HSlider", none)

	# Scroll bars: thin, square, in the control outline's color
	for type in ["VScrollBar", "HScrollBar"]:
		var side := Vector4(3, 3, 3, 3)
		t.set_stylebox("scroll", type, box(Color.TRANSPARENT, 0, Color.TRANSPARENT, 0, side))
		t.set_stylebox("scroll_focus", type, box(Color.TRANSPARENT, 0, Color.TRANSPARENT, 0, side))
		t.set_stylebox("grabber", type, box(LINE, 0, Color.TRANSPARENT, 0, side))
		t.set_stylebox("grabber_highlight", type, box(LINE_HI, 0, Color.TRANSPARENT, 0, side))
		t.set_stylebox("grabber_pressed", type, box(MUTED, 0, Color.TRANSPARENT, 0, side))
		for ic in ["increment", "increment_highlight", "increment_pressed", "decrement", "decrement_highlight", "decrement_pressed"]:
			t.set_icon(ic, type, none)

	# The policy table
	t.set_stylebox("panel", "Tree", box(GROUND))
	t.set_stylebox("focus", "Tree", StyleBoxEmpty.new())
	t.set_stylebox("selected", "Tree", selected_box())
	t.set_stylebox("selected_focus", "Tree", selected_box())
	t.set_stylebox("cursor", "Tree", StyleBoxEmpty.new())
	t.set_stylebox("cursor_unfocused", "Tree", StyleBoxEmpty.new())
	t.set_stylebox("hovered", "Tree", box(HOVER))
	t.set_stylebox("hovered_dimmed", "Tree", box(HOVER))
	for st in ["title_button_normal", "title_button_hover", "title_button_pressed"]:
		t.set_stylebox(st, "Tree", edge_box(GROUND, RULE, "bottom", 1, Vector4(10, 8, 10, 8)))
	t.set_font("font", "Tree", font("mono"))
	t.set_font_size("font_size", "Tree", px(11))
	t.set_font("title_button_font", "Tree", font("caps"))
	t.set_font_size("title_button_font_size", "Tree", px(10))
	t.set_color("title_button_color", "Tree", MUTED)
	t.set_color("font_color", "Tree", TEXT2)
	t.set_color("font_selected_color", "Tree", TEXT)
	t.set_color("font_hovered_color", "Tree", TEXT)
	t.set_color("guide_color", "Tree", HOVER)  # (the rows' 1 px rule)
	t.set_color("relationship_line_color", "Tree", Color.TRANSPARENT)
	t.set_constant("draw_guides", "Tree", 1)
	t.set_constant("draw_relationship_lines", "Tree", 0)
	t.set_constant("h_separation", "Tree", px(8))
	t.set_constant("v_separation", "Tree", px(6))
	t.set_constant("item_margin", "Tree", 0)
	t.set_constant("inner_item_margin_left", "Tree", px(6))
	t.set_constant("inner_item_margin_right", "Tree", px(6))

	# Popups, tooltips, folds, splits, dialogs
	t.set_stylebox("panel", "PopupMenu", popup_box(Vector4(4, 4, 4, 4)))
	t.set_stylebox("hover", "PopupMenu", box(HOVER))
	t.set_font("font", "PopupMenu", font("body"))
	t.set_font_size("font_size", "PopupMenu", px(13))
	t.set_color("font_color", "PopupMenu", TEXT2)
	t.set_color("font_hover_color", "PopupMenu", TEXT)
	t.set_constant("v_separation", "PopupMenu", px(8))
	t.set_stylebox("panel", "PopupPanel", popup_box())
	t.set_stylebox("panel", "TooltipPanel", popup_box(Vector4(8, 5, 8, 5)))
	t.set_color("font_color", "TooltipLabel", TEXT2)
	t.set_font("font", "TooltipLabel", font("body"))
	t.set_font_size("font_size", "TooltipLabel", px(12))
	t.set_stylebox("title_panel", "FoldableContainer", StyleBoxEmpty.new())
	t.set_stylebox("title_hover_panel", "FoldableContainer", StyleBoxEmpty.new())
	t.set_stylebox("title_collapsed_panel", "FoldableContainer", StyleBoxEmpty.new())
	t.set_stylebox("title_collapsed_hover_panel", "FoldableContainer", StyleBoxEmpty.new())
	t.set_stylebox("panel", "FoldableContainer", box(Color.TRANSPARENT, 0, Color.TRANSPARENT, 0, Vector4(0, 10, 0, 0)))
	t.set_stylebox("focus", "FoldableContainer", StyleBoxEmpty.new())
	t.set_font("font", "FoldableContainer", font("caps"))
	t.set_font_size("font_size", "FoldableContainer", px(10))
	t.set_color("font_color", "FoldableContainer", MUTED)
	t.set_color("hover_font_color", "FoldableContainer", TEXT)
	t.set_color("collapsed_font_color", "FoldableContainer", MUTED)
	t.set_constant("h_separation", "FoldableContainer", px(6))
	t.set_stylebox("panel", "AcceptDialog", box(POPOVER, 0, Color.TRANSPARENT, 0, Vector4(14, 12, 14, 12)))
	t.set_stylebox("separator", "HSeparator", _line(false))
	t.set_stylebox("separator", "VSeparator", _line(true))
	t.set_constant("separation", "HSeparator", 1)
	t.set_constant("separation", "VSeparator", 1)
	t.set_constant("separation", "HBoxContainer", px(6))
	t.set_constant("separation", "VBoxContainer", px(6))
	t.set_constant("minimum_grab_thickness", "SplitContainer", px(6))
	t.set_stylebox("panel", "ScrollContainer", StyleBoxEmpty.new())
	_theme = t
	return t


static func _line(vertical: bool) -> StyleBoxLine:
	var l := StyleBoxLine.new()
	l.color = RULE
	l.thickness = 1
	l.vertical = vertical
	return l


## Drops the cached theme and fonts (the plugin leaving the editor).
static func release() -> void:
	_theme = null
	_fonts.clear()


## A button: sentence case, 28 tall (small: 24), in one of BUTTONS' variations.
static func button(text: String, variation: String, action: Callable, small := false) -> Button:
	var b := Button.new()
	b.text = text
	b.theme_type_variation = variation + ("Sm" if small else "")
	b.custom_minimum_size = Vector2(0, px(24 if small else 28))
	b.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	if action.is_valid():
		b.pressed.connect(action)
	return b


## A square button that is only an icon (one of GLYPHS at 14 px, tinted by the button's icon colors), named by its tooltip.
static func icon_button(name: String, tip: String, variation: String, action: Callable, small := false) -> Button:
	var b := button("", variation, action, small)
	b.icon = glyph(name, 14 if not name.begins_with("play") and name != "pause" else 12)
	b.icon_alignment = HORIZONTAL_ALIGNMENT_CENTER
	b.expand_icon = false
	var side := px(24 if small else 28)
	b.custom_minimum_size = Vector2(side, side)
	b.tooltip_text = tip
	return b


## A button that can't act now because of state (not connected, a run going), greyed as a disabled one is (opacity
## 0.45) but still pressable: pressing it says why on the status line. greyed() tells (for the checks).
static func grey(b: Control, on: bool) -> void:
	b.modulate = Color(1, 1, 1, 0.45 if on else 1.0)
	b.set_meta("greyed", on)


static func greyed(b: Control) -> bool:
	return b.get_meta("greyed", false) == true


## A rule: 1 px of RULE across (or down).
static func rule(vertical := false) -> ColorRect:
	var r := ColorRect.new()
	r.color = RULE
	r.custom_minimum_size = Vector2(1, 0) if vertical else Vector2(0, 1)
	r.mouse_filter = Control.MOUSE_FILTER_IGNORE
	return r


## A small solid square (a status dot: 6 or 7 px).
static func dot(color: Color, size := 6.0) -> ColorRect:
	var d := ColorRect.new()
	d.color = color
	d.custom_minimum_size = Vector2(px(size), px(size))
	d.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	d.size_flags_horizontal = Control.SIZE_SHRINK_BEGIN
	d.mouse_filter = Control.MOUSE_FILTER_IGNORE
	return d


## A tag: upper case IBM Plex Mono 500 (10 px, letter-spaced), 18 tall, square, on its kind's surface with its border
## (TAG_COLORS: ok, bad, warn, info, acc, mut).
static func tag(text: String, kind := "mut") -> PanelContainer:
	var c: Array = TAG_COLORS.get(kind, TAG_COLORS.mut)
	var p := PanelContainer.new()
	p.add_theme_stylebox_override("panel", box(c[0], 0, c[2], 1, Vector4(7, 2, 7, 2)))
	p.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	p.size_flags_horizontal = Control.SIZE_SHRINK_BEGIN
	p.custom_minimum_size = Vector2(0, px(18))
	var l := Label.new()
	l.theme_type_variation = "AutopilotTag"
	l.text = text.to_upper()
	l.add_theme_color_override("font_color", c[1])
	l.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	l.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
	p.add_child(l)
	return p


## The colors of a verdict: [block fill, ink on it, tag kind (TAG_COLORS), tag text, tag surface].
static func verdict_colors(verdict: String) -> Array:
	var kind := "mut"
	var fill := MUTED
	match verdict:
		"CONFIRMED", "GOAL HIT":
			kind = "ok"
			fill = SUCCESS
		"DENIED", "GOAL MISSED":
			kind = "bad"
			fill = RED
		"INCONCLUSIVE":
			kind = "warn"
			fill = AMBER
		"EXPLORED", "GAME TESTED":
			kind = "info"
			fill = INFO
		"LIVE":
			kind = "acc"
			fill = ACCENT
	var c: Array = TAG_COLORS[kind]
	return [fill, ON_ACCENT, kind, c[1], c[0]]
