"""A stand-in for Roblox Studio's MCP server (StudioMCP) for tests/test_studio_mcp.py: MCP over stdio, a JSON-RPC
message a line. Its tools: list_roblox_studios (the Studios in argv[1], JSON; a Studio with "player": n is a multiplayer
test's client, one without a name and player its server), execute_luau (a client's player, as the client says it) and
screen_capture (a 64x48 PNG, or an error when argv[2] is "fail", or no answer to the first when it is
"hang:<file>"; the picture's top-left pixel's red is the Studio's
place in the list). "late:<n>" in argv[2]: the first n lists are empty, as when Studio hasn't joined yet. It pings
the client once, as a server may, and writes a line of its own logging."""

import base64
import json
import os
import struct
import sys
import zlib


def png(width=64, height=48, mark=0):
    rows = b"".join(b"\x00" + b"".join(bytes(((x * 4 + mark * 100) % 256, y * 5 % 256, 128)) for x in range(width))
                    for y in range(height))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def main():
    studios = json.loads(sys.argv[1]) if len(sys.argv) > 1 else [{"id": "s1", "name": "Place1"}]
    fail = len(sys.argv) > 2 and sys.argv[2] == "fail"
    # ("hang:<file>": the first picture, of all the servers started, is never answered: the file says it was)
    marker = sys.argv[2][5:] if len(sys.argv) > 2 and sys.argv[2].startswith("hang:") else None
    hang = marker is not None and not os.path.exists(marker)
    late = int(sys.argv[2][5:]) if len(sys.argv) > 2 and sys.argv[2].startswith("late:") else 0
    out = sys.stdout
    print("StudioMCP stand-in starting", file=out, flush=True)  # (not JSON: a client skips it)
    pinged = False
    for line in sys.stdin:
        msg = json.loads(line)
        if "id" not in msg or "method" not in msg:
            continue  # (a notification, or the answer to our ping)
        method, rid = msg["method"], msg["id"]
        if method == "initialize":
            result = {"protocolVersion": msg["params"]["protocolVersion"], "capabilities": {"tools": {}},
                      "serverInfo": {"name": "StudioMCP stand-in", "version": "0"}}
        elif method == "tools/call":
            name = msg["params"]["name"]
            if not pinged:
                pinged = True
                out.write(json.dumps({"jsonrpc": "2.0", "id": "ping-1", "method": "ping"}) + "\n")
            if name == "list_roblox_studios":
                seen = [] if late > 0 else studios
                late -= 1
                result = {"content": [{"type": "text", "text": json.dumps({"studios": seen})}]}
            elif name == "execute_luau":
                args = msg["params"]["arguments"]
                studio = next(s for s in studios if s["id"] == args["studio_id"])
                if "player" not in studio:
                    result = {"content": [{"type": "text", "text": "Target is not reachable"}], "isError": True}
                else:
                    said = "player 1" if studio["player"] == 1 else "other"
                    result = {"content": [{"type": "text", "text": said}]}
            elif name == "screen_capture" and hang:
                hang = False
                open(marker, "w").close()
                continue
            elif name == "screen_capture" and fail:
                result = {"content": [{"type": "text", "text": "Studio isn't responding"}], "isError": True}
            elif name == "screen_capture":
                assert msg["params"]["arguments"]["studio_id"] in [s["id"] for s in studios]
                k = [s["id"] for s in studios].index(msg["params"]["arguments"]["studio_id"])
                result = {"content": [{"type": "image", "mimeType": "image/png",
                                       "data": base64.b64encode(png(64, 48, k)).decode("ascii")}]}
            else:
                result = {"content": [{"type": "text", "text": f"no tool {name}"}], "isError": True}
        else:
            out.write(json.dumps({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "no"}}) + "\n")
            out.flush()
            continue
        out.write(json.dumps({"jsonrpc": "2.0", "id": rid, "result": result}) + "\n")
        out.flush()


if __name__ == "__main__":
    main()
