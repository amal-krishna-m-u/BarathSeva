"""HTTP surface for the mock municipal ticketing APIs.

``app.integrations.mock_gov`` already simulates BBMP, BWSSB and BESCOM
in-process for the agent pipeline — ``MockGovernmentGateway.create_ticket``
is a plain Python call with a single caller, ``app.agents.dispatcher``. That
means the mock has never had a wire surface: nothing exercises it over HTTP,
and the deliberately heterogeneous response shapes it returns have never
been serialised and parsed back. This module puts the same three dialects on
the wire — ``POST /mock/gov/<dept>/<noun>`` — so a later task's HTTP gateway
client can be written and tested against a real request/response round trip
instead of an in-process function call. The in-process path through
``app.integrations.mock_gov.gateway`` remains the default for the dispatcher
and is untouched by this module.

Nothing here is a real integration. These routes impersonate no real
municipal system, carry no agency credentials, and exist only to give the
prototype something to talk to. They are reachable only when
``settings.mock_apis_enabled`` is true — never in production — because
``app.main.register_mock_routers`` is the single place that decides which
mock routers get wired up, and it checks that property rather than the raw
``enable_mock_apis`` flag. Real municipal connectivity requires formal
access and credentials this project does not hold; see "Prototype vs
Production" in the README.

Ticket ids are produced by ``_deterministic_suffix``, imported from
``app.integrations.mock_gov`` rather than copied, so the same ``reference``
yields the identical ticket id whether a caller goes through this HTTP
surface or the in-process gateway directly — a later task asserts that
equality directly, so it must hold by construction, not by coincidence.

Two things a real agency API would have that the original in-process mock
did not get until now:

* **Idempotency.** A repeat ``POST`` with the same ``reference`` for the
  same department returns the original ticket, unchanged, with ``200`` —
  never a second ticket. The store is a plain in-process dict; it does not
  survive a process restart, which is fine for a mock.
* **Failure injection reachable without editing Python.** The
  ``failing_departments`` attribute on ``MockGovernmentGateway`` existed
  before this module but nothing in the app or test suite ever populated
  it outside a unit test constructing the object directly — a simulated
  department outage was literally unreachable from outside a Python
  debugger. ``POST /mock/gov/_control`` exposes that same knob over HTTP
  (``GET`` reads it back), and the ``X-Mock-Failure`` request header adds
  three more per-request failure shapes that need no shared state at all.
  See ``_maybe_fail`` below for the exact contract.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from app.integrations.mock_gov import MockGovernmentGateway, _deterministic_suffix

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mock/gov", tags=["mock-gov"])

#: A gateway instance scoped to this module, not the
#: ``app.integrations.mock_gov.gateway`` singleton the dispatcher uses. The
#: HTTP ``_control`` endpoint below mutates ``failing_departments`` on this
#: instance; keeping it separate means poking the HTTP control surface in a
#: test can never change behaviour on the in-process path a different test
#: might be exercising at the same time. Both instances call the same
#: ``_deterministic_suffix`` function, so ticket ids still agree for the
#: same (department, reference) regardless of which instance produced them.
_gateway = MockGovernmentGateway()

#: source_reference -> the exact response body returned for the first POST,
#: keyed per department so two departments can reuse the same reference
#: without colliding. Repeat POSTs replay this instead of minting a new
#: ticket.
_tickets_by_reference: dict[tuple[str, str], dict[str, Any]] = {}

#: (department_code, ticket_id) -> response body, for GET .../ticket/{id}.
_tickets_by_id: dict[tuple[str, str], dict[str, Any]] = {}


@dataclass
class _ControlState:
    failing_departments: set[str] = field(default_factory=set)
    latency_ms: int = 0


_control = _ControlState()

#: Salt each department's dialect uses with _deterministic_suffix, mirrored
#: from app.integrations.mock_gov so _create_ticket can verify (not derive)
#: that the id MockGovernmentGateway handed back is the same one the real
#: suffix function would produce for this (department, reference) pair.
_SALTS = {"BBMP": "bbmp", "BWSSB": "bwssb", "BESCOM": "bescom"}


def reset_mock_gov_state() -> None:
    """Clear the idempotency store and the ``_control`` state.

    Both are module-level mutable state, so without this one test's
    injected failure or idempotent ticket could leak into the next.
    Importable directly by tests; there is no HTTP route for this because,
    unlike a failure mode, resetting is never something the system under
    test (a later task's gateway client) needs to trigger itself.
    """
    _tickets_by_reference.clear()
    _tickets_by_id.clear()
    _control.failing_departments = set()
    _control.latency_ms = 0
    _gateway.failing_departments = set()


# --------------------------------------------------------------------------
# Request/response shapes
# --------------------------------------------------------------------------


class _TicketRequest(BaseModel):
    """What a department intake endpoint needs, independent of its dialect.

    Mirrors the keyword arguments ``MockGovernmentGateway.create_ticket``
    already takes, since that is exactly what this body is routed into.
    """

    reference: str
    category: str = "other"
    priority: str = "medium"
    ward_name: Optional[str] = None
    description: str = ""
    latitude: float = 0.0
    longitude: float = 0.0


class _ControlRequest(BaseModel):
    failing_departments: list[str] = Field(default_factory=list)
    latency_ms: int = 0


def _control_state_body() -> dict[str, Any]:
    return {
        "failing_departments": sorted(_control.failing_departments),
        "latency_ms": _control.latency_ms,
    }


# --------------------------------------------------------------------------
# Failure injection — X-Mock-Failure header
# --------------------------------------------------------------------------

#: Default delay for X-Mock-Failure: slow, in milliseconds. Small on
#: purpose: a test asserting "slow still responds" must never actually wait
#: anywhere near real network latency, let alone seconds. Override per
#: request via POST /mock/gov/_control {"latency_ms": N} with a small N.
_DEFAULT_SLOW_LATENCY_MS = 50

_FAILURE_MODES = frozenset({"unavailable", "slow", "malformed", "rate_limit"})


async def _maybe_fail(department_code: str, request: Request) -> Optional[Response]:
    """Inspect X-Mock-Failure; return a short-circuit Response or None.

    Contract:
      X-Mock-Failure: unavailable | slow | malformed | rate_limit
        unavailable -> HTTP 503, ``{"error": "<DEPT> endpoint unavailable
                       (simulated)"}`` — the per-request twin of configuring
                       this department via POST /mock/gov/_control.
        slow        -> sleeps ``latency_ms`` from the current ``_control``
                       state (default 50ms, never seconds) and then falls
                       through to a normal, successful response.
        malformed   -> HTTP 200 whose body is not valid JSON, simulating an
                       agency endpoint that returns garbage instead of its
                       documented dialect.
        rate_limit  -> HTTP 429, ``{"error": "...", "retry_after_seconds": 1}``.
      Unrecognised values get HTTP 400 rather than being silently ignored,
      so a typo in a test header fails loudly instead of looking like a
      healthy department.
    """
    mode = request.headers.get("x-mock-failure")
    if not mode:
        return None
    mode = mode.strip().lower()
    if mode not in _FAILURE_MODES:
        return JSONResponse(
            status_code=400,
            content={"error": f"unknown X-Mock-Failure mode: {mode!r}"},
        )

    logger.info("mock gov injecting failure mode=%s department=%s", mode, department_code)

    if mode == "unavailable":
        return JSONResponse(
            status_code=503,
            content={"error": f"{department_code} endpoint unavailable (simulated)"},
        )
    if mode == "rate_limit":
        return JSONResponse(
            status_code=429,
            content={
                "error": f"{department_code} rate limit exceeded (simulated)",
                "retry_after_seconds": 1,
            },
        )
    if mode == "malformed":
        return Response(
            status_code=200,
            media_type="application/json",
            content="{not valid json, this is deliberately malformed }}",
        )
    if mode == "slow":
        delay_ms = _control.latency_ms or _DEFAULT_SLOW_LATENCY_MS
        await asyncio.sleep(delay_ms / 1000)
        return None  # fall through to a normal response, just delayed
    return None  # pragma: no cover - _FAILURE_MODES covers every branch above


# --------------------------------------------------------------------------
# Ticket creation — shared by all three dialects
# --------------------------------------------------------------------------


async def _create_ticket(department_code: str, payload: _TicketRequest, request: Request) -> Response:
    failure = await _maybe_fail(department_code, request)
    if failure is not None:
        return failure

    key = (department_code, payload.reference)
    existing = _tickets_by_reference.get(key)
    if existing is not None:
        return JSONResponse(status_code=200, content=existing)

    result = _gateway.create_ticket(
        department_code,
        reference=payload.reference,
        category=payload.category,
        priority=payload.priority,
        ward_name=payload.ward_name,
        description=payload.description,
        latitude=payload.latitude,
        longitude=payload.longitude,
    )

    if not result.ok:
        # Only reachable via POST /mock/gov/_control configuring this
        # department as failing — X-Mock-Failure: unavailable short-circuits
        # above before the gateway is ever called.
        return JSONResponse(status_code=503, content={"error": result.error})

    # Defence in depth, not derivation: create_ticket() already used
    # _deterministic_suffix internally to build result.ticket_id. This just
    # proves that id agrees with calling the real, imported function
    # ourselves, so a future edit to the handler methods in mock_gov.py that
    # quietly changed how a ticket id is formed would fail loudly here
    # instead of silently drifting the HTTP surface out of sync.
    expected_suffix = _deterministic_suffix(payload.reference, _SALTS[department_code])
    assert result.ticket_id and expected_suffix in result.ticket_id, (
        f"ticket id {result.ticket_id!r} does not contain the expected "
        f"_deterministic_suffix {expected_suffix!r} for {department_code}"
    )

    body = result.raw  # already the exact documented dialect shape, incl. echo
    _tickets_by_reference[key] = body
    if result.ticket_id:
        _tickets_by_id[(department_code, result.ticket_id)] = body
    return JSONResponse(status_code=200, content=body)


@router.post("/bbmp/grievance")
async def bbmp_grievance(payload: _TicketRequest, request: Request) -> Response:
    """BBMP dialect: ``{status, grievance_id, zone_office, received_on, echo}``."""
    return await _create_ticket("BBMP", payload, request)


@router.post("/bwssb/complaint")
async def bwssb_complaint(payload: _TicketRequest, request: Request) -> Response:
    """BWSSB dialect: ``{result, complaint_no, subdivision, logged_at, echo}``."""
    return await _create_ticket("BWSSB", payload, request)


@router.post("/bescom/ticket")
async def bescom_ticket(payload: _TicketRequest, request: Request) -> Response:
    """BESCOM dialect: ``{code, ticket_ref, section_office, timestamp, echo}``."""
    return await _create_ticket("BESCOM", payload, request)


# --------------------------------------------------------------------------
# Control + read-back
# --------------------------------------------------------------------------


@router.post("/_control", include_in_schema=False)
def set_control(body: _ControlRequest) -> dict[str, Any]:
    """Configure simulated department outages and the ``slow`` delay.

    Setting ``failing_departments`` here populates the same
    ``MockGovernmentGateway.failing_departments`` attribute the class has
    always had but which nothing outside a unit test could ever reach —
    this is that attribute's first HTTP-reachable door. Departments not
    named here (and left out entirely) succeed normally; a second call
    replaces the previous configuration rather than merging with it, so a
    test can always return to a known state with one POST.
    """
    _control.failing_departments = {code.upper() for code in body.failing_departments}
    _control.latency_ms = max(0, body.latency_ms)
    _gateway.failing_departments = set(_control.failing_departments)
    return _control_state_body()


@router.get("/_control", include_in_schema=False)
def get_control() -> dict[str, Any]:
    return _control_state_body()


@router.get("/{department_code}/ticket/{ticket_id}")
def get_ticket(department_code: str, ticket_id: str) -> dict[str, Any]:
    body = _tickets_by_id.get((department_code.upper(), ticket_id))
    if body is None:
        raise HTTPException(status_code=404, detail="ticket not found")
    return body
