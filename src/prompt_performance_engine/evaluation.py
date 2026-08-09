"""Matched original-versus-optimized Prompt evaluation runtime."""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any, Protocol, Sequence

from .contracts import ARTIFACT_SCHEMA_VERSION
from .evidence import Evidence, infer_evidence
from .case_checks import run_case_checks
from .domain_checks import run_domain_checks
from .hashing import hash_payload, sha256_json
from .software_sandbox import DockerSandbox


SCHEMA_VERSION = ARTIFACT_SCHEMA_VERSION
EVALUATION_FIELDS = {
    "schema_version",
    "suite_id",
    "case_count",
    "wins",
    "ties",
    "losses",
    "critical_regressions",
    "fatal_flaws",
    "optimized_hard_failures",
    "gate_passed",
    "repeated_or_cross_model",
    "evidence",
    "records",
    "evaluation_sha256",
}
EVIDENCE_FIELDS = {"level", "status", "claim", "limitations"}
RECORD_FIELDS = {
    "schema_version",
    "case_id",
    "domain",
    "difficulty",
    "rubric",
    "case_sha256",
    "executor",
    "execution_config",
    "original_prompt_sha256",
    "optimized_prompt_sha256",
    "original_output",
    "optimized_output",
    "execution_metadata",
    "original_output_sha256",
    "optimized_output_sha256",
    "blind_map",
    "hard_checks",
    "judge_decisions",
    "outcome",
    "critical_regression",
    "fatal_flaw",
    "record_sha256",
}
JUDGE_DECISION_FIELDS = {
    "judge",
    "winner",
    "reason",
    "fatal_flaw_a",
    "fatal_flaw_b",
    "metadata",
}
RECORDED_RUN_FIELDS = frozenset(
    {
        "schema_version",
        "suite_id",
        "job_id",
        "blind_seed",
        "execution_config",
        "outputs",
        "judges",
    }
)
RECORDED_RUN_REQUIRED_FIELDS = RECORDED_RUN_FIELDS - {"blind_seed"}
RECORDED_EXECUTION_CONFIG_FIELDS = frozenset(
    {"model", "temperature", "max_tokens", "seed"}
)
RECORDED_OUTPUT_FIELDS = frozenset({"case_id", "original", "optimized"})
RECORDED_JUDGE_FIELDS = frozenset({"name", "decisions"})
RECORDED_DECISION_FIELDS = frozenset(
    {"winner", "reason", "fatal_flaw_a", "fatal_flaw_b"}
)


def _require_exact_fields(
    value: dict[str, Any],
    expected: set[str],
    *,
    label: str,
    failures: list[str],
) -> None:
    if set(value) != expected:
        failures.append(f"{label} fields do not match the contract")


def _require_recorded_fields(
    value: Any,
    *,
    allowed: frozenset[str],
    required: frozenset[str],
    label: str,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object.")
    names = set(value)
    missing = sorted(required - names)
    unknown = sorted(names - allowed, key=str)
    if missing or unknown:
        details: list[str] = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if unknown:
            details.append("unknown " + ", ".join(map(str, unknown)))
        raise ValueError(
            f"{label} fields do not match the contract ({'; '.join(details)})."
        )
    return value


def _normalized_json_integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value)
    if isinstance(value, Decimal) and value.is_finite() and value == value.to_integral():
        return int(value)
    return None


def _is_finite_json_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return False
    if isinstance(value, Decimal):
        return value.is_finite()
    return not isinstance(value, float) or math.isfinite(value)


def validate_recorded_run_input(
    run: Any,
    *,
    suite_id: str,
    job_id: str,
    case_ids: Sequence[str],
) -> dict[str, Any]:
    """Validate and normalize one recorded-run transport for its benchmark job."""

    root = dict(
        _require_recorded_fields(
            run,
            allowed=RECORDED_RUN_FIELDS,
            required=RECORDED_RUN_REQUIRED_FIELDS,
            label="recorded run",
        )
    )
    if root.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"recorded run schema_version must be {SCHEMA_VERSION!r}.")
    if root.get("suite_id") != suite_id:
        raise ValueError("Recorded run does not match the benchmark suite.")
    if root.get("job_id") != job_id:
        raise ValueError("Recorded run does not match the benchmark job.")
    if "blind_seed" in root:
        blind_seed = _normalized_json_integer(root["blind_seed"])
        if blind_seed is None:
            raise ValueError("recorded run blind_seed must be an integer.")
        root["blind_seed"] = blind_seed

    config = dict(
        _require_recorded_fields(
            root.get("execution_config"),
            allowed=RECORDED_EXECUTION_CONFIG_FIELDS,
            required=frozenset({"model"}),
            label="recorded run execution_config",
        )
    )
    model = config.get("model")
    if not isinstance(model, str) or not model.strip():
        raise ValueError("recorded run execution_config.model must be non-empty.")
    if "temperature" in config and not _is_finite_json_number(
        config["temperature"]
    ):
        raise ValueError(
            "recorded run execution_config.temperature must be a finite number."
        )
    if isinstance(config.get("temperature"), Decimal):
        temperature = float(config["temperature"])
        if not math.isfinite(temperature):
            raise ValueError(
                "recorded run execution_config.temperature is outside the "
                "supported runtime range."
            )
        config["temperature"] = temperature
    if "max_tokens" in config:
        max_tokens = _normalized_json_integer(config["max_tokens"])
        if max_tokens is None or max_tokens < 1:
            raise ValueError(
                "recorded run execution_config.max_tokens must be a positive integer."
            )
        config["max_tokens"] = max_tokens
    if "seed" in config and config["seed"] is not None:
        seed = _normalized_json_integer(config["seed"])
        if seed is None:
            raise ValueError(
                "recorded run execution_config.seed must be an integer or null."
            )
        config["seed"] = seed
    root["execution_config"] = config

    expected_case_ids = list(case_ids)
    if (
        not expected_case_ids
        or any(not isinstance(case_id, str) or not case_id for case_id in expected_case_ids)
        or len(expected_case_ids) != len(set(expected_case_ids))
    ):
        raise ValueError("Benchmark job case ids must be non-empty and unique.")
    outputs = root.get("outputs")
    if not isinstance(outputs, list):
        raise ValueError("recorded run outputs must be an array.")
    if len(outputs) != len(expected_case_ids):
        raise ValueError("recorded run must contain exactly one output per case.")
    observed_case_ids: list[str] = []
    for index, raw_output in enumerate(outputs):
        output = _require_recorded_fields(
            raw_output,
            allowed=RECORDED_OUTPUT_FIELDS,
            required=RECORDED_OUTPUT_FIELDS,
            label=f"recorded run outputs[{index}]",
        )
        case_id = output.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            raise ValueError(f"recorded run outputs[{index}].case_id must be non-empty.")
        for side in ("original", "optimized"):
            text = output.get(side)
            if not isinstance(text, str) or not text.strip():
                raise ValueError(
                    f"recorded run outputs[{index}].{side} must be non-empty text."
                )
        observed_case_ids.append(case_id)
    if len(observed_case_ids) != len(set(observed_case_ids)):
        raise ValueError("recorded run output case_id values must be unique.")
    missing_cases = sorted(set(expected_case_ids) - set(observed_case_ids))
    unknown_cases = sorted(set(observed_case_ids) - set(expected_case_ids))
    if missing_cases or unknown_cases:
        raise ValueError(
            "recorded run output case_id set is incomplete or unknown "
            f"(missing={missing_cases}, unknown={unknown_cases})."
        )

    judges = root.get("judges")
    if not isinstance(judges, list) or len(judges) < 2:
        raise ValueError("recorded run must contain at least two judges.")
    judge_names: list[str] = []
    decision_counts: list[int] = []
    for judge_index, raw_judge in enumerate(judges):
        judge = _require_recorded_fields(
            raw_judge,
            allowed=RECORDED_JUDGE_FIELDS,
            required=RECORDED_JUDGE_FIELDS,
            label=f"recorded run judges[{judge_index}]",
        )
        name = judge.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(
                f"recorded run judges[{judge_index}].name must be non-empty."
            )
        judge_names.append(name)
        decisions = judge.get("decisions")
        if not isinstance(decisions, list):
            raise ValueError(
                f"recorded run judges[{judge_index}].decisions must be an array."
            )
        if len(decisions) > len(expected_case_ids):
            raise ValueError("recorded judge decisions exceed the benchmark case count.")
        decision_counts.append(len(decisions))
        for decision_index, raw_decision in enumerate(decisions):
            label = (
                f"recorded run judges[{judge_index}].decisions[{decision_index}]"
            )
            decision = _require_recorded_fields(
                raw_decision,
                allowed=RECORDED_DECISION_FIELDS,
                required=RECORDED_DECISION_FIELDS,
                label=label,
            )
            winner = decision.get("winner")
            if not isinstance(winner, str) or winner not in {"A", "B", "tie"}:
                raise ValueError(f"{label}.winner is invalid.")
            reason = decision.get("reason")
            if not isinstance(reason, str) or not reason.strip():
                raise ValueError(f"{label}.reason must be non-empty.")
            if not isinstance(decision.get("fatal_flaw_a"), bool) or not isinstance(
                decision.get("fatal_flaw_b"), bool
            ):
                raise ValueError(f"{label} fatal-flaw fields must be booleans.")
    if len(judge_names) != len(set(judge_names)):
        raise ValueError("recorded run judge names must be unique.")
    if len(set(decision_counts)) != 1:
        raise ValueError("recorded run judges must provide equal decision counts.")
    return root


@dataclass(frozen=True)
class ExecutionConfig:
    model: str
    temperature: float | None = 0.0
    max_tokens: int | None = 2048
    seed: int | None = 0

    def __post_init__(self) -> None:
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("Execution model must not be empty.")
        if self.temperature is not None:
            if not _is_finite_json_number(self.temperature):
                raise ValueError("temperature must be finite when specified.")
            normalized_temperature = float(self.temperature)
            if not math.isfinite(normalized_temperature):
                raise ValueError("temperature is outside the supported runtime range.")
            object.__setattr__(self, "temperature", normalized_temperature)
        if self.max_tokens is not None:
            normalized_max_tokens = _normalized_json_integer(self.max_tokens)
            if normalized_max_tokens is None or normalized_max_tokens < 1:
                raise ValueError("max_tokens must be positive when specified.")
            object.__setattr__(self, "max_tokens", normalized_max_tokens)
        if self.seed is not None:
            normalized_seed = _normalized_json_integer(self.seed)
            if normalized_seed is None:
                raise ValueError("seed must be an integer when specified.")
            object.__setattr__(self, "seed", normalized_seed)

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
        if not isinstance(self.winner, str) or self.winner not in {"A", "B", "tie"}:
            raise ValueError(f"Unknown judge winner: {self.winner!r}.")
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("Judge reason must be a non-empty string.")
        if not isinstance(self.fatal_flaw_a, bool) or not isinstance(
            self.fatal_flaw_b,
            bool,
        ):
            raise ValueError("Judge fatal-flaw fields must be JSON booleans.")
        if not isinstance(self.metadata, dict):
            raise ValueError("Judge metadata must be an object.")


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


def _contains_unnegated_forbidden(output: str, forbidden: str) -> bool:
    """Distinguish requested content from an explicit negative constraint."""
    lowered = output.casefold()
    target = forbidden.casefold()
    start = 0
    while True:
        index = lowered.find(target, start)
        if index < 0:
            return False
        clause_start = max(
            lowered.rfind(mark, 0, index) for mark in (".", ";", ":", "\n")
        )
        context = lowered[clause_start + 1 : index]
        negation = re.search(
            r"\b(?:no|not|never|without|avoid(?:ing)?|exclude\w*|reject\w*|"
            r"refus\w*|declin\w*|prohibit\w*|cannot|can't|won't|"
            r"(?:do|must|should)\s+not)\b",
            context,
        )
        if negation is not None:
            scope = context[negation.end() :]
            if re.search(
                r"\b(?:but|except|however|include\w*|add\w*|show\w*|"
                r"depict\w*|feature\w*)\b",
                scope,
            ):
                return True
            start = index + len(target)
            continue

        before = lowered[max(0, index - 240) : index]
        after = lowered[index + len(target) : index + len(target) + 160]
        reported_request = re.search(
            r"\b(?:request|prompt|brief|proposal|note|instruction|claim|"
            r"message|stakeholder)\b.{0,120}\b(?:ask\w*|instruct\w*|"
            r"require\w*|demand\w*|request\w*|contain\w*|include\w*|"
            r"say\w*|use\w*)\b[^.!?;\n]*$",
            before,
            flags=re.DOTALL,
        )
        warning_context = re.search(
            r"\b(?:risk|warning|red flag|fraud|unsafe|misleading|deceptive|"
            r"unsupported|unsubstantiated|review|verify)\b[^.!?;\n]{0,140}$",
            before,
        ) or re.search(
            r"^[^.!?;\n]{0,120}\b(?:risk|warning|red flag|fraud|unsafe|"
            r"misleading|deceptive|unsupported|unsubstantiated)\b",
            after,
        )
        chinese_rejection = re.search(
            r"(?:不|无|未|勿|拒绝|禁止|避免|无法|不得|不应|虚假|伪造|误导)"
            r"[^。！？\n]{0,100}$",
            before,
        ) or re.search(
            r"^[^。！？\n]{0,100}(?:不支持|无依据|虚假|误导|拒绝|禁止|"
            r"无法|不得|不应)",
            after,
        )
        if reported_request or warning_context or chinese_rejection:
            start = index + len(target)
            continue
        return True


def _hard_checks(
    case: EvaluationCase,
    output: str,
    *,
    software_sandbox: DockerSandbox | None = None,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    for required in case.required_substrings:
        checks.append(
            {
                "check": "required_substring",
                "value": required,
                "passed": required.casefold() in output.casefold(),
            }
        )
    for forbidden in case.forbidden_substrings:
        checks.append(
            {
                "check": "forbidden_substring",
                "value": forbidden,
                "passed": not _contains_unnegated_forbidden(
                    output,
                    forbidden,
                ),
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
    checks.extend(
        run_case_checks(
            case.case_id,
            output,
            sandbox=software_sandbox,
        )
    )
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
    software_sandbox: DockerSandbox | None = None,
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
    original_checks = _hard_checks(
        case,
        original_output,
        software_sandbox=software_sandbox,
    )
    optimized_checks = _hard_checks(
        case,
        optimized_output,
        software_sandbox=software_sandbox,
    )
    optimized_is_a = _optimized_is_a(case.case_id, blind_seed)
    output_a = optimized_output if optimized_is_a else original_output
    output_b = original_output if optimized_is_a else optimized_output

    critical_regression = original_checks["passed"] and not optimized_checks["passed"]
    judge_records: list[dict[str, Any]] = []
    fatal_flaw = False
    if critical_regression:
        outcome = "loss"
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
        "schema_version": SCHEMA_VERSION,
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
    software_sandbox: DockerSandbox | None = None,
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
            software_sandbox=software_sandbox,
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
    )
    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "suite_id": suite_id,
        "case_count": len(records),
        "wins": wins,
        "ties": ties,
        "losses": losses,
        "critical_regressions": critical_regressions,
        "fatal_flaws": fatal_flaws,
        "optimized_hard_failures": optimized_hard_failures,
        "gate_passed": gate_passed,
        # Kept in the wire contract for compatibility.  A suite evaluation is
        # always one run and cannot authoritatively claim repetition; callers
        # must use a validated benchmark replicate report to obtain E3.
        "repeated_or_cross_model": False,
        "evidence": asdict(evidence),
        "records": records,
    }
    result["evaluation_sha256"] = hash_payload(result, "evaluation_sha256")
    return result


def _validate_evaluation(result: Any) -> list[str]:
    if not isinstance(result, dict):
        return ["evaluation root must be an object"]
    failures: list[str] = []
    _require_exact_fields(
        result,
        EVALUATION_FIELDS,
        label="evaluation",
        failures=failures,
    )
    if result.get("schema_version") != SCHEMA_VERSION:
        failures.append("unsupported evaluation schema")
    if not isinstance(result.get("suite_id"), str) or not result["suite_id"]:
        failures.append("evaluation suite_id must be a non-empty string")
    if result.get("evaluation_sha256") != hash_payload(
        result,
        "evaluation_sha256",
    ):
        failures.append("evaluation hash mismatch")
    for field in (
        "case_count",
        "wins",
        "ties",
        "losses",
        "critical_regressions",
        "fatal_flaws",
        "optimized_hard_failures",
    ):
        count = _normalized_json_integer(result.get(field))
        if count is None or count < 0:
            failures.append(f"evaluation {field} must be a non-negative integer")
    if not isinstance(result.get("gate_passed"), bool):
        failures.append("evaluation gate_passed must be a boolean")
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

    def hard_group_passed(case_id: Any, label: str, value: Any) -> bool | None:
        if not isinstance(value, dict):
            failures.append(f"{case_id}: {label} hard checks must be an object")
            return None
        _require_exact_fields(
            value,
            {"passed", "checks"},
            label=f"{case_id}: {label} hard-check group",
            failures=failures,
        )
        checks = value.get("checks")
        if not isinstance(checks, list):
            failures.append(f"{case_id}: {label} hard checks must be an array")
            return None
        authoritative_results: list[bool] = []
        for check in checks:
            if not isinstance(check, dict) or not isinstance(check.get("passed"), bool):
                failures.append(f"{case_id}: {label} hard check is invalid")
                return None
            check_name = check.get("check")
            if not isinstance(check_name, str) or not check_name:
                failures.append(f"{case_id}: {label} hard-check name is invalid")
                return None
            if check_name in {"required_substring", "forbidden_substring"}:
                expected_fields = {"check", "value", "passed"}
            elif check_name == "valid_json":
                expected_fields = {"check", "passed"}
            elif check_name == "max_characters":
                expected_fields = {"check", "value", "observed", "passed"}
            else:
                expected_fields = {
                    "check",
                    "passed",
                    "detail",
                    "authoritative",
                    "source",
                }
            _require_exact_fields(
                check,
                expected_fields,
                label=f"{case_id}: {label} hard check",
                failures=failures,
            )
            authoritative = check.get("authoritative", True)
            if not isinstance(authoritative, bool):
                failures.append(
                    f"{case_id}: {label} hard-check authority flag is invalid"
                )
                return None
            if authoritative:
                authoritative_results.append(check["passed"])
        expected = all(authoritative_results)
        if not isinstance(value.get("passed"), bool) or value["passed"] != expected:
            failures.append(f"{case_id}: {label} hard-check result mismatch")
        return expected

    for record in records:
        if not isinstance(record, dict):
            failures.append("evaluation record must be an object")
            continue
        _require_exact_fields(
            record,
            RECORD_FIELDS,
            label="evaluation record",
            failures=failures,
        )
        case_id = record.get("case_id")
        if not isinstance(case_id, str) or not case_id or case_id in case_ids:
            failures.append(f"duplicate or invalid case id: {case_id!r}")
        else:
            case_ids.add(case_id)
        if record.get("schema_version") != SCHEMA_VERSION:
            failures.append(f"{case_id}: unsupported record schema")
        if not isinstance(record.get("domain"), str) or not record["domain"]:
            failures.append(f"{case_id}: domain must be a non-empty string")
        difficulty = record.get("difficulty")
        if not isinstance(difficulty, str) or difficulty not in {
            "normal",
            "difficult",
            "adversarial",
        }:
            failures.append(f"{case_id}: difficulty is invalid")
        rubric = record.get("rubric")
        if not isinstance(rubric, list) or not rubric or any(
            not isinstance(item, str) or not item for item in rubric
        ):
            failures.append(f"{case_id}: rubric is invalid")
        if not isinstance(record.get("executor"), str) or not record["executor"]:
            failures.append(f"{case_id}: executor must be a non-empty string")
        for field in (
            "case_sha256",
            "original_prompt_sha256",
            "optimized_prompt_sha256",
        ):
            digest = record.get(field)
            if not isinstance(digest, str) or re.fullmatch(
                r"[0-9a-f]{64}", digest
            ) is None:
                failures.append(f"{case_id}: invalid hash field: {field}")
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
        execution_config = record.get("execution_config")
        if not isinstance(execution_config, dict):
            failures.append(f"{case_id}: execution config must be an object")
        else:
            _require_exact_fields(
                execution_config,
                {"model", "temperature", "max_tokens", "seed"},
                label=f"{case_id}: execution config",
                failures=failures,
            )
            model = execution_config.get("model")
            if not isinstance(model, str) or not model.strip():
                failures.append(
                    f"{case_id}: execution config model must be a non-empty string"
                )
            temperature = execution_config.get("temperature")
            if temperature is not None and not _is_finite_json_number(temperature):
                failures.append(
                    f"{case_id}: execution config temperature must be a finite "
                    "number or null"
                )
            max_tokens = execution_config.get("max_tokens")
            normalized_max_tokens = (
                None
                if max_tokens is None
                else _normalized_json_integer(max_tokens)
            )
            if max_tokens is not None and (
                normalized_max_tokens is None or normalized_max_tokens < 1
            ):
                failures.append(
                    f"{case_id}: execution config max_tokens must be a positive "
                    "integer or null"
                )
            seed = execution_config.get("seed")
            if seed is not None and _normalized_json_integer(seed) is None:
                failures.append(
                    f"{case_id}: execution config seed must be an integer or null"
                )
        execution_metadata = record.get("execution_metadata")
        if not isinstance(execution_metadata, dict):
            failures.append(f"{case_id}: execution metadata must be an object")
        else:
            _require_exact_fields(
                execution_metadata,
                {"original", "optimized"},
                label=f"{case_id}: execution metadata",
                failures=failures,
            )
            if any(
                not isinstance(execution_metadata.get(side), dict)
                for side in ("original", "optimized")
            ):
                failures.append(f"{case_id}: execution metadata entry is invalid")
        configs.add(sha256_json(execution_config))
        original_prompts.add(str(record.get("original_prompt_sha256")))
        optimized_prompts.add(str(record.get("optimized_prompt_sha256")))

        outcome = record.get("outcome")
        if not isinstance(outcome, str) or outcome not in {"win", "tie", "loss"}:
            failures.append(f"{case_id}: invalid outcome")
        else:
            outcomes.append(outcome)
        if not isinstance(record.get("critical_regression"), bool):
            failures.append(f"{case_id}: critical regression must be a boolean")
        if not isinstance(record.get("fatal_flaw"), bool):
            failures.append(f"{case_id}: fatal flaw must be a boolean")
        critical = record.get("critical_regression") is True
        fatal = record.get("fatal_flaw") is True
        critical_count += critical
        fatal_count += fatal
        hard_checks = record.get("hard_checks")
        if not isinstance(hard_checks, dict):
            failures.append(f"{case_id}: hard checks must be an object")
            optimized_hard_failure_count += 1
            original_passed = None
            optimized_passed = None
        else:
            _require_exact_fields(
                hard_checks,
                {"original", "optimized"},
                label=f"{case_id}: hard checks",
                failures=failures,
            )
            original_passed = hard_group_passed(
                case_id,
                "original",
                hard_checks.get("original"),
            )
            optimized_passed = hard_group_passed(
                case_id,
                "optimized",
                hard_checks.get("optimized"),
            )
            if optimized_passed is not True:
                optimized_hard_failure_count += 1
        blind_map = record.get("blind_map")
        blind_a = blind_map.get("A") if isinstance(blind_map, dict) else None
        blind_b = blind_map.get("B") if isinstance(blind_map, dict) else None
        blind_valid = (
            isinstance(blind_map, dict)
            and isinstance(blind_map.get("seed"), int)
            and not isinstance(blind_map.get("seed"), bool)
            and isinstance(blind_a, str)
            and isinstance(blind_b, str)
            and {blind_a, blind_b} == {
                "original",
                "optimized",
            }
        )
        optimized_is_a = False
        if not blind_valid:
            failures.append(f"{case_id}: blind map is invalid")
        else:
            optimized_is_a = _optimized_is_a(case_id, blind_map["seed"])
            expected_map = {
                "A": "optimized" if optimized_is_a else "original",
                "B": "original" if optimized_is_a else "optimized",
                "seed": blind_map["seed"],
            }
            if blind_map != expected_map:
                failures.append(f"{case_id}: blind map does not match its seed")
                blind_valid = False
        judge_data = record.get("judge_decisions")
        decisions: list[JudgeDecision] = []
        judges_valid = isinstance(judge_data, list)
        if not judges_valid:
            failures.append(f"{case_id}: judge decisions must be an array")
        else:
            for item in judge_data:
                if not isinstance(item, dict) or not isinstance(
                    item.get("judge"), str
                ) or not item.get("judge"):
                    failures.append(f"{case_id}: judge decision is invalid")
                    judges_valid = False
                    continue
                _require_exact_fields(
                    item,
                    JUDGE_DECISION_FIELDS,
                    label=f"{case_id}: judge decision",
                    failures=failures,
                )
                try:
                    decision = JudgeDecision(
                        winner=item.get("winner"),
                        reason=item.get("reason"),
                        fatal_flaw_a=item.get("fatal_flaw_a"),
                        fatal_flaw_b=item.get("fatal_flaw_b"),
                        metadata=item.get("metadata"),
                    )
                    decision.validate()
                    decisions.append(decision)
                except (TypeError, ValueError) as exc:
                    failures.append(f"{case_id}: invalid judge decision: {exc}")
                    judges_valid = False
        if original_passed is not None and optimized_passed is not None:
            expected_critical = original_passed and not optimized_passed
            expected_fatal = False
            expected_outcome: str | None
            if expected_critical:
                expected_outcome = "loss"
            elif not original_passed and not optimized_passed:
                expected_outcome = "tie"
            elif blind_valid and judges_valid and len(decisions) >= 2:
                expected_outcome, expected_fatal = _aggregate_judges(
                    decisions,
                    optimized_is_a=optimized_is_a,
                )
            else:
                expected_outcome = None
                failures.append(f"{case_id}: matched outputs require two blind judges")
            if record.get("critical_regression") is not expected_critical:
                failures.append(f"{case_id}: critical regression is not derived")
            if expected_outcome is not None and outcome != expected_outcome:
                failures.append(f"{case_id}: outcome does not match authoritative facts")
            if record.get("fatal_flaw") is not expected_fatal:
                failures.append(f"{case_id}: fatal flaw is not derived")

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
        _require_exact_fields(
            evidence_data,
            EVIDENCE_FIELDS,
            label="evaluation evidence",
            failures=failures,
        )
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
            )
            if evidence != expected_evidence:
                failures.append("evaluation evidence does not match aggregate gate")
        except (TypeError, ValueError) as exc:
            failures.append(f"invalid evaluation evidence: {exc}")
    if result.get("repeated_or_cross_model") is not False:
        failures.append("a suite evaluation cannot claim repeated-run authority")
    return failures


def validate_evaluation(result: Any) -> list[str]:
    """Validate untrusted evaluation input without leaking structural errors."""

    try:
        return _validate_evaluation(result)
    except (
        ArithmeticError,
        AttributeError,
        KeyError,
        RecursionError,
        TypeError,
        UnicodeError,
        ValueError,
    ) as exc:
        return [f"malformed evaluation: {exc}"]
