# SPDX-License-Identifier: MIT
# Copyright (c) 2026 sunofch
"""Windows 控制台输入与 ANSI 绘制，不依赖 curses。"""

import ctypes
from ctypes import wintypes
from contextlib import contextmanager
import os
import sys
import unicodedata


def cell_width(char):
    if unicodedata.category(char).startswith("C"):
        return 0
    if unicodedata.combining(char) or unicodedata.category(char) in {"Mn", "Me"}:
        return 0
    return 2 if unicodedata.east_asian_width(char) in "WF" else 1


def clip(text, width):
    result, used = [], 0
    for char in str(text):
        if unicodedata.category(char).startswith("C"):
            continue
        size = cell_width(char)
        if used + size > width:
            break
        result.append(char)
        used += size
    return "".join(result)


def wrap(text, width):
    if width <= 0:
        return []
    result, line, used = [], "", 0
    for char in str(text):
        if char == "\n":
            result.append(line)
            line, used = "", 0
            continue
        if unicodedata.category(char).startswith("C"):
            continue
        size = cell_width(char)
        if used + size > width:
            result.append(line)
            line, used = "", 0
        if size <= width:
            line += char
            used += size
    if line:
        result.append(line)
    return result


STYLES = {
    "base": "\x1b[38;5;252;48;5;234m",
    "muted": "\x1b[38;5;245;48;5;234m",
    "accent": "\x1b[38;5;80;48;5;234m",
    "border": "\x1b[38;5;239;48;5;234m",
    "selected": "\x1b[1;38;5;255;48;5;24m",
    "hint": "\x1b[38;5;222;48;5;234m",
}


class Canvas:
    def __init__(self, width, height):
        # 保留最后一列，避免自动换行导致终端滚屏。
        self.width, self.height = max(0, width - 1), max(0, height)
        self.commands = ["\x1b[H", STYLES["base"], "\x1b[2J"]

    def put(self, row, col, text, style="base", limit=None):
        if not (0 <= row < self.height and 0 <= col < self.width):
            return
        width = self.width - col
        if limit is not None:
            width = min(width, max(0, limit))
        value = clip(text, width)
        self.commands.append(f"\x1b[{row + 1};{col + 1}H\x1b[0m{STYLES[style]}{value}")

    def box(self, top, left, height, width, label="", accent=False):
        if height < 2 or width < 2:
            return
        style = "accent" if accent else "border"
        self.put(top, left, "╭" + "─" * (width - 2) + "╮", style)
        for row in range(top + 1, top + height - 1):
            self.put(row, left, "│", style)
            self.put(row, left + width - 1, "│", style)
        self.put(top + height - 1, left, "╰" + "─" * (width - 2) + "╯", style)
        self.put(top, left + 2, " " + label + " ", "accent", width - 4)

    def output(self):
        return "".join(self.commands) + "\x1b[0m"


class COORD(ctypes.Structure):
    _fields_ = [("X", wintypes.SHORT), ("Y", wintypes.SHORT)]


class SMALL_RECT(ctypes.Structure):
    _fields_ = [(name, wintypes.SHORT) for name in ("Left", "Top", "Right", "Bottom")]


class CONSOLE_SCREEN_BUFFER_INFO(ctypes.Structure):
    _fields_ = [("size", COORD), ("cursor", COORD), ("attributes", wintypes.WORD),
                ("window", SMALL_RECT), ("maximum", COORD)]


class KEY_EVENT_RECORD(ctypes.Structure):
    _fields_ = [("down", wintypes.BOOL), ("repeat", wintypes.WORD),
                ("virtual_key", wintypes.WORD), ("scan", wintypes.WORD),
                ("char", wintypes.WCHAR), ("control", wintypes.DWORD)]


class MOUSE_EVENT_RECORD(ctypes.Structure):
    _fields_ = [("position", COORD), ("buttons", wintypes.DWORD),
                ("control", wintypes.DWORD), ("flags", wintypes.DWORD)]


class EVENT_UNION(ctypes.Union):
    _fields_ = [("key", KEY_EVENT_RECORD), ("mouse", MOUSE_EVENT_RECORD),
                ("size", COORD), ("padding", ctypes.c_byte * 16)]


class INPUT_RECORD(ctypes.Structure):
    _fields_ = [("kind", wintypes.WORD), ("event", EVENT_UNION)]


KEYS = {0x26: "up", 0x28: "down", 0x21: "pageup", 0x22: "pagedown",
        0x24: "home", 0x23: "end", 0x0D: "enter", 0x1B: "escape",
        0x09: "tab", 0x08: "backspace"}


class WindowsConsole:
    """退出或异常时恢复输入输出模式、光标和原屏幕。"""

    def __init__(self):
        if os.name != "nt":
            raise RuntimeError("此 TUI 面向原生 Windows 控制台。")
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.api.GetStdHandle.argtypes = [wintypes.DWORD]
        self.api.GetStdHandle.restype = wintypes.HANDLE
        self.api.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        self.api.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.api.GetConsoleScreenBufferInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(CONSOLE_SCREEN_BUFFER_INFO)]
        self.api.ReadConsoleInputW.argtypes = [wintypes.HANDLE, ctypes.POINTER(INPUT_RECORD),
                                             wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        self.input = self.api.GetStdHandle(-10 & 0xffffffff)
        self.output = self.api.GetStdHandle(-11 & 0xffffffff)
        self.saved = []
        self.pending_surrogate = ""

    @staticmethod
    def check(result):
        if not result:
            raise ctypes.WinError(ctypes.get_last_error())

    def __enter__(self):
        try:
            for handle, output in ((self.input, False), (self.output, True)):
                mode = wintypes.DWORD()
                self.check(self.api.GetConsoleMode(handle, ctypes.byref(mode)))
                # 输入禁用行缓冲、回显、快速编辑和 VT 输入，启用鼠标及窗口事件。
                new = (mode.value | 0x0004) if output else ((mode.value | 0x0098) & ~0x0267)
                self.saved.append((handle, mode.value))
                self.check(self.api.SetConsoleMode(handle, new))
            self.draw("\x1b[?1049h\x1b[?25l")
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *unused):
        try:
            if self.saved:
                self.draw("\x1b[0m\x1b[?25h\x1b[?1049l")
        finally:
            for handle, mode in reversed(self.saved):
                self.api.SetConsoleMode(handle, mode)
            self.saved.clear()

    def size(self):
        info = CONSOLE_SCREEN_BUFFER_INFO()
        self.check(self.api.GetConsoleScreenBufferInfo(self.output, ctypes.byref(info)))
        return info.window.Right - info.window.Left + 1, info.window.Bottom - info.window.Top + 1

    def draw(self, text):
        sys.stdout.write(text)
        sys.stdout.flush()

    def read(self):
        while True:
            record, count = INPUT_RECORD(), wintypes.DWORD()
            self.check(self.api.ReadConsoleInputW(self.input, ctypes.byref(record), 1, ctypes.byref(count)))
            if record.kind == 4:
                return "resize", None
            if record.kind == 2:
                mouse = record.event.mouse
                if mouse.flags == 4:
                    return "wheel", -3 if ctypes.c_short(mouse.buttons >> 16).value > 0 else 3
                if mouse.flags in (0, 2) and mouse.buttons & 1:
                    info = CONSOLE_SCREEN_BUFFER_INFO()
                    self.check(self.api.GetConsoleScreenBufferInfo(self.output, ctypes.byref(info)))
                    return "click", (mouse.position.X - info.window.Left, mouse.position.Y - info.window.Top)
                continue
            if record.kind != 1 or not record.event.key.down:
                continue
            key = record.event.key
            if key.virtual_key in KEYS:
                return KEYS[key.virtual_key], None
            if key.char in ("\x03", "\x15"):
                return {"\x03": "escape", "\x15": "clear"}[key.char], None
            char = key.char
            if not char or char == "\x00":
                continue
            if 0xD800 <= ord(char) <= 0xDBFF:
                self.pending_surrogate = char
                continue
            if 0xDC00 <= ord(char) <= 0xDFFF:
                if not self.pending_surrogate:
                    continue
                char = (self.pending_surrogate + char).encode("utf-16", "surrogatepass").decode("utf-16")
            self.pending_surrogate = ""
            if char.isprintable():
                return "text", char * max(1, key.repeat)


@contextmanager
def preserve_console_modes():
    """同时保护读取会话期间 Node/Codex 子进程可能改变的终端模式。"""
    saved = []
    console = WindowsConsole() if os.name == "nt" else None
    if console:
        for handle in (console.input, console.output):
            mode = wintypes.DWORD()
            # --list 可通过管道运行，非控制台句柄无需恢复。
            if console.api.GetConsoleMode(handle, ctypes.byref(mode)):
                saved.append((handle, mode.value))
    try:
        yield
    finally:
        for handle, mode in reversed(saved):
            console.api.SetConsoleMode(handle, mode)
