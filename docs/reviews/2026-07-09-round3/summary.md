# KZOCR 统一 OCR 引擎架构方案 —— 第 3 轮评审汇总与修订清单

- **汇总日期**：2026-07-09
- **评审对象**：`docs/plans/ocr-engine-unification.md`（草案 v0.1）
- **输入**：8 个角色评审初稿（architect / security / performance / domain / maintainability / data_integrity / proofreading_ux / testing）+ 主方案第 8 章 + round2 整改背景（H1–H8 已落地）
- **范围**：仅调查与文档评审，未修改主方案 `ocr-engine-unification.md`（修订由主会话执行）

---

## 1. 总评

**方案整体可行，8 个角色一致判「有条件通过」**——相比 round2（4 个角色判「不通过」），H1–H8 整改已落地后方案健康状况显著改善，分层解耦（接引擎 / 选引擎 / 质量门 / 人工兜底）的方向与协议面（`recognize_page`/`recognize_pages` 两方法）被普遍认可。

**主要风险定位（按必须进入实现前的硬门槛优先级排序）：**

1. **契约与共享逻辑（架构 / 可维护性，贯穿阶段 1）**：层间数据契约未引用已存在的 `kzocr/engine/types.py`，适配器只返回 `str` 会丢失置信度/多源信息；`run.py` 约 200+ 行跨引擎共享逻辑无去处，不先下沉则 registry 无法真正消除对 `run.py` 的改动（解耦收益被抵消）。
2. **数据出境最小化伪命题 + 端点 SSRF（安全，阶段 1–2）**：版心裁剪不构成脱敏，全局开关缺细粒度同意/审计，新增的云端/本地适配器 `base_url`/`vlm_host` 完全无端点校验——这是新架构引入的「适配器即出境通道」面。
3. **归档层目标 schema 错配（数据完整性，阶段 5，CRITICAL）**：方案依据的 `to_zai_prisma.py` 扁平子集与规范 `schema.prisma` 在表名/列名/隔离键上严重不符（如 TOC 树 `ContentNode`、方剂 `FormulaComposition` 早已存在，方案却要「新增」），按扁平子集实现会导致校对台、原图回溯、sha256 归档全部落空。
4. **字形校验对罕见中医字误判 + UNKNOWN 漏放（领域 + UX，阶段 3–4）**：罕见但正确的中医字（萆薢、䗪虫…）会被批量标 UNKNOWN 淹没校对台；且 `UNKNOWN` 未纳入 HumanGate 触发，构成「错字自动放行进库」的漏放路径，直接违背原则 4。
5. **性能预算缺失（性能，贯穿）**：无 GPU 下 consensus 不成立、VLM 无总超时/并发/熔断，长尾页可拖垮整本；`KZOCR_MAX_PAGES=2000` 仅是内存闸非 SLA 闸。

**结论：可进入实现的先决条件是先闭合「契约冻结 + 共享逻辑下沉 + 安全端点收敛 + 目标 schema 对齐 + 性能预算」五件事，再谈 10 个适配器铺开。**

---

## 2. 各角色结论汇总表

| 角色 | 结论 | 一句话核心意见 |
|------|------|----------------|
| 架构师 architect | 有条件通过 | 分层方向正确，但层间契约缺失、`types.py` 弃用、共识职责重叠、mock 回退与 H8 反向、TOC 分析器未落地，须在进入阶段 1 前闭合。 |
| 安全 security | 有条件通过 | 默认关云端 + 密钥不落地守住了底线，但「版心裁剪=数据最小化」是伪命题、出境开关缺细粒度同意/审计、新增适配器端点无 SSRF 校验，阶段 1–2 须落地。 |
| 性能 performance | 有条件通过 | 第 8 章假设多数成立，但无 GPU 前提未贯彻：consensus 不成立、VLM 无总超时/并发/熔断、字形校验若逐字符查库会是隐藏瓶颈，须把性能预算补为第 0 优先级。 |
| 领域 domain | 有条件通过 | 工程四原则站得住，但字形校验会淹没罕见中医字、缺繁简/异体归一化、方剂库漏七类临床字段、khub/term_kb 闭环悬空，阶段 3/5 须补领域层。 |
| 可维护性 maintainability | 有条件通过 | 方向正确，但 kimi 内部适配器被当顶层引擎用、接口签名未对齐、`run.py` 共享逻辑无去处、降级/可观测性零规划，不先解决会变跨仓库双维护 + 逻辑僵尸化。 |
| 数据完整性 data_integrity | 有条件通过 | 第 6 章在「目标 schema 是什么」上系统性错配（扁平子集 vs 规范 `schema.prisma`），须回到规范 schema 重写后才能落地（C1 为 CRITICAL）。 |
| 校对 UX proofreading_ux | 有条件通过 | 人工兜底意图正确，但漏放（UNKNOWN 不触发）、校对台无原图、无优先级/批量聚合、闭环回填未定义，前两项属严重级。 |
| 测试 testing | 有条件通过 | 解耦方向正确，但 `glyphVerified` 字段语义冲突、适配器缺置信度通道、`probe` 不可注入，须先冻结三套契约（适配器结构 / 路由纯函数 / 校验枚举）再实现。 |

> 8/8 均为「有条件通过」，无「不通过 / 需大修」。但 data_integrity 的 C1、security 的 H-A/H-B、performance 的 P1–P3、architect 的 K1–K4 须作为进入阶段 1 的硬门槛。

---

## 3. 跨角色高优先级问题（去重合并）

> 下列条目为被 ≥2 个角色共同指出、或单角色判 CRITICAL/严重 的最高优先级问题。严重度（高/中/低）与归属阶段（阶段 0=文档修订，1–6=实施阶段）。

### [高] I1 — 层间契约缺失：适配器返回 `str` 过弱，且未引用已存在的 `types.py`；`glyphVerified` 字段语义冲突
- **归属角色**：架构师 K1/K5、测试 K1/K2；数据完整性 H3 亦涉及落库字段。
- **阶段**：0–1 / 3。
- **核心**：草案 §2.1 适配器只返回 `str`，丢失调度所需的逐行 `engine_texts`、逐字 `confidence`、`char_level_json`、段落/标题结构——而这些恰是 `kzocr/engine/types.py` 已存在的 `LineResult`/`BookResult` 所承载。同时 §4.3 把 `Line.glyphVerified` 定义为状态枚举，与 `types.py` 现有「存校验后文本」语义硬冲突（现有 `mock.py`、导出、落库、CLI 全按文本消费）。
- **影响**：若不在实现前冻结契约，EngineRouter 会膨胀为新的单体（重演 `run.py` 600 行问题），且现有 15 个测试失锚。

### [高] I2 — `run.py` 共享逻辑无去处 + kimi 适配器接口未对齐（registry 无法真正减负）
- **归属角色**：可维护性 High-1/2/3、架构师 K6/K5。
- **阶段**：1。
- **核心**：`run.py` 约 200+ 行跨引擎逻辑（渲染/裁剪/后处理/跨页合并/Markdown 重建）方案未说搬去哪；kimi 的 `*_adapter` 是 `BookPipeline` 内部子组件（签名 `recognize_page(page_img, prompt=None)->str`），与方案 `recognize_page(img)->list[str]` 漂移。「搬出 run.py」若不先下沉共享逻辑，会变成「改 run.py + 改 router」两处，registry 消除硬编码分支的承诺不成立。

### [高] I3 — 共识职责重叠 + 跨引擎逐行对齐未定义（consensus 不可行且不可测）
- **归属角色**：架构师 K2/K8、测试 K4、性能 P1。
- **阶段**：2–3。
- **核心**：§3 策略 B 与 §4.2 第 4 条都声称拥有「跨引擎逐行一致/多数票」计算权，无单一归属。且 local-nonvision（裸文本）与 vision（Markdown 带标题/空行）换行点不同，直接「逐行比对」在异构引擎间不可行，也无法写交叉比对测试。

### [高] I4 — 数据出境最小化伪命题 + 全局开关缺细粒度同意/审计 + 适配器端点无 SSRF 校验
- **归属角色**：安全 H-A/H-B/M-A/M-D/M-E（全为安全角色，但属方案核心破口）。
- **阶段**：1–2。
- **核心**：版心裁剪只切白边不剔除任何文字，不构成脱敏（合规层面属「声称最小化实则全量出境」）；`allow_cloud_vision` 单一全局布尔，开启后 consensus 会让一页图像同时送多家云端，且无逐书/逐页同意、无 PII 脱敏、无出境审计日志；新增的云端 `base_url`/本地 `vlm_host` 完全无端点校验，`allow_cloud_vision` 只控「是否发」不控「发往谁」，篡改 toml 即可把图像外泄给攻击者。

### [高] I5 — 归档层目标 schema 错配（扁平子集 vs 规范 `schema.prisma`），且最小小节切分丢失原图回溯
- **归属角色**：数据完整性 C1/H1/H2/H3（C1 为 CRITICAL）、校对 UX C2 亦涉及原图。
- **阶段**：5–6。
- **核心**：规范 `schema.prisma` 已含 `ContentNode`（即 TOC/Section 树）、`FormulaComposition`（含 alias/root/referenced/crossPage）、`FinalDocumentRecord(sha256)`、`BookRegistry.status` 等，但方案基于扁平子集声称「需新增 TOC/Section 表」「全文→Book.final_markdown（该列不存在）」。从 `final_markdown` 重切生成「新 Line」会丢失 `Line.id`/`pageNum`/`charLevelJson(bbox)` 原图回溯，人工校对「看原图」核心诉求失效。

### [高] I6 — 字形校验对罕见中医字误判 UNKNOWN（淹没校对台）+ 缺繁简/异体归一化 + `UNKNOWN` 漏放（未触发 HumanGate）
- **归属角色**：领域 K1/K2/K8、校对 UX C1/H5/M5。
- **阶段**：3–4。
- **核心**：方剂/本草书充满生僻但正确的字（萆薢、䗪虫、蘡薁…），多落 CJK Ext-A/B 不在 term_kb，会被批量标 UNKNOWN 推人工；且 §4.2 未先做繁→简+异体→正体归一化（黨參/黃連 整本 UNKNOWN）。更严重：§5 HumanGate 触发条件只列 FAIL/UNCERTAIN，**漏列 UNKNOWN**，使「新字/新药材名」无定义出口，要么被当可放行、要么静默滞留——直接违背原则 4「绝不静默放行错字」。

### [高] I7 — 降级/桩数据（mock）缺乏强制标识透传，重演 round2 H8「publish 假古籍」
- **归属角色**：架构师 K4、安全 M-E、性能 P3、校对 UX C2（跨 4 角色）。
- **阶段**：1/4。
- **核心**：§9 把「回退到 use_mock 跑通全链路」当卖点，却未要求置 `is_mock`、未要求 ERROR 日志、未要求阻断 publish；`Book` 表无 `is_mock` 列，校对员无法区分 mock 演示与真实结果。统一 EngineRouter 的多候选降级若不对候选文本标 `source/is_mock`，会与真实结果混同进 `engine_texts` 与共识。

### [高] I8 — 性能预算缺失：无 GPU 下 consensus 不成立 + VLM 无总超时/并发/熔断
- **归属角色**：性能 P1/P2/P3/P4（全为性能角色，P1–P3 标严重）。
- **阶段**：1–3。
- **核心**：两个本地 CPU 引擎并行纯内耗（VLM `-t 10` 占满核，PaddleOCR 逐行 1–2s/行）；VLM 单页 `timeout=600s`、纯串行、无 wall-clock 总预算，大书可占机数天；单页失败即 `continue` 丢页，无重试/退避/熔断。

### [中] I9 — 方剂库 schema 漏七类核心字段 + khub/term_kb 闭环悬空 + 毒性剂量告警缺失
- **归属角色**：领域 K3/K4/K6/K7、数据完整性 H2、校对 UX H4。
- **阶段**：5。
- **核心**：`Formula` 仅持久化方名+组成，丢用法/功用/主治/方解/加减/疗效/附记七类；khub 当前无 Formula/Term 表、client 只推整篇文档，「闭环」无机制；毒性药材（附子/细辛…）剂量错 OCR 有临床风险却无告警规则。

### [中] I10 — 配置存放/迁移未定 + 降级链治理收口未定 + 可观测性零规划
- **归属角色**：可维护性 Medium-3/4/6、架构师 K9、安全（端点审计）。
- **阶段**：1–2。
- **核心**：§8 假设 5 倾向每适配器 toml 会让配置真相源碎片化（且 KZOCR↔kimi 已双轨），不利于集中审计出境端点；降级顺序散落各适配器 vs 收口 Router 未决；方案全文未提可观测性（无法回答「走哪条候选、耗时多少、降几次级」）。

---

## 4. 对方案第 8 章 6 项假设的裁决建议

> 用户最关心的决策点。每条给出「采纳 / 调整 / 否决」及理由（综合 8 角色立场）。

### 假设 1 — 字形校验机制：以「字典/知识库+置信度+多引擎共识」为主，暂不加独立再识别视觉模型
- **裁决：采纳（默认不加独立再识别视觉模型），但调整**
- **理由**：8 角色中 7 个明确同意默认（性能视角：再识别=对 FAIL/UNKNOWN 行二次跑 VLM，CPU 下成本爆炸）。调整点：(a) 须在协议层预留 `VisionRecheckAdapter`/`recheck(line, crop_img)` 挂点，避免未来补时需重构 Router；(b) 对 FAIL/UNKNOWN/UNCERTAIN 兜底行**回看版心裁剪图并随行推送**（既是校验补强、亦是 UX C2 刚需），且 recheck **仅限本地视觉引擎**执行——云端路径下再识别会放大出境面（与安全目标冲突）。

### 假设 2 — 最小小节定义：以 TOC 三级标题为最小单元，还是更小
- **裁决：调整（不钉死「三级标题」，改为可配置 + 按 `book_type` 选定 + 经 `contentNodeId` 挂载）**
- **理由**：领域与 UX 均反对纯三级标题（针灸最小单元是「穴」、本草是「药」、临床是「证/方」）；数据完整性反对从 `final_markdown` 重切段落/行（丢失 bbox 回溯）。采纳方向：①最小小节 =「任意级别标题或方剂/穴位块界定的 Markdown 块」，粒度经 `min_section_level` 可配置；②归属通过 `Paragraph.contentNodeId` 挂载到 `ContentNode`，**严禁重切生成新 Line**；③默认候选仍为 TOC 标题块。

### 假设 3 — 方剂库归属：写 zai `Formula` 表即可，还是必须同步独立 khub 方剂系统
- **裁决：调整（主链只写 zai，khub 异步可选、不阻塞；且必须用规范 `FormulaComposition` 而非扁平 `Formula`）**
- **理由**：多数角色（架构/可维护性/测试/性能未持立场但反对强耦合、领域、数据完整性）主张先落 zai、khub 异步可选。结合事实——khub 当前无方剂/术语表、`client` 只推整篇文档、且记忆提示 khub/真实引擎处不稳定态，强行「必须同步 khub」会把不存在的依赖写进方案。否定 proofreading_ux 的「必须同步 khub」强制主张。但补充：跨库一致前提两张库先补 `version`+`checksum`，同步键用规范化名+别名图+来源书，单向 zai→khub 导入。

### 假设 4 — consensus 模式成本：无 GPU 下是否默认仅 single
- **裁决：采纳（默认 single，consensus 仅 opt-in，且提升为硬约束）**
- **理由**：8 角色一致支持默认 single（安全：云端多 provider 加剧出境面；测试：CI 确定性；性能：无 GPU 下全本地 consensus 纯内耗）。强化为硬约束：①`strategy.mode` 默认 `"single"`；②无 GPU 且全本地 CPU 引擎 consensus → **拒绝启动并告警**；仅当含云端视觉引擎时允许 consensus 且 `N≤2`；③单引擎模式须以 `UNKNOWN`/低置信度作补充触发，避免系统性一致错误漏放（呼应 I6）。

### 假设 5 — 适配器配置存放：集中 `config.py` 字段 vs 每适配器独立 `*.toml`
- **裁决：调整（集中 schema + `Config.engines.<name>` 命名空间 + 加载期 schema 校验/默认值合并；每适配器 toml 仅作可选覆盖层）**
- **理由**：安全硬性前提——toml 绝不含密钥（仅 host/port/model/timeout/enable，密钥只走环境变量/secret，否则重演 round2 明文密钥事件）；可维护性反对纯每适配器 toml（配置真相源碎片化、出境端点无法一眼审计、校验弱）；测试倾向每适配器 toml 但要求假适配器同构。折中：物理文件可分段，但归一加载到单一 `Config`，由 `AdapterMeta` 派生默认值并做 schema 校验 + 默认值合并；保留 `config.py` 环境变量入口以保证现有 env 契约不破。

### 假设 6 — 字形知识库来源：复用 kimi `term_kb`/RuntimeDB，还是 KZOCR 内置精简白名单
- **裁决：采纳（KZOCR 内置精简中医字形/异体/混淆白名单为事实源，kimi `term_kb` 仅可选增强）**
- **理由**：8 角色压倒性支持 KZOCR 自持（领域：中医专用混淆/异体须可控可评审；性能：必须进程内镜像、禁止逐字符查 RuntimeDB；测试：记忆提示 kimi 真实引擎处破损重构态，测试须不 `import kimi` 即可跑通；安全：外部 `KZOCR_TERM_KB_PATH` 须校验位于受控目录防路径穿越）。补充：启动时一次性进程内镜像白名单+中医词典+异体/繁简映射+预计算形似混淆表（O(字符数)）；闭环回填写回 KZOCR 侧库。

---

## 5. 修订清单（actionable，按阶段 0–6）

> 「方案文档需要怎么改」的具体条目，供主会话执行修订。未列出的既有 round2 问题（H1–H8）按其已落地状态不再重复。

### 阶段 0（本方案文档修订，落地到 `docs/`）
- [ ] **§1/§2 确立 `kzocr/engine/types.py` 为层间唯一契约（NIR）**：显式声明层与层之间传递 `BookResult/PageResult/ParagraphResult/LineResult`，删除「候选文本」口头契约（I1）。
- [ ] **§2.1 适配器返回值改为结构化**：`recognize_page(img) -> AdapterPageResult(text, confidence, char_confidences)`，而非 `str`（I1、测试 K2）。
- [ ] **冻结 `glyphVerified` 字段语义**：§4.3 改为新增 `Line.glyph_status: Literal[PASS|UNKNOWN|FAIL|UNCERTAIN]` 枚举，**保留** `glyph_verified` 作校验后文本，或显式声明迁移所有消费方；说明与 `types.py` 现有文本语义的兼容方案（I1、测试 K1）。
- [ ] **§1/§9 修正「版心裁剪=数据最小化」表述**：改为「版心裁剪仅用于图像尺寸/带宽压缩，不具脱敏作用」；双页上下文默认关闭或纳入出境同意（I4、安全 H-A）。
- [ ] **§2.1 补全 `AdapterMeta`**：增 `label`（对外 engine_label）、`supports_context`、`supports_confidence`；`kind` 用 `Literal`；区分 `recognize_pages`（多张独立页）与独立的 `recognize_with_context`（上下文，仅当 `supports_context`）（I1、架构 K5/K12）。
- [ ] **§3/§8 假设 4 软表述改硬约束**：「默认 single、consensus 可选」改为「默认 single；无 GPU 全本地 consensus 拒绝启动；含云端引擎时 N≤2」（I3/I8、假设 4 裁决）。
- [ ] **§8 第 1–6 项假设更新裁决结论**：按第 4 节「采纳/调整/否决」改写；补充两条隐含假设（降级编排收口 Router、可观测性统一进 BaseAdapter）（I7/I10）。

### 阶段 1（适配器注册表 + 共享逻辑下沉 + 接口对齐 + 安全收敛）
- [ ] **新建 `kzocr/engines/_common.py` 下沉 `run.py` 共享逻辑**：`page→numpy / _crop_to_body / 后处理 / markdown↔pages / 跨页合并`，由 `BaseAdapter` 默认复用——这是 registry 能消除对 `run.py` 改动的前提（I2、可维护性 High-3）。
- [ ] **kimi 适配器仅做薄封装（shim）**：不复制/不重写 kimi `tcm_ocr.core.engines.*_adapter`；适配 `recognize_pages` 返回 `list[str]`、保留 `prompt`，`AdapterMeta` 由 KZOCR 注入（I2、可维护性 High-1）。
- [ ] **区分 book-level 与 page-level 适配器**：`BookPipeline` 作为 `BookLevelAdapter`（输入 PDF、输出 `BookResult`），与页级 `OCREngineAdapter` 并列由 router 按 kind 调度，不强拧（I2、可维护性 High-2）。
- [ ] **端点 SSRF / 出境收敛**：复用并扩展 `khub/client.py:_validate_url` 到所有出站端点（云端 `base_url`、本地 `vlm_host`），加域名 allowlist（如 `*.sensenova.cn`、`api.deepseek.com`），拒绝 RFC1918/回环之外内网与明文 http 警告，校验与建连间做 DNS 复检防重绑定；`vlm_host` 仅本机/Unix socket，`auto_start` 显式绑 `127.0.0.1`（I4、安全 M-D/M-A）。
- [ ] **统一 llama-server 端口单一真相源**：`auto_start` 前用配置端口探测（解决 config `18080` vs 实际 `:8080` 不一致导致重复起服务）；可用内存 < 8GiB 时禁止 `auto_start`（I8、性能 P5）。
- [ ] **配置单一真相源 + 加载期校验**：`Config.engines.<name>` 命名空间集中，默认值由 `AdapterMeta` 派生，加载即 schema 校验 + 默认值合并；密钥不进 toml（I10、假设 5 裁决、安全 L-A）。
- [ ] **降级链收口到 `EngineRouter`**：各适配器只管自身可重试故障（单页超时），不感知降级目标；Router 持 `prefer` 候选 + 探测，逐个尝试/捕获/降级，全失败 → HumanGate（I10、可维护性 Medium-3）。
- [ ] **降级/桩数据强制 `is_mock` 透传**：任何降级候选进入 `engine_texts` 必带 `source/is_mock`；`use_mock` 全链路回退置 `Book.is_mock=True`、`glyph_status` 至多 UNKNOWN、归档/推送在 `is_mock=True` 时显 ERROR 且阻断 publish（I7、对齐 round2 H8）。
- [ ] **`BaseAdapter` 统一可观测性**：结构化日志前缀 `[engine=<name>]` + 指标 `latency/success/fail/chars/fallback_count`；Router 写 `engine_path: ["sensenova"(fail)→"paddleocr_vl16"(ok)]` 到 `BookResult`（I10、可维护性 Medium-4）。

### 阶段 2（路由层 + probe 纯函数 + 性能预算）
- [ ] **`probe_environment()` 返回可注入 `ProbeResult`**，抽出纯函数 `select_adapters(probe, strategy, registry) -> list[AdapterMeta]`，CI 用注入 `ProbeResult` 确定性验证分支（测试 K3）。
- [ ] **性能预算写入架构**：单页超时 VLM 120s / SenseNova 90s（性能 P3）；新增 `KZOCR_TOTAL_TIMEOUT=7200s` wall-clock 总预算，到点停后续页、已识别页归档、未识别页推 HumanGate（性能 P2/B4）；`KZOCR_MAX_PAGES` 由 2000 降到 500 作「时间+内存」双闸（性能 P5/B5）；`KZOCR_MAX_CONCURRENCY=1`（含云端时 ≤2），与 llama-server `--parallel` 对齐（性能 B2）。
- [ ] **每页重试 + 熔断**：`KZOCR_PAGE_RETRIES=2` + 退避，同引擎连续 2 次超时熔断本剩余页转 UNCERTAIN/HumanGate，禁止静默丢页（性能 P4/B6）。
- [ ] **共识行对齐定义**：基于 `ParagraphResult.node_type/heading_level` 做块级键对齐，非裸逐行；`strategy.mode` 默认 single（I3、测试 K4）。

### 阶段 3（字形校验 + 知识库）
- [ ] **校验前 `normalize(c)`**：繁→简（opencc 或自带映射）+ 异体→正体（`variant_map.json`，仅收明确等价项）+ 全角/旧字形归正（领域 K2）。
- [ ] **罕见中医字引入 `RARE` 态**：归一后不在已知集、但属合法 CJK Ext-A/B 且命中「中医候选字表」→ `glyph_status=PASS, auditSource=rare_allowlist`，**不进人工队**；其余未知 → UNKNOWN 送检（领域 K1）。
- [ ] **中医专用形似混淆集**：维护 `confusion_set.json`（莪术↔我术、黄芩↔黄芪、半夏↔半下…）作 FAIL 判据，非通用集（领域 K8）。
- [ ] **`UNKNOWN` 纳入 HumanGate 触发**（I6、校对 C1）；单引擎模式以 `UNKNOWN`/低置信补触发，避免系统性一致错误漏放（校对 M5）。
- [ ] **知识库进程内镜像**：KZOCR 内置精简白名单+异体+混淆集，启动时一次性载入 `set`/`dict`，禁止逐字符外部查询；`KZOCR_TERM_KB_PATH` 可选叠加并校验位于受控目录（I6、假设 6 裁决、性能 P6）。
- [ ] **预留 `VisionRecheckAdapter` 挂点**：对 FAIL/UNKNOWN 行回看裁剪图，仅限本地视觉引擎（假设 1 裁决）。

### 阶段 4（人工兜底 HumanGate 强化）
- [ ] **HumanGate 补列 `UNKNOWN` 触发条件**（I6）。
- [ ] **兜底行带原图裁剪**：FAIL/UNKNOWN/UNCERTAIN 行回看版心裁剪，裁剪图（或路径+bbox）随 `Line` 推送；`Book` 增 `is_mock`/`source` 列并映射 `BookResult.is_mock`（校对 C2、I7）。
- [ ] **优先级 `severity`**：`critical`=有毒药材/否定词；`warning`=FAIL/UNCERTAIN；`info`=require-human/mock，使校对台可排序（校对 H1/M4）。
- [ ] **错字聚合 + 批量校正**：同字形 group-by，复用 `Term`/`HerbOCRPattern` 给建议候选，支持「一处校正全局套用」（校对 H2）。
- [ ] **补 `glyphVerifiedReason` 列**；`auditSource` 改回语义（dictionary/consensus/human），修正 `to_zai_prisma.py:153` 把 `auditSource` 写成 `book.engine_label` 的错误（校对 H3/H5、架构附带发现）。

### 阶段 5（归档层 Archiver / TOC / 方剂）
- [ ] **明确唯一目标 schema = 规范 `schema.prisma`**：`to_zai_prisma.py` 扁平子集收敛为「向规范 schema 的适配层」；第 6 章措辞全部改引规范表名（I5、数据完整性 C1）。
- [ ] **最小小节切分不重建 Line**：复用既有 OCR `Line.id`，经 `Paragraph.contentNodeId` 挂到 `ContentNode`；`final_markdown` 仅展示层聚合（I5、数据完整性 H1）。
- [ ] **补 `ContentNode` 写入**（level/sequence/pageStart/pageEnd/source='toc'），替代「新增 TOC/Section 表」错误假设（数据完整性 H3）。
- [ ] **全文 → `FinalDocumentRecord(full_md, sha256)`**，不往 `Book/BookRegistry` 加不存在的 `final_markdown` 列（数据完整性 M2）。
- [ ] **方剂以规范 `FormulaComposition` 为准**：含 alias/root/referenced/crossPage；补齐七类核心字段 `usage/gongyong/zhuzhi/fangjie/fields_json`；剂量保留原串（`各15`/`等分`/`适量`）（领域 K3/K6、数据完整性 H2）。
- [ ] **毒性药材告警**：内置 `toxic_herbs.json` 打 `isToxic`，触发用量红线告警（细辛≤3g、附子须炮制）（领域 K7）。
- [ ] **归档幂等改 MERGE 人工层**：重跑仅 TRUNCATE 自动生成层，`humanFinalText/ProofreadRecord/KnowledgeAuditLog` 按 lineId 更新/追加，绝不整书清空人工成果（数据完整性 M1、呼应 round2 H2）。
- [ ] **归档收尾翻转生命周期**：`BookRegistry.status='archived'`+`archivedAt`，写 `OCRProcessingLog(stage='archive', status='completed')`（数据完整性 M4）。
- [ ] **闭环回填机制定义**：人工校正 → `CandidateSubmissionBatch` → term_kb/HerbOCRPattern/khub 术语·方剂库管线；khub 同步异步可选、不阻塞主链（领域 K4、校对 H4、假设 3 裁决）。
- [ ] **先补最小 TOC 分析器**作为 Phase 5 前置（从 `final_markdown` 按标题抽取章节树，可仅 regex 标题级）；或拆 5a（全文+标题块最小小节，可立即做）/5b（完整 TOC 树，依赖 round2 H5 立项）（架构 K3）。

### 阶段 6（说明文档补齐）
- [ ] **`docs/engines/*.md` 全量补齐**，CI 校验「注册即文档齐备 + 6 必需标题（含运营主体属地/是否跨境）」，缺失即 CI 失败（可维护性 Medium-5、安全 L-B）。
- [ ] **适配器脚手架 `kzocr engine new <name>`**：生成 `<name>.py + <name>.toml + docs 模板`，把新增成本从手写降到填空（可维护性 Medium-2）。
- [ ] **清理 `modelscope_pool.py` 注释残留密钥片段**；云端 provider 清单（8 个，超出方案所列）补数据出境说明（安全 L-A/L-B）。
- [ ] **可机抽字段由 `AdapterMeta` 自动生成**（`kzocr engine describe <name>`），纯主观项靠 CI 校验，杜绝「强制清单」式过期文档（可维护性 Medium-5）。
- [ ] **测试交付物（贯穿各阶段）**：`tests/test_router.py`/`test_glyph_verifier.py`/`test_adapters_protocol.py` + `tests/engines/fakes.py`（FakeOCRAdapter）+ fixtures；`probe`/`select_adapters` 纯函数参数化；保留 `run_engine` 为 `EngineRouter` 薄门面迁移现有 15 测试；新增 `kzocr smoke --adapter fake` 无依赖端到端（测试 K3/K5/K6/K7、架构 K14）。

---

## 附：核心结论摘要（给用户）

1. 方案方向正确、round2 硬伤已整改，8 角色一致判「有条件通过」，但进入实现前有五项硬门槛必须先闭合：契约冻结、共享逻辑下沉、安全端点收敛、目标 schema 对齐、性能预算。
2. 最危险的三处是：归档层误把扁平子集当事实（数据完整性 CRITICAL）、版心裁剪被误当作出境脱敏（安全硬伤）、以及 `UNKNOWN` 未触发人工兜底导致错字漏放（领域+UX 严重）。
3. 第 8 章 6 项假设，除「默认 single」「KZOCR 内置字形白名单」两项可直接采纳外，其余四项均需「调整」：最小小节改可配置+按 book_type 经 contentNodeId 挂载、方剂主链只写 zai 且用规范 FormulaComposition、配置改集中 schema+加载期校验、字形校验加本地回看原图挂点。
4. 降级/桩数据（mock）必须强制 `is_mock` 透传并阻断 publish，否则会在新架构里以更隐蔽形态重演 round2「假古籍」事件。
5. 实施顺序建议：阶段 0 先冻结三套契约与假设裁决 → 阶段 1 下沉共享逻辑+kimi 薄封装+安全收敛 → 阶段 2/3 路由纯函数+性能预算+字形校验 → 阶段 4/5 人工兜底与归档落到规范 schema，切勿在契约未定时先铺 10 个适配器。
