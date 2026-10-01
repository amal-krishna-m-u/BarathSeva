"""Mock municipal ticketing APIs.

These stand in for BBMP, BWSSB and BESCOM so the full lifecycle can be
demonstrated end to end without depending on live government systems. They
deliberately return *heterogeneous* response shapes — real agency APIs do not
agree on field names — so the adapter layer that normalises them is exercised
rather than assumed.

Nothing here is a real integration. Real municipal connectivity requires formal
access and credentials this project does not hold; see "Prototype vs
Production" in the README.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from hashlib import sha256
from typing import Any, Optional

from app.models import utcnow

logger = logging.getLogger(__name__)


class MockAPIError(RuntimeError):
    """Raised to simulate a department endpoint being unavailable."""


@dataclass
class TicketResponse:
    ok: bool
    department_code: str
    ticket_id: Optional[str] = None
    accepted_at: Optional[datetime] = None
    raw: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None


def _deterministic_suffix(reference: str, salt: str) -> str:
    """Stable pseudo-ticket id so demos and tests are reproducible."""
    digest = sha256(f"{salt}:{reference}".encode()).hexdigest()
    return digest[:6].upper()


class MockGovernmentGateway:
    """One gateway, three department dialects, one normalised result."""

    #: Set to a department code to make that endpoint fail (tests/demos).
    def __init__(self, failing_departments: Optional[set[str]] = None) -> None:
        self.failing_departments = failing_departments or set()

    def create_ticket(
        self,
        department_code: str,
        *,
        reference: str,
        category: str,
        priority: str,
        ward_name: Optional[str],
        description: str,
        latitude: float,
        longitude: float,
    ) -> TicketResponse:
        code = (department_code or "").upper()

        if code in self.failing_departments:
            return TicketResponse(
                ok=False,
                department_code=code,
                error=f"{code} endpoint unavailable (simulated)",
            )

        payload = {
            "category": category,
            "priority": priority,
            "ward": ward_name,
            "description": description[:500],
            "lat": latitude,
            "lon": longitude,
            "source": "BarathSeva AI",
            "source_reference": reference,
        }

        handler = {
            "BBMP": self._bbmp,
            "BWSSB": self._bwssb,
            "BESCOM": self._bescom,
        }.get(code)

        if handler is None:
            return TicketResponse(
                ok=False,
                department_code=code,
                error=f"No mock endpoint registered for department {code!r}",
            )

        raw = handler(reference, payload)
        ticket_id = (
            raw.get("grievance_id")
            or raw.get("complaint_no")
            or raw.get("ticket_ref")
        )
        logger.info("mock %s accepted %s as %s", code, reference, ticket_id)
        return TicketResponse(
            ok=True,
            department_code=code,
            ticket_id=ticket_id,
            accepted_at=utcnow(),
            raw=raw,
        )

    # --- department dialects -------------------------------------------------

    def _bbmp(self, reference: str, payload: dict) -> dict:
        return {
            "status": "ACCEPTED",
            "grievance_id": f"BBMP-{_deterministic_suffix(reference, 'bbmp')}",
            "zone_office": payload.get("ward") or "Central",
            "received_on": utcnow().strftime("%d-%m-%Y %H:%M"),
            "echo": payload,
        }

    def _bwssb(self, reference: str, payload: dict) -> dict:
        return {
            "result": "OK",
            "complaint_no": f"BWSSB/{_deterministic_suffix(reference, 'bwssb')}",
            "subdivision": payload.get("ward") or "Unassigned",
            "logged_at": utcnow().isoformat(),
            "echo": payload,
        }

    def _bescom(self, reference: str, payload: dict) -> dict:
        return {
            "code": 200,
            "ticket_ref": f"BESCOM{_deterministic_suffix(reference, 'bescom')}",
            "section_office": payload.get("ward") or "City",
            "timestamp": int(utcnow().timestamp()),
            "echo": payload,
        }


gateway = MockGovernmentGateway()
