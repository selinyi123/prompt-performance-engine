import hashlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

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
)


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


def software_evaluation() -> dict:
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
    )


class SoftwareEvidenceTests(unittest.TestCase):
    def test_builds_hashed_evidence_from_authoritative_checks(self):
        report = build_code_execution_evidence(
            software_evaluation(),
            report_id="software-v14",
        )

        self.assertEqual(report["facts"]["eligible_cases"], 5)
        self.assertEqual(report["facts"]["executed_cases"], 5)
        self.assertEqual(report["facts"]["passed_cases"], 5)
        self.assertEqual(report["facts"]["restricted_subprocess_cases"], 4)
        self.assertEqual(report["facts"]["formal_contract_cases"], 1)
        self.assertFalse(report["facts"]["sandboxed"])
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

    def test_cli_writes_code_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evaluation_path = root / "evaluation.json"
            output_path = root / "evidence" / "code-execution.json"
            evaluation_path.write_text(
                json.dumps(software_evaluation()),
                encoding="utf-8",
            )
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                exit_code = main(
                    [
                        "build-code-evidence",
                        str(evaluation_path),
                        "--report-id",
                        "software-v14",
                        "--output",
                        str(output_path),
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertTrue(output_path.is_file())
            summary = json.loads(stdout.getvalue())
            self.assertEqual(summary["passed_cases"], 5)
            self.assertFalse(summary["sandboxed"])

    def test_rejects_tampered_evaluation(self):
        evaluation = software_evaluation()
        evaluation["records"][0]["optimized_output"] = "tampered"

        with self.assertRaisesRegex(ValueError, "Evaluation is invalid"):
            build_code_execution_evidence(
                evaluation,
                report_id="software-v14",
            )


if __name__ == "__main__":
    unittest.main()
