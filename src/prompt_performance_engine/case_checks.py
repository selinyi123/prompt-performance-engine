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
from .software_sandbox import DockerSandbox


CaseCheckPlugin = Callable[..., list[dict[str, Any]]]
Verifier = Callable[..., tuple[bool, str]]


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
    *,
    requires_sandbox: bool,
) -> CaseCheckPlugin:
    def run(
        output: str,
        *,
        sandbox: DockerSandbox | None = None,
    ) -> list[dict[str, Any]]:
        if requires_sandbox and not isinstance(sandbox, DockerSandbox):
            return [
                _check(
                    check_name,
                    False,
                    "A verified DockerSandbox is required for software case checks.",
                )
            ]
        passed, detail = verifier(output, sandbox=sandbox)
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
DOCKER_REQUIRED_CASE_IDS = frozenset(
    case_id
    for case_id, (check_name, _) in SOFTWARE_CASE_VERIFIERS.items()
    if check_name.endswith("_restricted_execution")
)


CASE_CHECKS: dict[str, CaseCheckPlugin] = {
    case_id: _software_verification(
        check_name,
        verifier,
        requires_sandbox=case_id in DOCKER_REQUIRED_CASE_IDS,
    )
    for case_id, (check_name, verifier) in SOFTWARE_CASE_VERIFIERS.items()
}


def run_case_checks(
    case_id: str,
    output: str,
    *,
    sandbox: DockerSandbox | None = None,
) -> list[dict[str, Any]]:
    plugin = CASE_CHECKS.get(case_id)
    return plugin(output, sandbox=sandbox) if plugin is not None else []
