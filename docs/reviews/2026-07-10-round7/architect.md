# KZOCR v0.5 AMEND — 架构师复审报告（round 7，rc2）

- **评审角色**：首席架构师
- **评审日期**：2026-07-10
- **评审对象**：`docs/plans/ocr-engine-unification.v0.5-AMEND.md` v0.5-rc2
- **本轮焦点**：对 round 6 指出的 2 项 Blocking + 3 项 Major + 2 项 Medium 修订的逐一验证
- **前置评审**：`docs/reviews/2026-07-10-round6/architect.md`（需修订裁决）

---

## 总体裁决：有条件通过（Conditional Pass）

rc2 方案已解决了 round 6 指出的全部 **2 项 Blocking** 和 **3 项 Major** 问题。修订质量良好，主要在实现细节层面存在 **2 项 Minor 留心项**（非阻断，无需再修订方案）。

| 检查点 | round 6 状态 | rc2 状态 | 本次裁决 |
|--------|-------------|---------|---------|
| D3-1 (Blocking) Config 字段缺失 | ❌ 需修订 | ✅ D0 新增 `kzocr_output_dir` | **已解决** |
| D1-1 (Major) YAGNI 过度设计 | ❌ 需修订 | ✅ 4 类型体系 | **已解决** |
| D2-1 (Major) retry_with_policy 脱节 | ❌ 需修订 | ✅ D2 通过 retry_with_policy 实现 | **已解决** |
| D2-2 (Major) OverSize 静默 fallback | ❌ 需修订 | ✅ 抛出 RetryExhaustedError | **已解决** |
| D3-2 (Blocking) 缓存语义冲突 | ❌ 需修订 | ✅ engine_tag + config_hash 校验 | **已解决** |
| 冲突-2 (Medium) C1 L3 职责重复 | ❌ 需修订 | ✅ C1 L3 移除，D2 接管 | **已解决** |
| 实施顺序 P0→P1→P2→P3 | ❌ P0 遗漏 | ✅ 已纳入 | **已解决** |

---

## 逐项验证报告

### 1. [D3-1 Blocking] Config 新增 `kzocr_output_dir` 字段

**方案修订记录**：D0 节（行 11–31）新增。Config dataclass 增加 `kzocr_output_dir` 字段，映射环境变量 `KZOCR_OUTPUT_DIR`，默认值 `/tmp/kzocr/output`。实施顺序表中列为 P0。

**验证**：
- 字段定义位置正确：在 Config dataclass 中新增 `kzocr_output_dir`，与现有配置模式一致
- 默认值语义合理：`/tmp/kzocr/output` 是系统临时目录，符合"可清理的中间产物存储"定位
- 提及 C2 `_check_base` 路径穿越校验（行 18），安全面已纳入
- P0 优先级正确：D3 的缓存路径依赖此字段

**裁决**：✅ **已解决。** Config 字段 + P0 优先级安排完整解决了 round 6 的 D3-1 阻断问题。

---

### 2. [D1-1 Major] YAGNI 异常体系过度设计 → 4 类型

**方案修订记录**：D1 节（行 40–48）将异常子类从 8 个缩减为 4 个：`ApiError`、`RateLimitedError`、`OverSizeError`、`RetryExhaustedError`。

**验证**：
- `OcrSkipError` 替换为 `RetryExhaustedError`——命名从"动作"（跳过）改为"原因"（重试耗尽）。此为可接受的设计变更。`RetryExhaustedError` 在 `retry_with_policy` 中被抛出，更具通用性（其他调用方可自行决定如何响应重试耗尽）
- 4 个子类均有明确消费者：
  - `ApiError` / `RateLimitedError` → D2 的 API 重试路径
  - `OverSizeError` → D2 的超阈值检测路径
  - `RetryExhaustedError` → D2 重试耗尽后捕获并跳过
- 无未使用类型，符合 YAGNI

**裁决**：✅ **已解决。** YAGNI 合规。`OcrSkipError` → `RetryExhaustedError` 的命名变更合理（机制层命名优于动作层命名）。

---

### 3. [D2-1 Major] D2 通过 `retry_with_policy()` 实现重试，无手写循环

**方案修订记录**：D2 节（行 126–165）重写为 `retry_with_policy` 两次调用：
1. 第一次（行 141–146）：用 `RETRY_POLICIES["api"]` 处理 API/限流错误
2. 第二次（行 149–155）：用 `RETRY_POLICIES["oversize"]` 处理超阈值重 OCR

**验证**：
- 伪代码中无 `for attempt in range(...)` 手写循环
- `retry_with_policy` 作为唯一的重试入口，D1 的 `RETRY_POLICIES` 策略表通过此入口被 D2 消费
- 两次调用的策略 (`"api"` vs `"oversize"`) 对应不同的策略行，验证了策略表的可达性
- D1 的 `retry_with_policy` + `RetryPolicy` dataclass（回应软件工程评审建议）联合消除 dead code

**裁决**：✅ **已解决。** 架构脱节消除。

---

### 4. [D2-2 Major] OverSizeError 抛出 RetryExhaustedError，无静默 fallback

**方案修订记录**：D2 节行 169 明确声明 "OverSizeError 重 OCR 失败后**抛出 `RetryExhaustedError**，不走静默使用泄漏结果"。代码行 156–160 中 `except RetryExhaustedError` 分支负责跳过该页。

**验证**：
- 重 OCR 仍超阈 → `retry_with_policy` 耗尽 → 抛出 `RetryExhaustedError`（或 `OverSizeError` 经策略耗尽后触发）
- `failed_pages` 记录错误类型（行 158）：`"Exhausted:" + type(exc.__cause__).__name__`
- 确认无静默 fallback 路径

**裁决**：✅ **已解决。** 逻辑正确。

---

### 5. [D3-2 Blocking] 缓存 include `engine_tag` + `config_hash`，C2 冲突解决

**方案修订记录**：D3.1（行 199–201）+ D3.2（行 207–224）：

**路径层**：
```
{kzocr_output_dir}/vlm_cache/{engine_tag}/{book_code}/page_{N:04d}.txt
```
- `engine_tag` 参与路径 → 引擎切换时缓存自动隔离（SenseNova ↔ PaddleOCR-VL 不互相污染）

**元数据层**：
- 首行 `# config_hash={sha256(VLM_PARAMS_JSON)[:16]}`（行 209）
- `_cache_is_valid()`（行 214–224）同时检查 `is_complete`（文件完整性）+ `config_hash`（参数一致性）
- 参数变化 → `config_hash` 不匹配 → 缓存视为无效 → 重新 OCR

**C2 哲学冲突解决分析**：
- C2 "文件即状态"仍适用于文件完整性检查（`is_complete`）
- `config_hash` 增加了语义层：文件存在 ≠ 缓存有效
- 参数变更、引擎切换、prompt 更新等场景均导致缓存自动失效

**裁决**：✅ **已解决。** 双重校验（完整性 + 参数签名）恰当地解决了中间产物缓存与 C2 最终产物哲学之间的冲突。

**留心项**（Minor）：`VLM_PARAMS_JSON` 包含哪些字段需要在实现时明确定义文档。当前计划仅说"包含当前使用的引擎标识、max_tokens、VLM prompt 等影响输出的参数"。建议在实现时对 `_compute_config_hash` 函数增加显式的 field allowlist，防止无意中引入无关参数（如时间戳、请求 ID）导致每次 hash 不同而缓存永久失效。

---

### 6. [冲突-2] C1 L3 日志标记移除，D2 实时重试接管

**方案修订记录**：冲突-2 修订节（行 277–288）：

| C1 层级 | round 6 状态 | rc2 裁决 |
|---------|-------------|---------|
| L1 基线检测 | 保留 | **保留**（仅日志告警） |
| L2 max_tokens 上限 | 保留 | **保留** |
| L3 日志标记重 OCR | 存在（多余） | **移除** |
| L4 探针重叠检测 | 保留 | **保留** |

**验证**：
- D2 的 OverSizeError 实时处理超阈页面，C1 L3 的日志标记变为多余
- C1 L3 移除后，leakage 模块不再"标记重 OCR"——D2 已在循环层完成
- 确保 `LeakageDetector.detect` 输入是已重试文本（行 287），不重复触发阈值告警

**裁决**：✅ **已解决。** 职责边界清晰，无重叠。

---

### 7. 实施顺序（P0→P1→P2→P3）评估

| 优先级 | 项 | 依赖 | 评估 |
|--------|-----|------|------|
| P0 | D0 Config 扩展 | 无 | **正确**——D3/D2 均依赖此字段 |
| P1 | D1 异常类 + retry_with_policy + 测试 | D0 | **正确**——D2 的消费者 |
| P1 | D2 VLM 主循环重试（通过 retry_with_policy） | D0 + D1 | **正确**——消费 D1 的 retry_with_policy |
| P1 | 冲突-2 C1 L3 移除 | D2 完成后 | **正确**——D2 接管后才可移除 |
| P2 | D3 VLM 断点续跑 | D0 + D1 + D2 | **正确**——依赖 P0 Config + D2 `_run_vlm` 重构 |
| P3 | D4 层级异常检测 | 无硬依赖 | **正确**——可选延迟 |

**实施注意事项行 303–310 的补充校验**：
1. D1 + D2 同人实施 ✅——确保 retry_with_policy 被消费
2. D2 + D3 同人实施 ✅——避免 `_run_vlm` diff 冲突
3. D3 缓存路径经 C2 校验 ✅——`atomic_write(allowed_base=cfg.kzocr_output_dir)`
4. C1 L3 测试调整 ✅——标注需要对应调整 leakage 测试
5. 适配器 max_tokens 兼容性 ✅——已标注风险，待实施时确认
6. `_run_real` 路径不覆盖 ✅——已标记备查

**裁决**：✅ **顺序合理。** P0→P1→P2→P3 的分级正确，依赖关系清晰。

---

## 新增留心项（Minor，非阻断）

### [M-1 · Minor] `backoff` 对象在 D2 伪代码中未被使用

D2 节行 131–134：
```python
from kzocr.engines.ratelimit import ExponentialBackoff
...
backoff = ExponentialBackoff(base_delay=2.0)
```

但 `retry_with_policy` 内部自己管理退避逻辑（`ExponentialBackoff`），D2 不再直接调用 `backoff.sleep()`。此 `backoff` 对象在伪代码中创建后未使用。虽然伪代码不一定代表实现，但建议在最终实现中删除此行避免 dead code。

### [M-2 · Minor] `retry_with_policy` 的 `retry_kwargs` 参数无消费者

函数签名（行 74–80）包含 `retry_kwargs: dict[int, dict] | None = None` 参数，但 D2——唯一的消费者——通过 lambda 闭包传递不同的 `max_tokens`（行 150），未使用 `retry_kwargs`。

如果初始提交中无消费方使用 `retry_kwargs`，建议：按 YAGNI 原则在初始 `retry_with_policy` 签名中移除该参数，待出现真实需求时再添加。移除后函数签名更简洁：

```python
def retry_with_policy(
    fn: Callable[..., T],
    policy: RetryPolicy,
    error_types: tuple[type[Exception], ...] = (ApiError,),
    on_exhausted: Callable[[int, Exception], None] | None = None,
) -> T:
```

### [M-3 · Minor] D2 伪代码中 `max_tokens=...` 为占位符

行 151：
```python
lambda: vlm.recognize_pages(imgs, max_tokens=int(baseline.median * 1.8))
       if supports_two_page else vlm.recognize_page(imgs[0], max_tokens=...),
```

`recognize_page(imgs[0], max_tokens=...)` 中的 `...` 是 Ellipsis 字面量还是占位符？如果实际代码中出现 `Ellipsis`，会在运行时触发 `TypeError`（适配器接收 `max_tokens=Ellipsis`）。建议在实现前确认适配器层对 `max_tokens` 参数的支持情况（此问题已部分在实施注意事项第 5 条中标注）。

---

## 长期架构观察（非本轮阻断）

### [L-1] `_run_real` 路径的异常增强未被覆盖

与 round 6 A-3 观察一致——当前 v0.5 范围仍只覆盖 `_run_vlm`。`_run_real` 的异常处理仍为 `except Exception: raise`。不阻碍本次实施，但建议在 v0.6 或后续版本中统一升级。

### [L-2] `_cache_is_valid` 的 config_hash 字段清单需治理

如第 5 项留心所述，`_compute_config_hash` 的输入字段需有显式 allowlist。如果 hash 涵盖了运行时动态值（如 timestamp、session id、request_id），则每次缓存皆命中失败。建议在 `errors.py`（或独立常量模块）中定义：

```python
VLM_CACHE_PARAMS = ["engine_id", "max_tokens", "vlm_prompt_template"]
```

确保仅影响输出的参数参与 hash，避免缓存性能退化为"永不命中"。

---

## 最终裁决汇总

| 检查点 | 严重度 | 裁决 | 备注 |
|--------|--------|------|------|
| D3-1 Config 字段缺失 | ~~Blocking~~ → ✅ | **已解决** | D0 新增 kzocr_output_dir |
| D1-1 YAGNI 过度设计 | ~~Major~~ → ✅ | **已解决** | 4 类型，无未使用子类 |
| D2-1 retry_with_policy 脱节 | ~~Major~~ → ✅ | **已解决** | D2 通过 retry_with_policy 实现 |
| D2-2 OverSize 静默 fallback | ~~Major~~ → ✅ | **已解决** | 抛出 RetryExhaustedError |
| D3-2 缓存语义冲突 | ~~Blocking~~ → ✅ | **已解决** | engine_tag + config_hash |
| 冲突-2 C1 L3 职责重复 | ~~Medium~~ → ✅ | **已解决** | C1 L3 移除，D2 接管 |
| 实施顺序 | ~~Medium~~ → ✅ | **已解决** | P0→P1→P2→P3 清晰 |
| M-1 backoff 未使用 | Minor | 留心项 | 实现时删除死代码 |
| M-2 retry_kwargs 无消费者 | Minor | 留心项 | 建议按 YAGNI 移除 |
| M-3 max_tokens=... 占位符 | Minor | 留心项 | 实施时确认适配器签名 |

### 最终裁决：**有条件通过（Conditional Pass）**

**已解决（7/7）：** 所有 round 6 识别的问题均已正确处理。修订质量高——不仅机械地满足了裁决，还对部分问题给出了比建议更优的解决方案（例如 `OcrSkipError` → `RetryExhaustedError` 的机制层命名，以及 `lambda` 闭包传递 `max_tokens` 而非 `retry_kwargs` 的更简洁方式）。

**实施前需确认的 2 项**（上述 M-2 + M-3，低风险）：
1. `retry_kwargs` 是否保留——建议移除（YAGNI）
2. 适配器 `max_tokens` 参数签名——PaddleOCRVl16Adapter / SenseNovaAdapter 实现时确认

这两个留心项不需再修订方案文档，可在实施阶段由实施工程师自行处理。

---

*附录：本次复审参照的文档*
- `docs/plans/ocr-engine-unification.v0.5-AMEND.md` v0.5-rc2（修订版）
- `docs/reviews/2026-07-10-round6/architect.md`（round 6 架构师评审）
- `docs/reviews/2026-07-10-round6/testing.md`（测试评审，含 dataclass 建议来源）
- `docs/reviews/2026-07-10-round6/sweng.md`（软件工程评审）
- `docs/reviews/2026-07-10-round6/security.md`（安全评审，含缓存 TTL 建议来源）
