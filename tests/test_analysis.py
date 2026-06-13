import unittest

from prompt_performance_engine.analysis import recover_behavioral_contract


class BehavioralContractTests(unittest.TestCase):
    def test_extracts_variables_and_constraints(self):
        contract = recover_behavioral_contract(
            "Write about {{TOPIC}}.\nMust be concise.\nNever invent citations."
        )
        self.assertEqual(contract.variables, ("TOPIC",))
        self.assertEqual(len(contract.required_constraints), 1)
        self.assertEqual(len(contract.forbidden_behaviors), 1)

    def test_sparse_prompt_is_flagged(self):
        contract = recover_behavioral_contract("Write something good.")
        self.assertTrue(contract.ambiguities)

    def test_design_deliverable_takes_precedence_over_implementation_terms(self):
        contract = recover_behavioral_contract(
            "Design a backward-compatible database migration."
        )
        self.assertEqual(contract.deliverable_kind, "design_or_plan")

    def test_extracts_chinese_constraints(self):
        contract = recover_behavioral_contract(
            "写一份报告。\n必须引用来源。\n不得编造数据。"
        )
        self.assertEqual(len(contract.required_constraints), 1)
        self.assertEqual(len(contract.forbidden_behaviors), 1)


if __name__ == "__main__":
    unittest.main()
