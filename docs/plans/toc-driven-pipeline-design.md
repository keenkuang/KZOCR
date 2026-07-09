# TOC 驱动分节 OCR 管线方案（修订 v2）

> 面向 AI 代理的工作者：使用 subagent-driven-development 逐任务实现。步骤用复选框 `- [ ]` 跟踪进度。

**目标：** 建立"先识目录 → 分节(病)并行 OCR → 分节 DeepSeek 后处理 → 分节按小节(方)拆分 → 全书整合"的 TCM 书籍 OCR 管线，输出结构化、可校对的全书。

**架构：** TOC 分析器提取"章=科 → 节=病 → 小节=方"三级结构 → 按"节(病)"并行 OCR（SenseNova 双页上下文）→ 每节独立 DeepSeek-V4-Flash 后处理 → 按 `数字.数字` 拆小节 → 按 TOC 顺序整合全书。每节独立存储，每节评审 ≥4 轮。

**技术栈：** Python 3.13 / requests（SenseNova REST）/ openai SDK（DeepSeek）/ concurrent.futures（并行）/ fitz（PDF 渲染）/ @dataclass（数据模型，统一不用 Pydantic）。

---

## 0. 领域前置知识（新人必读）

**TCM 书三级结构**（《秘方求真》为例）：
- **章 = 科**：如 `【肿瘤科秘验方】`（扫描件中常是竖排侧眉）
- **节 = 病**：如 `§26 治甲状腺瘤秘方`、`§27 治甲状腺囊肿秘方`（一个病下有多方）
- **小节 = 方**：如 `26.4 鳖甲消瘤方`、`26.5 甲瘤汤`（独立成方，有固定字段）

**9 类固定标识**（每个方剂可能出现，有哪类提哪类，无需凑齐）：
| 标识 | 含义 | 示例 |
|------|------|------|
| 来源 | 出处 | `来源：文琢之，《中国中医秘方大全》` |
| 组成 | 药材剂量 | `组成：玄参12克，牡蛎30克...` |
| 用法 | 煎服法 | `用法：每日1剂，水煎服` |
| 功用 | 功效 | `功用：软坚散结，行滞活血` |
| 方解 | 方义 | `方解：方中玄参...` |
| 主治 | 适应症 | `主治：各种良性肿瘤包块` |
| 加减 | 化裁 | `加减：痰多者可加川贝母10克` |
| 疗效 | 效果 | `疗效：治疗156例...` |
| 附记 | 备注 | `附记：本方...` |

**API 101：**
- **SenseNova `sensenova-6.7-flash-lite`**：云端多模态 OCR。免费用量（限时）。已验证 ~10s/页：送"当前页 + 下一页顶部15%"双图，只输出当前页（`reasoning_effort: none` 参数提速）。适配器已在 `kimi_agent_ocr/tcm_ocr_system_v1.1/tcm_ocr/core/engines/sensenova_adapter.py`，方法 `recognize_pages(imgs)` 支持多图。
- **DeepSeek `deepseek-v4-flash`**：云端 Chat 模型，1M 上下文，思考/非思考可控，500 req/5h 限速。用于**后处理**（清洗 OCR 错字、抽取方剂结构化）。需令牌桶限速 + 退避重试。

**WHY 分节(病)而非分页？** 方剂常跨页（如 `26.4 鳖甲消瘤方` 从页4 末跨到页5 首）。按页调用会丢失跨页衔接；按"节=病"切分，每节是自然 OCR 边界，且节间可并行、每节可独立评审。

---

## 1. 系统架构

```
PDF 输入
   │
   ▼
┌─────────────────────────────┐
│ 1. TOC 分析器 analyze_toc       │ ← 识别"科/病"结构 + 起止页
│    输出: list[SectionPlan]      │
└─────────────────────────────┘
   │  SectionPlan[]（节名 + 起止页，按 TOC 序）
   ▼
┌─────────────────────────────┐
│ 2. 并行节处理器 process_sections │ ← ThreadPoolExecutor(max_workers=4)
│    每节:                      │
│     2a. SenseNova 双页 OCR    │ ← 复用 recognze_pages + 合并跨页
│     2b. raw.md 落盘            │ ← output/<book>/sections/<slug>/
└─────────────────────────────┘
   │  各节 raw.md 完成（并行）
   ▼
┌─────────────────────────────┐
│ 3. DeepSeek 分节后处理        │ ← 串行 + 令牌桶（500req/5h）
│    每节:                      │
│     3a. 清洗 OCR 错字         │
│     3b. 抽取 formulas.json    │
│     3c. cleaned.md 落盘       │
└─────────────────────────────┘
   │  各节 cleaned.md + formulas.json
   ▼
┌─────────────────────────────┐
│ 4. 小节拆分器 split_subsections │ ← 按 ^\d+\.\d+ 切分（非"来源："）
│    输出: formula_<n>.md        │
└─────────────────────────────┘
   │  各节 formula_*.md
   ▼
┌─────────────────────────────┐
│ 5. 全书整合器 integrate_book    │ ← 按 SectionPlan.order 排序拼接
│    输出: book.md + book.json   │
└─────────────────────────────┘
```

---

## 2. 组件设计（复用优先）

### 2.0 公共：数据模型 `core/pipeline/toc_models.py`
```python
from dataclasses import dataclass, field

@dataclass
class SectionPlan:
    order: int           # TOC 中的序号（整合排序用）
    chapter: str       # "肿瘤科秘验方"
    name: str            # "治甲状腺瘤秘方"
    slug: str            # 文件系统安全名（slugify）
    start_page: int
    end_page: int      # 显式计算，断言连续无重叠

@dataclass
class Formula:
    subsection_id: str  # "26.4"
    name: str            # "鳖甲消瘤方"
    section_slug: str
    page_range: tuple    # (start, end) 跨页标记
    fields: dict        # {"来源": "...", "组成": "...", ...}

@dataclass
class SectionResult:
    plan: SectionPlan
    raw_md: str
    cleaned_md: str
    formulas: list      # list[Formula]
    formula_files: list   # ["formula_26.4.md", ...]
```

### 2.1 `core/pipeline/toc_analyzer.py`
- `analyze_toc(pdf_path, api_key) -> list[SectionPlan]`
- **自带 OCR 遍**：本函数**独立**完成 fitz 渲染 + SenseNova 单页识别（**不依赖 step 2 的 body OCR**）。目录页通常是前 1-3 页，单独渲染识别即可获得"科/病"结构文本。
- **定位目录**：启发式扫描前 N 页（密度检测：编号标题 `§\d+` / `第X章` 出现频次），而非固定前3-5页。
- **识别结构**：
  - 章（科）：正则 `【?(.+?)科秘验方】?` 或 `第X章(.+)`
  - 节（病）：正则 `§\d+\s*治(.+?)秘方` 或 `治(.+?)秘方`（**不依赖 `§` 符号**，真实 OCR 常丢符号）
  - 跳过序言/前言页（无编号标题）
- **回查起止页**：对每节名，扫描**正文页**（step 2 的 raw.md 或独立 fitz 遍）找首次出现该名（**模糊匹配**：容忍 OCR 噪声，如 "瘤"→"留" 用 difflib 阈值）。回查所需的正文文本由本函数另发一次 SenseNova 单页识别获得，或在 step 2 完成后回填。
- **显式 end_page**：`end_page = next_section.start_page - 1`；断言所有节连续无重叠、无空隙
- **slugify**：`治甲状腺瘤秘方` → `zhi-jiazhuangliu-mifang`，防中文目录截断冲突
- 落盘 `output/<book>/toc.json`

### 2.2 `core/pipeline/section_ocr.py`
- `process_section(pdf_path, plan, adapter) -> SectionResult`
- **复用现有适配器**：`from tcm_ocr.core.engines.sensenova_adapter import SenseNovaAdapter`，调用 `adapter.recognize_pages([page_i, page_{i+1}_top15%])`
- **crop 在调用前**：`run.py:417-421` 的"取下一页顶部 15%"逻辑**提取为共享 helper** `crop_top15pct(img)`，在 `process_section` 内调用后再传 `recognize_pages`，**不重实现**（适配器收原始图，crop 在 KZOCR 侧）
- **遍历 `[start_page, end_page]`**：每页 i 送双图取第1页
- **跨页合并**：每节 OCR 后调用 `_merge_cross_page_breaks(pages_text)`（`kzocr/engine/run.py:306` 已测试），**修复节内方剂跨页断裂**
- **保留页标记**：合并前在 `raw.md` 每页首行插 `<!-- page N -->`，供 `split_subsections` 推算 `Formula.page_range`
- **落盘**：`output/<book>/sections/<slug>/raw.md`（带引擎/模型 lineage 头注释）

### 2.3 `core/pipeline/section_postproc.py`
- `postproc_section(plan, raw_md, deepseek) -> SectionResult`
- **独立串行阶段**（在 `process_sections` 的 worker 之外，用令牌桶限速）
- **DeepSeek 调用**（思考模式，1M 上下文）：
  - 清洗：修正 OCR 错字（如 "沙免"→"沙参"）、统一字段分隔符 `：`
  - 抽取：每方剂 9 类字段 → `formulas.json`
  - 容错：缺失/多余字段不报错，标 `unknown` 待人工
- **配置扩展**：`kzocr/config.py` 增加 `deepseek_api_key` / `deepseek_model="deepseek-v4-flash"` / `deepseek_base_url` / `deepseek_rpm` 字段，复用现有 `Config.from_env()` 模式
- **落盘**：`cleaned.md` + `formulas.json`

### 2.4 `core/pipeline/subsec_splitter.py`
- `split_subsections(cleaned_md) -> list[Formula]`
- **切分标记 ONLY `^\d+\.\d+`**（如 `26.4 `、`27.1 `），**绝不**按 `来源：` 切（来源是每方字段#1，会切碎）
- 每节输出 `formula_<subsection_id>.md`（如 `formula_26.4.md`）

### 2.5 `core/pipeline/book_integrator.py`
- `integrate_book(toc, sections) -> BookResult`
- **按 `SectionPlan.order` 排序**拼接（非 dict 顺序）
- 生成 `book.md`（带章/节标题）+ `book.json`（结构化，复用 `kzocr/engine/types.py:BookResult`）
- **Formula → FormulaEntry 映射**：遍历各节 `formulas`，将 `组成` 字段解析为 `FormulaIngredient[]`（药材+剂量），填入 `BookResult.formulas` 供 zai 结构化展示；`pages: list[PageResult]` 由 `split_subsections` 的 `formula_*.md` 逐行填充（沿用 `run.py:_vlm_markdown_to_pages`）

---

## 3. 并行与限速策略

- **SenseNova 并行 OCR**：`ThreadPoolExecutor(max_workers=4)`，4 节同时 OCR。
  - **每线程独立适配器**：每个 worker 内 `SenseNovaAdapter()` 新建实例（**不共享**，避免 `close()`/`__del__` GC 竞态），配 `requests.Session` 复用连接
  - **客户端限速**：SenseNova 免费层有 RPM 限制，加 `RateLimiter(6 RPM)` + 指数退避（429 时 wait=2^n）
  - **每页重试**：单页失败重试 3 次，仍失败标记 `skip` 继续，不杀整节
- **DeepSeek 串行后处理**：500 req/5h 硬限速 → 令牌桶 `TokenBucket(500/18000s)` + 退避。每节 1 次调用（非每方），批量处理。
- **检查点 + 原子写**：每节 `manifest.json` 记录阶段标志（toc/ocr/postproc/split）；写文件先 `.tmp` 再 `os.rename` 避免半写；DeepSeek 令牌桶持久化 `{last_refill, remaining}` 供重启续跑。

---

## 4. 关键风险与对策

| 风险 | 对策 |
|------|------|
| TOC 识别漏节名 | 启发式密度检测 + 模糊回查（difflib）兜底 |
| 节末方剂跨到下一节 | 后处理按节边界裁剪 + 1 页重叠合并 |
| DeepSeek 限速 | 令牌桶 + 退避 + 每节批量 1 调用 |
| 单页 OCR 失败 | 每页重试 3 次，失败标记 skip 不杀节 |
| 并行竞态 | 各节独立文件无共享；DeepSeek 串行队列 |
| 整合顺序错 | SectionPlan.order 显式排序，断言连续 |

---

## 5. 存储布局

```
output/<book_code>/
├── toc.json                    # list[SectionPlan]
├── sections/
│   ├── zhi-jiazhuangliu-mifang/   # 治甲状腺瘤秘方
│   │   ├── raw.md                # SenseNova 原始
│   │   ├── cleaned.md            # DeepSeek 清洗后
│   │   ├── formulas.json        # 结构化方剂
│   │   ├── formula_26.4.md   # 单个小节
│   │   └── formula_26.5.md
│   └── zhi-jiazhuangnangzhong-mifang/  # 治甲状腺囊肿秘方
└── book.md                     # 全书整合
```
（`<book_code>` 由 CLI `--book-code` 或 PDF 文件名生成，见 `kzocr/engine/run.py:352`）

---

## 6. 评审流程（≥4 轮）

1. **架构评审**：多 agent 评审 TOC 分析器 + 组件边界
2. **OCR 质量评审**：抽样对比 SenseNova 原始 vs 真值（已跑通 5 页 47s）
3. **后处理评审**：DeepSeek 清洗结果 agent + 人工双检
4. **整合评审**：全书结构完整性 + 顺序检查
5. 每轮修订后重新评审，直至收敛

---

## 7. 实施顺序（任务分解）

1. `toc_models.py` — 数据模型
2. `toc_analyzer.py` — TOC 分析（含测试）
3. `section_ocr.py` — 复用适配器 + 跨页合并（含测试）
4. `section_postproc.py` — DeepSeek 后处理 + 令牌桶（含测试）
5. `subsec_splitter.py` — 小节拆分（含测试）
6. `book_integrator.py` — 全书整合（含测试）
7. `pipeline_cli.py` — 串联 CLI（TOC→并行→后处理→拆分→整合）
8. 端到端 5 页冒烟测试
