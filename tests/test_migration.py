import unittest

from prompt_performance_engine.hashing import hash_payload
from prompt_performance_engine.migration import (
    import_legacy_audit,
    migrate_legacy_prompt,
)


class MigrationTests(unittest.TestCase):
    def test_prompt_migration_preserves_source_and_drops_scores(self):
        package = migrate_legacy_prompt(
            "Write about {{TOPIC}}.",
            legacy_version="3.0",
            domain="professional_writing",
        )
        self.assertEqual(
            package["request"]["source_prompt"],
            "Write about {{TOPIC}}.",
        )
        self.assertNotIn("score", package["request"])
        self.assertEqual(
            package["migration_sha256"],
            hash_payload(package, "migration_sha256"),
        )

    def test_legacy_e5_never_elevates_current_evidence(self):
        imported = import_legacy_audit(
            {
                "framework_version": "4.0",
                "schema_version": "3.0",
                "evidence_level": "E5",
                "status": "production certified",
            }
        )
        self.assertEqual(imported["evidence"]["level"], "E0")
        self.assertEqual(imported["evidence"]["claim"], "legacy_reference_only")
        self.assertTrue(imported["version_conflict_detected"])


if __name__ == "__main__":
    unittest.main()
