# KZOCR v0.5 AMEND — 软件工程评审

**结论：有条件通过。** D1–D4 整体方向正确，但 D1 与 D2 之间存在设计不一致（`retry_with_policy` 创建后未被 D2 使用），且 D2 存在 3 项需要先行修复的缺陷。建议 D1/D2 合并统一后再进入实现。

---

## D1 — 异常分类 + retry_with_policy

**整体评价：** 异常继承体系层级合理（1 基类 + 7 叶类型，无中间抽象层，无过度工程）。`OcrError` 作为所有异常的基类，调用方可以用单个 `except OcrError` 捕获所有分类异常。但 `retry_with_policy` 的设计存在 3 个问题。

### ✅ 正确之处

- 继承树只有 2 层（基类 → 叶类型），没有不必要的中间层 —— 避免了"为未来的异常做抽象"的过度工程陷阱。
- 7 种异常类型各自对应一种可区分的失败模式，命名与领域问题对应（`RateLimitedError`、`OverSizeError`、`LeakageError` 等）。
- `ExponentialBackoff` 在 `ratelimit.py:44-76` 已实现且单元测试覆盖（`test_ratelimit.py:18-41`，4 项测试），复用而非重写是正确的。

### ❌ 需修复

**1. `retry_with_policy` 签名使用 `dict` 作为策略参数（弱类型契约）**

```
def retry_with_policy(fn, policy: dict, error_types: tuple[type] = (ApiError,)):
```

`policy: dict` 没有类型约束 —— 调用方可以传入任意 key，IDE 无补全、mypy 不报错。建议改为 `@dataclass` 或 `TypedDict`：

```python
@dataclass
class RetryPolicy:
    max_retries: int = 0
    base_delay: float = 2.0
    max_delay: float = 300.0
    jitter: float = 0.5
    respect_retry_after: bool = False
    modify_params: Callable | None = None  # OverSizeError 场景：修改调用参数后重试

def retry_with_policy(fn, policy: RetryPolicy, error_types: tuple[type] = (ApiError,)):
    ...
```

这样每个策略项都明确其类型，调用方无法拼错 key。

**2. `OverSizeError` 的"重 OCR"语义不兼容统一 retry 模式**

政策表显示 OverSizeError 的策略是"重 OCR, 1 次, max_tokens×1.8"。但这不是"重试相同调用" —— 而是"用不同参数重新调用"。`retry_with_policy` 的 `fn` 是固定签名的 callable，无法表达"自动 ×1.8"。D2 代码片段也确实把 OverSizeError 逻辑直接内联在 `_run_vlm` 循环中，没有走 `retry_with_policy`。

建议：
- 将 OverSizeError 的处理从 `retry_with_policy` 的职责范围中移除，只在 `_run_vlm` 循环中内联处理（当前 D2 的做法是正确的）。
- 政策表中 OverSizeError 行添加注释 `※ 内联处理，不走 retry_with_policy`，避免读者困惑。

**3. `retry_with_policy` 未与 `ExponentialBackoff` 实例集成**

```python
# 最终调用者需要：
backoff = ExponentialBackoff(base_delay=2.0, max_retries=3)
retry_with_policy(fn, policy={"max_retries": 3, "base_delay": 2.0, ...})
# 而不是：
retry_with_policy(fn, backoff)  # 复用已有实例
```

`retry_with_policy` 内部需要指数退避逻辑，但参数是 `dict` 而非 `ExponentialBackoff` 实例 —— 导致 `ratelimit.py` 中已有的经过测试的退避类被绕过，`retry_with_policy` 需要自行实现同样的退避计算。**这不是复用，是重复。**

建议签名改为：

```python
def retry_with_policy(
    fn,
    backoff: ExponentialBackoff | None = None,
    error_types: tuple[type] = (ApiError,),
) -> Any:
```

默认可使用 `ExponentialBackoff()` 作为缺省。这样：
- `ExponentialBackoff` 的随机抖动、max_delay 封顶、jitter 全部透传
- 单元测试只需测 `ExponentialBackoff`（已有 4 项测试）和 `retry_with_policy` 的编排路径（成功/失败/耗尽）

**4. 次要：未定义异常 `__init__` 签名**

```python
class OcrError(Exception):
    def __init__(self, message: str, page_num: int | None = None):
        self.page_num = page_num
        super().__init__(message)
```

不定义 `__init__` 则 D2 中的 `_record_failure(..., type(exc).__name__)` 只能记类名，无法记录 `page_num` 等结构化上下文。建议统一定义。

**5. 次要：`OcrSkipError` 命名反向**

`OcrSkipError` 表示"重试耗尽，跳过"，但名称读起来像"这是一个跳过指令"而非"这是一次跳过报告"。检索 `test_ratelimit.py:18-20` 的 `ExponentialBackoff` 示例中的命名风格，建议 `RetryExhaustedError` 或 `PageSkippedError`，更清晰地表达它是**重试耗尽的结果**而非**跳过指令**。

### 🟡 向后兼容

- 新增模块 `kzocr/engines/errors.py`，零侵入现有代码。
- `kzocr/engines/__init__.py` 只需新增一行 `from .errors import OcrError` 等导出。
- 现有 `test_ratelimit.py` 的 18 项测试零影响。

---

## D2 — VLM 主循环重试 + 失败分类增强

**整体评价：** 增强方向正确（当前 `run.py:503-505` 的 `except Exception: continue` 确需分类处理），但实现设计存在 3 项需要对齐的问题。

### ✅ 正确之处

- 对 `RateLimitedError` 和 `ApiError` 做区分处理是正确的 —— 限流需要尊重 `Retry-After`（`ratelimit.py:169-178` 已有 `report_error` 的 status_code 分支），而普通 API 错误只需指数退避。
- `continue` 之前写 `_record_failure` 记录失败日志，解决了"所有失败静默跳过"的问题。

### ❌ 需修复

**1. D2 的内联循环未使用 D1 的 `retry_with_policy`（设计不一致）**

D1 创建了 `retry_with_policy` 作为统一的"指数退避重试"抽象，但 D2 的 `_run_vlm` 增强代码完全手工展开：

```python
for attempt in range(1, RETRY_POLICIES["api"]["max_retries"] + 1):
    try:
        text = vlm.recognize_pages(imgs)
        if baseline.ready and len(text) > baseline.threshold:
            raise OverSizeError(...)
        break
    except RateLimitedError:
        backoff.sleep(attempt + 1)
    except (ApiError, OcrSkipError) as exc:
        logger.warning(...)
        if attempt < max_retries:
            backoff.sleep(attempt + 1)
else:
    logger.error(...)
    _record_failure(...)
    continue
```

这个循环本质上就是 `retry_with_policy` 要做的事 —— 但没调用它。导致：
- `retry_with_policy` 没有消费者（代码写出来但无人使用，成为 dead code）
- `_run_vlm` 需要自行处理 `ExponentialBackoff` 的 `sleep` 调用和 attempt 计数
- 两套重试逻辑并存，未来维护者需要理解两个模式

**建议：** 统一设计。`_run_vlm` 的 API 重试部分调用 `retry_with_policy`，`OverSizeError` 的"换参数重 OCR"作为特例仍保留内联：

```python
try:
    text = retry_with_policy(
        lambda: vlm.recognize_pages(imgs),
        backoff=ExponentialBackoff(base_delay=2.0, max_retries=3),
        error_types=(ApiError, RateLimitedError),
    )
    # L3: OverSizeError 特例处理（不适合通用 retry 模式）
    if baseline.ready and len(text) > baseline.threshold:
        text = vlm.recognize_pages(imgs, max_tokens=int(baseline.median * 1.8))
        if len(text) > baseline.threshold:
            logger.warning("L3 重 OCR 仍超阈值，继续使用结果")
    break
except RetryExhaustedError as exc:
    logger.error("第 %d 页重试耗尽，跳过", i + 1)
    _record_failure(i + 1, type(exc).__name__)
    continue
```

**2. `_record_failure` 未定义且 `pipeline_state` 未引入**

D2 代码片段引用了 `_record_failure(pipeline_state, i + 1, type(exc).__name__)`，但：
- `pipeline_state` 在 `_run_vlm` 中未定义（当前函数签名 `_run_vlm(pdf_path, cfg, book_code)`）
- `_record_failure` 的返回值/存储位置未说明（内存 dict？JSON 文件？数据库？）
- 对 D3 有影响 —— 如果在 `_run_vlm` 中断恢复时需要知道"哪些页失败了"，当前无持久化方案

**建议：**
- 在 `_run_vlm` 顶部初始化 `failed_pages: dict[int, str] = {}`（`page_num → error_type`）
- `_record_failure` 改为 `failed_pages[i + 1] = type(exc).__name__`
- 函数返回时，如果 D3 断点续跑需要持久化失败记录，这部分应作为 D3 的扩展（在 `_run_vlm` 末尾将 `failed_pages` 写入 `{output_dir}/vlm_cache/{book_code}/_failed.json`）

**3. `RateLimitedError` 未尊重 `Retry-After` 头**

D1 政策表写明 `RateLimitedError: 3 次, 尊重 Retry-After`，但 D2 循环只调了 `backoff.sleep(attempt + 1)`，未添加 `respect_retry_after` 逻辑。`ExponentialBackoff` 当前是纯时间驱动（`ratelimit.py:64-75`），不支持 `Retry-After`。

`AdaptiveRateLimiter.report_error(429)`（`ratelimit.py:169-178`）会翻倍间隔，但它在调用前阶段（`_run_vlm` 的调用者一侧）使用，不在重试循环内。

**建议：** 如果确实要尊重 `Retry-After`：
- 在 `ExponentialBackoff` 上增加可选的 `retry_after: float | None` 参数，调用方收到 429 时解析 header 传入
- 或者注明：KZOCR 当前的 API 调用链路不暴露 HTTP header（通过 adapter 抽象），`respect_Retry-After` 降级为 `ExponentialBackoff` 的指数退避即可

**修复后优先级：** 属于 P1.5（建议而非阻塞），如果当前 API 链路确实无法暴露 HTTP header，降级为纯指数退避是可接受的。

### 🟡 向后兼容

- `_run_vlm` 签名不变（`pdf_path, cfg, book_code`），调用方零迁移。
- 行为变化：原来 `except Exception: continue` 对所有失败做相同处理；改动后 `ApiError` 会重试 3 次才跳过。这是行为增强，不破坏向后兼容。
- 但变更后 `RateLimitedError` 子类从 `except Exception` 中分离，如果现有测试 mock 了 VLM adapter 抛出 `Exception`（而非 `RateLimitedError`），测试可能需要更新。

---

## D3 — VLM 断点续跑

**整体评价：** 设计简单正确（文件存在 = 状态），与 C2 `is_complete()` + `atomic_write()` 的集成无缝隙。

### ✅ 正确之处

- `is_complete(cache_path)` 做断点检测（`atomic.py:79-85`），`atomic_write(cache_path, text)` 做缓存写入 —— 复用已有的、经测试的工具函数。
- 缓存目录 `vlm_cache/{book_code}/` 不自相覆盖，不同书互不影响。
- `KZOCR_CLEAR_CACHE=1` 环境变量做缓存清除，简单可预测。

### ❌ 需修复

**1. 缓存路径不含引擎标识 —— 切换 VLM 引擎会静默复用旧缓存**

```python
def _get_vlm_cache_path(cfg, book_code: str, page_num: int) -> Path:
    """{output_dir}/vlm_cache/{book_code}/page_{page_num:04d}.txt"""
```

如果用户先跑 SenseNova（缓存 10 页），然后切到 PaddleOCR-VL-1.6（或修改了 `vlm_host`/`vlm_port`），缓存不会被清除 —— 旧缓存会静默复用，导致用户以为结果是新引擎产出。

**建议：** 缓存路径中编码引擎标签：

```python
engine_tag = getattr(vlm, "engine_label", "unknown")
# 路径: vlm_cache/{engine_tag}/{book_code}/page_{num:04d}.txt
```

或者在 `book_code` 之后追加 `_config_hash`（VLM 配置的 SHA256 前缀），当配置变化时缓存自动失效。

**修复后优先级：** P1（实现 D3 时必须一并完成）。静默复用错引擎缓存是数据质量 bug。

**2. `is_complete` 的"非空"条件偏弱**

```python
def is_complete(path: Path) -> bool:
    return path.exists() and path.stat().st_size > 0
```

如果缓存文件存在但内容只有 1 个字符（如换行符 `\n`），`is_complete` 返回 `True`，后续处理 `cache_path.read_text()` 得到的是无效空文本。建议对 VLM 缓存增加最小长度检查：

```python
def is_vlm_cache_complete(path: Path) -> bool:
    """VLM 缓存文件至少包含有意义的内容（>2 个非空白字符）。"""
    if not is_complete(path):  # 复用现有 is_complete
        return False
    text = path.read_text(encoding="utf-8").strip()
    return len(text) > 2
```

这部分可以在 `run.py` 中内联，不需要修改 `atomic.py`。

**3. 次要：缺少缓存空间管理策略**

`KZOCR_CLEAR_CACHE=1` 是全局开关（清除所有缓存），没有增量清理机制。对处理数千本书的系统，`vlm_cache/` 目录会持续增长。建议：不是 P2 阻塞项，但在实现时在文档中注明 "TODO: 添加 LRU 缓存淘汰（计划阶段 6）"。

### 🟡 向后兼容

- 缓存目录 `vlm_cache/` 是新增子目录，不影响已有 `output_dir/` 下的其他文件。
- 环境变量 `KZOCR_CLEAR_CACHE` 新增，无冲突。
- `is_complete` 和 `atomic_write` 函数签名不变，零影响。

---

## D4 — 层级异常检测

**整体评价：** 设计干净、范围有限（P3），可独立于 D2/D3 实现。零向后兼容风险。

### ✅ 正确之处

- `@dataclass HierarchyAnomaly` 设计简洁，5 字段够表达检测结果。
- 输出到独立 JSON 文件而非侵入 `BookResult` 结构，职责分离清晰。
- "P3 低优先 + 与 TOC 管线阶段合并实施"的放置合理。

### ❌ 需修复

**1. `"段数 > 2 的编号"` 可能产生假阳性（领域层面的过度泛化）**

`check_hierarchy_anomaly` 的检测逻辑是"扫描所有编号，段数 > 2 即报异常"。但中医方剂中，三级编号（如 `16.7.1`）可能对应"主方下的加减方"或"同一节下的小变方" —— 这些是合法结构，不是异常。TOC 项目 970 页的经验应被引用来判断这个假阳性率。

如果不是做修复，而是做"标记"（flag for review），建议：
- 函数名改为 `find_nested_numbers`（中性，不预设"异常"）
- 输出中的 `resolution: str = "pending"` 改为 `flagged: bool = True`
- 在 JSON 输出的注释或 README 中说明此检测是"可疑标记"而非"确定异常"

**2. 次要：`HierarchyAnomaly` 缺少 `recipe_name` 字段**

仅有 `recipe_no: str`（编号如 `16.7.1`），但编号相同的异常放到一起时，如果没有对应方剂名称，人类 reviewer 无法定位问题页面。建议添加可选字段：

```python
@dataclass
class HierarchyAnomaly:
    recipe_no: str
    recipe_name: str = ""  # 取编号所在行文本的前 10 个中文字
    depth: int
    source_page: int
    flagged: bool = True
```

---

## 跨项发现

### 1. D1 的 `retry_with_policy` 无消费者；D2 的循环无调用者（设计不一致，评分：⚠ 必须修复）

如果按当前设计实现：D1 创建 `kzocr/engines/errors.py` + `retry_with_policy`，D2 在 `run.py` 内联展开。那么 `retry_with_policy` 是一个无任何调用点的抽象 —— 代码被写出但未使用。这是"为未来预留抽象"的反模式。

**建议统一路径：**
1. `errors.py` 保持不变（7 种异常类型）。
2. `retry_with_policy` 搬入 `ratelimit.py`（与 `ExponentialBackoff` 同文件，复用其能力）。
3. D2 的 `_run_vlm` 增强：API 重试调用 `retry_with_policy(backoff=ExponentialBackoff(...))`；`OverSizeError` 的重 OCR 保留内联。
4. 删除 D2 代码片段中的 `for attempt in range(...)` 展开。

### 2. C1 L3 日志与 D2 OverSizeError 重 OCR 重复（评分：⚠ 必须修复）

`leakage.py:192` 的 `apply_leakage_defense` 中 L3 部分已经有：

```python
logger.info("[leakage] L3: P%d 建议重 OCR（max_tokens=%.0f）", i + 1, char_count * 0.5)
```

而 D2 在 `_run_vlm` 中又做了一次 `if baseline.ready and len(text) > baseline.threshold: raise OverSizeError(...)`。这两者检测的是相同的条件（字符数超阈值），但：
- `leakage.py` 只记录日志（`建议重 OCR`），不执行重 OCR
- D2 实际执行重 OCR
- 结果：同一条件触发两条日志（`[leakage] L3` + `[VLM] OverSizeError`），用户困惑

**建议：** 修改 `apply_leakage_defense` 使其在 D2 落地后不再对超出阈值的情况记 L3 日志（因为 D2 已经处理了），或者将 `apply_leakage_defense` 的 L3 日志改为 debug 级别。

### 3. 测试计划未覆盖 D2 重试耗尽场景（评分：⚡ 建议修复）

D1 测试要求提到"`retry_with_policy` 成功/失败/耗尽三种路径"，但 D2 的重试耗尽路径（`_record_failure` + `continue`）在 D1 中没有直接覆盖 —— D1 的 `retry_with_policy` 抛出 `RetryExhaustedError`（假设），D2 捕获它并记录。测试用例需要：
- mock VLM adapter 在 3 次调用中全部失败 → 断言 `_record_failure` 被调用
- 混合成功/失败 → 断言最终成功
- `OverSizeError` 重 OCR 后依然超阈值 → 断言继续使用结果（而非 `continue`）

建议 D2 在实现时明确这些测试用例。

---

## 向后兼容性总结

| 考虑点 | 影响 |
|--------|------|
| `_run_vlm` 函数签名 | 不变，零影响 |
| `run_engine` 入口 | 不变，零影响 |
| 现有 `test_ratelimit.py` 18 项测试 | 零影响（新增文件/函数，未改现有） |
| `is_complete` / `atomic_write` | 函数签名不变，零影响 |
| `kzocr/engines/__init__.py` 导出 | 新增异常类导入，不影响已有 `from kzocr.engines.ratelimit import ...` |
| 缓存目录 `vlm_cache/` | 新增子目录，不影响已有 output_dir 结构 |
| `KZOCR_CLEAR_CACHE` 环境变量 | 新增，无冲突 |
| 若 `_run_vlm` 在 D2 前已有测试 mock | 需确认 mock 抛出 `Exception` 还是特定的异常子类 —— 若是 `Exception`，D2 改动可能改变测试断言 |

**结论：向后兼容性良好。** 全部修改均为新增或增强，不删除或更改任何已有函数签名。

---

## 实施顺序修正建议

当前 v0.5 AMEND 的优先级表：
```
| P1 | D1 异常分类 + retry_with_policy | kzocr/engines/errors.py (新建) |
| P1 | D1 retry_with_policy + 测试       | kzocr/engines/ratelimit.py (小改) |
| P1 | D2 VLM 主循环重试 + 失败分类    | kzocr/engine/run.py (改) |
| P2 | D3 VLM 断点续跑                 | kzocr/engine/run.py (改) |
| P3 | D4 层级异常检测                  | kzocr/engines/hierarchy.py (新建) |
```

**建议调整为：**

| 优先级 | 项 | 说明 |
|--------|-----|------|
| **P1** | D1 异常类型（不含 retry_with_policy） | `errors.py` — 仅定义 7 种异常 + `__init__` |
| **P1** | D2 + D1 retry_with_policy 统一 | `retry_with_policy` 搬入 `ratelimit.py`，D2 调用它（消除"抽象无消费者"问题） |
| **P1** | D2 OverSizeError 内联 + 修复 C1 L3 双重日志 | 保留在 `_run_vlm` 内联，修改 `apply_leakage_defense` 的日志级别 |
| **P2** | D3 断点续跑（含引擎标识入缓存路径） | 实现 checkpoint + 引擎标签编码 |
| **P3** | D4 层级异常检测 | 独立、低优先，与 TOC 管线合并 |

这样，`retry_with_policy` 在创建的同时就有消费者被实现，不会出现"写出来但没人用"的阶段。

---

## 最终评分

| 维度 | 评分 |
|------|------|
| 异常继承体系设计（不过度抽象） | ✅ 正确 |
| `retry_with_policy` 模式正确性 | ⚠️ 需修复（dict → dataclass，未复用 ExponentialBackoff） |
| D2 与 D1 的调用关系一致性 | ❌ 需修复（内联展开 vs retry_with_policy 二选一） |
| D2 `_record_failure` 定义完整性 | ❌ 需修复（未定义的函数和变量） |
| D3 缓存路径 + 引擎绑定 | ❌ 需修复（引擎切换时静默复用旧缓存） |
| D4 代码质量 | ✅ 正确（P3 非阻塞） |
| 向后兼容 | ✅ 良好（零破坏性变更） |

**覆盖的 4 项评审问题：**

1. **retry_with_policy 模式是否正确和可复用？** → ⚠️ 方向正确但需修复签名（dict→dataclass）和与 ExponentialBackoff 的集成。与 D2 统一后可作为通用的 API 重试抽象。

2. **异常继承体系是否遵循最佳实践（不过度抽象）？** → ✅ 正确。1 基类 + 7 叶子，仅 2 层，无中间抽象。命名方面 `OcrSkipError` 有轻微歧义，建议 `RetryExhaustedError`。

3. **run.py 改动存在代码质量/可维护性问题吗？** → ❌ 有。D1 创建 retry_with_policy 但 D2 不调用导致无消费者；_record_failure 未定义；C1 L3 日志与 D2 OverSizeError 双重报告。

4. **存在向后兼容问题吗？** → ✅ 无。全部为新增模块和增强，签名不变。
