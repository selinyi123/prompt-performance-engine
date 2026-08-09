import subprocess
import sys
import time
import unittest
from unittest.mock import Mock, patch

from prompt_performance_engine.software_execution import (
    verify_cli,
    verify_concurrency,
    verify_endpoint,
    verify_migration,
    verify_pagination,
)
from prompt_performance_engine.software_sandbox import DockerSandbox, SandboxRun


IMAGE = "python:3.13-alpine@sha256:" + "d" * 64


def fake_sandbox(
    *,
    passed: bool = True,
    policy_verified: bool = True,
) -> DockerSandbox:
    with patch(
        "prompt_performance_engine.software_sandbox.shutil.which",
        return_value="docker",
    ):
        sandbox = DockerSandbox(IMAGE)
    sandbox.run_script = Mock(
        return_value=SandboxRun(
            passed=passed,
            detail=(
                "Docker sandbox execution passed with verified runtime policy."
                if passed
                else "Docker sandbox execution failed: AssertionError"
            ),
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
            policy_verified=policy_verified,
        )
    )
    return sandbox


def executing_test_sandbox() -> DockerSandbox:
    """Run trusted fixtures locally while production remains Docker-only."""
    sandbox = fake_sandbox()

    def run_script(script: str, *, timeout_seconds: float = 8.0) -> SandboxRun:
        started = time.monotonic()
        try:
            completed = subprocess.run(
                [sys.executable, "-I", "-S", "-c", script],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return SandboxRun(
                passed=False,
                detail="Trusted test harness timed out.",
                stdout=exc.stdout or "",
                stderr=exc.stderr or "",
                exit_code=None,
                elapsed_ms=int((time.monotonic() - started) * 1000),
                timed_out=True,
                oom_killed=False,
                image_reference=IMAGE,
                image_id="sha256:" + "d" * 64,
                python_version=None,
                probe_facts={},
                policy={"test_only": True},
                policy_verified=True,
            )
        stderr_lines = completed.stderr.strip().splitlines()
        detail = (
            "Trusted test harness passed."
            if completed.returncode == 0
            else "Trusted test harness failed: "
            + (stderr_lines[-1] if stderr_lines else "unknown error")
        )
        return SandboxRun(
            passed=completed.returncode == 0,
            detail=detail,
            stdout=completed.stdout,
            stderr=completed.stderr,
            exit_code=completed.returncode,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            timed_out=False,
            oom_killed=False,
            image_reference=IMAGE,
            image_id="sha256:" + "d" * 64,
            python_version=None,
            probe_facts={},
            policy={"test_only": True},
            policy_verified=True,
        )

    sandbox.run_script.side_effect = run_script
    return sandbox


GOOD_CONCURRENCY = r"""```python
class SingleFlightCache:
    def __init__(self, fetch):
        self._fetch = fetch
        self._lock = threading.Lock()
        self._values = {}
        self._flights = {}

    def get(self, key):
        with self._lock:
            if key in self._values:
                return self._values[key]
            flight = self._flights.get(key)
            if flight is None:
                flight = {
                    "event": threading.Event(),
                    "value": None,
                    "error": None,
                }
                self._flights[key] = flight
                leader = True
            else:
                leader = False

        if leader:
            try:
                value = self._fetch(key)
            except BaseException as error:
                with self._lock:
                    flight["error"] = error
                    self._flights.pop(key, None)
                    flight["event"].set()
                raise
            else:
                with self._lock:
                    self._values[key] = value
                    flight["value"] = value
                    self._flights.pop(key, None)
                    flight["event"].set()
                return value

        flight["event"].wait()
        if flight["error"] is not None:
            raise flight["error"]
        return flight["value"]
```"""

GENERIC_HELPER_CONCURRENCY = r"""```python
from typing import Generic, TypeVar

V = TypeVar("V")

class _Flight(Generic[V]):
    def __init__(self):
        self.event = threading.Event()
        self.value = None
        self.error = None

class SingleFlightCache(Generic[V]):
    def __init__(self, fetch):
        if not callable(fetch):
            raise TypeError("fetch must be callable")
        self._fetch = fetch
        self._lock = threading.Lock()
        self._values = {}
        self._flights = {}

    def get(self, key):
        with self._lock:
            if key in self._values:
                return self._values[key]
            flight = self._flights.get(key)
            if flight is None:
                flight = _Flight()
                self._flights[key] = flight
                leader = True
            else:
                leader = False
        if leader:
            try:
                value = self._fetch(key)
            except BaseException as error:
                with self._lock:
                    flight.error = error
                    self._flights.pop(key, None)
                    flight.event.set()
                raise
            with self._lock:
                self._values[key] = value
                flight.value = value
                self._flights.pop(key, None)
                flight.event.set()
            return value
        flight.event.wait()
        if flight.error is not None:
            raise flight.error
        return flight.value
```"""

BAD_GLOBAL_LOCK = r"""```python
class SingleFlightCache:
    def __init__(self, fetch):
        self._fetch = fetch
        self._lock = threading.Lock()
        self._values = {}

    def get(self, key):
        with self._lock:
            if key not in self._values:
                self._values[key] = self._fetch(key)
            return self._values[key]
```"""

NESTED_HELPER_CONCURRENCY = r"""```python
class SingleFlightCache:
    class _Flight:
        def __init__(self):
            self.event = threading.Event()
            self.value = None
            self.error = None

    def __init__(self, fetch):
        self._fetch = fetch
        self._lock = threading.Lock()
        self._values = {}
        self._flights = {}

    def get(self, key):
        with self._lock:
            if key in self._values:
                return self._values[key]
            flight = self._flights.get(key)
            if flight is None:
                flight = self._Flight()
                self._flights[key] = flight
                leader = True
            else:
                leader = False
        if leader:
            try:
                value = self._fetch(key)
            except BaseException as error:
                with self._lock:
                    flight.error = error
                    self._flights.pop(key, None)
                    flight.event.set()
                raise
            with self._lock:
                self._values[key] = value
                flight.value = value
                self._flights.pop(key, None)
                flight.event.set()
            return value
        flight.event.wait()
        if flight.error is not None:
            raise flight.error
        return flight.value
```"""

GOOD_ENDPOINT = r"""```python
def handle_request(request, authenticate, create_item):
    token = request.get("token")
    user = authenticate(token)
    if user is None:
        return {
            "status": 401,
            "body": {"error": {"code": "unauthorized"}},
        }
    payload = request.get("json")
    if (
        not isinstance(payload, dict)
        or not isinstance(payload.get("name"), str)
        or not payload.get("name").strip()
    ):
        return {
            "status": 400,
            "body": {"error": {"code": "invalid_request"}},
        }
    item = create_item(user, payload)
    return {"status": 201, "body": {"item": item}}
```"""

GOOD_CLI = r"""```python
def rename_cli(argv, exists, rename, emit):
    arguments = list(argv)
    dry_run = False
    if "--dry-run" in arguments:
        arguments.remove("--dry-run")
        dry_run = True
    if len(arguments) != 2:
        return 2
    source, destination = arguments
    if not exists(source):
        return 2
    if exists(destination):
        return 3
    if dry_run:
        emit(source + " -> " + destination)
    else:
        rename(source, destination)
    return 0
```"""

GOOD_MIGRATION = r"""```json
{
  "phases": [
    {
      "name": "expand",
      "actions": ["add nullable replacement field"],
      "old_reader_supported": true,
      "new_reader_supported": false,
      "old_writer_supported": true,
      "new_writer_supported": false,
      "rollback_supported": true
    },
    {
      "name": "bridge",
      "actions": ["deploy fallback reads", "synchronize both write paths"],
      "old_reader_supported": true,
      "new_reader_supported": true,
      "old_writer_supported": true,
      "new_writer_supported": true,
      "rollback_supported": true,
      "synchronizes_old_writer_inserts": true,
      "synchronizes_old_writer_updates": true
    },
    {
      "name": "backfill",
      "actions": ["run bounded idempotent batches"],
      "old_reader_supported": true,
      "new_reader_supported": true,
      "old_writer_supported": true,
      "new_writer_supported": true,
      "rollback_supported": true
    },
    {
      "name": "cutover",
      "actions": ["switch reads while preserving dual writes"],
      "old_reader_supported": true,
      "new_reader_supported": true,
      "old_writer_supported": true,
      "new_writer_supported": true,
      "rollback_supported": true
    },
    {
      "name": "contract",
      "actions": ["retire old versions", "drop legacy field"],
      "old_reader_supported": false,
      "new_reader_supported": true,
      "old_writer_supported": false,
      "new_writer_supported": true,
      "rollback_supported": false,
      "drops_legacy_field": true,
      "enforces_new_not_null": true
    }
  ]
}
```"""


class SoftwareExecutionTests(unittest.TestCase):
    def test_executable_checks_fail_closed_without_docker_sandbox(self):
        with patch("subprocess.run") as host_run:
            passed, detail = verify_pagination(
                """```python
def paginate(items, page, page_size):
    return items
```"""
            )

        self.assertFalse(passed)
        self.assertIn("DockerSandbox", detail)
        host_run.assert_not_called()

    def test_executable_checks_reject_unverified_runtime_policy(self):
        sandbox = fake_sandbox(policy_verified=False)

        passed, detail = verify_endpoint(GOOD_ENDPOINT, sandbox=sandbox)

        self.assertFalse(passed)
        self.assertIn("policy", detail)

    def test_pagination_contract_runs_in_restricted_subprocess(self):
        passed, detail = verify_pagination(
            """```python
def paginate(items, page, page_size):
    if isinstance(page, bool) or not isinstance(page, int):
        raise TypeError("invalid page")
    if isinstance(page_size, bool) or not isinstance(page_size, int):
        raise TypeError("invalid page size")
    if page < 1 or page_size < 1:
        raise ValueError("must be positive")
    start = (page - 1) * page_size
    return items[start:start + page_size]
```""",
            sandbox=executing_test_sandbox(),
        )
        self.assertTrue(passed, detail)

    def test_pagination_list_conversion_breaks_tuple_contract(self):
        passed, detail = verify_pagination(
            """```python
def paginate(items, page, page_size):
    if page < 1 or page_size < 1:
        raise ValueError("must be positive")
    start = (page - 1) * page_size
    return list(items)[start:start + page_size]
```""",
            sandbox=executing_test_sandbox(),
        )
        self.assertFalse(passed)
        self.assertIn("failed", detail.lower())

    def test_concurrency_contract_runs_in_restricted_subprocess(self):
        passed, detail = verify_concurrency(
            GOOD_CONCURRENCY,
            sandbox=executing_test_sandbox(),
        )
        self.assertTrue(passed, detail)

    def test_global_lock_fails_independent_key_probe(self):
        passed, detail = verify_concurrency(
            BAD_GLOBAL_LOCK,
            sandbox=executing_test_sandbox(),
        )
        self.assertFalse(passed)
        self.assertIn("failed", detail.lower())

    def test_generic_helper_class_is_safely_normalized(self):
        passed, detail = verify_concurrency(
            GENERIC_HELPER_CONCURRENCY,
            sandbox=executing_test_sandbox(),
        )
        self.assertTrue(passed, detail)

    def test_nested_helper_class_is_supported(self):
        passed, detail = verify_concurrency(
            NESTED_HELPER_CONCURRENCY,
            sandbox=executing_test_sandbox(),
        )
        self.assertTrue(passed, detail)

    def test_endpoint_contract_runs_in_restricted_subprocess(self):
        passed, detail = verify_endpoint(
            GOOD_ENDPOINT,
            sandbox=executing_test_sandbox(),
        )
        self.assertTrue(passed, detail)

    def test_endpoint_schema_drift_fails(self):
        passed, _ = verify_endpoint(
            GOOD_ENDPOINT.replace('"status": 201', '"status": 200'),
            sandbox=executing_test_sandbox(),
        )
        self.assertFalse(passed)

    def test_cli_contract_runs_in_restricted_subprocess(self):
        passed, detail = verify_cli(GOOD_CLI, sandbox=executing_test_sandbox())
        self.assertTrue(passed, detail)

    def test_cli_allows_safe_module_constants(self):
        output = r"""```python
SUCCESS = 0
USAGE_OR_MISSING_SOURCE = 2
DESTINATION_COLLISION = 3

def rename_cli(argv, exists, rename, emit):
    arguments = list(argv)
    dry_run = "--dry-run" in arguments
    if dry_run:
        arguments.remove("--dry-run")
    if len(arguments) != 2:
        return USAGE_OR_MISSING_SOURCE
    source, destination = arguments
    if not exists(source):
        return USAGE_OR_MISSING_SOURCE
    if exists(destination):
        return DESTINATION_COLLISION
    if dry_run:
        emit(source + " -> " + destination)
    else:
        rename(source, destination)
    return SUCCESS
```"""
        passed, detail = verify_cli(output, sandbox=executing_test_sandbox())
        self.assertTrue(passed, detail)

    def test_cli_does_not_execute_dynamic_module_dependencies(self):
        output = r"""```python
SUCCESS = compute_success()

def rename_cli(argv, exists, rename, emit):
    return SUCCESS
```"""
        passed, detail = verify_cli(output, sandbox=executing_test_sandbox())
        self.assertFalse(passed)
        self.assertIn("SUCCESS", detail)

    def test_cli_allows_safe_startswith_validation(self):
        output = GOOD_CLI.replace(
            'if arg == "--dry-run":',
            'if arg.startswith("--dry") and arg == "--dry-run":',
        )
        passed, detail = verify_cli(output, sandbox=executing_test_sandbox())
        self.assertTrue(passed, detail)

    def test_cli_dry_run_side_effect_fails(self):
        unsafe = GOOD_CLI.replace(
            'emit(source + " -> " + destination)',
            'rename(source, destination)',
        )
        passed, _ = verify_cli(unsafe, sandbox=executing_test_sandbox())
        self.assertFalse(passed)

    def test_migration_machine_contract_passes(self):
        passed, detail = verify_migration(GOOD_MIGRATION)
        self.assertTrue(passed, detail)

    def test_migration_requires_insert_synchronization(self):
        unsafe = GOOD_MIGRATION.replace(
            '"synchronizes_old_writer_inserts": true',
            '"synchronizes_old_writer_inserts": false',
        )
        passed, detail = verify_migration(unsafe)
        self.assertFalse(passed)
        self.assertIn("inserts", detail)

    def test_migration_rejects_duplicate_json_fields(self):
        ambiguous = GOOD_MIGRATION.replace(
            '"synchronizes_old_writer_inserts": true',
            '"synchronizes_old_writer_inserts": false,\n'
            '      "synchronizes_old_writer_inserts": true',
        )

        passed, detail = verify_migration(ambiguous)

        self.assertFalse(passed)
        self.assertIn("No valid migration-plan JSON object", detail)

    def test_restricted_code_rejects_dunder_escape(self):
        output = """```python
def handle_request(request, authenticate, create_item):
    return request.__class__.__mro__
```"""
        passed, detail = verify_endpoint(output)
        self.assertFalse(passed)
        self.assertIn("Dunder", detail)

    def test_restricted_code_rejects_dunder_method_definition(self):
        output = """```python
class SingleFlightCache:
    def __init__(self, fetch):
        self._fetch = fetch

    def __getattribute__(self, name):
        return object

    def get(self, key):
        return self._fetch(key)
```"""
        passed, detail = verify_concurrency(output)
        self.assertFalse(passed)
        self.assertIn("dunder method definition", detail)

    def test_restricted_code_rejects_threading_module_alias_statically(self):
        sandbox = fake_sandbox()
        output = """```python
def handle_request(request, authenticate, create_item):
    module_alias = threading
    return module_alias._os
```"""

        passed, detail = verify_endpoint(output, sandbox=sandbox)

        self.assertFalse(passed)
        self.assertIn("threading module", detail)
        sandbox.run_script.assert_not_called()

    def test_restricted_code_rejects_indirect_callable_statically(self):
        sandbox = fake_sandbox()
        output = """```python
def handle_request(request, authenticate, create_item):
    def invoke(fn, value):
        return fn(value)
    module_alias = threading
    os_alias = module_alias._os
    return invoke(os_alias.system, "PPE_ESCAPE_SENTINEL")
```"""

        passed, detail = verify_endpoint(output, sandbox=sandbox)

        self.assertFalse(passed)
        self.assertIn("Indirect callable", detail)
        sandbox.run_script.assert_not_called()


if __name__ == "__main__":
    unittest.main()
