import tempfile
import unittest
from pathlib import Path

from prompt_performance_engine.adapters import CompletionResponse
from prompt_performance_engine.codex_evaluation import (
    CachedCodexBlindJudge,
    CachedCodexExecutor,
)
from prompt_performance_engine.evaluation import EvaluationCase, ExecutionConfig


class FakeAdapter:
    def __init__(self, responses, counter):
        self.responses = responses
        self.counter = counter

    def complete(self, *, system_prompt, user_payload, cancellation=None):
        self.counter.append((system_prompt, user_payload))
        return CompletionResponse(
            text=self.responses.pop(0),
            provider="fake-codex",
            model="fake-model",
            usage={"total_tokens": 5},
        )


class CodexEvaluationTests(unittest.TestCase):
    def _assert_judge_response_rejected(self, response: str, pattern: str) -> None:
        with tempfile.TemporaryDirectory() as directory:
            judge = CachedCodexBlindJudge(
                name="judge-1",
                adapter_factory=lambda: FakeAdapter([response], []),
                cache_directory=Path(directory),
            )
            case = EvaluationCase(
                case_id="case",
                input_text="A sufficiently substantive benchmark input.",
                rubric=("Correctness", "Usefulness", "Safety"),
            )
            with self.assertRaisesRegex(ValueError, pattern):
                judge.judge(case=case, output_a="a", output_b="b")

    def test_judge_requires_one_exact_json_object(self):
        valid = (
            '{"winner":"B","reason":"B is stronger.",'
            '"fatal_flaw_a":false,"fatal_flaw_b":false}'
        )
        cases = (
            ("commentary\n" + valid, "valid JSON"),
            ("```json\n" + valid + "\n```", "valid JSON"),
            (
                '{"winner":"A","winner":"B","reason":"B is stronger.",'
                '"fatal_flaw_a":false,"fatal_flaw_b":false}',
                "duplicate field",
            ),
            (valid[:-1] + ',"confidence":1}', "exactly"),
            (
                '{"winner":"B","reason":"B is stronger.",'
                '"fatal_flaw_a":false}',
                "missing fatal_flaw_b",
            ),
        )
        for response, pattern in cases:
            with self.subTest(response=response):
                self._assert_judge_response_rejected(response, pattern)

    def test_judge_rejects_unpaired_utf16_surrogate(self):
        self._assert_judge_response_rejected(
            r'{"winner":"B","reason":"\ud800","fatal_flaw_a":false,'
            r'"fatal_flaw_b":false}',
            "not valid UTF-8",
        )

    def test_judge_rejects_extreme_decimal_exponent(self):
        self._assert_judge_response_rejected(
            '{"winner":"B","reason":1e999999999,"fatal_flaw_a":false,'
            '"fatal_flaw_b":false}',
            "numeric exponent outside the supported range",
        )

    def test_judge_rejects_string_boolean_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            responses = [
                '{"winner":"B","reason":"B is stronger.",'
                '"fatal_flaw_a":"false","fatal_flaw_b":false}'
            ]
            judge = CachedCodexBlindJudge(
                name="judge-1",
                adapter_factory=lambda: FakeAdapter(responses, []),
                cache_directory=Path(directory),
            )
            case = EvaluationCase(
                case_id="case",
                input_text="A sufficiently substantive benchmark input.",
                rubric=("Correctness", "Usefulness", "Safety"),
            )

            with self.assertRaisesRegex(ValueError, "must be JSON booleans"):
                judge.judge(case=case, output_a="a", output_b="b")

    def test_judge_rejects_non_string_reason(self):
        with tempfile.TemporaryDirectory() as directory:
            responses = [
                '{"winner":"B","reason":{"summary":"B is stronger."},'
                '"fatal_flaw_a":false,"fatal_flaw_b":false}'
            ]
            judge = CachedCodexBlindJudge(
                name="judge-1",
                adapter_factory=lambda: FakeAdapter(responses, []),
                cache_directory=Path(directory),
            )
            case = EvaluationCase(
                case_id="case",
                input_text="A sufficiently substantive benchmark input.",
                rubric=("Correctness", "Usefulness", "Safety"),
            )

            with self.assertRaisesRegex(ValueError, "non-empty string"):
                judge.judge(case=case, output_a="a", output_b="b")

    def test_executor_cache_prevents_duplicate_model_call(self):
        with tempfile.TemporaryDirectory() as directory:
            calls = []
            responses = ["output"]
            factory = lambda: FakeAdapter(responses, calls)
            executor = CachedCodexExecutor(factory, Path(directory))
            config = ExecutionConfig(model="fake-model")
            first = executor.execute(prompt="prompt", input_text="input", config=config)
            second = executor.execute(prompt="prompt", input_text="input", config=config)
            self.assertEqual(first.text, "output")
            self.assertEqual(second.text, "output")
            self.assertEqual(len(calls), 1)
            self.assertTrue(executor.calls[-1]["cached"])

    def test_judge_cache_and_json_parsing(self):
        with tempfile.TemporaryDirectory() as directory:
            calls = []
            responses = [
                '{"winner":"B","reason":"B satisfies more rubric requirements.",'
                '"fatal_flaw_a":false,"fatal_flaw_b":false}'
            ]
            factory = lambda: FakeAdapter(responses, calls)
            judge = CachedCodexBlindJudge(
                name="judge-1",
                adapter_factory=factory,
                cache_directory=Path(directory),
            )
            case = EvaluationCase(
                case_id="case",
                input_text="A sufficiently substantive benchmark input.",
                rubric=("Correctness", "Usefulness", "Safety"),
            )
            first = judge.judge(case=case, output_a="a", output_b="b")
            second = judge.judge(case=case, output_a="a", output_b="b")
            self.assertEqual(first.winner, "B")
            self.assertEqual(second.winner, "B")
            self.assertEqual(len(calls), 1)
            self.assertTrue(judge.calls[-1]["cached"])


if __name__ == "__main__":
    unittest.main()
