"""Pydantic request/response models — the API contract.

Response shapes are built around the questions the README says a citizen needs
answered: was it received, was it verified, which department has it, what is
the reference, what is the status, what is the deadline, is it resolved.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, EmailStr, Field, field_validator


# ------------------------------------------------------------------ capture
class CaptureTokenRequest(BaseModel):
    device_id: Optional[str] = Field(None, max_length=120)
    latitude: Optional[float] = Field(None, ge=-90, le=90)
    longitude: Optional[float] = Field(None, ge=-180, le=180)
    phone: Optional[str] = Field(None, max_length=20)


class CaptureTokenResponse(BaseModel):
    token: str
    token_id: str
    issued_at: datetime
    expires_at: datetime
    ttl_seconds: int
    note: str = (
        "Capture the photo in-app and upload it with this token. The token is "
        "single-use and expires, which is what bounds the age of the evidence "
        "server-side."
    )


# ----------------------------------------------------------------- evidence
class SignalOut(BaseModel):
    code: str
    label: str
    severity: str
    detail: str
    delta: float = 0.0


class EvidenceOut(BaseModel):
    source: str
    capture_token_valid: bool
    server_received_at: datetime
    authenticity_score: float
    outcome: str
    hard_fail_reason: Optional[str] = None
    signals: list[SignalOut] = []

    sha256: Optional[str] = None
    phash: Optional[str] = None
    media_path: Optional[str] = None
    image_width: Optional[int] = None
    image_height: Optional[int] = None
    exif_present: bool = False
    exif_datetime: Optional[datetime] = None
    exif_time_delta_seconds: Optional[int] = None
    exif_gps_distance_meters: Optional[float] = None
    editing_software: Optional[str] = None
    gps_accuracy_meters: Optional[float] = None
    location_source: str = "unknown"
    mock_location_flag: bool = False
    inside_serviced_ward: Optional[bool] = None
    duplicate_of_complaint_id: Optional[int] = None
    exact_duplicate: bool = False


# ---------------------------------------------------------------- complaint
class ComplaintSubmitResponse(BaseModel):
    reference: str
    accepted: bool
    status: str
    message: str
    authenticity_score: float
    authenticity_outcome: str
    evidence_signals: list[SignalOut] = []
    rejection_reason: Optional[str] = None

    category: Optional[str] = None
    priority: Optional[str] = None
    ward_name: Optional[str] = None
    nearby_count: int = 0
    is_hotspot: bool = False
    department_code: Optional[str] = None
    external_ticket_id: Optional[str] = None
    sla_due_at: Optional[datetime] = None
    needs_human_review: bool = False
    pipeline_trace: list[str] = []


class EventOut(BaseModel):
    id: int
    event_type: str
    actor: str
    message: Optional[str] = None
    payload: dict[str, Any] = {}
    created_at: datetime
    entry_hash: Optional[str] = None


class AgentRunOut(BaseModel):
    id: int
    agent_name: str
    status: str
    is_ai: bool
    provider: Optional[str] = None
    model: Optional[str] = None
    confidence: Optional[float] = None
    latency_ms: Optional[int] = None
    rationale: Optional[str] = None
    output: dict[str, Any] = {}
    error: Optional[str] = None
    created_at: datetime


class ComplaintStatusResponse(BaseModel):
    """The citizen transparency view."""

    reference: str
    received: bool = True
    received_at: datetime
    verified: bool
    verification_reason: Optional[str] = None
    status: str
    category: Optional[str] = None
    priority: Optional[str] = None
    ward_name: Optional[str] = None
    department_code: Optional[str] = None
    department_name: Optional[str] = None
    external_ticket_id: Optional[str] = None
    sla_due_at: Optional[datetime] = None
    sla_breached: bool = False
    escalation_level: int = 0
    needs_human_review: bool = False
    is_hotspot: bool = False
    nearby_count: int = 0
    cluster_summary: Optional[str] = None
    resolved: bool = False
    resolved_at: Optional[datetime] = None
    resolution_message: Optional[str] = None
    rejection_reason: Optional[str] = None
    authenticity_score: Optional[float] = None
    authenticity_outcome: Optional[str] = None
    photo_url: Optional[str] = None
    events: list[EventOut] = []


class ComplaintListItem(BaseModel):
    id: int
    reference: str
    description: str
    status: str
    category: Optional[str] = None
    priority: Optional[str] = None
    ward_name: Optional[str] = None
    department_code: Optional[str] = None
    external_ticket_id: Optional[str] = None
    latitude: float
    longitude: float
    nearby_count: int = 0
    is_hotspot: bool = False
    sla_due_at: Optional[datetime] = None
    sla_breached: bool = False
    escalation_level: int = 0
    needs_human_review: bool = False
    authenticity_score: Optional[float] = None
    authenticity_outcome: Optional[str] = None
    photo_url: Optional[str] = None
    created_at: datetime


class ComplaintDetail(ComplaintListItem):
    address_text: Optional[str] = None
    severity_note: Optional[str] = None
    verification_reason: Optional[str] = None
    rejection_reason: Optional[str] = None
    cluster_summary: Optional[str] = None
    resolution_note: Optional[str] = None
    resolution_message: Optional[str] = None
    resolved_at: Optional[datetime] = None
    field_outcome: Optional[str] = None
    reporter_name: Optional[str] = None
    reporter_trust: Optional[float] = None
    evidence: Optional[EvidenceOut] = None
    events: list[EventOut] = []
    agent_runs: list[AgentRunOut] = []
    audit_chain_intact: bool = True


# -------------------------------------------------------------------- admin
class StatsResponse(BaseModel):
    total: int
    by_status: dict[str, int]
    by_category: dict[str, int]
    by_priority: dict[str, int]
    by_department: dict[str, int]
    open_count: int
    resolved_count: int
    rejected_count: int
    breached_count: int
    pending_review_count: int
    hotspot_count: int
    ai_provider: str
    ai_is_real_model: bool


class HotspotOut(BaseModel):
    cluster_id: int
    complaint_count: int
    latitude: float
    longitude: float
    categories: list[str] = []
    ward_name: Optional[str] = None
    open_count: int = 0
    breached_count: int = 0


class WardOut(BaseModel):
    id: int
    ward_number: str
    name: str
    zone: str
    complaint_count: int = 0
    boundary: dict[str, Any] = {}


class ResolveRequest(BaseModel):
    resolution_note: str = Field("", max_length=2000)
    field_outcome: str = Field("GENUINE_FIXED")

    @field_validator("field_outcome")
    @classmethod
    def _valid_outcome(cls, value: str) -> str:
        allowed = {"GENUINE_FIXED", "NOT_FOUND", "DUPLICATE", "NOT_OUR_DEPARTMENT"}
        if value not in allowed:
            raise ValueError(f"field_outcome must be one of {sorted(allowed)}")
        return value


class ReviewRequest(BaseModel):
    approve: bool
    note: str = Field("", max_length=2000)
    override_category: Optional[str] = None
    override_priority: Optional[str] = None


class SweepResponse(BaseModel):
    checked: int
    newly_breached: list[str] = []
    newly_escalated: list[str] = []
    clusters_updated: int = 0
    swept_at: str


class SocialPostOut(BaseModel):
    id: int
    complaint_reference: str
    kind: str
    content: str
    eligibility_reason: Optional[str] = None
    is_published: bool
    created_at: datetime


# --------------------------------------------------------------------- auth
class RegisterRequest(BaseModel):
    """Citizen self-registration. Staff accounts are never created this way."""

    email: EmailStr
    password: str = Field(..., min_length=8, max_length=256)
    display_name: str = Field(..., min_length=2, max_length=120)
    phone: Optional[str] = Field(None, max_length=20)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=1, max_length=256)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=256)
    new_password: str = Field(..., min_length=8, max_length=256)


class DepartmentSummary(BaseModel):
    id: int
    code: str
    name: str
    full_name: str
    service_label: str
    categories: list[str] = []
    is_mock: bool = True


class UserOut(BaseModel):
    id: int
    display_name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    role: str
    is_verified: bool
    is_active: bool
    trust_score: float
    reports_confirmed: int
    reports_rejected: int
    department: Optional[DepartmentSummary] = None
    created_at: datetime
    last_login_at: Optional[datetime] = None


class SessionResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    expires_at: datetime
    user: UserOut


# ------------------------------------------------------------- department
class DepartmentStats(BaseModel):
    department: DepartmentSummary
    total: int
    unacknowledged: int
    in_progress: int
    resolved: int
    breached: int
    due_soon: int
    by_priority: dict[str, int]
    by_category: dict[str, int]
    by_ward: dict[str, int]


class AcknowledgeRequest(BaseModel):
    note: str = Field("", max_length=1000)


class ReassignRequest(BaseModel):
    """A department disputing ownership of a complaint."""

    target_department_code: str = Field(..., min_length=2, max_length=32)
    reason: str = Field(..., min_length=5, max_length=1000)


# -------------------------------------------------------------- geocoding
class GeocodeResult(BaseModel):
    display_name: str
    latitude: float
    longitude: float
    category: Optional[str] = None
    type: Optional[str] = None
    importance: Optional[float] = None
    inside_service_area: bool = True


class ReverseGeocodeResult(BaseModel):
    display_name: Optional[str] = None
    road: Optional[str] = None
    suburb: Optional[str] = None
    city: Optional[str] = None
    postcode: Optional[str] = None
    latitude: float
    longitude: float
    inside_service_area: bool = True
    ward_name: Optional[str] = None
    ward_number: Optional[str] = None


# --------------------------------------------------------------- AI providers
class ProviderCredentialOut(BaseModel):
    """What the admin dashboard is told about one provider.

    The key itself is never included — only a mask, enough to recognise which
    key is installed without being enough to use it.
    """

    provider: str
    configured: bool
    masked_key: Optional[str] = None
    model: str
    #: "database" | "environment" | "none" — why it is configured this way.
    source: str
    is_active: bool = False
    updated_at: Optional[datetime] = None


class ProviderCredentialUpdate(BaseModel):
    #: Omit to leave the stored key untouched (so the model can be changed
    #: without re-pasting the key). Send "" to clear it.
    api_key: Optional[str] = None
    model: Optional[str] = None
    #: Make this the live provider for inference.
    make_active: bool = False


class ProviderTestResult(BaseModel):
    provider: str
    model: str
    ok: bool
    latency_ms: Optional[int] = None
    error_kind: Optional[str] = None
    detail: Optional[str] = None
