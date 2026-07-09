# KZOCR 多角色评审（第 2 轮）— 数据安全工程师

- **角色**：数据安全工程师
- **评审日期**：2026-07-09
- **评审范围**：
  - `kzocr/config.py`
  - `kzocr/engine/run.py`
  - `kzocr/engine/types.py`
  - `kzocr/engine/mock.py`
  - `kzocr/adapter/to_zai_prisma.py`
  - `kzocr/cli.py`
  - `kzocr/khub/client.py`
  - `kzocr/export_zai.py`
  - `kzocr/modelscope_pool.py`
  - `tests/test_pipeline.py`
  - `tests/test_vlm.py`
- **总体结论**：**不通过**

  本轮发现 1 项明文凭证硬编码（高危）、1 项会导致跨书数据被整体清除的写入逻辑缺陷（高危）、以及敏感古籍/患者页面图像可被发送至多家第三方云端（高危）。在凭证清理、写入幂等性、出境最小化完成前，不建议进入含真实患者/古籍数据的生产环境。

---

### [High] 源码中硬编码第三方 API 密钥

- 位置: `kzocr/modelscope_pool.py:101`、`kzocr/modelscope_pool.py:141`
- 描述: 模块在 `ProviderSpec` 中直接写入了两个明文 `api_key_fallback`：
  - ModelScope：`ms-40d78a2b-f786-433a-92e3-8e5f4049f602`（第 101 行）
  - SenseNova：`sk-4u2jMee2wGvEPtM7qXg6kPkc5H3gDKmw`（第 141 行）

  这些密钥以明文形式进入版本库。即便属于免费/试用额度，一旦仓库对外（含子模块、镜像、备份）流转，即构成凭据泄露，且 `_ProviderPool.__init__` 会在环境变量缺失时**直接回退使用这些硬编码密钥**（第 176 行 `os.environ.get(spec.api_key_env, "") or spec.api_key_fallback`），意味着缺省配置下会自动用泄露的密钥对外发起请求。
- 建议修复:
  1. 立即废除/轮换这两个密钥。
  2. 从源码中删除 `api_key_fallback` 明文值，改为仅读环境变量；缺失即禁用 provider（当前对 `modelscope`/`sensenova` 的兜底逻辑应移除）。
  3. 将密钥纳入 `.gitignore` 与密钥扫描（如 gitleaks）CI 卡点，防止再次提交。

---

### [High] 写入 zai 库时对 Pattern/Term/Formula 整表 DELETE，跨书数据被静默清除

- 位置: `kzocr/adapter/to_zai_prisma.py:90-92`
- 描述: 每次 `push_book_to_zai` 在写入前对 `FormulaIngredient`、`Formula`、`Pattern`、`Term` 四张表执行**无条件全表清空**：
  ```python
  for t in ("FormulaIngredient", "Formula", "Pattern", "Term"):
      cur.execute(f"DELETE FROM {t}")
  ```
  注释称“zai 单书模式”，但这与业务语义矛盾：
  - `Term` 插入时 `sourceBooks` 被硬编码为 `None`（第 189 行），根本无法按书归属，任何一次新书的推送都会把此前所有书的术语库抹掉。
  - `Pattern` 被导出侧当作“三大永久范式库（沉淀）”（`export_zai.py:49-62`）、`mock.py:165` 也称之为“永久范式库”，但实现上它随每次推送被整体重建，所谓“永久”实为“仅保留最后一次推送的书”。
  - 在含多本古籍/多患者批次的语料库场景下，先推送的书 A 的范式/术语/方剂，会被后推送的书 B 整体覆盖丢失。
- 建议修复:
  1. 为 `Pattern`/`Term`/`Formula`/`FormulaIngredient` 增加 `bookCode`（或 `sourceBook`）列，DELETE 与 INSERT 均按 `bookCode` 限定，与 `Line`/`Page`/`Proofread` 的一致。
  2. 若确为“全局永久库”，应另行设计合并/去重语义，而非全量清空；至少对已有 `sourceBooks` 作追加而非删除。
  3. 在推送前对跨书清空行为做显式确认或告警日志。

---

### [High] 敏感古籍/患者页面图像可被发送至多家第三方云端，缺乏最小化与出境控制

- 位置: `kzocr/modelscope_pool.py:190-194, 223-273, 336-352`；`kzocr/engine/run.py:155-196`（`_init_vlm_adapter` 中 SenseNova 分支）
- 描述: `CloudLLMPool` 的 `chat_vision` 将页面图像经 `_image_to_data_url` 转为 base64 后，连同 OCR prompt 一起发往多个外部 provider（modelscope、siliconflow、z.ai、zhipu、sensenova、glm、deepseek，见 `_PROVIDER_SPECS` 第 96-161 行）。结合项目定位（中医古籍 + 可能的患者/个人文本），若这些页面包含患者处方、个人健康信息，整页图像即被传出本地信任边界，且：
  - 未做脱敏/裁剪/最小化（整页 base64 直传）。
  - 无“是否允许出境/上云”的开关或审计记录；provider 故障转移链会自动把图像依次尝试多家境外/境内云厂商。
  - `run.py` 的 VLM 路径在 `vlm_engine="auto"` 且有 `SENSENOVA_API_KEY` 时会优先走 SenseNova 云端（`run.py:168-184`），同样把页面图像送出本地。
- 建议修复:
  1. 对含患者/个人数据的场景，默认禁用一切云端 vision provider，仅允许本地引擎（PaddleOCR-VL 本地 llama-server）。
  2. 增加“数据出境许可”配置项，未显式开启不得向第三方云发送页面图像；开启时记录审计日志（发往哪个 provider、哪本书）。
  3. 发送前对图像做必要裁剪（如仅版心）与去标识化，减少外传信息量。
  4. 在隐私/合规文档中明示第三方共享范围。

---

### [Medium] zai SQLite 库与标记文件以默认宽松权限创建，无加密存储

- 位置: `kzocr/adapter/to_zai_prisma.py:79-80`、`kzocr/adapter/to_zai_prisma.py:213-214`；`kzocr/config.py:47`（默认 `~/.khub/khub.db`）
- 描述: 写入库时仅 `os.makedirs(db.parent, exist_ok=True)` 后 `sqlite3.connect(str(db))`，未对生成的 `.db` 文件及 `.zai_prisma_marker` 标记文件做权限收紧（默认受 umask 影响，通常为 `0644`，同机其他用户可读）。库内 `Line`/`Proofread` 表存储原文与校正文本（含可能的患者/个人敏感文本），以明文存储且无对称加密（如 SQLCipher）。
- 建议修复:
  1. 建库后 `os.chmod(str(db), 0o600)`，标记文件同此处理；或在 `makedirs` 时确保父目录 `0700`。
  2. 评估对含 PII 的库启用 SQLCipher 等静态加密；至少明确“明文库不可落非受控目录”的规范。
  3. 在文档中登记库文件的备份与销毁（secure delete）策略。

---

### [Medium] kHUB 推送响应（含文档内容）被写入 INFO 级日志

- 位置: `kzocr/cli.py:66`、`kzocr/cli.py:99`；`kzocr/khub/client.py:43-44`
- 描述: `cmd_push` 执行 `log.info("已推送至 kHUB：%s", resp)`，`cmd_smoke` 执行 `log.info("    kHUB 响应：%s", resp)`。`resp` 来自 `khub_client.push_document`，其响应体可能回显 `content`（即校正后的古籍/患者文本）。默认 `logging.basicConfig(level=logging.INFO)`（`cli.py:24`），意味着敏感正文会落入运行日志，且日志文件通常未做权限控制与留存销毁策略。
- 建议修复:
  1. 仅记录 `doc_id`/`status` 等业务标识，不记录响应正文。
  2. 对日志统一脱敏，并约束日志文件权限与保留期。

---

### [Low] pipeline/冒烟默认库落在当前工作目录，存在误提交敏感库风险

- 位置: `kzocr/cli.py:34`（`cfg.zai_db = "kzocr.db"`）、`kzocr/cli.py:77`（`cfg.zai_db = "smoke.db"`）
- 描述: 未指定 `--db` 时，数据库文件落在 CWD 相对路径。若用户在项目根目录执行，敏感库 `kzocr.db` 易随仓库被误纳入版本控制（需确认 `.gitignore` 已覆盖 `*.db`）。
- 建议修复: 默认库改放到用户专属目录（如 `~/.cache/kzocr/` 或 `~/.kzocr/`），并以 `0600` 创建，避免与项目目录混用。

---

### [Low] 缺少数据库备份、留存与销毁（secure delete）策略

- 位置: 整体（无相关代码/配置）
- 描述: 全链路（zai 库、导出的 `exports/*.md`、`khub` 库）均以明文文件形式落盘，但代码中未见备份机制、留存期限、或删除时的安全擦除（如 `shred`/覆写）逻辑。含患者/古籍的库被 `rm` 后仅标记释放，可被恢复。
- 建议修复: 在运维规范中明确留存期与销毁流程；提供安全的库清除命令（覆写后删除），并考虑对导出物加密归档。

---

### [Info] 正面确认：SQL 语句均采用参数化绑定

- 位置: `kzocr/adapter/to_zai_prisma.py` 全文、`kzocr/export_zai.py` 全文、`kzocr/khub/client.py`
- 描述: 所有涉及用户输入/书籍数据的查询均使用 `?` 占位符参数绑定；动态部分仅限来自固定元组的表名常量，不存在用户可控的表名注入，未发现 SQL 注入风险。日志中未发现将 `book_code`/正文以 DEBUG 之外级别外泄的情况（VLM 跨页合并仅 `logger.debug`，第 377 行）。
- 说明: 此项为中性/正面记录，不计入问题计数。

---

## 数据安全维度评分

| 维度 | 评级 | 说明 |
|------|------|------|
| 存储安全 | **C** | 明文 SQLite、默认权限宽松、硬编码密钥与库同仓、无加密/备份/销毁策略 |
| 数据完整性 | **D** | 每次推送整表清空 Pattern/Term/Formula，跨书范式/术语/方剂被静默覆盖丢失，与“永久范式库”语义矛盾 |
| 隐私 / PII 保护 | **D** | 整页图像可无开关出境至多云、推送响应入日志、缺乏最小化与出境审计 |
| 凭证管理 | **F** | 源码内明文硬编码可用密钥，且缺省回退启用 |

> 综合评级：**不通过**。须先解决 High 项（密钥清理、写入幂等性、出境控制）后方可进入含真实数据的环境。
