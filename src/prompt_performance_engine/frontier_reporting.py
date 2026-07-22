"""Fail-closed final reporting and public-claim contracts for frontier evidence.

The helpers in this module deliberately separate a stable-release readiness
result from the frontier performance gates.  Every affirmative gate is bound to
one typed evidence digest and to a receipt checked at a trusted host boundary.
Local hashes alone never grant ``top_tier_scoped`` authority.
"""

from __future__ import annotations

import copy
import math
import re
from datetime import datetime
from typing import Any, Mapping, Protocol

from .frontier_contracts import (
    FRONTIER_CONTRACT_SCHEMA_VERSION,
    SHA256_RE,
    validate_frontier_release_policy,
)
from .hashing import hash_payload, sha256_json


FRONTIER_REPORT_STATUSES = frozenset(
    {
        "top_tier_scoped",
        "verified_improvement",
        "not_superior",
        "not_evaluable",
    }
)
FRONTIER_GATE_IDS = (
    "preflight",
    "readiness",
    "quality",
    "safety",
    "robustness",
    "cost",
    "latency",
    "independence",
    "reproduction",
)
GATE_EVIDENCE_KINDS = {
    "preflight": "frontier_preflight_report",
    "readiness": "stable_readiness_report",
    "quality": "statistics",
    "safety": "safety",
    "robustness": "robustness",
    "cost": "efficiency",
    "latency": "efficiency",
    "independence": "independence",
    "reproduction": "external_reproduction",
}
GATE_OUTCOMES = frozenset({"passed", "failed", "not_evaluable"})

FRONTIER_REPORT_INPUT_FIELDS = frozenset(
    {
        "schema_version",
        "report_id",
        "campaign_id",
        "generated_at",
        "scope",
        "source_artifacts_sha256",
        "environment_sha256",
        "gates",
        "required_strong_baseline_ids",
        "baseline_comparisons",
        "limitations",
    }
)
FRONTIER_REPORT_FIELDS = FRONTIER_REPORT_INPUT_FIELDS | frozenset(
    {"status", "failures", "report_sha256"}
)
REPORT_SCOPE_FIELDS = frozenset(
    {
        "campaign_sha256",
        "policy_sha256",
        "commitment_sha256",
        "analysis_sha256",
        "preflight_report_sha256",
        "target_model_snapshots",
        "domain_ids",
        "task_distribution_sha256",
        "evaluation_harness_sha256",
        "primary_budget_dimension",
        "budget_ceiling_sha256",
        "evidence_as_of",
        "expires_at",
    }
)
GATE_FIELDS = frozenset(
    {
        "gate_id",
        "outcome",
        "evidence_kind",
        "evidence_sha256",
        "authority_id",
        "authority_receipt_sha256",
        "failures",
    }
)
BASELINE_COMPARISON_FIELDS = frozenset(
    {
        "baseline_id",
        "baseline_kind",
        "outcome",
        "metric_id",
        "meaningful_margin",
        "point_estimate",
        "interval_lower",
        "interval_upper",
        "confidence_level",
        "evidence_sha256",
        "authority_id",
        "authority_receipt_sha256",
        "failures",
    }
)
STRONG_BASELINE_KINDS = frozenset(
    {"identity", "no_op", "expert", "random_search", "public_optimizer"}
)
COMPARISON_OUTCOMES = frozenset(
    {"superior", "not_superior", "not_evaluable"}
)

# Authorities may cover closely related evidence, but an authority must not
# attest across independent control roles.  In particular, the quality/statistics
# authority may sign all strong-baseline comparisons and the efficiency
# authority may sign both cost and latency; every other role is separated.
GATE_AUTHORITY_ROLES = {
    "preflight": "preflight",
    "readiness": "readiness",
    "quality": "quality_statistics",
    "safety": "safety",
    "robustness": "robustness",
    "cost": "efficiency",
    "latency": "efficiency",
    "independence": "independence",
    "reproduction": "reproduction",
}
INVALIDATING_GATE_IDS = frozenset(
    {"preflight", "readiness", "independence", "reproduction"}
)

CLAIM_INVENTORY_FIELDS = frozenset(
    {
        "schema_version",
        "inventory_id",
        "report_id",
        "report_sha256",
        "claims",
        "inventory_sha256",
    }
)
CLAIM_FIELDS = frozenset(
    {
        "claim_id",
        "claim_status",
        "statement",
        "scope",
        "scope_sha256",
        "metrics",
        "evidence_summary_sha256",
        "limitations",
        "expires_at",
        "authority_id",
        "authority_receipt_sha256",
        "claim_sha256",
    }
)
CLAIM_SCOPE_FIELDS = frozenset(
    {
        "target_model_snapshots",
        "domain_ids",
        "task_distribution_sha256",
        "evaluation_harness_sha256",
        "budget_ceiling_sha256",
        "evidence_as_of",
        "expires_at",
    }
)
CLAIM_METRIC_FIELDS = frozenset(
    {
        "source_kind",
        "source_id",
        "metric_id",
        "point_estimate",
        "interval_lower",
        "interval_upper",
        "confidence_level",
        "evidence_sha256",
    }
)
CLAIM_METRIC_SOURCE_KINDS = frozenset({"baseline_comparison", "gate"})

UTC_TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$"
)
IDENTIFIER_RE = re.compile(r"^[^\s]{1,256}$")


class FrontierReportAuthorityVerifier(Protocol):
    """Trusted boundary for typed gate and strong-baseline receipts."""

    def verify(
        self,
        *,
        evidence_type: str,
        evidence_id: str,
        authority_id: str,
        context_sha256: str,
    ) -> str | None:
        """Return the canonical receipt digest, or ``None`` when unverified."""


# Compatibility name for callers that only use gate receipts.
FrontierGateAuthorityVerifier = FrontierReportAuthorityVerifier


class FrontierClaimAuthorityVerifier(Protocol):
    """Trusted boundary for a proposed public claim."""

    def verify(
        self,
        *,
        claim_id: str,
        authority_id: str,
        context_sha256: str,
    ) -> str | None:
        """Return the canonical receipt digest, or ``None`` when unverified."""


def _unique(failures: list[str]) -> list[str]:
    return list(dict.fromkeys(failures))


def _exact_fields(value: Any, fields: frozenset[str], label: str) -> list[str]:
    if not isinstance(value, dict):
        return [f"{label} must be an object"]
    failures: list[str] = []
    if fields - set(value):
        failures.append(f"{label} is missing required fields")
    if set(value) - fields:
        failures.append(f"{label} contains unknown fields")
    return failures


def _valid_identifier(value: Any) -> bool:
    return isinstance(value, str) and IDENTIFIER_RE.fullmatch(value) is not None


def _valid_sha256(value: Any) -> bool:
    return isinstance(value, str) and SHA256_RE.fullmatch(value) is not None


def _valid_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or UTC_TIMESTAMP_RE.fullmatch(value) is None:
        return False
    try:
        datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return True


def _timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value[:-1] + "+00:00")


def _valid_string_array(
    value: Any,
    *,
    allow_empty: bool = False,
    identifiers: bool = False,
) -> bool:
    if not isinstance(value, list) or (not allow_empty and not value):
        return False
    validator = _valid_identifier if identifiers else (
        lambda item: isinstance(item, str) and bool(item.strip())
    )
    return all(validator(item) for item in value) and len(value) == len(set(value))


def _valid_failures(value: Any, *, empty: bool | None = None) -> bool:
    if not _valid_string_array(value, allow_empty=True):
        return False
    if value != sorted(value):
        return False
    if empty is True and value:
        return False
    if empty is False and not value:
        return False
    return True


def _safe_payload_hash(value: Any, hash_field: str) -> str | None:
    try:
        return hash_payload(value, hash_field) if isinstance(value, dict) else None
    except (OverflowError, RecursionError, TypeError, ValueError):
        return None


def _validate_report_scope(scope: Any) -> list[str]:
    failures = _exact_fields(scope, REPORT_SCOPE_FIELDS, "frontier report scope")
    if not isinstance(scope, dict):
        return failures
    for field in (
        "campaign_sha256",
        "policy_sha256",
        "commitment_sha256",
        "analysis_sha256",
        "preflight_report_sha256",
        "task_distribution_sha256",
        "evaluation_harness_sha256",
        "budget_ceiling_sha256",
    ):
        if not _valid_sha256(scope.get(field)):
            failures.append(f"frontier report scope {field} is invalid")
    for field in ("target_model_snapshots", "domain_ids"):
        if not _valid_string_array(scope.get(field), identifiers=True):
            failures.append(f"frontier report scope {field} is invalid")
    if scope.get("primary_budget_dimension") not in {
        "calls",
        "input_tokens",
        "output_tokens",
        "money_microunits",
        "wall_clock_ms",
    }:
        failures.append("frontier report primary budget dimension is invalid")
    for field in ("evidence_as_of", "expires_at"):
        if not _valid_timestamp(scope.get(field)):
            failures.append(f"frontier report scope {field} is invalid")
    if all(_valid_timestamp(scope.get(field)) for field in ("evidence_as_of", "expires_at")):
        if _timestamp(scope["expires_at"]) <= _timestamp(scope["evidence_as_of"]):
            failures.append("frontier report scope expiry must follow its evidence date")
    return failures


def _validate_gate(gate: Any) -> list[str]:
    failures = _exact_fields(gate, GATE_FIELDS, "frontier gate")
    if not isinstance(gate, dict):
        return failures
    gate_id = gate.get("gate_id")
    outcome = gate.get("outcome")
    if gate_id not in FRONTIER_GATE_IDS:
        failures.append("frontier gate id is invalid")
    if outcome not in GATE_OUTCOMES:
        failures.append("frontier gate outcome is invalid")
    if gate.get("evidence_kind") != GATE_EVIDENCE_KINDS.get(gate_id):
        failures.append("frontier gate evidence kind is invalid")

    evidence = gate.get("evidence_sha256")
    authority = gate.get("authority_id")
    receipt = gate.get("authority_receipt_sha256")
    for value, label, validator in (
        (evidence, "evidence digest", _valid_sha256),
        (authority, "authority id", _valid_identifier),
        (receipt, "authority receipt", _valid_sha256),
    ):
        if value is not None and not validator(value):
            failures.append(f"frontier gate {label} is invalid")
    populated = (evidence is not None, authority is not None, receipt is not None)
    if outcome in {"passed", "failed"} and populated != (True, True, True):
        failures.append("evaluated frontier gate requires evidence and authority")
    if any(populated) and not all(populated):
        failures.append("frontier gate evidence and authority must be all present or all absent")
    if not _valid_failures(
        gate.get("failures"),
        empty=True if outcome == "passed" else False,
    ):
        failures.append("frontier gate failures are invalid")
    return failures


def _validate_baseline_comparison(comparison: Any) -> list[str]:
    failures = _exact_fields(
        comparison,
        BASELINE_COMPARISON_FIELDS,
        "frontier baseline comparison",
    )
    if not isinstance(comparison, dict):
        return failures
    if not _valid_identifier(comparison.get("baseline_id")):
        failures.append("frontier baseline comparison id is invalid")
    if comparison.get("baseline_kind") not in STRONG_BASELINE_KINDS:
        failures.append("frontier baseline comparison kind is invalid")
    outcome = comparison.get("outcome")
    if outcome not in COMPARISON_OUTCOMES:
        failures.append("frontier baseline comparison outcome is invalid")
    if not _valid_identifier(comparison.get("metric_id")):
        failures.append("frontier baseline comparison metric id is invalid")
    for field in (
        "meaningful_margin",
        "point_estimate",
        "interval_lower",
        "interval_upper",
    ):
        if not _valid_number(comparison.get(field)):
            failures.append(f"frontier baseline comparison {field} is invalid")
    margin = comparison.get("meaningful_margin")
    if _valid_number(margin) and not 0 <= margin <= 1:
        failures.append("frontier baseline comparison meaningful margin is invalid")
    for field in ("point_estimate", "interval_lower", "interval_upper"):
        value = comparison.get(field)
        if _valid_number(value) and not -1 <= value <= 1:
            failures.append(f"frontier baseline comparison {field} is out of range")
    confidence = comparison.get("confidence_level")
    if not _valid_number(confidence) or not 0.5 <= confidence < 1:
        failures.append("frontier baseline comparison confidence level is invalid")
    values = [
        comparison.get("interval_lower"),
        comparison.get("point_estimate"),
        comparison.get("interval_upper"),
    ]
    if all(_valid_number(value) for value in values) and not (
        values[0] <= values[1] <= values[2]
    ):
        failures.append("frontier baseline comparison interval is invalid")
    if _valid_number(values[0]) and _valid_number(margin):
        if outcome == "superior" and values[0] <= margin:
            failures.append(
                "superior baseline comparison must clear its meaningful margin"
            )
        if outcome == "not_superior" and values[0] > margin:
            failures.append(
                "not-superior baseline comparison contradicts its interval"
            )
    evidence = comparison.get("evidence_sha256")
    authority = comparison.get("authority_id")
    receipt = comparison.get("authority_receipt_sha256")
    for value, label, validator in (
        (evidence, "evidence digest", _valid_sha256),
        (authority, "authority id", _valid_identifier),
        (receipt, "authority receipt", _valid_sha256),
    ):
        if value is not None and not validator(value):
            failures.append(f"frontier baseline comparison {label} is invalid")
    populated = (evidence is not None, authority is not None, receipt is not None)
    if outcome in {"superior", "not_superior"} and populated != (True, True, True):
        failures.append(
            "evaluated frontier baseline comparison requires evidence and authority"
        )
    if any(populated) and not all(populated):
        failures.append(
            "frontier baseline comparison evidence and authority must be all present or all absent"
        )
    if not _valid_failures(
        comparison.get("failures"),
        empty=True if outcome == "superior" else False,
    ):
        failures.append("frontier baseline comparison failures are invalid")
    return failures


def validate_frontier_report_input(report_input: Any) -> list[str]:
    """Validate the deterministic report payload before status derivation."""

    failures = _exact_fields(
        report_input,
        FRONTIER_REPORT_INPUT_FIELDS,
        "frontier report input",
    )
    if not isinstance(report_input, dict):
        return failures
    if report_input.get("schema_version") != FRONTIER_CONTRACT_SCHEMA_VERSION:
        failures.append("unsupported frontier report schema")
    for field in ("report_id", "campaign_id"):
        if not _valid_identifier(report_input.get(field)):
            failures.append(f"frontier report {field} is invalid")
    if not _valid_timestamp(report_input.get("generated_at")):
        failures.append("frontier report generation timestamp is invalid")
    failures.extend(_validate_report_scope(report_input.get("scope")))
    scope = report_input.get("scope")
    if (
        _valid_timestamp(report_input.get("generated_at"))
        and isinstance(scope, dict)
        and _valid_timestamp(scope.get("evidence_as_of"))
        and _valid_timestamp(scope.get("expires_at"))
        and not (
            _timestamp(scope["evidence_as_of"])
            <= _timestamp(report_input["generated_at"])
            < _timestamp(scope["expires_at"])
        )
    ):
        failures.append(
            "frontier report generation timestamp is outside its evidence window"
        )
    for field in ("source_artifacts_sha256", "environment_sha256"):
        if not _valid_sha256(report_input.get(field)):
            failures.append(f"frontier report {field} is invalid")
    gates = report_input.get("gates")
    gate_ids: list[str] = []
    if not isinstance(gates, list):
        failures.append("frontier report gates must be an array")
    else:
        for gate in gates:
            failures.extend(_validate_gate(gate))
            if isinstance(gate, dict) and isinstance(gate.get("gate_id"), str):
                gate_ids.append(gate["gate_id"])
        if gate_ids != list(FRONTIER_GATE_IDS):
            failures.append("frontier report gates must use the canonical complete order")
    required_baselines = report_input.get("required_strong_baseline_ids")
    if not _valid_string_array(required_baselines, identifiers=True):
        failures.append("frontier report required strong baseline ids are invalid")
        required_baselines = []
    comparisons = report_input.get("baseline_comparisons")
    comparison_ids: list[str] = []
    comparison_kinds: list[str] = []
    if not isinstance(comparisons, list) or not comparisons:
        failures.append("frontier report baseline comparisons must be non-empty")
    else:
        for comparison in comparisons:
            failures.extend(_validate_baseline_comparison(comparison))
            if isinstance(comparison, dict):
                if isinstance(comparison.get("baseline_id"), str):
                    comparison_ids.append(comparison["baseline_id"])
                if isinstance(comparison.get("baseline_kind"), str):
                    comparison_kinds.append(comparison["baseline_kind"])
        if comparison_ids != required_baselines:
            failures.append(
                "frontier baseline comparisons must match the canonical required order"
            )
        if set(comparison_kinds) != STRONG_BASELINE_KINDS:
            failures.append(
                "frontier baseline comparisons must cover every strong baseline kind"
            )
        if comparison_kinds.count("identity") != 1 or (
            comparison_kinds and comparison_kinds[0] != "identity"
        ):
            failures.append(
                "frontier original Prompt comparison must be first and unique"
            )
    if not _valid_string_array(report_input.get("limitations")):
        failures.append("frontier report limitations must be a non-empty unique array")
    return _unique(failures)


def validate_frontier_report_policy_binding(
    report_input: Any,
    release_policy: Any,
) -> list[str]:
    """Bind report thresholds and baseline order to one validated policy."""

    failures: list[str] = []
    policy_failures = validate_frontier_release_policy(release_policy)
    if policy_failures:
        return ["validated frontier release policy is required"]
    if not isinstance(report_input, dict) or not isinstance(release_policy, dict):
        return ["frontier report policy binding is invalid"]
    scope = report_input.get("scope")
    comparisons = report_input.get("baseline_comparisons")
    if not isinstance(scope, dict) or not isinstance(comparisons, list):
        return ["frontier report policy binding is invalid"]

    if scope.get("policy_sha256") != release_policy.get("policy_sha256"):
        failures.append("frontier report policy digest does not match release policy")
    if scope.get("primary_budget_dimension") != release_policy.get(
        "primary_budget_dimension"
    ):
        failures.append("frontier report budget dimension does not match release policy")
    required_ids = release_policy.get("required_strong_baseline_ids")
    if report_input.get("required_strong_baseline_ids") != required_ids:
        failures.append("frontier report baseline ids do not match release policy")

    expected_kinds = release_policy.get("required_strong_baseline_kinds")
    if [item.get("baseline_kind") for item in comparisons if isinstance(item, dict)] != expected_kinds:
        failures.append("frontier report baseline kinds do not match release policy")

    statistical_design = release_policy.get("statistical_design")
    if not isinstance(statistical_design, dict):
        return _unique(failures + ["frontier report statistical policy is invalid"])
    expected_metric = statistical_design.get("primary_metric")
    expected_margin = statistical_design.get("minimum_meaningful_effect_ppm") / 1_000_000
    expected_confidence = statistical_design.get("confidence_level_ppm") / 1_000_000
    for comparison in comparisons:
        if not isinstance(comparison, dict):
            continue
        if comparison.get("metric_id") != expected_metric:
            failures.append("frontier comparison metric does not match release policy")
        if comparison.get("meaningful_margin") != expected_margin:
            failures.append("frontier comparison margin does not match release policy")
        if comparison.get("confidence_level") != expected_confidence:
            failures.append("frontier comparison confidence does not match release policy")
    return _unique(failures)


def _authority_topology_failures(report_input: Mapping[str, Any]) -> list[str]:
    roles_by_authority: dict[str, set[str]] = {}
    for gate in report_input["gates"]:
        authority_id = gate.get("authority_id")
        if isinstance(authority_id, str):
            roles_by_authority.setdefault(authority_id, set()).add(
                GATE_AUTHORITY_ROLES[gate["gate_id"]]
            )
    for comparison in report_input["baseline_comparisons"]:
        authority_id = comparison.get("authority_id")
        if isinstance(authority_id, str):
            roles_by_authority.setdefault(authority_id, set()).add(
                "quality_statistics"
            )
    if any(len(roles) > 1 for roles in roles_by_authority.values()):
        return ["frontier report authority roles must be independent"]
    return []


def frontier_gate_context_sha256(
    report_input: Mapping[str, Any], gate: Mapping[str, Any]
) -> str:
    """Build the exact context a gate receipt authority must attest."""

    return sha256_json(
        {
            "schema_version": FRONTIER_CONTRACT_SCHEMA_VERSION,
            "report_id": report_input["report_id"],
            "campaign_id": report_input["campaign_id"],
            "generated_at": report_input["generated_at"],
            "scope_sha256": sha256_json(report_input["scope"]),
            "source_artifacts_sha256": report_input["source_artifacts_sha256"],
            "environment_sha256": report_input["environment_sha256"],
            "gate_id": gate["gate_id"],
            "evidence_kind": gate["evidence_kind"],
            "evidence_sha256": gate["evidence_sha256"],
            "outcome": gate["outcome"],
        }
    )


def frontier_comparison_context_sha256(
    report_input: Mapping[str, Any], comparison: Mapping[str, Any]
) -> str:
    """Build the exact context a baseline-comparison authority must attest."""

    comparison_payload = {
        key: value
        for key, value in comparison.items()
        if key != "authority_receipt_sha256"
    }
    return sha256_json(
        {
            "schema_version": FRONTIER_CONTRACT_SCHEMA_VERSION,
            "report_id": report_input["report_id"],
            "campaign_id": report_input["campaign_id"],
            "generated_at": report_input["generated_at"],
            "scope_sha256": sha256_json(report_input["scope"]),
            "source_artifacts_sha256": report_input["source_artifacts_sha256"],
            "environment_sha256": report_input["environment_sha256"],
            "comparison": comparison_payload,
        }
    )


def _verify_gate_receipts(
    report_input: Mapping[str, Any],
    verifier: FrontierReportAuthorityVerifier | None,
) -> tuple[list[str], list[str]]:
    gates = report_input["gates"]
    populated = [gate for gate in gates if gate["evidence_sha256"] is not None]
    if populated and verifier is None:
        return ["trusted frontier report authority verifier is required"], []
    receipts: list[str] = []
    failures: list[str] = []
    for gate in populated:
        if verifier is None:
            break
        context_sha256 = frontier_gate_context_sha256(report_input, gate)
        try:
            verified = verifier.verify(
                evidence_type="gate",
                evidence_id=gate["gate_id"],
                authority_id=gate["authority_id"],
                context_sha256=context_sha256,
            )
        except Exception:
            verified = None
        if (
            not _valid_sha256(verified)
            or verified != gate["authority_receipt_sha256"]
        ):
            failures.append("frontier gate authority verification failed")
        else:
            receipts.append(verified)
    if len(receipts) != len(set(receipts)):
        failures.append("frontier gate authority receipts must be unique")
    return _unique(failures), receipts


def _verify_comparison_receipts(
    report_input: Mapping[str, Any],
    verifier: FrontierReportAuthorityVerifier | None,
) -> tuple[list[str], list[str]]:
    comparisons = report_input["baseline_comparisons"]
    populated = [
        comparison
        for comparison in comparisons
        if comparison["evidence_sha256"] is not None
    ]
    if populated and verifier is None:
        return ["trusted frontier report authority verifier is required"], []
    failures: list[str] = []
    receipts: list[str] = []
    for comparison in populated:
        if verifier is None:
            break
        try:
            verified = verifier.verify(
                evidence_type="baseline_comparison",
                evidence_id=comparison["baseline_id"],
                authority_id=comparison["authority_id"],
                context_sha256=frontier_comparison_context_sha256(
                    report_input, comparison
                ),
            )
        except Exception:
            verified = None
        if (
            not _valid_sha256(verified)
            or verified != comparison["authority_receipt_sha256"]
        ):
            failures.append("frontier baseline comparison authority verification failed")
        else:
            receipts.append(verified)
    if len(receipts) != len(set(receipts)):
        failures.append("frontier baseline comparison receipts must be unique")
    return _unique(failures), receipts


def _derive_report_outcome(
    report_input: Mapping[str, Any],
    verifier: FrontierReportAuthorityVerifier | None,
) -> tuple[str, list[str]]:
    failures: list[str] = []
    has_failed = False
    has_not_evaluable = False
    has_invalidating_failure = False
    for gate in report_input["gates"]:
        if gate["outcome"] == "failed":
            has_failed = True
            failures.append(f"{gate['gate_id']} gate failed")
            if gate["gate_id"] in INVALIDATING_GATE_IDS:
                has_invalidating_failure = True
        elif gate["outcome"] == "not_evaluable":
            has_not_evaluable = True
            failures.append(f"{gate['gate_id']} gate is not evaluable")

    comparison_not_evaluable = False
    original_superior = False
    all_baselines_superior = True
    for index, comparison in enumerate(report_input["baseline_comparisons"]):
        if comparison["outcome"] == "not_evaluable":
            comparison_not_evaluable = True
            all_baselines_superior = False
            failures.append("a required baseline comparison is not evaluable")
        elif comparison["outcome"] == "not_superior":
            all_baselines_superior = False
            failures.append("a required baseline comparison is not superior")
        elif index == 0:
            original_superior = True

    gate_authority_failures, gate_receipts = _verify_gate_receipts(
        report_input, verifier
    )
    comparison_authority_failures, comparison_receipts = (
        _verify_comparison_receipts(report_input, verifier)
    )
    authority_failures = gate_authority_failures + comparison_authority_failures
    authority_failures.extend(_authority_topology_failures(report_input))
    if len(gate_receipts + comparison_receipts) != len(
        set(gate_receipts + comparison_receipts)
    ):
        authority_failures.append("frontier report authority receipts must be unique")
    failures.extend(authority_failures)
    if (
        has_not_evaluable
        or has_invalidating_failure
        or comparison_not_evaluable
        or authority_failures
    ):
        return "not_evaluable", sorted(set(failures))
    if not original_superior:
        return "not_superior", sorted(set(failures))
    if has_failed or not all_baselines_superior:
        return "verified_improvement", sorted(set(failures))
    return "top_tier_scoped", []


def build_frontier_report(
    report_input: Mapping[str, Any],
    *,
    release_policy: Mapping[str, Any] | None = None,
    authority_verifier: FrontierReportAuthorityVerifier | None,
) -> dict[str, Any]:
    """Derive the only authorized report status and its canonical self-hash."""

    failures = validate_frontier_report_input(report_input)
    failures.extend(
        validate_frontier_report_policy_binding(report_input, release_policy)
    )
    if failures:
        raise ValueError("frontier report input is invalid")
    payload = copy.deepcopy(dict(report_input))
    status, outcome_failures = _derive_report_outcome(payload, authority_verifier)
    report = {
        **payload,
        "status": status,
        "failures": outcome_failures,
    }
    report["report_sha256"] = hash_payload(report, "report_sha256")
    return report


def validate_frontier_report(
    report: Any,
    *,
    release_policy: Mapping[str, Any] | None = None,
    authority_verifier: FrontierReportAuthorityVerifier | None,
) -> list[str]:
    """Rebuild a final report and reject any field, status, or receipt drift."""

    failures = _exact_fields(report, FRONTIER_REPORT_FIELDS, "frontier report")
    if not isinstance(report, dict):
        return failures
    report_input = {
        field: report.get(field) for field in FRONTIER_REPORT_INPUT_FIELDS
    }
    input_failures = validate_frontier_report_input(report_input)
    input_failures.extend(
        validate_frontier_report_policy_binding(report_input, release_policy)
    )
    failures.extend(input_failures)
    if report.get("status") not in FRONTIER_REPORT_STATUSES:
        failures.append("frontier report status is invalid")
    expected_empty = True if report.get("status") == "top_tier_scoped" else False
    if not _valid_failures(report.get("failures"), empty=expected_empty):
        failures.append("frontier report failures are invalid")
    if not input_failures:
        status, outcome_failures = _derive_report_outcome(
            report_input, authority_verifier
        )
        if report.get("status") != status or report.get("failures") != outcome_failures:
            failures.append("frontier report status does not match its verified gates")
    expected_hash = _safe_payload_hash(report, "report_sha256")
    if report.get("report_sha256") != expected_hash:
        failures.append("frontier report hash mismatch")
    return _unique(failures)


def _validate_claim_scope(scope: Any) -> list[str]:
    failures = _exact_fields(scope, CLAIM_SCOPE_FIELDS, "frontier claim scope")
    if not isinstance(scope, dict):
        return failures
    for field in ("target_model_snapshots", "domain_ids"):
        if not _valid_string_array(scope.get(field), identifiers=True):
            failures.append(f"frontier claim scope {field} is invalid")
    for field in (
        "task_distribution_sha256",
        "evaluation_harness_sha256",
        "budget_ceiling_sha256",
    ):
        if not _valid_sha256(scope.get(field)):
            failures.append(f"frontier claim scope {field} is invalid")
    for field in ("evidence_as_of", "expires_at"):
        if not _valid_timestamp(scope.get(field)):
            failures.append(f"frontier claim scope {field} is invalid")
    if all(_valid_timestamp(scope.get(field)) for field in ("evidence_as_of", "expires_at")):
        if _timestamp(scope["expires_at"]) <= _timestamp(scope["evidence_as_of"]):
            failures.append("frontier claim expiry must follow its evidence date")
    return failures


def _valid_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    if isinstance(value, int):
        return abs(value) <= 9_007_199_254_740_991
    return math.isfinite(value)


def _validate_claim_metric(metric: Any) -> list[str]:
    failures = _exact_fields(metric, CLAIM_METRIC_FIELDS, "frontier claim metric")
    if not isinstance(metric, dict):
        return failures
    source_kind = metric.get("source_kind")
    if source_kind not in CLAIM_METRIC_SOURCE_KINDS:
        failures.append("frontier claim metric source kind is invalid")
    if not _valid_identifier(metric.get("source_id")):
        failures.append("frontier claim metric source id is invalid")
    if source_kind == "baseline_comparison":
        if not _valid_identifier(metric.get("metric_id")):
            failures.append("frontier claim metric id is invalid")
        for field in ("point_estimate", "interval_lower", "interval_upper"):
            if not _valid_number(metric.get(field)):
                failures.append(f"frontier claim metric {field} is invalid")
        confidence = metric.get("confidence_level")
        if not _valid_number(confidence) or not 0.5 <= confidence < 1:
            failures.append("frontier claim metric confidence level is invalid")
        values = [
            metric.get("interval_lower"),
            metric.get("point_estimate"),
            metric.get("interval_upper"),
        ]
        if all(_valid_number(value) for value in values) and not (
            values[0] <= values[1] <= values[2]
        ):
            failures.append("frontier claim metric interval is invalid")
    elif source_kind == "gate" and any(
        metric.get(field) is not None
        for field in (
            "metric_id",
            "point_estimate",
            "interval_lower",
            "interval_upper",
            "confidence_level",
        )
    ):
        failures.append("frontier gate evidence metric values must be null")
    if not _valid_sha256(metric.get("evidence_sha256")):
        failures.append("frontier claim metric evidence digest is invalid")
    return failures


def _claim_metric_report_source(
    metric: Mapping[str, Any], report: Mapping[str, Any]
) -> Mapping[str, Any] | None:
    """Resolve a claim metric only when it exactly mirrors one report source."""

    source_kind = metric.get("source_kind")
    source_id = metric.get("source_id")
    if source_kind == "baseline_comparison":
        sources = report.get("baseline_comparisons")
        source_key = "baseline_id"
    elif source_kind == "gate":
        sources = report.get("gates")
        source_key = "gate_id"
    else:
        return None
    if not isinstance(sources, list):
        return None
    matches = [
        source
        for source in sources
        if isinstance(source, dict) and source.get(source_key) == source_id
    ]
    if len(matches) != 1:
        return None
    source = matches[0]
    if source_kind == "baseline_comparison":
        for field in (
            "metric_id",
            "point_estimate",
            "interval_lower",
            "interval_upper",
            "confidence_level",
            "evidence_sha256",
        ):
            if metric.get(field) != source.get(field):
                return None
    elif (
        any(
            metric.get(field) is not None
            for field in (
                "metric_id",
                "point_estimate",
                "interval_lower",
                "interval_upper",
                "confidence_level",
            )
        )
        or metric.get("evidence_sha256") != source.get("evidence_sha256")
    ):
        return None
    return source


def frontier_claim_evidence_summary_sha256(
    report: Mapping[str, Any], claim: Mapping[str, Any]
) -> str:
    """Hash the exact report sources selected by a public claim's metrics."""

    if not isinstance(report, Mapping) or not isinstance(claim, Mapping):
        raise ValueError("frontier claim evidence summary inputs must be objects")
    metrics = claim.get("metrics")
    if not isinstance(metrics, list) or not metrics:
        raise ValueError("frontier claim evidence summary requires metrics")
    bindings: list[dict[str, Any]] = []
    source_keys: set[tuple[str, str]] = set()
    for metric in metrics:
        if not isinstance(metric, dict) or _validate_claim_metric(metric):
            raise ValueError("frontier claim metric is invalid")
        source_kind = metric["source_kind"]
        source_id = metric["source_id"]
        source_key = (source_kind, source_id)
        if source_key in source_keys:
            raise ValueError("frontier claim metric sources must be unique")
        source = _claim_metric_report_source(metric, report)
        if source is None:
            raise ValueError("frontier claim metric does not match its report source")
        source_keys.add(source_key)
        bindings.append(
            {
                "source_kind": source_kind,
                "source_id": source_id,
                "claim_metric": copy.deepcopy(metric),
                "report_source": copy.deepcopy(dict(source)),
            }
        )
    bindings.sort(key=lambda item: (item["source_kind"], item["source_id"]))
    return sha256_json(
        {
            "schema_version": FRONTIER_CONTRACT_SCHEMA_VERSION,
            "report_id": report.get("report_id"),
            "report_sha256": report.get("report_sha256"),
            "bindings": bindings,
        }
    )


def frontier_claim_context_sha256(
    inventory: Mapping[str, Any], claim: Mapping[str, Any]
) -> str:
    """Build the exact claim-auditor receipt context."""

    claim_payload = {
        key: value
        for key, value in claim.items()
        if key not in {"authority_receipt_sha256", "claim_sha256"}
    }
    return sha256_json(
        {
            "schema_version": FRONTIER_CONTRACT_SCHEMA_VERSION,
            "inventory_id": inventory["inventory_id"],
            "report_id": inventory["report_id"],
            "report_sha256": inventory["report_sha256"],
            "claim": claim_payload,
        }
    )


def _claim_scope_matches_report(
    claim_scope: Mapping[str, Any], report_scope: Mapping[str, Any]
) -> bool:
    if not set(claim_scope["target_model_snapshots"]).issubset(
        report_scope["target_model_snapshots"]
    ):
        return False
    if not set(claim_scope["domain_ids"]).issubset(report_scope["domain_ids"]):
        return False
    for field in (
        "task_distribution_sha256",
        "evaluation_harness_sha256",
        "budget_ceiling_sha256",
        "evidence_as_of",
    ):
        if claim_scope[field] != report_scope[field]:
            return False
    return _timestamp(claim_scope["expires_at"]) <= _timestamp(
        report_scope["expires_at"]
    )


def validate_frontier_claim_inventory(
    inventory: Any,
    *,
    report: Any,
    release_policy: Mapping[str, Any] | None = None,
    report_authority_verifier: FrontierReportAuthorityVerifier | None,
    claim_authority_verifier: FrontierClaimAuthorityVerifier | None,
    as_of: str | None,
) -> list[str]:
    """Validate every public statement against one authoritative report."""

    failures = _exact_fields(
        inventory, CLAIM_INVENTORY_FIELDS, "frontier claim inventory"
    )
    if not isinstance(inventory, dict):
        return failures
    if inventory.get("schema_version") != FRONTIER_CONTRACT_SCHEMA_VERSION:
        failures.append("unsupported frontier claim inventory schema")
    for field in ("inventory_id", "report_id"):
        if not _valid_identifier(inventory.get(field)):
            failures.append(f"frontier claim inventory {field} is invalid")
    if not _valid_sha256(inventory.get("report_sha256")):
        failures.append("frontier claim inventory report digest is invalid")
    if not _valid_timestamp(as_of):
        failures.append("trusted frontier claim validation timestamp is required")

    report_failures = validate_frontier_report(
        report,
        release_policy=release_policy,
        authority_verifier=report_authority_verifier,
    )
    if report_failures:
        failures.append("frontier claim inventory report authority is invalid")
    report_is_object = isinstance(report, dict)
    if report_is_object and (
        inventory.get("report_id") != report.get("report_id")
        or inventory.get("report_sha256") != report.get("report_sha256")
    ):
        failures.append("frontier claim inventory report binding is invalid")
    if (
        report_is_object
        and isinstance(report.get("scope"), dict)
        and _valid_timestamp(as_of)
        and _valid_timestamp(report["scope"].get("evidence_as_of"))
        and _valid_timestamp(report["scope"].get("expires_at"))
        and not (
            _timestamp(report["scope"]["evidence_as_of"])
            <= _timestamp(as_of)
            < _timestamp(report["scope"]["expires_at"])
        )
    ):
        failures.append("frontier claim inventory is outside its evidence window")

    claims = inventory.get("claims")
    claim_ids: list[str] = []
    receipts: list[str] = []
    if not isinstance(claims, list) or not claims:
        failures.append("frontier claim inventory claims must be non-empty")
    else:
        for claim in claims:
            claim_failures = _exact_fields(claim, CLAIM_FIELDS, "frontier claim")
            if not isinstance(claim, dict):
                failures.extend(claim_failures)
                continue
            if not _valid_identifier(claim.get("claim_id")):
                claim_failures.append("frontier claim id is invalid")
            else:
                claim_ids.append(claim["claim_id"])
            if claim.get("claim_status") not in FRONTIER_REPORT_STATUSES:
                claim_failures.append("frontier claim status is invalid")
            if report_is_object and claim.get("claim_status") != report.get("status"):
                claim_failures.append("frontier claim status exceeds its report")
            statement = claim.get("statement")
            if (
                not isinstance(statement, str)
                or not statement.strip()
                or len(statement) > 4096
            ):
                claim_failures.append("frontier claim statement is invalid")
            claim_failures.extend(_validate_claim_scope(claim.get("scope")))
            scope = claim.get("scope")
            if isinstance(scope, dict):
                if claim.get("scope_sha256") != sha256_json(scope):
                    claim_failures.append("frontier claim scope hash mismatch")
                if (
                    report_is_object
                    and isinstance(report.get("scope"), dict)
                    and not _validate_claim_scope(scope)
                    and not _validate_report_scope(report["scope"])
                    and not _claim_scope_matches_report(scope, report["scope"])
                ):
                    claim_failures.append("frontier claim scope exceeds its report")
            metrics = claim.get("metrics")
            metric_source_keys: list[tuple[str, str]] = []
            if not isinstance(metrics, list) or not metrics:
                claim_failures.append("frontier claim metrics must be non-empty")
            else:
                for metric in metrics:
                    metric_failures = _validate_claim_metric(metric)
                    claim_failures.extend(metric_failures)
                    if isinstance(metric, dict) and all(
                        isinstance(metric.get(field), str)
                        for field in ("source_kind", "source_id")
                    ):
                        metric_source_keys.append(
                            (metric["source_kind"], metric["source_id"])
                        )
                    if (
                        not metric_failures
                        and report_is_object
                        and _claim_metric_report_source(metric, report) is None
                    ):
                        claim_failures.append(
                            "frontier claim metric does not match its report source"
                        )
                if len(metric_source_keys) != len(set(metric_source_keys)):
                    claim_failures.append(
                        "frontier claim metric sources must be unique"
                    )
            if not _valid_sha256(claim.get("evidence_summary_sha256")):
                claim_failures.append("frontier claim evidence summary is invalid")
            elif report_is_object and isinstance(metrics, list) and metrics:
                try:
                    expected_evidence_summary = (
                        frontier_claim_evidence_summary_sha256(report, claim)
                    )
                except (OverflowError, RecursionError, TypeError, ValueError):
                    claim_failures.append(
                        "frontier claim evidence summary could not be derived"
                    )
                else:
                    if (
                        claim.get("evidence_summary_sha256")
                        != expected_evidence_summary
                    ):
                        claim_failures.append(
                            "frontier claim evidence summary does not match report bindings"
                        )
            if not _valid_string_array(claim.get("limitations")):
                claim_failures.append("frontier claim limitations are invalid")
            if not _valid_timestamp(claim.get("expires_at")):
                claim_failures.append("frontier claim expiry is invalid")
            elif isinstance(scope, dict) and claim.get("expires_at") != scope.get(
                "expires_at"
            ):
                claim_failures.append("frontier claim expiry does not match its scope")
            if (
                _valid_timestamp(as_of)
                and _valid_timestamp(claim.get("expires_at"))
                and _timestamp(as_of) >= _timestamp(claim["expires_at"])
            ):
                claim_failures.append("frontier claim has expired")
            if not _valid_identifier(claim.get("authority_id")):
                claim_failures.append("frontier claim authority id is invalid")
            elif report_is_object:
                report_authority_items: list[Any] = []
                for field in ("gates", "baseline_comparisons"):
                    items = report.get(field)
                    if isinstance(items, list):
                        report_authority_items.extend(items)
                report_authority_ids = {
                    item.get("authority_id")
                    for item in report_authority_items
                    if isinstance(item, dict)
                    and isinstance(item.get("authority_id"), str)
                }
                if claim.get("authority_id") in report_authority_ids:
                    claim_failures.append(
                        "frontier claim authority must be independent of report authorities"
                    )
            if not _valid_sha256(claim.get("authority_receipt_sha256")):
                claim_failures.append("frontier claim authority receipt is invalid")
            expected_claim_hash = _safe_payload_hash(claim, "claim_sha256")
            if claim.get("claim_sha256") != expected_claim_hash:
                claim_failures.append("frontier claim hash mismatch")

            if not claim_failures:
                if claim_authority_verifier is None:
                    claim_failures.append(
                        "trusted frontier claim authority verifier is required"
                    )
                else:
                    try:
                        verified = claim_authority_verifier.verify(
                            claim_id=claim["claim_id"],
                            authority_id=claim["authority_id"],
                            context_sha256=frontier_claim_context_sha256(
                                inventory, claim
                            ),
                        )
                    except Exception:
                        verified = None
                    if (
                        not _valid_sha256(verified)
                        or verified != claim["authority_receipt_sha256"]
                    ):
                        claim_failures.append(
                            "frontier claim authority verification failed"
                        )
                    else:
                        receipts.append(verified)
            failures.extend(claim_failures)
    if len(claim_ids) != len(set(claim_ids)):
        failures.append("frontier claim ids must be unique")
    if len(receipts) != len(set(receipts)):
        failures.append("frontier claim authority receipts must be unique")
    expected_inventory_hash = _safe_payload_hash(inventory, "inventory_sha256")
    if inventory.get("inventory_sha256") != expected_inventory_hash:
        failures.append("frontier claim inventory hash mismatch")
    return _unique(failures)
