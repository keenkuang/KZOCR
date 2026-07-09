# 网络安全评审（第 2 轮）

- **角色**：网络安全工程师
- **评审日期**：2026-07-09
- **评审范围**：
  - `kzocr/config.py`
  - `kzocr/engine/run.py`
  - `kzocr/adapter/to_zai_prisma.py`
  - `kzocr/cli.py`
  - `kzocr/khub/client.py`
  - `kzocr/export_zai.py`
  - `kzocr/modelscope_pool.py`
  - `tests/test_pipeline.py`
  - `tests/test_vlm.py`
- **总体结论**：**不通过**（存在硬编码凭证 / 明文传输 / 潜在 SSRF 与路径穿越缺陷）

---

## 已确认的安全问题

### [High] 源码中硬编码云端 API 凭证（Secret 泄露）

- **位置**：`kzocr/modelscope_pool.py:101`、`kzocr/modelscope_pool.py:141`
- **描述**：
  多个 provider 的 `api_key_fallback` 直接以明文写入源码：
  - 第 101 行：`api_key_fallback="ms-40d78a2b-f786-433a-92e3-8e5f4049f602"`（ModelScope）
  - 第 141 行：`api_key_fallback="sk-4u2jMee2wGvEPtM7qXg6kPkc5H3gDKmw"`（SenseNova 商汤）

  并在第 176 行 `api_key = os.environ.get(spec.api_key_env, "") or spec.api_key_fallback` 中启用：
  当环境变量未设置时，程序会**静默使用源码中内嵌的明文密钥**发起对外 HTTPS 请求。
  这意味着：
  1. 真实可用密钥随仓库进入版本控制与任意一份拷贝，构成长期凭证泄露；
  2. 密钥无法被轮换而不修改源码；
  3. 一旦仓库（含历史提交、派生 fork、打包产物）外泄，攻击者可冒用这些云账号产生费用或读取业务数据。
- **建议修复**：
  - 立即在对应云平台吊销/轮换这两个 `api_key_fallback` 值；
  - 删除源码中所有 `api_key_fallback` 明文，仅在环境变量缺失时禁用对应 provider（当前逻辑已支持 `enabled=False`，应直接返回 `None` 而非回落到硬编码 key）；
  - 将 `modelscope_pool.py` 加入 `.gitignore`/密钥扫描（如 gitleaks）的监控范围，并在 CI 中加入 secret 检测。

### [Medium] kHUB 客户端未校验 URL 协议，存在 SSRF / 本地文件读取风险

- **位置**：`kzocr/khub/client.py:29`、`kzocr/khub/client.py:43`
- **描述**：
  `url = f"{(base_url or config.config.khub_base_url).rstrip('/')}/documents"` 中的 `base_url`
  来源于环境变量 `KHUB_BASE_URL` 或命令行参数 `--khub-url`，随后直接交给
  `urllib.request.urlopen(req, timeout=30)`。代码未对协议做任何白名单校验。

  `urllib.request.urlopen` 在受影响环境下会处理 `file://`、`ftp://` 等非 HTTP 协议：
  - 若 `KHUB_BASE_URL=file:///etc/` → 实际请求 `file:///etc//documents`，可被利用读取本地任意文件内容（攻击者只要能影响该环境变量/参数即可）；
  - 若指向内网地址（如 `http://169.254.169.254/...` 或内网服务），则构成服务端请求伪造（SSRF）。
  本工具一旦被任何服务端/Web 入口间接调用，风险即转化为可利用的 SSRF。
- **建议修复**：
  - 校验 `base_url` 仅允许 `http://`/`https://`，并拒绝 `file:`、`ftp:` 等协议；
  - 对主机名做白名单或至少禁止指向 `127.0.0.0/8`、`169.254.0.0/16`、`10.0.0.0/8` 等保留网段（如 kHUB 仅本地）；
  - 考虑改用 `requests` 并显式 `allow_redirects=False`、设置 `trust_env` 等，避免跟随重定向导致的二次 SSRF。

### [Medium] `export` 命令以用户提供的 book_code 拼接输出路径，存在路径穿越写入

- **位置**：`kzocr/cli.py:48`（`out = args.out or f"exports/{args.book_code}.md"`）
- **描述**：
  `cmd_export` 在用户未指定 `--out` 时，将来自命令行参数 `args.book_code` 直接拼进文件路径
  `exports/{args.book_code}.md`，随后 `os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)`
  创建目录并以 `open(out, "w")` 写入。未对 `book_code` 做字符过滤，输入如
  `../../../../tmp/evil` 即可把导出文件写到 `exports/` 之外的任意可写位置（目录穿越）。
  尽管该工具为本地 CLI、操作者即本人，但凡 book_code 来自不可信源（例如被脚本化封装、或
  export 的输入来自上游不可信的 book_code 字段），即可被滥用写入任意文件（覆盖、投毒、计划任务等）。
- **建议修复**：
  - 对 `book_code` 做白名单（仅 `[A-Za-z0-9_\-]`），或 `os.path.basename()` 取其文件名部分后再拼路径；
  - 用 `Path(out).resolve()` 计算绝对路径，并确保其位于预期的 `exports/` 基目录下（`is_relative_to`），否则拒绝。

### [Medium] kHUB 推送默认明文 HTTP 且不强鉴权，敏感内容易被窃听/未授权写入

- **位置**：`kzocr/config.py:23`（默认 `khub_base_url="http://127.0.0.1:8000"`）、
  `kzocr/khub/client.py:29`、`kzocr/khub/client.py:39-41`
- **描述**：
  - 默认 `KHUB_BASE_URL` 为明文 `http://`，而推送内容为 OCR 校正后的古籍/中医文本（含方剂、药材、患者相关敏感信息）。
    若 kHUB 实际部署在非本机/跨网络，内容将以明文传输，存在中间人窃听与篡改风险。
  - 鉴权头仅在 `KHUB_API_TOKEN` 环境变量存在时才添加（`if token: req.add_header("Authorization", ...)`），
    默认情况下**完全不带鉴权**，任何能访问 kHUB 端口的客户端均可未授权推送/伪造文档。
    `verify_in_khub`（GET）同样不带鉴权。
- **建议修复**：
  - 默认优先 `https://`；对 `http://` 给出明确告警，并仅允许在显式 `localhost` 时放行；
  - 将鉴权由「可选」改为「强制」：缺失 `KHUB_API_TOKEN` 时拒绝推送，并在 kHUB 服务端强制校验 Bearer Token；
  - 对传输内容如包含 PII，考虑字段级脱敏或对通道启用 mTLS。

### [Low] VLM 模式将整本 PDF 全部载入内存且无上限校验，存在资源耗尽型 DoS

- **位置**：`kzocr/engine/run.py:407`（`all_pages = list(doc)`）、`kzocr/engine/run.py:395`
- **描述**：
  `_run_vlm` 在打开 PDF 后一次性 `list(doc)` 把所有页对象展开到内存，未在 `fitz.open(pdf_path)`
  之前对文件大小、页数、渲染分辨率做任何限制。攻击者提供一个页数极多或单页超大（高 DPI 渲染
  为 `np.ndarray`，150 DPI）的恶意/畸形 PDF，即可造成内存耗尽或 CPU 长时间占用，使进程 OOM/卡死。
  此外对输入文件是否确为合法 PDF 仅依赖 `fitz.open` 隐式校验，缺少大小/页数上限。
- **建议修复**：
  - 流式逐页处理而非 `list(doc)` 全量展开；
  - 增加 `total_pages`、`文件大小`、单页像素面积的上限校验，超限即拒绝并给出明确错误；
  - 对不可信 PDF 在沙箱/受限资源（cgroup/ulimit）下处理。

### [Low] 将环境变量可控的引擎目录前置注入 `sys.path`，存在模块劫持风险

- **位置**：`kzocr/engine/run.py:100-101`（`sys.path.insert(0, str(engine_dir))`）、
  `kzocr/engine/run.py:164-165`
- **描述**：
  `KIMI_ENGINE_DIR`（或 `cfg.kimi_engine_dir`）的值被无校验地 `sys.path.insert(0, ...)` 置于导入
  搜索路径最前，再 `from tcm_ocr... import ...`。若攻击者能够影响该环境变量或对应目录内容（例如
  共享主机、配置被注入），可在该目录放置同名恶意模块实现代码执行（import 劫持）。
- **建议修复**：
  - 对 `engine_dir` 做存在性与可信路径校验（如限定在已知项目根下的子目录）；
  - 优先通过 `importlib` 的命名空间/绝对导入或安装为正式包，避免运行时修改 `sys.path` 全局状态；
  - 在操作手册中明确 `KIMI_ENGINE_DIR` 必须由可信运维设置。

### [Info] `test_pipeline.py` 用 f-string 拼表名（当前安全但属脆弱模式）

- **位置**：`tests/test_pipeline.py:35`（`f"SELECT COUNT(*) FROM {t}"`）
- **描述**：
  测试中以 f-string 把表名拼入 SQL。当前 `t` 来自常量列表 `("Book", "Page", ...)`，无注入风险。
  但 f-string 拼 SQL 是易错模式，一旦将来表名来源扩展为外部输入，极易引入 SQL 注入。仅提示，无需紧急修复。
- **建议修复**：
  - 将表名白名单集中为常数并加类型/值校验；若需要动态表名，强制走白名单映射而非直接插值。

---

## 安全性维度评分

| 维度 | 评分 | 说明 |
|------|------|------|
| 注入防护（SQL/命令） | **B** | 写库/读库均使用参数化查询（`?` 占位），表名来自常量，未发现 SQL 注入；核心路径无 `subprocess`/`os.system`，无命令注入。加分。扣分项：测试里 f-string 拼 SQL 模式（Info）。 |
| 输入校验 | **D** | book_code 直接拼文件路径（路径穿越）、PDF 无大小/页数/格式校验、引擎目录无校验即注入 sys.path。明显不足。 |
| 传输与认证 | **D** | kHUB 默认明文 HTTP + 鉴权可选（默认无），且 URL 协议未白名单（SSRF/本地文件读取）。敏感内容保护薄弱。 |
| 依赖与凭证安全 | **F** | 源码硬编码并可静默回退使用真实云 API 密钥，属严重凭证泄露；无 `eval`/`exec` 危险用法（此为唯一正面项）。 |
| 安全默认值 | **C** | 端口默认绑定本机、冒烟/流水线默认落隔离库，整体倾向保守；但 kHUB 默认不鉴权、明文、且允许回退到内嵌密钥，默认偏危险。 |

> 综合：注入防护良好，但凭证管理、传输安全、输入校验存在可确认的中高危缺陷，故总体结论为**不通过**。

---

## 正向确认（非问题，供对照）

- `kzocr/adapter/to_zai_prisma.py` 与 `kzocr/export_zai.py` 的全部 `INSERT/SELECT/DELETE` 均使用
  参数化占位符，且被拼进 SQL 的表名均来自硬编码常量列表，**未发现 SQL 注入**。
- 核心代码范围内未发现 `eval` / `exec` / `os.system` / `subprocess` 等危险调用。
- 流水线默认落到隔离库（`kzocr.db`/`smoke.db`），避免误写真实 zai 控制台库，设计上较为稳健。
