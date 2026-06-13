"""Blind human-review packets, bias probes, adjudication, and E4 evidence."""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from itertools import combinations
from typing import Any, Sequence

from .evidence import LEVEL_ORDER, infer_evidence
from .evaluation import validate_evaluation
from .hashing import hash_payload


def _optimized_is_a(reviewer_id: str, item_id: str, seed: int) -> bool:
    digest = hashlib.sha256(
        f"{seed}:{reviewer_id}:{item_id}".encode("utf-8")
    ).digest()
    return digest[0] % 2 == 0


def create_reviewer_packet(
    evaluations: Sequence[dict[str, Any]],
    *,
    reviewer_id: str,
    sample_size: int = 24,
    seed: int = 0,
    position_probe_count: int = 2,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not reviewer_id.strip():
        raise ValueError("reviewer_id must not be empty.")
    records: list[dict[str, Any]] = []
    for evaluation in evaluations:
        failures = validate_evaluation(evaluation)
        if failures:
            raise ValueError(f"Invalid evaluation supplied to human review: {failures}")
        records.extend(evaluation["records"])
    if sample_size < 1 or sample_size > len(records):
        raise ValueError("sample_size must fit the available evaluation records.")

    ordered = sorted(
        records,
        key=lambda record: hashlib.sha256(
            f"{seed}:{record['domain']}:{record['difficulty']}:{record['case_id']}".encode(
                "utf-8"
            )
        ).hexdigest(),
    )
    selected = ordered[:sample_size]
    public_items: list[dict[str, Any]] = []
    secret_items: list[dict[str, Any]] = []

    def add_item(record: dict[str, Any], item_id: str, optimized_is_a: bool, probe: bool) -> None:
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
        public_items.append(
            {
                "item_id": item_id,
                "case_id": record["case_id"],
                "domain": record["domain"],
                "difficulty": record["difficulty"],
                "rubric": record["rubric"],
                "output_a": output_a,
                "output_b": output_b,
                "position_probe": probe,
            }
        )
        secret_items.append(
            {
                "item_id": item_id,
                "case_id": record["case_id"],
                "optimized_label": "A" if optimized_is_a else "B",
                "position_probe": probe,
            }
        )

    for record in selected:
        add_item(
            record,
            record["case_id"],
            _optimized_is_a(reviewer_id, record["case_id"], seed),
            False,
        )
    for record in selected[: min(position_probe_count, len(selected))]:
        base_label = next(
            item["optimized_label"]
            for item in secret_items
            if item["item_id"] == record["case_id"]
        )
        add_item(
            record,
            f"{record['case_id']}::position_probe",
            base_label != "A",
            True,
        )

    packet: dict[str, Any] = {
        "schema_version": "1.0.0",
        "reviewer_id": reviewer_id,
        "instructions": {
            "winner_values": ["A", "B", "tie"],
            "blind": True,
            "minimum_reason_characters": 20,
        },
        "items": public_items,
    }
    packet["packet_sha256"] = hash_payload(packet, "packet_sha256")
    key: dict[str, Any] = {
        "schema_version": "1.0.0",
        "reviewer_id": reviewer_id,
        "packet_sha256": packet["packet_sha256"],
        "items": secret_items,
    }
    key["key_sha256"] = hash_payload(key, "key_sha256")
    return packet, key


def validate_submission(
    packet: dict[str, Any],
    submission: dict[str, Any],
) -> list[str]:
    failures: list[str] = []
    if packet.get("packet_sha256") != hash_payload(packet, "packet_sha256"):
        failures.append("packet hash mismatch")
    if submission.get("reviewer_id") != packet.get("reviewer_id"):
        failures.append("submission reviewer mismatch")
    if submission.get("packet_sha256") != packet.get("packet_sha256"):
        failures.append("submission packet mismatch")
    expected_ids = {item["item_id"] for item in packet.get("items", [])}
    decisions = submission.get("decisions")
    if not isinstance(decisions, list):
        return [*failures, "submission decisions must be a list"]
    observed_ids: set[str] = set()
    for decision in decisions:
        if not isinstance(decision, dict):
            failures.append("invalid decision")
            continue
        item_id = decision.get("item_id")
        if item_id in observed_ids:
            failures.append(f"duplicate decision: {item_id}")
        observed_ids.add(item_id)
        if decision.get("winner") not in {"A", "B", "tie"}:
            failures.append(f"{item_id}: invalid winner")
        reason = decision.get("reason")
        if not isinstance(reason, str) or len(reason.strip()) < 20:
            failures.append(f"{item_id}: reason is too short")
    if observed_ids != expected_ids:
        failures.append("submission does not cover the assigned items exactly")
    return failures


def _mapped_winner(decision: dict[str, Any], secret: dict[str, Any]) -> str:
    winner = decision["winner"]
    if winner == "tie":
        return "tie"
    return "win" if winner == secret["optimized_label"] else "loss"


def aggregate_human_review(
    evaluations: Sequence[dict[str, Any]],
    packets: Sequence[dict[str, Any]],
    keys: Sequence[dict[str, Any]],
    submissions: Sequence[dict[str, Any]],
    *,
    adjudications: dict[str, str] | None = None,
) -> dict[str, Any]:
    adjudications = adjudications or {}
    if not (len(packets) == len(keys) == len(submissions)):
        raise ValueError("Packets, keys, and submissions must have equal lengths.")
    evaluation_by_case: dict[str, dict[str, Any]] = {}
    base_e3 = True
    base_gate = True
    for evaluation in evaluations:
        failures = validate_evaluation(evaluation)
        if failures:
            raise ValueError(f"Invalid evaluation: {failures}")
        base_e3 = base_e3 and LEVEL_ORDER[evaluation["evidence"]["level"]] >= 3
        base_gate = base_gate and evaluation["gate_passed"] is True
        for record in evaluation["records"]:
            evaluation_by_case[record["case_id"]] = record

    votes: dict[str, list[str]] = defaultdict(list)
    raw_labels: Counter[str] = Counter()
    reviewer_decisions: dict[str, dict[str, str]] = defaultdict(dict)
    longer_selected = 0
    non_tie_selected = 0
    probe_consistent = 0
    probe_total = 0
    reviewer_ids: set[str] = set()

    for packet, key, submission in zip(packets, keys, submissions):
        failures = validate_submission(packet, submission)
        if failures:
            raise ValueError(f"Invalid human submission: {failures}")
        if key.get("key_sha256") != hash_payload(key, "key_sha256"):
            raise ValueError("Human review key hash mismatch.")
        if key.get("packet_sha256") != packet.get("packet_sha256"):
            raise ValueError("Human review key does not match packet.")
        reviewer_id = str(packet["reviewer_id"])
        reviewer_ids.add(reviewer_id)
        secrets = {item["item_id"]: item for item in key["items"]}
        public = {item["item_id"]: item for item in packet["items"]}
        mapped_by_item: dict[str, str] = {}
        for decision in submission["decisions"]:
            item_id = decision["item_id"]
            secret = secrets[item_id]
            mapped = _mapped_winner(decision, secret)
            mapped_by_item[item_id] = mapped
            raw_labels[decision["winner"]] += 1
            if not secret["position_probe"]:
                votes[secret["case_id"]].append(mapped)
                reviewer_decisions[secret["case_id"]][reviewer_id] = mapped
            if decision["winner"] != "tie":
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
        for item_id, mapped in mapped_by_item.items():
            if not item_id.endswith("::position_probe"):
                continue
            base_id = item_id.removesuffix("::position_probe")
            if base_id in mapped_by_item:
                probe_total += 1
                probe_consistent += mapped == mapped_by_item[base_id]

    consensus: dict[str, str] = {}
    unresolved: list[str] = []
    for case_id, case_votes in votes.items():
        counts = Counter(case_votes)
        top = counts.most_common()
        if len(top) == 1 or (len(top) > 1 and top[0][1] > top[1][1]):
            consensus[case_id] = top[0][0]
        elif adjudications.get(case_id) in {"win", "tie", "loss"}:
            consensus[case_id] = adjudications[case_id]
        else:
            unresolved.append(case_id)

    pair_matches = 0
    pair_total = 0
    for decisions in reviewer_decisions.values():
        for first, second in combinations(decisions.values(), 2):
            pair_total += 1
            pair_matches += first == second
    judge_matches = 0
    judge_total = 0
    for case_id, outcome in consensus.items():
        record = evaluation_by_case.get(case_id)
        if record is not None:
            judge_total += 1
            judge_matches += outcome == record["outcome"]

    reviewed_base_cases = len(consensus)
    e4_ready = (
        base_e3
        and base_gate
        and len(reviewer_ids) >= 3
        and reviewed_base_cases >= 24
        and not unresolved
    )
    evidence = infer_evidence(
        deterministic_checks_passed=True,
        matched_cases=max(reviewed_base_cases, 20),
        comparative_improvement_passed=base_gate,
        repeated_or_cross_model=base_e3,
        expert_reviewers=len(reviewer_ids) if e4_ready else 0,
    )
    report: dict[str, Any] = {
        "schema_version": "1.0.0",
        "reviewer_count": len(reviewer_ids),
        "reviewed_case_count": reviewed_base_cases,
        "unresolved_cases": sorted(unresolved),
        "consensus": consensus,
        "pairwise_agreement": (
            pair_matches / pair_total if pair_total else None
        ),
        "judge_human_agreement": (
            judge_matches / judge_total if judge_total else None
        ),
        "position_a_selection_rate": (
            raw_labels["A"] / (raw_labels["A"] + raw_labels["B"])
            if raw_labels["A"] + raw_labels["B"]
            else None
        ),
        "position_probe_consistency": (
            probe_consistent / probe_total if probe_total else None
        ),
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
    return report
