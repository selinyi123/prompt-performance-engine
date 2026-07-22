import unittest
from unittest.mock import Mock, patch

from prompt_performance_engine.case_checks import run_case_checks
from prompt_performance_engine.software_sandbox import DockerSandbox, SandboxRun


IMAGE = "python:3.13-alpine@sha256:" + "d" * 64


def fake_sandbox(*, passed: bool = True) -> DockerSandbox:
    with patch(
        "prompt_performance_engine.software_sandbox.shutil.which",
        return_value="docker",
    ):
        sandbox = DockerSandbox(IMAGE)
    sandbox.run_script = Mock(
        return_value=SandboxRun(
            passed=passed,
            detail="verified" if passed else "harness failed",
            stdout='PPE_PYTHON_VERSION=3.13.14\n{"status": "passed"}\n',
            stderr="",
            exit_code=0 if passed else 1,
            elapsed_ms=1,
            timed_out=False,
            oom_killed=False,
            image_reference=IMAGE,
            image_id="sha256:" + "d" * 64,
            python_version="3.13.14",
            probe_facts={},
            policy={"network_mode": "none"},
            policy_verified=True,
        )
    )
    return sandbox


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
        checks = run_case_checks(
            "se-normal-pagination",
            GOOD_PAGINATION,
            sandbox=fake_sandbox(),
        )
        self.assertEqual(len(checks), 1)
        self.assertTrue(all(check["passed"] for check in checks))

    def test_off_by_one_paginate_fails_behavior_vectors(self):
        checks = run_case_checks(
            "se-normal-pagination",
            """```python
def paginate(items, page, page_size):
    return items[page * page_size:(page + 1) * page_size]
```""",
            sandbox=fake_sandbox(passed=False),
        )
        self.assertFalse(checks[0]["passed"])

    def test_imports_and_attribute_calls_are_rejected(self):
        checks = run_case_checks(
            "se-normal-pagination",
            """```python
def paginate(items, page, page_size):
    return __import__("os").listdir(".")
```""",
            sandbox=fake_sandbox(),
        )
        self.assertFalse(checks[0]["passed"])
        self.assertIn("Disallowed", checks[0]["detail"])

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
            sandbox=fake_sandbox(),
        )
        self.assertTrue(all(check["passed"] for check in checks))

    def test_non_target_case_has_no_case_checks(self):
        self.assertEqual(run_case_checks("ra-normal-market", GOOD_PAGINATION), [])

    def test_software_case_fails_closed_without_docker_sandbox(self):
        checks = run_case_checks("se-normal-pagination", GOOD_PAGINATION)

        self.assertEqual(len(checks), 1)
        self.assertFalse(checks[0]["passed"])
        self.assertIn("DockerSandbox", checks[0]["detail"])

    def test_all_five_software_cases_have_case_plugins(self):
        case_ids = (
            "se-normal-pagination",
            "se-difficult-concurrency",
            "se-adversarial-contract",
            "se-normal-cli",
            "se-difficult-migration",
        )
        for case_id in case_ids:
            with self.subTest(case_id=case_id):
                self.assertTrue(
                    run_case_checks(
                        case_id,
                        "not executable",
                        sandbox=fake_sandbox(),
                    )
                )


if __name__ == "__main__":
    unittest.main()
