"""Telegram Bot API client and draft store.

A Telegram report arrives as several messages — a photo with a caption, then a
shared location — so the bot needs a little state. Drafts live in Redis with a
short TTL: enough to complete one report, not long enough to accumulate.
"""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

API_ROOT = "https://api.telegram.org"
DRAFT_TTL_SECONDS = 900
DRAFT_KEY = "barathseva:telegram:draft:{chat_id}"


@dataclass
class Draft:
    description: str = ""
    photo_b64: Optional[str] = None
    photo_mime: Optional[str] = None
    display_name: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, raw: str) -> "Draft":
        return cls(**json.loads(raw))

    @property
    def photo_bytes(self) -> Optional[bytes]:
        return base64.b64decode(self.photo_b64) if self.photo_b64 else None


class DraftStore:
    """Redis-backed draft storage with an in-process fallback.

    The fallback exists so the webhook still works if Redis is briefly down;
    it is per-process and deliberately not a substitute for Redis.
    """

    def __init__(self) -> None:
        self._memory: dict[str, Draft] = {}
        self._redis = None
        try:
            import redis

            self._redis = redis.Redis.from_url(settings.redis_url, decode_responses=True)
            self._redis.ping()
        except Exception as exc:  # pragma: no cover - optional dependency path
            logger.warning("Redis unavailable for Telegram drafts (%s); using memory", exc)
            self._redis = None

    def get(self, chat_id: str) -> Draft:
        if self._redis is not None:
            try:
                raw = self._redis.get(DRAFT_KEY.format(chat_id=chat_id))
                return Draft.from_json(raw) if raw else Draft()
            except Exception:
                pass
        return self._memory.get(chat_id, Draft())

    def put(self, chat_id: str, draft: Draft) -> None:
        if self._redis is not None:
            try:
                self._redis.setex(
                    DRAFT_KEY.format(chat_id=chat_id), DRAFT_TTL_SECONDS, draft.to_json()
                )
                return
            except Exception:
                pass
        self._memory[chat_id] = draft

    def clear(self, chat_id: str) -> None:
        if self._redis is not None:
            try:
                self._redis.delete(DRAFT_KEY.format(chat_id=chat_id))
            except Exception:
                pass
        self._memory.pop(chat_id, None)


class TelegramClient:
    """Thin Bot API wrapper. Inert when no token is configured."""

    def __init__(self, token: Optional[str] = None) -> None:
        self.token = token or settings.telegram_bot_token

    @property
    def enabled(self) -> bool:
        return bool(self.token)

    def _url(self, method: str) -> str:
        return f"{API_ROOT}/bot{self.token}/{method}"

    def send_message(self, chat_id: str | int, text: str) -> bool:
        if not self.enabled:
            logger.info("telegram disabled; would send to %s: %s", chat_id, text[:120])
            return False
        try:
            response = httpx.post(
                self._url("sendMessage"),
                json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
                timeout=10.0,
            )
            response.raise_for_status()
            return True
        except Exception as exc:
            logger.warning("telegram sendMessage failed: %s", exc)
            return False

    def download_photo(self, file_id: str) -> tuple[Optional[bytes], Optional[str]]:
        """Resolve a file_id to bytes via getFile, then the file endpoint."""
        if not self.enabled:
            return None, None
        try:
            meta = httpx.get(self._url("getFile"), params={"file_id": file_id}, timeout=10.0)
            meta.raise_for_status()
            path = meta.json()["result"]["file_path"]
            blob = httpx.get(f"{API_ROOT}/file/bot{self.token}/{path}", timeout=30.0)
            blob.raise_for_status()
            mime = "image/jpeg" if path.lower().endswith((".jpg", ".jpeg")) else "image/png"
            return blob.content, mime
        except Exception as exc:
            logger.warning("telegram download_photo failed: %s", exc)
            return None, None


def largest_photo_id(message: dict[str, Any]) -> Optional[str]:
    """Telegram sends several sizes; the last entry is the largest."""
    photos = message.get("photo") or []
    if not photos:
        return None
    return photos[-1].get("file_id")


client = TelegramClient()
drafts = DraftStore()
