# KZOCR 统一 OCR 引擎架构方案（草案 v0.2 — Round 3 修订版）

> 状态：v0.2，依据 `docs/reviews/2026-07-09-round3/` 8 角色评审汇总修订。
> 关联：`docs/plans/toc-driven-pipeline-design.md`（TOC 分节设计）、`docs/reviews/2026-07-09-round2/summary.md`（H1–H8 整改已落地）。
> **变更记录（v0.1→v0.2）**：① 适配器以 `types.py` 为层间契约、返回 `PageResult`（原误用 `str`）；② 共识职责单点归 GlyphVerifier；③ 新增出站端点 allowlist + `is_mock` 阻断 publish（假数据不得标 PASS）；④ 无 GPU 下 consensus 硬约束 + VLM 资源预算（端口统一 `:8080`）；⑤ 字形 KB 进程内预载 + 繁简/异体归一 + 罕见字白名单 + 可选 `VisionRecheckAdapter`；⑥ HumanGate 触发补齐 `UNKNOWN` + 原图裁剪透传 + 闭环回填；⑦ 归档补 `Book.final_markdown` 列 + 小节→Line 外键 + 按 `book_type` 分流 + 方剂七类字段 + Outbox 跨库；⑧ 6 项假设全部收口（1/2/5 修订）。

## 0. 背景与目标

当前 KZOCR 的引擎对接散落在 `kzocr/engine/run.py` 的 `use_mock` / `use_vlm` / `use_real` 三路**硬编码分支**。随着 OCR 后端增多，这种硬编码会迅速腐化。本方案把"接一个引擎"和"用哪个引擎"彻底解耦，确立五条原则：

1. **统一**：所有后端实现同一适配器接口，各有**独立配置**与**使用说明**。
2. **可切换**：采用哪种引擎组合由"环境探测"驱动，**无需改代码、无需改流程**。
3. **质量门**：无论哪条链路，识别结果都必须**经得起字形校验**才算最终结论。
4. **人工兜底**：通不过字形校验的，强制推送**人工校对**，绝不静默放行错字。
5. **可归档**：校对后归档**全文 / 目录 / 按最小小节分割入库**；方剂书额外入**方剂库**。

## 1. 总体架构（分层）

```
                ┌──────────────────────────────────────────┐
   输入 PDF ──▶ │ 1. 预处理/分版 (fitz 渲染, 版心裁剪)      │
                └────────────────────┬─────────────────────┘
                                     │ 页面图像 (RGB numpy) + 原图裁剪(供校对/复核)
                                     ▼
                ┌──────────────────────────────────────────┐
                │ 2. 引擎路由层 EngineRouter               │
                │    - probe_environment() 探测硬件/网络/模型/密钥│
                │    - 选 1~N 适配器(single / consensus硬约束)│
                │    - 降级编排收口于此(各适配器只管自身重试)  │
                └────────────────────┬─────────────────────┘
                                     │ PageResult[] (多源 engine_texts + confidence)
                                     ▼
                ┌──────────────────────────────────────────┐
                │ 3. 字形校验层 GlyphVerifier (质量门)    │
                │    - 共识计算单点归属于此                  │
                │    - 逐字比对字形 KB(进程内预载)+置信度    │
                │    - PASS / UNKNOWN / FAIL / UNCERTAIN    │
                │    - FAIL/UNKNOWN 可选 VisionRecheck 回看原图│
                └──────────┬───────────────────┬────────────┘
                           │ PASS               │ FAIL/UNCERTAIN/UNKNOWN
                           ▼                    ▼
                ┌──────────────────┐  ┌──────────────────────────────┐
                │ 4a. 自动放行     │  │ 4b. 人工校对门 HumanGate     │
                │    (高置信已校验) │  │ 触发: FAIL/UNCERTAIN/UNKNOWN │
                │                   │  │ + 全失败 + require-human/mock │
                │                   │  │ → 推送 zai(带原图裁剪/多源)  │
                └────────┬─────────┘  └──────────────┬───────────────┘
                         │                            │ 人工校完(写 humanFinal)
                         └────────────┬───────────────┘
                                      ▼
                ┌──────────────────────────────────────────┐
                │ 5. 归档层 Archiver                       │
                │    - 全文 Book(final_markdown) + 目录 TOC│
                │    - 按 book_type 最小小节拆分(外键映射)  │
                │    - 方剂书 → 方剂库(七类字段) + Outbox同步│
                └──────────────────────────────────────────┘
```

每一层只依赖下一层的稳定接口；**层间唯一契约是 `kzocr/engine/types.py`**（`BookResult` / `PageResult` / `ParagraphResult` / `LineResult`），禁止把归一化结果弱化为 `str`。

## 2. 适配器规范（统一接口）

### 2.1 接口契约 `OCREngineAdapter`（基于 `types.py`）

所有后端实现同一协议，基类位于 `kzocr/engines/adapters/base.py`：

```python
@dataclass
class AdapterMeta:
    name: str                       # 唯一标识, 如 "paddleocr", "sensenova"
    kind: str                       # "local-nonvision" | "local-vision" | "cloud-vision"
    requires_gpu: bool
    requires_network: bool
    min_vram_gb: float = 0.0
    needs_api_key: bool = False
    default_enabled: bool = True
    # 安全: 云端适配器必须声明出站端点(供 allowlist 校验)
    egress_endpoints: list[str] = field(default_factory=list)  # 如 ["token.sensenova.cn"]

class OCREngineAdapter(Protocol):
    meta: AdapterMeta
    # 返回 PageResult(含按行 engine_texts/confidence), 而非裸 str
    def recognize_page(self, img: np.ndarray) -> PageResult: ...
    def recognize_pages(self, imgs: list[np.ndarray]) -> list[PageResult]: ...
    # 可选: 多页上下文(思考模式), 由 meta 声明 supports_context
```

- 适配器**不得**自行做跨引擎共识或静默降级到桩数据；降级编排在 EngineRouter。
- 任何回退/桩数据必须置 `BookResult.is_mock=True` 且 `glyph_verified` 不得为 `PASS`（与 H8 一致）。

### 2.2 适配器清单（按形态分组）

**(A) 本地非视觉 OCR（传统多架构，逐行/逐块）**

| 适配器 | 架构 | 现状 | 说明文档 |
|---|---|---|---|
| `PaddleOCRAdapter` | CNN+CTC (PP-OCRv4) | kimi `core.engines.paddleocr_adapter` | `docs/engines/paddleocr.md` |
| `TesseractAdapter` | LSTM CRNN | 待接入 | `docs/engines/tesseract.md` |
| `RapidOCRAdapter` | ONNX PP-OCR | 待接入（CPU vision_encoder 形状问题） | `docs/engines/rapidocr.md` |
| `UniRecAdapter` | 统一识别 | 待评估（CPU ONNX 崩溃风险） | `docs/engines/unirec.md` |

**(B) 本地视觉 OCR（本地 VLM，整页）**

| 适配器 | 后端 | 现状 | 说明文档 |
|---|---|---|---|
| `PaddleOCRVl16Adapter` | llama-server + GGUF | 已接入（默认禁用） | `docs/engines/paddleocr_vl16.md` |
| `ShizhenGPT7BVLAdapter` | llama-server | 已接入（默认禁用） | `docs/engines/shizhen_gpt_vl.md` |

**(C) 云端视觉 OCR（云端 VLM，整页）**

| 适配器 | 端点 | 现状 | 说明文档 |
|---|---|---|---|
| `SenseNovaAdapter` | token.sensenova.cn | 已接入 | `docs/engines/sensenova.md` |
| `ModelScopeVisionAdapter` | modelscope API | 已接入（modelscope_pool） | `docs/engines/modelscope.md` |
| `OfoxVisionAdapter` | ofox.io | 待接入（网络待验证） | `docs/engines/ofox.md` |
| `DeepSeekVisionAdapter` | api.deepseek.com | 待评估（是否视觉模型） | `docs/engines/deepseek_vision.md` |

### 2.3 每个适配器必须自带

1. **适配器模块** `kzocr/engines/adapters/<name>.py`（返回 `PageResult`）。
2. **集中配置**：在统一 `Config` 下挂 `Config.engines.<name>` 命名空间字段（host/port/key/model/timeout/enable）；加载期做 schema 校验 + 默认值合并。**不**采用每适配器独立 `*.toml`（避免双轨/审计困难）—— 10 份说明文档靠 CI「缺失即失败」+ `AdapterMeta` 自动生成兜底。
3. **使用说明** `docs/engines/<name>.md`：部署依赖、启动、配置项、资源占用、已知局限、**数据出境说明**（云端须标注发往哪个第三方域名，且该域名须在 allowlist）。

### 2.4 出站端点安全（新增，评审 B1）

- 所有出站端点（`vlm_host`、云端 `base_url`、khub `_validate_url`）统一经**域名 allowlist** 校验；未登记域名拒绝连接，防 SSRF/外泄。
- `allow_cloud_vision` 仅控制"是否允许云端出境"，**不**替代端点校验；二者叠加。

## 3. 路由与选择层 `EngineRouter`

`kzocr/engines/router.py`：

- `probe_environment()` 采集：GPU/显存、CPU 核数、本地 llama-server 端口（**统一 `:8080`**，修正原 `:18080` 配置冲突）是否监听、各云端 key 是否就绪、`KZOCR_ALLOW_CLOUD_VISION`。
- **策略 A 单引擎（默认）**：按 `KZOCR_OCR_PREFER` 或默认（无 GPU→本地非视觉 PaddleOCR / 有 GPU→本地视觉 PaddleOCR-VL-1.6 / 有 key 且允许云端→SenseNova）。
- **策略 B 多引擎共识（准，受硬约束）**：
  - **无 GPU 下**：consensus **仅允许含云端引擎且 N≤2**（两个本地 CPU 引擎并行纯内耗，禁止）。
  - 共识计算（逐行交叉比对 `engine_texts`）**单点归属 GlyphVerifier**，Router 不重复做。
- **降级编排收口于此**：某适配器失败→降级下一候选（如 SenseNova→PaddleOCR-VL）；各适配器只管自身可重试故障，不感知"降给谁"。全失败→HumanGate。
- **VLM 资源预算**（无 GPU 主路径）：单页 120s、单本 2h 总预算、并发≤1、超阈熔断；`KZOCR_MAX_PAGES` 同时作为内存闸与耗时闸（CPU 下 2000 页≈失控，须下调或分批）。
- CLI 每次运行重新 `probe`，无需改代码/重启即可随环境切换。

## 4. 字形校验层 `GlyphVerifier`（质量门，核心）

无论哪条链路，结果只有通过字形校验才算最终结论。

### 4.1 校验依据

- **字形知识库（进程内预载）**：KZOCR **自带精简字形白名单为事实源**（解耦 kimi `term_kb`/RuntimeDB，避免仓库强耦合），覆盖中医专业用字、药名、穴位、方剂名 + 通用汉字 Unicode 合法字形集 + **繁简/异体归一化表**。启动时一次性载入内存集合，**禁止逐字符查 `term_kb`/RuntimeDB**（性能 C4）。支持人工校对后**写回**白名单（闭环）。
- **置信度**：OCR 引擎字级置信度（`PaddleOCR` 有；VLM 退化为行级/整页级，靠共识补强）。
- **罕见字白名单**：明确登记罕见中医字，避免海量误判 `UNKNOWN` 淹掉校对台（领域 D1）。

### 4.2 校验逻辑（逐字，共识单点）

对每行每个识别字 `c`：

1. `c`（归一化后）∈ 白名单？→ `glyph_verified = PASS`，记 `auditSource`。
2. `c` ∉ 白名单但属合法 Unicode CJK 且领域词典可接纳 → `UNKNOWN`，进人工（**必须推送 HumanGate**，原草案遗漏此触发，见 E4）。
3. `c` 为明显错字（形似混淆 未/末、白木/白术）或低于置信阈值 → `FAIL`。
4. 多引擎一致且通过校验 → 提升 PASS；分歧 → `UNCERTAIN`。
5. **可选 `VisionRecheckAdapter`**：对 `FAIL`/`UNKNOWN` 行回看原图裁剪二次确认（评审推翻原"不加视觉模型"假设，兜底必须能回看原图）。

### 4.3 落库字段

- 复用现有 `Line.glyph_verified`：`PASS | UNKNOWN | FAIL | UNCERTAIN`
- `Line.audit_source`、`Line.engine_texts`、`Line.consensus`（已存在于 `types.py`）
- 桩/`is_mock` 数据**不得**标 `PASS`（安全 B3）。

## 5. 人工校对兜底 `HumanGate`

触发条件（任一即推送 zai 校对台）：

- 字形校验 `FAIL` / `UNCERTAIN` / **`UNKNOWN`**（第 4.2 定义，必须进触发列表）；
- 整页所有引擎均失败；用户显式 `--require-human`；或全程 mock。

推送内容（供人工高效定夺）：

- 每条待校行带 `glyph_verified` 状态、**原图裁剪**（形似字可对照）、各引擎 `engine_texts`、`consensus`、校验依据（如"未见于白名单"）；
- `is_mock` 强制透传落库，杜绝假古籍冒充已校验；
- **优先级分级 + 跨页同错字聚合**：同一错字多页出现一次校对全局生效；
- **闭环回填**：人工 `humanFinal` 回流字形白名单与术语库，避免重复劳动。

复用现有 `kzocr/adapter/to_zai_prisma.py` → zai `db/custom.db`，按 `bookCode` 隔离（H2 已落地）。

## 6. 校对后归档 `Archiver`

人工校完（或自动放行的高置信 PASS）后结构化归档：

1. **全文** → `Book` 表（**新增 `final_markdown` 列**，现有 DDL 缺此列，适配器须写入）。
2. **目录 TOC** → 由 TOC 分析抽取章节结构，存带 `bookCode` 外键的 `TocNode` 树。
3. **按最小小节分割入库（按 `book_type` 分流，非固定三级）**：
   - 方剂书：最小单元=方（`^\d+\.\d+`）；
   - 针灸书：最小单元=穴；
   - 本草书：最小单元=药；
   - 依 TOC 把 `final_markdown` 切成最小小节，落入 `Section/Paragraph/Line`，**通过带 `bookCode` 外键的 `SectionLine` 表建立小节→Line 归属映射**，杜绝孤儿行。
4. **方剂书 → 方剂库**：
   - zai `Formula` + `FormulaIngredient` 表**结构化七类字段**（方名/组成/剂量/用法/功用/主治/方解，原 schema 仅落方名+组成，失真）；
   - **跨库一致性**用 **Outbox + 最终一致** 模型：zai 为权威缓存，khub 方剂系统（当前代码不存在，不可假定）为可选单向 Outbox 同步，非双写；带同步键 + 幂等 + 重试。
5. **幂等保留人工成果**：归档重跑用"按小节/行 upsert"而非 `DELETE WHERE bookCode` 整书清空，避免抹掉 `Proofread`/`humanFinal`。

> 与现有 schema 关系：zai 现有 `Book/Page/Paragraph/Line/Proofread/Pattern/Term/Formula/FormulaIngredient` 覆盖部分；需**新增** `final_markdown` 列、`TocNode`、`SectionLine`，并补全方剂七类字段。

## 7. 实施路线（分阶段，复用现有代码）

- **阶段 0（本方案）**：写方案 + Round 3 多角色评审 + 落地 `docs/`。✅
- **阶段 1 适配器注册表**：以 `types.py` 为契约；适配器返回 `PageResult`；把 `run.py` 200+ 行共享逻辑（渲染/裁剪/后处理/跨页合并）下沉 `kzocr/engines/_common.py`；`registry.py` 自动发现；降级编排收口 EngineRouter；出站端点 allowlist；统一指标（latency/success/fail/fallback）。
- **阶段 2 路由层**：`probe_environment()` + single/consensus 硬约束；VLM 资源预算（端口 `:8080`、单页/单本超时、并发≤1、熔断）。
- **阶段 3 字形校验**：`GlyphVerifier` + 进程内字形白名单（含繁简/异体归一、罕见字登记）；共识单点归属；可选 `VisionRecheckAdapter`。
- **阶段 4 人工兜底强化**：`HumanGate` 触发补齐 `UNKNOWN`；原图裁剪透传；`is_mock` 强制透传；优先级 + 跨页聚合 + 回填闭环。
- **阶段 5 归档层**：`final_markdown` 列；`TocNode`/`SectionLine` 外键；按 `book_type` 切分；方剂七类字段；Outbox 跨库同步；幂等保留人工。
- **阶段 6 补齐说明文档**：`docs/engines/*.md`（CI 缺失即失败）。

## 8. 设计决策收口（Round 3 已裁决）

| # | 议题 | 裁决 |
|---|---|---|
| 1 | 字形校验是否加视觉再识别 | **加可选 `VisionRecheckAdapter`**（兜底 FAIL/UNKNOWN 回看原图）；主体仍字典+置信度+共识。 |
| 2 | 最小小节定义 | **按 `book_type` 分流**（方剂=方、针灸=穴、本草=药），非固定 TOC 三级。 |
| 3 | 方剂库归属 | zai `Formula` 为权威缓存，khub 为**可选单向 Outbox 同步**（非双写）；khub 方剂系统当前不存在。 |
| 4 | consensus 成本 | 无 GPU 下 **硬约束**（仅含云端且 N≤2）；默认 single。 |
| 5 | 配置存放 | **集中 schema + `Config.engines.<name>` 命名空间 + 加载期校验**，反对每适配器 `*.toml`。 |
| 6 | 字形知识库来源 | **KZOCR 自带精简字形白名单为事实源**（解耦 kimi `term_kb`），支持写回。 |

## 9. 风险与回退（修订）

- **云端出境合规**：`allow_cloud_vision=false` 默认关；开启需明确许可 + 逐书/逐页同意 + 审计日志；consensus 多第三方并发出境合规不可证明，默认禁。
- **新增暴露面**：所有出站端点经域名 allowlist 校验（含 khub `_validate_url` 延伸至统一校验），`0.0.0.0` 误绑被拦截。
- **引擎崩溃/超时**：每适配器 try/except 自重试；失败降级下一候选；全失败→HumanGate；VLM 超预算熔断。
- **假数据兜底**：`use_mock`/降级必须标 `is_mock`+ERROR+**阻断 publish**，桩数据不得标 `glyph_verified=PASS`。
- **字形库不全误判 UNKNOWN**：白名单持续从人工校对回流闭环。
- **回退路径**：任何阶段出问题可整体回退 `use_mock` 桩数据跑通全链路（已有），保证永远可演示、不阻塞。
