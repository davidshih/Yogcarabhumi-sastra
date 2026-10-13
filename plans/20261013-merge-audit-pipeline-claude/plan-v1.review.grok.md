> Reviewer: grok-4.7-xhigh via Cursor CLI (agent 2026.10.01-e373342), read-only (--mode=ask --sandbox enabled), slot grok.

## Verdict
REVISE. A literal reading of the legacy-audit steps fails the locked clean-juan test, and the resume rule after the markdown is swapped contradicts the state table, with no acceptance check on that window.

## Findings

1. **High — The legacy audit never sets `review_verdict`, so the unchanged quality gate fails a clean volume.**  
   Evidence: plan K4 item 11 and K5 step 9(c); branch `scripts/runner.py` `record_quality_gate` at lines 1705–1708 (`if prog.get("review_verdict") != "pass"` it appends `"review chain did not produce a passing re-gate verdict"`). Only `run_review_chain` sets that field (lines 1534–1535 and 1552). K5 calls `review_pass` directly and then `record_quality_gate`.  
   Why it matters: `tests/test_legacy_audit_acceptance.py` `test_clean_juan_passes_and_changes_nothing` requires exit 0 and a `quality_gate` event whose `verdict` is `pass`. The extra issue makes the gate fail, `require_quality_gate` raises, and the volume does not seal.  
   Suggested fix: in K5, when the re-review and the glossary check are clean, set `prog["review_verdict"] = "pass"` before `record_quality_gate`. On a failed re-review, return the fail report from step 8 and do not call the gate.

2. **High — After the real markdown is replaced, resume wipes the attestations the state table says are kept, and neither that window nor a failed seal is in the oracle.**  
   Evidence: plan K5 step 2 (reuse the candidate only when `candidate_base_sha256` equals the current markdown; otherwise clear contracts and attestations) versus the state table row “在第 9 步 (e) 換掉 md 之後、(g) 封存之前被硬殺” (unchanged units’ attestations stay valid and resume converges to a seal). Step 9(d) stores the pre-audit hash, and (e) then changes the markdown, so those two hashes differ. The exception rollback in step 9 (“(e) 到 (g) 之間任何例外” restores the markdown, the volume HTML, and every `search.json`) is also absent from `acceptance.sh` and from `tests/test_legacy_audit_acceptance.py`. The plan assigns those tests to the implementer (M4 step 3), so they are not an oracle.  
   Why it matters: a crash after the swap starts a full second review. A later model disagreement can fail a volume whose new text is already what the user has, while the page and `search.json` are still the previous bytes. An exception at `create_volume_attestation` can leave the new text on the site if the restore is skipped. The user-visible rule is that saved text is not lost and the site does not settle on a mix of old and new bytes.  
   Suggested fix: state one rule. After (e), resume continues when the markdown hash equals the candidate file, and it does not clear an attestation whose translation hash still matches. Keep the step 9(d) refusal when the hash matches neither the pre-audit base nor the candidate. Add:

```bash
seal_failure_restores() {  # exception at create_volume_attestation rolls the site back
  python3 -B -c '
import shutil, subprocess, sys, tempfile
from pathlib import Path
import tests.test_legacy_audit_acceptance as t
root = Path(tempfile.mkdtemp())
tree = root / "repo"
t.build_template(root / "template")
shutil.copytree(root / "template", tree)
md = tree / "translations" / "T1579-007-baihua.md"
search = tree / "docs" / "T1579" / "search.json"
before_md, before_search = md.read_bytes(), search.read_bytes()
code = """
import sys
sys.path.insert(0, "scripts")
import audited
def boom(*_a, **_k):
    raise RuntimeError("seal failed")
audited.create_volume_attestation = boom
sys.argv = ["audited.py", "audit", "--juans", "7", "--model", "echo-finding", "--no-push"]
try:
    audited.main()
except SystemExit as err:
    raise SystemExit(0 if err.code in (0, None) else err.code)
"""
done = subprocess.run([sys.executable, "-B", "-c", code], cwd=tree, capture_output=True, text=True)
assert done.returncode != 0, done.stderr[-400:]
assert md.read_bytes() == before_md, "published markdown was kept after a failed seal"
assert search.read_bytes() == before_search, "search.json was kept after a failed seal"
'
}
check "M4: a failed seal restores the markdown and search.json" seal_failure_restores
```

```bash
edit_during_audit_is_kept() {  # step 9(d): a write to the real md is not overwritten
  python3 -B -c '
import shutil, subprocess, sys, tempfile
from pathlib import Path
import tests.test_legacy_audit_acceptance as t
root = Path(tempfile.mkdtemp())
tree = root / "repo"
t.build_template(root / "template")
shutil.copytree(root / "template", tree)
code = """
import sys
sys.path.insert(0, "scripts")
import audited
_real = audited.review_pass
_once = {"done": False}
def wrapped(*a, **k):
    out = _real(*a, **k)
    if not _once["done"]:
        path = __import__("pathlib").Path("translations/T1579-007-baihua.md")
        path.write_text(path.read_text(encoding="utf-8") + "\nUSER-EDIT\n", encoding="utf-8")
        _once["done"] = True
    return out
audited.review_pass = wrapped
sys.argv = ["audited.py", "audit", "--juans", "7", "--model", "echo", "--no-push"]
try:
    audited.main()
except SystemExit as err:
    raise SystemExit(0 if err.code in (0, None) else err.code)
"""
done = subprocess.run([sys.executable, "-B", "-c", code], cwd=tree, capture_output=True, text=True)
text = (tree / "translations" / "T1579-007-baihua.md").read_text(encoding="utf-8")
assert done.returncode != 0, done.stderr[-400:]
assert "the translation changed during the audit" in (done.stdout + done.stderr + text)
assert "USER-EDIT" in text, "the edit made during the audit was overwritten"
'
}
check "M4: an edit during the audit is not overwritten" edit_during_audit_is_kept
```

3. **Medium — `--resume` is used as a path segment and is not constrained.**  
   Evidence: plan K5 (`--resume <job id>` loads `jobs/audited/<id>.json`) and the security section (the id becomes part of a path). Branch `scripts/audit.py` `AuditStore.__init__` line 65 sets `self.job_dir = root / "audits" / job_id`, and `append` line 88 does `self.job_dir.mkdir(parents=True, exist_ok=True)`. Branch `save_job` writes `JOBS_DIR / f"{job['id']}.json"`. A probe of `AuditStore(cwd, "../../outside")` resolves to `audits/../../outside`. K5 only says a missing file or a wrong `kind` raises `SystemExit`.  
   Why it matters: `audit --resume ../outside` can create directories outside `audits/` and `jobs/audited/`. This is a new CLI surface. The generated id format is safe; the resume argument is not.  
   Suggested fix: before any join, require the id to match `^[0-9]{8}-[0-9]{6}-audit-[0-9a-f]{4}$` (the format K5 already assigns) and reject anything else with `SystemExit` and no file created. Add:

```bash
resume_id_stays_inside_tree() {
  python3 -B -c '
import shutil, tempfile
from pathlib import Path
import tests.test_legacy_audit_acceptance as t
root = Path(tempfile.mkdtemp())
tree = root / "repo"
t.build_template(root / "template")
shutil.copytree(root / "template", tree)
outside = root / "outside"
done = t.audit(tree, "--resume", "../outside", "--no-push")
assert done.returncode != 0, done.stderr
assert not outside.exists()
assert not (tree / "outside").exists()
assert not (tree / "audits").exists()
'
}
check "M4: a resume id cannot escape the tree" resume_id_stays_inside_tree
```

4. **Medium — The legacy-audit oracle only exercises juans 7, 72, and 76, so those three volumes can be special-cased.**  
   Evidence: `tests/test_legacy_audit_acceptance.py` lines 29 and 178–384 (`FIXTURE_JUANS = (7, 72, 76)` and every `audit(...)` call). `acceptance.sh` `audit_tests` runs exactly those 11 ids. Plan G4 and the “After the run” section apply the same command to juans 67–72. Premise P6 says juan 67 has glossary failures and no missing lines.  
   Why it matters: the 112 branch tests never call `audit`. An `audit` implementation that only handles 7, 72, and 76, writes the K7 report, and calls the real sealer can make `acceptance.sh` exit 0 while juan 67 still does not follow K5.  
   Suggested fix:

```bash
juan_67_is_not_a_fixture_special_case() {
  python3 -B -c '
import json, shutil, tempfile
from pathlib import Path
import tests.test_legacy_audit_acceptance as t
root = Path(tempfile.mkdtemp())
tree = root / "repo"
t.build_template(root / "template")
shutil.copytree(root / "template", tree)
shutil.copy2(t.REPO / "data" / "T1579-067.json", tree / "data")
before = t.digest(tree, "docs", "translations")
done = t.audit(tree, "--juans", "67", "--model", "echo", "--no-push")
assert done.returncode == 1, done.stderr[-400:]
reports = sorted((tree / "audits").glob("*/T1579-067-audit-report.json"))
assert len(reports) == 1, reports
report = json.loads(reports[0].read_text(encoding="utf-8"))
assert report["verdict"] == "fail"
assert any("lacks one of" in line for line in report["blocking"]), report["blocking"]
assert before == t.digest(tree, "docs", "translations")
'
}
check "M4: juan 67 fails closed with the real glossary message" juan_67_is_not_a_fixture_special_case
```

I could not execute `acceptance.sh` here (the shell is blocked in this read-only pass). From the current tree, the checks that require `audited.py`, the merge, `ci.yml`, and the new `check_ranges` parameters fail before any of this work, which matches the plan’s red column for those rows.

## Questions for the plan author
None.
