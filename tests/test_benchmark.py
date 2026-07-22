import json
import tempfile
import unittest
from pathlib import Path

from prompt_performance_engine.benchmark import (
    BenchmarkJob,
    case_from_dict,
    group_jobs_by_domain,
    load_benchmark,
    load_benchmark_catalog,
    load_benchmark_definition,
    validate_benchmark,
)
from prompt_performance_engine.contracts import PACKAGE_ROOT


class BenchmarkTests(unittest.TestCase):
    @staticmethod
    def _valid_definition() -> dict:
        return {
            "schema_version": "2.0.0",
            "suite_id": "test-suite",
            "jobs": [
                {
                    "job_id": "test-job",
                    "domain": "generic",
                    "source_prompt": "Complete the supplied task.",
                    "cases": [
                        {
                            "case_id": "test-case",
                            "input_text": "Evaluate this sufficiently detailed input.",
                            "rubric": ["Correctness", "Safety", "Clarity"],
                        }
                    ],
                }
            ],
        }

    @staticmethod
    def _write_json(path: Path, payload: object) -> None:
        path.write_text(json.dumps(payload), encoding="utf-8")

    def test_loader_rejects_implicit_scalar_coercions(self):
        mutations = (
            lambda data: data.__setitem__("suite_id", 7),
            lambda data: data["jobs"][0].__setitem__("job_id", 7),
            lambda data: data["jobs"][0]["cases"][0].__setitem__("case_id", 7),
            lambda data: data["jobs"][0].__setitem__("domain", 7),
            lambda data: data["jobs"][0].__setitem__("source_prompt", 7),
            lambda data: data["jobs"][0]["cases"][0].__setitem__(
                "input_text", 7
            ),
            lambda data: data["jobs"][0]["cases"][0].__setitem__(
                "difficulty", 7
            ),
            lambda data: data["jobs"][0]["cases"][0].__setitem__(
                "require_json", "false"
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "benchmark.json"
            for mutation in mutations:
                with self.subTest(mutation=mutation):
                    data = self._valid_definition()
                    mutation(data)
                    self._write_json(path, data)
                    with self.assertRaises(ValueError):
                        load_benchmark(path)

    def test_loader_requires_exact_root_job_and_case_fields(self):
        mutations = (
            lambda data: data.__setitem__("unknown", True),
            lambda data: data.pop("suite_id"),
            lambda data: data["jobs"][0].__setitem__("unknown", True),
            lambda data: data["jobs"][0].pop("source_prompt"),
            lambda data: data["jobs"][0]["cases"][0].__setitem__(
                "unknown", True
            ),
            lambda data: data["jobs"][0]["cases"][0].pop("rubric"),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "benchmark.json"
            for mutation in mutations:
                with self.subTest(mutation=mutation):
                    data = self._valid_definition()
                    mutation(data)
                    self._write_json(path, data)
                    with self.assertRaises(ValueError):
                        load_benchmark_definition(path)

    def test_loader_rejects_wrong_array_shapes(self):
        mutations = (
            lambda data: data.__setitem__("jobs", {}),
            lambda data: data["jobs"][0].__setitem__("cases", {}),
            lambda data: data["jobs"][0]["cases"][0].__setitem__(
                "rubric", "Correctness"
            ),
            lambda data: data["jobs"][0]["cases"][0].__setitem__(
                "rubric", ["Correctness", 3]
            ),
            lambda data: data["jobs"][0]["cases"][0].__setitem__(
                "tags", ["safe", 3]
            ),
            lambda data: data["jobs"][0]["cases"][0].__setitem__(
                "required_substrings", "required"
            ),
            lambda data: data["jobs"][0]["cases"][0].__setitem__(
                "forbidden_substrings", [False]
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "benchmark.json"
            for mutation in mutations:
                with self.subTest(mutation=mutation):
                    data = self._valid_definition()
                    mutation(data)
                    self._write_json(path, data)
                    with self.assertRaises(ValueError):
                        load_benchmark(path)

    def test_max_characters_uses_json_mathematical_integer_semantics(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "benchmark.json"
            data = self._valid_definition()
            case = data["jobs"][0]["cases"][0]
            case["max_characters"] = 1200.0
            self._write_json(path, data)
            _, jobs = load_benchmark(path)
            self.assertEqual(jobs[0].cases[0].max_characters, 1200)

            for invalid in (True, 0, -1, 1.5, "1200", []):
                with self.subTest(invalid=invalid):
                    case["max_characters"] = invalid
                    self._write_json(path, data)
                    with self.assertRaises(ValueError):
                        load_benchmark(path)

    def test_loader_rejects_high_precision_near_integer(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "benchmark.json"
            payload = json.dumps(self._valid_definition(), separators=(",", ":"))
            payload = payload.replace(
                '"rubric":["Correctness","Safety","Clarity"]',
                '"rubric":["Correctness","Safety","Clarity"],'
                '"max_characters":1.00000000000000001',
            )
            path.write_text(payload, encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "positive integer"):
                load_benchmark_definition(path)

    def test_loader_rejects_duplicate_json_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "benchmark.json"
            path.write_text(
                '{"schema_version":"2.0.0","suite_id":"one",'
                '"suite_id":"two","jobs":[]}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "duplicate field"):
                load_benchmark(path)

    def test_catalog_requires_exact_unique_contained_string_includes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            benchmark_path = root / "benchmark.json"
            catalog_path = root / "catalog.json"
            self._write_json(benchmark_path, self._valid_definition())

            valid_catalog = {
                "schema_version": "2.0.0",
                "suite_id": "catalog-suite",
                "includes": ["benchmark.json"],
            }
            self._write_json(catalog_path, valid_catalog)
            suite_id, jobs = load_benchmark_catalog(catalog_path)
            self.assertEqual(suite_id, "catalog-suite")
            self.assertEqual(len(jobs), 1)

            invalid_catalogs = (
                {**valid_catalog, "unknown": True},
                {"schema_version": "2.0.0", "suite_id": "catalog-suite"},
                {**valid_catalog, "includes": "benchmark.json"},
                {**valid_catalog, "includes": [3]},
                {
                    **valid_catalog,
                    "includes": ["benchmark.json", ".\\benchmark.json"],
                },
                {**valid_catalog, "includes": ["..\\outside.json"]},
            )
            for catalog in invalid_catalogs:
                with self.subTest(catalog=catalog):
                    self._write_json(catalog_path, catalog)
                    with self.assertRaises(ValueError):
                        load_benchmark_definition(catalog_path)

    def test_domain_must_be_a_safe_output_identifier(self):
        case = case_from_dict(
            {
                "case_id": "unsafe-domain",
                "input_text": "Evaluate this sufficiently detailed benchmark input.",
                "rubric": ["Correctness", "Safety", "Clarity"],
                "domain": "../../escaped",
            }
        )

        failures = validate_benchmark(
            "suite",
            (
                BenchmarkJob(
                    job_id="unsafe-job",
                    domain="../../escaped",
                    source_prompt="Complete the supplied task.",
                    cases=(case,),
                ),
            ),
        )

        self.assertTrue(any("safe identifier" in failure for failure in failures))

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
