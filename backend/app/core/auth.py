"""Authentication: password hashing, session tokens, and role dependencies.

Three decisions worth stating, because each prevents a specific failure:

  * Passwords are SHA-256 pre-hashed before bcrypt. bcrypt silently truncates
    at 72 bytes, so without this a long passphrase would be validated on only
    its first 72 bytes — and two different long passphrases sharing a prefix
    would both unlock the account.
  * Tokens carry the account's ``token_version``. Changing a password bumps
    the counter, which invalidates every token issued under the old one.
    Without this, a stolen token survives the password change meant to stop it.
  * A DEPT_ADMIN's department is resolved from the *database*, never from the
    token's claim, at every request. A claim is attacker-influenced input once
    a key leaks; the row is not.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.config import settings
from app.core.enums import UserRole
from app.db import get_db
from app.models import User, utcnow

logger = logging.getLogger(__name__)

bearer_scheme = HTTPBearer(auto_error=False)

# Generic on purpose: never reveal whether an address is registered.
INVALID_CREDENTIALS = "Incorrect email or password."


# --------------------------------------------------------------- passwords
def _prepare(password: str) -> bytes:
    """SHA-256 then base64, so bcrypt never sees more than 44 bytes."""
    digest = hashlib.sha256(password.encode("utf-8")).digest()
    return base64.b64encode(digest)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(
        _prepare(password), bcrypt.gensalt(rounds=settings.bcrypt_rounds)
    ).decode("utf-8")


def verify_password(password: str, password_hash: Optional[str]) -> bool:
    if not password_hash:
        return False
    try:
        return bcrypt.checkpw(_prepare(password), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


class PasswordPolicyError(ValueError):
    pass


def validate_password(password: str) -> None:
    """Minimum viable policy. Deliberately not a complexity maze — length is
    what actually matters, and arbitrary character rules push people toward
    predictable substitutions."""
    if len(password) < settings.password_min_length:
        raise PasswordPolicyError(
            f"Password must be at least {settings.password_min_length} characters."
        )
    if len(password.encode("utf-8")) > 1024:
        raise PasswordPolicyError("Password is unreasonably long.")
    if password.lower() in {"password", "12345678", "qwertyui", "barathseva"}:
        raise PasswordPolicyError("That password is too common.")


# ------------------------------------------------------------------ tokens
@dataclass
class IssuedSession:
    access_token: str
    token_type: str
    expires_at: datetime
    expires_in: int


def _credentials_stamp(user: User) -> int:
    """The token generation this account is currently on."""
    return int(user.token_version or 1)


def create_access_token(user: User) -> IssuedSession:
    now = utcnow()
    expires = now + timedelta(minutes=settings.access_token_ttl_minutes)
    payload = {
        "sub": str(user.id),
        "role": user.role.value,
        "dept": user.department_id,
        "tv": _credentials_stamp(user),
        "iat": int(now.timestamp()),
        "exp": int(expires.timestamp()),
    }
    token = jwt.encode(
        payload, settings.resolved_jwt_secret, algorithm=settings.jwt_algorithm
    )
    return IssuedSession(
        access_token=token,
        token_type="bearer",
        expires_at=expires,
        expires_in=settings.access_token_ttl_minutes * 60,
    )


def decode_token(token: str) -> dict:
    return jwt.decode(
        token,
        settings.resolved_jwt_secret,
        algorithms=[settings.jwt_algorithm],
        options={"require": ["exp", "sub"]},
    )


# ------------------------------------------------------------ rate limiting
class LoginThrottle:
    """Caps failed logins per identifier. Redis-backed, memory fallback."""

    def __init__(self) -> None:
        self._memory: dict[str, list[float]] = {}
        self._redis = None
        try:
            import redis

            self._redis = redis.Redis.from_url(
                settings.redis_url, decode_responses=True, socket_connect_timeout=2
            )
            self._redis.ping()
        except Exception as exc:  # pragma: no cover - optional path
            logger.warning("Redis unavailable for login throttling (%s)", exc)
            self._redis = None

    def _key(self, identifier: str) -> str:
        return f"barathseva:login_fail:{identifier.lower()}"

    def register_failure(self, identifier: str) -> None:
        key = self._key(identifier)
        window = settings.login_attempt_window_seconds
        if self._redis is not None:
            try:
                count = self._redis.incr(key)
                if count == 1:
                    self._redis.expire(key, window)
                return
            except Exception:
                pass
        now = time.time()
        bucket = [t for t in self._memory.get(key, []) if now - t < window]
        bucket.append(now)
        self._memory[key] = bucket

    def is_locked(self, identifier: str) -> bool:
        key = self._key(identifier)
        limit = settings.login_max_attempts
        if self._redis is not None:
            try:
                value = self._redis.get(key)
                return bool(value) and int(value) >= limit
            except Exception:
                pass
        window = settings.login_attempt_window_seconds
        now = time.time()
        bucket = [t for t in self._memory.get(key, []) if now - t < window]
        self._memory[key] = bucket
        return len(bucket) >= limit

    def clear(self, identifier: str) -> None:
        key = self._key(identifier)
        if self._redis is not None:
            try:
                self._redis.delete(key)
                return
            except Exception:
                pass
        self._memory.pop(key, None)


throttle = LoginThrottle()


# ------------------------------------------------------------- dependencies
def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user_optional(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> Optional[User]:
    """Resolve the caller if a valid token is present, else None.

    Used by intake: reporting anonymously stays possible, but a signed-in
    citizen gets their report attributed so it appears under "my reports".
    """
    if credentials is None or not credentials.credentials:
        return None
    try:
        payload = decode_token(credentials.credentials)
    except jwt.PyJWTError:
        return None

    user = db.get(User, int(payload.get("sub", 0) or 0))
    if user is None or not user.is_active:
        return None
    if payload.get("tv") != _credentials_stamp(user):
        return None  # issued before the credentials changed
    return user


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None or not credentials.credentials:
        raise _unauthorized("Not authenticated.")
    try:
        payload = decode_token(credentials.credentials)
    except jwt.ExpiredSignatureError:
        raise _unauthorized("Session expired. Please sign in again.")
    except jwt.PyJWTError:
        raise _unauthorized("Invalid session token.")

    user = db.get(User, int(payload.get("sub", 0) or 0))
    if user is None:
        raise _unauthorized("Account no longer exists.")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="This account is disabled.")
    if payload.get("tv") != _credentials_stamp(user):
        raise _unauthorized("Credentials changed. Please sign in again.")
    return user


def require_roles(*roles: UserRole) -> Callable[..., User]:
    """Dependency factory enforcing membership in a role set."""
    allowed = set(roles)

    def _guard(user: User = Depends(get_current_user)) -> User:
        if user.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "This endpoint requires one of: "
                    f"{', '.join(sorted(r.value for r in allowed))}."
                ),
            )
        return user

    return _guard


require_citizen = require_roles(UserRole.CITIZEN)
require_staff = require_roles(UserRole.DEPT_ADMIN, UserRole.SUPER_ADMIN)
require_super_admin = require_roles(UserRole.SUPER_ADMIN)


def require_department_admin(
    user: User = Depends(require_roles(UserRole.DEPT_ADMIN, UserRole.SUPER_ADMIN)),
) -> User:
    """A department admin must actually be attached to a department.

    A DEPT_ADMIN with no department would otherwise fall through scoping
    filters and see everything — exactly the bug this guard exists to prevent.
    """
    if user.role is UserRole.DEPT_ADMIN and user.department_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "This staff account is not attached to any department. "
                "Ask a super admin to assign one."
            ),
        )
    return user


def client_identifier(request: Request, email: str) -> str:
    """Throttle key: the account plus the caller's address."""
    host = request.client.host if request.client else "unknown"
    return f"{email.strip().lower()}|{host}"
