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
