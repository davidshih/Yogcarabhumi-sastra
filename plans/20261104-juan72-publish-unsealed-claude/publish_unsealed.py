"""Publish the juan 72 rewrite of audit job 20261104-193950-audit-744f without a seal (owner decision 2026-11-04).

The audit failed with 22 blocking findings. The owner chose to publish the rewrite as it is and fix the findings
later. This script takes the steps of the audit's pass path except the reviews and the seal:

1. The live md must still be the text the audit started from (candidate_base_sha256 of the job).
2. assign_paragraph_ids gives the new paragraphs of the rewrite an id; no id of the live md may be lost.
3. run_content_checks: line ranges of the juan (each line at least once) and the glossary check.
4. With --install: write the md, build its page in the published (legacy) term style, rebuild the search indexes.

Run from the repository root:
    python3 -B plans/20261104-juan72-publish-unsealed-claude/publish_unsealed.py            # checks only
    python3 -B plans/20261104-juan72-publish-unsealed-claude/publish_unsealed.py --install  # checks, then writes
"""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import audited  # noqa: E402
import build_translation_html as bth  # noqa: E402

JOB_ID = "20261104-193950-audit-744f"
WORK, JUAN = "T1579", 72


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    install = "--install" in sys.argv[1:]
    paths = audited.juan_paths(WORK, JUAN)
    md_path, data_path = paths["md"], paths["data"]
    candidate = ROOT / "audits" / JOB_ID / "candidate" / md_path.name
    job = json.loads((ROOT / "jobs" / "audited" / f"{JOB_ID}.json").read_text(encoding="utf-8"))
    base_sha = job["progress"][str(JUAN)]["candidate_base_sha256"]
    if sha256(md_path) != base_sha:
        raise SystemExit(f"{md_path.name} changed after the audit started: stop")

    marked = bth.assign_paragraph_ids(candidate.read_text(encoding="utf-8"))
    live_ids = [pid for entry in bth.parse_entries(md_path.read_text(encoding="utf-8")) for pid in entry.paragraph_ids]
    new_ids = [pid for entry in bth.parse_entries(marked) for pid in entry.paragraph_ids]
    lost = [pid for pid in live_ids if pid not in new_ids]
    if lost:
        raise SystemExit(f"paragraph ids would be lost: {', '.join(lost)}")

    staged = ROOT / "audits" / JOB_ID / "candidate" / f"{md_path.stem}.publish-check.md"
    staged.write_text(marked, encoding="utf-8")
    try:
        start, end = audited.juan_bounds(audited.extract_lines(data_path))
        issues = audited.run_content_checks(staged, data_path, start, end, exact_once=False)
    finally:
        staged.unlink()
    print(f"paragraph ids: {len(live_ids)} live, {len(new_ids)} after, 0 lost")
    print(f"content checks: {len(issues)} issues")
    for issue in issues:
        print(f"  {issue}")
    if issues:
        return 1
    if not install:
        print("checks only; run again with --install to publish")
        return 0

    audited.atomic_write(md_path, marked)
    page = audited.build_legacy_page(md_path)
    audited.rebuild_search_indexes()
    print(f"installed {md_path.relative_to(ROOT)} (sha256 {sha256(md_path)[:12]}), page {page.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
