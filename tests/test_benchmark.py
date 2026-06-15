import unittest

from prompt_performance_engine.benchmark import (
    BenchmarkJob,
    case_from_dict,
    group_jobs_by_domain,
    load_benchmark,
    load_benchmark_definition,
    validate_benchmark,
)
from prompt_performance_engine.contracts import PACKAGE_ROOT


class BenchmarkTests(unittest.TestCase):
    def test_payload_dependent_domain_rejects_abstract_case_description(self):
        case = case_from_dict(
            {
                "case_id": "sd-abstract",
                "input_text": "Extract an invoice into the requested schema.",
                "rubric": ["Accuracy", "Schema", "Null handling"],
                "domain": "structured_data",
            }
        )
        failures = validate_benchmark(
            "suite",
            (
                BenchmarkJob(
                    job_id="job",
                    domain="structured_data",
                    source_prompt="Extract supplied content.",
                    cases=(case,),
                ),
            ),
        )
        self.assertIn("sd-abstract: concrete payload is too short", failures)
        self.assertTrue(
            any("concrete payload missing" in failure for failure in failures)
        )

    def test_source_payload_tag_requires_embedded_source_material(self):
        case = case_from_dict(
            {
                "case_id": "research-abstract",
                "input_text": "Research the question and cite good sources.",
                "rubric": ["Evidence", "Synthesis", "Uncertainty"],
                "domain": "research_analysis",
                "tags": ["requires_source_payload"],
            }
        )
        failures = validate_benchmark(
            "suite",
            (
                BenchmarkJob(
                    job_id="job",
                    domain="research_analysis",
                    source_prompt="Analyze supplied evidence.",
                    cases=(case,),
                ),
            ),
        )
        self.assertIn("research-abstract: source payload is too short", failures)
        self.assertIn(
            "research-abstract: source payload marker is missing",
            failures,
        )

    def test_marketing_domain_rejects_abstract_brief(self):
        case = case_from_dict(
            {
                "case_id": "marketing-abstract",
                "input_text": "Write a landing page for bookkeeping software.",
                "rubric": ["Audience", "Offer", "CTA"],
                "domain": "marketing_sales",
            }
        )
        failures = validate_benchmark(
            "suite",
            (
                BenchmarkJob(
                    job_id="job",
                    domain="marketing_sales",
                    source_prompt="Create truthful marketing copy.",
                    cases=(case,),
                ),
            ),
        )
        self.assertIn(
            "marketing-abstract: concrete payload is too short",
            failures,
        )
        self.assertTrue(
            any("concrete payload missing" in failure for failure in failures)
        )

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
        marketing = grouped["marketing_sales"]
        for case in marketing.cases:
            with self.subTest(case=case.case_id):
                self.assertGreaterEqual(len(case.input_text), 600)
                for marker in (
                    "BRIEF:",
                    "PRODUCT_FACTS:",
                    "AUDIENCE:",
                    "CHANNEL:",
                    "CTA:",
                    "EVIDENCE:",
                ):
                    self.assertIn(marker, case.input_text)


if __name__ == "__main__":
    unittest.main()
