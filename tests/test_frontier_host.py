import copy
import json
import unittest
from pathlib import Path

from prompt_performance_engine.frontier_budget import BudgetLedger
from prompt_performance_engine.frontier_contracts import (
    FRONTIER_CONTRACT_SCHEMA_VERSION,
)
from prompt_performance_engine.frontier_host import (
    EXECUTION_PLAN_FIELDS,
    EXECUTION_MANIFEST_FIELDS,
    EXECUTION_BUNDLE_ENTRY_FIELDS,
    EXECUTION_BUNDLE_FIELDS,
    EXECUTION_SLOT_FIELDS,
    FrontierExecutionHost,
    FrontierExecutionRejected,
    ProviderAttemptResult,
    build_frontier_execution_manifest,
    frontier_text_sha256,
    validate_frontier_attempt_report,
    validate_frontier_execution_plan,
    validate_frontier_execution_manifest,
)
from prompt_performance_engine.hashing import hash_payload, sha256_json


def resource_vector(
    *,
    calls: int = 4,
    input_tokens: int = 400,
    output_tokens: int = 400,
    money_microunits: int = 4_000,
    wall_clock_ms: int = 4_000,
) -> dict[str, int]:
    return {
        "calls": calls,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "money_microunits": money_microunits,
        "wall_clock_ms": wall_clock_ms,
    }


def passed_preflight(
    *,
    campaign_sha256: str = "1" * 64,
    evidence_expires_at: str = "2099-01-01T00:00:00Z",
) -> dict[str, object]:
    report: dict[str, object] = {
        "schema_version": FRONTIER_CONTRACT_SCHEMA_VERSION,
        "passed": True,
        "failures": [],
        "campaign_sha256": campaign_sha256,
        "policy_sha256": "2" * 64,
        "commitment_sha256": "3" * 64,
        "analysis_sha256": "4" * 64,
        "timestamp": "2026-07-21T00:00:00Z",
        "evidence_expires_at": evidence_expires_at,
        "clock_attestation_receipt_sha256": "e" * 64,
        "owner_attestation_receipt_sha256": "a" * 64,
        "custodian_attestation_receipt_sha256": "b" * 64,
        "capability_receipts_sha256": "c" * 64,
    }
    report["preflight_sha256"] = hash_payload(report, "preflight_sha256")
    return report


def component(*, unit_count: int = 2, attempts_per_unit: int = 2):
    return {
        "component_id": "generation-main",
        "role": "generation",
        "unit_count": unit_count,
        "attempts_per_unit": attempts_per_unit,
        "max_input_tokens_per_attempt": 100,
        "max_output_tokens_per_attempt": 100,
        "max_money_microunits_per_attempt": 1_000,
        "max_wall_clock_ms_per_attempt": 1_000,
        "model_ids": ["target-model"],
        "purposes": ["generation"],
    }


def execution_plan(
    execution_component=None,
    *,
    system_prompt="system",
    user_payload="payload",
):
    execution_component = execution_component or component()
    slots = []
    counter = 0
    for unit_index in range(execution_component["unit_count"]):
        for retry_index in range(execution_component["attempts_per_unit"]):
            counter += 1
            slots.append(
                {
                    "slot_id": f"attempt-{counter}",
                    "component_id": execution_component["component_id"],
                    "unit_id": f"unit-{unit_index + 1}",
                    "case_id": f"case-{unit_index + 1}",
                    "replicate_index": 0,
                    "retry_index": retry_index,
                    "model_id": "target-model",
                    "purpose": "generation",
                    "route": "deterministic",
                    "route_authority_id": "route-authority",
                    "system_prompt_sha256": frontier_text_sha256(system_prompt),
                    "user_payload_sha256": frontier_text_sha256(user_payload),
                    "seed": 1000 + unit_index,
                }
            )
    plan = {
        "schema_version": FRONTIER_CONTRACT_SCHEMA_VERSION,
        "plan_id": "execution-plan-1",
        "campaign_sha256": "1" * 64,
        "system_id": "candidate",
        "slots": slots,
    }
    plan["plan_sha256"] = hash_payload(plan, "plan_sha256")
    return plan


class SequenceClock:
    def __init__(self, *values):
        self.values = list(values)

    def __call__(self):
        if not self.values:
            raise RuntimeError("clock exhausted")
        return self.values.pop(0)


class Runner:
    def __init__(self, result=None, *, error=None, ledger=None):
        self.result = result
        self.error = error
        self.ledger = ledger
        self.host = None
        self.calls = []
        self.saw_reservation = False

    def attempt(self, request):
        self.calls.append(request)
        if self.host is not None:
            self.saw_reservation = bool(
                self.host.snapshot()["budget_ledger"]["active_reservations"]
            )
        if self.error is not None:
            raise self.error
        return self.result


class ReceiptVerifier:
    def __init__(self, *, fixed=None, fail=False):
        self.fixed = fixed
        self.fail = fail
        self.calls = []

    def verify(self, *, evidence, context_sha256):
        self.calls.append((dict(evidence), context_sha256))
        if self.fail:
            return None
        return self.fixed or sha256_json({"provider_context": context_sha256})


class Authorizer:
    def __init__(self, receipt="d" * 64):
        self.receipt = receipt
        self.calls = []

    def verify(self, *, evidence, context_sha256):
        self.calls.append((copy.deepcopy(dict(evidence)), context_sha256))
        return self.receipt


def successful_result(text="provider output"):
    return ProviderAttemptResult(
        text=text,
        provider="provider-a",
        model_id="target-model",
        status="success",
        input_tokens=7,
        output_tokens=11,
        money_microunits=19,
        physical_attempts=1,
        receipt_evidence={"opaque": "receipt"},
    )


def build_host(
    *,
    runner=None,
    verifier=None,
    authorizer=None,
    ledger=None,
    clock=None,
    components=None,
    preflight=None,
    plan=None,
    system_prompt="system",
    user_payload="payload",
    campaign_sha256="1" * 64,
):
    ledger = ledger or BudgetLedger(resource_vector())
    runner = runner or Runner(successful_result(), ledger=ledger)
    components = components or [component()]
    plan = plan or execution_plan(
        components[0],
        system_prompt=system_prompt,
        user_payload=user_payload,
    )
    if plan["campaign_sha256"] != campaign_sha256:
        plan = copy.deepcopy(plan)
        plan["campaign_sha256"] = campaign_sha256
        plan["plan_sha256"] = hash_payload(plan, "plan_sha256")
    host = FrontierExecutionHost(
        campaign_sha256=campaign_sha256,
        preflight_report=preflight or passed_preflight(
            campaign_sha256=campaign_sha256
        ),
        system_id="candidate",
        execution_plan=plan,
        case_routes=[
            {
                "case_id": f"case-{index + 1}",
                "route": "deterministic",
                "authority_id": "route-authority",
            }
            for index in range(components[0]["unit_count"])
        ],
        components=components,
        ledger=ledger,
        runner=runner,
        receipt_verifier=verifier or ReceiptVerifier(),
        preflight_authorizer=authorizer or Authorizer(),
        models=[
            {
                "model_id": "target-model",
                "provider": "provider-a",
                "snapshot": "model-snapshot-2026-07-21",
                "parameters_sha256": "5" * 64,
                "retry_policy_sha256": "6" * 64,
                "cache_policy_sha256": "7" * 64,
                "seeds_sha256": "8" * 64,
                "allowed_seeds": [
                    1000 + index for index in range(components[0]["unit_count"])
                ],
                "pricing_entry_sha256": "9" * 64,
                "price_snapshot_sha256": "a" * 64,
                "pricing_currency": "USD",
            }
        ],
    )
    host._monotonic = clock or SequenceClock(10.0, 10.125)
    if isinstance(runner, Runner):
        runner.host = host
    return host


class FrontierHostTests(unittest.TestCase):
    def test_success_is_reserved_verified_charged_and_sanitized(self):
        ledger = BudgetLedger(resource_vector())
        runner = Runner(successful_result(), ledger=ledger)
        verifier = ReceiptVerifier()
        authorizer = Authorizer()
        host = build_host(
            ledger=ledger,
            runner=runner,
            verifier=verifier,
            authorizer=authorizer,
            system_prompt="private system prompt",
            user_payload="sealed task payload",
        )

        outcome = host.execute_attempt(
            attempt_id="attempt-1",
            component_id="generation-main",
            retry_index=0,
            model_id="target-model",
            purpose="generation",
            system_prompt="private system prompt",
            user_payload="sealed task payload",
        )

        self.assertTrue(runner.saw_reservation)
        self.assertEqual(outcome.output_text, "provider output")
        self.assertTrue(outcome.report["usable"])
        self.assertEqual(outcome.report["usage"]["wall_clock_ms"], 125)
        self.assertEqual(validate_frontier_attempt_report(outcome.report), [])
        rendered = json.dumps(outcome.report)
        self.assertNotIn("private system prompt", rendered)
        self.assertNotIn("sealed task payload", rendered)
        self.assertNotIn("provider output", rendered)
        self.assertEqual(
            host.snapshot()["budget_ledger"]["consumed"]["calls"], 1
        )
        self.assertEqual(ledger.snapshot()["consumed"]["calls"], 0)
        self.assertEqual(len(authorizer.calls), 1)
        self.assertEqual(len(verifier.calls), 1)
        request = runner.calls[0]
        self.assertEqual(request.case_id, "case-1")
        self.assertEqual(request.model_snapshot, "model-snapshot-2026-07-21")
        self.assertEqual(request.parameters_sha256, "5" * 64)

    def test_constructor_requires_authoritative_unique_preflight(self):
        failed = passed_preflight()
        failed["passed"] = False
        failed["failures"] = ["authority unavailable"]
        failed["owner_attestation_receipt_sha256"] = None
        failed["preflight_sha256"] = hash_payload(failed, "preflight_sha256")
        with self.assertRaisesRegex(FrontierExecutionRejected, "did not pass"):
            build_host(preflight=failed)

        with self.assertRaisesRegex(FrontierExecutionRejected, "authority is invalid"):
            build_host(authorizer=Authorizer(receipt=None))

        with self.assertRaisesRegex(FrontierExecutionRejected, "not unique"):
            build_host(authorizer=Authorizer(receipt="a" * 64))

        mismatched = passed_preflight()
        mismatched["campaign_sha256"] = "9" * 64
        mismatched["preflight_sha256"] = hash_payload(
            mismatched, "preflight_sha256"
        )
        with self.assertRaisesRegex(FrontierExecutionRejected, "does not match"):
            build_host(preflight=mismatched)

    def test_unauthorized_route_and_retry_never_dispatch(self):
        runner = Runner(successful_result())
        host = build_host(runner=runner, clock=SequenceClock(1.0, 1.1))
        common = {
            "attempt_id": "attempt-1",
            "component_id": "generation-main",
            "retry_index": 0,
            "model_id": "target-model",
            "purpose": "generation",
            "system_prompt": "system",
            "user_payload": "payload",
        }
        for field, value in (
            ("component_id", "unknown"),
            ("model_id", "other-model"),
            ("purpose", "judging"),
            ("retry_index", 2),
        ):
            with self.subTest(field=field):
                request = dict(common)
                request[field] = value
                with self.assertRaises(FrontierExecutionRejected):
                    host.execute_attempt(**request)
        self.assertEqual(runner.calls, [])
        self.assertEqual(
            host.snapshot()["budget_ledger"]["consumed"]["calls"], 0
        )

    def test_timeout_charges_observed_elapsed_time_and_breaches(self):
        ledger = BudgetLedger(resource_vector(wall_clock_ms=1_000))
        host = build_host(
            ledger=ledger,
            runner=Runner(error=TimeoutError("provider timed out")),
            clock=SequenceClock(5.0, 6.5),
        )
        outcome = host.execute_attempt(
            attempt_id="attempt-1",
            component_id="generation-main",
            retry_index=0,
            model_id="target-model",
            purpose="generation",
            system_prompt="system",
            user_payload="payload",
        )
        self.assertFalse(outcome.report["usable"])
        self.assertEqual(outcome.report["failure_code"], "budget_breach")
        self.assertEqual(outcome.report["provider_status"], "timeout")
        self.assertEqual(outcome.report["usage"]["wall_clock_ms"], 1_500)
        snapshot = host.snapshot()["budget_ledger"]
        self.assertTrue(snapshot["breached"])
        self.assertEqual(snapshot["events"][0]["charged"]["wall_clock_ms"], 1_500)

    def test_unverified_and_reused_receipts_never_release_output(self):
        failed_host = build_host(
            verifier=ReceiptVerifier(fail=True),
            clock=SequenceClock(1.0, 1.1),
        )
        failed = failed_host.execute_attempt(
            attempt_id="attempt-1",
            component_id="generation-main",
            retry_index=0,
            model_id="target-model",
            purpose="generation",
            system_prompt="system",
            user_payload="payload",
        )
        self.assertIsNone(failed.output_text)
        self.assertEqual(failed.report["failure_code"], "receipt_unverified")
        self.assertEqual(failed.report["usage"]["input_tokens"], 100)
        self.assertEqual(failed.report["usage"]["money_microunits"], 1_000)

        fixed = "e" * 64
        reused_host = build_host(
            verifier=ReceiptVerifier(fixed=fixed),
            clock=SequenceClock(2.0, 2.1, 3.0, 3.1),
        )
        first = reused_host.execute_attempt(
            attempt_id="attempt-1",
            component_id="generation-main",
            retry_index=0,
            model_id="target-model",
            purpose="generation",
            system_prompt="system",
            user_payload="payload",
        )
        second = reused_host.execute_attempt(
            attempt_id="attempt-2",
            component_id="generation-main",
            retry_index=1,
            model_id="target-model",
            purpose="generation",
            system_prompt="system",
            user_payload="payload",
        )
        self.assertIsNotNone(first.output_text)
        self.assertIsNone(second.output_text)
        self.assertEqual(second.report["failure_code"], "receipt_reused")

    def test_interrupt_after_dispatch_is_settled_before_reraise(self):
        ledger = BudgetLedger(resource_vector())
        host = build_host(
            ledger=ledger,
            runner=Runner(error=KeyboardInterrupt()),
            clock=SequenceClock(7.0, 7.25),
        )
        with self.assertRaises(KeyboardInterrupt):
            host.execute_attempt(
                attempt_id="attempt-1",
                component_id="generation-main",
                retry_index=0,
                model_id="target-model",
                purpose="generation",
                system_prompt="system",
                user_payload="payload",
            )
        snapshot = host.snapshot()
        self.assertEqual(
            snapshot["attempt_reports"][0]["provider_status"],
            "cancelled_after_call",
        )
        self.assertEqual(snapshot["budget_ledger"]["consumed"]["calls"], 1)

    def test_invalid_clock_cannot_strand_a_reservation(self):
        ledger = BudgetLedger(resource_vector())
        runner = Runner(successful_result())
        host = build_host(
            ledger=ledger,
            runner=runner,
            clock=SequenceClock(float("nan")),
        )
        with self.assertRaisesRegex(FrontierExecutionRejected, "clock"):
            host.execute_attempt(
                attempt_id="attempt-1",
                component_id="generation-main",
                retry_index=0,
                model_id="target-model",
                purpose="generation",
                system_prompt="system",
                user_payload="payload",
            )
        self.assertEqual(runner.calls, [])
        self.assertEqual(ledger.snapshot()["active_reservations"], [])
        self.assertEqual(
            host.snapshot()["budget_ledger"]["active_reservations"], []
        )
        self.assertEqual(host.snapshot()["allocated_attempts"]["generation-main"], 0)

    def test_capacity_and_fresh_ledger_are_enforced(self):
        ledger = BudgetLedger(resource_vector())
        reservation = ledger.reserve(
            "prior-attempt",
            resource_vector(
                calls=1,
                input_tokens=1,
                output_tokens=1,
                money_microunits=1,
                wall_clock_ms=1,
            ),
        )
        with self.assertRaisesRegex(ValueError, "fresh"):
            build_host(ledger=ledger)

        host = build_host(
            components=[component(unit_count=1, attempts_per_unit=1)],
            clock=SequenceClock(1.0, 1.1, 2.0),
        )
        host.execute_attempt(
            attempt_id="attempt-1",
            component_id="generation-main",
            retry_index=0,
            model_id="target-model",
            purpose="generation",
            system_prompt="system",
            user_payload="payload",
        )
        with self.assertRaisesRegex(FrontierExecutionRejected, "consumed"):
            host.execute_attempt(
                attempt_id="attempt-1",
                component_id="generation-main",
                retry_index=0,
                model_id="target-model",
                purpose="generation",
                system_prompt="system",
                user_payload="payload",
            )

    def test_attempt_validation_rejects_tampering(self):
        host = build_host(clock=SequenceClock(1.0, 1.1))
        outcome = host.execute_attempt(
            attempt_id="attempt-1",
            component_id="generation-main",
            retry_index=0,
            model_id="target-model",
            purpose="generation",
            system_prompt="system",
            user_payload="payload",
        )
        tampered = copy.deepcopy(outcome.report)
        tampered["usage"]["calls"] = 0
        tampered["attempt_sha256"] = hash_payload(tampered, "attempt_sha256")
        self.assertTrue(validate_frontier_attempt_report(tampered))

    def test_frozen_text_and_complete_schedule_are_enforced(self):
        host = build_host(clock=SequenceClock(1.0, 1.1))
        with self.assertRaisesRegex(FrontierExecutionRejected, "text"):
            host.execute_attempt(
                attempt_id="attempt-1",
                component_id="generation-main",
                retry_index=0,
                model_id="target-model",
                purpose="generation",
                system_prompt="changed prompt",
                user_payload="payload",
            )
        self.assertEqual(host.snapshot()["used_slots"], [])

        incomplete = execution_plan()
        incomplete["slots"].pop()
        incomplete["plan_sha256"] = hash_payload(incomplete, "plan_sha256")
        with self.assertRaisesRegex(ValueError, "exact component call matrix"):
            build_host(plan=incomplete)

        malformed = execution_plan()
        malformed["slots"][0]["unknown"] = True
        malformed["plan_sha256"] = hash_payload(malformed, "plan_sha256")
        self.assertTrue(validate_frontier_execution_plan(malformed))

    def test_execution_manifest_requires_every_terminal_slot_and_budget_event(self):
        plan = execution_plan()
        host = build_host(
            plan=plan,
            clock=SequenceClock(
                1.0, 1.1, 2.0, 2.1, 3.0, 3.1, 4.0, 4.1
            ),
        )
        with self.assertRaisesRegex(ValueError, "incomplete"):
            build_frontier_execution_manifest(
                host.snapshot(), execution_plan=plan
            )
        for slot in plan["slots"]:
            host.execute_attempt(
                attempt_id=slot["slot_id"],
                component_id=slot["component_id"],
                retry_index=slot["retry_index"],
                model_id=slot["model_id"],
                purpose=slot["purpose"],
                system_prompt="system",
                user_payload="payload",
            )
        manifest = build_frontier_execution_manifest(
            host.snapshot(), execution_plan=plan
        )
        self.assertEqual(
            validate_frontier_execution_manifest(
                manifest, execution_plan=plan
            ),
            [],
        )
        tampered = copy.deepcopy(manifest)
        tampered["attempt_reports"][1] = copy.deepcopy(
            tampered["attempt_reports"][0]
        )
        tampered["manifest_sha256"] = hash_payload(
            tampered, "manifest_sha256"
        )
        self.assertTrue(
            validate_frontier_execution_manifest(tampered, execution_plan=plan)
        )

    def test_execution_manifest_is_canonical_after_out_of_order_completion(self):
        plan = execution_plan()
        host = build_host(
            plan=plan,
            clock=SequenceClock(
                1.0, 1.1, 2.0, 2.1, 3.0, 3.1, 4.0, 4.1
            ),
        )
        for slot in reversed(plan["slots"]):
            host.execute_attempt(
                attempt_id=slot["slot_id"],
                component_id=slot["component_id"],
                retry_index=slot["retry_index"],
                model_id=slot["model_id"],
                purpose=slot["purpose"],
                system_prompt="system",
                user_payload="payload",
            )

        manifest = build_frontier_execution_manifest(
            host.snapshot(), execution_plan=plan
        )

        self.assertEqual(
            manifest["completed_slot_ids"],
            [slot["slot_id"] for slot in plan["slots"]],
        )
        self.assertEqual(
            validate_frontier_execution_manifest(
                manifest, execution_plan=plan
            ),
            [],
        )

    def test_schema_identity_and_runtime_fields_match(self):
        schema = json.loads(
            (
                Path(__file__).parents[1]
                / "schemas"
                / "frontier-call-attempt.schema.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            schema["$id"],
            "urn:prompt-performance-engine:schema:frontier:call-attempt:1.0.0",
        )
        self.assertEqual(set(schema["required"]), set(schema["properties"]))
        execution_schema = json.loads(
            (
                Path(__file__).parents[1]
                / "schemas"
                / "frontier-execution-plan.schema.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            execution_schema["$id"],
            "urn:prompt-performance-engine:schema:frontier:execution-plan:1.0.0",
        )
        self.assertEqual(
            set(execution_schema["required"]), set(EXECUTION_PLAN_FIELDS)
        )
        self.assertEqual(
            set(execution_schema["$defs"]["slot"]["required"]),
            set(EXECUTION_SLOT_FIELDS),
        )
        manifest_schema = json.loads(
            (
                Path(__file__).parents[1]
                / "schemas"
                / "frontier-execution-manifest.schema.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            manifest_schema["$id"],
            "urn:prompt-performance-engine:schema:frontier:execution-manifest:1.0.0",
        )
        self.assertEqual(
            set(manifest_schema["required"]), set(EXECUTION_MANIFEST_FIELDS)
        )
        bundle_schema = json.loads(
            (
                Path(__file__).parents[1]
                / "schemas"
                / "frontier-execution-bundle.schema.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            bundle_schema["$id"],
            "urn:prompt-performance-engine:schema:frontier:execution-bundle:1.0.0",
        )
        self.assertEqual(
            set(bundle_schema["required"]), set(EXECUTION_BUNDLE_FIELDS)
        )
        self.assertEqual(
            set(bundle_schema["$defs"]["execution"]["required"]),
            set(EXECUTION_BUNDLE_ENTRY_FIELDS),
        )
        schemas_by_id = {
            item["$id"]: item
            for item in (
                schema,
                execution_schema,
                manifest_schema,
                bundle_schema,
            )
        }
        external_refs = {
            manifest_schema["properties"]["attempt_reports"]["items"]["$ref"],
            bundle_schema["$defs"]["execution"]["properties"]["execution_plan"][
                "$ref"
            ],
            bundle_schema["$defs"]["execution"]["properties"][
                "execution_manifest"
            ]["$ref"],
        }
        self.assertEqual(
            external_refs,
            {
                schema["$id"],
                execution_schema["$id"],
                manifest_schema["$id"],
            },
        )
        for reference in external_refs:
            self.assertIn(reference, schemas_by_id)


if __name__ == "__main__":
    unittest.main()
