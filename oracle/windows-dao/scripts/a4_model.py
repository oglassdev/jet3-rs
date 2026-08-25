#!/usr/bin/env python3
"""Typed, bounded primitives shared by the A4 analyzer layers.

This module deliberately contains no A4 scientific derivation.  It provides
replica-qualified physical identities, checked page access, canonical identity
hashes, registered analysis terminals, and the exact named work ledger required
by the preregistered plan.
"""

from __future__ import annotations

import hashlib
from collections.abc import Hashable, Mapping, Sequence
from dataclasses import dataclass
from itertools import islice
from types import MappingProxyType
from typing import Any, Protocol

from a4_spec import (
    BOUNDS,
    CHECKPOINT_IDS,
    PLAN,
    PREDICATE_IDS,
    canonical_json_bytes,
    sha256_hex,
)


def _plan_document() -> Mapping[str, Any]:
    """Accept either a checked-plan wrapper or the plan mapping itself."""
    document = getattr(PLAN, "document", PLAN)
    if not isinstance(document, Mapping):
        raise TypeError("A4 checked plan must expose a mapping document")
    return document


_PLAN_DOCUMENT = _plan_document()
_WORK_MODEL = _PLAN_DOCUMENT["work_model"]
_PRIMARY_WORK_TERMS = tuple(_WORK_MODEL["terms"])
_ALTERNATIVE_WORK_TERMS = tuple(
    _WORK_MODEL["terminal_path_maxima"]["alternative_terms"]
)
WORK_TERMS = _PRIMARY_WORK_TERMS + _ALTERNATIVE_WORK_TERMS
WORK_TERM_LIMITS: Mapping[str, int] = MappingProxyType(
    {
        **{
            term: int(_WORK_MODEL["terms"][term]["units"])
            for term in _PRIMARY_WORK_TERMS
        },
        **{
            term: int(
                _WORK_MODEL["terminal_path_maxima"]["alternative_terms"][term][
                    "units"
                ]
            )
            for term in _ALTERNATIVE_WORK_TERMS
        },
    }
)

_INVALID_DIRECTORY_TERM = "invalid_path_row_directory_entries"
_VALID_DIRECTORY_TERM = "valid_path_row_directory_entries"
_LATEST_PATH = tuple(
    _WORK_MODEL["terminal_path_maxima"]["term_table"][
        "h4_latest_derivation_terminal"
    ]
)
_VALID_DIRECTORY_PATH_TERMS = frozenset(
    _LATEST_PATH[_LATEST_PATH.index(_VALID_DIRECTORY_TERM) :]
) - {"candidate_serializations"}


def _checked_nonnegative(value: int, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a nonnegative integer")
    return value


def analysis_work_within_limit(total_work_units: int) -> bool:
    """Return the checked comparator result; equality is accepted."""
    total = _checked_nonnegative(total_work_units, "A4 work total")
    return total <= int(BOUNDS["max_analysis_work_units"])


@dataclass(frozen=True, order=True)
class QualifiedPage:
    """One derivation-replica page inspection at one checkpoint.

    Numeric page and checkpoint values in replicas 1 and 2 are intentionally
    distinct identities.  Replica 3 is holdout input and is not part of the
    frozen ``qualified_pages`` inventory.
    """

    replica: int
    checkpoint_id: str
    page_number: int

    def __post_init__(self) -> None:
        if isinstance(self.replica, bool) or self.replica not in (1, 2):
            raise ValueError("A4 qualified page replica must be 1 or 2")
        if self.checkpoint_id not in CHECKPOINT_IDS:
            raise ValueError(f"unknown A4 checkpoint {self.checkpoint_id!r}")
        page = _checked_nonnegative(self.page_number, "A4 qualified page number")
        if page >= int(BOUNDS["max_final_pages_per_replica"]):
            raise ValueError("A4 qualified page number exceeds the plan bound")

    def document(self) -> dict[str, int | str]:
        return {
            "replica": self.replica,
            "checkpoint_id": self.checkpoint_id,
            "page_number": self.page_number,
        }


class ReplicaData(Protocol):
    """Minimal page-store surface consumed by the A4 analyzer."""

    @property
    def checkpoint_ids(self) -> Sequence[str]: ...

    @property
    def page_count(self) -> Mapping[str, int]: ...

    @property
    def ordered_page_sha256(self) -> Mapping[str, Sequence[str]]: ...

    def page_bytes(self, sha256: str) -> bytes: ...


class A4AnalysisError(Exception):
    """One registered A4 campaign, layer, or holdout terminal."""

    def __init__(
        self,
        predicate_id: str,
        survivor_count: int = 0,
        *,
        detail: str | None = None,
    ) -> None:
        if predicate_id not in PREDICATE_IDS:
            raise ValueError(f"unregistered A4 predicate {predicate_id!r}")
        self.predicate_id = predicate_id
        self.survivor_count = _checked_nonnegative(
            survivor_count, "A4 survivor count"
        )
        self.detail = detail
        message = predicate_id if detail is None else f"{predicate_id}: {detail}"
        super().__init__(message)


def require_analysis_work_within_limit(total_work_units: int) -> None:
    """Raise the registered resource terminal when the comparator rejects."""
    if not analysis_work_within_limit(total_work_units):
        raise A4AnalysisError(
            "A4-RESOURCE-BOUND",
            detail=(
                f"analysis work {total_work_units} exceeds "
                f"{BOUNDS['max_analysis_work_units']}"
            ),
        )


def canonical_object_id(document: Mapping[str, Any]) -> str:
    """Hash one complete registered canonical identity object."""
    return sha256_hex(canonical_json_bytes(dict(document)))


def canonical_model_id(model_type: str, model: Mapping[str, Any]) -> str:
    """Hash only the replica-invariant A4 scientific model."""
    if not isinstance(model_type, str) or not model_type:
        raise ValueError("A4 model_type must be a nonempty string")
    return canonical_object_id({"model_type": model_type, "model": dict(model)})


def canonical_candidate_id(
    model_type: str,
    model: Mapping[str, Any],
    instance_bindings: Sequence[Mapping[str, Any]],
) -> str:
    """Hash one model together with its complete physical bindings."""
    if not isinstance(model_type, str) or not model_type:
        raise ValueError("A4 model_type must be a nonempty string")
    return canonical_object_id(
        {
            "model_type": model_type,
            "model": dict(model),
            "instance_bindings": [dict(binding) for binding in instance_bindings],
        }
    )


class View:
    """Validated, bounded access to one replica's checkpoint page store."""

    def __init__(self, replica: int, source: ReplicaData) -> None:
        if (
            isinstance(replica, bool)
            or not isinstance(replica, int)
            or not 1 <= replica <= int(BOUNDS["replicas"])
        ):
            raise ValueError("A4 replica ordinal is outside the plan")
        self.replica = replica
        self.source = source
        self._counts: dict[str, int] = {}
        self._hashes: dict[str, tuple[str, ...]] = {}
        self._page_cache: dict[str, bytes] = {}
        self._logical_page_reads: set[tuple[str, int]] = set()
        self._checkpoint_read_bytes = 0
        self._logical_read_bytes = 0

        checkpoint_ids = self._bounded_sequence(
            source.checkpoint_ids,
            len(CHECKPOINT_IDS),
            "checkpoint sequence",
        )
        if checkpoint_ids != tuple(CHECKPOINT_IDS):
            self._snapshot_error("checkpoint order does not match the plan")
        for checkpoint_id in CHECKPOINT_IDS:
            try:
                count = source.page_count[checkpoint_id]
                declared_hashes = source.ordered_page_sha256[checkpoint_id]
            except (KeyError, TypeError) as exc:
                self._snapshot_error(
                    f"missing page index for {checkpoint_id}", cause=exc
                )
            if (
                isinstance(count, bool)
                or not isinstance(count, int)
                or not 1 <= count <= int(BOUNDS["max_final_pages_per_replica"])
            ):
                self._snapshot_error(f"invalid page count at {checkpoint_id}")
            hashes = self._bounded_sequence(
                declared_hashes,
                int(BOUNDS["max_final_pages_per_replica"]),
                f"page index at {checkpoint_id}",
            )
            if len(hashes) != count:
                self._snapshot_error(f"invalid page count at {checkpoint_id}")
            if any(not _valid_sha256(digest) for digest in hashes):
                self._snapshot_error(f"invalid page digest at {checkpoint_id}")
            self._counts[checkpoint_id] = count
            self._hashes[checkpoint_id] = hashes

    @classmethod
    def _bounded_sequence(
        cls, values: Sequence[Any], maximum: int, label: str
    ) -> tuple[Any, ...]:
        """Materialize no more than one item beyond a registered maximum."""
        try:
            declared_length = len(values)
        except Exception as exc:
            cls._snapshot_error(f"invalid {label}", cause=exc)
        if (
            isinstance(declared_length, bool)
            or declared_length < 0
            or declared_length > maximum
        ):
            cls._snapshot_error(f"invalid {label} length")
        try:
            materialized = tuple(islice(iter(values), maximum + 1))
        except Exception as exc:
            cls._snapshot_error(f"invalid {label}", cause=exc)
        if len(materialized) > maximum or len(materialized) != declared_length:
            cls._snapshot_error(f"inconsistent {label} length")
        return materialized

    @staticmethod
    def _snapshot_error(
        detail: str, *, cause: BaseException | None = None
    ) -> None:
        error = A4AnalysisError("A4-SNAPSHOT-RECONSTRUCTION", detail=detail)
        if cause is None:
            raise error
        raise error from cause

    @property
    def logical_read_bytes(self) -> int:
        return self._logical_read_bytes

    @property
    def opened_page_digests(self) -> frozenset[str]:
        return frozenset(self._page_cache)

    @property
    def checkpoint_read_bytes(self) -> int:
        return self._checkpoint_read_bytes

    def page_count(self, checkpoint_id: str) -> int:
        return self._counts[checkpoint_id]

    def hashes(self, checkpoint_id: str) -> tuple[str, ...]:
        return self._hashes[checkpoint_id]

    def hash_at(self, checkpoint_id: str, page_number: int) -> str | None:
        hashes = self._hashes[checkpoint_id]
        return hashes[page_number] if 0 <= page_number < len(hashes) else None

    def qualified_page(
        self, checkpoint_id: str, page_number: int
    ) -> QualifiedPage:
        return QualifiedPage(self.replica, checkpoint_id, page_number)

    def page_optional(self, checkpoint_id: str, page_number: int) -> bytes | None:
        digest = self.hash_at(checkpoint_id, page_number)
        if digest is None:
            return None
        identity = (checkpoint_id, page_number)
        if identity not in self._logical_page_reads:
            next_logical = self._checkpoint_read_bytes + int(BOUNDS["page_size"])
            if next_logical > int(BOUNDS["max_logical_checkpoint_read_bytes_per_replica"]):
                raise A4AnalysisError(
                    "A4-RESOURCE-BOUND",
                    detail="analyzer logical checkpoint reads exceed the bound",
                )
            self._logical_page_reads.add(identity)
            self._checkpoint_read_bytes = next_logical
        cached = self._page_cache.get(digest)
        if cached is not None:
            return cached
        try:
            payload = self.source.page_bytes(digest)
        except (KeyError, OSError, TypeError, ValueError) as exc:
            self._snapshot_error(f"cannot read page blob {digest}", cause=exc)
        if (
            not isinstance(payload, bytes)
            or len(payload) != int(BOUNDS["page_size"])
            or hashlib.sha256(payload).hexdigest() != digest
        ):
            self._snapshot_error(f"page blob {digest} does not match its index")
        next_retained = self._logical_read_bytes + len(payload)
        if next_retained > int(BOUNDS["max_retained_page_store_bytes"]):
            raise A4AnalysisError(
                "A4-RESOURCE-BOUND",
                detail="analyzer retained page bytes exceed the bound",
            )
        self._page_cache[digest] = payload
        self._logical_read_bytes = next_retained
        return payload

    def page(self, checkpoint_id: str, page_number: int) -> bytes:
        payload = self.page_optional(checkpoint_id, page_number)
        if payload is None:
            raise A4AnalysisError(
                "A4-SNAPSHOT-RECONSTRUCTION",
                detail=f"page {page_number} is absent at {checkpoint_id}",
            )
        return payload


def _valid_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


class WorkLedger:
    """Checked A4 work charges with union-once physical identities."""

    def __init__(self, prior_work_units: int = 0) -> None:
        self._prior_work_units = _checked_nonnegative(
            prior_work_units, "A4 prior work total"
        )
        require_analysis_work_within_limit(self._prior_work_units)
        self._charges = {term: 0 for term in WORK_TERMS}
        self._invalid_directory_terminal = False
        self._identities: dict[str, set[Hashable]] = {
            term: set() for term in WORK_TERMS
        }
        self._reached_page_identities: set[Hashable] = set()

    @property
    def total_work_units(self) -> int:
        return sum(self._charges.values())

    def value(self, term: str) -> int:
        self._require_term(term)
        return self._charges[term]

    def charge(self, term: str, units: int = 1) -> None:
        """Charge a reached term, failing before mutating on any violation."""
        self._require_term(term)
        if self._invalid_directory_terminal and term == _VALID_DIRECTORY_TERM:
            term = _INVALID_DIRECTORY_TERM
        units = _checked_nonnegative(units, f"A4 {term} charge")
        if units == 0:
            return
        self._require_compatible_path(term)
        next_term = self._charges[term] + units
        if next_term > WORK_TERM_LIMITS[term]:
            raise A4AnalysisError(
                "A4-RESOURCE-BOUND",
                detail=f"{term} exceeds its registered maximum",
            )
        require_analysis_work_within_limit(
            self._prior_work_units + self.total_work_units + units
        )
        self._charges[term] = next_term

    def select_invalid_directory_terminal(self) -> None:
        """Classify both replica inspections under the registered alternative path."""
        if any(self._charges[term] for term in _VALID_DIRECTORY_PATH_TERMS):
            raise ValueError("A4 invalid-directory terminal was selected after later work")
        self._invalid_directory_terminal = True

    def charge_once(
        self,
        term: str,
        identity: Hashable,
        units: int = 1,
    ) -> bool:
        """Charge one identity once for this term; return whether it was new."""
        self._require_term(term)
        try:
            hash(identity)
        except TypeError as exc:
            raise ValueError("A4 work identity must be hashable") from exc
        if identity in self._identities[term]:
            return False
        self.charge(term, units)
        self._identities[term].add(identity)
        return True

    def charge_qualified(
        self,
        term: str,
        page: QualifiedPage,
        units: int = 1,
        *,
        discriminator: Hashable | None = None,
    ) -> bool:
        """Charge a replica/checkpoint/page/model identity exactly once."""
        identity: Hashable = (
            page if discriminator is None else (page, discriminator)
        )
        return self.charge_once(term, identity, units)

    def record_qualified_page(
        self,
        page: QualifiedPage,
        *,
        discriminator: Hashable | None = None,
    ) -> bool:
        """Record one reached page identity without changing registered work."""
        if not isinstance(page, QualifiedPage):
            raise TypeError("A4 reached page must be replica-qualified")
        identity: Hashable = (
            page if discriminator is None else (page, discriminator)
        )
        try:
            hash(identity)
        except TypeError as exc:
            raise ValueError("A4 reached-page discriminator must be hashable") from exc
        if identity in self._reached_page_identities:
            return False
        self._reached_page_identities.add(identity)
        return True

    def qualified_pages(self) -> tuple[QualifiedPage, ...]:
        """Return the immutable canonical union of charged page identities."""
        pages: set[QualifiedPage] = set()
        for identities in (*self._identities.values(), self._reached_page_identities):
            for identity in identities:
                if isinstance(identity, QualifiedPage):
                    pages.add(identity)
                elif (
                    isinstance(identity, tuple)
                    and identity
                    and isinstance(identity[0], QualifiedPage)
                ):
                    pages.add(identity[0])
        ordinal = {checkpoint: index for index, checkpoint in enumerate(CHECKPOINT_IDS)}
        return tuple(
            sorted(
                pages,
                key=lambda page: (
                    page.replica,
                    ordinal[page.checkpoint_id],
                    page.page_number,
                ),
            )
        )

    @staticmethod
    def _identity_page(identity: Hashable) -> QualifiedPage | None:
        if isinstance(identity, QualifiedPage):
            return identity
        if (
            isinstance(identity, tuple)
            and identity
            and isinstance(identity[0], QualifiedPage)
        ):
            return identity[0]
        return None

    def qualified_page_terms(self, page: QualifiedPage) -> tuple[str, ...]:
        """Return the registered charged terms that reached one physical page."""
        return tuple(
            term
            for term in WORK_TERMS
            if any(self._identity_page(identity) == page for identity in self._identities[term])
        )

    def reached_page_discriminators(self, page: QualifiedPage) -> frozenset[Hashable | None]:
        """Return zero-cost read discriminators recorded for one physical page."""
        output: set[Hashable | None] = set()
        for identity in self._reached_page_identities:
            if self._identity_page(identity) != page:
                continue
            output.add(identity[1] if isinstance(identity, tuple) else None)
        return frozenset(output)

    def document(self) -> dict[str, int]:
        self._validate_path()
        require_analysis_work_within_limit(self.total_work_units)
        return {
            **{term: self._charges[term] for term in WORK_TERMS},
            "total_work_units": self.total_work_units,
        }

    def identities(self, term: str) -> frozenset[Hashable]:
        """Return the immutable, charged identities for one registered term."""
        self._require_term(term)
        return frozenset(self._identities[term])

    def charge_candidate_documents(
        self, candidates: Sequence[Mapping[str, Any]]
    ) -> None:
        """Bound and charge each retained canonical candidate exactly once."""
        for candidate in candidates:
            payload = canonical_json_bytes(dict(candidate))
            if len(payload) > 4096:
                raise A4AnalysisError(
                    "A4-RESOURCE-BOUND",
                    detail="canonical candidate exceeds 4,096 encoded bytes",
                )
            self.charge_once(
                "candidate_serializations", sha256_hex(payload)
            )

    def _require_term(self, term: str) -> None:
        if term not in self._charges:
            raise ValueError(f"unknown A4 work term {term!r}")

    def _require_compatible_path(self, term: str) -> None:
        if term == _INVALID_DIRECTORY_TERM and any(
            self._charges[path_term] for path_term in _VALID_DIRECTORY_PATH_TERMS
        ):
            raise ValueError(
                "invalid-directory work is mutually exclusive with the valid path"
            )
        if (
            term in _VALID_DIRECTORY_PATH_TERMS
            and self._charges[_INVALID_DIRECTORY_TERM]
        ):
            raise ValueError(
                "valid-path work is mutually exclusive with invalid-directory work"
            )

    def _validate_path(self) -> None:
        if self._charges[_INVALID_DIRECTORY_TERM] and any(
            self._charges[term] for term in _VALID_DIRECTORY_PATH_TERMS
        ):
            raise ValueError(
                "A4 work document mixes mutually exclusive directory paths"
            )
