"""Versioned contracts for optimization requests and artifacts."""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version
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
try:
    PACKAGE_VERSION = version("prompt-performance-engine")
except PackageNotFoundError:
    PACKAGE_VERSION = (PACKAGE_ROOT / "VERSION").read_text(
        encoding="utf-8"
    ).strip()
ARTIFACT_SCHEMA_VERSION = "1.0.0"

MODES = {"balanced", "maximum_quality", "concise"}
OUTPUT_FORMATS = {"prompt_only", "standard", "evaluation_package"}
TARGET_SURFACES = {"chat", "api", "agent", "coding_agent", "image_model", "other"}


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

    def validate(self) -> None:
        if self.schema_version != ARTIFACT_SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported request schema {self.schema_version!r}; "
                f"expected {ARTIFACT_SCHEMA_VERSION!r}."
            )
        if not isinstance(self.source_prompt, str):
            raise TypeError("source_prompt must be a string.")
        if not self.source_prompt.strip():
            raise ValueError("source_prompt must not be empty.")
        if self.mode not in MODES:
            raise ValueError(f"Unsupported mode: {self.mode!r}.")
        if self.output_format not in OUTPUT_FORMATS:
            raise ValueError(f"Unsupported output_format: {self.output_format!r}.")
        if self.target_surface not in TARGET_SURFACES:
            raise ValueError(f"Unsupported target_surface: {self.target_surface!r}.")

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
        }
