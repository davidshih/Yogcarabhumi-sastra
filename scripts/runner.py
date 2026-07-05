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
from contextlib import contextmanager
from datetime import datetime
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
GLOSSARY_PATH = ROOT / "translations" / "glossary" / "T1579-terms.json"
STATUS_JSON = ROOT / "docs" / "status.json"
REVIEW_PASSES = ("review_terms", "review_doctrine", "review_parallel")
POLL_SECONDS = 10

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
    model = job["model"]
    if model == "mix":
        return MIX_ROUTING.get(stage, "claude")
    if model == "dual":
        return DUAL_ROUTING.get(stage, "claude")
    if model == "dual-echo":
        return "echo"
    return model


def is_dual(job: dict) -> bool:
    return job["model"] in ("dual", "dual-echo")

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
        jobs.append(job)
    atomic_write(STATUS_JSON, json.dumps({"generated": now_iso(), "jobs": jobs}, ensure_ascii=False))


def log(job: dict, msg: str) -> None:
    print(f"{now_iso()} [{job['id']}] {msg}", flush=True)


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


def parse_llm_blocks(raw: str) -> tuple[str, str | None]:
    tr = OUT_TRANSLATION_RE.search(raw)
    if not tr or not tr.group(1).strip():
        raise ValueError(f"no <<<TRANSLATION block in output: {raw[:200]!r}")
    if "<<<" in tr.group(1):
        raise ValueError("nested markers in translation output")
    nt = OUT_NOTE_RE.search(raw)
    return tr.group(1).strip(), (nt.group(1).strip() if nt else None)


# ---------- pipeline steps ----------

def llm_call(job: dict, prompt: str, stage: str) -> str:
    def on_wait(resume_at):
        job["state"] = "waiting_limit"
        job["resume_at"] = resume_at.isoformat(timespec="seconds")
        save_job(job)
    text = llm.call_with_limit_retry(stage_model(job, stage), prompt, on_wait=on_wait,
                                     log=lambda m: log(job, m))
    if job["state"] == "waiting_limit":
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
        raw = llm_call(job, prompt + errors, "segment")
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
    glossary = load_glossary()
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
            extra=extra, **prompt_vars(job)), stage)
        translation, note = parse_llm_blocks(raw)
        splice(md_path, idx, translation, note if note else None)
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
        if entry.translation.strip():
            continue  # resumable; human edits win
        raw = llm_call(job, prompt_text(
            "merge", juan=juan, title=entry.title, note=entry.note or "（無）",
            source=entry.source, glossary=glossary_subset_json(glossary, entry.source),
            draft_a=draft_a[idx].translation or "（缺稿）",
            draft_b=draft_b[idx].translation or "（缺稿）", **prompt_vars(job)), "merge")
        translation, note = parse_llm_blocks(raw)
        splice(paths["md"], idx, translation, note)
        prog["section"] = idx + 1
        save_job(job)
        log(job, f"juan {juan}: merge {idx + 1}/{len(entries)} {entry.title}")
        entries = bth.parse_entries(paths["md"].read_text(encoding="utf-8"))


def review_pass(job: dict, juan: int, md_path: Path, pass_name: str, prog: dict) -> None:
    glossary = load_glossary()
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
        raw = llm_call(job, prompt_text(pass_name, **vars), pass_name)
        new_terms = NEW_TERMS_RE.search(raw)
        if pass_name == "review_terms" and new_terms:
            append_new_terms(new_terms.group(1), job)
            glossary = load_glossary()
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
    glossary = load_glossary()
    issues += validate_glossary(glossary) or term_issues(
        glossary, parse_term_entries(md_path.read_text(encoding="utf-8")))
    return issues


def build_html(job: dict, md_path: Path) -> None:
    text = md_path.read_text(encoding="utf-8")
    entries = bth.parse_entries(text)
    juan = bth.infer_juan(md_path)
    output = bth.translation_output_path(md_path, None)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(bth.render(entries, md_path, juan, bth.infer_title(text, juan)),
                      encoding="utf-8")
    bsh.main()  # refresh section pages + index (includes new translation link)
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
    work = job["work"]
    paths = juan_paths(work, juan)
    prog = job["progress"].setdefault(str(juan), {})
    if prog.get("step") == "done":
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

    prog["step"] = "checks"
    save_job(job)
    issues = run_checks(job, juan, paths["md"], data_path, start, end)
    if issues and not prog.get("repaired"):
        # one repair round: re-translate the sections named in term-check issues
        bad_titles = {i.split(":")[0].strip() for i in issues if ":" in i}
        log(job, f"juan {juan}: checks failed ({len(issues)}), repairing {len(bad_titles)} sections")
        prog["repaired"] = True
        save_job(job)
        translate_sections(job, juan, paths["md"], prog, only_titles=bad_titles,
                           extra="注意：前次譯文未通過術語檢查：" + "；".join(issues[:10]),
                           stage="repair")
        issues = run_checks(job, juan, paths["md"], data_path, start, end)
    if issues:
        raise RuntimeError(f"juan {juan} checks failed: " + "; ".join(issues[:10]))

    prog["step"] = "html"
    save_job(job)
    build_html(job, paths["md"])

    prog["step"] = "commit"
    save_job(job)
    git_commit_push(job, juan)

    prog["step"] = "done"
    save_job(job)


def run_job(job: dict) -> None:
    job["state"] = "running"
    job["pid"] = os.getpid()
    save_job(job)
    if job.get("summary"):
        log(job, "summary: not implemented")
    failed = []
    for juan in job["juans"]:
        try:
            run_juan(job, juan)
        except Exception as err:  # noqa: BLE001 — job must survive a bad juan
            failed.append(juan)
            job["progress"].setdefault(str(juan), {})["error"] = str(err)[:500]
            log(job, f"juan {juan} FAILED: {err}")
    job["state"] = "failed" if failed else "done"
    job["error"] = f"juans failed: {failed}" if failed else None
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
    JOBS_DIR.mkdir(exist_ok=True)
    job = {
        "id": f"{datetime.now():%Y%m%d-%H%M%S}-{args.model}-{uuid.uuid4().hex[:4]}",
        "work": work, "juans": juans, "model": args.model,
        "state": "queued", "created": now_iso(), "updated": now_iso(),
        "pid": None, "resume_at": None, "error": None,
        "push": not args.no_push, "summary": args.summary,
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
