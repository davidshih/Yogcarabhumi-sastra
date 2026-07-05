#!/usr/bin/env python3
"""Subscription-CLI LLM backends with rate-limit auto-resume.

Billing guarantee (correctness requirement, not style):
- claude  -> Claude Code subscription: ANTHROPIC_* env vars are scrubbed so the
             CLI uses OAuth login, never metered API billing.
- codex   -> ChatGPT subscription: OPENAI_API_KEY scrubbed, ChatGPT login auth.
- glm     -> z.ai GLM coding plan: claude CLI pointed at the coding-plan
             endpoint via ANTHROPIC_BASE_URL/ANTHROPIC_AUTH_TOKEN.
"""

from __future__ import annotations

import datetime as dt
import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

MODELS = ("claude", "codex", "glm")
FAKE_MODELS = ("echo", "echo-limit-once")

ZAI_BASE_URL = "https://api.z.ai/api/anthropic"
ZAI_KEY_FILE = Path.home() / ".zai_key"
ZAI_MODEL = os.environ.get("ZAI_MODEL", "glm-5.2")

# Empty scratch cwd outside the repo so agentic CLIs don't read project files.
SCRATCH = Path(tempfile.gettempdir()) / "yogacara-llm-scratch"

TIMEOUT = 1800
TRANSIENT_RETRIES = 3
# ponytail: fixed backoff ladder when no reset time is parsable; minutes
BACKOFF_MINUTES = {"glm": [5, 5, 15, 30], "default": [15, 30, 60, 60]}
MAX_LIMIT_WAITS = 10

LIMIT_PATTERNS = re.compile(
    r"(?i)usage limit|limit reached|5-hour limit|hit your usage limit"
    r"|rate.?limit|too many requests|quota|exhausted|\b429\b"
)
RESET_CLOCK_RE = re.compile(r"(?i)resets?(?:\s+at)?\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)")
RESET_TS_RE = re.compile(r"limit reached\|(\d{10})")
RESET_IN_RE = re.compile(r"(?i)(?:try again|resets?)\s+in\s+(?:(\d+)\s*h(?:ours?)?)?\s*(?:(\d+)\s*m(?:in(?:utes?)?)?)?")


@dataclass
class LLMResult:
    ok: bool
    text: str = ""
    limit: bool = False
    resume_at: dt.datetime | None = None
    error: str = ""


class LLMError(Exception):
    pass


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
    return env


def parse_resume_at(text: str, now: dt.datetime | None = None) -> dt.datetime | None:
    now = now or dt.datetime.now().astimezone()
    match = RESET_TS_RE.search(text)
    if match:
        return dt.datetime.fromtimestamp(int(match.group(1))).astimezone()
    match = RESET_CLOCK_RE.search(text)
    if match:
        hour = int(match.group(1)) % 12 + (12 if match.group(3).lower() == "pm" else 0)
        candidate = now.replace(hour=hour, minute=int(match.group(2) or 0), second=0, microsecond=0)
        if candidate <= now:
            candidate += dt.timedelta(days=1)
        return candidate
    match = RESET_IN_RE.search(text)
    if match and (match.group(1) or match.group(2)):
        return now + dt.timedelta(hours=int(match.group(1) or 0), minutes=int(match.group(2) or 0))
    return None


def classify_output(exit_code: int, stdout: str, stderr: str) -> LLMResult:
    combined = f"{stdout}\n{stderr}"
    if LIMIT_PATTERNS.search(combined):
        return LLMResult(ok=False, limit=True, resume_at=parse_resume_at(combined),
                         error=combined.strip()[-500:])
    if exit_code != 0:
        return LLMResult(ok=False, error=combined.strip()[-500:] or f"exit {exit_code}")
    if not stdout.strip():
        return LLMResult(ok=False, error="empty output")
    return LLMResult(ok=True, text=stdout.strip())


def _run_fake(model: str, prompt: str) -> LLMResult:
    """Deterministic offline backends for testing the pipeline for free."""
    if model == "echo-limit-once":
        flag = SCRATCH / "echo-limit-flag"
        if not flag.exists():
            flag.write_text("1")
            return classify_output(1, "", "5-hour limit reached ∙ resets 3pm")
        model = "echo"
    if "<TSV>" in prompt:
        ids = re.findall(r"(T\d+n\d+[A-Za-z]?_p\d{4}[abc]\d{2})\t", prompt)
        if not ids:
            return LLMResult(ok=False, error="echo: no line ids in prompt")
        text = f"<TSV>\n全卷\t{ids[0]}-p{ids[-1].split('_p')[1]}\t\n</TSV>"
        return LLMResult(ok=True, text=text)
    source = re.search(r"【原文】\n(.*?)\n【原文結束】", prompt, re.DOTALL)
    if "審校" in prompt:
        return LLMResult(ok=True, text="OK")
    # echo "translation" = the source itself: passes term checks since patterns include the source term
    body = source.group(1).strip() if source else "（測試譯文）"
    return LLMResult(ok=True, text=f"<<<TRANSLATION\n{body}\n>>>\n<<<NOTE\n\n>>>")


def run_llm(model: str, prompt: str, timeout: int = TIMEOUT) -> LLMResult:
    SCRATCH.mkdir(parents=True, exist_ok=True)
    if model in FAKE_MODELS:
        return _run_fake(model, prompt)
    if model in ("claude", "glm"):
        cmd = ["claude", "-p"]
    elif model == "codex":
        out_file = SCRATCH / f"codex-out-{os.getpid()}-{time.monotonic_ns()}.txt"
        cmd = ["codex", "exec", "-s", "read-only", "--skip-git-repo-check",
               "--ephemeral", "--color", "never", "-o", str(out_file), "-"]
    else:
        raise LLMError(f"unknown model: {model}")
    try:
        proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                              timeout=timeout, cwd=SCRATCH, env=_env(model))
    except subprocess.TimeoutExpired:
        return LLMResult(ok=False, error=f"timeout after {timeout}s")
    stdout = proc.stdout
    if model == "codex":
        stdout = out_file.read_text(encoding="utf-8") if out_file.exists() else ""
        out_file.unlink(missing_ok=True)
        result = classify_output(proc.returncode, stdout, proc.stdout + proc.stderr)
        return result
    return classify_output(proc.returncode, stdout, proc.stderr)


def call_with_limit_retry(model: str, prompt: str, on_wait=None, log=print) -> str:
    """Blocking call that sleeps through rate limits and retries transient errors.

    on_wait(resume_at) is called before sleeping so callers can persist state.
    Raises LLMError when retries are exhausted.
    """
    transient = 0
    limit_waits = 0
    while True:
        result = run_llm(model, prompt)
        if result.ok:
            return result.text
        if result.limit:
            if limit_waits >= MAX_LIMIT_WAITS:
                raise LLMError(f"{model}: gave up after {limit_waits} limit waits")
            ladder = BACKOFF_MINUTES.get(model, BACKOFF_MINUTES["default"])
            fallback = dt.datetime.now().astimezone() + dt.timedelta(
                minutes=ladder[min(limit_waits, len(ladder) - 1)])
            resume_at = result.resume_at or fallback
            # never trust a resume time in the past or absurdly far out
            now = dt.datetime.now().astimezone()
            if resume_at <= now or resume_at > now + dt.timedelta(hours=12):
                resume_at = fallback
            limit_waits += 1
            if on_wait:
                on_wait(resume_at)
            log(f"[{model}] usage limit, sleeping until {resume_at:%H:%M}")
            time.sleep(max(60.0, (resume_at - now).total_seconds() + 60))
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
    assert parse_resume_at("resets 3pm") is not None
    assert parse_resume_at("try again in 2 hours 5 min") is not None
    c = classify_output(0, "You've hit your usage limit. try again in 1 hours", "")
    assert c.limit and c.resume_at is not None, c
    print("llm.py self-check OK")
