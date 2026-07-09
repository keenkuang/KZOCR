# 中医领域视角评审：OCR 引擎统一架构方案（草案 v0.1）

> 评审人角色：中医文献与术语领域专家
> 评审对象：`docs/plans/ocr-engine-unification.md`（重点 §4 字形校验、§6 归档/方剂库、§8 假设 2/3/6）+ `docs/plans/toc-driven-pipeline-design.md` + `kzocr/adapter/to_zai_prisma.py` schema
> 立场：只评审，不改主方案/代码。

## 结论

方案在"工程解耦 / 可切换 / 质量门 / 人工兜底"四原则上站得住，但**领域贴合度不足**，有三处会直接损害中医书籍（尤其方剂书、针灸书）的识别准确率与知识价值：

1. **字形校验会把大量"正确但罕见的中医字"误判 UNKNOWN**，人工复核量爆炸，质量门形同虚设；且缺繁简/异体→正体的归一化，繁体影印本整体失效。
2. **方剂库 schema 只持久化了"方名 + 组成"，丢掉了用法/功用/主治/方解/加减/疗效/附记七类核心字段**——方剂书最宝贵的临床信息未结构化。
3. **"khub 方剂系统 / term_kb 闭环"目前是悬空的**：khub 代码里既没有 Formula/Term 表，client 也只推整篇文档；回流机制完全未定义。

建议：在 round3 的"阶段 3 字形校验""阶段 5 归档/方剂库"补强领域层，并把"闭环"从口号落成一条可执行的回流 job，否则按现状落地，方剂书与针灸书的归档质量会明显低于预期。

## 关键问题（按严重度）

### 严重 / High

**K1 字形校验对罕见中医字的误判（§4.2 步骤 2）— High**
方案步骤 2：`c ∉ 已知集但属合法 Unicode CJK 且领域词典可接纳（如新见药名）→ UNKNOWN，进入待确认`。这本意是"宁可错杀"，但对中医书是灾难：方剂书/本草书里充满生僻但正确的字，如 萆薢(bì xiè)、薤白(xiè bái)、藁本(gǎo běn)、苁蓉、茺蔚子、楮实、蒺藜、菝葜(bá qiā)、䗪虫(土鳖虫)、虻虫、蛴螬、蜣螂、蝼蛄、鳢肠、蟾蜍、蘡薁(yīng yù) 等。它们多落在 **CJK Ext-A/B**，即便属合法 Unicode，也多半不在 term_kb 的"已知字形集"里——于是每一处都被标 UNKNOWN 推人工。一本《本草纲目》影印本可能生成上万条 UNKNOWN，校对台被淹没，质量门失效。
→ 必须区分"罕见但合法（放行 + 记录）"与"可疑错字（送检）"，不能一律 UNKNOWN。

**K2 缺繁简/异体→正体的归一化步骤（§4.1 提及但 §4.2 未用）— High**
§4.1 提到知识库含"异体字/繁简映射"，但 §4.2 的逐字校验是**直接拿原始字 c 比对已知集合**，没有先归一化。后果：影印古籍常是繁体——黨參/黃連/蒼朮/白朮/當歸——而 KB 多半是简体，整本被标 UNKNOWN。中医还有大量异体/通用字：朮(白朮) vs 术、菟(菟丝子) vs 兔、薁 vs 奥、蘡 vs 婴、栝楼 vs 括楼。
→ 校验前必须先做"繁→简 + 异体→正体"映射再比对；映射本身也应是可回流扩展的资源。

**K3 方剂库 schema 漏掉七类核心字段（§6.4 + to_zai_prisma.py）— High/Critical**
`to_zai_prisma.py` 的 `Formula` 表只有 `(id, bookCode, formulaName, sourcePages, createdAt)`，`FormulaIngredient` 只有 `(herbName, dosageValue, unit, roleInFormula, isToxic)`。即只有**方名 + 组成**被持久化。而方剂书最关键的 用法(煎服法)/功用(功效)/方解(方义)/主治/加减/疗效/附记 七类字段，在 `Formula`/`FormulaIngredient` 里**完全没有列**——它们只活在 `final_markdown` 文本里。
这违背了方案第 5 条原则"方剂书额外入方剂库"的领域价值：入库的方只剩药单，没有煎服法与主治，临床不可直接用。`toc-driven-pipeline-design.md` 的 `Formula.fields: dict` 已定义这 9 类，但 zai 落库 schema 没承接。

**K4 "khub 方剂系统 + term_kb 闭环"悬空（§6.4 / §9 / 假设 3/6）— High**
- khub 侧 `khub-m1/khub/models.py` 只有 `Attachment / RawDoc / CanonicalDoc / SyncResult` 四张表，**没有 Formula/Term/Pattern/药名表**；`khub/client.py` 的 `push_document` 只推整篇 markdown 文档。方案反复引用的"khub 方剂系统""khub / term_kb 闭环"目前**无对应实现**。
- 回流机制未定义：zai 校对台有 `Proofread(originalText, correctedText, changeType, triggeredPattern)`，但**没有任何 job 把 Proofread 聚合成 Term/Pattern 并回写字形知识库**。§9 只说"知识库持续回流"，没说谁来做、写到哪、scope 怎么定。

### 中 / Medium

**K5 最小小节 ≠ TOC 三级标题（§6.3 / 假设 2）— Medium**
方案默认"以 TOC 三级标题为最小单元"，但中医书组织多样：
- 针灸/经络书：最小知识单元是**穴**（合谷、足三里），常按"经→穴"或"部位→穴"组织，TOC 三级可能对应"经"而非"穴"，会把同经多穴塞进一节；
- 本草/药学书：最小单元是**药**（每药一条目：性味/归经/功效/主治/用法用量）；
- 临床/证治书：常按"证→方→药"或"病→证→方"。
`BookResult.book_type` 只有 `formula/clinical/classic/textbook`，**缺 meridian/herb**。应让最小单元与 `book_type` 联动，拆分策略可插拔。

**K6 方剂抽取边界与责任方不明（§6.4）— Medium**
- 方案没说"谁做方剂结构化抽取"。`toc-driven-pipeline-design.md` 用 DeepSeek 后处理抽 9 类字段；统一方案 §6.4 只说"抽取方剂名/组成/剂量"，未指定在 Archiver 用 LLM 还是后处理阶段。这层必须明确是 **LLM/后处理职责，不是 GlyphVerifier**。
- 边界易错：`附方`、`上方加减`、`变方`、`一方治多病` 不能误拆成新方；剂量里的 `各15克`、`等分`、`适量`、`少许`、`10–15克`、`三剂` 需保留原串而非硬解析成数值。

**K7 毒性药材剂量误 OCR 有临床风险（§6.4 + FormulaIngredient.isToxic）— Medium（领域安全）**
`isToxic` 字段已定义是好的，但：①抽取时要用"毒性药名表"打标（附子/乌头/马钱子/砒霜/蟾酥/斑蝥/生半夏/细辛等）；②细辛"不过钱"、附子须炮制、生川乌禁直接内服等**用量红线**应作为告警规则。这一步目前完全没提，而错剂量在方剂书里是直接危害。

**K8 形似混淆集应是中医专用（§4.2 步骤 3）— Medium**
方案举例用通用混淆（未/末、已/己）。中医高频混淆完全不同：莪术↔我术、川芎↔川穹、黄芩↔黄芪/茯苓(芩/苓)、栀子↔框子、半夏↔半下、紫菀↔紫苑、薤↔韭、葶苈↔亭历、白朮↔白木。应维护**中医专用形似混淆集**作为 FAIL 判据，而非通用集。

### 低 / Low（建议）
- **K9（低）** 异体/生僻字应在 KB 显式列举样例（朮/术、蘡薁、䗪虫），便于评审覆盖度。
- **K10（低）** 方剂跨页（组成跨页）需在 Formula 保留 `page_range`；`toc-driven` 已用 `<!-- page N -->` 推算，统一方案应继承。

## 改进建议（具体、举例）

**A. 字形校验：加"归一化 + 罕见放行"两道闸（针对 K1/K2/K8）**
1. 校验前先做 `normalize(c)`：繁→简（opencc 或自带映射）+ 异体→正体（自带 `variant_map.json`，仅收明确等价项，如 `{朮:术}` 需审慎，菟丝子之类不可误并）+ 全角/旧字形归正。归一后比对。
2. 结果三态重定义：
   - 归一后在已知集 → PASS（记 `auditSource=dictionary`）；
   - 归一后不在已知集、但属合法 CJK Ext-A/B **且**命中"中医候选字表（生僻药名/穴名字表）" → `RARE`（放行归档，标 `glyphVerified=PASS, auditSource=rare_allowlist`，**不入人工队**）；
   - 形似混淆集命中（莪术↔我术等）→ FAIL/UNCERTAIN 送检；
   - 其余未知 → UNKNOWN 送检。
3. KZOCR 自带一份精简"中医字形/异体白名单 + 形似混淆集"，**不依赖 kimi 侧 term_kb 的内部覆盖**（见 K4），term_kb 仅作可叠加的额外来源。

**B. 方剂库 schema 补齐七类字段（针对 K3）**
在 `Formula` 表增加核心必存列（临床最常用）与余下字段 JSON 列，使落库与 `Formula.fields` 对齐：
```sql
ALTER TABLE Formula ADD COLUMN usage TEXT;        -- 用法/煎服法
ALTER TABLE Formula ADD COLUMN gongyong TEXT;     -- 功用/功效
ALTER TABLE Formula ADD COLUMN zhuzhi TEXT;       -- 主治
ALTER TABLE Formula ADD COLUMN fangjie TEXT;      -- 方解
ALTER TABLE Formula ADD COLUMN fields_json TEXT;  -- 来源/加减/疗效/附记 等余下字段
```
并让 `to_zai_prisma.py` 的 `Formula` 写入承接 `FormulaEntry` 的字段（需在 `types.py` 的 `FormulaEntry` 增加 `fields: dict`，目前只有 `ingredients`）。

**C. 把"闭环"落成一条回流 job（针对 K4）**
新增阶段 5.5 `CorrectionIngestion`：
- 读 zai `Proofread` + `Pattern`（按 `changeType`：glyph/herb/meridian/negation/dosage）；
- 聚合为 `Term/Pattern` 候选，按 `scope`（global/publisher/book/era）分级；
- 写回 KZOCR 自带字形 KB（rare_allowlist / variant_map / confusion_set）+ 毒性药名表；
- 若未来 khub 真有方剂系统，再同步；**当前不要假设 khub 已具备**，方案应标注"khub 方剂库为后续依赖，round3 不假定其存在"。

**D. 最小单元按 book_type 分流（针对 K5）**
- 扩展 `book_type`：`formula | clinical | classic | textbook | meridian | herb`；
- 拆分策略注册表：`formula→(节=病, 小节=方)`、`meridian→(经→穴)`、`herb→(药条目)`、`clinical→(证/病)`；
- TOC 三级仅作默认候选，针灸书可下钻到"穴"级（按穴位名正则如 `^\s*[一-龥]{1,4}穴\s*$` 或"经名 + 序号"）。

**E. 方剂抽取责任与边界（针对 K6/K7）**
- 明确抽取在"后处理/LLM 阶段"，GlyphVerifier 只做字级校验、不解析语义；
- 剂量保留原串（`dosageValue` 允许 `"各15"`/`"等分"`/`"适量"`/`"10-15"`），单位单独抽；
- 毒性表：内置 `toxic_herbs.json`（附子/乌头/马钱子/砒霜/蟾酥/斑蝥/生半夏/细辛…），抽取时打 `isToxic=1` 并触发"用量红线"告警（细辛≤3g、附子须炮制等规则可先为占位）。

## 对第 8 章设计假设的立场

- **假设 2（字形校验机制，暂不加再识别视觉模型）— 同意**。中医字罕见且 KB 难全，加视觉再识别收益有限、成本高；先把归一化 + 白名单 + 混淆集做扎实更划算。
- **§8 假设 2（最小小节定义：TOC 三级即最小单元）— 不认同**。应改为"按 book_type 选定最小单元（方/穴/药/证），TOC 三级仅作默认"，见 K5/D。
- **假设 3（方剂库归属：zai Formula 表 vs khub 方剂系统）— 立场：先落 zai，khub 不假定存在**。当前 khub 无方剂/术语表、client 只推文档（K4），强行"同步 khub 方剂系统"会把不存在的依赖写进方案；建议标注 khub 方剂库为后续里程碑。
- **假设 6（字形知识库来源：复用 kimi term_kb vs KZOCR 内置白名单）— 立场：KZOCR 自带精简中医字形/异体/混淆白名单为系统事实源，kimi term_kb 仅作可选叠加**。理由：①term_kb 覆盖未知且跨仓库耦合（假设 6 自身也犹豫）；②中医专用混淆集/异体映射必须可控可评审（K1/K2/K8）。
