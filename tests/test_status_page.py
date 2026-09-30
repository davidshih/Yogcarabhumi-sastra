import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import runner  # noqa: E402


class StatusPageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.docs = base / "docs"
        self.jobs = base / "jobs"
        self.archive = self.jobs / "archive"
        self.locks = base / "locks"
        self.works = base / "works.json"
        for path in (self.docs, self.jobs, self.archive, self.locks):
            path.mkdir(parents=True, exist_ok=True)
        self.patches = [
            mock.patch.object(runner, "JOBS_DIR", self.jobs),
            mock.patch.object(runner, "ARCHIVED_JOBS_DIR", self.archive),
            mock.patch.object(runner, "LOCKS_DIR", self.locks),
            mock.patch.object(runner, "STATUS_JSON", self.docs / "status.json"),
            mock.patch.object(runner, "WORKS_PATH", self.works),
        ]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in reversed(self.patches):
            patch.stop()
        self.tmp.cleanup()

    def write_works(self, works):
        self.works.write_text(json.dumps({"works": works}, ensure_ascii=False), encoding="utf-8")

    def write_page(self, work, juan):
        translations = self.docs / work / "translations"
        translations.mkdir(parents=True, exist_ok=True)
        (translations / f"{work}-{juan:03d}-baihua.html").write_text("x", encoding="utf-8")

    def write_job(self, name, work, juans, state, progress):
        job = {"id": name, "work": work, "juans": juans, "state": state, "progress": progress}
        (self.jobs / f"{name}.json").write_text(json.dumps(job), encoding="utf-8")

    def test_cell_states_and_counts(self):
        self.write_works([{"id": "T0001", "title": "甲論", "subtitle": "某譯", "juans": 4}])
        self.write_page("T0001", 1)
        self.write_page("T0001", 2)
        self.write_job("active", "T0001", [2, 3], "running", {
            "2": {"step": "segment"}, "3": {"step": "translate"},
        })
        self.write_job("cancelled", "T0001", [4], "cancelled", {"4": {"step": "translate"}})

        runner.publish_status()

        html = (self.docs / "status.html").read_text(encoding="utf-8")
        self.assertIn('data-total="4" data-done="1" data-active="2" data-todo="1"', html)
        self.assertIn('<li class="juan is-done" data-juan="1"><a ', html)
        self.assertIn('<li class="juan is-active" data-juan="2"', html)
        self.assertIn('href="T0001/translations/T0001-002-baihua.html"', html)
        self.assertIn('<li class="juan is-active" data-juan="3"', html)
        self.assertNotIn('href="T0001/translations/T0001-003-baihua.html"', html)
        self.assertIn('<li class="juan is-todo" data-juan="4"', html)
        self.assertNotIn('href="T0001/translations/T0001-004-baihua.html"', html)
        self.assertIn('class="progress-stamp"', html)

    def test_work_without_juans_uses_highest_seen_juan(self):
        self.write_works([{"id": "T0002", "title": "乙論", "subtitle": ""}])
        self.write_page("T0002", 3)

        runner.publish_status()

        html = (self.docs / "status.html").read_text(encoding="utf-8")
        self.assertIn('data-total="3" data-done="1" data-active="0" data-todo="2"', html)
        self.assertEqual(html.count('class="juan is-todo"'), 2)
        self.assertEqual(html.count('class="juan is-done"'), 1)
        self.assertIn('<li class="juan is-done" data-juan="3"><a ', html)
