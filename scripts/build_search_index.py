#!/usr/bin/env python3
"""Emit per-work search.json files for translated sections."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_translation_html as bth

def main() -> int:
    by_work: dict[str, list[dict]] = {}
    for md in sorted((ROOT / "translations").glob("*-*-baihua.md")):
        work, juan = bth.infer_work_juan(md)
        records = by_work.setdefault(work, [])
        for entry in bth.parse_entries(md.read_text(encoding="utf-8")):
            start, _ = bth.parse_range(entry.range_label)
            records.append({
                "t": entry.title, "j": juan,
                "u": f"translations/{md.stem}.html#{start}",
                # translation + source both searchable; substring match client-side
                "x": entry.translation + "\n" + entry.source,
            })
    t1579_index = ROOT / "docs" / "T1579" / "index.html"
    if t1579_index.exists():
        records = by_work.setdefault("T1579", [])
        index_html = t1579_index.read_text(encoding="utf-8")
        for m in re.finditer(r"data-juan='(\d+)'><a href='(sections/[^']+)'>([^<]+)</a>", index_html):
            records.append({"t": m.group(3), "j": int(m.group(1)), "u": m.group(2), "x": ""})
    for work, records in sorted(by_work.items()):
        out = ROOT / "docs" / work / "search.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
        print(f"Wrote {out} ({len(records)} records)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
