"""Evidence-backed stable-release readiness assessment."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .hashing import hash_payload


SCHEMA_VERSION = "1.0.0"
CUSTOM_EVIDENCE_KINDS = {
    "operational_verification",
    "code_execution",
    "image_review",
    "expert_review_coverage",
    "independent_reproduction",
    "defect_register",
    "claims_audit",
}
REQUIREMENTS = (
    ("R01", "Release validation and behavior tests"),
    ("R02", "End-to-end CLI, API, service, package, and documentation"),
    ("R03", "Real 60-case coverage across all 12 domains"),
    ("R04", "Cross-domain improvement and zero-regression quality gate"),
    ("R05", "Executable verification for software cases"),
    ("R06", "Actual image generation and qualified visual review"),
    ("R07", "Blind independent expert human review"),
    ("R08", "Independent reproduction on three machines"),
    ("R09", "P0 and P1 defect closure"),
    ("R10", "Evidence-bound public claims"),
)


def build_evidence_report(
    *,
    kind: str,
    report_id: str,
    facts: dict[str, Any],
    provenance: dict[str, Any],
    limitations: list[str],
) -> dict[str, Any]:
    if kind not in CUSTOM_EVIDENCE_KINDS:
        raise ValueError(f"Unsupported readiness evidence kind: {kind}")
    if not report_id.strip():
        raise ValueError("Evidence report_id must not be empty.")
    if not limitations:
        raise ValueError("Evidence limitations must not be empty.")
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "report_id": report_id,
        "kind": kind,
        "facts": facts,
        "provenance": provenance,
        "limitations": limitations,
    }
    report["evidence_sha256"] = hash_payload(report, "evidence_sha256")
    return report


def build_readiness_manifest(
    artifacts: list[dict[str, str]],
) -> dict[str, Any]:
    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "artifacts": artifacts,
    }
    manifest["manifest_sha256"] = hash_payload(manifest, "manifest_sha256")
    return manifest


def _contained_path(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Evidence path escapes readiness root: {relative}") from exc
    return candidate


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _internal_hash_valid(kind: str, payload: dict[str, Any]) -> bool:
    if kind == "benchmark_summary":
        field = "summary_sha256"
    elif kind == "human_review":
        field = "human_review_sha256"
    else:
        field = "evidence_sha256"
        if payload.get("kind") != kind:
            return False
    value = payload.get(field)
    return isinstance(value, str) and value == hash_payload(payload, field)


def _load_artifacts(
    manifest: dict[str, Any],
    *,
    root: Path,
) -> tuple[dict[str, list[tuple[str, dict[str, Any]]]], list[str]]:
    import json

    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Unsupported readiness manifest schema.")
    if manifest.get("manifest_sha256") != hash_payload(
        manifest,
        "manifest_sha256",
    ):
        raise ValueError("Readiness manifest hash mismatch.")
    specs = manifest.get("artifacts")
    if not isinstance(specs, list):
        raise ValueError("Readiness manifest artifacts must be an array.")

    loaded: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    errors: list[str] = []
    allowed = CUSTOM_EVIDENCE_KINDS | {"benchmark_summary", "human_review"}
    for index, spec in enumerate(specs):
        if not isinstance(spec, dict):
            errors.append(f"artifact[{index}] must be an object")
            continue
        kind = spec.get("kind")
        relative = spec.get("path")
        expected_hash = spec.get("sha256")
        if kind not in allowed:
            errors.append(f"artifact[{index}] has unsupported kind: {kind!r}")
            continue
        if not isinstance(relative, str) or not relative:
            errors.append(f"artifact[{index}] has no path")
            continue
        if not isinstance(expected_hash, str) or len(expected_hash) != 64:
            errors.append(f"artifact[{index}] has no valid sha256")
            continue
        try:
            path = _contained_path(root, relative)
            if not path.is_file():
                raise ValueError("file does not exist")
            if _file_sha256(path) != expected_hash:
                raise ValueError("file sha256 mismatch")
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("JSON root is not an object")
            if payload.get("schema_version") != SCHEMA_VERSION:
                raise ValueError("unsupported evidence schema")
            if not _internal_hash_valid(str(kind), payload):
                raise ValueError("internal evidence hash mismatch")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"{kind}:{relative}: {exc}")
            continue
        loaded.setdefault(str(kind), []).append((relative, payload))
    return loaded, errors


def _requirement(
    requirement_id: str,
    title: str,
    *,
    relevant_count: int,
    evidence: list[str],
    failures: list[str],
    invalid_evidence: bool = False,
) -> dict[str, Any]:
    if invalid_evidence:
        status = "failed"
    elif relevant_count == 0:
        status = "missing"
    elif failures:
        status = "partial"
    else:
        status = "passed"
    return {
        "id": requirement_id,
        "title": title,
        "status": status,
        "evidence": sorted(evidence),
        "failures": failures,
    }


def _custom(
    artifacts: dict[str, list[tuple[str, dict[str, Any]]]],
    kind: str,
) -> list[tuple[str, dict[str, Any]]]:
    return artifacts.get(kind, [])


def _singleton_facts(
    artifacts: dict[str, list[tuple[str, dict[str, Any]]]],
    kind: str,
) -> tuple[list[str], dict[str, Any] | None, list[str]]:
    records = _custom(artifacts, kind)
    evidence = [path for path, _ in records]
    if not records:
        return evidence, None, []
    failures = []
    if len(records) != 1:
        failures.append(f"exactly one {kind} report is required")
    return evidence, records[0][1].get("facts", {}), failures


def _all_true(facts: dict[str, Any], names: tuple[str, ...]) -> list[str]:
    return [name for name in names if facts.get(name) is not True]


def assess_readiness(
    manifest: dict[str, Any],
    *,
    root: Path,
) -> dict[str, Any]:
    root = root.resolve()
    artifacts, evidence_errors = _load_artifacts(manifest, root=root)
    invalid_kinds = {
        error.split(":", 1)[0]
        for error in evidence_errors
        if ":" in error
    }
    requirements: list[dict[str, Any]] = []

    operational_evidence, operational, operational_failures = _singleton_facts(
        artifacts,
        "operational_verification",
    )
    release_failures = list(operational_failures)
    if operational is not None:
        release_failures.extend(
            f"{name} is not proven"
            for name in _all_true(
                operational,
                ("behavior_tests_passed", "release_validator_passed"),
            )
        )
    requirements.append(
        _requirement(
            *REQUIREMENTS[0],
            relevant_count=1 if operational is not None else 0,
            evidence=operational_evidence,
            failures=release_failures,
            invalid_evidence="operational_verification" in invalid_kinds,
        )
    )

    surface_failures = list(operational_failures)
    if operational is not None:
        surface_failures.extend(
            f"{name} is not proven"
            for name in _all_true(
                operational,
                (
                    "cli_passed",
                    "api_passed",
                    "service_passed",
                    "package_install_passed",
                    "documentation_verified",
                ),
            )
        )
    requirements.append(
        _requirement(
            *REQUIREMENTS[1],
            relevant_count=1 if operational is not None else 0,
            evidence=operational_evidence,
            failures=surface_failures,
            invalid_evidence="operational_verification" in invalid_kinds,
        )
    )

    benchmark_records = artifacts.get("benchmark_summary", [])
    benchmark_evidence = [path for path, _ in benchmark_records]
    benchmark_failures: list[str] = []
    benchmark = benchmark_records[0][1] if benchmark_records else None
    if len(benchmark_records) > 1:
        benchmark_failures.append("exactly one release benchmark summary is required")
    if benchmark is not None:
        if benchmark.get("domain_count", 0) < 12:
            benchmark_failures.append("fewer than 12 domains were completed")
        if benchmark.get("case_count", 0) < 60:
            benchmark_failures.append("fewer than 60 cases were completed")
        if len(benchmark.get("completed_domains", [])) < 12:
            benchmark_failures.append("completed domain list has fewer than 12 domains")
        usage = benchmark.get("usage", {})
        if not isinstance(usage, dict) or usage.get("actual_model_calls", 0) <= 0:
            benchmark_failures.append("no real model calls are recorded")
    requirements.append(
        _requirement(
            *REQUIREMENTS[2],
            relevant_count=len(benchmark_records),
            evidence=benchmark_evidence,
            failures=benchmark_failures,
            invalid_evidence="benchmark_summary" in invalid_kinds,
        )
    )

    quality_failures: list[str] = []
    if benchmark is not None:
        if benchmark.get("aggregate_gate_passed") is not True:
            quality_failures.append("aggregate improvement gate did not pass")
        if benchmark.get("all_domains_pass") is not True:
            quality_failures.append("at least one domain gate did not pass")
        if benchmark.get("net_improvement", 0.0) < 0.10:
            quality_failures.append("aggregate net improvement is below 10%")
        if benchmark.get("critical_regressions") != 0:
            quality_failures.append("critical regressions are present")
        if benchmark.get("fatal_flaws") != 0:
            quality_failures.append("fatal flaws are present")
        if benchmark.get("optimized_hard_failures") != 0:
            quality_failures.append("optimized outputs fail authoritative hard checks")
    requirements.append(
        _requirement(
            *REQUIREMENTS[3],
            relevant_count=len(benchmark_records),
            evidence=benchmark_evidence,
            failures=quality_failures,
            invalid_evidence="benchmark_summary" in invalid_kinds,
        )
    )

    code_evidence, code, code_failures = _singleton_facts(
        artifacts,
        "code_execution",
    )
    if code is not None:
        eligible = int(code.get("eligible_cases", 0))
        if eligible < 5:
            code_failures.append("fewer than five software cases are eligible")
        if int(code.get("executed_cases", 0)) != eligible:
            code_failures.append("not all eligible software cases were executed")
        if int(code.get("passed_cases", 0)) != eligible:
            code_failures.append("not all executed software cases passed")
        if code.get("sandboxed") is not True:
            code_failures.append("sandboxed execution is not proven")
        executable = int(code.get("executable_cases", 0))
        if executable < 4:
            code_failures.append("fewer than four software cases are executable")
        if int(code.get("sandboxed_cases", 0)) != executable:
            code_failures.append("not all executable software cases were sandboxed")
        sandbox = code.get("sandbox")
        if not isinstance(sandbox, dict):
            code_failures.append("sandbox evidence is missing")
        else:
            if sandbox.get("backend") != "docker":
                code_failures.append("Docker sandbox backend is not proven")
            if sandbox.get("policy_verified") is not True:
                code_failures.append("sandbox runtime policy is not verified")
            if sandbox.get("isolation_probe_passed") is not True:
                code_failures.append("sandbox isolation probe did not pass")
            isolation_facts = sandbox.get("isolation_facts")
            required_isolation_facts = {
                "network_blocked",
                "root_read_only",
                "tmp_writable",
                "non_root",
            }
            if (
                not isinstance(isolation_facts, dict)
                or any(
                    isolation_facts.get(name) is not True
                    for name in required_isolation_facts
                )
            ):
                code_failures.append("sandbox isolation facts are incomplete")
            if sandbox.get("resource_limits_verified") is not True:
                code_failures.append("sandbox resource limits are not verified")
            image_reference = sandbox.get("image_reference")
            image_id = sandbox.get("image_id")
            if (
                not isinstance(image_reference, str)
                or "@sha256:" not in image_reference
            ):
                code_failures.append("sandbox image reference is not digest-pinned")
            if (
                not isinstance(image_id, str)
                or not image_id.startswith("sha256:")
                or len(image_id) != 71
            ):
                code_failures.append("sandbox image id is invalid")
    requirements.append(
        _requirement(
            *REQUIREMENTS[4],
            relevant_count=1 if code is not None else 0,
            evidence=code_evidence,
            failures=code_failures,
            invalid_evidence="code_execution" in invalid_kinds,
        )
    )

    image_evidence, image, image_failures = _singleton_facts(
        artifacts,
        "image_review",
    )
    if image is not None:
        eligible = int(image.get("eligible_cases", 0))
        if eligible < 5:
            image_failures.append("fewer than five image cases are eligible")
        if int(image.get("generated_cases", 0)) != eligible:
            image_failures.append("not all image cases generated actual images")
        if int(image.get("reviewed_cases", 0)) != eligible:
            image_failures.append("not all generated images were reviewed")
        if int(image.get("qualified_reviewers", 0)) < 3:
            image_failures.append("fewer than three qualified visual reviewers")
        if image.get("blind") is not True:
            image_failures.append("visual review was not blind")
        if image.get("asset_integrity_verified") is not True:
            image_failures.append("image asset integrity is not verified")
        if image.get("review_coverage_verified") is not True:
            image_failures.append("image review coverage is not verified")
        if image.get("unresolved_cases"):
            image_failures.append("visual-review disagreements remain unresolved")
        if not image_failures:
            from .image_review import validate_image_evidence_assets

            image_failures.extend(
                validate_image_evidence_assets(image, root=root)
            )
    requirements.append(
        _requirement(
            *REQUIREMENTS[5],
            relevant_count=1 if image is not None else 0,
            evidence=image_evidence,
            failures=image_failures,
            invalid_evidence="image_review" in invalid_kinds,
        )
    )

    human_records = artifacts.get("human_review", [])
    human_evidence = [path for path, _ in human_records]
    expert_evidence, expert, expert_failures = _singleton_facts(
        artifacts,
        "expert_review_coverage",
    )
    expert_failures = list(expert_failures)
    human = human_records[0][1] if human_records else None
    if len(human_records) > 1:
        expert_failures.append("exactly one human review report is required")
    if human is not None:
        if human.get("e4_ready") is not True:
            expert_failures.append("human review is not E4-ready")
        if int(human.get("reviewer_count", 0)) < 3:
            expert_failures.append("fewer than three independent reviewers")
        if int(human.get("reviewed_case_count", 0)) < 24:
            expert_failures.append("fewer than 24 stratified cases were reviewed")
        if human.get("unresolved_cases"):
            expert_failures.append("human-review disagreements remain unresolved")
    if expert is not None:
        required_domains = {
            "creative_design",
            "research_synthesis",
            "business_strategy",
        }
        observed = set(expert.get("domains", []))
        if not required_domains.issubset(observed):
            expert_failures.append("required expert-review domains are incomplete")
        if expert.get("blind") is not True:
            expert_failures.append("expert review was not blind")
        if int(expert.get("qualified_reviewers", 0)) < 3:
            expert_failures.append("expert qualification coverage is below three")
    relevant_expert = int(human is not None) + int(expert is not None)
    requirements.append(
        _requirement(
            *REQUIREMENTS[6],
            relevant_count=relevant_expert,
            evidence=human_evidence + expert_evidence,
            failures=expert_failures
            + (["human review report is missing"] if human is None and expert else [])
            + (["expert coverage report is missing"] if expert is None and human else []),
            invalid_evidence=bool(
                {"human_review", "expert_review_coverage"} & invalid_kinds
            ),
        )
    )

    reproduction_records = _custom(artifacts, "independent_reproduction")
    reproduction_evidence = [path for path, _ in reproduction_records]
    reproduction_failures: list[str] = []
    machines: set[str] = set()
    operators: set[str] = set()
    for _, record in reproduction_records:
        facts = record.get("facts", {})
        machines.add(str(facts.get("machine_id_hash", "")))
        operators.add(str(facts.get("operator_id_hash", "")))
        reproduction_failures.extend(
            f"{record.get('report_id')}: {name} is not proven"
            for name in _all_true(facts, ("install_passed", "replay_passed"))
        )
    machines.discard("")
    operators.discard("")
    if reproduction_records and len(machines) < 3:
        reproduction_failures.append("fewer than three independent machines")
    if reproduction_records and len(operators) < 3:
        reproduction_failures.append("fewer than three independent operators")
    requirements.append(
        _requirement(
            *REQUIREMENTS[7],
            relevant_count=len(reproduction_records),
            evidence=reproduction_evidence,
            failures=reproduction_failures,
            invalid_evidence="independent_reproduction" in invalid_kinds,
        )
    )

    defect_evidence, defects, defect_failures = _singleton_facts(
        artifacts,
        "defect_register",
    )
    if defects is not None:
        if defects.get("open_p0") != 0:
            defect_failures.append("open P0 defects remain")
        if defects.get("open_p1") != 0:
            defect_failures.append("open P1 defects remain")
        if defects.get("triage_complete") is not True:
            defect_failures.append("defect triage is incomplete")
    requirements.append(
        _requirement(
            *REQUIREMENTS[8],
            relevant_count=1 if defects is not None else 0,
            evidence=defect_evidence,
            failures=defect_failures,
            invalid_evidence="defect_register" in invalid_kinds,
        )
    )

    claim_evidence, claims, claim_failures = _singleton_facts(
        artifacts,
        "claims_audit",
    )
    if claims is not None:
        if claims.get("unsupported_claims") != 0:
            claim_failures.append("unsupported public claims remain")
        if claims.get("all_claims_artifact_bound") is not True:
            claim_failures.append("not all public claims are artifact-bound")
        if claims.get("documentation_scanned") is not True:
            claim_failures.append("public documentation scan is incomplete")
    requirements.append(
        _requirement(
            *REQUIREMENTS[9],
            relevant_count=1 if claims is not None else 0,
            evidence=claim_evidence,
            failures=claim_failures,
            invalid_evidence="claims_audit" in invalid_kinds,
        )
    )

    passed = sum(item["status"] == "passed" for item in requirements)
    complete = passed == len(REQUIREMENTS) and not evidence_errors
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": "complete" if complete else "incomplete",
        "claim_ceiling": "stable_v1" if complete else "optimized_candidate",
        "completion_metric": "unweighted mandatory gate ratio",
        "passed_requirement_count": passed,
        "requirement_count": len(REQUIREMENTS),
        "completion_ratio": passed / len(REQUIREMENTS),
        "requirements": requirements,
        "evidence_errors": evidence_errors,
    }
    report["readiness_sha256"] = hash_payload(report, "readiness_sha256")
    return report


def validate_readiness_report(report: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    if report.get("schema_version") != SCHEMA_VERSION:
        failures.append("unsupported readiness report schema")
    if report.get("readiness_sha256") != hash_payload(
        report,
        "readiness_sha256",
    ):
        failures.append("readiness report hash mismatch")
    requirements = report.get("requirements")
    if not isinstance(requirements, list):
        return failures + ["requirements must be an array"]
    expected_ids = [item[0] for item in REQUIREMENTS]
    observed_ids = [item.get("id") for item in requirements if isinstance(item, dict)]
    if observed_ids != expected_ids:
        failures.append("readiness requirement ids are incomplete or out of order")
    allowed_statuses = {"passed", "partial", "missing", "failed"}
    if any(
        not isinstance(item, dict) or item.get("status") not in allowed_statuses
        for item in requirements
    ):
        failures.append("readiness requirement status is invalid")
    passed = sum(
        isinstance(item, dict) and item.get("status") == "passed"
        for item in requirements
    )
    if report.get("passed_requirement_count") != passed:
        failures.append("passed requirement count does not match requirements")
    if report.get("requirement_count") != len(REQUIREMENTS):
        failures.append("requirement count does not match the release contract")
    expected_complete = passed == len(REQUIREMENTS) and not report.get(
        "evidence_errors"
    )
    if (report.get("status") == "complete") != expected_complete:
        failures.append("readiness status does not match mandatory gates")
    expected_ceiling = "stable_v1" if expected_complete else "optimized_candidate"
    if report.get("claim_ceiling") != expected_ceiling:
        failures.append("claim ceiling does not match readiness status")
    expected_ratio = passed / len(REQUIREMENTS)
    if report.get("completion_ratio") != expected_ratio:
        failures.append("completion ratio does not match mandatory gates")
    return failures
