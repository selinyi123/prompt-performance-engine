import unittest

from prompt_performance_engine.case_checks import run_case_checks


GOOD_PAGINATION = """```python
def paginate(items, page, page_size):
    if not isinstance(page, int) or isinstance(page, bool):
        raise TypeError("page must be an integer")
    if not isinstance(page_size, int) or isinstance(page_size, bool):
        raise TypeError("page_size must be an integer")
    if page < 1 or page_size < 1:
        raise ValueError("page and page_size must be positive")
    start = (page - 1) * page_size
    return items[start:start + page_size]
```"""


class CaseCheckTests(unittest.TestCase):
    def test_valid_paginate_passes_restricted_behavior_checks(self):
        checks = run_case_checks("se-normal-pagination", GOOD_PAGINATION)
        self.assertEqual(len(checks), 3)
        self.assertTrue(all(check["passed"] for check in checks))

    def test_off_by_one_paginate_fails_behavior_vectors(self):
        checks = run_case_checks(
            "se-normal-pagination",
            """```python
def paginate(items, page, page_size):
    return items[page * page_size:(page + 1) * page_size]
```""",
        )
        self.assertFalse(
            next(
                check
                for check in checks
                if check["check"] == "pagination_behavior_vectors"
            )["passed"]
        )

    def test_imports_and_attribute_calls_are_rejected(self):
        checks = run_case_checks(
            "se-normal-pagination",
            """```python
def paginate(items, page, page_size):
    return __import__("os").listdir(".")
```""",
        )
        self.assertFalse(checks[0]["passed"])
        self.assertIn("Attribute", checks[0]["detail"])

    def test_bounded_type_error_wrapper_is_allowed(self):
        checks = run_case_checks(
            "se-normal-pagination",
            """```python
def paginate(items, page, page_size):
    if not isinstance(page, int) or isinstance(page, bool):
        raise TypeError("invalid page")
    if not isinstance(page_size, int) or isinstance(page_size, bool):
        raise TypeError("invalid page size")
    if page < 1 or page_size < 1:
        raise ValueError("values must be positive")
    try:
        start = (page - 1) * page_size
        return items[start:start + page_size]
    except TypeError as exc:
        raise TypeError("items must support slicing") from exc
```""",
        )
        self.assertTrue(all(check["passed"] for check in checks))

    def test_non_target_case_has_no_case_checks(self):
        self.assertEqual(run_case_checks("se-normal-cli", GOOD_PAGINATION), [])


if __name__ == "__main__":
    unittest.main()
