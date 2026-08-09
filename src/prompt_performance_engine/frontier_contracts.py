"""Strict, dependency-free contracts for a frozen frontier campaign preflight."""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .frontier_io import (
    DEFAULT_MAX_ARTIFACT_BYTES,
    FrontierArtifactIOError,
    _resolved_root,
    _safe_relative_path,
    load_authority_artifact,
    load_contained_file,
    resolve_contained_directory,
)
from .hashing import hash_payload, sha256_json


FRONTIER_CONTRACT_SCHEMA_VERSION = "1.0.0"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
OCI_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
UTC_TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$"
)
MAX_SAFE_INTEGER = 9_007_199_254_740_991
MAX_CAMPAIGN_FILE_BYTES = DEFAULT_MAX_ARTIFACT_BYTES

CAMPAIGN_PLAN_FIELDS = frozenset(
    {
        "schema_version",
        "campaign_id",
        "dataset_id",
        "owner_id",
        "dataset_custodian_id",
        "release_policy_path",
        "release_policy_sha256",
        "source_commitment_path",
        "source_commitment_sha256",
        "analysis_implementation_path",
        "analysis_implementation_sha256",
        "analysis_sha256",
        "lifecycle",
        "candidate_artifacts",
        "models",
        "systems",
        "judges",
        "budgets",
        "case_routes",
        "host_capabilities",
        "human_gold",
        "pricing",
        "attestations",
        "storage",
        "campaign_sha256",
    }
)
RELEASE_POLICY_FIELDS = frozenset(
    {
        "schema_version",
        "policy_id",
        "primary_budget_dimension",
        "required_strong_baseline_ids",
        "required_strong_baseline_kinds",
        "required_judge_count",
        "require_judge_family_independence",
        "require_judge_provider_independence",
        "require_dual_position",
        "allowed_authority_routes",
        "human_gold",
        "statistical_design",
        "analysis",
        "policy_sha256",
    }
)
SOURCE_COMMITMENT_FIELDS = frozenset(
    {
        "schema_version",
        "dataset_id",
        "case_commitments",
        "license_review_sha256",
        "license_review_path",
        "exclusion_rules_sha256",
        "exclusion_rules_path",
        "contamination_check_sha256",
        "contamination_check_path",
        "target_distribution_sha256",
        "target_distribution_path",
        "utility_anchors_sha256",
        "utility_anchors_path",
        "power_analysis_sha256",
        "power_analysis_path",
        "minimum_calibration_cases",
        "robustness_pairs",
        "analysis_sha256",
        "sealed_access_policy_sha256",
        "sealed_access_policy_path",
        "sealed_content_disclosed",
        "commitment_sha256",
    }
)

SYSTEM_FIELDS = frozenset(
    {
        "system_id",
        "kind",
        "version",
        "implementation_sha256",
        "optimizer_model_id",
        "generation_model_id",
        "strong_baseline",
        "provenance",
    }
)
MODEL_FIELDS = frozenset(
    {
        "model_id",
        "provider",
        "family",
        "snapshot",
        "operator",
        "parameters_path",
        "parameters_sha256",
        "retry_policy_path",
        "retry_policy_sha256",
        "cache_policy_path",
        "cache_policy_sha256",
        "seeds_path",
        "seeds_sha256",
        "pricing_entry_path",
        "pricing_entry_sha256",
    }
)
PROVENANCE_FIELDS = frozenset(
    {
        "source_path",
        "source_sha256",
        "source_revision",
        "configuration_path",
        "configuration_sha256",
        "search_space_path",
        "search_space_sha256",
        "stopping_rule_path",
        "stopping_rule_sha256",
        "dependency_lock_path",
        "dependency_lock_sha256",
        "environment_path",
        "environment_sha256",
        "license_eligibility_path",
        "license_eligibility_sha256",
        "track",
        "input_visibility",
    }
)
JUDGE_FIELDS = frozenset(
    {
        "judge_id",
        "model_id",
        "prompt_path",
        "prompt_sha256",
        "rubric_path",
        "rubric_sha256",
        "parser_path",
        "parser_sha256",
        "calibration_path",
        "calibration_sha256",
        "receipt_authority_id",
        "positions",
    }
)
BUDGET_FIELDS = frozenset(
    {
        "system_id",
        "primary_dimension",
        "ceilings",
        "components",
        "worst_case",
    }
)
BUDGET_COMPONENT_FIELDS = frozenset(
    {
        "component_id",
        "role",
        "model_ids",
        "purposes",
        "unit_count",
        "attempts_per_unit",
        "max_input_tokens_per_attempt",
        "max_output_tokens_per_attempt",
        "max_money_microunits_per_attempt",
        "max_wall_clock_ms_per_attempt",
    }
)
RESOURCE_FIELDS = (
    "calls",
    "input_tokens",
    "output_tokens",
    "money_microunits",
    "wall_clock_ms",
)
CASE_ROUTE_FIELDS = frozenset({"case_id", "route", "authority_id"})
HOST_CAPABILITY_FIELDS = frozenset(
    {
        "route",
        "authority_id",
        "verifier_id",
        "configuration_path",
        "configuration_sha256",
        "runtime_image_digest",
        "sandbox_policy_path",
        "sandbox_policy_sha256",
        "visual_verifier_id",
        "visual_calibration_path",
        "visual_calibration_sha256",
    }
)
HUMAN_GOLD_FIELDS = frozenset(
    {
        "expert_reviewer_ids",
        "senior_adjudicator_ids",
        "gold_set_path",
        "gold_set_sha256",
        "review_protocol_path",
        "review_protocol_sha256",
        "expert_eligibility_path",
        "expert_eligibility_sha256",
        "independence_attestation_path",
        "independence_attestation_sha256",
        "calibration_path",
        "calibration_sha256",
        "receipt_authority_id",
        "verifier_id",
    }
)
HUMAN_GOLD_POLICY_FIELDS = frozenset(
    {
        "ordinary_minimum_expert_reviewers",
        "critical_minimum_expert_reviewers",
        "require_senior_adjudication",
        "minimum_calibration_cases",
        "minimum_order_consistency_ppm",
        "minimum_human_majority_agreement_ppm",
        "minimum_agreement_coefficient_ppm",
        "minimum_critical_recall_ppm",
        "maximum_false_negative_rate_ppm",
        "maximum_length_bias_ppm",
    }
)
PRICING_FIELDS = frozenset(
    {
        "currency",
        "money_unit",
        "microunits_per_currency_unit",
        "price_snapshot_path",
        "price_snapshot_sha256",
    }
)
LIFECYCLE_FIELDS = frozenset(
    {
        "created_at",
        "evidence_expires_at",
        "responsible_operator_id",
        "expiry_policy_path",
        "expiry_policy_sha256",
    }
)
CANDIDATE_ARTIFACT_FIELDS = frozenset(
    {
        "package_path",
        "package_sha256",
        "source_revision",
        "optimizer_prompt_path",
        "optimizer_prompt_sha256",
        "profile_path",
        "profile_sha256",
        "code_path",
        "code_sha256",
    }
)
STATISTICAL_DESIGN_FIELDS = frozenset(
    {
        "primary_metric",
        "minimum_meaningful_effect_ppm",
        "minimum_detectable_effect_ppm",
        "confidence_level_ppm",
        "power_target_ppm",
        "tie_policy",
        "missing_observation_policy",
        "stopping_rule_path",
        "stopping_rule_sha256",
    }
)
ATTESTATIONS_FIELDS = frozenset({"owner", "dataset_custodian"})
ATTESTATION_FIELDS = frozenset(
    {
        "subject_id",
        "authority_id",
        "attestation_id",
        "signature_path",
        "signature_sha256",
    }
)
STORAGE_FIELDS = frozenset(
    {
        "available_bytes",
        "worst_case_bytes",
        "storage_root_path",
        "authority_id",
        "verifier_id",
    }
)
ANALYSIS_FIELDS = frozenset(
    {
        "bootstrap_seed",
        "bootstrap_iterations",
        "multiplicity_method",
        "percentile_method",
        "pareto_rule",
        "safety_event_unit",
        "safety_event_limit",
    }
)
CASE_COMMITMENT_FIELDS = frozenset(
    {
        "case_id",
        "stratum_id",
        "critical",
        "task_path",
        "task_sha256",
        "scoring_anchor_path",
        "scoring_anchor_sha256",
    }
)
ROBUSTNESS_PAIR_FIELDS = frozenset({"clean_case_id", "perturbed_case_id"})

SYSTEM_KINDS = {
    "candidate",
    "identity",
    "no_op",
    "expert",
    "random_search",
    "public_optimizer",
}
REQUIRED_STRONG_BASELINE_KINDS = {
    "identity",
    "no_op",
    "expert",
    "random_search",
    "public_optimizer",
}
BUDGET_DIMENSIONS = set(RESOURCE_FIELDS)
BUDGET_COMPONENT_ROLES = {
    "optimizer",
    "generation",
    "selector",
    "repair",
    "judge",
    "image_generation",
    "tool",
    "environment",
}
AUTHORITY_ROUTES = {
    "deterministic",
    "r05_docker",
    "r06_visual",
    "environment_state",
}
ROLE_PURPOSE = {
    "optimizer": "optimization",
    "generation": "generation",
    "selector": "selection",
    "repair": "repair",
    "judge": "judging",
    "image_generation": "image_generation",
    "tool": "tool_execution",
    "environment": "environment_verification",
}


@dataclass(frozen=True)
class FrontierCampaignBundle:
    root: Path
    plan_path: Path
    release_policy_path: Path
    source_commitment_path: Path
    analysis_implementation_path: Path
    storage_root_path: Path
    analysis_implementation_sha256_actual: str
    plan: dict[str, Any]
    release_policy: dict[str, Any]
    source_commitment: dict[str, Any]


def _unique_failures(failures: list[str]) -> list[str]:
    return list(dict.fromkeys(failures))


def _exact_fields(value: Any, expected: frozenset[str], label: str) -> list[str]:
    if not isinstance(value, dict):
        return [f"{label} must be an object"]
    failures: list[str] = []
    missing = expected - set(value)
    unknown = set(value) - expected
    if missing:
        failures.append(f"{label} is missing required fields")
    if unknown:
        failures.append(f"{label} contains unknown fields")
    return failures


def _valid_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 256


def _valid_portable_relative_path(value: Any) -> bool:
    """Match the schema path contract plus the authority I/O portability rules."""

    if (
        not isinstance(value, str)
        or not value
        or len(value) > 256
        or value.strip() != value
    ):
        return False
    try:
        parsed = _safe_relative_path(value)
    except FrontierArtifactIOError:
        return False
    # pathlib normalizes repeated separators and dot components.  Authority
    # contracts must reject those aliases rather than silently rewriting them.
    return parsed.as_posix() == value


def _valid_sha256(value: Any) -> bool:
    return isinstance(value, str) and SHA256_RE.fullmatch(value) is not None


def _parse_utc_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or UTC_TIMESTAMP_RE.fullmatch(value) is None:
        return None
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return None


def _is_json_integer(
    value: Any,
    *,
    minimum: int = 0,
    maximum: int = MAX_SAFE_INTEGER,
) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        normalized = value
    elif isinstance(value, float) and math.isfinite(value) and value.is_integer():
        normalized = int(value)
    else:
        return False
    return minimum <= normalized <= maximum


def _validate_string_array(
    value: Any,
    *,
    label: str,
    minimum: int = 1,
) -> list[str]:
    if not isinstance(value, list) or len(value) < minimum:
        return [f"{label} must be a non-empty array"]
    if any(not _valid_string(item) for item in value):
        return [f"{label} must contain non-empty strings"]
    if len(value) != len(set(value)):
        return [f"{label} must contain unique values"]
    return []


def _validate_resource_vector(value: Any, *, label: str) -> list[str]:
    failures = _exact_fields(value, frozenset(RESOURCE_FIELDS), label)
    if not isinstance(value, dict):
        return failures
    for field in RESOURCE_FIELDS:
        if not _is_json_integer(value.get(field)):
            failures.append(f"{label}.{field} must be a non-negative integer")
    return failures


def _safe_hash_payload(value: Any, hash_field: str) -> str | None:
    if not isinstance(value, dict):
        return None
    try:
        return hash_payload(value, hash_field)
    except (OverflowError, RecursionError, TypeError, ValueError):
        return None


def validate_frontier_campaign_plan(plan: Any) -> list[str]:
    failures = _exact_fields(plan, CAMPAIGN_PLAN_FIELDS, "campaign plan")
    if not isinstance(plan, dict):
        return failures
    if plan.get("schema_version") != FRONTIER_CONTRACT_SCHEMA_VERSION:
        failures.append("unsupported campaign plan schema")
    for field in (
        "campaign_id",
        "dataset_id",
        "owner_id",
        "dataset_custodian_id",
    ):
        if not _valid_string(plan.get(field)):
            failures.append(f"campaign plan {field} is invalid")
    for field in (
        "release_policy_path",
        "source_commitment_path",
        "analysis_implementation_path",
    ):
        if not _valid_portable_relative_path(plan.get(field)):
            failures.append(f"campaign plan {field} is invalid")
    if plan.get("owner_id") == plan.get("dataset_custodian_id"):
        failures.append("campaign owner and dataset custodian must be distinct")
    for field in (
        "release_policy_sha256",
        "source_commitment_sha256",
        "analysis_implementation_sha256",
        "analysis_sha256",
        "campaign_sha256",
    ):
        if not _valid_sha256(plan.get(field)):
            failures.append(f"campaign plan {field} is invalid")

    lifecycle = plan.get("lifecycle")
    failures.extend(_exact_fields(lifecycle, LIFECYCLE_FIELDS, "campaign lifecycle"))
    if isinstance(lifecycle, dict):
        created_at = _parse_utc_timestamp(lifecycle.get("created_at"))
        expires_at = _parse_utc_timestamp(lifecycle.get("evidence_expires_at"))
        if created_at is None:
            failures.append("campaign creation timestamp is invalid")
        if expires_at is None:
            failures.append("campaign evidence expiry timestamp is invalid")
        if created_at is not None and expires_at is not None and created_at >= expires_at:
            failures.append("campaign evidence must expire after campaign creation")
        if not _valid_string(lifecycle.get("responsible_operator_id")):
            failures.append("campaign lifecycle responsible_operator_id is invalid")
        if not _valid_portable_relative_path(lifecycle.get("expiry_policy_path")):
            failures.append("campaign lifecycle expiry_policy_path is invalid")
        if not _valid_sha256(lifecycle.get("expiry_policy_sha256")):
            failures.append("campaign lifecycle expiry policy digest is invalid")

    candidate_artifacts = plan.get("candidate_artifacts")
    failures.extend(
        _exact_fields(
            candidate_artifacts,
            CANDIDATE_ARTIFACT_FIELDS,
            "candidate artifacts",
        )
    )
    if isinstance(candidate_artifacts, dict):
        for field in (
            "package_path",
            "optimizer_prompt_path",
            "profile_path",
            "code_path",
        ):
            if not _valid_portable_relative_path(candidate_artifacts.get(field)):
                failures.append(f"candidate artifacts {field} is invalid")
        if not _valid_string(candidate_artifacts.get("source_revision")):
            failures.append("candidate artifacts source_revision is invalid")
        for field in (
            "package_sha256",
            "optimizer_prompt_sha256",
            "profile_sha256",
            "code_sha256",
        ):
            if not _valid_sha256(candidate_artifacts.get(field)):
                failures.append(f"candidate artifacts {field} is invalid")

    models = plan.get("models")
    model_ids: list[str] = []
    if not isinstance(models, list) or not models:
        failures.append("campaign plan models must be a non-empty array")
    else:
        for model in models:
            failures.extend(_exact_fields(model, MODEL_FIELDS, "campaign model"))
            if not isinstance(model, dict):
                continue
            for field in (
                "model_id",
                "provider",
                "family",
                "snapshot",
                "operator",
            ):
                if not _valid_string(model.get(field)):
                    failures.append(f"campaign model {field} is invalid")
            for field in (
                "parameters_path",
                "retry_policy_path",
                "cache_policy_path",
                "seeds_path",
                "pricing_entry_path",
            ):
                if not _valid_portable_relative_path(model.get(field)):
                    failures.append(f"campaign model {field} is invalid")
            for field in (
                "parameters_sha256",
                "retry_policy_sha256",
                "cache_policy_sha256",
                "seeds_sha256",
                "pricing_entry_sha256",
            ):
                if not _valid_sha256(model.get(field)):
                    failures.append(f"campaign model {field} is invalid")
            if isinstance(model.get("model_id"), str):
                model_ids.append(model["model_id"])
        if len(model_ids) != len(set(model_ids)):
            failures.append("campaign model ids must be unique")

    systems = plan.get("systems")
    system_ids: list[str] = []
    if not isinstance(systems, list) or not systems:
        failures.append("campaign systems must be a non-empty array")
    else:
        for system in systems:
            failures.extend(_exact_fields(system, SYSTEM_FIELDS, "campaign system"))
            if not isinstance(system, dict):
                continue
            for field in ("system_id", "version", "implementation_sha256"):
                validator = _valid_sha256 if field == "implementation_sha256" else _valid_string
                if not validator(system.get(field)):
                    failures.append(f"campaign system {field} is invalid")
            if system.get("kind") not in SYSTEM_KINDS:
                failures.append("campaign system kind is invalid")
            optimizer_model = system.get("optimizer_model_id")
            if optimizer_model is not None and not _valid_string(optimizer_model):
                failures.append("campaign system optimizer_model_id is invalid")
            if not _valid_string(system.get("generation_model_id")):
                failures.append("campaign system generation_model_id is invalid")
            if not isinstance(system.get("strong_baseline"), bool):
                failures.append("campaign system strong_baseline must be a boolean")
            provenance = system.get("provenance")
            failures.extend(
                _exact_fields(
                    provenance,
                    PROVENANCE_FIELDS,
                    "campaign system provenance",
                )
            )
            if isinstance(provenance, dict):
                for field in (
                    "source_revision",
                    "track",
                    "input_visibility",
                ):
                    if not _valid_string(provenance.get(field)):
                        failures.append(
                            f"campaign system provenance {field} is invalid"
                        )
                for field in (
                    "source_path",
                    "configuration_path",
                    "search_space_path",
                    "stopping_rule_path",
                    "dependency_lock_path",
                    "environment_path",
                    "license_eligibility_path",
                ):
                    if not _valid_portable_relative_path(provenance.get(field)):
                        failures.append(
                            f"campaign system provenance {field} is invalid"
                        )
                for field in (
                    "source_sha256",
                    "configuration_sha256",
                    "search_space_sha256",
                    "stopping_rule_sha256",
                    "dependency_lock_sha256",
                    "environment_sha256",
                    "license_eligibility_sha256",
                ):
                    if not _valid_sha256(provenance.get(field)):
                        failures.append(
                            f"campaign system provenance {field} is invalid"
                        )
            if isinstance(system.get("system_id"), str):
                system_ids.append(system["system_id"])
        if len(system_ids) != len(set(system_ids)):
            failures.append("campaign system ids must be unique")

    judges = plan.get("judges")
    judge_ids: list[str] = []
    if not isinstance(judges, list) or len(judges) < 2:
        failures.append("campaign requires at least two judges")
    else:
        for judge in judges:
            failures.extend(_exact_fields(judge, JUDGE_FIELDS, "campaign judge"))
            if not isinstance(judge, dict):
                continue
            for field in (
                "judge_id",
                "model_id",
                "receipt_authority_id",
            ):
                if not _valid_string(judge.get(field)):
                    failures.append(f"campaign judge {field} is invalid")
            for field in (
                "prompt_path",
                "rubric_path",
                "parser_path",
                "calibration_path",
            ):
                if not _valid_portable_relative_path(judge.get(field)):
                    failures.append(f"campaign judge {field} is invalid")
            for field in (
                "prompt_sha256",
                "rubric_sha256",
                "parser_sha256",
                "calibration_sha256",
            ):
                if not _valid_sha256(judge.get(field)):
                    failures.append(f"campaign judge {field} is invalid")
            if judge.get("positions") != ["A", "B"]:
                failures.append("campaign judge must cover both A/B positions")
            if isinstance(judge.get("judge_id"), str):
                judge_ids.append(judge["judge_id"])
        if len(judge_ids) != len(set(judge_ids)):
            failures.append("campaign judge ids must be unique")

    budgets = plan.get("budgets")
    if not isinstance(budgets, list) or not budgets:
        failures.append("campaign budgets must be a non-empty array")
    else:
        for budget in budgets:
            failures.extend(_exact_fields(budget, BUDGET_FIELDS, "campaign budget"))
            if not isinstance(budget, dict):
                continue
            if not _valid_string(budget.get("system_id")):
                failures.append("campaign budget system_id is invalid")
            if budget.get("primary_dimension") not in BUDGET_DIMENSIONS:
                failures.append("campaign budget primary_dimension is invalid")
            failures.extend(
                _validate_resource_vector(budget.get("ceilings"), label="budget ceilings")
            )
            failures.extend(
                _validate_resource_vector(budget.get("worst_case"), label="budget worst_case")
            )
            components = budget.get("components")
            component_ids: list[str] = []
            if not isinstance(components, list) or not components:
                failures.append("campaign budget components must be a non-empty array")
                continue
            for component in components:
                failures.extend(
                    _exact_fields(
                        component,
                        BUDGET_COMPONENT_FIELDS,
                        "campaign budget component",
                    )
                )
                if not isinstance(component, dict):
                    continue
                if not _valid_string(component.get("component_id")):
                    failures.append("campaign budget component_id is invalid")
                elif isinstance(component.get("component_id"), str):
                    component_ids.append(component["component_id"])
                if component.get("role") not in BUDGET_COMPONENT_ROLES:
                    failures.append("campaign budget component role is invalid")
                failures.extend(
                    _validate_string_array(
                        component.get("model_ids"),
                        label="campaign budget component model_ids",
                    )
                )
                failures.extend(
                    _validate_string_array(
                        component.get("purposes"),
                        label="campaign budget component purposes",
                    )
                )
                expected_purpose = ROLE_PURPOSE.get(component.get("role"))
                if expected_purpose is not None and component.get("purposes") != [
                    expected_purpose
                ]:
                    failures.append(
                        "campaign budget component purpose does not match its role"
                    )
                for field in (
                    "unit_count",
                    "attempts_per_unit",
                ):
                    if not _is_json_integer(component.get(field), minimum=1):
                        failures.append(f"campaign budget component {field} is invalid")
                for field in (
                    "max_input_tokens_per_attempt",
                    "max_output_tokens_per_attempt",
                    "max_money_microunits_per_attempt",
                    "max_wall_clock_ms_per_attempt",
                ):
                    if not _is_json_integer(component.get(field)):
                        failures.append(f"campaign budget component {field} is invalid")
            if len(component_ids) != len(set(component_ids)):
                failures.append("campaign budget component ids must be unique")

    routes = plan.get("case_routes")
    route_ids: list[str] = []
    if not isinstance(routes, list) or not routes:
        failures.append("campaign case_routes must be a non-empty array")
    else:
        for route in routes:
            failures.extend(_exact_fields(route, CASE_ROUTE_FIELDS, "case route"))
            if not isinstance(route, dict):
                continue
            for field in ("case_id", "authority_id"):
                if not _valid_string(route.get(field)):
                    failures.append(f"case route {field} is invalid")
            if route.get("route") not in AUTHORITY_ROUTES:
                failures.append("case authority route is invalid")
            if isinstance(route.get("case_id"), str):
                route_ids.append(route["case_id"])
        if len(route_ids) != len(set(route_ids)):
            failures.append("case route ids must be unique")

    capabilities = plan.get("host_capabilities")
    capability_keys: list[tuple[str, str]] = []
    if not isinstance(capabilities, list) or not capabilities:
        failures.append("campaign host_capabilities must be a non-empty array")
    else:
        for capability in capabilities:
            failures.extend(
                _exact_fields(
                    capability,
                    HOST_CAPABILITY_FIELDS,
                    "host capability",
                )
            )
            if not isinstance(capability, dict):
                continue
            route = capability.get("route")
            authority_id = capability.get("authority_id")
            if route not in AUTHORITY_ROUTES:
                failures.append("host capability route is invalid")
            for field in ("authority_id", "verifier_id"):
                if not _valid_string(capability.get(field)):
                    failures.append(f"host capability {field} is invalid")
            if not _valid_portable_relative_path(capability.get("configuration_path")):
                failures.append("host capability configuration_path is invalid")
            if not _valid_sha256(capability.get("configuration_sha256")):
                failures.append("host capability configuration_sha256 is invalid")
            runtime_image = capability.get("runtime_image_digest")
            sandbox_path = capability.get("sandbox_policy_path")
            sandbox_policy = capability.get("sandbox_policy_sha256")
            visual_verifier = capability.get("visual_verifier_id")
            visual_calibration_path = capability.get("visual_calibration_path")
            visual_calibration = capability.get("visual_calibration_sha256")
            if runtime_image is not None and (
                not isinstance(runtime_image, str)
                or OCI_DIGEST_RE.fullmatch(runtime_image) is None
            ):
                failures.append("host capability runtime image digest is invalid")
            if sandbox_policy is not None and not _valid_sha256(sandbox_policy):
                failures.append("host capability sandbox policy digest is invalid")
            if sandbox_path is not None and not _valid_portable_relative_path(
                sandbox_path
            ):
                failures.append("host capability sandbox policy path is invalid")
            if visual_verifier is not None and not _valid_string(visual_verifier):
                failures.append("host capability visual verifier id is invalid")
            if visual_calibration is not None and not _valid_sha256(
                visual_calibration
            ):
                failures.append("host capability visual calibration is invalid")
            if visual_calibration_path is not None and not _valid_portable_relative_path(
                visual_calibration_path
            ):
                failures.append("host capability visual calibration path is invalid")
            if route == "r05_docker":
                if (
                    not isinstance(runtime_image, str)
                    or OCI_DIGEST_RE.fullmatch(runtime_image) is None
                    or not _valid_portable_relative_path(sandbox_path)
                    or not _valid_sha256(sandbox_policy)
                ):
                    failures.append(
                        "r05 Docker capability requires a pinned image and sandbox"
                    )
                if (
                    visual_verifier is not None
                    or visual_calibration_path is not None
                    or visual_calibration is not None
                ):
                    failures.append("r05 Docker capability has visual-only fields")
            elif route == "r06_visual":
                if (
                    not _valid_string(visual_verifier)
                    or not _valid_portable_relative_path(visual_calibration_path)
                    or not _valid_sha256(visual_calibration)
                ):
                    failures.append(
                        "r06 visual capability requires a verifier and calibration"
                    )
                if (
                    runtime_image is not None
                    or sandbox_path is not None
                    or sandbox_policy is not None
                ):
                    failures.append("r06 visual capability has Docker-only fields")
            elif any(
                value is not None
                for value in (
                    runtime_image,
                    sandbox_path,
                    sandbox_policy,
                    visual_verifier,
                    visual_calibration_path,
                    visual_calibration,
                )
            ):
                failures.append("host capability has fields unsupported by its route")
            if isinstance(route, str) and isinstance(authority_id, str):
                capability_keys.append((route, authority_id))
        if len(capability_keys) != len(set(capability_keys)):
            failures.append("host capability route/authority pairs must be unique")

    human_gold = plan.get("human_gold")
    failures.extend(_exact_fields(human_gold, HUMAN_GOLD_FIELDS, "human gold"))
    if isinstance(human_gold, dict):
        reviewers = human_gold.get("expert_reviewer_ids")
        adjudicators = human_gold.get("senior_adjudicator_ids")
        failures.extend(
            _validate_string_array(
                reviewers,
                label="human-gold expert reviewer ids",
                minimum=3,
            )
        )
        failures.extend(
            _validate_string_array(
                adjudicators,
                label="human-gold senior adjudicator ids",
                minimum=1,
            )
        )
        if isinstance(reviewers, list) and isinstance(adjudicators, list):
            if set(reviewers).intersection(adjudicators):
                failures.append(
                    "human-gold reviewers and senior adjudicators must be distinct"
                )
        for field in (
            "receipt_authority_id",
            "verifier_id",
        ):
            if not _valid_string(human_gold.get(field)):
                failures.append(f"human gold {field} is invalid")
        for field in (
            "gold_set_path",
            "review_protocol_path",
            "expert_eligibility_path",
            "independence_attestation_path",
            "calibration_path",
        ):
            if not _valid_portable_relative_path(human_gold.get(field)):
                failures.append(f"human gold {field} is invalid")
        for field in (
            "gold_set_sha256",
            "review_protocol_sha256",
            "expert_eligibility_sha256",
            "independence_attestation_sha256",
            "calibration_sha256",
        ):
            if not _valid_sha256(human_gold.get(field)):
                failures.append(f"human gold {field} is invalid")

    pricing = plan.get("pricing")
    failures.extend(_exact_fields(pricing, PRICING_FIELDS, "campaign pricing"))
    if isinstance(pricing, dict):
        if pricing.get("currency") != "USD":
            failures.append("campaign pricing currency must be USD")
        if pricing.get("money_unit") != "money_microunits":
            failures.append("campaign pricing money unit is unsupported")
        if pricing.get("microunits_per_currency_unit") != 1_000_000:
            failures.append("campaign pricing microunit scale is invalid")
        if not _valid_portable_relative_path(pricing.get("price_snapshot_path")):
            failures.append("campaign price snapshot path is invalid")
        if not _valid_sha256(pricing.get("price_snapshot_sha256")):
            failures.append("campaign price snapshot digest is invalid")

    attestations = plan.get("attestations")
    failures.extend(_exact_fields(attestations, ATTESTATIONS_FIELDS, "attestations"))
    if isinstance(attestations, dict):
        for role in ("owner", "dataset_custodian"):
            attestation = attestations.get(role)
            failures.extend(
                _exact_fields(attestation, ATTESTATION_FIELDS, f"{role} attestation")
            )
            if isinstance(attestation, dict):
                for field in (
                    "subject_id",
                    "authority_id",
                    "attestation_id",
                ):
                    if not _valid_string(attestation.get(field)):
                        failures.append(f"{role} attestation {field} is invalid")
                if not _valid_portable_relative_path(
                    attestation.get("signature_path")
                ):
                    failures.append(f"{role} attestation signature_path is invalid")
                if not _valid_sha256(attestation.get("signature_sha256")):
                    failures.append(f"{role} attestation signature is invalid")

    storage = plan.get("storage")
    failures.extend(_exact_fields(storage, STORAGE_FIELDS, "campaign storage"))
    if isinstance(storage, dict):
        for field in ("available_bytes", "worst_case_bytes"):
            if not _is_json_integer(storage.get(field)):
                failures.append(f"campaign storage {field} is invalid")
        for field in ("authority_id", "verifier_id"):
            if not _valid_string(storage.get(field)):
                failures.append(f"campaign storage {field} is invalid")
        if not _valid_portable_relative_path(storage.get("storage_root_path")):
            failures.append("campaign storage storage_root_path is invalid")

    expected_hash = _safe_hash_payload(plan, "campaign_sha256")
    if expected_hash is None or plan.get("campaign_sha256") != expected_hash:
        failures.append("campaign plan hash mismatch")
    return _unique_failures(failures)


def validate_frontier_release_policy(policy: Any) -> list[str]:
    failures = _exact_fields(policy, RELEASE_POLICY_FIELDS, "release policy")
    if not isinstance(policy, dict):
        return failures
    if policy.get("schema_version") != FRONTIER_CONTRACT_SCHEMA_VERSION:
        failures.append("unsupported release policy schema")
    if not _valid_string(policy.get("policy_id")):
        failures.append("release policy id is invalid")
    if policy.get("primary_budget_dimension") not in BUDGET_DIMENSIONS:
        failures.append("release policy primary budget dimension is invalid")
    failures.extend(
        _validate_string_array(
            policy.get("required_strong_baseline_ids"),
            label="required strong baseline ids",
            minimum=len(REQUIRED_STRONG_BASELINE_KINDS),
        )
    )
    if isinstance(policy.get("required_strong_baseline_ids"), list) and len(
        policy["required_strong_baseline_ids"]
    ) != len(REQUIRED_STRONG_BASELINE_KINDS):
        failures.append("release policy must name exactly five strong baselines")
    failures.extend(
        _validate_string_array(
            policy.get("required_strong_baseline_kinds"),
            label="required strong baseline kinds",
            minimum=len(REQUIRED_STRONG_BASELINE_KINDS),
        )
    )
    kinds = policy.get("required_strong_baseline_kinds")
    if isinstance(kinds, list) and set(kinds) != REQUIRED_STRONG_BASELINE_KINDS:
        failures.append("release policy does not require the complete strong-baseline set")
    if not _is_json_integer(policy.get("required_judge_count"), minimum=2):
        failures.append("release policy requires at least two judges")
    if policy.get("require_judge_family_independence") is not True:
        failures.append("release policy must require judge-family independence")
    if policy.get("require_judge_provider_independence") is not True:
        failures.append("release policy must require judge-provider independence")
    if policy.get("require_dual_position") is not True:
        failures.append("release policy must require both A/B positions")
    failures.extend(
        _validate_string_array(
            policy.get("allowed_authority_routes"),
            label="allowed authority routes",
            minimum=1,
        )
    )
    routes = policy.get("allowed_authority_routes")
    if isinstance(routes, list) and any(route not in AUTHORITY_ROUTES for route in routes):
        failures.append("release policy contains an unsupported authority route")

    human_gold = policy.get("human_gold")
    failures.extend(
        _exact_fields(
            human_gold,
            HUMAN_GOLD_POLICY_FIELDS,
            "human-gold policy",
        )
    )
    if isinstance(human_gold, dict):
        ordinary_minimum = human_gold.get("ordinary_minimum_expert_reviewers")
        critical_minimum = human_gold.get("critical_minimum_expert_reviewers")
        if not _is_json_integer(ordinary_minimum, minimum=2):
            failures.append(
                "human-gold policy requires at least two ordinary reviewers"
            )
        if not _is_json_integer(critical_minimum, minimum=3):
            failures.append(
                "human-gold policy requires at least three critical reviewers"
            )
        if (
            _is_json_integer(ordinary_minimum, minimum=2)
            and _is_json_integer(critical_minimum, minimum=3)
            and int(critical_minimum) < int(ordinary_minimum)
        ):
            failures.append(
                "critical human-gold reviewer threshold is below the ordinary threshold"
            )
        if human_gold.get("require_senior_adjudication") is not True:
            failures.append("human-gold policy must require senior adjudication")
        if not _is_json_integer(
            human_gold.get("minimum_calibration_cases"),
            minimum=1,
        ):
            failures.append("human-gold minimum calibration cases is invalid")
        for field in (
            "minimum_order_consistency_ppm",
            "minimum_human_majority_agreement_ppm",
            "minimum_agreement_coefficient_ppm",
            "minimum_critical_recall_ppm",
            "maximum_false_negative_rate_ppm",
            "maximum_length_bias_ppm",
        ):
            if not _is_json_integer(human_gold.get(field), maximum=1_000_000):
                failures.append(f"human-gold policy {field} is invalid")

    statistical_design = policy.get("statistical_design")
    failures.extend(
        _exact_fields(
            statistical_design,
            STATISTICAL_DESIGN_FIELDS,
            "statistical design",
        )
    )
    if isinstance(statistical_design, dict):
        if not _valid_string(statistical_design.get("primary_metric")):
            failures.append("statistical design primary metric is invalid")
        for field in (
            "minimum_meaningful_effect_ppm",
            "minimum_detectable_effect_ppm",
        ):
            if not _is_json_integer(
                statistical_design.get(field),
                minimum=1,
                maximum=1_000_000,
            ):
                failures.append(f"statistical design {field} is invalid")
        meaningful_effect = statistical_design.get(
            "minimum_meaningful_effect_ppm"
        )
        detectable_effect = statistical_design.get(
            "minimum_detectable_effect_ppm"
        )
        if (
            _is_json_integer(meaningful_effect, minimum=1, maximum=1_000_000)
            and _is_json_integer(detectable_effect, minimum=1, maximum=1_000_000)
            and int(detectable_effect) > int(meaningful_effect)
        ):
            failures.append(
                "statistical minimum detectable effect exceeds meaningful effect"
            )
        if not _is_json_integer(
            statistical_design.get("confidence_level_ppm"),
            minimum=500_000,
            maximum=999_999,
        ):
            failures.append("statistical confidence level is invalid")
        if not _is_json_integer(
            statistical_design.get("power_target_ppm"),
            minimum=1,
            maximum=1_000_000,
        ):
            failures.append("statistical power target is invalid")
        if statistical_design.get("tie_policy") not in {
            "zero_delta",
            "half_credit",
            "human_adjudication",
        }:
            failures.append("statistical tie policy is unsupported")
        if statistical_design.get("missing_observation_policy") != "terminal_failure":
            failures.append("statistical missing-observation policy is unsupported")
        if not _valid_portable_relative_path(
            statistical_design.get("stopping_rule_path")
        ):
            failures.append("statistical stopping rule path is invalid")
        if not _valid_sha256(statistical_design.get("stopping_rule_sha256")):
            failures.append("statistical stopping rule digest is invalid")

    analysis = policy.get("analysis")
    failures.extend(_exact_fields(analysis, ANALYSIS_FIELDS, "analysis policy"))
    if isinstance(analysis, dict):
        if not _is_json_integer(analysis.get("bootstrap_seed")):
            failures.append("analysis bootstrap seed is invalid")
        if not _is_json_integer(analysis.get("bootstrap_iterations"), minimum=1_000):
            failures.append("analysis bootstrap iterations are insufficient")
        expected_strings = {
            "multiplicity_method": "holm",
            "percentile_method": "type_7",
            "pareto_rule": "strict_non_dominated",
            "safety_event_unit": "distinct_case",
        }
        for field, expected in expected_strings.items():
            if analysis.get(field) != expected:
                failures.append(f"analysis {field} is unsupported")
        if not _is_json_integer(analysis.get("safety_event_limit")):
            failures.append("analysis safety event limit is invalid")

    expected_hash = _safe_hash_payload(policy, "policy_sha256")
    if expected_hash is None or policy.get("policy_sha256") != expected_hash:
        failures.append("release policy hash mismatch")
    return _unique_failures(failures)


def validate_frontier_source_commitment(commitment: Any) -> list[str]:
    failures = _exact_fields(
        commitment,
        SOURCE_COMMITMENT_FIELDS,
        "source commitment",
    )
    if not isinstance(commitment, dict):
        return failures
    if commitment.get("schema_version") != FRONTIER_CONTRACT_SCHEMA_VERSION:
        failures.append("unsupported source commitment schema")
    if not _valid_string(commitment.get("dataset_id")):
        failures.append("source commitment dataset_id is invalid")
    for field in (
        "license_review_sha256",
        "exclusion_rules_sha256",
        "contamination_check_sha256",
        "target_distribution_sha256",
        "utility_anchors_sha256",
        "power_analysis_sha256",
        "analysis_sha256",
        "sealed_access_policy_sha256",
        "commitment_sha256",
    ):
        if not _valid_sha256(commitment.get(field)):
            failures.append(f"source commitment {field} is invalid")
    for field in (
        "license_review_path",
        "exclusion_rules_path",
        "contamination_check_path",
        "target_distribution_path",
        "utility_anchors_path",
        "power_analysis_path",
        "sealed_access_policy_path",
    ):
        if not _valid_portable_relative_path(commitment.get(field)):
            failures.append(f"source commitment {field} is invalid")
    if commitment.get("sealed_content_disclosed") is not False:
        failures.append("sealed content disclosure attestation must be false")
    if not _is_json_integer(
        commitment.get("minimum_calibration_cases"),
        minimum=1,
    ):
        failures.append("source commitment minimum calibration cases is invalid")

    cases = commitment.get("case_commitments")
    case_ids: list[str] = []
    critical_case_count = 0
    if not isinstance(cases, list) or not cases:
        failures.append("source commitment cases must be a non-empty array")
    else:
        for case in cases:
            failures.extend(
                _exact_fields(case, CASE_COMMITMENT_FIELDS, "case commitment")
            )
            if not isinstance(case, dict):
                continue
            for field in ("case_id", "stratum_id"):
                if not _valid_string(case.get(field)):
                    failures.append(f"case commitment {field} is invalid")
            for field in ("task_path", "scoring_anchor_path"):
                if not _valid_portable_relative_path(case.get(field)):
                    failures.append(f"case commitment {field} is invalid")
            for field in ("task_sha256", "scoring_anchor_sha256"):
                if not _valid_sha256(case.get(field)):
                    failures.append(f"case commitment {field} is invalid")
            if not isinstance(case.get("critical"), bool):
                failures.append("case commitment critical flag is invalid")
            elif case["critical"]:
                critical_case_count += 1
            if isinstance(case.get("case_id"), str):
                case_ids.append(case["case_id"])
        if len(case_ids) != len(set(case_ids)):
            failures.append("case commitment ids must be unique")
        if critical_case_count == 0:
            failures.append("source commitment must include a critical case")
        minimum_calibration_cases = commitment.get("minimum_calibration_cases")
        if (
            _is_json_integer(minimum_calibration_cases, minimum=1)
            and int(minimum_calibration_cases) > len(cases)
        ):
            failures.append(
                "source commitment minimum calibration cases exceed committed cases"
            )

    robustness_pairs = commitment.get("robustness_pairs")
    pair_keys: list[tuple[str, str]] = []
    if not isinstance(robustness_pairs, list) or not robustness_pairs:
        failures.append("source commitment robustness pairs must be non-empty")
    else:
        for pair in robustness_pairs:
            failures.extend(
                _exact_fields(pair, ROBUSTNESS_PAIR_FIELDS, "robustness pair")
            )
            if not isinstance(pair, dict):
                continue
            for field in ROBUSTNESS_PAIR_FIELDS:
                if not _valid_string(pair.get(field)):
                    failures.append(f"robustness pair {field} is invalid")
            clean_case_id = pair.get("clean_case_id")
            perturbed_case_id = pair.get("perturbed_case_id")
            if clean_case_id == perturbed_case_id:
                failures.append("robustness pair must contain two distinct cases")
            if isinstance(clean_case_id, str) and isinstance(perturbed_case_id, str):
                pair_keys.append((clean_case_id, perturbed_case_id))
        if len(pair_keys) != len(set(pair_keys)):
            failures.append("source commitment robustness pairs must be unique")
        known_case_ids = set(case_ids)
        if any(
            clean not in known_case_ids or perturbed not in known_case_ids
            for clean, perturbed in pair_keys
        ):
            failures.append("robustness pair references an unknown committed case")

    expected_hash = _safe_hash_payload(commitment, "commitment_sha256")
    if expected_hash is None or commitment.get("commitment_sha256") != expected_hash:
        failures.append("source commitment hash mismatch")
    return _unique_failures(failures)


def _required_relative_path(relative: Any, *, label: str) -> str:
    if not _valid_portable_relative_path(relative):
        raise ValueError(f"{label} must be a portable contained relative path")
    return relative


def _portable_relative_path(root: Path, relative: Any, *, label: str) -> Path:
    value = _required_relative_path(relative, label=label)
    return root.joinpath(*value.split("/"))


def _portable_relative_directory(root: Path, relative: Any, *, label: str) -> Path:
    value = _required_relative_path(relative, label=label)
    return resolve_contained_directory(root, value)


def _verify_file_reference(
    root: Path,
    value: dict[str, Any],
    *,
    path_field: str,
    digest_field: str,
    label: str,
    verify_contents: bool = True,
) -> Path:
    relative = _required_relative_path(value[path_field], label=label)
    loaded = load_contained_file(
        root,
        relative,
        max_bytes=MAX_CAMPAIGN_FILE_BYTES,
    )
    if verify_contents:
        if value[digest_field] != loaded.content_sha256:
            raise ValueError(f"{label} digest mismatch")
    return root.joinpath(*relative.split("/"))


def _contained_input_relative_path(
    root: Path,
    path: Path,
    *,
    label: str,
    lexical_root: Path | None = None,
) -> str:
    try:
        raw = os.fspath(path)
    except TypeError:
        raise ValueError(f"{label} must be a path") from None
    if not isinstance(raw, str) or not raw:
        raise ValueError(f"{label} must be a path")
    candidate = Path(raw)
    if candidate.is_absolute():
        relative_path: Path | None = None
        for allowed_root in (root, lexical_root):
            if allowed_root is None:
                continue
            try:
                relative_path = candidate.relative_to(allowed_root)
                break
            except ValueError:
                continue
        if relative_path is None:
            raise ValueError(f"{label} escapes the campaign root") from None
        relative = relative_path.as_posix()
    else:
        relative = raw
    return _required_relative_path(relative, label=label)


def load_frontier_campaign_bundle(
    plan_path: Path,
    *,
    root: Path,
) -> FrontierCampaignBundle:
    """Load only contained, duplicate-free campaign sources."""

    try:
        lexical_root = root.absolute()
        resolved_root = _resolved_root(root)
    except (FrontierArtifactIOError, OSError, RuntimeError, ValueError):
        raise ValueError("campaign root is unavailable") from None
    plan_relative = _contained_input_relative_path(
        resolved_root,
        plan_path,
        label="campaign plan",
        lexical_root=lexical_root,
    )
    resolved_plan = resolved_root.joinpath(*plan_relative.split("/"))
    plan = load_authority_artifact(
        resolved_root,
        plan_relative,
        max_bytes=MAX_CAMPAIGN_FILE_BYTES,
    ).value
    plan_failures = validate_frontier_campaign_plan(plan)
    if plan_failures:
        raise ValueError("frontier campaign plan is invalid")

    release_policy_relative = _required_relative_path(
        plan["release_policy_path"],
        label="release policy path",
    )
    release_policy_path = _portable_relative_path(
        resolved_root,
        release_policy_relative,
        label="release policy path",
    )
    source_commitment_relative = _required_relative_path(
        plan["source_commitment_path"],
        label="source commitment path",
    )
    source_commitment_path = _portable_relative_path(
        resolved_root,
        source_commitment_relative,
        label="source commitment path",
    )
    analysis_relative = _required_relative_path(
        plan["analysis_implementation_path"],
        label="analysis implementation path",
    )
    analysis_path = _portable_relative_path(
        resolved_root,
        analysis_relative,
        label="analysis implementation path",
    )
    analysis_implementation_sha256_actual = load_contained_file(
        resolved_root,
        analysis_relative,
        max_bytes=MAX_CAMPAIGN_FILE_BYTES,
    ).content_sha256
    release_policy = load_authority_artifact(
        resolved_root,
        release_policy_relative,
        max_bytes=MAX_CAMPAIGN_FILE_BYTES,
    ).value
    source_commitment = load_authority_artifact(
        resolved_root,
        source_commitment_relative,
        max_bytes=MAX_CAMPAIGN_FILE_BYTES,
    ).value
    if validate_frontier_release_policy(release_policy):
        raise ValueError("frontier release policy is invalid")
    if validate_frontier_source_commitment(source_commitment):
        raise ValueError("frontier source commitment is invalid")

    _verify_file_reference(
        resolved_root,
        plan["lifecycle"],
        path_field="expiry_policy_path",
        digest_field="expiry_policy_sha256",
        label="campaign expiry policy",
    )
    for path_field, digest_field, label in (
        ("package_path", "package_sha256", "candidate package"),
        (
            "optimizer_prompt_path",
            "optimizer_prompt_sha256",
            "candidate optimizer prompt",
        ),
        ("profile_path", "profile_sha256", "candidate profile"),
        ("code_path", "code_sha256", "candidate code"),
    ):
        _verify_file_reference(
            resolved_root,
            plan["candidate_artifacts"],
            path_field=path_field,
            digest_field=digest_field,
            label=label,
        )
    for model in plan["models"]:
        for prefix in (
            "parameters",
            "retry_policy",
            "cache_policy",
            "seeds",
            "pricing_entry",
        ):
            _verify_file_reference(
                resolved_root,
                model,
                path_field=f"{prefix}_path",
                digest_field=f"{prefix}_sha256",
                label=f"model {model['model_id']} {prefix}",
            )
    for system in plan["systems"]:
        provenance = system["provenance"]
        for prefix in (
            "source",
            "configuration",
            "search_space",
            "stopping_rule",
            "dependency_lock",
            "environment",
            "license_eligibility",
        ):
            _verify_file_reference(
                resolved_root,
                provenance,
                path_field=f"{prefix}_path",
                digest_field=f"{prefix}_sha256",
                label=f"system {system['system_id']} {prefix}",
            )
    for judge in plan["judges"]:
        for prefix in ("prompt", "rubric", "parser", "calibration"):
            _verify_file_reference(
                resolved_root,
                judge,
                path_field=f"{prefix}_path",
                digest_field=f"{prefix}_sha256",
                label=f"judge {judge['judge_id']} {prefix}",
            )
    for capability in plan["host_capabilities"]:
        _verify_file_reference(
            resolved_root,
            capability,
            path_field="configuration_path",
            digest_field="configuration_sha256",
            label="host capability configuration",
        )
        for prefix in ("sandbox_policy", "visual_calibration"):
            if capability[f"{prefix}_path"] is not None:
                _verify_file_reference(
                    resolved_root,
                    capability,
                    path_field=f"{prefix}_path",
                    digest_field=f"{prefix}_sha256",
                    label=f"host capability {prefix}",
                )
    human_gold = plan["human_gold"]
    _verify_file_reference(
        resolved_root,
        human_gold,
        path_field="gold_set_path",
        digest_field="gold_set_sha256",
        label="sealed human-gold set",
        verify_contents=False,
    )
    for prefix in (
        "review_protocol",
        "expert_eligibility",
        "independence_attestation",
        "calibration",
    ):
        _verify_file_reference(
            resolved_root,
            human_gold,
            path_field=f"{prefix}_path",
            digest_field=f"{prefix}_sha256",
            label=f"human-gold {prefix}",
        )
    _verify_file_reference(
        resolved_root,
        plan["pricing"],
        path_field="price_snapshot_path",
        digest_field="price_snapshot_sha256",
        label="provider price snapshot",
    )
    for role in ("owner", "dataset_custodian"):
        _verify_file_reference(
            resolved_root,
            plan["attestations"][role],
            path_field="signature_path",
            digest_field="signature_sha256",
            label=f"{role} signature",
        )
    _verify_file_reference(
        resolved_root,
        release_policy["statistical_design"],
        path_field="stopping_rule_path",
        digest_field="stopping_rule_sha256",
        label="campaign stopping rule",
    )
    for prefix in (
        "license_review",
        "exclusion_rules",
        "contamination_check",
        "target_distribution",
        "utility_anchors",
        "power_analysis",
        "sealed_access_policy",
    ):
        _verify_file_reference(
            resolved_root,
            source_commitment,
            path_field=f"{prefix}_path",
            digest_field=f"{prefix}_sha256",
            label=f"source commitment {prefix}",
        )
    for case in source_commitment["case_commitments"]:
        for prefix in ("task", "scoring_anchor"):
            _verify_file_reference(
                resolved_root,
                case,
                path_field=f"{prefix}_path",
                digest_field=f"{prefix}_sha256",
                label=f"sealed case {prefix}",
                verify_contents=False,
            )
    storage_root_path = _portable_relative_directory(
        resolved_root,
        plan["storage"]["storage_root_path"],
        label="campaign storage root",
    )
    return FrontierCampaignBundle(
        root=resolved_root,
        plan_path=resolved_plan,
        release_policy_path=release_policy_path,
        source_commitment_path=source_commitment_path,
        analysis_implementation_path=analysis_path,
        storage_root_path=storage_root_path,
        analysis_implementation_sha256_actual=(
            analysis_implementation_sha256_actual
        ),
        plan=plan,
        release_policy=release_policy,
        source_commitment=source_commitment,
    )


def analysis_sha256(
    *,
    implementation_sha256: str,
    analysis_policy: dict[str, Any],
) -> str:
    return sha256_json(
        {
            "implementation_sha256": implementation_sha256,
            "policy": analysis_policy,
        }
    )


def _computed_budget_worst_case(budget: dict[str, Any]) -> dict[str, int] | None:
    components = budget.get("components")
    if not isinstance(components, list) or not components:
        return None
    totals = {field: 0 for field in RESOURCE_FIELDS}
    for component in components:
        if not isinstance(component, dict):
            return None
        unit_count = component.get("unit_count")
        attempts = component.get("attempts_per_unit")
        if not _is_json_integer(unit_count, minimum=1) or not _is_json_integer(
            attempts,
            minimum=1,
        ):
            return None
        attempt_count = int(unit_count) * int(attempts)
        per_attempt = {
            "input_tokens": component.get("max_input_tokens_per_attempt"),
            "output_tokens": component.get("max_output_tokens_per_attempt"),
            "money_microunits": component.get("max_money_microunits_per_attempt"),
            "wall_clock_ms": component.get("max_wall_clock_ms_per_attempt"),
        }
        if any(not _is_json_integer(value) for value in per_attempt.values()):
            return None
        totals["calls"] += attempt_count
        for field, value in per_attempt.items():
            totals[field] += attempt_count * int(value)
        if any(value > MAX_SAFE_INTEGER for value in totals.values()):
            return None
    return totals


def validate_frontier_campaign_bundle(
    bundle: FrontierCampaignBundle,
) -> list[str]:
    """Validate all frozen cross-document and preflight invariants."""

    failures: list[str] = []
    plan = bundle.plan
    policy = bundle.release_policy
    commitment = bundle.source_commitment
    failures.extend(validate_frontier_campaign_plan(plan))
    failures.extend(validate_frontier_release_policy(policy))
    failures.extend(validate_frontier_source_commitment(commitment))
    if failures:
        return _unique_failures(failures)

    if plan["dataset_id"] != commitment["dataset_id"]:
        failures.append("campaign dataset does not match its source commitment")
    if plan["release_policy_sha256"] != policy["policy_sha256"]:
        failures.append("campaign release-policy digest mismatch")
    if plan["source_commitment_sha256"] != commitment["commitment_sha256"]:
        failures.append("campaign source-commitment digest mismatch")

    implementation_digest = bundle.analysis_implementation_sha256_actual
    if plan["analysis_implementation_sha256"] != implementation_digest:
        failures.append("analysis implementation digest mismatch")
    expected_analysis = analysis_sha256(
        implementation_sha256=implementation_digest,
        analysis_policy=policy["analysis"],
    )
    if plan["analysis_sha256"] != expected_analysis:
        failures.append("campaign analysis digest mismatch")
    if commitment["analysis_sha256"] != expected_analysis:
        failures.append("source commitment analysis digest mismatch")

    policy_human_gold = policy["human_gold"]
    committed_minimum_calibration = int(commitment["minimum_calibration_cases"])
    policy_minimum_calibration = int(
        policy_human_gold["minimum_calibration_cases"]
    )
    if policy_minimum_calibration < committed_minimum_calibration:
        failures.append(
            "human-gold policy calibration minimum is below the source commitment"
        )
    if policy_minimum_calibration > len(commitment["case_commitments"]):
        failures.append(
            "human-gold policy calibration minimum exceeds committed cases"
        )

    models = {item["model_id"]: item for item in plan["models"]}
    systems = {item["system_id"]: item for item in plan["systems"]}
    if len(systems) != len(plan["systems"]):
        failures.append("campaign system ids must be unique")
    candidates = [item for item in plan["systems"] if item["kind"] == "candidate"]
    if len(candidates) != 1:
        failures.append("campaign must register exactly one candidate system")
    for system in plan["systems"]:
        for field in ("optimizer_model_id", "generation_model_id"):
            model_id = system[field]
            if model_id is not None and model_id not in models:
                failures.append("campaign system references an unknown model")
        if system["kind"] == "candidate" and system["strong_baseline"]:
            failures.append("candidate system cannot be registered as a strong baseline")

    required_baselines = set(policy["required_strong_baseline_ids"])
    registered_baselines = {
        item["system_id"] for item in plan["systems"] if item["strong_baseline"]
    }
    if registered_baselines != required_baselines:
        failures.append("registered strong baselines do not match the release policy")
    registered_baseline_kinds = {
        item["kind"] for item in plan["systems"] if item["strong_baseline"]
    }
    if not REQUIRED_STRONG_BASELINE_KINDS.issubset(registered_baseline_kinds):
        failures.append("campaign omits one or more required strong-baseline kinds")

    budgets = {item["system_id"]: item for item in plan["budgets"]}
    if len(budgets) != len(plan["budgets"]) or set(budgets) != set(systems):
        failures.append("campaign must bind exactly one budget to every system")
    reference_ceilings: dict[str, Any] | None = None
    for budget in plan["budgets"]:
        if budget["primary_dimension"] != policy["primary_budget_dimension"]:
            failures.append("campaign budget primary dimensions do not match policy")
        ceilings = budget["ceilings"]
        if reference_ceilings is None:
            reference_ceilings = ceilings
        elif ceilings != reference_ceilings:
            failures.append("campaign systems do not have equal hard budget ceilings")
        primary = policy["primary_budget_dimension"]
        if not _is_json_integer(ceilings.get(primary), minimum=1):
            failures.append("campaign primary budget ceiling must be positive")
        computed = _computed_budget_worst_case(budget)
        if computed is None or budget["worst_case"] != computed:
            failures.append("campaign worst-case budget does not match its components")
            continue
        if any(computed[field] > int(ceilings[field]) for field in RESOURCE_FIELDS):
            failures.append("campaign worst-case budget exceeds a hard ceiling")
        system = systems.get(budget["system_id"])
        if system is None:
            continue
        judge_model_ids = {judge["model_id"] for judge in plan["judges"]}
        budgeted_judge_model_ids: set[str] = set()
        principal_model_ids = {
            model_id
            for model_id in (
                system["optimizer_model_id"],
                system["generation_model_id"],
            )
            if model_id is not None
        }
        for component in budget["components"]:
            component_model_ids = set(component["model_ids"])
            if not component_model_ids.issubset(models):
                failures.append("campaign budget component references an unknown model")
            role = component["role"]
            if role == "judge":
                if not component_model_ids.issubset(judge_model_ids):
                    failures.append(
                        "judge budget component references a non-judge model"
                    )
                budgeted_judge_model_ids.update(component_model_ids)
            elif role == "optimizer":
                expected_optimizer = system["optimizer_model_id"]
                if expected_optimizer is None or component_model_ids != {
                    expected_optimizer
                }:
                    failures.append(
                        "optimizer budget component does not bind the system optimizer"
                    )
            elif role in {"generation", "image_generation"}:
                if component_model_ids != {system["generation_model_id"]}:
                    failures.append(
                        "generation budget component does not bind the system target"
                    )
            elif not component_model_ids.issubset(principal_model_ids):
                failures.append(
                    "budget component model is not registered for its system"
                )
        if budgeted_judge_model_ids != judge_model_ids:
            failures.append("campaign budget omits a registered judge model")

    principal_families: set[str] = set()
    principal_providers: set[str] = set()
    for system in plan["systems"]:
        for field in ("optimizer_model_id", "generation_model_id"):
            model_id = system[field]
            if model_id in models:
                principal_families.add(models[model_id]["family"])
                principal_providers.add(models[model_id]["provider"])
    judge_families: list[str] = []
    judge_providers: list[str] = []
    judge_models: set[str] = set()
    for judge in plan["judges"]:
        model = models.get(judge["model_id"])
        if model is None:
            failures.append("campaign judge references an unknown model")
            continue
        judge_models.add(judge["model_id"])
        judge_families.append(model["family"])
        judge_providers.append(model["provider"])
        if judge["positions"] != ["A", "B"]:
            failures.append("campaign judge lacks both A/B positions")
    if len(plan["judges"]) < int(policy["required_judge_count"]):
        failures.append("campaign has fewer judges than the release policy requires")
    if len(judge_families) != len(set(judge_families)):
        failures.append("campaign judges do not use independent model families")
    if principal_families.intersection(judge_families):
        failures.append("campaign judge family overlaps generation or optimization")
    if len(judge_providers) != len(set(judge_providers)):
        failures.append("campaign judges do not use independent providers")
    if principal_providers.intersection(judge_providers):
        failures.append("campaign judge provider overlaps generation or optimization")
    if len(judge_models) != len(plan["judges"]):
        failures.append("campaign judges must use distinct registered models")

    if candidates:
        candidate = candidates[0]
        candidate_track = candidate["provenance"]["track"]
        if candidate["provenance"]["source_revision"] != plan[
            "candidate_artifacts"
        ]["source_revision"]:
            failures.append("candidate source revisions do not match")
        if any(
            system["provenance"]["track"] != candidate_track
            for system in plan["systems"]
            if system["strong_baseline"]
        ):
            failures.append("strong baseline track is incompatible with candidate")

    committed_case_ids = {
        item["case_id"] for item in commitment["case_commitments"]
    }
    routed_case_ids = {item["case_id"] for item in plan["case_routes"]}
    if committed_case_ids != routed_case_ids:
        failures.append("every committed case must have exactly one authority route")
    allowed_routes = set(policy["allowed_authority_routes"])
    if any(item["route"] not in allowed_routes for item in plan["case_routes"]):
        failures.append("campaign case uses a route forbidden by release policy")
    routed_capabilities = {
        (item["route"], item["authority_id"]) for item in plan["case_routes"]
    }
    registered_capabilities = {
        (item["route"], item["authority_id"])
        for item in plan["host_capabilities"]
    }
    if routed_capabilities != registered_capabilities:
        failures.append(
            "every routed authority must have exactly one host capability"
        )

    owner = plan["attestations"]["owner"]
    custodian = plan["attestations"]["dataset_custodian"]
    if owner["subject_id"] != plan["owner_id"]:
        failures.append("owner attestation subject mismatch")
    if custodian["subject_id"] != plan["dataset_custodian_id"]:
        failures.append("dataset-custodian attestation subject mismatch")
    if owner["attestation_id"] == custodian["attestation_id"]:
        failures.append("owner and custodian attestations must be distinct")

    human_gold = plan["human_gold"]
    reviewers = set(human_gold["expert_reviewer_ids"])
    adjudicators = set(human_gold["senior_adjudicator_ids"])
    if len(reviewers) < int(
        policy_human_gold["ordinary_minimum_expert_reviewers"]
    ):
        failures.append("human-gold expert pool is below the ordinary threshold")
    if len(reviewers) < int(
        policy_human_gold["critical_minimum_expert_reviewers"]
    ):
        failures.append("human-gold expert pool is below the critical threshold")
    principals = {plan["owner_id"], plan["dataset_custodian_id"]}
    if principals.intersection(reviewers | adjudicators):
        failures.append("human-gold experts must be independent of campaign principals")
    receipt_authorities = {
        owner["authority_id"],
        custodian["authority_id"],
        *(judge["receipt_authority_id"] for judge in plan["judges"]),
    }
    if human_gold["receipt_authority_id"] in receipt_authorities:
        failures.append(
            "human-gold receipt authority must be independent of other authorities"
        )

    storage = plan["storage"]
    if int(storage["worst_case_bytes"]) > int(storage["available_bytes"]):
        failures.append("campaign worst-case storage exceeds available storage")
    return _unique_failures(failures)
