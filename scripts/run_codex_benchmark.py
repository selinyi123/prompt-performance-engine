#!/usr/bin/env python3
"""Run the cross-domain benchmark through an authenticated local Codex CLI."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from prompt_performance_engine.adapters import CodexExecAdapter  # noqa: E402
from prompt_performance_engine.benchmark import (  # noqa: E402
    group_jobs_by_domain,
    load_benchmark_definition,
    validate_benchmark,
)
from prompt_performance_engine.codex_evaluation import (  # noqa: E402
    CachedCodexBlindJudge,
    CachedCodexExecutor,
    EVALUATION_PROTOCOL,
)
from prompt_performance_engine.contracts import OptimizationRequest  # noqa: E402
from prompt_performance_engine.contracts import PACKAGE_VERSION  # noqa: E402
from prompt_performance_engine.evaluation import (  # noqa: E402
    ExecutionConfig,
    evaluate_suite,
    validate_evaluation,
)
from prompt_performance_engine.evidence import infer_evidence  # noqa: E402
from prompt_performance_engine.hashing import hash_payload  # noqa: E402
from prompt_performance_engine.runtime import optimize  # noqa: E402
from prompt_performance_engine.validation import validate_artifact  # noqa: E402


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def load_valid_artifact(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if not validate_artifact(data) else None


def load_valid_evaluation(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if not validate_evaluation(data) else None


def ensure_run_manifest(
    output_directory: Path,
    configuration: dict[str, Any],
) -> dict[str, Any]:
    manifest = {
        "schema_version": "1.0.0",
        "configuration": configuration,
    }
    manifest["manifest_sha256"] = hash_payload(manifest, "manifest_sha256")
    path = output_directory / "run-manifest.json"
    if path.is_file():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing != manifest:
            raise ValueError(
                "Output directory belongs to a different benchmark configuration."
            )
    else:
        write_json(path, manifest)
    return manifest


def build_run_configuration(
    *,
    suite_id: str,
    benchmark_definition_sha256: str,
    model: str,
    reasoning_effort: str,
    candidate_count: int,
) -> dict[str, Any]:
    optimizer_prompt_sha256 = hashlib.sha256(
        (ROOT / "prompts" / "optimizer.md").read_bytes()
    ).hexdigest()
    domain_profiles_sha256 = hashlib.sha256(
        (ROOT / "profiles" / "domain_profiles.json").read_bytes()
    ).hexdigest()
    return {
        "suite_id": suite_id,
        "benchmark_definition_sha256": benchmark_definition_sha256,
        "optimizer_prompt_sha256": optimizer_prompt_sha256,
        "domain_profiles_sha256": domain_profiles_sha256,
        "package_version": PACKAGE_VERSION,
        "evaluation_protocol": EVALUATION_PROTOCOL,
        "model": model,
        "reasoning_effort": reasoning_effort,
        "temperature": None,
        "max_tokens": None,
        "generation_seed": None,
        "candidate_count": candidate_count,
        "blind_seed": 20260613,
    }


def actual_usage_from_tree(root: Path) -> dict[str, int]:
    totals: dict[str, int] = {"actual_model_calls": 0}
    paths = list(root.glob("*/optimization.json"))
    paths.extend(root.glob("*/cache/**/*.json"))
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        candidates: list[Any]
        if path.name == "optimization.json":
            runtime = data.get("runtime", {})
            if not isinstance(runtime, dict):
                continue
            totals["actual_model_calls"] += int(runtime.get("total_calls", 0))
            candidates = [runtime.get("total_usage")]
        else:
            totals["actual_model_calls"] += 1
            candidates = [
                data.get("metadata", {}).get("usage")
                if isinstance(data.get("metadata"), dict)
                else None,
                data.get("model_metadata", {}).get("usage")
                if isinstance(data.get("model_metadata"), dict)
                else None,
            ]
        for usage in candidates:
            if not isinstance(usage, dict):
                continue
            for key, value in usage.items():
                if isinstance(value, int) and not isinstance(value, bool):
                    totals[key] = totals.get(key, 0) + value
    return totals


def build_summary(
    suite_id: str,
    results: dict[str, dict[str, Any]],
    output_directory: Path,
) -> dict[str, Any]:
    wins = sum(result["wins"] for result in results.values())
    ties = sum(result["ties"] for result in results.values())
    losses = sum(result["losses"] for result in results.values())
    cases = sum(result["case_count"] for result in results.values())
    critical = sum(result["critical_regressions"] for result in results.values())
    fatal = sum(result["fatal_flaws"] for result in results.values())
    all_domains_pass = bool(results) and all(
        result["gate_passed"] for result in results.values()
    )
    net_improvement = (wins - losses) / cases if cases else 0.0
    aggregate_gate = (
        len(results) == 12
        and all_domains_pass
        and net_improvement >= 0.10
        and critical == 0
        and fatal == 0
    )
    evidence = infer_evidence(
        deterministic_checks_passed=True,
        matched_cases=cases,
        comparative_improvement_passed=aggregate_gate,
        repeated_or_cross_model=True,
    )
    summary: dict[str, Any] = {
        "schema_version": "1.0.0",
        "suite_id": suite_id,
        "completed_domains": sorted(results),
        "domain_count": len(results),
        "case_count": cases,
        "wins": wins,
        "ties": ties,
        "losses": losses,
        "net_improvement": net_improvement,
        "critical_regressions": critical,
        "fatal_flaws": fatal,
        "all_domains_pass": all_domains_pass,
        "aggregate_gate_passed": aggregate_gate,
        "evaluation_protocol": {
            "version": EVALUATION_PROTOCOL,
            "execution_model": "fixed per run",
            "temperature": "provider default; not configurable by Codex CLI",
            "max_tokens": "provider default; not configurable by Codex CLI",
            "generation_seed": "not configurable by Codex CLI",
            "blind_judges": 2,
            "judge_independence": "separate same-model calls and caches",
            "cross_model": False,
        },
        "usage": actual_usage_from_tree(output_directory),
        "evidence": asdict(evidence),
        "domain_results": {
            domain: {
                key: result[key]
                for key in (
                    "case_count",
                    "wins",
                    "ties",
                    "losses",
                    "critical_regressions",
                    "fatal_flaws",
                    "gate_passed",
                )
            }
            for domain, result in sorted(results.items())
        },
    }
    summary["summary_sha256"] = hash_payload(summary, "summary_sha256")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--benchmark",
        type=Path,
        default=ROOT / "benchmark" / "catalog-60.json",
    )
    parser.add_argument(
        "--output-directory",
        type=Path,
        default=ROOT / "artifacts" / "codex-benchmark-v10",
    )
    parser.add_argument("--model", default="gpt-5.5")
    parser.add_argument(
        "--reasoning-effort",
        choices=["minimal", "low", "medium", "high", "xhigh"],
        default="low",
    )
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument(
        "--candidate-count",
        type=int,
        choices=range(1, 6),
        default=1,
        help="Independent optimization candidates; values above 1 are experimental.",
    )
    parser.add_argument(
        "--domains",
        help="Comma-separated domain ids; default runs all domains.",
    )
    args = parser.parse_args()

    suite_id, jobs = load_benchmark_definition(args.benchmark)
    failures = validate_benchmark(suite_id, jobs)
    if failures:
        raise ValueError(f"Benchmark is invalid: {failures}")
    grouped = group_jobs_by_domain(jobs)
    selected = (
        [item.strip() for item in args.domains.split(",") if item.strip()]
        if args.domains
        else sorted(grouped)
    )
    unknown = set(selected) - set(grouped)
    if unknown:
        raise ValueError(f"Unknown benchmark domains: {sorted(unknown)}")

    output_directory = args.output_directory.resolve()
    output_directory.mkdir(parents=True, exist_ok=True)
    benchmark_definition_sha256 = hash_payload(
        {
            "suite_id": suite_id,
            "jobs": [asdict(job) for job in jobs],
        },
        "_not_present",
    )
    ensure_run_manifest(
        output_directory,
        build_run_configuration(
            suite_id=suite_id,
            benchmark_definition_sha256=benchmark_definition_sha256,
            model=args.model,
            reasoning_effort=args.reasoning_effort,
            candidate_count=args.candidate_count,
        ),
    )

    def adapter_factory() -> CodexExecAdapter:
        return CodexExecAdapter(
            model=args.model,
            working_directory=ROOT,
            reasoning_effort=args.reasoning_effort,
            timeout_seconds=args.timeout,
        )

    results: dict[str, dict[str, Any]] = {}
    for path in output_directory.glob("*/evaluation.json"):
        result = load_valid_evaluation(path)
        if result is not None:
            results[path.parent.name] = result

    for domain in selected:
        job = grouped[domain]
        domain_directory = output_directory / domain
        optimization_path = domain_directory / "optimization.json"
        evaluation_path = domain_directory / "evaluation.json"
        existing_evaluation = load_valid_evaluation(evaluation_path)
        if existing_evaluation is not None:
            results[domain] = existing_evaluation
            print(f"SKIP {domain}: valid cached evaluation")
            continue

        artifact = load_valid_artifact(optimization_path)
        if artifact is None:
            print(f"OPTIMIZE {domain}")
            optimized = optimize(
                OptimizationRequest(
                    source_prompt=job.source_prompt,
                    domain=domain,
                    output_format="standard",
                ),
                adapter_factory(),
                candidate_count=args.candidate_count,
            )
            artifact = optimized.artifact
            write_json(optimization_path, artifact)
        optimized_prompt = artifact["optimized_prompt"]

        executor = CachedCodexExecutor(
            adapter_factory,
            domain_directory / "cache" / "executions",
        )
        judges = [
            CachedCodexBlindJudge(
                name=f"codex-judge-{index}",
                adapter_factory=adapter_factory,
                cache_directory=domain_directory / "cache" / f"judge-{index}",
            )
            for index in (1, 2)
        ]
        print(f"EVALUATE {domain}: {len(job.cases)} cases")
        result = evaluate_suite(
            suite_id=f"{suite_id}:{domain}",
            original_prompt=job.source_prompt,
            optimized_prompt=optimized_prompt,
            cases=job.cases,
            executor=executor,
            judges=judges,
            config=ExecutionConfig(
                model=args.model,
                temperature=None,
                max_tokens=None,
                seed=None,
            ),
            blind_seed=20260613,
            repeated_or_cross_model=True,
        )
        validation_failures = validate_evaluation(result)
        if validation_failures:
            raise ValueError(
                f"Generated evaluation for {domain} is invalid: "
                f"{validation_failures}"
            )
        write_json(evaluation_path, result)
        results[domain] = result
        summary = build_summary(suite_id, results, output_directory)
        write_json(output_directory / "summary.json", summary)
        print(
            f"DONE {domain}: {result['wins']}W/{result['ties']}T/"
            f"{result['losses']}L gate={result['gate_passed']}"
        )

    summary = build_summary(suite_id, results, output_directory)
    write_json(output_directory / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if all(results[domain]["gate_passed"] for domain in selected) else 1


if __name__ == "__main__":
    raise SystemExit(main())
