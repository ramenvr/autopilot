# Setting up Autopilot with Roblox Studio

This guide takes you from a place in Studio to a first run with a recording. It takes a few minutes. The Roblox plugin's
own details are in [plugin/roblox](../plugin/roblox/README.md).

There are two ways to connect Autopilot to Studio:

| | **The Autopilot plugin** | **Studio's MCP server alone** (`roblox-mcp`, experimental) |
| --- | --- | --- |
| Best for | Playing a place from inside Studio | Runs started from a script, with nothing installed in Studio |
| Start a run from | The dock in Studio | A terminal |
| What you see of a run | A recording of each episode | Stills, no recording |
| Install from | The release zip | A clone of this repository |

Most people want the plugin, which the numbered steps below set up. For the other way, see [Without the
plugin](#without-the-plugin).

You need Roblox Studio on Windows or macOS, signed in to your Roblox account. The steps in Studio were written on Windows
with Studio 0.746; they may look a little different on macOS.

## What runs where

Three things work together, all on your computer:

- **The Autopilot plugin**, inside Studio. It plays your game.
- **The agent service**, a program you start in a terminal. A Roblox plugin can't start programs, so this step is yours.
- **Studio's MCP server**, which comes with Studio. It gives Autopilot pictures of your game: the run's recording and
  stills. Without it, runs still work, but with no recording or stills.

## 1. Install the agent service and the plugin

1. Download `Autopilot-<version>-Roblox.zip` and extract it.
2. Install the agent service: run `agent_service/install.ps1` (Windows) or `sh agent_service/install.sh` (macOS) from the
   extracted folder.
3. Move `Autopilot.rbxmx` into Studio's Plugins folder: `%LOCALAPPDATA%\Roblox\Plugins` (Windows) or
   `~/Documents/Roblox/Plugins` (macOS).

From a clone of this repository, `python tools/build_roblox.py --install` builds the plugin and puts it there.

## 2. Open your place

Open your place in Studio. If Studio was already open, reopen the place: Studio loads local plugins when a place opens.

The **Autopilot** dock appears on the right. **Plugins > Autopilot** shows or hides it.

## 3. Turn on Studio's MCP server

You do this once. Roblox's own docs don't say where the setting is:

1. Click **Assistant**, at the far right of Studio's top bar. The Assistant panel opens.
2. In the panel, click **…**, then **Settings** (or **Manage MCP Servers**, if your Studio shows that).
3. In **Assistant Settings**, open the **MCP Servers** tab.
4. Turn on **Enable Studio as MCP server**.

The status changes from **MCP off** to **MCP Ready**. On Windows, Studio also creates `%LOCALAPPDATA%\Roblox\mcp.bat`,
which is what Autopilot starts. If that file is missing, the server isn't on.

You don't need to change anything under **MCP Permissions**, and you don't need **Full access**.

## 4. Start the agent service

In a terminal, run this and leave it running while you use Autopilot:

```
autopilot serve
```

The dock connects by itself, within about 15 seconds, and its header shows `● CONNECTED`. **Connect** tries at once.

If Studio asks whether the plugin may make HTTP requests or connect to `127.0.0.1`, allow it. The plugin only talks to the
agent service on your computer.

## 5. Sign in and run

Follow [Step 3: Onboarding](../README.md#step-3-onboarding), then enter a prompt in the dock, for example *Verify that the
main menu's Play button starts a match*.

While a run plays, Autopilot starts and stops play sessions by itself. Leave Studio alone until it finishes: don't click
in the game view or press keys in it. Studio needn't be the window in front.

When the run ends, its result has a recording of each episode.

## Saving your place to Roblox (optional)

Autopilot works on a place saved only as a file. You need to save it to Roblox only to see **Assistant Settings > MCP
Permissions**, which otherwise shows *Save to Roblox to manage MCP permissions.*

1. Choose **File > Save to Roblox As…** (or **Publish to Roblox As…**).
2. Choose **Create new experience** and give it a name.
3. Keep **Privacy** set to **Private**, so that nobody else can play it.

## Without the plugin

The `roblox-mcp` engine plays your place through Studio's MCP server alone. A small connector process stands in for
the plugin. [docs/roblox-mcp.md](roblox-mcp.md) has the details and its limits.

1. [Install Autopilot from a clone](../CONTRIBUTING.md#development-setup) and set your keys in `.env`.
2. [Turn on Studio's MCP server](#3-turn-on-studios-mcp-server) and open your place.
3. Run the bench from the clone, with one prompt or a suite:

   ```bash
   AUTOPILOT_ENGINE=roblox-mcp AUTOPILOT_SHARE=0 python bench/run_autopilot.py --project <your Rojo folder or place file> --prompt "Verify that ..."
   ```

The bench starts the connector for you, on a port of its own. If you start `autopilot play` and the connector by hand
and the plugin is installed too, use a port other than 8765: the dock connects to 8765 by itself and would take the
run.

## If something goes wrong

**The dock doesn't connect.** Check that `autopilot serve` is still running. If port 8765 is taken, start it on another
port (`autopilot serve --port 8770`) and set **Agent service URL** in the dock's Settings to match
(`ws://127.0.0.1:8770/v1/bridge`).

**A run says *Studio's MCP server sees no Studio*.** Check that Studio is open on your place and shows **MCP Ready**.

**A run says *Studio's MCP server didn't answer* a few times, and the recording has gaps.** This is expected. Studio can
leave a picture unanswered while a play session starts or stops, and Autopilot restarts the server and carries on. The
moments around each start and stop may be missing from the recording, and the rest is there.

**No picture ever comes, in every run.** Close Studio completely, reopen your place, and try again. We've seen one Studio
session stop answering every picture while its other MCP tools kept working. Restarting Studio cleared it, while
restarting the MCP server, saving to Roblox and changing permissions didn't. We haven't found what causes it.

**A button can't be clicked, or a run says clicking it does nothing.** Look for Roblox's chat window on top of it. In
a small game view, the chat window at the top left can cover part of your UI, and it takes the mouse presses that land
on it. The same button works from outside the window or by keyboard. Make the game view bigger, or move the button.

**Studio's temp folder grows.** Each picture leaves a file of about 0.8 MB in
`%LOCALAPPDATA%\Roblox\tmp-capture-storage`, so a long session can reach gigabytes. Restarting Studio clears it.

**You'd rather not take pictures.** Set `AUTOPILOT_STUDIO_MCP=off` before starting the agent service.
