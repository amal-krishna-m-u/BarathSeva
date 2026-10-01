"""Symmetric encryption for secrets held at rest.

Used for provider API keys entered through the admin dashboard. The point is
narrow and worth stating plainly: the database stops being a place where live
credentials sit in readable form. A dump, a backup, a read replica or a SQL
injection that reads rows no longer hands over working keys, because the
derivation key is ``BARATHSEVA_SECRET_KEY`` and that lives in the environment,
not in a table.

It is NOT protection against an attacker who already has the application host,
since such an attacker has the secret key too. No key-at-rest scheme survives
that without an external KMS or HSM, which is a production concern this
prototype does not claim to address.
"""

from __future__ import annotations

import base64
import hashlib
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

#: Separates this derivation from any other use of the same secret key, so a
#: secret reused elsewhere does not produce the same encryption key here.
_DERIVATION_SALT = b"barathseva.provider-credentials.v1"


def _fernet() -> Fernet:
    """Derive the Fernet key from the application secret.

    Fernet needs 32 url-safe base64 bytes; the configured secret is arbitrary
    text, so it is hashed to a fixed width rather than used directly.
    """
    digest = hashlib.sha256(_DERIVATION_SALT + settings.secret_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str) -> Optional[str]:
    """Return the plaintext, or ``None`` if it cannot be decrypted.

    Returns None rather than raising because the realistic cause is a rotated
    ``BARATHSEVA_SECRET_KEY``, which must degrade to "this provider is not
    configured" and fall back to the environment, not crash the intake path on
    every complaint.
    """
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except (InvalidToken, ValueError, TypeError):
        return None


def mask_secret(plaintext: Optional[str]) -> Optional[str]:
    """Render a key for display: enough to recognise, not enough to use.

    A stored key is never returned in full by any endpoint. An operator needs
    to confirm *which* key is installed, which the last four characters give
    them; nothing about the admin screen requires the rest of it.
    """
    if not plaintext:
        return None
    if len(plaintext) <= 4:
        return "•" * len(plaintext)
    return f"{'•' * 8}{plaintext[-4:]}"
