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

Every release's zips have the same names, Autopilot-<engine>.zip (package_release.py --unversioned), so a link like
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
its tag. Run the two halves one after the other, not at once: two runs that both find no draft make two.

The downloads are signed as they're built (package_release.py --sign, which says what each needs): on Windows the
Unity package by Unity's upm (UNITY_ORGANIZATION and its signing service account's UPM_SERVICE_ACCOUNT_KEY_ID and
UPM_SERVICE_ACCOUNT_KEY_SECRET) and the Unreal plugins' binaries with DigiCert KeyLocker's smctl (the TribeVR Inc
keypair); on a Mac the Unreal plugins' binaries with the Developer ID Application certificate in the keychain, and the
zips notarized with notarytool's stored credentials (autopilot-notary), and on Windows every download's install.ps1
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


def half(name):
    """Which half of a release a file of it belongs to: Mac for the Mac Unreal zips, Win64 for the rest of the zips
    (README.md and SHA256SUMS.txt are both halves')."""
    if not name.endswith(".zip"):
        return None
    return "Mac" if name.endswith("-Mac.zip") else "Win64"


def complete(names):
    """Whether a release's files are both halves: Unreal for Windows and for Mac, Unity and Godot."""
    return (any(n.endswith("-Win64.zip") for n in names) and any(n.endswith("-Mac.zip") for n in names)
            and "Autopilot-Unity.zip" in names and "Autopilot-Godot.zip" in names)


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
    releases = json.loads(read("gh", "api", f"repos/{GITHUB}/releases?per_page=100"))
    draft = waiting(releases, platform)
    if draft:
        commit = draft["target_commitish"]
        if not re.fullmatch(r"[0-9a-f]{40}", commit) or subprocess.run(
                ["git", "-C", HERE, "merge-base", "--is-ancestor", commit, "origin/main"]).returncode != 0:
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
    draft = next((r for r in releases if r["draft"] and r["tag_name"] == tag), None)

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

    # Into the draft: a new one (and older drafts go: they would never be complete), or the other half's
    if draft is None:
        for old in releases:
            if old["draft"] and TAG.match(old["tag_name"]):
                print(f"Deleting the draft {old['tag_name']}: it's for an older commit than {short}.")
                run("gh", "api", "-X", "DELETE", f"repos/{GITHUB}/releases/{old['id']}")
        previous = next((r["tag_name"] for r in releases if not r["draft"] and not r["prerelease"]), None)
        notes = os.path.join(work, f"autopilot-release-{short}-notes.md")
        with open(notes, "w", encoding="utf-8", newline="\n") as f:
            f.write(f"Autopilot {version}, built from {commit}. Pick the zip for your engine (for Unreal, the one for "
                    "your project's Unreal version, on Windows or macOS); README.md says how to install it, and "
                    f"SHA256SUMS.txt lists the zips' hashes. {UNSIGNED_UNITY if args.no_upm else SIGNED} The "
                    "downloads use Aura at https://app.tryaura.dev."
                    f"{f' A respin of {base}: the same commit, built again.' if args.respin else ''}\n\n")
        run("gh", "release", "create", tag, *files, "-R", GITHUB, "--draft", "--target", commit, "--title",
            f"Autopilot {version} ({tag.rsplit('-', 1)[1]})", "--notes-file", notes, "--generate-notes",
            *(["--notes-start-tag", previous] if previous else []))
        os.remove(notes)
    else:
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

    assets =json.loads(read("gh", "release", "view", tag, "-R", GITHUB, "--json", "assets"))["assets"]
    names = [a["name"] for a in assets]
    if not (complete(names) or args.publish):
        other = "Mac" if platform == "Win64" else "Windows"
        print(f"\nThe draft {tag} has {HALVES[platform]}. Run this on {'a ' if other == 'Mac' else ''}{other} for the "
              f"rest, which publishes it; or run this again with --publish to publish it without.")
        print(f"The downloads are also in {out}")
        return
    run("gh", "release", "edit", tag, "-R", GITHUB, "--draft=false", "--latest")
    print(f"\nReleased https://github.com/{GITHUB}/releases/tag/{tag} (Latest)")
    for name in sorted(n for n in names if n.endswith(".zip")):
        print(f"    https://github.com/{GITHUB}/releases/latest/download/{name}")
    print(f"The downloads are also in {out}")


if __name__ == "__main__":
    main()
