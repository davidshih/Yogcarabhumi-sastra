"""Judge juan 72 again under the gate of option b, from the stored answers of run 20261022-083631-audit-1e67.

No model is called. The run used free-text categories, so each review_terms finding gets one of the five
categories of the new schema first: LEGACY_CATEGORIES maps a free-text label, and MIXED names the findings whose
required_change asks for a change of the translation or its 〔〕 although their label is about a binding. Then
audited.finding_blocks gives the verdict. Run it from the repository root:

    python3 -B plans/20261101-audit-gate-option-b-claude/rejudge_juan72.py
"""
import collections
import gzip
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import audited  # noqa: E402

JOB = ROOT / "audits" / "20261022-083631-audit-1e67"
OUT = Path(__file__).with_name("juan72-rejudge-claude.json")

LEGACY_CATEGORIES = {
    "first_occurrence_format": "first_occurrence_format",
    "glossary_plain_consistency": "plain_wording",
    "glossary_provenance": "glossary_provenance",
    "term_id_provenance": "glossary_provenance",
    "primary_term_id": "term_binding",
    "primary_term_id_missing": "term_binding",
    "missing_term_occurrence": "term_binding",
    "missing_occurrence": "term_binding",
    "term_binding_consistency": "term_binding",
    "occurrence_anchoring": "term_binding",
    "term_occurrence_coverage": "term_binding",
    "term_id_missing_for_glossed_terms": "term_binding",
    "occurrence_disambiguation": "term_binding",
    "term_surface_mismatch": "term_binding",
    "occurrence_granularity": "term_binding",
    "proposed_term_definition": "term_binding",
}
# (unit, start of the claim) -> category. The required_change of each asks to add or change a 〔〕 gloss.
MIXED = {
    ("T1579-072-003", "「自性、差別」"): "first_occurrence_format",
    ("T1579-072-003", "「行」（三界行"): "first_occurrence_format",
    ("T1579-072-005", "glossary stable 條目 dhyana"): "plain_wording",
    ("T1579-072-006", "「清淨善巧所緣」"): "first_occurrence_format",
    ("T1579-072-007", "有幾個術語沒有任何 occurrence"): "first_occurrence_format",
}


def last_answers() -> dict[tuple[str, str], dict]:
    """The last answer of each review and unit that passed its contract check, in the order of the events."""
    events = [json.loads(line) for line in (JOB / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    calls = {event["event_id"]: event for event in events if event.get("type") == "llm_attempt"}
    answers = {}
    for event in events:
        if event.get("type") != "review_contract_validation" or event.get("validator_error") is not None:
            continue
        call = calls[event["llm_event_id"]]
        answer = json.loads(gzip.decompress((ROOT / call["artifacts"]["response"]["path"]).read_bytes()))
        answers[(event["stage"], event["unit_id"])] = answer
    return answers


def relabel(unit: str, finding: dict) -> tuple[str, str]:
    for (mixed_unit, start), category in MIXED.items():
        if unit == mixed_unit and finding["claim"].startswith(start):
            return category, "mixed: the required change edits the translation"
    category = finding["category"]
    return LEGACY_CATEGORIES.get(category, category), "label"


def main() -> int:
    report = json.loads((JOB / "T1579-072-audit-report.json").read_text(encoding="utf-8"))
    findings = []
    for (stage, unit), answer in sorted(last_answers().items()):
        for finding in answer["findings"]:
            item = {"unit_id": unit, "review_stage": stage, "severity": finding["severity"],
                    "legacy_category": finding["category"], "claim": finding["claim"],
                    "required_change": finding["required_change"]}
            item["category"], item["relabel"] = (relabel(unit, finding) if stage == "review_terms"
                                                 else (finding["category"], "unchanged"))
            findings.append(item)
    # The glossary check is mechanical: its findings are the report lines that do not start with a unit id.
    for line in report["blocking"]:
        if not line.startswith("T1579-072-"):
            findings.append({"unit_id": None, "review_stage": audited.TERM_CHECK_STAGE, "severity": "high",
                             "legacy_category": "glossary_term", "category": "glossary_term", "claim": line,
                             "required_change": line, "relabel": "unchanged"})
    stages = collections.Counter(item["review_stage"] for item in findings)
    # The stored answers must give back exactly the 47 findings of the report: 39 terms, 5 doctrine, 3 check.
    expected = {"review_terms": 39, "review_doctrine": 5, audited.TERM_CHECK_STAGE: 3}
    if dict(stages) != expected or len(findings) != report["findings"]["remaining"]:
        raise SystemExit(f"the stored answers do not match the report: {dict(stages)}")
    unknown = sorted({item["legacy_category"] for item in findings if item["review_stage"] == "review_terms"
                      and item["category"] not in audited.REVIEW_TERMS_CATEGORIES})
    blocking = [item for item in findings if audited.finding_blocks(item)]
    backlog = [item for item in findings if not audited.finding_blocks(item)]
    result = {
        "schema_version": "1.0",
        "job_id": report["job_id"],
        "juan": 72,
        "gate": "option b (owner decision 2026-11-01)",
        "model_calls": 0,
        "verdict": "fail" if blocking else "pass",
        "counts": {
            "findings": len(findings),
            "blocking": len(blocking),
            "glossary_backlog": len(backlog),
            "blocking_by_stage": dict(collections.Counter(item["review_stage"] for item in blocking)),
            "blocking_by_category": dict(collections.Counter(item["category"] for item in blocking)),
            "backlog_by_category": dict(collections.Counter(item["category"] for item in backlog)),
            "relabeled_as_mixed": sum(1 for item in findings if item["relabel"].startswith("mixed")),
        },
        "unmapped_legacy_categories": unknown,
        "blocking": blocking,
        "glossary_backlog": backlog,
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("verdict", "counts", "unmapped_legacy_categories")},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
