"""Case-specific deterministic checks for benchmark outputs."""

from __future__ import annotations

from typing import Any, Callable

from .software_execution import (
    verify_cli,
    verify_concurrency,
    verify_endpoint,
    verify_migration,
    verify_pagination,
)


CaseCheckPlugin = Callable[[str], list[dict[str, Any]]]
Verifier = Callable[[str], tuple[bool, str]]


def _check(check: str, passed: bool, detail: str) -> dict[str, Any]:
    return {
        "check": check,
        "passed": passed,
        "detail": detail,
        "authoritative": True,
        "source": "case_plugin",
    }


def _software_verification(
    check_name: str,
    verifier: Verifier,
) -> CaseCheckPlugin:
    def run(output: str) -> list[dict[str, Any]]:
        passed, detail = verifier(output)
        return [_check(check_name, passed, detail)]

    return run


SOFTWARE_CASE_VERIFIERS: dict[str, tuple[str, Verifier]] = {
    "se-normal-pagination": (
        "pagination_restricted_execution",
        verify_pagination,
    ),
    "se-difficult-concurrency": (
        "concurrency_restricted_execution",
        verify_concurrency,
    ),
    "se-adversarial-contract": (
        "endpoint_contract_restricted_execution",
        verify_endpoint,
    ),
    "se-normal-cli": (
        "cli_contract_restricted_execution",
        verify_cli,
    ),
    "se-difficult-migration": (
        "migration_machine_contract",
        verify_migration,
    ),
}


CASE_CHECKS: dict[str, CaseCheckPlugin] = {
    case_id: _software_verification(check_name, verifier)
    for case_id, (check_name, verifier) in SOFTWARE_CASE_VERIFIERS.items()
}


def run_case_checks(case_id: str, output: str) -> list[dict[str, Any]]:
    plugin = CASE_CHECKS.get(case_id)
    return plugin(output) if plugin is not None else []
