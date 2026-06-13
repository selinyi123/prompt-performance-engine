import unittest

from prompt_performance_engine.evidence import infer_evidence


class EvidenceTests(unittest.TestCase):
    def test_static_only_is_candidate(self):
        evidence = infer_evidence(deterministic_checks_passed=True)
        self.assertEqual(evidence.level, "E1")
        self.assertEqual(evidence.claim, "optimized_candidate")

    def test_matched_execution_can_verify_scoped(self):
        evidence = infer_evidence(
            deterministic_checks_passed=True,
            matched_cases=5,
            comparative_improvement_passed=True,
        )
        self.assertEqual(evidence.level, "E2")
        self.assertEqual(evidence.claim, "verified_improvement")

    def test_case_count_without_improvement_stays_static(self):
        evidence = infer_evidence(
            deterministic_checks_passed=True,
            matched_cases=100,
            comparative_improvement_passed=False,
            repeated_or_cross_model=True,
        )
        self.assertEqual(evidence.level, "E1")
        self.assertEqual(evidence.claim, "optimized_candidate")

    def test_e5_requires_all_prior_evidence(self):
        evidence = infer_evidence(
            deterministic_checks_passed=True,
            matched_cases=20,
            comparative_improvement_passed=True,
            repeated_or_cross_model=True,
            expert_reviewers=3,
            independently_reproduced=True,
        )
        self.assertEqual(evidence.level, "E5")


if __name__ == "__main__":
    unittest.main()
