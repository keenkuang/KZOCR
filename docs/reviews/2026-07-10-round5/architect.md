# KZOCR v0.4 AMEND 架构师评审

- **评审角色**：首席架构师
- **评审日期**：2026-07-10
- **评审对象**：`docs/plans/ocr-engine-unification.v0.4-AMEND.md`（C1–C5，从 970 页 TOC 项目吸收的 5 项实战经验）
- **基准方案**：`v0.3-FREEZE.md`（B1–B8 已冻结，本次不回溯）
- **配套代码检查**：`run.py`、`engines/_common.py`、`engines/__init__.py`、`modelscope_pool.py`
- **配套设计参考**：`docs/plans/toc-driven-pipeline-design.md`（C5 原始设计）

---

## 1. C1 —— VLM 4 层泄漏防御与现有 `_merge_cross_page_breaks()` 兼容性

### [C1-1] 问题性质不同：可共存，但执行顺序必须明确
- `_merge_cross_page_breaks()`（`run.py:365-433`）处理的是**合法跨页内容**——处方从页末延续到页首，通过句末标点检测 + 下页续接行合并来修复。
- C1 L4（增量探针重叠检测）处理的是**非法泄漏**——VLM 双页上下文中模型"看到了下一页顶部"后错误输出了本不应输出的内容。
- 二者语义正交，**完全可以共存**，但执行顺序至关紧要：**C1 L4 必须后于 `_merge_cross_page_breaks` 运行**。若先做 L4 探针检测，合法的跨页续接行可能被误判为"泄漏"并截断；若先合并，则 L4 探针在合并后的文本上运行，被续接的文本不会因为"下页内容在本页末尾出现"而触发误报（因为那是合法合并的结果）。

**修改建议**：在 C1 实施文档中显式写明流水线顺序：
```
OCR 逐页 → C1 L1-L3 逐页防御 → _merge_cross_page_breaks → C1 L4 探针检测 → _vlm_postprocess
```
并在 `leakage.py` 的 L4 函数签名备注"调用方须确保已执行 `_merge_cross_page_breaks`"。

### [C1-2] `baseline` 计算要求 2-pass 扫描，与现有单遍流水线冲突
L1 要求用"前 50 页字符数中位数作为 baseline"，这意味着：
1. 必须先把前 50 页 OCR 一遍才能算出 baseline
2. 然后对前 50 页中超过 `threshold=baseline×1.5` 的页面做 L3 重 OCR
3. 第 51 页开始才能用 L1 prompt 注入

现有 `_run_vlm()`（`run.py:436-512`）是**单遍流式**：逐页识别、逐页追加、最后合并。引入 C1 后，前 50 页变为两遍（OCRs → 算 baseline → 有选择地重 OCR），第 51 页之后又切回一遍。这使流水线复杂度上升，且中断恢复（C2）需要记忆"前 50 页是否已算 baseline"的状态。

**修改建议**：
- 将 baseline 计算设计为**可分离的阶段**：`LeakageDetector.build_baseline(pages_text[:50]) → baseline`，与主循环解耦
- 第 51+ 页通过 C1 L1 prompt 注入防御（无需事后修正）
- C2 的 `is_complete` 需记录 `_baseline_done` 标记（可放一个 `leakage.state.json` 文件，或利用 C2 的文件存在性模式：写 `leakage.baseline` 文件作为基线就绪标志）

### [C1-3] L2 `max_tokens=2048` 下调的影响域需评估
当前 `_run_vlm()` 未显式设置 `max_tokens`——它由适配器内部默认值决定（`PaddleOCRVl16Adapter` / `SenseNovaAdapter` 各自有默认值）。将 `max_tokens` 从 4096 下调到 2048 是一个**全局改动**，不仅影响泄漏防御，还可能影响正常长页面的完整识别（例如一页包含大量密集中医文字时，2048 token 可能截断有效内容）。

**修改建议**：
- L2 不应是硬编码 `max_tokens=2048`，而应是 **`min(adapter_default, 2048)`** 或作为可配置项
- 在 `leakage.py` 暴露 `effective_max_tokens(page_char_count, baseline)` 策略，允许适配器按需调整
- 对 SenseNova 和 PaddleOCR-VL 分别估算 2048 token 对应的汉字数（约 1500–2500 汉字），验证是否超过典型中医书单页字数

### 逐项裁决

- [x] **[C1-1] 共存性**：`_merge_cross_page_breaks` 与 C1 L4 可共存，需显式规定执行顺序。**通过。**
- [ ] **[C1-2] 2-pass 基线计算**：与现有单遍流水线冲突，需改造 `_run_vlm` 为可分离的基线阶段 + 主循环阶段。**需修订。**
- [ ] **[C1-3] max_tokens 全局下调风险**：未评估对正常长页面的截断影响，应改为自适应策略。**需修订。**

---

## 2. C5（TOC 管线）与现有 Router 架构的边界

### [C5-1] TOC 分析器是 pipeline 层组件，不应放入 Router
C5 的 `pipeline/toc_analyzer.py` 职责是：
- 独立 OCR 目录页（自包含 SenseNova 调用）
- 输出 `list[SectionPlan]`（结构元数据，非 OCR 文本）
- 驱动后续的并行节处理器

这和 EngineRouter 的职责（**引擎选择 + 降级链 + 共识装配**）是**完全不同的抽象层次**。TOC 分析器是**高阶编排器**，Router 是**引擎层路由**。将 TOC 分析器塞进 Router 会导致 Router 既做引擎调度又做书籍结构分析，违反单一职责。

**架构定位**：
```
┌───────────────────┐
│  TOC Orchestrator  │ ← pipeline/ 层（高阶编排）
│  (toc_analyzer +   │
│   section_ocr +    │
│   section_postproc)│
└────────┬──────────┘
         │ 使用引擎
         ▼
┌───────────────────┐
│  EngineRouter      │ ← engines/ 层（引擎选择 + 降级）
│  + BaseAdapter     │
│  + egress.py       │
└───────────────────┘
         │
         ▼
┌───────────────────┐
│  Adapters          │ ← 具体引擎实现
│  (SenseNova,       │
│   PaddleOCR-VL…)   │
└───────────────────┘
```

### [C5-2] TOC 管线直接实例化 SenseNovaAdapter，绕过 Router 的降级链和出境校验
当前 `toc-driven-pipeline-design.md:131` 写的是 `from tcm_ocr.core.engines.sensenova_adapter import SenseNovaAdapter`——直接导入引擎适配器，**跳过了** v0.3 FREEZE 建立的：
- EngineRouter 的降级链（SenseNova 不可用→降级 PaddleOCR-VL）
- egress.py 的出境内核 allowlist 校验
- `adapter_to_line_result()` 的统一转换

这意味着当 P3 (Stage 2) Router + egress 上线后，TOC 管线将成为"后门"。

**修改建议**：TOC 管线不应直接实例化适配器，而应：
- 通过 `EngineRouter` 获取 OCR 能力：`router.ocr_page(page_img) → AdapterPageResult`
- 或至少通过 `BaseAdapter.create("sensenova")` 工厂方法，确保经过允许引擎清单校验
- 若 TOC 需要 SenseNova 独占（因为双页上下文切割逻辑是 sense nova 特有的），则要在 C5 的文档中**显式声明"TOC 管线硬依赖 SenseNova，不做降级"**，并为之单独通过 egres 校验

### [C5-3] TOC 管线应依赖 C3 限流器，但 C3 的多线程安全设计尚未定义
C5 的 `section_ocr.py` 用 `ThreadPoolExecutor(max_workers=4)` 并行 OCR。C3 的 `MultiTokenRateLimiter` 用公司级配额 `600 req/min`。如果每个 worker 创建自己的 rate limiter 实例，公司级配额无法被跨线程感知，导致 503 峰值。

**修改建议**：C3 的 `MultiTokenRateLimiter` 必须设计为**进程级单例（或显式共享实例）**，所有 worker 共享同一个令牌桶。`section_ocr.py` 的构造函数接收从外部传入的 `ratelimiter` 实例而非自建。

### 逐项裁决

- [x] **C5-1 TOC 层归属**：明确为 pipeline 层，不与 Router 合体。**通过（设计方向正确）。**
- [ ] **C5-2 绕过 Router 降级链和出境校验**：TOC 直接实例化 SenseNovaAdapter 是架构后门。**需修订。**
- [ ] **C5-3 限流器多线程共享**：`MultiTokenRateLimiter` 需设计为共享实例模式。**需修订（在设计层提前定义，非仅实现层问题）。**

---

## 3. C1–C5 之间的架构冲突

### [冲突-1 · Medium] C1 L3 重试 + C3 Layer 3 退避可能堆叠（retry chain stacking）
- C1 L3：超阈页面最多重 OCR 1 次（`max_tokens=baseline×1.8`）
- C3 Layer 3：`base_delay=2s, max_retries=5, max_delay=300s`

若 C1 L3 重 OCR 请求遇到 503/429，会**同时触发**：
- C1 L3 的"已重试 1 次"计数
- C3 Layer 3 的退避重试（最多再 5 次）

实际效果：C1 L3 的"1 次重试"被 C3 Layer 3 放大为潜在的 1 + 5 = 6 次 API 调用。更严重的是——C1 L3 重 OCR 使用更紧的 `max_tokens` 参数，若 C3 Layer 3 的 5 次退避最终成功但使用的是**原始** max_tokens（而非收紧的），则 C1 L3 的逻辑被破坏。

**修改建议**：
- C1 L3 的重 OCR 请求在传给 C3 限流器时，应将**重试上下文**（`retry_reason="leakage"`、`tight_max_tokens=...`）透传给 C3 Layer 3
- C3 Layer 3 的退避重试应保留原始请求的参数（包括 `max_tokens`），不降级为默认值
- 或者在 C1 L3 的上层设置**总重试计数器**：C1 的"1 次重 OCR" + C3 的退避共同组成一条重试链，而非两条嵌套的独立链。即 C1 声明"我只想多试 1 次"，C3 负责把那 1 次做得稳健（退避），而不是"C1 试 1 次，C3 再额外试 5 次"。

### [冲突-2 · Low] C2 `is_complete` 文件存在性语义在 C1 L3 重 OCR 场景下可能误判
- C2：`is_complete(path)` = 文件存在 + 非空
- C1 L3：首次 OCR 写入 `page_3.md`，发现超阈 → 重 OCR，用 `atomic_write` 覆盖 `page_3.md`

在 C1 L3 场景下：
- 首写 `page_3.md`（超阈，含泄漏内容）→ 标记 complete
- L3 判定需重 OCR → 生成新内容 → `atomic_write` 覆盖

如果中断发生在**首写完成之后、重 OCR 完成之前**：
- `page_3.md` 存在且非空（首次的内容），被 C2 判定为"已完成"
- 但实际已标记需重 OCR，且重 OCR 结果未就绪
- 重启后 C2 跳过第 3 页，使用首次的泄漏内容

**修改建议**：
- C1 L3 的重 OCR 标记不应隐式依赖 `page_3.md` 的最终版本，而应有显式的 `_pending_recheck` 标记
- 或者重 OCR 的产物先写临时文件（如 `page_3.recheck.md`），确认后才替换 `page_3.md`
- C2 的 `is_complete` 对于参与了 C1 L3 的页面，应额外检查是否存在 `page_3.recheck` 标记

### [冲突-3 · Low] C5 的 DeepSeek 后处理与 C3 Layer 2（公司级配额）的限速策略不一致
- C3 Layer 2：`MultiTokenRateLimiter` 使用 600 req/min 全局共享配额
- C5 `section_postproc.py` 使用 `TokenBucket(500/18000s)`（500 req / 5h）
- DeepSeek 的实际限速是 **500 req/5h**（约 1 req/36s），非 600 req/min

C5 的 `TokenBucket(500/18000s)` 是正确的（匹配 DeepSeek 官方限速），C3 Layer 2 的 `600 req/min` 是对公司级聚合配额的估计。但当 C5 的 DeepSeek 调用通过 C3 Layer 2 时，Layer 2 会允许 10 req/s（600/60），这远超 DeepSeek 实际容量。

**修改建议**：
- C3 的 `MultiTokenRateLimiter` 应支持**按目标域配置不同的速率**，而非单一的公司级 600 req/min
- 将 DeepSeek 限速独立为 `500/18000s` 的子限制器，在 C3 中按 `base_url` 路由到对应的限制器

### 逐项裁决

- [ ] **[冲突-1] C1 L3 重试 + C3 Layer 3 退避堆叠**：可能从"1 次重 OCR"放大为 6 次，需建立统一的重试链上下文。**需修订。**
- [x] **[冲突-2] C2 is_complete 与 C1 L3 重 OCR 的半写状态**：中断恢复时可能跳过本需重 OCR 的页面。**通过（方案层已识别，需实现层补充标记机制）。**
- [ ] **[冲突-3] C3 Layer 2 与 C5 DeepSeek 限速不匹配**：600 req/min 与 500 req/5h 差异达 43 倍，需按目标域配置不同速率。**需修订。**

---

## 4. 实施顺序分析

### 实施顺序表（基于 v0.3 §7 + C1–C5）

```
P0:  C4 (INSERT OR REPLACE 陷阱修复) — book_pipeline.py
P1:  B4 (is_mock sink)            — to_zai_prisma.py
P1:  C2 (原子写入)                  — kzocr/engines/atomic.py
P2:  C1 (泄漏防御)                  — kzocr/engines/leakage.py ↑
P2:  C3 (限流器)                    — kzocr/engines/ratelimit.py ↑
P2.5: C5 (TOC 管线)               — pipeline/toc_*.py
P3:  Stage 2 (B2/B3/B8 Router + egress)
P4:  Stage 3 (B1/B5 GlyphVerifier + 资源)
```

### [顺序-1 · High] P2.5 依赖 C3 但 C3 在 P2 并行——如果 C3 延迟或接口变化会阻塞
C5 `section_ocr.py` 的 4 线程并行 + `section_postproc.py` 的 DeepSeek 后处理都依赖 C3 的限流器接口。P2 中 C1 和 C3 标记为并行，但如果 C3 的接口在实现中发生变化（例如从同步 API 改为 asyncio 或有锁的单例模式），P2.5 的实现需要跟随调整。

实际风险较低（P2 内的 C3 不会大幅推迟 P2.5），但需在阶段规划中保证：**C3 的接口必须在进入 P2.5 前 FROZEN**，与 B1–B8 的"契约冻结"纪律一致。

### [顺序-2 · Medium] P2.5 绕过了 P3 的 Router——如果 P2.5 先做，后期需要适配 Router
规划中 P2.5 在 P3 之前。这意味着 TOC 管线会直接使用 SenseNovaAdapter（绕过 Router），而当 P3 Router + egress 上线后，TOC 管线要么：
- 继续作为独立路径（形成"双引擎路径"的架构债务）
- 需要重构为通过 Router 调用（适配成本）

**修改建议**：
- 方案一（推荐）：**P2.5 移到 P3 之后**，确保 TOC 管线从第一天就使用 Router，不走弯路
- 方案二（折中）：如果 TOC 管线需求紧迫，则在 P2.5 设计中**预留 Router 替换接口**——`SectionOcr` 接受 `callable` 或 `EngineRouter` 作为参数，而非硬编码 `SenseNovaAdapter`。这样 P3 Router 上线后只需替换注入目标，无需重写管线

### [顺序-3 · Low] P1 中 B4 和 C2 完全独立，建议显式标记可并行
B4（`to_zai_prisma.py`）和 C2（`kzocr/engines/atomic.py`）修改的是不同包的不同文件，无任何共享依赖。建议在实施安排中明确标注**可并行实现**，节省 wall-clock 时间。

### [顺序-4 · Low] P0 (C4) 的验证依赖 C2（非阻塞）
C4 修复 `book_pipeline.py` 的 `INSERT OR REPLACE` 本身不需要 C2，但验证"中断恢复不丢数据"这个属性需要 C2 的 `atomic_write` 设施来做 E2E 测试。建议 C4 修复先做，E2E 验证等 C2 就绪后补。

### 逐项裁决

- [ ] **[顺序-1] P2.5 依赖 C3 接口**：需要 C3 接口 FROZEN 后才能启动 P2.5。**需修订（增加阶段闸门条件）。**
- [ ] **[顺序-2] P2.5 在 P3 之前导致后续 Router 适配债**：TOC 管线可能成为"绕开 Router 的后门"。**需修订（推荐移至 P3 之后，或预留替换接口）。**
- [x] **[顺序-3] P1 内 B4 和 C2 独立可并行**：无依赖冲突，可以并行。**通过。**
- [x] **[顺序-4] P0 验证依赖 C2**：C4 修复本身独立，E2E 验证需 C2 就绪。**通过（非阻断，仅有验证延迟）。**

---

## 5. 其他架构关注点

### [A-1 · Medium] C2 的"文件存在即状态"哲学与 C1 的"baseline 阶段"需要额外的状态标记
C2 的设计原则是"文件存在即已完成"，不再使用 `pipeline_state.json`。但 C1 引入了一个**两阶段状态**：
1. 前 50 页 OCR 完成（可以计算 baseline，但文件已存在）
2. L3 重 OCR 完成（文件已覆盖）

如果 C2 在重启时看到前 50 页文件都存在且非空，它不知道 baseline 是否已计算、L3 是否需要执行。C1 的"前 50 页两遍扫描"引入了**基线就绪（baseline_done）** 和**重检就绪（recheck_pending）** 两个逻辑状态，但 C2 的哲学只提供"文件存在"一个物理信号。

**修改建议**：
- 在 C2 的 `is_complete` 基础上增加 `def is_complete_with_marker(path: Path, marker_name: str) -> bool`，支持检查配套的 `.done` 标记文件
- 或者在 `leakage.py` 内部维护 `_calc_baseline_done` 标志，利用 C2 写一个 `leakage.baseline` 文件作为标记——不破坏 C2"不用 JSON"的原则，但多一种文件类型

### [A-2 · Low] AMEND 文档未定义 C5 在 KZOCR 包内的目录结构
`toc-driven-pipeline-design.md` 写的路径是 `core/pipeline/toc_*.py`（来自 TOC 项目的源码树）。AMEND 说放在 `pipeline/toc_*.py`。但 KZOCR 当前的包结构是 `kzocr/engine/` + `kzocr/engines/` + `kzocr/adapter/`，没有 `pipeline/` 包。

**修改建议**：在 AMEND 中明确 C5 文件的目标路径，例如 `kzocr/pipeline/toc_analyzer.py`、`kzocr/pipeline/section_ocr.py`，并确保 `setup.py` / `pyproject.toml` 包含该包。

---

## 总体裁决

### **有条件通过（Conditional Pass）**

C1–C5 整体方向正确，从 970 页实战中提炼的经验对 KZOCR 价值明确。C4（INSERT OR REPLACE 陷阱）和 C2（原子写入）是干净的改进，无架构争议。C1（泄漏防御）的核心思路（4 层渐进）与现有代码**可共存**。

但 **4 项修订**必须在进入阶段实施前解决：

| # | 严重度 | 条目 | 类型 |
|---|--------|------|------|
| 1 | **High** | **[顺序-2] P2.5 TOC 管线绕开 Router**：在 P3 (Router) 之前实作 TOC 管线将产生"双路径"架构债务。推荐将 P2.5 移至 P3 之后，或预留 Router 替换接口。 | 顺序修订 |
| 2 | **Medium** | **[冲突-1] C1 L3 + C3 退避堆叠**：重试链需要统一上下文透传，避免"1 次重试"被放大为 6 次调用。 | 架构修订 |
| 3 | **Medium** | **[冲突-3] C3 Layer 2 与 C5 DeepSeek 限速不匹配**：600 req/min 的公司级速率与 500 req/5h 的 DeepSeek 实际限速差异巨大，需按目标域配置不同速率。 | 设计修订 |
| 4 | **Medium** | **[C5-2] TOC 管线直接实例化适配器**：跳过 egress 校验和降级链。TOC 必须通过 EngineRouter 或至少 `BaseAdapter.create()` 工厂获取引擎实例。 | 架构修订 |

### 通过项摘要

- [x] C1–C5 整体方向正确，无根本性架构错误
- [x] C1 L4 与 `_merge_cross_page_breaks` 可共存（顺序决定）
- [x] C4 修复是纯净改进，无架构冲突
- [x] C2 设计原则清晰（文件即状态），与 C1 的标记冲突可通过 `.baseline` 标记文件解决
- [x] P0–P1 顺序合理，B4 与 C2 可并行

### 修订建议汇总（定稿前必改）

1. **[High] 修正实施顺序**：P2.5 移到 P3 之后，或改为"预留 Router 替换接口 + P3 后适配"
2. **[Medium] C1 + C3 重试链统一**：C1 L3 重 OCR 的参数上下文透传给 C3 Layer 3；总重试上限在 C1 层控制
3. **[Medium] C3 MultiTokenRateLimiter 按目标域配置**：DeepSeek 的 500 req/5h 不应被 600 req/min 覆盖
4. **[Medium] C5 改为通过 EngineRouter/factory 获取引擎实例**：不直接 new SenseNovaAdapter
5. **[Low] C1-2 改造 `_run_vlm` 为分离的基线阶段 + 主循环**：文档中补充流水线阶段图
6. **[Low] C1-3 max_tokens 改为 `min(adapter_default, 2048)`**：评估对长页面的截断风险
7. **[Low] C5 文件路径落定**：在 AMEND 中写明 KZOCR 包内的实际路径（`kzocr/pipeline/toc_*.py`）
8. **[Low] C2 is_complete 增加标记文件支持**：`is_complete_with_marker(path, marker_name)` 供 C1 baseline 使用

---

*附录：本次评审检查的代码文件*
- `kzocr/engine/run.py`（`_merge_cross_page_breaks` L365-433, `_run_vlm` L436-512）
- `kzocr/engines/_common.py`（`adapter_to_line_result`）
- `kzocr/engines/__init__.py`
- `kzocr/modelscope_pool.py`（现有令牌桶逻辑，待迁移至 C3）
- `docs/plans/ocr-engine-unification.v0.3-FREEZE.md`（B1–B8 基线）
- `docs/plans/toc-driven-pipeline-design.md`（C5 原始设计）
- `docs/reviews/2026-07-09-round4/architect.md`（上一轮架构评审）
