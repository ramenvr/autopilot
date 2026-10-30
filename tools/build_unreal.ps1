# Builds Autopilot for one Unreal engine, such as one built from source, from this repository (a clone, or GitHub's
# Download ZIP unpacked): the plugin built for that engine, carrying the agent service its first-run setup installs.
# Copy the result into a project's Plugins folder.
#
#   powershell -ExecutionPolicy Bypass -File tools\build_unreal.ps1 -EngineDir C:\UnrealEngine
#       [-PluginsDir <YourProject>\Plugins] [-AuraServer https://<an Aura server>] [-OutDir <folder>] [-WorkDir C:\Temp]
#
# -EngineDir   the engine's folder (the one holding Engine\, or Engine\ itself); a launcher engine works too
# -PluginsDir  a project's Plugins folder (beside its .uproject; made if missing): the build is installed there as
#              Autopilot\, in place of an Autopilot already there. Without it, copy dist\Autopilot\ there yourself
# -AuraServer  the Aura server the plugin signs in to and runs through (default: Aura's own, app.tryaura.dev)
# -OutDir      where the result goes (default: dist\ in this checkout): Autopilot\, to copy into
#              <YourProject>\Plugins\, and the download zip to share, with its README and SHA256SUMS.txt
# -WorkDir     the build's temporary folder; keep it short, as Unreal's BuildPlugin fails on long paths
#
# Needs only the engine with the toolchain it builds with, and Git when this is a clone (the build names its commit; a
# copy without git's history is named by its files' hash): uv is installed first if it isn't there
# (https://docs.astral.sh/uv/), and brings the Python that runs tools/package_release.py, which does the build.

param(
    [Parameter(Mandatory = $true)][string]$EngineDir,
    [string]$PluginsDir,
    [string]$AuraServer,
    [string]$OutDir,
    [string]$WorkDir = "C:\Temp"
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
if (-not $OutDir) { $OutDir = Join-Path $repo "dist" }
$OutDir = [System.IO.Path]::GetFullPath($OutDir)

if (-not ((Test-Path (Join-Path $EngineDir "Engine\Build\Build.version")) -or (Test-Path (Join-Path $EngineDir "Build\Build.version")))) {
    throw "$EngineDir isn't an Unreal engine's folder: pass the folder that holds Engine\ (e.g. C:\UnrealEngine)."
}
if ((Test-Path (Join-Path $repo ".git")) -and -not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "$repo is a git clone, but Git isn't on PATH: the build names the commit it comes from. Install Git (https://git-scm.com), then run this again."
}

function Get-LoadedFile($folder) {
    # The first program file in folder that a running program has loaded (an editor with this project open), else $null.
    # Windows still lets such a folder be renamed, so this is how to tell: a loaded file can't be opened for writing.
    foreach ($file in Get-ChildItem -Path $folder -Recurse -File -Include "*.dll", "*.exe" -ErrorAction SilentlyContinue) {
        try { [System.IO.File]::Open($file.FullName, "Open", "ReadWrite", "None").Close() } catch { return $file.FullName }
    }
    return $null
}

# -PluginsDir is checked before the build: a project's Plugins folder, with no other Autopilot under it (Unreal loads
# every Autopilot.uplugin it finds there, whichever copy it picks) and no editor using the one there
if ($PluginsDir) {
    $PluginsDir = [System.IO.Path]::GetFullPath($PluginsDir)
    $target = Join-Path $PluginsDir "Autopilot"
    $aside = Join-Path $PluginsDir "Autopilot.replaced"  # (the one it replaces, until the new one is in place)
    if (-not (Get-ChildItem -Path (Split-Path -Parent $PluginsDir) -Filter "*.uproject" -ErrorAction SilentlyContinue)) {
        throw "$PluginsDir isn't a project's Plugins folder: pass the Plugins folder beside your .uproject (e.g. C:\Games\MyGame\Plugins)."
    }
    foreach ($folder in $target, $aside) {
        if ((Test-Path $folder) -and -not (Test-Path (Join-Path $folder "Autopilot.uplugin"))) {
            throw "$folder is in the way and isn't an Autopilot plugin: move it, then run this again."
        }
        $loaded = Get-LoadedFile $folder
        if ($loaded) { throw "An editor has $folder loaded ($loaded): close it, then run this again." }
    }
    if (Test-Path $PluginsDir) {
        $others = Get-ChildItem -Path $PluginsDir -Filter "Autopilot.uplugin" -Recurse -ErrorAction SilentlyContinue |
            Where-Object { $_.DirectoryName -ne $target -and $_.DirectoryName -ne $aside }
        if ($others) {
            throw "Another Autopilot is in $PluginsDir ($($others[0].DirectoryName)): remove it, then run this again. Unreal would load either copy."
        }
    }
}

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "Installing uv (https://docs.astral.sh/uv/), which brings the Python the build runs with..."
    powershell -ExecutionPolicy Bypass -c "irm https://astral.sh/uv/install.ps1 | iex"
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}

New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null
$env:RG_WORK = $WorkDir
$build = @("run", "--no-project", "--python", "3.12", (Join-Path $repo "tools\package_release.py"), $OutDir,
    "--engine-dir", $EngineDir, "--only-unreal")
if ($AuraServer) { $build += @("--aura-server", $AuraServer) }
$started = Get-Date
& uv @build
if ($LASTEXITCODE -ne 0) { throw "The build failed (exit code $LASTEXITCODE): see the lines above." }

# The plugin out of the zip this build made, as Autopilot\ beside it
$zip = Get-ChildItem -Path $OutDir -Filter "Autopilot-*-Unreal*-Win64.zip" |
    Where-Object { $_.LastWriteTime -ge $started.AddSeconds(-5) } | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $zip) { throw "The build finished, but no Unreal download is in $OutDir." }
$plugin = Join-Path $OutDir "Autopilot"
if (Test-Path $plugin) {
    if (-not (Test-Path (Join-Path $plugin "Autopilot.uplugin"))) { throw "$plugin is in the way and isn't an Autopilot plugin: move it, then run this again." }
    Remove-Item -Recurse -Force $plugin
}
Expand-Archive -Path $zip.FullName -DestinationPath $OutDir

if (-not $PluginsDir) {
    Write-Host ""
    Write-Host "Done. Copy this folder into your project's Plugins folder, so that Plugins\Autopilot\Autopilot.uplugin exists:"
    Write-Host "    $plugin"
    Write-Host "Then open the project and click Autopilot on the toolbar. To share this build: $($zip.FullName)"
    exit 0
}

# Into the project, once no editor has the Autopilot there loaded (an editor opened during the build counts): that one
# is renamed aside, comes back if the copy fails, and is deleted once the new one is in place
foreach ($folder in $target, $aside) {
    $loaded = Get-LoadedFile $folder
    if ($loaded) { throw "An editor has $folder loaded ($loaded): close it, then run this again. The project wasn't changed; the build is in $plugin." }
}
New-Item -ItemType Directory -Force -Path $PluginsDir | Out-Null
if (Test-Path $aside) { Remove-Item -Recurse -Force $aside }  # (a run before couldn't delete it; checked above)
if (Test-Path $target) { Rename-Item -Path $target -NewName (Split-Path -Leaf $aside) }
try { Copy-Item -Path $plugin -Destination $target -Recurse -ErrorAction Stop }
catch {
    if (Test-Path $target) { Remove-Item -Recurse -Force $target -ErrorAction SilentlyContinue }
    if (Test-Path $aside) { Rename-Item -Path $aside -NewName "Autopilot" -ErrorAction SilentlyContinue }
    throw "Couldn't copy the build into $target ($_). The project's Autopilot was left as it was; the build is in $plugin."
}
if (Test-Path $aside) {
    try { Remove-Item -Recurse -Force $aside -ErrorAction Stop }
    catch { Write-Warning "Delete $aside before you open the project: Unreal would load it as well as the new Autopilot." }
}

Write-Host ""
Write-Host "Done. Autopilot is in $target."
Write-Host "Open the project (restart its editor if it was open) and click Autopilot on the toolbar. To share this build: $($zip.FullName)"
