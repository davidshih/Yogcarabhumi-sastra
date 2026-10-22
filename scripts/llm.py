#!/usr/bin/env python3
"""Subscription-CLI LLM backends with rate-limit auto-resume.

Billing guarantee (correctness requirement, not style):
- claude  -> Claude Code subscription: ANTHROPIC_* env vars are scrubbed so the
             CLI uses OAuth login, never metered API billing. The app pins the
             Claude CLI model with --model so global user settings do not leak in.
- codex   -> ChatGPT subscription: OPENAI_API_KEY scrubbed, ChatGPT login auth.
             The runner pins the Codex CLI model so global user settings do not
             silently change translation jobs.
- glm     -> z.ai GLM coding plan: claude CLI pointed at the coding-plan
             endpoint via ANTHROPIC_BASE_URL/ANTHROPIC_AUTH_TOKEN.
- gpt-5.6-terra, gpt-5.6-sol -> the explicit Codex model ids of scripts/audited.py:
             the same ChatGPT login, and the reasoning effort is always explicit.
- claude-opus-5-5 -> the explicit Claude model id of `audited.py audit --model opus`: the
             same Claude Code subscription as `claude`, the effort always explicit, user
             customizations off (--safe-mode), no tools, and a JSON envelope with token usage.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MODELS = ("claude", "codex", "glm")  # production queues; scripts/runner.py derives QUEUES from it
AUDIT_MODELS = ("gpt-5.6-terra", "gpt-5.6-sol")  # explicit Codex model ids that scripts/audited.py uses
CLAUDE_AUDIT_MODELS = ("claude-opus-5-5",)  # explicit Claude model ids that scripts/audited.py uses
FAKE_MODELS = ("echo", "echo-limit-once")
# Offline backends of the legacy audit only. They are not in FAKE_MODELS, so they are never a production queue.
AUDIT_FAKE_MODELS = ("echo-finding", "echo-finding-stubborn", "echo-finding-merge")
AUDIT_FAKE_FIX_MARK = "【已修】"

ZAI_BASE_URL = "https://api.z.ai/api/anthropic"
ZAI_KEY_FILE = Path.home() / ".zai_key"
ZAI_MODEL = os.environ.get("ZAI_MODEL", "glm-5.2")
CLAUDE_MODEL = os.environ.get("YOGACARA_CLAUDE_MODEL", "sonnet")
CODEX_MODEL = os.environ.get("YOGACARA_CODEX_MODEL", "gpt-5.6-luna")
CODEX_REASONING_EFFORTS = ("low", "medium", "high", "xhigh", "max")
CODEX_REASONING_EFFORT = os.environ.get("YOGACARA_CODEX_REASONING_EFFORT", "high")
# z.ai error payloads carry naive datetimes in Beijing time:
# "[1308][Usage limit reached for 5 hour. Your limit will reset at 2026-07-06 06:10:27][...]"
ZAI_TZ = dt.timezone(dt.timedelta(hours=8))

# Empty scratch cwd outside the repo so agentic CLIs don't read project files.
SCRATCH = Path(tempfile.gettempdir()) / "yogacara-llm-scratch"

TIMEOUT = 1800
AVAILABILITY_TIMEOUT = 120
TRANSIENT_RETRIES = 3
# All three backends run 5-hour windows. When no reset time is parsable, this
# ladder (with MAX_LIMIT_WAITS=10) spans a bit over 5h, so an unknown-format
# limit message can never strand a job short of the next window.
BACKOFF_MINUTES = [15, 30, 45, 60]
MAX_LIMIT_WAITS = 10
DEFAULT_TZ_NAME = os.environ.get("YOGACARA_TIMEZONE", "America/New_York")
try:
    DEFAULT_TZ = ZoneInfo(DEFAULT_TZ_NAME)
except ZoneInfoNotFoundError as error:
    raise RuntimeError(f"unknown YOGACARA_TIMEZONE: {DEFAULT_TZ_NAME}") from error

LIMIT_PATTERNS = re.compile(
    r"(?i)usage limit|session limit|limit reached|5-hour limit|hit your (?:usage|session|weekly) limit"
    r"|rate.?limit|too many requests|quota|exhausted|\b429\b|overloaded"
)
# "…usage limit reached|1751721600" (claude CLI unix-ts form)
RESET_TS_RE = re.compile(r"\|(\d{10})\b")
# "resets 3pm" / "will reset at 8pm" / "Try again at 3:45 PM." / "available at 9 am"
RESET_CLOCK_RE = re.compile(
    r"(?i)(?:reset(?:s)?|try again|available|retry)[^\n.]{0,24}?"
    r"\b(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)"
)
# "resets 14:30" — 24h clock needs the colon so bare numbers don't match
RESET_CLOCK24_RE = re.compile(
    r"(?i)(?:reset(?:s)?|try again|available|retry)[^\n.]{0,24}?\b([01]?\d|2[0-3]):([0-5]\d)\b"
)
# "try again in 2 hours 15 minutes" / "resets in 45 min"
RESET_IN_RE = re.compile(
    r"(?i)(?:try again|resets?|retry)\s+in\s+(?:(\d+)\s*h(?:ours?|rs?)?)?\s*"
    r"(?:(\d+)\s*m(?:in(?:utes?)?)?)?"
)
# "resets_at":"2026-07-05T21:00:00Z" (json error payloads)
RESET_ISO_RE = re.compile(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:?\d{2})?)")
# "will reset at 2026-07-06 06:10:27" (z.ai bare datetime, naive — tz from caller)
RESET_DATETIME_RE = re.compile(r"(?i)reset(?:s)?\s+at\s+(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})")
# "try again at Jul 21st, 2026 5:43 AM" (Codex subscription CLI)
RESET_HUMAN_DATETIME_RE = re.compile(
    r"(?i)(?:reset(?:s)?|try again|available|retry)[^\n.]{0,24}?\bat\s+"
    r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+"
    r"(\d{1,2})(?:st|nd|rd|th)?,\s+(\d{4})\s+"
    r"(\d{1,2}):(\d{2})\s*(a\.?m\.?|p\.?m\.?)"
    r"(?:\s*\(([^)]+)\))?"
)
ERROR_LINE_RE = re.compile(r"(?i)^\s*(?:error|fatal)\s*:")
PROMPT_ECHO_PREFIX_RE = re.compile(
    r"(?i)^\s*(?:(?:user|prompt|input|stdin|\[user\])\s*:?\s*|>\s*)"
)
MIN_FRAMED_PROMPT_CHARS = 16
MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
# The audited pipeline frames its input and its schema this way (scripts/audited.py, prompts/audited/).
STRUCTURED_PROMPT_RE = re.compile(r"輸入 JSON：\n(.*?)\n\n輸出 JSON Schema：", re.DOTALL)


@dataclass
class LLMResult:
    ok: bool
    text: str = ""
    limit: bool = False
    resume_at: dt.datetime | None = None
    provider_resume_at: dt.datetime | None = None
    effective_resume_at: dt.datetime | None = None
    resume_decision: str | None = None
    error: str = ""
    sent_at: str | None = None
    first_response_at: str | None = None
    received_at: str | None = None
    duration_ms: int | None = None
    model: str | None = None
    effort: str | None = None
    request_id: str | None = None
    usage: dict | None = None
    exit_code: int | None = None
    metadata_availability: dict[str, str] = field(default_factory=dict)


class LLMError(Exception):
    pass


def availability_probe(model: str, timeout: int = AVAILABILITY_TIMEOUT) -> LLMResult:
    """Run a tiny paid-session probe and return the classified backend state."""
    try:
        return run_llm(model, "Reply with exactly OK.", timeout=timeout)
    except (LLMError, FileNotFoundError) as err:
        return LLMResult(ok=False, error=str(err))


def _zai_key() -> str:
    key = os.environ.get("ZAI_API_KEY", "")
    if not key and ZAI_KEY_FILE.exists():
        key = ZAI_KEY_FILE.read_text(encoding="utf-8").strip()
    if not key:
        raise LLMError("no z.ai key: set ZAI_API_KEY or write ~/.zai_key")
    return key


def _env(model: str) -> dict[str, str]:
    env = dict(os.environ)
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN",
                "ANTHROPIC_MODEL", "OPENAI_API_KEY"):
        env.pop(var, None)
    if model == "glm":
        env["ANTHROPIC_BASE_URL"] = ZAI_BASE_URL
        env["ANTHROPIC_AUTH_TOKEN"] = _zai_key()
        env["ANTHROPIC_MODEL"] = ZAI_MODEL
    if model in CLAUDE_AUDIT_MODELS:
        env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = "128000"  # room for xhigh thinking plus the JSON answer
    return env


def _now() -> dt.datetime:
    return dt.datetime.now(DEFAULT_TZ)


def _target_tz(now: dt.datetime, naive_tz: dt.tzinfo | None) -> dt.tzinfo:
    return naive_tz or now.tzinfo or DEFAULT_TZ


def _human_timezone(name: str | None, fallback: dt.tzinfo) -> dt.tzinfo:
    if not name:
        return fallback
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return fallback


def _line_candidates(text: str, now: dt.datetime,
                     naive_tz: dt.tzinfo | None) -> list[dt.datetime]:
    target_tz = _target_tz(now, naive_tz)
    local_now = now.astimezone(target_tz)
    candidates: list[dt.datetime] = []
    protected_spans: list[tuple[int, int]] = []

    def overlaps_protected(match: re.Match) -> bool:
        start, end = match.span()
        return any(start < protected_end and protected_start < end
                   for protected_start, protected_end in protected_spans)

    human_matches = list(RESET_HUMAN_DATETIME_RE.finditer(text))
    for match in human_matches:
        protected_spans.append(match.span())
        meridiem = match.group(6).lower().replace(".", "")
        hour = int(match.group(4)) % 12 + (12 if meridiem.startswith("p") else 0)
        zone = _human_timezone(match.group(7), target_tz)
        try:
            candidates.append(dt.datetime(
                int(match.group(3)), MONTHS[match.group(1)[:3].lower()],
                int(match.group(2)), hour, int(match.group(5)), tzinfo=zone,
            ))
        except ValueError:
            pass

    for match in RESET_TS_RE.finditer(text):
        try:
            candidates.append(dt.datetime.fromtimestamp(int(match.group(1)), tz=target_tz))
        except (OverflowError, OSError, ValueError):
            pass
    for match in RESET_ISO_RE.finditer(text):
        protected_spans.append(match.span())
        try:
            parsed = dt.datetime.fromisoformat(match.group(1).replace("Z", "+00:00"))
            candidates.append(parsed.replace(tzinfo=target_tz) if parsed.tzinfo is None
                              else parsed.astimezone(target_tz))
        except ValueError:
            pass
    for match in RESET_DATETIME_RE.finditer(text):
        protected_spans.append(match.span())
        try:
            parsed = dt.datetime.fromisoformat(f"{match.group(1)}T{match.group(2)}")
            candidates.append(parsed.replace(tzinfo=target_tz))
        except ValueError:
            pass

    # Explicit datetimes and 12-hour clocks also contain text that can look
    # like a bare 24-hour clock. Suppress only overlapping, less-specific hits.
    for match in RESET_CLOCK_RE.finditer(text):
        if overlaps_protected(match):
            continue
        protected_spans.append(match.span())
        meridiem = match.group(3).lower().replace(".", "")
        hour = int(match.group(1)) % 12 + (12 if meridiem.startswith("p") else 0)
        candidate = local_now.replace(
            hour=hour, minute=int(match.group(2) or 0), second=0, microsecond=0,
        )
        if candidate <= local_now:
            candidate += dt.timedelta(days=1)
        candidates.append(candidate)
    for match in RESET_CLOCK24_RE.finditer(text):
        if overlaps_protected(match):
            continue
        candidate = local_now.replace(
            hour=int(match.group(1)), minute=int(match.group(2)),
            second=0, microsecond=0,
        )
        if candidate <= local_now:
            candidate += dt.timedelta(days=1)
        candidates.append(candidate)
    for match in RESET_IN_RE.finditer(text):
        if match.group(1) or match.group(2):
            candidates.append(local_now + dt.timedelta(
                hours=int(match.group(1) or 0), minutes=int(match.group(2) or 0),
            ))
    return candidates


def _latest_candidate(candidates: list[dt.datetime], now: dt.datetime) -> dt.datetime | None:
    if not candidates:
        return None
    future = [candidate for candidate in candidates if candidate > now]
    return max(future or candidates)


def parse_resume_at(text: str, now: dt.datetime | None = None,
                    naive_tz: dt.tzinfo | None = None) -> dt.datetime | None:
    now = now or _now()
    error_lines = [line for line in text.splitlines() if ERROR_LINE_RE.match(line)]
    for line in reversed(error_lines):
        candidate = _latest_candidate(_line_candidates(line, now, naive_tz), now)
        if candidate is not None:
            return candidate
    return _latest_candidate(_line_candidates(text, now, naive_tz), now)


def _dedupe_lines(lines: list[str]) -> list[str]:
    seen: set[str] = set()
    unique = []
    for line in lines:
        normalized = line.strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            unique.append(normalized)
    return unique


def _bounded_contains(text: str, fragment: str) -> bool:
    start = text.find(fragment)
    while start >= 0:
        end = start + len(fragment)
        left_boundary = start == 0 or not (text[start - 1].isalnum() or text[start - 1] == "_")
        right_boundary = end == len(text) or not (text[end].isalnum() or text[end] == "_")
        if left_boundary and right_boundary:
            return True
        start = text.find(fragment, start + 1)
    return False


def _is_prompt_echo(line: str, prompt_lines: list[str]) -> bool:
    raw = line.strip()
    candidate = PROMPT_ECHO_PREFIX_RE.sub("", raw).strip()
    if not candidate:
        return True
    for prompt_line in prompt_lines:
        if len(prompt_line) >= MIN_FRAMED_PROMPT_CHARS and _bounded_contains(raw, prompt_line):
            return True
    if ERROR_LINE_RE.match(raw):
        return False
    for prompt_line in prompt_lines:
        if len(candidate) >= MIN_FRAMED_PROMPT_CHARS and _bounded_contains(prompt_line, candidate):
            return True
    return False


def authoritative_diagnostics(_model_output: str, diagnostics: str,
                              echoed_prompt: str | None = None) -> str:
    prompt_lines = _dedupe_lines(echoed_prompt.splitlines()) if echoed_prompt else []
    lines = [
        line.strip() for line in diagnostics.splitlines()
        if not prompt_lines or not _is_prompt_echo(line, prompt_lines)
    ]
    provider_errors = [line for line in lines if ERROR_LINE_RE.match(line)]
    if provider_errors:
        return provider_errors[-1]
    limit_lines = [line for line in lines if LIMIT_PATTERNS.search(line)]
    if limit_lines:
        return limit_lines[-1]
    return "\n".join(_dedupe_lines(lines))[-500:]


def classify_output(exit_code: int, model_output: str, diagnostics: str,
                    naive_tz: dt.tzinfo | None = None,
                    echoed_prompt: str | None = None) -> LLMResult:
    """Classify a call whose answer and diagnostics come on separate channels (Codex).

    Only the diagnostics can mean a usage limit: an answer that talks about a quota is still an answer.
    """
    safe_diagnostics = authoritative_diagnostics(model_output, diagnostics, echoed_prompt)
    if LIMIT_PATTERNS.search(safe_diagnostics):
        provider_resume_at = parse_resume_at(safe_diagnostics, naive_tz=naive_tz)
        return LLMResult(ok=False, limit=True,
                         resume_at=provider_resume_at,
                         provider_resume_at=provider_resume_at,
                         error=safe_diagnostics)
    if exit_code != 0:
        return LLMResult(ok=False, error=safe_diagnostics or f"exit {exit_code}")
    if not model_output.strip():
        return LLMResult(ok=False, error="empty output")
    return LLMResult(ok=True, text=model_output.strip())


def classify_combined_output(exit_code: int, stdout: str, stderr: str,
                             naive_tz: dt.tzinfo | None = None) -> LLMResult:
    """Classify a call whose answer and limit message share stdout (the claude CLI, also for glm)."""
    combined = f"{stdout}\n{stderr}"
    if LIMIT_PATTERNS.search(combined):
        provider_resume_at = parse_resume_at(combined, naive_tz=naive_tz)
        return LLMResult(ok=False, limit=True,
                         resume_at=provider_resume_at,
                         provider_resume_at=provider_resume_at,
                         error=combined.strip()[-500:])
    if exit_code != 0:
        return LLMResult(ok=False, error=combined.strip()[-500:] or f"exit {exit_code}")
    if not stdout.strip():
        return LLMResult(ok=False, error="empty output")
    return LLMResult(ok=True, text=stdout.strip())


def _claude_usage(envelope: dict) -> dict | None:
    """The token usage of a `claude -p --output-format json` answer, in the keys that audited.py records."""
    usage = envelope.get("usage")
    if not isinstance(usage, dict):
        return None
    fresh = usage.get("input_tokens") or 0
    read = usage.get("cache_read_input_tokens") or 0
    written = usage.get("cache_creation_input_tokens") or 0
    output = usage.get("output_tokens") or 0
    details = usage.get("output_tokens_details") or {}
    return {
        "input_tokens": fresh + read + written,
        "cached_input_tokens": read,
        "cache_write_input_tokens": written,
        "output_tokens": output,
        "reasoning_tokens": details.get("thinking_tokens"),
        "total_tokens": fresh + read + written + output,
        "cost_usd": envelope.get("total_cost_usd"),
    }


def classify_claude_json(exit_code: int, stdout: str, stderr: str,
                         naive_tz: dt.tzinfo | None = None) -> LLMResult:
    """Classify a `claude -p --output-format json` call. The envelope keeps the answer apart from errors.

    Only an error envelope can mean a usage limit: an answer that talks about a quota is still an answer.
    Output that is not an envelope is classified as plain claude CLI output.
    """
    try:
        envelope = json.loads(stdout)
    except ValueError:
        envelope = None
    if not isinstance(envelope, dict) or envelope.get("type") != "result":
        return classify_combined_output(exit_code, stdout, stderr, naive_tz)
    answer = envelope.get("result") if isinstance(envelope.get("result"), str) else ""
    if envelope.get("is_error") or exit_code != 0:
        return classify_combined_output(exit_code or 1, answer, stderr, naive_tz)
    if not answer.strip():
        return LLMResult(ok=False, error="empty output", usage=_claude_usage(envelope))
    return LLMResult(ok=True, text=answer.strip(), usage=_claude_usage(envelope))


def _first_quote(text: str) -> str:
    """The first 4 characters of the first non-blank line: a literal quote for a fake finding."""
    return next((line.strip() for line in text.splitlines() if line.strip()), "")[:4]


def _run_fake_structured(model: str, payload: dict) -> LLMResult:
    """Answers of the fake backends to a JSON-contract prompt of the audited pipeline."""
    stage = payload.get("stage")
    if stage == "segment":
        lines = payload.get("lines", [])
        if not lines:
            return LLMResult(ok=False, error="echo: no source lines")
        return LLMResult(ok=True, text=json.dumps({
            "schema_version": "1.0", "stage": "segment", "work": payload["work"],
            "juan": payload["juan"], "source_hash": payload["source_hash"],
            "segments": [{
                "title": "全卷", "start_line_id": lines[0]["line_id"],
                "end_line_id": lines[-1]["line_id"], "note": "",
            }],
        }, ensure_ascii=False))
    if stage in {"review_terms", "review_doctrine", "review_parallel"}:
        clause = payload.get("clause") or {}
        if (model in AUDIT_FAKE_MODELS and stage == "review_doctrine"
                and str(payload.get("unit_id", "")).endswith("-002")
                and AUDIT_FAKE_FIX_MARK not in clause.get("translation", "")):
            # The legacy-audit fakes flag the second section of a juan until its text carries the mark.
            return LLMResult(ok=True, text=json.dumps({
                "schema_version": "1.0", "stage": stage, "unit_id": payload["unit_id"],
                "source_hash": payload["source_hash"], "verdict": "changes_required",
                "findings": [{
                    "clause_id": clause["clause_id"], "severity": "high", "category": "test",
                    "claim": "echo-finding",
                    "evidence": {
                        "source_quote": _first_quote(clause.get("source", "")),
                        "translation_quote": _first_quote(clause.get("translation", "")),
                        "reference_ids": [],
                    },
                    "required_change": f"append {AUDIT_FAKE_FIX_MARK}",
                }],
                "proposed_terms": [],
            }, ensure_ascii=False))
        verdict = "not_checked" if stage == "review_parallel" and not payload.get("review_evidence") else "pass"
        return LLMResult(ok=True, text=json.dumps({
            "schema_version": "1.0", "stage": stage, "unit_id": payload["unit_id"],
            "source_hash": payload["source_hash"], "verdict": verdict,
            "findings": [], "proposed_terms": [],
        }, ensure_ascii=False))
    clause = (payload.get("clauses") or [payload.get("clause")])[0]
    source = clause.get("source", "")
    body = clause.get("translation") or source or "（測試譯文）"
    if stage == "fix" and model in AUDIT_FAKE_MODELS:
        translation = clause["translation"]
        body = {
            "echo-finding": translation + AUDIT_FAKE_FIX_MARK,
            "echo-finding-stubborn": translation,  # unchanged, so the finding stays
            "echo-finding-merge": translation.replace("\n\n", "\n") + AUDIT_FAKE_FIX_MARK,  # one paragraph
        }[model]
    return LLMResult(ok=True, text=json.dumps({
        "schema_version": "1.0", "stage": stage, "unit_id": payload["unit_id"],
        "source_hash": payload["source_hash"],
        "clauses": [{
            "clause_id": clause["clause_id"], "literal": source or body,
            "vernacular": body, "additions": [], "negation_scope": [],
            "references": [], "speakers": None, "term_occurrences": [],
            "variants": [], "notes": [],
        }],
    }, ensure_ascii=False))


def _run_fake(model: str, prompt: str) -> LLMResult:
    """Deterministic offline backends for testing the pipeline for free."""
    if model == "echo-limit-once":
        flag = SCRATCH / "echo-limit-flag"
        if not flag.exists():
            flag.write_text("1")
            return classify_combined_output(1, "", "5-hour limit reached ∙ resets 3pm")
        model = "echo"
    structured = STRUCTURED_PROMPT_RE.search(prompt)
    if structured:
        try:
            payload = json.loads(structured.group(1))
        except ValueError as error:
            return LLMResult(ok=False, error=f"echo: invalid input JSON: {error}")
        return _run_fake_structured(model, payload)
    if "<TSV>" in prompt:
        ids = re.findall(r"(T\d+n\d+[A-Za-z]?_p\d{4}[abc]\d{2})\t", prompt)
        if not ids:
            return LLMResult(ok=False, error="echo: no line ids in prompt")
        text = f"<TSV>\n全卷\t{ids[0]}-p{ids[-1].split('_p')[1]}\t\n</TSV>"
        return LLMResult(ok=True, text=text)
    if "<<<SOURCE_SECTION " in prompt:
        sections = re.findall(
            r"<<<SOURCE_SECTION (\d+)>>>\n.*?Source:\n(.*?)\n<<<END_SOURCE_SECTION \1>>>",
            prompt,
            re.DOTALL,
        )
        if not sections:
            return LLMResult(ok=False, error="echo: no source sections in batch prompt")
        text = "\n\n".join(
            f"<<<SECTION {number}>>>\n<<<TRANSLATION\n{source.strip()}\n>>>\n"
            f"<<<NOTE\n\n>>>\n<<<END_SECTION {number}>>>"
            for number, source in sections
        )
        return LLMResult(ok=True, text=text)
    source = re.search(r"【原文】\n(.*?)\n【原文結束】", prompt, re.DOTALL)
    if "【譯稿A】" in prompt:  # merge prompts must produce a translation, not "OK"
        body = source.group(1).strip() if source else "（測試合稿）"
        return LLMResult(ok=True, text=f"<<<TRANSLATION\n{body}\n>>>\n<<<NOTE\n\n>>>")
    if "審校" in prompt:
        return LLMResult(ok=True, text="OK")
    # echo "translation" = the source itself: passes term checks since patterns include the source term
    body = source.group(1).strip() if source else "（測試譯文）"
    return LLMResult(ok=True, text=f"<<<TRANSLATION\n{body}\n>>>\n<<<NOTE\n\n>>>")


def _timestamp() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="milliseconds")


def _with_call_metadata(result: LLMResult, model: str, effort: str | None,
                        sent_at: str, started: float, exit_code: int | None) -> LLMResult:
    result.sent_at = sent_at
    result.received_at = _timestamp()
    result.duration_ms = round((time.monotonic() - started) * 1000)
    result.model = model
    result.effort = effort
    result.exit_code = exit_code
    result.metadata_availability = {
        "first_response_at": "subscription CLI returns buffered output only",
        "request_id": "provider did not expose a request ID",
        "usage": "provider did not expose token usage",
        "events": "provider did not expose a safe event stream",
    }
    if result.usage is not None:
        del result.metadata_availability["usage"]
    if effort is None:
        result.metadata_availability["effort"] = "reasoning effort was not configured"
    return result


def run_llm(model: str, prompt: str, timeout: int = TIMEOUT,
            codex_model: str | None = None,
            codex_reasoning_effort: str | None = None,
            effort: str | None = None) -> LLMResult:
    sent_at = _timestamp()
    started = time.monotonic()
    SCRATCH.mkdir(parents=True, exist_ok=True)
    if model in FAKE_MODELS or model in AUDIT_FAKE_MODELS:
        result = _run_fake(model, prompt)
        return _with_call_metadata(result, model, effort, sent_at, started, 0 if result.ok else 1)
    out_file = None  # the Codex CLI writes the answer here; stdout and stderr are diagnostics only
    if model == "claude":
        cmd = ["claude", "--model", CLAUDE_MODEL, "-p"]
    elif model == "glm":
        cmd = ["claude", "-p"]
    elif model == "codex":
        codex_model = codex_model or CODEX_MODEL
        effort = codex_reasoning_effort or effort or CODEX_REASONING_EFFORT
        if effort not in CODEX_REASONING_EFFORTS:
            raise LLMError(f"invalid Codex reasoning effort: {effort}")
        out_file = SCRATCH / f"codex-out-{os.getpid()}-{time.monotonic_ns()}.txt"
        cmd = ["codex", "exec", "--model", codex_model, "-c", f'model_reasoning_effort="{effort}"',
               "-s", "read-only", "--skip-git-repo-check",
               "--ephemeral", "--color", "never", "-o", str(out_file), "-"]
    elif model in AUDIT_MODELS:
        if not effort:
            raise LLMError(f"{model}: reasoning effort must be explicit")
        out_file = SCRATCH / f"codex-out-{os.getpid()}-{time.monotonic_ns()}.txt"
        cmd = ["codex", "exec", "-s", "read-only", "--skip-git-repo-check",
               "--ephemeral", "--color", "never", "--model", model,
               "-c", f'model_reasoning_effort="{effort}"', "-o", str(out_file), "-"]
    elif model in CLAUDE_AUDIT_MODELS:
        if not effort:
            raise LLMError(f"{model}: reasoning effort must be explicit")
        cmd = ["claude", "-p", "--safe-mode", "--model", model, "--effort", effort, "--tools", "",
               "--strict-mcp-config", "--mcp-config", '{"mcpServers": {}}', "--no-session-persistence",
               "--output-format", "json"]
    else:
        raise LLMError(f"unknown model: {model}")
    try:
        proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                              timeout=timeout, cwd=SCRATCH, env=_env(model))
    except subprocess.TimeoutExpired:
        return _with_call_metadata(
            LLMResult(ok=False, error=f"timeout after {timeout}s"),
            model, effort, sent_at, started, None,
        )
    if out_file is not None:
        answer = out_file.read_text(encoding="utf-8") if out_file.exists() else ""
        out_file.unlink(missing_ok=True)
        result = classify_output(
            proc.returncode, answer, f"{proc.stdout}\n{proc.stderr}", None,
            echoed_prompt=prompt,
        )
    elif model in CLAUDE_AUDIT_MODELS:
        result = classify_claude_json(proc.returncode, proc.stdout, proc.stderr)
    else:
        # The claude CLI prints the answer and its limit message on stdout: they cannot be told apart.
        result = classify_combined_output(
            proc.returncode, proc.stdout, proc.stderr, ZAI_TZ if model == "glm" else None,
        )
    return _with_call_metadata(result, model, effort, sent_at, started, proc.returncode)


def limit_wait_decision(provider_resume_at: dt.datetime | None, limit_waits: int,
                        now: dt.datetime | None = None) -> tuple[dt.datetime, str]:
    now = now or _now()
    fallback = now + dt.timedelta(
        minutes=BACKOFF_MINUTES[min(limit_waits, len(BACKOFF_MINUTES) - 1)],
    )
    if provider_resume_at is None:
        return fallback, "fallback_unparsable"
    # never trust a resume time in the past or beyond one 5h window + slack
    if provider_resume_at <= now or provider_resume_at > now + dt.timedelta(hours=6):
        return fallback, "fallback_out_of_policy"
    return provider_resume_at, "provider"


def call_with_limit_retry(model: str, prompt: str, on_wait=None, log=print, run_fn=None,
                          effort: str | None = None, on_attempt=None) -> str:
    """Blocking call that sleeps through rate limits and retries transient errors.

    on_wait(resume_at) is called before sleeping so callers can persist state.
    run_fn(model, prompt) replaces the backend call (scripts/runner.py passes its guarded call);
    without it the call is run_llm(model, prompt, effort=effort).
    on_attempt(result, attempt) is called after every backend call (scripts/audited.py records it).
    Raises LLMError when retries are exhausted.
    """
    transient = 0
    limit_waits = 0
    while True:
        result = run_fn(model, prompt) if run_fn else run_llm(model, prompt, effort=effort)
        wait_now = None
        if result.limit:
            wait_now = _now()
            provider_resume_at = result.provider_resume_at or result.resume_at
            effective_resume_at, decision = limit_wait_decision(
                provider_resume_at, limit_waits, wait_now,
            )
            result.provider_resume_at = provider_resume_at
            result.effective_resume_at = effective_resume_at
            result.resume_decision = decision
        if on_attempt:
            on_attempt(result, limit_waits + transient + 1)
        if result.ok:
            return result.text
        if result.limit:
            if limit_waits >= MAX_LIMIT_WAITS:
                raise LLMError(f"{model}: gave up after {limit_waits} limit waits")
            resume_at = result.effective_resume_at
            if resume_at is None or wait_now is None:
                raise RuntimeError("rate-limit wait decision was not computed")
            limit_waits += 1
            if on_wait:
                on_wait(resume_at)
            # retry 5 minutes AFTER the reset time — never race the window edge
            if result.resume_decision == "provider":
                log(
                    f"[{model}] usage limit, provider reset at "
                    f"{resume_at.isoformat()}; sleeping until reset +5min"
                )
            else:
                provider_text = (
                    result.provider_resume_at.isoformat()
                    if result.provider_resume_at is not None else "unparsable"
                )
                log(
                    f"[{model}] usage limit, provider reset {provider_text}; "
                    f"next quota probe at {resume_at.isoformat()} +5min "
                    f"({result.resume_decision})"
                )
            time.sleep(max(60.0, (resume_at - wait_now).total_seconds() + 300))
            continue
        transient += 1
        if transient > TRANSIENT_RETRIES:
            raise LLMError(f"{model}: {result.error}")
        log(f"[{model}] transient error ({transient}/{TRANSIENT_RETRIES}): {result.error[:200]}")
        time.sleep(30 * transient)


if __name__ == "__main__":
    # self-check: fake backends + limit classification round-trip
    SCRATCH.mkdir(parents=True, exist_ok=True)
    (SCRATCH / "echo-limit-flag").unlink(missing_ok=True)
    r = run_llm("echo", "【原文】\n作意云何\n【原文結束】")
    assert r.ok and "作意云何" in r.text and "<<<TRANSLATION" in r.text, r
    r = run_llm("echo-limit-once", "x")
    assert r.limit and r.resume_at is not None, r
    r = run_llm("echo-limit-once", "審校【原文】\nx\n【原文結束】")
    assert r.ok and r.text == "OK", r
    r = run_llm("echo", "<TSV>\nT30n1579_p0328c02\tabc\nT30n1579_p0335a10\tdef\n")
    assert r.ok and "T30n1579_p0328c02-p0335a10" in r.text, r
    # limit-message formats seen from the three CLIs — every one must yield a reset time
    now = dt.datetime(2026, 7, 5, 12, 0, tzinfo=dt.timezone.utc).astimezone()
    cases = {
        # claude CLI, unix-ts form
        "Claude AI usage limit reached|1799999999": None,
        # claude CLI, human form
        "5-hour limit reached ∙ resets 3pm": (15, 0),
        "You've reached your usage limit. Your limit will reset at 8pm (America/New_York).": (20, 0),
        # claude CLI session-limit wording (seen live 2026-07-05; killed juans 4-5)
        "You've hit your session limit · resets 9pm (America/New_York)": (21, 0),
        # codex CLI
        "You've hit your usage limit. Try again at 3:45 PM.": (15, 45),
        "Rate limit exceeded. Try again in 2 hours 15 minutes.": None,
        # 24h clock + json payload variants (glm / API errors)
        "usage limit reached, resets 14:30": (14, 30),
        '429 {"error":{"message":"quota exhausted","resets_at":"2026-07-05T21:00:00Z"}}': None,
    }
    for msg, hm in cases.items():
        # claude and glm: the message is on stdout, next to the answer
        c = classify_combined_output(0, msg, "")
        assert c.limit, f"not flagged as limit: {msg}"
        # codex: the message is a diagnostic; the answer file is empty
        c = classify_output(1, "", msg)
        assert c.limit, f"not flagged as limit: {msg}"
        got = parse_resume_at(msg, now)
        assert got is not None, f"no reset parsed: {msg}"
        if hm:
            assert (got.hour, got.minute) == hm, f"{msg} -> {got}"
    # a Codex answer that talks about a quota is an answer, not a limit
    c = classify_output(0, "譯文談到 quota exhausted 與 HTTP 429", "")
    assert c.ok and not c.limit, c
    # unparsable phrasing still flags limit, falls back to ladder
    for classify in (classify_combined_output, classify_output):
        c = classify(1, "", "You have been rate-limited, please slow down")
        assert c.limit and c.resume_at is None, c
    # z.ai bare datetime is Beijing time: 06:10:27 +08:00 == 22:10:27 UTC the day before
    zai_msg = "[1308][Usage limit reached for 5 hour. Your limit will reset at 2026-07-06 06:10:27][20260706x]"
    for classify in (classify_combined_output, classify_output):
        c = classify(1, "", zai_msg, naive_tz=ZAI_TZ)
        assert c.limit and c.resume_at is not None, c
        assert c.resume_at.astimezone(dt.timezone.utc) == dt.datetime(
            2026, 7, 5, 22, 10, 27, tzinfo=dt.timezone.utc), c.resume_at
    # same message without tz hint parses as local, never as 06:10-as-clock24
    local = parse_resume_at(zai_msg, now)
    assert local is not None and local.second == 27, local
    # past clock time rolls to tomorrow
    late = dt.datetime(2026, 7, 5, 23, 0, tzinfo=dt.timezone.utc).astimezone()
    rolled = parse_resume_at("resets 3pm", late)
    assert rolled > late, rolled
    print("llm.py self-check OK")
