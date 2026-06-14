import hashlib
import json
import binascii
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from prompt_performance_engine.hashing import hash_payload
from prompt_performance_engine.readiness import (
    assess_readiness,
    build_evidence_report,
    build_readiness_manifest,
    validate_readiness_report,
)


def write_json(path: Path, payload: dict) -> str:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_png(path: Path, phase: int) -> dict:
    width = height = 256
    rows = bytearray()
    for y in range(height):
        rows.append(0)
        for x in range(width):
            rows.extend(((x + phase) % 256, (y * 3) % 256, (x + y) % 256))

    def chunk(name: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + name
            + payload
            + struct.pack(">I", binascii.crc32(name + payload) & 0xFFFFFFFF)
        )

    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(
            b"IHDR",
            struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0),
        )
        + chunk(b"IDAT", zlib.compress(bytes(rows)))
        + chunk(b"IEND", b"")
    )
    return {
        "path": path.name,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "width": width,
        "height": height,
    }


def benchmark_summary() -> dict:
    domains = {
        f"domain-{index}": {
            "case_count": 5,
            "wins": 3,
            "ties": 2,
            "losses": 0,
            "critical_regressions": 0,
            "fatal_flaws": 0,
            "gate_passed": True,
        }
        for index in range(12)
    }
    report = {
        "schema_version": "1.0.0",
        "suite_id": "release-suite",
        "completed_domains": sorted(domains),
        "domain_count": 12,
        "case_count": 60,
        "wins": 36,
        "ties": 24,
        "losses": 0,
        "net_improvement": 0.6,
        "critical_regressions": 0,
        "fatal_flaws": 0,
        "optimized_hard_failures": 0,
        "all_domains_pass": True,
        "aggregate_gate_passed": True,
        "usage": {"actual_model_calls": 100},
        "domain_results": domains,
    }
    report["summary_sha256"] = hash_payload(report, "summary_sha256")
    return report


def human_review() -> dict:
    report = {
        "schema_version": "1.0.0",
        "reviewer_count": 3,
        "reviewed_case_count": 24,
        "unresolved_cases": [],
        "e4_ready": True,
    }
    report["human_review_sha256"] = hash_payload(
        report,
        "human_review_sha256",
    )
    return report


class ReadinessTests(unittest.TestCase):
    def _add(
        self,
        root: Path,
        specs: list[dict[str, str]],
        filename: str,
        kind: str,
        payload: dict,
    ) -> None:
        path = root / filename
        sha256 = write_json(path, payload)
        specs.append({"kind": kind, "path": filename, "sha256": sha256})

    def _full_manifest(self, root: Path) -> dict:
        specs: list[dict[str, str]] = []
        self._add(
            root,
            specs,
            "benchmark.json",
            "benchmark_summary",
            benchmark_summary(),
        )
        self._add(
            root,
            specs,
            "human.json",
            "human_review",
            human_review(),
        )

        image_cases = []
        for index in range(5):
            baseline = write_png(root / f"baseline-{index}.png", index)
            optimized = write_png(root / f"optimized-{index}.png", index + 20)
            image_cases.append(
                {
                    "case_id": f"image-{index}",
                    "baseline": {
                        **baseline,
                        "call_id": f"baseline-call-{index}",
                    },
                    "optimized": {
                        **optimized,
                        "call_id": f"optimized-call-{index}",
                    },
                    "review_count": 3,
                    "consensus": "win",
                    "mean_optimized_score_delta": 1.0,
                }
            )

        reports = {
            "operational_verification": {
                "behavior_tests_passed": True,
                "release_validator_passed": True,
                "cli_passed": True,
                "api_passed": True,
                "service_passed": True,
                "package_install_passed": True,
                "documentation_verified": True,
            },
            "code_execution": {
                "eligible_cases": 5,
                "executed_cases": 5,
                "passed_cases": 5,
                "executable_cases": 4,
                "sandboxed_cases": 4,
                "sandboxed": True,
                "sandbox": {
                    "backend": "docker",
                    "image_reference": (
                        "python:3.13-alpine@sha256:" + "a" * 64
                    ),
                    "image_id": "sha256:" + "a" * 64,
                    "policy_verified": True,
                    "isolation_probe_passed": True,
                    "isolation_facts": {
                        "network_blocked": True,
                        "root_read_only": True,
                        "tmp_writable": True,
                        "non_root": True,
                    },
                    "resource_limits_verified": True,
                },
            },
            "image_review": {
                "eligible_cases": 5,
                "generated_cases": 5,
                "reviewed_cases": 5,
                "qualified_reviewers": 3,
                "blind": True,
                "asset_integrity_verified": True,
                "review_coverage_verified": True,
                "unresolved_cases": [],
                "cases": image_cases,
            },
            "expert_review_coverage": {
                "domains": [
                    "creative_design",
                    "research_synthesis",
                    "business_strategy",
                ],
                "blind": True,
                "qualified_reviewers": 3,
            },
            "defect_register": {
                "open_p0": 0,
                "open_p1": 0,
                "triage_complete": True,
            },
            "claims_audit": {
                "unsupported_claims": 0,
                "all_claims_artifact_bound": True,
                "documentation_scanned": True,
            },
        }
        for kind, facts in reports.items():
            payload = build_evidence_report(
                kind=kind,
                report_id=f"{kind}-release",
                facts=facts,
                provenance={"producer": "test"},
                limitations=["Fixture evidence for contract testing only."],
            )
            self._add(root, specs, f"{kind}.json", kind, payload)

        for index in range(3):
            kind = "independent_reproduction"
            payload = build_evidence_report(
                kind=kind,
                report_id=f"reproduction-{index}",
                facts={
                    "machine_id_hash": f"machine-{index}",
                    "operator_id_hash": f"operator-{index}",
                    "install_passed": True,
                    "replay_passed": True,
                },
                provenance={"producer": f"operator-{index}"},
                limitations=["Fixture evidence for contract testing only."],
            )
            self._add(root, specs, f"reproduction-{index}.json", kind, payload)
        return build_readiness_manifest(specs)

    def test_empty_evidence_cannot_claim_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            report = assess_readiness(
                build_readiness_manifest([]),
                root=Path(directory),
            )

        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(report["claim_ceiling"], "optimized_candidate")
        self.assertEqual(report["passed_requirement_count"], 0)
        self.assertEqual(validate_readiness_report(report), [])

    def test_all_ten_evidence_gates_are_required_for_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = assess_readiness(self._full_manifest(root), root=root)

        self.assertEqual(report["status"], "complete")
        self.assertEqual(report["claim_ceiling"], "stable_v1")
        self.assertEqual(report["passed_requirement_count"], 10)
        self.assertEqual(validate_readiness_report(report), [])

    def test_text_and_human_results_do_not_replace_code_or_image_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self._full_manifest(root)
            manifest["artifacts"] = [
                item
                for item in manifest["artifacts"]
                if item["kind"] not in {"code_execution", "image_review"}
            ]
            manifest["manifest_sha256"] = hash_payload(
                manifest,
                "manifest_sha256",
            )
            report = assess_readiness(manifest, root=root)

        by_id = {item["id"]: item for item in report["requirements"]}
        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(by_id["R05"]["status"], "missing")
        self.assertEqual(by_id["R06"]["status"], "missing")

    def test_tampered_evidence_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self._full_manifest(root)
            code_path = root / "code_execution.json"
            code_path.write_text("{}\n", encoding="utf-8")
            report = assess_readiness(manifest, root=root)

        by_id = {item["id"]: item for item in report["requirements"]}
        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(by_id["R05"]["status"], "failed")
        self.assertTrue(report["evidence_errors"])

    def test_sandbox_boolean_without_runtime_proof_is_partial(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self._full_manifest(root)
            code_path = root / "code_execution.json"
            code = json.loads(code_path.read_text(encoding="utf-8"))
            code["facts"]["sandbox"] = {}
            code["evidence_sha256"] = hash_payload(
                code,
                "evidence_sha256",
            )
            new_hash = write_json(code_path, code)
            for item in manifest["artifacts"]:
                if item["kind"] == "code_execution":
                    item["sha256"] = new_hash
            manifest["manifest_sha256"] = hash_payload(
                manifest,
                "manifest_sha256",
            )

            report = assess_readiness(manifest, root=root)

        r05 = next(
            item for item in report["requirements"] if item["id"] == "R05"
        )
        self.assertEqual(r05["status"], "partial")
        self.assertIn("Docker sandbox backend is not proven", r05["failures"])

    def test_report_hash_and_derived_status_are_validated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = assess_readiness(self._full_manifest(root), root=root)
        report["claim_ceiling"] = "optimized_candidate"
        report["readiness_sha256"] = hash_payload(report, "readiness_sha256")

        failures = validate_readiness_report(report)

        self.assertIn("claim ceiling does not match readiness status", failures)


if __name__ == "__main__":
    unittest.main()
