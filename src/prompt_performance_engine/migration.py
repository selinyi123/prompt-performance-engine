"""Migration helpers for legacy Prompt and audit artifacts."""

from __future__ import annotations

import hashlib
from typing import Any

from .contracts import ARTIFACT_SCHEMA_VERSION, OptimizationRequest, PACKAGE_VERSION
from .hashing import hash_payload, sha256_json


def migrate_legacy_prompt(
    source_prompt: str,
    *,
    legacy_version: str = "unknown",
    domain: str | None = None,
) -> dict[str, Any]:
    request = OptimizationRequest(
        source_prompt=source_prompt,
        domain=domain,
    )
    request.validate()
    package: dict[str, Any] = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "package_version": PACKAGE_VERSION,
        "migration_type": "legacy_prompt",
        "legacy_version": legacy_version,
        "source_sha256": hashlib.sha256(
            source_prompt.encode("utf-8")
        ).hexdigest(),
        "request": request.to_dict(),
        "warnings": [
            "Historical scores, release labels, and superiority claims were not migrated.",
            "The migrated request requires fresh optimization and evidence generation.",
        ],
    }
    package["migration_sha256"] = hash_payload(package, "migration_sha256")
    return package


def _collect_version_values(value: Any, path: str = "$") -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if key in {
                "version",
                "schema_version",
                "framework_version",
                "protocol_version",
                "package_version",
            } and isinstance(child, (str, int, float)):
                found.append({"path": child_path, "value": str(child)})
            found.extend(_collect_version_values(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_collect_version_values(child, f"{path}[{index}]"))
    return found


def import_legacy_audit(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise TypeError("Legacy audit root must be an object.")
    versions = _collect_version_values(data)
    distinct_versions = sorted({item["value"] for item in versions})
    imported: dict[str, Any] = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "package_version": PACKAGE_VERSION,
        "migration_type": "legacy_audit_reference",
        "legacy_payload_sha256": sha256_json(data),
        "legacy_versions": versions,
        "version_conflict_detected": len(distinct_versions) > 1,
        "evidence": {
            "level": "E0",
            "status": "candidate",
            "claim": "legacy_reference_only",
            "limitations": [
                "Legacy audit content was not executed or independently verified.",
                "Legacy evidence labels cannot elevate current evidence.",
                "Fresh matched evaluation is required for any improvement claim.",
            ],
        },
        "preserved_summary": {
            "top_level_keys": sorted(str(key) for key in data),
            "legacy_payload_type": "object",
        },
    }
    imported["migration_sha256"] = hash_payload(
        imported,
        "migration_sha256",
    )
    return imported
