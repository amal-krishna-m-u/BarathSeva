"""Image guard — does the photograph show what the citizen's words claim?

This runs before the verifier and asks exactly one question, because it is the
question that decides whether a municipal crew is dispatched on evidence nobody
has actually looked at. The text may describe a perfectly real pothole while
the photograph shows two people on a street; the words alone cannot catch that,
and a prompt juggling five verdicts at once is easier to talk out of it than a
prompt asked one thing.

The gate FAILS CLOSED, and that is its most important property. If a real
vision provider is configured but the call fails -- a 503, a timeout, a
malformed reply -- the pipeline previously fell back to the keyword stub, which
cannot see images and happily approved whatever the text described. A fake
photograph therefore sailed through at exactly the moment the AI was
unavailable. Here, an expected-but-missing check sends the complaint to a human
instead of waving it through.

Running deliberately keyless (``ai_provider="stub"``) is a different situation
and is NOT treated as a failure: the system never claimed to inspect images, so
holding every complaint would be a false signal rather than a safe one. The
distinction is drawn on whether an inference actually fell back, not on whether
the result happened to come from the stub.
"""

from __future__ import annotations

from typing import Any, Optional

from sqlalchemy.orm import Session

from app.agents.base import AgentResult, execute
from app.ai.coerce import as_bool, as_enum, as_float
from app.ai.factory import infer
from app.ai.prompts import build_image_match
from app.config import settings
from app.core import media
from app.models import Complaint, ComplaintEvidence

AGENT_NAME = "image_guard"

#: The controlled vocabulary the model must answer within. Anything outside it
#: is a hallucination and degrades to UNREADABLE rather than reaching a verdict.
IMAGE_KINDS = {
    "CAMERA_PHOTO_PLAUSIBLE",
    "PERSON_OR_GROUP",
    "INDOOR_SCENE",
    "SCREENSHOT_OR_REPOST",
    "ILLUSTRATION_OR_RENDER",
    "UNRELATED_SCENE",
    "UNREADABLE",
}

#: The only kind that may continue automatically. Everything else is a human's
#: decision -- including UNREADABLE, because "I could not tell" is not consent.
PASS_KINDS = {"CAMERA_PHOTO_PLAUSIBLE"}

#: Below this the model's own verdict is not trusted either way, so the
#: complaint goes to a human rather than being auto-approved OR auto-rejected.
MIN_CONFIDENCE = 0.40

ROUTE_CONTINUE = "continue"
ROUTE_HOLD = "hold"


def _load_image(
    evidence: Optional[ComplaintEvidence],
) -> tuple[Optional[bytes], Optional[str]]:
    if evidence is None or not evidence.media_path:
        return None, None
    path = media.resolve_path(evidence.media_path)
    if not path.exists():
        return None, evidence.media_mime
    return path.read_bytes(), evidence.media_mime


def _fell_back(result: Any) -> bool:
    """Did a configured real provider fail and silently become the stub?

    app.ai.factory stamps ``fell_back_from_<provider>`` onto the error when it
    substitutes the stub. That marker is the difference between "we chose not
    to look" and "we meant to look and could not".
    """
    return bool(result.error and str(result.error).startswith("fell_back_from_"))


def run(db: Session, complaint: Complaint, state: dict[str, Any]) -> AgentResult:
    evidence = complaint.evidence[0] if complaint.evidence else None
    image_bytes, image_mime = _load_image(evidence)

    def _work() -> AgentResult:
        # Nothing to compare. A text-only report is an existing, accepted path
        # that already carries less evidence weight; this gate has no opinion.
        if not image_bytes:
            return AgentResult.deterministic(
                output={"decision": ROUTE_CONTINUE, "checked": False, "reason_code": "NO_PHOTO"},
                rationale="No photograph attached; nothing to compare against the text.",
            )

        result = infer(
            build_image_match(
                description=complaint.description,
                image_bytes=image_bytes,
                image_mime=image_mime,
            )
        )

        # Expected a real look at the image and did not get one -> hold.
        if _fell_back(result):
            return AgentResult(
                output={
                    "decision": ROUTE_HOLD,
                    "checked": False,
                    "reason_code": "CHECK_UNAVAILABLE",
                    "provider_error": str(result.error)[:300],
                },
                rationale=(
                    "The image could not be checked: the configured vision provider "
                    "was unavailable. Held for human review rather than dispatched "
                    "on an unverified photograph."
                ),
                confidence=0.0,
                provider=result.provider,
                model=result.model,
                is_ai=False,
                error=result.error,
            )

        # Deliberately keyless. The system never claimed to inspect images, so
        # this is not a failure and must not manufacture a review queue.
        if not result.is_ai:
            return AgentResult.deterministic(
                output={
                    "decision": ROUTE_CONTINUE,
                    "checked": False,
                    "reason_code": "NO_VISION_PROVIDER",
                },
                rationale=(
                    "No vision provider configured; the photograph was not inspected. "
                    "Image-text agreement is unverified for this complaint."
                ),
            )

        matches = as_bool(result.data.get("image_matches_text"), False)
        kind = as_enum(result.data.get("image_kind"), IMAGE_KINDS, "UNREADABLE")
        confidence = as_float(result.confidence, 0.0, lo=0.0, hi=1.0)
        concern = str(result.data.get("concern") or "").strip()

        if confidence < MIN_CONFIDENCE:
            decision, reason_code = ROUTE_HOLD, "LOW_CONFIDENCE"
            rationale = (
                f"The image check was not confident enough to decide "
                f"({confidence:.2f} below the {MIN_CONFIDENCE:.2f} floor)."
            )
        elif not matches or kind not in PASS_KINDS:
            decision, reason_code = ROUTE_HOLD, kind
            rationale = concern or (
                "The attached photograph does not appear to show the problem "
                "described in the report."
            )
        else:
            decision, reason_code = ROUTE_CONTINUE, kind
            rationale = concern or "The photograph is consistent with the reported problem."

        return AgentResult(
            output={
                "decision": decision,
                "checked": True,
                "image_matches_text": matches,
                "image_kind": kind,
                "reason_code": reason_code,
                "concern": concern,
            },
            rationale=rationale,
            confidence=confidence,
            provider=result.provider,
            model=result.model,
            is_ai=result.is_ai,
            error=result.error,
        )

    return execute(
        db, agent_name=AGENT_NAME, complaint_id=complaint.id, state=state, fn=_work
    )
