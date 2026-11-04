"""Publish Autopilot's downloads as a GitHub Release, built from main as merged (origin/main), on production Aura
(autopilot.aura's default server). Run it after a merge to main that should reach users, on a Windows computer and on
a Mac, in either order:

    uv run --no-project --python 3.12 tools/release.py [--unreal 5.4,5.5,5.6,5.7,5.8] [--work-dir DIR] [--publish]
                                                       [--no-upm] [--respin N]

A release has two halves, built where their engines are: on Windows the plugin for Unreal 5.4-5.8 (Win64), Unity and
Godot; on a Mac the plugin for Unreal 5.4-5.8 (Mac). Each run builds its half and uploads it to the release's draft,
and the run that completes the draft publishes it as Latest. The repository has immutable releases on (a published
release's files and tag can't change), so nothing can be added after that: the draft is where the halves meet. A run
whose half the newest draft is missing builds that draft's commit (when main has moved on since the other half was
built); otherwise it builds origin/main's newest commit, and a draft for an older commit is deleted (it would never be
complete). --publish publishes the draft with what it has, e.g. when there is no Mac to build the other half on.

Every release's downloads have the same names, Autopilot-<engine>.zip (.dmg for Unreal on a Mac; package_release.py
--unversioned), so a link like
    https://github.com/ramenvr/autopilot/releases/latest/download/Autopilot-Unreal5.8-Win64.zip
always downloads the newest one; the release's tag (v<version>-<commit>) and title carry its version. A commit
released already isn't redone (but with --respin). The build happens in a worktree of that commit of its own, so what
this checkout has checked out doesn't matter.

--unreal    the Unreal versions to build for (installed under C:/Program Files/Epic Games, or on a Mac under
            /Users/Shared/Epic Games)
--work-dir  where the build happens, and where its downloads stay after (default C:/Temp on Windows: Unreal's
            BuildPlugin fails on long paths; the temporary folder on a Mac)
--no-upm    on Windows: the Unity package without upm's signature (package_release.py --no-upm), for when there is no
            Unity service account to sign it with; Unity 6.3 and later show it as unsigned. Everything else is still
            signed, and the release's notes say the Unity package isn't (UNSIGNED_UNITY).
--respin N  release a commit released already again, as its Nth release (tag v<version>-<commit>.<N>, from 2), e.g.
            to replace a broken download: a published release's tag can't be used again, even once it's deleted. The
            other half's run finishes the respin's draft without it.

Built by hand, not by CI, on purpose: a self-hosted runner with the engines would run whatever code a branch's or a
fork's workflow sends it. Needs Git, uv, the Unreal engines (and on a Mac the Xcodes they take, see package_release.py),
and gh signed in as a repository admin: a tag ruleset lets only admins create tags, and publishing a release creates
its tag. The two halves' runs may overlap: a run looks for the draft again when its build ends, and if both made one
the later draft's run moves its half into the earlier draft, so the halves still meet in one. A run waits for the other
run's upload to finish before adding to its draft, and the run that adds its half to the other's draft is the one that
publishes.

The downloads are signed as they're built (package_release.py --sign, which says what each needs): on Windows the
Unity package by Unity's upm (UNITY_ORGANIZATION and its signing service account's UPM_SERVICE_ACCOUNT_KEY_ID and
UPM_SERVICE_ACCOUNT_KEY_SECRET) and the Unreal plugins' binaries with DigiCert KeyLocker's smctl (the TribeVR Inc
keypair); on a Mac the Unreal plugins' binaries and their disk images with the Developer ID Application certificate in
the keychain, the disk images notarized with notarytool's stored credentials (autopilot-notary) and their tickets
stapled, and on Windows every download's install.ps1
with smctl. A run without them stops before building (but the Unity package's, with --no-upm): a published release
can't be signed after.
"""

import argparse
import itertools
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GITHUB = "ramenvr/autopilot"
HALVES = {"Win64": "the Unreal plugin for Windows, Unity and Godot", "Mac": "the Unreal plugin for Mac"}
TAG = re.compile(r"^v.+-[0-9a-f]{7}(\.[0-9]+)?$")  # (this script's tags: v<version>-<commit>, .<N> for a respin)
# What the release's notes say is signed: all of it, or (--no-upm) all but the Unity package
SIGNED = ("The Unity package is signed, and the Unreal plugins' binaries are signed by TribeVR Inc (the macOS ones "
          "notarized by Apple).")
UNSIGNED_UNITY = ("The Unreal plugins' binaries are signed by TribeVR Inc (the macOS ones notarized by Apple), as is "
                  "the installer the Windows downloads carry (install.ps1); the Unity package isn't signed (Unity 6.3 "
                  "and later show it as unsigned).")
UPLOAD_WAIT = 15 * 60  # (seconds a run waits for the other run's upload: it took under a minute so far)


def run(*args):
    """Runs a program, its output shown as it goes; a failure stops the release."""
    if subprocess.run(args).returncode != 0:
        sys.exit(f"{' '.join(args)} failed: see the lines above.")


def read(*args):
    """A program's output; a failure stops the release."""
    done = subprocess.run(args, capture_output=True, text=True, encoding="utf-8")
    if done.returncode != 0:
        sys.exit(f"{' '.join(args)} failed:\n{done.stderr.strip()}")
    return done.stdout


MAC_DOWNLOADS = ("-Mac.dmg", "-Mac.zip")  # (the Mac Unreal downloads: disk images, zips in releases made before)


def half(name):
    """Which half of a release a file of it belongs to: Mac for the Mac Unreal downloads, Win64 for the zips (README.md
    and SHA256SUMS.txt are both halves')."""
    if name.endswith(MAC_DOWNLOADS):
        return "Mac"
    return "Win64" if name.endswith(".zip") else None


def complete(names):
    """Whether a release's files are both halves: Unreal for Windows and for Mac, Unity and Godot."""
    return (any(n.endswith("-Win64.zip") for n in names) and any(n.endswith(MAC_DOWNLOADS) for n in names)
            and "Autopilot-Unity.zip" in names and "Autopilot-Godot.zip" in names)


def contains(commit, older):
    """Whether a commit's history has another commit in it."""
    return subprocess.run(["git", "-C", HERE, "merge-base", "--is-ancestor", older, commit]).returncode == 0


def list_releases():
    """The repository's releases, drafts too."""
    return json.loads(read("gh", "api", f"repos/{GITHUB}/releases?per_page=100"))


def delete_draft(draft):
    """Deletes a draft by its id: two drafts can have the same tag, and gh finds a draft by its tag."""
    run("gh", "api", "-X", "DELETE", f"repos/{GITHUB}/releases/{draft['id']}")


def leftovers(releases):
    """This script's drafts whose tag is published: the half of an overlap that never got moved. They'd pass for a
    draft waiting for the other half, and that run would stop at "released already" rather than build main."""
    published = {r["tag_name"] for r in releases if not r["draft"]}
    return [r for r in releases if r["draft"] and TAG.match(r["tag_name"]) and r["tag_name"] in published]


def draft_of(releases, tag, platform):
    """The draft a tag's halves meet in, and the extra drafts holding this platform's half (to delete): two runs that
    overlap each make a draft, because GitHub lets drafts share a tag, and the earlier one (lowest id) is the one to
    keep: neither run has to know what the other does, and the later run always sees the earlier run's draft, which
    was there before its own upload began. (None and [] when the tag has no draft.)"""
    drafts = sorted((r for r in releases if r["draft"] and r["tag_name"] == tag), key=lambda r: r["id"])
    extra = [r for r in drafts[1:] if any(half(a["name"]) == platform for a in r["assets"])]
    return (drafts[0] if drafts else None), extra


def uploaded(draft):
    """Whether the run filling a draft is done: gh starts a run's files in order and SHA256SUMS.txt is the last of them,
    and a file being uploaded isn't in the state "uploaded" yet."""
    assets = draft["assets"]
    return (any(a["name"] == "SHA256SUMS.txt" for a in assets)
            and all(a.get("state", "uploaded") == "uploaded" for a in assets))


def wait_for_upload(draft):
    """Waits for the other run to finish uploading to its draft: adding to the draft before that would lose its lines
    of SHA256SUMS.txt."""
    deadline = time.monotonic() + UPLOAD_WAIT
    while not uploaded(draft):
        if time.monotonic() > deadline:
            sys.exit(f"The draft {draft['tag_name']} isn't fully uploaded after {UPLOAD_WAIT // 60} minutes: if its "
                     "run failed, delete the draft, then run both halves again.")
        print(f"Waiting for the other half's upload to the draft {draft['tag_name']} to finish...", flush=True)
        time.sleep(10)
        draft = json.loads(read("gh", "api", f"repos/{GITHUB}/releases/{draft['id']}"))
    return draft


def waiting(releases, platform):
    """The newest of this script's drafts that is missing this platform's half (the other computer built the rest),
    else None."""
    # (only the newest: an older draft is deleted when a newer one is made)
    newest = max((r for r in releases if r["draft"] and TAG.match(r["tag_name"])), key=lambda r: r["created_at"],
                 default=None)
    if newest and not any(half(a["name"]) == platform for a in newest["assets"]):
        return newest
    return None


def merged_sums(old, new):
    """SHA256SUMS.txt with both halves' lines: the draft's (old), with this run's files (new) in place of theirs."""
    lines = {}
    for text in (old, new):
        for line in text.splitlines():
            if "  " in line:
                lines[line.split("  ", 1)[1]] = line
    return "".join(f"{line}\n" for line in lines.values())


def unsigned_unity(notes):
    """A draft's notes with SIGNED replaced by UNSIGNED_UNITY (--no-upm, when the other half wrote them); None when
    they don't say SIGNED."""
    return notes.replace(SIGNED, UNSIGNED_UNITY) if SIGNED in notes else None


def main(argv=None):
    ap = argparse.ArgumentParser(description="Publish Autopilot's downloads as a GitHub Release (see the top).")
    ap.add_argument("--unreal", default="5.4,5.5,5.6,5.7,5.8", help="the Unreal versions to build for")
    ap.add_argument("--work-dir", default="C:/Temp" if sys.platform == "win32" else tempfile.gettempdir(),
                    help="where the build happens, and where its downloads stay after")
    ap.add_argument("--publish", action="store_true", help="publish the draft with what it has (e.g. no Mac half)")
    ap.add_argument("--no-upm", action="store_true",
                    help="on Windows: the Unity package without upm's signature (no Unity service account)")
    ap.add_argument("--respin", type=int, metavar="N",
                    help="release a commit released already again, as its Nth release (from 2)")
    args = ap.parse_args(argv)
    if args.respin is not None and args.respin < 2:
        sys.exit("--respin N is a commit's Nth release, from 2: its first is the one without --respin.")
    platform = {"win32": "Win64", "darwin": "Mac"}.get(sys.platform)
    if platform is None:
        sys.exit("Releases are built on Windows and on a Mac.")
    if args.no_upm and platform != "Win64":
        sys.exit("--no-upm is for the Windows half: the Mac half has no Unity package.")
    for tool in ("git", "gh", "uv"):
        if shutil.which(tool) is None:
            sys.exit(f"{tool} isn't on PATH: install it, then run this again.")
    if subprocess.run(["gh", "auth", "status"], capture_output=True).returncode != 0:
        sys.exit("gh isn't signed in: run gh auth login, then run this again.")
    if read("gh", "api", f"repos/{GITHUB}", "--jq", ".permissions.admin").strip() != "true":
        sys.exit(f"Only {GITHUB}'s admins can publish a release, and gh isn't signed in as one.")
    # (origin/main is what's built and published to GITHUB: it has to be that repository's)
    origin = read("git", "-C", HERE, "remote", "get-url", "origin").strip()
    if not re.search(rf"github\.com[:/]{GITHUB}(\.git)?/?$", origin):
        sys.exit(f"This checkout's origin is {origin}, not {GITHUB}.")
    work = os.path.realpath(args.work_dir)  # (Unreal's build accelerator loses files under a symlink: macOS's /var)

    run("git", "-C", HERE, "fetch", "--quiet", "origin", "main")
    releases = list_releases()
    stale = leftovers(releases)
    for old in stale:
        print(f"Deleting the draft {old['tag_name']} (id {old['id']}): that tag is published already.")
        delete_draft(old)
    releases = [r for r in releases if r not in stale]
    draft = waiting(releases, platform)
    if draft:
        commit = draft["target_commitish"]
        if not re.fullmatch(r"[0-9a-f]{40}", commit) or not contains("origin/main", commit):
            sys.exit(f"The draft {draft['tag_name']} is for {commit}, which isn't on main: delete it, then run this "
                     "again.")
        print(f"The draft {draft['tag_name']} has the other half: building its commit, {commit[:7]}.")
    else:
        commit = read("git", "-C", HERE, "rev-parse", "origin/main").strip()
    short = commit[:7]
    uplugin = read("git", "-C", HERE, "show", f"{commit}:plugin/unreal/Autopilot/Autopilot.uplugin")
    version = json.loads(uplugin.lstrip("\ufeff"))["VersionName"]
    base = f"v{version}-{short}"
    tag = f"{base}.{args.respin}" if args.respin else base
    if draft:  # (its tag: the other half's run may have been a respin)
        if args.respin and draft["tag_name"] != tag:
            sys.exit(f"The draft {draft['tag_name']} waits for the other half: run this without --respin to finish "
                     "it, or delete it first.")
        tag = draft["tag_name"]
    published = {r["tag_name"] for r in releases if not r["draft"]}
    if tag in published:
        respin = next(n for n in itertools.count(2) if f"{base}.{n}" not in published)
        print(f"{short} is released already: https://github.com/{GITHUB}/releases/tag/{tag}\nTo release it again "
              f"(a published release can't change), run this with --respin {respin}.")
        return

    # This half, built in a worktree of that commit
    os.makedirs(work, exist_ok=True)
    tree = os.path.join(work, f"autopilot-release-{short}")
    out = os.path.join(work, f"autopilot-release-{short}-{platform}")
    if os.path.exists(tree):
        run("git", "-C", HERE, "worktree", "remove", "--force", tree)
    shutil.rmtree(out, ignore_errors=True)
    run("git", "-C", HERE, "worktree", "add", "--quiet", "--detach", tree, commit)
    try:
        print(f"Building Autopilot {version} ({short}): {HALVES[platform]}...", flush=True)
        build = ["uv", "run", "--no-project", "--python", "3.12", os.path.join(tree, "tools", "package_release.py"),
                 out, "--unreal", args.unreal, "--unversioned", "--sign"] + (["--only-unreal"] if platform == "Mac"
                                                                             else [])
        build += ["--no-upm"] if args.no_upm else []
        if subprocess.run(build, env={**os.environ, "RG_WORK": work}).returncode != 0:
            sys.exit("The build failed: see the lines above.")
    finally:
        run("git", "-C", HERE, "worktree", "remove", "--force", tree)
    files = sorted(os.path.join(out, n) for n in os.listdir(out))

    # What GitHub has now, not what it had before the build: the other half's run may have made the draft since
    releases = list_releases()
    if any(r["tag_name"] == tag and not r["draft"] for r in releases):
        sys.exit(f"{tag} was published during this build, without this half: to release {short} with both halves, run "
                 f"this with --respin. The downloads are in {out}")
    draft, extra = draft_of(releases, tag, platform)
    for old in extra:
        print(f"Deleting a second draft {tag} (id {old['id']}): this half goes into the first.")
        delete_draft(old)

    # Into the draft: a new one (and older drafts go: they would never be complete), or the other half's
    made_it = draft is None
    if made_it:
        for old in releases:
            # (not one newer than this commit: the list is from after the build, and main may have moved on since)
            if old["draft"] and TAG.match(old["tag_name"]) and contains(commit, old["target_commitish"]):
                print(f"Deleting the draft {old['tag_name']}: it's for an older commit than {short}.")
                delete_draft(old)
        previous = next((r["tag_name"] for r in releases if not r["draft"] and not r["prerelease"]), None)
        notes = os.path.join(work, f"autopilot-release-{short}-notes.md")
        with open(notes, "w", encoding="utf-8", newline="\n") as f:
            f.write(f"Autopilot {version}, built from {commit}. Pick the download for your engine (for Unreal, the one "
                    "for your project's Unreal version, a zip on Windows or a disk image on macOS); README.md says how "
                    "to install it, and SHA256SUMS.txt lists the downloads' hashes. "
                    f"{UNSIGNED_UNITY if args.no_upm else SIGNED} The downloads use Aura at https://app.tryaura.dev."
                    f"{f' A respin of {base}: the same commit, built again.' if args.respin else ''}\n\n")
        run("gh", "release", "create", tag, *files, "-R", GITHUB, "--draft", "--target", commit, "--title",
            f"Autopilot {version} ({tag.rsplit('-', 1)[1]})", "--notes-file", notes, "--generate-notes",
            *(["--notes-start-tag", previous] if previous else []))
        os.remove(notes)
        # The other run may have made one too while this one built, and uploaded: then this run's goes (see draft_of)
        first, extra = draft_of(list_releases(), tag, platform)
        for old in extra:
            print(f"Moving this half to the draft {tag} the other half's run made first (id {first['id']}).")
            delete_draft(old)
        if extra:
            draft, made_it = first, False
    if not made_it:
        draft = wait_for_upload(draft)
        sums = os.path.join(out, "SHA256SUMS.txt")
        with open(sums, encoding="utf-8") as f:
            ours = f.read()
        theirs = ""
        if any(a["name"] == "SHA256SUMS.txt" for a in draft["assets"]):
            got = os.path.join(work, f"autopilot-release-{short}-sums")
            run("gh", "release", "download", tag, "-R", GITHUB, "--pattern", "SHA256SUMS.txt", "--dir", got,
                "--clobber")
            with open(os.path.join(got, "SHA256SUMS.txt"), encoding="utf-8") as f:
                theirs = f.read()
            shutil.rmtree(got, ignore_errors=True)
        with open(sums, "w", encoding="utf-8", newline="\n") as f:
            f.write(merged_sums(theirs, ours))
        run("gh", "release", "upload", tag, *files, "-R", GITHUB, "--clobber")
        corrected = unsigned_unity(draft.get("body") or "") if args.no_upm else None
        if corrected is not None:  # (the Mac half made the draft: its notes say the Unity package is signed)
            notes = os.path.join(work, f"autopilot-release-{short}-notes.md")
            with open(notes, "w", encoding="utf-8", newline="\n") as f:
                f.write(corrected)
            run("gh", "release", "edit", tag, "-R", GITHUB, "--notes-file", notes)
            os.remove(notes)

    assets = json.loads(read("gh", "release", "view", tag, "-R", GITHUB, "--json", "assets"))["assets"]
    names = [a["name"] for a in assets]
    # (The run that made the draft doesn't publish: the other run adds its half after, and is still uploading when
    # this one could see its files; it publishes, once SHA256SUMS.txt has both halves')
    if not ((complete(names) and not made_it) or args.publish):
        other = "Mac" if platform == "Win64" else "Windows"
        print(f"\nThe draft {tag} has {HALVES[platform]}. Run this on {'a ' if other == 'Mac' else ''}{other} for the "
              "rest, which publishes it (a run there now finds this draft when its build ends); or run this again "
              "with --publish to publish it without.")
        print(f"The downloads are also in {out}")
        return
    run("gh", "release", "edit", tag, "-R", GITHUB, "--draft=false", "--latest")
    print(f"\nReleased https://github.com/{GITHUB}/releases/tag/{tag} (Latest)")
    for name in sorted(n for n in names if half(n)):
        print(f"    https://github.com/{GITHUB}/releases/latest/download/{name}")
    print(f"The downloads are also in {out}")


if __name__ == "__main__":
    main()
