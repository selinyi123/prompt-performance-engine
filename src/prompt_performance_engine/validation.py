"""Deterministic validation for optimization artifacts."""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .audit import CHECKS, audit_prompt
from .contracts import (
    ARTIFACT_SCHEMA_VERSION,
    LEGACY_ARTIFACT_SCHEMA_VERSION,
    LEGACY_ARTIFACT_PRODUCER_VERSIONS,
    SUPPORTED_ARTIFACT_PRODUCER_VERSIONS,
    candidate_strategy_plan,
    load_strict_json_object,
)
from .evidence import Evidence, infer_evidence
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


def _strict_int(value: Any, *, minimum: int) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return value >= minimum
    return (
        isinstance(value, float)
        and math.isfinite(value)
        and value.is_integer()
        and value >= minimum
    )


def _field_violations(
    value: dict[str, Any],
    *,
    required: set[str],
    allowed: set[str],
    label: str,
    rule_id: str,
) -> list[Violation]:
    violations = [
        Violation(rule_id, f"{label} is missing field: {name}.")
        for name in sorted(required - set(value), key=str)
    ]
    violations.extend(
        Violation(rule_id, f"{label} contains unknown field: {name}.")
        for name in sorted(set(value) - allowed, key=str)
    )
    return violations


def _validate_legacy_audit_report(
    report: Any,
    *,
    expected_text_sha256: str,
    expected_source_sha256: str | None,
    label: str,
) -> tuple[list[Violation], bool]:
    """Validate a 0.3 audit structurally when its source text is unavailable.

    Legacy artifacts did not retain ``source_prompt``, so their audit cannot be
    replayed.  This compatibility path therefore never supplies authority
    beyond the artifact's candidate-only evidence ceiling.
    """

    if not isinstance(report, dict):
        return [Violation("A11", f"{label} audit report must be an object.")], False
    fields = {
        "schema_version",
        "text_sha256",
        "source_sha256",
        "passed",
        "checks",
        "findings",
    }
    violations = _field_violations(
        report,
        required=fields,
        allowed=fields,
        label=f"{label} audit report",
        rule_id="A11",
    )
    if report.get("schema_version") != LEGACY_ARTIFACT_SCHEMA_VERSION:
        violations.append(Violation("A11", f"{label} audit schema mismatch."))
    if report.get("text_sha256") != expected_text_sha256:
        violations.append(Violation("A12", f"{label} audit text hash mismatch."))
    if report.get("source_sha256") != expected_source_sha256:
        violations.append(Violation("A12", f"{label} audit source hash mismatch."))
    checks = report.get("checks")
    if (
        not isinstance(checks, list)
        or any(not isinstance(item, str) for item in checks)
        or len(checks) != len(set(checks))
        or set(checks) != set(CHECKS)
    ):
        violations.append(Violation("A11", f"{label} audit check set is incomplete."))

    findings = report.get("findings")
    calculated_passed = True
    finding_fields = {
        "rule_id",
        "severity",
        "category",
        "message",
        "evidence_span",
        "remediation",
        "blocking",
    }
    if not isinstance(findings, list):
        violations.append(Violation("A11", f"{label} audit findings must be a list."))
        calculated_passed = False
    else:
        for finding in findings:
            if not isinstance(finding, dict):
                violations.append(Violation("A11", f"{label} audit finding is invalid."))
                calculated_passed = False
                continue
            violations.extend(
                _field_violations(
                    finding,
                    required=finding_fields,
                    allowed=finding_fields,
                    label=f"{label} audit finding",
                    rule_id="A11",
                )
            )
            severity = finding.get("severity")
            if severity not in {"low", "medium", "high", "critical"}:
                violations.append(
                    Violation("A11", f"{label} audit finding severity is invalid.")
                )
            for field in finding_fields - {"severity", "blocking"}:
                if not isinstance(finding.get(field), str):
                    violations.append(
                        Violation("A11", f"{label} audit finding {field} is invalid.")
                    )
            expected_blocking = severity in {"high", "critical"}
            if finding.get("blocking") is not expected_blocking:
                violations.append(
                    Violation(
                        "A11",
                        f"{label} audit finding blocking flag is inconsistent.",
                    )
                )
            if expected_blocking:
                calculated_passed = False
    if not isinstance(report.get("passed"), bool) or (
        report.get("passed") is not calculated_passed
    ):
        violations.append(Violation("A11", f"{label} audit pass status is inconsistent."))
    return violations, calculated_passed and not violations


def _validate_selection(
    selection: Any,
    *,
    optimized_prompt: Any,
    legacy: bool,
) -> list[Violation]:
    violations: list[Violation] = []
    if not isinstance(selection, dict):
        return [Violation("A09", "runtime selection must be an object.")]
    fields = {
        "method",
        "candidate_count",
        "selected_index",
        "selector_response_sha256",
        "candidates",
    }
    violations.extend(
        _field_violations(
            selection,
            required=fields,
            allowed=fields,
            label="runtime selection",
            rule_id="A09",
        )
    )
    candidates = selection.get("candidates")
    valid_candidates = isinstance(candidates, list) and 1 <= len(candidates) <= 5
    if not valid_candidates:
        violations.append(Violation("A09", "runtime candidates are invalid."))
        return violations
    candidate_count = selection.get("candidate_count")
    selected_index = selection.get("selected_index")
    if not _strict_int(candidate_count, minimum=1) or candidate_count != len(candidates):
        violations.append(Violation("A09", "runtime candidate count mismatch."))
    if not _strict_int(selected_index, minimum=1) or selected_index > len(candidates):
        violations.append(Violation("A09", "runtime selected index is invalid."))

    selected_records: list[dict[str, Any]] = []
    candidate_fields = {
        "index",
        "strategy",
        "strategy_focus",
        "prompt",
        "prompt_sha256",
        "selected",
    }
    required_candidate = candidate_fields - (
        {"strategy", "strategy_focus"} if legacy else set()
    )
    allowed_candidate = candidate_fields
    expected_strategies = candidate_strategy_plan(len(candidates))
    for expected_index, candidate in enumerate(candidates, start=1):
        if not isinstance(candidate, dict):
            violations.append(Violation("A09", "runtime candidate must be an object."))
            continue
        violations.extend(
            _field_violations(
                candidate,
                required=required_candidate,
                allowed=allowed_candidate,
                label="runtime candidate",
                rule_id="A09",
            )
        )
        index = candidate.get("index")
        if not _strict_int(index, minimum=1) or index != expected_index:
            violations.append(Violation("A09", "runtime candidate index mismatch."))
        strategy = candidate.get("strategy")
        strategy_focus = candidate.get("strategy_focus")
        if legacy and strategy is None and strategy_focus is None:
            pass
        elif (strategy, strategy_focus) != expected_strategies[expected_index - 1]:
            violations.append(Violation("A09", "runtime candidate strategy is invalid."))
        prompt = candidate.get("prompt")
        prompt_hash = candidate.get("prompt_sha256")
        if (
            not isinstance(prompt, str)
            or not prompt.strip()
            or not isinstance(prompt_hash, str)
            or not SHA256_RE.fullmatch(prompt_hash)
            or prompt_hash != hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        ):
            violations.append(Violation("A09", "runtime candidate hash mismatch."))
        selected = candidate.get("selected")
        if not isinstance(selected, bool):
            violations.append(Violation("A09", "runtime candidate selected flag is invalid."))
        elif selected:
            selected_records.append(candidate)

    if (
        len(selected_records) != 1
        or selected_records[0].get("index") != selected_index
        or selected_records[0].get("prompt") != optimized_prompt
    ):
        violations.append(Violation("A09", "runtime selected candidate mismatch."))
    expected_method = "model_selector" if len(candidates) > 1 else "single_candidate"
    if selection.get("method") != expected_method:
        violations.append(Violation("A09", "runtime selection method mismatch."))
    selector_hash = selection.get("selector_response_sha256")
    if len(candidates) > 1:
        if not isinstance(selector_hash, str) or not SHA256_RE.fullmatch(selector_hash):
            violations.append(Violation("A09", "runtime selector hash is invalid."))
    elif selector_hash is not None:
        violations.append(
            Violation("A09", "single-candidate runtime must not have a selector hash.")
        )
    return violations


def _validate_runtime(
    runtime_data: Any,
    *,
    optimized_prompt: Any,
    legacy: bool,
) -> list[Violation]:
    if not isinstance(runtime_data, dict):
        return [Violation("A09", "runtime must be an object.")]
    required = {"model_calls", "total_calls", "total_usage"}
    allowed = required | {"selection"}
    violations = _field_violations(
        runtime_data,
        required=required,
        allowed=allowed,
        label="runtime",
        rule_id="A09",
    )
    calls = runtime_data.get("model_calls")
    calculated_usage: dict[str, int] = {}
    if not isinstance(calls, list) or not calls:
        violations.append(Violation("A09", "runtime model_calls must be non-empty."))
        calls = []
    call_fields = {
        "provider",
        "model",
        "response_id",
        "usage",
        "attempts",
        "elapsed_ms",
        "status",
        "purpose",
        "request_sha256",
        "response_sha256",
    }
    required_call_fields = call_fields - (
        {"purpose", "request_sha256", "response_sha256"} if legacy else set()
    )
    for call in calls:
        if not isinstance(call, dict):
            violations.append(Violation("A09", "runtime model call must be an object."))
            continue
        violations.extend(
            _field_violations(
                call,
                required=required_call_fields,
                allowed=call_fields,
                label="runtime model call",
                rule_id="A09",
            )
        )
        string_fields = ("provider", "model", "status") + (
            ("purpose",) if not legacy or "purpose" in call else ()
        )
        for field in string_fields:
            value = call.get(field)
            if not isinstance(value, str) or not value.strip():
                violations.append(Violation("A09", f"runtime model call {field} is invalid."))
        hash_fields = tuple(
            field
            for field in ("request_sha256", "response_sha256")
            if not legacy or field in call
        )
        for field in hash_fields:
            value = call.get(field)
            if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
                violations.append(Violation("A09", f"runtime model call {field} is invalid."))
        response_id = call.get("response_id")
        if response_id is not None and not isinstance(response_id, str):
            violations.append(Violation("A09", "runtime response_id is invalid."))
        if not _strict_int(call.get("attempts"), minimum=1):
            violations.append(Violation("A09", "runtime attempts is invalid."))
        if not _strict_int(call.get("elapsed_ms"), minimum=0):
            violations.append(Violation("A09", "runtime elapsed_ms is invalid."))
        usage = call.get("usage")
        if not isinstance(usage, dict):
            violations.append(Violation("A09", "runtime usage is invalid."))
            continue
        for key, value in usage.items():
            if not isinstance(key, str) or not _strict_int(value, minimum=0):
                violations.append(Violation("A09", "runtime usage entry is invalid."))
            else:
                calculated_usage[key] = calculated_usage.get(key, 0) + value

    total_calls = runtime_data.get("total_calls")
    if not _strict_int(total_calls, minimum=1) or total_calls != len(calls):
        violations.append(Violation("A09", "runtime total_calls mismatch."))
    total_usage = runtime_data.get("total_usage")
    if not isinstance(total_usage, dict) or any(
        not isinstance(key, str) or not _strict_int(value, minimum=0)
        for key, value in total_usage.items()
    ):
        violations.append(Violation("A09", "runtime total_usage is invalid."))
    elif total_usage != calculated_usage:
        violations.append(Violation("A09", "runtime total_usage mismatch."))
    if "selection" in runtime_data:
        violations.extend(
            _validate_selection(
                runtime_data.get("selection"),
                optimized_prompt=optimized_prompt,
                legacy=legacy,
            )
        )
    return violations


def _validate_artifact(data: Any) -> list[Violation]:
    if not isinstance(data, dict):
        return [Violation("A01", "Artifact root must be an object.")]

    base_fields = {
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
    producer_version = data.get("package_version")
    legacy = (
        isinstance(producer_version, str)
        and producer_version in LEGACY_ARTIFACT_PRODUCER_VERSIONS
    )
    required = base_fields | (set() if legacy else {"source_prompt"})
    violations = _field_violations(
        data,
        required=required,
        allowed=base_fields | {"source_prompt"},
        label="artifact",
        rule_id="A02",
    )
    if required - set(data):
        return violations

    expected_schema_version = (
        LEGACY_ARTIFACT_SCHEMA_VERSION if legacy else ARTIFACT_SCHEMA_VERSION
    )
    if data["schema_version"] != expected_schema_version:
        violations.append(Violation("A03", "Artifact schema version mismatch."))
    producer_version = data["package_version"]
    if (
        not isinstance(producer_version, str)
        or producer_version not in SUPPORTED_ARTIFACT_PRODUCER_VERSIONS
    ):
        violations.append(Violation("A04", "Unsupported artifact producer version."))
    source_prompt = data.get("source_prompt")
    source_hash = data["source_sha256"]
    if source_prompt is not None and (
        not isinstance(source_prompt, str) or not source_prompt.strip()
    ):
        violations.append(Violation("A05", "source_prompt must not be empty."))
    if (
        not isinstance(source_hash, str)
        or not SHA256_RE.fullmatch(source_hash)
        or (
            isinstance(source_prompt, str)
            and source_hash != hashlib.sha256(source_prompt.encode("utf-8")).hexdigest()
        )
    ):
        violations.append(Violation("A05", "source_sha256 does not match source_prompt."))
    optimized_prompt = data["optimized_prompt"]
    if not isinstance(optimized_prompt, str) or not optimized_prompt.strip():
        violations.append(Violation("A06", "optimized_prompt must not be empty."))
    elif markers := find_mojibake(optimized_prompt):
        violations.append(
            Violation("A07", f"optimized_prompt contains mojibake markers: {markers}.")
        )
    domain = data["domain"]
    if not isinstance(domain, str) or not domain.strip():
        violations.append(Violation("A08", "domain must not be empty."))
    if (
        not isinstance(data["architecture"], str)
        or data["architecture"] not in ARCHITECTURES
    ):
        violations.append(Violation("A08", "Unknown optimization architecture."))

    violations.extend(
        _validate_runtime(
            data["runtime"],
            optimized_prompt=optimized_prompt,
            legacy=legacy,
        )
    )
    artifact_hash = data["artifact_payload_sha256"]
    try:
        expected_artifact_hash = hash_payload(data, "artifact_payload_sha256")
    except (TypeError, ValueError):
        expected_artifact_hash = None
    if (
        not isinstance(artifact_hash, str)
        or not SHA256_RE.fullmatch(artifact_hash)
        or artifact_hash != expected_artifact_hash
    ):
        violations.append(Violation("A09", "Artifact payload hash mismatch."))

    audit_data = data["audit"]
    optimized_audit_passed = False
    if not isinstance(audit_data, dict):
        violations.append(Violation("A10", "audit must be an object."))
    else:
        violations.extend(
            _field_violations(
                audit_data,
                required={"source", "optimized"},
                allowed={"source", "optimized"},
                label="audit",
                rule_id="A11",
            )
        )
        if (
            isinstance(source_prompt, str)
            and source_prompt.strip()
            and isinstance(optimized_prompt, str)
            and optimized_prompt.strip()
        ):
            expected_source_audit = audit_prompt(source_prompt).to_dict()
            expected_optimized_audit = audit_prompt(
                optimized_prompt,
                source_prompt=source_prompt,
            ).to_dict()
            if audit_data.get("source") != expected_source_audit:
                violations.append(
                    Violation("A13", "source audit does not match deterministic replay.")
                )
            if audit_data.get("optimized") != expected_optimized_audit:
                violations.append(
                    Violation("A13", "optimized audit does not match deterministic replay.")
                )
            optimized_audit_passed = (
                audit_data.get("optimized") == expected_optimized_audit
                and expected_optimized_audit["passed"] is True
            )
        elif legacy and isinstance(source_hash, str) and isinstance(optimized_prompt, str):
            source_violations, _ = _validate_legacy_audit_report(
                audit_data.get("source"),
                expected_text_sha256=source_hash,
                expected_source_sha256=None,
                label="source",
            )
            optimized_violations, optimized_audit_passed = (
                _validate_legacy_audit_report(
                    audit_data.get("optimized"),
                    expected_text_sha256=hashlib.sha256(
                        optimized_prompt.encode("utf-8")
                    ).hexdigest(),
                    expected_source_sha256=source_hash,
                    label="optimized",
                )
            )
            violations.extend(source_violations)
            violations.extend(optimized_violations)

    evidence_data = data["evidence"]
    if not isinstance(evidence_data, dict):
        violations.append(Violation("A14", "evidence must be an object."))
    else:
        evidence_fields = {"level", "status", "claim", "limitations"}
        violations.extend(
            _field_violations(
                evidence_data,
                required=evidence_fields,
                allowed=evidence_fields,
                label="evidence",
                rule_id="A16",
            )
        )
        limitations = evidence_data.get("limitations")
        if not isinstance(limitations, list):
            violations.append(Violation("A16", "Evidence limitations must be an array."))
        else:
            try:
                evidence = Evidence(
                    level=evidence_data.get("level", ""),
                    status=evidence_data.get("status", ""),
                    claim=evidence_data.get("claim", ""),
                    limitations=tuple(limitations),
                )
                evidence.validate()
            except (TypeError, ValueError) as exc:
                violations.append(Violation("A16", str(exc)))
        inferred = infer_evidence(
            deterministic_checks_passed=optimized_audit_passed,
        )
        expected_evidence = {
            "level": inferred.level,
            "status": inferred.status,
            "claim": inferred.claim,
            "limitations": list(inferred.limitations),
        }
        if evidence_data != expected_evidence:
            if legacy:
                detail = (
                    "Legacy artifact producers remain readable as candidates but "
                    "cannot establish frontier evidence."
                )
            else:
                detail = (
                    "Artifact evidence does not match replayed deterministic facts."
                )
            violations.append(
                Violation(
                    "A17",
                    detail,
                )
            )
    return violations


def validate_artifact(data: Any) -> list[Violation]:
    """Validate untrusted artifact input without leaking structural exceptions."""
    try:
        return _validate_artifact(data)
    except (
        ArithmeticError,
        AttributeError,
        KeyError,
        RecursionError,
        TypeError,
        UnicodeError,
        ValueError,
    ) as exc:
        return [Violation("A18", f"Malformed artifact: {exc}")]


def validate_artifact_file(path: Path) -> list[Violation]:
    """Load and validate an untrusted artifact without numeric normalization gaps."""

    try:
        data = load_strict_json_object(path, label="optimization artifact")
    except (TypeError, ValueError) as exc:
        return [Violation("A18", f"Malformed artifact: {exc}")]
    return validate_artifact(data)
