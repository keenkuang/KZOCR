# KZOCR v0.7 E1 实现评审 — 软件工程（sweng）

- **评审角色**：软件工程
- **评审日期**：2026-07-10
- **评审对象**：`kzocr/scheduler/registry.py`（`1d52cae`）、`tests/test_registry.py`、`kzocr/engine/types.py`
- **范围声明**：E1 聚焦首版；E2/E4 的竖排/egress/衰减/预算/overrides 未实现，不计入本 E1 阻塞。

---

## 1. 【阻塞 / 必须修复】

- **`registry.py:106,120-126` — `record` 的 `glyph` 参数契约断裂**。依据设计 §6.2：编排层 `registry.record(engine, success=…, glyph=verdict)` 传入 `GlyphVerdict` 对象（`types.py`），而非 `str`。当前签名 `glyph: Optional[str]` 且用 `glyph == "PASS"/"FAIL"/"UNKNOWN"` 比较，编排层落地后比较恒为 `False`，字形统计全部落空且无报错，属静默契约断裂。建议：`glyph: Optional[GlyphVerdict] = None`，在 `record` 内取 `status = glyph.status` 再比对 `GlyphStatus` 枚举成员。
- **`registry.py:46 & 82` — `adapter` 类型应为 `Optional[EngineRunner]`**。依据 §2.2 明确 `adapter: EngineRunner | None`。当前 `from kzocr.engine.types import … EngineRunner` 未导入，`Callable` 弱化接口约束，Orchestrator 调用 `run_page/run_book` 失去类型保障。建议两处统一改为 `Optional[EngineRunner]`。
- **`registry.py:143-146` — `select_candidates.prefer` 应为 `Literal["speed","accuracy"] | None`**。依据设计 §4.5/CLI（types.py 用 `Literal` 的既有约定）。`Optional[str]` 允许任意脏值，运行时才暴露逻辑分支缺失。建议 `from typing import Literal` 并收窄类型。

## 2. 【重要 / 强烈建议】

- **`registry.py:114` — 未注册引擎抛 `KeyError`**。依据设计 §7 / E1.6 要求 `errors.py` 新增 `SchedulerError(OcrError)`。裸 `KeyError` 与调度语义耦合弱、不可针对性捕获。建议定义 `SchedulerError`（复用 `kzocr/engines/errors.py` 的 `OcrError` 体系）并抛出，测试同步改为 `pytest.raises(SchedulerError)`。
- **`registry.py:43,89` — `config` 应改用 `EngineConfig`**。依据 `types.py` 已定义 `EngineConfig`，注释称「替代裸 dict」。当前 `dict` 与设计内部不一致，凭证引用缺乏字段约束。建议后续迁移（E1 聚焦可接受，但建议记录 TODO）。

## 3. 【优化 / 可选】

- `registry.py:120-126` — 字形归类逻辑可抽为 `_classify_glyph(status) -> Counter key`，消除字符串魔法比较，配合枚举化后更易维护（DRY）。
- **dataclass 可变默认陷阱**：确认 `stats` 用 `field(default_factory=…)`、`glyph_unknown_count` 等遵循无陷阱，良好；常量 `BAYESIAN_C/PRIOR` 命名规范，良好。
- `from __future__ import annotations` 在 registry.py/types.py/test 均已含，一致性通过。
