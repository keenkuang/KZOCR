# KZOCR 统一 OCR 引擎架构方案（草案 v0.1）

> 状态：草案，待多角色评审（round3）与修订。
> 关联文档：`docs/plans/toc-driven-pipeline-design.md`（TOC 分节管线设计，本方案第 5/6 章复用其结论）、`docs/reviews/2026-07-09-round2/`（既有 8 角色评审，H1–H8 整改已落地）。

## 0. 背景与目标

当前 KZOCR 的引擎对接散落在 `kzocr/engine/run.py` 的 `use_mock` / `use_vlm` / `use_real` 三路**硬编码分支**里：VLM 直连写死在 `_run_vlm`，真实 kimi 路径写死在 `_run_real`。随着要接入的 OCR 后端越来越多（本地非视觉多架构、本地视觉、云端视觉），这种硬编码会迅速腐化，且每次换环境/换引擎都要改核心代码。

本方案把"接一个引擎"和"用哪个引擎"彻底解耦，确立五条原则：

1. **统一**：所有 OCR 后端（不论架构、不论部署形态）都实现同一适配器接口，各有**独立配置文件**与**使用说明**。
2. **可切换**：最终采用哪种引擎组合，由"环境探测"驱动，可随时随环境变化调整，**无需改代码、无需改流程**。
3. **质量门**：无论走哪条链路，识别结果都必须**经得起字形校验**才算最终结论。
4. **人工兜底**：实在通不过字形校验的，强制推送**人工校对**，绝不静默放行错字。
5. **可归档**：校对（人工或自动放行）后，归档**全文 / 目录 / 按最小小节分割入库**；方剂书额外入**方剂库**。

## 1. 总体架构（分层）

```
                ┌──────────────────────────────────────────┐
   输入 PDF ──▶ │ 1. 预处理/分版 (fitz 渲染, 版心裁剪)      │
                └────────────────────┬─────────────────────┘
                                     │ 页面图像 (RGB numpy)
                                     ▼
                ┌──────────────────────────────────────────┐
                │ 2. 引擎路由层 EngineRouter               │
                │    - 探测环境(硬件/网络/模型/密钥)          │
                │    - 选定 1~N 个适配器 (单引擎 or 共识)    │
                └────────────────────┬─────────────────────┘
                                     │ 候选文本
                                     ▼
                ┌──────────────────────────────────────────┐
                │ 3. 字形校验层 GlyphVerifier (质量门)       │
                │    - 逐字/逐行比对字形知识库 + 置信度        │
                │    - 通过 → glyphVerified = PASS            │
                │    - 不通过 → FAIL / UNCERTAIN / UNKNOWN   │
                └──────────┬───────────────────┬────────────┘
                           │ PASS               │ FAIL/UNCERTAIN/UNKNOWN
                           ▼                    ▼
                ┌──────────────────┐  ┌──────────────────────────────┐
                │ 4a. 自动放行     │  │ 4b. 人工校对门 HumanGate     │
                │    (高置信已校验) │  │     → 推送 zai 校对台          │
                └────────┬─────────┘  └──────────────┬───────────────┘
                         │                            │ 人工校完
                         └────────────┬───────────────┘
                                      ▼
                ┌──────────────────────────────────────────┐
                │ 5. 归档层 Archiver                       │
                │    - 全文 Book + 目录 TOC                │
                │    - 按最小小节拆分 Section/Para/Line     │
                │    - 方剂书 → 方剂库 Formula/Ingredient   │
                └──────────────────────────────────────────┘
```

每一层只依赖下一层的稳定接口，互不感知具体引擎。

## 2. 适配器规范（统一接口）

### 2.1 接口契约 `OCREngineAdapter`

所有后端实现同一协议，基类位于 `kzocr/engines/adapters/base.py`：

```python
from dataclasses import dataclass

@dataclass
class AdapterMeta:
    name: str                       # 唯一标识, 如 "paddleocr", "sensenova"
    kind: str                       # "local-nonvision" | "local-vision" | "cloud-vision"
    requires_gpu: bool
    requires_network: bool
    min_vram_gb: float = 0.0
    needs_api_key: bool = False
    default_enabled: bool = True

class OCREngineAdapter(Protocol):
    meta: AdapterMeta
    def recognize_page(self, img: np.ndarray) -> str: ...
    def recognize_pages(self, imgs: list[np.ndarray]) -> list[str]: ...
    # 可选: 多页上下文(思考模式), 由 meta.supports_context 声明
```

### 2.2 适配器清单（按形态分组）

**(A) 本地非视觉 OCR（传统多架构，逐行/逐块识别）**

| 适配器 | 架构 | 现状 | 说明文档 |
|---|---|---|---|
| `PaddleOCRAdapter` | CNN + CTC (PP-OCRv4) | 已存在于 kimi `core.engines.paddleocr_adapter` | `docs/engines/paddleocr.md` |
| `TesseractAdapter` | LSTM CRNN | 待接入 | `docs/engines/tesseract.md` |
| `RapidOCRAdapter` | ONNX PP-OCR (Det+Rec) | 待接入（注意 CPU 下 vision_encoder 形状问题） | `docs/engines/rapidocr.md` |
| `UniRecAdapter` | 统一识别 | 待评估（CPU ONNX 崩溃风险） | `docs/engines/unirec.md` |

**(B) 本地视觉 OCR（本地 VLM，整页推理）**

| 适配器 | 后端 | 现状 | 说明文档 |
|---|---|---|---|
| `PaddleOCRVl16Adapter` | llama-server + GGUF | 已接入（默认禁用） | `docs/engines/paddleocr_vl16.md` |
| `ShizhenGPT7BVLAdapter` | llama-server | 已接入（默认禁用） | `docs/engines/shizhen_gpt_vl.md` |

**(C) 云端视觉 OCR（云端 VLM，整页推理）**

| 适配器 | 端点 | 现状 | 说明文档 |
|---|---|---|---|
| `SenseNovaAdapter` | token.sensenova.cn | 已接入 | `docs/engines/sensenova.md` |
| `ModelScopeVisionAdapter` | modelscope API | 已接入（modelscope_pool） | `docs/engines/modelscope.md` |
| `OfoxVisionAdapter` | ofox.io | 待接入（网络待验证） | `docs/engines/ofox.md` |
| `DeepSeekVisionAdapter` | api.deepseek.com | 待评估（是否视觉模型） | `docs/engines/deepseek_vision.md` |

### 2.3 每个适配器必须自带

1. **适配器模块** `kzocr/engines/adapters/<name>.py`
2. **配置片段** —— 在统一 `Config` 下挂 `<name>_*` 字段（host/port/key/model/timeout/enable），或由专属 `kzocr/engines/adapters/<name>.toml` 提供默认值 + 环境变量覆盖。
3. **使用说明** `docs/engines/<name>.md`，至少含：部署依赖、启动方式、配置项、资源占用、已知局限、**数据出境说明**（云端须标注发往哪个第三方）。

## 3. 路由与选择层 `EngineRouter`（可随环境调整）

`kzocr/engines/router.py`：

- 启动时 `probe_environment()` 采集：GPU 是否可用 / 显存、CPU 核数、本地 llama-server 端口是否监听、各云端 API key 是否就绪、`KZOCR_ALLOW_CLOUD_VISION` 开关。
- 依**策略**从"已注册且可用的适配器"中挑选：
  - **策略 A 单引擎（快）**：按 `KZOCR_OCR_PREFER` 指定或默认（无 GPU → 本地非视觉 PaddleOCR / 有 GPU → 本地视觉 PaddleOCR-VL-1.6 / 有 key 且允许云端 → SenseNova）。
  - **策略 B 多引擎共识（准）**：同时跑 N 个适配器，逐行交叉比对（`engine_texts` 多源），不一致的行降为 UNCERTAIN 进人工校对。
- **热调整**：环境变化（如手动起 llama-server、补了 key）只需重跑 `probe` 即可切换——CLI 每次运行重新 probe，无需改代码、无需重启。

配置示例（`config.py` 或 `engines.toml`）：

```toml
[strategy]
mode = "consensus"            # single | consensus
prefer = ["paddleocr_vl16", "paddleocr", "sensenova"]
allow_cloud_vision = false
```

`EngineRouter` 替换掉现有 `run_engine()` 的三路硬编码分支，成为唯一入口。

## 4. 字形校验层 `GlyphVerifier`（质量门，核心）

无论哪条链路，结果只有在字形校验通过后才算最终结论。

### 4.1 校验依据

- **字形知识库**：基于现有 `term_kb` / RuntimeDB（中医专业用字、药名、穴位、方剂名）+ 通用汉字 Unicode 合法字形集。每个"已知字形"预存：标准字、异体字/繁简映射、所属领域。
- **置信度**：OCR 引擎自带的字级置信度（`PaddleOCR` 有；VLM 无字级置信度 → 退化为行级/整页级，靠交叉共识补强）。

### 4.2 校验逻辑（逐字）

对每行每个识别字 `c`：

1. `c` ∈ 已知字形集？→ 标记 `glyphVerified = PASS`，并记录 `auditSource`。
2. `c` ∉ 已知集但属合法 Unicode CJK 且领域词典可接纳（如新见药名）→ `UNKNOWN`，进入"待确认"。
3. `c` 为明显错字（形似混淆 未/末、已/己）、或低于置信阈值 → `FAIL`。
4. 多引擎共识下，多数引擎一致且通过字形校验 → 提升为 PASS；分歧 → UNCERTAIN。

### 4.3 落库字段（复用现有 schema）

- `Line.glyphVerified`：`PASS | UNKNOWN | FAIL | UNCERTAIN`
- `Line.auditSource`：通过哪类校验（dictionary / consensus / human）
- 已存在 `glyphVerified TEXT` 列直接使用。

> **设计假设（待评审确认）**：字形校验以"字典/知识库比对 + 置信度阈值 + 多引擎共识"为主，暂**不**引入独立"再识别"视觉模型（避免二次成本）。如需更强保证，可加可选 `VisionRecheckAdapter` 对 FAIL/UNKNOWN 行回看原图裁剪。

## 5. 人工校对兜底 `HumanGate`

触发条件（任一即推送 zai 校对台）：

- 字形校验 `FAIL` 或 `UNCERTAIN` 且多引擎仍不一致；
- 整页所有引擎均失败（如现有 `_run_vlm` 的兜底逻辑）；
- 用户显式 `--require-human`，或全程走 mock。

推送复用现有 `kzocr/adapter/to_zai_prisma.py` → zai `db/custom.db`，按 `bookCode` 隔离（H2 整改已落地）。每条待校行带 `glyphVerified` 状态、各引擎 `engine_texts`、`consensus`，方便人工在 zai 工作台对照原图与多源结果。

## 6. 校对后归档 `Archiver`

人工校完（或自动放行的高置信 PASS 内容）后，执行结构化归档：

1. **全文** → `Book` 表（`final_markdown` 汇总）。
2. **目录 TOC** → 由 TOC 分析抽取章节结构（复用 H5 `toc_analyzer` 设计），存 `Section` 树（新增表或在 `Book` 扩展）。
3. **按最小小节分割入库** → 依 TOC 把 `final_markdown` 切成最小小节，落入 `Section/Paragraph/Line`（已有表结构），实现"可检索到最小知识单元"。
4. **方剂书 → 方剂库** → 若书籍被标为方剂书（`Book.bookType` 或 TOC 命中方剂章节），抽取方剂名/组成/剂量，写入 `Formula` + `FormulaIngredient`（已有表 + 待建的"方剂库"独立库 / khub 方剂系统，见 khub 药房系统关联）。

> 与现有 schema 的关系：zai 现有 `Book/Page/Paragraph/Line/Proofread/Pattern/Term/Formula/FormulaIngredient` 已覆盖第 1/3/4 点的大部分；需**新增 TOC/Section 表**与"最小小节 → Line 归属"映射。

## 7. 实施路线（分阶段，复用现有代码）

- **阶段 0（本方案）**：写方案 + 多角色评审 + 落地到 `docs/`。✅ 当前
- **阶段 1 适配器注册表**：搬出 `run.py` 硬编码，建 `kzocr/engines/adapters/base.py` + `registry.py` + 各适配器模块（先接已存在的：PaddleOCR/kimi、PaddleOCR-VL-1.6、SenseNova、ModelScope）。
- **阶段 2 路由层**：`EngineRouter.probe_environment()` + single/consensus 策略，替换 `run_engine` 三路分支。
- **阶段 3 字形校验**：`GlyphVerifier` + 字形知识库（接 `term_kb`），落到 `Line.glyphVerified`。
- **阶段 4 人工兜底强化**：`HumanGate` 统一触发逻辑，接 zai。
- **阶段 5 归档层**：TOC 抽取 + 最小小节分割 + 方剂入库（含 khub 方剂库）。
- **阶段 6 补齐说明文档**：`docs/engines/*.md` 全量补齐。

## 8. 待评审确认的设计假设（关键）

1. **字形校验机制**：以"字典/知识库 + 置信度 + 多引擎共识"为主，暂不加独立再识别视觉模型（假设 2）。是否要更强保证？
2. **最小小节的定义**：以 TOC 三级标题为最小单元，还是更小（段落 / 方证）？影响切分粒度。
3. **方剂库归属**：写入 zai 的 `Formula` 表即可，还是必须同步到独立 khub 方剂系统？涉及跨库。
4. **consensus 模式成本**：多引擎并行对无 GPU 环境的 CPU/时间压力，是否默认仅 single 模式、consensus 作为可选？
5. **适配器配置存放**：集中于 `config.py` 字段 vs 每适配器独立 `*.toml`，倾向后者以减少核心配置膨胀。
6. **字形知识库来源**：直接复用 kimi 侧 `term_kb`/RuntimeDB，还是 KZOCR 内置一份精简字形白名单？避免与引擎仓库强耦合。

## 9. 风险与回退

- **云端出境合规**：`allow_cloud_vision = false` 默认关，开启需明确许可（已有开关）。
- **引擎崩溃/超时**：每个适配器包 try/except，失败则降级下一候选，全失败 → HumanGate。
- **字形库不全导致误判 UNKNOWN**：知识库持续从人工校对结果回流（见 khub / term_kb 闭环）。
- **回退路径**：任何阶段出问题，可整体回退到 `use_mock` 桩数据跑通全链路（已有），保证系统永远可演示、不阻塞。
