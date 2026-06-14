"""Actual-image registration, blind visual review, and R06 evidence."""

from __future__ import annotations

import binascii
import hashlib
import json
import shutil
import struct
import zlib
from collections import Counter
from pathlib import Path
from typing import Any, Sequence

from .hashing import hash_payload
from .readiness import build_evidence_report


SCHEMA_VERSION = "1.0.0"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_PNG_BYTES = 50 * 1024 * 1024
MAX_IMAGE_PIXELS = 16_000_000


def verifier_implementation_sha256() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _contained(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"Image-review path escapes package root: {relative}") from exc
    return candidate


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inspect_png(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    if len(data) > MAX_PNG_BYTES:
        raise ValueError(f"PNG exceeds the 50 MiB evidence limit: {path.name}")
    if not data.startswith(PNG_SIGNATURE):
        raise ValueError(f"Image is not a PNG file: {path.name}")
    offset = len(PNG_SIGNATURE)
    width = height = bit_depth = color_type = interlace = None
    idat = bytearray()
    saw_iend = False
    while offset + 12 <= len(data):
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        chunk_type = data[offset + 4 : offset + 8]
        chunk_start = offset + 8
        chunk_end = chunk_start + length
        crc_end = chunk_end + 4
        if crc_end > len(data):
            raise ValueError(f"PNG chunk is truncated: {path.name}")
        chunk = data[chunk_start:chunk_end]
        expected_crc = struct.unpack(">I", data[chunk_end:crc_end])[0]
        observed_crc = binascii.crc32(chunk_type + chunk) & 0xFFFFFFFF
        if observed_crc != expected_crc:
            raise ValueError(f"PNG CRC mismatch: {path.name}")
        if chunk_type == b"IHDR":
            if length != 13:
                raise ValueError(f"PNG IHDR is invalid: {path.name}")
            width, height, bit_depth, color_type, _, _, interlace = struct.unpack(
                ">IIBBBBB", chunk
            )
        elif chunk_type == b"IDAT":
            idat.extend(chunk)
        elif chunk_type == b"IEND":
            saw_iend = True
            offset = crc_end
            break
        offset = crc_end
    if not saw_iend or offset != len(data):
        raise ValueError(f"PNG has no clean IEND terminator: {path.name}")
    if not width or not height or not idat:
        raise ValueError(f"PNG is missing required image data: {path.name}")
    if width < 256 or height < 256:
        raise ValueError(f"PNG is below the 256px minimum edge: {path.name}")
    if int(width) * int(height) > MAX_IMAGE_PIXELS:
        raise ValueError(f"PNG exceeds the 16-megapixel evidence limit: {path.name}")
    if bit_depth != 8 or color_type not in {0, 2, 4, 6} or interlace != 0:
        raise ValueError(
            f"PNG uses an unsupported evidence format: {path.name}"
        )
    channels = {0: 1, 2: 3, 4: 2, 6: 4}[int(color_type)]
    expected_size = int(height) * (1 + int(width) * channels)
    try:
        decompressor = zlib.decompressobj()
        pixels = decompressor.decompress(bytes(idat), expected_size + 1)
        pixels += decompressor.flush(expected_size + 1 - len(pixels))
    except zlib.error as exc:
        raise ValueError(f"PNG pixel stream is invalid: {path.name}") from exc
    if (
        len(pixels) != expected_size
        or decompressor.unconsumed_tail
        or not decompressor.eof
    ):
        raise ValueError(f"PNG pixel stream has an invalid size: {path.name}")
    sampled = pixels[:: max(1, len(pixels) // 100_000)]
    if len(set(sampled)) < 16:
        raise ValueError(f"PNG lacks substantive visual variation: {path.name}")
    return {
        "mime_type": "image/png",
        "width": int(width),
        "height": int(height),
        "byte_count": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def build_generation_manifest(
    plan: dict[str, Any],
    *,
    root: Path,
) -> dict[str, Any]:
    if plan.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Unsupported image-generation plan schema.")
    generator = plan.get("generator")
    if not isinstance(generator, dict):
        raise ValueError("Image-generation plan requires generator metadata.")
    for field in ("provider", "model"):
        if not isinstance(generator.get(field), str) or not generator[field].strip():
            raise ValueError(f"Generator {field} must not be empty.")
    cases = plan.get("cases")
    if not isinstance(cases, list) or len(cases) < 5:
        raise ValueError("At least five image cases are required.")
    observed_ids: set[str] = set()
    observed_calls: set[str] = set()
    registered: list[dict[str, Any]] = []
    for raw in cases:
        if not isinstance(raw, dict):
            raise ValueError("Image case must be an object.")
        case_id = str(raw.get("case_id", "")).strip()
        if not case_id or case_id in observed_ids:
            raise ValueError(f"Invalid or duplicate image case id: {case_id!r}")
        observed_ids.add(case_id)
        rubric = raw.get("rubric")
        if not isinstance(rubric, list) or len(rubric) < 3:
            raise ValueError(f"{case_id}: at least three rubric criteria required.")
        variants: dict[str, Any] = {}
        for name in ("baseline", "optimized"):
            variant = raw.get(name)
            if not isinstance(variant, dict):
                raise ValueError(f"{case_id}: missing {name} generation.")
            prompt = str(variant.get("prompt", "")).strip()
            relative = str(variant.get("path", "")).strip()
            call_id = str(variant.get("call_id", "")).strip()
            if not prompt or not relative or not call_id:
                raise ValueError(
                    f"{case_id}: {name} requires prompt, path, and call_id."
                )
            if call_id in observed_calls:
                raise ValueError(f"Duplicate image generation call id: {call_id}")
            observed_calls.add(call_id)
            path = _contained(root, relative)
            if not path.is_file():
                raise ValueError(f"Image asset does not exist: {relative}")
            metadata = inspect_png(path)
            variants[name] = {
                "prompt": prompt,
                "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                "path": relative,
                "call_id": call_id,
                **metadata,
            }
        registered.append(
            {
                "case_id": case_id,
                "input_text": str(raw.get("input_text", "")).strip(),
                "rubric": [str(item) for item in rubric],
                "baseline": variants["baseline"],
                "optimized": variants["optimized"],
            }
        )
    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "suite_id": str(plan.get("suite_id", "")).strip(),
        "generator": generator,
        "actual_generation": True,
        "cases": registered,
    }
    if not manifest["suite_id"]:
        raise ValueError("Image-generation suite_id must not be empty.")
    manifest["generation_manifest_sha256"] = hash_payload(
        manifest,
        "generation_manifest_sha256",
    )
    return manifest


def validate_generation_manifest(
    manifest: dict[str, Any],
    *,
    root: Path,
) -> list[str]:
    failures: list[str] = []
    if manifest.get("generation_manifest_sha256") != hash_payload(
        manifest,
        "generation_manifest_sha256",
    ):
        failures.append("generation manifest hash mismatch")
    if manifest.get("actual_generation") is not True:
        failures.append("actual image generation is not asserted")
    cases = manifest.get("cases")
    if not isinstance(cases, list) or len(cases) < 5:
        return [*failures, "fewer than five image cases"]
    ids: set[str] = set()
    for case in cases:
        if not isinstance(case, dict):
            failures.append("invalid image case")
            continue
        case_id = str(case.get("case_id", ""))
        if not case_id or case_id in ids:
            failures.append(f"invalid or duplicate image case: {case_id!r}")
        ids.add(case_id)
        for name in ("baseline", "optimized"):
            variant = case.get(name)
            if not isinstance(variant, dict):
                failures.append(f"{case_id}: missing {name} asset")
                continue
            try:
                path = _contained(root, str(variant.get("path", "")))
                metadata = inspect_png(path)
            except (OSError, ValueError) as exc:
                failures.append(f"{case_id}:{name}: {exc}")
                continue
            for field in ("sha256", "mime_type", "width", "height", "byte_count"):
                if variant.get(field) != metadata[field]:
                    failures.append(f"{case_id}:{name}: {field} mismatch")
            prompt = variant.get("prompt")
            if not isinstance(prompt, str) or variant.get(
                "prompt_sha256"
            ) != hashlib.sha256(prompt.encode("utf-8")).hexdigest():
                failures.append(f"{case_id}:{name}: prompt hash mismatch")
            if not str(variant.get("call_id", "")).strip():
                failures.append(f"{case_id}:{name}: generation call id missing")
    return failures


def _optimized_is_a(reviewer_id: str, case_id: str, seed: int) -> bool:
    digest = hashlib.sha256(
        f"image:{seed}:{reviewer_id}:{case_id}".encode("utf-8")
    ).digest()
    return digest[0] % 2 == 0


def _blind_asset_path(
    reviewer_id: str,
    case_id: str,
    label: str,
    asset_sha256: str,
    seed: int,
) -> str:
    reviewer_directory = hashlib.sha256(
        f"reviewer:{reviewer_id}".encode("utf-8")
    ).hexdigest()[:16]
    asset_name = hashlib.sha256(
        (
            f"asset:{seed}:{reviewer_id}:{case_id}:{label}:"
            f"{asset_sha256}"
        ).encode("utf-8")
    ).hexdigest()[:32]
    return f"review-assets/{reviewer_directory}/{asset_name}.png"


def create_visual_review_packet(
    manifest: dict[str, Any],
    *,
    root: Path,
    reviewer_id: str,
    seed: int = 0,
) -> tuple[dict[str, Any], dict[str, Any]]:
    failures = validate_generation_manifest(manifest, root=root)
    if failures:
        raise ValueError(f"Invalid image-generation manifest: {failures}")
    if not reviewer_id.strip():
        raise ValueError("reviewer_id must not be empty.")
    items: list[dict[str, Any]] = []
    secrets: list[dict[str, Any]] = []
    for case in manifest["cases"]:
        optimized_is_a = _optimized_is_a(reviewer_id, case["case_id"], seed)
        asset_a = case["optimized"] if optimized_is_a else case["baseline"]
        asset_b = case["baseline"] if optimized_is_a else case["optimized"]
        public_path_a = _blind_asset_path(
            reviewer_id,
            case["case_id"],
            "A",
            asset_a["sha256"],
            seed,
        )
        public_path_b = _blind_asset_path(
            reviewer_id,
            case["case_id"],
            "B",
            asset_b["sha256"],
            seed,
        )
        items.append(
            {
                "case_id": case["case_id"],
                "input_text": case["input_text"],
                "rubric": case["rubric"],
                "image_a": {
                    **{
                        key: asset_a[key]
                        for key in ("sha256", "mime_type", "width", "height")
                    },
                    "path": public_path_a,
                },
                "image_b": {
                    **{
                        key: asset_b[key]
                        for key in ("sha256", "mime_type", "width", "height")
                    },
                    "path": public_path_b,
                },
            }
        )
        secrets.append(
            {
                "case_id": case["case_id"],
                "optimized_label": "A" if optimized_is_a else "B",
                "deliveries": [
                    {
                        "public_path": public_path_a,
                        "source_path": asset_a["path"],
                        "sha256": asset_a["sha256"],
                    },
                    {
                        "public_path": public_path_b,
                        "source_path": asset_b["path"],
                        "sha256": asset_b["sha256"],
                    },
                ],
            }
        )
    packet: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "reviewer_id": reviewer_id,
        "generation_manifest_sha256": manifest["generation_manifest_sha256"],
        "instructions": {
            "blind": True,
            "winner_values": ["A", "B", "tie"],
            "score_range": [1, 5],
            "minimum_reason_characters": 20,
        },
        "items": items,
    }
    packet["packet_sha256"] = hash_payload(packet, "packet_sha256")
    key: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "reviewer_id": reviewer_id,
        "packet_sha256": packet["packet_sha256"],
        "items": secrets,
    }
    key["key_sha256"] = hash_payload(key, "key_sha256")
    return packet, key


def deliver_visual_review_assets(
    key: dict[str, Any],
    *,
    source_root: Path,
    packet_root: Path,
) -> list[Path]:
    if key.get("key_sha256") != hash_payload(key, "key_sha256"):
        raise ValueError("Visual review key hash mismatch.")
    delivered: list[Path] = []
    for item in key.get("items", []):
        for delivery in item.get("deliveries", []):
            source = _contained(source_root, str(delivery.get("source_path", "")))
            destination = _contained(
                packet_root,
                str(delivery.get("public_path", "")),
            )
            metadata = inspect_png(source)
            if metadata["sha256"] != delivery.get("sha256"):
                raise ValueError(
                    f"Visual review source hash mismatch: {source.name}"
                )
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            copied = inspect_png(destination)
            if copied["sha256"] != delivery["sha256"]:
                raise ValueError(
                    f"Visual review delivery hash mismatch: {destination.name}"
                )
            delivered.append(destination)
    return delivered


def validate_visual_review_packet(packet: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    if packet.get("schema_version") != SCHEMA_VERSION:
        failures.append("unsupported visual review packet schema")
    if packet.get("packet_sha256") != hash_payload(packet, "packet_sha256"):
        failures.append("packet hash mismatch")
    instructions = packet.get("instructions")
    if not isinstance(instructions, dict) or instructions.get("blind") is not True:
        failures.append("visual review packet is not blind")
    items = packet.get("items")
    if not isinstance(items, list) or not items:
        return [*failures, "visual review packet items are missing"]
    observed: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            failures.append("invalid visual review packet item")
            continue
        case_id = str(item.get("case_id", ""))
        if not case_id or case_id in observed:
            failures.append("visual review packet case ids must be unique")
        observed.add(case_id)
        rubric = item.get("rubric")
        if not isinstance(rubric, list) or not rubric:
            failures.append(f"{case_id}: visual rubric is missing")
        for label in ("image_a", "image_b"):
            asset = item.get(label)
            if not isinstance(asset, dict):
                failures.append(f"{case_id}:{label}: asset is missing")
                continue
            path = str(asset.get("path", "")).replace("\\", "/")
            lowered = path.lower()
            if (
                not path.startswith("review-assets/")
                or "/../" in f"/{path}/"
                or "baseline" in lowered
                or "optimized" in lowered
            ):
                failures.append(f"{case_id}:{label}: asset path is not blind")
            if asset.get("mime_type") != "image/png":
                failures.append(f"{case_id}:{label}: asset is not PNG")
            if not isinstance(asset.get("width"), int) or not isinstance(
                asset.get("height"), int
            ):
                failures.append(f"{case_id}:{label}: dimensions are invalid")
            digest = str(asset.get("sha256", ""))
            if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
                failures.append(f"{case_id}:{label}: sha256 is invalid")
    return failures


def build_reviewer_profile(
    reviewer_id: str,
    *,
    visual_review_experience_years: int,
    relevant_domains: Sequence[str],
    independent: bool,
    conflict_disclosed: bool,
) -> dict[str, Any]:
    profile: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "reviewer_id": reviewer_id.strip(),
        "qualification": {
            "visual_review_experience_years": visual_review_experience_years,
            "relevant_domains": sorted(set(relevant_domains)),
            "independent": independent,
            "conflict_disclosed": conflict_disclosed,
        },
    }
    profile["profile_sha256"] = hash_payload(profile, "profile_sha256")
    failures = validate_reviewer_profile(profile)
    if failures:
        raise ValueError(f"Invalid visual reviewer profile: {failures}")
    return profile


def validate_reviewer_profile(profile: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    if profile.get("schema_version") != SCHEMA_VERSION:
        failures.append("unsupported reviewer profile schema")
    if profile.get("profile_sha256") != hash_payload(profile, "profile_sha256"):
        failures.append("reviewer profile hash mismatch")
    if not str(profile.get("reviewer_id", "")).strip():
        failures.append("reviewer id missing")
    qualification = profile.get("qualification")
    if not isinstance(qualification, dict):
        return [*failures, "reviewer qualification missing"]
    if int(qualification.get("visual_review_experience_years", 0)) < 2:
        failures.append("reviewer has fewer than two years visual experience")
    if "image_generation" not in qualification.get("relevant_domains", []):
        failures.append("reviewer lacks image-generation domain qualification")
    if qualification.get("independent") is not True:
        failures.append("reviewer independence is not attested")
    if qualification.get("conflict_disclosed") is not True:
        failures.append("reviewer conflict disclosure is missing")
    return failures


def validate_visual_submission(
    packet: dict[str, Any],
    submission: dict[str, Any],
) -> list[str]:
    failures = validate_visual_review_packet(packet)
    if submission.get("submission_sha256") != hash_payload(
        submission,
        "submission_sha256",
    ):
        failures.append("submission hash mismatch")
    if submission.get("reviewer_id") != packet.get("reviewer_id"):
        failures.append("submission reviewer mismatch")
    if submission.get("packet_sha256") != packet.get("packet_sha256"):
        failures.append("submission packet mismatch")
    expected = {item["case_id"]: item for item in packet.get("items", [])}
    decisions = submission.get("decisions")
    if not isinstance(decisions, list):
        return [*failures, "submission decisions must be a list"]
    observed: set[str] = set()
    for decision in decisions:
        if not isinstance(decision, dict):
            failures.append("invalid visual decision")
            continue
        case_id = str(decision.get("case_id", ""))
        if case_id in observed:
            failures.append(f"duplicate visual decision: {case_id}")
        observed.add(case_id)
        item = expected.get(case_id)
        if item is None:
            failures.append(f"unknown visual case: {case_id}")
            continue
        if decision.get("winner") not in {"A", "B", "tie"}:
            failures.append(f"{case_id}: invalid winner")
        reason = decision.get("reason")
        if not isinstance(reason, str) or len(reason.strip()) < 20:
            failures.append(f"{case_id}: reason is too short")
        scores = decision.get("scores")
        if not isinstance(scores, dict):
            failures.append(f"{case_id}: scores are missing")
            continue
        expected_criteria = set(item["rubric"])
        if set(scores) != expected_criteria:
            failures.append(f"{case_id}: rubric scores are incomplete")
            continue
        for criterion, pair in scores.items():
            if (
                not isinstance(pair, dict)
                or set(pair) != {"A", "B"}
                or any(
                    not isinstance(pair[label], int)
                    or not 1 <= pair[label] <= 5
                    for label in ("A", "B")
                )
            ):
                failures.append(f"{case_id}:{criterion}: invalid A/B scores")
    if observed != set(expected):
        failures.append("submission does not cover assigned image cases exactly")
    return failures


def aggregate_visual_review(
    manifest: dict[str, Any],
    packets: Sequence[dict[str, Any]],
    keys: Sequence[dict[str, Any]],
    submissions: Sequence[dict[str, Any]],
    profiles: Sequence[dict[str, Any]],
    *,
    root: Path,
    report_id: str,
) -> dict[str, Any]:
    manifest_failures = validate_generation_manifest(manifest, root=root)
    if manifest_failures:
        raise ValueError(f"Invalid image-generation manifest: {manifest_failures}")
    if not (
        len(packets)
        == len(keys)
        == len(submissions)
        == len(profiles)
    ):
        raise ValueError("Visual review artifacts must have equal lengths.")
    reviewer_ids: set[str] = set()
    qualified_ids: set[str] = set()
    votes: dict[str, list[str]] = {
        case["case_id"]: [] for case in manifest["cases"]
    }
    score_deltas: dict[str, list[float]] = {
        case["case_id"]: [] for case in manifest["cases"]
    }
    for packet, key, submission, profile in zip(
        packets, keys, submissions, profiles
    ):
        packet_failures = validate_visual_review_packet(packet)
        if packet_failures:
            raise ValueError(f"Invalid visual review packet: {packet_failures}")
        submission_failures = validate_visual_submission(packet, submission)
        if submission_failures:
            raise ValueError(f"Invalid visual submission: {submission_failures}")
        profile_failures = validate_reviewer_profile(profile)
        if profile_failures:
            raise ValueError(f"Invalid visual reviewer: {profile_failures}")
        if key.get("key_sha256") != hash_payload(key, "key_sha256"):
            raise ValueError("Visual review key hash mismatch.")
        if key.get("packet_sha256") != packet.get("packet_sha256"):
            raise ValueError("Visual review key does not match packet.")
        if (
            packet.get("generation_manifest_sha256")
            != manifest.get("generation_manifest_sha256")
        ):
            raise ValueError("Visual review packet does not match generation manifest.")
        reviewer_id = str(packet["reviewer_id"])
        if reviewer_id != profile.get("reviewer_id"):
            raise ValueError("Visual reviewer profile does not match packet.")
        if reviewer_id in reviewer_ids:
            raise ValueError("Visual reviewer ids must be unique.")
        reviewer_ids.add(reviewer_id)
        qualified_ids.add(reviewer_id)
        secrets = {item["case_id"]: item for item in key["items"]}
        for decision in submission["decisions"]:
            case_id = decision["case_id"]
            winner = decision["winner"]
            optimized_label = secrets[case_id]["optimized_label"]
            mapped = (
                "tie"
                if winner == "tie"
                else "win"
                if winner == optimized_label
                else "loss"
            )
            votes[case_id].append(mapped)
            deltas = [
                pair[optimized_label]
                - pair["B" if optimized_label == "A" else "A"]
                for pair in decision["scores"].values()
            ]
            score_deltas[case_id].append(sum(deltas) / len(deltas))
    consensus: dict[str, str] = {}
    unresolved: list[str] = []
    for case_id, case_votes in votes.items():
        top = Counter(case_votes).most_common()
        if top and (len(top) == 1 or top[0][1] > top[1][1]):
            consensus[case_id] = top[0][0]
        else:
            unresolved.append(case_id)
    reviewed_cases = sum(
        bool(reviewer_ids) and len(case_votes) == len(reviewer_ids)
        for case_votes in votes.values()
    )
    case_evidence = []
    for case in manifest["cases"]:
        case_id = case["case_id"]
        case_evidence.append(
            {
                "case_id": case_id,
                "baseline": {
                    key: case["baseline"][key]
                    for key in ("path", "sha256", "width", "height", "call_id")
                },
                "optimized": {
                    key: case["optimized"][key]
                    for key in ("path", "sha256", "width", "height", "call_id")
                },
                "review_count": len(votes[case_id]),
                "consensus": consensus.get(case_id),
                "mean_optimized_score_delta": (
                    sum(score_deltas[case_id]) / len(score_deltas[case_id])
                    if score_deltas[case_id]
                    else None
                ),
            }
        )
    facts = {
        "eligible_cases": len(manifest["cases"]),
        "generated_cases": len(manifest["cases"]),
        "reviewed_cases": reviewed_cases,
        "qualified_reviewers": len(qualified_ids),
        "blind": True,
        "asset_integrity_verified": True,
        "review_coverage_verified": reviewed_cases == len(manifest["cases"]),
        "unresolved_cases": sorted(unresolved),
        "wins": sum(value == "win" for value in consensus.values()),
        "ties": sum(value == "tie" for value in consensus.values()),
        "losses": sum(value == "loss" for value in consensus.values()),
        "generation_manifest_sha256": manifest["generation_manifest_sha256"],
        "cases": case_evidence,
        "reviewer_profile_sha256": sorted(
            str(profile["profile_sha256"]) for profile in profiles
        ),
    }
    return build_evidence_report(
        kind="image_review",
        report_id=report_id,
        facts=facts,
        provenance={
            "producer": "prompt_performance_engine.image_review",
            "verifier_implementation_sha256": verifier_implementation_sha256(),
            "suite_id": manifest["suite_id"],
            "generator": manifest["generator"],
            "packet_sha256": sorted(
                str(packet["packet_sha256"]) for packet in packets
            ),
            "submission_sha256": sorted(
                hash_payload(submission, "submission_sha256")
                for submission in submissions
            ),
        },
        limitations=[
            "Reviewer qualifications and independence are attested in hashed "
            "profiles and still require external identity verification.",
            "This report proves matched image generation and review coverage; "
            "it does not by itself prove cross-domain stable-release quality.",
        ],
    )


def validate_image_evidence_assets(
    facts: dict[str, Any],
    *,
    root: Path,
) -> list[str]:
    failures: list[str] = []
    cases = facts.get("cases")
    if not isinstance(cases, list) or len(cases) < 5:
        return ["image evidence lacks five case-level records"]
    observed: set[str] = set()
    for case in cases:
        if not isinstance(case, dict):
            failures.append("invalid image evidence case")
            continue
        case_id = str(case.get("case_id", ""))
        if not case_id or case_id in observed:
            failures.append(f"invalid or duplicate image evidence case: {case_id!r}")
        observed.add(case_id)
        if int(case.get("review_count", 0)) < 3:
            failures.append(f"{case_id}: fewer than three visual reviews")
        if case.get("consensus") not in {"win", "tie", "loss"}:
            failures.append(f"{case_id}: visual consensus is unresolved")
        for name in ("baseline", "optimized"):
            asset = case.get(name)
            if not isinstance(asset, dict):
                failures.append(f"{case_id}: missing {name} image evidence")
                continue
            try:
                path = _contained(root, str(asset.get("path", "")))
                metadata = inspect_png(path)
            except (OSError, ValueError) as exc:
                failures.append(f"{case_id}:{name}: {exc}")
                continue
            for field in ("sha256", "width", "height"):
                if asset.get(field) != metadata[field]:
                    failures.append(f"{case_id}:{name}: {field} mismatch")
            if not str(asset.get("call_id", "")).strip():
                failures.append(f"{case_id}:{name}: generation call id missing")
    return failures
