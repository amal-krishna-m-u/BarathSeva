"""SQLAlchemy models — the source of truth for every complaint and decision.

Nothing in the pipeline keeps state only in memory or only in a model's
context: if it matters, it is a row here.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from geoalchemy2 import Geometry
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum as SAEnum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import (
    AgentStatus,
    AuthenticityOutcome,
    ComplaintCategory,
    ComplaintStatus,
    EvidenceSource,
    EventType,
    LocationSource,
    Priority,
    UserRole,
)
from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def enum_col(py_enum: type) -> SAEnum:
    """Map a Python enum to a checked VARCHAR column.

    ``native_enum=False`` keeps the storage portable (VARCHAR + CHECK) instead
    of creating a PostgreSQL ENUM type that would need a migration to extend.
    ``values_callable`` persists each member's *value*, and SQLAlchemy converts
    back to the enum member on load — without this the columns round-trip as
    bare strings and every ``.value`` access downstream breaks.
    """
    return SAEnum(
        py_enum,
        native_enum=False,
        validate_strings=True,
        values_callable=lambda e: [m.value for m in e],
        name=f"{py_enum.__name__.lower()}_enum",
    )



class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class User(Base, TimestampMixin):
    """Citizens and staff accounts.

    ``trust_score`` is the reporter reputation described in Layer 6: it rises
    with confirmed-genuine reports and falls with rejected ones. New accounts
    start neutral and are weighted lower, never blocked.

    ``department_id`` is what scopes a DEPT_ADMIN: their portal only ever
    returns complaints routed to that one agency. It is meaningless for
    citizens and must be NULL for them.

    ``password_hash`` is nullable on purpose — anonymous reporters exist
    without ever setting a password.
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[Optional[str]] = mapped_column(String(254), unique=True, index=True)
    phone: Mapped[Optional[str]] = mapped_column(String(20), unique=True)
    role: Mapped[UserRole] = mapped_column(enum_col(UserRole), default=UserRole.CITIZEN)

    # --- authentication ---
    password_hash: Mapped[Optional[str]] = mapped_column(String(128))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    #: Bumped on every credential change; tokens carry the value they were
    #: issued under, so an older one stops validating. A counter rather than a
    #: timestamp because timestamps have second resolution — two changes inside
    #: the same second would produce the same stamp and fail to invalidate.
    token_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    #: Audit only; never used for token validation.
    credentials_changed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )

    # --- staff scoping ---
    department_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("departments.id"), index=True
    )

    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    trust_score: Mapped[float] = mapped_column(Float, default=0.5)
    reports_confirmed: Mapped[int] = mapped_column(Integer, default=0)
    reports_rejected: Mapped[int] = mapped_column(Integer, default=0)

    complaints: Mapped[list["Complaint"]] = relationship(
        back_populates="reporter", foreign_keys="Complaint.reporter_id"
    )
    department: Mapped[Optional["Department"]] = relationship(
        back_populates="staff", foreign_keys=[department_id]
    )

    @property
    def can_login(self) -> bool:
        return bool(self.password_hash) and self.is_active


class Ward(Base):
    """Geographic ward with authoritative boundary geometry."""

    __tablename__ = "wards"

    id: Mapped[int] = mapped_column(primary_key=True)
    ward_number: Mapped[str] = mapped_column(String(16), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    zone: Mapped[str] = mapped_column(String(120), nullable=False)
    city: Mapped[str] = mapped_column(String(80), default="Bengaluru")
    boundary: Mapped[Any] = mapped_column(
        Geometry(geometry_type="MULTIPOLYGON", srid=4326, spatial_index=True)
    )

    complaints: Mapped[list["Complaint"]] = relationship(back_populates="ward")


class Department(Base):
    """Municipal agency and the categories it owns (the routing table)."""

    __tablename__ = "departments"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    full_name: Mapped[str] = mapped_column(String(300), nullable=False)
    categories: Mapped[list[str]] = mapped_column(JSON, default=list)
    #: Plain-language service name shown in the portal ("Water", "Electricity").
    service_label: Mapped[str] = mapped_column(String(80), default="Civic services")
    contact_email: Mapped[Optional[str]] = mapped_column(String(200))
    api_base_url: Mapped[Optional[str]] = mapped_column(String(300))
    is_mock: Mapped[bool] = mapped_column(Boolean, default=True)

    complaints: Mapped[list["Complaint"]] = relationship(back_populates="department")
    staff: Mapped[list["User"]] = relationship(
        back_populates="department", foreign_keys="User.department_id"
    )


class SLAPolicy(Base):
    """Deterministic SLA rules per category and priority."""

    __tablename__ = "sla_policies"
    __table_args__ = (UniqueConstraint("category", "priority", name="uq_sla_cat_prio"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    category: Mapped[ComplaintCategory] = mapped_column(enum_col(ComplaintCategory), nullable=False)
    priority: Mapped[Priority] = mapped_column(enum_col(Priority), nullable=False)
    resolution_hours: Mapped[int] = mapped_column(Integer, nullable=False)
    escalation_hours: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)


class Complaint(Base, TimestampMixin):
    """The primary complaint record."""

    __tablename__ = "complaints"

    id: Mapped[int] = mapped_column(primary_key=True)
    reference: Mapped[str] = mapped_column(String(24), unique=True, nullable=False)

    reporter_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))
    channel: Mapped[str] = mapped_column(String(24), default="web")

    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[ComplaintStatus] = mapped_column(
        enum_col(ComplaintStatus), default=ComplaintStatus.SUBMITTED, index=True
    )

    # --- location ---
    location: Mapped[Any] = mapped_column(
        Geometry(geometry_type="POINT", srid=4326, spatial_index=True)
    )
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    address_text: Mapped[Optional[str]] = mapped_column(String(400))
    location_source: Mapped[LocationSource] = mapped_column(
        enum_col(LocationSource), default=LocationSource.UNKNOWN
    )
    ward_id: Mapped[Optional[int]] = mapped_column(ForeignKey("wards.id"), index=True)

    # --- classification ---
    category: Mapped[Optional[ComplaintCategory]] = mapped_column(
        enum_col(ComplaintCategory), index=True
    )
    priority: Mapped[Optional[Priority]] = mapped_column(enum_col(Priority), index=True)
    severity_note: Mapped[Optional[str]] = mapped_column(Text)

    # --- verification ---
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    verification_confidence: Mapped[Optional[float]] = mapped_column(Float)
    verification_reason: Mapped[Optional[str]] = mapped_column(Text)
    rejection_reason: Mapped[Optional[str]] = mapped_column(Text)
    needs_human_review: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    # --- clustering ---
    nearby_count: Mapped[int] = mapped_column(Integer, default=0)
    is_hotspot: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    cluster_summary: Mapped[Optional[str]] = mapped_column(Text)

    # --- dispatch ---
    department_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("departments.id"), index=True
    )
    external_ticket_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    dispatched_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    #: Set when the owning department opens the ticket in their portal.
    acknowledged_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    acknowledged_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))

    # --- sla ---
    sla_policy_id: Mapped[Optional[int]] = mapped_column(ForeignKey("sla_policies.id"))
    sla_due_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), index=True
    )
    sla_escalate_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    sla_breached: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    escalation_level: Mapped[int] = mapped_column(Integer, default=0)

    # --- resolution ---
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    resolution_note: Mapped[Optional[str]] = mapped_column(Text)
    resolution_message: Mapped[Optional[str]] = mapped_column(Text)
    field_outcome: Mapped[Optional[str]] = mapped_column(String(32))

    reporter: Mapped[Optional[User]] = relationship(
        back_populates="complaints", foreign_keys=[reporter_id]
    )
    acknowledged_by: Mapped[Optional[User]] = relationship(
        foreign_keys=[acknowledged_by_id]
    )
    ward: Mapped[Optional[Ward]] = relationship(back_populates="complaints")
    department: Mapped[Optional[Department]] = relationship(back_populates="complaints")
    sla_policy: Mapped[Optional[SLAPolicy]] = relationship()
    evidence: Mapped[list["ComplaintEvidence"]] = relationship(
        back_populates="complaint", cascade="all, delete-orphan"
    )
    events: Mapped[list["ComplaintEvent"]] = relationship(
        back_populates="complaint",
        cascade="all, delete-orphan",
        order_by="ComplaintEvent.id",
    )
    agent_runs: Mapped[list["AgentRun"]] = relationship(
        back_populates="complaint",
        cascade="all, delete-orphan",
        order_by="AgentRun.id",
    )

    __table_args__ = (
        Index("ix_complaints_status_category", "status", "category"),
        Index("ix_complaints_created_at", "created_at"),
    )


class ComplaintEvidence(Base):
    """Per-submission evidence record and authenticity signal set.

    Stored in full so an acceptance or rejection can be re-explained later from
    the exact signals that produced it, and so duplicate detection has a hash
    corpus to query against.
    """

    __tablename__ = "complaint_evidence"

    id: Mapped[int] = mapped_column(primary_key=True)
    complaint_id: Mapped[int] = mapped_column(
        ForeignKey("complaints.id"), index=True, nullable=False
    )

    # --- Layer 1: server-bound capture ---
    source: Mapped[EvidenceSource] = mapped_column(
        enum_col(EvidenceSource), default=EvidenceSource.NONE
    )
    capture_token_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    capture_token_issued_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    capture_token_valid: Mapped[bool] = mapped_column(Boolean, default=False)
    server_received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    # --- media ---
    media_path: Mapped[Optional[str]] = mapped_column(String(400))
    media_mime: Mapped[Optional[str]] = mapped_column(String(80))
    media_bytes: Mapped[Optional[int]] = mapped_column(Integer)
    image_width: Mapped[Optional[int]] = mapped_column(Integer)
    image_height: Mapped[Optional[int]] = mapped_column(Integer)

    # --- Layer 2: forensics ---
    sha256: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    phash: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    exif: Mapped[dict] = mapped_column(JSON, default=dict)
    exif_present: Mapped[bool] = mapped_column(Boolean, default=False)
    exif_datetime: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    exif_time_delta_seconds: Mapped[Optional[int]] = mapped_column(Integer)
    exif_gps_distance_meters: Mapped[Optional[float]] = mapped_column(Float)
    editing_software: Mapped[Optional[str]] = mapped_column(String(200))

    # --- Layer 3: location plausibility ---
    gps_accuracy_meters: Mapped[Optional[float]] = mapped_column(Float)
    location_source: Mapped[LocationSource] = mapped_column(
        enum_col(LocationSource), default=LocationSource.UNKNOWN
    )
    mock_location_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    inside_serviced_ward: Mapped[Optional[bool]] = mapped_column(Boolean)
    reporter_speed_kmh: Mapped[Optional[float]] = mapped_column(Float)

    # --- Layer 4: duplicate detection ---
    duplicate_of_complaint_id: Mapped[Optional[int]] = mapped_column(Integer)
    duplicate_distance: Mapped[Optional[int]] = mapped_column(Integer)
    exact_duplicate: Mapped[bool] = mapped_column(Boolean, default=False)

    # --- scoring ---
    signals: Mapped[list] = mapped_column(JSON, default=list)
    authenticity_score: Mapped[float] = mapped_column(Float, default=0.0)
    outcome: Mapped[AuthenticityOutcome] = mapped_column(
        enum_col(AuthenticityOutcome), default=AuthenticityOutcome.HUMAN_REVIEW
    )
    hard_fail_reason: Mapped[Optional[str]] = mapped_column(Text)

    complaint: Mapped[Complaint] = relationship(back_populates="evidence")


class ComplaintEvent(Base):
    """Append-only audit trail.

    ``prev_hash``/``entry_hash`` form a hash chain so the trail is
    tamper-evident: a state change cannot be backdated or quietly rewritten.
    """

    __tablename__ = "complaint_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    complaint_id: Mapped[int] = mapped_column(
        ForeignKey("complaints.id"), index=True, nullable=False
    )
    event_type: Mapped[EventType] = mapped_column(enum_col(EventType), nullable=False)
    actor: Mapped[str] = mapped_column(String(80), default="system")
    message: Mapped[Optional[str]] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )

    prev_hash: Mapped[Optional[str]] = mapped_column(String(64))
    entry_hash: Mapped[Optional[str]] = mapped_column(String(64))

    complaint: Mapped[Complaint] = relationship(back_populates="events")


class AgentRun(Base):
    """One row per agent/node execution — the observability record."""

    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    complaint_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("complaints.id"), index=True
    )
    agent_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[AgentStatus] = mapped_column(enum_col(AgentStatus), default=AgentStatus.OK)

    input_state: Mapped[dict] = mapped_column(JSON, default=dict)
    output: Mapped[dict] = mapped_column(JSON, default=dict)
    rationale: Mapped[Optional[str]] = mapped_column(Text)
    confidence: Mapped[Optional[float]] = mapped_column(Float)

    provider: Mapped[Optional[str]] = mapped_column(String(32))
    model: Mapped[Optional[str]] = mapped_column(String(80))
    is_ai: Mapped[bool] = mapped_column(Boolean, default=False)
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer)
    error: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )

    complaint: Mapped[Optional[Complaint]] = relationship(back_populates="agent_runs")


class SocialPost(Base):
    """Public accountability content generated for eligible complaints."""

    __tablename__ = "social_posts"

    id: Mapped[int] = mapped_column(primary_key=True)
    complaint_id: Mapped[int] = mapped_column(
        ForeignKey("complaints.id"), index=True, nullable=False
    )
    kind: Mapped[str] = mapped_column(String(24), default="amplification")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    channel: Mapped[str] = mapped_column(String(24), default="internal")
    eligibility_reason: Mapped[Optional[str]] = mapped_column(Text)
    is_published: Mapped[bool] = mapped_column(Boolean, default=False)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    complaint: Mapped[Complaint] = relationship()


class CaptureToken(Base):
    """Issued capture tokens (Layer 1).

    Persisted so a token can be single-use: replaying one is detectable.
    """

    __tablename__ = "capture_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))
    device_id: Mapped[Optional[str]] = mapped_column(String(120))
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    issue_latitude: Mapped[Optional[float]] = mapped_column(Float)
    issue_longitude: Mapped[Optional[float]] = mapped_column(Float)
    consumed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class ProviderCredential(Base, TimestampMixin):
    """An AI provider's API key and model, set from the admin dashboard.

    Exists so a deployment can be pointed at a model WITHOUT redeploying or
    editing a .env on the host. The published aiKart container cannot ship a
    key (a public image is a public key), and an operator should not need shell
    access to rotate one.

    The key is stored encrypted (see app/core/crypto.py) and is never returned
    by any endpoint in full. A row here OVERRIDES the environment for that
    provider; deleting it falls back to the environment, so an existing .env
    deployment keeps working untouched.
    """

    __tablename__ = "provider_credentials"

    id: Mapped[int] = mapped_column(primary_key=True)
    #: "openai" | "gemini" | "nvidia" — matches app.ai.factory's registry.
    provider: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    #: Fernet ciphertext. Nullable so a model can be pinned without a key
    #: (useful when the key still comes from the environment).
    api_key_encrypted: Mapped[Optional[str]] = mapped_column(Text)
    model: Mapped[Optional[str]] = mapped_column(String(128))
    #: Exactly one row may be active; it selects the live provider at runtime.
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    updated_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
