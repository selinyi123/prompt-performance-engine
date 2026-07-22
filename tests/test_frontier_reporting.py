from __future__ import annotations

import copy
import json
import math
import unittest

from prompt_performance_engine.contracts import PACKAGE_ROOT
from prompt_performance_engine.frontier_contracts import (
    FRONTIER_CONTRACT_SCHEMA_VERSION,
)
from prompt_performance_engine.frontier_reporting import (
    BASELINE_COMPARISON_FIELDS,
    CLAIM_FIELDS,
    CLAIM_INVENTORY_FIELDS,
    CLAIM_METRIC_FIELDS,
    FRONTIER_GATE_IDS,
    FRONTIER_REPORT_FIELDS,
    GATE_EVIDENCE_KINDS,
    GATE_FIELDS,
    REPORT_SCOPE_FIELDS,
    build_frontier_report as _build_frontier_report,
    frontier_claim_context_sha256,
    frontier_claim_evidence_summary_sha256,
    frontier_comparison_context_sha256,
    frontier_gate_context_sha256,
    validate_frontier_claim_inventory as _validate_frontier_claim_inventory,
    validate_frontier_report as _validate_frontier_report,
    validate_frontier_report_input,
    validate_frontier_report_policy_binding,
)
from prompt_performance_engine.hashing import hash_payload, sha256_json


DIGEST = "a" * 64
CLAIM_VALIDATION_TIMESTAMP = "2026-07-21T12:00:00Z"


def release_policy_fixture() -> dict:
    policy = {
        "schema_version": FRONTIER_CONTRACT_SCHEMA_VERSION,
        "policy_id": "frontier-policy-1",
        "primary_budget_dimension": "money_microunits",
        "required_strong_baseline_ids": [
            "original",
            "no-op",
            "expert",
            "random-search",
            "gepa",
        ],
        "required_strong_baseline_kinds": [
            "identity",
            "no_op",
            "expert",
            "random_search",
            "public_optimizer",
        ],
        "required_judge_count": 2,
        "require_judge_family_independence": True,
        "require_judge_provider_independence": True,
        "require_dual_position": True,
        "allowed_authority_routes": ["deterministic"],
        "human_gold": {
            "ordinary_minimum_expert_reviewers": 2,
            "critical_minimum_expert_reviewers": 3,
            "require_senior_adjudication": True,
            "minimum_calibration_cases": 2,
            "minimum_order_consistency_ppm": 800_000,
            "minimum_human_majority_agreement_ppm": 800_000,
            "minimum_agreement_coefficient_ppm": 700_000,
            "minimum_critical_recall_ppm": 900_000,
            "maximum_false_negative_rate_ppm": 100_000,
            "maximum_length_bias_ppm": 100_000,
        },
        "statistical_design": {
            "primary_metric": "macro_delta",
            "minimum_meaningful_effect_ppm": 100_000,
            "minimum_detectable_effect_ppm": 50_000,
            "confidence_level_ppm": 950_000,
            "power_target_ppm": 800_000,
            "tie_policy": "human_adjudication",
            "missing_observation_policy": "terminal_failure",
            "stopping_rule_path": "policy/stopping-rule.json",
            "stopping_rule_sha256": "f" * 64,
        },
        "analysis": {
            "bootstrap_seed": 20260721,
            "bootstrap_iterations": 10_000,
            "multiplicity_method": "holm",
            "percentile_method": "type_7",
            "pareto_rule": "strict_non_dominated",
            "safety_event_unit": "distinct_case",
            "safety_event_limit": 0,
        },
    }
    policy["policy_sha256"] = hash_payload(policy, "policy_sha256")
    return policy


RELEASE_POLICY = release_policy_fixture()


def build_frontier_report(*args, **kwargs):
    kwargs.setdefault("release_policy", RELEASE_POLICY)
    return _build_frontier_report(*args, **kwargs)


def validate_frontier_report(*args, **kwargs):
    kwargs.setdefault("release_policy", RELEASE_POLICY)
    return _validate_frontier_report(*args, **kwargs)


def validate_frontier_claim_inventory(*args, **kwargs):
    kwargs.setdefault("release_policy", RELEASE_POLICY)
    return _validate_frontier_claim_inventory(*args, **kwargs)


class ReportVerifier:
    def verify(
        self,
        *,
        evidence_type: str,
        evidence_id: str,
        authority_id: str,
        context_sha256: str,
    ) -> str:
        return sha256_json(
            {
                "evidence_type": evidence_type,
                "evidence_id": evidence_id,
                "authority_id": authority_id,
                "context_sha256": context_sha256,
            }
        )


class ClaimVerifier:
    def verify(
        self,
        *,
        claim_id: str,
        authority_id: str,
        context_sha256: str,
    ) -> str:
        return sha256_json(
            {
                "claim_id": claim_id,
                "authority_id": authority_id,
                "context_sha256": context_sha256,
            }
        )


class RaisingVerifier:
    def verify(self, **kwargs):
        raise RuntimeError("SEALED-CONTENT-MUST-NOT-LEAK")


class DuplicateReportVerifier:
    def verify(self, **kwargs):
        return "d" * 64


class DuplicateClaimVerifier:
    def verify(self, **kwargs):
        return "e" * 64


def report_input_fixture(
    *,
    gate_outcome: tuple[str, str] | None = None,
    comparison_outcome: tuple[str, str] | None = None,
    verifier: ReportVerifier | DuplicateReportVerifier | None = None,
    release_policy: dict | None = None,
    scope_bindings: dict | None = None,
) -> dict:
    verifier = verifier or ReportVerifier()
    release_policy = release_policy or RELEASE_POLICY
    statistical_design = release_policy["statistical_design"]
    scope = {
        "campaign_sha256": "1" * 64,
        "policy_sha256": release_policy["policy_sha256"],
        "commitment_sha256": "3" * 64,
        "analysis_sha256": "4" * 64,
        "preflight_report_sha256": "5" * 64,
        "target_model_snapshots": ["model-a@2026-07-01"],
        "domain_ids": ["coding", "legal"],
        "task_distribution_sha256": "6" * 64,
        "evaluation_harness_sha256": "7" * 64,
        "primary_budget_dimension": "money_microunits",
        "budget_ceiling_sha256": "8" * 64,
        "evidence_as_of": "2026-07-21T00:00:00Z",
        "expires_at": "2026-10-21T00:00:00Z",
    }
    if scope_bindings:
        scope.update(scope_bindings)
    gates = []
    for index, gate_id in enumerate(FRONTIER_GATE_IDS):
        outcome = "passed"
        if gate_outcome and gate_outcome[0] == gate_id:
            outcome = gate_outcome[1]
        evaluated = outcome != "not_evaluable"
        gates.append(
            {
                "gate_id": gate_id,
                "outcome": outcome,
                "evidence_kind": GATE_EVIDENCE_KINDS[gate_id],
                "evidence_sha256": f"{index + 1:x}" * 64 if evaluated else None,
                "authority_id": f"{gate_id}-authority" if evaluated else None,
                "authority_receipt_sha256": "0" * 64 if evaluated else None,
                "failures": [] if outcome == "passed" else [f"{gate_id} failed"],
            }
        )
    kinds = list(release_policy["required_strong_baseline_kinds"])
    ids = list(release_policy["required_strong_baseline_ids"])
    comparisons = []
    for index, (baseline_id, baseline_kind) in enumerate(zip(ids, kinds)):
        outcome = "superior"
        if comparison_outcome and comparison_outcome[0] == baseline_id:
            outcome = comparison_outcome[1]
        evaluated = outcome != "not_evaluable"
        comparisons.append(
            {
                "baseline_id": baseline_id,
                "baseline_kind": baseline_kind,
                "outcome": outcome,
                "metric_id": statistical_design["primary_metric"],
                "meaningful_margin": statistical_design[
                    "minimum_meaningful_effect_ppm"
                ]
                / 1_000_000,
                "point_estimate": 0.2,
                "interval_lower": 0.11 if outcome == "superior" else 0.01,
                "interval_upper": 0.29,
                "confidence_level": statistical_design["confidence_level_ppm"]
                / 1_000_000,
                "evidence_sha256": f"{index + 10:x}" * 64 if evaluated else None,
                "authority_id": f"{baseline_id}-comparison-authority"
                if evaluated
                else None,
                "authority_receipt_sha256": "0" * 64 if evaluated else None,
                "failures": []
                if outcome == "superior"
                else ["registered superiority test did not pass"],
            }
        )
    payload = {
        "schema_version": FRONTIER_CONTRACT_SCHEMA_VERSION,
        "report_id": "frontier-report-1",
        "campaign_id": "campaign-1",
        "generated_at": "2026-07-21T01:02:03Z",
        "scope": scope,
        "source_artifacts_sha256": "9" * 64,
        "environment_sha256": "a" * 64,
        "gates": gates,
        "required_strong_baseline_ids": ids,
        "baseline_comparisons": comparisons,
        "limitations": ["Only the frozen scope and model snapshots are covered."],
    }
    for gate in gates:
        if gate["authority_id"] is not None:
            gate["authority_receipt_sha256"] = verifier.verify(
                evidence_type="gate",
                evidence_id=gate["gate_id"],
                authority_id=gate["authority_id"],
                context_sha256=frontier_gate_context_sha256(payload, gate),
            )
    for comparison in comparisons:
        if comparison["authority_id"] is not None:
            comparison["authority_receipt_sha256"] = verifier.verify(
                evidence_type="baseline_comparison",
                evidence_id=comparison["baseline_id"],
                authority_id=comparison["authority_id"],
                context_sha256=frontier_comparison_context_sha256(
                    payload, comparison
                ),
            )
    return payload


def claim_inventory_fixture(report: dict, verifier: ClaimVerifier | None = None) -> dict:
    verifier = verifier or ClaimVerifier()
    report_scope = report["scope"]
    comparison = report["baseline_comparisons"][0]
    claim_scope = {
        "target_model_snapshots": list(report_scope["target_model_snapshots"]),
        "domain_ids": list(report_scope["domain_ids"]),
        "task_distribution_sha256": report_scope["task_distribution_sha256"],
        "evaluation_harness_sha256": report_scope[
            "evaluation_harness_sha256"
        ],
        "budget_ceiling_sha256": report_scope["budget_ceiling_sha256"],
        "evidence_as_of": report_scope["evidence_as_of"],
        "expires_at": report_scope["expires_at"],
    }
    claim = {
        "claim_id": "public-claim-1",
        "claim_status": report["status"],
        "statement": "The candidate is top-tier within the frozen scope.",
        "scope": claim_scope,
        "scope_sha256": sha256_json(claim_scope),
        "metrics": [
            {
                "source_kind": "baseline_comparison",
                "source_id": comparison["baseline_id"],
                "metric_id": comparison["metric_id"],
                "point_estimate": comparison["point_estimate"],
                "interval_lower": comparison["interval_lower"],
                "interval_upper": comparison["interval_upper"],
                "confidence_level": comparison["confidence_level"],
                "evidence_sha256": comparison["evidence_sha256"],
            }
        ],
        "evidence_summary_sha256": "0" * 64,
        "limitations": ["No claim outside the frozen domains."],
        "expires_at": claim_scope["expires_at"],
        "authority_id": "claims-auditor",
        "authority_receipt_sha256": "0" * 64,
        "claim_sha256": "0" * 64,
    }
    inventory = {
        "schema_version": FRONTIER_CONTRACT_SCHEMA_VERSION,
        "inventory_id": "claim-inventory-1",
        "report_id": report["report_id"],
        "report_sha256": report["report_sha256"],
        "claims": [claim],
        "inventory_sha256": "0" * 64,
    }
    claim["evidence_summary_sha256"] = frontier_claim_evidence_summary_sha256(
        report, claim
    )
    claim["authority_receipt_sha256"] = verifier.verify(
        claim_id=claim["claim_id"],
        authority_id=claim["authority_id"],
        context_sha256=frontier_claim_context_sha256(inventory, claim),
    )
    claim["claim_sha256"] = hash_payload(claim, "claim_sha256")
    inventory["inventory_sha256"] = hash_payload(inventory, "inventory_sha256")
    return inventory


def resign_claim_inventory(inventory: dict, verifier: ClaimVerifier) -> None:
    for claim in inventory["claims"]:
        claim["authority_receipt_sha256"] = verifier.verify(
            claim_id=claim["claim_id"],
            authority_id=claim["authority_id"],
            context_sha256=frontier_claim_context_sha256(inventory, claim),
        )
        claim["claim_sha256"] = hash_payload(claim, "claim_sha256")
    inventory["inventory_sha256"] = hash_payload(inventory, "inventory_sha256")


class FrontierReportingTests(unittest.TestCase):
    def setUp(self):
        self.report_verifier = ReportVerifier()
        self.claim_verifier = ClaimVerifier()

    def test_schema_roots_are_stable_strict_frontier_contracts(self):
        expected = {
            "frontier-report.schema.json": (
                FRONTIER_REPORT_FIELDS,
                "urn:prompt-performance-engine:schema:frontier:report:1.0.0",
            ),
            "frontier-claim-inventory.schema.json": (
                CLAIM_INVENTORY_FIELDS,
                "urn:prompt-performance-engine:schema:frontier:claim-inventory:1.0.0",
            ),
        }
        for filename, (fields, schema_id) in expected.items():
            with self.subTest(filename=filename):
                schema = json.loads(
                    (PACKAGE_ROOT / "schemas" / filename).read_text(encoding="utf-8")
                )
                self.assertEqual(schema["$id"], schema_id)
                self.assertEqual(
                    schema["properties"]["schema_version"]["const"],
                    FRONTIER_CONTRACT_SCHEMA_VERSION,
                )
                self.assertFalse(schema["additionalProperties"])
                self.assertEqual(set(schema["required"]), set(fields))
                self.assertEqual(set(schema["properties"]), set(fields))

    def test_schema_nested_objects_reject_unknown_fields(self):
        report_schema = json.loads(
            (PACKAGE_ROOT / "schemas" / "frontier-report.schema.json").read_text(
                encoding="utf-8"
            )
        )
        inventory_schema = json.loads(
            (
                PACKAGE_ROOT / "schemas" / "frontier-claim-inventory.schema.json"
            ).read_text(encoding="utf-8")
        )
        self.assertFalse(report_schema["$defs"]["scope"]["additionalProperties"])
        self.assertFalse(report_schema["$defs"]["gate"]["additionalProperties"])
        self.assertFalse(
            report_schema["$defs"]["baselineComparison"]["additionalProperties"]
        )
        self.assertEqual(
            set(report_schema["$defs"]["gate"]["required"]), set(GATE_FIELDS)
        )
        self.assertEqual(
            set(report_schema["$defs"]["baselineComparison"]["required"]),
            set(BASELINE_COMPARISON_FIELDS),
        )
        self.assertEqual(
            set(report_schema["$defs"]["scope"]["required"]),
            set(REPORT_SCOPE_FIELDS),
        )
        self.assertFalse(inventory_schema["$defs"]["claim"]["additionalProperties"])
        self.assertEqual(
            set(inventory_schema["$defs"]["claim"]["required"]),
            set(CLAIM_FIELDS),
        )
        self.assertFalse(inventory_schema["$defs"]["metric"]["additionalProperties"])
        self.assertEqual(
            set(inventory_schema["$defs"]["metric"]["required"]),
            set(CLAIM_METRIC_FIELDS),
        )

    def test_all_passed_and_all_strong_baselines_superior_is_top_tier(self):
        payload = report_input_fixture(verifier=self.report_verifier)
        report = build_frontier_report(
            payload, authority_verifier=self.report_verifier
        )
        self.assertEqual(report["status"], "top_tier_scoped")
        self.assertEqual(report["failures"], [])
        self.assertEqual(
            validate_frontier_report(
                report, authority_verifier=self.report_verifier
            ),
            [],
        )

    def test_four_statuses_are_derived_from_authoritative_evidence(self):
        cases = (
            ({}, "top_tier_scoped"),
            (
                {"comparison_outcome": ("gepa", "not_superior")},
                "verified_improvement",
            ),
            (
                {"comparison_outcome": ("original", "not_superior")},
                "not_superior",
            ),
            (
                {"comparison_outcome": ("gepa", "not_evaluable")},
                "not_evaluable",
            ),
        )
        for options, expected in cases:
            with self.subTest(expected=expected):
                payload = report_input_fixture(
                    verifier=self.report_verifier, **options
                )
                report = build_frontier_report(
                    payload, authority_verifier=self.report_verifier
                )
                self.assertEqual(report["status"], expected)

    def test_failed_nonquality_gate_caps_result_at_verified_improvement(self):
        payload = report_input_fixture(
            gate_outcome=("safety", "failed"), verifier=self.report_verifier
        )
        report = build_frontier_report(
            payload, authority_verifier=self.report_verifier
        )
        self.assertEqual(report["status"], "verified_improvement")

    def test_invalidating_gate_failure_is_not_evaluable(self):
        for gate_id in ("preflight", "readiness", "independence", "reproduction"):
            with self.subTest(gate_id=gate_id):
                payload = report_input_fixture(
                    gate_outcome=(gate_id, "failed"), verifier=self.report_verifier
                )
                report = build_frontier_report(
                    payload, authority_verifier=self.report_verifier
                )
                self.assertEqual(report["status"], "not_evaluable")

    def test_release_policy_threshold_and_baseline_drift_is_rejected(self):
        payload = report_input_fixture(verifier=self.report_verifier)
        for mutate in (
            lambda value: value["required_strong_baseline_ids"].reverse(),
            lambda value: value["baseline_comparisons"][0].update(
                {"metric_id": "forged_metric"}
            ),
            lambda value: value["baseline_comparisons"][0].update(
                {"meaningful_margin": 0.01}
            ),
            lambda value: value["baseline_comparisons"][0].update(
                {"confidence_level": 0.90}
            ),
        ):
            candidate = copy.deepcopy(payload)
            mutate(candidate)
            self.assertTrue(
                validate_frontier_report_policy_binding(candidate, RELEASE_POLICY)
            )
            with self.assertRaises(ValueError):
                _build_frontier_report(
                    candidate,
                    release_policy=RELEASE_POLICY,
                    authority_verifier=self.report_verifier,
                )

        with self.assertRaises(ValueError):
            _build_frontier_report(
                payload,
                release_policy=None,
                authority_verifier=self.report_verifier,
            )

    def test_authority_roles_cannot_collapse_into_one_actor(self):
        payload = report_input_fixture(verifier=self.report_verifier)
        for gate in payload["gates"]:
            gate["authority_id"] = "single-authority"
        for comparison in payload["baseline_comparisons"]:
            comparison["authority_id"] = "single-authority"
        for gate in payload["gates"]:
            gate["authority_receipt_sha256"] = self.report_verifier.verify(
                evidence_type="gate",
                evidence_id=gate["gate_id"],
                authority_id=gate["authority_id"],
                context_sha256=frontier_gate_context_sha256(payload, gate),
            )
        for comparison in payload["baseline_comparisons"]:
            comparison["authority_receipt_sha256"] = self.report_verifier.verify(
                evidence_type="baseline_comparison",
                evidence_id=comparison["baseline_id"],
                authority_id=comparison["authority_id"],
                context_sha256=frontier_comparison_context_sha256(
                    payload, comparison
                ),
            )
        report = build_frontier_report(
            payload, authority_verifier=self.report_verifier
        )
        self.assertEqual(report["status"], "not_evaluable")
        self.assertTrue(any("roles" in item for item in report["failures"]))

    def test_missing_or_throwing_authority_fails_closed_without_leaking(self):
        payload = report_input_fixture(verifier=self.report_verifier)
        missing = build_frontier_report(payload, authority_verifier=None)
        throwing = build_frontier_report(payload, authority_verifier=RaisingVerifier())
        self.assertEqual(missing["status"], "not_evaluable")
        self.assertEqual(throwing["status"], "not_evaluable")
        self.assertNotIn("SEALED", " ".join(throwing["failures"]))

    def test_duplicate_gate_or_comparison_receipts_fail_closed(self):
        verifier = DuplicateReportVerifier()
        payload = report_input_fixture(verifier=verifier)
        report = build_frontier_report(payload, authority_verifier=verifier)
        self.assertEqual(report["status"], "not_evaluable")
        self.assertTrue(any("unique" in failure for failure in report["failures"]))

    def test_rehashed_status_or_gate_tampering_cannot_grant_authority(self):
        payload = report_input_fixture(verifier=self.report_verifier)
        report = build_frontier_report(
            payload, authority_verifier=self.report_verifier
        )
        report["status"] = "verified_improvement"
        report["failures"] = ["forged"]
        report["report_sha256"] = hash_payload(report, "report_sha256")
        failures = validate_frontier_report(
            report, authority_verifier=self.report_verifier
        )
        self.assertTrue(any("verified gates" in failure for failure in failures))

        tampered = build_frontier_report(
            payload, authority_verifier=self.report_verifier
        )
        tampered["gates"][0]["evidence_sha256"] = "f" * 64
        tampered["report_sha256"] = hash_payload(tampered, "report_sha256")
        failures = validate_frontier_report(
            tampered, authority_verifier=self.report_verifier
        )
        self.assertTrue(any("verified gates" in failure for failure in failures))

    def test_unknown_fields_in_nested_contracts_are_rejected(self):
        payload = report_input_fixture(verifier=self.report_verifier)
        for mutate in (
            lambda value: value.update({"unknown": True}),
            lambda value: value["scope"].update({"unknown": True}),
            lambda value: value["gates"][0].update({"unknown": True}),
            lambda value: value["baseline_comparisons"][0].update(
                {"unknown": True}
            ),
        ):
            with self.subTest(mutate=mutate):
                candidate = copy.deepcopy(payload)
                mutate(candidate)
                self.assertTrue(validate_frontier_report_input(candidate))

    def test_required_baseline_coverage_and_order_are_strict(self):
        payload = report_input_fixture(verifier=self.report_verifier)
        payload["baseline_comparisons"] = payload["baseline_comparisons"][1:]
        self.assertTrue(
            any(
                "canonical required order" in failure
                for failure in validate_frontier_report_input(payload)
            )
        )

    def test_nonfinite_or_inverted_comparison_is_rejected(self):
        payload = report_input_fixture(verifier=self.report_verifier)
        payload["baseline_comparisons"][0]["point_estimate"] = math.inf
        payload["baseline_comparisons"][1]["interval_lower"] = 1.0
        failures = validate_frontier_report_input(payload)
        self.assertTrue(any("point_estimate" in failure for failure in failures))
        self.assertTrue(any("interval" in failure for failure in failures))

    def test_comparison_outcome_must_match_preregistered_margin(self):
        superior = report_input_fixture(verifier=self.report_verifier)
        superior["baseline_comparisons"][0]["interval_lower"] = 0.05
        failures = validate_frontier_report_input(superior)
        self.assertTrue(any("meaningful margin" in failure for failure in failures))

        not_superior = report_input_fixture(
            verifier=self.report_verifier,
            comparison_outcome=("original", "not_superior"),
        )
        not_superior["baseline_comparisons"][0]["interval_lower"] = 0.2
        failures = validate_frontier_report_input(not_superior)
        self.assertTrue(any("contradicts" in failure for failure in failures))

    def test_huge_numbers_fail_without_raising_and_built_report_is_detached(self):
        payload = report_input_fixture(verifier=self.report_verifier)
        payload["baseline_comparisons"][0]["point_estimate"] = 10**10000
        self.assertTrue(validate_frontier_report_input(payload))

        payload = report_input_fixture(verifier=self.report_verifier)
        report = build_frontier_report(
            payload, authority_verifier=self.report_verifier
        )
        payload["scope"]["domain_ids"].append("mutated-after-build")
        self.assertNotIn("mutated-after-build", report["scope"]["domain_ids"])

    def test_report_and_claim_time_windows_fail_closed(self):
        payload = report_input_fixture(verifier=self.report_verifier)
        payload["generated_at"] = "2030-01-01T00:00:00Z"
        failures = validate_frontier_report_input(payload)
        self.assertTrue(any("evidence window" in failure for failure in failures))
        with self.assertRaises(ValueError):
            build_frontier_report(payload, authority_verifier=self.report_verifier)

        report = build_frontier_report(
            report_input_fixture(verifier=self.report_verifier),
            authority_verifier=self.report_verifier,
        )
        inventory = claim_inventory_fixture(report, self.claim_verifier)
        missing_clock = validate_frontier_claim_inventory(
            inventory,
            report=report,
            report_authority_verifier=self.report_verifier,
            claim_authority_verifier=self.claim_verifier,
            as_of=None,
        )
        expired = validate_frontier_claim_inventory(
            inventory,
            report=report,
            report_authority_verifier=self.report_verifier,
            claim_authority_verifier=self.claim_verifier,
            as_of="2026-10-21T00:00:00Z",
        )
        self.assertTrue(any("timestamp" in failure for failure in missing_clock))
        self.assertTrue(any("expired" in failure for failure in expired))

    def test_claim_inventory_binds_report_scope_metrics_and_authority(self):
        report = build_frontier_report(
            report_input_fixture(verifier=self.report_verifier),
            authority_verifier=self.report_verifier,
        )
        inventory = claim_inventory_fixture(report, self.claim_verifier)
        self.assertEqual(
            validate_frontier_claim_inventory(
                inventory,
                report=report,
                report_authority_verifier=self.report_verifier,
                claim_authority_verifier=self.claim_verifier,
                as_of=CLAIM_VALIDATION_TIMESTAMP,
            ),
            [],
        )

    def test_claim_metric_must_exactly_match_its_report_source(self):
        report = build_frontier_report(
            report_input_fixture(verifier=self.report_verifier),
            authority_verifier=self.report_verifier,
        )
        inventory = claim_inventory_fixture(report, self.claim_verifier)
        claim = inventory["claims"][0]
        claim["metrics"][0]["point_estimate"] = 123.0
        claim["metrics"][0]["interval_upper"] = 124.0
        claim["metrics"][0]["evidence_sha256"] = "f" * 64
        claim["evidence_summary_sha256"] = "e" * 64
        resign_claim_inventory(inventory, self.claim_verifier)

        failures = validate_frontier_claim_inventory(
            inventory,
            report=report,
            report_authority_verifier=self.report_verifier,
            claim_authority_verifier=self.claim_verifier,
            as_of=CLAIM_VALIDATION_TIMESTAMP,
        )
        self.assertTrue(
            any("does not match its report source" in failure for failure in failures)
        )
        self.assertTrue(
            any("evidence summary" in failure for failure in failures)
        )

    def test_claim_gate_evidence_binding_and_derived_summary(self):
        report = build_frontier_report(
            report_input_fixture(verifier=self.report_verifier),
            authority_verifier=self.report_verifier,
        )
        inventory = claim_inventory_fixture(report, self.claim_verifier)
        claim = inventory["claims"][0]
        gate = next(item for item in report["gates"] if item["gate_id"] == "safety")
        claim["metrics"] = [
            {
                "source_kind": "gate",
                "source_id": gate["gate_id"],
                "metric_id": None,
                "point_estimate": None,
                "interval_lower": None,
                "interval_upper": None,
                "confidence_level": None,
                "evidence_sha256": gate["evidence_sha256"],
            }
        ]
        claim["evidence_summary_sha256"] = frontier_claim_evidence_summary_sha256(
            report, claim
        )
        resign_claim_inventory(inventory, self.claim_verifier)
        self.assertEqual(
            validate_frontier_claim_inventory(
                inventory,
                report=report,
                report_authority_verifier=self.report_verifier,
                claim_authority_verifier=self.claim_verifier,
                as_of=CLAIM_VALIDATION_TIMESTAMP,
            ),
            [],
        )

        claim["evidence_summary_sha256"] = "d" * 64
        resign_claim_inventory(inventory, self.claim_verifier)
        failures = validate_frontier_claim_inventory(
            inventory,
            report=report,
            report_authority_verifier=self.report_verifier,
            claim_authority_verifier=self.claim_verifier,
            as_of=CLAIM_VALIDATION_TIMESTAMP,
        )
        self.assertTrue(
            any(
                "evidence summary does not match report bindings" in failure
                for failure in failures
            )
        )

    def test_claim_missing_authority_or_report_drift_is_rejected(self):
        report = build_frontier_report(
            report_input_fixture(verifier=self.report_verifier),
            authority_verifier=self.report_verifier,
        )
        inventory = claim_inventory_fixture(report, self.claim_verifier)
        failures = validate_frontier_claim_inventory(
            inventory,
            report=report,
            report_authority_verifier=self.report_verifier,
            claim_authority_verifier=None,
            as_of=CLAIM_VALIDATION_TIMESTAMP,
        )
        self.assertTrue(any("claim authority verifier" in failure for failure in failures))

        overlapping = claim_inventory_fixture(report, self.claim_verifier)
        claim = overlapping["claims"][0]
        claim["authority_id"] = report["gates"][0]["authority_id"]
        claim["authority_receipt_sha256"] = self.claim_verifier.verify(
            claim_id=claim["claim_id"],
            authority_id=claim["authority_id"],
            context_sha256=frontier_claim_context_sha256(overlapping, claim),
        )
        claim["claim_sha256"] = hash_payload(claim, "claim_sha256")
        overlapping["inventory_sha256"] = hash_payload(
            overlapping, "inventory_sha256"
        )
        overlap_failures = validate_frontier_claim_inventory(
            overlapping,
            report=report,
            report_authority_verifier=self.report_verifier,
            claim_authority_verifier=self.claim_verifier,
            as_of=CLAIM_VALIDATION_TIMESTAMP,
        )
        self.assertTrue(any("independent" in item for item in overlap_failures))

        drifted = copy.deepcopy(inventory)
        drifted["report_sha256"] = "f" * 64
        drifted["inventory_sha256"] = hash_payload(drifted, "inventory_sha256")
        self.assertTrue(
            any(
                "report binding" in failure
                for failure in validate_frontier_claim_inventory(
                    drifted,
                    report=report,
                    report_authority_verifier=self.report_verifier,
                    claim_authority_verifier=self.claim_verifier,
                    as_of=CLAIM_VALIDATION_TIMESTAMP,
                )
            )
        )

    def test_claim_scope_expansion_and_rehashed_metric_tampering_fail(self):
        report = build_frontier_report(
            report_input_fixture(verifier=self.report_verifier),
            authority_verifier=self.report_verifier,
        )
        inventory = claim_inventory_fixture(report, self.claim_verifier)
        expanded = copy.deepcopy(inventory)
        claim = expanded["claims"][0]
        claim["scope"]["domain_ids"].append("unseen-domain")
        claim["scope_sha256"] = sha256_json(claim["scope"])
        claim["claim_sha256"] = hash_payload(claim, "claim_sha256")
        expanded["inventory_sha256"] = hash_payload(expanded, "inventory_sha256")
        failures = validate_frontier_claim_inventory(
            expanded,
            report=report,
            report_authority_verifier=self.report_verifier,
            claim_authority_verifier=self.claim_verifier,
            as_of=CLAIM_VALIDATION_TIMESTAMP,
        )
        self.assertTrue(any("scope exceeds" in failure for failure in failures))

        tampered = copy.deepcopy(inventory)
        claim = tampered["claims"][0]
        claim["metrics"][0]["point_estimate"] = 0.25
        claim["claim_sha256"] = hash_payload(claim, "claim_sha256")
        tampered["inventory_sha256"] = hash_payload(tampered, "inventory_sha256")
        failures = validate_frontier_claim_inventory(
            tampered,
            report=report,
            report_authority_verifier=self.report_verifier,
            claim_authority_verifier=self.claim_verifier,
            as_of=CLAIM_VALIDATION_TIMESTAMP,
        )
        self.assertTrue(
            any("does not match its report source" in failure for failure in failures)
        )

    def test_duplicate_claim_receipts_are_rejected(self):
        report = build_frontier_report(
            report_input_fixture(verifier=self.report_verifier),
            authority_verifier=self.report_verifier,
        )
        verifier = DuplicateClaimVerifier()
        inventory = claim_inventory_fixture(report, verifier)
        second = copy.deepcopy(inventory["claims"][0])
        second["claim_id"] = "public-claim-2"
        inventory["claims"].append(second)
        for claim in inventory["claims"]:
            claim["authority_receipt_sha256"] = verifier.verify()
            claim["claim_sha256"] = hash_payload(claim, "claim_sha256")
        inventory["inventory_sha256"] = hash_payload(inventory, "inventory_sha256")
        failures = validate_frontier_claim_inventory(
            inventory,
            report=report,
            report_authority_verifier=self.report_verifier,
            claim_authority_verifier=verifier,
            as_of=CLAIM_VALIDATION_TIMESTAMP,
        )
        self.assertTrue(any("receipts must be unique" in failure for failure in failures))


if __name__ == "__main__":
    unittest.main()
