# KZOCR 统一 OCR 引擎架构方案（v0.2 · round3 修订版）

> 状态：草案 v0.2，已吸收 round3（8 角色）评审意见与 `summary.md` 裁决，待定稿后进入实施。
> 关联：`docs/plans/toc-driven-pipeline-design.md`（TOC 分节管线设计）、`docs/reviews/2026-07-09-round2/summary.md`（H1–H8 整改，已落地）、`docs/reviews/2026-07-09-round3/`（本轮 8 角色初稿 + summary）。

## 0. 背景与目标

当前 KZOCR 的引擎对接散落在 `kzocr/engine/run.py` 的 `use_mock` / `use_vlm` / `use_real` 三路**硬编码分支**里。随着要接入的 OCR 后端越来越多，这种硬编码会迅速腐化，且每次换环境/换引擎都要改核心代码。

本方案把"接一个引擎"和"用哪个引擎"彻底解耦，确立五条原则：

1. **统一**：所有 OCR 后端（不论架构、不论部署形态）都实现同一适配器接口，各有**独立配置文件**与**使用说明**。
2. **可切换**：最终采用哪种引擎组合，由"环境探测"驱动，可随时随环境变化调整，**无需改代码、无需改流程**。
3. **质量门**：无论走哪条链路，识别结果都必须**经得起字形校验**才算最终结论。
4. **人工兜底**：凡字形校验未放行（含 `UNKNOWN`）的，强制推送**人工校对**，绝不静默放行错字。
5. **可归档**：校对（人工或自动放行）后，归档**全文 / 目录 / 按最小小节分割入库**；方剂书额外入**方剂库**。

### 进入实现前的 5 道硬门槛（round3 决议）

未闭合前不得铺开 10 个适配器：

- **H0-A 契约冻结**：层间传递统一用 `kzocr/engine/types.py` 的 `BookResult/PageResult/ParagraphResult/LineResult`，适配器返回**结构化**而非裸 `str`。
- **H0-B 共享逻辑下沉**：把 `run.py` 约 200+ 行跨引擎逻辑（渲染/裁剪/后处理/跨页合并/Markdown↔pages）下沉到 `kzocr/engines/_common.py`，registry 才真能减负。
- **H0-C 安全端点收敛**：所有出站端点（云端 `base_url`、本地 `vlm_host`）做 SSRF/域名 allowlist 校验；版心裁剪**不构成脱敏**。
- **H0-D 目标 schema 对齐**：归档层唯一事实源是 zai 的规范 `schema.prisma`（含 `ContentNode`/`FormulaComposition`/`FinalDocumentRecord`），方案此前基于扁平子集属错配，须重写。
- **H0-E 性能预算**：无 GPU 下 consensus 不成立、VLM 需总超时/并发/熔断，写入架构而非事后补。

## 1. 总体架构（分层 + 唯一契约）

```
                ┌──────────────────────────────────────────────┐
   输入 PDF ──▶ │ 0. 预处理/分版 (fitz 渲染, 版心裁剪→仅压缩带宽) │
                └───────────────────────┬──────────────────────┘
                                       │ 页面图像 (RGB numpy) + bbox
                                       ▼
                ┌──────────────────────────────────────────────┐
                │ 1. 引擎路由层 EngineRouter               │
                │    - probe_environment() → 可注入 ProbleResult │
                │    - select_adapters(probe,strategy) 纯函数   │
                │    - 降级链收口于此(逐候选尝试/捕获/降级)      │
                └───────────────────────┬──────────────────────┘
                                       │ AdapterPageResult[] (含 confidence/char_conf)
                                       ▼
                ┌──────────────────────────────────────────────┐
                │ 2. 字形校验层 GlyphVerifier (质量门)       │
                │    - normalize(繁→简/异体→正体)            │
                │    - 逐字比进程内字形白名单+形似混淆集        │
                │    - glyph_status: PASS|RARE|UNKNOWN|FAIL|UNCERTAIN │
                │    - 可选 VisionRecheckAdapter 回看裁剪图(仅本地)│
                └──────────┬───────────────────┬──────────────┘
                           │ 放行                 │ 未放行
                           ▼                      ▼
                ┌──────────────────┐  ┌──────────────────────────────┐
                │ 3a. 自动放行     │  │ 3b. 人工校对门 HumanGate     │
                │ (高置信+校验PASS) │  │ 触发: FAIL/UNKNOWN/UNCERTAIN │
                │                   │  │ → 推送 zai(带原图裁剪+severity)│
                └────────┬─────────┘  └──────────────┬───────────────┘
                         │                            │ 人工校完
                         └────────────┬───────────────┘
                                      ▼
                ┌──────────────────────────────────────────────┐
                │ 4. 归档层 Archiver (目标=规范 schema.prisma) │
                │    - 全文 FinalDocumentRecord(sha256)        │
                │    - 目录 ContentNode 树(挂载既有 Line.id)   │
                │    - 最小小节切分(经 contentNodeId,禁重建Line) │
                │    - 方剂书 → FormulaComposition(含毒性告警)  │
                │    - 闭环回填 term_kb/khub(异步可选)         │
                └──────────────────────────────────────────────┘
```

**层间唯一契约 = `kzocr/engine/types.py`**（`BookResult/PageResult/ParagraphResult/LineResult`）。所有层只认这套 dataclass，不传裸文本。`BookResult.is_mock` 强制透传，归档/推送在 `is_mock=True` 时阻断 publish（呼应 round2 H8「假古籍」事件）。

## 2. 适配器规范（统一接口 + 结构化返回）

### 2.1 接口契约

`kzocr/engines/adapters/base.py`：

```python
from dataclasses import dataclass
from typing import Literal

@dataclass
class AdapterMeta:
    name: str                 # 唯一标识, 如 "paddleocr"
    label: str                # 对外 engine_label, 如 "PaddleOCR-VL-1.6"
    kind: Literal["local-nonvision", "local-vision", "cloud-vision"]
    requires_gpu: bool = False
    requires_network: bool = False
    min_vram_gb: float = 0.0
    needs_api_key: bool = False
    default_enabled: bool = True
    supports_context: bool = False     # 是否支持多页上下文(思考模式)
    supports_confidence: bool = False  # 是否返回字级置信度

@dataclass
class AdapterPageResult:
    text: str
    confidence: float | None = None          # 页级/行级置信
    char_confidences: list[float] | None = None  # 字级(若有)
    crop_img: np.ndarray | None = None        # 版心裁剪图(供 recheck/UX)
    meta: dict = field(default_factory=dict)

class OCREngineAdapter(Protocol):
    meta: AdapterMeta
    def recognize_page(self, img: np.ndarray) -> AdapterPageResult: ...
    def recognize_pages(self, imgs: list[np.ndarray]) -> list[AdapterPageResult]: ...
    # 仅当 supports_context: recognize_with_context(pages, ctx) -> ...
```

> **关键修正（v0.2）**：适配器**不再返回裸 `str`**，必须返回 `AdapterPageResult`，否则 EngineRouter 拿不到置信度/多源信息，会重演 `run.py` 单体化。Vision 类适配器可在 `crop_img` 回填裁剪图，供字形 recheck 与校对台原图回溯。

### 2.2 两种粒度

- **页级 `OCREngineAdapter`**：输入单页图像，输出 `AdapterPageResult`。
- **书级 `BookLevelAdapter`**：输入 PDF、输出 `BookResult`（如 kimi `BookPipeline`）。由 Router 按 `kind` 区分调度，`BookPipeline` **只做薄封装(shim)**，不复制/重写 kimi 内部 `*_adapter`，`AdapterMeta` 由 KZOCR 侧注入。

### 2.3 适配器清单（按形态分组）

**(A) 本地非视觉 OCR（传统多架构，逐行/逐块）**

| 适配器 | 架构 | 现状 | 说明文档 |
|---|---|---|---|
| `PaddleOCRAdapter` | CNN+CTC (PP-OCRv4) | kimi `core.engines.paddleocr_adapter`（shim 封装） | `docs/engines/paddleocr.md` |
| `TesseractAdapter` | LSTM CRNN | 待接入 | `docs/engines/tesseract.md` |
| `RapidOCRAdapter` | ONNX PP-OCR (Det+Rec) | 待接入（CPU 下 vision_encoder 形状风险） | `docs/engines/rapidocr.md` |
| `UniRecAdapter` | 统一识别 | 待评估 | `docs/engines/unirec.md` |

**(B) 本地视觉 OCR（本地 VLM，整页）**

| 适配器 | 后端 | 现状 | 说明文档 |
|---|---|---|---|
| `PaddleOCRVl16Adapter` | llama-server + GGUF | 已接入（默认禁用，`auto_start` 须显绑 127.0.0.1） | `docs/engines/paddleocr_vl16.md` |
| `ShizhenGPT7BVLAdapter` | llama-server | 已接入（默认禁用） | `docs/engines/shizhen_gpt_vl.md` |

**(C) 云端视觉 OCR（云端 VLM，整页）**

| 适配器 | 端点 | 现状 | 说明文档 |
|---|---|---|---|
| `SenseNovaAdapter` | token.sensenova.cn | 已接入 | `docs/engines/sensenova.md` |
| `ModelScopeVisionAdapter` | modelscope API | 已接入（modelscope_pool，共 8 个 provider） | `docs/engines/modelscope.md` |
| `OfoxVisionAdapter` | ofox.io | 待接入（网络待验证） | `docs/engines/ofox.md` |
| `DeepSeekVisionAdapter` | api.deepseek.com | 待评估（是否视觉模型） | `docs/engines/deepseek_vision.md` |

### 2.4 每个适配器必须自带 + 安全约束

1. **适配器模块** `kzocr/engines/adapters/<name>.py`
2. **配置片段**：集中在 `Config.engines.<name>` 命名空间，默认值由 `AdapterMeta` 派生 + 加载期 schema 校验 + 默认值合并；**密钥只走环境变量/secret，绝不进 `.toml`**（防 round2 明文密钥事件）。每适配器 `.toml` 仅作可选覆盖层（host/port/model/timeout/enable）。
3. **使用说明** `docs/engines/<name>.md`，强制含 6 项标题（含**运营主体属地 / 是否跨境 / 数据出境说明**），CI 校验"注册即文档齐备"。
4. **端点安全**：所有出站 `base_url`/`vlm_host` 经 `khub/client.py:_validate_url` 扩展的校验——域名 allowlist（如 `*.sensenova.cn`、`api.deepseek.com`）、拒绝 RFC1918/回环外内网与明文 http 警告、建连前 DNS 复检防重绑定；`vlm_host` 仅本机/Unix socket。

## 3. 路由与选择层 `EngineRouter`（可随环境调整）

`kzocr/engines/router.py`：

- **`probe_environment()` 返回可注入的 `ProbeResult`**：GPU/显存、CPU 核数、本地 llama-server 端口是否监听、各云端 API key 是否就绪、`allow_cloud_vision`。**抽成纯函数 `select_adapters(probe, strategy, registry) -> list[AdapterMeta]`**，CI 用注入 `ProbeResult` 确定性验证分支（不真探测）。
- **策略**：
  - **默认 `single`**（硬约束，采纳假设 4）：按 `prefer` 指定或默认（无 GPU → 本地非视觉 PaddleOCR / 有 GPU → 本地视觉 PaddleOCR-VL-1.6 / 有 key 且允许云端 → SenseNova）。
  - **`consensus` 仅 opt-in**：且 **无 GPU 全本地 CPU 引擎 consensus → 拒绝启动并告警**（纯内耗）；仅当含云端视觉引擎时允许 consensus 且 **N≤2**。
  - 单引擎模式须以 `UNKNOWN`/低置信度作补充触发，避免系统性一致错误漏放（呼应领域 I6）。
- **降级链收口到 Router**：各适配器只管自身可重试故障（单页超时），不感知降级目标；Router 持 `prefer` 候选 + 探测，逐个尝试/捕获/降级，全失败 → HumanGate。
- **性能预算（写入架构，H0-E）**：单页超时 VLM 120s / SenseNova 90s；`KZOCR_TOTAL_TIMEOUT=7200s` wall-clock 总预算，到点停后续页、已识别页归档、未识别页推 HumanGate；`KZOCR_MAX_PAGES=500`（时间+内存双闸）；`KZOCR_MAX_CONCURRENCY=1`（含云端≤2），与 llama-server `--parallel` 对齐；每页 `KZOCR_PAGE_RETRIES=2` + 退避，同引擎连续 2 次超时熔断本剩余页转 UNCERTAIN/HumanGate，**禁止静默丢页**。
- **统一可观测性**：`BaseAdapter` 结构化日志前缀 `[engine=<name>]` + 指标 `latency/success/fail/chars/fallback_count`；Router 写 `engine_path: ["sensenova"(fail)→"paddleocr_vl16"(ok)]` 到 `BookResult`。
- **热调整**：CLI 每次运行重新 probe，环境变化（起 llama-server、补 key）即切换，无需改代码、无需重启。
- **统一 llama-server 端口真相源**：`auto_start` 前用配置端口探测（解决 config `18080` vs 实际 `:8080` 不一致）；可用内存 < 8GiB 时禁止 `auto_start`。

## 4. 字形校验层 `GlyphVerifier`（质量门，核心）

无论哪条链路，结果只有在字形校验放行后才算最终结论。

### 4.1 校验依据（进程内镜像，H0-A/性能）

- **KZOCR 内置精简字形白名单为事实源**（采纳假设 6）：启动时一次性进程内镜像——中医字形/异体/繁简映射 + 预计算**形似混淆集**（`confusion_set.json`：莪术↔我术、黄芩↔黄芪、半夏↔半下…）+ 罕见中医候选字表。禁止逐字符查外部库（性能 P6）。可选 `KZOCR_TERM_KB_PATH` 叠加并校验位于受控目录（防路径穿越）。kimi `term_kb`/RuntimeDB 仅可选增强。

### 4.2 校验逻辑（逐字，先 normalize）

1. `normalize(c)`：繁→简 + 异体→正体（`variant_map.json` 仅收明确等价项）+ 全角/旧字形归正。
2. 归一后 `c` ∈ 白名单 → `glyph_status = PASS`，`auditSource=dictionary`。
3. 归一后不在已知集、但属合法 CJK Ext-A/B 且命中"中医候选字表" → **`RARE`（罕见但允许，不进人工队）**，`auditSource=rare_allowlist`（修正 I6 淹没问题）。
4. 命中形似混淆集（如 黄芩↔黄芪）→ `FAIL`，`auditSource=confusion`。
5. 其余未知 / 低于置信阈值 → `UNKNOWN`，**送人工**（见 §5）。
6. 多引擎共识下，多数引擎一致且 PASS → 提升；分歧 → `UNCERTAIN` → 送人工。
7. 可选 `VisionRecheckAdapter` 挂点：对 FAIL/UNKNOWN/UNCERTAIN 行**回看裁剪图**（仅限本地视觉引擎，避免云端放大出境面）。

### 4.3 落库字段（与 `types.py` 对齐，修正语义冲突）

- 新增 `Line.glyph_status: Literal[PASS|RARE|UNKNOWN|FAIL|UNCERTAIN]`（不占用现有文本语义列）。
- 保留 `Line.glyph_verified` 作"校验后文本"用途（与现有 `mock.py`/导出/落库/CLI 文本消费兼容），或显式迁移所有消费方——二者择一，方案定稿时冻结。
- 新增 `Line.glyph_verified_reason`；`Line.auditSource` 改回语义（dictionary/consensus/human/rare_allowlist/confusion），修正现有 `to_zai_prisma.py` 误把 `auditSource` 写成 `book.engine_label` 的 bug。

## 5. 人工校对兜底 `HumanGate`（强化）

触发条件（任一即推送 zai 校对台，**含 UNKNOWN**，修正 v0.1 漏放，呼应原则 4）：

- `glyph_status ∈ {FAIL, UNKNOWN, UNCERTAIN}`；
- 整页所有引擎均失败；
- 用户显式 `--require-human`，或全程 `use_mock`。

推送复用 `kzocr/adapter/to_zai_prisma.py` → zai 规范 `schema.prisma`，按 `bookCode` 隔离。每条待校行须带：

- `glyph_status` + `glyph_verified_reason` + 各引擎 `engine_texts`（含 `is_mock`/`source` 透传，绝不与真实结果混同）；
- **原图裁剪**（`crop_img` 或路径+bbox），满足"看原图"核心诉求；
- `severity` 优先级：`critical`=有毒药材/否定词、`warning`=FAIL/UNCERTAIN、`info`=require-human/mock，使校对台可排序；
- 错字聚合 + 批量校正：同字形 group-by，复用 `Term`/`HerbOCRPattern` 给候选，支持"一处校正全局套用"。

`Book` 表增 `is_mock`/`source` 列并映射 `BookResult.is_mock`；**`is_mock=True` 时归档/推送显 ERROR 且阻断 publish**。

## 6. 归档层 `Archiver`（目标 = 规范 `schema.prisma`）

> **关键修正（v0.2 / data_integrity C1）**：方案此前基于 `to_zai_prisma.py` 扁平子集，与规范 `schema.prisma` 在表名/列名/隔离键上严重不符（如"需新增 TOC/Section 表"实为已有 `ContentNode`、方剂应为 `FormulaComposition`）。**唯一事实源是规范 `schema.prisma`**，扁平子集收敛为"向规范 schema 的适配层"。

人工校完（或自动放行的高置信 PASS 内容）后：

1. **全文** → `FinalDocumentRecord(full_md, sha256)`（**非**不存在的 `Book.final_markdown`）。
2. **目录 TOC** → 写入 `ContentNode`（level/sequence/pageStart/pageEnd/source='toc'），替代"新增 TOC 表"错误假设。
3. **按最小小节分割入库（不重建 Line）**：复用既有 OCR `Line.id`，经 `Paragraph.contentNodeId` 挂到 `ContentNode`。最小小节 = "任意级别标题或方剂/穴位块界定的 Markdown 块"，粒度经 `min_section_level` **可配置**（按 `book_type` 选定：针灸最小单元是"穴"、本草是"药"、临床是"证/方"），默认候选仍为 TOC 标题块。**严禁从 `final_markdown` 重切生成新 Line**（会丢失 bbox 原图回溯）。
4. **方剂书 → 方剂库**：以规范 `FormulaComposition`（含 alias/root/referenced/crossPage）为准，补齐七类核心字段（usage/gongyong/zhuzhi/fangjie/fields_json…），剂量保留原串（`各15`/`等分`/`适量`）。**主链只写 zai，khub 同步异步可选、不阻塞**（采纳假设 3）。内置 `toxic_herbs.json` 打 `isToxic`，触发用量红线告警（细辛≤3g、附子须炮制）。
5. **闭环回填机制**：人工校正 → `CandidateSubmissionBatch` → term_kb / HerbOCRPattern / khub 术语·方剂库管线；khub 同步异步可选。
6. **幂等改 MERGE 人工层**：重跑仅 TRUNCATE 自动生成层，`humanFinalText`/`ProofreadRecord`/`KnowledgeAuditLog` 按 `lineId` 更新/追加，绝不整书清空人工成果（呼应 round2 H2）。
7. **生命周期收尾**：`BookRegistry.status='archived'` + `archivedAt`，写 `OCRProcessingLog(stage='archive', status='completed')`。

## 7. 实施路线（分阶段，复用现有代码）

- **阶段 0（本方案）**：写方案 + 多角色评审 + 定稿到 `docs/`。✅ 当前 v0.2
- **阶段 1 适配器注册表 + 共享逻辑下沉 + 接口对齐 + 安全收敛**：
  - 新建 `kzocr/engines/_common.py` 下沉 `run.py` 共享逻辑（渲染/裁剪/后处理/跨页合并/Markdown↔pages），`BaseAdapter` 默认复用（registry 能减负的前提）。
  - kimi 适配器仅做薄封装 shim；`BookPipeline` 作 `BookLevelAdapter`。
  - 端点 SSRF/域名 allowlist 收敛（扩展 `_validate_url`）。
  - 配置单一真相源 + 加载期 schema 校验；降级链收口 Router；`is_mock` 强制透传 + 阻断 publish。
- **阶段 2 路由层**：`probe_environment()` 可注入 + `select_adapters` 纯函数 + 性能预算/重试/熔断 + consensus 块级对齐。
- **阶段 3 字形校验**：`GlyphVerifier`（normalize + RARE 态 + 形似集 + 进程内白名单 + `UNKNOWN→HumanGate` + VisionRecheck 挂点）。
- **阶段 4 人工兜底强化**：`HumanGate`（UNKNOWN 触发、原图裁剪、severity、聚合批量、`glyph_verified_reason`）。
- **阶段 5 归档层**：落到规范 `schema.prisma`（全文/FinalDocumentRecord、ContentNode、最小小节挂载、FormulaComposition、毒性告警、MERGE 幂等、闭环回填）。先补最小 TOC 分析器（regex 标题级）作为前置。
- **阶段 6 说明文档补齐**：`docs/engines/*.md` 全量（CI 校验 6 必需标题含跨境说明）；适配器脚手架 `kzocr engine new <name>`；清理 `modelscope_pool.py` 残留密钥片段；测试交付物（`test_router`/`test_glyph_verifier`/`test_adapters_protocol` + `tests/engines/fakes.py` + `kzocr smoke --adapter fake` 无依赖端到端）。

## 8. 第 7 章 6 项假设的裁决（round3 决议）

| # | 假设 | 裁决 | 要点 |
|---|---|---|---|
| 1 | 字形校验以字典+置信度+共识为主，暂不加独立再识别视觉模型 | **采纳（默认不加），调整** | 协议层预留 `VisionRecheckAdapter`/`recheck(line,crop_img)` 挂点；对 FAIL/UNKNOWN/UNCERTAIN 回看裁剪图随行推送；recheck **仅限本地视觉引擎**（云端放大出境面） |
| 2 | 最小小节：TOC 三级标题 vs 更小 | **调整** | 不钉死，改可配置 + 按 `book_type` 选定 + 经 `contentNodeId` 挂载；严禁重切生成新 Line；默认候选 TOC 标题块 |
| 3 | 方剂库：写 zai 即可 vs 必须同步 khub | **调整** | 主链只写 zai 且用规范 `FormulaComposition`；khub 异步可选、不阻塞；跨库先补 `version`+`checksum`，单向 zai→khub |
| 4 | consensus 成本：无 GPU 默认仅 single | **采纳（提升为硬约束）** | `strategy.mode` 默认 `"single"`；无 GPU 全本地 consensus 拒绝启动；含云端时 N≤2；单引擎以 `UNKNOWN`/低置信补触发 |
| 5 | 配置：集中 config.py vs 每适配器 toml | **调整** | 集中 schema + `Config.engines.<name>` 命名空间 + 加载期校验/默认值合并；每适配器 toml 仅可选覆盖层；**密钥绝不进 toml** |
| 6 | 字形库：复用 kimi term_kb vs KZOCR 内置白名单 | **采纳（KZOCR 内置为事实源）** | 进程内镜像白名单+异体+混淆集；`term_kb` 仅可选增强；`KZOCR_TERM_KB_PATH` 须校验受控目录 |

## 9. 风险与回退（v0.2 增强）

- **五道硬门槛未闭合前不铺 10 适配器**：先冻结契约与假设裁决（阶段 0），再阶段 1 下沉+收敛。
- **云端出境合规**：`allow_cloud_vision=false` 默认关；版心裁剪**仅压缩带宽、不脱敏**；开启后 consensus 会让一页图像同时送多家云端，须逐书/逐页同意 + 出境审计日志，且密钥不落库。
- **引擎崩溃/超时**：每适配器包 try/except + 重试/退避；失败降级下一候选，全失败 → HumanGate；wall-clock 总预算到点停后续页转人工，**禁止静默丢页**。
- **字形库不全误判 UNKNOWN**：`RARE` 态放行罕见中医字；知识库持续从人工校对结果回流（term_kb/khub 闭环）。
- **mock/桩数据防重演"假古籍"（round2 H8）**：`is_mock` 强制透传，归档/推送在 `is_mock=True` 时显 ERROR 且**阻断 publish**；`Book` 表有 `is_mock` 列，校对员可区分演示与真实。
- **回退路径**：任何阶段出问题，可整体回退到 `use_mock` 桩跑通全链路（已有），保证系统永远可演示、不阻塞。
