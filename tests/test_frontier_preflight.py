import copy
import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from prompt_performance_engine.contracts import PACKAGE_ROOT
from prompt_performance_engine.frontier_contracts import (
    CAMPAIGN_PLAN_FIELDS,
    FRONTIER_CONTRACT_SCHEMA_VERSION,
    RELEASE_POLICY_FIELDS,
    SOURCE_COMMITMENT_FIELDS,
    analysis_sha256,
    load_frontier_campaign_bundle,
    validate_frontier_campaign_bundle,
)
from prompt_performance_engine.frontier_io import canonical_authority_bytes
from prompt_performance_engine.frontier_preflight import (
    ClockVerificationResult,
    PREFLIGHT_REPORT_FIELDS,
    HumanGoldVerificationResult,
    StorageVerificationResult,
    run_frontier_preflight,
    validate_frontier_preflight_report,
)
from prompt_performance_engine.hashing import hash_payload, sha256_json


PREFLIGHT_TIMESTAMP = "2026-07-21T12:00:00Z"


def digest(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_authority_bytes(payload))


def artifact(root: Path, relative: str, content: str | None = None) -> dict:
    path = root / Path(*relative.split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content if content is not None else relative, encoding="utf-8")
    return {
        "path": relative,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def add_reference(target: dict, prefix: str, reference: dict) -> None:
    target[f"{prefix}_path"] = reference["path"]
    target[f"{prefix}_sha256"] = reference["sha256"]


def budget_components(
    *,
    include_optimizer: bool,
    generation_model_id: str,
    optimizer_model_id: str | None,
    judge_model_ids: list[str],
) -> list[dict]:
    components = [
        {
            "component_id": "generation",
            "role": "generation",
            "model_ids": [generation_model_id],
            "purposes": ["generation"],
            "unit_count": 2,
            "attempts_per_unit": 2,
            "max_input_tokens_per_attempt": 100,
            "max_output_tokens_per_attempt": 200,
            "max_money_microunits_per_attempt": 1_000,
            "max_wall_clock_ms_per_attempt": 10_000,
        },
        {
            "component_id": "judging",
            "role": "judge",
            "model_ids": judge_model_ids,
            "purposes": ["judging"],
            "unit_count": 4,
            "attempts_per_unit": 1,
            "max_input_tokens_per_attempt": 400,
            "max_output_tokens_per_attempt": 50,
            "max_money_microunits_per_attempt": 2_000,
            "max_wall_clock_ms_per_attempt": 5_000,
        },
    ]
    if include_optimizer:
        assert optimizer_model_id is not None
        components.append(
            {
                "component_id": "optimization",
                "role": "optimizer",
                "model_ids": [optimizer_model_id],
                "purposes": ["optimization"],
                "unit_count": 1,
                "attempts_per_unit": 2,
                "max_input_tokens_per_attempt": 500,
                "max_output_tokens_per_attempt": 500,
                "max_money_microunits_per_attempt": 3_000,
                "max_wall_clock_ms_per_attempt": 20_000,
            }
        )
    return components


def worst_case(components: list[dict]) -> dict[str, int]:
    result = {
        "calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "money_microunits": 0,
        "wall_clock_ms": 0,
    }
    for component in components:
        attempts = component["unit_count"] * component["attempts_per_unit"]
        result["calls"] += attempts
        result["input_tokens"] += attempts * component["max_input_tokens_per_attempt"]
        result["output_tokens"] += attempts * component["max_output_tokens_per_attempt"]
        result["money_microunits"] += (
            attempts * component["max_money_microunits_per_attempt"]
        )
        result["wall_clock_ms"] += (
            attempts * component["max_wall_clock_ms_per_attempt"]
        )
    return result


def model(root: Path, model_id: str, provider: str, family: str) -> dict:
    value = {
        "model_id": model_id,
        "provider": provider,
        "family": family,
        "snapshot": f"{model_id}-2026-07-01",
        "operator": f"operator-{model_id}",
    }
    for prefix in (
        "parameters",
        "retry_policy",
        "cache_policy",
        "seeds",
        "pricing_entry",
    ):
        add_reference(
            value,
            prefix,
            artifact(root, f"models/{model_id}/{prefix}.json"),
        )
    return value


def provenance(root: Path, system_id: str, source_revision: str) -> dict:
    value = {
        "source_revision": source_revision,
        "track": "instruction-only",
        "input_visibility": "single-revealed-task",
    }
    for prefix in (
        "source",
        "configuration",
        "search_space",
        "stopping_rule",
        "dependency_lock",
        "environment",
        "license_eligibility",
    ):
        add_reference(
            value,
            prefix,
            artifact(root, f"systems/{system_id}/{prefix}.json"),
        )
    return value


def build_fixture(root: Path) -> Path:
    (root / "storage").mkdir(parents=True)
    analysis_path = root / "analysis.py"
    analysis_path.write_text("ANALYSIS_VERSION = 1\n", encoding="utf-8")
    implementation_digest = hashlib.sha256(analysis_path.read_bytes()).hexdigest()

    analysis_policy = {
        "bootstrap_seed": 20260721,
        "bootstrap_iterations": 10_000,
        "multiplicity_method": "holm",
        "percentile_method": "type_7",
        "pareto_rule": "strict_non_dominated",
        "safety_event_unit": "distinct_case",
        "safety_event_limit": 0,
    }
    stopping_rule = artifact(root, "policy/stopping-rule.json")
    policy = {
        "schema_version": FRONTIER_CONTRACT_SCHEMA_VERSION,
        "policy_id": "frontier-policy-1",
        "primary_budget_dimension": "money_microunits",
        "required_strong_baseline_ids": [
            "identity",
            "no-op",
            "expert",
            "random-search",
            "public-optimizer",
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
        "allowed_authority_routes": [
            "deterministic",
            "r05_docker",
            "r06_visual",
            "environment_state",
        ],
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
            "primary_metric": "normalized_utility_delta",
            "minimum_meaningful_effect_ppm": 100_000,
            "minimum_detectable_effect_ppm": 50_000,
            "confidence_level_ppm": 950_000,
            "power_target_ppm": 800_000,
            "tie_policy": "human_adjudication",
            "missing_observation_policy": "terminal_failure",
            "stopping_rule_path": stopping_rule["path"],
            "stopping_rule_sha256": stopping_rule["sha256"],
        },
        "analysis": analysis_policy,
    }
    policy["policy_sha256"] = hash_payload(policy, "policy_sha256")
    write_json(root / "release-policy.json", policy)

    combined_analysis_digest = analysis_sha256(
        implementation_sha256=implementation_digest,
        analysis_policy=analysis_policy,
    )
    sealed_a = artifact(root, "sealed/case-a.json", "SECRET TASK A")
    anchor_a = artifact(root, "sealed/anchor-a.json", "SECRET ANSWER A")
    sealed_b = artifact(root, "sealed/case-b.json", "SECRET TASK B")
    anchor_b = artifact(root, "sealed/anchor-b.json", "SECRET ANSWER B")
    commitment = {
        "schema_version": FRONTIER_CONTRACT_SCHEMA_VERSION,
        "dataset_id": "sealed-claim-v1",
        "case_commitments": [
            {
                "case_id": "case-a",
                "stratum_id": "normal",
                "critical": False,
                "task_path": sealed_a["path"],
                "task_sha256": sealed_a["sha256"],
                "scoring_anchor_path": anchor_a["path"],
                "scoring_anchor_sha256": anchor_a["sha256"],
            },
            {
                "case_id": "case-b",
                "stratum_id": "adversarial",
                "critical": True,
                "task_path": sealed_b["path"],
                "task_sha256": sealed_b["sha256"],
                "scoring_anchor_path": anchor_b["path"],
                "scoring_anchor_sha256": anchor_b["sha256"],
            },
        ],
        "minimum_calibration_cases": 2,
        "robustness_pairs": [
            {"clean_case_id": "case-a", "perturbed_case_id": "case-b"}
        ],
        "analysis_sha256": combined_analysis_digest,
        "sealed_content_disclosed": False,
    }
    for prefix in (
        "license_review",
        "exclusion_rules",
        "contamination_check",
        "target_distribution",
        "utility_anchors",
        "power_analysis",
        "sealed_access_policy",
    ):
        add_reference(
            commitment,
            prefix,
            artifact(root, f"source/{prefix}.json"),
        )
    commitment["commitment_sha256"] = hash_payload(
        commitment,
        "commitment_sha256",
    )
    write_json(root / "source-commitment.json", commitment)

    models = [
        model(root, "generation-model", "provider-a", "generation-family"),
        model(root, "optimizer-model", "provider-b", "optimizer-family"),
        model(root, "judge-model-a", "provider-c", "judge-family-a"),
        model(root, "judge-model-b", "provider-d", "judge-family-b"),
    ]
    system_specs = [
        ("candidate", "candidate", False, "optimizer-model"),
        ("identity", "identity", True, None),
        ("no-op", "no_op", True, None),
        ("expert", "expert", True, "optimizer-model"),
        ("random-search", "random_search", True, "optimizer-model"),
        ("public-optimizer", "public_optimizer", True, "optimizer-model"),
    ]
    systems = [
        {
            "system_id": system_id,
            "kind": kind,
            "version": "1",
            "implementation_sha256": digest(f"implementation:{system_id}"),
            "optimizer_model_id": optimizer_model_id,
            "generation_model_id": "generation-model",
            "strong_baseline": strong_baseline,
            "provenance": provenance(root, system_id, "rev-1"),
        }
        for system_id, kind, strong_baseline, optimizer_model_id in system_specs
    ]
    ceilings = {
        "calls": 100,
        "input_tokens": 100_000,
        "output_tokens": 100_000,
        "money_microunits": 1_000_000,
        "wall_clock_ms": 1_000_000,
    }
    budgets = []
    judge_model_ids = ["judge-model-a", "judge-model-b"]
    for system_id, _, _, optimizer_model_id in system_specs:
        components = budget_components(
            include_optimizer=optimizer_model_id is not None,
            generation_model_id="generation-model",
            optimizer_model_id=optimizer_model_id,
            judge_model_ids=judge_model_ids,
        )
        budgets.append(
            {
                "system_id": system_id,
                "primary_dimension": "money_microunits",
                "ceilings": copy.deepcopy(ceilings),
                "components": components,
                "worst_case": worst_case(components),
            }
        )

    candidate_artifacts = {"source_revision": "rev-1"}
    for prefix in ("package", "optimizer_prompt", "profile", "code"):
        add_reference(
            candidate_artifacts,
            prefix,
            artifact(root, f"candidate/{prefix}.json"),
        )
    judges = []
    for suffix in ("a", "b"):
        judge = {
            "judge_id": f"judge-{suffix}",
            "model_id": f"judge-model-{suffix}",
            "receipt_authority_id": f"judge-receipts-{suffix}",
            "positions": ["A", "B"],
        }
        for prefix in ("prompt", "rubric", "parser", "calibration"):
            add_reference(
                judge,
                prefix,
                artifact(root, f"judges/{suffix}/{prefix}.json"),
            )
        judges.append(judge)

    deterministic_config = artifact(root, "hosts/deterministic.json")
    docker_config = artifact(root, "hosts/r05.json")
    docker_sandbox = artifact(root, "hosts/r05-sandbox.json")
    host_capabilities = [
        {
            "route": "deterministic",
            "authority_id": "deterministic-v1",
            "verifier_id": "deterministic-host-verifier",
            "configuration_path": deterministic_config["path"],
            "configuration_sha256": deterministic_config["sha256"],
            "runtime_image_digest": None,
            "sandbox_policy_path": None,
            "sandbox_policy_sha256": None,
            "visual_verifier_id": None,
            "visual_calibration_path": None,
            "visual_calibration_sha256": None,
        },
        {
            "route": "r05_docker",
            "authority_id": "r05-host-v1",
            "verifier_id": "r05-live-host-verifier",
            "configuration_path": docker_config["path"],
            "configuration_sha256": docker_config["sha256"],
            "runtime_image_digest": f"sha256:{digest('r05-image')}",
            "sandbox_policy_path": docker_sandbox["path"],
            "sandbox_policy_sha256": docker_sandbox["sha256"],
            "visual_verifier_id": None,
            "visual_calibration_path": None,
            "visual_calibration_sha256": None,
        },
    ]

    human_gold = {
        "expert_reviewer_ids": ["expert-1", "expert-2", "expert-3"],
        "senior_adjudicator_ids": ["senior-1"],
        "receipt_authority_id": "human-gold-authority",
        "verifier_id": "human-gold-verifier-v1",
    }
    for prefix in (
        "gold_set",
        "review_protocol",
        "expert_eligibility",
        "independence_attestation",
        "calibration",
    ):
        content = "SECRET GOLD LABELS" if prefix == "gold_set" else None
        add_reference(
            human_gold,
            prefix,
            artifact(root, f"human/{prefix}.json", content),
        )
    pricing = {
        "currency": "USD",
        "money_unit": "money_microunits",
        "microunits_per_currency_unit": 1_000_000,
    }
    add_reference(pricing, "price_snapshot", artifact(root, "pricing/snapshot.json"))
    expiry = artifact(root, "policy/expiry.json")
    owner_signature = artifact(root, "signatures/owner.sig")
    custodian_signature = artifact(root, "signatures/custodian.sig")
    plan = {
        "schema_version": FRONTIER_CONTRACT_SCHEMA_VERSION,
        "campaign_id": "frontier-campaign-1",
        "dataset_id": commitment["dataset_id"],
        "owner_id": "owner-1",
        "dataset_custodian_id": "custodian-1",
        "release_policy_path": "release-policy.json",
        "release_policy_sha256": policy["policy_sha256"],
        "source_commitment_path": "source-commitment.json",
        "source_commitment_sha256": commitment["commitment_sha256"],
        "analysis_implementation_path": "analysis.py",
        "analysis_implementation_sha256": implementation_digest,
        "analysis_sha256": combined_analysis_digest,
        "lifecycle": {
            "created_at": "2026-07-01T00:00:00Z",
            "evidence_expires_at": "2027-07-01T00:00:00Z",
            "responsible_operator_id": "campaign-operator-1",
            "expiry_policy_path": expiry["path"],
            "expiry_policy_sha256": expiry["sha256"],
        },
        "candidate_artifacts": candidate_artifacts,
        "models": models,
        "systems": systems,
        "judges": judges,
        "budgets": budgets,
        "case_routes": [
            {
                "case_id": "case-a",
                "route": "deterministic",
                "authority_id": "deterministic-v1",
            },
            {
                "case_id": "case-b",
                "route": "r05_docker",
                "authority_id": "r05-host-v1",
            },
        ],
        "host_capabilities": host_capabilities,
        "human_gold": human_gold,
        "pricing": pricing,
        "attestations": {
            "owner": {
                "subject_id": "owner-1",
                "authority_id": "owner-identity-authority",
                "attestation_id": "owner-attestation-1",
                "signature_path": owner_signature["path"],
                "signature_sha256": owner_signature["sha256"],
            },
            "dataset_custodian": {
                "subject_id": "custodian-1",
                "authority_id": "custodian-identity-authority",
                "attestation_id": "custodian-attestation-1",
                "signature_path": custodian_signature["path"],
                "signature_sha256": custodian_signature["sha256"],
            },
        },
        "storage": {
            "available_bytes": 10_000_000,
            "worst_case_bytes": 1_000_000,
            "storage_root_path": "storage",
            "authority_id": "storage-authority",
            "verifier_id": "storage-verifier-v1",
        },
    }
    plan["campaign_sha256"] = hash_payload(plan, "campaign_sha256")
    plan_path = root / "campaign-plan.json"
    write_json(plan_path, plan)
    return plan_path


def rehash_plan(plan: dict) -> None:
    plan["campaign_sha256"] = hash_payload(plan, "campaign_sha256")


class FixtureAttestationVerifier:
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.calls = []

    def verify(self, **context):
        self.calls.append(context)
        if self.fail:
            raise RuntimeError("untrusted verifier detail")
        return digest(f"attestation:{context['role']}:{context['context_sha256']}")


class FixtureClockVerifier:
    def __init__(self, timestamp: str = PREFLIGHT_TIMESTAMP, *, fail: bool = False):
        self.timestamp = timestamp
        self.fail = fail
        self.calls = []

    def verify(self, **context):
        self.calls.append(context)
        if self.fail:
            return None
        return ClockVerificationResult(
            timestamp=self.timestamp,
            receipt_sha256=digest(f"clock:{context['context_sha256']}"),
        )


class FixtureHumanGoldVerifier:
    def __init__(self, *, fail: bool = False, low_metric: bool = False):
        self.fail = fail
        self.low_metric = low_metric
        self.calls = []

    def verify(self, **context):
        self.calls.append(context)
        if self.fail:
            raise RuntimeError("sealed verifier detail")
        metric = 100_000 if self.low_metric else 950_000
        return HumanGoldVerificationResult(
            receipt_sha256=digest(f"human:{context['context_sha256']}"),
            calibrated_case_count=context["expected_case_count"],
            covered_case_count=context["expected_case_count"],
            covered_critical_case_count=context["expected_critical_case_count"],
            complete_position_pair_case_count=context["expected_case_count"],
            ordinary_minimum_reviewers=2,
            critical_minimum_reviewers=3,
            senior_adjudication_complete=True,
            all_denominators_nonzero=True,
            order_consistency_ppm=metric,
            human_majority_agreement_ppm=metric,
            agreement_coefficient_ppm=metric,
            critical_recall_ppm=metric,
            false_negative_rate_ppm=50_000,
            length_bias_ppm=50_000,
        )


class FixtureHostVerifier:
    def __init__(self, *, fail_route: str | None = None):
        self.fail_route = fail_route
        self.calls = []

    def verify(self, **context):
        self.calls.append(context)
        if context["route"] == self.fail_route:
            return None
        return digest(
            f"host:{context['route']}:{context['authority_id']}:"
            f"{context['context_sha256']}"
        )


class FixtureStorageVerifier:
    def __init__(self, *, available_bytes: int = 10_000_000):
        self.available_bytes = available_bytes
        self.calls = []

    def verify(self, **context):
        self.calls.append(context)
        return StorageVerificationResult(
            receipt_sha256=digest(f"storage:{context['context_sha256']}"),
            available_bytes=self.available_bytes,
        )


def verifier_set():
    return {
        "attestation_verifier": FixtureAttestationVerifier(),
        "human_gold_verifier": FixtureHumanGoldVerifier(),
        "host_capability_verifier": FixtureHostVerifier(),
        "storage_verifier": FixtureStorageVerifier(),
        "clock_verifier": FixtureClockVerifier(),
    }


class FrontierContractTests(unittest.TestCase):
    def test_schema_roots_match_runtime_exact_field_contracts_and_stable_ids(self):
        expected = {
            "frontier-campaign-plan.schema.json": (
                CAMPAIGN_PLAN_FIELDS,
                "urn:prompt-performance-engine:schema:frontier:campaign-plan:1.0.0",
            ),
            "frontier-release-policy.schema.json": (
                RELEASE_POLICY_FIELDS,
                "urn:prompt-performance-engine:schema:frontier:release-policy:1.0.0",
            ),
            "frontier-source-commitment.schema.json": (
                SOURCE_COMMITMENT_FIELDS,
                "urn:prompt-performance-engine:schema:frontier:source-commitment:1.0.0",
            ),
            "frontier-preflight-report.schema.json": (
                PREFLIGHT_REPORT_FIELDS,
                "urn:prompt-performance-engine:schema:frontier:preflight-report:1.0.0",
            ),
        }
        for filename, (fields, schema_id) in expected.items():
            with self.subTest(filename=filename):
                schema = json.loads(
                    (PACKAGE_ROOT / "schemas" / filename).read_text(encoding="utf-8")
                )
                self.assertEqual(
                    schema["$schema"],
                    "https://json-schema.org/draft/2020-12/schema",
                )
                self.assertEqual(schema["$id"], schema_id)
                self.assertFalse(schema["additionalProperties"])
                self.assertEqual(set(schema["properties"]), set(fields))
                self.assertEqual(set(schema["required"]), set(fields))
                self.assertEqual(
                    schema["properties"]["schema_version"]["const"],
                    FRONTIER_CONTRACT_SCHEMA_VERSION,
                )

    def test_full_freeze_bundle_loads_and_validates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = load_frontier_campaign_bundle(build_fixture(root), root=root)
            self.assertEqual(validate_frontier_campaign_bundle(bundle), [])

    def test_unknown_fields_uncontained_paths_and_artifact_tampering_fail_closed(self):
        mutations = (
            ("unknown", lambda plan: plan.__setitem__("sealed_payload", "secret")),
            (
                "parent",
                lambda plan: plan.__setitem__("release_policy_path", "../policy.json"),
            ),
            (
                "drive-relative",
                lambda plan: plan.__setitem__("release_policy_path", "C:policy.json"),
            ),
            (
                "normalized-parent",
                lambda plan: plan.__setitem__(
                    "release_policy_path", "nested/../release-policy.json"
                ),
            ),
        )
        for name, mutate in mutations:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                plan_path = build_fixture(root)
                plan = json.loads(plan_path.read_text(encoding="utf-8"))
                mutate(plan)
                rehash_plan(plan)
                write_json(plan_path, plan)
                with self.assertRaises(ValueError):
                    load_frontier_campaign_bundle(plan_path, root=root)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan_path = build_fixture(root)
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            (root / plan["candidate_artifacts"]["package_path"]).write_text(
                "tampered", encoding="utf-8"
            )
            with self.assertRaises(ValueError):
                load_frontier_campaign_bundle(plan_path, root=root)

    def test_baseline_budget_judge_and_host_bindings_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = load_frontier_campaign_bundle(build_fixture(root), root=root)

            plan = copy.deepcopy(bundle.plan)
            plan["systems"] = [
                item for item in plan["systems"] if item["system_id"] != "public-optimizer"
            ]
            plan["budgets"] = [
                item for item in plan["budgets"] if item["system_id"] != "public-optimizer"
            ]
            rehash_plan(plan)
            self.assertTrue(
                any(
                    "strong baseline" in item
                    for item in validate_frontier_campaign_bundle(
                        replace(bundle, plan=plan)
                    )
                )
            )

            plan = copy.deepcopy(bundle.plan)
            plan["budgets"][0]["ceilings"]["calls"] += 1
            rehash_plan(plan)
            self.assertTrue(
                any(
                    "equal hard budget" in item
                    for item in validate_frontier_campaign_bundle(
                        replace(bundle, plan=plan)
                    )
                )
            )

            plan = copy.deepcopy(bundle.plan)
            plan["budgets"][0]["components"][0]["model_ids"] = ["optimizer-model"]
            rehash_plan(plan)
            self.assertTrue(
                any(
                    "system target" in item
                    for item in validate_frontier_campaign_bundle(
                        replace(bundle, plan=plan)
                    )
                )
            )

            plan = copy.deepcopy(bundle.plan)
            next(
                item for item in plan["models"] if item["model_id"] == "judge-model-a"
            )["provider"] = "provider-a"
            rehash_plan(plan)
            self.assertTrue(
                any(
                    "provider overlaps" in item
                    for item in validate_frontier_campaign_bundle(
                        replace(bundle, plan=plan)
                    )
                )
            )

            plan = copy.deepcopy(bundle.plan)
            plan["host_capabilities"][1]["runtime_image_digest"] = None
            rehash_plan(plan)
            self.assertTrue(
                any(
                    "pinned image" in item
                    for item in validate_frontier_campaign_bundle(
                        replace(bundle, plan=plan)
                    )
                )
            )

            plan = copy.deepcopy(bundle.plan)
            plan["host_capabilities"].pop()
            rehash_plan(plan)
            self.assertTrue(
                any(
                    "host capability" in item
                    for item in validate_frontier_campaign_bundle(
                        replace(bundle, plan=plan)
                    )
                )
            )

    def test_source_human_gold_and_statistical_thresholds_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = load_frontier_campaign_bundle(build_fixture(root), root=root)

            commitment = copy.deepcopy(bundle.source_commitment)
            commitment["robustness_pairs"][0]["perturbed_case_id"] = "unknown"
            commitment["commitment_sha256"] = hash_payload(
                commitment, "commitment_sha256"
            )
            failures = validate_frontier_campaign_bundle(
                replace(bundle, source_commitment=commitment)
            )
            self.assertTrue(any("robustness pair" in item for item in failures))

            policy = copy.deepcopy(bundle.release_policy)
            policy["human_gold"]["ordinary_minimum_expert_reviewers"] = 4
            policy["human_gold"]["critical_minimum_expert_reviewers"] = 4
            policy["policy_sha256"] = hash_payload(policy, "policy_sha256")
            failures = validate_frontier_campaign_bundle(
                replace(bundle, release_policy=policy)
            )
            self.assertTrue(any("expert pool" in item for item in failures))

            policy = copy.deepcopy(bundle.release_policy)
            policy["statistical_design"]["minimum_detectable_effect_ppm"] = 200_000
            policy["policy_sha256"] = hash_payload(policy, "policy_sha256")
            failures = validate_frontier_campaign_bundle(
                replace(bundle, release_policy=policy)
            )
            self.assertTrue(any("detectable effect" in item for item in failures))


class FrontierPreflightTests(unittest.TestCase):
    def test_authoritative_preflight_is_minimal_and_contains_no_sealed_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            verifiers = verifier_set()
            report = run_frontier_preflight(
                build_fixture(root),
                root=root,
                **verifiers,
            )
            self.assertTrue(report["passed"], report)
            self.assertEqual(validate_frontier_preflight_report(report), [])
            self.assertEqual(set(report), PREFLIGHT_REPORT_FIELDS)
            rendered = json.dumps(report, ensure_ascii=False)
            for forbidden in (
                "case-a",
                "case-b",
                "SECRET TASK",
                "SECRET ANSWER",
                "SECRET GOLD",
                "sealed-claim-v1",
                "r05-host-v1",
            ):
                self.assertNotIn(forbidden, rendered)

    def test_missing_or_failed_live_authorities_fail_closed(self):
        cases = (
            ("missing-human", {"human_gold_verifier": None}),
            ("missing-host", {"host_capability_verifier": None}),
            ("missing-storage", {"storage_verifier": None}),
            ("missing-clock", {"clock_verifier": None}),
            (
                "low-human-metric",
                {"human_gold_verifier": FixtureHumanGoldVerifier(low_metric=True)},
            ),
            (
                "unavailable-r05",
                {"host_capability_verifier": FixtureHostVerifier(fail_route="r05_docker")},
            ),
            (
                "insufficient-storage",
                {"storage_verifier": FixtureStorageVerifier(available_bytes=100)},
            ),
        )
        for name, overrides in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                verifiers = verifier_set()
                verifiers.update(overrides)
                report = run_frontier_preflight(
                    build_fixture(root),
                    root=root,
                    **verifiers,
                )
                self.assertFalse(report["passed"])
                self.assertTrue(report["failures"])
                self.assertEqual(validate_frontier_preflight_report(report), [])

    def test_invalid_local_contract_never_calls_external_authorities(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan_path = build_fixture(root)
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            next(
                system
                for system in plan["systems"]
                if system["system_id"] == "public-optimizer"
            )["strong_baseline"] = False
            rehash_plan(plan)
            write_json(plan_path, plan)
            verifiers = verifier_set()
            report = run_frontier_preflight(
                plan_path,
                root=root,
                **verifiers,
            )
            self.assertFalse(report["passed"])
            for verifier in verifiers.values():
                self.assertEqual(verifier.calls, [])

    def test_expired_campaign_fails_before_authority_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan_path = build_fixture(root)
            verifiers = verifier_set()
            verifiers["clock_verifier"] = FixtureClockVerifier(
                "2028-01-01T00:00:00Z"
            )
            report = run_frontier_preflight(
                plan_path,
                root=root,
                **verifiers,
            )
            self.assertFalse(report["passed"])
            self.assertTrue(any("expired" in item for item in report["failures"]))
            self.assertEqual(len(verifiers["clock_verifier"].calls), 1)
            for name, verifier in verifiers.items():
                if name == "clock_verifier":
                    continue
                self.assertEqual(verifier.calls, [])

    def test_report_semantics_and_self_hash_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = run_frontier_preflight(
                build_fixture(root),
                root=root,
                **verifier_set(),
            )
            report["campaign_sha256"] = None
            report["preflight_sha256"] = hash_payload(report, "preflight_sha256")
            self.assertTrue(validate_frontier_preflight_report(report))

            report["passed"] = False
            report["failures"] = []
            report["preflight_sha256"] = hash_payload(report, "preflight_sha256")
            self.assertTrue(validate_frontier_preflight_report(report))


if __name__ == "__main__":
    unittest.main()
