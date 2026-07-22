"""Fail-closed local preflight for a frozen frontier campaign bundle."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Protocol

from .frontier_contracts import (
    FRONTIER_CONTRACT_SCHEMA_VERSION,
    SHA256_RE,
    FrontierCampaignBundle,
    analysis_sha256,
    load_frontier_campaign_bundle,
    validate_frontier_campaign_bundle,
)
from .hashing import hash_payload, sha256_json


PREFLIGHT_REPORT_FIELDS = frozenset(
    {
        "schema_version",
        "passed",
        "failures",
        "campaign_sha256",
        "policy_sha256",
        "commitment_sha256",
        "analysis_sha256",
        "timestamp",
        "evidence_expires_at",
        "clock_attestation_receipt_sha256",
        "owner_attestation_receipt_sha256",
        "custodian_attestation_receipt_sha256",
        "capability_receipts_sha256",
        "preflight_sha256",
    }
)
UTC_TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$"
)


class PreflightAttestationVerifier(Protocol):
    """Trusted host boundary for owner and dataset-custodian attestations."""

    def verify(
        self,
        *,
        role: str,
        subject_id: str,
        authority_id: str,
        attestation_id: str,
        signature_sha256: str,
        context_sha256: str,
    ) -> str | None:
        """Return a unique canonical receipt digest, or None when unverified."""


@dataclass(frozen=True)
class HumanGoldVerificationResult:
    receipt_sha256: str
    calibrated_case_count: int
    covered_case_count: int
    covered_critical_case_count: int
    complete_position_pair_case_count: int
    ordinary_minimum_reviewers: int
    critical_minimum_reviewers: int
    senior_adjudication_complete: bool
    all_denominators_nonzero: bool
    order_consistency_ppm: int
    human_majority_agreement_ppm: int
    agreement_coefficient_ppm: int
    critical_recall_ppm: int
    false_negative_rate_ppm: int
    length_bias_ppm: int


@dataclass(frozen=True)
class StorageVerificationResult:
    receipt_sha256: str
    available_bytes: int


@dataclass(frozen=True)
class ClockVerificationResult:
    timestamp: str
    receipt_sha256: str


class PreflightClockVerifier(Protocol):
    """Trusted wall-clock boundary; callers cannot supply authoritative time."""

    def verify(
        self,
        *,
        campaign_sha256: str,
        lifecycle_sha256: str,
        context_sha256: str,
    ) -> ClockVerificationResult | None:
        """Return current UTC time and its authority receipt."""


class PreflightHumanGoldVerifier(Protocol):
    """Trusted boundary that reads sealed gold and returns aggregate evidence."""

    def verify(
        self,
        *,
        receipt_authority_id: str,
        verifier_id: str,
        human_gold_sha256: str,
        human_gold_policy_sha256: str,
        expected_case_count: int,
        expected_critical_case_count: int,
        context_sha256: str,
    ) -> HumanGoldVerificationResult | None:
        """Return aggregate calibration evidence without returning sealed labels."""


class PreflightHostCapabilityVerifier(Protocol):
    """Trusted live verifier for a registered authority route and host."""

    def verify(
        self,
        *,
        route: str,
        authority_id: str,
        verifier_id: str,
        configuration_path: Path,
        configuration_sha256: str,
        runtime_image_digest: str | None,
        sandbox_policy_path: Path | None,
        sandbox_policy_sha256: str | None,
        visual_verifier_id: str | None,
        visual_calibration_path: Path | None,
        visual_calibration_sha256: str | None,
        context_sha256: str,
    ) -> str | None:
        """Return a receipt only after checking current host availability."""


class PreflightStorageVerifier(Protocol):
    """Trusted live verifier for writable storage capacity on the bound host."""

    def verify(
        self,
        *,
        storage_root_path: Path,
        authority_id: str,
        verifier_id: str,
        declared_available_bytes: int,
        required_worst_case_bytes: int,
        context_sha256: str,
    ) -> StorageVerificationResult | None:
        """Return observed capacity and a canonical authority receipt."""


def _utc_timestamp(value: str | None) -> tuple[str, str | None]:
    if value is None:
        rendered = datetime.now(timezone.utc).isoformat(timespec="seconds")
        return rendered.replace("+00:00", "Z"), None
    if not isinstance(value, str) or UTC_TIMESTAMP_RE.fullmatch(value) is None:
        return "1970-01-01T00:00:00Z", "preflight timestamp is invalid"
    try:
        datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return "1970-01-01T00:00:00Z", "preflight timestamp is invalid"
    return value, None


def _computed_digests(bundle: FrontierCampaignBundle) -> dict[str, str]:
    implementation_digest = bundle.analysis_implementation_sha256_actual
    return {
        "campaign_sha256": hash_payload(bundle.plan, "campaign_sha256"),
        "policy_sha256": hash_payload(
            bundle.release_policy,
            "policy_sha256",
        ),
        "commitment_sha256": hash_payload(
            bundle.source_commitment,
            "commitment_sha256",
        ),
        "analysis_sha256": analysis_sha256(
            implementation_sha256=implementation_digest,
            analysis_policy=bundle.release_policy["analysis"],
        ),
    }


def _attestation_receipt(
    verifier: PreflightAttestationVerifier,
    *,
    role: str,
    attestation: Mapping[str, Any],
    digests: Mapping[str, str],
) -> str | None:
    context_sha256 = sha256_json(
        {
            "role": role,
            "subject_id": attestation["subject_id"],
            **dict(digests),
        }
    )
    try:
        receipt = verifier.verify(
            role=role,
            subject_id=attestation["subject_id"],
            authority_id=attestation["authority_id"],
            attestation_id=attestation["attestation_id"],
            signature_sha256=attestation["signature_sha256"],
            context_sha256=context_sha256,
        )
    except Exception:
        return None
    if not isinstance(receipt, str) or SHA256_RE.fullmatch(receipt) is None:
        return None
    return receipt


def _json_integer(value: Any, *, minimum: int = 0, maximum: int | None = None) -> bool:
    if isinstance(value, bool) or not isinstance(value, int):
        return False
    if value < minimum:
        return False
    return maximum is None or value <= maximum


def _human_gold_receipt(
    verifier: PreflightHumanGoldVerifier,
    *,
    bundle: FrontierCampaignBundle,
    digests: Mapping[str, str],
) -> tuple[str | None, list[str]]:
    human_gold = bundle.plan["human_gold"]
    policy = bundle.release_policy["human_gold"]
    cases = bundle.source_commitment["case_commitments"]
    expected_case_count = len(cases)
    expected_critical_case_count = sum(bool(case["critical"]) for case in cases)
    human_gold_sha256 = sha256_json(human_gold)
    human_gold_policy_sha256 = sha256_json(policy)
    context_sha256 = sha256_json(
        {
            "role": "human_gold",
            "human_gold_sha256": human_gold_sha256,
            "human_gold_policy_sha256": human_gold_policy_sha256,
            "expected_case_count": expected_case_count,
            "expected_critical_case_count": expected_critical_case_count,
            **dict(digests),
        }
    )
    try:
        result = verifier.verify(
            receipt_authority_id=human_gold["receipt_authority_id"],
            verifier_id=human_gold["verifier_id"],
            human_gold_sha256=human_gold_sha256,
            human_gold_policy_sha256=human_gold_policy_sha256,
            expected_case_count=expected_case_count,
            expected_critical_case_count=expected_critical_case_count,
            context_sha256=context_sha256,
        )
    except Exception:
        return None, ["human-gold authority could not be verified"]
    if not isinstance(result, HumanGoldVerificationResult):
        return None, ["human-gold authority returned invalid evidence"]

    failures: list[str] = []
    if (
        not isinstance(result.receipt_sha256, str)
        or SHA256_RE.fullmatch(result.receipt_sha256) is None
    ):
        failures.append("human-gold receipt digest is invalid")
    count_fields = (
        result.calibrated_case_count,
        result.covered_case_count,
        result.covered_critical_case_count,
        result.complete_position_pair_case_count,
        result.ordinary_minimum_reviewers,
        result.critical_minimum_reviewers,
    )
    if any(not _json_integer(value) for value in count_fields):
        failures.append("human-gold aggregate counts are invalid")
    metric_fields = (
        result.order_consistency_ppm,
        result.human_majority_agreement_ppm,
        result.agreement_coefficient_ppm,
        result.critical_recall_ppm,
        result.false_negative_rate_ppm,
        result.length_bias_ppm,
    )
    if any(
        not _json_integer(value, maximum=1_000_000) for value in metric_fields
    ):
        failures.append("human-gold aggregate metrics are invalid")
    if failures:
        return None, failures

    if result.covered_case_count != expected_case_count:
        failures.append("human-gold evidence does not cover all committed cases")
    if result.covered_critical_case_count != expected_critical_case_count:
        failures.append("human-gold evidence does not cover all critical cases")
    if not (
        result.calibrated_case_count >= int(policy["minimum_calibration_cases"])
        and result.calibrated_case_count <= result.covered_case_count
    ):
        failures.append("human-gold calibration case threshold is not met")
    if result.complete_position_pair_case_count != result.calibrated_case_count:
        failures.append("human-gold evidence has incomplete A/B position pairs")
    if result.ordinary_minimum_reviewers < int(
        policy["ordinary_minimum_expert_reviewers"]
    ):
        failures.append("ordinary human-gold expert threshold is not met")
    if result.critical_minimum_reviewers < int(
        policy["critical_minimum_expert_reviewers"]
    ):
        failures.append("critical human-gold expert threshold is not met")
    if result.senior_adjudication_complete is not True:
        failures.append("critical human-gold senior adjudication is incomplete")
    if result.all_denominators_nonzero is not True:
        failures.append("human-gold calibration has a zero denominator")
    minimum_metrics = {
        "order consistency": (
            result.order_consistency_ppm,
            policy["minimum_order_consistency_ppm"],
        ),
        "human-majority agreement": (
            result.human_majority_agreement_ppm,
            policy["minimum_human_majority_agreement_ppm"],
        ),
        "agreement coefficient": (
            result.agreement_coefficient_ppm,
            policy["minimum_agreement_coefficient_ppm"],
        ),
        "critical recall": (
            result.critical_recall_ppm,
            policy["minimum_critical_recall_ppm"],
        ),
    }
    for label, (actual, required) in minimum_metrics.items():
        if actual < int(required):
            failures.append(f"human-gold {label} threshold is not met")
    if result.false_negative_rate_ppm > int(
        policy["maximum_false_negative_rate_ppm"]
    ):
        failures.append("human-gold false-negative threshold is not met")
    if result.length_bias_ppm > int(policy["maximum_length_bias_ppm"]):
        failures.append("human-gold length-bias threshold is not met")
    if failures:
        return None, failures
    return result.receipt_sha256, []


def _host_capability_receipts(
    verifier: PreflightHostCapabilityVerifier,
    *,
    bundle: FrontierCampaignBundle,
    digests: Mapping[str, str],
) -> tuple[list[dict[str, str]], list[str]]:
    receipts: list[dict[str, str]] = []
    failures: list[str] = []
    capabilities = sorted(
        bundle.plan["host_capabilities"],
        key=lambda item: (item["route"], item["authority_id"]),
    )
    for capability in capabilities:
        context_sha256 = sha256_json(
            {
                "role": "host_capability",
                "capability_sha256": sha256_json(capability),
                **dict(digests),
            }
        )
        sandbox_path = capability["sandbox_policy_path"]
        visual_path = capability["visual_calibration_path"]
        try:
            receipt = verifier.verify(
                route=capability["route"],
                authority_id=capability["authority_id"],
                verifier_id=capability["verifier_id"],
                configuration_path=(
                    bundle.root
                    / Path(*PurePosixPath(capability["configuration_path"]).parts)
                ).resolve(),
                configuration_sha256=capability["configuration_sha256"],
                runtime_image_digest=capability["runtime_image_digest"],
                sandbox_policy_path=(
                    None
                    if sandbox_path is None
                    else (
                        bundle.root / Path(*PurePosixPath(sandbox_path).parts)
                    ).resolve()
                ),
                sandbox_policy_sha256=capability["sandbox_policy_sha256"],
                visual_verifier_id=capability["visual_verifier_id"],
                visual_calibration_path=(
                    None
                    if visual_path is None
                    else (
                        bundle.root / Path(*PurePosixPath(visual_path).parts)
                    ).resolve()
                ),
                visual_calibration_sha256=capability[
                    "visual_calibration_sha256"
                ],
                context_sha256=context_sha256,
            )
        except Exception:
            receipt = None
        if not isinstance(receipt, str) or SHA256_RE.fullmatch(receipt) is None:
            failures.append("a host capability could not be verified")
            continue
        receipts.append(
            {
                "route": capability["route"],
                "authority_id": capability["authority_id"],
                "receipt_sha256": receipt,
            }
        )
    receipt_values = [item["receipt_sha256"] for item in receipts]
    if len(receipt_values) != len(set(receipt_values)):
        failures.append("host capability receipts must be unique")
    if len(receipts) != len(capabilities):
        failures.append("not every host capability has an availability receipt")
    return receipts, sorted(set(failures))


def _storage_receipt(
    verifier: PreflightStorageVerifier,
    *,
    bundle: FrontierCampaignBundle,
    digests: Mapping[str, str],
) -> tuple[str | None, list[str]]:
    storage = bundle.plan["storage"]
    context_sha256 = sha256_json(
        {
            "role": "storage",
            "storage_sha256": sha256_json(storage),
            **dict(digests),
        }
    )
    try:
        result = verifier.verify(
            storage_root_path=bundle.storage_root_path,
            authority_id=storage["authority_id"],
            verifier_id=storage["verifier_id"],
            declared_available_bytes=int(storage["available_bytes"]),
            required_worst_case_bytes=int(storage["worst_case_bytes"]),
            context_sha256=context_sha256,
        )
    except Exception:
        return None, ["campaign storage availability could not be verified"]
    if not isinstance(result, StorageVerificationResult):
        return None, ["campaign storage verifier returned invalid evidence"]
    if (
        not isinstance(result.receipt_sha256, str)
        or SHA256_RE.fullmatch(result.receipt_sha256) is None
        or not _json_integer(result.available_bytes)
    ):
        return None, ["campaign storage verifier returned invalid evidence"]
    if result.available_bytes < int(storage["available_bytes"]):
        return None, ["verified storage is below the declared availability"]
    if result.available_bytes < int(storage["worst_case_bytes"]):
        return None, ["verified storage is below the campaign worst case"]
    return result.receipt_sha256, []


def _build_report(
    *,
    failures: list[str],
    digests: Mapping[str, str | None],
    timestamp: str,
    evidence_expires_at: str | None,
    clock_receipt: str | None,
    owner_receipt: str | None,
    custodian_receipt: str | None,
    capability_receipts: str | None,
) -> dict[str, Any]:
    normalized_failures = sorted(set(failures))
    passed = (
        not normalized_failures
        and owner_receipt is not None
        and clock_receipt is not None
        and custodian_receipt is not None
        and capability_receipts is not None
        and owner_receipt != custodian_receipt
    )
    if not passed and not normalized_failures:
        normalized_failures.append("preflight authority could not be verified")
    report: dict[str, Any] = {
        "schema_version": FRONTIER_CONTRACT_SCHEMA_VERSION,
        "passed": passed,
        "failures": normalized_failures,
        "campaign_sha256": digests.get("campaign_sha256"),
        "policy_sha256": digests.get("policy_sha256"),
        "commitment_sha256": digests.get("commitment_sha256"),
        "analysis_sha256": digests.get("analysis_sha256"),
        "timestamp": timestamp,
        "evidence_expires_at": evidence_expires_at,
        "clock_attestation_receipt_sha256": clock_receipt,
        "owner_attestation_receipt_sha256": owner_receipt,
        "custodian_attestation_receipt_sha256": custodian_receipt,
        "capability_receipts_sha256": capability_receipts,
    }
    report["preflight_sha256"] = hash_payload(report, "preflight_sha256")
    return report


def run_frontier_preflight(
    plan_path: Path,
    *,
    root: Path,
    attestation_verifier: PreflightAttestationVerifier | None,
    human_gold_verifier: PreflightHumanGoldVerifier | None = None,
    host_capability_verifier: PreflightHostCapabilityVerifier | None = None,
    storage_verifier: PreflightStorageVerifier | None = None,
    clock_verifier: PreflightClockVerifier | None = None,
) -> dict[str, Any]:
    """Load, validate, attest, and emit only the minimal preflight result."""

    rendered_timestamp = "1970-01-01T00:00:00Z"
    failures: list[str] = []
    empty_digests: dict[str, str | None] = {
        "campaign_sha256": None,
        "policy_sha256": None,
        "commitment_sha256": None,
        "analysis_sha256": None,
    }
    try:
        bundle = load_frontier_campaign_bundle(plan_path, root=root)
    except (OSError, TypeError, UnicodeError, ValueError):
        failures.append("campaign bundle could not be loaded")
        return _build_report(
            failures=failures,
            digests=empty_digests,
            timestamp=rendered_timestamp,
            evidence_expires_at=None,
            clock_receipt=None,
            owner_receipt=None,
            custodian_receipt=None,
            capability_receipts=None,
        )

    digests = _computed_digests(bundle)
    failures.extend(validate_frontier_campaign_bundle(bundle))
    clock_receipt: str | None = None
    if clock_verifier is None:
        failures.append("trusted preflight clock verifier is required")
    elif not failures:
        lifecycle_sha256 = sha256_json(bundle.plan["lifecycle"])
        clock_context_sha256 = sha256_json(
            {
                **digests,
                "lifecycle_sha256": lifecycle_sha256,
                "role": "preflight_clock",
            }
        )
        try:
            clock_result = clock_verifier.verify(
                campaign_sha256=digests["campaign_sha256"],
                lifecycle_sha256=lifecycle_sha256,
                context_sha256=clock_context_sha256,
            )
        except Exception:
            clock_result = None
        if not isinstance(clock_result, ClockVerificationResult):
            failures.append("preflight clock authority could not be verified")
        else:
            rendered_timestamp, timestamp_failure = _utc_timestamp(
                clock_result.timestamp
            )
            if timestamp_failure is not None:
                failures.append("preflight clock timestamp is invalid")
            if (
                not isinstance(clock_result.receipt_sha256, str)
                or SHA256_RE.fullmatch(clock_result.receipt_sha256) is None
            ):
                failures.append("preflight clock receipt is invalid")
            else:
                clock_receipt = clock_result.receipt_sha256
    if not failures:
        rendered_datetime = datetime.fromisoformat(
            rendered_timestamp[:-1] + "+00:00"
        )
        lifecycle = bundle.plan["lifecycle"]
        created_at = datetime.fromisoformat(
            lifecycle["created_at"][:-1] + "+00:00"
        )
        expires_at = datetime.fromisoformat(
            lifecycle["evidence_expires_at"][:-1] + "+00:00"
        )
        if rendered_datetime < created_at:
            failures.append("preflight timestamp predates campaign creation")
        if rendered_datetime >= expires_at:
            failures.append("campaign evidence window has expired")
    if attestation_verifier is None:
        failures.append("trusted preflight attestation verifier is required")
    if human_gold_verifier is None:
        failures.append("trusted human-gold verifier is required")
    if host_capability_verifier is None:
        failures.append("trusted host capability verifier is required")
    if storage_verifier is None:
        failures.append("trusted storage verifier is required")
    if failures:
        return _build_report(
            failures=failures,
            digests=digests,
            timestamp=rendered_timestamp,
            evidence_expires_at=bundle.plan["lifecycle"]["evidence_expires_at"],
            clock_receipt=clock_receipt,
            owner_receipt=None,
            custodian_receipt=None,
            capability_receipts=None,
        )

    authority_digests = {
        **digests,
        "preflight_timestamp": rendered_timestamp,
        "evidence_expires_at": bundle.plan["lifecycle"]["evidence_expires_at"],
        "clock_attestation_receipt_sha256": clock_receipt,
    }
    owner_receipt = _attestation_receipt(
        attestation_verifier,
        role="owner",
        attestation=bundle.plan["attestations"]["owner"],
        digests=authority_digests,
    )
    custodian_receipt = _attestation_receipt(
        attestation_verifier,
        role="dataset_custodian",
        attestation=bundle.plan["attestations"]["dataset_custodian"],
        digests=authority_digests,
    )
    if owner_receipt is None:
        failures.append("owner preflight attestation could not be verified")
    if custodian_receipt is None:
        failures.append("dataset-custodian preflight attestation could not be verified")
    if owner_receipt is not None and owner_receipt == custodian_receipt:
        failures.append("preflight attestation receipts must be unique")
    human_gold_receipt, human_gold_failures = _human_gold_receipt(
        human_gold_verifier,
        bundle=bundle,
        digests=authority_digests,
    )
    failures.extend(human_gold_failures)
    host_receipts, host_failures = _host_capability_receipts(
        host_capability_verifier,
        bundle=bundle,
        digests=authority_digests,
    )
    failures.extend(host_failures)
    storage_receipt, storage_failures = _storage_receipt(
        storage_verifier,
        bundle=bundle,
        digests=authority_digests,
    )
    failures.extend(storage_failures)

    raw_receipts = [
        receipt
        for receipt in (
            owner_receipt,
            custodian_receipt,
            clock_receipt,
            human_gold_receipt,
            storage_receipt,
            *(item["receipt_sha256"] for item in host_receipts),
        )
        if receipt is not None
    ]
    if len(raw_receipts) != len(set(raw_receipts)):
        failures.append("all preflight authority receipts must be unique")
    capability_receipts = None
    if (
        not human_gold_failures
        and not host_failures
        and not storage_failures
        and human_gold_receipt is not None
        and storage_receipt is not None
    ):
        capability_receipts = sha256_json(
            {
                "human_gold_receipt_sha256": human_gold_receipt,
                "host_capability_receipts": host_receipts,
                "storage_receipt_sha256": storage_receipt,
            }
        )
    if len(raw_receipts) != len(set(raw_receipts)):
        capability_receipts = None
    return _build_report(
        failures=failures,
        digests=digests,
        timestamp=rendered_timestamp,
        evidence_expires_at=bundle.plan["lifecycle"]["evidence_expires_at"],
        clock_receipt=clock_receipt,
        owner_receipt=owner_receipt,
        custodian_receipt=custodian_receipt,
        capability_receipts=capability_receipts,
    )


def validate_frontier_preflight_report(report: Any) -> list[str]:
    failures: list[str] = []
    if not isinstance(report, dict):
        return ["frontier preflight report must be an object"]
    if set(report) != PREFLIGHT_REPORT_FIELDS:
        failures.append("frontier preflight report fields do not match the contract")
    if report.get("schema_version") != FRONTIER_CONTRACT_SCHEMA_VERSION:
        failures.append("unsupported frontier preflight report schema")
    if not isinstance(report.get("passed"), bool):
        failures.append("frontier preflight passed status is invalid")
    report_failures = report.get("failures")
    if (
        not isinstance(report_failures, list)
        or any(not isinstance(item, str) or not item for item in report_failures)
        or report_failures != sorted(set(report_failures))
    ):
        failures.append("frontier preflight failures are invalid")
        report_failures = []
    for field in (
        "campaign_sha256",
        "policy_sha256",
        "commitment_sha256",
        "analysis_sha256",
        "clock_attestation_receipt_sha256",
        "owner_attestation_receipt_sha256",
        "custodian_attestation_receipt_sha256",
        "capability_receipts_sha256",
    ):
        value = report.get(field)
        if value is not None and (
            not isinstance(value, str) or SHA256_RE.fullmatch(value) is None
        ):
            failures.append(f"frontier preflight {field} is invalid")
    timestamp = report.get("timestamp")
    if (
        not isinstance(timestamp, str)
        or UTC_TIMESTAMP_RE.fullmatch(timestamp) is None
        or _utc_timestamp(timestamp)[1] is not None
    ):
        failures.append("frontier preflight timestamp is invalid")
    evidence_expires_at = report.get("evidence_expires_at")
    if evidence_expires_at is not None and (
        not isinstance(evidence_expires_at, str)
        or UTC_TIMESTAMP_RE.fullmatch(evidence_expires_at) is None
        or _utc_timestamp(evidence_expires_at)[1] is not None
    ):
        failures.append("frontier preflight evidence expiry is invalid")
    owner_receipt = report.get("owner_attestation_receipt_sha256")
    clock_receipt = report.get("clock_attestation_receipt_sha256")
    custodian_receipt = report.get("custodian_attestation_receipt_sha256")
    capability_receipts = report.get("capability_receipts_sha256")
    required_digests = (
        report.get("campaign_sha256"),
        report.get("policy_sha256"),
        report.get("commitment_sha256"),
        report.get("analysis_sha256"),
    )
    expected_passed = (
        not report_failures
        and all(
            isinstance(value, str) and SHA256_RE.fullmatch(value) is not None
            for value in required_digests
        )
        and isinstance(owner_receipt, str)
        and SHA256_RE.fullmatch(owner_receipt) is not None
        and isinstance(custodian_receipt, str)
        and SHA256_RE.fullmatch(custodian_receipt) is not None
        and isinstance(capability_receipts, str)
        and SHA256_RE.fullmatch(capability_receipts) is not None
        and owner_receipt != custodian_receipt
        and isinstance(clock_receipt, str)
        and SHA256_RE.fullmatch(clock_receipt) is not None
        and clock_receipt not in {owner_receipt, custodian_receipt, capability_receipts}
        and isinstance(evidence_expires_at, str)
        and UTC_TIMESTAMP_RE.fullmatch(evidence_expires_at) is not None
        and _utc_timestamp(evidence_expires_at)[1] is None
        and _utc_timestamp(timestamp)[1] is None
        and datetime.fromisoformat(timestamp[:-1] + "+00:00")
        < datetime.fromisoformat(evidence_expires_at[:-1] + "+00:00")
    )
    if report.get("passed") is not expected_passed:
        failures.append("frontier preflight pass status does not match its evidence")
    if report.get("passed") is False and not report_failures:
        failures.append("failed frontier preflight report must include a failure")
    expected_hash = None
    try:
        expected_hash = hash_payload(report, "preflight_sha256")
    except (OverflowError, RecursionError, TypeError, ValueError):
        pass
    if report.get("preflight_sha256") != expected_hash:
        failures.append("frontier preflight report hash mismatch")
    return list(dict.fromkeys(failures))
