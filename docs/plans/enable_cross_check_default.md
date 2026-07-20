# 跨引擎校验默认开启（KZOCR_ENABLE_CROSS_CHECK）

## 背景

v0.7 跨引擎校验（cross-check）由环境变量 `KZOCR_ENABLE_CROSS_CHECK` 控制，当前默认关闭（`config.py` 中 `enable_cross_check: bool = False`）。用户需显式设 `KZOCR_ENABLE_CROSS_CHECK=1` 才能启用跨引擎分歧检测 + 视觉仲裁闭环。

v0.7 调度器已稳定运行（主线上经过多轮 e2e、16 本古籍扩面测试），跨引擎校验的开销经验证可接受（双引擎并行，最坏情况为两倍页级延迟而不是页×引擎数的乘法级复杂度，因 `run_engines_concurrent` 取最快返回即停止）。建议默认开启，使所有 kzocr 用户自动获得分歧检测能力。

## 改动点

### 1. 配置默认值 — kzocr/config.py

```python
@dataclass
class EngineConfig:
    ...
    enable_cross_check: bool = True   # False → True
```

### 2. 环境变量文档注释 — kzocr/config.py

更新 `enable_cross_check` 的 docstring 或旁边注释，标注「自 v0.7 稳定后默认开；设 0 可关闭」。

### 3. CLI 默认值对齐 — kzocr/cli.py（如有）

检查 CLI 是否覆盖默认值。当前 `cli.py:38` 设 `KZOCR_ENABLE_CROSS_CHECK=1`，维持不变（显式开启的 CLI 入口仍保持）。

### 4. 测试

- 确认 `test_orchestrator.py` 和 `test_scheduler.py` 中默认行为测试不受影响（mock 已隔离实际开关值）
- 新增一例：验证 `EngineConfig()` 构造后 `enable_cross_check` 为 True

## 影响

- **正面**：新用户开箱即用获得跨引擎分歧检测；`kzocr smoke —skip-push` 等冒烟路径自动覆盖全流程。
- **负面**：无实际负面——开关无资源泄漏风险，不启用时仅为单次 `if` 判断（成本可忽略）。如需关闭仍可通过环境变量。

## 验收标准

1. `EngineConfig().enable_cross_check == True`
2. `ruff check kzocr/ tests/` — 0 errors
3. `pytest tests/ -q --ignore=tests/benchmarks` — 全量通过无回归
