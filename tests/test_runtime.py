import unittest

from prompt_performance_engine.adapters import CompletionResponse, MockSequenceAdapter
from prompt_performance_engine.contracts import OptimizationRequest
from prompt_performance_engine.runtime import optimize


class RuntimeTests(unittest.TestCase):
    def test_end_to_end_optimization(self):
        adapter = MockSequenceAdapter(
            ["## 优化后的 Prompt\n\n```text\nReturn exact valid JSON.\n```"]
        )
        result = optimize(
            OptimizationRequest(
                source_prompt="Extract data as JSON.",
                output_format="standard",
            ),
            adapter,
        )
        self.assertEqual(result.optimized_prompt, "Return exact valid JSON.")
        self.assertEqual(result.artifact["evidence"]["level"], "E1")
        self.assertTrue(result.artifact["audit"]["optimized"]["passed"])
        self.assertEqual(result.artifact["runtime"]["total_calls"], 1)
        self.assertEqual(
            result.artifact["runtime"]["selection"]["method"],
            "single_candidate",
        )
        self.assertEqual(result.selected_index, 1)
        self.assertEqual(result.repair_count, 0)

    def test_one_bounded_repair(self):
        adapter = MockSequenceAdapter(
            [
                "I forgot the Prompt.",
                "```text\nComplete repaired Prompt.\n```",
            ]
        )
        result = optimize(
            OptimizationRequest(
                source_prompt="Write a report.",
                output_format="standard",
            ),
            adapter,
        )
        self.assertEqual(result.optimized_prompt, "Complete repaired Prompt.")
        self.assertEqual(result.repair_count, 1)
        self.assertEqual(len(adapter.calls), 2)
        self.assertEqual(result.artifact["runtime"]["total_calls"], 2)

    def test_repair_can_be_disabled(self):
        adapter = MockSequenceAdapter(["Malformed."])
        with self.assertRaises(ValueError):
            optimize(
                OptimizationRequest(source_prompt="Write a report."),
                adapter,
                max_repairs=0,
            )

    def test_variable_loss_stays_at_e0(self):
        adapter = MockSequenceAdapter(
            ["## Optimized Prompt\n\n```text\nWrite a strong report.\n```"]
        )
        result = optimize(
            OptimizationRequest(
                source_prompt="Write about {{TOPIC}}.",
                output_format="standard",
            ),
            adapter,
        )
        self.assertEqual(result.artifact["evidence"]["level"], "E0")
        self.assertFalse(result.artifact["audit"]["optimized"]["passed"])

    def test_usage_is_aggregated_without_response_text(self):
        adapter = MockSequenceAdapter(
            [
                CompletionResponse(
                    text="## Optimized Prompt\n\n```text\nComplete prompt.\n```",
                    provider="test-provider",
                    model="test-model",
                    response_id="response-1",
                    usage={"input_tokens": 10, "output_tokens": 3, "total_tokens": 13},
                )
            ]
        )
        result = optimize(
            OptimizationRequest(source_prompt="Write a report."),
            adapter,
        )
        runtime = result.artifact["runtime"]
        self.assertEqual(runtime["total_usage"]["total_tokens"], 13)
        self.assertNotIn("text", runtime["model_calls"][0])

    def test_tournament_selects_one_of_three_candidates(self):
        adapter = MockSequenceAdapter(
            [
                "<optimized_prompt>Candidate one.</optimized_prompt>",
                "<optimized_prompt>Candidate two.</optimized_prompt>",
                "<optimized_prompt>Candidate three.</optimized_prompt>",
                '{"selected_index": 2}',
            ]
        )
        result = optimize(
            OptimizationRequest(source_prompt="Write a report."),
            adapter,
            candidate_count=3,
        )
        self.assertEqual(result.optimized_prompt, "Candidate two.")
        self.assertEqual(result.artifact["runtime"]["total_calls"], 4)
        self.assertEqual(len(adapter.calls), 4)
        self.assertEqual(result.candidates, (
            "Candidate one.",
            "Candidate two.",
            "Candidate three.",
        ))
        self.assertEqual(result.selected_index, 2)
        selection = result.artifact["runtime"]["selection"]
        self.assertEqual(selection["method"], "model_selector")
        self.assertEqual(selection["candidate_count"], 3)
        self.assertEqual(selection["selected_index"], 2)
        self.assertEqual(len(selection["selector_response_sha256"]), 64)
        self.assertEqual(
            [item["selected"] for item in selection["candidates"]],
            [False, True, False],
        )


if __name__ == "__main__":
    unittest.main()
