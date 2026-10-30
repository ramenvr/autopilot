"""Remote Control calls through a running tools/probe.py, for looking into a game from the shell.

    python tools/rc.py call <object path> <function> ['{"Param": ...}']
    python tools/rc.py get <object path> [property]
    python tools/rc.py table <data table path> [column ...]     # rows of a data table, as text
    python tools/rc.py op <op> ['{args}']                        # any bridge op (context, screen, ui, ...)
"""

import json
import sys
import urllib.request

PORT = 8771
DT_LIB = "/Script/Engine.Default__DataTableFunctionLibrary"


def op(name, args=None, timeout=60):
    body = json.dumps({"op": name, "args": args or {}, "timeout": timeout}).encode()
    with urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{PORT}", body), timeout=timeout + 5) as r:
        out = json.loads(r.read())
    if not out.get("ok"):
        raise RuntimeError(out.get("error"))
    return out["result"]


def call(obj, fn, params=None):
    return op("rc", {"verb": "call", "body": {"objectPath": obj, "functionName": fn, "parameters": params or {}}})


def get(obj, prop=None):
    body = {"objectPath": obj}
    if prop:
        body["propertyName"] = prop
    return op("rc", {"verb": "property", "body": body})


def table(path, columns=None):
    """{row name: {column: value text}} for a data table."""
    rows = call(DT_LIB, "GetDataTableRowNames", {"Table": path}).get("OutRowNames", [])
    if not columns:
        columns = call(DT_LIB, "GetDataTableColumnNames", {"Table": path}).get("ReturnValue", [])
    out = {r: {} for r in rows}
    for c in columns:
        values = call(DT_LIB, "GetDataTableColumnAsString",
                      {"DataTable": path, "PropertyName": c}).get("ReturnValue", [])
        for r, v in zip(rows, values):
            out[r][c] = v
    return out


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    cmd, rest = sys.argv[1], sys.argv[2:]
    if cmd == "call":
        result = call(rest[0], rest[1], json.loads(rest[2]) if len(rest) > 2 else None)
    elif cmd == "get":
        result = get(rest[0], rest[1] if len(rest) > 1 else None)
    elif cmd == "table":
        result = table(rest[0], rest[1:] or None)
    else:
        result = op(rest[0], json.loads(rest[1]) if len(rest) > 1 else None)
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
