"""Derive readiness evidence from authoritative software case checks."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from . import case_checks, software_execution, software_sandbox
from .case_checks import SOFTWARE_CASE_VERIFIERS
from .contracts import ARTIFACT_SCHEMA_VERSION, load_strict_json_object
from .evaluation import validate_evaluation
from .readiness import build_evidence_report
from .software_sandbox import DockerSandbox, SandboxRun


RESTRICTED_SUBPROCESS_CHECKS = {
    check_name
    for check_name, _ in SOFTWARE_CASE_VERIFIERS.values()
    if check_name.endswith("_restricted_execution")
}
FORMAL_CONTRACT_CHECKS = {
    check_name
    for check_name, _ in SOFTWARE_CASE_VERIFIERS.values()
    if check_name.endswith("_machine_contract")
}
REQUIRED_ISOLATION_FACTS = {
    "network_blocked",
    "root_read_only",
    "tmp_writable",
    "non_root",
}
CODE_EXECUTION_PLAN_FIELDS = {
    "schema_version",
    "report_id",
    "evaluation",
    "sandbox_image",
}


def _contained_evaluation_path(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative:
        raise ValueError(
            "Code-execution evaluation must be a non-empty relative path."
        )
    relative_path = Path(relative)
    if relative_path.is_absolute():
        raise ValueError("Code-execution evaluation path must be relative.")
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(
            "Code-execution evaluation path escapes the plan root."
        ) from exc
    return candidate


def load_code_execution_plan(
    plan: Any,
    *,
    root: Path,
) -> dict[str, Any]:
    """Strictly load the source evaluation named by an R05 replay plan."""

    if not isinstance(plan, dict) or set(plan) != CODE_EXECUTION_PLAN_FIELDS:
        raise ValueError(
            "Code-execution plan fields do not match the contract."
        )
    if plan.get("schema_version") != ARTIFACT_SCHEMA_VERSION:
        raise ValueError("Unsupported code-execution plan schema.")
    report_id = plan.get("report_id")
    evaluation_relative = plan.get("evaluation")
    sandbox_image = plan.get("sandbox_image")
    if not isinstance(report_id, str) or not report_id.strip():
        raise ValueError("Code-execution plan report_id must not be empty.")
    if (
        not isinstance(sandbox_image, str)
        or re.search(r"@sha256:[0-9a-f]{64}$", sandbox_image) is None
    ):
        raise ValueError(
            "Code-execution plan sandbox_image must use an immutable sha256 digest."
        )
    resolved_root = root.resolve()
    evaluation_path = _contained_evaluation_path(
        resolved_root,
        evaluation_relative,
    )
    evaluation = load_strict_json_object(
        evaluation_path,
        label="code-execution evaluation",
    )
    return {
        "report_id": report_id,
        "evaluation": evaluation,
        "sandbox_image": sandbox_image,
    }


def _resource_limits_verified(probes: dict[str, SandboxRun]) -> bool:
    timeout_probe = probes.get("timeout")
    memory_probe = probes.get("memory")
    return bool(
        isinstance(timeout_probe, SandboxRun)
        and timeout_probe.timed_out
        and not timeout_probe.passed
        and timeout_probe.policy_verified
        and isinstance(memory_probe, SandboxRun)
        and memory_probe.oom_killed
        and memory_probe.exit_code == 137
        and not memory_probe.passed
        and memory_probe.policy_verified
        and memory_probe.exit_state_verified
    )


def _verify_sandbox_before_candidate_execution(
    sandbox: DockerSandbox | None,
) -> tuple[SandboxRun, dict[str, SandboxRun]]:
    if not isinstance(sandbox, DockerSandbox):
        raise ValueError(
            "A DockerSandbox is required to build code-execution evidence."
        )
    try:
        isolation = sandbox.verify_isolation()
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValueError(
            "Docker sandbox verification failed before candidate execution."
        ) from exc
    isolation_facts = (
        isolation.probe_facts if isinstance(isolation, SandboxRun) else {}
    )
    if (
        not isinstance(isolation, SandboxRun)
        or isolation.passed is not True
        or isolation.policy_verified is not True
        or isolation.image_reference != sandbox.image
        or not REQUIRED_ISOLATION_FACTS.issubset(isolation_facts)
        or any(
            isolation_facts.get(name) is not True
            for name in REQUIRED_ISOLATION_FACTS
        )
    ):
        raise ValueError(
            "Docker sandbox isolation could not be verified before candidate execution."
        )
    try:
        resource_probes = sandbox.verify_resource_limits()
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValueError(
            "Docker sandbox resource-limit verification failed before "
            "candidate execution."
        ) from exc
    if not isinstance(resource_probes, dict) or not _resource_limits_verified(
        resource_probes
    ):
        raise ValueError(
            "Docker sandbox resource limits could not be verified before candidate execution."
        )
    return isolation, resource_probes


def verifier_implementation_sha256() -> str:
    digest = hashlib.sha256()
    for path in sorted(
        (
            Path(case_checks.__file__).resolve(),
            Path(software_execution.__file__).resolve(),
            Path(__file__).resolve(),
            Path(software_sandbox.__file__).resolve(),
        ),
        key=lambda item: item.name,
    ):
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        normalized = path.read_text(encoding="utf-8")
        digest.update(normalized.replace("\r\n", "\n").encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def build_code_execution_evidence(
    evaluation: dict[str, Any],
    *,
    report_id: str,
    sandbox: DockerSandbox | None = None,
) -> dict[str, Any]:
    failures = validate_evaluation(evaluation)
    if failures:
        raise ValueError(f"Evaluation is invalid: {failures}")
    isolation, resource_probes = _verify_sandbox_before_candidate_execution(
        sandbox
    )

    records = {
        record.get("case_id"): record
        for record in evaluation.get("records", [])
        if isinstance(record, dict)
    }
    executed_cases = 0
    passed_cases = 0
    restricted_subprocess_cases = 0
    formal_contract_cases = 0
    sandboxed_cases = 0
    case_results: dict[str, dict[str, Any]] = {}
    for case_id, (expected_check, verifier) in SOFTWARE_CASE_VERIFIERS.items():
        record = records.get(case_id)
        checks = (
            record.get("hard_checks", {}).get("optimized", {}).get("checks", [])
            if record is not None
            else []
        )
        observed = next(
            (
                check
                for check in checks
                if isinstance(check, dict)
                and check.get("check") == expected_check
                and check.get("authoritative") is True
                and check.get("source") == "case_plugin"
            ),
            None,
        )
        output = record.get("optimized_output") if record is not None else None
        reverified = False
        reverification_detail = "No optimized output was available."
        if isinstance(output, str):
            reverified, reverification_detail = verifier(
                output,
                sandbox=sandbox,
            )
        executed = observed is not None and isinstance(output, str)
        passed = (
            executed
            and observed.get("passed") is True
            and reverified
        )
        executed_cases += int(executed)
        passed_cases += int(passed)
        if executed and expected_check in RESTRICTED_SUBPROCESS_CHECKS:
            restricted_subprocess_cases += 1
            if passed:
                sandboxed_cases += 1
        if executed and expected_check in FORMAL_CONTRACT_CHECKS:
            formal_contract_cases += 1
        case_results[case_id] = {
            "check": expected_check,
            "executed": executed,
            "passed": passed,
            "reverified": reverified,
            "reverification_detail": reverification_detail,
            "backend": (
                "docker"
                if expected_check in RESTRICTED_SUBPROCESS_CHECKS
                else "formal_contract"
            ),
        }

    executable_cases = len(RESTRICTED_SUBPROCESS_CHECKS)
    timeout_probe = resource_probes.get("timeout")
    memory_probe = resource_probes.get("memory")
    resource_limits_verified = _resource_limits_verified(resource_probes)
    sandboxed = bool(
        isolation is not None
        and isolation.passed
        and isolation.policy_verified
        and resource_limits_verified
        and sandboxed_cases == executable_cases
    )
    sandbox_facts = (
        {
            "backend": "docker",
            "image_reference": isolation.image_reference,
            "image_id": isolation.image_id,
            "python_version": isolation.python_version,
            "policy": isolation.policy,
            "policy_verified": isolation.policy_verified,
            "isolation_probe_passed": isolation.passed,
            "isolation_probe_detail": isolation.detail,
            "isolation_facts": isolation.probe_facts,
            "resource_limits_verified": resource_limits_verified,
            "timeout_probe": {
                "timed_out": timeout_probe.timed_out,
                "policy_verified": timeout_probe.policy_verified,
            },
            "memory_probe": {
                "oom_killed": memory_probe.oom_killed,
                "exit_code": memory_probe.exit_code,
                "policy_verified": memory_probe.policy_verified,
                "exit_state_verified": memory_probe.exit_state_verified,
            },
        }
    )
    return build_evidence_report(
        kind="code_execution",
        report_id=report_id,
        facts={
            "eligible_cases": len(SOFTWARE_CASE_VERIFIERS),
            "executed_cases": executed_cases,
            "passed_cases": passed_cases,
            "restricted_subprocess_cases": restricted_subprocess_cases,
            "executable_cases": executable_cases,
            "sandboxed_cases": sandboxed_cases,
            "formal_contract_cases": formal_contract_cases,
            "sandboxed": sandboxed,
            "sandbox": sandbox_facts,
            "evaluation_gate_passed": evaluation.get("gate_passed") is True,
            "optimized_hard_failures": evaluation.get(
                "optimized_hard_failures",
            ),
            "case_results": case_results,
        },
        provenance={
            "producer": "prompt_performance_engine.software_evidence",
            "suite_id": evaluation.get("suite_id"),
            "evaluation_sha256": evaluation.get("evaluation_sha256"),
            "verifier_implementation_sha256": (
                verifier_implementation_sha256()
            ),
        },
        limitations=[
            "This report covers only the five recorded software-engineering "
            "benchmark cases and the optimized outputs in the source evaluation.",
            "The current verifier implementation re-executes each optimized "
            "output; historical model and judge calls are not repeated.",
            "Docker isolation is local first-party evidence and does not replace "
            "independent-machine reproduction.",
        ],
    )


def validate_code_execution_authority(
    report: Any,
    plan: Any,
    *,
    root: Path,
    sandbox: DockerSandbox | None,
) -> list[str]:
    """Re-execute the plan's source evaluation and require an exact report."""

    try:
        sources = load_code_execution_plan(plan, root=root)
    except Exception as exc:
        return [f"code-execution source plan is invalid: {exc}"]
    if not isinstance(sandbox, DockerSandbox):
        return [
            "a DockerSandbox is required for code-execution source authority"
        ]
    if getattr(sandbox, "image", None) != sources["sandbox_image"]:
        return [
            "code-execution sandbox image does not match the source plan"
        ]
    try:
        rebuilt = build_code_execution_evidence(
            sources["evaluation"],
            report_id=sources["report_id"],
            sandbox=sandbox,
        )
    except Exception as exc:
        return [f"code-execution source bundle is invalid: {exc}"]
    try:
        matches = rebuilt == report
    except (RecursionError, TypeError, ValueError):
        matches = False
    if not matches:
        return ["code-execution report does not match its source plan"]
    return []
