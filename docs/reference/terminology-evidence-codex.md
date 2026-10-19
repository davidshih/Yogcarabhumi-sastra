# Terminology context and language evidence

Created by Codex.

## Scope

Schema version 2 adds language_evidence for chinese, sanskrit, and pali.
Each candidate has form, status, evidence, and an optional note.
Candidate order and spellings must match xuanzang or the existing language list.
Existing status describes the legacy Chinese translation policy only.
It does not approve a foreign-language candidate. All migrated records remain unresolved.

## Evidence states

- unresolved: the mapping has not been established. Evidence may be empty.
- inferred: a proposed mapping supported by an explicit source, not an attested source form.
- comparative: a related form from another text or tradition.
- attested: a reviewer has approved the documented mapping.

Non-unresolved records require evidence. Each source needs source_id, locator, quote, and an HTTP(S) URL.
Attested records also require reviewed_by and reviewed_at as an ISO date.
Validation checks data structure. It does not fetch sources or prove the scholarly claim.
Publication risk gates and independent Codex/Opus review remain separate future work.
Legacy schema-1 terms can be read; cards mark missing evidence as unresolved.
Schema-2 terms without evidence records are rejected by the glossary validator.
The runner initializes all model-proposed terms as unresolved, with empty evidence.
It does not accept a model self-attestation. Add verified sources through editorial review.

## Context exclusions

source_exclusions lists longer expressions that do not use this sense.
sense_note states the reason. Whitespace is ignored for line-wrapped CBETA text.
Only an occurrence fully inside an excluded expression is excluded.
A true taste occurrence elsewhere in the same passage still triggers its check.
The checker and runner glossary selection use the same function.
This is conservative lexical disambiguation, not a complete semantic parser.
Remaining warnings need editorial review; they are not suppressed.

## Reader cards

Newly built translation pages embed the cards and interaction script in the sealed HTML.
Click a dotted term or press Enter or Space to open its native dialog.
Escape or the close button returns focus to the originating term.
Chinese, Sanskrit, and Pali have separate candidate, status, and source sections.
Source quotes and links are escaped. Unsafe link schemes are rejected.
Existing published pages remain unchanged until rebuilt through the attestation workflow.

## Status artifacts

The runner and progress page now use docs/jobs-status.json.
The old job snapshot is preserved there. docs/status.json follows the handoff schema.
This avoids the runner overwriting repository continuity metadata.

## Verify

Run qtest tests/test_term_evidence_codex.py tests/test_build_translation_html.py tests/test_pipeline_audit.py.
Build a preview outside docs with uv run python scripts/build_translation_html.py --translation translations/T1579-001-baihua.md --diagnostic-output /tmp/term-preview/T1579/translations/T1579-001-baihua.html.
Provide style.css and theme.js at /tmp/term-preview to view the existing site styling.
