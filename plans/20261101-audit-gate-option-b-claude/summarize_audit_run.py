"""Summarize one juan of an audit job under the option b gate: verdict, blocking and backlog counts, calls, cost.

Usage, from the repository root:
    python3 -B plans/20261101-audit-gate-option-b-claude/summarize_audit_run.py <job id> [juan] [-v]
It reads the stored answers, so it calls no model. It prints the counts of the audit report next to its own
recount; for a job of gate option b (3ab4f56 or later) the two must be equal.
"""
import collections, datetime, gzip, json, sys
from pathlib import Path

ROOT = Path.cwd()
sys.path.insert(0, str(ROOT / "scripts"))
import audited  # noqa: E402

job_id = sys.argv[1]
juan = int(sys.argv[2]) if len(sys.argv) > 2 else 72
JOB = ROOT / "audits" / job_id
report = json.loads((JOB / f"T1579-{juan:03d}-audit-report.json").read_text(encoding="utf-8"))
events = [json.loads(line) for line in (JOB / "events.jsonl").read_text(encoding="utf-8").splitlines()]
calls = [e for e in events if e.get("type") == "llm_attempt"]
by_id = {e["event_id"]: e for e in calls}
answers = {}
for e in events:
    if e.get("type") == "review_contract_validation" and e.get("validator_error") is None:
        call = by_id[e["llm_event_id"]]
        answers[(e["stage"], e["unit_id"])] = json.loads(
            gzip.decompress((ROOT / call["artifacts"]["response"]["path"]).read_bytes()))
blocking, backlog, all_terms_categories = [], [], collections.Counter()
for (stage, unit), answer in sorted(answers.items()):
    for f in answer["findings"]:
        item = dict(f, unit_id=unit, review_stage=stage)
        if stage == "review_terms":
            all_terms_categories[f["category"]] += 1
        (blocking if audited.finding_blocks(item) else backlog).append(item)
check = [line for line in report["blocking"] if not line.startswith(f"T1579-{juan:03d}-")]
t0 = min(datetime.datetime.fromisoformat(e["sent_at"]) for e in calls)
t1 = max(datetime.datetime.fromisoformat(e["received_at"]) for e in calls)
validations = [e for e in events if e.get("type", "").endswith("_contract_validation")]
summary = {
    "job_id": job_id, "verdict": report["verdict"], "install": report["install"],
    "attestation": report.get("attestation"), "findings_rounds": report["findings"],
    "report_blocking": len(report["blocking"]), "report_backlog": len(report.get("glossary_backlog", [])),
    "recomputed_blocking": len(blocking) + len(check), "recomputed_backlog": len(backlog),
    "blocking_by_stage": dict(collections.Counter(f["review_stage"] for f in blocking), term_check=len(check)),
    "blocking_review_terms_by_category": dict(collections.Counter(
        f["category"] for f in blocking if f["review_stage"] == "review_terms")),
    "blocking_by_severity": dict(collections.Counter(f["severity"] for f in blocking)),
    "doctrine_by_severity": dict(collections.Counter(
        f["severity"] for f in blocking if f["review_stage"] == "review_doctrine")),
    "backlog_by_category": dict(collections.Counter(f["category"] for f in backlog)),
    "review_terms_categories_outside_enum": sorted(
        c for c in all_terms_categories if c not in audited.REVIEW_TERMS_CATEGORIES),
    "calls": len(calls), "calls_by_stage": dict(collections.Counter(e["stage"] for e in calls)),
    "contracts_valid": f"{sum(1 for e in validations if e.get('validator_error') is None)}/{len(validations)}",
    "minutes": round((t1 - t0).total_seconds() / 60, 1),
    "list_usd": round(sum((e.get("usage") or {}).get("cost_usd") or 0 for e in calls), 2),
    "rate_limited_calls": sum(1 for e in calls if e.get("rate_limit")),
    "rewritten_units": report.get("rewritten_units"), "warnings": report.get("warnings"),
}
print(json.dumps(summary, ensure_ascii=False, indent=1))
if "-v" in sys.argv:
    for f in blocking:
        print("BLOCK", f["unit_id"], f["review_stage"], f["category"], f["severity"], "|", f["claim"][:160])
    for line in check:
        print("BLOCK term_check |", line)
