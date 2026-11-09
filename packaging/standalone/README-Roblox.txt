Autopilot for Roblox Studio
===========================

Install once
------------

1. Copy Autopilot.rbxmx into Studio's plugins folder:
   - Windows: %LOCALAPPDATA%\Roblox\Plugins
   - macOS:   ~/Documents/Roblox/Plugins
   (In Studio: Plugins > Plugins Folder opens it.)

2. Install the autopilot command, from this folder:
   - Windows, in PowerShell:
       powershell -ExecutionPolicy Bypass -File agent_service\install.ps1
   - macOS, in Terminal:
       sh agent_service/install.sh
   It installs everything it needs. It needs the internet once.

3. In Studio, turn on Studio's MCP server (Assistant > ... > Settings > MCP Servers >
   Enable Studio as MCP server). It gives Autopilot pictures of your game. Runs work
   without it, but with no recording or stills.

Each time you use Autopilot
---------------------------

1. Open a new terminal and run:
       autopilot serve
   Leave it running while you use Autopilot. (Right after installing, use a new terminal:
   the one you installed from doesn't have the autopilot command yet.)

2. Open your place in Studio. The Autopilot dock (Plugins > Autopilot) connects by itself.

More: the setup guide, docs/roblox-studio-setup.md, at https://github.com/ramenvr/autopilot
