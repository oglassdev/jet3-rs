#!/usr/bin/env python3
"""Predicate-major evaluation of replica-local A4 derivation primitives."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Generic, Mapping, Sequence, TypeVar

from a4_measurements import MeasurementRecorder, PredicateMeasurement
from a4_model import A4AnalysisError, WorkLedger


T = TypeVar("T")


class _TargetPassed(Exception):
    """Stop a probe immediately after its target predicate passes."""


class _TargetRecorder:
    """Validate a primitive's prefix while retaining only its target event."""

    def __init__(self, target: str, stop_after_pass: bool) -> None:
        self._target = target
        self._stop_after_pass = stop_after_pass
        self._validation = MeasurementRecorder()
        self.target_event: PredicateMeasurement | None = None

    def record(
        self,
        predicate_id: str,
        measured_count: int,
        passed: bool,
        *,
        replica: int | None = None,
    ) -> PredicateMeasurement:
        row = self._validation.record(
            predicate_id, measured_count, passed, replica=replica
        )
        if predicate_id == self._target:
            self.target_event = row
            if passed and self._stop_after_pass:
                raise _TargetPassed
        return row


@dataclass(frozen=True)
class LocalPredicateFailure:
    """The first replica-local failure in predicate-major order."""

    replica: int
    error: A4AnalysisError


@dataclass(frozen=True)
class PredicateMajorResult(Generic[T]):
    values: Mapping[int, T]
    failure: LocalPredicateFailure | None = None


ReplicaRunner = Callable[[int, WorkLedger, object], T]


def evaluate_replica_predicates(
    predicate_ids: Sequence[str],
    runner: ReplicaRunner[T],
    work: WorkLedger,
    measurements: MeasurementRecorder,
) -> PredicateMajorResult[T]:
    """Evaluate each predicate on replica 1 then 2 before advancing.

    Earlier successful predicates use bounded scratch ledgers and stop at the
    measurement boundary.  The final local predicate runs the complete
    primitive with the retained ledger, producing the value needed downstream
    while accounting the primitive's work exactly once.
    """
    ordered = tuple(predicate_ids)
    if not ordered:
        raise ValueError("predicate-major evaluation requires a nonempty sequence")
    values: dict[int, T] = {}
    for predicate_index, predicate_id in enumerate(ordered):
        final = predicate_index == len(ordered) - 1
        for replica in (1, 2):
            recorder = _TargetRecorder(predicate_id, stop_after_pass=not final)
            try:
                value = runner(replica, work if final else WorkLedger(), recorder)
            except _TargetPassed:
                value = None
            except A4AnalysisError as error:
                if error.predicate_id != predicate_id:
                    raise
                event = recorder.target_event
                if event is None or event.passed:
                    raise RuntimeError(
                        "A4 primitive failed without its target measurement"
                    ) from error
                if not final:
                    _account_reached_prefix(
                        predicate_id, replica, runner, work
                    )
                if replica == 1 and predicate_index:
                    _account_completed_replica(
                        ordered[predicate_index - 1], 2, runner, work
                    )
                measurements.record(
                    event.predicate_id,
                    event.measured_count,
                    event.passed,
                    replica=event.replica,
                )
                return PredicateMajorResult(
                    {}, LocalPredicateFailure(replica, error)
                )
            event = recorder.target_event
            if event is None or not event.passed:
                raise RuntimeError("A4 primitive did not reach its target predicate")
            measurements.record(
                event.predicate_id,
                event.measured_count,
                event.passed,
                replica=event.replica,
            )
            if final:
                values[replica] = value
    return PredicateMajorResult(values)


def _account_reached_prefix(
    predicate_id: str,
    failing_replica: int,
    runner: ReplicaRunner[object],
    work: WorkLedger,
) -> None:
    """Replay only the reached prefix into the retained work ledger."""
    if predicate_id == "A4-H2-ROW-DIRECTORY-INVALID":
        work.select_invalid_directory_terminal()
    for replica in range(1, failing_replica + 1):
        recorder = _TargetRecorder(predicate_id, stop_after_pass=True)
        try:
            runner(replica, work, recorder)
        except _TargetPassed:
            if replica == failing_replica:
                raise RuntimeError("A4 failing predicate passed during replay")
        except A4AnalysisError as error:
            if replica != failing_replica or error.predicate_id != predicate_id:
                raise
            return
    raise RuntimeError("A4 failing predicate did not fail during replay")


def _account_completed_replica(
    predicate_id: str,
    replica: int,
    runner: ReplicaRunner[object],
    work: WorkLedger,
) -> None:
    """Transfer the latest completed prefix for a not-yet-current replica."""
    recorder = _TargetRecorder(predicate_id, stop_after_pass=True)
    try:
        runner(replica, work, recorder)
    except _TargetPassed:
        return
    except A4AnalysisError as error:
        raise RuntimeError(
            "A4 completed predicate failed during accounting replay"
        ) from error
    raise RuntimeError("A4 completed predicate did not pass during accounting replay")
