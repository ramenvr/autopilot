"""File tools for an agent that has none of its own that Autopilot can hold to the game's folder (Codex: its shell and
patch tools are off, autopilot/agents/codex.py): read, list and search the game's folder and the kit; write and edit
only this run's folder, notes.md and lib.py. The same rules as OpenCode's permissions (autopilot/agents/opencode.py).

Paths are relative to the game's folder, with / (absolute ones too, but only inside what may be read). Files named
*.env or *.env.* are never read.
"""

import os
import re

MAX_READ_LINES = 2000
MAX_LINE = 2000  # characters of one line shown
MAX_LISTED = 500
MAX_MATCHES = 200
MAX_WRITE_BYTES = 2 * 1024 * 1024
SKIP_DIRS = {".git", "__pycache__", ".agent", "node_modules"}

FILE_TOOL_SPECS = (
    ("read_file",
     "Read a text file of this game's folder, or of the kit (autopilot/KIT.md, autopilot/kit/). Lines come numbered; "
     "`offset` (the first line, from 1) and `limit` (at most 2000) read a long file in parts.",
     {"type": "object", "properties": {"path": {"type": "string"}, "offset": {"type": "integer"},
                                       "limit": {"type": "integer"}}, "required": ["path"]}),
    ("list_files",
     "List the files of this game's folder whose paths match a glob, e.g. `codex/**/*.txt` or `runs/*/report.json` "
     "(** matches any folders). At most 500, the newest first.",
     {"type": "object", "properties": {"pattern": {"type": "string"}}, "required": ["pattern"]}),
    ("search",
     "Search this game's folder (or `path`, a folder or file in it) for a regular expression: matching lines as "
     "path:line: text. `glob` limits it to some files, e.g. `codex/**/*.txt`. At most 200 matches.",
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
        full = path if os.path.isabs(path) else os.path.join(self.root, *path.split("/"))
        return os.path.realpath(full)

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
        return full.replace(os.sep, "/")

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

    def read_file(self, path, offset=1, limit=MAX_READ_LINES):
        full = self.readable(path)
        if os.path.isdir(full):
            return f"{path} is a folder: use list_files."
        offset, limit = max(1, int(offset)), max(1, min(int(limit), MAX_READ_LINES))
        with open(full, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
        shown = [f"{n:6}\t{line[:MAX_LINE]}" for n, line in enumerate(lines[offset - 1:offset - 1 + limit], offset)]
        more = len(lines) - (offset - 1 + len(shown))
        tail = f"\n({more} more lines: read on from line {offset + len(shown)})" if more > 0 else ""
        return ("\n".join(shown) or "(empty)") + tail

    def _walk(self, top):
        for folder, dirs, names in os.walk(top):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
            for name in names:
                yield os.path.join(folder, name)

    def list_files(self, pattern):
        rx = _glob_rx(pattern)
        found = [full for full in self._walk(self.root) if rx.fullmatch(self.rel(full))]
        found.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        shown = [self.rel(p) for p in found[:MAX_LISTED]]
        more = f"\n({len(found) - MAX_LISTED} more: narrow the pattern)" if len(found) > MAX_LISTED else ""
        return ("\n".join(shown) or f"No files match {pattern}.") + more

    def search(self, pattern, path=".", glob=None):
        rx = re.compile(pattern)
        top = self.readable(path)
        files = [top] if os.path.isfile(top) else self._walk(top)
        only = _glob_rx(glob) if glob else None
        hits = []
        for full in files:
            rel = self.rel(full)
            name = os.path.basename(full).lower()
            if (only and not only.fullmatch(rel)) or name.endswith(".env") or ".env." in name:
                continue
            try:
                with open(full, encoding="utf-8", errors="strict") as f:
                    for n, line in enumerate(f, 1):
                        if rx.search(line):
                            hits.append(f"{rel}:{n}: {line.rstrip()[:MAX_LINE]}")
                            if len(hits) >= MAX_MATCHES:
                                return "\n".join(hits) + f"\n(stopped at {MAX_MATCHES} matches: narrow the search)"
            except (UnicodeDecodeError, OSError):
                continue  # (not text)
        return "\n".join(hits) or f"No match for {pattern}."

    def write_file(self, path, content):
        full = self.writable(path)
        data = str(content)
        if len(data.encode("utf-8")) > MAX_WRITE_BYTES:
            raise Refused(f"{path}: over {MAX_WRITE_BYTES} bytes.")
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8", newline="\n") as f:
            f.write(data)
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


def _glob_rx(pattern):
    """A glob as a regular expression over /-separated relative paths: * within a folder, ** across folders."""
    pattern = str(pattern).strip().replace("\\", "/")
    while pattern.startswith("./"):
        pattern = pattern[2:]
    pattern = pattern or "**"
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
        else:
            out, i = out + re.escape(pattern[i]), i + 1
    return re.compile(out, re.I if os.name == "nt" else 0)

