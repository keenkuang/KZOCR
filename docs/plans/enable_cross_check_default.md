# 跨引擎校验默认开启（KZOCR_ENABLE_CROSS_CHECK）

## 状态

**已实现（2026-07-22）**。注意：原计划描述的代码事实已过期——自 v0.7 稳定后，`SchedulerConfig.cross_check` 就**默认 `True`**（见 `kzocr/config.py:57`，注释明写「自 v0.7 稳定后默认开」），且 `kzocr/engine/run.py:117` 在构造 `EngineOverrides` 时用 `sc.cross_check` 覆盖其默认值。因此经 `run_engine` / CLI / 生产路径，跨引擎校验**早已默认开启**。

本次实际改动：把 `EngineOverrides.enable_cross_check` 的「被覆盖的默认值 `False`」翻为 `True`，使「直接调用 `orchestrate_book` 不带 overrides」的内部默认路径也与运行时一致，并同步修正本计划文件的过期描述。

## 背景

- v0.7 跨引擎校验由 `KZOCR_ENABLE_CROSS_CHECK` 控制，`SchedulerConfig.cross_check` 默认 `True`（设 `0` 可关），经 `run.py:117` 注入 `EngineOverrides.enable_cross_check`。
- `EngineOverrides.enable_cross_check`（原默认 `False`）仅在「不经由 `run_engine`、直接调 `orchestrate_book(overrides=None)`」时生效，是有意保留的「无 overrides 安全默认」。
- 此前有回归测试 `test_cross_check_disabled_by_default` 守护「默认不触发」，本次改为守护「默认触发 + 显式 `False` 仍可关闭」（`test_cross_check_enabled_by_default` / `test_cross_check_can_be_disabled`）。

## 改动点

### 1. kzocr/scheduler/scheduler.py:83

`EngineOverrides.enable_cross_check: bool = False` → `True`（注释同步「默认开」）。

### 2. 测试同步 — tests/test_cross_divergence.py

- 原 `test_cross_check_disabled_by_default`（断言默认不触发）已不适用，改为：
  - `test_cross_check_enabled_by_default`：默认 `overrides=None` → 有 Tier2 时触发 cross-check、分歧落库。
  - `test_cross_check_can_be_disabled`：显式 `EngineOverrides(enable_cross_check=False)` → 仍可不触发，守护关闭能力。

### 3. 计划文档自修正

本文件原计划误称「`config.py` 中 `enable_cross_check: bool = False`」并引用不存在的 `EngineConfig`，已据实修正。

## 影响

- `orchestrate_book(overrides=None)` 默认走跨引擎校验（与生产路径一致）。
- 经 `run_engine` / CLI 行为无变化（原本就是 `True`）。
- 仍可用 `KZOCR_ENABLE_CROSS_CHECK=0` 或显式 `EngineOverrides(enable_cross_check=False)` 关闭。

## 验收标准

1. `EngineOverrides().enable_cross_check == True`
2. `ruff check kzocr/ tests/` — 0 errors
3. `pytest tests/ -q` — 全量通过无回归
