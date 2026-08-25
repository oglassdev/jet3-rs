"""Immutable, plan-derived checkpoint schedule for the synthetic A4 generator.

The module deliberately contains no checkpoint table of its own.  Checkpoint
order, operations, lifecycle instances, schemas, growth thresholds, and
replica profiles are compiled from the checked document exported by
``a4_spec``.  The synthetic profiles are deterministic free choices; they do
not encode an analyzer outcome and their products are not A4 evidence.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping

from protocol_validation import ValidationError
from a4_spec import PLAN


class EventKind(str, Enum):
    """Closed set of mutations represented by the preregistered schedule."""

    EMPTY = "empty"
    IDLE = "idle"
    CREATE = "create"
    ADD_FIELD = "add_field"
    ADD_INDEX = "add_index"
    DROP = "drop"
    GROW = "grow"
    DELETE_ALL = "delete_all"
    REINSERT = "reinsert"


class ThresholdKind(str, Enum):
    """How a growth checkpoint's decimal suffix is interpreted."""

    NONE = "none"
    RELATIVE = "relative"
    ABSOLUTE = "absolute"


@dataclass(frozen=True)
class LifecycleInstance:
    """One role incarnation derived from the expected-schema tokens."""

    instance_id: str
    role: str
    version: str
    create_checkpoint: str
    last_extant_checkpoint: str
    extant_checkpoints: tuple[str, ...]


@dataclass(frozen=True)
class Event:
    """One checkpoint and the single logical operation that precedes it."""

    checkpoint_id: str
    ordinal: int
    kind: EventKind
    role: str | None
    lifecycle_instance: str | None
    expected_schema: tuple[str, ...]
    operation: str
    threshold_kind: ThresholdKind = ThresholdKind.NONE
    threshold_pages: int | None = None
    baseline_checkpoint_id: str | None = None

    @property
    def checkpoint(self) -> str:
        """Short generator-facing alias for ``checkpoint_id``."""
        return self.checkpoint_id

    @property
    def instance(self) -> str | None:
        """Short generator-facing alias for ``lifecycle_instance``."""
        return self.lifecycle_instance

    @property
    def uses_batches(self) -> bool:
        return self.kind is EventKind.GROW

    def target_pages(self, baseline_pages: int | None = None) -> int | None:
        """Resolve this checkpoint's target, rejecting the wrong baseline mode."""
        if self.threshold_kind is ThresholdKind.NONE:
            if baseline_pages is not None:
                raise ValidationError(f"{self.checkpoint_id}: non-growth event has a baseline")
            return None
        if self.threshold_pages is None or self.threshold_pages < 1:
            raise ValidationError(f"{self.checkpoint_id}: invalid growth threshold")
        if self.threshold_kind is ThresholdKind.ABSOLUTE:
            if baseline_pages is not None or self.baseline_checkpoint_id is not None:
                raise ValidationError(f"{self.checkpoint_id}: absolute target has a baseline")
            return self.threshold_pages
        if (
            isinstance(baseline_pages, bool)
            or not isinstance(baseline_pages, int)
            or baseline_pages < 0
            or self.baseline_checkpoint_id is None
        ):
            raise ValidationError(f"{self.checkpoint_id}: relative target lacks its baseline")
        return baseline_pages + self.threshold_pages


@dataclass(frozen=True)
class Profile:
    """A deterministic synthetic storage profile for exactly one replica.

    ``rows_per_page`` and ``initial_filler_pages`` are derived from the
    replica's position in the plan's role rotation.  A real DAO run does not
    use these values; it measures first-reaching file sizes directly.
    """

    replica: int
    rows_per_page: int
    initial_filler_pages: int
    batch_rows: int

    @property
    def pages_per_batch(self) -> int:
        if self.rows_per_page < 1 or self.batch_rows < 1:
            raise ValidationError(f"replica {self.replica}: invalid synthetic batch profile")
        return math.ceil(self.batch_rows / self.rows_per_page)


@dataclass(frozen=True)
class Schedule:
    """The fully checked plan projection consumed by ``a4_generator``."""

    events: tuple[Event, ...]
    instances: tuple[LifecycleInstance, ...]
    profiles: Mapping[int, Profile]
    batch_rows: int
    max_inserted_rows_per_replica: int
    _event_by_checkpoint: Mapping[str, Event]
    _instance_by_id: Mapping[str, LifecycleInstance]

    def event(self, checkpoint_id: str) -> Event:
        try:
            return self._event_by_checkpoint[checkpoint_id]
        except KeyError as exc:
            raise ValidationError(f"unknown A4 checkpoint {checkpoint_id!r}") from exc

    def instance(self, instance_id: str) -> LifecycleInstance:
        try:
            return self._instance_by_id[instance_id]
        except KeyError as exc:
            raise ValidationError(f"unknown A4 lifecycle instance {instance_id!r}") from exc

    def profile(self, replica: int) -> Profile:
        try:
            return self.profiles[replica]
        except KeyError as exc:
            raise ValidationError(f"unknown A4 replica {replica!r}") from exc

    @property
    def checkpoints(self) -> tuple[str, ...]:
        return tuple(event.checkpoint_id for event in self.events)


def _document() -> Mapping[str, Any]:
    document = getattr(PLAN, "document", PLAN)
    if not isinstance(document, Mapping):
        raise ValidationError("a4_spec.PLAN is not a checked mapping")
    return document


def _schema_identity(token: str, roles: tuple[str, ...]) -> tuple[str, str, str]:
    parts = token.split(":")
    if len(parts) < 2 or parts[0] not in roles:
        raise ValidationError(f"invalid expected-schema token {token!r}")
    role = parts[0]
    version = parts[1] if len(parts) > 2 and parts[1].startswith("v") else "v1"
    return role, version, f"{role}-{version}"


def _compile_instances(
    checkpoints: tuple[str, ...],
    expected: Mapping[str, Any],
    roles: tuple[str, ...],
) -> tuple[LifecycleInstance, ...]:
    spans: dict[str, tuple[str, str, list[str]]] = {}
    for checkpoint in checkpoints:
        schema = expected.get(checkpoint)
        if not isinstance(schema, list) or not all(isinstance(token, str) for token in schema):
            raise ValidationError(f"{checkpoint}: expected schema must be a string array")
        seen_roles: set[str] = set()
        for token in schema:
            role, version, instance_id = _schema_identity(token, roles)
            if role in seen_roles:
                raise ValidationError(f"{checkpoint}: duplicate expected-schema role {role}")
            seen_roles.add(role)
            if instance_id not in spans:
                spans[instance_id] = (role, version, [])
            spans[instance_id][2].append(checkpoint)
    ordinals = {checkpoint: ordinal for ordinal, checkpoint in enumerate(checkpoints)}
    instances = tuple(
        LifecycleInstance(instance_id, role, version, extant[0], extant[-1], tuple(extant))
        for instance_id, (role, version, extant) in sorted(
            spans.items(), key=lambda row: ordinals[row[1][2][0]]
        )
    )
    if not instances:
        raise ValidationError("A4 plan has no lifecycle instances")
    for instance in instances:
        actual = tuple(ordinals[checkpoint] for checkpoint in instance.extant_checkpoints)
        expected_ordinals = tuple(range(actual[0], actual[-1] + 1))
        if actual != expected_ordinals:
            raise ValidationError(f"{instance.instance_id}: lifecycle reappears after absence")
    return instances


def _classify(checkpoint: str, operation: str, ordinal: int, schema: tuple[str, ...]) -> EventKind:
    matches: list[EventKind] = []
    if ordinal == 0 and not schema and "fresh Jet 3 database" in operation:
        matches.append(EventKind.EMPTY)
    markers = (
        ("Close and reopen without mutation", EventKind.IDLE),
        ("TableDefs.Delete", EventKind.DROP),
        ("TableDefs.Append", EventKind.CREATE),
        ("Fields.Append", EventKind.ADD_FIELD),
        ("Indexes.Append", EventKind.ADD_INDEX),
        ("Delete every", EventKind.DELETE_ALL),
        ("Reinsert", EventKind.REINSERT),
    )
    matches.extend(kind for marker, kind in markers if marker in operation)
    if ("_REL_" in checkpoint or "_ABS_" in checkpoint) and "batch" in operation:
        matches.append(EventKind.GROW)
    matches = list(dict.fromkeys(matches))
    if len(matches) != 1:
        raise ValidationError(
            f"{checkpoint}: operation maps to {len(matches)} event kinds, expected exactly one"
        )
    return matches[0]


def _relative_baseline(
    checkpoint: str,
    role: str,
    transition_coverage: Mapping[str, Any],
) -> str:
    candidates: set[str] = set()
    for sequence in transition_coverage.values():
        if not isinstance(sequence, list) or checkpoint not in sequence:
            continue
        # The schema-snapshot coverage also contains every checkpoint.  A
        # baseline sequence is distinguished structurally: after its first
        # (baseline) member, every member belongs to the growing role.
        if not sequence[1:] or not all(value.startswith(f"{role}_") for value in sequence[1:]):
            continue
        position = sequence.index(checkpoint)
        if position < 1:
            continue
        role_growth = [value for value in sequence[1:] if value.startswith(f"{role}_REL_")]
        if checkpoint in role_growth:
            candidates.add(sequence[0])
    if len(candidates) != 1:
        raise ValidationError(
            f"{checkpoint}: expected one plan-declared relative baseline, found {sorted(candidates)}"
        )
    return next(iter(candidates))


def _role_for(checkpoint: str, roles: tuple[str, ...]) -> str | None:
    matches = [role for role in roles if checkpoint.startswith(f"{role}_")]
    if len(matches) > 1:
        raise ValidationError(f"{checkpoint}: ambiguous logical role")
    return matches[0] if matches else None


def _event_instance(
    kind: EventKind,
    role: str | None,
    schema: tuple[str, ...],
    previous_schema: tuple[str, ...],
    roles: tuple[str, ...],
) -> str | None:
    if role is None:
        return None
    source = previous_schema if kind is EventKind.DROP else schema
    matches = [_schema_identity(token, roles)[2] for token in source if token.split(":", 1)[0] == role]
    if len(matches) != 1:
        raise ValidationError(f"{role}: event does not select exactly one lifecycle instance")
    return matches[0]


def _compile_profiles(
    document: Mapping[str, Any],
    batch_rows: int,
    events: tuple[Event, ...],
) -> Mapping[int, Profile]:
    tables = document["tables"]
    roles = tuple(tables["logical_roles"])
    physical_names = tuple(tables["physical_names"])
    bindings = tables["role_bindings"]
    replica_count = int(document["replicas"]["count"])
    if len(bindings) != replica_count or len(roles) < replica_count:
        raise ValidationError("A4 role rotations do not cover the declared replicas")
    base_rows_per_page = batch_rows // len(roles)
    if base_rows_per_page <= replica_count - 1:
        raise ValidationError("A4 batch size cannot derive distinct positive replica profiles")
    growth_events = tuple(event for event in events if event.kind is EventKind.GROW)
    if not growth_events:
        raise ValidationError("A4 plan has no growth events")
    profiles: dict[int, Profile] = {}
    for binding in bindings:
        replica = binding.get("replica")
        if isinstance(replica, bool) or not isinstance(replica, int) or replica in profiles:
            raise ValidationError("A4 role bindings have an invalid replica id")
        try:
            rotation = physical_names.index(binding[roles[0]])
        except (KeyError, ValueError) as exc:
            raise ValidationError(f"replica {replica}: invalid role rotation") from exc
        profiles[replica] = Profile(
            replica=replica,
            rows_per_page=base_rows_per_page - rotation,
            initial_filler_pages=rotation,
            batch_rows=batch_rows,
        )
    expected_replicas = set(range(1, replica_count + 1))
    signatures = {(p.rows_per_page, p.initial_filler_pages, p.pages_per_batch) for p in profiles.values()}
    if set(profiles) != expected_replicas or len(signatures) != replica_count:
        raise ValidationError("A4 synthetic replica profiles are not complete and distinct")
    return MappingProxyType(profiles)


def build_schedule() -> Schedule:
    document = _document()
    design = document["checkpoint_design"]
    tables = document["tables"]
    bounds = document["bounds"]
    checkpoints = tuple(design["checkpoint_ids"])
    if (
        len(checkpoints) != design["count"]
        or len(checkpoints) != bounds["planned_checkpoints_per_replica"]
        or len(checkpoints) != len(set(checkpoints))
    ):
        raise ValidationError("A4 checkpoint count/order contract is inconsistent")
    roles = tuple(tables["logical_roles"])
    expected = tables["expected_schema_by_checkpoint"]
    operations = tables["checkpoint_operations"]
    if tuple(expected) != checkpoints or tuple(operations) != checkpoints:
        raise ValidationError("A4 schema/operation coverage differs from checkpoint order")
    batch_rows = int(tables["row_algorithm"]["growth_batch_rows"])
    if batch_rows < 1 or batch_rows > int(bounds["max_inserted_rows_per_replica"]):
        raise ValidationError("A4 growth batch size is outside the row bound")
    instances = _compile_instances(checkpoints, expected, roles)
    events: list[Event] = []
    previous_schema: tuple[str, ...] = ()
    for ordinal, checkpoint in enumerate(checkpoints):
        schema = tuple(expected[checkpoint])
        operation = operations[checkpoint]
        if not isinstance(operation, str):
            raise ValidationError(f"{checkpoint}: checkpoint operation is not text")
        kind = _classify(checkpoint, operation, ordinal, schema)
        role = _role_for(checkpoint, roles)
        instance_id = _event_instance(kind, role, schema, previous_schema, roles)
        threshold_kind = ThresholdKind.NONE
        threshold_pages = None
        baseline = None
        if kind is EventKind.GROW:
            try:
                threshold_pages = int(checkpoint.rsplit("_", 1)[1])
            except (IndexError, ValueError) as exc:
                raise ValidationError(f"{checkpoint}: growth suffix is not decimal") from exc
            if "_ABS_" in checkpoint:
                threshold_kind = ThresholdKind.ABSOLUTE
            elif "_REL_" in checkpoint and role is not None:
                threshold_kind = ThresholdKind.RELATIVE
                baseline = _relative_baseline(checkpoint, role, design["transition_coverage"])
            else:
                raise ValidationError(f"{checkpoint}: growth target mode is ambiguous")
        events.append(Event(
            checkpoint, ordinal, kind, role, instance_id, schema, operation,
            threshold_kind, threshold_pages, baseline,
        ))
        previous_schema = schema
    if tuple(event.ordinal for event in events) != tuple(range(len(checkpoints))):
        raise ValidationError("A4 event ordinals are not contiguous")
    if any(event.kind is EventKind.GROW and event.lifecycle_instance is None for event in events):
        raise ValidationError("A4 growth event lacks a lifecycle instance")
    created = [event for event in events if event.kind is EventKind.CREATE]
    if (
        len(created) != len(instances)
        or {event.lifecycle_instance for event in created}
        != {instance.instance_id for instance in instances}
        or any(
            event.checkpoint_id != next(
                instance.create_checkpoint
                for instance in instances
                if instance.instance_id == event.lifecycle_instance
            )
            for event in created
        )
    ):
        raise ValidationError("A4 create events do not cover lifecycle instances exactly")
    prior_roles: set[str] = set()
    for event in events:
        current_roles = {_schema_identity(token, roles)[0] for token in event.expected_schema}
        if event.kind is EventKind.CREATE:
            expected_roles = prior_roles | {event.role or ""}
        elif event.kind is EventKind.DROP:
            expected_roles = prior_roles - {event.role or ""}
        else:
            expected_roles = prior_roles
        if current_roles != expected_roles:
            raise ValidationError(
                f"{event.checkpoint_id}: schema roles disagree with the logical operation"
            )
        prior_roles = current_roles
    for left, right in design["idle_pairs"]:
        right_event = next((event for event in events if event.checkpoint_id == right), None)
        if (
            right_event is None
            or right_event.kind is not EventKind.IDLE
            or right_event.ordinal < 1
            or events[right_event.ordinal - 1].checkpoint_id != left
        ):
            raise ValidationError(f"A4 idle pair {left!r}, {right!r} is not adjacent")
    max_pages = int(bounds["max_final_pages_per_replica"])
    for event in events:
        if event.threshold_pages is not None and event.threshold_pages > max_pages:
            raise ValidationError(f"{event.checkpoint_id}: threshold exceeds the page bound")
    event_map = MappingProxyType({event.checkpoint_id: event for event in events})
    instance_map = MappingProxyType({instance.instance_id: instance for instance in instances})
    if len(event_map) != len(events) or len(instance_map) != len(instances):
        raise ValidationError("A4 compiled schedule contains duplicate identities")
    compiled_events = tuple(events)
    return Schedule(
        compiled_events, instances, _compile_profiles(document, batch_rows, compiled_events), batch_rows,
        int(bounds["max_inserted_rows_per_replica"]), event_map, instance_map,
    )


SCHEDULE = build_schedule()
EVENTS = SCHEDULE.events
EVENT_BY_CHECKPOINT = SCHEDULE._event_by_checkpoint
PROFILES = SCHEDULE.profiles
ROW_BATCH = SCHEDULE.batch_rows
DEFAULT_PROFILES = PROFILES
