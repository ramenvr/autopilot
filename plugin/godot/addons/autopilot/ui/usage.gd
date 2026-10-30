## The Settings page's usage data without UI: a run's token counts (the brain's `usage` message, docs/protocol.md "Usage"),
## their sum over runs, the numbers as shown, and the runs' usage kept between editor sessions (a JSON file per
## project, .godot/editor/autopilot_usage.json: more runs than the run history keeps, ui/history.gd).
@tool
extends RefCounted

const Prefs = preload("../lib/prefs.gd")

const FILE_NAME := "autopilot_usage.json"
const KEEP_RUNS := 1000  # the most runs the file keeps (the oldest go first)
const FIELDS := ["calls", "input", "output", "cache_read", "cache_write"]


static func _int(v) -> int:
	return maxi(0, int(v)) if typeof(v) in [TYPE_INT, TYPE_FLOAT] else 0


## The models of a usage message, cleaned: [{"kind": "llm"|"decision", "model", "calls", "input", "output",
## "cache_read", "cache_write"}], the counts whole numbers >= 0; entries that aren't objects are left out.
static func parse(models) -> Array:
	var out := []
	if typeof(models) != TYPE_ARRAY:
		return out
	for m in models:
		if typeof(m) != TYPE_DICTIONARY:
			continue
		var kind := str(m.get("kind", "llm"))
		var row := {"kind": "decision" if kind == "decision" else "llm", "model": str(m.get("model", ""))}
		for f in FIELDS:
			row[f] = _int(m.get(f))
		out.append(row)
	return out


## The rows of usage lists summed per kind and model: LLM rows first, then decision rows, each in order of first
## appearance.
static func total(usages: Array) -> Array:
	var by := {}
	var order := []
	for usage in usages:
		if typeof(usage) != TYPE_ARRAY:
			continue
		for m in parse(usage):
			var key := "%s\n%s" % [m.kind, m.model]
			if not by.has(key):
				by[key] = {"kind": m.kind, "model": m.model, "calls": 0, "input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
				order.append(key)
			for f in FIELDS:
				by[key][f] += m[f]
	var out := []
	for kind in ["llm", "decision"]:
		for key in order:
			if by[key].kind == kind:
				out.append(by[key])
	return out


## The tokens in and out of one kind ("llm" or "decision") in a usage list: [input, output].
static func tokens(usage: Array, kind: String) -> Array:
	var i := 0
	var o := 0
	for m in parse(usage):
		if m.kind == kind:
			i += m.input
			o += m.output
	return [i, o]


## A row's label, as the Settings page shows it: the model's id (a decision row tells itself apart by having no cache
## cells).
static func label(row: Dictionary) -> String:
	return str(row.get("model", ""))


# --- the Autopilot bill (Aura only) -------------------------------------------------------------------------

## A bill frame (or a saved run's bill), cleaned: {"usd": >= 0, "billed": bool, "reason"} ({} when it has no amount).
## billed false: Aura bills it later, or didn't (reason says why).
static func parse_bill(b) -> Dictionary:
	if typeof(b) != TYPE_DICTIONARY or not typeof(b.get("usd")) in [TYPE_INT, TYPE_FLOAT]:
		return {}
	var out := {"usd": maxf(0.0, float(b.usd)), "billed": b.get("billed", false) == true}
	if typeof(b.get("reason")) == TYPE_STRING and b.reason != "":
		out["reason"] = b.reason
	return out


## The amount a run's bill shows (-1 when nothing is shown: no bill, or one Aura didn't bill).
static func billed(bill) -> float:
	var b := parse_bill(bill)
	return float(b.usd) if not b.is_empty() and b.billed else -1.0


## What the runs were billed, summed (their bills that Aura billed): [usd, how many runs had one].
static func bill_total(entries: Array) -> Array:
	var usd := 0.0
	var n := 0
	for e in entries:
		if typeof(e) != TYPE_DICTIONARY:
			continue
		var v := billed(e.get("bill"))
		if v >= 0.0:
			usd += v
			n += 1
	return [usd, n]


## US dollars as shown: 0.593177 -> "$0.59", 1234.5 -> "$1,234.50", below a cent (but more than nothing) "< $0.01".
static func money(usd: float) -> String:
	if usd > 0.0 and usd < 0.01:
		return "< $0.01"
	var cents := int(round(maxf(0.0, usd) * 100.0))
	return "$%s.%02d" % [thousands(floori(cents / 100.0)), cents % 100]


## The account frame's bill ({"usd", "runs", "from", "to"}, what Autopilot billed this Aura account in the current period),
## cleaned: {"usd", "runs", "from", "to"} with from/to "" when missing; {} for null or no amount.
static func parse_account_bill(b) -> Dictionary:
	if typeof(b) != TYPE_DICTIONARY or not typeof(b.get("usd")) in [TYPE_INT, TYPE_FLOAT]:
		return {}
	return {"usd": maxf(0.0, float(b.usd)), "runs": _int(b.get("runs")),
		"from": str(b.from) if typeof(b.get("from")) == TYPE_STRING else "",
		"to": str(b.to) if typeof(b.get("to")) == TYPE_STRING else ""}


## The account frame's out_of_credits (Aura's words when the team has no credits left for a run, and where to add some),
## cleaned: {"message", "url"} with url "" when Aura names none (or not a web page: it is opened in the browser); {} for
## null, none (an older agent service) or no words.
static func parse_out_of_credits(o) -> Dictionary:
	if typeof(o) != TYPE_DICTIONARY or typeof(o.get("message")) != TYPE_STRING or o.message.strip_edges() == "":
		return {}
	var url: String = o.url.strip_edges() if typeof(o.get("url")) == TYPE_STRING else ""
	if not (url.begins_with("https://") or url.begins_with("http://")):
		url = ""
	return {"message": o.message.strip_edges(), "url": url}


## The account card's line for it: "Billing period (Oct 1 – Oct 31): $12.34 USD" ("Billing period: $12.34 USD" when
## its dates aren't known).
static func period_line(b: Dictionary) -> String:
	var dates := period_dates(str(b.get("from", "")), str(b.get("to", "")))
	return "Billing period%s: %s USD" % [(" (%s)" % dates) if dates != "" else "", money(float(b.get("usd", 0.0)))]


const MONTHS := ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


## A period's dates as shown: "2026-10-01", "2026-10-31" -> "Oct 1 – Oct 31" (with the years when they differ:
## "Dec 15, 2026 – Jan 14, 2027"); "" when either isn't a date.
static func period_dates(from: String, to: String) -> String:
	var a := _date(from)
	var b := _date(to)
	if a.is_empty() or b.is_empty():
		return ""
	if a[0] != b[0]:
		return "%s %d, %d – %s %d, %d" % [MONTHS[a[1] - 1], a[2], a[0], MONTHS[b[1] - 1], b[2], b[0]]
	return "%s %d – %s %d" % [MONTHS[a[1] - 1], a[2], MONTHS[b[1] - 1], b[2]]


## "YYYY-MM-DD" (anything after the day ignored) -> [year, month, day]; [] when it isn't one.
static func _date(s: String) -> Array:
	var p := s.left(10).split("-")
	if p.size() != 3 or not (p[0].is_valid_int() and p[1].is_valid_int() and p[2].is_valid_int()):
		return []
	var m := p[1].to_int()
	var d := p[2].to_int()
	if m < 1 or m > 12 or d < 1 or d > 31:
		return []
	return [p[0].to_int(), m, d]


## A whole number with thousands separators: 1234567 -> "1,234,567".
static func thousands(n: int) -> String:
	var neg := n < 0
	var digits := str(absi(n))
	var out := ""
	while digits.length() > 3:
		out = "," + digits.substr(digits.length() - 3) + out
		digits = digits.substr(0, digits.length() - 3)
	return ("-" if neg else "") + digits + out


## A count shortened: 950 -> "950", 1234 -> "1.2k", 3400000 -> "3.4M", 2100000000 -> "2.1B".
static func compact(n: int) -> String:
	var a := absi(n)
	if a < 1000:
		return str(n)
	for unit in [[1000000000, "B"], [1000000, "M"], [1000, "k"]]:
		if a >= unit[0]:
			var v := float(n) / float(unit[0])
			return ("%.0f%s" if absf(v) >= 100.0 else "%.1f%s") % [v, unit[1]]
	return str(n)


# --- kept between editor sessions ---------------------------------------------------------------------

## The runs saved in path: [{"id", "started_unix", "kind", "prompt", "verdict", "usage": [...], "final", "bill"}],
## oldest first. A missing or unreadable file is no runs; a saved run without usage (or with one that doesn't parse)
## loads with "usage": [], one without a bill (saved before bills, or not through Aura) with "bill": {}.
static func load_runs(path: String) -> Array:
	if path == "" or not FileAccess.file_exists(path):
		return []
	var data = Prefs.parse_json(FileAccess.get_file_as_string(path))
	var list = data.get("runs") if typeof(data) == TYPE_DICTIONARY else data
	var out := []
	if typeof(list) != TYPE_ARRAY:
		return out
	for r in list:
		if typeof(r) != TYPE_DICTIONARY or str(r.get("id", "")) == "":
			continue
		var started = r.get("started_unix", 0.0)
		out.append({"id": str(r.id), "started_unix": float(started) if typeof(started) in [TYPE_INT, TYPE_FLOAT] else 0.0,
			"kind": str(r.get("kind", "")), "prompt": str(r.get("prompt", "")), "verdict": str(r.get("verdict", "")),
			"usage": parse(r.get("usage", [])), "final": r.get("final", false) == true, "bill": parse_bill(r.get("bill"))})
	return out


## The runs with record put in (replacing the one of the same id), oldest first, at most KEEP_RUNS.
static func upsert(runs: Array, record: Dictionary) -> Array:
	var out := []
	for r in runs:
		if str(r.get("id", "")) != str(record.get("id", "")):
			out.append(r)
	out.append(record)
	out.sort_custom(func(a, b): return float(a.get("started_unix", 0.0)) < float(b.get("started_unix", 0.0)))
	if out.size() > KEEP_RUNS:
		out = out.slice(out.size() - KEEP_RUNS)
	return out


## Write the runs to path (atomically). OK or an error.
static func save_runs(path: String, runs: Array) -> Error:
	return Prefs.write_atomic(path, JSON.stringify({"version": 1, "runs": runs}, "  ") + "\n")
