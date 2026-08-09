"""Strict reproduction plans and deterministic offline frontier replay."""

from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Mapping, Protocol

from .frontier_contracts import (
    FRONTIER_CONTRACT_SCHEMA_VERSION,
    SHA256_RE,
    load_frontier_campaign_bundle,
    validate_frontier_campaign_bundle,
    validate_frontier_campaign_plan,
    validate_frontier_release_policy,
    validate_frontier_source_commitment,
)
from .frontier_host import (
    validate_frontier_execution_bundle,
)
from .frontier_io import (
    FrontierArtifactIOError,
    canonical_authority_bytes,
    load_authority_artifact,
)
from .frontier_preflight import validate_frontier_preflight_report
from .frontier_reporting import (
    FRONTIER_REPORT_INPUT_FIELDS,
    FrontierClaimAuthorityVerifier,
    FrontierReportAuthorityVerifier,
    build_frontier_report,
    validate_frontier_claim_inventory,
    validate_frontier_report,
)
from .hashing import hash_payload, sha256_json


REPRODUCTION_PLAN_FIELDS = frozenset(
    {
        "schema_version",
        "plan_id",
        "campaign_id",
        "report_id",
        "reproduction_group_id",
        "required_operator_count",
        "report_input_path",
        "expected_report_path",
        "expected_report_file_sha256",
        "expected_report_sha256",
        "expected_report_canonical_sha256",
        "claim_inventory_path",
        "claim_inventory_file_sha256",
        "claim_inventory_sha256",
        "artifacts",
        "source_artifacts_sha256",
        "input_commitments_sha256",
        "environment",
        "environment_sha256",
        "reproduction_operators",
        "operator",
        "plan_sha256",
    }
)
ARTIFACT_FIELDS = frozenset(
    {"artifact_id", "artifact_kind", "path", "content_sha256"}
)
ENVIRONMENT_FIELDS = frozenset(
    {
        "os_name",
        "architecture",
        "python_implementation",
        "python_version",
        "package_version",
        "package_sha256",
        "analysis_implementation_sha256",
        "dependency_lock_sha256",
        "container_image_digest",
        "replay_command_sha256",
    }
)
OPERATOR_FIELDS = frozenset(
    {"operator_id", "machine_id", "attestation_authority_id", "attestation_id"}
)
ARTIFACT_KINDS = frozenset(
    {
        "frontier_report_input",
        "campaign_plan",
        "release_policy",
        "source_commitment",
        "frontier_preflight_report",
        "execution_bundle",
        "judgment_manifest",
        "human_gold",
        "software_r05",
        "visual_r06",
        "statistics",
        "safety",
        "robustness",
        "efficiency",
        "independence",
        "external_reproduction",
        "defect_register",
        "stable_readiness_report",
    }
)
TOP_TIER_REQUIRED_ARTIFACT_KINDS = ARTIFACT_KINDS

EVIDENCE_ENVELOPE_KINDS = frozenset(
    {
        "judgment_manifest",
        "human_gold",
        "software_r05",
        "visual_r06",
        "statistics",
        "safety",
        "robustness",
        "efficiency",
        "independence",
        "external_reproduction",
        "defect_register",
        "stable_readiness_report",
    }
)
EVIDENCE_ENVELOPE_FIELDS = frozenset(
    {
        "schema_version",
        "artifact_kind",
        "campaign_sha256",
        "policy_sha256",
        "commitment_sha256",
        "report_id",
        "generated_at",
        "outcome",
        "failures",
        "payload",
        "payload_sha256",
        "semantic_authority_id",
        "semantic_receipt_sha256",
        "evidence_sha256",
    }
)
EVIDENCE_PAYLOAD_FIELDS = frozenset(
    {
        "subject_ids",
        "evaluated_case_count",
        "passed_case_count",
        "failed_case_count",
        "metric_ids",
        "record_count",
        "records_sha256",
        "source_artifact_sha256s",
    }
)

REPLAY_REPORT_FIELDS = frozenset(
    {
        "schema_version",
        "replay_id",
        "plan_id",
        "campaign_id",
        "report_id",
        "passed",
        "failures",
        "plan_sha256",
        "input_commitments_sha256",
        "source_artifacts_sha256",
        "expected_report_file_sha256",
        "actual_report_file_sha256",
        "expected_report_sha256",
        "actual_report_sha256",
        "expected_report_canonical_sha256",
        "actual_report_canonical_sha256",
        "expected_claim_inventory_file_sha256",
        "actual_claim_inventory_file_sha256",
        "expected_claim_inventory_sha256",
        "actual_claim_inventory_sha256",
        "environment_sha256",
        "replay_command_sha256",
        "exit_code",
        "operator_id",
        "machine_id",
        "claim_validation_timestamp",
        "operator_attestation_receipt_sha256",
        "replay_sha256",
    }
)
REPRODUCTION_SET_FIELDS = frozenset(
    {
        "schema_version",
        "reproduction_set_id",
        "reproduction_group_id",
        "campaign_id",
        "report_id",
        "required_operator_count",
        "plan_sha256s",
        "replay_sha256s",
        "operator_ids",
        "machine_ids",
        "attestation_authority_ids",
        "attestation_receipt_sha256s",
        "expected_report_sha256",
        "actual_report_sha256",
        "claim_validation_timestamp",
        "passed",
        "failures",
        "reproduction_set_sha256",
    }
)

UTC_TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$"
)


class FrontierReplayAttestationVerifier(Protocol):
    """Trusted operator/machine identity boundary for one offline replay."""

    def verify(
        self,
        *,
        operator_id: str,
        machine_id: str,
        authority_id: str,
        attestation_id: str,
        context_sha256: str,
    ) -> str | None:
        """Return the canonical attestation receipt, or ``None``."""


class FrontierEvidenceSemanticVerifier(Protocol):
    """Trusted offline boundary for kind-specific evidence semantics."""

    def verify(
        self,
        *,
        artifact_kind: str,
        authority_id: str,
        context_sha256: str,
    ) -> str | None:
        """Return the detached semantic receipt digest, or ``None``."""


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
    return (
        isinstance(value, str)
        and 0 < len(value) <= 256
        and not any(character.isspace() for character in value)
    )


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


def _safe_payload_hash(value: Any, field: str) -> str | None:
    try:
        return hash_payload(value, field) if isinstance(value, dict) else None
    except (OverflowError, RecursionError, TypeError, ValueError):
        return None


def _portable_path(value: Any) -> bool:
    if (
        not isinstance(value, str)
        or not value
        or value.strip() != value
        or len(value) > 1024
        or "\\" in value
    ):
        return False
    parts = value.split("/")
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    return (
        all(part not in {"", ".", ".."} for part in parts)
        and not posix.is_absolute()
        and not windows.is_absolute()
        and not windows.drive
    )


def _content_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def validate_frontier_evidence_envelope(envelope: Any) -> list[str]:
    """Validate a typed evidence summary without trusting its payload digest."""

    failures = _exact_fields(
        envelope, EVIDENCE_ENVELOPE_FIELDS, "frontier evidence envelope"
    )
    if not isinstance(envelope, dict):
        return failures
    if envelope.get("schema_version") != FRONTIER_CONTRACT_SCHEMA_VERSION:
        failures.append("unsupported frontier evidence envelope schema")
    if envelope.get("artifact_kind") not in EVIDENCE_ENVELOPE_KINDS:
        failures.append("frontier evidence envelope kind is invalid")
    for field in (
        "campaign_sha256",
        "policy_sha256",
        "commitment_sha256",
        "payload_sha256",
        "semantic_receipt_sha256",
        "evidence_sha256",
    ):
        if not _valid_sha256(envelope.get(field)):
            failures.append(f"frontier evidence envelope {field} is invalid")
    if not _valid_identifier(envelope.get("report_id")):
        failures.append("frontier evidence envelope report id is invalid")
    if not _valid_identifier(envelope.get("semantic_authority_id")):
        failures.append("frontier evidence semantic authority is invalid")
    if not _valid_timestamp(envelope.get("generated_at")):
        failures.append("frontier evidence envelope timestamp is invalid")
    outcome = envelope.get("outcome")
    if outcome not in {"passed", "failed", "not_evaluable"}:
        failures.append("frontier evidence envelope outcome is invalid")
    evidence_failures = envelope.get("failures")
    if (
        not isinstance(evidence_failures, list)
        or any(not isinstance(item, str) or not item for item in evidence_failures)
        or evidence_failures != sorted(set(evidence_failures))
        or (outcome == "passed" and evidence_failures)
        or (outcome != "passed" and not evidence_failures)
    ):
        failures.append("frontier evidence envelope failures are invalid")
    if envelope.get("evidence_sha256") != _safe_payload_hash(
        envelope, "evidence_sha256"
    ):
        failures.append("frontier evidence envelope hash mismatch")
    payload = envelope.get("payload")
    failures.extend(
        _exact_fields(payload, EVIDENCE_PAYLOAD_FIELDS, "frontier evidence payload")
    )
    if isinstance(payload, dict):
        for field in ("subject_ids", "metric_ids"):
            values = payload.get(field)
            if (
                not isinstance(values, list)
                or any(not _valid_identifier(item) for item in values)
                or len(values) != len(set(values))
            ):
                failures.append(f"frontier evidence payload {field} is invalid")
        for field in (
            "evaluated_case_count",
            "passed_case_count",
            "failed_case_count",
            "record_count",
        ):
            value = payload.get(field)
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 0 <= value <= 9_007_199_254_740_991
            ):
                failures.append(f"frontier evidence payload {field} is invalid")
        if not _valid_sha256(payload.get("records_sha256")):
            failures.append("frontier evidence payload records digest is invalid")
        sources = payload.get("source_artifact_sha256s")
        if (
            not isinstance(sources, list)
            or not sources
            or any(not _valid_sha256(item) for item in sources)
            or len(sources) != len(set(sources))
        ):
            failures.append("frontier evidence payload source digests are invalid")
        counts = [
            payload.get("evaluated_case_count"),
            payload.get("passed_case_count"),
            payload.get("failed_case_count"),
            payload.get("record_count"),
        ]
        if all(isinstance(value, int) and not isinstance(value, bool) for value in counts):
            if counts[1] + counts[2] != counts[0] or counts[3] < counts[0]:
                failures.append("frontier evidence payload counts are inconsistent")
            if outcome == "passed" and (counts[2] != 0 or counts[1] != counts[0]):
                failures.append("passing frontier evidence contains failed cases")
        try:
            payload_sha256 = sha256_json(payload)
        except (OverflowError, RecursionError, TypeError, ValueError):
            payload_sha256 = None
        if envelope.get("payload_sha256") != payload_sha256:
            failures.append("frontier evidence payload hash mismatch")
    return _unique(failures)


def frontier_evidence_semantic_context_sha256(
    envelope: Mapping[str, Any],
) -> str:
    """Bind a detached semantic receipt to the complete typed payload."""

    return sha256_json(
        {
            key: value
            for key, value in envelope.items()
            if key not in {"semantic_receipt_sha256", "evidence_sha256"}
        }
    )


def frontier_input_commitments_sha256(artifacts: list[dict[str, Any]]) -> str:
    """Bind the frozen, ordered artifact descriptors, including their paths."""

    return sha256_json(artifacts)


def frontier_source_artifacts_sha256(artifacts: list[dict[str, Any]]) -> str:
    """Bind portable evidence identities without creating a report-input cycle."""

    return sha256_json(
        [
            {
                "artifact_id": artifact["artifact_id"],
                "artifact_kind": artifact["artifact_kind"],
                "content_sha256": artifact["content_sha256"],
            }
            for artifact in artifacts
            if artifact["artifact_kind"] != "frontier_report_input"
        ]
    )


def validate_frontier_reproduction_environment(environment: Any) -> list[str]:
    failures = _exact_fields(
        environment, ENVIRONMENT_FIELDS, "frontier reproduction environment"
    )
    if not isinstance(environment, dict):
        return failures
    for field in (
        "os_name",
        "architecture",
        "python_implementation",
        "python_version",
        "package_version",
    ):
        if not _valid_identifier(environment.get(field)):
            failures.append(f"frontier reproduction environment {field} is invalid")
    for field in (
        "package_sha256",
        "analysis_implementation_sha256",
        "dependency_lock_sha256",
        "replay_command_sha256",
    ):
        if not _valid_sha256(environment.get(field)):
            failures.append(f"frontier reproduction environment {field} is invalid")
    image = environment.get("container_image_digest")
    if image is not None and (
        not isinstance(image, str)
        or len(image) != 71
        or not image.startswith("sha256:")
        or not _valid_sha256(image[7:])
    ):
        failures.append("frontier reproduction container image digest is invalid")
    return failures


def validate_frontier_reproduction_plan(plan: Any) -> list[str]:
    """Validate the frozen replay inputs without opening any artifact."""

    failures = _exact_fields(
        plan, REPRODUCTION_PLAN_FIELDS, "frontier reproduction plan"
    )
    if not isinstance(plan, dict):
        return failures
    if plan.get("schema_version") != FRONTIER_CONTRACT_SCHEMA_VERSION:
        failures.append("unsupported frontier reproduction plan schema")
    for field in (
        "plan_id",
        "campaign_id",
        "report_id",
        "reproduction_group_id",
    ):
        if not _valid_identifier(plan.get(field)):
            failures.append(f"frontier reproduction plan {field} is invalid")
    if plan.get("required_operator_count") != 3:
        failures.append("frontier reproduction plan requires three operators")
    for field in (
        "report_input_path",
        "expected_report_path",
        "claim_inventory_path",
    ):
        if not _portable_path(plan.get(field)):
            failures.append(f"frontier reproduction plan {field} is invalid")
    paths = [
        plan.get("report_input_path"),
        plan.get("expected_report_path"),
        plan.get("claim_inventory_path"),
    ]
    if all(isinstance(path, str) for path in paths) and len(paths) != len(set(paths)):
        failures.append("frontier reproduction report paths must be distinct")
    for field in (
        "expected_report_file_sha256",
        "expected_report_sha256",
        "expected_report_canonical_sha256",
        "claim_inventory_file_sha256",
        "claim_inventory_sha256",
        "source_artifacts_sha256",
        "input_commitments_sha256",
        "environment_sha256",
        "plan_sha256",
    ):
        if not _valid_sha256(plan.get(field)):
            failures.append(f"frontier reproduction plan {field} is invalid")

    artifacts = plan.get("artifacts")
    artifact_ids: list[str] = []
    artifact_paths: list[str] = []
    artifact_kinds: list[str] = []
    report_input_paths: list[str] = []
    if not isinstance(artifacts, list) or not artifacts:
        failures.append("frontier reproduction artifacts must be non-empty")
    else:
        for artifact in artifacts:
            failures.extend(
                _exact_fields(artifact, ARTIFACT_FIELDS, "frontier replay artifact")
            )
            if not isinstance(artifact, dict):
                continue
            if not _valid_identifier(artifact.get("artifact_id")):
                failures.append("frontier replay artifact id is invalid")
            else:
                artifact_ids.append(artifact["artifact_id"])
            kind = artifact.get("artifact_kind")
            if kind not in ARTIFACT_KINDS:
                failures.append("frontier replay artifact kind is invalid")
            else:
                artifact_kinds.append(kind)
            if not _portable_path(artifact.get("path")):
                failures.append("frontier replay artifact path is invalid")
            else:
                artifact_paths.append(artifact["path"])
                if kind == "frontier_report_input":
                    report_input_paths.append(artifact["path"])
            if not _valid_sha256(artifact.get("content_sha256")):
                failures.append("frontier replay artifact content digest is invalid")
        if len(artifact_ids) != len(set(artifact_ids)):
            failures.append("frontier replay artifact ids must be unique")
        if len(artifact_paths) != len(set(artifact_paths)):
            failures.append("frontier replay artifact paths must be unique")
        if len(artifact_kinds) != len(set(artifact_kinds)):
            failures.append("frontier replay artifact kinds must be unique")
        if report_input_paths != [plan.get("report_input_path")]:
            failures.append("frontier report input artifact binding is invalid")
        if plan.get("expected_report_path") in artifact_paths or plan.get(
            "claim_inventory_path"
        ) in artifact_paths:
            failures.append("frontier outputs cannot be replay source artifacts")
        try:
            if plan.get("input_commitments_sha256") != (
                frontier_input_commitments_sha256(artifacts)
            ):
                failures.append("frontier replay input commitments hash mismatch")
            if plan.get("source_artifacts_sha256") != (
                frontier_source_artifacts_sha256(artifacts)
            ):
                failures.append("frontier replay source artifacts hash mismatch")
        except (KeyError, OverflowError, RecursionError, TypeError, ValueError):
            failures.append("frontier replay artifact commitments are invalid")

    environment = plan.get("environment")
    failures.extend(validate_frontier_reproduction_environment(environment))
    if isinstance(environment, dict):
        try:
            if plan.get("environment_sha256") != sha256_json(environment):
                failures.append("frontier reproduction environment hash mismatch")
        except (OverflowError, RecursionError, TypeError, ValueError):
            failures.append("frontier reproduction environment hash mismatch")

    operator = plan.get("operator")
    failures.extend(_exact_fields(operator, OPERATOR_FIELDS, "reproduction operator"))
    if isinstance(operator, dict):
        for field in OPERATOR_FIELDS:
            if not _valid_identifier(operator.get(field)):
                failures.append(f"reproduction operator {field} is invalid")

    reproduction_operators = plan.get("reproduction_operators")
    operator_ids: list[str] = []
    machine_ids: list[str] = []
    attestation_ids: list[str] = []
    attestation_authority_ids: list[str] = []
    if not isinstance(reproduction_operators, list) or len(reproduction_operators) != 3:
        failures.append("frontier reproduction plan requires three operator commitments")
    else:
        for peer in reproduction_operators:
            failures.extend(
                _exact_fields(peer, OPERATOR_FIELDS, "reproduction operator commitment")
            )
            if not isinstance(peer, dict):
                continue
            for field in OPERATOR_FIELDS:
                if not _valid_identifier(peer.get(field)):
                    failures.append(
                        f"reproduction operator commitment {field} is invalid"
                    )
            if isinstance(peer.get("operator_id"), str):
                operator_ids.append(peer["operator_id"])
            if isinstance(peer.get("machine_id"), str):
                machine_ids.append(peer["machine_id"])
            if isinstance(peer.get("attestation_id"), str):
                attestation_ids.append(peer["attestation_id"])
            if isinstance(peer.get("attestation_authority_id"), str):
                attestation_authority_ids.append(peer["attestation_authority_id"])
        if len(operator_ids) != len(set(operator_ids)):
            failures.append("reproduction operator commitments must be independent")
        if len(machine_ids) != len(set(machine_ids)):
            failures.append("reproduction machine commitments must be independent")
        if len(attestation_ids) != len(set(attestation_ids)):
            failures.append("reproduction attestation commitments must be unique")
        if len(attestation_authority_ids) != len(set(attestation_authority_ids)):
            failures.append("reproduction attestation authorities must be independent")
        if isinstance(operator, dict) and operator not in reproduction_operators:
            failures.append("active reproduction operator is not in the frozen group")

    expected_plan_hash = _safe_payload_hash(plan, "plan_sha256")
    if plan.get("plan_sha256") != expected_plan_hash:
        failures.append("frontier reproduction plan hash mismatch")
    return _unique(failures)


def _plan_relative_path(root: Path, path: Path) -> str:
    """Return a lexical relative path for the hardened authority loader."""

    try:
        candidate = Path(os.fspath(path))
    except (TypeError, ValueError):
        raise ValueError("reproduction plan path is invalid") from None
    if candidate.is_absolute():
        # Windows may present the same directory through a long name and an
        # 8.3 alias.  Locate the lexical ancestor whose resolved identity is
        # the trust root, but preserve every child component so the hardened
        # loader can still reject a symlink/reparse point below that root.
        for ancestor in (candidate, *candidate.parents):
            try:
                if ancestor.resolve(strict=True) == root:
                    candidate = candidate.relative_to(ancestor)
                    break
            except (OSError, RuntimeError, ValueError):
                continue
        else:
            raise ValueError("reproduction plan escapes the reproduction root")
    return candidate.as_posix()


def frontier_replay_context_sha256(report: Mapping[str, Any]) -> str:
    """Build the detached identity attestation context for a passing replay."""

    fields = (
        "schema_version",
        "replay_id",
        "plan_id",
        "campaign_id",
        "report_id",
        "plan_sha256",
        "input_commitments_sha256",
        "source_artifacts_sha256",
        "expected_report_file_sha256",
        "actual_report_file_sha256",
        "expected_report_sha256",
        "actual_report_sha256",
        "expected_report_canonical_sha256",
        "actual_report_canonical_sha256",
        "expected_claim_inventory_file_sha256",
        "actual_claim_inventory_file_sha256",
        "expected_claim_inventory_sha256",
        "actual_claim_inventory_sha256",
        "environment_sha256",
        "replay_command_sha256",
        "exit_code",
        "operator_id",
        "machine_id",
        "claim_validation_timestamp",
    )
    return sha256_json({field: report[field] for field in fields})


def _replay_report(
    *,
    replay_id: str,
    plan: Mapping[str, Any] | None,
    failures: list[str],
    actuals: Mapping[str, str | None],
    receipt: str | None,
    claim_validation_timestamp: str | None = None,
) -> dict[str, Any]:
    safe_plan = plan if isinstance(plan, dict) else {}
    normalized_failures = sorted(set(failures))
    passed = not normalized_failures and receipt is not None
    if not passed and not normalized_failures:
        normalized_failures = ["frontier replay attestation could not be verified"]
    operator = safe_plan.get("operator")
    if not isinstance(operator, dict):
        operator = {}
    environment = safe_plan.get("environment")
    if not isinstance(environment, dict):
        environment = {}

    def safe_identifier(value: Any, fallback: str) -> str:
        return value if _valid_identifier(value) else fallback

    def safe_sha256(value: Any) -> str | None:
        return value if _valid_sha256(value) else None

    report: dict[str, Any] = {
        "schema_version": FRONTIER_CONTRACT_SCHEMA_VERSION,
        "replay_id": replay_id if _valid_identifier(replay_id) else "invalid-replay",
        "plan_id": safe_identifier(safe_plan.get("plan_id"), "unavailable-plan"),
        "campaign_id": safe_identifier(
            safe_plan.get("campaign_id"), "unavailable-campaign"
        ),
        "report_id": safe_identifier(
            safe_plan.get("report_id"), "unavailable-report"
        ),
        "passed": passed,
        "failures": normalized_failures,
        "plan_sha256": safe_sha256(safe_plan.get("plan_sha256")),
        "input_commitments_sha256": safe_sha256(
            safe_plan.get("input_commitments_sha256")
        ),
        "source_artifacts_sha256": safe_sha256(
            safe_plan.get("source_artifacts_sha256")
        ),
        "expected_report_file_sha256": safe_sha256(
            safe_plan.get("expected_report_file_sha256")
        ),
        "actual_report_file_sha256": actuals.get("report_file_sha256"),
        "expected_report_sha256": safe_sha256(
            safe_plan.get("expected_report_sha256")
        ),
        "actual_report_sha256": actuals.get("report_sha256"),
        "expected_report_canonical_sha256": safe_sha256(
            safe_plan.get("expected_report_canonical_sha256")
        ),
        "actual_report_canonical_sha256": actuals.get(
            "report_canonical_sha256"
        ),
        "expected_claim_inventory_file_sha256": safe_sha256(
            safe_plan.get("claim_inventory_file_sha256")
        ),
        "actual_claim_inventory_file_sha256": actuals.get(
            "claim_inventory_file_sha256"
        ),
        "expected_claim_inventory_sha256": safe_sha256(
            safe_plan.get("claim_inventory_sha256")
        ),
        "actual_claim_inventory_sha256": actuals.get("claim_inventory_sha256"),
        "environment_sha256": safe_sha256(safe_plan.get("environment_sha256")),
        "replay_command_sha256": safe_sha256(
            environment.get("replay_command_sha256")
        ),
        "exit_code": 0 if passed else 1,
        "operator_id": safe_identifier(
            operator.get("operator_id"), "unavailable-operator"
        ),
        "machine_id": safe_identifier(
            operator.get("machine_id"), "unavailable-machine"
        ),
        "claim_validation_timestamp": (
            claim_validation_timestamp
            if _valid_timestamp(claim_validation_timestamp)
            else "1970-01-01T00:00:00Z"
        ),
        "operator_attestation_receipt_sha256": receipt if passed else None,
    }
    report["replay_sha256"] = hash_payload(report, "replay_sha256")
    return report


def _load_canonical_object(
    root: Path,
    relative_path: str,
    *,
    label: str,
) -> tuple[dict[str, Any], bytes, str]:
    try:
        loaded = load_authority_artifact(root, relative_path)
    except FrontierArtifactIOError:
        raise ValueError(f"{label} is not a safe canonical authority artifact") from None
    return loaded.value, loaded.canonical_bytes, loaded.content_sha256


def _top_tier_artifact_failures(
    report: Mapping[str, Any],
    artifacts: list[dict[str, Any]],
    artifact_values: Mapping[str, dict[str, Any]],
    *,
    root: Path,
    evidence_semantic_verifier: FrontierEvidenceSemanticVerifier | None,
) -> list[str]:
    failures: list[str] = []
    if report.get("status") != "top_tier_scoped":
        return failures
    kinds = {artifact["artifact_kind"] for artifact in artifacts}
    if kinds != TOP_TIER_REQUIRED_ARTIFACT_KINDS:
        failures.append("top-tier replay source set is incomplete")
    by_kind: dict[str, set[str]] = {}
    for artifact in artifacts:
        by_kind.setdefault(artifact["artifact_kind"], set()).add(
            artifact["content_sha256"]
        )
    for gate in report["gates"]:
        if gate["evidence_sha256"] not in by_kind.get(gate["evidence_kind"], set()):
            failures.append("frontier gate evidence is not bound to a typed source")
    statistics = by_kind.get("statistics", set())
    for comparison in report["baseline_comparisons"]:
        if comparison["evidence_sha256"] not in statistics:
            failures.append(
                "frontier baseline comparison is not bound to statistics evidence"
            )
    preflight = artifact_values.get("frontier_preflight_report")
    if (
        not isinstance(preflight, dict)
        or validate_frontier_preflight_report(preflight)
        or preflight.get("passed") is not True
        or preflight.get("preflight_sha256")
        != report["scope"]["preflight_report_sha256"]
    ):
        failures.append("frontier preflight evidence is invalid")

    campaign_plan = artifact_values.get("campaign_plan")
    release_policy = artifact_values.get("release_policy")
    source_commitment = artifact_values.get("source_commitment")
    scope = report.get("scope")
    if (
        not isinstance(campaign_plan, dict)
        or validate_frontier_campaign_plan(campaign_plan)
        or not isinstance(release_policy, dict)
        or validate_frontier_release_policy(release_policy)
        or not isinstance(source_commitment, dict)
        or validate_frontier_source_commitment(source_commitment)
        or not isinstance(scope, dict)
    ):
        failures.append("frontier campaign contract evidence is invalid")
    else:
        if (
            campaign_plan.get("campaign_id") != report.get("campaign_id")
            or campaign_plan.get("campaign_sha256") != scope.get("campaign_sha256")
            or release_policy.get("policy_sha256") != scope.get("policy_sha256")
            or source_commitment.get("commitment_sha256")
            != scope.get("commitment_sha256")
            or campaign_plan.get("analysis_sha256") != scope.get("analysis_sha256")
            or source_commitment.get("analysis_sha256")
            != scope.get("analysis_sha256")
        ):
            failures.append("frontier report scope is not bound to campaign contracts")
        campaign_artifact = next(
            (
                artifact
                for artifact in artifacts
                if artifact["artifact_kind"] == "campaign_plan"
            ),
            None,
        )
        if campaign_artifact is None:
            failures.append("frontier campaign plan artifact is missing")
        else:
            try:
                bundle = load_frontier_campaign_bundle(
                    root / campaign_artifact["path"], root=root
                )
            except (OSError, TypeError, UnicodeError, ValueError):
                failures.append("frontier campaign bundle could not be loaded")
            else:
                if validate_frontier_campaign_bundle(bundle):
                    failures.append("frontier campaign bundle authority is invalid")

    execution_bundle = artifact_values.get("execution_bundle")
    if (
        not isinstance(execution_bundle, dict)
        or not isinstance(campaign_plan, dict)
        or validate_frontier_execution_bundle(
            execution_bundle, campaign_plan=campaign_plan
        )
        or not isinstance(preflight, dict)
        or any(
            entry.get("execution_manifest", {}).get("preflight_sha256")
            != preflight.get("preflight_sha256")
            for entry in execution_bundle.get("executions", [])
            if isinstance(entry, dict)
        )
    ):
        failures.append("frontier all-system execution bundle evidence is invalid")

    artifact_content_digests = {
        artifact["content_sha256"] for artifact in artifacts
    }
    envelope_receipts: list[str] = []
    envelope_authorities: list[str] = []
    for artifact_kind in sorted(EVIDENCE_ENVELOPE_KINDS):
        envelope = artifact_values.get(artifact_kind)
        if (
            not isinstance(envelope, dict)
            or validate_frontier_evidence_envelope(envelope)
            or envelope.get("artifact_kind") != artifact_kind
            or not isinstance(scope, dict)
            or envelope.get("campaign_sha256") != scope.get("campaign_sha256")
            or envelope.get("policy_sha256") != scope.get("policy_sha256")
            or envelope.get("commitment_sha256") != scope.get("commitment_sha256")
            or envelope.get("report_id") != report.get("report_id")
            or envelope.get("outcome") != "passed"
        ):
            failures.append(f"frontier {artifact_kind} evidence is invalid")
            continue
        source_digests = envelope["payload"]["source_artifact_sha256s"]
        if any(digest not in artifact_content_digests for digest in source_digests):
            failures.append(
                f"frontier {artifact_kind} evidence references an uncommitted source"
            )
            continue
        if _valid_timestamp(envelope.get("generated_at")) and (
            not _valid_timestamp(scope.get("evidence_as_of"))
            or not _valid_timestamp(scope.get("expires_at"))
            or not (
                datetime.fromisoformat(scope["evidence_as_of"][:-1] + "+00:00")
                <= datetime.fromisoformat(
                    envelope["generated_at"][:-1] + "+00:00"
                )
                < datetime.fromisoformat(scope["expires_at"][:-1] + "+00:00")
            )
        ):
            failures.append(
                f"frontier {artifact_kind} evidence is outside the evidence window"
            )
            continue
        if evidence_semantic_verifier is None:
            failures.append("trusted frontier evidence semantic verifier is required")
            continue
        try:
            verified = evidence_semantic_verifier.verify(
                artifact_kind=artifact_kind,
                authority_id=envelope["semantic_authority_id"],
                context_sha256=frontier_evidence_semantic_context_sha256(envelope),
            )
        except Exception:
            verified = None
        if (
            not _valid_sha256(verified)
            or verified != envelope.get("semantic_receipt_sha256")
        ):
            failures.append(
                f"frontier {artifact_kind} semantic authority is invalid"
            )
        else:
            envelope_receipts.append(verified)
            envelope_authorities.append(envelope["semantic_authority_id"])
    if len(envelope_receipts) != len(set(envelope_receipts)):
        failures.append("frontier semantic evidence receipts must be unique")
    report_authorities = {
        item.get("authority_id")
        for field in ("gates", "baseline_comparisons")
        for item in report.get(field, [])
        if isinstance(item, dict) and isinstance(item.get("authority_id"), str)
    }
    if report_authorities.intersection(envelope_authorities):
        failures.append(
            "frontier semantic evidence authorities must be independent of report authorities"
        )
    return _unique(failures)


def run_frontier_offline_replay(
    plan_path: Path,
    *,
    root: Path,
    environment: Mapping[str, Any],
    report_authority_verifier: FrontierReportAuthorityVerifier | None,
    claim_authority_verifier: FrontierClaimAuthorityVerifier | None,
    replay_attestation_verifier: FrontierReplayAttestationVerifier | None,
    evidence_semantic_verifier: FrontierEvidenceSemanticVerifier | None = None,
    claim_validation_timestamp: str | None = None,
    replay_id: str = "offline-replay",
) -> dict[str, Any]:
    """Rebuild canonical report bytes from frozen inputs without external calls."""

    resolved_root = root.resolve()
    failures: list[str] = []
    actuals: dict[str, str | None] = {
        "report_file_sha256": None,
        "report_sha256": None,
        "report_canonical_sha256": None,
        "claim_inventory_file_sha256": None,
        "claim_inventory_sha256": None,
    }
    if not _valid_timestamp(claim_validation_timestamp):
        failures.append("trusted claim validation timestamp is required")
    safe_claim_validation_timestamp = (
        claim_validation_timestamp
        if _valid_timestamp(claim_validation_timestamp)
        else "1970-01-01T00:00:00Z"
    )
    try:
        plan_relative = _plan_relative_path(resolved_root, plan_path)
        plan = load_authority_artifact(
            resolved_root,
            plan_relative,
        ).value
    except (FrontierArtifactIOError, OSError, TypeError, UnicodeError, ValueError):
        return _replay_report(
            replay_id=replay_id,
            plan=None,
            failures=["frontier reproduction plan could not be loaded"],
            actuals=actuals,
            receipt=None,
            claim_validation_timestamp=safe_claim_validation_timestamp,
        )
    plan_failures = validate_frontier_reproduction_plan(plan)
    if plan_failures:
        return _replay_report(
            replay_id=replay_id,
            plan=plan,
            failures=["frontier reproduction plan is invalid"],
            actuals=actuals,
            receipt=None,
            claim_validation_timestamp=safe_claim_validation_timestamp,
        )
    if validate_frontier_reproduction_environment(environment) or (
        sha256_json(dict(environment)) != plan["environment_sha256"]
        or dict(environment) != plan["environment"]
    ):
        return _replay_report(
            replay_id=replay_id,
            plan=plan,
            failures=["frontier reproduction environment does not match"],
            actuals=actuals,
            receipt=None,
            claim_validation_timestamp=safe_claim_validation_timestamp,
        )

    artifact_values: dict[str, dict[str, Any]] = {}
    report_input: dict[str, Any] | None = None
    try:
        for artifact in plan["artifacts"]:
            loaded = load_authority_artifact(
                resolved_root,
                artifact["path"],
            )
            if loaded.content_sha256 != artifact["content_sha256"]:
                raise ValueError("frontier replay artifact hash mismatch")
            value = loaded.value
            artifact_values[artifact["artifact_kind"]] = value
            if artifact["artifact_kind"] == "frontier_report_input":
                report_input = value
        if report_input is None or set(report_input) != FRONTIER_REPORT_INPUT_FIELDS:
            raise ValueError("frontier report input is missing")
    except (FrontierArtifactIOError, OSError, TypeError, UnicodeError, ValueError):
        return _replay_report(
            replay_id=replay_id,
            plan=plan,
            failures=["frontier replay source artifacts are invalid"],
            actuals=actuals,
            receipt=None,
            claim_validation_timestamp=safe_claim_validation_timestamp,
        )

    if report_input["source_artifacts_sha256"] != plan["source_artifacts_sha256"]:
        failures.append("frontier report source binding does not match the plan")
    if report_input["campaign_id"] != plan["campaign_id"] or report_input[
        "report_id"
    ] != plan["report_id"]:
        failures.append("frontier report identity does not match the plan")

    rebuilt: dict[str, Any] | None = None
    expected_report: dict[str, Any] | None = None
    release_policy = artifact_values.get("release_policy")
    try:
        rebuilt = build_frontier_report(
            report_input,
            release_policy=release_policy,
            authority_verifier=report_authority_verifier,
        )
        expected_report, expected_raw, expected_content_sha256 = (
            _load_canonical_object(
                resolved_root,
                plan["expected_report_path"],
                label="expected frontier report",
            )
        )
        actual_raw = canonical_authority_bytes(rebuilt)
        actuals["report_file_sha256"] = _content_sha256(actual_raw)
        actuals["report_canonical_sha256"] = _content_sha256(actual_raw)
        actuals["report_sha256"] = rebuilt["report_sha256"]
        if expected_content_sha256 != plan["expected_report_file_sha256"]:
            failures.append("expected frontier report file hash mismatch")
        if _content_sha256(canonical_authority_bytes(expected_report)) != plan[
            "expected_report_canonical_sha256"
        ]:
            failures.append("expected frontier report canonical hash mismatch")
        if expected_report.get("report_sha256") != plan["expected_report_sha256"]:
            failures.append("expected frontier report self-hash mismatch")
        if expected_report != rebuilt or expected_raw != actual_raw:
            failures.append("offline replay did not reproduce canonical report bytes")
        if validate_frontier_report(
            expected_report,
            release_policy=release_policy,
            authority_verifier=report_authority_verifier,
        ):
            failures.append("expected frontier report authority is invalid")
    except (
        FrontierArtifactIOError,
        KeyError,
        OSError,
        TypeError,
        UnicodeError,
        ValueError,
    ):
        failures.append("frontier report could not be rebuilt")

    inventory: dict[str, Any] | None = None
    if rebuilt is not None:
        try:
            inventory, inventory_raw, inventory_content_sha256 = (
                _load_canonical_object(
                    resolved_root,
                    plan["claim_inventory_path"],
                    label="frontier claim inventory",
                )
            )
            actuals["claim_inventory_file_sha256"] = inventory_content_sha256
            actuals["claim_inventory_sha256"] = inventory.get("inventory_sha256")
            if inventory_content_sha256 != plan["claim_inventory_file_sha256"]:
                failures.append("frontier claim inventory file hash mismatch")
            if inventory.get("inventory_sha256") != plan["claim_inventory_sha256"]:
                failures.append("frontier claim inventory self-hash mismatch")
            if validate_frontier_claim_inventory(
                inventory,
                report=rebuilt,
                release_policy=release_policy,
                report_authority_verifier=report_authority_verifier,
                claim_authority_verifier=claim_authority_verifier,
                as_of=safe_claim_validation_timestamp,
            ):
                failures.append("frontier claim inventory authority is invalid")
        except (
            FrontierArtifactIOError,
            KeyError,
            OSError,
            TypeError,
            UnicodeError,
            ValueError,
        ):
            failures.append("frontier claim inventory could not be verified")

    if rebuilt is not None:
        failures.extend(
            _top_tier_artifact_failures(
                rebuilt,
                plan["artifacts"],
                artifact_values,
                root=resolved_root,
                evidence_semantic_verifier=evidence_semantic_verifier,
            )
        )

    receipt: str | None = None
    if not failures:
        if replay_attestation_verifier is None:
            failures.append("trusted frontier replay attestation verifier is required")
        else:
            provisional = _replay_report(
                replay_id=replay_id,
                plan=plan,
                failures=[],
                actuals=actuals,
                receipt="0" * 64,
                claim_validation_timestamp=safe_claim_validation_timestamp,
            )
            operator = plan["operator"]
            try:
                receipt = replay_attestation_verifier.verify(
                    operator_id=operator["operator_id"],
                    machine_id=operator["machine_id"],
                    authority_id=operator["attestation_authority_id"],
                    attestation_id=operator["attestation_id"],
                    context_sha256=frontier_replay_context_sha256(provisional),
                )
            except Exception:
                receipt = None
            if not _valid_sha256(receipt):
                failures.append("frontier replay attestation could not be verified")
                receipt = None
    return _replay_report(
        replay_id=replay_id,
        plan=plan,
        failures=failures,
        actuals=actuals,
        receipt=receipt,
        claim_validation_timestamp=safe_claim_validation_timestamp,
    )


def validate_frontier_replay_report(
    report: Any,
    *,
    plan: Any | None = None,
    attestation_verifier: FrontierReplayAttestationVerifier | None,
) -> list[str]:
    """Validate replay output and recheck a passing operator receipt."""

    failures = _exact_fields(report, REPLAY_REPORT_FIELDS, "frontier replay report")
    if not isinstance(report, dict):
        return failures
    if report.get("schema_version") != FRONTIER_CONTRACT_SCHEMA_VERSION:
        failures.append("unsupported frontier replay report schema")
    for field in (
        "replay_id",
        "plan_id",
        "campaign_id",
        "report_id",
        "operator_id",
        "machine_id",
    ):
        if not _valid_identifier(report.get(field)):
            failures.append(f"frontier replay report {field} is invalid")
    if not _valid_timestamp(report.get("claim_validation_timestamp")):
        failures.append("frontier replay claim validation timestamp is invalid")
    if not isinstance(report.get("passed"), bool):
        failures.append("frontier replay pass status is invalid")
    report_failures = report.get("failures")
    if (
        not isinstance(report_failures, list)
        or any(not isinstance(item, str) or not item for item in report_failures)
        or report_failures != sorted(set(report_failures))
    ):
        failures.append("frontier replay failures are invalid")
        report_failures = []
    optional_hash_fields = (
        "plan_sha256",
        "input_commitments_sha256",
        "source_artifacts_sha256",
        "expected_report_file_sha256",
        "actual_report_file_sha256",
        "expected_report_sha256",
        "actual_report_sha256",
        "expected_report_canonical_sha256",
        "actual_report_canonical_sha256",
        "expected_claim_inventory_file_sha256",
        "actual_claim_inventory_file_sha256",
        "expected_claim_inventory_sha256",
        "actual_claim_inventory_sha256",
        "environment_sha256",
        "replay_command_sha256",
        "operator_attestation_receipt_sha256",
    )
    for field in optional_hash_fields:
        value = report.get(field)
        if value is not None and not _valid_sha256(value):
            failures.append(f"frontier replay report {field} is invalid")
    if not isinstance(report.get("exit_code"), int) or isinstance(
        report.get("exit_code"), bool
    ) or report.get("exit_code") < 0:
        failures.append("frontier replay exit code is invalid")

    if report.get("passed") is True:
        required = [report.get(field) for field in optional_hash_fields]
        if not all(_valid_sha256(value) for value in required):
            failures.append("passing frontier replay requires complete hashes")
        if report_failures:
            failures.append("passing frontier replay cannot contain failures")
        if report.get("exit_code") != 0:
            failures.append("passing frontier replay requires a zero exit code")
        comparisons = (
            ("expected_report_file_sha256", "actual_report_file_sha256"),
            ("expected_report_sha256", "actual_report_sha256"),
            (
                "expected_report_canonical_sha256",
                "actual_report_canonical_sha256",
            ),
            (
                "expected_claim_inventory_file_sha256",
                "actual_claim_inventory_file_sha256",
            ),
            (
                "expected_claim_inventory_sha256",
                "actual_claim_inventory_sha256",
            ),
        )
        if any(report.get(left) != report.get(right) for left, right in comparisons):
            failures.append("frontier replay expected and actual hashes differ")
        if attestation_verifier is None:
            failures.append("trusted frontier replay attestation verifier is required")
        elif not failures:
            operator = plan.get("operator") if isinstance(plan, dict) else None
            if not isinstance(operator, dict):
                failures.append("frontier reproduction plan is required for authority")
            else:
                try:
                    receipt = attestation_verifier.verify(
                        operator_id=report["operator_id"],
                        machine_id=report["machine_id"],
                        authority_id=operator["attestation_authority_id"],
                        attestation_id=operator["attestation_id"],
                        context_sha256=frontier_replay_context_sha256(report),
                    )
                except Exception:
                    receipt = None
                if receipt != report.get("operator_attestation_receipt_sha256"):
                    failures.append("frontier replay attestation is invalid")
    else:
        if not report_failures:
            failures.append("failed frontier replay must include a failure")
        if report.get("exit_code") == 0:
            failures.append("failed frontier replay requires a non-zero exit code")
        if report.get("operator_attestation_receipt_sha256") is not None:
            failures.append("failed frontier replay cannot claim an attestation receipt")

    if plan is not None:
        if validate_frontier_reproduction_plan(plan):
            failures.append("frontier replay plan authority is invalid")
        elif any(
            report.get(report_field) != plan.get(plan_field)
            for report_field, plan_field in (
                ("plan_id", "plan_id"),
                ("campaign_id", "campaign_id"),
                ("report_id", "report_id"),
                ("plan_sha256", "plan_sha256"),
                ("input_commitments_sha256", "input_commitments_sha256"),
                ("source_artifacts_sha256", "source_artifacts_sha256"),
                ("environment_sha256", "environment_sha256"),
                (
                    "expected_report_file_sha256",
                    "expected_report_file_sha256",
                ),
                ("expected_report_sha256", "expected_report_sha256"),
                (
                    "expected_report_canonical_sha256",
                    "expected_report_canonical_sha256",
                ),
                (
                    "expected_claim_inventory_file_sha256",
                    "claim_inventory_file_sha256",
                ),
                (
                    "expected_claim_inventory_sha256",
                    "claim_inventory_sha256",
                ),
            )
        ):
            failures.append("frontier replay report does not match its plan")
        elif (
            report.get("replay_command_sha256")
            != plan["environment"]["replay_command_sha256"]
            or report.get("operator_id") != plan["operator"]["operator_id"]
            or report.get("machine_id") != plan["operator"]["machine_id"]
        ):
            failures.append("frontier replay identity does not match its plan")
    if report.get("replay_sha256") != _safe_payload_hash(report, "replay_sha256"):
        failures.append("frontier replay report hash mismatch")
    return _unique(failures)


def build_frontier_reproduction_set(
    reproduction_set_id: str,
    *,
    plans: list[Mapping[str, Any]],
    replay_reports: list[Mapping[str, Any]],
    attestation_verifier: FrontierReplayAttestationVerifier | None,
) -> dict[str, Any]:
    """Aggregate exactly three independently attested, byte-identical replays."""

    if not _valid_identifier(reproduction_set_id):
        raise ValueError("frontier reproduction set id is invalid")
    if len(plans) != 3 or len(replay_reports) != 3:
        raise ValueError("frontier reproduction set requires exactly three replays")
    if not all(isinstance(item, Mapping) for item in plans + replay_reports):
        raise ValueError("frontier reproduction set inputs must be objects")

    normalized_plans = [dict(item) for item in plans]
    normalized_reports = [dict(item) for item in replay_reports]
    first_plan = normalized_plans[0]
    failures: list[str] = []
    for index, (plan, report) in enumerate(
        zip(normalized_plans, normalized_reports, strict=True)
    ):
        if validate_frontier_reproduction_plan(plan):
            failures.append(f"reproduction plan {index + 1} is invalid")
        if validate_frontier_replay_report(
            report,
            plan=plan,
            attestation_verifier=attestation_verifier,
        ):
            failures.append(f"replay report {index + 1} is invalid")
        if report.get("passed") is not True:
            failures.append(f"replay report {index + 1} did not pass")

    commitments = first_plan.get("reproduction_operators")
    if not isinstance(commitments, list) or len(commitments) != 3:
        commitments = [{}, {}, {}]
        failures.append("frozen reproduction group is invalid")
    common_plan_fields = (
        "campaign_id",
        "report_id",
        "reproduction_group_id",
        "required_operator_count",
        "report_input_path",
        "expected_report_path",
        "expected_report_file_sha256",
        "expected_report_sha256",
        "expected_report_canonical_sha256",
        "claim_inventory_path",
        "claim_inventory_file_sha256",
        "claim_inventory_sha256",
        "source_artifacts_sha256",
        "input_commitments_sha256",
        "environment_sha256",
        "reproduction_operators",
    )
    if any(
        any(plan.get(field) != first_plan.get(field) for field in common_plan_fields)
        for plan in normalized_plans[1:]
    ):
        failures.append("reproduction plans do not share one frozen evidence set")

    for index, (plan, report, commitment) in enumerate(
        zip(normalized_plans, normalized_reports, commitments, strict=True)
    ):
        if plan.get("operator") != commitment:
            failures.append("active reproduction operators are not in canonical order")
        if isinstance(commitment, dict) and (
            report.get("operator_id") != commitment.get("operator_id")
            or report.get("machine_id") != commitment.get("machine_id")
        ):
            failures.append("replay identities do not match frozen operators")

    operator_ids = [report.get("operator_id") for report in normalized_reports]
    machine_ids = [report.get("machine_id") for report in normalized_reports]
    receipts = [
        report.get("operator_attestation_receipt_sha256")
        for report in normalized_reports
    ]
    authorities = [
        commitment.get("attestation_authority_id")
        if isinstance(commitment, dict)
        else None
        for commitment in commitments
    ]
    for values, label in (
        (operator_ids, "operators"),
        (machine_ids, "machines"),
        (authorities, "attestation authorities"),
        (receipts, "attestation receipts"),
    ):
        if len(values) != len(set(values)):
            failures.append(f"reproduction {label} must be independent")

    exact_report_fields = (
        "expected_report_file_sha256",
        "actual_report_file_sha256",
        "expected_report_sha256",
        "actual_report_sha256",
        "expected_report_canonical_sha256",
        "actual_report_canonical_sha256",
        "expected_claim_inventory_file_sha256",
        "actual_claim_inventory_file_sha256",
        "expected_claim_inventory_sha256",
        "actual_claim_inventory_sha256",
        "input_commitments_sha256",
        "source_artifacts_sha256",
        "claim_validation_timestamp",
    )
    if any(
        any(
            report.get(field) != normalized_reports[0].get(field)
            for field in exact_report_fields
        )
        for report in normalized_reports[1:]
    ):
        failures.append("replays did not reproduce one byte-identical evidence result")

    normalized_failures = sorted(set(failures))
    aggregate: dict[str, Any] = {
        "schema_version": FRONTIER_CONTRACT_SCHEMA_VERSION,
        "reproduction_set_id": reproduction_set_id,
        "reproduction_group_id": first_plan.get("reproduction_group_id"),
        "campaign_id": first_plan.get("campaign_id"),
        "report_id": first_plan.get("report_id"),
        "required_operator_count": 3,
        "plan_sha256s": [plan.get("plan_sha256") for plan in normalized_plans],
        "replay_sha256s": [report.get("replay_sha256") for report in normalized_reports],
        "operator_ids": operator_ids,
        "machine_ids": machine_ids,
        "attestation_authority_ids": authorities,
        "attestation_receipt_sha256s": receipts,
        "expected_report_sha256": normalized_reports[0].get(
            "expected_report_sha256"
        ),
        "actual_report_sha256": normalized_reports[0].get("actual_report_sha256"),
        "claim_validation_timestamp": normalized_reports[0].get(
            "claim_validation_timestamp"
        ),
        "passed": not normalized_failures,
        "failures": normalized_failures,
    }
    aggregate["reproduction_set_sha256"] = hash_payload(
        aggregate, "reproduction_set_sha256"
    )
    return aggregate


def validate_frontier_reproduction_set(
    reproduction_set: Any,
    *,
    plans: list[Mapping[str, Any]],
    replay_reports: list[Mapping[str, Any]],
    attestation_verifier: FrontierReplayAttestationVerifier | None,
) -> list[str]:
    """Rebuild and validate a three-party reproduction aggregate."""

    failures = _exact_fields(
        reproduction_set, REPRODUCTION_SET_FIELDS, "frontier reproduction set"
    )
    if not isinstance(reproduction_set, dict):
        return failures
    if reproduction_set.get("schema_version") != FRONTIER_CONTRACT_SCHEMA_VERSION:
        failures.append("unsupported frontier reproduction set schema")
    try:
        rebuilt = build_frontier_reproduction_set(
            reproduction_set.get("reproduction_set_id"),
            plans=plans,
            replay_reports=replay_reports,
            attestation_verifier=attestation_verifier,
        )
    except (KeyError, OverflowError, RecursionError, TypeError, ValueError):
        failures.append("frontier reproduction set inputs are invalid")
    else:
        if reproduction_set != rebuilt:
            failures.append("frontier reproduction set does not match verified replays")
    if reproduction_set.get("reproduction_set_sha256") != _safe_payload_hash(
        reproduction_set, "reproduction_set_sha256"
    ):
        failures.append("frontier reproduction set hash mismatch")
    return _unique(failures)
