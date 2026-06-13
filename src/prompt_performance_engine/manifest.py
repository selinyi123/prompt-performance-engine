"""Build and verify deterministic file manifests."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Iterable

from .contracts import PACKAGE_VERSION
from .hashing import hash_payload


def build_manifest(paths: Iterable[Path], *, root: Path) -> dict[str, Any]:
    resolved_root = root.resolve()
    entries: list[dict[str, Any]] = []
    for path in sorted((path.resolve() for path in paths), key=str):
        if not path.is_file():
            raise ValueError(f"Manifest entry is not a file: {path}")
        try:
            relative = path.relative_to(resolved_root)
        except ValueError as exc:
            raise ValueError(f"Manifest entry is outside root: {path}") from exc
        payload = path.read_bytes()
        entries.append(
            {
                "path": relative.as_posix(),
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )

    manifest: dict[str, Any] = {
        "schema_version": "1.0.0",
        "package_version": PACKAGE_VERSION,
        "entries": entries,
    }
    manifest["manifest_sha256"] = hash_payload(manifest, "manifest_sha256")
    return manifest


def verify_manifest(
    manifest: dict[str, Any],
    *,
    root: Path | None = None,
) -> list[str]:
    failures: list[str] = []
    expected_hash = hash_payload(manifest, "manifest_sha256")
    if manifest.get("manifest_sha256") != expected_hash:
        failures.append("manifest hash mismatch")
    if manifest.get("package_version") != PACKAGE_VERSION:
        failures.append("manifest package version mismatch")
    entries = manifest.get("entries")
    if not isinstance(entries, list):
        return [*failures, "manifest entries must be a list"]
    if root is None:
        return failures

    resolved_root = root.resolve()
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            failures.append("invalid manifest entry")
            continue
        path = (resolved_root / entry["path"]).resolve()
        try:
            path.relative_to(resolved_root)
        except ValueError:
            failures.append(f"entry escapes manifest root: {entry['path']}")
            continue
        if not path.is_file():
            failures.append(f"missing file: {entry['path']}")
            continue
        payload = path.read_bytes()
        if entry.get("bytes") != len(payload):
            failures.append(f"size mismatch: {entry['path']}")
        if entry.get("sha256") != hashlib.sha256(payload).hexdigest():
            failures.append(f"hash mismatch: {entry['path']}")
    return failures
