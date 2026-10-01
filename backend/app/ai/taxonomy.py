"""Keyword taxonomy shared by the deterministic provider and the prompt builders.

This is not a model: it is the rule-based baseline the platform falls back to,
and the controlled vocabulary any model output is coerced into.
"""

from __future__ import annotations

from app.core.enums import ComplaintCategory as C

# Ordered most-specific first: PIPELINE_BURST must win over WATER_LEAK.
CATEGORY_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    (
        C.PIPELINE_BURST.value,
        (
            "pipeline burst", "burst pipe", "pipe burst", "main burst",
            "water gushing", "gushing water", "pipe broken", "water main",
        ),
    ),
    (
        C.SEWAGE.value,
        (
            "sewage", "sewer", "manhole", "septic", "foul smell", "stinking water",
            "drainage overflow", "toilet overflow",
        ),
    ),
    (
        C.WATER_LEAK.value,
        (
            "water leak", "leaking water", "water leakage", "leaky pipe",
            "tap leak", "water wastage", "wasting water", "leaking tap",
        ),
    ),
    (
        C.DRAINAGE.value,
        (
            "drain", "drainage", "storm water", "stormwater", "clogged drain",
            "water logging", "waterlogging", "water logged", "flooded road",
            "flooding", "blocked drain",
        ),
    ),
    (
        C.POTHOLE.value,
        ("pothole", "pot hole", "crater", "hole in the road", "hole in road", "gaddi"),
    ),
    (
        C.ROAD_DAMAGE.value,
        (
            "road damage", "damaged road", "broken road", "road broken",
            "cracked road", "road caved", "road cave", "tar", "uneven road",
            "road dug", "road digging",
        ),
    ),
    (
        C.GARBAGE.value,
        (
            "garbage", "trash", "rubbish", "waste dump", "dumping", "litter",
            "kasa", "not collected", "garbage pile",
        ),
    ),
    (
        C.STREETLIGHT.value,
        (
            "streetlight", "street light", "street lamp", "lamp post",
            "light not working", "dark street", "no light", "lights off",
        ),
    ),
    (
        C.POWER_OUTAGE.value,
        (
            "power cut", "powercut", "no electricity", "power outage", "outage",
            "transformer", "power failure", "no current", "electricity gone",
            "live wire", "no power",
        ),
    ),
]

# Phrases that indicate danger to life or property. Each raises priority.
ESCALATING_KEYWORDS: tuple[str, ...] = (
    "accident", "injured", "injury", "fell", "fallen", "danger", "dangerous",
    "live wire", "electrocut", "child", "children", "school", "hospital",
    "ambulance", "elderly", "two wheeler", "bike skid", "overflowing",
    "gushing", "burst", "collapsed", "sinking", "blocked completely",
    "whole street", "entire area", "no water for", "days",
)

# Phrases indicating physical scale.
SEVERITY_KEYWORDS: tuple[str, ...] = (
    "large", "huge", "massive", "very big", "deep", "wide", "big",
    "several", "multiple", "many",
)

# Indicates the message is not a civic infrastructure report at all.
NON_CIVIC_KEYWORDS: tuple[str, ...] = (
    "hello", "hi there", "test message", "testing", "good morning",
    "thank you", "thanks", "who are you", "what can you do", "help me with",
    "selfie", "my photo", "love", "joke",
)

CATEGORY_LABELS: dict[str, str] = {
    C.POTHOLE.value: "pothole",
    C.ROAD_DAMAGE.value: "road damage",
    C.WATER_LEAK.value: "water leak",
    C.PIPELINE_BURST.value: "burst water pipeline",
    C.DRAINAGE.value: "drainage problem",
    C.SEWAGE.value: "sewage overflow",
    C.GARBAGE.value: "garbage accumulation",
    C.STREETLIGHT.value: "streetlight failure",
    C.POWER_OUTAGE.value: "power outage",
    C.OTHER.value: "civic issue",
}


def match_category(text: str) -> tuple[str, list[str]]:
    """Return (category, matched phrases). Falls back to OTHER."""
    lowered = (text or "").lower()
    for category, phrases in CATEGORY_KEYWORDS:
        hits = [p for p in phrases if p in lowered]
        if hits:
            return category, hits
    return C.OTHER.value, []


def count_hits(text: str, phrases: tuple[str, ...]) -> list[str]:
    lowered = (text or "").lower()
    return [p for p in phrases if p in lowered]
