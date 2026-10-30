"""Build Autopilot downloads: everything a user needs to use Autopilot without this repository.

    python tools/package_release.py OUT_DIR [--unreal 5.5,5.6,5.7,5.8] [--no-unreal | --only-unreal]
                                            [--epic <engines folder>] [--engine-dir <an engine's folder>]...
                                            [--aura-server https://<an Aura server>] [--unversioned]

OUT_DIR gets, for each engine, one download with the plugin, the agent service and how to set them up:

    Autopilot-<version>-Unreal<x.y>-Win64.zip   Autopilot/: the plugin built for that Unreal (BuildPlugin, Win64:
    Autopilot-<version>-Unreal<x.y>-Mac.dmg      binaries for the editor and for game builds, without debug
                                                 symbols), to copy into the project's Plugins/; -Mac.dmg, a disk
                                                 image, when built on macOS (Mac binaries, Apple silicon and Intel
                                                 alike; see unreal_dmg for why not a zip)
    Autopilot-<version>-Unity.zip               com.ramenvr.autopilot-<version>.tgz, for Package Manager > Add package
                                                 from tarball
    Autopilot-<version>-Godot.zip               addons/autopilot/, to copy into the project

and inside each plugin (Unreal Autopilot/AgentService/, Unity AgentService~/, Godot agent_service/): this checkout's
autopilot wheel and install.ps1 / install.sh (packaging/standalone/), which install the agent service as the `autopilot`
command with uv; the plugin's first-run setup runs them (Install), and starts the agent service itself after.
Beside the zips: one README.md, the setup for every engine (packaging/standalone/README.md; no README goes in a zip,
nor the plugins' own, which are for contributors), and SHA256SUMS.txt, the zips' hashes. A git clone must be clean:
the README names the commit the downloads came from. A copy without git's history (GitHub's Download ZIP, unpacked)
builds too, named by its files' hash instead (see source()). Unreal builds need that engine installed, and are for
the platform this runs on (Windows or macOS); --unreal picks the versions (default: every UE_5.x installed under
--epic). On macOS, each engine builds with the newest Xcode in /Applications it accepts (Epic's engines refuse Xcodes
newer than their Engine/Config/Apple/Apple_SDK.json allows, e.g. Unreal 5.4 to 5.6 anything after 16.x), through
DEVELOPER_DIR: install the ones they need side by side (e.g. with xcodes). The work is done in a temporary folder under
RG_WORK when set (kept when a build fails, with its log): BuildPlugin fails on long paths, so on Windows set it to a
short one (e.g. C:/Temp).

--engine-dir: also build for the engine in that folder (its version from Engine/Build/Build.version), for an engine
that isn't under --epic, such as one built from source (C:/UnrealEngine); without --unreal, only for those. A plugin's
binaries load only in an engine with the same build id, so an engine that isn't Epic's own build (Build.version's
IsPromotedBuild 0: built from source, or an installed build a studio made from its source) gets
Autopilot-<version>-Unreal<x.y>-Source-Win64.zip (-Mac.dmg), for that engine only. A source tree (no
Engine/Build/InstalledBuild.txt) gets the editor's binaries only: its game builds compile the plugin's Source/
themselves, and BuildPlugin would otherwise build the whole engine twice more for UnrealGame (Development and
Shipping). An engine whose editor isn't built yet is built first, which takes as long as building it yourself.
With --only-unreal (as tools/build_unreal.ps1 runs it), OUT_DIR gets the Unreal zips only, no Unity or Godot one.

--aura-server: the downloads use that Aura server (a preview, before Autopilot's endpoints are on Aura's own): they
carry settings.env beside the installer, which the installer writes into the user's autopilot.env
(AUTOPILOT_AURA_SERVER) over what an earlier download wrote, so their Aura sign-in, runs through Aura and shared
run data go there. A download without it takes out the server an earlier one wrote.

--unversioned: the zips are named Autopilot-<engine>.zip instead of Autopilot-<version>-<engine>.zip
(tools/release.py's: every release's files have the same names, so a link to the newest release's download stays the
same from one release to the next, and a published release, which can't be changed, never needs renaming).

--sign (tools/release.py's): the downloads are signed as they're built, and each signature read back. The Unity package
is packed by Unity's upm (upm pack --organization-id $UNITY_ORGANIZATION, signed for that Unity organization with the
service account in UPM_SERVICE_ACCOUNT_KEY_ID and UPM_SERVICE_ACCOUNT_KEY_SECRET, which has the Package Manager Package
Signer role): Unity 6.3 and later flag a package without a signature. The Unreal plugin's binaries are signed before
they're zipped, with the certificates Aura's own builds are signed with. On Windows each .dll with DigiCert
KeyLocker's smctl (keypair WIN_KEYPAIR, the TribeVR Inc certificate; smctl set up with its SM_* variables and client
certificate); on a Mac each .dylib with the Developer ID Application identity of team MAC_TEAM in the keychain
(hardened runtime, Apple's timestamp), and then the disk image is signed with it too, notarized with the credentials
stored as NOTARY_PROFILE (xcrun notarytool store-credentials autopilot-notary --apple-id <id> --team-id 9ZSHVS35MZ
--password <app-specific password>), and has the ticket stapled to it, which Gatekeeper's verdict on it then has to
show. On Windows, every download's install.ps1 (what the plugins run to install the agent service) is signed
with smctl too. What signing needs is checked before anything is built. --no-upm: the
Unity package without upm's signature, for a computer without its service account (the rest still signed). Godot
has no signature for an addon, nor binaries in ours.
"""

import argparse
import glob
import hashlib
import io
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STANDALONE = os.path.join(HERE, "packaging", "standalone")
UNREAL = os.path.join(HERE, "plugin", "unreal", "Autopilot")
UNITY = os.path.join(HERE, "plugin", "unity", "com.ramenvr.autopilot")
GODOT = os.path.join(HERE, "plugin", "godot", "addons", "autopilot")
MAC = sys.platform == "darwin"  # Unreal builds are for the platform this runs on: Mac or Win64
PLATFORM = "Mac" if MAC else "Win64"
EPIC = "/Users/Shared/Epic Games" if MAC else "C:/Program Files/Epic Games"
RUNUAT = "RunUAT.sh" if MAC else "RunUAT.bat"
SKIP_NAMES = {"__pycache__", ".DS_Store"}

# The plugins' own READMEs (their source and settings, for contributors): the download's README.md has the setup
PLUGIN_READMES = {"README.md", "README.md.meta"}


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git(*args):
    """git's output in this checkout; git's own message when it fails (e.g. "detected dubious ownership")."""
    try:
        done = subprocess.run(["git", *args], cwd=HERE, capture_output=True, text=True)
    except FileNotFoundError:
        sys.exit(f"{HERE} is a git clone, but Git isn't on PATH: install Git (https://git-scm.com), then run this "
                 "again.")
    if done.returncode != 0:
        sys.exit(f"git {' '.join(args)} failed in {HERE}:\n{(done.stderr or done.stdout).strip()}")
    return done.stdout.strip()


# What the downloads are built from (beside HERE): a copy without git's history is named by these files' hash
SOURCES = ["pyproject.toml", "README.md", "LICENSE", "autopilot", "packaging/standalone", "plugin/unreal/Autopilot",
           "plugin/unity/com.ramenvr.autopilot", "plugin/godot/addons/autopilot"]
# ... but not what building or running them leaves there (Unreal's build output, OpenCode's packages, the wheel's stamp)
SOURCE_SKIP = SKIP_NAMES | {"Binaries", "Intermediate", "node_modules", "build.json"}
FILES_EPOCH = 1767225600  # 2026-01-01: the date in a copy's wheel, so the same files build the same bytes


def files_hash(root):
    """The hash of the files under root that the downloads are built from (SOURCES): their paths and bytes."""
    h = hashlib.sha256()
    for top in SOURCES:
        full = os.path.join(root, top)
        paths = [full] if os.path.isfile(full) else []
        for folder, dirs, names in os.walk(full):
            dirs[:] = [d for d in dirs if d not in SOURCE_SKIP and not d.endswith(".egg-info")]
            paths += [os.path.join(folder, n) for n in names
                      if n not in SOURCE_SKIP and not n.endswith((".pyc", ".pyo"))]
        for path in sorted(paths, key=lambda p: os.path.relpath(p, root).replace(os.sep, "/")):
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            h.update(f"{rel}\0{sha256(path)}\n".encode())
    return h.hexdigest()


def source(root=HERE):
    """(the downloads' ID, SOURCE_DATE_EPOCH for the wheel, what the README says they were built from). The ID is what
    the agent service announces and a plugin compares with its own (build_wheel), so it must change whenever what is
    built changes. A git clone: its commit, dated at the commit; one with uncommitted changes is refused, as the
    commit wouldn't name what was built. A copy without git's history (GitHub's Download ZIP, unpacked): "files-" and
    the hash of the files the downloads are built from, dated FILES_EPOCH, so the same files make the same ID and
    bytes, and any change another."""
    if not os.path.exists(os.path.join(root, ".git")):  # (a worktree's .git is a file)
        build_id = f"files-{files_hash(root)[:40]}"
        return build_id, FILES_EPOCH, f"{build_id} (a copy without git history, named by its files' hash)"
    dirty = git("status", "--porcelain", "--untracked-files=no")
    if dirty:
        sys.exit("The checkout has uncommitted changes; commit them first, so the downloads name their commit:\n"
                 + dirty)
    commit = git("rev-parse", "HEAD")
    return commit, int(git("log", "-1", "--format=%ct")), f"commit {commit}"


def versions():
    """(autopilot's version, the plugins' version)."""
    with open(os.path.join(HERE, "autopilot", "__init__.py"), encoding="utf-8") as f:
        autopilot = re.search(r'__version__ = "([^"]+)"', f.read()).group(1)
    with open(os.path.join(UNREAL, "Autopilot.uplugin"), encoding="utf-8") as f:
        plugin = json.load(f)["VersionName"]
    return autopilot, plugin


def build_wheel(out_dir, commit, epoch):
    """This checkout's wheel (its files dated at epoch, the commit's time, so the same commit builds the same bytes),
    with autopilot/build.json naming the commit (source()'s ID): the agent service announces it, and a plugin carrying
    another one replaces it (agent-service.json names the same commit). build.json also has the settings the download
    carries (SETTINGS), so downloads for two Aura servers are two wheels: a plugin whose wheel isn't the one installed
    installs its own, and the installer saves its settings."""
    shutil.rmtree(os.path.join(HERE, "build"), ignore_errors=True)
    env = {**os.environ, "SOURCE_DATE_EPOCH": str(epoch)}
    stamp = os.path.join(HERE, "autopilot", "build.json")
    with open(stamp, "w", encoding="utf-8") as f:
        json.dump({"commit": commit, **({"settings": SETTINGS} if SETTINGS else {})}, f)
    try:
        subprocess.run(["uv", "build", "--wheel", "--out-dir", out_dir], cwd=HERE, check=True, env=env)
    finally:
        os.remove(stamp)
    wheels = glob.glob(os.path.join(out_dir, "autopilot-*.whl"))
    if len(wheels) != 1:
        sys.exit(f"expected one wheel, got {wheels}")
    return wheels[0]


def add_tree(z, src, arc, skip=lambda rel: False):
    """Every file under src into the zip at arc/, but those skip(relative path) is true for."""
    for folder, dirs, names in os.walk(src):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_NAMES)
        for name in sorted(names):
            if name in SKIP_NAMES:
                continue
            full = os.path.join(folder, name)
            rel = os.path.relpath(full, src).replace(os.sep, "/")
            if not skip(rel):
                z.write(full, f"{arc}/{rel}")


# What the downloads' installer writes into autopilot.env (settings.env): --aura-server's AUTOPILOT_AURA_SERVER
SETTINGS = {}


AGENT_SERVICE_STAMP = "agent-service.json"  # which agent service a download carries; its installer keeps a copy


def agent_service_files(wheel, commit):
    """[(name, bytes, mode)]: what the plugin carries to install the agent service with (its first-run setup's
    Install runs install.ps1 or install.sh), settings.env when SETTINGS has any, and agent-service.json: which wheel
    this is ({"sha256", "commit", "wheel"}). The installer copies it into the user settings folder once it has
    installed that wheel; a plugin whose copy differs from the installed one installs its own before it starts it."""
    with open(wheel, "rb") as f:
        files = [(os.path.basename(wheel), f.read(), 0o644)]
    stamp = {"sha256": sha256(wheel), "commit": commit, "wheel": os.path.basename(wheel)}
    files.append((AGENT_SERVICE_STAMP, (json.dumps(stamp, indent=1) + "\n").encode(), 0o644))
    for name in ("install.ps1", "install.sh"):
        signed = SIGNED_INSTALLER if name == "install.ps1" else None  # (--sign on Windows: sign_installer's)
        with open(signed or os.path.join(STANDALONE, name), "rb") as f:
            data = f.read()
        if name.endswith(".sh"):
            data = data.replace(b"\r\n", b"\n")  # (sh refuses CRLF)
        files.append((name, data, 0o755 if name.endswith(".sh") else 0o644))
    if SETTINGS:
        files.append(("settings.env", "".join(f"{k}={v}\n" for k, v in SETTINGS.items()).encode(), 0o644))
    return files


def add_agent_service(z, wheel, folder, commit):
    """The agent service's installer and wheel into the zip at folder/ (inside the plugin: the plugin installs it)."""
    for name, data, mode in agent_service_files(wheel, commit):
        info = zipfile.ZipInfo(f"{folder}/{name}", (2026, 1, 1, 0, 0, 0))
        info.external_attr = mode << 16
        z.writestr(info, data, zipfile.ZIP_DEFLATED)


def readme(plugin_version, built_from, stem=None):
    """The downloads' README, written beside them; built_from is source()'s (e.g. "commit <sha>"), stem what the zips'
    names start with (default Autopilot-<version>; --unversioned's Autopilot)."""
    with open(os.path.join(STANDALONE, "README.md"), encoding="utf-8") as f:
        text = f.read().format(version=plugin_version, stem=stem or f"Autopilot-{plugin_version}")
    return text + f"\nBuilt from Autopilot {built_from}.\n"


def engine_root(folder):
    """The engine's root folder (the one holding Engine/), given it or its Engine/ folder."""
    folder = os.path.abspath(folder)
    if not os.path.exists(os.path.join(folder, "Engine", "Build", "Build.version")) \
            and os.path.exists(os.path.join(folder, "Build", "Build.version")):
        return os.path.dirname(folder)
    return folder


def engine_build(root):
    """The engine in root's Engine/Build/Build.version (utf-8-sig: an edited one may start with a byte order mark)."""
    try:
        with open(os.path.join(root, "Engine", "Build", "Build.version"), encoding="utf-8-sig") as f:
            build = json.load(f)
        int(build["MajorVersion"]), int(build["MinorVersion"])
        return build
    except (OSError, ValueError, KeyError, TypeError):
        sys.exit(f"{root} isn't an Unreal engine's folder (no readable Engine/Build/Build.version)")


def engine_version(root):
    build = engine_build(root)
    return f"{build['MajorVersion']}.{build['MinorVersion']}"


def epics_own_build(root):
    """Whether the engine in root is one Epic built (the launcher's): another engine's plugins don't load in it."""
    return bool(engine_build(root).get("IsPromotedBuild"))


def source_tree(root):
    """Whether the engine in root is built in place from source: an installed build has InstalledBuild.txt."""
    return not os.path.exists(os.path.join(root, "Engine", "Build", "InstalledBuild.txt"))


def version_tuple(text):
    """"16.4" -> (16, 4), to compare versions with."""
    return tuple(int(x) for x in re.findall(r"\d+", text)[:3])


def xcode_range(root):
    """(lowest, highest) Xcode the engine in root builds with on macOS (MinVersion and MaxVersion in its
    Engine/Config/Apple/Apple_SDK.json, which isn't strict JSON: it has comments), or None when it doesn't say."""
    try:
        with open(os.path.join(root, "Engine", "Config", "Apple", "Apple_SDK.json"), encoding="utf-8-sig") as f:
            text = f.read()
    except OSError:
        return None
    low, high = (re.search(rf'"{key}"\s*:\s*"([\d.]+)"', text) for key in ("MinVersion", "MaxVersion"))
    return (version_tuple(low.group(1)), version_tuple(high.group(1))) if low and high else None


def installed_xcodes(applications="/Applications"):
    """{(version): its Developer folder} for each Xcode in applications (Xcode.app, Xcode-16.4.0.app, ...)."""
    found = {}
    for app in glob.glob(os.path.join(applications, "Xcode*.app")):
        try:
            with open(os.path.join(app, "Contents", "version.plist"), "rb") as f:
                version = plistlib.load(f)["CFBundleShortVersionString"]
        except (OSError, KeyError, plistlib.InvalidFileException):
            continue
        found[version_tuple(version)] = os.path.join(app, "Contents", "Developer")
    return found


def xcode_for(engine, root):
    """The Developer folder (for DEVELOPER_DIR) of the newest installed Xcode the engine in root builds with, else
    exits naming the versions it takes; None when the engine doesn't say (then the selected Xcode builds it)."""
    span = xcode_range(root)
    if span is None:
        return None
    xcodes = installed_xcodes()
    fits = sorted(v for v in xcodes if span[0] <= v <= span[1])
    if not fits:
        low, high = (".".join(map(str, v)) for v in span)
        have = ", ".join(".".join(map(str, v)) for v in sorted(xcodes)) or "none"
        sys.exit(f"Unreal {engine} builds with Xcode {low} to {high}, and none is in /Applications (there: {have}): "
                 "install one beside the others (e.g. xcodes install <version>), then run this again.")
    return xcodes[fits[-1]]


def build_unreal(engine, root, work):
    """The plugin built for Unreal <engine>, the engine in root (BuildPlugin into a short path: long ones break it);
    returns its folder. A source tree builds the editor's binaries only (see --engine-dir)."""
    uat = os.path.join(root, "Engine", "Build", "BatchFiles", RUNUAT)
    if not os.path.exists(uat):
        sys.exit(f"Unreal {engine} isn't installed at {root}")
    source = source_tree(root)
    name = f"ue{engine.replace('.', '')}{'' if epics_own_build(root) else 'src'}"
    package = os.path.join(work, name)
    shutil.rmtree(package, ignore_errors=True)
    log = os.path.join(work, f"{name}.log")
    targets = ["-NoTargetPlatforms"] if source else [f"-TargetPlatforms={PLATFORM}", "-Rocket"]
    developer = xcode_for(engine, root) if MAC else None
    env = {**os.environ, "DEVELOPER_DIR": developer} if developer else None
    if developer:
        print(f"  with {os.path.dirname(os.path.dirname(developer))}")
    with open(log, "w", encoding="utf-8", errors="replace") as f:
        done = subprocess.run([uat, "BuildPlugin", f"-Plugin={os.path.join(UNREAL, 'Autopilot.uplugin')}",
                               f"-Package={package}", *targets], stdout=f, stderr=subprocess.STDOUT, env=env)
    if done.returncode != 0:
        sys.exit(f"BuildPlugin for Unreal {engine} failed ({done.returncode}): see {log}")
    unstamp(os.path.join(package, "Autopilot.uplugin"))
    return package


def unstamp(uplugin):
    """Take out the EngineVersion BuildPlugin writes into the .uplugin (e.g. "5.4.0"): with it, an editor of another
    patch (5.4.4), or one built from source, asks its user to confirm loading the plugin; without it, the plugin loads
    as compatible. The binaries are still matched to the engine by their own build id."""
    if not os.path.exists(uplugin):
        return
    with open(uplugin, encoding="utf-8-sig") as f:
        descriptor = json.load(f)
    if descriptor.pop("EngineVersion", None) is not None:
        with open(uplugin, "w", encoding="utf-8") as f:
            json.dump(descriptor, f, indent="\t")
            f.write("\n")


# --sign: the certificates Aura's own builds are signed with
WIN_KEYPAIR = "key_1306912465"  # DigiCert KeyLocker, the TribeVR Inc certificate
WIN_SIGNER = "CN=TribeVR Inc,"  # what the certificate's subject starts with
MAC_TEAM = "9ZSHVS35MZ"  # Apple Developer ID team
NOTARY_PROFILE = "autopilot-notary"  # notarytool's stored credentials (store-credentials, see the top)
SIGNED_INSTALLER = None  # --sign on Windows: install.ps1 signed by sign_installer, which every download carries


def signer(unreal=True, unity=False, upm=True):
    """Before anything is built: for Unity (upm), upm and its service account; for Unreal on a Mac, the Developer ID
    Application identity of MAC_TEAM (returned: its hash) and notarytool's credentials; on Windows smctl and its
    KeyLocker connection (the Unreal binaries and every download's install.ps1). Exits saying what's missing."""
    if unity and upm:
        missing = [name for name in ("UNITY_ORGANIZATION", "UPM_SERVICE_ACCOUNT_KEY_ID",
                                     "UPM_SERVICE_ACCOUNT_KEY_SECRET") if not os.environ.get(name)]
        if shutil.which("upm") is None or missing:
            sys.exit("Signing the Unity package needs Unity's upm (https://cdn.packages.unity.com/upm-cli/install.ps1 "
                     "or install.sh) and a service account with the Package Manager Package Signer role: "
                     + (f"set {', '.join(missing)}" if missing else "install upm") + ", then run this again.")
    if PLATFORM == "Mac":
        if not unreal:
            return None
        found = subprocess.run(["security", "find-identity", "-v", "-p", "codesigning"], capture_output=True, text=True)
        identity = next((line.split()[1] for line in found.stdout.splitlines()
                         if "Developer ID Application" in line and f"({MAC_TEAM})" in line), None)
        if identity is None:
            sys.exit(f"No Developer ID Application identity of team {MAC_TEAM} in the keychain: import the certificate "
                     "(its .p12), then run this again.")
        if subprocess.run(["xcrun", "notarytool", "history", "--keychain-profile", NOTARY_PROFILE],
                          capture_output=True).returncode != 0:
            sys.exit(f"notarytool has no working credentials named {NOTARY_PROFILE}: run xcrun notarytool "
                     f"store-credentials {NOTARY_PROFILE} --apple-id <id> --team-id {MAC_TEAM} --password "
                     "<app-specific password>, then run this again.")
        return identity
    if shutil.which("smctl") is None:
        sys.exit("smctl (DigiCert KeyLocker) isn't on PATH: install it and set it up (SM_HOST, SM_API_KEY, "
                 "SM_CLIENT_CERT_FILE, SM_CLIENT_CERT_PASSWORD), then run this again.")
    if subprocess.run(["smctl", "healthcheck"], capture_output=True).returncode != 0:
        sys.exit("smctl healthcheck failed: run it to see what KeyLocker needs, then run this again.")
    return None


def sign_binaries(package, identity):
    """Signs the plugin's binaries in package (.dll on Windows; .dylib on a Mac, not those in debug symbols) and reads
    every signature back: a signer's success alone isn't trusted (smctl has exited 0 without signing)."""
    mac = PLATFORM == "Mac"
    binaries = []
    for folder, dirs, names in os.walk(package):
        dirs[:] = sorted(d for d in dirs if not d.endswith(".dSYM"))
        binaries += [os.path.join(folder, n) for n in sorted(names) if n.endswith(".dylib" if mac else ".dll")]
    if not binaries:
        sys.exit(f"No binaries to sign in {package}")
    for path in binaries:
        sign = (["codesign", "--force", "--timestamp", "--options", "runtime", "--sign", identity, path] if mac
                else ["smctl", "sign", "--keypair-alias", WIN_KEYPAIR, "--input", path])
        if subprocess.run(sign).returncode != 0:
            sys.exit(f"Signing {path} failed: see the lines above.")
    if mac:
        for path in binaries:
            done = subprocess.run(["codesign", "--verify", "--strict", "--verbose=2", path], capture_output=True,
                                  text=True)
            if done.returncode != 0:
                sys.exit(f"{path} isn't validly signed after codesign:\n{done.stderr.strip()}")
        return binaries
    check_signed(binaries)
    return binaries


def check_signed(paths):
    """Each of paths has a valid Authenticode signature by TribeVR Inc (WIN_SIGNER), or this exits naming it."""
    # (one PowerShell for them all: each line Status|subject, in paths' order)
    quoted = ",".join("'" + p.replace("'", "''") + "'" for p in paths)
    done = subprocess.run(["powershell", "-NoProfile", "-Command",
                           f"foreach ($p in @({quoted})) {{ $s = Get-AuthenticodeSignature -LiteralPath $p; "
                           "\"$($s.Status)|$($s.SignerCertificate.Subject)\" }"], capture_output=True, text=True)
    lines = done.stdout.splitlines()
    for path, line in zip(paths, lines + [""] * len(paths)):
        status, _, subject = line.partition("|")
        if status != "Valid" or not subject.startswith(WIN_SIGNER):
            sys.exit(f"{path} isn't signed by TribeVR Inc after smctl (status: {status or 'unknown'}, signer: "
                     f"{subject or 'none'})")


def sign_installer(work):
    """--sign on Windows: install.ps1 signed with smctl into work (smctl sign, then the signature read back), and
    every download carries that copy (SIGNED_INSTALLER): the plugins run it to install the agent service. Returns its
    path."""
    global SIGNED_INSTALLER
    path = os.path.join(work, "install.ps1")
    shutil.copyfile(os.path.join(STANDALONE, "install.ps1"), path)
    if subprocess.run(["smctl", "sign", "--keypair-alias", WIN_KEYPAIR, "--input", path]).returncode != 0:
        sys.exit(f"Signing {path} failed: see the lines above.")
    check_signed([path])
    SIGNED_INSTALLER = path
    return path


def notarize(path):
    """Notarizes a Mac download (its disk image) with Apple (waits for the verdict); exits with Apple's log, which
    names every binary it refused, unless it's accepted."""
    print(f"Notarizing {os.path.basename(path)} (Apple usually answers in minutes)...", flush=True)
    done = subprocess.run(["xcrun", "notarytool", "submit", path, "--keychain-profile", NOTARY_PROFILE, "--wait",
                           "--timeout", "30m", "--output-format", "json"], capture_output=True, text=True)
    try:
        result = json.loads(done.stdout)
    except ValueError:
        result = {}
    if done.returncode == 0 and result.get("status") == "Accepted":
        return
    log = ""
    if result.get("id"):
        log = subprocess.run(["xcrun", "notarytool", "log", result["id"], "--keychain-profile", NOTARY_PROFILE],
                             capture_output=True, text=True).stdout
    sys.exit(f"Notarizing {path} failed (status: {result.get('status', 'unknown')}):\n"
             f"{(log or done.stdout or done.stderr).strip()}")


def not_shipped(rel):
    """What of a BuildPlugin package stays out of the Unreal download: debug symbols and the plugin's own README."""
    return rel.endswith(".pdb") or ".dSYM/" in rel or rel in PLUGIN_READMES


def unreal_zip(out, engine, package, wheel, stem, commit, source=False):
    """stem: what the zip's name starts with (Autopilot-<version>, or Autopilot with --unversioned). source: built for
    an engine that isn't Epic's own build (its zip is named so: it loads in that engine only)."""
    path = os.path.join(out, f"{stem}-Unreal{engine}{'-Source' if source else ''}-{PLATFORM}.zip")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        add_tree(z, package, "Autopilot", skip=not_shipped)
        add_agent_service(z, wheel, "Autopilot/AgentService", commit)
    return path


def unreal_dmg(out, engine, package, wheel, stem, commit, source=False):
    """The Mac download: the same Autopilot/ as unreal_zip's, in a disk image (hdiutil). Not a zip: a zip can't carry
    a stapled ticket, so a Mac that unzipped it from the browser (every file quarantined) asks Apple online about each
    dylib the editor loads, and when that lookup fails says Apple "could not verify" it. A disk image can be signed,
    notarized and stapled (sign_dmg, notarize, staple), as Aura's own DMG is."""
    path = os.path.join(out, f"{stem}-Unreal{engine}{'-Source' if source else ''}-{PLATFORM}.dmg")
    with tempfile.TemporaryDirectory(prefix="rgdmg-") as staging:
        plugin = os.path.join(staging, "Autopilot")
        for folder, dirs, names in os.walk(package):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_NAMES)
            for name in sorted(n for n in names if n not in SKIP_NAMES):
                full = os.path.join(folder, name)
                rel = os.path.relpath(full, package).replace(os.sep, "/")
                if not not_shipped(rel):
                    os.makedirs(os.path.join(plugin, os.path.dirname(rel)), exist_ok=True)
                    shutil.copy2(full, os.path.join(plugin, rel))  # (a Mach-O's signature is in its bytes)
        service = os.path.join(plugin, "AgentService")
        os.makedirs(service, exist_ok=True)
        for name, data, mode in agent_service_files(wheel, commit):
            with open(os.path.join(service, name), "wb") as f:
                f.write(data)
            os.chmod(os.path.join(service, name), mode)
        done = subprocess.run(["hdiutil", "create", "-volname", f"Autopilot for Unreal {engine}", "-srcfolder",
                               staging, "-fs", "HFS+", "-format", "UDZO", "-ov", path], capture_output=True, text=True)
    if done.returncode != 0:
        sys.exit(f"hdiutil couldn't make {path}:\n{(done.stderr or done.stdout).strip()}")
    return path


def sign_dmg(path, identity):
    """--sign on a Mac, before notarize: the disk image signed with the Developer ID and read back (Gatekeeper binds a
    stapled ticket to the image's own signature: an unsigned one is "rejected: no usable signature" even stapled)."""
    if subprocess.run(["codesign", "--force", "--timestamp", "--sign", identity, path]).returncode != 0:
        sys.exit(f"Signing {path} failed: see the lines above.")
    done = subprocess.run(["codesign", "--verify", "--strict", "--verbose=2", path], capture_output=True, text=True)
    if done.returncode != 0:
        sys.exit(f"{path} isn't validly signed after codesign:\n{done.stderr.strip()}")


def staple(path):
    """The notarized disk image's ticket stapled to it (so a Mac needn't ask Apple online), and Gatekeeper's verdict
    on it read back: it has to be accepted as notarized, as a downloader's Mac will judge it."""
    for step in ("staple", "validate"):
        done = subprocess.run(["xcrun", "stapler", step, path], capture_output=True, text=True)
        if done.returncode != 0:
            sys.exit(f"stapler {step} failed for {path}:\n{(done.stderr or done.stdout).strip()}")
    done = subprocess.run(["spctl", "--assess", "--type", "open", "--context", "context:primary-signature",
                           "--verbose=2", path], capture_output=True, text=True)
    verdict = (done.stderr + done.stdout).strip()
    if done.returncode != 0 or "Notarized Developer ID" not in verdict:
        sys.exit(f"Gatekeeper doesn't accept {path} as notarized:\n{verdict}")


def signed_unity_tarball(work, path, wheel, commit):
    """The Unity package packed and signed by Unity's upm (pack, for UNITY_ORGANIZATION) into path: the same files as
    unity_tarball's, from a copy of the package with AgentService~/ in it. Checks the tarball has them all and the
    signature (.attestation.p7m)."""
    folder = os.path.join(work, "unity-package")
    shutil.rmtree(folder, ignore_errors=True)
    shutil.copytree(UNITY, folder, ignore=lambda d, names: [n for n in names if n in SKIP_NAMES
                                                            or (os.path.samefile(d, UNITY) and n in PLUGIN_READMES)])
    os.makedirs(os.path.join(folder, "AgentService~"))
    expected = {"package/package.json"}
    for name, data, mode in agent_service_files(wheel, commit):
        with open(os.path.join(folder, "AgentService~", name), "wb") as f:
            f.write(data)
        os.chmod(os.path.join(folder, "AgentService~", name), mode)
        expected.add(f"package/AgentService~/{name}")
    packed = os.path.join(work, "unity-signed")
    shutil.rmtree(packed, ignore_errors=True)
    if subprocess.run(["upm", "pack", folder, "--organization-id", os.environ["UNITY_ORGANIZATION"],
                       "--destination", packed]).returncode != 0:
        sys.exit("upm pack failed: see the lines above.")
    made = glob.glob(os.path.join(packed, "*.tgz"))
    if len(made) != 1:
        sys.exit(f"upm pack made {made or 'no tarball'}, not one tarball")
    with tarfile.open(made[0], "r:gz") as tar:
        names = set(tar.getnames())
    missing = sorted(expected - names)
    if missing or not any(n.endswith(".attestation.p7m") for n in names):
        sys.exit(f"upm pack's tarball is missing {', '.join(missing) or 'its signature (.attestation.p7m)'}")
    os.replace(made[0], path)
    return path


def unity_tarball(work, plugin_version, wheel, commit, sign=False):
    """The Unity package as Package Manager takes a tarball: its files under package/, and the agent service's
    installer in package/AgentService~/ (Unity skips folders whose names end in ~). sign: packed and signed by upm
    (signed_unity_tarball), so Unity 6.3 and later don't flag it as unsigned."""
    with open(os.path.join(UNITY, "package.json"), encoding="utf-8") as f:
        version = json.load(f)["version"]
    path = os.path.join(work, f"com.ramenvr.autopilot-{version}.tgz")
    if sign:
        return signed_unity_tarball(work, path, wheel, commit)
    with tarfile.open(path, "w:gz") as tar:
        for folder, dirs, names in os.walk(UNITY):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_NAMES)
            for name in sorted(names):
                full = os.path.join(folder, name)
                rel = os.path.relpath(full, UNITY).replace(os.sep, "/")
                if rel not in PLUGIN_READMES:
                    tar.add(full, f"package/{rel}", recursive=False)
        for name, data, mode in agent_service_files(wheel, commit):
            info = tarfile.TarInfo(f"package/AgentService~/{name}")
            info.size, info.mode, info.mtime = len(data), mode, 1767225600
            tar.addfile(info, io.BytesIO(data))
    return path


def unity_zip(out, wheel, plugin_version, stem, commit, work, sign=False):
    tgz = unity_tarball(work, plugin_version, wheel, commit, sign)
    path = os.path.join(out, f"{stem}-Unity.zip")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        z.write(tgz, os.path.basename(tgz))
    return path


def godot_zip(out, wheel, stem, commit):
    path = os.path.join(out, f"{stem}-Godot.zip")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        add_tree(z, GODOT, "addons/autopilot")
        add_agent_service(z, wheel, "addons/autopilot/agent_service", commit)
        z.writestr("addons/autopilot/agent_service/.gdignore", "", zipfile.ZIP_DEFLATED)  # (not game assets)
    return path


def installed_unreals(epic):
    found = []
    for folder in glob.glob(os.path.join(epic, "UE_5.*")):
        version = os.path.basename(folder)[3:]
        if os.path.exists(os.path.join(folder, "Engine", "Build", "BatchFiles", RUNUAT)):
            found.append(version)
    return sorted(found, key=lambda v: tuple(int(x) for x in v.split(".")))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build Autopilot downloads for users (no repository needed).")
    ap.add_argument("out")
    ap.add_argument("--unreal", help="the Unreal versions to build for, e.g. 5.6,5.7,5.8 (default: those installed)")
    ap.add_argument("--no-unreal", action="store_true", help="only the Unity and Godot downloads")
    ap.add_argument("--only-unreal", action="store_true", help="only the Unreal downloads (no Unity or Godot zip)")
    ap.add_argument("--epic", default=EPIC, help=f"where Unreal engines are installed (default: {EPIC})")
    ap.add_argument("--engine-dir", action="append", default=[],
                    help="an Unreal engine's folder to build for too, e.g. one built from source (C:/UnrealEngine); "
                         "may be given more than once; without --unreal, only these")
    ap.add_argument("--aura-server", help="the Aura server the downloads use (AUTOPILOT_AURA_SERVER in autopilot.env)")
    ap.add_argument("--unversioned", action="store_true",
                    help="name the zips Autopilot-<engine>.zip, without the version (a release's: tools/release.py)")
    ap.add_argument("--sign", action="store_true",
                    help="sign the Unreal plugin's binaries (and notarize the Mac disk images; see the top)")
    ap.add_argument("--no-upm", action="store_true",
                    help="with --sign: leave out the Unity package's upm signature (when its service account isn't "
                         "set up; Unity 6.3 and later flag the package as unsigned)")
    args = ap.parse_args(argv)
    if args.no_upm and not args.sign:
        sys.exit("--no-upm goes with --sign")
    if args.no_unreal and args.only_unreal:
        sys.exit("--no-unreal and --only-unreal leave nothing to build: give one of them")
    if args.aura_server:
        if not re.match(r"^https://[A-Za-z0-9.-]+(:\d+)?/?$", args.aura_server):
            sys.exit(f"--aura-server must be an https:// server's root, not {args.aura_server!r}")
        SETTINGS["AUTOPILOT_AURA_SERVER"] = args.aura_server.rstrip("/")
    commit, epoch, built_from = source()
    _, plugin_version = versions()
    stem = "Autopilot" if args.unversioned else f"Autopilot-{plugin_version}"
    engines = []  # (x.y, the engine's folder): checked before anything is built
    if not args.no_unreal:
        if args.unreal or not args.engine_dir:
            for engine in (args.unreal.split(",") if args.unreal else installed_unreals(args.epic)):
                engines.append((engine.strip(), os.path.join(args.epic, f"UE_{engine.strip()}")))
        for folder in args.engine_dir:
            root = engine_root(folder)
            engines.append((engine_version(root), root))
    if MAC:
        for engine, root in engines:
            xcode_for(engine, root)  # (each has an Xcode it builds with)
    identity = signer(bool(engines), not args.only_unreal, not args.no_upm) if args.sign else None
    out = os.path.abspath(args.out)
    os.makedirs(out, exist_ok=True)
    made = []
    # (its real path: Unreal's build accelerator loses files under a symlink, such as macOS's /var -> /private/var)
    work = os.path.realpath(tempfile.mkdtemp(prefix="rgpkg-", dir=os.environ.get("RG_WORK") or None))
    wheel = build_wheel(os.path.join(work, "wheel"), commit, epoch)
    if args.sign and PLATFORM == "Win64":
        sign_installer(work)
    if not args.only_unreal:
        made.append(unity_zip(out, wheel, plugin_version, stem, commit, work, args.sign and not args.no_upm))
        made.append(godot_zip(out, wheel, stem, commit))
    for engine, root in engines:
        print(f"Building the plugin for Unreal {engine} ({root})...")
        package = build_unreal(engine, root, work)
        if args.sign:
            sign_binaries(package, identity)
        download = unreal_dmg if PLATFORM == "Mac" else unreal_zip
        made.append(download(out, engine, package, wheel, stem, commit, not epics_own_build(root)))
        if args.sign and PLATFORM == "Mac":
            sign_dmg(made[-1], identity)
            notarize(made[-1])
            staple(made[-1])
    shutil.rmtree(work, ignore_errors=True)  # (kept when a build fails: its message names its log there)
    with open(os.path.join(out, "README.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write(readme(plugin_version, built_from, stem))
    with open(os.path.join(out, "SHA256SUMS.txt"), "w", encoding="utf-8", newline="\n") as f:
        for path in made:
            f.write(f"{sha256(path)}  {os.path.basename(path)}\n")
    for path in made:
        print(f"{os.path.getsize(path) / 1e6:8.1f} MB  {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
