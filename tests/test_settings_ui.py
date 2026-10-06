"""CI executes the shipped UI's handlers; Node is an optional test runtime."""
from pathlib import Path
import shutil
import subprocess
import unittest


class SettingsUIRuntimeTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node is unavailable; UI runtime test skipped")
    def test_settings_handlers_and_safe_poll_recovery(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [shutil.which("node"), str(root / "tests" / "ui_settings_smoke.js"),
             str(root / "creativity_lab" / "static" / "app.js")],
            cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("settings QA passed", result.stdout)
        html = (root / "creativity_lab" / "static" / "index.html").read_text(encoding="utf-8")
        self.assertIn("API JSON 模式", html)
        self.assertNotIn("严格 JSON", html)


if __name__ == "__main__":
    unittest.main()
