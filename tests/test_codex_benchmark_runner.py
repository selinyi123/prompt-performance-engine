import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "run_codex_benchmark",
    ROOT / "scripts" / "run_codex_benchmark.py",
)
assert SPEC is not None and SPEC.loader is not None
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


class CodexBenchmarkRunnerTests(unittest.TestCase):
    def test_run_manifest_rejects_configuration_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            RUNNER.ensure_run_manifest(root, {"model": "model-a"})
            with self.assertRaisesRegex(ValueError, "different benchmark"):
                RUNNER.ensure_run_manifest(root, {"model": "model-b"})

    def test_actual_usage_does_not_count_embedded_evaluation_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            domain = root / "software_engineering"
            cache = domain / "cache" / "executions"
            cache.mkdir(parents=True)
            (domain / "optimization.json").write_text(
                json.dumps(
                    {
                        "runtime": {
                            "total_calls": 1,
                            "total_usage": {
                                "input_tokens": 100,
                                "output_tokens": 10,
                            },
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
                                        "usage": {
                                            "input_tokens": 999,
                                            "output_tokens": 999,
                                        }
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
            self.assertEqual(usage["input_tokens"], 300)
            self.assertEqual(usage["output_tokens"], 30)

    def test_full_passing_summary_can_reach_repeated_run_e3(self):
        result = {
            "case_count": 5,
            "wins": 3,
            "ties": 2,
            "losses": 0,
            "critical_regressions": 0,
            "fatal_flaws": 0,
            "gate_passed": True,
        }
        with tempfile.TemporaryDirectory() as directory:
            summary = RUNNER.build_summary(
                "suite",
                {f"domain-{index}": result for index in range(12)},
                Path(directory),
            )

        self.assertTrue(summary["aggregate_gate_passed"])
        self.assertEqual(summary["evidence"]["level"], "E3")
        self.assertEqual(
            summary["evaluation_protocol"]["version"],
            RUNNER.EVALUATION_PROTOCOL,
        )
        self.assertFalse(summary["evaluation_protocol"]["cross_model"])


if __name__ == "__main__":
    unittest.main()
