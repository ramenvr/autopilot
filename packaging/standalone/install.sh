#!/bin/sh
# Installs Autopilot's agent service (the `autopilot` command) from the wheel beside this script, on macOS or Linux.
#
#   sh install.sh
#
# It uses uv (https://docs.astral.sh/uv/), installing it first if it isn't there, and uv brings a Python 3.12 of its
# own; the agent, OpenCode, runs under Bun (https://bun.sh), installed the same way: nothing else needs to be
# installed. Run it again to update. Uninstall with `autopilot uninstall` (or Remove from this computer, on a
# plugin's Settings page).
set -eu

here=$(cd "$(dirname "$0")" && pwd)
wheel=$(ls "$here"/autopilot-*.whl 2>/dev/null | head -n 1)
if [ -z "$wheel" ]; then
  echo "No autopilot-*.whl next to install.sh: unpack the whole download, then run it again." >&2
  exit 1
fi

case "$(uname -s)" in
  Darwin) base="$HOME/Library/Application Support" ;;
  *) base="${XDG_CONFIG_HOME:-$HOME/.config}" ;;
esac
settings="$base/Autopilot"
env_file="$settings/autopilot.env"

# One install at a time. Two editors that start together (or one project opened twice) each run this when the agent
# service installed is older than theirs, and the second reinstalled under the first while it installed and started
# it. A later install waits for the one running (a lock folder naming its process; one whose process is gone is taken
# over), then leaves what that one installed if it is this same agent service and works.
lock="$settings/install.lock"
fresh=""
trap 'rm -rf "$fresh"; if [ "$(cat "$lock/pid" 2>/dev/null)" = "$$" ]; then rm -rf "$lock"; fi' EXIT
trap 'exit 1' HUP INT TERM
mkdir -p "$settings"
waited=0
waiting=0
until mkdir "$lock" 2>/dev/null; do
  holder=$(cat "$lock/pid" 2>/dev/null || true)
  if [ -n "$holder" ] && ! kill -0 "$holder" 2>/dev/null; then
    rm -rf "$lock"  # (an install that ended without letting go)
    continue
  fi
  if [ "$waited" -eq 0 ]; then
    echo "Another Autopilot install is running (another editor's): waiting for it to finish..."
    waited=1
  fi
  waiting=$((waiting + 1))
  if [ "$waiting" -gt 1200 ]; then
    echo "Couldn't install Autopilot: another install has been running for 20 minutes. Close the other editors, then run this again." >&2
    exit 1
  fi
  sleep 1
done
echo "$$" > "$lock/pid"

if ! command -v uv >/dev/null 2>&1; then
  echo "Installing uv (https://docs.astral.sh/uv/), which installs Autopilot and its Python..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  PATH="$HOME/.local/bin:$PATH"
fi

python="$(uv tool dir)/autopilot/bin/python"
if [ "$waited" -eq 1 ] && [ -f "$here/agent-service.json" ] && [ -x "$python" ] &&
    cmp -s "$here/agent-service.json" "$settings/agent-service.json" &&
    "$python" -I -c "import autopilot.cli"; then
  echo "The install that was running installed this same agent service: Autopilot is installed."
  exit 0
fi

echo "Installing Autopilot from $(basename "$wheel")..."
# The wheel is installed from a copy dated now, in a folder of its own. uv keeps a local wheel it has unpacked by its
# path and date, and every download's files are dated alike (2026-01-01, tools/package_release.py), so a plugin updated
# in place would have uv install the build before it again, from its cache, and say it installed this one
fresh=$(mktemp -d "${TMPDIR:-/tmp}/autopilot-wheel-XXXXXX")
cp "$wheel" "$fresh/"
touch "$fresh/$(basename "$wheel")"
uv tool install --force --python 3.12 "autopilot[claude,codex] @ file://$fresh/$(basename "$wheel")"
uv tool update-shell >/dev/null 2>&1 || true

# Which agent service this is (agent-service.json beside this script), kept in the settings folder: a plugin carrying
# another one installs its own before it starts the agent service
if [ -f "$here/agent-service.json" ]; then
  mkdir -p "$settings"
  cp "$here/agent-service.json" "$settings/agent-service.json"
fi

# Settings the download carries (settings.env beside this script, e.g. which Aura server it was built for) go into
# autopilot.env over what is there; those an earlier download carried and this one doesn't are taken out
# (python -I, here and below: the installed Autopilot, whatever folder this runs in; from one holding a checkout's
# autopilot/, that was imported instead, and OpenCode's packages went into it)
"$python" -I -c "import sys; from autopilot import account; account.apply_download_settings(sys.argv[1])" "$here/settings.env"

# The Coding Agents: Claude Code and Codex come with Autopilot; OpenCode, the default, runs under Bun (installed here when it
# isn't there), and its packages are fetched now, not at the first run. Without Bun, Claude Code is the default
if ! command -v bun >/dev/null 2>&1 && [ ! -x "$HOME/.bun/bin/bun" ]; then
  echo "Installing Bun (https://bun.sh), which runs the agent..."
  curl -fsSL https://bun.sh/install | bash || true
fi
if ! "$python" -I -c "from autopilot.agents import opencode; opencode.ensure_installed(opencode.bun())"
then
  mkdir -p "$settings"
  if ! grep -Eq '^[[:space:]]*(export[[:space:]]+)?AUTOPILOT_AGENT[[:space:]]*=' "$env_file" 2>/dev/null; then
    echo "AUTOPILOT_AGENT=claude" >> "$env_file"
    chmod 600 "$env_file"
  fi
  echo "OpenCode couldn't be set up under Bun, so Autopilot will use Claude Code; Codex works too (Coding Agent in the"
  echo "first-run setup). Install Bun (https://bun.sh), then run this again to use OpenCode."
fi

if [ -f "$here/../Autopilot.rbxmx" ]; then
  # (the Roblox download: a Roblox plugin can't start programs, so the user starts the agent service each time)
  cat <<'EOF'

Autopilot is installed. Each time you use Autopilot in Roblox Studio, open a new terminal (this one doesn't
have the autopilot command yet), run this, and leave it running:
  autopilot serve
Then open your place in Studio: the Autopilot dock connects to it by itself.
EOF
else
  cat <<'EOF'

Autopilot is installed. Autopilot starts it with your editor: open the Autopilot tab and finish its first-run
setup. To run it yourself instead, run this before you open the editor (Autopilot connects to it):
  autopilot serve
EOF
fi
