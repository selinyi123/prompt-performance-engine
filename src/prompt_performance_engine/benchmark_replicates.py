"""Tamper-evident aggregation for repeated benchmark runs."""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from .benchmark import BenchmarkJob, group_jobs_by_domain, validate_benchmark
from .contracts import ARTIFACT_SCHEMA_VERSION, load_strict_json_object
from .case_checks import DOCKER_REQUIRED_CASE_IDS, SOFTWARE_CASE_VERIFIERS
from .evaluation import EvaluationCase, _hard_checks, validate_evaluation
from .evidence import Evidence, infer_evidence
from .hashing import hash_payload, sha256_json
from .validation import validate_artifact

MINIMUM_REPLICATES = 3
SCHEMA_VERSION = ARTIFACT_SCHEMA_VERSION
RELEASE_DOMAIN_COUNT = 12
RELEASE_CASE_COUNT = 60
REPLICATE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
OUTCOMES = ("win", "tie", "loss")
MODEL_CALL_FIELDS = {
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
AUTHORITATIVE_MODEL_PROVIDERS = {"openai", "openai-codex"}
RECEIPT_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
BENCHMARK_JOB_FIELDS = {"job_id", "domain", "source_prompt", "cases"}
BENCHMARK_CASE_FIELDS = {
    "case_id",
    "input_text",
    "rubric",
    "domain",
    "difficulty",
    "tags",
    "required_substrings",
    "forbidden_substrings",
    "require_json",
    "max_characters",
}
RUN_MANIFEST_FIELDS = {"schema_version", "configuration", "manifest_sha256"}
RUN_CONFIGURATION_FIELDS = {
    "suite_id",
    "benchmark_definition_sha256",
    "optimizer_prompt_sha256",
    "domain_profiles_sha256",
    "evaluation_implementation_sha256",
    "python_version",
    "platform_system",
    "package_version",
    "evaluation_protocol",
    "model",
    "reasoning_effort",
    "temperature",
    "max_tokens",
    "generation_seed",
    "candidate_count",
    "replicate_id",
    "software_sandbox_image",
    "blind_seed",
}
RUN_CONFIGURATION_HASH_FIELDS = {
    "benchmark_definition_sha256",
    "optimizer_prompt_sha256",
    "domain_profiles_sha256",
    "evaluation_implementation_sha256",
}
RUN_CONFIGURATION_STRING_FIELDS = {
    "suite_id",
    "python_version",
    "platform_system",
    "package_version",
    "evaluation_protocol",
    "model",
    "reasoning_effort",
}
REASONING_EFFORTS = {"minimal", "low", "medium", "high", "xhigh"}
RUN_SUMMARY_FIELDS = {
    "schema_version",
    "suite_id",
    "replicate_id",
    "benchmark_definition_sha256",
    "run_manifest_sha256",
    "completed_domains",
    "domain_count",
    "case_count",
    "wins",
    "ties",
    "losses",
    "net_improvement",
    "critical_regressions",
    "fatal_flaws",
    "optimized_hard_failures",
    "all_domains_pass",
    "aggregate_gate_passed",
    "evaluation_protocol",
    "usage",
    "evidence",
    "domain_results",
    "summary_sha256",
}
SUMMARY_COUNT_FIELDS = {
    "domain_count",
    "case_count",
    "wins",
    "ties",
    "losses",
    "critical_regressions",
    "fatal_flaws",
    "optimized_hard_failures",
}
SUMMARY_DOMAIN_RESULT_FIELDS = {
    "case_count",
    "wins",
    "ties",
    "losses",
    "critical_regressions",
    "fatal_flaws",
    "optimized_hard_failures",
    "gate_passed",
}
SUMMARY_PROTOCOL_BASE_FIELDS = {
    "version",
    "implementation_sha256",
    "python_version",
    "platform_system",
    "repeated_run",
    "cross_model",
}
SUMMARY_PROTOCOL_RUNNER_DETAILS = {
    "execution_model": "fixed per run",
    "temperature": "provider default; not configurable by Codex CLI",
    "max_tokens": "provider default; not configurable by Codex CLI",
    "generation_seed": "not configurable by Codex CLI",
    "blind_judges": 2,
    "judge_independence": "separate same-model calls and caches",
}


class ModelCallReceiptVerifier(Protocol):
    """Trusted boundary that binds one provider call to its evaluation context."""

    def verify(
        self,
        *,
        metadata: Mapping[str, Any],
        context_sha256: str,
    ) -> str | None:
        """Return a unique canonical receipt digest, or None when unverified."""


def _is_json_integer(value: Any, *, minimum: int | None = None) -> bool:
    if isinstance(value, bool):
        return False
    valid = isinstance(value, int) or (
        isinstance(value, float) and math.isfinite(value) and value.is_integer()
    )
    return valid and (minimum is None or value >= minimum)


def _normalized_finite_float(value: Any) -> float | None:
    """Convert one JSON number to a finite runtime float without overflowing."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        normalized = float(value)
    except (OverflowError, ValueError):
        return None
    return normalized if math.isfinite(normalized) else None


def _validate_json_count_fields(
    value: Any,
    fields: set[str],
    *,
    label: str,
    failures: list[str],
    minimum: int = 0,
) -> None:
    """Enforce JSON Schema integer semantics without Python bool aliases."""

    if not isinstance(value, dict):
        failures.append(f"{label} must be an object")
        return
    for field in fields:
        if not _is_json_integer(value.get(field), minimum=minimum):
            failures.append(f"{label} {field} must be an integer")


def _valid_model_call_metadata(
    metadata: Any,
    *,
    expected_model: str | None = None,
    authoritative: bool = False,
) -> bool:
    if not isinstance(metadata, dict) or set(metadata) != MODEL_CALL_FIELDS:
        return False
    provider = metadata.get("provider")
    if not isinstance(provider, str) or not provider.strip():
        return False
    model = metadata.get("model")
    if not isinstance(model, str) or not model:
        return False
    if expected_model is not None and model != expected_model:
        return False
    usage = metadata.get("usage")
    if not isinstance(usage, dict) or any(
        not isinstance(key, str)
        or not _is_json_integer(value, minimum=0)
        for key, value in usage.items()
    ):
        return False
    response_id = metadata.get("response_id")
    if response_id is not None and not isinstance(response_id, str):
        return False
    if (
        not _is_json_integer(metadata.get("attempts"), minimum=1)
    ):
        return False
    if (
        not _is_json_integer(metadata.get("elapsed_ms"), minimum=0)
    ):
        return False
    status = metadata.get("status")
    if not isinstance(status, str) or not status.strip():
        return False
    if not isinstance(metadata.get("purpose"), str) or not metadata["purpose"].strip():
        return False
    if any(
        not isinstance(metadata.get(field), str)
        or re.fullmatch(r"[0-9a-f]{64}", metadata[field]) is None
        for field in ("request_sha256", "response_sha256")
    ):
        return False
    if authoritative and (
        provider not in AUTHORITATIVE_MODEL_PROVIDERS
        or not isinstance(response_id, str)
        or not response_id.strip()
        or not usage
        or sum(usage.values()) <= 0
        or status != "completed"
    ):
        return False
    return True


def _model_calls_from_payloads(
    artifacts: Sequence[dict[str, Any]],
    evaluations: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for artifact in artifacts:
        runtime = artifact.get("runtime") if isinstance(artifact, dict) else None
        model_calls = runtime.get("model_calls") if isinstance(runtime, dict) else None
        if isinstance(model_calls, list):
            calls.extend(call for call in model_calls if isinstance(call, dict))
    for evaluation in evaluations:
        records = evaluation.get("records") if isinstance(evaluation, dict) else None
        if not isinstance(records, list):
            continue
        for record in records:
            if not isinstance(record, dict):
                continue
            execution = record.get("execution_metadata")
            if isinstance(execution, dict):
                for side in ("original", "optimized"):
                    metadata = execution.get(side)
                    if isinstance(metadata, dict):
                        calls.append(metadata)
            judges = record.get("judge_decisions")
            if isinstance(judges, list):
                for decision in judges:
                    if isinstance(decision, dict):
                        metadata = decision.get("metadata")
                        if isinstance(metadata, dict):
                            calls.append(metadata)
    return calls


def _receipt_inputs(
    *,
    replicate_id: str,
    configuration: Mapping[str, Any],
    artifacts: Mapping[str, dict[str, Any]],
    evaluations: Mapping[str, dict[str, Any]],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    inputs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    run_configuration_sha256 = sha256_json(configuration)
    for domain in sorted(artifacts):
        artifact = artifacts[domain]
        for ordinal, metadata in enumerate(
            artifact["runtime"]["model_calls"], start=1
        ):
            inputs.append(
                (
                    metadata,
                    {
                        "replicate_id": replicate_id,
                        "run_configuration_sha256": run_configuration_sha256,
                        "domain": domain,
                        "role": metadata["purpose"],
                        "ordinal": ordinal,
                        "request_sha256": metadata["request_sha256"],
                        "response_sha256": metadata["response_sha256"],
                        "artifact_payload_sha256": artifact[
                            "artifact_payload_sha256"
                        ],
                        "source_sha256": artifact["source_sha256"],
                        "optimized_prompt_sha256": hashlib.sha256(
                            artifact["optimized_prompt"].encode("utf-8")
                        ).hexdigest(),
                    },
                )
            )
        for record in sorted(
            evaluations[domain]["records"], key=lambda item: item["case_id"]
        ):
            execution_config_sha256 = sha256_json(record["execution_config"])
            blind_map_sha256 = sha256_json(record["blind_map"])
            for side in ("original", "optimized"):
                metadata = record["execution_metadata"][side]
                inputs.append(
                    (
                        metadata,
                        {
                            "replicate_id": replicate_id,
                            "run_configuration_sha256": run_configuration_sha256,
                            "domain": domain,
                            "case_id": record["case_id"],
                            "role": "benchmark_execution",
                            "side": side,
                            "request_sha256": metadata["request_sha256"],
                            "response_sha256": metadata["response_sha256"],
                            "case_sha256": record["case_sha256"],
                            "execution_config_sha256": execution_config_sha256,
                            "hard_checks_sha256": sha256_json(
                                record["hard_checks"][side]
                            ),
                            "prompt_sha256": record[f"{side}_prompt_sha256"],
                            "output_sha256": record[f"{side}_output_sha256"],
                        },
                    )
                )
            for ordinal, decision in enumerate(record["judge_decisions"], start=1):
                metadata = decision["metadata"]
                inputs.append(
                    (
                        metadata,
                        {
                            "replicate_id": replicate_id,
                            "run_configuration_sha256": run_configuration_sha256,
                            "domain": domain,
                            "case_id": record["case_id"],
                            "case_sha256": record["case_sha256"],
                            "role": "benchmark_judge",
                            "ordinal": ordinal,
                            "judge": decision["judge"],
                            "execution_config_sha256": execution_config_sha256,
                            "blind_map_sha256": blind_map_sha256,
                            "request_sha256": metadata["request_sha256"],
                            "response_sha256": metadata["response_sha256"],
                            "original_output_sha256": record[
                                "original_output_sha256"
                            ],
                            "optimized_output_sha256": record[
                                "optimized_output_sha256"
                            ],
                            "winner": decision["winner"],
                            "fatal_flaw_a": decision["fatal_flaw_a"],
                            "fatal_flaw_b": decision["fatal_flaw_b"],
                        },
                    )
                )
    return inputs


def _verify_receipts(
    inputs: Sequence[tuple[dict[str, Any], dict[str, Any]]],
    verifier: ModelCallReceiptVerifier | None,
) -> list[str]:
    if verifier is None:
        return []
    receipts: list[str] = []
    for metadata, context in inputs:
        context_sha256 = sha256_json(context)
        try:
            receipt = verifier.verify(
                metadata=metadata,
                context_sha256=context_sha256,
            )
        except Exception as exc:
            raise ValueError("Provider call receipt verification failed.") from exc
        if not isinstance(receipt, str) or RECEIPT_DIGEST_RE.fullmatch(receipt) is None:
            raise ValueError("Provider call receipt is missing or invalid.")
        receipts.append(receipt)
    if len(receipts) != len(set(receipts)):
        raise ValueError("Provider call receipts are reused within one benchmark run.")
    return sorted(receipts)


def actual_usage_from_payloads(
    artifacts: Sequence[dict[str, Any]],
    evaluations: Sequence[dict[str, Any]],
) -> dict[str, int]:
    """Recompute model-call usage from hash-bound artifacts and evaluations."""
    totals: dict[str, int] = {"actual_model_calls": 0}
    for metadata in _model_calls_from_payloads(artifacts, evaluations):
        if not _valid_model_call_metadata(metadata):
            continue
        totals["actual_model_calls"] += 1
        for key, value in metadata["usage"].items():
            totals[key] = totals.get(key, 0) + value
    return totals


def _evidence_payload(evidence: Evidence) -> dict[str, Any]:
    payload = asdict(evidence)
    payload["limitations"] = list(evidence.limitations)
    return payload


def _replicate_evidence(
    *,
    matched_observations: int,
    release_gate_passed: bool,
) -> Evidence:
    """Materialize E3 only inside the validated replicate-report contract."""
    base = infer_evidence(
        deterministic_checks_passed=True,
        matched_cases=matched_observations,
        comparative_improvement_passed=release_gate_passed,
    )
    if release_gate_passed and base.level == "E2":
        return Evidence(
            level="E3",
            status="verified_scoped",
            claim="verified_improvement",
            limitations=base.limitations,
        )
    return base


def validate_replicate_id(
    value: Any,
    *,
    required: bool = True,
) -> str | None:
    """Validate and return a stable repeated-run identifier."""
    if value is None:
        if required:
            raise ValueError("replicate_id is required for repeated-run aggregation.")
        return None
    if not isinstance(value, str) or REPLICATE_ID_RE.fullmatch(value) is None:
        raise ValueError(
            "replicate_id must be 1-64 characters, start with an alphanumeric "
            "character, and contain only letters, digits, '.', '_', or '-'."
        )
    return value


def _validate_run_configuration(
    value: Any,
    *,
    root: Path,
) -> dict[str, Any]:
    """Return the exact, type-normalized runner configuration contract."""

    if not isinstance(value, dict) or set(value) != RUN_CONFIGURATION_FIELDS:
        raise ValueError(f"{root}: run manifest configuration fields are invalid.")
    configuration = dict(value)
    if any(
        not isinstance(configuration[field], str)
        or not configuration[field].strip()
        for field in RUN_CONFIGURATION_STRING_FIELDS
    ):
        raise ValueError(
            f"{root}: run manifest configuration strings must be non-empty."
        )
    if configuration["reasoning_effort"] not in REASONING_EFFORTS:
        raise ValueError(f"{root}: run reasoning_effort is unsupported.")
    if any(
        not isinstance(configuration[field], str)
        or RECEIPT_DIGEST_RE.fullmatch(configuration[field]) is None
        for field in RUN_CONFIGURATION_HASH_FIELDS
    ):
        raise ValueError(f"{root}: run configuration hashes are invalid.")

    temperature = configuration["temperature"]
    if temperature is not None:
        normalized_temperature = _normalized_finite_float(temperature)
        if normalized_temperature is None:
            raise ValueError(
                f"{root}: run temperature must be a finite number or null."
            )
        configuration["temperature"] = normalized_temperature

    for field, minimum, nullable in (
        ("max_tokens", 1, True),
        ("generation_seed", None, True),
        ("candidate_count", 1, False),
        ("blind_seed", None, False),
    ):
        candidate = configuration[field]
        if candidate is None and nullable:
            continue
        if not _is_json_integer(candidate, minimum=minimum):
            suffix = " or null" if nullable else ""
            raise ValueError(
                f"{root}: run {field} must be an integer{suffix}."
            )
        configuration[field] = int(candidate)
    if configuration["candidate_count"] > 5:
        raise ValueError(f"{root}: run candidate_count must be between 1 and 5.")

    validate_replicate_id(configuration["replicate_id"])
    sandbox_image = configuration["software_sandbox_image"]
    if sandbox_image is not None and (
        not isinstance(sandbox_image, str)
        or re.search(r"@sha256:[0-9a-f]{64}$", sandbox_image) is None
    ):
        raise ValueError(
            f"{root}: run software_sandbox_image must be digest-pinned or null."
        )
    return configuration


def _validate_run_summary(value: Any, *, root: Path) -> dict[str, Any]:
    """Validate the exact source-summary transport before derived comparisons."""

    if not isinstance(value, dict) or set(value) != RUN_SUMMARY_FIELDS:
        raise ValueError(f"{root}: run summary fields are invalid.")
    summary = value
    if summary.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"{root}: unsupported summary schema.")
    if any(
        not isinstance(summary.get(field), str)
        or RECEIPT_DIGEST_RE.fullmatch(summary[field]) is None
        for field in (
            "benchmark_definition_sha256",
            "run_manifest_sha256",
            "summary_sha256",
        )
    ):
        raise ValueError(f"{root}: run summary hashes are invalid.")
    if not isinstance(summary.get("suite_id"), str) or not summary["suite_id"]:
        raise ValueError(f"{root}: run summary suite_id is invalid.")
    validate_replicate_id(summary.get("replicate_id"))

    completed = summary.get("completed_domains")
    if (
        not isinstance(completed, list)
        or not completed
        or any(not isinstance(domain, str) or not domain for domain in completed)
        or len(completed) != len(set(completed))
    ):
        raise ValueError(f"{root}: completed_domains must be unique non-empty strings.")
    if any(
        not _is_json_integer(summary.get(field), minimum=0)
        for field in SUMMARY_COUNT_FIELDS
    ):
        raise ValueError(f"{root}: run summary counts are invalid.")
    net_improvement = summary.get("net_improvement")
    normalized_net_improvement = _normalized_finite_float(net_improvement)
    if (
        normalized_net_improvement is None
        or not -1.0 <= normalized_net_improvement <= 1.0
    ):
        raise ValueError(f"{root}: run summary net_improvement is invalid.")
    for field in ("all_domains_pass", "aggregate_gate_passed"):
        if not isinstance(summary.get(field), bool):
            raise ValueError(f"{root}: summary {field} must be a boolean.")

    protocol = summary.get("evaluation_protocol")
    allowed_protocol_fields = (
        SUMMARY_PROTOCOL_BASE_FIELDS,
        SUMMARY_PROTOCOL_BASE_FIELDS | set(SUMMARY_PROTOCOL_RUNNER_DETAILS),
    )
    if not isinstance(protocol, dict) or set(protocol) not in allowed_protocol_fields:
        raise ValueError(f"{root}: summary evaluation_protocol fields are invalid.")
    if any(
        not isinstance(protocol.get(field), str) or not protocol[field]
        for field in ("version", "python_version", "platform_system")
    ) or (
        not isinstance(protocol.get("implementation_sha256"), str)
        or RECEIPT_DIGEST_RE.fullmatch(protocol["implementation_sha256"]) is None
    ):
        raise ValueError(f"{root}: summary evaluation_protocol identity is invalid.")
    if not isinstance(protocol.get("repeated_run"), bool) or not isinstance(
        protocol.get("cross_model"), bool
    ):
        raise ValueError(f"{root}: summary evaluation protocol flags are invalid.")
    if set(protocol) != SUMMARY_PROTOCOL_BASE_FIELDS and any(
        protocol.get(field) != expected
        for field, expected in SUMMARY_PROTOCOL_RUNNER_DETAILS.items()
    ):
        raise ValueError(f"{root}: summary evaluation protocol details are invalid.")

    usage = summary.get("usage")
    if (
        not isinstance(usage, dict)
        or "actual_model_calls" not in usage
        or any(
            not isinstance(key, str) or not _is_json_integer(count, minimum=0)
            for key, count in usage.items()
        )
        or not _is_json_integer(usage.get("actual_model_calls"), minimum=0)
    ):
        raise ValueError(f"{root}: run summary usage is invalid.")

    evidence = summary.get("evidence")
    if (
        not isinstance(evidence, dict)
        or set(evidence) != {"level", "status", "claim", "limitations"}
        or any(
            not isinstance(evidence.get(field), str) or not evidence[field]
            for field in ("level", "status", "claim")
        )
        or not isinstance(evidence.get("limitations"), list)
        or any(
            not isinstance(item, str) or not item
            for item in evidence.get("limitations", [])
        )
    ):
        raise ValueError(f"{root}: run summary evidence is invalid.")

    domain_results = summary.get("domain_results")
    if not isinstance(domain_results, dict) or not domain_results:
        raise ValueError(f"{root}: summary domain_results must be a non-empty object.")
    for domain, result in domain_results.items():
        if (
            not isinstance(domain, str)
            or not domain
            or not isinstance(result, dict)
            or set(result) != SUMMARY_DOMAIN_RESULT_FIELDS
        ):
            raise ValueError(f"{root}: summary domain result fields are invalid.")
        if any(
            not _is_json_integer(result.get(field), minimum=0)
            for field in SUMMARY_DOMAIN_RESULT_FIELDS - {"gate_passed"}
        ):
            raise ValueError(f"{root}: summary domain result counts are invalid.")
        if not isinstance(result.get("gate_passed"), bool):
            raise ValueError(
                f"{root}: summary domain result gate_passed must be a boolean."
            )
    return summary


def _load_json(path: Path, label: str) -> dict[str, Any]:
    return load_strict_json_object(path, label=label)


def _load_benchmark_snapshot(
    root: Path,
) -> tuple[dict[str, Any], dict[str, BenchmarkJob]]:
    """Load the resolved benchmark source copied into one run directory."""
    snapshot = _load_json(
        root / "benchmark-definition.json",
        "resolved benchmark definition",
    )
    if set(snapshot) != {"suite_id", "jobs"}:
        raise ValueError(f"{root}: benchmark definition fields are invalid.")
    suite_id = snapshot.get("suite_id")
    raw_jobs = snapshot.get("jobs")
    if (
        not isinstance(suite_id, str)
        or not suite_id
        or not isinstance(raw_jobs, list)
        or not raw_jobs
    ):
        raise ValueError(f"{root}: benchmark definition root is invalid.")

    jobs: list[BenchmarkJob] = []
    for raw_job in raw_jobs:
        if not isinstance(raw_job, dict) or set(raw_job) != BENCHMARK_JOB_FIELDS:
            raise ValueError(f"{root}: benchmark job fields are invalid.")
        if any(
            not isinstance(raw_job.get(field), str) or not raw_job[field]
            for field in ("job_id", "domain", "source_prompt")
        ):
            raise ValueError(f"{root}: benchmark job identity is invalid.")
        raw_cases = raw_job.get("cases")
        if not isinstance(raw_cases, list) or not raw_cases:
            raise ValueError(f"{root}: benchmark job cases are invalid.")
        cases: list[EvaluationCase] = []
        for raw_case in raw_cases:
            if not isinstance(raw_case, dict) or set(raw_case) != BENCHMARK_CASE_FIELDS:
                raise ValueError(f"{root}: benchmark case fields are invalid.")
            if any(
                not isinstance(raw_case.get(field), str)
                for field in ("case_id", "input_text", "domain", "difficulty")
            ):
                raise ValueError(f"{root}: benchmark case identity is invalid.")
            for field in (
                "rubric",
                "tags",
                "required_substrings",
                "forbidden_substrings",
            ):
                value = raw_case.get(field)
                if not isinstance(value, list) or any(
                    not isinstance(item, str) for item in value
                ):
                    raise ValueError(
                        f"{root}: benchmark case {field} must be strings."
                    )
            if not isinstance(raw_case.get("require_json"), bool):
                raise ValueError(f"{root}: benchmark case require_json is invalid.")
            max_characters = raw_case.get("max_characters")
            if max_characters is not None and (
                not _is_json_integer(max_characters, minimum=1)
            ):
                raise ValueError(f"{root}: benchmark case max_characters is invalid.")
            cases.append(
                EvaluationCase(
                    case_id=raw_case["case_id"],
                    input_text=raw_case["input_text"],
                    rubric=tuple(raw_case["rubric"]),
                    domain=raw_case["domain"],
                    difficulty=raw_case["difficulty"],
                    tags=tuple(raw_case["tags"]),
                    required_substrings=tuple(raw_case["required_substrings"]),
                    forbidden_substrings=tuple(raw_case["forbidden_substrings"]),
                    require_json=raw_case["require_json"],
                    max_characters=(
                        int(max_characters) if max_characters is not None else None
                    ),
                )
            )
        jobs.append(
            BenchmarkJob(
                job_id=raw_job["job_id"],
                domain=raw_job["domain"],
                source_prompt=raw_job["source_prompt"],
                cases=tuple(cases),
            )
        )
    benchmark_failures = validate_benchmark(suite_id, tuple(jobs))
    if benchmark_failures:
        raise ValueError(
            f"{root}: resolved benchmark definition is invalid: {benchmark_failures}"
        )
    try:
        grouped = group_jobs_by_domain(tuple(jobs))
    except ValueError as exc:
        raise ValueError(f"{root}: resolved benchmark definition is invalid: {exc}") from exc
    return snapshot, grouped


def _replayable_hard_check_group(
    case: EvaluationCase,
    output: str,
    observed: Any,
) -> bool:
    """Compare checks that can be replayed without executing candidate software."""

    expected = _hard_checks(case, output, software_sandbox=None)
    if case.case_id not in DOCKER_REQUIRED_CASE_IDS:
        return observed == expected
    if not isinstance(observed, dict) or set(observed) != {"passed", "checks"}:
        return False
    observed_checks = observed.get("checks")
    if not isinstance(observed_checks, list):
        return False
    expected_replayable = [
        check
        for check in expected["checks"]
        if check.get("source") != "case_plugin"
    ]
    observed_replayable = [
        check
        for check in observed_checks
        if isinstance(check, dict) and check.get("source") != "case_plugin"
    ]
    sandbox_checks = [
        check
        for check in observed_checks
        if isinstance(check, dict) and check.get("source") == "case_plugin"
    ]
    expected_name = SOFTWARE_CASE_VERIFIERS[case.case_id][0]
    return (
        observed_replayable == expected_replayable
        and len(sandbox_checks) == 1
        and sandbox_checks[0].get("check") == expected_name
        and sandbox_checks[0].get("authoritative") is True
    )


def _normalized_configuration(configuration: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in configuration.items() if key != "replicate_id"}


def _expected_summary(
    evaluations: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    case_count = sum(item["case_count"] for item in evaluations.values())
    wins = sum(item["wins"] for item in evaluations.values())
    ties = sum(item["ties"] for item in evaluations.values())
    losses = sum(item["losses"] for item in evaluations.values())
    critical = sum(item["critical_regressions"] for item in evaluations.values())
    fatal = sum(item["fatal_flaws"] for item in evaluations.values())
    hard = sum(item["optimized_hard_failures"] for item in evaluations.values())
    all_domains_pass = bool(evaluations) and all(
        item["gate_passed"] is True for item in evaluations.values()
    )
    net = (wins - losses) / case_count if case_count else 0.0
    aggregate_gate = (
        len(evaluations) == RELEASE_DOMAIN_COUNT
        and all_domains_pass
        and net >= 0.10
        and critical == 0
        and fatal == 0
        and hard == 0
    )
    return {
        "completed_domains": sorted(evaluations),
        "domain_count": len(evaluations),
        "case_count": case_count,
        "wins": wins,
        "ties": ties,
        "losses": losses,
        "net_improvement": net,
        "critical_regressions": critical,
        "fatal_flaws": fatal,
        "optimized_hard_failures": hard,
        "all_domains_pass": all_domains_pass,
        "aggregate_gate_passed": aggregate_gate,
    }


def _load_run(
    run_directory: Path,
    *,
    receipt_verifier: ModelCallReceiptVerifier | None = None,
) -> dict[str, Any]:
    root = run_directory.resolve()
    benchmark_snapshot, benchmark_jobs = _load_benchmark_snapshot(root)
    benchmark_definition_sha256 = sha256_json(benchmark_snapshot)
    manifest = _load_json(root / "run-manifest.json", "run manifest")
    if set(manifest) != RUN_MANIFEST_FIELDS:
        raise ValueError(f"{root}: run manifest fields are invalid.")
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"{root}: unsupported run manifest schema.")
    if manifest.get("manifest_sha256") != hash_payload(manifest, "manifest_sha256"):
        raise ValueError(f"{root}: run manifest hash mismatch.")
    configuration = _validate_run_configuration(
        manifest.get("configuration"),
        root=root,
    )
    if configuration.get("suite_id") != benchmark_snapshot["suite_id"]:
        raise ValueError(f"{root}: benchmark definition suite_id mismatch.")
    if (
        configuration.get("benchmark_definition_sha256")
        != benchmark_definition_sha256
    ):
        raise ValueError(f"{root}: benchmark definition source hash mismatch.")
    replicate_id = validate_replicate_id(configuration.get("replicate_id"))

    summary = _validate_run_summary(
        _load_json(root / "summary.json", "run summary"),
        root=root,
    )
    if summary.get("summary_sha256") != hash_payload(summary, "summary_sha256"):
        raise ValueError(f"{root}: summary hash mismatch.")
    if summary.get("replicate_id") != replicate_id:
        raise ValueError(f"{root}: summary replicate_id mismatch.")
    if summary.get("run_manifest_sha256") != manifest.get("manifest_sha256"):
        raise ValueError(f"{root}: summary is not bound to its run manifest.")
    for field in ("suite_id", "benchmark_definition_sha256"):
        if summary.get(field) != configuration.get(field):
            raise ValueError(f"{root}: summary {field} mismatch.")

    completed = summary.get("completed_domains")
    if (
        not isinstance(completed, list)
        or not completed
        or not all(isinstance(item, str) and item for item in completed)
    ):
        raise ValueError(f"{root}: completed_domains must be non-empty strings.")
    if len(set(completed)) != len(completed):
        raise ValueError(f"{root}: completed_domains contains duplicates.")
    discovered = sorted(path.parent.name for path in root.glob("*/evaluation.json"))
    if sorted(completed) != discovered:
        raise ValueError(f"{root}: summary/evaluation domain set mismatch.")

    expected_model = configuration.get("model")
    if not isinstance(expected_model, str) or not expected_model:
        raise ValueError(f"{root}: run model must be a non-empty string.")
    expected_execution_config = {
        "model": expected_model,
        "temperature": configuration.get("temperature"),
        "max_tokens": configuration.get("max_tokens"),
        "seed": configuration.get("generation_seed"),
    }
    expected_blind_seed = configuration.get("blind_seed")

    evaluations: dict[str, dict[str, Any]] = {}
    artifacts: dict[str, dict[str, Any]] = {}
    for domain in completed:
        benchmark_job = benchmark_jobs.get(domain)
        if benchmark_job is None:
            raise ValueError(f"{root}: {domain} is absent from the benchmark definition.")
        path = (root / domain / "evaluation.json").resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"{root}: unsafe domain path {domain!r}.") from exc
        evaluation = _load_json(path, f"{domain} evaluation")
        failures = validate_evaluation(evaluation)
        if failures:
            raise ValueError(f"{root}: invalid {domain} evaluation: {failures}")
        if evaluation.get("repeated_or_cross_model") is not False:
            raise ValueError(
                f"{root}: component evaluation must be a single-run result."
            )
        if evaluation.get("suite_id") != f"{configuration.get('suite_id')}:{domain}":
            raise ValueError(f"{root}: {domain} evaluation suite_id mismatch.")
        expected_case_definitions = {
            case.case_id: case for case in benchmark_job.cases
        }
        expected_cases = {
            case_id: sha256_json(asdict(case))
            for case_id, case in expected_case_definitions.items()
        }
        observed_cases = {
            record.get("case_id"): record.get("case_sha256")
            for record in evaluation["records"]
        }
        if observed_cases != expected_cases:
            raise ValueError(
                f"{root}: {domain} evaluation does not match the benchmark source."
            )
        for record in evaluation["records"]:
            case_id = record.get("case_id")
            case = expected_case_definitions[case_id]
            if record.get("domain") != domain:
                raise ValueError(f"{root}: {domain}/{case_id} domain mismatch.")
            if (
                record.get("difficulty") != case.difficulty
                or record.get("rubric") != list(case.rubric)
            ):
                raise ValueError(
                    f"{root}: {domain}/{case_id} evaluation case contract "
                    "does not match the benchmark source."
                )
            hard_checks = record.get("hard_checks")
            if not isinstance(hard_checks, dict) or any(
                not _replayable_hard_check_group(
                    case,
                    record[f"{side}_output"],
                    hard_checks.get(side),
                )
                for side in ("original", "optimized")
            ):
                raise ValueError(
                    f"{root}: {domain}/{case_id} deterministic hard checks "
                    "do not match the benchmark case and recorded outputs."
                )
            if record.get("execution_config") != expected_execution_config:
                raise ValueError(
                    f"{root}: {domain}/{case_id} execution configuration mismatch."
                )
            blind_map = record.get("blind_map")
            if not isinstance(blind_map, dict) or blind_map.get(
                "seed"
            ) != expected_blind_seed:
                raise ValueError(f"{root}: {domain}/{case_id} blind seed mismatch.")
            execution_metadata = record.get("execution_metadata")
            if not isinstance(execution_metadata, dict) or any(
                not _valid_model_call_metadata(
                    execution_metadata.get(side),
                    expected_model=expected_model,
                    authoritative=True,
                )
                for side in ("original", "optimized")
            ):
                raise ValueError(
                    f"{root}: {domain}/{case_id} execution provenance mismatch."
                )
            for side in ("original", "optimized"):
                metadata = execution_metadata[side]
                if (
                    metadata.get("purpose") != "benchmark_execution"
                    or metadata.get("response_sha256")
                    != record.get(f"{side}_output_sha256")
                ):
                    raise ValueError(
                        f"{root}: {domain}/{case_id} execution response binding mismatch."
                    )
            judge_decisions = record.get("judge_decisions")
            if not isinstance(judge_decisions, list):
                raise ValueError(f"{root}: {domain}/{case_id} judge records are invalid.")
            both_passed = all(
                isinstance(record.get("hard_checks", {}).get(side), dict)
                and record["hard_checks"][side].get("passed") is True
                for side in ("original", "optimized")
            )
            if both_passed and len(judge_decisions) < 2:
                raise ValueError(
                    f"{root}: {domain}/{case_id} lacks independent judge calls."
                )
            if any(
                not isinstance(decision, dict)
                or not _valid_model_call_metadata(
                    decision.get("metadata"),
                    expected_model=expected_model,
                    authoritative=True,
                )
                for decision in judge_decisions
            ):
                raise ValueError(
                    f"{root}: {domain}/{case_id} judge provenance mismatch."
                )
            if any(
                decision["metadata"].get("purpose") != "benchmark_judge"
                for decision in judge_decisions
            ):
                raise ValueError(
                    f"{root}: {domain}/{case_id} judge purpose mismatch."
                )
        artifact = _load_json(
            root / domain / "optimization.json",
            f"{domain} optimization artifact",
        )
        violations = validate_artifact(artifact)
        if violations:
            raise ValueError(
                f"{root}: invalid {domain} optimization artifact: "
                f"{[item.detail for item in violations]}"
            )
        if artifact.get("domain") != domain:
            raise ValueError(f"{root}: {domain} optimization domain mismatch.")
        if artifact.get("source_prompt") != benchmark_job.source_prompt:
            raise ValueError(f"{root}: {domain} benchmark source Prompt mismatch.")
        model_calls = artifact.get("runtime", {}).get("model_calls")
        if not isinstance(model_calls, list) or any(
            not _valid_model_call_metadata(
                call,
                expected_model=expected_model,
                authoritative=True,
            )
            for call in model_calls
        ):
            raise ValueError(f"{root}: {domain} optimization model mismatch.")
        purposes = [call.get("purpose") for call in model_calls]
        allowed_purposes = {
            "optimization_candidate",
            "optimization_repair",
            "optimization_selector",
        }
        if any(purpose not in allowed_purposes for purpose in purposes):
            raise ValueError(f"{root}: {domain} optimization purpose mismatch.")
        selection = artifact.get("runtime", {}).get("selection", {})
        if selection.get("candidate_count") != configuration.get("candidate_count"):
            raise ValueError(f"{root}: {domain} candidate count mismatch.")
        candidate_count = selection.get("candidate_count")
        if purposes.count("optimization_candidate") != candidate_count:
            raise ValueError(f"{root}: {domain} optimization candidate calls mismatch.")
        expected_selector_calls = 1 if candidate_count > 1 else 0
        if purposes.count("optimization_selector") != expected_selector_calls:
            raise ValueError(f"{root}: {domain} optimization selector calls mismatch.")
        selector_calls = [
            call for call in model_calls if call["purpose"] == "optimization_selector"
        ]
        if selector_calls and selector_calls[0]["response_sha256"] != selection.get(
            "selector_response_sha256"
        ):
            raise ValueError(f"{root}: {domain} selector response binding mismatch.")
        original_hashes = {
            record["original_prompt_sha256"] for record in evaluation["records"]
        }
        optimized_hashes = {
            record["optimized_prompt_sha256"] for record in evaluation["records"]
        }
        expected_optimized_hash = hashlib.sha256(
            artifact["optimized_prompt"].encode("utf-8")
        ).hexdigest()
        if original_hashes != {artifact.get("source_sha256")}:
            raise ValueError(f"{root}: {domain} source Prompt binding mismatch.")
        if optimized_hashes != {expected_optimized_hash}:
            raise ValueError(f"{root}: {domain} optimized Prompt binding mismatch.")
        evaluations[domain] = evaluation
        artifacts[domain] = artifact

    execution_identities = sorted(
        f"{metadata['provider']}\0{metadata['response_id']}"
        for metadata in _model_calls_from_payloads(
            list(artifacts.values()),
            list(evaluations.values()),
        )
    )
    if len(execution_identities) != len(set(execution_identities)):
        raise ValueError(f"{root}: provider response ids are not unique within the run.")
    receipt_inputs = _receipt_inputs(
        replicate_id=replicate_id,
        configuration=configuration,
        artifacts=artifacts,
        evaluations=evaluations,
    )
    receipt_digests = _verify_receipts(receipt_inputs, receipt_verifier)

    expected = _expected_summary(evaluations)
    for field, value in expected.items():
        if summary.get(field) != value:
            raise ValueError(f"{root}: summary aggregate mismatch: {field}.")
    protocol = summary.get("evaluation_protocol")
    if not isinstance(protocol, dict):
        raise ValueError(f"{root}: summary evaluation_protocol must be an object.")
    if (
        protocol.get("repeated_run") is not False
        or protocol.get("cross_model") is not False
    ):
        raise ValueError(f"{root}: a component summary cannot claim repetition.")
    for field, expected_value in (
        ("version", configuration.get("evaluation_protocol")),
        (
            "implementation_sha256",
            configuration.get("evaluation_implementation_sha256"),
        ),
        ("python_version", configuration.get("python_version")),
        ("platform_system", configuration.get("platform_system")),
    ):
        if protocol.get(field) != expected_value:
            raise ValueError(f"{root}: summary evaluation protocol mismatch: {field}.")
    expected_evidence = _evidence_payload(
        infer_evidence(
            deterministic_checks_passed=True,
            matched_cases=expected["case_count"],
            comparative_improvement_passed=expected["aggregate_gate_passed"],
            repeated_or_cross_model=False,
        )
    )
    if summary.get("evidence") != expected_evidence:
        raise ValueError(f"{root}: summary evidence exceeds its single-run facts.")
    expected_usage = actual_usage_from_payloads(
        list(artifacts.values()),
        list(evaluations.values()),
    )
    if summary.get("usage") != expected_usage:
        raise ValueError(f"{root}: summary model usage does not match source calls.")
    actual_model_calls = expected_usage["actual_model_calls"]
    if actual_model_calls <= 0:
        raise ValueError(f"{root}: no real model calls are recorded.")
    domain_results = summary.get("domain_results")
    if not isinstance(domain_results, dict) or set(domain_results) != set(evaluations):
        raise ValueError(f"{root}: summary domain_results mismatch.")
    fields = (
        "case_count",
        "wins",
        "ties",
        "losses",
        "critical_regressions",
        "fatal_flaws",
        "optimized_hard_failures",
        "gate_passed",
    )
    for domain, evaluation in evaluations.items():
        expected_domain = {field: evaluation[field] for field in fields}
        if domain_results.get(domain) != expected_domain:
            raise ValueError(f"{root}: summary domain result mismatch: {domain}.")
    return {
        "root": root,
        "replicate_id": replicate_id,
        "manifest": manifest,
        "benchmark_definition": benchmark_snapshot,
        "benchmark_definition_sha256": benchmark_definition_sha256,
        "configuration": configuration,
        "summary": summary,
        "actual_model_calls": actual_model_calls,
        "execution_identities": execution_identities,
        "receipt_digests": receipt_digests,
        "evaluations": evaluations,
        "artifacts": artifacts,
    }


def _consensus(counts: dict[str, int]) -> tuple[str, str]:
    maximum = max(counts.values())
    leaders = [outcome for outcome in OUTCOMES if counts[outcome] == maximum]
    if len(leaders) > 1:
        return "tie", "tied_plurality"
    winner = leaders[0]
    if maximum == sum(counts.values()):
        return winner, "unanimous"
    return winner, "plurality"


def _domain_gate_from_observations(
    observations: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    counts = {outcome: 0 for outcome in OUTCOMES}
    for observation in observations:
        counts[observation["outcome"]] += 1
    critical = sum(item["critical_regression"] for item in observations)
    fatal = sum(item["fatal_flaw"] for item in observations)
    hard = sum(item["optimized_hard_failure"] for item in observations)
    gate = (
        len(observations) >= 5
        and counts["win"] > counts["loss"]
        and critical == 0
        and fatal == 0
        and hard == 0
    )
    return {
        "case_count": len(observations),
        "wins": counts["win"],
        "ties": counts["tie"],
        "losses": counts["loss"],
        "critical_regressions": critical,
        "fatal_flaws": fatal,
        "optimized_hard_failures": hard,
        "gate_passed": gate,
    }


def _semantic_payload_fingerprint(
    run: dict[str, Any],
    domains: Sequence[str],
) -> str:
    """Hash model-visible outputs and decisions, excluding timing and call ids."""
    optimizations: dict[str, Any] = {}
    evaluations: dict[str, Any] = {}
    for domain in domains:
        artifact = run["artifacts"][domain]
        selection = artifact["runtime"].get("selection", {})
        optimizations[domain] = {
            "source_sha256": artifact["source_sha256"],
            "optimized_prompt_sha256": hashlib.sha256(
                artifact["optimized_prompt"].encode("utf-8")
            ).hexdigest(),
            "architecture": artifact["architecture"],
            "selection": {
                "method": selection.get("method"),
                "candidate_count": selection.get("candidate_count"),
                "selected_index": selection.get("selected_index"),
            },
        }
        semantic_records = []
        for record in sorted(
            run["evaluations"][domain]["records"],
            key=lambda item: item["case_id"],
        ):
            semantic = {
                key: record[key]
                for key in (
                    "case_id",
                    "case_sha256",
                    "original_prompt_sha256",
                    "optimized_prompt_sha256",
                    "original_output_sha256",
                    "optimized_output_sha256",
                    "outcome",
                    "critical_regression",
                    "fatal_flaw",
                )
            }
            semantic["hard_checks"] = {
                side: sorted(
                    (
                        check["check"],
                        check.get("authoritative", True),
                        check["passed"],
                    )
                    for check in record["hard_checks"][side]["checks"]
                )
                for side in ("original", "optimized")
            }
            semantic["judge_decisions"] = [
                {
                    key: decision[key]
                    for key in ("winner", "fatal_flaw_a", "fatal_flaw_b")
                }
                for decision in record["judge_decisions"]
            ]
            semantic_records.append(semantic)
        evaluations[domain] = semantic_records
    return sha256_json(
        {
            "optimizations": optimizations,
            "evaluations": evaluations,
        }
    )


def aggregate_benchmark_replicates(
    run_directories: Sequence[Path],
    *,
    receipt_verifier: ModelCallReceiptVerifier | None = None,
) -> dict[str, Any]:
    """Aggregate compatible single runs into a repeatability report."""
    if len(run_directories) < MINIMUM_REPLICATES:
        raise ValueError(f"At least {MINIMUM_REPLICATES} benchmark runs are required.")
    runs = [
        _load_run(Path(path), receipt_verifier=receipt_verifier)
        for path in run_directories
    ]
    replicate_ids = [run["replicate_id"] for run in runs]
    if len(set(replicate_ids)) != len(replicate_ids):
        raise ValueError("replicate_id values must be unique.")

    normalized = [_normalized_configuration(run["configuration"]) for run in runs]
    reference_hash = sha256_json(normalized[0])
    if any(sha256_json(item) != reference_hash for item in normalized[1:]):
        raise ValueError("Benchmark run configurations are not compatible.")
    if any(
        run["benchmark_definition"] != runs[0]["benchmark_definition"]
        for run in runs[1:]
    ):
        raise ValueError("Benchmark runs do not use the same definition source.")
    domains = sorted(runs[0]["evaluations"])
    if any(sorted(run["evaluations"]) != domains for run in runs[1:]):
        raise ValueError("Benchmark runs do not cover identical domain sets.")
    seen_execution_identities: set[str] = set()
    seen_receipts: set[str] = set()
    for run in runs:
        identities = set(run["execution_identities"])
        if identities & seen_execution_identities:
            raise ValueError(
                "Benchmark replicates reuse provider response ids and are not "
                "independent executions."
            )
        seen_execution_identities.update(identities)
        receipts = set(run["receipt_digests"])
        if receipts & seen_receipts:
            raise ValueError(
                "Benchmark replicates reuse provider call receipts and are not "
                "independent executions."
            )
        seen_receipts.update(receipts)

    cases: list[dict[str, Any]] = []
    for domain in domains:
        record_maps = [
            {
                record["case_id"]: record
                for record in run["evaluations"][domain]["records"]
            }
            for run in runs
        ]
        case_ids = sorted(record_maps[0])
        if any(sorted(records) != case_ids for records in record_maps[1:]):
            raise ValueError(f"{domain}: benchmark runs have different case sets.")
        for case_id in case_ids:
            records = [record_map[case_id] for record_map in record_maps]
            case_hash = records[0]["case_sha256"]
            if any(record.get("case_sha256") != case_hash for record in records[1:]):
                raise ValueError(f"{domain}/{case_id}: case definition mismatch.")
            observations = []
            counts = {outcome: 0 for outcome in OUTCOMES}
            for run, record in zip(runs, records, strict=True):
                outcome = record["outcome"]
                counts[outcome] += 1
                observations.append(
                    {
                        "replicate_id": run["replicate_id"],
                        "outcome": outcome,
                        "critical_regression": record["critical_regression"] is True,
                        "fatal_flaw": record["fatal_flaw"] is True,
                        "optimized_hard_failure": (
                            record["hard_checks"]["optimized"]["passed"] is not True
                        ),
                    }
                )
            consensus, basis = _consensus(counts)
            cases.append(
                {
                    "domain": domain,
                    "case_id": case_id,
                    "case_sha256": case_hash,
                    "outcome_counts": counts,
                    "observations": observations,
                    "consensus": consensus,
                    "consensus_basis": basis,
                    "agreement_ratio": max(counts.values()) / len(runs),
                    "stable": len([value for value in counts.values() if value]) == 1,
                }
            )

    domain_results: dict[str, dict[str, Any]] = {}
    for domain in domains:
        domain_cases = [item for item in cases if item["domain"] == domain]
        replicate_results = []
        for replicate_id in replicate_ids:
            observations = [
                next(
                    observation
                    for observation in case["observations"]
                    if observation["replicate_id"] == replicate_id
                )
                for case in domain_cases
            ]
            replicate_results.append(
                {
                    "replicate_id": replicate_id,
                    **_domain_gate_from_observations(observations),
                }
            )
        consensus_observations = [
            {
                "outcome": case["consensus"],
                "critical_regression": any(
                    item["critical_regression"] for item in case["observations"]
                ),
                "fatal_flaw": any(item["fatal_flaw"] for item in case["observations"]),
                "optimized_hard_failure": any(
                    item["optimized_hard_failure"] for item in case["observations"]
                ),
            }
            for case in domain_cases
        ]
        consensus_result = _domain_gate_from_observations(consensus_observations)
        individual_pass = all(item["gate_passed"] for item in replicate_results)
        domain_results[domain] = {
            "case_count": len(domain_cases),
            "consensus_wins": consensus_result["wins"],
            "consensus_ties": consensus_result["ties"],
            "consensus_losses": consensus_result["losses"],
            "critical_regressions": sum(
                item["critical_regression"]
                for case in domain_cases
                for item in case["observations"]
            ),
            "fatal_flaws": sum(
                item["fatal_flaw"]
                for case in domain_cases
                for item in case["observations"]
            ),
            "optimized_hard_failures": sum(
                item["optimized_hard_failure"]
                for case in domain_cases
                for item in case["observations"]
            ),
            "all_individual_gates_passed": individual_pass,
            "consensus_gate_passed": consensus_result["gate_passed"],
            "gate_passed": individual_pass and consensus_result["gate_passed"],
            "replicate_results": replicate_results,
        }

    replicate_facts = []
    for run in runs:
        replicate_id = run["replicate_id"]
        results = [
            domain_results[domain]["replicate_results"][
                replicate_ids.index(replicate_id)
            ]
            for domain in domains
        ]
        case_count = sum(item["case_count"] for item in results)
        wins = sum(item["wins"] for item in results)
        ties = sum(item["ties"] for item in results)
        losses = sum(item["losses"] for item in results)
        critical = sum(item["critical_regressions"] for item in results)
        fatal = sum(item["fatal_flaws"] for item in results)
        hard = sum(item["optimized_hard_failures"] for item in results)
        net = (wins - losses) / case_count if case_count else 0.0
        aggregate_gate = (
            len(domains) == RELEASE_DOMAIN_COUNT
            and all(item["gate_passed"] for item in results)
            and net >= 0.10
            and critical == 0
            and fatal == 0
            and hard == 0
        )
        if aggregate_gate != run["summary"]["aggregate_gate_passed"]:
            raise ValueError(f"{replicate_id}: summary release gate mismatch.")
        evaluation_hashes = {
            domain: run["evaluations"][domain]["evaluation_sha256"]
            for domain in domains
        }
        artifact_hashes = {
            domain: run["artifacts"][domain]["artifact_payload_sha256"]
            for domain in domains
        }
        payload_fingerprint = _semantic_payload_fingerprint(run, domains)
        execution_identity_sha256 = sha256_json(run["execution_identities"])
        receipt_bundle_sha256 = sha256_json(run["receipt_digests"])
        verified_model_calls = len(run["receipt_digests"])
        provenance_verified = (
            run["actual_model_calls"] > 0
            and verified_model_calls == run["actual_model_calls"]
        )
        run_fingerprint = sha256_json(
            {
                "run_manifest_sha256": run["manifest"]["manifest_sha256"],
                "summary_sha256": run["summary"]["summary_sha256"],
                "payload_fingerprint_sha256": payload_fingerprint,
                "execution_identity_sha256": execution_identity_sha256,
                "receipt_bundle_sha256": receipt_bundle_sha256,
            }
        )
        replicate_facts.append(
            {
                "replicate_id": replicate_id,
                "run_manifest_sha256": run["manifest"]["manifest_sha256"],
                "summary_sha256": run["summary"]["summary_sha256"],
                "optimization_artifact_sha256": artifact_hashes,
                "evaluation_sha256": evaluation_hashes,
                "payload_fingerprint_sha256": payload_fingerprint,
                "execution_identity_sha256": execution_identity_sha256,
                "receipt_bundle_sha256": receipt_bundle_sha256,
                "run_fingerprint_sha256": run_fingerprint,
                "actual_model_calls": run["actual_model_calls"],
                "verified_model_calls": verified_model_calls,
                "provenance_verified": provenance_verified,
                "domain_count": len(domains),
                "case_count": case_count,
                "wins": wins,
                "ties": ties,
                "losses": losses,
                "critical_regressions": critical,
                "fatal_flaws": fatal,
                "optimized_hard_failures": hard,
                "aggregate_gate_passed": aggregate_gate,
            }
        )
    case_count = len(cases)
    consensus_counts = {
        outcome: sum(case["consensus"] == outcome for case in cases)
        for outcome in OUTCOMES
    }
    critical = sum(
        item["critical_regression"] for case in cases for item in case["observations"]
    )
    fatal = sum(item["fatal_flaw"] for case in cases for item in case["observations"])
    hard = sum(
        item["optimized_hard_failure"]
        for case in cases
        for item in case["observations"]
    )
    stable = sum(case["stable"] for case in cases)
    net = (
        (consensus_counts["win"] - consensus_counts["loss"]) / case_count
        if case_count
        else 0.0
    )
    full_coverage = (
        len(domains) == RELEASE_DOMAIN_COUNT and case_count >= RELEASE_CASE_COUNT
    )
    all_domain_gates = bool(domain_results) and all(
        item["gate_passed"] for item in domain_results.values()
    )
    all_release_gates = all(item["aggregate_gate_passed"] for item in replicate_facts)
    all_real_model_calls = all(
        item["actual_model_calls"] > 0 for item in replicate_facts
    )
    all_provider_receipts_verified = all(
        item["provenance_verified"] for item in replicate_facts
    )
    release_gate = (
        len(runs) >= MINIMUM_REPLICATES
        and full_coverage
        and all_release_gates
        and all_real_model_calls
        and all_provider_receipts_verified
        and all_domain_gates
        and net >= 0.10
        and critical == 0
        and fatal == 0
        and hard == 0
    )
    evidence = _replicate_evidence(
        matched_observations=case_count * len(runs),
        release_gate_passed=release_gate,
    )
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "report_type": "benchmark_replicate_aggregate",
        "suite_id": normalized[0].get("suite_id"),
        "benchmark_definition_sha256": normalized[0].get("benchmark_definition_sha256"),
        "compatibility_configuration": normalized[0],
        "compatibility_sha256": reference_hash,
        "replicate_count": len(runs),
        "replicates": replicate_facts,
        "coverage": {
            "domains": domains,
            "domain_count": len(domains),
            "case_count": case_count,
            "observation_count": case_count * len(runs),
        },
        "cases": cases,
        "domain_results": domain_results,
        "aggregate": {
            "consensus_wins": consensus_counts["win"],
            "consensus_ties": consensus_counts["tie"],
            "consensus_losses": consensus_counts["loss"],
            "actual_model_calls": sum(
                item["actual_model_calls"] for item in replicate_facts
            ),
            "verified_model_calls": sum(
                item["verified_model_calls"] for item in replicate_facts
            ),
            "net_improvement": net,
            "critical_regressions": critical,
            "fatal_flaws": fatal,
            "optimized_hard_failures": hard,
            "stable_cases": stable,
            "unstable_cases": case_count - stable,
            "exact_agreement_rate": stable / case_count if case_count else 0.0,
            "full_release_coverage": full_coverage,
            "all_individual_release_gates_passed": all_release_gates,
            "all_provider_receipts_verified": all_provider_receipts_verified,
            "all_domain_gates_passed": all_domain_gates,
            "diagnostic_gate_passed": all_domain_gates,
            "release_gate_passed": release_gate,
        },
        "evidence": _evidence_payload(evidence),
    }
    report["report_sha256"] = hash_payload(report, "report_sha256")
    failures = validate_replicate_report(report)
    if failures:
        raise AssertionError(f"Generated replicate report is invalid: {failures}")
    return report


def _validate_replicate_report(report: Any) -> list[str]:
    """Recompute every material derived fact in a replicate report."""
    if not isinstance(report, dict):
        return ["replicate report root must be an object"]
    failures: list[str] = []
    expected_root_fields = {
        "schema_version",
        "report_type",
        "suite_id",
        "benchmark_definition_sha256",
        "compatibility_configuration",
        "compatibility_sha256",
        "replicate_count",
        "replicates",
        "coverage",
        "cases",
        "domain_results",
        "aggregate",
        "evidence",
        "report_sha256",
    }
    if set(report) != expected_root_fields:
        failures.append("replicate report fields do not match the schema")
    if report.get("schema_version") != SCHEMA_VERSION:
        failures.append("unsupported replicate report schema")
    if report.get("report_type") != "benchmark_replicate_aggregate":
        failures.append("invalid replicate report type")
    suite_id = report.get("suite_id")
    if not isinstance(suite_id, str) or not suite_id:
        failures.append("replicate suite_id must be a non-empty string")
    benchmark_definition_sha256 = report.get("benchmark_definition_sha256")
    if (
        not isinstance(benchmark_definition_sha256, str)
        or RECEIPT_DIGEST_RE.fullmatch(benchmark_definition_sha256) is None
    ):
        failures.append("replicate benchmark definition hash is invalid")
    if report.get("report_sha256") != hash_payload(report, "report_sha256"):
        failures.append("replicate report hash mismatch")
    if not _is_json_integer(report.get("replicate_count"), minimum=3):
        failures.append("replicate count must be an integer of at least three")
    configuration = report.get("compatibility_configuration")
    compatibility_sha256 = report.get("compatibility_sha256")
    if (
        not isinstance(configuration, dict)
        or not isinstance(compatibility_sha256, str)
        or RECEIPT_DIGEST_RE.fullmatch(compatibility_sha256) is None
        or compatibility_sha256 != sha256_json(configuration)
    ):
        failures.append("compatibility configuration hash mismatch")
    else:
        if report.get("suite_id") != configuration.get("suite_id"):
            failures.append("replicate suite does not match compatibility configuration")
        if report.get("benchmark_definition_sha256") != configuration.get(
            "benchmark_definition_sha256"
        ):
            failures.append(
                "replicate benchmark definition does not match compatibility "
                "configuration"
            )
    replicates = report.get("replicates")
    cases = report.get("cases")
    domains_data = report.get("domain_results")
    if (
        not isinstance(replicates, list)
        or not isinstance(cases, list)
        or not isinstance(domains_data, dict)
    ):
        return [
            *failures,
            "replicates, cases, and domain_results must have valid container types",
        ]
    if not replicates:
        return [*failures, "replicate report must contain source replicates"]
    replicate_ids: list[str] = []
    replicate_count_fields = {
        "actual_model_calls",
        "verified_model_calls",
        "domain_count",
        "case_count",
        "wins",
        "ties",
        "losses",
        "critical_regressions",
        "fatal_flaws",
        "optimized_hard_failures",
    }
    for item in replicates:
        expected_replicate_fields = {
            "replicate_id",
            "run_manifest_sha256",
            "summary_sha256",
            "optimization_artifact_sha256",
            "evaluation_sha256",
            "payload_fingerprint_sha256",
            "execution_identity_sha256",
            "receipt_bundle_sha256",
            "run_fingerprint_sha256",
            "actual_model_calls",
            "verified_model_calls",
            "provenance_verified",
            "domain_count",
            "case_count",
            "wins",
            "ties",
            "losses",
            "critical_regressions",
            "fatal_flaws",
            "optimized_hard_failures",
            "aggregate_gate_passed",
        }
        if not isinstance(item, dict) or set(item) != expected_replicate_fields:
            failures.append("replicate source fields do not match the schema")
            continue
        _validate_json_count_fields(
            item,
            replicate_count_fields,
            label="replicate source",
            failures=failures,
        )
        if not _is_json_integer(item.get("actual_model_calls"), minimum=1):
            failures.append("replicate source actual_model_calls must be positive")
        for field in ("provenance_verified", "aggregate_gate_passed"):
            if not isinstance(item.get(field), bool):
                failures.append(f"replicate source {field} must be a boolean")
        try:
            replicate_ids.append(validate_replicate_id(item.get("replicate_id")))
        except (AttributeError, ValueError) as exc:
            failures.append(f"invalid report replicate: {exc}")
    if len(set(replicate_ids)) != len(replicate_ids):
        failures.append("report replicate IDs are not unique")
    if len(replicates) < MINIMUM_REPLICATES or report.get("replicate_count") != len(
        replicates
    ):
        failures.append("replicate count mismatch or below minimum")

    computed_domains: dict[str, list[dict[str, Any]]] = {}
    seen_cases: set[tuple[str, str]] = set()
    for case in cases:
        if not isinstance(case, dict):
            failures.append("case row must be an object")
            continue
        if set(case) != {
            "domain",
            "case_id",
            "case_sha256",
            "outcome_counts",
            "observations",
            "consensus",
            "consensus_basis",
            "agreement_ratio",
            "stable",
        }:
            failures.append("case row fields do not match the schema")
            continue
        domain, case_id = case.get("domain"), case.get("case_id")
        case_sha256 = case.get("case_sha256")
        key = (domain, case_id)
        if (
            not isinstance(domain, str)
            or not domain
            or not isinstance(case_id, str)
            or not case_id
            or key in seen_cases
        ):
            failures.append(f"invalid or duplicate report case: {key!r}")
            continue
        if not isinstance(case_sha256, str) or re.fullmatch(
            r"[0-9a-f]{64}",
            case_sha256,
        ) is None:
            failures.append(f"{domain}/{case_id}: invalid case hash")
            continue
        seen_cases.add(key)
        observations = case.get("observations")
        if not isinstance(observations, list):
            failures.append(f"{domain}/{case_id}: observations must be a list")
            continue
        outcome_counts = case.get("outcome_counts")
        if not isinstance(outcome_counts, dict) or set(outcome_counts) != set(OUTCOMES):
            failures.append(f"{domain}/{case_id}: outcome counts are invalid")
        else:
            _validate_json_count_fields(
                outcome_counts,
                set(OUTCOMES),
                label=f"{domain}/{case_id}: outcome count",
                failures=failures,
            )
        agreement_ratio = _normalized_finite_float(case.get("agreement_ratio"))
        if agreement_ratio is None or not 0.0 <= agreement_ratio <= 1.0:
            failures.append(f"{domain}/{case_id}: agreement ratio is invalid")
        if not isinstance(case.get("stable"), bool):
            failures.append(f"{domain}/{case_id}: stable must be a boolean")
        observed_ids = [
            item.get("replicate_id") for item in observations if isinstance(item, dict)
        ]
        if sorted(observed_ids) != sorted(replicate_ids):
            failures.append(f"{domain}/{case_id}: replicate observation set mismatch")
            continue
        counts = {outcome: 0 for outcome in OUTCOMES}
        valid_observations = True
        for observation in observations:
            if not isinstance(observation, dict) or set(observation) != {
                "replicate_id",
                "outcome",
                "critical_regression",
                "fatal_flaw",
                "optimized_hard_failure",
            }:
                failures.append(f"{domain}/{case_id}: observation fields are invalid")
                valid_observations = False
                break
            outcome = observation.get("outcome")
            if outcome not in OUTCOMES or not all(
                isinstance(observation.get(field), bool)
                for field in (
                    "critical_regression",
                    "fatal_flaw",
                    "optimized_hard_failure",
                )
            ):
                failures.append(f"{domain}/{case_id}: invalid observation")
                valid_observations = False
                break
            counts[outcome] += 1
        if not valid_observations:
            continue
        consensus, basis = _consensus(counts)
        stable = sum(value > 0 for value in counts.values()) == 1
        expected = {
            "outcome_counts": counts,
            "consensus": consensus,
            "consensus_basis": basis,
            "agreement_ratio": max(counts.values()) / len(replicates),
            "stable": stable,
        }
        for field, value in expected.items():
            if case.get(field) != value:
                failures.append(f"{domain}/{case_id}: derived mismatch: {field}")
        computed_domains.setdefault(domain, []).append(case)

    domain_result_fields = {
        "case_count",
        "consensus_wins",
        "consensus_ties",
        "consensus_losses",
        "critical_regressions",
        "fatal_flaws",
        "optimized_hard_failures",
        "all_individual_gates_passed",
        "consensus_gate_passed",
        "gate_passed",
        "replicate_results",
    }
    domain_count_fields = {
        "case_count",
        "consensus_wins",
        "consensus_ties",
        "consensus_losses",
        "critical_regressions",
        "fatal_flaws",
        "optimized_hard_failures",
    }
    domain_run_fields = {
        "replicate_id",
        "case_count",
        "wins",
        "ties",
        "losses",
        "critical_regressions",
        "fatal_flaws",
        "optimized_hard_failures",
        "gate_passed",
    }
    domain_run_count_fields = domain_run_fields - {"replicate_id", "gate_passed"}
    for domain, domain_result in domains_data.items():
        if not isinstance(domain_result, dict) or set(domain_result) != domain_result_fields:
            failures.append(f"domain result fields are invalid: {domain}")
            continue
        _validate_json_count_fields(
            domain_result,
            domain_count_fields,
            label=f"{domain}: domain result",
            failures=failures,
        )
        for field in (
            "all_individual_gates_passed",
            "consensus_gate_passed",
            "gate_passed",
        ):
            if not isinstance(domain_result.get(field), bool):
                failures.append(f"{domain}: {field} must be a boolean")
        replicate_results = domain_result.get("replicate_results")
        if not isinstance(replicate_results, list):
            failures.append(f"{domain}: replicate_results must be an array")
            continue
        for run_result in replicate_results:
            if not isinstance(run_result, dict) or set(run_result) != domain_run_fields:
                failures.append(f"{domain}: replicate result fields are invalid")
                continue
            _validate_json_count_fields(
                run_result,
                domain_run_count_fields,
                label=f"{domain}: replicate result",
                failures=failures,
            )
            if not isinstance(run_result.get("gate_passed"), bool):
                failures.append(f"{domain}: replicate gate_passed must be a boolean")

    if set(domains_data) != set(computed_domains):
        failures.append("domain_results keys do not match case domains")
    expected_replicate_domains: dict[str, list[dict[str, Any]]] = {
        replicate_id: [] for replicate_id in replicate_ids
    }
    for domain, domain_cases in computed_domains.items():
        expected_replicate_results = []
        for replicate_id in replicate_ids:
            observations = [
                next(
                    item
                    for item in case["observations"]
                    if item["replicate_id"] == replicate_id
                )
                for case in domain_cases
            ]
            result = {
                "replicate_id": replicate_id,
                **_domain_gate_from_observations(observations),
            }
            expected_replicate_results.append(result)
            expected_replicate_domains[replicate_id].append(result)
        consensus_observations = [
            {
                "outcome": case["consensus"],
                "critical_regression": any(
                    item["critical_regression"] for item in case["observations"]
                ),
                "fatal_flaw": any(item["fatal_flaw"] for item in case["observations"]),
                "optimized_hard_failure": any(
                    item["optimized_hard_failure"] for item in case["observations"]
                ),
            }
            for case in domain_cases
        ]
        consensus = _domain_gate_from_observations(consensus_observations)
        expected_domain = {
            "case_count": len(domain_cases),
            "consensus_wins": consensus["wins"],
            "consensus_ties": consensus["ties"],
            "consensus_losses": consensus["losses"],
            "critical_regressions": sum(
                item["critical_regression"]
                for case in domain_cases
                for item in case["observations"]
            ),
            "fatal_flaws": sum(
                item["fatal_flaw"]
                for case in domain_cases
                for item in case["observations"]
            ),
            "optimized_hard_failures": sum(
                item["optimized_hard_failure"]
                for case in domain_cases
                for item in case["observations"]
            ),
            "all_individual_gates_passed": all(
                item["gate_passed"] for item in expected_replicate_results
            ),
            "consensus_gate_passed": consensus["gate_passed"],
            "gate_passed": all(
                item["gate_passed"] for item in expected_replicate_results
            )
            and consensus["gate_passed"],
            "replicate_results": expected_replicate_results,
        }
        if domains_data.get(domain) != expected_domain:
            failures.append(f"domain derived mismatch: {domain}")

    expected_replicates = []
    run_fingerprints: list[str] = []
    execution_identity_hashes: list[str] = []
    receipt_bundle_hashes: list[str] = []
    manifest_hashes: list[str] = []
    summary_hashes: list[str] = []
    replicate_lookup = {
        item.get("replicate_id"): item for item in replicates if isinstance(item, dict)
    }
    for replicate_id in replicate_ids:
        source = replicate_lookup[replicate_id]
        manifest_hash = source.get("run_manifest_sha256")
        summary_hash = source.get("summary_sha256")
        if any(
            not isinstance(digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
            for digest in (manifest_hash, summary_hash)
        ):
            failures.append(f"{replicate_id}: invalid manifest or summary hash")
        else:
            manifest_hashes.append(manifest_hash)
            summary_hashes.append(summary_hash)
        artifact_hashes = source.get("optimization_artifact_sha256")
        evaluation_hashes = source.get("evaluation_sha256")
        valid_hash_maps = all(
            isinstance(value, dict)
            and set(value) == set(computed_domains)
            and all(
                isinstance(digest, str)
                and re.fullmatch(r"[0-9a-f]{64}", digest) is not None
                for digest in value.values()
            )
            for value in (artifact_hashes, evaluation_hashes)
        )
        if not valid_hash_maps:
            failures.append(f"{replicate_id}: invalid artifact hash maps")
        else:
            payload_fingerprint = source.get("payload_fingerprint_sha256")
            execution_identity_hash = source.get("execution_identity_sha256")
            receipt_bundle_hash = source.get("receipt_bundle_sha256")
            if not isinstance(payload_fingerprint, str) or re.fullmatch(
                r"[0-9a-f]{64}", payload_fingerprint
            ) is None:
                failures.append(f"{replicate_id}: payload fingerprint is invalid")
            if not isinstance(execution_identity_hash, str) or re.fullmatch(
                r"[0-9a-f]{64}", execution_identity_hash
            ) is None:
                failures.append(f"{replicate_id}: execution identity hash is invalid")
            else:
                execution_identity_hashes.append(execution_identity_hash)
            if not isinstance(receipt_bundle_hash, str) or re.fullmatch(
                r"[0-9a-f]{64}", receipt_bundle_hash
            ) is None:
                failures.append(f"{replicate_id}: receipt bundle hash is invalid")
            else:
                receipt_bundle_hashes.append(receipt_bundle_hash)
            expected_fingerprint = sha256_json(
                {
                    "run_manifest_sha256": manifest_hash,
                    "summary_sha256": summary_hash,
                    "payload_fingerprint_sha256": payload_fingerprint,
                    "execution_identity_sha256": execution_identity_hash,
                    "receipt_bundle_sha256": receipt_bundle_hash,
                }
            )
            if source.get("run_fingerprint_sha256") != expected_fingerprint:
                failures.append(f"{replicate_id}: run fingerprint mismatch")
            run_fingerprints.append(expected_fingerprint)
        results = expected_replicate_domains[replicate_id]
        case_count = sum(item["case_count"] for item in results)
        actual_model_calls = source.get("actual_model_calls")
        if not _is_json_integer(actual_model_calls, minimum=1):
            failures.append(f"{replicate_id}: no real model calls are recorded")
            actual_model_calls = 0
        verified_model_calls = source.get("verified_model_calls")
        if (
            not _is_json_integer(verified_model_calls, minimum=0)
            or not 0 <= verified_model_calls <= actual_model_calls
        ):
            failures.append(f"{replicate_id}: verified model-call count is invalid")
            verified_model_calls = 0
        provenance_verified = (
            actual_model_calls > 0 and verified_model_calls == actual_model_calls
        )
        if source.get("provenance_verified") is not provenance_verified:
            failures.append(f"{replicate_id}: provider provenance status mismatch")
        facts = {
            "actual_model_calls": actual_model_calls,
            "verified_model_calls": verified_model_calls,
            "provenance_verified": provenance_verified,
            "domain_count": len(results),
            "case_count": case_count,
            "wins": sum(item["wins"] for item in results),
            "ties": sum(item["ties"] for item in results),
            "losses": sum(item["losses"] for item in results),
            "critical_regressions": sum(
                item["critical_regressions"] for item in results
            ),
            "fatal_flaws": sum(item["fatal_flaws"] for item in results),
            "optimized_hard_failures": sum(
                item["optimized_hard_failures"] for item in results
            ),
        }
        net = (facts["wins"] - facts["losses"]) / case_count if case_count else 0.0
        facts["aggregate_gate_passed"] = (
            len(results) == RELEASE_DOMAIN_COUNT
            and all(item["gate_passed"] for item in results)
            and net >= 0.10
            and facts["critical_regressions"] == 0
            and facts["fatal_flaws"] == 0
            and facts["optimized_hard_failures"] == 0
        )
        for field, value in facts.items():
            if source.get(field) != value:
                failures.append(f"{replicate_id}: derived mismatch: {field}")
        expected_replicates.append(facts)
    if len(set(execution_identity_hashes)) != len(execution_identity_hashes):
        failures.append("replicate execution identity hashes are not unique")
    authoritative_receipt_bundles = [
        item.get("receipt_bundle_sha256")
        for item in replicates
        if isinstance(item, dict) and item.get("provenance_verified") is True
    ]
    if len(authoritative_receipt_bundles) != len(
        set(authoritative_receipt_bundles)
    ):
        failures.append("replicate verified receipt bundle hashes are not unique")
    if len(manifest_hashes) != len(set(manifest_hashes)):
        failures.append("replicate manifest hashes are not unique")
    if len(summary_hashes) != len(set(summary_hashes)):
        failures.append("replicate summary hashes are not unique")
    if len(run_fingerprints) != len(set(run_fingerprints)):
        failures.append("replicate full run fingerprints are not unique")

    domain_names = sorted(computed_domains)
    coverage = report.get("coverage")
    if not isinstance(coverage, dict) or set(coverage) != {
        "domains",
        "domain_count",
        "case_count",
        "observation_count",
    }:
        failures.append("coverage fields do not match the schema")
    else:
        _validate_json_count_fields(
            coverage,
            {"domain_count", "case_count", "observation_count"},
            label="coverage",
            failures=failures,
        )
    expected_coverage = {
        "domains": domain_names,
        "domain_count": len(domain_names),
        "case_count": len(cases),
        "observation_count": len(cases) * len(replicates),
    }
    if coverage != expected_coverage:
        failures.append("coverage derived mismatch")
    consensus_counts = {
        outcome: sum(case.get("consensus") == outcome for case in cases)
        for outcome in OUTCOMES
    }
    critical = sum(
        item.get("critical_regression") is True
        for case in cases
        for item in case.get("observations", [])
    )
    fatal = sum(
        item.get("fatal_flaw") is True
        for case in cases
        for item in case.get("observations", [])
    )
    hard = sum(
        item.get("optimized_hard_failure") is True
        for case in cases
        for item in case.get("observations", [])
    )
    stable = sum(case.get("stable") is True for case in cases)
    case_count = len(cases)
    net = (
        (consensus_counts["win"] - consensus_counts["loss"]) / case_count
        if case_count
        else 0.0
    )
    full = (
        len(domain_names) == RELEASE_DOMAIN_COUNT and case_count >= RELEASE_CASE_COUNT
    )
    all_domains = bool(domains_data) and all(
        item.get("gate_passed") is True for item in domains_data.values()
    )
    all_release = bool(expected_replicates) and all(
        item["aggregate_gate_passed"] for item in expected_replicates
    )
    all_real_model_calls = bool(expected_replicates) and all(
        item["actual_model_calls"] > 0 for item in expected_replicates
    )
    all_provider_receipts_verified = bool(expected_replicates) and all(
        item["provenance_verified"] for item in expected_replicates
    )
    release = (
        len(replicates) >= MINIMUM_REPLICATES
        and full
        and all_release
        and all_real_model_calls
        and all_provider_receipts_verified
        and all_domains
        and net >= 0.10
        and critical == 0
        and fatal == 0
        and hard == 0
    )
    expected_aggregate = {
        "consensus_wins": consensus_counts["win"],
        "consensus_ties": consensus_counts["tie"],
        "consensus_losses": consensus_counts["loss"],
        "actual_model_calls": sum(
            item["actual_model_calls"] for item in expected_replicates
        ),
        "verified_model_calls": sum(
            item["verified_model_calls"] for item in expected_replicates
        ),
        "net_improvement": net,
        "critical_regressions": critical,
        "fatal_flaws": fatal,
        "optimized_hard_failures": hard,
        "stable_cases": stable,
        "unstable_cases": case_count - stable,
        "exact_agreement_rate": stable / case_count if case_count else 0.0,
        "full_release_coverage": full,
        "all_individual_release_gates_passed": all_release,
        "all_provider_receipts_verified": all_provider_receipts_verified,
        "all_domain_gates_passed": all_domains,
        "diagnostic_gate_passed": all_domains,
        "release_gate_passed": release,
    }
    aggregate = report.get("aggregate")
    aggregate_count_fields = {
        "consensus_wins",
        "consensus_ties",
        "consensus_losses",
        "actual_model_calls",
        "verified_model_calls",
        "critical_regressions",
        "fatal_flaws",
        "optimized_hard_failures",
        "stable_cases",
        "unstable_cases",
    }
    aggregate_boolean_fields = {
        "full_release_coverage",
        "all_individual_release_gates_passed",
        "all_provider_receipts_verified",
        "all_domain_gates_passed",
        "diagnostic_gate_passed",
        "release_gate_passed",
    }
    if not isinstance(aggregate, dict) or set(aggregate) != set(expected_aggregate):
        failures.append("aggregate fields do not match the schema")
    else:
        _validate_json_count_fields(
            aggregate,
            aggregate_count_fields,
            label="aggregate",
            failures=failures,
        )
        for field in aggregate_boolean_fields:
            if not isinstance(aggregate.get(field), bool):
                failures.append(f"aggregate {field} must be a boolean")
        net_improvement = _normalized_finite_float(aggregate.get("net_improvement"))
        if net_improvement is None or not -1.0 <= net_improvement <= 1.0:
            failures.append("aggregate net_improvement is invalid")
        exact_agreement_rate = _normalized_finite_float(
            aggregate.get("exact_agreement_rate")
        )
        if exact_agreement_rate is None or not 0.0 <= exact_agreement_rate <= 1.0:
            failures.append("aggregate exact_agreement_rate is invalid")
    if aggregate != expected_aggregate:
        failures.append("aggregate derived mismatch")
    expected_evidence = _evidence_payload(
        _replicate_evidence(
            matched_observations=case_count * len(replicates),
            release_gate_passed=release,
        )
    )
    try:
        evidence_data = report.get("evidence")
        if not isinstance(evidence_data, dict):
            raise ValueError("evidence must be an object")
        Evidence(
            level=evidence_data.get("level", ""),
            status=evidence_data.get("status", ""),
            claim=evidence_data.get("claim", ""),
            limitations=tuple(evidence_data.get("limitations", [])),
        ).validate()
        if evidence_data != expected_evidence:
            failures.append("replicate evidence does not match release gate")
    except (TypeError, ValueError) as exc:
        failures.append(f"invalid replicate evidence: {exc}")
    return failures


def validate_replicate_report(report: Any) -> list[str]:
    """Validate untrusted report input without leaking parser exceptions."""
    try:
        return _validate_replicate_report(report)
    except (
        AttributeError,
        ArithmeticError,
        KeyError,
        RecursionError,
        TypeError,
        UnicodeError,
        ValueError,
        StopIteration,
    ) as exc:
        return [f"malformed replicate report: {exc}"]


def validate_e3_claim(report: Any) -> list[str]:
    """Validate the structure and derived facts of a detached E3 report."""
    failures = validate_replicate_report(report)
    if failures:
        return failures
    assert isinstance(report, dict)
    aggregate = report.get("aggregate")
    evidence = report.get("evidence")
    if not isinstance(aggregate, dict) or aggregate.get(
        "release_gate_passed"
    ) is not True:
        failures.append("replicate release gate did not pass")
    if not isinstance(evidence, dict) or evidence.get("level") != "E3":
        failures.append("replicate report does not carry E3 evidence")
    return failures


def validate_e3_authority(
    report: Any,
    run_directories: Sequence[Path] | None = None,
    *,
    receipt_verifier: ModelCallReceiptVerifier | None = None,
) -> list[str]:
    """Require E3 plus source runs that reproduce the detached report exactly."""
    failures = validate_e3_claim(report)
    if receipt_verifier is None:
        failures.append("trusted provider call receipt verifier is required for E3 authority")
    failures.extend(
        validate_replicate_sources(
            report,
            run_directories,
            receipt_verifier=receipt_verifier,
        )
    )
    return list(dict.fromkeys(failures))


def validate_replicate_sources(
    report: Any,
    run_directories: Sequence[Path] | None,
    *,
    receipt_verifier: ModelCallReceiptVerifier | None = None,
) -> list[str]:
    """Rebuild a detached replicate report from its complete source bundle."""
    failures = validate_replicate_report(report)
    if failures:
        return failures
    if run_directories is None:
        failures.append("source run directories are required for E3 authority")
        return failures
    if (
        not isinstance(run_directories, Sequence)
        or isinstance(run_directories, (str, bytes, bytearray))
        or any(not isinstance(path, Path) for path in run_directories)
    ):
        failures.append(
            "source run directories must be a sequence of Path values"
        )
        return failures
    try:
        rebuilt = aggregate_benchmark_replicates(
            run_directories,
            receipt_verifier=receipt_verifier,
        )
    except (
        OSError,
        ArithmeticError,
        RecursionError,
        TypeError,
        UnicodeError,
        ValueError,
        AssertionError,
    ) as exc:
        failures.append(f"source run authority is invalid: {exc}")
        return failures
    if rebuilt != report:
        failures.append("replicate report does not match its source run directories")
    return failures
