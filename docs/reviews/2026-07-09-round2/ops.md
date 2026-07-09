# KZOCR 多角色评审 · 运维工程师视角（第 2 轮）

- **角色**：运维工程师
- **评审日期**：2026-07-09
- **评审范围**：
  `kzocr/config.py`、`kzocr/engine/run.py`、`kzocr/engine/mock.py`、`kzocr/engine/types.py`、
  `kzocr/adapter/to_zai_prisma.py`、`kzocr/cli.py`、`kzocr/khub/client.py`、
  `kzocr/export_zai.py`、`kzocr/modelscope_pool.py`、`pyproject.toml`、
  `scripts/setup_submodules.sh`、`tests/test_pipeline.py`、`tests/test_vlm.py`
- **总体结论**：**不通过**

> 不通过原因（运维维度）：① 部署依赖声明缺失，且重依赖在 mock 路径被无条件 import，导致“零依赖跑通 mock 链路”不成立；② 适配器每次推送全量清空三大范式库/术语/方剂表，在多书场景下构成数据丢失；③ 硬编码 API 密钥入库（安全/凭据治理）；④ 推送失败处理存在“看似可恢复、实则会崩溃”的逻辑漏洞。以上任一项都足以阻断生产化部署，故判定不通过。

---

## 问题清单

### [High] 硬编码云端 API 密钥入库（凭据治理）

- 位置: `kzocr/modelscope_pool.py:101` 与 `kzocr/modelscope_pool.py:141`
- 描述: 两个 provider 的 `api_key_fallback` 直接写明了明文密钥
  `ms-40d78a2b-f786-433a-92e3-8e5f4049f602`（modelscope）与
  `sk-4u2jMee2wGvEPtM7qXg6kPkc5H3gDKmw`（sensenova）。`api_key = os.environ.get(...) or spec.api_key_fallback`
  意味着即使运维未注入环境变量，也会使用代码内密钥。密钥一旦入库即不可回收、可被任意 clone 者滥用，且上游一旦吊销会导致静默降级。
- 建议修复: 删除所有 `api_key_fallback` 明文；仅保留 `api_key_env` 从环境变量读取；
  密钥经部署平台的 Secret 管理（如 `.env`/Vault/K8s Secret）注入。对“已知免费 key”也应视为凭据，不得入库。
  同时在 `.gitignore` 与文档中明令禁止提交密钥，必要时将该文件从历史中清理。

### [High] 适配器每次推送全量清空范式库/术语/方剂表（数据丢失风险）

- 位置: `kzocr/adapter/to_zai_prisma.py:90-92`
- 描述: 推送一本书时，对 `FormulaIngredient`、`Formula`、`Pattern`、`Term` 四张表执行
  `DELETE FROM {t}`（无条件全表清空）。注释自称“zai 单书模式”，但 KZOCR 的真实流水线是把多本书
  PDF → 写库 → 推送 kHUB。一旦推送第二本书，第一本书沉淀的范式/术语/方剂即被整体抹除，
  这与“三大永久范式库”的定位直接矛盾，且过程无任何日志与确认。
- 建议修复: 改为按 `sourceBooks`/归属维度增量去重写入（upsert 或带 `bookCode` 关联）；
  若确为单书库，应在配置中显式声明并在 CLI 给出危险操作二次确认；
  至少补齐操作前日志与受影响行数日志，避免静默数据丢失。

### [High] pyproject 缺失运行依赖，且 mock 路径仍被强制 import 重依赖

- 位置: `pyproject.toml:6-10` 与 `kzocr/engine/run.py:17-18`
- 描述: `pyproject.toml` 的 `dependencies` 为空，注释声称“编排层仅用标准库，无需额外 pip 依赖即可跑通
  mock 链路”。但 `kzocr/engine/run.py` 在模块顶层 `import fitz`（PyMuPDF）与 `import numpy`；
  而 `kzocr/cli.py:19` 又 `from kzocr.engine import run`。因此 `kzocr smoke`/`kzocr pipeline --mock`
  等全部命令在导入期就要求 PyMuPDF 与 numpy 已安装。结论：无任何第三方依赖声明的包，实际上跑不起来，
  部署文档（pyproject 注释）与真实行为不一致；`openai`（`modelscope_pool.py:29`）同样未声明。
- 建议修复: 在 `pyproject.toml` 显式声明运行时依赖（至少 `PyMuPDF`、`numpy`；按需 `openai`），并按能力拆分
  `[project.optional-dependencies]`（如 `mock`/`engine`/`vlm`）。同时把 `fitz`/`numpy` 等重依赖改为在
  `_run_vlm`/`_run_real` 内局部导入，使纯 mock 链路真正零重依赖，避免“为桩数据被迫装 torch 全家桶”。

### [Medium] 单页 VLM 失败导致整本书静默降级为 mock

- 位置: `kzocr/engine/run.py:36-43`（及 `:45-51` 真实引擎同构）
- 描述: `run_engine` 对 `_run_vlm` 的失败是整段 `try/except` 捕获，一旦发生异常（包括某页 VLM 超时/单页崩），
  已识别的前 N-1 页成果全部丢弃，整本书回退到 `build_mock_book` 桩数据，且日志仅一条 warning。
  对 200 页大 PDF，第 50 页一次瞬时网络抖动即让前 49 页的成果作废，且无断点续跑/部分落库能力。
- 建议修复: 将容错粒度下沉到“每页”——单页失败跳过并记录、其余页照常产出；在 `_run_vlm` 内对每页调用做
  try/except 与有限重试；整本书级别只在“完全无可用结果”时才降级，并明确标注 `is_mock`/来源以供下游区分。

### [Medium] kHUB 推送失败处理看似可恢复，实则会让冒烟崩溃

- 位置: `kzocr/cli.py:94-104` 与 `kzocr/khub/client.py:43-44`
- 描述: `cmd_smoke` 用 `except RuntimeError as e:` 包裹 `khub_client.push_document(...)`，意图“推送失败可跳过”。
  但 `push_document` 内部用 `urllib.request.urlopen`，真实失败抛的是 `urllib.error.URLError`/`HTTPError`
  （继承自 `OSError`），**不是** `RuntimeError`。该 `except` 永远不会命中，kHUB 不可达时冒烟命令直接抛栈崩溃，
  与“推送跳过”的设计意图相悖。另外 `client.py` 本身也不抛 `RuntimeError`，故该分支是死代码。
- 建议修复: 将 `except` 改为捕获 `urllib.error.URLError` / `OSError`（或让 client 统一封装为 `KHubPushError` 基类）；
  明确区分“可跳过”的网络错误与“需中断”的 4xx 错误。

### [Medium] kHUB 推送无重试、无幂等、超时可能偏短

- 位置: `kzocr/khub/client.py:18-44`
- 描述: `push_document` 单次 `urlopen(..., timeout=30)`，无重试/退避；长文档（大 Markdown）在慢网络下 30s 可能不足；
  推送失败后书籍已写入 zai 库但 kHUB 缺失，无自动补偿。虽然传了 `source_id`，但客户端未做“已存在则跳过”的
  去重核对（仅 `verify_in_khub` 存在、默认不启用）。网络抖动会直接导致推送缺口。
- 建议修复: 增加指数退避重试（如 3 次，含 429/5xx 识别）；超时按内容长度自适应或放宽；
  推送前用 `verify_in_khub(source_id=...)` 做幂等去重，避免重复入库；把推送结果（doc_id/状态）结构化回写以便追踪。

### [Medium] SQLite 并发无 busy_timeout，多写者易触发 database is locked

- 位置: `kzocr/adapter/to_zai_prisma.py:80` 与 `kzocr/export_zai.py:16`
- 描述: 两处均用 `sqlite3.connect(str(db))`/`sqlite3.connect(db)` 直连，未设置 `timeout=`（busy timeout）。
  同一 `custom.db` 同时被 zai 的 Prisma 服务与 KZOCR 写入（适配器设计即“直写 zai 库”），存在并发写；
  SQLite 默认 busy timeout 仅 5s，高并发或长事务下极易抛 `database is locked`，且当前代码无该错误捕获/重试。
- 建议修复: 连接时显式 `sqlite3.connect(db, timeout=30)`；写入路径对 `sqlite3.OperationalError` 做有限重试；
  评估是否由单一写者（KZOCR）独占写、zai 只读，避免双写竞争。

### [Medium] 配置在 import 期固化为单例，环境变更需重启且易踩坑

- 位置: `kzocr/config.py:80-89`（`config = load_config()` 模块级求值）
- 描述: `config.py` 在模块导入时即构建并缓存 `config` 单例；`run.py` 默认使用 `app_config.config`。
  这意味着所有配置在“首次 import kzocr”那一刻定型，运行时再设置环境变量不会生效，必须重启进程。
  在长时间运行的服务/守护进程或测试拆分场景中，易因 import 顺序与 env 注入时机不同步而拿到陈旧配置（如误用默认 `/home/keen/...` 路径）。
- 建议修复: 避免模块级单例，改为按需 `load_config()`（或显式注入；当前 `run_engine` 已支持 `config=` 入参，建议 CLI 全程传参而非依赖单例）；
  对路径类默认值加环境可移植性校验（如默认指向不存在的 `/home/keen/...` 时给出明确告警而非静默）。

### [Medium] 适配器写库过程零日志，长链路不可观测

- 位置: `kzocr/adapter/to_zai_prisma.py:75-218` 与 `kzocr/export_zai.py:14-66`
- 描述: `push_book_to_zai` 与 `export_book_markdown` 全程无任何 `logger` 输出（无“开始写库/已写 N 页/提交完成”等）。
  对大书（上千页/上万行），运维无法从日志判断进度或卡点；失败时只能看到异常栈，缺少结构化步骤日志。
- 建议修复: 在写库前后及分页循环内（如每 100 页）输出 `logger.info` 进度；提交后输出各表写入计数；
  导出函数记录目标/字符数。建议统一采用结构化（dict/JSON）日志字段。

### [Medium] _run_vlm 缺整体超时、页数与并发上限保护

- 位置: `kzocr/engine/run.py:383-447`（`_run_vlm`）
- 描述: 逐页 `recognize_page`/`recognize_pages` 循环对单个 PDF 没有整体超时、没有页数上限、没有并发度控制
  （纯串行），也没有每页耗时统计。大 PDF（数百上千页）在弱网/慢模型下可能长时间无输出、资源占用不可控；
  某一页“转圈”不会超时中断整条链路（仅依赖适配器自身 timeout，且 PaddleOCR-VL 分支未显式传 timeout）。
- 建议修复: 增加可选 `--max-pages`、整体 `timeout` 与每页 `timeout`；按页记录耗时/失败；
  对本地 `llama-server` 的 `auto_start=True` 增加启动超时与失败告警（`run.py:192-196`）。

### [Low] setup_submodules.sh 注释与实际行为/健壮性不符

- 位置: `scripts/setup_submodules.sh:2-3`、`9-13`
- 描述: 注释称“用 SSH URL”，但脚本实际执行 `git submodule update --init --recursive`，依赖 `.gitmodules` 中
  已配置的远程（并未在脚本里改写 URL），若 `.gitmodules` 仍为 HTTPS 仍会失败；脚本在 `set -e` 下若 submodule
  初始化失败直接退出，且随后 `ls -d engines/* console/*` 仅做信息展示，未校验子模块是否真正拉取到非空内容，
  下游 `KIMI_ENGINE_DIR` 缺失时才会更晚暴露。
- 建议修复: 脚本内校验子模块目录非空并给出明确失败信息；如需 SSH 回退，显式改写 remote 或说明前提；
  增加 `--remote` 以同步最新提交（视发布策略）。

### [Low] CLI 缺少日志级别/详细度开关，失败输出为原始栈

- 位置: `kzocr/cli.py:24`、`111-147`
- 描述: 仅 `logging.basicConfig(level=INFO)` 写死；无 `--verbose`/`--log-level`，调试期难以提升为 DEBUG；
  各命令对异常无统一兜底，`pipeline`/`export`/`push` 失败即抛栈，缺少“退出码 + 友好错误”的运维友好处理。
- 建议修复: 增加 `--log-level` 参数；在 `main()` 顶层包裹 `try/except` 输出非零退出码与精简错误信息。

### [Low] 引擎失败时静默降级为 mock，易掩盖生产故障

- 位置: `kzocr/engine/run.py:42`、`50`（默认 `require_real=False`）
- 描述: 真实/VLM 引擎失败时默认降级到桩数据，仅 warning 日志。在“非 mock 部署”场景下，这会让真实 OCR 故障
  被伪装成“成功产出 mock 数据”，下游无法区分，故障信号被吞。
- 建议修复: 在 `require_real=False` 的降级路径，除了 warning 外应可配置“告警/上报/标记来源”；
  在落库数据中明确标注 `engine_label="mock"`（`BookResult` 已有 `is_mock`），并建议导出/推送时校验非预期 mock。

### [Info] config.py 中 khub_db 的 getenv 调用冗余（不影响功能）

- 位置: `kzocr/config.py:48-51`
- 描述: `os.environ.get("KHUB_DB", os.path.expanduser(os.environ.get("KHUB_DB", "~/.khub/khub.db")))`
  内层再次 `get("KHUB_DB", ...)`，逻辑上冗余（设了外层就走外层，没设则内层返回默认并 expanduser）。功能正确，但可读性差。
- 建议修复: 简化为 `os.environ.get("KHUB_DB") or os.path.expanduser("~/.khub/khub.db")`。

### [Info] 缺少结构化日志与就绪/健康检查机制

- 位置: 全局（无 JSON 日志、无 health/readiness 端点）
- 描述: 当前为 `%(asctime)s [%(levelname)s] %(name)s: %(message)s` 文本日志。作为要对接 kHUB/zai 的长链路服务，
  缺少可采集的结构化日志字段（book_code、stage、耗时、行数），也未见 readiness/health 探针，不利于上 Prometheus/ELK。
- 建议修复: 引入结构化日志（JSON formatter 或 logging extra）；对关键阶段（engine/zai/khub）输出带 `book_code`、
  `stage`、`duration_ms` 的字段；视部署形态补充健康检查入口。

---

## 运维维度评分

| 维度 | 评分 | 说明 |
|------|------|------|
| 部署 / 依赖 | **D** | pyproject 零依赖声明，重依赖在 mock 路径被强 import，与文档矛盾；子模块脚本健壮性一般。 |
| 可观测性 | **D** | 适配器/导出零日志；CLI 无日志级别；缺少结构化日志与进度/耗时指标；推送与引擎降级信号易被吞。 |
| 可靠性 / 故障恢复 | **D** | 单页失败整书降级、kHUB 推送无重试且冒烟异常处理捕获不到真实异常、并发写 SQLite 无 busy 超时、范式库全清有数据丢失风险。 |
| 安全 / 凭据治理 | **D** | 源码内硬编码明文 API 密钥并作为兜底启用。 |
| 配置管理 | **C-** | 支持环境变量覆盖且多数有默认值，但模块级单例导致运行时 env 变更不生效；路径默认值绑定特定机器。 |

**综合结论：不通过** —— 需优先处理 3 项 High（密钥入库、范式库全清、依赖缺失/误 import）与 4 项 Medium（单页降级、推送异常捕获失效、推送无重试、SQLite 并发）后方可进入生产化部署评估。
