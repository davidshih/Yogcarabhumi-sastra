#!/usr/bin/env bash
# Locked acceptance checks for 20261013-merge-audit-pipeline-claude. Written before implementation; do not edit
# after hand-off. Every literal it compares with is inside this file. It writes no tracked file; scratch files go
# to a mktemp directory. No network.
set -uo pipefail
cd "$(git -C "$(dirname "$0")" rev-parse --show-toplevel)" || exit 2
BASE=dd46ba8e01cf0492f5ac4a57433f8418a3ecf3fd        # main when the run started
BRANCH_TIP=a5f077952bc77d420adf9ebfd766e52f95a80607  # origin/codex/batch1-69
export PYTHONDONTWRITEBYTECODE=1
SCRATCH=$(mktemp -d) || exit 2
fail=0
check() {  # check <name> <command...>: passes when the command exits 0
  local name=$1 out; shift
  if out=$("$@" 2>&1); then echo "PASS  $name"; else echo "FAIL  $name"; tail -n 6 <<<"$out"; fail=1; fi
}

FROZEN_PATHS=$(cat <<'ACCEPT_EOF'
docs
translations
data
works.json
scripts/runner.py
scripts/webapp.py
scripts/build_search_index.py
scripts/build_shengwen_di_html.py
scripts/make_translation_skeleton.py
scripts/check_paragraph_ids.py
scripts/check_translation_terms.py
scripts/check_html_links.py
prompts/merge.txt
prompts/review_doctrine.txt
prompts/review_parallel.txt
prompts/review_terms.txt
prompts/segment.txt
prompts/translate.txt
prompts/translate_batch.txt
tests/test_check_translation_coverage.py
tests/test_llm_codex_config.py
tests/test_paragraph_ids.py
tests/test_runner_parallel.py
tests/test_status_page.py
.github/workflows/deploy-pages.yml
.github/dependabot.yml
.docs-exceptions
.gitignore
ACCEPT_EOF
)
BRANCH_BLOBS=$(cat <<'ACCEPT_EOF'
ab8c08bf4402dabc6e232ebcadf7761e62c414ffbe1b88c3031eea2bdbe5dbc5  scripts/audit.py
a251a7f31846af33bdf9af47aa6296eaaa305bb811c2961d5bcd0ce88d3ae097  scripts/publisher.py
a527240ed3af3e758af2361f802b54ba83b06db71b72c797dc2a77688fa874ce  prompts/audited/common_policy.txt
6018f844f0b8edf2f3a4dec37082759a81d13a7d2f6b119f81dd07fc7ea31094  prompts/audited/fix.txt
3c6484882d78fd929f6c5a8a1868cd98f85ce1e16fd130240f740d3f1d413c3d  prompts/audited/merge.txt
15e430b6ee129042c8abf60189833d385438ae9ccf70e91d78c3e1238b22bcdb  prompts/audited/review_doctrine.txt
ad16acc2003955e8ad6bd1fe87636cf68be4680b29908d60427f2155c914974c  prompts/audited/review_parallel.txt
12aa3eb26530643a1ecb4b157bae754fb6ba7baa4e2cb27b03af4db12e129f0f  prompts/audited/review_terms.txt
e89ee402cc89dfc25572d521c7fbebd61d468de15b48ad6eeecea5387817f447  prompts/audited/segment.txt
f18783a214bbefd4c46fe2882a4685e591a202f7f61e6a49afd61a1798eed0ae  prompts/audited/translate.txt
e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855  tests/__init__.py
5c0665811546fd7783d2ed527e4a14f37714a5db75c7a2c4c380d65dbb3f5b92  tests/test_translation_coverage.py
917b4f7908c2846c8d605416f4c1f157c8468c71c2c45462edb407157c403821  tests/test_audit_hardening.py
ACCEPT_EOF
)
BRANCH_TESTS=$(cat <<'ACCEPT_EOF'
test_audit_hardening.SecretRedactionTests.test_redacts_structured_and_header_secrets_without_breaking_json
test_audit_hardening.SecretRedactionTests.test_preserves_assignment_syntax_and_quote_style
test_audit_hardening.ManifestHardeningTests.test_sums_complete_token_fields_and_marks_only_missing_fields
test_audit_hardening.ManifestHardeningTests.test_sums_every_field_when_all_calls_report_tokens
test_audit_hardening.ManifestHardeningTests.test_null_tokens_mark_every_field_unavailable_without_dropping_raw_usage
test_audit_hardening.ManifestHardeningTests.test_commit_updates_remain_reconstructable_from_append_only_events
test_build_translation_html.TooltipRenderingTests.test_juan_70_wraps_source_and_translation_with_stable_identity
test_build_translation_html.TooltipRenderingTests.test_glossary_changes_are_visible_in_the_same_process
test_build_translation_html.TooltipRenderingTests.test_alias_owner_and_overlap_are_deterministic
test_build_translation_html.TooltipRenderingTests.test_first_occurrence_uses_visible_canonical_format
test_build_translation_html.TooltipRenderingTests.test_first_occurrence_visible_text_is_escaped
test_build_translation_html.TooltipRenderingTests.test_ambiguous_alias_without_primary_owner_is_rejected
test_build_translation_html.FullRebuildTests.test_build_all_renders_every_translation
test_build_translation_html.FullRebuildTests.test_cli_requires_explicit_legacy_gate_for_all
test_build_translation_html.FullRebuildTests.test_cli_requires_attestation_for_docs_or_explicit_external_diagnostic
test_build_translation_html.FullRebuildTests.test_attested_candidate_hash_mismatch_preserves_existing_output
test_build_translation_html.FullRebuildTests.test_direct_docs_build_requires_explicit_authorization
test_build_translation_html.FullRebuildTests.test_attested_build_verifies_before_and_after_write
test_build_translation_html.RangeParsingTests.test_parse_range_requires_full_cbeta_line_ids
test_llm_rate_limits.ResumeParserTests.test_codex_human_date_preserves_date_year_and_timezone
test_llm_rate_limits.ResumeParserTests.test_human_date_accepts_ordinals_and_month_names
test_llm_rate_limits.ResumeParserTests.test_explicit_provider_timezone_does_not_use_process_timezone
test_llm_rate_limits.ResumeParserTests.test_final_provider_error_beats_earlier_reset_metadata
test_llm_rate_limits.ResumeParserTests.test_without_provider_error_uses_latest_future_candidate
test_llm_rate_limits.DiagnosticSanitizationTests.test_successful_model_output_cannot_trigger_limit_classification
test_llm_rate_limits.DiagnosticSanitizationTests.test_run_llm_does_not_treat_out_file_as_diagnostic_channel
test_llm_rate_limits.DiagnosticSanitizationTests.test_final_auth_fatal_overrides_earlier_quota_error
test_llm_rate_limits.DiagnosticSanitizationTests.test_partial_linewise_prefixed_prompt_echo_cannot_trigger_limit
test_llm_rate_limits.DiagnosticSanitizationTests.test_arbitrary_framing_with_complete_prompt_line_cannot_trigger_limit
test_llm_rate_limits.DiagnosticSanitizationTests.test_short_prompt_fragment_does_not_filter_real_provider_error
test_llm_rate_limits.DiagnosticSanitizationTests.test_complete_long_prompt_echo_is_filtered_even_when_error_prefixed
test_llm_rate_limits.DiagnosticSanitizationTests.test_prompt_echo_cannot_trigger_limit_classification
test_llm_rate_limits.DiagnosticSanitizationTests.test_sanitized_error_keeps_exact_reset_sentence_and_drops_prompt
test_llm_rate_limits.LimitDecisionTests.test_out_of_policy_provider_reset_uses_probe_without_losing_provider_time
test_llm_rate_limits.LimitDecisionTests.test_no_reset_first_wait_uses_fifteen_minute_probe
test_llm_rate_limits.LimitDecisionTests.test_fallback_log_calls_next_wait_a_probe
test_llm_rate_limits.RunnerRateLimitAuditTests.test_rate_limit_event_separates_provider_and_effective_resume_times
test_pipeline_audit.StructuredContractTests.test_semantic_retry_repairs_observed_unit_009_and_012_shapes
test_pipeline_audit.StructuredContractTests.test_semantic_retry_repairs_parse_json_value_error
test_pipeline_audit.StructuredContractTests.test_semantic_retry_raises_second_value_error_without_saving
test_pipeline_audit.StructuredContractTests.test_semantic_retry_first_valid_has_one_call_and_validation_event
test_pipeline_audit.StructuredContractTests.test_semantic_retry_does_not_catch_llm_error
test_pipeline_audit.StructuredContractTests.test_llm_attempt_records_semantic_attempt
test_pipeline_audit.StructuredContractTests.test_semantic_retry_audit_links_attempts_and_request_hashes
test_pipeline_audit.StructuredContractTests.test_semantic_retry_redacts_validator_error_and_never_sleeps
test_pipeline_audit.StructuredContractTests.test_semantic_validation_requires_linked_llm_event
test_pipeline_audit.StructuredContractTests.test_semantic_validation_rejects_stale_caller_llm_event_ids
test_pipeline_audit.StructuredContractTests.test_semantic_retry_makes_no_premature_translation_mutation
test_pipeline_audit.StructuredContractTests.test_prior_saved_contract_still_skips_semantic_call
test_pipeline_audit.StructuredContractTests.test_all_translation_mutation_call_sites_use_semantic_retry
test_pipeline_audit.StructuredContractTests.test_translation_schema_exposes_exact_envelopes
test_pipeline_audit.StructuredContractTests.test_translation_schema_exposes_nested_object_shapes
test_pipeline_audit.StructuredContractTests.test_translation_schema_and_validator_share_nested_shapes
test_pipeline_audit.StructuredContractTests.test_translation_contract_rejects_observed_nested_alias_shapes
test_pipeline_audit.StructuredContractTests.test_translation_contract_rejects_nonstring_and_blank_text
test_pipeline_audit.StructuredContractTests.test_bound_translation_schema_uses_contract_consts
test_pipeline_audit.StructuredContractTests.test_translate_sections_passes_bound_schema_to_prompt
test_pipeline_audit.StructuredContractTests.test_addition_schema_describes_inner_text_without_delimiters
test_pipeline_audit.StructuredContractTests.test_translate_prompt_requires_unwrapped_addition_text
test_pipeline_audit.StructuredContractTests.test_bracketed_addition_text_is_rejected_but_inner_text_passes
test_pipeline_audit.StructuredContractTests.test_addition_text_with_delimiters_is_rejected_even_if_double_wrapped
test_pipeline_audit.StructuredContractTests.test_addition_wrapper_must_appear_exactly_once
test_pipeline_audit.StructuredContractTests.test_translation_contract_requires_stable_identity_and_fields
test_pipeline_audit.StructuredContractTests.test_translation_nested_objects_reject_empty_shapes
test_pipeline_audit.StructuredContractTests.test_review_contract_cannot_return_rewritten_translation
test_pipeline_audit.StructuredContractTests.test_review_schema_binds_exact_finding_evidence_and_parallel_identity
test_pipeline_audit.StructuredContractTests.test_review_prompts_and_schema_require_contiguous_literal_evidence_quotes
test_pipeline_audit.StructuredContractTests.test_review_schema_exposes_shape_that_rejects_observed_67_evidence_string
test_pipeline_audit.StructuredContractTests.test_review_terms_receives_only_whitelisted_structured_contract_annotations
test_pipeline_audit.StructuredContractTests.test_review_terms_reloads_glossary_before_building_next_section_prompt
test_pipeline_audit.StructuredContractTests.test_review_retry_second_failure_has_no_premature_mutations
test_pipeline_audit.StructuredContractTests.test_doctrine_retry_offers_safe_single_line_evidence_candidates
test_pipeline_audit.StructuredContractTests.test_safe_evidence_candidates_cover_production_shaped_18_units
test_pipeline_audit.StructuredContractTests.test_safe_evidence_candidates_do_not_split_unicode_graphemes
test_pipeline_audit.StructuredContractTests.test_safe_evidence_candidates_skip_clusters_longer_than_max_chars
test_pipeline_audit.StructuredContractTests.test_safe_evidence_candidates_define_nonpositive_cap_policy
test_pipeline_audit.StructuredContractTests.test_review_retry_repairs_parse_error_but_does_not_catch_llm_error
test_pipeline_audit.StructuredContractTests.test_review_preflight_rejects_stale_or_malformed_contract_before_llm
test_pipeline_audit.StructuredContractTests.test_review_whole_pass_preflight_rejects_second_stale_contract_before_any_llm
test_pipeline_audit.StructuredContractTests.test_review_chain_fixes_then_runs_all_reviewers_again
test_pipeline_audit.StructuredContractTests.test_declarative_routing_has_no_legacy_provider
test_pipeline_audit.StructuredContractTests.test_review_verdict_quotes_and_parallel_allowlist_are_enforced
test_pipeline_audit.StructuredContractTests.test_review_parallel_normalizes_nonblank_source_ids_for_input_and_schema
test_pipeline_audit.StructuredContractTests.test_formal_codex_command_pins_model_and_effort
test_pipeline_audit.StructuredContractTests.test_stale_translation_contract_is_rerun
test_pipeline_audit.StructuredContractTests.test_dual_merge_stale_contract_is_rerun
test_pipeline_audit.StructuredContractTests.test_enqueue_saves_positive_requested_parallel_configuration
test_pipeline_audit.AuditStoreTests.test_dual_echo_one_volume_reaches_attested_build_in_temp_root
test_pipeline_audit.AuditStoreTests.test_artifacts_are_deduplicated_redacted_and_manifested
test_pipeline_audit.AuditStoreTests.test_quality_gate_hash_seal_blocks_build_bypass
test_pipeline_audit.AuditStoreTests.test_fake_backend_reports_measured_and_unavailable_metadata
test_pipeline_audit.AuditStoreTests.test_ledger_requires_terms_and_doctrine_review_events
test_pipeline_audit.AuditStoreTests.test_real_review_events_seal_structured_contract_digest_for_ledger
test_pipeline_audit.PublisherTests.test_publication_retry_is_idempotent_after_each_push_failure
test_pipeline_audit.PublisherTests.test_build_accepts_content_gate_before_attestation_exists
test_pipeline_audit.PublisherTests.test_volume_attestation_seals_every_publishable_output
test_pipeline_audit.PublisherTests.test_content_publication_paths_are_all_sealed
test_pipeline_audit.PublisherTests.test_attestation_snapshot_survives_append_but_sealed_content_does_not
test_pipeline_audit.PublisherTests.test_publisher_uses_explicit_paths_branch_and_read_only_remote_check
test_pipeline_audit.PublisherTests.test_publisher_rejects_main_and_remote_ahead
test_translation_coverage.TranslationRangeCoverageTests.test_exact_once_ranges_and_source_blocks_pass
test_translation_coverage.TranslationRangeCoverageTests.test_middle_gap_fails
test_translation_coverage.TranslationRangeCoverageTests.test_overlap_fails
test_translation_coverage.TranslationRangeCoverageTests.test_source_block_mismatch_fails
test_translation_coverage.TranslationRangeCoverageTests.test_source_block_ignores_layout_and_star_apparatus
test_translation_coverage.TranslationRangeCoverageTests.test_source_block_does_not_hide_non_allowlisted_apparatus
test_translation_coverage.ClauseCoverageLedgerTests.test_valid_ledger_passes
test_translation_coverage.ClauseCoverageLedgerTests.test_stale_hash_and_missing_clause_line_fail
test_translation_coverage.ClauseCoverageLedgerTests.test_duplicate_clause_id_and_uncovered_status_fail
test_translation_coverage.ClauseCoverageLedgerTests.test_empty_or_malformed_evidence_fails
test_translation_coverage.ClauseCoverageLedgerTests.test_translation_evidence_cannot_point_to_source_block
test_translation_coverage.CoverageCliTests.test_requires_explicit_ledger_or_ranges_only_mode
ACCEPT_EOF
)
MAIN_TEST_MODULES="tests.test_check_translation_coverage tests.test_llm_codex_config tests.test_paragraph_ids tests.test_runner_parallel tests.test_status_page"
AUDIT_TESTS="test_clean_juan_passes_and_changes_nothing test_shared_boundary_line_is_a_warning test_flagged_section_is_rewritten_and_everything_else_is_kept test_unresolved_finding_fails_and_touches_nothing test_glossary_issues_the_fixer_cannot_repair_fail_and_touch_nothing test_missing_source_lines_fail_before_any_model_call test_empty_translation_fails_before_any_model_call test_resume_of_a_finished_job_does_nothing test_two_juans_run_in_one_job_and_one_failure_gives_exit_1 test_refuses_to_run_while_a_runner_holds_the_lock test_other_works_are_refused"

# ---------- helpers ----------

merge_recorded() {  # the merge of the branch is in progress (stage 4) or committed (stage 5)
  git cat-file -e "$BRANCH_TIP^{commit}" || return 1
  if git rev-parse -q --verify MERGE_HEAD >/dev/null; then
    [[ $(git rev-parse MERGE_HEAD) == "$BRANCH_TIP" ]]
  else
    git merge-base --is-ancestor "$BRANCH_TIP" HEAD
  fi
}

nothing_unmerged() {
  [[ -z $(git diff --name-only --diff-filter=U) ]] || { git diff --name-only --diff-filter=U; return 1; }
  if grep -rIlE '^(<<<<<<< |>>>>>>> )' scripts tests prompts README.md .github 2>/dev/null | grep -q .; then
    grep -rIlE '^(<<<<<<< |>>>>>>> )' scripts tests prompts README.md .github; return 1
  fi
}

frozen_equal_main() {  # every frozen path has the bytes of main; the three content folders hold no new file
  local path bad=0
  while IFS= read -r path; do
    [[ -n $path ]] || continue
    git diff --quiet "$BASE" -- "$path" || { echo "differs from main: $path"; bad=1; }
  done <<<"$FROZEN_PATHS"
  [[ -z $(git ls-files --others --exclude-standard -- docs translations data) ]] \
    || { echo "new files:"; git ls-files --others --exclude-standard -- docs translations data | head -5; bad=1; }
  return $bad
}

branch_files_landed() {  # the branch's two modules, eight prompts and three test files, byte for byte
  local digest path bad=0
  while read -r digest path; do
    [[ -n $digest ]] || continue
    [[ -f $path && $(shasum -a 256 < "$path" | cut -d' ' -f1) == "$digest" ]] || { echo "missing or changed: $path"; bad=1; }
  done <<<"$BRANCH_BLOBS"
  return $bad
}

ran_ok() {  # ran_ok <count> <unittest ids...>: exactly <count> tests ran, and all passed
  local want=$1 out; shift
  out=$(python3 -B -m unittest "$@" 2>&1) || { tail -n 20 <<<"$out"; return 1; }
  grep -qE "^Ran $want tests? in " <<<"$out" || { tail -n 5 <<<"$out"; return 1; }
}

main_tests() {  # shellcheck disable=SC2086
  ran_ok 44 $MAIN_TEST_MODULES
}

branch_tests() {
  local ids=() id
  while IFS= read -r id; do [[ -n $id ]] && ids+=("tests.$id"); done <<<"$BRANCH_TESTS"
  [[ ${#ids[@]} -eq 112 ]] || { echo "the pinned list has ${#ids[@]} ids"; return 1; }
  ran_ok 112 "${ids[@]}"
}

audit_tests() {
  local ids=() name
  for name in $AUDIT_TESTS; do ids+=("tests.test_legacy_audit_acceptance.LegacyAuditAcceptance.$name"); done
  ran_ok 11 "${ids[@]}"
}

checkout_still_clean() {  # no test wrote into the checkout: content as on main, and no audit data left behind
  frozen_equal_main || return 1
  [[ ! -e audits && ! -e artifacts ]] || { echo "audits/ or artifacts/ exists in the checkout"; return 1; }
  [[ -z $(git status --porcelain -- docs translations data) ]] || { git status --porcelain -- docs translations data | head -5; return 1; }
}

suite_size() {  # the suite that CI runs holds main's 44, the branch's 112 and the 11 acceptance tests
  python3 -B - <<'PY'
import unittest
count = unittest.TestLoader().discover("tests").countTestCases()
assert count >= 167, count
PY
}

py() {  # py <<'PY' ... PY: a snippet that runs from the repo root with scripts/ on the path
  python3 -B -c 'import sys; sys.path.insert(0, "scripts"); exec(sys.stdin.read())'
}

pages_reproducible() {  # every published translation page is still the legacy rendering of its md
  py <<'PY'
from pathlib import Path
import build_translation_html as bth
sources = sorted(Path("translations").glob("*-baihua.md")) + sorted(Path("translations/versions").glob("*.md"))
assert len(sources) == 81, len(sources)
for md in sources:
    text = md.read_text(encoding="utf-8")
    work, juan = bth.infer_work_juan(md)
    page = Path("docs") / work / "translations" / f"{md.stem}.html"
    html = bth.render(bth.parse_entries(text), md, work, juan, bth.infer_title(text, work, juan))
    assert html.encode("utf-8") == page.read_bytes(), md.name
PY
}

ranges_default_unchanged() {  # the production call of check_ranges still accepts all 81 translations
  py <<'PY'
from pathlib import Path
import build_translation_html as bth
import check_translation_coverage as cov
from make_translation_skeleton import extract_lines, line_key
checked = 0
for md in sorted(Path("translations").glob("*-baihua.md")):
    work, juan = bth.infer_work_juan(md)
    data = Path("data") / f"{work}-{juan:03d}.json"
    lines = extract_lines(data)
    ids = sorted((i for i, t in lines.items() if t), key=line_key)
    issues = cov.check_ranges(cov.parse_ranges(md.read_text(encoding="utf-8")), cov.data_line_ids(data), ids[0], ids[-1])
    assert issues == [], (md.name, issues[:2])
    checked += 1
assert checked == 81, checked
PY
}

ranges_strict_modes() {  # exact-once is on with source lines; a legacy audit may allow shared boundary lines
  py <<'PY'
from pathlib import Path
import check_translation_coverage as cov
from make_translation_skeleton import extract_lines, line_key
md, data = Path("translations/T1579-072-baihua.md"), Path("data/T1579-072.json")
lines = extract_lines(data)
ids = sorted((i for i, t in lines.items() if t), key=line_key)
entries, line_ids = cov.parse_ranges(md.read_text(encoding="utf-8")), cov.data_line_ids(data)
strict = cov.check_ranges(entries, line_ids, ids[0], ids[-1], lines)
assert strict == ["translation ranges overlapping lines: 0695a23"], strict
assert cov.check_ranges(entries, line_ids, ids[0], ids[-1], lines, exact_once=False) == []
assert cov.range_overlaps(entries, line_ids, ids[0], ids[-1]) == ["T30n1579_p0695a23"]
gap = [entry for index, entry in enumerate(entries) if index != 2]
loose = cov.check_ranges(gap, line_ids, ids[0], ids[-1], lines, exact_once=False)
assert len(loose) == 1 and loose[0].startswith("translation ranges missing lines: "), loose
other = cov.parse_ranges("# t\n\n## 01 x\nRange: T29n1558_p0001a02-p0001a07\n\nSource:\n<<<\n甲\n>>>\n")
assert (other[0].start, other[0].end, other[0].source) == ("T29n1558_p0001a02", "T29n1558_p0001a07", "甲")
PY
}

llm_names() {
  py <<'PY'
import llm
assert llm.MODELS == ("claude", "codex", "glm"), llm.MODELS
assert llm.FAKE_MODELS == ("echo", "echo-limit-once"), llm.FAKE_MODELS
assert llm.AUDIT_MODELS == ("gpt-5.6-terra", "gpt-5.6-sol"), llm.AUDIT_MODELS
assert llm.AUDIT_FAKE_MODELS == ("echo-finding", "echo-finding-stubborn"), llm.AUDIT_FAKE_MODELS
PY
}

production_queues() {
  py <<'PY'
import runner
assert runner.QUEUES == ("claude", "codex", "glm", "mix", "dual", "echo", "echo-limit-once", "dual-echo"), runner.QUEUES
text = runner.prompt_text("translate", juan=1, title="t", note="n", source="s", glossary="[]", extra="",
                          work="T1579", work_title="w", file_id="T30n1579")
assert "$" not in text and text.strip(), text[:80]
PY
}

llm_claude_limit_on_stdout() {  # the claude CLI prints its limit message on stdout: it must stay a limit
  py <<'PY'
import subprocess
from unittest import mock
import llm
message = "You've hit your session limit · resets 9pm (America/New_York)"
with mock.patch.object(llm.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, message, "")):
    result = llm.run_llm("claude", "x")
assert not result.ok and result.limit and result.resume_at is not None, result
with mock.patch.object(llm.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "譯文", "")):
    result = llm.run_llm("claude", "x")
assert result.ok and result.text == "譯文" and not result.limit, result
PY
}

llm_codex_answer_is_not_a_limit() {  # a Codex answer comes from the -o file; its words never mean a limit
  py <<'PY'
import subprocess
from pathlib import Path
from unittest import mock
import llm
answer = "譯文談到 quota exhausted 與 HTTP 429"
def fake(command, **_kwargs):
    Path(command[command.index("-o") + 1]).write_text(answer, encoding="utf-8")
    return subprocess.CompletedProcess(command, 0, "CLI completed", "")
with mock.patch.object(llm.subprocess, "run", side_effect=fake):
    result = llm.run_llm("codex", "p")
assert result.ok and not result.limit and result.text == answer, result
def limited(command, **_kwargs):
    return subprocess.CompletedProcess(command, 1, "", "ERROR: You've hit your usage limit. Try again at 3:45 PM.")
with mock.patch.object(llm.subprocess, "run", side_effect=limited):
    result = llm.run_llm("codex", "p")
assert not result.ok and result.limit and result.resume_at is not None, result
PY
}

llm_retry_api() {  # both callers of call_with_limit_retry keep working: run_fn (production), effort and on_attempt (audited)
  py <<'PY'
import llm
assert llm.call_with_limit_retry("codex", "p", run_fn=lambda model, prompt: llm.LLMResult(ok=True, text="T")) == "T"
seen = []
text = llm.call_with_limit_retry("echo", "【原文】\n甲\n【原文結束】", effort="high", on_attempt=lambda result, attempt: seen.append((result.effort, attempt)))
assert "甲" in text and seen == [("high", 1)], (text, seen)
PY
}

llm_self_check() {
  [[ $(python3 -B scripts/llm.py 2>&1 | tail -n 1) == "llm.py self-check OK" ]]
}

fake_finding_models() {  # the two fake backends that the acceptance tests of the legacy audit use
  py <<'PY'
import json
import llm
def prompt(stage, translation, unit="T1579-007-002"):
    clause = {"clause_id": "T30n1579_p0308c16-p0309a02", "source": "宿作因論者\n謂如有一", "translation": translation, "note": ""}
    payload = {"schema_version": "1.0", "stage": stage, "unit_id": unit, "source_hash": "a" * 64, "clause": clause}
    return "任務\n\n輸入 JSON：\n" + json.dumps(payload, ensure_ascii=False, indent=2) + "\n\n輸出 JSON Schema：\n{}\n"
def ask(model, stage, translation, unit="T1579-007-002"):
    result = llm.run_llm(model, prompt(stage, translation, unit), effort="high")
    assert result.ok, result
    return json.loads(result.text)
found = ask("echo-finding", "review_doctrine", "主張宿作因的人\n是這樣說的")
assert found["verdict"] == "changes_required" and len(found["findings"]) == 1, found
finding = found["findings"][0]
assert finding["clause_id"] == "T30n1579_p0308c16-p0309a02" and finding["severity"] == "high", finding
assert finding["evidence"] == {"source_quote": "宿作因論", "translation_quote": "主張宿作", "reference_ids": []}, finding
assert ask("echo-finding", "review_doctrine", "主張宿作因的人【已修】")["verdict"] == "pass"
assert ask("echo-finding", "review_terms", "主張宿作因的人")["verdict"] == "pass"
assert ask("echo-finding", "review_doctrine", "主張宿作因的人", unit="T1579-007-003")["verdict"] == "pass"
assert ask("echo-finding", "fix", "主張宿作因的人")["clauses"][0]["vernacular"] == "主張宿作因的人【已修】"
assert ask("echo-finding-stubborn", "fix", "主張宿作因的人")["clauses"][0]["vernacular"] == "主張宿作因的人"
assert ask("echo-finding-stubborn", "review_doctrine", "主張宿作因的人")["verdict"] == "changes_required"
assert ask("echo", "review_doctrine", "主張宿作因的人")["verdict"] == "pass"
PY
}

builder_cli_guard() {  # the CLI no longer writes an unsealed page into docs/
  local out code
  out=$(python3 -B scripts/build_translation_html.py --translation translations/T1579-033-baihua.md 2>&1); code=$?
  [[ $code -eq 2 ]] || { echo "exit $code"; return 1; }
  grep -q -- "--attestation" <<<"$out" || { echo "$out" | tail -n 3; return 1; }
  git diff --quiet "$BASE" -- docs/T1579/translations/T1579-033-baihua.html translations/T1579-033-baihua.md
}

builder_diagnostic_legacy() {  # a diagnostic build in the legacy style equals the published page
  python3 -B scripts/build_translation_html.py --translation translations/T1579-033-baihua.md \
    --diagnostic-output "$SCRATCH/T1579-033-baihua.html" --term-style legacy >/dev/null 2>&1 || return 1
  cmp -s "$SCRATCH/T1579-033-baihua.html" docs/T1579/translations/T1579-033-baihua.html
}

builder_api() {
  py <<'PY'
import re
from pathlib import Path
import build_translation_html as bth
assert bth.parse_range("T29n1558_p0001a02-p0001a07") == ("T29n1558_p0001a02", "T29n1558_p0001a07")
assert bth.parse_range("T30n1579_p0684c24-T30n1579_p0685a24") == ("T30n1579_p0684c24", "T30n1579_p0685a24")
for malformed in ("T30n1579_p0465a", "T30n1579_p0465a23-", "p0465a23", "T30n1579_p0465a23-T31n1585_p0001a01"):
    try:
        bth.parse_range(malformed)
    except ValueError:
        continue
    raise AssertionError(f"accepted: {malformed}")
md = Path("translations/T1579-007-baihua.md")
text = md.read_text(encoding="utf-8")
entries = bth.parse_entries(text)
title = bth.infer_title(text, "T1579", 7)
legacy = bth.render(entries, md, "T1579", 7, title)
policy = bth.render(entries, md, "T1579", 7, title, term_style="policy")
assert legacy == bth.render(entries, md, "T1579", 7, title, term_style="legacy")
assert "data-term-id=" not in legacy and "data-term-id=" in policy and "data-first-in-juan=" in policy
ids = [pid for entry in entries for pid in entry.paragraph_ids]
assert len(ids) > 20 and all(f'<p id="{pid}">' in policy for pid in ids), "the policy rendering keeps every paragraph id"
assert bth.render_translation_bytes(md) == policy.encode("utf-8")
assert bth.render_translation_bytes(md, term_style="legacy") == legacy.encode("utf-8")
PY
}

audited_is_isolated() {  # the audited pipeline has its own job folder, status file and prompts
  py <<'PY'
import audited
import runner
assert audited.JOBS_DIR == runner.JOBS_DIR / "audited", audited.JOBS_DIR
assert audited.STATUS_JSON == runner.LOCKS_DIR / "audited-status.json", audited.STATUS_JSON
assert audited.PROMPTS_DIR == runner.PROMPTS_DIR / "audited", audited.PROMPTS_DIR
assert audited.POLICY_PATH == audited.PROMPTS_DIR / "common_policy.txt"
assert sorted(p.name for p in audited.PROMPTS_DIR.glob("*.txt")) == ["common_policy.txt", "fix.txt", "merge.txt",
    "review_doctrine.txt", "review_parallel.txt", "review_terms.txt", "segment.txt", "translate.txt"]
assert audited.stage_model({"model": "auto"}, "review_doctrine") == "gpt-5.6-sol"
assert audited.stage_model({"model": "echo-finding"}, "review_doctrine") == "echo-finding"
assert audited.stage_model({"model": "dual-echo"}, "merge") == "echo"
PY
}

audited_keeps_paragraph_ids() {  # a rewrite through the audited pipeline keeps the paragraph ids of the md
  py <<'PY'
import tempfile
from pathlib import Path
import audited
import build_translation_html as bth
with tempfile.TemporaryDirectory() as tmp:
    md = Path(tmp) / "T1579-007-baihua.md"
    md.write_text("# t\n\n## 01 x\nRange: T30n1579_p0001a01\n\nSource:\n<<<\n原文\n>>>\n\nTranslation:\n<<<\n"
                  "<!-- #T30n1579_p0001a01-aaaaaa -->\n第一段\n\n<!-- #T30n1579_p0001a01-bbbbbb -->\n第二段\n>>>\n\nNote:\n<<<\n\n>>>\n",
                  encoding="utf-8")
    audited.splice(md, 0, "第一段改\n\n第二段", None)
    entry = bth.parse_entries(md.read_text(encoding="utf-8"))[0]
    assert entry.translation == "第一段改\n\n第二段", entry.translation
    assert entry.paragraph_ids == ("T30n1579_p0001a01-aaaaaa", "T30n1579_p0001a01-bbbbbb"), entry.paragraph_ids
PY
}

audited_cli() {
  local out
  out=$(python3 -B scripts/audited.py audit --help 2>&1) || { echo "$out" | tail -n 3; return 1; }
  for flag in --juans --work --model --no-push --resume; do grep -q -- "$flag" <<<"$out" || { echo "no $flag"; return 1; }; done
}

ci_workflow() {
  diff - .github/workflows/ci.yml <<'ACCEPT_EOF'
name: CI

on:
  pull_request:
  push:
    branches:
      - main

permissions:
  contents: read

concurrency:
  group: ci-${{ github.ref }}
  cancel-in-progress: true

jobs:
  ci:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - name: Checkout
        uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1

      - name: Show the Python version
        run: python3 --version

      - name: Unit tests
        run: python3 -B -m unittest discover -s tests

      - name: Backend self-check
        run: python3 -B scripts/llm.py

      - name: Links of the site
        run: python3 -B scripts/check_html_links.py

      - name: Paragraph ids of the site
        run: python3 -B scripts/check_paragraph_ids.py
ACCEPT_EOF
}

readme_documents_the_pipeline() {
  local line bad=0
  while IFS= read -r line; do
    [[ -n $line ]] || continue
    grep -qxF -- "$line" README.md || { echo "README lacks: ${line:0:60}"; bad=1; }
  done <<'ACCEPT_EOF'
## 可稽核管線（`scripts/audited.py`）

`scripts/runner.py` 是正式排程器，行為不變。`scripts/audited.py` 是另一條獨立的管線：每次模型呼叫都留下紀錄，審查要附原文與譯文的逐字引文，過了品質關卡才封存成卷。它目前只支援 T1579，工作狀態放在 `jobs/audited/`，不會認領正式排程器的工作。兩者共用 `locks/runner.pid`，同一時間只能跑一個。

稽核紀錄在 `audits/<job id>/`（事件、coverage ledger、封存證明、報告），模型的輸入與輸出在 `artifacts/sha256/`。這兩個資料夾都進 git。

只審查已發布的舊譯文，不重譯：

```sh
python3 scripts/audited.py audit --juans 67-72 --no-push
python3 scripts/audited.py audit --resume <job id> --no-push
```

- 每一段跑術語與義理兩道審查，再跑術語機械檢查。沒有問題的段落一個字都不動，段落 id 也不動。
- 被點名的段落交給 fixer 重寫，只有重寫過的段落會再審一次。
- 還有問題就整卷不過：譯稿與網頁維持原狀，候選稿與報告留在 `audits/<job id>/`。
- 相鄰段落共用一行邊界只記成警告，不擋關。
- `--model echo` 可以離線試跑整個流程，不花模型額度。
- 用封存證明重建舊式頁面時加上 `--term-style legacy`：`python3 scripts/build_translation_html.py --translation translations/T1579-067-baihua.md --attestation <封存證明> --term-style legacy`。
ACCEPT_EOF
  grep -qF '段落 id（閱讀 app 以此還原閱讀位置）：' README.md || { echo "README lost the paragraph id section"; bad=1; }
  grep -qF -- '--all --legacy-rebuild' README.md || { echo "README lost the builder rule"; bad=1; }
  grep -qF -- '--diagnostic-output' README.md || bad=1
  grep -qF -- '--ranges-only' README.md || bad=1
  return $bad
}

# ---------- M1: the merge and the three shared libraries ----------
check "M1: the merge of codex/batch1-69 is recorded"                    merge_recorded
check "M1: no unmerged path and no conflict marker"                     nothing_unmerged
check "M1: site, translations, data and production files equal main"    frozen_equal_main
check "M1: the branch's modules, prompts and tests landed byte for byte" branch_files_landed
check "M1: main's 44 tests pass unchanged"                              main_tests
check "M1: every published page is still the rendering of its md"       pages_reproducible
check "M1: check_ranges accepts all 81 translations as before"          ranges_default_unchanged
check "M1: check_ranges strict modes and the multi-work ids"            ranges_strict_modes
check "M1: llm model names"                                             llm_names
check "M1: production queues and prompts are unchanged"                 production_queues
check "M1: llm: a claude limit on stdout is a limit"                    llm_claude_limit_on_stdout
check "M1: llm: a Codex answer is never read as a limit"                llm_codex_answer_is_not_a_limit
check "M1: llm: both callers of call_with_limit_retry work"             llm_retry_api
check "M1: llm: self-check"                                             llm_self_check
check "M1: builder: no unsealed write into docs/ from the CLI"          builder_cli_guard
check "M1: builder: legacy diagnostic equals the published page"        builder_diagnostic_legacy
check "M1: builder: strict ranges, both term styles, ids in both"       builder_api
# ---------- M2: the audited pipeline, isolated ----------
check "M2: the branch's 112 tests are all present and pass"             branch_tests
check "M2: the audited pipeline has its own job folder and prompts"     audited_is_isolated
check "M2: a rewrite keeps the paragraph ids"                           audited_keeps_paragraph_ids
check "M2: the fake backends of the legacy audit"                       fake_finding_models
# ---------- M3: CI and README ----------
check "M3: .github/workflows/ci.yml is the reviewed file"               ci_workflow
check "M3: README documents the audited pipeline and keeps both sides"  readme_documents_the_pipeline
# ---------- M4: the legacy audit, end to end ----------
check "M4: the audit command and its options exist"                     audited_cli
check "M4: the 11 acceptance tests of the legacy audit pass"            audit_tests
check "M4: the suite that CI runs holds every test"                     suite_size
check "M4: no test left a file in the checkout"                         checkout_still_clean
exit $fail
