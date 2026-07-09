# 安全与隐私评审 — OCR 引擎统一架构方案（round3）

> 评审对象：`/home/keen/KZOCR/docs/plans/ocr-engine-unification.md`（v0.1）
> 评审视角：安全 / 隐私 / 数据合规
> 代码基线：`config.py` / `khub/client.py` / `modelscope_pool.py` / `engine/run.py` / `adapter/to_zai_prisma.py`
> 关联：round2 H1–H8 整改（凭证硬编码 H1 已在当前代码中修复——`modelscope_pool.py` 的 `api_key_fallback` 已全部置空，密钥仅从环境变量读取）

---

## 结论

方案在**默认关云端视觉**（`allow_cloud_vision=false`）和**密钥不落地**（H1 已修）两处守住了安全底线，方向上正确。但存在三类必须修订后才能进入真实数据环境的问题：

1. **"版心裁剪=数据最小化"是错误安全断言**。裁剪只去掉白边与页眉页脚，正文内的全部敏感文本（人名、患者信息、秘方、药材剂量）仍在版心内、仍整页发往第三方。`crop_to_body`（`engine/run.py:254`）不构成出境最小化，只能降带宽。方案 §1/§9 的相关表述需改为"图像尺寸压缩"而非"敏感文本防泄漏"。
2. **出境开关是全局布尔，缺逐书/逐页同意、缺 PII 脱敏、缺出境审计日志**。一旦开 `true`，同一页可能被 consensus 模式同时发往 SenseNova/ModelScope/Ofox/DeepSeek 等多家（方案 §3 策略 B），且部分 provider 归属与数据跨境性质未在"数据出境说明"中界定（尤其 Ofox/硅基流动/z.ai 等）。
3. **SSRF 防护与本地服务暴露、入库落盘权限未随新架构延伸**。khub 同步与归档层（方案 §5/§6）会新增调用面，但既有的 `_validate_url`（`client.py:24`）只拦了元数据网段、未拦 RFC1918 内网段；归档导出的 `exports/*.md`（`export_markdown`）与 khub 库未继承 `0600` 权限保护。
4. **新增适配器调用面（云端 `base_url`/本地 `vlm_host`）完全无端点校验**：`allow_cloud_vision` 只控"是否发"不控"发往谁"，环境变量/`*.toml` 里的 `base_url` 被篡改即可把页面图像外泄到攻击者，且 `0.0.0.0` 误绑与远端 llama-server 也未被路由层拦截（M-D）。
5. **降级/桩数据缺乏强制标识透传**：方案描述了降级链路与 `use_mock` 回退，却未要求 `is_mock`/`auditSource` 固化落库，存在"桩数据冒充 `glyphVerified=PASS`"重演 round2 H8 的风险（M-E）。

**总评：方案可继续推进实现，但"数据出境最小化""SSRF/端点校验收敛""归档权限继承""降级/桩数据标识透传"四项须在阶段 1–2 设计即落地，否则 round2 已修的隐患会在新架构里以不同形态复发。**

---

## 关键问题

### 高 (High)

**[H-A] 数据最小化是伪命题：版心裁剪不能防止敏感文本出境**
- 位置：方案 §1「版心裁剪（数据最小化）」、§9「版心裁剪/数据最小化是否真能防止敏感文本出境」；代码 `engine/run.py:254-291`（`_crop_to_body`）、`engine/run.py:456`。
- 现象：`_crop_to_body` 仅按暗像素投影切除上下/左右空白（留 2% 边距），**不识别、不剔除任何文字内容**。中医古籍正文的全部敏感信息（患者/医家姓名、秘方组成与剂量、药材名）都在版心内，裁剪后依然整图 base64 发往第三方（`modelscope_pool.py:190` `_image_to_data_url`）。此外 SenseNova 双页上下文（`engine/run.py:459-464`）还会把**下一页顶部 15%**一并外传，比"当前页"更多。
- 风险：合规层面属于"声称已最小化实则全量出境"，一旦用于真实古籍/患者数据，无法满足《数据安全法》《个人信息保护法》的"最小必要"要求；若第三方在境外或受境外控制，构成数据出境。
- 判定：**高**。这是方案安全主张的核心破口。

**[H-B] 全局 `allow_cloud_vision` 开关缺乏细粒度同意与审计**
- 位置：方案 §3（`KZOCR_ALLOW_CLOUD_VISION`）、§2.3（仅要求文档标注"数据出境说明"）。
- 现象：开关为单一全局布尔。开启后无：逐书/逐页的用户确认、PII 预扫描脱敏、已发生出境的审计日志（发往哪个 provider、哪一页、何时）。consensus 策略（§3 策略 B）会让**一页图像同时送多家**云端。
- 风险：无法向合规方证明"哪些数据出了境、出了境多少"；多 provider 并行放大泄露面。
- 判定：**高**（合规审计缺失）。

### 中 (Medium)

**[M-A] kHUB URL 校验未覆盖 RFC1918 内网，`khub` 方剂库同步将继承该 SSRF 面**
- 位置：`khub/client.py:24-42`（`_validate_url`）；方案 §6「方剂书→方剂库（khub 方剂系统）」。
- 现象：仅拒绝 `file:/ftp:` 协议与 `169.254.169.254`/元数据主机，并仅对"非 localhost 的 http"**打 warning 不拦截**。未拦截 `10.0.0.0/8`、`172.16.0.0/12`、`192.168.0.0/16`、`127.0.0.0/8`（除显式名单外）、`[::1]` 之外 IPv6 等内网。DNS 重绑定也未防护（校验与 `urlopen` 之间存在 TOCTOU）。
- 风险：若 `KHUB_BASE_URL` 被配置或注入为内网地址（如 `http://192.168.x.x:port/internal-admin`），即构成 SSRF，可探测/访问内网服务。方案新增的 khub 同步直接用同一 `client.py`，会原样继承。
- 判定：**中**。当前默认 `127.0.0.1` 降低了即时风险，但架构扩展后需显式收敛。

**[M-B] 本地 llama-server 无鉴权，多用户/本机其它进程可滥用推理端点**
- 位置：`engine/run.py:231-237`（`PaddleOCRVl16Adapter(host, port, auto_start=True)`）；`config.py:35-36`（`vlm_host/vlm_port` 来自环境变量，默认 `127.0.0.1:18080`）。
- 现象：llama-server 默认无 `--api-key`，绑定 `127.0.0.1`。同一主机多用户/其它进程可自由调用该端点：① 耗尽 GPU/CPU 资源导致 OCR 拒服；② 借本机已加载的视觉模型对外提供推理（资源与模型被搭便车）；③ 若 `KZOCR_VLM_HOST` 被指向远端 llama-server，则本机图像会被发往该远端，形成新增出境通道。
- 风险：共享主机下的资源滥用与潜在的信息流动边界失控。
- 判定：**中**。

**[M-C] 归档导出与 khub 库未继承 `0600` 权限保护**
- 位置：`adapter/to_zai_prisma.py:70-75`（`_restrict_db_perms` 仅作用于 zai 库）；方案 §6 `Archiver`；`adapter/to_zai_prisma.py:284-287`（`export_markdown` 写 `*.md` 不限制权限）。
- 现象：zai 库已做 `0600`，但：① `export_markdown` 产出的 Markdown（含全文敏感文本）以默认 umask（通常 `0644`）落盘；② `.zai_prisma_marker`（`adapter:231`）未收紧权限；③ 方案 §6 的 khub 库/导出物未在架构层面要求权限约束。round2 数据安全的"明文库默认权限宽松"问题在归档扩展面仍会复发。
- 风险：同机其它用户可读到含患者/秘方文本的导出文件与标记文件。
- 判定：**中**。

### 低 (Low)

**[L-A] `modelscope_pool.py` 注释残留明文密钥片段**
- 位置：`modelscope_pool.py:109,125,133` 注释提及 "CHANGELOG key 被截断（78184ed8…）"、"77313fa0…" 等。
- 现象：功能上已无明文密钥（H1 修复，`api_key_fallback=""`），但注释仍引用疑似真实 key 前缀，属凭据卫生残留，仓库外传时可能造成误导或泄露线索。
- 判定：**低**。建议清理注释。

**[L-B] 云端 provider 清单超出方案所述，扩大出境面认知盲区**
- 位置：`modelscope_pool.py:96-161`（实际含 ofox、siliconflow、z.ai、zhipu、glm、deepseek 等 8 个 provider）；方案 §2.2(C) 仅列 SenseNova/ModelScope/Ofox/DeepSeek。
- 现象：大池启用范围与方案文档不一致，部分 provider（如硅基流动、z.ai、zhipu）的运营主体与数据归属未在"数据出境说明"中界定，易在 consensus 模式下被静默引入。
- 判定：**低**。

**[M-D] 云端适配器 `base_url` 与本地 `vlm_host` 无任何 SSRF/白名单校验（新暴露面）**
- 位置：方案 §3 `probe_environment()`（仅"探测端口是否监听""key 是否就绪"，**无端点合法性校验**）；代码 `config.py:40,45,66,70`（`sensenova_base_url`/`deepseek_base_url` 来自 `SENSENOVA_BASE_URL`/`DEEPSEEK_BASE_URL` 环境变量）、`engine/run.py:71`（`KZOCR_LLM_BASE_URL`）、`modelscope_pool.py:99-161`（各 provider `base_url` 写死或来自配置）。
- 现象：既有 `_validate_url`（`khub/client.py:24`）**只对 kHUB 推送生效**，云端视觉适配器与本地 llama-server 的端点完全未校验：
  1. **恶意/误配适配器指向外部**：`*.toml` 或环境变量里的 `base_url`（如 `SENSENOVA_BASE_URL`、`KZOCR_LLM_BASE_URL`、大池各 provider `base_url`）一旦被篡改或配置错误为攻击者控制的地址，页面图像会被**直接外发到攻击者**，且 `allow_cloud_vision` 只控"是否启用云端"、**不控"发往何处"**，无法拦截。这是方案新增适配器接入后引入的**新 SSRF/数据外泄入口**。
  2. **`0.0.0.0` 误绑 / 远端 llama-server**：路由层对 `vlm_host` 取值（来自 `KZOCR_VLM_HOST`）不做校验。若 llama-server 被误绑 `0.0.0.0` 或被指向远端主机，本机页面图像经该端点外流；而 `auto_start=True` 的本地服务默认无鉴权（见 M-B），同机其它进程亦可借道。
  3. **DNS 重绑定 / TOCTOU**：即便加入类似 `_validate_url` 的校验，若校验与建连之间存在 DNS 重解析，仍可能绕过。
- 风险：构成方案 §9 未覆盖的"适配器即出境通道"——比 khub 既有防护更分散、更难审计。
- 判定：**中**（与 M-A 同源，但针对**新增的云端/本地适配器调用面**，方案当前完全未提及）。

**[M-E] 降级/桩数据缺乏强制标识透传，存在"假古籍冒充已校验"风险**
- 位置：方案 §3（"失败则降级下一候选，全失败 → HumanGate"）、§9（"可整体回退到 `use_mock` 桩数据跑通全链路"）；对照 round2 H8（静默回退写死假数据、不报错）。
- 现象：方案描述了**降级链路**与**回退到桩**，但**未要求降级结果必须携带 `is_mock` / `auditSource` 标识并固化进 `Line`/`Book` 落库字段**。当前 `mock.py:147`、`types.py:143` 虽有 `is_mock=True`，但：① 统一的 `EngineRouter` 多候选降级时，被降级的候选文本若无 `source`/`is_mock` 标注，会与真实引擎结果混同进入 `engine_texts` 与共识；② `use_mock` 全链路回退若不强制在 `glyphVerified` 之外标 `is_mock=False 的真实结论 vs 桩`，归档层（§6）可能把桩数据当 `glyphVerified=PASS` 入库，重演 round2 H8 的"publish 假古籍"。
- 风险：演示可用性与数据可信度混淆，下游 khub/方剂库可能沉淀错误知识。
- 判定：**中**（对应用户关注点 5「假数据/降级防误用」）。

---

## 改进建议

1. **修正数据最小化表述与机制（对应 H-A）**
   - 方案 §1/§9 将"版心裁剪=数据最小化"改为"版心裁剪仅用于**图像尺寸/带宽压缩**，不具脱敏作用"。
   - 在适配器层新增真正的出境前处理：可选 PII/人名/剂量掩码、或仅发送由本地引擎产出的**文本**而非原图（对可本地识别的页面走本地链路，云端仅作冗余校验）。
   - 双页上下文（下一页顶部 15%）默认关闭，或纳入出境同意范围。

2. **把全局开关升级为带审计的出境控制（对应 H-B）**
   - `allow_cloud_vision` 之外增加：逐书 `--consent-cloud` 显式确认、provider 白名单（明确允许哪几家）、出境审计日志（page→provider→time→bytes）。
   - 要求每个云端适配器在 `docs/engines/<name>.md` 中标注**运营主体属地**与**是否跨境**，未标注者默认禁用。
   - consensus 模式下云端 provider 数量应设上限并默认 1，避免一图多发。

3. **收敛 SSRF（对应 M-A）**
   - `_validate_url` 增加 RFC1918/`127.0.0.0/8`（除显式 localhost）、`::1` 之外链路本地地址的拒绝；DNS 解析后在请求前复检 IP（防重绑定）；`allow_redirects=False`。
   - khub 同步作为新调用面，强制复用收敛后的校验。

4. **为本地 llama-server 加鉴权/收敛暴露（对应 M-B）**
   - 启动 llama-server 时加 `--api-key`（从环境变量注入），KZOCR 持有同一 token 调用；
   - 或改绑 unix socket；`KZOCR_VLM_HOST` 设为远端时按"出境"处理，须 `allow_cloud_vision` 同意。

5. **归档权限与加密继承（对应 M-C、L 中的库）**
   - `Archiver` 所有落盘物（zai 库、khub 库、`.zai_prisma_marker`、导出 `*.md`）统一 `0600`；新增 `export_markdown` 收尾 `os.chmod(0o600)`。
   - 评估对含 PII 的库启用 SQLCipher 静态加密；明确"明文库不得落非受控目录"。

6. **凭据卫生（对应 L-A/L-B）**
   - 清理 `modelscope_pool.py` 注释中的 key 片段；
   - 适配器 toml 规范（见下）禁止任何明文密钥，仅保留非机密默认值（host/port/model/timeout）。

7. **收敛适配器端点暴露面（对应 M-D）**
   - 复用并扩展 `_validate_url` 至**所有出站端点**：云端视觉适配器的 `base_url`、本地 `vlm_host` 均须通过同一校验（拒绝 `file:/ftp:`、元数据网段、RFC1918/回环之外内网、明文 http 警告）；新增 `base_url` **allowlist**——仅允许方案白名单内第三方域名（如 `*.sensenova.cn`、`api.deepseek.com`、`api-inference.modelscope.cn`），其余一律拒绝，使 `allow_cloud_vision` 同时具备"是否发"与"发往谁"的双重约束。
   - 对 `vlm_host` 增加"仅本机/Unix socket"约束；匹配远端时按出境处理，须经 `allow_cloud_vision` 同意。`auto_start` 的 llama-server 显式绑 `127.0.0.1`（禁止 `0.0.0.0`）。
   - 校验与建连间做 DNS 解析结果复检，防重绑定。

8. **降级/桩数据强制标识透传（对应 M-E，呼应 round2 H8）**
   - `OCREngineAdapter.recognize_page(s)` 返回值须附带 `source`/`is_mock` 元数据；`EngineRouter` 多级降级时，候选文本进入 `engine_texts` 必须保留来源标记，共识与人工门据此区分。
   - `use_mock` 全链路回退路径：落地 `Book.is_mock=True` 且 `Line.glyphVerified` 不得标 `PASS`（至多 `UNKNOWN`/人工），归档层与 khub 同步在 `is_mock=True` 时降级为"仅本地/不沉淀"，并打 ERROR 级醒目日志（延续 H8 整改要求）。

---

## 对第 8 章假设项的立场

- **假设 1（字形校验机制，暂不加独立再识别视觉模型）**：**同意**。再识别模型（如 `VisionRecheckAdapter`）会再次把 FAIL/UNKNOWN 行原图/裁剪送视觉模型，在云端路径下反而**放大出境面**，与隐私目标冲突。建议即便加，也限定在本地视觉引擎执行。
- **假设 2（最小小节定义）**：**中立偏支持"以 TOC 三级标题为最小单元"**。粒度越细，单条记录越易被单独导出/外传，建议切分粒度与安全分级解耦——敏感书允许更粗粒度入库。
- **假设 3（方剂库归属 zai 即可 vs 必须同步 khub）**：**建议"zai 本地库优先，khub 同步须经出境/内网校验"**。khub 走 `client.py` 既有 SSRF 面（M-A），且若 khub 部署跨网络则为明文传输（round2 netsec 已标记），需先收敛再开启同步。
- **假设 4（consensus 成本，是否默认仅 single）**：**强烈建议默认 `single`，consensus 设为显式可选**。除成本外，consensus 的云端多 provider 并行直接加剧 H-B 出境面问题。
- **假设 5（适配器配置集中 `config.py` 字段 vs 每适配器 `*.toml`）**：**倾向每适配器 `*.toml`，但必须写入硬性约束——toml 仅含非机密默认值（host/port/model/timeout/enable），密钥字段一律禁止出现，只能由环境变量/secret 注入**（延续 H1 修复成果）。否则 `*.toml` 一旦入库即重演 round2 明文密钥事件。**这是本评审对假设 5 的硬性前提。**
- **假设 6（字形知识库来源，复用 kimi `term_kb` vs KZOCR 内置精简白名单）**：**建议 KZOCR 内置精简字形白名单，避免与 kimi 引擎仓库强耦合**。知识库若为外部路径（`KZOCR_TERM_KB_PATH`，`run.py:91`）且未校验路径归属，存在被本地篡改/路径穿越风险，应在加载时校验路径位于项目受控目录内。

- **降级/桩数据标识（方案 §3/§9 隐含，非第 8 章原假设）**：**强烈建议将"`is_mock` / `auditSource` 强制透传并禁止桩数据标 `glyphVerified=PASS`"列为方案硬性约束**，而非仅"可回退到桩跑通演示"。否则统一 `EngineRouter` 的多候选降级与 `use_mock` 回退会在新架构里以更隐蔽的形态重演 round2 H8（假古籍冒充已校验入库）。这是本次评审对"可归档"原则（方案原则 5）的必要安全补强。
