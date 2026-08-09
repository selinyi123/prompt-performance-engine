import hashlib
import json
import copy
import shutil
import binascii
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from prompt_performance_engine.hashing import hash_payload
from prompt_performance_engine.benchmark_replicates import (
    aggregate_benchmark_replicates,
)
from prompt_performance_engine.human_review import (
    aggregate_human_review as _aggregate_human_review,
    create_reviewer_packet as _create_reviewer_packet,
)
from prompt_performance_engine.readiness import (
    REQUIRED_EXPERT_DOMAINS,
    REQUIRED_RELEASE_DOMAINS,
    _release_domain_set_failure,
    assess_readiness as _assess_readiness,
    build_evidence_report,
    build_readiness_manifest,
    validate_readiness_authority as _validate_readiness_authority,
    validate_readiness_manifest,
    validate_readiness_report,
)
from prompt_performance_engine.image_review import (
    aggregate_visual_review,
    build_generation_manifest,
    build_reviewer_profile,
    create_visual_review_packet,
)
from prompt_performance_engine.software_evidence import build_code_execution_evidence
from prompt_performance_engine.profiles import load_profiles
from tests.test_benchmark_replicates import TRUSTED_FIXTURE_RECEIPTS, create_run
from tests.test_human_review import (
    DOMAINS,
    TRUSTED_FIXTURE_REVIEWERS,
    perfect_submission,
)
from tests.test_image_review import (
    TRUSTED_GENERATION_RECEIPTS,
    TRUSTED_VISUAL_REVIEWERS,
    generation_plan,
    perfect_submission as perfect_visual_submission,
)
from tests.test_software_evidence import IMAGE as SOFTWARE_IMAGE
from tests.test_software_evidence import software_evaluation, verified_sandbox


class RegisteredAttestationVerifier:
    def __init__(self, namespace: str) -> None:
        self.namespace = namespace
        self.expected_evidence_sha256: dict[tuple[str, str], str] = {}

    def register(self, evidence: dict) -> None:
        self.expected_evidence_sha256[
            (evidence["kind"], evidence["report_id"])
        ] = evidence["evidence_sha256"]

    def verify(self, *, evidence, context_sha256):
        key = (evidence.get("kind"), evidence.get("report_id"))
        if self.expected_evidence_sha256.get(key) != evidence.get(
            "evidence_sha256"
        ):
            return None
        return hashlib.sha256(
            f"{self.namespace}\0{context_sha256}".encode("utf-8")
        ).hexdigest()


class FixedAttestationVerifier:
    def __init__(self, receipt: str | None = None, *, raises: bool = False) -> None:
        self.receipt = receipt
        self.raises = raises

    def verify(self, *, evidence, context_sha256):
        del evidence, context_sha256
        if self.raises:
            raise RuntimeError("fixture verifier failure")
        return self.receipt


TRUSTED_REPRODUCTION_ATTESTATIONS = RegisteredAttestationVerifier("reproduction")
TRUSTED_CLAIMS_AUDITS = RegisteredAttestationVerifier("claims-audit")


def create_reviewer_packet(*args, **kwargs):
    kwargs.setdefault("model_receipt_verifier", TRUSTED_FIXTURE_RECEIPTS)
    return _create_reviewer_packet(*args, **kwargs)


def aggregate_human_review(*args, **kwargs):
    kwargs.setdefault("model_receipt_verifier", TRUSTED_FIXTURE_RECEIPTS)
    kwargs.setdefault("reviewer_submission_verifier", TRUSTED_FIXTURE_REVIEWERS)
    return _aggregate_human_review(*args, **kwargs)


def assess_readiness(*args, **kwargs):
    kwargs.setdefault("model_receipt_verifier", TRUSTED_FIXTURE_RECEIPTS)
    kwargs.setdefault("reviewer_submission_verifier", TRUSTED_FIXTURE_REVIEWERS)
    kwargs.setdefault("software_sandbox", verified_sandbox())
    kwargs.setdefault(
        "image_generation_receipt_verifier",
        TRUSTED_GENERATION_RECEIPTS,
    )
    kwargs.setdefault(
        "visual_reviewer_submission_verifier",
        TRUSTED_VISUAL_REVIEWERS,
    )
    kwargs.setdefault(
        "reproduction_attestation_verifier",
        TRUSTED_REPRODUCTION_ATTESTATIONS,
    )
    kwargs.setdefault("claims_audit_verifier", TRUSTED_CLAIMS_AUDITS)
    return _assess_readiness(*args, **kwargs)


def validate_readiness_authority(*args, **kwargs):
    kwargs.setdefault("model_receipt_verifier", TRUSTED_FIXTURE_RECEIPTS)
    kwargs.setdefault("reviewer_submission_verifier", TRUSTED_FIXTURE_REVIEWERS)
    kwargs.setdefault("software_sandbox", verified_sandbox())
    kwargs.setdefault(
        "image_generation_receipt_verifier",
        TRUSTED_GENERATION_RECEIPTS,
    )
    kwargs.setdefault(
        "visual_reviewer_submission_verifier",
        TRUSTED_VISUAL_REVIEWERS,
    )
    kwargs.setdefault(
        "reproduction_attestation_verifier",
        TRUSTED_REPRODUCTION_ATTESTATIONS,
    )
    kwargs.setdefault("claims_audit_verifier", TRUSTED_CLAIMS_AUDITS)
    return _validate_readiness_authority(*args, **kwargs)


def losing_visual_submission(packet: dict, key: dict) -> dict:
    submission = perfect_visual_submission(packet, key)
    optimized_labels = {
        item["case_id"]: item["optimized_label"] for item in key["items"]
    }
    for decision in submission["decisions"]:
        optimized = optimized_labels[decision["case_id"]]
        baseline = "B" if optimized == "A" else "A"
        decision["winner"] = baseline
        decision["reason"] = (
            "The baseline image satisfies every visual criterion more clearly."
        )
        decision["scores"] = {
            criterion: {optimized: 3, baseline: 5}
            for criterion in decision["scores"]
        }
    submission["submission_sha256"] = hash_payload(
        submission,
        "submission_sha256",
    )
    return submission


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


class ReadinessTests(unittest.TestCase):
    def test_required_expert_domains_exist_in_profile_registry(self):
        self.assertTrue(REQUIRED_EXPERT_DOMAINS.issubset(load_profiles()))
        self.assertEqual(
            REQUIRED_RELEASE_DOMAINS,
            set(load_profiles()) - {"generic"},
        )

    def test_arbitrary_twelve_domain_set_is_not_release_coverage(self):
        arbitrary = sorted(
            (REQUIRED_RELEASE_DOMAINS - {"marketing_sales"}) | {"generic"}
        )

        failure = _release_domain_set_failure(arbitrary)

        self.assertIsNotNone(failure)
        self.assertIn("missing=['marketing_sales']", failure)
        self.assertIn("unexpected=['generic']", failure)

    @classmethod
    def setUpClass(cls):
        cls.fixture_directory = tempfile.TemporaryDirectory()
        fixture_root = Path(cls.fixture_directory.name)
        runs = [
            create_run(fixture_root, f"run-{index}", DOMAINS)
            for index in range(1, 4)
        ]
        cls.runs = runs
        cls.replicate_report = aggregate_benchmark_replicates(
            runs,
            receipt_verifier=TRUSTED_FIXTURE_RECEIPTS,
        )
        evaluations = [
            json.loads(
                (runs[0] / domain / "evaluation.json").read_text(
                    encoding="utf-8"
                )
            )
            for domain in DOMAINS
        ]
        cls.evaluations = evaluations
        packets = []
        keys = []
        submissions = []
        for reviewer in ("reviewer-1", "reviewer-2", "reviewer-3"):
            packet, key = create_reviewer_packet(
                evaluations,
                replicate_report=cls.replicate_report,
                run_directories=runs,
                reviewer_id=reviewer,
                sample_size=24,
                seed=7,
            )
            packets.append(packet)
            keys.append(key)
            submissions.append(perfect_submission(packet, key))
        cls.human_review_report = aggregate_human_review(
            evaluations,
            packets,
            keys,
            submissions,
            replicate_report=cls.replicate_report,
            run_directories=runs,
        )
        cls.packets = packets
        cls.keys = keys
        cls.submissions = submissions

    @classmethod
    def tearDownClass(cls):
        cls.fixture_directory.cleanup()

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

    def _full_manifest(
        self,
        root: Path,
        *,
        visual_submission_factory=perfect_visual_submission,
    ) -> dict:
        specs: list[dict[str, str]] = []
        self._add(
            root,
            specs,
            "benchmark-replicates.json",
            "benchmark_replicate",
            copy.deepcopy(self.replicate_report),
        )
        self._add(
            root,
            specs,
            "human.json",
            "human_review",
            copy.deepcopy(self.human_review_report),
        )

        software_source = software_evaluation()
        write_json(root / "code-evaluation.json", software_source)
        code_report = build_code_execution_evidence(
            software_source,
            report_id="code_execution-release",
            sandbox=verified_sandbox(),
        )
        code_plan = {
            "schema_version": "2.0.0",
            "report_id": "code_execution-release",
            "evaluation": "code-evaluation.json",
            "sandbox_image": SOFTWARE_IMAGE,
        }
        write_json(root / "code-execution-plan.json", code_plan)

        image_manifest = build_generation_manifest(
            generation_plan(root),
            root=root,
        )
        write_json(root / "image-generation-manifest.json", image_manifest)
        visual_packets = []
        visual_keys = []
        visual_submissions = []
        visual_profiles = []
        visual_reviews = []
        for index, reviewer in enumerate(
            ("reviewer-1", "reviewer-2", "reviewer-3"),
            start=1,
        ):
            packet, key = create_visual_review_packet(
                image_manifest,
                root=root,
                reviewer_id=reviewer,
                seed=7,
            )
            submission = visual_submission_factory(packet, key)
            profile = build_reviewer_profile(
                reviewer,
                visual_review_experience_years=3,
                relevant_domains=["image_generation", "creative_design"],
                independent=True,
                conflict_disclosed=True,
            )
            visual_packets.append(packet)
            visual_keys.append(key)
            visual_submissions.append(submission)
            visual_profiles.append(profile)
            review_paths = {}
            for label, payload in (
                ("packet", packet),
                ("key", key),
                ("submission", submission),
                ("profile", profile),
            ):
                relative = f"visual-{label}-{index}.json"
                write_json(root / relative, payload)
                review_paths[label] = relative
            visual_reviews.append(review_paths)
        visual_report = aggregate_visual_review(
            image_manifest,
            visual_packets,
            visual_keys,
            visual_submissions,
            visual_profiles,
            root=root,
            report_id="image_review-release",
            generation_receipt_verifier=TRUSTED_GENERATION_RECEIPTS,
            reviewer_submission_verifier=TRUSTED_VISUAL_REVIEWERS,
        )
        visual_plan = {
            "schema_version": "2.0.0",
            "report_id": "image_review-release",
            "generation_manifest": "image-generation-manifest.json",
            "reviews": visual_reviews,
        }
        write_json(root / "visual-review-plan.json", visual_plan)

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
            "expert_review_coverage": {
                "domains": [
                    "creative_design",
                    "research_analysis",
                    "business_strategy",
                ],
                "blind": True,
                "qualified_reviewers": 3,
                "reviewer_ids": ["reviewer-1", "reviewer-2", "reviewer-3"],
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
            if kind == "claims_audit":
                TRUSTED_CLAIMS_AUDITS.register(payload)
            self._add(root, specs, f"{kind}.json", kind, payload)
        self._add(root, specs, "code_execution.json", "code_execution", code_report)
        self._add(root, specs, "image_review.json", "image_review", visual_report)

        for index in range(3):
            kind = "independent_reproduction"
            payload = build_evidence_report(
                kind=kind,
                report_id=f"reproduction-{index}",
                facts={
                    "machine_id_hash": hashlib.sha256(
                        f"machine-{index}".encode("utf-8")
                    ).hexdigest(),
                    "operator_id_hash": hashlib.sha256(
                        f"operator-{index}".encode("utf-8")
                    ).hexdigest(),
                    "install_passed": True,
                    "replay_passed": True,
                },
                provenance={"producer": f"operator-{index}"},
                limitations=["Fixture evidence for contract testing only."],
            )
            TRUSTED_REPRODUCTION_ATTESTATIONS.register(payload)
            self._add(root, specs, f"reproduction-{index}.json", kind, payload)
        authority_root = root / "authority"
        authority_root.mkdir()
        run_directories = []
        for run in self.runs:
            target = authority_root / run.name
            shutil.copytree(run, target)
            run_directories.append(target.relative_to(root).as_posix())
        reviews = []
        for index, (packet, key, submission) in enumerate(
            zip(self.packets, self.keys, self.submissions, strict=True),
            start=1,
        ):
            packet_path = authority_root / f"packet-{index}.json"
            key_path = authority_root / f"key-{index}.json"
            submission_path = authority_root / f"submission-{index}.json"
            write_json(packet_path, packet)
            write_json(key_path, key)
            write_json(submission_path, submission)
            reviews.append(
                {
                    "packet": packet_path.relative_to(root).as_posix(),
                    "key": key_path.relative_to(root).as_posix(),
                    "submission": submission_path.relative_to(root).as_posix(),
                }
            )
        first_run = authority_root / self.runs[0].name
        human_plan = {
            "schema_version": "2.0.0",
            "replicate_report": "benchmark-replicates.json",
            "run_directories": run_directories,
            "evaluations": [
                (first_run / domain / "evaluation.json").relative_to(root).as_posix()
                for domain in DOMAINS
            ],
            "reviews": reviews,
        }
        human_plan_path = root / "human-review-plan.json"
        write_json(human_plan_path, human_plan)
        return build_readiness_manifest(
            specs,
            expected_benchmark_suite_id="replicate-suite",
            expected_benchmark_definition_sha256=self.replicate_report[
                "benchmark_definition_sha256"
            ],
            benchmark_run_directories=run_directories,
            human_review_plan=human_plan_path.relative_to(root).as_posix(),
            code_execution_plan="code-execution-plan.json",
            visual_review_plan="visual-review-plan.json",
        )

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

    def test_manifest_validator_rejects_unknown_fields_and_invalid_types(self):
        valid_spec = {
            "kind": "claims_audit",
            "path": "claims.json",
            "sha256": "a" * 64,
        }
        mutations = (
            (
                "root field",
                lambda value: value.update({"schema_forbidden_nonce": True}),
                "readiness manifest fields do not match the schema",
            ),
            (
                "benchmark target field",
                lambda value: value["benchmark_target"].update(
                    {"schema_forbidden_nonce": True}
                ),
                "readiness benchmark target fields do not match the schema",
            ),
            (
                "authority source field",
                lambda value: value["authority_sources"].update(
                    {"schema_forbidden_nonce": True}
                ),
                "readiness authority source fields do not match the schema",
            ),
            (
                "artifact field",
                lambda value: value["artifacts"][0].update(
                    {"schema_forbidden_nonce": True}
                ),
                "artifact[0] fields do not match the schema",
            ),
            (
                "run directory type",
                lambda value: value["authority_sources"].update(
                    {"benchmark_run_directories": "run-1"}
                ),
                "readiness benchmark run directories are invalid",
            ),
            (
                "duplicate run directories",
                lambda value: value["authority_sources"].update(
                    {"benchmark_run_directories": ["run-1", "run-1"]}
                ),
                "readiness benchmark run directories are invalid",
            ),
        )
        for name, mutate, expected in mutations:
            with self.subTest(name=name):
                manifest = build_readiness_manifest([copy.deepcopy(valid_spec)])
                mutate(manifest)
                manifest["manifest_sha256"] = hash_payload(
                    manifest,
                    "manifest_sha256",
                )
                self.assertIn(expected, validate_readiness_manifest(manifest))

        with tempfile.TemporaryDirectory() as directory:
            manifest = build_readiness_manifest([])
            manifest["schema_forbidden_nonce"] = True
            manifest["manifest_sha256"] = hash_payload(
                manifest,
                "manifest_sha256",
            )
            with self.assertRaisesRegex(ValueError, "fields do not match the schema"):
                assess_readiness(manifest, root=Path(directory))

    def test_stale_replicate_report_cannot_satisfy_release_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self._full_manifest(root)
            manifest["benchmark_target"] = {
                "suite_id": "replicate-suite-v2",
                "definition_sha256": "c" * 64,
            }
            manifest["manifest_sha256"] = hash_payload(
                manifest,
                "manifest_sha256",
            )
            report = assess_readiness(manifest, root=root)

        by_id = {item["id"]: item for item in report["requirements"]}
        self.assertEqual(by_id["R03"]["status"], "partial")
        self.assertEqual(by_id["R04"]["status"], "partial")
        self.assertIn(
            "benchmark replicate suite does not match the release target",
            by_id["R03"]["failures"],
        )
        self.assertIn(
            "benchmark replicate definition hash does not match the release target",
            by_id["R04"]["failures"],
        )

    def test_authoritative_arbitrary_twelve_domains_cannot_satisfy_r03_or_r04(self):
        arbitrary = sorted(
            (REQUIRED_RELEASE_DOMAINS - {"marketing_sales"}) | {"generic"}
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runs = [
                create_run(root, f"arbitrary-run-{index}", arbitrary)
                for index in range(1, 4)
            ]
            replicate = aggregate_benchmark_replicates(
                runs,
                receipt_verifier=TRUSTED_FIXTURE_RECEIPTS,
            )
            self.assertTrue(replicate["aggregate"]["release_gate_passed"])
            replicate_path = root / "arbitrary-replicates.json"
            replicate_sha256 = write_json(replicate_path, replicate)
            manifest = build_readiness_manifest(
                [
                    {
                        "kind": "benchmark_replicate",
                        "path": replicate_path.name,
                        "sha256": replicate_sha256,
                    }
                ],
                expected_benchmark_suite_id=replicate["suite_id"],
                expected_benchmark_definition_sha256=replicate[
                    "benchmark_definition_sha256"
                ],
                benchmark_run_directories=[path.name for path in runs],
            )
            report = assess_readiness(manifest, root=root)

        by_id = {item["id"]: item for item in report["requirements"]}
        expected = (
            "completed domain set does not match the required release domains"
        )
        for requirement_id in ("R03", "R04"):
            self.assertEqual(by_id[requirement_id]["status"], "partial")
            self.assertTrue(
                any(
                    failure.startswith(expected)
                    for failure in by_id[requirement_id]["failures"]
                )
            )

    def test_manifest_rejects_unsupported_benchmark_artifact_kind(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self._full_manifest(root)
            benchmark_spec = next(
                item
                for item in manifest["artifacts"]
                if item["kind"] == "benchmark_replicate"
            )
            benchmark_spec["kind"] = "benchmark_summary"
            manifest["manifest_sha256"] = hash_payload(
                manifest,
                "manifest_sha256",
            )
            with self.assertRaisesRegex(ValueError, "unsupported kind"):
                assess_readiness(manifest, root=root)

    def test_all_ten_evidence_gates_are_required_for_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = assess_readiness(self._full_manifest(root), root=root)

        self.assertEqual(report["status"], "complete")
        self.assertEqual(report["claim_ceiling"], "stable_v1")
        self.assertEqual(report["passed_requirement_count"], 10)
        self.assertEqual(validate_readiness_report(report), [])

    def test_visual_review_losses_cannot_satisfy_r06(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self._full_manifest(
                root,
                visual_submission_factory=losing_visual_submission,
            )
            report = assess_readiness(manifest, root=root)

        requirement = next(
            item for item in report["requirements"] if item["id"] == "R06"
        )
        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(requirement["status"], "partial")
        self.assertIn(
            "optimized images do not win more reviewed cases than they lose",
            requirement["failures"],
        )

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

    def test_duplicate_field_in_evidence_source_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "claims-audit.json"
            path.write_text(
                '{"schema_version":"2.0.0","schema_version":"2.0.0"}\n',
                encoding="utf-8",
            )
            manifest = build_readiness_manifest(
                [
                    {
                        "kind": "claims_audit",
                        "path": path.name,
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    }
                ]
            )

            report = assess_readiness(manifest, root=root)

        self.assertEqual(report["status"], "incomplete")
        self.assertTrue(
            any(
                "claims_audit:claims-audit.json" in error
                and "duplicate field 'schema_version'" in error
                for error in report["evidence_errors"]
            )
        )

    def test_custom_evidence_envelope_is_exact_and_fails_closed(self):
        mutations = (
            ("non-object facts", lambda evidence: evidence.__setitem__("facts", [])),
            ("unknown root field", lambda evidence: evidence.__setitem__("unknown", True)),
            ("missing report id", lambda evidence: evidence.pop("report_id")),
        )
        for name, mutate in mutations:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                manifest = self._full_manifest(root)
                path = root / "claims_audit.json"
                evidence = json.loads(path.read_text(encoding="utf-8"))
                mutate(evidence)
                evidence["evidence_sha256"] = hash_payload(
                    evidence,
                    "evidence_sha256",
                )
                new_hash = write_json(path, evidence)
                next(
                    item
                    for item in manifest["artifacts"]
                    if item["kind"] == "claims_audit"
                )["sha256"] = new_hash
                manifest["manifest_sha256"] = hash_payload(
                    manifest,
                    "manifest_sha256",
                )

                report = assess_readiness(manifest, root=root)

                r10 = next(
                    item for item in report["requirements"] if item["id"] == "R10"
                )
                self.assertEqual(r10["status"], "failed")
                self.assertTrue(report["evidence_errors"])

    def test_expert_domains_must_be_a_string_array_not_mapping_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self._full_manifest(root)
            path = root / "expert_review_coverage.json"
            evidence = json.loads(path.read_text(encoding="utf-8"))
            evidence["facts"]["domains"] = {
                domain: True for domain in REQUIRED_EXPERT_DOMAINS
            }
            evidence["evidence_sha256"] = hash_payload(
                evidence,
                "evidence_sha256",
            )
            new_hash = write_json(path, evidence)
            next(
                item
                for item in manifest["artifacts"]
                if item["kind"] == "expert_review_coverage"
            )["sha256"] = new_hash
            manifest["manifest_sha256"] = hash_payload(
                manifest,
                "manifest_sha256",
            )

            report = assess_readiness(manifest, root=root)

        r07 = next(item for item in report["requirements"] if item["id"] == "R07")
        self.assertEqual(r07["status"], "failed")
        self.assertTrue(report["evidence_errors"])

    def test_reproduction_identity_hashes_require_sha256_strings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self._full_manifest(root)
            specs = {
                item["path"]: item
                for item in manifest["artifacts"]
                if item["kind"] == "independent_reproduction"
            }
            for index in range(3):
                path = root / f"reproduction-{index}.json"
                evidence = json.loads(path.read_text(encoding="utf-8"))
                evidence["facts"]["machine_id_hash"] = index + 1
                evidence["facts"]["operator_id_hash"] = [index + 1]
                evidence["evidence_sha256"] = hash_payload(
                    evidence,
                    "evidence_sha256",
                )
                specs[path.name]["sha256"] = write_json(path, evidence)
            manifest["manifest_sha256"] = hash_payload(
                manifest,
                "manifest_sha256",
            )

            report = assess_readiness(manifest, root=root)

        r08 = next(item for item in report["requirements"] if item["id"] == "R08")
        self.assertEqual(r08["status"], "failed")
        self.assertTrue(report["evidence_errors"])

    def test_self_hashed_reproduction_and_claims_require_host_verifiers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self._full_manifest(root)
            report = assess_readiness(
                manifest,
                root=root,
                reproduction_attestation_verifier=None,
                claims_audit_verifier=None,
            )

        by_id = {item["id"]: item for item in report["requirements"]}
        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(report["claim_ceiling"], "optimized_candidate")
        self.assertEqual(by_id["R08"]["status"], "partial")
        self.assertEqual(by_id["R10"]["status"], "partial")
        self.assertTrue(
            any(
                "trusted reproduction attestation verifier is required" in failure
                for failure in by_id["R08"]["failures"]
            )
        )
        self.assertTrue(
            any(
                "trusted claims audit verifier is required" in failure
                for failure in by_id["R10"]["failures"]
            )
        )

    def test_readiness_authority_rechecks_reproduction_and_claims_verifiers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self._full_manifest(root)
            report = assess_readiness(manifest, root=root)
            failures = validate_readiness_authority(
                report,
                manifest,
                root=root,
                reproduction_attestation_verifier=None,
                claims_audit_verifier=None,
            )

        self.assertIn(
            "readiness report does not match its source manifest",
            failures,
        )

    def test_reproduction_attestation_receipts_fail_closed(self):
        cases = (
            (
                "invalid",
                FixedAttestationVerifier("not-a-sha256"),
                "receipt is missing or invalid",
            ),
            (
                "duplicate",
                FixedAttestationVerifier("a" * 64),
                "receipt is reused",
            ),
            (
                "exception",
                FixedAttestationVerifier(raises=True),
                "verifier failed",
            ),
        )
        for name, verifier, expected in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                manifest = self._full_manifest(root)
                report = assess_readiness(
                    manifest,
                    root=root,
                    reproduction_attestation_verifier=verifier,
                )

                r08 = next(
                    item for item in report["requirements"] if item["id"] == "R08"
                )
                self.assertEqual(report["status"], "incomplete")
                self.assertEqual(r08["status"], "partial")
                self.assertTrue(
                    any(expected in failure for failure in r08["failures"]),
                    r08["failures"],
                )

    def test_claims_audit_receipts_fail_closed(self):
        cases = (
            (
                "invalid",
                FixedAttestationVerifier("not-a-sha256"),
                "receipt is missing or invalid",
            ),
            (
                "exception",
                FixedAttestationVerifier(raises=True),
                "verifier failed",
            ),
        )
        for name, verifier, expected in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                manifest = self._full_manifest(root)
                report = assess_readiness(
                    manifest,
                    root=root,
                    claims_audit_verifier=verifier,
                )

                r10 = next(
                    item for item in report["requirements"] if item["id"] == "R10"
                )
                self.assertEqual(report["status"], "incomplete")
                self.assertEqual(r10["status"], "partial")
                self.assertTrue(
                    any(expected in failure for failure in r10["failures"]),
                    r10["failures"],
                )

    def test_rehashed_attestation_tampering_still_fails_authority(self):
        mutations = (
            (
                "independent_reproduction",
                "reproduction-0.json",
                lambda evidence: evidence["facts"].__setitem__(
                    "machine_id_hash", "f" * 64
                ),
                "R08",
                "reproduction attestation receipt is missing or invalid",
            ),
            (
                "claims_audit",
                "claims_audit.json",
                lambda evidence: evidence["provenance"].__setitem__(
                    "producer", "forged-producer"
                ),
                "R10",
                "claims audit receipt is missing or invalid",
            ),
        )
        for kind, filename, mutate, requirement_id, expected in mutations:
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                manifest = self._full_manifest(root)
                path = root / filename
                evidence = json.loads(path.read_text(encoding="utf-8"))
                mutate(evidence)
                evidence["evidence_sha256"] = hash_payload(
                    evidence,
                    "evidence_sha256",
                )
                new_hash = write_json(path, evidence)
                next(
                    item
                    for item in manifest["artifacts"]
                    if item["kind"] == kind and item["path"] == filename
                )["sha256"] = new_hash
                manifest["manifest_sha256"] = hash_payload(
                    manifest,
                    "manifest_sha256",
                )

                report = assess_readiness(manifest, root=root)

                requirement = next(
                    item
                    for item in report["requirements"]
                    if item["id"] == requirement_id
                )
                self.assertEqual(report["status"], "incomplete")
                self.assertEqual(requirement["status"], "partial")
                self.assertTrue(
                    any(expected in failure for failure in requirement["failures"]),
                    requirement["failures"],
                )

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

    def test_rehashed_detached_r05_and_r06_reports_fail_source_replay(self):
        for kind, mutate, requirement_id, expected in (
            (
                "code_execution",
                lambda report: report["provenance"].__setitem__(
                    "suite_id", "forged-suite"
                ),
                "R05",
                "does not match its source plan",
            ),
            (
                "image_review",
                lambda report: report["provenance"]["generation_artifacts"][0].__setitem__(
                    "generation_receipt_sha256", "f" * 64
                ),
                "R06",
                "does not match its source plan",
            ),
        ):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                manifest = self._full_manifest(root)
                path = root / f"{kind}.json"
                evidence = json.loads(path.read_text(encoding="utf-8"))
                mutate(evidence)
                evidence["evidence_sha256"] = hash_payload(
                    evidence,
                    "evidence_sha256",
                )
                new_hash = write_json(path, evidence)
                next(
                    item for item in manifest["artifacts"] if item["kind"] == kind
                )["sha256"] = new_hash
                manifest["manifest_sha256"] = hash_payload(
                    manifest,
                    "manifest_sha256",
                )

                report = assess_readiness(manifest, root=root)

                requirement = next(
                    item
                    for item in report["requirements"]
                    if item["id"] == requirement_id
                )
                self.assertEqual(requirement["status"], "partial")
                self.assertTrue(
                    any(expected in failure for failure in requirement["failures"])
                )

    def test_report_hash_and_derived_status_are_validated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = assess_readiness(self._full_manifest(root), root=root)
        report["claim_ceiling"] = "optimized_candidate"
        report["readiness_sha256"] = hash_payload(report, "readiness_sha256")

        failures = validate_readiness_report(report)

        self.assertIn("claim ceiling does not match readiness status", failures)

    def test_report_accepts_json_schema_mathematical_integers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = assess_readiness(self._full_manifest(root), root=root)
        report["passed_requirement_count"] = float(
            report["passed_requirement_count"]
        )
        report["requirement_count"] = float(report["requirement_count"])
        report["readiness_sha256"] = hash_payload(report, "readiness_sha256")

        self.assertEqual(validate_readiness_report(report), [])

    def test_report_validator_rejects_malformed_root_and_derived_fields(self):
        self.assertEqual(
            validate_readiness_report([]),
            ["readiness report root must be an object"],
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = assess_readiness(self._full_manifest(root), root=root)

        malformed_errors = copy.deepcopy(report)
        malformed_errors["evidence_errors"] = "forged"
        malformed_errors["readiness_sha256"] = hash_payload(
            malformed_errors,
            "readiness_sha256",
        )
        self.assertIn(
            "readiness evidence error list is invalid",
            validate_readiness_report(malformed_errors),
        )

        malformed_status = copy.deepcopy(report)
        malformed_status["status"] = "almost-complete"
        malformed_status["readiness_sha256"] = hash_payload(
            malformed_status,
            "readiness_sha256",
        )
        self.assertIn(
            "readiness status is invalid",
            validate_readiness_report(malformed_status),
        )

        unknown_root = copy.deepcopy(report)
        unknown_root["schema_forbidden_nonce"] = True
        unknown_root["readiness_sha256"] = hash_payload(
            unknown_root,
            "readiness_sha256",
        )
        self.assertIn(
            "readiness report fields do not match the schema",
            validate_readiness_report(unknown_root),
        )

        unknown_requirement = copy.deepcopy(report)
        unknown_requirement["requirements"][0]["schema_forbidden_nonce"] = True
        unknown_requirement["readiness_sha256"] = hash_payload(
            unknown_requirement,
            "readiness_sha256",
        )
        self.assertIn(
            "R01: readiness requirement fields are invalid",
            validate_readiness_report(unknown_requirement),
        )

    def test_authority_validator_rejects_non_object_manifest_without_traceback(self):
        manifest = build_readiness_manifest([])
        report = assess_readiness(manifest, root=Path.cwd())

        for malformed in ([], None):
            with self.subTest(malformed=malformed):
                self.assertEqual(
                    validate_readiness_authority(
                        report,
                        malformed,
                        root=Path.cwd(),
                    ),
                    ["readiness manifest root must be an object"],
                )

    def test_authority_validation_replays_the_source_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self._full_manifest(root)
            report = assess_readiness(manifest, root=root)
            submission_path = root / "authority" / "submission-1.json"
            submission = json.loads(submission_path.read_text(encoding="utf-8"))
            submission["decisions"][0]["winner"] = "tie"
            write_json(submission_path, submission)

            failures = validate_readiness_authority(
                report,
                manifest,
                root=root,
            )

        self.assertTrue(failures)
        self.assertTrue(
            any("source manifest" in failure for failure in failures)
        )

    def test_expert_identity_must_match_human_reviewers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self._full_manifest(root)
            expert_path = root / "expert_review_coverage.json"
            expert = json.loads(expert_path.read_text(encoding="utf-8"))
            expert["facts"]["reviewer_ids"] = ["other-1", "other-2", "other-3"]
            expert["evidence_sha256"] = hash_payload(expert, "evidence_sha256")
            new_hash = write_json(expert_path, expert)
            for item in manifest["artifacts"]:
                if item["kind"] == "expert_review_coverage":
                    item["sha256"] = new_hash
            manifest["manifest_sha256"] = hash_payload(
                manifest,
                "manifest_sha256",
            )

            report = assess_readiness(manifest, root=root)

        r07 = next(item for item in report["requirements"] if item["id"] == "R07")
        self.assertEqual(r07["status"], "partial")
        self.assertIn(
            "expert reviewer identities do not match human review",
            r07["failures"],
        )


if __name__ == "__main__":
    unittest.main()
