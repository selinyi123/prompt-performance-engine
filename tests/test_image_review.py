import binascii
import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

from prompt_performance_engine.hashing import hash_payload, sha256_json
from prompt_performance_engine.image_review import (
    aggregate_visual_review,
    build_generation_manifest,
    build_reviewer_profile,
    create_visual_review_packet,
    deliver_visual_review_assets,
    inspect_png,
    load_visual_review_plan,
    validate_generation_manifest,
    validate_reviewer_profile,
    validate_visual_review_key,
    validate_visual_review_authority,
    validate_visual_review_packet,
    validate_visual_submission,
)


PACKAGE_ROOT = Path(__file__).resolve().parents[1]


class TrustedImageGenerationReceipts:
    def verify(self, **context):
        return sha256_json(context)


class TrustedVisualReviewerReceipts:
    def verify(self, **context):
        return sha256_json(context)


TRUSTED_GENERATION_RECEIPTS = TrustedImageGenerationReceipts()
TRUSTED_VISUAL_REVIEWERS = TrustedVisualReviewerReceipts()


def png_chunk(name: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + name
        + payload
        + struct.pack(">I", binascii.crc32(name + payload) & 0xFFFFFFFF)
    )


def write_test_png(path: Path, *, phase: int) -> None:
    width = height = 256
    rows = bytearray()
    for y in range(height):
        rows.append(0)
        for x in range(width):
            rows.extend(
                (
                    (x + phase * 17) % 256,
                    (y * 3 + phase * 29) % 256,
                    (x + y + phase * 41) % 256,
                )
            )
    data = (
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(
            b"IHDR",
            struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0),
        )
        + png_chunk(b"IDAT", zlib.compress(bytes(rows), 6))
        + png_chunk(b"IEND", b"")
    )
    path.write_bytes(data)


def generation_plan(root: Path) -> dict:
    cases = []
    for index in range(5):
        baseline = root / f"case-{index}-baseline.png"
        optimized = root / f"case-{index}-optimized.png"
        write_test_png(baseline, phase=index)
        write_test_png(optimized, phase=index + 20)
        cases.append(
            {
                "case_id": f"ig-{index}",
                "input_text": f"Create substantive benchmark image {index}.",
                "rubric": ["Subject fidelity", "Composition", "Lighting"],
                "baseline": {
                    "prompt": f"Baseline image prompt {index}",
                    "path": baseline.name,
                    "call_id": f"baseline-call-{index}",
                },
                "optimized": {
                    "prompt": f"Optimized image prompt {index}",
                    "path": optimized.name,
                    "call_id": f"optimized-call-{index}",
                },
            }
        )
    return {
        "schema_version": "2.0.0",
        "suite_id": "image-suite",
        "generator": {
            "provider": "test-provider",
            "model": "test-image-model",
            "settings": {"size": "256x256"},
        },
        "cases": cases,
    }


def perfect_submission(packet: dict, key: dict) -> dict:
    labels = {item["case_id"]: item["optimized_label"] for item in key["items"]}
    decisions = []
    for item in packet["items"]:
        optimized = labels[item["case_id"]]
        baseline = "B" if optimized == "A" else "A"
        scores = {
            criterion: {optimized: 5, baseline: 3}
            for criterion in item["rubric"]
        }
        decisions.append(
            {
                "case_id": item["case_id"],
                "winner": optimized,
                "reason": "The selected image satisfies every visual criterion more clearly.",
                "scores": scores,
            }
        )
    submission = {
        "schema_version": "2.0.0",
        "reviewer_id": packet["reviewer_id"],
        "packet_sha256": packet["packet_sha256"],
        "decisions": decisions,
    }
    submission["submission_sha256"] = hash_payload(
        submission,
        "submission_sha256",
    )
    return submission


class ImageReviewTests(unittest.TestCase):
    def test_visual_review_schemas_define_closed_nested_contracts(self):
        manifest_schema = json.loads(
            (PACKAGE_ROOT / "schemas" / "image-generation-manifest.schema.json").read_text(
                encoding="utf-8"
            )
        )
        packet_schema = json.loads(
            (PACKAGE_ROOT / "schemas" / "visual-review-packet.schema.json").read_text(
                encoding="utf-8"
            )
        )
        submission_schema = json.loads(
            (
                PACKAGE_ROOT
                / "schemas"
                / "visual-review-submission.schema.json"
            ).read_text(encoding="utf-8")
        )
        profile_schema = json.loads(
            (PACKAGE_ROOT / "schemas" / "visual-reviewer-profile.schema.json").read_text(
                encoding="utf-8"
            )
        )
        key_schema = json.loads(
            (PACKAGE_ROOT / "schemas" / "visual-review-key.schema.json").read_text(
                encoding="utf-8"
            )
        )
        plan_schema = json.loads(
            (PACKAGE_ROOT / "schemas" / "visual-review-plan.schema.json").read_text(
                encoding="utf-8"
            )
        )

        manifest_case = manifest_schema["properties"]["cases"]["items"]
        self.assertFalse(manifest_schema["properties"]["generator"]["additionalProperties"])
        self.assertFalse(manifest_case["additionalProperties"])
        self.assertFalse(manifest_schema["$defs"]["asset"]["additionalProperties"])
        self.assertFalse(
            packet_schema["properties"]["instructions"]["additionalProperties"]
        )
        self.assertEqual(
            packet_schema["properties"]["instructions"]["properties"][
                "winner_values"
            ]["const"],
            ["A", "B", "tie"],
        )
        self.assertFalse(
            submission_schema["$defs"]["scorePair"]["additionalProperties"]
        )
        self.assertFalse(
            profile_schema["properties"]["qualification"]["additionalProperties"]
        )
        self.assertIn("seed", key_schema["required"])
        self.assertIn("generation_manifest_sha256", key_schema["required"])
        self.assertEqual(
            packet_schema["properties"]["protocol"]["properties"]["name"][
                "const"
            ],
            "balanced_hmac_sha256_v2",
        )
        self.assertEqual(
            set(plan_schema["properties"]),
            {"schema_version", "report_id", "generation_manifest", "reviews"},
        )

    def test_png_requires_valid_pixels_and_crc(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "image.png"
            write_test_png(path, phase=3)
            metadata = inspect_png(path)
            self.assertEqual(metadata["width"], 256)
            damaged = bytearray(path.read_bytes())
            damaged[-8] ^= 1
            path.write_bytes(damaged)
            with self.assertRaisesRegex(ValueError, "CRC mismatch"):
                inspect_png(path)

    def test_runtime_accepts_schema_integer_numbers_written_with_fraction(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = build_generation_manifest(generation_plan(root), root=root)
            for case in manifest["cases"]:
                for name in ("baseline", "optimized"):
                    asset = case[name]
                    for field in ("width", "height", "byte_count"):
                        asset[field] = float(asset[field])
            manifest["generation_manifest_sha256"] = hash_payload(
                manifest,
                "generation_manifest_sha256",
            )
            self.assertEqual([], validate_generation_manifest(manifest, root=root))

            packet, key = create_visual_review_packet(
                manifest,
                root=root,
                reviewer_id="reviewer-1",
                seed=7.0,
            )
            self.assertEqual([], validate_visual_review_packet(packet))
            self.assertEqual([], validate_visual_review_key(manifest, packet, key))

            submission = perfect_submission(packet, key)
            for decision in submission["decisions"]:
                for pair in decision["scores"].values():
                    pair["A"] = float(pair["A"])
                    pair["B"] = float(pair["B"])
            submission["submission_sha256"] = hash_payload(
                submission,
                "submission_sha256",
            )
            self.assertEqual([], validate_visual_submission(packet, submission))

            profile = build_reviewer_profile(
                "reviewer-1",
                visual_review_experience_years=3,
                relevant_domains=["image_generation"],
                independent=True,
                conflict_disclosed=True,
            )
            profile["qualification"]["visual_review_experience_years"] = 3.0
            profile["profile_sha256"] = hash_payload(profile, "profile_sha256")
            self.assertEqual([], validate_reviewer_profile(profile))

    def test_png_rejects_excessive_declared_dimensions_before_decompression(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "oversized.png"
            path.write_bytes(
                b"\x89PNG\r\n\x1a\n"
                + png_chunk(
                    b"IHDR",
                    struct.pack(">IIBBBBB", 10_000, 10_000, 8, 2, 0, 0, 0),
                )
                + png_chunk(b"IDAT", zlib.compress(b"\x00"))
                + png_chunk(b"IEND", b"")
            )
            with self.assertRaisesRegex(ValueError, "16-megapixel"):
                inspect_png(path)

    def test_manifest_detects_replaced_image(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = build_generation_manifest(
                generation_plan(root),
                root=root,
            )
            write_test_png(root / "case-0-optimized.png", phase=99)
            failures = validate_generation_manifest(manifest, root=root)
            self.assertTrue(
                any("ig-0:optimized: sha256 mismatch" in item for item in failures)
            )

    def test_submission_requires_complete_rubric_scores(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = build_generation_manifest(
                generation_plan(root),
                root=root,
            )
            packet, key = create_visual_review_packet(
                manifest,
                root=root,
                reviewer_id="reviewer-1",
            )
            submission = perfect_submission(packet, key)
            submission["decisions"][0]["scores"].pop("Lighting")
            failures = validate_visual_submission(packet, submission)
            self.assertIn(
                "ig-0: rubric scores are incomplete",
                failures,
            )

    def test_blind_packet_uses_opaque_delivered_asset_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            packet_root = root / "packet"
            manifest = build_generation_manifest(
                generation_plan(root),
                root=root,
            )
            packet, key = create_visual_review_packet(
                manifest,
                root=root,
                reviewer_id="reviewer-1",
                seed=19,
            )
            delivered = deliver_visual_review_assets(
                key,
                source_root=root,
                packet_root=packet_root,
            )
            self.assertEqual(len(delivered), 10)
            for item in packet["items"]:
                for label in ("image_a", "image_b"):
                    asset = item[label]
                    self.assertNotIn("baseline", asset["path"])
                    self.assertNotIn("optimized", asset["path"])
                    delivered_path = packet_root / asset["path"]
                    self.assertTrue(delivered_path.is_file())
                    self.assertEqual(inspect_png(delivered_path)["sha256"], asset["sha256"])

    def test_same_public_inputs_get_fresh_unpredictable_blinding_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = build_generation_manifest(generation_plan(root), root=root)
            first_packet, first_key = create_visual_review_packet(
                manifest,
                root=root,
                reviewer_id="reviewer-1",
                seed=0,
            )
            second_packet, second_key = create_visual_review_packet(
                manifest,
                root=root,
                reviewer_id="reviewer-1",
                seed=0,
            )

            self.assertNotEqual(
                first_key["protocol"]["blinding_key"],
                second_key["protocol"]["blinding_key"],
            )
            self.assertNotEqual(first_packet["packet_sha256"], second_packet["packet_sha256"])
            self.assertNotIn("seed", first_packet)
            self.assertNotIn("blinding_key", first_packet["protocol"])
            self.assertEqual(
                first_packet["protocol"]["blinding_key_sha256"],
                first_key["protocol"]["blinding_key_sha256"],
            )

    def test_reused_visual_blinding_key_cannot_count_as_independent_review(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = build_generation_manifest(generation_plan(root), root=root)
            packets = []
            keys = []
            submissions = []
            profiles = []
            with patch(
                "prompt_performance_engine.image_review.secrets.token_hex",
                return_value="a" * 64,
            ):
                for index in range(3):
                    reviewer = f"reviewer-{index}"
                    packet, key = create_visual_review_packet(
                        manifest,
                        root=root,
                        reviewer_id=reviewer,
                    )
                    packets.append(packet)
                    keys.append(key)
                    submissions.append(perfect_submission(packet, key))
                    profiles.append(
                        build_reviewer_profile(
                            reviewer,
                            visual_review_experience_years=3,
                            relevant_domains=["image_generation"],
                            independent=True,
                            conflict_disclosed=True,
                        )
                    )
            with self.assertRaisesRegex(ValueError, "blinding keys must be unique"):
                aggregate_visual_review(
                    manifest,
                    packets,
                    keys,
                    submissions,
                    profiles,
                    root=root,
                    report_id="reused-visual-key",
                    generation_receipt_verifier=TRUSTED_GENERATION_RECEIPTS,
                    reviewer_submission_verifier=TRUSTED_VISUAL_REVIEWERS,
                )

    def test_rehashed_packet_with_label_leaking_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = build_generation_manifest(
                generation_plan(root),
                root=root,
            )
            packet, _ = create_visual_review_packet(
                manifest,
                root=root,
                reviewer_id="reviewer-1",
            )
            packet["items"][0]["image_a"]["path"] = "images/case-baseline.png"
            packet["packet_sha256"] = hash_payload(packet, "packet_sha256")
            self.assertIn(
                "ig-0:image_a: asset path is not blind",
                validate_visual_review_packet(packet),
            )

    def test_submission_hash_tampering_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = build_generation_manifest(
                generation_plan(root),
                root=root,
            )
            packet, key = create_visual_review_packet(
                manifest,
                root=root,
                reviewer_id="reviewer-1",
            )
            submission = perfect_submission(packet, key)
            submission["decisions"][0]["reason"] += " Changed."
            self.assertIn(
                "submission hash mismatch",
                validate_visual_submission(packet, submission),
            )

    def test_validators_fail_closed_on_malformed_types_and_unknown_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = build_generation_manifest(generation_plan(root), root=root)
            packet, key = create_visual_review_packet(
                manifest,
                root=root,
                reviewer_id="reviewer-1",
                seed=11,
            )
            submission = perfect_submission(packet, key)
            profile = build_reviewer_profile(
                "reviewer-1",
                visual_review_experience_years=3,
                relevant_domains=["image_generation"],
                independent=True,
                conflict_disclosed=True,
            )

            self.assertTrue(validate_generation_manifest([], root=root))
            self.assertTrue(validate_visual_review_packet("not-an-object"))
            self.assertTrue(validate_visual_submission(packet, None))
            self.assertTrue(validate_reviewer_profile(3))
            self.assertTrue(validate_visual_review_key(manifest, packet, []))

            malformed_manifest = json.loads(json.dumps(manifest))
            malformed_manifest["cases"][0]["unexpected"] = True
            malformed_manifest["generation_manifest_sha256"] = hash_payload(
                malformed_manifest,
                "generation_manifest_sha256",
            )
            self.assertIn(
                "image case fields do not match the schema",
                validate_generation_manifest(malformed_manifest, root=root),
            )

            malformed_text = json.loads(json.dumps(manifest))
            malformed_text["cases"][0]["baseline"]["prompt"] = "\ud800"
            self.assertIn(
                "ig-0:baseline: prompt hash mismatch",
                validate_generation_manifest(malformed_text, root=root),
            )

            malformed_packet = json.loads(json.dumps(packet))
            malformed_packet["items"][0]["image_a"]["width"] = True
            malformed_packet["packet_sha256"] = hash_payload(
                malformed_packet,
                "packet_sha256",
            )
            self.assertIn(
                "ig-0:image_a: dimensions are invalid",
                validate_visual_review_packet(malformed_packet),
            )

            malformed_submission = json.loads(json.dumps(submission))
            first_criterion = next(iter(malformed_submission["decisions"][0]["scores"]))
            malformed_submission["decisions"][0]["scores"][first_criterion]["A"] = True
            malformed_submission["submission_sha256"] = hash_payload(
                malformed_submission,
                "submission_sha256",
            )
            self.assertIn(
                f"ig-0:{first_criterion}: invalid A/B scores",
                validate_visual_submission(packet, malformed_submission),
            )

            for winner in ([], {}):
                with self.subTest(winner=winner):
                    wrong_winner = json.loads(json.dumps(submission))
                    wrong_winner["decisions"][0]["winner"] = winner
                    wrong_winner["submission_sha256"] = hash_payload(
                        wrong_winner,
                        "submission_sha256",
                    )
                    self.assertIn(
                        "ig-0: invalid winner",
                        validate_visual_submission(packet, wrong_winner),
                    )

            malformed_key = json.loads(json.dumps(key))
            malformed_key["items"][0]["optimized_label"] = []
            malformed_key["key_sha256"] = hash_payload(
                malformed_key,
                "key_sha256",
            )
            self.assertIn(
                "ig-0: optimized label is invalid",
                validate_visual_review_key(manifest, packet, malformed_key),
            )

            malformed_profile = json.loads(json.dumps(profile))
            malformed_profile["qualification"][
                "visual_review_experience_years"
            ] = "three"
            malformed_profile["profile_sha256"] = hash_payload(
                malformed_profile,
                "profile_sha256",
            )
            self.assertIn(
                "reviewer has fewer than two years visual experience",
                validate_reviewer_profile(malformed_profile),
            )

    def test_rehashed_visual_key_tampering_fails_replay_binding(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = build_generation_manifest(generation_plan(root), root=root)
            packet, key = create_visual_review_packet(
                manifest,
                root=root,
                reviewer_id="reviewer-1",
                seed=19,
            )
            self.assertEqual([], validate_visual_review_key(manifest, packet, key))

            tampered = json.loads(json.dumps(key))
            tampered["items"][0]["optimized_label"] = (
                "B" if tampered["items"][0]["optimized_label"] == "A" else "A"
            )
            tampered["items"][0]["deliveries"][0]["sha256"] = manifest["cases"][
                1
            ]["baseline"]["sha256"]
            tampered["key_sha256"] = hash_payload(tampered, "key_sha256")
            failures = validate_visual_review_key(manifest, packet, tampered)
            self.assertIn(
                "visual review key items do not match replayed hidden mapping",
                failures,
            )

            profile = build_reviewer_profile(
                "reviewer-1",
                visual_review_experience_years=3,
                relevant_domains=["image_generation"],
                independent=True,
                conflict_disclosed=True,
            )
            submission = perfect_submission(packet, key)
            with self.assertRaisesRegex(ValueError, "replayed hidden mapping"):
                aggregate_visual_review(
                    manifest,
                    [packet],
                    [tampered],
                    [submission],
                    [profile],
                    root=root,
                    report_id="tampered-key",
                )

    def test_three_qualified_reviewers_produce_image_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = build_generation_manifest(
                generation_plan(root),
                root=root,
            )
            packets = []
            keys = []
            submissions = []
            profiles = []
            for index in range(3):
                reviewer = f"reviewer-{index}"
                packet, key = create_visual_review_packet(
                    manifest,
                    root=root,
                    reviewer_id=reviewer,
                    seed=7,
                )
                packets.append(packet)
                keys.append(key)
                submissions.append(perfect_submission(packet, key))
                profiles.append(
                    build_reviewer_profile(
                        reviewer,
                        visual_review_experience_years=3,
                        relevant_domains=["image_generation", "creative_design"],
                        independent=True,
                        conflict_disclosed=True,
                    )
                )
            report = aggregate_visual_review(
                manifest,
                packets,
                keys,
                submissions,
                profiles,
                root=root,
                report_id="image-review-test",
                generation_receipt_verifier=TRUSTED_GENERATION_RECEIPTS,
                reviewer_submission_verifier=TRUSTED_VISUAL_REVIEWERS,
            )
            self.assertEqual(report["facts"]["generated_cases"], 5)
            self.assertEqual(report["facts"]["reviewed_cases"], 5)
            self.assertEqual(report["facts"]["qualified_reviewers"], 3)
            self.assertEqual(report["facts"]["wins"], 5)
            self.assertEqual(report["facts"]["unresolved_cases"], [])
            self.assertTrue(report["facts"]["asset_integrity_verified"])
            self.assertTrue(report["facts"]["generation_receipts_verified"])
            self.assertTrue(report["facts"]["reviewer_receipts_verified"])

            class DuplicateReviewerReceipts:
                def verify(self, **context):
                    return "b" * 64

            with self.assertRaisesRegex(ValueError, "receipts must be unique"):
                aggregate_visual_review(
                    manifest,
                    packets,
                    keys,
                    submissions,
                    profiles,
                    root=root,
                    report_id="duplicate-reviewer-receipts",
                    generation_receipt_verifier=TRUSTED_GENERATION_RECEIPTS,
                    reviewer_submission_verifier=DuplicateReviewerReceipts(),
                )

    def test_zero_reviewers_cannot_count_cases_as_reviewed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = build_generation_manifest(
                generation_plan(root),
                root=root,
            )
            report = aggregate_visual_review(
                manifest,
                [],
                [],
                [],
                [],
                root=root,
                report_id="image-generation-progress",
            )
            self.assertEqual(report["facts"]["generated_cases"], 5)
            self.assertEqual(report["facts"]["reviewed_cases"], 0)
            self.assertEqual(report["facts"]["qualified_reviewers"], 0)
            self.assertEqual(len(report["facts"]["unresolved_cases"]), 5)
            self.assertFalse(report["facts"]["generation_receipts_verified"])
            self.assertFalse(report["facts"]["reviewer_receipts_verified"])

    def test_visual_authority_replays_strict_plan_and_rejects_rehashed_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = build_generation_manifest(generation_plan(root), root=root)
            (root / "manifest.json").write_text(
                json.dumps(manifest),
                encoding="utf-8",
            )
            packets = []
            keys = []
            submissions = []
            profiles = []
            reviews = []
            for index in range(3):
                reviewer = f"reviewer-{index}"
                packet, key = create_visual_review_packet(
                    manifest,
                    root=root,
                    reviewer_id=reviewer,
                    seed=7,
                )
                submission = perfect_submission(packet, key)
                profile = build_reviewer_profile(
                    reviewer,
                    visual_review_experience_years=3,
                    relevant_domains=["image_generation"],
                    independent=True,
                    conflict_disclosed=True,
                )
                packets.append(packet)
                keys.append(key)
                submissions.append(submission)
                profiles.append(profile)
                entry = {}
                for name, payload in (
                    ("packet", packet),
                    ("key", key),
                    ("submission", submission),
                    ("profile", profile),
                ):
                    relative = f"{name}-{index}.json"
                    (root / relative).write_text(
                        json.dumps(payload),
                        encoding="utf-8",
                    )
                    entry[name] = relative
                reviews.append(entry)
            plan = {
                "schema_version": "2.0.0",
                "report_id": "visual-authority-test",
                "generation_manifest": "manifest.json",
                "reviews": reviews,
            }
            report = aggregate_visual_review(
                manifest,
                packets,
                keys,
                submissions,
                profiles,
                root=root,
                report_id=plan["report_id"],
                generation_receipt_verifier=TRUSTED_GENERATION_RECEIPTS,
                reviewer_submission_verifier=TRUSTED_VISUAL_REVIEWERS,
            )

            loaded = load_visual_review_plan(plan, root=root)
            self.assertEqual(loaded["report_id"], plan["report_id"])
            with self.assertRaisesRegex(ValueError, "fields do not match"):
                load_visual_review_plan(
                    {**plan, "unexpected": True},
                    root=root,
                )
            with self.assertRaisesRegex(ValueError, "escapes package root"):
                load_visual_review_plan(
                    {**plan, "generation_manifest": "../manifest.json"},
                    root=root,
                )
            self.assertEqual(
                [],
                validate_visual_review_authority(
                    report,
                    plan,
                    root=root,
                    generation_receipt_verifier=TRUSTED_GENERATION_RECEIPTS,
                    reviewer_submission_verifier=TRUSTED_VISUAL_REVIEWERS,
                ),
            )
            self.assertIn(
                "trusted image-generation receipt verifier is required",
                validate_visual_review_authority(
                    report,
                    plan,
                    root=root,
                ),
            )

            changed_profile = json.loads((root / "profile-0.json").read_text())
            changed_profile["qualification"]["visual_review_experience_years"] = 4
            changed_profile["profile_sha256"] = hash_payload(
                changed_profile,
                "profile_sha256",
            )
            (root / "profile-0.json").write_text(
                json.dumps(changed_profile),
                encoding="utf-8",
            )
            self.assertTrue(
                validate_visual_review_authority(
                    report,
                    plan,
                    root=root,
                    generation_receipt_verifier=TRUSTED_GENERATION_RECEIPTS,
                    reviewer_submission_verifier=TRUSTED_VISUAL_REVIEWERS,
                )
            )

    def test_duplicate_generation_receipts_are_rejected(self):
        class DuplicateReceipts:
            def verify(self, **context):
                return "a" * 64

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = build_generation_manifest(generation_plan(root), root=root)
            with self.assertRaisesRegex(ValueError, "receipts must be unique"):
                aggregate_visual_review(
                    manifest,
                    [],
                    [],
                    [],
                    [],
                    root=root,
                    report_id="duplicate-receipts",
                    generation_receipt_verifier=DuplicateReceipts(),
                )

    def test_unqualified_reviewer_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "fewer than two years"):
            build_reviewer_profile(
                "reviewer",
                visual_review_experience_years=1,
                relevant_domains=["image_generation"],
                independent=True,
                conflict_disclosed=True,
            )


if __name__ == "__main__":
    unittest.main()
