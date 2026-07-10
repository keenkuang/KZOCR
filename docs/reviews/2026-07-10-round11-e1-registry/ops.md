# KZOCR v0.7 E1 实现评审 — 运维（ops）

- **评审角色**：运维
- **评审日期**：2026-07-10
- **评审对象**：`kzocr/scheduler/registry.py`（`1d52cae`）、`tests/test_registry.py`
- **范围声明**：E1 聚焦首版，仅注册中心 + 候选选择；mark_unavailable / benchmark / trace 属 E1.5/E2/E4 未实现，不计入本 E1 阻塞，但须排期补全。

---

## 1. 【阻塞 / 必须修复】

**无。** E1 为聚焦首版，无影响线上可运维性的硬伤；mark_unavailable / benchmark / trace 等缺口按约定不计入本 E1 阻塞，但须排期补全。

## 2. 【重要 / 强烈建议】（运维契约缺口，需排期）

1. **可观测性缺口** — `registry.py:143`(select_candidates)、`registry.py:102`(record)。依据 §6.5。现象：两关键路径零 logging，未来「某引擎总不被选中 / 冷启动评分趋同」无任何轨迹可查。建议 select_candidates 入口记结构化日志（tier、prefer、候选名+评分+落选原因），record 记每次调用（engine、success、latency、glyph），与 §6.5 trace 对齐，待 E4 统一 logger 接入。
2. **状态位缓存未落地** — `registry.py:44`(status 字段)、`registry.py:155`(list_by_tier)。依据 §4.1。现象：status 恒 HEALTHY 且 select_candidates 不过滤 UNAVAILABLE；设计要求的资源过滤（状态位由 mark_unavailable / 周期 probe 刷新）无入口，`status` 几乎未被使用。建议 E1.5 必须提供 mark_unavailable / 状态转换并在 select 中过滤，否则 §4.1 资源过滤成空文。
3. **last_seen 跨进程语义落空** — `registry.py:35,129`、§3.1。现象：注释称「支持跨进程持久化」，但无 save/load，last_seen 仅进程内有效，跨进程语义未落地，注释会误导运维。建议 E1.5 benchmark 持久化时一并加载/重建 last_seen，并修正注释措辞。

## 3. 【优化 / 可选】

1. **record KeyError 诊断** — `registry.py:114`。依据 §6.5 失败诊断。现象：消息仅 `f"未注册的引擎: {name}"`，运维定位「哪个可用」需另查。建议附当前已注册引擎列表，缩短排障路径。
2. **benchmark NDJSON 追加** — （未实现，§7.1）。提示：E2 实现时必须 O(1) 行级追加、复用 `engines/atomic.py`，禁止 JSON 全文覆写，避免运维 I/O 退化。
