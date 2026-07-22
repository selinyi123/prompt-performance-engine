import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from prompt_performance_engine.frontier_io import canonical_authority_bytes
from prompt_performance_engine.frontier_preflight import (
    validate_frontier_preflight_report,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPOSITORY_ROOT / "scripts" / "run_frontier_phase0.py"


def load_phase0_script():
    spec = importlib.util.spec_from_file_location("run_frontier_phase0", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Phase 0 script could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FrontierPhase0PilotTests(unittest.TestCase):
    def test_public_synthetic_dry_run_is_blocked_and_not_evaluable(self):
        module = load_phase0_script()
        status = module.build_phase0_status()

        self.assertEqual(status["boundary"]["machine_claim"], "not_evaluable")
        self.assertEqual(status["boundary"]["pilot_state"], "blocked")
        self.assertFalse(status["boundary"]["authority_bearing"])
        self.assertEqual(status["artifact_authority"], "none")
        self.assertFalse(status["preflight_report"]["passed"])
        self.assertEqual(
            validate_frontier_preflight_report(status["preflight_report"]), []
        )
        self.assertIn(
            "campaign bundle could not be loaded",
            status["preflight_report"]["failures"],
        )
        self.assertEqual(status["downstream"]["execution_host"], "rejected")
        self.assertEqual(
            status["downstream"]["execution_host_rejection"],
            "frontier preflight did not pass.",
        )
        self.assertEqual(status["downstream"]["frontier_report"], "not_constructed")
        self.assertEqual(status["downstream"]["claim_inventory"], "not_constructed")
        self.assertEqual(status["downstream"]["offline_replay"], "not_started")
        self.assertTrue(
            all(value is False for value in status["external_activity"].values())
        )
        for field in (
            "clock_attestation_receipt_sha256",
            "owner_attestation_receipt_sha256",
            "custodian_attestation_receipt_sha256",
            "capability_receipts_sha256",
        ):
            self.assertIsNone(status["preflight_report"][field])

    def test_cli_writes_one_canonical_idempotent_blocked_record(self):
        module = load_phase0_script()
        with tempfile.TemporaryDirectory() as directory:
            output_root = Path(directory)
            output = io.StringIO()
            with redirect_stdout(output):
                first_exit = module.main(["--output-root", str(output_root)])
            output_path = output_root / "phase0-status.json"
            first_bytes = output_path.read_bytes()
            with redirect_stdout(output):
                second_exit = module.main(["--output-root", str(output_root)])

            self.assertEqual(first_exit, module.BLOCKED_EXIT_CODE)
            self.assertEqual(second_exit, module.BLOCKED_EXIT_CODE)
            self.assertEqual(output.getvalue().count("not_evaluable: blocked"), 2)
            self.assertEqual(output_path.read_bytes(), first_bytes)
            payload = json.loads(first_bytes)
            self.assertEqual(first_bytes, canonical_authority_bytes(payload))
            self.assertEqual(payload["boundary"]["machine_claim"], "not_evaluable")


if __name__ == "__main__":
    unittest.main()
