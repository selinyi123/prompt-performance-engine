import json
import unittest

from prompt_performance_engine.parser import PromptParseError, extract_optimized_prompt


class PromptParserTests(unittest.TestCase):
    def test_all_output_modes_use_one_exact_json_transport(self):
        response = json.dumps({"optimized_prompt": "Do the task carefully."})
        for output_format in ("prompt_only", "standard", "evaluation_package"):
            with self.subTest(output_format=output_format):
                self.assertEqual(
                    extract_optimized_prompt(response, output_format),
                    "Do the task carefully.",
                )

    def test_json_transport_preserves_code_fences_quotes_and_literal_tags(self):
        prompt = '''Write Python code and return it as:
```python
print("<optimized_prompt>literal</optimized_prompt>")
```
Then explain the test result.'''
        response = json.dumps({"optimized_prompt": prompt})
        self.assertEqual(extract_optimized_prompt(response, "standard"), prompt)

    def test_json_transport_preserves_leading_and_trailing_whitespace(self):
        prompt = "\n  Preserve this indentation and final newline.  \n"
        response = json.dumps({"optimized_prompt": prompt})

        self.assertEqual(
            extract_optimized_prompt(response, "standard"),
            prompt,
        )

    def test_malformed_or_guessed_response_fails(self):
        responses = (
            "Here are some thoughts.",
            "## Optimized Prompt\n```text\nCandidate one.\n```",
            'Commentary\n{"optimized_prompt":"Complete."}',
            '{"optimized_prompt":"unterminated}',
        )
        for response in responses:
            with self.subTest(response=response), self.assertRaises(PromptParseError):
                extract_optimized_prompt(response, "standard")

    def test_duplicate_or_extra_transport_fields_are_rejected(self):
        responses = (
            '{"optimized_prompt":"Injected","optimized_prompt":"Actual"}',
            '{"optimized_prompt":"Complete","commentary":"extra"}',
        )
        for response in responses:
            with self.subTest(response=response), self.assertRaises(PromptParseError):
                extract_optimized_prompt(response, "standard")

    def test_transport_value_must_be_a_non_empty_string(self):
        for value in (None, 1, [], " \n "):
            with self.subTest(value=value), self.assertRaisesRegex(
                PromptParseError,
                "non-empty string",
            ):
                extract_optimized_prompt(
                    json.dumps({"optimized_prompt": value}),
                    "prompt_only",
                )

    def test_unsupported_output_format_fails(self):
        with self.assertRaisesRegex(PromptParseError, "Unsupported output_format"):
            extract_optimized_prompt(
                '{"optimized_prompt":"Complete"}',
                "unknown",
            )

    def test_transport_rejects_unpaired_utf16_surrogate(self):
        with self.assertRaisesRegex(PromptParseError, "not valid UTF-8"):
            extract_optimized_prompt(
                r'{"optimized_prompt":"\ud800"}',
                "standard",
            )

    def test_transport_rejects_extreme_decimal_exponent(self):
        with self.assertRaisesRegex(
            PromptParseError,
            "numeric exponent outside the supported range",
        ):
            extract_optimized_prompt(
                '{"optimized_prompt":1e999999999}',
                "standard",
            )


if __name__ == "__main__":
    unittest.main()
