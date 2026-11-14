"""Autopilot QA's look: the editor tab's theme (plugin/unreal/Autopilot/Source/AutopilotPlaytestEditor/Private/
AutopilotStyle.h: Aura's "The Flatness", dark only). Flat fills, 1px rules, one amber accent, square controls, IBM Plex
Sans and Mono (bundled in qa_fonts/ with their SIL Open Font Licenses, loaded for this process alone; elsewhere than
Windows and macOS, or when they can't be loaded, the system's own). Sizes are the design's px (Tk: negative sizes)."""

import os
import sys

FONTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "qa_fonts")
FONT_FILES = ("IBMPlexSans-Regular.ttf", "IBMPlexSans-Medium.ttf", "IBMPlexMono-Regular.ttf")

# AutopilotColor (AutopilotStyle.h)
GROUND, RAIL, PLATE, HOVER, ROW_ACTIVE, WELL = "#121316", "#0f1013", "#181a1e", "#1d2026", "#2a1b10", "#0d0e11"
RULE, LINE, LINE_HOVER = "#24262b", "#2c2f36", "#3a3e47"
TEXT, TEXT2, TEXT3, MUTED = "#e6e6e8", "#d3d5da", "#a4a8b0", "#8b8f98"
ACCENT, ACCENT_HOVER, ACCENT_LINE, ON_ACCENT, FOCUS = "#f0873a", "#f59b57", "#6b4122", "#141517", "#ffb066"
# A state's tag: (its color, its words, its surface, its outline)
TONES = {
    "success": ("#74b078", "#b6ddb9", "#16281a", "#3d6b42"),
    "red": ("#e26a62", "#f0b2ac", "#2c1715", "#7a3b36"),
    "amber": ("#e8a94e", "#f6dcae", "#2a2114", "#7a5c2a"),
    "info": ("#7aa5d8", "#bcd4ee", "#161f2b", "#3a5578"),
    "neutral": (MUTED, "#c9ccd2", HOVER, LINE),
}
RED_FILL = TONES["red"][2]


def faded(color, alpha=0.45, over=GROUND):
    """A color at alpha over the ground (a disabled control: 45%)."""
    a = [int(color[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(over[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x * alpha + y * (1 - alpha)):02x}" for x, y in zip(a, b))


def dot(root, color, size=8, box=14):
    """A square dot of color (the list's result mark), centred in a box-px transparent image."""
    import tkinter as tk

    img = tk.PhotoImage(master=root, width=box, height=box)
    at = (box - size) // 2
    img.put(color, to=(at, at, at + size, at + size))
    return img


def load_fonts():
    """Make the bundled fonts usable by this process (never installed for the user): whether it worked."""
    files = [os.path.join(FONTS, f) for f in FONT_FILES if os.path.isfile(os.path.join(FONTS, f))]
    if not files:
        return False
    try:
        if sys.platform == "win32":
            import ctypes

            FR_PRIVATE = 0x10
            add = ctypes.windll.gdi32.AddFontResourceExW
            return all(add(f, FR_PRIVATE, 0) for f in files)
        if sys.platform == "darwin":
            import ctypes
            import ctypes.util

            cf = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreFoundation"))
            ct = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreText"))
            cf.CFURLCreateFromFileSystemRepresentation.restype = ctypes.c_void_p
            cf.CFURLCreateFromFileSystemRepresentation.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long,
                                                                   ctypes.c_bool]
            ct.CTFontManagerRegisterFontsForURL.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p]
            ok = True
            for f in files:
                raw = f.encode("utf-8")
                url = cf.CFURLCreateFromFileSystemRepresentation(None, raw, len(raw), False)
                ok = bool(ct.CTFontManagerRegisterFontsForURL(url, 1, None)) and ok  # (1: this process alone)
            return ok
    except Exception:
        return False
    return False


class Fonts:
    """The theme's fonts as Tk font descriptions, at the design's px: body, medium (buttons, titles), mono (numbers,
    ids, the log), label (the micro-label: mono 10px, shown upper case)."""

    def __init__(self, root):
        from tkinter import font as tkfont

        load_fonts()
        families = set(tkfont.families(root))
        win, mac = sys.platform == "win32", sys.platform == "darwin"
        self.sans = "IBM Plex Sans" if "IBM Plex Sans" in families else (
            "Segoe UI" if win else "Helvetica Neue" if mac else "DejaVu Sans")
        self.mono = "IBM Plex Mono" if "IBM Plex Mono" in families else (
            "Consolas" if win else "Menlo" if mac else "DejaVu Sans Mono")
        # (Windows lists the weight as a family of its own: "IBM Plex Sans Medm")
        medium = sorted(f for f in families if f.startswith("IBM Plex Sans Med"))
        if medium:
            self.medium_face = (medium[0], "normal")
        else:
            self.medium_face = (self.sans, "bold" if self.sans != "IBM Plex Sans" else "normal")

    def body(self, px=13):
        return (self.sans, -px)

    def medium(self, px=13):
        family, weight = self.medium_face
        return (family, -px, weight)

    def mono_font(self, px=12):
        return (self.mono, -px)

    def label(self, px=10):
        return (self.mono, -px)


def dark_title_bar(root):
    """Windows: the window's title bar dark too (DWMWA_USE_IMMERSIVE_DARK_MODE); elsewhere nothing."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        on = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(on), ctypes.sizeof(on))
    except Exception:
        pass


def apply(root):
    """The ttk styles of the theme (on clam, the one ttk theme whose every color can be set): its Fonts."""
    from tkinter import ttk

    fonts = Fonts(root)
    root.configure(bg=GROUND)
    s = ttk.Style(root)
    s.theme_use("clam")
    s.configure(".", background=GROUND, foreground=TEXT, fieldbackground=WELL, bordercolor=LINE, lightcolor=GROUND,
                darkcolor=GROUND, troughcolor=GROUND, focuscolor=FOCUS, selectbackground=ROW_ACTIVE,
                selectforeground=TEXT, insertcolor=TEXT, font=fonts.body())

    def button(name, fill, outline, hover, ink, hover_ink=None, pad=(14, 5)):
        # (relief raised: clam draws a 1px border in bordercolor, its bevel in light/darkcolor, here the fill; solid
        # would draw 2px, flat none)
        s.configure(name, background=fill, foreground=ink, bordercolor=outline, lightcolor=fill, darkcolor=fill,
                    relief="raised", borderwidth=1, padding=pad, font=fonts.medium(13), focusthickness=1,
                    focuscolor=fill)
        s.map(name, background=[("disabled", faded(fill) if fill != GROUND else GROUND), ("active", hover)],
              lightcolor=[("disabled", faded(fill) if fill != GROUND else GROUND), ("active", hover)],
              darkcolor=[("disabled", faded(fill) if fill != GROUND else GROUND), ("active", hover)],
              foreground=[("disabled", faded(ink)), ("active", hover_ink or ink)],
              bordercolor=[("disabled", faded(outline) if outline != GROUND else GROUND), ("focus", FOCUS)],
              focuscolor=[("active", hover)])

    button("Autopilot.TButton", GROUND, LINE, HOVER, TEXT)  # (outlined)
    button("Primary.Autopilot.TButton", ACCENT, ACCENT, ACCENT_HOVER, ON_ACCENT)
    button("Danger.Autopilot.TButton", GROUND, GROUND, RED_FILL, TONES["red"][0])
    button("Quiet.Autopilot.TButton", GROUND, GROUND, HOVER, MUTED, TEXT)
    for bg, name in ((GROUND, "Autopilot.TCheckbutton"), (PLATE, "Plate.Autopilot.TCheckbutton")):
        s.configure(name, background=bg, foreground=TEXT2, font=fonts.body(13), indicatorbackground=WELL,
                    indicatorforeground=ACCENT, upperbordercolor=LINE_HOVER, lowerbordercolor=LINE_HOVER,
                    indicatormargin=(0, 0, 8, 0), focuscolor=bg, padding=(0, 2))
        s.map(name, background=[("active", bg)], indicatorbackground=[("selected", ROW_ACTIVE), ("active", HOVER)],
              upperbordercolor=[("selected", ACCENT_LINE)], lowerbordercolor=[("selected", ACCENT_LINE)])
    s.configure("Autopilot.Treeview", background=GROUND, fieldbackground=GROUND, foreground=TEXT2, bordercolor=RULE,
                lightcolor=GROUND, darkcolor=GROUND, rowheight=30, font=fonts.body(13), borderwidth=0)
    s.map("Autopilot.Treeview", background=[("selected", ROW_ACTIVE)], foreground=[("selected", TEXT)])
    s.layout("Autopilot.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])  # (no frame round it)
    s.configure("Autopilot.Treeview.Heading", background=GROUND, foreground=MUTED, bordercolor=RULE,
                lightcolor=GROUND, darkcolor=GROUND, relief="flat", font=fonts.label(10), padding=(8, 6))
    s.map("Autopilot.Treeview.Heading", background=[("active", GROUND)], foreground=[("active", TEXT)])
    s.configure("Autopilot.Vertical.TScrollbar", background=LINE, troughcolor=GROUND, bordercolor=GROUND,
                lightcolor=LINE, darkcolor=LINE, arrowcolor=MUTED, gripcount=0, arrowsize=12)
    s.map("Autopilot.Vertical.TScrollbar", background=[("active", LINE_HOVER)])
    s.configure("Autopilot.TPanedwindow", background=GROUND)
    s.configure("Sash", sashthickness=6, background=GROUND, lightcolor=GROUND, bordercolor=GROUND)
    return fonts
