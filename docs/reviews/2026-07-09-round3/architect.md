# KZOCR 统一 OCR 引擎架构方案 —— 架构师评审（round3）

- **评审角色**：首席架构师
- **评审日期**：2026-07-09
- **评审对象**：`docs/plans/ocr-engine-unification.md`（草案 v0.1）
- **参考**：`kzocr/engine/run.py`、`kzocr/config.py`、`kzocr/adapter/to_zai_prisma.py`、`kzocr/engine/types.py`、`docs/reviews/2026-07-09-round2/summary.md`

---

## 结论

**有条件通过（Conditional Pass）。**

分层方向与"解耦接入/选择"的总体原则是正确且必要的，协议面（仅 `recognize_page`/`recognize_pages` 两个方法）也恰到好处地避免了过度设计。但草案在 **层间契约、职责切分、阶段依赖、mock 回退一致性** 四个方面存在 High 级缺口，且完全忽略了仓库内已存在的归一化中间表示 `kzocr/engine/types.py`。这些缺口必须在进入实现（阶段 1）之前闭合，否则会把 `run.py` 的单点硬编码腐化，替换为"Router 大杂烩 + str 字符串胶水"的新型腐化。

---

## 关键问题（带严重度）

### [High] K1 — 层间数据契约缺失，适配器返回值过弱（`str`）
草案 §2.1 的 `OCREngineAdapter.recognize_page(img) -> str` 只返回一段文本，但下游 `GlyphVerifier`（§4）与共识比对（§3 策略 B）需要：
- 逐行 `engine_texts`（多源）、逐字/逐行 `confidence`、可选 `char_level_json`、段落/标题结构。
这些恰恰是 `kzocr/engine/types.py` 中 **已存在** 的 `LineResult`（`engine_texts/consensus/confidence/glyph_verified/engine_results/char_level_json`）与 `BookResult`（`is_mock`）所承载的内容。草案未引用该模块，等于把已有 NIR 弃之不用，又用 `str` 重新引入信息丢失。

后果：str→`LineResult`/`BookResult` 的转换（以及跨页合并、Markdown 分行）将被迫塞进 `EngineRouter`，使其膨胀为新的单体（重演 `run.py` 的 `_run_vlm` 600 行问题）。

### [High] K2 — 共识（consensus）职责在 `EngineRouter` 与 `GlyphVerifier` 之间重叠
- §3 策略 B：EngineRouter "同时跑 N 个适配器，逐行交叉比对（`engine_texts` 多源），不一致的行降为 UNCERTAIN"。
- §4.2 第 4 条：GlyphVerifier "多引擎共识下，多数引擎一致且通过字形校验 → 提升为 PASS；分歧 → UNCERTAIN"。

谁来计算"跨引擎逐行一致/多数票"？两处都声称拥有该逻辑，无单一归属。若不澄清，会出现双份比对代码与状态机冲突（PASS/UNCERTAIN 由谁裁定）。

### [High] K3 — 阶段 5（Archiver/TOC）依赖未落地的 TOC 分析器
§6 称 TOC 抽取"复用 H5 `toc_analyzer` 设计"，但 round2 **H5** 已明确：TOC 驱动分节管线"在代码中完全未落地"，且被列为"后续路线图，需单独立项"。Phase 5 因此建立在不存在的实现之上，会直接卡住。草案未把"先实现最小 TOC 分析器"列为前置依赖。

### [High] K4 — §9 的 mock 回退静默发布假数据，与 round2 H8 整改方向相反
§9 写道："任何阶段出问题，可整体回退到 `use_mock` 桩数据跑通全链路（已有），保证系统永远可演示、不阻塞。"但 round2 **H8** 的已共识修复是：降级必须升级为 **ERROR 级醒目提示并标注 `is_mock`**，不再"假装成功"。（注：`BookResult.is_mock` 字段已实现，但 `run.py` 的失败回退路径 `build_mock_book(...)` 未设 `is_mock=True` 也未 ERROR 日志，正是 H8 现状。）草案 §9 把"回退到 mock 跑通"当作卖点，未要求置 `is_mock`、未要求 ERROR 日志、未要求阻断"publish 假古籍"，等于回退 H8。

### [Medium] K5 — 协议契约自相矛盾：`meta.supports_context` 被引用但未定义
§2.1 注释写"由 meta.supports_context 声明"，但给出的 `AdapterMeta` dataclass（§2.1 代码块）**没有该字段**。同时 `recognize_pages` 的语义被两用：既用于"批处理多页"（共识模式），又用于"当前页+下页顶部作上下文"（SenseNova 思考模式，`_run_vlm:459`）。两种语义未区分。

### [Medium] K6 — kimi/`BookPipeline` 适配器的返回形状与协议不匹配，且"接入方式"未定
现有 `_run_real` 调用 kimi `BookPipeline.process_book` 返回**整本书 Markdown**（`final_markdown`），其 TOC/后处理都在 kimi 侧完成；而协议要求每页返回 `str`。如何把 BookPipeline 包成"每页 `str` 适配器"未说明。更大的决策缺口：已存在的适配器（PaddleOCR-VL-1.6、SenseNova、ModelScope）当前位于 **kimi 仓库** `tcm_ocr.core.engines.*`（`run.py:105,214,229`），草案 §7 阶段 1 说"先接已存在的"——是 **import 包装**（沿用 `sys.path.insert` 方式）、**vendor 复制** 还是 **重写**？三种策略工作量与耦合度天差地别，未决策。

### [Medium] K7 — `HumanGate` 人工校正的回写路径未定义
§5 图与 §5 文字只描述"推送 zai 校对台"，但 HumanGate→Archiver 的汇合点需要"人工校完"的数据。现有 `to_zai_prisma.py` 只负责**写**（`Line.humanFinal` 由人工在 zai 填），KZOCR 侧**读回** `humanFinal` 的机制（zai→KZOCR 回流）完全未描述。没有它，Archiver 拿不到人工终稿。

### [Medium] K8 — 跨引擎逐行对齐（共识可行性）被低估
共识"逐行交叉比对"要求不同引擎产出**行对齐**的文本。但 local-nonvision（PaddleOCR）输出裸文本、local/cloud-vision（VLM）输出 Markdown（含标题/空行），二者换行点不同。草案未定义行对齐/键对齐步骤，直接"逐行比对"在异构引擎间不可行。同时 §8 假设 4 已承认 CPU 成本，但未给出默认 single 的明确结论。

### [Medium] K9 — 配置迁移路径缺失
`config.py` 已有散落的逐引擎字段（`vlm_host/port`、`sensenova_*`、`deepseek_*`、`allow_cloud_vision`），且现有适配器（如 `_init_vlm_adapter`）直接读 `cfg.sensenova_api_key` 等。草案 §8 假设 5 倾向"每适配器独立 `*.toml`"，但未说明：如何在不破坏现有环境变量与现有适配器读取方式的前提下过渡？没有给出 `config.py` 字段→`*.toml` 的兼容/迁移方案。

### [Medium] K10 — TOC/Section 表是跨仓库 schema 变更，协调缺失
§6 承认"需新增 TOC/Section 表"。但 zai 的 schema 真源在 `tcm_ocr_zai/prisma/schema.prisma`，`to_zai_prisma.py` 只是按子集**自动建表**（`CREATE TABLE IF NOT EXISTS`）。新增表必须同时落到 zai 的 prisma schema 与 KZOCR 的 `_SCHEMA_DDL`，否则 zai 工作台无法展示、且 KZOCR 自动建表子集与 zai 真源会分叉。草案未提跨仓库协调。

### [Medium] K11 — 方剂抽取逻辑归属未定
§6 说方剂书"抽取方剂名/组成/剂量，写入 Formula/FormulaIngredient"。现有 `to_zai_prisma.py` 只**写** `Formula/FormulaIngredient`，抽取逻辑在 kimi `BookPipeline` 内。对新 Archiver（尤其 VLM 路径、未来无 kimi 的路径），方剂抽取需复用还是重写？未说明。

### [Low] K12 — `AdapterMeta.kind` 用 `str` 联合类型，建议 `Enum`/`Literal` 提升类型安全。
### [Low] K13 — 预处理模块（fitz 渲染、`_crop_to_body` 版心裁剪）当前嵌在 `_run_vlm`（`run.py:241-291`），草案"层 1"未明确其归属文件与对外接口。
### [Low] K14 — 各阶段无测试交付物，与 round2 **H7**（CLI/Router 零单测）未衔接；新 Router/Registry/Verifier 必须带单测。
### [Low] K15 — 草案未给适配器定义统一的超时/重试/可观测性契约（round2 ops 多条 Medium：缺整体超时、`database is locked` 等）。

### 附带发现（非草案问题，但影响评审）
- `to_zai_prisma.py:153` 当前把 `auditSource` 写成 `book.engine_label`（引擎名），而草案 §4.3 想让 `auditSource` = `dictionary/consensus/human`。现有写入语义与草案意图不一致，落地时需改。
- `LineResult`/`BookResult` 已具备方案所需全部字段，草案重述字段（§4.3 "复用现有 schema"）时却未引用 `types.py`，属信息割裂。

---

## 改进建议（具体）

1. **确立 `kzocr/engine/types.py` 为层间唯一契约（NIR）。** 草案应在 §1/§2 显式声明：每层之间传递的就是 `BookResult`/`PageResult`/`ParagraphResult`/`LineResult`。不要另起一套"候选文本"口头契约。

2. **把适配器返回值从 `str` 改为结构化。** 协议改为：
   ```python
   def recognize_page(self, img: np.ndarray) -> PageResult: ...
   def recognize_pages(self, imgs: list[np.ndarray]) -> list[PageResult]: ...
   def recognize_with_context(self, pages: list[np.ndarray]) -> str: ...  # 仅当 supports_context
   ```
   `PageResult` 复用现有 `types.py`（`paragraphs[].lines[].engine_texts[自引擎名]=...`、填 `confidence`/`char_level_json`）。这样 EngineRouter 只需**装配** `engine_texts`，无需做 str→结构转换。

3. **切分共识职责（解决 K2）。** EngineRouter 只负责"候选装配"：运行 N 个适配器，把各自结果按行键（见建议 8）填入同一 `LineResult.engine_texts` + `engine_results`。GlyphVerifier 只负责"质量裁定"：读 `engine_texts` 做字典/置信度/多数投票，产出 `glyph_verified` + `auditSource`，**不重复做跨引擎比对**。删除 §4.2 第 4 条里"多引擎共识"的计算职责，改为"消费 EngineRouter 已填好的 `engine_texts`"。

4. **闭环 mock 回退（解决 K4，对齐 H8）。** mock 作为 `MockAdapter`（最低优先级、显式注册）。任何失败降级时：`BookResult.is_mock = True`、记 **ERROR** 日志、且 `Archiver`/`publish` 在 `is_mock=True` 时**拒绝入库/显式告警**，绝不静默成功。§9 改写为此语义。

5. **修复协议契约（解决 K5/K12）。** `AdapterMeta` 增加 `supports_context: bool = False`、`supports_confidence: bool = False`；`kind` 用 `Literal["local-nonvision","local-vision","cloud-vision"]`。明确定义 `recognize_pages` = "多张独立页→多结果"，上下文模式用独立的 `recognize_with_context`。

6. **先补最小 TOC 分析器，再进 Phase 5（解决 K3）。** 把"从 `final_markdown` 按 Markdown 标题/`headingLevel` 抽取章节树"作为 Phase 5 的**前置子任务**（可仅 regex 标题级），避免 Phase 5 卡在 round2 H5 的未实现项上。或把 Phase 5 拆为 5a（全文+按标题块最小小节，可立即做）与 5b（完整 TOC 树，依赖 H5 立项）。

7. **明确 HumanGate 回写（解决 K7）。** 新增 `pull_corrections(book_code) -> BookResult`：从 zai `Line.humanFinal` 读回填入 `BookResult`，作为 Archiver 的输入来源之一。把"zai→KZOCR 回流"写成独立小节或 Archiver 引导步骤。

8. **定义跨引擎行对齐键（解决 K8）。** 要求适配器输出按"段落/标题块"对齐（`ParagraphResult.node_type/heading_level` 已支持），EngineRouter 在多数投票前做基于块的键对齐，而非裸逐行。默认 `mode = "single"`，consensus 仅 opt-in（呼应假设 4）。

9. **配置兼容迁移（解决 K9）。** 保留 `config.py` 作为环境变量入口（不破坏现有 `SENSENOVA_*`/`KZOCR_VLM_*`）；每个适配器提供 `from_config(cfg)` 向后兼容读取现有字段，并**可选**叠加 `<name>.toml` 覆盖。不要一次性强制迁到 toml。

10. **跨仓库 schema 协调（解决 K10）。** TOC/Section 表同时加到 zai `prisma/schema.prisma` 与 `to_zai_prisma._SCHEMA_DDL`，由单一迁移 PR 协调，KZOCR 自动建表子集须与 zai 真源逐列对齐。

11. **方剂抽取归属（解决 K11）。** 明确：Phase 5 先复用 kimi `BookPipeline` 的方剂抽取（通过 kimi 适配器返回 `BookResult.formulas`）；VLM-only / 无 kimi 路径的方剂抽取列为后续独立任务，不在本方案强制。

12. **每阶段带测试（解决 K14/H7）。** Phase 1–5 各增交付：`tests/test_registry.py`（注册/降级）、`tests/test_router.py`（探测+选择+single/consensus）、`tests/test_verifier.py`（PASS/FAIL/UNCERTAIN 状态机）。

13. **适配器统一横切契约（解决 K15）。** 在 `base.py` 约定：每适配器 `timeout`、失败抛 `AdapterError`、Router 捕获后降级下一候选（已有 §9 思路，需落到 `base.py`）。

---

## 对方案第 8 章 6 项假设的立场

1. **字形校验机制（暂不加独立再识别视觉模型）** —— **同意默认**，但要求把"可选 `VisionRecheckAdapter` 对 FAIL/UNKNOWN 行回看原图"做成**可插拔钩子**（协议预留 `recheck(line, crop_img)`），不要等以后推倒重来。

2. **最小小节定义（TOC 三级 vs 更小）** —— **不主张钉死为三级标题**。建议最小小节 = "任意级别标题或方剂块所界定的 Markdown 块"，并用 `heading_level` 记录层级；粒度做成**可配置**，默认"任一标题块"。避免后期改 schema。

3. **方剂库归属（zai Formula 表 vs 独立 khub）** —— **zai 为权威、khub 为可选同步**。KZOCR 先写 zai `Formula/FormulaIngredient`；仅当 khub 可达且显式开启时才同步（沿用 round2 H3/H6 教训：不静默跨库写、异常要优雅）。禁止把 khub 同步当作 Phase 5 阻塞项。

4. **consensus 模式成本** —— **同意默认 single，consensus 仅 opt-in**（§3 策略 B 作为可选）。并补一条：consensus 必须配合建议 8 的行对齐与每适配器 `timeout`，否则 CPU/时延不可控。

5. **适配器配置存放（集中 vs 每适配器 toml）** —— **混合，不纯 toml**。保留 `config.py` 环境变量为主（兼容现有部署），每适配器 `*.toml` 作为可选覆盖层。避免核心配置膨胀的同时不破坏现有 env 契约。

6. **字形知识库来源（复用 kimi term_kb vs KZOCR 内置白名单）** —— **KZOCR 内置精简白名单为主，kimi term_kb 为可选增强**。KZOCR 应自带 Unicode CJK 合法字形集 + 高频中医用字白名单（bundled，不依赖 kimi 仓库），若 `KZOCR_TERM_KB_PATH` 提供则额外加载 kimi `term_kb`。解耦引擎仓库，避免 Phase 3 被假设 6 悬而未决拖累。

---

## 落地前必改清单（进入 Phase 1 的门槛）
- [ ] 引用并确立 `kzocr/engine/types.py` 为层间 NIR（K1）
- [ ] 适配器返回 `PageResult` 而非 `str`（K1）
- [ ] 单点归属共识计算（EngineRouter 装配 / Verifier 裁定）（K2）
- [ ] 补最小 TOC 分析器或拆分 Phase 5（K3）
- [ ] mock 回退置 `is_mock` + ERROR + 阻断 publish（K4，对齐 H8）
- [ ] 补全 `AdapterMeta`（supports_context/supports_confidence，kind 用 Literal）（K5/K12）
- [ ] 定义 HumanGate 回写机制（K7）
- [ ] 决策 kimi 适配器"import 包装 vs vendor vs 重写"（K6）
