#!/usr/bin/env python3
"""Check translation glossary shape and basic term coverage."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from urllib.parse import urlsplit
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


# Codex contribution: share sense selection between checks and model inputs.
def source_term_matches(term: dict[str, Any], source: str) -> bool:
    """Ignore a sense only inside an explicitly documented longer expression."""
    text = re.sub(r"\s+", "", source)
    excluded = [match.span() for phrase in term.get("source_exclusions", [])
                for match in re.finditer(re.escape(re.sub(r"\s+", "", phrase)), text)]
    for word in term.get("source_terms", []):
        for match in re.finditer(re.escape(re.sub(r"\s+", "", word)), text):
            if not any(start <= match.start() and match.end() <= end for start, end in excluded):
                return True
    return False


# Codex contribution: evidence is per language and candidate, not inherited from stable.
def validate_language_evidence(term: dict[str, Any], required: bool = False) -> list[str]:
    metadata = term.get("language_evidence")
    if metadata is None and not required:
        return []  # Legacy terms remain readable as unresolved candidates.
    prefix = str(term.get("id", "<unknown>"))
    if not isinstance(metadata, dict):
        return [f"{prefix}: language_evidence must be an object"]
    issues = []
    for language in ("chinese", "sanskrit", "pali"):
        forms = [term.get("xuanzang")] if language == "chinese" else term.get(language, [])
        records = metadata.get(language)
        if not isinstance(records, list):
            issues.append(f"{prefix}: {language} evidence must be a list")
            continue
        if not isinstance(forms, list) or not all(isinstance(form, str) and form.strip() for form in forms):
            issues.append(f"{prefix}: {language} forms must be nonempty strings")
            continue
        if [record.get("form") if isinstance(record, dict) else None for record in records] != forms:
            issues.append(f"{prefix}: {language} evidence forms must match the candidate list")
        for record in records:
            if not isinstance(record, dict):
                issues.append(f"{prefix}: invalid {language} record")
                continue
            status = record.get("status")
            if not isinstance(status, str) or status not in {"attested", "inferred", "comparative", "unresolved"}:
                issues.append(f"{prefix}: invalid {language} evidence status")
            evidence = record.get("evidence")
            if not isinstance(evidence, list):
                issues.append(f"{prefix}: {language} evidence must be a list")
                continue
            if status != "unresolved" and not evidence:
                issues.append(f"{prefix}: {language} {status} needs source evidence")
            if status == "attested":
                try:
                    date.fromisoformat(record.get("reviewed_at", ""))
                except (TypeError, ValueError):
                    issues.append(f"{prefix}: attested evidence needs a review date")
                if not isinstance(record.get("reviewed_by"), str) or not record["reviewed_by"].strip():
                    issues.append(f"{prefix}: attested evidence needs a reviewer")
            for field in ("reviewed_by", "reviewed_at"):
                if field in record and not isinstance(record[field], str):
                    issues.append(f"{prefix}: {field} must be text")
            if "note" in record and not isinstance(record["note"], str):
                issues.append(f"{prefix}: evidence note must be text")
            for item in evidence:
                if not isinstance(item, dict) or not all(
                    isinstance(item.get(key), str) and item[key].strip()
                    for key in ("source_id", "locator", "quote", "url")
                ):
                    issues.append(f"{prefix}: source evidence needs id, locator, quote and URL")
                    continue
                try:
                    url = urlsplit(item["url"])
                    valid = url.scheme in {"https", "http"} and bool(url.hostname) and not url.username
                except ValueError:
                    valid = False
                if not valid:
                    issues.append(f"{prefix}: evidence URL must be HTTP(S)")
    return issues


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
        issues.extend(validate_language_evidence(term, glossary.get("schema_version", 1) == 2))
        exclusions = term.get("source_exclusions", [])
        if not isinstance(exclusions, list) or not all(isinstance(item, str) and item.strip() for item in exclusions):
            issues.append(f"{term_id}: source_exclusions must contain nonempty strings")
        elif exclusions and not term.get("sense_note"):
            issues.append(f"{term_id}: source_exclusions need a sense_note")
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
            if source_term_matches(term, entry.source):
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
