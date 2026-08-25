#!/usr/bin/env python3
"""Contract-checked primitive predicate measurements for the A4 analyzer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from protocol_validation import ValidationError
from a4_spec import PREDICATE_CONTRACTS, validate_failure_count


@dataclass(frozen=True)
class PredicateMeasurement:
    predicate_id: str
    order: int
    scope: str
    counted_set_kind: str
    measured_count: int
    passed: bool
    replica: int | None

    def document(self) -> dict[str, Any]:
        return {
            "predicate_id": self.predicate_id,
            "order": self.order,
            "scope": self.scope,
            "counted_set_kind": self.counted_set_kind,
            "predicate_measured_survivor_count": self.measured_count,
            "status": "pass" if self.passed else "fail",
            "replica": self.replica,
        }


class MeasurementRecorder:
    """Retain actual counts while enforcing registry identity and local order."""

    def __init__(self) -> None:
        self._events: list[PredicateMeasurement] = []
        self._keys: set[tuple[str, int | None]] = set()
        self._last_order: dict[tuple[str, int | None], int] = {}

    @property
    def events(self) -> tuple[PredicateMeasurement, ...]:
        return tuple(self._events)

    def for_predicate(self, predicate_id: str) -> tuple[PredicateMeasurement, ...]:
        self._contract(predicate_id)
        return tuple(row for row in self._events if row.predicate_id == predicate_id)

    def record(
        self,
        predicate_id: str,
        measured_count: int,
        passed: bool,
        *,
        replica: int | None = None,
    ) -> PredicateMeasurement:
        contract = self._contract(predicate_id)
        if isinstance(measured_count, bool) or not isinstance(measured_count, int) or measured_count < 0:
            raise ValidationError("A4 predicate measurement must be a nonnegative integer")
        if not isinstance(passed, bool):
            raise ValidationError("A4 predicate measurement status must be boolean")
        if replica is not None and (isinstance(replica, bool) or replica not in (1, 2, 3)):
            raise ValidationError("A4 predicate measurement replica is invalid")
        if not passed:
            validate_failure_count(predicate_id, measured_count)
        key = (predicate_id, replica)
        if key in self._keys:
            raise ValidationError("A4 predicate measurement is duplicated")
        lane = (str(contract["scope"]), replica)
        order = int(contract["order"])
        if order <= self._last_order.get(lane, 0):
            raise ValidationError("A4 predicate measurements are out of contract order")
        row = PredicateMeasurement(
            predicate_id,
            order,
            str(contract["scope"]),
            str(contract["counted_set_kind"]),
            measured_count,
            passed,
            replica,
        )
        self._events.append(row)
        self._keys.add(key)
        self._last_order[lane] = order
        return row

    @staticmethod
    def _contract(predicate_id: str) -> Any:
        try:
            return PREDICATE_CONTRACTS[predicate_id]
        except KeyError as exc:
            raise ValidationError(f"unregistered A4 predicate {predicate_id!r}") from exc


def measure(
    recorder: MeasurementRecorder | None,
    predicate_id: str,
    measured_count: int,
    passed: bool,
    *,
    replica: int | None = None,
) -> None:
    """Record one primitive boundary when orchestration supplied a recorder."""
    if recorder is not None:
        recorder.record(predicate_id, measured_count, passed, replica=replica)
