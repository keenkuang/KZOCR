# KZOCR 统一 OCR 引擎架构方案 —— 第 4 轮评审汇总、闭环判定与定稿裁决

- **汇总日期**：2026-07-09
- **评审对象**：`docs/plans/ocr-engine-unification.md`（草案 **v0.2**，已吸收 round3 八角色意见 + `summary.md` 裁决）
- **输入**：8 个角色评审初稿（architect / security / performance / domain / maintainability / data_integrity / proofreading_ux / testing）
- **对比基线**：round3 `summary.md` §3（I1–I10）+ 五道硬门槛（H0-A~H0-E）
- **范围**：仅调查与文档评审，未修改主方案 `ocr-engine-unification.md`（修订由主会话执行）

---

## 1. 总评 + 定稿裁决

**v0.2 相对 v0.1 / round3 是质变性改善：round3 的全部 High/Medium 问题在方案文档层均被正面接住，无一处「未闭合」。** 五道硬门槛中 4 道在「设计意图」层已闭合，1 道（H0-A 契约冻结）仅「部分」；I1–I10 中 3 道已闭合（设计）、7 道部分闭合、0 道未闭合。八角色一致判**有条件通过**，无「不通过 / 需大修」。

**但 v0.2 暴露了一批「定稿前必须冻结」的契约 / 阈值 / 安全裁决缺口**——它们共同特征是：方案把冲突「点名」了却「推迟裁决」或「写了规划没写归属」，若带着这些模糊点进入阶段 1，会重演 round3 最担心的几类事故：Router 膨胀回单体（H0-A 名存实亡）、500 页大书单本永远跑不完（双闸互斥）、crop_img 击穿内存闸、无 GPU 默认走逐行 PaddleOCR 慢路径、allowlist 被 toml 膨胀绕过 SSRF、mock 桩无 sink 守卫重演「假古籍」、首本影印书因白名单为空而整书 UNKNOWN 淹没。

### 定稿裁决

> **有条件定稿（冻结 blocker 后方可进入阶段 1）。**
> 方案方向、分层、职责切分、降级收口、性能预算框架、目标 schema 指向均已自洽，**具备定稿成熟度**；但「定稿」这一动作（主会话修订 `ocr-engine-unification.md`）必须在本轮回合的 **8 项必改（§5）** 冻结后，才算「定稿完成」。在 §5 的 B1–B8 未落定前，**不得铺 10 个适配器**（沿用 round3 五门槛纪律）。

理由：本轮回合没有任何角色要求推倒重来，但存在 8 处「文档层必须给出确定结论 / 具体数字 / 明确归属」的缺口，它们不构成架构重设计，却是进入实现前的硬门槛。冻结成本极低（多为口径一句话 + 三处字段），不冻结的代价极高（重演已知事故）。

---

## 2. 各角色结论汇总表

| 角色 | 结论 | 一句话核心意见 |
|------|------|----------------|
| 架构师 architect | 有条件通过 | 方向质变正确，但 `AdapterPageResult→LineResult` 转换责任未指定、`glyph_status`/`glyph_verified` 二选一未裁决——两项须定稿前真冻结。 |
| 安全 security | 有条件通过 | 主张层真闭合（脱敏错觉撤除、is_mock 阻断已规划、密钥不落 toml），但 allowlist 治理缺位（新绕过面）、出境审计/同意无机制、代码残留（key 注释/M-C 权限/M-E sink）使 H0-C 仍停留纸面。 |
| 性能 performance | 有条件通过 | 预算框架已落地，但 `TOTAL_TIMEOUT` 与 `MAX_PAGES` 数学互斥（大书不可完成）、`crop_img` 内存反模式、§3 默认引擎反向（无 GPU 选 PaddleOCR 而非 VLM）——三处阈值/契约修正即可。 |
| 领域 domain | 有条件通过 | 机制正确，但 4 个资源文件（variant_map/confusion_set/toxic_herbs/候选字表）当前库里**不存在**→ RARE 永不触发、混淆集为空；且 RARE 误放行风险、闭环毒化、剂量原串 vs 红线矛盾待补。 |
| 可维护性 maintainability | 有条件通过 | 方向全对，但 run.py 未承诺 retire 为 facade（Medium-1 仍可能两处改）、KZOCR↔kimi 配置双轨未统一、映射责任悬空（与 architect 新-1 同源）。 |
| 数据完整性 data_integrity | 有条件通过 | 目标 schema 错配这一 CRITICAL 在「方向层」已真闭合（规范 `schema.prisma` 真实存在且支撑全部表），但「代码层」适配器仍写老子集；且规范 schema 未纳入 KZOCR、Line 身份映射/七类字段/glyph_status 列落点未定义。 |
| 校对 UX proofreading_ux | 有条件通过 | 规划层已把 UNKNOWN 触发/原图/severity/聚合/auditSource 全点名，但实现层全断：原图存储链路无落点、severity 列缺失、is_mock 仅 Book 级、auditSource bug 仍存活。 |
| 测试 testing | 有条件通过 | 契约文本层全吸收，但 K1 是「伪闭合」（fork 未消、types.py 无 glyph_status）、ProbeResult 字段未定义、转换责任无锚、run_engine 门面未在 §7 承诺→15 测试静默失锚。 |

> 8/8 均为「有条件通过」。跨角色共同指向的定稿前 blockers：**契约转换归属 + glyph_status 裁决（architect/testing/maintainability 同源）、双闸阈值 + crop_img + 默认引擎（performance）、allowlist 治理（security）、is_mock sink（architect/security/UX 同源）、领域种子资源（domain/data_integrity 同源）**。

---

## 3. round3 问题闭合度

> 判定口径（综合各角色）：**已闭合** = 文档层给明确自洽可落地的设计与归属；**部分** = 方向/意图已写，但有关键裁决/归属/代码层仍悬空（或仅「规划已说、机制未定」）；**未闭合** = 仍重述旧问题或未实质回应。

### 3.1 五道硬门槛（H0-A~H0-E）

| 门槛 | 闭合度 | 证据 / 说明 |
|---|---|---|
| **H0-A 契约冻结** | **部分** | §1 显式「层间唯一契约 = types.py」、§2.1 适配器改返回结构化 `AdapterPageResult`、§4.3 冻结 `glyph_status` 枚举——方向全对。残留：(a) `AdapterPageResult`（不在 types.py）「→ `LineResult/PageResult` 的转换归属」整段未指定（新缺口，见 §4-B2）；(b) `glyph_verified`(文本) vs 新增 `glyph_status`(枚举) 的取舍写「二者择一，定稿时冻结」，未裁决（§4-B1）。**含定稿前 blocker。** |
| **H0-B 共享逻辑下沉** | **已闭合（设计意图）** | §7 建 `kzocr/engines/_common.py` 下沉渲染/裁剪/后处理/跨页合并/Markdown↔pages，`BaseAdapter` 默认复用；§2.2 区分 page/book 级适配器。意图自洽，registry 可减负。实现期需逐函数给定签名与 `_common` 边界（否则逻辑泄漏进适配器）。 |
| **H0-C 安全端点收敛** | **部分（设计闭 / 机制未定）** | architect 判「已闭合（设计）」：§2.4 扩展 `_validate_url`、allowlist、DNS 复检、`vlm_host` 仅本机、§9 撤除脱敏错觉、补同意+审计。但 **security 判「部分」**：allowlist 治理缺位（新绕过面，§4-B6）、出境审计日志/同意仅有「要求」无「机制」、代码层 `modelscope_pool.py` key 注释未清（L-A）、`to_zai_prisma.py` 权限未继承 0600（M-C）、`Book` 无 `is_mock` 列且无阻断守卫（M-E）。**设计闭、机制/代码未闭，含定稿前 blocker（B6/B7）。** |
| **H0-D 目标 schema 对齐** | **部分（设计闭 / 代码未闭）** | §6 全文重写为「唯一事实源 = 规范 `schema.prisma`」，扁平子集收敛为适配层；`FinalDocumentRecord`/`ContentNode`/`FormulaComposition`/严禁重切生成新 Line——直接回应 data_integrity C1（CRITICAL）。但 **data_integrity 本轮更正**：规范 `schema.prisma` 真实存在且支撑全部表，故 round3 CRITICAL 在「方向层」已解除；**代码层**适配器 `_SCHEMA_DDL` 仍自建漂移子集、规范 schema 未纳入 KZOCR、Line 身份映射/七类字段/glyph_status 列落点未定义（§4-D*）。设计真闭合，代码与子缺口待阶段 5。 |
| **H0-E 性能预算** | **部分（框架闭 / 阈值不自洽）** | §3 把预算写入架构：单页 VLM 120s / SenseNova 90s、`TOTAL_TIMEOUT=7200s`、`MAX_PAGES=500`、`MAX_CONCURRENCY=1`、重试熔断、禁静默丢页——覆盖 P1–P5。但 **performance 判「部分」**：`7200s` 与 `500 页`在 120s/页下数学互斥（60 页即触总预算，500 页形同虚设，大书不可完成，§4-B3）；`crop_img` 内存反模式（§4-B4）；§3 默认引擎反向（无 GPU 选 PaddleOCR 而非 VLM，§4-B5）。**含 3 个定稿前 blocker。** |

### 3.2 跨角色 High/Medium 问题（I1–I10）

| # | round3 问题 | 闭合度 | 证据 / 说明 |
|---|---|---|---|
| **I1** | 层间契约缺失（`str`）+ `glyphVerified` 语义冲突 | **部分** | 适配器改返回结构化（§2.1）、显式引用 types.py（§1）——已解决「str 过弱」「弃用 types.py」。但 `glyph_verified`/`glyph_status` 仍「二者择一，定稿时冻结」（未裁决，testing 称「伪闭合」）；且 `AdapterPageResult→LineResult` 转换口子未堵（§4-B1/B2）。 |
| **I2** | `run.py` 共享逻辑无去处 + kimi 接口不对齐 | **已闭合（设计）** | §7 `_common.py` 下沉 + §2.2 `BookPipeline` 作 `BookLevelAdapter` 薄封装 shim、`AdapterMeta` 由 KZOCR 注入。设计自洽；实现期确认 kimi `*_adapter` 不被当顶层引擎重写。 |
| **I3** | 共识职责重叠 + 跨引擎行对齐不可行 | **部分** | 职责已厘清（Router 候选装配写 `engine_texts`，GlyphVerifier 多数票裁定）。但异构引擎「块级行对齐键机制」仍无正文描述、仅列「阶段 2 前置」（对齐难点未在本方案正文落地，靠阶段 2 兜底）。 |
| **I4** | 出境最小化伪命题 + SSRF | **部分** | 设计层：脱敏错觉已撤（§0/§1/§9）、端点 allowlist/DNS 复检、细粒度同意+审计已写入 §9。但 security 指出：allowlist 治理缺位（可被配置层膨胀，新绕过面）、审计/同意无机制、跨云 consensus 仍默认允许（N≤2≠默认禁）。 |
| **I5** | 归档 schema 错配 + 重切丢 bbox | **部分** | 设计层：`ContentNode`/`FormulaComposition`/`FinalDocumentRecord` 已转正，重切生成新 Line 被明文禁止（直接回应 C1）。但 data_integrity 指出：适配器仍写老子集（代码未闭）、规范 schema 未纳入、Line 身份映射未定义、七类字段规范 schema 无家。 |
| **I6** | 罕见中医字误判 + 缺归一化 + `UNKNOWN` 漏放 | **已闭合（规划）** | §4.2 加 `normalize()`、`RARE` 态（命中候选字表不进人工队）、形似混淆集（FAIL）；§5 HumanGate 显式补列 `UNKNOWN`（原漏放点已堵）。**但 domain 指出「数据悬空」**：候选字表/混淆集当前库里为空→RARE 永不触发、混淆集为空，首本实书仍会复现 K1 淹没（§4-D*）。规划闭合、数据落点待补。 |
| **I7** | mock 回退静默发假数据（重演 H8） | **部分（source 端已正确 / sink 端未动）** | 代码核对：`mock.py:147` 已置 `is_mock=True`，且 run.py 降级路径正调用它——**source 端比 round3 假设更优**。残余在 **sink 端**：`to_zai_prisma.py` 的 `Book` DDL 无 `is_mock` 列、`push_book_to_zai` 无阻断守卫（规划已写、代码未动）。必须阶段 1 落地（§4-B7）。 |
| **I8** | 性能预算缺失 | **部分** | 框架已落地（从零到 KZOCR_* 全套变量 + 熔断 + 进程内 KB），是 round3 头号缺口根本闭合。但双闸阈值不自洽（B3）+ 默认引擎反向（B5）使框架在真实大书上行为异常。 |
| **I9** | 方剂七字段 + khub 闭环 + 毒性告警 | **已闭合（设计）/ 数据悬空** | §6 以规范 `FormulaComposition` 为准、补齐七类字段、剂量保留原串、内置 `toxic_herbs.json` + 红线。但：① 七类字段规范 schema 实际无家（data_integrity N4）；② 闭环回填无审核 gate（domain N2 毒化）；③ 红线与「原串保留」矛盾（domain N3）。 |
| **I10** | 配置迁移 + 降级收口 + 可观测性 | **已闭合（设计）/ 双轨未合** | §8 假设 5（集中 schema + 加载期校验 + toml 仅覆盖层 + 密钥不进 toml）；§3 降级收口 Router、可观测性统一。但 maintainability 指出：KZOCR↔kimi 配置双轨未统一审计（kimi `engine_configs` 另一套 schema、不经 KZOCR allowlist，§4-M*）。 |

**闭合度小结**：五门槛 0 未闭合（H0-B 最闭，H0-A/C/D/E 均含部分残留）；I1–I10 中 3 已闭合（设计）、7 部分、0 未闭合。**round3 的每一处都被 v0.2 正面接住，这是相对 v0.1 的本质进步；但「设计层闭合、代码层/阈值/机制层未闭」是本轮主旋律，且 I6/I9 的「机制对、数据空」构成落地期最大隐患。**

---

## 4. v0.2 新引入且必须 freeze 的问题（去重 + 严重度 + 是否 blocker）

> 去重合并 8 角色「新引入」清单。严重度：🔴 高 / 🟠 中 / 🟡 低。**blocker = 定稿（主会话修订方案）前必须冻结，否则不进阶段 1。**

### 4.1 架构 / 契约（architect 2 项 residual + 同源项）

- **B1 🟠 中 · `glyph_verified`(文本) vs `glyph_status`(枚举) 未裁决（推迟 fork）** — architect 新-2、testing K1（伪闭合）、data_integrity N5 同源。**blocker**。§4.3 写「二者择一，定稿时冻结」把冲突推迟。建议采「并存」确定结论：保留 `glyph_verified` 作校验后文本，`glyph_status` 新增枚举独立列，二者正交不冲突，删「或迁移」开放句。
- **B2 🔴 高 · `AdapterPageResult → LineResult/PageResult` 转换责任缺位** — architect 新-1（削弱 H0-A）、maintainability 新-1、testing N1 同源。**blocker**。§1 称「层间唯一契约 = types.py」，但 `AdapterPageResult` 不在 types.py，且从页 `text` 切行 / 按 `AdapterMeta.name` 填 `engine_texts` / 把 `char_confidences` 对齐到 `char_level_json` 的装配逻辑 nowhere assigned。建议指定「EngineRouter（或 `_common.py: adapter_page_to_line_result`）是唯一归属」，Verifier/Archiver 不再做文本切分。
- **M-a 🟠 中 · book-level 适配器与字形校验层边界未定义** — architect 新-3。**非 blocker（实现期）**。§2.2 须补：book-level 适配器必须返回已填 `engine_texts`/`confidence` 的 `LineResult`，统一进 GlyphVerifier，其「内部校验」不替代本方案质量门。
- **M-b 🟡 低 · §5「Book 表增 is_mock/source 列」措辞与现状不符** — architect 新-4。`source` 列已存在承载 `engine_label`，仅增 `is_mock`。文档精度，随 B7 一并修。

### 4.2 性能（performance 3 项阈值 / 内存冲突）

- **B3 🔴 严重 · `KZOCR_TOTAL_TIMEOUT=7200s` 与 `KZOCR_MAX_PAGES=500` 阈值不自洽** — performance N1。**blocker**。并发 1、单页 ~120s 下 7200/120≈60 页即触总预算，500 页上限永远先被卡死，且任何 >60 页书单本不可完成。建议方案 A：**保留 `TOTAL_TIMEOUT=7200s`，`MAX_PAGES` 改为 ~150**（7200/45≈160），并明文「`TOTAL_TIMEOUT` 是真实 SLA 上限，`MAX_PAGES` 仅防快页内存尖峰」；另明确预算检查点在每次页尝试（含重试）前后各查一次。
- **B4 🟠 中 · `AdapterPageResult.crop_img: np.ndarray` 长期驻留内存** — performance N2、testing N3 同源。**blocker（击穿自身内存闸）**。500 页 × 4.5MB ≈ 2.25GB 直接击穿 H0-E 主张的内存闸。建议契约：`crop_img` 仅作瞬时可选字段，持久 UX/recheck 一律走 `crop_path: str | None` + `bbox`；视觉适配器默认不回填 `ndarray`，仅 recheck/HumanGate 真正需要时按需重渲染。
- **B5 🟠 中 · §3 默认引擎选择与 round3 性能结论反向** — performance N3。**blocker（重演慢路径）**。§3 写「无 GPU → 本地非视觉 PaddleOCR」，但 round3 实测 PaddleOCR CPU 逐行 1–2s/行近乎不可用、无 GPU 唯一可行路径是 VLM 整页推理（PaddleOCR-VL-1.6）。建议反向：无 GPU 首选 VLM，PaddleOCR 仅作「无 VLM/无 llama-server」兜底；§2.3「VLM 默认禁用」同步放开 `auto_start` 端口绑定约束（仍须绑 127.0.0.1）。
- **L-a 🟡 低 · 进程内 KB 启动耗时 × 每 CLI 重探**（N4）/ **客户端 120s 超时不能取消服务端生成**（N5）：文档注明边角即可，不阻塞。

### 4.3 安全（security 新缺口）

- **B6 🔴 高 · allowlist 治理缺位（新引入绕过面）** — security N1。**blocker（H0-C 子门槛）**。v0.2 要求 allowlist 但未界定：①来源（若 toml 可增域名，攻击者改 toml 即把自身域名加入白名单，SSRF 守卫形同虚设）；②env 提供的 `SENSENOVA_BASE_URL` 等直接进 Config 不经 allowlist；③DNS 复检与 allowlist 比对时序。建议：allowlist 为**代码内冻结常量**（不可经 toml/env 增删）；所有 `base_url`（含 env 注入）建连前解析 IP 并经「域名 + IP 网段」双校验、协议 https 拒绝明文、connect 前二次比对缩窄 TOCTOU。
- **M-c 🟠 中 · 出境审计日志 + 逐书/逐页同意仅有「要求」无「机制」** — security H-B/N2/N3。**非 blocker（阶段 1 落地）**。建议 §9 定义审计落点（复用 `KnowledgeAuditLog` 或 append-only 日志）、字段（page→provider→time→bytes→consent_id）、留存；`allow_cloud_vision=true` 无审计能力应拒绝启动；`--consent-cloud` 须产可审计 consent 记录（书级 ID+时间戳+provider 白名单）。
- **M-d 🟠 中 · 跨云 consensus「合规不可证」却仍默认允许（N≤2≠默认禁）** — security N3。**非 blocker（建议强化）**。跨 ≥2 家云端 provider 的 consensus 须显式逐书 consent，否则拒绝；N≤2 上限保留。
- **M-e 🟡 低 · `vlm_host` 远端判定/鉴权未机制化** — security N4/M-B。建议 `auto_start` llama-server 加 `--api-key`（env 注入）、`vlm_host` 仅 127.0.0.1/localhost/Unix socket 放行、远端按出境处理须经 consent。
- **M-f 🟡 低 · 代码残留（L-A key 注释、M-C 导出权限 0600）** — 建议从阶段 6 提前到阶段 1 一并清；归档落盘物统一 `0600`。

### 4.4 领域 / 数据完整性（资源悬空 + 新缺口）

- **B7+ 🔴 高 · 领域资源文件悬空（事实源为空）** — domain（最该补）、data_integrity N1 同源。**blocker（阶段 3 落地前提）**。当前库中 `variant_map.json`/`confusion_set.json`/`toxic_herbs.json`/「中医候选字表」**均不存在**，机制正确但开局空表 → RARE 永不触发、混淆集为空、毒性表为空，首本《本草纲目》影印书原样复现 K1 淹没。建议定稿承诺：**4 个资源文件随包内置种子数据**（混淆集 ≥15 对含 severity、候选字表含萆薢/䗪虫…、toxic_herbs 含附子/细辛≤3g 等）；且规范 `schema.prisma` 须 vendore/pin 进 KZOCR（data_integrity N1），适配器从规范 schema 派生而非手搓 `_SCHEMA_DDL`。
- **M-g 🟠 中 · RARE 误放行 + 闭环回填毒化** — domain N1/N2。**非 blocker（阶段 3/5）**。RARE 须过字级置信门（`char_conf ≥ 阈值`，低置信转 UNCERTAIN）+ 每本抽 5% QA；闭环回填设两级审核 gate（人工复核/累计 N 次确认才升正式白名单），term_kb/khub 路径因 kimi 破损态暂不启用。
- **M-h 🟠 中 · 剂量「原串保留」与「红线可判定」自相矛盾** — domain N3/K7。**非 blocker（阶段 5）**。`FormulaIngredient` 同时存 `dosageRaw` + 解析 `dosageValue/unit`，红线规则结构化（`max_grams`/`must_processed`）比对解析值。
- **M-i 🟠 中 · Line 身份映射未定义（C2 延伸）** — data_integrity N2。**非 blocker（阶段 5）**。明确 KZOCR `LineResult` → 规范 `Line(paragraphId)` 稳定键（建议 `(bookCode,pageNum,paraSeq,lineSeq)` 派生确定性 id），跨引擎重跑复用同键，避免重复/悬空。
- **M-j 🟠 中 · 七类字段 / glyph_status 在规范 schema 无家** — data_integrity N4/N5。**非 blocker（阶段 5 前置）**。v0.2 §6.4 既称「以规范 schema 为准」又要补规范里不存在的七类列 / `glyph_status` 列——须二选一冻结：给规范 schema 加 `formulaFieldsJson` 迁移 + `glyphStatus` 列，并在方案注明这是对规范 schema 的扩展而非「已存在」。
- **M-k 🟡 低 · TOC-less 影印/手写本 fallback（K5 延伸）、variant_map 两档制（K2）、ContentNode 依赖 TOC 分析器（N3）、khub 同步无承载（N6）** — 均阶段 5 实现期补，不阻塞定稿。

### 4.5 校对 UX / 测试 / 可维护性（其余新缺口）

- **M-l 🟠 中 · 原图裁剪「出境合规 vs 看原图」冲突** — UX 新-UX-1。**非 blocker（阶段 4 前裁决）**。§5 把裁剪图推校对台，与「裁剪非脱敏」定性自相矛盾。建议：原图裁剪仅本机/局域网校对台可用，跨境降级为 bbox + 本地取图代理。
- **M-m 🟠 中 · mock 行级可区分性缺位（Book 级 `is_mock` 不够）** — UX 新-UX-2。**非 blocker（阶段 4）**。`LineResult.engine_texts` 值结构升级为携带 `is_mock`/`source`；`Line` 表增 `isMockLine`，使行级可区分（根治 I7 行级残留）。
- **M-n 🟠 中 · `run.py` 未承诺 retire 为 facade + 15 测试迁移表弱化** — maintainability Medium-1、testing K5。**非 blocker（阶段 1）但建议定稿写入**。§7 须显式「`run_engine` 改为仅调 `EngineRouter.run()` 的薄门面，删 mock/vlm/real 三路分支与 `_init_vlm_adapter` 硬编码降级；现有 15 测试经 facade 迁移」——否则 registry 减负仍是「两处改」。
- **M-o 🟠 中 · KZOCR↔kimi 配置双轨未统一审计** — maintainability 新-2。**非 blocker（阶段 1）**。指定 KZOCR `Config.engines.<name>` 为唯一真相源，kimi `engine_configs` 由 KZOCR 从同一 Config 单向翻译注入（或显式划清审计边界并复用 `_validate_url`）。
- **M-p 🟡 低 · `ProbeResult` 字段未定义** — testing N2/K3。**非 blocker（阶段 2）**。§3 补 `ProbeResult` dataclass 字段表（gpu/vram_gb/cpu_cores/ports/keys/allow_cloud_vision），Router 接受注入。
- **M-q 🟡 低 · RARE/白名单/混淆集 优先级链未定义** — testing N4。**非 blocker（阶段 3）**。§4.2 用编号显式声明优先级链（白名单 > 混淆集 > RARE 候选 > UNKNOWN），含空库推 UNKNOWN 不 FAIL 的硬约束。

---

## 5. 定稿前必改清单（actionable，进入阶段 1 的门槛）

> 以下 B1–B8 为**定稿动作本身（主会话修订 `ocr-engine-unification.md`）必须冻结**的项；其余 M-* 为阶段 1–5 实现期落地，方案只需写明归属/数字/机制。

**B1 — 冻结 `glyph_status`/`glyph_verified` 为「并存」确定结论**（§4.3）
删「二者择一，定稿时冻结」开放句，改：保留 `glyph_verified`（校验后文本），新增 `glyph_status: Literal[PASS|RARE|UNKNOWN|FAIL|UNCERTAIN]` 独立枚举列，二者正交并存；并注明 `types.py` 与 `Line` 表均增 `glyph_status`/`glyph_verified_reason` 两列（呼应 data_integrity N5 / testing K1）。

**B2 — 指定 `AdapterPageResult → LineResult` 装配归属**（§2.1 或 §3）
新增「装配契约」小节：EngineRouter（或 `_common.py: adapter_page_to_line_result`）是唯一转换归属——把每页 `AdapterPageResult` 装配为 `LineResult`：`engine_texts[AdapterMeta.name]=text`、`confidence` 取行/页级、`char_confidences` 按字符序序列化进 `char_level_json`、块级结构由 `ParagraphResult.node_type/heading_level` 承载；Verifier/Archiver 不再做文本切分（呼应 architect 新-1 / maint 新-1 / testing N1）。

**B3 — 重算并自洽双闸**（§3）
采用方案 A：保留 `KZOCR_TOTAL_TIMEOUT=7200s`，`KZOCR_MAX_PAGES` 由 500 改为 **150**（7200/45≈160 圆整）；明文「`TOTAL_TIMEOUT` 是真实 SLA 上限，`MAX_PAGES` 仅防快页内存尖峰」；预算检查点在每次页尝试（含重试）前后各查一次（呼应 performance N1）。

**B4 — `crop_img` 契约改「路径/bbox 优先」**（§2.1 + §5）
`AdapterPageResult.crop_img` 仅作瞬时可选字段（类型保留但注释「不耐久驻留」）；持久 UX/recheck 一律经 `crop_path: str | None` + `bbox`；视觉适配器默认不回填 `ndarray`，仅 recheck/HumanGate 真正需要时按需重渲染。§5 HumanGate 的「原图裁剪」明确为路径优先（呼应 performance N2 / testing N3）。

**B5 — 反向默认引擎选择**（§3）
无 GPU 时首选 VLM（PaddleOCR-VL-1.6），PaddleOCR（本地非视觉）仅作「无 VLM/无 llama-server」兜底；与 §2.3「VLM 默认禁用」同步放开 `auto_start` 端口绑定约束（仍须绑 127.0.0.1）（呼应 performance N3）。

**B6 — 冻结 allowlist 为代码常量**（§2.4）
allowlist 定为 `kzocr/engines/_common.py` 或 `config.py` 内**不可经 toml/env 增删的常量**；所有 `base_url`（含 env 注入的 `SENSENOVA_BASE_URL` 等）在建连前解析 IP 并经「域名 + IP 网段」双校验、协议 https 拒绝明文、connect 前二次比对缩窄 TOCTOU（呼应 security N1）。

**B7 — 冻结 `is_mock` sink 端落地**（§5 / §7 阶段 1）
方案明确：`to_zai_prisma.py` 的 `Book` DDL 增 `is_mock` 列（非「增 is_mock/source」，因 source 已存在承载 engine_label）；`push_book_to_zai` 加 `is_mock=True` 阻断守卫；作为「阻断 publish 假古籍」最后一块拼图（呼应 architect 改进建议3 / security M-E / UX C2 / I7 sink 端）。

**B8 — 承诺领域资源文件随包内置种子 + 规范 schema 纳入**（§4.1 / §6 / §7）
定稿声明：4 个资源文件（`variant_map.json` ≥200 等价异体、`confusion_set.json` ≥15 对含 severity、`toxic_herbs.json` 含附子/细辛≤3g 等、`rare_allowlist` 候选字表含萆薢/䗪虫…）**随包内置种子数据、非空表冷启动**；规范 `schema.prisma` 须 vendore/pin 进 KZOCR，适配器从规范 schema 派生而非手搓 `_SCHEMA_DDL`（呼应 domain 最该补 / data_integrity N1）。

> 上述 B1–B8 冻结后，v0.2 即具备「定稿完成、进入阶段 1」资格。实现期还需落地 M-a~M-q（已在 §4 标注归属阶段），其中 M-n（run.py retire 门面）、M-o（kimi 双轨真相源）、M-c/M-d（审计/同意机制）、M-l/M-m（原图合规 + 行级 mock）建议优先在阶段 1 一并落地，以防 round3 隐患以新形态复发。

---

## 6. 对 v0.2 第 8 章 6 项假设裁决的再确认

> 8 角色一致确认：v0.2 §8 对 round3 六项裁决的吸收**全部准确、无走样**，且把「默认 single」「KZOCR 内置白名单」等提升为硬约束/事实源。本轮**无一项被推翻**，仅对边界补强。

| # | 假设（round3 裁决） | round4 再确认 | 是否推翻/调整 |
|---|---|---|---|
| **1** | 字形校验暂不加独立再识别视觉模型（采纳 + 预留 VisionRecheck 挂点、recheck 仅限本地） | 8 角色全确认正确。architect/security/UX/testing 均确认「recheck 仅限本地」避免云端出境放大。 | **维持**，仅补强：挂点须落到 `LineResult.crop_img`（依赖 B4）否则无图可看（testing N1/N3）。 |
| **2** | 最小小节可配置 + 按 book_type + 经 contentNodeId 挂载、禁重切 | 领域/UX 确认方向贴合中医书结构（针灸=穴/本草=药/临床=证/方）。 | **维持**，补充：须补 **TOC-less 影印/手写本 fallback**（domain K5、M-k）；导出一致性（export_zai 仍扁平）本轮仍未触及。 |
| **3** | 方剂主链只写 zai（规范 FormulaComposition）、khub 异步可选不阻塞 | 数据完整性本轮**强化**该裁决：khub 当前无任何方剂表/client，故「只写 zai」是唯一可落地路径，事实层安全。 | **维持**，补充：须注明「khub 同步是待立项扩展点、当前主链仅写 zai」，且跨库一致需前瞻补 `FormulaSyncOutbox`（H1/N6）；闭环回填须加两级审核 gate 防毒化（domain N2）。 |
| **4** | consensus 成本：默认 single、无 GPU 全本地 consensus 拒绝启动、含云端 N≤2、单引擎 UNKNOWN/低置信补触发 | 性能/测试/UX 全确认 v0.2 落地更严格可 CI 校验。 | **维持并强化**：含 ≥2 家云端 provider 的 consensus 须显式逐书 consent（security N3）；且依赖 B5（默认引擎选对）方能完整兑现性能收益。 |
| **5** | 配置集中 schema + `Config.engines.<name>` + 加载期校验 + toml 仅覆盖层 + 密钥不进 toml | 可维护性/测试/安全确认方向正确。 | **维持，范围收窄**：须补「KZOCR↔kimi 配置唯一真相源（KZOCR 单向翻译注入 kimi）」（maint 新-2）；toml 覆盖层**不得膨胀 allowlist**（依赖 B6，security N1）。 |
| **6** | 字形知识库：KZOCR 内置白名单为事实源、term_kb 仅可选增强 | 领域/测试/安全确认。 | **维持，但事实源当前为空表**——须随包内置种子（B8）方为真事实源；`KZOCR_TERM_KB_PATH` 须校验受控目录（防路径穿越）。 |

**再确认结论**：round3 六项裁决**全部维持，无推翻**；本轮仅对假设 2（补 TOC-less fallback）、3（补 Outbox 前瞻 + 审核 gate）、4（补跨云 consent）、5（补 kimi 双轨真相源 + allowlist 锁定）做了边界补强，且补强项均已在 §4/§5 的 M-* 与 B6/B8 中落到可执行。

---

## 附：核心结论摘要（给用户）

1. **建议有条件定稿**——v0.2 相对 v0.1/round3 是质变性改善，round3 全部问题在文档层均被正面接住（五门槛 0 未闭合、I1–I10 仅 3 已闭 7 部分闭），八角色一致「有条件通过」，方向、分层、降级收口、性能框架、目标 schema 指向均已自洽，具备定稿成熟度。
2. **但「定稿」动作本身须先冻结 8 项 blocker（B1–B8）**，否则不得进入阶段 1：其中最大的 blocker 是 **`glyph_status`/`glyph_verified` 二选一未裁决 + `AdapterPageResult→LineResult` 转换责任悬空**（二者共同削弱 H0-A 契约冻结，会让 Router 重新膨胀为单体），以及**性能双闸阈值互斥（`TOTAL_TIMEOUT` 与 `MAX_PAGES=500` 使大书单本永远跑不完）**。
3. 另外三处定稿前必改：**allowlist 治理缺位（可被 toml 膨胀绕过 SSRF，新引入绕过面）、`is_mock` sink 端未落地（mock 桩仍可能无守卫重演「假古籍」）、领域 4 个资源文件当前库里为空（首本影印书会原样复现 UNKNOWN 淹没）**。
4. round3 六项假设裁决**全部维持、无推翻**，仅补强边界（TOC-less fallback、跨云 consensus 显式 consent、kimi 双轨唯一真相源、allowlist 锁定、白名单随包内置种子）；v0.2 §8 对裁决的吸收准确无走样。
5. 一句话给主会话：**先按 §5 的 B1–B8 修订 `ocr-engine-unification.md` 并冻结，再铺 10 个适配器；其余 M-* 在实现期（阶段 1–5）按标注归属落地即可。**
