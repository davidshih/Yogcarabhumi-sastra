> Reviewer: grok-4.7-xhigh via Cursor CLI (agent 2026.10.01-e373342), read-only (--mode=ask --sandbox enabled), slot grok.

## Verdict
FIX. A caught error after the translation is moved aside can finish the command with the published file missing or replaced while `install` is still `none`, which the plan's fail guarantee forbids.

## Findings
1. **High. A finished audit can leave the translation missing or replaced when `place_exclusive` fails after the swap.**

   Evidence: `scripts/audited.py:2573-2592` (K5 step 9), `scripts/audited.py:2535-2548` (K5 step 12), `scripts/audited.py:2726-2740`; plan fail guarantee at `plans/20261013-merge-audit-pipeline-claude/plan-final.html:319` and the kill-only empty window at lines 315 and 431. `place_exclusive` (`scripts/audited.py:2268-2276`) is `os.link` then `os.unlink`. The callers catch only `FileExistsError`. `OSError` is not a subclass match the other way: `FileExistsError` is caught, `OSError(18)` (cross-device), `OSError(5)`, and a failed `staged.write_bytes` are not.

   `os.replace` has already moved the markdown off its path, and `install` is still `none`. `audit_existing_juan` restores only when `install == "installed"`, so the exception becomes a fail report and the command exits 1. That is not the kill window: the process is still alive and turns the error into a report. Three outcomes:
   - `os.link` onto the markdown path raises: the path stays empty. The start bytes sit in `audits/<job>/backup/` until a later `--resume`.
   - The hash does not match `candidate_base_sha256` and putting the backup back raises: the save made during the audit is not left on the translation path (state table, plan line 434).
   - `os.link` succeeds and `os.unlink` of the staged file raises: the path already holds the candidate, `install` stays `none`, and the fail report is written anyway. The fail guarantee says those bytes must still be the bytes from the start.
   - On the restore side, the same `OSError` leaves the path empty. A second `restore_original_translation` then sees no file, treats that as an approved re-run (`scripts/audited.py:2527-2528`), and drops `original_backup` without putting anything back (`scripts/audited.py:2546-2549`).

   Suggested fix: after `os.replace`, any failure of the following write or `place_exclusive` must put the moved file back when the destination is still missing, then fail. A failure that already linked the candidate into place must move that candidate aside and put the pre-swap bytes back before returning `install == "none"`. `FileExistsError` stays as it is: a file that appeared in between is left alone. Do not clear `original_backup` while the path is empty because this call just emptied it.

   Counterexample, in `tests/test_legacy_audit_paths.py` (`LegacyAuditPathTests`). Not run (this review cannot execute tests).

```python
def test_a_link_error_after_the_swap_puts_the_translation_back(self) -> None:
    original = self.md.read_bytes()
    done = fixture.drive(self.tree, """
import os
from pathlib import Path
real_link = os.link
def link(src, dst, *args, **kwargs):
    if Path(dst).name == "T1579-007-baihua.md":
        raise OSError(18, "Cross-device link")
    return real_link(src, dst, *args, **kwargs)
os.link = link
""", "--juans", "7", "--model", "echo-finding")
    self.assert_exit(done, 1)
    self.assertEqual(original, self.md.read_bytes())
    report = self.report()
    self.assertEqual(("fail", "none"), (report["verdict"], report["install"]))
```

   On the current tree the markdown path does not exist, so `read_bytes` raises. After the fix the command still exits 1 and the bytes equal `original`. The same class of bug, opposite leftover: patch `os.unlink` to raise `OSError(5)` when the path ends with `.candidate`. The current tree exits 1 with `【已修】` in the markdown and `install` `none`; the fix keeps the original bytes.

## Report claims not supported by the diff
The control flow and the deviations that are visible in the tree match the report (import-time paths in `build_translation_html.py`, the skeleton `work` argument, restore-when-still-installed, the post-seal hash check, the codex effort fallback, rejecting `--all` with `--term-style legacy`, `runner.pid` opened with `a+`). These execution claims are not in the diff and were not re-run here: acceptance 28/28 (including the earlier partial run), 242 tests on Python 3.14.7 and 3.12.13, `actionlint`, the llm / link / paragraph-id checks, the 81-page byte match, the mutant kills, the scratch audits, and `MERGE_HEAD` still `a5f0779`.
