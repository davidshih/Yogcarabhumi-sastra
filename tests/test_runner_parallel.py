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
        self.locks = base / "locks"
        self.status = base / "docs" / "status.json"
        self.jobs.mkdir()
        self.locks.mkdir()
        self.status.parent.mkdir()
        self.patches = [
            mock.patch.object(runner, "JOBS_DIR", self.jobs),
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


if __name__ == "__main__":
    unittest.main()
