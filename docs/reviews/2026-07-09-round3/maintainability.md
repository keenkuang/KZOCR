# 可维护性与工程规范评审 · Round 3 — OCR 引擎统一架构方案

- **角色**：可维护性 / 工程化评审专家
- **评审日期**：2026-07-09
- **评审对象**：`docs/plans/ocr-engine-unification.md`（v0.1）
- **对照代码**：`kzocr/engine/run.py`、`kzocr/config.py`、`kzocr/modelscope_pool.py`
- **结论倾向**：**有条件通过**。分层方向正确，但"适配器接口与 kimi 现状未对齐"、"`run.py` 共享逻辑无去处"、"降级链与可观测性未规划"三件事不先解决，落地会变跨仓库双重维护 + 逻辑僵尸化，抵消解耦收益。

---

## 一、结论

把"接引擎"与"选引擎"解耦、用 `EngineRouter` 替换 `run_engine()` 三路硬编码分支，方向完全正确；质量门（字形校验 + 人工兜底）思想扎实。但作为一份**可维护性**草案，它最危险的不是分层本身，而是 5 个工程化问题未落地：

1. **适配器来源误配**：方案 §2.2 把 kimi `BookPipeline` 的**内部子组件**（6 个 `*_adapter`）当成 KZOCR 可直接 `recognize_page(img)->str` 的顶层引擎，会引发跨仓库双套接口 / 双重维护（严重度 High）。
2. **共享逻辑无去处**：`run.py` 约 200+ 行引擎无关逻辑（渲染/裁剪/后处理/跨页合并/Markdown 重建）方案未说搬去哪，是"每适配器各抄一份"还是"继续僵尸在 run.py"，二者皆坏（High）。
3. **registry 不能单独消除对 `run.py` 的改动**：只有把共享逻辑下沉后，registry 才算真正接管；否则"改 run.py"会变成"改 run.py + 改 router"两处（Medium，见下）。
4. **降级链与可观测性零规划**：现有 `_init_vlm_adapter` 已是 SenseNova→PaddleOCR-VL 硬编码降级；方案 §9 只说"每适配器 try/except 降级下一候选"，既没说降级编排放哪，也没说统一日志/指标（评审点 4、5 缺口，Medium/High）。
5. **配置与文档碎片化无治理机制**：§8(5) 倾向每适配器 `*.toml`，会让 KZOCR 配置在"已双轨（KZOCR↔kimi）"基础上再碎成 10 份；10 份 `docs/engines/*.md` 也缺"缺失即 CI 失败"的强制措施（Medium）。

**建议**：把"接口对齐 + 共享逻辑下沉 + 降级链/可观测性统一进 base"作为阶段 1 的硬门槛，再谈 10 个适配器铺开。

---

## 二、关键问题（按严重度）

### [High-1] 跨仓库适配器复制 + "双套接口"漂移风险（评审点 1）

- **现状**：方案 §2.2 的 10 个适配器中，6 个标注"已存在于 kimi `tcm_ocr.core.engines.*_adapter`"（PaddleOCR / RapidOCR / UniRec / PaddleOCR-VL-1.6 / ShizhenGPT / SenseNova）。但 kimi 的 `*_adapter` 是 `BookPipeline` 的**内部子组件**，不是 KZOCR 可直接以 `recognize_page(img)->str` 驱动的顶层单元。
- **接口实锤漂移**（对照 `tcm_ocr/core/engines/`）：
  - kimi：`recognize_page(self, page_img, prompt=None) -> str`、`recognize_pages(self, page_imgs, prompt=None) -> str`（**返回单个字符串**）；
  - 方案：`recognize_page(self, img) -> str`、`recognize_pages(self, imgs) -> list[str]`（**返回列表**）。参数名、`list[str]` 语义、`prompt` 是否保留均不同；
  - kimi 现有适配器**没有** `meta: AdapterMeta`，没有 `kind/requires_gpu/needs_api_key/min_vram_gb` 声明；方案引入的全新 `AdapterMeta` 与现状零对接。
- **后果**：阶段 1 "搬出 run.py"时，要么从 tcm_ocr **复制**适配器代码到 `kzocr/engines/adapters/`（双仓库双重维护、必然漂移），要么在 KZOCR 侧**重写**一层适配（工作量翻倍且易与上游行为不一致）。
- **建议**：KZOCR 适配器只做**薄封装**（wrapper/facade）引用 tcm_ocr 适配器，绝不复刻；以方案 `OCREngineAdapter` 为准，提供从 kimi `page_img`/`str` 语义到 `img`/`list[str]` 语义的 shim 适配层，`AdapterMeta` 由 KZOCR 侧注入，不要求 kimi 改接口。

### [High-2] "真实 kimi 路径"无法套用 `recognize_page(img)->str` 契约（架构正确性）

- **现状**：`_run_real`（`run.py:96`）调 `BookPipeline.process_book(pdf, book_id)`，返回整书 `final_markdown` 再 `_markdown_to_pages` 重建；**不接受单页 numpy、也不逐页返回字符串**。
- **矛盾**：方案 §2.2(A) 把 `PaddleOCRAdapter`（kimi 侧）列为"本地非视觉 OCR，逐行/逐块识别"，假设 `recognize_page(img)->str`；但 kimi 的 `PaddleOCRAdapter` 是 BookPipeline 内部被编排的零件，不是对外单页识别器。
- **建议**：明确"书级适配器"与"页级适配器"两档契约。`BookPipeline` 作为 `BookLevelAdapter`（输入 PDF、输出 `BookResult`），与页级 `OCREngineAdapter` 并列，router 按 kind 调度，避免强行把所有东西压成 `recognize_page`。

### [High-3] `run.py` 共享逻辑去处未规划（评审点 1、4）

- **现状**：`run.py` 中 `_pdf_page_to_numpy`(241)、`_crop_to_body`(254)、`_vlm_postprocess`(325)、`_vlm_markdown_to_pages`(294)、`_merge_cross_page_breaks`(348)、`_markdown_to_pages`(159) 等约 200+ 行是**跨引擎通用**的，与具体引擎无关。（注：round2 SWEng 已指出 `_crop_to_body` 当前 VLM 路径实际调用的是 `_crop_to_body(_pdf_page_to_numpy(page))`，但 `_run_real` 路径不裁切——逻辑分布需统一。）
- **问题**：方案只说"建 `base.py` + `registry.py` + 各适配器模块"，**没说这些 helper 搬去哪**。
  - 若每适配器各抄一份 → 直接违背"减少重复"初衷；
  - 若仍留在被绕过的 `run.py` → router 重构后 `run.py` 继续臃肿且逻辑僵尸化。
- **建议（硬门槛）**：阶段 1 即把这些通用逻辑下沉到 `kzocr/engines/_common.py` 或 `base.py`（page→numpy、crop、postprocess、markdown↔pages、跨页合并），由 `BaseAdapter` 默认复用，单一真相来源。

### [Medium-1] registry 能否消除对 `run.py` 的改动？——"新增一个后端要改几处"量化（评审点 1）

理想状态：有了 `registry.py` + 自动发现，新增后端应只需 **改 1 处**（放置适配器模块，registry 自动 import）。但按当前方案草案，实际改动处为：

| 改动项 | 是否必要 | 说明 |
|---|---|---|
| 1. 新增 `adapters/<name>.py` + 实现契约 | 必要 | 核心工作 |
| 2. `registry.py` 注册一行（或 `__init__` 自动扫描） | 视机制 | 若用显式注册表则 +1 处；自动发现则 0 |
| 3. `AdapterMeta` 声明 | 必要 | 可内联在模块内 |
| 4. 配置：`<name>_*` 字段 或 `<name>.toml` | 必要 | 见 Medium-3 |
| 5. `docs/engines/<name>.md` | 必要 | 见 Medium-2 |
| 6. `run.py` / `EngineRouter` 的改动 | **取决于共享逻辑是否下沉** | **这是关键** |

**核心判断**：`registry.py` 只能消除"在 `run.py` 里加 if/else 分支"那种硬编码改动；**它无法消除对 `run.py` 的改动，除非 `run.py` 的 200+ 行共享逻辑已下沉到 `_common.py`**。否则迁移后会出现 `run.py`（残留共享逻辑）+ `router.py`（新编排）两处都要动，反而增加改动面。方案当前缺这一步，故"registry 消除对 run.py 改动"的承诺**不成立**，必须先做 High-3。

### [Medium-2] 无适配器模板/脚手架，10× 重复成本不可控（评审点 1）

- 每个适配器"模块 + 配置 + 说明"三件套，方案未提供任何脚手架。10 个适配器 = 10 份高度同构的代码骨架与文档。
- **建议**：
  - `BaseAdapter` 提供默认实现：`recognize_pages` 默认循环调用 `recognize_page`；统一出错包装（每适配器 try/except 降级）、统一 `engine_label` 取自 `meta.name`；
  - 提供 `kzocr engine new <name>` CLI 生成骨架（`<name>.py` + `<name>.toml` 占位 + `docs/engines/<name>.md` 模板含必需标题），把"新增成本"从手写降到填空。

### [Medium-3] 降级链治理：放 Router 还是散落各适配器（评审点 4）

- **现状**：`_init_vlm_adapter`（`run.py:194`）已是硬编码降级——SenseNova 失败 → 降级 PaddleOCR-VL。这是**散落在引擎初始化里**的降级逻辑。
- **方案 §9 风险**说"每个适配器包 try/except，失败则降级下一候选，全失败 → HumanGate"。若字面落地，即把降级链**写进每个适配器**，则：
  - 降级顺序（谁降谁）散落在 10 个文件，改优先级要改 N 处；
  - 与 `EngineRouter` 的"按策略挑选候选"职责重叠，形成两套选择逻辑。
- **建议（明确立场）**：**降级编排统一收口到 `EngineRouter`**，各适配器只对自己内部可重试的故障（如单页超时）负责，**不感知"我该降给谁"**。Router 持有 `prefer` 候选列表与可用性探测结果，逐个尝试、捕获异常、推进下一候选，全失败才 `HumanGate`。这样既消除 `_init_vlm_adapter` 的硬编码降级，又保证降级顺序单一可配（在 `engines.toml [strategy]` 里调 `prefer` 即可，无需改代码）。

### [Medium-4] 可观测性：每个适配器/环节缺统一日志与指标（评审点 5）

- **现状**：`modelscope_pool.py` 日志较规范（启用/禁用 provider、成功/失败均有 `logger.info/warning`）；但 `run.py` 的 VLM 路径只有零散 `logger.info`，且**无任何结构化指标**（耗时、成功率、字级数、降级次数）。各适配器一旦独立，可观测性会更碎。
- **缺口**：方案全文未提可观测性——排障时无法回答"这次走的是哪个适配器、耗时多少、第几页失败、降了几次级"。
- **建议（明确立场）**：在 `BaseAdapter` 内嵌统一可观测性，而非各适配器自写：
  - **统一日志**：结构化 `logger` 前缀 `[engine=<name>]`，每页/每书一条带 `book_code/page/elapsed_ms/chars` 的日志；
  - **统一指标**：`BaseAdapter` 包裹 `recognize_page(s)`，统计 `latency_ms / success / fail / chars_out / fallback_count`，暴露为进程内指标（如 `prometheus_client` 或轻量 `dict`）并落到现有 `Line.engine_texts` / 新增 `run_metrics` 便于回溯；
  - Router 层再汇总"本次链路最终走哪条候选、降级几次"，写入 `BookResult` 元数据（如 `engine_path: ["sensenova"(fail)→"paddleocr_vl16"(ok)]`），这是排障第一手证据。

### [Medium-5] 文档一致性靠"强制清单"无机制保障（评审点 3）

- 方案 §2.3 要求 `docs/engines/*.md` 必含 6 项（部署依赖/启动/配置/资源/局限/出境）。但 round2 已实锤同类问题：`toc-driven-pipeline-design.md` 所述组件**从未实现**，文档与代码行号漂移（SWEng Medium：「设计文档与代码已不同步」）。在 10 个文档上复刻同一"强制清单"几乎必然过期。
- **建议**：能机器抽取的字段（部署依赖、端点、是否出境、资源占用）从 `AdapterMeta` **自动生成**（`kzocr engine describe <name>` 或 CI 渲染），纯主观项（已知局限）由 **CI 校验"每个已注册适配器必须存在 `docs/engines/<name>.md` 且含 6 个必需标题"，缺失即 CI 失败**。否则又是一堆过期 md。

### [Medium-6] 配置存放：集中 vs 每适配器 toml 的取舍（评审点 2）

- **现状碎片**：`run.py` 已有 `_build_engine_config()` 以 `engine_configs: {paddleocr, rapidocr, unirec, paddleocr_vl16, shizhengpt, mineru, tesseract, cloud_llm}` 注入 kimi BookPipeline；kimi 读的是**自己的 config schema**，与 KZOCR `Config` 是两套表示。`config.py` 另平铺 `sensenova_*` / `deepseek_*` / `vlm_*` 字段。KZOCR↔kimi 已是**配置双轨**。
- **对 §8(5) 倾向"每适配器 toml"的担忧**：再在 KZOCR 内拆 10 个独立 `*.toml` + 各自 loader，会让配置真相源进一步碎片化，且与 kimi 侧 schema 形成"三套"。每适配器 toml 把这些值打散到 10 个文件，**不利于集中审计**（尤其 H3 数据出境开关需一眼可见所有出境端点）。
- **对 discoverability / 校验的影响**：
  - 每适配器 toml：**可发现性差**（要翻 10 个文件才知道有哪些可配项）、**校验弱**（分散 loader 难做统一 schema 校验）、**默认值散落**（各自文件，易不一致）；
  - 集中 schema + 命名空间：**可发现性好**（一处可览全部）、**校验强**（单一 schema + 默认值合并机制）、但 `config.py` 平铺 10 组字段会膨胀。
- **建议（见第四节立场）**：采用"**集中 schema + `Config.engines.<name>` 命名空间**"：物理上 `engines.toml` 可分段，但加载后归一到一个 `Config` 对象；由 `AdapterMeta` 声明默认值与必填项，提供**默认值合并 + schema 校验**（缺失 key / 类型错 / 出境端点未声明则启动报错），避免 10 个独立 toml 的碎片化与校验盲区。

### [Medium-7] 测试覆盖不足，迁移回归风险高（评审点 4）

- 现有仅 `tests/test_pipeline.py`、`tests/test_vlm.py`，且 `run.py` 的 mock/vlm/real 三路分支、`_init_vlm_adapter` 降级链、跨页合并均缺充分单测。迁移到 registry/router 后回归面放大：适配器契约、router 选择矩阵、glyph verifier、降级链。
- **建议**：把"契约测试"作为阶段 1 完成门槛——`runtime_checkable Protocol` + 启动期 registry 自检（每个注册适配器满足 `OCREngineAdapter` 且 `meta` 完整）；阶段 2 加 router `probe` 矩阵测试（含降级顺序）；阶段 3 加 `GlyphVerifier` 单测。

### [Low-1] `engine_label` 硬编码延续

- `run.py` 在运行时赋值 `engine_label`（`VLM_ENGINE_LABEL` 硬编码即使实际走 SenseNova，见 `run.py:27,222,236`）。方案用 `meta.name` 替代方向正确，但迁移期需保证旧 `adapter.engine_label` 用法全量替换或兼容，否则新旧两套标识并存。

### [Low-2] `recognize_pages` 上下文语义未定义

- 方案 `recognize_pages(imgs: list) -> list[str]` 未说明"多页是否共享上下文"。kimi `SenseNovaAdapter.recognize_pages` 接收含"下页顶部 15%"的 imgs 作上下文（`run.py:459-464`）。若契约只给裸页列表，`BaseAdapter` 默认循环会丢失跨页上下文。需在 `meta.supports_context` 之外明确 `recognize_pages` 的入参约定（如允许传 `(cur_img, next_top_img)` 上下文对）。

---

## 三、改进建议（落地优先级）

1. **接口对齐写进阶段 1（硬门槛）**：定义唯一 `OCREngineAdapter` 契约 + kimi→KZOCR 的 shim 适配层；`AdapterMeta` 由 KZOCR 注入；`BaseAdapter` 默认实现通用方法（页级循环、出错包装、`engine_label` 取自 `meta`）。
2. **区分 book-level 与 page-level 适配器**：`BookPipeline` 作为 `BookLevelAdapter`，与页级适配器并列被 router 调度，避免契约强拧。
3. **共享逻辑下沉（决定 registry 是否真能减负）**：`page→numpy / crop / postprocess / markdown↔pages / 跨页合并` 全部移入 `kzocr/engines/_common.py`，由 `BaseAdapter` 复用。**这是 Medium-1 中"消除对 run.py 改动"的前提。**
4. **降级链收口到 Router**：各适配器只管自身可重试故障，不感知降级目标；Router 持有 `prefer` 候选列表与可用性探测，逐个尝试、全失败才 `HumanGate`。消除 `_init_vlm_adapter` 硬编码降级。
5. **统一可观测性进 BaseAdapter**：结构化日志 `[engine=<name>]` + 统一指标（latency/success/fail/chars/fallback），Router 汇总 `engine_path` 写入 `BookResult` 元数据，作为排障第一手证据。
6. **脚手架**：`kzocr engine new <name>` 生成 `*.py + *.toml + docs 模板`；CI 校验"注册即文档齐备 + 6 必需标题 + Protocol 自检 + 配置 schema 校验"。
7. **配置单一真相源 + 校验**：`Config.engines.<name>` 命名空间集中，默认值由 `AdapterMeta` 派生，加载即做 schema 校验与默认值合并；避免 10 个独立 toml 碎片化。
8. **文档自动化**：可机抽字段由 `AdapterMeta` 生成，主观项靠 CI 校验，杜绝"强制清单"式过期文档。
9. **契约测试前置**：registry 启动自检 + router 选择矩阵（含降级顺序）+ glyph verifier 单测，作为各阶段完成门槛。

---

## 四、对第 8 章假设项的立场

| 假设 | 立场 | 理由 |
|---|---|---|
| **(1) 字形校验机制**：暂不加独立再识别视觉模型 | **基本同意，预留接口** | 成本合理；建议把 `VisionRecheckAdapter` 作为**可选挂载点**预留（对 FAIL/UNKNOWN 行回看裁剪图），而非完全搁置，避免未来补时需重构 router。 |
| **(2) 最小小节定义**：TOC 三级 vs 更小 | **可配置，默认二级/三级** | 切分粒度属策略而非契约，抽象层应接受 `min_section_level` 配置，避免硬编码切分逻辑散落归档层。 |
| **(3) 方剂库归属**：zai `Formula` 表 vs 独立 khub | **先写 zai，khub 异步可选** | 主链路只写 zai `Formula/FormulaIngredient`（最小耦合）；khub 同步作为**异步/可选导出**，不在主 OCR 链路强制跨库事务，降低故障面。 |
| **(4) consensus 模式成本**：默认 single？ | **同意默认 single** | 无 GPU 环境多引擎并行代价高；consensus 仅当多本地引擎可用或显式 `allow_cloud_vision` 时开启，逐行比对走 UNCERTAIN→人工，不阻塞主链路。 |
| **(5) 适配器配置存放**：集中 `config.py` vs 每适配器 `*.toml` | **反对纯每适配器 toml，主张"集中 schema + `Config.engines.<name>` 命名空间 + 加载期 schema 校验/默认值合并"** | 每适配器 toml 会碎片化配置真相源（且 KZOCR↔kimi 已是双轨），不利于集中审计（尤其 H3 数据出境需一眼可见所有出境端点）；可发现性差、分散 loader 难做统一校验。集中式契合"去硬编码、集中可审计"整改，但 `config.py` 平铺会膨胀——故用命名空间集中、物理文件可拆但归一加载，并强制 schema 校验与默认值合并。 |
| **(6) 字形知识库来源**：复用 kimi `term_kb` vs KZOCR 内置白名单 | **KZOCR 内置精简白名单，term_kb 作可选增强** | 避免与引擎仓库（tcm_ocr）强耦合；KZOCR 自持最小字形白名单保证可独立运行，term_kb/RuntimeDB 作为可选回流源（人工校对结果回灌闭环）。 |
| **（新增）降级编排归属** | **收口到 `EngineRouter`，各适配器不感知降级目标** | 现有 `_init_vlm_adapter` 硬编码降级是反面教材；散落各适配器的降级链会导致顺序不可配、与 router 职责重叠。Router 持有 `prefer` 候选与可用性探测，统一尝试/降级/兜底。 |
| **（新增）可观测性** | **统一进 `BaseAdapter` + Router 汇总** | 现状无结构化指标，排障黑盒。BaseAdapter 包裹统计 latency/success/fail/chars/fallback；Router 写 `engine_path` 到 `BookResult`，为每次链路留第一手证据。 |

---

## 五、一句话风险摘要

方案分层正确，但"适配器来自 kimi 内部组件却要当顶层引擎用"+"接口签名未对齐"+"共享逻辑无去处"+"降级/可观测性零规划"四件事不先解决，阶段 1 一动手就会变成跨仓库双重维护 + `run.py` 逻辑僵尸化 + 降级链散落，抵消全部解耦收益；其中**只有先做 High-3 共享逻辑下沉，registry 才能真消除对 `run.py` 的改动**。
