import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from prompt_performance_engine.contracts import (
    INSTALLED_DATA_ROOTS,
    OPTIMIZATION_REQUEST_FIELDS,
    OPTIMIZATION_REQUEST_REQUIRED_FIELDS,
    PACKAGE_ROOT,
    OptimizationRequest,
    load_strict_json_object,
    parse_strict_json_object,
)


class OptimizationRequestTests(unittest.TestCase):
    def test_runtime_data_root_contains_version(self):
        self.assertTrue((PACKAGE_ROOT / "VERSION").is_file())
        self.assertEqual(len(INSTALLED_DATA_ROOTS), 2)

    def test_request_schema_matches_authoritative_contract_fields(self):
        schema = json.loads(
            (PACKAGE_ROOT / "schemas" / "optimization-request.schema.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(set(schema["properties"]), set(OPTIMIZATION_REQUEST_FIELDS))
        self.assertEqual(
            set(schema["required"]),
            set(OPTIMIZATION_REQUEST_REQUIRED_FIELDS),
        )
        self.assertEqual(schema["properties"]["candidate_count"]["minimum"], 1)
        self.assertEqual(schema["properties"]["candidate_count"]["maximum"], 5)

    def test_valid_request(self):
        request = OptimizationRequest(source_prompt="Write a product strategy.")
        request.validate()
        self.assertEqual(request.to_dict()["mode"], "maximum_quality")
        self.assertEqual(request.to_dict()["candidate_count"], 1)

    def test_empty_prompt_rejected(self):
        with self.assertRaises(ValueError):
            OptimizationRequest(source_prompt="  ").validate()

    def test_unknown_mode_rejected(self):
        with self.assertRaises(ValueError):
            OptimizationRequest(source_prompt="x", mode="extreme").validate()

    def test_all_scalar_fields_are_strictly_typed(self):
        invalid_fields = {
            "schema_version": 1,
            "source_prompt": ["x"],
            "mode": ["balanced"],
            "output_format": {"standard": True},
            "domain": 7,
            "audience": False,
            "target_model": ["model"],
            "target_surface": ["api"],
            "candidate_count": True,
        }
        for field, value in invalid_fields.items():
            with self.subTest(field=field):
                values = {"source_prompt": "x", field: value}
                with self.assertRaises(TypeError):
                    OptimizationRequest(**values).validate()

    def test_behavior_arrays_require_string_elements(self):
        for field in ("required_behaviors", "forbidden_changes"):
            with self.subTest(field=field, case="container"):
                with self.assertRaisesRegex(TypeError, "tuple of strings"):
                    OptimizationRequest(
                        source_prompt="x",
                        **{field: ["not-a-tuple"]},
                    ).validate()
            with self.subTest(field=field, case="element"):
                with self.assertRaisesRegex(TypeError, r"\[1\] must be a string"):
                    OptimizationRequest(
                        source_prompt="x",
                        **{field: ("valid", 3)},
                    ).validate()

    def test_from_dict_rejects_missing_unknown_and_pseudo_arrays(self):
        valid = {
            "schema_version": "2.0.0",
            "source_prompt": "Write a report.",
            "mode": "maximum_quality",
            "output_format": "standard",
        }
        with self.assertRaisesRegex(ValueError, "Missing required"):
            OptimizationRequest.from_dict({"source_prompt": "x"})
        with self.assertRaisesRegex(ValueError, "Unknown optimization request"):
            OptimizationRequest.from_dict({**valid, "ignored": True})
        with self.assertRaisesRegex(TypeError, "array of strings"):
            OptimizationRequest.from_dict(
                {**valid, "required_behaviors": "preserve citations"}
            )
        with self.assertRaisesRegex(TypeError, r"forbidden_changes\[0\]"):
            OptimizationRequest.from_dict({**valid, "forbidden_changes": [3]})

    def test_from_dict_preserves_candidate_count_and_service_surface_default(self):
        request = OptimizationRequest.from_dict(
            {
                "schema_version": "2.0.0",
                "source_prompt": "Write a report.",
                "mode": "maximum_quality",
                "output_format": "standard",
                "candidate_count": 3,
            },
            target_surface_default="api",
        )
        self.assertEqual(request.candidate_count, 3)
        self.assertEqual(request.target_surface, "api")

    def test_from_dict_uses_json_schema_integer_semantics(self):
        base = {
            "schema_version": "2.0.0",
            "source_prompt": "Write a report.",
            "mode": "maximum_quality",
            "output_format": "standard",
        }
        for value in (1.0, 3.0):
            with self.subTest(value=value):
                request = OptimizationRequest.from_dict(
                    {**base, "candidate_count": value}
                )
                self.assertIsInstance(request.candidate_count, int)
                self.assertEqual(request.candidate_count, int(value))

        for value in (Decimal("1.0"), Decimal("3.0")):
            with self.subTest(value=value):
                request = OptimizationRequest.from_dict(
                    {**base, "candidate_count": value}
                )
                self.assertEqual(request.candidate_count, int(value))

    def test_strict_json_preserves_near_integer_precision(self):
        data = parse_strict_json_object(
            '{"candidate_count":1.0000000000000000000000000001}',
            label="test payload",
        )
        self.assertEqual(
            data["candidate_count"],
            Decimal("1.0000000000000000000000000001"),
        )

    def test_strict_json_rejects_excessive_nesting_without_recursion_leak(self):
        payload = '{"value":' + "[" * 10_000 + "0" + "]" * 10_000 + "}"
        with self.assertRaisesRegex(ValueError, "must be valid JSON"):
            parse_strict_json_object(payload, label="test payload")

    def test_strict_json_rejects_unpaired_utf16_surrogates(self):
        for surrogate in (r"\ud800", r"\udfff"):
            with self.subTest(surrogate=surrogate), self.assertRaisesRegex(
                ValueError,
                "not valid UTF-8",
            ):
                parse_strict_json_object(
                    '{"value":"' + surrogate + '"}',
                    label="test payload",
                )

    def test_strict_json_rejects_extreme_decimal_exponents(self):
        for number in ("1e999999999", "1e-999999999"):
            with self.subTest(number=number), self.assertRaisesRegex(
                ValueError,
                "numeric exponent outside the supported range",
            ):
                parse_strict_json_object(
                    '{"value":' + number + "}",
                    label="test payload",
                )

    def test_strict_file_loader_rejects_duplicates_without_decimal_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.json"
            path.write_text('{"value":1.25,"value":2.5}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate field"):
                load_strict_json_object(path, label="authority source")

            path.write_text('{"value":1.25}', encoding="utf-8")
            loaded = load_strict_json_object(path, label="authority source")
            self.assertIs(type(loaded["value"]), float)
            self.assertEqual(loaded["value"], 1.25)

    def test_strict_file_loader_rejects_float_precision_loss(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.json"
            path.write_text(
                '{"value":1.00000000000000001}',
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "without precision loss"):
                load_strict_json_object(path, label="authority source")

            loaded = load_strict_json_object(
                path,
                label="authority source",
                preserve_decimal=True,
            )
            self.assertEqual(loaded["value"], Decimal("1.00000000000000001"))


if __name__ == "__main__":
    unittest.main()
