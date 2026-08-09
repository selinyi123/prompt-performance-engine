from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from prompt_performance_engine.contracts import PACKAGE_ROOT
from prompt_performance_engine.frontier_contracts import (
    FRONTIER_CONTRACT_SCHEMA_VERSION,
)
from prompt_performance_engine.frontier_io import canonical_authority_bytes
from prompt_performance_engine.frontier_replay import (
    ARTIFACT_FIELDS,
    EVIDENCE_ENVELOPE_FIELDS,
    EVIDENCE_ENVELOPE_KINDS,
    EVIDENCE_PAYLOAD_FIELDS,
    ENVIRONMENT_FIELDS,
    OPERATOR_FIELDS,
    REPLAY_REPORT_FIELDS,
    REPRODUCTION_PLAN_FIELDS,
    REPRODUCTION_SET_FIELDS,
    TOP_TIER_REQUIRED_ARTIFACT_KINDS,
    build_frontier_reproduction_set,
    frontier_input_commitments_sha256,
    frontier_evidence_semantic_context_sha256,
    frontier_replay_context_sha256,
    frontier_source_artifacts_sha256,
    run_frontier_offline_replay,
    validate_frontier_replay_report,
    validate_frontier_evidence_envelope,
    validate_frontier_reproduction_plan,
    validate_frontier_reproduction_set,
)
from prompt_performance_engine.frontier_reporting import (
    build_frontier_report,
    frontier_comparison_context_sha256,
    frontier_gate_context_sha256,
)
from prompt_performance_engine.hashing import hash_payload, sha256_json
from prompt_performance_engine.frontier_host import (
    build_frontier_execution_bundle,
    validate_frontier_execution_bundle,
)
from tests.test_frontier_preflight import build_fixture as build_campaign_fixture
from tests.test_frontier_reporting import (
    CLAIM_VALIDATION_TIMESTAMP,
    ClaimVerifier,
    RELEASE_POLICY,
    ReportVerifier,
    claim_inventory_fixture,
    report_input_fixture,
)


def bytes_sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class ReplayVerifier:
    def __init__(self):
        self.calls = 0

    def verify(
        self,
        *,
        operator_id: str,
        machine_id: str,
        authority_id: str,
        attestation_id: str,
        context_sha256: str,
    ) -> str:
        self.calls += 1
        return sha256_json(
            {
                "operator_id": operator_id,
                "machine_id": machine_id,
                "authority_id": authority_id,
                "attestation_id": attestation_id,
                "context_sha256": context_sha256,
            }
        )


class RaisingReplayVerifier:
    def verify(self, **kwargs):
        raise RuntimeError("SEALED-REPLAY-CONTENT")


class EvidenceSemanticVerifier:
    def verify(
        self,
        *,
        artifact_kind: str,
        authority_id: str,
        context_sha256: str,
    ) -> str:
        return sha256_json(
            {
                "artifact_kind": artifact_kind,
                "authority_id": authority_id,
                "context_sha256": context_sha256,
            }
        )


class RejectingEvidenceSemanticVerifier:
    def verify(self, **kwargs) -> str:
        return "f" * 64


def write_canonical(path: Path, value: dict) -> bytes:
    raw = canonical_authority_bytes(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return raw


def preflight_fixture(
    *,
    valid: bool = True,
    campaign_sha256: str = "1" * 64,
    policy_sha256: str = "2" * 64,
    commitment_sha256: str = "3" * 64,
    analysis_sha256: str = "4" * 64,
) -> dict:
    if not valid:
        return {
            "schema_version": "1.0.0",
            "report_id": "legacy-readiness",
            "claim_ceiling": "stable_v1",
        }
    report = {
        "schema_version": FRONTIER_CONTRACT_SCHEMA_VERSION,
        "passed": True,
        "failures": [],
        "campaign_sha256": campaign_sha256,
        "policy_sha256": policy_sha256,
        "commitment_sha256": commitment_sha256,
        "analysis_sha256": analysis_sha256,
        "timestamp": "2026-07-21T00:00:00Z",
        "evidence_expires_at": "2027-07-01T00:00:00Z",
        "clock_attestation_receipt_sha256": "8" * 64,
        "owner_attestation_receipt_sha256": "5" * 64,
        "custodian_attestation_receipt_sha256": "6" * 64,
        "capability_receipts_sha256": "7" * 64,
        "preflight_sha256": "0" * 64,
    }
    report["preflight_sha256"] = hash_payload(report, "preflight_sha256")
    return report


def evidence_envelope(
    artifact_kind: str,
    *,
    campaign_sha256: str,
    policy_sha256: str,
    commitment_sha256: str,
    source_artifact_sha256s: list[str],
    semantic_verifier: EvidenceSemanticVerifier,
) -> dict:
    payload = {
        "subject_ids": [f"{artifact_kind}-subject"],
        "evaluated_case_count": 1,
        "passed_case_count": 1,
        "failed_case_count": 0,
        "metric_ids": [f"{artifact_kind}-metric"],
        "record_count": 1,
        "records_sha256": sha256_json({"records": artifact_kind}),
        "source_artifact_sha256s": list(source_artifact_sha256s),
    }
    envelope = {
        "schema_version": FRONTIER_CONTRACT_SCHEMA_VERSION,
        "artifact_kind": artifact_kind,
        "campaign_sha256": campaign_sha256,
        "policy_sha256": policy_sha256,
        "commitment_sha256": commitment_sha256,
        "report_id": "frontier-report-1",
        "generated_at": "2026-07-21T00:30:00Z",
        "outcome": "passed",
        "failures": [],
        "payload": payload,
        "payload_sha256": sha256_json(payload),
        "semantic_authority_id": f"{artifact_kind}-semantic-authority",
        "semantic_receipt_sha256": "0" * 64,
        "evidence_sha256": "0" * 64,
    }
    envelope["semantic_receipt_sha256"] = semantic_verifier.verify(
        artifact_kind=artifact_kind,
        authority_id=envelope["semantic_authority_id"],
        context_sha256=frontier_evidence_semantic_context_sha256(envelope),
    )
    envelope["evidence_sha256"] = hash_payload(envelope, "evidence_sha256")
    return envelope


def synthetic_all_system_execution_bundle(
    campaign_plan: dict,
    preflight: dict,
) -> dict:
    """Create deterministic contract fixtures; production uses the execution host."""

    models = {item["model_id"]: item for item in campaign_plan["models"]}
    routes = list(campaign_plan["case_routes"])
    executions = []
    for system_index, system in enumerate(campaign_plan["systems"]):
        budget = next(
            item
            for item in campaign_plan["budgets"]
            if item["system_id"] == system["system_id"]
        )
        slots = []
        slot_components = {}
        for component in budget["components"]:
            for unit_index in range(component["unit_count"]):
                route = routes[unit_index % len(routes)]
                model_id = component["model_ids"][
                    unit_index % len(component["model_ids"])
                ]
                for retry_index in range(component["attempts_per_unit"]):
                    slot_id = (
                        f"{system['system_id']}-{component['component_id']}-"
                        f"{unit_index}-{retry_index}"
                    )
                    slot = {
                        "slot_id": slot_id,
                        "component_id": component["component_id"],
                        "unit_id": (
                            f"{system['system_id']}-{component['component_id']}-"
                            f"unit-{unit_index}"
                        ),
                        "case_id": route["case_id"],
                        "replicate_index": unit_index // len(routes),
                        "retry_index": retry_index,
                        "model_id": model_id,
                        "purpose": component["purposes"][0],
                        "route": route["route"],
                        "route_authority_id": route["authority_id"],
                        "system_prompt_sha256": sha256_json(
                            {"text": f"system:{system['system_id']}"}
                        ),
                        "user_payload_sha256": sha256_json(
                            {"text": f"case:{route['case_id']}"}
                        ),
                        "seed": 10_000 * system_index + unit_index,
                    }
                    slots.append(slot)
                    slot_components[slot_id] = component
        execution_plan = {
            "schema_version": FRONTIER_CONTRACT_SCHEMA_VERSION,
            "plan_id": f"execution-plan-{system['system_id']}",
            "campaign_sha256": campaign_plan["campaign_sha256"],
            "system_id": system["system_id"],
            "slots": slots,
            "plan_sha256": "0" * 64,
        }
        execution_plan["plan_sha256"] = hash_payload(
            execution_plan, "plan_sha256"
        )
        execution_authority = sha256_json(
            {"execution_authority": system["system_id"]}
        )
        attempt_reports = []
        events = []
        consumed = {
            "calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "money_microunits": 0,
            "wall_clock_ms": 0,
        }
        for slot in slots:
            component = slot_components[slot["slot_id"]]
            model = models[slot["model_id"]]
            usage = {
                "calls": 1,
                "input_tokens": min(
                    1, component["max_input_tokens_per_attempt"]
                ),
                "output_tokens": min(
                    1, component["max_output_tokens_per_attempt"]
                ),
                "money_microunits": min(
                    1, component["max_money_microunits_per_attempt"]
                ),
                "wall_clock_ms": min(
                    1, component["max_wall_clock_ms_per_attempt"]
                ),
            }
            for axis in consumed:
                consumed[axis] += usage[axis]
            receipt = sha256_json(
                {"system_id": system["system_id"], "slot_id": slot["slot_id"]}
            )
            attempt = {
                "schema_version": FRONTIER_CONTRACT_SCHEMA_VERSION,
                "campaign_sha256": campaign_plan["campaign_sha256"],
                "preflight_sha256": preflight["preflight_sha256"],
                "evidence_expires_at": preflight["evidence_expires_at"],
                "execution_authority_receipt_sha256": execution_authority,
                "system_id": system["system_id"],
                "component_id": slot["component_id"],
                "attempt_id": slot["slot_id"],
                "execution_plan_sha256": execution_plan["plan_sha256"],
                "unit_id": slot["unit_id"],
                "case_id": slot["case_id"],
                "replicate_index": slot["replicate_index"],
                "retry_index": slot["retry_index"],
                "purpose": slot["purpose"],
                "route": slot["route"],
                "route_authority_id": slot["route_authority_id"],
                "provider": model["provider"],
                "model_id": model["model_id"],
                "model_snapshot": model["snapshot"],
                "parameters_sha256": model["parameters_sha256"],
                "retry_policy_sha256": model["retry_policy_sha256"],
                "cache_policy_sha256": model["cache_policy_sha256"],
                "seeds_sha256": model["seeds_sha256"],
                "seed": slot["seed"],
                "pricing_entry_sha256": model["pricing_entry_sha256"],
                "price_snapshot_sha256": campaign_plan["pricing"][
                    "price_snapshot_sha256"
                ],
                "pricing_currency": campaign_plan["pricing"]["currency"],
                "request_sha256": sha256_json({"request": slot["slot_id"]}),
                "response_sha256": sha256_json({"response": slot["slot_id"]}),
                "provider_status": "success",
                "usage": usage,
                "receipt_sha256": receipt,
                "usable": True,
                "failure_code": None,
                "attempt_sha256": "0" * 64,
            }
            attempt["attempt_sha256"] = hash_payload(attempt, "attempt_sha256")
            attempt_reports.append(attempt)
            events.append(
                {
                    "reservation_id": slot["slot_id"],
                    "status": "success",
                    "reserved": {
                        "calls": 1,
                        "input_tokens": component[
                            "max_input_tokens_per_attempt"
                        ],
                        "output_tokens": component[
                            "max_output_tokens_per_attempt"
                        ],
                        "money_microunits": component[
                            "max_money_microunits_per_attempt"
                        ],
                        "wall_clock_ms": component[
                            "max_wall_clock_ms_per_attempt"
                        ],
                    },
                    "charged": usage,
                    "receipt_sha256": receipt,
                    "over_reserved_axes": [],
                    "over_ceiling_axes": [],
                }
            )
        zero = {axis: 0 for axis in consumed}
        ledger = {
            "ceilings": copy.deepcopy(budget["ceilings"]),
            "consumed": consumed,
            "active_reserved": zero,
            "remaining_unreserved": {
                axis: budget["ceilings"][axis] - consumed[axis]
                for axis in consumed
            },
            "active_reservations": [],
            "events": events,
            "pre_call_cancellations": [],
            "reservation_rejections": [],
            "breached": False,
            "ledger_sha256": "0" * 64,
        }
        ledger["ledger_sha256"] = hash_payload(ledger, "ledger_sha256")
        manifest = {
            "schema_version": FRONTIER_CONTRACT_SCHEMA_VERSION,
            "manifest_id": f"execution-manifest-{system['system_id']}",
            "campaign_sha256": campaign_plan["campaign_sha256"],
            "preflight_sha256": preflight["preflight_sha256"],
            "evidence_expires_at": preflight["evidence_expires_at"],
            "system_id": system["system_id"],
            "execution_plan_sha256": execution_plan["plan_sha256"],
            "execution_authority_receipt_sha256": execution_authority,
            "attempt_reports": attempt_reports,
            "completed_slot_ids": [slot["slot_id"] for slot in slots],
            "budget_ledger": ledger,
            "host_sha256": sha256_json({"host": system["system_id"]}),
            "manifest_sha256": "0" * 64,
        }
        manifest["manifest_sha256"] = hash_payload(manifest, "manifest_sha256")
        executions.append(
            {
                "system_id": system["system_id"],
                "execution_plan": execution_plan,
                "execution_manifest": manifest,
            }
        )
    return build_frontier_execution_bundle(
        campaign_plan, executions=executions
    )


def resign_report_input(payload: dict, verifier: ReportVerifier) -> None:
    for gate in payload["gates"]:
        if gate["authority_id"] is not None:
            gate["authority_receipt_sha256"] = verifier.verify(
                evidence_type="gate",
                evidence_id=gate["gate_id"],
                authority_id=gate["authority_id"],
                context_sha256=frontier_gate_context_sha256(payload, gate),
            )
    for comparison in payload["baseline_comparisons"]:
        if comparison["authority_id"] is not None:
            comparison["authority_receipt_sha256"] = verifier.verify(
                evidence_type="baseline_comparison",
                evidence_id=comparison["baseline_id"],
                authority_id=comparison["authority_id"],
                context_sha256=frontier_comparison_context_sha256(
                    payload, comparison
                ),
            )


def replay_fixture(root: Path, *, fake_preflight: bool = False):
    report_verifier = ReportVerifier()
    claim_verifier = ClaimVerifier()
    replay_verifier = ReplayVerifier()
    evidence_semantic_verifier = EvidenceSemanticVerifier()
    campaign_plan_path = build_campaign_fixture(root)
    campaign_plan = json.loads(campaign_plan_path.read_text(encoding="utf-8"))
    release_policy = json.loads(
        (root / campaign_plan["release_policy_path"]).read_text(encoding="utf-8")
    )
    source_commitment = json.loads(
        (root / campaign_plan["source_commitment_path"]).read_text(encoding="utf-8")
    )
    authoritative_preflight = preflight_fixture(
        valid=True,
        campaign_sha256=campaign_plan["campaign_sha256"],
        policy_sha256=release_policy["policy_sha256"],
        commitment_sha256=source_commitment["commitment_sha256"],
        analysis_sha256=campaign_plan["analysis_sha256"],
    )
    preflight = (
        preflight_fixture(valid=False)
        if fake_preflight
        else authoritative_preflight
    )
    execution_bundle = synthetic_all_system_execution_bundle(
        campaign_plan, authoritative_preflight
    )

    typed_values = {
        "frontier_preflight_report": preflight,
        "campaign_plan": campaign_plan,
        "release_policy": release_policy,
        "source_commitment": source_commitment,
        "execution_bundle": execution_bundle,
    }
    artifact_bytes: dict[str, bytes] = {
        kind: canonical_authority_bytes(value)
        for kind, value in typed_values.items()
    }
    semantic_sources = [
        bytes_sha256(artifact_bytes[kind])
        for kind in ("campaign_plan", "source_commitment", "execution_bundle")
    ]
    for kind in sorted(TOP_TIER_REQUIRED_ARTIFACT_KINDS - {"frontier_report_input"}):
        if kind not in artifact_bytes:
            artifact_bytes[kind] = canonical_authority_bytes(
                evidence_envelope(
                kind,
                campaign_sha256=campaign_plan["campaign_sha256"],
                policy_sha256=release_policy["policy_sha256"],
                commitment_sha256=source_commitment["commitment_sha256"],
                    source_artifact_sha256s=semantic_sources,
                    semantic_verifier=evidence_semantic_verifier,
                )
            )

    artifacts: list[dict] = []
    for index, kind in enumerate(sorted(artifact_bytes)):
        path = f"evidence/{index:02d}-{kind}.json"
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_bytes(artifact_bytes[kind])
        artifacts.append(
            {
                "artifact_id": f"artifact-{index:02d}",
                "artifact_kind": kind,
                "path": path,
                "content_sha256": bytes_sha256(artifact_bytes[kind]),
            }
        )

    placeholder = {
        "artifact_id": "artifact-report-input",
        "artifact_kind": "frontier_report_input",
        "path": "report-input.json",
        "content_sha256": "0" * 64,
    }
    artifacts.insert(0, placeholder)
    source_sha256 = frontier_source_artifacts_sha256(artifacts)
    candidate_budget = next(
        item for item in campaign_plan["budgets"] if item["system_id"] == "candidate"
    )
    payload = report_input_fixture(
        verifier=report_verifier,
        release_policy=release_policy,
        scope_bindings={
            "campaign_sha256": campaign_plan["campaign_sha256"],
            "commitment_sha256": source_commitment["commitment_sha256"],
            "analysis_sha256": campaign_plan["analysis_sha256"],
            "task_distribution_sha256": source_commitment[
                "target_distribution_sha256"
            ],
            "evaluation_harness_sha256": campaign_plan[
                "analysis_implementation_sha256"
            ],
            "budget_ceiling_sha256": sha256_json(candidate_budget["ceilings"]),
        },
    )
    payload["campaign_id"] = campaign_plan["campaign_id"]
    payload["source_artifacts_sha256"] = source_sha256
    preflight_artifact = next(
        item for item in artifacts if item["artifact_kind"] == "frontier_preflight_report"
    )
    payload["scope"]["preflight_report_sha256"] = preflight.get(
        "preflight_sha256", "f" * 64
    )
    evidence_by_kind = {
        artifact["artifact_kind"]: artifact["content_sha256"]
        for artifact in artifacts
        if artifact["artifact_kind"] != "frontier_report_input"
    }
    for gate in payload["gates"]:
        gate["evidence_sha256"] = evidence_by_kind[gate["evidence_kind"]]
    for comparison in payload["baseline_comparisons"]:
        comparison["evidence_sha256"] = evidence_by_kind["statistics"]
    resign_report_input(payload, report_verifier)
    report_input_raw = write_canonical(root / "report-input.json", payload)
    placeholder["content_sha256"] = bytes_sha256(report_input_raw)

    report = build_frontier_report(
        payload,
        release_policy=release_policy,
        authority_verifier=report_verifier,
    )
    report_raw = write_canonical(root / "frontier-report.json", report)
    inventory = claim_inventory_fixture(report, claim_verifier)
    inventory_raw = write_canonical(root / "claim-inventory.json", inventory)

    environment = {
        "os_name": "windows",
        "architecture": "amd64",
        "python_implementation": "cpython",
        "python_version": "3.13.1",
        "package_version": "0.4.0-test",
        "package_sha256": "a" * 64,
        "analysis_implementation_sha256": "b" * 64,
        "dependency_lock_sha256": "c" * 64,
        "container_image_digest": "sha256:" + "d" * 64,
        "replay_command_sha256": "e" * 64,
    }
    plan = {
        "schema_version": FRONTIER_CONTRACT_SCHEMA_VERSION,
        "plan_id": "reproduction-plan-1",
        "campaign_id": payload["campaign_id"],
        "report_id": payload["report_id"],
        "reproduction_group_id": "reproduction-group-1",
        "required_operator_count": 3,
        "report_input_path": "report-input.json",
        "expected_report_path": "frontier-report.json",
        "expected_report_file_sha256": bytes_sha256(report_raw),
        "expected_report_sha256": report["report_sha256"],
        "expected_report_canonical_sha256": bytes_sha256(report_raw),
        "claim_inventory_path": "claim-inventory.json",
        "claim_inventory_file_sha256": bytes_sha256(inventory_raw),
        "claim_inventory_sha256": inventory["inventory_sha256"],
        "artifacts": artifacts,
        "source_artifacts_sha256": source_sha256,
        "input_commitments_sha256": frontier_input_commitments_sha256(artifacts),
        "environment": environment,
        "environment_sha256": sha256_json(environment),
        "reproduction_operators": [
            {
                "operator_id": "independent-operator-1",
                "machine_id": "independent-machine-1",
                "attestation_authority_id": "reproduction-authority-1",
                "attestation_id": "reproduction-attestation-1",
            },
            {
                "operator_id": "independent-operator-2",
                "machine_id": "independent-machine-2",
                "attestation_authority_id": "reproduction-authority-2",
                "attestation_id": "reproduction-attestation-2",
            },
            {
                "operator_id": "independent-operator-3",
                "machine_id": "independent-machine-3",
                "attestation_authority_id": "reproduction-authority-3",
                "attestation_id": "reproduction-attestation-3",
            },
        ],
        "operator": {
            "operator_id": "independent-operator-1",
            "machine_id": "independent-machine-1",
            "attestation_authority_id": "reproduction-authority-1",
            "attestation_id": "reproduction-attestation-1",
        },
        "plan_sha256": "0" * 64,
    }
    plan["plan_sha256"] = hash_payload(plan, "plan_sha256")
    plan_path = root / "reproduction-plan.json"
    write_canonical(plan_path, plan)
    return {
        "plan_path": plan_path,
        "plan": plan,
        "environment": environment,
        "report": report,
        "inventory": inventory,
        "report_verifier": report_verifier,
        "claim_verifier": claim_verifier,
        "replay_verifier": replay_verifier,
        "evidence_semantic_verifier": evidence_semantic_verifier,
        "preflight_artifact": preflight_artifact,
    }


def rewrite_plan(root: Path, fixture: dict) -> None:
    plan = fixture["plan"]
    plan["plan_sha256"] = hash_payload(plan, "plan_sha256")
    write_canonical(fixture["plan_path"], plan)


def run_three_replays(root: Path, fixture: dict) -> tuple[list[dict], list[dict]]:
    plans: list[dict] = []
    reports: list[dict] = []
    for index, operator in enumerate(fixture["plan"]["reproduction_operators"]):
        plan = copy.deepcopy(fixture["plan"])
        plan["operator"] = copy.deepcopy(operator)
        plan["plan_sha256"] = hash_payload(plan, "plan_sha256")
        plan_path = root / f"reproduction-plan-{index + 1}.json"
        write_canonical(plan_path, plan)
        replay = run_frontier_offline_replay(
            plan_path,
            root=root,
            environment=fixture["environment"],
            report_authority_verifier=fixture["report_verifier"],
            claim_authority_verifier=fixture["claim_verifier"],
            replay_attestation_verifier=fixture["replay_verifier"],
            evidence_semantic_verifier=fixture["evidence_semantic_verifier"],
            claim_validation_timestamp=CLAIM_VALIDATION_TIMESTAMP,
            replay_id=f"offline-replay-{index + 1}",
        )
        plans.append(plan)
        reports.append(replay)
    return plans, reports


class FrontierReplayTests(unittest.TestCase):
    def test_schema_roots_and_nested_objects_are_strict(self):
        expected = {
            "frontier-reproduction-plan.schema.json": (
                REPRODUCTION_PLAN_FIELDS,
                "urn:prompt-performance-engine:schema:frontier:reproduction-plan:1.0.0",
            ),
            "frontier-replay-report.schema.json": (
                REPLAY_REPORT_FIELDS,
                "urn:prompt-performance-engine:schema:frontier:replay-report:1.0.0",
            ),
            "frontier-reproduction-set.schema.json": (
                REPRODUCTION_SET_FIELDS,
                "urn:prompt-performance-engine:schema:frontier:reproduction-set:1.0.0",
            ),
            "frontier-evidence-envelope.schema.json": (
                EVIDENCE_ENVELOPE_FIELDS,
                "urn:prompt-performance-engine:schema:frontier:evidence-envelope:1.0.0",
            ),
        }
        for filename, (fields, schema_id) in expected.items():
            schema = json.loads(
                (PACKAGE_ROOT / "schemas" / filename).read_text(encoding="utf-8")
            )
            self.assertEqual(schema["$id"], schema_id)
            self.assertFalse(schema["additionalProperties"])
            self.assertEqual(set(schema["required"]), set(fields))
            self.assertEqual(set(schema["properties"]), set(fields))
        plan_schema = json.loads(
            (
                PACKAGE_ROOT / "schemas" / "frontier-reproduction-plan.schema.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            set(plan_schema["$defs"]["artifact"]["required"]),
            set(ARTIFACT_FIELDS),
        )
        self.assertEqual(
            set(plan_schema["$defs"]["environment"]["required"]),
            set(ENVIRONMENT_FIELDS),
        )
        self.assertEqual(
            set(plan_schema["$defs"]["operator"]["required"]),
            set(OPERATOR_FIELDS),
        )
        for definition in ("artifact", "environment", "operator"):
            self.assertFalse(
                plan_schema["$defs"][definition]["additionalProperties"]
            )
        evidence_schema = json.loads(
            (
                PACKAGE_ROOT / "schemas" / "frontier-evidence-envelope.schema.json"
            ).read_text(encoding="utf-8")
        )
        self.assertFalse(evidence_schema["$defs"]["payload"]["additionalProperties"])
        self.assertEqual(
            set(evidence_schema["$defs"]["payload"]["required"]),
            set(EVIDENCE_PAYLOAD_FIELDS),
        )
        self.assertEqual(
            set(evidence_schema["$defs"]["payload"]["properties"]),
            set(EVIDENCE_PAYLOAD_FIELDS),
        )

    def test_typed_evidence_envelope_rejects_kind_scope_and_hash_drift(self):
        envelope = evidence_envelope(
            "statistics",
            campaign_sha256="1" * 64,
            policy_sha256="2" * 64,
            commitment_sha256="3" * 64,
            source_artifact_sha256s=["4" * 64],
            semantic_verifier=EvidenceSemanticVerifier(),
        )
        self.assertEqual(validate_frontier_evidence_envelope(envelope), [])
        for field, value in (
            ("artifact_kind", "campaign_plan"),
            ("outcome", "forged"),
            ("payload_sha256", "not-a-digest"),
        ):
            with self.subTest(field=field):
                candidate = copy.deepcopy(envelope)
                candidate[field] = value
                candidate["evidence_sha256"] = hash_payload(
                    candidate, "evidence_sha256"
                )
                self.assertTrue(validate_frontier_evidence_envelope(candidate))

    def test_execution_bundle_exactly_covers_candidate_and_all_baselines(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = replay_fixture(root)
            campaign_artifact = next(
                item
                for item in fixture["plan"]["artifacts"]
                if item["artifact_kind"] == "campaign_plan"
            )
            execution_artifact = next(
                item
                for item in fixture["plan"]["artifacts"]
                if item["artifact_kind"] == "execution_bundle"
            )
            campaign = json.loads(
                (root / campaign_artifact["path"]).read_text(encoding="utf-8")
            )
            execution_bundle = json.loads(
                (root / execution_artifact["path"]).read_text(encoding="utf-8")
            )
            self.assertEqual(
                execution_bundle["system_ids"],
                [system["system_id"] for system in campaign["systems"]],
            )
            self.assertEqual(
                validate_frontier_execution_bundle(
                    execution_bundle, campaign_plan=campaign
                ),
                [],
            )
            omitted = copy.deepcopy(execution_bundle)
            omitted["system_ids"].pop()
            omitted["executions"].pop()
            omitted["bundle_sha256"] = hash_payload(omitted, "bundle_sha256")
            self.assertTrue(
                validate_frontier_execution_bundle(omitted, campaign_plan=campaign)
            )

    def test_valid_plan_and_offline_replay_reproduce_exact_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = replay_fixture(root)
            self.assertTrue((root / "frontier-report.json").read_bytes().endswith(b"\n"))
            self.assertEqual(validate_frontier_reproduction_plan(fixture["plan"]), [])
            replay = run_frontier_offline_replay(
                fixture["plan_path"],
                root=root,
                environment=fixture["environment"],
                report_authority_verifier=fixture["report_verifier"],
                claim_authority_verifier=fixture["claim_verifier"],
                replay_attestation_verifier=fixture["replay_verifier"],
                evidence_semantic_verifier=fixture[
                    "evidence_semantic_verifier"
                ],
                claim_validation_timestamp=CLAIM_VALIDATION_TIMESTAMP,
                replay_id="offline-replay-1",
            )
            self.assertTrue(replay["passed"], replay["failures"])
            self.assertEqual(
                replay["actual_report_file_sha256"],
                replay["expected_report_file_sha256"],
            )
            self.assertEqual(
                validate_frontier_replay_report(
                    replay,
                    plan=fixture["plan"],
                    attestation_verifier=fixture["replay_verifier"],
                ),
                [],
            )

    def test_three_independent_replays_are_required_for_reproduction_set(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = replay_fixture(root)
            plans, reports = run_three_replays(root, fixture)
            self.assertTrue(all(report["passed"] for report in reports))
            aggregate = build_frontier_reproduction_set(
                "reproduction-set-1",
                plans=plans,
                replay_reports=reports,
                attestation_verifier=fixture["replay_verifier"],
            )
            self.assertTrue(aggregate["passed"], aggregate["failures"])
            self.assertEqual(
                validate_frontier_reproduction_set(
                    aggregate,
                    plans=plans,
                    replay_reports=reports,
                    attestation_verifier=fixture["replay_verifier"],
                ),
                [],
            )

            duplicate_plans = [plans[0], plans[0], plans[2]]
            duplicate_reports = [reports[0], reports[0], reports[2]]
            duplicate = build_frontier_reproduction_set(
                "reproduction-set-duplicate",
                plans=duplicate_plans,
                replay_reports=duplicate_reports,
                attestation_verifier=fixture["replay_verifier"],
            )
            self.assertFalse(duplicate["passed"])
            self.assertTrue(
                any("independent" in failure for failure in duplicate["failures"])
            )

            tampered = copy.deepcopy(aggregate)
            tampered["machine_ids"][0] = "forged-machine"
            tampered["reproduction_set_sha256"] = hash_payload(
                tampered, "reproduction_set_sha256"
            )
            self.assertTrue(
                validate_frontier_reproduction_set(
                    tampered,
                    plans=plans,
                    replay_reports=reports,
                    attestation_verifier=fixture["replay_verifier"],
                )
            )

    def test_pretty_printed_expected_report_is_not_canonical_reproduction(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = replay_fixture(root)
            pretty = json.dumps(
                fixture["report"], ensure_ascii=False, sort_keys=True, indent=2
            ).encode("utf-8")
            (root / "frontier-report.json").write_bytes(pretty)
            fixture["plan"]["expected_report_file_sha256"] = bytes_sha256(pretty)
            rewrite_plan(root, fixture)
            replay = self._run(root, fixture)
            self.assertFalse(replay["passed"])
            self.assertTrue(any("rebuilt" in item for item in replay["failures"]))

    def test_noncanonical_plan_without_required_lf_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = replay_fixture(root)
            raw = fixture["plan_path"].read_bytes()
            self.assertTrue(raw.endswith(b"\n"))
            fixture["plan_path"].write_bytes(raw[:-1])

            replay = self._run(root, fixture)
            self.assertFalse(replay["passed"])
            self.assertTrue(any("plan" in item for item in replay["failures"]))

    def test_symlinked_authority_artifact_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = replay_fixture(root)
            source = root / "report-input.json"
            target = root / "linked-report-input.json"
            target.write_bytes(source.read_bytes())
            source.unlink()
            try:
                os.symlink(target, source)
            except OSError as exc:
                self.skipTest(f"symbolic links are unavailable: {exc}")

            replay = self._run(root, fixture)
            self.assertFalse(replay["passed"])
            self.assertTrue(
                any("source artifacts" in item for item in replay["failures"])
            )

    def test_tampered_source_fails_before_attestation_and_does_not_leak(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = replay_fixture(root)
            artifact = fixture["plan"]["artifacts"][2]
            (root / artifact["path"]).write_text(
                "SEALED-CONTENT-SHOULD-NOT-LEAK", encoding="utf-8"
            )
            replay = self._run(root, fixture)
            self.assertFalse(replay["passed"])
            self.assertEqual(fixture["replay_verifier"].calls, 0)
            self.assertNotIn("SEALED", " ".join(replay["failures"]))

    def test_environment_mismatch_fails_before_opening_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = replay_fixture(root)
            environment = copy.deepcopy(fixture["environment"])
            environment["architecture"] = "arm64"
            replay = run_frontier_offline_replay(
                fixture["plan_path"],
                root=root,
                environment=environment,
                report_authority_verifier=fixture["report_verifier"],
                claim_authority_verifier=fixture["claim_verifier"],
                replay_attestation_verifier=fixture["replay_verifier"],
                evidence_semantic_verifier=fixture[
                    "evidence_semantic_verifier"
                ],
                claim_validation_timestamp=CLAIM_VALIDATION_TIMESTAMP,
            )
            self.assertFalse(replay["passed"])
            self.assertEqual(fixture["replay_verifier"].calls, 0)
            self.assertTrue(any("environment" in item for item in replay["failures"]))

    def test_path_traversal_and_unknown_fields_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = replay_fixture(root)
            plan = copy.deepcopy(fixture["plan"])
            plan["artifacts"][0]["path"] = "../sealed.json"
            plan["plan_sha256"] = hash_payload(plan, "plan_sha256")
            self.assertTrue(
                any(
                    "artifact path" in item
                    for item in validate_frontier_reproduction_plan(plan)
                )
            )
            plan = copy.deepcopy(fixture["plan"])
            plan["environment"]["unknown"] = True
            plan["environment_sha256"] = sha256_json(plan["environment"])
            plan["plan_sha256"] = hash_payload(plan, "plan_sha256")
            self.assertTrue(
                any(
                    "unknown fields" in item
                    for item in validate_frontier_reproduction_plan(plan)
                )
            )
            plan = copy.deepcopy(fixture["plan"])
            plan["artifacts"][1]["artifact_kind"] = plan["artifacts"][0][
                "artifact_kind"
            ]
            plan["input_commitments_sha256"] = frontier_input_commitments_sha256(
                plan["artifacts"]
            )
            plan["source_artifacts_sha256"] = frontier_source_artifacts_sha256(
                plan["artifacts"]
            )
            plan["plan_sha256"] = hash_payload(plan, "plan_sha256")
            self.assertTrue(
                any(
                    "artifact kinds must be unique" in item
                    for item in validate_frontier_reproduction_plan(plan)
                )
            )
            plan = copy.deepcopy(fixture["plan"])
            plan["reproduction_operators"][1]["machine_id"] = plan[
                "reproduction_operators"
            ][0]["machine_id"]
            plan["plan_sha256"] = hash_payload(plan, "plan_sha256")
            self.assertTrue(
                any(
                    "machine commitments must be independent" in item
                    for item in validate_frontier_reproduction_plan(plan)
                )
            )

    def test_missing_or_throwing_replay_attestation_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = replay_fixture(root)
            missing = run_frontier_offline_replay(
                fixture["plan_path"],
                root=root,
                environment=fixture["environment"],
                report_authority_verifier=fixture["report_verifier"],
                claim_authority_verifier=fixture["claim_verifier"],
                replay_attestation_verifier=None,
                evidence_semantic_verifier=fixture[
                    "evidence_semantic_verifier"
                ],
                claim_validation_timestamp=CLAIM_VALIDATION_TIMESTAMP,
            )
            throwing = run_frontier_offline_replay(
                fixture["plan_path"],
                root=root,
                environment=fixture["environment"],
                report_authority_verifier=fixture["report_verifier"],
                claim_authority_verifier=fixture["claim_verifier"],
                replay_attestation_verifier=RaisingReplayVerifier(),
                evidence_semantic_verifier=fixture[
                    "evidence_semantic_verifier"
                ],
                claim_validation_timestamp=CLAIM_VALIDATION_TIMESTAMP,
            )
            self.assertFalse(missing["passed"])
            self.assertFalse(throwing["passed"])
            self.assertNotIn("SEALED", " ".join(throwing["failures"]))

    def test_missing_or_rejecting_evidence_semantic_verifier_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = replay_fixture(root)
            missing = run_frontier_offline_replay(
                fixture["plan_path"],
                root=root,
                environment=fixture["environment"],
                report_authority_verifier=fixture["report_verifier"],
                claim_authority_verifier=fixture["claim_verifier"],
                replay_attestation_verifier=fixture["replay_verifier"],
                evidence_semantic_verifier=None,
                claim_validation_timestamp=CLAIM_VALIDATION_TIMESTAMP,
            )
            rejecting = run_frontier_offline_replay(
                fixture["plan_path"],
                root=root,
                environment=fixture["environment"],
                report_authority_verifier=fixture["report_verifier"],
                claim_authority_verifier=fixture["claim_verifier"],
                replay_attestation_verifier=fixture["replay_verifier"],
                evidence_semantic_verifier=RejectingEvidenceSemanticVerifier(),
                claim_validation_timestamp=CLAIM_VALIDATION_TIMESTAMP,
            )
            self.assertFalse(missing["passed"])
            self.assertFalse(rejecting["passed"])
            self.assertTrue(
                any("semantic verifier is required" in item for item in missing["failures"])
            )
            self.assertTrue(
                any("semantic authority is invalid" in item for item in rejecting["failures"])
            )

    def test_legacy_readiness_cannot_substitute_frontier_preflight(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = replay_fixture(root, fake_preflight=True)
            replay = self._run(root, fixture)
            self.assertFalse(replay["passed"])
            self.assertTrue(any("preflight" in item for item in replay["failures"]))

    def test_semantically_rehashed_expected_report_still_fails_exact_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = replay_fixture(root)
            changed = copy.deepcopy(fixture["report"])
            changed["limitations"] = ["A different public limitation."]
            changed["report_sha256"] = hash_payload(changed, "report_sha256")
            raw = write_canonical(root / "frontier-report.json", changed)
            plan = fixture["plan"]
            plan["expected_report_file_sha256"] = bytes_sha256(raw)
            plan["expected_report_sha256"] = changed["report_sha256"]
            plan["expected_report_canonical_sha256"] = bytes_sha256(raw)
            rewrite_plan(root, fixture)
            replay = self._run(root, fixture)
            self.assertFalse(replay["passed"])
            self.assertTrue(any("canonical report bytes" in item for item in replay["failures"]))

    def test_rehashed_claim_inventory_tampering_fails_claim_authority(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = replay_fixture(root)
            inventory = copy.deepcopy(fixture["inventory"])
            claim = inventory["claims"][0]
            claim["statement"] = "An unsupported expanded public statement."
            claim["claim_sha256"] = hash_payload(claim, "claim_sha256")
            inventory["inventory_sha256"] = hash_payload(
                inventory, "inventory_sha256"
            )
            raw = write_canonical(root / "claim-inventory.json", inventory)
            fixture["plan"]["claim_inventory_file_sha256"] = bytes_sha256(raw)
            fixture["plan"]["claim_inventory_sha256"] = inventory[
                "inventory_sha256"
            ]
            rewrite_plan(root, fixture)
            replay = self._run(root, fixture)
            self.assertFalse(replay["passed"])
            self.assertTrue(
                any("claim inventory authority" in item for item in replay["failures"])
            )

    def test_duplicate_json_report_input_is_rejected_without_traceback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = replay_fixture(root)
            raw = b'{"schema_version":"1.0.0","schema_version":"1.0.0"}'
            (root / "report-input.json").write_bytes(raw)
            descriptor = fixture["plan"]["artifacts"][0]
            descriptor["content_sha256"] = bytes_sha256(raw)
            fixture["plan"]["input_commitments_sha256"] = (
                frontier_input_commitments_sha256(fixture["plan"]["artifacts"])
            )
            rewrite_plan(root, fixture)
            replay = self._run(root, fixture)
            self.assertFalse(replay["passed"])
            self.assertTrue(any("source artifacts" in item for item in replay["failures"]))

    def test_rehashed_passing_replay_tampering_fails_attestation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = replay_fixture(root)
            replay = self._run(root, fixture)
            self.assertTrue(replay["passed"])
            replay["machine_id"] = "forged-machine"
            replay["replay_sha256"] = hash_payload(replay, "replay_sha256")
            failures = validate_frontier_replay_report(
                replay,
                plan=fixture["plan"],
                attestation_verifier=fixture["replay_verifier"],
            )
            self.assertTrue(any("attestation" in item for item in failures))

    def test_invalid_plan_produces_a_valid_fail_closed_replay_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan_path = root / "invalid-plan.json"
            plan_path.write_bytes(
                canonical_authority_bytes(
                    {
                        "schema_version": FRONTIER_CONTRACT_SCHEMA_VERSION,
                        "plan_id": None,
                    }
                )
            )
            replay = run_frontier_offline_replay(
                plan_path,
                root=root,
                environment={},
                report_authority_verifier=None,
                claim_authority_verifier=None,
                replay_attestation_verifier=None,
                claim_validation_timestamp=CLAIM_VALIDATION_TIMESTAMP,
            )
            self.assertFalse(replay["passed"])
            self.assertEqual(
                validate_frontier_replay_report(
                    replay, plan=None, attestation_verifier=None
                ),
                [],
            )

    def _run(self, root: Path, fixture: dict) -> dict:
        return run_frontier_offline_replay(
            fixture["plan_path"],
            root=root,
            environment=fixture["environment"],
            report_authority_verifier=fixture["report_verifier"],
            claim_authority_verifier=fixture["claim_verifier"],
            replay_attestation_verifier=fixture["replay_verifier"],
            evidence_semantic_verifier=fixture["evidence_semantic_verifier"],
            claim_validation_timestamp=CLAIM_VALIDATION_TIMESTAMP,
            replay_id="offline-replay-test",
        )


if __name__ == "__main__":
    unittest.main()
