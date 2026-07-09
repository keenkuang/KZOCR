# KZOCR 统一 OCR 引擎架构方案 —— v0.5 AMEND（异常处理体系改进）

> 本文件是 `ocr-engine-unification.v0.3-FREEZE.md` 的**增量修订**。v0.3 FREEZE (B1–B8) 和 v0.4 AMEND (C1–C5) 维持有效，此处仅追加 D1–D4 共 4 项从 TOC OCR 项目（970 页实战验证）吸收的异常处理体系改进。
> 来源项目：`/home/keen/Documents/trae_projects/traedocu/docs/`
>
> **多角色评审（6 角色）：** `docs/reviews/2026-07-10-round6/`
> 本次修订吸收了架构师、软件工程、测试、安全、领域 5 项评审意见（裁决：**有条件通过 / 需修订**）。

---

## D0 —— 基础设施：Config 新增 `kzocr_output_dir`

**来源：** 架构师评审 D3-1（Blocking）——`cfg.kzocr_output_dir` 在 Config 类中不存在。

**裁决：** Config（`kzocr/config.py`）新增 `kzocr_output_dir` 字段，映射环境变量 `KZOCR_OUTPUT_DIR`（默认 `/tmp/kzocr/output`）。

- 该目录用于：D3 的 `vlm_cache/` 子目录、C1 L3 重 OCR 产物、后续可能的其他中间产物
- 新增字段须经过 C2 `_check_base` 路径穿越校验（`atomic.py`）
- 确保 `config.py` 中处理 `None` 默认值场景

```python
@dataclass
class Config:
    ...
    kzocr_output_dir: str = field(
        default_factory=lambda: os.environ.get("KZOCR_OUTPUT_DIR", "/tmp/kzocr/output")
    )
```

**测试要求：** Config 默认值测试 + 环境变量覆盖测试。

---

## D1 —— 异常分类 + `retry_with_policy`

**来源：** TOC 项目 V3 §6.2–6.3 的异常类型 × 重试策略表。

**问题：** KZOCR 当前异常处理散落各处，`_run_vlm` 的 `except Exception: continue` 对所有失败一视同仁。

**裁决：** 新增 `kzocr/engines/errors.py`，按 YAGNI 原则初始只保留 4 个实际使用的子类：

```python
class OcrError(Exception):           # 基类
    """KZOCR 所有的 OCR 相关异常基类。"""
class ApiError(OcrError):            # API 调用失败（HTTP 错误/超时）
class RateLimitedError(ApiError):    # 429/503 限流
class OverSizeError(OcrError):       # 字数超阈值（L1 触发后重 OCR 仍超）
class RetryExhaustedError(OcrError): # 重试耗尽，跳过该页
```

**关于 `retry_with_policy` 的退避参数**（软件工程评审建议：避免 `RetryPolicy` 与 `ExponentialBackoff` 重复字段）：
- `retry_with_policy` 不引入独立的 `RetryPolicy` dataclass，直接接受 `ExponentialBackoff` 实例
- 不同场景通过不同的 `ExponentialBackoff` 配置区分：

```python
from kzocr.engines.ratelimit import ExponentialBackoff

BACKOFF_CONFIGS = {
    "api":      ExponentialBackoff(base_delay=1.0, max_retries=3, max_delay=300.0, jitter=0.5),   # 性能建议：2.0→1.0s 减少首轮重试延迟
    "ratelimit": ExponentialBackoff(base_delay=1.0, max_retries=3, max_delay=60.0,  jitter=0.3),
    "oversize": ExponentialBackoff(base_delay=0,    max_retries=1),  # 快速重试，max_tokens×1.8
}
```

- `strategy="reocr"` 由调用方在 `retry_kwargs` 中传递调整参数（如 `max_tokens`），`retry_with_policy` 在重试时注入 fn 参数
- `RateLimitedError` 尊重 `Retry-After` header（若有则用其值作为本轮延迟，覆盖退避计算）

**`retry_with_policy()` 辅助函数**（定义在 `kzocr/engines/errors.py` 中，靠近异常类和策略表）：

```python
def retry_with_policy(
    fn: Callable[..., T],
    backoff: ExponentialBackoff,
    error_types: tuple[type[Exception], ...] = (ApiError,),
    retry_kwargs: dict[int, dict] | None = None,  # attempt → fn kwargs
    on_exhausted: Callable[[int, Exception], None] | None = None,
) -> T:
    """按指数退避策略执行 fn，耗尽后抛出 RetryExhaustedError。

    Args:
        fn              : 要执行的函数。
        backoff         : ExponentialBackoff 实例（决定退避参数）。
        error_types     : 哪些异常触发重试（默认 ApiError）。
        retry_kwargs    : attempt 序号 → fn 的参数字典（用于 OverSizeError 的 max_tokens 调整）。
        on_exhausted    : 所有重试耗尽后回调 (page_num, last_exception) → None。
                          注意：page_num 需由调用方闭包捕获，retry_with_policy 不管理此值。

    Returns:
        fn 成功执行的返回值。

    Raises:
        RetryExhaustedError: 所有重试耗尽，原异常通过 __cause__ 链传递。
    """
```

- 重试行为：
  - `strategy="exponential"`：使用 `ExponentialBackoff` 计算延迟 + 随机抖动
  - `strategy="reocr"`：使用 `retry_kwargs` 传入调整参数（如 `max_tokens×1.8`）
  - `strategy="none"`：不重试，直接抛出
- `RateLimitedError` 构造函数接受可选 `retry_after: float | None` 参数，`retry_with_policy` 在捕获 `RateLimitedError` 时优先使用此值作为退避延迟（覆盖指数退避计算）
- `RateLimitedError` 定义：

```python
class RateLimitedError(ApiError):
    """429/503 限流错误。"""
    def __init__(self, message: str = "", retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after
```

**测试要求（32–42 个用例）：**
- 每个异常类型可独立构造和捕获（参数化 + 继承关系验证）
- `retry_with_policy` 成功/重试后成功/耗尽三种路径
- `ExponentialBackoff` 集成测试

---

## D2 —— VLM 主循环重试 + 失败分类增强

**来源：** 架构师 + 软件工程评审均指出 D1 的 `retry_with_policy` 必须被 D2 消费才能避免 dead code。

**问题：** `_run_vlm` 逐页循环当前为：
```python
except Exception as exc:
    logger.warning("[VLM] 第 %d 页识别失败，跳过：%s", i + 1, exc)
    continue
```

无重试、无分类、无失败记录。

**裁决：** D2 通过 `retry_with_policy` 实现重试，不手写循环：

```python
from kzocr.engines.errors import (
    retry_with_policy,
    ApiError, RateLimitedError, OverSizeError, RetryExhaustedError,
)
from kzocr.engines.ratelimit import ExponentialBackoff

failed_pages: dict[int, str] = {}       # page_num → error_type

for i, page in enumerate(all_pages):
    page_num = i + 1                    # 闭包捕获，避免 on_exhausted 使用 attempt 序号
    imgs = [_prepare_image(page, all_pages, i)]
    text = ""
    try:
        # 正常 OCR → 字数校验
        text = retry_with_policy(
            lambda: vlm.recognize_pages(imgs) if supports_two_page else vlm.recognize_page(imgs[0]),
            backoff=BACKOFF_CONFIGS["api"],
            error_types=(ApiError, RateLimitedError),
            on_exhausted=lambda _attempt, exc: failed_pages.update({page_num: type(exc).__name__}),
        )
        # OverSizeError 检测 + 重 OCR
        if baseline.ready and len(text) > baseline.threshold:
            text = retry_with_policy(
                lambda: vlm.recognize_pages(imgs, max_tokens=int(baseline.median * 1.8))
                       if supports_two_page else vlm.recognize_page(imgs[0], max_tokens=int(baseline.median * 1.8)),
                backoff=BACKOFF_CONFIGS["oversize"],
                error_types=(OverSizeError,),
                on_exhausted=lambda _attempt, exc: failed_pages.update({page_num: "OverSize:" + type(exc).__name__}),
            )
    except RetryExhaustedError as exc:
        # 重试耗尽 → 记录失败页 + 跳过（注意：on_exhausted 先于 except 执行）
        logger.error("[VLM] 第 %d 页重试耗尽（%s），跳过", page_num, exc.__cause__)
        continue

    pages_text.append(text)
    baseline.feed(text)
```

**关键细节：**
- `RateLimitedError` 尊重 `Retry-After` header（`ApiError` 的 `retry_with_policy` 内部处理）
- OverSizeError 重 OCR 失败后**抛出 `RetryExhaustedError`**，不走静默使用泄漏结果
- C1 L3 的日志标记在 D2 实施后变多余 → 调整为仅做 L4 探针检测（见 冲突-2 修订）
- `failed_pages` 在 `_run_vlm` 结束时可通过日志输出或扩展 `BookResult` 返回
- 适配器需确认支持 `max_tokens` 参数（若不支持则 OverSizeError 不触发重试）

**测试要求（约 15 个用例）：**
- ApiError 退避重试 → 第 3 次成功
- OverSizeError 重 OCR → 成功
- OverSizeError 重 OCR → 仍超 → 抛出 RetryExhaustedError → 跳过
- 所有重试耗尽 → `failed_pages` 正确记录
- D2 使用 `retry_with_policy` → 验证 `errors.py` 无 dead code

---

## D3 —— VLM 断点续跑

**来源：** 架构师评审 D3-1（Blocking）+ D3-2（Blocking）+ 安全评审。

**问题：** 中断后从头识别所有页，无恢复机制。简单 `is_complete` 存在缓存与语义冲突。

### D3.0 —— 前提

- P0 `kzocr_output_dir` Config 字段已存在（D0）
- Config 路径须传入 C2 `_check_base` 校验

### D3.1 —— 缓存路径

```python
VLM_CACHE_DIR = "vlm_cache"  # 可配置常量

def _get_vlm_cache_path(cfg, engine_tag: str, book_code: str, page_num: int) -> Path:
    """缓存文件路径：{kzocr_output_dir}/vlm_cache/{engine_tag}/{book_code}/page_{page_num:04d}.txt"""
    return Path(cfg.kzocr_output_dir) / VLM_CACHE_DIR / engine_tag / safe_book_code / f"page_{page_num:04d}.txt"
```

- **engine_tag** 参与路径，避免 SenseNova ↔ PaddleOCR-VL 切换时误用旧缓存

### D3.2 —— 缓存有效性校验（解决 C2 哲学冲突）

- 缓存文件包含**参数签名（config_hash）** 作为首行元数据
- 写入时：`# config_hash={sha256(VLM_PARAMS_JSON)[:16]}\n{page_text}`
- 读取时：校验元数据匹配当前配置，不匹配则视为无效缓存
- `VLM_PARAMS_JSON` 包含当前使用的引擎标识、max_tokens、VLM prompt 等影响输出的参数

```python
def _cache_is_valid(cache_path: Path, cfg: Config) -> bool:
    """验证缓存文件是否由当前配置生成。"""
    if not is_complete(cache_path):
        return False
    try:
        first_line = cache_path.read_text(encoding="utf-8").split("\n")[0]
        expected_hash = _compute_config_hash(cfg)
        return first_line == f"# config_hash={expected_hash}"
    except (OSError, IndexError):
        return False

def _compute_config_hash(cfg: Config) -> str:
    """计算影响 VLM 输出的配置参数的 SHA256 摘要（前 16 字符）。

    参与哈希的参数：
    - vlm_engine（SenseNova / PaddleOCR-VL-1.6）
    - sensenova_model / sensenova_base_url
    - vlm_host / vlm_port
    - max_tokens（如果 Config 中存在）
    """
    import hashlib, json
    params = {
        "engine": cfg.vlm_engine,
        "sensenova_model": getattr(cfg, "sensenova_model", ""),
        "sensenova_base_url": getattr(cfg, "sensenova_base_url", ""),
        "vlm_host": getattr(cfg, "vlm_host", "127.0.0.1"),
        "vlm_port": getattr(cfg, "vlm_port", 18080),
    }
    raw = json.dumps(params, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]
```

### D3.3 —— 缓存生命周期

- 安全评审建议：缓存文件含 PDF 明文，需 TTL 策略
- 默认 TTL：24 小时（可通过 `KZOCR_CACHE_TTL` 环境变量覆盖，单位秒）
- `KZOCR_CLEAR_CACHE=1` 清除所有缓存
- `KZOCR_OUTPUT_DIR` 应指向可清理的临时目录

**测试要求（约 6 个用例）：**
- 中断+恢复 → 跳过缓存页（config_hash 匹配）
- 参数变化 → 缓存不匹配 → 不跳过
- config_hash 不匹配 → 重新 OCR
- `KZOCR_CLEAR_CACHE=1` 全量重跑
- `is_complete` 检查后再读取（TOCTOU 防护，security O1）

---

## D4 —— 层级异常检测（P3 低优先）

**来源：** TOC 项目 V3 §2.2 `hierarchy_anomaly` 表。

**问题：** 方剂编号如 `16.7.1`（三级编号）表示超正常层级，当前 VLM 路径不做检测。

**裁决：** P3 低优先，**expected_depth 参数化**（domain 评审 P0 发现——硬编码 2 段在通用 TCM 书籍场景将产生 100% 假阳性）。

### 设计（草案，不参与 P1/P2 实施）

```python
@dataclass
class HierarchyAnomaly:
    recipe_no: str
    depth: int
    expected_depth: int
    source_page: int
    resolution: str = "pending"

def check_hierarchy_anomaly(
    text: str, page_num: int,
    expected_depth: int = 2,    # 可配置：CLI --expected-depth 或 TOC 推断
) -> list[HierarchyAnomaly]:
    """扫描 OCR 文本中段数 > expected_depth 的编号，返回异常列表。"""
```

- 输出：JSON 文件 `{kzocr_output_dir}/hierarchy_anomalies.json`
- 仅属于 KZOCR 内部的检测工具，不直接介入 VLM 主流程
- `HierarchyAnomalyError` 异常类型不参与初始提交（按 D1 YAGNI 原则）

**效果目标：** 0 三级编号方剂在入库前漏报。

---

## 冲突-2 修订：C1 L3 与 D2 OverSizeError 的职责边界

**来源：** 架构师评审 冲突-2（Major）。

**事实：** C1 L3（`leakage.py:192`）当前的"日志标记重 OCR"功能在 D2 到来后变得多余——D2 已经实时处理了超阈页面。

**裁决：** D2 实施后修改 C1 `apply_leakage_defense`：
- C1 L1（基线检测）保留——用于日志告警
- C1 L2（max_tokens 物理上限）保留
- C1 L3（日志标记重 OCR）**移除**——D2 已接管实时重试
- C1 L4（探针重叠检测）保留
- 确保 D2 的重 OCR 处理后，LeakageDetector.detect 的输入是"已重试的文本"，不会重复触发阈值告警

---

## 实施顺序

| 优先级 | 项 | 文件 | 说明 |
|--------|-----|------|------|
| **P0** | D0 Config 扩展 | `kzocr/config.py` (改) | 新增 `kzocr_output_dir` 映射 `KZOCR_OUTPUT_DIR` |
| **P1** | D1 异常分类 (4 类) | `kzocr/engines/errors.py` (新建) | 初始提交仅 ApiError/RateLimitedError/OverSizeError/RetryExhaustedError |
| **P1** | D1 retry_with_policy + 测试 | `kzocr/engines/errors.py` (同上) | 32-42 用例，D2 使用它消除 dead code |
| **P1** | D2 VLM 主循环重试 + 失败分类 | `kzocr/engine/run.py` (改) | 通过 retry_with_policy，不手写循环 |
| **P1** | 冲突-2 修正 | `kzocr/engines/leakage.py` (小改) | C1 L3 日志标记移除 |
| **P2** | D3 VLM 断点续跑 | `kzocr/engine/run.py` (改) | 依赖 P0 + P1 |
| **P3** | D4 层级异常检测 | `kzocr/engines/hierarchy.py` (新建) | 可选，延迟到 TOC 管线阶段 |

## 实施注意事项

1. **D1 + D2 由同一人实施**——确保 `retry_with_policy` 被 D2 实际消费，消除架构脱节
2. **D2 + D3 修改同一函数 `_run_vlm`**——即使分 P1/P2 实施，建议同人处理避免 diff 冲突
3. **`_run_vlm` 重构建议**（软件工程评审）：提取 `_process_vlm_page(page, page_num, ...)` 子函数封装单页 OCR + 重试 + 缓存逻辑，降低 `_run_vlm` 主循环复杂度
4. **D3 缓存路径必须经过 C2 路径穿越校验**——调用 `atomic_write(cache_path, text, allowed_base=cfg.kzocr_output_dir)`
5. **C1 L3 修改不破坏现有测试**——现有 leakage 测试中 L3 仅验证日志输出，移除后测试需对应调整
6. **适配器 `max_tokens` 参数兼容**——PaddleOCRVl16Adapter / SenseNovaAdapter 需确认支持 `max_tokens` 参数；若不支持，D2 OverSizeError 不触发重试而是直接走过 OversizeError 路径（抛出异常）
7. **`_run_real` 路径待未来升级**——当前 v0.5 不覆盖 `_run_real` 的异常增强（架构师 A-3 备查项）
8. **`RateLimitedError` Retry-After header**——当前退避降级为纯指数退避；若适配器层不支持读取 `Retry-After` header，文档注明"纯指数退避，Retry-After 被忽略"

## 版本历史

| 版本 | 日期 | 修订内容 |
|------|------|----------|
| v0.5-rc1 | 2026-07-10 | 初始方案，D1-D4 完整 |
| **v0.5-rc4** | **2026-07-10** | **吸收 round8 评审（含性能）：** `RateLimitedError` 新增 `retry_after` 构造函数参数（测试 B1）；D3 补充 `_compute_config_hash()` 完整定义（测试 B2）；base_delay 2.0→1.0s（性能建议）；新增累积重试时间跟踪建议（批量场景） |
