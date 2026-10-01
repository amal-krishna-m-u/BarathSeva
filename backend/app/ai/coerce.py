"""Strict output coercion — the boundary between model JSON and Python types.

A model provider returns JSON. JSON has no notion of "the Python type this
field is supposed to be" — it just has strings, numbers, booleans and nulls,
and a model is free to hand back any of them regardless of what the schema
asked for. The deterministic stub provider always returns real Python
booleans and in-range floats, which is why none of this has ever mattered
until now: a real model routinely returns the JSON string ``"false"`` where a
boolean was asked for, and ``bool("false") is True`` in Python. That single
footgun is capable of inverting a verdict — a model correctly judging a photo
as not a civic issue would be recorded as valid, because the non-empty string
is truthy.

Every helper here is therefore deliberately boring: it never does ``bool(x)``
or ``float(x)`` on a value of unknown provenance. It pattern-matches the
handful of shapes a model realistically sends, and falls back to a caller-
supplied default for anything else — never raises, because a malformed field
should degrade the complaint's confidence, not take down the pipeline.
"""

from __future__ import annotations

from typing import Any, Optional

#: Case-insensitive string spellings of "true" that models commonly emit.
_TRUE_STRINGS = {"true", "yes", "1"}
#: Case-insensitive string spellings of "false" that models commonly emit.
_FALSE_STRINGS = {"false", "no", "0"}


def as_bool(value: Any, default: bool) -> bool:
    """Coerce a model-returned value to a real bool.

    Real bools pass through untouched. Strings are matched case-insensitively
    against known true/false spellings. Numbers are truthy iff non-zero.
    Anything else (``None``, an empty string, unrecognised text, a dict, a
    list) returns ``default`` rather than guessing. Never ``bool(value)`` —
    that is exactly the bug this function exists to prevent.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        candidate = value.strip().lower()
        if candidate in _TRUE_STRINGS:
            return True
        if candidate in _FALSE_STRINGS:
            return False
        return default
    return default


def as_float(
    value: Any,
    default: float,
    lo: Optional[float] = None,
    hi: Optional[float] = None,
) -> float:
    """Coerce a model-returned value to a float, clamped to ``[lo, hi]``.

    Accepts real numbers and numeric strings (``"0.9"``). Anything that does
    not parse returns ``default`` unclamped — a default is a deliberate
    fallback value, not a measurement, so it is not forced into the range.
    ``lo``/``hi`` of ``None`` disables clamping on that side, which is how a
    confidence score (``0..1``) and an unbounded field share one helper.
    """
    parsed: Optional[float] = None
    if isinstance(value, bool):
        parsed = None  # a bool is not a numeric answer, regardless of int subclassing
    elif isinstance(value, (int, float)):
        parsed = float(value)
    elif isinstance(value, str):
        try:
            parsed = float(value.strip())
        except ValueError:
            parsed = None

    if parsed is None:
        return default
    if lo is not None:
        parsed = max(lo, parsed)
    if hi is not None:
        parsed = min(hi, parsed)
    return parsed


def as_enum(value: Any, allowed: set[str], default: str) -> str:
    """Coerce a model-returned value into a controlled vocabulary.

    Upper-cases the value and checks membership in ``allowed``. A
    hallucinated category (one the model invented, or a near-miss spelling)
    degrades to ``default`` instead of reaching the database — the whole
    point of a controlled vocabulary is that nothing outside it is ever
    persisted.
    """
    if value is None:
        return default
    candidate = str(value).strip().upper()
    if candidate in allowed:
        return candidate
    return default


def validate_against_schema(
    data: dict[str, Any], schema: dict[str, type]
) -> tuple[dict[str, Any], list[str]]:
    """Coerce ``data`` against an expected ``{field: python_type}`` shape.

    Returns ``(coerced, problems)`` where ``problems`` names every field that
    was either missing or present with the wrong Python type — the exact set
    of fields ``as_bool``/``as_float`` silently patched over. A field that is
    missing or wrong-typed is reported even though the returned value is
    still usable, so callers can log it: a model that is quietly sending
    strings instead of booleans is worth knowing about even when the
    coercion saves the complaint from corruption.
    """
    coerced: dict[str, Any] = {}
    problems: list[str] = []

    for field, expected_type in schema.items():
        if field not in data:
            problems.append(field)
            if expected_type is bool:
                coerced[field] = False
            elif expected_type is float:
                coerced[field] = 0.0
            elif expected_type is str:
                coerced[field] = ""
            else:
                coerced[field] = None
            continue

        value = data[field]
        if expected_type is bool:
            coerced[field] = as_bool(value, False)
            if not isinstance(value, bool):
                problems.append(field)
        elif expected_type is float:
            coerced[field] = as_float(value, 0.0)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                problems.append(field)
        else:
            coerced[field] = value
            if not isinstance(value, expected_type):
                problems.append(field)

    return coerced, problems
