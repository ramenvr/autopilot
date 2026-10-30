"""How much Aura's relay adds to a Jev call, measured three ways in one Aura run (started as autopilot/aura.py starts
one; finished at the end, so Aura bills its few tokens like any run's):

- steady: the same small decision asked of TypeSafe directly (with your own TYPESAFE_API_KEY) and through the
  relay, alternately, back to back.
- after idle: a decision after a pause (the agent thinking between episodes, a long option), asked through the
  relay by a plain client (the SDK's 5 s keep-alive, no warm-up) and by Jev's own client (autopilot.runtime.jev_client:
  its connection kept open and warm), with TypeSafe directly for reference.
- stale status (--old-relay): a pause past the relay's 10 s run-status cache, through two relays on one held-open
  connection each, to show what a deployment that reads Redis before answering costs next to this one.

    python tools/relay_latency.py --aura https://<the Aura server> [--calls 30] [--gaps 12,30] [--gap-calls 6]
        [--old-relay https://<an earlier deployment>] [--token <Aura session token>]

The Aura session is the Aura client's on this machine unless --token is given.
"""

import argparse
import os
import statistics
import sys
import time
from urllib.parse import urlsplit, urlunsplit

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx2  # noqa: E402
from dotenv import load_dotenv  # noqa: E402
from typesafe_sdk import Choice, RetryPolicy, TypeSafeClient  # noqa: E402
from typesafe_sdk.constants import DEFAULT_BASE_URL  # noqa: E402

from autopilot import aura, runtime  # noqa: E402

STATE = {"goal": "Pick the option with the bigger number.", "situation": {"a": 3, "b": 9}}
QUESTIONS = {"pick": Choice(instructions="Pick the bigger number.",
                            criteria={"a": "the number 3", "b": "the number 9"})}
RETRY = RetryPolicy(max_retries=0)


def timed(client):
    t = time.perf_counter()
    client.system_one(state=STATE, questions=QUESTIONS, retry=RETRY, timeout=10)
    return (time.perf_counter() - t) * 1000


def summary(name, ms):
    ms = sorted(ms)
    p90 = ms[min(len(ms) - 1, int(len(ms) * 0.9))]
    print(f"  {name:>24}: median {statistics.median(ms):6.0f} ms   p90 {p90:6.0f} ms   min {ms[0]:6.0f} ms"
          f"   max {ms[-1]:6.0f} ms   (n={len(ms)})")


def plain(base_url, key, keepalive=None):
    """A client as the SDK makes one (5 s keep-alive), or holding its connection for keepalive seconds."""
    http = None if keepalive is None else httpx2.Client(timeout=10, limits=httpx2.Limits(keepalive_expiry=keepalive))
    return TypeSafeClient(base_url=base_url, api_key=key, retry=RETRY, timeout=10, http_client=http,
                          compression=False,  # (as runtime.jev_client: the relay doesn't pass Content-Encoding on)
                          model=runtime.jev_model())


def on_host(url, host_url):
    """url with its scheme and host taken from host_url."""
    u, h = urlsplit(url), urlsplit(host_url)
    return urlunsplit((h.scheme, h.netloc, u.path, u.query, u.fragment))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--aura", required=True, help="Aura's server URL (what AUTOPILOT_AURA_URL would be)")
    ap.add_argument("--calls", type=int, default=30, help="steady calls each way")
    ap.add_argument("--gaps", default="12,30", help="idle pauses to measure after, in seconds (comma-separated; '' "
                                                    "for none)")
    ap.add_argument("--gap-calls", type=int, default=6, help="decisions after each pause")
    ap.add_argument("--old-relay", help="an earlier deployment's URL, for the stale-status comparison")
    ap.add_argument("--token", help="an Aura session token (default: the Aura client's on this machine)")
    args = ap.parse_args()
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
    if not os.environ.get("TYPESAFE_API_KEY"):
        sys.exit("Direct calls need your own TYPESAFE_API_KEY (in the environment or .env)")
    gaps = [float(g) for g in args.gaps.split(",") if g.strip()]
    os.environ["AUTOPILOT_AURA_URL"] = args.aura
    token = args.token or aura.client_session()[1]
    if not token:
        sys.exit("No Aura session: sign in to the Aura client, or pass --token")
    # (read before the run: it points both at the relay while it lasts)
    key = os.environ["TYPESAFE_API_KEY"]
    direct_url = os.environ.get("TYPESAFE_BASE_URL") or DEFAULT_BASE_URL
    direct = plain(direct_url, key)
    try:
        run = aura.Run(token, "goal", project="relay latency").start()
    except aura.Refused as e:
        sys.exit(f"Aura refused the run: HTTP {e.status} {e.code}: {e.message}")
    with run:
        relay = run.relay["typesafe"]
        print(f"relay: {relay}")

        print("\nsteady (back to back):")
        relayed = plain(relay, run.run_token)
        timed(direct), timed(relayed)  # (connections opened, each region warm)
        steady = {"direct": [], "relayed": []}
        for _ in range(args.calls):
            steady["direct"].append(timed(direct))
            steady["relayed"].append(timed(relayed))
        summary("direct", steady["direct"])
        summary("relayed", steady["relayed"])
        print(f"  the relay adds {statistics.median(steady['relayed']) - statistics.median(steady['direct']):.0f} ms "
              "to a median Jev call")

        for gap in gaps:
            print(f"\nafter {gap:.0f} s idle:")
            # (each its own connection, opened and warm before the first pause)
            old = plain(relay, run.run_token)
            jev = runtime.jev_client()  # (the run's relay and token, from the environment the run set)
            ref = plain(direct_url, key)
            for c in (old, jev, ref):
                timed(c)
            times = {"direct": [], "relayed, plain client": [], "relayed, Jev's client": []}
            order = [("relayed, Jev's client", jev), ("relayed, plain client", old), ("direct", ref)]
            for i in range(args.gap_calls):
                time.sleep(gap)
                # (one after another: each waited the pause, and the order rotates so none always goes first)
                for name, c in order[i % 3:] + order[:i % 3]:
                    times[name].append(timed(c))
            for name, ms in times.items():
                summary(name, ms)
            jev.close()

        if args.old_relay:
            print("\nstale run status (12 s idle, connection held open, so only the deployment differs):")
            new_c = plain(relay, run.run_token, keepalive=300)
            old_c = plain(on_host(relay, args.old_relay), run.run_token, keepalive=300)
            timed(new_c), timed(old_c)
            times = {"this deployment": [], "the earlier one": []}
            for i in range(args.gap_calls):
                time.sleep(12)
                pair = [("this deployment", new_c), ("the earlier one", old_c)]
                for name, c in pair if i % 2 == 0 else pair[::-1]:
                    times[name].append(timed(c))
            for name, ms in times.items():
                summary(name, ms)


if __name__ == "__main__":
    main()
