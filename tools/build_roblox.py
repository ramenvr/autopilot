"""Build the Roblox Studio plugin (plugin/roblox) as one model file, Autopilot.rbxmx: a Script that starts the plugin
and, under it, one ModuleScript per source file of plugin/roblox/src (Studio loads a plugin from a single .rbxmx in its
Plugins folder).

    python tools/build_roblox.py                    # dist/roblox/Autopilot.rbxmx
    python tools/build_roblox.py --install          # ... and copy it into Studio's Plugins folder
    python tools/build_roblox.py --out path/x.rbxmx

No Rojo or other tool is needed: the model file is XML written here.
"""

import argparse
import os
import shutil
import sys
from xml.sax.saxutils import escape

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "plugin", "roblox", "src")
ENTRY = "Main"  # the module whose start(plugin, options) the plugin's Script calls
NAME = "Autopilot"
BOOT = (f"-- Autopilot for Roblox Studio (plugin/roblox in https://github.com/ramenvr/autopilot)\n"
        f"require(script.{ENTRY}).start(plugin, {{}})\n")


def modules(src=SRC):
    """{module name: source} of the plugin's .luau files (the file's name without .luau; a .server.luau, a plugin's own
    script, is none)."""
    out = {}
    for f in sorted(os.listdir(src)):
        if f.endswith(".luau") and not f.endswith(".server.luau"):
            with open(os.path.join(src, f), encoding="utf-8") as fh:
                out[f[: -len(".luau")]] = fh.read()
    return out


def plugins_folder():
    """Studio's local plugins folder: %LOCALAPPDATA%\\Roblox\\Plugins on Windows, ~/Documents/Roblox/Plugins on
    macOS."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
        return os.path.join(base, "Roblox", "Plugins")
    return os.path.join(os.path.expanduser("~"), "Documents", "Roblox", "Plugins")


def _cdata(text):
    """Text as one or more CDATA sections (a "]]>" in the text splits them)."""
    return "<![CDATA[" + text.replace("]]>", "]]]]><![CDATA[>") + "]]>"


def _item(cls, name, source, ref, children=""):
    return (f'<Item class="{cls}" referent="{ref}"><Properties>'
            f'<string name="Name">{escape(name)}</string>'
            f'<ProtectedString name="Source">{_cdata(source)}</ProtectedString>'
            f"</Properties>{children}</Item>")


def model_xml(mods=None):
    """The plugin as .rbxmx text."""
    mods = modules() if mods is None else mods
    if ENTRY not in mods:
        raise SystemExit(f"no {ENTRY}.luau in {SRC}")
    children = "".join(_item("ModuleScript", name, src, f"RBX{i + 2}") for i, (name, src) in enumerate(mods.items()))
    return ('<roblox xmlns:xmime="http://www.w3.org/2005/05/xmlmime" version="4">'
            + _item("Script", NAME, BOOT, "RBX1", children) + "</roblox>\n")


def build(out):
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(model_xml())
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build the Roblox Studio plugin as Autopilot.rbxmx")
    ap.add_argument("--out", default=os.path.join(ROOT, "dist", "roblox", f"{NAME}.rbxmx"))
    ap.add_argument("--install", action="store_true", help="copy it into Studio's Plugins folder too")
    args = ap.parse_args(argv)
    out = build(args.out)
    print(f"Built {out}")
    if args.install:
        dest = os.path.join(plugins_folder(), f"{NAME}.rbxmx")
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copyfile(out, dest)
        print(f"Installed {dest} (Studio loads it when a place opens)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
