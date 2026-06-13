import unittest

from prompt_performance_engine.contracts import (
    INSTALLED_DATA_ROOTS,
    PACKAGE_ROOT,
    OptimizationRequest,
)


class OptimizationRequestTests(unittest.TestCase):
    def test_runtime_data_root_contains_version(self):
        self.assertTrue((PACKAGE_ROOT / "VERSION").is_file())
        self.assertEqual(len(INSTALLED_DATA_ROOTS), 2)

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
