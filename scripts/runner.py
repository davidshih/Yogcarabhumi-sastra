#!/usr/bin/env python3
"""Background job runner: translate, review, attest, build, and publish juans.

Usage:
  python3 scripts/runner.py enqueue --juans 13-15 --model auto [--no-push]
  python3 scripts/runner.py enqueue --link https://cbetaonline.dila.edu.tw/zh/T1579_013 --model dual
  python3 scripts/runner.py run

State lives in jobs/*.json (atomic writes); a crash resumes from the last
saved step. Volumes within one job execute serially; external orchestration may
inspect the requested worker limit recorded by enqueue.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import sys
import threading
import time
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
)

JOBS_DIR = ROOT / "jobs"
LOCKS_DIR = ROOT / "locks"
PROMPTS_DIR = ROOT / "prompts"
WORKS_PATH = ROOT / "works.json"
GLOSSARY_PATH = ROOT / "translations" / "glossary" / "T1579-terms.json"
STATUS_JSON = ROOT / "docs" / "status.json"
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

TRANSLATION_STAGES = ("translate", "draft_codex", "draft_glm", "merge", "repair", "fix")
TRANSLATION_NESTED_SHAPES = {
    "additions": ("clause_id", "text"),
    "negation_scope": ("clause_id", "text", "scope"),
    "references": ("clause_id", "expression", "referent"),
    "term_occurrences": ("clause_id", "term_id", "surface"),
    "variants": ("clause_id", "text", "rationale"),
    "notes": ("clause_id", "text"),
}


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
REVIEW_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["schema_version", "stage", "unit_id", "source_hash", "verdict", "findings"],
    "properties": {
        "schema_version": {"const": "1.0"},
        "stage": {"enum": list(REVIEW_PASSES)},
        "unit_id": {"type": "string"},
        "source_hash": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "verdict": {"enum": ["pass", "changes_required", "not_checked"]},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["clause_id", "severity", "category", "claim", "evidence",
                             "required_change"],
            },
        },
        "proposed_terms": {"type": "array"},
    },
}
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
    LOCKS_DIR.mkdir(exist_ok=True)
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
    atomic_write(STATUS_JSON, json.dumps({"generated": now_iso(), "jobs": jobs}, ensure_ascii=False))


def log(job: dict, msg: str) -> None:
    print(f"{now_iso()} [{job['id']}] {msg}", flush=True)


def prompt_text(name: str, **vars) -> str:
    policy = POLICY_PATH.read_text(encoding="utf-8").strip()
    stage = Template((PROMPTS_DIR / f"{name}.txt").read_text(encoding="utf-8")).substitute(**vars)
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


def validate_review_contract(payload: dict, stage: str, expected_unit: str,
                             expected_hash: str, expected_clause: str, *,
                             source: str, translation: str,
                             allowed_reference_ids: set[str] | None = None) -> list[dict]:
    allowed = {"schema_version", "stage", "unit_id", "source_hash", "verdict", "findings",
               "proposed_terms"}
    required_payload = {"schema_version", "stage", "unit_id", "source_hash", "verdict", "findings"}
    if payload.keys() - allowed or required_payload - payload.keys():
        raise ValueError(f"{stage}: reviewer output contains forbidden fields")
    if payload.get("unit_id") != expected_unit or payload.get("source_hash") != expected_hash:
        raise ValueError(f"{stage}: unit_id/source_hash mismatch")
    verdict = payload.get("verdict")
    findings = payload.get("findings")
    allowed_verdicts = {"pass", "changes_required"}
    if stage == "review_parallel" and not allowed_reference_ids:
        allowed_verdicts = {"not_checked"}
    if verdict not in allowed_verdicts or not isinstance(findings, list):
        raise ValueError(f"{stage}: invalid verdict/findings")
    required = {"clause_id", "severity", "category", "claim", "evidence", "required_change"}
    for finding in findings:
        if not isinstance(finding, dict) or set(finding) != required:
            raise ValueError(f"{stage}: finding is missing required fields")
        if finding["clause_id"] != expected_clause or finding["severity"] not in {
            "crit", "high", "med", "low",
        }:
            raise ValueError(f"{stage}: finding clause/severity is invalid")
        evidence = finding["evidence"]
        evidence_fields = {"source_quote", "translation_quote", "reference_ids"}
        if not isinstance(evidence, dict) or set(evidence) != evidence_fields:
            raise ValueError(f"{stage}: finding requires source and translation evidence")
        source_quote = evidence["source_quote"]
        translation_quote = evidence["translation_quote"]
        reference_ids = evidence["reference_ids"]
        if (not isinstance(source_quote, str) or not source_quote.strip() or source_quote not in source
                or not isinstance(translation_quote, str) or not translation_quote.strip()
                or translation_quote not in translation):
            raise ValueError(f"{stage}: evidence quotes are not contained in the reviewed text")
        if not isinstance(reference_ids, list) or not all(isinstance(item, str) for item in reference_ids):
            raise ValueError(f"{stage}: evidence reference_ids must be strings")
        if stage == "review_parallel" and not set(reference_ids).issubset(allowed_reference_ids or set()):
            raise ValueError(f"{stage}: evidence reference_ids are outside the input allowlist")
        for field in ("category", "claim", "required_change"):
            if not isinstance(finding[field], str) or not finding[field].strip():
                raise ValueError(f"{stage}: finding {field} must be non-empty text")
    if (findings and verdict != "changes_required") or (not findings and verdict == "changes_required"):
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
    return json.loads(GLOSSARY_PATH.read_text(encoding="utf-8"))


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


def splice(md_path: Path, index: int, translation: str, note: str | None) -> None:
    text = md_path.read_text(encoding="utf-8")
    sec = md_sections(text)[index]
    # replace note first so translation span offsets stay valid (note comes after)
    if note is not None and sec["note_span"]:
        s, e = sec["note_span"]
        text = text[:s] + note.strip() + text[e:]
    s, e = sec["tr_span"]
    text = text[:s] + translation.strip() + text[e:]
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
        record = store.append({
            "type": "llm_attempt",
            "call_id": call_id,
            "attempt": attempt,
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
            "retry_reason": audit.redact_secrets(result.error) if not result.ok else None,
            "usage": usage,
            "tokens": tokens,
            "availability_reason": availability,
            "hashes": {
                "policy_sha256": audit.sha256_text(POLICY_PATH.read_text(encoding="utf-8")),
                "prompt_sha256": audit.sha256_text(prompt),
                "source_sha256": context.get("source_hash"),
                "glossary_sha256": context.get("glossary_hash"),
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
        raw = llm_call(
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
        )
        payload = parse_json_contract(raw, stage)
        clause = validate_translation_contract(
            payload, stage, current_unit, current_hash, current_clause,
        )
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
        raw = llm_call(
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
        )
        payload = parse_json_contract(raw, "merge")
        clause = validate_translation_contract(
            payload, "merge", current_unit, current_hash, current_clause,
        )
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


def review_pass(job: dict, juan: int, md_path: Path, pass_name: str,
                prog: dict, round_number: int) -> list[dict]:
    glossary = load_glossary()
    entries = bth.parse_entries(md_path.read_text(encoding="utf-8"))
    findings: list[dict] = []
    verdicts = []
    for idx, entry in enumerate(entries):
        current_unit = unit_id(job, juan, idx)
        current_clause = clause_id(entry)
        current_hash = source_hash(entry.source)
        glossary_subset = glossary_subset_json(glossary, entry.source)
        ratio = translation_ratio(entry.source, entry.translation)
        low_ratio = ratio < (1 / 3)
        if low_ratio:
            warning = {
                "unit_id": current_unit,
                "clause_id": current_clause,
                "translation_ratio": round(ratio, 4),
                "warning": "translation_ratio_below_one_third",
                "routing": {"stage": "review_doctrine", "model": "gpt-5.6-sol", "effort": "xhigh"},
            }
            warnings = prog.setdefault("warnings", [])
            if warning not in warnings:
                warnings.append(warning)
        evidence = job.get("review_evidence", {}).get(current_unit, [])
        allowed_reference_ids = {
            item["source_id"] for item in evidence
            if isinstance(item, dict) and isinstance(item.get("source_id"), str)
        }
        input_payload = {
            "schema_version": "1.0", "stage": pass_name, "unit_id": current_unit,
            "source_hash": current_hash,
            "clause": {
                "clause_id": current_clause, "source": entry.source,
                "translation": entry.translation, "note": entry.note,
            },
            "glossary": json.loads(glossary_subset) if pass_name != "review_parallel" else [],
            "review_evidence": evidence if pass_name == "review_parallel" else [],
            "translation_ratio_warning": low_ratio,
        }
        effort = stage_effort(pass_name, low_ratio=low_ratio)
        call_context = {
            "volume": juan, "unit_id": current_unit, "clause_ids": [current_clause],
            "source_hash": current_hash, "glossary_hash": audit.sha256_text(glossary_subset),
        }
        prog.setdefault("review_attestations", {}).setdefault(current_unit, {}).pop(pass_name, None)
        raw = llm_call(
            job,
            prompt_text(pass_name, input_json=json_text(input_payload),
                        schema_json=json_text(REVIEW_SCHEMA)),
            pass_name,
            context=call_context,
            effort=effort,
        )
        payload = parse_json_contract(raw, pass_name)
        section_findings = validate_review_contract(
            payload, pass_name, current_unit, current_hash, current_clause,
            source=entry.source,
            translation=entry.translation,
            allowed_reference_ids=allowed_reference_ids,
        )
        verdicts.append({
            "round": round_number, "stage": pass_name, "unit_id": current_unit,
            "verdict": payload["verdict"], "finding_count": len(section_findings),
        })
        if payload["verdict"] == "pass":
            prog["review_attestations"][current_unit][pass_name] = {
                "stage": pass_name,
                "verdict": "pass",
                "review_event_id": call_context.get("_audit_event_ids", [])[-1],
                "model": stage_model(job, pass_name),
                "effort": effort,
                "source_sha256": current_hash,
                "translation_sha256": audit.sha256_text(entry.translation),
                "source_quote": entry.source,
                "translation_quote": entry.translation,
            }
        if pass_name == "review_terms" and payload.get("proposed_terms"):
            append_new_terms(json.dumps(payload["proposed_terms"], ensure_ascii=False), job)
            glossary = load_glossary()
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
        raw = llm_call(
            job,
            prompt_text("fix", input_json=json_text(input_payload),
                        schema_json=json_text(translation_schema(
                            "fix", current_unit, current_hash, current_clause,
                        ))),
            "fix",
            context={"volume": juan, "unit_id": current_unit, "clause_ids": [current_clause],
                     "source_hash": current_hash},
        )
        payload = parse_json_contract(raw, "fix")
        clause = validate_translation_contract(
            payload, "fix", current_unit, current_hash, current_clause,
        )
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
                          start: str, end: str) -> Path:
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
        clause = contract.get("clause", {})
        line_ids = lines_in_range(expected_line_ids, source_range.start, source_range.end)
        contract_valid = (
            contract.get("source_hash") == source_hash(entry.source)
            and
            clause.get("clause_id") == clause_id(entry)
            and clause.get("vernacular") == entry.translation
            and bool(str(clause.get("literal", "")).strip())
        )
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
    path = audit_store(job).job_dir / f"{job['work']}-{juan:03d}-coverage.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json_text(ledger) + "\n")
    prog["coverage_ledger"] = {
        "path": path.relative_to(ROOT).as_posix(),
        "sha256": audit.sha256_text(path.read_text(encoding="utf-8")),
    }
    return path


def run_content_checks(md_path: Path, data_path: Path, start: str, end: str) -> list[str]:
    translation_text = md_path.read_text(encoding="utf-8")
    source_lines = extract_lines(data_path)
    issues = list(check_ranges(
        parse_ranges(translation_text), data_line_ids(data_path), start, end,
        source_lines=source_lines,
    ))
    glossary = load_glossary()
    issues += validate_glossary(glossary) or term_issues(
        glossary, parse_term_entries(translation_text))
    return issues


def run_checks(job: dict, juan: int, md_path: Path, data_path: Path,
               start: str, end: str) -> list[str]:
    translation_text = md_path.read_text(encoding="utf-8")
    source_lines = extract_lines(data_path)
    issues = run_content_checks(md_path, data_path, start, end)
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
            issues += check_coverage_ledger(ledger, source_lines, start, end, translation_text)
    return issues


def record_quality_gate(job: dict, juan: int, md_path: Path, issues: list[str]) -> None:
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
    audit_store(job).append({
        "type": "quality_gate",
        "volume": juan,
        "verdict": "pass" if gate["passed"] else "fail",
        "blocking": True,
        "translation_sha256": gate["translation_sha256"],
        "review_verdict": gate["review_verdict"],
        "issues": issues,
        "warnings": gate["warnings"],
        "artifacts": {"translation": audit_store(job).artifact(
            "translation", md_path.read_text(encoding="utf-8"),
        )},
    })
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
    juan = bth.infer_juan(md_path)
    require_content_gate(job, juan, md_path)
    output = bth.translation_output_path(md_path, None)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(bth.render(entries, md_path, juan, bth.infer_title(text, juan)),
                      encoding="utf-8")
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
        atomic_write(paths["md"], render_skeleton(juan, start, end, segments, lines))

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


def cmd_run() -> None:
    JOBS_DIR.mkdir(exist_ok=True)
    LOCKS_DIR.mkdir(exist_ok=True)
    pid_fh = open(LOCKS_DIR / "runner.pid", "w")
    try:
        fcntl.flock(pid_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("another runner is already active")
    pid_fh.write(str(os.getpid()))
    pid_fh.flush()
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
    try:
        get_work(work)
    except ValueError as err:
        raise SystemExit(str(err))
    if not juans:
        raise SystemExit("specify --juans or a link with a juan number")
    if args.requested_parallel < 1:
        raise SystemExit("--requested-parallel must be at least 1")
    JOBS_DIR.mkdir(exist_ok=True)
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
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
    args = parser.parse_args()
    if args.cmd == "enqueue":
        cmd_enqueue(args)
    else:
        cmd_run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
