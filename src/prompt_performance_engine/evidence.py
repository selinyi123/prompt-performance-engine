"""Evidence-level rules and claim ceilings."""

from __future__ import annotations

from dataclasses import dataclass


LEVEL_ORDER = {f"E{index}": index for index in range(6)}


@dataclass(frozen=True)
class Evidence:
    level: str
    status: str
    claim: str
    limitations: tuple[str, ...]

    def validate(self) -> None:
        if self.level not in LEVEL_ORDER:
            raise ValueError(f"Unknown evidence level: {self.level!r}.")
        if self.status not in {"candidate", "verified_scoped"}:
            raise ValueError(f"Unknown evidence status: {self.status!r}.")
        if self.status == "verified_scoped" and LEVEL_ORDER[self.level] < 2:
            raise ValueError("verified_scoped requires at least E2 evidence.")
        if not self.claim.strip():
            raise ValueError("Evidence claim must not be empty.")
        if not self.limitations:
            raise ValueError("Evidence limitations must not be empty.")


def infer_evidence(
    *,
    deterministic_checks_passed: bool = False,
    matched_cases: int = 0,
    comparative_improvement_passed: bool = False,
    repeated_or_cross_model: bool = False,
    expert_reviewers: int = 0,
    independently_reproduced: bool = False,
) -> Evidence:
    level = "E0"
    if deterministic_checks_passed:
        level = "E1"
    if (
        level == "E1"
        and matched_cases >= 5
        and comparative_improvement_passed
    ):
        level = "E2"
    if level == "E2" and matched_cases >= 20 and repeated_or_cross_model:
        level = "E3"
    if level == "E3" and expert_reviewers >= 3:
        level = "E4"
    if level == "E4" and independently_reproduced:
        level = "E5"

    verified = LEVEL_ORDER[level] >= 2
    limitations = [
        "Evidence is valid only for the recorded models, settings, cases, and artifact versions.",
        "Evidence does not establish universal superiority or award equivalence.",
    ]
    return Evidence(
        level=level,
        status="verified_scoped" if verified else "candidate",
        claim="verified_improvement" if verified else "optimized_candidate",
        limitations=tuple(limitations),
    )
