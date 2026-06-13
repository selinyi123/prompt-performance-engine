"""Derive readiness evidence from authoritative software case checks."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from . import case_checks, software_execution
from .case_checks import SOFTWARE_CASE_VERIFIERS
from .evaluation import validate_evaluation
from .readiness import build_evidence_report


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


def verifier_implementation_sha256() -> str:
    digest = hashlib.sha256()
    for path in sorted(
        (
            Path(case_checks.__file__).resolve(),
            Path(software_execution.__file__).resolve(),
            Path(__file__).resolve(),
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
) -> dict[str, Any]:
    failures = validate_evaluation(evaluation)
    if failures:
        raise ValueError(f"Evaluation is invalid: {failures}")

    records = {
        record.get("case_id"): record
        for record in evaluation.get("records", [])
        if isinstance(record, dict)
    }
    executed_cases = 0
    passed_cases = 0
    restricted_subprocess_cases = 0
    formal_contract_cases = 0
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
            reverified, reverification_detail = verifier(output)
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
        if executed and expected_check in FORMAL_CONTRACT_CHECKS:
            formal_contract_cases += 1
        case_results[case_id] = {
            "check": expected_check,
            "executed": executed,
            "passed": passed,
            "reverified": reverified,
            "reverification_detail": reverification_detail,
        }

    return build_evidence_report(
        kind="code_execution",
        report_id=report_id,
        facts={
            "eligible_cases": len(SOFTWARE_CASE_VERIFIERS),
            "executed_cases": executed_cases,
            "passed_cases": passed_cases,
            "restricted_subprocess_cases": restricted_subprocess_cases,
            "formal_contract_cases": formal_contract_cases,
            "sandboxed": False,
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
            "Restricted AST validation and isolated Python subprocesses are "
            "defense in depth, not an OS or container sandbox.",
            "The host kernel does not enforce network, filesystem, memory, or "
            "CPU isolation for these case-specific checks.",
            "This report covers only the five recorded software-engineering "
            "benchmark cases and the optimized outputs in the source evaluation.",
            "The current verifier implementation re-executes each optimized "
            "output; historical model and judge calls are not repeated.",
        ],
    )
