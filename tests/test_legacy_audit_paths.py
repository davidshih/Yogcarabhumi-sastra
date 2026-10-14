"""Paths of `python3 scripts/audited.py audit` that tests/test_legacy_audit_acceptance.py does not walk.

Like the acceptance tests, every test copies the scripts and the inputs of juan 7 into a temporary tree and
runs the command there in a subprocess, with an offline backend. A driver places one event (a kill, a save
by the owner) at an exact point. This file only borrows the tree builder and the runners of the acceptance
module; it changes nothing in it.
"""
import gzip
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import audited  # noqa: E402
import test_legacy_audit_acceptance as fixture  # noqa: E402  (helpers only: a module, so no test runs twice)

WORK, MARK = fixture.WORK, fixture.MARK
OWNER_SAVES_AFTER_THE_INSTALL = """
real_page = audited.build_legacy_page
def page_then_owner_save(md_path, *args, **kwargs):
    result = real_page(md_path, *args, **kwargs)
    path = Path("translations/T1579-007-baihua.md")
    saved = path.read_bytes() + b"\\nOWNER-AFTER-INSTALL\\n"
    path.write_bytes(saved)
    Path("owner-saved.md").write_bytes(saved)
    return result
audited.build_legacy_page = page_then_owner_save
"""
# The real backend path (model auto), with a CLI that answers with something that is not the JSON contract.
ANSWER_IS_NOT_THE_CONTRACT = """
def fake_run(command, input=None, **_kwargs):
    Path(command[command.index("-o") + 1]).write_text("not json", encoding="utf-8")
    return subprocess.CompletedProcess(command, 0, "", "")
llm.subprocess.run = fake_run
"""
KILL = """
def killed(*_args, **_kwargs):
    os._exit(9)
"""
KILL_BEFORE_THE_INSTALL_IS_RECORDED = KILL + "audited.record_install = killed\n"
KILL_AT_THE_SEAL = KILL + "audited.create_legacy_attestation = killed\n"  # the page and the indexes are rebuilt by then
THE_CANDIDATE_CANNOT_BE_LINKED = """
real_link = os.link
def fail_candidate_link(src, dst, *args, **kwargs):
    if Path(src).name.endswith(".candidate"):
        raise OSError("simulated candidate link failure")
    return real_link(src, dst, *args, **kwargs)
os.link = fail_candidate_link
"""
NO_HARD_LINK_ONTO_THE_TRANSLATION = """
real_link = os.link
def no_link_onto_the_translation(src, dst, *args, **kwargs):
    if Path(dst).name == "T1579-007-baihua.md":
        raise OSError(18, "Cross-device link")
    return real_link(src, dst, *args, **kwargs)
os.link = no_link_onto_the_translation
"""
THE_STAGED_CANDIDATE_CANNOT_BE_REMOVED = """
real_unlink = os.unlink
def keep_the_staged_candidate(path, *args, **kwargs):
    if str(path).endswith(".candidate"):
        raise OSError(5, "Input/output error")
    return real_unlink(path, *args, **kwargs)
os.unlink = keep_the_staged_candidate
"""
THE_ORIGINAL_CANNOT_BE_LINKED = """
real_link = os.link
def no_link_from_the_first_backup(src, dst, *args, **kwargs):
    if Path(src).name == "1-T1579-007-baihua.md":
        raise OSError(5, "Input/output error")
    return real_link(src, dst, *args, **kwargs)
os.link = no_link_from_the_first_backup
"""
# After the install, no file of the backup folder can be linked back to the path of the translation.
NOTHING_GOES_BACK = """
real_link = os.link
def no_link_from_the_backup_folder(src, dst, *args, **kwargs):
    if Path(src).parent.name == "backup" and not str(src).endswith(".candidate"):
        raise OSError(5, "Input/output error")
    return real_link(src, dst, *args, **kwargs)
os.link = no_link_from_the_backup_folder
"""
NO_HARD_LINKS_AT_ALL = """
def no_hard_link(src, dst, *args, **kwargs):
    raise OSError(1, "Operation not permitted")
os.link = no_hard_link
"""
# While an install is undone, the owner saves a file at the path between the two moves of the restore.
OWNER_SAVES_WHILE_THE_INSTALL_IS_UNDONE = """
real_replace = os.replace
MOVES = []
def replace_then_owner_save(src, dst, *args, **kwargs):
    real_replace(src, dst, *args, **kwargs)
    if Path(src).name == "T1579-007-baihua.md" and Path(dst).parent.name == "backup":
        MOVES.append(dst)
        if len(MOVES) == 2:  # the first move is the install, the second one the restore
            Path(src).write_bytes(b"OWNER-SAVE-DURING-THE-RESTORE")
os.replace = replace_then_owner_save
"""
KILL_WHILE_THE_LINK_PROBE_EXISTS = """
real_unlink = os.unlink
def kill_at_the_probe(path, *args, **kwargs):
    if Path(path).name == "T1579-007-baihua.md.link" and Path(path).exists():
        os._exit(9)  # the probe was made and is not removed yet
    return real_unlink(path, *args, **kwargs)
os.unlink = kill_at_the_probe
"""
NEW_POLICY = """
def new_policy():
    policy = Path("prompts/audited/common_policy.txt")
    policy.write_text(policy.read_text(encoding="utf-8") + "\\n- New policy.\\n", encoding="utf-8")
"""
THE_POLICY_CHANGES_AFTER_THE_FIRST_ANSWER = fixture.CAPTURE_BACKEND + NEW_POLICY + """
real_backend = llm.subprocess.run
def answer_then_new_policy(command, input=None, **kwargs):
    result = real_backend(command, input=input, **kwargs)
    if len(CALLS) == 1:
        new_policy()
    return result
llm.subprocess.run = answer_then_new_policy
"""


def a_late_change(change: str) -> str:
    """A driver: in the last check of the reviews (round 3), change() runs once before the first prompt is built.

    `change` is driver code that defines change(). When it also defines undo(), that runs before the
    second prompt is built. The audit has pinned its review inputs by then.
    """
    return f"""
real_prompt, real_pass = audited.prompt_text, audited.review_pass
PROMPTS = []
{change}
def prompt_in_the_last_check(name, **vars):
    PROMPTS.append(name)
    if len(PROMPTS) == 1:
        change()
    elif len(PROMPTS) == 2 and "undo" in globals():
        undo()
    return real_prompt(name, **vars)
def patch_the_last_check(job, juan, md_path, pass_name, prog, round_number, **kwargs):
    audited.prompt_text = prompt_in_the_last_check if round_number == 3 else real_prompt
    return real_pass(job, juan, md_path, pass_name, prog, round_number, **kwargs)
audited.review_pass = patch_the_last_check
"""


LATE_POLICY = """
POLICY = Path("prompts/audited/common_policy.txt")
BEFORE = []
def change():
    BEFORE.append(POLICY.read_text(encoding="utf-8"))
    POLICY.write_text(BEFORE[0] + "\\n- Late policy.\\n", encoding="utf-8")
"""
LATE_POLICY_AND_BACK = LATE_POLICY + """
def undo():
    POLICY.write_text(BEFORE[0], encoding="utf-8")
"""
LATE_GLOSSARY = """
def change():
    glossary = Path("translations/glossary/T1579-terms.json")
    data = json.loads(glossary.read_text(encoding="utf-8"))
    data["terms"].append({"id": "test-late", "xuanzang": "測試用玄奘詞", "display": "測試用玄奘詞",
                          "plain": "測試用白話詞", "source_terms": ["。"],
                          "translation_patterns": ["沒有任何譯文會有的字串"], "status": "test", "rule": "test"})
    glossary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
"""
LATE_PROMPT = """
def change():
    prompt = Path("prompts/audited/review_terms.txt")
    prompt.write_text(prompt.read_text(encoding="utf-8") + "\\nLATE-LINE\\n", encoding="utf-8")
"""
THE_FIRST_ANSWER_IS_NOT_THE_CONTRACT = fixture.CAPTURE_BACKEND + """
real_backend = llm.subprocess.run
def first_answer_is_not_json(command, input=None, **kwargs):
    result = real_backend(command, input=input, **kwargs)
    if len(CALLS) == 1:
        Path(command[command.index("-o") + 1]).write_text("not json", encoding="utf-8")
    return result
llm.subprocess.run = first_answer_is_not_json
"""
PROPOSED_TERM = {"id": "test-proposed", "xuanzang": "測試用玄奘詞", "display": "測試用玄奘詞", "plain": "測試用白話詞",
                 "source_terms": ["測試用玄奘詞"], "translation_patterns": ["測試用白話詞"], "status": "proposed",
                 "rule": "test"}
A_REVIEWER_PROPOSES_A_TERM = fixture.CAPTURE_BACKEND + f"""
real_backend = llm.subprocess.run
def answer_with_a_proposed_term(command, input=None, **kwargs):
    result = real_backend(command, input=input, **kwargs)
    out = Path(command[command.index("-o") + 1])
    answer = json.loads(out.read_text(encoding="utf-8"))
    if answer["stage"] == "review_terms" and answer["unit_id"].endswith("-001"):
        answer["proposed_terms"] = [json.loads({json.dumps(json.dumps(PROPOSED_TERM, ensure_ascii=False))})]
        out.write_text(json.dumps(answer, ensure_ascii=False), encoding="utf-8")
    return result
llm.subprocess.run = answer_with_a_proposed_term
"""


class PlaceExclusiveTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.src, self.dst = Path(self._tmp.name) / "src.md", Path(self._tmp.name) / "dst.md"
        self.src.write_bytes(b"new")

    def test_it_moves_the_file_to_a_free_path(self):
        audited.place_exclusive(self.src, self.dst)

        self.assertEqual(b"new", self.dst.read_bytes())
        self.assertFalse(self.src.exists())

    def test_it_never_replaces_a_file_and_keeps_the_source(self):
        self.dst.write_bytes(b"saved by the owner")

        with self.assertRaises(FileExistsError):
            audited.place_exclusive(self.src, self.dst)

        self.assertEqual(b"saved by the owner", self.dst.read_bytes())
        self.assertEqual(b"new", self.src.read_bytes())


class PutBackTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.src, self.dst = Path(self._tmp.name) / "src.md", Path(self._tmp.name) / "dst.md"
        self.src.write_bytes(b"moved aside")

    def test_it_moves_the_file_back_to_a_free_path(self):
        audited.put_back(self.src, self.dst)

        self.assertEqual(b"moved aside", self.dst.read_bytes())
        self.assertFalse(self.src.exists())

    def test_a_file_that_is_at_the_path_stays_and_so_does_the_source(self):
        self.dst.write_bytes(b"OWNER-SAVE")

        audited.put_back(self.src, self.dst)

        self.assertEqual(b"OWNER-SAVE", self.dst.read_bytes())
        self.assertEqual(b"moved aside", self.src.read_bytes())

    def test_a_link_that_cannot_be_made_raises_and_writes_nothing(self):
        with mock.patch("os.link", side_effect=OSError(5, "Input/output error")):
            with self.assertRaises(OSError):
                audited.put_back(self.src, self.dst)

        self.assertFalse(self.dst.exists(), "no half file, no copy")
        self.assertEqual(b"moved aside", self.src.read_bytes())

    def test_failed_put_back_never_deletes_an_existing_save(self):
        self.dst.write_bytes(b"OWNER-SAVE")
        with mock.patch("os.link", side_effect=OSError("link I/O failure")):
            with mock.patch("builtins.open", side_effect=PermissionError("open failed")):
                try:
                    audited.put_back(self.src, self.dst)
                except OSError:
                    pass

        self.assertTrue(self.dst.is_file())
        self.assertEqual(b"OWNER-SAVE", self.dst.read_bytes())


class LegacyAuditPathTests(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls) -> None:
        cls._template_dir = tempfile.TemporaryDirectory()
        cls.template = Path(cls._template_dir.name) / "template"
        fixture.build_template(cls.template)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._template_dir.cleanup()

    def setUp(self) -> None:
        self._tree_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tree_dir.cleanup)
        self.tree = Path(self._tree_dir.name) / "repo"
        shutil.copytree(self.template, self.tree)
        self.md = self.tree / "translations" / fixture.md_name(7)
        self.page = self.tree / "docs" / WORK / "translations" / f"{WORK}-007-baihua.html"

    # ---------- helpers ----------

    def report(self) -> dict:
        found = sorted((self.tree / "audits").glob(f"*/{WORK}-007-audit-report.json"))
        self.assertEqual(1, len(found), found)
        return json.loads(found[0].read_text(encoding="utf-8"))

    def job_path(self) -> Path:
        found = sorted((self.tree / "jobs" / "audited").glob("*.json"))
        self.assertEqual(1, len(found), found)
        return found[0]

    def model_calls(self) -> int:
        return sum(1 for path in (self.tree / "audits").glob("*/events.jsonl")
                   for line in path.read_text(encoding="utf-8").splitlines()
                   if json.loads(line).get("type") == "llm_attempt")

    def backups(self) -> list[bytes]:
        return [path.read_bytes() for path in sorted((self.tree / "audits").glob("*/backup/*.md"))]

    def assert_exit(self, done: subprocess.CompletedProcess, code: int) -> None:
        self.assertEqual(code, done.returncode, f"stdout:\n{done.stdout}\nstderr:\n{done.stderr}")

    def assert_sealed(self, report: dict) -> None:
        self.assertEqual(("pass", "sealed"), (report["verdict"], report["install"]))
        done = fixture.run_python(self.tree, "-c", fixture.VERIFY_SNIPPET, report["attestation"])
        self.assertEqual("sealed", done.stdout.strip(), done.stderr)

    def assert_page_is_the_rendering_of_the_md(self) -> None:
        done = subprocess.run([sys.executable, "-B", "-c", fixture.RENDER_SNIPPET, f"translations/{self.md.name}"],
                              cwd=self.tree, capture_output=True, timeout=120)
        self.assertEqual(0, done.returncode, done.stderr.decode("utf-8", "replace"))
        self.assertEqual(done.stdout, self.page.read_bytes())

    # ---------- step 2: the translation is not there ----------

    def test_missing_translation_without_a_backup_fails_and_calls_no_model(self) -> None:
        self.md.unlink()
        before = fixture.digest(self.tree, "docs", "translations")

        done = fixture.audit(self.tree, "--juans", "7", "--model", "echo")

        self.assert_exit(done, 1)
        report = self.report()
        self.assertEqual(("fail", "none", 0), (report["verdict"], report["install"], report["sections_total"]))
        self.assertEqual(["translation file is missing"], report["blocking"])
        self.assertIsNone(report["attestation"])
        self.assertEqual(0, self.model_calls())
        self.assertFalse(self.md.exists())
        self.assertEqual(before, fixture.digest(self.tree, "docs", "translations"))

    def test_resume_puts_back_only_a_file_of_its_own_backup_folder(self) -> None:
        self.assert_exit(fixture.audit(self.tree, "--juans", "7", "--model", "echo-finding-stubborn"), 1)
        job_path = self.job_path()
        job = json.loads(job_path.read_text(encoding="utf-8"))
        job["progress"]["7"]["swap"] = {"backup": "works.json"}  # a path that the audit never recorded
        job_path.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
        self.md.unlink()
        calls = self.model_calls()

        done = fixture.audit(self.tree, "--resume", job_path.stem)

        self.assert_exit(done, 1)
        self.assertEqual(["translation file is missing"], self.report()["blocking"])
        self.assertFalse(self.md.exists(), "a file from outside the backup folder must not become the translation")
        self.assertTrue((self.tree / "works.json").is_file())
        self.assertEqual(calls, self.model_calls())

    # ---------- steps 5 to 7: the reviewer does not keep the contract ----------

    def test_an_answer_outside_the_contract_is_asked_again_once_and_then_fails_the_juan(self) -> None:
        before = fixture.digest(self.tree, "docs", "translations")

        done = fixture.drive(self.tree, ANSWER_IS_NOT_THE_CONTRACT, "--juans", "7")

        self.assert_exit(done, 1)
        report = self.report()
        self.assertEqual(("fail", "none", 12), (report["verdict"], report["install"], report["sections_total"]))
        self.assertEqual(1, len(report["blocking"]), report["blocking"])
        self.assertIn("review_terms: output is not one JSON object", report["blocking"][0])
        self.assertEqual(2, self.model_calls(), "one retry, and no call for the sections after the failed one")
        self.assertEqual(before, fixture.digest(self.tree, "docs", "translations"))
        self.assertTrue((self.tree / "audits" / report["job_id"] / "candidate" / self.md.name).is_file())

    # ---------- step 9, second case: an earlier attempt installed another candidate ----------

    def test_a_new_finding_after_an_install_puts_the_original_back_before_the_second_swap(self) -> None:
        original = self.md.read_bytes()
        old_sections = fixture.sections(original.decode("utf-8"))
        third_source = re.search(r"Source:\n<<<\n(.*?)\n>>>", old_sections[3], flags=re.DOTALL).group(1)
        needle = third_source.splitlines()[0]
        self.assertEqual([3], [index for index, text in enumerate(old_sections) if needle in text],
                         "the test needs a source line that only section 3 has")

        killed = fixture.drive(self.tree, fixture.KILL_AT_PAGE_BUILD, "--juans", "7", "--model", "echo-finding")
        self.assert_exit(killed, 9)
        installed_first = self.md.read_bytes()
        self.assertEqual(1, installed_first.decode("utf-8").count(MARK), "the kill came after the first swap")

        # A glossary change between the attempts: section 3 now fails the term check until the fixer marks it.
        glossary_path = self.tree / "translations" / "glossary" / f"{WORK}-terms.json"
        glossary = json.loads(glossary_path.read_text(encoding="utf-8"))
        glossary["terms"].append({
            "id": "test-mark", "xuanzang": "測試用玄奘詞", "display": "測試用玄奘詞", "plain": "測試用白話詞",
            "source_terms": [needle], "translation_patterns": [MARK], "status": "test", "rule": "test",
        })
        glossary_path.write_text(json.dumps(glossary, ensure_ascii=False, indent=2), encoding="utf-8")

        resumed = fixture.audit(self.tree, "--resume", self.job_path().stem)

        self.assert_exit(resumed, 0)
        report = self.report()
        self.assert_sealed(report)
        self.assertEqual([f"{WORK}-007-002", f"{WORK}-007-003"], report["rewritten_units"])
        new_sections = fixture.sections(self.md.read_text(encoding="utf-8"))
        self.assertEqual(13, len(new_sections))
        for index, (old, new) in enumerate(zip(old_sections, new_sections)):
            if index in (2, 3):
                self.assertEqual(1, fixture.translation_block(new).count(MARK), f"section {index} was rewritten once")
            else:
                self.assertEqual(old, new, f"section {index} was never flagged")
        backups = self.backups()
        self.assertIn(original, backups, "the translation as it was before the audit stays in the backup folder")
        self.assertIn(installed_first, backups, "the candidate of the first attempt was moved away, not overwritten")
        self.assert_page_is_the_rendering_of_the_md()

    def test_a_juan_that_fails_after_an_install_gets_its_original_translation_back(self) -> None:
        original = self.md.read_bytes()
        old_page = self.page.read_bytes()
        old_search = (self.tree / "docs" / WORK / "search.json").read_bytes()
        third_source = re.search(r"Source:\n<<<\n(.*?)\n>>>", fixture.sections(original.decode("utf-8"))[3],
                                 flags=re.DOTALL).group(1)
        killed = fixture.drive(self.tree, fixture.KILL_AT_PAGE_BUILD, "--juans", "7", "--model", "echo-finding")
        self.assert_exit(killed, 9)
        self.assertNotEqual(original, self.md.read_bytes(), "the kill came after the swap")

        # A glossary change that no fixer of this test can satisfy: section 3 keeps failing the term check.
        glossary_path = self.tree / "translations" / "glossary" / f"{WORK}-terms.json"
        glossary = json.loads(glossary_path.read_text(encoding="utf-8"))
        glossary["terms"].append({
            "id": "test-unfixable", "xuanzang": "測試用玄奘詞", "display": "測試用玄奘詞", "plain": "測試用白話詞",
            "source_terms": [third_source.splitlines()[0]], "translation_patterns": ["沒有任何譯文會有的字串"],
            "status": "test", "rule": "test",
        })
        glossary_path.write_text(json.dumps(glossary, ensure_ascii=False, indent=2), encoding="utf-8")

        resumed = fixture.audit(self.tree, "--resume", self.job_path().stem)

        self.assert_exit(resumed, 1)
        report = self.report()
        self.assertEqual(("fail", "none", []), (report["verdict"], report["install"], report["rewritten_units"]))
        self.assertTrue(any("lacks one of" in line for line in report["blocking"]), report["blocking"])
        self.assertEqual(original, self.md.read_bytes(), "a juan that does not pass keeps its translation as it was")
        self.assertEqual(old_page, self.page.read_bytes())
        self.assertEqual(old_search, (self.tree / "docs" / WORK / "search.json").read_bytes())
        self.assertEqual([], sorted((self.tree / "audits" / report["job_id"]).glob("*attestation*")))

    # ---------- step 12: somebody saved the translation after the install ----------

    def test_a_save_after_the_install_is_not_sealed_and_stays_at_its_path(self) -> None:
        original = self.md.read_bytes()
        old_page = self.page.read_bytes()
        before_docs = fixture.digest(self.tree, "docs")

        done = fixture.drive(self.tree, OWNER_SAVES_AFTER_THE_INSTALL, "--juans", "7", "--model", "echo-finding")

        self.assert_exit(done, 1)
        saved = (self.tree / "owner-saved.md").read_bytes()
        self.assertIn(MARK.encode("utf-8"), saved, "the owner saved on top of the installed candidate")
        self.assertEqual(saved, self.md.read_bytes(), "the file that the owner saved is at the path again")
        report = self.report()
        self.assertEqual(("fail", "none"), (report["verdict"], report["install"]))
        self.assertTrue(any("changed during the audit" in line for line in report["blocking"]), report["blocking"])
        self.assertIsNone(report["attestation"])
        self.assertEqual([], sorted((self.tree / "audits" / report["job_id"]).glob("*attestation*")),
                         "text that nobody reviewed is never sealed")
        self.assertEqual(old_page, self.page.read_bytes())
        self.assertEqual(before_docs, fixture.digest(self.tree, "docs"), "the page and the search index are back")
        self.assertIn(original, self.backups(), "the translation as it was before the audit is kept")

    # ---------- what the command refuses ----------

    def test_juans_outside_the_work_are_refused_before_anything_is_written(self) -> None:
        for spec in ("101", "0", "99-101"):
            with self.subTest(spec=spec):
                done = fixture.audit(self.tree, "--juans", spec, "--model", "echo")
                self.assertNotEqual(0, done.returncode, done.stdout)
                self.assertIn("invalid audit job: juans outside 1-100", done.stdout + done.stderr)
        self.assertFalse((self.tree / "audits").exists())
        self.assertFalse((self.tree / "jobs").exists())

    def test_resume_rejects_a_job_whose_juans_were_moved_outside_the_work(self) -> None:
        self.assert_exit(fixture.audit(self.tree, "--juans", "7", "--model", "echo"), 0)
        job_path = self.job_path()
        job = json.loads(job_path.read_text(encoding="utf-8"))
        calls = self.model_calls()
        for juans in ([101], [7, 7], [True]):
            with self.subTest(juans=juans):
                job_path.write_text(json.dumps({**job, "juans": juans}, ensure_ascii=False), encoding="utf-8")
                done = fixture.audit(self.tree, "--resume", job_path.stem)
                self.assertNotEqual(0, done.returncode, done.stdout)
                self.assertIn("invalid audit job", done.stdout + done.stderr)
        self.assertEqual(calls, self.model_calls())

    def test_resume_cannot_change_the_model_of_a_job(self) -> None:
        self.assert_exit(fixture.audit(self.tree, "--juans", "7", "--model", "echo-finding-stubborn"), 1)
        calls = self.model_calls()

        done = fixture.audit(self.tree, "--resume", self.job_path().stem, "--model", "echo")

        self.assertNotEqual(0, done.returncode, done.stdout)
        self.assertIn("a resume cannot change it", done.stdout + done.stderr)
        self.assertEqual(calls, self.model_calls())

    # ---------- a swap or a rebuild that is cut short (fix round 1 of the diff review) ----------

    def events(self) -> list[dict]:
        return [json.loads(line) for path in sorted((self.tree / "audits").glob("*/events.jsonl"))
                for line in path.read_text(encoding="utf-8").splitlines()]

    def artifact_text(self, reference: dict) -> str:
        return gzip.decompress((self.tree / reference["path"]).read_bytes()).decode("utf-8")

    def test_an_io_error_during_the_swap_puts_the_translation_back(self) -> None:
        original = self.md.read_bytes()
        before = fixture.digest(self.tree, "docs", "translations")

        done = fixture.drive(self.tree, THE_CANDIDATE_CANNOT_BE_LINKED, "--juans", "7", "--model", "echo-finding")

        self.assert_exit(done, 1)
        self.assertTrue(self.md.is_file(), "an error in the middle of the swap must not leave the path empty")
        self.assertEqual(original, self.md.read_bytes())
        self.assertEqual(before, fixture.digest(self.tree, "docs", "translations"))
        report = self.report()
        self.assertEqual(("fail", "none"), (report["verdict"], report["install"]))
        self.assertTrue(any("simulated candidate link failure" in line for line in report["blocking"]),
                        report["blocking"])

    def test_a_filesystem_without_hard_links_fails_the_install_before_the_translation_moves(self) -> None:
        original = self.md.read_bytes()
        inode = self.md.stat().st_ino
        before = fixture.digest(self.tree, "docs", "translations")

        done = fixture.drive(self.tree, NO_HARD_LINKS_AT_ALL, "--juans", "7", "--model", "echo-finding")

        self.assert_exit(done, 1)
        self.assertEqual(original, self.md.read_bytes())
        self.assertEqual(inode, self.md.stat().st_ino, "the translation never left its path")
        self.assertEqual(before, fixture.digest(self.tree, "docs", "translations"))
        self.assertEqual([], self.backups())
        report = self.report()
        self.assertEqual(("fail", "none"), (report["verdict"], report["install"]))
        self.assertEqual(["the translation was not installed: no hard link can be made here (Operation not permitted)"],
                         report["blocking"])

    def backup_names(self) -> list[str]:
        return sorted(path.name for path in (self.tree / "audits").glob("*/backup/*"))

    def candidate_names(self) -> list[str]:
        return sorted(path.name for path in (self.tree / "audits").glob("*/candidate/*"))

    def test_a_kill_at_the_link_probe_does_not_stop_the_resume(self) -> None:
        original = self.md.read_bytes()
        killed = fixture.drive(self.tree, KILL_WHILE_THE_LINK_PROBE_EXISTS, "--juans", "7", "--model", "echo-finding")
        self.assert_exit(killed, 9)
        self.assertEqual(original, self.md.read_bytes(), "the probe comes before the translation moves")
        self.assertEqual([self.md.name, f"{self.md.name}.link"], self.candidate_names())

        resumed = fixture.audit(self.tree, "--resume", self.job_path().stem)

        self.assert_exit(resumed, 0)
        self.assert_sealed(self.report())
        self.assertEqual([self.md.name], self.candidate_names(), "the probe of the killed attempt is gone")

    def test_a_swap_leaves_no_helper_file_in_the_job_folder(self) -> None:
        done = fixture.audit(self.tree, "--juans", "7", "--model", "echo-finding")

        self.assert_exit(done, 0)
        self.assertEqual([f"1-{self.md.name}", "1-site"], self.backup_names())
        self.assertEqual([self.md.name], self.candidate_names())

    def test_a_refused_swap_leaves_nothing_in_the_backup_folder(self) -> None:
        done = fixture.drive(self.tree, fixture.OWNER_EDITS_AFTER_GATE, "--juans", "7", "--model", "echo-finding")

        self.assert_exit(done, 1)
        self.assertEqual(["the translation changed during the audit"], self.report()["blocking"])
        self.assertEqual([], self.backup_names())

    def test_a_save_of_the_owner_while_the_install_is_undone_stays_at_the_path(self) -> None:
        original = self.md.read_bytes()

        done = fixture.drive(self.tree, fixture.FAIL_AT_SEAL + OWNER_SAVES_WHILE_THE_INSTALL_IS_UNDONE,
                             "--juans", "7", "--model", "echo-finding")

        self.assert_exit(done, 1)
        self.assertEqual(b"OWNER-SAVE-DURING-THE-RESTORE", self.md.read_bytes())
        report = self.report()
        self.assertEqual(("fail", "none"), (report["verdict"], report["install"]))
        self.assertEqual(["the install was undone: seal failed on purpose"], report["blocking"])
        self.assertIn(original, self.backups(), "the original is kept; the owner's file was not replaced")

    def test_a_translation_that_cannot_go_back_waits_in_the_backup_folder_and_a_resume_puts_it_back(self) -> None:
        original = self.md.read_bytes()

        done = fixture.drive(self.tree, NO_HARD_LINK_ONTO_THE_TRANSLATION, "--juans", "7", "--model", "echo-finding")

        self.assert_exit(done, 1)
        self.assertFalse(self.md.exists(), "no copy and no half file is put at the path")
        self.assertEqual([original], self.backups())
        report = self.report()
        self.assertEqual(("fail", "none"), (report["verdict"], report["install"]))
        self.assertEqual([f"the translation is in audits/{report['job_id']}/backup/1-{self.md.name}: "
                          "it could not go back to its path (Cross-device link)"], report["blocking"])
        self.assertIn("swap", json.loads(self.job_path().read_text(encoding="utf-8"))["progress"]["7"])
        (self.tree / "data" / f"{WORK}-007.json").unlink()  # the resume cannot pass

        resumed = fixture.audit(self.tree, "--resume", self.job_path().stem)

        self.assert_exit(resumed, 1)
        self.assertEqual(original, self.md.read_bytes(), "the resume put the original back and adopted nothing else")
        self.assertEqual([], self.backups())

    def test_a_save_of_the_owner_that_cannot_go_back_is_put_back_by_the_resume(self) -> None:
        saved = self.md.read_bytes() + b"\nOWNER-EDIT\n"

        done = fixture.drive(self.tree, fixture.OWNER_EDITS_AFTER_GATE + NO_HARD_LINK_ONTO_THE_TRANSLATION,
                             "--juans", "7", "--model", "echo-finding")

        self.assert_exit(done, 1)
        self.assertFalse(self.md.exists())
        self.assertEqual([saved], self.backups(), "the file that the owner saved is kept as it is")
        self.assertTrue(any("could not go back to its path" in line for line in self.report()["blocking"]))

        resumed = fixture.audit(self.tree, "--resume", self.job_path().stem)

        self.assert_exit(resumed, 0)  # the file is back, and the audit started again from it
        self.assert_sealed(self.report())
        self.assertIn(b"OWNER-EDIT", self.md.read_bytes())
        self.assertIn(saved, self.backups(), "what the owner saved is the original of the new audit")

    def test_a_candidate_that_was_placed_before_an_error_is_taken_out_again(self) -> None:
        original = self.md.read_bytes()
        before = fixture.digest(self.tree, "docs", "translations")

        done = fixture.drive(self.tree, THE_STAGED_CANDIDATE_CANNOT_BE_REMOVED, "--juans", "7",
                             "--model", "echo-finding")

        self.assert_exit(done, 1)
        self.assertEqual(original, self.md.read_bytes(), "the candidate reached the path, and the juan failed")
        self.assertEqual(before, fixture.digest(self.tree, "docs", "translations"))
        report = self.report()
        self.assertEqual(("fail", "none"), (report["verdict"], report["install"]))
        self.assertTrue(any("Input/output error" in line for line in report["blocking"]), report["blocking"])

    def test_an_original_that_cannot_go_back_leaves_the_candidate_at_the_path_and_the_report_says_so(self) -> None:
        original = self.md.read_bytes()

        done = fixture.drive(self.tree, fixture.FAIL_AT_SEAL + THE_ORIGINAL_CANNOT_BE_LINKED, "--juans", "7",
                             "--model", "echo-finding")

        self.assert_exit(done, 1)
        self.assertTrue(self.md.is_file(), "the file that was moved aside goes back: the path is not left empty")
        self.assertIn(MARK.encode("utf-8"), self.md.read_bytes())
        report = self.report()
        self.assertEqual(("fail", "installed"), (report["verdict"], report["install"]))
        self.assertTrue(any("the translation could not be put back" in line for line in report["blocking"]),
                        report["blocking"])
        self.assertIn(original, self.backups())

        resumed = fixture.audit(self.tree, "--resume", self.job_path().stem)

        self.assert_exit(resumed, 0)
        self.assert_sealed(self.report())
        self.assertIn(original, self.backups())
        self.assert_page_is_the_rendering_of_the_md()

    def test_when_nothing_can_go_back_the_job_keeps_what_it_needs_and_a_resume_finishes(self) -> None:
        original = self.md.read_bytes()

        done = fixture.drive(self.tree, fixture.FAIL_AT_SEAL + NOTHING_GOES_BACK, "--juans", "7",
                             "--model", "echo-finding")

        self.assert_exit(done, 1)
        report = self.report()
        self.assertEqual(("fail", "installed"), (report["verdict"], report["install"]),
                         "the job must not claim that the original is back")
        self.assertTrue(any("it could not go back to its path (Input/output error)" in line
                            for line in report["blocking"]), report["blocking"])
        self.assertFalse(self.md.exists(), "the test needs the worst case: the path stayed empty")
        prog = json.loads(self.job_path().read_text(encoding="utf-8"))["progress"]["7"]
        self.assertIn("swap", prog, "the file that was moved aside is still recorded")
        self.assertIn("original_backup", prog, "and so is the original")
        self.assertIn(original, self.backups())

        resumed = fixture.audit(self.tree, "--resume", self.job_path().stem)

        self.assert_exit(resumed, 0)
        self.assert_sealed(self.report())
        self.assertIn(MARK.encode("utf-8"), self.md.read_bytes())
        self.assertIn(original, self.backups())
        self.assert_page_is_the_rendering_of_the_md()

    def test_a_kill_between_the_install_and_its_record_is_undone_when_the_juan_fails(self) -> None:
        original = self.md.read_bytes()
        before = fixture.digest(self.tree, "docs", "translations")
        killed = fixture.drive(self.tree, KILL_BEFORE_THE_INSTALL_IS_RECORDED, "--juans", "7",
                               "--model", "echo-finding")
        self.assert_exit(killed, 9)
        self.assertIn(MARK.encode("utf-8"), self.md.read_bytes(), "the kill came after the candidate was placed")
        (self.tree / "data" / f"{WORK}-007.json").unlink()  # the resume cannot pass

        resumed = fixture.audit(self.tree, "--resume", self.job_path().stem)

        self.assert_exit(resumed, 1)
        report = self.report()
        self.assertEqual(("fail", "none"), (report["verdict"], report["install"]))
        self.assertEqual(original, self.md.read_bytes(), "a juan that does not pass keeps its translation as it was")
        self.assertEqual(before, fixture.digest(self.tree, "docs", "translations"))

    def test_a_failed_resume_after_the_site_was_rebuilt_puts_the_site_back(self) -> None:
        before = fixture.digest(self.tree, "docs", "translations")
        killed = fixture.drive(self.tree, KILL_AT_THE_SEAL, "--juans", "7", "--model", "echo-finding")
        self.assert_exit(killed, 9)
        self.assertIn(MARK, self.page.read_text(encoding="utf-8"), "the kill came after the page was rebuilt")
        self.assertIn(MARK, (self.tree / "docs" / WORK / "search.json").read_text(encoding="utf-8"))
        (self.tree / "data" / f"{WORK}-007.json").unlink()  # the resume cannot pass

        resumed = fixture.audit(self.tree, "--resume", self.job_path().stem)

        self.assert_exit(resumed, 1)
        report = self.report()
        self.assertEqual(("fail", "none"), (report["verdict"], report["install"]))
        self.assertEqual(before, fixture.digest(self.tree, "docs", "translations"),
                         "the translation, its page and the search indexes are what they were before the audit")

    def test_a_resume_that_fails_at_the_seal_puts_back_the_site_of_before_the_first_attempt(self) -> None:
        before = fixture.digest(self.tree, "docs", "translations")
        killed = fixture.drive(self.tree, KILL_AT_THE_SEAL, "--juans", "7", "--model", "echo-finding")
        self.assert_exit(killed, 9)

        resumed = fixture.drive(self.tree, fixture.FAIL_AT_SEAL, "--resume", self.job_path().stem)

        self.assert_exit(resumed, 1)
        report = self.report()
        self.assertEqual(("fail", "none"), (report["verdict"], report["install"]))
        self.assertTrue(any("the install was undone" in line for line in report["blocking"]), report["blocking"])
        self.assertEqual(before, fixture.digest(self.tree, "docs", "translations"),
                         "not the site that the killed attempt had rebuilt")

    def test_a_search_index_that_another_job_rebuilt_is_built_again_and_not_overwritten(self) -> None:
        original = self.md.read_bytes()
        old_page = self.page.read_bytes()
        search = self.tree / "docs" / WORK / "search.json"
        killed = fixture.drive(self.tree, KILL_AT_THE_SEAL, "--juans", "7", "--model", "echo-finding")
        self.assert_exit(killed, 9)
        # Before the resume, another job publishes a change of juan 76 and rebuilds the search indexes.
        other = self.tree / "translations" / fixture.md_name(76)
        text = other.read_text(encoding="utf-8")
        published = re.sub(r"(Translation:\n<<<\n.*?)(\n>>>)", r"\1OTHER-JOB\2", text, count=1, flags=re.DOTALL)
        self.assertNotEqual(text, published)
        other.write_text(published, encoding="utf-8")
        self.assert_exit(fixture.run_python(self.tree, "scripts/build_search_index.py"), 0)
        self.assertIn("OTHER-JOB", search.read_text(encoding="utf-8"))
        (self.tree / "data" / f"{WORK}-007.json").unlink()  # the resume cannot pass

        resumed = fixture.audit(self.tree, "--resume", self.job_path().stem)

        self.assert_exit(resumed, 1)
        self.assertEqual(original, self.md.read_bytes())
        self.assertEqual(old_page, self.page.read_bytes())
        index = search.read_text(encoding="utf-8")
        self.assertIn("OTHER-JOB", index, "the snapshot of the audit is older than what the other job published")
        self.assertNotIn(MARK, index, "the candidate that was taken out again is not in the index")

    def test_a_juan_that_fails_after_the_owner_saved_over_a_killed_install_gets_its_old_page_back(self) -> None:
        old_page = self.page.read_bytes()
        search = self.tree / "docs" / WORK / "search.json"
        killed = fixture.drive(self.tree, KILL_AT_THE_SEAL, "--juans", "7", "--model", "echo-finding")
        self.assert_exit(killed, 9)
        self.assertNotEqual(old_page, self.page.read_bytes(), "the kill came after the page was rebuilt")
        # The owner saves a text of their own at the path. The audit starts again from that file.
        saved = re.sub(r"(Translation:\n<<<\n.*?)(\n>>>)", r"\1OWNER-TEXT\2",
                       self.md.read_text(encoding="utf-8").replace(MARK, ""), count=1, flags=re.DOTALL)
        self.md.write_text(saved, encoding="utf-8")
        (self.tree / "data" / f"{WORK}-007.json").unlink()  # the resume cannot pass

        resumed = fixture.audit(self.tree, "--resume", self.job_path().stem)

        self.assert_exit(resumed, 1)
        self.assertEqual(saved, self.md.read_text(encoding="utf-8"), "the file that the owner saved stays")
        self.assertEqual(old_page, self.page.read_bytes(), "the page of the killed attempt is not left online")
        self.assertNotIn(MARK, search.read_text(encoding="utf-8"))

    def test_a_resume_that_cannot_take_the_candidate_out_keeps_it_out_of_the_site(self) -> None:
        old_page = self.page.read_bytes()
        search = self.tree / "docs" / WORK / "search.json"
        old_search = search.read_bytes()
        killed = fixture.drive(self.tree, KILL_AT_THE_SEAL, "--juans", "7", "--model", "echo-finding")
        self.assert_exit(killed, 9)
        self.assertIn(MARK, search.read_text(encoding="utf-8"), "the kill came after the indexes were rebuilt")

        resumed = fixture.drive(self.tree, fixture.FAIL_AT_SEAL + THE_ORIGINAL_CANNOT_BE_LINKED,
                                "--resume", self.job_path().stem)

        self.assert_exit(resumed, 1)
        report = self.report()
        self.assertEqual(("fail", "installed"), (report["verdict"], report["install"]))
        self.assertIn(MARK.encode("utf-8"), self.md.read_bytes(), "the candidate could not be taken out")
        self.assertEqual(old_page, self.page.read_bytes())
        self.assertEqual(old_search, search.read_bytes(), "no index is built from a candidate that did not pass")

    # ---------- the gate stores the inputs that the reviews answered ----------

    def test_the_gate_stores_the_policy_that_every_review_answered(self) -> None:
        done = fixture.drive(self.tree, THE_POLICY_CHANGES_AFTER_THE_FIRST_ANSWER, "--juans", "7")

        self.assert_exit(done, 0)
        self.assert_sealed(self.report())
        gate = self.assert_every_review_answered_the_policy_of_the_gate()
        self.assertIn("- New policy.", self.artifact_text(gate["artifacts"]["policy"]))
        first = [event for event in self.events() if event["type"] == "llm_attempt"][0]
        self.assertNotEqual(gate["artifacts"]["policy"]["sha256"], first["hashes"]["policy_sha256"],
                            "the first request was made with the old policy, and its event says so")
        self.assertEqual(25, self.model_calls(), "only the unit that had answered the old policy was asked again")

    def assert_every_review_answered_the_policy_of_the_gate(self) -> dict:
        """Every attested review of juan 7 holds the policy that the gate stored. Returns the gate event."""
        events = {event["event_id"]: event for event in self.events()}
        gate = [event for event in events.values() if event["type"] == "quality_gate"][-1]
        policy = self.artifact_text(gate["artifacts"]["policy"])
        job = json.loads(self.job_path().read_text(encoding="utf-8"))
        attestations = job["progress"]["7"]["review_attestations"]
        self.assertEqual(12, len(attestations))
        for unit, passes in attestations.items():
            for stage in ("review_terms", "review_doctrine"):
                review = events[passes[stage]["review_event_id"]]
                request = self.artifact_text(review["artifacts"]["request"])
                self.assertTrue(request.startswith(policy.strip() + "\n\n---\n\n"), f"{unit} {stage}")
                self.assertEqual(gate["artifacts"]["policy"]["sha256"], review["hashes"]["policy_sha256"])
        return gate

    def assert_a_late_change_is_not_part_of_the_audit(self, change: str, artifact: str, needle: str) -> None:
        done = fixture.drive(self.tree, a_late_change(change), "--juans", "7", "--model", "echo")

        self.assert_exit(done, 0)
        self.assert_sealed(self.report())
        self.assertEqual(24, self.model_calls(), "no unit was asked again with the file that changed")
        gate = self.assert_every_review_answered_the_policy_of_the_gate()
        self.assertNotIn(needle, self.artifact_text(gate["artifacts"][artifact]),
                         "the gate stores the inputs that the audit pinned for its last check")

    def test_a_policy_that_changes_in_the_last_check_is_not_part_of_this_audit(self) -> None:
        self.assert_a_late_change_is_not_part_of_the_audit(LATE_POLICY, "policy", "- Late policy.")

        policy = self.tree / "prompts" / "audited" / "common_policy.txt"
        self.assertIn("- Late policy.", policy.read_text(encoding="utf-8"), "the test did change the file")

    def test_a_policy_that_changes_and_is_put_back_in_the_last_check_reaches_no_review(self) -> None:
        self.assert_a_late_change_is_not_part_of_the_audit(LATE_POLICY_AND_BACK, "policy", "- Late policy.")

    def test_a_unit_that_is_asked_again_in_the_last_check_answers_the_pinned_policy(self) -> None:
        # The first review answered the policy of the start. The policy then changes once during round 1
        # (the last check pins that version and asks the first unit again) and once more after the pin.
        done = fixture.drive(self.tree, THE_POLICY_CHANGES_AFTER_THE_FIRST_ANSWER + a_late_change(LATE_POLICY),
                             "--juans", "7")

        self.assert_exit(done, 0)
        self.assert_sealed(self.report())
        self.assertEqual(25, self.model_calls())
        gate = self.assert_every_review_answered_the_policy_of_the_gate()
        policy = self.artifact_text(gate["artifacts"]["policy"])
        self.assertIn("- New policy.", policy)
        self.assertNotIn("- Late policy.", policy)

    def test_a_glossary_that_changes_in_the_last_check_is_not_part_of_this_audit(self) -> None:
        self.assert_a_late_change_is_not_part_of_the_audit(LATE_GLOSSARY, "glossary", "test-late")

    def test_a_prompt_file_that_changes_in_the_last_check_is_not_part_of_this_audit(self) -> None:
        self.assert_a_late_change_is_not_part_of_the_audit(LATE_PROMPT, "prompt:review_terms.txt", "LATE-LINE")

    def test_a_review_that_passed_on_its_retry_is_not_asked_again(self) -> None:
        killed = fixture.drive(self.tree, THE_FIRST_ANSWER_IS_NOT_THE_CONTRACT + fixture.KILL_AT_PAGE_BUILD,
                               "--juans", "7")
        self.assert_exit(killed, 9)
        self.assertEqual(25, self.model_calls(), "12 sections with 2 reviews each, and the one retry")
        retried = [event for event in self.events() if event["type"] == "llm_attempt"][1]
        self.assertEqual(2, retried["semantic_attempt"])
        self.assertNotEqual(retried["hashes"]["prompt_sha256"], retried["hashes"]["bound_prompt_sha256"])

        resumed = fixture.drive(self.tree, fixture.CAPTURE_BACKEND, "--resume", self.job_path().stem)

        self.assert_exit(resumed, 0)
        self.assert_sealed(self.report())
        self.assertEqual(25, self.model_calls(), "the review that passed on its retry stands for the resume too")

    # ---------- what a review may not change ----------

    def test_a_proposed_term_is_reported_and_the_glossary_stays_as_it_is(self) -> None:
        glossary = self.tree / "translations" / "glossary" / f"{WORK}-terms.json"
        before = glossary.read_bytes()

        done = fixture.drive(self.tree, A_REVIEWER_PROPOSES_A_TERM, "--juans", "7")

        self.assert_exit(done, 0)
        report = self.report()
        self.assert_sealed(report)
        self.assertEqual([PROPOSED_TERM], report["proposed_terms"])
        self.assertEqual(before, glossary.read_bytes(), "only the owner adds a term to the glossary")

    def test_an_unresolved_finding_is_named_in_the_blocking_lines(self) -> None:
        done = fixture.audit(self.tree, "--juans", "7", "--model", "echo-finding-stubborn")

        self.assert_exit(done, 1)
        self.assertEqual([f"{WORK}-007-002 review_doctrine: echo-finding"], self.report()["blocking"])
        self.assertEqual(27, self.model_calls(),
                         "24 reviews, 1 fix, 2 reviews of the rewritten unit: the last check does not ask it again")


if __name__ == "__main__":
    unittest.main()
