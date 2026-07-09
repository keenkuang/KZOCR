# KZOCR v0.5 AMEND rc4 领域评审（终局确认）

- **评审角色**：领域专家
- **评审日期**：2026-07-10
- **评审版本**：v0.5-rc4
- **依据文档**：`docs/plans/ocr-engine-unification.v0.5-AMEND.md`
- **前轮裁决**：Approved（round8）

---

## 结论：Approved

rc4 相对 rc3 的变更仅为工程/性能优化，均不涉及 TCM 领域语义，不影响此前已 approved 的领域评估。

### rc4 增量变更的领域影响评估

| rc3→rc4 变更 | 来源 | 领域影响 |
|---|---|---|
| `RateLimitedError` 新增 `retry_after` 构造函数参数 | 测试 B1 | **无** — API 限流处理，不改变 TCM 语义 |
| `_compute_config_hash()` 完整定义 | 测试 B2 | **无** — 缓存元数据校验，不改变 TCM 语义 |
| `base_delay 2.0s→1.0s` | 性能建议 | **无** — 退避延迟调整，不影响 TCM 文本识别 |
| 累积重试时间跟踪建议 | 性能建议 | **无** — 批量场景监控，不改变领域行为 |

### D4 — 层级异常检测（P3）

rc3 中已验证 `expected_depth: int = 2` 参数化方案完整。rc4 无新增变更，P3 优先级定位保持合理。

### 冲突-2 修订

C1 L3（日志标记重 OCR）移除在 round6/round7/round8 已闭环，rc4 无新增影响。

---

## 实施准备度评估（领域角度）

| 条件 | 状态 | 说明 |
|------|------|------|
| D4 `expected_depth` 参数化 | ✅ 已满足 | rc2 修复，rc4 保持 |
| 适配器 `max_tokens` 兼容性 | ⚠️ 需实施确认 | 实施注意事项 #6 — 影响 D2 OverSizeError 重 OCR 路径有效性 |
| `_run_real` 异常增强不覆盖 | ✅ 已注明 | v0.5 范围合理 |

---

## 综合结论

**Approved.** rc4 的全部变更为工程/性能层面的增量优化，无 TCM 领域影响。所有域相关问题（D4 `expected_depth` 参数化、D2 `failed_pages` 长期观察、OverSizeError 因子可配置性）已在 round6–8 闭环。v0.5 AMEND rc4 可进入实施。
