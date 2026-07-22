import copy
import hashlib
import io
import json
import tempfile
import unittest
from itertools import count
from contextlib import redirect_stdout
from dataclasses import asdict
from pathlib import Path

from prompt_performance_engine.benchmark_replicates import (
    _receipt_inputs,
    actual_usage_from_payloads,
    aggregate_benchmark_replicates,
    validate_e3_authority,
    validate_replicate_id,
    validate_replicate_report,
    validate_replicate_sources,
)
from prompt_performance_engine.adapters import CompletionResponse, MockSequenceAdapter
from prompt_performance_engine.cli import main
from prompt_performance_engine.contracts import OptimizationRequest
from prompt_performance_engine.evaluation import (
    EvaluationCase,
    ExecutionConfig,
    ExecutionOutput,
    JudgeDecision,
    RecordedExecutor,
    evaluate_suite,
)
from prompt_performance_engine.evidence import infer_evidence
from prompt_performance_engine.hashing import hash_payload, sha256_json
from prompt_performance_engine.runtime import optimize


CALL_SEQUENCE = count(1)


def response_id() -> str:
    return f"test-authoritative-response-{next(CALL_SEQUENCE)}"


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
        decision_payload = {
            "winner": winner,
            "reason": "Deterministic marker comparison.",
            "fatal_flaw_a": False,
            "fatal_flaw_b": False,
        }
        return JudgeDecision(
            winner=winner,
            reason=decision_payload["reason"],
            metadata=model_call_metadata(
                purpose="benchmark_judge",
                response_text=json.dumps(decision_payload, sort_keys=True),
            ),
        )


def model_call_metadata(*, purpose: str, response_text: str) -> dict:
    return {
        "provider": "openai-codex",
        "model": "model-a",
        "response_id": response_id(),
        "usage": {"total_tokens": 1},
        "attempts": 1,
        "elapsed_ms": 0,
        "status": "completed",
        "purpose": purpose,
        "request_sha256": "0" * 64,
        "response_sha256": hashlib.sha256(response_text.encode("utf-8")).hexdigest(),
    }


class TrustedFixtureReceiptVerifier:
    """Explicit test-only trust boundary for synthetic provider receipts."""

    def __init__(self):
        self.contexts = {}

    def register(self, *, replicate_id, configuration, artifacts, evaluations):
        for metadata, context in _receipt_inputs(
            replicate_id=replicate_id,
            configuration=configuration,
            artifacts=artifacts,
            evaluations=evaluations,
        ):
            identity = (metadata["provider"], metadata["response_id"])
            self.contexts[identity] = sha256_json(context)

    def verify(self, *, metadata, context_sha256):
        identity = (metadata.get("provider"), metadata.get("response_id"))
        if self.contexts.get(identity) != context_sha256:
            return None
        return sha256_json(
            {"metadata": dict(metadata), "context_sha256": context_sha256}
        )


TRUSTED_FIXTURE_RECEIPTS = TrustedFixtureReceiptVerifier()


class ModelBackedRecordedExecutor(RecordedExecutor):
    name = "model-backed-recorded-executor"

    def execute(self, **kwargs) -> ExecutionOutput:
        text = super().execute(**kwargs)
        return ExecutionOutput(
            text=text,
            metadata=model_call_metadata(
                purpose="benchmark_execution",
                response_text=text,
            ),
        )


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def expected_summary(
    evaluations: dict[str, dict],
    artifacts: dict[str, dict],
    replicate_id: str,
    benchmark_definition_sha256: str,
) -> dict:
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
        "schema_version": "2.0.0",
        "suite_id": "replicate-suite",
        "replicate_id": replicate_id,
        "benchmark_definition_sha256": benchmark_definition_sha256,
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
            "implementation_sha256": "d" * 64,
            "python_version": "3.11.0",
            "platform_system": "TestOS",
            "repeated_run": False,
            "cross_model": False,
        },
        "usage": actual_usage_from_payloads(
            list(artifacts.values()),
            list(evaluations.values()),
        ),
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
    artifacts: dict[str, dict] = {}

    def case_input(domain: str, index: int) -> str:
        markers = {
            "agents_automation": (
                "EXECUTION_MODE: simulated_tool_trace\n"
                "AVAILABLE_TOOLS: search, read\nTOOL_RESULTS: none\n"
            ),
            "structured_data": "OUTPUT_SCHEMA: object\nCONTENT: fixture data\n",
            "translation_localization": (
                "TARGET_LOCALE: fr-FR\nSOURCE_TEXT: fixture source text\n"
            ),
            "marketing_sales": (
                "BRIEF: fixture\nPRODUCT_FACTS: verified\nAUDIENCE: test\n"
                "CHANNEL: email\nCTA: review\nEVIDENCE: fixture\n"
            ),
        }
        return (
            markers.get(domain, "")
            + f"Input payload for {domain} benchmark case {index}. "
            + "Concrete fixture context with explicit constraints and verification "
            * 4
        )

    original_prompts = {
        domain: f"Original prompt for {domain}." for domain in domains
    }
    cases_by_domain = {
        domain: [
            EvaluationCase(
                case_id=f"{domain}-case-{index}",
                input_text=case_input(domain, index),
                rubric=(
                    "Prefer the higher-quality response.",
                    "Check completeness and correctness.",
                    "Check constraints and verification detail.",
                ),
                domain=domain,
            )
            for index in range(1, 6)
        ]
        for domain in domains
    }
    benchmark_definition = {
        "suite_id": "replicate-suite",
        "jobs": [
            {
                "job_id": f"{domain}-job",
                "domain": domain,
                "source_prompt": original_prompts[domain],
                "cases": [asdict(case) for case in cases_by_domain[domain]],
            }
            for domain in domains
        ],
    }
    benchmark_definition_sha256 = sha256_json(benchmark_definition)
    write_json(run_root / "benchmark-definition.json", benchmark_definition)
    for domain in domains:
        original_prompt = original_prompts[domain]
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
                [
                    CompletionResponse(
                        text=json.dumps({"optimized_prompt": optimized_prompt}),
                        provider="openai-codex",
                        model="model-a",
                        response_id=response_id(),
                        usage={"total_tokens": 1},
                    )
                ]
            ),
        ).artifact
        artifacts[domain] = artifact
        write_json(run_root / domain / "optimization.json", artifact)
        cases = cases_by_domain[domain]
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
            executor=ModelBackedRecordedExecutor(outputs),
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
        "benchmark_definition_sha256": benchmark_definition_sha256,
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
        "software_sandbox_image": None,
        "blind_seed": 42,
    }
    TRUSTED_FIXTURE_RECEIPTS.register(
        replicate_id=replicate_id,
        configuration=configuration,
        artifacts=artifacts,
        evaluations=evaluations,
    )
    manifest = {"schema_version": "2.0.0", "configuration": configuration}
    manifest["manifest_sha256"] = hash_payload(manifest, "manifest_sha256")
    write_json(run_root / "run-manifest.json", manifest)

    summary = expected_summary(
        evaluations,
        artifacts,
        replicate_id,
        benchmark_definition_sha256,
    )
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

            report = aggregate_benchmark_replicates(
                runs,
                receipt_verifier=TRUSTED_FIXTURE_RECEIPTS,
            )

            self.assertEqual(validate_replicate_report(report), [])
            self.assertTrue(report["aggregate"]["diagnostic_gate_passed"])
            self.assertFalse(report["aggregate"]["release_gate_passed"])
            self.assertEqual(report["aggregate"]["unstable_cases"], 1)
            self.assertEqual(report["evidence"]["level"], "E1")
            self.assertIn(
                "replicate release gate did not pass",
                validate_e3_authority(report, runs),
            )

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

            report = aggregate_benchmark_replicates(
                runs,
                receipt_verifier=TRUSTED_FIXTURE_RECEIPTS,
            )

            self.assertEqual(validate_replicate_report(report), [])
            self.assertEqual(report["coverage"]["case_count"], 60)
            self.assertEqual(report["coverage"]["observation_count"], 180)
            self.assertEqual(report["aggregate"]["actual_model_calls"], 756)
            self.assertTrue(report["aggregate"]["release_gate_passed"])
            self.assertEqual(report["evidence"]["level"], "E3")
            self.assertEqual(
                validate_e3_authority(
                    report,
                    runs,
                    receipt_verifier=TRUSTED_FIXTURE_RECEIPTS,
                ),
                [],
            )
            self.assertIn(
                "source run directories are required for E3 authority",
                validate_e3_authority(
                    report,
                    receipt_verifier=TRUSTED_FIXTURE_RECEIPTS,
                ),
            )
            extra_root = copy.deepcopy(report)
            extra_root["schema_forbidden_nonce"] = "forbidden"
            extra_root["report_sha256"] = hash_payload(
                extra_root,
                "report_sha256",
            )
            self.assertIn(
                "replicate report fields do not match the schema",
                validate_replicate_report(extra_root),
            )

            reused_bundle = copy.deepcopy(report)
            reused_bundle["replicates"][1]["receipt_bundle_sha256"] = (
                reused_bundle["replicates"][0]["receipt_bundle_sha256"]
            )
            source = reused_bundle["replicates"][1]
            source["run_fingerprint_sha256"] = sha256_json(
                {
                    "run_manifest_sha256": source["run_manifest_sha256"],
                    "summary_sha256": source["summary_sha256"],
                    "payload_fingerprint_sha256": source[
                        "payload_fingerprint_sha256"
                    ],
                    "execution_identity_sha256": source[
                        "execution_identity_sha256"
                    ],
                    "receipt_bundle_sha256": source["receipt_bundle_sha256"],
                }
            )
            reused_bundle["report_sha256"] = hash_payload(
                reused_bundle,
                "report_sha256",
            )
            self.assertIn(
                "replicate verified receipt bundle hashes are not unique",
                validate_replicate_report(reused_bundle),
            )

    def test_rehashed_definition_source_drift_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runs = [
                create_run(root, f"run-{index}", ["professional_writing"])
                for index in range(1, 4)
            ]
            run = runs[0]
            definition_path = run / "benchmark-definition.json"
            definition = json.loads(definition_path.read_text(encoding="utf-8"))
            definition["jobs"][0]["cases"][0]["input_text"] += " changed"
            write_json(definition_path, definition)
            definition_sha256 = sha256_json(definition)

            manifest_path = run / "run-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["configuration"]["benchmark_definition_sha256"] = (
                definition_sha256
            )
            manifest["manifest_sha256"] = hash_payload(
                manifest,
                "manifest_sha256",
            )
            write_json(manifest_path, manifest)

            summary_path = run / "summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            summary["benchmark_definition_sha256"] = definition_sha256
            summary["run_manifest_sha256"] = manifest["manifest_sha256"]
            summary["summary_sha256"] = hash_payload(summary, "summary_sha256")
            write_json(summary_path, summary)

            with self.assertRaisesRegex(
                ValueError,
                "evaluation does not match the benchmark source",
            ):
                aggregate_benchmark_replicates(
                    runs,
                    receipt_verifier=TRUSTED_FIXTURE_RECEIPTS,
                )

    def test_provider_metadata_without_trusted_receipts_stays_below_e3(self):
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

            self.assertFalse(report["aggregate"]["release_gate_passed"])
            self.assertFalse(report["aggregate"]["all_provider_receipts_verified"])
            self.assertNotEqual(report["evidence"]["level"], "E3")
            self.assertIn(
                "trusted provider call receipt verifier is required for E3 authority",
                validate_e3_authority(report, runs),
            )

    def test_run_without_real_model_calls_cannot_enter_replicate_authority(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runs = [
                create_run(root, f"run-{index}", ["professional_writing"])
                for index in range(1, 4)
            ]
            summary_path = runs[0] / "summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            summary["usage"]["actual_model_calls"] = 0
            summary["summary_sha256"] = hash_payload(summary, "summary_sha256")
            write_json(summary_path, summary)

            with self.assertRaisesRegex(ValueError, "model usage does not match"):
                aggregate_benchmark_replicates(runs)

    def test_mock_or_unidentified_model_call_cannot_enter_e3(self):
        mutations = (
            ("mock", lambda call: call.update({"provider": "mock"})),
            ("missing response", lambda call: call.update({"response_id": None})),
            ("empty usage", lambda call: call.update({"usage": {}})),
        )
        for name, mutate in mutations:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                runs = [
                    create_run(root, f"run-{index}", ["professional_writing"])
                    for index in range(1, 4)
                ]
                path = runs[0] / "professional_writing" / "optimization.json"
                artifact = json.loads(path.read_text(encoding="utf-8"))
                mutate(artifact["runtime"]["model_calls"][0])
                artifact["runtime"]["total_usage"] = dict(
                    artifact["runtime"]["model_calls"][0]["usage"]
                )
                artifact["artifact_payload_sha256"] = hash_payload(
                    artifact,
                    "artifact_payload_sha256",
                )
                write_json(path, artifact)

                with self.assertRaisesRegex(ValueError, "optimization model mismatch"):
                    aggregate_benchmark_replicates(runs)

    def test_rehashed_report_cannot_claim_e3_without_real_model_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runs = [
                create_run(root, f"run-{index}", ["professional_writing"])
                for index in range(1, 4)
            ]
            report = aggregate_benchmark_replicates(runs)
            report["replicates"][0]["actual_model_calls"] = 0
            report["aggregate"]["actual_model_calls"] = sum(
                item["actual_model_calls"] for item in report["replicates"]
            )
            report["report_sha256"] = hash_payload(report, "report_sha256")

            failures = validate_replicate_report(report)

            self.assertTrue(
                any("no real model calls" in failure for failure in failures)
            )

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

            with self.assertRaisesRegex(ValueError, "execution configuration mismatch"):
                aggregate_benchmark_replicates(runs)

    def test_run_configuration_requires_exact_fields_and_scalar_types(self):
        mutations = (
            ("boolean candidate count", lambda config: config.__setitem__("candidate_count", True)),
            ("boolean blind seed", lambda config: config.__setitem__("blind_seed", True)),
            ("boolean generation seed", lambda config: config.__setitem__("generation_seed", True)),
            ("boolean max tokens", lambda config: config.__setitem__("max_tokens", True)),
            ("boolean temperature", lambda config: config.__setitem__("temperature", True)),
            ("oversized temperature", lambda config: config.__setitem__("temperature", 10**4000)),
            ("unpinned sandbox", lambda config: config.__setitem__("software_sandbox_image", "ppe:latest")),
            ("unknown field", lambda config: config.__setitem__("unknown", "value")),
            ("missing field", lambda config: config.pop("package_version")),
        )
        for name, mutate in mutations:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                runs = [
                    create_run(root, f"run-{index}", ["professional_writing"])
                    for index in range(1, 4)
                ]
                manifest_path = runs[0] / "run-manifest.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                mutate(manifest["configuration"])
                manifest["manifest_sha256"] = hash_payload(
                    manifest,
                    "manifest_sha256",
                )
                write_json(manifest_path, manifest)

                with self.assertRaisesRegex(
                    ValueError,
                    "run (?:.*configuration|temperature|software_sandbox_image|"
                    "candidate_count|blind_seed|generation_seed|max_tokens)",
                ):
                    aggregate_benchmark_replicates(runs)

    def test_run_summary_boolean_gates_reject_integer_aliases(self):
        mutations = (
            (
                "all domains",
                lambda summary: summary.__setitem__("all_domains_pass", 1),
                "summary all_domains_pass must be a boolean",
            ),
            (
                "aggregate",
                lambda summary: summary.__setitem__("aggregate_gate_passed", 0),
                "summary aggregate_gate_passed must be a boolean",
            ),
            (
                "domain",
                lambda summary: summary["domain_results"][
                    "professional_writing"
                ].__setitem__("gate_passed", 1),
                "summary domain result gate_passed must be a boolean",
            ),
        )
        for name, mutate, expected in mutations:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                runs = [
                    create_run(root, f"run-{index}", ["professional_writing"])
                    for index in range(1, 4)
                ]
                summary_path = runs[0] / "summary.json"
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
                mutate(summary)
                summary["summary_sha256"] = hash_payload(
                    summary,
                    "summary_sha256",
                )
                write_json(summary_path, summary)

                with self.assertRaisesRegex(ValueError, expected):
                    aggregate_benchmark_replicates(
                        runs,
                    receipt_verifier=TRUSTED_FIXTURE_RECEIPTS,
                )

    def test_authority_fails_closed_for_oversized_summary_number(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runs = [
                create_run(root, f"run-{index}", ["professional_writing"])
                for index in range(1, 4)
            ]
            report = aggregate_benchmark_replicates(
                runs,
                receipt_verifier=TRUSTED_FIXTURE_RECEIPTS,
            )
            summary_path = runs[0] / "summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            summary["net_improvement"] = 10**4000
            summary["summary_sha256"] = hash_payload(
                summary,
                "summary_sha256",
            )
            write_json(summary_path, summary)

            failures = validate_e3_authority(
                report,
                runs,
                receipt_verifier=TRUSTED_FIXTURE_RECEIPTS,
            )

            self.assertTrue(
                any(
                    "source run authority is invalid" in failure
                    and "net_improvement is invalid" in failure
                    for failure in failures
                )
            )

    def test_rehashed_evaluation_count_boolean_alias_cannot_enter_e3(self):
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
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runs = [
                create_run(root, f"run-{index}", domains)
                for index in range(1, 4)
            ]
            original_report = aggregate_benchmark_replicates(
                runs,
                receipt_verifier=TRUSTED_FIXTURE_RECEIPTS,
            )
            self.assertTrue(original_report["aggregate"]["release_gate_passed"])
            self.assertEqual(original_report["evidence"]["level"], "E3")

            path = runs[0] / "professional_writing" / "evaluation.json"
            evaluation = json.loads(path.read_text(encoding="utf-8"))
            evaluation["critical_regressions"] = False
            evaluation["evaluation_sha256"] = hash_payload(
                evaluation,
                "evaluation_sha256",
            )
            write_json(path, evaluation)

            with self.assertRaisesRegex(
                ValueError,
                "evaluation critical_regressions must be a non-negative integer",
            ):
                aggregate_benchmark_replicates(
                    runs,
                    receipt_verifier=TRUSTED_FIXTURE_RECEIPTS,
                )

    def test_rehashed_evaluation_provenance_drift_is_rejected(self):
        mutations = (
            (
                "domain",
                lambda record: record.__setitem__("domain", "other-domain"),
                "domain mismatch",
            ),
            (
                "execution config",
                lambda record: record["execution_config"].__setitem__(
                    "model", "other-model"
                ),
                "execution settings|execution configuration mismatch",
            ),
            (
                "blind seed",
                lambda record: record["blind_map"].__setitem__("seed", 99),
                "blind map does not match its seed|blind seed mismatch",
            ),
        )
        for name, mutate, expected in mutations:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                runs = [
                    create_run(root, f"run-{index}", ["professional_writing"])
                    for index in range(1, 4)
                ]
                path = runs[0] / "professional_writing" / "evaluation.json"
                evaluation = json.loads(path.read_text(encoding="utf-8"))
                mutate(evaluation["records"][0])
                evaluation["records"][0]["record_sha256"] = hash_payload(
                    evaluation["records"][0],
                    "record_sha256",
                )
                evaluation["evaluation_sha256"] = hash_payload(
                    evaluation,
                    "evaluation_sha256",
                )
                write_json(path, evaluation)

                with self.assertRaisesRegex(ValueError, expected):
                    aggregate_benchmark_replicates(runs)

    def test_boolean_execution_config_cannot_match_integer_run_config(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runs = [
                create_run(root, f"run-{index}", ["professional_writing"])
                for index in range(1, 4)
            ]
            for run in runs:
                manifest_path = run / "run-manifest.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest["configuration"]["max_tokens"] = 1

                artifact = json.loads(
                    (run / "professional_writing" / "optimization.json").read_text(
                        encoding="utf-8"
                    )
                )
                evaluation_path = run / "professional_writing" / "evaluation.json"
                evaluation = json.loads(
                    evaluation_path.read_text(encoding="utf-8")
                )
                for record in evaluation["records"]:
                    record["execution_config"]["max_tokens"] = True
                    record["record_sha256"] = hash_payload(
                        record,
                        "record_sha256",
                    )
                evaluation["evaluation_sha256"] = hash_payload(
                    evaluation,
                    "evaluation_sha256",
                )
                write_json(evaluation_path, evaluation)

                TRUSTED_FIXTURE_RECEIPTS.register(
                    replicate_id=manifest["configuration"]["replicate_id"],
                    configuration=manifest["configuration"],
                    artifacts={"professional_writing": artifact},
                    evaluations={"professional_writing": evaluation},
                )
                manifest["manifest_sha256"] = hash_payload(
                    manifest,
                    "manifest_sha256",
                )
                write_json(manifest_path, manifest)

                summary_path = run / "summary.json"
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
                summary["run_manifest_sha256"] = manifest["manifest_sha256"]
                summary["summary_sha256"] = hash_payload(
                    summary,
                    "summary_sha256",
                )
                write_json(summary_path, summary)

            with self.assertRaisesRegex(ValueError, "execution config max_tokens"):
                aggregate_benchmark_replicates(
                    runs,
                    receipt_verifier=TRUSTED_FIXTURE_RECEIPTS,
                )

    def test_rehashed_definition_fields_and_hard_checks_are_replayed(self):
        mutations = (
            (
                "rubric",
                lambda record: record.__setitem__(
                    "rubric", ["Attacker-replaced review criterion."]
                ),
                "case contract does not match",
            ),
            (
                "hard check detail",
                lambda record: record["hard_checks"]["optimized"]["checks"][0].__setitem__(
                    "detail", "Attacker-replaced deterministic result."
                ),
                "deterministic hard checks do not match",
            ),
        )
        for name, mutate, expected in mutations:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                runs = [
                    create_run(root, f"run-{index}", ["professional_writing"])
                    for index in range(1, 4)
                ]
                path = runs[0] / "professional_writing" / "evaluation.json"
                evaluation = json.loads(path.read_text(encoding="utf-8"))
                mutate(evaluation["records"][0])
                evaluation["records"][0]["record_sha256"] = hash_payload(
                    evaluation["records"][0],
                    "record_sha256",
                )
                evaluation["evaluation_sha256"] = hash_payload(
                    evaluation,
                    "evaluation_sha256",
                )
                write_json(path, evaluation)

                with self.assertRaisesRegex(ValueError, expected):
                    aggregate_benchmark_replicates(
                        runs,
                        receipt_verifier=TRUSTED_FIXTURE_RECEIPTS,
                    )

    def test_synchronized_execution_configuration_rehash_breaks_receipt_binding(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runs = [
                create_run(root, f"run-{index}", ["professional_writing"])
                for index in range(1, 4)
            ]
            for run in runs:
                manifest_path = run / "run-manifest.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest["configuration"]["temperature"] = 0.5
                manifest["manifest_sha256"] = hash_payload(
                    manifest,
                    "manifest_sha256",
                )
                write_json(manifest_path, manifest)

                evaluation_path = run / "professional_writing" / "evaluation.json"
                evaluation = json.loads(
                    evaluation_path.read_text(encoding="utf-8")
                )
                for record in evaluation["records"]:
                    record["execution_config"]["temperature"] = 0.5
                    record["record_sha256"] = hash_payload(
                        record,
                        "record_sha256",
                    )
                evaluation["evaluation_sha256"] = hash_payload(
                    evaluation,
                    "evaluation_sha256",
                )
                write_json(evaluation_path, evaluation)

                summary_path = run / "summary.json"
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
                summary["run_manifest_sha256"] = manifest["manifest_sha256"]
                summary["summary_sha256"] = hash_payload(
                    summary,
                    "summary_sha256",
                )
                write_json(summary_path, summary)

            with self.assertRaisesRegex(ValueError, "receipt is missing or invalid"):
                aggregate_benchmark_replicates(
                    runs,
                    receipt_verifier=TRUSTED_FIXTURE_RECEIPTS,
                )

    def test_synchronized_blind_seed_rehash_breaks_receipt_binding(self):
        def optimized_is_a(case_id, seed):
            return hashlib.sha256(f"{seed}:{case_id}".encode()).digest()[0] % 2 == 0

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runs = [
                create_run(root, f"run-{index}", ["professional_writing"])
                for index in range(1, 4)
            ]
            first_evaluation = json.loads(
                (runs[0] / "professional_writing" / "evaluation.json").read_text(
                    encoding="utf-8"
                )
            )
            case_ids = [record["case_id"] for record in first_evaluation["records"]]
            replacement_seed = next(
                seed
                for seed in range(43, 10_000)
                if all(
                    optimized_is_a(case_id, seed)
                    == optimized_is_a(case_id, 42)
                    for case_id in case_ids
                )
            )
            for run in runs:
                manifest_path = run / "run-manifest.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest["configuration"]["blind_seed"] = replacement_seed
                manifest["manifest_sha256"] = hash_payload(
                    manifest,
                    "manifest_sha256",
                )
                write_json(manifest_path, manifest)

                evaluation_path = run / "professional_writing" / "evaluation.json"
                evaluation = json.loads(
                    evaluation_path.read_text(encoding="utf-8")
                )
                for record in evaluation["records"]:
                    record["blind_map"]["seed"] = replacement_seed
                    record["record_sha256"] = hash_payload(
                        record,
                        "record_sha256",
                    )
                evaluation["evaluation_sha256"] = hash_payload(
                    evaluation,
                    "evaluation_sha256",
                )
                write_json(evaluation_path, evaluation)

                summary_path = run / "summary.json"
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
                summary["run_manifest_sha256"] = manifest["manifest_sha256"]
                summary["summary_sha256"] = hash_payload(
                    summary,
                    "summary_sha256",
                )
                write_json(summary_path, summary)

            with self.assertRaisesRegex(ValueError, "receipt is missing or invalid"):
                aggregate_benchmark_replicates(
                    runs,
                    receipt_verifier=TRUSTED_FIXTURE_RECEIPTS,
                )

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

    def test_independent_receipts_allow_identical_semantic_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runs = [
                create_run(root, f"run-{index}", ["professional_writing"])
                for index in range(1, 4)
            ]
            source = runs[0] / "professional_writing"
            for suffix, target_root in enumerate(runs[1:], start=2):
                target = target_root / "professional_writing"
                artifact = json.loads(
                    (source / "optimization.json").read_text(encoding="utf-8")
                )
                for call_index, call in enumerate(
                    artifact["runtime"]["model_calls"],
                    start=1,
                ):
                    call["elapsed_ms"] = suffix
                    call["response_id"] = f"copied-artifact-{suffix}-{call_index}"
                artifact["artifact_payload_sha256"] = hash_payload(
                    artifact,
                    "artifact_payload_sha256",
                )
                write_json(target / "optimization.json", artifact)

                evaluation = json.loads(
                    (source / "evaluation.json").read_text(encoding="utf-8")
                )
                identity_index = 0
                for record in evaluation["records"]:
                    metadata_records = [
                        *record["execution_metadata"].values(),
                        *(item["metadata"] for item in record["judge_decisions"]),
                    ]
                    for metadata in metadata_records:
                        identity_index += 1
                        metadata["elapsed_ms"] = suffix
                        metadata["response_id"] = (
                            f"copied-evaluation-{suffix}-{identity_index}"
                        )
                    record["record_sha256"] = hash_payload(record, "record_sha256")
                evaluation["evaluation_sha256"] = hash_payload(
                    evaluation,
                    "evaluation_sha256",
                )
                write_json(target / "evaluation.json", evaluation)
                configuration = json.loads(
                    (target_root / "run-manifest.json").read_text(encoding="utf-8")
                )["configuration"]
                TRUSTED_FIXTURE_RECEIPTS.register(
                    replicate_id=configuration["replicate_id"],
                    configuration=configuration,
                    artifacts={"professional_writing": artifact},
                    evaluations={"professional_writing": evaluation},
                )

            report = aggregate_benchmark_replicates(
                runs,
                receipt_verifier=TRUSTED_FIXTURE_RECEIPTS,
            )
            replicates = report["replicates"]
            self.assertEqual(
                len({item["payload_fingerprint_sha256"] for item in replicates}),
                1,
            )
            self.assertEqual(
                len({item["execution_identity_sha256"] for item in replicates}),
                3,
            )
            self.assertEqual(
                len({item["receipt_bundle_sha256"] for item in replicates}),
                3,
            )
            self.assertEqual(
                validate_replicate_sources(
                    report,
                    runs,
                    receipt_verifier=TRUSTED_FIXTURE_RECEIPTS,
                ),
                [],
            )

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
                        CompletionResponse(
                            text=json.dumps(
                                {
                                    "optimized_prompt": (
                                        "A different but valid optimized prompt with "
                                        "sufficient detail."
                                    )
                                }
                            ),
                            provider="openai-codex",
                            model="model-a",
                            response_id=response_id(),
                            usage={"total_tokens": 1},
                        )
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

            missing_source_hash = copy.deepcopy(report)
            missing_source_hash["replicates"][0]["summary_sha256"] = None
            missing_source_hash["report_sha256"] = hash_payload(
                missing_source_hash,
                "report_sha256",
            )
            self.assertTrue(
                any(
                    "invalid manifest or summary hash" in failure
                    for failure in validate_replicate_report(missing_source_hash)
                )
            )

    def test_rehashed_report_rejects_boolean_integer_aliases_at_every_level(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runs = [
                create_run(root, f"run-{index}", ["professional_writing"])
                for index in range(1, 4)
            ]
            report = aggregate_benchmark_replicates(runs)
            mutations = {
                "replicate": lambda value: value["replicates"][0].__setitem__(
                    "domain_count", True
                ),
                "coverage": lambda value: value["coverage"].__setitem__(
                    "domain_count", True
                ),
                "case outcome": lambda value: value["cases"][0][
                    "outcome_counts"
                ].__setitem__("loss", False),
                "domain": lambda value: value["domain_results"][
                    "professional_writing"
                ].__setitem__("critical_regressions", False),
                "domain replicate": lambda value: value["domain_results"][
                    "professional_writing"
                ]["replicate_results"][0].__setitem__("ties", False),
                "aggregate": lambda value: value["aggregate"].__setitem__(
                    "unstable_cases", False
                ),
            }

            for label, mutate in mutations.items():
                with self.subTest(label=label):
                    tampered = copy.deepcopy(report)
                    mutate(tampered)
                    tampered["report_sha256"] = hash_payload(
                        tampered,
                        "report_sha256",
                    )
                    self.assertTrue(validate_replicate_report(tampered))

    def test_empty_replicates_fail_closed_without_division_by_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runs = [
                create_run(root, f"run-{index}", ["professional_writing"])
                for index in range(1, 4)
            ]
            report = aggregate_benchmark_replicates(runs)
            report["replicates"] = []
            report["replicate_count"] = 0
            for case in report["cases"]:
                case["observations"] = []
            report["report_sha256"] = hash_payload(report, "report_sha256")

            failures = validate_replicate_report(report)

            self.assertIn(
                "replicate report must contain source replicates",
                failures,
            )

    def test_rehashed_top_level_target_cannot_escape_compatibility_binding(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runs = [
                create_run(root, f"run-{index}", ["professional_writing"])
                for index in range(1, 4)
            ]
            report = aggregate_benchmark_replicates(runs)
            report["suite_id"] = "different-suite"
            report["benchmark_definition_sha256"] = "f" * 64
            report["report_sha256"] = hash_payload(report, "report_sha256")

            failures = validate_replicate_report(report)

            self.assertIn(
                "replicate suite does not match compatibility configuration",
                failures,
            )
            self.assertIn(
                "replicate benchmark definition does not match compatibility configuration",
                failures,
            )

    def test_rehashed_top_level_target_rejects_synchronized_type_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runs = [
                create_run(root, f"run-{index}", ["professional_writing"])
                for index in range(1, 4)
            ]
            report = aggregate_benchmark_replicates(runs)
            mutations = (
                ("suite_id", [], "replicate suite_id must be a non-empty string"),
                (
                    "benchmark_definition_sha256",
                    {},
                    "replicate benchmark definition hash is invalid",
                ),
            )
            for field, value, expected in mutations:
                with self.subTest(field=field):
                    tampered = copy.deepcopy(report)
                    tampered[field] = value
                    tampered["compatibility_configuration"][field] = copy.deepcopy(
                        value
                    )
                    tampered["compatibility_sha256"] = sha256_json(
                        tampered["compatibility_configuration"]
                    )
                    tampered["report_sha256"] = hash_payload(
                        tampered,
                        "report_sha256",
                    )
                    self.assertIn(expected, validate_replicate_report(tampered))

    def test_source_authority_rejects_wrong_directory_container_types(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runs = [
                create_run(root, f"run-{index}", ["professional_writing"])
                for index in range(1, 4)
            ]
            report = aggregate_benchmark_replicates(runs)
            malformed_values = (1, {}, "run", [1], [{}])
            for malformed in malformed_values:
                with self.subTest(malformed=malformed):
                    expected = (
                        "source run directories must be a sequence of Path values"
                    )
                    self.assertIn(
                        expected,
                        validate_replicate_sources(report, malformed),
                    )
                    self.assertIn(
                        expected,
                        validate_e3_authority(report, malformed),
                    )

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
                    [
                        "validate-benchmark-replicates",
                        str(report_path),
                        *(str(path) for path in runs),
                    ]
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
