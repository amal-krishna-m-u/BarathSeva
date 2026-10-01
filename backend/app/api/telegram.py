"""Telegram webhook — the second intake channel over the same backend contract.

Telegram submissions carry no capture token (there is no in-app camera to bind
to), so they are accepted with a reduced evidence weight rather than rejected.
That trade-off is explicit in the authenticity engine, not hidden here.
"""

from __future__ import annotations

import base64
import logging
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.integrations.telegram import Draft, client, drafts, largest_photo_id
from app.models import Complaint
from app.services.intake import IntakeRequest, submit_complaint

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/telegram", tags=["telegram"])

PROMPT_NEED_LOCATION = (
    "Got it. Now tap the paperclip and share your <b>Location</b> so the "
    "complaint can be mapped to the right ward and department."
)
PROMPT_NEED_DESCRIPTION = (
    "Thanks for the location. Please also send a short description of the "
    "problem (a photo with a caption works best)."
)
PROMPT_HELP = (
    "<b>BarathSeva AI</b>\n\n"
    "Report a civic problem in three steps:\n"
    "1. Send a photo of the issue with a short caption.\n"
    "2. Share your Location.\n"
    "3. You will get a reference number and the department handling it.\n\n"
    "Potholes, water leaks, drainage, garbage, streetlights and power outages "
    "are all supported."
)


@router.get("/status")
def telegram_status() -> dict[str, Any]:
    """Whether the bot is configured. Useful for setup verification."""
    return {
        "enabled": client.enabled,
        "webhook_secret_set": bool(settings.telegram_webhook_secret),
        "note": (
            "Set BARATHSEVA_TELEGRAM_BOT_TOKEN to enable outbound replies and "
            "photo downloads. The webhook accepts and processes updates either "
            "way, but cannot fetch photos or reply without a token."
        ),
    }


@router.post("/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Receive a Telegram update and advance that chat's complaint draft."""
    if settings.telegram_webhook_secret:
        if x_telegram_bot_api_secret_token != settings.telegram_webhook_secret:
            raise HTTPException(status_code=401, detail="Bad webhook secret.")

    update = await request.json()
    message = update.get("message") or update.get("edited_message") or {}
    chat = message.get("chat") or {}
    chat_id = str(chat.get("id") or "")
    if not chat_id:
        return {"ok": True, "skipped": "no chat id"}

    sender = message.get("from") or {}
    display_name = " ".join(
        part for part in [sender.get("first_name"), sender.get("last_name")] if part
    ) or chat.get("title") or "Telegram Citizen"

    text = (message.get("text") or message.get("caption") or "").strip()
    location = message.get("location") or {}
    photo_id = largest_photo_id(message)

    if text.startswith("/start") or text.startswith("/help"):
        drafts.clear(chat_id)
        client.send_message(chat_id, PROMPT_HELP)
        return {"ok": True, "action": "help"}

    if text.startswith("/cancel"):
        drafts.clear(chat_id)
        client.send_message(chat_id, "Draft cleared.")
        return {"ok": True, "action": "cancel"}

    draft: Draft = drafts.get(chat_id)
    draft.display_name = display_name or draft.display_name

    if text and not text.startswith("/"):
        draft.description = text if not draft.description else f"{draft.description} {text}"

    if photo_id:
        photo_bytes, photo_mime = client.download_photo(photo_id)
        if photo_bytes:
            draft.photo_b64 = base64.b64encode(photo_bytes).decode()
            draft.photo_mime = photo_mime

    if not location:
        drafts.put(chat_id, draft)
        client.send_message(chat_id, PROMPT_NEED_LOCATION)
        return {"ok": True, "action": "awaiting_location"}

    if not draft.description:
        drafts.put(chat_id, draft)
        client.send_message(chat_id, PROMPT_NEED_DESCRIPTION)
        return {"ok": True, "action": "awaiting_description"}

    result = submit_complaint(
        db,
        IntakeRequest(
            description=draft.description,
            latitude=float(location["latitude"]),
            longitude=float(location["longitude"]),
            channel="telegram",
            declared_source="telegram",
            capture_token=None,
            image_bytes=draft.photo_bytes,
            image_mime=draft.photo_mime,
            gps_accuracy_meters=float(location.get("horizontal_accuracy") or 0) or None,
            telegram_chat_id=chat_id,
            reporter_name=draft.display_name,
        ),
    )
    drafts.clear(chat_id)

    complaint = db.get(Complaint, result.complaint_id)
    if not result.accepted:
        reply = (
            f"Your report could not be accepted automatically.\n\n"
            f"<b>{result.hard_fail_reason or 'Evidence checks failed.'}</b>\n\n"
            f"Reference <code>{result.reference}</code> is on record. Send a "
            f"fresh photo taken now to try again."
        )
    elif complaint.needs_human_review:
        reply = (
            f"Received — reference <code>{result.reference}</code>.\n\n"
            f"Held for human review before dispatch "
            f"(evidence score {result.authenticity_score:.2f})."
        )
    else:
        due = (
            complaint.sla_due_at.strftime("%d %b %H:%M")
            if complaint.sla_due_at
            else "not set"
        )
        reply = (
            f"Report registered.\n\n"
            f"Reference: <code>{result.reference}</code>\n"
            f"Issue: {complaint.category.value if complaint.category else 'under review'}"
            f" ({complaint.priority.value if complaint.priority else '-'})\n"
            f"Ward: {complaint.ward.name if complaint.ward else 'unmapped'}\n"
            f"Department: {complaint.department.code if complaint.department else 'pending'}\n"
            f"Ticket: <code>{complaint.external_ticket_id or 'pending'}</code>\n"
            f"Resolution deadline: {due}\n\n"
            f"Track it any time with reference <code>{result.reference}</code>."
        )
    client.send_message(chat_id, reply)

    return {
        "ok": True,
        "action": "submitted",
        "reference": result.reference,
        "accepted": result.accepted,
        "status": result.status,
    }
