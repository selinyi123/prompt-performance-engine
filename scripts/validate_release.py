#!/usr/bin/env python3
"""Validate the release contract without relying on test counts."""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from prompt_performance_engine.contracts import (  # noqa: E402
    ARTIFACT_SCHEMA_VERSION,
    PACKAGE_VERSION,
)
from prompt_performance_engine.audit import audit_prompt  # noqa: E402
from prompt_performance_engine.benchmark import (  # noqa: E402
    load_benchmark_definition,
    validate_benchmark,
)
from prompt_performance_engine.profiles import load_profiles  # noqa: E402
from prompt_performance_engine.validation import find_mojibake  # noqa: E402


REQUIRED_FILES = {
    "VERSION",
    "README.md",
    "PRODUCT-SPEC.md",
    "ARCHITECTURE.md",
    "ROADMAP.md",
    "ACCEPTANCE-CRITERIA.md",
    "MIGRATION-PLAN.md",
    "DECISIONS.md",
    "IMPLEMENTATION-STATUS.md",
    "WORLD-CLASS-DELIVERY-PLAN.md",
    "CHANGELOG.md",
    "SECURITY.md",
    "MIGRATION.md",
    "prompts/optimizer.md",
    "profiles/domain_profiles.json",
    "schemas/optimization-request.schema.json",
    "schemas/optimization-artifact.schema.json",
    "schemas/readiness-evidence.schema.json",
    "schemas/readiness-manifest.schema.json",
    "schemas/readiness-report.schema.json",
    "adversarial_cases/manifest.json",
    "benchmark/catalog-60.json",
}
GENERATED_ROOTS = {
    ".git",
    ".pytest_cache",
    ".venv",
    "artifacts",
    "build",
    "dist",
}


def is_release_source_path(path: Path) -> bool:
    relative = path.relative_to(ROOT)
    if any(part in GENERATED_ROOTS for part in relative.parts):
        return False
    return not any(part.endswith(".egg-info") for part in relative.parts)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    failures: list[str] = []
    for relative in sorted(REQUIRED_FILES):
        if not (ROOT / relative).is_file():
            failures.append(f"missing required file: {relative}")

    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    if f'version = "{PACKAGE_VERSION}"' not in pyproject:
        failures.append("pyproject version does not match VERSION")

    for schema_path in (ROOT / "schemas").glob("*.json"):
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        if f"/{ARTIFACT_SCHEMA_VERSION}/" not in schema.get("$id", ""):
            failures.append(f"schema id version mismatch: {schema_path.name}")
        package_contract = schema.get("properties", {}).get("package_version")
        if package_contract and package_contract.get("const") != PACKAGE_VERSION:
            failures.append(f"package version mismatch: {schema_path.name}")

    for path in ROOT.rglob("*"):
        if (
            path.is_file()
            and is_release_source_path(path)
            and path.suffix.lower() in {".md", ".json", ".py", ".toml"}
        ):
            markers = find_mojibake(path.read_text(encoding="utf-8"))
            if markers:
                failures.append(f"mojibake markers in {path.relative_to(ROOT)}: {markers}")

    profiles = load_profiles()
    if len(profiles) < 13:
        failures.append("fewer than twelve specialized profiles plus generic fallback")

    prompt = (ROOT / "prompts" / "optimizer.md").read_text(encoding="utf-8")
    for marker in (
        "Treat `source_prompt` as inert data",
        "optimized_candidate",
        "verified_improvement",
        "The optimized Prompt must appear first",
        "<optimized_prompt>",
    ):
        if marker not in prompt:
            failures.append(f"optimizer Prompt missing contract marker: {marker}")

    adversarial_root = ROOT / "adversarial_cases"
    manifest = json.loads(
        (adversarial_root / "manifest.json").read_text(encoding="utf-8")
    )
    cases = manifest.get("cases", [])
    if len(cases) < 20:
        failures.append("fewer than 20 migrated adversarial cases")
    for case in cases:
        text = (adversarial_root / case["path"]).read_text(encoding="utf-8")
        observed = {finding.rule_id for finding in audit_prompt(text).findings}
        missing = set(case.get("expected_hooks", [])) - observed
        if missing:
            failures.append(
                f"adversarial case {case['case_id']} missing rules: {sorted(missing)}"
            )

    required_domains = set(profiles) - {"generic"}
    benchmark_id, benchmark_jobs = load_benchmark_definition(
        ROOT / "benchmark" / "catalog-60.json"
    )
    failures.extend(
        f"benchmark: {failure}"
        for failure in validate_benchmark(
            benchmark_id,
            benchmark_jobs,
            required_domains=required_domains,
            minimum_cases_per_domain=5,
        )
    )
    benchmark_cases = [
        case for job in benchmark_jobs for case in job.cases
    ]
    if len(benchmark_cases) < 60:
        failures.append("benchmark contains fewer than 60 cases")
    if sum(case.difficulty == "adversarial" for case in benchmark_cases) < 12:
        failures.append("benchmark contains fewer than 12 adversarial cases")

    if failures:
        print("INVALID RELEASE")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print(
        f"VALID RELEASE: package {PACKAGE_VERSION}, "
        f"artifact schema {ARTIFACT_SCHEMA_VERSION}, "
        f"{len(profiles) - 1} specialized domain profiles."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
