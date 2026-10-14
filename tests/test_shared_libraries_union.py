"""The three libraries that scripts/runner.py and scripts/audited.py share, after their merge (plan K1 to K3).

The production path of each library keeps its behavior; the stricter path is the one of the audited
pipeline. A CLI of a model is never started: `subprocess.run` is replaced.
"""
import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_translation_html as bth  # noqa: E402
import check_translation_coverage as coverage  # noqa: E402
import llm  # noqa: E402


NOW = dt.datetime(2026, 7, 14, 8, 30, 45, tzinfo=ZoneInfo("America/New_York"))


class BackendRoutingTests(unittest.TestCase):
    def run_with(self, model: str, completed: subprocess.CompletedProcess, **options) -> llm.LLMResult:
        with patch.object(llm.subprocess, "run", return_value=completed) as run, \
             patch.dict(os.environ, {"ZAI_API_KEY": "test-key"}):
            result = llm.run_llm(model, "prompt", **options)
        self.command = run.call_args.args[0]
        return result

    def test_glm_reads_the_naive_reset_time_of_its_stdout_as_beijing_time(self):
        message = "[1308][Usage limit reached for 5 hour. Your limit will reset at 2026-07-06 06:10:27][x]"

        result = self.run_with("glm", subprocess.CompletedProcess([], 1, message, ""))

        self.assertTrue(result.limit)
        self.assertEqual(dt.datetime(2026, 7, 5, 22, 10, 27, tzinfo=dt.timezone.utc),
                         result.resume_at.astimezone(dt.timezone.utc))
        self.assertEqual(result.resume_at, result.provider_resume_at)
        self.assertEqual(["claude", "-p"], self.command)

    def test_a_claude_answer_that_names_a_quota_is_still_read_as_a_limit(self):
        # The claude CLI prints its answer and its limit message on stdout, so they cannot be told apart.
        result = self.run_with("claude", subprocess.CompletedProcess([], 0, "譯文談到 quota exhausted", ""))

        self.assertTrue(result.limit)
        self.assertFalse(result.ok)

    def test_the_same_words_in_a_codex_answer_are_an_answer(self):
        self.assertTrue(llm.classify_combined_output(0, "譯文談到 quota exhausted", "").limit)

        result = llm.classify_output(0, "譯文談到 quota exhausted", "")

        self.assertEqual((True, False, "譯文談到 quota exhausted"), (result.ok, result.limit, result.text))

    def test_an_audit_model_needs_an_explicit_effort(self):
        with self.assertRaisesRegex(llm.LLMError, "gpt-5.6-sol: reasoning effort must be explicit"):
            llm.run_llm("gpt-5.6-sol", "prompt")

    def test_an_unknown_model_is_refused(self):
        with self.assertRaisesRegex(llm.LLMError, "unknown model: gpt-4"):
            llm.run_llm("gpt-4", "prompt", effort="high")

    def test_a_fake_backend_of_the_audit_is_not_a_production_queue(self):
        import runner

        for name in llm.AUDIT_FAKE_MODELS + llm.AUDIT_MODELS:
            self.assertNotIn(name, runner.QUEUES)

    def test_every_backend_reports_the_call_metadata(self):
        result = self.run_with("claude", subprocess.CompletedProcess([], 0, "答案", ""))

        self.assertEqual(("claude", None, 0, "答案"), (result.model, result.effort, result.exit_code, result.text))
        self.assertIsNotNone(result.sent_at)
        self.assertIn("effort", result.metadata_availability)


class AuditFakeBackendTests(unittest.TestCase):
    """The three offline backends of the legacy audit (plan K6). They flag the second section of a juan."""

    CLAUSE_ID = "T30n1579_p0308c16-p0309a02"

    def ask(self, model: str, stage: str, translation: str, unit: str = "T1579-007-002") -> dict:
        clause = {"clause_id": self.CLAUSE_ID, "source": "\n宿作因論者\n謂如有一", "translation": translation, "note": ""}
        payload = {"schema_version": "1.0", "stage": stage, "unit_id": unit, "source_hash": "a" * 64, "clause": clause}
        prompt = "任務\n\n輸入 JSON：\n" + json.dumps(payload, ensure_ascii=False, indent=2) + "\n\n輸出 JSON Schema：\n{}\n"
        result = llm.run_llm(model, prompt, effort="high")
        self.assertTrue(result.ok, result)
        return json.loads(result.text)

    def test_the_doctrine_review_of_the_second_section_has_one_finding_with_literal_quotes(self):
        for model in llm.AUDIT_FAKE_MODELS:
            with self.subTest(model=model):
                self.assertEqual({
                    "schema_version": "1.0", "stage": "review_doctrine", "unit_id": "T1579-007-002",
                    "source_hash": "a" * 64, "verdict": "changes_required",
                    "findings": [{
                        "clause_id": self.CLAUSE_ID, "severity": "high", "category": "test", "claim": "echo-finding",
                        "evidence": {"source_quote": "宿作因論", "translation_quote": "主張宿作", "reference_ids": []},
                        "required_change": "append 【已修】",
                    }],
                    "proposed_terms": [],
                }, self.ask(model, "review_doctrine", "  主張宿作因的人\n是這樣說的"))

    def test_every_other_review_passes(self):
        self.assertEqual("pass", self.ask("echo-finding", "review_doctrine", "主張宿作因的人【已修】")["verdict"])
        self.assertEqual("pass", self.ask("echo-finding", "review_terms", "主張宿作因的人")["verdict"])
        self.assertEqual("pass", self.ask("echo-finding", "review_doctrine", "主張宿作因的人",
                                          unit="T1579-007-003")["verdict"])
        self.assertEqual("pass", self.ask("echo", "review_doctrine", "主張宿作因的人")["verdict"])

    def test_each_backend_fixes_in_its_own_way(self):
        def fixed(model: str) -> str:
            return self.ask(model, "fix", "第一段\n第一段第二行\n\n第二段")["clauses"][0]["vernacular"]

        self.assertEqual("第一段\n第一段第二行\n\n第二段【已修】", fixed("echo-finding"))
        self.assertEqual("第一段\n第一段第二行\n\n第二段", fixed("echo-finding-stubborn"))
        self.assertEqual("第一段\n第一段第二行\n第二段【已修】", fixed("echo-finding-merge"))
        self.assertEqual("第一段\n第一段第二行\n\n第二段", fixed("echo"))


class LimitRetryTests(unittest.TestCase):
    def test_the_production_caller_sleeps_through_a_limit_of_its_own_backend(self):
        answers = [llm.LLMResult(ok=False, limit=True, error="usage limit"), llm.LLMResult(ok=True, text="譯文")]
        calls, waits, logs = [], [], []

        def run_fn(model, prompt):
            calls.append((model, prompt))
            return answers.pop(0)

        with patch.object(llm, "_now", return_value=NOW), patch.object(llm.time, "sleep") as sleep, \
             patch.object(llm, "run_llm", side_effect=AssertionError("run_fn replaces the backend call")):
            text = llm.call_with_limit_retry("codex", "p", on_wait=waits.append, log=logs.append, run_fn=run_fn)

        self.assertEqual("譯文", text)
        self.assertEqual([("codex", "p"), ("codex", "p")], calls)
        self.assertEqual([NOW + dt.timedelta(minutes=15)], waits, "no reset time: the first step of the ladder")
        sleep.assert_called_once_with(1200.0)  # 15 minutes, and 5 more so the retry never races the reset
        self.assertIn("fallback_unparsable", logs[0])

    def test_a_reset_time_inside_the_window_is_used_as_it_is(self):
        reset = NOW + dt.timedelta(hours=2)

        self.assertEqual((reset, "provider"), llm.limit_wait_decision(reset, 3, NOW))
        self.assertEqual((NOW + dt.timedelta(minutes=60), "fallback_out_of_policy"),
                         llm.limit_wait_decision(NOW - dt.timedelta(minutes=1), 3, NOW))


class CoverageModeTests(unittest.TestCase):
    """Four lines; the second section starts on the line where the first one ends."""

    def setUp(self):
        self.lines = {"T30n1579_p0001a01": "甲", "T30n1579_p0001a02": "乙",
                      "T30n1579_p0001a03": "丙", "T30n1579_p0001a04": "丁"}
        self.start, self.end = "T30n1579_p0001a01", "T30n1579_p0001a04"
        self.shared = [
            coverage.RangeEntry("first", self.start, "T30n1579_p0001a02", "甲\n乙"),
            coverage.RangeEntry("second", "T30n1579_p0001a02", self.end, "乙\n丙\n丁"),
        ]

    def test_the_production_check_does_not_look_at_shared_or_missing_lines(self):
        gap = [self.shared[0], coverage.RangeEntry("second", "T30n1579_p0001a04", self.end)]

        self.assertEqual([], coverage.check_ranges(self.shared, set(self.lines), self.start, self.end))
        self.assertEqual([], coverage.check_ranges(gap, set(self.lines), self.start, self.end))

    def test_a_shared_boundary_line_is_an_issue_only_in_exact_once_mode(self):
        strict = coverage.check_ranges(self.shared, set(self.lines), self.start, self.end, self.lines)
        loose = coverage.check_ranges(self.shared, set(self.lines), self.start, self.end, self.lines,
                                      exact_once=False)

        self.assertEqual(["translation ranges overlapping lines: 0001a02"], strict)
        self.assertEqual([], loose)
        self.assertEqual(["T30n1579_p0001a02"],
                         coverage.range_overlaps(self.shared, set(self.lines), self.start, self.end))

    def test_a_missing_line_is_an_issue_in_both_modes(self):
        gap = [self.shared[0], coverage.RangeEntry("second", "T30n1579_p0001a04", self.end, "丁")]

        for exact_once in (True, False):
            with self.subTest(exact_once=exact_once):
                self.assertEqual(
                    ["translation ranges missing lines: 0001a03"],
                    coverage.check_ranges(gap, set(self.lines), self.start, self.end, self.lines,
                                          exact_once=exact_once))
        self.assertEqual([], coverage.range_overlaps(gap, set(self.lines), self.start, self.end))

    def test_bare_labels_take_the_prefix_of_the_expected_start_of_any_work(self):
        lines = {"T29n1558_p0001a02": "甲", "T29n1558_p0001a03": "乙"}
        entries = [coverage.RangeEntry("all", "T29n1558_p0001a02", "T29n1558_p0001a03", "甲\n乙")]

        self.assertEqual([], coverage.check_ranges(
            entries, {"0001a02", "0001a03"}, "T29n1558_p0001a02", "T29n1558_p0001a03", lines))
        self.assertEqual("T29n1558_p0001a02", coverage.full_line_id("0001a02", "T29n1558"))
        self.assertEqual("T29n1558_p0001a02", coverage.full_line_id("T29n1558_p0001a02"))
        with self.assertRaisesRegex(ValueError, "Invalid line id: 0001a02"):
            coverage.full_line_id("0001a02")

    def ledger(self, translation: str) -> dict:
        def clause(name, line_ids, quote):
            return {"clause_id": name, "source_line_ids": line_ids, "status": "covered",
                    "source_hash": coverage.sha256_text(coverage.canonical_source(self.lines, line_ids)),
                    "evidence": [{"source_quote": self.lines[line_ids[0]], "translation_quote": quote}]}

        every = list(self.lines)
        return {
            "schema_version": "1.0", "work": "T1579", "juan": 1,
            "source_hash": coverage.sha256_text(coverage.canonical_source(self.lines, every)),
            "translation_hash": coverage.sha256_text(translation),
            "clauses": [clause("c1", every[:2], "first"), clause("c2", every[1:], "second")],
            "passed": True, "issues": [],
        }

    def test_a_ledger_accepts_a_shared_line_only_when_it_declares_at_least_once(self):
        translation = "first second\n"
        declared = {**self.ledger(translation), "coverage": "at_least_once"}

        def check(ledger, **options):
            return coverage.check_coverage_ledger(ledger, self.lines, self.start, self.end, translation, **options)

        self.assertEqual(["ledger clauses overlapping lines: 0001a02"], check(self.ledger(translation)))
        self.assertEqual([], check(declared))
        self.assertEqual(["ledger clauses overlapping lines: 0001a02"], check(declared, exact_once=True),
                         "a caller that asks for exact-once is not overruled by the ledger")
        self.assertEqual([], check(self.ledger(translation), exact_once=False))

    def test_a_quote_over_two_paragraphs_is_found_in_a_translation_with_id_markers(self):
        translation = ("Translation:\n<<<\n<!-- #T30n1579_p0001a01-aaaaaa -->\nfirst\n\n"
                       "<!-- #T30n1579_p0001a01-bbbbbb -->\nsecond\n>>>\n")
        ledger = {**self.ledger(translation), "coverage": "at_least_once"}
        ledger["clauses"][0]["evidence"][0]["translation_quote"] = "first\n\nsecond"

        self.assertEqual("first\n\nsecond", coverage.translation_content(translation))
        self.assertEqual([], coverage.check_coverage_ledger(ledger, self.lines, self.start, self.end, translation))


class BuilderStyleTests(unittest.TestCase):
    MARKED = ("# 測試白話對照\n\n## 01 Test\nRange: T30n1579_p0001a01-p0001a02\n\nSource:\n<<<\nabc\n>>>\n\n"
              "Translation:\n<<<\n<!-- #T30n1579_p0001a01-aaaaaa -->\nabc\n>>>\n")

    def render(self, text: str, **options) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "T1579-067-baihua.md"
            source.write_text(text, encoding="utf-8")
            return bth.render(bth.parse_entries(text), source, "T1579", 67, "t", **options)

    def test_the_default_style_is_the_published_one(self):
        legacy = self.render(self.MARKED)

        self.assertEqual(legacy, self.render(self.MARKED, term_style="legacy"))
        self.assertIn('<p id="T30n1579_p0001a01-aaaaaa">abc</p>', legacy)
        self.assertNotIn("data-term-id=", legacy)

    def test_an_unknown_style_is_an_error(self):
        with self.assertRaisesRegex(ValueError, "Unknown term style: new"):
            self.render(self.MARKED, term_style="new")

    def test_an_entry_without_ids_has_no_ids_in_the_policy_style_and_is_an_error_in_the_published_one(self):
        unmarked = self.MARKED.replace("<!-- #T30n1579_p0001a01-aaaaaa -->\n", "")

        self.assertIn("<p>abc</p>", self.render(unmarked, term_style="policy"))
        self.assertIn('<p id="T30n1579_p0001a01-aaaaaa">abc</p>', self.render(self.MARKED, term_style="policy"))
        with self.assertRaisesRegex(ValueError, "paragraphs without ids"):
            self.render(unmarked)

    def test_a_range_ends_with_a_full_id_of_the_same_file_or_a_label(self):
        expected = ("T31n1585_p0001a02", "T31n1585_p0001b07")
        for label in ("T31n1585_p0001a02-T31n1585_p0001b07", "T31n1585_p0001a02-p0001b07",
                      "T31n1585_p0001a02-0001b07"):
            with self.subTest(label=label):
                self.assertEqual(expected, bth.parse_range(label))
        self.assertEqual(("T31n1585_p0001a02", "T31n1585_p0001a02"), bth.parse_range("T31n1585_p0001a02"))
        for malformed in ("T31n1585_p0001a02-T30n1579_p0001b07", "T31n1585_p0001a02-T31n1585_0001b07",
                          "T31n1585_p0001a02 ", "t31n1585_p0001a02", "T31n1585_p0001d02"):
            with self.subTest(malformed=malformed):
                with self.assertRaisesRegex(ValueError, "Invalid range"):
                    bth.parse_range(malformed)

    def test_the_default_output_follows_the_output_folder(self):
        source = Path("translations") / "T1558-003-baihua.md"
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(bth, "DEFAULT_OUTPUT_DIR", Path(tmp) / "docs" / "T1579" / "translations"):
            self.assertEqual(Path(tmp) / "docs" / "T1558" / "translations" / "T1558-003-baihua.html",
                             bth.translation_output_path(source, None))
            self.assertEqual(Path("x.html"), bth.translation_output_path(source, Path("x.html")))

    def test_a_sealed_page_is_rebuilt_only_in_the_style_that_was_sealed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "translations" / "T1579-067-baihua.md"
            source.parent.mkdir(parents=True)
            source.write_text(self.MARKED.replace("abc", "為解脫而修行。"), encoding="utf-8")  # 解脫 is a glossary term
            output = root / "docs" / "T1579" / "translations" / "T1579-067-baihua.html"
            output.parent.mkdir(parents=True)
            sealed_page = bth.render_translation_bytes(source, term_style="legacy")
            output.write_bytes(sealed_page)
            attestation = root / "attestation.json"
            bth.publisher.create_attestation(root, attestation, {"translation": source, "volume_html": output})

            with patch.object(bth, "ROOT", root):
                with self.assertRaisesRegex(ValueError, "does not match the attested output"):
                    bth.build_translation(source, output, attestation=attestation)
                self.assertEqual(sealed_page, output.read_bytes(), "the other style writes nothing")
                bth.build_translation(source, output, attestation=attestation, term_style="legacy")

            self.assertEqual(sealed_page, output.read_bytes())
        self.assertIn(b'data-tip="', sealed_page)
        self.assertNotIn(b"data-term-id=", sealed_page)


if __name__ == "__main__":
    unittest.main()
