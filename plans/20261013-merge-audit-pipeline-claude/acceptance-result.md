# Acceptance result: 20261013-merge-audit-pipeline-claude

Stage 5 of the run, 2026-10-13. Base commit `7e204ed` (`main` `dd46ba8` plus the three checkpoints of the run folder).
Implementer: `opus-implementer-max` (claude-opus-5-5 max), tier hard; 618,108 tokens, 105 tool uses, 51 minutes.

## What the orchestrator checked

| Check | Result |
| --- | --- |
| `git rev-parse HEAD` equals `base-commit` | yes: `7e204ede99e968b98456fa205566edff91e96d2f`. The implementer made no commit. |
| Merge state | `MERGE_HEAD` is `a5f0779` (`origin/codex/batch1-69`); 0 unmerged paths. |
| Acceptance lock, trusted copy `~/.claude/pipeline-locks/yogacara-merge-audit-pipeline-20261013-merge-audit-pipeline-claude.sha256` | `acceptance.sh`: OK, `acceptance.manifest`: OK, `tests/test_legacy_audit_acceptance.py`: OK (exit 0) |
| `bash ~/.claude/hooks/harness.sh integrity <root> <base>` | exit 0, no finding (no deleted test, no added skip, no removed assertion) |
| `timeout 5m bash plans/20261013-merge-audit-pipeline-claude/acceptance.sh` | **exit 0: 28 PASS, 0 FAIL**, 33 s (base: 9 PASS, 19 FAIL) |
| Files outside the plan | none. Every changed path maps to a plan step; `docs/`, `translations/`, `data/` and the 28 frozen paths have no diff against `dd46ba8`. |

```text
PASS  M1: the merge of codex/batch1-69 is recorded
PASS  M1: no unmerged path and no conflict marker
PASS  M1: site, translations, data and production files equal main
PASS  M1: the branch's modules, prompts and tests landed byte for byte
PASS  M1: main's 44 tests pass unchanged
PASS  M1: every published page is still the rendering of its md
PASS  M1: check_ranges accepts all 81 translations as before
PASS  M1: check_ranges strict modes and the multi-work ids
PASS  M1: llm model names
PASS  M1: production queues and prompts are unchanged
PASS  M1: llm: a claude limit on stdout is a limit
PASS  M1: llm: a Codex answer is never read as a limit
PASS  M1: llm: both callers of call_with_limit_retry work
PASS  M1: llm: self-check
PASS  M1: builder CLI: sealed writes only, legacy diagnostic, --all
PASS  M1: builder: strict ranges, both term styles, ids in both
PASS  M2: the branch's 112 tests are all present and pass
PASS  M2: the branch tests carry only the reviewed adaptations
PASS  M2: the audited pipeline has its own job folder and prompts
PASS  M2: a rewrite keeps the paragraph ids
PASS  M2: the fake backends of the legacy audit
PASS  M2: an audit job is never claimed by a translation worker
PASS  M3: .github/workflows/ci.yml is the reviewed file
PASS  M3: README documents the audited pipeline and keeps both sides
PASS  M4: the audit command and its options exist
PASS  M4: the 27 acceptance tests of the legacy audit pass
PASS  M4: the whole suite passes, with no skip
PASS  M4: no test left a file in the checkout
```

## Gates: baseline to final

| Command | Baseline (`baseline.md`) | Final |
| --- | --- | --- |
| `python3 -B -m unittest discover -s tests` (Python 3.14.7) | the repo's 44 tests pass; the planner's 27 fail by design | **242 tests, OK**, no skip |
| the same on Python 3.12.13 (`/opt/homebrew/bin/python3.12`) | not measured | 242 tests, OK |
| `python3 -B scripts/llm.py` | `llm.py self-check OK` | `llm.py self-check OK` |
| `python3 -B scripts/check_html_links.py` | `All relative links resolve.` | `All relative links resolve.` |
| `python3 -B scripts/check_paragraph_ids.py` | 81 pages, 11556 paragraphs, every id present and unique | the same |
| `bash plans/20260929-progress-page/acceptance.sh` | exit 1 in this worktree, only because its `discover` found the planner's red tests (12 PASS on a pristine `main`) | exit 0 |
| `actionlint .github/workflows/*.yml` | clean | clean, with the new `ci.yml` |

242 tests = 44 of `main` + 112 of the branch + 27 of the planner + 59 new of the implementer (three files). There is no pre-existing failure.

## Deterministic scan

| Scanner | Ran | Exit | Findings |
| --- | --- | --- | --- |
| gitleaks (staged, temporary index) | yes | 0 | 0 |
| semgrep `p/default` on the 26 changed files outside `plans/` and `docs/` | yes | 1 | 1 |
| pip-audit | no | n/a | not applicable: no dependency file changed, and the repo has none |

| # | Source | Severity | Finding | Decision | Reason |
| --- | --- | --- | --- | --- | --- |
| S-1 | semgrep | blocking (rule default) | `scripts/build_translation_html.py:102` uses SHA-1 (`insecure-hash-algorithm-sha1`). | rejected | The line is `new_paragraph_id` of `main` (`dd46ba8` L77), moved by the merge and not changed. It derives the 6 hex digits of a paragraph id, an identifier that must stay stable; it is not a signature and guards nothing. Changing the algorithm would change the id format that the paragraph-ids thread fixed on 2026-10-08, and is outside this plan. |

The tables above are the state before the outside reviews. The sections below add the rulings, fix round 1 and the state after it.

## Deviations of the implementer: ruling

The report lists 10. I read the code of each one. All are accepted.

| # | Deviation | Ruling | Reason |
| --- | --- | --- | --- |
| 1 | K9 and the whole of `audited.py` came before the first `acceptance.sh` run | accepted | Running the suite earlier would have run the branch tests against the production `runner.py` in the checkout. |
| 2 | `build_translation_html.py`: three functions of `main` read paths that are fixed at import | accepted | A locked branch test patches `bth.ROOT` between two renders. The 81 pages still reproduce byte for byte (acceptance M1). |
| 3 | The skeleton call passes `work` and `work_title` | accepted | The signature on `main` changed; without it a new juan gets a wrong title. Same call as `scripts/runner.py`. Tested. |
| 4 | A juan that fails gets its original back when an earlier, killed attempt had installed a candidate | accepted | G4 of the plan. Fix round 1 extends it to the page and the search indexes (C-2). |
| 5 | After the seal, the sealed translation hash must equal the reviewed hash | accepted | Closes the window between the check of step 9 and the seal. Tested. |
| 6 | Ten small choices in K5 (reuse on `installed_sha256`, fail on an invalid glossary or a missing data file, per-section term findings, an empty path stays empty on restore, `--resume` refuses another `--model`, backup paths only inside the job's `backup/`, exit 0 only when every juan is sealed, unchanged reports are not rewritten, four new state fields, two files under `locks/`) | accepted | Each one fails closed or records more. "An empty path stays empty on restore" is narrowed by G-1: a path that this job emptied is filled first. |
| 7 | K1: the effort of the `codex` alias is `codex_reasoning_effort or effort or CODEX_REASONING_EFFORT` | accepted | The plan did not define it; production callers pass no `effort` for that alias. |
| 8 | K3: `--all` with `--term-style legacy` is refused | accepted | `build_all` has no style argument; a refusal is better than a silent ignore. |
| 9 | K2: strict mode reports boundary problems before Source block problems | accepted | Same checks, another order; no test or caller reads the order. |
| 10 | `cmd_run` uses `acquire_runner_lock()` and opens `runner.pid` with `a+` | accepted | One lock for the worker and the audit command; `a+` does not truncate the pid of a live runner. |

## Critical modules: mutation check

`mutation-check` needs a mutation tool. `command -v mutmut` finds none, and the repo has no `pyproject.toml` (standard library only). Install command, not run without the owner's word: `uv add --dev mutmut`. Instead I made mutants by hand in throwaway copies of the tree, one edit each, and ran the suite on every one.

**Before the reviews: 25 mutants of the implementer's code, 22 killed, 3 survived.**

| Survivor | Ruling | Action |
| --- | --- | --- |
| The re-review of round 2 covers every unit, not only the rewritten ones | equivalent under the offline backends: the other units answer `pass` again | none. Fix round 1 adds a last check over every unit with the reuse rule (C-3), so the scope of round 2 no longer decides what the gate sees. |
| `apply_proposed_terms=False` flipped: a proposed term is written to the glossary | real gap: no offline backend proposed a term | test `test_a_proposed_term_is_reported_and_the_glossary_stays_as_it_is` |
| The remaining findings of round 2 are ignored | redundant gate: the ledger and content checks stop the same juan, but with another message | test `test_an_unresolved_finding_is_named_in_the_blocking_lines` pins the line `<unit> <stage>: <claim>` |

**After fix round 1:** 20 mutants of the new code and of the two gaps above, 20 killed (four had survived a first run: one was equivalent as written and was made stricter, two got a test, one guarded a line that was then removed).

**After fix round 2:** 34 mutants, 34 killed. The table is in "Mutants of the fix-round code (final tree)" below; it replaces the table of round 1, whose tests round 2 partly renamed.

## Cross-vendor diff review, round 1

- Codex's slot: Codex `gpt-6.1-sol` xhigh through `run_codex.sh diffreview`, read-only. Verdict FIX, 3 findings. File: `diff.review.r1.md`.
- Grok: `grok-4.7-xhigh` through `run_cursor.sh diffreview`, read-only. Verdict FIX, 1 finding. File: `diff.review.grok.r1.md`.

No reviewer could run a test (read-only). I ran every counterexample in a throwaway copy of the tree before ruling: all four were red on the implementer's tree.

### Diff review disposition

| # | Source | Severity | Finding | Decision | Reason | Fix |
| --- | --- | --- | --- | --- | --- | --- |
| C-1 | Codex | high | After `os.replace(md, backup)` in step 9, an I/O error that is not `FileExistsError` leaves the path of the translation empty with `install: none`. A kill after the candidate is placed and before the record leaves an install that no later failure undoes. | accepted | Counterexample red: the md was gone after exit 1. | `put_back`, `settle_swap`, `record_install`; the swap of `install_audit_candidate` runs inside `try` and settles on every exception. Tests: `test_an_io_error_during_the_swap_puts_the_translation_back`, `test_a_kill_between_the_install_and_its_record_is_undone_when_the_juan_fails`. |
| G-1 | Grok | high | The same defect, with four outcomes: a link error (path empty), an owner's save that cannot go back, `os.unlink` of the staged file fails after the link (candidate at the path, `install: none`), and the restore side (the second restore finds no file and forgets `original_backup`). | accepted, merged with C-1; the fix was narrowed in round 2 (see C2-1, C2-2) | Counterexample red: `read_bytes` raised, no file. | The C-1 fix, plus: a candidate that reached the path is recorded as installed and then taken out; `restore_original_translation` first fills a path that this job emptied; a swap that cannot be undone keeps its record. Round 1 also put the file back with a copy when no hard link could be made; round 2 removed that copy. Final tests: `test_a_candidate_that_was_placed_before_an_error_is_taken_out_again`, `test_an_original_that_cannot_go_back_leaves_the_candidate_at_the_path_and_the_report_says_so`, `test_when_nothing_can_go_back_the_job_keeps_what_it_needs_and_a_resume_finishes`, and the tests named at C2-2. |
| C-2 | Codex | high | The snapshot of the page and the search indexes (step 10) lives in memory. After a kill between the rebuild and the seal, a resume that fails puts the md back and leaves the rebuilt page and indexes online. | accepted | Counterexample red: the digest of `docs/` and `translations/` differed. The cause is the plan, not the implementer: K5 step 10 said "in memory" and step 12 said a killed process is not rolled back. | `snapshot_site` writes the snapshot to `audits/<job>/backup/<n>-site/` before the rebuild; `undo_install` puts the translation and the site back on every failure of the juan, also in a later attempt. Tests: `test_a_failed_resume_after_the_site_was_rebuilt_puts_the_site_back`, `test_a_resume_that_fails_at_the_seal_puts_back_the_site_of_before_the_first_attempt`, `test_a_search_index_that_another_job_rebuilt_is_built_again_and_not_overwritten`, `test_a_juan_that_fails_after_the_owner_saved_over_a_killed_install_gets_its_old_page_back`. |
| C-3 | Codex | medium | `llm_call` reads the policy for the event hash after the answer, and the gate stores the live glossary, policy and prompts without confirming that every attested review answered them. A policy saved while a call waits is recorded and sealed as the version of a review that never saw it. | accepted; the way the gate is bound was replaced in round 2 (see G2-1) | Counterexample red: the request of the first review did not start with the policy that the gate stored. | `llm_call` reads the policy hash when the request is made. Before the gate, one more pass over every unit with the reuse rule asks again each unit whose prompt changed (no call when nothing changed). Round 1 compared the inputs before and after that pass; round 2 pins them instead. Final tests: `test_the_gate_stores_the_policy_that_every_review_answered`, and the tests named at G2-1. |

| O-1 | orchestrator | medium | Found while re-reading the C-3 fix, not by a reviewer. A review that passed on its semantic retry is attested by the event of the retry, whose prompt hash is that of the retry envelope. `review_is_reusable` compared it with the bound prompt, so such a review was never reusable: a resume asked it again, and the new last check of C-3 would have asked it again in every run, with one more chance of a finding and no fix round left. | accepted | A test written first was red: 26 model calls where 25 are right. | `semantic_review_call` puts `bound_prompt_sha256` in the call context, `llm_call` records it in the event like the other context hashes, and `review_is_reusable` accepts either hash. Test: `test_a_review_that_passed_on_its_retry_is_not_asked_again`. |

Accepted 4 of 4 from the reviewers (3 Codex, 1 Grok; C-1 and G-1 are one defect), plus 1 of my own. Rejected 0.

## Security review

`SECURITY_REVIEW=auto` on a plan marked security-sensitive, `SECURITY_DIFF_SCAN=codex`.

- Attempt 1: `run_codex.sh securityreview` exit 3 (`stream disconnected before completion`). The fallback `plan-reviewer` with `model: fable` then failed with the account's Fable limit (HTTP 429). No review text came out of either.
- Attempt 2: the same Codex command again, queued behind the diff review. Exit 0. **Verdict PASS, 0 findings**, all 26 changed files read. File: `security.review.md`.
- It names two defects that exist on `main` and that this change does not touch or make reachable: `scripts/build_shengwen_di_html.py:60` (a TLS verification fallback, CWE-295) and `:373` (search results written through `innerHTML`, CWE-79). Decision: recorded as pre-existing, not fixed here; reported to the owner as a separate task.
- The security review read the tree before fix round 1. The fix adds no input path: the new site snapshot folder is validated like the md backup (a direct child of the job's `backup/`), and files are read from it only at paths that the code computes.
- `security.diff` was scratch input. It is deleted and not committed.

## Fix round 1 (of at most 2)

Made by the orchestrator, with the harness hold off. Only two files changed: `scripts/audited.py` and `tests/test_legacy_audit_paths.py`.

| Part | Change in `scripts/audited.py` |
| --- | --- |
| C-1 / G-1 | New `put_back`, `record_install`, `settle_swap`. `install_audit_candidate`, `restore_original_translation` and `select_audit_candidate` end every swap through `settle_swap`. |
| C-2 | New `recorded_site_backup`, `rebuild_search_indexes`, `snapshot_site`, `restore_site`, `undo_install`. `publish_audited_juan` and the end of `audit_existing_juan` use `undo_install`. `select_audit_candidate` keeps `site_backup` when the audit starts again from a file of the owner. |
| C-3 | `llm_call` reads the policy hash before the call. `review_audit_candidate` runs the last check and compares the review inputs before and after it. `record_quality_gate` takes `review_inputs`. (Round 2 replaces the comparison and that argument with the pin.) |
| O-1 | `semantic_review_call` names the bound prompt in the call context; `llm_call` records `hashes.bound_prompt_sha256`; `review_is_reusable` accepts the prompt hash or the bound prompt hash of the event. |

Choices of this fix that the plan text does not have:

- A later attempt does not write the old search indexes back. It builds them again from the translations, because another job may have published a juan between the kill and the resume. The attempt that took the snapshot writes its bytes back (seconds apart). The page of the juan always comes from the snapshot.
- A put-back that cannot make a hard link created the file exclusively (`open(..., "xb")`). **Removed in round 2** (C2-1, C2-2): the copy could delete or truncate a translation.
- A juan can now end with verdict `fail` and install `installed`: only when the original cannot be linked back to the path. The report says so, the job keeps `original_backup` (and `swap` when the path is empty), and `--resume` finishes the work.
- `restore_original_translation` uses the same put-back as the install.
- A sealed juan keeps `site_backup` in its job file: it names the snapshot of the site before the audit.

Tests after round 1: 18 new in `tests/test_legacy_audit_paths.py` (29 in the file; round 2 changes that, see below). I ran the final file against the implementer's `audited.py` in a throwaway copy: 15 of the 18 fail there (11 FAIL, 4 ERROR). The 3 that pass there pin behavior that was right and unguarded: the two mutation gaps, and `test_the_gate_stores_the_inputs_that_were_checked_and_reads_no_file_again`, which guards the new `review_inputs` argument (mutant M13).

State after fix round 1 (2026-10-13 19:54 EDT):

| Check | Result |
| --- | --- |
| `git rev-parse HEAD` | still `7e204ed` = `base-commit`; `MERGE_HEAD` `a5f0779`; 0 unmerged paths |
| Acceptance lock (trusted copy) | 3 files OK, exit 0 |
| `harness.sh integrity` | exit 0, no finding |
| `timeout 5m bash plans/20261013-merge-audit-pipeline-claude/acceptance.sh` | **exit 0: 28 PASS, 0 FAIL**, 46 s |
| `python3 -B -m unittest discover -s tests` (3.14.7) | **260 tests, OK** (242 before the fix) |
| the same on Python 3.12.13 | 260 tests, OK |
| `scripts/llm.py`, `check_html_links.py`, `check_paragraph_ids.py` | OK; all links resolve; 81 pages, 11556 paragraphs |
| `bash plans/20260929-progress-page/acceptance.sh` | exit 0, 12 PASS |
| `actionlint .github/workflows/*.yml` | clean |
| `git diff <base> -- docs translations data` | empty |
| gitleaks / semgrep | 0 / 1 (S-1 again, rejected above) |

## Re-review of fix round 1

- Codex's slot: Codex `gpt-6.1-sol` xhigh, incremental (the diff from tree `c7148ac` to tree `7505d51`). Verdict FIX, 2 findings. File: `diff.review.r2.md`.
- Grok: `grok-4.7-xhigh`, full review since the base commit. Verdict FIX, 2 findings. File: `diff.review.grok.r2.md`.

All four findings are in code that fix round 1 added. I ran each counterexample as written on the tree that the reviewers read (tree `7505d51`, in a throwaway copy): all four were red.

### Diff review disposition, re-review

| # | Source | Severity | Finding | Decision | Reason | Fix |
| --- | --- | --- | --- | --- | --- | --- |
| C2-1 | Codex | high | The copy that `put_back` falls back to cleans up with `dst.unlink()` on every error. When the exclusive open fails, or when the owner replaces the file during the write, that deletes a file that the audit did not create. | accepted | Counterexample red: `OWNER-SAVE` was gone. | The copy is removed. `put_back` is one hard link, the only step that is atomic and never replaces a file; when it fails, nothing is written and the error goes up. Tests: `PutBackTests` (4), with Codex's `test_failed_put_back_never_deletes_an_existing_save` as written. |
| C2-2 | Codex | high | A kill in the middle of that copy leaves half a file at the path. The resume drops the swap record, takes the half file for a save of the owner and starts again from it. | accepted | Counterexample red: the md was 1 byte after the resume. | The same removal: no step of the audit writes a partial file at the path of a translation. `require_hard_link` refuses the swap before the translation moves when the filesystem cannot make a hard link, so a filesystem property can no longer strand the translation in the backup folder. Tests: `test_a_filesystem_without_hard_links_fails_the_install_before_the_translation_moves`, `test_a_kill_at_the_link_probe_does_not_stop_the_resume`, `test_a_translation_that_cannot_go_back_waits_in_the_backup_folder_and_a_resume_puts_it_back` (Codex's scenario: the resume fails and the original is back, nothing else is adopted), `test_a_save_of_the_owner_that_cannot_go_back_is_put_back_by_the_resume`. |
| G2-1 | Grok | high | The last check compares the review inputs before and after the pass. A policy that changes and is put back during the pass passes that comparison: one unit is asked again under the temporary policy, and the gate seals the original as the version every review answered. The same window exists for the glossary and the prompt files. | accepted; fixed by construction, so the expectation of its test changes | Counterexample red: the audit sealed (exit 0). After the fix that scenario still ends with exit 0, and rightly: the audit reads the glossary, the policy and every prompt file once (`pinned_review_inputs`), builds every prompt of the last check and every later check from those bytes, and stores those bytes at the gate. No file is read again, so no window is left; the unit is not asked under the temporary policy at all. The error `the review inputs changed during the audit` is gone with the comparison. | `review_input_bytes`, `review_input_text`, `pinned_review_inputs`, `gate_audit_candidate`; `prompt_text`, `load_glossary`, the policy hash of `llm_call` and `review_input_artifacts` read through the pin. Tests: `test_a_policy_that_changes_and_is_put_back_in_the_last_check_reaches_no_review` (Grok's scenario), `test_a_policy_that_changes_in_the_last_check_is_not_part_of_this_audit`, `test_a_glossary_that_changes_in_the_last_check_is_not_part_of_this_audit`, `test_a_prompt_file_that_changes_in_the_last_check_is_not_part_of_this_audit`, `test_a_unit_that_is_asked_again_in_the_last_check_answers_the_pinned_policy`. |
| G2-2 | Grok | medium | After a kill, a resume that cannot take the candidate out again still rebuilds `search.json` from the translations, so the index holds the candidate while the page is the original. | accepted | Counterexample red: the mark of the candidate was in `search.json`. | `restore_site(rebuild_search=…)`: the indexes are built again only by a later attempt whose translation went back. While the candidate is still at the path, the snapshot bytes are written back. Test: `test_a_resume_that_cannot_take_the_candidate_out_keeps_it_out_of_the_site`. |

Accepted 4 of 4 (2 Codex, 2 Grok). Rejected 0.

**What this changes in the ruling on G-1.** Its counterexample expected the original bytes at the path at the end of the same command when no hard link onto that path can be made. Fix round 1 met that with a copy; C2-1 and C2-2 show that the copy can delete or truncate a translation, which is worse. The final behavior: a filesystem that cannot make hard links is refused before the translation moves (nothing changes); a link error after the move leaves the translation in the backup folder, the job keeps its record, the report names the file (`the translation is in audits/<job>/backup/<n>-<name>: it could not go back to its path (…)`), and the next restore or `--resume` puts it back. The path is never filled with anything but a complete file by an atomic step.

## Fix round 2 (the last one)

Made by the orchestrator, with the harness hold off. Two files changed again: `scripts/audited.py` and `tests/test_legacy_audit_paths.py`.

| Part | Change in `scripts/audited.py` |
| --- | --- |
| C2-1, C2-2 | `put_back` is `os.link` and nothing else. New `require_hard_link` (a probe link next to the candidate, removed at once), called in `install_audit_candidate` before the translation moves. `settle_swap` reports a way back that failed with the place of the file and keeps the swap record. `os.replace(md, backup)` is inside the `try` of the swap. |
| G2-1 | New `PINNED_REVIEW_INPUTS`, `review_input_bytes`, `review_input_text`, `pinned_review_inputs`. `review_audit_candidate` ends with `with pinned_review_inputs(): gate_audit_candidate(...)`; the last check, the paragraph id check, the ledger, the content checks and the gate are that new function. `record_quality_gate` lost the `review_inputs` argument of round 1. |
| G2-2 | `restore_site` takes `rebuild_search`; `undo_install` sets it only for a later attempt whose translation went back. |

Tests: `tests/test_legacy_audit_paths.py` has 41 tests now (11 before the fix rounds). Round 2 added 15, rewrote 4 of round 1 whose behavior changed with the copy (`…goes_back_as_a_new_file` twice, `…when_no_hard_link_can_be_made`, `…leaves_the_candidate_at_the_path…`) and removed 3 that tested the comparison and the copy (`test_inputs_that_change_during_the_last_check_…`, `test_the_gate_stores_the_inputs_that_were_checked_…`, the every-other-new-file variant). I ran the final test file against the round-1 code in a throwaway copy: 13 tests fail there, which are the tests of the four findings and of the behavior they change.

State after fix round 2 (2026-10-13 20:41 EDT):

| Check | Result |
| --- | --- |
| `git rev-parse HEAD` | still `7e204ed` = `base-commit`; `MERGE_HEAD` `a5f0779`; 0 unmerged paths |
| Acceptance lock (trusted copy) | 3 files OK, exit 0 |
| `harness.sh integrity` | exit 0, no finding |
| `timeout 5m bash plans/20261013-merge-audit-pipeline-claude/acceptance.sh` | **exit 0: 28 PASS, 0 FAIL**, 53 s |
| `python3 -B -m unittest discover -s tests` (3.14.7) | **272 tests, OK** (242 after the implementer, 260 after fix round 1) |
| the same on Python 3.12.13 | 272 tests, OK |
| `scripts/llm.py`, `check_html_links.py`, `check_paragraph_ids.py` | OK; all links resolve; 81 pages, 11556 paragraphs |
| `bash plans/20260929-progress-page/acceptance.sh` | exit 0, 12 PASS |
| `actionlint .github/workflows/*.yml` | clean |
| `git diff <base> -- docs translations data` | empty |
| gitleaks / semgrep | 0 / 1 (S-1 again, rejected above) |

### Mutants of the fix-round code (final tree)

34 mutants on the final tree, in three throwaway copies, each against `tests.test_legacy_audit_paths` and `tests.test_legacy_audit_acceptance`: **34 killed, 0 survived**. M4 survived the first run: after round 2 the last check stops the same juan with the same line, one model call later. `test_an_unresolved_finding_is_named_in_the_blocking_lines` now also pins the number of model calls (27), and M4 was run again: killed.

| Mutant | Killed by |
| --- | --- |
| M1 the site snapshot is forgotten when the audit starts again from an owner's file | `test_a_juan_that_fails_after_the_owner_saved_over_a_killed_install_gets_its_old_page_back` |
| M2 the restore forgets the original while the path is empty | `test_when_nothing_can_go_back_the_job_keeps_what_it_needs_and_a_resume_finishes` |
| M3 a proposed term changes the glossary | `test_a_proposed_term_is_reported_and_the_glossary_stays_as_it_is` |
| M4 the findings that remain after the fix round are ignored | `test_an_unresolved_finding_is_named_in_the_blocking_lines` |
| M5 no last check of the reviews | `test_a_policy_that_changes_in_the_last_check_is_not_part_of_this_audit`, `test_a_unit_that_is_asked_again_in_the_last_check_answers_the_pinned_policy` and 1 more |
| M8 a later attempt writes the old search indexes back and does not build them again | `test_a_search_index_that_another_job_rebuilt_is_built_again_and_not_overwritten` |
| M8b a later attempt leaves the search indexes as they are | `test_a_failed_resume_after_the_site_was_rebuilt_puts_the_site_back`, `test_a_juan_that_fails_after_the_owner_saved_over_a_killed_install_gets_its_old_page_back` and 2 more |
| M9 a placed candidate without a record is not adopted | `test_a_candidate_that_was_placed_before_an_error_is_taken_out_again`, `test_a_kill_between_the_install_and_its_record_is_undone_when_the_juan_fails` |
| M10 the policy hash of an event is read after the answer | `test_a_unit_that_is_asked_again_in_the_last_check_answers_the_pinned_policy`, `test_the_gate_stores_the_policy_that_every_review_answered` |
| M11 every attempt takes a new site snapshot | `test_a_resume_that_cannot_take_the_candidate_out_keeps_it_out_of_the_site`, `test_a_resume_that_fails_at_the_seal_puts_back_the_site_of_before_the_first_attempt` |
| M12 a failed juan with only a rebuilt site is left alone | `test_a_juan_that_fails_after_the_owner_saved_over_a_killed_install_gets_its_old_page_back` |
| M14 an error in the swap does not settle it | `test_translation_edited_during_the_audit_is_never_overwritten`, `test_a_candidate_that_was_placed_before_an_error_is_taken_out_again` and 4 more |
| M15 an error on the way back does not settle the swap | `test_a_resume_that_cannot_take_the_candidate_out_keeps_it_out_of_the_site`, `test_an_original_that_cannot_go_back_leaves_the_candidate_at_the_path_and_the_report_says_so` |
| M17 the restore does not let a newer file stay at the path | `test_a_save_of_the_owner_while_the_install_is_undone_stays_at_the_path` |
| M18 the site is not put back when the translation could not be | `test_a_failed_resume_after_the_site_was_rebuilt_puts_the_site_back`, `test_a_juan_that_fails_after_the_owner_saved_over_a_killed_install_gets_its_old_page_back` and 6 more |
| M19 a review that passed on its retry is not reusable | `test_a_review_that_passed_on_its_retry_is_not_asked_again` |
| M20 the event of a retry does not name its bound prompt | `test_a_review_that_passed_on_its_retry_is_not_asked_again` |
| N1 no check for hard links before the translation moves | `test_a_filesystem_without_hard_links_fails_the_install_before_the_translation_moves`, `test_a_kill_at_the_link_probe_does_not_stop_the_resume` |
| N2 put_back fails on a file that is at the path | `test_a_file_that_is_at_the_path_stays_and_so_does_the_source`, `test_a_save_of_the_owner_while_the_install_is_undone_stays_at_the_path` |
| N3 put_back leaves the moved file in the backup folder | `test_a_refused_swap_leaves_nothing_in_the_backup_folder`, `test_a_translation_that_cannot_go_back_waits_in_the_backup_folder_and_a_resume_puts_it_back` and 1 more |
| N4 a way back that fails is not reported with the place of the file | `test_a_save_of_the_owner_that_cannot_go_back_is_put_back_by_the_resume`, `test_a_translation_that_cannot_go_back_waits_in_the_backup_folder_and_a_resume_puts_it_back` and 1 more |
| N6 a probe that a kill left stops the next attempt | `test_a_kill_at_the_link_probe_does_not_stop_the_resume` |
| N7 the probe is left in the job folder | `test_a_kill_at_the_link_probe_does_not_stop_the_resume`, `test_a_swap_leaves_no_helper_file_in_the_job_folder` |
| N9 put_back replaces a file at the path | `test_a_resume_that_cannot_take_the_candidate_out_keeps_it_out_of_the_site`, `test_a_save_of_the_owner_that_cannot_go_back_is_put_back_by_the_resume` and 7 more |
| N10 the swap record is dropped when the way back fails | `test_a_save_of_the_owner_that_cannot_go_back_is_put_back_by_the_resume`, `test_a_translation_that_cannot_go_back_waits_in_the_backup_folder_and_a_resume_puts_it_back` and 1 more |
| P1 the last check pins nothing | `test_a_glossary_that_changes_in_the_last_check_is_not_part_of_this_audit`, `test_a_policy_that_changes_and_is_put_back_in_the_last_check_reaches_no_review` and 3 more |
| P2 a prompt reads the policy file, not the pinned policy | `test_a_policy_that_changes_and_is_put_back_in_the_last_check_reaches_no_review`, `test_a_policy_that_changes_in_the_last_check_is_not_part_of_this_audit` and 1 more |
| P3 a prompt reads its template file, not the pinned template | `test_a_prompt_file_that_changes_in_the_last_check_is_not_part_of_this_audit` |
| P4 the glossary is read from the file, not from the pin | `test_a_glossary_that_changes_in_the_last_check_is_not_part_of_this_audit` |
| P5 the policy hash of an event comes from the file, not from the pin | `test_a_unit_that_is_asked_again_in_the_last_check_answers_the_pinned_policy` |
| P6 the search indexes are built from a candidate that is still at the path | `test_a_resume_that_cannot_take_the_candidate_out_keeps_it_out_of_the_site` |
| P8 the gate stores the policy file, not the pinned policy | `test_a_policy_that_changes_in_the_last_check_is_not_part_of_this_audit`, `test_a_unit_that_is_asked_again_in_the_last_check_answers_the_pinned_policy` |
| P9 the gate stores the glossary file, not the pinned glossary | `test_a_glossary_that_changes_in_the_last_check_is_not_part_of_this_audit` |
| P10 the gate stores the prompt files, not the pinned prompts | `test_a_prompt_file_that_changes_in_the_last_check_is_not_part_of_this_audit` |

## Re-review of fix round 2

| Attempt | Result |
| --- | --- |
| Codex `gpt-6.1-sol` xhigh, incremental since tree `7505d51` (2026-10-13 20:42) | exit 3, `CODEX_UNAVAILABLE`: the usage limit of the 5-hour window (it stood at 98% at the start; another session of this account was reviewing at the same time). |
| Codex's slot through Cursor, `gpt-5.6-sol` xhigh (20:44) | exit 3, `CURSOR_UNAVAILABLE`: the monthly usage limit of that model on Cursor. It resets on 2026-11-02, or with a spend limit, which only the owner sets. |
| Grok `grok-4.7-xhigh` through Cursor (20:42 to 20:58) | exit 1: `You're out of usage` on Cursor after 16 minutes. No review was written. |
| Codex `gpt-6.1-sol` xhigh, incremental since tree `7505d51` (23:47, after the quota reset) | exit 0. **Verdict FIX, 2 findings (both medium).** File: `diff.review.md`. It confirms that the two `put_back` findings of the round before are fixed. |

Grok did not read fix round 2: Cursor stays out of usage. So this re-review has one outside reviewer.

Between the attempts the branch was committed (`a979331`, `ac3fa85`), pushed, and opened as draft pull request #9. Its `ci` check passed on GitHub (run 38493433975, the first run of the new workflow, Python 3.12 on ubuntu).

### Diff review disposition, re-review of fix round 2

I ran both counterexamples as written on the final tree, in a throwaway copy: both are red.

| # | Source | Severity | Finding | Decision | Reason | Fix |
| --- | --- | --- | --- | --- | --- | --- |
| C3-1 | Codex | medium | When a later attempt cannot take the candidate out (the original cannot be linked back), `undo_install` calls `restore_site(rebuild_search=False)`, which writes the whole snapshot of every search index back. A juan that another job published between the kill and the resume loses its records in `search.json`. The G2-2 fix introduced this: it keeps the candidate out of the index, at the price of the other juan. | valid; **not fixed** | Counterexample red: `OTHER-JOB` was gone from `docs/T1579/search.json`. It needs four things at once: a kill after the rebuild, another job that publishes before the resume, a resume that fails, and an original that cannot be linked back. The md files and the pages are not touched; the index is whole again at the next rebuild. | none: the fix limit is reached. Proposed: in that one case put back only the records of the failed juan from the snapshot and keep every other record of the live index. |
| C3-2 | Codex | medium | `pinned_review_inputs` takes its list of prompt files from `glob("*.txt")`, and `review_input_bytes` reads a path that is not in the pin from the live file. A required prompt that is absent while the folder is listed and created again right after is read live during the last check and is not stored at the gate. | valid; **not fixed** | Counterexample red: the juan sealed and the gate had no `prompt:review_terms.txt` artifact. It needs a prompt file to disappear at the instant of the listing and to come back before the first prompt is built. | none: the fix limit is reached. Proposed: pin the required files by name, and refuse (not read live) any review input that is not in the pin. |

Valid 2 of 2. Fixed 0: both fixes would be a third fix round.

## Run status: stopped at the fix limit

The run used its two fix rounds, and the review of the second one still says FIX. By the rule of the pipeline the orchestrator stops here and the owner decides. Nothing is merged: pull request #9 is a draft.

What holds on the pushed commit `ac3fa85`: the acceptance lock, `harness.sh integrity` (exit 0), `acceptance.sh` (28 PASS, exit 0), 272 tests on Python 3.14.7 and 3.12.13, the gates, `ci` on GitHub, and no difference in `docs/`, `translations/`, `data/` against `main`. The two open findings are in failure paths of `scripts/audited.py audit`; the merge itself, the production runner and the site are not affected by them.

The choices for the owner:

1. Allow a third fix round for C3-1 and C3-2 only, then one more Codex review (Grok is not available before 2026-11-02 without a spend limit).
2. Merge as it is and fix the two findings in a follow-up pull request before the audit command is used on juan 67 to 72.
3. Merge as it is and accept the two findings as known limits of the audit command.

## Codex cost

| Stage | Line |
| --- | --- |
| Plan review, round 1 (Codex `gpt-6.1-sol` xhigh) | `quota: 5h 35%->45% (+10), weekly 31%->33% (+2), input 2733675 tokens (2580352 cached), output 28021` |
| Plan review, round 1 (Grok) | `cursor usage: input 212409 (cached 3639168) tokens, output 65893` |
| Plan review, round 2 (Codex) | `quota: 5h 50%->59% (+9), weekly 34%->35% (+1), input 2545179 tokens (2382208 cached), output 18607` |
| Implement | Claude (`opus-implementer-max`): no line |
| Security review, attempt 1 (Codex, failed with exit 3) | `quota: 5h 0%->16% (+16), weekly 37%->39% (+2), input 6553997 tokens (6316544 cached), output 13374` |
| Diff review, round 1 (Codex) | `quota: 5h 16%->27% (+11), weekly 39%->41% (+2), input 3851707 tokens (3647360 cached), output 14691` |
| Diff review, round 1 (Grok) | `cursor usage: input 337432 (cached 5536128) tokens, output 131649` |
| Security review, attempt 2 (Codex) | `quota: 5h 54%->69% (+15), weekly 45%->48% (+3), input 6536148 tokens (6312576 cached), output 12281` |
| Re-review of fix round 1, first start (Codex and Grok) | stopped by the orchestrator after 5 minutes (O-1); no review, no line |
| Re-review of fix round 1 (Codex, incremental) | `quota: 5h 84%->90% (+6), weekly 50%->51% (+1), input 1277693 tokens (1165568 cached), output 13342` |
| Re-review of fix round 1 (Grok, full) | `cursor usage: input 257160 (cached 2496000) tokens, output 66879` |
| Re-review of fix round 2, Codex (failed: usage limit) | `quota: 5h 98%->100% (+2), weekly 52%->52% (+0), input 285455 tokens (235136 cached), output 1632` |
| Re-review of fix round 2, Codex's slot through Cursor (failed: usage limit) | `cursor usage: unknown` |
| Re-review of fix round 2, Grok (failed: out of usage) | `cursor usage: unknown` |
| Re-review of fix round 2, Codex after the quota reset | `quota: 5h 0%->6% (+6), weekly 52%->53% (+1), input 1415378 tokens (1289472 cached), output 11047` |

## Quality gate

Not degraded. Outside-family reviewers produced the plan reviews (Codex twice, Grok once), every diff review (Codex and Grok on the implementation and on fix round 1, Codex alone on fix round 2) and the security review (Codex). The one same-family fallback that was tried (the security review on `plan-reviewer`) produced nothing and was replaced by the Codex retry. No same-family review stood in for an outside one.
