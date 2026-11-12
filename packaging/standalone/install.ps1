# Installs Autopilot's agent service (the `autopilot` command) from the wheel beside this script, on Windows.
#
#   powershell -ExecutionPolicy Bypass -File install.ps1
#
# It uses uv (https://docs.astral.sh/uv/), installing it first if it isn't there, and uv brings a Python 3.12 of its
# own; the agent, OpenCode, runs under Bun (https://bun.sh), installed the same way: nothing else needs to be
# installed. Run it again to update. Uninstall with `autopilot uninstall` (or Remove from this computer, on a
# plugin's Settings page).

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$settings = Join-Path $env:LOCALAPPDATA "Autopilot"
$envFile = Join-Path $settings "autopilot.env"
$wheel = Get-ChildItem -Path $here -Filter "autopilot-*.whl" | Select-Object -First 1
if (-not $wheel) { throw "No autopilot-*.whl next to install.ps1: unpack the whole download, then run it again." }

# One install at a time. Two editors that start together (or one project opened twice) each run this when the agent
# service installed is older than theirs; the second moved the installed environment aside while the first was still
# installing and using it, and left it broken. A later install waits for the one running (the lock is let go when its
# PowerShell ends, however it ends), then leaves what that one installed if it is this same agent service and works.
$installLock = New-Object System.Threading.Mutex($false, "Local\AutopilotAgentServiceInstall")
function Enter-InstallLock([int]$Milliseconds) {
    try { return $installLock.WaitOne($Milliseconds) }
    catch [System.Threading.AbandonedMutexException] { return $true }  # (one that ended without letting go: ours now)
}
$waited = $false
if (-not (Enter-InstallLock 0)) {
    Write-Host "Another Autopilot install is running (another editor's): waiting for it to finish..."
    $waited = $true
    if (-not (Enter-InstallLock 1200000)) {
        throw "Couldn't install Autopilot: another install has been running for 20 minutes. Close the other editors, then run this again."
    }
}

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "Installing uv (https://docs.astral.sh/uv/), which installs Autopilot and its Python..."
    powershell -ExecutionPolicy Bypass -c "irm https://astral.sh/uv/install.ps1 | iex"
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}

$toolDir = "$(uv tool dir)".Trim()
$binDir = "$(uv tool dir --bin)".Trim()
if ($LASTEXITCODE -ne 0 -or -not $binDir) { $binDir = Join-Path $env:USERPROFILE ".local\bin" }  # (an older uv)
$envDir = Join-Path $toolDir "autopilot"
$command = Join-Path $binDir "autopilot.exe"

$installedStamp = Join-Path $settings "agent-service.json"
$ourStamp = Join-Path $here "agent-service.json"
$envPython = Join-Path $envDir "Scripts\python.exe"
if ($waited -and (Test-Path $ourStamp) -and (Test-Path $installedStamp) -and (Test-Path $envPython) -and
        ((Get-Content -Raw $ourStamp) -eq (Get-Content -Raw $installedStamp))) {
    & $envPython -I -c "import autopilot.cli"
    if ($LASTEXITCODE -eq 0) {
        Write-Host "The install that was running installed this same agent service: Autopilot is installed."
        exit 0
    }
}

Write-Host "Installing Autopilot from $($wheel.Name)..."
# An update never leaves a broken Autopilot behind. `uv tool install --force` deletes the installed environment first,
# and a program running from it (another editor's agent service, the `autopilot mcp` a Claude Code or Codex session
# keeps open) holds files Windows won't delete: uv then stopped halfway, Lib (the autopilot package with it) gone. A
# held folder and a running .exe can still be moved, so the installed environment and its command are moved aside,
# the new one is installed where they were and checked, and on any failure the old ones are moved back. What was moved
# aside is deleted once nothing holds it (here, or by the next install).
# The folder is moved by one rename ([System.IO.Directory]::Move), all of it or none: Move-Item, when a file in it is
# open by a program that doesn't let it be moved, moves the files before that one and stops, and the installed
# environment was left in two halves (no pyvenv.cfg where uv looks: "No pyvenv.cfg file", every start).
# Folders are deleted through their \\?\ path: OpenCode's packages (in the autopilot package) go deeper than Windows
# PowerShell's 260-character paths, and Remove-Item left every environment moved aside behind.
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$asideDir = Join-Path (Split-Path -Parent $toolDir) "autopilot-previous"  # (not in the tool folder: uv lists those)
$envAside = Join-Path $asideDir "autopilot-$stamp"
$commandAside = "autopilot.exe.$stamp.old"  # (beside it: an .exe moves only within its drive)

function Move-Folder([string]$From, [string]$To) {
    try { [System.IO.Directory]::Move($From, $To) }
    catch { throw $(if ($_.Exception.InnerException) { $_.Exception.InnerException } else { $_.Exception }) }
}

function Remove-Folder([string]$Path) {
    # (as much as isn't held: what is stays for the next install)
    try { [System.IO.Directory]::Delete("\\?\" + [System.IO.Path]::GetFullPath($Path), $true) } catch { }
}

function Remove-Aside {
    # (what earlier updates moved aside)
    if (Test-Path $asideDir) {
        Get-ChildItem $asideDir | ForEach-Object { Remove-Folder $_.FullName }
    }
    Get-ChildItem $binDir -Filter "autopilot.exe.*.old" -ErrorAction SilentlyContinue |
        ForEach-Object { Remove-Item -Force $_.FullName -ErrorAction SilentlyContinue }
}

function Restore-Previous {
    if ($script:movedEnv) {
        if (Test-Path $envDir) { Remove-Folder $envDir }  # (what uv made of it)
        try { Move-Folder $envAside $envDir } catch { }
    }
    if ($script:movedCommand) {
        if (Test-Path $command) { Remove-Item -Force $command -ErrorAction SilentlyContinue }
        Rename-Item -LiteralPath (Join-Path $binDir $commandAside) -NewName "autopilot.exe" -ErrorAction SilentlyContinue
    }
    if ($script:movedEnv -and -not (Test-Path (Join-Path $envDir "Lib\site-packages\autopilot"))) {
        return " The version installed before couldn't be put back either: close every program using Autopilot, then run this again."
    }
    if ($script:movedEnv) { return " The version installed before is kept." }
    return ""
}

Remove-Aside
$script:movedEnv = $false
$script:movedCommand = $false
$inUse = ("Close what runs Autopilot (another editor's Autopilot tab, a Claude Code or Codex session using it), then " +
          "run this again. The version installed before is unchanged.")
if (Test-Path $envDir) {
    # (the command first: a running .exe can't be moved, so an agent service it runs, another editor's, leaves
    # everything as it was)
    if (Test-Path $command) {
        try { Rename-Item -LiteralPath $command -NewName $commandAside -ErrorAction Stop; $script:movedCommand = $true }
        catch { throw "Couldn't update Autopilot: $command is running. $inUse" }
    }
    New-Item -ItemType Directory -Force -Path $asideDir | Out-Null
    try { Move-Folder $envDir $envAside; $script:movedEnv = $true }
    catch {
        Restore-Previous | Out-Null
        throw "Couldn't update Autopilot: its installed files are in use ($($_.Exception.Message)). $inUse"
    }
}
# The wheel is installed from a copy dated now, in a folder of its own. uv keeps a local wheel it has unpacked by its
# path and date, and every download's files are dated alike (2026-01-01, tools/package_release.py), so a plugin updated
# in place would have uv install the build before it again, from its cache, and say it installed this one
$fresh = Join-Path ([System.IO.Path]::GetTempPath()) ("autopilot-wheel-" + [guid]::NewGuid().ToString("N"))
try {
    New-Item -ItemType Directory -Force -Path $fresh | Out-Null
    $copy = Join-Path $fresh $wheel.Name
    Copy-Item -LiteralPath $wheel.FullName -Destination $copy
    (Get-Item -LiteralPath $copy).LastWriteTime = Get-Date
}
catch {
    Remove-Item -Recurse -Force $fresh -ErrorAction SilentlyContinue
    $kept = Restore-Previous
    throw "Couldn't copy $($wheel.Name) to install it ($($_.Exception.Message)).$kept"
}
$uri = ([System.Uri]$copy).AbsoluteUri
uv tool install --force --python 3.12 "autopilot[claude,codex] @ $uri"
$installed = $LASTEXITCODE
Remove-Item -Recurse -Force $fresh -ErrorAction SilentlyContinue
if ($installed -eq 0) {
    & (Join-Path $envDir "Scripts\python.exe") -I -c "import autopilot.cli"
    if ($LASTEXITCODE -ne 0) {
        $kept = Restore-Previous
        throw "uv installed Autopilot, but it doesn't start (python -c `"import autopilot.cli`" failed).$kept"
    }
}
else {
    $kept = Restore-Previous
    throw "uv couldn't install Autopilot (exit code $installed).$kept"
}
Remove-Aside
uv tool update-shell | Out-Null

# Which agent service this is (agent-service.json beside this script), kept in the settings folder: a plugin carrying
# another one installs its own before it starts the agent service
$stamp = Join-Path $here "agent-service.json"
if (Test-Path $stamp) {
    New-Item -ItemType Directory -Force -Path $settings | Out-Null
    Copy-Item -Force $stamp (Join-Path $settings "agent-service.json")
}

# Settings the download carries (settings.env beside this script, e.g. which Aura server it was built for) go into
# autopilot.env over what is there; those an earlier download carried and this one doesn't are taken out
# (python -I, here and below: the installed Autopilot, whatever folder this runs in; from one holding a checkout's
# autopilot/, that was imported instead, and OpenCode's packages went into it)
$python = Join-Path (uv tool dir) "autopilot\Scripts\python.exe"
& $python -I -c "import sys; from autopilot import account; account.apply_download_settings(sys.argv[1])" (Join-Path $here "settings.env")
if ($LASTEXITCODE -ne 0) { throw "Couldn't save the download's settings in $envFile (exit code $LASTEXITCODE)." }

# The Coding Agents: Claude Code and Codex come with Autopilot; OpenCode, the default, runs under Bun (installed here when it
# isn't there), and its packages are fetched now, not at the first run. Without Bun, Claude Code is the default
$bun = Get-Command bun -ErrorAction SilentlyContinue
$bunHome = Join-Path $env:USERPROFILE ".bun\bin\bun.exe"
if (-not $bun -and -not (Test-Path $bunHome)) {
    Write-Host "Installing Bun (https://bun.sh), which runs the agent..."
    try { powershell -ExecutionPolicy Bypass -c "irm bun.sh/install.ps1 | iex" } catch { Write-Host "  $_" }
}
& $python -I -c "from autopilot.agents import opencode; opencode.ensure_installed(opencode.bun())"
if ($LASTEXITCODE -ne 0) {
    New-Item -ItemType Directory -Force -Path $settings | Out-Null
    $lines = @()
    if (Test-Path $envFile) { $lines = Get-Content $envFile }
    if (-not ($lines | Where-Object { $_ -match '^\s*(export\s+)?AUTOPILOT_AGENT\s*=' })) {
        Add-Content -Path $envFile -Value "AUTOPILOT_AGENT=claude"
    }
    Write-Host "OpenCode couldn't be set up under Bun, so Autopilot will use Claude Code; Codex works too (Coding Agent in the"
    Write-Host "first-run setup). Install Bun (https://bun.sh), then run this again to use OpenCode."
}

Write-Host ""
if (Test-Path (Join-Path (Split-Path -Parent $here) "Autopilot.rbxmx")) {
    # (the Roblox download: a Roblox plugin can't start programs, so the user starts the agent service each time)
    Write-Host "Autopilot is installed. Each time you use Autopilot in Roblox Studio, open a new terminal (this one doesn't"
    Write-Host "have the autopilot command yet), run this, and leave it running:"
    Write-Host "  autopilot serve"
    Write-Host "Then open your place in Studio: the Autopilot dock connects to it by itself."
} else {
    Write-Host "Autopilot is installed. Autopilot starts it with your editor: open the Autopilot tab and finish its first-run"
    Write-Host "setup. To run it yourself instead, run this before you open the editor (Autopilot connects to it):"
    Write-Host "  autopilot serve"
}
