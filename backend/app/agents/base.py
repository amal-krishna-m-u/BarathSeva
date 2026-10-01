"""Agent execution and recording.

Every node runs through ``execute`` so that three things are guaranteed
regardless of what the node does:

  1. Latency is measured.
  2. An ``agent_runs`` row is written — the observability record that makes any
     outcome explainable after the fact.
  3. An exception inside a node becomes a recorded FAILED run rather than a
     lost complaint.
"""

from __future__ import annotations

import logging
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from sqlalchemy.orm import Session

from app.core.enums import AgentStatus
from app.models import AgentRun

logger = logging.getLogger(__name__)


@dataclass
class AgentResult:
    """What a node decided, plus the provenance needed to audit it."""

    output: dict[str, Any] = field(default_factory=dict)
    rationale: str = ""
    confidence: Optional[float] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    is_ai: bool = False
    status: AgentStatus = AgentStatus.OK
    error: Optional[str] = None

    @classmethod
    def deterministic(
        cls, output: dict[str, Any], rationale: str, confidence: float = 1.0
    ) -> "AgentResult":
        return cls(
            output=output,
            rationale=rationale,
            confidence=confidence,
            provider="python",
            model="deterministic",
            is_ai=False,
        )


def _summarise_input(state: dict[str, Any]) -> dict[str, Any]:
    """Store a compact, JSON-safe snapshot of what the node was given."""
    skip = {"image_bytes"}
    summary: dict[str, Any] = {}
    for key, value in state.items():
        if key in skip:
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            summary[key] = value
        elif isinstance(value, (list, dict)):
            serialised = str(value)
            summary[key] = value if len(serialised) <= 2000 else f"<{type(value).__name__} len={len(value)}>"
        else:
            summary[key] = str(value)
    return summary


def execute(
    db: Session,
    *,
    agent_name: str,
    complaint_id: Optional[int],
    state: dict[str, Any],
    fn: Callable[[], AgentResult],
) -> AgentResult:
    """Run a node, record it, and never let it raise into the graph."""
    started = time.perf_counter()
    try:
        result = fn()
    except Exception as exc:  # noqa: BLE001 - recorded, not swallowed silently
        logger.exception("agent %s failed for complaint %s", agent_name, complaint_id)
        result = AgentResult(
            output={},
            rationale="",
            status=AgentStatus.FAILED,
            error=f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=3)}",
        )

    latency_ms = int((time.perf_counter() - started) * 1000)
    db.add(
        AgentRun(
            complaint_id=complaint_id,
            agent_name=agent_name,
            status=result.status,
            input_state=_summarise_input(state),
            output=result.output,
            rationale=result.rationale,
            confidence=result.confidence,
            provider=result.provider,
            model=result.model,
            is_ai=result.is_ai,
            latency_ms=latency_ms,
            error=result.error,
        )
    )
    db.flush()
    return result
