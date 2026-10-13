"""Acceptance tests of the legacy audit: `python3 scripts/audited.py audit`.

The planner wrote this file before the code existed (plans/20261013-merge-audit-pipeline-claude). It is the
oracle of the feature, so the implementer does not change it.

Every test copies the scripts, the prompts and the T1579 inputs into a temporary tree and runs the command
there in a subprocess. Nothing in this checkout is written. There is no network: the CBETA data files of the
fixture juans are in the repo, and the model is a fake backend of scripts/llm.py or a patched subprocess.

Fixture juans (facts measured on main, commit dd46ba8):
  juan 7   12 sections, no shared boundary line, no glossary issue; section 2 has 6 paragraphs
  juan 67  23 sections, one shared boundary line, 5 glossary issues (one of the six juans the owner wants audited)
  juan 72   7 sections, one shared boundary line, 9 glossary issues
  juan 76  15 sections, one shared boundary line, no glossary issue; section 2 has 1 paragraph
"""
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WORK = "T1579"
FIXTURE_JUANS = (7, 67, 72, 76)
MARK = "【已修】"
MARKER_RE = re.compile(r"<!--\s*#([A-Za-z0-9_-]+)\s*-->")
RENDER_SNIPPET = (
    "import sys\n"
    "from pathlib import Path\n"
    "sys.path.insert(0, 'scripts')\n"
    "import build_translation_html as bth\n"
    "md = Path(sys.argv[1])\n"
    "text = md.read_text(encoding='utf-8')\n"
    "work, juan = bth.infer_work_juan(md)\n"
    "page = bth.render(bth.parse_entries(text), md, work, juan, bth.infer_title(text, work, juan))\n"
    "sys.stdout.buffer.write(page.encode('utf-8'))\n"
)
VERIFY_SNIPPET = (
    "import sys\n"
    "from pathlib import Path\n"
    "sys.path.insert(0, 'scripts')\n"
    "import publisher\n"
    "publisher.verify_attestation(Path('.').resolve(), Path(sys.argv[1]).resolve())\n"
    "print('sealed')\n"
)
# A driver runs the real command inside the copied tree with one function replaced, so a test can place an
# event (a kill, a failure, an edit by the owner) at an exact point of the audit.
DRIVER_HEAD = (
    "import json, os, re, subprocess, sys\n"
    "from pathlib import Path\n"
    "sys.path.insert(0, 'scripts')\n"
    "import audited\n"
    "import build_translation_html as bth\n"
    "import llm\n"
)
CAPTURE_BACKEND = """
CALLS = []
def fake_run(command, input=None, **_kwargs):
    CALLS.append(command)
    payload = json.loads(re.search(r"輸入 JSON：\\n(.*?)\\n\\n輸出 JSON Schema：", input, re.S).group(1))
    answer = {"schema_version": "1.0", "stage": payload["stage"], "unit_id": payload["unit_id"],
              "source_hash": payload["source_hash"], "verdict": "pass", "findings": [], "proposed_terms": []}
    Path(command[command.index("-o") + 1]).write_text(json.dumps(answer, ensure_ascii=False), encoding="utf-8")
    Path("commands.json").write_text(json.dumps(CALLS), encoding="utf-8")
    return subprocess.CompletedProcess(command, 0, "", "")
llm.subprocess.run = fake_run
"""
KILL_AT_PAGE_BUILD = """
def killed(*_args, **_kwargs):
    os._exit(9)
bth.build_page = killed
"""
FAIL_AT_SEAL = """
def failing(*_args, **_kwargs):
    raise RuntimeError("seal failed on purpose")
audited.create_legacy_attestation = failing
"""
OWNER_EDITS_AFTER_GATE = """
real_gate = audited.record_quality_gate
def gate_then_owner_edit(*args, **kwargs):
    result = real_gate(*args, **kwargs)
    path = Path("translations/T1579-007-baihua.md")
    path.write_text(path.read_text(encoding="utf-8") + "\\nOWNER-EDIT\\n", encoding="utf-8")
    return result
audited.record_quality_gate = gate_then_owner_edit
"""


def md_name(juan: int) -> str:
    return f"{WORK}-{juan:03d}-baihua.md"


def build_template(dest: Path) -> None:
    """The smallest tree in which the audit of a fixture juan can run and rebuild its page."""
    ignore = shutil.ignore_patterns("__pycache__")
    shutil.copytree(REPO / "scripts", dest / "scripts", ignore=ignore)
    shutil.copytree(REPO / "prompts", dest / "prompts")
    shutil.copytree(REPO / "translations", dest / "translations")
    shutil.copy2(REPO / "works.json", dest / "works.json")
    (dest / "data").mkdir()
    pages = dest / "docs" / WORK / "translations"
    pages.mkdir(parents=True)
    for juan in FIXTURE_JUANS:
        shutil.copy2(REPO / "data" / f"{WORK}-{juan:03d}.json", dest / "data")
        shutil.copy2(REPO / "docs" / WORK / "translations" / f"{WORK}-{juan:03d}-baihua.html", pages)
    shutil.copy2(REPO / "docs" / "index.html", dest / "docs" / "index.html")
    shutil.copy2(REPO / "docs" / WORK / "index.html", dest / "docs" / WORK / "index.html")
    workflow = REPO / "docs" / WORK / "docs" / "translation-workflow.html"  # the reader page links it when it exists
    if workflow.exists():
        (dest / "docs" / WORK / "docs").mkdir()
        shutil.copy2(workflow, dest / "docs" / WORK / "docs" / workflow.name)
    for search in sorted((REPO / "docs").glob("*/search.json")):
        target = dest / "docs" / search.parent.name
        target.mkdir(parents=True, exist_ok=True)
        shutil.copy2(search, target / "search.json")


def run_python(tree: Path, *args: str, timeout: int = 180) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run([sys.executable, "-B", *args], cwd=tree, env=env,
                          capture_output=True, text=True, timeout=timeout)


def audit(tree: Path, *args: str) -> subprocess.CompletedProcess:
    return run_python(tree, str(tree / "scripts" / "audited.py"), "audit", *args)


def drive(tree: Path, patch: str, *args: str) -> subprocess.CompletedProcess:
    """Run `audited.py audit <args>` in the tree with the functions that `patch` replaces."""
    script = DRIVER_HEAD + patch + f"\nsys.argv = {['audited.py', 'audit', *args]!r}\nraise SystemExit(audited.main())\n"
    (tree / "driver.py").write_text(script, encoding="utf-8")
    return run_python(tree, "driver.py")


def digest(tree: Path, *roots: str) -> str:
    """One hash over every file below the given folders: paths and bytes."""
    hasher = hashlib.sha256()
    for root in roots:
        for path in sorted((tree / root).rglob("*")):
            if path.is_file():
                hasher.update(path.relative_to(tree).as_posix().encode("utf-8") + b"\0")
                hasher.update(path.read_bytes() + b"\0")
    return hasher.hexdigest()


def sections(text: str) -> list[str]:
    """The md split at its `## ` headings; item 0 is the text before the first section."""
    return re.split(r"(?m)^(?=## )", text)


def translation_block(section: str) -> str:
    return re.search(r"Translation:\n<<<\n(.*?)\n>>>", section, flags=re.DOTALL).group(1)


class LegacyAuditAcceptance(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls) -> None:
        cls._template_dir = tempfile.TemporaryDirectory()
        cls.template = Path(cls._template_dir.name) / "template"
        build_template(cls.template)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._template_dir.cleanup()

    def setUp(self) -> None:
        self._tree_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tree_dir.cleanup)
        self.outside = Path(self._tree_dir.name)
        self.tree = self.outside / "repo"
        shutil.copytree(self.template, self.tree)

    # ---------- helpers ----------

    def md(self, juan: int) -> Path:
        return self.tree / "translations" / md_name(juan)

    def page(self, juan: int) -> Path:
        return self.tree / "docs" / WORK / "translations" / f"{WORK}-{juan:03d}-baihua.html"

    def report(self, juan: int) -> dict:
        found = sorted((self.tree / "audits").glob(f"*/{WORK}-{juan:03d}-audit-report.json"))
        self.assertEqual(1, len(found), f"expected one audit report of juan {juan}: {found}")
        return json.loads(found[0].read_text(encoding="utf-8"))

    def only_job(self) -> Path:
        found = sorted((self.tree / "jobs" / "audited").glob("*.json"))
        self.assertEqual(1, len(found), found)
        return found[0]

    def events(self) -> list[dict]:
        records = []
        for path in sorted((self.tree / "audits").glob("*/events.jsonl")):
            records += [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        return records

    def llm_stages(self) -> list[str]:
        return [event["stage"] for event in self.events() if event.get("type") == "llm_attempt"]

    def reviews_of_other_units(self) -> int:
        return sum(1 for event in self.events()
                   if event.get("type") == "llm_attempt" and event["stage"].startswith("review_")
                   and not event["unit_id"].endswith("-002"))

    def assert_exit(self, done: subprocess.CompletedProcess, code: int) -> None:
        self.assertEqual(code, done.returncode, f"stdout:\n{done.stdout}\nstderr:\n{done.stderr}")

    def assert_no_production_state(self) -> None:
        self.assertEqual([], sorted(path.name for path in (self.tree / "jobs").glob("*.json")),
                         "the audit must not write a job file into the production job folder")
        self.assertFalse((self.tree / "docs" / "status.json").exists(),
                         "the audit must not write the production status file")
        self.assertFalse((self.tree / ".git").exists(), "the audit never runs git")

    def assert_sealed(self, report: dict) -> None:
        self.assertEqual("sealed", report["install"])
        self.assertIsInstance(report["attestation"], str)
        self.assertTrue((self.tree / report["attestation"]).is_file(), report["attestation"])
        done = run_python(self.tree, "-c", VERIFY_SNIPPET, report["attestation"])
        self.assertEqual("sealed", done.stdout.strip(), done.stderr)

    def assert_not_sealed(self, report: dict) -> None:
        self.assertIsNone(report["attestation"])
        self.assertEqual([], sorted((self.tree / "audits" / report["job_id"]).glob("*attestation*")))

    def assert_page_matches_md(self, juan: int) -> None:
        done = subprocess.run([sys.executable, "-B", "-c", RENDER_SNIPPET, f"translations/{md_name(juan)}"],
                              cwd=self.tree, capture_output=True, timeout=120,
                              env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        self.assertEqual(0, done.returncode, done.stderr.decode("utf-8", "replace"))
        self.assertEqual(done.stdout, self.page(juan).read_bytes(),
                         "the published page must be the legacy rendering of the md")

    # ---------- a clean juan ----------

    def test_clean_juan_passes_and_changes_nothing(self) -> None:
        before = digest(self.tree, "docs", "translations")

        done = audit(self.tree, "--juans", "7", "--model", "echo")

        self.assert_exit(done, 0)
        self.assertEqual(before, digest(self.tree, "docs", "translations"),
                         "an audit without a finding must not change one byte of the site or the translations")
        report = self.report(7)
        self.assertEqual("pass", report["verdict"])
        self.assertEqual((WORK, 7, 12), (report["work"], report["juan"], report["sections_total"]))
        self.assertEqual(([], [], []), (report["rewritten_units"], report["warnings"], report["blocking"]))
        self.assertEqual({"round1": 0, "remaining": 0}, report["findings"])
        self.assert_sealed(report)
        self.assert_no_production_state()

        job_dir = self.tree / "audits" / report["job_id"]
        ledger = json.loads((job_dir / f"{WORK}-007-coverage.json").read_text(encoding="utf-8"))
        self.assertIs(True, ledger["passed"])
        self.assertEqual([], ledger["issues"])
        self.assertEqual("at_least_once", ledger["coverage"])
        self.assertEqual(12, len(ledger["clauses"]))
        for clause in ledger["clauses"]:
            self.assertEqual("covered", clause["status"])
            self.assertEqual(["review_terms", "review_doctrine"], [item["stage"] for item in clause["evidence"]])

        stages = self.llm_stages()
        self.assertEqual(12, stages.count("review_terms"))
        self.assertEqual(12, stages.count("review_doctrine"))
        self.assertEqual({"review_terms", "review_doctrine"}, set(stages),
                         "a legacy audit translates nothing and has no parallel-source review")
        gates = [event for event in self.events() if event.get("type") == "quality_gate"]
        self.assertEqual(["pass"], [gate["verdict"] for gate in gates])
        self.assertEqual("legacy_review", gates[0]["audit_kind"])
        job = json.loads(self.only_job().read_text(encoding="utf-8"))
        self.assertEqual(("legacy_audit", "echo", report["job_id"]), (job["kind"], job["model"], job["id"]))

    def test_shared_boundary_line_is_a_warning(self) -> None:
        before = digest(self.tree, "docs", "translations")

        done = audit(self.tree, "--juans", "76", "--model", "echo")

        self.assert_exit(done, 0)
        report = self.report(76)
        self.assertEqual("pass", report["verdict"])
        self.assertEqual(15, report["sections_total"])
        self.assertEqual([{"kind": "shared_boundary_lines", "line_ids": ["T30n1579_p0721a09"]}], report["warnings"])
        self.assertEqual(before, digest(self.tree, "docs", "translations"))
        self.assert_sealed(report)

    # ---------- a rewritten section ----------

    def test_flagged_section_is_rewritten_and_everything_else_is_kept(self) -> None:
        old_md = self.md(7).read_text(encoding="utf-8")
        old_page = self.page(7).read_bytes()
        old_sections = sections(old_md)
        self.assertEqual(13, len(old_sections))  # the header and 12 sections

        done = audit(self.tree, "--juans", "7", "--model", "echo-finding")

        self.assert_exit(done, 0)
        report = self.report(7)
        self.assertEqual("pass", report["verdict"])
        self.assertEqual([f"{WORK}-007-002"], report["rewritten_units"])
        self.assertEqual({"round1": 1, "remaining": 0}, report["findings"])
        self.assert_sealed(report)

        new_sections = sections(self.md(7).read_text(encoding="utf-8"))
        self.assertEqual(13, len(new_sections))
        for index, (old, new) in enumerate(zip(old_sections, new_sections)):
            if index != 2:
                self.assertEqual(old, new, f"section {index} was not flagged, so it must be byte-identical")
        old_block, new_block = translation_block(old_sections[2]), translation_block(new_sections[2])
        self.assertEqual(old_sections[2].replace(old_block, ""), new_sections[2].replace(new_block, ""),
                         "only the Translation block of the flagged section may change")
        self.assertEqual(1, new_block.count(MARK))
        old_ids = MARKER_RE.findall(old_block)
        self.assertEqual(6, len(old_ids))
        self.assertEqual(old_ids, MARKER_RE.findall(new_block), "the paragraph ids of a rewritten section are kept")
        self.assertEqual(MARKER_RE.sub("", old_block).rstrip() + MARK, MARKER_RE.sub("", new_block).rstrip())

        self.assertNotEqual(old_page, self.page(7).read_bytes())
        self.assertIn(MARK, self.page(7).read_text(encoding="utf-8"))
        self.assert_page_matches_md(7)
        self.assertIn(MARK, (self.tree / "docs" / WORK / "search.json").read_text(encoding="utf-8"))

        stages = self.llm_stages()
        self.assertEqual(1, stages.count("fix"))
        self.assertEqual(13, stages.count("review_terms"), "12 sections in round 1, and only the rewritten one again")
        self.assertEqual(13, stages.count("review_doctrine"))
        self.assertEqual({"review_terms", "review_doctrine", "fix"}, set(stages))
        self.assert_no_production_state()

    def test_fixer_that_merges_paragraphs_fails_and_keeps_every_id(self) -> None:
        before = digest(self.tree, "docs", "translations")

        done = audit(self.tree, "--juans", "7", "--model", "echo-finding-merge")

        self.assert_exit(done, 1)
        self.assertEqual(before, digest(self.tree, "docs", "translations"),
                         "a rewrite that would drop a paragraph id must not reach the translation")
        report = self.report(7)
        self.assertEqual(("fail", "none"), (report["verdict"], report["install"]))
        self.assertEqual({"round1": 1, "remaining": 0}, report["findings"])
        self.assertTrue(any("paragraph id" in line for line in report["blocking"]), report["blocking"])
        self.assert_not_sealed(report)
        self.assertTrue((self.tree / "audits" / report["job_id"] / "candidate" / md_name(7)).is_file())

    # ---------- failures leave the site alone ----------

    def test_unresolved_finding_fails_and_touches_nothing(self) -> None:
        before = digest(self.tree, "docs", "translations")

        done = audit(self.tree, "--juans", "7", "--model", "echo-finding-stubborn")

        self.assert_exit(done, 1)
        self.assertEqual(before, digest(self.tree, "docs", "translations"),
                         "a failed audit must leave the translation and the site as they were")
        report = self.report(7)
        self.assertEqual(("fail", "none"), (report["verdict"], report["install"]))
        self.assertEqual([], report["rewritten_units"])
        self.assertEqual(1, report["findings"]["round1"])
        self.assertGreaterEqual(report["findings"]["remaining"], 1)
        self.assertTrue(report["blocking"])
        self.assert_not_sealed(report)
        self.assertTrue((self.tree / "audits" / report["job_id"] / "candidate" / md_name(7)).is_file(),
                        "the candidate with the attempted fix stays in the job folder for the owner")
        self.assertEqual(1, self.llm_stages().count("fix"))

    def test_glossary_issues_the_fixer_cannot_repair_fail_and_touch_nothing(self) -> None:
        before = digest(self.tree, "docs", "translations")

        done = audit(self.tree, "--juans", "72", "--model", "echo")

        self.assert_exit(done, 1)
        self.assertEqual(before, digest(self.tree, "docs", "translations"))
        report = self.report(72)
        self.assertEqual("fail", report["verdict"])
        self.assertEqual(7, report["sections_total"])
        self.assertEqual([{"kind": "shared_boundary_lines", "line_ids": ["T30n1579_p0695a23"]}], report["warnings"])
        self.assertTrue(any("lacks one of" in line for line in report["blocking"]), report["blocking"])
        self.assert_not_sealed(report)
        self.assertGreaterEqual(self.llm_stages().count("fix"), 1,
                                "a glossary issue goes to the fixer like a review finding")

    def test_a_juan_the_owner_wants_audited_fails_closed(self) -> None:
        before = digest(self.tree, "docs", "translations")

        done = audit(self.tree, "--juans", "67", "--model", "echo")

        self.assert_exit(done, 1)
        self.assertEqual(before, digest(self.tree, "docs", "translations"))
        report = self.report(67)
        self.assertEqual(("fail", 23), (report["verdict"], report["sections_total"]))
        self.assertEqual([{"kind": "shared_boundary_lines", "line_ids": ["T30n1579_p0669a29"]}], report["warnings"])
        self.assertTrue(any("lacks one of" in line for line in report["blocking"]), report["blocking"])
        self.assertEqual(23, self.llm_stages().count("review_doctrine") - self.llm_stages().count("fix"),
                         "round 1 reviews all 23 sections; the re-review covers only the sections the fixer touched")

    def test_missing_source_lines_fail_before_any_model_call(self) -> None:
        parts = sections(self.md(7).read_text(encoding="utf-8"))
        del parts[3]  # drop the third section: its source lines are now covered by no section
        self.md(7).write_text("".join(parts), encoding="utf-8")
        before = digest(self.tree, "docs", "translations")

        done = audit(self.tree, "--juans", "7", "--model", "echo")

        self.assert_exit(done, 1)
        self.assertEqual(before, digest(self.tree, "docs", "translations"))
        report = self.report(7)
        self.assertEqual("fail", report["verdict"])
        self.assertTrue(any("missing lines" in line for line in report["blocking"]), report["blocking"])
        self.assertEqual([], self.llm_stages(), "a structural failure must cost no model call")

    def test_empty_translation_fails_before_any_model_call(self) -> None:
        parts = sections(self.md(7).read_text(encoding="utf-8"))
        parts[1] = parts[1].replace(translation_block(parts[1]), "")
        self.md(7).write_text("".join(parts), encoding="utf-8")
        before = digest(self.tree, "docs", "translations")

        done = audit(self.tree, "--juans", "7", "--model", "echo")

        self.assert_exit(done, 1)
        self.assertEqual(before, digest(self.tree, "docs", "translations"))
        report = self.report(7)
        self.assertEqual("fail", report["verdict"])
        self.assertTrue(report["blocking"])
        self.assertEqual([], self.llm_stages())

    # ---------- the real backend ----------

    def test_auto_model_calls_the_pinned_codex_backend(self) -> None:
        before = digest(self.tree, "docs", "translations")

        done = drive(self.tree, CAPTURE_BACKEND, "--juans", "7")  # no --model: the default is auto

        self.assert_exit(done, 0)
        commands = json.loads((self.tree / "commands.json").read_text(encoding="utf-8"))
        self.assertEqual(24, len(commands), "12 sections, two reviews each, every one through the real backend")
        for command in commands:
            self.assertEqual(["codex", "exec"], command[:2])
            self.assertEqual("gpt-5.6-sol", command[command.index("--model") + 1])
            self.assertTrue(any(option in command for option in ('model_reasoning_effort="high"',
                                                                 'model_reasoning_effort="xhigh"')), command)
        self.assertEqual("auto", json.loads(self.only_job().read_text(encoding="utf-8"))["model"])
        attempts = [event for event in self.events() if event.get("type") == "llm_attempt"]
        self.assertEqual(24, len(attempts))
        self.assertEqual({"gpt-5.6-sol"}, {event["model"] for event in attempts})
        self.assertEqual("pass", self.report(7)["verdict"])
        self.assertEqual(before, digest(self.tree, "docs", "translations"))

    # ---------- the job as a whole ----------

    def test_resume_of_a_finished_job_does_nothing(self) -> None:
        first = audit(self.tree, "--juans", "7", "--model", "echo")
        self.assert_exit(first, 0)
        job_id = self.report(7)["job_id"]
        before = digest(self.tree, "docs", "translations", "audits", "artifacts")
        calls = len(self.llm_stages())

        again = audit(self.tree, "--resume", job_id)

        self.assert_exit(again, 0)
        self.assertEqual(calls, len(self.llm_stages()), "a finished juan must not be reviewed again")
        self.assertEqual(before, digest(self.tree, "docs", "translations", "audits", "artifacts"))
        self.assertEqual("pass", self.report(7)["verdict"])

    def test_two_juans_run_in_one_job_and_one_failure_gives_exit_1(self) -> None:
        done = audit(self.tree, "--juans", "72,76", "--model", "echo")

        self.assert_exit(done, 1)
        failed, passed = self.report(72), self.report(76)
        self.assertEqual(("fail", "pass"), (failed["verdict"], passed["verdict"]))
        self.assertEqual(failed["job_id"], passed["job_id"])
        self.assert_sealed(passed)

    def test_two_rewritten_juans_keep_both_seals_and_resume_is_free(self) -> None:
        done = audit(self.tree, "--juans", "7,76", "--model", "echo-finding")

        self.assert_exit(done, 0)
        first, second = self.report(7), self.report(76)
        self.assertEqual([f"{WORK}-007-002"], first["rewritten_units"])
        self.assertEqual([f"{WORK}-076-002"], second["rewritten_units"])
        self.assert_sealed(second)
        self.assert_sealed(first)  # the rewrite of juan 76 rebuilt the shared search index after juan 7 was sealed
        before = digest(self.tree, "docs", "translations", "audits", "artifacts")
        calls = len(self.llm_stages())

        again = audit(self.tree, "--resume", first["job_id"])

        self.assert_exit(again, 0)
        self.assertEqual(calls, len(self.llm_stages()))
        self.assertEqual(before, digest(self.tree, "docs", "translations", "audits", "artifacts"))

    def test_resume_reuses_reviews_until_an_input_changes(self) -> None:
        first = audit(self.tree, "--juans", "7", "--model", "echo-finding-stubborn")
        self.assert_exit(first, 1)
        job_id = self.report(7)["job_id"]
        self.assertEqual(22, self.reviews_of_other_units())  # 11 sections, two reviews each

        second = audit(self.tree, "--resume", job_id)

        self.assert_exit(second, 1)
        self.assertEqual(22, self.reviews_of_other_units(),
                         "a resume must not review again a section whose review inputs did not change")

        policy = self.tree / "prompts" / "audited" / "common_policy.txt"
        policy.write_text(policy.read_text(encoding="utf-8") + "\n- 測試用的新增規則。\n", encoding="utf-8")
        third = audit(self.tree, "--resume", job_id)

        self.assert_exit(third, 1)
        self.assertEqual(44, self.reviews_of_other_units(),
                         "a changed policy changes every review prompt, so no earlier review may be reused")

    def test_kill_after_the_swap_rolls_forward_on_resume(self) -> None:
        killed = drive(self.tree, KILL_AT_PAGE_BUILD, "--juans", "7", "--model", "echo-finding")

        self.assert_exit(killed, 9)
        self.assertIn(MARK, self.md(7).read_text(encoding="utf-8"), "the kill came after the translation was swapped")
        job_id = self.only_job().stem
        self.assertEqual([], sorted((self.tree / "audits" / job_id).glob("*attestation*")))
        calls = len(self.llm_stages())

        resumed = audit(self.tree, "--resume", job_id)

        self.assert_exit(resumed, 0)
        self.assertEqual(calls, len(self.llm_stages()), "the review was done before the kill; resume only finishes the install")
        report = self.report(7)
        self.assertEqual(("pass", [f"{WORK}-007-002"]), (report["verdict"], report["rewritten_units"]))
        self.assert_sealed(report)
        self.assert_page_matches_md(7)

    def test_seal_failure_restores_the_translation(self) -> None:
        before = digest(self.tree, "docs", "translations")

        done = drive(self.tree, FAIL_AT_SEAL, "--juans", "7", "--model", "echo-finding")

        self.assert_exit(done, 1)
        self.assertEqual(before, digest(self.tree, "docs", "translations"),
                         "a failure after the swap must put back the translation, its page and the search index")
        report = self.report(7)
        self.assertEqual(("fail", "none"), (report["verdict"], report["install"]))
        self.assertTrue(any("seal failed on purpose" in line for line in report["blocking"]), report["blocking"])
        self.assert_not_sealed(report)

    def test_translation_edited_during_the_audit_is_never_overwritten(self) -> None:
        original = self.md(7).read_text(encoding="utf-8")
        page = self.page(7).read_bytes()

        done = drive(self.tree, OWNER_EDITS_AFTER_GATE, "--juans", "7", "--model", "echo-finding")

        self.assert_exit(done, 1)
        self.assertEqual(original + "\nOWNER-EDIT\n", self.md(7).read_text(encoding="utf-8"),
                         "text the owner saved while the audit ran is kept")
        self.assertEqual(page, self.page(7).read_bytes())
        report = self.report(7)
        self.assertEqual(("fail", "none"), (report["verdict"], report["install"]))
        self.assertTrue(any("changed during the audit" in line for line in report["blocking"]), report["blocking"])
        self.assert_not_sealed(report)

    # ---------- what the command refuses ----------

    def test_refuses_to_run_while_a_runner_holds_the_lock(self) -> None:
        locks = self.tree / "locks"
        locks.mkdir()
        before = digest(self.tree, "docs", "translations")
        with open(locks / "runner.pid", "w") as holder:
            fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)

            done = audit(self.tree, "--juans", "7", "--model", "echo")

        self.assertNotEqual(0, done.returncode, done.stdout)
        self.assertIn("another runner is already active", done.stdout + done.stderr)
        self.assertFalse((self.tree / "audits").exists())
        self.assertEqual(before, digest(self.tree, "docs", "translations"))

    def test_other_works_are_refused(self) -> None:
        done = audit(self.tree, "--work", "T1821", "--juans", "1", "--model", "echo")

        self.assertNotEqual(0, done.returncode, done.stdout)
        self.assertIn("T1579", done.stdout + done.stderr)
        self.assertFalse((self.tree / "audits").exists())

        queued = run_python(self.tree, "scripts/audited.py", "enqueue", "--work", "T1821", "--juans", "1",
                            "--model", "echo", "--no-push")

        self.assertNotEqual(0, queued.returncode, queued.stdout)
        self.assertIn("T1579", queued.stdout + queued.stderr)
        self.assertEqual([], sorted((self.tree / "jobs").rglob("*.json")), "the audited pipeline is T1579 only")

    def test_resume_rejects_what_is_not_a_job_id(self) -> None:
        for value in ("../outside", "../../outside", "x", str(self.outside / "outside"), "20261013-120000-audit-zzzz"):
            with self.subTest(value=value):
                done = audit(self.tree, "--resume", value)
                self.assertNotEqual(0, done.returncode, done.stdout)
                self.assertIn("invalid job id", done.stdout + done.stderr)
        self.assertEqual(["repo"], sorted(path.name for path in self.outside.iterdir()),
                         "a resume id must never create a path outside the tree")
        self.assertFalse((self.tree / "audits").exists())
        self.assertFalse((self.tree / "jobs").exists())

    def test_resume_rejects_a_job_file_that_was_tampered_with(self) -> None:
        self.assert_exit(audit(self.tree, "--juans", "7", "--model", "echo-finding-stubborn"), 1)
        job_path = self.only_job()
        saved = job_path.read_text(encoding="utf-8")
        calls = len(self.llm_stages())
        for field, value in (("work", "T1821"), ("kind", "translation"), ("juans", ["7"]), ("juans", [])):
            with self.subTest(field=field, value=value):
                job_path.write_text(json.dumps({**json.loads(saved), field: value}, ensure_ascii=False), encoding="utf-8")
                done = audit(self.tree, "--resume", job_path.stem)
                self.assertNotEqual(0, done.returncode, done.stdout)
                self.assertIn("invalid audit job", done.stdout + done.stderr)
        self.assertEqual(calls, len(self.llm_stages()), "a tampered job must cost no model call")

    # ---------- the ledger can be checked again ----------

    def test_legacy_ledger_passes_the_coverage_cli_and_a_gap_does_not(self) -> None:
        self.assert_exit(audit(self.tree, "--juans", "76", "--model", "echo"), 0)
        report = self.report(76)
        ledger_path = f"audits/{report['job_id']}/{WORK}-076-coverage.json"
        ledger = json.loads((self.tree / ledger_path).read_text(encoding="utf-8"))
        start, end = ledger["clauses"][0]["source_line_ids"][0], ledger["clauses"][-1]["source_line_ids"][-1]
        command = ("scripts/check_translation_coverage.py", "--translation", f"translations/{md_name(76)}",
                   "--data", f"data/{WORK}-076.json", "--start", start, "--end", end, "--ledger", ledger_path)

        checked = run_python(self.tree, *command)

        self.assert_exit(checked, 0)  # the ledger declares at_least_once, so the shared boundary line is accepted

        parts = sections(self.md(76).read_text(encoding="utf-8"))
        del parts[3]
        self.md(76).write_text("".join(parts), encoding="utf-8")

        self.assert_exit(run_python(self.tree, *command), 1)


if __name__ == "__main__":
    unittest.main()
