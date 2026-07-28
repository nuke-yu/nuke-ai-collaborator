"""Canonical relations between durable Memory records."""
from __future__ import annotations

from enum import StrEnum


class MemoryRelationType(StrEnum):
    """Small, explicit relation vocabulary for provenance and temporal change."""

    DUPLICATE_OF = "duplicate_of"
    REINFORCES = "reinforces"
    REFINES = "refines"
    CONTRADICTS = "contradicts"
    SUPERSEDES = "supersedes"
    DERIVED_FROM = "derived_from"
    SUPPORTS = "supports"
