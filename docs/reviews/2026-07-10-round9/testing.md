# Round 9 Final Testing Review: v0.5 AMEND rc4

> Review date: 2026-07-10
> Reviewer: general-purpose-13 (testing)
> Scope: Confirm Round 8 blocking issues (B1/B2) resolved in rc4; final plan approval
> Reference files:
>   - Plan: `docs/plans/ocr-engine-unification.v0.5-AMEND.md`
>   - Round 8 review: `docs/reviews/2026-07-10-round8/testing.md`
>   - Existing tests: `tests/test_ratelimit.py`, `tests/test_vlm.py`, `tests/test_leakage.py`
>   - Codebase snapshot: `kzocr/config.py`, `kzocr/engine/run.py`, `kzocr/engines/leakage.py`

---

## 1. Executive Summary

**Verdict: APPROVED — with 3 minor notes for implementation**

| Item | Status |
|------|--------|
| B1 — RateLimitedError Retry-After design | ✅ **Resolved** in rc4 (§104-109) |
| B2 — `_compute_config_hash` definition | ✅ **Resolved** in rc4 (§230-248) |
| R1 — safe_book_code vs book_code param | ⚠️ **Minor** (see §4.1) |
| R2–R5 recommendations | ⚠️ **Unaddressed** but non-blocking (see §4.2) |
| Implementation started? | ❌ **No** — v0.5 code not yet written |

**Codebase state (pre-implementation):** All recent commits (rc2→rc4) are documentation-only. The source tree reflects the pre-v0.5 baseline:
- `kzocr/engines/errors.py` → does not exist (D1 not started)
- `kzocr/config.py` → no `kzocr_output_dir` field (D0 not started)
- `_run_vlm` → still bare `except Exception: continue` (D2 not started)
- `leakage.py:192` → L3 log marking still present (Conflict-2 not applied)
- No cache logic in `run.py` (D3 not started)

This is expected — the plan is ready for implementation, not yet implemented.

---

## 2. B1 Verification — RateLimitedError Retry-After

**Round 8 finding:** `RateLimitedError` had no constructor design for `retry_after` — plan text said "尊重 Retry-After header" but provided no mechanism.

**rc4 resolution** (plan §104-109):
```python
class RateLimitedError(ApiError):
    """429/503 限流错误。"""
    def __init__(self, message: str = "", retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after
```

**Verdict: ✅ RESOLVED.** The exception now explicitly carries `retry_after` as an optional float. The plan also adds a note (implementation note 8, §337) that if the adapter layer doesn't support reading `Retry-After`, "纯指数退避，Retry-After 被忽略" — this is a proper fallback document.

**Testability:** Test case "RateLimitedError Retry-After header overrides backoff" (Round 8 D1 #17, 18 tests estimate) is now implementable:
```python
exc = RateLimitedError("rate limited", retry_after=5.0)
assert exc.retry_after == 5.0

exc2 = RateLimitedError("rate limited")  # no retry-after
assert exc2.retry_after is None
```

---

## 3. B2 Verification — `_compute_config_hash` Definition

**Round 8 finding:** `_compute_config_hash(cfg)` was referenced but undefined.

**rc4 resolution** (plan §230-248):
```python
def _compute_config_hash(cfg: Config) -> str:
    params = {
        "engine": cfg.vlm_engine,
        "sensenova_model": getattr(cfg, "sensenova_model", ""),
        "sensenova_base_url": getattr(cfg, "sensenova_base_url", ""),
        "vlm_host": getattr(cfg, "vlm_host", "127.0.0.1"),
        "vlm_port": getattr(cfg, "vlm_port", 18080),
    }
    raw = json.dumps(params, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]
```

**Verdict: ✅ RESOLVED.** The function is fully defined:
- `getattr` with defaults ensures robustness for Config field presence
- `sort_keys=True` ensures deterministic hash across Python versions
- 16-char truncated SHA256 is appropriate for cache invalidation (collision probability at 16 hex chars = 1 in 2^64)

**Testability:** D3 test "config_hash mismatch → cache miss" is now implementable:
```python
cfg1 = Config(vlm_engine="sensenova")
cfg2 = Config(vlm_engine="paddleocr_vl16")
assert _compute_config_hash(cfg1) != _compute_config_hash(cfg2)
```

---

## 4. Round 8 Recommendations Status

### 4.1 R1 — safe_book_code parameter mismatch (Minor)

**Status: ⚠️ Partially resolved.** The function signature uses `book_code: str` (correct) but plan §206 body still references `safe_book_code`:

```python
def _get_vlm_cache_path(cfg, engine_tag: str, book_code: str, page_num: int) -> Path:
    return Path(cfg.kzocr_output_dir) / VLM_CACHE_DIR / engine_tag / safe_book_code / ...
```

**Impact:** Trivial pre-implementation fix — the implementation will naturally use `book_code` (the parameter name). The plan text has a stale variable reference. No re-review needed.

**Recommendation:** Fix on implementation — use `book_code` in the path expression.

### 4.2 R2–R5 — Non-blocking recommendations (Unchanged)

| Item | Status | Impact |
|------|--------|--------|
| R2 — "No dead code" test requirement | ⚠️ Still in D2 §184 | Non-blocking. Implementation will naturally verify this. |
| R3 — on_exhausted lambda closure | ✅ Resolved in code example (§152) | Lambda uses `_attempt` (ignored), `page_num` from closure. |
| R4 — D1 estimate 32–42 (±10) | ⚠️ Still unchanged | Non-blocking. Accepted as planning margin. |
| R5 — TOCTOU test in D3 | ⚠️ Still in D3 §263 | Non-blocking. Can be deferred to P3 or documented as residual risk. |

---

## 5. Existing Test Compatibility (re-verified)

| File | Tests | Status | Notes |
|------|------:|--------|-------|
| `test_ratelimit.py` | 15 | ✅ Unchanged | `ExponentialBackoff` reused by D1. |
| `test_vlm.py` | 10 | ✅ Unchanged | Mocks `_run_vlm` entirely; D2 adds inside it, no conflict. |
| `test_leakage.py` | 12 | ✅ Unchanged | L3 log removal (Conflict-2) — current tests don't assert L3 output. |
| `test_atomic.py` | 10 | ✅ Unchanged | Not in v0.5 scope. |
| `test_pipeline.py` | — | ✅ Unchanged | Not in v0.5 scope. |

**Cumulative total after v0.5 implementation:** ~85–99 tests (existing 47 + new 38–52).

---

## 6. Pre-Implementation Checklist

| Priority | Item | File | Status |
|:--------:|------|------|:------:|
| P0 | D0 Config: add `kzocr_output_dir` | `kzocr/config.py` | ⏳ Not started |
| P1 | D1 Errors: create 4 exception classes | `kzocr/engines/errors.py` (new) | ⏳ Not started |
| P1 | D1 retry_with_policy + 18–22 tests | `kzocr/engines/errors.py` | ⏳ Not started |
| P1 | D2 VLM retry: wrap `_run_vlm` | `kzocr/engine/run.py` | ⏳ Not started |
| P1 | Conflict-2: remove L3 log | `kzocr/engines/leakage.py` | ⏳ Not started |
| P2 | D3 VLM cache: checkpoint/resume | `kzocr/engine/run.py` | ⏳ Not started |
| P2 | D3 tests: `test_vlm_cache.py` (new) | `tests/test_vlm_cache.py` | ⏳ Not started |
| P3 | D4 Hierarchy anomaly | `kzocr/engines/hierarchy.py` | ⏳ Deferred |

---

## 7. Summary

**Plan v0.5 AMEND rc4 is approved for implementation.**

- **B1** (RateLimitedError `retry_after`) — properly designed with constructor parameter and fallback to pure exponential backoff
- **B2** (`_compute_config_hash`) — fully defined with deterministic SHA256 over VLM-affecting config fields
- **Recommendations R1–R5** — non-blocking; 3 remain unaddressed but do not prevent implementation
- **Implementation not yet started** — codebase is clean pre-v0.5 baseline

The 3 minor notes for implementation:
1. **R1 fix:** Use param name `book_code` in `_get_vlm_cache_path` body, not the stale `safe_book_code` reference
2. **R2 accept:** Remove "no dead code" as standalone test requirement (covered by D2 consuming D1)
3. **R5 doc:** Document TOCTOU as accepted residual risk for P1/P2 scope
