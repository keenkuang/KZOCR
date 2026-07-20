# load_benchmarks 逐行容错改造

## 背景

`kzocr/scheduler/registry.py` 的 `load_benchmarks()` 从 NDJSON 文件加载引擎基准事件时，`try/except` 包裹了整个文件处理循环（含逐行 JSON 解析），而非单行级别的异常处理：

```python
for ndjson in sorted(base.glob("*.ndjson")):
    engine = ndjson.stem
    try:
        with ndjson.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                _apply_event(self.get(engine), json.loads(line))
    except (json.JSONDecodeError, OSError):
        continue
```

**后果**：任何一行损坏（如写入过程中文件崩溃、磁盘静默错误、或文本中混入乱码）会导致 `json.loads` 抛出 `JSONDecodeError`，`except` 捕获后执行 `continue` 跳至**下一个文件**，当前文件剩余有效行全部丢失。

## 影响

- 正常情况下 NDJSON 文件不应有损坏行（`persist_benchmarks` 追加写入保证每行是一个完整 JSON）。
- 但跨进程并发写入（多个 Celery worker 同时向同一文件追加）可能导致行交错损坏。
- 现有容错策略是全盘放弃，属「安全优先但数据损失」的保守设计。改为逐行跳过更合理：丢了损坏行不影响同文件其余行的加载。

## 改动方案

### 1. 代码—kzocr/scheduler/registry.py

将 `load_benchmarks` 中的 `try/except` 从句外移到内层循环：

```python
def load_benchmarks(self) -> None:
    if self.benchmark_dir is None:
        return
    base = Path(self.benchmark_dir)
    if not base.is_dir():
        return
    for ndjson in sorted(base.glob("*.ndjson")):
        engine = ndjson.stem
        try:
            with ndjson.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        _apply_event(self.get(engine), json.loads(line))
                    except (json.JSONDecodeError, OSError):
                        continue  # 只跳过损坏行，不放弃整文件
        except EOFError:
            continue  # 文件读取本身失败（如被截断）：跳过整文件
```

- 外层 `except` 从 `(json.JSONDecodeError, OSError)` 缩减为仅 `EOFError`（文件级读取失败确实应放弃整文件）。
- 内层新增 `try/except (json.JSONDecodeError, OSError)`：仅跳过损坏行，不放弃文件剩余行。

### 2. 测试 — tests/test_registry.py

更新 `test_load_benchmarks_corrupt_line_skipped`，断言改为 `total_calls == 2`（两行有效都被加载，中间损坏行被跳过）：

```python
def test_load_benchmarks_corrupt_line_skipped(self, tmp_path):
    """损坏行被跳过，同文件其余有效行仍被加载。"""
    bdir = tmp_path / "bench"
    bdir.mkdir()
    good = json.dumps({"engine": "paddleocr", "page": 1, "latency_ms": 100,
                       "glyph_status": "PASS", "success": True}) + "\n"
    (bdir / "paddleocr.ndjson").write_text(
        good + "不是一个 json\n" + good, encoding="utf-8")
    r = EngineRegistry(benchmark_dir=str(bdir))
    r.register(_reg(meta=_am("paddleocr")))
    r.load_benchmarks()
    s = r.get("paddleocr").stats
    assert s.total_calls == 2  # 修复后：2 行有效都被加载
    assert s.glyph_pass_count == 2
```

## 风险

- `OSError` 原在外层可捕获 `Path.open` 失败（如权限不足/文件被删除）。拆到内层后，文件打开失败仍由内层 `try/except OSError` 捕获（读到的第一行就会抛），行为一致；唯一漏掉的是 `OSError` 在 `ndjson.open` 时的抛出一一但此时内层 try 尚未进入，异常会向上冒泡到 改前的 `except (json.JSONDecodeError, OSError)` ——已退化为 `except EOFError`，不捕获 `OSError`。
  - **缓解方案**：保留外层 `except OSError`（文件打开/遍历失败），仅将 `JSONDecodeError` 下放内层。即外层 `except EOFError` 改回 `except OSError`：
  ```python
  except OSError:
      continue  # 文件遍历/打开失败：跳过整文件
  ```

## 验收标准

1. `ruff check kzocr/ tests/` — 0 errors
2. `pytest tests/test_registry.py` — 全量通过（含更新后的 corrupt line 测试）
3. `pytest tests/ -q --ignore=tests/benchmarks` — 732 passed + 2 skipped 无回归
