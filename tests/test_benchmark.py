import unittest

from prompt_performance_engine.benchmark import (
    group_jobs_by_domain,
    load_benchmark,
    load_benchmark_definition,
    validate_benchmark,
)
from prompt_performance_engine.contracts import PACKAGE_ROOT


class BenchmarkTests(unittest.TestCase):
    def test_core_18_has_real_six_domain_coverage(self):
        suite_id, jobs = load_benchmark(PACKAGE_ROOT / "benchmark" / "core-18.json")
        required = {
            "software_engineering",
            "research_analysis",
            "professional_writing",
            "image_generation",
            "creative_design",
            "business_strategy",
        }
        self.assertEqual(
            validate_benchmark(
                suite_id,
                jobs,
                required_domains=required,
                minimum_cases_per_domain=3,
            ),
            [],
        )
        self.assertEqual(len(jobs), 6)
        self.assertEqual(sum(len(job.cases) for job in jobs), 18)
        for job in jobs:
            self.assertEqual(
                {case.difficulty for case in job.cases},
                {"normal", "difficult", "adversarial"},
            )

    def test_catalog_has_twelve_domains_and_sixty_cases(self):
        suite_id, jobs = load_benchmark_definition(
            PACKAGE_ROOT / "benchmark" / "catalog-60.json"
        )
        required = {
            "software_engineering",
            "research_analysis",
            "professional_writing",
            "image_generation",
            "creative_design",
            "business_strategy",
            "structured_data",
            "marketing_sales",
            "education",
            "translation_localization",
            "agents_automation",
            "high_risk_advisory",
        }
        self.assertEqual(
            validate_benchmark(
                suite_id,
                jobs,
                required_domains=required,
                minimum_cases_per_domain=5,
            ),
            [],
        )
        cases = [case for job in jobs for case in job.cases]
        self.assertEqual(len(cases), 60)
        self.assertGreaterEqual(
            sum(case.difficulty == "adversarial" for case in cases),
            12,
        )
        grouped = group_jobs_by_domain(jobs)
        self.assertEqual(len(grouped), 12)
        self.assertTrue(all(len(job.cases) == 5 for job in grouped.values()))


if __name__ == "__main__":
    unittest.main()
