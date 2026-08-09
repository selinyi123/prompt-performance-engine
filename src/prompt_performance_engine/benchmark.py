"""Load and validate versioned benchmark definitions."""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from .contracts import ARTIFACT_SCHEMA_VERSION, load_strict_json_object
from .evaluation import EvaluationCase


CONCRETE_PAYLOAD_MARKERS = {
    "agents_automation": (
        ("EXECUTION_MODE: simulated_tool_trace",),
        ("AVAILABLE_TOOLS:",),
        ("TOOL_RESULTS:", "LATEST_TOOL_RESULTS:"),
    ),
    "structured_data": (("OUTPUT_SCHEMA:",), ("CONTENT:",)),
    "translation_localization": (
        ("TARGET_LOCALE:",),
        ("SOURCE_TEXT:", "SOURCE_STRINGS:"),
    ),
    "marketing_sales": (
        ("BRIEF:",),
        ("PRODUCT_FACTS:",),
        ("AUDIENCE:",),
        ("CHANNEL:",),
        ("CTA:",),
        ("EVIDENCE:",),
    ),
}
MINIMUM_CONCRETE_PAYLOAD_CHARACTERS = 200
DOMAIN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
SOURCE_PAYLOAD_MARKERS = (
    "EVIDENCE_PACKET:",
    "SOURCE_NOTES:",
    "SOURCE_POLICY:",
)
BENCHMARK_ROOT_FIELDS = frozenset({"schema_version", "suite_id", "jobs"})
CATALOG_ROOT_FIELDS = frozenset({"schema_version", "suite_id", "includes"})
BENCHMARK_JOB_FIELDS = frozenset(
    {"job_id", "domain", "source_prompt", "cases"}
)
BENCHMARK_CASE_REQUIRED_FIELDS = frozenset(
    {"case_id", "input_text", "rubric"}
)
BENCHMARK_CASE_FIELDS = frozenset(
    {
        *BENCHMARK_CASE_REQUIRED_FIELDS,
        "domain",
        "difficulty",
        "tags",
        "required_substrings",
        "forbidden_substrings",
        "require_json",
        "max_characters",
    }
)
BENCHMARK_CASE_STRING_ARRAY_FIELDS = (
    "rubric",
    "tags",
    "required_substrings",
    "forbidden_substrings",
)


@dataclass(frozen=True)
class BenchmarkJob:
    job_id: str
    domain: str
    source_prompt: str
    cases: tuple[EvaluationCase, ...]


def _require_exact_fields(
    data: Any,
    *,
    required: frozenset[str],
    allowed: frozenset[str],
    label: str,
) -> None:
    if not isinstance(data, dict):
        raise ValueError(f"{label} must be an object.")
    fields = set(data)
    missing = sorted(required - fields)
    unknown = sorted(fields - allowed, key=str)
    if missing or unknown:
        details: list[str] = []
        if missing:
            details.append(f"missing fields {missing}")
        if unknown:
            details.append(f"unknown fields {unknown}")
        raise ValueError(f"{label} has invalid fields: {', '.join(details)}.")


def _require_string(value: Any, *, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string.")
    return value


def _require_string_array(value: Any, *, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) for item in value
    ):
        raise ValueError(f"{label} must be an array of strings.")
    return tuple(value)


def _is_json_integer(value: Any, *, minimum: int | None = None) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        valid = True
    elif isinstance(value, float):
        valid = math.isfinite(value) and value.is_integer()
    elif isinstance(value, Decimal):
        valid = value.is_finite() and value == value.to_integral_value()
    else:
        valid = False
    return valid and (minimum is None or value >= minimum)


def _validate_schema_version(data: dict[str, Any], *, label: str) -> None:
    version = data["schema_version"]
    if not isinstance(version, str) or version != ARTIFACT_SCHEMA_VERSION:
        raise ValueError(
            f"{label} schema_version must be {ARTIFACT_SCHEMA_VERSION!r}."
        )


def case_from_dict(data: dict[str, Any], *, default_domain: str = "generic") -> EvaluationCase:
    _require_exact_fields(
        data,
        required=BENCHMARK_CASE_REQUIRED_FIELDS,
        allowed=BENCHMARK_CASE_FIELDS,
        label="benchmark case",
    )
    default_domain = _require_string(
        default_domain,
        label="benchmark case default_domain",
    )
    case_id = _require_string(data["case_id"], label="benchmark case case_id")
    input_text = _require_string(
        data["input_text"],
        label=f"benchmark case {case_id!r} input_text",
    )
    domain = _require_string(
        data.get("domain", default_domain),
        label=f"benchmark case {case_id!r} domain",
    )
    difficulty = _require_string(
        data.get("difficulty", "normal"),
        label=f"benchmark case {case_id!r} difficulty",
    )
    arrays = {
        field: _require_string_array(
            data.get(field, []),
            label=f"benchmark case {case_id!r} {field}",
        )
        for field in BENCHMARK_CASE_STRING_ARRAY_FIELDS
    }
    require_json = data.get("require_json", False)
    if not isinstance(require_json, bool):
        raise ValueError(
            f"benchmark case {case_id!r} require_json must be a boolean."
        )
    max_characters = data.get("max_characters")
    if max_characters is not None and not _is_json_integer(
        max_characters,
        minimum=1,
    ):
        raise ValueError(
            f"benchmark case {case_id!r} max_characters must be null or a "
            "positive integer."
        )
    return EvaluationCase(
        case_id=case_id,
        input_text=input_text,
        rubric=arrays["rubric"],
        domain=domain,
        difficulty=difficulty,
        tags=arrays["tags"],
        required_substrings=arrays["required_substrings"],
        forbidden_substrings=arrays["forbidden_substrings"],
        require_json=require_json,
        max_characters=(
            int(max_characters) if max_characters is not None else None
        ),
    )


def _benchmark_from_data(
    data: dict[str, Any],
) -> tuple[str, tuple[BenchmarkJob, ...]]:
    _require_exact_fields(
        data,
        required=BENCHMARK_ROOT_FIELDS,
        allowed=BENCHMARK_ROOT_FIELDS,
        label="benchmark definition",
    )
    _validate_schema_version(data, label="benchmark definition")
    suite_id = _require_string(data["suite_id"], label="benchmark suite_id")
    raw_jobs = data["jobs"]
    if not isinstance(raw_jobs, list):
        raise ValueError("benchmark jobs must be an array.")
    jobs: list[BenchmarkJob] = []
    for job_index, raw_job in enumerate(raw_jobs):
        job_label = f"benchmark job {job_index}"
        _require_exact_fields(
            raw_job,
            required=BENCHMARK_JOB_FIELDS,
            allowed=BENCHMARK_JOB_FIELDS,
            label=job_label,
        )
        job_id = _require_string(raw_job["job_id"], label=f"{job_label} job_id")
        domain = _require_string(raw_job["domain"], label=f"{job_label} domain")
        source_prompt = _require_string(
            raw_job["source_prompt"],
            label=f"{job_label} source_prompt",
        )
        raw_cases = raw_job["cases"]
        if not isinstance(raw_cases, list):
            raise ValueError(f"{job_label} cases must be an array.")
        cases = tuple(
            case_from_dict(case, default_domain=domain)
            for case in raw_cases
        )
        jobs.append(
            BenchmarkJob(
                job_id=job_id,
                domain=domain,
                source_prompt=source_prompt,
                cases=cases,
            )
        )
    return suite_id, tuple(jobs)


def load_benchmark(path: Path) -> tuple[str, tuple[BenchmarkJob, ...]]:
    data = load_strict_json_object(
        path,
        label="benchmark definition",
        preserve_decimal=True,
    )
    return _benchmark_from_data(data)


def _benchmark_catalog_from_data(
    path: Path,
    data: dict[str, Any],
) -> tuple[str, tuple[BenchmarkJob, ...]]:
    _require_exact_fields(
        data,
        required=CATALOG_ROOT_FIELDS,
        allowed=CATALOG_ROOT_FIELDS,
        label="benchmark catalog",
    )
    _validate_schema_version(data, label="benchmark catalog")
    suite_id = _require_string(data["suite_id"], label="benchmark suite_id")
    includes = data["includes"]
    if not isinstance(includes, list):
        raise ValueError("benchmark catalog includes must be an array.")
    root = path.resolve().parent
    jobs: list[BenchmarkJob] = []
    included_paths: set[Path] = set()
    for include_index, relative in enumerate(includes):
        if not isinstance(relative, str) or not relative:
            raise ValueError(
                f"Benchmark include {include_index} must be a non-empty string."
            )
        try:
            included = (root / relative).resolve()
        except (OSError, ValueError) as exc:
            raise ValueError(f"Invalid benchmark include path: {relative!r}") from exc
        try:
            included.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"Benchmark include escapes catalog root: {relative}") from exc
        if included in included_paths:
            raise ValueError(f"Duplicate benchmark include: {relative}")
        included_paths.add(included)
        _, included_jobs = load_benchmark(included)
        jobs.extend(included_jobs)
    return suite_id, tuple(jobs)


def load_benchmark_catalog(path: Path) -> tuple[str, tuple[BenchmarkJob, ...]]:
    data = load_strict_json_object(
        path,
        label="benchmark catalog",
        preserve_decimal=True,
    )
    return _benchmark_catalog_from_data(path, data)


def load_benchmark_definition(path: Path) -> tuple[str, tuple[BenchmarkJob, ...]]:
    data = load_strict_json_object(
        path,
        label="benchmark definition",
        preserve_decimal=True,
    )
    fields = set(data)
    if fields == CATALOG_ROOT_FIELDS:
        return _benchmark_catalog_from_data(path, data)
    if fields == BENCHMARK_ROOT_FIELDS:
        return _benchmark_from_data(data)
    raise ValueError(
        "benchmark definition root fields must exactly describe either a "
        "benchmark or a catalog."
    )


def validate_benchmark(
    suite_id: str,
    jobs: tuple[BenchmarkJob, ...],
    *,
    required_domains: set[str] | None = None,
    minimum_cases_per_domain: int = 1,
) -> list[str]:
    failures: list[str] = []
    if not suite_id.strip():
        failures.append("suite_id must not be empty")
    if not jobs:
        return [*failures, "benchmark must contain jobs"]

    job_ids: set[str] = set()
    case_ids: set[str] = set()
    domain_counts: Counter[str] = Counter()
    for job in jobs:
        if not isinstance(job.domain, str) or not DOMAIN_ID_RE.fullmatch(job.domain):
            failures.append(
                f"{job.job_id}: domain must be a safe identifier containing only "
                "letters, numbers, underscores, or hyphens"
            )
        if job.job_id in job_ids:
            failures.append(f"duplicate job id: {job.job_id}")
        job_ids.add(job.job_id)
        if not job.source_prompt.strip():
            failures.append(f"{job.job_id}: source_prompt must not be empty")
        if not job.cases:
            failures.append(f"{job.job_id}: must contain cases")
        for case in job.cases:
            try:
                case.validate()
            except ValueError as exc:
                failures.append(str(exc))
            if case.case_id in case_ids:
                failures.append(f"duplicate case id: {case.case_id}")
            case_ids.add(case.case_id)
            if case.domain != job.domain:
                failures.append(f"{case.case_id}: domain does not match job")
            if len(case.input_text.strip()) < 24:
                failures.append(f"{case.case_id}: input is too shallow")
            marker_groups = CONCRETE_PAYLOAD_MARKERS.get(job.domain)
            if marker_groups is not None:
                if (
                    len(case.input_text.strip())
                    < MINIMUM_CONCRETE_PAYLOAD_CHARACTERS
                ):
                    failures.append(
                        f"{case.case_id}: concrete payload is too short"
                    )
                for alternatives in marker_groups:
                    if not any(
                        marker in case.input_text for marker in alternatives
                    ):
                        failures.append(
                            f"{case.case_id}: concrete payload missing one of "
                            f"{alternatives}"
                        )
            if "requires_source_payload" in case.tags:
                if (
                    len(case.input_text.strip())
                    < MINIMUM_CONCRETE_PAYLOAD_CHARACTERS
                ):
                    failures.append(
                        f"{case.case_id}: source payload is too short"
                    )
                if not any(
                    marker in case.input_text
                    for marker in SOURCE_PAYLOAD_MARKERS
                ):
                    failures.append(
                        f"{case.case_id}: source payload marker is missing"
                    )
            if len(case.rubric) < 3:
                failures.append(f"{case.case_id}: rubric needs at least three criteria")
            domain_counts[job.domain] += 1

    if required_domains is not None:
        missing = required_domains - set(domain_counts)
        if missing:
            failures.append(f"missing domains: {sorted(missing)}")
        for domain in required_domains:
            if domain_counts[domain] < minimum_cases_per_domain:
                failures.append(
                    f"{domain}: fewer than {minimum_cases_per_domain} cases"
                )
    return failures


def group_jobs_by_domain(
    jobs: tuple[BenchmarkJob, ...],
) -> dict[str, BenchmarkJob]:
    grouped: dict[str, list[BenchmarkJob]] = {}
    for job in jobs:
        grouped.setdefault(job.domain, []).append(job)
    result: dict[str, BenchmarkJob] = {}
    for domain, domain_jobs in grouped.items():
        prompts = {job.source_prompt for job in domain_jobs}
        if len(prompts) != 1:
            raise ValueError(
                f"Domain {domain!r} has multiple source Prompts and cannot be "
                "evaluated as one matched suite."
            )
        cases = tuple(case for job in domain_jobs for case in job.cases)
        result[domain] = BenchmarkJob(
            job_id=f"{domain}-combined",
            domain=domain,
            source_prompt=domain_jobs[0].source_prompt,
            cases=cases,
        )
    return result
