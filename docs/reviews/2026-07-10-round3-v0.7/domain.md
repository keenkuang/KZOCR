# KZOCR v0.7 自适应 OCR 引擎编排层 — 第三轮领域评审（DETAILED 设计）

- **评审角色**：中医古籍 OCR / 中医药信息化领域专家
- **评审日期**：2026-07-10
- **评审版本**：v0.7 DETAILED（`docs/plans/ocr-engine-unification.v0.7-DETAILED.md`）
- **前序评审**：`docs/reviews/2026-07-10-round1-v0.7/domain.md`、`docs/reviews/2026-07-10-round2-v0.7/domain.md`
- **重点审查范围**：§5 Detector 协议（ToxinDoseDetector 剂量校验）、§4 domain_adjust 偏移规则、§2 引擎 preset priority、TermKBMatcher 假设

---

## 总体判断

**条件通过（CONDITIONAL PASS）**。DETAILED 设计在前两轮评审基础上做了扎实的落地转化：ToxinDoseDetector 的单位转换补全、domain_adjust 的加法偏移改造、竖排跳过 T1 的确定性逻辑，均体现了对领域反馈的认真吸收。本轮的审查集中于"细粒度实现决策是否符合中医古籍真实场景"，发现 3 项需要调整的领域问题（P1）和 4 项需跟踪的边界条件（P2），无 P0 阻塞项。

---

## §5 — ToxinDoseDetector 剂量校验逻辑

### 5.1 单位转换覆盖面

**设计现状**：正则匹配 `(g|克|钱)`，钱 → 克 转换系数 3.0。

**领域评估：🟡 需补充 3 种古籍常见单位**

中医古籍（特别是明/清刻本和民国抄本）中，剂量表达用的单位远多于 g/克/钱。以下 3 种在实际 corpus 中高频出现：

| 单位 | 示例 | 与克的换算 | 出现场景 |
|------|------|-----------|---------|
| **两** | "附子 三钱" → 已覆盖；"附子 五两" | 1 两 ≈ 30g（汉制，后世沿用） | 唐宋方、古法遗方 |
| **分** | "细辛 三分" | 1 分 ≈ 0.3g（十进制钱分） | 宋元方、散剂 |
| **枚** | "巴豆 三枚" | 非公制，需查询药材枚重表 | 丸散剂、雷公炮炙论 |

**影响判定**：
- **两**（P1）：毒性药材用两计量在古籍中并不罕见（如《伤寒论》中的附子"一枚"或数两）。OCR 输出"附子 五两"若不识别两单位，系统会误认为安全（因未匹配任何单位而被忽略）。实际上 5两 ≈ 150g，远超 15g 上限，应 FAIL。
- **分**（P1）：细分是中医古籍中最常见的微量单位。如"细辛 三分"≈ 0.9g 安全，但"细辛 一钱五分"（current pattern 抽提出 1.5钱≈4.5g）超上限 3g → 应 FAIL。当前 `\d+(?:\.\d+)?` 支持"一钱五分"中的 1.5 数字，但"分"单位被忽略时，实为"一钱五分"＝1.5 钱，regex 会匹配 `1.5` + `钱` → 转换正确。但若为"细辛 五分"（无"钱"），regex 匹配不到，实际剂量 5分≈1.5g 并未超限 —— 这里的问题是对"分"字单独出现时的漏报（假阴性）尚可接受，但对"两"单位的漏报（假阴性）不可接受。

**建议**：补充 `两` 单位支持（`1两=30g`），同步更新 `toxic_herbs.json` 中的剂量上限单位说明。分/枚/粒列为 P2 跟踪项。

### 5.2 正则边界安全性

```python
pattern = re.compile(rf"{re.escape(herb)}\s*(\d+(?:\.\d+)?)\s*(g|克|钱)")
```

**问题：药名与方剂名的子串匹配（P1）**

`re.escape(herb)` 只转义正则元字符，不解决子串问题。当 OCR 文本出现"附子汤 15g"时：
- `附子` 是 `附子汤` 的子串
- 正则匹配到 `附子` 后面跟着 `\s*(\d+)` —— 但"附子汤"和"15g"之间隔着"汤"字，正常来说不会匹配
- 但若 OCR 输出为"附子汤 15g"（'汤' + 空格 + '15g'），则 `附子` 后跟 `汤`，`汤\s*15g` 不会匹配 `附子\s*15g`，因为中间有"汤"字

等一下，让我重新分析。正则模式是 `附子\s*(\d+(?:\.\d+)?)\s*(g|克|钱)`。在"附子汤 15g"中：
- `附子` 匹配
- `\s*` 匹配到"汤"之前的空字符串？不，`\s*` 匹配 0 个或多个空白字符。但"附子"后面直接是"汤"（汉字，非 \s），所以 `\s*` 匹配 0 个空白，然后 `(\d+...)` 需要匹配数字，但遇到的是"汤"，匹配失败。
- 正则引擎会回溯，继续在字符串中找下一个"附子"

实际上，"附子汤 15g" 不会匹配 `附子\s*(\d+)` 模式，因为"附子"后面不是空白也不是数字。所以子串问题在实践中可能不会触发假阳性。

**但存在另一种假阳性场景（P1）**：古籍中常有并列列举多个药材的情况：

```
附子 10g, 干姜 6g, 甘草 6g
```

这种场景下正则工作正常。真正的领域风险在于：

**药名在 OCR 中产生合并/拆分误差**：OCR 将"附子"误识为"附了"（形似）时，检测器完全漏报。这在 glyph_pass_rate < 90% 的引擎中并不少见。当前设计**没有处理 OCR 字误差导致的毒药名漏检**。

**建议**：
- 在 `confusion_set.json` 中添加毒性药材的高频 OCR 误识对（如"附子↔附了/付子/附于"、"川乌↔川乌/川鸟"）
- ToxinDoseDetector 在遍历 `toxic_herbs.json` 时应同时检查 confusion_set 中的等价形近药名
- 正则建议追加 `\b`（单词边界）：`rf"(?<!\w){re.escape(herb)}(?!\w)\s*(\d+(?:\.\d+)?)\s*(g|克|钱)"`。但 Python 的正则 `\b` 有中文字符边界问题（汉字之间的 `\b` 永远为 False）。替代方案：匹配前缀不能为汉字 `(?<![^\s])` 或更稳妥的前瞻检查。

### 5.3 炮制方法与剂量上限的关联

**设计现状**：`toxic_herbs.json` 中 `HerbEntry` 为扁平结构，`max_dosage_g` 为单一标量。

**领域评估（P2）**：多个毒性药材在不同炮制方法下的安全上限差异显著：

| 药材 | 生品上限 | 制品上限 | 差异倍数 |
|------|---------|---------|---------|
| 附子 | 生用：0.5g | 制用：15g | 30× |
| 川乌 | 生用：0.3g | 制用：9g | 30× |
| 马钱子 | 生用：禁用 | 制用：0.6g | ∞ |

古籍 OCR 文本中，炮制方法在药名上下文中明确出现（如"制附子 12g""生川乌 0.2g"）。当前 ToxinDoseDetector 若用单一上限 15g 判断，生川乌 0.2g 会 PASS（误放行），制附子 12g 也会 PASS（正确）。对生川乌场景属于假阴性。

**建议**：在 `toxic_herbs.json` 中支持 `max_dosage_by_method: dict[str, float]` 结构，回退到 `max_dosage_g`。ToxinDoseDetector 在匹配时向前扫描 2-3 个字识别"制/生/炙/煅/炒"等修饰词。此条列为 P2 跟踪，不在 v0.7 阻塞范围内。

### 5.4 ToxinDoseDetector 的聚合毒性缺失（P2）

**领域观察**：一张方剂中多味毒性药材的累积毒性效应，当前设计未考虑。例如"附子 12g + 川乌 3g + 草乌 1.5g"各自均在安全上限内，但三味乌头属药材联用时的心脏毒性远超单独评估。此条列为 v0.7 后增强。

---

## §4 — domain_adjust 三条偏移规则

### 4.1 规则完整性评估

| 规则 | 当前实现 | 领域评估 |
|------|---------|---------|
| 竖排 T2/T3 +0.2 | 已实现 | **合理但可能不足**，见下文 |
| 激光照排+快速+0.1 | 已实现 | **合理**，激光排版的字符清晰度高，快速引擎已足够 |
| 方剂+高召回+0.1 | 已实现 | **方向正确但触发条件窄**，见下文 |

### 4.2 竖排偏移 +0.2 的充分性（P1）

前两轮评审的核心建议是"竖排/雕版场景下 VLM 应获得充分提权，确保不被同 tier 的低延迟引擎排挤"。DETAILED 设计用 `+0.2` 加法偏移实现。

**代入真实数据测试**：假设 Tier 2 候选引擎评分如下（base_score 范围通常 0.0~1.0）：

| 引擎 | glyph_pass_rate | avg_latency_ms | 权重衰减 | base_score | +0.2 后 | 结果 |
|------|----------------|---------------|---------|-----------|---------|------|
| sensenova | 0.95 | 12000 | 1.0 | 0.95×(1000/12000)×1.0 = 0.079 | **0.279** | ✅ 领先 |
| 某低延迟 T2 引擎 | 0.50 | 2000 | 1.0 | 0.50×(1000/2000)×1.0 = 0.250 | **0.450** | 仍领先 |

**结论**：+0.2 偏移量在 sensenova 延迟 12s、低延迟引擎 2s 的对比下不足以翻转排序。低延迟引擎的 base_score 已经 0.250，加 0.2 后 0.450 仍高于 sensenova 的 0.279。

**建议**：
- 将竖排页的偏移修改为加法+乘法混合：`base_score * 1.5 + 0.2`，或在 `page_layout.is_vertical` 且 `tier >= 2` 时，直接用 `glyph_pass_rate` 作为排序键（忽略延迟）。对于竖排古籍场景，质量优先于速度是领域硬要求。
- 或者在竖排页情况下，对 Tier 2/3 的候选引擎过滤条件改为只保留 `glyph_pass_rate > 0.8` 的引擎，然后按通过率倒序（即更严格的精度优先选择）。

此处建议的最小改动：**竖排页且 `tier >= 2` 时，将延迟的权重系数从 `1000/latency` 替换为 `log(10000/latency)`（压缩延迟影响范围），或直接对延迟为 0~10s 的引擎不惩罚。**

### 4.3 方剂高召回偏移的触发条件（P2）

当前规则 `book_type == "formula" and glyph_pass_rate > 0.9` 触发 +0.1。此规则的假设是：

> 方剂书中字符数少、稀有字比例高，引擎如果通过率高说明更适合。

这个假设在部分场景下有问题：
- 方剂书中有大量表格（君臣佐使方剂表格），T1 引擎在表格页的通过率会显著低于非表格页（受栏线/表框干扰）。`glyph_pass_rate > 0.9` 的阈值在混合版式的方剂书中将导致 T1 引擎从未触发此偏移。
- 表格页不应适用此偏移（表格页有专用编排路径更好）。

**建议**：补充 `page_layout.has_table == False` 前置条件，避免表格页中的引擎 score 受到不适用于表格的偏移影响。

### 4.4 缺少出版时代感知偏移（P2）

前两轮评审提出的"雕版/铅印本感知"在 DETAILED 中没有实现对应的正向偏移。当前只有 `pub_era == "laser"` → 快速引擎 +0.1。雕版印刷本（lead_print）的笔画断裂/墨迹不均等特征，应该对高容忍度引擎（如 VLM/T2）提供正向偏移。建议补充：

```python
if page_info.pub_era == "lead_print" and tier >= 2:
    adjustments += 0.15
```

---

## §2 — 引擎 preset priority 排序

### 2.1 全局优先级顺序评估

```
sensenova > paddleocr_vl16 > paddleocr > rapidocr > mineru > unirec > shizhengpt
```

### 2.2 对此排序的领域评估

**sensenova 第一（✅ 合理）：** SenseNova VLM 是全方案能力上限最高的引擎，冷启动期放在首位可以减少预设优先级阶段的降级次数。对竖排古籍正确，对横排现代书也正确（精度高）。

**paddleocr_vl16 第二（⚠️ 偏高）：** PaddleOCR-VL-1.6 在之前的测试（见 field fix 记录）中被发现 temp=0 下存在死循环 bug，且 v0.6 中评价为"DEGRADED"。虽然在 DETAILED 场景中 VLM 是竖排古籍的有效方案，但 PaddleOCR-VL-1.6 在 Tier 3 且延迟较高（18.7s/页，见 benchmark 列表），冷启动时排在第二可能会让它先于 paddleocr/rapidocr 被选中——对于横排现代书来说，这不是最优选择。

**建议（P1）：将 paddleocr_vl16 调至 paddleocr/rapidocr 之后：**

```
sensenova > paddleocr > rapidocr > mineru > unirec > paddleocr_vl16 > shizhengpt
```

理由：
- `sensenova` 保留第一：云端 VLM 能力上限最高，冷启动期第一选择合理
- `paddleocr` > `rapidocr` > `mineru` > `unirec`：实际 benchmark 数据（见 DETAILED §10.2）中，paddleocr 通过率 96.2%（4.2s/页）、rapidocr 94.8%（3.1s/页）、mineru 97.1%（5.5s/页）、unirec 未显示但已知低于三者。按通过率排序：mineru > paddleocr > rapidocr > unirec。
- `paddleocr_vl16` 降后：89.2% 通过率、18.7s/页 延迟、且有死循环风险，不应在冷启动期排在前列。冷启动期的前 3 次调用应优先选择稳定性高的引擎。
- `shizhengpt` 最后：无 GPU 不可用，且 benchmark 显示 UNAVAILABLE，排最后正确。

### 2.3 预设优先级在不同 Tier 内的含义

DETAILED 设计中的 `PRESET_PRIORITY` 是"全局"排序（跨 Tier 定序），但调度器的 `select_candidates()` 是按 Tier 分别调用的。这意味着：

- Tier 1 冷启动：候选集是 paddleocr、rapidocr、mineru、unirec → 按全局顺序，paddleocr > rapidocr > mineru > unirec
  - ✅ 合理：paddleocr 通过率最高（96.2%）排在 OCR 类引擎第一
- Tier 2 冷启动：候选集是 sensenova → 仅一个引擎，顺序无关
- Tier 3 冷启动：候选集是 paddleocr_vl16、shizhengpt → paddleocr_vl16 > shizhengpt
  - ⚠️ 若 paddleocr_vl16 不可用（无 GPU），shizhengpt 是唯一候选

全局顺序在 Tier 1 和 Tier 3 内有效，但 `sensenova` 排第一对 Tier 1（永远不可选）和 Tier 3（不可选）没有实际影响。建议在文档中明确标注全局顺序的生效范围（每个 Tier 内相对排序），避免读者误解。

---

## TermKBMatcher 匹配假设

### 3.1 核心假设与领域评估

TermKBMatcher 的设计基于以下假设，逐条评估：

| 假设 | 设计中的体现 | 领域评估 |
|------|-----------|---------|
| H1: 术语知识库（rare_allowlist + variant_map）可通过 Python 字符串匹配高效匹配 | `check()` 中直接比对文本内容 | ✅ 成立：rare_allowlist ~22 项、variant_map ~50 对，字符串匹配是该规模下的正确选择 |
| H2: 术语是孤立的、非重叠的 | 无重叠检测逻辑 | ⚠️ 部分不成立：中医古籍中"麻黄汤"和"麻黄"是重叠术语，"桂枝汤"和"桂枝"同理。简单字符串匹配会将"麻黄汤"中的"麻黄"同时标记为 PASS，导致"麻黄汤"整体被 correct 检测器放行 |
| H3: rare_allowlist 中的术语确认正确→应 PASS | §5.3 TermKBMatcher 返回 PASS/RARE | ✅ 合理：已知正确的稀有字直接 PASS |
| H4: variant_map 映射总是可逆的 | 无逆映射支持 | ❌ 不成立：variant_map 是"古籍字→现代规范字"的映射。TermKBMatcher 将其用于"如果在 OCR 文本中匹配到 modern 字形→判断为正常的"，但 variant_map 通常用于 normalization（归一化）而非验证。一个古籍字"薑"在 variant_map 中是"薑→姜"，但 TermKBMatcher 会尝试在 OCR 输出中寻找"姜" → 如果 OCR 输出了正确的"姜"，TermKBMatcher 实际上并不做任何事。这不是典型的"术语知识库匹配"用例 |

### 3.2 rare_allowlist 规模问题（P2）

前两轮反复提及：rare_allowlist 当前仅 ~22 条，预估实际需要 >200 条。DETAILED 设计中无 rare_allowlist 扩充计划。`TermKBMatcher.__init__` 中用 `self._enabled = bool(rare_allowlist) or bool(variant_map)`，22 条会 enable 但覆盖范围极小。

**此假设对系统行为的影响**：冷启动期（前几本书），大部分稀有 TCM 字不会被 rare_allowlist 覆盖，TermKBMatcher 频繁返回 None（无意见）→ GlyphVerifier 聚合为 PASS（无检测器命中）。正确路径：RARE/UNKNOWN 缺失 → 编排循环继续下一 tier。当前行为：全 None → `all_detectors_passed` → PASS → 放行。这是**假阴性**（稀有字错误地未被标记）。

**影响严重性**：短期内（rare_allowlist 未扩充前），TermKBMatcher 实际处于"绝大多数时间无意见"状态，对验证准确率的贡献接近于零。这不是 TermKBMatcher 结构的问题，而是资源规模问题。

**建议**：在 DETAILED 文档中明确标注 rare_allowlist 的当前覆盖度和期望覆盖度，补充一个"冷启动期"的性能预期数据（如"预计第 1~5 本书的稀有字 RARE 标记率 < 5%"），让读者对冷启动期的验证能力有清晰预期。

### 3.3 variant_map 的"假匹配"风险（P2）

variant_map 形如 `{"薑": "姜", "並": "并"}`。TermKBMatcher 在 `check()` 中对文本中的每个字——以什么逻辑匹配的？DETAILED 中的实现是 `...`（待定）。假设为"遍历 variant_map 的 key，在文本中搜索"，则：

- variant_map 中的 key 是古籍字（如"薑"）
- OCR 输出中如果出现"薑"，variant_map key 匹配成功 → RARE
- 但这种情况下，引擎正确输出了古籍字"薑"（未简化），是正确的 → 应 PASS 而非 RARE

这就是 H4 所述的映射方向问题。variant_map 的设计意图是"古籍字→现代字"（用于归一化），但 TermKBMatcher 的匹配方向恰好相反——在 OCR 输出中找古籍字→标记为 RARE。这会导致**正确的古籍字被误标**。

**建议**：
- 明确 TermKBMatcher 的匹配方向：是对 OCR 文本进行"现代字→古籍字"反向查询（在现代字在文本中出现 → 可能是正确的），还是"古籍字→现代字"正向查询（古籍字在文本中出现 → 标记 RARE）？
- 如果是前者（正确的用例），variant_map 需要补充逆映射，或使用独立的 `rare_glyph_set`（古籍稀有字形列表）
- 如果是后者（当前模棱两可），会导致前述的假阳性

**从设计意图推断**：TermKBMatcher 的职责是"匹配知识库术语，命中 PASS/RARE"。更合理的实现方向是：维护一个 `rare_glyph_set`（包含 variant_map 中所有的古籍字 + rare_allowlist 中的术语），OCR 文本中的字首次出现时查询该集合，命中 → PASS（这是已知正确的稀有字）。variant_map 用于 normalization 而非 verification。

---

## 风险跟踪表

| 编号 | 风险 | 类型 | 等级 | 当前状态 |
|------|------|------|------|---------|
| D3-1 | 剂量单位"两"未支持 → 漏报毒性剂量 | §5 遗漏 | **P1** | 未解决 |
| D3-2 | 正则对毒性药名的 OCR 误差无容错 | §5 边界 | **P1** | 未解决 |
| D3-3 | 竖排偏移 +0.2 在延迟差异大时不足以翻转 T2 排序 | §4 力度 | **P1** | 未解决 |
| D3-4 | Preset priority 中 paddleocr_vl16 排第二，冷启动期不合理 | §2 排序 | **P1** | 未解决 |
| D3-5 | TermKBMatcher 的 variant_map 匹配方向未明确，存在古籍字"假匹配"风险 | §5 逻辑 | **P1** | 未解决 |
| D3-6 | 方剂书+高召回偏移适用于表格页 | §4 边界 | P2 | 未解决 |
| D3-7 | 缺少雕版印刷本的感知偏移（仅有激光规则） | §4 遗漏 | P2 | 未解决 |
| D3-8 | rare_allowlist 规模 ~22 vs 需求 200+，冷启动期 TermKBMatcher 贡献近零 | §5 资源 | P2 | 未解决 |
| D3-9 | 剂量单位"分/枚/粒"未支持 | §5 边界 | P2 | 可接受 |
| D3-10 | 炮制方法关联剂量上限 | §5 扩展 | P2 | 可接受 |
| D3-11 | 方剂聚合毒性缺失 | §5 扩展 | P2 | 可接受 |

---

## 实施建议优先级

### Phase 2 内必须调整（P1，4 项）

1. **补充 `两` 单位支持**（§5 ToxinDoseDetector）：`(g|克|钱|两)`，`两→30g`。中医古籍中两是最常见的公制外单位。约 1 小时实现 + 测试。

2. **Preset priority 重排**（§2）：`sensenova > paddleocr > rapidocr > mineru > unirec > paddleocr_vl16 > shizhengpt`。paddleocr_vl16 的稳定性和性能数据（89.2% / 18.7s）不支持在冷启动期排第二。纯配置变更，约 15 分钟。

3. **竖排偏移增强**（§4 domain_adjust）：竖排 T2/T3 改用 `base_score * 1.5 + 0.2` 混合模式，确保高延迟高精度引擎在竖排古籍场景下不被低延迟低精度引擎排挤。约 0.5 小时实现。

4. **明确 TermKBMatcher 的方向语义**（§5）：补充 `rare_glyph_set`（古籍字集合），TermKBMatcher 据此做 PASS/RARE 判断，variant_map 仅用于 normalization，两者职责分离。约 2 小时实现 + 测试。

### Phase 3 可跟踪（P2，4 项）

5. 药材名 OCR 误差容错（confusion_set 毒性别名接入 ToxinDoseDetector）
6. 方剂偏移加 `has_table == False` 前置条件
7. 雕版印刷本正向偏移（+0.15）
8. 文档中标注 rare_allowlist 冷启动期覆盖度

### 跟踪项（当前决定合理，无需变更）

9. 其他单位（分/枚/粒）—— 未来版再议
10. 炮制方法关联剂量上限 —— 未来版再议
11. 聚合毒性 —— 未来版再议

---

## 总结

| 审查焦点 | 裁决 | 关键问题 |
|---------|------|---------|
| §5 ToxinDoseDetector 剂量校验 | 🟡 条件通过 | 缺"两"单位（P1）、药材名 OCR 误差无容错（P1）、炮制方法关联上限（P2） |
| §4 domain_adjust 三条偏移规则 | 🟡 条件通过 | 竖排 +0.2 力度不足（P1）、缺少雕版感知（P2）、方剂偏移表格兼容（P2） |
| §2 引擎 preset priority 排序 | 🟡 需调整 | paddleocr_vl16 排第二不合理（P1），建议整体按通过率+稳定性重排 |
| TermKBMatcher 假设 | 🟡 需明确 | variant_map 匹配方向未定（P1）、rare_allowlist 冷启动期覆盖度极低（P2） |

DETAILED 设计整体扎实，上述 4 项 P1 调整量均属低风险、低成本的配置/参数变更，不影响架构和核心逻辑。建议在 Phase 2（核心逻辑实施）完成前一并处理。
