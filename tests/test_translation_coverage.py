from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from check_translation_coverage import (  # noqa: E402
    RangeEntry,
    canonical_source,
    check_coverage_ledger,
    check_ranges,
    normalize_source_text,
    main,
    sha256_text,
)


class TranslationRangeCoverageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source_lines = {
            "T30n1579_p0001a01": "甲",
            "T30n1579_p0001a02": "乙",
            "T30n1579_p0001a03": "丙",
            "T30n1579_p0001a04": "丁",
        }
        self.line_ids = {"0001a01", "0001a02", "0001a03", "0001a04"}
        self.start = "T30n1579_p0001a01"
        self.end = "T30n1579_p0001a04"

    def test_exact_once_ranges_and_source_blocks_pass(self) -> None:
        entries = [
            RangeEntry("first", self.start, "T30n1579_p0001a02", "甲\n乙"),
            RangeEntry("second", "T30n1579_p0001a03", self.end, "丙\n丁"),
        ]

        self.assertEqual(
            check_ranges(entries, self.line_ids, self.start, self.end, self.source_lines),
            [],
        )

    def test_middle_gap_fails(self) -> None:
        entries = [
            RangeEntry("first", self.start, "T30n1579_p0001a01", "甲"),
            RangeEntry("second", "T30n1579_p0001a03", self.end, "丙\n丁"),
        ]

        issues = check_ranges(entries, self.line_ids, self.start, self.end, self.source_lines)

        self.assertTrue(any("missing lines: 0001a02" in issue for issue in issues), issues)

    def test_overlap_fails(self) -> None:
        entries = [
            RangeEntry("first", self.start, "T30n1579_p0001a02", "甲\n乙"),
            RangeEntry("second", "T30n1579_p0001a02", self.end, "乙\n丙\n丁"),
        ]

        issues = check_ranges(entries, self.line_ids, self.start, self.end, self.source_lines)

        self.assertTrue(any("overlapping lines: 0001a02" in issue for issue in issues), issues)

    def test_source_block_mismatch_fails(self) -> None:
        entries = [RangeEntry("all", self.start, self.end, "甲\n乙\n錯\n丁")]

        issues = check_ranges(entries, self.line_ids, self.start, self.end, self.source_lines)

        self.assertIn("all: Source block does not match data range", issues)

    def test_source_block_ignores_layout_and_star_apparatus(self) -> None:
        entries = [RangeEntry("all", self.start, self.end, "甲[＊] 乙\n丙\n\n丁")]

        issues = check_ranges(entries, self.line_ids, self.start, self.end, self.source_lines)

        self.assertEqual(issues, [])
        self.assertEqual(normalize_source_text("甲[＊]\n乙"), "甲乙")

    def test_source_block_does_not_hide_non_allowlisted_apparatus(self) -> None:
        entries = [RangeEntry("all", self.start, self.end, "甲[甲]乙丙丁")]

        issues = check_ranges(entries, self.line_ids, self.start, self.end, self.source_lines)

        self.assertIn("all: Source block does not match data range", issues)


class ClauseCoverageLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source_lines = {
            "T30n1579_p0001a01": "甲",
            "T30n1579_p0001a02": "乙",
            "T30n1579_p0001a03": "丙",
        }
        self.start = "T30n1579_p0001a01"
        self.end = "T30n1579_p0001a03"
        self.translation = "translated output\n"

    def ledger(self) -> dict:
        first_ids = ["T30n1579_p0001a01", "T30n1579_p0001a02"]
        second_ids = ["T30n1579_p0001a03"]
        all_ids = first_ids + second_ids
        return {
            "schema_version": "1.0",
            "work": "T1579",
            "juan": 1,
            "source_hash": sha256_text(canonical_source(self.source_lines, all_ids)),
            "translation_hash": sha256_text(self.translation),
            "clauses": [
                {
                    "clause_id": "c001",
                    "source_line_ids": first_ids,
                    "source_hash": sha256_text(canonical_source(self.source_lines, first_ids)),
                    "status": "covered",
                    "evidence": [
                        {"source_quote": "甲乙", "translation_quote": "translated"}
                    ],
                },
                {
                    "clause_id": "c002",
                    "source_line_ids": second_ids,
                    "source_hash": sha256_text(canonical_source(self.source_lines, second_ids)),
                    "status": "covered",
                    "evidence": [
                        {"source_quote": "丙", "translation_quote": "output"}
                    ],
                },
            ],
            "passed": True,
            "issues": [],
        }

    def test_valid_ledger_passes(self) -> None:
        self.assertEqual(
            check_coverage_ledger(
                self.ledger(), self.source_lines, self.start, self.end, self.translation
            ),
            [],
        )

    def test_stale_hash_and_missing_clause_line_fail(self) -> None:
        ledger = self.ledger()
        ledger["translation_hash"] = "0" * 64
        ledger["clauses"][0]["source_hash"] = "f" * 64
        ledger["clauses"][1]["source_line_ids"] = ["T30n1579_p0001a02"]

        issues = check_coverage_ledger(
            ledger, self.source_lines, self.start, self.end, self.translation
        )

        self.assertIn("ledger translation_hash does not match translation file", issues)
        self.assertIn("ledger clause c001 source_hash does not match source lines", issues)
        self.assertTrue(any("missing lines: 0001a03" in issue for issue in issues), issues)
        self.assertTrue(any("overlapping lines: 0001a02" in issue for issue in issues), issues)

    def test_duplicate_clause_id_and_uncovered_status_fail(self) -> None:
        ledger = self.ledger()
        ledger["clauses"][1]["clause_id"] = "c001"
        ledger["clauses"][1]["status"] = "partial"
        ledger["clauses"][1]["evidence"] = []

        issues = check_coverage_ledger(
            ledger, self.source_lines, self.start, self.end, self.translation
        )

        self.assertIn("duplicate ledger clause_id: c001", issues)
        self.assertIn("ledger clause 2 status must be covered", issues)
        self.assertIn("ledger clause 2 evidence must be a non-empty list", issues)

    def test_empty_or_malformed_evidence_fails(self) -> None:
        invalid_items = [
            {},
            {"source_quote": "", "translation_quote": "translated"},
            {"source_quote": "[＊]", "translation_quote": "translated"},
            {"source_quote": "甲", "translation_quote": ""},
            {"source_quote": "甲", "translation_quote": "   "},
            {"source_quote": "戊", "translation_quote": "translated"},
            {"source_quote": "甲", "translation_quote": "not present"},
            "quoted text",
        ]
        for item in invalid_items:
            with self.subTest(item=item):
                ledger = self.ledger()
                ledger["clauses"][0]["evidence"] = [item]

                issues = check_coverage_ledger(
                    ledger, self.source_lines, self.start, self.end, self.translation
                )

                self.assertTrue(
                    any("ledger clause c001 evidence 1" in issue for issue in issues), issues
                )

    def test_translation_evidence_cannot_point_to_source_block(self) -> None:
        translation = (
            "Source:\n<<<\nsource-only quote\n>>>\n\n"
            "Translation:\n<<<\nactual translated prose\n>>>\n"
        )
        ledger = self.ledger()
        ledger["translation_hash"] = sha256_text(translation)
        ledger["clauses"][0]["evidence"][0]["translation_quote"] = "source-only quote"
        ledger["clauses"][1]["evidence"][0]["translation_quote"] = "actual translated prose"

        issues = check_coverage_ledger(
            ledger, self.source_lines, self.start, self.end, translation
        )

        self.assertIn(
            "ledger clause c001 evidence 1 translation_quote is not in translated content",
            issues,
        )


class CoverageCliTests(unittest.TestCase):
    def test_requires_explicit_ledger_or_ranges_only_mode(self) -> None:
        base = [
            "check_translation_coverage.py",
            "--translation", "unused.md",
            "--data", "unused.json",
            "--start", "T30n1579_p0001a01",
            "--end", "T30n1579_p0001a02",
        ]
        with patch.object(sys, "argv", base), self.assertRaises(SystemExit) as missing:
            main()
        self.assertEqual(2, missing.exception.code)

        with patch.object(
            sys, "argv", [*base, "--ledger", "unused-ledger.json", "--ranges-only"]
        ), self.assertRaises(SystemExit) as conflicting:
            main()
        self.assertEqual(2, conflicting.exception.code)


if __name__ == "__main__":
    unittest.main()
