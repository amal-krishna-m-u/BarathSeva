"""LangGraph orchestration — the control plane.

The graph is explicit: which node runs, in what order, under which conditions.
That is what makes a complaint's path deterministic and replayable, and it is
why a low-confidence or failed node routes to a human-review edge instead of
stalling the complaint or letting a model improvise the next step.

Each node opens its own session and commits. A node's decision therefore
survives a later node failing — the audit trail is never left half-written.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from langgraph.graph import END, StateGraph

from app.agents import classifier, dispatcher, geocluster, resolution, sla_monitor, social
from app.agents import verifier
from app.agents.base import AgentResult, execute
from app.core.enums import AgentStatus, AuthenticityOutcome, ComplaintStatus, EventType
from app.core.events import append_event
from app.db import SessionLocal
from app.models import Complaint
from app.workflow.state import ComplaintState

logger = logging.getLogger(__name__)

ROUTE_CONTINUE = "continue"
ROUTE_HALT = "halt"


def _load(db, complaint_id: int) -> Complaint:
    complaint = db.get(Complaint, complaint_id)
    if complaint is None:
        raise LookupError(f"complaint {complaint_id} not found")
    return complaint


def _trace(state: ComplaintState, node: str) -> list[str]:
    return [*state.get("trace", []), node]


# --------------------------------------------------------------------- nodes


def evidence_gate_node(state: ComplaintState) -> dict[str, Any]:
    """Read the persisted evidence verdict and decide whether to proceed.

    The forensics already ran at intake (they must, to decide whether a
    complaint row is even accepted). This node is where that verdict becomes a
    routing decision, and it is recorded as its own agent run so the gate is
    auditable like any other node.
    """
    with SessionLocal() as db:
        complaint = _load(db, state["complaint_id"])
        evidence = complaint.evidence[0] if complaint.evidence else None
        outcome = evidence.outcome if evidence else AuthenticityOutcome.HUMAN_REVIEW
        score = float(evidence.authenticity_score) if evidence else 0.0

        def _work() -> AgentResult:
            if outcome == AuthenticityOutcome.HARD_FAIL:
                decision, reason = ROUTE_HALT, (
                    evidence.hard_fail_reason if evidence else "Evidence hard-failed."
                )
            elif outcome == AuthenticityOutcome.HUMAN_REVIEW:
                decision, reason = ROUTE_HALT, (
                    f"Authenticity score {score:.2f} is below the review floor; "
                    "held for human review before any dispatch."
                )
            else:
                decision, reason = ROUTE_CONTINUE, (
                    f"Authenticity {outcome.value} at score {score:.2f}; "
                    "proceeding to verification."
                )
            return AgentResult.deterministic(
                output={
                    "decision": decision,
                    "authenticity_outcome": outcome.value,
                    "authenticity_score": score,
                    "flagged": outcome == AuthenticityOutcome.ACCEPT_FLAGGED,
                },
                rationale=reason,
            )

        result = execute(
            db,
            agent_name="evidence_gate",
            complaint_id=complaint.id,
            state=dict(state),
            fn=_work,
        )

        halted = result.output.get("decision") == ROUTE_HALT
        if halted and outcome == AuthenticityOutcome.HUMAN_REVIEW:
            complaint.needs_human_review = True
            complaint.status = ComplaintStatus.PENDING_REVIEW
            append_event(
                db,
                complaint.id,
                EventType.HELD_FOR_REVIEW,
                result.rationale,
                {"authenticity_score": score},
                actor="evidence_gate",
            )
        db.commit()

        return {
            "authenticity_score": score,
            "authenticity_outcome": outcome.value,
            "halted": halted,
            "halt_reason": result.rationale if halted else None,
            "needs_human_review": complaint.needs_human_review,
            "status": complaint.status.value,
            "trace": _trace(state, "evidence_gate"),
        }


def verifier_node(state: ComplaintState) -> dict[str, Any]:
    with SessionLocal() as db:
        complaint = _load(db, state["complaint_id"])
        result = verifier.run(db, complaint, dict(state))
        db.commit()
        halted = complaint.status in {
            ComplaintStatus.REJECTED,
            ComplaintStatus.PENDING_REVIEW,
        }
        return {
            "is_civic_issue": bool(result.output.get("is_civic_issue")),
            "evidence_sufficient": bool(result.output.get("evidence_sufficient")),
            "verification_confidence": result.confidence,
            "verification_reason": result.rationale,
            "category_hint": result.output.get("category_hint"),
            "halted": halted,
            "halt_reason": complaint.rejection_reason if halted else None,
            "needs_human_review": complaint.needs_human_review,
            "status": complaint.status.value,
            "trace": _trace(state, "verifier"),
        }


def classifier_node(state: ComplaintState) -> dict[str, Any]:
    with SessionLocal() as db:
        complaint = _load(db, state["complaint_id"])
        result = classifier.run(db, complaint, dict(state))
        db.commit()
        return {
            "category": result.output.get("category"),
            "priority": result.output.get("priority"),
            "status": complaint.status.value,
            "trace": _trace(state, "classifier"),
        }


def geocluster_node(state: ComplaintState) -> dict[str, Any]:
    with SessionLocal() as db:
        complaint = _load(db, state["complaint_id"])
        result = geocluster.run(db, complaint, dict(state))
        db.commit()
        return {
            "ward_id": result.output.get("ward_id"),
            "ward_name": result.output.get("ward_name"),
            "nearby_count": int(result.output.get("nearby_count") or 0),
            "is_hotspot": bool(result.output.get("is_hotspot")),
            # GeoCluster may deterministically raise priority on corroboration.
            "priority": complaint.priority.value if complaint.priority else None,
            "status": complaint.status.value,
            "trace": _trace(state, "geocluster"),
        }


def dispatcher_node(state: ComplaintState) -> dict[str, Any]:
    with SessionLocal() as db:
        complaint = _load(db, state["complaint_id"])
        result = dispatcher.run(db, complaint, dict(state))
        db.commit()
        dispatched = bool(result.output.get("dispatched"))
        return {
            "dispatched": dispatched,
            "department_code": result.output.get("department_code"),
            "external_ticket_id": result.output.get("external_ticket_id"),
            "halted": not dispatched,
            "halt_reason": None if dispatched else result.error,
            "needs_human_review": complaint.needs_human_review,
            "status": complaint.status.value,
            "trace": _trace(state, "dispatcher"),
        }


def sla_node(state: ComplaintState) -> dict[str, Any]:
    with SessionLocal() as db:
        complaint = _load(db, state["complaint_id"])
        result = sla_monitor.start(db, complaint, dict(state))
        db.commit()
        return {
            "sla_due_at": result.output.get("due_at"),
            "status": complaint.status.value,
            "trace": _trace(state, "sla_monitor"),
        }


def social_node(state: ComplaintState) -> dict[str, Any]:
    with SessionLocal() as db:
        complaint = _load(db, state["complaint_id"])
        result = social.run(db, complaint, dict(state))
        db.commit()
        return {
            "social_post_id": result.output.get("social_post_id"),
            "status": complaint.status.value,
            "trace": _trace(state, "social_amplifier"),
        }


def resolution_node(state: ComplaintState) -> dict[str, Any]:
    with SessionLocal() as db:
        complaint = _load(db, state["complaint_id"])
        result = resolution.run(
            db,
            complaint,
            dict(state),
            resolution_note=state.get("halt_reason") or "",
            field_outcome="GENUINE_FIXED",
        )
        db.commit()
        return {
            "status": complaint.status.value,
            "trace": _trace(state, "resolution_update"),
        }


# -------------------------------------------------------------------- routing


def _route(state: ComplaintState) -> str:
    return ROUTE_HALT if state.get("halted") else ROUTE_CONTINUE


def build_intake_graph():
    """Intake pipeline: evidence gate -> verify -> classify -> locate ->
    dispatch -> SLA -> amplify."""
    graph = StateGraph(ComplaintState)

    graph.add_node("evidence_gate", evidence_gate_node)
    graph.add_node("verifier", verifier_node)
    graph.add_node("classifier", classifier_node)
    graph.add_node("geocluster", geocluster_node)
    graph.add_node("dispatcher", dispatcher_node)
    graph.add_node("sla_monitor", sla_node)
    graph.add_node("social_amplifier", social_node)

    graph.set_entry_point("evidence_gate")
    graph.add_conditional_edges(
        "evidence_gate", _route, {ROUTE_CONTINUE: "verifier", ROUTE_HALT: END}
    )
    graph.add_conditional_edges(
        "verifier", _route, {ROUTE_CONTINUE: "classifier", ROUTE_HALT: END}
    )
    graph.add_edge("classifier", "geocluster")
    graph.add_edge("geocluster", "dispatcher")
    graph.add_conditional_edges(
        "dispatcher", _route, {ROUTE_CONTINUE: "sla_monitor", ROUTE_HALT: END}
    )
    graph.add_edge("sla_monitor", "social_amplifier")
    graph.add_edge("social_amplifier", END)

    return graph.compile()


def build_resolution_graph():
    """Resolution transition: generate the citizen message and close out."""
    graph = StateGraph(ComplaintState)
    graph.add_node("resolution_update", resolution_node)
    graph.set_entry_point("resolution_update")
    graph.add_edge("resolution_update", END)
    return graph.compile()


_intake_graph = None
_resolution_graph = None


def intake_graph():
    global _intake_graph
    if _intake_graph is None:
        _intake_graph = build_intake_graph()
    return _intake_graph


def resolution_graph():
    global _resolution_graph
    if _resolution_graph is None:
        _resolution_graph = build_resolution_graph()
    return _resolution_graph


def run_intake(complaint_id: int, channel: str = "web") -> ComplaintState:
    """Execute the intake pipeline for a persisted complaint."""
    initial: ComplaintState = {
        "complaint_id": complaint_id,
        "channel": channel,
        "halted": False,
        "needs_human_review": False,
        "trace": [],
    }
    return intake_graph().invoke(initial)
