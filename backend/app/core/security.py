"""Capture tokens — Layer 1 of the evidence architecture.

The client is never trusted to say when or where a photo was taken. Before the
camera opens it must request a short-lived, HMAC-signed token from the server.
The token embeds its own issue time, the user and device identity, and the GPS
fix at issue. Uploads that cannot present a valid, unexpired, unconsumed token
have no server-bound capture time.

This bounds the age of the evidence server-side: a photo uploaded against a
token issued three minutes ago cannot be an old image unless it was staged on
the device beforehand — a far higher-effort attack than editing EXIF.
"""

from __future__ import annotations

import base64
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Optional

from sqlalchemy.orm import Session

from app.config import settings
from app.models import CaptureToken, utcnow


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64d(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _sign(payload: bytes) -> str:
    return hmac.new(settings.secret_key.encode(), payload, sha256).hexdigest()


@dataclass
class IssuedToken:
    token: str
    token_id: str
    issued_at: datetime
    expires_at: datetime
    ttl_seconds: int


def issue_capture_token(
    db: Session,
    user_id: Optional[int] = None,
    device_id: Optional[str] = None,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
) -> IssuedToken:
    """Mint a signed, single-use capture token and persist it."""
    now = utcnow()
    ttl = settings.capture_token_ttl_seconds
    expires = now + timedelta(seconds=ttl)
    token_id = secrets.token_urlsafe(16)

    payload = {
        "jti": token_id,
        "uid": user_id,
        "did": device_id,
        "iat": int(now.timestamp()),
        "exp": int(expires.timestamp()),
        "lat": latitude,
        "lon": longitude,
    }
    body = _b64e(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    token = f"{body}.{_sign(body.encode())}"

    db.add(
        CaptureToken(
            token_id=token_id,
            user_id=user_id,
            device_id=device_id,
            issued_at=now,
            expires_at=expires,
            issue_latitude=latitude,
            issue_longitude=longitude,
        )
    )
    db.flush()
    return IssuedToken(token, token_id, now, expires, ttl)


@dataclass
class TokenCheck:
    valid: bool
    reason: str
    token_id: Optional[str] = None
    issued_at: Optional[datetime] = None
    issue_latitude: Optional[float] = None
    issue_longitude: Optional[float] = None
    age_seconds: Optional[int] = None

    @property
    def is_replay(self) -> bool:
        return self.reason == "token_replayed"


def verify_capture_token(db: Session, token: Optional[str]) -> TokenCheck:
    """Validate signature, expiry and single-use status."""
    if not token:
        return TokenCheck(False, "token_missing")

    parts = token.split(".")
    if len(parts) != 2:
        return TokenCheck(False, "token_malformed")
    body, signature = parts

    if not hmac.compare_digest(_sign(body.encode()), signature):
        return TokenCheck(False, "token_bad_signature")

    try:
        payload = json.loads(_b64d(body))
    except (ValueError, TypeError):
        return TokenCheck(False, "token_undecodable")

    token_id = payload.get("jti")
    record = db.query(CaptureToken).filter_by(token_id=token_id).one_or_none()
    if record is None:
        return TokenCheck(False, "token_unknown", token_id=token_id)

    now = utcnow()
    expires_at = record.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if now > expires_at:
        return TokenCheck(False, "token_expired", token_id=token_id)

    if record.consumed_at is not None:
        return TokenCheck(False, "token_replayed", token_id=token_id)

    issued_at = record.issued_at
    if issued_at.tzinfo is None:
        issued_at = issued_at.replace(tzinfo=timezone.utc)

    return TokenCheck(
        valid=True,
        reason="ok",
        token_id=token_id,
        issued_at=issued_at,
        issue_latitude=record.issue_latitude,
        issue_longitude=record.issue_longitude,
        age_seconds=int((now - issued_at).total_seconds()),
    )


def consume_capture_token(db: Session, token_id: str) -> None:
    """Mark a token used so a second upload with it is detectable as a replay."""
    record = db.query(CaptureToken).filter_by(token_id=token_id).one_or_none()
    if record and record.consumed_at is None:
        record.consumed_at = utcnow()
        db.flush()
