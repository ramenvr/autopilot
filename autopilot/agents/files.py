"""File tools for an agent that has none of its own that Autopilot can hold to the game's folder (Codex: its shell and
patch tools are off, autopilot/agents/codex.py): read, list and search the game's folder and the kit; write and edit
only this run's folder, notes.md and lib.py. The same rules as OpenCode's permissions (autopilot/agents/opencode.py).

Paths are relative to the game's folder, with / (absolute ones too, but only inside what may be read); autopilot/KIT.md
and autopilot/kit/... are the kit's, unless the game's folder has them. Files named *.env or *.env.* are never read.
"""

import os
import re
from collections import Counter

from .. import memory
from . import whole

MAX_READ_LINES = 2000
MAX_WRITE_BYTES = 2 * 1024 * 1024
SKIP_DIRS = {".git", "__pycache__", ".agent", "node_modules"}
# What a run kept whole for being too long for a tool result (runs/<id>/whole/: earlier searches' matches among them):
# a search of the game's folder doesn't look in it, unless it is asked to
KEPT = re.compile(rf"runs/[^/]+/{whole.FOLDER}(/|$)")

FILE_TOOL_SPECS = (
    ("read_file",
     "Read a text file of this game's folder, or of the kit (autopilot/KIT.md, autopilot/kit/). Lines come numbered "
     "and whole; `offset` (the first line, from 1) and `limit` (at most 2000 lines) read a long file in parts, and "
     f"one call gives at most {whole.ANSWER_CHARS:,} characters: the answer says where to read on. A line longer "
     "than that comes in parts: `column` (from 1) is where in the `offset` line to start.",
     {"type": "object", "properties": {"path": {"type": "string"}, "offset": {"type": "integer"},
                                       "limit": {"type": "integer"}, "column": {"type": "integer"}},
      "required": ["path"]}),
    ("list_files",
     "List the files of this game's folder (or of the kit: autopilot/kit/...) whose paths match a glob, e.g. "
     "`codex/**/*.txt`, `runs/*/report.json` or `codex/**/*.{cpp,h}` (** matches any folders, {a,b} either), the "
     "newest first. When they are too many for one answer, all of them are written to a file of this run, and the "
     "answer says where. Names starting with a dot (Autopilot's own records) are left out unless the glob names one.",
     {"type": "object", "properties": {"pattern": {"type": "string"}}, "required": ["pattern"]}),
    ("search",
     "Search this game's folder (or `path`, a folder or file in it) for a regular expression: matching lines, whole, "
     "as path:line: text. `glob` limits it to some files, e.g. `codex/**/*.txt` or `**/*.{cpp,h}`. When the matches "
     "are too long for one answer, all of them are written to a file of this run, and the answer gives how many there "
     "are in which files, that file, and where they are (path:line). Files and folders whose names start with a dot "
     "(Autopilot's own records) are left out unless `path` or `glob` names one.",
     {"type": "object", "properties": {"pattern": {"type": "string"}, "path": {"type": "string"},
                                       "glob": {"type": "string"}}, "required": ["pattern"]}),
    ("write_file",
     "Write a whole file: this run's harness.py (or other files in this run's folder), notes.md or lib.py. Nothing "
     "else may be written.",
     {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
      "required": ["path", "content"]}),
    ("edit_file",
     "Replace `old` with `new` in a file that may be written (see write_file). `old` must appear exactly once, unless "
     "`replace_all` is true.",
     {"type": "object", "properties": {"path": {"type": "string"}, "old": {"type": "string"}, "new": {"type": "string"},
                                       "replace_all": {"type": "boolean"}}, "required": ["path", "old", "new"]}),
)
FILE_TOOLS = tuple(name for name, _, _ in FILE_TOOL_SPECS)


class Refused(Exception):
    pass


def _real(path):
    return os.path.normcase(os.path.realpath(path))


def _inside(path, folder):
    path, folder = _real(path), _real(folder)
    return path == folder or path.startswith(folder.rstrip(os.sep) + os.sep)


class FileTools:
    """The file tools for one run: root, the game's folder; run_rel, this run's folder in it (runs/<id>); package,
    the autopilot package (its KIT.md and kit/ may be read)."""

    def __init__(self, root, run_rel, package):
        self.root = os.path.realpath(root)
        self.run_dir = os.path.join(self.root, *run_rel.split("/"))
        self.package = os.path.realpath(package)

    # -- what may be read and written --------------------------------------------------------------------------

    def _path(self, path):
        if not isinstance(path, str) or not path.strip():
            raise Refused("No path given.")
        path = path.strip().replace("\\", "/")
        if os.path.isabs(path):
            return os.path.realpath(path)
        full = os.path.realpath(os.path.join(self.root, *path.split("/")))
        kit = self._kit(path)
        return kit if kit and not os.path.exists(full) else full

    def _kit(self, path):
        """The kit's own path for a relative autopilot/KIT.md or autopilot/kit[/...] (as the tools' texts name them),
        else None."""
        parts = [p for p in path.split("/") if p not in ("", ".")]
        if len(parts) < 2 or parts[0].lower() != "autopilot":
            return None
        if (len(parts) == 2 and parts[1].lower() == "kit.md") or parts[1].lower() == "kit":
            name = "KIT.md" if parts[1].lower() == "kit.md" else "kit"
            return os.path.realpath(os.path.join(self.package, name, *parts[2:]))
        return None

    def readable(self, path):
        full = self._path(path)
        name = os.path.basename(full).lower()
        if name.endswith(".env") or ".env." in name or name == ".env":
            raise Refused(f"{path}: environment files are never read.")
        kit = (_real(full) == _real(os.path.join(self.package, "KIT.md"))
               or _inside(full, os.path.join(self.package, "kit")))
        if not (_inside(full, self.root) or kit):
            raise Refused(f"{path}: only this game's folder and the kit (autopilot/KIT.md, autopilot/kit/) "
                          "may be read.")
        return full

    def writable(self, path):
        full = self._path(path)
        in_run = _inside(full, self.run_dir) and _real(full) != _real(self.run_dir)
        top = _real(full) in (_real(os.path.join(self.root, "notes.md")), _real(os.path.join(self.root, "lib.py")))
        if not (in_run or top):
            rel = os.path.relpath(self.run_dir, self.root).replace(os.sep, "/")
            raise Refused(f"{path}: only {rel}/ (this run's folder), notes.md and lib.py may be written.")
        return full

    def rel(self, full):
        if _inside(full, self.root):
            return os.path.relpath(full, self.root).replace(os.sep, "/")
        if _inside(full, self.package):  # (the kit, named as the tools' texts name it: autopilot/kit/...)
            return "autopilot/" + os.path.relpath(full, self.package).replace(os.sep, "/")
        return full.replace(os.sep, "/")

    def _existing(self, path):
        """readable(path), refused when nothing is there (rather than a search of nothing finding no match)."""
        full = self.readable(path)
        if not os.path.exists(full):
            raise Refused(f"{path}: there is no such file or folder in this game's folder"
                          + (" or the kit." if self._kit(path.strip().replace("\\", "/")) else
                             " (paths are relative to it; the kit is autopilot/KIT.md and autopilot/kit/)."))
        return full

    # -- the tools ---------------------------------------------------------------------------------------------

    def call(self, tool, args):
        """The tool's answer as text (a refusal or failure is text too)."""
        try:
            return getattr(self, tool)(**{k: v for k, v in args.items() if v is not None})
        except Refused as e:
            return f"Refused: {e}"
        except TypeError as e:
            return f"Bad arguments for {tool}: {e}"
        except (OSError, ValueError, re.error) as e:
            return f"{tool} failed: {type(e).__name__}: {e}"

    def read_file(self, path, offset=1, limit=MAX_READ_LINES, column=1):
        """Lines offset.. (at most limit), numbered and whole, in at most whole.ANSWER_CHARS characters; the answer
        ends saying where to read on. A line longer than a call holds comes in parts (column: where in the offset
        line to start, from 1)."""
        full = self._existing(path)
        if os.path.isdir(full):
            return f"{path} is a folder: use list_files."
        offset, limit = max(1, int(offset)), max(1, min(int(limit), MAX_READ_LINES))
        column = max(1, int(column))
        with open(full, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
        room = whole.ANSWER_CHARS - 200  # (for the line saying where to read on)
        shown, size, n = [], 0, offset
        while n <= len(lines) and len(shown) < limit:
            start = column - 1 if n == offset else 0
            row = f"{n:6}\t{lines[n - 1][start:]}"
            if size + len(row) + 1 <= room:
                shown.append(row)
                size += len(row) + 1
                n += 1
                continue
            if shown:
                return "\n".join(shown) + (f"\n(one call gives at most {whole.ANSWER_CHARS:,} characters: read on "
                                           f"from line {n})")
            # A line longer than one call holds: this call gives the part that fits, and says where it goes on
            line = lines[n - 1]
            part = line[start:start + room - 8]
            return (f"{n:6}\t{part}\n(line {n} is {len(line):,} characters: this is characters {start + 1:,}-"
                    f"{start + len(part):,} of it; read on with offset={n}, column={start + len(part) + 1})")
        more = len(lines) - (n - 1)
        if not shown:
            return "(empty)" if not lines else f"(the file has {len(lines)} lines: none from line {offset})"
        tail = f"\n({more} more lines: read on from line {n})" if more > 0 else ""
        if column > 1:  # (the first line shown is the rest of a long one)
            shown[0] = shown[0].replace("\t", f"\t(from character {column:,}) ", 1)
        return "\n".join(shown) + tail

    def _walk(self, top, hidden=False):
        """Every file under top. A file or folder whose name starts with a dot is Autopilot's own record keeping
        (.codex-seen.json.gz, codex/.fetched.json, .indexing/, ...): left out unless `hidden` (the search or listing
        names one)."""
        for folder, dirs, names in os.walk(top):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and (hidden or not d.startswith(".")))
            for name in names:
                if hidden or not name.startswith("."):
                    yield os.path.join(folder, name)

    def _kept(self, stem, text):
        """text written whole to this run's folder (runs/<id>/whole/<stem>-<k>.txt): its path in the game's folder."""
        return whole.kept(self.run_dir, self.rel(self.run_dir), stem, text)[1]

    def list_files(self, pattern):
        rx = _glob_rx(pattern)
        files = list(self._walk(self.root, hidden=_names_hidden(_bare(pattern))))
        if _bare(pattern).lower().startswith("autopilot/") and not os.path.isdir(os.path.join(self.root, "autopilot")):
            # (the kit's files, when the pattern names them and the game's folder has no autopilot/ of its own)
            files += [os.path.join(self.package, "KIT.md"), *self._walk(os.path.join(self.package, "kit"))]
        found = [full for full in files if rx.fullmatch(self.rel(full))]
        if not found:
            return f"No files match {pattern}."
        found.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        text = "\n".join(self.rel(p) for p in found)
        if len(text) <= whole.ANSWER_CHARS:
            return text
        return whole.deliver(text, whole.ANSWER_CHARS, self._kept("list", text + "\n"),
                             what=f"The list of the {len(found)} files that match {pattern}")

    def search(self, pattern, path=".", glob=None):
        """Every matching line, whole. Too long for one answer: all of them go to runs/<id>/whole/search-<k>.txt, and
        the answer gives their count, how many each file has, that file and the locations (path:line) that fit."""
        rx = re.compile(pattern)
        top = self._existing(path)
        files = [top] if os.path.isfile(top) else self._walk(
            top, hidden=_names_hidden(self.rel(top)) or bool(glob and _names_hidden(_bare(glob))))
        only = _glob_rx(glob) if glob else None
        asked_kept = bool(KEPT.match(self.rel(top) + "/"))
        out = None  # (the file all matches go to, once they are more than an answer holds)
        shown, size, places = [], 0, []
        folder = os.path.join(self.run_dir, whole.FOLDER)
        name = None
        try:
            for full in files:
                rel = self.rel(full)
                base = os.path.basename(full).lower()
                if (only and not only.fullmatch(rel)) or base.endswith(".env") or ".env." in base:
                    continue
                if not asked_kept and KEPT.match(rel):
                    continue
                try:
                    with open(full, encoding="utf-8", errors="strict") as f:
                        for n, line in enumerate(f, 1):
                            if not rx.search(line):
                                continue
                            hit = f"{rel}:{n}: {line.rstrip(chr(13) + chr(10))}"
                            places.append((rel, n))
                            size += len(hit) + 1
                            if out is None and size <= whole.ANSWER_CHARS:
                                shown.append(hit)
                                continue
                            if out is None:  # (from here they go to a file: the ones so far first)
                                os.makedirs(folder, exist_ok=True)
                                name = whole.next_name(folder, "search")
                                out = open(os.path.join(folder, name), "w", encoding="utf-8", newline="\n")
                                out.write("".join(h + "\n" for h in shown))
                                shown = None
                            out.write(hit + "\n")
                except (UnicodeDecodeError, OSError):
                    continue  # (not text)
            if out is None:
                return "\n".join(shown) or f"No match for {pattern}."
            counts = Counter(rel for rel, _ in places)
            out.write("\nMatches per file:\n" + "".join(f"{rel}: {k}\n" for rel, k in counts.items()))
        finally:
            if out is not None:
                out.close()
        where = f"{self.rel(self.run_dir)}/{whole.FOLDER}/{name}"
        text = (f"{len(places)} matches for {pattern} in {len(counts)} files: more than one answer holds, so all of "
                f"them, whole lines as path:line: text, are in {where} (one a line; how many each file has at its "
                "end). Read it there, read the lines below in their files, or narrow the search.")
        per = [f"{rel}: {k}" for rel, k in counts.items()]
        k = whole.listed(per, (whole.ANSWER_CHARS - len(text)) // 3)
        text += "\nMatches per file" + (f" (the first {k} of {len(per)} files; all at the end of {where})"
                                        if k < len(per) else "") + ":\n" + "\n".join(per[:k])
        locations = [f"{rel}:{n}" for rel, n in places]
        k = whole.listed(locations, whole.ANSWER_CHARS - len(text) - 120)
        text += ("\nWhere they are (path:line)" + (f", the first {k} of {len(locations)} (the rest in {where})"
                                                     if k < len(locations) else "") + ":\n" + "\n".join(locations[:k]))
        return text

    def write_file(self, path, content):
        full = self.writable(path)
        data = str(content)
        if len(data.encode("utf-8")) > MAX_WRITE_BYTES:
            raise Refused(f"{path}: over {MAX_WRITE_BYTES} bytes.")
        memory.write_atomic(full, data)  # (in one step: another run or a memory sync never reads half of it)
        return f"Wrote {self.rel(full)} ({len(data.splitlines())} lines)."

    def edit_file(self, path, old, new, replace_all=False):
        full = self.writable(path)
        with open(full, encoding="utf-8") as f:
            text = f.read()
        count = text.count(old) if old else 0
        if count == 0:
            return f"{path}: `old` was not found (it must match exactly, whitespace included)."
        if count > 1 and not replace_all:
            return f"{path}: `old` appears {count} times: give more of the text around it, or replace_all."
        return self.write_file(path, text.replace(old, new) if replace_all else text.replace(old, new, 1))


def _bare(pattern):
    """A glob with / and without a leading ./"""
    pattern = str(pattern).strip().replace("\\", "/")
    while pattern.startswith("./"):
        pattern = pattern[2:]
    return pattern


def _names_hidden(path):
    """Whether a path or glob names a file or folder whose name starts with a dot (codex/.hashes.json, **/.*)."""
    return any(part.startswith(".") and part not in (".", "..") for part in str(path).replace("\\", "/").split("/"))


def _glob_rx(pattern):
    """A glob as a regular expression over /-separated relative paths: * within a folder, ** across folders, ? one
    character, {a,b} either (nested too; a { without its } is itself)."""
    return re.compile(_glob_body(_bare(pattern) or "**"), re.I if os.name == "nt" else 0)


def _glob_body(pattern):
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif pattern.startswith("**", i):
            out, i = out + ".*", i + 2
        elif pattern[i] == "*":
            out, i = out + "[^/]*", i + 1
        elif pattern[i] == "?":
            out, i = out + "[^/]", i + 1
        elif pattern[i] == "{" and (alternatives := _braces(pattern, i)):
            options, i = alternatives
            out += "(?:" + "|".join(_glob_body(o) for o in options) + ")"
        else:
            out, i = out + re.escape(pattern[i]), i + 1
    return out


def _braces(pattern, start):
    """The {a,b,...} at start: (its alternatives, the index after its }), or None when it has no } or no comma."""
    depth, options, last = 0, [], start + 1
    for i in range(start, len(pattern)):
        if pattern[i] == "{":
            depth += 1
        elif pattern[i] == "}":
            depth -= 1
            if depth == 0:
                options.append(pattern[last:i])
                return (options, i + 1) if len(options) > 1 else None
        elif pattern[i] == "," and depth == 1:
            options.append(pattern[last:i])
            last = i + 1
    return None
