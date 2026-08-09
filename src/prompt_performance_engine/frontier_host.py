"""Fail-closed provider-attempt host for a frozen frontier campaign."""

from __future__ import annotations

import copy
import math
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Protocol

from .frontier_budget import BUDGET_AXES, BudgetExceededError, BudgetLedger
from .frontier_contracts import (
    FRONTIER_CONTRACT_SCHEMA_VERSION,
    validate_frontier_campaign_plan,
)
from .frontier_preflight import validate_frontier_preflight_report
from .hashing import hash_payload, sha256_json


ATTEMPT_STATUSES = frozenset(
    {
        "success",
        "failure",
        "timeout",
        "refusal",
        "quota",
        "malformed",
        "cancelled_after_call",
    }
)
PROVIDER_RESULT_STATUSES = ATTEMPT_STATUSES - {"cancelled_after_call"}
ATTEMPT_REPORT_FIELDS = frozenset(
    {
        "schema_version",
        "campaign_sha256",
        "preflight_sha256",
        "evidence_expires_at",
        "execution_authority_receipt_sha256",
        "system_id",
        "component_id",
        "attempt_id",
        "execution_plan_sha256",
        "unit_id",
        "case_id",
        "replicate_index",
        "retry_index",
        "purpose",
        "route",
        "route_authority_id",
        "provider",
        "model_id",
        "model_snapshot",
        "parameters_sha256",
        "retry_policy_sha256",
        "cache_policy_sha256",
        "seeds_sha256",
        "pricing_entry_sha256",
        "price_snapshot_sha256",
        "seed",
        "pricing_currency",
        "request_sha256",
        "response_sha256",
        "provider_status",
        "usage",
        "receipt_sha256",
        "usable",
        "failure_code",
        "attempt_sha256",
    }
)
EXECUTION_PLAN_FIELDS = frozenset(
    {
        "schema_version",
        "plan_id",
        "campaign_sha256",
        "system_id",
        "slots",
        "plan_sha256",
    }
)
EXECUTION_SLOT_FIELDS = frozenset(
    {
        "slot_id",
        "component_id",
        "unit_id",
        "case_id",
        "replicate_index",
        "retry_index",
        "model_id",
        "purpose",
        "route",
        "route_authority_id",
        "system_prompt_sha256",
        "user_payload_sha256",
        "seed",
    }
)
MODEL_EXECUTION_FIELDS = frozenset(
    {
        "model_id",
        "provider",
        "snapshot",
        "parameters_sha256",
        "retry_policy_sha256",
        "cache_policy_sha256",
        "seeds_sha256",
        "allowed_seeds",
        "pricing_entry_sha256",
        "price_snapshot_sha256",
        "pricing_currency",
    }
)
HOST_SNAPSHOT_FIELDS = frozenset(
    {
        "campaign_sha256",
        "preflight_sha256",
        "evidence_expires_at",
        "system_id",
        "execution_plan_sha256",
        "execution_authority_receipt_sha256",
        "component_capacities",
        "allocated_attempts",
        "used_slots",
        "receipt_digests",
        "attempt_reports",
        "budget_ledger",
        "host_sha256",
    }
)
EXECUTION_MANIFEST_FIELDS = frozenset(
    {
        "schema_version",
        "manifest_id",
        "campaign_sha256",
        "preflight_sha256",
        "evidence_expires_at",
        "system_id",
        "execution_plan_sha256",
        "execution_authority_receipt_sha256",
        "attempt_reports",
        "completed_slot_ids",
        "budget_ledger",
        "host_sha256",
        "manifest_sha256",
    }
)
EXECUTION_BUNDLE_ENTRY_FIELDS = frozenset(
    {"system_id", "execution_plan", "execution_manifest"}
)
EXECUTION_BUNDLE_FIELDS = frozenset(
    {
        "schema_version",
        "bundle_id",
        "campaign_sha256",
        "system_ids",
        "executions",
        "bundle_sha256",
    }
)
USAGE_FIELDS = frozenset(BUDGET_AXES)
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
UTC_TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$"
)
MAX_RESOURCE_VALUE = 9_007_199_254_740_991


class FrontierExecutionRejected(RuntimeError):
    """Raised before dispatch when frozen campaign authority is unavailable."""


@dataclass(frozen=True)
class ProviderAttemptRequest:
    """One physical provider request; retrying requires a new host attempt."""

    provider: str
    model_id: str
    model_snapshot: str
    purpose: str
    slot_id: str
    unit_id: str
    case_id: str
    replicate_index: int
    retry_index: int
    route: str
    route_authority_id: str
    parameters_sha256: str
    retry_policy_sha256: str
    cache_policy_sha256: str
    seeds_sha256: str
    seed: int | None
    pricing_entry_sha256: str
    price_snapshot_sha256: str
    pricing_currency: str
    system_prompt: str
    user_payload: str


@dataclass(frozen=True)
class ProviderAttemptResult:
    """Sanitized result from exactly one physical provider attempt."""

    text: str
    provider: str
    model_id: str
    status: str
    input_tokens: int
    output_tokens: int
    money_microunits: int
    physical_attempts: int
    receipt_evidence: Mapping[str, Any]


@dataclass(frozen=True)
class FrontierAttemptOutcome:
    """A public audit record and output released only after authority checks."""

    report: dict[str, Any]
    output_text: str | None


class ProviderAttemptRunner(Protocol):
    def attempt(self, request: ProviderAttemptRequest) -> ProviderAttemptResult:
        """Perform exactly one physical call with no hidden retry or fallback."""


class ProviderReceiptVerifier(Protocol):
    def verify(
        self,
        *,
        evidence: Mapping[str, Any],
        context_sha256: str,
    ) -> str | None:
        """Return a unique receipt digest bound to the complete attempt context."""


class PreflightExecutionAuthorizer(Protocol):
    def verify(
        self,
        *,
        evidence: Mapping[str, Any],
        context_sha256: str,
    ) -> str | None:
        """Reverify preflight authorities and the complete execution binding."""


def _identifier(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or IDENTIFIER_RE.fullmatch(value) is None:
        raise ValueError(f"{label} is invalid.")
    return value


def _resource(value: Any, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be a non-negative integer.")
    if not 0 <= value <= MAX_RESOURCE_VALUE:
        raise ValueError(f"{label} is outside the supported range.")
    return value


def _component_maximums(component: Mapping[str, Any]) -> dict[str, int]:
    expected = {
        "component_id",
        "role",
        "unit_count",
        "attempts_per_unit",
        "max_input_tokens_per_attempt",
        "max_output_tokens_per_attempt",
        "max_money_microunits_per_attempt",
        "max_wall_clock_ms_per_attempt",
        "model_ids",
        "purposes",
    }
    if not isinstance(component, Mapping) or set(component) != expected:
        raise ValueError("component fields do not match the execution contract.")
    _identifier(component["component_id"], label="component_id")
    _identifier(component["role"], label="component role")
    unit_count = _resource(component["unit_count"], label="component unit_count")
    attempts = _resource(
        component["attempts_per_unit"],
        label="component attempts_per_unit",
    )
    if unit_count == 0 or attempts == 0:
        raise ValueError("component attempt capacity must be positive.")
    maximums = {
        "calls": 1,
        "input_tokens": _resource(
            component["max_input_tokens_per_attempt"],
            label="component max_input_tokens_per_attempt",
        ),
        "output_tokens": _resource(
            component["max_output_tokens_per_attempt"],
            label="component max_output_tokens_per_attempt",
        ),
        "money_microunits": _resource(
            component["max_money_microunits_per_attempt"],
            label="component max_money_microunits_per_attempt",
        ),
        "wall_clock_ms": _resource(
            component["max_wall_clock_ms_per_attempt"],
            label="component max_wall_clock_ms_per_attempt",
        ),
    }
    return maximums


def _attempt_capacity(component: Mapping[str, Any]) -> int:
    return int(component["unit_count"]) * int(component["attempts_per_unit"])


def _request_sha256(request: ProviderAttemptRequest) -> str:
    return sha256_json(
        {
            "provider": request.provider,
            "model_id": request.model_id,
            "model_snapshot": request.model_snapshot,
            "purpose": request.purpose,
            "slot_id": request.slot_id,
            "unit_id": request.unit_id,
            "case_id": request.case_id,
            "replicate_index": request.replicate_index,
            "retry_index": request.retry_index,
            "route": request.route,
            "route_authority_id": request.route_authority_id,
            "parameters_sha256": request.parameters_sha256,
            "retry_policy_sha256": request.retry_policy_sha256,
            "cache_policy_sha256": request.cache_policy_sha256,
            "seeds_sha256": request.seeds_sha256,
            "seed": request.seed,
            "pricing_entry_sha256": request.pricing_entry_sha256,
            "price_snapshot_sha256": request.price_snapshot_sha256,
            "pricing_currency": request.pricing_currency,
            "system_prompt": request.system_prompt,
            "user_payload": request.user_payload,
        }
    )


def frontier_text_sha256(value: str) -> str:
    """Hash one exact provider text value using the execution-plan contract."""

    if not isinstance(value, str):
        raise TypeError("frontier execution text must be a string.")
    return sha256_json({"text": value})


def _safe_payload_hash(value: Any, hash_field: str) -> str | None:
    try:
        return hash_payload(value, hash_field) if isinstance(value, dict) else None
    except (OverflowError, RecursionError, TypeError, ValueError):
        return None


def validate_frontier_execution_plan(plan: Any) -> list[str]:
    """Validate the immutable, single-use physical-call schedule."""

    failures: list[str] = []
    if not isinstance(plan, dict):
        return ["frontier execution plan must be an object"]
    if set(plan) != EXECUTION_PLAN_FIELDS:
        failures.append("frontier execution plan fields do not match the contract")
    if plan.get("schema_version") != FRONTIER_CONTRACT_SCHEMA_VERSION:
        failures.append("unsupported frontier execution plan schema")
    for field in ("plan_id", "system_id"):
        try:
            _identifier(plan.get(field), label=field)
        except ValueError:
            failures.append(f"frontier execution plan {field} is invalid")
    campaign_sha256 = plan.get("campaign_sha256")
    if not isinstance(campaign_sha256, str) or SHA256_RE.fullmatch(
        campaign_sha256
    ) is None:
        failures.append("frontier execution plan campaign digest is invalid")

    slots = plan.get("slots")
    slot_ids: list[str] = []
    slot_keys: list[tuple[str, str, int]] = []
    if not isinstance(slots, list) or not slots:
        failures.append("frontier execution plan slots must be non-empty")
    else:
        for slot in slots:
            if not isinstance(slot, dict):
                failures.append("frontier execution slot must be an object")
                continue
            if set(slot) != EXECUTION_SLOT_FIELDS:
                failures.append("frontier execution slot fields do not match the contract")
            for field in (
                "slot_id",
                "component_id",
                "unit_id",
                "case_id",
                "model_id",
                "purpose",
                "route",
                "route_authority_id",
            ):
                try:
                    _identifier(slot.get(field), label=field)
                except ValueError:
                    failures.append(f"frontier execution slot {field} is invalid")
            for field in ("system_prompt_sha256", "user_payload_sha256"):
                value = slot.get(field)
                if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
                    failures.append(f"frontier execution slot {field} is invalid")
            for field in ("replicate_index", "retry_index"):
                value = slot.get(field)
                if (
                    isinstance(value, bool)
                    or not isinstance(value, int)
                    or not 0 <= value <= MAX_RESOURCE_VALUE
                ):
                    failures.append(f"frontier execution slot {field} is invalid")
            seed = slot.get("seed")
            if seed is not None and (
                isinstance(seed, bool)
                or not isinstance(seed, int)
                or not -MAX_RESOURCE_VALUE <= seed <= MAX_RESOURCE_VALUE
            ):
                failures.append("frontier execution slot seed is invalid")
            if isinstance(slot.get("slot_id"), str):
                slot_ids.append(slot["slot_id"])
            if (
                isinstance(slot.get("component_id"), str)
                and isinstance(slot.get("unit_id"), str)
                and isinstance(slot.get("retry_index"), int)
                and not isinstance(slot.get("retry_index"), bool)
            ):
                slot_keys.append(
                    (slot["component_id"], slot["unit_id"], slot["retry_index"])
                )
        if len(slot_ids) != len(set(slot_ids)):
            failures.append("frontier execution slot ids must be unique")
        if len(slot_keys) != len(set(slot_keys)):
            failures.append("frontier execution unit retry slots must be unique")
    if plan.get("plan_sha256") != _safe_payload_hash(plan, "plan_sha256"):
        failures.append("frontier execution plan hash mismatch")
    return list(dict.fromkeys(failures))


def _elapsed_milliseconds(
    monotonic: Callable[[], float],
    started: float,
    *,
    fallback: int,
) -> int:
    """Return a conservative, portable elapsed value for one dispatched call."""

    try:
        elapsed = monotonic() - started
        if isinstance(elapsed, bool) or not isinstance(elapsed, (int, float)):
            raise TypeError
        if not math.isfinite(float(elapsed)) or elapsed < 0:
            raise ValueError
        # Ceiling avoids silently discarding a positive fractional millisecond.
        return min(MAX_RESOURCE_VALUE, math.ceil(float(elapsed) * 1000))
    except Exception:
        return fallback


def _build_report(
    *,
    campaign_sha256: str,
    preflight_sha256: str,
    evidence_expires_at: str,
    execution_authority_receipt_sha256: str,
    system_id: str,
    component_id: str,
    attempt_id: str,
    execution_plan_sha256: str,
    unit_id: str,
    case_id: str,
    replicate_index: int,
    retry_index: int,
    purpose: str,
    route: str,
    route_authority_id: str,
    provider: str,
    model_id: str,
    model_snapshot: str,
    parameters_sha256: str,
    retry_policy_sha256: str,
    cache_policy_sha256: str,
    seeds_sha256: str,
    seed: int | None,
    pricing_entry_sha256: str,
    price_snapshot_sha256: str,
    pricing_currency: str,
    request_sha256: str,
    response_sha256: str | None,
    provider_status: str,
    usage: Mapping[str, int],
    receipt_sha256: str | None,
    usable: bool,
    failure_code: str | None,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema_version": FRONTIER_CONTRACT_SCHEMA_VERSION,
        "campaign_sha256": campaign_sha256,
        "preflight_sha256": preflight_sha256,
        "evidence_expires_at": evidence_expires_at,
        "execution_authority_receipt_sha256": execution_authority_receipt_sha256,
        "system_id": system_id,
        "component_id": component_id,
        "attempt_id": attempt_id,
        "execution_plan_sha256": execution_plan_sha256,
        "unit_id": unit_id,
        "case_id": case_id,
        "replicate_index": replicate_index,
        "retry_index": retry_index,
        "purpose": purpose,
        "route": route,
        "route_authority_id": route_authority_id,
        "provider": provider,
        "model_id": model_id,
        "model_snapshot": model_snapshot,
        "parameters_sha256": parameters_sha256,
        "retry_policy_sha256": retry_policy_sha256,
        "cache_policy_sha256": cache_policy_sha256,
        "seeds_sha256": seeds_sha256,
        "seed": seed,
        "pricing_entry_sha256": pricing_entry_sha256,
        "price_snapshot_sha256": price_snapshot_sha256,
        "pricing_currency": pricing_currency,
        "request_sha256": request_sha256,
        "response_sha256": response_sha256,
        "provider_status": provider_status,
        "usage": dict(usage),
        "receipt_sha256": receipt_sha256,
        "usable": usable,
        "failure_code": failure_code,
    }
    report["attempt_sha256"] = hash_payload(report, "attempt_sha256")
    return report


class FrontierExecutionHost:
    """Dispatch only preflight-authorized, fully budgeted physical attempts."""

    def __init__(
        self,
        *,
        campaign_sha256: str,
        preflight_report: Mapping[str, Any],
        system_id: str,
        execution_plan: Mapping[str, Any],
        case_routes: list[Mapping[str, Any]],
        components: list[Mapping[str, Any]],
        ledger: BudgetLedger,
        runner: ProviderAttemptRunner,
        receipt_verifier: ProviderReceiptVerifier,
        preflight_authorizer: PreflightExecutionAuthorizer,
        models: list[Mapping[str, Any]],
    ) -> None:
        if not isinstance(preflight_report, dict):
            raise FrontierExecutionRejected("frontier preflight report is invalid.")
        failures = validate_frontier_preflight_report(preflight_report)
        if failures or preflight_report.get("passed") is not True:
            raise FrontierExecutionRejected("frontier preflight did not pass.")
        if (
            not isinstance(campaign_sha256, str)
            or SHA256_RE.fullmatch(campaign_sha256) is None
            or preflight_report.get("campaign_sha256") != campaign_sha256
        ):
            raise FrontierExecutionRejected("campaign does not match preflight.")
        try:
            preflight_timestamp = datetime.fromisoformat(
                str(preflight_report["timestamp"][:-1]) + "+00:00"
            )
            expiry_timestamp = datetime.fromisoformat(
                str(preflight_report["evidence_expires_at"][:-1]) + "+00:00"
            )
        except (KeyError, TypeError, ValueError):
            raise FrontierExecutionRejected(
                "campaign evidence expiry is invalid."
            ) from None
        now = datetime.now(timezone.utc)
        if preflight_timestamp > now or now >= expiry_timestamp:
            raise FrontierExecutionRejected(
                "campaign preflight is outside its trusted evidence window."
            )
        if receipt_verifier is None:
            raise FrontierExecutionRejected("trusted provider receipt verifier is required.")
        if preflight_authorizer is None:
            raise FrontierExecutionRejected("trusted preflight authorizer is required.")
        self.campaign_sha256 = campaign_sha256
        self.preflight_sha256 = str(preflight_report["preflight_sha256"])
        self.system_id = _identifier(system_id, label="system_id")
        self.evidence_expires_at = str(preflight_report["evidence_expires_at"])
        self._expiry_timestamp = expiry_timestamp
        self.runner = runner
        self.receipt_verifier = receipt_verifier
        self.preflight_authorizer = preflight_authorizer
        # Production construction always uses the process monotonic clock.  It
        # is intentionally not caller-injectable because latency is an
        # authority-bearing budget dimension.
        self._monotonic = time.monotonic
        self._lock = threading.RLock()
        if not isinstance(ledger, BudgetLedger):
            raise TypeError("ledger must be a BudgetLedger.")
        if not isinstance(models, list) or not models:
            raise ValueError("at least one campaign model is required.")
        initial_ledger = ledger.snapshot()
        if (
            initial_ledger.get("breached") is not False
            or initial_ledger.get("events")
            or initial_ledger.get("active_reservations")
            or initial_ledger.get("reservation_rejections")
            or any(initial_ledger.get("consumed", {}).values())
        ):
            raise ValueError("execution host requires a fresh budget ledger.")
        # The caller-supplied ledger is a ceiling proposal, not shared mutable
        # execution state.  Owning a fresh ledger prevents a runner or a
        # concurrent caller from cancelling a live reservation after dispatch.
        self._ledger = BudgetLedger(initial_ledger["ceilings"])
        self._model_bindings: dict[str, dict[str, Any]] = {}
        normalized_models: list[dict[str, Any]] = []
        for model in models:
            if not isinstance(model, Mapping):
                raise ValueError("campaign model is invalid.")
            if not MODEL_EXECUTION_FIELDS.issubset(model):
                raise ValueError("campaign model execution binding is incomplete.")
            binding = {field: model[field] for field in MODEL_EXECUTION_FIELDS}
            model_id = _identifier(binding["model_id"], label="model_id")
            _identifier(binding["provider"], label="model provider")
            _identifier(binding["snapshot"], label="model snapshot")
            for field in (
                "parameters_sha256",
                "retry_policy_sha256",
                "cache_policy_sha256",
                "seeds_sha256",
                "pricing_entry_sha256",
                "price_snapshot_sha256",
            ):
                value = binding[field]
                if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
                    raise ValueError("campaign model execution digest is invalid.")
            _identifier(binding["pricing_currency"], label="pricing currency")
            allowed_seeds = binding["allowed_seeds"]
            if (
                not isinstance(allowed_seeds, list)
                or not allowed_seeds
                or len(allowed_seeds) != len(set(allowed_seeds))
                or any(
                    seed is not None
                    and (
                        isinstance(seed, bool)
                        or not isinstance(seed, int)
                        or not -MAX_RESOURCE_VALUE <= seed <= MAX_RESOURCE_VALUE
                    )
                    for seed in allowed_seeds
                )
            ):
                raise ValueError("campaign model allowed seeds are invalid.")
            if model_id in self._model_bindings:
                raise ValueError("campaign model ids must be unique.")
            self._model_bindings[model_id] = binding
            normalized_models.append(dict(binding))
        self._component_maximums: dict[str, dict[str, int]] = {}
        self._component_capacities: dict[str, int] = {}
        self._component_attempts_per_unit: dict[str, int] = {}
        self._component_unit_counts: dict[str, int] = {}
        self._component_models: dict[str, frozenset[str]] = {}
        self._component_purposes: dict[str, frozenset[str]] = {}
        self._allocated: dict[str, int] = {}
        if not isinstance(components, list):
            raise TypeError("components must be an array.")
        normalized_components: list[dict[str, Any]] = []
        for component in components:
            maximums = _component_maximums(component)
            component_id = str(component["component_id"])
            if component_id in self._component_maximums:
                raise ValueError("component ids must be unique.")
            self._component_maximums[component_id] = maximums
            self._component_capacities[component_id] = _attempt_capacity(component)
            self._component_attempts_per_unit[component_id] = int(
                component["attempts_per_unit"]
            )
            self._component_unit_counts[component_id] = int(component["unit_count"])
            model_ids = component["model_ids"]
            purposes = component["purposes"]
            if (
                not isinstance(model_ids, list)
                or not model_ids
                or any(
                    not isinstance(item, str) or item not in self._model_bindings
                    for item in model_ids
                )
                or len(model_ids) != len(set(model_ids))
            ):
                raise ValueError("component model_ids are invalid.")
            if (
                not isinstance(purposes, list)
                or not purposes
                or any(
                    not isinstance(item, str) or IDENTIFIER_RE.fullmatch(item) is None
                    for item in purposes
                )
                or len(purposes) != len(set(purposes))
            ):
                raise ValueError("component purposes are invalid.")
            self._component_models[component_id] = frozenset(model_ids)
            self._component_purposes[component_id] = frozenset(purposes)
            self._allocated[component_id] = 0
            normalized_components.append(dict(component))
        if not self._component_maximums:
            raise ValueError("at least one budget component is required.")
        if not isinstance(case_routes, list) or not case_routes:
            raise ValueError("campaign case routes must be a non-empty array.")
        normalized_case_routes: list[dict[str, str]] = []
        route_by_case: dict[str, tuple[str, str]] = {}
        for case_route in case_routes:
            if not isinstance(case_route, Mapping) or set(case_route) != {
                "case_id",
                "route",
                "authority_id",
            }:
                raise ValueError("campaign case route fields are invalid.")
            case_id = _identifier(case_route["case_id"], label="case_id")
            route = _identifier(case_route["route"], label="route")
            authority_id = _identifier(
                case_route["authority_id"], label="route authority"
            )
            if case_id in route_by_case:
                raise ValueError("campaign case route ids must be unique.")
            route_by_case[case_id] = (route, authority_id)
            normalized_case_routes.append(
                {
                    "case_id": case_id,
                    "route": route,
                    "authority_id": authority_id,
                }
            )
        if not isinstance(execution_plan, dict) or validate_frontier_execution_plan(
            execution_plan
        ):
            raise ValueError("frontier execution plan is invalid.")
        if (
            execution_plan["campaign_sha256"] != self.campaign_sha256
            or execution_plan["system_id"] != self.system_id
        ):
            raise FrontierExecutionRejected(
                "frontier execution plan identity does not match preflight."
            )
        self.execution_plan_sha256 = str(execution_plan["plan_sha256"])
        self._slots: dict[str, dict[str, Any]] = {}
        component_units: dict[str, dict[str, set[int]]] = {
            component_id: {} for component_id in self._component_maximums
        }
        unit_bindings: dict[tuple[str, str], tuple[Any, ...]] = {}
        for raw_slot in execution_plan["slots"]:
            slot = dict(raw_slot)
            component_id = slot["component_id"]
            if component_id not in self._component_maximums:
                raise ValueError("execution slot references an unknown component.")
            if slot["model_id"] not in self._component_models[component_id]:
                raise ValueError("execution slot model is not authorized for component.")
            if slot["purpose"] not in self._component_purposes[component_id]:
                raise ValueError("execution slot purpose is not authorized for component.")
            if slot["retry_index"] >= self._component_attempts_per_unit[component_id]:
                raise ValueError("execution slot retry index exceeds component policy.")
            expected_route = route_by_case.get(slot["case_id"])
            if expected_route != (slot["route"], slot["route_authority_id"]):
                raise ValueError("execution slot route does not match the campaign case.")
            if slot["seed"] not in self._model_bindings[slot["model_id"]][
                "allowed_seeds"
            ]:
                raise ValueError("execution slot seed is not frozen for its model.")
            unit_key = (component_id, slot["unit_id"])
            unit_binding = (
                slot["case_id"],
                slot["replicate_index"],
                slot["model_id"],
                slot["purpose"],
                slot["route"],
                slot["route_authority_id"],
                slot["system_prompt_sha256"],
                slot["user_payload_sha256"],
                slot["seed"],
            )
            previous_binding = unit_bindings.setdefault(unit_key, unit_binding)
            if previous_binding != unit_binding:
                raise ValueError("execution unit binding changes across retry slots.")
            self._slots[slot["slot_id"]] = slot
            component_units[component_id].setdefault(slot["unit_id"], set()).add(
                slot["retry_index"]
            )
        for component_id, units in component_units.items():
            expected_retries = set(
                range(self._component_attempts_per_unit[component_id])
            )
            if len(units) != self._component_unit_counts[component_id] or any(
                retry_indexes != expected_retries for retry_indexes in units.values()
            ):
                raise ValueError(
                    "execution plan does not cover the exact component call matrix."
                )
            case_replicates = {
                (binding[0], binding[1])
                for (bound_component, _unit_id), binding in unit_bindings.items()
                if bound_component == component_id
            }
            if len(case_replicates) != len(units):
                raise ValueError(
                    "execution component repeats a case/replicate under another unit."
                )
        self._used_slots: set[str] = set()
        self._slot_order = {
            slot_id: index for index, slot_id in enumerate(self._slots)
        }
        self._receipts: set[str] = set()
        self._reports: list[dict[str, Any]] = []
        binding_context = sha256_json(
            {
                "campaign_sha256": self.campaign_sha256,
                "preflight_sha256": self.preflight_sha256,
                "evidence_expires_at": self.evidence_expires_at,
                "system_id": self.system_id,
                "budget_ceilings": initial_ledger["ceilings"],
                "models": sorted(normalized_models, key=lambda item: str(item["model_id"])),
                "components": sorted(
                    normalized_components,
                    key=lambda item: str(item["component_id"]),
                ),
                "execution_plan_sha256": self.execution_plan_sha256,
                "execution_plan": dict(execution_plan),
                "case_routes": sorted(
                    normalized_case_routes,
                    key=lambda item: item["case_id"],
                ),
                "monotonic_clock": "python.time.monotonic",
            }
        )
        try:
            authority_receipt = preflight_authorizer.verify(
                evidence=preflight_report,
                context_sha256=binding_context,
            )
        except Exception:
            authority_receipt = None
        if (
            not isinstance(authority_receipt, str)
            or SHA256_RE.fullmatch(authority_receipt) is None
        ):
            raise FrontierExecutionRejected("preflight execution authority is invalid.")
        embedded_receipts = {
            preflight_report.get("clock_attestation_receipt_sha256"),
            preflight_report.get("owner_attestation_receipt_sha256"),
            preflight_report.get("custodian_attestation_receipt_sha256"),
            preflight_report.get("capability_receipts_sha256"),
        }
        if authority_receipt in embedded_receipts:
            raise FrontierExecutionRejected(
                "preflight execution authority receipt is not unique."
            )
        self.preflight_authority_receipt_sha256 = authority_receipt

    def _claim_slot(self, slot_id: str) -> tuple[dict[str, Any], dict[str, int]]:
        with self._lock:
            slot = self._slots.get(slot_id)
            if slot is None:
                raise FrontierExecutionRejected("attempt is not in the frozen schedule.")
            if slot_id in self._used_slots:
                raise FrontierExecutionRejected("frozen execution slot was already consumed.")
            component_id = slot["component_id"]
            if self._allocated[component_id] >= self._component_capacities[component_id]:
                raise FrontierExecutionRejected("component attempt capacity is exhausted.")
            self._used_slots.add(slot_id)
            self._allocated[component_id] += 1
            return copy.deepcopy(slot), dict(self._component_maximums[component_id])

    def _record(self, report: dict[str, Any]) -> None:
        with self._lock:
            self._reports.append(copy.deepcopy(report))

    def _release_slot(self, slot_id: str, component_id: str) -> None:
        with self._lock:
            self._used_slots.remove(slot_id)
            self._allocated[component_id] -= 1

    def execute_attempt(
        self,
        *,
        attempt_id: str,
        component_id: str,
        retry_index: int,
        model_id: str,
        purpose: str,
        system_prompt: str,
        user_payload: str,
    ) -> FrontierAttemptOutcome:
        """Execute and account for one call without persisting prompt or output text."""

        attempt_id = _identifier(attempt_id, label="attempt_id")
        component_id = _identifier(component_id, label="component_id")
        model_id = _identifier(model_id, label="model_id")
        purpose = _identifier(purpose, label="purpose")
        if isinstance(retry_index, bool) or not isinstance(retry_index, int) or retry_index < 0:
            raise ValueError("retry_index must be a non-negative integer.")
        if not isinstance(system_prompt, str) or not isinstance(user_payload, str):
            raise TypeError("provider request text must be strings.")
        if datetime.now(timezone.utc) >= self._expiry_timestamp:
            raise FrontierExecutionRejected(
                "campaign evidence window expired before dispatch."
            )
        slot = self._slots.get(attempt_id)
        if slot is None:
            raise FrontierExecutionRejected("attempt is not in the frozen schedule.")
        if (
            slot["component_id"] != component_id
            or slot["retry_index"] != retry_index
            or slot["model_id"] != model_id
            or slot["purpose"] != purpose
        ):
            raise FrontierExecutionRejected(
                "attempt metadata does not match the frozen execution slot."
            )
        if (
            slot["system_prompt_sha256"] != frontier_text_sha256(system_prompt)
            or slot["user_payload_sha256"] != frontier_text_sha256(user_payload)
        ):
            raise FrontierExecutionRejected(
                "attempt text does not match the frozen execution slot."
            )
        model = self._model_bindings[model_id]
        provider = model["provider"]

        # Acquire the clock before reserving budget.  A broken injected clock
        # therefore cannot strand an active reservation before dispatch.
        started = self._monotonic()
        if (
            isinstance(started, bool)
            or not isinstance(started, (int, float))
            or not math.isfinite(float(started))
        ):
            raise FrontierExecutionRejected("host monotonic clock is invalid.")

        claimed_slot, maximums = self._claim_slot(attempt_id)
        try:
            reservation = self._ledger.reserve(attempt_id, maximums)
        except BaseException:
            self._release_slot(attempt_id, component_id)
            raise
        reservation_token = reservation["reservation_token"]
        request = ProviderAttemptRequest(
            provider=provider,
            model_id=model_id,
            model_snapshot=model["snapshot"],
            purpose=purpose,
            slot_id=attempt_id,
            unit_id=claimed_slot["unit_id"],
            case_id=claimed_slot["case_id"],
            replicate_index=claimed_slot["replicate_index"],
            retry_index=retry_index,
            route=claimed_slot["route"],
            route_authority_id=claimed_slot["route_authority_id"],
            parameters_sha256=model["parameters_sha256"],
            retry_policy_sha256=model["retry_policy_sha256"],
            cache_policy_sha256=model["cache_policy_sha256"],
            seeds_sha256=model["seeds_sha256"],
            seed=claimed_slot["seed"],
            pricing_entry_sha256=model["pricing_entry_sha256"],
            price_snapshot_sha256=model["price_snapshot_sha256"],
            pricing_currency=model["pricing_currency"],
            system_prompt=system_prompt,
            user_payload=user_payload,
        )
        request_digest = _request_sha256(request)
        result: ProviderAttemptResult | None = None
        provider_status = "failure"
        response_digest: str | None = None
        receipt_digest: str | None = None
        failure_code: str | None = "provider_exception"
        usable = False
        charged = dict(maximums)
        observed_wall_clock_ms: int | None = None
        usage_authoritative = False
        pending_interrupt: BaseException | None = None
        try:
            candidate = self.runner.attempt(request)
            observed_wall_clock_ms = _elapsed_milliseconds(
                self._monotonic,
                float(started),
                fallback=maximums["wall_clock_ms"],
            )
            if not isinstance(candidate, ProviderAttemptResult):
                raise ValueError("provider result type is invalid")
            result = candidate
            if (
                candidate.provider != provider
                or candidate.model_id != model_id
                or isinstance(candidate.physical_attempts, bool)
                or not isinstance(candidate.physical_attempts, int)
                or candidate.physical_attempts != 1
                or candidate.status not in PROVIDER_RESULT_STATUSES
                or not isinstance(candidate.text, str)
                or (candidate.status == "success" and not candidate.text.strip())
                or not isinstance(candidate.receipt_evidence, Mapping)
            ):
                raise ValueError("provider result identity or shape is invalid")
            provider_status = candidate.status
            charged = {
                "calls": 1,
                "input_tokens": _resource(
                    candidate.input_tokens,
                    label="provider input_tokens",
                ),
                "output_tokens": _resource(
                    candidate.output_tokens,
                    label="provider output_tokens",
                ),
                "money_microunits": _resource(
                    candidate.money_microunits,
                    label="provider money_microunits",
                ),
                "wall_clock_ms": observed_wall_clock_ms,
            }
            response_digest = sha256_json({"text": candidate.text})
            context_sha256 = sha256_json(
                {
                    "campaign_sha256": self.campaign_sha256,
                    "preflight_sha256": self.preflight_sha256,
                    "evidence_expires_at": self.evidence_expires_at,
                    "execution_plan_sha256": self.execution_plan_sha256,
                    "execution_authority_receipt_sha256": (
                        self.preflight_authority_receipt_sha256
                    ),
                    "system_id": self.system_id,
                    "component_id": component_id,
                    "attempt_id": attempt_id,
                    "retry_index": retry_index,
                    "purpose": purpose,
                    "provider": provider,
                    "model_id": model_id,
                    "request_sha256": request_digest,
                    "response_sha256": response_digest,
                    "provider_status": provider_status,
                    "usage": charged,
                }
            )
            try:
                verified = self.receipt_verifier.verify(
                    evidence=candidate.receipt_evidence,
                    context_sha256=context_sha256,
                )
            except Exception:
                verified = None
            if not isinstance(verified, str) or SHA256_RE.fullmatch(verified) is None:
                failure_code = "receipt_unverified"
            else:
                with self._lock:
                    if verified in self._receipts:
                        failure_code = "receipt_reused"
                    else:
                        self._receipts.add(verified)
                        receipt_digest = verified
                        usage_authoritative = True
                        failure_code = None if provider_status == "success" else provider_status
            usable = provider_status == "success" and failure_code is None
        except (ArithmeticError, AttributeError, TypeError, ValueError):
            provider_status = "malformed"
            failure_code = "invalid_provider_result"
            result = None
        except TimeoutError:
            provider_status = "timeout"
            failure_code = "provider_timeout"
            result = None
        except Exception:
            provider_status = "failure"
            failure_code = "provider_exception"
            result = None
        except BaseException as exc:
            provider_status = "cancelled_after_call"
            failure_code = "host_interrupted"
            result = None
            pending_interrupt = exc

        if observed_wall_clock_ms is None:
            observed_wall_clock_ms = _elapsed_milliseconds(
                self._monotonic,
                float(started),
                fallback=maximums["wall_clock_ms"],
            )
        if usage_authoritative:
            charged["wall_clock_ms"] = observed_wall_clock_ms
        else:
            # Without a unique provider receipt, self-reported token and money
            # figures have no authority.  Charge the full reservation and at
            # least the independently observed host elapsed time.
            charged = dict(maximums)
            charged["wall_clock_ms"] = max(
                maximums["wall_clock_ms"],
                observed_wall_clock_ms,
            )

        try:
            self._ledger.settle(
                attempt_id,
                charged,
                status=provider_status,
                receipt_sha256=receipt_digest,
                reservation_token=reservation_token,
            )
        except BudgetExceededError:
            usable = False
            failure_code = "budget_breach"

        report = _build_report(
            campaign_sha256=self.campaign_sha256,
            preflight_sha256=self.preflight_sha256,
            evidence_expires_at=self.evidence_expires_at,
            execution_authority_receipt_sha256=(
                self.preflight_authority_receipt_sha256
            ),
            system_id=self.system_id,
            component_id=component_id,
            attempt_id=attempt_id,
            execution_plan_sha256=self.execution_plan_sha256,
            unit_id=claimed_slot["unit_id"],
            case_id=claimed_slot["case_id"],
            replicate_index=claimed_slot["replicate_index"],
            retry_index=retry_index,
            purpose=purpose,
            route=claimed_slot["route"],
            route_authority_id=claimed_slot["route_authority_id"],
            provider=provider,
            model_id=model_id,
            model_snapshot=model["snapshot"],
            parameters_sha256=model["parameters_sha256"],
            retry_policy_sha256=model["retry_policy_sha256"],
            cache_policy_sha256=model["cache_policy_sha256"],
            seeds_sha256=model["seeds_sha256"],
            seed=claimed_slot["seed"],
            pricing_entry_sha256=model["pricing_entry_sha256"],
            price_snapshot_sha256=model["price_snapshot_sha256"],
            pricing_currency=model["pricing_currency"],
            request_sha256=request_digest,
            response_sha256=response_digest,
            provider_status=provider_status,
            usage=charged,
            receipt_sha256=receipt_digest,
            usable=usable,
            failure_code=failure_code,
        )
        self._record(report)
        if pending_interrupt is not None:
            raise pending_interrupt
        return FrontierAttemptOutcome(
            report=copy.deepcopy(report),
            output_text=result.text if usable and result is not None else None,
        )

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            attempt_reports = sorted(
                copy.deepcopy(self._reports),
                key=lambda item: self._slot_order.get(
                    item.get("attempt_id"), MAX_RESOURCE_VALUE
                ),
            )
            budget_ledger = self._ledger.snapshot()
            budget_ledger["events"] = sorted(
                budget_ledger["events"],
                key=lambda item: self._slot_order.get(
                    item.get("reservation_id"), MAX_RESOURCE_VALUE
                ),
            )
            budget_ledger["ledger_sha256"] = hash_payload(
                budget_ledger, "ledger_sha256"
            )
            snapshot: dict[str, Any] = {
                "campaign_sha256": self.campaign_sha256,
                "preflight_sha256": self.preflight_sha256,
                "evidence_expires_at": self.evidence_expires_at,
                "system_id": self.system_id,
                "execution_plan_sha256": self.execution_plan_sha256,
                "execution_authority_receipt_sha256": (
                    self.preflight_authority_receipt_sha256
                ),
                "component_capacities": dict(self._component_capacities),
                "allocated_attempts": dict(self._allocated),
                "used_slots": sorted(self._used_slots),
                "receipt_digests": sorted(self._receipts),
                "attempt_reports": attempt_reports,
                "budget_ledger": budget_ledger,
            }
            snapshot["host_sha256"] = hash_payload(snapshot, "host_sha256")
            return snapshot


def validate_frontier_attempt_report(report: Any) -> list[str]:
    """Validate an untrusted attempt record without granting receipt authority."""

    failures: list[str] = []
    if not isinstance(report, dict):
        return ["frontier attempt report must be an object"]
    if set(report) != ATTEMPT_REPORT_FIELDS:
        failures.append("frontier attempt report fields do not match the contract")
    if report.get("schema_version") != FRONTIER_CONTRACT_SCHEMA_VERSION:
        failures.append("unsupported frontier attempt report schema")
    for field in (
        "campaign_sha256",
        "preflight_sha256",
        "execution_plan_sha256",
        "execution_authority_receipt_sha256",
        "request_sha256",
        "attempt_sha256",
        "parameters_sha256",
        "retry_policy_sha256",
        "cache_policy_sha256",
        "seeds_sha256",
        "pricing_entry_sha256",
        "price_snapshot_sha256",
    ):
        value = report.get(field)
        if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
            failures.append(f"frontier attempt {field} is invalid")
    evidence_expires_at = report.get("evidence_expires_at")
    try:
        datetime.fromisoformat(str(evidence_expires_at)[:-1] + "+00:00")
    except (TypeError, ValueError):
        failures.append("frontier attempt evidence expiry is invalid")
    else:
        if (
            not isinstance(evidence_expires_at, str)
            or UTC_TIMESTAMP_RE.fullmatch(evidence_expires_at) is None
        ):
            failures.append("frontier attempt evidence expiry is invalid")
    for field in ("response_sha256", "receipt_sha256"):
        value = report.get(field)
        if value is not None and (
            not isinstance(value, str) or SHA256_RE.fullmatch(value) is None
        ):
            failures.append(f"frontier attempt {field} is invalid")
    for field in (
        "system_id",
        "component_id",
        "attempt_id",
        "unit_id",
        "case_id",
        "purpose",
        "route",
        "route_authority_id",
        "provider",
        "model_id",
        "model_snapshot",
        "pricing_currency",
    ):
        value = report.get(field)
        if not isinstance(value, str) or IDENTIFIER_RE.fullmatch(value) is None:
            failures.append(f"frontier attempt {field} is invalid")
    retry_index = report.get("retry_index")
    if isinstance(retry_index, bool) or not isinstance(retry_index, int) or retry_index < 0:
        failures.append("frontier attempt retry_index is invalid")
    replicate_index = report.get("replicate_index")
    if (
        isinstance(replicate_index, bool)
        or not isinstance(replicate_index, int)
        or replicate_index < 0
    ):
        failures.append("frontier attempt replicate_index is invalid")
    seed = report.get("seed")
    if seed is not None and (
        isinstance(seed, bool)
        or not isinstance(seed, int)
        or not -MAX_RESOURCE_VALUE <= seed <= MAX_RESOURCE_VALUE
    ):
        failures.append("frontier attempt seed is invalid")
    if report.get("provider_status") not in ATTEMPT_STATUSES:
        failures.append("frontier attempt provider_status is invalid")
    usage = report.get("usage")
    if not isinstance(usage, dict) or set(usage) != USAGE_FIELDS:
        failures.append("frontier attempt usage fields are invalid")
    else:
        for field in BUDGET_AXES:
            try:
                _resource(usage[field], label=field)
            except (TypeError, ValueError):
                failures.append(f"frontier attempt usage {field} is invalid")
        if usage.get("calls") != 1:
            failures.append("frontier attempt usage calls must equal one")
    usable = report.get("usable")
    if not isinstance(usable, bool):
        failures.append("frontier attempt usable flag is invalid")
    failure_code = report.get("failure_code")
    if failure_code is not None and (
        not isinstance(failure_code, str) or IDENTIFIER_RE.fullmatch(failure_code) is None
    ):
        failures.append("frontier attempt failure_code is invalid")
    if usable and (
        report.get("provider_status") != "success"
        or failure_code is not None
        or report.get("response_sha256") is None
        or report.get("receipt_sha256") is None
    ):
        failures.append("usable frontier attempt lacks successful authority")
    if not usable and failure_code is None:
        failures.append("unusable frontier attempt lacks a failure code")
    try:
        expected_hash = hash_payload(report, "attempt_sha256")
    except (OverflowError, RecursionError, TypeError, ValueError):
        expected_hash = None
    if report.get("attempt_sha256") != expected_hash:
        failures.append("frontier attempt report hash mismatch")
    return list(dict.fromkeys(failures))


def _validate_terminal_budget_ledger(
    ledger: Any,
    attempt_reports: list[Mapping[str, Any]],
) -> list[str]:
    failures: list[str] = []
    fields = {
        "ceilings",
        "consumed",
        "active_reserved",
        "remaining_unreserved",
        "active_reservations",
        "events",
        "pre_call_cancellations",
        "reservation_rejections",
        "breached",
        "ledger_sha256",
    }
    if not isinstance(ledger, dict) or set(ledger) != fields:
        return ["frontier execution budget ledger fields are invalid"]
    vectors: dict[str, dict[str, int]] = {}
    for field in ("ceilings", "consumed", "active_reserved", "remaining_unreserved"):
        value = ledger.get(field)
        if (
            not isinstance(value, dict)
            or set(value) != set(BUDGET_AXES)
            or any(
                isinstance(amount, bool)
                or not isinstance(amount, int)
                or not 0 <= amount <= MAX_RESOURCE_VALUE
                for amount in value.values()
            )
        ):
            failures.append(f"frontier execution budget {field} is invalid")
        else:
            vectors[field] = value
    if ledger.get("active_reservations") != []:
        failures.append("frontier execution budget has active reservations")
    if ledger.get("pre_call_cancellations") != []:
        failures.append("frontier execution budget has cancelled schedule slots")
    if ledger.get("reservation_rejections") != []:
        failures.append("frontier execution budget has rejected reservations")
    if ledger.get("breached") is not False:
        failures.append("frontier execution budget was breached")
    events = ledger.get("events")
    if not isinstance(events, list) or len(events) != len(attempt_reports):
        failures.append("frontier execution budget events are incomplete")
        events = []
    else:
        for event, report in zip(events, attempt_reports, strict=True):
            if (
                not isinstance(event, dict)
                or set(event)
                != {
                    "reservation_id",
                    "status",
                    "reserved",
                    "charged",
                    "receipt_sha256",
                    "over_reserved_axes",
                    "over_ceiling_axes",
                }
                or event.get("reservation_id") != report.get("attempt_id")
                or event.get("status") != report.get("provider_status")
                or event.get("charged") != report.get("usage")
                or event.get("receipt_sha256") != report.get("receipt_sha256")
                or event.get("over_reserved_axes") != []
                or event.get("over_ceiling_axes") != []
            ):
                failures.append("frontier execution budget event does not match attempt")
    if all(field in vectors for field in ("ceilings", "consumed", "active_reserved", "remaining_unreserved")):
        computed_consumed = {
            axis: sum(
                int(report["usage"][axis])
                for report in attempt_reports
                if isinstance(report.get("usage"), dict)
                and isinstance(report["usage"].get(axis), int)
                and not isinstance(report["usage"].get(axis), bool)
            )
            for axis in BUDGET_AXES
        }
        if vectors["consumed"] != computed_consumed:
            failures.append("frontier execution consumed budget does not match attempts")
        if any(vectors["active_reserved"][axis] != 0 for axis in BUDGET_AXES):
            failures.append("frontier execution active budget is nonzero")
        expected_remaining = {
            axis: vectors["ceilings"][axis] - vectors["consumed"][axis]
            for axis in BUDGET_AXES
        }
        if any(value < 0 for value in expected_remaining.values()) or vectors[
            "remaining_unreserved"
        ] != expected_remaining:
            failures.append("frontier execution remaining budget is invalid")
    try:
        expected_hash = hash_payload(ledger, "ledger_sha256")
    except (OverflowError, RecursionError, TypeError, ValueError):
        expected_hash = None
    if ledger.get("ledger_sha256") != expected_hash:
        failures.append("frontier execution budget ledger hash mismatch")
    return list(dict.fromkeys(failures))


def validate_frontier_execution_manifest(
    manifest: Any,
    *,
    execution_plan: Any,
) -> list[str]:
    """Validate complete terminal coverage of every frozen execution slot."""

    failures: list[str] = []
    if not isinstance(manifest, dict) or set(manifest) != EXECUTION_MANIFEST_FIELDS:
        return ["frontier execution manifest fields are invalid"]
    if manifest.get("schema_version") != FRONTIER_CONTRACT_SCHEMA_VERSION:
        failures.append("unsupported frontier execution manifest schema")
    if not isinstance(execution_plan, dict) or validate_frontier_execution_plan(
        execution_plan
    ):
        failures.append("frontier execution manifest plan is invalid")
        slots: list[dict[str, Any]] = []
    else:
        slots = execution_plan["slots"]
    for field in ("manifest_id", "system_id"):
        value = manifest.get(field)
        if not isinstance(value, str) or IDENTIFIER_RE.fullmatch(value) is None:
            failures.append(f"frontier execution manifest {field} is invalid")
    for field in (
        "campaign_sha256",
        "preflight_sha256",
        "execution_plan_sha256",
        "execution_authority_receipt_sha256",
        "host_sha256",
        "manifest_sha256",
    ):
        value = manifest.get(field)
        if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
            failures.append(f"frontier execution manifest {field} is invalid")
    expiry = manifest.get("evidence_expires_at")
    if not isinstance(expiry, str) or UTC_TIMESTAMP_RE.fullmatch(expiry) is None:
        failures.append("frontier execution manifest evidence expiry is invalid")
    if slots and (
        manifest.get("campaign_sha256") != execution_plan.get("campaign_sha256")
        or manifest.get("system_id") != execution_plan.get("system_id")
        or manifest.get("execution_plan_sha256") != execution_plan.get("plan_sha256")
    ):
        failures.append("frontier execution manifest does not match its plan")
    reports = manifest.get("attempt_reports")
    if not isinstance(reports, list) or len(reports) != len(slots):
        failures.append("frontier execution manifest attempts are incomplete")
        reports = []
    else:
        for slot, report in zip(slots, reports, strict=True):
            failures.extend(validate_frontier_attempt_report(report))
            bindings = {
                "attempt_id": "slot_id",
                "component_id": "component_id",
                "unit_id": "unit_id",
                "case_id": "case_id",
                "replicate_index": "replicate_index",
                "retry_index": "retry_index",
                "model_id": "model_id",
                "purpose": "purpose",
                "route": "route",
                "route_authority_id": "route_authority_id",
                "seed": "seed",
            }
            if not isinstance(report, dict) or any(
                report.get(report_field) != slot.get(slot_field)
                for report_field, slot_field in bindings.items()
            ):
                failures.append("frontier execution attempt does not match its slot")
            elif (
                report.get("campaign_sha256") != manifest.get("campaign_sha256")
                or report.get("preflight_sha256") != manifest.get("preflight_sha256")
                or report.get("evidence_expires_at")
                != manifest.get("evidence_expires_at")
                or report.get("system_id") != manifest.get("system_id")
                or report.get("execution_plan_sha256")
                != manifest.get("execution_plan_sha256")
                or report.get("execution_authority_receipt_sha256")
                != manifest.get("execution_authority_receipt_sha256")
            ):
                failures.append("frontier execution attempt authority binding drifted")
    expected_slot_ids = [slot["slot_id"] for slot in slots]
    if manifest.get("completed_slot_ids") != expected_slot_ids:
        failures.append("frontier execution manifest slot coverage is incomplete")
    failures.extend(_validate_terminal_budget_ledger(manifest.get("budget_ledger"), reports))
    try:
        expected_hash = hash_payload(manifest, "manifest_sha256")
    except (OverflowError, RecursionError, TypeError, ValueError):
        expected_hash = None
    if manifest.get("manifest_sha256") != expected_hash:
        failures.append("frontier execution manifest hash mismatch")
    return list(dict.fromkeys(failures))


def build_frontier_execution_manifest(
    host_snapshot: Mapping[str, Any],
    *,
    execution_plan: Mapping[str, Any],
    manifest_id: str = "frontier-execution-manifest",
) -> dict[str, Any]:
    """Build a complete manifest from a terminal execution-host snapshot."""

    snapshot = copy.deepcopy(dict(host_snapshot))
    if set(snapshot) != HOST_SNAPSHOT_FIELDS or snapshot.get("host_sha256") != hash_payload(
        snapshot, "host_sha256"
    ):
        raise ValueError("frontier host snapshot is invalid")
    reports = snapshot["attempt_reports"]
    manifest: dict[str, Any] = {
        "schema_version": FRONTIER_CONTRACT_SCHEMA_VERSION,
        "manifest_id": manifest_id,
        "campaign_sha256": snapshot["campaign_sha256"],
        "preflight_sha256": snapshot["preflight_sha256"],
        "evidence_expires_at": snapshot["evidence_expires_at"],
        "system_id": snapshot["system_id"],
        "execution_plan_sha256": snapshot["execution_plan_sha256"],
        "execution_authority_receipt_sha256": snapshot[
            "execution_authority_receipt_sha256"
        ],
        "attempt_reports": reports,
        "completed_slot_ids": [report["attempt_id"] for report in reports],
        "budget_ledger": snapshot["budget_ledger"],
        "host_sha256": snapshot["host_sha256"],
    }
    manifest["manifest_sha256"] = hash_payload(manifest, "manifest_sha256")
    failures = validate_frontier_execution_manifest(
        manifest, execution_plan=execution_plan
    )
    if failures:
        raise ValueError("frontier execution manifest is incomplete")
    return manifest


def validate_frontier_execution_bundle(
    bundle: Any,
    *,
    campaign_plan: Any,
) -> list[str]:
    """Require complete, equal-budget execution evidence for every system."""

    failures: list[str] = []
    if not isinstance(bundle, dict) or set(bundle) != EXECUTION_BUNDLE_FIELDS:
        return ["frontier execution bundle fields are invalid"]
    if bundle.get("schema_version") != FRONTIER_CONTRACT_SCHEMA_VERSION:
        failures.append("unsupported frontier execution bundle schema")
    if not isinstance(campaign_plan, dict) or validate_frontier_campaign_plan(
        campaign_plan
    ):
        failures.append("frontier execution bundle campaign plan is invalid")
        systems: list[dict[str, Any]] = []
        budgets: dict[str, dict[str, Any]] = {}
        models: dict[str, dict[str, Any]] = {}
        case_routes: dict[str, tuple[str, str]] = {}
    else:
        systems = campaign_plan["systems"]
        budgets = {item["system_id"]: item for item in campaign_plan["budgets"]}
        models = {item["model_id"]: item for item in campaign_plan["models"]}
        case_routes = {
            item["case_id"]: (item["route"], item["authority_id"])
            for item in campaign_plan["case_routes"]
        }
        if bundle.get("campaign_sha256") != campaign_plan.get("campaign_sha256"):
            failures.append("frontier execution bundle campaign digest mismatch")
    if (
        not isinstance(bundle.get("bundle_id"), str)
        or IDENTIFIER_RE.fullmatch(bundle["bundle_id"]) is None
    ):
        failures.append("frontier execution bundle id is invalid")
    expected_system_ids = [item["system_id"] for item in systems]
    if bundle.get("system_ids") != expected_system_ids:
        failures.append("frontier execution bundle system coverage is incomplete")
    executions = bundle.get("executions")
    if not isinstance(executions, list) or len(executions) != len(expected_system_ids):
        failures.append("frontier execution bundle entries are incomplete")
        executions = []
    for expected_system_id, execution in zip(expected_system_ids, executions):
        if not isinstance(execution, dict) or set(execution) != EXECUTION_BUNDLE_ENTRY_FIELDS:
            failures.append("frontier execution bundle entry fields are invalid")
            continue
        if execution.get("system_id") != expected_system_id:
            failures.append("frontier execution bundle order is invalid")
            continue
        plan = execution.get("execution_plan")
        manifest = execution.get("execution_manifest")
        if not isinstance(plan, dict) or validate_frontier_execution_plan(plan):
            failures.append("frontier execution bundle plan is invalid")
            continue
        if validate_frontier_execution_manifest(manifest, execution_plan=plan):
            failures.append("frontier execution bundle manifest is invalid")
            continue
        if (
            plan.get("campaign_sha256") != bundle.get("campaign_sha256")
            or plan.get("system_id") != expected_system_id
        ):
            failures.append("frontier execution bundle plan identity drifted")
        budget = budgets.get(expected_system_id)
        if not isinstance(budget, dict):
            failures.append("frontier execution bundle budget is missing")
            continue
        if manifest["budget_ledger"].get("ceilings") != budget.get("ceilings"):
            failures.append("frontier execution bundle budget ceiling drifted")
        components = {
            item["component_id"]: item for item in budget.get("components", [])
        }
        slots_by_component: dict[str, list[dict[str, Any]]] = {
            component_id: [] for component_id in components
        }
        for slot in plan["slots"]:
            component = components.get(slot["component_id"])
            if component is None:
                failures.append("frontier execution bundle uses an unknown component")
                continue
            slots_by_component[slot["component_id"]].append(slot)
            if (
                slot["model_id"] not in component["model_ids"]
                or slot["purpose"] not in component["purposes"]
                or case_routes.get(slot["case_id"])
                != (slot["route"], slot["route_authority_id"])
            ):
                failures.append("frontier execution slot exceeds campaign authority")
        for component_id, component in components.items():
            expected_count = int(component["unit_count"]) * int(
                component["attempts_per_unit"]
            )
            if len(slots_by_component[component_id]) != expected_count:
                failures.append("frontier execution component coverage is incomplete")

        events_by_id = {
            event.get("reservation_id"): event
            for event in manifest["budget_ledger"].get("events", [])
            if isinstance(event, dict)
        }
        reports_by_id = {
            report.get("attempt_id"): report
            for report in manifest.get("attempt_reports", [])
            if isinstance(report, dict)
        }
        for slot in plan["slots"]:
            component = components.get(slot["component_id"])
            report = reports_by_id.get(slot["slot_id"])
            event = events_by_id.get(slot["slot_id"])
            if component is None or report is None or event is None:
                continue
            maximums = {
                "calls": 1,
                "input_tokens": component["max_input_tokens_per_attempt"],
                "output_tokens": component["max_output_tokens_per_attempt"],
                "money_microunits": component[
                    "max_money_microunits_per_attempt"
                ],
                "wall_clock_ms": component["max_wall_clock_ms_per_attempt"],
            }
            if event.get("reserved") != maximums or any(
                report["usage"][axis] > maximums[axis] for axis in BUDGET_AXES
            ):
                failures.append("frontier execution attempt exceeds frozen maximums")
            model = models.get(slot["model_id"])
            if model is None or any(
                report.get(report_field) != model.get(model_field)
                for report_field, model_field in (
                    ("provider", "provider"),
                    ("model_snapshot", "snapshot"),
                    ("parameters_sha256", "parameters_sha256"),
                    ("retry_policy_sha256", "retry_policy_sha256"),
                    ("cache_policy_sha256", "cache_policy_sha256"),
                    ("seeds_sha256", "seeds_sha256"),
                    ("pricing_entry_sha256", "pricing_entry_sha256"),
                )
            ) or report.get("price_snapshot_sha256") != campaign_plan[
                "pricing"
            ]["price_snapshot_sha256"] or report.get(
                "pricing_currency"
            ) != campaign_plan[
                "pricing"
            ]["currency"]:
                failures.append("frontier execution model or pricing binding drifted")
    try:
        expected_hash = hash_payload(bundle, "bundle_sha256")
    except (OverflowError, RecursionError, TypeError, ValueError):
        expected_hash = None
    if bundle.get("bundle_sha256") != expected_hash:
        failures.append("frontier execution bundle hash mismatch")
    return list(dict.fromkeys(failures))


def build_frontier_execution_bundle(
    campaign_plan: Mapping[str, Any],
    *,
    executions: list[Mapping[str, Any]],
    bundle_id: str = "frontier-execution-bundle",
) -> dict[str, Any]:
    """Build an all-systems execution bundle and reject partial evidence."""

    payload: dict[str, Any] = {
        "schema_version": FRONTIER_CONTRACT_SCHEMA_VERSION,
        "bundle_id": bundle_id,
        "campaign_sha256": campaign_plan.get("campaign_sha256"),
        "system_ids": [item["system_id"] for item in campaign_plan.get("systems", [])],
        "executions": copy.deepcopy([dict(item) for item in executions]),
    }
    payload["bundle_sha256"] = hash_payload(payload, "bundle_sha256")
    if validate_frontier_execution_bundle(payload, campaign_plan=campaign_plan):
        raise ValueError("frontier execution bundle is incomplete")
    return payload
