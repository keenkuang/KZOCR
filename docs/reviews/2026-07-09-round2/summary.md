# KZOCR 多角色评审汇总（第 2 轮）

- **评审日期**: 2026-07-09
- **评审对象**: `de270c91` 及其承载的 KZOCR 当前实现（对照 `docs/plans/toc-driven-pipeline-design.md` 设计）
- **评审角色（8 个独立 agent）**: 产品经理 / 顶级架构师 / 顶级软件工程师 / 顶级测试工程师 / 数据安全工程师 / 运维工程师 / 网络安全工程师 / 使用客户
- **总体结论**: **不通过 → 有条件通过之间**。8 个角色中 4 个（数据安全、运维、网络安全、使用客户）判**不通过**，4 个判**有条件通过**。共识是：mock 全链路可用可测，但存在 1 项跨 4 角色确认的**严重安全硬伤（硬编码密钥）**、若干**数据完整性/崩溃/出境** High 问题，以及设计与实现的**结构性偏离（TOC 分节管线未落地）**。

---

## 一、各角色结论

| 角色 | 问题数 | 结论 |
|------|--------|------|
| 产品经理 | 11（2H/3M/6L） | 有条件通过 |
| 顶级架构师 | 15（2H/7M/4L/1Info/1交叉） | 有条件通过 |
| 顶级软件工程师 | 16（1H/5M/7L/3Info） | 有条件通过 |
| 顶级测试工程师 | 12（2H/6M/2L/2Info） | 有条件通过 |
| 数据安全工程师 | 7（3H/2M/2L） | **不通过** |
| 运维工程师 | 15（3H/7M/3L/2Info） | **不通过** |
| 网络安全工程师 | 7（1H/3M/2L/1Info） | **不通过** |
| 使用客户 | 9（3H/3M/2L/1Info） | **不通过** |

---

## 二、跨角色共识的 HIGH 问题（必须修复）

| # | 问题 | 独立发现者 | 位置 |
|---|------|------------|------|
| **H1** | 源码硬编码明文云 API 密钥，且环境变量缺失时静默回退启用 | 数据安全 / 网络安全 / 运维 / 软件工程 | `modelscope_pool.py:101,141` |
| **H2** | 每次推送对 Pattern/Term/Formula 整表 `DELETE`，跨书范式/术语/方剂被静默清空 | 数据安全 / 运维（软件工程、架构师亦列 Medium） | `adapter/to_zai_prisma.py:90-92` |
| **H3** | 敏感古籍/患者页面图像可无开关出境至多家第三方云（含故障转移链） | 数据安全 / 网络安全 | `modelscope_pool.py:190-352` + `run.py:155-196` |
| **H4** | 真实 kimi 引擎路径丢弃全部结构化数据 → zai 校对台显示"空书"（pageCount=0） | 产品经理 / 架构师 / 使用客户 | `engine/run.py:114-119` |
| **H5** | 设计承诺的 TOC 驱动分节管线（5 阶段/6 模块）在代码中完全未落地 | 产品经理 / 架构师 / 使用客户 | `toc-driven-pipeline-design.md` vs `kzocr/` 实际文件 |
| **H6** | kHUB 推送异常类型错误（`except RuntimeError` 抓不到 `URLError`）→ smoke 在未起 kHUB 时崩溃、push 甩 traceback | 测试 / 运维（软件工程、架构师、产品经理亦列 Medium） | `cli.py:94` + `khub/client.py:43` |
| **H7** | CLI 与 kHUB 推送**零单元测试**，且上述异常缺陷无覆盖 | 测试（2 条 High） | `tests/` 仅 `test_pipeline.py`/`test_vlm.py` |
| **H8** | 引擎/VLM 一失败就静默回退写死假数据，且不报错（"publish 假古籍"风险） | 使用客户（运维、软件工程亦列 Medium） | `engine/run.py:39-51` + `engine/mock.py` |

---

## 三、关键 MEDIUM 问题（按主题）

- **崩溃/可用性**: `export --out` 传裸文件名时 `os.makedirs("")` 崩溃（`cli.py:49`）；pipeline/export 默认库不一致导致 export 报"未找到书籍"（`cli.py:30-34` vs `:44-47`）；CLI 无 `--log-level`、错误直接甩栈（`cli.py:24`）。
- **数据/一致性**: DB 导出缺"术语/方剂"模块（`export_zai.py:48-63`）；VLM `engine_label` 硬编码 `"PaddleOCR-VL-1.6"` 即使实际走 SenseNova（`run.py:27,442`）；`book_code` 净化用 `\w` 含 Unicode 与注释不符（`run.py:393`）。
- **配置/部署**: `pyproject.toml` 零依赖声明，但 mock 路径顶层强 `import fitz/numpy`（`run.py:17-18`）→ 零依赖跑通不成立；配置模块级单例与 `load_config()` 双源并存（`config.py:89`）；硬编码 `/home/keen/...` 绝对路径默认值（`config.py:45-46`、`run.py:73-74`）。
- **安全/运维**: kHUB 推送默认明文 HTTP + 鉴权可选（`config.py:23`、`client.py:39-41`）；`base_url` 协议未白名单（SSRF/本地文件读取，`client.py:29,43`）；`export` 用 `book_code` 拼路径（路径穿越，`cli.py:48`）；SQLite 无 `busy_timeout` 并发写易 `database is locked`（`adapter:80`、`export_zai.py:16`）；库文件默认宽松权限/无加密（`adapter:79-80`）；推送响应体入 INFO 日志（`cli.py:66,99`）。
- **可观测性**: 适配器/导出写库零日志、VLM/真实引擎缺整体超时与页数上限（`ops` 多条 Medium）。
- **死代码/重复**: `_crop_to_body`、`_PAGE_END_INCOMPLETE` 未使用；`_merge_cross_page_breaks` 重复计算 `cur_lines`；`list(doc)` 全量物化大书（`run.py` 多条 Low/Info）。

---

## 四、本轮修订范围建议

**本轮必须修订（可安全落地、可本地验证）**：
1. H1 — 移除 `modelscope_pool.py` 明文密钥，仅从环境变量读取，缺省禁用 provider。
2. H2 — 为 Pattern/Term/Formula/FormulaIngredient 增加 `bookCode` 列，DELETE/INSERT 按 `bookCode` 隔离，消除跨书互损。
3. H3 — 增加"数据出境许可"开关（默认关闭云端 vision），开启时记录审计日志。
4. H4 — `_run_real` 真实路径下若 `pages` 为空，从 `final_markdown` 重建 pages，保证校对台非空。
5. H6 — `khub/client.py` 将 `URLError/HTTPError` 统一包装为 `KHUBError(RuntimeError)`；`cmd_smoke` 优雅跳过、`cmd_push` 友好报错。
6. H7 — 新增 `tests/test_cli.py`、`tests/test_khub.py`、跨页合并/适配器选择/导出一致性测试。
7. H8 — 降级路径升级为 ERROR 级醒目提示并标注 `is_mock`，不再"假装成功"。
8. Medium 崩溃类（`export --out` 路径穿越+裸文件名、默认库不一致、CLI 顶层友好报错）、DB 导出补齐术语/方剂、`engine_label` 修正、`book_code` 净化、`pyproject` 依赖声明、SQLite `busy_timeout`、库文件权限、kHUB 协议白名单/鉴权告警、日志脱敏。

**后续路线图（不在本轮，需单独立项）**：
- **H5 — TOC 驱动分节管线完整实现**（目录分析→分节并行 OCR→DeepSeek 后处理→小节拆分→全书整合）。属大规模新功能开发，依赖真实 kimi 引擎与 DeepSeek 接入，无法在本机无重依赖下验证。本轮仅补齐 DeepSeek 配置项（`config.py` §2.3 字段）与在文档/CHANGELOG 明确标注"设计规划 vs 当前实现"，避免误导用户。
- 真实 kimi 引擎与 VLM 后处理的深度结构化抽取、多书累积范式库合并语义、结构化日志/健康检查。
