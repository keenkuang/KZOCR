# KZOCR v0.5 AMEND — 软件工程再评审（Round 7）

**评审范围：** v0.5-rc2 对 Round 6 软件工程评审 3 项 must-fix 的修复验证。\
**评审对象：** `docs/plans/ocr-engine-unification.v0.5-AMEND.md` + 源代码（`run.py`, `ratelimit.py`, `config.py`）

---

## 1. retry_with_policy 集成至 D2 — 修复验证

### ✅ 已修复项

| Round 6 Must-Fix | 状态 | 证据 |
|---|---|---|
| D1 `policy: dict` → 强类型 | ✅ 已修复 | `RetryPolicy` 现为 `@dataclass`（含 `strategy`, `max_retries`, `base_delay`, `max_delay`, `jitter` 5 个显式字段） |
| D1 `retry_with_policy` 无消费者 → 被 D2 消费 | ✅ 已修复 | D2 循环中两处调用 `retry_with_policy(..., policy=RETRY_POLICIES[...], ...)` |
| `OcrSkipError` → `RetryExhaustedError` | ✅ 已修复 | D1 异常表已替换；D2 `except RetryExhaustedError as exc` 一致性 |
| D3 缓存路径含 `engine_tag` | ✅ 已修复 | `_get_vlm_cache_path` 路径为 `…/vlm_cache/{engine_tag}/{book_code}/page_{num:04d}.txt` |

---

### ❌ 剩余设计不一致（共 3 项）

#### 问题 A（⚡ 建议修复）— `RetryPolicy` 与 `ExponentialBackoff` 字段重复

```
RetryPolicy                    ExponentialBackoff (ratelimit.py:44)
---------                      --------------------
max_retries: int = 3           max_retries: int = 5
base_delay: float = 2.0        base_delay: float = 2.0
max_delay: float = 300.0       max_delay: float = 300.0
jitter: float = 0.5            jitter: float = 0.5
```

两个 dataclass 声明的 4 个字段语义完全重叠，但默认值**不一致**（`RetryPolicy` 默认 `max_retries=3`，`ExponentialBackoff` 默认 `max_retries=5`）。

**影响分析：**
- 新增的 `RetryPolicy` 成为 `ExponentialBackoff` 的"影子配置层"——`retry_with_policy` 内部需要将 `RetryPolicy` 参数手动映射到 `ExponentialBackoff()` 构造，产生"两份配置源"。
- `ExponentialBackoff` 已有 4 项单元测试验证其退避计算。`RetryPolicy` 的字段重复意味着这些测试的有效性只覆盖了实际退避计算层，不覆盖配置传递层。
- 默认值不一致（3 vs 5）可能在修改一处而忘记另一处时引入静默行为变化。

**建议：** 两种修复方向（二选一）：
1. **简化方案**：`retry_with_policy` 直接接受 `ExponentialBackoff | None` 参数（Round 6 原建议），删除 `RetryPolicy` dataclass。`RETRY_POLICIES` 表改为 `ExponentialBackoff` 实例：
   ```python
   RETRY_POLICIES = {
       "api":      ExponentialBackoff(max_retries=3, base_delay=2.0),
       "ratelimit": ExponentialBackoff(max_retries=3, base_delay=1.0),
   }
   ```
2. **最小改动**：`RetryPolicy` 内部持有一个 `ExponentialBackoff` 实例而非重复字段：
   ```python
   @dataclass
   class RetryPolicy:
       strategy: str
       backoff: ExponentialBackoff = field(default_factory=lambda: ExponentialBackoff(max_retries=3))
   ```
   这样 `ExponentialBackoff` 仍为退避计算的单一真实来源。

**优先级：** ⚡ 建议修复（不影响正确性，但长期维护负担）。

---

#### 问题 B（⚠️ 必须修复）— `retry_with_policy` 抛出语义与 D2 捕获不一致

**D1 `retry_with_policy` 文档（plan 第 94 行）：**
```
Raises:
    重试耗尽后抛出最后一次捕获的异常。
```

**D2 代码（plan 第 156–159 行）：**
```python
except RetryExhaustedError as exc:
    failed_pages[i + 1] = "Exhausted:" + type(exc.__cause__).__name__
```

**矛盾：** 文档说抛出最后一次捕获的异常（如 `ApiError`），但 D2 捕获的是 `RetryExhaustedError`。如果按文档实现，`except RetryExhaustedError` **永远不会触发**。

**根因：** 文档与代码取了两种不同的设计方案：
- 方案 A：耗尽时**直接**抛出最后一次捕获的异常（文档描述）。调用方无法区分"正常异常"和"耗尽后最后一次异常"。
- 方案 B：耗尽时**包装**为 `RetryExhaustedError`（D2 预期）。调用方可以统一捕获 `RetryExhaustedError` 并读取 `__cause__` 获取原始异常。

**建议：** 采用方案 B，修正文档为：
```
Raises:
    RetryExhaustedError — 所有重试耗尽，原始异常链在 __cause__ 中。
```

同时在 `retry_with_policy` 实现中确保：
```python
raise RetryExhaustedError(f"重试 {policy.max_retries} 次后仍失败") from last_exception
```

**优先级：** ⚠️ 必须修复（实现前对齐设计，否则 D2 单元测试会失败）。

---

#### 问题 C（⚠️ 必须修复）— `on_exhausted` lambda 中的 `pn` 参数语义错误

**D2 代码（plan 第 145 行）：**
```python
on_exhausted=lambda pn, exc: failed_pages.update({pn: type(exc).__name__}),
```

**问题：** `retry_with_policy` 的 `on_exhausted` 签名是 `Callable[[int, Exception], None]`，其中第一个 `int` 参数按照函数设计意图很可能是**尝试次数**（第几次重试耗尽），但 D2 lambda 将其当作**页码**（`pn`）用于 `failed_pages` 字典的 key。

这将导致：
- `failed_pages` 中存储的 key 是尝试次数（1–3）而非页码（如 7、23）
- `failed_pages` 的输出结果完全错误，失去"哪些页失败"的追踪能力

**修复建议：** 用闭包捕获当前页码：
```python
page_num = i + 1
on_exhausted=lambda _attempt, exc: failed_pages.update({page_num: type(exc).__name__}),
```

**优先级：** ⚠️ 必须修复（功能缺陷，`failed_pages` 数据会完全错误）。

---

### 🟡 注意项（非阻塞）

#### D — `backoff` 变量在 D2 作用域中未使用

D2 代码片段初始化了 `backoff = ExponentialBackoff(base_delay=2.0)`，但后续只使用了 `RETRY_POLICIES["api"]`/`RETRY_POLICIES["oversize"]`，没有将 `backoff` 传给任何函数。该变量在 D2 作用域内是**死代码**。实现时建议删除（如果采用问题 A 的简化方案则自然消除）。

#### E — `RateLimitedError` 的 `Retry-After` 缺乏传递机制

Plan 第 102 行声明"`RateLimitedError` 尊重 `Retry-After` header"，但：
- `RateLimitedError` 异常类设计中没有携带 `retry_after` 字段
- `RetryPolicy` dataclass 中没有 `respect_retry_after` 参数（Round 6 曾建议添加）
- `retry_with_policy` 的实现没有从异常中提取 `Retry-After` 的逻辑

如果当前 API 链路通过 adapter 抽象无法暴露 HTTP header，建议在实现文档中注明 **降级为纯指数退避**（Round 6 已认可此降级方案为 P1.5 非阻塞），并将 Plan 第 102 行改为条件性表述。

---

## 2. `_run_vlm` 重构规模评估

### 重构范围

`_run_vlm`（当前 97 行，`run.py:438–533`）的 D2 + D3 改动将修改以下区域：

| 区域 | 当前行数 | 改动幅度 | 说明 |
|------|---------|---------|------|
| 循环前（`for i, page in enumerate(all_pages):`） | 438-474 初始化 | 小 | 增加 `failed_pages`, `backoff` 等变量 |
| 循环体（核心识别） | 487-505 | **中-大** | `try/except Exception: continue` → `retry_with_policy` × 2 + `except RetryExhaustedError` + OverSize 重 OCR |
| 循环体（D3 缓存） | 同上 | **中-大** | 循环体顶部加入缓存检查（hit → skip），底部加入缓存写入 |
| 循环后（`apply_leakage_defense`） | 512-531 | 小 | `failed_pages` 日志输出 |

### 评估结论

**总体风险：低-中。** 重构方向正确，范围清晰，但有 3 点需注意：

1. **循环体复杂度将从 ~18 行增至 ~50 行**（含 D2 + D3）。建议考虑将"单页处理逻辑"提取为独立函数（如 `_process_vlm_page`），保持主循环可读性：
   ```python
   def _process_vlm_page(
       page: fitz.Page, page_num: int, vlm, baseline, cfg, supports_two_page: bool,
       all_pages: list, i: int, failed_pages: dict
   ) -> str | None:
       """处理单页 VLM OCR，含重试、字数校验和 OverSize 重 OCR。返回文本或 None（跳过）。"""
   ```
   这样主循环保持清晰，缓存逻辑（D3）和重试逻辑（D2）各自独立。

2. **D2 + D3 必须同人同次实施。** Plan 已正确识别并建议了这一点。如果拆为两次 PR，中间状态的 `_run_vlm` 会有一段不稳定的过渡期（D2 改完的循环在 D3 缓存到来前没有断点续跑能力，但结构已变）。

3. **Lambda 捕获在调试时不够友好。** `retry_with_policy` 当前设计接受 `Callable`，D2 使用 `lambda` 包裹 adapter 调用。当抛出异常时，堆栈跟踪只会显示 `<lambda>` 而不会显示调用上下文。建议：
   - 使用 `functools.partial` 替代 lambda（更清晰的 `__name__`）
   - 或确保 `retry_with_policy` 的日志足够详细以定位问题

---

## 3. 剩余代码质量问题

### D0 — Config 扩展

| 项目 | 状态 |
|------|------|
| `kzocr_output_dir` 字段使用 `field(default_factory=...)` | ✅ 正确 |
| `from_env()` 未显式设置该字段 | ✅ 可接受（依赖 `default_factory` 读取 `KZOCR_OUTPUT_DIR` 环境变量） |
| 加载时机：`load_config()` 模块级调用 | ✅ 与其他环境变量一致 |

**注意：** `from_env()` 和 `load_config()` 之间存在"双通道"问题——`from_env` 使用 `field(default_factory=...)` 读取环境变量，而 `load_config` 直接对已有字段赋值（`cfg.use_mock = ...`）。`kzocr_output_dir` 走了前一条路径，行为与其他字段一致，无兼容风险。

### C1 L3 移除

Plan 第 277-288 行正确描述了 `apply_leakage_defense` 的修改范围：
- L1（基线检测）保留 ✓
- L2（max_tokens 物理上限）保留 ✓
- L3（日志标记重 OCR）移除 ✓
- L4（探针重叠检测）保留 ✓

**验证：** 检查 `leakage.py` 当前代码确认 L3 的日志标记位置——确认后实现时修改即可。需注意对应测试文件的微调（plan 实施注意事项 #4 已识别）。

### 测试计划

Round 6 要求 D2 的重试耗尽场景测试（约 15 个用例）。Plan 第 173-179 行已列出，覆盖：
- ApiError 退避重试 → 第 3 次成功 ✓
- OverSizeError 重 OCR → 成功 ✓
- OverSizeError 重 OCR → 仍超 → 抛出 RetryExhaustedError → 跳过 ✓
- 所有重试耗尽 → `failed_pages` 正确记录 ✓
- D2 使用 `retry_with_policy` → 验证 `errors.py` 无 dead code ✓

**建议补充：**
- 验证 `on_exhausted` 回调在耗尽时被正确调用（修复问题 C 后）
- 验证 `RetryExhaustedError.__cause__` 包含原始异常（修复问题 B 后）
- 验证 D0 路径穿越校验：`atomic_write(cache_path, text, allowed_base=cfg.kzocr_output_dir)`

---

## 总体评分

| 维度 | 评分 |
|------|------|
| Round 6 must-fix 已修复（3/3） | ✅ 全部修复 |
| `retry_with_policy` 集成至 D2 | ⚠️ 需修复问题 B（异常抛出语义） + 问题 C（on_exhausted 参数） |
| `RetryPolicy` 与 `ExponentialBackoff` 设计整合 | ⚡ 建议修复（字段重复） |
| `_run_vlm` 重构风险 | 低-中（建议提取子函数降复杂） |
| D3 缓存路径 + 引擎标签 | ✅ 正确 |
| C1 L3 移除范围 | ✅ 正确 |
| 测试计划覆盖 | ✅ 完整（建议新增问题 B/C 相关用例） |
| 向后兼容性 | ✅ 良好（零破坏性变更） |

**最终结论：有条件通过。** Round 6 的 3 项 must-fix 已得到正确修复，但引入了一项新的一致性问题（问题 B 和 C）。建议修复这两项后再进入实现阶段。问题 A 不是阻塞项，可在实现时一并处理。
