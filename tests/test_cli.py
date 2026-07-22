import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from prompt_performance_engine.adapters import AdapterError, AdapterQuotaError
from prompt_performance_engine.cli import _service_token, cli_main, main
from prompt_performance_engine.hashing import hash_payload
from prompt_performance_engine.readiness import build_readiness_manifest
from prompt_performance_engine.validation import validate_artifact


class CliTests(unittest.TestCase):
    def test_service_token_allows_local_only_only_when_option_is_omitted(self):
        self.assertIsNone(_service_token("127.0.0.1", None))

    def test_service_token_rejects_missing_or_blank_explicit_environment(self):
        environment_name = "PPE_TEST_SERVICE_TOKEN"
        for value in (None, "", " \t\r\n "):
            with self.subTest(value=value):
                environment = {} if value is None else {environment_name: value}
                with mock.patch.dict(os.environ, environment, clear=True):
                    with self.assertRaisesRegex(
                        ValueError,
                        "Authentication token environment variable.*missing or empty",
                    ):
                        _service_token("127.0.0.1", environment_name)

    def test_service_token_returns_trimmed_explicit_token(self):
        with mock.patch.dict(
            os.environ,
            {"PPE_TEST_SERVICE_TOKEN": "  service-secret  "},
            clear=True,
        ):
            self.assertEqual(
                _service_token("localhost", "PPE_TEST_SERVICE_TOKEN"),
                "service-secret",
            )

    def test_serve_openai_rejects_missing_explicit_auth_before_startup(self):
        environment_name = "PPE_TEST_MISSING_SERVICE_TOKEN"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with mock.patch.dict(os.environ, {}, clear=True), mock.patch(
                "prompt_performance_engine.cli.OptimizationService"
            ) as service, mock.patch(
                "prompt_performance_engine.cli.create_http_server"
            ) as create_server:
                with self.assertRaisesRegex(
                    ValueError,
                    "Authentication token environment variable.*missing or empty",
                ):
                    main(
                        [
                            "serve-openai",
                            "--model",
                            "test-model",
                            "--auth-token-env",
                            environment_name,
                            "--db",
                            str(root / "jobs.sqlite3"),
                            "--artifacts",
                            str(root / "artifacts"),
                        ]
                    )

            service.assert_not_called()
            create_server.assert_not_called()

    def test_human_review_cli_passes_replicate_authority(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evaluation_path = root / "evaluation.json"
            replicate_path = root / "replicate.json"
            packet_path = root / "packet.json"
            key_path = root / "key.json"
            run_paths = [root / f"run-{name}" for name in ("a", "b", "c")]
            for run_path in run_paths:
                run_path.mkdir()
            evaluation_path.write_text('{"evaluation": true}', encoding="utf-8")
            replicate_path.write_text('{"report": true}', encoding="utf-8")
            packet = {"items": [], "packet_sha256": "a" * 64}
            key = {"key_sha256": "b" * 64}

            with mock.patch(
                "prompt_performance_engine.cli.create_reviewer_packet",
                return_value=(packet, key),
            ) as create, redirect_stdout(io.StringIO()):
                exit_code = main(
                    [
                        "create-review-packet",
                        str(evaluation_path),
                        "--replicate-report",
                        str(replicate_path),
                        "--run-directory",
                        str(run_paths[0]),
                        "--reviewer",
                        "reviewer-1",
                        "--packet",
                        str(packet_path),
                        "--key",
                        str(key_path),
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertEqual(
                create.call_args.kwargs["replicate_report"],
                {"report": True},
            )
            self.assertEqual(create.call_args.args, ([{"evaluation": True}],))

            submission_path = root / "submission.json"
            submission_path.write_text('{"submission": true}', encoding="utf-8")
            plan_path = root / "plan.json"
            plan_path.write_text(
                json.dumps(
                    {
                        "schema_version": "2.0.0",
                        "replicate_report": replicate_path.name,
                        "run_directories": [path.name for path in run_paths],
                        "evaluations": [evaluation_path.name],
                        "reviews": [
                            {
                                "packet": packet_path.name,
                                "key": key_path.name,
                                "submission": submission_path.name,
                            }
                        ]
                        * 3,
                    }
                ),
                encoding="utf-8",
            )
            report_path = root / "human-report.json"
            human_report = {
                "reviewer_count": 3,
                "reviewed_case_count": 24,
                "evidence": {"level": "E4"},
            }
            with mock.patch(
                "prompt_performance_engine.cli.aggregate_human_review",
                return_value=human_report,
            ) as aggregate, redirect_stdout(io.StringIO()):
                exit_code = main(
                    [
                        "aggregate-human-review",
                        str(plan_path),
                        "--output",
                        str(report_path),
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertEqual(
                aggregate.call_args.kwargs["replicate_report"],
                {"report": True},
            )
            self.assertEqual(
                aggregate.call_args.kwargs["run_directories"],
                [path.resolve() for path in run_paths],
            )

    def test_recorded_software_evaluation_requires_sandbox_image(self):
        root = Path(__file__).resolve().parents[1]
        missing = root / "does-not-need-to-exist-for-this-check"
        with self.assertRaisesRegex(ValueError, "--sandbox-image is required"):
            main(
                [
                    "evaluate-recorded",
                    str(root / "benchmark" / "catalog-60.json"),
                    "software-engineering",
                    str(missing),
                    str(missing),
                    str(missing),
                    "--output",
                    str(missing),
                ]
            )

    def test_cli_quota_failure_is_structured_without_traceback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prompt = root / "prompt.txt"
            response = root / "response.txt"
            prompt.write_text("Write a report.", encoding="utf-8")
            response.write_text("unused", encoding="utf-8")
            stderr = io.StringIO()
            with mock.patch(
                "prompt_performance_engine.cli.optimize",
                side_effect=AdapterQuotaError("usage limit; retry later"),
            ), redirect_stderr(stderr):
                exit_code = cli_main(
                    [
                        "optimize",
                        str(prompt),
                        "--mock-response",
                        str(response),
                    ]
                )
            payload = json.loads(stderr.getvalue())
            self.assertEqual(exit_code, 75)
            self.assertEqual(payload["category"], "quota")
            self.assertTrue(payload["retryable"])
            self.assertNotIn("Traceback", stderr.getvalue())

    def test_cli_adapter_failure_is_structured_without_traceback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prompt = root / "prompt.txt"
            response = root / "response.txt"
            prompt.write_text("Write a report.", encoding="utf-8")
            response.write_text("unused", encoding="utf-8")
            stderr = io.StringIO()
            with mock.patch(
                "prompt_performance_engine.cli.optimize",
                side_effect=AdapterError("sanitized provider failure"),
            ), redirect_stderr(stderr):
                exit_code = cli_main(
                    [
                        "optimize",
                        str(prompt),
                        "--mock-response",
                        str(response),
                    ]
                )
            payload = json.loads(stderr.getvalue())
            self.assertEqual(exit_code, 1)
            self.assertEqual(payload["category"], "adapter")
            self.assertFalse(payload["retryable"])
            self.assertNotIn("Traceback", stderr.getvalue())

    def test_cli_missing_input_is_structured_without_traceback(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing-evaluation.json"
            stderr = io.StringIO()

            with redirect_stderr(stderr):
                exit_code = cli_main(
                    ["validate-evaluation", str(missing)]
                )

            payload = json.loads(stderr.getvalue())
            self.assertEqual(exit_code, 2)
            self.assertEqual(payload["category"], "input")
            self.assertFalse(payload["retryable"])
            self.assertIn("missing-evaluation.json", payload["message"])
            self.assertNotIn("Traceback", stderr.getvalue())
    def test_mock_optimize_prints_copyable_prompt_not_wrapper(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prompt = root / "prompt.txt"
            response = root / "response.md"
            prompt.write_text("Write a report.", encoding="utf-8")
            response.write_text(
                json.dumps({"optimized_prompt": "Complete optimized Prompt."}),
                encoding="utf-8",
            )
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = main(
                    [
                        "optimize",
                        str(prompt),
                        "--mock-response",
                        str(response),
                    ]
                )
            self.assertEqual(exit_code, 0)
            self.assertEqual(stdout.getvalue().strip(), "Complete optimized Prompt.")

    def test_optimize_creates_artifact_parent_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prompt = root / "prompt.txt"
            response = root / "response.md"
            artifact = root / "nested" / "artifact.json"
            prompt.write_text("Write a report.", encoding="utf-8")
            response.write_text(
                json.dumps({"optimized_prompt": "Complete optimized Prompt."}),
                encoding="utf-8",
            )
            with redirect_stdout(io.StringIO()):
                exit_code = main(
                    [
                        "optimize",
                        str(prompt),
                        "--mock-response",
                        str(response),
                        "--artifact",
                        str(artifact),
                    ]
                )
            self.assertEqual(exit_code, 0)
            self.assertTrue(artifact.is_file())

    def test_validate_artifact_rejects_near_integer_precision_loss(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prompt = root / "prompt.txt"
            response = root / "response.json"
            artifact = root / "artifact.json"
            prompt.write_text("Write a report.", encoding="utf-8")
            response.write_text(
                json.dumps({"optimized_prompt": "Complete optimized Prompt."}),
                encoding="utf-8",
            )
            with redirect_stdout(io.StringIO()):
                self.assertEqual(
                    main(
                        [
                            "optimize",
                            str(prompt),
                            "--mock-response",
                            str(response),
                            "--artifact",
                            str(artifact),
                        ]
                    ),
                    0,
                )

            data = json.loads(artifact.read_text(encoding="utf-8"))
            data["runtime"]["total_calls"] = 1.0
            data["artifact_payload_sha256"] = hash_payload(
                data,
                "artifact_payload_sha256",
            )
            rendered = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
            exact_float = '"total_calls":1.0'
            self.assertIn(exact_float, rendered)
            artifact.write_text(
                rendered.replace(
                    exact_float,
                    '"total_calls":1.00000000000000001',
                    1,
                ),
                encoding="utf-8",
            )

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = main(["validate-artifact", str(artifact)])

            report = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 1)
            self.assertFalse(report["valid"])
            self.assertEqual(report["violations"][0]["rule_id"], "A18")
            self.assertIn(
                "without precision loss",
                report["violations"][0]["detail"],
            )

    def test_mock_optimize_records_three_candidate_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prompt = root / "prompt.txt"
            artifact = root / "artifact.json"
            prompt.write_text("Write a report.", encoding="utf-8")
            responses = []
            for index in range(1, 4):
                response = root / f"candidate-{index}.txt"
                response.write_text(
                    json.dumps({"optimized_prompt": f"Candidate {index}."}),
                    encoding="utf-8",
                )
                responses.append(response)
            selector = root / "selector.json"
            selector.write_text('{"selected_index": 2}', encoding="utf-8")

            argv = [
                "optimize",
                str(prompt),
                "--candidate-count",
                "3",
                "--artifact",
                str(artifact),
            ]
            for response in [*responses, selector]:
                argv.extend(["--mock-response", str(response)])
            with redirect_stdout(io.StringIO()):
                exit_code = main(argv)

            self.assertEqual(exit_code, 0)
            data = json.loads(artifact.read_text(encoding="utf-8"))
            self.assertEqual(validate_artifact(data), [])
            self.assertEqual(data["runtime"]["selection"]["selected_index"], 2)
            self.assertEqual(data["runtime"]["selection"]["candidate_count"], 3)

    def test_external_command_end_to_end(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prompt = root / "prompt.txt"
            permissions = root / "permissions.json"
            artifact = root / "artifact.json"
            prompt.write_text("Write a report.", encoding="utf-8")
            permissions.write_text(
                json.dumps(
                    {
                        "schema_version": "2.0.0",
                        "allowed_executables": [sys.executable],
                        "maximum_timeout_seconds": 2,
                    }
                ),
                encoding="utf-8",
            )
            code = (
                "import json,sys;"
                "json.load(sys.stdin);"
                "print(json.dumps({'output_text':json.dumps({"
                "'optimized_prompt':'Complete command Prompt.'}),"
                "'usage':{'total_tokens':5}}))"
            )
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = main(
                    [
                        "optimize-command",
                        str(prompt),
                        "--permissions",
                        str(permissions),
                        "--timeout",
                        "1",
                        "--artifact",
                        str(artifact),
                        "--command",
                        sys.executable,
                        "-c",
                        code,
                    ]
                )
            self.assertEqual(exit_code, 0)
            self.assertEqual(stdout.getvalue().strip(), "Complete command Prompt.")
            data = json.loads(artifact.read_text(encoding="utf-8"))
            self.assertEqual(validate_artifact(data), [])
            self.assertEqual(data["runtime"]["total_usage"]["total_tokens"], 5)

    def test_readiness_completion_gate_returns_failure_for_missing_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = build_readiness_manifest([])
            path = root / "readiness-manifest.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                exit_code = main(
                    [
                        "assess-readiness",
                        str(path),
                        "--require-complete",
                    ]
                )

            report = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 1)
            self.assertEqual(report["status"], "incomplete")


if __name__ == "__main__":
    unittest.main()
