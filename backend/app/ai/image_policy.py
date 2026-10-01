"""The image-gate vocabulary and the thresholds applied to it.

Lives beside the prompt that defines the vocabulary (``app/ai/prompts.py``)
rather than inside the agent that consumes it, for two reasons. The words the
model is allowed to answer with and the words the pipeline is willing to act on
must be the same list, and keeping them in one module is how that stays true.
And this module imports nothing from the database layer, so a caller that only
needs the policy -- the aiKart sandbox agent, which runs with no Postgres --
can read it without dragging in SQLAlchemy and a Postgres driver.
"""

from __future__ import annotations

#: Every answer the model may give for ``image_kind``. Anything outside this
#: set is a hallucination and is coerced to UNREADABLE rather than trusted.
IMAGE_KINDS = {
    "CAMERA_PHOTO_PLAUSIBLE",
    "PERSON_OR_GROUP",
    "INDOOR_SCENE",
    "SCREENSHOT_OR_REPOST",
    "ILLUSTRATION_OR_RENDER",
    "UNRELATED_SCENE",
    "UNREADABLE",
}

#: The only kind that may continue automatically. UNREADABLE is deliberately
#: excluded: "I could not tell" is not consent to dispatch a crew.
PASS_KINDS = {"CAMERA_PHOTO_PLAUSIBLE"}

#: Below this the model's verdict is not trusted in either direction, so the
#: complaint goes to a human rather than being auto-approved OR auto-rejected.
MIN_CONFIDENCE = 0.40
