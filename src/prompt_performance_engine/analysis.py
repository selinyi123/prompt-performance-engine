"""Conservative deterministic recovery of visible Prompt requirements."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


VARIABLE_RE = re.compile(r"\{\{\s*([A-Za-z][A-Za-z0-9_]*)\s*\}\}")
REQUIRED_RE = re.compile(
    r"(?i)\b(must|required?|ensure|need to|shall)\b|必须|需要|务必|应当"
)
FORBIDDEN_RE = re.compile(
    r"(?i)\b(do not|don't|must not|never|forbid|prohibit|without)\b|不得|禁止|不要|不可"
)


DELIVERABLE_PATTERNS = (
    (
        "design_or_plan",
        re.compile(
            r"(?i)\b(design|architecture|plan|strategy|roadmap|migration)\b"
            r"|设计|方案|规划|架构"
        ),
    ),
    (
        "implementation",
        re.compile(
            r"(?i)\b(implement|build|create|write code|add|repair|fix|program)\b"
            r"|实现|编写代码|修复|开发"
        ),
    ),
    (
        "image_prompt",
        re.compile(
            r"(?i)\b(image prompt|generate an image|text-to-image|photo prompt)\b"
            r"|图片提示词|生成图片|图像提示词"
        ),
    ),
    (
        "creative_concept",
        re.compile(
            r"(?i)\b(concept|campaign|art direction|moodboard|logo)\b"
            r"|创意概念|广告创意|视觉方向|标志"
        ),
    ),
    (
        "translation",
        re.compile(
            r"(?i)\b(translate|translation|localize|localization)\b|翻译|本地化"
        ),
    ),
    (
        "analysis",
        re.compile(
            r"(?i)\b(analyze|analysis|research|evaluate|compare|report)\b"
            r"|分析|研究|评估|报告"
        ),
    ),
)


@dataclass(frozen=True)
class BehavioralContract:
    objective_hint: str
    deliverable_kind: str
    required_constraints: tuple[str, ...]
    forbidden_behaviors: tuple[str, ...]
    variables: tuple[str, ...]
    ambiguities: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "objective_hint": self.objective_hint,
            "deliverable_kind": self.deliverable_kind,
            "required_constraints": list(self.required_constraints),
            "forbidden_behaviors": list(self.forbidden_behaviors),
            "variables": list(self.variables),
            "ambiguities": list(self.ambiguities),
        }


def _clean_lines(source_prompt: str) -> list[str]:
    return [
        " ".join(line.strip().split())
        for line in source_prompt.splitlines()
        if line.strip()
    ]


def recover_behavioral_contract(source_prompt: str) -> BehavioralContract:
    lines = _clean_lines(source_prompt)
    objective = lines[0][:300] if lines else ""
    deliverable_kind = next(
        (
            kind
            for kind, pattern in DELIVERABLE_PATTERNS
            if pattern.search(source_prompt)
        ),
        "general_response",
    )
    required = tuple(dict.fromkeys(line for line in lines if REQUIRED_RE.search(line)))
    forbidden = tuple(dict.fromkeys(line for line in lines if FORBIDDEN_RE.search(line)))
    variables = tuple(sorted(set(VARIABLE_RE.findall(source_prompt))))
    ambiguities: list[str] = []
    if len(lines) == 1 and len(source_prompt.split()) < 12:
        ambiguities.append("Source Prompt is sparse; audience, context, or acceptance criteria may be missing.")
    if not variables and "{{" in source_prompt:
        ambiguities.append("Template-like syntax is present but no valid {{VARIABLE}} names were found.")
    return BehavioralContract(
        objective_hint=objective,
        deliverable_kind=deliverable_kind,
        required_constraints=required[:20],
        forbidden_behaviors=forbidden[:20],
        variables=variables,
        ambiguities=tuple(ambiguities),
    )
