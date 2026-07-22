import copy
import hashlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from prompt_performance_engine.cli import main
from prompt_performance_engine.evaluation import (
    EvaluationCase,
    ExecutionConfig,
    JudgeDecision,
    RecordedExecutor,
    RecordedJudge,
    evaluate_suite,
)
from prompt_performance_engine.hashing import hash_payload
from prompt_performance_engine.software_evidence import (
    build_code_execution_evidence,
    load_code_execution_plan,
    validate_code_execution_authority,
)
from prompt_performance_engine.software_sandbox import DockerSandbox, SandboxRun


IMAGE = (
    "python:3.13-alpine@sha256:"
    "db66119d6609a3a941a9433b225f4e13d33c459cede097cf3ec2fc4d1bd314b2"
)


def sandbox_run(
    *,
    passed: bool,
    detail: str,
    exit_code: int | None,
    timed_out: bool = False,
    oom_killed: bool = False,
    probe_facts: dict | None = None,
    exit_state_verified: bool = True,
    elapsed_ms: int = 1,
) -> SandboxRun:
    return SandboxRun(
        passed=passed,
        detail=detail,
        stdout='PPE_PYTHON_VERSION=3.13.14\n{"status": "passed"}\n',
        stderr="",
        exit_code=exit_code,
        elapsed_ms=elapsed_ms,
        timed_out=timed_out,
        oom_killed=oom_killed,
        image_reference=IMAGE,
        image_id="sha256:" + "d" * 64,
        python_version="3.13.14",
        probe_facts=probe_facts or {},
        policy={"network_mode": "none"},
        policy_verified=True,
        exit_state_verified=exit_state_verified,
    )


def verified_sandbox() -> DockerSandbox:
    with patch(
        "prompt_performance_engine.software_sandbox.shutil.which",
        return_value="docker",
    ):
        sandbox = DockerSandbox(IMAGE)
    sandbox.run_script = Mock(
        return_value=sandbox_run(
            passed=True,
            detail="verified",
            exit_code=0,
        )
    )
    sandbox.verify_isolation = Mock(
        return_value=sandbox_run(
            passed=True,
            detail="verified",
            exit_code=0,
            probe_facts={
                "network_blocked": True,
                "root_read_only": True,
                "tmp_writable": True,
                "non_root": True,
            },
        )
    )
    sandbox.verify_resource_limits = Mock(
        return_value={
            "timeout": sandbox_run(
                passed=False,
                detail="expected timeout",
                exit_code=0,
                timed_out=True,
            ),
            "memory": sandbox_run(
                passed=False,
                detail="expected OOM",
                exit_code=137,
                oom_killed=True,
            ),
        }
    )
    return sandbox


VALID_OUTPUTS = {
    "se-normal-pagination": """```python
def paginate(items, page, page_size):
    if isinstance(page, bool) or not isinstance(page, int):
        raise TypeError("page")
    if isinstance(page_size, bool) or not isinstance(page_size, int):
        raise TypeError("page_size")
    if page <= 0 or page_size <= 0:
        raise ValueError("positive")
    start = (page - 1) * page_size
    return items[start:start + page_size]
```""",
    "se-difficult-concurrency": """```python
class _Flight:
    def __init__(self):
        self.done = threading.Event()
        self.value = None
        self.error = None

class SingleFlightCache:
    def __init__(self, fetch):
        self._fetch = fetch
        self.cache = {}
        self.inflight = {}
        self.lock = threading.Lock()

    def get(self, key):
        with self.lock:
            if key in self.cache:
                return self.cache[key]
            flight = self.inflight.get(key)
            if flight is None:
                flight = _Flight()
                self.inflight[key] = flight
                leader = True
            else:
                leader = False
        if leader:
            try:
                value = self._fetch(key)
            except BaseException as error:
                with self.lock:
                    flight.error = error
                    del self.inflight[key]
                    flight.done.set()
                raise
            with self.lock:
                self.cache[key] = value
                flight.value = value
                del self.inflight[key]
                flight.done.set()
            return value
        flight.done.wait()
        if flight.error is not None:
            raise flight.error
        return flight.value
```""",
    "se-adversarial-contract": """```python
def handle_request(request, authenticate, create_item):
    user = authenticate(request.get("token"))
    if user is None:
        return {"status": 401, "body": {"error": {"code": "unauthorized"}}}
    payload = request.get("json")
    if not isinstance(payload, dict) or not isinstance(payload.get("name"), str):
        return {"status": 400, "body": {"error": {"code": "invalid_request"}}}
    return {"status": 201, "body": {"item": create_item(user, payload)}}
```""",
    "se-normal-cli": """```python
def rename_cli(argv, exists, rename, emit):
    args = list(argv)
    dry_run = "--dry-run" in args
    if args.count("--dry-run") > 1:
        return 2
    if dry_run:
        args.remove("--dry-run")
    if len(args) != 2:
        return 2
    source, destination = args
    if not exists(source):
        return 2
    if exists(destination):
        return 3
    if dry_run:
        emit("Would rename " + source + " to " + destination)
    else:
        rename(source, destination)
    return 0
```""",
    "se-difficult-migration": """```json
{
  "phases": [
    {
      "name": "expand",
      "actions": ["add nullable field"],
      "old_reader_supported": true,
      "new_reader_supported": true,
      "old_writer_supported": true,
      "new_writer_supported": false,
      "rollback_supported": true
    },
    {
      "name": "bridge",
      "actions": ["dual write"],
      "old_reader_supported": true,
      "new_reader_supported": true,
      "old_writer_supported": true,
      "new_writer_supported": true,
      "rollback_supported": true,
      "synchronizes_old_writer_inserts": true,
      "synchronizes_old_writer_updates": true
    },
    {
      "name": "backfill",
      "actions": ["backfill"],
      "old_reader_supported": true,
      "new_reader_supported": true,
      "old_writer_supported": true,
      "new_writer_supported": true,
      "rollback_supported": true
    },
    {
      "name": "cutover",
      "actions": ["read new field"],
      "old_reader_supported": true,
      "new_reader_supported": true,
      "old_writer_supported": true,
      "new_writer_supported": true,
      "rollback_supported": true
    },
    {
      "name": "contract",
      "actions": ["drop old field"],
      "old_reader_supported": false,
      "new_reader_supported": true,
      "old_writer_supported": false,
      "new_writer_supported": true,
      "rollback_supported": false,
      "drops_legacy_field": true,
      "enforces_new_not_null": true
    }
  ]
}
```""",
}


def software_evaluation(
    *,
    sandbox: DockerSandbox | None = None,
) -> dict:
    sandbox = sandbox or verified_sandbox()
    cases = [
        EvaluationCase(
            case_id=case_id,
            input_text=f"input-{case_id}",
            rubric=("Correctness",),
            domain="software_engineering",
        )
        for case_id in VALID_OUTPUTS
    ]
    outputs = {}
    for case in cases:
        input_hash = hashlib.sha256(case.input_text.encode("utf-8")).hexdigest()
        for prompt in ("original", "optimized"):
            prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
            outputs[(prompt_hash, input_hash)] = VALID_OUTPUTS[case.case_id]
    judges = [
        RecordedJudge(
            [JudgeDecision("tie", "Equivalent.") for _ in cases],
            name=f"judge-{index}",
        )
        for index in (1, 2)
    ]
    return evaluate_suite(
        suite_id="software-evidence-fixture",
        original_prompt="original",
        optimized_prompt="optimized",
        cases=cases,
        executor=RecordedExecutor(outputs),
        judges=judges,
        config=ExecutionConfig(model="recorded"),
        software_sandbox=sandbox,
    )


def code_execution_authority_fixture(
    root: Path,
) -> tuple[dict, dict, dict, DockerSandbox]:
    sandbox = verified_sandbox()
    evaluation = software_evaluation(sandbox=sandbox)
    report = build_code_execution_evidence(
        evaluation,
        report_id="software-authority",
        sandbox=sandbox,
    )
    (root / "evaluation.json").write_text(
        json.dumps(evaluation),
        encoding="utf-8",
    )
    plan = {
        "schema_version": "2.0.0",
        "report_id": "software-authority",
        "evaluation": "evaluation.json",
        "sandbox_image": IMAGE,
    }
    return report, plan, evaluation, sandbox


class SoftwareEvidenceTests(unittest.TestCase):
    def test_builds_hashed_evidence_from_authoritative_checks(self):
        sandbox = verified_sandbox()
        report = build_code_execution_evidence(
            software_evaluation(sandbox=sandbox),
            report_id="software-v14",
            sandbox=sandbox,
        )

        self.assertEqual(report["facts"]["eligible_cases"], 5)
        self.assertEqual(report["facts"]["executed_cases"], 5)
        self.assertEqual(report["facts"]["passed_cases"], 5)
        self.assertEqual(report["facts"]["restricted_subprocess_cases"], 4)
        self.assertEqual(report["facts"]["formal_contract_cases"], 1)
        self.assertTrue(report["facts"]["sandboxed"])
        self.assertTrue(
            all(
                result["reverified"]
                for result in report["facts"]["case_results"].values()
            )
        )
        self.assertEqual(
            len(report["provenance"]["verifier_implementation_sha256"]),
            64,
        )
        self.assertEqual(
            report["evidence_sha256"],
            hash_payload(report, "evidence_sha256"),
        )

    def test_build_code_evidence_fails_closed_without_sandbox(self):
        with self.assertRaisesRegex(ValueError, "DockerSandbox is required"):
            build_code_execution_evidence(
                software_evaluation(),
                report_id="software-v14",
            )

    def test_failed_isolation_probe_prevents_candidate_execution(self):
        evaluation = software_evaluation()
        sandbox = verified_sandbox()
        sandbox.verify_isolation.return_value = sandbox_run(
            passed=False,
            detail="isolation failed",
            exit_code=2,
        )

        with self.assertRaisesRegex(ValueError, "isolation could not be verified"):
            build_code_execution_evidence(
                evaluation,
                report_id="software-v14",
                sandbox=sandbox,
            )

        sandbox.run_script.assert_not_called()
        sandbox.verify_resource_limits.assert_not_called()

    def test_conflicting_memory_probe_exit_state_prevents_candidate_execution(self):
        evaluation = software_evaluation()
        sandbox = verified_sandbox()
        sandbox.verify_resource_limits.return_value["memory"] = sandbox_run(
            passed=False,
            detail="Docker sandbox exit state did not match the attached process.",
            exit_code=137,
            oom_killed=True,
            exit_state_verified=False,
        )

        with self.assertRaisesRegex(ValueError, "resource limits could not be verified"):
            build_code_execution_evidence(
                evaluation,
                report_id="software-v14",
                sandbox=sandbox,
            )

        sandbox.run_script.assert_not_called()

    def test_docker_evidence_requires_probe_and_all_executable_cases(self):
        sandbox = verified_sandbox()
        report = build_code_execution_evidence(
            software_evaluation(sandbox=sandbox),
            report_id="software-docker",
            sandbox=sandbox,
        )

        self.assertTrue(report["facts"]["sandboxed"])
        self.assertEqual(report["facts"]["sandboxed_cases"], 4)
        self.assertTrue(report["facts"]["sandbox"]["policy_verified"])
        self.assertTrue(
            report["facts"]["sandbox"]["resource_limits_verified"]
        )

    def test_cli_writes_code_evidence(self):
        sandbox = verified_sandbox()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evaluation_path = root / "evaluation.json"
            output_path = root / "evidence" / "code-execution.json"
            evaluation_path.write_text(
                json.dumps(software_evaluation(sandbox=sandbox)),
                encoding="utf-8",
            )
            stdout = io.StringIO()

            with patch(
                "prompt_performance_engine.cli.DockerSandbox",
                return_value=sandbox,
            ):
                with redirect_stdout(stdout):
                    exit_code = main(
                        [
                            "build-code-evidence",
                            str(evaluation_path),
                            "--report-id",
                            "software-v14",
                            "--sandbox-image",
                            IMAGE,
                            "--output",
                            str(output_path),
                        ]
                    )

            self.assertEqual(exit_code, 0)
            self.assertTrue(output_path.is_file())
            summary = json.loads(stdout.getvalue())
            self.assertEqual(summary["passed_cases"], 5)
            self.assertTrue(summary["sandboxed"])

    def test_rejects_tampered_evaluation(self):
        evaluation = software_evaluation()
        evaluation["records"][0]["optimized_output"] = "tampered"

        with self.assertRaisesRegex(ValueError, "Evaluation is invalid"):
            build_code_execution_evidence(
                evaluation,
                report_id="software-v14",
            )

    def test_authority_rebuilds_report_from_strict_source_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report, plan, _, sandbox = code_execution_authority_fixture(root)

            sources = load_code_execution_plan(plan, root=root)
            self.assertEqual(sources["report_id"], "software-authority")
            self.assertEqual(sources["sandbox_image"], IMAGE)
            self.assertEqual(
                sources["evaluation"]["evaluation_sha256"],
                report["provenance"]["evaluation_sha256"],
            )
            self.assertEqual(
                validate_code_execution_authority(
                    report,
                    plan,
                    root=root,
                    sandbox=sandbox,
                ),
                [],
            )

    def test_authority_ignores_nondeterministic_probe_elapsed_time(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report, plan, _, sandbox = code_execution_authority_fixture(root)
            sandbox.verify_resource_limits.return_value = {
                "timeout": sandbox_run(
                    passed=False,
                    detail="expected timeout",
                    exit_code=0,
                    timed_out=True,
                    elapsed_ms=999,
                ),
                "memory": sandbox_run(
                    passed=False,
                    detail="expected OOM",
                    exit_code=137,
                    oom_killed=True,
                    elapsed_ms=1234,
                ),
            }

            self.assertEqual(
                validate_code_execution_authority(
                    report,
                    plan,
                    root=root,
                    sandbox=sandbox,
                ),
                [],
            )

    def test_authority_rejects_self_rehashed_forged_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report, plan, _, sandbox = code_execution_authority_fixture(root)
            forged = copy.deepcopy(report)
            forged["facts"]["passed_cases"] = 0
            forged["evidence_sha256"] = hash_payload(
                forged,
                "evidence_sha256",
            )

            self.assertEqual(
                validate_code_execution_authority(
                    forged,
                    plan,
                    root=root,
                    sandbox=sandbox,
                ),
                ["code-execution report does not match its source plan"],
            )

    def test_authority_rejects_tampered_source_evaluation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report, plan, evaluation, sandbox = (
                code_execution_authority_fixture(root)
            )
            evaluation["suite_id"] = "forged-suite"
            evaluation["evaluation_sha256"] = hash_payload(
                evaluation,
                "evaluation_sha256",
            )
            (root / "evaluation.json").write_text(
                json.dumps(evaluation),
                encoding="utf-8",
            )

            self.assertEqual(
                validate_code_execution_authority(
                    report,
                    plan,
                    root=root,
                    sandbox=sandbox,
                ),
                ["code-execution report does not match its source plan"],
            )

    def test_authority_rejects_duplicate_fields_in_source_evaluation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report, plan, _, sandbox = code_execution_authority_fixture(root)
            (root / "evaluation.json").write_text(
                '{"schema_version":"2.0.0","schema_version":"2.0.0"}',
                encoding="utf-8",
            )
            sandbox.verify_isolation.reset_mock()

            failures = validate_code_execution_authority(
                report,
                plan,
                root=root,
                sandbox=sandbox,
            )

            self.assertEqual(len(failures), 1)
            self.assertIn("source plan is invalid", failures[0])
            sandbox.verify_isolation.assert_not_called()

    def test_plan_requires_exact_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, plan, _, _ = code_execution_authority_fixture(root)
            plan["unexpected"] = True

            with self.assertRaisesRegex(ValueError, "fields do not match"):
                load_code_execution_plan(plan, root=root)

    def test_plan_loader_rejects_unpinned_sandbox_image(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, plan, _, _ = code_execution_authority_fixture(root)
            plan["sandbox_image"] = "python:latest"

            with self.assertRaisesRegex(ValueError, "immutable sha256 digest"):
                load_code_execution_plan(plan, root=root)

    def test_authority_rejects_evaluation_path_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            root = parent / "authority"
            root.mkdir()
            report, plan, _, sandbox = code_execution_authority_fixture(root)
            plan["evaluation"] = "../outside.json"
            (parent / "outside.json").write_text("{}", encoding="utf-8")

            failures = validate_code_execution_authority(
                report,
                plan,
                root=root,
                sandbox=sandbox,
            )

            self.assertEqual(len(failures), 1)
            self.assertIn("escapes the plan root", failures[0])

    def test_authority_rejects_sandbox_image_mismatch_before_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report, plan, _, sandbox = code_execution_authority_fixture(root)
            plan["sandbox_image"] = "python:3.13-alpine@sha256:" + "a" * 64
            sandbox.verify_isolation.reset_mock()

            self.assertEqual(
                validate_code_execution_authority(
                    report,
                    plan,
                    root=root,
                    sandbox=sandbox,
                ),
                [
                    "code-execution sandbox image does not match the source plan"
                ],
            )
            sandbox.verify_isolation.assert_not_called()

    def test_authority_fails_closed_without_or_with_failed_sandbox(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report, plan, _, sandbox = code_execution_authority_fixture(root)

            self.assertEqual(
                validate_code_execution_authority(
                    report,
                    plan,
                    root=root,
                    sandbox=None,
                ),
                [
                    "a DockerSandbox is required for code-execution source authority"
                ],
            )

            sandbox.verify_isolation.side_effect = RuntimeError("docker failed")
            failures = validate_code_execution_authority(
                report,
                plan,
                root=root,
                sandbox=sandbox,
            )
            self.assertEqual(len(failures), 1)
            self.assertIn("source bundle is invalid", failures[0])


if __name__ == "__main__":
    unittest.main()
