"""Evidence authenticity engine — Layers 1-4 and 6, plus scoring.

Design stance, restated from the README so it is visible at the call site:

  * Nothing the client asserts about time or place is treated as fact.
  * Each signal is stored, never collapsed into a pass/fail bit, so an
    acceptance or rejection can be re-explained later from the exact signals
    that produced it.
  * Falsely rejecting a real pothole is worse than processing an occasional
    fake one. Weak signals therefore *reduce a score* and route toward human
    review. Outright rejection is reserved for hard failures.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.core.enums import AuthenticityOutcome, EvidenceSource
from app.core.exif import ImageFacts, extract_image_facts
from app.core.geo import haversine_meters, inside_city_envelope, resolve_ward
from app.core.hashing import dhash, hamming_distance, sha256_bytes
from app.core.security import TokenCheck, verify_capture_token
from app.models import User, utcnow

# How many recent fingerprints to compare against. A production deployment
# would use a BK-tree or a bit-string index instead of a bounded scan.
PHASH_SCAN_LIMIT = 5000

SEVERITY_INFO = "info"
SEVERITY_WARN = "warn"
SEVERITY_FAIL = "fail"


@dataclass
class Signal:
    code: str
    label: str
    severity: str
    detail: str
    delta: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EvidenceAssessment:
    """Everything the evidence stage learned, ready to persist verbatim."""

    score: float = 1.0
    outcome: AuthenticityOutcome = AuthenticityOutcome.HUMAN_REVIEW
    signals: list[Signal] = field(default_factory=list)
    #: Every unambiguous failure found, in detection order. Checks append
    #: rather than assign so one failure never masks another in the audit
    #: record — a replayed token *and* a reused image are both worth knowing.
    hard_fail_reasons: list[str] = field(default_factory=list)
    #: Set by signals that are suspicious but not conclusive. Caps the outcome
    #: at HUMAN_REVIEW instead of rejecting a possibly-genuine report.
    force_review: bool = False

    # Layer 1
    source: EvidenceSource = EvidenceSource.NONE
    capture_token_id: Optional[str] = None
    capture_token_issued_at: Optional[datetime] = None
    capture_token_valid: bool = False
    server_received_at: datetime = field(default_factory=utcnow)

    # media
    sha256: Optional[str] = None
    phash: Optional[str] = None
    media_bytes: Optional[int] = None
    image_width: Optional[int] = None
    image_height: Optional[int] = None

    # Layer 2
    exif: dict = field(default_factory=dict)
    exif_present: bool = False
    exif_datetime: Optional[datetime] = None
    exif_time_delta_seconds: Optional[int] = None
    exif_gps_distance_meters: Optional[float] = None
    editing_software: Optional[str] = None

    # Layer 3
    gps_accuracy_meters: Optional[float] = None
    mock_location_flag: bool = False
    inside_serviced_ward: Optional[bool] = None
    reporter_speed_kmh: Optional[float] = None

    # Layer 4
    duplicate_of_complaint_id: Optional[int] = None
    duplicate_distance: Optional[int] = None
    exact_duplicate: bool = False

    def add(
        self, code: str, label: str, severity: str, detail: str, delta: float = 0.0
    ) -> None:
        self.signals.append(Signal(code, label, severity, detail, delta))

    def fail(self, code: str, label: str, detail: str) -> None:
        """Record an unambiguous failure."""
        self.hard_fail_reasons.append(detail)
        self.add(code, label, SEVERITY_FAIL, detail)

    @property
    def hard_fail_reason(self) -> Optional[str]:
        if not self.hard_fail_reasons:
            return None
        if len(self.hard_fail_reasons) == 1:
            return self.hard_fail_reasons[0]
        return " ".join(
            f"({i}) {reason}" for i, reason in enumerate(self.hard_fail_reasons, 1)
        )

    @property
    def signal_codes(self) -> list[str]:
        return [s.code for s in self.signals]

    def signals_as_json(self) -> list[dict[str, Any]]:
        return [s.to_dict() for s in self.signals]


def _aware(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _find_duplicate(
    db: Session, sha: Optional[str], ph: Optional[str]
) -> tuple[Optional[int], Optional[int], bool]:
    """Return (complaint_id, phash_distance, exact) for the closest prior match."""
    if sha:
        exact = db.execute(
            text(
                "SELECT complaint_id FROM complaint_evidence "
                "WHERE sha256 = :sha ORDER BY id ASC LIMIT 1"
            ),
            {"sha": sha},
        ).first()
        if exact:
            return int(exact.complaint_id), 0, True

    if not ph:
        return None, None, False

    rows = db.execute(
        text(
            "SELECT complaint_id, phash FROM complaint_evidence "
            "WHERE phash IS NOT NULL ORDER BY id DESC LIMIT :lim"
        ),
        {"lim": PHASH_SCAN_LIMIT},
    ).all()

    best_id: Optional[int] = None
    best_distance: Optional[int] = None
    for row in rows:
        distance = hamming_distance(ph, row.phash)
        if distance is None:
            continue
        if best_distance is None or distance < best_distance:
            best_distance, best_id = distance, int(row.complaint_id)

    if best_distance is not None and best_distance <= settings.phash_duplicate_distance:
        return best_id, best_distance, False
    return None, best_distance, False


def _reporter_velocity(
    db: Session, reporter_id: Optional[int], lat: float, lon: float
) -> Optional[float]:
    """Implied travel speed since this reporter's previous complaint."""
    if reporter_id is None:
        return None
    row = db.execute(
        text(
            "SELECT latitude, longitude, created_at FROM complaints "
            "WHERE reporter_id = :rid ORDER BY created_at DESC LIMIT 1"
        ),
        {"rid": reporter_id},
    ).first()
    if not row:
        return None
    previous = _aware(row.created_at)
    elapsed_h = (utcnow() - previous).total_seconds() / 3600.0
    if elapsed_h <= 0:
        return None
    metres = haversine_meters(row.latitude, row.longitude, lat, lon)
    if metres < 50:  # same place; speed is meaningless
        return 0.0
    return (metres / 1000.0) / max(elapsed_h, 1e-6)


def assess_evidence(
    db: Session,
    *,
    latitude: float,
    longitude: float,
    channel: str = "web",
    declared_source: str = "camera",
    capture_token: Optional[str] = None,
    image_bytes: Optional[bytes] = None,
    image_mime: Optional[str] = None,
    gps_accuracy_meters: Optional[float] = None,
    mock_location: bool = False,
    reporter: Optional[User] = None,
) -> EvidenceAssessment:
    """Run the deterministic evidence layers and produce a scored assessment."""
    a = EvidenceAssessment()
    a.gps_accuracy_meters = gps_accuracy_meters
    a.mock_location_flag = bool(mock_location)

    # ---------------- Layer 1: server-bound capture ----------------
    token_required = channel == "web" and declared_source == EvidenceSource.CAMERA.value
    check: TokenCheck = verify_capture_token(db, capture_token)
    a.capture_token_valid = check.valid
    a.capture_token_id = check.token_id
    a.capture_token_issued_at = check.issued_at

    if check.valid:
        a.source = EvidenceSource(declared_source) if declared_source in {
            s.value for s in EvidenceSource
        } else EvidenceSource.CAMERA
        a.add(
            "capture_token_valid",
            "Server-bound capture confirmed",
            SEVERITY_INFO,
            f"Token issued {check.age_seconds}s before upload "
            f"(TTL {settings.capture_token_ttl_seconds}s).",
        )
    elif check.is_replay:
        a.fail(
            "token_replayed",
            "Capture token replayed",
            "Capture token had already been used. A replayed token cannot "
            "establish when this photo was taken.",
        )
    elif token_required:
        a.fail(
            "capture_token_invalid",
            "Capture token missing or invalid",
            f"No valid server-bound capture ({check.reason}). The submission "
            "claims an in-app camera capture but cannot prove one.",
        )
    else:
        # Telegram and gallery paths legitimately have no token: accept, weigh lower.
        a.source = (
            EvidenceSource.TELEGRAM
            if channel == "telegram"
            else EvidenceSource(declared_source)
            if declared_source in {s.value for s in EvidenceSource}
            else EvidenceSource.GALLERY
        )
        a.add(
            "capture_unbound",
            "No server-bound capture",
            SEVERITY_WARN,
            f"Channel '{channel}' submitted without a capture token "
            f"({check.reason}); evidence age cannot be bounded server-side.",
            delta=-0.18,
        )

    if a.source == EvidenceSource.GALLERY:
        a.add(
            "source_gallery",
            "Gallery upload",
            SEVERITY_WARN,
            "Photo chosen from the device gallery rather than captured in-app.",
            delta=-0.12,
        )

    # ---------------- Layer 2: media + forensics ----------------
    if not image_bytes:
        a.add(
            "no_photo",
            "No photographic evidence",
            SEVERITY_WARN,
            "Text-only report; nothing to corroborate the description visually.",
            delta=-0.30,
        )
    else:
        a.media_bytes = len(image_bytes)
        a.sha256 = sha256_bytes(image_bytes)
        a.phash = dhash(image_bytes)
        facts: ImageFacts = extract_image_facts(image_bytes)
        a.image_width, a.image_height = facts.width, facts.height
        a.exif = facts.exif
        a.exif_present = facts.exif_present
        a.exif_datetime = facts.exif_datetime
        a.editing_software = str(facts.software) if facts.software else None

        if not facts.readable:
            a.fail(
                "media_unreadable",
                "Unreadable media",
                f"Uploaded file is not a readable image ({facts.error}).",
            )
        else:
            if not facts.exif_present:
                a.add(
                    "exif_absent",
                    "EXIF metadata absent",
                    SEVERITY_WARN,
                    "No EXIF block: consistent with a screenshot, re-encode, "
                    "messaging-app forward or web download.",
                    delta=-0.10,
                )
            if facts.exif_datetime:
                delta_s = int(
                    abs((a.server_received_at - facts.exif_datetime).total_seconds())
                )
                a.exif_time_delta_seconds = delta_s
                if delta_s > settings.exif_time_tolerance_seconds:
                    a.add(
                        "exif_time_mismatch",
                        "Photo predates the reporting session",
                        SEVERITY_WARN,
                        f"EXIF capture time is {delta_s // 60} min from server "
                        f"receipt (tolerance "
                        f"{settings.exif_time_tolerance_seconds // 60} min).",
                        delta=-0.25,
                    )
                else:
                    a.add(
                        "exif_time_consistent",
                        "EXIF time consistent",
                        SEVERITY_INFO,
                        f"EXIF capture time within {delta_s}s of server receipt.",
                    )
            if facts.has_gps:
                gps_distance = haversine_meters(
                    facts.exif_latitude, facts.exif_longitude, latitude, longitude
                )
                a.exif_gps_distance_meters = gps_distance
                if gps_distance > settings.exif_gps_tolerance_meters:
                    a.add(
                        "exif_gps_mismatch",
                        "Photo GPS differs from report location",
                        SEVERITY_WARN,
                        f"Embedded GPS is {gps_distance:.0f} m from the reported "
                        f"point (tolerance {settings.exif_gps_tolerance_meters:.0f} m).",
                        delta=-0.20,
                    )
                else:
                    a.add(
                        "exif_gps_consistent",
                        "Photo GPS matches report",
                        SEVERITY_INFO,
                        f"Embedded GPS is {gps_distance:.0f} m from the reported point.",
                    )
            if facts.software:
                a.add(
                    "editing_software",
                    "Editing software tag present",
                    SEVERITY_WARN,
                    f"Image carries a Software tag: {facts.software}.",
                    delta=-0.15,
                )
            if facts.looks_like_screenshot:
                a.add(
                    "screenshot_dimensions",
                    "Screen-capture dimensions",
                    SEVERITY_WARN,
                    f"{facts.width}x{facts.height} matches a common screen size "
                    "rather than a camera frame.",
                    delta=-0.10,
                )

        # ---------------- Layer 4: duplicate / reuse ----------------
        dup_id, dup_distance, exact = _find_duplicate(db, a.sha256, a.phash)
        a.duplicate_distance = dup_distance
        a.exact_duplicate = exact
        if dup_id is not None:
            a.duplicate_of_complaint_id = dup_id
            if exact:
                a.fail(
                    "image_reused_exact",
                    "Identical image already submitted",
                    f"This exact file was already submitted with complaint id "
                    f"{dup_id} (byte-identical SHA-256).",
                )
            elif (
                dup_distance is not None
                and dup_distance <= settings.phash_hard_fail_distance
            ):
                a.fail(
                    "image_reused_near_exact",
                    "Same image re-encoded",
                    f"This image is effectively identical to the one on "
                    f"complaint id {dup_id} (perceptual distance "
                    f"{dup_distance}), consistent with a rescale or re-encode "
                    f"of the same photo.",
                )
            else:
                # Suggestive, not conclusive: two genuine photos of the same
                # pothole minutes apart can land here. Route to a human.
                a.force_review = True
                a.add(
                    "image_similar",
                    "Visually similar to an earlier complaint",
                    SEVERITY_WARN,
                    f"Perceptual distance {dup_distance} from the image on "
                    f"complaint id {dup_id} (reuse threshold "
                    f"{settings.phash_duplicate_distance}). Could be genuine "
                    f"corroboration of the same issue or a reused photo — "
                    f"held for a human to decide.",
                    delta=-0.40,
                )

    # ---------------- Layer 3: location plausibility ----------------
    if not inside_city_envelope(db, latitude, longitude):
        a.inside_serviced_ward = False
        a.fail(
            "outside_service_area",
            "Outside serviced area",
            f"Coordinates fall outside the serviced area ({settings.city}).",
        )
    else:
        ward = resolve_ward(db, latitude, longitude)
        a.inside_serviced_ward = ward.ward_id is not None
        if ward.ward_id is None:
            a.add(
                "no_ward_match",
                "No ward could be resolved",
                SEVERITY_WARN,
                "Point is inside the city envelope but not near any configured ward.",
                delta=-0.15,
            )
        elif not ward.exact:
            a.add(
                "ward_approximate",
                "Ward assigned by proximity",
                SEVERITY_INFO,
                f"Point lies {ward.distance_meters:.0f} m outside "
                f"{ward.ward_name}; assigned as nearest ward.",
                delta=-0.03,
            )
        else:
            a.add(
                "ward_exact",
                "Ward confirmed by containment",
                SEVERITY_INFO,
                f"Point is inside ward {ward.ward_number} ({ward.ward_name}).",
            )

    if gps_accuracy_meters is not None:
        if gps_accuracy_meters > settings.gps_accuracy_max_meters:
            a.add(
                "gps_accuracy_poor",
                "GPS fix too imprecise",
                SEVERITY_WARN,
                f"Accuracy radius {gps_accuracy_meters:.0f} m exceeds the "
                f"{settings.gps_accuracy_max_meters:.0f} m limit, which cannot "
                "support a ward assignment.",
                delta=-0.20,
            )
        else:
            a.add(
                "gps_accuracy_ok",
                "GPS fix precise enough",
                SEVERITY_INFO,
                f"Accuracy radius {gps_accuracy_meters:.0f} m.",
            )

    if mock_location:
        # Ward and department are both derived from this coordinate. If the
        # fix itself is spoofed, the routing decision cannot be trusted either,
        # so this never auto-dispatches regardless of the arithmetic.
        a.force_review = True
        a.add(
            "mock_location",
            "Mock location reported by device",
            SEVERITY_WARN,
            "The OS flagged this fix as coming from a mock location provider; "
            "ward and department routing derive from this coordinate, so the "
            "report is held for a human.",
            delta=-0.35,
        )

    speed = _reporter_velocity(db, reporter.id if reporter else None, latitude, longitude)
    a.reporter_speed_kmh = speed
    if speed is not None and speed > settings.reporter_max_speed_kmh:
        a.add(
            "velocity_implausible",
            "Implausible travel since last report",
            SEVERITY_WARN,
            f"Implied speed {speed:.0f} km/h since this reporter's previous "
            f"complaint exceeds the {settings.reporter_max_speed_kmh:.0f} km/h limit.",
            delta=-0.25,
        )

    # ---------------- Layer 6 (partial): reporter reputation ----------------
    if reporter is not None:
        if reporter.reports_rejected >= 3 and reporter.trust_score < 0.3:
            a.add(
                "reporter_low_trust",
                "Reporter has a low trust score",
                SEVERITY_WARN,
                f"Trust {reporter.trust_score:.2f} after "
                f"{reporter.reports_rejected} rejected reports.",
                delta=-0.15,
            )
        elif reporter.trust_score >= 0.8 and reporter.reports_confirmed >= 3:
            a.add(
                "reporter_trusted",
                "Reporter has a strong track record",
                SEVERITY_INFO,
                f"Trust {reporter.trust_score:.2f} across "
                f"{reporter.reports_confirmed} confirmed reports.",
                delta=0.05,
            )
        if not reporter.is_verified:
            a.add(
                "reporter_unverified",
                "Reporter identity unverified",
                SEVERITY_WARN,
                "No phone or Telegram verification on this account.",
                delta=-0.08,
            )

    # ---------------- scoring + banding ----------------
    a.score = max(0.0, min(1.0, 1.0 + sum(s.delta for s in a.signals)))
    if a.hard_fail_reasons:
        a.score = 0.0
        a.outcome = AuthenticityOutcome.HARD_FAIL
    elif a.force_review:
        # An inconclusive-but-serious signal caps the outcome at review,
        # whatever the arithmetic says.
        a.outcome = AuthenticityOutcome.HUMAN_REVIEW
    elif a.score >= settings.authenticity_auto_accept:
        a.outcome = AuthenticityOutcome.AUTO_ACCEPT
    elif a.score >= settings.authenticity_review_floor:
        a.outcome = AuthenticityOutcome.ACCEPT_FLAGGED
    else:
        a.outcome = AuthenticityOutcome.HUMAN_REVIEW
    return a
