"""在隐藏的新控制台中验证真实 Win32 输入、绘制和模式恢复。"""

import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import codex_resume as app
from terminal_ui import COORD, INPUT_RECORD, WindowsConsole


def child(report, launcher=None):
    result = {}
    try:
        console = WindowsConsole()
        api = console.api
        api.WriteConsoleInputW.argtypes = [wintypes.HANDLE, ctypes.POINTER(INPUT_RECORD),
                                          wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        api.ReadConsoleOutputCharacterW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR,
                                                   wintypes.DWORD, COORD, ctypes.POINTER(wintypes.DWORD)]

        def modes():
            values = []
            for handle in (console.input, console.output):
                mode = wintypes.DWORD()
                console.check(api.GetConsoleMode(handle, ctypes.byref(mode)))
                values.append(mode.value)
            return values

        before = modes()
        with console:
            console.draw(app.Picker([], "", True, os.getcwd()).render(*console.size()))
            width, height = console.size()
            buffer = ctypes.create_unicode_buffer(width * height + 1)
            count = wintypes.DWORD()
            console.check(api.ReadConsoleOutputCharacterW(console.output, buffer, width * height,
                                                         COORD(0, 0), ctypes.byref(count)))
            assert "CODEX" in buffer.value, "终端未绘制预期标题"
        assert modes() == before, "绘制后未恢复模式"
        rows = [{"id": str(i), "name": f"中文测试 {i}", "cwd": os.getcwd() if i < 12 else "D:/other",
                 "updatedAt": i, "preview": "预览内容"} for i in range(20)]

        def queue_keys(keys):
            records = (INPUT_RECORD * len(keys))()
            for record, (virtual_key, char) in zip(records, keys):
                record.kind = 1
                record.event.key.down = True
                record.event.key.repeat = 1
                record.event.key.virtual_key = virtual_key
                record.event.key.char = char
            written = wintypes.DWORD()
            console.check(api.WriteConsoleInputW(console.input, records, len(records), ctypes.byref(written)))
            assert written.value == len(records)

        queue_keys([(0, "中"), (0x08, "\b"), (0, "1"), (0, "9"), (0x09, "\t"), (0x0D, "\r")])
        with patch.object(app, "WindowsConsole", return_value=console):
            selected = app.choose(rows, argparse.Namespace(query="", all=False), os.getcwd())
        assert selected["id"] == "19", "搜索或 Tab 切换失败"
        assert modes() == before, "选择后未恢复模式"
        queue_keys([(0x28, "\x00"), (0x28, "\x00"), (0x0D, "\r")])
        with patch.object(app, "WindowsConsole", return_value=console):
            selected = app.choose(rows, argparse.Namespace(query="", all=False), os.getcwd())
        assert selected["id"] == "2", "方向键选择失败"
        queue_keys([(0x1B, "\x1b")])
        with patch.object(app, "WindowsConsole", return_value=console):
            assert app.choose(rows, argparse.Namespace(query="", all=True), os.getcwd()) is None
        assert modes() == before, "退出后未恢复模式"
        result = {"ok": True, "checks": ["ANSI 绘制", "中文输入与退格", "搜索", "Tab 目录切换", "方向键", "Enter", "Esc", "控制台模式恢复"]}
        if launcher:
            # CMD/Conda 的启动初始化也会改变模式，用无 TUI 的 help 建立基线。
            entry = [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", str(Path(launcher).resolve())]
            help_run = subprocess.run([*entry, "--help"], timeout=45)
            assert help_run.returncode == 0, "命令帮助入口失败"
            launcher_modes = modes()
            # 真实入口只运行 dry-run，回车后打印命令，不进入任何会话。
            queue_keys([(0x0D, "\r")])
            launched = subprocess.run([*entry, "--all", "--dry-run"], timeout=45)
            assert launched.returncode == 0, "真实命令入口失败"
            assert modes() == launcher_modes, f"真实入口控制台模式变化：{launcher_modes} -> {modes()}"
            result["launcher_modes"] = {"fresh_console": before, "help": launcher_modes, "tui_exit": modes()}
            result["checks"].append("真实 .cmd 入口、会话读取和 dry-run")
    except BaseException as error:
        import traceback
        result = {"ok": False, "error": str(error), "traceback": traceback.format_exc()}
    Path(report).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return 0 if result["ok"] else 1


def main():
    if len(sys.argv) >= 3 and sys.argv[1] == "--child":
        return child(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
    if os.name != "nt":
        raise RuntimeError("此检查仅适用于 Windows。")
    with tempfile.TemporaryDirectory(prefix="codex-tui-smoke-") as directory:
        report = Path(directory) / "report.json"
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0
        command = [sys.executable, str(Path(__file__).resolve()), "--child", str(report)]
        if len(sys.argv) == 3 and sys.argv[1] == "--launcher":
            command.append(sys.argv[2])
        process = subprocess.Popen(command,
                                   creationflags=subprocess.CREATE_NEW_CONSOLE, startupinfo=startup)
        try:
            code = process.wait(timeout=90)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            raise RuntimeError("控制台检查超时。")
        result = json.loads(report.read_text(encoding="utf-8"))
        print(json.dumps(result, ensure_ascii=False))
        return code


if __name__ == "__main__":
    sys.exit(main())
