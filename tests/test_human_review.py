import hashlib
import unittest

from prompt_performance_engine.evaluation import (
    EvaluationCase,
    ExecutionConfig,
    JudgeDecision,
    RecordedExecutor,
    evaluate_suite,
)
from prompt_performance_engine.human_review import (
    aggregate_human_review,
    create_reviewer_packet,
    validate_submission,
)


ORIGINAL = "Original Prompt"
OPTIMIZED = "Optimized Prompt"


class BetterJudge:
    def __init__(self, name):
        self.name = name

    def judge(self, *, case, output_a, output_b):
        if "better" in output_a:
            return JudgeDecision("A", "A is more correct and useful.")
        return JudgeDecision("B", "B is more correct and useful.")


def build_e3_evaluation():
    cases = [
        EvaluationCase(
            case_id=f"human-{index:02d}",
            input_text=f"Substantive evaluation input number {index}.",
            rubric=("Correctness", "Usefulness", "Constraint compliance"),
            domain=("software_engineering" if index % 2 else "research_analysis"),
            difficulty=("adversarial" if index % 3 == 0 else "difficult"),
        )
        for index in range(24)
    ]
    outputs = {}
    for case in cases:
        input_hash = hashlib.sha256(case.input_text.encode("utf-8")).hexdigest()
        outputs[
            (hashlib.sha256(ORIGINAL.encode()).hexdigest(), input_hash)
        ] = "baseline output"
        outputs[
            (hashlib.sha256(OPTIMIZED.encode()).hexdigest(), input_hash)
        ] = "better output with concrete verification"
    return evaluate_suite(
        suite_id="human-review-suite",
        original_prompt=ORIGINAL,
        optimized_prompt=OPTIMIZED,
        cases=cases,
        executor=RecordedExecutor(outputs),
        judges=[BetterJudge("judge-1"), BetterJudge("judge-2")],
        config=ExecutionConfig(model="recorded-model"),
        repeated_or_cross_model=True,
    )


def perfect_submission(packet, key):
    labels = {item["item_id"]: item["optimized_label"] for item in key["items"]}
    return {
        "schema_version": "1.0.0",
        "reviewer_id": packet["reviewer_id"],
        "packet_sha256": packet["packet_sha256"],
        "decisions": [
            {
                "item_id": item["item_id"],
                "winner": labels[item["item_id"]],
                "reason": "This output more completely satisfies the stated rubric.",
            }
            for item in packet["items"]
        ],
    }


class HumanReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.evaluation = build_e3_evaluation()

    def test_three_reviewers_and_24_cases_produce_e4(self):
        packets = []
        keys = []
        submissions = []
        for reviewer in ("reviewer-1", "reviewer-2", "reviewer-3"):
            packet, key = create_reviewer_packet(
                [self.evaluation],
                reviewer_id=reviewer,
                sample_size=24,
                seed=7,
                position_probe_count=2,
            )
            packets.append(packet)
            keys.append(key)
            submissions.append(perfect_submission(packet, key))

        report = aggregate_human_review(
            [self.evaluation],
            packets,
            keys,
            submissions,
        )
        self.assertTrue(report["e4_ready"])
        self.assertEqual(report["evidence"]["level"], "E4")
        self.assertEqual(report["reviewer_count"], 3)
        self.assertEqual(report["reviewed_case_count"], 24)
        self.assertEqual(report["pairwise_agreement"], 1.0)
        self.assertEqual(report["judge_human_agreement"], 1.0)
        self.assertEqual(report["position_probe_consistency"], 1.0)

    def test_two_reviewers_cannot_produce_e4(self):
        packets = []
        keys = []
        submissions = []
        for reviewer in ("reviewer-1", "reviewer-2"):
            packet, key = create_reviewer_packet(
                [self.evaluation],
                reviewer_id=reviewer,
                sample_size=24,
            )
            packets.append(packet)
            keys.append(key)
            submissions.append(perfect_submission(packet, key))
        report = aggregate_human_review(
            [self.evaluation],
            packets,
            keys,
            submissions,
        )
        self.assertFalse(report["e4_ready"])
        self.assertEqual(report["evidence"]["level"], "E3")

    def test_submission_requires_complete_reasoned_decisions(self):
        packet, key = create_reviewer_packet(
            [self.evaluation],
            reviewer_id="reviewer",
            sample_size=24,
        )
        submission = perfect_submission(packet, key)
        submission["decisions"][0]["reason"] = "short"
        self.assertTrue(validate_submission(packet, submission))


if __name__ == "__main__":
    unittest.main()
