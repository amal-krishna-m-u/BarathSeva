"""Image size guard for vision-capable providers.

A citizen's photo can arrive well past ``settings.ai_max_image_bytes`` — modern
phone cameras routinely produce multi-megabyte JPEGs. Providers need a bounded
payload (the request timeout and the vendor's own upload limits both assume
one), but a complaint must never be lost just because its photo was large or
awkward. This module makes that trade-off explicit and provider-agnostic: an
oversized image is downscaled and re-encoded as JPEG with Pillow (already a
dependency, see ``app/core/hashing.py`` and ``app/core/exif.py`` for other
uses); if Pillow cannot make sense of the bytes at all, the image is dropped
and the caller proceeds text-only rather than failing the inference.

Used by ``NvidiaProvider`` and ``GeminiProvider`` — the single size guard
for every vision-capable provider.
"""

from __future__ import annotations

import io
import logging
from typing import Optional

logger = logging.getLogger(__name__)

#: JPEG re-encode quality. Not binary-searched — a fixed quality kept simple
#: and good enough, since the loop below shrinks dimensions (not quality) to
#: close the gap to budget.
_REENCODE_QUALITY = 85

#: What the re-encode path always produces, and therefore what it must
#: report. Hard-coded rather than derived because the ``save`` call below is
#: hard-coded to JPEG too; the two must never drift apart.
_REENCODED_MIME = "image/jpeg"

#: Upper bound on halving iterations. A realistic phone photo (≤ ~50MP) needs
#: nowhere near this many halvings to drop under a few-megabyte budget; the
#: cap just guarantees the loop terminates instead of relying on that.
_MAX_ITERATIONS = 6


def prepare_image_for_inference(
    image_bytes: Optional[bytes], max_bytes: int, mime: Optional[str] = None
) -> tuple[Optional[bytes], Optional[str]]:
    """Return ``(bytes, media_type)`` safe to hand to a vision model.

    The media type is returned alongside the bytes, never assumed by the
    caller, because the re-encode path CHANGES it: a 5MB PNG comes back as
    JPEG, and a provider told ``image/png`` while being handed JPEG bytes is
    being lied to. Strict decoders reject that mismatch, which loses the photo
    on exactly the uploads most likely to need downscaling — and loses it
    silently, since the text half of the request still answers.

    - No bytes in -> ``(None, None)`` (nothing to send).
    - At or under ``max_bytes`` -> returned unchanged, keeping the caller's
      own ``mime`` (no re-encode cost, and no relabelling, for the common case).
    - Over ``max_bytes`` -> repeatedly halved and re-encoded as JPEG until it
      fits or the iteration cap is hit (in which case the smallest size
      reached is returned — still better than the original), reported as
      ``image/jpeg``.
    - Any failure decoding, converting or re-encoding the image ->
      ``(None, None)``. Callers must treat that as "proceed text-only", not as
      an error: a photo a citizen cannot usefully get on the record is better
      dropped than allowed to lose the whole complaint.
    """
    if not image_bytes:
        return None, None
    if len(image_bytes) <= max_bytes:
        return image_bytes, mime

    try:
        from PIL import Image

        with Image.open(io.BytesIO(image_bytes)) as opened:
            img = opened.convert("RGB")

        width, height = img.size
        encoded = image_bytes
        for _ in range(_MAX_ITERATIONS):
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=_REENCODE_QUALITY)
            encoded = buf.getvalue()
            if len(encoded) <= max_bytes:
                return encoded, _REENCODED_MIME
            width = max(1, width // 2)
            height = max(1, height // 2)
            img = img.resize((width, height), Image.Resampling.LANCZOS)
        # Best effort: the smallest size reached within the cap. Still JPEG,
        # so still reported as JPEG even though it missed the budget.
        return encoded, _REENCODED_MIME
    except Exception as exc:
        logger.warning(
            "image downscale failed (%s); dropping image, proceeding text-only",
            exc,
        )
        return None, None
