import json
import importlib.util
import shutil
import tempfile
import tomllib
import unittest
from pathlib import Path

from prompt_performance_engine.contracts import (
    ARTIFACT_SCHEMA_VERSION,
    LEGACY_ARTIFACT_PRODUCER_VERSIONS,
    LEGACY_ARTIFACT_SCHEMA_VERSION,
    PACKAGE_ROOT,
    PACKAGE_VERSION,
    SUPPORTED_ARTIFACT_PRODUCER_VERSIONS,
)
from prompt_performance_engine.frontier_contracts import (
    FRONTIER_CONTRACT_SCHEMA_VERSION,
)

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "validate_release",
    ROOT / "scripts" / "validate_release.py",
)
assert SPEC is not None and SPEC.loader is not None
VALIDATE_RELEASE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATE_RELEASE)


class ReleaseContractTests(unittest.TestCase):
    def test_generated_artifacts_are_excluded_from_source_scan(self):
        self.assertFalse(
            VALIDATE_RELEASE.is_release_source_path(
                ROOT / "artifacts" / "venv" / "third-party.py"
            )
        )
        self.assertTrue(
            VALIDATE_RELEASE.is_release_source_path(
                ROOT / "src" / "prompt_performance_engine" / "runtime.py"
            )
        )

    def test_pyproject_version_matches(self):
        source_version = (PACKAGE_ROOT / "VERSION").read_text(
            encoding="utf-8"
        ).strip()
        pyproject = tomllib.loads(
            (PACKAGE_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        )
        self.assertEqual(PACKAGE_VERSION, source_version)
        self.assertEqual(pyproject["project"]["version"], source_version)

    def test_release_validator_requires_every_schema(self):
        schema_paths = {
            path.relative_to(PACKAGE_ROOT).as_posix()
            for path in (PACKAGE_ROOT / "schemas").glob("*.json")
        }
        self.assertEqual(
            VALIDATE_RELEASE.SCHEMA_INVENTORY_PACKAGE_VERSION,
            PACKAGE_VERSION,
        )
        self.assertTrue(schema_paths.issubset(VALIDATE_RELEASE.REQUIRED_FILES))
        self.assertEqual(
            VALIDATE_RELEASE.REQUIRED_SCHEMA_FILES,
            frozenset(schema_paths),
        )
        self.assertEqual(VALIDATE_RELEASE.schema_inventory_failures(), [])

    def test_schema_inventory_detects_deleted_registered_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(PACKAGE_ROOT / "schemas", root / "schemas")
            (root / "schemas" / "frontier-report.schema.json").unlink()

            failures = VALIDATE_RELEASE.schema_inventory_failures(root)

        self.assertIn(
            "missing registered schema file: frontier-report.schema.json",
            failures,
        )

    def test_schema_inventory_detects_unregistered_new_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(PACKAGE_ROOT / "schemas", root / "schemas")
            (root / "schemas" / "unreviewed.schema.json").write_text(
                '{"$id":"https://example.invalid/unreviewed","type":"object"}\n',
                encoding="utf-8",
            )

            failures = VALIDATE_RELEASE.schema_inventory_failures(root)

        self.assertIn("unregistered schema file: unreviewed.schema.json", failures)

    def test_schema_references_reject_unregistered_external_id(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(PACKAGE_ROOT / "schemas", root / "schemas")
            path = root / "schemas" / "frontier-execution-bundle.schema.json"
            schema = json.loads(path.read_text(encoding="utf-8"))
            schema["$defs"]["execution"]["properties"]["execution_plan"]["$ref"] = (
                "urn:prompt-performance-engine:schema:frontier:missing:1.0.0"
            )
            path.write_text(json.dumps(schema), encoding="utf-8")

            failures = VALIDATE_RELEASE.schema_reference_failures(root)

        self.assertTrue(
            any("does not match a registered absolute schema $id" in item for item in failures),
            failures,
        )

    def test_schema_references_reject_missing_local_definition(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(PACKAGE_ROOT / "schemas", root / "schemas")
            path = root / "schemas" / "frontier-execution-bundle.schema.json"
            schema = json.loads(path.read_text(encoding="utf-8"))
            schema["properties"]["bundle_id"]["$ref"] = "#/$defs/missing"
            path.write_text(json.dumps(schema), encoding="utf-8")

            failures = VALIDATE_RELEASE.schema_reference_failures(root)

        self.assertIn(
            "frontier-execution-bundle.schema.json: unresolved schema $ref "
            "'#/$defs/missing'",
            failures,
        )

    def test_wheel_data_configuration_includes_schemas_and_frontier_specs(self):
        pyproject = tomllib.loads(
            (PACKAGE_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        )
        data_files = pyproject["tool"]["setuptools"]["data-files"]
        self.assertIn("schemas/*.json", data_files["prompt_performance_engine_data/schemas"])
        self.assertTrue(
            {
                "QUALITY-GATE-SPEC.md",
                "FRONTIER-EVIDENCE-CAMPAIGN.md",
                "STRONG-BASELINE-MATRIX.md",
            }.issubset(data_files["prompt_performance_engine_data"])
        )

    def test_frontier_governance_documents_are_release_required(self):
        self.assertTrue(
            VALIDATE_RELEASE.FRONTIER_NORMATIVE_DOCS.issubset(
                VALIDATE_RELEASE.REQUIRED_FILES
            )
        )
        self.assertEqual(
            VALIDATE_RELEASE.frontier_document_contract_failures(),
            [],
        )

    def test_frontier_status_separates_implementation_from_authority(self):
        status = set(VALIDATE_RELEASE.FRONTIER_CONTRACT_STATUS)
        self.assertIn("frontier_contract_package: 0.4.0", status)
        self.assertIn("frontier_machine_claim: not_evaluable", status)
        self.assertIn("frontier_contract_implemented: true", status)
        self.assertIn("frontier_preflight_contract_implemented: true", status)
        self.assertIn("frontier_execution_host_implemented: true", status)
        self.assertIn("frontier_offline_replay_implemented: true", status)
        self.assertIn("frontier_independent_authority_executed: false", status)
        self.assertIn("frontier_external_campaign_executed: false", status)
        self.assertNotIn("frontier_machine_claim: top_tier_scoped", status)
        self.assertNotIn("frontier_machine_claim: not_implemented", status)

    def test_frontier_document_contract_fails_when_boundary_marker_is_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "FRONTIER-EVIDENCE-CAMPAIGN.md").write_text(
                "QUALITY-GATE-SPEC.md\n"
                f"package `{PACKAGE_VERSION}`\n"
                "top_tier_scoped\n",
                encoding="utf-8",
            )

            failures = VALIDATE_RELEASE.frontier_document_contract_failures(root)

        self.assertTrue(
            any(
                "exact top-level frontier contract status block" in failure
                for failure in failures
            )
        )

    def test_hidden_markers_do_not_satisfy_frontier_document_contract(self):
        markers = "\n".join(VALIDATE_RELEASE.FRONTIER_CONTRACT_STATUS)
        hidden_markers = f"""<!--
{markers}
-->
```text
{markers}
```
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in (
                "README.md",
                "PRODUCT-SPEC.md",
                "ACCEPTANCE-CRITERIA.md",
                "QUALITY-GATE-SPEC.md",
                "FRONTIER-EVIDENCE-CAMPAIGN.md",
            ):
                (root / name).write_text(hidden_markers, encoding="utf-8")

            failures = VALIDATE_RELEASE.frontier_document_contract_failures(root)

        self.assertTrue(failures)

    def test_commonmark_code_variants_do_not_satisfy_frontier_contract(self):
        markers = "\n".join(VALIDATE_RELEASE.FRONTIER_CONTRACT_STATUS)
        variants = {
            "long-fence": f"````text\n```\n{markers}\n````\n",
            "blockquote-fence": "\n".join(
                f"> {line}" for line in f"```text\n{markers}\n```".splitlines()
            ),
            "blockquote-indent": "\n".join(
                f">     {line}" for line in markers.splitlines()
            ),
            "list-nested-fence": "- ```text\n  "
            + markers.replace("\n", "\n  ")
            + "\n  ```\n",
            "multiline-inline-code": f"``\n{markers}\n``\n",
            "link-reference-definition": f'[hidden]: / "{markers}"\n',
            "hidden-html": f"<div hidden>\n{markers}\n</div>\n",
        }
        for label, hidden_text in variants.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                for name in (
                    "README.md",
                    "PRODUCT-SPEC.md",
                    "ACCEPTANCE-CRITERIA.md",
                    "QUALITY-GATE-SPEC.md",
                    "FRONTIER-EVIDENCE-CAMPAIGN.md",
                ):
                    (root / name).write_text(hidden_text, encoding="utf-8")

                failures = VALIDATE_RELEASE.frontier_document_contract_failures(
                    root
                )

            self.assertTrue(failures, label)

    def test_non_markdown_line_separators_cannot_forge_status_block(self):
        separators = (
            "\v",
            "\f",
            "\x1c",
            "\x1d",
            "\x1e",
            "\x85",
            "\u2028",
            "\u2029",
        )
        for separator in separators:
            with (
                self.subTest(separator=repr(separator)),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                for name, title in VALIDATE_RELEASE.FRONTIER_CONTRACT_TITLES.items():
                    forged = separator.join(
                        (title, "", *VALIDATE_RELEASE.FRONTIER_CONTRACT_STATUS)
                    )
                    (root / name).write_text(forged, encoding="utf-8")

                failures = VALIDATE_RELEASE.frontier_document_contract_failures(
                    root
                )

            self.assertEqual(len(failures), 5, repr(separator))

    def test_schema_versions_match(self):
        for path in (PACKAGE_ROOT / "schemas").glob("*.json"):
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(
                data["$id"],
                VALIDATE_RELEASE.expected_schema_id(path.name),
                path.name,
            )
            package_contract = data.get("properties", {}).get("package_version")
            if package_contract:
                self.assertEqual(package_contract, {"const": PACKAGE_VERSION})

    def test_schema_family_versions_are_independent_and_stable(self):
        self.assertEqual(ARTIFACT_SCHEMA_VERSION, "2.0.0")
        self.assertEqual(FRONTIER_CONTRACT_SCHEMA_VERSION, "1.0.0")
        self.assertEqual(
            VALIDATE_RELEASE.SCHEMA_FAMILY_REGISTRY["stable"]["version"],
            ARTIFACT_SCHEMA_VERSION,
        )
        self.assertEqual(
            VALIDATE_RELEASE.SCHEMA_FAMILY_REGISTRY["frontier"]["version"],
            FRONTIER_CONTRACT_SCHEMA_VERSION,
        )
        self.assertEqual(
            VALIDATE_RELEASE.expected_schema_id(
                "optimization-artifact.schema.json"
            ),
            "https://local.invalid/prompt-performance/2.0.0/"
            "optimization-artifact.schema.json",
        )
        self.assertEqual(
            VALIDATE_RELEASE.expected_schema_id("frontier-report.schema.json"),
            "urn:prompt-performance-engine:schema:frontier:report:1.0.0",
        )

    def test_current_artifact_schema_is_not_a_legacy_compatibility_schema(self):
        schema = json.loads(
            (PACKAGE_ROOT / "schemas" / "optimization-artifact.schema.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            schema["properties"]["package_version"],
            {"const": PACKAGE_VERSION},
        )
        self.assertIn("source_prompt", schema["required"])
        self.assertTrue(
            {"purpose", "request_sha256", "response_sha256"}.issubset(
                schema["properties"]["runtime"]["properties"]["model_calls"][
                    "items"
                ]["required"]
            )
        )
        self.assertTrue(
            {"strategy", "strategy_focus"}.issubset(
                schema["properties"]["runtime"]["properties"]["selection"][
                    "properties"
                ]["candidates"]["items"]["required"]
            )
        )
        self.assertEqual(LEGACY_ARTIFACT_SCHEMA_VERSION, "1.0.0")
        self.assertNotEqual(LEGACY_ARTIFACT_SCHEMA_VERSION, ARTIFACT_SCHEMA_VERSION)
        self.assertEqual(
            SUPPORTED_ARTIFACT_PRODUCER_VERSIONS,
            LEGACY_ARTIFACT_PRODUCER_VERSIONS | {PACKAGE_VERSION},
        )

    def test_internal_schema_references_and_required_fields_resolve(self):
        def walk(value):
            if isinstance(value, dict):
                yield value
                for child in value.values():
                    yield from walk(child)
            elif isinstance(value, list):
                for child in value:
                    yield from walk(child)

        for path in (PACKAGE_ROOT / "schemas").glob("*.json"):
            schema = json.loads(path.read_text(encoding="utf-8"))
            for node in walk(schema):
                required = node.get("required")
                properties = node.get("properties")
                if isinstance(required, list) and isinstance(properties, dict):
                    self.assertTrue(
                        set(required).issubset(properties),
                        f"{path.name}: required field has no property schema",
                    )
                reference = node.get("$ref")
                if not isinstance(reference, str) or not reference.startswith("#/"):
                    continue
                target = schema
                for raw_part in reference[2:].split("/"):
                    part = raw_part.replace("~1", "/").replace("~0", "~")
                    self.assertIsInstance(
                        target,
                        dict,
                        f"{path.name}: invalid internal reference {reference}",
                    )
                    self.assertIn(
                        part,
                        target,
                        f"{path.name}: unresolved internal reference {reference}",
                    )
                    target = target[part]
        self.assertEqual(VALIDATE_RELEASE.schema_reference_failures(), [])

    def test_no_mojibake_in_primary_docs(self):
        for name in (
            "README.md",
            "PRODUCT-SPEC.md",
            "ARCHITECTURE.md",
            "ROADMAP.md",
            "ACCEPTANCE-CRITERIA.md",
            "MIGRATION-PLAN.md",
            "QUALITY-GATE-SPEC.md",
            "FRONTIER-EVIDENCE-CAMPAIGN.md",
        ):
            text = (PACKAGE_ROOT / name).read_text(encoding="utf-8")
            self.assertEqual(VALIDATE_RELEASE.find_mojibake(text), [], name)


if __name__ == "__main__":
    unittest.main()
