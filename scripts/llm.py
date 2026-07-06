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


@dataclass
class LLMResult:
    ok: bool
    text: str = ""
    limit: bool = False
    resume_at: dt.datetime | None = None
    error: str = ""


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
    return env


def parse_resume_at(text: str, now: dt.datetime | None = None,
                    naive_tz: dt.timezone | None = None) -> dt.datetime | None:
    now = now or dt.datetime.now().astimezone()
    match = RESET_TS_RE.search(text)
    if match:
        return dt.datetime.fromtimestamp(int(match.group(1))).astimezone()
    match = RESET_ISO_RE.search(text)
    if match:
        try:
            parsed = dt.datetime.fromisoformat(match.group(1).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.astimezone()
            return parsed.astimezone()
        except ValueError:
            pass
    match = RESET_DATETIME_RE.search(text)  # before clock24: "…-06 06:10:27" would false-hit it
    if match:
        try:
            parsed = dt.datetime.fromisoformat(f"{match.group(1)}T{match.group(2)}")
            return parsed.replace(tzinfo=naive_tz or now.tzinfo).astimezone()
        except ValueError:
            pass
    match = RESET_CLOCK_RE.search(text)
    if match:
        meridiem = match.group(3).lower().replace(".", "")
        hour = int(match.group(1)) % 12 + (12 if meridiem.startswith("p") else 0)
        candidate = now.replace(hour=hour, minute=int(match.group(2) or 0), second=0, microsecond=0)
        if candidate <= now:
            candidate += dt.timedelta(days=1)
        return candidate
    match = RESET_CLOCK24_RE.search(text)
    if match:
        candidate = now.replace(hour=int(match.group(1)), minute=int(match.group(2)),
                                second=0, microsecond=0)
        if candidate <= now:
            candidate += dt.timedelta(days=1)
        return candidate
    match = RESET_IN_RE.search(text)
    if match and (match.group(1) or match.group(2)):
        return now + dt.timedelta(hours=int(match.group(1) or 0), minutes=int(match.group(2) or 0))
    return None


def classify_output(exit_code: int, stdout: str, stderr: str,
                    naive_tz: dt.timezone | None = None) -> LLMResult:
    combined = f"{stdout}\n{stderr}"
    if LIMIT_PATTERNS.search(combined):
        return LLMResult(ok=False, limit=True,
                         resume_at=parse_resume_at(combined, naive_tz=naive_tz),
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
    if "【譯稿A】" in prompt:  # merge prompts must produce a translation, not "OK"
        body = source.group(1).strip() if source else "（測試合稿）"
        return LLMResult(ok=True, text=f"<<<TRANSLATION\n{body}\n>>>\n<<<NOTE\n\n>>>")
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
    naive_tz = ZAI_TZ if model == "glm" else None
    stdout = proc.stdout
    if model == "codex":
        stdout = out_file.read_text(encoding="utf-8") if out_file.exists() else ""
        out_file.unlink(missing_ok=True)
        return classify_output(proc.returncode, stdout, proc.stdout + proc.stderr, naive_tz)
    return classify_output(proc.returncode, stdout, proc.stderr, naive_tz)


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
            fallback = dt.datetime.now().astimezone() + dt.timedelta(
                minutes=BACKOFF_MINUTES[min(limit_waits, len(BACKOFF_MINUTES) - 1)])
            resume_at = result.resume_at or fallback
            # never trust a resume time in the past or beyond one 5h window + slack
            now = dt.datetime.now().astimezone()
            if resume_at <= now or resume_at > now + dt.timedelta(hours=6):
                resume_at = fallback
            limit_waits += 1
            if on_wait:
                on_wait(resume_at)
            # retry 5 minutes AFTER the reset time — never race the window edge
            log(f"[{model}] usage limit, sleeping until {resume_at:%m-%d %H:%M} +5min")
            time.sleep(max(60.0, (resume_at - now).total_seconds() + 300))
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
        c = classify_output(0, msg, "")
        assert c.limit, f"not flagged as limit: {msg}"
        got = parse_resume_at(msg, now)
        assert got is not None, f"no reset parsed: {msg}"
        if hm:
            assert (got.hour, got.minute) == hm, f"{msg} -> {got}"
    # unparsable phrasing still flags limit, falls back to ladder
    c = classify_output(1, "", "You have been rate-limited, please slow down")
    assert c.limit and c.resume_at is None, c
    # z.ai bare datetime is Beijing time: 06:10:27 +08:00 == 22:10:27 UTC the day before
    zai_msg = "[1308][Usage limit reached for 5 hour. Your limit will reset at 2026-07-06 06:10:27][20260706x]"
    c = classify_output(1, "", zai_msg, naive_tz=ZAI_TZ)
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
