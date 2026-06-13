import hashlib
import unittest

from prompt_performance_engine.evaluation import (
    EvaluationCase,
    ExecutionConfig,
    JudgeDecision,
    RecordedExecutor,
    RecordedJudge,
    evaluate_case,
    evaluate_suite,
    validate_evaluation,
)


ORIGINAL = "Original Prompt"
OPTIMIZED = "Optimized Prompt"


def recorded_outputs(cases, original_output="baseline", optimized_output="better"):
    outputs = {}
    for case in cases:
        input_hash = hashlib.sha256(case.input_text.encode("utf-8")).hexdigest()
        for prompt, output in (
            (ORIGINAL, original_output),
            (OPTIMIZED, optimized_output),
        ):
            prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
            outputs[(prompt_hash, input_hash)] = output
    return outputs


class ContentJudge:
    def __init__(self, name):
        self.name = name
        self.calls = []

    def judge(self, *, case, output_a, output_b):
        self.calls.append((case.case_id, output_a, output_b))
        if "better" in output_a and "better" not in output_b:
            return JudgeDecision("A", "A better satisfies the rubric.")
        if "better" in output_b and "better" not in output_a:
            return JudgeDecision("B", "B better satisfies the rubric.")
        return JudgeDecision("tie", "No material difference.")


class EvaluationRuntimeTests(unittest.TestCase):
    def make_cases(self, count):
        return [
            EvaluationCase(
                case_id=f"case-{index}",
                input_text=f"input-{index}",
                rubric=("Correctness", "Usefulness"),
            )
            for index in range(count)
        ]

    def test_uncontrolled_provider_settings_can_be_recorded_as_null(self):
        config = ExecutionConfig(
            model="provider-model",
            temperature=None,
            max_tokens=None,
            seed=None,
        )
        self.assertEqual(
            config.to_dict(),
            {
                "model": "provider-model",
                "temperature": None,
                "max_tokens": None,
                "seed": None,
            },
        )

    def test_five_matched_wins_produce_e2(self):
        cases = self.make_cases(5)
        executor = RecordedExecutor(recorded_outputs(cases))
        judges = [ContentJudge("judge-1"), ContentJudge("judge-2")]
        result = evaluate_suite(
            suite_id="suite",
            original_prompt=ORIGINAL,
            optimized_prompt=OPTIMIZED,
            cases=cases,
            executor=executor,
            judges=judges,
            config=ExecutionConfig(model="recorded-model"),
            blind_seed=42,
        )
        self.assertEqual(result["wins"], 5)
        self.assertEqual(result["losses"], 0)
        self.assertTrue(result["gate_passed"])
        self.assertEqual(result["evidence"]["level"], "E2")
        self.assertEqual(validate_evaluation(result), [])
        self.assertEqual(len(judges[0].calls), 5)
        self.assertEqual(len(executor.calls), 10)

    def test_four_cases_cannot_claim_verified_improvement(self):
        cases = self.make_cases(4)
        result = evaluate_suite(
            suite_id="small-suite",
            original_prompt=ORIGINAL,
            optimized_prompt=OPTIMIZED,
            cases=cases,
            executor=RecordedExecutor(recorded_outputs(cases)),
            judges=[ContentJudge("judge-1"), ContentJudge("judge-2")],
            config=ExecutionConfig(model="recorded-model"),
        )
        self.assertFalse(result["gate_passed"])
        self.assertEqual(result["evidence"]["level"], "E1")

    def test_hard_regression_overrides_judges(self):
        case = EvaluationCase(
            case_id="hard-check",
            input_text="input",
            rubric=("Correctness",),
            required_substrings=("PASS",),
        )
        executor = RecordedExecutor(
            recorded_outputs(
                [case],
                original_output="PASS",
                optimized_output="missing",
            )
        )
        judges = [
            RecordedJudge([JudgeDecision("A", "unused")], name="judge-1"),
            RecordedJudge([JudgeDecision("A", "unused")], name="judge-2"),
        ]
        record = evaluate_case(
            original_prompt=ORIGINAL,
            optimized_prompt=OPTIMIZED,
            case=case,
            executor=executor,
            judges=judges,
            config=ExecutionConfig(model="recorded-model"),
        )
        self.assertEqual(record["outcome"], "loss")
        self.assertTrue(record["critical_regression"])
        self.assertEqual(record["judge_decisions"], [])
        self.assertEqual(judges[0].calls, [])

    def test_case_behavior_regression_overrides_judges(self):
        case = EvaluationCase(
            case_id="se-normal-pagination",
            input_text=(
                "Implement a Python function paginate(items, page, page_size) "
                "with one-based pages and predictable validation errors."
            ),
            rubric=("Correctness", "Boundary handling"),
            domain="software_engineering",
        )
        original_output = """```python
def paginate(items, page, page_size):
    if not isinstance(page, int) or isinstance(page, bool):
        raise TypeError("invalid page")
    if not isinstance(page_size, int) or isinstance(page_size, bool):
        raise TypeError("invalid page size")
    if page < 1 or page_size < 1:
        raise ValueError("values must be positive")
    start = (page - 1) * page_size
    return items[start:start + page_size]
```"""
        optimized_output = """```python
def paginate(items, page, page_size):
    return items[page * page_size:(page + 1) * page_size]
```"""
        executor = RecordedExecutor(
            recorded_outputs(
                [case],
                original_output=original_output,
                optimized_output=optimized_output,
            )
        )
        judges = [
            RecordedJudge([JudgeDecision("A", "unused")], name="judge-1"),
            RecordedJudge([JudgeDecision("A", "unused")], name="judge-2"),
        ]
        record = evaluate_case(
            original_prompt=ORIGINAL,
            optimized_prompt=OPTIMIZED,
            case=case,
            executor=executor,
            judges=judges,
            config=ExecutionConfig(model="recorded-model"),
        )
        self.assertEqual(record["outcome"], "loss")
        self.assertTrue(record["critical_regression"])
        self.assertEqual(record["judge_decisions"], [])
        self.assertEqual(judges[0].calls, [])

    def test_tampered_record_fails_validation(self):
        cases = self.make_cases(5)
        result = evaluate_suite(
            suite_id="suite",
            original_prompt=ORIGINAL,
            optimized_prompt=OPTIMIZED,
            cases=cases,
            executor=RecordedExecutor(recorded_outputs(cases)),
            judges=[ContentJudge("judge-1"), ContentJudge("judge-2")],
            config=ExecutionConfig(model="recorded-model"),
        )
        result["records"][0]["optimized_output"] = "tampered"
        self.assertTrue(validate_evaluation(result))


if __name__ == "__main__":
    unittest.main()
