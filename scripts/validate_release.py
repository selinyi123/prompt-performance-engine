#!/usr/bin/env python3
"""Validate the release contract without relying on test counts."""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from prompt_performance_engine.contracts import (  # noqa: E402
    ARTIFACT_SCHEMA_VERSION,
    LEGACY_ARTIFACT_PRODUCER_VERSIONS,
    LEGACY_ARTIFACT_SCHEMA_VERSION,
    PACKAGE_VERSION,
    SUPPORTED_ARTIFACT_PRODUCER_VERSIONS,
    load_strict_json_object,
)
from prompt_performance_engine.frontier_contracts import (  # noqa: E402
    FRONTIER_CONTRACT_SCHEMA_VERSION,
)
from prompt_performance_engine.audit import audit_prompt  # noqa: E402
from prompt_performance_engine.benchmark import (  # noqa: E402
    load_benchmark_definition,
    validate_benchmark,
)
from prompt_performance_engine.profiles import load_profiles  # noqa: E402
from prompt_performance_engine.validation import find_mojibake  # noqa: E402


FRONTIER_NORMATIVE_DOCS = {
    "QUALITY-GATE-SPEC.md",
    "FRONTIER-EVIDENCE-CAMPAIGN.md",
}
FRONTIER_CONTRACT_TITLES = {
    "README.md": "# Prompt Performance Engine",
    "PRODUCT-SPEC.md": "# Product Specification",
    "ACCEPTANCE-CRITERIA.md": "# Acceptance Criteria",
    "QUALITY-GATE-SPEC.md": "# Frontier Quality Gate Specification",
    "FRONTIER-EVIDENCE-CAMPAIGN.md": "# Frontier Evidence Campaign",
}
FRONTIER_CONTRACT_STATUS = (
    "## Frontier Contract Status",
    "",
    f"frontier_contract_package: {PACKAGE_VERSION}",
    "frontier_machine_claim: not_evaluable",
    "frontier_target_claim: top_tier_scoped",
    "frontier_stable_gate: R01-R10",
    "frontier_design_gate_sufficient_for_claim: false",
    "frontier_quality_spec: QUALITY-GATE-SPEC.md",
    "frontier_campaign: FRONTIER-EVIDENCE-CAMPAIGN.md",
    "frontier_contract_implemented: true",
    "frontier_preflight_contract_implemented: true",
    "frontier_execution_host_implemented: true",
    "frontier_offline_replay_implemented: true",
    "frontier_independent_authority_executed: false",
    "frontier_external_campaign_executed: false",
)

SCHEMA_FAMILY_REGISTRY = {
    "stable": {
        "version": ARTIFACT_SCHEMA_VERSION,
        "id_template": (
            "https://local.invalid/prompt-performance/{version}/{filename}"
        ),
    },
    "frontier": {
        "version": FRONTIER_CONTRACT_SCHEMA_VERSION,
        "id_template": (
            "urn:prompt-performance-engine:schema:frontier:{contract}:{version}"
        ),
    },
}

# This inventory is a release contract, not a discovery result. Adding,
# removing, or renaming a schema requires an intentional inventory update in
# the same package release. Keeping the package version literal makes a future
# version bump fail closed until that review happens.
SCHEMA_INVENTORY_PACKAGE_VERSION = "0.4.0"
REGISTERED_SCHEMA_FILENAMES = frozenset(
    {
        "benchmark-replicate-report.schema.json",
        "code-execution-plan.schema.json",
        "evaluation-recorded-run.schema.json",
        "frontier-call-attempt.schema.json",
        "frontier-campaign-plan.schema.json",
        "frontier-claim-inventory.schema.json",
        "frontier-evidence-envelope.schema.json",
        "frontier-execution-bundle.schema.json",
        "frontier-execution-manifest.schema.json",
        "frontier-execution-plan.schema.json",
        "frontier-preflight-report.schema.json",
        "frontier-release-policy.schema.json",
        "frontier-replay-report.schema.json",
        "frontier-report.schema.json",
        "frontier-reproduction-plan.schema.json",
        "frontier-reproduction-set.schema.json",
        "frontier-source-commitment.schema.json",
        "human-review-key.schema.json",
        "human-review-packet.schema.json",
        "human-review-plan.schema.json",
        "human-review-report.schema.json",
        "human-review-submission.schema.json",
        "image-generation-manifest.schema.json",
        "optimization-artifact.schema.json",
        "optimization-request.schema.json",
        "readiness-evidence.schema.json",
        "readiness-manifest.schema.json",
        "readiness-report.schema.json",
        "tool-permission-manifest.schema.json",
        "visual-review-key.schema.json",
        "visual-review-packet.schema.json",
        "visual-review-plan.schema.json",
        "visual-review-submission.schema.json",
        "visual-reviewer-profile.schema.json",
    }
)


def schema_family_name(filename: str) -> str:
    """Return the registered schema family for a release schema filename."""

    return "frontier" if filename.startswith("frontier-") else "stable"


def expected_schema_id(filename: str) -> str:
    """Build the exact stable identifier for a release schema."""

    family_name = schema_family_name(filename)
    family = SCHEMA_FAMILY_REGISTRY[family_name]
    contract = filename.removesuffix(".schema.json").removeprefix("frontier-")
    return family["id_template"].format(
        contract=contract,
        filename=filename,
        version=family["version"],
    )


REQUIRED_SCHEMA_FILES = frozenset(
    f"schemas/{filename}" for filename in REGISTERED_SCHEMA_FILENAMES
)

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
    "IMAGE-REVIEW-PROTOCOL.md",
    "CHANGELOG.md",
    "SECURITY.md",
    "SOFTWARE-SANDBOX.md",
    "MIGRATION.md",
    "prompts/optimizer.md",
    "profiles/domain_profiles.json",
    "adversarial_cases/manifest.json",
    "benchmark/catalog-60.json",
} | FRONTIER_NORMATIVE_DOCS | REQUIRED_SCHEMA_FILES
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


def frontier_document_contract_failures(root: Path = ROOT) -> list[str]:
    """Require an exact, unwrapped status block at the top of frontier docs.

    Exact prefix matching is intentionally narrower than attempting to render a
    permissive Markdown/HTML language. A code span, nested fence, block quote,
    link definition, comment, or hidden HTML container cannot satisfy it.
    """

    failures: list[str] = []
    for relative, title in FRONTIER_CONTRACT_TITLES.items():
        path = root / relative
        if not path.is_file():
            continue
        expected_prefix = (title, "", *FRONTIER_CONTRACT_STATUS)
        document = path.read_text(encoding="utf-8")
        physical_lines = document.replace("\r\n", "\n").replace("\r", "\n")
        actual_prefix = tuple(physical_lines.split("\n")[: len(expected_prefix)])
        if actual_prefix != expected_prefix:
            failures.append(
                f"{relative} missing exact top-level frontier contract status block"
            )
    return failures


def schema_inventory_failures(root: Path = ROOT) -> list[str]:
    """Return failures for drift from the versioned release schema inventory."""

    failures: list[str] = []
    if SCHEMA_INVENTORY_PACKAGE_VERSION != PACKAGE_VERSION:
        failures.append(
            "schema inventory package version "
            f"{SCHEMA_INVENTORY_PACKAGE_VERSION} does not match runtime package "
            f"version {PACKAGE_VERSION}"
        )
    schema_root = root / "schemas"
    actual = {
        path.name
        for path in schema_root.glob("*.json")
        if path.is_file()
    }
    for filename in sorted(REGISTERED_SCHEMA_FILENAMES - actual):
        failures.append(f"missing registered schema file: {filename}")
    for filename in sorted(actual - REGISTERED_SCHEMA_FILENAMES):
        failures.append(f"unregistered schema file: {filename}")
    return failures


def load_registered_schemas(
    root: Path = ROOT,
) -> tuple[dict[str, dict], list[str]]:
    """Load the registered schema set without consulting unregistered files."""

    schemas: dict[str, dict] = {}
    failures: list[str] = []
    for filename in sorted(REGISTERED_SCHEMA_FILENAMES):
        path = root / "schemas" / filename
        if not path.is_file():
            continue
        try:
            schemas[filename] = load_strict_json_object(
                path,
                label=f"JSON Schema {filename}",
            )
        except ValueError as exc:
            failures.append(str(exc))
    return schemas, failures


def _walk_json_objects(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_json_objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json_objects(child)


def _json_pointer_exists(document, fragment: str) -> bool:
    """Return whether an RFC 6901 URI fragment points into the document."""

    if fragment == "":
        return True
    decoded = unquote(fragment)
    if not decoded.startswith("/"):
        return False
    target = document
    for raw_part in decoded[1:].split("/"):
        index = 0
        while index < len(raw_part):
            if raw_part[index] == "~" and (
                index + 1 >= len(raw_part) or raw_part[index + 1] not in "01"
            ):
                return False
            index += 2 if raw_part[index] == "~" else 1
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if isinstance(target, dict):
            if part not in target:
                return False
            target = target[part]
        elif isinstance(target, list):
            if (
                not part.isdecimal()
                or (len(part) > 1 and part.startswith("0"))
                or int(part) >= len(target)
            ):
                return False
            target = target[int(part)]
        else:
            return False
    return True


def schema_reference_failures(
    root: Path = ROOT,
    *,
    schemas: dict[str, dict] | None = None,
) -> list[str]:
    """Validate every nested local and cross-schema JSON Schema reference."""

    failures: list[str] = []
    if schemas is None:
        schemas, load_failures = load_registered_schemas(root)
        failures.extend(load_failures)

    schemas_by_id = {
        schema["$id"]: schema
        for filename, schema in schemas.items()
        if schema.get("$id") == expected_schema_id(filename)
    }
    for filename, schema in sorted(schemas.items()):
        for node in _walk_json_objects(schema):
            if "$ref" not in node:
                continue
            reference = node["$ref"]
            if not isinstance(reference, str) or not reference:
                failures.append(f"{filename}: $ref must be a non-empty string")
                continue
            if reference.startswith("#"):
                target_schema = schema
                fragment = reference[1:]
            else:
                target_id, separator, fragment = reference.partition("#")
                target_schema = schemas_by_id.get(target_id)
                if target_schema is None:
                    failures.append(
                        f"{filename}: external $ref {reference!r} does not match "
                        "a registered absolute schema $id"
                    )
                    continue
                if not separator:
                    fragment = ""
            if not _json_pointer_exists(target_schema, fragment):
                failures.append(
                    f"{filename}: unresolved schema $ref {reference!r}"
                )
    return failures


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    failures: list[str] = []
    for relative in sorted(REQUIRED_FILES - REQUIRED_SCHEMA_FILES):
        if not (ROOT / relative).is_file():
            failures.append(f"missing required file: {relative}")
    failures.extend(frontier_document_contract_failures())
    failures.extend(schema_inventory_failures())

    source_version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project_version = pyproject.get("project", {}).get("version")
    if PACKAGE_VERSION != source_version:
        failures.append("runtime package version does not match VERSION")
    if project_version != source_version:
        failures.append("pyproject version does not match VERSION")
    expected_producer_versions = LEGACY_ARTIFACT_PRODUCER_VERSIONS | {
        PACKAGE_VERSION
    }
    if SUPPORTED_ARTIFACT_PRODUCER_VERSIONS != expected_producer_versions:
        failures.append("runtime artifact producer compatibility set mismatch")
    if LEGACY_ARTIFACT_SCHEMA_VERSION == ARTIFACT_SCHEMA_VERSION:
        failures.append("legacy and current artifact schema versions must differ")

    schemas, schema_load_failures = load_registered_schemas()
    failures.extend(schema_load_failures)
    for filename, schema in sorted(schemas.items()):
        if schema.get("$id") != expected_schema_id(filename):
            failures.append(f"schema id version mismatch: {filename}")
        package_contract = schema.get("properties", {}).get("package_version")
        if package_contract:
            if package_contract != {"const": PACKAGE_VERSION}:
                failures.append(
                    f"current artifact producer version contract mismatch: {filename}"
                )
    failures.extend(schema_reference_failures(schemas=schemas))

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
        "exactly one JSON object",
        "JSON string escaping is the transport boundary",
    ):
        if marker not in prompt:
            failures.append(f"optimizer Prompt missing contract marker: {marker}")

    adversarial_root = ROOT / "adversarial_cases"
    manifest = load_strict_json_object(
        adversarial_root / "manifest.json",
        label="adversarial manifest",
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
        f"frontier schema {FRONTIER_CONTRACT_SCHEMA_VERSION}, "
        f"{len(profiles) - 1} specialized domain profiles."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
