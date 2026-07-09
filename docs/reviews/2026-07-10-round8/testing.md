# Round 8 Final Testing Review: v0.5 AMEND rc3

> Review date: 2026-07-10
> Reviewer: general-purpose-9 (testing)
> Scope: D0–D4 testability audit + existing test pattern compatibility
> Reference files:
>   - Plan: `docs/plans/ocr-engine-unification.v0.5-AMEND.md`
>   - Existing tests: `tests/test_ratelimit.py`, `tests/test_vlm.py`, `tests/test_atomic.py`, `tests/test_leakage.py`

---

## 1. Executive Summary

**Verdict: CONDITIONALLY PASS — 7 issues to resolve before implementation**

The plan is **broadly testable** and the existing test infrastructure (`unittest.mock` + `tmp_path` + parameterized patterns) provides good scaffolding. However, 7 specific gaps must be resolved before implementation starts — 2 blocking (must fix), 5 recommended (fix or document).

| Metric | Status |
|--------|--------|
| Test architecture alignment | ✅ Patterns from `test_vlm.py` / `test_atomic.py` / `test_ratelimit.py` are reusable |
| Mock infrastructure maturity | ✅ `fitz.open` + `_init_vlm_adapter` mocking is well-established |
| Total test estimate accuracy | ⚠️ Plan says 56–60; realistic estimate is **38–52** (see §2) |
| Unresolved design gaps | ❌ 2 blocking items (§3), 5 recommendations (§4) |

---

## 2. Test Count Reconciliation

| Item | Plan Estimate | My Estimate | Delta | Rationale |
|------|:---:|:---:|:---:|-----------|
| **D0** — Config `kzocr_output_dir` | (not counted separately) | 2 | — | Default value test + env override |
| **D1** — Exception classes × retry | **32–42** | **18–22** | −14–20 | Range ±10 is too wide; see §2.1 for breakdown |
| **D2** — VLM main loop retry | **~15** | **10–15** | −0–5 | OK; some cases can be parametrized |
| **D3** — VLM checkpoint/resume | **~6** | **5–6** | −0–1 | OK; drop TOCTOU from P1/P2 scope |
| **D4** — Hierarchy anomaly (P3) | (not counted) | 3–5 | — | Pure function, trivial |
| **Conflict-2** — C1 L3 removal | (not counted) | 0–2 | — | 0 new tests, may need 2 assertions adjustment |
| **Total P1/P2** | **56–60** | **38–52** | **−8–22** | Gap driven by D1 over-estimation |

### 2.1 D1 real breakdown (recommended: 18–22 tests)

```
Exception hierarchy:
  1. OcrError can be raised and caught as base
  2. ApiError isinstance(OcrError) — inherited catch
  3. RateLimitedError isinstance(ApiError) — inherited catch
  4. OverSizeError isinstance(OcrError) — inherited catch
  5. RetryExhaustedError isinstance(OcrError) — inherited catch
  6. RetryExhaustedError.__cause__ carries original exception

retry_with_policy:
  7. Success path — first attempt returns immediately
  8. Retry → success on 2nd attempt (ApiError)
  9. Retry → success on nth attempt (RateLimitedError)
  10. All retries exhausted → RetryExhaustedError
  11. All retries exhausted → on_exhausted callback invoked
  12. retry_kwargs passed correctly per attempt (OverSizeError)
  13. error_types tuple filtering — non-matching exception not caught

BACKOFF_CONFIGS:
  14. "api" config valid (constructs and computes delay)
  15. "ratelimit" config valid
  16. "oversize" config valid

RateLimitedError edge cases:
  17. RateLimitedError Retry-After header overrides backoff
     (BLOCKING — depends on exception carrying header data)

Integration:
  18. retry_with_policy called with ExponentialBackoff instances
     (verifies no RetryPolicy dataclass needed — tests the simplification)
```

---

## 3. BLOCKING Issues (must resolve before implementation)

### B1 — `RateLimitedError` Retry-After header: exception design undefined

**Plan line 66:** `RateLimitedError 尊重 Retry-After header`

The exception class signature is shown as `class RateLimitedError(ApiError)` with no constructor. There is no design for:
- How the `Retry-After` header value reaches the exception
- Whether it's stored as `self.retry_after` or `self.retry_delay`
- Whether `retry_with_policy` reads it from the caught exception or from the adapter layer

**Impact:** Cannot test Retry-After behavior until this is designed. The plan's "尊重" claim is unimplementable as written.

**Recommendation:** Add to `RateLimitedError`:
```python
class RateLimitedError(ApiError):
    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after  # seconds, None = use backoff
```

### B2 — `_compute_config_hash(cfg)` undefined

**Plan D3.2:** References `_compute_config_hash(cfg)` but this function does not exist anywhere in the codebase. D3 testing depends on its output being deterministic and reproducible.

**Impact:** Tests like "config_hash mismatch → re-OCR" and "parameter change → cache miss" cannot be written until the hash computation is defined.

**Recommendation:** Specify what fields are included in the hash (engine_tag, max_tokens, VLM prompt template, etc.) before implementation. Tests can then directly verify hash stability.

---

## 4. RECOMMENDED Issues (fix or document before implementation)

### R1 — D3 cache path: `safe_book_code` variable scope mismatch

**Plan D3.1:** `_get_vlm_cache_path` references `safe_book_code` — but `safe_book_code` is a local variable inside `_run_vlm` (line 448 of `run.py`). The cache function signature does not take it as a parameter.

**Recommendation:** Add `safe_book_code: str` as a parameter:
```python
def _get_vlm_cache_path(cfg, engine_tag: str, book_code: str, page_num: int) -> Path:
```

### R2 — "No dead code" is a coverage metric, not a test case

**Plan D2 test requirement 5:** "验证 errors.py 无 dead code"

This cannot be verified by a single test case. It requires either:
- `pytest-cov` + coverage threshold assertion, or
- A test that explicitly calls `retry_with_policy` through the `_run_vlm` path (already covered by other D2 tests)

**Recommendation:** Remove this as a standalone requirement. D2 tests that actually call `retry_with_policy` through `_run_vlm` already prove it's consumed.

### R3 — `on_exhausted` lambda closure: plan text vs. code contradiction

**Plan line 143:** `on_exhausted=lambda pn, exc: failed_pages.update({pn: type(exc).__name__})`

The plan description says: "page_num 需由调用方闭包捕获，retry_with_policy 不管理此值"

But the lambda signature takes `pn` as a parameter — meaning `retry_with_policy` IS expected to pass the page number. The text and code contradict each other.

**Recommendation:** Clarify: does `retry_with_policy` pass `(page_num, exception)` or `(attempt, exception)` to `on_exhausted`? Based on the code examples, it seems to be `(int, Exception)` where `int` is page_num from the caller. This must be explicitly specified in `retry_with_policy`'s type signature.

### R4 — D1 estimate 32–42 is imprecise (±10 spread)

A ±10 range on 32 means ±31% margin of error. This is too wide for planning confidence. The realistic count is 18–22 (see §2.1).

**Recommendation:** Narrow to 18–22. Accept that parametrization can cover edge cases without inflating count.

### R5 — TOCTOU test impractical for P1/P2

**Plan D3 test requirement 5:** "TOCTOU 防护"

The `is_complete` check is inherently racy — deterministic TOCTOU testing requires OS-level timing manipulation (`fault injection`, `threading.Timer` to delete files between check and read). This is impractical in standard `pytest` without custom fixtures.

**Recommendation:** Document TOCTOU as an accepted residual risk for P1/P2. Move to P3 if a dedicated security regression test is needed.

---

## 5. Existing Test Compatibility

| Existing Test File | Compatibility with v0.5 AMEND | Notes |
|---|---|---|
| `test_ratelimit.py` (15 tests) | ✅ **Fully compatible** | `ExponentialBackoff` tests (lines 18–41) are reused by D1. D1 adds tests on top, doesn't modify these. |
| `test_vlm.py` (10 tests) | ⚠️ **Minor adjustments needed** | Mocking patterns are reusable. D2 adds tests _inside_ `_run_vlm` — existing tests mock `_run_vlm` entirely (line 31), so no conflict. |
| `test_atomic.py` (10 tests) | ✅ **Fully compatible** | D3 adds new test file or section for cache tests using same `tmp_path` pattern. |
| `test_leakage.py` (12 tests) | ✅ **No breakage expected** | Conflict-2 removes C1 L3 logging. Current `test_leakage.py` doesn't assert L3 log output explicitly — all assertions are on text content. |
| `test_pipeline.py` | ✅ **No change** | Not in v0.5 AMEND scope |
| `test_resources.py` | ✅ **No change** | Not in v0.5 AMEND scope |

**Total existing tests: ~47** (unaffected by v0.5 AMEND)

**Total after v0.5 AMEND P1/P2: ~85–99** (existing 47 + new 38–52)

---

## 6. Implementation Recommendations for Testability

### 6.1 D1 + D2 must be implemented by the same person (plan already states this)
Ensures `retry_with_policy` is actually consumed by D2. Test overlap is significant.

### 6.2 D2 test directory recommendation
Add D2 tests to `tests/test_vlm.py` (same file, extended test class). The mocking infrastructure is already there. Do not create a separate file — this avoids duplicating mock setup.

### 6.3 D3 test file recommendation
Add `tests/test_vlm_cache.py` — separate file for cache-specific tests. Use `tmp_path` + `monkeypatch.setenv` for `KZOCR_CLEAR_CACHE` and TTL tests.

### 6.4 Suggested `conftest.py` additions (optional, nice-to-have)
If the implementation goes to round 9 or later rounds, consider adding a `conftest.py` with shared VLM mock fixtures to reduce boilerplate across `test_vlm.py` and `test_vlm_cache.py`.

---

## 7. Summary Checklist for Implementation

| Item | File | Action | Blocking |
|------|------|--------|:--------:|
| D0 Config | `kzocr/config.py` | Add `kzocr_output_dir` field | No |
| B1 Retry-After design | `kzocr/engines/errors.py` | Add `retry_after` to `RateLimitedError.__init__` | **Yes** |
| B2 Config hash | `kzocr/engine/run.py` (or `errors.py`) | Define `_compute_config_hash` | **Yes** |
| D1 Exceptions | `kzocr/engines/errors.py` (new) | 4 exception classes + `retry_with_policy` | No |
| D2 VLM retry | `kzocr/engine/run.py` | Modify `_run_vlm` loop | No |
| R1 Cache param | `kzocr/engine/run.py` | Add `book_code` to cache path fn | No |
| R3 on_exhausted spec | `kzocr/engines/errors.py` | Fix docstring for callback signature | No |
| Conflict-2 | `kzocr/engines/leakage.py` | Remove L3 log marking | No |
| D3 Cache | `kzocr/engine/run.py` | Add cache read/write in `_run_vlm` | No |
| D4 Hierarchy (P3) | `kzocr/engines/hierarchy.py` (new) | Optional, defer | No |
