# Handoff

Created by Codex.
Updated: 2026-10-19T13:38:24-04:00

## Current state
- Worktree: shengwen-di-terminology.
- Branch: codex/terminology-context-evidence-cards.
- Base: origin/codex/auditable-translation-pipeline at 26658a9.
- The user approved this branch and base on 2026-10-19.
- A, B, and D are implemented and verified in this worktree.
- Baseline: 89 tests and 55 subtests passed. No baseline test failed.
- Existing glossary checks report 29 warnings across seven translation files.
- The minimal taste-attachment reproduction now passes.
- Affected tests: 89 -> 102 passed; subtests: 55 -> 57 passed.
- Final full suite: 120 tests and 66 subtests passed.
- Chromium verified cards at 400px and 1280px in both themes.
- Keyboard, click, Escape, focus return, and overflow checks passed.

## Commits
- 017c6a8 implements A, B, and D and records the warning audit.
- No PR or merge was performed. The branch is stacked on the unmerged pipeline.

## Scope
- A: distinguish terminology senses and classify existing warnings.
- B: record separate evidence and status for each language.
- D: show Sanskrit and Pali terms, uncertainty, and sources in reader cards.
- Do not invent Sanskrit or Pali evidence. Keep uncertain mappings unresolved.
- Do not edit translations to silence checker warnings.

## H: independent Codex and Opus reviewers
- Deferred at the user's request until usage recovers. Do not launch paid reviews now.
- H1: review a small sample of difficult passages independently with Codex and Opus.
- Give both reviewers the same source, translation, glossary, and evidence snapshot.
- Do not show either reviewer the other review before both finish.
- Compare shared findings, single-reviewer findings, and conflicting interpretations.
- Each finding needs a clause locator, exact source quote, translation quote, and rationale.
- Missing source evidence must yield not_checked or unresolved. Do not cite model memory.
- Record useful new findings, false positives, elapsed time, calls, and observed cost.
- H1 engineering estimate: 0.5 to 1 day, excluding human scholarly adjudication.
- H2: integrate dual review, failures, reconciliation, post-fix review, and publication gates.
- Both reviewers must review changed passages again. Old approvals must expire.
- Preserve disagreements about Sanskrit, Pali, and Guang/Bao commentary interpretations.
- Do not decide scholarly claims by majority vote. Route major disputes to a human.
- H2 engineering estimate: 2 to 3 days. Full evidence governance depends on B.
- Decide full-volume versus risk-triggered dual review from H1 results.
- Actual Codex and Opus model IDs, access, quotas, and prices remain unverified.
- Never use an Astra model for delegated review.

## Gotchas
- Runner job status now uses docs/jobs-status.json. The previous snapshot is preserved.
- docs/status.json uses the handoff schema. The progress reader and writer use the new job path.
- Existing published translation pages require attestation. Build previews outside docs.

## Unverified
- No paid LLM review, scholarly adjudication, or production publication ran.
- The 16 remaining warnings are retained. See docs/reference/term-warning-audit-codex.md.
- Existing candidate spellings, translations, and legacy Chinese statuses are unchanged.
- All migrated language evidence remains unresolved.

## Next
- Review the branch diff and resume H1 when usage recovers.
- Use qtest --all to repeat the full suite.
- Use the audit report in ~/Documents/claude-reports for background findings.
