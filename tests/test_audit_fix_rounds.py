"""`audited.py audit --fix-rounds N`: up to N fix rounds per juan before the verdict (default 1)."""
import argparse
import contextlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import audited  # noqa: E402

JOB_ID = "20261022-000000-audit-abcd"
MD = "".join(
    f"## 0{n} 段{n}\nRange: T30n1579_p0694c1{n}-p0694c1{n}\n\nSource:\n<<<\n原文{n}原文\n>>>\n\n"
    f"Translation:\n<<<\n譯文{n}譯文\n>>>\n\n"
    for n in (1, 2)
)


def finding(section: int) -> dict:
    return {"unit_id": audited.unit_id({"work": "T1579"}, 72, section), "review_stage": "review_doctrine",
            "claim": f"段 {section + 1} 漏譯", "section_index": section}


class FixRoundsJobTest(unittest.TestCase):
    def test_a_new_job_has_one_fix_round_by_default(self):
        self.assertEqual(1, audited.new_audit_job("T1579", "72", "echo")["fix_rounds"])

    def test_a_new_job_keeps_the_rounds_it_is_given(self):
        self.assertEqual(2, audited.new_audit_job("T1579", "72", "opus", 2)["fix_rounds"])

    def test_fix_rounds_must_be_a_positive_integer(self):
        for bad in (0, -1, "2", 1.5, True):
            with self.subTest(bad=bad), self.assertRaises(SystemExit):
                audited.new_audit_job("T1579", "72", "echo", bad)

    def test_a_job_written_before_the_option_is_valid(self):
        job = audited.new_audit_job("T1579", "72", "echo")
        del job["fix_rounds"]
        self.assertIsNone(audited.audit_job_problem(job, job["id"]))

    def test_the_command_line_takes_fix_rounds(self):
        seen = {}
        with patch.object(sys, "argv", ["audited.py", "audit", "--juans", "72", "--fix-rounds", "2"]), \
             patch.object(audited, "cmd_audit", side_effect=lambda args: seen.update(vars(args)) or 0):
            audited.main()
        self.assertEqual(2, seen["fix_rounds"])

    def test_a_resume_cannot_change_the_fix_rounds(self):
        job = audited.new_audit_job("T1579", "72", "opus", 1)
        args = argparse.Namespace(resume=job["id"], juans=None, work="T1579", model=None, fix_rounds=2)
        with patch.object(audited, "acquire_runner_lock", contextlib.nullcontext), \
             patch.object(audited, "load_audit_job", return_value=job), \
             patch.object(audited, "audit_existing_juan") as audit_juan, \
             self.assertRaisesRegex(SystemExit, "fix rounds"):
            audited.cmd_audit(args)
        audit_juan.assert_not_called()


class FixRoundsReviewTest(unittest.TestCase):
    """review_audit_candidate with the reviews, the fixer and the gate replaced by recorders."""

    def run_rounds(self, rounds: int | None, script: dict[int, list[dict]]):
        job = {"model": "echo", "work": "T1579", "id": JOB_ID, "progress": {}}
        if rounds is not None:
            job["fix_rounds"] = rounds
        prog: dict = {}
        reviews, fixes, gates = [], [], []

        def fake_findings(job, juan, prog, candidate, round_number, only_units=None):
            reviews.append((round_number, only_units))
            return list(script.get(round_number, []))

        with tempfile.TemporaryDirectory() as tmp:
            candidate = Path(tmp) / "T1579-072-baihua.md"
            candidate.write_text(MD, encoding="utf-8")
            with patch.object(audited, "audit_findings", side_effect=fake_findings), \
                 patch.object(audited, "fix_findings",
                              side_effect=lambda job, juan, md, found, prog: fixes.append(found)), \
                 patch.object(audited, "gate_audit_candidate", side_effect=lambda *a: gates.append(a)), \
                 patch.object(audited, "pinned_review_inputs", contextlib.nullcontext), \
                 patch.object(audited, "save_job"):
                try:
                    audited.review_audit_candidate(job, 72, prog, candidate, Path(tmp), "s", "e")
                    blocked = None
                except audited.AuditBlocked as error:
                    blocked = error.reasons
        return reviews, fixes, gates, blocked, prog

    def test_two_rounds_fix_what_the_first_fix_left(self):
        a, b = finding(0), finding(1)
        reviews, fixes, gates, blocked, _ = self.run_rounds(2, {1: [a, b], 2: [b], 3: []})
        self.assertIsNone(blocked)
        self.assertEqual([[a, b], [b]], fixes)
        self.assertEqual([(1, None), (2, {a["unit_id"], b["unit_id"]}), (3, {b["unit_id"]})], reviews)
        self.assertEqual(1, len(gates))

    def test_one_round_blocks_on_what_the_first_fix_left(self):
        a, b = finding(0), finding(1)
        reviews, fixes, gates, blocked, prog = self.run_rounds(1, {1: [a, b], 2: [b], 3: []})
        self.assertEqual([[a, b]], fixes)
        self.assertEqual([audited.blocking_line(b)], blocked)
        self.assertEqual({"round1": 2, "remaining": 1}, prog["findings"])
        self.assertEqual([], gates)
        self.assertEqual(2, len(reviews))

    def test_a_job_without_the_field_runs_one_round(self):
        a, b = finding(0), finding(1)
        _, fixes, _, blocked, _ = self.run_rounds(None, {1: [a, b], 2: [b]})
        self.assertEqual(1, len(fixes))
        self.assertEqual([audited.blocking_line(b)], blocked)

    def test_the_last_round_still_blocks(self):
        a, b = finding(0), finding(1)
        _, fixes, gates, blocked, prog = self.run_rounds(2, {1: [a, b], 2: [b], 3: [b]})
        self.assertEqual(2, len(fixes))
        self.assertEqual([audited.blocking_line(b)], blocked)
        self.assertEqual({"round1": 2, "remaining": 1}, prog["findings"])
        self.assertEqual([], gates)

    def test_a_clean_first_review_goes_straight_to_the_gate(self):
        _, fixes, gates, blocked, _ = self.run_rounds(2, {})
        self.assertEqual([], fixes)
        self.assertIsNone(blocked)
        self.assertEqual(1, len(gates))

    def test_the_gate_review_follows_the_last_fix_round(self):
        for rounds, expected in ((None, 3), (1, 3), (2, 4)):
            job = {"model": "echo", "work": "T1579", "id": JOB_ID}
            if rounds is not None:
                job["fix_rounds"] = rounds
            seen = []

            def fake_findings(job, juan, prog, candidate, round_number, only_units=None):
                seen.append(round_number)
                return [finding(0)]

            with self.subTest(rounds=rounds), \
                 patch.object(audited, "audit_findings", side_effect=fake_findings), \
                 patch.object(audited, "save_job"), self.assertRaises(audited.AuditBlocked):
                audited.gate_audit_candidate(job, 72, {}, Path("unused.md"), Path("unused"), "s", "e", 1)
            self.assertEqual([expected], seen)


if __name__ == "__main__":
    unittest.main()
