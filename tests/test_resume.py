"""验证协议、Windows 路径处理及恢复参数，避免启动真实会话。"""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import codex_resume as app


class ResumeTests(unittest.TestCase):
    def server(self, mode):
        return [sys.executable, str(Path(__file__).with_name("fake_server.py")), mode]

    def test_pagination_and_deduplication(self):
        self.assertEqual([item["id"] for item in app.load_threads(self.server("ok"))], ["b", "a"])

    def test_repeated_cursor(self):
        with self.assertRaisesRegex(RuntimeError, "重复"):
            app.load_threads(self.server("loop"))

    def test_rpc_error(self):
        with self.assertRaisesRegex(RuntimeError, "接口错误"):
            app.load_threads(self.server("error"))

    def test_early_exit(self):
        with self.assertRaisesRegex(RuntimeError, "模拟启动错误"):
            app.load_threads(self.server("exit"))

    @unittest.skipUnless(sys.platform == "win32", "验证 Windows 路径规则")
    def test_directory_and_search(self):
        rows = [{"id": "x", "name": "中文 API", "cwd": "D:/Project/"},
                {"id": "y", "name": "中文 API", "cwd": "D:/Project/sub"}]
        self.assertEqual(app.matches(rows, "中文 api", "d:\\project"), rows[:1])
        self.assertEqual(app.matches(rows, "missing"), [])

    def test_resume_arguments_preserve_special_characters(self):
        with tempfile.TemporaryDirectory(prefix="中文 & ") as directory:
            row = {"id": "session-id", "cwd": directory}
            self.assertEqual(app.resume_command(["node", "codex.js"], row, "."),
                             ["node", "codex.js", "resume", "session-id", "--cd", directory])
            self.assertEqual(app.resume_command(["codex"], row, directory, True)[-1], directory)

    def test_missing_directory_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            row = {"id": "x", "cwd": str(Path(directory) / "missing")}
            self.assertEqual(app.resume_command(["codex"], row, directory)[-1], directory)

    def test_npm_shim_uses_node(self):
        with tempfile.TemporaryDirectory() as directory:
            shim = Path(directory) / "codex.cmd"
            shim.touch()
            entry = Path(directory) / "node_modules/@openai/codex/bin/codex.js"
            entry.parent.mkdir(parents=True)
            entry.touch()
            with patch.object(app.shutil, "which", return_value="node.exe"):
                self.assertEqual(app.resolve_codex(str(shim)), ["node.exe", str(entry)])


if __name__ == "__main__":
    unittest.main()
