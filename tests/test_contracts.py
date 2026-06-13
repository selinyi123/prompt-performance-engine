import unittest

from prompt_performance_engine.contracts import OptimizationRequest


class OptimizationRequestTests(unittest.TestCase):
    def test_valid_request(self):
        request = OptimizationRequest(source_prompt="Write a product strategy.")
        request.validate()
        self.assertEqual(request.to_dict()["mode"], "maximum_quality")

    def test_empty_prompt_rejected(self):
        with self.assertRaises(ValueError):
            OptimizationRequest(source_prompt="  ").validate()

    def test_unknown_mode_rejected(self):
        with self.assertRaises(ValueError):
            OptimizationRequest(source_prompt="x", mode="extreme").validate()


if __name__ == "__main__":
    unittest.main()
