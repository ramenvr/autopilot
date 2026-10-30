"""A run's data sent to the developers of Autopilot, when the user shares it (docs/run-data.md).

After a run, each shared run of the project not sent yet (one with telemetry/run.json, which only a shared run has)
is cleaned and laid out as autopilot/dataset.py says: secrets, the project's and the user's home folder, other absolute
paths, user and computer names and email addresses taken out (autopilot/redact.py), one folder per run,
runs/<run id>-<uid>/. It goes to storage file by file, through Aura's endpoint (POST <url>, ops list / sign): the
brain sends the files itself over short-lived signed URLs and never holds the storage's credentials. A run's
run.json goes last, once every other file of it is stored: a run without one hasn't arrived whole. What is stored
already (same path, same MD5) isn't sent again, so a run whose upload was cut short is finished by the next one.
Nothing is ever removed from there by Autopilot.

A send that fails for a reason that passes (the network, the endpoint or the storage busy or down: autopilot/retry.py)
is tried again within the run, after waits that double from RETRY_SECONDS (RETRIES times), each going on from what is
stored already; one refused (consent, a bad token, the day's quota: HTTP 4xx) is left for the next run.

Who it is stored for:
- a run through Aura (autopilot/aura.py): with the run's token, for the user's team; only when Aura answered the run
with
  a `training` URL, which it does only when the user's Aura account allows sharing (Aura's Privacy setting).
- a run with the user's own keys: with this machine's install token, got once from the endpoint's `register` and
  kept in Autopilot/install.json in the user settings folder (an id with no account behind it).
Each run goes only the way it ran (run.json's through_aura): a run through Aura is never sent with the install token,
nor one with the user's own keys for an Aura team.

Never sent: runs whose data wasn't shared, the codex (whole codebases are never stored: a run names the codex it saw
by its id, and its calls to the agent's model hold the parts the agent read), the project's notes.md and lib.py (each
run has them as it found and left them), project.json (where the project is on this machine), Claude Code's own
folder, bytecode caches, and files over the endpoint's limit. A failure is logged and left for the next run: shared
data is never what a run waits on.
"""

import concurrent.futures as futures
import json
import os
import shutil
import tempfile
import threading

import requests

from . import bridge, dataset, retry

DEFAULT_URL = "https://app.tryaura.dev/api/autopilot/training"  # AUTOPILOT_TRAINING_URL names another
# {"schema": 2, "runs": {run id: its prefix in storage}, "codex": [paths stored] (by an Autopilot that sent the codex)}
SYNC_FILE = ".training-sync.json"
INSTALL_FILE = "install.json"
MAX_FILE_BYTES = 2 * 1024 ** 3
BATCH = 100
WORKERS = 4
TIMEOUT = 120.0
RETRIES = retry.TRIES  # times a send that failed for a passing reason is tried again within the run
RETRY_SECONDS = 5.0  # the first wait before trying again; each next one twice as long (5, 10, 20 s): in the background
RETRY_STATUSES = retry.STATUSES
TYPES = {".json": "application/json", ".jsonl": "application/x-ndjson", ".gz": "application/gzip",
         ".mp4": "video/mp4", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".txt": "text/plain",
         ".log": "text/plain", ".md": "text/markdown"}
_lock = threading.Lock()  # one upload at a time


class UploadError(Exception):
    """A send that failed. passing: whether trying again soon may work (an answer in RETRY_STATUSES)."""

    def __init__(self, message, passing=False):
        super().__init__(message)
        self.passing = passing


def endpoint():
    """AUTOPILOT_TRAINING_URL, else the endpoint of the Aura server AUTOPILOT_AURA_SERVER names, else Aura's
    own."""
    server = (os.environ.get("AUTOPILOT_AURA_SERVER") or "").strip().rstrip("/")
    return (os.environ.get("AUTOPILOT_TRAINING_URL") or "").strip() or (f"{server}/api/autopilot/training" if server
                                                                     else DEFAULT_URL)


def content_type(path):
    return TYPES.get(os.path.splitext(path)[1].lower(), "application/octet-stream")


# -- who it is stored for --------------------------------------------------------------------------------------------

def install_token(http=requests, url=None):
    """This machine's install token, registered with the endpoint the first time. Raises UploadError."""
    path = bridge.brain_file(INSTALL_FILE)
    try:
        with open(path, encoding="utf-8") as f:
            saved = json.load(f)
        if saved.get("token") and saved.get("url") == (url or endpoint()):
            return saved["token"]
    except (OSError, ValueError, AttributeError):
        pass
    try:
        r = http.post(url or endpoint(), json={"op": "register"}, timeout=TIMEOUT)
    except requests.RequestException as e:
        raise UploadError(f"couldn't register this machine: {type(e).__name__}: {e}", passing=True) from None
    if r.status_code != 200:
        raise UploadError(f"couldn't register this machine: HTTP {r.status_code}: {r.text[:200]}",
                          passing=r.status_code in RETRY_STATUSES)
    answer = r.json()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"install_id": answer.get("install_id"), "token": answer["token"], "url": url or endpoint()}, f)
    os.replace(tmp, path)
    return answer["token"]


class Upload:
    """One project's data to the endpoint. token: a run's token (Aura) or this machine's install token;
    project: the project's memory folder name (an install token's data is stored per project by it)."""

    def __init__(self, url, token, project, http=None):
        self.url = url
        self.headers = {"Authorization": f"Bearer {token}"}
        self.project = project
        self.http = http or requests.Session()

    def _call(self, body):
        r = self.http.post(self.url, json={**body, "project": self.project}, headers=self.headers, timeout=TIMEOUT)
        if r.status_code != 200:
            raise UploadError(f"{body.get('op')}: HTTP {r.status_code}: {r.text[:300]}",
                              passing=r.status_code in RETRY_STATUSES)
        return r.json()

    def listing(self):
        found = self._call({"op": "list"})
        return {f["path"]: f.get("md5") for f in found.get("files", [])}

    def _put(self, signed, fulls):
        def send(s):
            with open(fulls[s["path"]], "rb") as f:
                r = requests.put(s["url"], data=f, headers=s.get("headers") or {}, timeout=TIMEOUT * 5)
            if r.status_code in (200, 201):
                return True
            return (f"{s['path']}: HTTP {r.status_code}: {r.text[:200]}", r.status_code in RETRY_STATUSES)

        with futures.ThreadPoolExecutor(WORKERS) as pool:
            failed = [done for done in pool.map(send, signed) if done is not True]
        if failed:
            raise UploadError("; ".join(f for f, _ in failed[:3]), passing=all(p for _, p in failed))

    def put_files(self, items, stored):
        """items: [(path in storage, local file, md5, size)]; those not stored as they are here are sent (`stored`,
        {path: md5}, is kept up to date). Returns how many were sent."""
        todo = [(p, full, m, size) for p, full, m, size in items
                if 0 < size <= MAX_FILE_BYTES and stored.get(p) != m]
        for i in range(0, len(todo), BATCH):
            batch = todo[i:i + BATCH]
            put = [{"path": p, "md5": m, "size": size, "content_type": content_type(p)} for p, _, m, size in batch]
            signed = self._call({"op": "sign", "put": put}).get("put", [])
            if sorted(s["path"] for s in signed) != sorted(p for p, _, _, _ in batch):
                raise UploadError("the endpoint signed other files than asked")
            self._put(signed, {p: full for p, full, _, _ in batch})
            for p, _, m, _ in batch:
                stored[p] = m
        return len(todo)

    def send(self, folder, through_aura):
        """Send the project's shared runs that went this way (through Aura or not) and aren't sent yet, each cleaned
        (autopilot/dataset.py). Returns how many files were sent."""
        state = _read(folder)
        pending = [(run_dir, m) for run_dir, m in unsent(folder) if bool(m.get("through_aura")) == bool(through_aura)]
        if not pending:
            return 0
        stored = self.listing()
        sent = 0
        work = tempfile.mkdtemp(prefix="autopilot-upload-")
        try:
            for run_dir, m in pending:
                prefix = dataset.run_prefix(run_dir, m)
                out = os.path.join(work, os.path.basename(run_dir))
                manifest = dataset.export_run(run_dir, out)
                sent += self.put_files([(prefix + f["path"], f["source"], f["md5"], f["bytes"])
                                        for f in manifest["files"]], stored)
                last = os.path.join(out, "run.json")  # (last: a run with its run.json has arrived whole)
                sent += self.put_files([(prefix + "run.json", last, dataset.md5_of(last), os.path.getsize(last))],
                                       stored)
                state["runs"][os.path.basename(run_dir)] = prefix
                _write(folder, state)
                shutil.rmtree(out, ignore_errors=True)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        return sent


def unsent(folder):
    """A project folder's shared runs not sent yet (either way: through Aura or not), oldest first: [(run dir,
    manifest)]."""
    state = _read(folder)
    return [(run_dir, m) for run_dir, m in dataset.shared_runs(folder)
            if state["runs"].get(os.path.basename(run_dir)) != dataset.run_prefix(run_dir, m)]


def _read(folder):
    """What was sent from this project folder: {"runs": {run id: its prefix}, "codex": [paths]}. An older file (it
    listed the files of the layout before this one) counts as nothing sent: those runs go again, cleaned."""
    try:
        with open(os.path.join(folder, SYNC_FILE), encoding="utf-8") as f:
            saved = json.load(f)
        if saved.get("schema") == dataset.SCHEMA:
            return {"runs": dict(saved.get("runs") or {}), "codex": list(saved.get("codex") or [])}
    except (OSError, ValueError, AttributeError):
        pass
    return {"runs": {}, "codex": []}


def _write(folder, state):
    tmp = os.path.join(folder, SYNC_FILE + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"schema": dataset.SCHEMA, **state}, f)
    os.replace(tmp, os.path.join(folder, SYNC_FILE))


def upload(folder, project, aura_run=None, http=None, sleep=None):
    """Send a project's data (its memory folder's shared runs). aura_run: the run through Aura it came
    from (its token and Aura's `training` URL), or None (this machine's install token). A send that fails for a
    passing reason is tried again (RETRIES times, the waits doubling), each time going on from what is stored already.
    Returns how many files were sent; never raises."""
    with _lock:
        if aura_run is not None and not getattr(aura_run, "training", None):
            return 0  # (the user's Aura account doesn't allow sharing)

        def attempt():
            if aura_run is not None:
                job = Upload(aura_run.training, aura_run.run_token, project, http)
            else:
                job = Upload(endpoint(), install_token(http or requests), project, http)
            return job.send(folder, through_aura=aura_run is not None)

        try:
            sent = retry.call(attempt, "sending the run's data", RETRY_SECONDS, sleep=sleep)
        except Exception as e:  # noqa: BLE001 - left for the next run
            print(f"note: the run's data wasn't sent (the next run sends it): {type(e).__name__}: {e}")
            return 0
        print(f"Shared run data: {sent} files sent for {project}")
        return sent


def upload_later(folder, project, aura_run=None):
    """upload() in a thread of its own (the plugin has its result already); returns the thread."""
    t = threading.Thread(target=upload, args=(folder, project, aura_run), name="training-upload", daemon=False)
    t.start()
    return t
