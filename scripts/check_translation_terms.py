#!/usr/bin/env python3
"""Check translation glossary shape and basic term coverage."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GLOSSARY = ROOT / "translations" / "glossary" / "T1579-terms.json"
DEFAULT_TRANSLATION = ROOT / "translations" / "T1579-033-baihua.md"

REQUIRED_TERM_FIELDS = {
    "id",
    "xuanzang",
    "display",
    "plain",
    "source_terms",
    "translation_patterns",
    "status",
    "rule",
}


@dataclass
class Entry:
    title: str
    source: str
    translation: str


def parse_entries(text: str) -> list[Entry]:
    entries: list[Entry] = []
    parts = re.split(r"^##\s+", text, flags=re.MULTILINE)
    for part in parts[1:]:
        lines = part.splitlines()
        title = lines[0].strip()
        body = "\n".join(lines[1:])
        source_match = re.search(r"Source:\n<<<\n(.*?)\n>>>", body, flags=re.DOTALL)
        translation_match = re.search(r"Translation:\n<<<\n(.*?)\n>>>", body, flags=re.DOTALL)
        if not (source_match and translation_match):
            raise ValueError(f"Invalid translation entry: {title}")
        entries.append(
            Entry(
                title=title,
                source=source_match.group(1).strip(),
                translation=translation_match.group(1).strip(),
            )
        )
    return entries


def require_list(term: dict[str, Any], field: str) -> list[str]:
    value = term.get(field)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{term.get('id', '<unknown>')} field {field} must be a list of strings")
    return value


def validate_glossary(glossary: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    terms = glossary.get("terms")
    if not isinstance(terms, list) or not terms:
        return ["Glossary must contain a non-empty terms list"]

    seen_ids: set[str] = set()
    for term in terms:
        if not isinstance(term, dict):
            issues.append("Each term must be an object")
            continue
        missing = sorted(REQUIRED_TERM_FIELDS.difference(term))
        if missing:
            issues.append(f"{term.get('id', '<unknown>')} missing fields: {', '.join(missing)}")
            continue
        term_id = str(term["id"])
        if term_id in seen_ids:
            issues.append(f"Duplicate term id: {term_id}")
        seen_ids.add(term_id)
        for field in ("source_terms", "translation_patterns"):
            try:
                values = require_list(term, field)
            except ValueError as error:
                issues.append(str(error))
                continue
            if not values:
                issues.append(f"{term_id} field {field} must not be empty")
    return issues


def check_translation_terms(glossary: dict[str, Any], entries: list[Entry]) -> list[str]:
    issues: list[str] = []
    terms = glossary.get("terms", [])
    for entry in entries:
        for term in terms:
            source_terms = require_list(term, "source_terms")
            patterns = require_list(term, "translation_patterns")
            if any(source_term in entry.source for source_term in source_terms):
                if not any(pattern in entry.translation for pattern in patterns):
                    issues.append(
                        f"{entry.title}: source has {term['xuanzang']} but translation lacks "
                        f"one of {', '.join(patterns)}"
                    )
    return issues


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--glossary", type=Path, default=DEFAULT_GLOSSARY)
    parser.add_argument("--translation", type=Path, default=DEFAULT_TRANSLATION)
    args = parser.parse_args()

    glossary = json.loads(args.glossary.read_text(encoding="utf-8"))
    entries = parse_entries(args.translation.read_text(encoding="utf-8"))

    issues = validate_glossary(glossary)
    if not issues:
        issues.extend(check_translation_terms(glossary, entries))

    if issues:
        for issue in issues:
            print(f"term-check: {issue}", file=sys.stderr)
        return 1

    print(f"Checked {len(glossary['terms'])} glossary terms against {len(entries)} entries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
