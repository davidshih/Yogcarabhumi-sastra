"""The gate of the legacy audit, option b (owner decision 2026-11-01).

A finding on the reader's text blocks the verdict: every review_doctrine finding, every glossary-check finding,
and a review_terms finding on the first-occurrence format, the plain wording or the rendering of a term. A
review_terms finding on a term_occurrences binding or on the glossary itself is a warning: it goes to the
glossary backlog of the report and does not block.
"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
import audited  # noqa: E402
import test_legacy_audit_acceptance as acceptance  # noqa: E402

JUAN = 7
SECTIONS = 12  # juan 7 has 12 sections, see test_legacy_audit_acceptance


def finding(stage: str, category: str, section: int = 1) -> dict:
    return {"clause_id": "c", "severity": "high", "category": category, "claim": f"{stage} {category}",
            "evidence": {"source_quote": "s", "translation_quote": "t", "reference_ids": []},
            "required_change": "change", "unit_id": audited.unit_id({"work": "T1579"}, JUAN, section),
            "review_stage": stage, "section_index": section}


class FindingBlocksTest(unittest.TestCase):
    def test_a_binding_or_provenance_finding_of_review_terms_is_a_warning(self):
        for category in audited.TERM_BACKLOG_CATEGORIES:
            with self.subTest(category=category):
                self.assertFalse(audited.finding_blocks(finding("review_terms", category)))

    def test_a_review_terms_finding_on_the_readers_text_blocks(self):
        for category in audited.TERM_TEXT_CATEGORIES:
            with self.subTest(category=category):
                self.assertTrue(audited.finding_blocks(finding("review_terms", category)))

    def test_an_unknown_category_blocks(self):
        for category in ("primary_term_id", "term_consistency", "", "Term_Binding"):
            with self.subTest(category=category):
                self.assertTrue(audited.finding_blocks(finding("review_terms", category)))

    def test_every_doctrine_and_glossary_check_finding_blocks_whatever_its_label(self):
        for stage in ("review_doctrine", audited.TERM_CHECK_STAGE, "review_parallel"):
            for category in audited.TERM_BACKLOG_CATEGORIES:
                with self.subTest(stage=stage, category=category):
                    self.assertTrue(audited.finding_blocks(finding(stage, category)))

    def test_the_stage_can_be_given_for_a_raw_review_answer(self):
        raw = {key: value for key, value in finding("review_terms", "term_binding").items()
               if key in audited.REVIEW_FINDING_FIELDS}
        self.assertFalse(audited.finding_blocks(raw, "review_terms"))
        self.assertTrue(audited.finding_blocks(raw, "review_doctrine"))


class ReviewTermsCategoriesTest(unittest.TestCase):
    def category_schema(self, stage: str) -> dict:
        return audited.review_schema(stage)["properties"]["findings"]["items"]["properties"]["category"]

    def test_review_terms_names_its_category_from_the_five_values(self):
        self.assertEqual(list(audited.REVIEW_TERMS_CATEGORIES), self.category_schema("review_terms")["enum"])
        self.assertEqual(5, len(set(audited.REVIEW_TERMS_CATEGORIES)))

    def test_the_other_reviews_keep_free_text(self):
        for stage in ("review_doctrine", "review_parallel"):
            with self.subTest(stage=stage):
                self.assertNotIn("enum", self.category_schema(stage))

    def test_the_prompt_the_reviewer_reads_explains_every_category(self):
        schema = audited.review_schema("review_terms", "T1579-072-001", "a" * 64, "c")
        prompt = audited.prompt_text("review_terms", input_json="{}", schema_json=audited.json_text(schema))
        for category in audited.REVIEW_TERMS_CATEGORIES:
            self.assertIn(f"{category}（", prompt, "each category is defined, not only listed in the enum")
        self.assertIn("拆成兩個 finding", prompt)

    def test_the_cli_schema_keeps_the_enum_and_the_guide(self):
        import llm
        schema = audited.review_schema("review_terms", "T1579-072-001", "a" * 64, "c")
        prompt = audited.prompt_text("review_terms", input_json="{}", schema_json=audited.json_text(schema))
        sent = llm._schema_from_prompt(prompt)  # what `claude -p --json-schema` gets
        category = sent["properties"]["findings"]["items"]["properties"]["category"]
        self.assertEqual(list(audited.REVIEW_TERMS_CATEGORIES), category["enum"])
        self.assertEqual(audited.TERM_CATEGORY_GUIDE, category["description"])


class AuditFindingsTest(unittest.TestCase):
    """audit_findings returns the blocking findings only; review_pass keeps the warnings in the backlog."""

    def run_findings(self, by_stage: dict[str, list[dict]]):
        seen = []

        def fake_review(job, juan, md_path, pass_name, prog, round_number, **kwargs):
            seen.append(kwargs.get("blocks"))
            return list(by_stage.get(pass_name, []))

        with patch.object(audited, "review_pass", side_effect=fake_review), \
             patch.object(audited, "term_check_findings", return_value=list(by_stage.get("term_check", []))):
            found = audited.audit_findings({"work": "T1579"}, JUAN, {}, Path("unused.md"), 1)
        self.assertEqual([audited.finding_blocks] * 2, seen, "both reviews get the classifier")
        return found

    def test_warnings_are_not_returned(self):
        warning = finding("review_terms", "term_binding")
        self.assertEqual([], self.run_findings({"review_terms": [warning]}))

    def test_doctrine_terms_on_the_text_and_the_glossary_check_are_returned(self):
        doctrine = finding("review_doctrine", "命題缺漏")
        text = finding("review_terms", "first_occurrence_format", 2)
        check = finding(audited.TERM_CHECK_STAGE, "glossary_term", 3)
        warning = finding("review_terms", "glossary_provenance", 4)
        found = self.run_findings({"review_terms": [text, warning], "review_doctrine": [doctrine],
                                   "term_check": [check]})
        self.assertEqual([text, doctrine, check], found)


class GlossaryBacklogReportTest(unittest.TestCase):
    def test_the_report_lists_the_backlog_of_every_unit(self):
        a = finding("review_terms", "term_binding", 1)
        b = finding("review_terms", "glossary_provenance", 3)
        prog = {"glossary_backlog": {a["unit_id"]: {"review_terms": [a]}, b["unit_id"]: {"review_terms": [b]}}}
        job = {"id": "20261101-000000-audit-abcd", "work": "T1579"}
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(audited, "audit_report_path", return_value=Path(tmp) / "report.json"):
            report = audited.write_audit_report(job, JUAN, prog, [])
        strip = lambda item: {key: value for key, value in item.items() if key != "section_index"}  # noqa: E731
        self.assertEqual([strip(a), strip(b)], report["glossary_backlog"])
        self.assertEqual("pass", report["verdict"])

    def test_a_report_without_a_backlog_has_an_empty_list(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(audited, "audit_report_path", return_value=Path(tmp) / "report.json"):
            report = audited.write_audit_report({"id": "x", "work": "T1579"}, JUAN, {}, ["blocked"])
        self.assertEqual([], report["glossary_backlog"])


# The driver answers review_terms (or review_doctrine) of section 2 with one finding of the given category. The
# backend is `echo`: every other review passes and the fixer gives the translation back unchanged.
ANSWER_WITH_A_FINDING = """
import dataclasses
real_structured = llm._run_fake_structured
def with_finding(model, payload):
    result = real_structured(model, payload)
    if payload.get("stage") == STAGE and str(payload.get("unit_id", "")).endswith("-002"):
        answer = json.loads(result.text)
        clause = payload["clause"]
        answer["verdict"] = "changes_required"
        answer["findings"] = [{
            "clause_id": clause["clause_id"], "severity": "high", "category": CATEGORY, "claim": "gate-b-finding",
            "evidence": {"source_quote": llm._first_quote(clause["source"]),
                         "translation_quote": llm._first_quote(clause["translation"]), "reference_ids": []},
            "required_change": "bind the term",
        }]
        result = dataclasses.replace(result, text=json.dumps(answer, ensure_ascii=False))
    return result
llm._run_fake_structured = with_finding
"""


class GateOptionBAcceptance(unittest.TestCase):
    """The real command in a copied tree, as in test_legacy_audit_acceptance."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._template_dir = tempfile.TemporaryDirectory()
        cls.template = Path(cls._template_dir.name) / "template"
        acceptance.build_template(cls.template)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._template_dir.cleanup()

    def setUp(self) -> None:
        self._tree_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tree_dir.cleanup)
        self.tree = Path(self._tree_dir.name) / "repo"
        shutil.copytree(self.template, self.tree)

    def run_audit(self, stage: str, category: str):
        patch_code = f"STAGE = {stage!r}\nCATEGORY = {category!r}\n" + ANSWER_WITH_A_FINDING
        return acceptance.drive(self.tree, patch_code, "--juans", str(JUAN), "--model", "echo")

    def report(self) -> dict:
        found = sorted((self.tree / "audits").glob(f"*/T1579-{JUAN:03d}-audit-report.json"))
        self.assertEqual(1, len(found), found)
        return json.loads(found[0].read_text(encoding="utf-8"))

    def llm_stages(self) -> list[str]:
        stages = []
        for path in (self.tree / "audits").glob("*/events.jsonl"):
            for line in path.read_text(encoding="utf-8").splitlines():
                event = json.loads(line)
                if event.get("type") == "llm_attempt":
                    stages.append(event["stage"])
        return stages

    def test_a_juan_with_only_a_binding_finding_passes_with_a_backlog_and_is_sealed(self):
        for category in audited.TERM_BACKLOG_CATEGORIES:
            with self.subTest(category=category):
                self.setUp()
                before = acceptance.digest(self.tree, "docs", "translations")

                done = self.run_audit("review_terms", category)

                self.assertEqual(0, done.returncode, f"stdout:\n{done.stdout}\nstderr:\n{done.stderr}")
                report = self.report()
                self.assertEqual(("pass", "sealed", []), (report["verdict"], report["install"], report["blocking"]))
                self.assertEqual([("T1579-007-002", "review_terms", category, "gate-b-finding")],
                                 [(item["unit_id"], item["review_stage"], item["category"], item["claim"])
                                  for item in report["glossary_backlog"]])
                self.assertEqual({"round1": 0, "remaining": 0}, report["findings"])
                self.assertEqual(before, acceptance.digest(self.tree, "docs", "translations"),
                                 "a warning is not sent to the fixer: no byte of the site or the md changes")
                stages = self.llm_stages()
                self.assertNotIn("fix", stages)
                self.assertEqual(SECTIONS, stages.count("review_terms"),
                                 "the gate reuses the review that had only a warning: no second call")
                done = acceptance.run_python(self.tree, "-c", acceptance.VERIFY_SNIPPET, report["attestation"])
                self.assertEqual("sealed", done.stdout.strip(), done.stderr)
                ledger = json.loads((self.tree / "audits" / report["job_id"] / f"T1579-{JUAN:03d}-coverage.json")
                                    .read_text(encoding="utf-8"))
                self.assertEqual((True, []), (ledger["passed"], ledger["issues"]))
                evidence = {item["stage"]: item for item in ledger["clauses"][1]["evidence"]}
                self.assertEqual(1, evidence["review_terms"]["warnings"])
                self.assertNotIn("warnings", evidence["review_doctrine"])

    def test_a_review_terms_finding_on_the_readers_text_still_fails(self):
        before = acceptance.digest(self.tree, "docs", "translations")

        done = self.run_audit("review_terms", "first_occurrence_format")

        self.assertEqual(1, done.returncode, f"stdout:\n{done.stdout}\nstderr:\n{done.stderr}")
        report = self.report()
        self.assertEqual(("fail", "none"), (report["verdict"], report["install"]))
        self.assertEqual(["T1579-007-002 review_terms: gate-b-finding"], report["blocking"])
        self.assertEqual([], report["glossary_backlog"])
        self.assertIn("fix", self.llm_stages())
        self.assertEqual(before, acceptance.digest(self.tree, "docs", "translations"))

    def test_a_doctrine_finding_fails_even_with_a_backlog_label(self):
        done = self.run_audit("review_doctrine", "term_binding")

        self.assertEqual(1, done.returncode, f"stdout:\n{done.stdout}\nstderr:\n{done.stderr}")
        report = self.report()
        self.assertEqual(("fail", "none", None), (report["verdict"], report["install"], report["attestation"]))
        self.assertEqual(["T1579-007-002 review_doctrine: gate-b-finding"], report["blocking"])
        self.assertEqual([], report["glossary_backlog"])


if __name__ == "__main__":
    unittest.main()
