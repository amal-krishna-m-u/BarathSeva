"""Typed workflow state.

The graph carries only identifiers and decisions — never image bytes, never an
open session. Each node re-reads what it needs from PostgreSQL, which is what
makes a complaint's path replayable from the database alone.
"""

from __future__ import annotations

from typing import Any, Optional, TypedDict


class ComplaintState(TypedDict, total=False):
    # --- identity ---
    complaint_id: int
    reference: str
    channel: str

    # --- evidence stage (already persisted before the graph runs) ---
    authenticity_score: float
    authenticity_outcome: str
    evidence_signal_codes: list[str]
    has_photo: bool

    # --- image guard ---
    #: False when no vision provider ran (keyless, no photo, or provider down).
    image_checked: bool
    image_matches_text: Optional[bool]
    image_kind: Optional[str]

    # --- verifier ---
    is_civic_issue: bool
    evidence_sufficient: bool
    verification_confidence: Optional[float]
    verification_reason: str
    category_hint: Optional[str]

    # --- classifier ---
    category: Optional[str]
    priority: Optional[str]

    # --- geocluster ---
    ward_id: Optional[int]
    ward_name: Optional[str]
    nearby_count: int
    is_hotspot: bool

    # --- dispatcher ---
    dispatched: bool
    department_code: Optional[str]
    external_ticket_id: Optional[str]

    # --- sla ---
    sla_due_at: Optional[str]

    # --- social ---
    social_post_id: Optional[int]

    # --- control plane ---
    status: str
    halted: bool
    halt_reason: Optional[str]
    needs_human_review: bool
    trace: list[str]
