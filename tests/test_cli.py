import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from prompt_performance_engine.cli import main
from prompt_performance_engine.hashing import hash_payload
from prompt_performance_engine.validation import validate_artifact


class CliTests(unittest.TestCase):
    def test_mock_optimize_prints_copyable_prompt_not_wrapper(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prompt = root / "prompt.txt"
            response = root / "response.md"
            prompt.write_text("Write a report.", encoding="utf-8")
            response.write_text(
                "## Optimized Prompt\n\n```text\nComplete optimized Prompt.\n```",
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
                "## Optimized Prompt\n\n```text\nComplete optimized Prompt.\n```",
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
                    f"<optimized_prompt>Candidate {index}.</optimized_prompt>",
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
                        "schema_version": "1.0.0",
                        "allowed_executables": [sys.executable],
                        "maximum_timeout_seconds": 2,
                    }
                ),
                encoding="utf-8",
            )
            code = (
                "import json,sys;"
                "json.load(sys.stdin);"
                "print(json.dumps({'output_text':'## Optimized Prompt"
                "\\n\\n```text\\nComplete command Prompt.\\n```',"
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
            manifest = {
                "schema_version": "1.0.0",
                "artifacts": [],
            }
            manifest["manifest_sha256"] = hash_payload(
                manifest,
                "manifest_sha256",
            )
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
