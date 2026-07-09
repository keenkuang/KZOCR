# 使用客户评审（第 2 轮）

- **角色**: 使用客户（终端用户）—— 中医文献整理者 / 校对人员，需要把扫描版中医古籍转成可检索、可人工校对的文档
- **评审日期**: 2026-07-09
- **评审范围**:
  - README.md（期望的上手文档）
  - kzocr/cli.py
  - kzocr/config.py
  - kzocr/engine/run.py
  - kzocr/export_zai.py
  - kzocr/khub/client.py
  - docs/plans/toc-driven-pipeline-design.md
  - tests/test_pipeline.py
- **总体结论**: **不通过**

> 一句话：设计文档把"目录分析 → 分节并行 OCR → 后处理 → 按小节拆分 → 全书整合"吹得很完整，但代码里这些一个都没落地；更糟的是真实引擎一出错就**偷偷塞给你一份固定假数据**还不吭声。作为要靠它出校对稿、甚至推送进知识库的人，这两点任何一条都让我不敢用。

---

### [High] 没有 README，新用户根本无法 10 分钟上手

- 位置: 项目根目录（无 README.md，仅存在 CHANGELOG.md）；最接近"上手文档"的是 kzocr/cli.py:1-8 的模块 docstring
- 描述: 项目根目录里没有 README。我作为新用户，不知道怎么装依赖、要不要先 `git submodule update`、要设哪些环境变量、kHB 服务怎么起。cli.py 顶部那段 docstring 提到了四条命令，但没说配置从哪来、子模块没拉会怎样。CHANGELOG 是给开发者看的变更记录，不是上手指南。
- 建议修复: 补一个 README，至少含：安装步骤、子模块初始化、必填环境变量清单（KIMI_ENGINE_DIR / ZAI_DIR / KHUB_BASE_URL / KZOCR_* ）、一条"10 分钟跑通示例"（pipeline → export → push）、以及哪些功能当前只是设计未实现。cli.py 的 `--help` 应作为最低限度的兜底，README 不能省。

### [High] 设计承诺的「TOC 分节管线」全程未实现（承诺了但代码没落地）

- 位置: docs/plans/toc-driven-pipeline-design.md:116-157（组件设计）、214-224（实施顺序）对照 kzocr/ 实际文件清单
- 描述: 设计文档第 2 节承诺了 `toc_analyzer.py / section_ocr.py / section_postproc.py / subsec_splitter.py / book_integrator.py / pipeline_cli.py` 六个模块，并把"先识目录 → 按病分节并行 OCR → DeepSeek 后处理 → 按 `数字.数字` 拆小节 → 全书整合"列为全部价值所在。但我在 kzocr/ 下实际只找到 cli.py / config.py / engine/* / adapter/* / export_zai.py / khub/*。那六个文件**一个都不存在**。实际 `kzocr pipeline` 命令（kzocr/cli.py:28-40）只是"整本 PDF 丢给引擎 → 写 zai 库"，完全没有目录分析、没有分节、没有后处理、没有拆分、没有整合。文档第 5 节画的 output/<book_code>/sections/... 目录结构也从未产生。
- 建议修复: 要么把设计里已实现的标注清楚、未实现的明确写"TODO/未实现"并从设计文档标题去掉"已支持"的暗示；要么按实施顺序把六个模块补上。作为用户，我需要一眼知道：现在到底能用的是"整本 OCR"还是"分节智能管线"——别让我读完设计以为有分节能力，跑起来才发现是整本糊上去的。

### [High] 引擎/VLM 一失败就静默回退成"固定假数据"，还假装成功

- 位置: kzocr/engine/run.py:39-43（VLM 分支）、47-51（真实引擎分支）；假数据内容见 kzocr/engine/mock.py:103-150
- 描述: 这是我最不能接受的一点。`run_engine` 里无论 VLM 还是 kimi 真实引擎，只要抛任何异常，就被 `except Exception` 吞掉，只打一条 `logger.warning`，然后返回 `build_mock_book(...)`。而 mock 数据是**写死的占位内容**——标题固定叫"中医方剂验案选（样张）"，正文是"方用白术三钱……""取足三里……"这种和我的 PDF 毫无关系的样例。换句话说：引擎崩了 → 不报错 → 写库 → 我在 zai 校对台看到的是一份假书，还以为 OCR 成功了。除非我事先 `export KZOCR_REQUIRE_REAL=1`，否则全程无感。对要出最终校对稿、还要推 kHUB 的人，这是" publish 假古籍"级别的风险。
- 建议修复: 默认行为应是**失败即失败**（抛错或明确非零退出 + 醒目提示），绝不能悄悄用 mock 覆盖真实输入。mock/降级只能显式开关（如 `KZOCR_USE_MOCK=1`）下才允许，且必须在日志和 stdout 用 ERROR 级、带"⚠ 本次为占位假数据，非真实 OCR"的醒目警告，并让 `kzocr pipeline` 退出码非零。

### [Medium] pipeline 写库的库 与 export 读库的库默认不一致，export 直接报错

- 位置: kzocr/cli.py:30-34（pipeline 默认 `zai_db = "kzocr.db"`）对照 kzocr/cli.py:44-47（export 默认读 `load_config().zai_db`，即环境变量 ZAI_DB，缺省为 `<zai_dir>/db/custom.db`）
- 描述: 我按直觉跑 `kzocr pipeline 我的书.pdf`（不带 --db），数据被写进当前目录的 `./kzocr.db`。然后我跑 `kzocr export <book_code>`（也不带 --db），它去读 `<zai_dir>/db/custom.db`，结果 `export_zai.py:22` 抛 `ValueError: 未找到书籍`。也就是说"写入的库"和"读出的库"默认不是同一个，用户必须每次手动带 `--db` 才能对上，否则就报"没这本书"。
- 建议修复: pipeline 与 export 应使用**同一个默认库**（例如统一走 `KZOCR_ZAI_DB` 或一个固定本地库），或 pipeline 跑完把实际 db 路径和 book_code 写进一份 manifest 文件，export 默认读该 manifest。至少 CLI `--help` 要把"pipeline 和 export 必须传同一个 --db"写清楚。

### [Medium] 出错时直接甩 Python traceback，不是人话

- 位置: kzocr/khub/client.py:43-44（urlopen 失败抛 URLError，kzocr/cli.py:57-68 的 cmd_push 未捕获）；kzocr/export_zai.py:22（未找到书籍抛 ValueError，kzocr/cli.py:43-54 的 cmd_export 未捕获）
- 描述: `push` 时如果 kHUB 没起或地址错，`urllib.request.urlopen` 会抛出 `URLError`，直接糊我一脸 traceback。export 时 book_code 写错或库不对，直接 `ValueError` traceback。作为非开发用户，我希望看到的是"推送失败：连不上 kHUB（http://127.0.0.1:8000），请确认服务已启动"这种一句人话，而不是去读堆栈。
- 建议修复: 在 cli 层用 `try/except` 包住各子命令，把常见错误（网络、文件不存在、书籍未找到、配置缺失）转成一句中文提示 + 非零退出码，traceback 仅 `--debug` 时打印。

### [Medium] 大本书无进度/无 ETA，真实引擎路径几乎零反馈，容易以为卡死

- 位置: kzocr/cli.py:35-39（pipeline 仅一条 `运行引擎：pdf` 日志）；kzocr/engine/run.py:_run_real（真实 kimi 引擎路径无任何进度日志）
- 描述: 跑一本几百页的古籍，用户面前只有一条"运行引擎：xxx.pdf"就再没动静。VLM 路径每 5 页会 log 一次（run.py:410-411，不错），但真实 kimi 引擎路径（`_run_real`）从 `process_book` 到读完交付物全程无日志。一本大书可能要等很久，没有任何百分比/已处理页数/预计剩余，我会一直怀疑它是不是静默卡死了。
- 建议修复: 在 `_run_real` 和 pipeline 命令层加进度反馈（已处理页数 / 总页数 / 已用时间）。至少每 N 页或每分钟打一条 INFO，让用户知道还活着。

### [Low] export 把整库的全局范式库塞进"某一本书"的导出里

- 位置: kzocr/export_zai.py:48-63（导出时 `SELECT ... FROM Pattern` 全库无 book_code 过滤）
- 描述: 我导出某一本书的校对稿，结果 md 末尾被追加了"三大永久范式库"，而且是所有书累计的"药名/经络"范式，不是这本书的。对只想拿这一本书的人来说是噪音；推到 kHUB 后每本书都带一份全库范式，也会造成重复索引。
- 建议修复: 导出单本书时只带本书沉淀的范式（按 book_code 关联），或把"全库范式库"做成独立导出命令，不要默认塞进每本书。

### [Low] pipeline 只把 BOOK_CODE 打印到 stdout，export 又必须用它，容易丢

- 位置: kzocr/cli.py:39（`print(f"BOOK_CODE={...}")`）对照 kzocr/cli.py:43-47（export 需要 book_code 参数）
- 描述: pipeline 跑完只 `print` 一行 `BOOK_CODE=xxx`，export 必须手动传这个 code。一旦终端滚动没了或我换了个 shell，就不知道 code 是啥。结合上面"库默认不一致"的问题，找书/找库都靠记忆，体验很碎。
- 建议修复: pipeline 跑完顺手写一份 `exports/<book_code>.meta.json`（含 book_code、db 路径、引擎、时间），export 默认从最近一份 meta 读取；或 export 支持按书名模糊匹配。

### [Info] config.from_env 里 KHUB_DB 的嵌套取值冗余且易误读

- 位置: kzocr/config.py:48-51
- 描述: `khub_db = os.environ.get("KHUB_DB", os.path.expanduser(os.environ.get("KHUB_DB", "~/.khub/khub.db")))` —— 内外层都读 `KHUB_DB`，内层默认值其实是字符串 `"~/.khub/khub.db"`，逻辑上等价于直接 `os.environ.get("KHUB_DB", "~/.khub/khub.db")` 再 expanduser。虽然能跑，但读起来像 bug，建议简化，避免后人误改。
- 建议修复: 简化为 `khub_db = os.path.expanduser(os.environ.get("KHUB_DB", "~/.khub/khub.db"))`。

---

## 用户体验维度评分

| 维度 | 评分 | 说明 |
|------|------|------|
| 上手成本 | **D** | 无 README，配置全靠读源码；子模块/环境变量无清单 |
| 工作流契合 | **D** | 设计承诺的目录分析/分节并行/后处理/拆分/整合全部未实现，实际只是整本 OCR |
| 错误体验 | **D** | 引擎失败静默回退假数据；普通错误直接甩 traceback |
| 性能与反馈 | **C** | VLM 路径有每 5 页日志，真实引擎路径零反馈；无 ETA |
| 输出可用性 | **B** | 导出 md 结构清晰、能送 kHUB；但默认库不一致会让人导不出、且全库范式噪音 |

> 评分等级：A 优秀 / B 良好 / C 及格 / D 偏弱 / E 差 / F 不可用。

**阻断性条件（必须先解决才能"通过"）**：
1. 引擎失败严禁静默回退假数据（High #3）。
2. 补 README 与可用的 10 分钟上手示例（High #1）。
3. 明确标注 TOC 分节管线的实现状态，别让设计文档误导用户以为已可用（High #2）。
4. 统一 pipeline/export 的默认库，消除"导不出书"的坑（Medium #4）。
