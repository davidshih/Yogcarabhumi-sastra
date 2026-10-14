# Baseline: 20261013-merge-audit-pipeline-claude

Measured on the base commit 7e204ed (main `dd46ba8` plus the checkpoints of the run folder), 2026-10-13 17:47 EDT, before the implementer started. `acceptance.sh` is not part of the baseline.

| Command | Exit | Result |
| --- | --- | --- |
| `python3 -B -m unittest tests.test_check_translation_coverage tests.test_llm_codex_config tests.test_paragraph_ids tests.test_runner_parallel tests.test_status_page` | 0 | Ran 44 tests in 0.193s  OK  |
| `python3 -B -m unittest discover -s tests` | 1 | Ran 71 tests in 3.906s  FAILED (failures=30)  |
| `python3 -B scripts/llm.py` | 0 | llm.py self-check OK  |
| `python3 -B scripts/check_html_links.py` | 0 | All relative links resolve.  |
| `python3 -B scripts/check_paragraph_ids.py` | 0 | 81 pages, 11556 translation paragraphs: every id present and unique.  |
| `bash plans/20260929-progress-page/acceptance.sh` | 1 | PASS  CSS filter hides non-matching cells PASS  CSS marks the selected tab chip PASS  README documents status.html  |

Notes:

- The repo's own suite is the first row: 44 tests, all pass.
- `discover` also runs `tests/test_legacy_audit_acceptance.py`, the planner's 27 tests of a command that does not exist yet. They fail on the base by design (the reproduction of the feature); they are not pre-existing failures of the repo.
- `bash plans/20260929-progress-page/acceptance.sh` exits 1 in this worktree for one reason: its check `full unittest suite` runs `discover`, which finds the same 27 planner tests. Its other 11 checks pass. On a pristine checkout of `main` (`dd46ba8`, without the planner's test file) the script gives 12 PASS, 0 FAIL, exit 0 (measured in a scratch worktree, 2026-10-13).
- So no failing check exists on `main`. Every failure after the change comes from the change.
- `actionlint`: installed, clean on the base.
