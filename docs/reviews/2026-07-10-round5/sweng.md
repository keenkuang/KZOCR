# KZOCR v0.4 AMEND — 软件工程评审

**结论：** C1–C5 整体审慎，C2/C3 有实现细节需补缺；现有 46 测试不受影响。

- [x] **C1** — `kzocr/engines/leakage.py` 是正确选择。`run.py:365` 的 `_merge_cross_page_breaks` 只做句末标点启发式，C1 4 层防御（基线+token 上限+重试+探针）复杂度远超前者，合入 `run.py` 会使其膨胀到 ~600 行。新文件独立可测、可被 TOC 管线复用。
- [ ] **C2** — `atomic_write` 的 `path.with_suffix(path.suffix + ".tmp")` 在 path 无后缀时退化为 `.tmp` 覆盖原文件（`foo` → `foo.tmp`），丢失原始文件类型；建议 `tmp = path.parent / (path.name + ".tmp")`。临时文件不需要清理——`os.replace` 原子的 consume 了 tmp，进程崩溃后重跑会覆盖旧的 `.tmp`。但当前 `to_zai_prisma.py:296` 的 `op.write_text()`（`export_markdown`）和 `cli.py:63` 的 `open().write()`（`cmd_export`）都非原子写，**建议这次就把它们升级为 `atomic_write`**，否则 C2 工具模块写好却没有消费者。
- [ ] **C3** — 集成点有两处：(1)`modelscope_pool.py:197` `_ProviderPool._do_call()` 内部 `self._client.chat.completions.create()` 之前——这里 insert `AdaptiveRateLimiter.wait()`，503/429 响应由外层 `chat()`/`chat_vision()` 的 `except` 块反馈给限流器降速。(2)`run.py:233` `SenseNovaAdapter` 的 `recognize_page()` 调用——需在 `kzocr/engines/` 侧包一层 wrapper，或改造 `SenseNovaAdapter` 接受限流器参数。**不需要 context manager**；纯同步项目用 `limiter.wait()` + `try/except` 反馈即可——context manager 会强制 `with` 嵌套，在已经有多层 `try/except` 的 `_run_vlm` 循环中（`run.py:468-490`）反而增加缩进复杂度。
- [x] **C4** — `book_pipeline.py` 在外部包 `tcm_ocr` 中，不在此仓库的 46 测试覆盖范围内。`INSERT OR REPLACE` → `ON CONFLICT DO UPDATE` + `COALESCE` 是标准修复模式，可开箱通过。
- [x] **C5** — TOC 管线全新增模块，零侵入。
- [ ] **TOC 隐患复现检查 — 发现 2 项残留：**
  - **非原子写入残留**：`to_zai_prisma.py:296` `export_markdown()` 的 `op.write_text()` 和 `cli.py:63` `cmd_export()` 的 `open().write()` 都不是原子写入——进程崩溃产生半写文件。建议 C2 落地后立即升级这两处。
  - **`cmd_smoke` 推送失败仍返 0**：`cli.py:121` catch `RuntimeError` 后打印 warning 但未改 `return` 值 —— 用户看到 exit 0 以为是全绿，实际 `kHUB` 推送已静默跳过。建议此时 `return 1`。
