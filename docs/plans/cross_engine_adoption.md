# KZOCR 借鉴 ocr_pipeline_v2 跨引擎校验方案

> 状态：cross_align.py 已落地（feat/cross-align, commit 8a1d8db），本文档固化「借鉴什么 / 映射到哪 / 下一步怎么接」。
> 来源：① `ocr_pipeline_v2` 项目（M0-M5 已合并，2026-07-17 仓库级记忆 `project_ocr_pipeline_v2.md`）；② 豆包帖《古籍字形比对系统升级改造》（独立印证同一结论）。

## 1. 三方一致的核心结论

ocr_pipeline_v2 四角色评审 + 实测、以及豆包帖独立验证，**对同一问题给出相同铁律**：

1. **字形校验（任何图像相似度路线：骨架/CLIP）对中文单字基本无效**——任意两汉字骨架相似度≈0.92~0.98，CLIP≈0.998~1.000，无法区分形近字，只抓 gross failure（空白/污渍/严重残缺）。
2. **字符级交叉对照不可行**——VLM（SenseNova）输出归一化文本，行粒度/顺序与逐字引擎（PPOCRv6/HunyuanOCR）不匹配，`char_idx` 投影对齐机制失效。
3. **真正的判错主力 = 双模型交叉（token 级模糊对齐）+ 人工/视觉复核**；字形相似度永久降级为粗闸门（sim<0.3 丢，≥0.3 只记日志不判对错）。
4. **数字/剂量类分歧最危险**（6↔5、9↔3、8↔3、69↔53；中文 二↔三、五↔三），应优先高亮送复核。

## 2. KZOCR 现状审计（好消息：多数已具备）

通读 `kzocr/scheduler/verifier.py` 确认 KZOCR 已**天然规避**上述陷阱，且部分能力领先 ocr_pipeline_v2：

| ocr_pipeline_v2 / 豆包 | KZOCR 现状 | 结论 |
|---|---|---|
| 字形闸门（骨架相似度） | **无**——GlyphVerifier 用检测器链（ToxinDose/Leakage/CharCountSpike/ConfusionSet/TermKB），非图像相似度 | ✅ 已规避陷阱 |
| M3 双模型交叉（字符级 PK join） | **无**——当前是「逐 tier 验证→失败降级」，从不比对两引擎文本 | ❌ 缺口（本次补齐） |
| 形近字黑名单 | `ConfusionSetDetector` + `confusion_set.json` | ✅ 已有 |
| 剂量安全 | `ToxinDoseDetector`（药名+剂量超上限→FAIL critical） | ✅ 已有，且比 ocr_pipeline_v2 更领域化 |
| M4 视觉仲裁 | `VisionRecheckAdapter`（SenseNova 6.7-flash-lite / Qwen3-VL，整页 PASS/FAIL 回看） | ✅ 雏形已有 |
| M4 复核队列规则（冲突100%+黑名单+conf≤0.90+随机10%） | 无显式队列规则 | ❌ 待补 |
| M6 语义校验大模型 | `QualityChecker`（LLM 质检） | ✅ 已超前 |

**结论**：KZOCR 不需要从零重建交叉校验，真正缺的只有 **M3 的 token 级分歧对齐**；M4 复核队列规则与「分歧级视觉仲裁」是次要增强。

## 3. 已落地：`kzocr/scheduler/cross_align.py`（feat/cross-align）

纯函数、无网络依赖、10 例单测全过、ruff 无报错。

- `align_engines(text_a, text_b, ctx=8, confusion_set=None, boxes_a=None) -> list[Divergence]`
  两边去标点/空白后 `difflib.SequenceMatcher` 最优对齐，抽 replace/delete/insert 分歧；数字/剂量与形近黑名单分歧标 `priority='high'`。
- `run_cross_align(page_no, text_a, text_b, ..., engine_a, engine_b)` 端到端封装，填 page_no/引擎标签。
- `write_divergences(db_path, page_no, divs, engine_a, engine_b)` 落 `cross_divergence` 表（幂等 CREATE TABLE IF NOT EXISTS）。
- `Divergence`：page_no / div_type / a_seg / b_seg / a_context（±8 字，分歧处【】标出）/ boxes（可选，供裁图）/ priority / status / engine_a / engine_b。

## 4. 下一步集成映射

### 4.1 orchestrator 接入点（M3 比对）
`kzocr/scheduler/orchestrator.py` 当前：`Tier1 验证 → 失败页 Tier2 → Tier3 → HumanGate`。
- **失败路径已有 Tier1 与 Tier2 双文本**：在 Tier2 产出后、送 HumanGate 前，调 `run_cross_align(page, tier1_text, tier2_text, confusion_set=...)` 生成分歧。
- **增强路径（可选）**：对 Tier1 通过但高风险页（含方剂/剂量），并行跑 Tier2 采样比对，捕获 Tier1 验证器抓不到的字符级错误（因 GlyphVerifier 对中文无法判对错）。
- `boxes_a` 当前为空（KZOCR 归一化数据无逐字 box）；待 `BookResult` 携带 char box 后可填，供 4.3 裁图。

### 4.2 M4 复核队列规则（映射到 HumanGate / arbitrate）
豆包帖规则，对应 KZOCR 实现：
- M3 冲突（cross_divergence 任一分歧）→ **100% 进复核**（priority 分歧本就 high）。
- 形近黑名单命中（`ConfusionSetDetector` 已标 UNKNOWN）→ 强制复核。
- `conf≤0.90` 强制复核：KZOCR 当前不在 cross_align 内带置信度；需在 4.1 接入时把引擎置信度（若可用）并入 Divergence，或复用 `analyze_ppocr_conf.py` 思路校准阈值。
- 随机抽样 10%：对「两引擎一致」页抽样送视觉仲裁，覆盖「两引擎同错」盲区（ocr_pipeline_v2 方案 C）。

### 4.3 分歧级视觉仲裁（升级 VisionRecheckAdapter）→ Box-Guided VL ✅ 已落地
当前 `VisionRecheckAdapter.recheck` 对整页发 VL 模型问 PASS/FAIL，粒度粗；且纯文本比对对「两引擎共识错误」失明。
借鉴 ocr_pipeline_v2 `cross_arbitrate.py` + 豆包帖《形近字共识错误难破的原因与应对》的 **Box-Guided VL** 范式（关键增量）：
- **框约束校验**：不给 VL 整页问 PASS/FAIL，而是拿候选字 + 它的 quad 框，**只让 VL 重审那一小框的字形**（认知对比），Prompt 强制输出 `{candidate_char, is_match, confidence, real_char}` 纯 JSON，内置形近字清单（炙≠灸、芩≠苓…）。
- **裁框规则**：向外 Padding 扩张 8px（防墨迹晕染）、用原图像素绝对坐标、禁止多字同框；`conf<0.85`/尺寸过小/跨边缘/多字合并 → 跳过 VL 强制人工。
- **结果对齐**：按 quad 中心点距离匹配（阈值 40px），**禁止顺序匹配**（VL 输出顺序不可信）。
- **M4-AI 仲裁层触发于「两引擎一致」样本**：不仅分歧页，更要对 Tier1/Tier3 一致页抽样跑 Box-Guided VL，覆盖「两引擎同错」盲区（与横向交叉原理正交，分层压制漏检率）。
- **VL 门控**：`is_match=False` 或 `conf<0.65` → 强制人工；`conf≥0.65` → 放行+兜底。JSON 解析失败 → 标 `parse_failed` 统一人工。
- 裁决映射：`accepted_a` / `accepted_b` / `both_wrong`(→manual) / `uncertain`(→L3)。
- 复用 `MultiTokenRateLimiter`（`kzocr/engines/ratelimit.py`，设计文档所述 `AdaptiveTokenBucket` 即此类）共享进程级限流，key=`vision_recheck`，tokens=30/window=60s。
- **开关**：复用 `allow_cloud_vision` 作为等价 `--enable-arb` 总闸（vision_adapter 仅在该开关开启时创建）；默认关闭，不调网络。
- **落地形态（feat/cross-align，待提交）**：
  - `VisionRecheckAdapter.arbitrate_divergence(divergence, page_img, confusion_set, bucket)`：未配置/无图像→直接 manual；`boxes` 非空→精确 Box-Guided（多字框/过小框→manual 跳过）；`boxes` 为空（当前 KZOCR 归一化数据无逐字 bbox）→**退化整页缩图 + 上下文提示**。
  - 纯函数辅助 `_build_arbitration_prompt` / `_parse_arbitration_response`（容错去 ```json 围栏、截取首尾大括号）/ `_gate_arbitration`（门控映射）可单测。
  - `DivergenceArbitration` dataclass（kzocr/scheduler/cross_align.py）承载裁决结果。
  - orchestrator 在 4.2 M4 入队后，对 high 分歧在 `allow_cloud_vision` 且 `page_img` 非空时调仲裁，结果经 `BookDB.update_cross_divergence_status` 落库；整段包 try/except 不阻断主流程。
  - 单测：`tests/test_verifier_arbitration.py`（解析/门控/degraded/box_guided/非法框）+ `tests/test_cross_divergence.py::test_cross_divergence_arbitrated_by_vision`（编排集成）。
- **前置（仍待补）**：精确 Box-Guided 依赖 char-level quad（见 §5 风险）；当前 `Divergence.boxes` 为空 → 走退化模式，待 `BookResult` 携带 box 后启用精确裁框。共识错误抽样（两引擎一致页）也待 boxes 与采样基建。

### 4.4 落库与暴露 ✅ 已落地
- `cross_divergence` 表并入 KZOCR 的 `BookDB`（F2），与 page_progress / benchmark 同库。
- Web 页面 `/book/{book_code}/divergences`（模板 `divergences.html`，含优先级筛选 + 每分歧「学为形近字」按钮）。
- REST 端点：
  - `GET /api/books/{code}/divergences`（可选 `page` / `priority` 过滤，返回 JSON 列表）。
  - `POST /api/confusion`（自学习入口：新增/更新一条形近字混淆对，见 §4.5）。

### 4.5 形近字黑名单「自学习 / 可进化」（用户明确方向）
静态黑名单应**常驻内存快速调用**，但**内容动态优化、自学习**——不是写死不变的 JSON。
落地机制（feat/cross-align，待提交）：
- `load_confusion_set()` 首次构建后缓存在模块级 `_CONFUSION_CACHE`，后续调用直接命中（静态呆在内存）；`reload=True` 或 `reload_confusion_set()` 强制从磁盘重读。
- `learned_confusion.json`（运行时生成，已 gitignore）叠加在静态 `confusion_set.json` 之上（learned 覆盖静态）。
- `add_learned_confusion(wrong, correct, source)`：原子写入学习集（复用 `atomic_write` + `allowed_base` 防路径穿越，同 C2 修复），**并同步更新内存缓存** → 新混淆立即对后续比对生效。
- 触发来源：Web 分歧台「学为形近字」按钮（人工确认 A↔B 形近）调 `POST /api/confusion`；未来可接仲裁 `both_wrong` 信号（待定，避免噪声）。
- 设计原则（用户原话）：*黑名单应静态呆在内存里、方便随时快速调用，但内容不是静态的、是动态优化的自学习的*。

## 5. 风险 / 待决

- **无逐字 box**：KZOCR 归一化数据不含 char-level bbox，4.3 裁图与豆包 M3 空间中心点匹配（欧氏距离<32px）暂不可做；需先让 `BookResult` 携带 box（kimi/Tier2 引擎若输出 quad 则可填 `boxes_a`）。
- **置信度门控**：4.2 的 `conf≤0.90` 规则需 KZOCR 引擎产出带置信度；当前 Tier 文本不带，需确认引擎接口。
- **真实图源未端到端跑**：与 ocr_pipeline_v2 同样，KZOCR 真实流水线未在本环境跑过（无扫描图）；cross_align 纯函数已单测，但 orchestrator 接入需真实书验证。
- **是否并行采样比对**：4.1 增强路径会显著增加 Tier2 调用成本，需按 ocr_pipeline_v2 §1.6 置信度门控/预算护栏收敛。

## 6. 任务拆分（建议）

1. ✅ cross_align.py 模块 + 单测（已完成，feat/cross-align）
2. ✅ orchestrator 接入：失败路径 Tier1/Tier3 比对 → cross_divergence（4.1；注：当前编排 Tier1 验证失败直接跳 Tier3 本地 LLM，跳过 Tier2 云端 OCR，故比对对象为 Tier3 文本；分歧落 BookDB.cross_divergence 表，纯函数无网络、失败页量小）
3. ✅ 复核队列规则落 HumanGate（4.2）：high 优先级分歧（数字/剂量、形近字）100% 经 `db.record_anomaly(detector_chain=["CrossAlign"])` 进人工复核队列。注：`conf≤0.90` 门控前置依赖引擎逐字置信度，KZOCR 归一化文本暂不带，待引擎接口补置信度后启用。
4. ✅ VisionRecheckAdapter 升级为分歧级仲裁（4.3）：Box-Guided VL + 退化模式，high 分歧经仲裁落库（arbitrate_divergence + DivergenceArbitration + update_cross_divergence_status）。注：精确裁框待 BookResult 携带 char box；共识错误两引擎一致抽样待后续。
5. ✅ cross_divergence 并入 BookDB + Web/REST 暴露（4.4）：HTML 页面 `/book/{code}/divergences` + REST `GET /api/books/{code}/divergences`，含优先级筛选。
6. ✅ 形近字黑名单自学习/可进化（4.5）：内存常驻缓存 `_CONFUSION_CACHE` + `learned_confusion.json` + `add_learned_confusion` + `POST /api/confusion` + 分歧台「学为形近字」按钮（内容动态、调用静态）。
7. ⬜ 真实书端到端验证（需扫描图源）
