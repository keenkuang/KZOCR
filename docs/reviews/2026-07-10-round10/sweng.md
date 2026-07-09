# 软件工程最终签署 — Round 10

| 字段 | 值 |
|---|---|
| 审查对象 | `docs/plans/ocr-engine-unification.v0.5-AMEND.md` rc5 |
| 审查基线 | `948f42e` (main) |
| 审查人 | code-reviewer-5 |
| 日期 | 2026-07-10 |
| 前序裁决 (R8+R9) | Approved |
| **本轮裁决** | **APPROVED** |

---

## 裁决理由

### 1. 所有前序阻塞已清除

| 来源 | 阻塞项 | rc5 状态 |
|---|---|---|
| Round 6 多角色审查 | — 保留项已全量吸收 | 已吸收 |
| Round 7 sweng 再审查 | 2 项 must-fix | rc3 已吸收 |
| Round 8 测试审查 | B1/B2 阻塞 | rc4 已吸收 |
| Round 9 运维审查 | D3 缓存 TTL 强制实施 FAIL | rc5 已吸收 |

### 2. config.py 缺失字段确认

代码核对：

- `kzocr_output_dir` — 当前 `config.py` 无此字段。方案 `D0` 要求新增，对应 `KZOCR_OUTPUT_DIR` 环境变量。**计划内容与实际代码缺口吻合，无遗漏。**
- `cache_ttl_seconds` — 当前 `config.py` 无此字段。方案 `D3` 要求新增，对应 `KZOCR_CACHE_TTL_SECONDS` 环境变量。**同上。**
- `vlm_engine` — 当前 `load_config()` 已通过 `os.environ.get("KZOCR_VLM_ENGINE", "auto")` 设置。`D0` 将其提升为显式 Config 字段，是查漏补缺，不是新需求。**可接受。**

### 3. 异常分层 (`errors.py`) 设计合理

- `BaseEngineError` → 4 个具名子类，继承链清晰
- `retry_with_policy()` 的 `BACKOFF_CONFIGS` 按异常类型分派退避策略
- `TimeoutError`→ `AutoScaleError`→ `RateLimitError` 的降级链在实践中验证过（类似 TOC 的 provider fallback 模式）
- 非 RetryableError（校验/编码错误）立即透传，避免无效重试

### 4. VLM 主循环重试 (`run.py:_run_vlm`)

当前代码：

```python
except Exception as exc:
    logger.warning("...")
    continue
```

方案替换为 `retry_with_policy()` + `failed_pages` 收集 + `_handle_failed_pages()` 汇总日志。

**风险点：** `for attempt in range(backoff.max_retries)` 的最内层重试与最外层 `for page_num in page_range` 形成 O(p × r) 复杂度。如果 `max_retries >= 5` 且页数 > 20，失败页会显著拖慢整体。方案中的 `TOTAL_PAGE_RETRY_LIMIT=3` 提供了总控上限，是必要的安全网。**接受。**

### 5. 断点续跑 (D3)

- 方案要求 `failed_pages` 作为 `set[int]` 收集，不走持久化文件
- `cache_ttl_seconds` 的 TTL 强制实施（rc5 修复）覆盖了运维评审 FAIL 项
- 日志中 `FINISHED {n}/{total} ...` 格式已定，后期如果真的要恢复可凭日志重建

### 6. 冲突-2 修订 (leakage.py C1:L3)

`leakage.py:192` 的 log_mark 打印移除已包含在 plan 中。代码层面无歧义。

### 7. 提供者限流 (`ratelimit.py`)

- `ExponentialBackoff` 来自 TOC 7000+ 调用验证，可靠性已知
- `BACKOFF_CONFIGS` 中 `RateLimitError` 走 `ExponentialBackoff` 而非 `AdaptiveRateLimiter`，合理——重试策略独立于限流器状态
- `AdaptiveRateLimiter` + `MultiTokenRateLimiter` 的持久化 (`RateLimitStore`) 已实现在 `ratelimit.py`，方案只是组装调用，无新风险

---

## 无阻塞项

P3 优先级项（D4 层级异常检测）标注为 `[WARN]` 非阻塞，与 R6 共识一致。

**签署结论：可实施。**
