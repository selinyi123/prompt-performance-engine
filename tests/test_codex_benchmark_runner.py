import importlib.util
import json
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from unittest.mock import Mock, patch

from prompt_performance_engine.adapters import AdapterError, AdapterQuotaError
from prompt_performance_engine.hashing import hash_payload


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "run_codex_benchmark",
    ROOT / "scripts" / "run_codex_benchmark.py",
)
assert SPEC is not None and SPEC.loader is not None
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


class CodexBenchmarkRunnerTests(unittest.TestCase):
    def test_software_domain_requires_sandbox_before_model_calls(self):
        stderr = StringIO()
        with patch.object(
            sys,
            "argv",
            [
                "run_codex_benchmark.py",
                "--domains",
                "software_engineering",
            ],
        ), patch.object(RUNNER, "DockerSandbox") as sandbox, redirect_stderr(
            stderr
        ):
            with self.assertRaises(SystemExit) as context:
                RUNNER.main()
        self.assertEqual(context.exception.code, 2)
        self.assertIn("--sandbox-image is required", stderr.getvalue())
        sandbox.assert_not_called()

    def test_empty_domain_selection_is_a_usage_error(self):
        stderr = StringIO()
        with patch.object(
            sys,
            "argv",
            ["run_codex_benchmark.py", "--domains", ",,"],
        ), redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as context:
                RUNNER.main()
        self.assertEqual(context.exception.code, 2)
        self.assertIn("must contain at least one domain id", stderr.getvalue())

    def test_output_path_rejects_domain_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "artifacts"
            with self.assertRaisesRegex(ValueError, "escapes the output directory"):
                RUNNER.write_run_failure(
                    root,
                    domain="../../escaped",
                    phase="optimization",
                    error=AdapterError("failed"),
                    run_manifest_sha256="a" * 64,
                )

    def test_model_workspace_is_empty_and_outside_the_checkout(self):
        owner, workspace = RUNNER.create_isolated_model_workspace()
        try:
            self.assertTrue(workspace.is_dir())
            self.assertEqual(list(workspace.iterdir()), [])
            with self.assertRaises(ValueError):
                workspace.relative_to(ROOT.resolve())
        finally:
            owner.cleanup()

    def test_main_cleans_model_workspace_on_usage_error(self):
        owner = Mock()
        with patch.object(
            RUNNER,
            "create_isolated_model_workspace",
            return_value=(owner, Path("unused-model-workspace")),
        ), patch.object(
            sys,
            "argv",
            ["run_codex_benchmark.py", "--domains", ",,"],
        ), redirect_stderr(StringIO()):
            with self.assertRaises(SystemExit):
                RUNNER.main()

        owner.cleanup.assert_called_once_with()

    def test_cleanup_error_does_not_mask_quota_failure(self):
        owner = Mock()
        owner.cleanup.side_effect = PermissionError("workspace still locked")
        stderr = StringIO()
        with patch.object(
            RUNNER,
            "create_isolated_model_workspace",
            return_value=(owner, Path("unused-model-workspace")),
        ), patch.object(
            RUNNER,
            "_main",
            side_effect=AdapterQuotaError("quota"),
        ), redirect_stderr(stderr):
            exit_code = RUNNER.cli_main()

        payload = json.loads(stderr.getvalue())
        self.assertEqual(exit_code, 75)
        self.assertEqual(payload["category"], "quota")
        owner.cleanup.assert_called_once_with()

    def test_default_output_directory_tracks_protocol_version(self):
        self.assertEqual(
            RUNNER.default_output_directory(),
            ROOT / "artifacts" / "codex-benchmark-v26",
        )

    def test_atomic_json_writes_are_safe_under_concurrency(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "summary.json"
            payloads = [{"writer": index, "value": "x" * 1000} for index in range(20)]
            with ThreadPoolExecutor(max_workers=8) as executor:
                list(executor.map(lambda payload: RUNNER.write_json(path, payload), payloads))

            result = json.loads(path.read_text(encoding="utf-8"))
            self.assertIn(result, payloads)
            self.assertEqual(list(root.glob("*.tmp")), [])

    def test_run_manifest_rejects_configuration_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            RUNNER.ensure_run_manifest(root, {"model": "model-a"})
            with self.assertRaisesRegex(ValueError, "different benchmark"):
                RUNNER.ensure_run_manifest(root, {"model": "model-b"})

    def test_run_configuration_binds_optimizer_and_package_version(self):
        configuration = RUNNER.build_run_configuration(
            suite_id="suite",
            benchmark_definition_sha256="a" * 64,
            model="model-a",
            reasoning_effort="low",
            candidate_count=1,
            replicate_id="replicate-a",
        )

        self.assertEqual(len(configuration["optimizer_prompt_sha256"]), 64)
        self.assertEqual(len(configuration["domain_profiles_sha256"]), 64)
        self.assertEqual(
            len(configuration["evaluation_implementation_sha256"]),
            64,
        )
        self.assertTrue(configuration["python_version"])
        self.assertTrue(configuration["platform_system"])
        self.assertEqual(
            configuration["package_version"],
            RUNNER.PACKAGE_VERSION,
        )
        self.assertEqual(
            configuration["evaluation_protocol"],
            RUNNER.EVALUATION_PROTOCOL,
        )
        self.assertEqual(configuration["replicate_id"], "replicate-a")
        self.assertIsNone(configuration["software_sandbox_image"])

    def test_implementation_hash_binds_adapter_source(self):
        relative = {
            path.relative_to(ROOT).as_posix()
            for path in RUNNER.evaluation_implementation_paths()
        }
        self.assertIn("scripts/run_codex_benchmark.py", relative)
        self.assertIn("src/prompt_performance_engine/adapters.py", relative)
        self.assertIn("src/prompt_performance_engine/runtime.py", relative)

    def test_quota_failure_is_written_as_hashed_retryable_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = RUNNER.write_run_failure(
                root,
                domain="research_analysis",
                phase="optimization",
                error=AdapterQuotaError("usage limit; try again at 02:17"),
                run_manifest_sha256="a" * 64,
            )
            stored = json.loads(
                (root / "failures" / "research_analysis.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(stored, report)
            self.assertEqual(stored["category"], "quota")
            self.assertTrue(stored["retryable"])
            self.assertEqual(stored["run_manifest_sha256"], "a" * 64)
            self.assertEqual(
                stored["failure_sha256"],
                hash_payload(stored, "failure_sha256"),
            )

    def test_cli_quota_failure_uses_temporary_failure_exit_without_traceback(self):
        stderr = StringIO()
        with patch.object(
            RUNNER,
            "main",
            side_effect=AdapterQuotaError("usage limit; try again later"),
        ), redirect_stderr(stderr):
            exit_code = RUNNER.cli_main()
        payload = json.loads(stderr.getvalue())
        self.assertEqual(exit_code, 75)
        self.assertEqual(payload["category"], "quota")
        self.assertTrue(payload["retryable"])

    def test_cli_adapter_failure_is_structured_without_traceback(self):
        stderr = StringIO()
        with patch.object(
            RUNNER,
            "main",
            side_effect=AdapterError("adapter failed"),
        ), redirect_stderr(stderr):
            exit_code = RUNNER.cli_main()
        payload = json.loads(stderr.getvalue())
        self.assertEqual(exit_code, 1)
        self.assertEqual(payload["category"], "adapter")
        self.assertFalse(payload["retryable"])

    def test_actual_usage_counts_bound_metadata_and_ignores_orphan_cache_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            domain = root / "software_engineering"
            cache = domain / "cache" / "executions"
            cache.mkdir(parents=True)
            (domain / "optimization.json").write_text(
                json.dumps(
                    {
                        "runtime": {
                            "model_calls": [
                                {
                                    "provider": "test",
                                    "model": "model-a",
                                    "response_id": None,
                                    "usage": {
                                        "input_tokens": 100,
                                        "output_tokens": 10,
                                    },
                                    "attempts": 1,
                                    "elapsed_ms": 1,
                                    "status": "completed",
                                    "purpose": "prompt_optimization",
                                    "request_sha256": "a" * 64,
                                    "response_sha256": "b" * 64,
                                }
                            ],
                        }
                    }
                ),
                encoding="utf-8",
            )
            (cache / "call.json").write_text(
                json.dumps(
                    {
                        "metadata": {
                            "usage": {
                                "input_tokens": 200,
                                "output_tokens": 20,
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            (domain / "evaluation.json").write_text(
                json.dumps(
                    {
                        "records": [
                            {
                                "execution_metadata": {
                                    "original": {
                                        "provider": "test",
                                        "model": "model-a",
                                        "response_id": None,
                                        "usage": {
                                            "input_tokens": 999,
                                            "output_tokens": 999,
                                        },
                                        "attempts": 1,
                                        "elapsed_ms": 1,
                                        "status": "completed",
                                        "purpose": "benchmark_execution",
                                        "request_sha256": "c" * 64,
                                        "response_sha256": "d" * 64,
                                    }
                                }
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            usage = RUNNER.actual_usage_from_tree(root)

            self.assertEqual(usage["actual_model_calls"], 2)
            self.assertEqual(usage["input_tokens"], 1099)
            self.assertEqual(usage["output_tokens"], 1009)

    def test_single_full_passing_summary_is_capped_at_e2(self):
        result = {
            "case_count": 5,
            "wins": 3,
            "ties": 2,
            "losses": 0,
            "critical_regressions": 0,
            "fatal_flaws": 0,
            "optimized_hard_failures": 0,
            "gate_passed": True,
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            RUNNER.ensure_run_manifest(
                root,
                {
                    "benchmark_definition_sha256": "a" * 64,
                    "evaluation_implementation_sha256": "b" * 64,
                    "python_version": "3.13.0",
                    "platform_system": "TestOS",
                },
            )
            summary = RUNNER.build_summary(
                "suite",
                {f"domain-{index}": result for index in range(12)},
                root,
            )

        self.assertTrue(summary["aggregate_gate_passed"])
        self.assertEqual(summary["evidence"]["level"], "E2")
        self.assertEqual(
            summary["evaluation_protocol"]["version"],
            RUNNER.EVALUATION_PROTOCOL,
        )
        self.assertFalse(summary["evaluation_protocol"]["cross_model"])
        self.assertFalse(summary["evaluation_protocol"]["repeated_run"])
        self.assertEqual(
            summary["evaluation_protocol"]["implementation_sha256"],
            "b" * 64,
        )
        self.assertEqual(
            summary["evaluation_protocol"]["python_version"],
            "3.13.0",
        )
        self.assertEqual(
            summary["evaluation_protocol"]["platform_system"],
            "TestOS",
        )
        self.assertEqual(summary["optimized_hard_failures"], 0)
        self.assertEqual(summary["benchmark_definition_sha256"], "a" * 64)
        self.assertEqual(len(summary["run_manifest_sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
