import json
import tempfile
import unittest
from pathlib import Path

from prompt_performance_engine.cli import (
    TOOL_PERMISSION_MANIFEST_FIELDS,
    TOOL_PERMISSION_MANIFEST_REQUIRED_FIELDS,
    _load_tool_permissions,
)
from prompt_performance_engine.contracts import PACKAGE_ROOT


class ToolPermissionLoaderTests(unittest.TestCase):
    def load(self, payload) -> object:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "permissions.json"
            text = payload if isinstance(payload, str) else json.dumps(payload)
            path.write_text(text, encoding="utf-8")
            return _load_tool_permissions(path)

    def test_schema_and_runtime_field_sets_match(self):
        schema = json.loads(
            (PACKAGE_ROOT / "schemas" / "tool-permission-manifest.schema.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            set(schema["properties"]),
            set(TOOL_PERMISSION_MANIFEST_FIELDS),
        )
        self.assertEqual(
            set(schema["required"]),
            set(TOOL_PERMISSION_MANIFEST_REQUIRED_FIELDS),
        )

    def test_valid_manifest_is_typed_without_truthiness_coercion(self):
        manifest = self.load(
            {
                "schema_version": "2.0.0",
                "allowed_executables": ["python"],
                "allowed_environment": ["API_KEY"],
                "working_directory": ".",
                "maximum_timeout_seconds": 2.5,
                "allow_sensitive_environment": True,
            }
        )
        self.assertEqual(manifest.allowed_executables, ("python",))
        self.assertEqual(manifest.allowed_environment, ("API_KEY",))
        self.assertEqual(manifest.maximum_timeout_seconds, 2.5)
        self.assertIs(manifest.allow_sensitive_environment, True)
        self.assertTrue(manifest.working_directory.is_absolute())

    def test_duplicate_unknown_missing_and_wrong_schema_fields_are_rejected(self):
        payloads = (
            '{"schema_version":"2.0.0","allowed_executables":["python"],'
            '"allowed_executables":["other"]}',
            {
                "schema_version": "2.0.0",
                "allowed_executables": ["python"],
                "unknown": True,
            },
            {"schema_version": "2.0.0"},
            {"schema_version": "1.0.0", "allowed_executables": ["python"]},
        )
        for payload in payloads:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.load(payload)

    def test_executable_and_environment_arrays_are_strict(self):
        payloads = (
            {
                "schema_version": "2.0.0",
                "allowed_executables": "python",
            },
            {"schema_version": "2.0.0", "allowed_executables": []},
            {"schema_version": "2.0.0", "allowed_executables": [3]},
            {"schema_version": "2.0.0", "allowed_executables": [" "]},
            {
                "schema_version": "2.0.0",
                "allowed_executables": ["python"],
                "allowed_environment": "PATH",
            },
            {
                "schema_version": "2.0.0",
                "allowed_executables": ["python"],
                "allowed_environment": [False],
            },
        )
        for payload in payloads:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.load(payload)

    def test_sensitive_environment_flag_requires_a_json_boolean(self):
        for value in ("false", "true", 0, 1, None):
            payload = {
                "schema_version": "2.0.0",
                "allowed_executables": ["python"],
                "allowed_environment": ["API_KEY"],
                "allow_sensitive_environment": value,
            }
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.load(payload)

        with self.assertRaisesRegex(ValueError, "Sensitive environment"):
            self.load(
                {
                    "schema_version": "2.0.0",
                    "allowed_executables": ["python"],
                    "allowed_environment": ["API_KEY"],
                    "allow_sensitive_environment": False,
                }
            )

    def test_working_directory_and_timeout_types_and_bounds_are_strict(self):
        invalid_values = (True, "2", 0, -1, 3600.1)
        for value in invalid_values:
            payload = {
                "schema_version": "2.0.0",
                "allowed_executables": ["python"],
                "maximum_timeout_seconds": value,
            }
            with self.subTest(timeout=value), self.assertRaises(ValueError):
                self.load(payload)

        with self.assertRaises(ValueError):
            self.load(
                {
                    "schema_version": "2.0.0",
                    "allowed_executables": ["python"],
                    "working_directory": 1,
                }
            )

    def test_timeout_decimal_bounds_are_checked_before_float_conversion(self):
        invalid_documents = (
            '{"schema_version":"2.0.0","allowed_executables":["python"],'
            '"maximum_timeout_seconds":3600.000000000000000001}',
            '{"schema_version":"2.0.0","allowed_executables":["python"],'
            '"maximum_timeout_seconds":1e-10000}',
            '{"schema_version":"2.0.0","allowed_executables":["python"],'
            '"maximum_timeout_seconds":3599.999999999999999999}',
            '{"schema_version":"2.0.0","allowed_executables":["python"],'
            '"maximum_timeout_seconds":1' + "0" * 4000 + '}',
        )
        for document in invalid_documents:
            with self.subTest(document=document), self.assertRaises(ValueError):
                self.load(document)

        manifest = self.load(
            '{"schema_version":"2.0.0","allowed_executables":["python"],'
            '"maximum_timeout_seconds":0.1}'
        )
        self.assertEqual(manifest.maximum_timeout_seconds, 0.1)


if __name__ == "__main__":
    unittest.main()
