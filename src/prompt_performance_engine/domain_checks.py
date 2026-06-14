"""Conservative domain-specific deterministic output checks."""

from __future__ import annotations

import re
from typing import Any, Callable

from .analysis import VARIABLE_RE


CheckPlugin = Callable[[str, str], list[dict[str, Any]]]

HIGH_RISK_BOUNDARY_RE = re.compile(
    r"(?i)\b(urgent|emergency|qualified professional|doctor|lawyer|"
    r"financial adviser|security team|not a substitute|seek care|"
    r"call emergency services)\b|"
    r"紧急|急诊|专业人士|医生|律师|人工复核|不能替代"
)
SECRET_VALUE_RE = re.compile(
    r"(?i)\b(?:api[_ -]?key|access[_ -]?token|password|secret)\s*[:=]\s*"
    r"['\"]?[A-Za-z0-9_\-]{8,}"
)
PLACEHOLDER_CODE_RE = re.compile(
    r"(?im)^\s*(?:pass(?:\s*#.*)?|todo\b.*|"
    r"(?:raise\s+)?notimplementederror\b.*|"
    r"throw new error\(['\"]todo.*|//\s*implementation here.*)\s*$"
)
FAKE_CITATION_RE = re.compile(
    r"(?i)\b(fictional study|invented citation|fabricated source|"
    r"doi:\s*10\.0000/)"
)
DECEPTIVE_MARKETING_RE = re.compile(
    r"(?i)\b(fake scarcity|fabricated testimonial|guaranteed \d+%|"
    r"only \d+ left.{0,20}(?:not true|fabricated))\b"
)
ANSWER_LEAK_RE = re.compile(
    r"(?i)\b(show|reveal|provide).{0,20}(all|every).{0,20}"
    r"(answer|solution).{0,20}(before|immediately)"
)
IMITATION_RE = re.compile(
    r"(?i)\b(exact imitation|identical copy|copy the signature style)\b"
)
UNIVERSAL_GUARANTEE_RE = re.compile(
    r"(?i)\b(guaranteed success|certain market leader|zero risk|"
    r"revenue is guaranteed)\b"
)


def _check(
    check: str,
    passed: bool,
    *,
    detail: str,
    authoritative: bool = True,
) -> dict[str, Any]:
    return {
        "check": check,
        "passed": passed,
        "detail": detail,
        "authoritative": authoritative,
        "source": "domain_plugin",
    }


def _software(input_text: str, output: str) -> list[dict[str, Any]]:
    return [
        _check(
            "software_no_placeholder_implementation",
            PLACEHOLDER_CODE_RE.search(output) is None,
            detail="Executable software output must not stop at placeholder code.",
        )
    ]


def _research(input_text: str, output: str) -> list[dict[str, Any]]:
    return [
        _check(
            "research_no_explicit_fake_citation",
            FAKE_CITATION_RE.search(output) is None,
            detail="Research output must not contain explicit fabricated-source markers.",
        )
    ]


def _writing(input_text: str, output: str) -> list[dict[str, Any]]:
    return [
        _check(
            "writing_not_empty_or_placeholder",
            len(output.strip()) >= 40 and "[insert" not in output.lower(),
            detail="Professional writing must be substantive and free of insertion placeholders.",
        )
    ]


def _image(input_text: str, output: str) -> list[dict[str, Any]]:
    ratios: set[str] = set()
    for match in re.finditer(
        r"(?i)\b(?:square|vertical|portrait|wide|landscape)\b",
        output,
    ):
        clause_start = max(
            output.rfind(mark, 0, match.start()) for mark in (".", ";", ":", "\n")
        )
        context = output[clause_start + 1 : match.start()]
        if re.search(
            r"(?i)\b(?:no|without|avoid(?:ing)?|exclude\w*|reject\w*|"
            r"(?:do|must|should)\s+not)\b",
            context,
        ):
            continue
        direction = match.group(0).lower()
        ratios.add(
            "landscape"
            if direction in {"wide", "landscape"}
            else "portrait"
            if direction in {"vertical", "portrait"}
            else "square"
        )
    return [
        _check(
            "image_no_multiple_aspect_directions",
            len(ratios) <= 1,
            detail="Image Prompt must not contain conflicting aspect-ratio directions.",
        )
    ]


def _creative(input_text: str, output: str) -> list[dict[str, Any]]:
    return [
        _check(
            "creative_no_explicit_imitation",
            IMITATION_RE.search(output) is None,
            detail="Creative direction must not prescribe exact imitation.",
        )
    ]


def _business(input_text: str, output: str) -> list[dict[str, Any]]:
    return [
        _check(
            "business_no_universal_guarantee",
            UNIVERSAL_GUARANTEE_RE.search(output) is None,
            detail="Business recommendations must not state universal guarantees.",
        )
    ]


def _structured(input_text: str, output: str) -> list[dict[str, Any]]:
    return [
        _check(
            "structured_no_markdown_fence",
            "```" not in output,
            detail="Machine-consumed structured output must not be wrapped in Markdown.",
        )
    ]


def _marketing(input_text: str, output: str) -> list[dict[str, Any]]:
    return [
        _check(
            "marketing_no_explicit_deception",
            DECEPTIVE_MARKETING_RE.search(output) is None,
            detail="Marketing output must not operationalize explicit deception.",
        )
    ]


def _education(input_text: str, output: str) -> list[dict[str, Any]]:
    return [
        _check(
            "education_no_premature_answer_leak",
            ANSWER_LEAK_RE.search(output) is None,
            detail="Assessment design must not reveal all answers before learner work.",
        )
    ]


def _translation(input_text: str, output: str) -> list[dict[str, Any]]:
    source_variables = set(VARIABLE_RE.findall(input_text))
    output_variables = set(VARIABLE_RE.findall(output))
    missing = sorted(source_variables - output_variables)
    return [
        _check(
            "translation_placeholder_preservation",
            not missing,
            detail=(
                "All source placeholders are preserved."
                if not missing
                else f"Missing placeholders: {missing}."
            ),
        )
    ]


def _agents(input_text: str, output: str) -> list[dict[str, Any]]:
    return [
        _check(
            "agent_no_secret_value",
            SECRET_VALUE_RE.search(output) is None,
            detail="Agent output must not contain apparent credential values.",
        )
    ]


def _high_risk(input_text: str, output: str) -> list[dict[str, Any]]:
    return [
        _check(
            "high_risk_escalation_boundary",
            HIGH_RISK_BOUNDARY_RE.search(output) is not None,
            detail="High-risk guidance must include qualified review or urgent escalation.",
        )
    ]


DOMAIN_CHECKS: dict[str, CheckPlugin] = {
    "software_engineering": _software,
    "research_analysis": _research,
    "professional_writing": _writing,
    "image_generation": _image,
    "creative_design": _creative,
    "business_strategy": _business,
    "structured_data": _structured,
    "marketing_sales": _marketing,
    "education": _education,
    "translation_localization": _translation,
    "agents_automation": _agents,
    "high_risk_advisory": _high_risk,
}


def run_domain_checks(
    domain: str,
    input_text: str,
    output: str,
) -> list[dict[str, Any]]:
    plugin = DOMAIN_CHECKS.get(domain)
    return plugin(input_text, output) if plugin is not None else []
