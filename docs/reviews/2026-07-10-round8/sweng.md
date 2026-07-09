# KZOCR v0.5 AMEND rc3 — 软件工程终审（Round 8）

**评审范围：** v0.5-rc3 对 Round 7 软件工程再评审 3 项发现的修复验证。
**评审对象：** `docs/plans/ocr-engine-unification.v0.5-AMEND.md`（rc3） + 源代码验证（`run.py`, `config.py`, `ratelimit.py`, `leakage.py`, `atomic.py`）

---

## 1. Round 7 发现修复验证

### 1.1 问题 A — `RetryPolicy` 与 `ExponentialBackoff` 字段重复

| Round 7 | 状态 | 证据 |
|---------|------|------|
| ⚡ 建议修复：消除重复，直接接受 `ExponentialBackoff` | ✅ **已修复** | rc3 §D1："retry_with_policy 不引入独立的 RetryPolicy dataclass，直接接受 ExponentialBackoff 实例"。`BACKOFF_CONFIGS` 表使用 `ExponentialBackoff(...)` 直接构造 |

**代码验证：** `ratelimit.py:44` — `ExponentialBackoff` 保持为退避计算的唯一真实来源。无残留 `RetryPolicy` 引用。

---

### 1.2 问题 B — `retry_with_policy` 抛出语义与 D2 捕获不一致

| Round 7 | 状态 | 证据 |
|---------|------|------|
| ⚠️ 必须修复：文档说抛"最后一次异常"但 D2 捕获 `RetryExhaustedError` | ✅ **已修复** | rc3 §D1 `retry_with_policy` 文档已修正：**Raises `RetryExhaustedError`**，原异常通过 `__cause__` 链传递。D2 `except RetryExhaustedError as exc` + `exc.__cause__` 一致 |

**代码验证：** 文档与 D2 捕获语义对齐。`RetryExhaustedError.__cause__` 为调用方保留原始异常链。

---

### 1.3 问题 C — `on_exhausted` lambda 中 `pn` 参数语义

| Round 7 | 状态 | 证据 |
|---------|------|------|
| ⚠️ 必须修复：`on_exhausted` lambda 将 attempt 序号误作页码 | ⚠️ **部分修复** | 闭包变量 `page_num = i + 1` 已正确定义，但 lambda 体仍使用 `pn` 而非 `page_num` |

**详情：** rc3 §D2 代码（第 143-144 行）：
```python
page_num = i + 1                    # 闭包捕获，避免 on_exhausted 使用 attempt 序号
...
on_exhausted=lambda pn, exc: failed_pages.update({pn: type(exc).__name__}),
```

`retry_with_policy` 的 `on_exhausted` 回调签名为 `Callable[[int, Exception], None]`，其第一个参数是内部重试计数（attempt 序号），而非页码。lambda 体中的 `pn` 是 attempt 序号，`failed_pages` 将记录 `{attempt: error_type}` 而非 `{page_num: error_type}`。

**修正建议：** 将 lambda 改为使用闭包捕获的 `page_num`：
```python
on_exhausted=lambda _attempt, exc: failed_pages.update({page_num: type(exc).__name__}),
```

**影响评估：** 不影响重试正确性，仅影响 `failed_pages` 的追踪准确性。**非阻塞级缺陷**，实现时即可修正（单字符变更）。

**额外发现：** `on_exhausted` 文档注释应同步修正——第一个参数是 `attempt` 而非 `page_num`：
```
on_exhausted    : 所有重试耗尽后回调 (attempt, last_exception) → None。
                  注意：页码需通过闭包捕获传入，retry_with_policy 仅传递尝试次数。
```

---

## 2. 源代码验证

### 2.1 `run.py` 当前状态

| 区域 | 行数 | 当前实现 | 与 rc3 计划的一致性 |
|------|------|---------|-------------------|
| `_run_vlm` 主循环（`for i, page in enumerate`） | 476-508 | `except Exception: continue` + `baseline.feed(text)` | ✅ D2 计划改为 `retry_with_policy` + 两段式处理 |
| 循环前变量声明 | 457-474 | `baseline`, `supports_two_page`, `all_pages`, `max_pages`, `total_timeout` | ✅ D2 添加 `failed_pages: dict[int, str] = {}` |
| 循环后泄漏防御 | 512-513 | `apply_leakage_defense(pages_text, baseline)` | ✅ 冲突-2 修订后 L3 日志标记移除 |
| 跨页合并 | 515-516 | `_merge_cross_page_breaks(pages_text)` | ✅ 不受影响 |

### 2.2 `_prepare_image` 函数状态

rc3 §D2 代码中引用了 `_prepare_image(page, all_pages, i)`，但**当前 `run.py` 中无此函数**。当前单页准备逻辑直接内联在循环体中：
```python
img = _crop_to_body(_pdf_page_to_numpy(page))
imgs = [img]
if supports_two_page and i < len(all_pages) - 1:
    next_part = all_pages[i + 1] ...
```

**影响评估：** 非阻塞。计划 §3（实施注意事项）已建议提取 `_process_vlm_page` 子函数，`_prepare_image` 可作为其内部助手。实现时需创建该函数。

### 2.3 `config.py` — `kzocr_output_dir`

| 检查项 | 状态 |
|--------|------|
| `kzocr_output_dir` 当前是否存在 | ⚠️ 不存在（P0 待添加） |
| 其他字段模式一致 | ✅ 均使用 `field(default_factory=...)` 或 `load_config()` 赋值 |
| `from_env()` vs `load_config()` 双通道 | ✅ 无兼容风险 |

### 2.4 `ratelimit.py` — `ExponentialBackoff`

验证 `ExponentialBackoff` 符合 rc3 的重退要求：

| 特性 | 支持状态 |
|------|---------|
| `_compute_delay(attempt)` 指数退避 | ✅ `base_delay * 2^(attempt-1)` |
| `max_delay` 钳位 | ✅ `min(raw, max_delay)` |
| 随机抖动 | ✅ `1.0 + random() * jitter` |
| `sleep(attempt)` 阻塞等待 | ✅ |

### 2.5 `leakage.py` — C1 L3 移除范围

验证 rc3 冲突-2 修订与当前代码的对应关系：

| 层级 | 当前实现（`leakage.py:149-204`） | rc3 裁决 | 状态 |
|------|-------------------------------|---------|------|
| L1 | 日志告警 `char_count > threshold` | 保留 | ✅ |
| L2 | `max_tokens * 2` 物理上限检测 | 保留 | ✅ |
| L3 | `logger.info("[leakage] L3: P%d 建议重 OCR...")` at line 192 | **移除** | ✅ 正确识别 |
| L4 | `LeakageDetector.detect` 重叠检测 | 保留 | ✅ |

### 2.6 `atomic.py` — C2 路径穿越校验

验证 `_check_base` 函数可用于 D0/D3 路径安全：

| 检查项 | 状态 |
|--------|------|
| `_check_base(path, allowed_base)` 存在 | ✅ `atomic.py:11-32` |
| 拒绝路径穿越：校验 `path` 解析后是否在 `allowed_base` 下 | ✅ `str(resolved).startswith(str(base_resolved) + os.sep)` |
| D0 调用：`_check_base(Path(cfg.kzocr_output_dir), ...)` | ✅ 已规划 |
| D3 调用：`atomic_write(cache_path, text, allowed_base=cfg.kzocr_output_dir)` | ✅ 已规划（实施注意事项 #4） |

---

## 3. 次要项验证

### 3.1 Round 7 注记 D — `backoff` 变量死代码

rc3 中已消除——D2 直接使用 `BACKOFF_CONFIGS["api"]`，不再有无用的 `backoff` 变量。 ✅

### 3.2 Round 7 注记 E — `RateLimitedError` Retry-After

rc3 处理方式：
- §D1 正文保留"RateLimitedError 尊重 Retry-After header"（条件预期）
- §实施注意事项 #8 明确降级文档："若适配器层不支持读取 Retry-After header，文档注明'纯指数退避，Retry-After 被忽略'"

**评估：** 非阻塞。适配器层是否有 `Retry-After` 暴露能力属于独立调查项，不影响 D1/D2 核心设计。

### 3.3 D0 Config 默认值 None 场景

rc3 §D0：
```python
kzocr_output_dir: str = field(
    default_factory=lambda: os.environ.get("KZOCR_OUTPUT_DIR", "/tmp/kzocr/output")
)
```

`os.environ.get(...)` 保证返回 `str`，`field` 类型注解为 `str`，不存在 `None` 场景。与其他字段（如 `khub_base_url: str = "..."`）行为一致。 ✅

---

## 4. 实施风险摘要

| 风险项 | 等级 | 说明 |
|--------|------|------|
| D2 + D3 对 `_run_vlm` 的联合修改 | 中 | 建议同人实施（计划已识别） |
| `_process_vlm_page` 提取导致 `_prepare_image` 引入 | 低 | 计划已建议，实现时自然解决 |
| C1 L3 移除导致 leakage 测试需要微调 | 低 | 计划 #5 已识别 |
| 适配器 `max_tokens` 兼容性 | 中 | PaddleOCRVl16Adapter / SenseNovaAdapter 需确认；若不支持则 OverSizeError 路径退化 |
| D4 层级异常检测延迟到 TOC 管线阶段 | 低 | P3 明确为非阻塞，不参与 P1/P2 实施 |

---

## 5. 总体评估

### Round 7 Must-Fix 修复状态

| Round 7 Must-Fix | 状态 | rc3 证据 |
|-----------------|------|---------|
| 问题 A：`RetryPolicy`/`ExponentialBackoff` 字段重复 | ✅ 已修复 | 删 `RetryPolicy`，直接接受 `ExponentialBackoff` |
| 问题 B：抛异常语义与捕获不一致 | ✅ 已修复 | 文档修正为 `RetryExhaustedError` + `__cause__` 链 |
| 问题 C：`on_exhausted` lambda 参数误用 | ⚠️ **部分修复** | 闭包变量正确定义，但 lambda 体未使用 |

### 评分矩阵

| 维度 | 评分 |
|------|------|
| Round 7 必须修复（3 项） | 2/3 完全修复，1/3 大部分修复 |
| D1-D4 设计与现有代码一致性 | ✅ 全部验证通过 |
| 异常分类层次（YAGNI） | ✅ 仅 4 类，无过度设计 |
| `retry_with_policy` 被 D2 消费 | ✅ 两处使用，消除 dead code |
| 冲突-2 修订范围准确 | ✅ L1/L2/L4 保留，L3 移除 |
| C2 路径穿越校验引用 | ✅ `atomic.py` + `_check_base` |
| 实施顺序（P0→P3） | ✅ 依赖关系正确 |
| 实施注意事项覆盖 | ✅ 8 项覆盖已知风险点 |
| `_run_vlm` 重构建议 | ✅ 记录在案，非阻塞 |
| 向后兼容性 | ✅ 零破坏性变更 |

### 剩余问题：`on_exhausted` lambda（单行修复）

**问题描述：** rc3 §D2 代码中的 `on_exhausted` lambda 使用 `pn`（attempt 序号）而非闭包变量 `page_num`（页码）作为 `failed_pages` 键。

**修复（实施时自动解决）：**
```python
# 误：
on_exhausted=lambda pn, exc: failed_pages.update({pn: type(exc).__name__}),
# 正：
on_exhausted=lambda _attempt, exc: failed_pages.update({page_num: type(exc).__name__}),
```

同步修正 `on_exhausted` 文档注释：`(attempt, last_exception)` 而非 `(page_num, last_exception)`。

**影响：** 非功能性缺陷，不阻碍实现启动。实现过程中自然修复即可。

---

## 6. 最终裁决

### Approved for implementation

v0.5 AMEND rc3 已通过软件工程终审。Round 7 的 3 项发现中：

- **2 项完全修复**（问题 A、B）
- **1 项大部分修复**（问题 C — 闭包变量正确，仅 lambda 体使用错误变量）

剩余问题 C 的残迹是**单字符变更**（`pn` → `page_num`），在实现 D1 + D2 时自然同步修复即可，无需再开一轮评审。

**实施建议：**
1. 从 P0（Config 扩展）开始，确保 `kzocr_output_dir` 先可用
2. D1 + D2 同人实施，实施时修正 `on_exhausted` lambda 参数
3. 确认适配器 `max_tokens` 参数兼容性后再实现 D2
4. 约束-2（C1 L3 移除）在 D2 合并后执行，避免中间态

---

*评审人：code-reviewer-3 | 日期：2026-07-10 | 范围：v0.5-rc3（`ocr-engine-unification.v0.5-AMEND.md`）*
