"""Prompt construction for model-backed providers.

Each builder returns an ``InferenceRequest`` carrying both a natural-language
prompt (for model providers) and a structured ``context`` dict (which the
deterministic provider reads instead). One call site, two execution paths.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from app.ai.base import InferenceRequest, Task
from app.core.enums import ComplaintCategory, Priority

CATEGORIES = [c.value for c in ComplaintCategory]
PRIORITIES = [p.value for p in Priority]

_JSON_RULE = (
    "Respond with a single JSON object and nothing else. Do not wrap it in "
    "markdown. Include every field in the schema."
)

IMAGE_MATCH_SYSTEM = (
    "You are the image gate of a municipal civic-complaint pipeline in India. "
    "Your only job is to compare the citizen's words with the photograph they "
    "attached and say whether the photograph actually shows what the words "
    "describe.\n\n"
    "You are NOT judging whether the complaint sounds reasonable. The text may "
    "describe a perfectly real problem while the photograph shows something "
    "else entirely — that is the exact case you exist to catch.\n\n"
    "Set image_matches_text to false whenever the photograph does not show the "
    "reported problem: a photograph of a person or group, an indoor scene, a "
    "screenshot, a meme, a stock or illustrated image, a blank or unreadable "
    "frame, or simply a different subject from the one described.\n\n"
    "You cannot establish where or when a photo was taken, and you must not "
    "guess at it. Judge only whether the visible subject matches the words.\n\n"
    + _JSON_RULE
)

VERIFY_SYSTEM = (
    "You are the verification gate of a municipal civic-complaint pipeline in "
    "India. You decide whether a citizen's report describes a genuine civic "
    "infrastructure problem and whether the evidence supports acting on it.\n\n"
    "Judge only what the text and image actually show. Do not infer a problem "
    "that is not evidenced. A selfie, a meme, an indoor scene, a blank frame, "
    "a screenshot or a greeting is not a civic issue.\n\n"
    "You are NOT deciding whether the photo is authentic — deterministic "
    "forensics already ran and their findings are given to you as signals. "
    "Weigh those signals, but your own job is image-text consistency.\n\n"
    "Bias: falsely rejecting a real problem is worse than passing a doubtful "
    "one to a human reviewer. When genuinely unsure, lower your confidence "
    "rather than rejecting outright.\n\n" + _JSON_RULE
)

CLASSIFY_SYSTEM = (
    "You classify civic complaints for an Indian municipal corporation.\n\n"
    f"Pick exactly one category from: {', '.join(CATEGORIES)}.\n"
    f"Pick exactly one priority from: {', '.join(PRIORITIES)} "
    "(P1 = immediate danger to life or essential services, P2 = serious, "
    "P3 = routine, P4 = minor).\n\n"
    "Raise priority for danger to life, injury, schools or hospitals nearby, "
    "live electrical hazards, large-scale flooding, or many corroborating "
    "reports. Explain the priority in one sentence.\n\n" + _JSON_RULE
)

CLUSTER_SYSTEM = (
    "You summarise the geographic context of a civic complaint for municipal "
    "officers. Be factual and brief (max 2 sentences). State the number of "
    "nearby reports, the ward, and whether this looks like a recurring site "
    "that needs an inspection rather than a one-off repair.\n\n" + _JSON_RULE
)

SOCIAL_SYSTEM = (
    "You write short public accountability posts for a civic platform in "
    "India. Be factual and neutral: state the issue, locality, reference "
    "number, responsible department and deadline. Never insult officials, "
    "never allege corruption, never speculate about causes, never name private "
    "individuals. Max 300 characters plus hashtags.\n\n" + _JSON_RULE
)

RESOLUTION_SYSTEM = (
    "You write the closing message a citizen receives when their civic "
    "complaint is resolved. Be warm, concrete and brief (max 3 sentences). "
    "State what was reported, which department handled it, and how to reopen "
    "the complaint if the problem persists. Do not invent work that was not "
    "described in the resolution note.\n\n" + _JSON_RULE
)


def _ctx_block(context: dict[str, Any]) -> str:
    return json.dumps(context, indent=2, default=str, ensure_ascii=False)


def build_verify(
    *,
    description: str,
    has_photo: bool,
    image_readable: bool,
    authenticity_score: float,
    authenticity_outcome: str,
    signals: list[dict[str, Any]],
    image_bytes: Optional[bytes] = None,
    image_mime: Optional[str] = None,
) -> InferenceRequest:
    signal_codes = [s.get("code") for s in signals]
    context = {
        "description": description,
        "has_photo": has_photo,
        "image_readable": image_readable,
        "authenticity_score": authenticity_score,
        "authenticity_outcome": authenticity_outcome,
        "signal_codes": signal_codes,
        "signals": [
            {"code": s.get("code"), "severity": s.get("severity"), "detail": s.get("detail")}
            for s in signals
        ],
    }
    user = (
        f'Citizen report: "{description}"\n\n'
        f"Photograph attached: {'yes' if has_photo else 'no'}"
        f"{' (unreadable)' if has_photo and not image_readable else ''}\n\n"
        f"Deterministic evidence findings:\n{_ctx_block(context['signals'])}\n\n"
        f"Evidence authenticity score: {authenticity_score:.2f} "
        f"({authenticity_outcome})\n\n"
        "Decide: is this a genuine civic infrastructure issue, and is the "
        "evidence sufficient to dispatch a municipal crew?"
    )
    return InferenceRequest(
        task=Task.VERIFY,
        system=VERIFY_SYSTEM,
        user=user,
        schema={
            "is_civic_issue": "boolean",
            "evidence_sufficient": "boolean",
            "category_hint": f"one of {CATEGORIES}",
            "rationale": "string, one or two sentences explaining the decision",
            "confidence": "number between 0 and 1",
        },
        context=context,
        image_bytes=image_bytes,
        image_mime=image_mime,
    )


def build_classify(
    *,
    description: str,
    nearby_count: int,
    ward_name: Optional[str],
    category_hint: Optional[str],
    image_bytes: Optional[bytes] = None,
    image_mime: Optional[str] = None,
) -> InferenceRequest:
    context = {
        "description": description,
        "nearby_count": nearby_count,
        "ward_name": ward_name,
        "category_hint": category_hint,
    }
    user = (
        f'Citizen report: "{description}"\n\n'
        f"Ward: {ward_name or 'unknown'}\n"
        f"Corroborating reports nearby: {nearby_count}\n"
        f"Keyword pre-match: {category_hint or 'none'}\n\n"
        "Classify the category and priority."
    )
    return InferenceRequest(
        task=Task.CLASSIFY,
        system=CLASSIFY_SYSTEM,
        user=user,
        schema={
            "category": f"one of {CATEGORIES}",
            "priority": f"one of {PRIORITIES}",
            "severity_note": "string, one sentence justifying the priority",
            "rationale": "string",
            "confidence": "number between 0 and 1",
        },
        context=context,
        image_bytes=image_bytes,
        image_mime=image_mime,
    )


def build_cluster_summary(
    *,
    nearby_count: int,
    ward_name: Optional[str],
    category: Optional[str],
    radius_meters: float,
    is_hotspot: bool,
    window_hours: int,
) -> InferenceRequest:
    context = {
        "nearby_count": nearby_count,
        "ward_name": ward_name,
        "category": category,
        "radius_meters": radius_meters,
        "is_hotspot": is_hotspot,
        "window_hours": window_hours,
    }
    user = (
        f"Complaint context:\n{_ctx_block(context)}\n\n"
        "Summarise this geographic context for a municipal officer."
    )
    return InferenceRequest(
        task=Task.CLUSTER_SUMMARY,
        system=CLUSTER_SYSTEM,
        user=user,
        schema={"summary": "string, max 2 sentences", "confidence": "number"},
        context=context,
    )


def build_social(**context: Any) -> InferenceRequest:
    user = (
        f"Complaint facts:\n{_ctx_block(context)}\n\n"
        "Write the public accountability post."
    )
    return InferenceRequest(
        task=Task.SOCIAL,
        system=SOCIAL_SYSTEM,
        user=user,
        schema={"content": "string, max 300 chars plus hashtags", "confidence": "number"},
        context=context,
    )


def build_resolution(**context: Any) -> InferenceRequest:
    user = (
        f"Resolved complaint facts:\n{_ctx_block(context)}\n\n"
        "Write the citizen's resolution message."
    )
    return InferenceRequest(
        task=Task.RESOLUTION,
        system=RESOLUTION_SYSTEM,
        user=user,
        schema={"message": "string, max 3 sentences", "confidence": "number"},
        context=context,
    )


def build_image_match(
    *,
    description: str,
    image_bytes: Optional[bytes] = None,
    image_mime: Optional[str] = None,
) -> InferenceRequest:
    """Ask whether the attached photograph depicts what the text claims.

    Deliberately a SEPARATE, single-question request rather than more fields on
    the verify prompt: this is the one judgement that decides whether a crew is
    sent on evidence nobody has looked at, and a prompt asked to do one thing
    is harder to talk out of it than a prompt juggling five verdicts.
    """
    context = {"description": description, "has_photo": bool(image_bytes)}
    user = (
        f'Citizen report: "{description}"\n\n'
        "Compare that text against the attached photograph. Does the "
        "photograph show the problem the text describes?"
    )
    return InferenceRequest(
        task=Task.IMAGE_MATCH,
        system=IMAGE_MATCH_SYSTEM,
        user=user,
        schema={
            "image_matches_text": "boolean, true only if the photo shows the reported problem",
            "image_kind": (
                "one of CAMERA_PHOTO_PLAUSIBLE, PERSON_OR_GROUP, INDOOR_SCENE, "
                "SCREENSHOT_OR_REPOST, ILLUSTRATION_OR_RENDER, UNRELATED_SCENE, UNREADABLE"
            ),
            "concern": "string, one short sentence a citizen could be shown, or empty if it matches",
            "confidence": "number between 0 and 1",
        },
        context=context,
        image_bytes=image_bytes,
        image_mime=image_mime,
    )
