"""Compile secure, model-ready Prompt optimization requests."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .analysis import recover_behavioral_contract
from .contracts import OptimizationRequest, PACKAGE_ROOT, PACKAGE_VERSION
from .profiles import DomainProfile, resolve_profile


OPTIMIZER_PROMPT_PATH = PACKAGE_ROOT / "prompts" / "optimizer.md"


def domain_guardrails(profile_id: str) -> list[str]:
    if profile_id == "software_engineering":
        return [
            "When an existing repository or CLI is referenced but its code is absent, "
            "do not invent a replacement application, concrete existing exit codes, "
            "or repository-specific APIs. Require an adaptable patch pattern, exact "
            "behavioral tests, explicit integration points, and clearly marked assumptions.",
            "For rolling deployments and data migrations, require an old/new "
            "reader-writer compatibility matrix, mixed-version write synchronization, "
            "and explicit rollback points. Do not enforce a constraint or remove a "
            "field while an old writer or rollback target can still violate it.",
            "When a task defines a machine-readable output schema, preserve every "
            "required top-level key, container type, field type, and ordering rule "
            "exactly. Validate the final structure against that schema before responding.",
            "Return self-contained code blocks: every referenced constant and helper must "
            "be defined in the same block unless the task explicitly supplies it as an "
            "external dependency. Preserve exact public names and signatures.",
        ]
    if profile_id == "agents_automation":
        return [
            "Preserve the verified current external state exactly. Do not relabel a "
            "failed, unchanged, or unexecuted state as awaiting approval merely because "
            "a retry or recovery action could be approved later. Never infer the current "
            "state from a rollback target, desired version, or proposed next action.",
            "Keep commands, named arguments, retry semantics, checkpoint mutations, "
            "approval scope, and rollback authorization exact. Do not add process "
            "ceremony that obscures the requested operational result.",
            "Do not add approval gates beyond the supplied policy or a clearly inherent "
            "irreversible risk. Do not mandate a fixed visible report template; match the "
            "task's requested fields and keep any extra planning internal.",
        ]
    if profile_id == "marketing_sales":
        return [
            "Deliver finished channel-ready copy in the source Prompt's requested "
            "language. Do not emit unresolved placeholders, alternative versions, or a "
            "test plan unless the source explicitly requests them.",
            "If product facts are missing, avoid unsupported feature claims while still "
            "producing the strongest usable copy supported by the brief. Use every "
            "supplied audience, workflow, offer, objection, and CTA detail; turn abstract "
            "benefits into concrete decisions and situations rather than generic claims.",
            "Preserve the requested deliverable depth and any supplied concrete CTA. A "
            "single finished deliverable may contain all required landing-page sections "
            "or all messages in an email sequence. Cover every requested component once "
            "at channel-appropriate depth without repeating the same proof, CTA, or value "
            "claim merely to appear complete.",
            "Answer every supplied audience objection explicitly where that audience "
            "encounters it; do not leave an objection merely implied by a feature list. "
            "For multi-segment work, give each segment distinct priorities, objections, "
            "proof or evaluation criteria, and CTA framing under one shared positioning "
            "core. For multi-channel work, adapt hierarchy, detail, and tone to each "
            "channel instead of repeating one block of copy.",
            "Preserve the exact relationship and qualification of every proof point. Do "
            "not merge separate facts into a stronger implication, such as implying that "
            "all current customers share the company's full operating tenure.",
            "For deceptive briefs, include a concise visible compliance note that names "
            "and rejects each deceptive element, then provide the complete specific "
            "compliant alternative. The note is part of the requested finished "
            "deliverable, not optional process commentary.",
        ]
    if profile_id == "image_generation":
        return [
            "Return exactly one directly usable image-generation Prompt unless variants "
            "are requested. Preserve explicit aspect ratio, placement, focal hierarchy, "
            "negative space, subject count, lighting direction, and exclusions.",
        ]
    if profile_id == "education":
        return [
            "Honor the requested learner level, duration, and module size before adding "
            "enrichment. Keep objectives, practice, feedback, and assessment feasible "
            "inside the stated time.",
        ]
    if profile_id == "research_analysis":
        return [
            "Match the requested report length and structure. Keep methodology, "
            "confidence language, and caveats concise unless the task requires a formal "
            "research protocol.",
        ]
    return []


def surface_contract(target_surface: str) -> dict[str, Any]:
    agent_surface = target_surface in {"agent", "coding_agent"}
    return {
        "available_context": (
            "supplied conversation plus authorized tools"
            if agent_surface
            else "supplied conversation or API payload only"
        ),
        "tool_access": "authorized tools may be used" if agent_surface else "not assumed",
        "repository_access": (
            "may be inspected when relevant and authorized"
            if agent_surface
            else "must not be assumed"
        ),
        "missing_context_behavior": (
            "inspect authorized context before asking a minimal blocking question"
            if agent_surface
            else "produce the strongest self-contained result from supplied input; "
            "ask only for genuinely indispensable facts"
        ),
        "non_blocking_fallback": (
            "continue with authorized inspection and then provide a patch or exact blocker"
            if agent_surface
            else "for implementation requests, provide a complete adaptable pattern, "
            "focused tests, and explicit integration points instead of merely "
            "restating requirements; block only when guessing would violate an "
            "explicit public, security, data, or compatibility contract"
        ),
    }


def select_architecture(request: OptimizationRequest, profile: DomainProfile) -> str:
    text = request.source_prompt.lower()
    if profile.id == "software_engineering":
        return "plan_execute_verify"
    if profile.id == "research_analysis":
        return "research_then_synthesize"
    if profile.id == "structured_data":
        return "strict_contract"
    if profile.id == "agents_automation":
        return "tool_agent"
    if profile.id == "high_risk_advisory":
        return "high_risk_review"
    if profile.id in {"professional_writing", "creative_design", "image_generation"}:
        return (
            "multi_candidate_tournament"
            if request.mode == "maximum_quality"
            else "brief_then_execute"
        )
    if any(term in text for term in ("json", "schema", "extract", "csv", "xml")):
        return "strict_contract"
    if any(term in text for term in ("medical", "legal", "financial", "security")):
        return "high_risk_review"
    if any(term in text for term in ("tool", "agent", "browser", "send", "deploy")):
        return "tool_agent"
    if profile.id == "business_strategy":
        return "multi_candidate_tournament"
    if profile.id == "marketing_sales":
        return "multi_candidate_tournament"
    if profile.id in {"education", "translation_localization"}:
        return "brief_then_execute"
    return "generate_critique_revise" if request.mode == "maximum_quality" else "direct"


def compile_request(request: OptimizationRequest) -> dict[str, Any]:
    request.validate()
    profile = resolve_profile(request.source_prompt, request.domain)
    architecture = select_architecture(request, profile)
    source_bytes = request.source_prompt.encode("utf-8")
    source_sha256 = hashlib.sha256(source_bytes).hexdigest()

    inert_source = json.dumps(
        {
            "encoding": "json_string",
            "sha256": source_sha256,
            "content": request.source_prompt,
        },
        ensure_ascii=False,
    )
    runtime_request = {
        "schema_version": request.schema_version,
        "mode": request.mode,
        "output_format": request.output_format,
        "target_surface": request.target_surface,
        "surface_contract": surface_contract(request.target_surface),
        "target_model": request.target_model,
        "audience": request.audience,
        "resolved_domain": profile.to_dict(),
        "domain_guardrails": domain_guardrails(profile.id),
        "selected_architecture": architecture,
        "recovered_behavioral_contract": recover_behavioral_contract(
            request.source_prompt
        ).to_dict(),
        "required_behaviors": list(request.required_behaviors),
        "forbidden_changes": list(request.forbidden_changes),
        "source_prompt": inert_source,
        "evidence_boundary": {
            "level": "E0",
            "status": "candidate",
            "claim": "optimized_candidate",
            "limitations": ["No comparative execution has been performed."],
        },
    }
    return {
        "package_version": PACKAGE_VERSION,
        "schema_version": request.schema_version,
        "system_prompt": OPTIMIZER_PROMPT_PATH.read_text(encoding="utf-8"),
        "runtime_request": runtime_request,
    }
