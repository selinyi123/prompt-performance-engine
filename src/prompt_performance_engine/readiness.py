"""Evidence-backed stable-release readiness assessment."""

from __future__ import annotations

import hashlib
import math
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping, Protocol

from .benchmark_replicates import (
    ModelCallReceiptVerifier,
    validate_e3_authority,
    validate_replicate_report,
)
from .contracts import ARTIFACT_SCHEMA_VERSION, load_strict_json_object
from .hashing import hash_payload, sha256_json
from .human_review import (
    ReviewerSubmissionVerifier,
    validate_human_review_authority,
    validate_human_review_report,
)

if TYPE_CHECKING:
    from .image_review import (
        ImageGenerationReceiptVerifier,
        VisualReviewerSubmissionVerifier,
    )
    from .software_sandbox import DockerSandbox


class ReproductionAttestationVerifier(Protocol):
    """Trusted boundary for one independent reproduction attestation."""

    def verify(
        self,
        *,
        evidence: Mapping[str, Any],
        context_sha256: str,
    ) -> str | None:
        """Return a unique bound receipt digest, or ``None`` when unverified."""


class ClaimsAuditVerifier(Protocol):
    """Trusted boundary for one public-claims audit attestation."""

    def verify(
        self,
        *,
        evidence: Mapping[str, Any],
        context_sha256: str,
    ) -> str | None:
        """Return a unique bound receipt digest, or ``None`` when unverified."""


SCHEMA_VERSION = ARTIFACT_SCHEMA_VERSION
CUSTOM_EVIDENCE_KINDS = {
    "operational_verification",
    "code_execution",
    "image_review",
    "expert_review_coverage",
    "independent_reproduction",
    "defect_register",
    "claims_audit",
}
READINESS_ARTIFACT_KINDS = CUSTOM_EVIDENCE_KINDS | {
    "benchmark_replicate",
    "human_review",
}
READINESS_MANIFEST_FIELDS = {
    "schema_version",
    "benchmark_target",
    "authority_sources",
    "artifacts",
    "manifest_sha256",
}
READINESS_EVIDENCE_FIELDS = {
    "schema_version",
    "report_id",
    "kind",
    "facts",
    "provenance",
    "limitations",
    "evidence_sha256",
}
OPERATIONAL_FACT_FIELDS = {
    "behavior_tests_passed",
    "release_validator_passed",
    "cli_passed",
    "api_passed",
    "service_passed",
    "package_install_passed",
    "documentation_verified",
}
EXPERT_FACT_FIELDS = {
    "domains",
    "blind",
    "qualified_reviewers",
    "reviewer_ids",
}
REPRODUCTION_FACT_FIELDS = {
    "machine_id_hash",
    "operator_id_hash",
    "install_passed",
    "replay_passed",
}
DEFECT_FACT_FIELDS = {"open_p0", "open_p1", "triage_complete"}
CLAIMS_FACT_FIELDS = {
    "unsupported_claims",
    "all_claims_artifact_bound",
    "documentation_scanned",
}
CODE_EXECUTION_FACT_FIELDS = {
    "eligible_cases",
    "executed_cases",
    "passed_cases",
    "restricted_subprocess_cases",
    "executable_cases",
    "sandboxed_cases",
    "formal_contract_cases",
    "sandboxed",
    "sandbox",
    "evaluation_gate_passed",
    "optimized_hard_failures",
    "case_results",
}
IMAGE_REVIEW_FACT_FIELDS = {
    "eligible_cases",
    "generated_cases",
    "reviewed_cases",
    "qualified_reviewers",
    "generation_receipts_verified",
    "reviewer_receipts_verified",
    "blind",
    "asset_integrity_verified",
    "review_coverage_verified",
    "unresolved_cases",
    "wins",
    "ties",
    "losses",
    "generation_manifest_sha256",
    "cases",
    "reviewer_profile_sha256",
}
REQUIREMENTS = (
    ("R01", "Release validation and behavior tests"),
    ("R02", "End-to-end CLI, API, service, package, and documentation"),
    ("R03", "Real 60-case coverage across all 12 domains"),
    ("R04", "Cross-domain improvement and zero-regression quality gate"),
    ("R05", "Executable verification for software cases"),
    ("R06", "Actual image generation and qualified visual review"),
    ("R07", "Blind independent expert human review"),
    ("R08", "Independent reproduction on three machines"),
    ("R09", "P0 and P1 defect closure"),
    ("R10", "Evidence-bound public claims"),
)
REQUIRED_EXPERT_DOMAINS = frozenset(
    {"creative_design", "research_analysis", "business_strategy"}
)
REQUIRED_RELEASE_DOMAINS = frozenset(
    {
        "agents_automation",
        "business_strategy",
        "creative_design",
        "education",
        "high_risk_advisory",
        "image_generation",
        "marketing_sales",
        "professional_writing",
        "research_analysis",
        "software_engineering",
        "structured_data",
        "translation_localization",
    }
)


def _release_domain_set_failure(value: Any) -> str | None:
    if (
        not isinstance(value, list)
        or any(not isinstance(item, str) or not item for item in value)
        or len(value) != len(set(value))
    ):
        return "completed domain list is invalid"
    observed = set(value)
    if observed == REQUIRED_RELEASE_DOMAINS:
        return None
    missing = sorted(REQUIRED_RELEASE_DOMAINS - observed)
    unexpected = sorted(observed - REQUIRED_RELEASE_DOMAINS)
    return (
        "completed domain set does not match the required release domains: "
        f"missing={missing}, unexpected={unexpected}"
    )


def build_evidence_report(
    *,
    kind: str,
    report_id: str,
    facts: dict[str, Any],
    provenance: dict[str, Any],
    limitations: list[str],
) -> dict[str, Any]:
    if kind not in CUSTOM_EVIDENCE_KINDS:
        raise ValueError(f"Unsupported readiness evidence kind: {kind}")
    if not report_id.strip():
        raise ValueError("Evidence report_id must not be empty.")
    if not limitations:
        raise ValueError("Evidence limitations must not be empty.")
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "report_id": report_id,
        "kind": kind,
        "facts": facts,
        "provenance": provenance,
        "limitations": limitations,
    }
    report["evidence_sha256"] = hash_payload(report, "evidence_sha256")
    failures = validate_readiness_evidence(report, expected_kind=kind)
    if failures:
        raise ValueError(f"Invalid readiness evidence: {failures}")
    return report


def build_readiness_manifest(
    artifacts: list[dict[str, str]],
    *,
    expected_benchmark_suite_id: str | None = None,
    expected_benchmark_definition_sha256: str | None = None,
    benchmark_run_directories: list[str] | None = None,
    human_review_plan: str | None = None,
    code_execution_plan: str | None = None,
    visual_review_plan: str | None = None,
) -> dict[str, Any]:
    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "benchmark_target": {
            "suite_id": expected_benchmark_suite_id,
            "definition_sha256": expected_benchmark_definition_sha256,
        },
        "authority_sources": {
            "benchmark_run_directories": benchmark_run_directories or [],
            "human_review_plan": human_review_plan,
            "code_execution_plan": code_execution_plan,
            "visual_review_plan": visual_review_plan,
        },
        "artifacts": artifacts,
    }
    manifest["manifest_sha256"] = hash_payload(manifest, "manifest_sha256")
    return manifest


def _contained_path(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Evidence path escapes readiness root: {relative}") from exc
    return candidate


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _internal_hash_valid(kind: str, payload: dict[str, Any]) -> bool:
    if kind == "benchmark_replicate":
        return not validate_replicate_report(payload)
    elif kind == "human_review":
        field = "human_review_sha256"
    else:
        field = "evidence_sha256"
        if payload.get("kind") != kind:
            return False
    value = payload.get(field)
    return isinstance(value, str) and value == hash_payload(payload, field)


def _valid_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _attestation_context_sha256(
    record: Mapping[str, Any],
    *,
    identity: Mapping[str, Any],
) -> str:
    """Bind a host attestation to the complete evidence digest and identity."""

    provenance = record.get("provenance")
    producer = provenance.get("producer") if isinstance(provenance, dict) else None
    return sha256_json(
        {
            "schema_version": record.get("schema_version"),
            "kind": record.get("kind"),
            "report_id": record.get("report_id"),
            "evidence_sha256": record.get("evidence_sha256"),
            "producer": producer,
            "identity": dict(identity),
        }
    )


def _verify_attestation_receipt(
    record: Mapping[str, Any],
    *,
    identity: Mapping[str, Any],
    verifier: ReproductionAttestationVerifier | ClaimsAuditVerifier | None,
    label: str,
) -> tuple[str | None, str | None]:
    if verifier is None:
        return None, f"trusted {label} verifier is required"
    try:
        receipt = verifier.verify(
            evidence=record,
            context_sha256=_attestation_context_sha256(
                record,
                identity=identity,
            ),
        )
    except Exception:  # The host trust boundary must fail closed.
        return None, f"{label} verifier failed"
    if not _valid_sha256(receipt):
        return None, f"{label} receipt is missing or invalid"
    return receipt, None


def _normalized_json_integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value)
    if isinstance(value, Decimal) and value.is_finite() and value == value.to_integral():
        return int(value)
    return None


def _validate_string_list(
    value: Any,
    *,
    label: str,
    sorted_unique: bool = False,
) -> list[str]:
    if (
        not isinstance(value, list)
        or any(not isinstance(item, str) or not item.strip() for item in value)
        or len(value) != len(set(value))
        or (sorted_unique and value != sorted(value))
    ):
        return [f"{label} must be a unique array of non-empty strings"]
    return []


def _validate_custom_evidence_facts(kind: str, facts: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    expected_fields = {
        "operational_verification": OPERATIONAL_FACT_FIELDS,
        "code_execution": CODE_EXECUTION_FACT_FIELDS,
        "image_review": IMAGE_REVIEW_FACT_FIELDS,
        "expert_review_coverage": EXPERT_FACT_FIELDS,
        "independent_reproduction": REPRODUCTION_FACT_FIELDS,
        "defect_register": DEFECT_FACT_FIELDS,
        "claims_audit": CLAIMS_FACT_FIELDS,
    }[kind]
    if set(facts) != expected_fields:
        failures.append(f"{kind} facts fields do not match the contract")
        return failures

    if kind == "operational_verification":
        if any(not isinstance(facts[field], bool) for field in expected_fields):
            failures.append("operational verification facts must be booleans")
    elif kind == "expert_review_coverage":
        failures.extend(
            _validate_string_list(facts["domains"], label="expert domains")
        )
        failures.extend(
            _validate_string_list(
                facts["reviewer_ids"],
                label="expert reviewer_ids",
                sorted_unique=True,
            )
        )
        qualified = _normalized_json_integer(facts["qualified_reviewers"])
        if qualified is None or qualified < 0:
            failures.append("expert qualified_reviewers must be a non-negative integer")
        if not isinstance(facts["blind"], bool):
            failures.append("expert blind must be a boolean")
    elif kind == "independent_reproduction":
        if not _valid_sha256(facts["machine_id_hash"]):
            failures.append("reproduction machine_id_hash must be a SHA-256 digest")
        if not _valid_sha256(facts["operator_id_hash"]):
            failures.append("reproduction operator_id_hash must be a SHA-256 digest")
        if any(
            not isinstance(facts[field], bool)
            for field in ("install_passed", "replay_passed")
        ):
            failures.append("reproduction pass facts must be booleans")
    elif kind == "defect_register":
        for field in ("open_p0", "open_p1"):
            count = _normalized_json_integer(facts[field])
            if count is None or count < 0:
                failures.append(f"{field} must be a non-negative integer")
        if not isinstance(facts["triage_complete"], bool):
            failures.append("triage_complete must be a boolean")
    elif kind == "claims_audit":
        unsupported = _normalized_json_integer(facts["unsupported_claims"])
        if unsupported is None or unsupported < 0:
            failures.append("unsupported_claims must be a non-negative integer")
        if any(
            not isinstance(facts[field], bool)
            for field in ("all_claims_artifact_bound", "documentation_scanned")
        ):
            failures.append("claims audit pass facts must be booleans")
    elif kind == "code_execution":
        for field in (
            "eligible_cases",
            "executed_cases",
            "passed_cases",
            "restricted_subprocess_cases",
            "executable_cases",
            "sandboxed_cases",
            "formal_contract_cases",
            "optimized_hard_failures",
        ):
            count = _normalized_json_integer(facts[field])
            if count is None or count < 0:
                failures.append(f"code execution {field} must be non-negative integer")
        if any(
            not isinstance(facts[field], bool)
            for field in ("sandboxed", "evaluation_gate_passed")
        ):
            failures.append("code execution gate facts must be booleans")
        if not isinstance(facts["sandbox"], dict):
            failures.append("code execution sandbox must be an object")
        if not isinstance(facts["case_results"], dict):
            failures.append("code execution case_results must be an object")
    elif kind == "image_review":
        for field in (
            "eligible_cases",
            "generated_cases",
            "reviewed_cases",
            "qualified_reviewers",
            "wins",
            "ties",
            "losses",
        ):
            count = _normalized_json_integer(facts[field])
            if count is None or count < 0:
                failures.append(f"image review {field} must be non-negative integer")
        if any(
            not isinstance(facts[field], bool)
            for field in (
                "generation_receipts_verified",
                "reviewer_receipts_verified",
                "blind",
                "asset_integrity_verified",
                "review_coverage_verified",
            )
        ):
            failures.append("image review gate facts must be booleans")
        failures.extend(
            _validate_string_list(
                facts["unresolved_cases"],
                label="image unresolved_cases",
                sorted_unique=True,
            )
        )
        if not _valid_sha256(facts["generation_manifest_sha256"]):
            failures.append("image generation manifest hash is invalid")
        if not isinstance(facts["cases"], list) or any(
            not isinstance(case, dict) for case in facts["cases"]
        ):
            failures.append("image review cases must be an array of objects")
        profile_hashes = facts["reviewer_profile_sha256"]
        if (
            not isinstance(profile_hashes, list)
            or any(not _valid_sha256(value) for value in profile_hashes)
            or profile_hashes != sorted(set(profile_hashes))
        ):
            failures.append("image reviewer profile hashes are invalid")
    return failures


def _validate_readiness_evidence(
    payload: Any,
    *,
    expected_kind: str | None = None,
) -> list[str]:
    if not isinstance(payload, dict):
        return ["readiness evidence root must be an object"]
    failures: list[str] = []
    if set(payload) != READINESS_EVIDENCE_FIELDS:
        failures.append("readiness evidence fields do not match the schema")
    if payload.get("schema_version") != SCHEMA_VERSION:
        failures.append("unsupported readiness evidence schema")
    report_id = payload.get("report_id")
    if not isinstance(report_id, str) or not report_id.strip():
        failures.append("readiness evidence report_id is invalid")
    kind = payload.get("kind")
    if not isinstance(kind, str) or kind not in CUSTOM_EVIDENCE_KINDS:
        failures.append("readiness evidence kind is invalid")
    elif expected_kind is not None and kind != expected_kind:
        failures.append("readiness evidence kind does not match its manifest entry")
    facts = payload.get("facts")
    if not isinstance(facts, dict):
        failures.append("readiness evidence facts must be an object")
    elif isinstance(kind, str) and kind in CUSTOM_EVIDENCE_KINDS:
        failures.extend(_validate_custom_evidence_facts(kind, facts))
    provenance = payload.get("provenance")
    if not isinstance(provenance, dict):
        failures.append("readiness evidence provenance must be an object")
    elif not isinstance(provenance.get("producer"), str) or not provenance[
        "producer"
    ].strip():
        failures.append("readiness evidence producer is invalid")
    limitations = payload.get("limitations")
    if (
        not isinstance(limitations, list)
        or not limitations
        or any(
            not isinstance(limitation, str) or not limitation.strip()
            for limitation in limitations
        )
    ):
        failures.append("readiness evidence limitations are invalid")
    digest = payload.get("evidence_sha256")
    if not _valid_sha256(digest):
        failures.append("readiness evidence hash is invalid")
    elif digest != hash_payload(payload, "evidence_sha256"):
        failures.append("readiness evidence hash mismatch")
    return failures


def validate_readiness_evidence(
    payload: Any,
    *,
    expected_kind: str | None = None,
) -> list[str]:
    """Validate one untrusted custom-readiness evidence envelope and facts."""

    try:
        return _validate_readiness_evidence(
            payload,
            expected_kind=expected_kind,
        )
    except (
        ArithmeticError,
        AttributeError,
        KeyError,
        RecursionError,
        TypeError,
        UnicodeError,
        ValueError,
    ) as exc:
        return [f"malformed readiness evidence: {exc}"]


def _validate_readiness_manifest(manifest: Any) -> list[str]:
    if not isinstance(manifest, dict):
        return ["readiness manifest root must be an object"]

    failures: list[str] = []
    if set(manifest) != READINESS_MANIFEST_FIELDS:
        failures.append("readiness manifest fields do not match the schema")
    if manifest.get("schema_version") != SCHEMA_VERSION:
        failures.append("unsupported readiness manifest schema")

    benchmark_target = manifest.get("benchmark_target")
    if not isinstance(benchmark_target, dict):
        failures.append("readiness benchmark target must be an object")
    else:
        if set(benchmark_target) != {"suite_id", "definition_sha256"}:
            failures.append("readiness benchmark target fields do not match the schema")
        suite_id = benchmark_target.get("suite_id")
        if suite_id is not None and (
            not isinstance(suite_id, str) or not suite_id
        ):
            failures.append("readiness benchmark target suite_id is invalid")
        definition_sha256 = benchmark_target.get("definition_sha256")
        if definition_sha256 is not None and not _valid_sha256(
            definition_sha256
        ):
            failures.append("readiness benchmark target definition hash is invalid")

    authority_sources = manifest.get("authority_sources")
    if not isinstance(authority_sources, dict):
        failures.append("readiness authority sources must be an object")
    else:
        if set(authority_sources) != {
            "benchmark_run_directories",
            "human_review_plan",
            "code_execution_plan",
            "visual_review_plan",
        }:
            failures.append("readiness authority source fields do not match the schema")
        run_directories = authority_sources.get("benchmark_run_directories")
        if (
            not isinstance(run_directories, list)
            or any(
                not isinstance(value, str) or not value
                for value in run_directories
            )
            or (
                all(isinstance(value, str) for value in run_directories)
                and len(run_directories) != len(set(run_directories))
            )
        ):
            failures.append("readiness benchmark run directories are invalid")
        human_review_plan = authority_sources.get("human_review_plan")
        if human_review_plan is not None and (
            not isinstance(human_review_plan, str) or not human_review_plan
        ):
            failures.append("readiness human-review plan path is invalid")
        for field, label in (
            ("code_execution_plan", "code-execution"),
            ("visual_review_plan", "visual-review"),
        ):
            plan_path = authority_sources.get(field)
            if plan_path is not None and (
                not isinstance(plan_path, str) or not plan_path
            ):
                failures.append(f"readiness {label} plan path is invalid")

    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        failures.append("readiness manifest artifacts must be an array")
    else:
        for index, spec in enumerate(artifacts):
            if not isinstance(spec, dict):
                failures.append(f"artifact[{index}] must be an object")
                continue
            if set(spec) != {"kind", "path", "sha256"}:
                failures.append(f"artifact[{index}] fields do not match the schema")
            kind = spec.get("kind")
            if not isinstance(kind, str) or kind not in READINESS_ARTIFACT_KINDS:
                failures.append(f"artifact[{index}] has unsupported kind: {kind!r}")
            relative = spec.get("path")
            if not isinstance(relative, str) or not relative:
                failures.append(f"artifact[{index}] has no valid path")
            if not _valid_sha256(spec.get("sha256")):
                failures.append(f"artifact[{index}] has no valid sha256")

    manifest_sha256 = manifest.get("manifest_sha256")
    if not _valid_sha256(manifest_sha256):
        failures.append("readiness manifest hash is invalid")
    elif manifest_sha256 != hash_payload(manifest, "manifest_sha256"):
        failures.append("readiness manifest hash mismatch")
    return failures


def validate_readiness_manifest(manifest: Any) -> list[str]:
    """Validate untrusted readiness manifest structure and content types."""
    try:
        return _validate_readiness_manifest(manifest)
    except (
        ArithmeticError,
        AttributeError,
        KeyError,
        RecursionError,
        TypeError,
        UnicodeError,
        ValueError,
    ) as exc:
        return [f"malformed readiness manifest: {exc}"]


def _load_artifacts(
    manifest: dict[str, Any],
    *,
    root: Path,
) -> tuple[dict[str, list[tuple[str, dict[str, Any]]]], list[str]]:
    manifest_failures = validate_readiness_manifest(manifest)
    if manifest_failures:
        raise ValueError(f"Invalid readiness manifest: {manifest_failures}")
    specs = manifest.get("artifacts")
    assert isinstance(specs, list)

    loaded: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    errors: list[str] = []
    for index, spec in enumerate(specs):
        if not isinstance(spec, dict):
            errors.append(f"artifact[{index}] must be an object")
            continue
        kind = spec.get("kind")
        relative = spec.get("path")
        expected_hash = spec.get("sha256")
        if kind not in READINESS_ARTIFACT_KINDS:
            errors.append(f"artifact[{index}] has unsupported kind: {kind!r}")
            continue
        if not isinstance(relative, str) or not relative:
            errors.append(f"artifact[{index}] has no path")
            continue
        if not isinstance(expected_hash, str) or len(expected_hash) != 64:
            errors.append(f"artifact[{index}] has no valid sha256")
            continue
        try:
            path = _contained_path(root, relative)
            if not path.is_file():
                raise ValueError("file does not exist")
            if _file_sha256(path) != expected_hash:
                raise ValueError("file sha256 mismatch")
            payload = load_strict_json_object(
                path,
                label=f"{kind} readiness evidence",
                preserve_decimal=False,
            )
            if not isinstance(payload, dict):
                raise ValueError("JSON root is not an object")
            if payload.get("schema_version") != SCHEMA_VERSION:
                raise ValueError("unsupported evidence schema")
            if kind in CUSTOM_EVIDENCE_KINDS:
                evidence_failures = validate_readiness_evidence(
                    payload,
                    expected_kind=str(kind),
                )
                if evidence_failures:
                    raise ValueError(
                        f"invalid readiness evidence: {evidence_failures}"
                    )
            elif not _internal_hash_valid(str(kind), payload):
                raise ValueError("internal evidence hash mismatch")
        except (OSError, ValueError) as exc:
            errors.append(f"{kind}:{relative}: {exc}")
            continue
        loaded.setdefault(str(kind), []).append((relative, payload))
    return loaded, errors


def _requirement(
    requirement_id: str,
    title: str,
    *,
    relevant_count: int,
    evidence: list[str],
    failures: list[str],
    invalid_evidence: bool = False,
) -> dict[str, Any]:
    if invalid_evidence:
        status = "failed"
    elif relevant_count == 0:
        status = "missing"
    elif failures:
        status = "partial"
    else:
        status = "passed"
    return {
        "id": requirement_id,
        "title": title,
        "status": status,
        "evidence": sorted(evidence),
        "failures": failures,
    }


def _custom(
    artifacts: dict[str, list[tuple[str, dict[str, Any]]]],
    kind: str,
) -> list[tuple[str, dict[str, Any]]]:
    return artifacts.get(kind, [])


def _singleton_facts(
    artifacts: dict[str, list[tuple[str, dict[str, Any]]]],
    kind: str,
) -> tuple[list[str], dict[str, Any] | None, list[str]]:
    records = _custom(artifacts, kind)
    evidence = [path for path, _ in records]
    if not records:
        return evidence, None, []
    failures = []
    if len(records) != 1:
        failures.append(f"exactly one {kind} report is required")
    return evidence, records[0][1].get("facts", {}), failures


def _all_true(facts: dict[str, Any], names: tuple[str, ...]) -> list[str]:
    return [name for name in names if facts.get(name) is not True]


def assess_readiness(
    manifest: dict[str, Any],
    *,
    root: Path,
    model_receipt_verifier: ModelCallReceiptVerifier | None = None,
    reviewer_submission_verifier: ReviewerSubmissionVerifier | None = None,
    software_sandbox: DockerSandbox | None = None,
    image_generation_receipt_verifier: ImageGenerationReceiptVerifier | None = None,
    visual_reviewer_submission_verifier: VisualReviewerSubmissionVerifier | None = None,
    reproduction_attestation_verifier: ReproductionAttestationVerifier | None = None,
    claims_audit_verifier: ClaimsAuditVerifier | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    artifacts, evidence_errors = _load_artifacts(manifest, root=root)
    authority_sources = manifest.get("authority_sources")
    if not isinstance(authority_sources, dict):
        authority_sources = {}
    invalid_kinds = {
        error.split(":", 1)[0]
        for error in evidence_errors
        if ":" in error
    }
    requirements: list[dict[str, Any]] = []

    operational_evidence, operational, operational_failures = _singleton_facts(
        artifacts,
        "operational_verification",
    )
    release_failures = list(operational_failures)
    if operational is not None:
        release_failures.extend(
            f"{name} is not proven"
            for name in _all_true(
                operational,
                ("behavior_tests_passed", "release_validator_passed"),
            )
        )
    requirements.append(
        _requirement(
            *REQUIREMENTS[0],
            relevant_count=1 if operational is not None else 0,
            evidence=operational_evidence,
            failures=release_failures,
            invalid_evidence="operational_verification" in invalid_kinds,
        )
    )

    surface_failures = list(operational_failures)
    if operational is not None:
        surface_failures.extend(
            f"{name} is not proven"
            for name in _all_true(
                operational,
                (
                    "cli_passed",
                    "api_passed",
                    "service_passed",
                    "package_install_passed",
                    "documentation_verified",
                ),
            )
        )
    requirements.append(
        _requirement(
            *REQUIREMENTS[1],
            relevant_count=1 if operational is not None else 0,
            evidence=operational_evidence,
            failures=surface_failures,
            invalid_evidence="operational_verification" in invalid_kinds,
        )
    )

    benchmark_records = artifacts.get("benchmark_replicate", [])
    benchmark_evidence = [path for path, _ in benchmark_records]
    benchmark_failures: list[str] = []
    benchmark_binding_failures: list[str] = []
    benchmark = benchmark_records[0][1] if benchmark_records else None
    benchmark_target = manifest.get("benchmark_target")
    target_suite_id = (
        benchmark_target.get("suite_id")
        if isinstance(benchmark_target, dict)
        else None
    )
    target_definition_sha256 = (
        benchmark_target.get("definition_sha256")
        if isinstance(benchmark_target, dict)
        else None
    )
    if not isinstance(target_suite_id, str) or not target_suite_id.strip():
        benchmark_binding_failures.append(
            "release benchmark suite target is not bound"
        )
    if (
        not isinstance(target_definition_sha256, str)
        or len(target_definition_sha256) != 64
        or any(character not in "0123456789abcdef" for character in target_definition_sha256)
    ):
        benchmark_binding_failures.append(
            "release benchmark definition hash is not bound"
        )
    if len(benchmark_records) > 1:
        benchmark_failures.append("exactly one benchmark replicate report is required")
    if benchmark is not None:
        if benchmark.get("suite_id") != target_suite_id:
            benchmark_binding_failures.append(
                "benchmark replicate suite does not match the release target"
            )
        if (
            benchmark.get("benchmark_definition_sha256")
            != target_definition_sha256
        ):
            benchmark_binding_failures.append(
                "benchmark replicate definition hash does not match the release target"
            )
        run_sources = authority_sources.get("benchmark_run_directories")
        source_directories: list[Path] = []
        if not isinstance(run_sources, list) or len(run_sources) < 3:
            benchmark_binding_failures.append(
                "benchmark source run directories are not bound"
            )
        else:
            try:
                source_directories = [
                    _contained_path(root, relative)
                    for relative in run_sources
                    if isinstance(relative, str)
                ]
            except ValueError as exc:
                benchmark_binding_failures.append(str(exc))
            if len(source_directories) != len(run_sources) or any(
                not path.is_dir() for path in source_directories
            ):
                benchmark_binding_failures.append(
                    "benchmark source run directory is missing or invalid"
                )
        if source_directories and not benchmark_binding_failures:
            benchmark_binding_failures.extend(
                validate_e3_authority(
                    benchmark,
                    source_directories,
                    receipt_verifier=model_receipt_verifier,
                )
            )
        coverage = benchmark.get("coverage")
        if not isinstance(coverage, dict):
            coverage = {}
            benchmark_failures.append("replicate coverage is missing")
        if coverage.get("domain_count", 0) < 12:
            benchmark_failures.append("fewer than 12 domains were completed")
        if coverage.get("case_count", 0) < 60:
            benchmark_failures.append("fewer than 60 cases were completed")
        completed_domains = coverage.get("domains", [])
        if not isinstance(completed_domains, list) or len(completed_domains) < 12:
            benchmark_failures.append("completed domain list has fewer than 12 domains")
        domain_set_failure = _release_domain_set_failure(completed_domains)
        if domain_set_failure is not None:
            benchmark_failures.append(domain_set_failure)
        aggregate = benchmark.get("aggregate")
        actual_model_calls = (
            aggregate.get("actual_model_calls")
            if isinstance(aggregate, dict)
            else None
        )
        normalized_model_calls = _normalized_json_integer(actual_model_calls)
        if normalized_model_calls is None or normalized_model_calls <= 0:
            benchmark_failures.append("no real model calls are recorded")
    benchmark_failures.extend(benchmark_binding_failures)
    requirements.append(
        _requirement(
            *REQUIREMENTS[2],
            relevant_count=len(benchmark_records),
            evidence=benchmark_evidence,
            failures=benchmark_failures,
            invalid_evidence="benchmark_replicate" in invalid_kinds,
        )
    )

    quality_failures: list[str] = []
    quality_failures.extend(benchmark_binding_failures)
    if benchmark is not None and domain_set_failure is not None:
        quality_failures.append(domain_set_failure)
    if benchmark is not None:
        aggregate = benchmark.get("aggregate")
        if not isinstance(aggregate, dict):
            aggregate = {}
            quality_failures.append("replicate aggregate is missing")
        if aggregate.get("release_gate_passed") is not True:
            quality_failures.append("aggregate improvement gate did not pass")
        if aggregate.get("all_domain_gates_passed") is not True:
            quality_failures.append("at least one domain gate did not pass")
        if aggregate.get("net_improvement", 0.0) < 0.10:
            quality_failures.append("aggregate net improvement is below 10%")
        if aggregate.get("critical_regressions") != 0:
            quality_failures.append("critical regressions are present")
        if aggregate.get("fatal_flaws") != 0:
            quality_failures.append("fatal flaws are present")
        if aggregate.get("optimized_hard_failures") != 0:
            quality_failures.append("optimized outputs fail authoritative hard checks")
    requirements.append(
        _requirement(
            *REQUIREMENTS[3],
            relevant_count=len(benchmark_records),
            evidence=benchmark_evidence,
            failures=quality_failures,
            invalid_evidence="benchmark_replicate" in invalid_kinds,
        )
    )

    code_records = artifacts.get("code_execution", [])
    code_report = code_records[0][1] if code_records else None
    code_evidence, code, code_failures = _singleton_facts(
        artifacts,
        "code_execution",
    )
    if code is not None:
        eligible = _normalized_json_integer(code.get("eligible_cases"))
        executed = _normalized_json_integer(code.get("executed_cases"))
        passed_cases = _normalized_json_integer(code.get("passed_cases"))
        if eligible is None or eligible < 5:
            code_failures.append("fewer than five software cases are eligible")
        if eligible is None or executed != eligible:
            code_failures.append("not all eligible software cases were executed")
        if eligible is None or passed_cases != eligible:
            code_failures.append("not all executed software cases passed")
        if code.get("sandboxed") is not True:
            code_failures.append("sandboxed execution is not proven")
        executable = _normalized_json_integer(code.get("executable_cases"))
        sandboxed_cases = _normalized_json_integer(code.get("sandboxed_cases"))
        if executable is None or executable < 4:
            code_failures.append("fewer than four software cases are executable")
        if executable is None or sandboxed_cases != executable:
            code_failures.append("not all executable software cases were sandboxed")
        sandbox = code.get("sandbox")
        if not isinstance(sandbox, dict):
            code_failures.append("sandbox evidence is missing")
        else:
            if sandbox.get("backend") != "docker":
                code_failures.append("Docker sandbox backend is not proven")
            if sandbox.get("policy_verified") is not True:
                code_failures.append("sandbox runtime policy is not verified")
            if sandbox.get("isolation_probe_passed") is not True:
                code_failures.append("sandbox isolation probe did not pass")
            isolation_facts = sandbox.get("isolation_facts")
            required_isolation_facts = {
                "network_blocked",
                "root_read_only",
                "tmp_writable",
                "non_root",
            }
            if (
                not isinstance(isolation_facts, dict)
                or any(
                    isolation_facts.get(name) is not True
                    for name in required_isolation_facts
                )
            ):
                code_failures.append("sandbox isolation facts are incomplete")
            if sandbox.get("resource_limits_verified") is not True:
                code_failures.append("sandbox resource limits are not verified")
            image_reference = sandbox.get("image_reference")
            image_id = sandbox.get("image_id")
            if (
                not isinstance(image_reference, str)
                or "@sha256:" not in image_reference
            ):
                code_failures.append("sandbox image reference is not digest-pinned")
            if (
                not isinstance(image_id, str)
                or not image_id.startswith("sha256:")
                or len(image_id) != 71
            ):
                code_failures.append("sandbox image id is invalid")
        plan_relative = authority_sources.get("code_execution_plan")
        if not isinstance(plan_relative, str) or not plan_relative:
            code_failures.append("code-execution source plan is not bound")
        else:
            try:
                from .software_evidence import validate_code_execution_authority

                plan_path = _contained_path(root, plan_relative)
                plan = load_strict_json_object(
                    plan_path,
                    label="code-execution source plan",
                    preserve_decimal=False,
                )
                code_failures.extend(
                    validate_code_execution_authority(
                        code_report,
                        plan,
                        root=plan_path.parent,
                        sandbox=software_sandbox,
                    )
                )
            except (OSError, ValueError) as exc:
                code_failures.append(
                    f"code-execution source plan is invalid: {exc}"
                )
    requirements.append(
        _requirement(
            *REQUIREMENTS[4],
            relevant_count=1 if code is not None else 0,
            evidence=code_evidence,
            failures=code_failures,
            invalid_evidence="code_execution" in invalid_kinds,
        )
    )

    image_records = artifacts.get("image_review", [])
    image_report = image_records[0][1] if image_records else None
    image_evidence, image, image_failures = _singleton_facts(
        artifacts,
        "image_review",
    )
    if image is not None:
        eligible = _normalized_json_integer(image.get("eligible_cases"))
        generated = _normalized_json_integer(image.get("generated_cases"))
        reviewed = _normalized_json_integer(image.get("reviewed_cases"))
        qualified = _normalized_json_integer(image.get("qualified_reviewers"))
        wins = _normalized_json_integer(image.get("wins"))
        ties = _normalized_json_integer(image.get("ties"))
        losses = _normalized_json_integer(image.get("losses"))
        if eligible is None or eligible < 5:
            image_failures.append("fewer than five image cases are eligible")
        if eligible is None or generated != eligible:
            image_failures.append("not all image cases generated actual images")
        if eligible is None or reviewed != eligible:
            image_failures.append("not all generated images were reviewed")
        if qualified is None or qualified < 3:
            image_failures.append("fewer than three qualified visual reviewers")
        if image.get("generation_receipts_verified") is not True:
            image_failures.append("image-generation receipts are not verified")
        if image.get("reviewer_receipts_verified") is not True:
            image_failures.append("visual-reviewer receipts are not verified")
        if image.get("blind") is not True:
            image_failures.append("visual review was not blind")
        if image.get("asset_integrity_verified") is not True:
            image_failures.append("image asset integrity is not verified")
        if image.get("review_coverage_verified") is not True:
            image_failures.append("image review coverage is not verified")
        if image.get("unresolved_cases"):
            image_failures.append("visual-review disagreements remain unresolved")
        if any(value is None or value < 0 for value in (wins, ties, losses)):
            image_failures.append("visual-review outcome counts are invalid")
        elif reviewed is None or wins + ties + losses != reviewed:
            image_failures.append(
                "visual-review outcome counts do not match reviewed cases"
            )
        elif wins <= losses:
            image_failures.append(
                "optimized images do not win more reviewed cases than they lose"
            )
        if not image_failures:
            from .image_review import (
                validate_image_evidence_assets,
                validate_visual_review_authority,
            )

            image_failures.extend(
                validate_image_evidence_assets(image, root=root)
            )
            plan_relative = authority_sources.get("visual_review_plan")
            if not isinstance(plan_relative, str) or not plan_relative:
                image_failures.append("visual-review source plan is not bound")
            else:
                try:
                    plan_path = _contained_path(root, plan_relative)
                    plan = load_strict_json_object(
                        plan_path,
                        label="visual-review source plan",
                        preserve_decimal=False,
                    )
                    image_failures.extend(
                        validate_visual_review_authority(
                            image_report,
                            plan,
                            root=plan_path.parent,
                            generation_receipt_verifier=(
                                image_generation_receipt_verifier
                            ),
                            reviewer_submission_verifier=(
                                visual_reviewer_submission_verifier
                            ),
                        )
                    )
                except (OSError, ValueError) as exc:
                    image_failures.append(
                        f"visual-review source plan is invalid: {exc}"
                    )
    requirements.append(
        _requirement(
            *REQUIREMENTS[5],
            relevant_count=1 if image is not None else 0,
            evidence=image_evidence,
            failures=image_failures,
            invalid_evidence="image_review" in invalid_kinds,
        )
    )

    human_records = artifacts.get("human_review", [])
    human_evidence = [path for path, _ in human_records]
    expert_evidence, expert, expert_failures = _singleton_facts(
        artifacts,
        "expert_review_coverage",
    )
    expert_failures = list(expert_failures)
    human = human_records[0][1] if human_records else None
    if len(human_records) > 1:
        expert_failures.append("exactly one human review report is required")
    if human is not None:
        expert_failures.extend(
            validate_human_review_report(
                human,
                replicate_report=benchmark,
            )
        )
        if human.get("e4_ready") is not True:
            expert_failures.append("human review is not E4-ready")
        reviewer_count = _normalized_json_integer(human.get("reviewer_count"))
        reviewed_case_count = _normalized_json_integer(
            human.get("reviewed_case_count")
        )
        if reviewer_count is None or reviewer_count < 3:
            expert_failures.append("fewer than three independent reviewers")
        if reviewed_case_count is None or reviewed_case_count < 24:
            expert_failures.append("fewer than 24 stratified cases were reviewed")
        if human.get("unresolved_cases"):
            expert_failures.append("human-review disagreements remain unresolved")
        plan_relative = authority_sources.get("human_review_plan")
        if not isinstance(plan_relative, str) or not plan_relative:
            expert_failures.append("human-review source plan is not bound")
        else:
            try:
                plan_path = _contained_path(root, plan_relative)
                plan = load_strict_json_object(
                    plan_path,
                    label="human-review source plan",
                    preserve_decimal=False,
                )
                expert_failures.extend(
                    validate_human_review_authority(
                        human,
                        plan,
                        root=plan_path.parent,
                        model_receipt_verifier=model_receipt_verifier,
                        reviewer_submission_verifier=reviewer_submission_verifier,
                    )
                )
            except (OSError, ValueError) as exc:
                expert_failures.append(f"human-review source plan is invalid: {exc}")
    if expert is not None:
        required_domains = REQUIRED_EXPERT_DOMAINS
        expert_domains = expert.get("domains")
        observed = set(expert_domains) if isinstance(expert_domains, list) else set()
        if not required_domains.issubset(observed):
            expert_failures.append("required expert-review domains are incomplete")
        if expert.get("blind") is not True:
            expert_failures.append("expert review was not blind")
        expert_qualified = _normalized_json_integer(
            expert.get("qualified_reviewers")
        )
        if expert_qualified is None or expert_qualified < 3:
            expert_failures.append("expert qualification coverage is below three")
        qualified_reviewer_ids = expert.get("reviewer_ids")
        if (
            not isinstance(qualified_reviewer_ids, list)
            or any(
                not isinstance(reviewer_id, str) or not reviewer_id.strip()
                for reviewer_id in qualified_reviewer_ids
            )
            or qualified_reviewer_ids != sorted(set(qualified_reviewer_ids))
        ):
            expert_failures.append("expert reviewer identities are invalid")
            qualified_reviewer_ids = []
        if expert.get("qualified_reviewers") != len(qualified_reviewer_ids):
            expert_failures.append("expert reviewer count does not match identities")
        if human is not None:
            if qualified_reviewer_ids != human.get("reviewer_ids"):
                expert_failures.append(
                    "expert reviewer identities do not match human review"
                )
            human_domains = {
                item.get("domain")
                for item in human.get("case_coverage", [])
                if isinstance(item, dict)
            }
            if not required_domains.issubset(human_domains):
                expert_failures.append(
                    "human review does not cover all required expert domains"
                )
    relevant_expert = int(human is not None) + int(expert is not None)
    requirements.append(
        _requirement(
            *REQUIREMENTS[6],
            relevant_count=relevant_expert,
            evidence=human_evidence + expert_evidence,
            failures=expert_failures
            + (["human review report is missing"] if human is None and expert else [])
            + (["expert coverage report is missing"] if expert is None and human else []),
            invalid_evidence=bool(
                {"human_review", "expert_review_coverage"} & invalid_kinds
            ),
        )
    )

    reproduction_records = _custom(artifacts, "independent_reproduction")
    reproduction_evidence = [path for path, _ in reproduction_records]
    reproduction_failures: list[str] = []
    machines: set[str] = set()
    operators: set[str] = set()
    reproduction_receipts: set[str] = set()
    for _, record in reproduction_records:
        facts = record.get("facts", {})
        machine_id_hash = facts.get("machine_id_hash")
        operator_id_hash = facts.get("operator_id_hash")
        if _valid_sha256(machine_id_hash):
            machines.add(machine_id_hash)
        else:
            reproduction_failures.append(
                f"{record.get('report_id')}: machine identity hash is invalid"
            )
        if _valid_sha256(operator_id_hash):
            operators.add(operator_id_hash)
        else:
            reproduction_failures.append(
                f"{record.get('report_id')}: operator identity hash is invalid"
            )
        reproduction_failures.extend(
            f"{record.get('report_id')}: {name} is not proven"
            for name in _all_true(facts, ("install_passed", "replay_passed"))
        )
        receipt, receipt_failure = _verify_attestation_receipt(
            record,
            identity={
                "machine_id_hash": machine_id_hash,
                "operator_id_hash": operator_id_hash,
            },
            verifier=reproduction_attestation_verifier,
            label="reproduction attestation",
        )
        if receipt_failure is not None:
            reproduction_failures.append(
                f"{record.get('report_id')}: {receipt_failure}"
            )
        elif receipt in reproduction_receipts:
            reproduction_failures.append(
                f"{record.get('report_id')}: reproduction attestation receipt is reused"
            )
        else:
            assert receipt is not None
            reproduction_receipts.add(receipt)
    if reproduction_records and len(machines) < 3:
        reproduction_failures.append("fewer than three independent machines")
    if reproduction_records and len(operators) < 3:
        reproduction_failures.append("fewer than three independent operators")
    requirements.append(
        _requirement(
            *REQUIREMENTS[7],
            relevant_count=len(reproduction_records),
            evidence=reproduction_evidence,
            failures=reproduction_failures,
            invalid_evidence="independent_reproduction" in invalid_kinds,
        )
    )

    defect_evidence, defects, defect_failures = _singleton_facts(
        artifacts,
        "defect_register",
    )
    if defects is not None:
        if defects.get("open_p0") != 0:
            defect_failures.append("open P0 defects remain")
        if defects.get("open_p1") != 0:
            defect_failures.append("open P1 defects remain")
        if defects.get("triage_complete") is not True:
            defect_failures.append("defect triage is incomplete")
    requirements.append(
        _requirement(
            *REQUIREMENTS[8],
            relevant_count=1 if defects is not None else 0,
            evidence=defect_evidence,
            failures=defect_failures,
            invalid_evidence="defect_register" in invalid_kinds,
        )
    )

    claim_evidence, claims, claim_failures = _singleton_facts(
        artifacts,
        "claims_audit",
    )
    claim_receipts: set[str] = set()
    for _, record in _custom(artifacts, "claims_audit"):
        provenance = record.get("provenance")
        producer = provenance.get("producer") if isinstance(provenance, dict) else None
        receipt, receipt_failure = _verify_attestation_receipt(
            record,
            identity={"producer": producer},
            verifier=claims_audit_verifier,
            label="claims audit",
        )
        if receipt_failure is not None:
            claim_failures.append(f"{record.get('report_id')}: {receipt_failure}")
        elif receipt in claim_receipts:
            claim_failures.append(
                f"{record.get('report_id')}: claims audit receipt is reused"
            )
        else:
            assert receipt is not None
            claim_receipts.add(receipt)
    if claims is not None:
        if claims.get("unsupported_claims") != 0:
            claim_failures.append("unsupported public claims remain")
        if claims.get("all_claims_artifact_bound") is not True:
            claim_failures.append("not all public claims are artifact-bound")
        if claims.get("documentation_scanned") is not True:
            claim_failures.append("public documentation scan is incomplete")
    requirements.append(
        _requirement(
            *REQUIREMENTS[9],
            relevant_count=1 if claims is not None else 0,
            evidence=claim_evidence,
            failures=claim_failures,
            invalid_evidence="claims_audit" in invalid_kinds,
        )
    )

    passed = sum(item["status"] == "passed" for item in requirements)
    complete = passed == len(REQUIREMENTS) and not evidence_errors
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": "complete" if complete else "incomplete",
        "claim_ceiling": "stable_v1" if complete else "optimized_candidate",
        "completion_metric": "unweighted mandatory gate ratio",
        "passed_requirement_count": passed,
        "requirement_count": len(REQUIREMENTS),
        "completion_ratio": passed / len(REQUIREMENTS),
        "source_manifest_sha256": manifest.get("manifest_sha256"),
        "requirements": requirements,
        "evidence_errors": evidence_errors,
    }
    report["readiness_sha256"] = hash_payload(report, "readiness_sha256")
    return report


def _validate_readiness_report(report: Any) -> list[str]:
    if not isinstance(report, dict):
        return ["readiness report root must be an object"]
    failures: list[str] = []
    expected_fields = {
        "schema_version",
        "status",
        "claim_ceiling",
        "completion_metric",
        "passed_requirement_count",
        "requirement_count",
        "completion_ratio",
        "source_manifest_sha256",
        "requirements",
        "evidence_errors",
        "readiness_sha256",
    }
    if set(report) != expected_fields:
        failures.append("readiness report fields do not match the schema")
    if report.get("schema_version") != SCHEMA_VERSION:
        failures.append("unsupported readiness report schema")
    if report.get("readiness_sha256") != hash_payload(
        report,
        "readiness_sha256",
    ):
        failures.append("readiness report hash mismatch")
    source_manifest_sha256 = report.get("source_manifest_sha256")
    if (
        not isinstance(source_manifest_sha256, str)
        or len(source_manifest_sha256) != 64
        or any(character not in "0123456789abcdef" for character in source_manifest_sha256)
    ):
        failures.append("readiness source manifest hash is invalid")
    if report.get("completion_metric") != "unweighted mandatory gate ratio":
        failures.append("readiness completion metric is invalid")
    evidence_errors = report.get("evidence_errors")
    if not isinstance(evidence_errors, list) or any(
        not isinstance(value, str) or not value for value in evidence_errors
    ):
        failures.append("readiness evidence error list is invalid")
        evidence_errors = []
    requirements = report.get("requirements")
    if not isinstance(requirements, list):
        return failures + ["requirements must be an array"]
    expected_ids = [item[0] for item in REQUIREMENTS]
    observed_ids = [item.get("id") for item in requirements if isinstance(item, dict)]
    if observed_ids != expected_ids:
        failures.append("readiness requirement ids are incomplete or out of order")
    for item, (expected_id, expected_title) in zip(
        requirements,
        REQUIREMENTS,
        strict=False,
    ):
        if not isinstance(item, dict):
            continue
        if set(item) != {"id", "title", "status", "evidence", "failures"}:
            failures.append(f"{expected_id}: readiness requirement fields are invalid")
        if item.get("id") != expected_id or item.get("title") != expected_title:
            failures.append(f"readiness requirement contract mismatch: {expected_id}")
        evidence = item.get("evidence")
        item_failures = item.get("failures")
        if (
            not isinstance(evidence, list)
            or any(not isinstance(value, str) or not value for value in evidence)
            or evidence != sorted(evidence)
            or len(evidence) != len(set(evidence))
        ):
            failures.append(f"{expected_id}: readiness evidence list is invalid")
            evidence = []
        if not isinstance(item_failures, list) or any(
            not isinstance(value, str) or not value for value in item_failures
        ):
            failures.append(f"{expected_id}: readiness failure list is invalid")
            item_failures = []
        status = item.get("status")
        if status == "passed" and (not evidence or item_failures):
            failures.append(f"{expected_id}: passed status lacks clean evidence")
        elif status == "partial" and (not evidence or not item_failures):
            failures.append(f"{expected_id}: partial status is not evidence-backed")
        elif status == "missing" and evidence:
            failures.append(f"{expected_id}: missing status contains evidence")
        elif status == "failed" and not evidence_errors:
            failures.append(f"{expected_id}: failed status lacks an evidence error")
    allowed_statuses = {"passed", "partial", "missing", "failed"}
    if any(
        not isinstance(item, dict) or item.get("status") not in allowed_statuses
        for item in requirements
    ):
        failures.append("readiness requirement status is invalid")
    passed = sum(
        isinstance(item, dict) and item.get("status") == "passed"
        for item in requirements
    )
    passed_requirement_count = report.get("passed_requirement_count")
    if _normalized_json_integer(passed_requirement_count) != passed:
        failures.append("passed requirement count does not match requirements")
    requirement_count = report.get("requirement_count")
    if _normalized_json_integer(requirement_count) != len(REQUIREMENTS):
        failures.append("requirement count does not match the release contract")
    expected_complete = passed == len(REQUIREMENTS) and not evidence_errors
    status = report.get("status")
    if not isinstance(status, str) or status not in {"complete", "incomplete"}:
        failures.append("readiness status is invalid")
    if (report.get("status") == "complete") != expected_complete:
        failures.append("readiness status does not match mandatory gates")
    expected_ceiling = "stable_v1" if expected_complete else "optimized_candidate"
    if report.get("claim_ceiling") != expected_ceiling:
        failures.append("claim ceiling does not match readiness status")
    expected_ratio = passed / len(REQUIREMENTS)
    completion_ratio = report.get("completion_ratio")
    if (
        not isinstance(completion_ratio, (int, float))
        or isinstance(completion_ratio, bool)
        or completion_ratio != expected_ratio
    ):
        failures.append("completion ratio does not match mandatory gates")
    return failures


def validate_readiness_report(report: Any) -> list[str]:
    """Validate untrusted readiness input without leaking structural errors."""
    try:
        return _validate_readiness_report(report)
    except (
        ArithmeticError,
        AttributeError,
        KeyError,
        RecursionError,
        TypeError,
        UnicodeError,
        ValueError,
    ) as exc:
        return [f"malformed readiness report: {exc}"]


def validate_readiness_authority(
    report: dict[str, Any],
    manifest: dict[str, Any],
    *,
    root: Path,
    model_receipt_verifier: ModelCallReceiptVerifier | None = None,
    reviewer_submission_verifier: ReviewerSubmissionVerifier | None = None,
    software_sandbox: DockerSandbox | None = None,
    image_generation_receipt_verifier: ImageGenerationReceiptVerifier | None = None,
    visual_reviewer_submission_verifier: VisualReviewerSubmissionVerifier | None = None,
    reproduction_attestation_verifier: ReproductionAttestationVerifier | None = None,
    claims_audit_verifier: ClaimsAuditVerifier | None = None,
) -> list[str]:
    """Reassess every bound source artifact and compare the readiness result."""
    failures = validate_readiness_report(report)
    if failures:
        return failures
    manifest_failures = validate_readiness_manifest(manifest)
    if manifest_failures:
        return manifest_failures
    assert isinstance(manifest, dict)
    if report.get("source_manifest_sha256") != manifest.get("manifest_sha256"):
        return ["readiness report does not match the supplied source manifest"]
    try:
        rebuilt = assess_readiness(
            manifest,
            root=root,
            model_receipt_verifier=model_receipt_verifier,
            reviewer_submission_verifier=reviewer_submission_verifier,
            software_sandbox=software_sandbox,
            image_generation_receipt_verifier=(
                image_generation_receipt_verifier
            ),
            visual_reviewer_submission_verifier=(
                visual_reviewer_submission_verifier
            ),
            reproduction_attestation_verifier=reproduction_attestation_verifier,
            claims_audit_verifier=claims_audit_verifier,
        )
    except (
        ArithmeticError,
        AttributeError,
        OSError,
        RecursionError,
        TypeError,
        UnicodeError,
        ValueError,
    ) as exc:
        return [f"readiness source manifest is invalid: {exc}"]
    if rebuilt != report:
        failures.append("readiness report does not match its source manifest")
    return failures
