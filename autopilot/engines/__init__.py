"""The engines a game can be made in, each one adapter (see base.Engine): Unreal, Unity, Godot, Roblox (its Studio
plugin) and roblox-mcp. The plugin's hello says which (`engine`; a plugin that doesn't say is Unreal's). roblox-mcp has
no plugin inside Studio: its connector (engines/roblox_connector.py) speaks for Roblox Studio's built-in MCP server.

A project two engines could play (a Roblox place: roblox and roblox-mcp both can) goes to the engine registered first;
AUTOPILOT_ENGINE (an engine's name) chooses one instead, for every project found by its path."""

import os

from .base import Engine, OpEngine  # noqa: F401
from .godot import Godot
from .roblox import Roblox
from .roblox_connector import RobloxMcp
from .unity import Unity
from .unreal import Unreal

ENGINES = {e.name: e for e in (Unreal(), Unity(), Godot(), Roblox(), RobloxMcp())}
DEFAULT = "unreal"


def get(name=None):
    """The engine of a name (None, "" or an unknown name: Unreal, as plugins from before engines are)."""
    return ENGINES.get(str(name or "").strip().lower()) or ENGINES[DEFAULT]


def for_hello(hello):
    """The engine a plugin's hello says."""
    return get((hello or {}).get("engine") if isinstance(hello, dict) else None)


def for_link(link):
    return for_hello(getattr(link, "hello", None))


def of(game):
    """The engine of a Game (or of anything with a link): Unreal for one that doesn't say."""
    eng = getattr(game, "engine", None)
    return eng if isinstance(eng, Engine) else for_link(getattr(game, "link", None))


def find_project(path, engine=None):
    """(engine, project) for a path the user gave: a project file or folder of any engine (or of `engine` alone).
    (None, None) when it holds none."""
    if not path or not os.path.exists(path):
        return None, None
    if not engine:  # (AUTOPILOT_ENGINE: one engine for every path; a name no engine has is ignored)
        chosen = (os.environ.get("AUTOPILOT_ENGINE") or "").strip().lower()
        engine = chosen if chosen in ENGINES else None
    for eng in ([get(engine)] if engine else ENGINES.values()):
        project = eng.project_at(path)
        if project:
            return eng, project
    return None, None
