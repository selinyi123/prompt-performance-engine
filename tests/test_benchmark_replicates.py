import copy
import hashlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import asdict
from pathlib import Path

from prompt_performance_engine.benchmark_replicates import (
    aggregate_benchmark_replicates,
    validate_replicate_id,
    validate_replicate_report,
)
from prompt_performance_engine.adapters import MockSequenceAdapter
from prompt_performance_engine.cli import main
from prompt_performance_engine.contracts import OptimizationRequest
from prompt_performance_engine.evaluation import (
    EvaluationCase,
    ExecutionConfig,
    JudgeDecision,
    RecordedExecutor,
    evaluate_suite,
)
from prompt_performance_engine.evidence import infer_evidence
from prompt_performance_engine.hashing import hash_payload
from prompt_performance_engine.runtime import optimize


class MarkerJudge:
    def __init__(self, name: str) -> None:
        self.name = name

    def judge(
        self,
        *,
        case: EvaluationCase,
        output_a: str,
        output_b: str,
    ) -> JudgeDecision:
        del case
        a_is_better = "QUALITY_MARKER" in output_a
        b_is_better = "QUALITY_MARKER" in output_b
        if a_is_better and not b_is_better:
            winner = "A"
        elif b_is_better and not a_is_better:
            winner = "B"
        else:
            winner = "tie"
        return JudgeDecision(winner=winner, reason="Deterministic marker comparison.")


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def expected_summary(evaluations: dict[str, dict], replicate_id: str) -> dict:
    cases = sum(item["case_count"] for item in evaluations.values())
    wins = sum(item["wins"] for item in evaluations.values())
    ties = sum(item["ties"] for item in evaluations.values())
    losses = sum(item["losses"] for item in evaluations.values())
    critical = sum(item["critical_regressions"] for item in evaluations.values())
    fatal = sum(item["fatal_flaws"] for item in evaluations.values())
    hard = sum(item["optimized_hard_failures"] for item in evaluations.values())
    all_domains = all(item["gate_passed"] for item in evaluations.values())
    net = (wins - losses) / cases
    aggregate_gate = (
        len(evaluations) == 12
        and all_domains
        and net >= 0.10
        and critical == 0
        and fatal == 0
        and hard == 0
    )
    summary = {
        "schema_version": "1.0.0",
        "suite_id": "replicate-suite",
        "replicate_id": replicate_id,
        "benchmark_definition_sha256": "a" * 64,
        "run_manifest_sha256": None,
        "completed_domains": sorted(evaluations),
        "domain_count": len(evaluations),
        "case_count": cases,
        "wins": wins,
        "ties": ties,
        "losses": losses,
        "net_improvement": net,
        "critical_regressions": critical,
        "fatal_flaws": fatal,
        "optimized_hard_failures": hard,
        "all_domains_pass": all_domains,
        "aggregate_gate_passed": aggregate_gate,
        "evaluation_protocol": {
            "version": "test-v26",
            "repeated_run": False,
            "cross_model": False,
        },
        "usage": {"actual_model_calls": 0},
        "evidence": asdict(
            infer_evidence(
                deterministic_checks_passed=True,
                matched_cases=cases,
                comparative_improvement_passed=aggregate_gate,
                repeated_or_cross_model=False,
            )
        ),
        "domain_results": {
            domain: {
                field: evaluation[field]
                for field in (
                    "case_count",
                    "wins",
                    "ties",
                    "losses",
                    "critical_regressions",
                    "fatal_flaws",
                    "optimized_hard_failures",
                    "gate_passed",
                )
            }
            for domain, evaluation in sorted(evaluations.items())
        },
    }
    return summary


def create_run(
    root: Path,
    replicate_id: str,
    domains: list[str],
    outcomes: dict[str, list[str]] | None = None,
) -> Path:
    run_root = root / replicate_id
    evaluations: dict[str, dict] = {}
    for domain in domains:
        original_prompt = f"Original prompt for {domain}."
        optimized_prompt = (
            f"Optimized prompt for {domain} in {replicate_id}. "
            "Include qualified professional review where risk requires it."
        )
        artifact = optimize(
            OptimizationRequest(
                source_prompt=original_prompt,
                domain=domain,
            ),
            MockSequenceAdapter(
                [f"<optimized_prompt>{optimized_prompt}</optimized_prompt>"]
            ),
        ).artifact
        write_json(run_root / domain / "optimization.json", artifact)
        cases = [
            EvaluationCase(
                case_id=f"{domain}-case-{index}",
                input_text=f"Input for {domain} case {index}.",
                rubric=("Prefer the higher-quality response.",),
                domain=domain,
            )
            for index in range(1, 6)
        ]
        desired = (outcomes or {}).get(domain, ["win"] * 5)
        outputs: dict[tuple[str, str], str] = {}
        for case, outcome in zip(cases, desired, strict=True):
            baseline = (
                "A complete baseline response with enough detail for deterministic "
                "benchmark execution and qualified professional review."
            )
            quality = (
                "QUALITY_MARKER A complete superior response with explicit reasoning, "
                "constraints, verification evidence, and qualified professional review."
            )
            if outcome == "win":
                original_output, optimized_output = baseline, quality
            elif outcome == "loss":
                original_output, optimized_output = quality, baseline
            else:
                original_output = optimized_output = baseline
            input_hash = hashlib.sha256(case.input_text.encode()).hexdigest()
            outputs[
                (hashlib.sha256(original_prompt.encode()).hexdigest(), input_hash)
            ] = original_output
            outputs[
                (hashlib.sha256(optimized_prompt.encode()).hexdigest(), input_hash)
            ] = optimized_output
        evaluation = evaluate_suite(
            suite_id=f"replicate-suite:{domain}",
            original_prompt=original_prompt,
            optimized_prompt=optimized_prompt,
            cases=cases,
            executor=RecordedExecutor(outputs),
            judges=[MarkerJudge("judge-1"), MarkerJudge("judge-2")],
            config=ExecutionConfig(
                model="model-a",
                temperature=None,
                max_tokens=None,
                seed=None,
            ),
            blind_seed=42,
            repeated_or_cross_model=False,
        )
        evaluations[domain] = evaluation
        write_json(run_root / domain / "evaluation.json", evaluation)

    configuration = {
        "suite_id": "replicate-suite",
        "benchmark_definition_sha256": "a" * 64,
        "optimizer_prompt_sha256": "b" * 64,
        "domain_profiles_sha256": "c" * 64,
        "evaluation_implementation_sha256": "d" * 64,
        "python_version": "3.11.0",
        "platform_system": "TestOS",
        "package_version": "0.3.0",
        "evaluation_protocol": "test-v26",
        "model": "model-a",
        "reasoning_effort": "low",
        "temperature": None,
        "max_tokens": None,
        "generation_seed": None,
        "candidate_count": 1,
        "replicate_id": replicate_id,
        "blind_seed": 42,
    }
    manifest = {"schema_version": "1.0.0", "configuration": configuration}
    manifest["manifest_sha256"] = hash_payload(manifest, "manifest_sha256")
    write_json(run_root / "run-manifest.json", manifest)

    summary = expected_summary(evaluations, replicate_id)
    summary["run_manifest_sha256"] = manifest["manifest_sha256"]
    summary["summary_sha256"] = hash_payload(summary, "summary_sha256")
    write_json(run_root / "summary.json", summary)
    return run_root


class BenchmarkReplicateTests(unittest.TestCase):
    def test_partial_repeatability_report_is_diagnostic_e1(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            domain = "professional_writing"
            runs = [
                create_run(root, "run-a", [domain]),
                create_run(root, "run-b", [domain]),
                create_run(
                    root,
                    "run-c",
                    [domain],
                    {domain: ["tie", "win", "win", "win", "win"]},
                ),
            ]

            report = aggregate_benchmark_replicates(runs)

            self.assertEqual(validate_replicate_report(report), [])
            self.assertTrue(report["aggregate"]["diagnostic_gate_passed"])
            self.assertFalse(report["aggregate"]["release_gate_passed"])
            self.assertEqual(report["aggregate"]["unstable_cases"], 1)
            self.assertEqual(report["evidence"]["level"], "E1")

    def test_three_full_passing_runs_reach_e3(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            domains = [
                "agents_automation",
                "business_strategy",
                "creative_design",
                "education",
                "high_risk_advisory",
                "image_generation",
                "marketing_sales",
                "professional_writing",
                "research_analysis",
                "software_engineering",
                "structured_data",
                "translation_localization",
            ]
            runs = [create_run(root, f"run-{index}", domains) for index in range(1, 4)]

            report = aggregate_benchmark_replicates(runs)

            self.assertEqual(validate_replicate_report(report), [])
            self.assertEqual(report["coverage"]["case_count"], 60)
            self.assertEqual(report["coverage"]["observation_count"], 180)
            self.assertTrue(report["aggregate"]["release_gate_passed"])
            self.assertEqual(report["evidence"]["level"], "E3")

    def test_configuration_drift_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runs = [
                create_run(root, f"run-{index}", ["professional_writing"])
                for index in range(1, 4)
            ]
            manifest_path = runs[2] / "run-manifest.json"
            summary_path = runs[2] / "summary.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["configuration"]["model"] = "model-b"
            manifest["manifest_sha256"] = hash_payload(manifest, "manifest_sha256")
            write_json(manifest_path, manifest)
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            summary["run_manifest_sha256"] = manifest["manifest_sha256"]
            summary["summary_sha256"] = hash_payload(summary, "summary_sha256")
            write_json(summary_path, summary)

            with self.assertRaisesRegex(ValueError, "not compatible"):
                aggregate_benchmark_replicates(runs)

    def test_duplicate_replicate_ids_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runs = [
                create_run(root / "one", "same", ["professional_writing"]),
                create_run(root / "two", "same", ["professional_writing"]),
                create_run(root / "three", "other", ["professional_writing"]),
            ]
            with self.assertRaisesRegex(ValueError, "must be unique"):
                aggregate_benchmark_replicates(runs)

    def test_copied_run_artifacts_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runs = [
                create_run(root, f"run-{index}", ["professional_writing"])
                for index in range(1, 4)
            ]
            source = runs[0] / "professional_writing"
            for target_root in runs[1:]:
                target = target_root / "professional_writing"
                for name in ("optimization.json", "evaluation.json"):
                    (target / name).write_bytes((source / name).read_bytes())

            with self.assertRaisesRegex(ValueError, "not independent"):
                aggregate_benchmark_replicates(runs)

    def test_tampered_evaluation_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runs = [
                create_run(root, f"run-{index}", ["professional_writing"])
                for index in range(1, 4)
            ]
            path = runs[0] / "professional_writing" / "evaluation.json"
            evaluation = json.loads(path.read_text(encoding="utf-8"))
            evaluation["records"][0]["optimized_output"] += " tampered"
            write_json(path, evaluation)

            with self.assertRaisesRegex(
                ValueError,
                "invalid professional_writing evaluation",
            ):
                aggregate_benchmark_replicates(runs)

    def test_valid_but_mismatched_optimization_artifact_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runs = [
                create_run(root, f"run-{index}", ["professional_writing"])
                for index in range(1, 4)
            ]
            replacement = optimize(
                OptimizationRequest(
                    source_prompt="Original prompt for professional_writing.",
                    domain="professional_writing",
                ),
                MockSequenceAdapter(
                    [
                        "<optimized_prompt>A different but valid optimized "
                        "prompt with sufficient detail.</optimized_prompt>"
                    ]
                ),
            ).artifact
            write_json(
                runs[0] / "professional_writing" / "optimization.json",
                replacement,
            )

            with self.assertRaisesRegex(ValueError, "Prompt binding mismatch"):
                aggregate_benchmark_replicates(runs)

    def test_tampered_optimization_artifact_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runs = [
                create_run(root, f"run-{index}", ["professional_writing"])
                for index in range(1, 4)
            ]
            path = runs[0] / "professional_writing" / "optimization.json"
            artifact = json.loads(path.read_text(encoding="utf-8"))
            artifact["optimized_prompt"] += " tampered"
            write_json(path, artifact)

            with self.assertRaisesRegex(
                ValueError,
                "invalid professional_writing optimization artifact",
            ):
                aggregate_benchmark_replicates(runs)

    def test_rehashed_derived_tampering_is_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runs = [
                create_run(root, f"run-{index}", ["professional_writing"])
                for index in range(1, 4)
            ]
            report = aggregate_benchmark_replicates(runs)
            tampered = copy.deepcopy(report)
            tampered["cases"][0]["consensus"] = "loss"
            tampered["report_sha256"] = hash_payload(tampered, "report_sha256")

            failures = validate_replicate_report(tampered)

            self.assertTrue(any("derived mismatch" in failure for failure in failures))

            malformed = copy.deepcopy(report)
            malformed["cases"][0]["observations"][0]["replicate_id"] = None
            malformed["report_sha256"] = hash_payload(malformed, "report_sha256")
            malformed_failures = validate_replicate_report(malformed)
            self.assertTrue(malformed_failures)
            self.assertIn("malformed replicate report", malformed_failures[0])

    def test_cli_aggregates_and_validates_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runs = [
                create_run(root, f"run-{index}", ["professional_writing"])
                for index in range(1, 4)
            ]
            report_path = root / "reports" / "replicates.json"
            with redirect_stdout(io.StringIO()):
                aggregate_exit = main(
                    [
                        "aggregate-benchmark-replicates",
                        *(str(path) for path in runs),
                        "--output",
                        str(report_path),
                    ]
                )
                validate_exit = main(
                    ["validate-benchmark-replicates", str(report_path)]
                )
            self.assertEqual(aggregate_exit, 0)
            self.assertEqual(validate_exit, 0)
            self.assertTrue(report_path.is_file())

    def test_replicate_id_validation(self):
        self.assertEqual(validate_replicate_id("run.2026-06-19_A"), "run.2026-06-19_A")
        self.assertIsNone(validate_replicate_id(None, required=False))
        for invalid in (None, "", "-bad", "bad id", "a" * 65):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    validate_replicate_id(invalid)


if __name__ == "__main__":
    unittest.main()
