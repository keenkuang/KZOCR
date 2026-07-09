# 多角色评审 · 第 2 轮 — 顶级软件工程师（代码实现质量）

- **角色**：顶级软件工程师（代码实现质量视角）
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
  - （对照）`docs/plans/toc-driven-pipeline-design.md`
- **总体结论**：**有条件通过**（存在 1 项 High 安全问题和若干 Medium 正确性/健壮性问题，建议在合入前修复；其余为 Low/Info 可维护性项）。

---

## [High] modelscope_pool.py 将明文 API Key 硬编码进源码

- **位置**: `kzocr/modelscope_pool.py:101`、`kzocr/modelscope_pool.py:141`
- **描述**: `ProviderSpec` 的 `api_key_fallback` 字段把真实形态的密钥直接写进了源码仓库：
  ```python
  api_key_fallback="ms-40d78a2b-f786-433a-92e3-8e5f4049f602"   # modelscope
  api_key_fallback="sk-4u2jMee2wGvEPtM7qXg6kPkc5H3gDKmw"        # sensenova
  ```
  在 `_ProviderPool.__init__` 中 `api_key = os.environ.get(spec.api_key_env, "") or spec.api_key_fallback`——环境变量一旦缺失，就会自动用源码里的明文 key 发起云端请求。这意味着：
  1. 密钥已随代码进入版本库/镜像，存在泄露风险；
  2. 任何未配置环境变量的人都会**静默**使用他人配额/身份。
- **建议修复**: 删除 `api_key_fallback` 中的明文值，统一改为 `""`，缺失即视为该 provider 禁用；密钥只从环境变量/secret 注入。若需本地兜底，请从 `.env`（不入库）读取。

---

## [Medium] cli.py 的 export 命令在 `--out` 为纯文件名时会崩溃

- **位置**: `kzocr/cli.py:49`
- **描述**:
  ```python
  os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
  ```
  当 `--out foo.md`（无目录）时，`os.path.dirname("foo.md")` 返回 `""`，`os.makedirs("")` 会抛 `FileNotFoundError`（已实测复现：`FileNotFoundError: [Errno 2] No such file or directory: ''`）。默认 `exports/<code>.md` 有目录名不会触发，但用户一旦传入裸文件名即崩溃。
- **建议修复**: 计算目录后判空，例如：
  ```python
  out_dir = os.path.dirname(os.path.abspath(out))
  if out_dir:
      os.makedirs(out_dir, exist_ok=True)
  ```

---

## [Medium] cli.py 的 smoke 命令对 kHUB 未运行的“友好跳过”实际不会触发

- **位置**: `kzocr/cli.py:94`（配合 `kzocr/khub/client.py:43`）
- **描述**: `cmd_smoke` 用 `except RuntimeError as e:` 包住 `push_document`。但 `push_document` 内部调用 `urllib.request.urlopen`，当 kHUB 未运行/连接失败时抛出的是 `urllib.error.URLError`（继承自 `OSError`），**不是** `RuntimeError`。因此异常不会被捕获，函数以 traceback 终止，与设计文档/注释承诺的“推送跳过”行为不符。
- **建议修复**: 在 `client.push_document` 内将连接/HTTP 错误统一包装为 `RuntimeError` 抛出，或在 `cmd_smoke` 的 `except` 中同时捕获 `urllib.error.URLError`/`OSError`。推荐前者（让客户端契约明确）。

---

## [Medium] adapter/to_zai_prisma.py 每次推送会清空全局范式库/术语库/方剂库（多书互损）

- **位置**: `kzocr/adapter/to_zai_prisma.py:91-92`
- **描述**:
  ```python
  for t in ("FormulaIngredient", "Formula", "Pattern", "Term"):
      cur.execute(f"DELETE FROM {t}")
  ```
  注释称“zai 单书模式”，但含义是：推送**任意一本新书**会 `DELETE FROM Pattern/Term/Formula` 全表，即把之前所有书沉淀的范式库、术语库与方剂全部清空。若后续按设计接多书/多批次，先写的数据会被后写覆盖丢失，且过程静默无提示。
- **建议修复**: 至少记录一条 `logger.warning` 明确“全量清空全局表”；更稳妥的做法是为这些表增加 `bookCode`/来源列做按书隔离，或提供 `full_reset` 显式开关，避免默认静默清空。

---

## [Medium] engine/run.py 的 book_code 净化与注释不符（\w 含 Unicode，未真正限制为 ASCII）

- **位置**: `kzocr/engine/run.py:393`
- **描述**:
  ```python
  safe_book_code = re.sub(r"[^\w\-]", "_", raw_book_code)
  ```
  注释写“只保留安全字符（ASCII 字母/数字/连字符/下划线）”，但 Python 默认 `re.UNICODE` 下 `\w` 匹配中日韩等 Unicode 字母。已实测 `re.sub(r'[^\w\-]','_','中医方')` 返回 `"中医方"`——中文标题被整体保留，并非“ASCII 安全”。若 `book_code` 后续用于文件路径/SQL/URL，可能引入非预期的非 ASCII 标识。
- **建议修复**: 若需纯 ASCII 标识，用 `re.sub(r"[^A-Za-z0-9_\-]", "_", raw_book_code)` 或显式 `re.ASCII` 标志；并相应调整注释。

---

## [Medium] 实现与 toc-driven-pipeline-design.md 设计严重偏离（仅 Info 级需说明的吻合度缺口）

- **位置**: 整体对照 `docs/plans/toc-driven-pipeline-design.md` 与 `kzocr/engine/run.py`
- **描述**: 设计方案描述的是“TOC 分析→按节(病)并行 OCR→DeepSeek 分节后处理→小节拆分→全书整合”的管线，并明确要求 `toc_analyzer.py` / `section_ocr.py` / `section_postproc.py` / `subsec_splitter.py` / `book_integrator.py` 等组件。但本次评审的核心代码中**均未实现这些组件**：`run.py` 实际是一个“逐页 VLM 识别 + 跨页合并 + 拼 Markdown”的扁平流水线，没有 TOC 分节、没有并行、没有 DeepSeek 后处理。此外设计文档引用的行号也已漂移（`run.py:417-421` 取下一页顶部 15% → 现位于 416-421 但逻辑被包在 `if supports_two_page`；`run.py:306` 跨页合并 → 现 306 仍存在；`run.py:352` book_code → 现为 391-393），说明设计文档与代码已不同步。
- **建议修复**: 要么补齐设计文档中的 TOC 分节组件，要么更新/降级设计文档以反映当前“逐页 VLM”实现，并删除已失效的行号引用，避免评审与后续开发产生误导。

---

## [Low] engine/run.py `_crop_to_body` 为死代码

- **位置**: `kzocr/engine/run.py:212-249`
- **描述**: `_crop_to_body` 已被定义但全仓库未被任何调用（`grep` 仅命中定义处）。设计文档曾计划把它提取为共享 helper，但实际未被 `_run_vlm` 使用，属遗留死代码。
- **建议修复**: 若后续 section 级 OCR 需要版心裁剪则补调用；否则删除，避免维护负担与“看似有用实则不跑”的误导。

---

## [Low] engine/run.py `_PAGE_END_INCOMPLETE` 为死代码

- **位置**: `kzocr/engine/run.py:301-303`
- **描述**: 该正则编译后在 `_merge_cross_page_breaks` 中并未被引用——跨页判定实际用的是 `_SENTENCE_END` 集合（330 行之后），而非常量 `_PAGE_END_INCOMPLETE`。
- **建议修复**: 删除未使用常量，或将判定逻辑统一到一处。

---

## [Low] engine/run.py `_merge_cross_page_breaks` 存在重复计算

- **位置**: `kzocr/engine/run.py:323-334`
- **描述**: `cur_lines = cur.split("\n")`、`last_line = cur_lines[-1].strip()` 与空行判断在 323-327 行计算一次，紧接着 330-334 行又**原样重复计算一次**。逻辑冗余，可读性差。
- **建议修复**: 删除第一段重复计算，只保留一处。

---

## [Low] engine/run.py 一次性 `list(doc)` 物化全部页对象（大书内存放大）

- **位置**: `kzocr/engine/run.py:407`
- **描述**: `all_pages = list(doc)` 把整本 PDF 的 Page 对象一次性全部载入内存，仅为便于取 `all_pages[i+1]` 双页上下文。对页数很多的书是明显的峰值内存放大，且这些对象长期持有至循环结束。
- **建议修复**: 改用生成器 + 滑动窗口缓存（保留上一页/下一页引用）或按索引 `doc[i]`、`doc[i+1]` 惰性访问，避免全量物化。

---

## [Low] engine/run.py `_SENTENCE_END` 在循环内重复构造

- **位置**: `kzocr/engine/run.py:330`
- **描述**: `set("。！？；）】」\"\'")` 在 `for i in range(...)` 每次迭代都重新构造。虽开销极小，但属无谓重复，常量应提到模块级。
- **建议修复**: 提取为模块级 `_SENTENCE_END` 常量。

---

## [Low] Markdown 渲染逻辑三处分叉，易漂移

- **位置**: `kzocr/engine/mock.py:153`（`_render_markdown`）、`kzocr/adapter/to_zai_prisma.py:221`（`export_markdown`）、`kzocr/export_zai.py:14`（`export_book_markdown`）
- **描述**: 三处各自实现了一份“Book → Markdown”的渲染：
  - `mock._render_markdown`：仅含 药名/经络/上下文 三类范式；
  - `adapter.export_markdown`：额外含 术语/方剂；
  - `export_zai.export_book_markdown`：从 DB 读，只含 药名/经络 两类，不含 上下文/术语/方剂。
  导出结果随入口不同而不一致，且任一处改版需同步三处，维护风险高。
- **建议修复**: 抽出单一共享渲染函数（可按 `include_*` 开关裁剪），三处调用同一实现。

---

## [Low] export_zai.export_book_markdown 表缺失时抛出误导性错误

- **位置**: `kzocr/export_zai.py:16-22`
- **描述**: `sqlite3.connect(db)` 在库文件不存在时会**新建空文件**；随后 `SELECT ... FROM Book` 在表不存在时抛 `sqlite3.OperationalError("no such table")`，而非被捕获后给出 `ValueError("未找到书籍")`。“库存在但无此书”与“库/表根本不存在”两种情形被混为一谈，错误信息不准确。
- **建议修复**: 先 `SELECT name FROM sqlite_master WHERE type='table' AND name='Book'`，缺失则明确提示“数据库未初始化（需先 pipeline/smoke 写入）”。

---

## [Low] tests 使用已废弃的 tempfile.mktemp 且未清理临时文件

- **位置**: `tests/test_pipeline.py:28`、`tests/test_pipeline.py:54`
- **描述**: `tempfile.mktemp()` 官方已标记为不安全（存在竞态、可被预测）；且测试结束后临时 `.db` 文件未删除，反复运行会残留。
- **建议修复**: 改用 `tempfile.TemporaryDirectory()` 上下文管理器，自动隔离并清理。

---

## [Info] engine/run.py `_pdf_page_to_numpy` 对灰度页（pix.n==1）的鲁棒性

- **位置**: `kzocr/engine/run.py:209`
- **描述**: `np.frombuffer(...).reshape(pix.height, pix.width, 3)` 假定 3 通道。当前 `page.get_pixmap()` 默认产出 RGB（n=3），所以一般没问题；但若上游传入/生成灰度 Pixmap（n=1），`reshape(...,3)` 会因样本数不匹配抛 `ValueError`。属防御性边界问题，当前路径未触发。
- **建议修复**: 统一 `pix = fitz.Pixmap(pix, 0)`（强制转 RGB 去 alpha）后再 reshape，或按 `pix.n` 分支处理。

---

## [Info] config.py 模块级单例 `config = load_config()` 与 CLI 每次重新 `load_config()` 并存

- **位置**: `kzocr/config.py:89`（配合 `kzocr/cli.py:18,29,44,72`）
- **描述**: `config.py` 在 import 时构造模块级单例 `config`；`engine/run.py` 默认也使用它（`app_config.config`）。但 CLI 每个子命令都重新 `load_config()` 得到新对象。若某代码路径以 `config=None` 调用 `run_engine`，用的是“import 时刻”的环境快照，可能与 CLI 已覆盖（如 `cfg.zai_db = ...`）之后的配置不一致。
- **建议修复**: 明确单一真相来源（例如 CLI 始终显式传递 `config`，或提供 `get_config()` 懒加载），避免 import 期副作用与运行期重载两套并存。

---

## 代码质量维度评分

| 维度 | 评分 | 说明 |
|------|------|------|
| 正确性 (Correctness) | **B-** | 发现若干真实 bug：`os.makedirs("")` 崩溃、smoke 的 `except` 过窄导致跳过失效、全局 `DELETE` 互损、book_code 净化与注释不符。核心链路在 mock 下可跑通，但边界/健壮性有硬伤。 |
| 可读性 / 可维护性 (Readability) | **B** | 命名总体清晰、函数职责基本分明；但存在死代码（`_crop_to_body`/`_PAGE_END_INCOMPLETE`）、重复计算、三处 Markdown 渲染分叉、设计文档与代码行号漂移。 |
| 性能 (Performance) | **B+** | 整体无严重性能问题；`list(doc)` 全量物化、循环内重复构造集合等属次要放大，可按 Low 项优化。资源句柄（PDF `doc.close`、SQLite `conn.close`）均有 `finally`/上下文管理，释放到位。 |
| 错误处理 (Error handling) | **C+** | 引擎层降级策略清晰（mock/require_real）；但适配器静默清空全局表、客户端错误类型未被 CLI 捕获、`export_zai` 表缺失错误信息误导、temp 路径竞态等，fail-fast 与错误表述不够严谨。 |

> 结论重申：**有条件通过**。建议优先处理 1 项 High（硬编码密钥）与 4 项 Medium（export 崩溃、smoke 跳过失效、全局清空互损、book_code 净化不符），Low/Info 项可在后续迭代清理。
