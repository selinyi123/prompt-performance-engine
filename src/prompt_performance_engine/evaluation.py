"""Matched original-versus-optimized Prompt evaluation runtime."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol, Sequence

from .evidence import Evidence, infer_evidence
from .case_checks import run_case_checks
from .domain_checks import run_domain_checks
from .hashing import hash_payload, sha256_json


@dataclass(frozen=True)
class ExecutionConfig:
    model: str
    temperature: float | None = 0.0
    max_tokens: int | None = 2048
    seed: int | None = 0

    def __post_init__(self) -> None:
        if not self.model.strip():
            raise ValueError("Execution model must not be empty.")
        if self.max_tokens is not None and self.max_tokens < 1:
            raise ValueError("max_tokens must be positive when specified.")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExecutionOutput:
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EvaluationCase:
    case_id: str
    input_text: str
    rubric: tuple[str, ...]
    domain: str = "generic"
    difficulty: str = "normal"
    tags: tuple[str, ...] = field(default_factory=tuple)
    required_substrings: tuple[str, ...] = field(default_factory=tuple)
    forbidden_substrings: tuple[str, ...] = field(default_factory=tuple)
    require_json: bool = False
    max_characters: int | None = None

    def validate(self) -> None:
        if not self.case_id.strip():
            raise ValueError("case_id must not be empty.")
        if not self.rubric:
            raise ValueError(f"{self.case_id}: rubric must not be empty.")
        if self.difficulty not in {"normal", "difficult", "adversarial"}:
            raise ValueError(f"{self.case_id}: invalid difficulty.")
        if self.max_characters is not None and self.max_characters < 1:
            raise ValueError(f"{self.case_id}: max_characters must be positive.")


class PromptExecutor(Protocol):
    name: str

    def execute(
        self,
        *,
        prompt: str,
        input_text: str,
        config: ExecutionConfig,
    ) -> str | ExecutionOutput:
        """Execute one Prompt under the supplied controls."""


@dataclass(frozen=True)
class JudgeDecision:
    winner: str
    reason: str
    fatal_flaw_a: bool = False
    fatal_flaw_b: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if self.winner not in {"A", "B", "tie"}:
            raise ValueError(f"Unknown judge winner: {self.winner!r}.")
        if not self.reason.strip():
            raise ValueError("Judge reason must not be empty.")


class BlindJudge(Protocol):
    name: str

    def judge(
        self,
        *,
        case: EvaluationCase,
        output_a: str,
        output_b: str,
    ) -> JudgeDecision:
        """Judge anonymized outputs A and B."""


@dataclass
class RecordedExecutor:
    """Deterministic executor backed by recorded prompt/case outputs."""

    outputs: dict[tuple[str, str], str]
    name: str = "recorded-executor"
    calls: list[dict[str, Any]] = field(default_factory=list)

    def execute(
        self,
        *,
        prompt: str,
        input_text: str,
        config: ExecutionConfig,
    ) -> str:
        prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        input_hash = hashlib.sha256(input_text.encode("utf-8")).hexdigest()
        self.calls.append(
            {
                "prompt_sha256": prompt_hash,
                "input_sha256": input_hash,
                "config": config.to_dict(),
            }
        )
        key = (prompt_hash, input_hash)
        if key not in self.outputs:
            raise KeyError(f"No recorded output for prompt/input hashes: {key}")
        return self.outputs[key]


@dataclass
class RecordedJudge:
    """Return predefined blind decisions in order."""

    decisions: list[JudgeDecision]
    name: str = "recorded-judge"
    calls: list[dict[str, Any]] = field(default_factory=list)

    def judge(
        self,
        *,
        case: EvaluationCase,
        output_a: str,
        output_b: str,
    ) -> JudgeDecision:
        self.calls.append(
            {
                "case_id": case.case_id,
                "output_a_sha256": hashlib.sha256(
                    output_a.encode("utf-8")
                ).hexdigest(),
                "output_b_sha256": hashlib.sha256(
                    output_b.encode("utf-8")
                ).hexdigest(),
            }
        )
        if not self.decisions:
            raise RuntimeError("RecordedJudge has no decision remaining.")
        decision = self.decisions.pop(0)
        decision.validate()
        return decision


def _hard_checks(case: EvaluationCase, output: str) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    for required in case.required_substrings:
        checks.append(
            {
                "check": "required_substring",
                "value": required,
                "passed": required in output,
            }
        )
    for forbidden in case.forbidden_substrings:
        checks.append(
            {
                "check": "forbidden_substring",
                "value": forbidden,
                "passed": forbidden not in output,
            }
        )
    if case.require_json:
        import json

        try:
            json.loads(output)
            valid_json = True
        except (TypeError, ValueError):
            valid_json = False
        checks.append({"check": "valid_json", "passed": valid_json})
    if case.max_characters is not None:
        checks.append(
            {
                "check": "max_characters",
                "value": case.max_characters,
                "observed": len(output),
                "passed": len(output) <= case.max_characters,
            }
        )
    checks.extend(run_domain_checks(case.domain, case.input_text, output))
    checks.extend(run_case_checks(case.case_id, output))
    return {
        "passed": all(
            check["passed"]
            for check in checks
            if check.get("authoritative", True)
        ),
        "checks": checks,
    }


def _normalize_execution(value: str | ExecutionOutput) -> ExecutionOutput:
    if isinstance(value, ExecutionOutput):
        if not value.text.strip():
            raise ValueError("Executor returned empty output.")
        return value
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Executor returned empty output.")
    return ExecutionOutput(value)


def _optimized_is_a(case_id: str, seed: int) -> bool:
    digest = hashlib.sha256(f"{seed}:{case_id}".encode("utf-8")).digest()
    return digest[0] % 2 == 0


def _map_winner(winner: str, optimized_is_a: bool) -> str:
    if winner == "tie":
        return "tie"
    optimized_won = (winner == "A" and optimized_is_a) or (
        winner == "B" and not optimized_is_a
    )
    return "win" if optimized_won else "loss"


def _aggregate_judges(
    decisions: Sequence[JudgeDecision],
    *,
    optimized_is_a: bool,
) -> tuple[str, bool]:
    mapped = [_map_winner(decision.winner, optimized_is_a) for decision in decisions]
    fatal_optimized = any(
        decision.fatal_flaw_a if optimized_is_a else decision.fatal_flaw_b
        for decision in decisions
    )
    if fatal_optimized:
        return "loss", True
    wins = mapped.count("win")
    losses = mapped.count("loss")
    if wins > losses:
        return "win", False
    if losses > wins:
        return "loss", False
    return "tie", False


def evaluate_case(
    *,
    original_prompt: str,
    optimized_prompt: str,
    case: EvaluationCase,
    executor: PromptExecutor,
    judges: Sequence[BlindJudge],
    config: ExecutionConfig,
    blind_seed: int = 0,
) -> dict[str, Any]:
    case.validate()
    if len(judges) < 2:
        raise ValueError("At least two blind judges are required.")

    original_execution = _normalize_execution(
        executor.execute(
            prompt=original_prompt,
            input_text=case.input_text,
            config=config,
        )
    )
    optimized_execution = _normalize_execution(
        executor.execute(
            prompt=optimized_prompt,
            input_text=case.input_text,
            config=config,
        )
    )
    original_output = original_execution.text
    optimized_output = optimized_execution.text
    original_checks = _hard_checks(case, original_output)
    optimized_checks = _hard_checks(case, optimized_output)
    optimized_is_a = _optimized_is_a(case.case_id, blind_seed)
    output_a = optimized_output if optimized_is_a else original_output
    output_b = original_output if optimized_is_a else optimized_output

    critical_regression = original_checks["passed"] and not optimized_checks["passed"]
    deterministic_win = optimized_checks["passed"] and not original_checks["passed"]
    judge_records: list[dict[str, Any]] = []
    fatal_flaw = False
    if critical_regression:
        outcome = "loss"
    elif deterministic_win:
        outcome = "win"
    elif not original_checks["passed"] and not optimized_checks["passed"]:
        outcome = "tie"
    else:
        decisions: list[JudgeDecision] = []
        for judge in judges:
            decision = judge.judge(
                case=case,
                output_a=output_a,
                output_b=output_b,
            )
            decision.validate()
            decisions.append(decision)
            judge_records.append(
                {
                    "judge": judge.name,
                    **asdict(decision),
                }
            )
        outcome, fatal_flaw = _aggregate_judges(
            decisions,
            optimized_is_a=optimized_is_a,
        )

    record: dict[str, Any] = {
        "schema_version": "1.0.0",
        "case_id": case.case_id,
        "domain": case.domain,
        "difficulty": case.difficulty,
        "rubric": list(case.rubric),
        "case_sha256": sha256_json(asdict(case)),
        "executor": executor.name,
        "execution_config": config.to_dict(),
        "original_prompt_sha256": hashlib.sha256(
            original_prompt.encode("utf-8")
        ).hexdigest(),
        "optimized_prompt_sha256": hashlib.sha256(
            optimized_prompt.encode("utf-8")
        ).hexdigest(),
        "original_output": original_output,
        "optimized_output": optimized_output,
        "execution_metadata": {
            "original": original_execution.metadata,
            "optimized": optimized_execution.metadata,
        },
        "original_output_sha256": hashlib.sha256(
            original_output.encode("utf-8")
        ).hexdigest(),
        "optimized_output_sha256": hashlib.sha256(
            optimized_output.encode("utf-8")
        ).hexdigest(),
        "blind_map": {
            "A": "optimized" if optimized_is_a else "original",
            "B": "original" if optimized_is_a else "optimized",
            "seed": blind_seed,
        },
        "hard_checks": {
            "original": original_checks,
            "optimized": optimized_checks,
        },
        "judge_decisions": judge_records,
        "outcome": outcome,
        "critical_regression": critical_regression,
        "fatal_flaw": fatal_flaw,
    }
    record["record_sha256"] = hash_payload(record, "record_sha256")
    return record


def evaluate_suite(
    *,
    suite_id: str,
    original_prompt: str,
    optimized_prompt: str,
    cases: Sequence[EvaluationCase],
    executor: PromptExecutor,
    judges: Sequence[BlindJudge],
    config: ExecutionConfig,
    blind_seed: int = 0,
    repeated_or_cross_model: bool = False,
) -> dict[str, Any]:
    if not suite_id.strip():
        raise ValueError("suite_id must not be empty.")
    if not cases:
        raise ValueError("Evaluation suite must contain at least one case.")
    records = [
        evaluate_case(
            original_prompt=original_prompt,
            optimized_prompt=optimized_prompt,
            case=case,
            executor=executor,
            judges=judges,
            config=config,
            blind_seed=blind_seed,
        )
        for case in cases
    ]
    wins = sum(record["outcome"] == "win" for record in records)
    ties = sum(record["outcome"] == "tie" for record in records)
    losses = sum(record["outcome"] == "loss" for record in records)
    critical_regressions = sum(record["critical_regression"] for record in records)
    fatal_flaws = sum(record["fatal_flaw"] for record in records)
    optimized_hard_failures = sum(
        record["hard_checks"]["optimized"]["passed"] is not True
        for record in records
    )
    gate_passed = (
        len(records) >= 5
        and wins > losses
        and critical_regressions == 0
        and fatal_flaws == 0
        and optimized_hard_failures == 0
    )
    evidence: Evidence = infer_evidence(
        deterministic_checks_passed=True,
        matched_cases=len(records),
        comparative_improvement_passed=gate_passed,
        repeated_or_cross_model=repeated_or_cross_model,
    )
    result: dict[str, Any] = {
        "schema_version": "1.0.0",
        "suite_id": suite_id,
        "case_count": len(records),
        "wins": wins,
        "ties": ties,
        "losses": losses,
        "critical_regressions": critical_regressions,
        "fatal_flaws": fatal_flaws,
        "optimized_hard_failures": optimized_hard_failures,
        "gate_passed": gate_passed,
        "repeated_or_cross_model": repeated_or_cross_model,
        "evidence": asdict(evidence),
        "records": records,
    }
    result["evaluation_sha256"] = hash_payload(result, "evaluation_sha256")
    return result


def validate_evaluation(result: Any) -> list[str]:
    if not isinstance(result, dict):
        return ["evaluation root must be an object"]
    failures: list[str] = []
    if result.get("evaluation_sha256") != hash_payload(
        result,
        "evaluation_sha256",
    ):
        failures.append("evaluation hash mismatch")
    records = result.get("records")
    if not isinstance(records, list) or not records:
        return [*failures, "evaluation records must be a non-empty list"]

    outcomes: list[str] = []
    critical_count = 0
    fatal_count = 0
    optimized_hard_failure_count = 0
    configs: set[str] = set()
    original_prompts: set[str] = set()
    optimized_prompts: set[str] = set()
    case_ids: set[str] = set()
    for record in records:
        if not isinstance(record, dict):
            failures.append("evaluation record must be an object")
            continue
        case_id = record.get("case_id")
        if not isinstance(case_id, str) or case_id in case_ids:
            failures.append(f"duplicate or invalid case id: {case_id!r}")
        else:
            case_ids.add(case_id)
        if record.get("record_sha256") != hash_payload(record, "record_sha256"):
            failures.append(f"{case_id}: record hash mismatch")
        original_output = record.get("original_output")
        optimized_output = record.get("optimized_output")
        if not isinstance(original_output, str) or hashlib.sha256(
            original_output.encode("utf-8")
        ).hexdigest() != record.get("original_output_sha256"):
            failures.append(f"{case_id}: original output hash mismatch")
        if not isinstance(optimized_output, str) or hashlib.sha256(
            optimized_output.encode("utf-8")
        ).hexdigest() != record.get("optimized_output_sha256"):
            failures.append(f"{case_id}: optimized output hash mismatch")
        configs.add(sha256_json(record.get("execution_config")))
        original_prompts.add(str(record.get("original_prompt_sha256")))
        optimized_prompts.add(str(record.get("optimized_prompt_sha256")))

        outcome = record.get("outcome")
        if outcome not in {"win", "tie", "loss"}:
            failures.append(f"{case_id}: invalid outcome")
        else:
            outcomes.append(outcome)
        critical = record.get("critical_regression") is True
        fatal = record.get("fatal_flaw") is True
        critical_count += critical
        fatal_count += fatal
        hard_checks = record.get("hard_checks")
        if not isinstance(hard_checks, dict):
            failures.append(f"{case_id}: hard checks must be an object")
            optimized_hard_failure_count += 1
        else:
            optimized_checks = hard_checks.get("optimized")
            if (
                not isinstance(optimized_checks, dict)
                or optimized_checks.get("passed") is not True
            ):
                optimized_hard_failure_count += 1
        if critical and outcome != "loss":
            failures.append(f"{case_id}: critical regression must be a loss")
        if fatal and outcome != "loss":
            failures.append(f"{case_id}: fatal flaw must be a loss")

    if len(configs) != 1:
        failures.append("execution settings are not matched across records")
    if len(original_prompts) != 1 or len(optimized_prompts) != 1:
        failures.append("prompt hashes are inconsistent across records")

    wins = outcomes.count("win")
    ties = outcomes.count("tie")
    losses = outcomes.count("loss")
    expected_gate = (
        len(records) >= 5
        and wins > losses
        and critical_count == 0
        and fatal_count == 0
        and optimized_hard_failure_count == 0
    )
    for field, expected in (
        ("case_count", len(records)),
        ("wins", wins),
        ("ties", ties),
        ("losses", losses),
        ("critical_regressions", critical_count),
        ("fatal_flaws", fatal_count),
        ("optimized_hard_failures", optimized_hard_failure_count),
        ("gate_passed", expected_gate),
    ):
        if result.get(field) != expected:
            failures.append(f"aggregate mismatch: {field}")

    evidence_data = result.get("evidence")
    if not isinstance(evidence_data, dict):
        failures.append("evaluation evidence must be an object")
    else:
        try:
            evidence = Evidence(
                level=evidence_data.get("level", ""),
                status=evidence_data.get("status", ""),
                claim=evidence_data.get("claim", ""),
                limitations=tuple(evidence_data.get("limitations", [])),
            )
            evidence.validate()
            expected_evidence = infer_evidence(
                deterministic_checks_passed=True,
                matched_cases=len(records),
                comparative_improvement_passed=expected_gate,
                repeated_or_cross_model=(
                    result.get("repeated_or_cross_model") is True
                ),
            )
            if evidence != expected_evidence:
                failures.append("evaluation evidence does not match aggregate gate")
        except (TypeError, ValueError) as exc:
            failures.append(f"invalid evaluation evidence: {exc}")
    return failures
