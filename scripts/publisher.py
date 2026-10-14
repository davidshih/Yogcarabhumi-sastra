#!/usr/bin/env python3
"""Immutable volume seals and explicit-path Git publication."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import audit


class PublishError(RuntimeError):
    pass


def _relative(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def create_attestation(root: Path, output: Path, sealed_paths: dict[str, Path]) -> dict:
    files = {
        name: {"path": _relative(root, path), "sha256": audit.sha256_bytes(path.read_bytes())}
        for name, path in sorted(sealed_paths.items())
    }
    payload = {"schema_version": "1.0", "files": files}
    payload["seal_sha256"] = audit.sha256_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )
    if output.exists():
        if json.loads(output.read_text(encoding="utf-8")) != payload:
            raise PublishError("immutable volume attestation already exists with different hashes")
        return payload
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                      encoding="utf-8")
    return payload


def verify_attestation(root: Path, path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PublishError(f"volume attestation is unreadable: {error}") from error
    seal = payload.pop("seal_sha256", None)
    expected = audit.sha256_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )
    if seal != expected:
        raise PublishError("volume attestation seal mismatch")
    payload["seal_sha256"] = seal
    for item in payload.get("files", {}).values():
        file_path = root / item["path"]
        if not file_path.is_file() or audit.sha256_bytes(file_path.read_bytes()) != item["sha256"]:
            raise PublishError(f"sealed file changed: {item['path']}")
    return payload


def publication_paths(root: Path, job_id: str, work: str, juan: int,
                      attestation_path: Path, include_pipeline_code: bool = False) -> list[Path]:
    candidates = [attestation_path]
    if attestation_path.is_file():
        attestation = json.loads(attestation_path.read_text(encoding="utf-8"))
        candidates.extend(root / item["path"] for item in attestation.get("files", {}).values())
    return sorted({path for path in candidates if path.is_file()})


def _run(command, root: Path, *args: str):
    return command([*args], cwd=root, capture_output=True, text=True)


def _read_receipt(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise PublishError("publication receipt is invalid") from error


def _write_receipt(path: Path, branch: str, content_commit: str, phase: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema_version": "1.0", "branch": branch,
        "content_commit": content_commit, "phase": phase,
    }, sort_keys=True) + "\n", encoding="utf-8")


def publish_volume(root: Path, store: audit.AuditStore, work: str, juan: int,
                   attestation_path: Path, *, include_pipeline_code: bool = False,
                   command=subprocess.run) -> str:
    verify_attestation(root, attestation_path)
    branch_result = _run(command, root, "git", "branch", "--show-current")
    branch = branch_result.stdout.strip() if branch_result.returncode == 0 else ""
    if not branch.startswith("codex/"):
        raise PublishError("publication requires a checked-out codex/* branch")
    remote = _run(command, root, "git", "ls-remote", "--exit-code", "--heads", "origin", branch)
    if remote.returncode == 0:
        if _run(command, root, "git", "fetch", "origin", branch).returncode != 0:
            raise PublishError("git fetch failed")
        ancestry = _run(command, root, "git", "merge-base", "--is-ancestor",
                        f"origin/{branch}", "HEAD")
        if ancestry.returncode != 0:
            raise PublishError("remote branch is ahead or diverged; rebase before publication")
    elif remote.returncode not in {2}:
        raise PublishError("could not determine remote branch state")
    receipt_path = store.job_dir / f"{work}-{juan:03d}-publication.json"
    receipt = _read_receipt(receipt_path)
    if receipt and receipt.get("branch") != branch:
        raise PublishError("publication receipt branch does not match current branch")
    if receipt:
        content_commit = receipt["content_commit"]
    else:
        staged = _run(command, root, "git", "diff", "--cached", "--name-only")
        if staged.returncode != 0 or staged.stdout.strip():
            raise PublishError("refusing to publish with pre-staged changes")
        allowed = publication_paths(root, store.job_id, work, juan, attestation_path,
                                    include_pipeline_code)
        add = _run(command, root, "git", "add", "--", *(_relative(root, path) for path in allowed))
        if add.returncode != 0:
            raise PublishError("git add failed")
        staged = _run(command, root, "git", "diff", "--cached", "--name-only")
        staged_paths = {line for line in staged.stdout.splitlines() if line}
        allowed_paths = {_relative(root, path) for path in allowed}
        if staged.returncode != 0 or not staged_paths or not staged_paths.issubset(allowed_paths):
            raise PublishError("staged paths exceed the explicit volume allowlist")
        message = f"feat: publish {work} juan {juan} audited translation"
        if _run(command, root, "git", "commit", "-m", message).returncode != 0:
            raise PublishError("git commit failed")
        revision = _run(command, root, "git", "rev-parse", "HEAD")
        if revision.returncode != 0:
            raise PublishError("could not read content commit hash")
        content_commit = revision.stdout.strip()
        _write_receipt(receipt_path, branch, content_commit, "content_committed")
        receipt = _read_receipt(receipt_path)
    if receipt["phase"] == "content_committed":
        if _run(command, root, "git", "push", "origin", branch).returncode != 0:
            raise PublishError("git push failed")
        store.update_commit(branch=branch, commit_hash=content_commit, pushed_at=audit.timestamp())
        _write_receipt(receipt_path, branch, content_commit, "metadata_pending")
    head = _run(command, root, "git", "rev-parse", "HEAD")
    if head.returncode != 0:
        raise PublishError("could not inspect publication retry state")
    if head.stdout.strip() == content_commit:
        audit_paths = [store.event_path, store.manifest_path, receipt_path]
        if _run(command, root, "git", "add", "--",
                *(_relative(root, path) for path in audit_paths)).returncode != 0:
            raise PublishError("could not stage publication audit metadata")
        if _run(command, root, "git", "commit", "-m",
                "chore: record translation publication audit").returncode != 0:
            raise PublishError("could not commit publication audit metadata")
    if _run(command, root, "git", "push", "origin", branch).returncode != 0:
        raise PublishError("could not push publication audit metadata")
    return content_commit
