# 多角色评审 — 交付式校对台设计（round 1, 2026-07-22）

> 评审对象：`docs/plans/delivered-proofread-station.md`
> 评审角色：architect / security / ops / performance / testing / domain / pm / sweng
> 结论：设计方向正确（数据层闭环已具备），但**回导安全、双写权威性、web 数据源抽象**三处必须在立项前补强；阶段 0（回导入口）应独立于前端形态先行。

---

## architect（架构）

- **双写权威性未定义（高优先级）**：站内校对走 `BookDB.resolve_anomaly`，交付式走 `custom.db.Line.humanFinal` → `import_proofread_package` 回写 BookDB。两条修正写入路径并存，未定义冲突时的**优先级/合并规则**。若同一行既被站内改又被交付包改，谁赢？需在设计中明确"回导为权威覆盖"或"按时间戳合并"，否则数据会静默互覆盖。
- **web 数据源硬绑定被低估（高优先级）**：`kzocr/web/app.py` 约 35 条路由均直接 `BookDB(book_code, db_dir=_db_dir())`。方案 A 的"custom.db 校对模式"若只加 `if mode==` 分支会迅速腐化。应先抽 `ReviewDataSource` 协议 + `BookDBAdapter` / `CustomDBAdapter` 两个实现，再让路由依赖协议，而非散点判断。
- **校对上下文缺失**：custom.db 的 `Line` 仅有文本字段，无原图/分歧原因/severity。校对员改 `humanFinal` 时**看不到为何该行被标记**，校对质量无保障。应携带 `disputed`/`auditSource`/severity 及可选原图裁剪（复用 `char_boxes`）。

## security（安全）

- **导入不可信 custom.db 是污染面（高优先级）**：校对回来的包来自外部人员，`import_proofread_package` 对文件直接 `sqlite3.connect`。应：① 用**只读连接**打开校验；② 校验 schema 与行数上限，拒绝畸形/超大库；③ `humanFinal` 视为不可信文本，渲染时依赖 Jinja2 默认 autoescape（确认开启），防 XSS。
- **路径穿越**：`--db`/上传路由需限制 custom.db 落在允许的 `KZOCR_DB_DIR` 内，禁止任意路径。
- **交付包依赖最小化**：桌面打包**必须排除** torch/PaddleOCR/GLM 等引擎依赖（既缩体积也缩攻击面），仅打包"纯校对"最小依赖（FastAPI + PyMuPDF + numpy）。
- `.frozen` 标记是软约定非访问控制，可接受，但文档应说明其仅为防误覆盖、非安全边界。

## ops（运维）

- **register_postgres 默认须强制翻转（中）**：`import_proofread_package(register_postgres=True)` 默认会连 PG；交付环境通常无 PG。应在"交付模式"下**强制 `register_postgres=False`**（best-effort 静默跳过已具备），而非仅"建议"。
- **跨平台打包细节**：PyMuPDF/numpy 有 win/mac wheel，可打包；需明确 `hiddenimports` 与 `excludes`（排除引擎）；单文件 exe vs 单目录需定（单目录利于排错，单文件利于分发）。
- **离线**：交付包应完全无网络依赖（当前逻辑满足），但需在打包时验证无隐式联网（如 egress 校验在纯校对路径不触发）。

## performance（性能）

- §4.1 运行时效率对比成立。补充：大书（1000+ 页）SSR 整页渲染 divergences 可能偏重；SPA 可懒加载。但交付式校对通常审子集，影响有限，记为低优先级。
- `import_proofread_package` 全量读 `Line`/`Proofread` 表，SQLite 处理无压力，OK。

## testing（测试）

- **跨平台 CI**：当前 CI 仅 Linux 3.10/3.11/3.12。交付包需新增 **Windows / macOS** smoke runner（至少启动 `kzocr web --db <custom.db>` + 一次导入）。
- **回导入口测试（阶段 0）**：CLI `kzocr import` 与 web 上传路由需测试（扩展 `tests/test_doc_import.py`，mock 文件系统）。
- **web custom.db 模式测试**：需 fixture custom.db + 路由级测试，验证校对模式只读 humanFinal、不污染 BookDB。

## domain（中医古籍校对领域）

- **原图回溯应提升为必需**：校对员（尤其形近字/剂量数字分歧）必须看到原图裁剪与引擎分歧原因，否则无法有效终校。建议在 custom.db 增加可选 `crop_img`/`reason` 字段（复用 `engine/run.py` 版心裁剪 + char_boxes），而非留作"开放问题"。
- **字符级校正**：custom.db 已有 `charLevelJson`，应支持字符级而非仅行级 `humanFinal` 编辑，贴合中医字校对实际。

## pm（项目管理）

- **阶段 0 应独立于前端形态先行（高优先级）**：回导入口（CLI + web）价值最高、风险最低，且与 A/B/C 决策无关，应作为单独最小立项立即做。
- §9 决策点清晰；建议**先冻结前端形态（A/B/C）再启动阶段 1**，避免阶段 0/1 返工。
- 范围建议：本期聚焦"交付式最小可用闭环"（打包→离线审→回导），桌面华丽 UI 可后置。

## sweng（软件工程）

- **先抽象后分支**：方案 A 落地前必须引入 `ReviewDataSource` 协议与双适配器，禁止路由内 `if mode` 散点；否则维护成本陡增。
- **走新路径**：新代码直接用 `kzocr/doc/*`（`push_book_to_zai`/`import_proofread_package`/`freeze_custom_db`），不要经 `kzocr/adapter/to_zai_prisma.py` 兼容壳。

---

## 评审汇总（按优先级）

| 优先级 | 角色 | 发现 | 处置建议 |
|--------|------|------|----------|
| 高 | architect | 双写权威性/冲突优先级未定义 | 设计明确回导覆盖规则 |
| 高 | architect | web 数据源硬绑定被低估 | 先抽 `ReviewDataSource` 协议 + 双适配器 |
| 高 | security | 导入不可信 custom.db 污染面 | 只读连接 + schema 校验 + autoescape + 路径限制 |
| 高 | pm | 阶段 0 未独立于前端形态 | 阶段 0 单独立项先行 |
| 中 | security/ops | register_postgres 默认应强制 False | 交付模式强制关闭 |
| 中 | domain | 原图回溯/分歧原因应为必需 | custom.db 增加 crop_img/reason |
| 中 | testing | 缺跨平台 + 回导入口测试 | 补 win/mac CI + 入口测试 |
| 低 | performance | 大书 SSR 整页重 | 懒加载（后续） |
| 低 | domain | 字符级校正 | 用 charLevelJson |
