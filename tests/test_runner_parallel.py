import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import runner  # noqa: E402


class RunnerParallelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.jobs = base / "jobs"
        self.archived_jobs = self.jobs / "archive"
        self.locks = base / "locks"
        self.status = base / "docs" / "status.json"
        self.jobs.mkdir()
        self.locks.mkdir()
        self.status.parent.mkdir()
        self.patches = [
            mock.patch.object(runner, "JOBS_DIR", self.jobs),
            mock.patch.object(runner, "ARCHIVED_JOBS_DIR", self.archived_jobs),
            mock.patch.object(runner, "LOCKS_DIR", self.locks),
            mock.patch.object(runner, "STATUS_JSON", self.status),
        ]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in reversed(self.patches):
            patch.stop()
        self.tmp.cleanup()

    def job(self, juans, parallel=2):
        return {
            "id": "test-job",
            "work": "T0001",
            "juans": juans,
            "model": "dual-echo",
            "state": "queued",
            "created": runner.now_iso(),
            "updated": runner.now_iso(),
            "pid": None,
            "resume_at": None,
            "error": None,
            "push": False,
            "summary": False,
            "parallel_juans": parallel,
            "progress": {},
        }

    def test_parse_parallel_juans_validation(self):
        self.assertEqual(runner.parse_parallel_juans("1"), 1)
        self.assertEqual(runner.parse_parallel_juans(5), 5)
        with self.assertRaises(ValueError):
            runner.parse_parallel_juans(0)
        with self.assertRaises(ValueError):
            runner.parse_parallel_juans(6)
        with self.assertRaises(ValueError):
            runner.parse_parallel_juans("nope")

    def test_run_job_limits_concurrent_juans(self):
        job = self.job([1, 2, 3, 4], parallel=2)
        lock = threading.Lock()
        active = 0
        max_active = 0
        started = []

        def fake_run_juan(job_arg, juan):
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
                started.append(juan)
            time.sleep(0.05)
            job_arg["progress"].setdefault(str(juan), {})["step"] = "done"
            with lock:
                active -= 1

        with mock.patch.object(runner, "run_juan", side_effect=fake_run_juan):
            runner.run_job(job)

        self.assertEqual(sorted(started), [1, 2, 3, 4])
        self.assertLessEqual(max_active, 2)
        self.assertEqual(job["state"], "done")

    def test_run_job_skips_done_and_cancelled_juans(self):
        job = self.job([1, 2, 3], parallel=2)
        job["progress"] = {
            "1": {"step": "done"},
            "2": {"cancelled": True, "step": "cancelled"},
        }
        started = []

        def fake_run_juan(job_arg, juan):
            started.append(juan)
            job_arg["progress"].setdefault(str(juan), {})["step"] = "done"

        with mock.patch.object(runner, "run_juan", side_effect=fake_run_juan):
            runner.run_job(job)

        self.assertEqual(started, [3])
        self.assertEqual(job["state"], "done")

    def test_stage_model_uses_per_volume_models(self):
        job = self.job([1, 2])
        prog_a = {"models": {"merge": "claude"}}
        prog_b = {"models": {"merge": "codex"}}

        self.assertEqual(runner.stage_model(job, "merge", prog_a), "claude")
        self.assertEqual(runner.stage_model(job, "merge", prog_b), "codex")

    def test_held_job_sets_waiting_model_after_other_juans_finish(self):
        job = self.job([1, 2], parallel=2)

        def fake_run_juan(job_arg, juan):
            prog = job_arg["progress"].setdefault(str(juan), {})
            if juan == 1:
                prog["resume_at"] = "2099-01-01T00:00:00+00:00"
                raise runner.JobHold("held")
            prog["step"] = "done"

        with mock.patch.object(runner, "run_juan", side_effect=fake_run_juan):
            runner.run_job(job)

        self.assertEqual(job["state"], "waiting_model")
        self.assertEqual(job["resume_at"], "2099-01-01T00:00:00+00:00")

    def test_failed_juan_takes_precedence_over_held_juan(self):
        job = self.job([1, 2], parallel=2)

        def fake_run_juan(job_arg, juan):
            prog = job_arg["progress"].setdefault(str(juan), {})
            if juan == 1:
                prog["resume_at"] = "2099-01-01T00:00:00+00:00"
                raise runner.JobHold("held")
            raise RuntimeError("boom")

        with mock.patch.object(runner, "run_juan", side_effect=fake_run_juan):
            runner.run_job(job)

        self.assertEqual(job["state"], "failed")
        self.assertEqual(job["error"], "juans failed: [2]")

    def test_waiting_job_owned_by_current_runner_is_claimable_after_run_returns(self):
        job = self.job([1], parallel=2)
        job["pid"] = runner.os.getpid()
        job["state"] = "waiting_model"

        with mock.patch.dict(runner.ACTIVE_JOB_PARALLEL, {}, clear=True):
            self.assertTrue(runner.claimable(job, "dual-echo"))

        with mock.patch.dict(runner.ACTIVE_JOB_PARALLEL, {job["id"]: 2}, clear=True):
            self.assertFalse(runner.claimable(job, "dual-echo"))

    def test_force_start_juan_preserves_review_section_and_requeues(self):
        job = self.job([1], parallel=2)
        job["state"] = "waiting_model"
        job["resume_at"] = "2099-01-01T00:00:00+00:00"
        job["error"] = "juans waiting: [1]"
        job["progress"] = {
            "1": {
                "step": "waiting_limit",
                "resume_at": "2099-01-01T00:00:00+00:00",
                "error": "limit",
                "tasks": {
                    "review": {
                        "state": "waiting",
                        "section": 7,
                        "sections_total": 15,
                        "resume_at": "2099-01-01T00:00:00+00:00",
                        "error": "limit",
                    },
                    "draft_codex": {"state": "done", "section": 15, "sections_total": 15},
                },
            },
        }
        runner.save_job(job)

        ok, message = runner.force_start_juan(job["id"], 1)
        saved = runner.load_job(self.jobs / f"{job['id']}.json")
        review = saved["progress"]["1"]["tasks"]["review"]

        self.assertTrue(ok, message)
        self.assertEqual(saved["state"], "queued")
        self.assertIsNone(saved["resume_at"])
        self.assertIsNone(saved["error"])
        self.assertEqual(saved["progress"]["1"]["step"], "merge")
        self.assertEqual(review["state"], "pending")
        self.assertEqual(review["section"], 7)
        self.assertEqual(review["sections_total"], 15)
        self.assertNotIn("resume_at", review)
        self.assertNotIn("error", review)

    def test_force_start_juan_rejects_done_volume(self):
        job = self.job([1], parallel=2)
        job["progress"] = {"1": {"step": "done"}}
        runner.save_job(job)

        ok, message = runner.force_start_juan(job["id"], 1)

        self.assertFalse(ok)
        self.assertEqual(message, "此卷已完成")

    def test_final_failure_marks_running_task_failed(self):
        job = self.job([1], parallel=2)
        job["progress"] = {
            "1": {
                "step": "checks",
                "tasks": {"checks": {"state": "running"}},
            },
        }

        with mock.patch.object(runner, "run_juan", side_effect=RuntimeError("bad range")):
            result = runner.run_juan_with_retries(job, 1)

        checks = job["progress"]["1"]["tasks"]["checks"]
        self.assertEqual(result["state"], "failed")
        self.assertEqual(job["progress"]["1"]["error"], "bad range")
        self.assertEqual(checks["state"], "failed")
        self.assertEqual(checks["error"], "bad range")

    def test_mark_juan_done_clears_stale_error_state(self):
        prog = {
            "step": "checks",
            "error": "old error",
            "resume_at": "2099-01-01T00:00:00+00:00",
            "tasks": {
                "checks": {
                    "state": "done",
                    "error": "old check error",
                    "resume_at": "2099-01-01T00:00:00+00:00",
                },
                "draft_codex": {
                    "state": "done",
                    "reason": "kept for history",
                },
            },
        }

        runner.mark_juan_done(prog)

        self.assertEqual(prog["step"], "done")
        self.assertNotIn("error", prog)
        self.assertNotIn("resume_at", prog)
        self.assertNotIn("error", prog["tasks"]["checks"])
        self.assertNotIn("resume_at", prog["tasks"]["checks"])
        self.assertEqual(prog["tasks"]["draft_codex"]["reason"], "kept for history")

    def test_draft_section_batches_keep_sections_whole(self):
        entries = [
            (0, runner.bth.Entry("s1", "r1", "a" * 1500, "", "")),
            (1, runner.bth.Entry("s2", "r2", "b" * 1500, "", "")),
            (2, runner.bth.Entry("s3", "r3", "c" * 1500, "", "")),
        ]

        batches = runner.draft_section_batches(entries)

        self.assertEqual([[idx for idx, _entry in batch] for batch in batches], [[0, 1], [2]])

    def test_draft_section_batches_allow_single_oversized_section(self):
        entries = [
            (0, runner.bth.Entry("large", "r1", "a" * 5000, "", "")),
            (1, runner.bth.Entry("small", "r2", "b" * 100, "", "")),
        ]

        batches = runner.draft_section_batches(entries)

        self.assertEqual([[idx for idx, _entry in batch] for batch in batches], [[0], [1]])

    def test_translate_sections_batched_splices_batch_output(self):
        md_path = Path(self.tmp.name) / "draft.md"
        md_path.write_text(
            "# Test\n\n"
            "## s1\nRange: r1\n\nSource:\n<<<\n" + ("a" * 1500) + "\n>>>\n\nTranslation:\n<<<\n\n>>>\n\nNote:\n<<<\n\n>>>\n\n"
            "## s2\nRange: r2\n\nSource:\n<<<\n" + ("b" * 1500) + "\n>>>\n\nTranslation:\n<<<\n\n>>>\n\nNote:\n<<<\n\n>>>\n\n"
            "## s3\nRange: r3\n\nSource:\n<<<\n" + ("c" * 1500) + "\n>>>\n\nTranslation:\n<<<\n\n>>>\n\nNote:\n<<<\n\n>>>\n",
            encoding="utf-8",
        )
        job = self.job([1])
        prog = {}
        prompts = []

        def fake_llm_call(_job, prompt, _stage, _juan):
            prompts.append(prompt)
            if "<<<SOURCE_SECTION 1>>>" in prompt:
                return (
                    "<<<SECTION 1>>>\n<<<TRANSLATION\n譯一\n>>>\n<<<NOTE\n註一\n>>>\n<<<END_SECTION 1>>>\n"
                    "<<<SECTION 2>>>\n<<<TRANSLATION\n譯二\n>>>\n<<<NOTE\n\n>>>\n<<<END_SECTION 2>>>"
                )
            return "<<<TRANSLATION\n譯三\n>>>\n<<<NOTE\n註三\n>>>"

        with mock.patch.object(runner, "llm_call", side_effect=fake_llm_call):
            runner.translate_sections_batched(job, 1, md_path, prog, stage="draft_codex")

        entries = runner.bth.parse_entries(md_path.read_text(encoding="utf-8"))
        self.assertEqual([entry.translation for entry in entries], ["譯一", "譯二", "譯三"])
        self.assertEqual([entry.note for entry in entries], ["註一", "", "註三"])
        self.assertEqual(prog["section"], 3)
        self.assertEqual(len(prompts), 2)
        self.assertIn("<<<SOURCE_SECTION 1>>>", prompts[0])
        self.assertIn("【原文】", prompts[1])

    def test_archive_job_moves_completed_job_out_of_active_queue(self):
        job = self.job([1], parallel=2)
        job["state"] = "done"
        job["progress"] = {"1": {"step": "done"}}
        runner.save_job(job)

        ok, message = runner.archive_job(job["id"])

        self.assertTrue(ok, message)
        self.assertFalse((self.jobs / f"{job['id']}.json").exists())
        archived = self.archived_jobs / f"{job['id']}.json"
        self.assertTrue(archived.exists())
        saved = runner.load_job(archived)
        self.assertEqual(saved["state"], "done")
        self.assertIn("archived_at", saved)
        status = runner.load_job(self.status)
        self.assertEqual(status["jobs"], [])

    def test_archive_job_rejects_active_job(self):
        job = self.job([1], parallel=2)
        job["state"] = "running"
        runner.save_job(job)

        ok, message = runner.archive_job(job["id"])

        self.assertFalse(ok)
        self.assertEqual(message, "只能封存已完成或已取消的批次")
        self.assertTrue((self.jobs / f"{job['id']}.json").exists())

    def test_build_site_index_links_generated_work_indexes(self):
        base = Path(self.tmp.name)
        docs = base / "docs"
        (docs / "T1558").mkdir(parents=True)
        (docs / "T1558" / "index.html").write_text("work index", encoding="utf-8")
        works = base / "works.json"
        works.write_text(
            '{"works":[{"id":"T1558","title":"阿毘達磨俱舍論","subtitle":"世親 / 玄奘"},'
            '{"id":"T1585","title":"成唯識論","subtitle":"護法 / 玄奘"}]}',
            encoding="utf-8",
        )

        with mock.patch.object(runner, "ROOT", base), mock.patch.object(runner, "WORKS_PATH", works):
            runner.build_site_index()

        html = (docs / "index.html").read_text(encoding="utf-8")
        self.assertIn('<a href="T1558/index.html">阿毘達磨俱舍論（T1558）</a>', html)
        self.assertIn("成唯識論（T1585）<span>護法 / 玄奘・準備中</span>", html)


if __name__ == "__main__":
    unittest.main()
