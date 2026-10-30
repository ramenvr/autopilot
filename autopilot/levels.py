"""The level a run plays: the editor's own (what its Play button plays), unless the run names another (its start
frame's `level`), or the agent moves to the one the request means (restart_level, with a level from its briefing's
list: no words of the prompt are matched here). The plugins list the project's levels (the `levels` op,
docs/protocol.md) and play the one asked for without opening it in the editor (Unreal's Play In Editor map override,
Unity's play mode start scene, Godot's play_custom_scene): what is open there, unsaved changes and all, stays as it
is."""

import re
from dataclasses import dataclass, field

FILE_ENDINGS = (".umap", ".unity", ".tscn", ".scn")
BRIEF_LEVELS = 200  # levels the agent's briefing lists


@dataclass
class Level:
    """The level a run plays, as cli.prepare chose it."""

    levels: list = field(default_factory=list)  # the project's (the plugin's `levels`; [] from a plugin without it)
    open: str = ""  # the editor's own level: what a start without a map plays (Godot: the main scene)
    playing: str = ""  # the level the game plays (as the plugin names it; "" when it doesn't say)
    asked: str = ""  # the level the run asked for (its start frame's `level`), as the list names it
    hint: str = ""  # the run's `level` when it names none of the list's ("the first person one"): the agent decides
    playing_asked: bool = False  # the game plays the level asked for
    failed: str = ""  # why it couldn't (the run plays the open level)


def short(path):
    """A level's own name: /Game/Maps/Lvl_Harbor(.Lvl_Harbor) -> Lvl_Harbor, Assets/Scenes/Arena.unity -> Arena,
    res://levels/arena.tscn -> arena. A PIE world's prefix goes too (UEDPIE_0_Arena -> Arena)."""
    name = re.split(r"[/\:]", str(path or "").strip())[-1]
    name = name.split(".")[0] if not name.lower().endswith(FILE_ENDINGS) else name.rsplit(".", 1)[0]
    return re.sub(r"^UEDPIE_\d+_", "", name)


def package(path):
    """A level's path as the plugins list it, for comparing: /Game/Maps/UEDPIE_0_Arena.Arena -> /game/maps/arena."""
    path = str(path or "").strip().replace("\\", "/")
    head, _, tail = path.rpartition("/")
    tail = re.sub(r"^UEDPIE_\d+_", "", tail)
    if not tail.lower().endswith(FILE_ENDINGS):
        tail = tail.split(".")[0]
    return f"{head}/{tail}".lower() if head else tail.lower()


def same(a, b):
    """Whether two paths name the same level."""
    return bool(a) and bool(b) and package(a) == package(b)


def resolve(name, levels):
    """The level of `levels` (paths) that `name` is: its path, or its own name when no other level shares it (any
    case: "Lvl_Harbor", "lvl_harbor.umap"). None when the list has no such level; `name` itself when there is no list
    (a plugin from before `levels`: it finds the level, or says it can't)."""
    name = str(name or "").strip()
    if not name:
        return None
    if not levels:
        return name
    for path in levels:
        if same(path, name):
            return path
    by_name = [p for p in levels if short(p).lower() == short(name).lower()]
    return by_name[0] if len(by_name) == 1 else None


def listing(answer):
    """A `levels` answer's level paths, open level and playing level ([], "", "" from a plugin without the op)."""
    answer = answer if isinstance(answer, dict) else {}
    levels = [str(p) for p in answer.get("levels") or [] if isinstance(p, str) and p.strip()]
    return levels, str(answer.get("open") or ""), str(answer.get("playing") or "")


def briefing(level):
    """The agent's briefing on the level (cli.prepare's Level): which plays, the project's others, and that the agent
    decides whether the request means another."""
    if not level or not (level.levels or level.playing):
        return None
    own = "the editor's own level (the one its Play button plays)"
    if level.asked and not level.playing_asked:
        why = f"the run asked for {level.asked}, which couldn't be played ({level.failed}): this is {own}"
    elif level.asked:
        why = "the run was asked to play it"
    else:
        why = own
    out = [f"This run plays {level.playing or level.open or own}: {why}."]
    if level.levels:
        shown = level.levels[:BRIEF_LEVELS]
        more = f" (and {len(level.levels) - len(shown)} more)" if len(level.levels) > len(shown) else ""
        out.append(f"The project's levels ({len(level.levels)}): " + ", ".join(shown) + more + ".")
    if level.hint:
        out.append(f"The run asked for the level \"{level.hint}\", which is none of the list's by that name: it "
                   "means one of them (or, if none fits, play on in this one and say so in the report).")
    if level.levels and not level.asked:
        out.append("Your first decision: does the request mean a level other than this one (by its name, or by "
                   "describing it: \"the harbor\", \"the 2D one\", \"level 3\")? If so, call restart_level with "
                   "`level` set to that level's path from the list before anything else. If it names none, or the "
                   "level it means is the one playing, play on here.")
    out.append("restart_level with `level` plays that level from then on, and every restart (run_harness's too) "
               "starts it over. A harness's start_map, when set, still wins.")
    return "## The level\n" + "\n".join(out)
