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

Owned by Task 3, used by ``NvidiaProvider``; a later task may import it too.
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

#: Upper bound on halving iterations. A realistic phone photo (≤ ~50MP) needs
#: nowhere near this many halvings to drop under a few-megabyte budget; the
#: cap just guarantees the loop terminates instead of relying on that.
_MAX_ITERATIONS = 6


def prepare_image_for_inference(
    image_bytes: Optional[bytes], max_bytes: int
) -> Optional[bytes]:
    """Return image bytes safe to hand to a vision model, or ``None``.

    - No bytes in -> ``None`` out (nothing to send).
    - At or under ``max_bytes`` -> returned unchanged (no re-encode cost for
      the common case).
    - Over ``max_bytes`` -> repeatedly halved and re-encoded as JPEG until it
      fits or the iteration cap is hit (in which case the smallest size
      reached is returned — still better than the original).
    - Any failure decoding, converting or re-encoding the image -> ``None``.
      Callers must treat ``None`` as "proceed text-only", not as an error:
      a photo a citizen cannot usefully get on the record is better handled
      by dropping it than by losing the whole complaint.
    """
    if not image_bytes:
        return None
    if len(image_bytes) <= max_bytes:
        return image_bytes

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
                return encoded
            width = max(1, width // 2)
            height = max(1, height // 2)
            img = img.resize((width, height), Image.Resampling.LANCZOS)
        return encoded  # best effort: smallest size reached within the cap
    except Exception as exc:
        logger.warning(
            "image downscale failed (%s); dropping image, proceeding text-only",
            exc,
        )
        return None
