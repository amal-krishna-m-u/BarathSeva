"""Authentication endpoints.

Self-registration creates citizens only. Staff accounts — department admins
and super admins — are provisioned through seeding or by a super admin, never
by anyone choosing their own role at signup. Letting a registration payload
carry a role is how privilege escalation happens.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import Response
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.api.serializers import user_out
from app.config import settings
from app.core.auth import (
    INVALID_CREDENTIALS,
    PasswordPolicyError,
    client_identifier,
    create_access_token,
    get_current_user,
    hash_password,
    throttle,
    validate_password,
    verify_password,
)
from app.core.enums import UserRole
from app.db import get_db
from app.models import User, utcnow
from app.schemas import (
    ChangePasswordRequest,
    LoginRequest,
    RegisterRequest,
    SessionResponse,
    UserOut,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])


def _session_response(db: Session, user: User) -> SessionResponse:
    issued = create_access_token(user)
    return SessionResponse(
        access_token=issued.access_token,
        token_type=issued.token_type,
        expires_in=issued.expires_in,
        expires_at=issued.expires_at,
        user=user_out(user),
    )


@router.post("/register", response_model=SessionResponse, status_code=201)
def register(
    payload: RegisterRequest, db: Session = Depends(get_db)
) -> SessionResponse:
    """Register a citizen account and sign them straight in."""
    if not settings.allow_citizen_self_registration:
        raise HTTPException(
            status_code=403, detail="Self-registration is disabled on this deployment."
        )

    email = payload.email.strip().lower()
    try:
        validate_password(payload.password)
    except PasswordPolicyError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    existing = (
        db.query(User).filter(func.lower(User.email) == email).one_or_none()
    )
    if existing is not None:
        raise HTTPException(
            status_code=409, detail="An account with this email already exists."
        )

    phone = (payload.phone or "").strip() or None
    if phone:
        claimed = db.query(User).filter(User.phone == phone).one_or_none()
        if claimed is not None and claimed.email:
            raise HTTPException(
                status_code=409, detail="That phone number is already registered."
            )
        if claimed is not None:
            # An anonymous reporter is claiming their prior reports by phone.
            claimed.email = email
            claimed.display_name = payload.display_name.strip()
            claimed.password_hash = hash_password(payload.password)
            claimed.is_verified = True
            claimed.credentials_changed_at = utcnow()
            claimed.last_login_at = utcnow()
            db.commit()
            return _session_response(db, claimed)

    user = User(
        display_name=payload.display_name.strip(),
        email=email,
        phone=phone,
        role=UserRole.CITIZEN,  # never taken from the request
        password_hash=hash_password(payload.password),
        is_active=True,
        is_verified=True,
        trust_score=0.5,
        credentials_changed_at=utcnow(),
        last_login_at=utcnow(),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    logger.info("registered citizen %s", user.id)
    return _session_response(db, user)


@router.post("/login", response_model=SessionResponse)
def login(
    payload: LoginRequest, request: Request, db: Session = Depends(get_db)
) -> SessionResponse:
    """Sign in. One generic error for every failure mode, so the endpoint
    cannot be used to enumerate which addresses are registered."""
    email = payload.email.strip().lower()
    identifier = client_identifier(request, email)

    if throttle.is_locked(identifier):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                "Too many failed sign-in attempts. Try again in "
                f"{settings.login_attempt_window_seconds // 60} minutes."
            ),
        )

    user = (
        db.query(User)
        .options(joinedload(User.department))
        .filter(func.lower(User.email) == email)
        .one_or_none()
    )

    if user is None or not verify_password(payload.password, user.password_hash):
        throttle.register_failure(identifier)
        raise HTTPException(status_code=401, detail=INVALID_CREDENTIALS)

    if not user.is_active:
        throttle.register_failure(identifier)
        raise HTTPException(status_code=403, detail="This account is disabled.")

    throttle.clear(identifier)
    user.last_login_at = utcnow()
    db.commit()
    db.refresh(user)
    logger.info("login: user=%s role=%s", user.id, user.role.value)
    return _session_response(db, user)


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> UserOut:
    return user_out(user)


@router.post("/change-password", response_model=SessionResponse)
def change_password(
    payload: ChangePasswordRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SessionResponse:
    """Rotate the password and invalidate every existing session token."""
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=401, detail="Current password is incorrect.")
    try:
        validate_password(payload.new_password)
    except PasswordPolicyError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if verify_password(payload.new_password, user.password_hash):
        raise HTTPException(
            status_code=422, detail="New password must differ from the current one."
        )

    user.password_hash = hash_password(payload.new_password)
    # Bumping the counter invalidates every previously issued token.
    user.token_version = (user.token_version or 1) + 1
    user.credentials_changed_at = utcnow()
    db.commit()
    db.refresh(user)
    return _session_response(db, user)


@router.post("/logout", status_code=204, response_class=Response)
def logout(user: User = Depends(get_current_user)) -> Response:
    """Client-side token discard.

    Stateless JWTs cannot be revoked individually without a denylist, which
    this prototype does not keep. The honest position: the client drops the
    token, and anything stronger (immediate revocation) needs server-side
    session storage — listed under Production in the README. Changing a
    password *does* invalidate every outstanding token.
    """
    return Response(status_code=204)
