"""Generate synthetic JPEGs with real EXIF for testing the evidence layers.

Produces camera-like images carrying DateTimeOriginal, GPS coordinates and
Make/Model tags, so the EXIF time/GPS consistency checks exercise their real
code paths instead of always seeing "EXIF absent".
"""

from __future__ import annotations

import io
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw
from PIL.TiffImagePlugin import IFDRational

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

IST = timezone(timedelta(minutes=330))

TAG_MAKE = 271
TAG_MODEL = 272
TAG_SOFTWARE = 305
TAG_DATETIME = 306
IFD_EXIF = 0x8769
IFD_GPS = 0x8825
TAG_DATETIME_ORIGINAL = 36867


def _to_dms(value: float) -> tuple[IFDRational, IFDRational, IFDRational]:
    """EXIF GPS stores degrees/minutes/seconds as TIFF rationals."""
    value = abs(value)
    degrees = int(value)
    minutes_full = (value - degrees) * 60
    minutes = int(minutes_full)
    seconds = (minutes_full - minutes) * 60
    return (
        IFDRational(degrees, 1),
        IFDRational(minutes, 1),
        IFDRational(int(round(seconds * 100)), 100),
    )


def _scene(seed: int, size: tuple[int, int]) -> Image.Image:
    """A deterministic but genuinely varied scene.

    Early versions offset one gradient per seed, which produced images whose
    perceptual hashes were only a few bits apart — distinct photos looked like
    reuse of each other. Random blocks plus noise from a seeded PRNG give each
    seed its own structure, so dHash distances between different seeds land
    where real distinct photographs do.
    """
    rng = random.Random(seed * 7919 + 13)
    width, height = size
    background = (rng.randrange(40, 200), rng.randrange(40, 200), rng.randrange(40, 200))
    image = Image.new("RGB", size, background)
    draw = ImageDraw.Draw(image)

    for _ in range(rng.randrange(18, 34)):
        x0 = rng.randrange(0, width)
        y0 = rng.randrange(0, height)
        x1 = min(width, x0 + rng.randrange(width // 12, width // 3))
        y1 = min(height, y0 + rng.randrange(height // 12, height // 3))
        colour = (rng.randrange(256), rng.randrange(256), rng.randrange(256))
        if rng.random() < 0.35:
            draw.ellipse([x0, y0, x1, y1], fill=colour)
        else:
            draw.rectangle([x0, y0, x1, y1], fill=colour)

    # Coarse noise so re-encoding still survives but structure differs by seed.
    noise = Image.new("RGB", (max(1, width // 24), max(1, height // 24)))
    noise_pixels = noise.load()
    for x in range(noise.width):
        for y in range(noise.height):
            level = rng.randrange(256)
            noise_pixels[x, y] = (level, rng.randrange(256), rng.randrange(256))
    noise = noise.resize(size, Image.Resampling.BILINEAR)
    return Image.blend(image, noise, 0.35)


def make_photo(
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    when: Optional[datetime] = None,
    seed: int = 1,
    size: tuple[int, int] = (1600, 1200),
    software: Optional[str] = None,
    with_exif: bool = True,
) -> bytes:
    """Return JPEG bytes, optionally stamped with EXIF time/GPS/provenance."""
    image = _scene(seed, size)
    buffer = io.BytesIO()

    if not with_exif:
        image.save(buffer, "JPEG", quality=88)
        return buffer.getvalue()

    moment = (when or datetime.now(timezone.utc)).astimezone(IST)
    stamp = moment.strftime("%Y:%m:%d %H:%M:%S")

    exif = Image.Exif()
    exif[TAG_MAKE] = "BarathSeva"
    exif[TAG_MODEL] = "TestCam A1"
    exif[TAG_DATETIME] = stamp
    if software:
        exif[TAG_SOFTWARE] = software

    exif.get_ifd(IFD_EXIF)[TAG_DATETIME_ORIGINAL] = stamp

    if latitude is not None and longitude is not None:
        gps = exif.get_ifd(IFD_GPS)
        gps[1] = "N" if latitude >= 0 else "S"
        gps[2] = _to_dms(latitude)
        gps[3] = "E" if longitude >= 0 else "W"
        gps[4] = _to_dms(longitude)

    image.save(buffer, "JPEG", quality=88, exif=exif)
    return buffer.getvalue()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("output")
    parser.add_argument("--lat", type=float, default=12.9352)
    parser.add_argument("--lon", type=float, default=77.6245)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--age-minutes", type=int, default=0)
    parser.add_argument("--no-exif", action="store_true")
    parser.add_argument("--software")
    args = parser.parse_args()

    data = make_photo(
        latitude=args.lat,
        longitude=args.lon,
        when=datetime.now(timezone.utc) - timedelta(minutes=args.age_minutes),
        seed=args.seed,
        software=args.software,
        with_exif=not args.no_exif,
    )
    Path(args.output).write_bytes(data)
    print(f"wrote {args.output} ({len(data)} bytes)")
