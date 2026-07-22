"""Blind human-review packets bound to authoritative E3 replicate evidence."""

from __future__ import annotations

import hashlib
import hmac
import math
import re
import secrets
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path
from typing import Any, Protocol, Sequence

from .benchmark_replicates import (
    ModelCallReceiptVerifier,
    validate_e3_authority,
    validate_e3_claim,
)
from .contracts import ARTIFACT_SCHEMA_VERSION, load_strict_json_object
from .evidence import Evidence
from .evaluation import validate_evaluation
from .hashing import hash_payload, sha256_json


SCHEMA_VERSION = ARTIFACT_SCHEMA_VERSION
MINIMUM_E4_REVIEWERS = 3
MINIMUM_E4_CASES = 24
MINIMUM_POSITION_PROBES = 2
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REVIEW_ITEM_ID_RE = re.compile(r"^review:[0-9a-f]{64}$")
REVIEW_PROTOCOL = "balanced_round_robin_hmac_sha256_v3"
REVIEW_INSTRUCTIONS = {
    "winner_values": ["A", "B", "tie"],
    "blind": True,
    "minimum_reason_characters": 20,
}


class ReviewerSubmissionVerifier(Protocol):
    """Trusted boundary for reviewer identity, independence, and submission receipt."""

    def verify(
        self,
        *,
        reviewer_id: str,
        packet_sha256: str,
        submission_sha256: str,
    ) -> str | None:
        """Return one unique canonical reviewer receipt digest, or None."""


def _is_json_integer(value: Any, *, minimum: int | None = None) -> bool:
    if isinstance(value, bool):
        return False
    valid = isinstance(value, int) or (
        isinstance(value, float) and math.isfinite(value) and value.is_integer()
    )
    return valid and (minimum is None or value >= minimum)


def _contained_path(root: Path, relative: str, *, label: str) -> Path:
    if not isinstance(relative, str) or not relative:
        raise ValueError(f"{label} must be a non-empty relative path.")
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} escapes the human-review plan root.") from exc
    return candidate


def _load_plan_json(root: Path, relative: str, *, label: str) -> dict[str, Any]:
    path = _contained_path(root, relative, label=label)
    return load_strict_json_object(path, label=label)


def load_human_review_plan(
    plan: Any,
    *,
    root: Path,
) -> dict[str, Any]:
    """Load the complete source bundle for an authoritative human review."""
    if not isinstance(plan, dict):
        raise ValueError("Human-review plan root must be an object.")
    required = {
        "schema_version",
        "replicate_report",
        "run_directories",
        "evaluations",
        "reviews",
    }
    allowed = required | {"adjudications"}
    if set(plan) - allowed:
        raise ValueError("Human-review plan contains unknown fields.")
    if required - set(plan) or plan.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Human-review plan fields or schema are incomplete.")
    root = root.resolve()
    run_paths = plan.get("run_directories")
    evaluation_paths = plan.get("evaluations")
    reviews = plan.get("reviews")
    if (
        not isinstance(run_paths, list)
        or len(run_paths) < 3
        or any(not isinstance(path, str) or not path for path in run_paths)
        or len(set(run_paths)) != len(run_paths)
    ):
        raise ValueError("Human-review plan requires three unique run directories.")
    if (
        not isinstance(evaluation_paths, list)
        or not evaluation_paths
        or any(not isinstance(path, str) or not path for path in evaluation_paths)
    ):
        raise ValueError("Human-review plan requires evaluation paths.")
    if not isinstance(reviews, list) or len(reviews) < 3:
        raise ValueError("Human-review plan requires at least three reviews.")
    run_directories = [
        _contained_path(root, relative, label="run directory")
        for relative in run_paths
    ]
    if any(not path.is_dir() for path in run_directories):
        raise ValueError("Human-review source run directory does not exist.")
    replicate_report = _load_plan_json(
        root,
        plan["replicate_report"],
        label="replicate report",
    )
    evaluations = [
        _load_plan_json(root, relative, label="evaluation")
        for relative in evaluation_paths
    ]
    packets: list[dict[str, Any]] = []
    keys: list[dict[str, Any]] = []
    submissions: list[dict[str, Any]] = []
    for review in reviews:
        if not isinstance(review, dict) or set(review) != {
            "packet",
            "key",
            "submission",
        }:
            raise ValueError("Human-review plan review fields are invalid.")
        packets.append(_load_plan_json(root, review["packet"], label="review packet"))
        keys.append(_load_plan_json(root, review["key"], label="review key"))
        submissions.append(
            _load_plan_json(root, review["submission"], label="review submission")
        )
    adjudications = plan.get("adjudications", {})
    if not isinstance(adjudications, dict) or any(
        not isinstance(item_id, str)
        or not item_id
        or not isinstance(outcome, str)
        or outcome not in {"win", "tie", "loss"}
        for item_id, outcome in adjudications.items()
    ):
        raise ValueError("Human-review adjudications are invalid.")
    return {
        "replicate_report": replicate_report,
        "run_directories": run_directories,
        "evaluations": evaluations,
        "packets": packets,
        "keys": keys,
        "submissions": submissions,
        "adjudications": adjudications,
    }


def _blinded_digest(blinding_key: str, message: str) -> bytes:
    try:
        key = bytes.fromhex(blinding_key)
    except ValueError as exc:
        raise ValueError("Human-review blinding key is invalid.") from exc
    if len(key) != 32:
        raise ValueError("Human-review blinding key must contain 256 bits.")
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()


def _optimized_is_a(
    reviewer_id: str,
    item_id: str,
    seed: int,
    blinding_key: str,
) -> bool:
    digest = _blinded_digest(
        blinding_key,
        f"{REVIEW_PROTOCOL}:label:{seed}:{reviewer_id}:{item_id}",
    )
    return digest[0] % 2 == 0


def _base_item_id(record: dict[str, Any]) -> str:
    return f"case:{record['case_sha256']}"


def _opaque_review_item_id(
    *,
    reviewer_id: str,
    case_sha256: str,
    seed: int,
    variant: str,
    blinding_key: str,
) -> str:
    digest = _blinded_digest(
        blinding_key,
        f"{REVIEW_PROTOCOL}:item:{seed}:{reviewer_id}:{case_sha256}:{variant}",
    ).hex()
    return f"review:{digest}"


def _review_items(
    records: dict[str, dict[str, Any]],
    *,
    reviewer_id: str,
    sample_size: int,
    seed: int,
    position_probe_count: int,
    blinding_key: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if sample_size > len(records):
        raise ValueError("sample_size must fit the available evaluation records.")
    by_domain: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records.values():
        by_domain[record["domain"]].append(record)
    for domain_records in by_domain.values():
        domain_records.sort(
            key=lambda record: hashlib.sha256(
                f"{seed}:{record['domain']}:{record['difficulty']}:"
                f"{record['case_id']}".encode("utf-8")
            ).hexdigest()
        )
    selected: list[dict[str, Any]] = []
    round_index = 0
    while len(selected) < sample_size:
        added = False
        for domain in sorted(by_domain):
            domain_records = by_domain[domain]
            if round_index < len(domain_records):
                selected.append(domain_records[round_index])
                added = True
                if len(selected) == sample_size:
                    break
        if not added:
            break
        round_index += 1

    public_items: list[dict[str, Any]] = []
    secret_items: list[dict[str, Any]] = []

    def add_item(
        record: dict[str, Any],
        item_id: str,
        optimized_is_a: bool,
        probe: bool,
    ) -> None:
        output_a = (
            record["optimized_output"]
            if optimized_is_a
            else record["original_output"]
        )
        output_b = (
            record["original_output"]
            if optimized_is_a
            else record["optimized_output"]
        )
        secret_shared = {
            "item_id": item_id,
            "case_id": record["case_id"],
            "domain": record["domain"],
            "case_sha256": record["case_sha256"],
            "source_evaluation_sha256": record["source_evaluation_sha256"],
        }
        public_items.append(
            {
                "item_id": item_id,
                "input_text": record["input_text"],
                "rubric": record["rubric"],
                "output_a": output_a,
                "output_b": output_b,
            }
        )
        secret_items.append(
            {
                **secret_shared,
                "base_item_id": _base_item_id(record),
                "position_probe": probe,
                "optimized_label": "A" if optimized_is_a else "B",
            }
        )

    for record in selected:
        base_id = _base_item_id(record)
        item_id = _opaque_review_item_id(
            reviewer_id=reviewer_id,
            case_sha256=record["case_sha256"],
            seed=seed,
            variant="base",
            blinding_key=blinding_key,
        )
        add_item(
            record,
            item_id,
            _optimized_is_a(reviewer_id, base_id, seed, blinding_key),
            False,
        )
    for record in selected[:position_probe_count]:
        base_id = _base_item_id(record)
        base_label = next(
            item["optimized_label"]
            for item in secret_items
            if item["base_item_id"] == base_id and not item["position_probe"]
        )
        add_item(
            record,
            _opaque_review_item_id(
                reviewer_id=reviewer_id,
                case_sha256=record["case_sha256"],
                seed=seed,
                variant="probe",
                blinding_key=blinding_key,
            ),
            base_label != "A",
            True,
        )
    public_items.sort(key=lambda item: item["item_id"])
    secret_items.sort(key=lambda item: item["item_id"])
    return public_items, secret_items


def _authority_from_report(
    replicate_report: dict[str, Any],
    evaluation_sha256s: Sequence[str],
) -> dict[str, Any]:
    return {
        "replicate_report_sha256": replicate_report["report_sha256"],
        "suite_id": replicate_report["suite_id"],
        "benchmark_definition_sha256": replicate_report[
            "benchmark_definition_sha256"
        ],
        "evaluation_sha256s": sorted(evaluation_sha256s),
    }


def _authoritative_case_sources(
    run_directories: Sequence[Path],
    *,
    benchmark_definition_sha256: str,
) -> dict[tuple[str, str], dict[str, str]]:
    """Reload the E3-bound benchmark snapshots and expose review task inputs."""

    reference: dict[str, Any] | None = None
    for run_directory in run_directories:
        root = Path(run_directory).resolve()
        snapshot = load_strict_json_object(
            root / "benchmark-definition.json",
            label="human-review benchmark definition",
        )
        if sha256_json(snapshot) != benchmark_definition_sha256:
            raise ValueError(
                "Human-review benchmark definition is not bound to the "
                "replicate report."
            )
        if reference is None:
            reference = snapshot
        elif snapshot != reference:
            raise ValueError(
                "Human-review run directories contain different benchmark "
                "definitions."
            )
    if reference is None:
        raise ValueError("Human review requires authoritative run directories.")

    raw_jobs = reference.get("jobs")
    if not isinstance(raw_jobs, list) or not raw_jobs:
        raise ValueError("Human-review benchmark definition jobs are invalid.")
    sources: dict[tuple[str, str], dict[str, str]] = {}
    for raw_job in raw_jobs:
        if not isinstance(raw_job, dict) or not isinstance(raw_job.get("cases"), list):
            raise ValueError("Human-review benchmark definition job is invalid.")
        for raw_case in raw_job["cases"]:
            if not isinstance(raw_case, dict):
                raise ValueError("Human-review benchmark definition case is invalid.")
            domain = raw_case.get("domain")
            case_id = raw_case.get("case_id")
            input_text = raw_case.get("input_text")
            if (
                not isinstance(domain, str)
                or not domain
                or not isinstance(case_id, str)
                or not case_id
                or not isinstance(input_text, str)
                or not input_text.strip()
            ):
                raise ValueError("Human-review benchmark case input is invalid.")
            key = (domain, case_id)
            if key in sources:
                raise ValueError("Human-review benchmark cases contain duplicates.")
            sources[key] = {
                "input_text": input_text,
                "case_sha256": sha256_json(raw_case),
            }
    return sources


def _authoritative_records(
    evaluations: Sequence[dict[str, Any]],
    replicate_report: dict[str, Any],
    run_directories: Sequence[Path],
    *,
    model_receipt_verifier: ModelCallReceiptVerifier | None,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    authority_failures = validate_e3_authority(
        replicate_report,
        run_directories,
        receipt_verifier=model_receipt_verifier,
    )
    if authority_failures:
        raise ValueError(
            "Human review requires an authoritative E3 replicate report: "
            f"{authority_failures}"
        )
    if not evaluations:
        raise ValueError("At least one evaluation is required for human review.")
    case_sources = _authoritative_case_sources(
        run_directories,
        benchmark_definition_sha256=replicate_report[
            "benchmark_definition_sha256"
        ],
    )

    allowed_by_domain: dict[str, set[str]] = defaultdict(set)
    for replicate in replicate_report["replicates"]:
        for domain, digest in replicate["evaluation_sha256"].items():
            allowed_by_domain[domain].add(digest)
    report_cases = {
        (case["domain"], case["case_id"]): case["case_sha256"]
        for case in replicate_report["cases"]
    }

    records: dict[str, dict[str, Any]] = {}
    evaluation_sha256s: list[str] = []
    seen_evaluations: set[str] = set()
    for evaluation in evaluations:
        failures = validate_evaluation(evaluation)
        if failures:
            raise ValueError(f"Invalid evaluation supplied to human review: {failures}")
        evaluation_sha256 = evaluation["evaluation_sha256"]
        if evaluation_sha256 in seen_evaluations:
            raise ValueError("Human-review evaluations contain a duplicate artifact.")
        seen_evaluations.add(evaluation_sha256)
        evaluation_sha256s.append(evaluation_sha256)
        for record in evaluation["records"]:
            domain = record["domain"]
            case_id = record["case_id"]
            case_key = (domain, case_id)
            if evaluation_sha256 not in allowed_by_domain.get(domain, set()):
                raise ValueError(
                    f"{domain}/{case_id}: evaluation is not bound to the "
                    "replicate report."
                )
            if report_cases.get(case_key) != record.get("case_sha256"):
                raise ValueError(
                    f"{domain}/{case_id}: case definition is not bound to the "
                    "replicate report."
                )
            case_source = case_sources.get(case_key)
            if (
                case_source is None
                or case_source["case_sha256"] != record.get("case_sha256")
            ):
                raise ValueError(
                    f"{domain}/{case_id}: task input is not bound to the "
                    "evaluation case."
                )
            item_id = _base_item_id(record)
            if item_id in records:
                raise ValueError(
                    f"{domain}/{case_id}: duplicate case supplied to human review."
                )
            records[item_id] = {
                **record,
                "input_text": case_source["input_text"],
                "source_evaluation_sha256": evaluation_sha256,
            }
    return _authority_from_report(replicate_report, evaluation_sha256s), records


def create_reviewer_packet(
    evaluations: Sequence[dict[str, Any]],
    *,
    replicate_report: dict[str, Any],
    run_directories: Sequence[Path],
    reviewer_id: str,
    sample_size: int = 24,
    seed: int = 0,
    position_probe_count: int = 2,
    model_receipt_verifier: ModelCallReceiptVerifier | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(reviewer_id, str) or not reviewer_id.strip():
        raise ValueError("reviewer_id must not be empty.")
    if (
        not isinstance(sample_size, int)
        or isinstance(sample_size, bool)
        or sample_size < 1
    ):
        raise ValueError("sample_size must be a positive integer.")
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise ValueError("seed must be an integer.")
    if (
        not isinstance(position_probe_count, int)
        or isinstance(position_probe_count, bool)
        or position_probe_count < MINIMUM_POSITION_PROBES
        or position_probe_count > sample_size
    ):
        raise ValueError(
            "position_probe_count must be an integer between 2 and sample_size."
        )
    authority, record_lookup = _authoritative_records(
        evaluations,
        replicate_report,
        run_directories,
        model_receipt_verifier=model_receipt_verifier,
    )
    blinding_key = secrets.token_hex(32)
    public_items, secret_items = _review_items(
        record_lookup,
        reviewer_id=reviewer_id,
        sample_size=sample_size,
        seed=seed,
        position_probe_count=position_probe_count,
        blinding_key=blinding_key,
    )
    public_protocol = {
        "name": REVIEW_PROTOCOL,
        "sample_size": sample_size,
        "blinding_key_sha256": hashlib.sha256(
            bytes.fromhex(blinding_key)
        ).hexdigest(),
    }
    secret_protocol = {
        "name": REVIEW_PROTOCOL,
        "sample_size": sample_size,
        "seed": seed,
        "position_probe_count": position_probe_count,
        "blinding_key": blinding_key,
    }

    packet: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "reviewer_id": reviewer_id,
        "authority": authority,
        "protocol": public_protocol,
        "instructions": {
            **REVIEW_INSTRUCTIONS,
            "winner_values": list(REVIEW_INSTRUCTIONS["winner_values"]),
        },
        "items": public_items,
    }
    packet["packet_sha256"] = hash_payload(packet, "packet_sha256")
    key: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "reviewer_id": reviewer_id,
        "authority": authority,
        "protocol": secret_protocol,
        "packet_sha256": packet["packet_sha256"],
        "items": secret_items,
    }
    key["key_sha256"] = hash_payload(key, "key_sha256")
    return packet, key


def _validate_submission(
    packet: dict[str, Any],
    submission: dict[str, Any],
) -> list[str]:
    if not isinstance(packet, dict) or not isinstance(submission, dict):
        return ["packet and submission roots must be objects"]
    failures: list[str] = []
    if set(packet) != {
        "schema_version",
        "reviewer_id",
        "authority",
        "protocol",
        "instructions",
        "items",
        "packet_sha256",
    }:
        failures.append("packet fields do not match the schema")
    if set(submission) != {
        "schema_version",
        "reviewer_id",
        "authority",
        "packet_sha256",
        "decisions",
    }:
        failures.append("submission fields do not match the schema")
    if packet.get("schema_version") != SCHEMA_VERSION:
        failures.append("unsupported packet schema")
    if submission.get("schema_version") != SCHEMA_VERSION:
        failures.append("unsupported submission schema")
    if packet.get("packet_sha256") != hash_payload(packet, "packet_sha256"):
        failures.append("packet hash mismatch")
    if (
        not isinstance(packet.get("reviewer_id"), str)
        or not packet["reviewer_id"]
    ):
        failures.append("packet reviewer id does not match the schema")
    authority = packet.get("authority")
    authority_fields = {
        "replicate_report_sha256",
        "suite_id",
        "benchmark_definition_sha256",
        "evaluation_sha256s",
    }
    if not isinstance(authority, dict) or set(authority) != authority_fields:
        failures.append("packet authority fields do not match the schema")
    else:
        if any(
            not isinstance(authority.get(field), str)
            or SHA256_RE.fullmatch(authority[field]) is None
            for field in (
                "replicate_report_sha256",
                "benchmark_definition_sha256",
            )
        ) or not isinstance(authority.get("suite_id"), str) or not authority[
            "suite_id"
        ]:
            failures.append("packet authority content does not match the schema")
        evaluation_sha256s = authority.get("evaluation_sha256s")
        if (
            not isinstance(evaluation_sha256s, list)
            or not evaluation_sha256s
            or any(
                not isinstance(value, str) or SHA256_RE.fullmatch(value) is None
                for value in evaluation_sha256s
            )
            or len(evaluation_sha256s) != len(set(evaluation_sha256s))
        ):
            failures.append("packet evaluation authority does not match the schema")
    protocol = packet.get("protocol")
    if (
        not isinstance(protocol, dict)
        or set(protocol)
        != {"name", "sample_size", "blinding_key_sha256"}
        or protocol.get("name") != REVIEW_PROTOCOL
        or not _is_json_integer(protocol.get("sample_size"), minimum=1)
        or not isinstance(protocol.get("blinding_key_sha256"), str)
        or SHA256_RE.fullmatch(protocol["blinding_key_sha256"]) is None
    ):
        failures.append("packet protocol fields do not match the schema")
    if packet.get("instructions") != REVIEW_INSTRUCTIONS:
        failures.append("packet instructions do not match the schema")
    if submission.get("reviewer_id") != packet.get("reviewer_id"):
        failures.append("submission reviewer mismatch")
    if submission.get("packet_sha256") != packet.get("packet_sha256"):
        failures.append("submission packet mismatch")
    if submission.get("authority") != packet.get("authority"):
        failures.append("submission authority mismatch")
    packet_items = packet.get("items")
    if not isinstance(packet_items, list) or any(
        not isinstance(item, dict) for item in packet_items
    ):
        return [*failures, "packet items must be an array of objects"]
    expected_ids = [item.get("item_id") for item in packet_items]
    if any(not isinstance(item_id, str) or not item_id for item_id in expected_ids):
        failures.append("packet contains an invalid item id")
        return failures
    if len(set(expected_ids)) != len(expected_ids):
        failures.append("packet contains duplicate item ids")
    for item in packet_items:
        if set(item) != {
            "item_id",
            "input_text",
            "rubric",
            "output_a",
            "output_b",
        }:
            failures.append("packet item fields do not match the schema")
            continue
        rubric = item.get("rubric")
        if (
            REVIEW_ITEM_ID_RE.fullmatch(item["item_id"]) is None
            or not isinstance(rubric, list)
            or not rubric
            or any(not isinstance(value, str) or not value for value in rubric)
            or any(
                not isinstance(item.get(field), str) or not item[field]
                for field in ("input_text", "output_a", "output_b")
            )
        ):
            failures.append("packet item content does not match the schema")
    decisions = submission.get("decisions")
    if not isinstance(decisions, list):
        return [*failures, "submission decisions must be a list"]
    observed_ids: list[str] = []
    for decision in decisions:
        if not isinstance(decision, dict):
            failures.append("invalid decision")
            continue
        if set(decision) != {"item_id", "winner", "reason"}:
            failures.append("decision fields do not match the schema")
        item_id = decision.get("item_id")
        if not isinstance(item_id, str) or not item_id:
            failures.append("decision has an invalid item id")
            continue
        if item_id in observed_ids:
            failures.append(f"duplicate decision: {item_id}")
        observed_ids.append(item_id)
        winner = decision.get("winner")
        if not isinstance(winner, str) or winner not in {"A", "B", "tie"}:
            failures.append(f"{item_id}: invalid winner")
        reason = decision.get("reason")
        if not isinstance(reason, str) or len(reason.strip()) < 20:
            failures.append(f"{item_id}: reason is too short")
    if set(observed_ids) != set(expected_ids) or len(observed_ids) != len(
        expected_ids
    ):
        failures.append("submission does not cover the assigned items exactly")
    return failures


def validate_submission(
    packet: dict[str, Any],
    submission: dict[str, Any],
) -> list[str]:
    """Validate untrusted review input without leaking structural errors."""

    try:
        return _validate_submission(packet, submission)
    except (
        ArithmeticError,
        AttributeError,
        KeyError,
        RecursionError,
        TypeError,
        UnicodeError,
        ValueError,
    ) as exc:
        return [f"malformed human-review submission: {exc}"]


def _mapped_winner(decision: dict[str, Any], secret: dict[str, Any]) -> str:
    winner = decision["winner"]
    if winner == "tie":
        return "tie"
    return "win" if winner == secret["optimized_label"] else "loss"


def _validate_packet_key(
    packet: dict[str, Any],
    key: dict[str, Any],
    authority: dict[str, Any],
    records: dict[str, dict[str, Any]],
) -> None:
    if set(packet) != {
        "schema_version",
        "reviewer_id",
        "authority",
        "protocol",
        "instructions",
        "items",
        "packet_sha256",
    }:
        raise ValueError("Human-review packet fields are invalid.")
    if set(key) != {
        "schema_version",
        "reviewer_id",
        "authority",
        "protocol",
        "packet_sha256",
        "items",
        "key_sha256",
    }:
        raise ValueError("Human-review key fields are invalid.")
    if packet.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Unsupported human-review packet schema.")
    if key.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Unsupported human-review key schema.")
    if key.get("key_sha256") != hash_payload(key, "key_sha256"):
        raise ValueError("Human-review key hash mismatch.")
    if key.get("packet_sha256") != packet.get("packet_sha256"):
        raise ValueError("Human-review key does not match packet.")
    if packet.get("authority") != authority or key.get("authority") != authority:
        raise ValueError("Human-review packet/key authority binding mismatch.")
    if key.get("reviewer_id") != packet.get("reviewer_id"):
        raise ValueError("Human-review key reviewer mismatch.")
    reviewer_id = packet.get("reviewer_id")
    if not isinstance(reviewer_id, str) or not reviewer_id.strip():
        raise ValueError("Human-review reviewer id is invalid.")
    if packet.get("instructions") != REVIEW_INSTRUCTIONS:
        raise ValueError("Human-review packet is not an enforced blind review.")
    public_protocol = packet.get("protocol")
    secret_protocol = key.get("protocol")
    if not isinstance(public_protocol, dict) or not isinstance(
        secret_protocol,
        dict,
    ):
        raise ValueError("Human-review packet/key protocol binding mismatch.")
    if set(public_protocol) != {
        "name",
        "sample_size",
        "blinding_key_sha256",
    } or public_protocol.get("name") != REVIEW_PROTOCOL:
        raise ValueError("Human-review public protocol is invalid.")
    if set(secret_protocol) != {
        "name",
        "sample_size",
        "seed",
        "position_probe_count",
        "blinding_key",
    } or secret_protocol.get("name") != REVIEW_PROTOCOL:
        raise ValueError("Human-review protocol is invalid.")
    sample_size = secret_protocol.get("sample_size")
    seed = secret_protocol.get("seed")
    position_probe_count = secret_protocol.get("position_probe_count")
    blinding_key = secret_protocol.get("blinding_key")
    if (
        not _is_json_integer(sample_size, minimum=1)
        or not _is_json_integer(seed)
        or not _is_json_integer(
            position_probe_count,
            minimum=MINIMUM_POSITION_PROBES,
        )
        or position_probe_count > sample_size
        or not isinstance(blinding_key, str)
        or SHA256_RE.fullmatch(blinding_key) is None
    ):
        raise ValueError("Human-review protocol parameters are invalid.")
    sample_size = int(sample_size)
    seed = int(seed)
    position_probe_count = int(position_probe_count)
    if public_protocol.get("sample_size") != sample_size:
        raise ValueError("Human-review packet/key sample size mismatch.")
    if public_protocol.get("blinding_key_sha256") != hashlib.sha256(
        bytes.fromhex(blinding_key)
    ).hexdigest():
        raise ValueError("Human-review packet/key blinding commitment mismatch.")
    public_items = packet.get("items")
    secret_items = key.get("items")
    if not isinstance(public_items, list) or not isinstance(secret_items, list):
        raise ValueError("Human-review packet/key items must be arrays.")
    if any(not isinstance(item, dict) for item in [*public_items, *secret_items]):
        raise ValueError("Human-review packet/key item must be an object.")
    if any(
        not isinstance(item.get("item_id"), str)
        or REVIEW_ITEM_ID_RE.fullmatch(item["item_id"]) is None
        for item in [*public_items, *secret_items]
    ):
        raise ValueError("Human-review packet/key item id is invalid.")
    public = {item.get("item_id"): item for item in public_items}
    secrets = {item.get("item_id"): item for item in secret_items}
    if (
        len(public) != len(public_items)
        or len(secrets) != len(secret_items)
        or set(public) != set(secrets)
    ):
        raise ValueError("Human-review packet/key item sets do not match.")
    expected_public, expected_secret = _review_items(
        records,
        reviewer_id=reviewer_id,
        sample_size=sample_size,
        seed=seed,
        position_probe_count=position_probe_count,
        blinding_key=blinding_key,
    )
    if public_items != expected_public or secret_items != expected_secret:
        raise ValueError(
            "Human-review packet/key do not match the bound sampling and blind protocol."
        )
    for item_id, item in public.items():
        secret = secrets[item_id]
        if set(item) != {
            "item_id",
            "input_text",
            "rubric",
            "output_a",
            "output_b",
        }:
            raise ValueError(f"{item_id}: public item fields are invalid.")
        if set(secret) != {
            "item_id",
            "case_id",
            "domain",
            "case_sha256",
            "source_evaluation_sha256",
            "base_item_id",
            "position_probe",
            "optimized_label",
        }:
            raise ValueError(f"{item_id}: secret item fields are invalid.")
        base_id = secret.get("base_item_id")
        record = records.get(base_id)
        if record is None:
            raise ValueError(f"{item_id}: item is not bound to an evaluation.")
        expected_shared = {
            "case_id": record["case_id"],
            "domain": record["domain"],
            "case_sha256": record["case_sha256"],
            "source_evaluation_sha256": record["source_evaluation_sha256"],
        }
        if any(secret.get(field) != value for field, value in expected_shared.items()):
            raise ValueError(f"{item_id}: secret item evaluation binding mismatch.")
        if not isinstance(secret.get("position_probe"), bool):
            raise ValueError(f"{item_id}: secret probe marker is invalid.")
        if (
            item.get("input_text") != record["input_text"]
            or item.get("rubric") != record["rubric"]
        ):
            raise ValueError(f"{item_id}: public review contract mismatch.")
        optimized_label = secret.get("optimized_label")
        if not isinstance(optimized_label, str) or optimized_label not in {"A", "B"}:
            raise ValueError(f"{item_id}: invalid optimized label.")
        expected_a = (
            record["optimized_output"]
            if optimized_label == "A"
            else record["original_output"]
        )
        expected_b = (
            record["original_output"]
            if optimized_label == "A"
            else record["optimized_output"]
        )
        if item.get("output_a") != expected_a or item.get("output_b") != expected_b:
            raise ValueError(f"{item_id}: packet outputs do not match the evaluation.")
    by_base: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for secret in secrets.values():
        by_base[secret["base_item_id"]].append(secret)
    for base_id, items in by_base.items():
        bases = [item for item in items if not item["position_probe"]]
        probes = [item for item in items if item["position_probe"]]
        if len(bases) != 1 or len(probes) > 1:
            raise ValueError(f"{base_id}: invalid position-probe pairing.")
        if probes and probes[0]["optimized_label"] == bases[0]["optimized_label"]:
            raise ValueError(f"{base_id}: position probe does not reverse labels.")


def _human_evidence(e4_ready: bool) -> Evidence:
    return Evidence(
        level="E4" if e4_ready else "E3",
        status="verified_scoped",
        claim="verified_improvement",
        limitations=(
            "Evidence is valid only for the bound replicate report, evaluations, "
            "review packets, and recorded reviewers.",
            "Human review does not establish universal superiority or award equivalence.",
        ),
    )


def _reviewer_receipt(
    verifier: ReviewerSubmissionVerifier | None,
    *,
    reviewer_id: str,
    packet_sha256: str,
    submission_sha256: str,
) -> str | None:
    if verifier is None:
        return None
    try:
        receipt = verifier.verify(
            reviewer_id=reviewer_id,
            packet_sha256=packet_sha256,
            submission_sha256=submission_sha256,
        )
    except Exception as exc:
        raise ValueError("Reviewer submission receipt verification failed.") from exc
    if not isinstance(receipt, str) or SHA256_RE.fullmatch(receipt) is None:
        raise ValueError("Reviewer submission receipt is missing or invalid.")
    return receipt


def _source_bundle_sha256(
    *,
    replicate_report: dict[str, Any],
    evaluations: Sequence[dict[str, Any]],
    review_artifacts: Sequence[dict[str, Any]],
    adjudications: dict[str, str],
) -> str:
    return sha256_json(
        {
            "replicate_report_sha256": replicate_report["report_sha256"],
            "run_fingerprint_sha256s": sorted(
                item["run_fingerprint_sha256"]
                for item in replicate_report["replicates"]
            ),
            "evaluation_sha256s": sorted(
                evaluation["evaluation_sha256"] for evaluation in evaluations
            ),
            "review_artifacts": sorted(
                review_artifacts,
                key=lambda item: item["reviewer_id"],
            ),
            "adjudications": dict(sorted(adjudications.items())),
        }
    )


def aggregate_human_review(
    evaluations: Sequence[dict[str, Any]],
    packets: Sequence[dict[str, Any]],
    keys: Sequence[dict[str, Any]],
    submissions: Sequence[dict[str, Any]],
    *,
    replicate_report: dict[str, Any],
    run_directories: Sequence[Path],
    adjudications: dict[str, str] | None = None,
    model_receipt_verifier: ModelCallReceiptVerifier | None = None,
    reviewer_submission_verifier: ReviewerSubmissionVerifier | None = None,
) -> dict[str, Any]:
    if adjudications is None:
        adjudications = {}
    elif not isinstance(adjudications, dict) or any(
        not isinstance(item_id, str)
        or not item_id
        or not isinstance(outcome, str)
        or outcome not in {"win", "tie", "loss"}
        for item_id, outcome in adjudications.items()
    ):
        raise ValueError("Human-review adjudications are invalid.")
    else:
        adjudications = dict(adjudications)
    if not (len(packets) == len(keys) == len(submissions)):
        raise ValueError("Packets, keys, and submissions must have equal lengths.")
    authority, records = _authoritative_records(
        evaluations,
        replicate_report,
        run_directories,
        model_receipt_verifier=model_receipt_verifier,
    )

    votes: dict[str, list[str]] = defaultdict(list)
    base_raw_labels: Counter[str] = Counter()
    reviewer_decisions: dict[str, dict[str, str]] = defaultdict(dict)
    longer_selected = 0
    non_tie_selected = 0
    probe_consistent = 0
    probe_completed = 0
    probe_expected = 0
    probe_biased = 0
    probe_inconclusive = 0
    reviewer_ids: set[str] = set()
    reviewer_receipts: set[str] = set()
    blinding_keys: set[str] = set()
    blinding_key_commitments: set[str] = set()
    reviewer_protocol_results: list[dict[str, Any]] = []
    review_artifacts: list[dict[str, Any]] = []

    for packet, key, submission in zip(packets, keys, submissions, strict=True):
        failures = validate_submission(packet, submission)
        if failures:
            raise ValueError(f"Invalid human submission: {failures}")
        _validate_packet_key(packet, key, authority, records)
        blinding_key = key["protocol"]["blinding_key"]
        blinding_key_commitment = packet["protocol"]["blinding_key_sha256"]
        if blinding_key in blinding_keys:
            raise ValueError("Human-review blinding keys must be unique per packet.")
        if blinding_key_commitment in blinding_key_commitments:
            raise ValueError(
                "Human-review blinding-key commitments must be unique per packet."
            )
        blinding_keys.add(blinding_key)
        blinding_key_commitments.add(blinding_key_commitment)
        reviewer_id = packet.get("reviewer_id")
        if not isinstance(reviewer_id, str) or not reviewer_id.strip():
            raise ValueError("Human-review packet has an invalid reviewer id.")
        if reviewer_id in reviewer_ids:
            raise ValueError("Human-review reviewer ids must be unique.")
        reviewer_ids.add(reviewer_id)
        submission_sha256 = sha256_json(submission)
        reviewer_receipt = _reviewer_receipt(
            reviewer_submission_verifier,
            reviewer_id=reviewer_id,
            packet_sha256=packet["packet_sha256"],
            submission_sha256=submission_sha256,
        )
        if reviewer_receipt is not None:
            if reviewer_receipt in reviewer_receipts:
                raise ValueError("Reviewer submission receipts must be unique.")
            reviewer_receipts.add(reviewer_receipt)
        review_artifacts.append(
            {
                "reviewer_id": reviewer_id,
                "packet_sha256": packet["packet_sha256"],
                "key_sha256": key["key_sha256"],
                "submission_sha256": submission_sha256,
                "reviewer_receipt_sha256": reviewer_receipt,
                "reviewer_identity_verified": reviewer_receipt is not None,
            }
        )
        secrets = {item["item_id"]: item for item in key["items"]}
        public = {item["item_id"]: item for item in packet["items"]}
        pair_decisions: dict[str, dict[str, str]] = defaultdict(dict)
        reviewer_base_labels: Counter[str] = Counter()
        for decision in submission["decisions"]:
            item_id = decision["item_id"]
            secret = secrets[item_id]
            mapped = _mapped_winner(decision, secret)
            base_id = secret["base_item_id"]
            pair_decisions[base_id][
                "probe" if secret["position_probe"] else "base"
            ] = decision["winner"]
            if not secret["position_probe"]:
                base_raw_labels[decision["winner"]] += 1
                reviewer_base_labels[decision["winner"]] += 1
                if reviewer_id in reviewer_decisions[base_id]:
                    raise ValueError(
                        f"{reviewer_id}: duplicate base-case review for {base_id}."
                    )
                votes[base_id].append(mapped)
                reviewer_decisions[base_id][reviewer_id] = mapped
            if not secret["position_probe"] and decision["winner"] != "tie":
                item = public[item_id]
                selected = (
                    item["output_a"]
                    if decision["winner"] == "A"
                    else item["output_b"]
                )
                rejected = (
                    item["output_b"]
                    if decision["winner"] == "A"
                    else item["output_a"]
                )
                non_tie_selected += 1
                longer_selected += len(selected) > len(rejected)
        reviewer_expected = key["protocol"]["position_probe_count"]
        reviewer_completed = 0
        reviewer_consistent = 0
        reviewer_biased = 0
        reviewer_inconclusive = 0
        for pair in pair_decisions.values():
            if "probe" not in pair:
                continue
            reviewer_completed += 1
            if pair.get("base") == "tie" or pair["probe"] == "tie":
                reviewer_inconclusive += 1
            elif pair["base"] != pair["probe"]:
                reviewer_consistent += 1
            else:
                reviewer_biased += 1
        reviewer_passed = (
            reviewer_expected >= MINIMUM_POSITION_PROBES
            and reviewer_completed == reviewer_expected
            and reviewer_consistent == reviewer_expected
            and reviewer_biased == 0
            and reviewer_inconclusive == 0
            and reviewer_base_labels["A"] > 0
            and reviewer_base_labels["B"] > 0
        )
        reviewer_protocol_results.append(
            {
                "reviewer_id": reviewer_id,
                "expected_probe_pairs": reviewer_expected,
                "completed_probe_pairs": reviewer_completed,
                "consistent_probe_pairs": reviewer_consistent,
                "position_biased_probe_pairs": reviewer_biased,
                "inconclusive_probe_pairs": reviewer_inconclusive,
                "base_a_selections": reviewer_base_labels["A"],
                "base_b_selections": reviewer_base_labels["B"],
                "base_tie_selections": reviewer_base_labels["tie"],
                "passed": reviewer_passed,
            }
        )
        probe_expected += reviewer_expected
        probe_completed += reviewer_completed
        probe_consistent += reviewer_consistent
        probe_biased += reviewer_biased
        probe_inconclusive += reviewer_inconclusive

    consensus: dict[str, str] = {}
    unresolved: list[str] = []
    tied_item_ids: set[str] = set()
    adjudicated_item_ids: set[str] = set()
    for item_id, case_votes in votes.items():
        counts = Counter(case_votes)
        top = counts.most_common()
        if len(top) == 1 or (len(top) > 1 and top[0][1] > top[1][1]):
            consensus[item_id] = top[0][0]
        elif item_id in adjudications:
            tied_item_ids.add(item_id)
            adjudicated_item_ids.add(item_id)
            consensus[item_id] = adjudications[item_id]
        else:
            tied_item_ids.add(item_id)
            unresolved.append(item_id)
    unused_adjudications = set(adjudications) - tied_item_ids
    if unused_adjudications:
        raise ValueError(
            "Human-review adjudications contain unknown or non-tied case ids."
        )

    pair_matches = 0
    pair_total = 0
    for decisions in reviewer_decisions.values():
        for first, second in combinations(decisions.values(), 2):
            pair_total += 1
            pair_matches += first == second
    judge_matches = 0
    judge_total = 0
    for item_id, outcome in consensus.items():
        record = records[item_id]
        judge_total += 1
        judge_matches += outcome == record["outcome"]

    sorted_reviewers = sorted(reviewer_ids)
    case_coverage = []
    for item_id in sorted(votes):
        record = records[item_id]
        case_reviewers = sorted(reviewer_decisions[item_id])
        case_coverage.append(
            {
                "item_id": item_id,
                "case_id": record["case_id"],
                "domain": record["domain"],
                "case_sha256": record["case_sha256"],
                "source_evaluation_sha256": record[
                    "source_evaluation_sha256"
                ],
                "reviewer_ids": case_reviewers,
                "reviewer_count": len(case_reviewers),
            }
        )
    reviewed_base_cases = len(votes)
    fully_covered = sum(
        item["reviewer_ids"] == sorted_reviewers for item in case_coverage
    )
    all_reviewer_protocols_passed = bool(reviewer_protocol_results) and all(
        item["passed"] for item in reviewer_protocol_results
    )
    all_reviewer_receipts_verified = bool(reviewer_ids) and len(
        reviewer_receipts
    ) == len(reviewer_ids)
    human_improvement_confirmed = sum(
        outcome == "win"
        for item_id, outcome in consensus.items()
        if item_id not in adjudicated_item_ids
    ) > sum(
        outcome == "loss"
        for item_id, outcome in consensus.items()
        if item_id not in adjudicated_item_ids
    )
    e4_ready = (
        len(reviewer_ids) >= MINIMUM_E4_REVIEWERS
        and reviewed_base_cases >= MINIMUM_E4_CASES
        and fully_covered == reviewed_base_cases
        and not unresolved
        and not adjudicated_item_ids
        and all_reviewer_protocols_passed
        and all_reviewer_receipts_verified
        and human_improvement_confirmed
    )
    evidence = _human_evidence(e4_ready)
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "authority": authority,
        "reviewer_ids": sorted_reviewers,
        "reviewer_count": len(reviewer_ids),
        "reviewed_case_count": reviewed_base_cases,
        "fully_covered_case_count": fully_covered,
        "case_coverage": case_coverage,
        "review_artifacts": sorted(
            review_artifacts,
            key=lambda item: item["reviewer_id"],
        ),
        "reviewer_protocol_results": sorted(
            reviewer_protocol_results,
            key=lambda item: item["reviewer_id"],
        ),
        "source_bundle_sha256": _source_bundle_sha256(
            replicate_report=replicate_report,
            evaluations=evaluations,
            review_artifacts=review_artifacts,
            adjudications=adjudications,
        ),
        "unresolved_cases": sorted(unresolved),
        "adjudicated_cases": sorted(adjudicated_item_ids),
        "consensus": dict(sorted(consensus.items())),
        "pairwise_agreement": pair_matches / pair_total if pair_total else None,
        "judge_human_agreement": (
            judge_matches / judge_total if judge_total else None
        ),
        "base_position_a_selection_rate": (
            base_raw_labels["A"]
            / (base_raw_labels["A"] + base_raw_labels["B"])
            if base_raw_labels["A"] + base_raw_labels["B"]
            else None
        ),
        "position_probe_expected": probe_expected,
        "position_probe_completed": probe_completed,
        "position_probe_coverage": (
            probe_completed / probe_expected if probe_expected else None
        ),
        "position_probe_consistency": (
            probe_consistent / probe_completed if probe_completed else None
        ),
        "position_biased_probe_pairs": probe_biased,
        "position_inconclusive_probe_pairs": probe_inconclusive,
        "all_reviewer_protocols_passed": all_reviewer_protocols_passed,
        "all_reviewer_receipts_verified": all_reviewer_receipts_verified,
        "human_improvement_confirmed": human_improvement_confirmed,
        "longer_output_selection_rate": (
            longer_selected / non_tie_selected if non_tie_selected else None
        ),
        "e4_ready": e4_ready,
        "evidence": {
            "level": evidence.level,
            "status": evidence.status,
            "claim": evidence.claim,
            "limitations": list(evidence.limitations),
        },
    }
    report["human_review_sha256"] = hash_payload(
        report,
        "human_review_sha256",
    )
    failures = validate_human_review_report(
        report,
        replicate_report=replicate_report,
    )
    if failures:
        raise AssertionError(f"Generated human-review report is invalid: {failures}")
    return report


def _validate_human_review_report(
    report: Any,
    *,
    replicate_report: dict[str, Any] | None,
) -> list[str]:
    """Recompute E4 eligibility and verify its E3 authority binding."""
    if not isinstance(report, dict):
        return ["human-review report root must be an object"]
    failures: list[str] = []
    expected_fields = {
        "schema_version",
        "authority",
        "reviewer_ids",
        "reviewer_count",
        "reviewed_case_count",
        "fully_covered_case_count",
        "case_coverage",
        "review_artifacts",
        "reviewer_protocol_results",
        "source_bundle_sha256",
        "unresolved_cases",
        "adjudicated_cases",
        "consensus",
        "pairwise_agreement",
        "judge_human_agreement",
        "base_position_a_selection_rate",
        "position_probe_expected",
        "position_probe_completed",
        "position_probe_coverage",
        "position_probe_consistency",
        "position_biased_probe_pairs",
        "position_inconclusive_probe_pairs",
        "all_reviewer_protocols_passed",
        "all_reviewer_receipts_verified",
        "human_improvement_confirmed",
        "longer_output_selection_rate",
        "e4_ready",
        "evidence",
        "human_review_sha256",
    }
    if set(report) != expected_fields:
        failures.append("human-review report fields do not match the schema")
    if report.get("schema_version") != SCHEMA_VERSION:
        failures.append("unsupported human-review report schema")
    if report.get("human_review_sha256") != hash_payload(
        report,
        "human_review_sha256",
    ):
        failures.append("human-review report hash mismatch")
    source_bundle_sha256 = report.get("source_bundle_sha256")
    if (
        not isinstance(source_bundle_sha256, str)
        or SHA256_RE.fullmatch(source_bundle_sha256) is None
    ):
        failures.append("human-review source bundle hash is invalid")

    authority = report.get("authority")
    if not isinstance(authority, dict):
        failures.append("human-review authority must be an object")
        authority = {}
    required_authority = {
        "replicate_report_sha256",
        "suite_id",
        "benchmark_definition_sha256",
        "evaluation_sha256s",
    }
    if set(authority) != required_authority:
        failures.append("human-review authority fields are incomplete")
    evaluation_sha256s = authority.get("evaluation_sha256s")
    if (
        not isinstance(evaluation_sha256s, list)
        or not evaluation_sha256s
        or any(
            not isinstance(digest, str) or SHA256_RE.fullmatch(digest) is None
            for digest in evaluation_sha256s
        )
        or evaluation_sha256s != sorted(set(evaluation_sha256s))
    ):
        failures.append("human-review evaluation authority is invalid")

    allowed_by_domain: dict[str, set[str]] = defaultdict(set)
    report_cases: dict[tuple[str, str], str] = {}
    if replicate_report is None:
        failures.append("replicate report is required to validate human-review authority")
    else:
        authority_failures = validate_e3_claim(replicate_report)
        failures.extend(
            f"invalid E3 authority: {failure}" for failure in authority_failures
        )
        if not authority_failures:
            expected = _authority_from_report(
                replicate_report,
                evaluation_sha256s if isinstance(evaluation_sha256s, list) else [],
            )
            for field in (
                "replicate_report_sha256",
                "suite_id",
                "benchmark_definition_sha256",
            ):
                if authority.get(field) != expected[field]:
                    failures.append(f"human-review authority mismatch: {field}")
            allowed_evaluations = {
                digest
                for replicate in replicate_report["replicates"]
                for digest in replicate["evaluation_sha256"].values()
            }
            for replicate in replicate_report["replicates"]:
                for domain, digest in replicate["evaluation_sha256"].items():
                    allowed_by_domain[domain].add(digest)
            report_cases = {
                (case["domain"], case["case_id"]): case["case_sha256"]
                for case in replicate_report["cases"]
            }
            if isinstance(evaluation_sha256s, list) and not set(
                evaluation_sha256s
            ).issubset(allowed_evaluations):
                failures.append(
                    "human-review authority contains an unbound evaluation"
                )

    reviewer_ids = report.get("reviewer_ids")
    if (
        not isinstance(reviewer_ids, list)
        or any(
            not isinstance(reviewer_id, str) or not reviewer_id.strip()
            for reviewer_id in reviewer_ids
        )
        or reviewer_ids != sorted(set(reviewer_ids))
    ):
        failures.append("human-review reviewer ids are invalid")
        reviewer_ids = []
    if not _is_json_integer(report.get("reviewer_count"), minimum=0):
        failures.append("human-review reviewer count is invalid")
    if report.get("reviewer_count") != len(reviewer_ids):
        failures.append("human-review reviewer count mismatch")

    consensus = report.get("consensus")
    unresolved = report.get("unresolved_cases")
    adjudicated = report.get("adjudicated_cases")
    coverage = report.get("case_coverage")
    if not isinstance(consensus, dict) or any(
        not isinstance(outcome, str) or outcome not in {"win", "tie", "loss"}
        for outcome in consensus.values()
    ):
        failures.append("human-review consensus is invalid")
        consensus = {}
    if (
        not isinstance(unresolved, list)
        or any(not isinstance(item, str) or not item for item in unresolved)
        or unresolved != sorted(set(unresolved))
    ):
        failures.append("human-review unresolved cases are invalid")
        unresolved = []
    if (
        not isinstance(adjudicated, list)
        or any(not isinstance(item, str) or not item for item in adjudicated)
        or adjudicated != sorted(set(adjudicated))
    ):
        failures.append("human-review adjudicated cases are invalid")
        adjudicated = []
    if not set(adjudicated).issubset(consensus):
        failures.append("human-review adjudicated cases are not resolved cases")
    if set(adjudicated) & set(unresolved):
        failures.append("human-review adjudicated and unresolved case sets overlap")
    if not isinstance(coverage, list) or any(
        not isinstance(item, dict) for item in coverage
    ):
        failures.append("human-review case coverage must be an array of objects")
        coverage = []
    coverage_by_id: dict[str, dict[str, Any]] = {}
    for item in coverage:
        item_id = item.get("item_id")
        if not isinstance(item_id, str) or not item_id:
            failures.append("human-review case coverage has an invalid item id")
            continue
        if item_id in coverage_by_id:
            failures.append("human-review case coverage contains duplicate item ids")
            continue
        coverage_by_id[item_id] = item
    if set(coverage_by_id) != set(consensus) | set(unresolved):
        failures.append("human-review case coverage does not match reviewed cases")
    if set(consensus) & set(unresolved):
        failures.append("human-review resolved and unresolved case sets overlap")
    fully_covered = 0
    seen_case_bindings: set[tuple[str, str, str, str]] = set()
    for item_id, item in coverage_by_id.items():
        if set(item) != {
            "item_id",
            "case_id",
            "domain",
            "case_sha256",
            "source_evaluation_sha256",
            "reviewer_ids",
            "reviewer_count",
        }:
            failures.append(f"{item_id}: human-review case coverage fields are invalid")
        item_reviewers = item.get("reviewer_ids")
        if (
            not isinstance(item_id, str)
            or not item_id.startswith("case:")
            or not isinstance(item_reviewers, list)
            or any(not isinstance(reviewer, str) for reviewer in item_reviewers)
            or item_reviewers != sorted(set(item_reviewers))
            or any(reviewer not in reviewer_ids for reviewer in item_reviewers)
            or not _is_json_integer(item.get("reviewer_count"), minimum=0)
            or item.get("reviewer_count") != len(item_reviewers)
            or not all(
                isinstance(item.get(field), str) and item.get(field)
                for field in ("case_id", "domain")
            )
            or any(
                not isinstance(item.get(field), str)
                or SHA256_RE.fullmatch(item[field]) is None
                for field in ("case_sha256", "source_evaluation_sha256")
            )
        ):
            failures.append(f"{item_id}: invalid human-review case coverage")
            continue
        if item_id != f"case:{item['case_sha256']}":
            failures.append(f"{item_id}: item id does not match the case hash")
        case_binding = (
            item["domain"],
            item["case_id"],
            item["case_sha256"],
            item["source_evaluation_sha256"],
        )
        if case_binding in seen_case_bindings:
            failures.append(f"{item_id}: duplicate human-review case binding")
        seen_case_bindings.add(case_binding)
        if isinstance(evaluation_sha256s, list) and item[
            "source_evaluation_sha256"
        ] not in evaluation_sha256s:
            failures.append(f"{item_id}: case is not bound to an evaluation")
        if report_cases and report_cases.get(
            (item["domain"], item["case_id"])
        ) != item["case_sha256"]:
            failures.append(f"{item_id}: case is not bound to the replicate report")
        if allowed_by_domain and item["source_evaluation_sha256"] not in (
            allowed_by_domain.get(item["domain"], set())
        ):
            failures.append(
                f"{item_id}: evaluation is not bound to the case domain"
            )
        if item_reviewers == reviewer_ids:
            fully_covered += 1
    reviewed_case_count = len(coverage)
    if not _is_json_integer(report.get("reviewed_case_count"), minimum=0):
        failures.append("human-review reviewed case count is invalid")
    if not _is_json_integer(
        report.get("fully_covered_case_count"),
        minimum=0,
    ):
        failures.append("human-review full-coverage count is invalid")
    if report.get("reviewed_case_count") != reviewed_case_count:
        failures.append("human-review reviewed case count mismatch")
    if report.get("fully_covered_case_count") != fully_covered:
        failures.append("human-review full-coverage count mismatch")

    review_artifacts = report.get("review_artifacts")
    if not isinstance(review_artifacts, list) or any(
        not isinstance(item, dict) for item in review_artifacts
    ):
        failures.append("human-review artifact bindings must be an array")
        review_artifacts = []
    artifact_reviewers = [item.get("reviewer_id") for item in review_artifacts]
    if any(not isinstance(item, str) for item in artifact_reviewers) or sorted(
        artifact_reviewers
    ) != reviewer_ids:
        failures.append("human-review artifact reviewer set mismatch")
    for item in review_artifacts:
        if set(item) != {
            "reviewer_id",
            "packet_sha256",
            "key_sha256",
            "submission_sha256",
            "reviewer_receipt_sha256",
            "reviewer_identity_verified",
        }:
            failures.append("human-review artifact fields are invalid")
        if any(
            not isinstance(item.get(field), str)
            or SHA256_RE.fullmatch(item[field]) is None
            for field in ("packet_sha256", "key_sha256", "submission_sha256")
        ):
            failures.append("human-review artifact hash is invalid")
        receipt = item.get("reviewer_receipt_sha256")
        if receipt is not None and (
            not isinstance(receipt, str) or SHA256_RE.fullmatch(receipt) is None
        ):
            failures.append("human-review reviewer receipt is invalid")
        verified = isinstance(receipt, str) and SHA256_RE.fullmatch(receipt) is not None
        if item.get("reviewer_identity_verified") is not verified:
            failures.append("human-review reviewer receipt status mismatch")
    for field in ("packet_sha256", "key_sha256", "submission_sha256"):
        values = [
            item.get(field) for item in review_artifacts if isinstance(item, dict)
        ]
        if all(isinstance(value, str) for value in values) and len(values) != len(
            set(values)
        ):
            failures.append(f"human-review artifact hashes are not unique: {field}")

    receipt_values = [
        item.get("reviewer_receipt_sha256") for item in review_artifacts
    ]
    verified_receipts = [
        value
        for value in receipt_values
        if isinstance(value, str) and SHA256_RE.fullmatch(value) is not None
    ]
    if len(verified_receipts) != len(set(verified_receipts)):
        failures.append("human-review reviewer receipt hashes are not unique")
    all_reviewer_receipts_verified = bool(reviewer_ids) and len(
        verified_receipts
    ) == len(reviewer_ids)
    if (
        report.get("all_reviewer_receipts_verified")
        is not all_reviewer_receipts_verified
    ):
        failures.append("human-review reviewer receipt aggregate mismatch")

    protocol_results = report.get("reviewer_protocol_results")
    protocol_fields = {
        "reviewer_id",
        "expected_probe_pairs",
        "completed_probe_pairs",
        "consistent_probe_pairs",
        "position_biased_probe_pairs",
        "inconclusive_probe_pairs",
        "base_a_selections",
        "base_b_selections",
        "base_tie_selections",
        "passed",
    }
    if not isinstance(protocol_results, list) or any(
        not isinstance(item, dict) or set(item) != protocol_fields
        for item in protocol_results
    ):
        failures.append("human-review reviewer protocol results are invalid")
        protocol_results = []
    if [item.get("reviewer_id") for item in protocol_results] != reviewer_ids:
        failures.append("human-review reviewer protocol set mismatch")
    count_fields = protocol_fields - {"reviewer_id", "passed"}
    for item in protocol_results:
        if any(
            not _is_json_integer(item.get(field), minimum=0)
            for field in count_fields
        ) or not _is_json_integer(
            item.get("expected_probe_pairs"),
            minimum=MINIMUM_POSITION_PROBES,
        ) or not isinstance(item.get("passed"), bool):
            failures.append("human-review reviewer protocol counts are invalid")
            continue
        expected_passed = (
            item["expected_probe_pairs"] >= MINIMUM_POSITION_PROBES
            and item["completed_probe_pairs"] == item["expected_probe_pairs"]
            and item["consistent_probe_pairs"] == item["expected_probe_pairs"]
            and item["position_biased_probe_pairs"] == 0
            and item["inconclusive_probe_pairs"] == 0
            and item["base_a_selections"] > 0
            and item["base_b_selections"] > 0
        )
        if item["passed"] is not expected_passed:
            failures.append("human-review reviewer protocol status mismatch")
    expected_probe_pairs = sum(
        item.get("expected_probe_pairs", 0) for item in protocol_results
    )
    completed_probe_pairs = sum(
        item.get("completed_probe_pairs", 0) for item in protocol_results
    )
    consistent_probe_pairs = sum(
        item.get("consistent_probe_pairs", 0) for item in protocol_results
    )
    biased_probe_pairs = sum(
        item.get("position_biased_probe_pairs", 0) for item in protocol_results
    )
    inconclusive_probe_pairs = sum(
        item.get("inconclusive_probe_pairs", 0) for item in protocol_results
    )
    for field, expected in (
        ("position_probe_expected", expected_probe_pairs),
        ("position_probe_completed", completed_probe_pairs),
        ("position_biased_probe_pairs", biased_probe_pairs),
        ("position_inconclusive_probe_pairs", inconclusive_probe_pairs),
    ):
        if not _is_json_integer(report.get(field), minimum=0):
            failures.append(f"human-review probe aggregate is invalid: {field}")
        if report.get(field) != expected:
            failures.append(f"human-review probe aggregate mismatch: {field}")
    expected_coverage = (
        completed_probe_pairs / expected_probe_pairs if expected_probe_pairs else None
    )
    expected_consistency = (
        consistent_probe_pairs / completed_probe_pairs
        if completed_probe_pairs
        else None
    )
    if report.get("position_probe_coverage") != expected_coverage:
        failures.append("human-review position probe coverage mismatch")
    if report.get("position_probe_consistency") != expected_consistency:
        failures.append("human-review position probe consistency mismatch")
    all_reviewer_protocols_passed = bool(protocol_results) and all(
        item.get("passed") is True for item in protocol_results
    )
    if report.get("all_reviewer_protocols_passed") is not all_reviewer_protocols_passed:
        failures.append("human-review protocol aggregate mismatch")
    human_improvement_confirmed = sum(
        outcome == "win"
        for item_id, outcome in consensus.items()
        if item_id not in adjudicated
    ) > sum(
        outcome == "loss"
        for item_id, outcome in consensus.items()
        if item_id not in adjudicated
    )
    if report.get("human_improvement_confirmed") is not human_improvement_confirmed:
        failures.append("human-review improvement status mismatch")

    expected_e4 = (
        len(reviewer_ids) >= MINIMUM_E4_REVIEWERS
        and reviewed_case_count >= MINIMUM_E4_CASES
        and fully_covered == reviewed_case_count
        and not unresolved
        and not adjudicated
        and all_reviewer_protocols_passed
        and all_reviewer_receipts_verified
        and human_improvement_confirmed
    )
    if report.get("e4_ready") is not expected_e4:
        failures.append("human-review E4 status does not match coverage")
    expected_evidence = _human_evidence(expected_e4)
    evidence = report.get("evidence")
    expected_evidence_payload = {
        "level": expected_evidence.level,
        "status": expected_evidence.status,
        "claim": expected_evidence.claim,
        "limitations": list(expected_evidence.limitations),
    }
    if evidence != expected_evidence_payload:
        failures.append("human-review evidence does not match authority and coverage")
    for field in (
        "pairwise_agreement",
        "judge_human_agreement",
        "base_position_a_selection_rate",
        "position_probe_coverage",
        "position_probe_consistency",
        "longer_output_selection_rate",
    ):
        value = report.get(field)
        if value is not None and (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not 0.0 <= value <= 1.0
        ):
            failures.append(f"human-review metric is invalid: {field}")
    return failures


def validate_human_review_report(
    report: Any,
    *,
    replicate_report: dict[str, Any] | None,
) -> list[str]:
    """Validate untrusted E4 report input without leaking structural errors."""
    try:
        return _validate_human_review_report(
            report,
            replicate_report=replicate_report,
        )
    except (
        ArithmeticError,
        AttributeError,
        KeyError,
        RecursionError,
        TypeError,
        UnicodeError,
        ValueError,
    ) as exc:
        return [f"malformed human-review report: {exc}"]


def validate_human_review_authority(
    report: Any,
    plan: Any,
    *,
    root: Path,
    model_receipt_verifier: ModelCallReceiptVerifier | None = None,
    reviewer_submission_verifier: ReviewerSubmissionVerifier | None = None,
) -> list[str]:
    """Reload every review source and reproduce the E4 report exactly."""
    try:
        sources = load_human_review_plan(plan, root=root)
    except (
        ArithmeticError,
        AttributeError,
        OSError,
        RecursionError,
        TypeError,
        UnicodeError,
        ValueError,
    ) as exc:
        return [f"human-review source plan is invalid: {exc}"]
    failures = validate_human_review_report(
        report,
        replicate_report=sources["replicate_report"],
    )
    failures.extend(
        f"invalid E3 source authority: {failure}"
        for failure in validate_e3_authority(
            sources["replicate_report"],
            sources["run_directories"],
            receipt_verifier=model_receipt_verifier,
        )
    )
    if failures:
        return failures
    try:
        rebuilt = aggregate_human_review(
            sources["evaluations"],
            sources["packets"],
            sources["keys"],
            sources["submissions"],
            replicate_report=sources["replicate_report"],
            run_directories=sources["run_directories"],
            adjudications=sources["adjudications"],
            model_receipt_verifier=model_receipt_verifier,
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
        return [f"human-review source bundle is invalid: {exc}"]
    if rebuilt != report:
        failures.append("human-review report does not match its source plan")
    return failures
