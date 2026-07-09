# Round 9 — 软件工程评审报告

**评审者：** code-reviewer-4（软件工程）
**日期：** 2026-07-10
**评审对象：** `ocr-engine-unification.v0.5-AMEND.md` (rc5, commit 948f42e on GitHub)
**当前 HEAD：** `66ae7aa`（v0.6.0，已包含全部 v0.5 AMEND 实施）

---

## 逐项验证

| 项 | 预期 | 实际状态 | 结论 |
|----|------|---------|------|
| **D0** — Config `kzocr_output_dir` + `cache_ttl_seconds` | `config.py` 新增字段，`from_env()` 读取 `KZOCR_OUTPUT_DIR` / `KZOCR_CACHE_TTL` | `Config` 含 `kzocr_output_dir`（默认 `/tmp/kzocr/output`）和 `cache_ttl_seconds`（默认 86400），均从环境变量读取 | ✅ |
| **D1** — 异常分类 + `retry_with_policy` | `kzocr/engines/errors.py` 含 4 异常子类 + `retry_with_policy` + `BACKOFF_CONFIGS` | `errors.py` (3846B)：`OcrError` / `ApiError` / `RateLimitedError`(带 `retry_after`) / `OverSizeError` / `RetryExhaustedError` + `BACKOFF_CONFIGS` 字典 + `retry_with_policy` 完整实现 | ✅ |
| **D2** — VLM 主循环重试 + 失败分类 | `_run_vlm` 使用 `retry_with_policy` 而非手写循环，`failed_pages` 记录失败 | `run.py` 第 610–662 行：结构化异常处理，使用 `retry_with_policy(BACKOFF_CONFIGS["api"])` 和 `BACKOFF_CONFIGS["oversize"]`，`failed_pages: dict[int, str]` 记录 + OverSizeError 降低 DPI 重试 | ✅ |
| **D3** — VLM 断点续跑 | 缓存路径（`vlm_cache/`）、`config_hash`、TTL 过期、`KZOCR_CLEAR_CACHE=1` | `run.py` 第 364–429 行：`_compute_config_hash` / `_get_vlm_cache_dir` / `_load_cache_text`（含 TTL 检查）/ `_save_cache_text`；主循环缓存先读 + 写入 + 清除逻辑 | ✅ |
| **D4** — 层级异常检测 | `kzocr/engines/hierarchy.py` 新建，P3 低优先 | `hierarchy.py` (4614B) 存在 | ✅ |
| **冲突-2** — C1 L3 移除 | `leakage.py` L3 日志标记移除，改为注释说明由 D2 接管 | `leakage.py:174` 注释 "L3 已由 D2 实时重试取代"，原 L3 `logger.info` 已删除；L1/L2/L4 保留 | ✅ |
| **计划提交** | `c4120cd` (D0+D1), `dd9b76f` (D2), `cc6f52a` (D4), `1f52052` (D3) | 4 个提交全部在 git 历史中确认 | ✅ |
| **测试通过** | ≥177 测试全通过 (0.94s) | **268 passed** in 14.11s（超出计划要求） | ✅ |

---

## 代码质量观察

### 优点
1. **D1 + D2 无 dead code**：`retry_with_policy` 在 D2 中被 `_run_vlm` 两处实际消费（API 重试 + OverSize 重试），架构脱节风险消除。
2. **`_process_vlm_page` 提取**：`run.py:434` 将单页 VLM 识别逻辑从主循环中提取为独立函数，降低 `_run_vlm` 复杂度。符合软件工程评审建议。
3. **`_run_vlm` 函数长度合理**：读取全部代码确认，主循环清晰，各职责（缓存、重试、泄漏防御、跨页合并）分离明确。
4. **Config hash 设计合理**：包含 engine/host/port/model/api_key 等影响输出的参数，不含 TTL 等策略参数（由调用侧单独校验）。
5. **持久化限流器状态**：`ratelimit.py` 的 `AdaptiveRateLimiter` 已含 `RateLimitStore` 持久化后端（SQLite），`_persist()` 方法在 `wait()` / `report_success()` / `report_error()` 后调用。

### 建议（非 blocking）
1. `_compute_config_hash` 实现（`run.py:367-382`）使用 `"|"` 拼接 + 简单 SHA256，与计划中的 JSON 序列化方案略有差异——当前方案更轻量，功能等价，建议保留。
2. 文档更新（计划状态行）指向 HEAD `1f52052`，实际 HEAD 已到 `66ae7aa`（v0.6.0）。这是正常的后续版本演进，不影响 v0.5 AMEND 正确性。建议下次文档同步时更新版本引用。

---

## 裁决

**Approved.** ✅

v0.5 AMEND（D0–D4 + 冲突-2）全部完成并合并。异常处理体系（分类/重试/退避）、VLM 断点续跑、层级异常检测均已按计划实现，测试 268 例全通过。可以继续推进后续工作。
