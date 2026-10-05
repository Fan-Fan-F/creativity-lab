from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from creativity_lab.__main__ import main
from creativity_lab.providers import DemoProvider, ProviderError


class FailingPaidProvider(DemoProvider):
    demo = False
    identity = {"kind": "test", "model": "paid-fixture"}

    def complete(self, payload, budget):
        if budget.calls == 1:
            budget.reserve()
            raise ProviderError("sensitive upstream body must not be exported")
        return super().complete(payload, budget)


class CLITests(unittest.TestCase):
    def invoke(self, args):
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            return main(args)

    def test_failed_paid_run_is_saved_and_exit_is_unsuccessful(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "partial.json"
            with patch("creativity_lab.__main__.provider_factory", return_value=FailingPaidProvider), patch("creativity_lab.__main__.judge_factory", return_value=None):
                code = self.invoke(["run", "Design a reusable library service", "--output", str(output)])
            result = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(code, 1)
            self.assertEqual(result["status"], "provider_error")
            self.assertEqual(result["budget"]["calls"], 2)
            self.assertEqual(len(result["ideas"]), 1)
            self.assertNotIn("sensitive upstream body", output.read_text(encoding="utf-8"))

    def test_existing_user_output_survives_another_run(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "existing.json"
            output.write_text("original user result", encoding="utf-8")
            code = self.invoke(["run", "Design a reusable library service", "--demo", "--output", str(output)])
            self.assertEqual(code, 1)
            self.assertEqual(output.read_text(encoding="utf-8"), "original user result")


if __name__ == "__main__":
    unittest.main()
