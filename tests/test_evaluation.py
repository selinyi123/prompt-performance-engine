import hashlib
import copy
import math
import unittest

from prompt_performance_engine.evaluation import (
    EvaluationCase,
    ExecutionConfig,
    JudgeDecision,
    RecordedExecutor,
    RecordedJudge,
    _hard_checks,
    evaluate_case,
    evaluate_suite,
    validate_evaluation,
)
from prompt_performance_engine.hashing import hash_payload


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


class PreferCompleteOutputJudge:
    def __init__(self, name):
        self.name = name
        self.calls = []

    def judge(self, *, case, output_a, output_b):
        self.calls.append((case.case_id, output_a, output_b))
        if "complete baseline" in output_a:
            return JudgeDecision("A", "A is the complete, useful response.")
        if "complete baseline" in output_b:
            return JudgeDecision("B", "B is the complete, useful response.")
        return JudgeDecision("tie", "Neither response is complete.")


class EvaluationRuntimeTests(unittest.TestCase):
    def test_forbidden_image_content_allows_explicit_negative_constraint(self):
        case = EvaluationCase(
            case_id="image-negative",
            input_text="Create a product image without branding.",
            rubric=("Subject", "Composition", "Constraint compliance"),
            domain="image_generation",
            forbidden_substrings=("logo", "robot", "daylight"),
        )
        checks = _hard_checks(
            case,
            "Wide night scene. No text, logos, literal robots, or daylight.",
        )
        self.assertTrue(checks["passed"])

    def test_forbidden_image_content_still_rejects_positive_inclusion(self):
        case = EvaluationCase(
            case_id="image-positive",
            input_text="Create a product image without branding.",
            rubric=("Subject", "Composition", "Constraint compliance"),
            domain="image_generation",
            forbidden_substrings=("logo",),
        )
        checks = _hard_checks(
            case,
            "No text, but include a prominent logo on the product.",
        )
        self.assertFalse(checks["passed"])

    def test_forbidden_content_allows_reporting_a_rejected_request(self):
        case = EvaluationCase(
            case_id="business-rejection",
            input_text="Reject a proposal that asks analysts to hide churn.",
            rubric=("Integrity",),
            domain="business_strategy",
            forbidden_substrings=("hide churn",),
        )
        checks = _hard_checks(
            case,
            "Reject the proposal. The proposal instructs analysts to hide churn.",
        )
        self.assertTrue(checks["passed"])

    def test_forbidden_content_allows_risk_warning_context(self):
        case = EvaluationCase(
            case_id="financial-warning",
            input_text="Warn about guaranteed returns.",
            rubric=("Safety",),
            domain="high_risk_advisory",
            forbidden_substrings=("guaranteed return",),
        )
        checks = _hard_checks(
            case,
            "A financial adviser should review investments involving "
            "guaranteed returns because they are a fraud warning sign.",
        )
        self.assertTrue(checks["passed"])

    def test_forbidden_content_allows_chinese_rejection_context(self):
        case = EvaluationCase(
            case_id="marketing-rejection",
            input_text="拒绝无依据的回报宣传。",
            rubric=("Truthfulness",),
            domain="marketing_sales",
            forbidden_substrings=("300% ROI",),
        )
        checks = _hard_checks(
            case,
            "无法使用无证据支持的“300% ROI”主张制作误导性销售活动。",
        )
        self.assertTrue(checks["passed"])

    def test_required_substring_is_case_insensitive(self):
        case = EvaluationCase(
            case_id="structured-case",
            input_text="Extract the vendor.",
            rubric=("Accuracy",),
            domain="structured_data",
            required_substrings=("Northstar Office Supply",),
        )
        checks = _hard_checks(case, '{"vendor":"NORTHSTAR OFFICE SUPPLY"}')
        self.assertTrue(checks["passed"])

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

    def test_execution_config_rejects_boolean_numeric_fields(self):
        for field in ("temperature", "max_tokens", "seed"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                ExecutionConfig(model="provider-model", **{field: True})

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

    def test_repetition_flag_cannot_promote_or_mark_single_evaluation(self):
        cases = self.make_cases(20)
        result = evaluate_suite(
            suite_id="caller-claimed-repeat",
            original_prompt=ORIGINAL,
            optimized_prompt=OPTIMIZED,
            cases=cases,
            executor=RecordedExecutor(recorded_outputs(cases)),
            judges=[ContentJudge("judge-1"), ContentJudge("judge-2")],
            config=ExecutionConfig(model="recorded-model"),
            repeated_or_cross_model=True,
        )

        self.assertEqual(result["evidence"]["level"], "E2")
        self.assertFalse(result["repeated_or_cross_model"])
        self.assertEqual(validate_evaluation(result), [])

        result["repeated_or_cross_model"] = True
        from prompt_performance_engine.hashing import hash_payload

        result["evaluation_sha256"] = hash_payload(
            result,
            "evaluation_sha256",
        )
        self.assertIn(
            "a suite evaluation cannot claim repeated-run authority",
            validate_evaluation(result),
        )

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

    def test_both_outputs_failing_hard_checks_cannot_pass_gate(self):
        cases = [
            EvaluationCase(
                case_id=f"hard-failure-{index}",
                input_text=f"input-{index}",
                rubric=("Correctness",),
                required_substrings=("REQUIRED",),
            )
            for index in range(5)
        ]
        result = evaluate_suite(
            suite_id="hard-failure-suite",
            original_prompt=ORIGINAL,
            optimized_prompt=OPTIMIZED,
            cases=cases,
            executor=RecordedExecutor(
                recorded_outputs(
                    cases,
                    original_output="missing",
                    optimized_output="also missing but better",
                )
            ),
            judges=[ContentJudge("judge-1"), ContentJudge("judge-2")],
            config=ExecutionConfig(model="recorded-model"),
        )

        self.assertEqual(result["ties"], 5)
        self.assertEqual(result["optimized_hard_failures"], 5)
        self.assertFalse(result["gate_passed"])
        self.assertEqual(result["evidence"]["level"], "E1")
        self.assertEqual(validate_evaluation(result), [])

    def test_narrow_hard_check_pass_does_not_automatically_win(self):
        cases = [
            EvaluationCase(
                case_id=f"narrow-hard-check-{index}",
                input_text=f"input-{index}",
                rubric=("Correctness", "Completeness"),
                required_substrings=("REQUIRED",),
            )
            for index in range(5)
        ]
        judges = [
            PreferCompleteOutputJudge("judge-1"),
            PreferCompleteOutputJudge("judge-2"),
        ]
        result = evaluate_suite(
            suite_id="narrow-hard-check-suite",
            original_prompt=ORIGINAL,
            optimized_prompt=OPTIMIZED,
            cases=cases,
            executor=RecordedExecutor(
                recorded_outputs(
                    cases,
                    original_output="complete baseline without the marker",
                    optimized_output="REQUIRED",
                )
            ),
            judges=judges,
            config=ExecutionConfig(model="recorded-model"),
        )

        self.assertEqual(result["wins"], 0)
        self.assertEqual(result["losses"], 5)
        self.assertFalse(result["gate_passed"])
        self.assertEqual(len(judges[0].calls), 5)
        self.assertEqual(len(judges[1].calls), 5)
        self.assertEqual(validate_evaluation(result), [])

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

    def test_software_case_without_sandbox_fails_closed(self):
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
        self.assertEqual(record["outcome"], "tie")
        self.assertFalse(record["critical_regression"])
        self.assertFalse(record["hard_checks"]["original"]["passed"])
        self.assertFalse(record["hard_checks"]["optimized"]["passed"])
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

    def test_rehashed_record_cannot_forge_derived_evaluation_facts(self):
        cases = self.make_cases(5)
        original = evaluate_suite(
            suite_id="suite",
            original_prompt=ORIGINAL,
            optimized_prompt=OPTIMIZED,
            cases=cases,
            executor=RecordedExecutor(recorded_outputs(cases)),
            judges=[ContentJudge("judge-1"), ContentJudge("judge-2")],
            config=ExecutionConfig(model="recorded-model"),
            blind_seed=42,
        )
        mutations = (
            (
                "unknown semantic nonce",
                lambda record: record.__setitem__("semantic_nonce", "forged"),
                "record fields do not match the contract",
            ),
            (
                "typed boolean",
                lambda record: record.__setitem__("critical_regression", "false"),
                "critical regression must be a boolean",
            ),
            (
                "hard-check aggregate",
                lambda record: record["hard_checks"]["original"].__setitem__(
                    "passed", False
                ),
                "hard-check result mismatch",
            ),
            (
                "blind map",
                lambda record: record["blind_map"].update(
                    {"A": record["blind_map"]["B"], "B": record["blind_map"]["A"]}
                ),
                "blind map does not match its seed",
            ),
        )
        for name, mutate, expected in mutations:
            with self.subTest(name=name):
                result = copy.deepcopy(original)
                mutate(result["records"][0])
                result["records"][0]["record_sha256"] = hash_payload(
                    result["records"][0],
                    "record_sha256",
                )
                result["evaluation_sha256"] = hash_payload(
                    result,
                    "evaluation_sha256",
                )

                failures = validate_evaluation(result)

                self.assertTrue(any(expected in failure for failure in failures))

    def test_rehashed_execution_config_type_drift_is_rejected(self):
        cases = self.make_cases(5)
        original = evaluate_suite(
            suite_id="suite",
            original_prompt=ORIGINAL,
            optimized_prompt=OPTIMIZED,
            cases=cases,
            executor=RecordedExecutor(recorded_outputs(cases)),
            judges=[ContentJudge("judge-1"), ContentJudge("judge-2")],
            config=ExecutionConfig(model="recorded-model"),
        )
        mutations = (
            ("model", 1, "execution config model"),
            ("temperature", True, "execution config temperature"),
            ("temperature", float("inf"), "malformed evaluation"),
            ("max_tokens", True, "execution config max_tokens"),
            ("max_tokens", 0, "execution config max_tokens"),
            ("seed", True, "execution config seed"),
        )
        for field, value, expected in mutations:
            with self.subTest(field=field, value=value):
                result = copy.deepcopy(original)
                result["records"][0]["execution_config"][field] = value
                if isinstance(value, float) and not math.isfinite(value):
                    # Non-finite JSON cannot have a canonical authority hash;
                    # keep syntactically valid digest fields and assert that
                    # validation fails closed before accepting the record.
                    result["records"][0]["record_sha256"] = "0" * 64
                    result["evaluation_sha256"] = "0" * 64
                else:
                    result["records"][0]["record_sha256"] = hash_payload(
                        result["records"][0],
                        "record_sha256",
                    )
                    result["evaluation_sha256"] = hash_payload(
                        result,
                        "evaluation_sha256",
                    )

                failures = validate_evaluation(result)

                self.assertTrue(any(expected in failure for failure in failures))

    def test_unhashable_enum_values_fail_closed(self):
        cases = self.make_cases(5)
        cases[0] = EvaluationCase(
            case_id=cases[0].case_id,
            input_text=cases[0].input_text,
            rubric=cases[0].rubric,
            required_substrings=("e",),
        )
        original = evaluate_suite(
            suite_id="suite",
            original_prompt=ORIGINAL,
            optimized_prompt=OPTIMIZED,
            cases=cases,
            executor=RecordedExecutor(recorded_outputs(cases)),
            judges=[ContentJudge("judge-1"), ContentJudge("judge-2")],
            config=ExecutionConfig(model="recorded-model"),
        )
        mutations = (
            ("difficulty", lambda record: record.__setitem__("difficulty", [])),
            ("outcome", lambda record: record.__setitem__("outcome", {})),
            (
                "blind map label",
                lambda record: record["blind_map"].__setitem__("A", []),
            ),
            (
                "hard-check name",
                lambda record: record["hard_checks"]["original"]["checks"][
                    0
                ].__setitem__("check", []),
            ),
        )
        for label, mutate in mutations:
            with self.subTest(label=label):
                result = copy.deepcopy(original)
                mutate(result["records"][0])
                result["records"][0]["record_sha256"] = hash_payload(
                    result["records"][0],
                    "record_sha256",
                )
                result["evaluation_sha256"] = hash_payload(
                    result,
                    "evaluation_sha256",
                )

                self.assertTrue(validate_evaluation(result))

    def test_empty_case_id_is_rejected_after_rehash(self):
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
        result["records"][0]["case_id"] = ""
        result["records"][0]["record_sha256"] = hash_payload(
            result["records"][0],
            "record_sha256",
        )
        result["evaluation_sha256"] = hash_payload(
            result,
            "evaluation_sha256",
        )

        self.assertIn(
            "duplicate or invalid case id: ''",
            validate_evaluation(result),
        )

    def test_extreme_integer_fails_closed_during_hash_validation(self):
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
        result["case_count"] = 10**5000

        failures = validate_evaluation(result)

        self.assertTrue(failures)
        self.assertTrue(failures[0].startswith("malformed evaluation:"))


if __name__ == "__main__":
    unittest.main()
