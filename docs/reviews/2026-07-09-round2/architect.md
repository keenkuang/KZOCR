# KZOCR 多角色评审 · 顶级软件架构师视角（第 2 轮）

- **角色**：顶级软件架构师（架构/分层/接口/可扩展性）
- **评审日期**：2026-07-09
- **评审范围**：
  - `kzocr/config.py`
  - `kzocr/engine/run.py`
  - `kzocr/engine/types.py`
  - `kzocr/engine/mock.py`
  - `kzocr/adapter/to_zai_prisma.py`
  - `kzocr/cli.py`
  - `kzocr/khub/client.py`
  - `kzocr/export_zai.py`
  - `kzocr/modelscope_pool.py`
  - `tests/test_pipeline.py`
  - `tests/test_vlm.py`
  - `docs/plans/toc-driven-pipeline-design.md`（最新设计）
- **总体结论**：**有条件通过**（Conditional Pass）。核心分层骨架（types 归一化数据结构 + engine 驱动 + adapter 写库 + export + khub 客户端）方向合理，但存在与最新设计严重偏离、真实引擎路径结构化数据丢失、配置双源、孤儿模块与全局表误清空等一系列需在合入前解决的架构缺陷。

---

## 架构层面的总体判断

项目的分层意图是清晰的：`types.py` 定义与引擎无关的归一化数据结构，作为编排层与具体引擎/下游之间的稳定契约；`engine/run.py` 负责把 PDF 跑成 `BookResult`；`adapter/to_zai_prisma.py` 把 `BookResult` 落盘到 zai 控制台；`export_zai.py` 从库导出；`khub/client.py` 推送下游。这条"归一化中间结构 + 适配器"的主线是可取的，也是后续扩展的基础。

但本轮确认的几个问题，已从"可维护性"升级为"架构正确性"层面：最新设计的核心思想（TOC 驱动分节管线）在代码中完全不存在；真实引擎路径把最宝贵的结构化逐行/范式数据全部丢弃，使 zai 工作台失去展示意义；配置存在两套来源、全局范式表被多书推送互相清空。这些问题若不先收敛，后续补 TOC 分节管线时会在错误地基上叠加复杂度。

---

### [High] 实现架构与最新设计文档严重偏离（TOC 驱动分节管线未落地）

- **位置**: `docs/plans/toc-driven-pipeline-design.md:41-80` 与 `kzocr/engine/run.py`、`kzocr/` 包结构（`ls kzocr` 仅含 adapter/cli/config/engine/export_zai/khub/modelscope_pool）
- **描述**: 最新设计定义了五阶段 TOC 驱动管线：`analyze_toc → process_sections（并行）→ section_postproc（DeepSeek）→ split_subsections → integrate_book`，并规划了 `toc_analyzer.py / section_ocr.py / section_postproc.py / subsec_splitter.py / book_integrator.py` 等组件。实际代码库**完全不存在这些组件**：`kzocr` 下只有 `engine/run.py` 的单体 `run_engine`（整本 PDF → 一个 `BookResult`），没有任何 TOC 分析、分节并行、后处理、小节拆分、整合器。设计 §2.2 还明确要求把 `run.py:417-421` 的"取下一页顶部 15%"提取为共享 helper `crop_top15pct`、复用 `_merge_cross_page_breaks`，但 `run.py` 仍是内联实现、且行号已漂移（`run.py:416-421` 而非 `417-421`）。设计与实现的"架构契约"已断裂：设计的中心思想（按"节=病"切分、跨页衔接、并行、独立评审）在代码中没有任何承载。
- **建议修复**: 明确本轮范围——要么把设计降格为"远期规划"并更新文档以匹配现有单体管线，要么在 `docs/plans/` 之外建立可追踪的实施路线图，把五阶段组件补齐并与现有 `run_engine` 的 VLM/真实引擎两条路径对接。不要让"设计 v2"与"代码现状"长期并行而无人裁决。

### [High] 真实引擎路径丢弃全部结构化数据，zai 工作台失去展示内容

- **位置**: `kzocr/engine/run.py:114-119`（`_run_real` 构造 `BookResult` 仅填 `book_code/title/engine_label/final_markdown`）
- **描述**: `_run_real` 从 kimi 的 `BookPipeline` 取出 `final_markdown` 后，构造的 `BookResult` 中 `pages/paragraphs/lines/proofreads/herb_patterns/meridian_patterns/context_patterns/terms/formulas` **全部为空**（`types.py:133-138` 默认值）。而 mock 路径（`mock.py`）填充了完整的逐行 `engine_texts/consensus/proofreads` 与三大范式库。当后续 `push_book_to_zai`（`to_zai_prisma.py:108-149`）写入 zai 库时，真实引擎产出的书会落得 `Page/Line/Proofread/Pattern/Term/Formula` 全为 0 行——这正是 zai 人工校对台赖以工作的逐行共识/校对记录/范式库。即：真实引擎路径下，整条"写库→人工校对→导出"链路的内容被掏空，流水线退化为仅存一段 `final_markdown` 文本。
- **建议修复**: 在 `_run_real` 中将 kimi `BookPipeline` 的真实结构化输出（逐行 `engine_texts`、proofread、范式库）映射进 `BookResult`，复用 `types.py` 已有字段；若 kimi 当前版本只给 Markdown，应在 `run.py` 增加"从 Markdown 重建 pages/lines"的解析层，保证真实路径与 mock 路径产出同构的 `BookResult`。并在测试中断言真实路径的 `len(book.pages) > 0`。

### [Medium] 配置存在两套真相：模块级单例 `config` 与 `load_config()` 并存

- **位置**: `kzocr/config.py:89`（`config = load_config()` 模块级单例）；`kzocr/cli.py:29,44,72`（`load_config()` 重新构造）；`kzocr/adapter/to_zai_prisma.py:21` 与 `kzocr/khub/client.py:15`（`from .. import config` 用单例 `config.config`）
- **描述**: 配置访问方式不统一：CLI 每次命令用 `load_config()` 重新读取环境变量构造新 `Config`；而 adapter 与 khub 客户端通过 `from .. import config` 使用的是**模块导入时**就已构造好的单例 `config.config`。两套来源在单进程内可能不一致（单例在 import 时刻定格，运行期改环境/测试 patch `os.environ` 不会反映到单例）。更严重的是，CLI 把 `cfg` 显式传给 `run_engine`，但 `khub/client.push_document` 在 `base_url` 参数为 `None` 时回退到单例 `config.config.khub_base_url`——同一进程里其实是两棵配置树。这是典型的"全局可变配置单例"反模式。
- **建议修复**: 收敛为单一真相——删除模块级 `config = load_config()` 单例，所有模块通过显式传入的 `Config` 或统一的 `get_config()` 工厂访问；adapter/khub 不应再 `from .. import config`。或在 `Config` 上提供 `@classmethod from_env()` 作为唯一入口，各调用方统一调用，避免 import-time 副作用。

### [Medium] `modelscope_pool.py` 是未被管线集成的孤儿模块，且与 run.py 形成两套并行 LLM 集成策略

- **位置**: `kzocr/modelscope_pool.py:1-356`；对比 `kzocr/engine/run.py:155-196`（`_init_vlm_adapter`）
- **描述**: `modelscope_pool.py` 定义了约 350 行的 `CloudLLMPool`（聚合 8 个 provider、跨 provider 故障转移），但全代码库中**没有任何管线代码 import 它**（仅在 `CHANGELOG.md` 与同轮 data-security 评审中提及）。实际 OCR/VLM 调用走的是 `run.py` 里直接 `from tcm_ocr.core.engines.sensenova_adapter import SenseNovaAdapter` / `PaddleOCRVl16Adapter` 的**具体类**，完全绕过 `CloudLLMPool`。结果是仓库里存在两套 LLM 集成策略（一个抽象的 `CloudLLMPool`，一个具体的 `tcm_ocr.*.adapter`），只有后者被接线。孤儿模块带来维护面与认知负担，且其内硬编码了多个 provider 的 `api_key_fallback`（如 `modelscope_pool.py:101,141`，真实密钥串），密钥问题已由同轮安全评审专文覆盖，此处仅从架构角度指出"未接线的大模块 + 重复抽象"应清理或明确其定位。
- **建议修复**: 二选一——(a) 若 `CloudLLMPool` 是未来 VLM/后处理统一后端，应在 `run.py`/`_init_vlm_adapter` 中实际接入并删除散落的具体 import；(b) 若已被 `tcm_ocr.core.engines.*` 取代，应移出核心包（或标注 deprecated 并加测试说明其不在线）。不要让它以"有效代码"形态继续存在却无人调用。

### [Medium] 多书模式下全局范式库/术语/方剂被推送互相清空

- **位置**: `kzocr/adapter/to_zai_prisma.py:90-92`（`DELETE FROM FormulaIngredient / Formula / Pattern / Term` 全量清空）
- **描述**: 适配器在每次 `push_book_to_zai` 时，对 `Pattern/Term/Formula/FormulaIngredient` 四张**无 `bookCode` 列**的表执行全量 `DELETE`（注释称"zai 单书模式"）。这意味着：推送第二本书会**整体抹掉第一本书沉淀的范式库/术语/方剂**。这与设计反复强调的"三大永久范式库（本批沉淀）"语义相冲突——范式库本应跨书累积、越沉淀越厚，却因缺少 `bookCode` 关联键而被整体清空。在 kHUB 多书入库场景下，这是数据正确性问题。
- **建议修复**: 给 `Pattern/Term/Formula/FormulaIngredient` 增加 `bookCode`（或 `sourceBook`）列，删除时按 `bookCode` 精确清除；或改为 upsert（按唯一键幂等写入），保留跨书累积的范式库。至少应在文档/注释中明确"当前为单书模式，多书会互相覆盖"。

### [Medium] 导出 Markdown 实现不一致：DB 导出缺 术语/方剂/语境范式

- **位置**: `kzocr/export_zai.py:48-63`（DB 导出仅 herb+meridian）；对比 `kzocr/adapter/to_zai_prisma.py:221-271`（`export_markdown` 含 context/terms/formulas）
- **描述**: 存在两份 Markdown 渲染逻辑，且内容不对齐。CLI 的 `export` 子命令走 `export_zai.export_book_markdown`（`cli.py:47`），它从 DB 读 `Pattern` 表时**只输出 `libType=1`（药名）与 `libType=2`（经络穴位）**，完全丢弃 `context_patterns`、术语（`Term`）、方剂（`Formula`/`FormulaIngredient`）。而 `to_zai_prisma.export_markdown`（从内存 `BookResult` 渲染）则包含"三大永久范式库 + 术语 + 方剂"。最终用户通过 `kzocr export` 拿到的文档，缺少设计承诺的术语与方剂沉淀——同一本书、两种渲染、两种真相。
- **建议修复**: 收敛为单一 Markdown 渲染器（建议以 `export_zai` 的 DB 读取为权威，因为终校以 DB 为准），补齐 `context_patterns/Term/Formula` 的输出；或让 `to_zai_prisma.export_markdown` 与 `export_zai` 共用同一渲染函数，消除分叉。

### [Medium] VLM 引擎标签硬编码为 "PaddleOCR-VL-1.6"，即使实际用的是 SenseNova

- **位置**: `kzocr/engine/run.py:27`（`VLM_ENGINE_LABEL = "PaddleOCR-VL-1.6"`）；`kzocr/engine/run.py:442`（`engine_label=VLM_ENGINE_LABEL` 无条件赋值）
- **描述**: `_run_vlm` 通过 `_init_vlm_adapter`（`run.py:155-196`）按 `vlm_engine` 配置与密钥情况，可能实际选用 **SenseNova**（`sensenova_adapter`）或 **PaddleOCR-VL-1.6**（`paddleocr_vl16_adapter`）。但无论实际选了哪个，`BookResult.engine_label` 都被硬编码成 `"PaddleOCR-VL-1.6"`（`run.py:442`）。随后 adapter 把该 label 写进 `Book.source` 与 `Line.auditSource`（`to_zai_prisma.py:101,135`），导致 zai 库与导出文档里的"引擎 lineage"在 SenseNova 路径下是**错误**的。lineage 错误会直接误导后续人工校对与质量统计。
- **建议修复**: 让适配器暴露 `engine_name`/`label` 属性，`_run_vlm` 用实际选定适配器的标签填充 `BookResult.engine_label`，而非写死常量。

### [Medium] kHUB 推送失败无法被 smoke 优雅跳过（抛 `URLError` 而非 `RuntimeError`）

- **位置**: `kzocr/khub/client.py:43`（`urllib.request.urlopen` 未捕获网络错误）；`kzocr/cli.py:94`（`except RuntimeError` 只接 `RuntimeError`）；`kzocr/cli.py:57-68`（`cmd_push` 无异常处理）
- **描述**: `push_document` 使用 `urllib.request.urlopen`，网络不可达/超时会抛出 `urllib.error.URLError`，但函数内部未捕获。`cmd_smoke` 的推送段用 `except RuntimeError` 兜底（注释意图是"推送跳过"），然而 `URLError` 不是 `RuntimeError` 的子类，故 kHUB 未启动时 smoke 会直接崩溃而非优雅跳过。同理 `cmd_push` 对网络错误无任何处理，用户一次打错 URL 就得到未捕获栈。这与设计"smoke 不应因 kHUB 未起而失败"的预期不符。
- **建议修复**: 在 `khub/client.py` 中将 `urlopen` 的 `URLError/HTTPError/timeout` 统一包装为 `KHUBError(RuntimeError)` 或项目自定义异常；`cmd_smoke` 捕获该异常以跳过推送；`cmd_push` 也应捕获并给出友好报错。

### [Medium] 引擎层直接耦合具体子模块类，缺少引擎接口/注册表，扩展新 OCR 引擎需改 `run.py`

- **位置**: `kzocr/engine/run.py:103`（`from tcm_ocr.pipeline.book_pipeline import BookPipeline`）；`kzocr/engine/run.py:175,189`（`from tcm_ocr.core.engines... import SenseNovaAdapter / PaddleOCRVl16Adapter`）
- **描述**: `types.py:1-5` 的注释明确"KZOCR 编排层不依赖任何具体引擎实现，便于 mock 与真实引擎切换"。但 `run.py` 仍用 `sys.path.insert` 注入 `kimi_engine_dir` 后**直接 import 具体类**，并在 `_run_real`/`_init_vlm_adapter` 里用 if/分支硬编码引擎选择。新增一个 OCR 引擎（如设计提到的硅基流动/DeepSeek OCR）必须改动 `run.py` 两处函数。引擎选择逻辑（mock / vlm / real、以及 VLM 内 SenseNova↔Paddle 降级）与"把一页图交给某引擎识别"的胶水代码纠缠在一起，没有可插拔的 `Engine` 协议或注册表。
- **建议修复**: 抽象出稳定的 `OCREngine` 协议（如 `recognize(pdf_page|pages) -> list[str]` + `name` 属性），用注册表/工厂（`ENGINES = {"kimi": ..., "sensenova": ..., "paddle_vl16": ...}`）管理；`run_engine` 只负责按配置选注册项，具体适配器构造与降级链下沉到各引擎实现。这样"新增引擎"收敛为"新增一个实现 + 一行注册"。

### [Medium] 多处硬编码开发者绝对路径作为默认值，损害可移植性

- **位置**: `kzocr/config.py:45-46`（默认 `/home/keen/kimi_agent_ocr/tcm_ocr_system_v1.1`、`/home/keen/tcm_ocr_zai`）；`kzocr/engine/run.py:73-74`（默认 `/home/keen/kzocr_engine_lib`、`/home/keen/kzocr_engine_lib/results`）
- **描述**: 配置默认值与 `_build_engine_config` 的引擎 lib/output 目录直接写死 `/home/keen/...` 某开发机的家目录。未设环境变量时，任何其它机器/CI 会落到不存在的路径。这属于把个人开发环境编译进"框架默认值"，与"项目应可在任意环境运行"的架构目标冲突。
- **建议修复**: 默认值改为相对路径或明确"未配置即报错"；或默认指向 `<repo>/.cache/engine_lib` 之类随仓库可创建的位置。至少在校验处（如 `run.py:97` 的目录不存在检查）给出清晰的错误而不是静默落到错误路径。

### [Low] `config.py` 中 `khub_db` 取值逻辑冗余且 `~` 不展开

- **位置**: `kzocr/config.py:48-51`
- **描述**: `khub_db = os.environ.get("KHUB_DB", os.path.expanduser(os.environ.get("KHUB_DB", "~/.khub/khub.db")))` 对 `KHUB_DB` 读取了两次：若环境变量已设置，返回的是**未展开**的原始值（含 `~` 不展开）；若未设置，返回的才是 `expanduser("~/.khub/khub.db")`。逻辑冗余且行为不一致（设置了 `~` 反而失效）。
- **建议修复**: 改为 `os.path.expanduser(os.environ.get("KHUB_DB", "~/.khub/khub.db"))` 单行即可，保证两种情况都展开。

### [Low] mock 数据 `author` 字段带前导空格

- **位置**: `kzocr/engine/mock.py:107`（`author=" mock 引擎"`）
- **描述**: mock 书的 `author` 为 `" mock 引擎"`（前导空格），会被一路写入 `Book.author` 与导出文档。虽是演示数据，但会污染导出 Markdown 的表头与任何按 author 精确匹配的逻辑。
- **建议修复**: 改为 `"mock 引擎"`（去掉前导空格）。

### [Low] `run.py` 存在死代码：`_PAGE_END_INCOMPLETE` 未使用、`cur_lines` 重复赋值

- **位置**: `kzocr/engine/run.py:301-303`（`_PAGE_END_INCOMPLETE` 定义后未引用）；`kzocr/engine/run.py:323` 与 `:331`（`cur_lines = cur.split("\n")` 重复赋值，第一次结果在 `:325-327` 用后即被覆盖）
- **描述**: `_PAGE_END_INCOMPLETE` 编译了正则却从未参与跨页合并逻辑；`_merge_cross_page_breaks` 内 `cur_lines` 在 323 行与 331 行被赋值两次，第一次赋值的值仅用于 325 行的 `last_line` 计算，紧接着 331 行又重算一次，可读性差且易误导维护者以为两处有不同语义。
- **建议修复**: 删除未使用的 `_PAGE_END_INCOMPLETE`；合并重复赋值，只保留一次 `cur_lines = cur.split("\n")`。

### [Low] `final_markdown` 富文本（含三大范式库）从未持久化，DB 导出拿不到

- **位置**: `kzocr/engine/types.py:139`（`final_markdown` 字段）；`kzocr/adapter/to_zai_prisma.py:27-31`（`Book` 表 DDL 无 `final_markdown` 列）；`kzocr/engine/mock.py:149`（`book.final_markdown = _render_markdown(book)`）
- **描述**: `BookResult.final_markdown` 在 mock/VLM 路径被渲染成含"三大永久范式库"的富文本，但 `to_zai_prisma` 的 `Book` 表既无该列、INSERT 也不包含它，`final_markdown` 仅存在于内存 `BookResult` 中。下游导出走 DB（`export_zai`），因此 mock 精心渲染的范式库章节在经 DB 流转后丢失（仅 `export_markdown(book)` 内存版保留）。这解释了上一"导出不一致"问题的一部分根因。
- **建议修复**: 要么在 `Book` 表增加 `finalMarkdown` 列并落库，要么明确"DB 中的 Line 表是权威终校来源，final_markdown 仅作内存中间态"并在文档中说明，避免两路导出语义长期分叉。

### [Info] 测试覆盖缺口：真实路径与对外客户端基本无单测

- **位置**: `tests/test_pipeline.py`（仅 mock + adapter + 内存导出）；`tests/test_vlm.py`（仅 run_engine 路由与 `_run_vlm` mock 逻辑）；`kzocr/engine/run.py:94-119`（`_run_real`）、`kzocr/engine/run.py:155-196`（`_init_vlm_adapter` 的 SenseNova 分支）、`kzocr/export_zai.py`、`kzocr/khub/client.py` 均无测试
- **描述**: 当前测试覆盖了 mock 引擎、适配器写库、VLM 路由与 `_vlm_markdown_to_pages`，但：(1) `_run_real` 真实引擎路径（含上文 High 级"结构化数据丢失"问题）无任何断言；(2) `_init_vlm_adapter` 的 SenseNova 分支与降级链无测试；(3) `export_zai.export_book_markdown` 与 `khub/client.push_document/verify_in_khub` 零覆盖。高风险/高偏离的代码路径恰是测试盲区，意味着 [High]/[Medium] 级缺陷难以被回归捕获。
- **建议修复**: 至少为 `_run_real` 增加"结构化字段应被填充"的契约测试（可用 stub 替代真实 `BookPipeline`），为 `export_zai` 增加与 `to_zai_prisma.export_markdown` 输出一致性测试，为 `khub/client` 增加基于 `unittest.mock` 的 `urlopen` 桩测试（含网络失败分支）。

---

## 架构维度评分

| 维度 | 评分 | 说明 |
|------|------|------|
| 分层（Layering） | **B-** | `types` 归一化中间层 + engine/adapter/export/khub 的纵向分层方向正确、边界基本清晰；但引擎层用 `sys.path` 注入直接 import 子模块具体类，核心包对上游存在硬耦合。 |
| 接口与抽象（Interfaces/Abstraction） | **C+** | `BookResult` 数据契约稳定且设计良好；但缺少引擎（`OCREngine`）与下游抽象，引擎选择为 if/分支硬编码，新增引擎/下游需改核心文件。 |
| 可扩展性（Extensibility） | **C** | mock/real/VLM 三态可切换是优点；但无注册表/工厂，新 OCR 引擎、新 LLM provider（`CloudLLMPool` 未接线）、新下游均需侵入式修改；全局范式表无 `bookCode` 关联键，多书扩展有数据正确性风险。 |
| 数据流与一致性（Data Flow/Consistency） | **C** | 真实引擎路径丢弃逐行/范式结构化数据、两份 Markdown 渲染器内容分叉、`final_markdown` 不落库——导致"写库→校对→导出"链路在不同引擎下产出异构结果。 |
| 配置管理（Config） | **C-** | 模块级单例与 `load_config()` 双源并存、硬编码个人绝对路径默认值、`khub_db` 取值逻辑冗余，配置一致性偏弱。 |
| 与设计一致性（Alignment w/ Design） | **D** | 最新设计的核心（TOC 驱动分节五阶段管线）在代码中完全未实现，设计与实现已出现结构性偏离，需尽快裁决。 |

> 备注：关于 `modelscope_pool.py` 中硬编码 API 密钥与页面图像外发的安全问题，已由同轮 **data-security 评审**专文覆盖，本架构评审仅从"孤儿模块/重复抽象/未接线"角度指出其架构定位问题，避免重复。
