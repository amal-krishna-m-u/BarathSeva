"""Media persistence. Files are addressed by content hash so an identical
upload never occupies storage twice."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from app.config import settings

EXTENSION_BY_MIME = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/heic": ".heic",
    "image/heif": ".heif",
}


def media_root() -> Path:
    root = Path(settings.media_root)
    root.mkdir(parents=True, exist_ok=True)
    return root


def store_image(data: bytes, sha256: str, mime: Optional[str]) -> str:
    """Write bytes under a content-addressed path and return the relative path."""
    ext = EXTENSION_BY_MIME.get((mime or "").lower(), ".bin")
    shard = sha256[:2]
    folder = media_root() / shard
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{sha256}{ext}"
    if not path.exists():
        path.write_bytes(data)
    return str(path.relative_to(media_root()))


def resolve_path(relative: str) -> Path:
    return media_root() / relative
