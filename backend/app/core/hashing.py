"""Content and perceptual hashing — Layer 4, plus the audit hash chain.

``sha256`` catches byte-identical re-submissions. ``dhash`` is a 64-bit
perceptual fingerprint that survives rescaling, light cropping, re-encoding and
quality changes — the common case an exact hash misses.
"""

from __future__ import annotations

import io
import json
from hashlib import sha256 as _sha256
from typing import Optional

from PIL import Image

DHASH_SIZE = 8  # produces an 8x8 comparison grid -> 64 bits -> 16 hex chars


def sha256_bytes(data: bytes) -> str:
    return _sha256(data).hexdigest()


def dhash(data: bytes, size: int = DHASH_SIZE) -> Optional[str]:
    """Difference hash: compare each pixel with its right-hand neighbour."""
    try:
        with Image.open(io.BytesIO(data)) as img:
            grey = img.convert("L").resize((size + 1, size), Image.Resampling.LANCZOS)
            pixels = list(grey.getdata())
    except Exception:
        return None

    bits = 0
    index = 0
    for row in range(size):
        base = row * (size + 1)
        for col in range(size):
            left = pixels[base + col]
            right = pixels[base + col + 1]
            if left > right:
                bits |= 1 << index
            index += 1
    return f"{bits:0{size * size // 4}x}"


def hamming_distance(a: Optional[str], b: Optional[str]) -> Optional[int]:
    """Bit difference between two hex fingerprints of equal length."""
    if not a or not b or len(a) != len(b):
        return None
    try:
        return bin(int(a, 16) ^ int(b, 16)).count("1")
    except ValueError:
        return None


def chain_hash(prev_hash: Optional[str], payload: dict) -> str:
    """Link an audit entry to its predecessor, making the trail tamper-evident."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return _sha256(f"{prev_hash or ''}|{canonical}".encode()).hexdigest()
