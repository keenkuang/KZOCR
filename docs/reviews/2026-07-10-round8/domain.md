# KZOCR v0.5 AMEND rc3 领域评审（终局确认）

- **评审角色**：领域专家
- **评审日期**：2026-07-10
- **评审版本**：v0.5-rc3
- **依据文档**：`docs/plans/ocr-engine-unification.v0.5-AMEND.md`
- **前轮裁决**：Approved（round7）

---

## 结论：Approved — 无未解决的领域关注点

rc3 相对 rc2 的变更（软件工程再评审吸收：`RetryPolicy` 简化、签名修正、闭包捕获修正、子函数提取建议）均不涉及 TCM 领域语义，不影响此前已 approved 的领域评估。

以下逐项确认。

---

### D0 — Config 扩展

| 项 | 状态 |
|----|------|
| `kzocr_output_dir` 默认 `/tmp/kzocr/output` | 通过 — 无领域影响 |
| 经过 C2 路径穿越校验 | 通过 — 安全约束不改变领域语义 |

**裁决：** 无变更，保持通过。

---

### D1 — 异常分类 + `retry_with_policy`

`OcrError` → `{ApiError, RateLimitedError, OverSizeError, RetryExhaustedError}` 四级分类在 TCM OCR 场景下覆盖完整：
- **ApiError / RateLimitedError**：VLM API（云端 SenseNova）调用失败的标配
- **OverSizeError**：TCM 方剂文本超长（基线偏离）的特定场景，方案`max_tokens×1.8` 的退避策略在 TOC 项目 970 页中验证有效
- **RetryExhaustedError**：防止静默吞异常，确保失败可见

**工程细节变更（rc3）：** `ExponentialBackoff` 直接使用取代 `RetryPolicy` dataclass — 无领域语义变化。

**裁决：** 通过。

---

### D2 — VLM 主循环重试 + 失败分类

`_run_vlm` 的核心循环从 `except Exception: continue` 升级为：
1. 正常 API 重试（`BACKOFF_CONFIGS["api"]`）
2. OverSize 重 OCR（`BACKOFF_CONFIGS["oversize"]` + `max_tokens×1.8`）
3. 失败记录到 `failed_pages: dict[int, str]`

**领域观察：**
- `failed_pages` 是潜在的价值数据。长期运行中，如果某类 TCM 书籍的特定页码（如奇数页右半栏、带表格的页）持续失败，可以从 `failed_pages` 的分布中发现布局层面的系统性问题。建议在文档中注明此用途。
- OverSizeError 的 `max_tokens×1.8` 是从《秘方求真》基线有效值，对不同 TCM 书籍（如长篇方论、带大量注释的方书）可能不足。D2 的 `BACKOFF_CONFIGS` 定义了但未暴露为配置项；若将来遇到持续 OverSize，可考虑将此因子参数化。

**工程细节变更（rc3）：** `on_exhausted` 闭包正确捕获 `page_num`；`_process_vlm_page` 子函数提取建议 — 均不改变领域行为。

**裁决：** 通过。

---

### D3 — VLM 断点续跑

**领域评估：**
- 缓存含 PDF 原文，TTL 24h 在 TCM 数据治理层面可接受（中间产物而非终稿）
- `engine_tag` 参与路径避免 SenseNova ↔ PaddleOCR-VL 切换导致误用旧缓存 — **领域关键**：两引擎对 TCM 文本的识别差异显著，混用缓存会产生不一致的基线/泄露检测
- `config_hash` 校验确保参数改变时缓存失效 — 在领域实验阶段（如调整 VLM prompt）至关重要

**裁决：** 通过。

---

### D4 — 层级异常检测

**状态：** round6 中提出的 `expected_depth` 硬编码问题已在 rc2 修复，rc3 保持。无新增领域关注点。

**终局确认：**
- `expected_depth: int = 2` 作为默认值合理（对单卷经验方集保持向后兼容）
- TOC 驱动的自动推断规划在 P4，当前通过 CLI `--expected-depth` 配置是足够的过渡方案
- P3（低优先）定位仍合理

**裁决：** 通过。

---

### 冲突-2 修订：C1 L3 与 D2 职责边界

C1 L3（日志标记重 OCR）移除的合理性已在 round6/round7 确认。rc3 无额外领域影响。

**裁决：** 通过。

---

## 实施准备度评估

| 条件 | 状态 | 说明 |
|------|------|------|
| D4 `expected_depth` 参数化 | 已满足 | rc2 修复，rc3 保持 |
| D1+D2 同一人实施 | — | 工程约束，无领域要求 |
| 适配器 `max_tokens` 兼容 | 需确认 | 实施注意事项第 6 条 — PaddleOCRVl16Adapter / SenseNovaAdapter 的 `max_tokens` 支持程度影响 D2 OverSizeError 路径是否生效，建议实施前确认 |
| `_run_real` 异常增强不覆盖 | — | 已注明，v0.5 范围合理 |

---

## 综合结论

**Approved.** 四轮领域评审（round6 P0 发现 → round7 修正确认 → round8 终局验收）已闭环。v0.5 AMEND rc3 在 TCM 领域覆盖完整，无遗漏的边缘场景，可进入实施。
