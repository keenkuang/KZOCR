# Architect Review — v0.7 自适应 OCR 引擎编排层 (round9)

**Date**: 2026-07-10
**Scope**: `docs/plans/ocr-engine-unification.v0.7.md`(概览) + `ocr-engine-unification.v0.7-DETAILED.md`(详设) + `kzocr/engine/types.py`(已落地代码)
**Verdict**: 条件通过 — 架构方向正确,存在若干跨文档/代码契约不一致,需在进入编码前同步。

## Findings

| ID | Severity | Location | Issue | Recommendation |
|----|----------|----------|-------|----------------|
| ARCH-1 | HIGH | overview §2.2 / DETAILED §6.1 / §6.4 | `EngineRunner(Protocol)` 同时声明 `run_page` + `run_book`,但 §6.4 适配映射显示 page 级引擎(sensenova / paddleocr_vl16 / shizhengpt)仅实现 `run_page`。非 `runtime_checkable` 的 Protocol 要求两方法 → page 引擎不满足协议,类型检查与 `isinstance` 分派失效。 | 拆分为 `PageRunner` / `BookRunner` 两个 Protocol;或 `run_book` 标记可选(默认 `raise NotImplementedError`)。 |
| ARCH-2 | HIGH | types.py:182 vs DETAILED §6.1 / overview §6.2 | `PageInput` 字段名不一致:代码用 `image: np.ndarray`,设计(及编排伪代码 `page_input.img`)用 `img`。 | 将 types.py 字段改为 `img` 以对齐设计契约(当前无消费方,改动零风险)。 |
| ARCH-3 | MEDIUM | overview §3.1 / DETAILED §36 vs types.py | `EngineStatus = Literal["HEALTHY","DEGRADED","UNAVAILABLE"]` 在概览/详设被 `EngineRegistration.status` 引用,但 types.py 未定义该类型,亦无 `EngineRegistration` / `EngineStats`。 | types.py 需补齐设计已引用但未实现的类型(本 PR 至少补 `EngineStatus`)。 |
| ARCH-4 | MEDIUM | overview §6.3 vs DETAILED §5.2 | 概览 `Budget.exhausted` 恒返回 `False`(死属性,注释"由外部循环管理"但无设置点);DETAILED 已改为 `_exhausted` + `exhaust()` 并标注"修复 exhausted 恒为 False"。概览文档滞后。 | 同步概览 `Budget` 定义至 DETAILED 版本。 |
| ARCH-5 | INFO | 整体 | v0.7 大量模块(scheduler / registry / verifier / orchestrator)仅在设计中描述,尚未落地;types.py 仅有数据结构 + `EngineRunner` + `AdapterMeta`。 | 编码前先对齐 types.py 与详设,降低返工。 |

## 结论
架构分层(Registry → Scheduler → Verifier → Orchestrator)清晰,两级流水线(书级一次 + 页级降级)合理。主要风险是**设计契约与 types.py 漂移**,应在 Phase 1 前完成同步(本 PR 的 `ProbeResult.keys` 修复即其中一项)。
