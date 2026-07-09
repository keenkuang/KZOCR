# 评审报告 — 产品经理视角（第 2 轮）

- **角色**：产品经理（独立评审员）
- **评审日期**：2026-07-09
- **评审范围**：
  - kzocr/config.py
  - kzocr/engine/run.py
  - kzocr/engine/types.py
  - kzocr/engine/mock.py
  - kzocr/adapter/to_zai_prisma.py
  - kzocr/cli.py
  - kzocr/khub/client.py
  - kzocr/export_zai.py
  - kzocr/modelscope_pool.py
  - tests/test_pipeline.py
  - tests/test_vlm.py
  - docs/plans/toc-driven-pipeline-design.md（最新设计）
- **总体结论**：**有条件通过**

产品线已打通"mock 引擎 → 适配器写 zai 库 → 导出 Markdown → 推送 kHUB"的全链路，并配有可独立运行的回归测试，基础交付可用。但对照最新设计《TOC 驱动分节 OCR 管线方案》存在明显需求缺口：设计承诺的核心能力（TOC 分析→分节并行 OCR→DeepSeek 后处理→分节拆分→全书整合）在 KZOCR 侧尚未实现；且真实 kimi 引擎路径产出的书籍缺少分页/分行结构，校对台将无法展示内容。需补齐上述缺口后方可视为完整交付。

---

### [High] 真实 kimi 引擎路径产出的书籍无分页/分行结构，校对台显示空书

- 位置: kzocr/engine/run.py:114-119
- 描述: `_run_real()` 调用 kimi 的 `BookPipeline.process_book()` 后，只取回 `final_markdown` 字符串，构造 `BookResult` 时未填充 `pages`（`pages=[]` 使用默认值）。随后 `push_book_to_zai` 遍历 `book.pages` 写入 Page/Paragraph/Line 表，因列表为空，最终 zai 库中该书 `pageCount=0`、`lineCount=0`。真实引擎是面向生产的主路径，其结果对人工校对人员而言是一本"有标题、无内容"的空书，完全丧失产品价值。与之相对，mock 路径（mock.py）和 VLM 路径（run.py:437）都正确填充了 `pages`。
- 建议修复: 在 `_run_real()` 中解析 `BookPipeline` 交付物（或为 `BookPipeline` 结果增加结构化解析），将分节/分页/分行结果映射为 `PageResult/ParagraphResult/LineResult` 并填入 `BookResult.pages`，使 zai 校对台能正常展示。至少应保证 `pageCount/lineCount>0` 与人工可校对行存在。

### [High] 最新设计（TOC 驱动分节管线）在 KZOCR 侧缺少编排入口与配置

- 位置: kzocr/cli.py:111-141、kzocr/config.py:22-41
- 描述: 设计文档《toc-driven-pipeline-design.md》定义了 `pipeline_cli.py`（实施顺序第 7 步：串联 TOC→并行→后处理→拆分→整合）并明确 `kzocr/config.py` 需新增 `deepseek_api_key / deepseek_model / deepseek_base_url / deepseek_rpm`（§2.3）。实测 KZOCR 中：CLI 仅有 `pipeline/export/push/smoke` 四个子命令，`pipeline` 直接对整本 PDF 跑一次性 `run_engine`，并非 TOC 驱动的分节并行管线；`config.py` 中完全不存在 `deepseek` 相关字段（grep 确认）。这意味着设计承诺的核心能力在 KZOCR 侧既无编排入口、也无后处理阶段所需的配置，无法被用户使用或验证。
- 建议修复: 在 `kzocr/config.py` 的 `Config` 与 `from_env()` 中按设计 §2.3 补齐 DeepSeek 四项配置；并新增 TOC 驱动管线的 CLI 入口（或在 `pipeline` 命令内支持 TOC 模式开关），串起 `analyze_toc → process_sections → postproc → split → integrate` 全步骤，使该能力可被端到端调用与验证。

### [Medium] VLM 直接模式与设计目标（SenseNova 双页 + DeepSeek 后处理）不一致，无结构化产出

- 位置: kzocr/engine/run.py:27、kzocr/engine/run.py:267-271
- 描述: 设计文档将 SenseNova 双页上下文 OCR + DeepSeek 分节后处理作为主路径，并承诺输出"结构化、可校对的全书"（含章/病/方三级、9 类字段抽取、方剂结构化）。但当前 `use_vlm` 路径实际默认使用本地 `PaddleOCR-VL-1.6`（run.py:27 `VLM_ENGINE_LABEL="PaddleOCR-VL-1.6"`），且 `_vlm_markdown_to_pages`（run.py:267-271）把每页全部文本塞进**单个 Paragraph 的单个 Line 列表**，既不识别标题/章节、也不做字段抽取与方剂拆分。整条 KZOCR 代码中没有任何 DeepSeek 后处理调用。因此即便走 VLM 路径，校对台拿到的也只是"逐页纯文本"，与设计的"结构化、可分节评审"目标有明显落差。
- 建议修复: 将 VLM 路径与设计的后处理阶段对齐——至少在 `_run_vlm` 之后接入字段抽取与分节/分方拆分（可复用设计 §2.4/§2.5 的思路），并为每页识别 `is_heading`、`node_type`，使 zai 校对台的结构化展示（章节、方剂字段）真正可用。

### [Medium] kHUB 推送失败对用户不可见：CLI 直接抛裸异常

- 位置: kzocr/cli.py:57-68、kzocr/khub/client.py:43
- 描述: `cmd_push` 调用 `khub_client.push_document` 时没有任何 `try/except`。`push_document` 用 `urllib.request.urlopen` 直接发起请求，当 kHUB 服务未启动或地址不可达时会抛出 `urllib.error.URLError`；该异常未被捕获，普通用户（管理员）将看到原始 traceback，既无法判断"推送失败"也无法获得可操作提示。`cmd_smoke` 的 `except RuntimeError` 也捕获不到 `URLError`，同样会崩。作为面向管理员的归档功能，失败缺乏友好反馈与可验证性。
- 建议修复: 在 `cmd_push`（以及 `cmd_smoke` 的推送段）中捕获 `urllib.error.URLError`/`ConnectionError`，输出清晰的错误信息与排查建议（如"kHUB 未运行，请先启动服务"），并以非 0 退出码返回，保证失败对用户可见、可被脚本判定。

### [Medium] 数据库导出的终校文档丢失"术语"与"方剂"两大模块

- 位置: kzocr/export_zai.py:48-63
- 描述: `export_book_markdown`（从 zai 库导出、即 `kzocr export` 使用的函数）只导出 `libType=1`（药名范式）和 `libType=2`（经络穴位范式），**未导出 Term 表与 Formula 表**。而同仓 `adapter/to_zai_prisma.py:254-262` 的 `export_markdown`（内存对象直接导出）却包含"术语"与"方剂"两段。二者行为不一致：校对人员人工终校后导出的"最终文档"会比桩数据导出的版本少掉术语与方剂，交付物不完整。
- 建议修复: 在 `export_zai.py` 中补充从 `Term` 表（termName/sublib/errorPattern/correctForm）与 `Formula`/`FormulaIngredient` 表读取并渲染"术语"与"方剂"章节，使其与 `to_zai_prisma.export_markdown` 的内容范围一致。

### [Low] config.py 的 KHUB_DB 读取冗余，且 khub_db 配置项未被任何代码消费

- 位置: kzocr/config.py:48-51、kzocr/config.py:24-25
- 描述: `from_env()` 中 `khub_db` 通过嵌套 `os.environ.get("KHUB_DB", os.path.expanduser(os.environ.get("KHUB_DB", ...)))` 两次读取同一变量，逻辑冗余易误读。`Config.khub_db` 字段在 `client.py`（kHUB 推送只走 HTTP `khub_base_url`）中从未被使用，属于死配置，会让用户误以为需要配置本地库路径才能自检。
- 建议修复: 简化 `KHUB_DB` 读数为单次 `os.environ.get("KHUB_DB", os.path.expanduser("~/.khub/khub.db"))`；若自检功能暂不实现，建议移除 `khub_db` 字段或在文档中明确标注其为预留、当前未启用。

### [Low] 缺少面向用户的 README/上手文档

- 位置: 仓库根目录（无 README）、kzocr/cli.py（仅有 argparse `--help`）
- 描述: 项目根目录没有 README，唯一文档是设计稿 `docs/plans/toc-driven-pipeline-design.md`。`kzocr --help` 与各子命令 `--help` 可用（较好），但没有任何文档告诉校对人员/管理员：如何配置 `KIMI_ENGINE_DIR`、`ZAI_DIR`、`KHUB_BASE_URL`、`SENSENOVA_API_KEY` 等环境变量，如何对一本真实 PDF 跑全流程，以及如何确认结果已进入 zai 校对台与 kHUB。新用户上手成本偏高。
- 建议修复: 新增一份 README，覆盖：环境准备、必需/可选环境变量清单、四个 CLI 命令的典型用法与预期产物、kHUB 服务对接说明、以及 mock/smoke 自检步骤。

### [Low] mock 引擎的 author 字段带前导空格

- 位置: kzocr/engine/mock.py:106
- 描述: `author=" mock 引擎"` 字符串含前导空格，会被写入 `Book.author` 并出现在导出的 Markdown 元信息中（`来源： 演示出版社`），属低级排版瑕疵，影响样张观感与可验证性。
- 建议修复: 改为 `author="mock 引擎"`（去掉前导空格），或统一为更语义化的占位值。

### [Low] run.py 中 `_merge_cross_page_breaks` 重复计算 last_line

- 位置: kzocr/engine/run.py:323-342
- 描述: 函数内先在第 323-327 行计算 `cur_lines`/`last_line` 并做空行判断，随后在第 330-334 行又重复计算了一遍同样的 `cur_lines`/`last_line`。属死代码/重复逻辑，徒增阅读负担，且与实际落地的跨页合并行为耦合，后续维护易引入不一致。
- 建议修复: 删除重复的第二次计算，只保留一处 `last_line` 求值。

### [Low] modelscope_pool.py 内置明文回退 API Key

- 位置: kzocr/modelscope_pool.py:101、kzocr/modelscope_pool.py:141
- 描述: 文件在 `ProviderSpec.api_key_fallback` 中硬编码了 `MODELSCOPE_API_KEY` 与 `SENSENOVA_API_KEY` 的明文值作为兜底。从用户信任与凭证卫生角度，将可消费的密钥随源码分发会带来配额被冒用、密钥意外泄露的风险，也可能让用户在不自知的情况下用上了并非自己的凭证。
- 建议修复: 移除源码中的明文 `api_key_fallback`，仅通过环境变量注入密钥；若确需演示用默认 key，应在文档中显式声明其用途、归属与用量限制，并提示用户替换为自有密钥。

---

## 产品维度评分

| 维度 | 评分 | 说明 |
|------|------|------|
| 需求对齐 | **C** | 最新 TOC 驱动设计的核心能力（分节并行、DeepSeek 后处理、分节拆分整合）在 KZOCR 侧尚未落地；VLM 路径与设计目标存在偏差。 |
| 用户价值与可验证性 | **C** | mock/VLM 路径结构可用、测试可跑；但真实 kimi 路径产出空书、kHUB 推送失败不可见、DB 导出缺模块，用户（校对/管理员）实际可用性与可验证性不足。 |
| 交付完整性 | **C** | mock→适配器→导出→推送链路打通，但真实引擎路径交付物残缺、缺少 TOC 编排入口与 DeepSeek 配置、缺 README。 |
| 文档与可读性 | **D** | 仅有一份设计稿，无 README/上手指引；部分配置项（khub_db）为死配置易致误解。 |
| 范围控制 | **B** | 未见明显未定义需求的蔓延；主要问题是"设计承诺项缺失"而非"擅自加需求"。 |

> 综合结论"有条件通过"：基础全链路（mock 模式）可用且可验证，但须在下一轮补齐上述 High/Medium 项（真实引擎结构化产出、TOC 管线编排与 DeepSeek 配置、kHUB 推送失败反馈、DB 导出完整性），否则无法对真实用户交付设计所承诺的价值。
