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

### 4.3 分歧级视觉仲裁（升级 VisionRecheckAdapter）→ Box-Guided VL
当前 `VisionRecheckAdapter.recheck` 对整页发 VL 模型问 PASS/FAIL，粒度粗；且纯文本比对对「两引擎共识错误」失明。
借鉴 ocr_pipeline_v2 `cross_arbitrate.py` + 豆包帖《形近字共识错误难破的原因与应对》的 **Box-Guided VL** 范式（关键增量）：
- **框约束校验**：不给 VL 整页问 PASS/FAIL，而是拿候选字 + 它的 quad 框，**只让 VL 重审那一小框的字形**（认知对比），Prompt 强制输出 `{quad, candidate_char, is_match, confidence, real_char}` 纯 JSON，内置形近字清单（炙≠灸、芩≠苓…）。
- **裁框规则**：向外 Padding 扩张 8px（防墨迹晕染）、用原图像素绝对坐标、禁止多字同框；`conf<0.85`/尺寸过小/跨边缘/多字合并 → 跳过 VL 强制人工。
- **结果对齐**：按 quad 中心点距离匹配（阈值 40px），**禁止顺序匹配**（VL 输出顺序不可信）。
- **M4-AI 仲裁层触发于「两引擎一致」样本**：不仅分歧页，更要对 Tier1/Tier3 一致页抽样跑 Box-Guided VL，覆盖「两引擎同错」盲区（与横向交叉原理正交，分层压制漏检率）。
- **VL 门控**：`is_match=False` 或 `conf<0.65` → 强制人工；`conf≥0.65` → 放行+兜底。JSON 解析失败 → 标 `parse_failed` 统一人工。
- 裁决映射：`accepted_a` / `accepted_b` / `both_wrong`(→manual) / `uncertain`(→L3)。
- 复用 `AdaptiveTokenBucket` 共享进程级限流（KZOCR 已有 scheduler 限流）；默认关闭，`--enable-arb` 才激活。
- **前置**：Box-Guided 依赖 char-level quad（见 §5 风险）；`Divergence.boxes` 为空时退化为「整页/上下文片段」仲裁，待 BookResult 携带 box 后启用精确裁框。

### 4.4 落库与暴露
- `cross_divergence` 表可并入 KZOCR 的 `BookDB`（F2），与 page_progress / benchmark 同库，供 Web 面板「校对工作台」展示分歧。
- REST API 可加 `/api/books/{code}/divergences` 端点。

## 5. 风险 / 待决

- **无逐字 box**：KZOCR 归一化数据不含 char-level bbox，4.3 裁图与豆包 M3 空间中心点匹配（欧氏距离<32px）暂不可做；需先让 `BookResult` 携带 box（kimi/Tier2 引擎若输出 quad 则可填 `boxes_a`）。
- **置信度门控**：4.2 的 `conf≤0.90` 规则需 KZOCR 引擎产出带置信度；当前 Tier 文本不带，需确认引擎接口。
- **真实图源未端到端跑**：与 ocr_pipeline_v2 同样，KZOCR 真实流水线未在本环境跑过（无扫描图）；cross_align 纯函数已单测，但 orchestrator 接入需真实书验证。
- **是否并行采样比对**：4.1 增强路径会显著增加 Tier2 调用成本，需按 ocr_pipeline_v2 §1.6 置信度门控/预算护栏收敛。

## 6. 任务拆分（建议）

1. ✅ cross_align.py 模块 + 单测（已完成，feat/cross-align）
2. ✅ orchestrator 接入：失败路径 Tier1/Tier3 比对 → cross_divergence（4.1；注：当前编排 Tier1 验证失败直接跳 Tier3 本地 LLM，跳过 Tier2 云端 OCR，故比对对象为 Tier3 文本；分歧落 BookDB.cross_divergence 表，纯函数无网络、失败页量小）
3. ✅ 复核队列规则落 HumanGate（4.2）：high 优先级分歧（数字/剂量、形近字）100% 经 `db.record_anomaly(detector_chain=["CrossAlign"])` 进人工复核队列。注：`conf≤0.90` 门控前置依赖引擎逐字置信度，KZOCR 归一化文本暂不带，待引擎接口补置信度后启用。
4. ⬜ VisionRecheckAdapter 升级为分歧级仲裁（4.3）
5. ⬜ cross_divergence 并入 BookDB + Web/REST 暴露（4.4）
6. ⬜ 真实书端到端验证（需扫描图源）
