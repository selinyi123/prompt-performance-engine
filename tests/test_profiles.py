import unittest

from prompt_performance_engine.profiles import load_profiles, resolve_profile


class DomainProfileTests(unittest.TestCase):
    def test_registry_has_initial_profiles(self):
        profiles = load_profiles()
        self.assertEqual(len(profiles), 13)
        self.assertIn("generic", profiles)
        self.assertTrue(all(profile.observable_checks for profile in profiles.values()))

    def test_software_prompt_resolves(self):
        profile = resolve_profile("Write Python code with tests for this API.")
        self.assertEqual(profile.id, "software_engineering")

    def test_explicit_domain_wins(self):
        profile = resolve_profile("Write code.", explicit_domain="business_strategy")
        self.assertEqual(profile.id, "business_strategy")

    def test_new_domains_resolve(self):
        cases = {
            "Extract records under this JSON schema.": "structured_data",
            "Create a landing page conversion campaign.": "marketing_sales",
            "Design a lesson with a quiz for students.": "education",
            "Translate and localize this glossary for the locale.": "translation_localization",
            "Build an agent workflow with tool call approval.": "agents_automation",
            "Provide a careful medical diagnosis boundary.": "high_risk_advisory",
        }
        for prompt, expected in cases.items():
            with self.subTest(prompt=prompt):
                self.assertEqual(resolve_profile(prompt).id, expected)


if __name__ == "__main__":
    unittest.main()
