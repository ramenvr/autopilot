"""How long a round trip to a Roblox place takes through Roblox Studio's built-in MCP server, and so how fast a harness
can play it: N execute_luau calls in play mode, each the engine's own batched observe (a pose, a few transforms and
reads in one call, as engines/roblox_connector.py sends a game.batch), timed one after another.

    python tools/roblox_latency.py [--calls 100] [--datamodel Client] [--no-start] [--ops 4]
    python tools/roblox_latency.py --tools      # what the server offers: tools/list, with each tool's input schema
    python tools/roblox_latency.py --fake       # the plumbing against FakeStudio (no Studio; numbers mean nothing)

Studio must be open on a place with its MCP server on (Assistant > ... > Settings > MCP Servers > Enable Studio as MCP
server); the server is started as the engine starts it (AUTOPILOT_ROBLOX_MCP_CMD, else Roblox's documented command).
Without --no-start, play mode is started first (start_stop_play) and stopped again at the end.

It prints p50 / p95 and the harness settings they allow: tick_hz up to one observe a round trip, and a tempo (Jev's
decisions a second) of one per two round trips (an observe, then the input the decision sends), below the kit's 4-8
for reflex play when the round trip is long.
"""

import argparse
import json
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autopilot.engines import roblox_connector as roblox  # noqa: E402
from autopilot.engines import roblox_mcp as mcp  # noqa: E402

# Reads like a harness's observe: the player, a handful of things, a property, the screen's texts
OBSERVE = [("pose", {}), ("find", {"things": True, "limit": 8}), ("context", {}), ("screen", {})]


def percentile(ms, p):
    ms = sorted(ms)
    return ms[min(len(ms) - 1, max(0, round(p / 100 * (len(ms) - 1))))]


def recommend(p50, p95):
    """(tick_hz, tempo) a harness can keep at this round trip (ms): an observe each tick, and one decision per two
    round trips, taking the slow end (p95) so most decisions keep up."""
    rt = max(1.0, float(p95)) / 1000.0
    tick_hz = min(30.0, 1.0 / rt)
    tempo = min(8.0, 1.0 / (2 * rt))
    return round(tick_hz, 1), round(tempo, 1)


def measure(studio, calls, datamodel, ops, say=print):
    """Milliseconds of each of `calls` execute_luau calls of the engine's batched observe (`ops` reads in one call)."""
    batch = (OBSERVE * (ops // len(OBSERVE) + 1))[:ops]
    nonce = "@@lat@@"
    echo = (os.environ.get(mcp.ENV_RESULT) or "").strip().lower() == "print"
    ms, failed = [], 0
    for i in range(calls):
        code = roblox.luau_chunk(batch, nonce=nonce, echo=echo)
        t = time.perf_counter()
        try:
            text = studio.execute_luau(code, datamodel=datamodel)
        except mcp.McpError as e:
            failed += 1
            say(f"  call {i + 1}: {e}")
            if failed >= 5 and not ms:
                raise
            continue
        took = (time.perf_counter() - t) * 1000
        if roblox.parse_answer(text, nonce) is None:
            if not echo:  # (a server that gives back only what a chunk prints: print from now on, as the engine does)
                echo = True
                continue
            failed += 1
            say(f"  call {i + 1}: no JSON answer ({str(text)[:120]!r})")
            continue
        ms.append(took)
    return ms, failed


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--calls", type=int, default=100)
    ap.add_argument("--datamodel", default="Client", choices=mcp.DATAMODELS,
                    help="where the reads run (the engine reads the player's Client while playing)")
    ap.add_argument("--ops", type=int, default=4, help="reads in each call (a harness's observe: 3-8)")
    ap.add_argument("--no-start", action="store_true", help="don't start (or stop) play mode")
    ap.add_argument("--tools", action="store_true", help="print the server's tools/list and exit")
    ap.add_argument("--fake", action="store_true", help="against FakeStudio (no Studio): checks the tool runs")
    args = ap.parse_args(argv)

    transport = mcp.LocalTransport(mcp.FakeStudio()) if args.fake else None
    t0 = time.perf_counter()
    try:
        studio = mcp.Studio.connect(transport)
    except mcp.McpError as e:
        print(e, file=sys.stderr)
        return 2
    print(f"Connected to {(studio.client.server_info.get('serverInfo') or {}).get('name') or 'the server'} "
          f"({' '.join(studio.client.transport.cmd)}) in {(time.perf_counter() - t0) * 1000:.0f} ms; "
          f"{len(studio.tools)} tools")
    try:
        if args.tools:
            print(json.dumps(list(studio.tools.values()), indent=1))
            return 0
        for tool, why in studio.check().items():
            print(f"note: no {tool}: {why}")
        started = False
        if not args.no_start and args.datamodel != "Edit":
            print("Starting play mode ...")
            studio.start_stop_play(True)
            started = True
            time.sleep(0 if args.fake else 3.0)  # (the character spawns)
        try:
            ms, failed = measure(studio, args.calls, args.datamodel, args.ops)
        finally:
            if started:
                studio.start_stop_play(False)
        if not ms:
            print("No call answered: nothing to measure")
            return 1
        p50, p95 = statistics.median(ms), percentile(ms, 95)
        tick_hz, tempo = recommend(p50, p95)
        print(f"\nexecute_luau round trip ({args.datamodel}, {args.ops} reads a call, n={len(ms)}, {failed} failed):")
        print(f"  p50 {p50:6.0f} ms   p95 {p95:6.0f} ms   min {min(ms):6.0f} ms   max {max(ms):6.0f} ms")
        print(f"Recommended for a harness on this machine: tick_hz <= {tick_hz:g}, tempo <= {tempo:g} decisions/s"
              + ("  (too slow for the kit's reflex play, 4-8)" if tempo < 4 else ""))
        if args.fake:
            print("(against FakeStudio: these numbers say nothing about Studio)")
        return 0
    finally:
        studio.close()


if __name__ == "__main__":
    sys.exit(main())
