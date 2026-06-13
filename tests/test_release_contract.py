import json
import unittest
from pathlib import Path

from prompt_performance_engine.contracts import (
    ARTIFACT_SCHEMA_VERSION,
    PACKAGE_ROOT,
    PACKAGE_VERSION,
)


class ReleaseContractTests(unittest.TestCase):
    def test_pyproject_version_matches(self):
        pyproject = (PACKAGE_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn(f'version = "{PACKAGE_VERSION}"', pyproject)

    def test_schema_versions_match(self):
        for path in (PACKAGE_ROOT / "schemas").glob("*.json"):
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertIn(f"/{ARTIFACT_SCHEMA_VERSION}/", data["$id"])
            package_contract = data.get("properties", {}).get("package_version")
            if package_contract:
                self.assertEqual(package_contract["const"], PACKAGE_VERSION)

    def test_no_mojibake_in_primary_docs(self):
        markers = tuple(
            value.encode("ascii").decode("unicode_escape")
            for value in (
                r"\u951b",
                r"\u9286",
                r"\u9225",
                r"\u5a34",
                r"\u7487",
                r"\u9356",
            )
        )
        for name in (
            "README.md",
            "PRODUCT-SPEC.md",
            "ARCHITECTURE.md",
            "ROADMAP.md",
            "ACCEPTANCE-CRITERIA.md",
            "MIGRATION-PLAN.md",
        ):
            text = (PACKAGE_ROOT / name).read_text(encoding="utf-8")
            self.assertFalse(any(marker in text for marker in markers), name)


if __name__ == "__main__":
    unittest.main()
