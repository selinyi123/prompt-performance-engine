import json
import unittest
from pathlib import Path

from prompt_performance_engine.audit import audit_prompt
from prompt_performance_engine.contracts import PACKAGE_ROOT


class StaticAuditTests(unittest.TestCase):
    def ids(self, text, source_prompt=None):
        return {
            finding.rule_id
            for finding in audit_prompt(
                text,
                source_prompt=source_prompt,
            ).findings
        }

    def test_clean_prompt_passes(self):
        report = audit_prompt("Summarize the article in five concise bullets.")
        self.assertTrue(report.passed)
        self.assertEqual(report.findings, ())

    def test_template_variable_loss_blocks_static_evidence(self):
        report = audit_prompt(
            "Write a useful report.",
            source_prompt="Write about {{TOPIC}} for {{AUDIENCE}}.",
        )
        self.assertFalse(report.passed)
        self.assertIn("C02_template_variable_loss", self.ids(
            "Write a useful report.",
            "Write about {{TOPIC}} for {{AUDIENCE}}.",
        ))

    def test_conflicting_output_formats_are_detected(self):
        report = audit_prompt("Return only JSON. Then output only Markdown.")
        self.assertFalse(report.passed)
        self.assertIn(
            "C01_conflicting_output_contract",
            {finding.rule_id for finding in report.findings},
        )

    def test_high_risk_prompt_requires_boundary(self):
        report = audit_prompt("Provide a medical diagnosis and prescription.")
        self.assertFalse(report.passed)
        self.assertIn(
            "R01_missing_high_risk_boundary",
            {finding.rule_id for finding in report.findings},
        )

    def test_all_migrated_adversarial_cases(self):
        root = PACKAGE_ROOT / "adversarial_cases"
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(len(manifest["cases"]), 20)
        for case in manifest["cases"]:
            with self.subTest(case=case["case_id"]):
                text = (root / case["path"]).read_text(encoding="utf-8")
                observed = {
                    finding.rule_id
                    for finding in audit_prompt(text).findings
                }
                self.assertTrue(
                    set(case["expected_hooks"]).issubset(observed),
                    f"expected {case['expected_hooks']}, observed {sorted(observed)}",
                )


if __name__ == "__main__":
    unittest.main()
