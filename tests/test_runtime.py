import json
import unittest

from prompt_performance_engine.adapters import CompletionResponse, MockSequenceAdapter
from prompt_performance_engine.contracts import OptimizationRequest
from prompt_performance_engine.runtime import optimize


def transport(prompt: str) -> str:
    return json.dumps({"optimized_prompt": prompt})


class RuntimeTests(unittest.TestCase):
    def test_end_to_end_optimization(self):
        adapter = MockSequenceAdapter([transport("Return exact valid JSON.")])
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
                transport("Complete repaired Prompt."),
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
        adapter = MockSequenceAdapter([transport("Write a strong report.")])
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
                    text=transport("Complete prompt."),
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
                transport("Candidate one."),
                transport("Candidate two."),
                transport("Candidate three."),
                '{"selected_index": 2}',
            ]
        )
        result = optimize(
            OptimizationRequest(
                source_prompt="Write a report.",
                candidate_count=3,
            ),
            adapter,
        )
        self.assertEqual(result.optimized_prompt, "Candidate two.")
        self.assertEqual(result.artifact["runtime"]["total_calls"], 4)
        self.assertEqual(len(adapter.calls), 4)
        self.assertEqual(result.candidates, (
            "Candidate one.",
            "Candidate two.",
            "Candidate three.",
        ))
        self.assertEqual(
            result.candidate_strategies,
            ("fidelity_guardrail", "coverage_matrix", "concise_channel_fit"),
        )
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

    def test_request_candidate_count_is_used_when_call_override_is_omitted(self):
        adapter = MockSequenceAdapter(
            [
                transport("Candidate one."),
                transport("Candidate two."),
                '{"selected_index": 2}',
            ]
        )
        result = optimize(
            OptimizationRequest(
                source_prompt="Write a report.",
                candidate_count=2,
            ),
            adapter,
        )
        self.assertEqual(result.optimized_prompt, "Candidate two.")
        self.assertEqual(result.artifact["runtime"]["selection"]["candidate_count"], 2)

    def test_legacy_candidate_count_keyword_overrides_request_default(self):
        adapter = MockSequenceAdapter(
            [
                transport("Candidate one."),
                transport("Candidate two."),
                '{"selected_index": 2}',
            ]
        )
        result = optimize(
            OptimizationRequest(source_prompt="Write a report."),
            adapter,
            candidate_count=2,
        )
        self.assertEqual(result.optimized_prompt, "Candidate two.")
        self.assertEqual(result.artifact["runtime"]["selection"]["candidate_count"], 2)

    def test_legacy_candidate_count_keyword_rejects_request_conflict(self):
        adapter = MockSequenceAdapter([])
        with self.assertRaisesRegex(ValueError, "candidate_count conflicts"):
            optimize(
                OptimizationRequest(
                    source_prompt="Write a report.",
                    candidate_count=2,
                ),
                adapter,
                candidate_count=3,
            )
        self.assertEqual(adapter.calls, [])

    def test_selector_does_not_guess_json_from_commentary(self):
        adapter = MockSequenceAdapter(
            [
                transport("Candidate one."),
                transport("Candidate two."),
                'I selected this one: {"selected_index": 2}',
            ]
        )
        with self.assertRaisesRegex(ValueError, "one JSON object only"):
            optimize(
                OptimizationRequest(
                    source_prompt="Write a report.",
                    candidate_count=2,
                ),
                adapter,
            )

    def test_selector_rejects_duplicate_and_extra_fields(self):
        for selector_response in (
            '{"selected_index": 1, "selected_index": 2}',
            '{"selected_index": 1, "comment": "extra"}',
        ):
            with self.subTest(selector_response=selector_response):
                adapter = MockSequenceAdapter(
                    [
                        transport("Candidate one."),
                        transport("Candidate two."),
                        selector_response,
                    ]
                )
                with self.assertRaisesRegex(ValueError, "Candidate selector"):
                    optimize(
                        OptimizationRequest(
                            source_prompt="Write a report.",
                            candidate_count=2,
                        ),
                        adapter,
                    )

    def test_selector_receives_complete_compiled_contract(self):
        adapter = MockSequenceAdapter(
            [
                transport("Candidate one."),
                transport("Candidate two."),
                '{"selected_index": 1}',
            ]
        )
        optimize(
            OptimizationRequest(
                source_prompt="Write truthful landing-page copy with a CTA.",
                domain="marketing_sales",
                required_behaviors=("Preserve the exact CTA.",),
                forbidden_changes=("Do not change the offer.",),
                candidate_count=2,
            ),
            adapter,
        )
        selector_payload = json.loads(adapter.calls[-1]["user_payload"])
        self.assertEqual(
            selector_payload["selected_architecture"],
            "multi_candidate_tournament",
        )
        self.assertTrue(selector_payload["domain_guardrails"])
        self.assertEqual(
            selector_payload["required_behaviors"],
            ["Preserve the exact CTA."],
        )
        self.assertEqual(
            selector_payload["forbidden_changes"],
            ["Do not change the offer."],
        )
        self.assertIn("Do not reward verbosity", adapter.calls[-1]["system_prompt"])
        generation_contexts = [
            json.loads(call["user_payload"])["candidate_context"]
            for call in adapter.calls[:-1]
        ]
        self.assertEqual(
            [item["strategy"] for item in generation_contexts],
            ["fidelity_guardrail", "coverage_matrix"],
        )
        self.assertEqual(
            [item["strategy"] for item in selector_payload["candidates"]],
            ["fidelity_guardrail", "coverage_matrix"],
        )


if __name__ == "__main__":
    unittest.main()
