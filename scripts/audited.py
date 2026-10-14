#!/usr/bin/env python3
"""The audited pipeline: translate, review, attest, build and publish T1579 juans, or audit published ones.

This is a pipeline of its own, next to scripts/runner.py (the production scheduler, which it does not
change). It keeps its jobs in jobs/audited/, its prompts in prompts/audited/ and its records in audits/
and artifacts/, so it never claims a job of the production scheduler. Both take locks/runner.pid: only
one of them runs at a time. It supports T1579 only.

Usage:
  python3 scripts/audited.py enqueue --juans 13-15 --model auto [--no-push]
  python3 scripts/audited.py enqueue --link https://cbetaonline.dila.edu.tw/zh/T1579_013 --model dual
  python3 scripts/audited.py run
  python3 scripts/audited.py audit --juans 67-72
  python3 scripts/audited.py audit --resume <job id>

`enqueue` and `run` are the full pipeline: every model call is recorded, a review quotes the source and
the translation, and a juan is sealed after its quality gate. State lives in jobs/audited/*.json (atomic
writes); a crash resumes from the last saved step. Volumes within one job execute serially; external
orchestration may inspect the requested worker limit recorded by enqueue.

`audit` reviews a published translation without translating it again. It never commits and never pushes.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import io
import json
import os
import re
import sys
import threading
import time
import unicodedata
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from string import Template

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import llm
import audit
import publisher
import build_translation_html as bth
import build_shengwen_di_html as bsh
from make_translation_skeleton import extract_lines, read_segments, render as render_skeleton, line_key
from check_translation_terms import validate_glossary, check_translation_terms as term_issues, parse_entries as parse_term_entries
from check_translation_coverage import (
    canonical_source,
    check_coverage_ledger,
    check_ranges,
    data_line_ids,
    lines_in_range,
    ordered_line_ids,
    parse_ranges,
    range_overlaps,
)

SUPPORTED_WORK = "T1579"  # the glossary, the tooltips and the prompts of this pipeline are T1579 only
JOBS_DIR = ROOT / "jobs" / "audited"  # never jobs/: that folder belongs to scripts/runner.py
LOCKS_DIR = ROOT / "locks"
PROMPTS_DIR = ROOT / "prompts" / "audited"
WORKS_PATH = ROOT / "works.json"
GLOSSARY_PATH = ROOT / "translations" / "glossary" / "T1579-terms.json"
STATUS_JSON = LOCKS_DIR / "audited-status.json"  # never docs/status.json: the production runner publishes that
REVIEW_PASSES = ("review_terms", "review_doctrine", "review_parallel")
POLL_SECONDS = 10
VOLUME_STATES = {"queued", "running", "waiting_limit", "done", "failed", "cancelled"}
TERMINAL_VOLUME_STATES = {"done", "failed", "cancelled"}

@dataclass(frozen=True)
class StageSpec:
    model: str
    effort: str
    contract: str


STAGE_SPECS = {
    "segment": StageSpec("gpt-5.6-terra", "high", "segment"),
    "translate": StageSpec("gpt-5.6-terra", "high", "translation"),
    "draft_codex": StageSpec("gpt-5.6-terra", "high", "translation"),
    "draft_glm": StageSpec("gpt-5.6-terra", "high", "translation"),
    "merge": StageSpec("gpt-5.6-sol", "high", "translation"),
    "review_terms": StageSpec("gpt-5.6-sol", "high", "review"),
    "review_doctrine": StageSpec("gpt-5.6-sol", "high", "review"),
    "review_parallel": StageSpec("gpt-5.6-sol", "high", "review"),
    "fix": StageSpec("gpt-5.6-sol", "high", "translation"),
    "repair": StageSpec("gpt-5.6-sol", "high", "translation"),
}
DUAL_STAGES = ("draft_codex", "draft_glm")
QUEUES = ("auto", "dual", "mix") + llm.FAKE_MODELS + ("dual-echo",)
ENQUEUE_MODES = ("auto", "dual") + llm.FAKE_MODELS + ("dual-echo",)


def work_info(work_id: str) -> dict:
    works = json.loads(WORKS_PATH.read_text(encoding="utf-8"))["works"]
    match = next((w for w in works if w["id"] == work_id), None)
    if match is None:
        raise ValueError(f"未收錄的經典：{work_id}（先在 works.json 建檔）")
    return match


def get_work(work_id: str) -> dict:
    match = work_info(work_id)
    if not match.get("pipeline_ready"):
        raise ValueError(f"{work_id} {match['title']} 已建檔，但 pipeline 尚未支援（排程任務後續處理）")
    return match


def prompt_vars(job: dict) -> dict:
    info = work_info(job["work"])
    return {"work": info["id"], "work_title": info["title"], "file_id": info["file_id"]}


def stage_model(job: dict, stage: str) -> str:
    if job["model"] in llm.AUDIT_FAKE_MODELS:
        return job["model"]  # an offline backend of the legacy audit answers every stage itself
    if job["model"] in llm.FAKE_MODELS or job["model"] == "dual-echo":
        return "echo"
    return STAGE_SPECS[stage].model


def stage_effort(stage: str, *, low_ratio: bool = False) -> str:
    if stage == "review_doctrine" and low_ratio:
        return "xhigh"
    return STAGE_SPECS[stage].effort


def is_dual(job: dict) -> bool:
    return job["model"] in ("dual", "dual-echo")

TRANSLATION_BLOCK_RE = re.compile(r"(Translation:\n<<<\n)(.*?)(\n?>>>)", re.DOTALL)
NOTE_BLOCK_RE = re.compile(r"(Note:\n<<<\n)(.*?)(\n?>>>)", re.DOTALL)
POLICY_PATH = PROMPTS_DIR / "common_policy.txt"
POLICY_VERSION = "T1579-auditable-v1"
AUDIT_JOB_KIND = "legacy_audit"  # a job of `audited.py audit`; a translation worker never claims it

TRANSLATION_STAGES = ("translate", "draft_codex", "draft_glm", "merge", "repair", "fix")
# The contract stage of a published translation that a legacy audit reviews. No model may answer with it.
LEGACY_CONTRACT_STAGE = "legacy"
TRANSLATION_NESTED_SHAPES = {
    "additions": ("clause_id", "text"),
    "negation_scope": ("clause_id", "text", "scope"),
    "references": ("clause_id", "expression", "referent"),
    "term_occurrences": ("clause_id", "term_id", "surface"),
    "variants": ("clause_id", "text", "rationale"),
    "notes": ("clause_id", "text"),
}
STRUCTURED_CONTRACT_ANNOTATION_FIELDS = (
    "additions", "negation_scope", "references", "speakers",
    "term_occurrences", "variants", "notes",
)


def translation_schema(stage: str | None = None, unit_id_value: str | None = None,
                       source_hash_value: str | None = None,
                       clause_id_value: str | None = None) -> dict:
    def nonblank_string() -> dict:
        return {"type": "string", "pattern": r"\S"}

    def bound_string(value: str | None) -> dict:
        return {"const": value} if value is not None else nonblank_string()

    def nested_array(fields: tuple[str, ...]) -> dict:
        properties = {
            field: bound_string(clause_id_value) if field == "clause_id" else nonblank_string()
            for field in fields
        }
        return {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": list(fields),
                "properties": properties,
            },
        }

    clause_properties = {
        "clause_id": bound_string(clause_id_value),
        "literal": nonblank_string(),
        "vernacular": nonblank_string(),
        **{
            field: nested_array(fields)
            for field, fields in TRANSLATION_NESTED_SHAPES.items()
        },
        "speakers": {"type": ["object", "null"]},
    }
    addition_text = clause_properties["additions"]["items"]["properties"]["text"]
    addition_text["pattern"] = r"^(?=[^〔〕]*\S)[^〔〕]+$"
    addition_text["description"] = (
        "Inner text only. Do not include the 〔 or 〕 delimiters; "
        "vernacular contains exactly one 〔text〕 wrapper."
    )
    properties = {
        "schema_version": {"const": "1.0"},
        "stage": {"const": stage} if stage is not None else {"enum": list(TRANSLATION_STAGES)},
        "unit_id": bound_string(unit_id_value),
        "source_hash": ({"const": source_hash_value} if source_hash_value is not None
                        else {"type": "string", "pattern": "^[0-9a-f]{64}$"}),
        "clauses": {
            "type": "array", "minItems": 1, "maxItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": list(clause_properties),
                "properties": clause_properties,
            },
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(properties),
        "properties": properties,
    }


TRANSLATION_SCHEMA = translation_schema()
REVIEW_EVIDENCE_FIELDS = ("source_quote", "translation_quote", "reference_ids")
REVIEW_FINDING_FIELDS = (
    "clause_id", "severity", "category", "claim", "evidence", "required_change",
)


def normalized_reference_ids(values: set[str]) -> set[str]:
    return {
        value.strip() for value in values
        if isinstance(value, str) and value.strip()
    }


def review_schema(stage: str | None = None, unit_id_value: str | None = None,
                  source_hash_value: str | None = None,
                  clause_id_value: str | None = None,
                  allowed_reference_ids: set[str] | None = None) -> dict:
    def nonblank_string() -> dict:
        return {"type": "string", "pattern": r"\S"}

    def bound_string(value: str | None) -> dict:
        return {"const": value} if value is not None else nonblank_string()

    normalized_allowed_ids = (
        normalized_reference_ids(allowed_reference_ids)
        if allowed_reference_ids is not None else None
    )
    reference_ids = {
        "type": "array",
        "items": nonblank_string(),
    }
    if stage in {"review_terms", "review_doctrine"}:
        reference_ids["maxItems"] = 0
    elif stage == "review_parallel" and normalized_allowed_ids is not None:
        if normalized_allowed_ids:
            reference_ids["items"] = {"enum": sorted(normalized_allowed_ids)}
        else:
            reference_ids["maxItems"] = 0

    evidence_properties = {
        "source_quote": nonblank_string(),
        "translation_quote": nonblank_string(),
        "reference_ids": reference_ids,
    }
    evidence_properties["source_quote"]["description"] = (
        "Copy verbatim from clause.source as a single contiguous literal substring. "
        "Preserve original whitespace and line breaks. Do not use ellipsis, paraphrase, "
        "or concatenation."
    )
    evidence_properties["translation_quote"]["description"] = (
        "Copy verbatim from clause.translation as a single contiguous literal substring. "
        "Preserve original whitespace and line breaks. Do not use ellipsis, paraphrase, "
        "or concatenation."
    )
    finding_properties = {
        "clause_id": bound_string(clause_id_value),
        "severity": {"enum": ["crit", "high", "med", "low"]},
        "category": nonblank_string(),
        "claim": nonblank_string(),
        "evidence": {
            "type": "object",
            "additionalProperties": False,
            "required": list(REVIEW_EVIDENCE_FIELDS),
            "properties": evidence_properties,
        },
        "required_change": nonblank_string(),
    }
    findings = {
        "type": "array",
        "items": {
            "type": "object",
            "additionalProperties": False,
            "required": list(REVIEW_FINDING_FIELDS),
            "properties": finding_properties,
        },
    }
    no_parallel_evidence = stage == "review_parallel" and normalized_allowed_ids == set()
    if no_parallel_evidence:
        findings["maxItems"] = 0

    if no_parallel_evidence:
        verdict = {"const": "not_checked"}
    elif stage is None:
        verdict = {"enum": ["pass", "changes_required", "not_checked"]}
    elif stage == "review_parallel":
        verdict = {"enum": ["pass", "changes_required", "not_checked"]}
    else:
        verdict = {"enum": ["pass", "changes_required"]}
    proposed_terms = {"type": "array"}
    if stage is not None and stage != "review_terms":
        proposed_terms["maxItems"] = 0
    properties = {
        "schema_version": {"const": "1.0"},
        "stage": {"const": stage} if stage is not None else {"enum": list(REVIEW_PASSES)},
        "unit_id": bound_string(unit_id_value),
        "source_hash": ({"const": source_hash_value} if source_hash_value is not None
                        else {"type": "string", "pattern": "^[0-9a-f]{64}$"}),
        "verdict": verdict,
        "findings": findings,
        "proposed_terms": proposed_terms,
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "stage", "unit_id", "source_hash", "verdict", "findings"],
        "properties": properties,
        "oneOf": [
            {
                "properties": {
                    "verdict": {"const": "pass"},
                    "findings": {"maxItems": 0},
                },
            },
            {
                "properties": {
                    "verdict": {"const": "changes_required"},
                    "findings": {"minItems": 1},
                },
            },
            {
                "properties": {
                    "verdict": {"const": "not_checked"},
                    "findings": {"maxItems": 0},
                },
            },
        ],
    }


REVIEW_SCHEMA = review_schema()
SEGMENT_SCHEMA = {
    "type": "object",
    "required": ["schema_version", "stage", "work", "juan", "source_hash", "segments"],
    "properties": {
        "schema_version": {"const": "1.0"},
        "stage": {"const": "segment"},
        "segments": {
            "type": "array", "minItems": 1,
            "items": {
                "type": "object",
                "required": ["title", "start_line_id", "end_line_id", "note"],
            },
        },
    },
}


# ---------- small utilities ----------

def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.rename(tmp, path)


@contextmanager
def flock(name: str):
    LOCKS_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOCKS_DIR / f"{name}.lock", "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def volume_key(juan: int | str) -> str:
    return str(int(juan))


def initial_volumes(juans: list[int]) -> dict[str, dict]:
    return {volume_key(juan): {"state": "queued"} for juan in juans}


def inferred_volume_state(job: dict, juan: int) -> str:
    key = volume_key(juan)
    progress = job.get("progress", {}).get(key, {})
    if progress.get("error"):
        return "failed"
    if progress.get("step") == "done":
        return "done"
    if job.get("state") == "cancelled":
        return "cancelled"
    if job.get("state") == "done":
        return "done"
    if job.get("state") == "failed" and progress:
        return "failed"
    if progress:
        return "waiting_limit" if job.get("state") == "waiting_limit" else "running"
    return "queued"


def normalize_job(job: dict) -> dict:
    """Backfill per-volume state so old job files keep working."""
    job.setdefault("progress", {})
    volumes = job.setdefault("volumes", {})
    for juan in job.get("juans", []):
        key = volume_key(juan)
        volume = volumes.setdefault(key, {})
        state = volume.get("state") or inferred_volume_state(job, int(juan))
        volume["state"] = state if state in VOLUME_STATES else "queued"
    for key in list(volumes):
        if int(key) not in {int(juan) for juan in job.get("juans", [])}:
            volumes.pop(key, None)
    return job


def load_job(path: Path) -> dict:
    return normalize_job(json.loads(path.read_text(encoding="utf-8")))


def merge_cancelled_from_disk(job: dict) -> None:
    path = JOBS_DIR / f"{job['id']}.json"
    if not path.exists():
        return
    try:
        disk_job = normalize_job(json.loads(path.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError):
        return
    if disk_job.get("state") == "cancelled" and job.get("state") == "queued":
        job.clear()
        job.update(disk_job)
        return
    normalize_job(job)
    for juan, disk_volume in disk_job.get("volumes", {}).items():
        if disk_volume.get("state") != "cancelled":
            continue
        volume = job["volumes"].get(juan)
        if volume and volume.get("state") == "queued":
            volume.update(disk_volume)


def save_job(job: dict) -> None:
    merge_cancelled_from_disk(job)
    normalize_job(job)
    job["updated"] = now_iso()
    atomic_write(JOBS_DIR / f"{job['id']}.json", json.dumps(job, ensure_ascii=False, indent=1))
    publish_status()


def publish_status() -> None:
    jobs = []
    for path in sorted(JOBS_DIR.glob("*.json")):
        try:
            job = load_job(path)
        except (json.JSONDecodeError, OSError):
            continue
        job.pop("pid", None)
        job.pop("_current_juan", None)
        jobs.append(job)
    STATUS_JSON.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(STATUS_JSON, json.dumps({"generated": now_iso(), "jobs": jobs}, ensure_ascii=False))


def log(job: dict, msg: str) -> None:
    print(f"{now_iso()} [{job['id']}] {msg}", flush=True)


# The glossary, the policy and the prompt files as one legacy audit pinned them; None: read the files.
PINNED_REVIEW_INPUTS: dict[Path, bytes] | None = None


def review_input_bytes(path: Path) -> bytes:
    """A review input (glossary, policy, prompt file): the pinned bytes while an audit holds them, else the file.

    While an audit holds a pin, an input that is not in it is refused and not read from its file: a
    version that the gate does not store must not reach a prompt or a check.
    """
    if PINNED_REVIEW_INPUTS is None:
        return path.read_bytes()
    if path not in PINNED_REVIEW_INPUTS:
        raise AuditBlocked([f"the review input {path.name} is not among the inputs that the audit pinned"])
    return PINNED_REVIEW_INPUTS[path]


def review_input_text(path: Path) -> str:
    """The same input as Path.read_text gives it: UTF-8 with universal newlines."""
    return review_input_bytes(path).decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")


@contextmanager
def pinned_review_inputs():
    """Read the glossary, the policy and every prompt file once; inside, every prompt and check uses those bytes.

    The last check of a legacy audit and its gate run inside. So the version that the gate stores is the
    version that every review answered, whatever happens to the files meanwhile. The audit command is one
    thread and holds locks/runner.pid, so no worker shares this module state.
    """
    global PINNED_REVIEW_INPUTS
    # The files that the last check and the gate read are named: one that is missing fails the audit here,
    # whatever a listing of the folder shows. Every other prompt file is pinned and stored with them.
    required = (GLOSSARY_PATH, POLICY_PATH, *(PROMPTS_DIR / f"{name}.txt" for name in AUDIT_REVIEW_PASSES))
    PINNED_REVIEW_INPUTS = {path: path.read_bytes() for path in (*required, *sorted(PROMPTS_DIR.glob("*.txt")))}
    try:
        yield
    finally:
        PINNED_REVIEW_INPUTS = None


def prompt_text(name: str, **vars) -> str:
    policy = review_input_text(POLICY_PATH).strip()
    stage = Template(review_input_text(PROMPTS_DIR / f"{name}.txt")).substitute(**vars)
    return f"{policy}\n\n---\n\n{stage.strip()}\n"


def json_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def source_hash(source: str) -> str:
    return audit.sha256_text(source)


def unit_id(job: dict, juan: int, index: int) -> str:
    return f"{job['work']}-{juan:03d}-{index + 1:03d}"


def clause_id(entry) -> str:
    return entry.range_label


def parse_json_contract(raw: str, stage: str) -> dict:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as err:
        raise ValueError(f"{stage}: output is not one JSON object: {err}") from err
    if not isinstance(payload, dict):
        raise ValueError(f"{stage}: output must be a JSON object")
    if payload.get("schema_version") != "1.0" or payload.get("stage") != stage:
        raise ValueError(f"{stage}: schema_version/stage mismatch")
    return payload


def validate_translation_contract(payload: dict, stage: str, expected_unit: str,
                                  expected_hash: str, expected_clause: str) -> dict:
    payload_fields = {"schema_version", "stage", "unit_id", "source_hash", "clauses"}
    if set(payload) != payload_fields:
        raise ValueError(f"{stage}: translation output contains missing or extra fields")
    if payload.get("unit_id") != expected_unit or payload.get("source_hash") != expected_hash:
        raise ValueError(f"{stage}: unit_id/source_hash mismatch")
    clauses = payload.get("clauses")
    if not isinstance(clauses, list) or len(clauses) != 1:
        raise ValueError(f"{stage}: expected exactly one clause")
    clause = clauses[0]
    required = {"clause_id", "literal", "vernacular", "speakers", *TRANSLATION_NESTED_SHAPES}
    if not isinstance(clause, dict) or set(clause) != required:
        raise ValueError(f"{stage}: clause is missing required fields")
    if clause.get("clause_id") != expected_clause:
        raise ValueError(f"{stage}: clause_id mismatch")
    if any(not isinstance(clause.get(field), str) or not clause[field].strip()
           for field in ("literal", "vernacular")):
        raise ValueError(f"{stage}: literal and vernacular must be non-empty text")
    for key in TRANSLATION_NESTED_SHAPES:
        if not isinstance(clause[key], list):
            raise ValueError(f"{stage}: {key} must be an array")
        if not all(isinstance(item, dict) for item in clause[key]):
            raise ValueError(f"{stage}: {key} items must be objects")
    for key, ordered_fields in TRANSLATION_NESTED_SHAPES.items():
        fields = set(ordered_fields)
        for item in clause[key]:
            if set(item) != fields or item.get("clause_id") != expected_clause:
                raise ValueError(f"{stage}: {key} item has an invalid shape")
            if any(not isinstance(item[field], str) or not item[field].strip()
                   for field in fields - {"clause_id"}):
                raise ValueError(f"{stage}: {key} item fields must be non-empty text")
    if clause["speakers"] is not None and not isinstance(clause["speakers"], dict):
        raise ValueError(f"{stage}: speakers must be an object or null")
    for addition in clause["additions"]:
        text = addition["text"]
        if "〔" in text or "〕" in text:
            raise ValueError(f"{stage}: addition text must not contain 〔 or 〕")
        if clause["vernacular"].count(f"〔{text}〕") != 1:
            raise ValueError(f"{stage}: every addition must appear exactly once inside 〔〕")
    return clause


def canonical_structured_contract(contract: dict, expected_unit: str, expected_hash: str,
                                  expected_clause: str, translation: str,
                                  *, error_stage: str) -> dict:
    if not isinstance(contract, dict):
        raise ValueError(f"{error_stage}: structured translation contract is missing")
    contract_stage = contract.get("stage")
    if contract_stage == LEGACY_CONTRACT_STAGE:
        # No model of this pipeline wrote the text, so the contract binds the text and nothing else.
        clause = contract.get("clause")
        if contract.get("source_hash") != expected_hash:
            raise ValueError(f"{LEGACY_CONTRACT_STAGE}: unit_id/source_hash mismatch")
        if not isinstance(clause, dict) or clause.get("clause_id") != expected_clause:
            raise ValueError(f"{LEGACY_CONTRACT_STAGE}: clause_id mismatch")
        if clause.get("vernacular") != translation:
            raise ValueError(f"{error_stage}: structured translation contract is stale")
        return {
            "stage": LEGACY_CONTRACT_STAGE,
            "source_hash": expected_hash,
            "translation_sha256": audit.sha256_text(translation),
            "clause_id": expected_clause,
            "annotations": {},
        }
    if contract_stage not in TRANSLATION_STAGES:
        raise ValueError(f"{error_stage}: structured translation contract stage is invalid")
    contract_payload = {
        "schema_version": "1.0",
        "stage": contract_stage,
        "unit_id": expected_unit,
        "source_hash": contract.get("source_hash"),
        "clauses": [contract.get("clause")],
    }
    clause = validate_translation_contract(
        contract_payload, contract_stage, expected_unit, expected_hash, expected_clause,
    )
    if clause["vernacular"] != translation:
        raise ValueError(f"{error_stage}: structured translation contract is stale")
    annotations = json.loads(json.dumps({
        field: clause[field] for field in STRUCTURED_CONTRACT_ANNOTATION_FIELDS
    }, ensure_ascii=False))
    return {
        "stage": contract_stage,
        "source_hash": expected_hash,
        "translation_sha256": audit.sha256_text(translation),
        "clause_id": expected_clause,
        "annotations": annotations,
    }


def structured_contract_sha256(contract: dict) -> str:
    canonical = json.dumps(
        contract, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    return audit.sha256_text(canonical)


def validate_review_contract(payload: dict, stage: str, expected_unit: str,
                             expected_hash: str, expected_clause: str, *,
                             source: str, translation: str,
                             allowed_reference_ids: set[str] | None = None) -> list[dict]:
    allowed = {"schema_version", "stage", "unit_id", "source_hash", "verdict", "findings",
               "proposed_terms"}
    required_payload = {"schema_version", "stage", "unit_id", "source_hash", "verdict", "findings"}
    if payload.keys() - allowed or required_payload - payload.keys():
        raise ValueError(f"{stage}: reviewer output contains forbidden fields")
    if payload.get("schema_version") != "1.0" or payload.get("stage") != stage:
        raise ValueError(f"{stage}: schema_version/stage mismatch")
    if payload.get("unit_id") != expected_unit or payload.get("source_hash") != expected_hash:
        raise ValueError(f"{stage}: unit_id/source_hash mismatch")
    if "proposed_terms" in payload and not isinstance(payload["proposed_terms"], list):
        raise ValueError(f"{stage}: proposed_terms must be an array")
    verdict = payload.get("verdict")
    findings = payload.get("findings")
    allowed_verdicts = {"pass", "changes_required"}
    normalized_allowed_ids = normalized_reference_ids(allowed_reference_ids or set())
    if stage == "review_parallel":
        allowed_verdicts = (
            {"pass", "changes_required", "not_checked"}
            if normalized_allowed_ids else {"not_checked"}
        )
    if verdict not in allowed_verdicts or not isinstance(findings, list):
        raise ValueError(f"{stage}: invalid verdict/findings")
    required = set(REVIEW_FINDING_FIELDS)
    for finding in findings:
        if not isinstance(finding, dict) or set(finding) != required:
            raise ValueError(f"{stage}: finding is missing required fields")
        if finding["clause_id"] != expected_clause or finding["severity"] not in {
            "crit", "high", "med", "low",
        }:
            raise ValueError(f"{stage}: finding clause/severity is invalid")
        evidence = finding["evidence"]
        evidence_fields = set(REVIEW_EVIDENCE_FIELDS)
        if not isinstance(evidence, dict) or set(evidence) != evidence_fields:
            raise ValueError(f"{stage}: finding requires source and translation evidence")
        source_quote = evidence["source_quote"]
        translation_quote = evidence["translation_quote"]
        reference_ids = evidence["reference_ids"]
        if (not isinstance(source_quote, str) or not source_quote.strip() or source_quote not in source
                or not isinstance(translation_quote, str) or not translation_quote.strip()
                or translation_quote not in translation):
            raise ValueError(f"{stage}: evidence quotes are not contained in the reviewed text")
        if (not isinstance(reference_ids, list)
                or not all(isinstance(item, str) and item.strip() for item in reference_ids)):
            raise ValueError(f"{stage}: evidence reference_ids must be strings")
        if (stage == "review_parallel"
                and not set(reference_ids).issubset(normalized_allowed_ids)):
            raise ValueError(f"{stage}: evidence reference_ids are outside the input allowlist")
        if stage != "review_parallel" and reference_ids:
            raise ValueError(f"{stage}: evidence reference_ids must be empty")
        for field in ("category", "claim", "required_change"):
            if not isinstance(finding[field], str) or not finding[field].strip():
                raise ValueError(f"{stage}: finding {field} must be non-empty text")
    if ((verdict in {"pass", "not_checked"} and findings)
            or (verdict == "changes_required" and not findings)):
        raise ValueError(f"{stage}: verdict does not match findings")
    if stage != "review_terms" and payload.get("proposed_terms"):
        raise ValueError(f"{stage}: only review_terms may propose terms")
    return findings


def contract_note(clause: dict) -> str | None:
    notes = []
    for item in clause["notes"]:
        if isinstance(item, dict) and item.get("text"):
            notes.append(str(item["text"]).strip())
        elif isinstance(item, str) and item.strip():
            notes.append(item.strip())
    return "\n\n".join(notes) or None


def save_clause_contract(prog: dict, current_unit: str, stage: str,
                         current_hash: str, clause: dict) -> None:
    prog.setdefault("clause_contracts", {})[current_unit] = {
        "stage": stage,
        "source_hash": current_hash,
        "clause": clause,
    }


def audit_store(job: dict) -> audit.AuditStore:
    return audit.AuditStore(ROOT, job["id"], job.get("batch_id"))


def set_volume_state(job: dict, juan: int, state: str, error: str | None = None) -> None:
    if state not in VOLUME_STATES:
        raise ValueError(f"unsupported volume state: {state}")
    volume = normalize_job(job)["volumes"][volume_key(juan)]
    volume["state"] = state
    volume["updated"] = now_iso()
    if error:
        volume["error"] = error[:500]
    elif state != "failed":
        volume.pop("error", None)


def queued_volumes(job: dict) -> list[int]:
    normalize_job(job)
    return [
        int(juan)
        for juan, volume in job["volumes"].items()
        if volume.get("state") == "queued"
    ]


def finalize_job_state(job: dict, failed: list[int]) -> None:
    normalize_job(job)
    states = [volume.get("state") for volume in job["volumes"].values()]
    if failed or "failed" in states:
        job["state"] = "failed"
        failed_set = sorted(set(failed + [
            int(juan) for juan, volume in job["volumes"].items()
            if volume.get("state") == "failed"
        ]))
        job["error"] = f"juans failed: {failed_set}"
    elif states and all(state == "cancelled" for state in states):
        job["state"] = "cancelled"
        job["error"] = None
    else:
        job["state"] = "done"
        job["error"] = None


def cancel_job(job: dict) -> None:
    normalize_job(job)
    if job.get("state") != "queued":
        raise ValueError("only queued jobs can be cancelled")
    for juan in job.get("juans", []):
        volume = job["volumes"][volume_key(juan)]
        if volume.get("state") == "queued":
            set_volume_state(job, juan, "cancelled")
    job["state"] = "cancelled"
    job["error"] = None
    job["pid"] = None


def cancel_volume(job: dict, juan: int) -> None:
    normalize_job(job)
    key = volume_key(juan)
    if key not in job["volumes"]:
        raise KeyError(f"juan {juan} is not in this job")
    if job["volumes"][key].get("state") != "queued":
        raise ValueError("only queued volumes can be cancelled")
    set_volume_state(job, juan, "cancelled")
    if job.get("state") == "queued" and not queued_volumes(job):
        finalize_job_state(job, [])


# ---------- CBETA / file paths ----------

def juan_paths(work: str, juan: int) -> dict[str, Path]:
    return {
        "data": ROOT / "data" / f"{work}-{juan:03d}.json",
        "tsv": ROOT / "translations" / "segments" / f"{work}-{juan:03d}.tsv",
        "md": ROOT / "translations" / f"{work}-{juan:03d}-baihua.md",
        "draft_codex": ROOT / "translations" / "drafts" / f"{work}-{juan:03d}-draft-codex.md",
        "draft_glm": ROOT / "translations" / "drafts" / f"{work}-{juan:03d}-draft-glm.md",
    }


def ensure_data(work: str, juan: int) -> Path:
    path = juan_paths(work, juan)["data"]
    path.parent.mkdir(exist_ok=True)
    bsh.fetch_json(f"{bsh.BASE_URL}/juans?work={work}&juan={juan}&toc=1&work_info=1", path)
    return path


def juan_bounds(lines: dict[str, str]) -> tuple[str, str]:
    ids = sorted((i for i, t in lines.items() if t), key=line_key)
    if not ids:
        raise RuntimeError("juan has no non-empty lines")
    return ids[0], ids[-1]


# ---------- glossary ----------

def load_glossary() -> dict:
    return json.loads(review_input_text(GLOSSARY_PATH))


def glossary_subset_json(glossary: dict, source: str) -> str:
    terms = [t for t in glossary["terms"] if any(s in source for s in t["source_terms"])]
    return json.dumps(terms, ensure_ascii=False)


def append_new_terms(raw_json: str, job: dict) -> None:
    try:
        new_terms = json.loads(raw_json)
        assert isinstance(new_terms, list)
    except (json.JSONDecodeError, AssertionError):
        log(job, "NEW_TERMS unparsable, discarded")
        return
    with flock("glossary"):
        glossary = load_glossary()
        existing = {t["id"] for t in glossary["terms"]}
        added = [t for t in new_terms if isinstance(t, dict) and t.get("id") not in existing]
        candidate = {**glossary, "terms": glossary["terms"] + added}
        if added and not validate_glossary(candidate):
            atomic_write(GLOSSARY_PATH, json.dumps(candidate, ensure_ascii=False, indent=2))
            log(job, f"glossary +{len(added)} terms: {[t['id'] for t in added]}")
        elif added:
            log(job, "NEW_TERMS failed glossary validation, discarded")


# ---------- markdown section access ----------

def md_sections(text: str) -> list[dict]:
    """Sections with absolute char spans for translation/note block contents."""
    heads = list(re.finditer(r"^## .+$", text, re.MULTILINE))
    sections = []
    for i, head in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        chunk = text[head.start():end]
        tr = TRANSLATION_BLOCK_RE.search(chunk)
        nt = NOTE_BLOCK_RE.search(chunk)
        if not tr:
            raise ValueError(f"section missing Translation block: {head.group(0)}")
        sections.append({
            "title": head.group(0)[3:].strip(),
            "tr_span": (head.start() + tr.start(2), head.start() + tr.end(2)),
            "note_span": (head.start() + nt.start(2), head.start() + nt.end(2)) if nt else None,
        })
    return sections


def normalize_translation(text: str) -> str:
    """A translation as bth.parse_entries reads it back from the md: paragraphs joined by one blank line.

    Every accepted clause is normalized before it is saved and spliced. So the vernacular of the contract
    equals the translation of the md, also after paragraph id markers are written into the md.
    """
    return "\n\n".join("\n".join(content) for _pid, content in bth.marked_paragraphs(text))


def splice(md_path: Path, index: int, translation: str, note: str | None) -> None:
    text = md_path.read_text(encoding="utf-8")
    sec = md_sections(text)[index]
    # replace note first so translation span offsets stay valid (note comes after)
    if note is not None and sec["note_span"]:
        s, e = sec["note_span"]
        text = text[:s] + note.strip() + text[e:]
    s, e = sec["tr_span"]
    text = text[:s] + bth.carry_paragraph_ids(text[s:e], translation) + text[e:]  # keep paragraph ids
    atomic_write(md_path, text)


# ---------- pipeline steps ----------

def llm_call(job: dict, prompt: str, stage: str, *, context: dict | None = None,
             effort: str | None = None) -> str:
    context = context or {}
    model = stage_model(job, stage)
    effort = effort or stage_effort(stage)
    call_id = uuid.uuid4().hex
    store = audit_store(job)
    request_ref = store.artifact("request", prompt)
    # Read when the request is made: a call can wait for hours on a limit, and the file may change meanwhile.
    policy_sha256 = audit.sha256_text(review_input_text(POLICY_PATH))

    def on_wait(resume_at):
        job["state"] = "waiting_limit"
        job["resume_at"] = resume_at.isoformat(timespec="seconds")
        current_juan = job.get("_current_juan")
        if current_juan is not None:
            set_volume_state(job, int(current_juan), "waiting_limit")
        save_job(job)

    def on_attempt(result: llm.LLMResult, attempt: int) -> None:
        artifacts = {"request": request_ref}
        if result.text:
            artifacts["response"] = store.artifact("response", result.text)
        if result.error:
            artifacts["error"] = store.artifact("error", result.error)
        availability = dict(result.metadata_availability)
        usage = result.usage
        tokens = {
            "input": None, "cached_input": None, "output": None,
            "reasoning": None, "total": None,
        }
        if isinstance(usage, dict):
            tokens = {
                "input": usage.get("input_tokens"),
                "cached_input": usage.get("cached_input_tokens"),
                "output": usage.get("output_tokens"),
                "reasoning": usage.get("reasoning_tokens"),
                "total": usage.get("total_tokens"),
            }
        retry_reason = None
        if not result.ok:
            reason_code = "rate_limited" if result.limit else "llm_error"
            safe_error = audit.redact_secrets(result.error).strip()
            retry_reason = f"{reason_code}: {safe_error}" if safe_error else reason_code
        record = store.append({
            "type": "llm_attempt",
            "call_id": call_id,
            "attempt": attempt,
            "semantic_attempt": context.get("semantic_attempt"),
            "volume": context.get("volume"),
            "unit_id": context.get("unit_id"),
            "clause_ids": context.get("clause_ids", []),
            "stage": stage,
            "model": result.model or model,
            "effort": result.effort,
            "sent_at": result.sent_at,
            "first_response_at": result.first_response_at,
            "received_at": result.received_at,
            "duration_ms": result.duration_ms,
            "request_id": result.request_id,
            "status": "ok" if result.ok else "rate_limited" if result.limit else "error",
            "exit_code": result.exit_code,
            "rate_limit": result.limit,
            "retry_reason": retry_reason,
            "provider_resume_at": (
                result.provider_resume_at.isoformat()
                if result.provider_resume_at is not None else None
            ),
            "effective_resume_at": (
                result.effective_resume_at.isoformat()
                if result.effective_resume_at is not None else None
            ),
            "rate_limit_decision": result.resume_decision,
            "usage": usage,
            "tokens": tokens,
            "availability_reason": availability,
            "hashes": {
                "policy_sha256": policy_sha256,
                "prompt_sha256": audit.sha256_text(prompt),
                "bound_prompt_sha256": context.get("bound_prompt_sha256"),
                "source_sha256": context.get("source_hash"),
                "glossary_sha256": context.get("glossary_hash"),
                "structured_contract_sha256": context.get("structured_contract_sha256"),
            },
            "policy_version": POLICY_VERSION,
            "artifacts": artifacts,
            "events_artifact": None,
        })
        context.setdefault("_audit_event_ids", []).append(record["event_id"])

    text = llm.call_with_limit_retry(
        model, prompt, on_wait=on_wait, log=lambda m: log(job, m),
        effort=effort, on_attempt=on_attempt,
    )
    if job["state"] == "waiting_limit":
        job["state"] = "running"
        job["resume_at"] = None
        current_juan = job.get("_current_juan")
        if current_juan is not None:
            set_volume_state(job, int(current_juan), "running")
        save_job(job)
    return text


def semantic_retry_prompt(original_prompt: str, validator_error: str,
                          previous_invalid_output_sha256: str, stage: str,
                          expected_unit: str, expected_hash: str,
                          expected_clause: str) -> str:
    identities = {
        "stage": stage,
        "unit_id": expected_unit,
        "source_hash": expected_hash,
        "clause_id": expected_clause,
    }
    return (
        f"<original_bound_prompt>\n{original_prompt}\n</original_bound_prompt>\n\n"
        "<semantic_retry_instruction>\n"
        "前次 JSON 未通過 semantic contract validation。請依上方原始 bound schema 完整重做。\n\n"
        f"validator_error: {validator_error}\n"
        f"previous_invalid_output_sha256: {previous_invalid_output_sha256}\n\n"
        f"expected_identities:\n{json_text(identities)}\n\n"
        "修正指令：只輸出一個完整 JSON object，不得使用 Markdown code fence 或附加說明；"
        "必須保留 schema_version、stage、unit_id、source_hash、clauses 的完整 envelope；"
        "clauses 必須恰有一項，主 clause_id 與每一個 nested item 的 clause_id 都必須完全等於"
        " expected_identities.clause_id；references: clause_id, expression, referent；"
        "negation_scope: clause_id, text, scope；其餘 nested arrays 必須依上方 bound schema 的 exact keys；"
        "不得把完整 range 縮成單一 line id。\n"
        "</semantic_retry_instruction>"
    )


def semantic_translation_call(job: dict, prompt: str, stage: str, *, context: dict,
                              expected_unit: str, expected_hash: str,
                              expected_clause: str) -> dict:
    current_prompt = prompt
    for semantic_attempt in (1, 2):
        attempt_context = {
            key: value for key, value in context.items() if key != "_audit_event_ids"
        }
        attempt_context["semantic_attempt"] = semantic_attempt
        raw = llm_call(job, current_prompt, stage, context=attempt_context)
        llm_event_ids = attempt_context.get("_audit_event_ids", [])
        if not llm_event_ids:
            raise RuntimeError("semantic validation requires a linked LLM audit event")
        llm_event_id = llm_event_ids[-1]
        try:
            payload = parse_json_contract(raw, stage)
            clause = validate_translation_contract(
                payload, stage, expected_unit, expected_hash, expected_clause,
            )
        except ValueError as err:
            validator_error = audit.redact_secrets(str(err))
            audit_store(job).append({
                "type": "translation_contract_validation",
                "llm_event_id": llm_event_id,
                "semantic_attempt": semantic_attempt,
                "verdict": "fail",
                "validator_error": validator_error,
                "stage": stage,
                "unit_id": expected_unit,
                "clause_id": expected_clause,
                "source_hash": expected_hash,
            })
            if semantic_attempt == 2:
                raise
            current_prompt = semantic_retry_prompt(
                prompt, validator_error, audit.sha256_text(raw), stage,
                expected_unit, expected_hash, expected_clause,
            )
            continue
        audit_store(job).append({
            "type": "translation_contract_validation",
            "llm_event_id": llm_event_id,
            "semantic_attempt": semantic_attempt,
            "verdict": "pass",
            "validator_error": None,
            "stage": stage,
            "unit_id": expected_unit,
            "clause_id": expected_clause,
            "source_hash": expected_hash,
        })
        return clause
    raise AssertionError("semantic translation retry exhausted without a result")


def _is_grapheme_extend(char: str) -> bool:
    codepoint = ord(char)
    return (
        unicodedata.combining(char) != 0
        or unicodedata.category(char) in {"Mn", "Mc", "Me"}
        or 0xFE00 <= codepoint <= 0xFE0F
        or 0xE0100 <= codepoint <= 0xE01EF
        or 0x1F3FB <= codepoint <= 0x1F3FF
    )


def _grapheme_safe_chunks(text: str, max_chars: int) -> list[str]:
    if max_chars < 1:
        raise ValueError("max_chars must be positive")
    clusters = []
    current_cluster = ""
    for char in text:
        if (not current_cluster or _is_grapheme_extend(char)
                or char == "\u200d" or current_cluster.endswith("\u200d")):
            current_cluster += char
        else:
            clusters.append(current_cluster)
            current_cluster = char
    if current_cluster:
        clusters.append(current_cluster)

    chunks = []
    current_chunk = ""
    for cluster in clusters:
        if len(cluster) > max_chars:
            if current_chunk:
                chunks.append(current_chunk)
                current_chunk = ""
            continue
        if current_chunk and len(current_chunk) + len(cluster) > max_chars:
            chunks.append(current_chunk)
            current_chunk = cluster
        else:
            current_chunk += cluster
    if current_chunk:
        chunks.append(current_chunk)
    return chunks


def safe_evidence_candidates(text: str, *, max_chars: int = 48,
                             max_candidates: int = 64) -> list[str]:
    if max_candidates < 0:
        raise ValueError("max_candidates must not be negative")
    if max_candidates == 0:
        return []
    candidates = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        for chunk in _grapheme_safe_chunks(line, max_chars):
            candidate = chunk.strip()
            if candidate and candidate not in candidates:
                candidates.append(candidate)
            if len(candidates) >= max_candidates:
                return candidates
    return candidates


def semantic_review_retry_prompt(original_prompt: str, validator_error: str,
                                 previous_invalid_output_sha256: str, stage: str,
                                 expected_unit: str, expected_hash: str,
                                 expected_clause: str, source: str,
                                 translation: str) -> str:
    identities = {
        "stage": stage,
        "unit_id": expected_unit,
        "source_hash": expected_hash,
        "clause_id": expected_clause,
    }
    evidence_candidates = {
        "source_quote": safe_evidence_candidates(source),
        "translation_quote": safe_evidence_candidates(translation),
    }
    return (
        f"<original_bound_prompt>\n{original_prompt}\n</original_bound_prompt>\n\n"
        "<semantic_retry_instruction>\n"
        "前次 JSON 未通過 review contract validation。請依上方原始 bound schema 完整重做。\n\n"
        f"validator_error: {validator_error}\n"
        f"previous_invalid_output_sha256: {previous_invalid_output_sha256}\n\n"
        f"expected_identities:\n{json_text(identities)}\n\n"
        "以下候選只由 original bound clause.source / clause.translation 逐字產生，"
        "不含前次 invalid output。每一項都是可直接複製的 short, single-line, literal quote；"
        "不得串接不同候選：\n"
        f"<safe_evidence_candidates>\n{json_text(evidence_candidates)}\n"
        "</safe_evidence_candidates>\n\n"
        "修正指令：只輸出一個完整 JSON object，不得使用 Markdown code fence 或附加說明；"
        "必須保留 schema_version、stage、unit_id、source_hash、verdict、findings 的完整 envelope；"
        "每個 finding 的 clause_id 必須等於 expected_identities.clause_id；"
        "evidence.source_quote 與 evidence.translation_quote 必須分別逐字複製自 clause.source "
        "與 clause.translation，且各自是單一連續 literal substring。優先逐字採用上方與 finding "
        "相符的 short, single-line candidate；若候選語義不足，才從 bound text 另選短的單行連續"
        "substring。不得插入或刪除 whitespace/newline，不得使用省略號、不得改寫、不得串接多段。\n"
        "</semantic_retry_instruction>"
    )


def semantic_review_call(job: dict, prompt: str, stage: str, *, context: dict,
                         effort: str, expected_unit: str, expected_hash: str,
                         expected_clause: str, source: str, translation: str,
                         allowed_reference_ids: set[str]) -> tuple[dict, list[dict], str]:
    current_prompt = prompt
    for semantic_attempt in (1, 2):
        attempt_context = {
            key: value for key, value in context.items() if key != "_audit_event_ids"
        }
        attempt_context["semantic_attempt"] = semantic_attempt
        # A retry sends the bound prompt inside a retry envelope. Its event names the bound prompt too, so
        # a review that passed on its retry is known as an answer to that prompt.
        attempt_context["bound_prompt_sha256"] = audit.sha256_text(prompt)
        raw = llm_call(
            job, current_prompt, stage, context=attempt_context, effort=effort,
        )
        llm_event_ids = attempt_context.get("_audit_event_ids", [])
        if not llm_event_ids:
            raise RuntimeError("semantic validation requires a linked LLM audit event")
        llm_event_id = llm_event_ids[-1]
        try:
            payload = parse_json_contract(raw, stage)
            findings = validate_review_contract(
                payload, stage, expected_unit, expected_hash, expected_clause,
                source=source, translation=translation,
                allowed_reference_ids=allowed_reference_ids,
            )
        except ValueError as err:
            validator_error = audit.redact_secrets(str(err))
            audit_store(job).append({
                "type": "review_contract_validation",
                "llm_event_id": llm_event_id,
                "semantic_attempt": semantic_attempt,
                "verdict": "fail",
                "validator_error": validator_error,
                "stage": stage,
                "unit_id": expected_unit,
                "clause_id": expected_clause,
                "source_hash": expected_hash,
                "structured_contract_sha256": context.get(
                    "structured_contract_sha256"
                ),
            })
            if semantic_attempt == 2:
                raise
            current_prompt = semantic_review_retry_prompt(
                prompt, validator_error, audit.sha256_text(raw), stage,
                expected_unit, expected_hash, expected_clause, source, translation,
            )
            continue
        audit_store(job).append({
            "type": "review_contract_validation",
            "llm_event_id": llm_event_id,
            "semantic_attempt": semantic_attempt,
            "verdict": "pass",
            "validator_error": None,
            "stage": stage,
            "unit_id": expected_unit,
            "clause_id": expected_clause,
            "source_hash": expected_hash,
            "structured_contract_sha256": context.get(
                "structured_contract_sha256"
            ),
        })
        return payload, findings, llm_event_id
    raise AssertionError("semantic review retry exhausted without a result")


def llm_segment(job: dict, juan: int, lines: dict[str, str], tsv_path: Path) -> None:
    ordered = sorted(((i, t) for i, t in lines.items() if t), key=lambda kv: line_key(kv[0]))
    input_payload = {
        "schema_version": "1.0",
        "stage": "segment",
        "work": job["work"],
        "juan": juan,
        "source_hash": audit.sha256_text("\n".join(f"{i}\t{t}" for i, t in ordered)),
        "lines": [{"line_id": line_id, "text": text} for line_id, text in ordered],
    }
    prompt = prompt_text(
        "segment", input_json=json_text(input_payload), schema_json=json_text(SEGMENT_SCHEMA),
    )
    errors = ""
    for attempt in range(2):
        raw = llm_call(
            job, prompt + errors, "segment",
            context={"volume": juan, "source_hash": input_payload["source_hash"]},
        )
        try:
            payload = parse_json_contract(raw, "segment")
            if payload.get("work") != job["work"] or payload.get("juan") != juan:
                raise ValueError("segment: work/juan mismatch")
            if payload.get("source_hash") != input_payload["source_hash"]:
                raise ValueError("segment: source_hash mismatch")
            rows = []
            for segment in payload.get("segments", []):
                if not isinstance(segment, dict):
                    raise ValueError("segment: invalid segment object")
                start_line = segment["start_line_id"]
                end_line = segment["end_line_id"]
                raw_range = start_line if start_line == end_line else f"{start_line}-p{end_line.split('_p')[1]}"
                rows.append([str(segment["title"]), raw_range, str(segment.get("note", ""))])
            tsv_path.parent.mkdir(exist_ok=True)
            atomic_write(tsv_path, "# title\trange\tnote\n"
                         + "\n".join("\t".join(row) for row in rows) + "\n")
            try:
                segments = read_segments(tsv_path)
                start, end = juan_bounds(lines)
                issues = []
                if segments[0].start != start:
                    issues.append(f"first segment must start at {start}")
                if segments[-1].end != end:
                    issues.append(f"last segment must end at {end}")
                for seg in segments:
                    for line_id in (seg.start, seg.end):
                        if line_id not in lines:
                            issues.append(f"{seg.title}: {line_id} not in juan")
                if not issues:
                    log(job, f"juan {juan}: LLM segmented into {len(segments)} sections")
                    return
                errors = "\n\n前次輸出有誤，請修正：" + "；".join(issues)
            except ValueError as err:
                errors = f"\n\n前次輸出有誤，請修正：{err}"
        except (KeyError, TypeError, ValueError) as err:
            errors = f"\n\n前次輸出有誤，請修正：{err}"
        tsv_path.unlink(missing_ok=True)
        log(job, f"juan {juan}: segmentation attempt {attempt + 1} invalid")
    raise RuntimeError(f"juan {juan}: LLM segmentation failed twice")


def translate_sections(job: dict, juan: int, md_path: Path, prog: dict,
                       only_titles: set[str] | None = None, extra: str = "",
                       stage: str = "translate") -> None:
    glossary = load_glossary()
    glossary_json = json_text(glossary)
    entries = bth.parse_entries(md_path.read_text(encoding="utf-8"))
    prog["sections_total"] = len(entries)
    for idx, entry in enumerate(entries):
        current_unit = unit_id(job, juan, idx)
        current_clause = clause_id(entry)
        current_hash = source_hash(entry.source)
        if only_titles is not None and entry.title not in only_titles:
            continue
        contract = prog.get("clause_contracts", {}).get(current_unit, {})
        prior_clause = contract.get("clause", {})
        if (only_titles is None and entry.translation.strip()
                and contract.get("source_hash") == current_hash
                and prior_clause.get("clause_id") == current_clause
                and prior_clause.get("vernacular") == entry.translation):
            continue
        input_payload = {
            "schema_version": "1.0",
            "stage": stage,
            "work_profile": {**prompt_vars(job), "tradition": "Yogacara", "translator": "Xuanzang"},
            "unit_id": current_unit,
            "source_hash": current_hash,
            "context_before": entries[idx - 1].source if idx else "",
            "clauses": [{"clause_id": current_clause, "source": entry.source}],
            "context_after": entries[idx + 1].source if idx + 1 < len(entries) else "",
            "glossary": json.loads(glossary_subset_json(glossary, entry.source)),
            "existing_note": entry.note,
            "repair_instruction": extra,
        }
        clause = semantic_translation_call(
            job,
            prompt_text(
                "translate", input_json=json_text(input_payload),
                schema_json=json_text(translation_schema(
                    stage, current_unit, current_hash, current_clause,
                )),
            ),
            stage,
            context={
                "volume": juan, "unit_id": current_unit, "clause_ids": [current_clause],
                "source_hash": current_hash, "glossary_hash": audit.sha256_text(glossary_json),
            },
            expected_unit=current_unit, expected_hash=current_hash,
            expected_clause=current_clause,
        )
        clause["vernacular"] = normalize_translation(clause["vernacular"])
        save_clause_contract(prog, current_unit, stage, current_hash, clause)
        splice(md_path, idx, clause["vernacular"], contract_note(clause))
        prog["section"] = idx + 1
        save_job(job)
        log(job, f"juan {juan}: {stage} {idx + 1}/{len(entries)} {entry.title}")
        entries = bth.parse_entries(md_path.read_text(encoding="utf-8"))  # re-read after splice


def merge_sections(job: dict, juan: int, paths: dict[str, Path], prog: dict) -> None:
    """claude reviews both drafts against the source and settles the final text."""
    glossary = load_glossary()
    entries = bth.parse_entries(paths["md"].read_text(encoding="utf-8"))
    draft_a = bth.parse_entries(paths["draft_codex"].read_text(encoding="utf-8"))
    draft_b = bth.parse_entries(paths["draft_glm"].read_text(encoding="utf-8"))
    if not (len(entries) == len(draft_a) == len(draft_b)):
        raise RuntimeError(f"juan {juan}: draft/skeleton section counts differ "
                           f"({len(entries)}/{len(draft_a)}/{len(draft_b)})")
    prog["sections_total"] = len(entries)
    for idx, entry in enumerate(entries):
        current_unit = unit_id(job, juan, idx)
        current_clause = clause_id(entry)
        current_hash = source_hash(entry.source)
        contract = prog.get("clause_contracts", {}).get(current_unit, {})
        prior_clause = contract.get("clause", {})
        if (entry.translation.strip() and contract.get("stage") == "merge"
                and contract.get("source_hash") == current_hash
                and prior_clause.get("clause_id") == current_clause
                and prior_clause.get("vernacular") == entry.translation):
            continue
        glossary_subset = glossary_subset_json(glossary, entry.source)
        input_payload = {
            "schema_version": "1.0", "stage": "merge", "unit_id": current_unit,
            "source_hash": current_hash,
            "clauses": [{"clause_id": current_clause, "source": entry.source}],
            "draft_a": draft_a[idx].translation or "（缺稿）",
            "draft_b": draft_b[idx].translation or "（缺稿）",
            "glossary": json.loads(glossary_subset),
        }
        clause = semantic_translation_call(
            job,
            prompt_text("merge", input_json=json_text(input_payload),
                        schema_json=json_text(translation_schema(
                            "merge", current_unit, current_hash, current_clause,
                        ))),
            "merge",
            context={
                "volume": juan, "unit_id": current_unit, "clause_ids": [current_clause],
                "source_hash": current_hash, "glossary_hash": audit.sha256_text(glossary_subset),
            },
            expected_unit=current_unit, expected_hash=current_hash,
            expected_clause=current_clause,
        )
        clause["vernacular"] = normalize_translation(clause["vernacular"])
        save_clause_contract(prog, current_unit, "merge", current_hash, clause)
        splice(paths["md"], idx, clause["vernacular"], contract_note(clause))
        prog["section"] = idx + 1
        save_job(job)
        log(job, f"juan {juan}: merge {idx + 1}/{len(entries)} {entry.title}")
        entries = bth.parse_entries(paths["md"].read_text(encoding="utf-8"))


def translation_ratio(source: str, translation: str) -> float:
    source_size = len(re.sub(r"\s+", "", source))
    translated_size = len(re.sub(r"\s+", "", translation))
    return translated_size / source_size if source_size else 1.0


def review_is_reusable(attestation: object, events: dict[str, dict], *, stage: str, unit: str,
                       model: str, effort: str, prompt_sha256: str) -> bool:
    """A passing review may stand in for a new call only when it answered exactly this prompt.

    The prompt holds the source, the translation, the note, the glossary subset, the contract, the schema
    and the policy. So a change of any of them changes the hash, and the unit is reviewed again. A review
    that passed on its semantic retry answered this prompt inside the retry envelope: its event names it.
    """
    if not isinstance(attestation, dict) or attestation.get("verdict") != "pass":
        return False
    event = events.get(attestation.get("review_event_id"))
    return (
        isinstance(event, dict)
        and event.get("type") == "llm_attempt"
        and event.get("status") == "ok"
        and event.get("stage") == stage
        and event.get("unit_id") == unit
        and event.get("model") == model
        and event.get("effort") == effort
        and prompt_sha256 in ((event.get("hashes") or {}).get(key)
                              for key in ("prompt_sha256", "bound_prompt_sha256"))
    )


def review_pass(job: dict, juan: int, md_path: Path, pass_name: str,
                prog: dict, round_number: int, *, only_units: set[str] | None = None,
                reuse_attestations: bool = False, apply_proposed_terms: bool = True) -> list[dict]:
    """Run one review pass over the sections of a translation and return its findings.

    only_units: review these units only; the attestations of every other unit stay as they are.
    reuse_attestations: skip a unit whose passing review answered the prompt that would be sent now.
    apply_proposed_terms: when false, a proposed term is kept in prog["proposed_terms"] and the glossary
    file is not changed.
    """
    entries = bth.parse_entries(md_path.read_text(encoding="utf-8"))
    selected = [idx for idx in range(len(entries))
                if only_units is None or unit_id(job, juan, idx) in only_units]
    prepared = []
    existing_attestations = prog.get("review_attestations", {})
    if isinstance(existing_attestations, dict) and not reuse_attestations:
        for idx in selected:
            unit_attestations = existing_attestations.get(unit_id(job, juan, idx))
            if isinstance(unit_attestations, dict):
                unit_attestations.pop(pass_name, None)
    for idx in selected:
        entry = entries[idx]
        current_unit = unit_id(job, juan, idx)
        current_clause = clause_id(entry)
        current_hash = source_hash(entry.source)
        structured_contract = canonical_structured_contract(
            prog.get("clause_contracts", {}).get(current_unit),
            current_unit, current_hash, current_clause, entry.translation,
            error_stage=pass_name,
        )
        ratio = translation_ratio(entry.source, entry.translation)
        low_ratio = ratio < (1 / 3)
        warning = None
        if low_ratio:
            warning = {
                "unit_id": current_unit,
                "clause_id": current_clause,
                "translation_ratio": round(ratio, 4),
                "warning": "translation_ratio_below_one_third",
                "routing": {
                    "stage": "review_doctrine", "model": "gpt-5.6-sol", "effort": "xhigh",
                },
            }
        raw_evidence = job.get("review_evidence", {}).get(current_unit, [])
        evidence = json.loads(json.dumps([
            {**item, "source_id": item["source_id"].strip()}
            for item in raw_evidence
            if (isinstance(item, dict)
                and isinstance(item.get("source_id"), str)
                and item["source_id"].strip())
        ], ensure_ascii=False))
        allowed_reference_ids = {item["source_id"] for item in evidence}
        effort = stage_effort(pass_name, low_ratio=low_ratio)
        prepared.append({
            "idx": idx,
            "unit_id": current_unit,
            "clause_id": current_clause,
            "source_hash": current_hash,
            "source": entry.source,
            "translation": entry.translation,
            "note": entry.note,
            "low_ratio": low_ratio,
            "warning": warning,
            "review_evidence": evidence,
            "allowed_reference_ids": allowed_reference_ids,
            "structured_contract": structured_contract,
            "structured_contract_sha256": structured_contract_sha256(structured_contract),
            "effort": effort,
        })

    findings: list[dict] = []
    verdicts = []
    glossary = load_glossary()
    earlier_events = (
        {event["event_id"]: event for event in audit_store(job).events()} if reuse_attestations else {}
    )
    for item in prepared:
        idx = item["idx"]
        current_unit = item["unit_id"]
        current_clause = item["clause_id"]
        current_hash = item["source_hash"]
        glossary_subset = glossary_subset_json(glossary, item["source"])
        input_payload = {
            "schema_version": "1.0", "stage": pass_name, "unit_id": current_unit,
            "source_hash": current_hash,
            "clause": {
                "clause_id": current_clause, "source": item["source"],
                "translation": item["translation"], "note": item["note"],
            },
            "glossary": json.loads(glossary_subset) if pass_name != "review_parallel" else [],
            "review_evidence": (
                item["review_evidence"] if pass_name == "review_parallel" else []
            ),
            "translation_ratio_warning": item["low_ratio"],
            "structured_contract": item["structured_contract"],
        }
        call_context = {
            "volume": juan, "unit_id": current_unit, "clause_ids": [current_clause],
            "source_hash": current_hash,
            "glossary_hash": audit.sha256_text(glossary_subset),
            "structured_contract_sha256": item["structured_contract_sha256"],
        }
        bound_prompt = prompt_text(
            pass_name,
            input_json=json_text(input_payload),
            schema_json=json_text(review_schema(
                pass_name, current_unit, current_hash, current_clause,
                item["allowed_reference_ids"],
            )),
        )
        if reuse_attestations:
            unit_attestations = (existing_attestations.get(current_unit)
                                 if isinstance(existing_attestations, dict) else None)
            if not isinstance(unit_attestations, dict):
                unit_attestations = {}
            if review_is_reusable(
                    unit_attestations.get(pass_name), earlier_events, stage=pass_name,
                    unit=current_unit, model=stage_model(job, pass_name), effort=item["effort"],
                    prompt_sha256=audit.sha256_text(bound_prompt)):
                continue
            unit_attestations.pop(pass_name, None)
        payload, section_findings, review_event_id = semantic_review_call(
            job, bound_prompt, pass_name, context=call_context,
            effort=item["effort"], expected_unit=current_unit,
            expected_hash=current_hash, expected_clause=current_clause,
            source=item["source"], translation=item["translation"],
            allowed_reference_ids=item["allowed_reference_ids"],
        )
        if item["warning"] is not None:
            warnings = prog.setdefault("warnings", [])
            if item["warning"] not in warnings:
                warnings.append(item["warning"])
        verdicts.append({
            "round": round_number, "stage": pass_name, "unit_id": current_unit,
            "verdict": payload["verdict"], "finding_count": len(section_findings),
        })
        if payload["verdict"] == "pass":
            prog.setdefault("review_attestations", {}).setdefault(current_unit, {})[pass_name] = {
                "stage": pass_name,
                "verdict": "pass",
                "review_event_id": review_event_id,
                "model": stage_model(job, pass_name),
                "effort": item["effort"],
                "source_sha256": current_hash,
                "translation_sha256": audit.sha256_text(item["translation"]),
                "structured_contract_sha256": item["structured_contract_sha256"],
                "source_quote": item["source"],
                "translation_quote": item["translation"],
            }
        if pass_name == "review_terms" and payload.get("proposed_terms"):
            if apply_proposed_terms:
                append_new_terms(json.dumps(payload["proposed_terms"], ensure_ascii=False), job)
                glossary = load_glossary()
            else:
                proposed = prog.setdefault("proposed_terms", [])
                proposed.extend(term for term in payload["proposed_terms"] if term not in proposed)
        for finding in section_findings:
            findings.append({**finding, "unit_id": current_unit, "review_stage": pass_name,
                             "section_index": idx})
        prog["section"] = idx + 1
        save_job(job)
    prog.setdefault("review_verdicts", []).extend(verdicts)
    return findings


def fix_findings(job: dict, juan: int, md_path: Path, findings: list[dict], prog: dict) -> None:
    entries = bth.parse_entries(md_path.read_text(encoding="utf-8"))
    for idx in sorted({finding["section_index"] for finding in findings}):
        entry = entries[idx]
        current_unit = unit_id(job, juan, idx)
        current_clause = clause_id(entry)
        current_hash = source_hash(entry.source)
        section_findings = [
            {key: value for key, value in finding.items() if key != "section_index"}
            for finding in findings if finding["section_index"] == idx
        ]
        input_payload = {
            "schema_version": "1.0", "stage": "fix", "unit_id": current_unit,
            "source_hash": current_hash,
            "clause": {
                "clause_id": current_clause, "source": entry.source,
                "translation": entry.translation, "note": entry.note,
            },
            "findings": section_findings,
        }
        clause = semantic_translation_call(
            job,
            prompt_text("fix", input_json=json_text(input_payload),
                        schema_json=json_text(translation_schema(
                            "fix", current_unit, current_hash, current_clause,
                        ))),
            "fix",
            context={"volume": juan, "unit_id": current_unit, "clause_ids": [current_clause],
                     "source_hash": current_hash},
            expected_unit=current_unit, expected_hash=current_hash,
            expected_clause=current_clause,
        )
        clause["vernacular"] = normalize_translation(clause["vernacular"])
        save_clause_contract(prog, current_unit, "fix", current_hash, clause)
        splice(md_path, idx, clause["vernacular"], contract_note(clause))
        log(job, f"juan {juan}: fixer applied {len(section_findings)} findings to {entry.title}")
        entries = bth.parse_entries(md_path.read_text(encoding="utf-8"))
        prog["section"] = idx + 1
        save_job(job)


def run_review_chain(job: dict, juan: int, md_path: Path, prog: dict) -> None:
    first_findings = []
    for pass_name in REVIEW_PASSES:
        prog["step"] = pass_name
        prog["section"] = 0
        save_job(job)
        first_findings.extend(review_pass(job, juan, md_path, pass_name, prog, 1))
    if not first_findings:
        prog["review_verdict"] = "pass"
        return
    prog["step"] = "fix"
    prog["section"] = 0
    save_job(job)
    fix_findings(job, juan, md_path, first_findings, prog)
    remaining = []
    for pass_name in REVIEW_PASSES:
        prog["step"] = f"{pass_name}_regate"
        prog["section"] = 0
        save_job(job)
        remaining.extend(review_pass(job, juan, md_path, pass_name, prog, 2))
    if remaining:
        prog["review_verdict"] = "fail"
        prog["review_blocking_findings"] = remaining
        save_job(job)
        raise RuntimeError(f"juan {juan}: reviewer re-gate failed with {len(remaining)} findings")
    prog["review_verdict"] = "pass"


def write_coverage_ledger(job: dict, juan: int, md_path: Path, data_path: Path,
                          start: str, end: str, *, exact_once: bool = True) -> Path:
    prog = job["progress"].setdefault(str(juan), {})
    translation_text = md_path.read_text(encoding="utf-8")
    entries = bth.parse_entries(translation_text)
    ranges = parse_ranges(translation_text)
    source_lines = extract_lines(data_path)
    all_line_ids = ordered_line_ids(set(source_lines))
    expected_line_ids = lines_in_range(all_line_ids, start, end)
    issues = []
    clauses = []
    contracts = prog.get("clause_contracts", {})
    attestations = prog.get("review_attestations", {})
    review_events = {event["event_id"]: event for event in audit_store(job).events()}
    for idx, (entry, source_range) in enumerate(zip(entries, ranges)):
        current_unit = unit_id(job, juan, idx)
        contract = contracts.get(current_unit, {})
        line_ids = lines_in_range(expected_line_ids, source_range.start, source_range.end)
        try:
            structured_contract = canonical_structured_contract(
                contract, current_unit, source_hash(entry.source), clause_id(entry),
                entry.translation, error_stage="coverage",
            )
        except (TypeError, ValueError):
            contract_valid = False
            expected_contract_sha256 = None
        else:
            contract_valid = True
            expected_contract_sha256 = structured_contract_sha256(structured_contract)
        if not contract_valid:
            issues.append(f"{current_unit}: missing or stale structured translation contract")
        review_evidence = []
        for review_stage in ("review_terms", "review_doctrine"):
            attestation = attestations.get(current_unit, {}).get(review_stage, {})
            event = review_events.get(attestation.get("review_event_id"), {})
            expected_model = stage_model(job, review_stage)
            expected_effort = stage_effort(
                review_stage,
                low_ratio=translation_ratio(entry.source, entry.translation) < (1 / 3),
            )
            response_ref = event.get("artifacts", {}).get("response")
            try:
                response = json.loads(audit.read_artifact(ROOT, response_ref).decode("utf-8"))
            except (AttributeError, KeyError, OSError, ValueError, TypeError):
                response = {}
            response_valid = (
                response.get("stage") == review_stage
                and response.get("unit_id") == current_unit
                and response.get("source_hash") == source_hash(entry.source)
                and response.get("verdict") == "pass"
                and response.get("findings") == []
            )
            valid_attestation = (
                attestation.get("verdict") == "pass"
                and attestation.get("stage") == review_stage
                and attestation.get("model") == expected_model
                and attestation.get("effort") == expected_effort
                and attestation.get("source_sha256") == source_hash(entry.source)
                and attestation.get("translation_sha256") == audit.sha256_text(entry.translation)
                and attestation.get("structured_contract_sha256") == expected_contract_sha256
                and isinstance(attestation.get("source_quote"), str)
                and attestation["source_quote"] in entry.source
                and isinstance(attestation.get("translation_quote"), str)
                and attestation["translation_quote"] in entry.translation
                and event.get("stage") == review_stage
                and event.get("model") == expected_model
                and event.get("effort") == expected_effort
                and event.get("unit_id") == current_unit
                and event.get("clause_ids") == [clause_id(entry)]
                and event.get("hashes", {}).get("source_sha256") == source_hash(entry.source)
                and event.get("hashes", {}).get(
                    "structured_contract_sha256"
                ) == expected_contract_sha256
                and event.get("status") == "ok"
                and isinstance(response_ref, dict)
                and response_valid
            )
            if not valid_attestation:
                issues.append(f"{current_unit}: missing or stale {review_stage} attestation")
                continue
            review_evidence.append({
                "type": "reviewer_attestation",
                "stage": review_stage,
                "model": attestation["model"],
                "review_event_id": attestation["review_event_id"],
                "source_quote": attestation["source_quote"],
                "translation_quote": attestation["translation_quote"],
            })
        valid = contract_valid and len(review_evidence) == 2
        clauses.append({
            "clause_id": clause_id(entry),
            "source_line_ids": line_ids,
            "source_hash": audit.sha256_text(canonical_source(source_lines, line_ids)),
            "status": "covered" if valid else "missing",
            "evidence": review_evidence if valid else [],
        })
    if len(entries) != len(ranges):
        issues.append("translation entries and source ranges differ")
    ledger = {
        "schema_version": "1.0",
        "work": job["work"],
        "juan": juan,
        "source_hash": audit.sha256_text(canonical_source(source_lines, expected_line_ids)),
        "translation_hash": audit.sha256_text(translation_text),
        "clauses": clauses,
        "passed": not issues,
        "issues": issues,
    }
    if not exact_once:
        # A legacy audit: adjacent sections of a published translation may share their boundary line.
        ledger["coverage"] = "at_least_once"
    path = audit_store(job).job_dir / f"{job['work']}-{juan:03d}-coverage.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json_text(ledger) + "\n")
    prog["coverage_ledger"] = {
        "path": path.relative_to(ROOT).as_posix(),
        "sha256": audit.sha256_text(path.read_text(encoding="utf-8")),
    }
    return path


def run_content_checks(md_path: Path, data_path: Path, start: str, end: str, *,
                       exact_once: bool = True) -> list[str]:
    translation_text = md_path.read_text(encoding="utf-8")
    source_lines = extract_lines(data_path)
    issues = list(check_ranges(
        parse_ranges(translation_text), data_line_ids(data_path), start, end,
        source_lines=source_lines, exact_once=exact_once,
    ))
    glossary = load_glossary()
    issues += validate_glossary(glossary) or term_issues(
        glossary, parse_term_entries(translation_text))
    return issues


def run_checks(job: dict, juan: int, md_path: Path, data_path: Path,
               start: str, end: str, *, exact_once: bool = True) -> list[str]:
    translation_text = md_path.read_text(encoding="utf-8")
    source_lines = extract_lines(data_path)
    issues = run_content_checks(md_path, data_path, start, end, exact_once=exact_once)
    ledger_info = job.get("progress", {}).get(str(juan), {}).get("coverage_ledger", {})
    ledger_path = ROOT / ledger_info.get("path", "") if ledger_info.get("path") else None
    if not ledger_path or not ledger_path.is_file():
        issues.append("mandatory coverage ledger is missing")
    else:
        try:
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as err:
            issues.append(f"mandatory coverage ledger is unreadable: {err}")
        else:
            issues += check_coverage_ledger(ledger, source_lines, start, end, translation_text,
                                            exact_once=exact_once)
    return issues


def review_input_artifacts(store: audit.AuditStore) -> dict[str, dict]:
    """The glossary, the policy and every prompt file, stored as artifacts: the pinned bytes, else the files now."""
    prompts = (sorted(PROMPTS_DIR.glob("*.txt")) if PINNED_REVIEW_INPUTS is None
               else sorted(path for path in PINNED_REVIEW_INPUTS if path.parent == PROMPTS_DIR))
    artifacts = {
        "glossary": store.artifact("glossary", review_input_bytes(GLOSSARY_PATH)),
        "policy": store.artifact("policy", review_input_bytes(POLICY_PATH)),
    }
    for path in prompts:
        artifacts[f"prompt:{path.name}"] = store.artifact("prompt", review_input_bytes(path))
    return artifacts


def record_quality_gate(job: dict, juan: int, md_path: Path, issues: list[str], *,
                        audit_kind: str | None = None) -> None:
    """Record the blocking gate of a juan. audit_kind marks the gate of a legacy audit.

    A legacy audit also stores the review inputs it used: the bytes that it pinned for its last check.
    Its seal binds those artifacts, not the live files, which the audit of the next juan or a production
    job may change.
    """
    prog = job["progress"].setdefault(str(juan), {})
    if prog.get("review_verdict") != "pass":
        issues = [*issues, "review chain did not produce a passing re-gate verdict"]
    gate = {
        "passed": not issues,
        "checked_at": now_iso(),
        "translation_sha256": audit.sha256_text(md_path.read_text(encoding="utf-8")),
        "blocking_issues": issues,
        "warnings": prog.get("warnings", []),
        "review_verdict": prog.get("review_verdict", "not_run"),
    }
    prog["quality_gate"] = gate
    store = audit_store(job)
    event = {
        "type": "quality_gate",
        "volume": juan,
        "verdict": "pass" if gate["passed"] else "fail",
        "blocking": True,
        "translation_sha256": gate["translation_sha256"],
        "review_verdict": gate["review_verdict"],
        "issues": issues,
        "warnings": gate["warnings"],
        "artifacts": {"translation": store.artifact(
            "translation", md_path.read_text(encoding="utf-8"),
        )},
    }
    if audit_kind is not None:
        event["audit_kind"] = audit_kind
        event["artifacts"].update(review_input_artifacts(store))
    store.append(event)
    save_job(job)


def create_volume_attestation(job: dict, juan: int, md_path: Path, data_path: Path) -> Path:
    prog = job["progress"][str(juan)]
    ledger_path = ROOT / prog["coverage_ledger"]["path"]
    store = audit_store(job)
    event_snapshot = store.artifact("audit_event_log", store.event_path.read_bytes())
    sealed_paths = {
        "source_data": data_path,
        "translation": md_path,
        "coverage_ledger": ledger_path,
        "glossary": GLOSSARY_PATH,
        "common_policy": POLICY_PATH,
        "audit_event_log_snapshot": ROOT / event_snapshot["path"],
        "volume_html": ROOT / "docs" / job["work"] / "translations"
                       / f"{job['work']}-{juan:03d}-baihua.html",
        "work_index": ROOT / "docs" / job["work"] / "index.html",
        "work_search": ROOT / "docs" / job["work"] / "search.json",
        "root_index": ROOT / "docs" / "index.html",
    }
    translation_dir = ROOT / "docs" / job["work"] / "translations"
    for neighbor in (juan - 1, juan + 1):
        neighbor_path = translation_dir / f"{job['work']}-{neighbor:03d}-baihua.html"
        if neighbor_path.is_file():
            sealed_paths[f"neighbor:{neighbor}"] = neighbor_path
    manifest = json.loads(store.manifest_path.read_text(encoding="utf-8"))
    for artifact in manifest.get("artifacts", []):
        artifact_path = ROOT / artifact["path"]
        if artifact_path.is_file():
            sealed_paths[f"artifact:{artifact['sha256']}"] = artifact_path
    sealed_paths.update({
        f"prompt:{path.name}": path for path in sorted(PROMPTS_DIR.glob("*.txt"))
    })
    event_hash = event_snapshot["sha256"][:12]
    path = store.job_dir / f"{job['work']}-{juan:03d}-attestation-{event_hash}.json"
    attestation = publisher.create_attestation(ROOT, path, sealed_paths)
    prog["volume_attestation"] = {
        "path": path.relative_to(ROOT).as_posix(),
        "sha256": audit.sha256_text(path.read_text(encoding="utf-8")),
        "seal_sha256": attestation["seal_sha256"],
    }
    save_job(job)
    return path


def require_content_gate(job: dict, juan: int, md_path: Path) -> None:
    gate = job.get("progress", {}).get(str(juan), {}).get("quality_gate", {})
    current_hash = audit.sha256_text(md_path.read_text(encoding="utf-8"))
    if not gate.get("passed") or gate.get("review_verdict") != "pass":
        raise RuntimeError(f"juan {juan}: blocking quality gate has not passed")
    if gate.get("translation_sha256") != current_hash:
        raise RuntimeError(f"juan {juan}: translation changed after quality gate")


def require_quality_gate(job: dict, juan: int, md_path: Path) -> None:
    require_content_gate(job, juan, md_path)
    attestation = job.get("progress", {}).get(str(juan), {}).get("volume_attestation", {})
    path = ROOT / attestation.get("path", "") if attestation.get("path") else None
    if not path:
        raise RuntimeError(f"juan {juan}: immutable volume attestation is missing")
    try:
        publisher.verify_attestation(ROOT, path)
    except publisher.PublishError as error:
        raise RuntimeError(f"juan {juan}: {error}") from error


def build_html(job: dict, md_path: Path) -> None:
    text = md_path.read_text(encoding="utf-8")
    entries = bth.parse_entries(text)
    work, juan = bth.infer_work_juan(md_path)
    require_content_gate(job, juan, md_path)
    output = bth.translation_output_path(md_path, None)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        bth.render(entries, md_path, work, juan, bth.infer_title(text, work, juan), term_style="policy"),
        encoding="utf-8",
    )
    bsh.main()  # refresh section pages + index (includes new translation link)
    import build_search_index
    build_search_index.main()


def git_commit_push(job: dict, juan: int) -> None:
    require_quality_gate(job, juan, juan_paths(job["work"], juan)["md"])
    if not job.get("push", True):
        log(job, f"juan {juan}: --no-push, skipping commit")
        return
    with flock("git"):
        attestation = ROOT / job["progress"][str(juan)]["volume_attestation"]["path"]
        revision = publisher.publish_volume(
            ROOT, audit_store(job), job["work"], juan, attestation,
            include_pipeline_code=False,
        )
        log(job, f"juan {juan}: pushed audited content commit {revision}")


def run_juan(job: dict, juan: int) -> None:
    work = job["work"]
    paths = juan_paths(work, juan)
    prog = job["progress"].setdefault(str(juan), {})
    if prog.get("step") == "done":
        try:
            require_quality_gate(job, juan, paths["md"])
        except (OSError, RuntimeError):
            for key in ("quality_gate", "volume_attestation", "coverage_ledger",
                        "review_attestations", "review_verdict", "repaired"):
                prog.pop(key, None)
            prog["step"] = "resume_rebuild"
            save_job(job)
        else:
            return
    resume_step = prog.get("step")  # step saved before a crash/restart, if any

    prog["step"] = "fetch"
    save_job(job)
    data_path = ensure_data(work, juan)
    lines = extract_lines(data_path)
    start, end = juan_bounds(lines)

    if not paths["tsv"].exists() and not paths["md"].exists():
        # segmentation only feeds the skeleton; an existing md makes it moot
        prog["step"] = "segment"
        save_job(job)
        llm_segment(job, juan, lines, paths["tsv"])

    if not paths["md"].exists():
        prog["step"] = "skeleton"
        save_job(job)
        segments = read_segments(paths["tsv"])
        paths["md"].parent.mkdir(exist_ok=True)
        atomic_write(paths["md"], render_skeleton(
            juan, start, end, segments, lines, work=work, work_title=work_info(work)["title"]))

    if is_dual(job):
        # two independent drafts (codex, glm), then claude settles the final text
        import shutil
        for stage in DUAL_STAGES:
            draft = paths[stage]
            if not draft.exists():
                draft.parent.mkdir(exist_ok=True)
                shutil.copyfile(paths["md"], draft)
            if stage != resume_step:
                prog["section"] = 0
            prog["step"] = stage
            save_job(job)
            translate_sections(job, juan, draft, prog, stage=stage)
        if "merge" != resume_step:
            prog["section"] = 0
        prog["step"] = "merge"
        save_job(job)
        merge_sections(job, juan, paths, prog)
    else:
        prog["step"] = "translate"
        save_job(job)
        translate_sections(job, juan, paths["md"], prog)

    prog["step"] = "checks_pre_review"
    save_job(job)
    issues = run_content_checks(paths["md"], data_path, start, end)
    if issues and not prog.get("repaired"):
        # one repair round: re-translate the sections named in term-check issues
        bad_titles = {i.split(":")[0].strip() for i in issues if ":" in i}
        log(job, f"juan {juan}: checks failed ({len(issues)}), repairing {len(bad_titles)} sections")
        prog["repaired"] = True
        save_job(job)
        translate_sections(job, juan, paths["md"], prog, only_titles=bad_titles,
                           extra="注意：前次譯文未通過術語檢查：" + "；".join(issues[:10]),
                           stage="repair")
        issues = run_content_checks(paths["md"], data_path, start, end)
    if issues:
        raise RuntimeError(f"juan {juan} checks failed: " + "; ".join(issues[:10]))

    prog["step"] = "review"
    prog["review_verdict"] = "not_run"
    save_job(job)
    run_review_chain(job, juan, paths["md"], prog)

    # Paragraph ids go into the md now, so the ledger, the gate and the seal bind the same bytes.
    reviewed = paths["md"].read_text(encoding="utf-8")
    marked = bth.assign_paragraph_ids(reviewed)
    if marked != reviewed:
        atomic_write(paths["md"], marked)

    prog["step"] = "checks"
    save_job(job)
    write_coverage_ledger(job, juan, paths["md"], data_path, start, end)
    issues = run_checks(job, juan, paths["md"], data_path, start, end)
    record_quality_gate(job, juan, paths["md"], issues)
    if issues:
        raise RuntimeError(f"juan {juan} final checks failed: " + "; ".join(issues[:10]))

    prog["step"] = "html"
    save_job(job)
    build_html(job, paths["md"])
    create_volume_attestation(job, juan, paths["md"], data_path)
    require_quality_gate(job, juan, paths["md"])

    prog["step"] = "commit"
    save_job(job)
    git_commit_push(job, juan)

    prog["step"] = "done"
    save_job(job)


def run_job(job: dict) -> None:
    normalize_job(job)
    if job.get("work") != SUPPORTED_WORK:
        job["state"] = "failed"
        job["error"] = f"the audited pipeline supports T1579 only: {job.get('work')}"
        job["pid"] = None
        save_job(job)
        log(job, f"job refused: {job['error']}")
        return
    job["state"] = "running"
    job["pid"] = os.getpid()
    save_job(job)
    if job.get("summary"):
        log(job, "summary: not implemented")
    failed = []
    for juan in job["juans"]:
        merge_cancelled_from_disk(job)
        normalize_job(job)
        volume_state = job["volumes"][volume_key(juan)].get("state")
        if volume_state == "cancelled":
            log(job, f"juan {juan}: cancelled, skipping")
            continue
        if volume_state == "done":
            continue
        job["_current_juan"] = juan
        set_volume_state(job, juan, "running")
        save_job(job)
        try:
            run_juan(job, juan)
        except Exception as err:  # noqa: BLE001 — job must survive a bad juan
            failed.append(juan)
            message = str(err)[:500]
            job["progress"].setdefault(str(juan), {})["error"] = message
            set_volume_state(job, juan, "failed", message)
            log(job, f"juan {juan} FAILED: {err}")
            save_job(job)
        else:
            set_volume_state(job, juan, "done")
            save_job(job)
        finally:
            job.pop("_current_juan", None)
    finalize_job_state(job, failed)
    job["pid"] = None
    save_job(job)
    log(job, f"job finished: {job['state']}")


# ---------- queue / workers ----------

def claimable(job: dict, model: str) -> bool:
    if job.get("kind") == AUDIT_JOB_KIND:
        return False  # a legacy audit runs in the foreground; only `audit --resume` continues it
    if job.get("model") != model:
        return False
    state = job.get("state")
    if state == "queued":
        return True
    if state in ("running", "waiting_limit"):
        pid = job.get("pid")
        if pid == os.getpid():
            return False  # owned by another thread in this process
        try:
            if pid:
                os.kill(pid, 0)
                return False  # a live foreign runner owns it (shouldn't happen: pid lock)
        except (ProcessLookupError, PermissionError):
            pass
        return True  # dead owner -> resume
    return False


def worker(model: str) -> None:
    while True:
        candidates = []
        for path in sorted(JOBS_DIR.glob("*.json")):
            try:
                job = load_job(path)
            except (json.JSONDecodeError, OSError):
                continue
            if claimable(job, model):
                candidates.append(job)
        if candidates:
            job = candidates[0]
            resume_at = job.get("resume_at")
            if resume_at:
                wait = (datetime.fromisoformat(resume_at) - datetime.now().astimezone()).total_seconds()
                if wait > 0:
                    print(f"{now_iso()} [{job['id']}] resuming at {resume_at}", flush=True)
                    time.sleep(min(wait + 300, 3600))  # 5 min past the reset, chunked by the loop
            run_job(job)
        else:
            time.sleep(POLL_SECONDS)


def acquire_runner_lock():
    """Take locks/runner.pid without waiting. scripts/runner.py takes the same lock: one runner at a time.

    The lock lasts as long as the returned file stays open.
    """
    LOCKS_DIR.mkdir(parents=True, exist_ok=True)
    pid_fh = open(LOCKS_DIR / "runner.pid", "a+")  # "a+": the pid of the holder stays until the lock is ours
    try:
        fcntl.flock(pid_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        pid_fh.close()
        raise SystemExit("another runner is already active")
    pid_fh.seek(0)
    pid_fh.truncate()
    pid_fh.write(str(os.getpid()))
    pid_fh.flush()
    return pid_fh


def cmd_run() -> None:
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    pid_fh = acquire_runner_lock()  # held until the process ends
    threads = [threading.Thread(target=worker, args=(m,), daemon=True, name=m) for m in QUEUES]
    for t in threads:
        t.start()
    print(f"{now_iso()} runner up (pid {os.getpid()}), workers: {', '.join(QUEUES)}", flush=True)
    while True:
        time.sleep(3600)


# ---------- enqueue ----------

def parse_juans(spec: str) -> list[int]:
    juans: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            lo, hi = part.split("-", 1)
            juans.extend(range(int(lo), int(hi) + 1))
        elif part:
            juans.append(int(part))
    if not juans:
        raise ValueError(f"no juans in: {spec}")
    return sorted(set(juans))


def parse_link(link: str) -> tuple[str, int | None]:
    match = re.search(r"/([A-Z]+\d+[a-z]?)(?:_(\d{1,3}))?/?$", link.strip())
    if not match:
        raise ValueError(f"cannot parse CBETA link: {link}")
    return match.group(1), int(match.group(2)) if match.group(2) else None


def cmd_enqueue(args) -> None:
    work = args.work
    juans = parse_juans(args.juans) if args.juans else None
    if args.link:
        work, link_juan = parse_link(args.link)
        if juans is None and link_juan:
            juans = [link_juan]
    if work != SUPPORTED_WORK:
        raise SystemExit(f"the audited pipeline supports T1579 only: {work}")
    try:
        get_work(work)
    except ValueError as err:
        raise SystemExit(str(err))
    if not juans:
        raise SystemExit("specify --juans or a link with a juan number")
    if args.requested_parallel < 1:
        raise SystemExit("--requested-parallel must be at least 1")
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    job = {
        "id": f"{datetime.now():%Y%m%d-%H%M%S}-{args.model}-{uuid.uuid4().hex[:4]}",
        "work": work, "juans": juans, "model": args.model,
        "state": "queued", "created": now_iso(), "updated": now_iso(),
        "pid": None, "resume_at": None, "error": None,
        "push": not args.no_push, "summary": args.summary,
        "requested_parallel": args.requested_parallel,
        "progress": {},
    }
    save_job(job)
    print(f"enqueued {job['id']}: {work} juans {juans} via {args.model}")


# ---------- legacy audit: review a published translation without translating it again ----------

AUDIT_JOB_ID_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-audit-[0-9a-f]{4}$")
AUDIT_JOB_MODELS = ("auto", "echo") + llm.AUDIT_FAKE_MODELS
# No review_parallel: the command has no channel for parallel-source evidence.
AUDIT_REVIEW_PASSES = ("review_terms", "review_doctrine")
TERM_CHECK_STAGE = "term_check"


class AuditBlocked(Exception):
    """The audit of one juan cannot go on. `reasons` become the blocking lines of its report."""

    def __init__(self, reasons: list[str]):
        super().__init__("; ".join(reasons))
        self.reasons = list(reasons)


def file_sha256(path: Path) -> str:
    return audit.sha256_bytes(path.read_bytes())


def atomic_write_bytes(path: Path, data: bytes) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def place_exclusive(src: Path, dst: Path) -> None:
    """Move src to dst and never replace a file: FileExistsError when dst exists, and src stays.

    Every file that the audit puts at the path of a translation goes through here. os.replace is used
    only to move a translation away from its path. So the file that is moved away is the one that was
    there, and a file that appears later is never overwritten.
    """
    os.link(src, dst)
    os.unlink(src)


def put_back(src: Path, dst: Path) -> None:
    """Return a file that a swap moved aside to its path. A file that appeared there meanwhile stays, with src.

    The only way back is a hard link: it is atomic and never replaces a file. A copy would leave half a
    file at the path after a kill, and a rename could replace a newer save. So when the link cannot be
    made, the error goes up and nothing is written: the file stays in the backup folder, the job keeps
    its record of it, and the next restore or resume tries again.
    """
    try:
        os.link(src, dst)
    except FileExistsError:
        return
    with contextlib.suppress(OSError):
        os.unlink(src)  # the file is back; a second name for it in the backup folder does no harm


def require_hard_link(path: Path) -> None:
    """Refuse a swap on a filesystem that cannot make a hard link, before the translation is moved.

    `path` is a file of the job folder. A swap moves the translation into that folder and needs a hard
    link to finish and to undo. Without one the translation would stay there.
    """
    probe = path.with_name(f"{path.name}.link")
    probe.unlink(missing_ok=True)  # the probe of an attempt that was killed between the next two steps
    try:
        os.link(path, probe)
    except OSError as error:
        raise AuditBlocked([
            f"the translation was not installed: no hard link can be made here ({error.strerror or error})"
        ]) from error
    probe.unlink()


def audit_job_dir(job: dict) -> Path:
    return audit_store(job).job_dir


def audit_backup_dir(job: dict) -> Path:
    return audit_job_dir(job) / "backup"


def free_backup_path(job: dict, name: str) -> Path:
    """A path in the backup folder of the job that no file has yet: `<n>-<name>`."""
    backup_dir = audit_backup_dir(job)
    backup_dir.mkdir(parents=True, exist_ok=True)
    number = 1
    while (backup_dir / f"{number}-{name}").exists():
        number += 1
    return backup_dir / f"{number}-{name}"


def recorded_backup(job: dict, relative: object) -> Path | None:
    """A backup path that the job file records, or None when it is not a file of this job's backup folder."""
    if not isinstance(relative, str):
        return None
    path = (ROOT / relative).resolve()
    if path.parent != audit_backup_dir(job).resolve() or not path.is_file():
        return None
    return path


def recorded_site_backup(job: dict, relative: object) -> Path | None:
    """The folder of a site snapshot that the job file records, or None when it is not in this job's backup folder."""
    if not isinstance(relative, str):
        return None
    path = (ROOT / relative).resolve()
    if path.parent != audit_backup_dir(job).resolve() or not path.is_dir():
        return None
    return path


def audit_report_path(job: dict, juan: int) -> Path:
    return audit_job_dir(job) / f"{job['work']}-{juan:03d}-audit-report.json"


def write_audit_report(job: dict, juan: int, prog: dict, blocking: list[str]) -> dict:
    """The report of the last attempt on a juan. The file is written only when its content changes."""
    passed = not blocking
    install = prog.get("install", "none")
    report = {
        "schema_version": "1.0",
        "job_id": job["id"],
        "work": job["work"],
        "juan": juan,
        "verdict": "pass" if passed else "fail",
        "install": install,
        "sections_total": prog.get("sections_total", 0),
        "rewritten_units": prog.get("rewritten_units", []) if passed else [],
        "warnings": prog.get("audit_warnings", []),
        "blocking": blocking,
        "findings": prog.get("findings", {"round1": 0, "remaining": 0}),
        "proposed_terms": prog.get("proposed_terms", []),
        "attestation": prog.get("volume_attestation", {}).get("path") if install == "sealed" else None,
    }
    path = audit_report_path(job, juan)
    report_text = json_text(report) + "\n"
    if not path.is_file() or path.read_text(encoding="utf-8") != report_text:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(path, report_text)
    return report


def select_audit_candidate(job: dict, juan: int, prog: dict, md_path: Path) -> Path:
    """K5 step 2: put the translation back when a swap was cut short, then choose the candidate.

    The candidate is the copy in audits/<job id>/candidate/ that the audit reviews and rewrites. It is
    kept while the translation is still a file of this audit: the text the candidate was copied from,
    the candidate itself, or the candidate that an earlier attempt installed. Any other content means
    that somebody else changed the translation: the audit starts again from that file.
    """
    candidate = audit_job_dir(job) / "candidate" / md_path.name
    if prog.get("swap") or not md_path.exists():
        with flock("jobs"):
            settle_swap(job, prog, md_path, file_sha256(candidate) if candidate.is_file() else None)
        if not md_path.exists():
            raise AuditBlocked(["translation file is missing"])
    current_bytes = md_path.read_bytes()
    current = audit.sha256_bytes(current_bytes)
    ours = {prog.get("candidate_base_sha256")}
    if candidate.is_file():
        ours.add(file_sha256(candidate))
    if prog.get("install") == "installed":
        ours.add(prog.get("installed_sha256"))
    if candidate.is_file() and current in ours:
        return candidate
    candidate.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(candidate, current_bytes)
    # A site snapshot of an attempt that was cut short stays: it still is the site before the audit.
    kept = {key: prog[key] for key in ("site_backup",) if key in prog}
    prog.clear()
    prog.update({"candidate_base_sha256": current, "install": "none", **kept})
    save_job(job)
    return candidate


def check_audit_structure(job: dict, juan: int, prog: dict, candidate: Path,
                          data_path: Path) -> tuple[str, str]:
    """K5 steps 3 and 4: the checks that cost no model call, and the shared-boundary warning."""
    prog["audit_warnings"] = []
    text = candidate.read_text(encoding="utf-8")
    try:
        entries = bth.parse_entries(text)
        ranges = parse_ranges(text)
    except ValueError as error:
        raise AuditBlocked([str(error)])
    prog["sections_total"] = len(entries)
    if "base_units" not in prog:
        # The candidate is still the exact copy of the translation: remember what must survive a rewrite.
        prog["base_units"] = {
            unit_id(job, juan, idx): {
                "translation_sha256": audit.sha256_text(entry.translation),
                "paragraph_ids": [pid for pid in entry.paragraph_ids if pid],
            }
            for idx, entry in enumerate(entries)
        }
    if not data_path.is_file():
        raise AuditBlocked([f"source data file is missing: {data_path.relative_to(ROOT).as_posix()}"])
    source_lines = extract_lines(data_path)
    start, end = juan_bounds(source_lines)
    line_ids = data_line_ids(data_path)
    blocking = [f"{unit_id(job, juan, idx)}: empty translation"
                for idx, entry in enumerate(entries) if not entry.translation.strip()]
    try:
        blocking += check_ranges(ranges, line_ids, start, end, source_lines, exact_once=False)
    except ValueError as error:
        blocking.append(str(error))
    blocking += validate_glossary(load_glossary())
    if blocking:
        raise AuditBlocked(blocking)
    overlaps = range_overlaps(ranges, line_ids, start, end)
    if overlaps:
        # Adjacent sections of a published translation may share their boundary line: recorded, not blocking.
        prog["audit_warnings"] = [{"kind": "shared_boundary_lines", "line_ids": overlaps}]
    return start, end


def term_check_findings(job: dict, juan: int, md_path: Path) -> list[dict]:
    """K5 step 6: every issue of the mechanical glossary check, as a finding on its section."""
    text = md_path.read_text(encoding="utf-8")
    entries = bth.parse_entries(text)
    glossary = load_glossary()
    findings = []
    for idx, term_entry in enumerate(parse_term_entries(text)):
        for message in term_issues(glossary, [term_entry]):
            findings.append({
                "clause_id": clause_id(entries[idx]),
                "severity": "high",
                "category": "glossary_term",
                "claim": message,
                "evidence": {"source_quote": "", "translation_quote": "", "reference_ids": []},
                "required_change": message,
                "unit_id": unit_id(job, juan, idx),
                "review_stage": TERM_CHECK_STAGE,
                "section_index": idx,
            })
    return findings


def audit_findings(job: dict, juan: int, prog: dict, candidate: Path, round_number: int,
                   only_units: set[str] | None = None) -> list[dict]:
    """The two evidence reviews and the mechanical glossary check of one round."""
    findings: list[dict] = []
    for pass_name in AUDIT_REVIEW_PASSES:
        findings += review_pass(
            job, juan, candidate, pass_name, prog, round_number, only_units=only_units,
            reuse_attestations=only_units is None, apply_proposed_terms=False,
        )
    return findings + term_check_findings(job, juan, candidate)


def blocking_line(finding: dict) -> str:
    if finding["review_stage"] == TERM_CHECK_STAGE:
        return finding["claim"]
    return f"{finding['unit_id']} {finding['review_stage']}: {finding['claim']}"


def review_audit_candidate(job: dict, juan: int, prog: dict, candidate: Path, data_path: Path,
                           start: str, end: str) -> None:
    """K5 steps 5 to 8: review, one fix round, and the gate. Returns only when the candidate passed."""
    # Step 8 is gate_audit_candidate, below.
    entries = bth.parse_entries(candidate.read_text(encoding="utf-8"))
    contracts = prog.setdefault("clause_contracts", {})
    for idx, entry in enumerate(entries):
        unit = unit_id(job, juan, idx)
        try:
            canonical_structured_contract(
                contracts.get(unit), unit, source_hash(entry.source), clause_id(entry),
                entry.translation, error_stage="audit",
            )
        except (TypeError, ValueError):
            contracts[unit] = {
                "stage": LEGACY_CONTRACT_STAGE,
                "source_hash": source_hash(entry.source),
                "clause": {"clause_id": clause_id(entry), "vernacular": entry.translation},
            }
    save_job(job)

    first = audit_findings(job, juan, prog, candidate, 1)
    remaining: list[dict] = []
    if first:
        fix_findings(job, juan, candidate, first, prog)
        # One fix round. Only the rewritten units are reviewed again; the glossary check reads the whole file.
        remaining = audit_findings(job, juan, prog, candidate, 2,
                                   only_units={finding["unit_id"] for finding in first})
    if remaining:
        prog["findings"] = {"round1": len(first), "remaining": len(remaining)}
        prog["review_verdict"] = "fail"
        save_job(job)
        raise AuditBlocked([blocking_line(finding) for finding in remaining])
    with pinned_review_inputs():
        gate_audit_candidate(job, juan, prog, candidate, data_path, start, end, len(first))


def gate_audit_candidate(job: dict, juan: int, prog: dict, candidate: Path, data_path: Path,
                         start: str, end: str, round1: int) -> None:
    """K5 step 8, with the review inputs pinned: the last check of the reviews, the other checks, the gate.

    The glossary, the policy or a prompt may have changed while the reviews ran, and may change now. The
    gate stores one version of them: the pinned one. So every review must have answered that version: the
    reuse rule asks again each unit whose prompt, built from the pinned inputs, is not the one that was
    answered. Nothing changed: no model is called.
    """
    remaining = audit_findings(job, juan, prog, candidate, 3)
    # Nothing new in round 1: the findings that an earlier attempt fixed stay in the count.
    prog["findings"] = {"round1": round1 or prog.get("findings", {}).get("round1", 0),
                        "remaining": len(remaining)}
    if remaining:
        prog["review_verdict"] = "fail"
        save_job(job)
        raise AuditBlocked([blocking_line(finding) for finding in remaining])
    prog["review_verdict"] = "pass"  # record_quality_gate reads it; in the full pipeline run_review_chain sets it

    reviewed = candidate.read_text(encoding="utf-8")
    marked = bth.assign_paragraph_ids(reviewed)
    if marked != reviewed:
        atomic_write(candidate, marked)
    units = {unit_id(job, juan, idx): entry for idx, entry in enumerate(bth.parse_entries(marked))}
    base_units = prog["base_units"]
    lost = []
    for unit, base in base_units.items():
        kept = units[unit].paragraph_ids if unit in units else ()
        missing = [pid for pid in base["paragraph_ids"] if pid not in kept]
        if missing:
            lost.append(f"paragraph ids would be lost in {unit}: {', '.join(missing)}")
    if lost:
        save_job(job)
        raise AuditBlocked(lost)

    write_coverage_ledger(job, juan, candidate, data_path, start, end, exact_once=False)
    issues = run_checks(job, juan, candidate, data_path, start, end, exact_once=False)
    record_quality_gate(job, juan, candidate, issues, audit_kind="legacy_review")
    if issues:
        raise AuditBlocked(issues)
    prog["rewritten_units"] = [
        unit for unit, entry in units.items()
        if base_units.get(unit, {}).get("translation_sha256") != audit.sha256_text(entry.translation)
    ]
    save_job(job)


def record_install(prog: dict, installed_sha: str) -> None:
    """The candidate is at the path of the translation, and what was there is the backup of the swap."""
    prog["original_backup"] = prog.pop("swap")["backup"]
    prog["install"] = "installed"
    prog["installed_sha256"] = installed_sha


def settle_swap(job: dict, prog: dict, md_path: Path, candidate_sha: str | None) -> None:
    """End a swap of install_audit_candidate that an error or a kill cut short. The caller holds the jobs lock.

    The path is empty: the file that was moved aside goes back. The candidate is at the path and the
    original is in the backup folder: the swap was complete, so the install is recorded, and a later
    failure puts the original back. Any other file at the path stays, and so does the backup.
    """
    backup = recorded_backup(job, (prog.get("swap") or {}).get("backup"))
    if not md_path.exists():
        if backup is None:
            return  # nothing of this job to put back: the caller reports the missing translation
        try:
            put_back(backup, md_path)
        except OSError as error:
            # The record of the swap stays, so the next restore or resume puts the file back.
            raise AuditBlocked([
                f"the translation is in {backup.relative_to(ROOT.resolve()).as_posix()}: "
                f"it could not go back to its path ({error.strerror or error})"
            ]) from error
        prog.pop("swap", None)
    elif (backup is not None and candidate_sha is not None and prog.get("install") != "installed"
          and file_sha256(md_path) == candidate_sha
          and file_sha256(backup) == prog.get("candidate_base_sha256")):
        record_install(prog, candidate_sha)
    else:
        prog.pop("swap", None)
    save_job(job)


def restore_original_translation(job: dict, prog: dict, md_path: Path) -> None:
    """K5 step 12, the translation: take an installed candidate out again. The caller holds the jobs lock.

    The file at the path is moved into the backup folder first. When it is the candidate that this audit
    installed, the original goes back; when somebody changed it after the install, it goes back itself.
    When the path is empty, somebody moved the translation away (an approved re-run archives it): nothing
    is put there, and the original stays in the backup folder.
    """
    if prog.get("swap") and not md_path.exists():
        # A swap of this job failed and left the path empty: its file goes back before anything is forgotten.
        settle_swap(job, prog, md_path, None)
    original = recorded_backup(job, prog.get("original_backup"))
    if md_path.exists():
        moved = free_backup_path(job, md_path.name)
        prog["swap"] = {"backup": moved.relative_to(ROOT).as_posix()}
        save_job(job)
        os.replace(md_path, moved)
        try:
            ours = file_sha256(moved) == prog.get("installed_sha256")
            put_back(original if ours and original is not None else moved, md_path)
        except BaseException:
            settle_swap(job, prog, md_path, None)  # the original cannot go back: the moved file does, not nothing
            raise
        prog.pop("swap", None)
        if ours and original is None:
            # Somebody removed the original from the backup folder. The job must not claim that it is back.
            save_job(job)
            raise AuditBlocked(["the original translation is no longer in the backup folder of the job"])
    for key in ("installed_sha256", "original_backup"):
        prog.pop(key, None)
    prog["install"] = "none"
    save_job(job)


def install_audit_candidate(job: dict, juan: int, prog: dict, candidate: Path, md_path: Path) -> str:
    """K5 step 9: put the passed candidate at the path of the translation, under the jobs lock.

    The web app holds the same lock while an approved re-run moves a translation away. Returns the
    sha256 of the reviewed bytes, which the translation must still have when it is sealed.
    """
    candidate_bytes = candidate.read_bytes()
    candidate_sha = audit.sha256_bytes(candidate_bytes)
    with flock("jobs"):
        if not md_path.exists():
            raise AuditBlocked(["translation file is missing"])
        if file_sha256(md_path) == candidate_sha:
            return candidate_sha  # no section was rewritten, or an earlier attempt already installed this text
        if prog.get("install") == "installed":
            # The path holds the candidate of an earlier attempt, not this one: back to the original first.
            restore_original_translation(job, prog, md_path)
            if not md_path.exists():
                raise AuditBlocked(["translation file is missing"])
        require_hard_link(candidate)  # found out now, while the translation is still at its path
        backup = free_backup_path(job, md_path.name)
        prog["swap"] = {"backup": backup.relative_to(ROOT).as_posix()}
        save_job(job)
        try:
            os.replace(md_path, backup)
            if file_sha256(backup) != prog.get("candidate_base_sha256"):
                # Somebody saved the translation while the audit ran: their file goes back as it is.
                raise AuditBlocked(["the translation changed during the audit"])
            staged = backup.with_name(f"{backup.name}.candidate")
            staged.write_bytes(candidate_bytes)
            try:
                place_exclusive(staged, md_path)
            except FileExistsError:
                # Somebody saved a file between the two moves: it stays, and the original stays in the backup.
                staged.unlink()
                raise AuditBlocked(["the translation changed during the audit"])
        except BaseException:
            # Whatever stopped the swap (a refusal above, an I/O error, an interrupt): the file that was
            # moved aside goes back, unless the candidate or a newer file is at the path.
            settle_swap(job, prog, md_path, candidate_sha)
            raise
        record_install(prog, candidate_sha)
        save_job(job)
    return candidate_sha


def build_legacy_page(md_path: Path) -> Path:
    """Write the reader page of one translation in the published (legacy) term style.

    It writes this one page. bth.build_page would also give ids to the archived versions of the juan and
    rewrite their pages, which an audit must never touch.
    """
    text = md_path.read_text(encoding="utf-8")
    work, juan = bth.infer_work_juan(md_path)
    page = bth.translation_output_path(md_path, None)
    rendered = bth.render(bth.parse_entries(text), md_path, work, juan,
                          bth.infer_title(text, work, juan), term_style="legacy")
    page.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(page, rendered.encode("utf-8"))
    return page


def create_legacy_attestation(job: dict, juan: int, md_path: Path, data_path: Path) -> Path:
    """Seal one audited juan: its four files, the event log as it is now, and every artifact of the job.

    The artifacts are the requests, the answers, the passed candidate and the review inputs (glossary,
    policy, prompts) that record_quality_gate stored. Live shared files are not sealed: the glossary, the
    prompts, search.json, the index pages and the pages of other juans change with the next juan or with
    a production job, and a seal over them would break then. After this the job makes no new artifact
    for the juan.
    """
    prog = job["progress"][str(juan)]
    store = audit_store(job)
    event_snapshot = store.artifact("audit_event_log", store.event_path.read_bytes())
    sealed_paths = {
        "source_data": data_path,
        "translation": md_path,
        "volume_html": bth.translation_output_path(md_path, None),
        "coverage_ledger": ROOT / prog["coverage_ledger"]["path"],
        "audit_event_log_snapshot": ROOT / event_snapshot["path"],
    }
    manifest = json.loads(store.manifest_path.read_text(encoding="utf-8"))
    for artifact in manifest.get("artifacts", []):
        sealed_paths[f"artifact:{artifact['sha256']}"] = ROOT / artifact["path"]
    path = store.job_dir / f"{job['work']}-{juan:03d}-attestation-{event_snapshot['sha256'][:12]}.json"
    attestation = publisher.create_attestation(ROOT, path, sealed_paths)
    prog["volume_attestation"] = {
        "path": path.relative_to(ROOT).as_posix(),
        "sha256": audit.sha256_text(path.read_text(encoding="utf-8")),
        "seal_sha256": attestation["seal_sha256"],
    }
    save_job(job)
    return path


def search_index_paths() -> list[Path]:
    return sorted((ROOT / "docs").glob("*/search.json"))


def rebuild_search_indexes() -> None:
    import build_search_index
    with contextlib.redirect_stdout(io.StringIO()):
        build_search_index.main()


def snapshot_site(job: dict, prog: dict, md_path: Path) -> bool:
    """K5 step 10: copy the page of the juan and every search index into the backup folder of the job.

    The copy is on disk, so a kill after the rebuild does not lose what the site was. Returns whether
    this call made it. An attempt that finds the snapshot of an earlier attempt keeps that one: it was
    taken before any attempt rebuilt the site.
    """
    if recorded_site_backup(job, prog.get("site_backup")) is not None:
        return False
    folder = free_backup_path(job, "site")
    folder.mkdir()
    for path in (bth.translation_output_path(md_path, None), *search_index_paths()):
        if path.is_file():
            copy = folder / path.relative_to(ROOT)
            copy.parent.mkdir(parents=True, exist_ok=True)
            copy.write_bytes(path.read_bytes())
    prog["site_backup"] = folder.relative_to(ROOT).as_posix()  # recorded before anything is rebuilt
    save_job(job)
    return True


def restore_juan_search_records(folder: Path, md_path: Path) -> None:
    """Put the search records of one juan back from the snapshot and keep every other record as it is now."""
    work, juan = bth.infer_work_juan(md_path)
    live = ROOT / "docs" / work / "search.json"
    copy = folder / live.relative_to(ROOT)
    if not live.is_file() or not copy.is_file():
        return  # no index now, or none before the audit: nothing of this juan to put back
    page = f"translations/{md_path.stem}.html"

    def of_this_juan(record: dict) -> bool:
        return record.get("j") == juan and str(record.get("u", "")).startswith(page)

    records = json.loads(live.read_text(encoding="utf-8"))
    before = [record for record in json.loads(copy.read_text(encoding="utf-8")) if of_this_juan(record)]
    place = next((index for index, record in enumerate(records) if of_this_juan(record)), len(records))
    others = [record for record in records if not of_this_juan(record)]
    place -= sum(1 for record in records[:place] if of_this_juan(record))
    merged = others[:place] + before + others[place:]
    if merged != records:
        atomic_write(live, json.dumps(merged, ensure_ascii=False))


def restore_site(job: dict, prog: dict, md_path: Path, *, search: str) -> None:
    """K5 step 12, the site: the page and the search indexes go back to what they were before the audit.

    The page belongs to this juan: it always comes from the snapshot. A search index holds every juan of
    its work, so `search` says how it goes back:
    "snapshot": the bytes of the snapshot. Right for the attempt that took it, seconds ago.
    "rebuild": built again from the translations. A later attempt does that when the translation went
    back, because another job may have published a juan since the snapshot.
    "juan": a later attempt whose candidate is still at the path. Only the records of this juan come from
    the snapshot; every other record stays, so no candidate is indexed and no other juan loses an update.
    """
    folder = recorded_site_backup(job, prog.get("site_backup"))
    if folder is not None:
        paths = {bth.translation_output_path(md_path, None)}
        if search == "snapshot":
            paths |= {ROOT / copy.relative_to(folder) for copy in folder.glob("docs/*/search.json")}
            paths |= set(search_index_paths())
        for path in sorted(paths):
            copy = folder / path.relative_to(ROOT)
            if not copy.is_file():
                path.unlink(missing_ok=True)  # the audit created it
            elif not path.is_file() or path.read_bytes() != copy.read_bytes():
                atomic_write_bytes(path, copy.read_bytes())
        if search == "rebuild":
            rebuild_search_indexes()
        elif search == "juan":
            restore_juan_search_records(folder, md_path)
    prog.pop("site_backup", None)  # only now: an error above leaves the record for the next attempt
    save_job(job)


def undo_install(job: dict, prog: dict, md_path: Path, *, same_attempt: bool) -> list[str]:
    """K5 step 12: a juan that does not pass keeps its translation and its site. Returns what could not go back."""
    problems: list[str] = []
    for step in ("translation", "site"):
        try:
            if step == "site":
                still_installed = prog.get("install") == "installed"  # the candidate could not be taken out
                restore_site(job, prog, md_path,
                             search="snapshot" if same_attempt else "juan" if still_installed else "rebuild")
            elif prog.get("install") == "installed":
                with flock("jobs"):
                    restore_original_translation(job, prog, md_path)
        except AuditBlocked as blocked:
            problems += blocked.reasons
        except Exception as error:  # noqa: BLE001 — reported with the rest, and the other step still runs
            message = audit.redact_secrets(str(error)).strip() or type(error).__name__
            problems.append(f"the {step} could not be put back: {message[:500]}")
    return problems


def publish_audited_juan(job: dict, juan: int, prog: dict, md_path: Path, data_path: Path,
                         reviewed_sha: str) -> None:
    """K5 steps 10 to 12: rebuild the page and the search indexes, seal, and undo all of it on a failure.

    The seal must bind the reviewed bytes. A translation that somebody saved after the install is not
    sealed: it counts as a failure, and their file stays at the path.
    """
    same_attempt = snapshot_site(job, prog, md_path)
    attestation = None
    try:
        build_legacy_page(md_path)
        rebuild_search_indexes()
        attestation = create_legacy_attestation(job, juan, md_path, data_path)
        sealed = publisher.verify_attestation(ROOT, attestation)
        if sealed["files"]["translation"]["sha256"] != reviewed_sha:
            raise RuntimeError("the translation changed during the audit")
    except Exception as error:  # noqa: BLE001 — whatever failed, the translation and the site go back
        problems = undo_install(job, prog, md_path, same_attempt=same_attempt)
        prog.pop("volume_attestation", None)
        if attestation is not None:
            attestation.unlink(missing_ok=True)  # written by this attempt; it never became the seal of the juan
        if not problems:
            prog["install"] = "none"
        save_job(job)
        message = audit.redact_secrets(str(error)).strip() or type(error).__name__
        raise AuditBlocked([f"the install was undone: {message[:500]}", *problems]) from error
    prog["install"] = "sealed"  # site_backup stays in the job: it names the snapshot of the site before the audit
    save_job(job)


def audit_existing_juan(job: dict, juan: int) -> dict:
    """Audit one published juan and return its report. A juan that does not pass is a report, not an exception.

    Sections that no review and no glossary check names stay byte for byte, with their paragraph ids. A
    named section goes to the fixer once, and only that section is reviewed again. When anything is left,
    the translation and its page stay as they were; the candidate and the report stay in audits/<job id>/.
    """
    paths = juan_paths(job["work"], juan)
    md_path, data_path = paths["md"], paths["data"]
    prog = job["progress"].setdefault(str(juan), {})
    if prog.get("install") == "sealed":
        # K5 step 1. A sealed juan is never reviewed again by this job: a new audit is a new job.
        try:
            publisher.verify_attestation(ROOT, ROOT / (prog.get("volume_attestation") or {}).get("path", ""))
        except publisher.PublishError as error:
            return write_audit_report(job, juan, prog, [f"sealed files changed after the audit: {error}"])
        return write_audit_report(job, juan, prog, [])

    job["state"] = "running"
    job["pid"] = os.getpid()
    job["_current_juan"] = juan
    set_volume_state(job, juan, "running")
    save_job(job)
    blocking: list[str] = []
    try:
        candidate = select_audit_candidate(job, juan, prog, md_path)
        start, end = check_audit_structure(job, juan, prog, candidate, data_path)
        review_audit_candidate(job, juan, prog, candidate, data_path, start, end)
        reviewed_sha = install_audit_candidate(job, juan, prog, candidate, md_path)
        publish_audited_juan(job, juan, prog, md_path, data_path, reviewed_sha)
    except AuditBlocked as blocked:
        blocking = blocked.reasons
    except Exception as error:  # noqa: BLE001 — the job goes on with its next juan
        message = audit.redact_secrets(str(error)).strip() or type(error).__name__
        blocking = [message[:500]]
    finally:
        job.pop("_current_juan", None)
    if blocking and (prog.get("install") == "installed" or prog.get("site_backup")):
        # The juan did not pass, and an attempt before a kill left its candidate at the path, or the page
        # and the search indexes that it rebuilt. A juan that does not pass keeps all of them as they were.
        blocking = blocking + [line for line in undo_install(job, prog, md_path, same_attempt=False)
                               if line not in blocking]
    if blocking:
        set_volume_state(job, juan, "failed", "; ".join(blocking))
    else:
        set_volume_state(job, juan, "done")
    save_job(job)
    return write_audit_report(job, juan, prog, blocking)


def audit_job_problem(job: object, job_id: str) -> str | None:
    """Why a job is not an audit job that this command may run, or None."""
    if not isinstance(job, dict):
        return "the job file is not a JSON object"
    if job.get("id") != job_id:
        return "the id does not match the file name"
    if job.get("kind") != AUDIT_JOB_KIND:
        return f"kind is {job.get('kind')!r}, not {AUDIT_JOB_KIND!r}"
    if job.get("work") != SUPPORTED_WORK:
        return f"the audited pipeline supports T1579 only: {job.get('work')}"
    juans = job.get("juans")
    if (not isinstance(juans, list) or not juans
            or any(type(juan) is not int for juan in juans) or len(set(juans)) != len(juans)):
        return "juans must be a non-empty list of distinct integers"
    total = work_info(SUPPORTED_WORK)["juans"]
    outside = [juan for juan in juans if not 1 <= juan <= total]
    if outside:
        return f"juans outside 1-{total}: {outside}"
    if job.get("model") not in AUDIT_JOB_MODELS:
        return f"model {job.get('model')!r} is not one of {', '.join(AUDIT_JOB_MODELS)}"
    for field in ("progress", "volumes"):
        value = job.get(field, {})
        if not isinstance(value, dict) or any(not isinstance(item, dict) for item in value.values()):
            return f"{field} must be an object of objects"
    if any(not key.isdecimal() for key in job.get("volumes", {})):
        return "volumes must be keyed by juan numbers"
    return None


def new_audit_job(work: str, juans_spec: str, model: str) -> dict:
    try:
        juans = parse_juans(juans_spec)
    except ValueError:
        raise SystemExit(f"invalid --juans: {juans_spec}")
    job = {
        "id": f"{datetime.now():%Y%m%d-%H%M%S}-audit-{uuid.uuid4().hex[:4]}",
        "kind": AUDIT_JOB_KIND, "work": work, "juans": juans, "model": model,
        "state": "queued", "created": now_iso(), "updated": now_iso(),
        "pid": None, "resume_at": None, "error": None,
        "progress": {},
    }
    problem = audit_job_problem(job, job["id"])
    if problem:
        raise SystemExit(f"invalid audit job: {problem}")
    return normalize_job(job)


def load_audit_job(job_id: str) -> dict:
    path = JOBS_DIR / f"{job_id}.json"
    try:
        job = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"invalid audit job: {path.relative_to(ROOT).as_posix()} does not exist")
    except (OSError, ValueError) as error:
        raise SystemExit(f"invalid audit job: {error}")
    problem = audit_job_problem(job, job_id)
    if problem:
        raise SystemExit(f"invalid audit job: {problem}")
    return normalize_job(job)


def cmd_audit(args) -> int:
    """`audit`: review published juans in the foreground, one at a time. Exit 0 when every juan is sealed.

    It never commits, never pushes and never runs git. The operator delivers the result with a pull request.
    """
    # 1. The arguments, before any file or folder is made. A resume id becomes part of a path.
    if args.resume is not None and not AUDIT_JOB_ID_RE.fullmatch(args.resume):
        raise SystemExit(f"invalid job id: {args.resume}")
    if args.work != SUPPORTED_WORK:
        raise SystemExit(f"the audited pipeline supports T1579 only: {args.work}")
    # 2. One runner at a time, for the whole command.
    with acquire_runner_lock():
        # 3. The job.
        if args.resume is not None:
            job = load_audit_job(args.resume)
            if args.model is not None and args.model != job["model"]:
                raise SystemExit(f"job {job['id']} runs with --model {job['model']}; a resume cannot change it")
        else:
            job = new_audit_job(args.work, args.juans, args.model or "auto")
            JOBS_DIR.mkdir(parents=True, exist_ok=True)
        unchanged = json.dumps(job, ensure_ascii=False, sort_keys=True)
        print(f"audit job {job['id']}: {job['work']} juans {sorted(job['juans'])} via {job['model']}", flush=True)
        reports = []
        for juan in sorted(job["juans"]):
            report = audit_existing_juan(job, juan)
            reports.append(report)
            print(
                f"juan {juan}: {report['verdict']} ({report['install']}), "
                f"{report['sections_total']} sections, {len(report['rewritten_units'])} rewritten, "
                f"{len(report['warnings'])} warnings, {len(report['blocking'])} blocking; report "
                f"{audit_report_path(job, juan).relative_to(ROOT).as_posix()}",
                flush=True,
            )
        failed = [report["juan"] for report in reports
                  if report["verdict"] != "pass" or report["install"] != "sealed"]
        finalize_job_state(job, failed)
        job["pid"] = None
        if json.dumps(job, ensure_ascii=False, sort_keys=True) != unchanged:
            save_job(job)  # a resume of a finished job changes nothing, so it writes nothing
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    enq = sub.add_parser("enqueue")
    enq.add_argument("--work", default="T1579")
    enq.add_argument("--link", help="CBETA link, e.g. https://cbetaonline.dila.edu.tw/zh/T1579_013")
    enq.add_argument("--juans", help="e.g. 13, 13-15, or 13,15")
    enq.add_argument("--model", required=True, choices=ENQUEUE_MODES)
    enq.add_argument(
        "--requested-parallel",
        type=int,
        default=1,
        help="record an external orchestrator worker request; this job remains serialized",
    )
    enq.add_argument("--no-push", action="store_true")
    enq.add_argument("--summary", action="store_true", help="also build a summary page (not implemented)")
    sub.add_parser("run")
    aud = sub.add_parser(
        "audit",
        help="review published translations without translating them again",
        description="Review published translations without translating them again. "
                    "The command never commits and never pushes.",
    )
    target = aud.add_mutually_exclusive_group(required=True)
    target.add_argument("--juans", help="juans of a new audit job, e.g. 67, 67-72, or 67,70")
    target.add_argument("--resume", metavar="JOB_ID", help="continue the audit job with this id")
    aud.add_argument("--work", default=SUPPORTED_WORK, help="only T1579 is supported")
    aud.add_argument("--model", choices=AUDIT_JOB_MODELS,
                     help="auto (default) uses the pinned models; the echo backends run offline for free")
    args = parser.parse_args()
    if args.cmd == "enqueue":
        cmd_enqueue(args)
    elif args.cmd == "audit":
        return cmd_audit(args)
    else:
        cmd_run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
