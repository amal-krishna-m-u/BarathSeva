"""aiKart "Try Me Now" entrypoint for the BarathSeva civic triage agent.

Implements the aiKart v1 runtime contract: read the buyer's answers from
/aikart/input.json (or $AIKART_INPUT), write a single result object to
/aikart/output.json, exit 0.

WHY THIS IS A TRIAGE AGENT AND NOT "BarathSeva IN A BOX"
--------------------------------------------------------
BarathSeva is a platform: FastAPI, PostGIS, Redis, Celery, a Next.js front
end, and a durable audit trail. The sandbox is a single ephemeral container
with no network by default, no persistence, and a wall-clock cap -- a web
platform cannot be demonstrated there, and pretending otherwise would ship a
listing that fails the moment a buyer clicks Run.

What IS demonstrable, and is the part with the actual judgement in it, is the
triage decision: given what a citizen wrote and the photo they attached, is
this a real civic issue, does the photo actually support it, what kind of
problem is it, who owns it, and by when must it be fixed. That runs with no
database because every reference table it needs (wards, departments, SLA
hours, category vocabulary) is plain Python data in app/core/city.py.

The authenticity layers that DO need the database -- perceptual-hash reuse
detection across prior complaints, reporter velocity, corroboration -- are
deliberately absent and are reported as such. An agent that silently drops
half its controls and still prints a confident verdict is worse than one that
says what it did not check.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any, Optional

# aiKart mounts these exact paths. AIKART_DIR exists only so the same code can
# be exercised locally without root, and defaults to the spec location.
_AIKART_DIR = Path(os.environ.get("AIKART_DIR", "/aikart"))
INPUT_PATH = _AIKART_DIR / "input.json"
OUTPUT_PATH = _AIKART_DIR / "output.json"


# --------------------------------------------------------------------------
# aiKart I/O contract
# --------------------------------------------------------------------------
def read_input() -> dict[str, Any]:
    """Prefer the file, fall back to the env var; the spec provides both."""
    if INPUT_PATH.exists():
        return json.loads(INPUT_PATH.read_text() or "{}")
    raw = os.environ.get("AIKART_INPUT")
    return json.loads(raw) if raw else {}


def write_output(markdown: str) -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps({"format": "markdown", "response": markdown}, ensure_ascii=False)
    )


# --------------------------------------------------------------------------
# Provider configuration -- must happen BEFORE app.config is imported, since
# pydantic-settings reads the environment once at import time.
# --------------------------------------------------------------------------
def configure_provider(payload: dict[str, Any]) -> str:
    """Return the provider mode chosen from the buyer's input.

    No API key is baked into this image. The manifest has no secret-injection
    mechanism, and this image is public, so a key committed here would be a
    published credential. A buyer who wants real vision supplies their own.
    """
    api_key = str(payload.get("gemini_api_key") or "").strip()
    os.environ.setdefault("BARATHSEVA_SECRET_KEY", "aikart-sandbox-ephemeral")
    if api_key:
        os.environ["BARATHSEVA_AI_PROVIDER"] = "gemini"
        os.environ["BARATHSEVA_GEMINI_API_KEY"] = api_key
        model = str(payload.get("gemini_model") or "").strip()
        if model:
            os.environ["BARATHSEVA_GEMINI_MODEL"] = model
        os.environ.setdefault("BARATHSEVA_AI_REQUEST_TIMEOUT_SECONDS", "60")
        return "gemini"
    os.environ["BARATHSEVA_AI_PROVIDER"] = "stub"
    return "stub"


# --------------------------------------------------------------------------
# Image sourcing. aiKart v1 input types are text|textarea|number|boolean|
# select -- there is NO file upload -- so a photo arrives either as one of the
# bundled synthetic samples or as a base64 string the buyer pastes in.
# --------------------------------------------------------------------------
def resolve_image(payload: dict[str, Any]) -> tuple[Optional[bytes], Optional[str], str]:
    """Return (bytes, mime, provenance_note)."""
    pasted = str(payload.get("photo_base64") or "").strip()
    if pasted:
        if "," in pasted and pasted.lower().startswith("data:"):
            pasted = pasted.split(",", 1)[1]
        try:
            data = base64.b64decode(pasted, validate=True)
        except (binascii.Error, ValueError):
            return None, None, "A photo was pasted but could not be base64-decoded."
        if data:
            return data, "image/jpeg", "Buyer-supplied image (pasted as base64)."

    choice = str(payload.get("sample_photo") or "None").strip()
    if choice in ("", "None"):
        return None, None, "No photograph attached."

    from samples import SAMPLE_BUILDERS, SAMPLE_NOTES

    builder = SAMPLE_BUILDERS.get(choice)
    if builder is None:
        return None, None, f"Unknown sample photo {choice!r}; proceeding without an image."
    return builder(), "image/jpeg", SAMPLE_NOTES[choice]


# --------------------------------------------------------------------------
# Geography and routing -- all from app/core/city.py, no database.
# --------------------------------------------------------------------------
def resolve_ward(locality: str):
    from app.core.city import WARD_SEEDS

    for ward in WARD_SEEDS:
        if ward.name.lower() == locality.strip().lower():
            return ward
    return None


def resolve_department(category: str) -> Optional[dict[str, Any]]:
    from app.core.city import DEPARTMENT_SEEDS

    for dept in DEPARTMENT_SEEDS:
        if category in (dept.get("categories") or []):
            return dept
    return None


def sla_hours(category: str, priority: str) -> Optional[int]:
    from app.core.city import SLA_RESOLUTION_HOURS

    return (SLA_RESOLUTION_HOURS.get(category) or {}).get(priority)


# --------------------------------------------------------------------------
# The triage run
# --------------------------------------------------------------------------
def triage(payload: dict[str, Any], mode: str) -> str:
    from app.ai.image_policy import IMAGE_KINDS, MIN_CONFIDENCE, PASS_KINDS
    from app.ai.coerce import as_bool, as_enum, as_float
    from app.ai.factory import infer
    from app.ai.prompts import build_classify, build_image_match, build_verify
    from app.core.exif import extract_image_facts

    description = str(payload.get("description") or "").strip()
    locality = str(payload.get("locality") or "Koramangala")

    image_bytes, image_mime, image_note = resolve_image(payload)
    lines: list[str] = []
    out = lines.append

    out("# Civic Complaint Triage")
    out("")
    out(f"> {description}")
    out("")
    out(f"**Locality reported:** {locality}  ")
    out(f"**Photograph:** {image_note}  ")
    out(
        f"**Inference mode:** {'Gemini (buyer-supplied key)' if mode == 'gemini' else 'Deterministic rules — no API key supplied'}"
    )
    out("")

    # --- Layer 2: file forensics, deterministic, no database -------------
    out("## 1. Evidence forensics")
    out("")
    if image_bytes:
        facts = extract_image_facts(image_bytes)
        out(f"- Readable image: **{facts.readable}**")
        out(f"- Bytes: `{len(image_bytes):,}`")
        out(
            f"- Dimensions: `{facts.width}x{facts.height}` ({facts.image_format or 'unknown format'})"
            if facts.width
            else "- Dimensions: unavailable"
        )
        out(f"- EXIF block present: **{facts.exif_present}**")
        out(f"- EXIF capture time: `{facts.exif_datetime or 'absent'}`")
        out(
            f"- EXIF GPS: `{facts.exif_latitude}, {facts.exif_longitude}`"
            if facts.has_gps
            else "- EXIF GPS: `absent`"
        )
        out(f"- Camera make/model: `{facts.make or 'absent'} / {facts.model or 'absent'}`")
        out(f"- Editing software tag: `{facts.software or 'none'}`")
        if facts.looks_like_screenshot:
            out("- ⚠️ Dimensions match a common screen size — consistent with a screenshot.")
        if not facts.exif_present:
            out(
                "- ⚠️ No EXIF block — consistent with a screenshot, a re-encode, a "
                "messaging-app forward or a web download. Individually weak; recorded, not decisive."
            )
    else:
        out("- No image supplied, so no file forensics were possible.")
    out("")
    out(
        "> Not checked here: perceptual-hash reuse against prior complaints, "
        "reporter velocity and corroboration. Those are database-backed layers "
        "of the full platform and cannot run in an ephemeral sandbox."
    )
    out("")

    # --- The image guard -------------------------------------------------
    out("## 2. Image guard — does the photo show what the text claims?")
    out("")
    held = False
    hold_reason = ""
    if not image_bytes:
        out("- No photograph to compare. Nothing to decide.")
    else:
        result = infer(
            build_image_match(
                description=description, image_bytes=image_bytes, image_mime=image_mime
            )
        )
        fell_back = bool(result.error and str(result.error).startswith("fell_back_from_"))
        if fell_back:
            held, hold_reason = True, "the vision provider was unavailable"
            out(f"- ❌ **Check unavailable** — `{str(result.error)[:160]}`")
            out("- Held for human review. The gate fails closed: an unseen photo is never auto-approved.")
        elif not result.is_ai:
            out("- ⚠️ **Not inspected** — no vision provider configured (no API key supplied).")
            out(
                "- Not treated as a failure: the agent never claimed to look. Supply a "
                "Gemini API key to enable the real check."
            )
        else:
            matches = as_bool(result.data.get("image_matches_text"), False)
            kind = as_enum(result.data.get("image_kind"), IMAGE_KINDS, "UNREADABLE")
            confidence = as_float(result.confidence, 0.0, lo=0.0, hi=1.0)
            concern = str(result.data.get("concern") or "").strip()
            out(f"- Image kind: **{kind}**")
            out(f"- Matches the text: **{matches}**")
            out(f"- Confidence: **{confidence:.2f}**")
            if concern:
                out(f"- Concern: _{concern}_")
            if confidence < MIN_CONFIDENCE:
                held, hold_reason = True, "the check was not confident enough to decide"
            elif not matches or kind not in PASS_KINDS:
                held, hold_reason = True, concern or "the photo does not show the reported problem"
            if held:
                out("- 🛑 **Held for human review** — not dispatched, and not auto-rejected.")
            else:
                out("- ✅ Photo is consistent with the report. Proceeding.")
    out("")

    if held:
        out("## Outcome — HELD FOR REVIEW")
        out("")
        out(f"**Why:** {hold_reason.rstrip('.')}.")
        out("")
        out(
            "In the full platform it moves to `PENDING_REVIEW` with "
            "`needs_human_review = true` and appears in the super admin's queue. "
            "An officer approving it re-enters the pipeline from verification "
            "onward, so classification, routing and the SLA clock all still "
            "happen — a human overrides the verdict without taking over the "
            "workflow. It is never auto-rejected on an image judgement alone."
        )
        return "\n".join(lines)

    # --- Verification ----------------------------------------------------
    out("## 3. Verification")
    out("")
    verify = infer(
        build_verify(
            description=description,
            has_photo=bool(image_bytes),
            image_readable=bool(image_bytes),
            authenticity_score=0.75,
            authenticity_outcome="ACCEPT",
            signals=[],
            image_bytes=image_bytes,
            image_mime=image_mime,
        )
    )
    is_civic = as_bool(verify.data.get("is_civic_issue"), False)
    sufficient = as_bool(verify.data.get("evidence_sufficient"), False)
    out(f"- Genuine civic issue: **{is_civic}**")
    out(f"- Evidence sufficient to dispatch: **{sufficient}**")
    out(f"- Confidence: **{as_float(verify.confidence, 0.0, lo=0.0, hi=1.0):.2f}**")
    out(f"- Rationale: _{verify.rationale or 'none returned'}_")
    out("")

    if not (is_civic and sufficient):
        out("## Outcome — REJECTED")
        out("")
        out(verify.rationale or "The report did not describe a verifiable civic issue.")
        out("")
        out("The citizen is shown this rationale, which is what makes an automated rejection answerable.")
        return "\n".join(lines)

    # --- Classification, routing, SLA ------------------------------------
    out("## 4. Classification and routing")
    out("")
    ward = resolve_ward(locality)
    classify = infer(
        build_classify(
            description=description,
            nearby_count=0,
            ward_name=ward.name if ward else None,
            category_hint=verify.data.get("category_hint"),
            image_bytes=image_bytes,
            image_mime=image_mime,
        )
    )
    from app.core.enums import ComplaintCategory, Priority

    valid_categories = {c.value for c in ComplaintCategory}
    valid_priorities = {p.value for p in Priority}
    category = as_enum(classify.data.get("category"), valid_categories, "OTHER")
    priority = as_enum(classify.data.get("priority"), valid_priorities, "P3")

    out(f"- Category: **{category}**")
    out(f"- Priority: **{priority}**")
    if ward:
        out(f"- Ward: **{ward.name}** (ward {ward.ward_number}, {ward.zone} zone)")
    else:
        out(f"- Ward: locality `{locality}` is outside the serviced ward list.")

    dept = resolve_department(category)
    hours = sla_hours(category, priority)
    if dept:
        out(f"- Department: **{dept['code']}** — {dept['full_name']} ({dept['service_label']})")
    else:
        out("- Department: no owner in the routing table; would be queued for manual assignment.")
    if hours:
        out(f"- SLA: resolve within **{hours}h**, escalate if untouched after {max(1, round(hours * 0.6))}h.")
    out("")

    out("## Outcome — VERIFIED AND ROUTED")
    out("")
    out(
        f"The complaint would be dispatched to **{dept['code'] if dept else 'manual assignment'}** "
        f"with priority **{priority}**"
        + (f" and a {hours}-hour resolution deadline." if hours else ".")
    )
    out("")
    out(
        "In the full platform this also opens a ticket against the department's "
        "API, starts the SLA clock, and writes a hash-chained audit entry for "
        "every step above."
    )
    return "\n".join(lines)


def main() -> int:
    try:
        payload = read_input()
        mode = configure_provider(payload)

        description = str(payload.get("description") or "").strip()
        if not description:
            write_output(
                "# Nothing to triage\n\n"
                "No complaint text was supplied. Describe the civic problem "
                "(for example: *\"Large pothole on 5th Main, Koramangala — two-wheelers "
                "are swerving into oncoming traffic\"*) and run again."
            )
            return 0

        write_output(triage(payload, mode))
        return 0
    except Exception:
        # Buyers never see stderr, so leave a readable result AND signal
        # failure through the exit code, which is what aiKart acts on.
        traceback.print_exc(file=sys.stderr)
        try:
            write_output(
                "# Triage failed\n\nThe agent hit an unexpected error and could "
                "not complete this run."
            )
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
