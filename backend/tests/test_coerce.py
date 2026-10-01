"""Unit tests for the strict output coercion boundary (``app.ai.coerce``).

``as_bool("false") is False`` is the headline regression test: it is the
exact shape of value a real model sends and the stub never did, which is why
``bool(result.data.get("is_civic_issue", False))`` in the verifier was able
to silently invert a "not a civic issue" verdict into a valid one.
"""

from __future__ import annotations

import pytest

from app.ai.coerce import as_bool, as_enum, as_float, validate_against_schema


# ------------------------------------------------------------------- as_bool
class TestAsBool:
    def test_string_false_is_false(self):
        """The headline regression test: bool("false") is True in Python."""
        assert as_bool("false", True) is False

    @pytest.mark.parametrize("value", ["False", "FALSE", "no", "No", "NO", "0"])
    def test_false_spellings(self, value):
        assert as_bool(value, True) is False

    @pytest.mark.parametrize("value", ["true", "True", "TRUE", "yes", "Yes", "1"])
    def test_true_spellings(self, value):
        assert as_bool(value, False) is True

    def test_real_true_passes_through(self):
        assert as_bool(True, False) is True

    def test_real_false_passes_through(self):
        assert as_bool(False, True) is False

    def test_empty_string_returns_default(self):
        assert as_bool("", True) is True
        assert as_bool("", False) is False

    def test_none_returns_default(self):
        assert as_bool(None, True) is True
        assert as_bool(None, False) is False

    def test_garbage_string_returns_default(self):
        assert as_bool("garbage", True) is True
        assert as_bool("garbage", False) is False

    def test_numeric_zero_is_false(self):
        assert as_bool(0, True) is False

    def test_numeric_one_is_true(self):
        assert as_bool(1, False) is True

    def test_numeric_nonzero_float_is_true(self):
        assert as_bool(0.5, False) is True

    def test_never_bare_bool_of_string(self):
        """Documents the exact footgun this module exists to avoid."""
        assert bool("false") is True  # the bug, for contrast
        assert as_bool("false", False) is False  # the fix


# ------------------------------------------------------------------ as_float
class TestAsFloat:
    def test_real_float_passes_through(self):
        assert as_float(0.42, 0.0) == 0.42

    def test_real_int_is_converted(self):
        assert as_float(2, 0.0) == 2.0

    def test_numeric_string_is_parsed(self):
        assert as_float("0.9", 0.0) == 0.9

    def test_numeric_string_with_whitespace(self):
        assert as_float(" 0.75 ", 0.0) == 0.75

    def test_garbage_string_returns_default(self):
        assert as_float("garbage", 0.5) == 0.5

    def test_none_returns_default(self):
        assert as_float(None, 0.3) == 0.3

    def test_clamps_above_hi(self):
        assert as_float(1.7, 0.0, lo=0.0, hi=1.0) == 1.0

    def test_clamps_below_lo(self):
        assert as_float(-0.3, 0.0, lo=0.0, hi=1.0) == 0.0

    def test_within_range_unclamped(self):
        assert as_float(0.6, 0.0, lo=0.0, hi=1.0) == 0.6

    def test_string_clamps_too(self):
        assert as_float("1.7", 0.0, lo=0.0, hi=1.0) == 1.0

    def test_default_not_forced_into_range(self):
        """A default is a fallback, not a measurement — it is not clamped."""
        assert as_float("garbage", 5.0, lo=0.0, hi=1.0) == 5.0

    def test_bool_is_not_treated_as_numeric(self):
        assert as_float(True, 0.25) == 0.25


# ------------------------------------------------------------------- as_enum
class TestAsEnum:
    ALLOWED = {"POTHOLE", "GARBAGE", "OTHER"}

    def test_valid_value_passes_through(self):
        assert as_enum("POTHOLE", self.ALLOWED, "OTHER") == "POTHOLE"

    def test_lowercase_is_uppercased_and_matched(self):
        assert as_enum("pothole", self.ALLOWED, "OTHER") == "POTHOLE"

    def test_hallucinated_value_degrades_to_default(self):
        assert as_enum("SPACESHIP", self.ALLOWED, "OTHER") == "OTHER"

    def test_none_degrades_to_default(self):
        assert as_enum(None, self.ALLOWED, "OTHER") == "OTHER"

    def test_whitespace_is_stripped(self):
        assert as_enum("  garbage  ", self.ALLOWED, "OTHER") == "GARBAGE"


# ----------------------------------------------------- validate_against_schema
class TestValidateAgainstSchema:
    def test_well_formed_data_reports_no_problems(self):
        schema = {"is_civic_issue": bool, "confidence": float, "note": str}
        data = {"is_civic_issue": True, "confidence": 0.8, "note": "fine"}
        coerced, problems = validate_against_schema(data, schema)
        assert problems == []
        assert coerced == {"is_civic_issue": True, "confidence": 0.8, "note": "fine"}

    def test_missing_field_is_reported(self):
        schema = {"is_civic_issue": bool, "confidence": float}
        data = {"is_civic_issue": True}
        coerced, problems = validate_against_schema(data, schema)
        assert "confidence" in problems
        assert coerced["confidence"] == 0.0

    def test_wrong_typed_bool_field_is_reported_and_coerced(self):
        schema = {"is_civic_issue": bool}
        data = {"is_civic_issue": "false"}
        coerced, problems = validate_against_schema(data, schema)
        assert "is_civic_issue" in problems
        assert coerced["is_civic_issue"] is False

    def test_wrong_typed_float_field_is_reported_and_coerced(self):
        schema = {"confidence": float}
        data = {"confidence": "0.9"}
        coerced, problems = validate_against_schema(data, schema)
        assert "confidence" in problems
        assert coerced["confidence"] == 0.9

    def test_missing_and_wrong_typed_both_reported_together(self):
        schema = {"is_civic_issue": bool, "evidence_sufficient": bool, "confidence": float}
        data = {"is_civic_issue": "true"}
        coerced, problems = validate_against_schema(data, schema)
        assert set(problems) == {"is_civic_issue", "evidence_sufficient", "confidence"}
        assert coerced["is_civic_issue"] is True
        assert coerced["evidence_sufficient"] is False
        assert coerced["confidence"] == 0.0
