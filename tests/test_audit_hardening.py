import gzip
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import audit


class SecretRedactionTests(unittest.TestCase):
    def test_redacts_structured_and_header_secrets_without_breaking_json(self):
        payload = json.dumps({
            "api_key": "json-secret",
            "Authorization": "Bearer bearer-secret",
            "nested": {"auth-token": "token-secret"},
        })
        redacted = audit.redact_secrets(payload)

        parsed = json.loads(redacted)
        self.assertEqual("[REDACTED]", parsed["api_key"])
        self.assertEqual("Bearer [REDACTED]", parsed["Authorization"])
        self.assertEqual("[REDACTED]", parsed["nested"]["auth-token"])
        for secret in ("json-secret", "bearer-secret", "token-secret"):
            self.assertNotIn(secret, redacted)

    def test_preserves_assignment_syntax_and_quote_style(self):
        source = "Authorization: Bearer header-secret\npassword = 'quoted-secret'"
        redacted = audit.redact_secrets(source)

        self.assertEqual(
            "Authorization: Bearer [REDACTED]\npassword = '[REDACTED]'",
            redacted,
        )
        self.assertNotIn("header-secret", redacted)
        self.assertNotIn("quoted-secret", redacted)


class ManifestHardeningTests(unittest.TestCase):
    def test_sums_complete_token_fields_and_marks_only_missing_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = audit.AuditStore(Path(tmp), "job")
            store.append(self.attempt("translate", {
                "input": 10, "cached_input": 3, "output": 5,
                "reasoning": 2, "total": 15,
            }, raw_usage={"input_tokens": 10, "provider_extra": 7}))
            store.append(self.attempt("translate", {
                "input": 20, "cached_input": None, "output": 6,
                "reasoning": 4, "total": 26,
            }, raw_usage={"input_tokens": 20, "provider_extra": 9}))
            manifest = json.loads(store.manifest_path.read_text(encoding="utf-8"))
            events = store.events()

        stage = manifest["stages"]["translate"]
        self.assertEqual(30, stage["usage"]["input"])
        self.assertEqual(11, stage["usage"]["output"])
        self.assertEqual(6, stage["usage"]["reasoning"])
        self.assertEqual(41, stage["usage"]["total"])
        self.assertIsNone(stage["usage"]["cached_input"])
        self.assertEqual(
            "provider omitted cached tokens",
            stage["usage_availability_reason"]["cached_input"],
        )
        self.assertNotIn("input", stage["usage_availability_reason"])
        self.assertEqual(7, events[0]["usage"]["provider_extra"])
        self.assertEqual(9, events[1]["usage"]["provider_extra"])

    def test_sums_every_field_when_all_calls_report_tokens(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = audit.AuditStore(Path(tmp), "job")
            store.append(self.attempt("review", {
                "input": 1, "cached_input": 2, "output": 3,
                "reasoning": 4, "total": 8,
            }))
            store.append(self.attempt("review", {
                "input": 10, "cached_input": 20, "output": 30,
                "reasoning": 40, "total": 80,
            }))
            manifest = json.loads(store.manifest_path.read_text(encoding="utf-8"))

        self.assertEqual({
            "input": 11, "cached_input": 22, "output": 33,
            "reasoning": 44, "total": 88,
        }, manifest["stages"]["review"]["usage"])
        self.assertEqual({}, manifest["stages"]["review"]["usage_availability_reason"])

    def test_null_tokens_mark_every_field_unavailable_without_dropping_raw_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = audit.AuditStore(Path(tmp), "job")
            store.append(self.attempt(
                "translate", None, raw_usage={"provider_event": "usage"},
            ))
            manifest = json.loads(store.manifest_path.read_text(encoding="utf-8"))

            stage = manifest["stages"]["translate"]
            self.assertTrue(all(value is None for value in stage["usage"].values()))
            self.assertEqual(set(stage["usage"]), set(stage["usage_availability_reason"]))
            self.assertEqual("usage", store.events()[0]["usage"]["provider_event"])

    def test_commit_updates_remain_reconstructable_from_append_only_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = audit.AuditStore(root, "job")
            ref = store.artifact("response", "safe response")
            first_gzip = (root / ref["path"]).read_bytes()
            duplicate = store.artifact("response", "safe response")
            store.append({"type": "result", "artifacts": {"response": ref}})
            initial = json.loads(store.manifest_path.read_text(encoding="utf-8"))
            store.update_commit(branch="codex/audit")
            store.update_commit(commit_hash="abc1234")
            store.update_commit(pushed_at="2026-07-14T12:00:00-04:00")
            manifest = json.loads(store.manifest_path.read_text(encoding="utf-8"))

            self.assertEqual(ref, duplicate)
            self.assertEqual(first_gzip, (root / ref["path"]).read_bytes())
            self.assertEqual(b"safe response", audit.read_artifact(root, ref))
            self.assertEqual({"branch": None, "hash": None, "pushed_at": None}, initial["commit"])
            self.assertEqual({
                "branch": "codex/audit", "hash": "abc1234",
                "pushed_at": "2026-07-14T12:00:00-04:00",
            }, manifest["commit"])
            self.assertEqual(
                audit.sha256_bytes(store.event_path.read_bytes()),
                manifest["event_log"]["sha256"],
            )
            self.assertEqual(4, manifest["event_count"])
            self.assertEqual(first_gzip, gzip.compress(b"safe response", mtime=0))

    @staticmethod
    def attempt(stage, tokens, raw_usage=None):
        return {
            "type": "llm_attempt",
            "stage": stage,
            "model": "provider-model",
            "effort": "xhigh",
            "sent_at": "2026-07-14T10:00:00-04:00",
            "tokens": tokens,
            "usage": raw_usage if raw_usage is not None else dict(tokens),
            "availability_reason": {"usage": "provider omitted cached tokens"},
        }


if __name__ == "__main__":
    unittest.main()
