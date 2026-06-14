import binascii
import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from prompt_performance_engine.hashing import hash_payload
from prompt_performance_engine.image_review import (
    aggregate_visual_review,
    build_generation_manifest,
    build_reviewer_profile,
    create_visual_review_packet,
    deliver_visual_review_assets,
    inspect_png,
    validate_generation_manifest,
    validate_visual_review_packet,
    validate_visual_submission,
)


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
        "schema_version": "1.0.0",
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
        "schema_version": "1.0.0",
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
            )
            self.assertEqual(report["facts"]["generated_cases"], 5)
            self.assertEqual(report["facts"]["reviewed_cases"], 5)
            self.assertEqual(report["facts"]["qualified_reviewers"], 3)
            self.assertEqual(report["facts"]["wins"], 5)
            self.assertEqual(report["facts"]["unresolved_cases"], [])
            self.assertTrue(report["facts"]["asset_integrity_verified"])

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
