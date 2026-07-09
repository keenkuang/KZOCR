# KZOCR 统一 OCR 引擎架构方案 —— 架构师评审（round4 · 核查 round3 真闭合）

- **评审角色**：首席架构师
- **评审日期**：2026-07-09
- **评审对象**：`docs/plans/ocr-engine-unification.md`（草案 **v0.2**，已吸收 round3 8 角色意见 + `summary.md` 裁决）
- **核查来源**：round3 `summary.md` §3（I1–I10）与 §1（H0-A~H0-E 五道硬门槛）；本角色 round3 `architect.md`（K1–K15）；配套代码 `kzocr/engine/types.py`、`kzocr/engine/run.py`、`kzocr/engine/mock.py`、`kzocr/adapter/to_zai_prisma.py`
- **范围**：仅调查 + 文档评审，未修改主方案或代码。

---

## 结论

**有条件通过（Conditional Pass）。**

v0.2 相比 v0.1 是**质的改善**：round3 的 8 角色全部 High 级问题在文档层面都得到了正面回应，H0-A~H0-E 五道硬门槛也逐条落到阶段 0/1 的设计意图里。特别是「契约冻结」「共享逻辑下沉」「目标 schema 对齐」「性能预算」四处此前最危险的缺口，已从"方案盲区"变为"明确写入架构的设计约束"。

但**两个 residual 项必须在定稿（进入阶段 1 实现）前真冻结**，否则会削弱 H0-A 的承诺、并让 v0.2 自己新引入的转换边界缺口暴露出来：

1. **AdapterPageResult → `LineResult/PageResult` 的转换责任未指定**（v0.2 新引入的架构问题，见 §3）。§1 声称"层间唯一契约=types.py"，但 §2.1 适配器边界返回的是**不在 types.py 中的新类型 `AdapterPageResult`**；从一页 `text` 切行、按 `AdapterMeta.name` 填入 `LineResult.engine_texts`、对齐字级 `char_confidences` 到具体字符——这一整段装配逻辑 nowhere assigned。若不实写归属，它会像 round3 K1 预言的那样溜回 `EngineRouter`，使"唯一契约"名存实亡。
2. **`glyph_verified`（文本）vs 新增 `glyph_status`（枚举）的最终裁决被推迟**（§4.3 写"二者择一，方案定稿时冻结"）。这是 I1 的核心残留——冲突已被**点名**，但**未裁决**。

这两项是"定稿前必改"的门槛；在它们冻结之前，建议**不要铺 10 个适配器**。除此之外，v0.2 的架构方向、分层、职责切分、降级收口、性能预算、出境收敛均已自洽。

---

## round3 问题闭合度

> 判定口径：
> - **已闭合** = v0.2 在文档层面给出明确、自洽、可落地的设计，无残留歧义。
> - **部分** = 方向已对/意图已写，但有关键裁决或归属仍悬空、或"代码声称"需实现后才成立。
> - **未闭合** = 仍重述旧问题或未实质回应。

### 五道硬门槛（H0-A~H0-E）

| 门槛 | 判定 | 证据 / 说明 |
|---|---|---|
| **H0-A 契约冻结** | **部分** | §1 显式"层间唯一契约=`types.py`"、§2.1 适配器改返回结构化 `AdapterPageResult`、§4.3 冻结 `glyph_status` 枚举——方向全对。残留：(a) `AdapterPageResult` 不在 types.py，且它→`LineResult` 的转换归属未写（新缺口，见 §3）；(b) `glyph_verified`/`glyph_status` 关系推迟"定稿时冻结"，未真裁决（见 §3 新项 2）。 |
| **H0-B 共享逻辑下沉** | **已闭合（设计意图）** | §7 阶段 1 建 `kzocr/engines/_common.py` 下沉渲染/裁剪/后处理/跨页合并/Markdown↔pages，`BaseAdapter` 默认复用；§2.2 区分 page/book 级适配器。核对 `run.py`：待下沉函数确为 `_pdf_page_to_numpy`、`_crop_to_body`、`_vlm_markdown_to_pages`、`_vlm_postprocess`、`_merge_cross_page_breaks`、`_markdown_to_pages`（约 150–200 行）。意图在架构上自洽——下沉后 registry/适配器无需再碰 `run.py`。**但本角色仅核"意图自洽"，未看实现**（符合评审约定）。实现期需逐函数给定签名与 `_common` 边界，否则逻辑可能泄漏进适配器。 |
| **H0-C 安全端点收敛** | **已闭合（设计）** | §2.4 复用/扩展 `khub/client.py:_validate_url`：域名 allowlist、拒 RFC1918/回环外内网、明文 http 告警、建连前 DNS 复检防重绑定；`vlm_host` 仅本机/Unix socket。§1/§9 已把"版心裁剪=数据最小化"更正为"仅压缩带宽、不脱敏"（§9：「版心裁剪仅压缩带宽、不脱敏」）。出境开关 + 逐书/逐页同意 + 出境审计日志写入 §9。设计层已闭环。 |
| **H0-D 目标 schema 对齐** | **已闭合（设计）** | §6 全文重写为"唯一事实源=规范 `schema.prisma`"，扁平子集收敛为「向规范 schema 的适配层」；全文→`FinalDocumentRecord(sha256)`（非不存在的 `Book.final_markdown`）、目录→`ContentNode`、最小小节经 `contentNodeId` 挂载、**严禁重切生成新 Line**、方剂→规范 `FormulaComposition`。直接回应 data_integrity C1（CRITICAL）。设计意图闭合。 |
| **H0-E 性能预算** | **已闭合（设计）** | §3 把预算写入架构：单页 VLM 120s / SenseNova 90s、`KZOCR_TOTAL_TIMEOUT=7200s` wall-clock、`KZOCR_MAX_PAGES=500`（时间+内存双闸）、`KZOCR_MAX_CONCURRENCY=1`（含云端≤2）、`KZOCR_PAGE_RETRIES=2`+退避、同引擎连续 2 超时熔断转 UNCERTAIN/HumanGate、禁止静默丢页。全面覆盖 round3 P1–P5。设计层闭合。 |

### 跨角色 High/Medium 问题（I1–I10）

| # | round3 问题 | 闭合度 | 证据 / 说明 |
|---|---|---|---|
| **I1** | 层间契约缺失（`str`）+ `glyphVerified` 语义冲突 | **部分** | 适配器改返回结构化（§2.1）、显式引用 types.py（§1）——已解决"str 过弱"与"弃用 types.py"。但：`glyph_verified`(文本) 与新增 `glyph_status`(枚举) 的最终取舍仍写"二者择一，定稿时冻结"（§4.3），**未裁决**；且 `AdapterPageResult`→`LineResult` 转换口子未堵（见 §3）。故判部分。 |
| **I2** | `run.py` 共享逻辑无去处 + kimi 接口不对齐 | **已闭合（设计）** | §7 `_common.py` 下沉 + §2.2 `BookPipeline` 作 `BookLevelAdapter` 薄封装 shim、`AdapterMeta` 由 KZOCR 注入。设计自洽。实现期需确认 kimi 内部 `*_adapter` 不被当顶层引擎重写（可维护性 High-1）。 |
| **I3** | 共识职责重叠 + 跨引擎行对齐不可行 | **部分** | 职责归属已厘清：§3 Router 只做**顺序降级 + 候选装配**（写 `engine_texts`），§4.2.6 GlyphVerifier 读 `engine_texts` 做**多数票裁定**（PASS/UNCERTAIN）。这消除了 K2 的"双份比对"冲突。但异构引擎（裸文本 vs Markdown）的**块级行对齐键机制**仍无正文描述、仅列"阶段 2 前置"，属于延续性未闭合（原 K8/I3 的对齐难点未在本方案正文落地，靠阶段 2 兜底）。 |
| **I4** | 出境最小化伪命题 + SSRF | **已闭合（设计）** | 见 H0-C。版心裁剪伪脱敏已更正、端点 allowlist/DNS 复检已规划、细粒度同意+出境审计已写入 §9。 |
| **I5** | 归档 schema 错配 + 重切丢 bbox | **已闭合（设计）** | 见 H0-D。`ContentNode`/`FormulaComposition`/`FinalDocumentRecord` 已转正，重切生成新 Line 被明文禁止。 |
| **I6** | 罕见中医字误判 + 缺归一化 + `UNKNOWN` 漏放 | **已闭合** | §4.2 增加 `normalize()`（繁→简/异体→正体）、`RARE` 态（命中中医候选字表不进人工队）、形似混淆集（FAIL）；§5 HumanGate 触发条件**显式补列 `UNKNOWN`**（原 v0.1 漏放点已堵）。完整回应领域 K1/K2/K8 + UX C1。 |
| **I7** | mock 回退静默发假数据（重演 H8） | **已闭合（设计）** + 代码已部分到位 | v0.2 §5/§9/阶段 1 明确：`is_mock` 强制透传、`Book` 增 `is_mock` 列、`is_mock=True` 时归档/推送显 ERROR **且阻断 publish**。代码核对：`kzocr/engine/mock.py:147` 的 `mock_book_result` **已**置 `is_mock=True`，且 `run.py` 的降级路径（`run.py:34/44/53`）正是调用它——**故 round3 所称"run.py 失败回退未设 `is_mock`"在当前代码中已不成立**（比 round3 假设的状态更好）。**残余缺口在 sink 端**：`to_zai_prisma.py` 的 `Book` DDL（L30-33）**无 `is_mock` 列**，且 `push_book_to_zai` 全程**无 `is_mock` 阻断守卫**——这正是 v0.2 要在阶段 1 补的。结论：source 端已正确，sink 端已"规划"，规划与现状一致；设计层闭合，实现待补。 |
| **I8** | 性能预算缺失 | **已闭合（设计）** | 见 H0-E。 |
| **I9** | 方剂七字段 + khub 闭环 + 毒性告警 | **已闭合（设计）** | §6 以规范 `FormulaComposition` 为准、补齐七类字段、剂量保留原串；内置 `toxic_herbs.json` 打 `isToxic` + 用量红线（细辛≤3g、附子须炮制）；khub 异步可选、不阻塞（采纳假设 3）。 |
| **I10** | 配置迁移 + 降级收口 + 可观测性 | **已闭合（设计）** | §8 假设 5（集中 schema + `Config.engines.<name>` + 加载期校验 + toml 仅覆盖层 + 密钥不进 toml）；§3 降级链收口 Router、各适配器不感知降级目标；`BaseAdapter` 统一 `[engine=<name>]` 日志前缀 + 指标 + `engine_path` 写 `BookResult`。 |

**闭合度小结**：五门槛中 4 个「已闭合（设计）」、1 个「部分」（H0-A）；I1–I10 中 7 个「已闭合（设计）」、3 个「部分」（I1、I3，及 I7 的 sink 端待实现但已规划）。**无「未闭合」**——round3 的每一处都被 v0.2 正面接住，这是相对 v0.1 的本质进步。

---

## v0.2 新引入的架构问题

### [新-1 · High] AdapterPageResult → `LineResult` 转换责任缺位（削弱 H0-A）
- **现象**：§2.1 适配器接口返回 `AdapterPageResult(text: str, confidence, char_confidences: list[float], crop_img, meta)`，而 §1 宣称"层间唯一契约=types.py（`BookResult/PageResult/ParagraphResult/LineResult`）"。`AdapterPageResult` **本身不在 `types.py` 中**（定义在 `base.py` 代码块内），是适配器的外部边界类型。
- **缺口**：从一页 `AdapterPageResult.text` 到 `PageResult.paragraphs[].lines[]` 需要"切行/分段 → 按 `AdapterMeta.name` 填 `LineResult.engine_texts[engine]=text` → 把 `char_confidences`（一个扁平 list）对齐到具体字符"的完整装配。这段逻辑 **v0.2 未指定归属**。
- **风险**：若不实写，它会像 round3 K1 预言的那样回流进 `EngineRouter`，使"唯一契约"沦为口号——Router 既选引擎又切文本又对齐置信度，重新膨胀为单体。这与 H0-A 的"契约冻结、registry 真减负"直接冲突。
- **必须补**：在 §2.1 或 §3 显式写明"EngineRouter 负责把每个 `AdapterPageResult` 装配进共享的 `LineResult/PageResult`（`engine_texts` key=AdapterMeta.name、`confidence` 取页/行级、块级结构由 `ParagraphResult.node_type/heading_level` 承载）"，并约定 `char_confidences` 的对齐策略（按字符顺序对应 `char_level_json`）。

### [新-2 · Medium] `glyph_status`（枚举）与 `glyph_verified`（文本）的双字段语义仍悬而未决
- §4.3 提议新增 `Line.glyph_status: Literal[PASS|RARE|UNKNOWN|FAIL|UNCERTAIN]`，并说"保留 `glyph_verified` 作校验后文本…或显式迁移所有消费方——**二者择一，方案定稿时冻结**"。
- 这是 I1 的核心残留：冲突已点名，但**未裁决**。当前 `types.py` 的 `glyph_verified: Optional[str]` 被 `to_zai_prisma.py`、`export_markdown`、`mock.py`、CLI 文本消费按"文本"使用；若直接新增 `glyph_status` 枚举且让 `glyph_verified` 仍表文本，**二者可并存且无冲突**（语义正交：一个存"状态"，一个存"校验后文本"）。建议 v0.2 定稿时直接采纳"并存"方案、删除"或迁移"的开放选项，把 §4.3 的"二者择一"改写为确定结论，避免在实现期再起分歧。

### [新-3 · Medium] book-level 适配器与字形校验层的边界未定义
- §2.2 允许 `BookLevelAdapter`（输入 PDF、输出 `BookResult`，如 kimi `BookPipeline` 薄封装）。但 `BookResult` 已含 `pages[].lines[].engine_texts` 等契约字段；boundary 问题是：**GlyphVerifier 是否对 book-level 适配器产出的 `LineResult` 再做逐行校验？** 若 `BookPipeline` 已返回"校验后"文本，则 Verifier 是重验还是信任？v0.2 未说明 book-level 结果进入 Verifier 的契约。
- 建议 §2.2 补一句：book-level 适配器**必须返回已填 `engine_texts`/`confidence` 的 `LineResult`**，与 page-level 适配器对齐，统一进入 GlyphVerifier；`BookPipeline` 的"内部校验"不替代本方案的质量门。

### [新-4 · Low] `to_zai_prisma.py:153` 的 `auditSource` bug 与"source 列"措辞需对齐
- v0.2 §4.3 已点名 `to_zai_prisma.py:153` 把 `auditSource` 写成 `book.engine_label` 的 bug，并计划修正为 `dictionary/consensus/human/rare_allowlist/confusion`。✓
- 但 §5 说"Book 表增 `is_mock`/`source` 列"——实际 `to_zai_prisma.py` 的 `Book` DDL **已有 `source` 列**（L32），且当前被写入 `book.engine_label`（L119），并非 `is_mock`。v0.2 的"增 source 列"措辞与现状不符，应改为"增 `is_mock` 列、`source` 列维持承载 engine_label（或改名 `engineLabel`）"。属文档精度问题，不影响架构。

---

## 改进建议（定稿前必改 / 实现期要点）

1. **（定稿必改）冻结 `AdapterPageResult`→`LineResult` 转换归属**（解决新-1 / H0-A）。在 §2.1 或 §3 新增一小节"装配契约"：EngineRouter 把每个 `AdapterPageResult` 装配为 `LineResult`，`engine_texts[AdapterMeta.name]=text`、`confidence` 取行/页级、`char_confidences` 按字符序对齐到 `char_level_json`、块级结构经 `ParagraphResult.node_type/heading_level` 承载。明确此职责**只属于 EngineRouter**，Verifier/Archiver 不再做文本切分。
2. **（定稿必改）把 `glyph_status`/`glyph_verified` 改为"并存"确定结论**（解决新-2 / I1）。§4.3 删除"二者择一，定稿时冻结"的开放句，直接写：`glyph_status` 为新增枚举（状态）、`glyph_verified` 保留为校验后文本（与现有消费方兼容），二者正交并存。
3. **（定稿必改）`to_zai_prisma.py` 的 `Book` DDL 增 `is_mock` 列 + `push_book_to_zai` 加 `is_mock=True` 阻断守卫**（解决 I7 sink 端）。这是对"阻断 publish 假古籍"的最后一块拼图——当前 source 端已正确（mock.py:147），sink 端缺失。
4. **（实现期）`_common.py` 下沉时逐函数给定签名与边界**（H0-B）。建议明确清单：`_pdf_page_to_numpy`、`_crop_to_body`、`_vlm_markdown_to_pages`、`_vlm_postprocess`、`_merge_cross_page_breaks`、`_markdown_to_pages` 迁至 `_common`，由 `BaseAdapter` 默认复用；kimi shim 不复写这些。
5. **（实现期）共识行对齐机制落正文**（I3 残留）。§2.1 的 `AdapterPageResult.text` 是裸串，跨引擎块级对齐仍缺输入。建议在 `AdapterPageResult` 增加可选 `blocks: list[BlockResult]`（node_type/heading_level/lines），使 consensus 模式能基于块键对齐而非裸逐行——至少定义接口，实现可阶段 2 补。
6. **（定稿）修正 §5 "Book 表增 is_mock/source 列"措辞**（新-4）：source 已存在且承载 engine_label，仅增 is_mock。

---

## 对第 8 章 6 项假设裁决的再确认（round4）

> 站在架构师视角，复核 v0.2 §8 对 round3 裁决的吸收是否准确。

| # | 假设 | round3 裁决 | v0.2 吸收 | 再确认 |
|---|---|---|---|---|
| 1 | 字形校验暂不加独立再识别视觉模型 | 采纳（默认不加）+ 预留 `VisionRecheckAdapter` 钩子、recheck 仅限本地 | §4.2.7 预留 `VisionRecheckAdapter` 挂点、回看裁剪图、`仅限本地视觉引擎` | **准确**。钩子定位正确，且"仅限本地"避免了云端出境放大，与 H0-C 自洽。 |
| 2 | 最小小节定义 | 调整：可配置 + 按 book_type + 经 contentNodeId 挂载、禁止重切 | §6.3 最小小节=标题/方剂/穴位块界定的 Markdown 块、`min_section_level` 可配、默认 TOC 标题块、严禁重切生成新 Line | **准确**。且"严禁重切生成新 Line"直接堵住 I5 的 bbox 丢失路径，与 H0-D 一致。 |
| 3 | 方剂库归属 | 调整：主链只写 zai、用规范 `FormulaComposition`、khub 异步可选不阻塞 | §6.4 规范 `FormulaComposition`、七类字段、khub 异步可选、`version`+`checksum` 单向 zai→khub | **准确**。采纳假设 3 裁决，未把 khub 当阻塞项（符合 round3 对"强制同步 khub"的否决）。 |
| 4 | consensus 成本 | 采纳（硬约束）：默认 single、无 GPU 全本地 consensus 拒绝启动、含云端 N≤2、单引擎以 UNKNOWN/低置信补触发 | §3 策略：`single` 硬约束、无 GPU 全本地 consensus 拒绝启动、含云端 N≤2、单引擎 UNKNOWN/低置信补触发 | **准确且强化为硬约束**。✓ |
| 5 | 配置存放 | 调整：集中 schema + `Config.engines.<name>` + 加载期校验、toml 仅覆盖层、密钥不进 toml | §2.4 + §8.5 完全一致；密钥只走环境变量/secret | **准确**。安全底线（密钥不进 toml）与可维护性（真相源不碎片）双满足。 |
| 6 | 字形知识库来源 | 采纳：KZOCR 内置白名单为事实源、term_kb 仅可选增强、`KZOCR_TERM_KB_PATH` 须受控目录 | §4.1 进程内镜像白名单+异体+混淆集、term_kb 可选增强、`KZOCR_TERM_KB_PATH` 校验受控目录防路径穿越 | **准确**。且与性能 P6（禁逐字符查库）、安全（路径穿越）一致。 |

**再确认结论**：v0.2 §8 对 round3 六项裁决的吸收**全部准确、无走样**，且把"默认 single""KZOCR 内置白名单"等提升为硬约束/事实源，方向与 8 角色立场一致。无需回退任何一条裁决。

---

## 落地前必改清单（进入阶段 1 的门槛，相较 round3 增量）

- [ ] **指定 `AdapterPageResult`→`LineResult` 装配归属（EngineRouter）**，约定 `engine_texts` key、`confidence` 取级、`char_confidences` 对齐（新-1 / H0-A）。**定稿必改。**
- [ ] **`glyph_status`/`glyph_verified` 改为"并存"确定结论**，删除 §4.3"二者择一，定稿时冻结"开放句（新-2 / I1）。**定稿必改。**
- [ ] **`to_zai_prisma.py` 的 `Book` DDL 增 `is_mock` 列 + `push_book_to_zai` 加 `is_mock=True` 阻断守卫**（I7 sink 端）。**定稿必改。**
- [ ] **book-level 适配器进入 GlyphVerifier 的契约**（返回已填 `engine_texts` 的 `LineResult`，统一质量门）（新-3）。
- [ ] **共识块级对齐机制落正文**（`AdapterPageResult` 增可选 `blocks`，阶段 2 实现）（I3 残留）。
- [ ] **修正 §5 "Book 表增 is_mock/source 列"措辞**（source 已存在承载 engine_label，仅增 is_mock）（新-4）。

> 上述前 3 项为「定稿前必改」；其余为实现期要点。在 3 项定稿必改未冻结前，建议暂不铺 10 个适配器（沿用 round3 "五门槛未闭不合适配器"的纪律）。
