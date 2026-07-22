"""Run the public-synthetic frontier authority Phase 0 fail-closed check.

This script deliberately has no provider, judge, reviewer, credential, or
authority adapter.  It verifies that an incomplete public fixture cannot cross
the existing preflight and execution-host boundaries, then writes a canonical,
write-once status record.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from prompt_performance_engine.frontier_host import (
    FrontierExecutionHost,
    FrontierExecutionRejected,
)
from prompt_performance_engine.frontier_io import (
    FrontierArtifactIOError,
    write_authority_artifact,
)
from prompt_performance_engine.frontier_preflight import (
    run_frontier_preflight,
    validate_frontier_preflight_report,
)


PILOT_ID = "frontier-authority-phase0-public-synthetic"
BLOCKED_EXIT_CODE = 3
OUTPUT_ERROR_EXIT_CODE = 4


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _fixture_root() -> Path:
    return _repository_root() / "pilot" / "frontier-authority-phase0" / "public-synthetic"


def _verify_execution_host_rejects(preflight_report: dict[str, Any]) -> str:
    """Exercise the real host gate without constructing any external adapter."""

    try:
        FrontierExecutionHost(
            campaign_sha256=str(preflight_report.get("campaign_sha256") or "0" * 64),
            preflight_report=preflight_report,
            system_id="phase0-public-synthetic",
            execution_plan={},
            case_routes=[],
            components=[],
            ledger=None,  # type: ignore[arg-type]
            runner=None,  # type: ignore[arg-type]
            receipt_verifier=None,  # type: ignore[arg-type]
            preflight_authorizer=None,  # type: ignore[arg-type]
            models=[],
        )
    except FrontierExecutionRejected as error:
        return str(error)
    raise RuntimeError("unsafe frontier host state: failed preflight was accepted")


def build_phase0_status() -> dict[str, Any]:
    """Build one non-authoritative record from the bundled public fixture."""

    fixture_root = _fixture_root()
    preflight_report = run_frontier_preflight(
        fixture_root / "campaign-plan.json",
        root=fixture_root,
        attestation_verifier=None,
        human_gold_verifier=None,
        host_capability_verifier=None,
        storage_verifier=None,
        clock_verifier=None,
    )
    validation_failures = validate_frontier_preflight_report(preflight_report)
    if validation_failures:
        raise RuntimeError("generated frontier preflight report is structurally invalid")
    if preflight_report["passed"] is not False:
        raise RuntimeError("unsafe Phase 0 state: synthetic preflight unexpectedly passed")

    host_rejection = _verify_execution_host_rejects(preflight_report)
    blockers = sorted(
        set(
            preflight_report["failures"]
            + [
                "complete frozen frontier campaign bundle is not supplied",
                "independent clock, owner, and dataset-custodian authorities are not supplied",
                "independent host-capability and storage authorities are not supplied",
                "independent human-gold, judge, and reviewer authorities are not supplied",
                (
                    "independent replay operators, machines, and attestation "
                    "authorities are not supplied"
                ),
                "real provider runners and provider-receipt verifiers are intentionally disabled",
            ]
        )
    )
    return {
        "pilot_id": PILOT_ID,
        "pilot_phase": "phase_0",
        "fixture_class": "public_synthetic_placeholder",
        "artifact_authority": "none",
        "boundary": {
            "machine_claim": "not_evaluable",
            "pilot_state": "blocked",
            "authority_bearing": False,
            "reason": (
                "The public dry-run verifies fail-closed wiring only; it supplies no "
                "sealed benchmark or independent authority evidence."
            ),
        },
        "external_activity": {
            "sealed_benchmark_accessed": False,
            "production_credentials_accessed": False,
            "provider_calls_attempted": False,
            "judge_calls_attempted": False,
            "human_reviews_attempted": False,
            "external_messages_or_writes_attempted": False,
        },
        "preflight_report": preflight_report,
        "downstream": {
            "execution_host": "rejected",
            "execution_host_rejection": host_rejection,
            "frontier_report": "not_constructed",
            "claim_inventory": "not_constructed",
            "offline_replay": "not_started",
        },
        "blockers": blockers,
        "next_required_inputs": [
            "a contract-valid frozen campaign bundle with sealed-source commitments",
            "independent preflight clock, owner, and dataset-custodian verification",
            "independent human-gold, host-capability, and storage verification",
            "approved provider and judge adapters with unique receipt verification",
            "three independent replay operators and machines with attestation verification",
        ],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the non-authoritative public-synthetic frontier Phase 0 check."
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        required=True,
        help="Existing or new directory for the write-once Phase 0 status record.",
    )
    parser.add_argument(
        "--output-name",
        default="phase0-status.json",
        help="Portable relative output path under --output-root.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    output_root = args.output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    status = build_phase0_status()
    try:
        result = write_authority_artifact(
            output_root,
            args.output_name,
            status,
        )
    except FrontierArtifactIOError as error:
        print(f"Phase 0 output error: {error}", file=sys.stderr)
        return OUTPUT_ERROR_EXIT_CODE
    print(
        f"{status['boundary']['machine_claim']}: {status['boundary']['pilot_state']}; "
        f"record={result.relative_path}; sha256={result.content_sha256}"
    )
    return BLOCKED_EXIT_CODE


if __name__ == "__main__":
    raise SystemExit(main())
