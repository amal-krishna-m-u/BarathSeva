"""Error taxonomy and the provider-cache swap hook (Task 2).

These tests are the contract Tasks 3, 4 and 8 build against: every
``classify_*`` branch, the body-text rate-limit sniff that lets a non-429
quota error (Gemini's ``RESOURCE_EXHAUSTED``) still be recognised, the
``InferenceResult.rate_limited`` convenience property, and
``reset_provider_cache()`` actually forcing a rebuild. No network, no API
key required.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.ai.base import InferenceRequest, InferenceResult, Task
from app.ai.errors import ErrorKind, classify_exception, classify_http_status, is_rate_limit_body


# ----------------------------------------------------------- classify_http_status
class TestClassifyHttpStatus:
    def test_429_is_rate_limited(self):
        assert classify_http_status(429) == ErrorKind.RATE_LIMITED

    @pytest.mark.parametrize("status", [401, 403])
    def test_401_403_is_auth(self, status):
        assert classify_http_status(status) == ErrorKind.AUTH

    @pytest.mark.parametrize("status", [500, 502, 503, 599])
    def test_5xx_is_server(self, status):
        assert classify_http_status(status) == ErrorKind.SERVER

    @pytest.mark.parametrize("status", [200, 301, 400, 404, 418])
    def test_everything_else_is_other(self, status):
        assert classify_http_status(status) == ErrorKind.OTHER


# ------------------------------------------------------------- is_rate_limit_body
class TestRateLimitBodySniff:
    @pytest.mark.parametrize(
        "body",
        [
            "RESOURCE_EXHAUSTED",
            "resource_exhausted: quota exceeded",
            "You have exceeded your QUOTA for this model",
            "Rate Limit exceeded, slow down",
            "Too Many Requests",
            "TOO MANY REQUESTS",
        ],
    )
    def test_matches_known_rate_limit_phrasings_case_insensitively(self, body):
        assert is_rate_limit_body(body) is True

    @pytest.mark.parametrize(
        "body",
        [
            "",
            "internal server error",
            "invalid api key",
            "the model is temporarily overloaded",
            "bad request: missing field 'prompt'",
        ],
    )
    def test_does_not_match_unrelated_text(self, body):
        assert is_rate_limit_body(body) is False


# -------------------------------------------------------------- classify_exception
class TestClassifyException:
    def _status_error(self, status: int, body: str = "") -> httpx.HTTPStatusError:
        request = httpx.Request("POST", "https://example.invalid/v1/chat")
        response = httpx.Response(status, request=request, text=body)
        return httpx.HTTPStatusError("boom", request=request, response=response)

    def test_http_status_error_delegates_to_classify_http_status(self):
        assert classify_exception(self._status_error(429)) == ErrorKind.RATE_LIMITED
        assert classify_exception(self._status_error(401)) == ErrorKind.AUTH
        assert classify_exception(self._status_error(500)) == ErrorKind.SERVER
        assert classify_exception(self._status_error(418)) == ErrorKind.OTHER

    def test_http_status_error_body_sniff_upgrades_non_429_to_rate_limited(self):
        """Gemini's signature failure mode: a non-429 status with a
        RESOURCE_EXHAUSTED body. The status-code classification alone would
        call this OTHER; the body sniff must override it."""
        exc = self._status_error(400, body='{"error": {"status": "RESOURCE_EXHAUSTED"}}')
        assert classify_exception(exc) == ErrorKind.RATE_LIMITED

    def test_http_status_error_403_with_non_matching_body_stays_auth(self):
        exc = self._status_error(403, body="permission denied")
        assert classify_exception(exc) == ErrorKind.AUTH  # no rate-limit text; nothing to override

    def test_http_status_error_403_with_resource_exhausted_body_upgrades_to_rate_limited(self):
        """Google documents 403 + reason rateLimitExceeded/quotaExceeded on
        some API surfaces. AUTH must be overridable: misreading a real
        quota-403 as AUTH would route the fallback ladder to the stub
        forever and never try the rate-limit fallback for this failure
        mode."""
        exc = self._status_error(
            403, body='{"error": {"status": "RESOURCE_EXHAUSTED", "message": "quota exceeded"}}'
        )
        assert classify_exception(exc) == ErrorKind.RATE_LIMITED

    def test_http_status_error_403_with_quota_exceeded_reason_upgrades_to_rate_limited(self):
        exc = self._status_error(
            403, body='{"error": {"errors": [{"reason": "quotaExceeded"}]}}'
        )
        assert classify_exception(exc) == ErrorKind.RATE_LIMITED

    def test_http_status_error_401_with_matching_body_upgrades_to_rate_limited(self):
        exc = self._status_error(401, body="Too Many Requests")
        assert classify_exception(exc) == ErrorKind.RATE_LIMITED

    def test_http_status_error_5xx_with_matching_body_stays_server(self):
        """Pins the deliberate asymmetry: SERVER is never overridden by the
        body sniff, even when the body happens to contain rate-limit-shaped
        text. A 5xx carrying incidental quota text is far more likely a
        genuine server fault than a disguised quota error, and Google has no
        documented 5xx quota analogue. Do not "fix" this to match AUTH's
        behaviour."""
        exc = self._status_error(503, body="quota exceeded, service temporarily unavailable")
        assert classify_exception(exc) == ErrorKind.SERVER

    def test_timeout_classifies_as_timeout_not_network(self):
        """The ordering trap: httpx.TimeoutException IS a httpx.TransportError
        subclass. A naive TransportError-first check would misclassify this
        as NETWORK."""
        request = httpx.Request("POST", "https://example.invalid/v1/chat")
        exc = httpx.ReadTimeout("timed out", request=request)
        assert isinstance(exc, httpx.TransportError)  # sanity: confirms the trap is real
        assert classify_exception(exc) == ErrorKind.TIMEOUT
        assert classify_exception(exc) != ErrorKind.NETWORK

    def test_connect_timeout_also_classifies_as_timeout(self):
        request = httpx.Request("POST", "https://example.invalid/v1/chat")
        exc = httpx.ConnectTimeout("connect timed out", request=request)
        assert classify_exception(exc) == ErrorKind.TIMEOUT

    def test_connect_error_classifies_as_network(self):
        request = httpx.Request("POST", "https://example.invalid/v1/chat")
        exc = httpx.ConnectError("connection refused", request=request)
        assert classify_exception(exc) == ErrorKind.NETWORK

    def test_other_transport_error_classifies_as_network(self):
        request = httpx.Request("POST", "https://example.invalid/v1/chat")
        exc = httpx.ReadError("read failed", request=request)
        assert classify_exception(exc) == ErrorKind.NETWORK

    def test_json_decode_error_classifies_as_bad_response(self):
        try:
            json.loads("{not json")
        except json.JSONDecodeError as exc:
            assert classify_exception(exc) == ErrorKind.BAD_RESPONSE
        else:
            pytest.fail("expected JSONDecodeError")

    def test_value_error_classifies_as_bad_response(self):
        assert classify_exception(ValueError("missing field")) == ErrorKind.BAD_RESPONSE

    def test_unrecognised_exception_classifies_as_other(self):
        assert classify_exception(RuntimeError("unexpected")) == ErrorKind.OTHER


# ------------------------------------------------------- InferenceResult.rate_limited
class TestInferenceResultRateLimited:
    def test_true_only_for_rate_limited_kind(self):
        result = InferenceResult(data={}, error="429", error_kind=ErrorKind.RATE_LIMITED)
        assert result.rate_limited is True

    @pytest.mark.parametrize(
        "kind",
        [ErrorKind.AUTH, ErrorKind.TIMEOUT, ErrorKind.BAD_RESPONSE, ErrorKind.NETWORK, ErrorKind.SERVER, ErrorKind.OTHER, None],
    )
    def test_false_for_every_other_kind(self, kind):
        result = InferenceResult(data={}, error="x" if kind else None, error_kind=kind)
        assert result.rate_limited is False

    def test_error_field_unaffected_by_error_kind(self):
        """error_kind is additive — the human-readable error string is
        untouched by its presence."""
        result = InferenceResult(data={}, error="boom: details", error_kind=ErrorKind.SERVER)
        assert result.error == "boom: details"
        assert result.ok is False


# --------------------------------------------------------------- reset_provider_cache
class TestProviderCache:
    def test_reset_forces_rebuild(self, monkeypatch):
        from app.ai import factory

        monkeypatch.setattr(factory.settings, "ai_provider", "stub", raising=False)
        factory.reset_provider_cache()

        first = factory.get_provider()
        second = factory.get_provider()
        assert first is second  # cached: same object without a reset

        factory.reset_provider_cache()
        third = factory.get_provider()
        assert third is not first  # rebuilt: a fresh object after reset
        assert third.name == first.name == "stub"

        factory.reset_provider_cache()  # leave a clean slate for other tests


# --------------------------------------------------------------- factory.infer()
class TestFactoryInferUnchanged:
    """Proves this task did not change any inference behaviour: a failing
    provider still falls back to the stub with an ``error`` string prefixed
    ``fell_back_from_<name>:`` — the only addition is that ``error_kind``
    now rides along on the final result."""

    class _FailingProvider:
        name = "fake"
        model = "fake-model"
        is_ai = True

        def infer(self, request: InferenceRequest) -> InferenceResult:
            return InferenceResult(
                data={},
                provider=self.name,
                model=self.model,
                is_ai=True,
                error="simulated failure",
                error_kind=ErrorKind.RATE_LIMITED,
            )

    def test_failure_falls_back_to_stub_with_prefixed_error_and_propagated_kind(self, monkeypatch):
        from app.ai import factory

        monkeypatch.setattr(factory, "get_provider", lambda: self._FailingProvider())

        request = InferenceRequest(
            task=Task.CLASSIFY,
            system="",
            user="",
            schema={},
            context={"description": "a large pothole on 5th main road"},
        )
        result = factory.infer(request)

        assert result.error is not None
        assert result.error.startswith("fell_back_from_fake: simulated failure")
        assert result.provider == "stub"  # stub actually produced the data
        assert result.data  # stub ran its real classification logic
        # Additive: the kind that caused the fallback rides along.
        assert result.error_kind == ErrorKind.RATE_LIMITED
        assert result.rate_limited is True

    def test_success_passes_through_untouched(self, monkeypatch):
        from app.ai import factory

        class _SucceedingProvider:
            name = "fake-ok"
            model = "fake-model"
            is_ai = True

            def infer(self, request: InferenceRequest) -> InferenceResult:
                return InferenceResult(
                    data={"ok": True}, provider=self.name, model=self.model, is_ai=True
                )

        monkeypatch.setattr(factory, "get_provider", lambda: _SucceedingProvider())

        request = InferenceRequest(task=Task.CLASSIFY, system="", user="", schema={})
        result = factory.infer(request)

        assert result.provider == "fake-ok"
        assert result.error is None
        assert result.error_kind is None
        assert result.ok is True
