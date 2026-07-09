# 可维护性与工程规范评审 · Round 4 — OCR 引擎统一架构方案（v0.2）

- **角色**：可维护性 / 工程化评审专家
- **评审日期**：2026-07-09
- **评审对象**：`docs/plans/ocr-engine-unification.md`（v0.2，已吸收 round3 八角色意见）
- **对照代码**：`kzocr/engine/run.py`、`kzocr/config.py`、`kzocr/engine/types.py`
- **评审任务**：核查 round3 可维护问题（High-1/2/3、Medium-1~6）在 v0.2 是否被**真闭合**，并发现 v0.2 新引入的可维护问题。
- **结论倾向**：**有条件通过（较 v0.1 显著改善，但 2 处"意图闭合"未在文档层面落到机制，且 1 处新引入映射责任未定义）**。

---

## 一、结论

v0.2 对 round3 可维护问题的**方向性闭合是成立的**：接口对齐（High-1/2）、共享逻辑下沉（High-3）、降级收口（Medium-3）、可观测性（Medium-4）、配置集中（Medium-6）六条，方案文字都已给出明确处置。尤其 §3「降级链收口 Router + `engine_path` 指标」和 §2.4「集中 schema + AdapterMeta 派生默认值 + 加载期校验 + 密钥不进 toml」是教科书式的正确治理，round3 提出的「散落降级」「配置碎片化」两面破口被堵住。

**但仍未"真闭合"的有两处，且需要文档层落地机制而非仅口头意图：**

1. **「registry 减负」依赖 run.py 退化为薄门面，v0.2 未显式承诺 retire run.py**——Medium-1 的"改 run.py + 改 router 两处"风险只在 `run_engine` 被明确改为 `EngineRouter` 薄门面时才消除，而 v0.2 §7 阶段1 只说"下沉共享逻辑"，未说"run.py 退休为 facade"。此条判**部分闭合**。
2. **KZOCR↔kimi 配置双轨未统一审计**——v0.2 的"集中 `Config.engines.<name>`"只解决了 KZOCR 内部碎片，但同一引擎（paddleocr/rapidocr…）在 KZOCR 侧与 kimi 侧 `_build_engine_config()` 仍有两套参数表示，出境端点审计仍要查两处。此条判**未闭合（新暴露）**。

**v0.2 新引入的可维护问题（round3 未及）：**
- **映射责任悬空**：`AdapterPageResult` → `types.py` 的 `PageResult/LineResult` 由谁转换，方案全程未定义（§7 列了"Markdown↔pages"但结构化 `char_confidences` 路径无归属），是下一段"逻辑僵尸"的种子。
- **10 份 `docs/engines/*.md` 一致性**：v0.2 加了 CI"6 必需标题 + 机抽字段由 AdapterMeta 生成"，比 round3 的纯强制清单进步，但"配置键与 AdapterMeta 一致"仍未机检，仍可能过期。

---

## 二、round3 问题闭合度（逐条）

### [High-1] kimi 内部适配器误当顶层引擎 → 跨仓库双维护
- **v0.2 处置**：§2.2 明确要求「`BookPipeline` 只做薄封装(shim)，不复制/不重写 kimi 内部 `*_adapter`，`AdapterMeta` 由 KZOCR 侧注入」；§7 阶段1 复述同样约束。
- **判定：✅ 闭合（意图）。** 接口漂移（kimi `recognize_page(page_img, prompt=None)->str` vs 方案 `recognize_page(img)->AdapterPageResult`）由 shim 适配层消化，且明确"不要求 kimi 改接口"，免去双仓库改接口成本。
- **残留**：shim 的具体落点（哪个文件、如何把 `str` 包成 `AdapterPageResult`）未在 §7 给出，属实现细节，不挡闭环，但建议在阶段1 清单补一行"kimi shim 适配层落点 `kzocr/engines/adapters/kimi_*.py`"。

### [High-2] 书级/页级契约混用
- **v0.2 处置**：§2.2 显式区分 `OCREngineAdapter`（页级）与 `BookLevelAdapter`（书级，输入 PDF 输出 `BookResult`），router 按 `kind` 调度。
- **判定：✅ 闭合。** 与 round3 建议一致，`_run_real` 的 `BookPipeline.process_book` 路径有归口。

### [High-3] `run.py` 共享逻辑无去处
- **v0.2 处置**：§7 阶段1 新建 `kzocr/engines/_common.py` 下沉「渲染/裁剪/后处理/跨页合并/Markdown↔pages」，由 `BaseAdapter` 默认复用，并标注"registry 能减负的前提"。
- **判定：✅ 闭合（意图）。** 对照现状 `run.py` 的 `_pdf_page_to_numpy`(241)/`_crop_to_body`(254)/`_vlm_postprocess`(325)/`_vlm_markdown_to_pages`(294)/`_merge_cross_page_breaks`(348)/`_markdown_to_pages`(159) 共 200+ 行，v0.2 已把这些 helper 的归宿指名到 `_common.py`，不重演"每适配器各抄一份"或"僵尸在 run.py"。
- **残留**：未说 `_common.py` 是否也要承接 `BookLevelAdapter` 路径的 markdown→pages（kimi 路径 `_run_real` 也调 `_markdown_to_pages`）。建议明确"书级适配器返回的 `BookResult.pages` 也统一经 `_common.py` 的 markdown↔pages 重建"，避免两套重建逻辑。

### [Medium-1] registry 能否消除对 `run.py` 的改动（"改 run.py + 改 router"两处）
- **v0.2 处置**：§7 阶段1「下沉共享逻辑 → BaseAdapter 默认复用（registry 能减负的前提）」。
- **判定：🟡 部分闭合。** 逻辑上：下沉后 `run_engine` 若退化为 `EngineRouter` 薄门面，则新增后端只需『加适配器模块 + 注册 + meta + 配置 + 文档』，**run.py 本身不再逐后端改动**——这正是 Medium-1 想要的"改 1 处"。
- **但 v0.2 未显式承诺 retire**：
  - v0.2 §7 阶段1 只讲"下沉 + kimi shim + 安全收敛 + 配置"，**没说 `run.py` 的 `run_engine` 三路分支被 EngineRouter 取代后 run.py 如何处置**；
  - 该承诺仅出现在 round3 `summary.md` 阶段6 测试交付物「保留 `run_engine` 为 `EngineRouter` 薄门面迁移现有 15 测试」，主方案未回写。
  - **风险**：若 run.py 与 router.py 长期并存且 run.py 仍含 `run_engine` 三路分支（mock/vlm/real），则"每加一个后端"仍可能要在 run.py 的 `_build_engine_config`/`_init_vlm_adapter` 里动——即 Medium-1 担心的"两处"并未消除。
- **建议（硬门槛补一项）**：阶段1 清单增「`run.py` 的 `run_engine` 改为仅调 `EngineRouter.run()` 的薄门面，原三路分支与 `_init_vlm_adapter` 硬编码降级删除；15 测试经 facade 迁移」。**只有这一句落到方案，Medium-1 才是真闭合。**

### [Medium-3] 降级链治理：放 Router 还是散各适配器
- **v0.2 处置**：§3「降级链收口到 Router：各适配器只管自身可重试故障（单页超时），不感知降级目标；Router 持 `prefer` 候选 + 探测，逐个尝试/捕获/降级，全失败 → HumanGate」。并写入 `engine_path: ["sensenova"(fail)→"paddleocr_vl16"(ok)]`。
- **判定：✅ 闭合（且优于 round3 期望）。** 直接消除现状 `_init_vlm_adapter`(run.py:194) 的硬编码 SenseNova→PaddleOCR-VL 降级。降级顺序单一可配（调 `prefer`），顺序不再散落 10 文件。

### [Medium-4] 可观测性
- **v0.2 处置**：§3「`BaseAdapter` 结构化日志前缀 `[engine=<name>]` + 指标 `latency/success/fail/chars/fallback_count`；Router 写 `engine_path` 到 `BookResult`」。
- **判定：✅ 闭合。** 比 round3 建议更完整（已含 `fallback_count`、`engine_path` 落 `BookResult`）。缺口仅一处：指标的**暴露形态**未定（进程内 dict / prometheus / OTEL），建议阶段2 明确，否则各适配器自起 client 又会碎。

### [Medium-5] 文档一致性靠"强制清单"无机制
- **v0.2 处置**：§2.4「强制含 6 项标题（含运营主体属地/是否跨境/数据出境说明），CI 校验'注册即文档齐备'」；§7 阶段6「可机抽字段由 `AdapterMeta` 自动生成（`kzocr engine describe <name>`），纯主观项靠 CI 校验，杜绝强制清单式过期文档」。
- **判定：🟡 部分闭合（改善但未根治）。** 比 round3 纯"6 必需标题"进步两点：① 机抽字段由 `AdapterMeta` 生成，避免文档与代码行号/字段漂移（直击 round2 已实锤的"设计文档与代码不同步"）；② CI 缺失即失败强制齐备。
- **未根治**："6 必需标题"仍是清单式，且**标题项下内容是否与 `AdapterMeta`/`Config.engines.<name>` 实际字段一致未机检**（如文档写的 endpoint 与实际 allowlist 不符）。建议把"文档中的端点/是否跨境/资源"也由 CI 对 `AdapterMeta` 反查，使文档成 `AdapterMeta` 的渲染而非独立手写源。

### [Medium-6] 配置存放：集中 vs 每适配器 toml
- **v0.2 处置**：§2.4/§8 假设5 采纳「集中 schema + `Config.engines.<name>` 命名空间 + `AdapterMeta` 派生默认值 + 加载期 schema 校验 + 默认值合并；每适配器 toml 仅可选覆盖层；密钥绝不进 toml」。
- **判定：✅ 闭合（KZOCR 内部）。** 与 round3 立场完全一致，且加了"加载期 schema 校验 + 默认值合并"，把 round3 担忧的"分散 loader 校验弱/默认值散落"一并解决。
- **未闭合（新暴露，见第三.2）**：该集中只覆盖 **KZOCR 侧**。kimi `BookPipeline` 仍由 `run.py:_build_engine_config()` 用**另一套 schema**（`engine_configs:{paddleocr,rapidocr,…}`）喂入。同一引擎在 KZOCR `Config.engines.paddleocr` 与 kimi `engine_configs.paddleocr` 是两套参数表示，出境端点审计要查两处——round3 提的"KZOCR↔kimi 双轨"在 v0.2 没被消除，只是从"KZOCR 内 10 份碎片"收敛为"KZOCR 集中 + kimi 另一份"。

### [Medium-7] 测试覆盖不足
- **v0.2 处置**：§7 阶段6 测试交付物 `test_router`/`test_glyph_verifier`/`test_adapters_protocol` + `tests/engines/fakes.py` + `kzocr smoke --adapter fake` + registry 启动自检（`runtime_checkable Protocol`）。
- **判定：✅ 闭合（意图）。** 契约测试前置、fake 适配器、无依赖 smoke 全列齐。注意需与 Medium-1 的 run.py 退休协同：facade 迁移 15 测试应随阶段1 而非阶段6。

### [Low-1] `engine_label` 硬编码延续
- **v0.2 处置**：§2.1 `AdapterMeta.label` 承载对外 `engine_label`，`meta.name` 替代硬编码。
- **判定：✅ 闭合。** 但现状 `run.py:222,236` 仍对 kimi 适配器 `adapter.engine_label = "..."` 赋值——shim 层要把 `AdapterMeta.label` 注入，否则 v0.2 的"label 由 KZOCR 注入"在 kimi shim 路径会漏。

### [Low-2] `recognize_pages` 上下文语义
- **v0.2 处置**：§2.1 以 `supports_context` + 独立 `recognize_with_context(pages, ctx)` 区分；§3 单引擎模式以 `UNKNOWN`/低置信补触发。
- **判定：✅ 闭合（意图）。** 比 round3 仅 `supports_context` 标志更明确——把"双页上下文"从 `recognize_pages` 入参约定提升为独立方法，避免 `BaseAdapter` 默认循环丢失上下文。

---

## 三、v0.2 新引入的可维护问题

### [新-1] `AdapterPageResult` → `types.py` 映射责任悬空（高优先）
- **现象**：v0.2 在 §2.1 引入适配器返回 `AdapterPageResult(text, confidence, char_confidences, crop_img, meta)`，层间契约是 `types.py` 的 `PageResult/ParagraphResult/LineResult`。但**全方案没有任何一处说明"谁把 `AdapterPageResult` 转成 `PageResult/LineResult`"**。
- **对比现状**：当前 `run.py` 里这个转换散在 `_vlm_markdown_to_pages`(294) / `_markdown_to_pages`(159)，且只处理"整页 Markdown→逐行"，**没有字级 `char_confidences` 的归位**（现状 `LineResult.char_level_json` 是 str，适配器却要返回 `list[float]`，类型都对不上）。
- **风险**：若每个适配器各自把 `AdapterPageResult` 拼成 `PageResult`，又是"每适配器各抄一份转换"——重演 High-3 删掉的那类重复；若放在 router，router 又掺入引擎无关的转换逻辑，膨胀为新的单体。
- **建议**：在 §7 阶段1 的 `_common.py` 职责里**显式加一条**「`AdapterPageResult → LineResult/PageResult` 统一转换器（`_common.page_result_from_adapter`）」，结构化 `char_confidences` 落 `LineResult.char_level_json`（需定序列化约定），`crop_img` 落 `LineResult` 原图回溯字段。这是 §7 写漏的一环，不补则"结构化返回"的架构收益落不了地。

### [新-2] KZOCR↔kimi 配置双轨未统一审计（中优先，Medium-6 未闭合的延伸）
- **现象**：v0.2 的集中 `Config.engines.<name>` 是 KZOCR 侧真相源；但 kimi `BookPipeline` 经 `run.py:_build_engine_config()` 用 `engine_configs:{paddleocr,rapidocr,unirec,paddleocr_vl16,shizhengpt,mineru,tesseract,cloud_llm}` 另一套 schema 喂入。两套对同一物理引擎（如 paddleocr_vl16 的 host/port/auto_start）的参数格式与默认值不一致。
- **影响**：① 出境端点（sensenova_base_url / deepseek_base_url / cloud_llm.base_url）在 KZOCR 侧一处可审计，但在 kimi `engine_configs.cloud_llm.base_url` 又一份，且 kimi 那份**不经 KZOCR 的 allowlist 校验**（§2.4 的端点校验只说"所有出站 base_url/vlm_host"，但 kimi 路径是否在 KZOCR 里统一拦截未明）；② 改一个超时/端口要在两处同步，必然漂移。
- **建议**：要么 **KZOCR 侧 `Config.engines.<name>` 作为唯一真相源，kimi 的 `engine_configs` 由 KZOCR 在调用 `BookPipeline` 前从同一 `Config` 翻译生成**（单方向翻译，不双写），要么在方案里显式声明"kimi 内部引擎配置不在本方案审计范围，由 kimi 仓各自负责 + 其 allowlist 复用 KZOCR 的 `_validate_url`"。二选一必须写清，否则双轨永远对不齐。

### [新-3] `BaseAdapter` 可观测性指标暴露形态未定（低优先）
- 见 Medium-4 残留：指标 `latency/success/fail/chars/fallback_count` 落点（进程内 dict / prometheus / OTEL）未定。若交给各适配器自选 client，会重新碎片化。建议阶段2 明确统一出口（如 `kzocr/engines/metrics.py` 单例）。

### [新-4] 10 份 `docs/engines/*.md` 与 `AdapterMeta` 一致性仍靠人工（低优先）
- 见 Medium-5 残留：机抽字段（部署依赖/端点/是否跨境/资源）可由 `AdapterMeta` 自动生成，但"配置键名、超时/端口取值"与 `Config.engines.<name>` 实际字段是否一致未机检。建议 CI 增加"文档中出现的端点/host 必须命中 `AdapterMeta` 或 allowlist"的反查断言。

### [新-5] `_common.py` 与 `BookLevelAdapter` 路径的重建逻辑可能重复（低优先）
- 见 High-3 残留：kimi 书级适配器返回 `BookResult.pages` 时仍会用 markdown→pages 重建（现状 `_run_real` 调 `_markdown_to_pages`）。若 `_common.py` 的 markdown↔pages 只为页级适配器服务，书级路径又会自带一份。建议 `_common.py` 的 markdown↔pages 同时服务两类适配器。

---

## 四、改进建议（落地优先级）

1. **（硬门槛，补 Medium-1）** 阶段1 清单增：「`run.py.run_engine` 改为仅调 `EngineRouter.run()` 的薄门面；删除原 mock/vlm/real 三路分支与 `_init_vlm_adapter` 硬编码降级；现有 15 测试经 facade 迁移」。否则"registry 消除对 run.py 改动"仍可能变成两处改动。
2. **（硬门槛，补 新-1）** 阶段1 `_common.py` 职责显式加「`AdapterPageResult → PageResult/LineResult` 统一转换器」，定义 `char_confidences→char_level_json` 的序列化约定与 `crop_img` 落点。否则结构化返回落不了地。
3. **（补 新-2）** 写明 KZOCR↔kimi 配置的唯一真相源与翻译方向（KZOCR `Config.engines.<name>` 单向翻译给 kimi `engine_configs`，或显式划清审计边界并复用 `_validate_url`）。统一出境端点审计面。
4. **（补 Medium-5 / 新-4）** CI 文档校验从"6 必需标题存在"升级为"标题存在 + 关键字段（端点/跨境/资源）反查 `AdapterMeta`/allowlist 一致"；机抽字段经 `kzocr engine describe` 渲染，文档成为 `AdapterMeta` 的视图而非独立源。
5. **（补 Medium-4 / 新-3）** 阶段2 明确指标统一出口（`kzocr/engines/metrics.py` 单例），禁止各适配器自选监控 client。
6. **（补 High-1 / Low-1）** 阶段1 写明 kimi shim 落点（`kzocr/engines/adapters/kimi_*.py`），并由 shim 把 `AdapterMeta.label` 注入 kimi 适配器，消除 `run.py:222,236` 的 `engine_label=` 硬编码赋值。
7. **（补 High-3 / 新-5）** `_common.py` 的 markdown↔pages 同时服务页级与书级适配器，避免书级路径自带重建。

---

## 五、对假设 5 裁决的再确认

**round3 裁决**：调整（集中 schema + `Config.engines.<name>` 命名空间 + 加载期 schema 校验/默认值合并；每适配器 toml 仅可选覆盖层；密钥绝不进 toml）。

**round4 再确认**：**维持原裁决，方向正确，但范围需收窄表述。**
- ✅ v0.2 把"集中 schema + AdapterMeta 派生默认值 + 加载期校验 + 默认值合并 + 密钥不进 toml"全部落到 §2.4/§8，比 round3 裁决更完整，**确认采纳**。
- ⚠️ 但原裁决隐含的"统一审计 KZOCR↔kimi 双轨"在 v0.2 只解决了 KZOCR 半边（见 新-2）。**建议把假设 5 的裁决要点补一句**：「集中 `Config.engines.<name>` 为 KZOCR 侧唯一真相源；kimi `BookPipeline` 的配置由 KZOCR 从同一 `Config` 单向翻译注入，不在 KZOCR 仓内另起一份 schema」。否则假设 5 的"可集中审计"目标对出境端点只达成一半。
- 密钥不进 toml：v0.2 与现状 `config.py`（密钥全走 env）一致，✅ 确认，且已消除 round2 明文密钥事件面。

---

## 附：闭合度速查表

| round3 问题 | 判定 | 一句话 |
|---|---|---|
| High-1 kimi 双维护 | ✅ 闭合 | §2.2 shim + AdapterMeta 注入，不复制 |
| High-2 书/页级混用 | ✅ 闭合 | §2.2 显式 `BookLevelAdapter` |
| High-3 共享逻辑无去处 | ✅ 闭合(意图) | §7 `_common.py` 指名归宿 |
| Medium-1 registry 减负 | 🟡 部分 | 需显式 retire run.py 为 facade 才真消除"两处" |
| Medium-3 降级收口 | ✅ 闭合 | §3 Router 收口 + engine_path |
| Medium-4 可观测性 | ✅ 闭合 | §3 BaseAdapter + engine_path（指标出口待定） |
| Medium-5 文档清单 | 🟡 部分 | 加机抽字段 + CI，但内容一致性未机检 |
| Medium-6 配置集中 | ✅ KZOCR内闭合 / 🟡 双轨未合 | 见新-2 |
| Medium-7 测试 | ✅ 闭合(意图) | §7 测试交付物齐 |
| Low-1 engine_label | ✅ 闭合 | shim 注入需注意漏点 |
| Low-2 上下文语义 | ✅ 闭合 | 独立 `recognize_with_context` |
| **新-1 映射责任** | 🔴 新引入 | `AdapterPageResult→types` 转换无人认领 |
| **新-2 双轨审计** | 🔴 新引入 | KZOCR↔kimi 配置仍两源 |
