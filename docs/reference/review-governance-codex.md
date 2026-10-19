# Evidence governance and targeted review

Created by Codex. Scope: C, E, F, and G. H remains deferred.

## Publication gate (C)

The six initial high-risk issues remain open. No scholarly decision was made.
A high-risk term requires a resolved, evidenced editorial choice for each affected clause.
The parallel review must pass for the same source, translation, term record, evidence, and decision.
`not_checked`, missing evidence, changed snapshots, and stale decisions block publication.
The runner checks this before HTML construction. The publisher checks it again against live dependencies.
A new volume seal alone cannot bypass this check. Review claims refer to hashed response artifacts and audit events.
These local hashes detect drift. They are not signatures against a malicious repository writer.

## Commentary comparison (E)

`translations/review/registry-codex.json` records authors, quoted authors, relations, interpretations, and adoption notes.
Short CBETA snapshots retain edition, acquisition time, and the upstream document hash.
The five located excerpts are from T1821, T1822, T1828, and T1829.
X0836 has a short source snapshot but no accepted term evidence.
The catalog also records X0369, T1830, T1832, and T1833 as unverified leads.
Reader cards distinguish direct commentary, cross-text comparison, quotation, and disagreement.
A quoted author is not an independent second witness. Later commentators are not all direct disciples of Xuanzang.
Chinese commentary does not establish a Sanskrit or Pali form by itself.

## Issue workflow (F)

Run from the repository root. Commands print JSON and return a nonzero status on invalid input.

```sh
uv run python scripts/review_governance.py list
uv run python scripts/review_governance.py sources
uv run python scripts/review_governance.py verify
```

Edit the registry to add an issue or located evidence. Keep issue IDs and term IDs unique.
Verify the exact quotation against a pinned source snapshot. Add source edition and author metadata.
Candidate JSON has `id`, `label`, and `evidence_ids` fields. Empty evidence is permitted for an unresolved candidate.

```sh
uv run python scripts/review_governance.py candidate --issue issue-viveka --file /tmp/candidate.json
uv run python scripts/review_governance.py decide --issue issue-viveka --choice CHOICE_ID --reviewer REVIEWER --rationale 'Evidence-based reason'
uv run python scripts/review_governance.py reopen --issue issue-viveka
```

A decision command is an explicit editorial action. It binds all current occurrences of that term.
Use separate term senses when one decision is not valid for every occurrence.
Changing a candidate reopens the issue. Replacing or reopening a decision preserves its prior record.
`list` reports effective stale status and affected occurrence IDs. Reader cards link to affected clauses.
The CLI edits the repository; the reader is read-only. Publication still requires a fresh audited review.

## Source and decision impact (G)

Take a snapshot before a source or terminology change. Compare it afterward.

```sh
uv run python scripts/review_governance.py snapshot --output /tmp/review-before.json
uv run python scripts/review_governance.py impact --baseline /tmp/review-before.json --output /tmp/review-impact.json
```

The report lists added, removed, and changed occurrences and clauses. A raw artifact hash change is reported separately.
A line change invalidates the affected clauses. An edition change invalidates all clauses from that edition.
The runner stores the review dependency snapshot and impact report in job progress.
It removes affected review attestations. It reuses other reviews only after checking call artifacts and current inputs.
A source excerpt mismatch must be reconciled before approval. The tool does not rewrite source text.
New downloads record URL, hash, observation time, and fetch time in a `.source-codex.json` sidecar.
For existing caches, fetch time is null. The tool does not invent an acquisition date.
The source cache is not automatically refreshed. Replace it from a verified source, then run `impact`.

## Verification and limits

The seeded data contains 133 clauses, 642 term-clause occurrences, and 63 required occurrences.
Eighteen existing source-range mismatches occur in volume 33. These require a separate source alignment review.
No translation was edited. No production page was rebuilt. No paid model call ran.
The code tests cover missing evidence, stale decisions, tampered artifacts, publisher bypass, and selective review reuse.
The browser checks use generated diagnostic pages. Existing published pages need a new successful attestation before replacement.
