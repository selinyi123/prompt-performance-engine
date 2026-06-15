import unittest

from prompt_performance_engine.software_execution import (
    verify_cli,
    verify_concurrency,
    verify_endpoint,
    verify_migration,
    verify_pagination,
)


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
```"""
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
```"""
        )
        self.assertFalse(passed)
        self.assertIn("failed", detail.lower())

    def test_concurrency_contract_runs_in_restricted_subprocess(self):
        passed, detail = verify_concurrency(GOOD_CONCURRENCY)
        self.assertTrue(passed, detail)

    def test_global_lock_fails_independent_key_probe(self):
        passed, detail = verify_concurrency(BAD_GLOBAL_LOCK)
        self.assertFalse(passed)
        self.assertIn("failed", detail.lower())

    def test_generic_helper_class_is_safely_normalized(self):
        passed, detail = verify_concurrency(GENERIC_HELPER_CONCURRENCY)
        self.assertTrue(passed, detail)

    def test_nested_helper_class_is_supported(self):
        passed, detail = verify_concurrency(NESTED_HELPER_CONCURRENCY)
        self.assertTrue(passed, detail)

    def test_endpoint_contract_runs_in_restricted_subprocess(self):
        passed, detail = verify_endpoint(GOOD_ENDPOINT)
        self.assertTrue(passed, detail)

    def test_endpoint_schema_drift_fails(self):
        passed, _ = verify_endpoint(
            GOOD_ENDPOINT.replace('"status": 201', '"status": 200')
        )
        self.assertFalse(passed)

    def test_cli_contract_runs_in_restricted_subprocess(self):
        passed, detail = verify_cli(GOOD_CLI)
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
        passed, detail = verify_cli(output)
        self.assertTrue(passed, detail)

    def test_cli_does_not_execute_dynamic_module_dependencies(self):
        output = r"""```python
SUCCESS = compute_success()

def rename_cli(argv, exists, rename, emit):
    return SUCCESS
```"""
        passed, detail = verify_cli(output)
        self.assertFalse(passed)
        self.assertIn("SUCCESS", detail)

    def test_cli_allows_safe_startswith_validation(self):
        output = GOOD_CLI.replace(
            'if arg == "--dry-run":',
            'if arg.startswith("--dry") and arg == "--dry-run":',
        )
        passed, detail = verify_cli(output)
        self.assertTrue(passed, detail)

    def test_cli_dry_run_side_effect_fails(self):
        unsafe = GOOD_CLI.replace(
            'emit(source + " -> " + destination)',
            'rename(source, destination)',
        )
        passed, _ = verify_cli(unsafe)
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


if __name__ == "__main__":
    unittest.main()
