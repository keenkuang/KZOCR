# KZOCR 统一 OCR 引擎架构方案 —— v0.5 AMEND（异常处理体系改进）

> 本文件是 `ocr-engine-unification.v0.3-FREEZE.md` 的**增量修订**。v0.3 FREEZE (B1–B8) 和 v0.4 AMEND (C1–C5) 维持有效，此处仅追加 D1–D4 共 4 项从 TOC OCR 项目（970 页实战验证）吸收的异常处理体系改进。
> 来源项目：`/home/keen/Documents/trae_projects/traedocu/docs/`

---

## D1 —— 异常分类 + 重试策略统一

**来源：** TOC 项目 V3 §6.2–6.3 的 8 种异常类型 × 检测方式 × 处理策略表 + 3 种重试策略，经 13 个缺陷修复验证。

**问题：** KZOCR 当前异常处理散落在各模块中：

| 位置 | 当前行为 | 问题 |
|------|---------|------|
| `_run_vlm` 逐页循环 | `except Exception: continue` | 所有失败一视同仁跳过，无重试、无日志上下文 |
| `_run_real` | `except Exception: raise`（require_real）或降级 mock | 无重试、无失败原因分类 |
| `_init_vlm_adapter` | `except Exception: logger.warning + 降级` | 降级日志已清晰，合理 |
| `ratelimit.py` | `ExponentialBackoff` 类已实现 | 但未被主循环使用 |

**裁决：** 新增 `kzocr/engines/errors.py`，统一异常分类 + 重试入口：

**异常继承体系：**
```
OcrError (Exception)           # 基类
├── ApiError                   # API 调用失败（HTTP 错误/超时）
│   └── RateLimitedError       # 429/503 限流
├── LeakageError               # 跨页泄漏检测未通过（L4 截断后仍残留）
├── OverSizeError              # 字数超阈值（L1 触发后重 OCR 仍超）
├── CrossPageIncompleteError   # 跨页方剂不完整（9 字段缺组成）
├── HierarchyAnomalyError      # 层级异常（三级编号等）
├── OcrSkipError               # 跳过页（重试耗尽但仍失败）
└── DbWriteError               # 数据库写入冲突
```

**重试策略表（复用 `ratelimit.py` 已有 `ExponentialBackoff`）：**

| 异常类型 | 策略 | 参数 | 说明 |
|---------|------|------|------|
| `ApiError` | 指数退避 | 3 次, 2s→4s→8s | 网络/超时错误 |
| `RateLimitedError` | 指数退避 | 3 次, 尊重 Retry-After | 限流 429/503 |
| `OverSizeError` | 重 OCR | 1 次, max_tokens×1.8 | 字数超阈值 |
| `DbWriteError` | 幂等 | 0 次 | UPSERT 保证幂等 |
| 其他 | 跳过 | 0 次 | 记录日志，继续 |

**`retry_with_policy()` 辅助函数：**
```python
def retry_with_policy(fn, policy: dict, error_types: tuple[type] = (ApiError,)):
    """按策略执行 fn；指数退避或幂等重试，耗尽后抛出源异常。"""
```

**测试要求：**
- 每个异常类型可独立构造和捕获
- `retry_with_policy` 成功/失败/耗尽三种路径
- `ExponentialBackoff` 集成测试（与现有 18 项 ratelimit 测试合并）

---

## D2 —— VLM 主循环重试 + 失败分类增强

**来源：** TOC 项目 V3 §6.3 重试策略（OCR API 失败重试 3 次 + 超阈值重 OCR 1 次 + 仍失败标记跳过）。

**问题：** `_run_vlm`（`kzocr/engine/run.py:503`）当前：

```python
except Exception as exc:
    logger.warning("[VLM] 第 %d 页识别失败，跳过：%s", i + 1, exc)
    continue
```

所有异常统一处理，无重试、无分类、无失败计数。

**裁决：** 增强 `_run_vlm` 逐页循环的异常处理：

```python
# 每页重试逻辑
for attempt in range(1, RETRY_POLICIES["api"]["max_retries"] + 1):
    try:
        text = vlm.recognize_pages(imgs)
        # L3: 字数超阈值的重 OCR
        if baseline.ready and len(text) > baseline.threshold:
            raise OverSizeError(...)
        break
    except RateLimitedError:
        backoff.sleep(attempt + 1)
    except OverSizeError:
        # 1 次重 OCR 带更紧的 max_tokens
        text = vlm.recognize_pages(imgs, max_tokens=int(baseline.median * 1.8))
        if len(text) > baseline.threshold:
            logger.warning("L3 重 OCR 仍超阈值，继续使用结果")
    except (ApiError, OcrSkipError) as exc:
        logger.warning("第 %d 页 attempt %d 失败: %s", i + 1, attempt, exc)
        if attempt < max_retries:
            backoff.sleep(attempt + 1)
else:
    logger.error("第 %d 页重试耗尽，跳过", i + 1)
    _record_failure(pipeline_state, i + 1, type(exc).__name__)
    continue
```

**效果目标：** 瞬时失败自动恢复，持久失败有分类和记录。

---

## D3 —— VLM 主循环断点续跑集成

**来源：** TOC 项目 `pipeline_state.json` + C2 `is_complete()`。经 v0.4 AMEND 验证，文件存在 = 状态，无需 state.json。

**问题：** `atomic.py` 已有 `is_complete()`，但 `_run_vlm` 未使用——中断后必须从头识别所有页。

**裁决：** 为 VLM 路径增加文件级断点：

```python
def _get_vlm_cache_path(cfg, book_code: str, page_num: int) -> Path:
    """VLM 缓存目录：{output_dir}/vlm_cache/{book_code}/page_{page_num:04d}.txt"""

# 主循环开头
cache_dir = cfg.kzocr_output_dir / "vlm_cache" / safe_book_code
cache_dir.mkdir(parents=True, exist_ok=True)

for i, page in enumerate(all_pages):
    cache_path = _get_vlm_cache_path(cfg, safe_book_code, i + 1)

    # 断点检测：已有缓存文件 → 跳过
    if is_complete(cache_path):
        text = cache_path.read_text(encoding="utf-8")
        pages_text.append(text)
        baseline.feed(text)
        continue

    # ... 正常 OCR 流程 ...

    # 原子写入缓存
    atomic_write(cache_path, text)
```

**断点清除：** 环境变量 `KZOCR_CLEAR_CACHE=1` 清除所有缓存重新 OCR；默认保留缓存。

**效果目标：** 任意中断恢复，零页重复 OCR。

---

## D4 —— 层级异常检测（可选，P3 低优先）

**来源：** TOC 项目 V3 §2.2 `_check_hierarchy_anomaly()` + `hierarchy_anomaly` 表。

**问题：** 方剂编号如 `16.7.1`（三级编号）表示 4 级目录结构，超出正常 2 段编号（节号.方序号）。当前 VLM 路径不做检测，异常编号方剂可能在后续入库阶段遗漏。

**裁决：** （低优先，与 TOC 管线阶段合并实施）

- 新增 `kzocr/engines/hierarchy.py`：
  ```python
  @dataclass
  class HierarchyAnomaly:
      recipe_no: str
      depth: int
      source_page: int
      resolution: str = "pending"

  def check_hierarchy_anomaly(text: str, page_num: int) -> list[HierarchyAnomaly]:
      """扫描 OCR 文本中段数 > 2 的编号，返回异常列表。"""
  ```

- 输出：JSON 文件 `{output_dir}/hierarchy_anomalies.json`
- 集成点：`_run_vlm` 中 VLM 后处理之后

**效果目标：** 0 三级编号方剂在入库前漏报。

---

## 实施顺序

| 优先级 | 项 | 文件 | 工作量 |
|--------|-----|------|--------|
| **P1** | D1 异常分类 + retry_with_policy | `kzocr/engines/errors.py` (新建) | 小 |
| **P1** | D1 `retry_with_policy` + 测试 | `kzocr/engines/ratelimit.py` (小改) | 小 |
| **P1** | D2 VLM 主循环重试 + 失败分类 | `kzocr/engine/run.py` (改) | 中 |
| **P2** | D3 VLM 断点续跑 | `kzocr/engine/run.py` (改) | 中 |
| **P3** | D4 层级异常检测 | `kzocr/engines/hierarchy.py` (新建) | 小 |

## v0.5 评审要求

| 角色 | 评审重点 |
|------|---------|
| **架构师** | D1 异常继承体系与现有模块兼容性；D3 缓存策略对输出目录结构的影响 |
| **软件工程** | D2 `retry_with_policy` 模式正确性；D1 异常不滥用继承层级 |
| **测试** | D2 重试路径覆盖（成功/耗尽/超阈值三种）；D3 断点续跑测试（中断恢复 + 缓存清除） |
| **安全** | D3 VLM 缓存文件内容可能包含敏感 PDF 数据，清除策略 |
| **领域** | D4 层级异常对中医方剂编号的实用性（三级编号是否确实为异常） |
