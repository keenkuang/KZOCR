# KZOCR v0.7 E1 实现评审 — 多角色汇总（round 11）

- **评审日期**：2026-07-10
- **评审对象**：`kzocr/scheduler/registry.py`（`1d52cae`，分支 `feat/v0.7-registry`）、`tests/test_registry.py`、`kzocr/scheduler/__init__.py`
- **参与角色**（8 个）：架构师 / 安全 / 性能 / 测试 / 软件工程 / 运维 / 领域 / 项目管理
- **客观基线**：`ruff check` 无报错；`pytest tests/test_registry.py` 10 passed。

---

## 总体裁决：**有条件通过（聚焦首版健康，但需先修 1 个真实崩溃 bug + 关键类型契约）**

E1 是设计文档明确的「聚焦首版」——`__init__.py` 已声明仅落地「注册中心 + 候选选择」，竖排/egress/衰减/预算/overrides（E2/E4）、benchmark 持久化/mark_unavailable/probe（E1.5）均未实现，**不计入本 E1 阻塞**。但本提交作为后续 E2–E4 的地基，存在 1 项被 **3 个角色独立交叉确认**的硬 bug，以及若干类型契约/安全加固项，须修复后方可合入 `main`。

---

## 一、跨角色共识问题（按优先级）

### 🔴 P0 — 必须修复（合入前）

**1. 除零崩溃（performance + testing + pm 三方独立确认）**
- `registry.py:63` 的 `avg_latency_per_page_ms` 仅在 `total_pages==0` 时返回默认值；当 `pages>0` 且从未传 `latency_ms`（或传 0）时 `total_latency_ms==0` → 返回 `0.0` → `_bayesian_score`（`registry.py:140`）`1.0/0.0` 抛 `ZeroDivisionError`。
- **复现**：`reg.record("x", success=True, glyph="PASS")` 后调 `select_candidates(reg, tier=1)` 即崩。极常见路径，当前测试未覆盖。
- **修复**：兜底判定改为「无延迟样本」——`if self.stats.total_latency_ms == 0: return AVG_LATENCY_DEFAULT_MS`；并补一条回归测试。

### 🟠 P1 — 强烈建议（合入前，属 E1 自身范围）

**2. `record()` 的 `glyph` 类型契约断裂**（architect + sweng + domain 确认）
- 当前 `glyph: Optional[str]` 用 `== "PASS"` 比较，但设计 §6.2 编排层将传入 `GlyphVerdict` 对象 → E2 接入后比较恒 `False`，**字形统计静默失效**。
- 同时仅处理 PASS/FAIL/UNKNOWN，**静默丢弃 RARE/UNCERTAIN**（中医古籍 rare 字/雕版异体字占比高，会导致通过率被系统性低估）。
- **修复**：参数改为 `Optional[GlyphVerdict]`（取 `.status`），按 `GlyphStatus` 枚举归类；补 `glyph_rare_count`/`glyph_uncertain_count`。

**3. `adapter` 应改为 `Optional[EngineRunner]`**（architect + sweng）—— `types.py` 已定义协议，替换 `Callable` 以静态约束 Orchestrator 调用。

**4. `select_candidates.prefer` 应改为 `Literal["speed","accuracy"] | None`**（sweng）。

**5. 异常类型：`record` 抛 `KeyError` → 应抛 `SchedulerError(OcrError)`**（sweng + pm）—— 设计 §7 已要求该异常类，本提交尚未落地。

**6. 安全加固（§3.3 硬性要求）**（security + architect + sweng + pm）
- `EngineStats`/`EngineRegistration` 未实现 `__repr__`/`__str__` 掩码，默认 repr 会泄露 `config`（含 `api_key_env` 名）与 `last_error`。
- `config` 用裸 `dict`，与 `types.py` 中已定义的 `EngineConfig`（「替代裸 dict」）矛盾。须二选一：统一为 `EngineConfig` 或显式否决。

### 🟡 P2 — 排期（E1.5/E2，非本提交阻塞）

- **benchmark NDJSON 持久化（§7.1）未实现**：后续必须 O(1) 行级追加，复用 `engines/atomic.py`，禁全文覆写。
- **状态位缓存未落地**（ops + pm）：`mark_unavailable` / `EngineStatus` 流转缺入口，`status` 字段未被 `select_candidates` 消费，§4.1 资源过滤成空文。
- **`last_seen` 跨进程语义落空**（ops）：注释称「支持跨进程持久化」但无 save/load，须 E1.5 补齐并修正注释。
- **可观测性缺口**（ops）：`select_candidates`/`record` 零 logging，§6.5 trace 未接入。
- **文档与现实矛盾**（pm + testing）：设计 §8 Phase 1 交付标准/§10.1 声称含 benchmark/probe/conftest/SchedulerError，本提交未含，须在 `v0.7.md` 显式拆分「已落地 / 延期」。
- **E1 无调用方**：CLI（`--engine`/`--prefer`，§7 E5）尚未接入，建议 E2 起加组合烟雾测试防成"永久内部模块"。

---

## 二、各角色摘要

| 角色 | 阻塞 | 重要 | 关键结论 |
|------|------|------|---------|
| 架构 | 1 | 3 | glyph 契约断裂、adapter 应为 EngineRunner、config 与 EngineConfig 冲突、select_candidates 缺 E2 扩展预留 |
| 安全 | 0 | 2 | 无明文泄露；但 §3.3 的 `__repr__` 掩码与 `EngineConfig` 类型缺失 |
| 性能 | 1 | 2 | 除零崩溃确认；贝叶斯公式与设计一致；排序开销可忽略 |
| 测试 | 1 | 4 | 除零路径无测试；§10.1 覆盖缺口（probe/benchmark/状态/去重）；mock 合规 |
| 软件工程 | 3 | 2 | glyph 契约、adapter 类型、prefer Literal 三项阻塞；KeyError→SchedulerError |
| 运维 | 0 | 3 | 可观测性/状态位/last_seen 三处契约缺口，须排期 |
| 领域 | 0 | 2 | record 静默丢弃 RARE/UNCERTAIN，古籍通过率被低估；冷启动常量不一致、tier 无界 |
| 项目管理 | 0 | 4 | 范围收缩合理；文档未同步；除零缺陷；SchedulerError 未定义 |

> 注释：`pytest 10 passed` 仅证明 happy-path 通过；**除零与 glyph 契约两项缺陷在纯单元测试下不暴露**，将在 E2 编排层接入时爆发，故列为 P0/P1 必须前置修复。

---

## 三、建议修复顺序

1. **P0**：`avg_latency_per_page_ms` 兜底改 `total_latency_ms==0` + 回归测试。
2. **P1**：`record` 的 `glyph` 枚举化（顺带补 RARE/UNCERTAIN 计数）；`adapter: EngineRunner`；`prefer: Literal`；新增 `SchedulerError` 并替换 `KeyError`；`__repr__` 掩码 + `config`/`EngineConfig` 决策。
3. **P2**：实现 benchmark 持久化 / mark_unavailable / probe / conftest；补 logging；在 `v0.7.md` 同步 Phase 1 实际落地范围。

---

*附录：本轮 8 角色报告见同目录 `architect.md` / `security.md` / `performance.md` / `testing.md` / `sweng.md` / `ops.md` / `domain.md` / `pm.md`。*
