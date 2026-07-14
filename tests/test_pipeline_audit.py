import json
import re
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import audit
import build_translation_html as bth
import llm
import publisher
import runner


class StructuredContractTests(unittest.TestCase):
    def test_translation_schema_exposes_exact_envelopes(self):
        schema = runner.TRANSLATION_SCHEMA
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(schema["required"]), set(schema["properties"]))

        clauses = schema["properties"]["clauses"]
        self.assertEqual(1, clauses["minItems"])
        self.assertEqual(1, clauses["maxItems"])

        clause = clauses["items"]
        self.assertFalse(clause["additionalProperties"])
        self.assertEqual(set(clause["required"]), set(clause["properties"]))

    def test_translation_schema_exposes_nested_object_shapes(self):
        expected = {
            "additions": {"clause_id", "text"},
            "negation_scope": {"clause_id", "text", "scope"},
            "references": {"clause_id", "expression", "referent"},
            "term_occurrences": {"clause_id", "term_id", "surface"},
            "variants": {"clause_id", "text", "rationale"},
            "notes": {"clause_id", "text"},
        }
        properties = runner.TRANSLATION_SCHEMA["properties"]["clauses"]["items"]["properties"]
        for field, fields in expected.items():
            item = properties[field]["items"]
            with self.subTest(field=field):
                self.assertEqual("object", item["type"])
                self.assertFalse(item["additionalProperties"])
                self.assertEqual(fields, set(item["required"]))
                self.assertEqual(fields, set(item["properties"]))

    def test_translation_schema_and_validator_share_nested_shapes(self):
        properties = runner.TRANSLATION_SCHEMA["properties"]["clauses"]["items"]["properties"]
        for field, fields in runner.TRANSLATION_NESTED_SHAPES.items():
            item = properties[field]["items"]
            with self.subTest(field=field):
                self.assertEqual(set(fields), set(item["required"]))
                self.assertEqual(set(fields), set(item["properties"]))
                for name in set(fields) - {"clause_id"}:
                    expected_pattern = (
                        r"^(?=[^〔〕]*\S)[^〔〕]+$"
                        if field == "additions" and name == "text" else r"\S"
                    )
                    self.assertEqual(expected_pattern, item["properties"][name]["pattern"])

    def test_translation_contract_rejects_observed_nested_alias_shapes(self):
        observed = {
            "term_occurrences": {
                "source_term": "作意", "primary_term_id": "manaskara", "occurrence": 1,
            },
            "notes": {"clause_id": "T30n1579_p0001a01", "content": "校註"},
        }
        for field, item in observed.items():
            payload = self.translation_payload()
            payload["clauses"][0][field] = [item]
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, field):
                runner.validate_translation_contract(
                    payload, "translate", "T1579-067-001", "a" * 64,
                    "T30n1579_p0001a01",
                )

    def test_translation_contract_rejects_nonstring_and_blank_text(self):
        for field, value in (("literal", 123), ("vernacular", 7)):
            payload = self.translation_payload()
            payload["clauses"][0][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "non-empty text"):
                runner.validate_translation_contract(
                    payload, "translate", "T1579-067-001", "a" * 64,
                    "T30n1579_p0001a01",
                )

        for value in (7, "   "):
            payload = self.translation_payload()
            payload["clauses"][0]["vernacular"] = f"完整白話〔{value}〕"
            payload["clauses"][0]["additions"] = [{
                "clause_id": "T30n1579_p0001a01", "text": value,
            }]
            with self.subTest(addition=value), self.assertRaisesRegex(ValueError, "non-empty text"):
                runner.validate_translation_contract(
                    payload, "translate", "T1579-067-001", "a" * 64,
                    "T30n1579_p0001a01",
                )

    def test_bound_translation_schema_uses_contract_consts(self):
        schema = runner.translation_schema(
            "translate", "T1579-067-001", "a" * 64, "T30n1579_p0001a01",
        )
        properties = schema["properties"]
        self.assertEqual("translate", properties["stage"]["const"])
        self.assertEqual("T1579-067-001", properties["unit_id"]["const"])
        self.assertEqual("a" * 64, properties["source_hash"]["const"])

        clause = properties["clauses"]["items"]
        self.assertEqual("T30n1579_p0001a01", clause["properties"]["clause_id"]["const"])
        for field in runner.TRANSLATION_NESTED_SHAPES:
            nested = clause["properties"][field]["items"]["properties"]["clause_id"]
            self.assertEqual("T30n1579_p0001a01", nested["const"])

    def test_translate_sections_passes_bound_schema_to_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            markdown = Path(tmp) / "translation.md"
            markdown.write_text(
                "# test\n\n## 01 Test\nRange: T30n1579_p0001a01\n\n"
                "Source:\n<<<\n原文\n>>>\n\nTranslation:\n<<<\n\n>>>\n\nNote:\n<<<\n\n>>>\n",
                encoding="utf-8",
            )
            job = {"id": "job", "work": "T1579", "model": "echo"}
            response = json.dumps({
                "schema_version": "1.0", "stage": "translate",
                "unit_id": "T1579-067-001", "source_hash": audit.sha256_text("原文"),
                "clauses": [{
                    "clause_id": "T30n1579_p0001a01", "literal": "原文", "vernacular": "新譯",
                    "additions": [], "negation_scope": [], "references": [], "speakers": None,
                    "term_occurrences": [], "variants": [], "notes": [],
                }],
            }, ensure_ascii=False)
            with patch.object(runner, "llm_call", return_value=response), \
                 patch.object(runner, "prompt_text", wraps=runner.prompt_text) as prompt, \
                 patch.object(runner, "save_job"), patch.object(runner, "log"):
                runner.translate_sections(job, 67, markdown, {})

        schema = json.loads(prompt.call_args.kwargs["schema_json"])
        self.assertEqual("translate", schema["properties"]["stage"]["const"])
        self.assertEqual("T1579-067-001", schema["properties"]["unit_id"]["const"])
        clause = schema["properties"]["clauses"]["items"]
        self.assertEqual("T30n1579_p0001a01", clause["properties"]["clause_id"]["const"])

    def test_addition_schema_describes_inner_text_without_delimiters(self):
        schema = runner.translation_schema(
            "translate", "T1579-067-001", "a" * 64, "T30n1579_p0001a01",
        )
        addition_text = schema["properties"]["clauses"]["items"]["properties"] \
            ["additions"]["items"]["properties"]["text"]
        self.assertEqual({
            "type": "string",
            "pattern": r"^(?=[^〔〕]*\S)[^〔〕]+$",
            "description": (
                "Inner text only. Do not include the 〔 or 〕 delimiters; "
                "vernacular contains exactly one 〔text〕 wrapper."
            ),
        }, addition_text)
        pattern = addition_text["pattern"]
        self.assertIsNotNone(re.search(pattern, "專注觀修"))
        for invalid in ("", "   ", "〔專注觀修〕", "專注〔觀修"):
            with self.subTest(invalid=invalid):
                self.assertIsNone(re.search(pattern, invalid))

    def test_translate_prompt_requires_unwrapped_addition_text(self):
        prompt = (ROOT / "prompts" / "translate.txt").read_text(encoding="utf-8")
        self.assertIn("additions[].text", prompt)
        self.assertIn("不得包含「〔」或「〕」", prompt)
        self.assertIn("恰好包裹一次", prompt)

    def test_bracketed_addition_text_is_rejected_but_inner_text_passes(self):
        payload = self.translation_payload()
        payload["clauses"][0]["vernacular"] = (
            "聲聞乘相應作意〔專注觀修〕修、有加行〔修行上的預備用功〕修。"
        )
        payload["clauses"][0]["additions"] = [{
            "clause_id": "T30n1579_p0001a01", "text": "〔專注觀修〕",
        }, {
            "clause_id": "T30n1579_p0001a01", "text": "〔修行上的預備用功〕",
        }]
        with self.assertRaisesRegex(ValueError, "must not contain"):
            runner.validate_translation_contract(
                payload, "translate", "T1579-067-001", "a" * 64,
                "T30n1579_p0001a01",
            )

        payload["clauses"][0]["additions"] = [{
            "clause_id": "T30n1579_p0001a01", "text": "專注觀修",
        }, {
            "clause_id": "T30n1579_p0001a01", "text": "修行上的預備用功",
        }]
        runner.validate_translation_contract(
            payload, "translate", "T1579-067-001", "a" * 64,
            "T30n1579_p0001a01",
        )

    def test_addition_text_with_delimiters_is_rejected_even_if_double_wrapped(self):
        payload = self.translation_payload()
        payload["clauses"][0]["vernacular"] = "作意〔〔專注觀修〕〕修。"
        payload["clauses"][0]["additions"] = [{
            "clause_id": "T30n1579_p0001a01", "text": "〔專注觀修〕",
        }]
        with self.assertRaisesRegex(ValueError, "must not contain"):
            runner.validate_translation_contract(
                payload, "translate", "T1579-067-001", "a" * 64,
                "T30n1579_p0001a01",
            )

    def test_addition_wrapper_must_appear_exactly_once(self):
        payload = self.translation_payload()
        payload["clauses"][0]["vernacular"] = "作意〔專注觀修〕修；再說〔專注觀修〕。"
        payload["clauses"][0]["additions"] = [{
            "clause_id": "T30n1579_p0001a01", "text": "專注觀修",
        }]
        with self.assertRaisesRegex(ValueError, "exactly once"):
            runner.validate_translation_contract(
                payload, "translate", "T1579-067-001", "a" * 64,
                "T30n1579_p0001a01",
            )

    def test_translation_contract_requires_stable_identity_and_fields(self):
        payload = self.translation_payload()
        parsed = runner.parse_json_contract(json.dumps(payload), "translate")
        clause = runner.validate_translation_contract(
            parsed, "translate", "T1579-067-001", "a" * 64, "T30n1579_p0001a01",
        )
        self.assertEqual("完整白話", clause["vernacular"])

        payload["clauses"][0].pop("negation_scope")
        with self.assertRaisesRegex(ValueError, "missing required fields"):
            runner.validate_translation_contract(
                payload, "translate", "T1579-067-001", "a" * 64,
                "T30n1579_p0001a01",
            )

    def test_translation_nested_objects_reject_empty_shapes(self):
        for field in ("references", "negation_scope", "term_occurrences", "variants"):
            payload = self.translation_payload()
            payload["clauses"][0][field] = [{}]
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, field):
                runner.validate_translation_contract(
                    payload, "translate", "T1579-067-001", "a" * 64,
                    "T30n1579_p0001a01",
                )

    def test_review_contract_cannot_return_rewritten_translation(self):
        payload = {
            "schema_version": "1.0", "stage": "review_doctrine",
            "unit_id": "T1579-067-001", "source_hash": "a" * 64,
            "verdict": "pass", "findings": [],
            "translation": "reviewer must not return a rewrite",
        }
        parsed = runner.parse_json_contract(json.dumps(payload), "review_doctrine")
        with self.assertRaisesRegex(ValueError, "forbidden fields"):
            runner.validate_review_contract(
                parsed, "review_doctrine", "T1579-067-001", "a" * 64,
                "T30n1579_p0001a01",
                source="原文", translation="譯文",
            )
        self.assertNotIn("translation", runner.REVIEW_SCHEMA["properties"])

    def test_review_chain_fixes_then_runs_all_reviewers_again(self):
        job = {"id": "test", "progress": {"67": {}}}
        finding = {"section_index": 0, "clause_id": "c1"}
        results = [[finding], [], [], [], [], []]
        with patch.object(runner, "save_job"), \
             patch.object(runner, "review_pass", side_effect=results) as review, \
             patch.object(runner, "fix_findings") as fixer:
            runner.run_review_chain(job, 67, Path("unused"), job["progress"]["67"])

        self.assertEqual(6, review.call_count)
        fixer.assert_called_once()
        self.assertEqual("pass", job["progress"]["67"]["review_verdict"])

    def test_declarative_routing_has_no_legacy_provider(self):
        terra = {"segment", "translate", "draft_codex", "draft_glm"}
        for stage, spec in runner.STAGE_SPECS.items():
            expected = "gpt-5.6-terra" if stage in terra else "gpt-5.6-sol"
            self.assertEqual(expected, spec.model)
            self.assertEqual("high", spec.effort)
            self.assertNotIn(spec.model, {"claude", "glm", "luna"})
        self.assertEqual("xhigh", runner.stage_effort("review_doctrine", low_ratio=True))

    def test_review_verdict_quotes_and_parallel_allowlist_are_enforced(self):
        base = {
            "schema_version": "1.0", "unit_id": "u", "source_hash": "a" * 64,
            "findings": [], "verdict": "not_checked",
        }
        with self.assertRaisesRegex(ValueError, "invalid verdict"):
            runner.validate_review_contract(
                {**base, "stage": "review_terms"}, "review_terms", "u", "a" * 64, "c",
                source="原文", translation="譯文",
            )
        self.assertEqual([], runner.validate_review_contract(
            {**base, "stage": "review_parallel"}, "review_parallel", "u", "a" * 64, "c",
            source="原文", translation="譯文", allowed_reference_ids=set(),
        ))
        finding = {
            "clause_id": "c", "severity": "high", "category": "parallel",
            "claim": "問題", "required_change": "修正",
            "evidence": {
                "source_quote": "原文", "translation_quote": "譯文",
                "reference_ids": ["outside"],
            },
        }
        with self.assertRaisesRegex(ValueError, "allowlist"):
            runner.validate_review_contract(
                {**base, "stage": "review_parallel", "verdict": "changes_required",
                 "findings": [finding]},
                "review_parallel", "u", "a" * 64, "c", source="原文", translation="譯文",
                allowed_reference_ids={"allowed"},
            )

    def test_formal_codex_command_pins_model_and_effort(self):
        with tempfile.TemporaryDirectory() as tmp:
            scratch = Path(tmp)
            commands = []

            def fake_run(command, **kwargs):
                commands.append(command)
                output = Path(command[command.index("-o") + 1])
                output.write_text('{"ok":true}', encoding="utf-8")
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            with patch.object(llm, "SCRATCH", scratch), patch.object(llm.subprocess, "run", fake_run):
                result = llm.run_llm("gpt-5.6-sol", "prompt", effort="xhigh")

        self.assertTrue(result.ok)
        self.assertEqual("gpt-5.6-sol", result.model)
        self.assertIn("--model", commands[0])
        self.assertEqual("gpt-5.6-sol", commands[0][commands[0].index("--model") + 1])
        self.assertIn('model_reasoning_effort="xhigh"', commands[0])

    def test_stale_translation_contract_is_rerun(self):
        with tempfile.TemporaryDirectory() as tmp:
            markdown = Path(tmp) / "translation.md"
            markdown.write_text(
                "# test\n\n## 01 Test\nRange: T30n1579_p0001a01\n\n"
                "Source:\n<<<\n原文\n>>>\n\nTranslation:\n<<<\n舊譯\n>>>\n\nNote:\n<<<\n\n>>>\n",
                encoding="utf-8",
            )
            job = {"id": "job", "work": "T1579", "model": "echo"}
            prog = {"clause_contracts": {"T1579-067-001": {
                "stage": "translate", "source_hash": "stale",
                "clause": {"clause_id": "T30n1579_p0001a01", "vernacular": "舊譯"},
            }}}
            response = json.dumps({
                "schema_version": "1.0", "stage": "translate",
                "unit_id": "T1579-067-001", "source_hash": audit.sha256_text("原文"),
                "clauses": [{
                    "clause_id": "T30n1579_p0001a01", "literal": "原文", "vernacular": "新譯",
                    "additions": [], "negation_scope": [], "references": [], "speakers": None,
                    "term_occurrences": [], "variants": [], "notes": [],
                }],
            }, ensure_ascii=False)
            with patch.object(runner, "llm_call", return_value=response) as call, \
                 patch.object(runner, "save_job"), patch.object(runner, "log"):
                runner.translate_sections(job, 67, markdown, prog)

        call.assert_called_once()
        self.assertEqual(audit.sha256_text("原文"),
                         prog["clause_contracts"]["T1579-067-001"]["source_hash"])

    def test_dual_merge_stale_contract_is_rerun(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            def write(name, translation):
                path = root / name
                path.write_text(
                    "# test\n\n## 01 Test\nRange: T30n1579_p0001a01\n\n"
                    f"Source:\n<<<\n原文\n>>>\n\nTranslation:\n<<<\n{translation}\n>>>\n\n"
                    "Note:\n<<<\n\n>>>\n",
                    encoding="utf-8",
                )
                return path
            paths = {"md": write("final.md", "舊譯"), "draft_codex": write("a.md", "稿甲"),
                     "draft_glm": write("b.md", "稿乙")}
            job = {"id": "job", "work": "T1579", "model": "dual-echo"}
            prog = {"clause_contracts": {"T1579-067-001": {
                "stage": "merge", "source_hash": "stale",
                "clause": {"clause_id": "T30n1579_p0001a01", "vernacular": "舊譯"},
            }}}
            response = json.dumps({
                "schema_version": "1.0", "stage": "merge", "unit_id": "T1579-067-001",
                "source_hash": audit.sha256_text("原文"), "clauses": [{
                    "clause_id": "T30n1579_p0001a01", "literal": "原文", "vernacular": "新合稿",
                    "additions": [], "negation_scope": [], "references": [], "speakers": None,
                    "term_occurrences": [], "variants": [], "notes": [],
                }],
            }, ensure_ascii=False)
            with patch.object(runner, "llm_call", return_value=response) as call, \
                 patch.object(runner, "save_job"), patch.object(runner, "log"):
                runner.merge_sections(job, 67, paths, prog)
            merged = bth.parse_entries(paths["md"].read_text(encoding="utf-8"))[0].translation
        call.assert_called_once()
        self.assertEqual("新合稿", merged)

    def test_enqueue_saves_positive_requested_parallel_configuration(self):
        captured = {}
        base = dict(work="T1579", juans="67-69", link=None, model="auto",
                    no_push=True, summary=False)
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(runner, "JOBS_DIR", Path(tmp)), patch.object(runner, "get_work"), \
             patch.object(runner, "save_job", side_effect=lambda job: captured.update(job)):
            runner.cmd_enqueue(SimpleNamespace(**base, requested_parallel=2))
        self.assertEqual(2, captured["requested_parallel"])
        with self.assertRaisesRegex(SystemExit, "parallel"):
            runner.cmd_enqueue(SimpleNamespace(**base, requested_parallel=0))

    @staticmethod
    def translation_payload():
        return {
            "schema_version": "1.0", "stage": "translate",
            "unit_id": "T1579-067-001", "source_hash": "a" * 64,
            "clauses": [{
                "clause_id": "T30n1579_p0001a01", "literal": "直譯",
                "vernacular": "完整白話", "additions": [], "negation_scope": [],
                "references": [], "speakers": None, "term_occurrences": [],
                "variants": [], "notes": [],
            }],
        }


class AuditStoreTests(unittest.TestCase):
    def test_dual_echo_one_volume_reaches_attested_build_in_temp_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prompt_dir = root / "prompts"
            prompt_dir.mkdir(parents=True)
            for source in runner.PROMPTS_DIR.glob("*.txt"):
                (prompt_dir / source.name).write_bytes(source.read_bytes())

            glossary = root / "translations" / "glossary" / "T1579-terms.json"
            glossary.parent.mkdir(parents=True)
            glossary.write_bytes(runner.GLOSSARY_PATH.read_bytes())
            markdown = root / "translations" / "T1579-067-baihua.md"
            markdown.parent.mkdir(parents=True, exist_ok=True)
            markdown.write_text(
                "# test\n\n## 01 Test\nRange: T30n1579_p0001a01\n\n"
                "Source:\n<<<\n原文\n>>>\n\nTranslation:\n<<<\n\n>>>\n\n"
                "Note:\n<<<\n\n>>>\n",
                encoding="utf-8",
            )
            data = root / "data" / "T1579-067.json"
            data.parent.mkdir(parents=True)
            data.write_text("{}", encoding="utf-8")
            output_dir = root / "docs" / "T1579" / "translations"
            line_id = "T30n1579_p0001a01"

            def build_site() -> None:
                work_dir = root / "docs" / "T1579"
                work_dir.mkdir(parents=True, exist_ok=True)
                (work_dir / "index.html").write_text("work", encoding="utf-8")
                (root / "docs" / "index.html").write_text("root", encoding="utf-8")

            def build_search() -> None:
                path = root / "docs" / "T1579" / "search.json"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("[]", encoding="utf-8")

            job = {
                "id": "dual-echo-e2e", "work": "T1579", "model": "dual-echo",
                "state": "running", "resume_at": None, "push": False,
                "progress": {"67": {}},
            }
            with ExitStack() as stack:
                stack.enter_context(patch.object(runner, "ROOT", root))
                stack.enter_context(patch.object(runner, "PROMPTS_DIR", prompt_dir))
                stack.enter_context(patch.object(runner, "POLICY_PATH", prompt_dir / "common_policy.txt"))
                stack.enter_context(patch.object(runner, "GLOSSARY_PATH", glossary))
                stack.enter_context(patch.object(runner, "ensure_data", return_value=data))
                stack.enter_context(patch.object(
                    runner, "extract_lines", return_value={line_id: "原文"},
                ))
                stack.enter_context(patch.object(runner, "data_line_ids", return_value={line_id}))
                stack.enter_context(patch.object(
                    runner, "prompt_vars",
                    return_value={"work": "T1579", "work_title": "test", "file_id": "T30n1579"},
                ))
                stack.enter_context(patch.object(runner, "save_job"))
                stack.enter_context(patch.object(runner, "log"))
                stack.enter_context(patch.object(bth, "DEFAULT_OUTPUT_DIR", output_dir))
                stack.enter_context(patch.object(bth, "term_tips", return_value=()))
                stack.enter_context(patch.object(runner.bsh, "main", side_effect=build_site))
                stack.enter_context(patch("build_search_index.main", side_effect=build_search))
                runner.run_juan(job, 67)

            attestation_path = root / job["progress"]["67"]["volume_attestation"]["path"]
            sealed = publisher.verify_attestation(root, attestation_path)
            sealed_paths = {item["path"] for item in sealed["files"].values()}
            self.assertEqual("done", job["progress"]["67"]["step"])
            self.assertTrue((output_dir / "T1579-067-baihua.html").is_file())
            self.assertIn("docs/T1579/translations/T1579-067-baihua.html", sealed_paths)
            self.assertIn("docs/T1579/search.json", sealed_paths)

    def test_artifacts_are_deduplicated_redacted_and_manifested(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = audit.AuditStore(root, "job-1", "batch-1")
            first = store.artifact("request", "api_key=super-secret\nrequest")
            second = store.artifact("request", "api_key=super-secret\nrequest")
            self.assertEqual(first, second)
            self.assertNotIn(b"super-secret", audit.read_artifact(root, first))

            store.append({
                "type": "llm_attempt", "call_id": "call-1", "attempt": 1,
                "stage": "translate", "model": "echo", "effort": None,
                "sent_at": "2026-07-14T00:00:00-04:00", "received_at": "2026-07-14T00:00:01-04:00",
                "duration_ms": 1000, "request_id": None, "usage": None,
                "availability_reason": {"usage": "provider did not expose token usage"},
                "artifacts": {"request": first},
            })
            manifest = json.loads(store.manifest_path.read_text(encoding="utf-8"))

        self.assertEqual("2026-07-14T00:00:00-04:00", manifest["translation_started_at"])
        self.assertEqual(1, manifest["stages"]["translate"]["call_count"])
        self.assertIsNone(manifest["stages"]["translate"]["usage"]["total"])
        self.assertEqual(1, len(manifest["artifacts"]))

    def test_quality_gate_hash_seal_blocks_build_bypass(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            translation = root / "T1579-067-baihua.md"
            translation.write_text("approved", encoding="utf-8")
            attestation_path = root / "attestation.json"
            publisher.create_attestation(root, attestation_path, {"translation": translation})
            job = {"progress": {"67": {"quality_gate": {
                "passed": True,
                "review_verdict": "pass",
                "translation_sha256": audit.sha256_text("approved"),
            }, "volume_attestation": {"path": "attestation.json"}}}}
            with patch.object(runner, "ROOT", root):
                runner.require_quality_gate(job, 67, translation)
                translation.write_text("changed after gate", encoding="utf-8")
                with self.assertRaisesRegex(RuntimeError, "changed after quality gate"):
                    runner.require_quality_gate(job, 67, translation)

    def test_fake_backend_reports_measured_and_unavailable_metadata(self):
        result = llm.run_llm("echo", "【原文】\n作意云何\n【原文結束】", effort="xhigh")
        self.assertTrue(result.ok)
        self.assertEqual("echo", result.model)
        self.assertEqual("xhigh", result.effort)
        self.assertIsNotNone(result.sent_at)
        self.assertIsNotNone(result.received_at)
        self.assertIsNotNone(result.duration_ms)
        self.assertIsNone(result.usage)
        self.assertIn("usage", result.metadata_availability)

    def test_ledger_requires_terms_and_doctrine_review_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            markdown = root / "translation.md"
            markdown.write_text(
                "# test\n\n## 01 Test\nRange: T30n1579_p0001a01\n\n"
                "Source:\n<<<\n原文\n>>>\n\nTranslation:\n<<<\n譯文\n>>>\n\nNote:\n<<<\n\n>>>\n",
                encoding="utf-8",
            )
            data = root / "data.json"
            data.write_text("{}", encoding="utf-8")
            store = audit.AuditStore(root, "job")
            attestations = {}
            for stage in ("review_terms", "review_doctrine"):
                response = store.artifact("response", json.dumps({
                    "schema_version": "1.0", "stage": stage, "unit_id": "T1579-067-001",
                    "source_hash": audit.sha256_text("原文"), "verdict": "pass",
                    "findings": [], "proposed_terms": [],
                }, ensure_ascii=False))
                event = store.append({
                    "type": "llm_attempt", "stage": stage, "model": "gpt-5.6-sol",
                    "effort": "high", "status": "ok", "sent_at": "2026-07-14T00:00:00Z",
                    "unit_id": "T1579-067-001", "clause_ids": ["T30n1579_p0001a01"],
                    "hashes": {"source_sha256": audit.sha256_text("原文")},
                    "artifacts": {"response": response},
                })
                attestations[stage] = {
                    "stage": stage, "verdict": "pass", "review_event_id": event["event_id"],
                    "model": "gpt-5.6-sol", "effort": "high",
                    "source_sha256": audit.sha256_text("原文"),
                    "translation_sha256": audit.sha256_text("譯文"),
                    "source_quote": "原文", "translation_quote": "譯文",
                }
            job = {"id": "job", "work": "T1579", "model": "auto", "progress": {"67": {
                "clause_contracts": {"T1579-067-001": {
                    "stage": "translate", "source_hash": audit.sha256_text("原文"),
                    "clause": {"clause_id": "T30n1579_p0001a01", "literal": "原文",
                               "vernacular": "譯文"},
                }},
                "review_attestations": {"T1579-067-001": attestations},
            }}}
            with patch.object(runner, "ROOT", root), \
                 patch.object(runner, "audit_store", return_value=store), \
                 patch.object(runner, "extract_lines", return_value={"T30n1579_p0001a01": "原文"}):
                path = runner.write_coverage_ledger(
                    job, 67, markdown, data, "T30n1579_p0001a01", "T30n1579_p0001a01",
                )
                passed = json.loads(path.read_text(encoding="utf-8"))
                bad_response = store.artifact("response", json.dumps({
                    "schema_version": "1.0", "stage": "review_terms",
                    "unit_id": "T1579-067-001", "source_hash": audit.sha256_text("原文"),
                    "verdict": "changes_required", "findings": [{}], "proposed_terms": [],
                }, ensure_ascii=False))
                bad_event = store.append({
                    "type": "llm_attempt", "stage": "review_terms", "model": "gpt-5.6-sol",
                    "effort": "high", "status": "ok", "sent_at": "2026-07-14T00:00:00Z",
                    "unit_id": "T1579-067-001", "clause_ids": ["T30n1579_p0001a01"],
                    "hashes": {"source_sha256": audit.sha256_text("原文")},
                    "artifacts": {"response": bad_response},
                })
                job["progress"]["67"]["review_attestations"]["T1579-067-001"]["review_terms"][
                    "review_event_id"] = bad_event["event_id"]
                bad_path = runner.write_coverage_ledger(
                    job, 67, markdown, data, "T30n1579_p0001a01", "T30n1579_p0001a01",
                )
                bad = json.loads(bad_path.read_text(encoding="utf-8"))
                job["progress"]["67"]["review_attestations"]["T1579-067-001"]["review_terms"][
                    "review_event_id"] = next(
                        event_id for event_id, event in {
                            item["event_id"]: item for item in store.events()
                        }.items() if event.get("stage") == "review_terms"
                        and event.get("artifacts", {}).get("response") != bad_response
                    )
                del job["progress"]["67"]["review_attestations"]["T1579-067-001"]["review_doctrine"]
                failed_path = runner.write_coverage_ledger(
                    job, 67, markdown, data, "T30n1579_p0001a01", "T30n1579_p0001a01",
                )
                failed = json.loads(failed_path.read_text(encoding="utf-8"))

        self.assertTrue(passed["passed"])
        self.assertEqual({"review_terms", "review_doctrine"},
                         {item["stage"] for item in passed["clauses"][0]["evidence"]})
        self.assertFalse(bad["passed"])
        self.assertFalse(failed["passed"])


class PublisherTests(unittest.TestCase):
    def test_publication_retry_is_idempotent_after_each_push_failure(self):
        for failed_push in (1, 2):
            with self.subTest(failed_push=failed_push), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                sealed = root / "translation.md"
                sealed.write_text("translation", encoding="utf-8")
                store = audit.AuditStore(root, "job")
                store.append({"type": "quality_gate", "verdict": "pass", "artifacts": {}})
                attestation = root / "attestation.json"
                publisher.create_attestation(root, attestation, {"translation": sealed})
                state = {"head": "base", "staged": set(), "committed": set(), "pushes": 0}

                def command(args, **kwargs):
                    if args[:3] == ["git", "branch", "--show-current"]:
                        return SimpleNamespace(returncode=0, stdout="codex/test\n", stderr="")
                    if args[:2] == ["git", "ls-remote"]:
                        return SimpleNamespace(returncode=2, stdout="", stderr="")
                    if args[:4] == ["git", "diff", "--cached", "--name-only"]:
                        staged = state["staged"] - state["committed"]
                        return SimpleNamespace(returncode=0, stdout="".join(f"{p}\n" for p in staged), stderr="")
                    if args[:3] == ["git", "add", "--"]:
                        state["staged"] = set(args[3:])
                        return SimpleNamespace(returncode=0, stdout="", stderr="")
                    if args[:2] == ["git", "commit"]:
                        state["committed"].update(state["staged"])
                        state["staged"] = set()
                        state["head"] = "content123" if "feat:" in args[-1] else "metadata456"
                        return SimpleNamespace(returncode=0, stdout="", stderr="")
                    if args[:3] == ["git", "rev-parse", "HEAD"]:
                        return SimpleNamespace(returncode=0, stdout=state["head"] + "\n", stderr="")
                    if args[:2] == ["git", "push"]:
                        state["pushes"] += 1
                        code = 1 if state["pushes"] == failed_push else 0
                        return SimpleNamespace(returncode=code, stdout="", stderr="")
                    return SimpleNamespace(returncode=0, stdout="", stderr="")

                with self.assertRaises(publisher.PublishError):
                    publisher.publish_volume(root, store, "T1579", 67, attestation,
                                             command=command)
                revision = publisher.publish_volume(root, store, "T1579", 67, attestation,
                                                    command=command)
                self.assertEqual("content123", revision)

    def test_build_accepts_content_gate_before_attestation_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "T1579-067-baihua.md"
            source.write_text(
                "# test\n\n## 01 Test\nRange: T30n1579_p0001a01\n\n"
                "Source:\n<<<\n原文\n>>>\n\nTranslation:\n<<<\n譯文\n>>>\n",
                encoding="utf-8",
            )
            output = root / "docs" / "T1579-067-baihua.html"
            job = {"progress": {"67": {"quality_gate": {
                "passed": True, "review_verdict": "pass",
                "translation_sha256": audit.sha256_text(source.read_text(encoding="utf-8")),
            }}}}
            with patch.object(bth, "translation_output_path", return_value=output), \
                 patch.object(runner.bsh, "main"), patch("build_search_index.main"):
                runner.build_html(job, source)
            built = output.exists()
        self.assertTrue(built)

    def test_volume_attestation_seals_every_publishable_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            files = {
                "data": root / "data.json", "translation": root / "translation.md",
                "ledger": root / "ledger.json", "glossary": root / "glossary.json",
                "policy": root / "prompts" / "common_policy.txt",
                "prompt": root / "prompts" / "translate.txt",
                "html": root / "docs" / "T1579" / "translations" / "T1579-067-baihua.html",
                "work_index": root / "docs" / "T1579" / "index.html",
                "search": root / "docs" / "T1579" / "search.json",
                "root_index": root / "docs" / "index.html",
                "neighbor": root / "docs" / "T1579" / "translations" / "T1579-068-baihua.html",
            }
            for path in files.values():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(path.name, encoding="utf-8")
            store = audit.AuditStore(root, "job")
            store.append({"type": "quality_gate", "verdict": "pass", "artifacts": {}})
            job = {"id": "job", "work": "T1579", "progress": {"67": {
                "coverage_ledger": {"path": "ledger.json"},
            }}}
            with patch.object(runner, "ROOT", root), patch.object(runner, "GLOSSARY_PATH", files["glossary"]), \
                 patch.object(runner, "POLICY_PATH", files["policy"]), \
                 patch.object(runner, "PROMPTS_DIR", root / "prompts"), \
                 patch.object(runner, "audit_store", return_value=store), \
                 patch.object(runner, "save_job"):
                path = runner.create_volume_attestation(
                    job, 67, files["translation"], files["data"],
                )
            sealed = {item["path"] for item in publisher.verify_attestation(root, path)["files"].values()}
        for key in ("html", "work_index", "search", "root_index", "neighbor"):
            self.assertIn(files[key].relative_to(root).as_posix(), sealed)

    def test_content_publication_paths_are_all_sealed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sealed = root / "translation.md"
            sealed.write_text("translation", encoding="utf-8")
            store = audit.AuditStore(root, "job")
            store.append({"type": "quality_gate", "verdict": "pass", "artifacts": {}})
            attestation = root / "attestation.json"
            publisher.create_attestation(root, attestation, {"translation": sealed})
            paths = publisher.publication_paths(root, "job", "T1579", 67, attestation)
            allowed = {sealed.resolve(), attestation.resolve()}
        self.assertEqual(allowed, {path.resolve() for path in paths})

    def test_attestation_snapshot_survives_append_but_sealed_content_does_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            translation = root / "translation.md"
            snapshot = root / "event-snapshot.gz"
            translation.write_text("translation", encoding="utf-8")
            snapshot.write_bytes(b"sealed events")
            path = root / "attestation.json"
            publisher.create_attestation(root, path, {
                "translation": translation, "audit_event_log_snapshot": snapshot,
            })
            live_log = root / "events.jsonl"
            live_log.write_text("later publication event", encoding="utf-8")
            publisher.verify_attestation(root, path)
            translation.write_text("tampered", encoding="utf-8")
            with self.assertRaisesRegex(publisher.PublishError, "sealed file changed"):
                publisher.verify_attestation(root, path)

    def test_publisher_uses_explicit_paths_branch_and_read_only_remote_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            translation = root / "translations" / "T1579-067-baihua.md"
            translation.parent.mkdir(parents=True)
            translation.write_text("translation", encoding="utf-8")
            store = audit.AuditStore(root, "job")
            store.append({"type": "quality_gate", "verdict": "pass", "artifacts": {}})
            snapshot = store.artifact("audit_event_log", store.event_path.read_bytes())
            attestation = store.job_dir / "attestation.json"
            publisher.create_attestation(root, attestation, {
                "translation": translation, "audit_event_log_snapshot": root / snapshot["path"],
            })
            commands = []
            diff_calls = 0

            def fake_command(command, **kwargs):
                nonlocal diff_calls
                commands.append(command)
                key = tuple(command[:3])
                if command[:3] == ["git", "branch", "--show-current"]:
                    return SimpleNamespace(returncode=0, stdout="codex/test\n", stderr="")
                if command[:4] == ["git", "diff", "--cached", "--name-only"]:
                    diff_calls += 1
                    output = "" if diff_calls == 1 else "translations/T1579-067-baihua.md\n"
                    return SimpleNamespace(returncode=0, stdout=output, stderr="")
                if command[:2] == ["git", "ls-remote"]:
                    return SimpleNamespace(returncode=0, stdout="remote", stderr="")
                if command[:3] == ["git", "rev-parse", "HEAD"]:
                    return SimpleNamespace(returncode=0, stdout="abc123\n", stderr="")
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            revision = publisher.publish_volume(
                root, store, "T1579", 67, attestation, command=fake_command,
            )
            publisher.verify_attestation(root, attestation)

        self.assertEqual("abc123", revision)
        flattened = [item for command in commands for item in command]
        self.assertNotIn("-A", flattened)
        self.assertNotIn("rebase", flattened)
        self.assertIn("merge-base", flattened)
        self.assertNotIn("main", flattened)
        add_commands = [command for command in commands if command[:2] == ["git", "add"]]
        self.assertTrue(all("unrelated.txt" not in command for command in add_commands))

    def test_publisher_rejects_main_and_remote_ahead(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sealed = root / "sealed"
            sealed.write_text("x", encoding="utf-8")
            attestation = root / "attestation.json"
            publisher.create_attestation(root, attestation, {"sealed": sealed})
            store = audit.AuditStore(root, "job")

            def main_command(command, **kwargs):
                return SimpleNamespace(returncode=0, stdout="main\n", stderr="")

            with self.assertRaisesRegex(publisher.PublishError, "codex"):
                publisher.publish_volume(root, store, "T1579", 67, attestation,
                                         command=main_command)

            def ahead_command(command, **kwargs):
                if command[:3] == ["git", "branch", "--show-current"]:
                    return SimpleNamespace(returncode=0, stdout="codex/test\n", stderr="")
                if command[:4] == ["git", "diff", "--cached", "--name-only"]:
                    return SimpleNamespace(returncode=0, stdout="", stderr="")
                if command[:2] == ["git", "ls-remote"]:
                    return SimpleNamespace(returncode=0, stdout="remote", stderr="")
                if command[:2] == ["git", "merge-base"]:
                    return SimpleNamespace(returncode=1, stdout="", stderr="")
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            with self.assertRaisesRegex(publisher.PublishError, "ahead or diverged"):
                publisher.publish_volume(root, store, "T1579", 67, attestation,
                                         command=ahead_command)


if __name__ == "__main__":
    unittest.main()
