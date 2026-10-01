# Plan: Kimi-K3 (NVIDIA NIM) provider, rate-limit-only Gemini fallback, and HTTP mock APIs

**Repo:** `/Users/krishnadevss/project/bharathseva/BarathSeva` (branch `sub1`)
**Baseline:** 127 backend tests passing, HEAD `547b2bb`, working tree dirty with the user's in-flight auth/department/geocode work.

---

## 1. Spec (the binding authority)

From the user, verbatim intent:

1. Integrate `moonshotai/kimi-k3` hosted on NVIDIA NIM (`https://build.nvidia.com/moonshotai/kimi-k3`) as an LLM provider, using its free tier.
2. Use **Gemini Flash as a fallback ONLY when the primary is rate-limited.** Not on auth errors, not on timeouts, not on malformed responses, not on network errors — rate limit only.
3. Create mock APIs.
4. Find gaps (done — three reports, summarised in §3).
5. Plan well; execute with parallel subagents.

### Verified facts about the target endpoint

| Property | Value |
|---|---|
| Base URL | `https://integrate.api.nvidia.com/v1` |
| Endpoint | `POST /chat/completions` |
| Wire format | OpenAI-compatible |
| Auth | `Authorization: Bearer $NVIDIA_API_KEY` |
| Model id | `moonshotai/kimi-k3` |
| Context | 1,048,576 tokens |
| Vision | Yes (image input supported) |
| Structured output | Yes |

### Non-negotiable behaviour (the fallback ladder)

```
                    ┌─────────────────────────┐
  inference  ──────▶│  NvidiaProvider (kimi)  │
  request           └───────────┬─────────────┘
                                │
              ┌─────────────────┼──────────────────┐
              │                 │                  │
         ok   │      429 /      │     any other    │
              │   quota exceeded│     failure      │
              ▼                 ▼                  ▼
          ┌───────┐     ┌───────────────┐    ┌──────────────┐
          │ return│     │ Gemini Flash  │    │ StubProvider │
          └───────┘     └───────┬───────┘    │ (determinist)│
                                │            └──────────────┘
                   ┌────────────┼────────────┐
                   │ ok         │ ANY failure│
                   ▼            ▼            │
               ┌───────┐  ┌──────────────┐   │
               │ return│  │ StubProvider │◀──┘
               └───────┘  └──────────────┘
```

Plus a **cooldown**: once the primary returns a rate limit, the chain skips the
primary entirely for `ai_rate_limit_cooldown_seconds` and goes straight to
Gemini. This is the difference between "handles a 429" and "does not hammer a
free-tier endpoint that just told us to stop". When the cooldown expires the
primary is tried again.

---

## 2. Global Constraints

These bind every task. A reviewer should treat a violation as a defect.

- **GC1 — No new runtime dependency.** `httpx==0.28.1` is already pinned in
  `requirements.txt` and installed in `backend/.venv`. `openai` and
  `google-genai` are **not installed** and `requirements-ai.txt` is not wired
  into `make setup`. Every provider added or rewritten here talks HTTP via
  `httpx`. No new entries in `requirements.txt`, and no test-only HTTP-mock
  library (use `httpx.MockTransport`, which ships with httpx).
- **GC2 — The stub must stay the default and must never stop working.**
  `BARATHSEVA_AI_PROVIDER` defaults to `stub`; the platform runs with zero API
  keys. The README promises this. All 127 existing tests must still pass.
- **GC3 — `/health` is unauthenticated.** (Confirmed by two independent
  analyses.) The public provider surface may expose **only**: provider name,
  model id, `is_real_model`, and a coarse state of exactly
  `active | cooled_down | degraded | not_configured`. It must NOT expose API
  keys, key-presence booleans, base URLs, quota numbers, retry counts, or raw
  provider error strings. Anything more diagnostic goes behind the existing
  admin gate or to logs only.
- **GC4 — Intake must never block on a third party.** Every outbound provider
  call carries an explicit connect+read timeout from settings. No unbounded
  call on the synchronous intake path.
- **GC5 — Mock routes are gated.** All `/mock/*` routes are registered only
  when `enable_mock_apis` is true, and that setting must resolve to false when
  `environment == "production"` regardless of how it is set.
- **GC6 — Do not touch the user's in-flight work.** Out of scope, hands off:
  `frontend/**`, `backend/app/api/auth.py`, `backend/app/core/auth.py`,
  `backend/app/api/department.py`, `backend/app/api/geocode.py`,
  `backend/tests/test_auth.py`. A second Claude session has also reported two
  defects we are deliberately NOT fixing here: the `/api/admin/stream`
  EventSource-cannot-send-Authorization bug, and `deps.require_admin` being
  dead code. Leave both alone.
- **GC7 — Tests must not depend on network or keys.** Every test added here
  passes with no API key set and no internet. Use `httpx.MockTransport` or the
  in-app mock LLM endpoint.
- **GC8 — Tests must not assert absolute complaint counts** or the max
  reference number. The shared dev database contains leftover rows
  (`BRS-000037`–`BRS-000042`) from another session's run.
- **GC9 — Exact setting names.** Every setting name in §4 Task 1 is normative.
  Later tasks must use those names verbatim; no task may invent a variant.

---

## 3. Gap analysis findings (what we are building on)

Full reports: `scratchpad/gaps/{ai-layer,integrations,config-tests}.md`.

### Blocking gaps this plan must fix

| # | Gap | Evidence | Fixed by |
|---|---|---|---|
| G1 | `InferenceResult.error` is a free-form string with **no rate-limit discriminator**. `except Exception` discards the distinguishable information. The spec's core requirement is literally inexpressible today. | `app/ai/base.py:48`, `app/ai/openai_provider.py:62`, `app/ai/gemini_provider.py:50` | Task 2 |
| G2 | `factory.infer()` hardcodes one provider + stub. No chain concept. | `app/ai/factory.py:62-82` | Task 8 |
| G3 | `@lru_cache` on zero-arg `get_provider()` cannot hold mutable cooldown state and cannot be swapped in a test. No reset hook. | `app/ai/factory.py:57-60` | Task 2 |
| G4 | `OpenAIProvider` has no `base_url` parameter, so the one class suited to speak OpenAI-wire **cannot point at NVIDIA NIM**. | `app/ai/openai_provider.py:20-22` | Task 3 |
| G5 | No settings surface for a primary + conditional-fallback pair. | `app/config.py` ai block | Task 1 |
| G6 | `mock_gov.py` is invoked **only in-process**, from exactly one caller. No HTTP path exists, so "mock APIs" has no wire surface today. | `app/integrations/mock_gov.py`, `app/agents/dispatcher.py` | Tasks 7, 9 |
| G7 | `gateway.failing_departments` is **never populated anywhere** — simulated department failure is unreachable without editing Python. | grep of app + tests | Tasks 7, 9 |
| G8 | **No ticket-status callback exists.** A department can never report progress back; status is only ever advanced by our own UI. | `app/api/department.py`, `app/api/admin.py` | Task 11 |
| G9 | Neither `openai` nor `google-genai` is installed; `requirements-ai.txt` is in no Makefile target. The existing `GeminiProvider` would fail at import **today**. | `.venv` inspection | GC1 + Task 4 |
| G10 | Zero validation that a configured provider has its key, or that `secret_key`/`admin_api_key` have left their insecure defaults outside development. | `app/config.py` | Task 1 |

### Adjacent defects this plan fixes (because this feature activates them)

| # | Defect | Why it is in scope |
|---|---|---|
| D1 | `bool(result.data.get("is_civic_issue", False))` — a model returning the JSON **string** `"false"` yields `True`, inverting the verdict. `bool("false") is True`. | `app/agents/verifier.py:64-65`. Latent only because the stub returns real bools. Switching to Kimi is exactly what activates it. Task 5. |
| D2 | No response-schema validation anywhere; every call site coerces ad hoc. | Same root cause as D1. Task 5. |
| D3 | **No timeout on any provider HTTP call**, on the synchronous intake path. | A hung free-tier endpoint hangs citizen intake. GC4, Tasks 3/4. |
| D4 | No size/format guard before base64-ing an image into a request (up to ~16MB, sent twice per complaint). | Directly worsens the rate-limit problem we are solving. Task 3. |
| D5 | "How often did we fall back?" is answerable only by a brittle `LIKE 'fell_back_from_%'` string match. | We are adding a fallback path; it needs to be observable. Task 10. |
| D6 | `requirements-ai.txt` comment says `JANSEVA_AI_PROVIDER` (stale rename). | One-line docs fix. Task 1. |

### Explicitly out of scope (recorded, not fixed)

- `/api/admin/stream` + `EventSource` 401 — reported by the peer session; needs a query-param token design. Not ours.
- `deps.require_admin` dead code while `/health` reports `admin_key_enforced` as if meaningful.
- `BARATHSEVA_DEMO_PASSWORD` read from raw `os.environ` instead of `Settings`.
- Unmapped complaint categories silently defaulting to BBMP.

---

## 4. Tasks

Dependencies are explicit. Tasks in the same **wave** touch disjoint files and
run in parallel.

### Wave 0

#### Task 1 — Settings and environment documentation
**Files:** `backend/app/config.py`, `backend/.env.example`, `backend/requirements-ai.txt`
**Depends on:** nothing. **Runs alone** (every later task reads these names).

Add to the `# --- ai ---` block of `Settings`. These names are normative (GC9):

```python
ai_provider: str = "stub"  # stub | openai | gemini | nvidia
# --- NVIDIA NIM (Kimi K3) ---
nvidia_api_key: Optional[str] = None
nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
nvidia_model: str = "moonshotai/kimi-k3"
# --- fallback policy ---
#: Provider used ONLY when the primary reports a rate limit. Empty disables.
ai_rate_limit_fallback: str = "gemini"
#: After a rate limit, skip the primary for this long and use the fallback.
ai_rate_limit_cooldown_seconds: int = 120
#: Connect+read timeout for every provider HTTP call. Intake is synchronous,
#: so an unbounded call would hang a citizen's submission.
ai_request_timeout_seconds: float = 20.0
#: Largest image sent to a vision model. Larger images are downscaled.
ai_max_image_bytes: int = 3_000_000
# --- mock surfaces ---
enable_mock_apis: bool = True
gov_gateway_mode: str = "inprocess"  # inprocess | http
mock_gov_base_url: str = "http://localhost:8000/mock/gov"
gov_callback_secret: Optional[str] = None
```

Also:
- Keep `gemini_model: str = "gemini-2.0-flash"` as-is — it is already Flash,
  which is what the spec asks for as the fallback.
- Add a `mock_apis_enabled` **property** that returns
  `self.enable_mock_apis and self.environment != "production"` (GC5).
- Add a `validate_runtime()` method (or module function) that returns a list of
  human-readable warnings: a configured provider missing its key; a
  `ai_rate_limit_fallback` naming a provider with no key; `secret_key` or
  `admin_api_key` still at the insecure default while
  `environment != "development"`. Call it once at startup in
  `app/main.py` and log each warning at WARNING. It must **not** raise —
  degradation over refusal to boot (G10).
- Document every new key in `.env.example`, commented out, following the
  existing style. Update the stale comment at `.env.example:23-24` to mention
  `nvidia`.
- Fix the `JANSEVA_AI_PROVIDER` typo in `requirements-ai.txt` → `BARATHSEVA_`
  (D6), and note in that file that the nvidia and gemini providers need **no**
  SDK (httpx only).

**Tests:** settings defaults load; `mock_apis_enabled` is false when
`environment="production"` even with `enable_mock_apis=True`;
`validate_runtime()` warns for a keyless configured provider and for default
secrets outside development, and returns empty for a clean dev config.

**Done when:** `.env.example` documents every new field, and the 127 existing
tests still pass.

---

### Wave 1 (parallel: Tasks 2 and 6 — disjoint files)

#### Task 2 — Provider contract: error taxonomy and a swappable cache
**Files:** `backend/app/ai/base.py`, `backend/app/ai/errors.py` (new), `backend/app/ai/factory.py`
**Depends on:** Task 1.

Fixes G1 and G3 — the two gaps that make the spec inexpressible.

1. In `app/ai/errors.py`, add a string-valued `ErrorKind`:
   `RATE_LIMITED`, `AUTH`, `TIMEOUT`, `BAD_RESPONSE`, `NETWORK`, `SERVER`, `OTHER`.
2. Add `classify_http_status(status: int) -> str`: `429 → RATE_LIMITED`,
   `401/403 → AUTH`, `5xx → SERVER`, everything else `OTHER`.
3. Add `classify_exception(exc: Exception) -> str` handling the httpx families:
   `httpx.TimeoutException → TIMEOUT`,
   `httpx.TransportError`/`ConnectError` → `NETWORK`,
   `json.JSONDecodeError`/`ValueError` → `BAD_RESPONSE`, else `OTHER`.
   It must also treat an `httpx.HTTPStatusError` by delegating to
   `classify_http_status`.
4. Add a **body-text** rate-limit sniff for providers that signal quota
   exhaustion with a non-429 status: if the response body matches
   `resource_exhausted|quota|rate limit|too many requests` case-insensitively,
   classify `RATE_LIMITED`. Gemini's REST API in particular returns
   `RESOURCE_EXHAUSTED`.
5. In `base.py`, add `error_kind: Optional[str] = None` to `InferenceResult`.
   **Keep `error` exactly as-is** — the human-readable string stays, the new
   field is additive. Add a convenience property `rate_limited` returning
   `self.error_kind == ErrorKind.RATE_LIMITED`.
6. In `factory.py`, replace the bare `@lru_cache` with an explicit
   module-level cached singleton plus `reset_provider_cache()`. Behaviour of
   `get_provider()` is unchanged for callers; the reset hook exists so tests
   and the chain's cooldown state are controllable.

**Constraint:** this task changes **no** inference behaviour. The ladder arrives
in Task 8. A reviewer should verify `factory.infer()` still does exactly what
it did, only now propagating `error_kind`.

**Tests:** every `classify_*` branch including the body-text sniff;
`InferenceResult.rate_limited`; `reset_provider_cache()` causes the next
`get_provider()` to rebuild; existing stub behaviour unchanged.

#### Task 6 — Mock LLM endpoint (OpenAI-compatible, with failure injection)
**Files:** `backend/app/api/mock_llm.py` (new), `backend/app/main.py` (registration only)
**Depends on:** Task 1.

This is what makes the whole feature testable with **no API key** — which
matters, because the machine has no `.env` and no key in the environment. It is
also how we prove the 429 → Gemini path without burning a real quota.

- `POST /mock/llm/v1/chat/completions` — accepts and returns the OpenAI
  chat-completions shape, so `nvidia_base_url` can be pointed at
  `http://localhost:8000/mock/llm/v1` and the real provider code path runs
  unmodified.
- Infer the task from the request (the prompts name it) and return a
  deterministic, schema-valid JSON object in
  `choices[0].message.content`, consistent with what `StubProvider` would
  decide. Reuse `app/ai/taxonomy.py` vocabulary; do not invent categories.
- Accept image parts without choking (just acknowledge them).
- **Failure injection**, driven by a request header so a test needs no shared
  state: `X-Mock-Failure: rate_limit | auth | server | timeout | garbage`
  → respectively a real `429` with a quota-shaped body, `401`, `500`, a
  delay exceeding the configured timeout, and a `200` carrying unparseable
  content. Also support `X-Mock-Failure-Count: N` to fail only the first N
  calls.
- Also expose `POST /mock/llm/v1/...` for a Gemini-shaped endpoint
  (`/mock/llm/gemini/v1beta/models/{model}:generateContent`) so Task 4's REST
  provider is testable the same way.
- Register in `main.py` **only** when `settings.mock_apis_enabled` (GC5).

**Tests:** happy path returns OpenAI-shaped, schema-valid JSON; each
`X-Mock-Failure` value produces the documented status/body; `X-Mock-Failure-Count`
fails exactly N times then succeeds; routes are absent when
`environment="production"`.

---

### Wave 2 (parallel: Tasks 3, 4, 5, 7 — disjoint files)

#### Task 3 — `NvidiaProvider`: Kimi K3 over httpx
**Files:** `backend/app/ai/nvidia_provider.py` (new), `backend/app/ai/openai_provider.py`
**Depends on:** Tasks 1, 2. Testable against Task 6 but must not require it.

Fixes G4, D3, D4.

- `class NvidiaProvider` with `name = "nvidia"`, `model` from
  `settings.nvidia_model`, `is_ai = True`.
- Constructor takes `api_key`, `model`, `base_url`, `timeout`, and an optional
  `transport` parameter so tests can inject `httpx.MockTransport` (GC7).
- `POST {base_url}/chat/completions` with `Authorization: Bearer <key>`,
  `response_format={"type": "json_object"}`, `temperature=0.2`,
  `max_tokens=600` — mirroring the existing `OpenAIProvider` request shape so
  prompts carry over unchanged (the gap report confirms prompts are already
  provider-agnostic).
- Vision: base64 data URI in an `image_url` content part, as
  `OpenAIProvider` already does. **Before encoding**, if
  `len(image_bytes) > settings.ai_max_image_bytes`, downscale with Pillow
  (already a dependency) and re-encode as JPEG; if Pillow fails, drop the image
  and proceed text-only rather than failing the inference (D4).
- Explicit `httpx.Timeout` from `settings.ai_request_timeout_seconds` (GC4).
- On any failure, return an `InferenceResult` with **both** `error` (the
  existing human string) and the new `error_kind` from Task 2's classifiers.
  A real `429` from NIM must yield `error_kind == RATE_LIMITED`. This is the
  single most important assertion in the task.
- Register `"nvidia"` (and accept `"kimi"` as an alias) in `_build_provider()`,
  following the existing keyless-fallback-to-stub pattern.
- Small, separate change: add an optional `base_url` to `OpenAIProvider` and
  populate `error_kind` there too, for consistency (G4).

**Tests** (all with `MockTransport`, no network): happy path parses content JSON
into `data` and sets `is_ai=True`; `429` → `RATE_LIMITED`; `401` →
`AUTH`; `500` → `SERVER`; timeout → `TIMEOUT`; non-JSON content →
`BAD_RESPONSE`; oversized image is downscaled and the request still sends;
request carries the bearer header and the exact model id `moonshotai/kimi-k3`.

#### Task 4 — Rewrite `GeminiProvider` on httpx REST
**Files:** `backend/app/ai/gemini_provider.py`
**Depends on:** Tasks 1, 2.

Fixes G9 and D3. Rationale for the rewrite (a ruling, see §6): the
SDK is **not installed**, this file has **zero test coverage**, and it would
fail at import today. Going to REST satisfies GC1, removes the dependency risk
entirely, and — decisively — makes rate-limit detection a plain HTTP status
code instead of an SDK-specific exception type, which is exactly what the
spec's one hard requirement needs.

- Keep the class name `GeminiProvider` and `name = "gemini"` so
  `factory.py`, `.env.example`, and the README stay accurate.
- `POST {base}/v1beta/models/{model}:generateContent?key=<api_key>` with
  `generationConfig: {"response_mime_type": "application/json",
  "temperature": 0.2}`, base URL from a new
  `gemini_base_url: str = "https://generativelanguage.googleapis.com"` setting
  (add it in this task; it is the one addition to Task 1's list, and it belongs
  here because only this provider reads it).
- Inline image as `inline_data: {mime_type, data: <base64>}`, with the same
  size guard as Task 3 (share the helper — put it in `app/ai/images.py` and
  have Task 3's reviewer check for duplication).
- Timeout from settings; `transport` injection for tests.
- Populate `error_kind`. Gemini signals quota exhaustion as `429` **and**
  sometimes as a `RESOURCE_EXHAUSTED` body — Task 2's body-text sniff must
  catch both. Verify both in tests.
- Drop `google-genai` from `requirements-ai.txt` (leave `openai`, which the
  untouched `OpenAIProvider` still uses).

**Tests:** happy path; `429` → `RATE_LIMITED`; a `200`/`400` carrying
`RESOURCE_EXHAUSTED` → `RATE_LIMITED`; `401` → `AUTH`; timeout → `TIMEOUT`;
image inlined correctly; no `google` import anywhere in the module.

#### Task 5 — Strict output coercion (fixes the `bool("false")` inversion)
**Files:** `backend/app/ai/coerce.py` (new), `backend/app/agents/verifier.py`, `backend/app/agents/classifier.py`, and any other agent reading `result.data`
**Depends on:** Task 2.

Fixes D1 and D2. **This is a correctness fix, not a nicety** — today a model
returning `"is_civic_issue": "false"` gets `bool("false") == True` and a
non-civic report is marked valid. The stub returns real booleans, which is the
only reason this has never fired. Task 3 is what makes it fire.

- `as_bool(value, default)`: real bools pass through; strings are matched
  case-insensitively against `{"true","yes","1"}` / `{"false","no","0"}`;
  numbers use `!= 0`; anything unrecognised returns `default`. Never
  `bool(str)`.
- `as_float(value, default, lo, hi)`: parses numeric strings, clamps to range,
  returns `default` on failure. Use it for `confidence` so a model returning
  `"0.9"` or `1.7` behaves.
- `as_enum(value, allowed, default)`: upper-cases and checks membership against
  `app/ai/taxonomy.py` vocabulary, so a hallucinated category degrades to
  `OTHER` rather than reaching the database.
- `validate_against_schema(data, schema) -> (coerced, list[str])`: returns
  coerced data plus the names of missing or wrong-typed fields, for logging.
- Replace every ad-hoc coercion at the call sites with these helpers. Start
  with `verifier.py:64-65` and `classifier.py`.

**Tests:** `as_bool("false") is False` — the regression test for D1 — plus
`"False"`, `"no"`, `"0"`, `0`, `""`, `None`, `"garbage"`, real bools;
`as_float` clamping and string parsing; `as_enum` rejecting a hallucinated
category; a verifier-level test proving a model returning the string `"false"`
yields `valid=False`.

#### Task 7 — Mock government HTTP APIs
**Files:** `backend/app/api/mock_gov_api.py` (new), `backend/app/main.py` (registration only)
**Depends on:** Task 1. **Must not** modify `app/integrations/mock_gov.py` or `dispatcher.py` — that is Task 9.

Fixes G7 and gives G6 its wire surface.

Expose the three existing department dialects over HTTP, preserving their
deliberately heterogeneous response shapes (that heterogeneity is the point —
it exercises the normalising adapter):

- `POST /mock/gov/bbmp/grievance` → `{status, grievance_id, zone_office, received_on, echo}`
- `POST /mock/gov/bwssb/complaint` → `{result, complaint_no, subdivision, logged_at, echo}`
- `POST /mock/gov/bescom/ticket` → `{code, ticket_ref, section_office, timestamp, echo}`

Reuse `_deterministic_suffix` from `mock_gov.py` (import it; do not copy it) so
HTTP and in-process modes produce **identical** ticket ids for the same
reference. A reviewer should check this explicitly.

Add what a real agency API does and the current fake does not:
- **Idempotency** on `source_reference`: a repeat POST returns the original
  ticket id and `200`, not a second ticket. Keep the store in-process.
- **Failure injection reachable without editing Python** (G7): header
  `X-Mock-Failure: unavailable | slow | malformed | rate_limit`, and a control
  endpoint `POST /mock/gov/_control` taking
  `{"failing_departments": ["BWSSB"], "latency_ms": 0}` with
  `GET /mock/gov/_control` to read it back. This is the first time a
  simulated department outage is triggerable from outside the code.
- `GET /mock/gov/{dept}/ticket/{id}` to read a ticket's mock status back.

Register only when `settings.mock_apis_enabled` (GC5).

**Tests:** each dialect returns its documented shape; the same `reference`
yields the same ticket id as `MockGovernmentGateway` produces in-process
(assert equality against the real function); idempotent repeat POST;
`_control`-configured failure makes that department fail and others succeed;
each `X-Mock-Failure` value behaves; routes absent in production.

---

### Wave 3 (parallel: Tasks 8 and 9)

#### Task 8 — `ChainProvider`: the rate-limit-only ladder
**Files:** `backend/app/ai/chain.py` (new), `backend/app/ai/factory.py`
**Depends on:** Tasks 2, 3, 4. **This is the task that implements the spec.**

Fixes G2. `ChainProvider` itself satisfies the `AIProvider` protocol, so the
narrow contract in `base.py` is preserved.

```python
ChainProvider(primary, rate_limit_fallback, final_fallback, cooldown_seconds)
```

Behaviour, exactly:
1. If the primary is in cooldown, skip it and call the rate-limit fallback.
2. Otherwise call the primary. If `ok`, return it.
3. If `result.error_kind == RATE_LIMITED`: start the cooldown, then call the
   rate-limit fallback. If the fallback is `ok`, return it, annotated so the
   caller can tell a fallback served the request. If the fallback fails **for
   any reason**, fall through to the final fallback (stub).
4. If the primary failed for **any other reason**: go straight to the final
   fallback. **Gemini is not consulted.** This is the spec's one hard
   requirement and the single most important assertion in the plan.
5. The stub is the floor and must never raise.

Details:
- Cooldown state is mutable and shared; guard it with a `threading.Lock`
  (Celery workers and FastAPI threads both touch it). Expose
  `cooldown_remaining()` and a `reset()` for tests.
- Preserve provenance on the returned result: set `provider` to the provider
  that actually served it, and prefix `error` with the existing
  `fell_back_from_<name>:` convention so nothing that parses it breaks.
- In `_build_provider()`, construct the chain when `ai_provider` resolves to a
  model provider **and** `ai_rate_limit_fallback` names a different, usable
  provider. If the fallback is unset or keyless, build the plain provider with
  the stub floor — i.e. exactly today's behaviour.
- Keep `factory.infer()`'s public signature unchanged.

**Tests** (the heart of the suite; fakes for all three providers, no network):
- primary ok → primary served, Gemini never called (assert call count 0)
- primary `429` → Gemini called once, Gemini's result returned
- primary `500` → **Gemini never called**, stub served
- primary `AUTH` → Gemini never called, stub served
- primary `TIMEOUT` → Gemini never called, stub served
- primary `BAD_RESPONSE` → Gemini never called, stub served
- primary `429` then Gemini `429` → stub served
- primary `429` then Gemini `500` → stub served
- after a `429`, the next call within the cooldown skips the primary entirely
  (primary call count does not increase)
- after the cooldown expires (inject the clock; do not sleep), the primary is
  tried again
- no fallback configured → `429` falls to the stub
- the chain satisfies `isinstance(chain, AIProvider)`

#### Task 9 — `HttpGovernmentGateway` and gateway selection
**Files:** `backend/app/integrations/http_gov.py` (new), `backend/app/integrations/mock_gov.py`, `backend/app/agents/dispatcher.py`
**Depends on:** Tasks 1, 7.

Fixes G6 — makes the mock APIs a real wire path instead of a disconnected demo
surface. This is the decision the integrations report flagged as needing a
ruling; see §6.

- `HttpGovernmentGateway` with the **same** `create_ticket(...)` signature and
  the same `TicketResponse` return type as `MockGovernmentGateway`, so
  `dispatcher.py` is agnostic. Talks to `settings.mock_gov_base_url` via httpx
  with the settings timeout; maps each department to its route and normalises
  the three dialects using the same `grievance_id | complaint_no | ticket_ref`
  precedence the in-process gateway already uses.
- A network failure, timeout, or non-2xx returns
  `TicketResponse(ok=False, error=...)` — never raises. The dispatcher's
  existing failure handling must keep working unchanged.
- Add `get_gateway()` selecting on `settings.gov_gateway_mode`
  (`inprocess` default | `http`), and have `dispatcher.py` call it instead of
  importing the module-level `gateway` singleton. **Default stays
  `inprocess`** so all 127 tests and the verified end-to-end path are
  untouched (GC2).

**Tests:** `http` mode creates a ticket through the real app via TestClient and
returns a normalised `TicketResponse`; ids match in-process mode for the same
reference; a `_control`-injected department outage surfaces as `ok=False` with
the dispatcher still completing; mode defaults to `inprocess`; both gateways
satisfy the same structural interface.

---

### Wave 4 (parallel: Tasks 10 and 11)

#### Task 10 — Observability without leaking configuration
**Files:** `backend/app/ai/metrics.py` (new), `backend/app/api/health.py`, `backend/app/api/admin.py`
**Depends on:** Task 8.

Fixes D5. **GC3 is the binding constraint here** — `/health` is public.

- In-process counter registry: calls, successes, and failures keyed by
  `(provider, error_kind)`, plus fallback-served and cooldown-entered counts.
  Lock-guarded, with `snapshot()` and `reset()`. **No DB migration** — this
  project has no migration tooling and adding columns is out of scope.
- `factory.infer()` / `ChainProvider` record into it.
- `/health`'s `ai` block gains **only** `state`, one of
  `active | cooled_down | degraded | not_configured`, alongside the existing
  `provider`/`model`/`is_real_model`. Explicitly NOT: keys, key-presence,
  base URLs, quota numbers, or raw error strings.
- Full detail — per-provider counters, `error_kind` breakdown, cooldown
  seconds remaining — goes on a **new admin-gated** endpoint
  `GET /api/admin/ai-status`, behind the same dependency the rest of
  `/api/admin/*` already uses. Do not weaken that gate, and do not touch the
  `/api/admin/stream` route (GC6).

**Tests:** counters increment correctly across the ladder; `/health` exposes
the four allowed fields and a test asserts the response body contains **no**
API key, no base URL, and no raw provider error text; `/api/admin/ai-status`
requires auth and returns the detail; cooldown is reflected as `cooled_down`.

#### Task 11 — Department status callback webhook
**Files:** `backend/app/api/gov_callback.py` (new), `backend/app/api/mock_gov_api.py`, `backend/app/main.py`
**Depends on:** Tasks 7, 9.

Fixes G8 — today a department can never report progress back, so the mock APIs
we just built would have no way to close the loop.

**Security framing — read this first.** A second analysis confirmed that today
there are exactly 15 status-transition sites, and **every one** is either our
own in-process pipeline or a role-gated staff endpoint:

- pipeline agents: `classifier.py:106`, `geocluster.py:97`,
  `verifier.py:101/118/130`, `dispatcher.py:104/122`, `sla_monitor.py:125`,
  `resolution.py:83`, `workflow/graph.py:98`, `services/intake.py:236`
- authenticated staff: `api/department.py:250` (acknowledge → IN_PROGRESS),
  `api/department.py:341` (reassign → DISPATCHED), `api/admin.py:332/337`
  (super-admin review → VERIFIED/REJECTED)

There is **no inbound path at all**. The only existing inbound webhook,
`POST /api/telegram/webhook` (`api/telegram.py:59`), is citizen intake and never
advances an existing complaint. So this task opens a **net-new trust boundary
into status mutation**, and it would instantly be the weakest link in the
system: an unauthenticated callback that can move a ticket to `RESOLVED` is
strictly worse than anything currently reachable. Treat the auth here as the
primary requirement and the feature as secondary.

Also note the lookup gap: `dispatcher.py:102` is the **only** writer of
`complaint.external_ticket_id`, and nothing ever reads an inbound ticket id to
find a complaint. The column is indexed (`models.py:238`) and
`/api/department/complaints?search=` matches it, but no route resolves it. This
task must add that lookup; without it the callback has no consumer.

- `POST /api/integrations/gov-callback` accepting
  `{department_code, ticket_id, source_reference, status, note, occurred_at}`.
- Resolve the complaint by `external_ticket_id` (the indexed column), and
  require that the resolved complaint's **department matches
  `department_code`** — a BWSSB callback must not be able to move a BBMP
  ticket. Return `404` when unresolved, and do not leak whether the ticket
  exists under a different department.
- Authenticated by an HMAC-SHA256 signature over the **raw request body** in an
  `X-Gov-Signature` header, keyed by `settings.gov_callback_secret`, compared
  with `hmac.compare_digest` (constant time — not `==`). Reject with `401` on
  mismatch and **`503` when the secret is unset** — fail closed, never open.
  Include `occurred_at` in the signed payload and reject a timestamp outside a
  ±5 minute window, so a captured callback cannot be replayed indefinitely.
- **Transition guard:** define an explicit allow-list of transitions a
  department may cause — `DISPATCHED → IN_PROGRESS`, `IN_PROGRESS → RESOLVED`,
  `DISPATCHED → RESOLVED`, and a department-rejection back to
  `PENDING_REVIEW`. Everything else is `409`. A department must **never** be
  able to reach `VERIFIED` or `REJECTED` (those are super-admin decisions at
  `admin.py:332/337`) or re-open a resolved complaint. Reuse the existing
  transition logic rather than writing a second copy — a reviewer should check
  it does not duplicate `department.py`'s rules.
- Map the department's status vocabulary onto our status enum via
  `app/core/enums.py`; an unmappable status is `422`, never a junk write.
- Idempotent: the same `(ticket_id, status)` twice is a no-op `200`.
- Write an audit record for every accepted callback, consistent with how the
  existing staff transitions are recorded, so an externally-caused status
  change is distinguishable from a staff one.
- Add `POST /mock/gov/{dept}/advance` to the mock API so a demo can drive a
  real signed callback into our app and watch a complaint progress.

**Tests:** valid signature advances status; bad signature `401`; unset secret
`503`; signature compared in constant time; stale `occurred_at` rejected;
replayed body rejected; unmappable status `422`; disallowed transition `409`;
a department callback cannot reach `VERIFIED` or `REJECTED`; a BWSSB callback
cannot move a BBMP ticket (`404`); unknown ticket id `404`; replay of the same
`(ticket_id, status)` is a no-op; audit record written; the mock `advance`
endpoint drives a complaint from dispatched to resolved end to end.

---

### Wave 5

#### Task 12 — Keyless end-to-end verification and documentation
**Files:** `backend/tests/test_ai_chain_e2e.py` (new), `README.md`, `Makefile`
**Depends on:** all previous.

The machine has **no API key and no `.env`**, so this is how we prove the
feature actually works rather than asserting it does.

- End-to-end test: set `ai_provider=nvidia` with
  `nvidia_base_url` pointed at the in-app mock LLM and a dummy key, then run a
  full complaint intake and assert the pipeline completes and Kimi's path was
  used. Then, using `X-Mock-Failure: rate_limit`, assert the same intake
  completes **via Gemini** (also pointed at the mock). Then, with
  `X-Mock-Failure: server`, assert it completes **via the stub**. One test
  file that demonstrates the whole spec with zero keys and zero network.
- `make ai-test` target running the AI suite; `make setup-ai` installing
  `requirements-ai.txt` (which, after Task 4, is only needed for the OpenAI
  provider).
- README: document the `nvidia` provider, the model id, how to get a free
  NVIDIA NIM key, the rate-limit-only fallback ladder with its cooldown, the
  `/mock/*` surfaces and how to drive failure injection, and the `/health` vs
  `/api/admin/ai-status` split. Update the provider list wherever `openai|gemini`
  is currently enumerated.

**Done when:** the full suite passes (127 existing + everything added) with no
API key set and no network access.

---

## 5. Execution notes

- **Waves:** 0 → {2,6} → {3,4,5,7} → {8,9} → {10,11} → 12.
- `app/main.py` is touched by Tasks 6, 7, and 11 (router registration only).
  Those are in different waves except 6 and 7 — assign both `main.py` edits to
  Task 7's implementer and have Task 6 only create its router, to avoid a
  same-wave collision on one file.
- `app/ai/factory.py` is touched by Tasks 2, 3, and 8 — all in different waves.
  Good.
- Model tiers: Task 1 and Task 5 are cheap-tier (mechanical, fully specified).
  Tasks 2, 6, 7, 9, 10, 11 are standard. Tasks 3, 4, 8 and 12 are standard-to-
  capable; **Task 8 carries the spec's core requirement** and should get the
  most capable implementer and the most careful review.

## 6. Rulings made while planning

1. **`httpx` everywhere, no SDKs.** Driven by evidence, not taste: `openai` and
   `google-genai` are not installed, `requirements-ai.txt` is in no Makefile
   target, and `httpx==0.28.1` already is installed. It also makes 429
   detection an HTTP status code rather than an SDK exception type, which is
   what the spec needs. *Cost if wrong:* we hand-roll two small request
   bodies instead of using vendor clients, and must track API shape changes
   ourselves.
2. **Rewrite `GeminiProvider` to REST rather than adding a parallel class.**
   Zero test coverage today and un-importable as installed, so the blast
   radius is nil, and two Gemini providers would be worse. *Cost if wrong:*
   anyone depending on the SDK path loses it; mitigated by keeping the class
   name, provider name, and settings keys identical.
3. **Cooldown after a rate limit**, not just per-call fallback. "Free tier"
   plus "retry the primary on every request after it said 429" is how a quota
   stays exhausted. *Cost if wrong:* up to `cooldown_seconds` of Gemini use
   after a single spurious 429; tunable, default 120s.
4. **Gemini is consulted on rate-limit only; every other failure goes to the
   stub.** This is the literal reading of the request and preserves the
   existing never-fail-intake guarantee. *Cost if wrong:* if the user actually
   wanted Gemini as a general-purpose fallback, Task 8's step 4 is a one-line
   change.
5. **"Mock APIs" means both** HTTP mock government department APIs and a mock
   OpenAI-compatible LLM endpoint. The government mocks are the natural reading
   of an existing in-process fake with no wire surface; the LLM mock is what
   makes the feature verifiable on a machine with no API key. *Cost if wrong:*
   one of the two surfaces is unwanted work; both are additive and gated off in
   production.
6. **`gov_gateway_mode` defaults to `inprocess`.** The integrations report
   asked whether `/mock/*` should be a disconnected demo surface or whether
   `dispatcher.py` should be refactored to call it. Answer: build the HTTP path
   and make it selectable, but leave the default alone so the verified
   end-to-end behaviour and all 127 tests are untouched. *Cost if wrong:* the
   HTTP path is opt-in and could rot untested — mitigated by Task 9 testing it
   explicitly.
7. **No DB schema change for observability.** The project has no migration
   tooling; in-process counters plus an admin endpoint answer the question
   without one. *Cost if wrong:* counters reset on restart and are per-process,
   so they are not a long-term analytics answer.
8. **The `bool("false")` fix (D1) is in scope** even though it predates this
   work, because switching from the stub to a real model is precisely what
   activates it. *Cost if wrong:* a little scope creep in Task 5.
