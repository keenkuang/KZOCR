# 安全与隐私评审 — OCR 引擎统一架构方案（round4 / v0.2）

> 评审对象：`/home/keen/KZOCR/docs/plans/ocr-engine-unification.md`（草案 v0.2）
> 评审视角：安全 / 隐私 / 数据合规
> 代码基线：`config.py` / `khub/client.py` / `modelscope_pool.py` / `adapter/to_zai_prisma.py`（截至 2026-07-09 现状）
> 任务：核查 round3（security.md / summary.md 的 I4、I7、H0-C）安全问题在 v0.2 是否被**真闭合**
> 范围：仅调查与文档评审，未修改主方案或代码。

---

## 结论

**有条件通过（Conditional Pass）。**

v0.2 在**安全主张的修正层面**是真闭合了 round3 的核心破口：①"版心裁剪=数据最小化"的错误断言被正式撤除（§0 H0-C、§1、§9 三处一致改为"仅压缩带宽、不脱敏"）；②"is_mock 强制透传 + 阻断 publish"已被写入 §5/§9 与阶段 1 清单（I7 / M-E 真规划）；③"密钥绝不进 toml"在 §2.4 与假设 5 裁决中重申，且代码侧 `config.py`/`modelscope_pool.py` 确已无明文密钥落盘（H1 成果守住）。

**但有三处"规划已说、治理未定"的硬伤，使 round3 的 SSRF/出境收敛在 v0.2 仍停留在"纸面闭合"而非"机制闭合"：**

1. **allowlist 治理缺位 → 可能把"校验"本身变成新的出境通道**（见新引入问题 N1）。v0.2 §2.4 要求扩展 `_validate_url` 做 allowlist，却未界定 allowlist 的**来源、冻结性、与 env/base_url 的校验关系**——这是 round3 M-D 没根治反而新增的风险面。
2. **出境审计日志与逐书/逐页同意仅有"要求"无"机制"**（H-B 部分闭合）：审计落哪、schema、留存、同意如何捕获均未定义；且 v0.2 自承"consensus 多第三方并发出境合规不可证明"，却未据此**默认禁掉跨云 consensus**，只给了 N≤2 的上限。
3. **代码层残留未清**：`modelscope_pool.py` 仍带明文 key 片段注释（L-A 未闭合），`to_zai_prisma.py` 的 `Book` 表仍无 `is_mock` 列且无阻断守卫（M-E 代码未闭合，仅规划）。

> 进入实现前，建议把以下三点作为 H0-C 的"子门槛"在阶段 1 落地验证，否则 round3 隐患会以"已规划"的错觉复发。

---

## round3 问题闭合度（逐条）

| round3 编号 | round3 问题 | v0.2 对应 | 闭合状态 | 判定说明 |
|---|---|---|---|---|
| **H-A** | 版心裁剪被误当数据最小化/脱敏 | §0 H0-C「版心裁剪**不构成脱敏**」；§1「版心裁剪→仅压缩带宽」；§9「版心裁剪仅压缩带宽、不脱敏」 | **已闭合** | 三处表述一致撤除脱敏主张，与事实对齐。但注意：v0.2 未提供任何"真正的出境前处理"（如 PII 掩码/本地文本替代原图）作为替代机制——表述对了，能力仍空（见 N2）。 |
| **H-B** | 全局 `allow_cloud_vision` 缺细粒度同意/审计 | §9「逐书/逐页同意 + 出境审计日志」；§3 consensus 限 N≤2；§2.3 要求文档标"运营主体属地/是否跨境" | **部分闭合** | 文本要求到位，但：①审计日志落点/schema/留存未定义；②同意捕获机制（谁、何时、如何记录）未定义；③自承"多第三方并发出境合规不可证明"却未**默认禁用跨云 consensus**，仅设 N≤2 上限，合规不可证仍放行。 |
| **M-A** | kHUB `_validate_url` 未覆盖 RFC1918/回环外内网、DNS 重绑定 | §2.4「拒绝 RFC1918/回环外内网与明文 http 警告、建连前 DNS 复检防重绑定」 | **部分闭合（规划）** | v0.2 明确要求扩展，但当前 `client.py:24-42` 仍只拦元数据网段、对非本机 http 仅 warning、`urlparse.hostname` 后**无任何 IP/网段/DNS 解析**。属"规划已说、代码未动"——对一份实现前方案属正常，但**阶段 1 必须交付该扩展**才算真闭合。 |
| **M-B** | 本地 llama-server 无鉴权、误绑 0.0.0.0 / 远端 vlm_host 失控 | §2.3「`auto_start` 须显绑 127.0.0.1」；§2.4「`vlm_host` 仅本机/Unix socket」 | **部分闭合** | 绑定约束明确（收敛误绑），但**未要求 `--api-key` 鉴权**；远端 `vlm_host` 仅说"按出境处理须经 `allow_cloud_vision`"，未定义如何判定"远端"与如何强制同意。同机搭便车/资源滥用风险仍在。 |
| **M-C** | 归档导出 `exports/*.md` 与 marker 未继承 `0600` | （v0.2 全文未显式提及导出文件权限） | **未闭合** | v0.2 §6/§9 对归档层只谈 schema 与生命周期，**未要求** `export_markdown` 产出 `*.md`、`.zai_prisma_marker` 收尾 `os.chmod(0o600)`。当前 `to_zai_prisma.py:231-232` marker 与 `:284-287` 导出均按默认 umask 落盘。round2 明文库权限宽松问题在扩展面复发。 |
| **M-D** | 云端 `base_url` / 本地 `vlm_host` 无任何 SSRF/白名单校验 | §2.4「所有出站 `base_url`/`vlm_host` 经扩展 `_validate_url`：域名 allowlist + 拒 RFC1918/回环外 + 明文 http 警告 + DNS 复检」；§2.3(C) 清单 + 文档 6 标题含跨境 | **部分闭合（规划，且治理缺位）** | 方向对、范围对。但**allowlist 的"来源与冻结性"未定义**（见 N1），且 `config.py` 当前 `SENSENOVA_BASE_URL`/`DEEPSEEK_BASE_URL`/`KZOCR_LLM_BASE_URL` 等 env 值**直接进 Config 不经 allowlist 校验**——若不把 env 提供的 URL 也强制过 allowlist，则 M-D 在实质层面未闭合。 |
| **M-E** | 降级/mock 缺乏 `is_mock`/`auditSource` 强制透传，`is_mock=True` 未阻断 publish | §5「`Book` 增 `is_mock`/`source` 列…`is_mock=True` 时归档/推送显 ERROR 且阻断 publish」；§9 同；§1「`BookResult.is_mock` 强制透传」；§7 阶段 1「`is_mock` 强制透传 + 阻断 publish」 | **部分闭合（真规划，代码未动）** | **v0.2 阶段 1 已真规划**（§5/§7 明确列项），直接回应 round3 I7/M-E。但当前 `to_zai_prisma.py:30-33` `Book` DDL **无 `is_mock` 列**、插入语句 `:115-121` 无该字段、整文件**无阻断守卫**。属"方案已决、实现待做"——需以 H0-C 子门槛在阶段 1 强制落地，否则易在赶工中被跳过。 |
| **L-A** | `modelscope_pool.py` 注释残留明文 key 片段 | §7 阶段 6「清理 `modelscope_pool.py` 注释残留密钥片段」 | **未闭合（残留仍在）** | 代码现状 `modelscope_pool.py:109/117/125/133` 仍含 `CHANGELOG key 被截断（78184ed8…）`、`77313fa0…` 等疑似真实 key 前缀注释。阶段 6 才清，**建议提前到阶段 1 安全收敛一并清除**（属卫生类、零成本）。 |
| **L-B** | provider 实际 8 家超出方案所列 4 家，扩大出境面认知盲区 | §2.3(C) 列 SenseNova/ModelScope/Ofox/DeepSeek；§2.4 要求文档含"运营主体属地/是否跨境"+CI 校验 | **部分闭合** | 文档强制标注方向正确，但 v0.2 §2.3(C) 仍未把 `siliconflow/z.ai/zhipu/glm` 显式纳入清单，其运营主体与数据归属盲区未消除；且 CI 校验只在"注册时"触发，对已存在的大池 8 provider 未要求在 v0.2 定稿时一次性补齐跨境说明。 |

**小结**：9 项中 **1 项已闭合（H-A）**、**7 项部分闭合（多为"规划已说、机制/代码未定"）**、**1 项未闭合（M-C）**；另 **L-A 为代码残留未清**。

---

## v0.2 新引入 / 仍未根治的问题

### [N1] allowlist 治理缺位——可能把"校验"变成新的出境通道（重点）
- 位置：v0.2 §2.4「域名 allowlist（如 `*.sensenova.cn`、`api.deepseek.com`）」；对照 `config.py:62-70`（env 直取 base_url）。
- 现象：v0.2 要求对所有出站端点做 allowlist，但**未界定**：
  1. **allowlist 来源**：是代码内冻结常量、还是可经 `*.toml`/`env` 扩展？§2.4.2 说每适配器 toml 仅作"可选覆盖层（host/port/model/timeout/enable）"——若 `host` 可被 toml 覆盖，而 allowlist 又允许 toml 增列域名，则**攻击者改 toml 即可把自己域名加入 allowlist**，SSRF 守卫形同虚设。
  2. **env 提供的 base_url 是否过 allowlist**：当前 `SENSENOVA_BASE_URL`/`DEEPSEEK_BASE_URL`/`KZOCR_LLM_BASE_URL` 等直接进 `Config`（`config.py:64-70`），**不经任何白名单**。v0.2 未要求"env URL 必须落在冻结 allowlist 内"。
  3. **allowlist 与 DNS 解析的时序**：若先校验域名在 allowlist、再解析建连，仍可被重绑定绕过（round3 已提）；v0.2 说"建连前 DNS 复检"但未说"复检后是否再比对 allowlist 的解析结果"。
- 风险：**新引入的绕过面**——比 round3 M-D 描述的"无校验"更隐蔽：表面有 allowlist，实则 allowlist 可被配置层膨胀。这是本轮最该补的一刀。
- 建议（写入改进建议）：allowlist 必须是**代码内冻结常量**（不可经 toml/env 增删）；所有 `base_url`（含 env 注入）在建连前必须**解析为 IP 并经 allowlist 的 IP/网段 + 域名双重校验**，且解析结果与 allowlist 网段在 connect 前再做一次比对（缩窄 TOCTOU）。

### [N2] "版心裁剪≠脱敏"已说对，但无替代的出境前处理机制
- 位置：v0.2 §0/§1/§9（已纠正表述）；§4.2.7「VisionRecheck 仅限本地视觉引擎」。
- 现象：v0.2 正确撤除了脱敏错觉，但**未提供任何"真最小化"手段**——开启 `allow_cloud_vision` 后，整页原图（含双页上下文下一页顶部 15%，round3 H-A 已指出）仍全量出境。方案仅用"逐书/逐页同意 + 审计"对冲，无技术层 PII/人名/剂量掩码，也无"本地引擎产文本替代原图外发"的降级路径。
- 风险：合规层面仍属"声称已获同意实则全量出境"，同意书难以覆盖不可预期的敏感字段（患者名、秘方剂量）。
- 建议：在 §2.4 / §9 增加"出境前处理"必选项（至少：关闭双页上下文默认、可选 PII 掩码、或仅境外发本地引擎产出的文本而非原图）。

### [N3] 跨云 consensus "合规不可证明"却仍默认允许（N≤2 上限≠默认禁）
- 位置：v0.2 §9「consensus 多第三方并发出境合规不可证明」；§3「含云端时 N≤2」；§8 假设 4 裁决。
- 现象：方案自承多第三方并发出境合规不可证，但只设 N≤2 上限，未**默认禁用跨云 consensus**。consensus 模式在"含云端视觉引擎时允许"，意味着开云端后两名第三方可同时收到同一页原图。
- 风险：无法向合规方证明"哪些数据出了境、出了几家"，与 H-B 的审计目标自相矛盾。
- 建议：默认 `single`；consensus 若含≥2 家云端 provider，须**显式逐书同意 + 强制审计**（而非仅靠 N≤2）。

### [N4] `vlm_host` "仅本机/Unix socket" 判定与远端处置未机制化（M-B 延伸）
- 位置：v0.2 §2.4「`vlm_host` 仅本机/Unix socket」；`config.py:35-36` 默认 `127.0.0.1:18080`。
- 现象：约束存在，但"如何判定远端"与"远端时如何强制走 `allow_cloud_vision` 同意 + 鉴权"未定义；本地 llama-server 仍无 `--api-key`。
- 风险：同机其它进程搭便车推理端点；若 `KZOCR_VLM_HOST` 指向远端，本机图像外流且无同意闸门。
- 建议：见改进建议 4。

---

## 改进建议（按优先级）

1. **冻结 allowlist（堵 N1）**：将出站域名 allowlist 定为 `kzocr/engines/_common.py` 或 `config.py` 内的**不可经 toml/env 修改的常量**；所有 `base_url`（含 env 注入的 `SENSENOVA_BASE_URL` 等）在建连前必须 (a) 解析 IP、(b) 命中 allowlist 域名**且**解析 IP 不在 RFC1918/回环外/链路本地/元数据网段、(c) 协议为 https（明文 http 一律拒绝而非仅 warning）。解析→校验→connect 之间重新解析并二次比对，缩窄 TOCTOU。
2. **审计日志机制化（补 H-B）**：在 §9 定义出境审计的落点（建议复用 zai 库 `KnowledgeAuditLog` 或独立 append-only 日志）、字段（page→provider→time→bytes→consent_id）、留存策略；`allow_cloud_vision=true` 时若缺审计写入能力应拒绝启动。
3. **同意捕获机制化（补 H-B/N3）**：`--consent-cloud` 须产生可审计的 consent 记录（书级 ID + 时间戳 + 同意的 provider 白名单），云端与共识路径在实际发送前校验该 consent 存在且覆盖目标 provider；跨云 consensus（≥2 家云端）默认禁，须显式同意。
4. **`vlm_host` 治理（补 M-B/N4）**：`auto_start` 的 llama-server 加 `--api-key`（从 env 注入，KZOCR 持同 token 调用）；`vlm_host` 经扩展 `_validate_url` 判定——仅 `127.0.0.1`/`localhost`/`::1`/Unix socket 放行，远端一律按出境处理，须经 consent + `allow_cloud_vision`。
5. **归档权限继承（补 M-C）**：§6/§9 显式要求 `Archiver` 所有落盘物（zai 库、`khub` 库、`.zai_prisma_marker`、`exports/*.md`）统一 `0600`；`export_markdown` 收尾 `os.chmod(0o600)`；明确"明文库不得落非受控目录"。
6. **提前清除密钥注释（补 L-A）**：把 `modelscope_pool.py:109/117/125/133` 的 `CHANGELOG key 被截断（78184ed8…）`/`77313fa0…` 等注释从阶段 6 提前到阶段 1 安全收敛一并删除。
7. **补齐 provider 清单（补 L-B）**：v0.2 §2.3(C) 显式列出 `siliconflow/z.ai/zhipu/glm` 共 8 家，并要求在定稿时一次性补齐各家的"运营主体属地/是否跨境/数据归属"说明，CI 校验覆盖大池全部 `_PROVIDER_SPECS`。

---

## 对第 8 章安全相关假设裁决的再确认

- **假设 1（暂不加独立再识别视觉模型）**：**维持"采纳（默认不加）"**。v0.2 §4.2.7 已把 `VisionRecheckAdapter` 限定"仅本地视觉引擎"，与隐私目标一致——云端路径下再识别会放大出境面，确认不支持云端 recheck。
- **假设 2（最小小节粒度）**：**维持"调整"**。补充安全视角：切分粒度应与敏感分级解耦，敏感书（含患者/秘方）允许更粗粒度入库，降低单条记录被独立导出/外传的风险（与 N2 出境前处理呼应）。
- **假设 3（方剂库主链只写 zai，khub 异步可选）**：**维持"调整"**。khub 同步走 `client.py` 既有 SSRF 面（M-A），须先收敛 `_validate_url` 再开启；且 khub 若跨网络为明文传输（round2 netsec 已标记），同步前须 https + token，**未经收敛不得默认开启**。
- **假设 4（默认 single，consensus 仅 opt-in）**：**维持"采纳并提升为硬约束"**，但**强化**：含≥2 家云端 provider 的 consensus 视为"合规不可证明"，必须显式逐书 consent，否则拒绝（呼应 N3）。N≤2 上限保留。
- **假设 5（集中 config + 每适配器 toml 仅可选覆盖层）**：**维持"调整"**，但**加硬性前提**：toml 覆盖层**不得包含**任何可膨胀 allowlist 的字段（host 覆盖须经冻结 allowlist 二次校验）；密钥字段一律禁止出现，只能由 env/secret 注入（延续 H1）。这是 M-D/N1 的根治前提。
- **假设 6（KZOCR 内置字形白名单）**：**维持"采纳"**。补充：`KZOCR_TERM_KB_PATH`（`run.py:91` 既存机制）加载时须校验路径位于受控目录（防路径穿越），与 §4.1 一致。

---

## 一句话裁决

v0.2 在**主张层面**真闭合了 round3 的核心安全破口（脱敏错觉撤除、`is_mock` 阻断已规划、密钥不落 toml 守住），但在**机制层面**仍停留在"规划已说"——allowlist 治理缺位（N1 新风险）、出境审计/同意无机制、代码残留（L-A、M-C、M-E 代码未动），故判**有条件通过**，须以 H0-C 子门槛在阶段 1 强制落地 N1/N2/N3/M-A/M-E 后方可视为 round3 安全项真闭合。
