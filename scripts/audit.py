#!/usr/bin/env python3
"""Append-only audit events and content-addressed pipeline artifacts."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import uuid
from datetime import datetime
from pathlib import Path


SCHEMA_VERSION = "1.0"
SECRET_RE = re.compile(
    r"(?ix)"
    r"(?P<prefix>(?P<key_quote>[\"']?)(?:authorization|api[_-]?key|auth[_-]?token|password)"
    r"(?P=key_quote)\s*[:=]\s*)"
    r"(?P<value>"
    r"(?P<value_quote>[\"'])(?P<quoted>(?:\\.|(?! (?P=value_quote)).)*)(?P=value_quote)"
    r"|(?P<bare>(?:bearer\s+)?[^\s,;}\]]+)"
    r")"
)


def timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def redact_secrets(text: str) -> str:
    def replace(match: re.Match) -> str:
        value = match.group("quoted") if match.group("value_quote") else match.group("bare")
        bearer = re.match(r"(?i)(bearer\s+)", value)
        redacted = f"{bearer.group(1) if bearer else ''}[REDACTED]"
        quote = match.group("value_quote") or ""
        return f"{match.group('prefix')}{quote}{redacted}{quote}"

    return SECRET_RE.sub(replace, text)


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


class AuditStore:
    """Persist reconstructable public I/O without credentials or hidden reasoning."""

    def __init__(self, root: Path, job_id: str, batch_id: str | None = None):
        self.root = root
        self.job_id = job_id
        self.batch_id = batch_id
        self.job_dir = root / "audits" / job_id
        self.event_path = self.job_dir / "events.jsonl"
        self.manifest_path = self.job_dir / "manifest.json"

    def artifact(self, kind: str, content: str | bytes) -> dict:
        raw = content.encode("utf-8") if isinstance(content, str) else content
        if kind in {"request", "response", "error"}:
            raw = redact_secrets(raw.decode("utf-8", errors="replace")).encode("utf-8")
        digest = sha256_bytes(raw)
        relative = Path("artifacts") / "sha256" / digest[:2] / f"{digest}.gz"
        path = self.root / relative
        if not path.exists():
            _atomic_write(path, gzip.compress(raw, mtime=0))
        return {
            "kind": kind,
            "sha256": digest,
            "bytes": len(raw),
            "encoding": "utf-8",
            "compression": "gzip",
            "path": relative.as_posix(),
        }

    def append(self, event: dict) -> dict:
        self.job_dir.mkdir(parents=True, exist_ok=True)
        record = {
            "schema_version": SCHEMA_VERSION,
            "event_id": event.get("event_id") or uuid.uuid4().hex,
            "recorded_at": timestamp(),
            "job_id": self.job_id,
            "batch_id": self.batch_id,
            **event,
        }
        with self.event_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        self._write_manifest()
        return record

    def update_commit(self, *, branch: str | None = None,
                      commit_hash: str | None = None,
                      pushed_at: str | None = None) -> dict:
        """Append repository publication metadata and refresh the manifest."""
        return self.append({
            "type": "commit_metadata",
            "branch": branch,
            "hash": commit_hash,
            "pushed_at": pushed_at,
        })

    def events(self) -> list[dict]:
        if not self.event_path.exists():
            return []
        return [json.loads(line) for line in self.event_path.read_text(encoding="utf-8").splitlines()
                if line.strip()]

    def _write_manifest(self) -> None:
        events = self.events()
        calls = [event for event in events if event.get("type") == "llm_attempt"]
        gates = [event for event in events if event.get("type") == "quality_gate"]
        commit_events = [event for event in events if event.get("type") == "commit_metadata"]
        artifacts: dict[str, dict] = {}
        stages: dict[str, dict] = {}
        for event in events:
            for ref in event.get("artifacts", {}).values():
                if isinstance(ref, dict) and ref.get("sha256"):
                    artifacts[ref["sha256"]] = ref
        for call in calls:
            stage = stages.setdefault(call["stage"], {
                "models": [], "efforts": [], "call_count": 0,
                "usage": {"input": None, "cached_input": None, "output": None,
                          "reasoning": None, "total": None},
                "usage_availability_reason": {},
            })
            if call.get("model") not in stage["models"]:
                stage["models"].append(call.get("model"))
            if call.get("effort") not in stage["efforts"]:
                stage["efforts"].append(call.get("effort"))
            stage["call_count"] += 1
        for stage_name, stage in stages.items():
            stage_calls = [call for call in calls if call.get("stage") == stage_name]
            for field in stage["usage"]:
                values = [(call.get("tokens") or {}).get(field) for call in stage_calls]
                if all(isinstance(value, int) and not isinstance(value, bool) for value in values):
                    stage["usage"][field] = sum(values)
                    continue
                reasons = [
                    (call.get("availability_reason") or {}).get("usage")
                    for call, value in zip(stage_calls, values)
                    if not isinstance(value, int) or isinstance(value, bool)
                ]
                stage["usage_availability_reason"][field] = next(
                    (reason for reason in reasons if reason),
                    "provider usage unavailable for one or more calls",
                )
        commit = {"branch": None, "hash": None, "pushed_at": None}
        for event in commit_events:
            for field in commit:
                if event.get(field) is not None:
                    commit[field] = event[field]
        first_sent = min((call["sent_at"] for call in calls if call.get("sent_at")), default=None)
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "job_id": self.job_id,
            "batch_id": self.batch_id,
            "translation_started_at": first_sent,
            "event_count": len(events),
            "event_log": {
                "path": self.event_path.relative_to(self.root).as_posix(),
                "sha256": sha256_bytes(self.event_path.read_bytes()),
            },
            "stages": stages,
            "quality_verdict": gates[-1].get("verdict") if gates else "not_run",
            "commit": commit,
            "artifacts": [artifacts[key] for key in sorted(artifacts)],
            "generated_at": timestamp(),
        }
        _atomic_write(
            self.manifest_path,
            (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        )


def read_artifact(root: Path, ref: dict) -> bytes:
    raw = gzip.decompress((root / ref["path"]).read_bytes())
    if sha256_bytes(raw) != ref["sha256"]:
        raise ValueError(f"artifact hash mismatch: {ref['path']}")
    return raw
