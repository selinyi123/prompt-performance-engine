"""Actual-image registration, blind visual review, and R06 evidence."""

from __future__ import annotations

import binascii
import hashlib
import hmac
import math
import re
import secrets
import shutil
import struct
import zlib
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol, Sequence

from .contracts import ARTIFACT_SCHEMA_VERSION, load_strict_json_object
from .hashing import hash_payload, sha256_json
from .readiness import build_evidence_report


SCHEMA_VERSION = ARTIFACT_SCHEMA_VERSION
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_PNG_BYTES = 50 * 1024 * 1024
MAX_IMAGE_PIXELS = 16_000_000
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
BLIND_ASSET_PATH_RE = re.compile(
    r"^review-assets/[0-9a-f]{16}/[0-9a-f]{32}\.png$"
)

GENERATION_MANIFEST_FIELDS = {
    "schema_version",
    "suite_id",
    "generator",
    "actual_generation",
    "cases",
    "generation_manifest_sha256",
}
GENERATOR_FIELDS = {"provider", "model", "settings"}
GENERATION_CASE_FIELDS = {
    "case_id",
    "input_text",
    "rubric",
    "baseline",
    "optimized",
}
GENERATION_ASSET_FIELDS = {
    "prompt",
    "prompt_sha256",
    "path",
    "call_id",
    "mime_type",
    "width",
    "height",
    "byte_count",
    "sha256",
}
VISUAL_PACKET_FIELDS = {
    "schema_version",
    "reviewer_id",
    "generation_manifest_sha256",
    "protocol",
    "instructions",
    "items",
    "packet_sha256",
}
VISUAL_PACKET_ITEM_FIELDS = {
    "case_id",
    "input_text",
    "rubric",
    "image_a",
    "image_b",
}
BLIND_IMAGE_FIELDS = {"path", "sha256", "mime_type", "width", "height"}
VISUAL_REVIEW_INSTRUCTIONS = {
    "blind": True,
    "winner_values": ["A", "B", "tie"],
    "score_range": [1, 5],
    "minimum_reason_characters": 20,
}
VISUAL_SUBMISSION_FIELDS = {
    "schema_version",
    "reviewer_id",
    "packet_sha256",
    "decisions",
    "submission_sha256",
}
VISUAL_DECISION_FIELDS = {"case_id", "winner", "reason", "scores"}
VISUAL_REVIEWER_PROFILE_FIELDS = {
    "schema_version",
    "reviewer_id",
    "qualification",
    "profile_sha256",
}
VISUAL_QUALIFICATION_FIELDS = {
    "visual_review_experience_years",
    "relevant_domains",
    "independent",
    "conflict_disclosed",
}
VISUAL_REVIEW_KEY_FIELDS = {
    "schema_version",
    "reviewer_id",
    "generation_manifest_sha256",
    "packet_sha256",
    "protocol",
    "seed",
    "items",
    "key_sha256",
}
VISUAL_REVIEW_KEY_ITEM_FIELDS = {
    "case_id",
    "optimized_label",
    "deliveries",
}
VISUAL_REVIEW_DELIVERY_FIELDS = {
    "label",
    "public_path",
    "source_path",
    "sha256",
}
VISUAL_REVIEW_PROTOCOL = "balanced_hmac_sha256_v2"
VISUAL_PUBLIC_PROTOCOL_FIELDS = {"name", "blinding_key_sha256"}
VISUAL_PRIVATE_PROTOCOL_FIELDS = {
    "name",
    "blinding_key",
    "blinding_key_sha256",
}
VISUAL_REVIEW_PLAN_FIELDS = {
    "schema_version",
    "report_id",
    "generation_manifest",
    "reviews",
}
VISUAL_REVIEW_PLAN_REVIEW_FIELDS = {
    "packet",
    "key",
    "submission",
    "profile",
}


class ImageGenerationReceiptVerifier(Protocol):
    """Trusted boundary for provider-backed image-generation receipts."""

    def verify(
        self,
        *,
        suite_id: str,
        case_id: str,
        variant: str,
        provider: str,
        model: str,
        settings_sha256: str,
        prompt_sha256: str,
        asset_sha256: str,
        call_id: str,
    ) -> str | None:
        """Return one stable unique receipt digest, or None when unverified."""


class VisualReviewerSubmissionVerifier(Protocol):
    """Trusted boundary for visual-reviewer identity and submission receipts."""

    def verify(
        self,
        *,
        reviewer_id: str,
        profile_sha256: str,
        generation_manifest_sha256: str,
        packet_sha256: str,
        submission_sha256: str,
    ) -> str | None:
        """Return one stable unique receipt digest, or None when unverified."""


def _is_json_integer(
    value: Any,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> bool:
    if isinstance(value, bool):
        return False
    valid = (
        isinstance(value, int)
        or (
            isinstance(value, float)
            and math.isfinite(value)
            and value.is_integer()
        )
        or (
            isinstance(value, Decimal)
            and value.is_finite()
            and value == value.to_integral()
        )
    )
    if not valid:
        return False
    if minimum is not None and value < minimum:
        return False
    return maximum is None or value <= maximum


def _is_nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and SHA256_RE.fullmatch(value) is not None


def _utf8_sha256(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()
    except UnicodeError:
        return None


def _hash_matches(payload: Any, hash_field: str) -> bool:
    if not isinstance(payload, dict) or not _is_sha256(payload.get(hash_field)):
        return False
    try:
        return payload[hash_field] == hash_payload(payload, hash_field)
    except (
        ArithmeticError,
        RecursionError,
        TypeError,
        UnicodeError,
        ValueError,
    ):
        return False


def _valid_string_list(
    value: Any,
    *,
    minimum_items: int,
    unique: bool = True,
) -> bool:
    if (
        not isinstance(value, list)
        or len(value) < minimum_items
        or any(not _is_nonempty_string(item) for item in value)
    ):
        return False
    return not unique or len(set(value)) == len(value)


def verifier_implementation_sha256() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _contained(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative:
        raise ValueError("Image-review path must be a non-empty relative path.")
    relative_path = Path(relative)
    if relative_path.is_absolute():
        raise ValueError(f"Image-review path must be relative: {relative}")
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"Image-review path escapes package root: {relative}") from exc
    return candidate


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
    if not isinstance(plan, dict):
        raise ValueError("Image-generation plan root must be an object.")
    if plan.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Unsupported image-generation plan schema.")
    generator = plan.get("generator")
    if not isinstance(generator, dict):
        raise ValueError("Image-generation plan requires generator metadata.")
    if (
        not {"provider", "model"}.issubset(generator)
        or set(generator) - GENERATOR_FIELDS
    ):
        raise ValueError("Image-generation generator fields are invalid.")
    for field in ("provider", "model"):
        if not _is_nonempty_string(generator.get(field)):
            raise ValueError(f"Generator {field} must not be empty.")
    if "settings" in generator and not isinstance(generator["settings"], dict):
        raise ValueError("Generator settings must be an object.")
    cases = plan.get("cases")
    if not isinstance(cases, list) or len(cases) < 5:
        raise ValueError("At least five image cases are required.")
    observed_ids: set[str] = set()
    observed_calls: set[str] = set()
    registered: list[dict[str, Any]] = []
    for raw in cases:
        if not isinstance(raw, dict):
            raise ValueError("Image case must be an object.")
        case_id_value = raw.get("case_id")
        case_id = case_id_value.strip() if isinstance(case_id_value, str) else ""
        if not case_id or case_id in observed_ids:
            raise ValueError(f"Invalid or duplicate image case id: {case_id!r}")
        observed_ids.add(case_id)
        input_text = raw.get("input_text")
        if not _is_nonempty_string(input_text):
            raise ValueError(f"{case_id}: input_text must not be empty.")
        rubric = raw.get("rubric")
        if not _valid_string_list(rubric, minimum_items=3):
            raise ValueError(f"{case_id}: at least three rubric criteria required.")
        variants: dict[str, Any] = {}
        for name in ("baseline", "optimized"):
            variant = raw.get(name)
            if not isinstance(variant, dict):
                raise ValueError(f"{case_id}: missing {name} generation.")
            prompt_value = variant.get("prompt")
            relative_value = variant.get("path")
            call_id_value = variant.get("call_id")
            if not all(
                _is_nonempty_string(value)
                for value in (prompt_value, relative_value, call_id_value)
            ):
                raise ValueError(
                    f"{case_id}: {name} requires prompt, path, and call_id."
                )
            prompt = prompt_value.strip()
            relative = relative_value.strip()
            call_id = call_id_value.strip()
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
                "input_text": input_text.strip(),
                "rubric": [item.strip() for item in rubric],
                "baseline": variants["baseline"],
                "optimized": variants["optimized"],
            }
        )
    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "suite_id": str(plan.get("suite_id", "")).strip(),
        "generator": {
            key: generator[key]
            for key in ("provider", "model", "settings")
            if key in generator
        },
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
    manifest: Any,
    *,
    root: Path,
) -> list[str]:
    if not isinstance(manifest, dict):
        return ["generation manifest root must be an object"]
    failures: list[str] = []
    if set(manifest) != GENERATION_MANIFEST_FIELDS:
        failures.append("generation manifest fields do not match the schema")
    if manifest.get("schema_version") != SCHEMA_VERSION:
        failures.append("unsupported generation manifest schema")
    if not _hash_matches(manifest, "generation_manifest_sha256"):
        failures.append("generation manifest hash mismatch")
    if not _is_nonempty_string(manifest.get("suite_id")):
        failures.append("generation suite id is invalid")
    generator = manifest.get("generator")
    if (
        not isinstance(generator, dict)
        or not {"provider", "model"}.issubset(generator)
        or set(generator) - GENERATOR_FIELDS
    ):
        failures.append("generator fields do not match the schema")
    else:
        if any(
            not _is_nonempty_string(generator.get(field))
            for field in ("provider", "model")
        ):
            failures.append("generator provider and model must not be empty")
        if "settings" in generator and not isinstance(generator["settings"], dict):
            failures.append("generator settings must be an object")
    if manifest.get("actual_generation") is not True:
        failures.append("actual image generation is not asserted")
    cases = manifest.get("cases")
    if not isinstance(cases, list) or len(cases) < 5:
        return [*failures, "fewer than five image cases"]
    ids: set[str] = set()
    call_ids: set[str] = set()
    for case in cases:
        if not isinstance(case, dict):
            failures.append("invalid image case")
            continue
        if set(case) != GENERATION_CASE_FIELDS:
            failures.append("image case fields do not match the schema")
        case_id_value = case.get("case_id")
        case_id = case_id_value if isinstance(case_id_value, str) else ""
        if not _is_nonempty_string(case_id) or case_id in ids:
            failures.append(f"invalid or duplicate image case: {case_id!r}")
        elif case_id:
            ids.add(case_id)
        if not _is_nonempty_string(case.get("input_text")):
            failures.append(f"{case_id}: input text is invalid")
        if not _valid_string_list(case.get("rubric"), minimum_items=3):
            failures.append(f"{case_id}: visual rubric is invalid")
        for name in ("baseline", "optimized"):
            variant = case.get(name)
            if not isinstance(variant, dict):
                failures.append(f"{case_id}: missing {name} asset")
                continue
            if set(variant) != GENERATION_ASSET_FIELDS:
                failures.append(f"{case_id}:{name}: asset fields do not match the schema")
            prompt = variant.get("prompt")
            if not _is_nonempty_string(prompt):
                failures.append(f"{case_id}:{name}: prompt is invalid")
            elif (
                not _is_sha256(variant.get("prompt_sha256"))
                or variant.get("prompt_sha256") != _utf8_sha256(prompt)
            ):
                failures.append(f"{case_id}:{name}: prompt hash mismatch")
            relative = variant.get("path")
            if not _is_nonempty_string(relative):
                failures.append(f"{case_id}:{name}: asset path is invalid")
            call_id = variant.get("call_id")
            if not _is_nonempty_string(call_id):
                failures.append(f"{case_id}:{name}: generation call id missing")
            elif call_id in call_ids:
                failures.append(f"{case_id}:{name}: duplicate generation call id")
            else:
                call_ids.add(call_id)
            if variant.get("mime_type") != "image/png":
                failures.append(f"{case_id}:{name}: asset is not PNG")
            if not _is_json_integer(variant.get("width"), minimum=256) or not _is_json_integer(
                variant.get("height"), minimum=256
            ):
                failures.append(f"{case_id}:{name}: dimensions are invalid")
            if not _is_json_integer(
                variant.get("byte_count"),
                minimum=1,
                maximum=MAX_PNG_BYTES,
            ):
                failures.append(f"{case_id}:{name}: byte count is invalid")
            if not _is_sha256(variant.get("sha256")):
                failures.append(f"{case_id}:{name}: sha256 is invalid")
            if not _is_nonempty_string(relative):
                continue
            try:
                path = _contained(root, relative)
                metadata = inspect_png(path)
            except (OSError, ValueError) as exc:
                failures.append(f"{case_id}:{name}: {exc}")
                continue
            for field in ("sha256", "mime_type", "width", "height", "byte_count"):
                if variant.get(field) != metadata[field]:
                    failures.append(f"{case_id}:{name}: {field} mismatch")
    return failures


def _visual_hmac(
    blinding_key: str,
    *,
    purpose: str,
    reviewer_id: str,
    case_id: str,
    seed: int | float | Decimal,
    label: str = "",
    asset_sha256: str = "",
) -> bytes:
    payload = (
        f"{purpose}\0{seed}\0{reviewer_id}\0{case_id}\0{label}\0"
        f"{asset_sha256}"
    ).encode("utf-8")
    return hmac.new(bytes.fromhex(blinding_key), payload, hashlib.sha256).digest()


def _optimized_is_a(
    reviewer_id: str,
    case_id: str,
    seed: int | float | Decimal,
    blinding_key: str,
) -> bool:
    digest = _visual_hmac(
        blinding_key,
        purpose="position",
        reviewer_id=reviewer_id,
        case_id=case_id,
        seed=seed,
    )
    return digest[0] % 2 == 0


def _blind_asset_path(
    reviewer_id: str,
    case_id: str,
    label: str,
    asset_sha256: str,
    seed: int | float | Decimal,
    blinding_key: str,
) -> str:
    reviewer_directory = _visual_hmac(
        blinding_key,
        purpose="reviewer-directory",
        reviewer_id=reviewer_id,
        case_id="",
        seed=seed,
    ).hex()[:16]
    asset_name = _visual_hmac(
        blinding_key,
        purpose="asset-path",
        reviewer_id=reviewer_id,
        case_id=case_id,
        seed=seed,
        label=label,
        asset_sha256=asset_sha256,
    ).hex()[:32]
    return f"review-assets/{reviewer_directory}/{asset_name}.png"


def _build_visual_review_material(
    manifest: dict[str, Any],
    *,
    reviewer_id: str,
    seed: int | float | Decimal,
    blinding_key: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    items: list[dict[str, Any]] = []
    secrets: list[dict[str, Any]] = []
    for case in manifest["cases"]:
        optimized_is_a = _optimized_is_a(
            reviewer_id,
            case["case_id"],
            seed,
            blinding_key,
        )
        asset_a = case["optimized"] if optimized_is_a else case["baseline"]
        asset_b = case["baseline"] if optimized_is_a else case["optimized"]
        public_path_a = _blind_asset_path(
            reviewer_id,
            case["case_id"],
            "A",
            asset_a["sha256"],
            seed,
            blinding_key,
        )
        public_path_b = _blind_asset_path(
            reviewer_id,
            case["case_id"],
            "B",
            asset_b["sha256"],
            seed,
            blinding_key,
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
                        "label": "A",
                        "public_path": public_path_a,
                        "source_path": asset_a["path"],
                        "sha256": asset_a["sha256"],
                    },
                    {
                        "label": "B",
                        "public_path": public_path_b,
                        "source_path": asset_b["path"],
                        "sha256": asset_b["sha256"],
                    },
                ],
            }
        )
    return items, secrets


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
    if not _is_nonempty_string(reviewer_id):
        raise ValueError("reviewer_id must not be empty.")
    if not _is_json_integer(seed):
        raise ValueError("Visual review seed must be a JSON integer.")
    reviewer_id = reviewer_id.strip()
    blinding_key = secrets.token_hex(32)
    blinding_key_sha256 = hashlib.sha256(
        bytes.fromhex(blinding_key)
    ).hexdigest()
    items, secret_items = _build_visual_review_material(
        manifest,
        reviewer_id=reviewer_id,
        seed=seed,
        blinding_key=blinding_key,
    )
    packet: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "reviewer_id": reviewer_id,
        "generation_manifest_sha256": manifest["generation_manifest_sha256"],
        "protocol": {
            "name": VISUAL_REVIEW_PROTOCOL,
            "blinding_key_sha256": blinding_key_sha256,
        },
        "instructions": {
            key: list(value) if isinstance(value, list) else value
            for key, value in VISUAL_REVIEW_INSTRUCTIONS.items()
        },
        "items": items,
    }
    packet["packet_sha256"] = hash_payload(packet, "packet_sha256")
    key: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "reviewer_id": reviewer_id,
        "generation_manifest_sha256": manifest["generation_manifest_sha256"],
        "packet_sha256": packet["packet_sha256"],
        "protocol": {
            "name": VISUAL_REVIEW_PROTOCOL,
            "blinding_key": blinding_key,
            "blinding_key_sha256": blinding_key_sha256,
        },
        "seed": seed,
        "items": secret_items,
    }
    key["key_sha256"] = hash_payload(key, "key_sha256")
    return packet, key


def _validate_visual_review_key_shape(key: Any) -> list[str]:
    if not isinstance(key, dict):
        return ["visual review key root must be an object"]
    failures: list[str] = []
    if set(key) != VISUAL_REVIEW_KEY_FIELDS:
        failures.append("visual review key fields do not match the schema")
    if key.get("schema_version") != SCHEMA_VERSION:
        failures.append("unsupported visual review key schema")
    if not _hash_matches(key, "key_sha256"):
        failures.append("visual review key hash mismatch")
    if not _is_nonempty_string(key.get("reviewer_id")):
        failures.append("visual review key reviewer id is invalid")
    if not _is_sha256(key.get("generation_manifest_sha256")):
        failures.append("visual review key manifest hash is invalid")
    if not _is_sha256(key.get("packet_sha256")):
        failures.append("visual review key packet hash is invalid")
    if not _is_json_integer(key.get("seed")):
        failures.append("visual review key seed is invalid")
    protocol = key.get("protocol")
    if not isinstance(protocol, dict) or set(protocol) != VISUAL_PRIVATE_PROTOCOL_FIELDS:
        failures.append("visual review key protocol fields are invalid")
    else:
        blinding_key = protocol.get("blinding_key")
        commitment = protocol.get("blinding_key_sha256")
        if protocol.get("name") != VISUAL_REVIEW_PROTOCOL:
            failures.append("visual review key protocol is unsupported")
        if not _is_sha256(blinding_key):
            failures.append("visual review blinding key is invalid")
        elif commitment != hashlib.sha256(bytes.fromhex(blinding_key)).hexdigest():
            failures.append("visual review blinding key commitment mismatch")
    items = key.get("items")
    if not isinstance(items, list) or len(items) < 5:
        return [*failures, "visual review key requires at least five items"]
    observed: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            failures.append("invalid visual review key item")
            continue
        if set(item) != VISUAL_REVIEW_KEY_ITEM_FIELDS:
            failures.append("visual review key item fields do not match the schema")
        case_id = item.get("case_id")
        if not _is_nonempty_string(case_id) or case_id in observed:
            failures.append("visual review key case ids must be non-empty and unique")
        elif isinstance(case_id, str):
            observed.add(case_id)
        optimized_label = item.get("optimized_label")
        if not isinstance(optimized_label, str) or optimized_label not in {"A", "B"}:
            failures.append(f"{case_id}: optimized label is invalid")
        deliveries = item.get("deliveries")
        if not isinstance(deliveries, list) or len(deliveries) != 2:
            failures.append(f"{case_id}: visual review deliveries are invalid")
            continue
        for expected_label, delivery in zip(("A", "B"), deliveries):
            if not isinstance(delivery, dict):
                failures.append(f"{case_id}:{expected_label}: delivery is invalid")
                continue
            if set(delivery) != VISUAL_REVIEW_DELIVERY_FIELDS:
                failures.append(
                    f"{case_id}:{expected_label}: delivery fields do not match the schema"
                )
            if delivery.get("label") != expected_label:
                failures.append(f"{case_id}:{expected_label}: delivery label is invalid")
            public_path = delivery.get("public_path")
            if (
                not isinstance(public_path, str)
                or BLIND_ASSET_PATH_RE.fullmatch(public_path) is None
            ):
                failures.append(
                    f"{case_id}:{expected_label}: delivery public path is invalid"
                )
            if not _is_nonempty_string(delivery.get("source_path")):
                failures.append(
                    f"{case_id}:{expected_label}: delivery source path is invalid"
                )
            if not _is_sha256(delivery.get("sha256")):
                failures.append(f"{case_id}:{expected_label}: delivery sha256 is invalid")
    return failures


def validate_visual_review_key(
    manifest: Any,
    packet: Any,
    key: Any,
) -> list[str]:
    """Validate and replay the private A/B mapping against public authority."""

    failures = _validate_visual_review_key_shape(key)
    if not isinstance(manifest, dict):
        return [*failures, "generation manifest root must be an object"]
    if not isinstance(packet, dict):
        return [*failures, "visual review packet root must be an object"]
    packet_failures = validate_visual_review_packet(packet)
    failures.extend(
        f"packet: {failure}" for failure in packet_failures
    )
    if not isinstance(key, dict):
        return failures
    if key.get("reviewer_id") != packet.get("reviewer_id"):
        failures.append("visual review key reviewer does not match packet")
    if key.get("packet_sha256") != packet.get("packet_sha256"):
        failures.append("visual review key does not match packet")
    manifest_hash = manifest.get("generation_manifest_sha256")
    if packet.get("generation_manifest_sha256") != manifest_hash:
        failures.append("visual review packet does not match generation manifest")
    if key.get("generation_manifest_sha256") != manifest_hash:
        failures.append("visual review key does not match generation manifest")
    reviewer_id = key.get("reviewer_id")
    seed = key.get("seed")
    key_protocol = key.get("protocol")
    packet_protocol = packet.get("protocol")
    blinding_key = (
        key_protocol.get("blinding_key")
        if isinstance(key_protocol, dict)
        else None
    )
    if (
        not isinstance(key_protocol, dict)
        or not isinstance(packet_protocol, dict)
        or packet_protocol
        != {
            "name": key_protocol.get("name"),
            "blinding_key_sha256": key_protocol.get("blinding_key_sha256"),
        }
    ):
        failures.append("visual review public and private protocols do not match")
    cases = manifest.get("cases")
    if (
        not _is_nonempty_string(reviewer_id)
        or not _is_json_integer(seed)
        or not _is_sha256(blinding_key)
        or not isinstance(cases, list)
        or any(not isinstance(case, dict) for case in cases)
    ):
        failures.append("visual review key replay inputs are invalid")
        return failures
    try:
        expected_items, expected_secrets = _build_visual_review_material(
            manifest,
            reviewer_id=reviewer_id,
            seed=seed,
            blinding_key=blinding_key,
        )
    except (KeyError, TypeError, ValueError):
        failures.append("visual review key replay inputs are invalid")
        return failures
    if packet.get("items") != expected_items:
        failures.append("visual review packet items do not match replayed A/B mapping")
    if key.get("items") != expected_secrets:
        failures.append("visual review key items do not match replayed hidden mapping")
    return failures


def deliver_visual_review_assets(
    key: dict[str, Any],
    *,
    source_root: Path,
    packet_root: Path,
) -> list[Path]:
    key_failures = _validate_visual_review_key_shape(key)
    if key_failures:
        raise ValueError(f"Invalid visual review key: {key_failures}")
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


def validate_visual_review_packet(packet: Any) -> list[str]:
    if not isinstance(packet, dict):
        return ["visual review packet root must be an object"]
    failures: list[str] = []
    if set(packet) != VISUAL_PACKET_FIELDS:
        failures.append("visual review packet fields do not match the schema")
    if packet.get("schema_version") != SCHEMA_VERSION:
        failures.append("unsupported visual review packet schema")
    if not _hash_matches(packet, "packet_sha256"):
        failures.append("packet hash mismatch")
    if not _is_nonempty_string(packet.get("reviewer_id")):
        failures.append("visual review packet reviewer id is invalid")
    if not _is_sha256(packet.get("generation_manifest_sha256")):
        failures.append("visual review packet manifest hash is invalid")
    protocol = packet.get("protocol")
    if not isinstance(protocol, dict) or set(protocol) != VISUAL_PUBLIC_PROTOCOL_FIELDS:
        failures.append("visual review packet protocol fields are invalid")
    else:
        if protocol.get("name") != VISUAL_REVIEW_PROTOCOL:
            failures.append("visual review packet protocol is unsupported")
        if not _is_sha256(protocol.get("blinding_key_sha256")):
            failures.append("visual review packet key commitment is invalid")
    instructions = packet.get("instructions")
    if instructions != VISUAL_REVIEW_INSTRUCTIONS:
        failures.append("visual review packet instructions do not match the schema")
    if not isinstance(instructions, dict) or instructions.get("blind") is not True:
        failures.append("visual review packet is not blind")
    items = packet.get("items")
    if not isinstance(items, list) or len(items) < 5:
        return [*failures, "visual review packet requires at least five items"]
    observed: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            failures.append("invalid visual review packet item")
            continue
        if set(item) != VISUAL_PACKET_ITEM_FIELDS:
            failures.append("visual review packet item fields do not match the schema")
        case_id = item.get("case_id")
        if not _is_nonempty_string(case_id) or case_id in observed:
            failures.append(
                "visual review packet case ids must be non-empty and unique"
            )
        elif isinstance(case_id, str):
            observed.add(case_id)
        if not _is_nonempty_string(item.get("input_text")):
            failures.append(f"{case_id}: visual input text is invalid")
        rubric = item.get("rubric")
        if not _valid_string_list(rubric, minimum_items=3):
            failures.append(f"{case_id}: visual rubric is invalid")
        for label in ("image_a", "image_b"):
            asset = item.get(label)
            if not isinstance(asset, dict):
                failures.append(f"{case_id}:{label}: asset is missing")
                continue
            if set(asset) != BLIND_IMAGE_FIELDS:
                failures.append(
                    f"{case_id}:{label}: asset fields do not match the schema"
                )
            path = asset.get("path")
            if not isinstance(path, str) or BLIND_ASSET_PATH_RE.fullmatch(path) is None:
                failures.append(f"{case_id}:{label}: asset path is not blind")
            if asset.get("mime_type") != "image/png":
                failures.append(f"{case_id}:{label}: asset is not PNG")
            if not _is_json_integer(
                asset.get("width"), minimum=256
            ) or not _is_json_integer(
                asset.get("height"), minimum=256
            ):
                failures.append(f"{case_id}:{label}: dimensions are invalid")
            if not _is_sha256(asset.get("sha256")):
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


def validate_reviewer_profile(profile: Any) -> list[str]:
    if not isinstance(profile, dict):
        return ["visual reviewer profile root must be an object"]
    failures: list[str] = []
    if set(profile) != VISUAL_REVIEWER_PROFILE_FIELDS:
        failures.append("visual reviewer profile fields do not match the schema")
    if profile.get("schema_version") != SCHEMA_VERSION:
        failures.append("unsupported reviewer profile schema")
    if not _hash_matches(profile, "profile_sha256"):
        failures.append("reviewer profile hash mismatch")
    if not _is_nonempty_string(profile.get("reviewer_id")):
        failures.append("reviewer id missing")
    qualification = profile.get("qualification")
    if not isinstance(qualification, dict):
        return [*failures, "reviewer qualification missing"]
    if set(qualification) != VISUAL_QUALIFICATION_FIELDS:
        failures.append("reviewer qualification fields do not match the schema")
    if not _is_json_integer(
        qualification.get("visual_review_experience_years"),
        minimum=2,
    ):
        failures.append("reviewer has fewer than two years visual experience")
    relevant_domains = qualification.get("relevant_domains")
    if not _valid_string_list(relevant_domains, minimum_items=1):
        failures.append("reviewer domains are invalid")
    if (
        not isinstance(relevant_domains, list)
        or "image_generation" not in relevant_domains
    ):
        failures.append("reviewer lacks image-generation domain qualification")
    if qualification.get("independent") is not True:
        failures.append("reviewer independence is not attested")
    if qualification.get("conflict_disclosed") is not True:
        failures.append("reviewer conflict disclosure is missing")
    return failures


def validate_visual_submission(
    packet: Any,
    submission: Any,
) -> list[str]:
    failures = validate_visual_review_packet(packet)
    if not isinstance(submission, dict):
        return [*failures, "visual submission root must be an object"]
    if set(submission) != VISUAL_SUBMISSION_FIELDS:
        failures.append("visual submission fields do not match the schema")
    if submission.get("schema_version") != SCHEMA_VERSION:
        failures.append("unsupported visual submission schema")
    if not _hash_matches(submission, "submission_sha256"):
        failures.append("submission hash mismatch")
    packet_object = packet if isinstance(packet, dict) else {}
    if not _is_nonempty_string(submission.get("reviewer_id")):
        failures.append("submission reviewer id is invalid")
    if submission.get("reviewer_id") != packet_object.get("reviewer_id"):
        failures.append("submission reviewer mismatch")
    if not _is_sha256(submission.get("packet_sha256")):
        failures.append("submission packet hash is invalid")
    if submission.get("packet_sha256") != packet_object.get("packet_sha256"):
        failures.append("submission packet mismatch")
    expected: dict[str, dict[str, Any]] = {}
    packet_items = packet_object.get("items")
    if isinstance(packet_items, list):
        for item in packet_items:
            if (
                isinstance(item, dict)
                and _is_nonempty_string(item.get("case_id"))
                and item["case_id"] not in expected
            ):
                expected[item["case_id"]] = item
    decisions = submission.get("decisions")
    if not isinstance(decisions, list) or len(decisions) < 5:
        return [*failures, "submission requires at least five decisions"]
    observed: set[str] = set()
    for decision in decisions:
        if not isinstance(decision, dict):
            failures.append("invalid visual decision")
            continue
        if set(decision) != VISUAL_DECISION_FIELDS:
            failures.append("visual decision fields do not match the schema")
        case_id = decision.get("case_id")
        if not _is_nonempty_string(case_id):
            failures.append("visual decision case id is invalid")
            continue
        if case_id in observed:
            failures.append(f"duplicate visual decision: {case_id}")
        observed.add(case_id)
        item = expected.get(case_id)
        if item is None:
            failures.append(f"unknown visual case: {case_id}")
            continue
        winner = decision.get("winner")
        if not isinstance(winner, str) or winner not in {"A", "B", "tie"}:
            failures.append(f"{case_id}: invalid winner")
        reason = decision.get("reason")
        if not isinstance(reason, str) or len(reason.strip()) < 20:
            failures.append(f"{case_id}: reason is too short")
        scores = decision.get("scores")
        if not isinstance(scores, dict):
            failures.append(f"{case_id}: scores are missing")
            continue
        rubric = item.get("rubric")
        expected_criteria = (
            set(rubric)
            if _valid_string_list(rubric, minimum_items=3)
            else set()
        )
        if not expected_criteria or set(scores) != expected_criteria:
            failures.append(f"{case_id}: rubric scores are incomplete")
        for criterion, pair in scores.items():
            if (
                not _is_nonempty_string(criterion)
                or not isinstance(pair, dict)
                or set(pair) != {"A", "B"}
                or any(
                    not _is_json_integer(pair[label], minimum=1, maximum=5)
                    for label in ("A", "B")
                )
            ):
                failures.append(f"{case_id}:{criterion}: invalid A/B scores")
    if observed != set(expected):
        failures.append("submission does not cover assigned image cases exactly")
    return failures


def _generation_receipt(
    verifier: ImageGenerationReceiptVerifier | None,
    **context: str,
) -> str | None:
    if verifier is None:
        return None
    try:
        receipt = verifier.verify(**context)
    except Exception as exc:
        raise ValueError("Image-generation receipt verification failed.") from exc
    if not _is_sha256(receipt):
        raise ValueError("Image-generation receipt is missing or invalid.")
    return receipt


def _visual_reviewer_receipt(
    verifier: VisualReviewerSubmissionVerifier | None,
    **context: str,
) -> str | None:
    if verifier is None:
        return None
    try:
        receipt = verifier.verify(**context)
    except Exception as exc:
        raise ValueError("Visual-reviewer receipt verification failed.") from exc
    if not _is_sha256(receipt):
        raise ValueError("Visual-reviewer receipt is missing or invalid.")
    return receipt


def load_visual_review_plan(
    plan: Any,
    *,
    root: Path,
) -> dict[str, Any]:
    """Strictly load every source artifact named by a visual-review plan."""

    if not isinstance(plan, dict) or set(plan) != VISUAL_REVIEW_PLAN_FIELDS:
        raise ValueError("Visual-review plan fields do not match the contract.")
    if plan.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Unsupported visual-review plan schema.")
    report_id = plan.get("report_id")
    manifest_relative = plan.get("generation_manifest")
    reviews = plan.get("reviews")
    if not _is_nonempty_string(report_id):
        raise ValueError("Visual-review plan report_id must not be empty.")
    if not _is_nonempty_string(manifest_relative):
        raise ValueError("Visual-review plan generation_manifest is invalid.")
    if not isinstance(reviews, list):
        raise ValueError("Visual-review plan reviews must be an array.")
    root = root.resolve()
    observed_paths: set[str] = set()

    def load(relative: Any, *, label: str) -> dict[str, Any]:
        if not _is_nonempty_string(relative):
            raise ValueError(f"Visual-review {label} path is invalid.")
        normalized = relative.strip()
        if normalized in observed_paths:
            raise ValueError("Visual-review plan artifact paths must be unique.")
        observed_paths.add(normalized)
        path = _contained(root, normalized)
        return load_strict_json_object(path, label=f"visual-review {label}")

    manifest = load(manifest_relative, label="generation manifest")
    packets: list[dict[str, Any]] = []
    keys: list[dict[str, Any]] = []
    submissions: list[dict[str, Any]] = []
    profiles: list[dict[str, Any]] = []
    for index, review in enumerate(reviews):
        if not isinstance(review, dict) or set(review) != VISUAL_REVIEW_PLAN_REVIEW_FIELDS:
            raise ValueError(
                f"Visual-review plan review[{index}] fields do not match the contract."
            )
        packets.append(load(review.get("packet"), label=f"packet[{index}]"))
        keys.append(load(review.get("key"), label=f"key[{index}]"))
        submissions.append(
            load(review.get("submission"), label=f"submission[{index}]")
        )
        profiles.append(load(review.get("profile"), label=f"profile[{index}]"))
    return {
        "report_id": report_id.strip(),
        "manifest": manifest,
        "packets": packets,
        "keys": keys,
        "submissions": submissions,
        "profiles": profiles,
    }


def aggregate_visual_review(
    manifest: dict[str, Any],
    packets: Sequence[dict[str, Any]],
    keys: Sequence[dict[str, Any]],
    submissions: Sequence[dict[str, Any]],
    profiles: Sequence[dict[str, Any]],
    *,
    root: Path,
    report_id: str,
    generation_receipt_verifier: ImageGenerationReceiptVerifier | None = None,
    reviewer_submission_verifier: VisualReviewerSubmissionVerifier | None = None,
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
    generator = manifest["generator"]
    settings_sha256 = sha256_json(generator.get("settings", {}))
    generation_receipts: set[str] = set()
    generation_artifacts: list[dict[str, Any]] = []
    for case in manifest["cases"]:
        for variant in ("baseline", "optimized"):
            asset = case[variant]
            receipt = _generation_receipt(
                generation_receipt_verifier,
                suite_id=manifest["suite_id"],
                case_id=case["case_id"],
                variant=variant,
                provider=generator["provider"],
                model=generator["model"],
                settings_sha256=settings_sha256,
                prompt_sha256=asset["prompt_sha256"],
                asset_sha256=asset["sha256"],
                call_id=asset["call_id"],
            )
            if receipt is not None:
                if receipt in generation_receipts:
                    raise ValueError("Image-generation receipts must be unique.")
                generation_receipts.add(receipt)
            generation_artifacts.append(
                {
                    "case_id": case["case_id"],
                    "variant": variant,
                    "call_id": asset["call_id"],
                    "prompt_sha256": asset["prompt_sha256"],
                    "asset_sha256": asset["sha256"],
                    "generation_receipt_sha256": receipt,
                    "generation_verified": receipt is not None,
                }
            )
    generation_receipts_verified = bool(generation_artifacts) and len(
        generation_receipts
    ) == len(generation_artifacts)
    reviewer_ids: set[str] = set()
    qualified_ids: set[str] = set()
    reviewer_receipts: set[str] = set()
    blinding_keys: set[str] = set()
    blinding_key_commitments: set[str] = set()
    review_artifacts: list[dict[str, Any]] = []
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
        key_failures = validate_visual_review_key(manifest, packet, key)
        if key_failures:
            raise ValueError(f"Invalid visual review key: {key_failures}")
        blinding_key = key["protocol"]["blinding_key"]
        blinding_key_commitment = packet["protocol"]["blinding_key_sha256"]
        if blinding_key in blinding_keys:
            raise ValueError("Visual-review blinding keys must be unique per packet.")
        if blinding_key_commitment in blinding_key_commitments:
            raise ValueError(
                "Visual-review blinding-key commitments must be unique per packet."
            )
        blinding_keys.add(blinding_key)
        blinding_key_commitments.add(blinding_key_commitment)
        reviewer_id = str(packet["reviewer_id"])
        if reviewer_id != profile.get("reviewer_id"):
            raise ValueError("Visual reviewer profile does not match packet.")
        if reviewer_id in reviewer_ids:
            raise ValueError("Visual reviewer ids must be unique.")
        reviewer_ids.add(reviewer_id)
        qualified_ids.add(reviewer_id)
        reviewer_receipt = _visual_reviewer_receipt(
            reviewer_submission_verifier,
            reviewer_id=reviewer_id,
            profile_sha256=profile["profile_sha256"],
            generation_manifest_sha256=manifest["generation_manifest_sha256"],
            packet_sha256=packet["packet_sha256"],
            submission_sha256=submission["submission_sha256"],
        )
        if reviewer_receipt is not None:
            if reviewer_receipt in reviewer_receipts:
                raise ValueError("Visual-reviewer receipts must be unique.")
            reviewer_receipts.add(reviewer_receipt)
        review_artifacts.append(
            {
                "reviewer_id": reviewer_id,
                "profile_sha256": profile["profile_sha256"],
                "packet_sha256": packet["packet_sha256"],
                "key_sha256": key["key_sha256"],
                "submission_sha256": submission["submission_sha256"],
                "reviewer_receipt_sha256": reviewer_receipt,
                "reviewer_identity_verified": reviewer_receipt is not None,
            }
        )
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
    reviewer_receipts_verified = bool(reviewer_ids) and len(
        reviewer_receipts
    ) == len(reviewer_ids)
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
        "generation_receipts_verified": generation_receipts_verified,
        "reviewer_receipts_verified": reviewer_receipts_verified,
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
    source_bundle_sha256 = sha256_json(
        {
            "generation_manifest_sha256": manifest[
                "generation_manifest_sha256"
            ],
            "generation_artifacts": generation_artifacts,
            "review_artifacts": sorted(
                review_artifacts,
                key=lambda item: item["reviewer_id"],
            ),
        }
    )
    return build_evidence_report(
        kind="image_review",
        report_id=report_id,
        facts=facts,
        provenance={
            "producer": "prompt_performance_engine.image_review",
            "verifier_implementation_sha256": verifier_implementation_sha256(),
            "suite_id": manifest["suite_id"],
            "generator": manifest["generator"],
            "source_bundle_sha256": source_bundle_sha256,
            "generation_artifacts": generation_artifacts,
            "review_artifacts": sorted(
                review_artifacts,
                key=lambda item: item["reviewer_id"],
            ),
            "packet_sha256": sorted(
                str(packet["packet_sha256"]) for packet in packets
            ),
            "submission_sha256": sorted(
                hash_payload(submission, "submission_sha256")
                for submission in submissions
            ),
        },
        limitations=[
            "Stable-release use requires trusted, unique provider generation "
            "receipts and visual-reviewer identity/submission receipts.",
            "This report proves matched image generation and review coverage; "
            "it does not by itself prove cross-domain stable-release quality.",
        ],
    )


def validate_visual_review_authority(
    report: Any,
    plan: Any,
    *,
    root: Path,
    generation_receipt_verifier: ImageGenerationReceiptVerifier | None = None,
    reviewer_submission_verifier: VisualReviewerSubmissionVerifier | None = None,
) -> list[str]:
    """Replay a visual-review source bundle and compare the complete report."""

    failures: list[str] = []
    if not isinstance(report, dict):
        return ["image-review evidence root must be an object"]
    if report.get("kind") != "image_review":
        failures.append("image-review evidence kind is invalid")
    if not _hash_matches(report, "evidence_sha256"):
        failures.append("image-review evidence hash mismatch")
    if generation_receipt_verifier is None:
        failures.append("trusted image-generation receipt verifier is required")
    if reviewer_submission_verifier is None:
        failures.append("trusted visual-reviewer receipt verifier is required")
    if failures:
        return failures
    try:
        sources = load_visual_review_plan(plan, root=root)
        rebuilt = aggregate_visual_review(
            sources["manifest"],
            sources["packets"],
            sources["keys"],
            sources["submissions"],
            sources["profiles"],
            root=root,
            report_id=sources["report_id"],
            generation_receipt_verifier=generation_receipt_verifier,
            reviewer_submission_verifier=reviewer_submission_verifier,
        )
    except (
        ArithmeticError,
        AssertionError,
        AttributeError,
        OSError,
        RecursionError,
        TypeError,
        UnicodeError,
        ValueError,
    ) as exc:
        return [f"visual-review source plan is invalid: {exc}"]
    if rebuilt != report:
        failures.append("image-review evidence does not match its source plan")
    return failures


def validate_image_evidence_assets(
    facts: Any,
    *,
    root: Path,
) -> list[str]:
    if not isinstance(facts, dict):
        return ["image evidence facts must be an object"]
    failures: list[str] = []
    cases = facts.get("cases")
    if not isinstance(cases, list) or len(cases) < 5:
        return ["image evidence lacks five case-level records"]
    observed: set[str] = set()
    for case in cases:
        if not isinstance(case, dict):
            failures.append("invalid image evidence case")
            continue
        case_id_value = case.get("case_id")
        case_id = case_id_value if isinstance(case_id_value, str) else ""
        if not _is_nonempty_string(case_id) or case_id in observed:
            failures.append(f"invalid or duplicate image evidence case: {case_id!r}")
        elif case_id:
            observed.add(case_id)
        if not _is_json_integer(case.get("review_count"), minimum=3):
            failures.append(f"{case_id}: fewer than three visual reviews")
        consensus = case.get("consensus")
        if not isinstance(consensus, str) or consensus not in {"win", "tie", "loss"}:
            failures.append(f"{case_id}: visual consensus is unresolved")
        for name in ("baseline", "optimized"):
            asset = case.get(name)
            if not isinstance(asset, dict):
                failures.append(f"{case_id}: missing {name} image evidence")
                continue
            relative = asset.get("path")
            if not _is_nonempty_string(relative):
                failures.append(f"{case_id}:{name}: asset path is invalid")
                continue
            try:
                path = _contained(root, relative)
                metadata = inspect_png(path)
            except (OSError, ValueError) as exc:
                failures.append(f"{case_id}:{name}: {exc}")
                continue
            for field in ("sha256", "width", "height"):
                if asset.get(field) != metadata[field]:
                    failures.append(f"{case_id}:{name}: {field} mismatch")
            if not _is_nonempty_string(asset.get("call_id")):
                failures.append(f"{case_id}:{name}: generation call id missing")
    return failures
