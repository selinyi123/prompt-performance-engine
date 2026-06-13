import unittest

from prompt_performance_engine.domain_checks import (
    DOMAIN_CHECKS,
    run_domain_checks,
)


class DomainCheckTests(unittest.TestCase):
    def test_every_required_domain_has_plugin(self):
        self.assertEqual(len(DOMAIN_CHECKS), 12)

    def test_translation_placeholder_loss_fails(self):
        checks = run_domain_checks(
            "translation_localization",
            "Hello {{USER_NAME}}, open {{RESET_URL}}.",
            "你好，请打开链接。",
        )
        self.assertFalse(checks[0]["passed"])
        self.assertIn("RESET_URL", checks[0]["detail"])

    def test_high_risk_without_escalation_fails(self):
        checks = run_domain_checks(
            "high_risk_advisory",
            "Recurring chest pain",
            "This is probably harmless.",
        )
        self.assertFalse(checks[0]["passed"])

    def test_agent_secret_value_fails(self):
        checks = run_domain_checks(
            "agents_automation",
            "Report status.",
            "api_key = abcdefghijklmnop",
        )
        self.assertFalse(checks[0]["passed"])

    def test_clean_software_output_passes(self):
        checks = run_domain_checks(
            "software_engineering",
            "Implement a parser.",
            "def parse(value):\n    return value.strip()\n",
        )
        self.assertTrue(checks[0]["passed"])


if __name__ == "__main__":
    unittest.main()
