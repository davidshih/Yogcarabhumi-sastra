"""The Claude Opus 5.5 backend of the legacy audit (`audited.py audit --model opus`)."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import audited  # noqa: E402
import build_translation_html as bth  # noqa: E402
import llm  # noqa: E402

OPUS = "claude-opus-5-5"


def envelope(result: str, *, is_error: bool = False, usage: dict | None = None) -> str:
    return json.dumps({
        "type": "result", "subtype": "success", "is_error": is_error, "result": result,
        "total_cost_usd": 0.25,
        "usage": usage if usage is not None else {
            "input_tokens": 10, "cache_read_input_tokens": 1000, "cache_creation_input_tokens": 200,
            "output_tokens": 500, "output_tokens_details": {"thinking_tokens": 300},
        },
    })


class OpusCommandTest(unittest.TestCase):
    def run_opus(self, stdout: str, *, returncode: int = 0, effort: str = "xhigh"):
        calls = []

        def fake_run(command, **kwargs):
            calls.append((command, kwargs))
            return SimpleNamespace(returncode=returncode, stdout=stdout, stderr="")

        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(llm, "SCRATCH", Path(tmp)), \
             patch.object(llm.subprocess, "run", fake_run), \
             patch.dict(llm.os.environ, {"ANTHROPIC_API_KEY": "sk-test"}):
            result = llm.run_llm(OPUS, "the prompt", effort=effort)
        return result, calls

    def test_command_pins_model_effort_and_turns_customizations_off(self):
        result, calls = self.run_opus(envelope('{"verdict":"pass"}'))
        command, kwargs = calls[0]
        self.assertEqual(["claude", "-p"], command[:2])
        self.assertEqual(OPUS, command[command.index("--model") + 1])
        self.assertEqual("xhigh", command[command.index("--effort") + 1])
        self.assertEqual("", command[command.index("--tools") + 1])
        self.assertIn("--safe-mode", command)
        self.assertIn("--strict-mcp-config", command)
        self.assertEqual("json", command[command.index("--output-format") + 1])
        self.assertEqual("the prompt", kwargs["input"])
        self.assertNotIn("ANTHROPIC_API_KEY", kwargs["env"])  # the subscription login, never an API key
        self.assertTrue(result.ok)
        self.assertEqual('{"verdict":"pass"}', result.text)
        self.assertEqual(OPUS, result.model)
        self.assertEqual("xhigh", result.effort)

    def test_usage_comes_from_the_envelope(self):
        result, _ = self.run_opus(envelope("answer"))
        self.assertEqual(1210, result.usage["input_tokens"])
        self.assertEqual(1000, result.usage["cached_input_tokens"])
        self.assertEqual(500, result.usage["output_tokens"])
        self.assertEqual(300, result.usage["reasoning_tokens"])
        self.assertEqual(1710, result.usage["total_tokens"])
        self.assertEqual(0.25, result.usage["cost_usd"])
        self.assertNotIn("usage", result.metadata_availability)

    def test_an_answer_that_talks_about_a_quota_is_still_an_answer(self):
        result, _ = self.run_opus(envelope('{"claim":"rate limit 與 quota 只是字面"}'))
        self.assertTrue(result.ok)
        self.assertFalse(result.limit)

    def test_error_envelope_with_a_limit_message_is_a_limit(self):
        result, _ = self.run_opus(
            envelope("You've hit your weekly limit · resets Oct 25 at 7pm", is_error=True), returncode=1,
        )
        self.assertFalse(result.ok)
        self.assertTrue(result.limit)

    def test_error_envelope_without_a_limit_message_is_an_error(self):
        result, _ = self.run_opus(envelope("API Error: 500 internal", is_error=True), returncode=1)
        self.assertFalse(result.ok)
        self.assertFalse(result.limit)
        self.assertIn("API Error", result.error)

    def test_stdout_that_is_not_an_envelope_is_classified_as_plain_output(self):
        result, _ = self.run_opus("Claude usage limit reached. Your limit will reset at 3pm", returncode=1)
        self.assertFalse(result.ok)
        self.assertTrue(result.limit)

    def test_effort_must_be_explicit(self):
        with self.assertRaises(llm.LLMError):
            llm.run_llm(OPUS, "prompt")


class OpusAuditJobTest(unittest.TestCase):
    def test_opus_is_an_audit_job_model(self):
        self.assertIn("opus", audited.AUDIT_JOB_MODELS)
        job = audited.new_audit_job("T1579", "72", "opus")
        self.assertEqual("opus", job["model"])
        self.assertIsNone(audited.audit_job_problem(job, job["id"]))

    def test_opus_job_routes_the_review_chain_and_the_fixer_to_opus(self):
        job = {"model": "opus"}
        for stage in ("review_terms", "review_doctrine", "fix"):
            self.assertEqual(OPUS, audited.stage_model(job, stage))
        self.assertEqual("high", audited.stage_effort("review_terms"))
        self.assertEqual("xhigh", audited.stage_effort("review_doctrine", low_ratio=True))

    def test_auto_job_keeps_the_pinned_codex_models(self):
        self.assertEqual("gpt-5.6-sol", audited.stage_model({"model": "auto"}, "review_doctrine"))

    def test_low_ratio_warning_names_the_model_that_is_routed(self):
        source = "原文" * 60
        md = ("# 卷\n\n## 01 一段\nRange: T30n1579_p0694c18-p0694c19\n\nSource:\n<<<\n" + source +
              "\n>>>\n\nTranslation:\n<<<\n短\n>>>\n")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "T1579-072-baihua.md"
            path.write_text(md, encoding="utf-8")
            entry = bth.parse_entries(md)[0]
            for model, expected in (("opus", OPUS), ("auto", "gpt-5.6-sol")):
                job = {"model": model, "work": "T1579", "id": "20261022-000000-audit-abcd"}
                # The legacy contract that review_audit_candidate gives every section before the reviews.
                prog: dict = {"clause_contracts": {audited.unit_id(job, 72, 0): {
                    "stage": audited.LEGACY_CONTRACT_STAGE,
                    "source_hash": audited.source_hash(entry.source),
                    "clause": {"clause_id": audited.clause_id(entry), "vernacular": entry.translation},
                }}}
                with patch.object(audited, "semantic_review_call",
                                  return_value=({"verdict": "pass", "findings": []}, [], "event")), \
                     patch.object(audited, "audit_store"), patch.object(audited, "save_job"):
                    audited.review_pass(job, 72, path, "review_doctrine", prog, 1)
                self.assertEqual(expected, prog["warnings"][0]["routing"]["model"])
                self.assertEqual("xhigh", prog["warnings"][0]["routing"]["effort"])


if __name__ == "__main__":
    unittest.main()
