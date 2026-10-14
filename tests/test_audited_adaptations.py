"""What scripts/audited.py adds to the branch runner it came from (plan K4), through its stage functions.

The model is the offline echo backend of scripts/llm.py or a replaced `llm.call_with_limit_retry`. Every
file is written below a temporary folder.
"""
import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import audit  # noqa: E402
import audited  # noqa: E402
import build_translation_html as bth  # noqa: E402
import llm  # noqa: E402


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def section(number: int, line_id: str, source: str, translation: str) -> str:
    return (f"## {number:02d} Test {number}\nRange: {line_id}\n\nSource:\n<<<\n{source}\n>>>\n\n"
            f"Translation:\n<<<\n{translation}\n>>>\n\nNote:\n<<<\n\n>>>\n\n")


def write_data(path: Path, lines: dict[str, str]) -> Path:
    """A CBETA juan file with these lines, in the shape that the API returns."""
    html = "".join(f'<span class="lb" id="{line_id}">{line_id}</span>{text}' for line_id, text in lines.items())
    path.write_text(json.dumps({"results": [html]}, ensure_ascii=False), encoding="utf-8")
    return path


def legacy_contract(source: str, clause: str, translation: str) -> dict:
    return {"stage": "legacy", "source_hash": sha(source),
            "clause": {"clause_id": clause, "vernacular": translation}}


class NormalizedTranslationTests(unittest.TestCase):
    def test_paragraphs_are_joined_by_one_blank_line_and_markers_are_dropped(self):
        text = "  第一段\n第一段次行\n\n\n<!-- #x -->\n第二段  \n"

        self.assertEqual("第一段\n第一段次行\n\n第二段", audited.normalize_translation(text))

    def test_extra_blank_lines_from_the_model_do_not_make_the_contract_stale(self):
        first, second = "T30n1579_p0001a01-aaaaaa", "T30n1579_p0001a01-bbbbbb"
        with tempfile.TemporaryDirectory() as tmp:
            markdown = Path(tmp) / "translation.md"
            markdown.write_text("# test\n\n" + section(
                1, "T30n1579_p0001a01", "原文",
                f"<!-- #{first} -->\n第一段\n\n<!-- #{second} -->\n第二段"), encoding="utf-8")
            answer = json.dumps({
                "schema_version": "1.0", "stage": "translate", "unit_id": "T1579-067-001",
                "source_hash": sha("原文"),
                "clauses": [{
                    "clause_id": "T30n1579_p0001a01", "literal": "原文",
                    "vernacular": "第一段改\n\n\n\n第二段\n",
                    "additions": [], "negation_scope": [], "references": [], "speakers": None,
                    "term_occurrences": [], "variants": [], "notes": [],
                }],
            }, ensure_ascii=False)

            def fake_call(_job, _prompt, _stage, *, context, effort=None):
                context.setdefault("_audit_event_ids", []).append("llm-test-event")
                return answer

            prog = {}
            job = {"id": "job", "work": "T1579", "model": "echo"}
            with patch.object(audited, "llm_call", side_effect=fake_call), \
                 patch.object(audited, "audit_store", return_value=SimpleNamespace(append=lambda event: event)), \
                 patch.object(audited, "save_job"), patch.object(audited, "log"):
                audited.translate_sections(job, 67, markdown, prog)
            entry = bth.parse_entries(markdown.read_text(encoding="utf-8"))[0]

        self.assertEqual("第一段改\n\n第二段", entry.translation)
        self.assertEqual((first, second), entry.paragraph_ids, "the rewrite keeps the paragraph ids of the md")
        contract = prog["clause_contracts"]["T1579-067-001"]
        self.assertEqual("第一段改\n\n第二段", contract["clause"]["vernacular"])
        checked = audited.canonical_structured_contract(
            contract, "T1579-067-001", sha("原文"), "T30n1579_p0001a01", entry.translation,
            error_stage="review_terms",
        )
        self.assertEqual("translate", checked["stage"])


class LegacyContractTests(unittest.TestCase):
    def check(self, contract: dict, translation: str = "譯文") -> dict:
        return audited.canonical_structured_contract(
            contract, "T1579-067-001", sha("原文"), "T30n1579_p0001a01", translation, error_stage="audit",
        )

    def test_a_published_translation_is_bound_by_its_text(self):
        self.assertEqual(
            {"stage": "legacy", "source_hash": sha("原文"), "translation_sha256": sha("譯文"),
             "clause_id": "T30n1579_p0001a01", "annotations": {}},
            self.check(legacy_contract("原文", "T30n1579_p0001a01", "譯文")),
        )

    def test_another_source_clause_or_text_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "source_hash mismatch"):
            self.check(legacy_contract("別的原文", "T30n1579_p0001a01", "譯文"))
        with self.assertRaisesRegex(ValueError, "clause_id mismatch"):
            self.check(legacy_contract("原文", "T30n1579_p0001a02", "譯文"))
        with self.assertRaisesRegex(ValueError, "contract is stale"):
            self.check(legacy_contract("原文", "T30n1579_p0001a01", "譯文"), translation="改過的譯文")

    def test_no_model_may_answer_with_the_legacy_stage(self):
        self.assertNotIn("legacy", audited.TRANSLATION_STAGES)
        self.assertNotIn("legacy", audited.translation_schema()["properties"]["stage"]["enum"])
        answer = json.dumps({"schema_version": "1.0", "stage": "legacy"})
        with self.assertRaisesRegex(ValueError, "stage mismatch"):
            audited.parse_json_contract(answer, "fix")


class ReviewPassOptionTests(unittest.TestCase):
    """Two sections in a temporary root, reviewed by the echo backend; a model call is an llm_attempt event."""

    UNIT_1, UNIT_2 = "T1579-067-001", "T1579-067-002"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.markdown = self.root / "translation.md"
        self.write_markdown("譯文乙")
        self.prompts = self.root / "prompts"
        shutil.copytree(audited.PROMPTS_DIR, self.prompts)
        self.job = {"id": "job", "work": "T1579", "model": "echo", "state": "running", "resume_at": None,
                    "progress": {"67": {"clause_contracts": {
                        self.UNIT_1: legacy_contract("原文甲", "T30n1579_p0001a01", "譯文甲"),
                        self.UNIT_2: legacy_contract("原文乙", "T30n1579_p0001a02", "譯文乙"),
                    }}}}
        self.prog = self.job["progress"]["67"]
        for name, value in (("ROOT", self.root), ("PROMPTS_DIR", self.prompts),
                            ("POLICY_PATH", self.prompts / "common_policy.txt")):
            patcher = patch.object(audited, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(audited, "save_job")
        patcher.start()
        self.addCleanup(patcher.stop)

    def write_markdown(self, second_translation: str) -> None:
        self.markdown.write_text(
            "# test\n\n" + section(1, "T30n1579_p0001a01", "原文甲", "譯文甲")
            + section(2, "T30n1579_p0001a02", "原文乙", second_translation), encoding="utf-8")

    def review(self, round_number: int = 1, **options) -> list[dict]:
        return audited.review_pass(self.job, 67, self.markdown, "review_terms", self.prog, round_number, **options)

    def reviewed_units(self) -> list[str]:
        return [event["unit_id"] for event in audit.AuditStore(self.root, "job").events()
                if event.get("type") == "llm_attempt"]

    def event_of(self, unit: str) -> str:
        return self.prog["review_attestations"][unit]["review_terms"]["review_event_id"]

    def test_without_options_every_unit_is_reviewed_again(self):
        self.review()
        self.review(2)

        self.assertEqual([self.UNIT_1, self.UNIT_2, self.UNIT_1, self.UNIT_2], self.reviewed_units())

    def test_only_units_reviews_those_units_and_leaves_the_other_attestations(self):
        self.review()
        first_event, second_event = self.event_of(self.UNIT_1), self.event_of(self.UNIT_2)

        findings = self.review(2, only_units={self.UNIT_2})

        self.assertEqual([], findings)
        self.assertEqual([self.UNIT_1, self.UNIT_2, self.UNIT_2], self.reviewed_units())
        self.assertEqual(first_event, self.event_of(self.UNIT_1), "the review of the other unit still stands")
        self.assertNotEqual(second_event, self.event_of(self.UNIT_2))

    def test_reuse_skips_a_review_that_answered_the_same_prompt(self):
        self.review()
        first_event = self.event_of(self.UNIT_1)

        findings = self.review(reuse_attestations=True)

        self.assertEqual([], findings)
        self.assertEqual([self.UNIT_1, self.UNIT_2], self.reviewed_units(), "no input changed: no model call")
        self.assertEqual(first_event, self.event_of(self.UNIT_1))

    def test_a_changed_translation_is_reviewed_again_and_the_unchanged_one_is_not(self):
        self.review()
        self.write_markdown("譯文乙改")
        self.prog["clause_contracts"][self.UNIT_2] = legacy_contract("原文乙", "T30n1579_p0001a02", "譯文乙改")

        self.review(reuse_attestations=True)

        self.assertEqual([self.UNIT_1, self.UNIT_2, self.UNIT_2], self.reviewed_units())

    def test_a_changed_policy_changes_every_prompt_so_nothing_is_reused(self):
        self.review()
        policy = self.prompts / "common_policy.txt"
        policy.write_text(policy.read_text(encoding="utf-8") + "\n- 測試用的新增規則。\n", encoding="utf-8")

        self.review(reuse_attestations=True)

        self.assertEqual([self.UNIT_1, self.UNIT_2, self.UNIT_1, self.UNIT_2], self.reviewed_units())

    def test_a_review_by_another_model_is_not_reused(self):
        self.review()
        self.job["model"] = "echo-finding"  # the same answers, but another backend gave the earlier ones

        self.review(reuse_attestations=True)

        self.assertEqual([self.UNIT_1, self.UNIT_2, self.UNIT_1, self.UNIT_2], self.reviewed_units())

    def proposing_backend(self, _model, _prompt, *, on_wait, log, effort, on_attempt):
        unit = self.UNIT_1 if len(self.reviewed_units()) == 0 else self.UNIT_2
        source = "原文甲" if unit == self.UNIT_1 else "原文乙"
        text = json.dumps({
            "schema_version": "1.0", "stage": "review_terms", "unit_id": unit, "source_hash": sha(source),
            "verdict": "pass", "findings": [], "proposed_terms": [{"id": "new-term"}],
        }, ensure_ascii=False)
        on_attempt(llm.LLMResult(ok=True, text=text, model="echo", effort=effort), 1)
        return text

    def test_proposed_terms_are_kept_aside_when_they_are_not_applied(self):
        with patch.object(llm, "call_with_limit_retry", side_effect=self.proposing_backend), \
             patch.object(audited, "append_new_terms") as append:
            self.review(apply_proposed_terms=False)

        append.assert_not_called()
        self.assertEqual([{"id": "new-term"}], self.prog["proposed_terms"], "the same proposal is listed once")

    def test_proposed_terms_go_to_the_glossary_by_default(self):
        with patch.object(llm, "call_with_limit_retry", side_effect=self.proposing_backend), \
             patch.object(audited, "append_new_terms") as append:
            self.review()

        self.assertEqual(2, append.call_count)
        self.assertEqual('[{"id": "new-term"}]', append.call_args.args[0])
        self.assertNotIn("proposed_terms", self.prog)


class CoverageModeTests(unittest.TestCase):
    def ledger(self, **options) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            markdown = root / "translation.md"
            markdown.write_text("# test\n\n" + section(1, "T30n1579_p0001a01", "原文", "譯文"), encoding="utf-8")
            data = write_data(root / "data.json", {"T30n1579_p0001a01": "原文"})
            job = {"id": "job", "work": "T1579", "model": "echo", "progress": {"67": {}}}
            with patch.object(audited, "ROOT", root):
                path = audited.write_coverage_ledger(
                    job, 67, markdown, data, "T30n1579_p0001a01", "T30n1579_p0001a01", **options)
                return json.loads(path.read_text(encoding="utf-8"))

    def test_the_ledger_of_the_full_pipeline_declares_no_coverage_mode(self):
        self.assertNotIn("coverage", self.ledger())

    def test_the_ledger_of_a_legacy_audit_declares_at_least_once(self):
        self.assertEqual("at_least_once", self.ledger(exact_once=False)["coverage"])

    def test_content_checks_accept_a_shared_boundary_line_only_when_asked(self):
        with tempfile.TemporaryDirectory() as tmp:
            markdown = Path(tmp) / "translation.md"
            markdown.write_text(
                "# test\n\n" + section(1, "T30n1579_p0001a01-p0001a02", "aaa\nbbb", "first")
                + section(2, "T30n1579_p0001a02-p0001a03", "bbb\nccc", "second"), encoding="utf-8")
            data = write_data(Path(tmp) / "data.json", {
                "T30n1579_p0001a01": "aaa", "T30n1579_p0001a02": "bbb", "T30n1579_p0001a03": "ccc"})
            strict = audited.run_content_checks(markdown, data, "T30n1579_p0001a01", "T30n1579_p0001a03")
            loose = audited.run_content_checks(markdown, data, "T30n1579_p0001a01", "T30n1579_p0001a03",
                                               exact_once=False)

        self.assertEqual(["translation ranges overlapping lines: 0001a02"], strict)
        self.assertEqual([], loose)


class FreshJuanTests(unittest.TestCase):
    """The full pipeline on a juan that has no translation yet: echo backend, temporary root, no site build."""

    def run_fresh_juan(self, root: Path) -> dict:
        prompts = root / "prompts"
        shutil.copytree(audited.PROMPTS_DIR, prompts)
        glossary = root / "translations" / "glossary" / "T1579-terms.json"
        glossary.parent.mkdir(parents=True)
        glossary.write_bytes(audited.GLOSSARY_PATH.read_bytes())
        (root / "data").mkdir()
        write_data(root / "data" / "T1579-067.json", {"T30n1579_p0001a01": "原文"})
        pages = root / "docs" / "T1579" / "translations"

        def build_site() -> None:  # stands in for the section pages, which need every data file of the work
            (root / "docs" / "T1579").mkdir(parents=True, exist_ok=True)
            (root / "docs" / "T1579" / "index.html").write_text("work", encoding="utf-8")
            (root / "docs" / "index.html").write_text("root", encoding="utf-8")

        def build_search() -> None:
            (root / "docs" / "T1579" / "search.json").write_text("[]", encoding="utf-8")

        job = {"id": "fresh", "work": "T1579", "model": "echo", "state": "running", "resume_at": None,
               "push": False, "progress": {"67": {}}}
        with ExitStack() as stack:
            for name, value in (("ROOT", root), ("PROMPTS_DIR", prompts), ("GLOSSARY_PATH", glossary),
                                ("POLICY_PATH", prompts / "common_policy.txt")):
                stack.enter_context(patch.object(audited, name, value))
            stack.enter_context(patch.object(audited, "save_job"))
            stack.enter_context(patch.object(audited, "log"))
            stack.enter_context(patch.object(bth, "DEFAULT_OUTPUT_DIR", pages))
            stack.enter_context(patch.object(audited.bsh, "main", side_effect=build_site))
            stack.enter_context(patch("build_search_index.main", side_effect=build_search))
            audited.run_juan(job, 67)
        return job

    def test_a_new_juan_is_named_like_the_production_runner_names_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.run_fresh_juan(Path(tmp))
            lines = (Path(tmp) / "translations" / "T1579-067-baihua.md").read_text(encoding="utf-8").splitlines()

        self.assertEqual("# 瑜伽師地論卷第六十七白話對照來源稿", lines[0])
        self.assertEqual(
            "底本：CBETA T1579《瑜伽師地論》卷第六十七。範圍為 `T30n1579_p0001a01` 至 `T30n1579_p0001a01`。", lines[2])

    def test_the_ledger_the_gate_and_the_seal_bind_the_md_that_holds_the_paragraph_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job = self.run_fresh_juan(root)
            prog = job["progress"]["67"]
            markdown = (root / "translations" / "T1579-067-baihua.md").read_text(encoding="utf-8")
            page = (root / "docs" / "T1579" / "translations" / "T1579-067-baihua.html").read_text(encoding="utf-8")
            ledger = json.loads((root / prog["coverage_ledger"]["path"]).read_text(encoding="utf-8"))
            sealed = audited.publisher.verify_attestation(root, root / prog["volume_attestation"]["path"])

        self.assertEqual("done", prog["step"])
        entry = bth.parse_entries(markdown)[0]
        self.assertEqual(1, len(entry.paragraph_ids))
        self.assertRegex(entry.paragraph_ids[0], r"^T30n1579_p0001a01-[0-9a-f]{6}$")
        self.assertIn(f"<!-- #{entry.paragraph_ids[0]} -->", markdown)
        self.assertIn(f'<p id="{entry.paragraph_ids[0]}">', page, "the page of the audited pipeline carries the id")
        self.assertEqual(sha(markdown), prog["quality_gate"]["translation_sha256"])
        self.assertEqual(sha(markdown), ledger["translation_hash"])
        self.assertEqual(sha(markdown), sealed["files"]["translation"]["sha256"])
        self.assertIs(True, ledger["passed"])
        self.assertNotIn("coverage", ledger)


class IsolationTests(unittest.TestCase):
    def test_the_pipeline_keeps_its_jobs_status_and_prompts_apart_from_the_production_runner(self):
        import runner

        self.assertEqual(ROOT / "jobs" / "audited", audited.JOBS_DIR)
        self.assertEqual(ROOT / "locks" / "audited-status.json", audited.STATUS_JSON)
        self.assertEqual(ROOT / "prompts" / "audited", audited.PROMPTS_DIR)
        self.assertEqual(ROOT / "prompts" / "audited" / "common_policy.txt", audited.POLICY_PATH)
        self.assertEqual((ROOT / "jobs", ROOT / "docs" / "status.json", ROOT / "prompts"),
                         (runner.JOBS_DIR, runner.STATUS_JSON, runner.PROMPTS_DIR))
        self.assertEqual(ROOT / "locks", audited.LOCKS_DIR)
        self.assertEqual(runner.LOCKS_DIR, audited.LOCKS_DIR, "both take locks/runner.pid: one runner at a time")

    def test_a_translation_worker_never_claims_an_audit_job(self):
        for model in ("auto", "dual", "echo"):
            for state in ("queued", "running", "waiting_limit"):
                with self.subTest(model=model, state=state):
                    job = {"kind": "legacy_audit", "model": model, "state": state, "pid": None}
                    self.assertFalse(audited.claimable(job, model))
        self.assertTrue(audited.claimable({"model": "auto", "state": "queued", "pid": None}, "auto"))

    def test_an_offline_backend_of_the_audit_answers_every_stage_itself(self):
        self.assertEqual("gpt-5.6-sol", audited.stage_model({"model": "auto"}, "review_doctrine"))
        self.assertEqual("gpt-5.6-terra", audited.stage_model({"model": "auto"}, "translate"))
        self.assertEqual("echo-finding", audited.stage_model({"model": "echo-finding"}, "review_doctrine"))
        self.assertEqual("echo-finding-merge", audited.stage_model({"model": "echo-finding-merge"}, "fix"))
        self.assertEqual("echo", audited.stage_model({"model": "echo"}, "fix"))
        self.assertEqual("echo", audited.stage_model({"model": "dual-echo"}, "merge"))


class OtherWorkTests(unittest.TestCase):
    def test_a_job_of_another_work_is_marked_failed_and_not_run(self):
        job = {"id": "job", "work": "T1821", "juans": [1], "model": "echo", "state": "queued", "progress": {}}
        with patch.object(audited, "save_job") as save, patch.object(audited, "log"), \
             patch.object(audited, "run_juan") as run_juan:
            audited.run_job(job)

        run_juan.assert_not_called()
        save.assert_called_once()
        self.assertEqual(("failed", "the audited pipeline supports T1579 only: T1821"), (job["state"], job["error"]))

    def test_enqueue_refuses_another_work_before_it_writes_a_job(self):
        args = SimpleNamespace(work="T1821", juans="1", link=None, model="echo", no_push=True,
                               summary=False, requested_parallel=1)
        with patch.object(audited, "save_job") as save, \
             self.assertRaisesRegex(SystemExit, "the audited pipeline supports T1579 only: T1821"):
            audited.cmd_enqueue(args)

        save.assert_not_called()

    def test_a_link_to_another_work_is_refused_too(self):
        args = SimpleNamespace(work="T1579", juans=None, link="https://cbetaonline.dila.edu.tw/zh/T1821_001",
                               model="echo", no_push=True, summary=False, requested_parallel=1)
        with patch.object(audited, "save_job") as save, \
             self.assertRaisesRegex(SystemExit, "supports T1579 only: T1821"):
            audited.cmd_enqueue(args)

        save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
