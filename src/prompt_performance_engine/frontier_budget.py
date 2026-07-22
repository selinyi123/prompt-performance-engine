"""Atomic, fail-closed budget accounting for frontier campaign calls."""

from __future__ import annotations

import copy
import hmac
import re
import secrets
import threading
from typing import Any, Mapping

from .hashing import hash_payload


BUDGET_AXES = (
    "calls",
    "input_tokens",
    "output_tokens",
    "money_microunits",
    "wall_clock_ms",
)
TERMINAL_STATUSES = frozenset(
    {
        "success",
        "failure",
        "timeout",
        "refusal",
        "quota",
        "malformed",
        "cancelled_after_call",
    }
)
MAX_BUDGET_VALUE = 9_007_199_254_740_991
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class BudgetExceededError(RuntimeError):
    """Raised after a reservation or settlement breaches a hard ceiling."""


def _strict_amount(value: Any, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be a non-negative integer.")
    if not 0 <= value <= MAX_BUDGET_VALUE:
        raise ValueError(
            f"{label} must be between zero and {MAX_BUDGET_VALUE}."
        )
    return value


def _amounts(
    value: Mapping[str, Any],
    *,
    label: str,
    one_call: bool,
) -> dict[str, int]:
    if not isinstance(value, Mapping) or set(value) != set(BUDGET_AXES):
        raise ValueError(f"{label} must contain exactly {list(BUDGET_AXES)}.")
    normalized = {
        axis: _strict_amount(value[axis], label=f"{label}.{axis}")
        for axis in BUDGET_AXES
    }
    if one_call and normalized["calls"] != 1:
        raise ValueError(f"{label}.calls must equal one per provider attempt.")
    return normalized


def _sum_amounts(values: list[Mapping[str, int]]) -> dict[str, int]:
    return {
        axis: sum(value[axis] for value in values)
        for axis in BUDGET_AXES
    }


class BudgetLedger:
    """Reserve worst-case resources before calls and charge every terminal call.

    Monetary amounts are integer micro-units of the currency frozen by the
    campaign contract. A host must call ``reserve``
    before invoking a provider and ``settle`` exactly once afterward, including
    for failures, timeouts, refusals, quota errors, malformed responses, and
    retries. Rejected reservations and settlement overages permanently breach
    the ledger so a campaign cannot continue after a hard ceiling is reached.
    """

    def __init__(self, ceilings: Mapping[str, Any]) -> None:
        self._ceilings = _amounts(
            ceilings,
            label="ceilings",
            one_call=False,
        )
        if self._ceilings["calls"] == 0:
            raise ValueError("ceilings.calls must be greater than zero.")
        self._lock = threading.RLock()
        self._consumed = {axis: 0 for axis in BUDGET_AXES}
        self._reservations: dict[str, tuple[dict[str, int], str]] = {}
        self._used_ids: set[str] = set()
        self._events: list[dict[str, Any]] = []
        self._pre_call_cancellations: list[str] = []
        self._rejections: list[dict[str, Any]] = []
        self._breached = False

    def reserve(
        self,
        reservation_id: str,
        maximums: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Atomically reserve one provider attempt's worst-case resources."""

        if not isinstance(reservation_id, str) or not IDENTIFIER_RE.fullmatch(
            reservation_id
        ):
            raise ValueError("reservation_id is invalid.")
        reserved = _amounts(maximums, label="maximums", one_call=True)
        with self._lock:
            if reservation_id in self._used_ids:
                raise ValueError("reservation_id has already been used.")
            if self._breached:
                raise BudgetExceededError("budget ledger is already breached.")
            active = _sum_amounts(
                [reserved for reserved, _token in self._reservations.values()]
            )
            exceeded = [
                axis
                for axis in BUDGET_AXES
                if self._consumed[axis] + active[axis] + reserved[axis]
                > self._ceilings[axis]
            ]
            self._used_ids.add(reservation_id)
            if exceeded:
                self._breached = True
                self._rejections.append(
                    {
                        "reservation_id": reservation_id,
                        "requested": dict(reserved),
                        "exceeded_axes": exceeded,
                    }
                )
                raise BudgetExceededError(
                    "budget reservation exceeds hard ceilings: "
                    + ", ".join(exceeded)
                )
            reservation_token = secrets.token_hex(32)
            self._reservations[reservation_id] = (reserved, reservation_token)
            return {
                "reservation_id": reservation_id,
                "reserved": dict(reserved),
                "reservation_token": reservation_token,
            }

    def cancel_before_call(
        self,
        reservation_id: str,
        *,
        reservation_token: str,
    ) -> None:
        """Release a reservation only when no provider attempt was made."""

        with self._lock:
            reservation = self._reservations.get(reservation_id)
            if reservation is None:
                raise ValueError("reservation_id is not active.")
            if (
                not isinstance(reservation_token, str)
                or not hmac.compare_digest(reservation[1], reservation_token)
            ):
                raise ValueError("reservation token is invalid.")
            del self._reservations[reservation_id]
            self._pre_call_cancellations.append(reservation_id)

    def settle(
        self,
        reservation_id: str,
        actuals: Mapping[str, Any],
        *,
        status: str,
        receipt_sha256: str | None,
        reservation_token: str,
    ) -> dict[str, Any]:
        """Charge one terminal attempt, recording overages before raising."""

        charged = _amounts(actuals, label="actuals", one_call=True)
        if status not in TERMINAL_STATUSES:
            raise ValueError("status is not a supported terminal status.")
        if receipt_sha256 is not None and (
            not isinstance(receipt_sha256, str)
            or SHA256_RE.fullmatch(receipt_sha256) is None
        ):
            raise ValueError("receipt_sha256 must be a SHA-256 digest or null.")
        with self._lock:
            reservation = self._reservations.get(reservation_id)
            if reservation is None:
                raise ValueError("reservation_id is not active.")
            reserved, expected_token = reservation
            if (
                not isinstance(reservation_token, str)
                or not hmac.compare_digest(expected_token, reservation_token)
            ):
                raise ValueError("reservation token is invalid.")
            del self._reservations[reservation_id]
            for axis in BUDGET_AXES:
                self._consumed[axis] += charged[axis]
            over_reserved = [
                axis for axis in BUDGET_AXES if charged[axis] > reserved[axis]
            ]
            over_ceiling = [
                axis
                for axis in BUDGET_AXES
                if self._consumed[axis] > self._ceilings[axis]
            ]
            event = {
                "reservation_id": reservation_id,
                "status": status,
                "reserved": dict(reserved),
                "charged": dict(charged),
                "receipt_sha256": receipt_sha256,
                "over_reserved_axes": over_reserved,
                "over_ceiling_axes": over_ceiling,
            }
            self._events.append(event)
            if over_reserved or over_ceiling:
                self._breached = True
                axes = sorted(set(over_reserved + over_ceiling))
                raise BudgetExceededError(
                    "settled provider attempt breached budget: "
                    + ", ".join(axes)
                )
            return copy.deepcopy(event)

    def snapshot(self) -> dict[str, Any]:
        """Return a canonical, hash-bound snapshot without exposing mutability."""

        with self._lock:
            active = _sum_amounts(
                [reserved for reserved, _token in self._reservations.values()]
            )
            snapshot: dict[str, Any] = {
                "ceilings": dict(self._ceilings),
                "consumed": dict(self._consumed),
                "active_reserved": active,
                "remaining_unreserved": {
                    axis: max(
                        0,
                        self._ceilings[axis]
                        - self._consumed[axis]
                        - active[axis],
                    )
                    for axis in BUDGET_AXES
                },
                "active_reservations": [
                    {
                        "reservation_id": reservation_id,
                        "reserved": dict(self._reservations[reservation_id][0]),
                    }
                    for reservation_id in sorted(self._reservations)
                ],
                "events": copy.deepcopy(self._events),
                "pre_call_cancellations": list(self._pre_call_cancellations),
                "reservation_rejections": copy.deepcopy(self._rejections),
                "breached": self._breached,
            }
            snapshot["ledger_sha256"] = hash_payload(snapshot, "ledger_sha256")
            return snapshot
