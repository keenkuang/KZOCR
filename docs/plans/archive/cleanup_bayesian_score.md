# 清扫 _bayesian_score：移除或内联

## 背景

`kzocr/scheduler/registry.py` 有一个模块级函数 `_bayesian_score(reg: EngineRegistration) -> float`（line 335），是贝叶斯平均评分的纯函数实现。它原被模块级 `select_candidates` 调用（已于 `f20ac3d` 删除），现为零生产调用方——仅被 `tests/test_registry.py` 直接引用（line 24、`TestBayesianScore` 类 5 例测试）。

## 选项

### 选项 A：直接删除（推荐）

`_bayesian_score` 的评分逻辑与 `EngineScheduler._compute_bayesian_score`（`scheduler.py`）重复。后者是当前实际调度器使用的评分函数。前者已无生产用途。

**操作步骤**：
1. 删除 `kzocr/scheduler/registry.py` 中 `_bayesian_score` 的定义（line 335–343）
2. 删除 `tests/test_registry.py` 中的 `TestBayesianScore` 类（line 256–284 附近）及 import
3. 删除 `kzocr/scheduler/registry.py` 中 `BAYESIAN_C` / `BAYESIAN_PRIOR` 常量（line 34–35）——若仅被 `_bayesian_score` 引用则一并删除

**风险**：极低。`_bayesian_score` 未在任何其他生产模块中被 import（grep 确认），测试引用已隔离。

### 选项 B：内联到 `EngineStats` 类方法

将 `_bayesian_score` 改为 `EngineStats.bayesian_score()` 实例方法，保持可用且带类型：

```python
@dataclass
class EngineStats:
    ...
    def bayesian_score(self) -> float:
        n = self.total_pages
        pass_rate = ...  # 衍生计算
        latency = self.avg_latency_per_page_ms
        return (pass_rate * n + BAYESIAN_C * BAYESIAN_PRIOR) / (n + BAYESIAN_C) * (1.0 / latency)
```

**优点**：比删除更多保留 API 表面；便于未来 `EngineScheduler` 引用（与 `EngineStats.decay()` 一致）
**缺点**：当前仍无生产调用方；增加维护负担。

## 建议

选 A（删除）。`_bayesian_score` 的功能已完全被 `EngineScheduler._compute_bayesian_score` 覆盖，且后者是实际使用的评分函数。保留 `_bayesian_score` 只会让读者困惑「两个贝叶斯函数有何不同」。

## 验收标准

1. `ruff check kzocr/ tests/` — 0 errors
2. `pytest tests/test_registry.py` — 全量通过，净减 5 例（TestBayesianScore）
3. `pytest tests/ -q --ignore=tests/benchmarks` — 净减 5 = 727 passed + 2 skipped
