"""Looking while playing: the game's last seconds of pictures answered as questions, in the background, while a
harness plays (docs/architecture.md, "Looking at the picture").

A harness reads the game's numbers; some of what stops the player shows only in the picture (a beam the numbers
don't name, a thing in the way of a shot, a menu over the view, a body stuck against a ledge). So while an episode
plays, a Watcher samples the recording's latest frames (the last WINDOW seconds, RATE frames a second: the recording
is already there, so looking costs the game nothing) and puts questions to a model that sees them: the default
questions below (is the player stuck; what happened to it: a hazard, a fall, an enemy, a moving object; does a menu
cover the view), optional ones a harness turns on (is the thing aimed at in plain sight), and the harness's own game
questions (Harness.watch_questions). The answers go to the harness (Harness.on_watch, game.watch) for its executors
to act on, and to the trace, so the agent reads what the picture showed beside what the numbers said. A look is made
every `every` seconds of play and right after what matters (a death, an option that failed, a stall), one at a time,
never on the tick thread: the run never waits for it.

At an episode's end the Watcher reviews its deaths: the last seconds before each, described in a sentence (the vision
model, when the run has one) with the question answers of the moment, into the trace and the summary. An agent that
reads "the player ran into a laser beam" fixes the harness in one revision; one that reads only "fell" guesses.

    Looker          answers questions about frames: through Jev (TypeSafe's System One takes images as assets: the
                    frames go as the state, fast and cheap) when the run has Jev, else, or when Jev's answers about
                    pictures fail, a vision model of the run's provider (vision.Wire) answering as JSON. Also
                    describe(): a sentence about frames (the vision model only).
    RemoteLooker    the Looker's surface in the sandbox's child (autopilot/sandbox.py): the parent, which holds the
                    frames and the keys, looks.
    Watcher         the runtime's: when to look, with what, and what to do with the answers.
"""

import json
import re
import threading
import time

from typesafe_sdk import Choice, Noul, Score

WINDOW = 5.0  # seconds of play one look covers
RATE = 3.0  # frames a second sampled from the recording for a look after an event
PERIODIC_RATE = 1.0  # ... for a look made because time passed (fewer frames: it costs less)
MAX_FRAMES = 15  # frames one look takes, at most
JEV_FRAMES = 6  # frames Jev is shown (its answers come fast; the vision model sees them all)
EVERY = 8.0  # seconds of game time between looks while nothing happens (Harness.watch_every overrides)
AFTER_EVENT = 0.7  # wall seconds after an event before its look, so the frames show what came of it
MIN_GAP = 1.5  # wall seconds between two looks, at least
TIMEOUT = 60  # seconds one look may take
REVIEW_MAX = 4  # deaths reviewed at an episode's end, at most (the latest)
REVIEW_WAIT = 25.0  # seconds an episode's end waits for its reviews
JEV_FAILURES = 2  # Jev answers about pictures failing this often in a row: the vision model from then on

# The default questions, asked at every look. The player is "the player character" to a model that sees the picture
DEFAULT = {
    "blocked": Noul(instructions="The player character is stuck: pushing against something without getting "
                                 "anywhere, or standing where it clearly means to move on but doesn't"),
    "cause": Choice(instructions="What happened to the player character at the end of these frames", criteria={
        "nothing": "it went on as before, alive and moving freely",
        "hazard": "it touched or was hit by a hazard: a beam, laser, fire, spikes, a blade, a trap, an explosion",
        "fall": "it fell into a gap, off an edge, into water or lava, or out of the world",
        "enemy": "it was hit, shot or caught by an enemy or a projectile",
        "moving_object": "it was crushed, swept away or pushed off by a moving object (a press, a bar, a pendulum, "
                         "a platform)",
        "died_unseen": "it died, respawned or was moved back for a reason that doesn't show",
    }),
    "menu": Noul(instructions="A menu, dialog, death screen, loading screen or prompt covers the game's view"),
}
# Optional ones a harness turns on by naming them in watch_questions ({"clear_shot": True})
OPTIONAL = {
    "clear_shot": Noul(instructions="The thing the player aims at (under the crosshair, in the middle of the view) is "
                                    "in plain sight, with nothing standing between it and the player"),
    "target_in_view": Noul(instructions="An enemy or target is in view on the screen"),
    "moving": Noul(instructions="The player character is moving (its place in the scene changes from frame to frame)"),
}
# What a death's review asks the vision model
REVIEW_QUESTION = ("In one or two sentences: what happened to the player character over the last moments of these "
                   "frames? Name what it touched, was hit by, or fell from, and where it was.")

SYSTEM = """You answer questions about frames of a video game for a program that plays it. You see the frames in
order, with the game time each shows, and the questions. Judge by what is visible in the frames; a thing you can't
make out gets 0.5. Answer with JSON only, nothing else: an object with one entry per question id: for a yes/no
question the probability (0 to 1) that the answer is yes; for a choice, an object of label to probability (they sum
to 1); for a score, the score. Add "note": one sentence saying what happened to the player character over the frames,
naming what it touched, was hit by, or fell from."""


def jsonable_questions(questions):
    """Questions as JSON (for the sandbox's relay): {id: the question's wire form}."""
    return {k: (q.model_dump(mode="json", exclude_none=True) if hasattr(q, "model_dump") else q)
            for k, q in questions.items()}


def questions_from(dumped):
    """Questions from their JSON (jsonable_questions)."""
    kinds = {"choice": Choice, "noul": Noul, "score": Score}
    out = {}
    for k, d in (dumped or {}).items():
        if hasattr(d, "model_dump"):
            out[k] = d
        elif isinstance(d, dict):
            out[k] = kinds.get(d.get("type", "noul"), Noul).model_validate(d)
    return out


def sample(frames, wall0, wall1, rate, limit=MAX_FRAMES):
    """Up to `limit` of `frames` (oldest first, each with "wall") captured from wall0 to wall1, about `rate` a
    second: the frame nearest each moment of a grid ending at wall1."""
    inside = [f for f in frames if wall0 <= f["wall"] <= wall1]
    if not inside:
        return []
    n = max(1, min(limit, int(round((wall1 - wall0) * rate)) + 1))
    chosen = []
    for i in range(n):
        at = wall1 - (n - 1 - i) / max(rate, 1e-6)
        best = min(inside, key=lambda f: abs(f["wall"] - at))
        if not chosen or chosen[-1] is not best:
            chosen.append(best)
    return chosen


class Looker:
    """Answers questions about the recording's frames. frames: the run's vision.Frames; jev: the run's Jev client
    (None: no Jev); route: a vision model's route (vision.route), or None; provider, usage, proxied: as
    vision.Describer's; log: a shared run's telemetry folder (each look goes to watch.jsonl there), or None."""

    def __init__(self, frames, jev=None, route=None, provider="anthropic", usage=None, proxied=None, log=None,
                 named=False):
        self.frames = frames
        self.jev = jev
        self.route = route
        self.provider = provider
        self.usage = usage
        self.proxied = proxied
        self.named = named
        self.log = log
        self.jev_failed = 0  # Jev's answers about pictures failing in a row
        self.jev_error = None  # why Jev's last answer about pictures failed (for the trace, once)
        self.calls = {"jev": 0, "model": 0, "describe": 0}
        self.lock = threading.Lock()

    def available(self):
        """Why no look can be made at all (no frames, no model that looks), or None."""
        if self.frames is None:
            return "the game isn't filmed in this run"
        if self.jev is None and self.route is None:
            return "no model that looks at pictures (no Jev, and no vision model of the agent's provider)"
        return None

    def can_describe(self):
        return self.route is not None and self.frames is not None

    def look(self, wall0, wall1, questions, facts=None, rate=RATE, limit=MAX_FRAMES):
        """The answers to questions (ids to Noul/Choice/Score) about the frames captured from wall0 to wall1:
        {id: probability (Noul) | {label: probability} (Choice) | score (Score), "frames": [game times], "by":
        "jev"|"model", "latency", "note"? (the model's sentence)}. Raises when nothing could be looked at."""
        why = self.available()
        if why:
            raise RuntimeError(why)
        frames = sample(self.frames.between(wall0, wall1), wall0, wall1, rate, limit)
        if not frames:
            raise RuntimeError("no frames of that stretch (the game drew no picture then)")
        started = time.time()
        answers = None
        by = None
        if self.jev is not None and self.jev_failed < JEV_FAILURES:
            try:
                answers = self._jev(frames[-JEV_FRAMES:], questions, facts)
                by = "jev"
                self.jev_failed = 0
            except Exception as e:  # noqa: BLE001 - the vision model answers instead
                self.jev_failed += 1
                self.jev_error = f"{type(e).__name__}: {str(e)[:200]}"
                if self.route is None:
                    raise RuntimeError(f"Jev couldn't answer about the frames: {type(e).__name__}: {e}") from None
        if answers is None:
            answers = self._model(frames, questions, facts)
            by = "model"
        answers["frames"] = [f["t"] for f in frames]
        answers["by"] = by
        answers["latency"] = round(time.time() - started, 2)
        if self.jev_error and self.jev is not None:
            answers["jev_error"] = self.jev_error
        self._log("look", frames, questions, answers)
        return answers

    def describe(self, wall0, wall1, question=REVIEW_QUESTION, rate=RATE, limit=MAX_FRAMES):
        """A vision model's sentence about the frames captured from wall0 to wall1 (None without a vision model or
        frames)."""
        if not self.can_describe():
            return None
        from . import vision

        frames = sample(self.frames.between(wall0, wall1), wall0, wall1, rate, limit)
        if not frames:
            return None
        parts = vision.frame_parts(frames) + [("text", question)]
        wire = vision.Wire(self.route, self.provider, usage=self.usage, proxied=self.proxied, named=self.named)
        text, model, usage = wire.ask(parts, vision.SYSTEM, 600, vision.EFFORT)
        self.route = wire.route
        with self.lock:
            self.calls["describe"] += 1
        self._log("describe", frames, {"q": question}, {"text": text, "model": model})
        return text.strip()

    # -- the two ways of asking ------------------------------------------------------------------------------

    def _jev(self, frames, questions, facts):
        """Jev's answers: the frames go as image assets in the state (TypeSafe's System One), with the facts."""
        from typesafe_sdk import Image

        images = [Image(f["jpeg"]) for f in frames]
        state = {"frames": [{"game_time": f["t"], "picture": image} for f, image in zip(frames, images)],
                 "note": "frames of a video game in order, the last the latest; the player character is the one "
                         "the camera follows"}
        if facts:
            state["situation"] = facts
        # (TypeSafe takes images only with this header: "Images are experimental", its 400 says without it)
        r = self.jev.system_one(state=state, questions=questions,
                                extra_headers={"TypeSafe-Danger-Zone-Images": "true"})
        out = {}
        for k, a in r.nouls.items():
            out[k] = float(a.noul)
        for k, a in r.choices.items():
            probs = getattr(a, "probabilities", None)
            out[k] = {str(c): float(p) for c, p in dict(probs).items()} if probs else {str(a.choice): 1.0}
        for k, a in r.scores.items():
            out[k] = float(a.score)
        with self.lock:
            self.calls["jev"] += 1
        return out

    def _model(self, frames, questions, facts):
        """The vision model's answers, asked as JSON."""
        if self.route is None:
            raise RuntimeError("no vision model to look with")
        from . import vision

        asked = []
        for k, q in questions.items():
            kind = getattr(q, "type", "noul")
            text = str(getattr(q, "instructions", None) or "")
            if kind == "choice":
                labels = "; ".join(f"{label}: {desc}" for label, desc in (q.criteria or {}).items())
                asked.append(f'"{k}" (choice; labels: {labels}): {text}')
            elif kind == "score":
                asked.append(f'"{k}" (score 0..{max(0, len(q.criteria or []) - 1)}): {text}')
            else:
                asked.append(f'"{k}" (yes/no): {text}')
        parts = vision.frame_parts(frames)
        if facts:
            parts.append(("text", "What the game's numbers say now: " + json.dumps(facts, default=str)[:1500]))
        parts.append(("text", "Questions:\n" + "\n".join(asked)))
        wire = vision.Wire(self.route, self.provider, usage=self.usage, proxied=self.proxied, named=self.named)
        text, model, usage = wire.ask(parts, SYSTEM, 800, vision.EFFORT)
        self.route = wire.route
        with self.lock:
            self.calls["model"] += 1
        return parse_answers(text, questions)

    def _log(self, kind, frames, questions, answers):
        if not self.log:
            return
        import os

        line = {"wall": round(time.time(), 3), "kind": kind,
                "frames": [{"t": f["t"], "wall": round(f["wall"], 3)} for f in frames],
                "questions": jsonable_questions(questions) if kind == "look" else questions, "answers": answers}
        try:
            with self.lock, open(os.path.join(self.log, "watch.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps(line, default=str) + "\n")
        except OSError:
            pass


def parse_answers(text, questions):
    """The answers in a model's JSON text, coerced to each question's kind; a question the text doesn't answer gets
    0.5 (a Noul), an even split (a Choice) or None (a Score). The "note" is kept."""
    found = {}
    m = re.search(r"\{.*\}", text or "", re.S)
    if m:
        try:
            found = json.loads(m.group(0))
        except ValueError:
            found = {}
    if not isinstance(found, dict):
        found = {}
    out = {}
    for k, q in questions.items():
        kind = getattr(q, "type", "noul")
        v = found.get(k)
        if kind == "choice":
            labels = list((q.criteria or {}).keys())
            if isinstance(v, dict):
                probs = {label: _prob(v.get(label, 0.0)) for label in labels}
            elif isinstance(v, str) and v in labels:
                probs = {label: (1.0 if label == v else 0.0) for label in labels}
            else:
                probs = {label: 1.0 / max(1, len(labels)) for label in labels}
            total = sum(probs.values()) or 1.0
            out[k] = {label: round(p / total, 3) for label, p in probs.items()}
        elif kind == "score":
            out[k] = float(v) if isinstance(v, (int, float)) else None
        else:
            out[k] = _prob(v) if v is not None else 0.5
    note = found.get("note")
    if isinstance(note, str) and note.strip():
        out["note"] = note.strip()[:300]
    return out


def _prob(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        if isinstance(v, str):
            return 1.0 if v.strip().lower() in ("yes", "true") else 0.0
        return 0.5
    return max(0.0, min(1.0, x))


class RemoteLooker:
    """The Looker in the sandbox's child: the parent looks (autopilot/sandbox.py, "watch" messages)."""

    def __init__(self, link, available=None):
        self.link = link
        self._available = available  # (why no look can be made, as the parent said at the start; None: it can)

    def available(self):
        return self._available

    def can_describe(self):
        return self._available is None

    def _ask(self, msg, timeout):
        r = self.link._ask({"type": "watch", **msg}, timeout=timeout)
        if not r.get("ok"):
            raise RuntimeError(r.get("error") or "the look failed")
        return r.get("result")

    def look(self, wall0, wall1, questions, facts=None, rate=RATE, limit=MAX_FRAMES):
        return self._ask({"op": "look", "wall0": wall0, "wall1": wall1, "questions": jsonable_questions(questions),
                          "facts": facts, "rate": rate, "limit": limit}, timeout=TIMEOUT + 10)

    def describe(self, wall0, wall1, question=REVIEW_QUESTION, rate=RATE, limit=MAX_FRAMES):
        return self._ask({"op": "describe", "wall0": wall0, "wall1": wall1, "question": question, "rate": rate,
                          "limit": limit}, timeout=TIMEOUT + 10)


def serve(looker, msg):
    """A RemoteLooker's message answered by the parent's looker: the result (the parent's sandbox calls this)."""
    op = msg.get("op")
    if op == "look":
        return looker.look(float(msg["wall0"]), float(msg["wall1"]), questions_from(msg.get("questions")),
                           facts=msg.get("facts"), rate=float(msg.get("rate") or RATE),
                           limit=int(msg.get("limit") or MAX_FRAMES))
    if op == "describe":
        return looker.describe(float(msg["wall0"]), float(msg["wall1"]), question=msg.get("question") or
                               REVIEW_QUESTION, rate=float(msg.get("rate") or RATE),
                               limit=int(msg.get("limit") or MAX_FRAMES))
    raise ValueError(f"unknown watch op {op}")


class Watcher:
    """The runtime's looks while an episode plays (see the module's docstring). looker: a Looker or RemoteLooker;
    harness: the episode's (its watch_questions and on_watch); game: the kit's Game (its `watch` gets the latest
    answers); trace: the runtime's Trace (watch, review events); every: seconds of game time between periodic looks
    (None: the harness's watch_every, else EVERY; 0: only after events)."""

    def __init__(self, looker, harness, game, trace, every=None):
        self.looker = looker
        self.h = harness
        self.game = game
        self.trace = trace
        every = getattr(harness, "watch_every", None) if every is None else every
        self.every = float(EVERY if every is None else every)
        self.obs = None
        self.facts = None
        self.last_t = None  # game time of the last look
        self.last_wall = 0.0
        self.due = []  # (trigger, info, not before wall time) waiting to be looked at
        self.deaths = []  # {"t", "wall", "how", "answers"?}: the episode's deaths, for the review
        self.looks = 0
        self.failures = 0
        self.busy = False  # a look in flight
        self.cond = threading.Condition()
        self.stopped = False
        self.thread = threading.Thread(target=self._loop, name="watcher", daemon=True)
        self.thread.start()

    # -- from the runtime (its threads) ------------------------------------------------------------------------

    def observed(self, obs, facts, t):
        """Every observation: the latest facts (for Jev's state), and a periodic look when its time has come."""
        self.obs, self.facts = obs, facts
        if self.every > 0 and (self.last_t is None or t - self.last_t >= self.every):
            with self.cond:
                if not any(d[0] == "time" for d in self.due):
                    self.due.append(("time", {}, time.time()))
                    self.cond.notify()

    def event(self, kind, **info):
        """Something happened worth a look (a death, a failed option, a stall): looked at AFTER_EVENT later, so the
        frames show what came of it. A death is kept for the review."""
        wall = time.time()
        if kind == "death":
            self.deaths.append({"t": info.get("t"), "wall": wall, "how": info.get("how") or "", "answers": None})
        with self.cond:
            if not any(d[0] == kind for d in self.due):
                self.due.append((kind, info, wall + AFTER_EVENT))
                self.cond.notify()

    def drain(self, timeout):
        """Wait up to timeout seconds for the looks due (an event's just before the episode ended) to be made."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self.cond:
                if not self.due and not self.busy:
                    return
                self.cond.wait(0.1)

    def stop(self):
        """End the looks (one in flight ends by itself)."""
        with self.cond:
            self.stopped = True
            self.cond.notify_all()

    def review(self, wait=REVIEW_WAIT):
        """The episode's deaths reviewed, the latest REVIEW_MAX: [{"t", "how", "cause", "p", "text"}], each with the
        cause the look after it gave (if one was made) and the vision model's sentence about the seconds before it.
        Waits up to `wait` seconds for the sentences; one not back in time says so. Each goes to the trace too."""
        picked = self.deaths[-REVIEW_MAX:]
        if not picked:
            return []
        results = [None] * len(picked)

        def one(i, death):
            try:
                results[i] = self.looker.describe(death["wall"] - WINDOW, death["wall"] + 0.5)
            except Exception as e:  # noqa: BLE001 - the review says so
                results[i] = f"(the picture couldn't be described: {type(e).__name__}: {str(e)[:120]})"

        threads = []
        if self.looker.can_describe():
            for i, death in enumerate(picked):
                th = threading.Thread(target=one, args=(i, death), daemon=True)
                th.start()
                threads.append(th)
        deadline = time.time() + wait
        for th in threads:
            th.join(max(0.0, deadline - time.time()))
        out = []
        for i, death in enumerate(picked):
            cause, p = _cause(death.get("answers"))
            text = results[i]
            if text is None and threads:
                text = "(the picture's description hadn't come back in time)"
            entry = {"t": death["t"], "how": death["how"], "cause": cause, "p": p, "text": text}
            out.append(entry)
            self.trace("review", **entry)
        return out

    # -- the looks (the watcher's thread) ------------------------------------------------------------------------

    def _loop(self):
        while True:
            with self.cond:
                while not self.stopped and not self._ready():
                    self.cond.wait(0.2)
                if self.stopped:
                    return
                item = self._take()
                self.busy = item is not None
            if item is None:
                continue
            try:
                self._look(*item)
            finally:
                with self.cond:
                    self.busy = False
                    self.cond.notify_all()

    def _ready(self):
        now = time.time()
        return any(d[2] <= now for d in self.due) and now - self.last_wall >= MIN_GAP

    def _take(self):
        now = time.time()
        for i, d in enumerate(self.due):
            if d[2] <= now:
                # (an event's look first: a periodic one waiting can go after it)
                best = next((e for e in self.due if e[2] <= now and e[0] != "time"), d)
                self.due.remove(best)
                return best
        return None

    def _questions(self):
        asked = dict(DEFAULT)
        try:
            extra = self.h.watch_questions(self.obs) if self.obs is not None else {}
        except Exception as e:  # noqa: BLE001 - a harness bug: the defaults alone
            self.trace("error", t=self.game.t, where="watch_questions", error=f"{type(e).__name__}: {e}"[:300])
            extra = {}
        for k, q in (extra or {}).items():
            if q is None or q is False:
                asked.pop(k, None)
            elif q is True and k in OPTIONAL:
                asked[k] = OPTIONAL[k]
            elif isinstance(q, str):
                asked[k] = Noul(instructions=q)
            elif hasattr(q, "model_dump"):
                asked[k] = q
        return asked

    def _look(self, trigger, info, _at):
        wall1 = time.time()
        wall0 = wall1 - WINDOW
        rate = PERIODIC_RATE if trigger == "time" else RATE
        questions = self._questions()
        t = self.game.t
        try:
            answers = self.looker.look(wall0, wall1, questions, facts=self.facts, rate=rate)
        except Exception as e:  # noqa: BLE001 - told once in the trace, then the watcher goes on
            self.failures += 1
            if self.failures <= 2:
                self.trace("watch_error", t=t, trigger=trigger, error=f"{type(e).__name__}: {e}"[:300])
            self.last_wall, self.last_t = time.time(), t
            return
        self.looks += 1
        self.last_wall, self.last_t = time.time(), t
        if answers.get("jev_error") and not getattr(self, "jev_told", False):
            self.jev_told = True  # (once: why the vision model answers rather than Jev)
            self.trace("watch_jev_off", t=t, error=answers["jev_error"])
        shown = {k: v for k, v in answers.items() if k not in ("frames", "by", "latency", "jev_error")}
        self.game.watch = {**shown, "t": t, "trigger": trigger}
        if trigger == "death" and self.deaths:
            self.deaths[-1]["answers"] = shown
        try:
            self.h.on_watch(shown, self.obs)
        except Exception as e:  # noqa: BLE001
            self.trace("error", t=t, where="on_watch", error=f"{type(e).__name__}: {e}"[:300])
        self.trace("watch", t=t, trigger=trigger, info=info, answers=shown, frames=answers.get("frames"),
                   by=answers.get("by"), latency=answers.get("latency"))


def _cause(answers):
    """(the likeliest cause label, its probability) from a look's answers, or (None, None)."""
    cause = (answers or {}).get("cause")
    if not isinstance(cause, dict) or not cause:
        return None, None
    label = max(cause, key=cause.get)
    return label, round(float(cause[label]), 2)


def picture_summary(events):
    """The "What the picture showed" section of a trace's summary (runtime.summarize): the deaths with each one's
    review and the look's cause, how often the player looked stuck, and the looks made. [] when nothing was looked
    at."""
    looks = [e for e in events if e.get("kind") == "watch"]
    reviews = [e for e in events if e.get("kind") == "review"]
    deaths = [e for e in events if e.get("kind") == "death"]
    errors = [e for e in events if e.get("kind") == "watch_error"]
    if not looks and not reviews and not errors:
        return []
    t_first = next((e.get("t") for e in events if e.get("t") is not None), 0) or 0
    out = ["\nWhat the picture showed (a model that sees the last 5 s of frames; probabilities 0-1):"]
    if deaths:
        causes = {}
        for e in looks:
            if e.get("trigger") == "death":
                label, p = _cause(e.get("answers"))
                if label:
                    causes[label] = causes.get(label, 0) + 1
        said = ", ".join(f"{k} x{n}" for k, n in sorted(causes.items(), key=lambda kv: -kv[1]))
        out.append(f"  Deaths: {len(deaths)}" + (f". The look after each said: {said}." if said else "."))
    for r in reviews:
        t = (r.get("t") or 0) - t_first
        cause = f" [{r['cause']} {r['p']}]" if r.get("cause") else ""
        text = r.get("text") or "(no description)"
        out.append(f"  Death at t={t:.1f}s{cause}: {text}")
    stuck = [e for e in looks if isinstance(e.get("answers", {}).get("blocked"), (int, float))
             and e["answers"]["blocked"] >= 0.7]
    menu = [e for e in looks if isinstance(e.get("answers", {}).get("menu"), (int, float))
            and e["answers"]["menu"] >= 0.7]
    by = {}
    for e in looks:
        by[e.get("by")] = by.get(e.get("by"), 0) + 1
    line = f"  Looks: {len(looks)}" + (f" ({', '.join(f'{n} by {k}' for k, n in by.items())})" if by else "")
    if stuck:
        when = ", ".join(f"t={(e.get('t') or 0) - t_first:.0f}" for e in stuck[:8])
        line += f"; the player looked stuck in {len(stuck)} of them ({when}{'...' if len(stuck) > 8 else ''})"
    if menu:
        line += f"; a menu or screen covered the view in {len(menu)}"
    out.append(line + ".")
    notes = [(e.get("t"), e["answers"]["note"]) for e in looks
             if e.get("trigger") != "time" and isinstance(e.get("answers", {}).get("note"), str)]
    for t, note in notes[-4:]:
        out.append(f"  t={(t or 0) - t_first:.1f}: {note}")
    extra = {}
    for e in looks:
        for k, v in (e.get("answers") or {}).items():
            if k not in DEFAULT and k != "note" and isinstance(v, (int, float)):
                extra.setdefault(k, []).append(v)
    for k, vals in extra.items():
        out.append(f"  {k}: yes in {sum(1 for v in vals if v >= 0.7)} of {len(vals)} looks")
    if errors:
        out.append(f"  (looks that failed: {len(errors)}, e.g. {errors[0].get('error')})")
    off = next((e for e in events if e.get("kind") == "watch_jev_off"), None)
    if off:
        out.append("  (the decision model couldn't answer about the frames, so a vision model did: "
                   f"{off.get('error')})")
    return out
