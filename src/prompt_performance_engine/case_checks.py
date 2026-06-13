"""Case-specific deterministic checks for benchmark outputs."""

from __future__ import annotations

import ast
import re
from typing import Any, Callable


CaseCheckPlugin = Callable[[str], list[dict[str, Any]]]
PYTHON_BLOCK_RE = re.compile(
    r"```(?:python|py)\s*\r?\n(.*?)```",
    flags=re.IGNORECASE | re.DOTALL,
)


def _check(check: str, passed: bool, detail: str) -> dict[str, Any]:
    return {
        "check": check,
        "passed": passed,
        "detail": detail,
        "authoritative": True,
        "source": "case_plugin",
    }


def _python_blocks(output: str) -> list[str]:
    return [block.strip() for block in PYTHON_BLOCK_RE.findall(output) if block.strip()]


ALLOWED_PAGINATION_NODES = {
    ast.Module,
    ast.FunctionDef,
    ast.arguments,
    ast.arg,
    ast.Return,
    ast.If,
    ast.Try,
    ast.ExceptHandler,
    ast.Raise,
    ast.Expr,
    ast.Assign,
    ast.AnnAssign,
    ast.Name,
    ast.Load,
    ast.Store,
    ast.Constant,
    ast.Call,
    ast.Subscript,
    ast.Slice,
    ast.BinOp,
    ast.BoolOp,
    ast.UnaryOp,
    ast.Compare,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.And,
    ast.Or,
    ast.Not,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.Eq,
    ast.NotEq,
    ast.Is,
    ast.IsNot,
    ast.In,
    ast.NotIn,
    ast.Tuple,
    ast.List,
}
ALLOWED_PAGINATION_CALLS = {"isinstance", "TypeError", "ValueError"}
SAFE_PAGINATION_GLOBALS = {
    "__builtins__": {},
    "bool": bool,
    "int": int,
    "isinstance": isinstance,
    "TypeError": TypeError,
    "ValueError": ValueError,
}


def _safe_paginate_function(output: str) -> tuple[Callable[..., Any] | None, str]:
    blocks = _python_blocks(output)
    if not blocks:
        return None, "No fenced Python implementation was found."
    for block in blocks:
        try:
            tree = ast.parse(block)
        except SyntaxError:
            continue
        functions = [
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "paginate"
        ]
        if not functions:
            continue
        function = functions[0]
        if (
            len(function.args.args) != 3
            or function.args.vararg is not None
            or function.args.kwarg is not None
        ):
            return None, "paginate must accept exactly items, page, and page_size."
        for node in ast.walk(function):
            if type(node) not in ALLOWED_PAGINATION_NODES:
                return None, f"Disallowed Python construct: {type(node).__name__}."
            if isinstance(node, ast.Call):
                if not isinstance(node.func, ast.Name):
                    return None, "Attribute and dynamic calls are not permitted."
                if node.func.id not in ALLOWED_PAGINATION_CALLS:
                    return None, f"Disallowed function call: {node.func.id}."
            if isinstance(node, ast.Name) and node.id == "paginate" and isinstance(
                node.ctx, ast.Load
            ):
                return None, "Recursive paginate calls are not permitted."
            if isinstance(node, ast.Constant):
                if isinstance(node.value, (str, bytes)) and len(node.value) > 500:
                    return None, "Oversized constants are not permitted."
                if isinstance(node.value, int) and abs(node.value) > 1_000_000:
                    return None, "Oversized integer constants are not permitted."
        isolated = ast.Module(body=[function], type_ignores=[])
        ast.fix_missing_locations(isolated)
        namespace = dict(SAFE_PAGINATION_GLOBALS)
        exec(compile(isolated, "<paginate-check>", "exec"), namespace, namespace)
        candidate = namespace.get("paginate")
        if callable(candidate):
            return candidate, "A restricted paginate function was extracted."
    return None, "No parseable paginate function was found."


def _pagination(output: str) -> list[dict[str, Any]]:
    paginate, extraction_detail = _safe_paginate_function(output)
    if paginate is None:
        return [
            _check(
                "pagination_restricted_extraction",
                False,
                extraction_detail,
            )
        ]
    checks = [
        _check(
            "pagination_restricted_extraction",
            True,
            extraction_detail,
        )
    ]
    vectors = (
        (([1, 2, 3, 4, 5], 1, 2), [1, 2]),
        (([1, 2, 3, 4, 5], 2, 2), [3, 4]),
        (([1, 2, 3], 3, 2), []),
        ((("a", "b", "c"), 2, 2), ("c",)),
    )
    try:
        behavior_passed = all(paginate(*arguments) == expected for arguments, expected in vectors)
    except Exception as exc:
        behavior_passed = False
        behavior_detail = f"Behavior vectors raised {type(exc).__name__}."
    else:
        behavior_detail = "One-based slicing and out-of-range behavior passed."
    checks.append(
        _check(
            "pagination_behavior_vectors",
            behavior_passed,
            behavior_detail,
        )
    )

    validation_passed = True
    for arguments in (([1], 0, 1), ([1], 1, 0), ([1], True, 1)):
        try:
            paginate(*arguments)
        except (TypeError, ValueError):
            continue
        except Exception:
            validation_passed = False
            break
        validation_passed = False
        break
    checks.append(
        _check(
            "pagination_validation_errors",
            validation_passed,
            "Invalid page inputs must raise TypeError or ValueError.",
        )
    )
    return checks


CASE_CHECKS: dict[str, CaseCheckPlugin] = {
    "se-normal-pagination": _pagination,
}


def run_case_checks(case_id: str, output: str) -> list[dict[str, Any]]:
    plugin = CASE_CHECKS.get(case_id)
    return plugin(output) if plugin is not None else []
