"""Domain enumerations.

These are the controlled vocabularies the deterministic layer enforces. AI
nodes may *propose* a value but the proposal is always coerced back into one of
these members before it is persisted.
"""

from __future__ import annotations

from enum import Enum


class ComplaintStatus(str, Enum):
    SUBMITTED = "SUBMITTED"
    PENDING_REVIEW = "PENDING_REVIEW"
    REJECTED = "REJECTED"
    VERIFIED = "VERIFIED"
    CLASSIFIED = "CLASSIFIED"
    LOCATED = "LOCATED"
    DISPATCHED = "DISPATCHED"
    IN_PROGRESS = "IN_PROGRESS"
    RESOLVED = "RESOLVED"
    ESCALATED = "ESCALATED"

    @property
    def is_terminal(self) -> bool:
        return self in {ComplaintStatus.RESOLVED, ComplaintStatus.REJECTED}


class ComplaintCategory(str, Enum):
    POTHOLE = "POTHOLE"
    ROAD_DAMAGE = "ROAD_DAMAGE"
    WATER_LEAK = "WATER_LEAK"
    PIPELINE_BURST = "PIPELINE_BURST"
    DRAINAGE = "DRAINAGE"
    SEWAGE = "SEWAGE"
    GARBAGE = "GARBAGE"
    STREETLIGHT = "STREETLIGHT"
    POWER_OUTAGE = "POWER_OUTAGE"
    OTHER = "OTHER"


class Priority(str, Enum):
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"
    P4 = "P4"


class EvidenceSource(str, Enum):
    CAMERA = "camera"
    GALLERY = "gallery"
    NONE = "none"


class AuthenticityOutcome(str, Enum):
    AUTO_ACCEPT = "AUTO_ACCEPT"
    ACCEPT_FLAGGED = "ACCEPT_FLAGGED"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    HARD_FAIL = "HARD_FAIL"


class AgentStatus(str, Enum):
    OK = "OK"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class UserRole(str, Enum):
    """Who the account belongs to.

    DEPT_ADMIN is scoped to exactly one department: the portal only ever shows
    complaints routed to that agency. SUPER_ADMIN sees the whole city.
    """

    CITIZEN = "CITIZEN"
    DEPT_ADMIN = "DEPT_ADMIN"
    SUPER_ADMIN = "SUPER_ADMIN"

    @property
    def is_staff(self) -> bool:
        return self in {UserRole.DEPT_ADMIN, UserRole.SUPER_ADMIN}


class LocationSource(str, Enum):
    """How the reported coordinate was obtained.

    A device GPS fix and a pin dragged onto a map are not equivalent evidence.
    The first is measured; the second is asserted by the reporter, carries no
    accuracy radius, and is recorded as such so the evidence engine can weigh
    it accordingly.
    """

    DEVICE_GPS = "device_gps"
    MAP_PICKED = "map_picked"
    GEOCODED = "geocoded"
    UNKNOWN = "unknown"


class EventType(str, Enum):
    CREATED = "CREATED"
    EVIDENCE_CHECKED = "EVIDENCE_CHECKED"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"
    HELD_FOR_REVIEW = "HELD_FOR_REVIEW"
    CLASSIFIED = "CLASSIFIED"
    LOCATED = "LOCATED"
    DISPATCHED = "DISPATCHED"
    SLA_STARTED = "SLA_STARTED"
    SLA_BREACHED = "SLA_BREACHED"
    ESCALATED = "ESCALATED"
    SOCIAL_POSTED = "SOCIAL_POSTED"
    STATUS_CHANGED = "STATUS_CHANGED"
    RESOLVED = "RESOLVED"
    REVIEW_DECISION = "REVIEW_DECISION"
