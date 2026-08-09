"""Versioned contracts for optimization requests and artifacts."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, DecimalException
from importlib.metadata import PackageNotFoundError, version
import json
import math
from pathlib import Path
import sysconfig
from typing import Any


MODULE_ROOT = Path(__file__).resolve().parent
SOURCE_ROOT = MODULE_ROOT.parents[1]
INSTALLED_DATA_ROOTS = (
    MODULE_ROOT.parent / "prompt_performance_engine_data",
    Path(sysconfig.get_path("data")) / "prompt_performance_engine_data",
)
PACKAGE_ROOT = (
    SOURCE_ROOT
    if (SOURCE_ROOT / "VERSION").is_file()
    else next(
        (
            candidate
            for candidate in INSTALLED_DATA_ROOTS
            if (candidate / "VERSION").is_file()
        ),
        INSTALLED_DATA_ROOTS[0],
    )
)
if (SOURCE_ROOT / "VERSION").is_file():
    PACKAGE_VERSION = (SOURCE_ROOT / "VERSION").read_text(encoding="utf-8").strip()
else:
    try:
        PACKAGE_VERSION = version("prompt-performance-engine")
    except PackageNotFoundError:
        PACKAGE_VERSION = (PACKAGE_ROOT / "VERSION").read_text(
            encoding="utf-8"
        ).strip()
ARTIFACT_SCHEMA_VERSION = "2.0.0"
LEGACY_ARTIFACT_SCHEMA_VERSION = "1.0.0"
# Producer compatibility is an explicit contract, not an alias for the version
# of the currently installed package.  In particular, 0.3.0/schema-1.0.0
# artifacts remain readable after the 0.4.0/schema-2.0.0 package release.
SUPPORTED_ARTIFACT_PRODUCER_VERSIONS = frozenset({"0.3.0", "0.4.0"})
LEGACY_ARTIFACT_PRODUCER_VERSIONS = frozenset({"0.3.0"})
MAX_JSON_DECIMAL_EXPONENT = 10_000

MODES = {"balanced", "maximum_quality", "concise"}
OUTPUT_FORMATS = {"prompt_only", "standard", "evaluation_package"}
TARGET_SURFACES = {"chat", "api", "agent", "coding_agent", "image_model", "other"}
CANDIDATE_STRATEGIES: tuple[tuple[str, str], ...] = (
    (
        "fidelity_guardrail",
        "Prioritize exact intent, constraint, evidence-scope, and output-contract fidelity.",
    ),
    (
        "coverage_matrix",
        "Systematically cover every actor, deliverable component, objection, and check.",
    ),
    (
        "concise_channel_fit",
        "Minimize ceremony and repetition while maximizing target-surface usability.",
    ),
    (
        "adversarial_red_team",
        "Eliminate likely safety, unsupported-claim, ambiguity, and regression failures.",
    ),
    (
        "balanced_synthesis",
        "Balance fidelity, completeness, usability, safeguards, and token efficiency.",
    ),
)
OPTIMIZATION_REQUEST_FIELDS = frozenset(
    {
        "schema_version",
        "source_prompt",
        "mode",
        "output_format",
        "domain",
        "audience",
        "target_model",
        "target_surface",
        "required_behaviors",
        "forbidden_changes",
        "candidate_count",
    }
)
OPTIMIZATION_REQUEST_REQUIRED_FIELDS = frozenset(
    {"schema_version", "source_prompt", "mode", "output_format"}
)


def candidate_strategy_plan(count: int) -> tuple[tuple[str, str], ...]:
    """Return the only valid strategy plan for a candidate count."""

    if count == 1:
        return (("default", "Use the standard balanced optimization contract."),)
    if not 2 <= count <= len(CANDIDATE_STRATEGIES):
        raise ValueError("candidate strategy count must be between 1 and 5.")
    return CANDIDATE_STRATEGIES[:count]


def _parse_strict_json_object(
    text: str,
    *,
    label: str,
    preserve_decimal: bool,
) -> dict[str, Any]:
    """Decode one JSON object with bounded, duplicate-free JSON semantics."""

    def object_from_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for name, value in pairs:
            if name in result:
                raise ValueError(f"{label} contains duplicate field {name!r}.")
            result[name] = value
        return result

    def reject_constant(value: str) -> None:
        raise ValueError(f"{label} contains non-JSON numeric constant {value!r}.")

    def bounded_decimal(value: str) -> Decimal:
        number = Decimal(value)
        exponent = number.as_tuple().exponent
        if (
            not isinstance(exponent, int)
            or abs(exponent) > MAX_JSON_DECIMAL_EXPONENT
        ):
            raise ValueError(
                f"{label} contains a numeric exponent outside the supported range."
            )
        return number

    def bounded_float(value: str) -> float:
        exact_number = bounded_decimal(value)
        number = float(value)
        if not math.isfinite(number):
            raise ValueError(f"{label} contains a number outside the supported range.")
        if Decimal(str(number)) != exact_number:
            raise ValueError(
                f"{label} contains a number that cannot be represented "
                "without precision loss."
            )
        return number

    try:
        decoded = json.loads(
            text,
            object_pairs_hook=object_from_pairs,
            parse_float=bounded_decimal if preserve_decimal else bounded_float,
            parse_constant=reject_constant,
        )
    except (json.JSONDecodeError, DecimalException, RecursionError) as exc:
        raise ValueError(f"{label} must be valid JSON.") from exc
    if not isinstance(decoded, dict):
        raise ValueError(f"{label} root must be an object.")

    def require_utf8(value: Any) -> None:
        if isinstance(value, str):
            try:
                value.encode("utf-8", errors="strict")
            except UnicodeEncodeError as exc:
                raise ValueError(
                    f"{label} contains text that is not valid UTF-8."
                ) from exc
            return
        if isinstance(value, dict):
            for name, item in value.items():
                require_utf8(name)
                require_utf8(item)
            return
        if isinstance(value, list):
            for item in value:
                require_utf8(item)

    try:
        require_utf8(decoded)
    except RecursionError as exc:
        raise ValueError(f"{label} exceeds the supported nesting depth.") from exc
    return decoded


def parse_strict_json_object(text: str, *, label: str) -> dict[str, Any]:
    """Decode one JSON object while preserving finite decimal precision."""

    return _parse_strict_json_object(
        text,
        label=label,
        preserve_decimal=True,
    )


def load_strict_json_object(
    path: Path,
    *,
    label: str,
    preserve_decimal: bool = False,
) -> dict[str, Any]:
    """Load a duplicate-free JSON object with explicit number representation.

    Authority-source hashing historically consumes Python ``float`` values.
    The default keeps that representation so strict authority loading does not
    change canonical hash payloads to non-serializable ``Decimal`` instances,
    while rejecting decimal literals whose value would change during conversion.
    Callers that must apply exact JSON Schema integer semantics may opt in to
    decimal preservation and normalize validated values before hashing.
    """

    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"Cannot load {label} at {path}: {exc}") from exc
    try:
        return _parse_strict_json_object(
            text,
            label=label,
            preserve_decimal=preserve_decimal,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Cannot load {label} at {path}: {exc}") from exc


def _validate_optional_string(name: str, value: object) -> None:
    if value is not None and not isinstance(value, str):
        raise TypeError(f"{name} must be a string or null.")


def _validate_string_tuple(name: str, value: object) -> None:
    if not isinstance(value, tuple):
        raise TypeError(f"{name} must be a tuple of strings.")
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise TypeError(f"{name}[{index}] must be a string.")


@dataclass(frozen=True)
class OptimizationRequest:
    source_prompt: str
    mode: str = "maximum_quality"
    output_format: str = "standard"
    domain: str | None = None
    audience: str | None = None
    target_model: str | None = None
    target_surface: str = "chat"
    required_behaviors: tuple[str, ...] = field(default_factory=tuple)
    forbidden_changes: tuple[str, ...] = field(default_factory=tuple)
    schema_version: str = ARTIFACT_SCHEMA_VERSION
    candidate_count: int = 1

    def validate(self) -> None:
        if not isinstance(self.schema_version, str):
            raise TypeError("schema_version must be a string.")
        if self.schema_version != ARTIFACT_SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported request schema {self.schema_version!r}; "
                f"expected {ARTIFACT_SCHEMA_VERSION!r}."
            )
        if not isinstance(self.source_prompt, str):
            raise TypeError("source_prompt must be a string.")
        if not self.source_prompt.strip():
            raise ValueError("source_prompt must not be empty.")
        if not isinstance(self.mode, str):
            raise TypeError("mode must be a string.")
        if self.mode not in MODES:
            raise ValueError(f"Unsupported mode: {self.mode!r}.")
        if not isinstance(self.output_format, str):
            raise TypeError("output_format must be a string.")
        if self.output_format not in OUTPUT_FORMATS:
            raise ValueError(f"Unsupported output_format: {self.output_format!r}.")
        _validate_optional_string("domain", self.domain)
        _validate_optional_string("audience", self.audience)
        _validate_optional_string("target_model", self.target_model)
        if not isinstance(self.target_surface, str):
            raise TypeError("target_surface must be a string.")
        if self.target_surface not in TARGET_SURFACES:
            raise ValueError(f"Unsupported target_surface: {self.target_surface!r}.")
        _validate_string_tuple("required_behaviors", self.required_behaviors)
        _validate_string_tuple("forbidden_changes", self.forbidden_changes)
        if not isinstance(self.candidate_count, int) or isinstance(
            self.candidate_count, bool
        ):
            raise TypeError("candidate_count must be an integer.")
        if not 1 <= self.candidate_count <= 5:
            raise ValueError("candidate_count must be between 1 and 5.")

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
        *,
        target_surface_default: str = "chat",
    ) -> OptimizationRequest:
        """Decode the request using JSON Schema numeric semantics without field loss."""
        if not isinstance(data, dict):
            raise TypeError("optimization request root must be an object.")
        unknown = set(data).difference(OPTIMIZATION_REQUEST_FIELDS)
        if unknown:
            names = ", ".join(sorted(repr(name) for name in unknown))
            raise ValueError(f"Unknown optimization request field(s): {names}.")
        missing = OPTIMIZATION_REQUEST_REQUIRED_FIELDS.difference(data)
        if missing:
            names = ", ".join(sorted(missing))
            raise ValueError(f"Missing required optimization request field(s): {names}.")

        arrays: dict[str, tuple[str, ...]] = {}
        for name in ("required_behaviors", "forbidden_changes"):
            raw = data.get(name, [])
            if not isinstance(raw, list):
                raise TypeError(f"{name} must be an array of strings.")
            for index, item in enumerate(raw):
                if not isinstance(item, str):
                    raise TypeError(f"{name}[{index}] must be a string.")
            arrays[name] = tuple(raw)

        candidate_count = data.get("candidate_count", 1)
        if isinstance(candidate_count, (float, Decimal)):
            finite = (
                candidate_count.is_finite()
                if isinstance(candidate_count, Decimal)
                else candidate_count == candidate_count
                and candidate_count not in (float("inf"), float("-inf"))
            )
            if (
                finite
                and 1 <= candidate_count <= 5
                and candidate_count == int(candidate_count)
            ):
                candidate_count = int(candidate_count)
        request = cls(
            source_prompt=data["source_prompt"],
            mode=data["mode"],
            output_format=data["output_format"],
            domain=data.get("domain"),
            audience=data.get("audience"),
            target_model=data.get("target_model"),
            target_surface=data.get("target_surface", target_surface_default),
            required_behaviors=arrays["required_behaviors"],
            forbidden_changes=arrays["forbidden_changes"],
            candidate_count=candidate_count,
            schema_version=data["schema_version"],
        )
        request.validate()
        return request

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "schema_version": self.schema_version,
            "source_prompt": self.source_prompt,
            "mode": self.mode,
            "output_format": self.output_format,
            "domain": self.domain,
            "audience": self.audience,
            "target_model": self.target_model,
            "target_surface": self.target_surface,
            "required_behaviors": list(self.required_behaviors),
            "forbidden_changes": list(self.forbidden_changes),
            "candidate_count": self.candidate_count,
        }
