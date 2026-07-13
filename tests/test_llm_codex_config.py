import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import llm  # noqa: E402


class CodexConfigTests(unittest.TestCase):
    def test_codex_command_pins_requested_model_and_effort(self):
        captured = {}

        def fake_run(command, **_kwargs):
            captured["command"] = command
            Path(command[command.index("-o") + 1]).write_text("OK", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, "", "")

        with mock.patch.object(llm.subprocess, "run", side_effect=fake_run):
            result = llm.run_llm("codex", "test", codex_model="gpt-5.6-luna",
                                 codex_reasoning_effort="xhigh")

        self.assertTrue(result.ok)
        self.assertEqual(captured["command"][captured["command"].index("--model") + 1], "gpt-5.6-luna")
        self.assertIn('model_reasoning_effort="xhigh"', captured["command"])

    def test_codex_command_rejects_unknown_reasoning_effort(self):
        with self.assertRaisesRegex(llm.LLMError, "invalid Codex reasoning effort"):
            llm.run_llm("codex", "test", codex_reasoning_effort="extra-high")
