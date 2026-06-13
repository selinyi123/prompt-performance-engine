"""Deterministic static audit for source and optimized Prompts."""

from __future__ import annotations

import base64
import hashlib
import re
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any, Iterable

from .analysis import VARIABLE_RE


@dataclass(frozen=True)
class AuditFinding:
    rule_id: str
    severity: str
    category: str
    message: str
    evidence_span: str
    remediation: str

    @property
    def blocking(self) -> bool:
        return self.severity in {"high", "critical"}


@dataclass(frozen=True)
class AuditReport:
    schema_version: str
    text_sha256: str
    source_sha256: str | None
    passed: bool
    checks: tuple[str, ...]
    findings: tuple[AuditFinding, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "text_sha256": self.text_sha256,
            "source_sha256": self.source_sha256,
            "passed": self.passed,
            "checks": list(self.checks),
            "findings": [
                {**asdict(finding), "blocking": finding.blocking}
                for finding in self.findings
            ],
        }


HOOK_PATTERNS: tuple[tuple[str, str, str, re.Pattern[str]], ...] = (
    (
        "H01_authority_override",
        "high",
        "authority_override",
        re.compile(
            r"(?is)\b(system|developer|root|admin)\s*"
            r"(prompt|message|instruction|override)|"
            r"you are now|act as the real auditor"
        ),
    ),
    (
        "H02_rule_suppression",
        "high",
        "rule_suppression",
        re.compile(
            r"(?is)ignore\s+(?:all\s+)?(?:any\s+)?"
            r"(?:previous|above|current|the)?\s*.*?"
            r"(instructions|rules|policy|audit|wrapper)|"
            r"disregard .*?(instructions|rules|policy|audit|wrapper)|"
            r"bypass .*?(rules|policy|audit|wrapper)"
        ),
    ),
    (
        "H03_forced_score_or_grade",
        "medium",
        "judge_manipulation",
        re.compile(
            r"(?is)must (rate|score|grade)|"
            r"give (it|this).*?(100|10/10|G5|V5|SSS|A\+)|"
            r"always output .*?(G5|V5|100)"
        ),
    ),
    (
        "H04_output_schema_hijack",
        "high",
        "schema_hijack",
        re.compile(
            r"(?is)output only (markdown|text|yaml)|"
            r"do not output json|omit .*?(field|schema)|ignore .*?schema"
        ),
    ),
    (
        "H05_fake_evidence_or_validator_claim",
        "high",
        "fake_evidence",
        re.compile(
            r"(?is)(validator says|trusted_log|runtime_harness|"
            r"orchestration_layer).*?(passed|accepted|verified)|"
            r"evidence level\s*[:=]\s*E[1-5]|"
            r"(?:has been|already)\s+(?:expert reviewed|runtime tested|"
            r"cross[- ]model tested)"
        ),
    ),
    (
        "H06_history_contamination",
        "medium",
        "history_contamination",
        re.compile(
            r"(?is)(previous|prior|last|historical) "
            r"(score|audit|rating)|compare(d)? with (last|previous)|"
            r"baseline.*?delta"
        ),
    ),
    (
        "H07_context_exfiltration_request",
        "high",
        "context_exfiltration",
        re.compile(
            r"(?is)(reveal|show|print|dump).*?"
            r"(system prompt|developer message|hidden instruction|"
            r"chain of thought|memory)"
        ),
    ),
    (
        "H09_delimiter_escape",
        "high",
        "delimiter_escape",
        re.compile(
            r"(?is)</input_prompt>|<<<\s*INPUT_PROMPT.*?END\s*>>>|"
            r"```\s*(system|developer|user)?\s*$"
        ),
    ),
    (
        "H10_tool_or_runtime_impersonation",
        "high",
        "runtime_impersonation",
        re.compile(
            r"(?is)(tool_result|runtime_harness|orchestration_layer|"
            r"validator_log|trusted_log).*?(accepted|verified|passed)"
        ),
    ),
    (
        "H11_reward_or_threat_framing",
        "medium",
        "coercion",
        re.compile(
            r"(?is)(i will pay|reward|tip|bonus|punish|"
            r"you will be fired|\bharm\b|\bthreat\b)"
        ),
    ),
    (
        "H12_self_certification_claim",
        "high",
        "overclaim",
        re.compile(
            r"(?is)(already verified|production certified|universal best|"
            r"impossible to improve|award[- ]level|SSS[- ]?grade)"
        ),
    ),
)

ZERO_WIDTH_RE = re.compile("[\u200b\u200c\u200d\ufeff]")
BASE64_CANDIDATE_RE = re.compile(
    r"(?<![A-Za-z0-9+/=])(?:[A-Za-z0-9+/]{16,}={0,2})(?![A-Za-z0-9+/=])"
)
INJECTION_TERMS_RE = re.compile(
    r"(?is)ignore|bypass|system|developer|instructions|score|grade|validator|trusted"
)
ONLY_FORMAT_RE = re.compile(
    r"(?i)\b(?:output|return|respond with)\s+only\s+"
    r"(json|markdown|yaml|xml|csv|plain text|text)\b"
)
HIGH_RISK_RE = re.compile(
    r"(?i)\b(medical|diagnos(?:e|is)|legal advice|financial advice|"
    r"investment|prescri(?:be|ption)|security exploit)\b|"
    r"医疗|诊断|法律建议|投资建议|处方"
)
HIGH_RISK_BOUNDARY_RE = re.compile(
    r"(?i)\b(not a substitute|consult (?:a|an) qualified|"
    r"professional review|required human review|do not rely solely)\b|"
    r"不能替代|咨询.{0,8}(专业|医生|律师)|人工复核"
)

CHECKS = (
    "security_hooks",
    "hidden_encoding",
    "template_variable_preservation",
    "output_contract_conflicts",
    "unsupported_claims",
    "high_risk_boundaries",
    "size_and_repetition",
)


def _clip(text: str, max_len: int = 180) -> str:
    normalized = " ".join(text.split())
    return normalized if len(normalized) <= max_len else normalized[: max_len - 3] + "..."


def _finding(
    rule_id: str,
    severity: str,
    category: str,
    message: str,
    evidence: str,
    remediation: str,
) -> AuditFinding:
    return AuditFinding(
        rule_id=rule_id,
        severity=severity,
        category=category,
        message=message,
        evidence_span=_clip(evidence),
        remediation=remediation,
    )


def _security_findings(text: str) -> Iterable[AuditFinding]:
    for rule_id, severity, category, pattern in HOOK_PATTERNS:
        match = pattern.search(text)
        if match:
            yield _finding(
                rule_id,
                severity,
                category,
                f"Detected {category.replace('_', ' ')} language.",
                match.group(0),
                "Remove the authority, evidence, score, or output-contract override.",
            )

    if ZERO_WIDTH_RE.search(text):
        yield _finding(
            "H08_hidden_text_or_encoding",
            "high",
            "hidden_encoding",
            "Detected zero-width or concealed text characters.",
            "zero-width character",
            "Remove concealed characters and express every instruction visibly.",
        )
        return

    for candidate in BASE64_CANDIDATE_RE.findall(text):
        padded = candidate + "=" * (-len(candidate) % 4)
        try:
            decoded = base64.b64decode(padded, validate=True).decode(
                "utf-8", errors="ignore"
            )
        except (ValueError, UnicodeError):
            continue
        if decoded and INJECTION_TERMS_RE.search(decoded):
            yield _finding(
                "H08_hidden_text_or_encoding",
                "high",
                "hidden_encoding",
                "Encoded text contains injection-relevant instructions.",
                candidate,
                "Remove encoded instructions or expose them as inert test data.",
            )
            break


def _contract_findings(text: str, source_prompt: str | None) -> Iterable[AuditFinding]:
    formats = {match.group(1).lower() for match in ONLY_FORMAT_RE.finditer(text)}
    if len(formats) > 1:
        yield _finding(
            "C01_conflicting_output_contract",
            "high",
            "output_contract",
            f"Conflicting output-only formats were found: {sorted(formats)}.",
            ", ".join(sorted(formats)),
            "Choose one authoritative output format and remove the others.",
        )

    if source_prompt is not None:
        source_variables = set(VARIABLE_RE.findall(source_prompt))
        optimized_variables = set(VARIABLE_RE.findall(text))
        missing = sorted(source_variables - optimized_variables)
        if missing:
            yield _finding(
                "C02_template_variable_loss",
                "high",
                "intent_fidelity",
                f"Optimized Prompt dropped source variables: {missing}.",
                ", ".join(missing),
                "Restore every required source variable or document an intentional migration.",
            )

    if HIGH_RISK_RE.search(text) and not HIGH_RISK_BOUNDARY_RE.search(text):
        yield _finding(
            "R01_missing_high_risk_boundary",
            "high",
            "high_risk_boundary",
            "Consequential advice is requested without a qualified-review boundary.",
            HIGH_RISK_RE.search(text).group(0),  # type: ignore[union-attr]
            "Add uncertainty, qualified professional review, and emergency escalation limits.",
        )


def _quality_findings(text: str) -> Iterable[AuditFinding]:
    if len(text) > 100_000:
        yield _finding(
            "Q01_excessive_size",
            "high",
            "maintainability",
            "Prompt exceeds 100,000 characters.",
            f"{len(text)} characters",
            "Remove historical versions, duplicated policy, and non-executable commentary.",
        )
    elif len(text) > 30_000:
        yield _finding(
            "Q01_excessive_size",
            "medium",
            "maintainability",
            "Prompt exceeds 30,000 characters.",
            f"{len(text)} characters",
            "Review whether all sections materially affect the requested output.",
        )

    normalized_lines = [
        " ".join(line.lower().split())
        for line in text.splitlines()
        if len(line.strip()) >= 24
    ]
    duplicates = [line for line, count in Counter(normalized_lines).items() if count >= 3]
    if duplicates:
        yield _finding(
            "Q02_repeated_instruction",
            "medium",
            "maintainability",
            "The same substantial instruction appears at least three times.",
            duplicates[0],
            "Keep one authoritative instruction and remove repeated copies.",
        )


def audit_prompt(text: str, *, source_prompt: str | None = None) -> AuditReport:
    if not isinstance(text, str) or not text.strip():
        finding = _finding(
            "I01_empty_prompt",
            "critical",
            "input",
            "Prompt is empty.",
            "",
            "Provide a non-empty Prompt.",
        )
        return AuditReport(
            schema_version="1.0.0",
            text_sha256=hashlib.sha256(str(text).encode("utf-8")).hexdigest(),
            source_sha256=None,
            passed=False,
            checks=CHECKS,
            findings=(finding,),
        )

    findings = tuple(
        [
            *_security_findings(text),
            *_contract_findings(text, source_prompt),
            *_quality_findings(text),
        ]
    )
    return AuditReport(
        schema_version="1.0.0",
        text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        source_sha256=(
            hashlib.sha256(source_prompt.encode("utf-8")).hexdigest()
            if source_prompt is not None
            else None
        ),
        passed=not any(finding.blocking for finding in findings),
        checks=CHECKS,
        findings=findings,
    )
