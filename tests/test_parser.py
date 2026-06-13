import unittest

from prompt_performance_engine.parser import PromptParseError, extract_optimized_prompt


class PromptParserTests(unittest.TestCase):
    def test_extracts_headed_prompt(self):
        response = """## 优化后的 Prompt

```text
Do the task carefully.
```

## 关键改进
- Clearer contract
"""
        self.assertEqual(
            extract_optimized_prompt(response, "standard"),
            "Do the task carefully.",
        )

    def test_prompt_only_accepts_one_block(self):
        self.assertEqual(
            extract_optimized_prompt("```text\nComplete prompt.\n```", "prompt_only"),
            "Complete prompt.",
        )

    def test_tagged_prompt_preserves_nested_code_fences(self):
        response = """## Optimized Prompt
<optimized_prompt>
Write Python code and return it as:
```python
print("hello")
```
Then explain the test result.
</optimized_prompt>

## Key Improvements
- Preserves nested fences
"""
        self.assertEqual(
            extract_optimized_prompt(response, "standard"),
            """Write Python code and return it as:
```python
print("hello")
```
Then explain the test result.""",
        )

    def test_malformed_standard_response_fails(self):
        with self.assertRaises(PromptParseError):
            extract_optimized_prompt("Here are some thoughts.", "standard")


if __name__ == "__main__":
    unittest.main()
