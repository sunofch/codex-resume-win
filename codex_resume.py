# SPDX-License-Identifier: MIT
# Copyright (c) 2026 sunofch
"""原生 Windows Codex 本地会话选择器，仅使用 Python 标准库。"""

import argparse
import datetime
import json
import ntpath
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from terminal_ui import Canvas, WindowsConsole, clip, preserve_console_modes, wrap

__version__ = "0.1.0"


def resolve_codex(explicit=None):
    """优先通过 Node 直接运行 npm 入口，避免批处理转义用户路径。"""
    found = explicit or shutil.which("codex.exe") or shutil.which("codex.cmd") or shutil.which("codex")
    if not found:
        raise RuntimeError("找不到 Codex，请先安装并将其加入 PATH。")
    path = Path(found).resolve()
    if path.suffix.lower() in {".cmd", ".bat", ".ps1"}:
        entry = path.parent / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
        node = shutil.which("node.exe") or shutil.which("node")
        if entry.is_file() and node:
            return [node, str(entry)]
        raise RuntimeError("此 Codex 包装脚本无法直接启动，请用 --codex 指定 codex.exe。")
    return [str(path)]


def load_threads(command, timeout=30):
    """分页读取官方接口；每次 RPC 均有超时，并确保关闭子进程。"""
    errors = tempfile.TemporaryFile()
    try:
        process = subprocess.Popen(
            [*command, "app-server", "--stdio"], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=errors,
            text=True, encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except BaseException:
        errors.close()
        raise
    messages = queue.Queue()

    def read():
        try:
            for line in process.stdout:
                try:
                    messages.put(json.loads(line))
                except ValueError:
                    continue
        finally:
            messages.put(None)

    reader = threading.Thread(target=read, daemon=True)
    reader.start()

    def send(message):
        process.stdin.write(json.dumps(message) + "\n")
        process.stdin.flush()

    def call(number, method, params):
        send({"id": number, "method": method, "params": params})
        deadline = time.monotonic() + timeout
        while True:
            try:
                message = messages.get(timeout=max(0, deadline - time.monotonic()))
            except queue.Empty as error:
                raise RuntimeError("读取会话超时，请检查 Codex app-server。") from error
            if message is None:
                errors.seek(0)
                detail = errors.read().decode("utf-8", errors="replace").strip()[-2000:]
                raise RuntimeError("Codex app-server 提前退出。\n" + detail)
            if message.get("id") == number:
                if "error" in message:
                    raise RuntimeError("Codex 接口错误：" + str(message["error"]))
                return message["result"]

    try:
        call(1, "initialize", {"clientInfo": {"name": "codex_resume_win", "version": __version__},
                               "capabilities": {"experimentalApi": True}})
        send({"method": "initialized"})
        params = {"currentAccountOnly": False, "modelProviders": [], "limit": 100,
                  "sortKey": "updated_at", "sortDirection": "desc"}
        threads, cursors = {}, set()
        number = 2
        while True:
            result = call(number, "thread/list", params)
            for thread in result.get("data", []):
                threads.setdefault(thread["id"], thread)
            cursor = result.get("nextCursor")
            if not cursor:
                return sorted(threads.values(), key=lambda item: item.get("updatedAt", 0), reverse=True)
            if cursor in cursors:
                raise RuntimeError("接口返回了重复的分页游标。")
            cursors.add(cursor)
            params["cursor"] = cursor
            number += 1
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        reader.join(timeout=1)
        process.stdin.close()
        process.stdout.close()
        errors.close()


def clean(value):
    return " ".join(str(value or "").split())


def title(thread):
    return clean(thread.get("name") or thread.get("preview") or thread["id"])


def normalize_path(path):
    return ntpath.normcase(ntpath.normpath(path)) if os.name == "nt" else os.path.normpath(path)


def matches(threads, query="", cwd=None):
    words = query.casefold().split()
    return [item for item in threads
            if (cwd is None or normalize_path(item.get("cwd") or "") == normalize_path(cwd))
            and all(word in (title(item) + " " + (item.get("cwd") or "") + " " + item["id"]).casefold()
                    for word in words)]


def timestamp(thread):
    return datetime.datetime.fromtimestamp(thread.get("updatedAt", 0)).strftime("%Y-%m-%d %H:%M")


def resume_command(command, thread, launch_cwd, here=False):
    directory = launch_cwd if here else thread.get("cwd") or launch_cwd
    if not os.path.isdir(directory):
        print(f"原目录不存在：{directory}；改用当前目录：{launch_cwd}", file=sys.stderr)
        directory = launch_cwd
    return [*command, "resume", thread["id"], "--cd", directory]


def choose(threads, args, cwd):
    """使用备用屏幕；退出界面后由原终端执行恢复命令。"""
    picker = Picker(threads, args.query, args.all, cwd)
    with WindowsConsole() as console:
        while True:
            width, height = console.size()
            console.draw(picker.render(width, height))
            key, value = console.read()
            if key == "escape":
                return None
            if width < 42 or height < 16:
                continue
            if key == "enter" and picker.visible:
                return picker.visible[picker.selected]
            picker.handle(key, value)


class Picker:
    def __init__(self, threads, query, all_dirs, cwd):
        self.threads, self.query, self.all_dirs, self.cwd = threads, query, all_dirs, cwd
        self.selected, self.start, self.page_size = 0, 0, 1
        self.visible = []
        self.layout = None

    def render(self, width, height):
        canvas = Canvas(width, height)
        self.visible = matches(self.threads, self.query, None if self.all_dirs else self.cwd)
        self.selected = max(0, min(self.selected, len(self.visible) - 1))
        self.layout = None
        if width < 42 or height < 16:
            canvas.put(0, 0, "请放大终端至至少 42 列 × 16 行", "hint")
            canvas.put(2, 0, "Esc 退出 · 调整窗口后自动恢复", "muted")
            return canvas.output()
        canvas.put(1, 2, "◈ CODEX / 会话书签", "accent")
        scope = "全部目录" if self.all_dirs else "当前目录"
        if width >= 70:
            canvas.put(1, width - 30, "所有账号 · " + scope, "muted")
        canvas.put(2, 2, "继续上次的思考。", "muted")
        canvas.box(4, 1, 3, width - 3, "搜索", True)
        search = self.query
        while search and clip(search, width - 10) != search:
            search = search[1:]
        canvas.put(5, 3, search + "▏" if self.query else "输入标题、目录或会话 ID…", limit=width - 9)
        top, panel_height = 8, height - 11
        split = width >= 100
        left_width = int((width - 4) * .57) if split else width - 3
        canvas.box(top, 1, panel_height, left_width, f"{scope} · {len(self.visible)} 条")
        stride = 3 if height >= 23 else 1
        self.page_size = max(1, (panel_height - 2) // stride)
        self.start = max(0, min(self.start, self.selected))
        if self.selected >= self.start + self.page_size:
            self.start = self.selected - self.page_size + 1
        self.start = min(self.start, max(0, len(self.visible) - self.page_size))
        self.layout = (top, panel_height, left_width, stride)
        for index, item in enumerate(self.visible[self.start:self.start + self.page_size], self.start):
            row = top + 1 + (index - self.start) * stride
            active = index == self.selected
            style = "selected" if active else "base"
            for offset in range(2 if stride == 3 else 1):
                canvas.put(row + offset, 2, " " * (left_width - 2), style)
            canvas.put(row, 3, ("› " if active else "  ") + title(item), style, left_width - 5)
            if stride == 3:
                directory = ntpath.basename((item.get("cwd") or "").rstrip("\\/")) or item.get("cwd") or "未知目录"
                canvas.put(row + 1, 5, timestamp(item) + " · " + directory,
                           style if active else "muted", left_width - 7)
        if not self.visible:
            canvas.put(top + 2, 4, "没有匹配的会话", "hint", left_width - 5)
            canvas.put(top + 3, 4, "Tab 切换目录 · Ctrl+U 清空", "muted", left_width - 5)
        if split:
            right = left_width + 2
            detail_width = width - right - 2
            canvas.box(top, right, panel_height, detail_width, "会话详情")
            if self.visible:
                item = self.visible[self.selected]
                lines = [(title(item), "base", 2), ("", "base", 1), ("工作目录", "accent", 1),
                         (item.get("cwd") or "未知目录", "muted", 2),
                         ("最近更新 " + timestamp(item), "muted", 1),
                         ("ID " + item["id"], "muted", 2), ("", "base", 1),
                         ("对话预览", "accent", 1), (item.get("preview") or "暂无预览", "muted", panel_height)]
                row = top + 1
                for text, style, maximum in lines:
                    for line in (wrap(text, detail_width - 4) or [""])[:maximum]:
                        if row < top + panel_height - 1:
                            canvas.put(row, right + 2, line, style, detail_width - 4)
                            row += 1
        elif self.visible:
            canvas.put(height - 3, 2, clean(self.visible[self.selected].get("cwd")), "muted")
        canvas.put(height - 2, 2, "↑↓ 选择  Enter 恢复  Tab 目录  Esc 退出", "accent")
        if width >= 100:
            canvas.put(height - 2, width - 36, f"{self.selected + 1 if self.visible else 0}/{len(self.visible)} · Ctrl+U 清空", "muted")
        return canvas.output()

    def handle(self, key, value=None):
        if key in {"up", "down", "pageup", "pagedown", "wheel"}:
            step = {"up": -1, "down": 1, "pageup": -self.page_size,
                    "pagedown": self.page_size, "wheel": value or 0}[key]
            self.selected += step
        elif key == "home":
            self.selected = 0
        elif key == "end":
            self.selected = len(self.visible) - 1
        elif key == "tab":
            self.all_dirs, self.selected, self.start = not self.all_dirs, 0, 0
        elif key == "backspace":
            self.query, self.selected = self.query[:-1], 0
        elif key == "clear":
            self.query, self.selected = "", 0
        elif key == "text":
            self.query, self.selected = self.query + value, 0
        elif key == "click" and self.layout:
            top, panel_height, left_width, stride = self.layout
            x, y = value
            if 1 < x < left_width and top < y < top + panel_height - 1:
                offset = (y - top - 1) // stride
                index = self.start + offset
                if offset < self.page_size and index < len(self.visible):
                    self.selected = index
        self.selected = max(0, min(self.selected, len(self.visible) - 1))


def main(argv=None):
    with preserve_console_modes():
        return run(argv)


def run(argv=None):
    parser = argparse.ArgumentParser(prog="codex-resume", description="Windows Codex 跨账号本地会话选择器（TUI）")
    parser.add_argument("--version", action="version", version="%(prog)s " + __version__)
    parser.add_argument("query", nargs="?", default="")
    parser.add_argument("--all", action="store_true", help="显示全部目录，默认仅当前目录")
    parser.add_argument("--here", action="store_true", help="在当前目录恢复")
    parser.add_argument("--list", action="store_true", help="打印列表，不进入 TUI")
    parser.add_argument("--dry-run", action="store_true", help="选择后只打印命令")
    parser.add_argument("--codex", help="指定 Codex 可执行文件路径")
    args = parser.parse_args(argv)
    command = resolve_codex(args.codex)
    cwd = os.getcwd()
    if not args.list and not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise RuntimeError("请在交互终端运行，或用 --list 打印列表。")
    if not args.list:
        print("正在读取本地 Codex 会话…", file=sys.stderr)
    threads = load_threads(command)
    if args.list:
        for item in matches(threads, args.query, None if args.all else cwd):
            print(f"{item['id']}\t{timestamp(item)}\t{title(item)}\t{clean(item.get('cwd'))}")
        return 0
    selected = choose(threads, args, cwd)
    if selected is None:
        return 0
    launch = resume_command(command, selected, cwd, args.here)
    if args.dry_run:
        print(subprocess.list2cmdline(launch))
        return 0
    return subprocess.call(launch)


def cli():
    try:
        return main()
    except KeyboardInterrupt:
        return 130
    except (RuntimeError, OSError) as error:
        print(f"codex-resume: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(cli())
