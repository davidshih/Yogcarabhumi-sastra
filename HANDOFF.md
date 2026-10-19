# Handoff

Created by Codex.
Updated: 2026-10-19T13:59:30-04:00

## Current state
- Worktree: shengwen-di-terminology.
- Branch: codex/terminology-context-evidence-cards.
- Base: origin/codex/auditable-translation-pipeline at 26658a9.
- The user approved this branch and base on 2026-10-19.
- A, B, D and C, E, F, G are implemented and verified.
- C/E/F/G affected baseline: 102 tests and 57 subtests passed. No baseline failure.
- C/E/F/G affected final: 116 tests and 59 subtests passed.
- Final full suite: 134 tests and 68 subtests passed. No failure remains.
- Chromium verified reader cards and the audit report at 400px and 1280px in both themes.
- Keyboard, focus return, commentary expansion, issue filtering, and overflow checks passed.
- Both final status screenshots are 2560px wide. The report checker passed.
- No translation or production page was changed. No paid model call ran.

## Commits
- 017c6a8 implements A, B, and D and records the warning audit.
- 9ad182b implements C, E, F, G and records verification and source excerpts.
- No PR or merge was performed. This branch remains stacked on the unmerged pipeline.

## Scope and decisions
- A: distinguish terminology senses and classify existing warnings.
- B: keep evidence and status separate for each language.
- D: show language candidates and uncertainty in reader cards.
- C: block high-risk publication without current editorial evidence and audited parallel review.
- E: distinguish authors, quoted authors, cross-text comparisons, and disagreements.
- F: list issues; update candidates; decide or reopen; preserve earlier decisions.
- G: record source hashes and acquisition metadata; invalidate affected clause reviews.
- Unaffected reviews can be reused only after artifact and dependency checks.
- All six high-risk issues remain open. They affect 63 term-clause occurrences.
- Do not invent Sanskrit or Pali evidence. Chinese commentary alone does not establish an Indic form.
- The catalog includes Guang, Bao, Tai, Dunlun, Kuiji, Woncheuk, Huizhao, and Zhizhou.
- Catalog-only entries are not verified textual evidence or claims of direct discipleship.
- Retain scholarly disagreement. Do not decide it by model majority vote.

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
- Runtime job status uses docs/jobs-status.json. docs/status.json uses the handoff schema.
- Existing published translation pages require attestation. Build previews outside docs.
- Use --diagnostic-output for previews; --output alone does not authorize them.
- Local hashes detect drift. They do not authenticate against a malicious repository writer.
- Source files are cached. Refresh them explicitly before comparing snapshots.
- Old cache acquisition dates remain unknown. The source metadata records a null fetch time.
- A decision applies to every current occurrence of its term. Split distinct senses before deciding.
- Publication seals include the shared registry. An unrelated registry change requires a new seal.
- Review reuse remains selective even when a shared publication seal expires.

## Unverified and open findings
- No paid LLM review, scholarly adjudication, production publication, or merge ran.
- The 16 retained terminology warnings remain. See docs/reference/term-warning-audit-codex.md.
- Volume 33 has 18 existing source-range mismatches. Review alignment in a separate task.
- Five short commentary quotations were verified. Other catalog leads still need located excerpts.
- Existing language candidates remain unresolved. Do not treat UI cards as approval.

## Next
- Read docs/reports/20261019-audit-review-governance-codex.html for the issue and commentary view.
- Read docs/reference/review-governance-codex.md for CLI usage and schema limits.
- Run: uv run python scripts/review_governance.py list
- Run: uv run python scripts/review_governance.py verify
- Before changes, run: uv run python scripts/review_governance.py snapshot --output /tmp/review-before.json
- After changes, run: uv run python scripts/review_governance.py impact --baseline /tmp/review-before.json
- Run qtest --all to repeat verification.
- Resume H1 only after the user returns and usage recovers.
