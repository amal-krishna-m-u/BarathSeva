"""Social amplifier — policy decides *whether*, the model decides *how*.

Eligibility is a deterministic rule over severity, cluster size and SLA state.
Only once a complaint qualifies does a model draft the wording. Posts are
stored unpublished: the prototype never actually posts to an external network.
"""

from __future__ import annotations

from typing import Any, Optional

from sqlalchemy.orm import Session

from app.agents.base import AgentResult, execute
from app.ai.factory import infer
from app.ai.prompts import build_social
from app.config import settings
from app.core.enums import AgentStatus, EventType
from app.core.events import append_event
from app.models import Complaint, SocialPost

AGENT_NAME = "social_amplifier"


def check_eligibility(complaint: Complaint) -> tuple[bool, str]:
    """Deterministic policy gate. Returns (eligible, reason)."""
    reasons: list[str] = []
    priority = complaint.priority.value if complaint.priority else None

    if priority in settings.social_priority_list:
        reasons.append(f"priority {priority} is in the amplification policy")
    if complaint.nearby_count >= settings.social_min_cluster_size:
        reasons.append(
            f"{complaint.nearby_count} corroborating reports meet the "
            f"cluster threshold of {settings.social_min_cluster_size}"
        )
    if complaint.sla_breached:
        reasons.append("the SLA deadline has been breached")

    if not reasons:
        return False, (
            f"Not eligible: priority {priority} is outside "
            f"{settings.social_priority_list}, only {complaint.nearby_count} "
            f"nearby reports, and the SLA is not breached."
        )
    return True, "Eligible because " + "; and ".join(reasons) + "."


def run(db: Session, complaint: Complaint, state: dict[str, Any]) -> AgentResult:
    eligible, eligibility_reason = check_eligibility(complaint)

    def _work() -> AgentResult:
        if not eligible:
            return AgentResult(
                output={"eligible": False, "reason": eligibility_reason},
                rationale=eligibility_reason,
                confidence=1.0,
                provider="python",
                model="policy-rules",
                is_ai=False,
                status=AgentStatus.SKIPPED,
            )

        result = infer(
            build_social(
                reference=complaint.reference,
                category=complaint.category.value if complaint.category else None,
                priority=complaint.priority.value if complaint.priority else None,
                ward_name=complaint.ward.name if complaint.ward else None,
                department=complaint.department.code if complaint.department else None,
                nearby_count=complaint.nearby_count,
                sla_breached=complaint.sla_breached,
                sla_due_at_human=(
                    complaint.sla_due_at.strftime("%d %b %H:%M")
                    if complaint.sla_due_at
                    else None
                ),
            )
        )
        content = str(result.data.get("content") or "").strip()
        return AgentResult(
            output={"eligible": True, "reason": eligibility_reason, "content": content},
            rationale=f"{eligibility_reason} {result.rationale}".strip(),
            confidence=result.confidence,
            provider=result.provider,
            model=result.model,
            is_ai=result.is_ai,
            error=result.error,
        )

    result = execute(
        db, agent_name=AGENT_NAME, complaint_id=complaint.id, state=state, fn=_work
    )

    if result.output.get("eligible") and result.output.get("content"):
        post = SocialPost(
            complaint_id=complaint.id,
            kind="breach" if complaint.sla_breached else "amplification",
            content=result.output["content"],
            channel="internal",
            eligibility_reason=eligibility_reason,
            is_published=False,
        )
        db.add(post)
        db.flush()
        append_event(
            db,
            complaint.id,
            EventType.SOCIAL_POSTED,
            "Public accountability content generated (held unpublished).",
            {"social_post_id": post.id, "eligibility": eligibility_reason},
            actor=AGENT_NAME,
        )
        result.output["social_post_id"] = post.id

    db.flush()
    return result
