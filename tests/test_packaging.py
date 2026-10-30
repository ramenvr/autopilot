"""Downloads for users without this repository (tools/package_release.py) and where an installed Autopilot keeps its
memory (autopilot/codex.py)."""

import json
import os
import plistlib
import sys
import tarfile
import zipfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import package_release  # noqa: E402
import release  # noqa: E402

from autopilot import bridge, codex  # noqa: E402


def test_a_checkout_keeps_memory_in_it_and_an_install_in_the_user_settings_folder(tmp_path):
    checkout = tmp_path / "autopilot"
    (checkout / "autopilot").mkdir(parents=True)
    (checkout / "pyproject.toml").write_text("")
    assert codex.default_memory(str(checkout)) == str(checkout / "memory")
    assert codex.default_memory(str(tmp_path / "site-packages")) == bridge.brain_file("memory")


def on(monkeypatch, mac):
    """Unreal builds are for the platform package_release runs on: as if it ran on macOS (mac) or Windows."""
    monkeypatch.setattr(package_release, "PLATFORM", "Mac" if mac else "Win64")
    monkeypatch.setattr(package_release, "RUNUAT", "RunUAT.sh" if mac else "RunUAT.bat")


@pytest.fixture(autouse=True)
def on_windows(monkeypatch):
    on(monkeypatch, mac=False)


def wheel(tmp_path):
    path = tmp_path / "autopilot-0.1.0-py3-none-any.whl"
    path.write_bytes(b"wheel")
    return str(path)


def test_the_godot_download_has_the_addon_the_agent_service_and_how_to_install(tmp_path):
    path = package_release.godot_zip(str(tmp_path), wheel(tmp_path), "Autopilot-1.0.0", "abc123")
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        sh = z.read("addons/autopilot/agent_service/install.sh")
    assert "addons/autopilot/plugin.cfg" in names
    assert not any("/tests/" in n or "__pycache__" in n for n in names)
    assert {"addons/autopilot/agent_service/autopilot-0.1.0-py3-none-any.whl",
            "addons/autopilot/agent_service/install.ps1", "addons/autopilot/agent_service/install.sh",
            "addons/autopilot/agent_service/.gdignore"} <= set(names)
    assert b"\r\n" not in sh
    assert not any("readme" in n.lower() for n in names)  # (the one README is beside the zips)


def test_every_download_says_which_agent_service_it_carries(tmp_path):
    """agent-service.json beside the installer: the wheel's hash and commit (the installer keeps a copy; a plugin whose
    copy differs from the installed one installs its own)."""
    stamp = {"sha256": package_release.sha256(wheel(tmp_path)), "commit": "abc123",
             "wheel": "autopilot-0.1.0-py3-none-any.whl"}
    with zipfile.ZipFile(package_release.godot_zip(str(tmp_path), wheel(tmp_path), "Autopilot-1.0.0", "abc123")) as z:
        assert json.loads(z.read("addons/autopilot/agent_service/agent-service.json")) == stamp
    tgz = package_release.unity_tarball(str(tmp_path), "1.0.0", wheel(tmp_path), "abc123")
    with tarfile.open(tgz) as tar:
        assert json.load(tar.extractfile("package/AgentService~/agent-service.json")) == stamp
    for script in ("install.ps1", "install.sh"):  # (the installers keep it once the wheel is installed)
        with open(os.path.join(package_release.STANDALONE, script), encoding="utf-8") as f:
            assert "agent-service.json" in f.read()


def test_downloads_for_another_aura_server_are_another_wheel(tmp_path, monkeypatch):
    """build.json in the wheel has the settings the download carries: two builds of one commit for two Aura servers
    were one wheel, so a plugin found the other's installed already, never ran its installer and kept the old server."""
    run = package_release.subprocess.run
    stamps = []

    def fake_run(args, **kw):
        if args[0] != "uv":
            return run(args, **kw)
        with open(os.path.join(package_release.HERE, "autopilot", "build.json"), encoding="utf-8") as f:
            stamps.append(json.load(f))
        out = args[args.index("--out-dir") + 1]
        os.makedirs(out, exist_ok=True)
        with open(os.path.join(out, "autopilot-0.1.0-py3-none-any.whl"), "wb") as f:
            f.write(b"wheel")

    monkeypatch.setattr(package_release.subprocess, "run", fake_run)
    monkeypatch.setattr(package_release, "SETTINGS", {})
    package_release.build_wheel(str(tmp_path / "a"), "abc123", 1767225600)
    monkeypatch.setattr(package_release, "SETTINGS", {"AUTOPILOT_AURA_SERVER": "https://preview.aura.test"})
    package_release.build_wheel(str(tmp_path / "b"), "abc123", 1767225600)
    assert stamps == [{"commit": "abc123"},
                      {"commit": "abc123", "settings": {"AUTOPILOT_AURA_SERVER": "https://preview.aura.test"}}]
    for script in ("install.ps1", "install.sh"):  # (the installers save the download's settings over the old ones)
        with open(os.path.join(package_release.STANDALONE, script), encoding="utf-8") as f:
            assert "account.apply_download_settings" in f.read()


def test_the_unity_download_is_a_tarball_package_manager_takes(tmp_path):
    with open(os.path.join(package_release.UNITY, "package.json"), encoding="utf-8") as f:
        version = json.load(f)["version"]  # (the tarball is named for the package's version, not the download's)
    path = package_release.unity_zip(str(tmp_path), wheel(tmp_path), "1.0.0", "Autopilot-1.0.0", "abc123",
                                     str(tmp_path))
    with zipfile.ZipFile(path) as z:
        tgz = [n for n in z.namelist() if n.endswith(".tgz")]
        assert z.namelist() == tgz == [f"com.ramenvr.autopilot-{version}.tgz"]  # (no README: it is beside the zips)
    with tarfile.open(tmp_path / tgz[0]) as tar:
        names = tar.getnames()
    assert "package/package.json" in names and all(n.startswith("package/") for n in names)
    assert "package/README.md" not in names and "package/README.md.meta" not in names  # (the download's has the setup)
    assert {"package/AgentService~/install.ps1", "package/AgentService~/autopilot-0.1.0-py3-none-any.whl"} <= set(names)


def test_the_unreal_download_leaves_out_debug_symbols(tmp_path):
    built = tmp_path / "ue58"
    (built / "Binaries" / "Win64").mkdir(parents=True)
    (built / "Binaries" / "Win64" / "UnrealEditor-AutopilotPlaytest.dll").write_bytes(b"dll")
    (built / "Binaries" / "Win64" / "UnrealEditor-AutopilotPlaytest.pdb").write_bytes(b"pdb")
    (built / "Autopilot.uplugin").write_text("{}")
    (built / "README.md").write_text("the plugin's own")
    path = package_release.unreal_zip(str(tmp_path), "5.8", str(built), wheel(tmp_path), "Autopilot-1.0.0", "abc123")
    assert os.path.basename(path) == "Autopilot-1.0.0-Unreal5.8-Win64.zip"
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
    assert "Autopilot/Binaries/Win64/UnrealEditor-AutopilotPlaytest.dll" in names
    assert "Autopilot/AgentService/install.ps1" in names
    assert not any(n.endswith(".pdb") for n in names)
    assert not any("readme" in n.lower() for n in names)  # (neither the plugin's own nor the download's)


def test_on_macos_the_unreal_download_is_for_mac_and_leaves_out_debug_symbols(tmp_path, monkeypatch):
    on(monkeypatch, mac=True)
    built = tmp_path / "ue58"
    dsym = built / "Binaries" / "Mac" / "UnrealEditor-AutopilotPlaytest.dylib.dSYM" / "Contents"
    dsym.mkdir(parents=True)
    (dsym / "Info.plist").write_text("plist")
    (built / "Binaries" / "Mac" / "UnrealEditor-AutopilotPlaytest.dylib").write_bytes(b"dylib")
    (built / "Autopilot.uplugin").write_text("{}")
    (built / "README.md").write_text("the plugin's own")
    # A disk image, not a zip (a zip can't carry a stapled ticket): hdiutil makes it from a folder with Autopilot/ in it
    staged = {}

    def hdiutil(args):
        folder = args[args.index("-srcfolder") + 1]
        for root, _, files in os.walk(folder):
            for name in files:
                full = os.path.join(root, name)
                staged[os.path.relpath(full, folder).replace(os.sep, "/")] = os.stat(full).st_mode & 0o777
        return 0, ""
    calls = fake_tools(monkeypatch, hdiutil)
    path = package_release.unreal_dmg(str(tmp_path), "5.8", str(built), wheel(tmp_path), "Autopilot-1.0.0", "abc123")
    assert os.path.basename(path) == "Autopilot-1.0.0-Unreal5.8-Mac.dmg"
    assert calls[0][:2] == ["hdiutil", "create"] and calls[0][-1] == path
    assert "Autopilot/Binaries/Mac/UnrealEditor-AutopilotPlaytest.dylib" in staged
    assert staged["Autopilot/AgentService/install.sh"] == 0o755
    assert not any(".dSYM" in n or "readme" in n.lower() for n in staged)
    fake_tools(monkeypatch, lambda args: (1, ""))
    with pytest.raises(SystemExit, match="hdiutil couldn't make"):
        package_release.unreal_dmg(str(tmp_path), "5.8", str(built), wheel(tmp_path), "Autopilot-1.0.0", "abc123")
    launcher = fake_engine(tmp_path / "UE_5.8", minor=8, promoted=True, installed=True)
    args, _ = built_with(monkeypatch, "5.8", launcher, tmp_path)
    assert args[0] == os.path.join(launcher, "Engine", "Build", "BatchFiles", "RunUAT.sh")
    assert "-TargetPlatforms=Mac" in args and "-Rocket" in args


def fake_xcode(applications, name, version):
    contents = applications / name / "Contents"
    (contents / "Developer").mkdir(parents=True)
    with open(contents / "version.plist", "wb") as f:
        plistlib.dump({"CFBundleShortVersionString": version}, f)
    return str(contents / "Developer")


def test_on_macos_each_engine_builds_with_the_newest_xcode_it_accepts(tmp_path, monkeypatch):
    """Epic's engines refuse an Xcode newer than their Apple_SDK.json allows ("Platform Mac is not a valid platform
    to build"), so each builds with its own, through DEVELOPER_DIR."""
    on(monkeypatch, mac=True)
    monkeypatch.setattr(package_release, "MAC", True)
    applications = tmp_path / "Applications"
    xcode163 = fake_xcode(applications, "Xcode-16.3.0.app", "16.3")
    xcode16 = fake_xcode(applications, "Xcode-16.4.0.app", "16.4")
    xcode27 = fake_xcode(applications, "Xcode.app", "27.1")
    (applications / "Xcode-broken.app").mkdir()  # (no version.plist: not an Xcode)
    xcodes = package_release.installed_xcodes(str(applications))
    assert xcodes == {(16, 3): xcode163, (16, 4): xcode16, (27, 1): xcode27}
    monkeypatch.setattr(package_release, "installed_xcodes", lambda: xcodes)
    ue54 = fake_engine(tmp_path / "UE_5.4", minor=4, promoted=True, installed=True)
    sdk = tmp_path / "UE_5.4" / "Engine" / "Config" / "Apple"
    sdk.mkdir(parents=True)
    (sdk / "Apple_SDK.json").write_text('{\n\t"//1": "Xcode versions:",\n\t\t"MainVersion": "14.1",\n'
                                        '\t\t"MinVersion": "14.1.0",\n\t\t"MaxVersion": "16.9.0",\n'
                                        '\t// NOTE: a comment\n}\n')
    assert package_release.xcode_range(ue54) == ((14, 1, 0), (16, 9, 0))
    assert package_release.xcode_for("5.4", ue54) == xcode16
    calls = []
    monkeypatch.setattr(package_release.subprocess, "run",
                        lambda args, **kw: calls.append(kw) or package_release.subprocess.CompletedProcess(args, 0))
    package_release.build_unreal("5.4", ue54, str(tmp_path))
    assert calls[0]["env"]["DEVELOPER_DIR"] == xcode16
    (sdk / "Apple_SDK.json").write_text('{"MinVersion": "15.2.0", "MaxVersion": "15.9.0"}')
    with pytest.raises(SystemExit, match=r"Xcode 15\.2\.0 to 15\.9\.0.*there: 16\.3, 16\.4, 27\.1"):
        package_release.xcode_for("5.4", ue54)
    assert package_release.xcode_for("5.8", fake_engine(tmp_path / "UE_5.8", minor=8)) is None  # (no Apple_SDK.json)


def fake_engine(folder, minor=4, promoted=False, installed=False, bom=False):
    """An Unreal engine's folder as package_release reads it: Build.version, RunUAT (.bat, .sh), InstalledBuild.txt."""
    build = folder / "Engine" / "Build"
    (build / "BatchFiles").mkdir(parents=True)
    for runuat in ("RunUAT.bat", "RunUAT.sh"):
        (build / "BatchFiles" / runuat).write_text("")
    version = {"MajorVersion": 5, "MinorVersion": minor, "PatchVersion": 2, "IsPromotedBuild": int(promoted)}
    (build / "Build.version").write_text(("﻿" if bom else "") + json.dumps(version, indent="\t"), encoding="utf-8")
    if installed:
        (build / "InstalledBuild.txt").write_text("")
    return str(folder)


def built_with(monkeypatch, engine, root, tmp_path):
    """BuildPlugin's arguments when the plugin is built for the engine in root (RunUAT stubbed)."""
    calls = []

    def fake_run(args, **kw):
        calls.append(args)
        return package_release.subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(package_release.subprocess, "run", fake_run)
    package = package_release.build_unreal(engine, root, str(tmp_path))
    return calls[0], package


def test_any_engine_folder_names_its_version_and_whether_it_is_epics_own(tmp_path):
    source = fake_engine(tmp_path / "UnrealEngine", minor=4, bom=True)  # (an edited Build.version may have a BOM)
    assert package_release.engine_version(source) == "5.4"
    assert package_release.engine_root(os.path.join(source, "Engine")) == source  # (its Engine/ folder works too)
    assert package_release.engine_root(source) == source
    assert not package_release.epics_own_build(source) and package_release.source_tree(source)
    studio = fake_engine(tmp_path / "StudioInstalled", minor=3, installed=True)  # (an installed build from source)
    assert not package_release.epics_own_build(studio) and not package_release.source_tree(studio)
    launcher = fake_engine(tmp_path / "UE_5.8", minor=8, promoted=True, installed=True)
    assert package_release.epics_own_build(launcher) and not package_release.source_tree(launcher)


def test_a_folder_without_an_engine_is_refused_before_anything_is_built(tmp_path):
    with pytest.raises(SystemExit, match="isn't an Unreal engine's folder"):
        package_release.engine_version(str(tmp_path))


def test_a_source_tree_builds_the_editor_only_and_epics_engines_build_the_game_too(tmp_path, monkeypatch):
    """A source tree's BuildPlugin would compile the whole engine for UnrealGame twice: its games compile the
    plugin's Source/ themselves, so only the editor's binaries are built."""
    source = fake_engine(tmp_path / "UnrealEngine")
    args, package = built_with(monkeypatch, "5.4", source, tmp_path)
    assert args[0] == os.path.join(source, "Engine", "Build", "BatchFiles", "RunUAT.bat")
    assert "-NoTargetPlatforms" in args and "-Rocket" not in args
    assert not any(a.startswith("-TargetPlatforms=") for a in args)
    assert os.path.basename(package) == "ue54src"
    launcher = fake_engine(tmp_path / "UE_5.8", minor=8, promoted=True, installed=True)
    args, package = built_with(monkeypatch, "5.8", launcher, tmp_path)
    assert "-TargetPlatforms=Win64" in args and "-Rocket" in args and "-NoTargetPlatforms" not in args
    assert os.path.basename(package) == "ue58"
    studio = fake_engine(tmp_path / "StudioInstalled", minor=4, installed=True)  # (has the game's libraries)
    args, package = built_with(monkeypatch, "5.4", studio, tmp_path)
    assert "-TargetPlatforms=Win64" in args and os.path.basename(package) == "ue54src"


def test_the_built_plugin_names_no_engine_version_so_every_patch_loads_it_without_asking(tmp_path, monkeypatch):
    """BuildPlugin stamps its engine's version ("5.4.0") into the .uplugin: an editor of another patch (5.4.4), or a
    studio's own build, then asks its user whether to load the plugin. The download leaves it out."""
    launcher = fake_engine(tmp_path / "UE_5.4", minor=4, promoted=True, installed=True)
    stamped = {"FileVersion": 3, "VersionName": "1.2.3", "EngineVersion": "5.4.0", "Modules": [{"Name": "A"}]}

    def fake_run(args, **kw):
        package = next(a for a in args if a.startswith("-Package="))[len("-Package="):]
        os.makedirs(package)
        with open(os.path.join(package, "Autopilot.uplugin"), "w", encoding="utf-8") as f:
            json.dump(stamped, f)
        return package_release.subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(package_release.subprocess, "run", fake_run)
    package = package_release.build_unreal("5.4", launcher, str(tmp_path))
    with open(os.path.join(package, "Autopilot.uplugin"), encoding="utf-8") as f:
        descriptor = json.load(f)
    assert descriptor == {k: v for k, v in stamped.items() if k != "EngineVersion"}


def test_a_download_for_an_engine_that_isnt_epics_says_so_in_its_name(tmp_path):
    built = tmp_path / "ue54src"
    built.mkdir()
    (built / "Autopilot.uplugin").write_text("{}")
    path = package_release.unreal_zip(str(tmp_path), "5.4", str(built), wheel(tmp_path), "Autopilot-1.0.0", "abc123",
                                      True)
    assert os.path.basename(path) == "Autopilot-1.0.0-Unreal5.4-Source-Win64.zip"


def test_engine_dir_builds_for_that_engine_only_unless_unreal_names_more(tmp_path, monkeypatch):
    source = fake_engine(tmp_path / "UnrealEngine")
    epic = tmp_path / "Epic Games"
    fake_engine(epic / "UE_5.8", minor=8, promoted=True, installed=True)
    built = []
    monkeypatch.setattr(package_release, "source", lambda: ("abc123", 1767225600, "commit abc123"))
    monkeypatch.setattr(package_release, "build_wheel", lambda out, commit, epoch: wheel(tmp_path))
    monkeypatch.setattr(package_release, "build_unreal",
                        lambda engine, root, work: built.append((engine, root)) or work)
    monkeypatch.setattr(package_release, "unreal_zip", lambda out, engine, *a: str(tmp_path / "x.zip"))
    monkeypatch.setattr(package_release, "unity_zip", lambda *a: str(tmp_path / "x.zip"))
    monkeypatch.setattr(package_release, "godot_zip", lambda *a: str(tmp_path / "x.zip"))
    (tmp_path / "x.zip").write_bytes(b"zip")
    out = str(tmp_path / "out")
    package_release.main([out, "--epic", str(epic), "--engine-dir", os.path.join(source, "Engine")])
    assert built == [("5.4", source)]
    built.clear()
    package_release.main([out, "--epic", str(epic), "--engine-dir", source, "--unreal", "5.8"])
    assert built == [("5.8", str(epic / "UE_5.8")), ("5.4", source)]
    built.clear()
    package_release.main([out, "--epic", str(epic), "--engine-dir", source, "--no-unreal"])
    assert built == []


def test_only_unreal_makes_no_unity_or_godot_download(tmp_path, monkeypatch):
    """tools/build_unreal.ps1 builds for one Unreal engine: the Unity and Godot zips would only be clutter beside it."""
    source = fake_engine(tmp_path / "UnrealEngine")
    made = []
    monkeypatch.setattr(package_release, "source", lambda: ("abc123", 1767225600, "commit abc123"))
    monkeypatch.setattr(package_release, "build_wheel", lambda out, commit, epoch: wheel(tmp_path))
    monkeypatch.setattr(package_release, "build_unreal", lambda engine, root, work: work)
    (tmp_path / "x.zip").write_bytes(b"zip")
    for name in ("unreal_zip", "unity_zip", "godot_zip"):
        monkeypatch.setattr(package_release, name, lambda *a, name=name: made.append(name) or str(tmp_path / "x.zip"))
    package_release.main([str(tmp_path / "out"), "--engine-dir", source, "--only-unreal"])
    assert made == ["unreal_zip"]
    made.clear()
    package_release.main([str(tmp_path / "out"), "--engine-dir", source])
    assert made == ["unity_zip", "godot_zip", "unreal_zip"]
    with pytest.raises(SystemExit, match="leave nothing to build"):
        package_release.main([str(tmp_path / "out"), "--no-unreal", "--only-unreal"])
    with open(os.path.join(package_release.HERE, "tools", "build_unreal.ps1"), encoding="utf-8") as f:
        assert '"--only-unreal"' in f.read()


def test_unversioned_zips_have_the_same_names_in_every_release(tmp_path, monkeypatch):
    """tools/release.py's --unversioned: Autopilot-<engine>.zip in every release, so a link to the newest release's
    download never changes and a published (immutable) release is never renamed; the README's table names those
    files, its title the version."""
    out = str(tmp_path / "out")
    monkeypatch.setattr(package_release, "source", lambda: ("abc123", 1767225600, "commit abc123"))
    monkeypatch.setattr(package_release, "build_wheel", lambda out, commit, epoch: wheel(tmp_path))
    package_release.main([out, "--no-unreal", "--unversioned"])
    assert sorted(os.listdir(out)) == ["Autopilot-Godot.zip", "Autopilot-Unity.zip", "README.md", "SHA256SUMS.txt"]
    with open(os.path.join(out, "SHA256SUMS.txt"), encoding="utf-8") as f:
        assert [line.split("  ")[1] for line in f.read().splitlines()] == ["Autopilot-Unity.zip", "Autopilot-Godot.zip"]
    readme = package_release.readme("1.0.0", "commit abc123", "Autopilot")
    assert "Autopilot 1.0.0" in readme and "`Autopilot-Unity.zip`" in readme and "Autopilot-1.0.0-" not in readme
    assert "`Autopilot-1.0.0-Unity.zip`" in package_release.readme("1.0.0", "commit abc123")


def release_(tag, draft, files, created="2026-10-29T10:00:00Z"):
    return {"tag_name": tag, "draft": draft, "created_at": created, "assets": [{"name": n} for n in files]}


def test_a_release_is_published_when_windows_and_the_mac_have_both_built_their_half():
    """tools/release.py: Windows builds Unreal for Windows, Unity and Godot, a Mac builds Unreal for Mac, into one
    draft (a published release can't be added to); the run that completes it publishes it."""
    windows = ["Autopilot-Unreal5.8-Win64.zip", "Autopilot-Unity.zip", "Autopilot-Godot.zip", "README.md",
               "SHA256SUMS.txt"]
    mac = ["Autopilot-Unreal5.8-Mac.dmg", "README.md", "SHA256SUMS.txt"]
    assert [release.half(n) for n in windows] == ["Win64", "Win64", "Win64", None, None]
    assert release.half("Autopilot-Unreal5.8-Source-Mac.dmg") == "Mac"
    assert release.half("Autopilot-Unreal5.8-Mac.zip") == "Mac"  # (a draft made before the Mac downloads were dmgs)
    assert release.complete(windows + ["Autopilot-Unreal5.8-Mac.zip"])
    assert not release.complete(windows) and not release.complete(mac) and release.complete(windows + mac)
    # The Mac's run builds the commit of the draft Windows made, even when main has moved on since
    older = release_("v1.0.0-aaaaaaa", True, windows, "2026-10-29T09:00:00Z")
    assert release.waiting([older], "Mac") is older and release.waiting([older], "Win64") is None
    newer = release_("v1.0.0-bbbbbbb", True, mac, "2026-10-29T11:00:00Z")
    assert release.waiting([older, newer], "Win64") is newer and release.waiting([older, newer], "Mac") is None
    assert release.waiting([release_("v1.0.0-ccccccc", False, windows)], "Mac") is None  # (published: not a draft)
    assert release.waiting([release_("my-draft", True, [])], "Mac") is None  # (not this script's)
    respin = release_("v1.0.0-bbbbbbb.2", True, windows, "2026-10-29T12:00:00Z")
    assert release.waiting([older, newer, respin], "Mac") is respin
    # SHA256SUMS.txt lists both halves' zips; a rerun's lines take its file's place
    theirs = "aa  Autopilot-Unity.zip\nbb  Autopilot-Unreal5.8-Win64.zip\n"
    mac_sums = "cc  Autopilot-Unreal5.8-Mac.dmg\n"
    assert release.merged_sums(theirs, mac_sums) == theirs + mac_sums
    assert release.merged_sums(theirs, "dd  Autopilot-Unity.zip\n") == ("dd  Autopilot-Unity.zip\n"
                                                                         "bb  Autopilot-Unreal5.8-Win64.zip\n")
    with open(os.path.join(package_release.HERE, "tools", "release.py"), encoding="utf-8") as f:
        script = f.read()
    assert '"--unversioned"' in script and '"--only-unreal"' in script and "Autopilot-latest-" not in script
    assert '"--sign"' in script  # (a published release can't be signed after)


WINDOWS_HALF = ["Autopilot-Unity.zip", "Autopilot-Godot.zip", "Autopilot-Unreal5.8-Win64.zip", "README.md",
                "SHA256SUMS.txt"]
MAC_HALF = ["Autopilot-Unreal5.8-Mac.dmg", "README.md", "SHA256SUMS.txt"]


def fake_release_tools(tmp_path, monkeypatch, platform, releases, draft_has):
    """git, gh and package_release.py as release.py calls them, on Windows or a Mac (platform, as sys.platform): GitHub
    has releases, origin/main is bbbbbbb (1.0.0), the build makes the platform's half and the draft has draft_has
    after; the calls made, and the notes files gh was given."""
    calls, notes = [], []

    def run(args, **kw):
        args = list(args)  # (release.py's run and read pass tuples)
        calls.append(args)
        out = ""
        if args[:2] == ["gh", "api"] and args[2].endswith("/releases?per_page=100"):
            out = json.dumps(releases)
        elif args[:2] == ["gh", "api"]:
            out = "true\n"  # (admin)
        elif args[:2] == ["git", "-C"] and args[3:5] == ["remote", "get-url"]:
            out = "https://github.com/ramenvr/autopilot.git\n"
        elif args[:2] == ["git", "-C"] and args[3] == "show":
            out = json.dumps({"VersionName": "1.0.0"})
        elif args[:2] == ["git", "-C"] and args[3] == "rev-parse":
            out = "b" * 40 + "\n"
        elif any(str(a).endswith("package_release.py") for a in args):
            folder = args[args.index("--unreal") - 1]
            os.makedirs(folder)
            for name in WINDOWS_HALF if platform == "win32" else MAC_HALF:
                (tmp_path / folder / name).write_text(name)
            (tmp_path / folder / "SHA256SUMS.txt").write_text("aa  Autopilot-Unity.zip\n")
        elif args[:2] == ["gh", "release"] and "--notes-file" in args:
            with open(args[args.index("--notes-file") + 1], encoding="utf-8") as f:
                notes.append(f.read())
        elif args[:3] == ["gh", "release", "view"]:
            out = json.dumps({"assets": [{"name": n} for n in draft_has]})
        return release.subprocess.CompletedProcess(args, 0, out, "")
    monkeypatch.setattr(release.subprocess, "run", run)
    monkeypatch.setattr(release.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(release.sys, "platform", platform)
    return calls, notes


def test_no_upm_releases_with_the_unity_package_unsigned_and_the_notes_say_so(tmp_path, monkeypatch):
    """release.py --no-upm (no Unity service account): the Windows half builds with package_release.py --no-upm, and
    the release's notes don't say the Unity package is signed, even when the Mac half wrote them."""
    commit = "b" * 40
    tag = "v1.0.0-bbbbbbb"
    mac_notes = f"Autopilot 1.0.0, built from {commit}. {release.SIGNED} The downloads use Aura.\n\nChanges"
    drafts = [{"id": 1, "tag_name": tag, "draft": True, "prerelease": False, "created_at": "2026-10-29T11:00:00Z",
               "target_commitish": commit, "body": mac_notes, "assets": [{"name": "Autopilot-Unreal5.8-Mac.zip"}]}]
    calls, edited = fake_release_tools(tmp_path, monkeypatch, "win32", drafts, WINDOWS_HALF + MAC_HALF)
    release.main(["--no-upm", "--unreal", "5.8", "--work-dir", str(tmp_path)])
    build = next(c for c in calls if any(str(a).endswith("package_release.py") for a in c))
    assert "--sign" in build and "--no-upm" in build
    assert edited == [mac_notes.replace(release.SIGNED, release.UNSIGNED_UNITY)]
    assert "isn't signed" in edited[0] and release.SIGNED not in edited[0]
    assert ["gh", "release", "edit", tag, "-R", release.GITHUB, "--draft=false", "--latest"] in calls  # (published)
    # Without --no-upm the notes stay as they are; and the Mac half has no Unity package to leave unsigned
    assert release.unsigned_unity("Notes without the sentence") is None
    monkeypatch.setattr(release.sys, "platform", "darwin")
    with pytest.raises(SystemExit, match="--no-upm is for the Windows half"):
        release.main(["--no-upm"])


def test_respin_releases_a_released_commit_again_under_a_tag_of_its_own(tmp_path, monkeypatch, capsys):
    """release.py --respin N: a commit released already (its tag can't be used again, even once deleted) is released
    again as v<version>-<commit>.<N>; the other half's run finishes that draft without the flag."""
    commit = "b" * 40
    released = {"id": 1, "tag_name": "v1.0.0-bbbbbbb", "draft": False, "prerelease": False,
                "created_at": "2026-10-29T09:00:00Z", "target_commitish": commit, "body": "", "assets": []}
    args = ["--unreal", "5.8", "--work-dir", str(tmp_path)]
    # Without it a released commit isn't redone, and the run says how to
    calls, _ = fake_release_tools(tmp_path, monkeypatch, "win32", [released], [])
    release.main(args)
    assert "run this with --respin 2" in capsys.readouterr().out
    assert not any(c[:3] == ["gh", "release", "create"] for c in calls)
    # Windows' half makes the respin's draft
    calls, notes = fake_release_tools(tmp_path, monkeypatch, "win32", [released], WINDOWS_HALF)
    release.main(args + ["--respin", "2"])
    create = next(c for c in calls if c[:3] == ["gh", "release", "create"])
    assert create[3] == "v1.0.0-bbbbbbb.2" and create[create.index("--target") + 1] == commit
    assert create[create.index("--title") + 1] == "Autopilot 1.0.0 (bbbbbbb.2)"
    assert "A respin of v1.0.0-bbbbbbb: the same commit, built again." in notes[0]
    assert not any("--draft=false" in c for c in calls)  # (it waits for the Mac half)
    # The Mac's run, without --respin, finishes and publishes it
    draft = {**released, "id": 2, "tag_name": "v1.0.0-bbbbbbb.2", "draft": True, "created_at": "2026-10-29T11:00:00Z",
             "assets": [{"name": n} for n in WINDOWS_HALF if n != "SHA256SUMS.txt"]}
    assert release.waiting([draft, released], "Mac") is draft
    calls, _ = fake_release_tools(tmp_path, monkeypatch, "darwin", [draft, released], WINDOWS_HALF + MAC_HALF)
    release.main(args)
    assert next(c for c in calls if c[:3] == ["gh", "release", "upload"])[3] == "v1.0.0-bbbbbbb.2"
    assert ["gh", "release", "edit", "v1.0.0-bbbbbbb.2", "-R", release.GITHUB, "--draft=false", "--latest"] in calls
    assert not any(c[:3] == ["gh", "api", "-X"] for c in calls)  # (nothing deleted)
    # A respin released already isn't redone either: the run names the next
    fake_release_tools(tmp_path, monkeypatch, "win32", [{**draft, "draft": False}, released], [])
    release.main(args + ["--respin", "2"])
    assert "run this with --respin 3" in capsys.readouterr().out
    # A draft waiting for the other half is finished first, and a commit's first release is the one without --respin
    fake_release_tools(tmp_path, monkeypatch, "darwin", [draft, released], [])
    with pytest.raises(SystemExit, match="The draft v1.0.0-bbbbbbb.2 waits for the other half"):
        release.main(args + ["--respin", "3"])
    with pytest.raises(SystemExit, match="from 2"):
        release.main(args + ["--respin", "1"])


def built_plugin(tmp_path, platform):
    """A BuildPlugin package's binaries as --sign finds them: two of them and their debug symbols."""
    binaries = tmp_path / "ue58" / "Binaries" / platform
    binaries.mkdir(parents=True)
    ext = "dylib" if platform == "Mac" else "dll"
    for name in ("UnrealEditor-AutopilotPlaytest", "UnrealEditor-AutopilotCodex"):
        (binaries / f"{name}.{ext}").write_bytes(b"binary")
        if platform == "Mac":
            (binaries / f"{name}.{ext}.dSYM" / "Contents").mkdir(parents=True)
            (binaries / f"{name}.{ext}.dSYM" / "Contents" / f"{name}.{ext}").write_bytes(b"symbols")
        else:
            (binaries / f"{name}.pdb").write_bytes(b"symbols")
    return str(tmp_path / "ue58")


def fake_tools(monkeypatch, answer):
    """subprocess.run as package_release calls it, answered by answer(args) -> (returncode, stdout); the calls made."""
    calls = []

    def run(args, **kw):
        calls.append(args)
        code, stdout = answer(args)
        return package_release.subprocess.CompletedProcess(args, code, stdout, "")
    monkeypatch.setattr(package_release.subprocess, "run", run)
    return calls


def test_on_windows_sign_signs_each_dll_with_keylocker_and_reads_every_signature_back(tmp_path, monkeypatch):
    package = built_plugin(tmp_path, "Win64")
    signer = "CN=TribeVR Inc, O=TribeVR Inc, L=San Francisco, C=US"
    calls = fake_tools(monkeypatch, lambda args: (0, f"Valid|{signer}\nValid|{signer}\n"))
    signed = package_release.sign_binaries(package, None)
    assert [os.path.basename(p) for p in signed] == ["UnrealEditor-AutopilotCodex.dll",
                                                     "UnrealEditor-AutopilotPlaytest.dll"]
    assert [c for c in calls if c[0] == "smctl"] == [
        ["smctl", "sign", "--keypair-alias", package_release.WIN_KEYPAIR, "--input", p] for p in signed]
    assert calls[-1][0] == "powershell" and all(p in calls[-1][-1] for p in signed)
    # smctl's success isn't trusted: a binary not signed, or signed by someone else, stops the build
    for answer in (f"Valid|{signer}\nNotSigned|\n", f"Valid|{signer}\nValid|CN=Someone Else\n", f"Valid|{signer}\n"):
        fake_tools(monkeypatch, lambda args: (0, answer))
        with pytest.raises(SystemExit, match="UnrealEditor-AutopilotPlaytest.dll isn't signed by TribeVR Inc"):
            package_release.sign_binaries(package, None)
    fake_tools(monkeypatch, lambda args: (1, ""))
    with pytest.raises(SystemExit, match="Signing .*UnrealEditor-AutopilotCodex.dll failed"):
        package_release.sign_binaries(package, None)
    with pytest.raises(SystemExit, match="No binaries to sign"):
        package_release.sign_binaries(str(tmp_path / "empty"), None)


def test_on_macos_sign_signs_each_dylib_and_the_disk_image_and_staples_its_notarization(tmp_path, monkeypatch):
    on(monkeypatch, mac=True)
    package = built_plugin(tmp_path, "Mac")
    calls = fake_tools(monkeypatch, lambda args: (0, ""))
    signed = package_release.sign_binaries(package, "ABC123")
    assert len(signed) == 2 and not any(".dSYM" in p for p in signed)  # (not the debug symbols')
    assert [c for c in calls if "--sign" in c] == [
        ["codesign", "--force", "--timestamp", "--options", "runtime", "--sign", "ABC123", p] for p in signed]
    assert [c[-1] for c in calls if "--verify" in c] == signed
    fake_tools(monkeypatch, lambda args: (1, "") if "--verify" in args else (0, ""))
    with pytest.raises(SystemExit, match="isn't validly signed after codesign"):
        package_release.sign_binaries(package, "ABC123")
    # The disk image is signed too (a stapled ticket binds to its signature), and read back
    dmg = "Autopilot-Unreal5.8-Mac.dmg"
    calls = fake_tools(monkeypatch, lambda args: (0, ""))
    package_release.sign_dmg(dmg, "ABC123")
    assert calls == [["codesign", "--force", "--timestamp", "--sign", "ABC123", dmg],
                     ["codesign", "--verify", "--strict", "--verbose=2", dmg]]
    fake_tools(monkeypatch, lambda args: (1, "") if "--verify" in args else (0, ""))
    with pytest.raises(SystemExit, match="isn't validly signed after codesign"):
        package_release.sign_dmg(dmg, "ABC123")
    # It is notarized; a refusal shows Apple's log, which names the binaries it refused
    calls = fake_tools(monkeypatch, lambda args: (0, json.dumps({"id": "n1", "status": "Accepted"})))
    package_release.notarize(dmg)
    assert calls[0][:4] == ["xcrun", "notarytool", "submit", dmg]
    assert package_release.NOTARY_PROFILE in calls[0] and "--wait" in calls[0]
    fake_tools(monkeypatch, lambda args: (0, "UnrealEditor-AutopilotCodex.dylib: not signed") if "log" in args
               else (1, json.dumps({"id": "n1", "status": "Invalid"})))
    with pytest.raises(SystemExit, match=r"(?s)status: Invalid.*AutopilotCodex.dylib: not signed"):
        package_release.notarize(dmg)
    # Then the ticket is stapled, and Gatekeeper has to accept the image as notarized
    calls = fake_tools(monkeypatch, lambda args: (0, "source=Notarized Developer ID") if args[0] == "spctl"
                       else (0, ""))
    package_release.staple(dmg)
    assert [c[:3] for c in calls] == [["xcrun", "stapler", "staple"], ["xcrun", "stapler", "validate"],
                                      ["spctl", "--assess", "--type"]]
    fake_tools(monkeypatch, lambda args: (0, "source=Unnotarized Developer ID") if args[0] == "spctl" else (0, ""))
    with pytest.raises(SystemExit, match="doesn't accept .* as notarized"):
        package_release.staple(dmg)
    fake_tools(monkeypatch, lambda args: (65, "") if args[:3] == ["xcrun", "stapler", "staple"] else (0, ""))
    with pytest.raises(SystemExit, match="stapler staple failed"):
        package_release.staple(dmg)


def test_on_macos_a_signed_build_makes_a_disk_image_signs_notarizes_and_staples_it_in_that_order(tmp_path,
                                                                                                 monkeypatch):
    on(monkeypatch, mac=True)
    source = fake_engine(tmp_path / "UnrealEngine")
    steps = []
    monkeypatch.setattr(package_release, "MAC", False)  # (no Xcode to pick: build_unreal is faked)
    monkeypatch.setattr(package_release, "source", lambda: ("abc123", 1767225600, "commit abc123"))
    monkeypatch.setattr(package_release, "build_wheel", lambda out, commit, epoch: wheel(tmp_path))
    monkeypatch.setattr(package_release, "build_unreal", lambda engine, root, work: work)
    monkeypatch.setattr(package_release, "signer", lambda *a: "ABC123")
    monkeypatch.setattr(package_release, "sign_binaries", lambda package, identity: steps.append("dylibs"))
    dmg = tmp_path / "x.dmg"
    dmg.write_bytes(b"dmg")
    monkeypatch.setattr(package_release, "unreal_zip", lambda *a: pytest.fail("a zip for Mac"))
    monkeypatch.setattr(package_release, "unreal_dmg", lambda *a: steps.append("dmg") or str(dmg))
    monkeypatch.setattr(package_release, "sign_dmg", lambda path, identity: steps.append(f"sign {identity}"))
    monkeypatch.setattr(package_release, "notarize", lambda path: steps.append("notarize"))
    monkeypatch.setattr(package_release, "staple", lambda path: steps.append("staple"))
    package_release.main([str(tmp_path / "out"), "--engine-dir", source, "--only-unreal", "--sign"])
    assert steps == ["dylibs", "dmg", "sign ABC123", "notarize", "staple"]


def test_on_windows_sign_signs_the_installer_every_download_carries(tmp_path, monkeypatch):
    """smctl signs install.ps1, the signature is read back, and the Unity, Godot and
    Unreal downloads all carry the signed copy (their plugins run it to install the agent service)."""
    monkeypatch.setattr(package_release, "SIGNED_INSTALLER", None)
    signer = "CN=TribeVR Inc, O=TribeVR Inc, L=San Francisco, C=US"

    def answer(args):
        if args[0] == "smctl":
            with open(args[-1], "ab") as f:
                f.write(b"# SIG # Begin signature block\n")
        return 0, f"Valid|{signer}\n"
    calls = fake_tools(monkeypatch, answer)
    path = package_release.sign_installer(str(tmp_path))
    assert calls[0] == ["smctl", "sign", "--keypair-alias", package_release.WIN_KEYPAIR, "--input", path]
    assert calls[1][0] == "powershell" and path in calls[1][-1]
    with zipfile.ZipFile(package_release.godot_zip(str(tmp_path), wheel(tmp_path), "Autopilot-1.0.0", "abc123")) as z:
        assert z.read("addons/autopilot/agent_service/install.ps1").endswith(b"# SIG # Begin signature block\n")
        assert b"SIG #" not in z.read("addons/autopilot/agent_service/install.sh")
    tgz = package_release.unity_tarball(str(tmp_path), "1.0.0", wheel(tmp_path), "abc123")
    with tarfile.open(tgz) as tar:
        assert tar.extractfile("package/AgentService~/install.ps1").read().endswith(b"# SIG # Begin signature block\n")
    # smctl's success isn't trusted, nor its failure ignored
    fake_tools(monkeypatch, lambda args: (0, "NotSigned|\n"))
    with pytest.raises(SystemExit, match="install.ps1 isn't signed by TribeVR Inc"):
        package_release.sign_installer(str(tmp_path))
    fake_tools(monkeypatch, lambda args: (1, ""))
    with pytest.raises(SystemExit, match="Signing .*install.ps1 failed"):
        package_release.sign_installer(str(tmp_path))


def test_no_upm_signs_without_unitys_service_account(monkeypatch):
    for name in ("UNITY_ORGANIZATION", "UPM_SERVICE_ACCOUNT_KEY_ID", "UPM_SERVICE_ACCOUNT_KEY_SECRET"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(package_release.shutil, "which", lambda name: f"/bin/{name}")
    calls = fake_tools(monkeypatch, lambda args: (0, ""))
    assert package_release.signer(unreal=False, unity=True, upm=False) is None
    assert calls == [["smctl", "healthcheck"]]  # (the installer is still signed: smctl is checked)
    with pytest.raises(SystemExit, match="--no-upm goes with --sign"):
        package_release.main(["out", "--no-upm"])


def test_sign_says_what_is_missing_before_anything_is_built(monkeypatch):
    for name in ("UNITY_ORGANIZATION", "UPM_SERVICE_ACCOUNT_KEY_ID", "UPM_SERVICE_ACCOUNT_KEY_SECRET"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(package_release.shutil, "which", lambda name: None)
    with pytest.raises(SystemExit, match="smctl .* isn't on PATH"):
        package_release.signer()
    with pytest.raises(SystemExit, match="set UNITY_ORGANIZATION, UPM_SERVICE_ACCOUNT_KEY_ID, "
                                         "UPM_SERVICE_ACCOUNT_KEY_SECRET"):
        package_release.signer(unreal=False, unity=True)
    monkeypatch.setattr(package_release.shutil, "which", lambda name: f"/bin/{name}")
    fake_tools(monkeypatch, lambda args: (1, ""))
    with pytest.raises(SystemExit, match="smctl healthcheck failed"):
        package_release.signer()
    on(monkeypatch, mac=True)
    identities = ('  1) 1111 "Apple Development: Someone (AAAAAAAAAA)"\n'
                  '  2) 2222 "Developer ID Application: Other Co (BBBBBBBBBB)"\n'
                  f'  3) 3333 "Developer ID Application: TribeVR Inc ({package_release.MAC_TEAM})"\n')
    fake_tools(monkeypatch, lambda args: (0, identities))
    assert package_release.signer() == "3333"
    fake_tools(monkeypatch, lambda args: (0, identities) if args[0] == "security" else (1, ""))
    with pytest.raises(SystemExit, match="notarytool has no working credentials named autopilot-notary"):
        package_release.signer()
    fake_tools(monkeypatch, lambda args: (0, identities.replace(package_release.MAC_TEAM, "CCCCCCCCCC")))
    with pytest.raises(SystemExit, match="No Developer ID Application identity of team"):
        package_release.signer()


def test_a_signed_unity_package_is_upms_pack_of_the_same_files(tmp_path, monkeypatch):
    """--sign: upm pack signs the package for the Unity organization (Unity 6.3 and later flag one without a
    signature); the tarball must still carry AgentService~/, which upm packs from a copy of the package."""
    monkeypatch.setenv("UNITY_ORGANIZATION", "1234567890123")
    drop = []

    def upm(args):
        folder, destination = args[2], args[-1]
        assert args[:2] == ["upm", "pack"] and args[3:5] == ["--organization-id", "1234567890123"]
        os.makedirs(destination)
        with tarfile.open(os.path.join(destination, "com.ramenvr.autopilot-9.9.9.tgz"), "w:gz") as tar:
            for top, dirs, names in os.walk(folder):
                for name in names:
                    rel = os.path.relpath(os.path.join(top, name), folder).replace(os.sep, "/")
                    if not any(rel.startswith(d) for d in drop):
                        tar.add(os.path.join(top, name), f"package/{rel}")
            tar.add(os.path.join(folder, "package.json"), "package/.attestation.p7m")
        return 0, ""
    fake_tools(monkeypatch, upm)
    path = package_release.unity_tarball(str(tmp_path), "1.0.0", wheel(tmp_path), "abc123", sign=True)
    (tmp_path / "plain").mkdir()
    unsigned = package_release.unity_tarball(str(tmp_path / "plain"), "1.0.0", wheel(tmp_path), "abc123")
    assert os.path.basename(path) == os.path.basename(unsigned)  # (the name it always had)
    with tarfile.open(path) as tar, tarfile.open(unsigned) as plain:
        assert set(tar.getnames()) == set(plain.getnames()) | {"package/.attestation.p7m"}
    drop.append("AgentService~/")
    with pytest.raises(SystemExit, match="missing package/AgentService~/"):
        package_release.unity_tarball(str(tmp_path), "1.0.0", wheel(tmp_path), "abc123", sign=True)


def test_one_readme_sets_up_every_engine():
    readme = package_release.readme("1.0.0", "commit abc123")
    assert "{" not in readme and "Autopilot 1.0.0" in readme
    assert readme.endswith("Built from Autopilot commit abc123.\n")
    for engine in ("Plugins/Autopilot/Autopilot.uplugin", "Install package from tarball",
                   "Project > Project Settings > Plugins"):
        assert engine in readme
    for folder in ("Plugins/Autopilot/AgentService/", "AgentService~/", "addons/autopilot/agent_service/"):
        assert folder in readme


def copy_without_git(root, edit=None):
    """A copy of this repository's sources the way GitHub's Download ZIP unpacks them: no .git."""
    for top in ("pyproject.toml", "autopilot", "plugin/unreal/Autopilot"):
        path = root / top
        if top.endswith(".toml"):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("[project]\n")
        else:
            path.mkdir(parents=True, exist_ok=True)
    (root / "autopilot" / "__init__.py").write_text(edit or "x = 1\n")
    (root / "plugin" / "unreal" / "Autopilot" / "Autopilot.uplugin").write_text("{}")
    return str(root)


def test_a_copy_without_git_history_is_named_by_its_files(tmp_path, monkeypatch):
    """A user ran tools/build_unreal.ps1 from GitHub's Download ZIP: `git status` failed (no .git) and nothing was
    built. The ID such a copy's downloads carry is its files' hash: the same files the same ID (and wheel bytes, dated
    FILES_EPOCH), any change to what is built another, and what building or running leaves there no change."""
    monkeypatch.setattr(package_release, "git", lambda *a: pytest.fail(f"git {a} in a copy without git"))
    build_id, epoch, built_from = package_release.source(copy_without_git(tmp_path / "a"))
    assert build_id.startswith("files-") and len(build_id) == 46 and epoch == package_release.FILES_EPOCH
    assert built_from.startswith(build_id) and "without git history" in built_from
    assert package_release.source(copy_without_git(tmp_path / "b"))[0] == build_id
    left = tmp_path / "b"
    for junk in ("autopilot/__pycache__/x.cpython-312.pyc", "autopilot/build.json", "autopilot/x.pyc",
                 "autopilot/agents/opencode_host/node_modules/p/index.js", "plugin/unreal/Autopilot/Binaries/a.dll",
                 "plugin/unreal/Autopilot/Intermediate/b.obj", "dist/Autopilot-1.0.0-Unreal5.8-Win64.zip", "build/c"):
        (left / junk).parent.mkdir(parents=True, exist_ok=True)
        (left / junk).write_text("built")
    assert package_release.source(str(left))[0] == build_id
    assert package_release.source(copy_without_git(tmp_path / "c", edit="x = 2\n"))[0] != build_id
    (left / "autopilot" / "__init__.py").rename(left / "autopilot" / "other.py")
    assert package_release.source(str(left))[0] != build_id


def test_the_files_a_copy_is_named_by_are_what_the_downloads_are_built_from():
    for top in package_release.SOURCES:  # (one renamed would drop out of the hash unnoticed)
        assert os.path.exists(os.path.join(package_release.HERE, top)), top
    for folder in (package_release.UNREAL, package_release.UNITY, package_release.GODOT, package_release.STANDALONE):
        assert os.path.relpath(folder, package_release.HERE).replace(os.sep, "/") in package_release.SOURCES


def test_a_clone_git_fails_in_says_what_git_said(tmp_path, monkeypatch):
    """git's error went unshown: a traceback naming `git status` and exit status 128, nothing about why."""
    monkeypatch.setattr(package_release, "HERE", str(tmp_path))
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    with pytest.raises(SystemExit, match="not a git repository"):
        package_release.git("status", "--porcelain")
    monkeypatch.setattr(package_release.subprocess, "run", lambda *a, **kw: (_ for _ in ()).throw(FileNotFoundError()))
    with pytest.raises(SystemExit, match="Git isn't on PATH"):
        package_release.git("status")


