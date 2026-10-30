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

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "Installing uv (https://docs.astral.sh/uv/), which installs Autopilot and its Python..."
    powershell -ExecutionPolicy Bypass -c "irm https://astral.sh/uv/install.ps1 | iex"
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}

Write-Host "Installing Autopilot from $($wheel.Name)..."
# An update never leaves a broken Autopilot behind. `uv tool install --force` deletes the installed environment first,
# and a program running from it (another editor's agent service, the `autopilot mcp` a Claude Code or Codex session
# keeps open) holds files Windows won't delete: uv then stopped halfway, Lib (the autopilot package with it) gone. A
# held folder and a running .exe can still be moved, so the installed environment and its command are moved aside,
# the new one is installed where they were and checked, and on any failure the old ones are moved back. What was moved
# aside is deleted once nothing holds it (here, or by the next install).
$toolDir = "$(uv tool dir)".Trim()
$binDir = "$(uv tool dir --bin)".Trim()
if ($LASTEXITCODE -ne 0 -or -not $binDir) { $binDir = Join-Path $env:USERPROFILE ".local\bin" }  # (an older uv)
$envDir = Join-Path $toolDir "autopilot"
$command = Join-Path $binDir "autopilot.exe"
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$asideDir = Join-Path (Split-Path -Parent $toolDir) "autopilot-previous"  # (not in the tool folder: uv lists those)
$envAside = Join-Path $asideDir "autopilot-$stamp"
$commandAside = "autopilot.exe.$stamp.old"  # (beside it: an .exe moves only within its drive)

function Remove-Aside {
    # (what earlier updates moved aside: a file still held stays for the next one)
    if (Test-Path $asideDir) {
        Get-ChildItem $asideDir | ForEach-Object { Remove-Item -Recurse -Force $_.FullName -ErrorAction SilentlyContinue }
    }
    Get-ChildItem $binDir -Filter "autopilot.exe.*.old" -ErrorAction SilentlyContinue |
        ForEach-Object { Remove-Item -Force $_.FullName -ErrorAction SilentlyContinue }
}

function Restore-Previous {
    if ($script:movedEnv) {
        if (Test-Path $envDir) { Remove-Item -Recurse -Force $envDir -ErrorAction SilentlyContinue }  # (what uv made of it)
        Move-Item -LiteralPath $envAside -Destination $envDir -ErrorAction SilentlyContinue
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
    try { Move-Item -LiteralPath $envDir -Destination $envAside -ErrorAction Stop; $script:movedEnv = $true }
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
Write-Host "Autopilot is installed. Autopilot starts it with your editor: open the Autopilot tab and finish its first-run"
Write-Host "setup. To run it yourself instead, run this before you open the editor (Autopilot connects to it):"
Write-Host "  autopilot serve"
