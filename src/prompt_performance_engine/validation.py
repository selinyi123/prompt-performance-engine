"""Deterministic validation for optimization artifacts."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from .audit import CHECKS
from .contracts import ARTIFACT_SCHEMA_VERSION, PACKAGE_VERSION
from .evidence import Evidence
from .hashing import hash_payload


ARCHITECTURES = {
    "direct",
    "brief_then_execute",
    "research_then_synthesize",
    "generate_critique_revise",
    "multi_candidate_tournament",
    "plan_execute_verify",
    "strict_contract",
    "tool_agent",
    "high_risk_review",
}

SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
MOJIBAKE_MARKERS = tuple(
    value.encode("ascii").decode("unicode_escape")
    for value in (
        r"\u951b",
        r"\u9286",
        r"\u9225",
        r"\u5a34",
        r"\u7487",
        r"\u9356",
        r"\u6d7c",
        r"\u5bee",
        r"\u935a",
        r"\u6bb7",
    )
)


@dataclass(frozen=True)
class Violation:
    rule_id: str
    detail: str


def find_mojibake(text: str) -> list[str]:
    return sorted({marker for marker in MOJIBAKE_MARKERS if marker in text})


def _validate_audit_report(
    report: Any,
    *,
    expected_text_sha256: str,
    expected_source_sha256: str | None,
    label: str,
) -> tuple[list[Violation], bool]:
    violations: list[Violation] = []
    if not isinstance(report, dict):
        return [Violation("A11", f"{label} audit report must be an object.")], False
    if report.get("schema_version") != "1.0.0":
        violations.append(Violation("A11", f"{label} audit schema mismatch."))
    if report.get("text_sha256") != expected_text_sha256:
        violations.append(Violation("A12", f"{label} audit text hash mismatch."))
    if report.get("source_sha256") != expected_source_sha256:
        violations.append(Violation("A12", f"{label} audit source hash mismatch."))
    checks = report.get("checks")
    if not isinstance(checks, list) or set(checks) != set(CHECKS):
        violations.append(Violation("A11", f"{label} audit check set is incomplete."))

    findings = report.get("findings")
    if not isinstance(findings, list):
        violations.append(Violation("A11", f"{label} audit findings must be a list."))
        return violations, False
    calculated_passed = True
    for finding in findings:
        if not isinstance(finding, dict):
            violations.append(Violation("A11", f"{label} audit finding is invalid."))
            calculated_passed = False
            continue
        severity = finding.get("severity")
        expected_blocking = severity in {"high", "critical"}
        if finding.get("blocking") is not expected_blocking:
            violations.append(
                Violation("A11", f"{label} audit finding blocking flag is inconsistent.")
            )
        if expected_blocking:
            calculated_passed = False
    if report.get("passed") is not calculated_passed:
        violations.append(Violation("A11", f"{label} audit pass status is inconsistent."))
    return violations, calculated_passed and not violations


def validate_artifact(data: Any) -> list[Violation]:
    if not isinstance(data, dict):
        return [Violation("A01", "Artifact root must be an object.")]

    required = {
        "schema_version",
        "package_version",
        "source_sha256",
        "optimized_prompt",
        "domain",
        "architecture",
        "runtime",
        "audit",
        "evidence",
        "artifact_payload_sha256",
    }
    missing = sorted(required - set(data))
    violations = [
        Violation("A02", f"Missing required field: {field}.")
        for field in missing
    ]
    if missing:
        return violations

    if data["schema_version"] != ARTIFACT_SCHEMA_VERSION:
        violations.append(Violation("A03", "Artifact schema version mismatch."))
    if data["package_version"] != PACKAGE_VERSION:
        violations.append(Violation("A04", "Package version mismatch."))
    if not isinstance(data["source_sha256"], str) or not SHA256_RE.fullmatch(data["source_sha256"]):
        violations.append(Violation("A05", "source_sha256 must be a lowercase SHA-256 hex string."))
    optimized_prompt = data["optimized_prompt"]
    if not isinstance(optimized_prompt, str) or not optimized_prompt.strip():
        violations.append(Violation("A06", "optimized_prompt must not be empty."))
    elif markers := find_mojibake(optimized_prompt):
        violations.append(Violation("A07", f"optimized_prompt contains mojibake markers: {markers}."))
    if data["architecture"] not in ARCHITECTURES:
        violations.append(Violation("A08", "Unknown optimization architecture."))

    runtime_data = data["runtime"]
    if not isinstance(runtime_data, dict):
        violations.append(Violation("A09", "runtime must be an object."))
    else:
        calls = runtime_data.get("model_calls")
        if not isinstance(calls, list) or not calls:
            violations.append(Violation("A09", "runtime model_calls must be non-empty."))
        elif runtime_data.get("total_calls") != len(calls):
            violations.append(Violation("A09", "runtime total_calls mismatch."))
        else:
            calculated_usage: dict[str, int] = {}
            for call in calls:
                if not isinstance(call, dict):
                    violations.append(Violation("A09", "runtime model call is invalid."))
                    continue
                if "text" in call or "authorization" in {
                    str(key).lower() for key in call
                }:
                    violations.append(
                        Violation("A09", "runtime metadata contains forbidden content.")
                    )
                usage = call.get("usage", {})
                if not isinstance(usage, dict):
                    violations.append(Violation("A09", "runtime usage is invalid."))
                    continue
                for key, value in usage.items():
                    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                        violations.append(Violation("A09", "runtime usage value is invalid."))
                    else:
                        calculated_usage[key] = calculated_usage.get(key, 0) + value
            if runtime_data.get("total_usage") != calculated_usage:
                violations.append(Violation("A09", "runtime total_usage mismatch."))

    artifact_hash = data["artifact_payload_sha256"]
    if (
        not isinstance(artifact_hash, str)
        or not SHA256_RE.fullmatch(artifact_hash)
        or artifact_hash != hash_payload(data, "artifact_payload_sha256")
    ):
        violations.append(Violation("A09", "Artifact payload hash mismatch."))

    audit_data = data["audit"]
    optimized_audit_passed = False
    if not isinstance(audit_data, dict):
        violations.append(Violation("A10", "audit must be an object."))
    else:
        source_audit = audit_data.get("source")
        optimized_audit = audit_data.get("optimized")
        source_violations, _ = _validate_audit_report(
            source_audit,
            expected_text_sha256=data["source_sha256"],
            expected_source_sha256=None,
            label="source",
        )
        violations.extend(source_violations)
        if isinstance(optimized_prompt, str):
            expected_optimized_hash = hashlib.sha256(
                optimized_prompt.encode("utf-8")
            ).hexdigest()
            optimized_violations, optimized_audit_passed = _validate_audit_report(
                optimized_audit,
                expected_text_sha256=expected_optimized_hash,
                expected_source_sha256=data["source_sha256"],
                label="optimized",
            )
            violations.extend(optimized_violations)

    evidence_data = data["evidence"]
    if not isinstance(evidence_data, dict):
        violations.append(Violation("A14", "evidence must be an object."))
    else:
        try:
            evidence = Evidence(
                level=evidence_data.get("level", ""),
                status=evidence_data.get("status", ""),
                claim=evidence_data.get("claim", ""),
                limitations=tuple(evidence_data.get("limitations", [])),
            )
            evidence.validate()
            if evidence.level != "E0" and not optimized_audit_passed:
                violations.append(
                    Violation("A15", "Static evidence requires a passing optimized audit.")
                )
        except (TypeError, ValueError) as exc:
            violations.append(Violation("A16", str(exc)))
    return violations
