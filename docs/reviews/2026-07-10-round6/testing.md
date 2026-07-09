# v0.5 AMEND — 测试评审报告

> 评审人：general-purpose-1
> 评审日期：2026-07-10
> 基于：`docs/plans/ocr-engine-unification.v0.5-AMEND.md` D1–D4
> 参考测试：`tests/test_ratelimit.py`、`tests/test_atomic.py`、`tests/test_vlm.py`

---

## 现有测试基础设施摘要

| 文件 | 类/范围 | 数量 | 关键模式 |
|------|---------|------|----------|
| `test_ratelimit.py` | `TestExponentialBackoff` (4), `TestAdaptiveRateLimiter` (6), `TestMultiTokenRateLimiter` (4), `TestRateLimitStore` (5) | 19 | 纯单元测试，构造 + 断言 + 时间度量，无 mock 外部依赖 |
| `test_atomic.py` | `TestAtomicWrite` (10) | 10 | `tmp_path` fixture + 路径穿越安全测试，`is_complete` 边界（已存在/空/不存在） |
| `test_vlm.py` | 路由测试 (4) + 逻辑测试 (3) + markdown 单元 (2) + 回归 (1) | 10 | `unittest.mock.patch` 重度使用（mock fitz + VLM adapter），降级 mock 整体替换 |

**结论：现有测试框架支持纯单元（零 mock）和 mock 集成两种风格，D1–D4 均可复用。** 以下逐项展开。

---

## D1 —— 异常分类 + 重试策略统一

### 1.1 异常类型可独立构造和捕获

**评估：** 可行，且应该通过纯单元测试覆盖。无需任何 mock。

**推荐测试文件：** `tests/test_errors.py`（新建）

**异常体系验证（参数化测试）：**

```python
@pytest.mark.parametrize("exc_cls,kwargs", [
    (ApiError, {"status_code": 500, "message": "Internal Server Error"}),
    (RateLimitedError, {"status_code": 429, "retry_after": 30}),
    (LeakageError, {"page": 5, "score": 0.85}),
    (OverSizeError, {"actual": 3200, "threshold": 2000}),
    (CrossPageIncompleteError, {"page": 10, "missing_fields": ["组成"]}),
    (HierarchyAnomalyError, {"recipes": ["16.7.1"]}),
    (OcrSkipError, {"page": 3, "reason": "重试耗尽"}),
    (DbWriteError, {"table": "recipes", "conflict_key": "uuid-123"}),
])
def test_exception_construct_and_catch(exc_cls, kwargs):
    """每个异常类型可独立构造、捕获，并读取字段。"""
    exc = exc_cls(**kwargs) if kwargs else exc_cls()
    with pytest.raises(exc_cls) as exc_info:
        raise exc
    for k, v in kwargs.items():
        assert getattr(exc_info.value, k) == v
```

**继承关系验证：**
- `RateLimitedError` 应是 `ApiError` 的子类 → `isinstance(RateLimitedError(...), ApiError)` 为 True
- `OcrError` 是所有异常的基类
- 策略分发可以按 `isinstance(exc, (ApiError, RateLimitedError))` 匹配

**边界值测试：**
- `ApiError` 构造时缺省 `message` 可空
- `RateLimitedError` 构造时 `retry_after` 为 None（无 Retry-After 头的情况）
- `OverSizeError` 的 `actual == threshold` 边界

### 1.2 `retry_with_policy` 三种路径测试

**评估：** 需要 mock `ExponentialBackoff.sleep` 来模拟时间流逝（与现有 `test_ratelimit.py::test_sleep_blocking` 风格一致）。不应真实 sleep。

**成功路径（第一次就成功）：**

```python
def test_retry_succeeds_first_attempt():
    fn = MagicMock(return_value="ok")
    result = retry_with_policy(fn, {"max_retries": 3})
    assert result == "ok"
    assert fn.call_count == 1
```

**失败路径（第 N 次重试成功）：**

```python
def test_retry_succeeds_on_retry():
    fn = MagicMock(side_effect=[ApiError(500), ApiError(502), "ok"])
    result = retry_with_policy(fn, {"max_retries": 3})
    assert result == "ok"
    assert fn.call_count == 3
```

**耗尽路径（所有重试用完仍失败）：**

```python
def test_retry_exhausted_raises_last_error():
    fn = MagicMock(side_effect=ApiError(503))
    with pytest.raises(ApiError):
        retry_with_policy(fn, {"max_retries": 3})
    assert fn.call_count == 3
```

**集成验证 `ExponentialBackoff.sleep` 被调用：**

```python
@patch("kzocr.engines.errors.ExponentialBackoff.sleep")
def test_retry_calls_backoff_between_attempts(mock_sleep):
    fn = MagicMock(side_effect=[ApiError(500), "ok"])
    retry_with_policy(fn, {"max_retries": 3})
    assert mock_sleep.call_count == 1  # 只在失败后 sleep
```

**策略表全覆盖：**

| 策略 | Key | 测试覆盖 |
|------|-----|---------|
| 指数退避 | `"api"` | 3 次重试，耗尽抛出源异常 |
| 重 OCR | `"oversize"` | 1 次重试 + 不同参数（max_tokens×1.8） |
| 幂等 | `"idempotent"` | 0 次重试，失败直接抛 |
| 跳过 | `"skip"` | 0 次重试，记录日志不抛异常 |

**注意：** 计划中 `retry_with_policy` 的签名使用 `policy: dict`，但策略参数分散在 dict 中。测试需要覆盖不同策略 dict 的语义。建议考虑 `@dataclass` 定义策略，以获取 IDE 类型检查和更好的测试可见性。

### 1.3 `ExponentialBackoff` 集成测试（与现有 18 项合并）

现有 `TestExponentialBackoff`（4 项）覆盖了：
- 递增性（`test_increases_with_attempt`）
- 最大上限（`test_max_delay_capped`）
- 抖动范围（`test_jitter_range`）
- sleep 阻塞性（`test_sleep_blocking`）

**新增建议：**

```python
def test_retry_after_respected():
    """RateLimitedError 带 retry_after 头时优先使用该值而非指数退避。"""
    # 验证 retry_with_policy 读取 exc.retry_after，调用 backoff.sleep(retry_after)
```

---

## D2 —— VLM 主循环重试行为测试

### 2.1 Mock 方案评估

**评估：** Mock 方案可行。现有 `test_vlm.py` 已重度使用 `unittest.mock.patch` mock `fitz.open` 和 `_init_vlm_adapter`，D2 在此基础上增加 mock `retry_with_policy` 或 mock `ExponentialBackoff.sleep` 即可。

**层次建议：**

```
┌─ test_vlm.py（现有 mock _run_vlm 入口）
│   已覆盖：路由正确性、PDF 渲染→VLM→Markdown 流程
│
└─ test_vlm_retry.py（新增：专注于重试路径）
     ├─ 单元级别：mock retry_with_policy 返回值（成功/失败/超阈值）
     └─ 集成级别：mock ExponentialBackoff.sleep（验证重试次数）
```

### 2.2 具体测试用例

**基础路径——API 错误重试后成功：**

```python
@patch("kzocr.engine.run.retry_with_policy")
@patch("kzocr.engine.run.fitz.open")
@patch("kzocr.engine.run._init_vlm_adapter")
def test_vlm_retry_api_error_then_success(mock_init, mock_fitz, mock_retry):
    """API 错误经 1 次重试后成功，继续处理后续页。"""
    mock_page = MagicMock()
    mock_page.get_pixmap.return_value = MagicMock(samples=b"x"*300, n=3, height=100, width=200)
    mock_doc = MagicMock(__len__=lambda s: 2, __iter__=lambda s: iter([mock_page, mock_page]))
    mock_fitz.return_value = mock_doc
    mock_vlm = MagicMock()
    mock_vlm.engine_label = "vlm"
    mock_init.return_value = mock_vlm

    # 第 1 页重试 2 次后成功，第 2 页直接成功
    mock_retry.side_effect = [
        "方用白术三钱。",  # 第 1 页第 1 次 → 失败（由 retry 重试）
        # retry_with_policy 内部重试，外部看到最终结果
    ]
    # 实际实现中 retry_with_policy 包装 recognize，所以应 mock 被包裹的函数
```

**精确方案：** 更清晰的方案是直接 mock `ExponentialBackoff.sleep`（零睡眠），然后用 `vlm.recognize_pages.side_effect` 依次抛出 `ApiError`、返回正常文本：

```python
@patch("kzocr.engine.run.ExponentialBackoff.sleep")
@patch("kzocr.engine.run.fitz.open")
@patch("kzocr.engine.run._init_vlm_adapter")
def test_vlm_page_retries_on_api_error(mock_init, mock_fitz, mock_sleep):
    """单页抛出 ApiError，重试后成功。"""
    # ... PDF mock 同上 ...
    mock_vlm = MagicMock()
    mock_vlm.recognize_pages.side_effect = [ApiError(502), "恢复后的文本"]
    mock_init.return_value = mock_vlm

    cfg = Config(use_vlm=True, kimi_engine_dir="/tmp")
    result = run_engine("/fake.pdf", config=cfg)
    assert "恢复后的文本" in result.final_markdown
    assert mock_sleep.call_count == 1  # 1 次重试等待
```

**上限路径——OverSizeError 重 OCR：**

```python
@patch("kzocr.engine.run.ExponentialBackoff.sleep")
@patch("kzocr.engine.run.fitz.open")
@patch("kzocr.engine.run._init_vlm_adapter")
def test_vlm_oversize_triggers_rerun(mock_init, mock_fitz, mock_sleep):
    """OverSizeError 触发重 OCR，使用更大的 max_tokens。"""
    # ... mock setup ...
    mock_vlm.recognize_pages.side_effect = [
        OverSizeError(actual=3000, threshold=2000),
        "重试后的较短文本",
    ]
    mock_init.return_value = mock_vlm
    cfg = Config(use_vlm=True, kimi_engine_dir="/tmp")
    result = run_engine("/fake.pdf", config=cfg)
    assert "重试后的较短文本" in result.final_markdown
    # 验证第二次调用使用了更大的 max_tokens
    second_call_args = mock_vlm.recognize_pages.call_args_list[1]
    assert second_call_args[1].get("max_tokens") is not None
```

**耗尽路径——重试全部失败：**

```python
@patch("kzocr.engine.run.ExponentialBackoff.sleep")
@patch("kzocr.engine.run.fitz.open")
@patch("kzocr.engine.run._init_vlm_adapter")
def test_vlm_retry_exhausted_skips_page(mock_init, mock_fitz, mock_sleep):
    """某页重试耗尽后跳过该页，继续处理后续页。"""
    mock_vlm.recognize_pages.side_effect = [
        ApiError(503),  # 第 1 页尝试 1
        ApiError(503),  # 第 1 页尝试 2
        ApiError(503),  # 第 1 页尝试 3 → 耗尽，跳过
        "第 2 页内容",    # 第 2 页成功
    ]
    mock_init.return_value = mock_vlm
    cfg = Config(use_vlm=True, kimi_engine_dir="/tmp")
    result = run_engine("/fake.pdf", config=cfg)
    assert "第 2 页内容" in result.final_markdown
    # 第 1 页的 _record_failure 被调用
```

### 2.3 失败计数验证

需要验证 `_record_failure(pipeline_state, page_num, error_type)` 在重试耗尽后被调用。可以通过：
- mock `_record_failure` 并断言 `call_count`
- 或验证 `pipeline_state.failures` 字典包含预期条目

### 2.4 风险提示

现有 `test_vlm.py` 大量使用 `MagicMock` 替代真实对象，D2 引入更深的控制流分支后，`MagicMock` 的递归 mock 行为可能导致测试表面通过但实际未覆盖分支。建议：
- mock 最小化：只 mock 外部 IO（`fitz`、网络调用），不 mock 内部控制流
- 在测试中添加断言验证 `vlm.recognize_pages.call_count == N`（锁定预期重试次数）

---

## D3 —— VLM 断点续跑（checkpoint resume）

### 3.1 测试策略总览

**评估：** D3 测试需要文件系统交互，建议采用 `tmp_path` fixture（现有 `test_atomic.py` 已使用）。核心是验证：

1. **中断前已完成的页** → 跳过，不重复 OCR
2. **部分完成的页（缓存存在但写入失败）** → 重新 OCR（`is_complete` 返回 False）
3. **完全中断后恢复** → 所有已完成页被跳过，未完成页从断点继续
4. **`KZOCR_CLEAR_CACHE=1`** → 清除所有缓存后重新 OCR

### 3.2 推荐测试文件

**新文件：** `tests/test_vlm_checkpoint.py`（与现有 VLM 测试分离，专注于文件系统交互）

### 3.3 具体测试用例

**中断 + 恢复：**

```python
@patch("kzocr.engine.run.fitz.open")
@patch("kzocr.engine.run._init_vlm_adapter")
def test_vlm_checkpoint_resume(mock_init, mock_fitz, tmp_path):
    """中断后恢复：已有缓存的页跳过，未完成的页继续 OCR。"""
    # Setup: 5 页 PDF，前 2 页已有缓存
    mock_doc = _mock_pdf_doc(5)
    mock_fitz.return_value = mock_doc
    mock_vlm = MagicMock()
    mock_vlm.recognize_pages.side_effect = ["页 3", "页 4", "页 5"]
    mock_vlm.recognize_page.side_effect = ["页 3", "页 4", "页 5"]
    mock_vlm.engine_label = "vlm"
    mock_init.return_value = mock_vlm

    # 手动创建前 2 页缓存
    from kzocr.engine.run import _get_vlm_cache_path
    for p in range(1, 3):
        path = _get_vlm_cache_path(tmp_path, "TEST-BOOK", p)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(path, f"页 {p}")

    cfg = Config(use_vlm=True, kimi_engine_dir="/tmp", kzocr_output_dir=tmp_path)
    result = run_engine("/fake/book.pdf", book_code="TEST-BOOK", config=cfg)

    # 验证：只有 3 页被实际 OCR（页 3-5）
    assert mock_vlm.recognize_pages.call_count == 3
    # 验证：最终 Markdown 包含全部 5 页
    assert "## 第 1 页" in result.final_markdown
    assert "## 第 5 页" in result.final_markdown
```

**缓存清除：**

```python
@patch("kzocr.engine.run.fitz.open")
@patch("kzocr.engine.run._init_vlm_adapter")
def test_vlm_cache_clear_env(mock_init, mock_fitz, tmp_path, monkeypatch):
    """KZOCR_CLEAR_CACHE=1 时清除所有缓存，全部重新 OCR。"""
    monkeypatch.setenv("KZOCR_CLEAR_CACHE", "1")
    # ... 同上 setup，但所有 5 页都被 OCR
    # 验证：mock_vlm.recognize_pages.call_count == 5（全部重新识别）
```

**缓存文件不完整（is_complete 返回 False）：**

```python
def test_vlm_partial_cache_reruns(..., tmp_path):
    """缓存文件存在但内容为空（is_complete=False）→ 重新 OCR。"""
    # 手动创建空缓存文件
    path = _get_vlm_cache_path(tmp_path, "TEST-BOOK", 1)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()  # 空文件
    # "第 1 页" 应该被重新 OCR（而不跳过）
```

**缓存目录结构验证：**

- 输出目录应为 `{output_dir}/vlm_cache/{book_code}/page_{N:04d}.txt`
- `test_atomic.py` 有 `test_atomic_write_creates_parent_dirs` — D3 测试应验证 `_get_vlm_cache_path` 创建正确路径

### 3.4 风险提示

D3 依赖于 `atomic.py` 的 `is_complete`（已测试）和 `atomic_write`（已测试），因此 D3 测试的失败不应来自底层原子写原语。D3 测试应关注：
- 业务逻辑（何时跳过、何时重新 OCR）
- 环境变量配置（缓存清除开关）
- 多个 book_code 的缓存隔离

---

## D4 —— 层级异常检测

### 4.1 覆盖度评估

**评估：** P3 低优先，但测试简单且可以完全不依赖外部依赖。推荐纯单元测试，与 D1 异常类型测试结合。

### 4.2 推荐测试文件

**新文件：** `tests/test_hierarchy.py`

### 4.3 具体测试用例

**`HierarchyAnomaly` dataclass：**

```python
def test_hierarchy_anomaly_dataclass():
    """@dataclass 字段正确初始化。"""
    ha = HierarchyAnomaly(recipe_no="16.7.1", depth=3, source_page=42)
    assert ha.recipe_no == "16.7.1"
    assert ha.depth == 3
    assert ha.source_page == 42
    assert ha.resolution == "pending"
```

**`check_hierarchy_anomaly` 功能测试：**

| 测试 | 输入 | 预期输出 |
|------|------|---------|
| 正常编号 | "15.1 黄芪桂枝五物汤" | 0 个异常 |
| 三级编号 | "16.7.1 防己黄芪汤" | 1 个异常（depth=3） |
| 混合文本 | "15.1 方一\n16.7.1 方二" | 1 个异常（仅 16.7.1） |
| 无编号文本 | "此为随证加减之方" | 0 个异常 |
| 四级编号 | "10.2.3.1" | 1 个异常（depth=4） |
| 分页跨页编号 | 两页文本，第二页出现了"15.1.2" | 1 个异常 |

```python
@pytest.mark.parametrize("text,page_num,expected", [
    ("15.1 黄芪桂枝五物汤", 1, 0),
    ("16.7.1 防己黄芪汤", 5, 1),
    ("15.1 方一\n16.7.1 方二", 10, 1),
    ("此为随证加减之方", 3, 0),
    ("10.2.3.1 复杂编号", 7, 1),
])
def test_check_hierarchy_anomaly(text, page_num, expected):
    results = check_hierarchy_anomaly(text, page_num)
    assert len(results) == expected
```

**异常字段验证：**

```python
def test_hierarchy_anomaly_fields():
    results = check_hierarchy_anomaly("16.7.1 防己黄芪汤", 5)
    assert len(results) == 1
    assert results[0].recipe_no == "16.7.1"
    assert results[0].depth == 3
    assert results[0].source_page == 5
```

**JSON 输出验证：**
- D4 输出为 `{output_dir}/hierarchy_anomalies.json`
- 测试应验证多个异常正确序列化为 JSON 数组
- 无异常时应输出空数组 `[]` 或无文件

**集成到 `_run_vlm` 的测试（可选，P3 低优先）：**

```python
@patch("kzocr.engine.run.check_hierarchy_anomaly")
def test_vlm_hierarchy_anomaly_integration(mock_check):
    """验证 VLM 主循环在每页后调用 check_hierarchy_anomaly。"""
    # ... mock fitz + vlm ...
    mock_check.return_value = []
    result = run_engine(...)
    assert mock_check.call_count == total_pages
```

### 4.4 风险提示

D4 的正则检测模式尚未定义。测试编写需要先确认编号检测规则。建议：
- 先定义 `check_hierarchy_anomaly` 中用于匹配编号的正则表达式
- 测试覆盖中英文编号格式（"16.7.1" vs "16-7-1" vs "十六·七·一"）
- 区分页首的"第 1 页"不应该被当作层级异常

---

## 跨项风险评估

### 测试之间的依赖

| 依赖 | 方向 | 影响 |
|------|------|------|
| D3 → atomic.py | D3 依赖 `is_complete` + `atomic_write` | 低——这两个函数已有完整测试 |
| D2 → D1 | D2 使用 `retry_with_policy` + 异常类型 | 中——D1 测试必须优先于 D2；D2 测试可 mock `retry_with_policy` 解耦 |
| D4 → D1 | D4 使用 `HierarchyAnomalyError` | 低——异常类可独立测试和 mock |

### 测试优先级建议

| 优先级 | 项目 | 文件 | 预估用例数 |
|--------|------|------|-----------|
| P1（先通） | D1 异常构造 + 继承 | `tests/test_errors.py` | 10–12 |
| P1 | D1 `retry_with_policy` 三路径 | `tests/test_errors.py` | 6–8 |
| P1 | D2 重试 + 耗尽 + 超阈值 | `tests/test_vlm_retry.py` | 6–8 |
| P2 | D3 断点续跑（中断 + 恢复 + 清除） | `tests/test_vlm_checkpoint.py` | 4–6 |
| P3 | D4 层级异常检测 | `tests/test_hierarchy.py` | 6–8 |

### 总预估用例数

约 **32–42 个新增测试用例**，加上现有 39 个测试（total ~71–81）。所有 D1–D4 测试理论上都能完全自动化运行（无需 llama-server 或真实 PDF），CI 友好。

### 一个需要计划澄清的问题

D2 计划代码中 `overSizeError` 捕获路径有一个潜在的逻辑问题——重试时抛 `OverSizeError` 但 `recognize_pages` 是 VLM 调用，`OverSizeError` 的重试策略是 1 次重试（而非指数退避）。测试需要验证这个分支。另外，D2 计划中的 `except RateLimitedError: backoff.sleep(attempt + 1)` 直接 sleep 而不是通过 `retry_with_policy`——这意味着 D2 内联使用了退避逻辑而非统一入口。测试应当覆盖这种"双重路径"的存在，并验证逻辑一致性。
