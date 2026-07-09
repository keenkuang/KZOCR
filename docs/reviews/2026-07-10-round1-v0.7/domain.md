# KZOCR v0.7 自适应 OCR 引擎编排层 — 领域评审

- **评审角色**：中医古籍 OCR / 中医药信息化领域专家
- **评审日期**：2026-07-10
- **评审版本**：v0.7（第一轮）
- **依据文档**：`docs/plans/ocr-engine-unification.v0.7.md`
- **上下文代码**：`kzocr/engine/types.py`、`kzocr/engine/run.py`、`kzocr/resources/*.json`、`kzocr/config.py`、`kzocr/engines/errors.py`

---

## 总体判断

**条件通过（CONDITIONAL APPROVE）** — 架构方向正确，三级兜底的设计思想与中医古籍 OCR 的实际场景匹配。但存在 **6 项领域特定问题** 需要在实施前解决或明确方案边界，另有 **3 项领域概念遗漏** 需要补充设计。整体方案成熟度约 65%，核心风险集中在 GlyphVerifier 对中医古籍异体字/特殊版式的覆盖度不足，以及人工校对衔接过于薄弱的闭环断裂。

---

## 逐项评审

### 1. 三级兜底设计是否符合中医古籍 OCR 实际场景？

**裁决：通过，但需补充竖排/混合版式的处理说明**

| Tier | 设计 | 领域评估 |
|------|------|---------|
| T1：OCR 引擎（paddleocr/rapidocr/mineru/unirec） | 通用 OCR | 对横排现代重印本 OK；对古籍影印本/竖排本识别率极低（<30%） |
| T2：云端视觉 LLM（SenseNova/SiliconFlow VLM） | 视觉理解 | 能力强但受限于：①数据出境合规（古老方剂/秘方是否允许出域）②成本（970页×3轮兜底）③需网络 |
| T3：本地中医 LLM（shizhengpt/paddleocr_vl16） | 领域定制 | 最匹配场景但严重依赖 GPU；PaddleOCR-VL-1.6 在 temp=0 下有死循环 bug（已有 field fix） |

**领域核心观察：T1 到 T2 存在"断崖式"能力跳跃。**

竖排中医古籍（明刻本/清抄本）在 T1 引擎中几乎全部失败（通用 OCR 引擎不支持竖排），这意味着 Tier 1 对所有竖排古籍的实际通过率 ≈0%。这将导致全部负载直接压到 T2/T3，使三级设计的"分级降级"效果大幅削弱。

**建议：**
- 在调度器中增加 `page_layout` 感知：若页级布局分析检出竖排 → 跳过 T1 直接进入 T2（减少无效调用）
- 文档中明确标注 T1 引擎的**竖排能力空白**，避免用户对"三级"产生全场景覆盖的误解
- 考虑未来在 T1 中引入竖排 OCR 引擎（如 PP-OCRv4 竖排模式、PaddleOCR 的 `lang="chinese_cht"`）

### 2. GlyphVerifier 是否足以支撑中医古籍异体字/形似混淆等特有需求？

**裁决：需修订 — 当前设计在异体字覆盖面和验证深度上不足**

**当前资源规模（严重不足）：**

| 资源文件 | 条目数 | 覆盖度评估 |
|---------|-------|-----------|
| `variant_map.json` | ~50 对 | 仅覆盖最常见的繁简/异体对。中医古籍实际跨时代异体字 >500 种 |
| `confusion_set.json` | ~25 项 | 对中医方剂高频混淆场景有覆盖（如"我术→莪术"），但规模过小 |
| `rare_allowlist.json` | ~22 项 | 覆盖了少数稀有字（萆薢、䗪虫等），但完整中医古籍稀有字清单 >200 项 |

**三个领域特定缺失：**

**缺失 1：偏旁层级匹配（Unihan 部首分解）**

中医古籍的异体字大量存在于偏旁级别的变体（如"艹-⺾"混用、"辶-⻌"混用），而不是整字变异。当前 variant_map 是"整字→整字"映射，对未见过的异体字无能。

建议在 GlyphVerifier 中增加 **部首分解回退**：当 OCR 输出字不在 variant_map 中时，分解为部首 + 剩余部件，检查部首是否属于已知异体偏旁集合。

**缺失 2：TCM 领域词频感知**

当前 `RARE` 的判定逻辑缺少 TCM 领域词频上下文：

- "砒"在现代汉语中罕见（→容易误判 RARE/UNKNOWN），但在中医矿物药中是高频字
- "瘛瘲"（chì zòng）在现代汉语几乎不出现，但在儿科/m病证章节是日常用词

应引入 **TCM 领域字符频率表**（可从 corpus/ 中统计），使 GlyphVerifier 对"在通用语中罕见但 TCM 常见"的字自动 PASS。

**缺失 3：上下文消歧**

形似混淆的正确与否严重依赖上下文：

- "己/已/巳"：孤立判断不可能分清，但若上下文为"己 亥"、"已 煎"、"巳 时"则可消歧
- "白术" vs "白木"：与剂量（3~15g vs 不存在）和治法上下文有关

当前 GlyphVerifier 只接收 `page.context` 但 plan 中未明确如何使用。建议至少支持 **bigram/trigram 上下文匹配**。

### 3. 方案是否存在领域概念遗漏？

**裁决：需补充 — 存在 3 项显著遗漏**

#### 遗漏 A：竖排（直行排版）

如第 1 项所述，Tier 1 OCR 引擎全都不支持竖排。这是中医古籍最常见的版式。v0.7 的架构完全没有体现对竖排的应对策略。

**影响：** 如果 v0.7 的目标是"统一编排"，就必须说明竖排场景的编排策略。当前方案在此处留白。

#### 遗漏 B：眉批 / 夹注 / 旁注

中医古籍常有：
- **眉批**（上方批注）：读者/医家在书眉处的按语
- **夹注**（行间双行小字）：在正文行间的注释（如《本草纲目》中的"某某曰"等）
- **旁注**（侧边标注）：药性说明、配伍禁忌

这些属于**非正文内容**，应：
1. 标记为独立段落（`node_type="annotation"` / `"marginalia"`）
2. 不应与正文混淆进入 glyph verification 的主流程
3. 可能需要 T2/T3 引擎专门处理（T1 几乎无法识别小字）

`ParagraphResult` 的 `node_type` 当前只有 `text/heading/formula/list_item/quote`，建议增加 `annotation` 和 `marginalia`。

#### 遗漏 C：方剂表格（田字格/栏线表格）

中医方剂书中常见：
```
┌─────────┬──────┬──────────┐
│ 当归    │ 12g  │ 君       │
│ 川芎    │ 10g  │ 臣       │
│ 白芍    │ 10g  │ 臣       │
│ 熟地黄  │ 15g  │ 使       │
└─────────┴──────┴──────────┘
```

T1 OCR 引擎识别这类表格结构的能力很差（方框线干扰、行列对应混乱）。当前方案没有为表格页设计单独的编排路径。表格页的 GlyphVerifier 也需要特殊处理（药名+剂量+君臣佐使三元组的一致性和合理性校验）。

### 4. 调度器按历史字形通过率选候选的策略是否合理？

**裁决：需修订 — 在中医古籍场景下有多项盲点**

当前排序公式：`glyph_pass_rate × (1/avg_latency)`

**领域特定问题：**

1. **延迟权重惩罚视觉 LLM**：SenseNova（T2）的延迟是 OCR 引擎的 10-50 倍，按此公式 T2/T3 永远排不到候选前列，与"视觉 LLM 才是竖排古籍实际有效引擎"的领域现实矛盾。建议引入**场景权重**：竖排页 → T2/T3 优先（或跳过 T1）。

2. **"通过率"分母盲点**：一个只识别了 10 个字且全对的 OCR 引擎，其通过率为 100%，远高于处理了 1000 个字且错了 20 个的引擎（98%）。但前者对古籍几乎毫无产出。建议**加权通过率 = glyph_pass_count / max(total_pages, min_pages_threshold)**，或引入**单页平均识别字数量**作为辅助指标。

3. **无出版时代感知**：`BookResult` 已有 `pub_era`（`lead_print` / `transition` / `laser`），但调度器完全未使用。雕版印刷本（lead_print）的笔画断裂/模糊特征严重，应优先选容忍度高的引擎。

**建议的领域特定排序：**

```python
def domain_sort_key(engine, page_info, book_info):
    base = engine.stats.glyph_pass_rate
    # 场景调整
    if page_info.is_vertical and engine.kind in ("tier1_ocr"):
        base *= 0.3  # 竖排对T1降权
    if book_info.pub_era == "lead_print" and engine.kind == "tier3_vlm":
        base *= 1.5  # 雕版对VLM提权
    if page_info.has_table and engine.kind in ("tier1_ocr"):
        base *= 0.5  # 表格对T1降权
    # 通过率置信度调整
    if engine.stats.total_pages < 10:
        base *= 0.8  # 冷启动降权
    return base
```

### 5. 领域资源在 GlyphVerifier 中的使用是否充分？

**裁决：需优化 — toxic_herbs 和 variant_map 的使用方式有改善空间**

| 资源 | 当前使用 | 领域评估 | 建议 |
|------|---------|---------|------|
| `variant_map.json` | 归一化映射 | 充分但未考虑**上下文相关性** | 在特定古籍中，"薑"（非简化的古体）是风格标记而非"需要归一化"的文本，建议添加`scope: book` 限定 |
| `confusion_set.json` | 标记 UNKNOWN | 标记为 UNKNOWN → 进入 T2/T3，设计合理 | 但 UNKNOWN→T2 再识别可能仍错（VLM 也有天花板），建议把 confusion_set 直接作为 **纠错修正映射** 独立应用 |
| `rare_allowlist.json` | 标记 RARE | **RARE 不够**——已知正确的稀有字应 PASS | 建议对 rare_allowlist 中的 term，当全文匹配（含上下文确认）时直接 PASS 而非 RARE |
| `toxic_herbs.json` | 仅校对台使用 | **严重未充分利用** | OCR 将"草乌 5g"误识为"草乌 6g"或"早乌 5g"都是致命错误。建议 GlyphVerifier 对剂量附近出现的 herb 名优先做 confusion_set 匹配；对判断为 toxic_herb 的条目标记 severity="critical" |

**新增建议资源：**
- **TCM 字符频率表**：从已识别的 TCM 书籍语料中统计字符频率，用于 RARE 判定（200+ 条）
- **剂量合理性规则**：`toxic_herbs.json` 已有 `max_dosage_g` 但未被验证器使用。OCR 出来的"附子 45g"（最大 15g）应在验证器层面直接 FAIL

### 6. 人工校对衔接设计是否合理？

**裁决：需修订 — 当前设计过于薄弱，缺少反馈闭环**

```python
# 当前 HumanGate（过于简单）
if verdict.status in ("FAIL", "UNKNOWN"):
    failed_pages[page.num] = f"All engines/modes failed. Last: {verdict.details}"
    continue
```

**问题清单：**

1. **不做优先级区分**：FAIL 页（全引擎失败）、RARE 页（稀有字但可能正确）、UNCERTAIN 页（字符数异常）混合在一起。人工校对者需要清晰的分级指引。

2. **无结构化证据输出**：`failed_pages` 只有一行字符串，没有：
   - 每级引擎的识别结果（便于对比）
   - 对应页面截图路径（`crop_img_path` 已存在于 `LineResult` 但未被传递）
   - 哪些 glyph 触发了 FAIL，confusion_set 中哪条规则命中

3. **无反馈回路**：人工校对的修正（现有 `ProofreadRecord` 结构已支持 `change_type=glyph/dosage/herb...`）没有写回 variant_map / confusion_set 的机制。这意味着每本书的人工修正无法帮助后续书籍的字形验证。

4. **`human_final` 字段未参与编排**：`LineResult.human_final` 已定义，v0.7 的编排器没有使用它。

**建议补充人工校对衔接设计：**

```
人工校对输出数据结构（建议在 v0.7 中加入 review_manifest）：
{
  "book_code": "mifangqiuzhen-970",
  "pages_for_review": [
    {
      "page_num": 23,
      "priority": "P0",          # P0=FAIL, P1=UNKNOWN, P2=RARE
      "engine_results": {        # 每级引擎的产出
        "t1_paddleocr": "当 归 12g 川 芎 ...",
        "t2_sensenova": "当归12g，川芎10g...",
        "t3_shizhengpt": "..."
      },
      "crop_img_path": "/tmp/kzocr/output/xxxx/crops/p023.png",
      "issues": [
        {"position": 5, "ocr_char": "术", "expected": "术",
         "issue_type": "glyph", "severity": "warning"}
      ]
    }
  ],
  "human_corrections_feedback_path": "review_feedback.json"
}
```

并定义 `feedback_apply()` 函数将人工修正记录中的新知识反向同步到 `variant_map` 和 `confusion_set`。

---

## 领域特有风险

| 风险 | 等级 | 说明 |
|------|------|------|
| **竖排盲区** | P0 | T1 引擎全不支持竖排，导致竖排古籍的实际兜底链降为 T2→T3→Human，仅 2 级有效 |
| **异体字覆盖面不足** | P1 | variant_map 仅 50 对 vs 实际需要 500+，冷启动期 UNKNOWN 数量过高，HumanGate 将被淹没 |
| **Toxic herb 剂量缺漏** | P1 | toxic_herbs.json 已有结构化数据但未被 GlyphVerifier 使用，OCR 将附子 15g 误为 45g 无法被检出 |
| **RARE 判断缺少领域频率** | P1 | TCM 稀有字被过度标记为 RARE/UNKNOWN，增加不必要的 T2/T3 调用和人工审查 |
| **人工校对无反馈闭环** | P1 | 每本书的知识积累不能正向传递给下一本，长期运营成本线性增长 |
| **表格页编排路径缺失** | P2 | 方剂表格页 T1 识别率极低，且 GlyphVerifier 无表格行对应校验，结构化方剂数据可能不可用 |
| **T1→T2 调用量失控** | P2 | 若 T1 全部失败且无竖排跳过机制，T2/T3 的 API 成本可能膨胀至不可接受水平 |

---

## 建议汇总

### 实施前必须解决（P0）

1. **竖排感知调度**：在调度器中增加 `page_layout.is_vertical` 判断逻辑，竖排页跳过 T1 直接进入 T2，并在文档中明确标注 T1 的竖排能力上限。

2. **GlyphVerifier 补充偏旁层级回退**：当整字不在 variant_map 中时，尝试 Unihan 部首分解匹配。

### 需要补充设计（P1）

3. **toxic_herbs 剂量合理性校验接入 GlyphVerifier**：利用已有 `max_dosage_g` 字段，对药名+剂量组合做合理性断言；剂量超标的 glyph 直接 FAIL。

4. **引入 TCM 领域字符频率表**：从已处理语料中统计字符频率，使 GlyphVerifier 在 TCM 常见但通用罕见的字符上正确 PASS。

5. **人工校对反馈闭环**：补充 `review_manifest` 输出结构和 `feedback_apply()` 回写机制，将 `ProofreadRecord` 中确认的 glyph 纠错自动纳入 variant_map / confusion_set。

### 实施中优化（P2）

6. **调度排序算法增加领域感知权重**：纳入 `pub_era`（雕版→VLM 提权）、`is_vertical`（竖排→T1 降权）、最低识别页数阈值（防冷启动误导）。

7. **rare_allowlist 标记策略调整**：将已知正确的稀有术语从 RARE 改为 PASS，减少不必要的 T2/T3 调用。

8. **`ParagraphResult.node_type` 扩展**：增加 `annotation` / `marginalia` / `table` 类型，支撑眉批/夹注/方剂表格的结构化处理。

---

## 总结

| 评审项 | 裁决 | 关键问题 |
|--------|------|---------|
| 1. 三级兜底设计 | ✅ 条件通过 | 竖排导致 T1 实际 ⊥，需补竖排感知调度 |
| 2. GlyphVerifier 支撑力 | ❌ 需修订 | 异体字覆盖面 50/500+，缺偏旁层级匹配和领域频率感知 |
| 3. 领域概念遗漏 | ❌ 需补充 | 竖排/眉批夹注/方剂表格三个结构性遗漏 |
| 4. 调度器排序策略 | ❌ 需修订 | 延迟权重惩罚 VLM，无 pub_era/布局感知 |
| 5. 领域资源使用 | ⚠️ 需优化 | toxic_herbs 剂量校验未用，rare_allowlist 过保守 |
| 6. 人工校对衔接 | ❌ 需修订 | 无优先级/无结构化证据/无反馈闭环/无 human_final 接入 |

**总体领域评价：**

v0.7 的自适应编排架构方向正确，EngineRegistry + Scheduler + GlyphVerifier + Orchestrator 四层结构足以支撑未来的引擎扩展。但方案对**中医古籍的竖排版式、罕见字的频率分布、方剂表格的结构化处理**这三个核心领域特性缺乏充分认知，导致 GlyphVerifier 的设计在中度复杂的古籍场景下可能被 UNKNOWN/FAIL 淹没。

三个最关键的领域建议按优先级实施：
1. **竖排感知调度**（P0）—— 否则 T1 在竖排古籍上毫无价值
2. **toxic_herbs 剂量校验**（P1）—— 安全底线，OCR 误读毒药剂量可造成临床风险
3. **人工校对反馈闭环**（P1）—— 避免知识积累随每次扫描清零
