#!/usr/bin/env python3
"""Background job runner: LLM-translate juans, 3-pass review, build HTML, push.

Usage:
  python3 scripts/runner.py enqueue --juans 13-15 --model claude [--no-push]
  python3 scripts/runner.py enqueue --link https://cbetaonline.dila.edu.tw/zh/T1579_013 --model glm
  python3 scripts/runner.py run          # start worker threads (one per model)

State lives in jobs/*.json (atomic writes); a crash resumes from the last
saved step. Same-model jobs run sequentially, different models in parallel.
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
import shutil
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from string import Template
from subprocess import run as sh

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import llm
import build_translation_html as bth
import build_shengwen_di_html as bsh
from make_translation_skeleton import extract_lines, read_segments, render as render_skeleton, line_key
from check_translation_terms import validate_glossary, check_translation_terms as term_issues, parse_entries as parse_term_entries
from check_translation_coverage import parse_ranges, data_line_ids, check_ranges

JOBS_DIR = ROOT / "jobs"
LOCKS_DIR = ROOT / "locks"
PROMPTS_DIR = ROOT / "prompts"
WORKS_PATH = ROOT / "works.json"
STATUS_JSON = ROOT / "docs" / "status.json"
REVIEW_PASSES = ("review_terms", "review_doctrine", "review_parallel")
POLL_SECONDS = 10
MODEL_WAIT_FALLBACK_MINUTES = 15
DEFAULT_PARALLEL_JUANS = 2
MAX_PARALLEL_JUANS = 5
JOB_WRITE_LOCK = threading.RLock()
PUBLISH_LOCK = threading.RLock()
VERSIONS_DIR = ROOT / "translations" / "versions"
DUAL_TASKS = ("availability", "segment", "skeleton", "draft_codex", "draft_glm",
              "review", "checks", "repair", "html", "commit")
# Per-stage model + fallback defaults; a job may carry its own "stages" dict
# with the same shape (set from the web form).
DEFAULT_STAGES = {
    "segment": {"primary": "claude", "fallback": "codex"},
    "draft_codex": {"primary": "codex", "fallback": None},
    "draft_glm": {"primary": "glm", "fallback": None},
    "merge": {"primary": "claude", "fallback": "codex"},
    "repair": {"primary": "claude", "fallback": "codex"},
}

# Cost-optimal default routing for --model mix: bulk translation on the cheap
# GLM coding plan, hardest review line on claude, cross-vendor reviews so the
# reviewer doesn't share the translator's blind spots.
MIX_ROUTING = {
    "segment": "claude",
    "translate": "glm",
    "review_terms": "codex",
    "review_doctrine": "claude",
    "review_parallel": "codex",
}
# --model dual: codex and glm each produce a full draft file, claude reviews
# both against the source and settles the final text (replaces the 3-line
# review). dual-echo is the free offline variant for pipeline testing.
DUAL_ROUTING = {
    "segment": "claude",
    "draft_codex": "codex",
    "draft_glm": "glm",
    "merge": "claude",
    "repair": "claude",
}
DUAL_STAGES = ("draft_codex", "draft_glm")
QUEUES = llm.MODELS + ("mix", "dual") + llm.FAKE_MODELS + ("dual-echo",)
WORK_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9._-]*$")
MODEL_GATE = threading.Condition()
MODEL_ACTIVE = {model: 0 for model in llm.MODELS + llm.FAKE_MODELS}
ACTIVE_JOB_PARALLEL: dict[str, int] = {}


def work_info(work_id: str) -> dict:
    if not WORK_ID_RE.fullmatch(work_id):
        raise ValueError(f"無效的經典代號：{work_id}")
    works = json.loads(WORKS_PATH.read_text(encoding="utf-8"))["works"]
    match = next((w for w in works if w["id"] == work_id), None)
    if match is None:
        return {"id": work_id, "file_id": work_id, "title": work_id, "pipeline_ready": True}
    return {**match, "pipeline_ready": True}


def get_work(work_id: str) -> dict:
    return work_info(work_id)


def prompt_vars(job: dict) -> dict:
    info = work_info(job["work"])
    return {"work": info["id"], "work_title": info["title"], "file_id": info["file_id"]}


def job_stages(job: dict) -> dict:
    return {**DEFAULT_STAGES, **(job.get("stages") or {})}


def parse_parallel_juans(value) -> int:
    try:
        parallel = int(value)
    except (TypeError, ValueError):
        raise ValueError("parallel_juans must be an integer")
    if not 1 <= parallel <= MAX_PARALLEL_JUANS:
        raise ValueError(f"parallel_juans must be between 1 and {MAX_PARALLEL_JUANS}")
    return parallel


def job_parallel_juans(job: dict) -> int:
    return parse_parallel_juans(job.get("parallel_juans", DEFAULT_PARALLEL_JUANS))


def stage_fallback(job: dict, stage: str) -> str | None:
    if job["model"] == "dual-echo":
        return None
    return job_stages(job).get(stage, {}).get("fallback")


def stage_model(job: dict, stage: str, prog: dict | None = None) -> str:
    runtime = (prog or {}).get("models", {})
    if stage in runtime:
        return runtime[stage]
    model = job["model"]
    if model == "mix":
        return MIX_ROUTING.get(stage, "claude")
    if model == "dual":
        return job_stages(job).get(stage, {}).get("primary") or DUAL_ROUTING.get(stage, "claude")
    if model == "dual-echo":
        return "echo"
    return model


def is_dual(job: dict) -> bool:
    return job["model"] in ("dual", "dual-echo")


def task_name_for_stage(stage: str) -> str:
    return "review" if stage == "merge" else stage

TRANSLATION_BLOCK_RE = re.compile(r"(Translation:\n<<<\n)(.*?)(\n?>>>)", re.DOTALL)
NOTE_BLOCK_RE = re.compile(r"(Note:\n<<<\n)(.*?)(\n?>>>)", re.DOTALL)
OUT_TRANSLATION_RE = re.compile(r"<<<TRANSLATION\n(.*?)\n?>>>", re.DOTALL)
OUT_NOTE_RE = re.compile(r"<<<NOTE\n(.*?)\n?>>>", re.DOTALL)
TSV_RE = re.compile(r"<TSV>\n(.*?)\n?</TSV>", re.DOTALL)
NEW_TERMS_RE = re.compile(r"^NEW_TERMS:\s*(\[.*\])\s*$", re.MULTILINE)


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


def load_job(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save_job(job: dict) -> None:
    with JOB_WRITE_LOCK:
        job["updated"] = now_iso()
        persisted = {k: v for k, v in job.items() if not k.startswith("_")}
        atomic_write(JOBS_DIR / f"{job['id']}.json", json.dumps(persisted, ensure_ascii=False, indent=1))
        publish_status()


def publish_status() -> None:
    with JOB_WRITE_LOCK:
        jobs = []
        for path in sorted(JOBS_DIR.glob("*.json")):
            try:
                job = load_job(path)
            except (json.JSONDecodeError, OSError):
                continue
            job.pop("pid", None)
            jobs.append(job)
        atomic_write(STATUS_JSON, json.dumps({"generated": now_iso(), "jobs": jobs}, ensure_ascii=False))


def log(job: dict, msg: str) -> None:
    print(f"{now_iso()} [{job['id']}] {msg}", flush=True)
    with JOB_WRITE_LOCK:
        ring = job.setdefault("log", [])
        ring.append(f"{now_iso()} {msg}")
        del ring[:-50]  # keep only the last 50 entries; persisted by the next save_job


def effective_model_limit() -> int:
    return max(ACTIVE_JOB_PARALLEL.values(), default=DEFAULT_PARALLEL_JUANS)


@contextmanager
def model_gate(model: str):
    if model not in MODEL_ACTIVE:
        yield
        return
    with MODEL_GATE:
        while MODEL_ACTIVE[model] >= effective_model_limit():
            MODEL_GATE.wait()
        MODEL_ACTIVE[model] += 1
    try:
        yield
    finally:
        with MODEL_GATE:
            MODEL_ACTIVE[model] -= 1
            MODEL_GATE.notify_all()


def register_job_parallel(job_id: str, parallel: int) -> None:
    with MODEL_GATE:
        ACTIVE_JOB_PARALLEL[job_id] = parallel
        MODEL_GATE.notify_all()


def unregister_job_parallel(job_id: str) -> None:
    with MODEL_GATE:
        ACTIVE_JOB_PARALLEL.pop(job_id, None)
        MODEL_GATE.notify_all()


def run_llm_guarded(model: str, prompt: str, timeout: int = llm.TIMEOUT) -> llm.LLMResult:
    with model_gate(model):
        return llm.run_llm(model, prompt, timeout=timeout)


def record_model_event(prog: dict | None, stage: str, primary: str, fallback: str,
                       reason: str) -> None:
    if prog is None:
        return
    event = {
        "stage": stage,
        "from": primary,
        "to": fallback,
        "reason": reason[:300],
        "updated": now_iso(),
    }
    prog.setdefault("model_events", []).append(event)
    task_state(prog, task_name_for_stage(stage)).setdefault("model_events", []).append(event)


MODEL_STATUS_PATH = LOCKS_DIR / "model-status.json"  # not jobs/: that dir is globbed as jobs


def publish_model_status(statuses: dict) -> None:
    """Persist the latest probe results so the web UI can show an availability bar."""
    LOCKS_DIR.mkdir(exist_ok=True)
    with JOB_WRITE_LOCK:
        try:
            current = json.loads(MODEL_STATUS_PATH.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            current = {}
        current.update(statuses)
        atomic_write(MODEL_STATUS_PATH, json.dumps(current, ensure_ascii=False))


class JobHold(Exception):
    pass


class JobCancelled(Exception):
    pass


class VolumeCancelled(Exception):
    pass


class OptionalModelUnavailable(Exception):
    pass


def cancel_flag(job_id: str, juan: int | None = None) -> Path:
    # ponytail: sentinel files instead of json fields — save_job can never erase them
    suffix = f".{juan}.cancel" if juan is not None else ".cancel"
    return JOBS_DIR / f"{job_id}{suffix}"


def check_cancel(job: dict, juan: int | None = None) -> None:
    if cancel_flag(job["id"]).exists():
        raise JobCancelled(job["id"])
    if juan is not None and cancel_flag(job["id"], juan).exists():
        raise VolumeCancelled(juan)


def task_state(prog: dict, name: str) -> dict:
    return prog.setdefault("tasks", {}).setdefault(name, {"state": "pending"})


def set_task(prog: dict, name: str, state: str, **extra) -> dict:
    task = task_state(prog, name)
    task.update({"state": state, "updated": now_iso()})
    task.update({k: v for k, v in extra.items() if v is not None})
    return task


def reset_task_progress(task: dict, resume_step: str | None, name: str) -> None:
    if name != resume_step:
        task["section"] = 0


def mark_failed_running_task(prog: dict, error: str) -> None:
    for name, task in prog.get("tasks", {}).items():
        if task.get("state") == "running":
            set_task(prog, name, "failed", error=error[:500])
            return
    task_name = task_name_for_stage(prog.get("step", ""))
    if task_name in prog.get("tasks", {}):
        set_task(prog, task_name, "failed", error=error[:500])


def mark_cancelled_tasks(prog: dict) -> None:
    for name in DUAL_TASKS:
        task = task_state(prog, name)
        if task.get("state") in (None, "pending", "queued"):
            set_task(prog, name, "cancelled")


def can_cancel_juan(prog: dict | None) -> bool:
    if not prog:
        return True
    if prog.get("cancelled") or prog.get("step") == "cancelled":
        return False
    if prog.get("step") not in (None, "queued", "availability", "waiting_model"):
        return False
    for name, task in prog.get("tasks", {}).items():
        if name == "availability":
            continue
        if task.get("state") not in (None, "pending", "queued", "cancelled"):
            return False
    return True


def cancel_juan(job_id: str, juan: int) -> tuple[bool, str]:
    path = JOBS_DIR / f"{job_id}.json"
    if not path.exists():
        return False, "job not found"
    with flock("jobs"):
        job = load_job(path)
        if juan not in job.get("juans", []):
            return False, "juan not in job"
        prog = job.setdefault("progress", {}).setdefault(str(juan), {})
        if prog.get("cancelled") or prog.get("step") in ("cancelled", "done"):
            return False, "此卷已完成或已取消"
        if can_cancel_juan(prog):  # not started yet: cancel immediately
            prog["cancelled"] = True
            prog["step"] = "cancelled"
            mark_cancelled_tasks(prog)
            save_job(job)
            return True, "已取消"
        # mid-processing: raise the per-volume flag; runner stops at the next section
        cancel_flag(job_id, juan).touch()
        return True, "已要求取消，將於下一段落停止"


def cancel_job(job_id: str) -> tuple[bool, str]:
    path = JOBS_DIR / f"{job_id}.json"
    if not path.exists():
        return False, "job not found"
    with flock("jobs"):
        job = load_job(path)
        state = job.get("state")
        if state in ("done", "cancelled"):
            return False, f"job already {state}"
        if state in ("queued", "awaiting_approval", "failed"):
            job["state"] = "cancelled"
            save_job(job)
            return True, "cancelled"
        # running / waiting: raise the flag; the runner cancels at the next section
        cancel_flag(job_id).touch()
        return True, "cancel requested; stops at the next section"


def reset_juan(prog: dict) -> None:
    """Reset one volume's progress for a fresh attempt: drop failure/retry state,
    unstick the step, and requeue any failed/cancelled/waiting tasks."""
    for key in ("error", "auto_retried", "repaired", "cancelled", "force_fallback"):
        prog.pop(key, None)
    prog.pop("step", None)
    for task in prog.get("tasks", {}).values():
        if task.get("state") in ("failed", "cancelled", "waiting"):
            task["state"] = "pending"
            task.pop("error", None)


def retry_job(job_id: str) -> tuple[bool, str]:
    path = JOBS_DIR / f"{job_id}.json"
    if not path.exists():
        return False, "job not found"
    with flock("jobs"):
        job = load_job(path)
        if job.get("state") not in ("failed", "cancelled"):
            return False, "only failed or cancelled jobs can be retried"
        cancel_flag(job_id).unlink(missing_ok=True)
        for stale in JOBS_DIR.glob(f"{job_id}.*.cancel"):  # per-volume sentinels too
            stale.unlink(missing_ok=True)
        job["state"] = "queued"
        job["error"] = None
        job["resume_at"] = None
        for prog in job.get("progress", {}).values():
            if prog.get("step") == "done":
                continue  # keep successful volumes untouched
            reset_juan(prog)
        save_job(job)
    return True, "requeued"


def retry_juan(job_id: str, juan: int) -> tuple[bool, str]:
    path = JOBS_DIR / f"{job_id}.json"
    if not path.exists():
        return False, "job not found"
    with flock("jobs"):
        job = load_job(path)
        if juan not in job.get("juans", []):
            return False, "juan not in job"
        prog = job.setdefault("progress", {}).setdefault(str(juan), {})
        if prog.get("step") == "done":
            return False, "此卷已完成"
        cancel_flag(job_id, juan).unlink(missing_ok=True)
        reset_juan(prog)
        if job.get("state") in ("failed", "cancelled", "done"):
            job["state"] = "queued"  # run_juan short-circuits already-done volumes
        save_job(job)
    return True, "已重排此卷"


def force_resume_step(prog: dict) -> str:
    step_map = {
        "availability": "availability",
        "segment": "segment",
        "skeleton": "skeleton",
        "draft_codex": "draft_codex",
        "draft_glm": "draft_glm",
        "review": "merge",
        "checks": "checks",
        "repair": "repair",
        "html": "html",
        "commit": "commit",
    }
    current = prog.get("step")
    if current in {"availability", "segment", "skeleton", "draft_codex", "draft_glm",
                   "merge", "checks", "repair", "html", "commit"}:
        return current
    for name in DUAL_TASKS:
        task = prog.get("tasks", {}).get(name, {})
        if task.get("state") in {"waiting", "failed", "running"}:
            return step_map.get(name, "queued")
    return "queued"


def force_start_juan(job_id: str, juan: int) -> tuple[bool, str]:
    path = JOBS_DIR / f"{job_id}.json"
    if not path.exists():
        return False, "job not found"
    with flock("jobs"):
        job = load_job(path)
        if juan not in job.get("juans", []):
            return False, "juan not in job"
        prog = job.setdefault("progress", {}).setdefault(str(juan), {})
        if prog.get("step") == "done":
            return False, "此卷已完成"
        cancel_flag(job_id).unlink(missing_ok=True)
        cancel_flag(job_id, juan).unlink(missing_ok=True)
        resume_step = force_resume_step(prog)
        for key in ("cancelled", "resume_at", "error"):
            prog.pop(key, None)
        prog["step"] = resume_step
        prog["force_started"] = now_iso()
        for task in prog.get("tasks", {}).values():
            if task.get("state") in ("waiting", "failed", "cancelled"):
                task["state"] = "pending"
                task.pop("error", None)
                task.pop("resume_at", None)
        if job.get("state") != "running":
            job["state"] = "queued"
        job["resume_at"] = None
        job["error"] = None
        save_job(job)
    return True, "已強制從目前進度續跑此卷"


# ---------- reprocess approval / versioning ----------

def juan_translated(work: str, juan: int) -> bool:
    md = juan_paths(work, juan)["md"]
    if not md.exists():
        return False
    try:
        return any(e.translation.strip() for e in bth.parse_entries(md.read_text(encoding="utf-8")))
    except ValueError:
        return False


def archive_version(work: str, juan: int) -> int:
    """Move the current translation aside as v<N>; drafts go stale and are removed."""
    paths = juan_paths(work, juan)
    VERSIONS_DIR.mkdir(exist_ok=True)
    stem = paths["md"].stem
    n = 1 + max((int(m.group(1)) for p in VERSIONS_DIR.glob(f"{stem}.v*.md")
                 if (m := re.search(r"\.v(\d+)\.md$", p.name))), default=0)
    shutil.move(paths["md"], VERSIONS_DIR / f"{stem}.v{n}.md")
    for key in ("draft_codex", "draft_glm"):
        paths[key].unlink(missing_ok=True)
    return n


def approve_job(job_id: str) -> tuple[bool, str]:
    path = JOBS_DIR / f"{job_id}.json"
    if not path.exists():
        return False, "job not found"
    with flock("jobs"):
        job = load_job(path)
        if job.get("state") != "awaiting_approval":
            return False, "job is not awaiting approval"
        archived = []
        for juan in job.get("needs_approval", []):
            if juan_translated(job["work"], juan):
                archived.append(f"卷{juan}→v{archive_version(job['work'], juan)}")
        job["state"] = "queued"
        job["approved"] = now_iso()
        save_job(job)
    return True, "已核准重跑；" + ("、".join(archived) if archived else "無需封存")


def resume_time(result: llm.LLMResult) -> datetime:
    now = datetime.now().astimezone()
    if result.resume_at and now < result.resume_at <= now + timedelta(hours=6):
        return result.resume_at
    return now + timedelta(minutes=MODEL_WAIT_FALLBACK_MINUTES)


def probe_dual_models(job: dict, juan: int, prog: dict) -> dict[str, str]:
    """Probe every model the job's stage config references; resolve each stage
    to primary-if-available else fallback-if-available. Draft stages with no
    usable model are skipped (one draft is enough); no usable review model or
    no usable draft at all holds the job until the earliest known reset."""
    availability = set_task(prog, "availability", "running")
    prog["step"] = "availability"
    save_job(job)
    if job["model"] == "dual-echo":
        availability["models"] = {m: {"state": "available"} for m in ("codex", "glm", "claude")}
        prog["models"] = {stage: "echo" for stage in ("segment", "draft_codex", "draft_glm", "merge", "repair")}
        set_task(prog, "availability", "done")
        save_job(job)
        return prog["models"]

    stages = job_stages(job)
    wanted = {cfg.get(k) for cfg in stages.values() for k in ("primary", "fallback") if cfg.get(k)}
    statuses: dict[str, dict] = {}
    for model in sorted(wanted):
        result = llm.availability_probe(model)
        if result.ok:
            statuses[model] = {"state": "available", "checked": now_iso()}
        else:
            statuses[model] = {
                "state": "limited" if result.limit else "unavailable",
                "checked": now_iso(),
                "resume_at": result.resume_at.isoformat(timespec="seconds") if result.resume_at else None,
                "error": result.error[:300],
            }
    availability["models"] = statuses
    prog["availability"] = statuses
    publish_model_status(statuses)

    def usable(model: str | None) -> bool:
        return bool(model) and statuses.get(model, {}).get("state") == "available"

    force_fallback = prog.get("force_fallback")  # 2nd auto-retry: prefer fallbacks
    stage_models: dict[str, str] = {}
    for stage, cfg in stages.items():
        primary, fallback = cfg.get("primary"), cfg.get("fallback")
        if force_fallback and usable(fallback):
            stage_models[stage] = fallback
        elif usable(primary):
            stage_models[stage] = primary
        elif usable(fallback):
            stage_models[stage] = fallback

    # draft 1 is required; draft 2 is strictly optional
    if "draft_codex" in stage_models and "merge" in stage_models:
        for stage in DUAL_STAGES:
            if stage not in stage_models:
                reason = ("未選用（單稿模式）" if not stages[stage].get("primary")
                          else "模型不可用，僅以草稿 1 進行")
                set_task(prog, stage, "skipped", model=stages[stage].get("primary"), reason=reason)
        for stage in ("segment", "repair"):  # never blockers: borrow the review model
            stage_models.setdefault(stage, stage_models["merge"])
        prog["models"] = stage_models
        set_task(prog, "availability", "done")
        save_job(job)
        return stage_models

    # nothing usable for drafts or review: hold until the earliest known reset
    resumes = [datetime.fromisoformat(s["resume_at"]) for s in statuses.values() if s.get("resume_at")]
    resume_at = (min(resumes) + timedelta(minutes=5)) if resumes else (
        datetime.now().astimezone() + timedelta(minutes=MODEL_WAIT_FALLBACK_MINUTES))
    prog["resume_at"] = resume_at.isoformat(timespec="seconds")
    prog["error"] = "所需模型均不可用，等待額度重置"
    prog["step"] = "waiting_model"
    set_task(prog, "availability", "waiting", resume_at=prog["resume_at"])
    save_job(job)
    raise JobHold(f"juan {juan}: no usable models, waiting until {prog['resume_at']}")


def prompt_text(name: str, **vars) -> str:
    return Template((PROMPTS_DIR / f"{name}.txt").read_text(encoding="utf-8")).substitute(**vars)


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

def glossary_path(work: str) -> Path:
    return ROOT / "translations" / "glossary" / f"{work}-terms.json"


def load_glossary(work: str) -> dict:
    path = glossary_path(work)
    if not path.exists():
        return {"work": work, "terms": []}
    return json.loads(path.read_text(encoding="utf-8"))


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
        glossary = load_glossary(job["work"])
        existing = {t["id"] for t in glossary["terms"]}
        added = [t for t in new_terms if isinstance(t, dict) and t.get("id") not in existing]
        candidate = {**glossary, "terms": glossary["terms"] + added}
        if added and not validate_glossary(candidate):
            path = glossary_path(job["work"])
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(path, json.dumps(candidate, ensure_ascii=False, indent=2))
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


def parse_llm_blocks(raw: str) -> tuple[str, str | None]:
    tr = OUT_TRANSLATION_RE.search(raw)
    if not tr or not tr.group(1).strip():
        raise ValueError(f"no <<<TRANSLATION block in output: {raw[:200]!r}")
    if "<<<" in tr.group(1):
        raise ValueError("nested markers in translation output")
    nt = OUT_NOTE_RE.search(raw)
    return tr.group(1).strip(), (nt.group(1).strip() if nt else None)


# ---------- pipeline steps ----------

def llm_call(job: dict, prompt: str, stage: str, juan: int | None = None) -> str:
    prog = job["progress"].setdefault(str(juan), {}) if juan is not None else None
    task_name = task_name_for_stage(stage)

    def on_wait(resume_at):
        if prog is not None:
            prog["step"] = "waiting_limit"
            prog["resume_at"] = resume_at.isoformat(timespec="seconds")
            set_task(prog, task_name, "waiting", model=model, resume_at=prog["resume_at"])
        else:
            job["state"] = "waiting_limit"
            job["resume_at"] = resume_at.isoformat(timespec="seconds")
        save_job(job)

    check_cancel(job, juan)
    model = stage_model(job, stage, prog)
    fallback = stage_fallback(job, stage)
    if fallback and fallback != model:
        # one shot on the primary; any failure switches this stage to the fallback
        try:
            result = run_llm_guarded(model, prompt)
        except (llm.LLMError, FileNotFoundError) as err:
            result = llm.LLMResult(ok=False, error=str(err))
        if result.ok:
            return result.text
        log(job, f"[{model}] {stage} failed ({result.error[:160]}), falling back to {fallback}")
        record_model_event(prog, stage, model, fallback, result.error)
        if prog is not None:
            prog.setdefault("models", {})[stage] = fallback
            set_task(prog, task_name, task_state(prog, task_name).get("state", "running"),
                     model=fallback)
            save_job(job)
        model = fallback
    elif stage in DUAL_STAGES and is_dual(job):
        # draft without fallback: one shot, then skip this draft (the other one carries)
        try:
            result = run_llm_guarded(model, prompt)
        except (llm.LLMError, FileNotFoundError) as err:
            result = llm.LLMResult(ok=False, error=str(err))
        if result.ok:
            return result.text
        raise OptionalModelUnavailable(f"{model} unavailable during {stage}: {result.error[:300]}")
    text = llm.call_with_limit_retry(model, prompt, on_wait=on_wait, log=lambda m: log(job, m),
                                     run_fn=run_llm_guarded)
    if prog is not None and task_state(prog, task_name).get("state") == "waiting":
        set_task(prog, task_name, "running", model=model)
        prog.pop("resume_at", None)
        if prog.get("step") == "waiting_limit":
            prog["step"] = stage
        save_job(job)
    elif job["state"] == "waiting_limit":
        job["state"] = "running"
        job["resume_at"] = None
        save_job(job)
    return text


def llm_segment(job: dict, juan: int, lines: dict[str, str], tsv_path: Path) -> None:
    ordered = sorted(((i, t) for i, t in lines.items() if t), key=lambda kv: line_key(kv[0]))
    lines_text = "\n".join(f"{i}\t{t}" for i, t in ordered)
    last = ordered[min(3, len(ordered) - 1)][0]
    range_example = f"{ordered[0][0]}-p{last.split('_p')[1]}"  # built from this juan's real line ids
    prompt = prompt_text("segment", juan=juan, lines=lines_text,
                         range_example=range_example, **prompt_vars(job))
    errors = ""
    for attempt in range(2):
        raw = llm_call(job, prompt + errors, "segment", juan)
        match = TSV_RE.search(raw)
        if match:
            # normalize each row to exactly 3 columns (LLMs drop the empty note column)
            rows = [parts[:3] + [""] * (3 - len(parts))
                    for line in match.group(1).splitlines()
                    if (parts := [p.strip() for p in line.split("\t")]) and parts[0]
                    and not parts[0].startswith("#")]
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
        else:
            errors = "\n\n前次輸出缺少 <TSV> 標記，請依格式重新輸出。"
        tsv_path.unlink(missing_ok=True)
        log(job, f"juan {juan}: segmentation attempt {attempt + 1} invalid")
    raise RuntimeError(f"juan {juan}: LLM segmentation failed twice")


def translate_sections(job: dict, juan: int, md_path: Path, prog: dict,
                       only_titles: set[str] | None = None, extra: str = "",
                       stage: str = "translate") -> None:
    glossary = load_glossary(job["work"])
    entries = bth.parse_entries(md_path.read_text(encoding="utf-8"))
    prog["sections_total"] = len(entries)
    for idx, entry in enumerate(entries):
        if only_titles is not None and entry.title not in only_titles:
            continue
        if only_titles is None and entry.translation.strip():
            continue  # never overwrite existing text (human edits win)
        raw = llm_call(job, prompt_text(
            "translate", juan=juan, title=entry.title, note=entry.note or "（無）",
            source=entry.source, glossary=glossary_subset_json(glossary, entry.source),
            extra=extra, **prompt_vars(job)), stage, juan)
        translation, note = parse_llm_blocks(raw)
        splice(md_path, idx, translation, note if note else None)
        prog["section"] = idx + 1
        save_job(job)
        log(job, f"juan {juan}: {stage} {idx + 1}/{len(entries)} {entry.title}")
        entries = bth.parse_entries(md_path.read_text(encoding="utf-8"))  # re-read after splice


def merge_sections(job: dict, juan: int, paths: dict[str, Path], prog: dict) -> None:
    """Review available drafts against the source and settle the final text."""
    glossary = load_glossary(job["work"])
    entries = bth.parse_entries(paths["md"].read_text(encoding="utf-8"))
    draft_a = bth.parse_entries(paths["draft_codex"].read_text(encoding="utf-8"))
    draft_b = (bth.parse_entries(paths["draft_glm"].read_text(encoding="utf-8"))
               if paths["draft_glm"].exists() else draft_a)
    if not (len(entries) == len(draft_a) == len(draft_b)):
        raise RuntimeError(f"juan {juan}: draft/skeleton section counts differ "
                           f"({len(entries)}/{len(draft_a)}/{len(draft_b)})")
    prog["sections_total"] = len(entries)
    for idx, entry in enumerate(entries):
        if entry.translation.strip():
            continue  # resumable; human edits win
        raw = llm_call(job, prompt_text(
            "merge", juan=juan, title=entry.title, note=entry.note or "（無）",
            source=entry.source, glossary=glossary_subset_json(glossary, entry.source),
            draft_a=draft_a[idx].translation or "（缺稿）",
            draft_b=draft_b[idx].translation or "（缺稿）", **prompt_vars(job)), "merge", juan)
        translation, note = parse_llm_blocks(raw)
        splice(paths["md"], idx, translation, note)
        prog["section"] = idx + 1
        save_job(job)
        log(job, f"juan {juan}: merge {idx + 1}/{len(entries)} {entry.title}")
        entries = bth.parse_entries(paths["md"].read_text(encoding="utf-8"))


def review_pass(job: dict, juan: int, md_path: Path, pass_name: str, prog: dict) -> None:
    glossary = load_glossary(job["work"])
    entries = bth.parse_entries(md_path.read_text(encoding="utf-8"))
    start_at = prog.get("section", 0)
    for idx, entry in enumerate(entries):
        if idx < start_at:
            continue
        vars = dict(juan=juan, title=entry.title, source=entry.source,
                    translation=entry.translation, note=entry.note or "（無）",
                    **prompt_vars(job))
        if pass_name != "review_parallel":
            vars["glossary"] = glossary_subset_json(glossary, entry.source)
        raw = llm_call(job, prompt_text(pass_name, **vars), pass_name, juan)
        new_terms = NEW_TERMS_RE.search(raw)
        if pass_name == "review_terms" and new_terms:
            append_new_terms(new_terms.group(1), job)
            glossary = load_glossary(job["work"])
        if raw.strip() != "OK" and OUT_TRANSLATION_RE.search(raw):
            translation, note = parse_llm_blocks(raw)
            splice(md_path, idx, translation, note)
            log(job, f"juan {juan}: {pass_name} revised {entry.title}")
        prog["section"] = idx + 1
        save_job(job)
        entries = bth.parse_entries(md_path.read_text(encoding="utf-8"))


def run_checks(job: dict, juan: int, md_path: Path, data_path: Path,
               start: str, end: str) -> list[str]:
    issues = list(check_ranges(parse_ranges(md_path.read_text(encoding="utf-8")),
                               data_line_ids(data_path), start, end))
    glossary = load_glossary(job["work"])
    if glossary.get("terms"):
        issues += validate_glossary(glossary) or term_issues(
            glossary, parse_term_entries(md_path.read_text(encoding="utf-8")))
    return issues


def build_simple_work_index(work: str) -> None:
    info = work_info(work)
    pages = []
    for md in sorted((ROOT / "translations").glob(f"{work}-*-baihua.md")):
        try:
            _work, juan = bth.infer_work_juan(md)
        except ValueError:
            continue
        pages.append((juan, f"translations/{md.stem}.html"))
    items = "\n".join(
        f"      <li class=\"level-0\"><a href=\"{href}\">{info['title']} 卷第{juan}</a></li>"
        for juan, href in pages
    )
    out_dir = ROOT / "docs" / work
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "index.html").write_text(f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{info['title']}（{work}）</title>
  <link rel="stylesheet" href="../style.css">
  <script src="../theme.js"></script>
</head>
<body>
  <div class="topbar">
    <a class="topbar-brand" href="../index.html">總目錄</a>
    <button class="theme-toggle" type="button" aria-label="切換深色或淺色模式"></button>
  </div>
  <header class="site-header">
    <div class="kicker">CBETA {work}</div>
    <h1>{info['title']}</h1>
    <p>{info.get('subtitle', '')}</p>
  </header>
  <main class="toc-list">
    <ol>
{items}
    </ol>
  </main>
</body>
</html>
""", encoding="utf-8")


def build_html(job: dict, md_path: Path) -> None:
    bth.build_page(md_path)  # latest + any archived versions (version selector)
    if job["work"] == "T1579":
        bsh.main()  # refresh section pages + index (includes new translation link)
    else:
        build_simple_work_index(job["work"])
    import build_search_index
    build_search_index.main()


def git_commit_push(job: dict, juan: int) -> None:
    if not job.get("push", True):
        log(job, f"juan {juan}: --no-push, skipping commit")
        return
    with flock("git"):
        publish_status()
        sh(["git", "add", "-A", "data", "translations", "docs"], cwd=ROOT, check=True)
        diff = sh(["git", "diff", "--cached", "--quiet"], cwd=ROOT)
        if diff.returncode == 0:
            return  # nothing staged
        msg = f"(feat) {job['work']} juan {juan} vernacular translation via {job['model']} [auto]"
        sh(["git", "commit", "-m", msg], cwd=ROOT, check=True)
        for attempt in (1, 2):
            pull = sh(["git", "pull", "--rebase", "origin", "main"], cwd=ROOT)
            push = sh(["git", "push", "origin", "main"], cwd=ROOT)
            if push.returncode == 0:
                log(job, f"juan {juan}: pushed")
                return
        raise RuntimeError("git push failed twice")


def run_juan(job: dict, juan: int) -> None:
    check_cancel(job, juan)
    work = job["work"]
    paths = juan_paths(work, juan)
    prog = job["progress"].setdefault(str(juan), {})
    if prog.get("cancelled"):
        prog["step"] = "cancelled"
        mark_cancelled_tasks(prog)
        save_job(job)
        return
    if prog.get("step") == "done":
        return
    resume_step = prog.get("step")  # step saved before a crash/restart, if any

    if is_dual(job):
        probe_dual_models(job, juan, prog)
    else:
        prog["models"] = {}

    prog["step"] = "fetch"
    save_job(job)
    data_path = ensure_data(work, juan)
    lines = extract_lines(data_path)
    start, end = juan_bounds(lines)

    if not paths["tsv"].exists() and not paths["md"].exists():
        # segmentation only feeds the skeleton; an existing md makes it moot
        set_task(prog, "segment", "running", model=stage_model(job, "segment", prog))
        prog["step"] = "segment"
        save_job(job)
        llm_segment(job, juan, lines, paths["tsv"])
        set_task(prog, "segment", "done")

    if not paths["md"].exists():
        set_task(prog, "skeleton", "running")
        prog["step"] = "skeleton"
        save_job(job)
        segments = read_segments(paths["tsv"])
        paths["md"].parent.mkdir(exist_ok=True)
        info = work_info(work)
        atomic_write(paths["md"], render_skeleton(
            juan, start, end, segments, lines, work=work, work_title=info["title"]))
        set_task(prog, "skeleton", "done")
        save_job(job)

    if is_dual(job):
        # Codex and GLM drafts are independent; run both when both are available.
        def run_draft(stage: str) -> None:
            draft = paths[stage]
            if not draft.exists():
                draft.parent.mkdir(exist_ok=True)
                shutil.copyfile(paths["md"], draft)
            task = task_state(prog, stage)
            if task.get("state") == "done":
                return
            reset_task_progress(task, resume_step, stage)
            set_task(prog, stage, "running", model=stage_model(job, stage, prog))
            prog["step"] = stage
            save_job(job)
            try:
                translate_sections(job, juan, draft, task, stage=stage)
            except OptionalModelUnavailable as err:
                set_task(prog, stage, "skipped", model=stage_model(job, stage, prog), reason=str(err)[:500])
                save_job(job)
                return
            except Exception as err:
                set_task(prog, stage, "failed", error=str(err)[:500])
                save_job(job)
                raise
            set_task(prog, stage, "done")
            save_job(job)

        draft_stages = [stage for stage in DUAL_STAGES if stage in prog.get("models", {})]
        if len(draft_stages) > 1:
            with ThreadPoolExecutor(max_workers=len(draft_stages)) as pool:
                futures = [pool.submit(run_draft, stage) for stage in draft_stages]
                for future in futures:
                    future.result()
        else:
            for stage in draft_stages:
                run_draft(stage)

        if all(task_state(prog, s).get("state") == "skipped" for s in DUAL_STAGES):
            raise RuntimeError(f"juan {juan}: 兩份草稿的模型都不可用")

        review_task = task_state(prog, "review")
        if review_task.get("state") != "done":
            reset_task_progress(review_task, resume_step, "merge")
            set_task(prog, "review", "running", model=stage_model(job, "merge", prog))
            prog["step"] = "merge"
            save_job(job)
            try:
                merge_sections(job, juan, paths, review_task)
            except Exception as err:
                set_task(prog, "review", "failed", error=str(err)[:500])
                save_job(job)
                raise
            set_task(prog, "review", "done")
            save_job(job)
    else:
        prog["step"] = "translate"
        save_job(job)
        translate_sections(job, juan, paths["md"], prog)

        for pass_name in REVIEW_PASSES:
            if pass_name in prog.get("review_done", []):
                continue
            if pass_name != resume_step:  # keep section counter when resuming mid-pass
                prog["section"] = 0
            prog["step"] = pass_name
            save_job(job)
            review_pass(job, juan, paths["md"], pass_name, prog)
            prog.setdefault("review_done", []).append(pass_name)
            prog["section"] = 0
            save_job(job)

    set_task(prog, "checks", "running")
    prog["step"] = "checks"
    save_job(job)
    issues = run_checks(job, juan, paths["md"], data_path, start, end)
    if issues and not prog.get("repaired"):
        # one repair round: re-translate the sections named in term-check issues
        bad_titles = {i.split(":")[0].strip() for i in issues if ":" in i}
        log(job, f"juan {juan}: checks failed ({len(issues)}), repairing {len(bad_titles)} sections")
        prog["repaired"] = True
        repair_task = task_state(prog, "repair")
        repair_task["section"] = 0
        set_task(prog, "repair", "running", model=stage_model(job, "repair", prog))
        save_job(job)
        translate_sections(job, juan, paths["md"], repair_task, only_titles=bad_titles,
                           extra=("重要：前次譯文未通過術語檢查。以下每一條「lacks one of」後列出的詞，"
                                  "你的譯文必須一字不差地包含其中至少一個（可用「白話（玄奘詞）」形式嵌入）："
                                  + "；".join(issues[:10])),
                           stage="repair")
        set_task(prog, "repair", "done")
        issues = run_checks(job, juan, paths["md"], data_path, start, end)
    if issues:
        set_task(prog, "checks", "failed", error="; ".join(issues[:10])[:500])
        raise RuntimeError(f"juan {juan} checks failed: " + "; ".join(issues[:10]))
    set_task(prog, "checks", "done")

    with PUBLISH_LOCK:
        with flock("publish"):
            set_task(prog, "html", "running")
            prog["step"] = "html"
            save_job(job)
            build_html(job, paths["md"])
            set_task(prog, "html", "done")

            set_task(prog, "commit", "running")
            prog["step"] = "commit"
            save_job(job)
            git_commit_push(job, juan)
            set_task(prog, "commit", "done")

    prog["step"] = "done"
    save_job(job)


def run_juan_with_retries(job: dict, juan: int) -> dict:
    try:
        run_juan(job, juan)
        return {"state": "done", "juan": juan}
    except JobHold as err:
        log(job, str(err))
        return {"state": "held", "juan": juan, "error": str(err)}
    except VolumeCancelled:
        prog = job["progress"].setdefault(str(juan), {})
        prog["cancelled"] = True
        prog["step"] = "cancelled"
        mark_cancelled_tasks(prog)
        cancel_flag(job["id"], juan).unlink(missing_ok=True)
        save_job(job)
        log(job, f"juan {juan} cancelled by user")
        return {"state": "cancelled", "juan": juan}
    except JobCancelled:
        log(job, "job cancelled by user")
        return {"state": "job_cancelled", "juan": juan}
    except Exception as err:  # noqa: BLE001 — job must survive a bad juan
        prog = job["progress"].setdefault(str(juan), {})
        raw_retried = prog.get("auto_retried", 0)
        retried = int(raw_retried) if isinstance(raw_retried, int) else int(bool(raw_retried))
        while retried < 2:
            retried += 1
            prog["auto_retried"] = retried
            prog.pop("error", None)
            prog.pop("repaired", None)  # fresh repair budget on each rerun
            prog.pop("resume_at", None)
            for task in prog.get("tasks", {}).values():
                if task.get("state") == "failed":
                    task["state"] = "pending"
                    task.pop("error", None)
            if retried >= 2:
                prog["force_fallback"] = True  # probe_dual_models prefers fallbacks
            group = "備援模型組" if prog.get("force_fallback") else "同組模型"
            save_job(job)
            log(job, f"juan {juan} failed ({str(err)[:200]}); auto-retry #{retried} 用{group}")
            try:
                run_juan(job, juan)
                return {"state": "done", "juan": juan}
            except JobHold as err2:
                log(job, str(err2))
                return {"state": "held", "juan": juan, "error": str(err2)}
            except VolumeCancelled:
                prog["cancelled"] = True
                prog["step"] = "cancelled"
                mark_cancelled_tasks(prog)
                cancel_flag(job["id"], juan).unlink(missing_ok=True)
                save_job(job)
                log(job, f"juan {juan} cancelled by user")
                return {"state": "cancelled", "juan": juan}
            except JobCancelled:
                log(job, "job cancelled by user")
                return {"state": "job_cancelled", "juan": juan}
            except Exception as err2:  # noqa: BLE001
                err = err2
        prog["error"] = str(err)[:500]
        mark_failed_running_task(prog, str(err))
        save_job(job)
        log(job, f"juan {juan} FAILED after {retried} retries: {err}")
        return {"state": "failed", "juan": juan, "error": str(err)}


def runnable_juans(job: dict) -> list[int]:
    juans = []
    for juan in job["juans"]:
        prog = job["progress"].setdefault(str(juan), {})
        if prog.get("cancelled") or prog.get("step") in ("cancelled", "done"):
            continue
        prog.setdefault("step", "queued")
        juans.append(juan)
    return juans


def earliest_resume(progress: dict) -> str | None:
    resumes = [p.get("resume_at") for p in progress.values() if p.get("resume_at")]
    return min(resumes) if resumes else None


def run_job(job: dict) -> None:
    parallel = job_parallel_juans(job)
    register_job_parallel(job["id"], parallel)
    try:
        run_job_registered(job, parallel)
    finally:
        unregister_job_parallel(job["id"])


def run_job_registered(job: dict, parallel: int) -> None:
    job["state"] = "running"
    job["pid"] = os.getpid()
    job["resume_at"] = None
    job["error"] = None
    job["parallel_juans"] = parallel
    juans = runnable_juans(job)
    save_job(job)
    if job.get("summary"):
        log(job, "summary: not implemented")
    failed = []
    held = []
    job_cancelled = False
    next_idx = 0
    active = {}
    with ThreadPoolExecutor(max_workers=parallel) as pool:
        while next_idx < len(juans) or active:
            while not job_cancelled and next_idx < len(juans) and len(active) < parallel:
                if cancel_flag(job["id"]).exists():
                    job_cancelled = True
                    break
                juan = juans[next_idx]
                next_idx += 1
                active[pool.submit(run_juan_with_retries, job, juan)] = juan
            if not active:
                break
            done, _ = wait(active, return_when=FIRST_COMPLETED)
            for future in done:
                active.pop(future, None)
                result = future.result()
                state = result["state"]
                if state == "failed":
                    failed.append(result["juan"])
                elif state == "held":
                    held.append(result["juan"])
                elif state == "job_cancelled":
                    job_cancelled = True
            if job_cancelled:
                next_idx = len(juans)
    if job_cancelled or cancel_flag(job["id"]).exists():
        cancel_flag(job["id"]).unlink(missing_ok=True)
        job["state"] = "cancelled"
        job["error"] = None
        save_job(job)
        log(job, "job finished: cancelled")
        return
    cancel_flag(job["id"]).unlink(missing_ok=True)
    if failed:
        job["state"] = "failed"
        job["error"] = f"juans failed: {failed}"
    elif held:
        job["state"] = "waiting_model"
        job["resume_at"] = earliest_resume(job.get("progress", {}))
        job["error"] = f"juans waiting: {held}"
    else:
        job["state"] = "done"
        job["error"] = None
    save_job(job)
    log(job, f"job finished: {job['state']}")


# ---------- queue / workers ----------

def claimable(job: dict, model: str) -> bool:
    if job.get("model") != model:
        return False
    state = job.get("state")
    if state == "queued":
        return True
    if state in ("running", "waiting_limit", "waiting_model"):
        pid = job.get("pid")
        if pid == os.getpid():
            return state in ("waiting_limit", "waiting_model") and job.get("id") not in ACTIVE_JOB_PARALLEL
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
                    time.sleep(min(wait + 300, POLL_SECONDS))  # short poll so force-start wakes quickly
                    continue
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
    match = re.search(r"/([A-Za-z][A-Za-z0-9.-]*)(?:_(\d{1,3}))?/?$", link.strip())
    if not match:
        raise ValueError(f"cannot parse CBETA link: {link}")
    return match.group(1), int(match.group(2)) if match.group(2) else None


def cmd_enqueue(args) -> None:
    work = args.work
    juans = parse_juans(args.juans) if args.juans else None
    try:
        parallel_juans = parse_parallel_juans(args.parallel_juans)
    except ValueError as err:
        raise SystemExit(str(err))
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
    JOBS_DIR.mkdir(exist_ok=True)
    job = {
        "id": f"{datetime.now():%Y%m%d-%H%M%S}-{args.model}-{uuid.uuid4().hex[:4]}",
        "work": work, "juans": juans, "model": args.model,
        "state": "queued", "created": now_iso(), "updated": now_iso(),
        "pid": None, "resume_at": None, "error": None,
        "push": not args.no_push, "summary": args.summary,
        "parallel_juans": parallel_juans,
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
    enq.add_argument("--model", required=True, choices=QUEUES)
    enq.add_argument("--parallel-juans", type=int, default=DEFAULT_PARALLEL_JUANS,
                     help=f"number of juans to process concurrently (1-{MAX_PARALLEL_JUANS})")
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
