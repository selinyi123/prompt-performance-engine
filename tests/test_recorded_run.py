import copy
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest import mock

from prompt_performance_engine import benchmark_replicates
from prompt_performance_engine.benchmark import BenchmarkJob
from prompt_performance_engine.cli import main
from prompt_performance_engine.contracts import PACKAGE_ROOT
from prompt_performance_engine.evaluation import (
    RECORDED_DECISION_FIELDS,
    RECORDED_EXECUTION_CONFIG_FIELDS,
    RECORDED_JUDGE_FIELDS,
    RECORDED_OUTPUT_FIELDS,
    RECORDED_RUN_FIELDS,
    RECORDED_RUN_REQUIRED_FIELDS,
    EvaluationCase,
    validate_recorded_run_input,
)


def recorded_run() -> dict:
    decision = {
        "winner": "A",
        "reason": "A is more complete.",
        "fatal_flaw_a": False,
        "fatal_flaw_b": False,
    }
    return {
        "schema_version": "2.0.0",
        "suite_id": "suite",
        "job_id": "job",
        "blind_seed": 7,
        "execution_config": {
            "model": "recorded-model",
            "temperature": 0.0,
            "max_tokens": 2048,
            "seed": 0,
        },
        "outputs": [
            {"case_id": "case-1", "original": "a", "optimized": "b"},
            {"case_id": "case-2", "original": "c", "optimized": "d"},
        ],
        "judges": [
            {"name": "judge-1", "decisions": [copy.deepcopy(decision)]},
            {"name": "judge-2", "decisions": [copy.deepcopy(decision)]},
        ],
    }


class RecordedRunContractTests(unittest.TestCase):
    def assert_invalid(self, mutate) -> None:
        run = recorded_run()
        mutate(run)
        with self.assertRaises(ValueError):
            validate_recorded_run_input(
                run,
                suite_id="suite",
                job_id="job",
                case_ids=("case-1", "case-2"),
            )

    def test_schema_and_runtime_field_sets_match(self):
        schema = json.loads(
            (PACKAGE_ROOT / "schemas" / "evaluation-recorded-run.schema.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(set(schema["properties"]), set(RECORDED_RUN_FIELDS))
        self.assertEqual(set(schema["required"]), set(RECORDED_RUN_REQUIRED_FIELDS))
        config = schema["properties"]["execution_config"]
        self.assertEqual(
            set(config["properties"]),
            set(RECORDED_EXECUTION_CONFIG_FIELDS),
        )
        output = schema["properties"]["outputs"]["items"]
        self.assertEqual(set(output["properties"]), set(RECORDED_OUTPUT_FIELDS))
        self.assertEqual(set(output["required"]), set(RECORDED_OUTPUT_FIELDS))
        judge = schema["properties"]["judges"]["items"]
        self.assertEqual(set(judge["properties"]), set(RECORDED_JUDGE_FIELDS))
        self.assertEqual(set(judge["required"]), set(RECORDED_JUDGE_FIELDS))
        decision = judge["properties"]["decisions"]["items"]
        self.assertEqual(
            set(decision["properties"]),
            set(RECORDED_DECISION_FIELDS),
        )
        self.assertEqual(set(decision["required"]), set(RECORDED_DECISION_FIELDS))

    def test_valid_recorded_run(self):
        validate_recorded_run_input(
            recorded_run(),
            suite_id="suite",
            job_id="job",
            case_ids=("case-1", "case-2"),
        )

    def test_json_schema_integral_float_values_normalize_to_int(self):
        run = recorded_run()
        run["blind_seed"] = Decimal("7.0")
        run["execution_config"]["max_tokens"] = Decimal("2048.0")
        run["execution_config"]["seed"] = Decimal("0.0")
        run["execution_config"]["temperature"] = Decimal("0.25")
        normalized = validate_recorded_run_input(
            run,
            suite_id="suite",
            job_id="job",
            case_ids=("case-1", "case-2"),
        )
        self.assertIs(type(normalized["blind_seed"]), int)
        self.assertIs(type(normalized["execution_config"]["max_tokens"]), int)
        self.assertIs(type(normalized["execution_config"]["seed"]), int)
        self.assertIs(type(normalized["execution_config"]["temperature"]), float)

    def test_high_precision_near_integer_values_are_rejected(self):
        for field in ("blind_seed", "max_tokens", "seed"):
            run = recorded_run()
            value = Decimal("1.0000000000000000000000000001")
            if field == "blind_seed":
                run[field] = value
            else:
                run["execution_config"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_recorded_run_input(
                    run,
                    suite_id="suite",
                    job_id="job",
                    case_ids=("case-1", "case-2"),
                )

    def test_root_fields_version_bindings_and_seed_are_strict(self):
        mutations = (
            lambda run: run.__setitem__("unknown", True),
            lambda run: run.pop("schema_version"),
            lambda run: run.__setitem__("schema_version", "1.0.0"),
            lambda run: run.__setitem__("suite_id", "other"),
            lambda run: run.__setitem__("job_id", "other"),
            lambda run: run.__setitem__("blind_seed", True),
            lambda run: run.__setitem__("blind_seed", 1.5),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                self.assert_invalid(mutate)

    def test_execution_config_fields_types_and_bounds_are_strict(self):
        mutations = (
            lambda run: run["execution_config"].__setitem__("unknown", True),
            lambda run: run["execution_config"].pop("model"),
            lambda run: run["execution_config"].__setitem__("model", " "),
            lambda run: run["execution_config"].__setitem__("temperature", True),
            lambda run: run["execution_config"].__setitem__(
                "temperature", float("inf")
            ),
            lambda run: run["execution_config"].__setitem__("max_tokens", True),
            lambda run: run["execution_config"].__setitem__("max_tokens", 0),
            lambda run: run["execution_config"].__setitem__("max_tokens", 1.5),
            lambda run: run["execution_config"].__setitem__("seed", False),
            lambda run: run["execution_config"].__setitem__("seed", 1.5),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                self.assert_invalid(mutate)

    def test_outputs_require_exact_unique_complete_case_records(self):
        mutations = (
            lambda run: run.__setitem__("outputs", tuple(run["outputs"])),
            lambda run: run["outputs"].pop(),
            lambda run: run["outputs"][1].__setitem__("case_id", "case-1"),
            lambda run: run["outputs"][1].__setitem__("case_id", "unknown"),
            lambda run: run["outputs"][0].__setitem__("unknown", True),
            lambda run: run["outputs"][0].pop("optimized"),
            lambda run: run["outputs"][0].__setitem__("original", " "),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                self.assert_invalid(mutate)

    def test_judges_and_decisions_require_exact_typed_balanced_records(self):
        mutations = (
            lambda run: run["judges"].pop(),
            lambda run: run["judges"][1].__setitem__("name", "judge-1"),
            lambda run: run["judges"][1].__setitem__("decisions", []),
            lambda run: run["judges"][0].__setitem__("unknown", True),
            lambda run: run["judges"][0]["decisions"][0].pop("fatal_flaw_a"),
            lambda run: run["judges"][0]["decisions"][0].__setitem__(
                "unknown", True
            ),
            lambda run: run["judges"][0]["decisions"][0].__setitem__(
                "winner", "C"
            ),
            lambda run: run["judges"][0]["decisions"][0].__setitem__(
                "winner", []
            ),
            lambda run: run["judges"][0]["decisions"][0].__setitem__(
                "winner", {}
            ),
            lambda run: run["judges"][0]["decisions"][0].__setitem__(
                "reason", " "
            ),
            lambda run: run["judges"][0]["decisions"][0].__setitem__(
                "fatal_flaw_b", "false"
            ),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                self.assert_invalid(mutate)

    def test_cli_rejects_duplicate_fields_before_last_wins_mapping(self):
        case = EvaluationCase(
            case_id="case-1",
            input_text="A sufficiently substantive generic benchmark input.",
            rubric=("Correctness", "Usefulness", "Safety"),
        )
        job = BenchmarkJob(
            job_id="job",
            domain="generic",
            source_prompt="Original prompt.",
            cases=(case,),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "original.txt"
            optimized = root / "optimized.txt"
            source = root / "run.json"
            output = root / "evaluation.json"
            original.write_text("Original prompt.", encoding="utf-8")
            optimized.write_text("Optimized prompt.", encoding="utf-8")
            source.write_text(
                '{"schema_version":"2.0.0",'
                '"schema_version":"2.0.0"}',
                encoding="utf-8",
            )
            with mock.patch(
                "prompt_performance_engine.cli.load_benchmark_definition",
                return_value=("suite", (job,)),
            ), mock.patch(
                "prompt_performance_engine.cli.evaluate_suite"
            ) as evaluate, self.assertRaisesRegex(ValueError, "duplicate field"):
                main(
                    [
                        "evaluate-recorded",
                        str(root / "benchmark.json"),
                        "job",
                        str(original),
                        str(optimized),
                        str(source),
                        "--output",
                        str(output),
                    ]
                )
            evaluate.assert_not_called()


class AuthoritySourceLoaderTests(unittest.TestCase):
    def test_authority_loader_rejects_duplicate_fields_and_keeps_float(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.json"
            source.write_text('{"score":1.5,"score":2.5}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate field"):
                benchmark_replicates._load_json(source, "authority source")

            source.write_text('{"score":1.5}', encoding="utf-8")
            loaded = benchmark_replicates._load_json(source, "authority source")
            self.assertIs(type(loaded["score"]), float)


if __name__ == "__main__":
    unittest.main()
