> Reviewer: grok-4.7-xhigh via Cursor CLI (agent 2026.10.12-d47ca18), read-only (--mode=ask --sandbox enabled), slot grok.

## Verdict
FIX. The merge matches the plan’s milestones, and the accepted swap and snapshot repairs hold for the cases they name, but the last review check can still seal a policy a review did not answer, and a later attempt can publish a failed candidate into the search index.

## Findings
1. **high** — A policy edited and then restored during the last check is sealed as the version every review answered.

   **Evidence:** `scripts/audited.py:2523-2540` (K5 step 8; the accepted C-3 repair). The snapshot is taken once in `review_input_artifacts`, round 3 then builds each prompt from the live files (`prompt_text` at `488-491`), and the only comparison is another read after that pass returns. `record_quality_gate` stores the snapshot taken before the pass (`2561`).

   **Why it matters:** Juan 7 with `--model echo` makes 24 `prompt_text` calls in round 1, then the last check starts. If the policy file changes before the first prompt of that check and is restored before the next prompt, unit `T1579-007-001` is asked again under the temporary policy (its request and `hashes.policy_sha256` record that text) while every later unit is reused under the original. The two `review_input_artifacts` results match, the juan seals, and the gate’s policy artifact is the original file. A review that passed under the other policy is sealed as an answer to the restored one. The same window exists for the glossary and the prompt files. A change that is still there at the end is already rejected; a change that is undone during the pass is not.

   **Suggested fix:** Remember a mismatch as soon as the live inputs differ from the snapshot, including after each prompt is built, and fail the juan with `the review inputs changed during the audit` even if a later read matches. Before the gate, require every review event used for an attestation to have `hashes.policy_sha256` equal to the stored policy artifact.

   **Counterexample:** add this to `tests/test_legacy_audit_paths.py`. It fails on this tree because the audit seals. It passes once a reverted edit fails the juan. **not run** (this sandbox cannot execute commands).

```python
POLICY_REVERTS_DURING_THE_LAST_CHECK = """
real_prompt = audited.prompt_text
real_pass = audited.review_pass
ORIGINAL = None
def prompt_in_the_last_check(name, **vars):
    global ORIGINAL
    policy = Path("prompts/audited/common_policy.txt")
    if ORIGINAL is None:
        ORIGINAL = policy.read_text(encoding="utf-8")
        policy.write_text(ORIGINAL + "\\n- New policy.\\n", encoding="utf-8")
    elif policy.read_text(encoding="utf-8") != ORIGINAL:
        policy.write_text(ORIGINAL, encoding="utf-8")
    return real_prompt(name, **vars)
def flip_only_in_the_last_check(*args, **kwargs):
    round_number = kwargs.get("round_number", args[5] if len(args) > 5 else None)
    pass_name = kwargs.get("pass_name", args[3] if len(args) > 3 else None)
    audited.prompt_text = (
        prompt_in_the_last_check
        if round_number == 3 and pass_name == "review_terms" else real_prompt
    )
    return real_pass(*args, **kwargs)
audited.review_pass = flip_only_in_the_last_check
"""

def test_a_policy_that_changes_and_reverts_during_the_last_check_fails_the_juan(self) -> None:
    before = fixture.digest(self.tree, "docs", "translations")

    done = fixture.drive(self.tree, POLICY_REVERTS_DURING_THE_LAST_CHECK,
                         "--juans", "7", "--model", "echo")

    self.assert_exit(done, 1)
    self.assertEqual(["the review inputs changed during the audit"], self.report()["blocking"])
    self.assertEqual([], [event for event in self.events() if event["type"] == "quality_gate"])
    self.assertEqual(before, fixture.digest(self.tree, "docs", "translations"))
```

2. **medium** — After a kill, a resume that cannot put the original translation back rebuilds `search.json` from the failed candidate.

   **Evidence:** `scripts/audited.py:2778-2793` (K5 step 12; the accepted C-2 repair). `undo_install` always calls `restore_site` after a translation error. For a later attempt `restore_site` writes the page back from the snapshot, then `rebuild_search_indexes` (`2731-2734`), and `scripts/build_search_index.py:18-38` indexes whatever `translations/*-baihua.md` contains now. `restore_original_translation` (`2617-2622`) leaves `install` as `installed` and the candidate at the path when the original cannot be created and the moved file can (`EVERY_OTHER_NEW_FILE_FAILS`). The same-attempt path writes the snapshot’s index bytes back (`2769-2777`), which is why the first-attempt test still has a clean index.

   **Why it matters:** Kill the process after the page and the index were rebuilt (`KILL_AT_THE_SEAL`), then resume with the seal failing and that filesystem patch. The page returns to the pre-audit bytes, and the report says the translation could not be put back, while `docs/T1579/search.json` is rebuilt from the markdown that still contains `【已修】`. Readers find the unreviewed wording in search. The page they open is the original.

   **Suggested fix:** Rebuild the indexes only after the original translation is back at its path. When `install` is still `installed`, write the snapshotted `search.json` bytes back the same way a same-attempt restore does, and leave `site_backup` in place if that write fails.

   **Counterexample:** add this to `tests/test_legacy_audit_paths.py`. It fails on this tree because the index contains `【已修】`. It passes once a failed resume leaves that mark out of the index. **not run**.

```python
def test_a_failed_resume_that_cannot_restore_the_translation_leaves_the_candidate_out_of_search(self) -> None:
    search = self.tree / "docs" / WORK / "search.json"
    killed = fixture.drive(self.tree, KILL_AT_THE_SEAL, "--juans", "7", "--model", "echo-finding")
    self.assert_exit(killed, 9)

    resumed = fixture.drive(
        self.tree, fixture.FAIL_AT_SEAL + EVERY_OTHER_NEW_FILE_FAILS,
        "--resume", self.job_path().stem,
    )

    self.assert_exit(resumed, 1)
    self.assertNotIn(MARK, self.page.read_text(encoding="utf-8"))
    self.assertNotIn(MARK, search.read_text(encoding="utf-8"))
```

## Report claims not supported by the diff
`implement-report.md` says `tests/test_legacy_audit_paths.py` has 12 tests and that the suite is 242. That file defines 29 test methods; the extra 18 are the fix-round tests named in `acceptance-result.md`, which also records 260 tests after that round.
