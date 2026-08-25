#!/usr/bin/env python3
"""Neutral checked observations shared by the A4 H2 and H3 layers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MapRow:
    tag: int
    payload: bytes
    base: int | None
    bitmap: bytes | None


@dataclass(frozen=True)
class FrozenOwnedRow:
    """One frozen-H2 allocation-row observation handed to H3."""

    replica: int
    logical_role: str
    lifecycle_instance: str
    allocation_role: str
    checkpoint_id: str
    map_page: int
    map_row: int
    representation: str
    owned_pages: tuple[int, ...] | None
    type_1_references: tuple[int, ...] | None
    type_0_span: tuple[int, int] | None
