"""Tamper-evident aggregation for repeated benchmark runs."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any, Sequence

from .evaluation import validate_evaluation
from .evidence import Evidence, infer_evidence
from .hashing import hash_payload, sha256_json
from .validation import validate_artifact

MINIMUM_REPLICATES = 3
RELEASE_DOMAIN_COUNT = 12
RELEASE_CASE_COUNT = 60
REPLICATE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
OUTCOMES = ("win", "tie", "loss")


def _evidence_payload(evidence: Evidence) -> dict[str, Any]:
    payload = asdict(evidence)
    payload["limitations"] = list(evidence.limitations)
    return payload


def validate_replicate_id(
    value: Any,
    *,
    required: bool = True,
) -> str | None:
    """Validate and return a stable repeated-run identifier."""
    if value is None:
        if required:
            raise ValueError("replicate_id is required for repeated-run aggregation.")
        return None
    if not isinstance(value, str) or REPLICATE_ID_RE.fullmatch(value) is None:
        raise ValueError(
            "replicate_id must be 1-64 characters, start with an alphanumeric "
            "character, and contain only letters, digits, '.', '_', or '-'."
        )
    return value


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot load {label} at {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} at {path} must be a JSON object.")
    return value


def _normalized_configuration(configuration: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in configuration.items() if key != "replicate_id"}


def _expected_summary(
    evaluations: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    case_count = sum(item["case_count"] for item in evaluations.values())
    wins = sum(item["wins"] for item in evaluations.values())
    ties = sum(item["ties"] for item in evaluations.values())
    losses = sum(item["losses"] for item in evaluations.values())
    critical = sum(item["critical_regressions"] for item in evaluations.values())
    fatal = sum(item["fatal_flaws"] for item in evaluations.values())
    hard = sum(item["optimized_hard_failures"] for item in evaluations.values())
    all_domains_pass = bool(evaluations) and all(
        item["gate_passed"] is True for item in evaluations.values()
    )
    net = (wins - losses) / case_count if case_count else 0.0
    aggregate_gate = (
        len(evaluations) == RELEASE_DOMAIN_COUNT
        and all_domains_pass
        and net >= 0.10
        and critical == 0
        and fatal == 0
        and hard == 0
    )
    return {
        "completed_domains": sorted(evaluations),
        "domain_count": len(evaluations),
        "case_count": case_count,
        "wins": wins,
        "ties": ties,
        "losses": losses,
        "net_improvement": net,
        "critical_regressions": critical,
        "fatal_flaws": fatal,
        "optimized_hard_failures": hard,
        "all_domains_pass": all_domains_pass,
        "aggregate_gate_passed": aggregate_gate,
    }


def _load_run(run_directory: Path) -> dict[str, Any]:
    root = run_directory.resolve()
    manifest = _load_json(root / "run-manifest.json", "run manifest")
    if manifest.get("schema_version") != "1.0.0":
        raise ValueError(f"{root}: unsupported run manifest schema.")
    if manifest.get("manifest_sha256") != hash_payload(manifest, "manifest_sha256"):
        raise ValueError(f"{root}: run manifest hash mismatch.")
    configuration = manifest.get("configuration")
    if not isinstance(configuration, dict):
        raise ValueError(f"{root}: run manifest configuration must be an object.")
    replicate_id = validate_replicate_id(configuration.get("replicate_id"))

    summary = _load_json(root / "summary.json", "run summary")
    if summary.get("schema_version") != "1.0.0":
        raise ValueError(f"{root}: unsupported summary schema.")
    if summary.get("summary_sha256") != hash_payload(summary, "summary_sha256"):
        raise ValueError(f"{root}: summary hash mismatch.")
    if summary.get("replicate_id") != replicate_id:
        raise ValueError(f"{root}: summary replicate_id mismatch.")
    if summary.get("run_manifest_sha256") != manifest.get("manifest_sha256"):
        raise ValueError(f"{root}: summary is not bound to its run manifest.")
    for field in ("suite_id", "benchmark_definition_sha256"):
        if summary.get(field) != configuration.get(field):
            raise ValueError(f"{root}: summary {field} mismatch.")

    completed = summary.get("completed_domains")
    if (
        not isinstance(completed, list)
        or not completed
        or not all(isinstance(item, str) and item for item in completed)
    ):
        raise ValueError(f"{root}: completed_domains must be non-empty strings.")
    if len(set(completed)) != len(completed):
        raise ValueError(f"{root}: completed_domains contains duplicates.")
    discovered = sorted(path.parent.name for path in root.glob("*/evaluation.json"))
    if sorted(completed) != discovered:
        raise ValueError(f"{root}: summary/evaluation domain set mismatch.")

    evaluations: dict[str, dict[str, Any]] = {}
    artifacts: dict[str, dict[str, Any]] = {}
    for domain in completed:
        path = (root / domain / "evaluation.json").resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"{root}: unsafe domain path {domain!r}.") from exc
        evaluation = _load_json(path, f"{domain} evaluation")
        failures = validate_evaluation(evaluation)
        if failures:
            raise ValueError(f"{root}: invalid {domain} evaluation: {failures}")
        if evaluation.get("repeated_or_cross_model") is not False:
            raise ValueError(
                f"{root}: component evaluation must be a single-run result."
            )
        if evaluation.get("suite_id") != f"{configuration.get('suite_id')}:{domain}":
            raise ValueError(f"{root}: {domain} evaluation suite_id mismatch.")
        artifact = _load_json(
            root / domain / "optimization.json",
            f"{domain} optimization artifact",
        )
        violations = validate_artifact(artifact)
        if violations:
            raise ValueError(
                f"{root}: invalid {domain} optimization artifact: "
                f"{[item.detail for item in violations]}"
            )
        if artifact.get("domain") != domain:
            raise ValueError(f"{root}: {domain} optimization domain mismatch.")
        selection = artifact.get("runtime", {}).get("selection", {})
        if selection.get("candidate_count") != configuration.get("candidate_count"):
            raise ValueError(f"{root}: {domain} candidate count mismatch.")
        original_hashes = {
            record["original_prompt_sha256"] for record in evaluation["records"]
        }
        optimized_hashes = {
            record["optimized_prompt_sha256"] for record in evaluation["records"]
        }
        expected_optimized_hash = hashlib.sha256(
            artifact["optimized_prompt"].encode("utf-8")
        ).hexdigest()
        if original_hashes != {artifact.get("source_sha256")}:
            raise ValueError(f"{root}: {domain} source Prompt binding mismatch.")
        if optimized_hashes != {expected_optimized_hash}:
            raise ValueError(f"{root}: {domain} optimized Prompt binding mismatch.")
        evaluations[domain] = evaluation
        artifacts[domain] = artifact

    expected = _expected_summary(evaluations)
    for field, value in expected.items():
        if summary.get(field) != value:
            raise ValueError(f"{root}: summary aggregate mismatch: {field}.")
    protocol = summary.get("evaluation_protocol")
    if not isinstance(protocol, dict):
        raise ValueError(f"{root}: summary evaluation_protocol must be an object.")
    if (
        protocol.get("repeated_run") is not False
        or protocol.get("cross_model") is not False
    ):
        raise ValueError(f"{root}: a component summary cannot claim repetition.")
    expected_evidence = _evidence_payload(
        infer_evidence(
            deterministic_checks_passed=True,
            matched_cases=expected["case_count"],
            comparative_improvement_passed=expected["aggregate_gate_passed"],
            repeated_or_cross_model=False,
        )
    )
    if summary.get("evidence") != expected_evidence:
        raise ValueError(f"{root}: summary evidence exceeds its single-run facts.")
    domain_results = summary.get("domain_results")
    if not isinstance(domain_results, dict) or set(domain_results) != set(evaluations):
        raise ValueError(f"{root}: summary domain_results mismatch.")
    fields = (
        "case_count",
        "wins",
        "ties",
        "losses",
        "critical_regressions",
        "fatal_flaws",
        "optimized_hard_failures",
        "gate_passed",
    )
    for domain, evaluation in evaluations.items():
        expected_domain = {field: evaluation[field] for field in fields}
        if domain_results.get(domain) != expected_domain:
            raise ValueError(f"{root}: summary domain result mismatch: {domain}.")
    return {
        "root": root,
        "replicate_id": replicate_id,
        "manifest": manifest,
        "configuration": configuration,
        "summary": summary,
        "evaluations": evaluations,
        "artifacts": artifacts,
    }


def _consensus(counts: dict[str, int]) -> tuple[str, str]:
    maximum = max(counts.values())
    leaders = [outcome for outcome in OUTCOMES if counts[outcome] == maximum]
    if len(leaders) > 1:
        return "tie", "tied_plurality"
    winner = leaders[0]
    if maximum == sum(counts.values()):
        return winner, "unanimous"
    return winner, "plurality"


def _domain_gate_from_observations(
    observations: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    counts = {outcome: 0 for outcome in OUTCOMES}
    for observation in observations:
        counts[observation["outcome"]] += 1
    critical = sum(item["critical_regression"] for item in observations)
    fatal = sum(item["fatal_flaw"] for item in observations)
    hard = sum(item["optimized_hard_failure"] for item in observations)
    gate = (
        len(observations) >= 5
        and counts["win"] > counts["loss"]
        and critical == 0
        and fatal == 0
        and hard == 0
    )
    return {
        "case_count": len(observations),
        "wins": counts["win"],
        "ties": counts["tie"],
        "losses": counts["loss"],
        "critical_regressions": critical,
        "fatal_flaws": fatal,
        "optimized_hard_failures": hard,
        "gate_passed": gate,
    }


def aggregate_benchmark_replicates(
    run_directories: Sequence[Path],
) -> dict[str, Any]:
    """Aggregate compatible single runs into a repeatability report."""
    if len(run_directories) < MINIMUM_REPLICATES:
        raise ValueError(f"At least {MINIMUM_REPLICATES} benchmark runs are required.")
    runs = [_load_run(Path(path)) for path in run_directories]
    replicate_ids = [run["replicate_id"] for run in runs]
    if len(set(replicate_ids)) != len(replicate_ids):
        raise ValueError("replicate_id values must be unique.")

    normalized = [_normalized_configuration(run["configuration"]) for run in runs]
    reference_hash = sha256_json(normalized[0])
    if any(sha256_json(item) != reference_hash for item in normalized[1:]):
        raise ValueError("Benchmark run configurations are not compatible.")
    domains = sorted(runs[0]["evaluations"])
    if any(sorted(run["evaluations"]) != domains for run in runs[1:]):
        raise ValueError("Benchmark runs do not cover identical domain sets.")

    cases: list[dict[str, Any]] = []
    for domain in domains:
        record_maps = [
            {
                record["case_id"]: record
                for record in run["evaluations"][domain]["records"]
            }
            for run in runs
        ]
        case_ids = sorted(record_maps[0])
        if any(sorted(records) != case_ids for records in record_maps[1:]):
            raise ValueError(f"{domain}: benchmark runs have different case sets.")
        for case_id in case_ids:
            records = [record_map[case_id] for record_map in record_maps]
            case_hash = records[0]["case_sha256"]
            if any(record.get("case_sha256") != case_hash for record in records[1:]):
                raise ValueError(f"{domain}/{case_id}: case definition mismatch.")
            observations = []
            counts = {outcome: 0 for outcome in OUTCOMES}
            for run, record in zip(runs, records, strict=True):
                outcome = record["outcome"]
                counts[outcome] += 1
                observations.append(
                    {
                        "replicate_id": run["replicate_id"],
                        "outcome": outcome,
                        "critical_regression": record["critical_regression"] is True,
                        "fatal_flaw": record["fatal_flaw"] is True,
                        "optimized_hard_failure": (
                            record["hard_checks"]["optimized"]["passed"] is not True
                        ),
                    }
                )
            consensus, basis = _consensus(counts)
            cases.append(
                {
                    "domain": domain,
                    "case_id": case_id,
                    "case_sha256": case_hash,
                    "outcome_counts": counts,
                    "observations": observations,
                    "consensus": consensus,
                    "consensus_basis": basis,
                    "agreement_ratio": max(counts.values()) / len(runs),
                    "stable": len([value for value in counts.values() if value]) == 1,
                }
            )

    domain_results: dict[str, dict[str, Any]] = {}
    for domain in domains:
        domain_cases = [item for item in cases if item["domain"] == domain]
        replicate_results = []
        for replicate_id in replicate_ids:
            observations = [
                next(
                    observation
                    for observation in case["observations"]
                    if observation["replicate_id"] == replicate_id
                )
                for case in domain_cases
            ]
            replicate_results.append(
                {
                    "replicate_id": replicate_id,
                    **_domain_gate_from_observations(observations),
                }
            )
        consensus_observations = [
            {
                "outcome": case["consensus"],
                "critical_regression": any(
                    item["critical_regression"] for item in case["observations"]
                ),
                "fatal_flaw": any(item["fatal_flaw"] for item in case["observations"]),
                "optimized_hard_failure": any(
                    item["optimized_hard_failure"] for item in case["observations"]
                ),
            }
            for case in domain_cases
        ]
        consensus_result = _domain_gate_from_observations(consensus_observations)
        individual_pass = all(item["gate_passed"] for item in replicate_results)
        domain_results[domain] = {
            "case_count": len(domain_cases),
            "consensus_wins": consensus_result["wins"],
            "consensus_ties": consensus_result["ties"],
            "consensus_losses": consensus_result["losses"],
            "critical_regressions": sum(
                item["critical_regression"]
                for case in domain_cases
                for item in case["observations"]
            ),
            "fatal_flaws": sum(
                item["fatal_flaw"]
                for case in domain_cases
                for item in case["observations"]
            ),
            "optimized_hard_failures": sum(
                item["optimized_hard_failure"]
                for case in domain_cases
                for item in case["observations"]
            ),
            "all_individual_gates_passed": individual_pass,
            "consensus_gate_passed": consensus_result["gate_passed"],
            "gate_passed": individual_pass and consensus_result["gate_passed"],
            "replicate_results": replicate_results,
        }

    replicate_facts = []
    for run in runs:
        replicate_id = run["replicate_id"]
        results = [
            domain_results[domain]["replicate_results"][
                replicate_ids.index(replicate_id)
            ]
            for domain in domains
        ]
        case_count = sum(item["case_count"] for item in results)
        wins = sum(item["wins"] for item in results)
        ties = sum(item["ties"] for item in results)
        losses = sum(item["losses"] for item in results)
        critical = sum(item["critical_regressions"] for item in results)
        fatal = sum(item["fatal_flaws"] for item in results)
        hard = sum(item["optimized_hard_failures"] for item in results)
        net = (wins - losses) / case_count if case_count else 0.0
        aggregate_gate = (
            len(domains) == RELEASE_DOMAIN_COUNT
            and all(item["gate_passed"] for item in results)
            and net >= 0.10
            and critical == 0
            and fatal == 0
            and hard == 0
        )
        if aggregate_gate != run["summary"]["aggregate_gate_passed"]:
            raise ValueError(f"{replicate_id}: summary release gate mismatch.")
        evaluation_hashes = {
            domain: run["evaluations"][domain]["evaluation_sha256"]
            for domain in domains
        }
        artifact_hashes = {
            domain: run["artifacts"][domain]["artifact_payload_sha256"]
            for domain in domains
        }
        run_fingerprint = sha256_json(
            {
                "optimization_artifact_sha256": artifact_hashes,
                "evaluation_sha256": evaluation_hashes,
            }
        )
        replicate_facts.append(
            {
                "replicate_id": replicate_id,
                "run_manifest_sha256": run["manifest"]["manifest_sha256"],
                "summary_sha256": run["summary"]["summary_sha256"],
                "optimization_artifact_sha256": artifact_hashes,
                "evaluation_sha256": evaluation_hashes,
                "run_fingerprint_sha256": run_fingerprint,
                "domain_count": len(domains),
                "case_count": case_count,
                "wins": wins,
                "ties": ties,
                "losses": losses,
                "critical_regressions": critical,
                "fatal_flaws": fatal,
                "optimized_hard_failures": hard,
                "aggregate_gate_passed": aggregate_gate,
            }
        )
    fingerprints = [item["run_fingerprint_sha256"] for item in replicate_facts]
    if len(set(fingerprints)) != len(fingerprints):
        raise ValueError(
            "Benchmark replicates contain duplicate optimization/evaluation "
            "artifacts and are not independent executions."
        )

    case_count = len(cases)
    consensus_counts = {
        outcome: sum(case["consensus"] == outcome for case in cases)
        for outcome in OUTCOMES
    }
    critical = sum(
        item["critical_regression"] for case in cases for item in case["observations"]
    )
    fatal = sum(item["fatal_flaw"] for case in cases for item in case["observations"])
    hard = sum(
        item["optimized_hard_failure"]
        for case in cases
        for item in case["observations"]
    )
    stable = sum(case["stable"] for case in cases)
    net = (
        (consensus_counts["win"] - consensus_counts["loss"]) / case_count
        if case_count
        else 0.0
    )
    full_coverage = (
        len(domains) == RELEASE_DOMAIN_COUNT and case_count >= RELEASE_CASE_COUNT
    )
    all_domain_gates = bool(domain_results) and all(
        item["gate_passed"] for item in domain_results.values()
    )
    all_release_gates = all(item["aggregate_gate_passed"] for item in replicate_facts)
    release_gate = (
        len(runs) >= MINIMUM_REPLICATES
        and full_coverage
        and all_release_gates
        and all_domain_gates
        and net >= 0.10
        and critical == 0
        and fatal == 0
        and hard == 0
    )
    evidence = infer_evidence(
        deterministic_checks_passed=True,
        matched_cases=case_count * len(runs),
        comparative_improvement_passed=release_gate,
        repeated_or_cross_model=True,
    )
    report: dict[str, Any] = {
        "schema_version": "1.0.0",
        "report_type": "benchmark_replicate_aggregate",
        "suite_id": normalized[0].get("suite_id"),
        "benchmark_definition_sha256": normalized[0].get("benchmark_definition_sha256"),
        "compatibility_configuration": normalized[0],
        "compatibility_sha256": reference_hash,
        "replicate_count": len(runs),
        "replicates": replicate_facts,
        "coverage": {
            "domains": domains,
            "domain_count": len(domains),
            "case_count": case_count,
            "observation_count": case_count * len(runs),
        },
        "cases": cases,
        "domain_results": domain_results,
        "aggregate": {
            "consensus_wins": consensus_counts["win"],
            "consensus_ties": consensus_counts["tie"],
            "consensus_losses": consensus_counts["loss"],
            "net_improvement": net,
            "critical_regressions": critical,
            "fatal_flaws": fatal,
            "optimized_hard_failures": hard,
            "stable_cases": stable,
            "unstable_cases": case_count - stable,
            "exact_agreement_rate": stable / case_count if case_count else 0.0,
            "full_release_coverage": full_coverage,
            "all_individual_release_gates_passed": all_release_gates,
            "all_domain_gates_passed": all_domain_gates,
            "diagnostic_gate_passed": all_domain_gates,
            "release_gate_passed": release_gate,
        },
        "evidence": _evidence_payload(evidence),
    }
    report["report_sha256"] = hash_payload(report, "report_sha256")
    failures = validate_replicate_report(report)
    if failures:
        raise AssertionError(f"Generated replicate report is invalid: {failures}")
    return report


def _validate_replicate_report(report: Any) -> list[str]:
    """Recompute every material derived fact in a replicate report."""
    if not isinstance(report, dict):
        return ["replicate report root must be an object"]
    failures: list[str] = []
    if report.get("schema_version") != "1.0.0":
        failures.append("unsupported replicate report schema")
    if report.get("report_type") != "benchmark_replicate_aggregate":
        failures.append("invalid replicate report type")
    if report.get("report_sha256") != hash_payload(report, "report_sha256"):
        failures.append("replicate report hash mismatch")
    configuration = report.get("compatibility_configuration")
    if not isinstance(configuration, dict) or report.get(
        "compatibility_sha256"
    ) != sha256_json(configuration):
        failures.append("compatibility configuration hash mismatch")
    replicates = report.get("replicates")
    cases = report.get("cases")
    domains_data = report.get("domain_results")
    if (
        not isinstance(replicates, list)
        or not isinstance(cases, list)
        or not isinstance(domains_data, dict)
    ):
        return [
            *failures,
            "replicates, cases, and domain_results must have valid container types",
        ]
    replicate_ids: list[str] = []
    for item in replicates:
        try:
            replicate_ids.append(validate_replicate_id(item.get("replicate_id")))
        except (AttributeError, ValueError) as exc:
            failures.append(f"invalid report replicate: {exc}")
    if len(set(replicate_ids)) != len(replicate_ids):
        failures.append("report replicate IDs are not unique")
    if len(replicates) < MINIMUM_REPLICATES or report.get("replicate_count") != len(
        replicates
    ):
        failures.append("replicate count mismatch or below minimum")

    computed_domains: dict[str, list[dict[str, Any]]] = {}
    seen_cases: set[tuple[str, str]] = set()
    for case in cases:
        if not isinstance(case, dict):
            failures.append("case row must be an object")
            continue
        domain, case_id = case.get("domain"), case.get("case_id")
        key = (domain, case_id)
        if (
            not isinstance(domain, str)
            or not isinstance(case_id, str)
            or key in seen_cases
        ):
            failures.append(f"invalid or duplicate report case: {key!r}")
            continue
        seen_cases.add(key)
        observations = case.get("observations")
        if not isinstance(observations, list):
            failures.append(f"{domain}/{case_id}: observations must be a list")
            continue
        observed_ids = [
            item.get("replicate_id") for item in observations if isinstance(item, dict)
        ]
        if sorted(observed_ids) != sorted(replicate_ids):
            failures.append(f"{domain}/{case_id}: replicate observation set mismatch")
            continue
        counts = {outcome: 0 for outcome in OUTCOMES}
        valid_observations = True
        for observation in observations:
            outcome = observation.get("outcome")
            if outcome not in OUTCOMES or not all(
                isinstance(observation.get(field), bool)
                for field in (
                    "critical_regression",
                    "fatal_flaw",
                    "optimized_hard_failure",
                )
            ):
                failures.append(f"{domain}/{case_id}: invalid observation")
                valid_observations = False
                break
            counts[outcome] += 1
        if not valid_observations:
            continue
        consensus, basis = _consensus(counts)
        stable = sum(value > 0 for value in counts.values()) == 1
        expected = {
            "outcome_counts": counts,
            "consensus": consensus,
            "consensus_basis": basis,
            "agreement_ratio": max(counts.values()) / len(replicates),
            "stable": stable,
        }
        for field, value in expected.items():
            if case.get(field) != value:
                failures.append(f"{domain}/{case_id}: derived mismatch: {field}")
        computed_domains.setdefault(domain, []).append(case)

    if set(domains_data) != set(computed_domains):
        failures.append("domain_results keys do not match case domains")
    expected_replicate_domains: dict[str, list[dict[str, Any]]] = {
        replicate_id: [] for replicate_id in replicate_ids
    }
    for domain, domain_cases in computed_domains.items():
        expected_replicate_results = []
        for replicate_id in replicate_ids:
            observations = [
                next(
                    item
                    for item in case["observations"]
                    if item["replicate_id"] == replicate_id
                )
                for case in domain_cases
            ]
            result = {
                "replicate_id": replicate_id,
                **_domain_gate_from_observations(observations),
            }
            expected_replicate_results.append(result)
            expected_replicate_domains[replicate_id].append(result)
        consensus_observations = [
            {
                "outcome": case["consensus"],
                "critical_regression": any(
                    item["critical_regression"] for item in case["observations"]
                ),
                "fatal_flaw": any(item["fatal_flaw"] for item in case["observations"]),
                "optimized_hard_failure": any(
                    item["optimized_hard_failure"] for item in case["observations"]
                ),
            }
            for case in domain_cases
        ]
        consensus = _domain_gate_from_observations(consensus_observations)
        expected_domain = {
            "case_count": len(domain_cases),
            "consensus_wins": consensus["wins"],
            "consensus_ties": consensus["ties"],
            "consensus_losses": consensus["losses"],
            "critical_regressions": sum(
                item["critical_regression"]
                for case in domain_cases
                for item in case["observations"]
            ),
            "fatal_flaws": sum(
                item["fatal_flaw"]
                for case in domain_cases
                for item in case["observations"]
            ),
            "optimized_hard_failures": sum(
                item["optimized_hard_failure"]
                for case in domain_cases
                for item in case["observations"]
            ),
            "all_individual_gates_passed": all(
                item["gate_passed"] for item in expected_replicate_results
            ),
            "consensus_gate_passed": consensus["gate_passed"],
            "gate_passed": all(
                item["gate_passed"] for item in expected_replicate_results
            )
            and consensus["gate_passed"],
            "replicate_results": expected_replicate_results,
        }
        if domains_data.get(domain) != expected_domain:
            failures.append(f"domain derived mismatch: {domain}")

    expected_replicates = []
    run_fingerprints: list[str] = []
    replicate_lookup = {
        item.get("replicate_id"): item for item in replicates if isinstance(item, dict)
    }
    for replicate_id in replicate_ids:
        source = replicate_lookup[replicate_id]
        artifact_hashes = source.get("optimization_artifact_sha256")
        evaluation_hashes = source.get("evaluation_sha256")
        valid_hash_maps = all(
            isinstance(value, dict)
            and set(value) == set(computed_domains)
            and all(
                isinstance(digest, str)
                and re.fullmatch(r"[0-9a-f]{64}", digest) is not None
                for digest in value.values()
            )
            for value in (artifact_hashes, evaluation_hashes)
        )
        if not valid_hash_maps:
            failures.append(f"{replicate_id}: invalid artifact hash maps")
        else:
            expected_fingerprint = sha256_json(
                {
                    "optimization_artifact_sha256": artifact_hashes,
                    "evaluation_sha256": evaluation_hashes,
                }
            )
            if source.get("run_fingerprint_sha256") != expected_fingerprint:
                failures.append(f"{replicate_id}: run fingerprint mismatch")
            run_fingerprints.append(expected_fingerprint)
        results = expected_replicate_domains[replicate_id]
        case_count = sum(item["case_count"] for item in results)
        facts = {
            "domain_count": len(results),
            "case_count": case_count,
            "wins": sum(item["wins"] for item in results),
            "ties": sum(item["ties"] for item in results),
            "losses": sum(item["losses"] for item in results),
            "critical_regressions": sum(
                item["critical_regressions"] for item in results
            ),
            "fatal_flaws": sum(item["fatal_flaws"] for item in results),
            "optimized_hard_failures": sum(
                item["optimized_hard_failures"] for item in results
            ),
        }
        net = (facts["wins"] - facts["losses"]) / case_count if case_count else 0.0
        facts["aggregate_gate_passed"] = (
            len(results) == RELEASE_DOMAIN_COUNT
            and all(item["gate_passed"] for item in results)
            and net >= 0.10
            and facts["critical_regressions"] == 0
            and facts["fatal_flaws"] == 0
            and facts["optimized_hard_failures"] == 0
        )
        for field, value in facts.items():
            if source.get(field) != value:
                failures.append(f"{replicate_id}: derived mismatch: {field}")
        expected_replicates.append(facts)
    if len(set(run_fingerprints)) != len(run_fingerprints):
        failures.append("replicate run fingerprints are not unique")

    domain_names = sorted(computed_domains)
    coverage = report.get("coverage")
    expected_coverage = {
        "domains": domain_names,
        "domain_count": len(domain_names),
        "case_count": len(cases),
        "observation_count": len(cases) * len(replicates),
    }
    if coverage != expected_coverage:
        failures.append("coverage derived mismatch")
    consensus_counts = {
        outcome: sum(case.get("consensus") == outcome for case in cases)
        for outcome in OUTCOMES
    }
    critical = sum(
        item.get("critical_regression") is True
        for case in cases
        for item in case.get("observations", [])
    )
    fatal = sum(
        item.get("fatal_flaw") is True
        for case in cases
        for item in case.get("observations", [])
    )
    hard = sum(
        item.get("optimized_hard_failure") is True
        for case in cases
        for item in case.get("observations", [])
    )
    stable = sum(case.get("stable") is True for case in cases)
    case_count = len(cases)
    net = (
        (consensus_counts["win"] - consensus_counts["loss"]) / case_count
        if case_count
        else 0.0
    )
    full = (
        len(domain_names) == RELEASE_DOMAIN_COUNT and case_count >= RELEASE_CASE_COUNT
    )
    all_domains = bool(domains_data) and all(
        item.get("gate_passed") is True for item in domains_data.values()
    )
    all_release = bool(expected_replicates) and all(
        item["aggregate_gate_passed"] for item in expected_replicates
    )
    release = (
        len(replicates) >= MINIMUM_REPLICATES
        and full
        and all_release
        and all_domains
        and net >= 0.10
        and critical == 0
        and fatal == 0
        and hard == 0
    )
    expected_aggregate = {
        "consensus_wins": consensus_counts["win"],
        "consensus_ties": consensus_counts["tie"],
        "consensus_losses": consensus_counts["loss"],
        "net_improvement": net,
        "critical_regressions": critical,
        "fatal_flaws": fatal,
        "optimized_hard_failures": hard,
        "stable_cases": stable,
        "unstable_cases": case_count - stable,
        "exact_agreement_rate": stable / case_count if case_count else 0.0,
        "full_release_coverage": full,
        "all_individual_release_gates_passed": all_release,
        "all_domain_gates_passed": all_domains,
        "diagnostic_gate_passed": all_domains,
        "release_gate_passed": release,
    }
    if report.get("aggregate") != expected_aggregate:
        failures.append("aggregate derived mismatch")
    expected_evidence = _evidence_payload(
        infer_evidence(
            deterministic_checks_passed=True,
            matched_cases=case_count * len(replicates),
            comparative_improvement_passed=release,
            repeated_or_cross_model=True,
        )
    )
    try:
        evidence_data = report.get("evidence")
        if not isinstance(evidence_data, dict):
            raise ValueError("evidence must be an object")
        Evidence(
            level=evidence_data.get("level", ""),
            status=evidence_data.get("status", ""),
            claim=evidence_data.get("claim", ""),
            limitations=tuple(evidence_data.get("limitations", [])),
        ).validate()
        if evidence_data != expected_evidence:
            failures.append("replicate evidence does not match release gate")
    except (TypeError, ValueError) as exc:
        failures.append(f"invalid replicate evidence: {exc}")
    return failures


def validate_replicate_report(report: Any) -> list[str]:
    """Validate untrusted report input without leaking parser exceptions."""
    try:
        return _validate_replicate_report(report)
    except (AttributeError, KeyError, TypeError, ValueError, StopIteration) as exc:
        return [f"malformed replicate report: {exc}"]
