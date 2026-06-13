"""Declarative domain-profile registry."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .contracts import ARTIFACT_SCHEMA_VERSION, PACKAGE_ROOT


PROFILE_PATH = PACKAGE_ROOT / "profiles" / "domain_profiles.json"


@dataclass(frozen=True)
class DomainProfile:
    id: str
    name: str
    keywords: tuple[str, ...]
    baseline_requirements: tuple[str, ...]
    professional_differentiators: tuple[str, ...]
    top_tier_differentiators: tuple[str, ...]
    fatal_flaws: tuple[str, ...]
    evaluation_dimensions: tuple[str, ...]
    observable_checks: tuple[str, ...]

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "DomainProfile":
        return cls(
            id=value["id"],
            name=value["name"],
            keywords=tuple(value["keywords"]),
            baseline_requirements=tuple(value["baseline_requirements"]),
            professional_differentiators=tuple(value["professional_differentiators"]),
            top_tier_differentiators=tuple(value["top_tier_differentiators"]),
            fatal_flaws=tuple(value["fatal_flaws"]),
            evaluation_dimensions=tuple(value["evaluation_dimensions"]),
            observable_checks=tuple(value["observable_checks"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "keywords": list(self.keywords),
            "baseline_requirements": list(self.baseline_requirements),
            "professional_differentiators": list(self.professional_differentiators),
            "top_tier_differentiators": list(self.top_tier_differentiators),
            "fatal_flaws": list(self.fatal_flaws),
            "evaluation_dimensions": list(self.evaluation_dimensions),
            "observable_checks": list(self.observable_checks),
        }


def load_profiles(path: Path = PROFILE_PATH) -> dict[str, DomainProfile]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != ARTIFACT_SCHEMA_VERSION:
        raise ValueError("Domain profile schema version does not match the runtime.")
    raw_profiles = data.get("profiles", [])
    identifiers = [item.get("id") for item in raw_profiles]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("Domain profile identifiers must be unique.")
    profiles = {
        item["id"]: DomainProfile.from_dict(item)
        for item in raw_profiles
    }
    if "generic" not in profiles:
        raise ValueError("The domain registry must contain a generic fallback.")
    for profile in profiles.values():
        required_lists = (
            profile.baseline_requirements,
            profile.professional_differentiators,
            profile.top_tier_differentiators,
            profile.fatal_flaws,
            profile.evaluation_dimensions,
            profile.observable_checks,
        )
        if not profile.id or not profile.name or any(not values for values in required_lists):
            raise ValueError(f"Incomplete domain profile: {profile.id!r}.")
    return profiles


def resolve_profile(
    source_prompt: str,
    explicit_domain: str | None = None,
    profiles: dict[str, DomainProfile] | None = None,
) -> DomainProfile:
    registry = profiles or load_profiles()
    if explicit_domain:
        normalized = explicit_domain.strip().lower().replace(" ", "_")
        if normalized in registry:
            return registry[normalized]
        for profile in registry.values():
            if profile.name.lower() == explicit_domain.strip().lower():
                return profile
        raise ValueError(f"Unknown explicit domain: {explicit_domain!r}.")

    lowered = source_prompt.lower()
    scores: list[tuple[int, str]] = []
    for profile_id, profile in registry.items():
        if profile_id == "generic":
            continue
        score = sum(1 for keyword in profile.keywords if keyword in lowered)
        scores.append((score, profile_id))
    best_score, best_id = max(scores, default=(0, "generic"))
    return registry[best_id] if best_score > 0 else registry["generic"]
