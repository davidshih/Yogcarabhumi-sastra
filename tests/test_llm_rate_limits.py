import datetime as dt
import gzip
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import audit
import llm
import runner


NY = ZoneInfo("America/New_York")
NOW = dt.datetime(2026, 7, 14, 8, 30, 45, tzinfo=NY)


class ResumeParserTests(unittest.TestCase):
    def test_codex_human_date_preserves_date_year_and_timezone(self):
        message = (
            "ERROR: You've hit your usage limit. "
            "Please try again at Jul 21st, 2026 5:43 AM."
        )

        parsed = llm.parse_resume_at(message, NOW)

        self.assertEqual(dt.datetime(2026, 7, 21, 5, 43, tzinfo=NY), parsed)

    def test_human_date_accepts_ordinals_and_month_names(self):
        cases = {
            "Jan 1st, 2027 1:01 AM": dt.datetime(2027, 1, 1, 1, 1, tzinfo=NY),
            "February 2nd, 2027 2:02 PM": dt.datetime(2027, 2, 2, 14, 2, tzinfo=NY),
            "Mar 3rd, 2027 3:03 AM": dt.datetime(2027, 3, 3, 3, 3, tzinfo=NY),
            "April 4th, 2027 4:04 PM": dt.datetime(2027, 4, 4, 16, 4, tzinfo=NY),
            "May 11th, 2027 5:11 AM": dt.datetime(2027, 5, 11, 5, 11, tzinfo=NY),
            "July 21st, 2027 7:21 PM": dt.datetime(2027, 7, 21, 19, 21, tzinfo=NY),
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(
                    expected,
                    llm.parse_resume_at(f"ERROR: Try again at {value}.", NOW),
                )

    def test_explicit_provider_timezone_does_not_use_process_timezone(self):
        utc_now = NOW.astimezone(dt.timezone.utc)
        message = "ERROR: Try again at Jul 21st, 2026 5:43 AM (America/New_York)."

        parsed = llm.parse_resume_at(message, utc_now)

        self.assertEqual(dt.datetime(2026, 7, 21, 5, 43, tzinfo=NY), parsed)

    def test_final_provider_error_beats_earlier_reset_metadata(self):
        diagnostics = "\n".join([
            "account window resets_at=2026-07-14T12:30:43-04:00",
            "ERROR: You've hit your usage limit. Try again at Jul 21st, 2026 5:43 AM.",
        ])

        parsed = llm.parse_resume_at(diagnostics, NOW)

        self.assertEqual(dt.datetime(2026, 7, 21, 5, 43, tzinfo=NY), parsed)

    def test_without_provider_error_uses_latest_future_candidate(self):
        diagnostics = "\n".join([
            "usage limit; resets_at=2026-07-14T12:30:43-04:00",
            "quota exhausted; resets_at=2026-07-15T08:00:00-04:00",
        ])

        parsed = llm.parse_resume_at(diagnostics, NOW)

        self.assertEqual(dt.datetime(2026, 7, 15, 8, 0, tzinfo=NY), parsed)


class DiagnosticSanitizationTests(unittest.TestCase):
    def test_successful_model_output_cannot_trigger_limit_classification(self):
        model_output = "A valid answer discussing quota exhausted and HTTP 429."

        result = llm.classify_output(0, model_output, "")

        self.assertTrue(result.ok)
        self.assertFalse(result.limit)
        self.assertEqual(model_output, result.text)

    def test_run_llm_does_not_treat_out_file_as_diagnostic_channel(self):
        model_output = "valid structured output discussing quota exhausted and HTTP 429"

        def fake_run(command, **_kwargs):
            output_path = Path(command[command.index("-o") + 1])
            output_path.write_text(model_output, encoding="utf-8")
            return SimpleNamespace(
                returncode=0,
                stdout="CLI completed",
                stderr="",
            )

        with patch.object(llm.subprocess, "run", side_effect=fake_run):
            result = llm.run_llm("gpt-5.6-terra", "prompt", effort="high")

        self.assertTrue(result.ok)
        self.assertFalse(result.limit)
        self.assertEqual(model_output, result.text)

    def test_final_auth_fatal_overrides_earlier_quota_error(self):
        diagnostics = "\n".join([
            "ERROR: quota exhausted; try again at 12:30 PM",
            "FATAL: authentication failed for the current account",
        ])

        result = llm.classify_output(1, "", diagnostics)

        self.assertFalse(result.limit)
        self.assertIsNone(result.provider_resume_at)
        self.assertEqual("FATAL: authentication failed for the current account", result.error)

    def test_partial_linewise_prefixed_prompt_echo_cannot_trigger_limit(self):
        prompt = "\n".join([
            "Translate the source.",
            "PRIVATE quota exhausted; try again at 12:30 PM",
            "Return JSON.",
        ])
        echoes = (
            "PRIVATE quota exhausted; try again at 12:30 PM",
            "> PRIVATE quota exhausted; try again at 12:30 PM",
            "user: quota exhausted; try again at 12:30 PM",
        )
        for echoed in echoes:
            with self.subTest(echoed=echoed):
                result = llm.classify_output(
                    1, "", echoed, echoed_prompt=prompt,
                )
                self.assertFalse(result.limit)
                self.assertNotIn("quota exhausted", result.error)

    def test_arbitrary_framing_with_complete_prompt_line_cannot_trigger_limit(self):
        prompt_line = "PRIVATE_SOURCE_SENTINEL quota exhausted; try again at 12:30 PM"
        prompt = f"Translate the source.\n{prompt_line}\nReturn JSON."
        diagnostics = f"diagnostic input: {prompt_line}"

        result = llm.classify_output(
            1, "", diagnostics, echoed_prompt=prompt,
        )

        self.assertFalse(result.limit)
        self.assertNotIn("PRIVATE_SOURCE_SENTINEL", result.error)
        self.assertNotIn("quota exhausted", result.error)

    def test_short_prompt_fragment_does_not_filter_real_provider_error(self):
        provider = "ERROR: quota exhausted; try again at 12:30 PM"

        result = llm.classify_output(
            1, "", provider, echoed_prompt="quota",
        )

        self.assertTrue(result.limit)
        self.assertEqual(provider, result.error)

    def test_complete_long_prompt_echo_is_filtered_even_when_error_prefixed(self):
        prompt_line = "ERROR: PRIVATE_SOURCE_SENTINEL quota exhausted; try again at 12:30 PM"

        result = llm.classify_output(
            1, "", prompt_line, echoed_prompt=prompt_line,
        )

        self.assertFalse(result.limit)
        self.assertNotIn("PRIVATE_SOURCE_SENTINEL", result.error)
        self.assertNotIn("quota exhausted", result.error)

    def test_prompt_echo_cannot_trigger_limit_classification(self):
        prompt = "PRIVATE_PROMPT quota exhausted; try again at 12:30 PM"

        result = llm.classify_output(
            1,
            "",
            f"{prompt}\ntransport connection closed",
            echoed_prompt=prompt,
        )

        self.assertFalse(result.limit)
        self.assertNotIn("PRIVATE_PROMPT", result.error)

    def test_sanitized_error_keeps_exact_reset_sentence_and_drops_prompt(self):
        prompt = "PRIVATE_SOURCE_SENTINEL\nterm_occurrences must follow schema"
        provider = (
            "ERROR: You've hit your usage limit. "
            "Try again at Jul 21st, 2026 5:43 AM."
        )

        result = llm.classify_output(
            1,
            "",
            f"{prompt}\n{provider}\n{provider}",
            echoed_prompt=prompt,
        )

        self.assertTrue(result.limit)
        self.assertEqual(provider, result.error)
        self.assertNotIn("PRIVATE_SOURCE_SENTINEL", result.error)
        self.assertEqual(dt.datetime(2026, 7, 21, 5, 43, tzinfo=NY), result.resume_at)


class LimitDecisionTests(unittest.TestCase):
    def test_out_of_policy_provider_reset_uses_probe_without_losing_provider_time(self):
        provider = dt.datetime(2026, 7, 21, 5, 43, tzinfo=NY)

        effective, decision = llm.limit_wait_decision(provider, 0, NOW)

        self.assertEqual(NOW + dt.timedelta(minutes=15), effective)
        self.assertEqual("fallback_out_of_policy", decision)

    def test_no_reset_first_wait_uses_fifteen_minute_probe(self):
        effective, decision = llm.limit_wait_decision(None, 0, NOW)

        self.assertEqual(NOW + dt.timedelta(minutes=15), effective)
        self.assertEqual("fallback_unparsable", decision)

    def test_fallback_log_calls_next_wait_a_probe(self):
        provider = dt.datetime(2026, 7, 21, 5, 43, tzinfo=NY)
        result = llm.LLMResult(
            ok=False,
            limit=True,
            resume_at=provider,
            provider_resume_at=provider,
            error="ERROR: Try again at Jul 21st, 2026 5:43 AM.",
        )
        waits = []
        logs = []
        attempts = []

        with patch.object(llm, "run_llm", return_value=result), \
             patch.object(llm, "_now", return_value=NOW), \
             patch.object(llm.time, "sleep", side_effect=RuntimeError("stop")), \
             self.assertRaisesRegex(RuntimeError, "stop"):
            llm.call_with_limit_retry(
                "gpt-5.6-terra",
                "prompt",
                on_wait=waits.append,
                log=logs.append,
                effort="high",
                on_attempt=lambda item, attempt: attempts.append((item, attempt)),
            )

        self.assertEqual([NOW + dt.timedelta(minutes=15)], waits)
        self.assertEqual(1, attempts[0][1])
        self.assertEqual(provider, attempts[0][0].provider_resume_at)
        self.assertEqual(NOW + dt.timedelta(minutes=15), attempts[0][0].effective_resume_at)
        self.assertEqual("fallback_out_of_policy", attempts[0][0].resume_decision)
        self.assertIn("next quota probe", logs[0])
        self.assertIn("fallback_out_of_policy", logs[0])
        self.assertIn("2026-07-21T05:43:00-04:00", logs[0])


class RunnerRateLimitAuditTests(unittest.TestCase):
    def test_rate_limit_event_separates_provider_and_effective_resume_times(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = audit.AuditStore(root, "job")
            provider = dt.datetime(2026, 7, 21, 5, 43, tzinfo=NY)
            effective = NOW + dt.timedelta(minutes=15)
            provider_line = "ERROR: Try again at Jul 21st, 2026 5:43 AM."

            def fake_backend(_model, _prompt, *, on_wait, log, effort, on_attempt):
                result = llm.LLMResult(
                    ok=False,
                    limit=True,
                    resume_at=provider,
                    provider_resume_at=provider,
                    effective_resume_at=effective,
                    resume_decision="fallback_out_of_policy",
                    error=provider_line,
                    model="gpt-5.6-terra",
                    effort=effort,
                    sent_at=NOW.isoformat(),
                    received_at=(NOW + dt.timedelta(seconds=1)).isoformat(),
                    duration_ms=1000,
                    exit_code=1,
                )
                on_attempt(result, 1)
                on_wait(effective)
                raise llm.LLMError("stop after audit")

            job = {
                "id": "job",
                "model": "auto",
                "state": "running",
                "resume_at": None,
                "progress": {},
            }
            prompt = "PRIVATE_SOURCE_SENTINEL"
            with patch.object(runner, "audit_store", return_value=store), \
                 patch.object(runner, "save_job"), \
                 patch.object(llm, "call_with_limit_retry", side_effect=fake_backend), \
                 self.assertRaisesRegex(llm.LLMError, "stop after audit"):
                runner.llm_call(job, prompt, "translate", context={})

            event = store.events()[0]
            error_ref = event["artifacts"]["error"]
            error_text = gzip.decompress((root / error_ref["path"]).read_bytes()).decode()

        self.assertEqual(provider.isoformat(), event["provider_resume_at"])
        self.assertEqual(effective.isoformat(), event["effective_resume_at"])
        self.assertEqual("fallback_out_of_policy", event["rate_limit_decision"])
        self.assertEqual(f"rate_limited: {provider_line}", event["retry_reason"])
        self.assertEqual(provider_line, error_text)
        self.assertNotIn("PRIVATE_SOURCE_SENTINEL", event["retry_reason"])
        self.assertNotIn("PRIVATE_SOURCE_SENTINEL", error_text)


if __name__ == "__main__":
    unittest.main()
