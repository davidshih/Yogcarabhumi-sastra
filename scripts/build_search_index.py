#!/usr/bin/env python3
"""Emit docs/T1579/search.json: one record per translated section + per outline section."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_translation_html as bth

OUT = ROOT / "docs" / "T1579" / "search.json"


def main() -> int:
    records = []
    for md in sorted((ROOT / "translations").glob("T1579-*-baihua.md")):
        juan = bth.infer_juan(md)
        for entry in bth.parse_entries(md.read_text(encoding="utf-8")):
            start, _ = bth.parse_range(entry.range_label)
            records.append({
                "t": entry.title, "j": juan,
                "u": f"translations/{md.stem}.html#{start}",
                # translation + source both searchable; substring match client-side
                "x": entry.translation + "\n" + entry.source,
            })
    index_html = (ROOT / "docs" / "T1579" / "index.html").read_text(encoding="utf-8")
    for m in re.finditer(r"data-juan='(\d+)'><a href='(sections/[^']+)'>([^<]+)</a>", index_html):
        records.append({"t": m.group(3), "j": int(m.group(1)), "u": m.group(2), "x": ""})
    OUT.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {OUT} ({len(records)} records)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
