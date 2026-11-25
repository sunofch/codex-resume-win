"""验证终端筛选、导航、布局以及异常退出后的恢复。"""

import argparse
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import codex_resume as app
from terminal_ui import Canvas, clip, wrap


ROWS = [{"id": str(index), "name": f"测试会话 {index}", "preview": "中文预览\n第二行",
         "cwd": "D:/project" if index < 12 else "D:/elsewhere", "updatedAt": index}
        for index in range(20)]


class FakeConsole:
    def __init__(self, events, size=(120, 30)):
        self.events, self.dimensions, self.frames = iter(events), size, []
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        self.closed = True

    def size(self):
        return self.dimensions

    def draw(self, frame):
        self.frames.append(frame)

    def read(self):
        event = next(self.events)
        if isinstance(event, BaseException):
            raise event
        return event


class TuiTests(unittest.TestCase):
    def picker(self):
        picker = app.Picker(ROWS, "", False, "D:/project")
        picker.render(120, 30)
        return picker

    def test_search_clear_and_scope(self):
        picker = self.picker()
        picker.handle("text", "测试会话 19")
        picker.render(120, 30)
        self.assertEqual(picker.visible, [])
        picker.handle("tab")
        picker.render(120, 30)
        self.assertEqual([row["id"] for row in picker.visible], ["19"])
        picker.handle("clear")
        picker.render(120, 30)
        self.assertEqual(len(picker.visible), 20)

    def test_navigation_pages_and_resize(self):
        picker = self.picker()
        picker.handle("pagedown")
        picker.render(120, 30)
        self.assertEqual(picker.selected, picker.page_size)
        picker.handle("end")
        picker.render(120, 30)
        self.assertEqual(picker.selected, 11)
        self.assertGreater(picker.start, 0)
        picker.render(60, 16)
        self.assertLessEqual(picker.selected, picker.start + picker.page_size - 1)
        picker.handle("home")
        picker.render(120, 30)
        self.assertEqual((picker.start, picker.selected), (0, 0))

    def test_mouse_selection_and_wheel(self):
        picker = self.picker()
        picker.handle("click", (4, 12))
        self.assertEqual(picker.selected, 1)
        picker.handle("wheel", 3)
        self.assertEqual(picker.selected, 4)
        picker.handle("click", (100, 12))
        self.assertEqual(picker.selected, 4)

    def test_wide_narrow_and_small_layouts(self):
        picker = self.picker()
        self.assertIn("会话详情", picker.render(120, 30))
        self.assertNotIn("会话详情", picker.render(80, 24))
        self.assertIn("请放大终端", picker.render(40, 12))

    def test_unicode_and_escape_sequences(self):
        self.assertEqual(clip("中文abc", 5), "中文a")
        self.assertEqual(wrap("中文abc", 4), ["中文", "abc"])
        self.assertEqual(clip("e\u0301x", 1), "e\u0301")
        canvas = Canvas(80, 24)
        canvas.put(0, 0, "标题\x1b[2J\n危险")
        self.assertIn("标题[2J危险", canvas.output())
        self.assertNotIn("标题\x1b", canvas.output())

    def test_choose_and_cleanup(self):
        console = FakeConsole([("text", "19"), ("tab", None), ("enter", None)])
        with patch.object(app, "WindowsConsole", return_value=console):
            result = app.choose(ROWS, argparse.Namespace(query="", all=False), "D:/project")
        self.assertEqual(result["id"], "19")
        self.assertTrue(console.closed)

    def test_escape_empty_and_interrupt(self):
        for events in [[("clear", None), ("escape", None)], [KeyboardInterrupt()]]:
            console = FakeConsole(events)
            with patch.object(app, "WindowsConsole", return_value=console):
                if isinstance(events[0], BaseException):
                    with self.assertRaises(KeyboardInterrupt):
                        app.choose([], argparse.Namespace(query="none", all=False), "D:/project")
                else:
                    self.assertIsNone(app.choose([], argparse.Namespace(query="none", all=False), "D:/project"))
            self.assertTrue(console.closed)


if __name__ == "__main__":
    unittest.main()
