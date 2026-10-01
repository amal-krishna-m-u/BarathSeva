"""Deterministic reference provider.

This is not a mock that returns canned strings — it is a rule-based baseline
that implements every inference task well enough to run the whole platform with
no API keys and no network. It exists for three reasons:

  1. The prototype must be demonstrable offline and reproducibly.
  2. It is the fallback when a configured model provider fails, so an outage
     degrades classification quality instead of halting intake.
  3. It documents, in executable form, exactly what each AI task is expected
     to return.

Because it is deterministic, ``is_ai`` is False and ``agent_runs`` records it
honestly as a non-AI decision.
"""

from __future__ import annotations

import time
from typing import Any

from app.ai.base import InferenceRequest, InferenceResult, Task
from app.ai.taxonomy import (
    CATEGORY_LABELS,
    ESCALATING_KEYWORDS,
    NON_CIVIC_KEYWORDS,
    SEVERITY_KEYWORDS,
    count_hits,
    match_category,
)
from app.config import settings
from app.core.enums import ComplaintCategory, Priority

# Priority ladder, least to most urgent.
PRIORITY_LADDER = [Priority.P4.value, Priority.P3.value, Priority.P2.value, Priority.P1.value]

BASE_PRIORITY = {
    ComplaintCategory.PIPELINE_BURST.value: Priority.P2.value,
    ComplaintCategory.POWER_OUTAGE.value: Priority.P2.value,
    ComplaintCategory.SEWAGE.value: Priority.P2.value,
    ComplaintCategory.WATER_LEAK.value: Priority.P3.value,
    ComplaintCategory.DRAINAGE.value: Priority.P3.value,
    ComplaintCategory.POTHOLE.value: Priority.P3.value,
    ComplaintCategory.ROAD_DAMAGE.value: Priority.P3.value,
    ComplaintCategory.GARBAGE.value: Priority.P3.value,
    ComplaintCategory.STREETLIGHT.value: Priority.P3.value,
    ComplaintCategory.OTHER.value: Priority.P4.value,
}


def _bump(priority: str, steps: int) -> str:
    if steps <= 0:
        return priority
    idx = PRIORITY_LADDER.index(priority)
    return PRIORITY_LADDER[min(idx + steps, len(PRIORITY_LADDER) - 1)]


class StubProvider:
    name = "stub"
    model = "deterministic-rules-v1"
    is_ai = False

    def infer(self, request: InferenceRequest) -> InferenceResult:
        started = time.perf_counter()
        handler = {
            Task.IMAGE_MATCH: self._image_match,
            Task.VERIFY: self._verify,
            Task.CLASSIFY: self._classify,
            Task.CLUSTER_SUMMARY: self._cluster_summary,
            Task.SOCIAL: self._social,
            Task.RESOLUTION: self._resolution,
        }.get(request.task)

        if handler is None:
            return InferenceResult(
                data={},
                rationale="",
                confidence=0.0,
                provider=self.name,
                model=self.model,
                latency_ms=0,
                is_ai=False,
                error=f"unsupported_task:{request.task}",
            )

        data, rationale, confidence = handler(request)
        return InferenceResult(
            data=data,
            rationale=rationale,
            confidence=confidence,
            provider=self.name,
            model=self.model,
            latency_ms=int((time.perf_counter() - started) * 1000),
            is_ai=False,
        )

    # ------------------------------------------------------------------ verify
    def _verify(self, req: InferenceRequest) -> tuple[dict[str, Any], str, float]:
        ctx = req.context
        text = str(ctx.get("description", ""))
        has_photo = bool(ctx.get("has_photo"))
        image_readable = ctx.get("image_readable", True)
        score = float(ctx.get("authenticity_score", 0.0))
        signal_codes: list[str] = list(ctx.get("signal_codes", []))

        category, hits = match_category(text)
        non_civic = count_hits(text, NON_CIVIC_KEYWORDS)
        words = len(text.split())

        is_civic = bool(hits) or (category != ComplaintCategory.OTHER.value)
        # A photo plus a substantive description can still be civic even when no
        # keyword matched — the description may simply use unusual wording.
        if not is_civic and has_photo and words >= 8 and not non_civic:
            is_civic = True

        if non_civic and not hits:
            is_civic = False

        evidence_sufficient = bool(
            (has_photo and image_readable) or (is_civic and words >= 10)
        )

        reasons: list[str] = []
        if hits:
            reasons.append(
                f"description matches {CATEGORY_LABELS.get(category, category)} "
                f"language ({', '.join(hits[:3])})"
            )
        elif is_civic:
            reasons.append("description is substantive and accompanied by a photo")
        else:
            reasons.append("no civic infrastructure issue identifiable in the report")

        if has_photo and image_readable:
            reasons.append("a readable photograph was supplied")
        elif has_photo:
            reasons.append("the supplied photograph could not be read")
        else:
            reasons.append("no photograph was supplied")

        if "exif_time_mismatch" in signal_codes:
            reasons.append("photo capture time is inconsistent with the report")
        if "exif_gps_mismatch" in signal_codes:
            reasons.append("photo GPS disagrees with the reported location")

        confidence = 0.35
        if hits:
            confidence += 0.30
        if has_photo and image_readable:
            confidence += 0.20
        if words >= 10:
            confidence += 0.05
        confidence += 0.10 * score
        if non_civic and not hits:
            confidence = max(confidence, 0.70)  # confident it is NOT a civic issue
        confidence = round(min(confidence, 0.95), 2)

        verdict = "valid civic issue" if (is_civic and evidence_sufficient) else "insufficient"
        rationale = f"Assessed as {verdict}: " + "; ".join(reasons) + "."

        return (
            {
                "is_civic_issue": is_civic,
                "evidence_sufficient": evidence_sufficient,
                "category_hint": category,
                "matched_phrases": hits,
            },
            rationale,
            confidence,
        )

    # ------------------------------------------------------------- image match
    def _image_match(self, req: InferenceRequest) -> tuple[dict[str, Any], str, float]:
        """Report, honestly, that no image inspection happened.

        The deterministic stub has no vision. It must not answer "the photo
        matches" — that would be the system asserting something nobody looked
        at, which is the precise failure the image gate exists to prevent. It
        returns ``unavailable`` and a null verdict, and ImageGuard reads
        ``is_ai`` before it reads any of this.
        """
        return (
            {
                "image_matches_text": None,
                "image_kind": "UNREADABLE",
                "concern": "Deterministic stub cannot inspect image content.",
                "unavailable": True,
            },
            "No image inspection performed: the deterministic provider cannot see images.",
            0.0,
        )

    # ---------------------------------------------------------------- classify
    def _classify(self, req: InferenceRequest) -> tuple[dict[str, Any], str, float]:
        ctx = req.context
        text = str(ctx.get("description", ""))
        nearby = int(ctx.get("nearby_count", 0) or 0)

        category, hits = match_category(text)
        escalators = count_hits(text, ESCALATING_KEYWORDS)
        severity_words = count_hits(text, SEVERITY_KEYWORDS)

        priority = BASE_PRIORITY.get(category, Priority.P3.value)
        steps = 0
        notes: list[str] = []

        if escalators:
            steps += 2 if len(escalators) >= 2 else 1
            notes.append(f"safety language present ({', '.join(escalators[:3])})")
        if severity_words:
            steps += 1
            notes.append(f"scale language present ({', '.join(severity_words[:2])})")
        if nearby >= settings.hotspot_min_complaints:
            steps += 1
            notes.append(f"{nearby} corroborating reports nearby")

        final_priority = _bump(priority, steps)

        if not notes:
            notes.append("no escalating or scale indicators found")
        severity_note = (
            f"Base priority for {CATEGORY_LABELS.get(category, category)} is "
            f"{priority}; adjusted to {final_priority} because "
            + "; ".join(notes)
            + "."
        )

        confidence = round(min(0.45 + 0.35 * bool(hits) + 0.05 * len(hits), 0.95), 2)
        rationale = (
            f"Matched category {category} on {hits or 'no explicit keyword'}; "
            f"priority {final_priority}."
        )
        return (
            {
                "category": category,
                "priority": final_priority,
                "severity_note": severity_note,
            },
            rationale,
            confidence,
        )

    # --------------------------------------------------------- cluster summary
    def _cluster_summary(self, req: InferenceRequest) -> tuple[dict[str, Any], str, float]:
        ctx = req.context
        nearby = int(ctx.get("nearby_count", 0) or 0)
        ward = ctx.get("ward_name") or "an unmapped area"
        category = ctx.get("category") or ComplaintCategory.OTHER.value
        radius = int(float(ctx.get("radius_meters", settings.cluster_radius_meters)))
        is_hotspot = bool(ctx.get("is_hotspot"))
        label = CATEGORY_LABELS.get(category, "civic issue")

        if nearby == 0:
            summary = (
                f"First reported {label} at this location in {ward} within the last "
                f"{settings.cluster_window_hours} hours."
            )
        elif is_hotspot:
            summary = (
                f"Hotspot: {nearby} other complaints within {radius} m in {ward} "
                f"over the last {settings.cluster_window_hours} hours. This location "
                f"is a recurring {label} site and warrants a scheduled inspection "
                f"rather than a single repair."
            )
        else:
            summary = (
                f"{nearby} other complaint(s) within {radius} m in {ward} in the last "
                f"{settings.cluster_window_hours} hours — this report is corroborated "
                f"but below the hotspot threshold of "
                f"{settings.hotspot_min_complaints}."
            )
        return ({"summary": summary}, f"Summarised {nearby} nearby complaints.", 0.9)

    # ------------------------------------------------------------------ social
    def _social(self, req: InferenceRequest) -> tuple[dict[str, Any], str, float]:
        ctx = req.context
        label = CATEGORY_LABELS.get(str(ctx.get("category")), "civic issue")
        ward = ctx.get("ward_name") or "Bengaluru"
        ref = ctx.get("reference", "")
        dept = ctx.get("department") or "the responsible department"
        nearby = int(ctx.get("nearby_count", 0) or 0)
        priority = ctx.get("priority", "")
        breached = bool(ctx.get("sla_breached"))
        due = ctx.get("sla_due_at_human") or "the committed deadline"

        if breached:
            content = (
                f"SLA BREACHED — {label.title()} in {ward} (ref {ref}) is still open "
                f"past its {due} deadline with {dept}. "
                f"{nearby} nearby report(s) describe the same problem. "
                f"#BarathSeva #{ward.replace(' ', '')} #CivicAccountability"
            )
        else:
            content = (
                f"{label.title()} reported in {ward} (ref {ref}), priority {priority}, "
                f"now assigned to {dept} with a resolution deadline of {due}. "
                f"{nearby} nearby report(s) corroborate it. "
                f"#BarathSeva #{ward.replace(' ', '')}"
            )
        return ({"content": content}, "Generated accountability post.", 0.85)

    # -------------------------------------------------------------- resolution
    def _resolution(self, req: InferenceRequest) -> tuple[dict[str, Any], str, float]:
        ctx = req.context
        label = CATEGORY_LABELS.get(str(ctx.get("category")), "civic issue")
        ward = ctx.get("ward_name") or "your area"
        ref = ctx.get("reference", "")
        dept = ctx.get("department") or "the responsible department"
        note = ctx.get("resolution_note") or "The issue was attended to by the field team."
        hours = ctx.get("hours_taken")
        ticket = ctx.get("external_ticket_id") or "-"

        timing = f" in {hours} hour(s)" if hours is not None else ""
        message = (
            f"Your report {ref} ({label} in {ward}) has been marked resolved{timing}. "
            f"{dept} handled it under ticket {ticket}. {note} "
            f"If the problem persists, reply to reopen this complaint and it will be "
            f"escalated with the original evidence attached."
        )
        return ({"message": message}, "Generated citizen resolution message.", 0.9)
