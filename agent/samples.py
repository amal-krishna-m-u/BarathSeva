"""Synthetic sample photographs for the aiKart sandbox.

Generated with Pillow at runtime rather than committed as image files, for one
reason that matters: the interesting negative cases for an image guard are
photographs of people, and publishing a real person's likeness inside a public
Docker image to demo a "this is not a pothole" check is not a trade worth
making. Everything here is drawn from primitives and depicts nobody.

These exist because the aiKart v1 input types are text, textarea, number,
boolean and select -- there is no file upload. A buyer who wants to test a real
photograph pastes it as base64 into the optional field instead.
"""

from __future__ import annotations

import io

from PIL import Image, ImageDraw


def _encode(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    return buf.getvalue()


def road_with_pothole() -> bytes:
    """The positive case: a road surface with an obvious crater."""
    img = Image.new("RGB", (640, 480), (108, 108, 112))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, 640, 130], fill=(138, 180, 216))          # sky
    d.rectangle([0, 130, 640, 185], fill=(92, 96, 90))           # buildings
    for x in range(0, 640, 90):                                   # lane markings
        d.rectangle([x + 24, 300, x + 70, 311], fill=(238, 238, 230))
    d.ellipse([228, 330, 452, 440], fill=(32, 30, 28))            # the pothole
    d.ellipse([252, 349, 428, 421], fill=(16, 18, 38))            # standing water
    d.ellipse([300, 366, 360, 392], fill=(42, 48, 78))            # reflection
    return _encode(img)


def indoor_room() -> bytes:
    """A plainly indoor scene: wrong context for a street complaint."""
    img = Image.new("RGB", (640, 480), (232, 224, 208))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 360, 640, 480], fill=(150, 110, 74))          # floor
    d.rectangle([70, 120, 250, 350], fill=(118, 150, 190))        # window
    d.line([160, 120, 160, 350], fill=(240, 240, 240), width=6)
    d.line([70, 235, 250, 235], fill=(240, 240, 240), width=6)
    d.rectangle([380, 250, 580, 370], fill=(96, 72, 56))          # table
    d.rectangle([300, 60, 360, 120], fill=(210, 200, 180))        # picture frame
    return _encode(img)


def flat_illustration() -> bytes:
    """A cartoon, not a photograph — tests the illustration verdict."""
    img = Image.new("RGB", (640, 480), (255, 246, 214))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 320, 640, 480], fill=(120, 200, 140))         # flat green ground
    d.ellipse([240, 150, 400, 310], fill=(255, 214, 86), outline=(40, 40, 40), width=7)
    d.ellipse([285, 200, 305, 220], fill=(40, 40, 40))
    d.ellipse([335, 200, 355, 220], fill=(40, 40, 40))
    d.arc([285, 230, 355, 280], start=10, end=170, fill=(40, 40, 40), width=7)
    d.rectangle([60, 360, 180, 440], fill=(240, 120, 110), outline=(40, 40, 40), width=6)
    return _encode(img)


def blank_frame() -> bytes:
    """An almost-empty frame: the unreadable-evidence case."""
    return _encode(Image.new("RGB", (640, 480), (246, 246, 246)))


SAMPLE_BUILDERS = {
    "Road with a pothole": road_with_pothole,
    "Indoor room": indoor_room,
    "Cartoon illustration": flat_illustration,
    "Blank frame": blank_frame,
}

SAMPLE_NOTES = {
    "Road with a pothole": "Bundled sample: a synthetic road surface with a water-filled crater.",
    "Indoor room": "Bundled sample: a synthetic indoor scene (window, table) — not a street.",
    "Cartoon illustration": "Bundled sample: a flat cartoon illustration, not a photograph.",
    "Blank frame": "Bundled sample: a near-blank frame carrying no evidence.",
}
