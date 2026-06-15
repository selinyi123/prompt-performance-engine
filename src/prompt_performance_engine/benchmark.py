"""Load and validate versioned benchmark definitions."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
SOURCE_PAYLOAD_MARKERS = (
    "EVIDENCE_PACKET:",
    "SOURCE_NOTES:",
    "SOURCE_POLICY:",
)


@dataclass(frozen=True)
class BenchmarkJob:
    job_id: str
    domain: str
    source_prompt: str
    cases: tuple[EvaluationCase, ...]


def case_from_dict(data: dict[str, Any], *, default_domain: str = "generic") -> EvaluationCase:
    return EvaluationCase(
        case_id=str(data["case_id"]),
        input_text=str(data["input_text"]),
        rubric=tuple(data["rubric"]),
        domain=str(data.get("domain", default_domain)),
        difficulty=str(data.get("difficulty", "normal")),
        tags=tuple(data.get("tags", [])),
        required_substrings=tuple(data.get("required_substrings", [])),
        forbidden_substrings=tuple(data.get("forbidden_substrings", [])),
        require_json=bool(data.get("require_json", False)),
        max_characters=data.get("max_characters"),
    )


def load_benchmark(path: Path) -> tuple[str, tuple[BenchmarkJob, ...]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    suite_id = str(data["suite_id"])
    jobs: list[BenchmarkJob] = []
    for raw_job in data["jobs"]:
        domain = str(raw_job["domain"])
        cases = tuple(
            case_from_dict(case, default_domain=domain)
            for case in raw_job["cases"]
        )
        jobs.append(
            BenchmarkJob(
                job_id=str(raw_job["job_id"]),
                domain=domain,
                source_prompt=str(raw_job["source_prompt"]),
                cases=cases,
            )
        )
    return suite_id, tuple(jobs)


def load_benchmark_catalog(path: Path) -> tuple[str, tuple[BenchmarkJob, ...]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    suite_id = str(data["suite_id"])
    root = path.resolve().parent
    jobs: list[BenchmarkJob] = []
    for relative in data["includes"]:
        included = (root / str(relative)).resolve()
        try:
            included.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"Benchmark include escapes catalog root: {relative}") from exc
        _, included_jobs = load_benchmark(included)
        jobs.extend(included_jobs)
    return suite_id, tuple(jobs)


def load_benchmark_definition(path: Path) -> tuple[str, tuple[BenchmarkJob, ...]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if "includes" in data:
        return load_benchmark_catalog(path)
    return load_benchmark(path)


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
