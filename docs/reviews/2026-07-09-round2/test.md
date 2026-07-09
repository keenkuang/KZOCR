# 第 2 轮多角色评审 · 测试工程视角

- **角色**：顶级测试工程师
- **评审日期**：2026-07-09
- **评审范围**：
  - `tests/test_pipeline.py`
  - `tests/test_vlm.py`
  - `kzocr/engine/run.py`
  - `kzocr/engine/mock.py`
  - `kzocr/engine/types.py`
  - `kzocr/adapter/to_zai_prisma.py`
  - `kzocr/khub/client.py`
  - `kzocr/cli.py`
  - `kzocr/config.py`
  - `docs/plans/toc-driven-pipeline-design.md`
- **总体结论**：**有条件通过（Conditional Pass）**

## 概述

套件现有 15 个用例，全部在本地环境（pytest 9.1.1 / Python 3.13）通过，提供了 mock 引擎 → 适配器写 zai 库 → Markdown 导出 这条端到端链的可用回归覆盖，以及 VLM 路由/失败降级的逻辑测试。但评审范围内明确列为“核心路径”的 **CLI** 与 **kHUB 推送** 两块完全没有任何测试；多个新引入的领域/图像处理函数（跨页合并、噪声清洗、版心裁剪、VLM 适配器选择、真实引擎路径）同样零覆盖。此外发现一处**已确认的真实缺陷**：smoke 流程对 kHUB 推送的异常处理类型写错，会在 kHUB 未启动时崩溃；以及设计文档声称“跨页合并已测试”与实际不符（假阳性信号）。

因此评为“有条件通过”：核心 mock→DB→导出链质量可接受，但必须在补齐 CLI/kHUB 覆盖、修复异常捕获缺陷、并纠正测试/文档不一致后方可视为通过。

---

### [High] kHUB 推送路径零测试，且异常捕获类型错误会导致 smoke 崩溃

- **位置**：`kzocr/cli.py:92-104`（`cmd_smoke`）；`kzocr/khub/client.py:18-44`（`push_document`）；`kzocr/cli.py:57-68`（`cmd_push`）
- **描述**：
  1. `kzocr/khub/client.py` 的 `push_document` 通过 `urllib.request.urlopen` 发请求，连接失败会抛出 `urllib.error.URLError`（其继承链为 `OSError`，**不是** `RuntimeError`——已验证 `issubclass(urllib.error.URLError, RuntimeError) == False`）。
  2. `cmd_smoke` 用 `except RuntimeError` 包裹推送，意图是“kHUB 未启动时优雅跳过”。但 kHUB 没起时 `urlopen` 抛 `URLError`，该 `except` 捕获不到，进程会**直接抛栈崩溃**，与注释“推送跳过”的预期行为相反。
  3. 整个 `push_document` / `verify_in_khub` 没有任何单元测试（未 mock `urllib`），推送请求体构造、鉴权头注入、404 去重、非 404 HTTP 错误重抛等分支全部未覆盖。
- **建议修复**：
  - 将 `kzocr/cli.py:103` 的 `except RuntimeError` 改为同时捕获 `urllib.error.URLError` / `OSError`（或直接捕获 `Exception` 并区分），并在测试里用 `unittest.mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("conn refused"))` 验证 smoke 在 kHUB 缺失时仍能完成并给出 warning。
  - 新增 `tests/test_khub.py`：mock `urllib.request.urlopen`，断言 `push_document` 发送的 JSON 含 `title/content/format/source`、存在 `Authorization` 头当 `KHUB_API_TOKEN` 设置、`verify_in_khub` 在 404 时返回 `[]`、在其他 `HTTPError` 时上抛。

### [High] CLI（kzocr/cli.py）零单元测试

- **位置**：`kzocr/cli.py:28`（`cmd_pipeline`）、`:43`（`cmd_export`）、`:57`（`cmd_push`）、`:71`（`cmd_smoke`）、`:111`（`build_parser`）、`:144`（`main`）
- **描述**：评审范围明确把“CLI”列为需测试的核心路径，但 `tests/` 下仅有 `test_pipeline.py` 与 `test_vlm.py`，无任何针对 `cli.py` 的测试。参数解析（`build_parser` 的子命令/`--book-code`/`--db`/`--skip-push`/`--verify`）、`main` 的派发、`cmd_pipeline` 的 `--db` 隔离默认名（落到 `kzocr.db`）、`cmd_export`/`cmd_push` 的文件读写与输出路径，均无回归保护。CLI 是用户主入口，缺测风险高。
- **建议修复**：新增 `tests/test_cli.py`，用 `unittest.mock.patch` 隔离 `engine_run.run_engine`、`push_book_to_zai`、`export_book_markdown`、`khub_client.push_document`，并通过 `kzocr.cli.main(["pipeline","x.pdf","--db","t.db"])` 等形式断言返回码、写库调用参数、`PRINT` 的 `BOOK_CODE=` 等副作用。至少覆盖 `build_parser` 的 required subcommand 与各命令成功路径。

### [Medium] 跨页合并逻辑 `_merge_cross_page_breaks` 零测试，且设计文档谎称“已测试”

- **位置**：`kzocr/engine/run.py:306`（`_merge_cross_page_breaks`）；`docs/plans/toc-driven-pipeline-design.md:134`（“`_merge_cross_page_breaks(pages_text)`（kzocr/engine/run.py:306 已测试）”）
- **描述**：`_merge_cross_page_breaks` 是 TCM 方剂跨页断裂合并的核心领域逻辑（含大量正则与续接判定分支），但 `tests/` 中没有任何用例引用它（已 grep 确认）。设计文档却声称其“已测试”，构成测试/文档不一致——这是典型的“假阳性信号”：评审者会误以为该关键逻辑受保护。该函数的多分支（页末不完整判定、章节标题排除、`来源/组成/...` 字段标识终止续接、装饰行跳过）极易回归且当前完全裸奔。
- **建议修复**：
  - 新增针对 `_merge_cross_page_breaks` 的单元测试：构造“上页末以 `、` 结尾 + 下页续接行”应合并、上页末以 `。` 结尾不应合并、`26.4 鳖甲消瘤方` 这类编号标题应终止续接、遇到 `来源：` 字段标识应终止续接等场景。
  - 修正 `docs/plans/toc-driven-pipeline-design.md:134` 的“已测试”措辞，改为“待补充测试”。

### [Medium] VLM 适配器选择分支 `_init_vlm_adapter` 零测试

- **位置**：`kzocr/engine/run.py:155-196`（`_init_vlm_adapter`）
- **描述**：该函数包含真实分支逻辑（按 `cfg.vlm_engine` 与 `SENSENOVA_API_KEY` 选择 SenseNova，失败再降级到 PaddleOCR-VL-1.6），是 VLM 直接模式的关键路由，但 `test_vlm.py` 中 `_init_vlm_adapter` 始终被 `MagicMock` 整体替换，其内部 SenseNova↔PaddleOCR 降级链、参数（api_key/model/base_url/timeout、host/port/auto_start）构造、导入失败兜底均未被验证。
- **建议修复**：新增测试，用 `patch` 控制 `cfg.vlm_engine` 与 `sensenova_api_key`，并 mock `tcm_ocr.core.engines.*` 的导入，断言在 `vlm_engine="sensenova"` 时返回 SenseNova 适配器、`vlm_engine="auto"` 且无 key 时降级到 PaddleOCR-VL、以及 SenseNova import 抛错时回退成功。

### [Medium] VLM 输出噪声清洗 `_vlm_postprocess` 零测试

- **位置**：`kzocr/engine/run.py:283-289`（`_vlm_postprocess`）+ `:275-280`（`_VLM_CLEANUP_RULES`）
- **描述**：正则清洗规则（`\(`→`(`、特殊符号→`：`、`秘方求真` 页眉页脚去除、多余空行压缩）是 VLM 直接模式的输出质量保障，但无任何测试。一旦其中某条正则写错（如 `秘方求真\s*\\?\(?...` 这种带转义的表达式），不会有人发现。
- **建议修复**：新增 `tests/test_vlm.py` 中对 `_vlm_postprocess` 的用例：输入含 `秘方求真 R`、`秘方求真$`、`( )`、连续空行，断言清洗后输出符合预期。

### [Medium] PDF 渲染/版心裁剪 `_pdf_page_to_numpy` 与 `_crop_to_body` 无测试

- **位置**：`kzocr/engine/run.py:199-209`（`_pdf_page_to_numpy`）、`:212-249`（`_crop_to_body`）
- **描述**：这两个函数承载真实的 numpy/fitz 像素处理（RGBA→RGB 转换、水平/垂直投影裁剪版心）。`test_vlm_renders_pdf_pages_to_markdown` 用 `MagicMock` 顶替 `page.get_pixmap().samples`，使这些函数只在“全白 255 图像”这一退化输入下被间接执行，未断言输出的 numpy 形状、通道、裁剪边界正确性；RGBA/gray 等非平凡分支完全未覆盖。
- **建议修复**：构造真实的小尺寸 `np.ndarray`（含 RGBA 4 通道、含文字暗像素行/列边界），直接调用 `_crop_to_body` 断言裁剪后的高度/宽度与上下边界；对 `_pdf_page_to_numpy` 用真实 `fitz.Pixmap` 或等价构造验证 reshape 与通道转换。

### [Medium] 真实引擎路径 `_run_real` 零行为测试，且真实失败降级分支未覆盖

- **位置**：`kzocr/engine/run.py:94-119`（`_run_real`）；`kzocr/engine/run.py:45-51`（真实引擎失败降级）
- **描述**：`_run_real` 是 `use_mock=False` 且 `use_vlm=False` 时的默认生产路径，但其行为在 `test_vlm.py` 中仅被 `MagicMock` 替换用于验证路由（`test_routes_to_real_when_use_vlm_is_false`、`test_run_real_regression_unaffected`），从未以真实或接近真实的输入执行；尤其 `run_engine` 第 45-51 行的“真实引擎抛错→降级 mock（非 require_real）”分支没有任何测试（现有降级测试只覆盖了 VLM 分支）。该分支一旦回归会静默吞错并产出假 mock 数据。
- **建议修复**：因重依赖难装，至少对 `run_engine` 的“`_run_real` 抛 `Exception` 且 `require_real=False`→返回 `is_mock` book、`require_real=True`→上抛”做与 VLM 分支对称的路由+降级测试（mock `_run_real` 抛错即可，无需真实引擎）。

### [Low] `test_vlm_markdown_to_pages_empty` docstring 与断言自相矛盾

- **位置**：`tests/test_vlm.py:189-193`
- **描述**：docstring 写“空输入应返回空列表”，但断言对象是 `len(pages) == 1`（实际 `_vlm_markdown_to_pages([""])` 返回 1 个 paragraphs 为空的 `PageResult`）。测试名与文档暗示“空→空”，代码却断言“空→1 个空页”，阅读者会被误导；同时传入 `[""]` 与传入 `[]` 行为不同（后者才返回空列表），“空输入”语义含糊。
- **建议修复**：明确意图——若设计为“每个输入元素恒产生一页”，则改 docstring 为“单个空字符串应产出一个无段落的空页”并保留 `len(pages)==1`；若期望空输入返回 `[]`，则改实现与断言一致，并补 `len(pages)==0` 的专门用例。

### [Low] `tests/test_pipeline.py` 使用已弃用的 `tempfile.mktemp` 且不清理临时库

- **位置**：`tests/test_pipeline.py:28`、`tests/test_pipeline.py:54`
- **描述**：`tempfile.mktemp(suffix=".db")` 已被 Python 标记为不安全（存在竞态/同名覆盖风险），且用例执行后不删除生成的 `.db` 文件，多次运行会在 cwd 残留 `*.db` 临时文件。
- **建议修复**：改用 `tempfile.TemporaryDirectory()` 或 `tempfile.mkstemp`，在 `try/finally` 或 fixture 中自动清理。

### [Info] `run.py` 顶层 `import fitz` / `import numpy` 使 VLM 测试采集强依赖重库

- **位置**：`kzocr/engine/run.py:17-18`
- **描述**：`test_vlm.py` 通过 `from kzocr.engine.run import ...` 触发顶层 `import fitz`/`import numpy`，与“轻依赖可单测”目标存在张力（当前环境已装，故可采集；但 CI 隔离性弱，缺这两个库会令整套 VLM 测试在采集期即整体失败）。
- **建议修复**：若未来要 CI 隔离，可将 `fitz`/`numpy` 改为函数内惰性导入，或在 `pyproject.toml` 显式标注测试依赖。当前仅作风险提示，不阻断通过。

### [Info] 推送幂等（`DELETE` 再 `INSERT`）与 `.zai_prisma_marker` 写入行为无测试

- **位置**：`kzocr/adapter/to_zai_prisma.py:86-92`（幂等清理）、`:212-214`（marker 写入）
- **描述**：`push_book_to_zai` 对同一 `bookCode` 先删后插（幂等），且 `skip_prisma_marker=False` 时会写 marker 文件。这些边界行为（重复 push 不重复累加、marker 内容正确性）目前无断言。
- **建议修复**：补充“同一 book 二次 push 后行数不变”“`skip_prisma_marker=False` 时生成 `<db>.zai_prisma_marker` 且内容为 book_code”的断言。

---

## 测试维度评分

| 维度 | 评分 | 说明 |
|------|------|------|
| 覆盖（Coverage） | **C** | mock→zai 写库→导出端到端链覆盖良好；但 CLI、kHUB 推送、`_run_real`、跨页合并、噪声清洗、版心裁剪、适配器选择等关键路径/函数为 0 覆盖。 |
| 正确性（Correctness） | **B** | 现有断言基本合理，无明显的“永远通过”假绿；但 `test_vlm_markdown_to_pages_empty` 的 docstring/断言矛盾、`_init_vlm_adapter` 仅被整体 mock 替换（未校验入参与分支）属瑕疵。 |
| 可测性（Testability） | **B** | 生产代码对全局/副作用依赖可控（DB 路径、配置均可注入），VLM 外部依赖可 mock；扣分点：顶层重依赖导入、`cmd_smoke` 异常处理类型错误导致该路径难以安全自动化。 |
| 测试/实现一致性 | **C** | 设计文档 `toc-driven-pipeline-design.md:134` 声称跨页合并“已测试”，实际无测试；其余测试针对当前实现而非过期接口。 |
| 端到端可跑通性 | **B** | 无重依赖下 mock→适配器→导出链路确有可运行测试（`test_pipeline.py`）并能跑通；但“→推送 kHUB”末端在无服务时因异常处理缺陷会崩溃，端到端 smoke 不健壮。 |

**总评**：有条件通过。放行前建议优先处理两条 High（CLI 测试、kHUB 推送测试+异常捕获修复）与 Medium 中的跨页合并/适配器选择/噪声清洗测试与文档修正。
