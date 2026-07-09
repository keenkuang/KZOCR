# KZOCR v0.5 AMEND 架构师评审

- **评审角色**：首席架构师
- **评审日期**：2026-07-10
- **评审对象**：`docs/plans/ocr-engine-unification.v0.5-AMEND.md`（D1–D4，从 TOC 项目 970 页实战吸收的异常处理体系改进）
- **基准方案**：`v0.3-FREEZE.md`（B1–B8）+ `v0.4-AMEND.md`（C1–C5），此处仅评 D1–D4
- **配套代码检查**：`kzocr/engine/run.py`、`kzocr/engines/ratelimit.py`、`kzocr/engines/atomic.py`、`kzocr/engines/leakage.py`、`kzocr/config.py`

---

## 总体裁决：**需修订（Need Revision）**

D1 和 D2 的核心方向（异常分类 + VLM 主循环重试）是正确且必要的补充。当前 `_run_vlm` 的 `except Exception: continue` 架构太粗糙，必须升级。

但存在 **3 项阻断性问题**（Blocking）和 **2 项重大设计问题**（Major），必须在进入实施前修订：

| # | 严重度 | 条目 | 类型 |
|---|--------|------|------|
| 1 | **Blocking** | D3 引用不存在的 `cfg.kzocr_output_dir`，Config 类无此字段 | 架构缺陷 |
| 2 | **Major** | D1 异常体系 ~50% 暂不需要（4/8 子类无消费者），违反 YAGNI | 过度设计 |
| 3 | **Major** | D1 定义 `retry_with_policy()` 但 D2 完全没用它——两个重试模式共存 | 架构脱节 |
| 4 | **Major** | D2 OverSizeError 处理逻辑缺陷：重 OCR 仍超阈值后静默使用泄漏结果 | 逻辑缺陷 |
| 5 | **Blocking** | D3 缓存存放在输出目录下，与 C2 文件即状态哲学冲突 | 设计冲突 |

---

## 1. D1 —— 异常分类体系评估

### [D1-1 · Major] 异常体系过度设计：8 子类中 4 个目前无消费者

当前 KZOCR 中实际用到的异常类型分析（基于 v0.5 D2 及现有代码）：

| 异常类型 | D2 中是否使用 | 当前 KZOCR 是否有其他消费者 | 裁决 |
|----------|-------------|--------------------------|------|
| `ApiError` | ✓（D2 主循环捕获） | — | **需要** |
| `RateLimitedError` | ✓（D2 主循环捕获，`backoff.sleep`） | ratelimit.py 中引用 | **需要** |
| `OverSizeError` | ✓（D2 L3 重 OCR 触发点） | — | **需要** |
| `OcrSkipError` | ✓（D2 重试耗尽后标记跳过） | — | **需要** |
| `LeakageError` | ✗ 未使用 | leakage.py 只 log+trim，不抛异常 | **暂不需要** |
| `CrossPageIncompleteError` | ✗ 未使用 | 计划未说明用途 | **暂不需要** |
| `HierarchyAnomalyError` | ✗ 未使用（D4 是 P3 deferred） | D4 本身就是可选 | **暂不需要** |
| `DbWriteError` | ✗ 未使用 | VLM 路径不写 DB | **暂不需要** |

**4/8 的子类（50%）目前无任何消费者。** `LeakageError`、`CrossPageIncompleteError`、`HierarchyAnomalyError`、`DbWriteError` 在 D2 主循环中既不捕获也不抛出。在前 TOC 项目中有意义，但在 KZOCR 当前架构层无对应触发点。

**修改建议**：遵循 YAGNI 原则，初始提交只保留 D2 实际使用的 4 个子类：
```
OcrError (Exception)
├── ApiError
│   └── RateLimitedError
├── OverSizeError
└── OcrSkipError
```
其他 4 个子类（`LeakageError`、`CrossPageIncompleteError`、`HierarchyAnomalyError`、`DbWriteError`）**留作注释但不要提交到代码**。在有明确的抛出点和捕获需求时再增量添加。这不影响将来的扩展性——`OcrError` 基类已经保证了 isinstance 检查的向后兼容。

### [D1-2 · Low] `ApiError` / `RateLimitedError` 的父子关系语义

`RateLimitedError` 继承自 `ApiError`，这在 OOP 上是合理的（限流是 API 错误的子集）。但需要注意：

- D2 捕获顺序：必须先捕获 `RateLimitedError`，再捕获 `ApiError`，否则 `RateLimitedError` 永远不会被触发
- `ApiError` 包含 HTTP 错误（4xx/5xx）和超时，`RateLimitedError` 仅 429/503。这决定了重试策略不同——`ApiError` 退避重试 3 次，但 `RateLimitedError` 需要尊重 Retry-After header

D2 的伪代码中 `RateLimitedError` 用的是 `backoff.sleep(attempt + 1)` 而非 `retry_after + 1s`。这其实**未体现 `RateLimitedError` 与 `ApiError` 的区别**——两者都走了指数退避。如果它们的行为完全一致，那子类化就多余了。

### [D1-3 · Info] OcrError 基类位置

计划说新增 `kzocr/engines/errors.py`。`engines/` 包下已有 `ratelimit.py`、`atomic.py`、`leakage.py`，`errors.py` 放在这里合理——属于引擎层工具，与 C2/C3 同级。

### 逐项裁决

- [ ] **[D1-1] 4/8 子类无消费者**：初始提交只保留 4 个实际使用的子类，其余待需求明确。**需修订。**
- [✓] **[D1-2] 父类-子类捕获顺序**：已识别，非阻断但需在实现文档中注明捕获顺序。**通过。**
- [✓] **[D1-3] 文件位置**：`kzocr/engines/errors.py` 位置合理。**通过。**

---

## 2. D2 —— VLM 主循环重试 + 失败分类评估

### [D2-1 · Major] D1 定义 `retry_with_policy()` 但 D2 完全没用它

D1 计划新增：
```python
def retry_with_policy(fn, policy: dict, error_types: tuple[type] = (ApiError,)):
    """按策略执行 fn；指数退避或幂等重试，耗尽后抛出源异常。"""
```

但 D2 的伪代码是**手写的 `for attempt in range` 循环**，完全没有使用 `retry_with_policy`。这意味着 D1 设计了统一的重试入口，但 D2——唯一的消费者——绕过了它。

具体的问题：
1. **架构脱节**：D1 的 `retry_with_policy` 成为无人调用的孤岛代码（dead code）
2. **测试覆盖冗余**：需要同时测试 `retry_with_policy` 和 D2 的手写循环，两套路径
3. **策略表与实现不符**：重试策略表定义了 `ApiError` 退避 3 次 + `OverSizeError` 重 OCR 1 次，但 D2 循环中对 `RateLimitedError` 不尊重 Retry-After，对 `OverSizeError` 的处理逻辑也有缺陷（见 D2-2）

**修改建议**：
- **方案 A（推荐）**：D2 的重试逻辑应该通过 `retry_with_policy` 实现——将不同的异常类型映射到不同的重试策略，由 `retry_with_policy` 统一调度。D2 的伪代码应当改为调用 `retry_with_policy` 的组合，减少手写循环。
- **方案 B**：如果 D2 必须手写重试（因为 OverSizeError 的处理特殊，需要调用同一个函数的不同参数），则应在 `retry_with_policy` 的参数上增加灵活性，或删除 `retry_with_policy` 不提交（避免 dead code）。

方案 A 更优，因为 `retry_with_policy` 的价值在于被使用。如果连唯一的集成点都绕开它，说明这个抽象层级是错的。

### [D2-2 · Major] OverSizeError 处理逻辑缺陷

D2 的 OverSizeError 处理代码：
```python
except OverSizeError:
    text = vlm.recognize_pages(imgs, max_tokens=int(baseline.median * 1.8))
    if len(text) > baseline.threshold:
        logger.warning("L3 重 OCR 仍超阈值，继续使用结果")
```

这里有 **3 个问题**：

1. **重 OCR 仍超阈值的处理是 silent fallback**：重 OCR 后仍然超阈值，日志打印 warning 后**不抛异常、不标记失败、直接继续使用泄漏结果**。这意味着 L3 重 OCR 的"补救"意图完全无效——最终还是用了泄漏内容。应该要么提高 threshold 再次重试（最多 N 次），要么标记失败走 skip 路径。

2. **重 OCR 的 max_tokens 参数未透传到适配器层**：`vlm.recognize_pages(imgs, max_tokens=...)` 假设适配器支持这个参数。当前 `PaddleOCRVl16Adapter` 和 `SenseNovaAdapter` 的参数签名是否支持 `max_tokens` 需要确认。如果适配器不支持此参数，这行代码会 `TypeError` 并落入外层 `except Exception` 统一处理，导致重试 3 次的 `for attempt` 循环立刻耗尽。

3. **与 C1 L3 的重 OCR 冲突**：C1（leakage.py）的 L3 也在做"超阈重 OCR"。D2 引入的 OverSizeError 实则是 C1 L1 的翻版——两者都在检测字符数超限并触发重试。如果 D2 的 OverSizeError 先触发，C1 的 apply_leakage_defense 后执行，就可能出现"两次重试"的冗余。

**修改建议**：
- D2 的 OverSizeError 重 OCR 失败后不应静默使用结果，应该走 `OcrSkipError` 路径
- 确认 VLM 适配器支持 `max_tokens` 参数，否则需要在适配器层增加兼容层
- 与 C1 协作：明确 D2 OverSizeError 是 C1 L3 的前置补充还是重复，建议二者合并为一个触发点

### [D2-3 · Medium] `_record_failure` 函数未定义

D2 的 `else` 分支调用了 `_record_failure(pipeline_state, i + 1, type(exc).__name__)`，但：
- `_record_failure` 函数在计划中未定义
- `pipeline_state` 变量未定义（是全局 dict？还是 _run_vlm 内部变量？）
- `_run_vlm` 当前不接受 `pipeline_state` 参数

如果没有 `_record_failure`，失败信息只在日志中，无法被上游（`run_engine`）感知。如果 `run_engine` 需要知道有多少页失败（以决定是否降级），目前缺乏这个机制。

**修改建议**：在 `_run_vlm` 中增加局部变量 `_failures: dict[int, str]`，循环结束后返回或记录。或者扩展 `BookResult` 增加 `failed_pages` 字段，让上游可以感知失败比例。

### [D2-4 · Low] 双重重试计数器可能冲突

D2 的伪代码：
```python
for attempt in range(1, RETRY_POLICIES["api"]["max_retries"] + 1):
```

但 `ratelimit.py` 中的 `ExponentialBackoff` 也有 `max_retries`。如果 `ExponentialBackoff` 的 `max_retries=5` 且 D2 的 also `max_retries=3`，实际重试行为取决于哪一层控制终止。D2 的循环已经控制了 max_retries=3，那么 `ExponentialBackoff` 的 `max_retries` 参数在这个场景中是无用的——外层 D2 循环不会超过 3 次，内层 `ExponentialBackoff` 的 5 次上限永远不会耗尽。

更合理的做法是：**用 `ExponentialBackoff` 计算延迟、用 D2 循环控制次数**，或者完全统一。

### 逐项裁决

- [ ] **[D2-1] `retry_with_policy` 未被 D2 使用**：D1 的辅助函数与 D2 的集成点脱节。**需修订（推荐方案 A：让 D2 真正使用 retry_with_policy）。**
- [ ] **[D2-2] OverSizeError 处理逻辑缺陷**：重 OCR 失败后静默使用泄漏结果 = 无用功。**需修订。**
- [ ] **[D2-3] `_record_failure` 未定义**：失败信息无法被上游感知。**需修订。**
- [✓] **[D2-4] 双重计数器**：非阻断，但建议统一为单一控制点。**通过（留意项）。**

---

## 3. D3 —— VLM 断点续跑 + 缓存策略评估

### [D3-1 · Blocking] `cfg.kzocr_output_dir` 在 Config 类中不存在——断点的存放位置未定义

D3 的伪代码：
```python
cache_dir = cfg.kzocr_output_dir / "vlm_cache" / safe_book_code
```

但 `kzocr/config.py` 的 `Config` 类**没有 `kzocr_output_dir` 字段**。当前 Config 仅有：
- `kimi_engine_dir` → kimi BookPipeline 的引擎目录
- `zai_dir` / `zai_db` → zai 控制台路径
- `khub_base_url` / `khub_db` → kHUB 路径
- 各种 VLM/SenseNova API 配置
- `use_mock` / `require_real` / `use_vlm` 等布尔开关

VLM 路径的 `_run_vlm()` **根本没有文件输出步骤**——它返回 `BookResult` 对象，调用方（`run_engine()`）负责后续处理。VLM 路径没有"输出目录"的概念。

这会使 D3 无法实施，除非：

**方案 A（推荐）**：在 Config 中新增 `kzocr_output_dir`，映射环境变量 `KZOCR_OUTPUT_DIR`（如 `/tmp/kzocr/output`），默认值为系统 tmp。该目录用于：
- D3 的 `vlm_cache/` 子目录（VLM 断点缓存）
- 后续可能的分页输出（C1 L3 重 OCR 产物等）

**方案 B**：VLM 缓存存到与 `_run_vlm` 调用者的工作目录下（如 `{pdf_dir}/.vlm_cache/`），避免新增 Config 字段。但 `pdf_dir` 不一定是持久化目录，可能位于临时路径。

方案 A 更清晰，且与 `kzocr_output_dir` 的语义（KZOCR 自己的输出，非引擎输出）一致。

**注意**：如果采用方案 A，需要确保 `KZOCR_OUTPUT_DIR` 也经过 C2 路径穿越校验（`_check_base`），防止任意路径写。

### [D3-2 · Blocking] D3 缓存策略与 C2 文件即状态哲学冲突

C2（atomic.py）的设计哲学是：**文件存在且非空 = 已完成**。D3 用 `is_complete(cache_path)` 检查缓存文件的存在性来判断是否跳过。

冲突点在于：**缓存文件是瞬态产物，不是最终交付物**。

如果：
1. 第 1 次运行：VLM 处理了 30 页，缓存了 30 个 `page_*.txt` 文件
2. 第 2 次运行：用户修改了 VLM 设置（如 `max_tokens`、`vlm_engine`），重跑时 D3 看到 30 个缓存文件全部 `is_complete` → 跳过 → 使用旧缓存 → 用户的新设置未生效

更隐蔽的场景：用户切换 VLM 引擎从 SenseNova 到 PaddleOCR-VL，但缓存文件已有 → D3 直接跳过 → 完全不会调用新的引擎。

C2 的"文件存在即状态"适用于**最终产物**（一旦生成就不需要重新计算），但 D3 的缓存是**中间产物**，其有效性取决于产生它的上下文（引擎版本、参数、模型版本等）。

**修改建议**：
1. **缓存键不仅依赖页码，还依赖 VLM 参数的哈希**：`page_0001_{config_hash}.txt`，当任何影响输出的参数变化时，缓存自动失效
2. 或者**在缓存文件中存储元数据头**：首行记录 `# engine=PaddleOCR-VL-1.6 max_tokens=2048`，重启时校验匹配
3. 或者更简单的：**`KZOCR_CLEAR_CACHE=1` 是废弃缓存的唯一方式，但默认不应将缓存文件等同为"已完成"**——在 `_run_vlm` 中，即使缓存命中，也应该验证参数一致性

### [D3-3 · Medium] D3 缓存路径硬编码 `vlm_cache` 子目录名，上游无感知

计划定义：
```python
cache_dir = cfg.kzocr_output_dir / "vlm_cache" / safe_book_code
```
`"vlm_cache"` 是硬编码的。如果未来有其他模块也需要缓存（如 TOC 管线），会有多个缓存目录分散在 `kzocr_output_dir` 下，缺乏统一的生命周期管理。

**修改建议**：将 `"vlm_cache"` 提取为常量或在 Config 中配置：
```python
VLM_CACHE_DIR = "vlm_cache"
```
或：
```python
cfg.vlm_cache_dir = "vlm_cache"  # 可配置
```

### 逐项裁决

- [ ] **[D3-1] `kzocr_output_dir` 不存在**：阻断。需新增 Config 字段或更改缓存存储策略。**需修订。**
- [ ] **[D3-2] 缓存与 C2 文件即状态哲学冲突**：参数变化时缓存不会失效，导致用户设置被静默忽略。**需修订。**
- [✓] **[D3-3] 硬编码子目录名**：非阻断，建议提取为常量。**通过（留意项）。**

---

## 4. D4 —— 层级异常检测（P3 低优先）

### [D4-1 · Info] D4 依赖 D1 的 HierarchyAnomalyError 但 D4 是 P3 低优先

如果采纳 D1 修订建议（初始只保留 4 个异常类型），则 `HierarchyAnomalyError` 不参与初始提交。D4 的 `hierarchy.py` 也不会在初始提交中实现。这与计划中的 P3 优先级一致——在 D1 初始实现中跳过 `HierarchyAnomalyError` 没有影响。

### [D4-2 · Info] 三级编号在中医方剂中是否确实为异常的领域问题

D4 说"三级编号表示 4 级目录结构，超出正常 2 段编号"。这个假设需要在领域侧验证——某些古籍可能确实有三级编号的目录结构。不过此问题属领域评审范畴，架构侧仅做记录。

### 逐项裁决

- [✓] **[D4-1] D4 依赖关系**：D4 作为 P3 deferred 项，不阻塞 D1–D3。**通过。**
- [✓] **[D4-2] 三级编号的领域正确性**：已记录，请领域评审确认。**通过（转领域评审）。**

---

## 5. D1–D4 之间的架构冲突

### [冲突-1 · Medium] D2 的 `retry_with_policy` 与 D1 定义不匹配

如 D2-1 所述，D1 创建了 `retry_with_policy` 统一入口，但 D2 绕过了它。这本质上不是"冲突"而是"脱节"。解决方式见 D2-1 的建议。

### [冲突-2 · Medium] D2 OverSizeError 重 OCR 与 C1 L3 重 OCR 重复

C1（v0.4）的 L3 已经在做"超阈自动重 OCR（最多重试 1 次）"。D2 的 OverSizeError 也是超阈检测 + 重 OCR。两个机制：

| 方面 | C1 L3 | D2 OverSizeError |
|------|-------|-----------------|
| 触发时机 | `apply_leakage_defense`（所有页 OCR 完成后） | `_run_vlm` 逐页循环中（实时） |
| 检测方式 | `char_count > baseline.threshold` | `len(text) > baseline.threshold` |
| 重试方式 | 日志提示（不实际重 OCR） | `vlm.recognize_pages(imgs, max_tokens=...)` |
| 结果 | 仅日志标记 | 实际调用新的 OCR |

C1 L3 实际上只是日志标记，**不做实际的重 OCR**（见 `leakage.py:192`：`logger.info("[leakage] L3: P%d 建议重 OCR...")`）。D2 的 OverSizeError 才是实际执行重 OCR 的地方。

这意味着 C1 L3 的"日志标记"功能在 D2 到来后将变得多余——D2 已经实时处理了超阈页面，不会留给 C1 execute 阶段。但反过来，如果 D2 的 pass 被 C1 的阈值检测再次触发，就会产生**二次日志警告**。

**修改建议**：在 D2 实施后，C1 L3 应当从"日志标记重 OCR"改为只做 L4 探针检测（不重复标记）。或者将 C1 L1 的阈值检测在 D2 的 OverSizeError 处理之前做一次，避免两个阶段各检测一次。

### [冲突-3 · Low] D3 缓存文件跳过 vs. C2 路径穿越防御

D3 计划使用 `atomic_write(cache_path, text)` 写入缓存。`atomic_write` 目前有可选的 `allowed_base` 参数做路径穿越防御（C2 `_check_base`）。

如果 D3 的缓存路径 `cfg.kzocr_output_dir / "vlm_cache" / safe_book_code` 是通过允许基目录校验的，需要在调用 `atomic_write` 时传入 `allowed_base=cfg.kzocr_output_dir`。

### 逐项裁决

- [ ] **[冲突-1] D1 `retry_with_policy` 与 D2 脱节**：见 D2-1。**需修订。**
- [ ] **[冲突-2] D2 OverSizeError 与 C1 L3 重复**：C1 L3 的日志标记功能在 D2 后变得多余。**需修订（明确 C1 L3 的职责边界）。**
- [✓] **[冲突-3] D3 缓存 + C2 路径穿越**：非阻断，只需正确传入 `allowed_base`。**通过。**

---

## 6. 实施顺序分析

### 当前顺序

| 优先级 | 项 | 文件 |
|--------|-----|------|
| P1 | D1 异常类 + retry_with_policy | `kzocr/engines/errors.py` (新建) |
| P1 | D1 retry_with_policy + 测试 | `kzocr/engines/ratelimit.py` (小改) |
| P1 | D2 VLM 主循环重试 + 失败分类 | `kzocr/engine/run.py` (改) |
| P2 | D3 VLM 断点续跑 | `kzocr/engine/run.py` (改) |
| P3 | D4 层级异常检测 | `kzocr/engines/hierarchy.py` (新建) |

### [顺序-1 · High] D3 依赖 Config 扩展，但扩展 Config 不在实施清单中

D3 的实施依赖于 `cfg.kzocr_output_dir` 的存在（见 D3-1），但实施清单中没有"Config 类新增 `kzocr_output_dir` 字段"这个前提步骤。无论 D3 是 P1 还是 P2，这个基础设施步骤必须先做。

**修改建议**：在实施清单中显式增加前提步骤：

```
P0: Config 类新增 `kzocr_output_dir` 字段（映射 KZOCR_OUTPUT_DIR 环境变量）
P1: D1 异常类（4 个实际使用的） + retry_with_policy + 测试
P1: D2 VLM 主循环重试 + 失败分类
P2: D3 VLM 断点续跑（依赖 P0 和 P1）
P3: D4 层级异常检测（可选）
```

### [顺序-2 · Medium] D1 的 `retry_with_policy` 测试应该在 D2 实施之前完成

如果 D2 使用 `retry_with_policy`（按修订建议 D2-1），那么 D1 的测试需要覆盖 `retry_with_policy` 的全部三种路径（成功/失败/耗尽），且必须通过后 D2 才能提交。这是合理的——D1 测试作为 D2 的门闸。

### [顺序-3 · Medium] D2 和 D3 修改同一个函数 `_run_vlm`

D2 和 D3 都会显著修改 `_run_vlm`（行 438-533）。即使实施顺序是先后进行（D2 P1，D3 P2），D3 的重构也需要仔细审视 D2 的改动，避免 diff 冲突。建议这两项由同一个人实施。

### 逐项裁决

- [ ] **[顺序-1] D3 依赖未列入清单的 Config 扩展**：需增加 P0 前提步骤。**需修订。**
- [✓] **[顺序-2] D1 测试作为 D2 门闸**：合理，无需修改顺序。**通过（保持）。**
- [✓] **[顺序-3] D2+D3 修改同一函数**：建议同人实施。**通过（实施建议，非架构问题）。**

---

## 7. 其他架构关注点

### [A-1 · Medium] D2 伪代码中的 `raise OverSizeError(...)` 应在 `break` 语句之前

D2 伪代码中：
```python
if baseline.ready and len(text) > baseline.threshold:
    raise OverSizeError(...)
break
```
但这个 `break` 是关于 `for attempt` 循环的——如果 `raise OverSizeError` 在 `break` 之前执行，`break` 永远不会到达（`raise` 中断控制流）。实际上 OverSizeError 被 `try/except` 捕获后，`break` 不会执行。这段代码目前无语法错误（因为 `raise` 先执行），但逻辑上是"先处理后 break"，值得在伪代码中澄清。

### [A-2 · Low] `RETRY_POLICIES` 全局变量未定义位置

D2 引用了 `RETRY_POLICIES["api"]["max_retries"]`，但这个字典定义在哪里？计划未说明。是：
- 放在 `errors.py` 中？（合理，策略定义靠近异常类型）
- 放在 `ratelimit.py` 中？（合理，策略涉及重试行为）
- 放在 `run.py` 中？（不合理，`run.py` 是消费者不是策略定义者）

**修改建议**：`RETRY_POLICIES` 应定义在 `errors.py` 中（靠近 `OcrError` 基类），或与 `retry_with_policy` 放在一起。

### [A-3 · Low] D2 未覆盖 `_run_real` 路径的异常增强

D1 的问题描述表中提到了 `_run_real` 的"无重试、无失败原因分类"问题，但 D2 只增强了 `_run_vlm`。`_run_real` 的异常处理计划在哪个阶段升级？

当前 `_run_real`（行 113-147）的异常处理仍然是 `except Exception: raise`（`require_real`）或降级 mock。这不属于 v0.5 的声明范围，但作为架构评审，应指出现有计划的覆盖缺口。

### 逐项裁决

- [✓] **[A-1] `raise` 在 `break` 前**：伪代码顺序需澄清。**通过（文档修正）。**
- [ ] **[A-2] `RETRY_POLICIES` 归属未明确**：建议定义在 `errors.py` 或靠近 `retry_with_policy` 处。**需修订（补充定义位置）。**
- [✓] **[A-3] `_run_real` 未覆盖**：超出 v0.5 范围，但建议记录为未来改进项。**通过（作为备查）。**

---

## 修订建议汇总（定稿前必改）

| # | 严重度 | 条目 | 类型 |
|---|--------|------|------|
| 1 | **Blocking** | **[D3-1]** Config 类缺少 `kzocr_output_dir` 字段，D3 缓存路径未定义。须新增 Config 字段映射 `KZOCR_OUTPUT_DIR`。 | 架构缺陷 |
| 2 | **Blocking** | **[D3-2]** D3 缓存文件存在即跳过的语义与 C2 文件即状态哲学冲突——参数变化时缓存不会失效，用户设置被静默忽略。需增加参数哈希缓存键或元数据校验。 | 设计冲突 |
| 3 | **Major** | **[D1-1]** 初始异常体系包含 4 个无消费者的子类（LeakageError / CrossPageIncompleteError / HierarchyAnomalyError / DbWriteError），违反 YAGNI。初始提交只保留 4 个实际使用的子类。 | 过度设计 |
| 4 | **Major** | **[D2-1]** D1 定义了 `retry_with_policy()` 但 D2 完全绕开它使用手写循环。两个重试模式并存但无调用关系 = dead code。让 D2 通过 `retry_with_policy` 实现。 | 架构脱节 |
| 5 | **Major** | **[D2-2]** OverSizeError 重 OCR 仍超阈值后静默使用泄漏结果，L3 补救完全无效。应走 OcrSkipError 路径 + 确认适配器支持 max_tokens 参数。 | 逻辑缺陷 |
| 6 | **Medium** | **[顺序-1]** D3 依赖 Config 扩展但实施清单中未含此前提步骤。需增加 P0。 | 顺序缺陷 |
| 7 | **Medium** | **[冲突-2]** D2 OverSizeError 与 C1 L3 超阈检测重复。C1 L3 日志标记功能在 D2 后多余。 | 职责不清 |
| 8 | **Medium** | **[D2-3]** `_record_failure` 函数和 `pipeline_state` 变量未定义。失败信息无法被上游感知。 | 设计遗漏 |
| 9 | **Low** | **[D1-2]** 异常捕获顺序需文档注明（RateLimitedError 先于 ApiError）。 | 文档 |
| 10 | **Low** | **[A-2]** `RETRY_POLICIES` 字典归属未明确，建议定义在 `errors.py`。 | 文档 |

---

## 最终裁决

### **需修订（Need Revision）**

D1–D4 的**异常分类 + VLM 主循环重试 + 断点续跑**方向是正确且必要的补充。当前 `_run_vlm` 的 `except Exception: continue` 架构太粗糙，KZOCR 必须升级。

但 **2 项阻断性问题和 3 项重大问题** 必须在进入实施前解决：

1. **[Blocking] D3 缓存路径引用不存在的配置属性**：`cfg.kzocr_output_dir` 在 Config 类中不存在。无论 D3 在哪个优先级实现，必须先新增 `KZOCR_OUTPUT_DIR` 环境变量及其 Config 字段。同时缓存文件存在性 ≠ 参数一致性，需要增加参数哈希或元数据校验。

2. **[Blocking] D3 缓存语义与 C2 文件即状态哲学冲突**：缓存文件是中间产物，其有效性取决于产生它的上下文（引擎版本、参数、模型版本）。`is_complete` 检测缓存文件存在就直接跳过，会导致 VLM 参数变更被静默忽略。

3. **[Major] 异常体系 50% 过度设计**：4/8 的子类在当前 KZOCR 架构中无消费者（无抛出点、无捕获点）。初始提交只保留 `ApiError`、`RateLimitedError`、`OverSizeError`、`OcrSkipError`，其余待需求明确再添加。

4. **[Major] `retry_with_policy` 与 D2 脱节**：D1 创建了统一重试入口但 D2 绕开了它。要么 D2 使用 `retry_with_policy`（方案 A），要么 D1 不提交 `retry_with_policy`（避免 dead code）。

5. **[Major] OverSizeError 重 OCR 逻辑缺陷**：重 OCR 仍超阈值后静默使用泄漏结果，使 L3 重 OCR 完全无效。应转入 `OcrSkipError` 路径。

### 修订后的实施顺序（推荐）

```
P0:  Config 新增 kzocr_output_dir 字段 (映射 KZOCR_OUTPUT_DIR)
P1:  D1 异常类（ApiError/RateLimitedError/OverSizeError/OcrSkipError）
       + retry_with_policy（让 D2 实际调用它）
       + 测试（成功/失败/耗尽三种路径）
P1:  D2 VLM 主循环重试（通过 retry_with_policy）
       + 修复 OverSizeError 逻辑
       + 补充 _record_failure 机制
P2:  D3 VLM 断点续跑
       + 解决缓存语义冲突（参数哈希/元数据校验）
       + 配合 C2 路径穿越校验
P3:  D4 层级异常检测（可选，延迟到需求明确）
```

### 通过项摘要

- [✓] D1 将异常集中到 `kzocr/engines/errors.py` 方向正确
- [✓] D2 VLM 主循环引入重试 + 失败分类方向正确
- [✓] D3 断点续跑方向正确（但需解决实现细节）
- [✓] D4 作为 P3 deferred 不影响前期实施
- [✓] 文件位置选择合理（`errors.py` 在 `engines/` 包下）
- [✓] P1→P2→P3 的大致优先级顺序合理

---

*附录：本次评审检查的代码文件*
- `kzocr/engine/run.py`（`_run_vlm` L438-533, `_run_real` L113-147）
- `kzocr/engines/ratelimit.py`（`ExponentialBackoff`, `AdaptiveRateLimiter`, `MultiTokenRateLimiter`）
- `kzocr/engines/atomic.py`（`atomic_write`, `is_complete`, `_check_base`）
- `kzocr/engines/leakage.py`（`CharCountBaseline`, `apply_leakage_defense`）
- `kzocr/config.py`（`Config` dataclass）
- `kzocr/engine/types.py`（`BookResult`, `PageResult`）
- `docs/plans/ocr-engine-unification.v0.3-FREEZE.md`（B1–B8 基线）
- `docs/plans/ocr-engine-unification.v0.4-AMEND.md`（C1–C5）
- `tests/test_ratelimit.py`（现有 18 项测试）
