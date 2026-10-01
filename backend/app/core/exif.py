"""EXIF and file forensics — Layer 2.

Every signal produced here is individually weak and individually forgeable.
Their value is in aggregate, and in being permanently recorded for audit. None
of them is treated as proof on its own.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from PIL import Image, UnidentifiedImageError

from app.config import settings

# EXIF tag ids we care about (avoids a PIL.ExifTags lookup per field).
TAG_DATETIME = 306  # DateTime (base IFD)
TAG_SOFTWARE = 305
TAG_MAKE = 271
TAG_MODEL = 272
IFD_EXIF = 0x8769
IFD_GPS = 0x8825
TAG_DATETIME_ORIGINAL = 36867
TAG_DATETIME_DIGITIZED = 36868

# Dimensions that commonly indicate a screen capture rather than a camera frame.
COMMON_SCREENSHOT_SIZES = {
    (1080, 1920), (1920, 1080), (750, 1334), (1125, 2436), (1170, 2532),
    (1284, 2778), (828, 1792), (1242, 2688), (1440, 2560), (1440, 3200),
    (2560, 1440), (1366, 768), (1536, 2048), (2048, 1536), (1179, 2556),
}


def _to_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _dms_to_decimal(dms: Any, ref: Optional[str]) -> Optional[float]:
    """Convert EXIF degrees/minutes/seconds rationals to signed decimal."""
    try:
        deg, minutes, seconds = (_to_float(v) for v in dms)
    except (TypeError, ValueError):
        return None
    if deg is None or minutes is None or seconds is None:
        return None
    dec = deg + minutes / 60.0 + seconds / 3600.0
    if ref and str(ref).upper() in {"S", "W"}:
        dec = -dec
    return dec


def _parse_exif_datetime(raw: Optional[str]) -> Optional[datetime]:
    """EXIF stores naive local time; interpret it in the configured city offset."""
    if not raw or not isinstance(raw, str):
        return None
    cleaned = raw.strip().replace("/", ":")
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y:%m:%d %H:%M"):
        try:
            naive = datetime.strptime(cleaned, fmt)
        except ValueError:
            continue
        tz = timezone(timedelta(minutes=settings.city_utc_offset_minutes))
        return naive.replace(tzinfo=tz).astimezone(timezone.utc)
    return None


@dataclass
class ImageFacts:
    readable: bool = False
    width: Optional[int] = None
    height: Optional[int] = None
    image_format: Optional[str] = None
    exif: dict = field(default_factory=dict)
    exif_present: bool = False
    exif_datetime: Optional[datetime] = None
    exif_latitude: Optional[float] = None
    exif_longitude: Optional[float] = None
    software: Optional[str] = None
    make: Optional[str] = None
    model: Optional[str] = None
    error: Optional[str] = None

    @property
    def looks_like_screenshot(self) -> bool:
        if not self.width or not self.height:
            return False
        return (self.width, self.height) in COMMON_SCREENSHOT_SIZES

    @property
    def has_gps(self) -> bool:
        return self.exif_latitude is not None and self.exif_longitude is not None


def extract_image_facts(data: bytes) -> ImageFacts:
    """Read dimensions, EXIF, embedded GPS and provenance tags from an image."""
    facts = ImageFacts()
    if not data:
        facts.error = "empty_payload"
        return facts

    try:
        with Image.open(io.BytesIO(data)) as img:
            facts.readable = True
            facts.width, facts.height = img.size
            facts.image_format = img.format
            raw_exif = img.getexif()
    except UnidentifiedImageError:
        facts.error = "unidentified_image"
        return facts
    except Exception as exc:  # pragma: no cover - defensive
        facts.error = f"unreadable:{type(exc).__name__}"
        return facts

    if not raw_exif:
        return facts

    collected: dict[str, Any] = {}
    facts.software = raw_exif.get(TAG_SOFTWARE)
    facts.make = raw_exif.get(TAG_MAKE)
    facts.model = raw_exif.get(TAG_MODEL)
    base_dt = raw_exif.get(TAG_DATETIME)

    for key, value in raw_exif.items():
        if isinstance(value, (str, int, float)):
            collected[str(key)] = value

    exif_ifd: dict = {}
    gps_ifd: dict = {}
    try:
        exif_ifd = dict(raw_exif.get_ifd(IFD_EXIF) or {})
        gps_ifd = dict(raw_exif.get_ifd(IFD_GPS) or {})
    except Exception:  # pragma: no cover - malformed exif
        pass

    original_dt = exif_ifd.get(TAG_DATETIME_ORIGINAL) or exif_ifd.get(
        TAG_DATETIME_DIGITIZED
    )
    facts.exif_datetime = _parse_exif_datetime(original_dt) or _parse_exif_datetime(
        base_dt
    )

    if gps_ifd:
        facts.exif_latitude = _dms_to_decimal(gps_ifd.get(2), gps_ifd.get(1))
        facts.exif_longitude = _dms_to_decimal(gps_ifd.get(4), gps_ifd.get(3))

    facts.exif_present = bool(collected or exif_ifd or gps_ifd)
    facts.exif = {
        "base": collected,
        "datetime_raw": str(original_dt or base_dt or ""),
        "software": str(facts.software or ""),
        "make": str(facts.make or ""),
        "model": str(facts.model or ""),
        "gps_present": bool(gps_ifd),
        "exif_latitude": facts.exif_latitude,
        "exif_longitude": facts.exif_longitude,
    }
    return facts
