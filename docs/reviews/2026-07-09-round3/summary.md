# KZOCR 统一 OCR 引擎架构方案 — Round 3 多角色评审汇总

> 评审对象：`docs/plans/ocr-engine-unification.md`（草案 v0.1）
> 评审日期：2026-07-09 ｜ 角色：架构 / 安全隐私 / 性能 / 中医领域 / 可维护性 / 数据完整性 / 校对 UX / 测试
> 总评：**有条件通过** —— 方向（统一适配器 / 可切换路由 / 字形校验门 / 人工兜底 / 结构化归档）获一致认可，但存在多处会"重演 `run.py` 单体腐化"或"与已落地 H1–H8 整改反向"的硬伤，须先闭合 High 缺口再进实现。

---

## 一、跨角色 High 级问题（必须修后实现）

### A. 架构契约与职责（架构 / 可维护）
- **A1 未复用既有归一化中间表示**：仓库已有 `kzocr/engine/types.py`（`BookResult`/`LineResult` 已含 `engine_texts` / `consensus` / `confidence` / `glyph_verified` / `is_mock`）。方案却把适配器返回值弱化为 `str`，会丢失逐行多源与置信度 → **适配器应返回 `PageResult`，以 `types.py` 为层间唯一契约**。
- **A2 共识职责重叠**：EngineRouter 与 GlyphVerifier 都声称做跨引擎比对 → 单点归属：Router 只管选引擎+降级，consensus/跨引擎比对收口到 GlyphVerifier。
- **A3 共享逻辑未下沉**：`registry` 本身不能消除对 `run.py` 的改动。须先把 `run.py` 200+ 行共享逻辑（渲染/版心裁剪/后处理/跨页合并）下沉到 `kzocr/engines/_common.py`，registry 才算真正接管；新增后端理想只改 1 处。
- **A4 降级链治理**：现有 `_init_vlm_adapter` 的 SenseNova→PaddleOCR-VL 硬编码降级是反面教材；降级编排收口到 EngineRouter，各适配器只管自身可重试故障。

### B. 安全与合规（安全）
- **B1 新增暴露面（最危险）**：云端适配器 `base_url` / `vlm_host` 完全无端点校验，`allow_cloud_vision` 只控"是否发"不控"发往谁" → 新 SSRF/外泄入口；`0.0.0.0` 误绑也未被拦截。须把 khub `_validate_url` 延伸至**所有**出站端点并加**域名 allowlist**。
- **B2 数据最小化伪命题**：版心裁剪只去白边，正文敏感文本仍整页 base64 外传，SenseNova 双页上下文还额外多送下一页顶部 15%。需**逐书/逐页出境同意 + 审计日志**；consensus 会让一页图像同时发往多家第三方，合规不可证明。
- **B3 假数据不得冒充已校验**：灰度回退与 `use_mock` 必须置 `is_mock` + ERROR + **阻断 publish**（与 H8 一致）；桩数据**不得**标 `glyphVerified=PASS`。

### C. 性能与资源（性能）
- **C1 资源配置冲突**：llama-server 实际监听 `:8080`，但配置与适配器默认都指向 `:18080` → 重复起服务抢资源（须统一为 `:8080`）。
- **C2 VLM 缺预算/熔断**：当前纯串行、单请求、无总超时（单页 600s、单本无上限）。须补**单页 120s + 单本 2h + 并发≤1 + 熔断**；`KZOCR_MAX_PAGES=2000` 只是内存闸，CPU 下≈16–66h 失控。
- **C3 无 GPU 下 consensus 硬约束**：两个本地 CPU 引擎并行纯内耗 → consensus **仅允许含云端引擎且 N≤2**；默认 single。
- **C4 字形校验与渲染开销**：字形 KB 须**启动时一次性载入进程内集合**，禁逐字符查 `term_kb`/RuntimeDB；两页上下文对下一页完整重复渲染、`list(doc)` 全物化 → 流式 + 渲染缓存。

### D. 领域贴合（中医领域）
- **D1 UNKNOWN 海淹**：罕见中医字（萆薢、䗪虫、蘡薁等）会被海量误判 UNKNOWN 淹掉校对台；缺**繁简/异体归一化**（繁体影印本整体失效）。
- **D2 方剂失真**：方剂库 schema 只落"方名+组成"，用法/功用/主治/方解等七类核心字段未结构化；"khub 方剂系统 / term_kb 闭环"在代码里**并不存在** → 回流机制悬空。
- **D3 最小单元不普适**：针灸书最小单元是"穴"、本草书是"药"，TOC 三级不普适。
- **D4 安全用药**：建议加中医专用**形似混淆集**（未/末、白木/白术）与**毒性用量红线**告警。

### E. 数据完整与校对闭环（数据完整性 / 校对 UX）
- **E1 孤儿数据**：全文 `final_markdown` 在现有 `Book` 表**无列**、适配器不写；最小小节→Line 归属映射未定义 → 归档产生孤儿行。须补 `Book.final_markdown` 列 + 带 `bookCode` 外键的 `SectionLine`/`TocNode` 表。
- **E2 跨库一致性悬空**：zai 是 SQLite、khub 是 HTTP，无事务 → 用 **Outbox + 最终一致**模型兜底（假设 3）。
- **E3 幂等抹掉人工成果**：现有 `DELETE WHERE bookCode` 整书清空会连人工 `Proofread`/`humanFinal` 一起抹掉 → 归档重跑即丢失校对。
- **E4 HumanGate 漏放**：第 4.2 定义了 `UNKNOWN` 却未进第 5 章触发列表 → 新药材名被自动放行进库（严重漏放）。
- **E5 校对台信息不足**：拿不到原图裁剪，形似字人工无法定夺；mock 未标 `is_mock`；无优先级分级、跨页同错字无聚合、闭环回填仅口头承诺；`export_zai.py` 缺 Term/Formula、行序可能错乱。

---

## 二、对方案第 8 章 6 项假设的收口结论

| # | 假设 | 多角色立场（收口） |
|---|---|---|
| 1 | 字形校验暂不加视觉再识别 | **修订**：领域+UX 反对维持"不加"。兜底 FAIL/UNKNOWN 行**必须回看原图裁剪**（加可选 `VisionRecheckAdapter`）。主体仍认可"字典+置信度+共识"为主。 |
| 2 | 最小小节 = TOC 三级 | **修订**：按 `book_type` 分流——方剂书=方（`^\d+\.\d+`）、针灸书=穴、本草书=药。非固定三级。 |
| 3 | 方剂库归属 | **收口**：zai `Formula` 表为权威缓存，khub 为**可选单向 Outbox 同步**（非双写）；khub 方剂系统当前不存在，不可假定。 |
| 4 | consensus 成本 | **收口**：无 GPU 下 consensus **硬约束**（仅含云端且 N≤2）；默认 single。 |
| 5 | 配置存放 | **修订**：反对纯每适配器 `*.toml`；主张"集中 schema + `Config.engines.<name>` 命名空间 + 加载期校验/默认值合并"，10 份文档靠 CI「缺失即失败」+ `AdapterMeta` 自动生成兜底。 |
| 6 | 字形知识库来源 | **收口**：KZOCR **自带精简字形白名单为事实源**（解耦 kimi `term_kb`），并支持写回（闭环）。 |

---

## 三、修订动作清单（落到阶段路线）

- **阶段 1 适配器注册表**：复用 `types.py` 为契约，适配器返回 `PageResult`；共享逻辑下沉 `_common.py`；降级编排收口 EngineRouter；出站端点统一 allowlist 校验；统一指标（latency/success/fail/fallback）。
- **阶段 2 路由层**：consensus 无 GPU 硬约束；VLM 资源预算（端口修正 `:8080`、单页/单本超时、并发≤1、熔断）。
- **阶段 3 字形校验**：KB 进程内预载；繁简/异体归一化；罕见中医字白名单防 UNKNOWN 海淹；可选 `VisionRecheckAdapter`。
- **阶段 4 人工兜底**：HumanGate 触发补齐 `UNKNOWN`；原图裁剪透传校对台；`is_mock` 强制透传；优先级+跨页聚合+回填闭环。
- **阶段 5 归档层**：`Book` 补 `final_markdown` 列；最小小节→Line 外键映射；按 `book_type` 切分；方剂七类字段结构化；Outbox 跨库同步；幂等保留人工校对。
- **阶段 6 说明文档**：补足 `docs/engines/*.md`（CI 缺失即失败）。

> 下一步：依据本汇总修订 `ocr-engine-unification.md`（v0.2），提交并推送，再进入阶段 1 实施。
