#!/usr/bin/env python3
"""Check translation source ranges against expected CBETA coverage."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from build_translation_html import PARA_MARKER_LINE_RE
from make_translation_skeleton import extract_lines


ROOT = Path(__file__).resolve().parents[1]
LINE_RE = re.compile(r"([A-Z]+\d+n\d+[A-Za-z]?_p(\d{4}[abc]\d{2}))")
RANGE_RE = re.compile(
    r"(?P<start>(?P<prefix>[A-Z]+\d+n\d+[A-Za-z]?)_p(?P<start_label>\d{4}[abc]\d{2}))"
    r"(?:-(?:(?P=prefix)_)?p?(?P<end_label>\d{4}[abc]\d{2}))?"
)
IGNORABLE_SOURCE_MARKERS = ("[＊]",)


@dataclass
class RangeEntry:
    title: str
    start: str
    end: str
    source: str | None = None


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
        source_match = re.search(r"Source:\n<<<\n(.*?)\n>>>", part, flags=re.DOTALL)
        source = source_match.group(1).strip() if source_match else None
        entries.append(RangeEntry(title=title, start=start, end=end, source=source))
    return entries


def data_line_ids(data_path: Path) -> set[str]:
    payload = json.loads(data_path.read_text(encoding="utf-8"))
    raw = payload["results"][0]
    return {match.group(1) for match in LINE_RE.finditer(raw)}


def line_prefix(line_id: str) -> str:
    """`T30n1579` of `T30n1579_p0001a01`."""
    return line_id.split("_p", 1)[0]


def full_line_id(line_id: str, prefix: str | None = None) -> str:
    """A full line id as it is; a bare label (`0001a01`) joined to `prefix` when one is given."""
    if LINE_RE.fullmatch(line_id):
        return line_id
    if prefix is not None:
        candidate = f"{prefix}_p{line_id}"
        if LINE_RE.fullmatch(candidate):
            return candidate
    raise ValueError(f"Invalid line id: {line_id}")


def ordered_line_ids(line_ids: set[str], prefix: str | None = None) -> list[str]:
    return sorted((full_line_id(line_id, prefix) for line_id in line_ids), key=line_key)


def lines_in_range(line_ids: list[str], start: str, end: str) -> list[str]:
    start_key = line_key(start)
    end_key = line_key(end)
    return [line_id for line_id in line_ids if start_key <= line_key(line_id) <= end_key]


def canonical_source(source_lines: Mapping[str, str], line_ids: list[str]) -> str:
    return "\n".join(source_lines.get(line_id, "") for line_id in line_ids if source_lines.get(line_id, ""))


def normalize_source_text(text: str) -> str:
    """Ignore CBETA layout whitespace and an explicit allowlist of apparatus markers."""
    normalized = text
    for marker in IGNORABLE_SOURCE_MARKERS:
        normalized = normalized.replace(marker, "")
    return re.sub(r"\s+", "", normalized)


def translation_content(text: str) -> str:
    """Return translated prose, excluding source and note blocks when using Markdown.

    Paragraph id marker lines are dropped the way build_translation_html.parse_entries drops them, so a
    quote that spans two paragraphs is still found in the prose.
    """
    blocks = re.findall(r"Translation:\n<<<\n(.*?)\n>>>", text, flags=re.DOTALL)
    return "\n".join(PARA_MARKER_LINE_RE.sub("", block).strip() for block in blocks) if blocks else text


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def summarize_line_ids(line_ids: list[str], limit: int = 8) -> str:
    labels = [line_id.split("_p", 1)[-1] for line_id in line_ids]
    if len(labels) <= limit:
        return ", ".join(labels)
    return f"{', '.join(labels[:limit])}, ... ({len(labels)} total)"


def check_exact_once(
    covered_line_ids: list[str], expected_line_ids: list[str], label: str, *, exact_once: bool = True
) -> list[str]:
    """Every expected line is covered; with exact_once, no line is covered twice."""
    issues: list[str] = []
    counts = Counter(covered_line_ids)
    missing = [line_id for line_id in expected_line_ids if counts[line_id] == 0]
    overlap = [line_id for line_id in expected_line_ids if counts[line_id] > 1]
    if missing:
        issues.append(f"{label} missing lines: {summarize_line_ids(missing)}")
    if overlap and exact_once:
        issues.append(f"{label} overlapping lines: {summarize_line_ids(overlap)}")
    return issues


def _check_range_bounds(entries: list[RangeEntry], line_ids: set[str], expected_start: str,
                        expected_end: str) -> list[str]:
    """The checks of the production runner: bounds, order and membership of each range."""
    issues: list[str] = []
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


def check_ranges(
    entries: list[RangeEntry],
    line_ids: set[str],
    expected_start: str,
    expected_end: str,
    source_lines: Mapping[str, str] | None = None,
    *,
    exact_once: bool = True,
) -> list[str]:
    """Check the ranges of a translation against the data file.

    Without source_lines this is the check of the production runner (scripts/runner.py): bounds, order
    and membership only. With source_lines every Source block must equal its data range and no line may
    be missing; exact_once also rejects a line that two ranges cover (a legacy audit passes False).
    """
    if not entries:
        return ["Translation source has no entries"]
    if source_lines is None:
        return _check_range_bounds(entries, line_ids, expected_start, expected_end)

    ordered_ids = ordered_line_ids(line_ids, line_prefix(expected_start))
    expected_ids = lines_in_range(ordered_ids, expected_start, expected_end)
    if not expected_ids:
        return [f"data file has no lines in expected coverage {expected_start}-{expected_end}"]

    issues = _check_range_bounds(entries, set(ordered_ids), expected_start, expected_end)
    covered_ids: list[str] = []
    for entry in entries:
        if line_key(entry.start) < line_key(expected_start) or line_key(entry.end) > line_key(expected_end):
            continue  # reported above; its lines do not count as covered
        entry_ids = lines_in_range(expected_ids, entry.start, entry.end)
        covered_ids.extend(entry_ids)
        if entry.source is None:
            issues.append(f"{entry.title}: missing Source block")
        else:
            expected_source = canonical_source(source_lines, entry_ids)
            if normalize_source_text(entry.source) != normalize_source_text(expected_source):
                issues.append(f"{entry.title}: Source block does not match data range")

    issues.extend(check_exact_once(covered_ids, expected_ids, "translation ranges", exact_once=exact_once))
    return issues


def range_overlaps(entries: list[RangeEntry], line_ids: set[str], expected_start: str,
                   expected_end: str) -> list[str]:
    """The full line ids that more than one range covers, in line order."""
    ordered_ids = ordered_line_ids(line_ids, line_prefix(expected_start))
    expected_ids = lines_in_range(ordered_ids, expected_start, expected_end)
    counts: Counter[str] = Counter()
    for entry in entries:
        counts.update(lines_in_range(expected_ids, entry.start, entry.end))
    return [line_id for line_id in expected_ids if counts[line_id] > 1]


def check_coverage_ledger(
    ledger: Mapping[str, Any],
    source_lines: Mapping[str, str],
    expected_start: str,
    expected_end: str,
    translation_text: str,
    *,
    exact_once: bool | None = None,
) -> list[str]:
    """Verify the persisted clause ledger against source and translation bytes.

    exact_once=None follows the ledger: `"coverage": "at_least_once"` (a legacy audit) accepts a line
    that two clauses cover; anything else, also a ledger without the key, is exact-once.
    """
    if exact_once is None:
        exact_once = ledger.get("coverage") != "at_least_once"
    issues: list[str] = []
    if ledger.get("schema_version") != "1.0":
        issues.append("ledger schema_version must be 1.0")
    if not isinstance(ledger.get("work"), str) or not ledger["work"]:
        issues.append("ledger work must be a non-empty string")
    if not isinstance(ledger.get("juan"), int) or ledger["juan"] < 1:
        issues.append("ledger juan must be a positive integer")

    prefix = line_prefix(expected_start)
    expected_ids = lines_in_range(ordered_line_ids(set(source_lines), prefix), expected_start, expected_end)
    if not expected_ids:
        return issues + [f"source data has no lines in expected coverage {expected_start}-{expected_end}"]

    expected_source_hash = sha256_text(canonical_source(source_lines, expected_ids))
    if ledger.get("source_hash") != expected_source_hash:
        issues.append("ledger source_hash does not match expected source")
    if ledger.get("translation_hash") != sha256_text(translation_text):
        issues.append("ledger translation_hash does not match translation file")
    if ledger.get("passed") is not True:
        issues.append("ledger passed must be true")
    if ledger.get("issues") != []:
        issues.append("ledger issues must be an empty list")

    clauses = ledger.get("clauses")
    if not isinstance(clauses, list) or not clauses:
        return issues + ["ledger clauses must be a non-empty list"]

    seen_clause_ids: set[str] = set()
    covered_ids: list[str] = []
    expected_id_set = set(expected_ids)
    translated_prose = translation_content(translation_text)
    for index, clause in enumerate(clauses, 1):
        label = f"ledger clause {index}"
        if not isinstance(clause, dict):
            issues.append(f"{label} must be an object")
            continue
        clause_id = clause.get("clause_id")
        if not isinstance(clause_id, str) or not clause_id:
            issues.append(f"{label} clause_id must be a non-empty string")
        elif clause_id in seen_clause_ids:
            issues.append(f"duplicate ledger clause_id: {clause_id}")
        else:
            seen_clause_ids.add(clause_id)
            label = f"ledger clause {clause_id}"

        raw_ids = clause.get("source_line_ids")
        if not isinstance(raw_ids, list) or not raw_ids or not all(isinstance(item, str) for item in raw_ids):
            issues.append(f"{label} source_line_ids must be a non-empty list of strings")
            continue
        try:
            clause_ids = [full_line_id(line_id, prefix) for line_id in raw_ids]
        except ValueError as error:
            issues.append(f"{label}: {error}")
            continue
        unknown = [line_id for line_id in clause_ids if line_id not in expected_id_set]
        if unknown:
            issues.append(f"{label} has lines outside expected coverage: {summarize_line_ids(unknown)}")
            continue
        if clause_ids != sorted(clause_ids, key=line_key):
            issues.append(f"{label} source_line_ids are not ordered")
        covered_ids.extend(clause_ids)

        expected_clause_hash = sha256_text(canonical_source(source_lines, clause_ids))
        if clause.get("source_hash") != expected_clause_hash:
            issues.append(f"{label} source_hash does not match source lines")
        if clause.get("status") != "covered":
            issues.append(f"{label} status must be covered")
        evidence = clause.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            issues.append(f"{label} evidence must be a non-empty list")
            continue
        clause_source = canonical_source(source_lines, clause_ids)
        normalized_clause_source = normalize_source_text(clause_source)
        for evidence_index, item in enumerate(evidence, 1):
            evidence_label = f"{label} evidence {evidence_index}"
            if not isinstance(item, dict):
                issues.append(f"{evidence_label} must be an object")
                continue
            source_quote = item.get("source_quote")
            translation_quote = item.get("translation_quote")
            if not isinstance(source_quote, str) or not source_quote.strip():
                issues.append(f"{evidence_label} source_quote must be a non-empty string")
            else:
                normalized_quote = normalize_source_text(source_quote)
                if not normalized_quote or normalized_quote not in normalized_clause_source:
                    issues.append(f"{evidence_label} source_quote is not in clause source")
            if not isinstance(translation_quote, str) or not translation_quote.strip():
                issues.append(f"{evidence_label} translation_quote must be a non-empty string")
            elif translation_quote not in translated_prose:
                issues.append(f"{evidence_label} translation_quote is not in translated content")

    issues.extend(check_exact_once(covered_ids, expected_ids, "ledger clauses", exact_once=exact_once))
    return issues


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--translation", type=Path, required=True)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--ledger", type=Path, help="Validate the production clause ledger")
    mode.add_argument(
        "--ranges-only",
        action="store_true",
        help="Diagnostic range/source checks only; not a production quality pass",
    )
    args = parser.parse_args()

    translation = args.translation if args.translation.is_absolute() else ROOT / args.translation
    data = args.data if args.data.is_absolute() else ROOT / args.data
    translation_text = translation.read_text(encoding="utf-8")
    entries = parse_ranges(translation_text)
    source_lines = extract_lines(data)
    ledger = None
    exact_once = True
    if args.ledger:
        # Read the ledger first: the ranges are checked in the coverage mode that it declares.
        ledger_path = args.ledger if args.ledger.is_absolute() else ROOT / args.ledger
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        exact_once = not (isinstance(ledger, dict) and ledger.get("coverage") == "at_least_once")
    issues = check_ranges(entries, data_line_ids(data), args.start, args.end, source_lines,
                          exact_once=exact_once)
    if ledger is not None:
        issues.extend(check_coverage_ledger(ledger, source_lines, args.start, args.end, translation_text))
    if issues:
        for issue in issues:
            print(f"coverage-check: {issue}", file=sys.stderr)
        return 1
    if args.ranges_only:
        print(
            f"RANGES-ONLY diagnostic: checked {len(entries)} ranges from "
            f"{entries[0].start} to {entries[-1].end}; clause quality was not validated"
        )
    else:
        print(
            f"Checked {len(entries)} ranges and clause ledger from "
            f"{entries[0].start} to {entries[-1].end}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
