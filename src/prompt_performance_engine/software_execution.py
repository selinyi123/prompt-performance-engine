"""Restricted executable verification for benchmark-owned software contracts."""

from __future__ import annotations

import ast
import copy
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


PYTHON_BLOCK_RE = re.compile(
    r"```(?:python|py)\s*\r?\n(.*?)```",
    flags=re.IGNORECASE | re.DOTALL,
)
JSON_BLOCK_RE = re.compile(
    r"```json\s*\r?\n(.*?)```",
    flags=re.IGNORECASE | re.DOTALL,
)
FORBIDDEN_NAMES = {
    "__import__",
    "breakpoint",
    "compile",
    "delattr",
    "dir",
    "eval",
    "exec",
    "getattr",
    "globals",
    "help",
    "input",
    "locals",
    "open",
    "setattr",
    "vars",
}
ALLOWED_THREADING_ATTRIBUTES = {"Condition", "Event", "Lock", "RLock"}
ALLOWED_METHOD_CALLS = {
    "_fetch",
    "_Flight",
    "acquire",
    "append",
    "clear",
    "copy",
    "count",
    "discard",
    "emit",
    "exists",
    "get",
    "is_set",
    "join",
    "notify",
    "notify_all",
    "pop",
    "release",
    "remove",
    "rename",
    "set",
    "setdefault",
    "startswith",
    "strip",
    "update",
    "wait",
}
ALLOWED_NODES = {
    ast.Add,
    ast.And,
    ast.AnnAssign,
    ast.arg,
    ast.arguments,
    ast.Assign,
    ast.Attribute,
    ast.AugAssign,
    ast.BinOp,
    ast.BitAnd,
    ast.BitOr,
    ast.BoolOp,
    ast.Break,
    ast.Call,
    ast.ClassDef,
    ast.Compare,
    ast.Constant,
    ast.Continue,
    ast.Del,
    ast.Delete,
    ast.Dict,
    ast.DictComp,
    ast.Div,
    ast.Eq,
    ast.ExceptHandler,
    ast.Expr,
    ast.FloorDiv,
    ast.For,
    ast.FormattedValue,
    ast.FunctionDef,
    ast.GeneratorExp,
    ast.Gt,
    ast.GtE,
    ast.If,
    ast.IfExp,
    ast.In,
    ast.Is,
    ast.IsNot,
    ast.JoinedStr,
    ast.keyword,
    ast.List,
    ast.ListComp,
    ast.Load,
    ast.Lt,
    ast.LtE,
    ast.Match,
    ast.MatchAs,
    ast.MatchOr,
    ast.MatchValue,
    ast.Mod,
    ast.Module,
    ast.Mult,
    ast.Name,
    ast.NamedExpr,
    ast.Not,
    ast.NotEq,
    ast.NotIn,
    ast.Or,
    ast.Pass,
    ast.Pow,
    ast.Raise,
    ast.Return,
    ast.Set,
    ast.SetComp,
    ast.Slice,
    ast.Starred,
    ast.Store,
    ast.Sub,
    ast.Subscript,
    ast.Try,
    ast.Tuple,
    ast.UnaryOp,
    ast.USub,
    ast.UAdd,
    ast.While,
    ast.With,
    ast.withitem,
    ast.Yield,
    ast.YieldFrom,
}


def _python_blocks(output: str) -> list[str]:
    return [block.strip() for block in PYTHON_BLOCK_RE.findall(output) if block.strip()]


def _definition_source(
    output: str,
    *,
    name: str,
    expected_type: type[ast.FunctionDef] | type[ast.ClassDef],
) -> tuple[str | None, str]:
    for block in _python_blocks(output):
        try:
            tree = ast.parse(block)
        except SyntaxError:
            continue
        for node in tree.body:
            if isinstance(node, expected_type) and node.name == name:
                if node.decorator_list:
                    return None, f"{name} decorators are not permitted."
                selected: list[ast.stmt] = [node]
                if isinstance(node, ast.ClassDef):
                    helpers = [
                        item
                        for item in tree.body
                        if isinstance(item, ast.ClassDef)
                        and item.name.startswith("_")
                        and item is not node
                    ]
                    selected = [*helpers, node]
                sanitized = copy.deepcopy(selected)
                for item in sanitized:
                    for candidate_class in (
                        child
                        for child in ast.walk(item)
                        if isinstance(child, ast.ClassDef)
                    ):
                        if candidate_class.decorator_list:
                            return (
                                None,
                                f"{candidate_class.name} decorators are not permitted.",
                            )
                        for base in candidate_class.bases:
                            generic_base = (
                                isinstance(base, ast.Name) and base.id == "Generic"
                            ) or (
                                isinstance(base, ast.Subscript)
                                and isinstance(base.value, ast.Name)
                                and base.value.id == "Generic"
                            )
                            if not generic_base:
                                return (
                                    None,
                                    f"{candidate_class.name} inheritance is not permitted.",
                                )
                        candidate_class.bases = []
                        candidate_class.keywords = []
                module = ast.Module(body=sanitized, type_ignores=[])
                ast.fix_missing_locations(module)
                failure = _validate_restricted_ast(module)
                if failure is not None:
                    return None, failure
                return (
                    "\n\n".join(ast.unparse(item) for item in sanitized),
                    f"Restricted {name} definition extracted.",
                )
    return None, f"No parseable {name} definition was found."


def _validate_restricted_ast(node: ast.AST) -> str | None:
    parameter_names = {
        argument.arg
        for item in ast.walk(node)
        if isinstance(item, (ast.FunctionDef, ast.Lambda))
        for argument in (
            list(item.args.posonlyargs)
            + list(item.args.args)
            + list(item.args.kwonlyargs)
        )
    }
    local_function_names = {
        item.name for item in ast.walk(node) if isinstance(item, ast.FunctionDef)
    }
    local_class_names = {
        item.name for item in ast.walk(node) if isinstance(item, ast.ClassDef)
    }
    allowed_direct_calls = parameter_names | local_function_names | local_class_names | {
        "BaseException",
        "Exception",
        "KeyError",
        "RuntimeError",
        "TypeError",
        "ValueError",
        "bool",
        "callable",
        "dict",
        "enumerate",
        "int",
        "islice",
        "isinstance",
        "len",
        "list",
        "max",
        "min",
        "range",
        "set",
        "str",
        "tuple",
        "zip",
    }
    for item in ast.walk(node):
        if type(item) not in ALLOWED_NODES:
            return f"Disallowed Python construct: {type(item).__name__}."
        if (
            isinstance(item, ast.FunctionDef)
            and item.name.startswith("__")
            and item.name != "__init__"
        ):
            return f"Disallowed dunder method definition: {item.name}."
        if isinstance(item, ast.ClassDef) and item.name.startswith("__"):
            return f"Disallowed dunder class definition: {item.name}."
        if isinstance(item, ast.Name) and item.id in FORBIDDEN_NAMES:
            return f"Disallowed Python name: {item.id}."
        if isinstance(item, ast.Attribute):
            if item.attr.startswith("__"):
                return "Dunder attribute access is not permitted."
            if isinstance(item.value, ast.Name) and item.value.id == "threading":
                if item.attr not in ALLOWED_THREADING_ATTRIBUTES:
                    return f"Disallowed threading attribute: {item.attr}."
        if isinstance(item, ast.Call):
            if isinstance(item.func, ast.Name):
                if item.func.id not in allowed_direct_calls:
                    return f"Disallowed function call: {item.func.id}."
            elif isinstance(item.func, ast.Attribute):
                threading_constructor = (
                    isinstance(item.func.value, ast.Name)
                    and item.func.value.id == "threading"
                    and item.func.attr in ALLOWED_THREADING_ATTRIBUTES
                )
                if (
                    not threading_constructor
                    and item.func.attr not in ALLOWED_METHOD_CALLS
                ):
                    return f"Disallowed method call: {item.func.attr}."
            else:
                return "Dynamic function calls are not permitted."
        if isinstance(item, ast.Constant):
            if isinstance(item.value, (str, bytes)) and len(item.value) > 4_000:
                return "Oversized constants are not permitted."
            if isinstance(item.value, int) and abs(item.value) > 10_000_000:
                return "Oversized integer constants are not permitted."
    return None


def _run_restricted(
    definition: str,
    harness: str,
    *,
    timeout_seconds: float = 8.0,
) -> tuple[bool, str]:
    script = f"""\
import __future__
import builtins
from collections.abc import Iterable
from itertools import islice
import json
import threading
import time

candidate_source = {definition!r}
safe_builtins = {{
    "__build_class__": builtins.__build_class__,
    "BaseException": BaseException,
    "Exception": Exception,
    "KeyError": KeyError,
    "RuntimeError": RuntimeError,
    "TypeError": TypeError,
    "ValueError": ValueError,
    "bool": bool,
    "callable": callable,
    "dict": dict,
    "enumerate": enumerate,
    "int": int,
    "isinstance": isinstance,
    "len": len,
    "list": list,
    "max": max,
    "min": min,
    "object": object,
    "range": range,
    "set": set,
    "str": str,
    "tuple": tuple,
    "zip": zip,
}}
candidate_globals = {{
    "__builtins__": safe_builtins,
    "__name__": "candidate",
    "threading": threading,
    "Iterable": Iterable,
    "islice": islice,
}}
exec(
    compile(
        candidate_source,
        "<candidate>",
        "exec",
        flags=__future__.annotations.compiler_flag,
        dont_inherit=True,
    ),
    candidate_globals,
    candidate_globals,
)
{harness}
print(json.dumps({{"status": "passed"}}))
"""
    with tempfile.TemporaryDirectory(prefix="ppe-software-check-") as directory:
        root = Path(directory)
        path = root / "verify.py"
        path.write_text(script, encoding="utf-8")
        environment = {
            "PYTHONIOENCODING": "utf-8",
            "PYTHONHASHSEED": "0",
            "PATH": os.environ.get("PATH", ""),
            "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
        }
        try:
            completed = subprocess.run(
                [sys.executable, "-I", "-S", str(path)],
                cwd=root,
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return False, f"Restricted verification timed out after {timeout_seconds:g}s."
    if completed.returncode != 0:
        final_error = completed.stderr.strip().splitlines()
        detail = final_error[-1] if final_error else "unknown subprocess failure"
        return False, f"Restricted verification failed: {detail}"
    try:
        result = json.loads(completed.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        return False, "Restricted verification returned an invalid result."
    return result.get("status") == "passed", "Restricted subprocess tests passed."


PAGINATION_HARNESS = r'''
paginate = candidate_globals["paginate"]

vectors = [
    (([1, 2, 3, 4, 5], 1, 2), [1, 2]),
    (([1, 2, 3, 4, 5], 2, 2), [3, 4]),
    (([1, 2, 3], 3, 2), []),
    ((("a", "b", "c"), 2, 2), ("c",)),
]
for arguments, expected in vectors:
    observed = paginate(*arguments)
    if observed != expected or type(observed) is not type(expected):
        raise AssertionError("one-based slicing or sequence-type contract failed")

for arguments in (
    ([1], 0, 1),
    ([1], 1, 0),
    ([1], True, 1),
    ([1], 1, False),
    ([1], 1.0, 1),
):
    try:
        paginate(*arguments)
    except (TypeError, ValueError):
        pass
    else:
        raise AssertionError("invalid pagination input was accepted")
'''


CONCURRENCY_HARNESS = r'''
SingleFlightCache = candidate_globals["SingleFlightCache"]

call_lock = threading.Lock()
call_count = 0
release_fetch = threading.Event()
fetch_started = threading.Event()

def fetch(key):
    global call_count
    with call_lock:
        call_count += 1
    fetch_started.set()
    if not release_fetch.wait(2):
        raise RuntimeError("fetch release timeout")
    return "value:" + key

cache = SingleFlightCache(fetch)
start = threading.Event()
results = []
errors = []

def worker():
    start.wait()
    try:
        results.append(cache.get("shared"))
    except BaseException as exc:
        errors.append(type(exc).__name__)

threads = [threading.Thread(target=worker) for _ in range(20)]
for thread in threads:
    thread.start()
start.set()
if not fetch_started.wait(2):
    raise AssertionError("upstream fetch never started")
time.sleep(0.05)
release_fetch.set()
for thread in threads:
    thread.join(2)
if any(thread.is_alive() for thread in threads):
    raise AssertionError("coalesced callers did not finish")
if errors or results != ["value:shared"] * 20 or call_count != 1:
    raise AssertionError("same-key requests were not coalesced")

attempts = 0
def flaky(key):
    global attempts
    attempts += 1
    if attempts == 1:
        raise ValueError("temporary")
    return "recovered"

retry_cache = SingleFlightCache(flaky)
try:
    retry_cache.get("retry")
except ValueError:
    pass
else:
    raise AssertionError("fetch failure was not propagated")
if retry_cache.get("retry") != "recovered" or attempts != 2:
    raise AssertionError("failed request was not retryable")

barrier = threading.Barrier(2)
def parallel_fetch(key):
    barrier.wait(timeout=2)
    return key

parallel_cache = SingleFlightCache(parallel_fetch)
parallel_results = []
parallel_errors = []
def parallel_worker(key):
    try:
        parallel_results.append(parallel_cache.get(key))
    except BaseException as exc:
        parallel_errors.append(type(exc).__name__)

parallel_threads = [
    threading.Thread(target=parallel_worker, args=("a",)),
    threading.Thread(target=parallel_worker, args=("b",)),
]
for thread in parallel_threads:
    thread.start()
for thread in parallel_threads:
    thread.join(3)
if parallel_errors or sorted(parallel_results) != ["a", "b"]:
    raise AssertionError("different keys were globally serialized")
'''


ENDPOINT_HARNESS = r'''
handle_request = candidate_globals["handle_request"]
created = []

def authenticate(token):
    return {"id": "user-1"} if token == "good-token" else None

def create_item(user, payload):
    created.append((user, payload.copy()))
    return {"id": 7, "name": payload["name"]}

unauthorized = handle_request(
    {"token": "bad-token", "json": {"name": "A"}},
    authenticate,
    create_item,
)
if unauthorized != {
    "status": 401,
    "body": {"error": {"code": "unauthorized"}},
}:
    raise AssertionError("authentication contract changed")

invalid = handle_request(
    {"token": "good-token", "json": {}},
    authenticate,
    create_item,
)
if invalid != {
    "status": 400,
    "body": {"error": {"code": "invalid_request"}},
}:
    raise AssertionError("validation contract changed")
if created:
    raise AssertionError("invalid requests reached create_item")

success = handle_request(
    {"token": "good-token", "json": {"name": "A"}},
    authenticate,
    create_item,
)
if success != {
    "status": 201,
    "body": {"item": {"id": 7, "name": "A"}},
}:
    raise AssertionError("success response schema changed")
if len(created) != 1:
    raise AssertionError("create_item call count changed")
'''


CLI_HARNESS = r'''
rename_cli = candidate_globals["rename_cli"]

def execute(argv, existing):
    emitted = []
    renamed = []
    def exists(path):
        return path in existing
    def rename(source, destination):
        renamed.append((source, destination))
    def emit(message):
        emitted.append(message)
    code = rename_cli(argv, exists, rename, emit)
    return code, emitted, renamed

code, emitted, renamed = execute(["old.txt", "new.txt"], {"old.txt"})
if code != 0 or renamed != [("old.txt", "new.txt")]:
    raise AssertionError("default rename behavior changed")

code, emitted, renamed = execute(
    ["--dry-run", "old.txt", "new.txt"],
    {"old.txt"},
)
if code != 0 or renamed:
    raise AssertionError("dry-run performed a rename")
if not emitted or "old.txt" not in emitted[-1] or "new.txt" not in emitted[-1]:
    raise AssertionError("dry-run did not report the planned rename")

code, _, renamed = execute(["--dry-run", "missing.txt", "new.txt"], set())
if code != 2 or renamed:
    raise AssertionError("missing-source exit code changed")

code, _, renamed = execute(
    ["--dry-run", "old.txt", "new.txt"],
    {"old.txt", "new.txt"},
)
if code != 3 or renamed:
    raise AssertionError("collision exit code changed")

code, _, renamed = execute(["old.txt"], {"old.txt"})
if code != 2 or renamed:
    raise AssertionError("usage exit code changed")
'''


def verify_concurrency(output: str) -> tuple[bool, str]:
    definition, detail = _definition_source(
        output,
        name="SingleFlightCache",
        expected_type=ast.ClassDef,
    )
    if definition is None:
        return False, detail
    passed, execution_detail = _run_restricted(definition, CONCURRENCY_HARNESS)
    return passed, f"{detail} {execution_detail}"


def verify_pagination(output: str) -> tuple[bool, str]:
    definition, detail = _definition_source(
        output,
        name="paginate",
        expected_type=ast.FunctionDef,
    )
    if definition is None:
        return False, detail
    passed, execution_detail = _run_restricted(definition, PAGINATION_HARNESS)
    return passed, f"{detail} {execution_detail}"


def verify_endpoint(output: str) -> tuple[bool, str]:
    definition, detail = _definition_source(
        output,
        name="handle_request",
        expected_type=ast.FunctionDef,
    )
    if definition is None:
        return False, detail
    passed, execution_detail = _run_restricted(definition, ENDPOINT_HARNESS)
    return passed, f"{detail} {execution_detail}"


def verify_cli(output: str) -> tuple[bool, str]:
    definition, detail = _definition_source(
        output,
        name="rename_cli",
        expected_type=ast.FunctionDef,
    )
    if definition is None:
        return False, detail
    passed, execution_detail = _run_restricted(definition, CLI_HARNESS)
    return passed, f"{detail} {execution_detail}"


def _json_candidates(output: str) -> list[str]:
    candidates = [block.strip() for block in JSON_BLOCK_RE.findall(output)]
    stripped = output.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        candidates.append(stripped)
    return candidates


def verify_migration(output: str) -> tuple[bool, str]:
    payload: dict[str, Any] | None = None
    for candidate in _json_candidates(output):
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            payload = value
            break
    if payload is None:
        return False, "No valid migration-plan JSON object was found."
    phases = payload.get("phases")
    if not isinstance(phases, list):
        return False, "Migration plan must contain a phases array."
    expected_names = ["expand", "bridge", "backfill", "cutover", "contract"]
    observed_names = [
        phase.get("name") if isinstance(phase, dict) else None for phase in phases
    ]
    if observed_names != expected_names:
        return False, f"Migration phase order must be {expected_names}."
    required_boolean_fields = {
        "old_reader_supported",
        "new_reader_supported",
        "old_writer_supported",
        "new_writer_supported",
        "rollback_supported",
    }
    for phase in phases:
        if not isinstance(phase, dict):
            return False, "Every migration phase must be an object."
        if any(not isinstance(phase.get(field), bool) for field in required_boolean_fields):
            return False, f"{phase.get('name')} compatibility fields must be booleans."
        actions = phase.get("actions")
        if (
            not isinstance(actions, list)
            or not actions
            or any(not isinstance(action, str) or not action.strip() for action in actions)
        ):
            return False, f"{phase.get('name')} must contain non-empty actions."
    bridge = phases[1]
    if bridge.get("synchronizes_old_writer_inserts") is not True:
        return False, "Bridge phase must synchronize old-writer inserts."
    if bridge.get("synchronizes_old_writer_updates") is not True:
        return False, "Bridge phase must synchronize old-writer updates."
    if any(
        phase.get("drops_legacy_field") is True
        or phase.get("enforces_new_not_null") is True
        for phase in phases[:-1]
    ):
        return False, "Destructive constraints are permitted only in contract."
    contract = phases[-1]
    if contract.get("old_reader_supported") or contract.get("old_writer_supported"):
        return False, "Contract phase must retire old readers and writers."
    if contract.get("rollback_supported"):
        return False, "Contract phase cannot claim old-version rollback support."
    if contract.get("drops_legacy_field") is not True:
        return False, "Contract phase must explicitly drop the legacy field."
    return True, "Migration compatibility matrix and destructive sequencing passed."
