#!/usr/bin/env python3
"""Check translation source ranges against expected CBETA coverage."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LINE_RE = re.compile(r"([A-Z]+\d+n\d+[A-Za-z]?_p(\d{4}[abc]\d{2}))")
RANGE_RE = re.compile(
    r"(?P<start>(?P<prefix>[A-Z]+\d+n\d+[A-Za-z]?)_p(?P<start_label>\d{4}[abc]\d{2}))"
    r"(?:-(?:(?P=prefix)_)?p?(?P<end_label>\d{4}[abc]\d{2}))?"
)


@dataclass
class RangeEntry:
    title: str
    start: str
    end: str


def line_key(line_id: str) -> tuple[int, int, int]:
    match = LINE_RE.fullmatch(line_id)
    if not match:
        raise ValueError(f"Invalid line id: {line_id}")
    label = match.group(2)
    return int(label[:4]), {"a": 0, "b": 1, "c": 2}[label[4]], int(label[5:])


def parse_ranges(text: str) -> list[RangeEntry]:
    entries: list[RangeEntry] = []
    parts = re.split(r"^##\s+", text, flags=re.MULTILINE)
    for part in parts[1:]:
        lines = part.splitlines()
        title = lines[0].strip()
        range_match = re.search(r"^Range:\s*(.+)$", part, flags=re.MULTILINE)
        if not range_match:
            raise ValueError(f"Missing Range in entry: {title}")
        label = range_match.group(1).strip()
        parsed = RANGE_RE.fullmatch(label)
        if not parsed:
            raise ValueError(f"Invalid Range in {title}: {label}")
        start = parsed.group("start")
        end = f"{parsed.group('prefix')}_p{parsed.group('end_label')}" if parsed.group("end_label") else start
        entries.append(RangeEntry(title=title, start=start, end=end))
    return entries


def data_line_ids(data_path: Path) -> set[str]:
    payload = json.loads(data_path.read_text(encoding="utf-8"))
    raw = payload["results"][0]
    return {match.group(1) for match in LINE_RE.finditer(raw)}


def check_ranges(entries: list[RangeEntry], line_ids: set[str], expected_start: str, expected_end: str) -> list[str]:
    issues: list[str] = []
    if not entries:
        return ["Translation source has no entries"]

    if entries[0].start != expected_start:
        issues.append(f"first range starts at {entries[0].start}, expected {expected_start}")
    if entries[-1].end != expected_end:
        issues.append(f"last range ends at {entries[-1].end}, expected {expected_end}")

    previous_start_key: tuple[int, int, int] | None = None
    for entry in entries:
        start_key = line_key(entry.start)
        end_key = line_key(entry.end)
        if end_key < start_key:
            issues.append(f"{entry.title}: range end precedes start")
        if previous_start_key is not None and start_key < previous_start_key:
            issues.append(f"{entry.title}: ranges are not monotonic")
        previous_start_key = start_key
        for line_id in (entry.start, entry.end):
            if line_id not in line_ids:
                issues.append(f"{entry.title}: {line_id} is not present in data file")
        if start_key < line_key(expected_start) or end_key > line_key(expected_end):
            issues.append(f"{entry.title}: range is outside expected coverage")
    return issues


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--translation", type=Path, required=True)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    args = parser.parse_args()

    translation = args.translation if args.translation.is_absolute() else ROOT / args.translation
    data = args.data if args.data.is_absolute() else ROOT / args.data
    entries = parse_ranges(translation.read_text(encoding="utf-8"))
    issues = check_ranges(entries, data_line_ids(data), args.start, args.end)
    if issues:
        for issue in issues:
            print(f"coverage-check: {issue}", file=sys.stderr)
        return 1
    print(f"Checked {len(entries)} ranges from {entries[0].start} to {entries[-1].end}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
