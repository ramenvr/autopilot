"""Trying again what failed for a reason that passes when Autopilot talks to Aura or its storage: the network, or an
answer saying busy or down (STATUSES). Shared run data's uploads (autopilot/training.py) and the memory kept with Aura
(autopilot/aura_memory.py) go through call(); a refusal (HTTP 4xx: consent, a bad token, the day's quota) is never
tried again, it is left for the next run.

    call(fn, what, first)  fn() tried again TRIES times after a failure that passes, the waits doubling from `first`
                           seconds (each within 20% of it, so machines that failed together don't come back together)
"""

import random
import time

import requests

TRIES = 3  # times something that failed for a passing reason is tried again
STATUSES = {408, 500, 502, 503, 504}  # HTTP answers that pass (a 429 is the day's quota: tomorrow, not in a minute)


def passing(e):
    """Whether something that failed with `e` may work if tried again soon: the network failing, or an error that says
    so (its `passing`, set from STATUSES)."""
    return isinstance(e, requests.RequestException) or bool(getattr(e, "passing", False))


def pause(seconds):
    time.sleep(seconds)  # (the tests make it return at once)


def call(fn, what, first, sleep=None, say=print):
    """fn()'s answer, fn tried again up to TRIES times after a passing failure (waits of about first, 2 x first, 4 x
    first seconds); the last failure is raised. what: what is being done, for the note each wait prints."""
    for attempt in range(TRIES + 1):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 - raised again unless it passes
            if attempt >= TRIES or not passing(e):
                raise
            wait = first * 2 ** attempt * random.uniform(0.8, 1.2)
            say(f"note: {what} failed ({type(e).__name__}: {e}); trying again in {wait:.0f} s")
            (sleep or pause)(wait)
