import threading
import unittest
from concurrent.futures import ThreadPoolExecutor

from prompt_performance_engine.frontier_budget import (
    BUDGET_AXES,
    BudgetExceededError,
    BudgetLedger,
)


def amounts(
    *,
    calls=1,
    input_tokens=10,
    output_tokens=10,
    money_microunits=100,
    wall_clock_ms=1000,
):
    return {
        "calls": calls,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "money_microunits": money_microunits,
        "wall_clock_ms": wall_clock_ms,
    }


class FrontierBudgetTests(unittest.TestCase):
    def test_terminal_failures_and_retries_are_both_charged(self):
        ledger = BudgetLedger(
            amounts(
                calls=3,
                input_tokens=100,
                output_tokens=100,
                money_microunits=1000,
                wall_clock_ms=5000,
            )
        )
        first = ledger.reserve("attempt-1", amounts())
        ledger.settle(
            "attempt-1",
            amounts(money_microunits=80, wall_clock_ms=900),
            status="timeout",
            receipt_sha256=None,
            reservation_token=first["reservation_token"],
        )
        second = ledger.reserve("attempt-2", amounts())
        ledger.settle(
            "attempt-2",
            amounts(money_microunits=90, wall_clock_ms=800),
            status="success",
            receipt_sha256="a" * 64,
            reservation_token=second["reservation_token"],
        )

        snapshot = ledger.snapshot()
        self.assertEqual(snapshot["consumed"]["calls"], 2)
        self.assertEqual(snapshot["consumed"]["money_microunits"], 170)
        self.assertEqual(
            [event["status"] for event in snapshot["events"]],
            ["timeout", "success"],
        )

    def test_reservation_happens_before_provider_callback(self):
        ledger = BudgetLedger(amounts(calls=1))
        observed = []

        reservation = ledger.reserve("attempt-1", amounts())
        observed.append(len(ledger.snapshot()["active_reservations"]))
        ledger.settle(
            "attempt-1",
            amounts(),
            status="success",
            receipt_sha256="b" * 64,
            reservation_token=reservation["reservation_token"],
        )

        self.assertEqual(observed, [1])

    def test_concurrent_reservations_cannot_oversell_one_call(self):
        ledger = BudgetLedger(amounts(calls=1))
        barrier = threading.Barrier(2)

        def reserve(index):
            barrier.wait()
            try:
                ledger.reserve(f"attempt-{index}", amounts())
            except BudgetExceededError:
                return False
            return True

        with ThreadPoolExecutor(max_workers=2) as executor:
            accepted = list(executor.map(reserve, (1, 2)))

        self.assertEqual(sum(accepted), 1)
        snapshot = ledger.snapshot()
        self.assertTrue(snapshot["breached"])
        self.assertEqual(len(snapshot["active_reservations"]), 1)
        self.assertEqual(len(snapshot["reservation_rejections"]), 1)

    def test_settlement_overage_is_recorded_before_error(self):
        ledger = BudgetLedger(amounts(calls=1, output_tokens=20))
        reservation = ledger.reserve("attempt-1", amounts(output_tokens=10))

        with self.assertRaisesRegex(BudgetExceededError, "output_tokens"):
            ledger.settle(
                "attempt-1",
                amounts(output_tokens=11),
                status="failure",
                receipt_sha256=None,
                reservation_token=reservation["reservation_token"],
            )

        snapshot = ledger.snapshot()
        self.assertTrue(snapshot["breached"])
        self.assertEqual(snapshot["events"][0]["charged"]["output_tokens"], 11)
        self.assertEqual(
            snapshot["events"][0]["over_reserved_axes"],
            ["output_tokens"],
        )

    def test_cancel_before_call_releases_without_charging(self):
        ledger = BudgetLedger(amounts(calls=1))
        reservation = ledger.reserve("attempt-1", amounts())
        ledger.cancel_before_call(
            "attempt-1",
            reservation_token=reservation["reservation_token"],
        )

        snapshot = ledger.snapshot()
        self.assertEqual(snapshot["consumed"]["calls"], 0)
        self.assertEqual(snapshot["events"], [])
        self.assertEqual(snapshot["pre_call_cancellations"], ["attempt-1"])
        with self.assertRaisesRegex(ValueError, "already been used"):
            ledger.reserve("attempt-1", amounts())

    def test_reservation_token_prevents_external_cancellation_or_settlement(self):
        ledger = BudgetLedger(amounts(calls=1))
        reservation = ledger.reserve("attempt-1", amounts())

        with self.assertRaisesRegex(ValueError, "token"):
            ledger.cancel_before_call(
                "attempt-1",
                reservation_token="0" * 64,
            )
        with self.assertRaisesRegex(ValueError, "token"):
            ledger.settle(
                "attempt-1",
                amounts(),
                status="success",
                receipt_sha256="a" * 64,
                reservation_token="0" * 64,
            )

        self.assertEqual(len(ledger.snapshot()["active_reservations"]), 1)
        ledger.settle(
            "attempt-1",
            amounts(),
            status="success",
            receipt_sha256="a" * 64,
            reservation_token=reservation["reservation_token"],
        )
        self.assertNotIn("reservation_token", str(ledger.snapshot()))

    def test_budget_inputs_fail_closed(self):
        with self.assertRaises(ValueError):
            BudgetLedger({"calls": 1})
        with self.assertRaises(ValueError):
            BudgetLedger(amounts(calls=True))
        with self.assertRaisesRegex(ValueError, "greater than zero"):
            BudgetLedger(amounts(calls=0))

        ledger = BudgetLedger(amounts(calls=2))
        with self.assertRaisesRegex(ValueError, "must equal one"):
            ledger.reserve("batch", amounts(calls=2))
        with self.assertRaisesRegex(ValueError, "reservation_id"):
            ledger.reserve("../escape", amounts())
        reservation = ledger.reserve("attempt-1", amounts())
        with self.assertRaisesRegex(ValueError, "terminal status"):
            ledger.settle(
                "attempt-1",
                amounts(),
                status="pending",
                receipt_sha256=None,
                reservation_token=reservation["reservation_token"],
            )

    def test_snapshot_is_defensive_and_hash_changes_with_events(self):
        ledger = BudgetLedger(amounts(calls=2))
        before = ledger.snapshot()
        before["ceilings"]["calls"] = 999
        self.assertEqual(ledger.snapshot()["ceilings"]["calls"], 2)

        reservation = ledger.reserve("attempt-1", amounts())
        ledger.settle(
            "attempt-1",
            amounts(),
            status="refusal",
            receipt_sha256=None,
            reservation_token=reservation["reservation_token"],
        )
        after = ledger.snapshot()
        self.assertNotEqual(before["ledger_sha256"], after["ledger_sha256"])
        self.assertEqual(set(after["ceilings"]), set(BUDGET_AXES))


if __name__ == "__main__":
    unittest.main()
