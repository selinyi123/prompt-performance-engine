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
        if not isinstance(self.level, str) or self.level not in LEVEL_ORDER:
            raise ValueError(f"Unknown evidence level: {self.level!r}.")
        if not isinstance(self.status, str) or self.status not in {
            "candidate",
            "verified_scoped",
        }:
            raise ValueError(f"Unknown evidence status: {self.status!r}.")
        if self.status == "verified_scoped" and LEVEL_ORDER[self.level] < 2:
            raise ValueError("verified_scoped requires at least E2 evidence.")
        if not isinstance(self.claim, str) or not self.claim.strip():
            raise ValueError("Evidence claim must not be empty.")
        if not isinstance(self.limitations, tuple) or not self.limitations:
            raise ValueError("Evidence limitations must be a non-empty tuple.")
        if any(
            not isinstance(limitation, str) or not limitation.strip()
            for limitation in self.limitations
        ):
            raise ValueError("Evidence limitations must contain non-empty strings.")


def infer_evidence(
    *,
    deterministic_checks_passed: bool = False,
    matched_cases: int = 0,
    comparative_improvement_passed: bool = False,
    repeated_or_cross_model: bool = False,
    expert_reviewers: int = 0,
    independently_reproduced: bool = False,
) -> Evidence:
    """Infer evidence available from an optimization or one matched evaluation.

    The higher-order inputs are retained for source compatibility, but they are
    intentionally non-authoritative.  E3 and above can only be serialized by
    the validators that own the corresponding aggregate artifacts:

    * E3: a valid, release-gated benchmark replicate report;
    * E4: a valid human-review report bound to that replicate report; and
    * E5: a future independent-reproduction aggregate bound to E4.

    A caller-supplied boolean or reviewer count is therefore never sufficient
    to promote evidence beyond a single matched evaluation (E2).
    """
    del repeated_or_cross_model, expert_reviewers, independently_reproduced
    level = "E0"
    if deterministic_checks_passed:
        level = "E1"
    if (
        level == "E1"
        and matched_cases >= 5
        and comparative_improvement_passed
    ):
        level = "E2"
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
