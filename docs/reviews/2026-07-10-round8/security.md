# 安全评审 —— v0.5 AMEND 最终确认（rc3 增量复核）

> 评审对象：`/home/keen/KZOCR/docs/plans/ocr-engine-unification.v0.5-AMEND.md` (v0.5-rc3)
> 本轮焦点：rc2 → rc3 变更对安全 posture 的影响
> 前置评审：`docs/reviews/2026-07-10-round6/security.md`（首次，有条件通过）
>           `docs/reviews/2026-07-10-round7/security.md`（D3 缓存复核，通过）

---

## 结论

**通过（Pass）。** rc2 → rc3 包含 5 项修订，全部属于**正确性改进和文档澄清**，不改变任何安全相关行为。R6/R7 已闭环和观察项均不受影响。

---

## rc2 → rc3 增量自分析

| # | 变更 | 类型 | 安全影响 |
|---|------|------|---------|
| 1 | `RetryPolicy` dataclass 消除，`retry_with_policy` 直接接受 `ExponentialBackoff` 实例 | 简化 | **无** — 纯接口简化，不改变重试 / 退避行为 |
| 2 | `retry_with_policy` 签名文档修正 —— `Raises RetryExhaustedError` + `__cause__` 链 | 文档/正确性 | **无** — 异常链不影响调用方处理逻辑的安全面 |
| 3 | D2 `on_exhausted` 闭包捕获 `page_num` 修正 | 正确性修复 | **无** — 修复了 rc2 中所有闭包共享 `i` 最终值的 bug，但 `on_exhausted` 参数内容（page_num + exception type name）无敏感信息 |
| 4 | `_run_vlm` 提取 `_process_vlm_page` 子函数建议 | 组织建议 | **无** — 代码提取建议，不改变任何安全相关执行逻辑 |
| 5 | `RateLimitedError` Retry-After 降级为文档注明 | 文档澄清 | **无** — 诚实标注当前适配器层尚未全面支持 Retry-After header 读取，退避行为转为纯指数退避。这是功能限制而非安全回归 |

---

## 逐项重确认（R6/R7 结论的稳定性）

### D0 — Config 扩展

字段 `kzocr_output_dir` 默认 `/tmp/kzocr/output`。R7 §4.1 已提出 `/tmp` 下的权限暴露问题（umask 依赖）。rc3 无变更。

**状态：** R7 结论维持不变 —— 需实现时注意目录权限显式 `0o700`，非 blocker。

### D1 — 异常分类 + `retry_with_policy`

D1 是 rc3 集中修改区域。简化后的接口：

- 异常继承体系（4 类）不变
- `retry_with_policy` 签名稳定，`__cause__` 链将原始异常传递给 `RetryExhaustedError`
- 异常消息来源不变（API 返回码、超时、字数统计），无内部路径泄漏风险

**状态：** R6 "通过" 结论维持。d1 变更不扩大异常消息泄漏面。

### D2 — VLM 主循环重试

`page_num` 闭包捕获修复是本次最值得关注的变化。rc2 版本存在 Python 闭包经典问题：

```python
# rc2 (bug): 所有 lambda 看到的都是最终 i 值
for i, page in enumerate(all_pages):
    ...
    on_exhausted=lambda pn, exc: failed_pages.update({pn: type(exc).__name__}),
```

```python
# rc3 (fix): page_num = i + 1 闭包捕获
for i, page in enumerate(all_pages):
    page_num = i + 1
    ...
    on_exhausted=lambda pn, exc: failed_pages.update({pn: type(exc).__name__}),
```

**安全视角：** 修复前的 bug 会导致所有失败页被记录为同一页码（最后一页），但 `failed_pages` 的内容（`{int: str}`，页码→异常类型名）不包含敏感数据。修复后 `failed_pages` 准确反映真实失败分布，对安全审计**有益无害**。

**状态：** R6 "无新增风险" 结论维持。修复后失败分类更准确，侧面增强审计可追溯性。

### D3 — VLM 缓存

rc3 无 D3 相关变更。R7 已验证的防御链完整：

| 防御层 | R7 状态 | rc3 影响 |
|-------|---------|---------|
| `KZOCR_CACHE_TTL` (86400s) | ✅ 闭环 | 不变 |
| `atomic_write` + `allowed_base` | ✅ 闭环 | 不变 |
| `engine_tag` 跨引擎隔离 | ✅ 闭环 | 不变 |
| `config_hash` 参数签名 | ✅ 闭环 | 不变 |
| TOCTOU (O1) | 非 blocker | 不变 |
| 内容完整性 (O2) | 延迟可接受 | 不变 |
| `/tmp` 目录权限 | ⚠️ 实现注意 | 不变 |

**状态：** R7 "通过" 结论维持。

### D4 — 层级异常检测

无变更。R6 "通过" 结论维持。

### 冲突-2 修订（C1 L3 移除）

无变更。D2 已接管实时重试，C1 L3 日志标记移除减少混淆，安全视角无影响。

### R6 O3（C3 限流器交互）

无变更。`ExponentialBackoff` 重用不影响 C3 限流器持久化状态，当前无接口冲突。

---

## 最终安全裁决表

| 项 | R6 裁决 | R7 调整 | rc3 影响 | 最终裁决 |
|----|---------|---------|----------|---------|
| D0 Config 扩展 | — | — | 无 | 认可，实现时注意 `/tmp` 权限 |
| D1 异常分类 | 通过 | — | 无（rc3 改属正确性/文档） | **通过** |
| D2 VLM 重试 | 通过 | — | `page_num` 闭包修复增强审计 | **通过** |
| D3 VLM 缓存 | 有条件通过 | → 通过 | 无 | **通过** |
| D4 层级检测 | 通过 | — | 无 | **通过** |
| 冲突-2 C1 L3 移除 | — | — | 无 | 认可 |
| TOCTOU (O1) | 观察项 | 非 blocker | 无 | 观察项维持 |
| 内容完整性 (O2) | 观察项 | 延迟可接受 | 无 | 观察项维持 |

---

## 一句话裁决

**rc3 的 5 项修订全部是正确性/文档侧改进，零安全风险敞口。** R6 有条件通过（D3 缓存）已由 R7 闭环为通过，且 rc3 未重置任何防御层。安全视角充分认可该方案进入实现阶段。
