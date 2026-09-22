"""Terminal output: colour, glyphs and wrapping, degrading to plain ASCII when piped."""

import os
import shutil
import sys
import textwrap

STYLES = {"bold": "1", "dim": "2", "red": "31", "green": "32", "yellow": "33", "cyan": "36"}

GLYPHS = {
    True: {"ok": "✓", "fail": "✗", "warn": "▲", "wait": "○", "arrow": "→"},
    False: {"ok": "+", "fail": "x", "warn": "!", "wait": "o", "arrow": "->"},
}


def _enable_windows_ansi():
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_ulong()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except (AttributeError, OSError):
        return False


def _supports_color(stream):
    if os.environ.get("NO_COLOR") or os.environ.get("TERM") == "dumb":
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    if not stream.isatty():
        return False
    return _enable_windows_ansi() if os.name == "nt" else True


class Console:
    def __init__(self, stream=None, color=None):
        self.stream = stream or sys.stdout
        self.color = _supports_color(self.stream) if color is None else color
        encoding = (getattr(self.stream, "encoding", None) or "").lower()
        self.unicode = encoding.startswith("utf")
        self.width = min(shutil.get_terminal_size((90, 24)).columns, 100)

    def paint(self, text, *styles):
        if not self.color or not styles:
            return text
        codes = ";".join(STYLES[name] for name in styles)
        return f"\033[{codes}m{text}\033[0m"

    def glyph(self, name):
        return GLYPHS[self.unicode][name]

    def line(self, text=""):
        print(text, file=self.stream, flush=True)

    def wrap(self, text, indent):
        return textwrap.wrap(text, width=self.width - indent, break_on_hyphens=False,
                             break_long_words=False) or [""]

    def wrapped(self, text, indent):
        for row in self.wrap(text, indent):
            self.line(" " * indent + row)
