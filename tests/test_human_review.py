import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from prompt_performance_engine.benchmark_replicates import (
    aggregate_benchmark_replicates,
)
from prompt_performance_engine.hashing import hash_payload, sha256_json
from prompt_performance_engine.human_review import (
    aggregate_human_review as _aggregate_human_review,
    create_reviewer_packet as _create_reviewer_packet,
    validate_human_review_authority as _validate_human_review_authority,
    validate_human_review_report,
    validate_submission,
)
from tests.test_benchmark_replicates import (
    TRUSTED_FIXTURE_RECEIPTS,
    create_run,
)


class TrustedFixtureReviewerVerifier:
    def verify(self, *, reviewer_id, packet_sha256, submission_sha256):
        return sha256_json(
            {
                "reviewer_id": reviewer_id,
                "packet_sha256": packet_sha256,
                "submission_sha256": submission_sha256,
            }
        )


TRUSTED_FIXTURE_REVIEWERS = TrustedFixtureReviewerVerifier()


def create_reviewer_packet(*args, **kwargs):
    kwargs.setdefault("model_receipt_verifier", TRUSTED_FIXTURE_RECEIPTS)
    return _create_reviewer_packet(*args, **kwargs)


def aggregate_human_review(*args, **kwargs):
    kwargs.setdefault("model_receipt_verifier", TRUSTED_FIXTURE_RECEIPTS)
    kwargs.setdefault("reviewer_submission_verifier", TRUSTED_FIXTURE_REVIEWERS)
    return _aggregate_human_review(*args, **kwargs)


def validate_human_review_authority(*args, **kwargs):
    kwargs.setdefault("model_receipt_verifier", TRUSTED_FIXTURE_RECEIPTS)
    kwargs.setdefault("reviewer_submission_verifier", TRUSTED_FIXTURE_REVIEWERS)
    return _validate_human_review_authority(*args, **kwargs)


DOMAINS = [
    "agents_automation",
    "business_strategy",
    "creative_design",
    "education",
    "high_risk_advisory",
    "image_generation",
    "marketing_sales",
    "professional_writing",
    "research_analysis",
    "software_engineering",
    "structured_data",
    "translation_localization",
]


def perfect_submission(packet, key):
    labels = {item["item_id"]: item["optimized_label"] for item in key["items"]}
    return {
        "schema_version": "2.0.0",
        "reviewer_id": packet["reviewer_id"],
        "authority": copy.deepcopy(packet["authority"]),
        "packet_sha256": packet["packet_sha256"],
        "decisions": [
            {
                "item_id": item["item_id"],
                "winner": labels[item["item_id"]],
                "reason": "This output more completely satisfies the stated rubric.",
            }
            for item in packet["items"]
        ],
    }


class HumanReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(cls.temporary_directory.name)
        cls.runs = [
            create_run(root, f"run-{index}", DOMAINS) for index in range(1, 4)
        ]
        cls.replicate_report = aggregate_benchmark_replicates(
            cls.runs,
            receipt_verifier=TRUSTED_FIXTURE_RECEIPTS,
        )
        cls.evaluations = [
            json.loads(
                (cls.runs[0] / domain / "evaluation.json").read_text(
                    encoding="utf-8"
                )
            )
            for domain in DOMAINS
        ]

    @classmethod
    def tearDownClass(cls):
        cls.temporary_directory.cleanup()

    def _reviews(self, reviewers, *, seeds=None, sample_size=24):
        packets = []
        keys = []
        submissions = []
        seeds = seeds or [7] * len(reviewers)
        for reviewer, seed in zip(reviewers, seeds, strict=True):
            packet, key = create_reviewer_packet(
                self.evaluations,
                replicate_report=self.replicate_report,
                run_directories=self.runs,
                reviewer_id=reviewer,
                sample_size=sample_size,
                seed=seed,
                position_probe_count=2,
            )
            packets.append(packet)
            keys.append(key)
            submissions.append(perfect_submission(packet, key))
        return packets, keys, submissions

    def test_packet_and_submission_schemas_match_runtime_constants(self):
        schema_root = Path(__file__).resolve().parents[1] / "schemas"
        packet_schema = json.loads(
            (schema_root / "human-review-packet.schema.json").read_text(
                encoding="utf-8"
            )
        )
        submission_schema = json.loads(
            (schema_root / "human-review-submission.schema.json").read_text(
                encoding="utf-8"
            )
        )

        minimum_reason = packet_schema["properties"]["instructions"][
            "properties"
        ]["minimum_reason_characters"]
        self.assertEqual(minimum_reason, {"const": 20})
        self.assertEqual(
            submission_schema["properties"]["decisions"]["minItems"],
            1,
        )
        packet_item = packet_schema["$defs"]["item"]
        self.assertIn("input_text", packet_item["required"])
        self.assertEqual(
            packet_item["properties"]["input_text"],
            {"type": "string", "minLength": 1},
        )

    def test_public_packet_hides_position_probe_identity(self):
        packets, keys, _ = self._reviews(["reviewer-opaque"])
        packet = packets[0]
        key = keys[0]

        self.assertEqual(
            packet["protocol"]["name"],
            "balanced_round_robin_hmac_sha256_v3",
        )
        self.assertEqual(
            set(packet["protocol"]),
            {"name", "sample_size", "blinding_key_sha256"},
        )
        self.assertNotIn("seed", packet["protocol"])
        self.assertNotIn("position_probe_count", packet["protocol"])
        self.assertNotIn("blinding_key", packet["protocol"])
        self.assertIn("blinding_key", key["protocol"])
        self.assertTrue(
            all(
                set(item)
                == {"item_id", "input_text", "rubric", "output_a", "output_b"}
                for item in packet["items"]
            )
        )
        definition = json.loads(
            (self.runs[0] / "benchmark-definition.json").read_text(encoding="utf-8")
        )
        case_inputs = {
            (case["domain"], case["case_id"]): case["input_text"]
            for job in definition["jobs"]
            for case in job["cases"]
        }
        public_items = {item["item_id"]: item for item in packet["items"]}
        self.assertTrue(
            all(
                public_items[item["item_id"]]["input_text"]
                == case_inputs[(item["domain"], item["case_id"])]
                for item in key["items"]
            )
        )
        self.assertTrue(
            all(
                item["item_id"].startswith("review:")
                and "position_probe" not in item["item_id"]
                for item in packet["items"]
            )
        )
        self.assertEqual(
            {item["item_id"] for item in packet["items"]},
            {item["item_id"] for item in key["items"]},
        )
        second_packet, second_key = create_reviewer_packet(
            self.evaluations,
            replicate_report=self.replicate_report,
            run_directories=self.runs,
            reviewer_id="reviewer-opaque",
            sample_size=24,
            seed=7,
            position_probe_count=2,
        )
        self.assertNotEqual(
            {item["item_id"] for item in packet["items"]},
            {item["item_id"] for item in second_packet["items"]},
        )
        self.assertNotEqual(
            key["protocol"]["blinding_key"],
            second_key["protocol"]["blinding_key"],
        )

    def test_reused_blinding_key_cannot_count_as_independent_review(self):
        with patch(
            "prompt_performance_engine.human_review.secrets.token_hex",
            return_value="a" * 64,
        ):
            packets, keys, submissions = self._reviews(
                ("reviewer-1", "reviewer-2", "reviewer-3")
            )

        with self.assertRaisesRegex(ValueError, "blinding keys must be unique"):
            aggregate_human_review(
                self.evaluations,
                packets,
                keys,
                submissions,
                replicate_report=self.replicate_report,
                run_directories=self.runs,
            )
    def test_coordinator_adjudication_cannot_manufacture_e4(self):
        packets, keys, submissions = self._reviews(
            ("reviewer-1", "reviewer-2", "reviewer-3", "reviewer-4")
        )
        for key, submission in zip(keys[2:], submissions[2:], strict=True):
            secrets_by_id = {item["item_id"]: item for item in key["items"]}
            for decision in submission["decisions"]:
                optimized_label = secrets_by_id[decision["item_id"]][
                    "optimized_label"
                ]
                decision["winner"] = "B" if optimized_label == "A" else "A"
        adjudications = {
            item["base_item_id"]: "win"
            for item in keys[0]["items"]
            if not item["position_probe"]
        }

        report = aggregate_human_review(
            self.evaluations,
            packets,
            keys,
            submissions,
            replicate_report=self.replicate_report,
            run_directories=self.runs,
            adjudications=adjudications,
        )

        self.assertEqual(report["adjudicated_cases"], sorted(adjudications))
        self.assertFalse(report["human_improvement_confirmed"])
        self.assertFalse(report["e4_ready"])

    def test_position_probe_count_is_bounded_by_protocol(self):
        for count in (0, 1, 25):
            with self.subTest(count=count), self.assertRaisesRegex(
                ValueError, "between 2 and sample_size"
            ):
                create_reviewer_packet(
                    self.evaluations,
                    replicate_report=self.replicate_report,
                    run_directories=self.runs,
                    reviewer_id=f"reviewer-{count}",
                    sample_size=24,
                    seed=7,
                    position_probe_count=count,
                )

    def test_fixed_position_and_tie_reviewers_cannot_reach_e4(self):
        for winner in ("A", "B", "tie"):
            with self.subTest(winner=winner):
                packets, keys, submissions = self._reviews(
                    ["reviewer-1", "reviewer-2", "reviewer-3"]
                )
                for submission in submissions:
                    for decision in submission["decisions"]:
                        decision["winner"] = winner
                report = aggregate_human_review(
                    self.evaluations,
                    packets,
                    keys,
                    submissions,
                    replicate_report=self.replicate_report,
                    run_directories=self.runs,
                )
                self.assertFalse(report["e4_ready"])
                self.assertFalse(report["all_reviewer_protocols_passed"])

    def test_probe_aware_position_gaming_cannot_reach_e4(self):
        packets, keys, submissions = self._reviews(
            ["reviewer-1", "reviewer-2", "reviewer-3"]
        )
        for key, submission in zip(keys, submissions, strict=True):
            probes = {
                item["item_id"]: item["position_probe"] for item in key["items"]
            }
            for decision in submission["decisions"]:
                decision["winner"] = "B" if probes[decision["item_id"]] else "A"
        report = aggregate_human_review(
            self.evaluations,
            packets,
            keys,
            submissions,
            replicate_report=self.replicate_report,
            run_directories=self.runs,
        )
        self.assertEqual(report["position_probe_consistency"], 1.0)
        self.assertEqual(report["base_position_a_selection_rate"], 1.0)
        self.assertFalse(report["all_reviewer_protocols_passed"])
        self.assertFalse(report["e4_ready"])

    def test_reviewer_ids_without_trusted_receipts_stay_below_e4(self):
        packets, keys, submissions = self._reviews(
            ["reviewer-1", "reviewer-2", "reviewer-3"]
        )
        report = _aggregate_human_review(
            self.evaluations,
            packets,
            keys,
            submissions,
            replicate_report=self.replicate_report,
            run_directories=self.runs,
            model_receipt_verifier=TRUSTED_FIXTURE_RECEIPTS,
        )
        self.assertFalse(report["all_reviewer_receipts_verified"])
        self.assertFalse(report["e4_ready"])
        self.assertEqual(report["evidence"]["level"], "E3")

    def test_malformed_reviewer_receipt_cannot_count_as_verified(self):
        packets, keys, submissions = self._reviews(
            ["reviewer-1", "reviewer-2", "reviewer-3"]
        )
        report = _aggregate_human_review(
            self.evaluations,
            packets,
            keys,
            submissions,
            replicate_report=self.replicate_report,
            run_directories=self.runs,
            model_receipt_verifier=TRUSTED_FIXTURE_RECEIPTS,
        )
        tampered = copy.deepcopy(report)
        tampered["review_artifacts"][0]["reviewer_receipt_sha256"] = 123
        tampered["review_artifacts"][0]["reviewer_identity_verified"] = False
        tampered["human_review_sha256"] = hash_payload(
            tampered,
            "human_review_sha256",
        )

        failures = validate_human_review_report(
            tampered,
            replicate_report=self.replicate_report,
        )

        self.assertIn("human-review reviewer receipt is invalid", failures)

    def test_case_coverage_reviewer_count_rejects_boolean(self):
        packets, keys, submissions = self._reviews(("reviewer-1",))
        report = aggregate_human_review(
            self.evaluations,
            packets,
            keys,
            submissions,
            replicate_report=self.replicate_report,
            run_directories=self.runs,
        )
        tampered = copy.deepcopy(report)
        item_id = tampered["case_coverage"][0]["item_id"]
        tampered["case_coverage"][0]["reviewer_count"] = True
        tampered["human_review_sha256"] = hash_payload(
            tampered,
            "human_review_sha256",
        )

        failures = validate_human_review_report(
            tampered,
            replicate_report=self.replicate_report,
        )

        self.assertIn(f"{item_id}: invalid human-review case coverage", failures)

    def test_three_reviewers_covering_same_24_cases_produce_e4(self):
        packets, keys, submissions = self._reviews(
            ("reviewer-1", "reviewer-2", "reviewer-3")
        )

        report = aggregate_human_review(
            self.evaluations,
            packets,
            keys,
            submissions,
            replicate_report=self.replicate_report,
            run_directories=self.runs,
        )

        self.assertTrue(report["e4_ready"])
        self.assertEqual(report["evidence"]["level"], "E4")
        self.assertEqual(report["reviewer_count"], 3)
        self.assertEqual(report["reviewed_case_count"], 24)
        self.assertEqual(report["fully_covered_case_count"], 24)
        self.assertTrue(
            all(item["reviewer_count"] == 3 for item in report["case_coverage"])
        )
        self.assertEqual(report["pairwise_agreement"], 1.0)
        self.assertEqual(report["judge_human_agreement"], 1.0)
        self.assertEqual(report["position_probe_consistency"], 1.0)
        self.assertEqual(
            validate_human_review_report(
                report,
                replicate_report=self.replicate_report,
            ),
            [],
        )

    def test_two_reviewers_inherit_e3_but_cannot_produce_e4(self):
        packets, keys, submissions = self._reviews(
            ("reviewer-1", "reviewer-2")
        )
        report = aggregate_human_review(
            self.evaluations,
            packets,
            keys,
            submissions,
            replicate_report=self.replicate_report,
            run_directories=self.runs,
        )

        self.assertFalse(report["e4_ready"])
        self.assertEqual(report["evidence"]["level"], "E3")

    def test_disjoint_reviewer_samples_cannot_be_combined_into_e4(self):
        packets, keys, submissions = self._reviews(
            ("reviewer-1", "reviewer-2", "reviewer-3"),
            seeds=(1, 2, 3),
        )
        report = aggregate_human_review(
            self.evaluations,
            packets,
            keys,
            submissions,
            replicate_report=self.replicate_report,
            run_directories=self.runs,
        )

        self.assertFalse(report["e4_ready"])
        self.assertLess(
            report["fully_covered_case_count"],
            report["reviewed_case_count"],
        )
        self.assertEqual(report["evidence"]["level"], "E3")

    def test_submission_requires_direct_upstream_authority_binding(self):
        packets, keys, submissions = self._reviews(("reviewer",))
        submission = submissions[0]
        submission["authority"]["replicate_report_sha256"] = "0" * 64

        failures = validate_submission(packets[0], submission)

        self.assertIn("submission authority mismatch", failures)

    def test_submission_requires_complete_reasoned_decisions(self):
        packets, _, submissions = self._reviews(("reviewer",))
        submission = submissions[0]
        submission["decisions"][0]["reason"] = "short"
        self.assertTrue(validate_submission(packets[0], submission))

        for winner in ([], {}):
            with self.subTest(winner=winner):
                malformed = copy.deepcopy(submissions[0])
                malformed["decisions"][0]["winner"] = winner
                failures = validate_submission(packets[0], malformed)
                self.assertIn(
                    f"{malformed['decisions'][0]['item_id']}: invalid winner",
                    failures,
                )

        extreme = copy.deepcopy(submissions[0])
        extreme_packet = copy.deepcopy(packets[0])
        extreme_packet["protocol"]["sample_size"] = 10**5000
        failures = validate_submission(extreme_packet, extreme)
        self.assertTrue(failures)
        self.assertTrue(
            failures[0].startswith("malformed human-review submission:")
        )

    def test_rehashed_packet_cannot_bypass_blind_randomization_protocol(self):
        for mutation in ("not-blind", "forced-position", "changed-input"):
            with self.subTest(mutation=mutation):
                packets, keys, _ = self._reviews(("reviewer",))
                packet = packets[0]
                key = keys[0]
                if mutation == "not-blind":
                    packet["instructions"]["blind"] = False
                elif mutation == "forced-position":
                    item = packet["items"][2]
                    secret = next(
                        value
                        for value in key["items"]
                        if value["item_id"] == item["item_id"]
                    )
                    secret["optimized_label"] = (
                        "B" if secret["optimized_label"] == "A" else "A"
                    )
                    item["output_a"], item["output_b"] = (
                        item["output_b"],
                        item["output_a"],
                    )
                else:
                    packet["items"][2]["input_text"] += " tampered"
                packet["packet_sha256"] = hash_payload(packet, "packet_sha256")
                key["packet_sha256"] = packet["packet_sha256"]
                key["key_sha256"] = hash_payload(key, "key_sha256")
                submission = perfect_submission(packet, key)

                with self.assertRaisesRegex(
                    ValueError,
                    "instructions do not match|sampling and blind protocol",
                ):
                    aggregate_human_review(
                        self.evaluations,
                        [packet],
                        [key],
                        [submission],
                        replicate_report=self.replicate_report,
                        run_directories=self.runs,
                    )

    def test_tampered_rehashed_coverage_cannot_self_report_e4(self):
        packets, keys, submissions = self._reviews(
            ("reviewer-1", "reviewer-2", "reviewer-3")
        )
        report = aggregate_human_review(
            self.evaluations,
            packets,
            keys,
            submissions,
            replicate_report=self.replicate_report,
            run_directories=self.runs,
        )
        tampered = copy.deepcopy(report)
        tampered["case_coverage"][0]["reviewer_ids"].pop()
        tampered["case_coverage"][0]["reviewer_count"] = 2
        tampered["human_review_sha256"] = hash_payload(
            tampered,
            "human_review_sha256",
        )

        failures = validate_human_review_report(
            tampered,
            replicate_report=self.replicate_report,
        )

        self.assertTrue(failures)
        self.assertIn("human-review E4 status does not match coverage", failures)

    def test_rehashed_item_id_and_duplicate_case_cannot_inflate_e4_coverage(self):
        packets, keys, submissions = self._reviews(
            ("reviewer-1", "reviewer-2", "reviewer-3")
        )
        report = aggregate_human_review(
            self.evaluations,
            packets,
            keys,
            submissions,
            replicate_report=self.replicate_report,
            run_directories=self.runs,
        )
        tampered = copy.deepcopy(report)
        original_id = tampered["case_coverage"][0]["item_id"]
        replaced_id = tampered["case_coverage"][1]["item_id"]
        fake_id = f"{original_id}:duplicate"
        tampered["case_coverage"][1] = copy.deepcopy(tampered["case_coverage"][0])
        tampered["case_coverage"][1]["item_id"] = fake_id
        tampered["consensus"].pop(replaced_id)
        tampered["consensus"][fake_id] = tampered["consensus"][original_id]
        tampered["human_review_sha256"] = hash_payload(
            tampered,
            "human_review_sha256",
        )

        failures = validate_human_review_report(
            tampered,
            replicate_report=self.replicate_report,
        )

        self.assertTrue(any("item id does not match" in item for item in failures))
        self.assertTrue(any("duplicate human-review case" in item for item in failures))

    def test_resolved_and_unresolved_case_sets_must_be_disjoint(self):
        packets, keys, submissions = self._reviews(
            ("reviewer-1", "reviewer-2", "reviewer-3")
        )
        report = aggregate_human_review(
            self.evaluations,
            packets,
            keys,
            submissions,
            replicate_report=self.replicate_report,
            run_directories=self.runs,
        )
        tampered = copy.deepcopy(report)
        tampered["unresolved_cases"] = [next(iter(tampered["consensus"]))]
        tampered["human_review_sha256"] = hash_payload(
            tampered,
            "human_review_sha256",
        )

        failures = validate_human_review_report(
            tampered,
            replicate_report=self.replicate_report,
        )

        self.assertIn(
            "human-review resolved and unresolved case sets overlap",
            failures,
        )

    def test_reviewer_artifact_hashes_must_be_independent(self):
        packets, keys, submissions = self._reviews(
            ("reviewer-1", "reviewer-2", "reviewer-3")
        )
        report = aggregate_human_review(
            self.evaluations,
            packets,
            keys,
            submissions,
            replicate_report=self.replicate_report,
            run_directories=self.runs,
        )
        tampered = copy.deepcopy(report)
        tampered["review_artifacts"][1]["packet_sha256"] = tampered[
            "review_artifacts"
        ][0]["packet_sha256"]
        tampered["human_review_sha256"] = hash_payload(
            tampered,
            "human_review_sha256",
        )

        failures = validate_human_review_report(
            tampered,
            replicate_report=self.replicate_report,
        )

        self.assertIn(
            "human-review artifact hashes are not unique: packet_sha256",
            failures,
        )

    def test_authority_validator_rebuilds_review_from_source_plan(self):
        packets, keys, submissions = self._reviews(
            ("reviewer-1", "reviewer-2", "reviewer-3")
        )
        report = aggregate_human_review(
            self.evaluations,
            packets,
            keys,
            submissions,
            replicate_report=self.replicate_report,
            run_directories=self.runs,
        )
        root = Path(self.temporary_directory.name)
        replicate_path = root / "human-authority-replicate.json"
        replicate_path.write_text(
            json.dumps(self.replicate_report),
            encoding="utf-8",
        )
        reviews = []
        for index, (packet, key, submission) in enumerate(
            zip(packets, keys, submissions, strict=True),
            start=1,
        ):
            paths = {
                "packet": root / f"human-authority-packet-{index}.json",
                "key": root / f"human-authority-key-{index}.json",
                "submission": root / f"human-authority-submission-{index}.json",
            }
            for kind, path in paths.items():
                value = {
                    "packet": packet,
                    "key": key,
                    "submission": submission,
                }[kind]
                path.write_text(json.dumps(value), encoding="utf-8")
            reviews.append({kind: path.name for kind, path in paths.items()})
        plan = {
            "schema_version": "2.0.0",
            "replicate_report": replicate_path.name,
            "run_directories": [path.name for path in self.runs],
            "evaluations": [
                f"run-1/{domain}/evaluation.json" for domain in DOMAINS
            ],
            "reviews": reviews,
        }

        self.assertEqual(
            validate_human_review_authority(report, plan, root=root),
            [],
        )

        submission_path = root / reviews[0]["submission"]
        changed = json.loads(submission_path.read_text(encoding="utf-8"))
        changed["decisions"][0]["winner"] = "tie"
        submission_path.write_text(json.dumps(changed), encoding="utf-8")
        self.assertTrue(validate_human_review_authority(report, plan, root=root))

        replicate_path.write_text(
            '{"schema_version":"2.0.0","schema_version":"2.0.0"}',
            encoding="utf-8",
        )
        duplicate_failures = validate_human_review_authority(
            report,
            plan,
            root=root,
        )
        self.assertTrue(
            any("duplicate field" in failure for failure in duplicate_failures)
        )


if __name__ == "__main__":
    unittest.main()
