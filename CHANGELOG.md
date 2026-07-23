# KZOCR 变更日志

> 文档版本：v2026-07-22 版本一致性
> 最后更新：2026-07-23 CST

---
## v2026-07-23 OvisOCR2 引擎接入与 e2e 跨引擎扩面（取代 RapidOCR）+ 单页容错

> 提交：`9d460a6`/`49dbe4b`/`995c35f`/`4a70c96`/`313a0ac`。
> OvisOCR2（GGUF 经 llama-server 作 e2e 跨引擎对照）取代 RapidOCR；修模型默认路径与扫描根；单页引擎超时容错，避免整批永久跳过。

| 模块 | 说明 |
|------|------|
| `kzocr/engine/adapters.py` | 新增 `OvisOCR2Adapter`（auto_spawn 拉起 llama-server + mmproj，`timeout=900`）；`EngineRegistry` 注册 `ovisocr2`。 |
| `scripts/bench_ovisocr2.py` | 新增量化 benchmark 脚本（结论：Q4_K_M≈Q8_0 质量、比 PaddleOCR 慢 10–15×、与 PaddleOCR 分歧 ~18%/字属架构噪声）。 |
| `scripts/e2e_expand_books.py` 等 | 跨引擎对照引擎 `RapidOCR`→`OvisOCR2-Q4_K_M`，registry `rapidocr`→`ovisocr2`；模型默认路径 `Q4_KM`→`Q4_K_M`；扫描根加入 `/media/keen/ZFS400`。 |
| `scripts/e2e_expand_books.py` `count_book` | 单页 `try/except` 容错（OvisOCR2 在 CPU 上偶发超 adapter timeout 不再令子进程崩溃），双引擎缺失页 `skip`；附 2 回归测试。 |

---
## v2026-07-23 校对台支持多书包切换（请求级 pkg + 目录扫描）

> 提交：`02802ca`/`3d757ce`。

| 模块 | 说明 |
|------|------|
| `kzocr/proofread/app.py` | 注册表 + cookie `kzocr_pkg` 路由当前包；启动扫描 + 运行时打开多本 `custom.db`；`current_db` 对失效 cookie 回退 `_DEFAULT_PKG`，`/packages/open` 异常转 303 而非 500（请求级隔离，并发安全）。 |
| `tests/test_proofread_multi_package.py` | 新增 11 例（列表/切换/运行时打开/失效 cookie 回退/损坏库 book_count=0/并发请求级隔离/碰撞 hash 后缀）。 |

---
## v2026-07-23 校对台前端对齐 zai 设计系统 + lucide 图标

> 提交：`84ea92b`/`6ce8182`（离线打包 `8c57cd1` 已另记）。框架仍为 FastAPI+Jinja2+Tailwind，未移植 React/shadcn；交互仍为逐行编辑。

| 模块 | 说明 |
|------|------|
| `base.html` | `:root` 设计令牌 OKLCH 与 zai `globals.css` 逐字节一致（primary/sidebar/bg/card/fg/muted/border 等）；状态色统一令牌（done=墨绿、pending=琥珀、del=朱砂、ins=墨绿、replace=琥珀）。 |
| 装饰 | 宣纸纹理 `tcm-texture`、章节标题装饰线 `section-title-deco`、印章 `seal-stamp`、fadeInUp 渐入；可选暗色主题（`.dark` + localStorage）。 |
| lucide | 引入 lucide UMD（`<i data-lucide>` + `lucide.createIcons()` 注入 SVG，图标色继承 `currentColor`）；CDN 离线静默降级。 |

---
## v2026-07-23 补齐校对台审计视图与保存审计行

> 提交：`dbf4860`。

| 模块 | 说明 |
|------|------|
| `kzocr/proofread/api.py` | `save_human_final` 重写为先查行、原文=新值 no-op、否则 `UPDATE Line` + `INSERT Proofread(changeType="human_edit")`；新增 `get_line_proofreads` + `get_import_audit`（`isfile` 守卫，`try/except` 不建空库）。 |
| `kzocr/proofread/app.py` | `GET /book/{code}/line/{id}/audit` + `GET /book/{code}/import-audit`。 |
| `review.html` | 每行人工终校框前加审计面板（字符级审计：时间·changeType·原文→改后）+ 回导历史按钮；`escapeHtml` 防 XSS。 |

---
## 两点修订 stage 2/3：字级 canonical 数据模型 + 落库 + stage 3 反哺

> 提交：`fec02a1`/`f2bc639`/`b6bcc75`/`688eb9e`/`455cf06`。全量 **1137 passed + 2 skipped + 2 deselected**。
> 坐标系铁律：bbox 一律版心图（dpi=150、不缩放、原点版心图左上），与 `line.char_boxes` 一致，绝不用 VL 缩放坐标。

| 模块 | 说明 |
|------|------|
| `kzocr/scheduler/canonical.py` | 字级规范实体 `CanonicalChar` / `EngineCharRecord` / `ErrorRecord` + 纯函数 `build_canonical_chars`（difflib 对齐到共识）/ `map_divergence_to_canonical` / `derive_error_records` / `build_page_canonical_and_errors`（含 `crop_char_to_png`）。 |
| `kzocr/storage/db.py` | 新表 `canonical_char`/`engine_char_record`/`error_record`；`save_canonical_chars`(UPSERT)/`get_error_stats`(每引擎错误率+混淆 Top-N)/`export_error_pairs`(JSONL 训练样本)/`add_learned_confusion_batch`。 |
| `kzocr/scheduler/orchestrator.py` | `_persist_canonical_and_errors` 在同步 + 延迟两条路径落库（best-effort，异常仅告警）。 |
| `scripts/feedback_canonical_errors.py` | stage 3 反哺驱动：`error_record` → `learned_confusion`，`BookDB.get_confusion_candidates` 产出候选。 |

---
## 两点修订 stage 1：所有 high 分歧进人工队列 + 字符级黄/红标注

> 提交：`a47264c`/`1190c5f`。全量 **1119 passed + 2 skipped + 2 deselected**。

| 模块 | 说明 |
|------|------|
| `kzocr/scheduler/orchestrator.py` | `_arbitrate_high_divergences` 移除「VL 明确裁决即自动接受」分支，`_apply_vl_fix` 删除；所有 high 分歧一律进 unresolved（一字不差）。 |
| `kzocr/scheduler/cross_align.py` | `compute_vl_marks(page_lines, divs)` 把 `cross_divergence.status` 映射为字符区间（accepted→vl 黄；pending/both_wrong→human 红）。 |
| `kzocr/doc/zai.py` + `review.html` | `Line.vl_marks` 烘焙进 custom.db；`.d-vl`(黄)/`.d-human`(红) + `applyVlMarks()`。 |

---
## 跨引擎校验默认开启 + 文档/覆盖率修正

> 提交：`59c5a5a`/`f6ecc0a`/`37e6728`。

| 模块 | 说明 |
|------|------|
| `kzocr/scheduler/scheduler.py` | `EngineOverrides.enable_cross_check` 默认翻 `True`（与 `run.py:117` 运行时覆盖一致，消除被覆盖的死默认值）。 |
| `docs/plans/enable_cross_check_default.md` | 修正过期描述（跨引擎校验自 v0.7 已默认开，复查易误判）。 |
| `CODEBUDDY.md` | 修正覆盖率门禁 `fail_under=80` 与 `main` 直推描述。 |

---
## v2026-07-22 校对台桌面打包打磨

> 提交：`f6a6777`。

| 模块 | 说明 |
|------|------|
| `scripts/build_proofread_app.sh` + `proofread_entry.py` | 启动 splash + 窗口标题 + 无图占位（`review.html` 灰块虚线「无原图」）；`proofread_entry.py` 改 uvicorn 后台线程 + 轮询就绪后开浏览器并销毁 splash，无 GUI 降级控制台提示。 |

---
## v0.26.0 版本号发布

> 提交：`7b97650`（与 B7 `22b5e9e` 关联，B7 已另记）。

| 模块 | 说明 |
|------|------|
| `pyproject.toml` / `kzocr/__init__.py` / `tcm_ocr/__init__.py` / README 徽章 | 版本号 `0.25.0` → `0.26.0`。 |

---
## 方向4：版面检测串行化 + 页级并发容错

> 提交：`b81db9b`/`d505a3d`。

| 模块 | 说明 |
|------|------|
| `kzocr/scheduler/orchestrator.py` | 版面检测模型 PP-DocLayoutV3 推理加锁串行化（避免并发资源争用）；页级并发编排增加单页容错（工作流 A 前置）。 |

---
## e2e 分歧形近字候选挖掘脚本

> 提交：`57d4a8c`（方向1 1-A）。

| 模块 | 说明 |
|------|------|
| `scripts/` | 新增 e2e 分歧形近字候选挖掘脚本，从跨引擎分歧中挖掘混淆对候选，衔接 `confusion_set` 学习。 |
---

## v2026-07-23 校对台 Tailwind 预编译升级（移除浏览器端 JIT 运行时）

> fix：proofread 前端原用 vendored 的 Tailwind Play CDN 运行时（tailwind.js，浏览器端 JIT 编译约 4MB），大页面有 FOUC 闪烁与编译卡顿。改为构建时预编译并固化 `tailwind.min.css` 进仓库；`tailwind.config.js` 由 base.html 内联配置迁移而来，`scripts/build_tailwind.sh` 经 npx 生成。CI/桌面包无需 node 工具链，离线更稳、产物更小（约 12KB）。

| 模块 | 说明 |
|------|------|
| kzocr/proofread/static/vendor/tailwind.min.css | 新增：预编译产物（提交进 git），替代 tailwind.js。 |
| kzocr/proofread/static/vendor/tailwind.js | 删除：不再需要浏览器端运行时。 |
| kzocr/proofread/templates/base.html | `<script src=tailwind.js>` + 内联 `tailwind.config` 改为 `<link rel=stylesheet href=tailwind.min.css>`。 |
| tailwind.config.js | 新增：content 扫描 templates，tcm.* oklch 配色，safelist 保底 JS 动态类。 |
| kzocr/proofread/static/src/input.css | 新增：`@tailwind` 三指令入口。 |
| scripts/build_tailwind.sh | 新增：npx tailwindcss@3 生成 CSS。 |
| tests/test_proofread.py | test_static_vendor_assets_served 改断言新文件与 safelist 类。 |

---

## v2026-07-23 校对台离线打包（Tailwind/lucide 本地化，桌面分发离线可用）

> fix：校对台 UI 原依赖 cdn.tailwindcss.com 与 unpkg.com/lucide 两个外部 CDN，桌面分发在离线环境缺样式/图标。改为本地 vendored 资源，由 FastAPI StaticFiles 挂载 /static 提供；不引入 node 工具链；版本锁定（Tailwind 3.4.17 / lucide 1.25.0）。

| 模块 | 说明 |
|------|------|
| kzocr/proofread/static/vendor/tailwind.js | 新增：vendored Tailwind Play CDN runtime（锁定 3.4.17），提交进 git。 |
| kzocr/proofread/static/vendor/lucide.min.js | 新增：vendored lucide UMD（锁定 1.25.0），提交进 git。 |
| kzocr/proofread/app.py | app_factory 用 StaticFiles 挂载 /static 指向 _PROOFREAD_DIR/static（目录存在时）。 |
| kzocr/proofread/templates/base.html | 两处外链改为 /static/vendor/tailwind.js 与 /static/vendor/lucide.min.js；保留 tailwind.config 自定义 tcm.* 色。 |
| scripts/build_proofread_app.sh | OPTS 新增 --add-data "kzocr/proofread/static:kzocr/proofread/static"。 |
| tests/test_proofread.py | 新增 2 例回归：test_static_vendor_assets_served（本地 serve 两资源） + test_index_no_cdn_references（渲染 HTML 不含外链）。 |

---

## v0.26.0 B7 行级裁剪图路径存储（persist 时按 char_boxes 切片落盘）

> 提交：`22b5e9e`（feat(storage): B7 行级裁剪图路径存储）。全量 **1117 passed + 2 skipped + 2 deselected**，ruff 全过，CI 在 3.10/3.11/3.12 全 ✅。
> 设计计划 `electric-nebula-curie-R5VASMaA.md`。

| 模块 | 说明 |
|------|------|
| `kzocr/storage/crop_images.py` | 新增：复用 zai.py 烘焙管线（`_pdf_page_to_numpy(dpi=150)` + `_crop_to_body`，**不缩放**），保证与 ingest 的 char_boxes 坐标严格对齐；按页缓存 doc/版心图，best-effort 失败仅告警。 |
| `kzocr/storage/db.py` | `line.crop_img_path` + `book.source_pdf` 两列（旧库自动 ALTER 补列）；`save_book_result` 落库时按整页版心图包围盒切 PNG 落 `<db_dir>/<book_code>_crops/P{page}_L{para}_{seq}.png`，受 `KZOCR_CROP_IMG` 开关控制（默认开）。 |
| `kzocr/engine/types.py` | `BookResult.source_pdf` 字段。 |
| `kzocr/scheduler/orchestrator.py` / `kzocr/engine/run.py` | 三处 `BookResult` 构造点填充 `source_pdf`（串行/并行路径经 `_finalize_book` 透传）。 |
| `tests/test_bookdb_crop_image.py` | 新增 4 例：切图落盘相对路径 / 开关关闭 / 无 source_pdf / 旧库迁移补列。 |

> 坐标不变量：char_boxes 是 `_crop_to_body` 后版心图（dpi=150，**不缩放**）像素坐标；绝不用 orchestrator 的 VL 缩放坐标，否则错位。模块 A 烘焙 custom.db 的 `crop_img` BLOB 不变（离线自包含）。

---

## v2026-07-22 交付式校对台增强（A–H）+ e2e 分歧明细落库

> 闭环计划 `electric-nebula-curie-R5VASMaA.md`（方向 2 三功能 + 方向 3 回导加固 B.1–B.5 + 识别率衔接 H）。
> **A–H 全部实现并推 main**（版本维持 **0.25.0**）：全量 **1104 passed + 2 skipped + 2 deselected**（较 1092 +12），ruff 全过，CI 在 3.10/3.11/3.12 全 ✅。
> 提交：`7d271a5`/`c3a51c3`/`b39f5e8`（e2e 落库）、`add27a6`/`269cb2b`/`d0c2185`（A/B后端/C/D/E/F/G）、`4919185`（B 前端字符框叠加）、`bfc7e1d`（H）、`6f00016`（README 徽章同步）。

### 一、e2e 扩面结果落主库 BookDB（前序，衔接识别率提升的数据归宿）

| 模块 | 说明 |
|------|------|
| `kzocr/storage/db.py` | 新增 `e2e_expansion` 表（按书分库；字段 book_code/pdf/book_title/pages_processed/pages_requested/total_divergences/high_divergences/engine_a/engine_b/render_warnings_json/run_at/batch）+ `save_e2e_expansion(...)`（INSERT 历史，返回 id）+ `get_e2e_expansions(book_code)`。 |
| `scripts/e2e_expand_books.py` | 抽 `_safe_book_code(name)`（复用 `run.py` 的 `re.sub(r"[^A-Za-z0-9_\\-]", "_", ...)`，与 VLM 链路对齐，保证同书落同一按书分库文件）；新增 `_persist_e2e(rec, db_dir)`（写该书 BookDB）；新增 `--persist-db` 开关（`KZOCR_E2E_PERSIST_DB` env 兜底）；每本书 checkpoint 后落库（落库失败仅告警不阻断汇总）。 |
| `scripts/run_e2e_nightly.py` | `run_batch` 传 `--persist-db`，使 nightly 默认落库。 |
| 修复 | `parse_target_line` 用 `rsplit(None, 1)` 只切「路径/页数」一处，保留文件名内任意连续空格/tab（修胡天宝书 2 空格致 `isfile` 误判 + 失败书无限重试 bug）；新增 `_FAILED_KEYS` 持久化跳过机制。 |

### 二、校对台增强 A–H（离线 custom.db 交付 + 回导加固 + 分歧明细落库）

| 模块 | 说明 |
|------|------|
| **A 原图回溯** | `kzocr/doc/zai.py` Line 加 `crop_img BLOB` + `_migrate_line()`（缺列 ALTER 兼容旧包）；`push_book_to_zai` 每书开一次 `fitz`，按该行 char_boxes 并集 bbox 裁 150 DPI 图烘焙进 custom.db（`KZOCR_CROP_IMG=0` 可关）。`api.py` `LineItem.crop_img_b64` + `review.html` 行首 `<img>` 离线自包含展示。 |
| **B 字符级校正** | `zai.py` Line 加 `charBoxes TEXT`，`push_book_to_zai` 写入（**转相对裁图坐标**，与 crop_img 原点对齐修复整体偏移 bug）；`api.py` `LineItem.char_boxes` + 纯函数 `scale_char_box`（像素→显示，利于测试）；`review.html` 字符框绝对定位叠加 + 点击高亮/复制坐标/跳转原图/插入字符。 |
| **C 差异高亮** | `api.py` 新增 `compute_diff(a,b)`（LCS 字符级 diff，`equal/insert/delete/replace` 四态）+ `DiffToken`；`review.html` 每行折叠「差异视图」面板，纯前端 JS 对「各引擎→共识」「共识→人工终校」做绿/红/橙高亮。数据（engine_texts/consensus/humanFinal）已存在 Line，无数据层改动。 |
| **D B.1 来源校验** | `zai.py` 新增 `ExportMeta` 表（tool_version/source_hash/signature）；`push_book_to_zai` 写 `sha256` 过 Line 不可变字段（排除 `humanFinal`）。`proofread.validate_proofread_package` 校验缺失/不一致即拒（旧包 `KZOCR_ALLOW_LEGACY=1` 放行）。 |
| **E B.2 审计** | `db.py` 新增 `import_audit` 表 + `record_import_audit`；`import_proofread_package` 成功回导后落一行（imported_by/package_hash/lines/proofreads/version）。 |
| **F B.3 事务幂等** | `db.py` `save_line_human_finals`/`save_proofreads` 加 `commit: bool=True`；`import_proofread_package` 包进单事务，异常 `rollback` 避免中途部分写入。 |
| **G B.5 多回导冲突** | `proofread` 表加 `imported_at`/`import_version`；每次回导版本号递增并保留历史行（`UNIQUE(line_id, corrected_text)`）；`import_audit` 记 version 供回溯。 |
| **H 识别率衔接（本次收尾）** | `e2e_expand_books.py` `count_book` 给每页 `per_page` 附加 `divergences`（`dataclasses.asdict`，引擎来源 PaddleOCR/RapidOCR 注明）；`_persist_e2e` 落库时把逐条分歧明细写进按书分库 BookDB 的 `cross_divergence` 表（按页号幂等覆盖），衔接 `confusion_set` 学习 / GlyphVerifier 调优 / 校对台差异高亮。`db.py` 新增 `clear_cross_divergences(page_nos)`。新增 4 例回归测试（count_book 附 divergences / 落库 cross_divergence / 重跑幂等 / 增量合并不误删旧页 / clear_cross_divergences）。 |

> 说明：H 路径 `run_cross_align` 未传 `boxes_a`，故 `Divergence.boxes` 恒空；需字符级定位的视觉仲裁走 orchestrator 全路径（已 `align_boxes_to_text` 注入 char_boxes）。
> 范围边界：不删任何代码；不引入外部依赖；`KZOCR_CROP_IMG=0`/`KZOCR_ALLOW_LEGACY=1` 等降级开关保留。

---

## v2026-07-22 版本号一致性校验与修复（维护性，版本维持 0.25.0）

> 按发版版本 bump 检查清单（记忆 `README_badge_count.md` A–F 分区）全量校验各文件版本戳，
> 发现并订正两处陈旧版本残留；**无功能变更，版本号维持 0.25.0**；ruff 全过。

| 模块 | 说明 |
|------|------|
| `kzocr/web/app.py` | **修复 ①**：`/health` 接口 `{"version": "0.19.0"}` 硬编码陈旧值 → 改引用 `__version__`（与 FastAPI `app.version` 一致）。此前同一文件 FastAPI 实例已用 `__version__`（L29），唯独健康检查端点遗漏，现实测返回 `0.25.0`。 |
| `kzocr/tcm_ocr/__init__.py` | **修复 ②**：`__version__` 长期停在 `"0.19.0"` → 订正为 `"0.25.0"`。依据提交 `464f68e`（版本号统一补提交）明确将 `kzocr/__init__.py`/`web/app.py`/`tcm_ocr/__init__.py` 三者一并统一至 `0.19.0`，证明 tcm_ocr 的 `__version__` 本就属主包版本线，后续 0.19.0→0.25.0 的历次 bump 只是遗漏。该值为仓库内无引用的死值，改动零功能风险。 |
| `memory/README_badge_count.md` | 将版本一致性记忆重构为 A–F 发版 bump 检查清单；**校正旧误解**：原记忆称 tcm_ocr `__version__` 为"独立遗留版本、勿改"，已据 git 历史更正为"属主包版本线、须随主线 bump"；独立保留的 `tcm_ocr/web/app.py` 的 `API_VERSION="1.1.0"`（API 契约版本）。 |

> **校验结论**：权威源 `pyproject.toml` / `kzocr/__init__.py`、手动线 `tcm_ocr/__init__.py`、文档戳
> `README.md` 双徽章 / `CODEBUDDY.md` 两处 / `scheduler/__init__.py` docstring、派生点 `web/app.py`+`cli.py`
> 全部 = `0.25.0`；活跃代码无残留 `0.19.0`–`0.24.0`；运行时实测 `kzocr` 与 `kzocr.tcm_ocr` 均为
> `0.25.0`；唯一有意分离的是 `tcm_ocr` 的 `API_VERSION=1.1.0`（正确保留）。

---

## v2026-07-21 续十三 — #3 质量/性能再提升（工作流 A：页级并发 + 渲染隔离）

> **#3 用户决策三个子方向全做**（页级并发编排① / 分歧率·质量优化② / 编排延迟优化③），不三选一。
> 本段交付**工作流 A（①+③合并）**：将主循环页处理主体提取为纯函数 `_process_one_page` + 数据类
> `_PageOutcome`/`_PageContext`，开启 `KZOCR_PAGE_PARALLEL` 时 `ThreadPoolExecutor` 跨页并行（每 worker
> 独立 `fitz` 渲染隔离），**合并阶段单线程串行**写 BookDB / 引擎统计 / `tally` / 延迟 VLM 仲裁
> （`_vl_lock` 串行视觉调用）。延迟优化（③）随并发一并交付：墙钟从 Σ(page_i) 降至 ~Σ/N。
> 全部默认关闭，冻结栈行为不变。新增 8 例 mock 测试（`tests/test_orchestrator_parallel.py`）。
> 工作流 B（② 自适应共识抽样 + 旋钮 env 化）同段交付：保守模式 `KZOCR_CONSERVATIVE_MODE`
> 按实时分歧率动态收紧 conf 门限（0.90→0.85，边界置信度页减少进人工队列）+ 上调共识抽样率
> （0→0.20），对脏书自动加严、干净书不受影响；串行主循环与并行合并阶段统一采用自适应质量参数。
> 新增 9 例 mock 测试（`tests/test_orchestrator_quality.py`），全量 **1029 passed + 2 skipped
> + 2 deselected**（较 1020 +9），ruff 全过。版本号维持 **0.25.0**。

| 模块 | 说明 |
|------|------|
| `kzocr/scheduler/orchestrator.py` | **工作流 A**：① 提取 `_process_one_page(page_num, page_input, ctx) -> _PageOutcome`（线程本地计算，无共享状态副作用，VLM 调用经 `_vl_lock`）；`Tier3` 分歧块抽为 `_run_tier3_divergence(..., defer=)`，成功路径 `_run_success_cross_check(..., defer=)` 新增延迟模式（不写库/不仲裁/不更新全局 tally，仅返回 `_DeferredCrossCheck` 含引擎名与 tally delta）；新增 `_finalize_divergences_{success,tier3}`（合并阶段按页序最终化分歧 + 延迟 VLM 仲裁）、`_render_one_page`（每 worker 独立渲染单页）、`_run_book_parallel`（并发 map + 串行合并）、`_finalize_book`（串行/并行共用书后处理收口）、`_PageOutcome`/`_PageContext`/`_DeferredCrossCheck` 数据类、`_vl_lock`。② 主循环 `KZOCR_PAGE_PARALLEL` 分支（默认关）：切片到 `budget.max_pages` + 跳过 `skip_pages`，提交 worker，合并阶段按页序落地全部共享状态。 |
| `kzocr/config.py` | `SchedulerConfig` 新增 `page_parallel: bool`（默认关，读 `KZOCR_PAGE_PARALLEL`）、`page_workers: int`（默认 0=自动 min(CPU,4)，读 `KZOCR_PAGE_WORKERS`）。 |
| `tests/test_orchestrator_parallel.py`（新增，8 例） | 默认关=串行路径不变；并行多页成功 / Tier1 失败→Tier3 成功 / 全失败 HumanGate；**等价性**测试（并行 vs 串行 `pages_text`/`failed_pages`/`uncertain_pages` 一致）；并行 + VL 交叉校验 high 分歧被仲裁且不进人工队列；**渲染隔离**（每 worker 独立渲染、调用次数=页数）；大书 + 受限 worker 全部页处理完。全程 mock 引擎 + mock 渲染，无真实 PDF/网络。 |

> 共享状态安全清单：db（仅合并阶段单线程写）、registry/engine_usage_counter（仅合并 `record`）、`tally`（页内算 delta、合并累加后判保守模式）、`pages_text`/`pages_order`/`trace`（合并 extend）、`fitz` Document（每 worker 独立打开）、`vision_adapter`/`vl_budget`（worker/合并均经 `_vl_lock` 串行）。降级：开关默认 0，置 0 即完全回到串行冻结栈行为。
> 范围边界：不改 `archival.py` / 主线 `BookDB` schema / `web/app.py`；不删任何代码；不引入外部依赖；并发仅用标准库 `ThreadPoolExecutor`。

### 续十三·B — 工作流 B：分歧率 / 质量优化（② 自适应共识抽样 + 旋钮 env 化）

| 模块 | 说明 |
|------|------|
| `kzocr/scheduler/orchestrator.py` | **工作流 B**：② 新增模块级常量 `_CONSERVATIVE_MODE`（读 `KZOCR_CONSERVATIVE_MODE`，默认关）、`_CONSERVATIVE_DIV_RATIO_THRESHOLD=0.30`（实时分歧率阈值）、`_CONSERVATIVE_BOOST_SAMPLE_RATE=0.20`（上调抽样率）、`_CONSERVATIVE_TIGHTEN_CONF_GATE=0.85`（收紧门限）、`_MIN_PAGES_FOR_RATIO=10`（早期样本不足不翻跳）；新增 `_adaptive_quality_params(tally, processed_pages, base_sample_rate) -> (抽样率, conf门限)`，保守模式且已处理页数达阈值且实时分歧率超阈值时返回 `(max(base,0.20), min(base_gate,0.85))`，否则返回基线值（行为不变）。串行主循环（L1465-1526）与并行合并阶段（`_run_book_parallel`）均调用该函数，按累计 `tally` 逐页取自适应 `rate/gate`：低置信度 PASS 页（`conf≤gate`）挂起待人工复核（合并阶段统一判定，串行等价）；共识一致页按自适应 `rate` 抽样送视觉仲裁。**并行路径修复**：worker 不再 Bake conf 门控（改合并阶段决策，依赖跨页累计 tally）；合并阶段从 `_DeferredCrossCheck.tally_div/tally_high` 累计 `tally`（此前 `_PageOutcome.tally_div` 始终为 0，致 `tally` 恒空、保守模式与 `_is_conservative` 在并行路径失效）。 |
| `kzocr/config.py` | `SchedulerConfig.from_env` 已读 `KZOCR_CONSENSUS_SAMPLE_RATE`（L85，默认 0.0）；`consensus_sample_rate` 经 `run.py` 注入 `EngineOverrides`，env 旋钮已生效（无需改动）。 |
| `tests/test_orchestrator_quality.py`（新增，9 例） | `_adaptive_quality_params` 单元：默认关=基线；开启+高分歧(且页数足)=上调抽样率+收紧 gate 至 0.85；低分歧/早期页数不足=基线。串行：保守模式对高分歧书降低 conf_low 队列（gate 0.85→0.88 不再进队）、干净书不变。`KZOCR_CONSENSUS_SAMPLE_RATE` 经 `SchedulerConfig.from_env` 生效；共识抽样率 rate=0 不抽样 / rate=1.0 全抽样。并行：合并阶段同样尊重保守模式自适应门控（与串行等价，验证并行 tally 累计修复）。全程 mock 引擎 + mock 渲染，无真实 PDF/网络。 |

> **设计说明（conf 门控方向）**：`conf ≤ gate` → 页挂起人工复核。降低 gate（0.90→0.85）使「更少」页满足 `conf ≤ gate` → **人工队列缩小**；保守模式据此对脏书（高分歧）减少边界置信度页进队，同时上调共识抽样率（0.20）捕获「两引擎同错」盲区，净效应为更小、更高质量的人工队列（与计划「降低人工复核队列」一致）。早期样本不足（`_MIN_PAGES_FOR_RATIO=10`）与干净书（分歧率<0.30）不触发，避免翻跳。

### 续十三·C — e2e 优先级语义全量对齐（修复 ② 并消除同源 web bug）

> 背景：core 优先级已于某次升级改为 `P0`(剂量数字)/`P1`(形近字)/`normal`（历史 `high` 语义等同），`orchestrator.py` 已用 `in ("P0","P1","high")` 归入高优先队列。但部分消费点仍停留在旧 `== "high"`，导致 P0/P1 永远不被统计/显示。

| 模块 | 说明 |
|------|------|
| `scripts/e2e_expand_books.py` | **修复 ②**：`count_book` 中 `n_high = sum(1 for d in divs if d.priority == "high")` → `in ("P0","P1","high")`。此前 `per_page[].high` 恒为 0（记忆记录的 e2e 扩面遗留 bug）；修正后 P0/P1 正确计入。 |
| `kzocr/web/templates/divergences.html` | **新发现同源 bug**：复核台「高优先级」红色徽章 `{% if d.priority == 'high' %}` 对 P0/P1 永不显示 → 改为 `in ('P0','P1','high')`，并显示真实标签（`high`→"高"，`P0`/`P1`→原值）。过滤栏 `?priority=high` 链接保留。 |
| `kzocr/web/app.py` | `book_divergences` / `api_divergences` 路由把用户面 `priority="high"` 映射为分组元组 `HIGH_PRIORITY_GROUP = ("P0","P1","high")`，使「高优先级」筛选与精确 `P0/P1/normal` 筛选均正确（此前 `?priority=high` 对 P0/P1 返回空集）。 |
| `kzocr/storage/db.py` | `get_cross_divergences(priority=...)` 扩展为接受单值或序列：序列走 `priority IN (...)`（向后兼容单值/None，既有测试不受影响）。 |
| `kzocr/scheduler/cross_align.py` | `Divergence.priority` 字段注解与类 docstring 由误导性的 `'high' \| 'normal'` 订正为 `P0/P1/normal`（历史曾用 `high`，语义等同）；零运行时变更。 |
| `scripts/e2e_orchestrator.py` / `scripts/e2e_cross_engine_realbook.py` | 活跃扩面脚本的 `high` 计数同样改为 `in ("P0","P1","high")`（与 core 一致）。`scripts/archive/*` 死代码未动。 |
| `tests/test_web_routes.py`（新增 1 例） | `test_divergences_high_priority_group_filter`：写入 P0/P1/high/normal 四类分歧，断言 HTML 与 JSON 路由的 `?priority=high` 返回 P0/P1/high 且排除 normal，精确 `?priority=P0`/`normal` 各自命中。 |

> **e2e 遗留 ①**：`run_e2e_batch.py` 覆盖冲掉 07-19 全量 16 本 JSON 为历史数据损失（仅 `docs/e2e-expand-divergence.md` 存叙述数，不可复算）。当前 `e2e_expand_books.py` 已用 `--merge` + 每本书检查点机制按 `pdf` 键合并，不再覆盖，**非当前活 bug**。

---

## v2026-07-21 续十二 — 缺口②：tcm_ocr RuntimeDB 接回 book_pipeline

> 闭环 db-layering.md §7.4 的**深层架构不兼容缺口（缺口②）**：让 `book_pipeline` 在受控开关下构造真正的 `RuntimeDB` 并把三个知识抽取模块接入自动发现链路，使 `knowledge/*/auto_discover.py` 从死代码复活。新增 9 例 mock 测试 + AST 硬守卫；全量 **1003 passed + 2 skipped + 2 deselected**，ruff 全过。版本号维持 **0.25.0**（增量接线，无行为变更）。

| 模块 | 说明 |
|------|------|
| `kzocr/tcm_ocr/pipeline/book_pipeline.py` | **缺口② 闭环**：`__init__` 新增 `self.db_runtime`（受控）；新增 `_init_runtime_db(pg_dsn)`（读 `KZOCR_TCM_KNOWLEDGE`，默认关闭；PG 缺失/构造失败→`None` 无害降级）；新增 `_run_knowledge_auto_discovery(book_id)`（经 `BookConnAdapter` 包装 `current_db_book` 满足 `BookDbConn`，依次调 `auto_discover_{herb,meridian,context}_patterns`，异常非致命）。`process_book` 自动发现步骤改为「`db_runtime` 已构造→知识模块；否则→原 `pipeline/auto_discovery.py` 裸 SQL 桩」——**默认路径零行为变更**。 |
| `tests/test_tcm_ocr_runtime_db_wiring.py`（新增，9 例） | 受控构造 RuntimeDB（开/关/空 dsn/构造失败降级）、知识路径调用 `create_*_pattern`、raw sqlite 经 `BookConnAdapter` 包装、异常非致命、`db_book` 为空跳过、**AST 硬守卫**三个知识模块被 `book_pipeline` 引用。全部 MagicMock PG + 内存 SQLite，CI 无真实 PG。 |

> 范围边界（呼应 db-layering §7）：不改 `archival.py` 裸 SQL，不统一 `archival` 与 `RuntimeDB.archive_*`，不删 `book_db.py`/`BookConnAdapter`，不改 `web/app.py`，不碰主线 `BookDB`，不做指针统一与 schema 合并。开关 `KZOCR_TCM_KNOWLEDGE` 默认 0，置 0 即完全回到冻结栈行为。prod 启用需 Postgres 可用且存在 `HerbOCRPattern`/`MeridianPointOCRPattern`/`FormulaContextPattern` 三表（DDL 属生产前置，不在本代码范围）。

---

## v2026-07-21 续十一 — 低覆盖模块补测 + 校对清单 JSON 导出

> **#1 低覆盖率模块优化**：补齐 cli_review.py / engine/run.py / resources/__init__.py 三个低覆盖模块的纯逻辑测试，cli_review `78%→100%`、engine/run `82%→93%`、resources `79%→100%`。**#2 校对台/导出增强**：`review_manifest` 新增 JSON 导出、`cli_review manifest` 支持 `--json/--out`。新增 4 个测试文件共 35 例，全量 **1003 passed + 2 skipped + 2 deselected**；ruff 全过。版本号 **0.24.0 → 0.25.0**。

| 模块 | 说明 |
|------|------|
| `tests/test_cli_review_cmd.py`（新增） | **#1** cli_review 命令处理器补测：`cmd_review_manifest` / `cmd_review_apply` 多本批量（断言「合计回写 6 条修正（2 本）」）/ `cmd_review_html` / `cmd_review_boxes` / `cmd_review_manifest_json`（#2 的 `--json` 模式）。cli_review 覆盖 `78%→100%`。 |
| `tests/test_engine_run_extra.py`（新增，231 行） | **#1** engine/run.py 纯逻辑分支：`_read_deliverable` 各分支、`_markdown_to_pages` 空分段、`_merge_cross_page_breaks` 空页/空续行/装饰行、`_process_vlm_page` 单页+OverSize、`_init_v07_registry` 真实引擎注册、`run_engine` persist_db 两分支、`_run_vlm` max_pages 截断。engine/run 覆盖 `82%→93%`。 |
| `tests/test_resources_coverage.py`（新增，9 例） | **#1** resources/__init__.py 缺口：空 variant map / 其他键回退 / 模块加载入口 / seed 文件缺失降级 / overlay 缺失文件·非法 JSON·非 dict·dict 合并·标量覆盖。resources 覆盖 `79%→100%`。 |
| `kzocr/scheduler/review_manifest.py` | **#2** 新增 `export_review_manifest_json(manifest, out_path=None)`：用 `dataclasses.asdict` 将 `ReviewManifest` 序列化为 JSON（`ensure_ascii=False, indent=2`），默认文件名 `{book_code}_review_manifest.json`。 |
| `kzocr/cli_review.py` | **#2** `cmd_review_manifest` 支持 `if getattr(args, "json", False)` 走 JSON 导出；`build_review_parser` 的 `manifest` 子命令新增 `--json`（store_true）与 `--out` 参数。 |
| `tests/test_review_export_json.py`（新增，2 例） | **#2** JSON 结构验证（`book_code` + `pages` 列表）+ 默认文件名。 |

> 范围边界：#2 仅新增 JSON 导出通道，与既有 HTML/apply 子命令互不干扰；`--json` 与默认文本清单输出互斥（json 优先）。#1 全部为纯逻辑/零资源测试，不触碰运行时行为。

---

## v2026-07-20 续十 — 校对台增强 + 优先级分级 + 并发估算

> 四大切入点全部落地：分歧高亮 HTML 报告、字符级 bbox 可视化、VL 仲裁自动回填 &
> 优先级三级（P0/P1/normal）、页级并发基准估算脚本。全量 **948 passed + 2 skipped
> + 2 deselected**；ruff 全过。版本号 **0.22.0 → 0.23.0**。

| 模块 | 说明 |
|------|------|
| `kzocr/scheduler/review_manifest.py` | **F1** 新增 `export_divergence_html` 渲染跨引擎分歧为 HTML 报告（字符级差异高亮 `<mark>`，零资源）；**F2** 新增 `visualize_char_boxes` 用 PIL 在页图/空白画布上绘逐行逐字字符框（不同行不同颜色）。 |
| `kzocr/cli_review.py` | `apply` 支持多书批量（`nargs="+"`）；新增 `html` 子命令（分歧高亮报告）；新增 `boxes` 子命令（bbox 可视化）。 |
| `tests/test_review_divergence_html.py`（5 例） | 高亮标记/HTML 转义/相同串无 mark/分组排序/空表。 |
| `tests/test_review_viz_boxes.py`（3 例） | 无 PDF 降级/缺数据/多行颜色区分。 |
| `kzocr/scheduler/orchestrator.py` | **F3** 新增 `_apply_vl_fix` 函数：VL 裁决 accepted_a/accepted_b 时自动搜索行文本并回填 `line.human_final`；`_arbitrate_high_divergences` 内 try/except 兜底。 |
| `kzocr/scheduler/cross_align.py` | **F3** `_is_priority` 从二元（high/normal）增强为三级：P0（剂量数字分歧）、P1（形近字黑名单）、normal（其他）。 |
| `kzocr/storage/db.py` | 新增 `get_page_lines(page_num)` 方法。 |
| `tests/test_vl_fix.py`（4 例） | accepted_a/b 回填、非 accepted 跳过、无匹配静默。 |
| `scripts/bench_page_concurrency.py` | **F4** 页级并发基准估算工具：模拟顺序编排与页面级并行，量化吞吐与理论加速比；支持 mock 引擎参数配置。 |

> 范围边界：F4 为估算工具（文档/分析），并非生产并发编排实现。计划中的页面级并发
> 生产化（ThreadPoolExecutor 处理多页）涉及 orchestrator 状态管理重构，属后续迭代。**


> 消除"双 BookDB"概念混乱：主线 `kzocr.storage.db.BookDB` 是唯一系统 of record；tcm_ocr 的 `BookDB` 类是未接通的遗留死亡代码（Capitalized 表名 + 读取缺失迁移文件），真实 tcm_ocr 库是独立的 snake_case 知识抽取工作台。本次引入共享 `BookDbConn` Protocol 统一类型契约、重指 5 处注解、修复死亡类 schema 源。新增 5 例守卫测试（全量 **930 passed + 2 skipped + 2 deselected**，核心覆盖率 **88.94%**）；ruff 全过。版本号 **0.21.0 → 0.22.0**。

| 模块 | 说明 |
|------|------|
| `kzocr/tcm_ocr/database/sqlite/book_db.py` | **W10 核心**：新增 `@runtime_checkable BookDbConn` Protocol（execute/get_cursor/commit/close/cursor），作为 tcm_ocr 知识/归档层 `db_book` 参数的统一连接契约；模块 docstring 澄清"只有一个主线 BookDB，本文件类是知识抽取工作台连接封装"。`BookDB.initialize_schema()` 改用内置的真实 snake_case DDL（与 `book_pipeline._create_book_database` 一致），消除缺失迁移文件的 `FileNotFoundError`。 |
| `kzocr/tcm_ocr/knowledge/{herb_pattern,meridian_pattern,context_pattern}/auto_discover.py` + `kzocr/tcm_ocr/knowledge/formula/extractor.py` | `from ...book_db import BookDB` → `import BookDbConn`；`db_book: BookDB` → `db_book: BookDbConn`（纯注解变更，零运行时风险）。 |
| `kzocr/tcm_ocr/database/manager.py` | 返回/局部注解 `-> BookDB` → `-> BookDbConn`；保留 `BookDB(db_path)` 实例化（冻结栈不删）。 |
| `docs/plans/db-layering.md` | 新增 §7「W10 双 BookDB 统一」：澄清两套"BookDB"的真实关系与最终职责分层（主线 BookDB=OCR of record；tcm_ocr snake_case 库=知识抽取工作台；Postgres=运营元数据+方剂归档；custom.db=校对包）。 |
| `tests/test_tcm_ocr_db_unified.py`（新增 5 例） | `test_only_mainline_bookdb_in_production`（AST 扫描生产入口，凡引用 BookDB 必须解析到 `kzocr.storage.db.BookDB`，硬守卫"统一"）；`test_bookdb_conn_protocol_contract`（BookDbConn 为 runtime_checkable；保留的 BookDB 提供 get_cursor；raw sqlite3.Connection 缺 get_cursor 不满足契约——印证自动发现链路既有 latent bug，非本次范围）；`test_book_db_initialize_schema_builds_snake_tables`（修复后不抛 FileNotFoundError 且建出真实 snake_case 表）；`test_knowledge_modules_reference_protocol` + `test_manager_still_imports_bookdb_class_for_instantiation`。 |

> 范围边界（明确不做）：① 指针统一（herb/meridian 改查主线 `proofread` 表）——中等风险；② schema 合并（formula/content_node 迁进主线 BookDB）——违背 db-layering 定调、高风险、非紧急；③ 删除 `book_db.py` 及其 `BookDB` 类——冻结栈"不删"，且 manager 仍实例化。已验证生产 OCR 闭环（converter → 主线 BookDB → Celery 接线）零改动、无回归。

---

## v2026-07-20 续八 — W8 覆盖率门禁 + W3 收口 + 测试 hardening

> CI 加覆盖率门禁防回归；tcm_ocr 两处 TODO 收口（注释明确化 + 回归测试）；修复 `learned_confusion.json` 全局状态污染导致的脆弱测试。新增 6 例测试（全量 **907 passed + 2 skipped + 2 deselected**，核心覆盖率 **88.94%**）；ruff 全过。版本号维持 **0.21.0**（v0.22.0 候选）。

| 模块 | 说明 |
|------|------|
| `pyproject.toml` + `.github/workflows/test.yml` | **W8 CI 覆盖率门禁**：`pyproject` 新增 `[tool.coverage.run]`（`omit = ["kzocr/tcm_ocr/*", "tests/*"]`，排除冻结栈与测试代码）+ `[tool.coverage.report]`（`fail_under = 80`）；`test.yml` 依赖加 `pytest-cov`，主测试阶段改 `python -m pytest tests/ --cov=kzocr --cov-report=term-missing`。本地全量验证门禁通过（核心 88.94% > 80%）。 |
| `kzocr/tcm_ocr/llm/pipeline/four_stage_pipeline.py` + `tests/test_four_stage_pipeline.py`（新增 4 例） | **W3 收口**：`four_stage_pipeline` 两处 TODO（heading 纯文本启发式、方剂名 heading 回填）经核实已闭环，将含糊注释明确化为「已实现 / deferred（冻结栈，不引入新依赖）」，新增回归测试锁定 `_backfill_formula_name`（heading 回填）+ `_classify_para_unit`（heading/text 分类），零资源、不改行为。 |
| `tests/test_cross_align.py` / `tests/test_cross_align_logic.py` | **脆弱测试修复**：两测试原传 `tmp_path` 缺失文件期望返回空，但 `load_confusion_keys`/`load_confusion_keys_split` 总是合并全局 `_LEARNED_CONFUSION_PATH`，隐含依赖该文件为空；仓库根 `learned_confusion.json` 被历史运行污染（含 X/Y/A/B/C/D）致其偶发失败。已加 `monkeypatch` 隔离全局 learned 路径，并清理被污染的生成产物。 |
| `tests/test_web_routes.py`（新增 2 例） | **W1 继续补测**：补 `register_submit` 的 toc 非空路径与非法 toc_json 解析失败降级两个细分支。 |

---

## v2026-07-20 续七 — W7 终校反馈→混淆集自动回流

> `review_manifest.feedback_apply` 在人工终校修正 (误认字 ocr_char → 正确字 expected) 时自动调 `add_learned_confusion` 回流进 `learned_confusion.json`，形成「分歧→仲裁→终校→回流」闭环；`load_confusion_keys`/`load_confusion_keys_split` 合并学习集（修分侧检测器看不到回流的不一致）。新增 8 例端到端测试（全量 **901 passed + 2 skipped + 2 deselected**，较 893 +8）。零运行时风险，版本号维持 **0.21.0**（v0.22.0 候选）

| 模块 | 说明 |
|------|------|
| `kzocr/scheduler/review_manifest.py` | 新增 `_parse_confusion_pair` 解析 anomaly.details `confusion;wrong=X;correct=Y`（verifier.py:228 写入）；`build_review_manifest` 据此填 `ReviewIssue.ocr_char`，供人工终校后回流；`feedback_apply` 对每个 `ocr_char`/`expected` 非空且相异的 issue 调 `add_learned_confusion(source="review_manifest")`（首次即写、去重，不做频率门控） |
| `kzocr/scheduler/cross_align.py` | 新增 `_merge_split_keys` 公共合并 helper；`load_confusion_keys`/`load_confusion_keys_split` 合并 `learned_confusion.json`（与 `load_confusion_set` 行为对齐）；`add_learned_confusion` 同步 `_KEYS_CACHE`/`_KEYS_SPLIT_CACHE`（学习对归三级通用，已有更高优先级不降级） |
| `tests/test_review_backflow.py`（新增 8 例） | `_parse_confusion_pair` 解析/非混淆；`build_review_manifest` 从 confusion 异常填 ocr_char、非混淆不填；`feedback_apply` 回流落盘 + `_is_priority` 命中 + 分侧检测器受益；ocr_char==expected / expected 空不回流；回流去重 |

---

## v2026-07-20 续六·补 — W6 xref 渲染健康度回检（已推送 origin/main）

> 新增 `scripts/check_render_health.py`，全量跑 v4 九本（共 **541 页**）：异常 **102 页**、**真丢字风险页 0 页**，所有异常（整本扫描件 / 封面无文本层 / xref 告警但文本完好）对图像 OCR 良性。报告落档 `e2e_expand/render_health/report.md` + `report.json` + 异常截图。**修复**：脚本原 `_crop_to_body` 拉起 PaddleX `PP-DocLayoutV3` 偶发 SIGTERM 崩溃（首轮回检只跑 3 本且未出报告），改纯投影降级 `_crop_to_body_fallback`。提交 `3b9423e`（与 W5 `419d908` 一并推送 `2ffad9c..3b9423e`）。

---

## v2026-07-20 续六 — W5 Celery 可观测性结构化日志

> `process_book_task` 结束记结构化汇总日志（页数/分歧数/BookDB 落库成败/耗时），机器可解析字段挂在 `celery_task_metrics`；新增 7 例回归测试。零运行时风险，版本号维持 **0.21.0**（v0.22.0 候选）

| 模块 | 说明 |
|------|------|
| `kzocr/tcm_ocr/celery_tasks/tasks.py` | `_persist_to_mainline_bookdb` 返回三态 `True/False`（成功/失败，此前恒 `None`）；新增 `_log_task_summary` helper，把 `pages`/`lines`/`divergences`(tcm_ocr `disputed_lines`)/`vl_calls`(本路径恒 0)/`elapsed_seconds`/`bookdb_persisted`(None=未开启) 聚合成带 `extra={"celery_task_metrics": {...}}` 的一条结构化日志；`process_book_task` 成功路径与幂等跳过路径均调用。VL 调用数沿用 0（视觉仲裁由 v0.7 orchestrator 路径承担），未引入 prometheus 依赖（按计划「先确认再实现」保留） |
| `tests/test_celery_task_summary.py`（新增 7 例） | `_log_task_summary` 字段完整 + 跳过标签 `n/a`；persist 返回 `True`(成功)/`False`(失败)；集成调 `process_book_task.__wrapped__.__func__` 配 fake self，断言汇总 `pages/lines/divergences/elapsed` 与 fake result 一致、`bookdb_persisted=None`(env 关)/`True`(env 开落库成功)/`False`(env 开落库失败且不抛) |

---

## v2026-07-20 续五 — W2 核心模块纯逻辑补测查漏

> 新增 3 个测试文件共 **+19 例**（全量 **880 passed + 2 skipped + 2 deselected**，较 861 +19）；4 个候选模块覆盖率：errors 100%、leakage 100%、hierarchy 96%→98%、to_zai_prisma 78%→84%（4 模块合计 86%→90%）。零运行时风险，版本号维持 **0.21.0**（v0.22.0 候选）

| 模块 | 说明 |
|------|------|
| `tests/test_to_zai_prisma_coverage.py`（新增 11 例） | 覆盖 `push_book_to_zai` 的 is_mock 阻断、三大范式库（herb/meridian/context）+ Term + Formula/FormulaIngredient 插入分支、`export_markdown` 正文/范式/术语/方剂渲染（含 out_path 写文件）、冻结库保护（`overwrite=False` 抛错 / `overwrite=True` 解除重写）、`import_proofread_package` 无 Line 行返回 `book_code=None`、文件缺失 `FileNotFoundError`、BookDB 落库失败记 `[DATA INTEGRITY]` 告警不阻断导出 |
| `tests/test_leakage.py` 增补（9 例） | `LeakageDetector._is_excluded` 含/不含排除词、`detect` 空白 page_b / 排除词前缀探针跳过的边界、`apply_leakage_defense` 的 L1 超基线阈值 / L2 超 max_tokens*2 / 相邻空页跳过等四层判定边界日志分支 |
| `tests/test_hierarchy.py` 增补（1 例） | 目标页四周非空邻居不足 → 跳过该页分支 |
| 未覆盖项（合理范围） | to_zai_prisma 剩余空缺均为环境相关分支：Postgres 元数据注册/归档路径（111-136、491-510，需 psycopg2+PG 连接）、chmod OSError 降级（85-86、215-216、218-221）、重导出 `except` 兜底（270-271）；hierarchy 剩余 1 行为不可达的 `median==0` 防御分支（邻居空页已被跳过，中位数恒为正） |

---

## v2026-07-20 续四 — W1 web 补测续（带数据分支 + 未测路由）

> `test_web_routes.py` 在既有 37 例基础上 **+30 例**（共 67），补全此前仅测「空库降级」的 handler 的「带数据」分支，以及此前完全未测的 `/`、`/health`、`/api/confusion`、`/engines/status/all`（含配置）等路由；全量 **861 passed + 2 skipped + 2 deselected**（较 831 +30）。web/app.py 覆盖 **53%→78%**。零运行时风险，版本号维持 **0.21.0**（v0.22.0 候选）

| 模块 | 说明 |
|------|------|
| 带数据分支补全 | 复用 `_populate_book` 助手（写 page/ocr/verify/anomaly/benchmark/cross_divergence/quality 真实数据）驱动：book 详情/异常/解决/方剂/分歧/质检/看板/方剂详情/工作台、monitor/benchmark/monitor-api 看板、search 带查询、REST `/api/books`(列表/详情/页/异常/方剂/分歧/解决)、`/api/engines`、`/registrations`、`/register/{code}` 编辑等 handler 的「有数据」路径 |
| 此前未测路由 | `test_index_with_data`（首页汇总）、`test_health`（`/health` 健康态）、`test_api_confusion_post`/`test_api_confusion_invalid_json`（`/api/confusion` 自学习混淆对 JSON 端点，路径经 monkeypatch 指向临时目录避免污染仓库 `learned_confusion.json` 与模块级缓存）、`test_engines_status_all_with_config`（`/engines/status/all` 配置非空分支，ftp:// 无效 URL 不联网直达 offline） |
| 覆盖率未覆盖项 | 仍空缺：真实网络探测分支（`/engines/{name}/status`、`/engines/status/all` 联网探测体、`/api/engines/{name}/test` 端口/进程检查）、`/health` 降级（`db_ok=False`）、`register_submit` 空 code 重定向等，属内存测试难以覆盖部分 |

---

## v2026-07-20 续三 — W4 VL 仲裁预算控制（防止付费端点失控开销）

> 新增 `vl_budget.py` + `SchedulerConfig` 两字段 + orchestrator 三处 VL 调用点透传；全量 **831 passed + 2 skipped + 2 deselected**（较 820 基线 +11，含 test_vl_budget 7 例 + test_orchestrator 3 例）。版本号维持 **0.21.0**（v0.22.0 候选）

| 模块 | 说明 |
|------|------|
| VL 预算守卫（`kzocr/scheduler/vl_budget.py`） | `VLBudgetConfig(per_run/per_day)` + `VLBudgetTracker`：per_run 内存计数限制单次编排视觉仲裁调用数；per_day 经可注入 `DayStore`（`_FileDayStore` JSON best-effort / `_MemDayStore` 测试）跨书当日累计。每次实际 VL 调用（`arbitrate_divergence`/`recheck`）前 `can_spend()` 判余量、调用后 `spend()` 计数 |
| 配置接入 | `SchedulerConfig` 新增 `vl_budget_per_run`/`vl_budget_per_day`，环境变量 `KZOCR_VL_BUDGET_PER_RUN`/`KZOCR_VL_BUDGET_PER_DAY`（默认 0=不限，经 `_safe_int` 解析） |
| orchestrator 透传 | `_arbitrate_high_divergences` 与 `_sample_consensus_error` 逐次检查预算，超预算停止 VL 调用、分歧留人工队列（同 conservative 降级语义），记 `detector_chain=["VLBudget"]` 观测异常；`orchestrate_book` 构造 tracker 透传三处调用点，书末打印 `VL budget usage` 对账日志 |
| 测试 | `tests/test_vl_budget.py` 7 例（不限/per_run 边界/per_day 跨书累计/fake clock 日期隔离/双维度）；`tests/test_orchestrator.py` 增 3 例（预算预耗尽全跳/逐次计数/抽样耗尽）。零资源可测，无真实 VL 调用 |

**使用**：大批量处理前设 `KZOCR_VL_BUDGET_PER_RUN=200`（单书上限）与 `KZOCR_VL_BUDGET_PER_DAY=1000`（当日上限），超预算的 high 分歧自动转人工队列，避免 GLM-4V-Flash 等付费端点失控。

---

## v2026-07-20 续二 — 零资源收口（核心模块补测 / web 路由补测 / tcm_ocr 清理）

> 纯逻辑测试 + 注释/注解清理，零运行时风险；全量 **820 passed + 2 skipped**（较 v0.21.0 的 753 +67）；ruff 默认与 `--select ANN` 三引擎文件均通过。版本号维持 **0.21.0**

| 模块 | 说明 |
|------|------|
| A 核心模块纯逻辑补测（`f5f7495`） | 新增 5 个测试文件共 **30 例**：`test_engine_config`(7)、`test_registration`(7，registration.py 覆盖 82%→100%)、`test_to_zai_prisma_logic`(8，_uid/_resolve_db/freeze_custom_db/_restrict_db_perms)、`test_modelscope_pool_logic`(2，拆文本/视觉 provider 两场景)、`test_cross_align_logic`(6，混淆文件边界)。零系统资源消耗 |
| B web/app.py 路由单测（`d39af17`） | 新增 `test_web_routes.py` **37 例**（临时目录隔离 4 个环境变量，零网络零真实 DB）；覆盖 prompts/engines/registration/book 空库降级/monitor/benchmark 等路由。web/app.py 覆盖 **35%→53%+**（纠正原记忆「~100%」偏差）；顺带修正 `app.version` 硬编码 `0.19.0`→动态读 `__version__`（现 0.21.0） |
| C tcm_ocr stub 注释规范化（`d1b0dd3`） | `graded_scheduler.py` 三处误导性 "stub" 注释改为「已知未接入引擎类型，降级返回空（设计意图）」；four_stage_pipeline/page_pipeline 的真实 TODO 保留不删；tcm_ocr 平行栈保持冻结 |
| D tcm_ocr 核心引擎类型注解（`0fa79fc`） | `graded_scheduler`/`mineru_adapter`/`paddleocr_adapter` 三文件 **14 处 ANN401 全部清零**（tcm_ocr 全栈 206→192）；`__exit__` 异常三元组改 `type[BaseException]\|None`/`BaseException\|None`/`types.TracebackType\|None`；`_init_engine3/4` 返回 `Optional[Dict[str,Any]]`；`term_kb` 鸭子类型抽 `TermKnowledgeBase` Protocol；`_preprocess_rec_image` 用 `TYPE_CHECKING` 守卫的 `paddle.Tensor` 前向引用 |

---

## v2026-07-20 — 古籍跨引擎分歧实测 v4（9 本追加，25 本全量复盘）

> 文档/ANA 更新，无代码变更；数据来源 `e2e_expand/summary_v4.json`（9 本 / 337 页）；合并前 16 本 = **25 本 / 1177 页 / 15688 分歧 / 4158 高分歧**，平均 div/pg=13.3、high 占比 26.5%

| 模块 | 说明 |
|------|------|
| 古籍跨引擎分歧实测 v4 | `scripts/e2e_expand_books.py --list e2e_expand/books_expand_v4.txt` 追加 9 本（mi-678、sh、名老中医之路全集、全量中药速查总表、264附子/265乌头/267半夏/268虎掌 单味药专论、中医中西医重点解读）；v4 合计 337 页 / 3987 分歧 / 1164 高分歧，div/pg=11.8、high 占比 29.2%；逐页分布中位 9、max=61、零分歧页 11.3%、无灾难尖峰 |
| 关键发现 | ① 分歧率仍由内容决定，v4 内 4.4（名老中医之路）→ 20.3（264附子）无统一带；② **high 占比是更敏感的二级信号**（mi-678 45.2%、速查表 43.4% 近半分歧为 high）；③ **采样应跳过封面/目录区从正文起算**（附子 p0–11 几乎全 0 分歧，p0 起采样会系统性低估）；④ MuPDF xref 损坏告警（速查表 p30）需专项渲染回检；⑤ 耗时稳定 ~21s/页无长作业退化 |
| 流水线含义修订 | high 占比作为 §5.5 VL 自动仲裁可信度二级判据；扩面采样协议修正（跳过非正文页）；带 xref 告警源文件增加渲染回检 |
| 文档 | [`docs/e2e-expand-divergence.md`](docs/e2e-expand-divergence.md) 标题/页眉扩至 25 本，新增 §7 v4 专节（九本汇总表 + 25 本全量 + 6 条关键发现 + 流水线含义修订 + 复现命令） |

---

## v2026-07-20 续 — v4 扩面结论落地（脚本增强 + high 占比判据 + 覆盖率补测）

> 文档/ANA/纯逻辑测试，无运行时风险；全量 **753 passed + 2 skipped**（较 v0.21.0 的 738 +15）

| 模块 | 说明 |
|------|------|
| 扩面脚本增强 | `scripts/e2e_expand_books.py` 新增 `--body-start N`（采样跳过封面/目录区从正文起算）+ `render_page` 返回 `(img, healthy)` 对 fitz 文本层缺失且图像非空白的页标记 `healthy=False`（疑似 xref 损坏丢字），`count_book` 收集 `render_warnings`；新增 4 例回归测试 |
| high 占比二级判据 | `orchestrator._is_conservative(tally)`：全书 high/总分歧 ≥ 0.40（样本 ≥10 页）进入保守模式；`_arbitrate_high_divergences(conservative=True)` 即便 VL 明确接受也全部留人工复核；`orchestrate_book` 维护全书分歧累计 tally 并同源回写成功/失败两路径；新增 4 例单测（保守覆盖接受/默认路由回归/阈值/tally 串流集成） |
| 覆盖率补测 | `kzocr/engine/prompt_manager.py` 纯文件 I/O 逻辑补 7 例单测（save/load 往返、缺失返回 None、损坏文件回退、list 含加载失败项、delete、init_defaults 幂等、KZOCR_PROMPT_DIR 覆盖），覆盖率 **74% → 98%** |
| 文档 | `docs/e2e-expand-divergence.md` §7.4 三项修订建议标注已落地并附提交号 |

---

## v2026-07-19 — v0.21.0 零资源收口（卫生/注解/测试/默认跨校验）

> **740 tests**（740 passed + 2 skipped；净增 8 = ratelimit 新测试 + 注册表纯逻辑测试）；ruff --select ANN 核心模块清零；覆盖率核心模块 83%、ratelimit 90%。自 v0.20.0 以来 ~30 笔提交通通无运行时变更。

| 模块 | 说明 |
|------|------|

### 文档漂移闭环
- **轮询采样** §2.3/§4.1 同步 + tier_limits 可突破策略明确
- **decay/record** 签名对齐、伪代码修正
- **EngineRegistry 线程锁** 删 `self._lock` 声明+标注单线程设计
- **scheduler/__init__.py** 「待实现」→「已落地」

### 代码卫生
- **死代码清理** registry.py 模块级 `select_candidates` + ratelimit.py MultiTokenRateLimiter.acquire 不可达耗尽路径
- **_bayesian_score 清扫** 函数+常量+测试一并删除（`EngineScheduler._compute_bayesian_score` 覆盖）
- **load_benchmarks 逐行容错** JSONDecodeError 下放逐行级，单行损坏不丢整文件

### 类型注解
- **web/app.py 50 处 ANN** 核心模块最后一处排除文件退出排除

### 默认行为
- **跨引擎校验默认开启** `cross_check: bool = True`

### 测试增强
- **add_learned_confusion** 5 例纯逻辑单测（文件读写/去重/损坏恢复/缓存同步）
- **registry 纯逻辑** 4 例（glyph 状态累加/路径穿越/损坏文件/pending）
- **ratelimit.py 9 例** 覆盖 wait/register/store 恢复/80% 分支/refill，覆盖率 68%→90%
- **版本+徽章同步**

---

## v2026-07-19 — v0.20 v0.7 调度器层完整落地 + 校对反馈闭环

> **740 tests**（736 passed + 2 skipped；2 例 benchmark 标记默认 CI 排除）；真实引擎（PaddleOCR / RapidOCR / GLM-4V-Flash）已接入并做性能基准

| 模块 | 说明 |
|------|------|
| v0.7 调度器核心 | EngineRegistry（注册中心 + NDJSON 基准持久化 + 贝叶斯评分 + probe_engines） + EngineScheduler（九步候选选择：pinned/tier/竖排/cloud/backoff/429/预算/加权/Top-N/轮询） + GlyphVerifier + Orchestrator 全部完整实现并运行 |
| SchedulerConfig | 统一管理 16 个调度环境变量，替换 orchestrator/run_engine/VLM 中散装 env read |
| review_manifest | 人工校对清单（P0/P1/P2 优先级） + feedback_apply 回写 BookDB |
| CLI benchmark | `kzocr benchmark status/history/run/reset` 子命令 |
| DB 分层 + Celery | BookDB 内容表 + 导出/导入闭环 + Celery 生产链路 KZOCR_PERSIST_DB 持久化 |
| 字符级 bbox | char_boxes 落地，零成本开销基准落地 |
| conf≤0.90 门控 | RapidOCR 置信度传递修复 + 低置信 PASS 页挂起复核 |
| 引擎适配器 | PaddleOCRAdapter 迁移 predict + 弃用告警回归测试 + AllEnginesFailedError 异常 |
| 性能基准 | DPI 72/150、进程单例、引擎倍速结论归档 |
| 视觉回看 | GLM-4V-Flash 生产接线 + 端到端验证 |
| 高分歧页视觉仲裁 | 成功/失败路径 high 分歧送 GLM-4V-Flash Box-Guided 仲裁（§5.5）：抽 `_arbitrate_high_divergences` 共享 helper，VL 已裁决（accepted_a/b、both_wrong）不进 M4 队列，仅 manual/无视觉能力时进人工复核；失败路径内联循环去重；新增 6 例 mock VL 路由 + 状态更新 + 静默跳过测试 |
| Celery Worker 部署文档 | README 新增 Celery Worker 节（docker compose redis+worker、broker 环境变量、镜像不含 OCR 引擎说明） |
| 代码评审硬化 | orchestrator 字符框按真实页号对齐（失败页缺口不再错配）；celery 移除双重重试（突破 max_retries）；run.py VLM 重试崩溃兜底；cross_align boxes_a 长度守卫；删除死代码 |
| 古籍跨引擎分歧实测（16 本扩面） | `scripts/e2e_expand_books.py` 升级：增量合并 `--merge`（只算未覆盖页）+ 每书检查点（防长作业崩溃丢进度）+ 含空格文件名容错；**修正旧 5 本样本假象**：分歧/页非固定带，实测区间 **3.8–47.5/页**（干净专著最低：疼痛妙方 3.8、学姚派 4.8；密集验方类最高：验方新编下册 47.5、上册 17.1）；16 本合计 840 页 / 11701 分歧 / 2994 高分歧，平均 div/pg=13.9、high/pg=3.6；逐页分布无单页灾难尖峰（属真实引擎分歧非 OCR 整页失败）；20 页采样对单书分歧/页具代表性（5 本加深到 80 页同量级波动）；**逐本书明细见 [`docs/e2e-expand-divergence.md`](docs/e2e-expand-divergence.md)** |
| Box-Guided VL 接入逐字框 | `cross_align.align_boxes_to_text` 把逐行 char_boxes 展平并逐字去标点对齐到 `boxes_a`，成功/失败两路径 `run_cross_align` 均传入；单字分歧（形近字/数字）现带 1 框 → §5.5 视觉仲裁精确裁框（box_guided）而非整页退化；框数 ≠ 文本字符数时安全降级为整页（不误配）；新增 6 例单测 + 端到端落库测试 |

## v2026-07-19 续 — 零资源收口（代码卫生 + 文档漂移闭环 + 测试增强）

> **732 tests**（732 passed + 2 skipped；净减 4 = 移除冗余 TestSelectCandidates）；ruff --select ANN 核心模块清零（web/app.py 退排）；全量 ruff 通过

| 模块 | 说明 |
|------|------|
| 文档漂移闭环 | DETAILED 稿轮询 §2.3/§4.1 同步 + decay/record 伪代码签名对齐 + EngineRegistry 线程锁过度宣称（删 `self._lock` 声明，标注单线程设计） |
| 死代码清理 | 删除 registry.py 模块级 `select_candidates`（零调用方，被 EngineScheduler.select_candidates 取代）+ 同步删冗余测试；`scheduler/__init__.py`「待实现」→「已落地」 |
| web/app.py 类型注解 | 50 处 ANN 全清（`-> Response` / `RedirectResponse` / `dict[str, Any]` 等）；最后一处核心模块退出 ruff ANN 排除名单 |
| add_learned_confusion 测试 | 5 例纯逻辑单测覆盖首次写入/去重/非法输入/文件损坏恢复/缓存同步 |
| CHANGELOG + 徽章同步 | README `tests-736`→`tests-732`；本表同步 |

## v2026-07-10 — v0.19 Web 增强 + 安全加固 + CLI 自动补全

> **483 tests**

| Commits | 模块 | 说明 |
|---------|------|------|
| `b8793a4` + `9f80e92` + `6a711a2` | Web | `/registrations` 已登记列表、`/book/{code}/quality` 质检页面、`/health` 端点 |
| `b8793a4` | 安全 | Docker `USER 1000:1000`、`.dockerignore` 补 `*.key` |
| `b8793a4` | CLI | `kzocr completion bash\|zsh\|fish`（shtab） |
| `9f80e92` | CI | `test.yml` 补 shtab 依赖 |

---

## v2026-07-10 — v0.18 文档代码一致性修复

> 多角色审计修复：版本同步、功能列表补全、CHANGELOG 补 v0.14/v0.17

| 文件 | 修复 |
|------|------|
| `pyproject.toml` | version `0.11.0` → `0.17.0` |
| `README.md` | 版本徽章 `0.14.0`→`0.17.0`；状态表补 v0.14–v0.17；功能/命令补全 |
| `CHANGELOG.md` | 补 v0.14（产品化）、v0.17（书籍登记）条目 |
| `docs/reviews/doc-code-audit.md` | 文档代码一致性审计报告 |

---

## v2026-07-10 — v0.17 Web UI 书籍登记 + 完整目录层级表单

> **6 files changed, 331 insertions, 479 tests**

| Commit | 模块 | 说明 |
|--------|------|------|
| `5fbb2f4` | `engine/registration.py` | 登记管理（save/load/list + registration_to_toc） |
| `5fbb2f4` | `web/templates/register.html` | 表单页面（元数据 + 1-5 层动态 TOC 行） |
| `5fbb2f4` | `web/app.py` | GET/POST /register 路由 |
| `5fbb2f4` | `engine/toc.py` | enrich_book_result 优先使用预登记 TOC |
| `5fbb2f4` | `tests/test_web_registration.py` | 4 例 |

---

## v2026-07-10 — v0.16 LLM 质检增强 + API 文档

> **475 tests**

| Commit | 模块 | 说明 |
|--------|------|------|
| *(current)* | `quality.py` | R3: LLM prompt 空字段占位符 |
| *(current)* | `db.py` | `quality_result` 表 + save/get 方法 |
| *(current)* | `cli.py` | `kzocr quality check/list` 子命令 |
| *(current)* | `web/app.py` | FastAPI 元数据（title/version/contact） |
| *(current)* | `tests/test_db.py` | 2 例 quality_result 测试 |
| *(current)* | `tests/test_cli.py` | 2 例 quality CLI 测试 |

---

## v2026-07-10 — v0.14 产品化（JSON导出/Web可视化/批量处理/校对工作台）

> **10 files changed, 418 insertions, 468 tests**

| Commit | 模块 | 说明 |
|--------|------|------|
| `f1f45af` | `export_zai.py` | `export_json` 结构化导出（recipes/herbs/quality_issues） |
| `f1f45af` | `cli.py` | `--format json` + `batch` 子命令 |
| `f1f45af` | `web/app.py` | dashboard/recipe_detail/search/workspace 4 路由 |
| `f1f45af` | `web/templates/` | 4 个新页面模板 |
| `f1f45af` | `test_web_enhanced.py` | 6 例 |

---

## v2026-07-10 — v0.15 文档体系完善

> README/CONTRIBUTING 全面重写，468 tests

| Commit | 模块 | 说明 |
|--------|------|------|
| *(current)* | `README.md` | 全面重写（功能一览/架构/命令参考/REST API/项目状态） |
| *(current)* | `CONTRIBUTING.md` | 更新测试命令 + 性能基准门禁说明 |

---

## v2026-07-10 — v0.14 产品化（JSON导出/Web可视化/批量处理/校对工作台）

> **10 files changed, 418 insertions, 468 tests**

| Commit | 模块 | 说明 |
|--------|------|------|
| `f1f45af` | `export_zai.py` | `export_json` 结构化导出（recipes/herbs/quality_issues） |
| `f1f45af` | `cli.py` | `--format json` + `batch` 子命令 |
| `f1f45af` | `web/app.py` | dashboard/recipe_detail/search/workspace 4 路由 |
| `f1f45af` | `web/templates/` | 4 个新页面模板 |
| `f1f45af` | `test_web_enhanced.py` | 6 例 |

---

## v2026-07-10 — v0.13 LLM 质检管道

> **1 commit, 4 files changed, 240 insertions, 462 tests passed**

| Commit | 模块 | 说明 |
|--------|------|------|
| *(current)* | `analysis/quality.py` | QualityChecker（rule-only + LLM-assisted）、RecipeIssue、QualityResult |
| *(current)* | `analysis/recipe_parser.py` | ParsedRecipe 补 `issues` 字段 |
| *(current)* | `tests/test_quality.py` | 7 例（字段缺失/剂量异常/单字药名/完整/LLM集成/LLMOK/LLM降级） |

### 质检项目

| 检查项 | 规则 | LLM 兜底 |
|--------|------|----------|
| 字段完整性 | 组成/主治必填 | 缺失时判断真实原因 |
| 剂量合理性 | 单味 > 100g 告警 | 确认是否合理 |
| 药名可疑 | 单字药名告警 | 判断是否为真实误识别 |

---

## v2026-07-10 — v0.12 仓库清理

> 38 个误提交的 `trace/*.jsonl` 运行时产物从 git 移除，`.gitignore` 补 `trace/`。

---

## v2026-07-10 — v0.11 REST API + Docker 化

> **1 commit, 8 files changed, 270 insertions, 455 tests passed**

| Commit | 模块 | 说明 |
|--------|------|------|
| *(current)* | `web/app.py` | 8 个 JSON REST API 端点（`/api/books`系列） |
| *(current)* | `Dockerfile` | Python 3.12-slim 容器镜像 |
| *(current)* | `docker-compose.yml` | kzocr 服务 + 持久化 volume |
| *(current)* | `pyproject.toml` | version 0.6.0 → 0.11.0；新增 `[web]` 可选依赖 |
| *(current)* | `tests/test_web.py` | 6 个 JSON API 测试 |

### API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/books` | 书籍列表 |
| `GET` | `/api/books/{code}` | 书籍详情 |
| `GET` | `/api/books/{code}/pages` | 逐页进度 |
| `GET` | `/api/books/{code}/anomalies` | 异常列表（`?status=` 过滤） |
| `POST` | `/api/books/{code}/anomalies/{id}/resolve` | 标记决议 |
| `GET` | `/api/books/{code}/recipes` | 方剂列表 |

### Docker

```bash
docker compose up -d     # 启动 Web 面板
docker compose build     # 构建镜像
docker compose down      # 停止
```

Web 面板可通过 `pip install kzocr[web]` 安装 Web 依赖。

---

## v2026-07-10 — v0.10 性能深度优化

> **1 commit, 5 files changed, 100 insertions, 449 tests passed**

| Commit | 模块 | 说明 |
|--------|------|------|
| *(current)* | `verifier.py` | 资源缓存（`_RESOURCE_CACHE` 进程级，重复 I/O → 0.1ms） |
| *(current)* | `concurrency.py` | 全局 `ThreadPoolExecutor` 单例（复用线程池，避免反复创建） |
| *(current)* | `tests/benchmarks/test_e2e_book_perf.py` | 全书 100 页 / 10 页端到端 CI 基准 |
| *(current)* | CI | 性能基准已含全书门禁 |

### 性能预期

| 指标 | 优化前 | 优化后 |
|------|--------|--------|
| `GlyphVerifier()` 重复构造 | ~5ms（重复 I/O） | ~0.1ms（缓存命中） |
| 全书 100 页 mock 编排 | 待测量 | <30s CI 门禁 |
| 并发 executor 开销 | 每次新建线程 | 复用全局池 |

---

## v2026-07-10 — v0.9 并发编排集成 + Web 管理面板

> **3 commits, 11 files changed, 676 insertions, 447 tests passed**

### 日期：2026-07-10

### 方向 C：并发编排集成

将 v0.8 阶段三的 `run_engines_concurrent` 集成到 E4 `orchestrate_book` 的 Tier2/Tier3 循环。

| Commit | 模块 | 说明 |
|--------|------|------|
| `6b8ee08` | `orchestrator.py` | Tier2/Tier3 从顺序 for 循环改为 `run_engines_concurrent` 并发执行 |
| `6b8ee08` | `orchestrator.py` | 加入 egress 预过滤（`validate_url` 检查） + `AdaptiveController` 动态并发调谐 |

### 方向 E：Web 管理面板

基于 FastAPI + Jinja2 的管理面板，设计借鉴 TCM-Modern-OCR 项目的 sidebar+content 布局。

| Commit | 模块 | 说明 |
|--------|------|------|
| `7ac0f71` | `kzocr/web/app.py` | FastAPI 应用，5 路由：首页/书籍详情/方剂列表/异常管理/决议 |
| `7ac0f71` | `web/templates/*.html` | 5 个 Jinja2 模板（base, index, book, anomalies, recipes） |
| `7ac0f71` | `cli.py` | `kzocr web --host --port` 子命令 |
| `7ac0f71` | `tests/test_web.py` | 6 例 Web 测试（FastAPI TestClient） |
| `7ac0f71` | `.github/workflows/test.yml` | 补 fastapi/uvicorn/jinja2 依赖 |

### 验证

| 指标 | v0.8 | v0.9 |
|------|------|------|
| 测试总数 | 441 | 447 (+6) |
| CI 门禁 | ruff + test + perf + chaos | + web test |
| Web 面板 | 无 | FastAPI + 5 路由 |

---

## v2026-07-10 — v0.8 五大方向全完成

> **5 commits, 16 files changed, 1542 insertions, 441 tests passed**

### 日期：2026-07-10

### 阶段一：方剂结构化入库

| Commit | 文件 | 说明 |
|--------|------|------|
| `6cdab80` | `kzocr/analysis/recipe_parser.py` | 九字段分割、药材解析（含"各X克"）、加减解析（规则，无需 LLM） |
| `6cdab80` | `tests/test_recipe_parser.py` (15例) | 方剂编号、药材解析、加减、hash |

### 阶段二：TOC 章节合并 + 三级编号校验

| Commit | 文件 | 说明 |
|--------|------|------|
| `ccc6b33` | `kzocr/engine/section_merger.py` | `merge_by_toc` 按 TOC 树合并章节、`_validate_numbers` 防 33% 事故 |
| `ccc6b33` | `tests/test_section_merger.py` (11例) | 无TOC/有TOC/子章节/编号跳号/节号切换/Markdown |

### 阶段三：并发引擎调度

| Commit | 文件 | 说明 |
|--------|------|------|
| `0fb0da7` | `kzocr/scheduler/concurrency.py` | `AdaptiveController`（滑动窗口20次错误率→并发调谐）、`run_engines_concurrent` |
| `0fb0da7` | `tests/test_concurrency.py` (7例) | 控制器边界、错误率升/降、并发取最快 |

### 阶段四：校对工单 CLI

| Commit | 文件 | 说明 |
|--------|------|------|
| `fae6263` | `kzocr/cli_review.py` | `kzocr review list/show/resolve` 子命令 |
| `fae6263` | `kzocr/storage/db.py` | `get_unresolved_anomalies` / `resolve_anomaly` |
| `fae6263` | `tests/test_cli_review.py` (4例) | 列出、查看、标记决议 |

### 阶段五：质量工程

| Commit | 文件 | 说明 |
|--------|------|------|
| `a9d4fc9` | `tests/benchmarks/` | 性能基准 CI 门禁（<10ms/<50ms） |
| `a9d4fc9` | `tests/test_chaos.py` | 混沌注入（API失败降级、全引擎失败→HumanGate） |
| `a9d4fc9` | `.github/workflows/test.yml` | 新增 performance + chaos 步骤 |

### 验证

| 指标 | v0.7 | v0.8 |
|------|------|------|
| 测试总数 | 396 | 441 (+45) |
| 覆盖方向 | 编排 | 编排 + 方剂 + 章节 + 并发 + 质控 |
| CI 门禁 | ruff + test | + performance + chaos |

---

## v2026-07-10 — v0.7 自适应引擎编排系统（大版本）

> **44 commits, 36 files changed, 4801 insertions, 396 tests passed**

### 日期：2026-07-10

### 架构概览

v0.7 引入完整的自适应引擎编排层，涵盖引擎注册/调度/字形验证/编排主循环（E1-E5）
和增强功能 TOC 抽取/结构化入库/断点续跑/即时 Benchmark（F1-F3）。

```
run_engine(pdf) → EngineRegistry → EngineScheduler → GlyphVerifier
                → OrchestrateBook(Tier1→Tier2→Tier3→HumanGate)
                → BookDB 写入 + TOC enrich + 即时 Benchmark 更新
```

### 新增/修改

| Commit | 模块 | 说明 |
|--------|------|------|
| `beea3eb` | E1 E2 | EngineRegistry + EngineScheduler + 贝叶斯评分 + prob_engines |
| `0d71278` | CLI | `--use-v07` 参数，`KZOCR_USE_V07` 环境变量 |
| `a3d2da1` | E5 | run_engine 委派模式集成 + 适配器（BookPipeline/VLM/Mock） |
| `38526e2` | E4 | OrchestrateBook 主循环（Tier1→T2→T3，双闸截断，超时包裹，trace） |
| `9c60c3b` | E3 | GlyphVerifier + 5 检测器（ToxinDose/Leakage/CharSpike/Confusion/TermKB） |
| `1435519` | F1 | TOC 抽取（文本方案，OCR 容错模糊匹配，5 层章节树） |
| `61b769e` | F2 | BookDB（SQLite WAL，`page_progress` 三态机 + `hierarchy_anomaly`） |
| `e595aae` | F3 | 滚动窗口 Benchmark + 混合评分 + 自适应 Backoff 调度 |
| `ed99d88` | types | `BookResult` 补 `uncertain_pages`/`engine_trace`/`toc`；`TocEntry`/`TocTree`/`GlyphVerdict` |
| `57d5f34` | 评审 | F1 TOC 多角色评审（架构/安全/领域/测试，5 B 类 + 10 R 类建议） |
| `12bc44f` | 冒烟 | MockAdapter tier+text 修复，手动冒烟验证通过 |
| `2c64db4` | 文档 | `docs/deploy-v07.md` 部署文档与用户指南 |

### 新文件统计

| 文件 | 行数 | 说明 |
|------|------|------|
| `kzocr/scheduler/registry.py` | 360 | E1 EngineRegistry + EngineStats + benchmark |
| `kzocr/scheduler/scheduler.py` | 230 | E2 EngineScheduler + Budget + 九步候选选择 |
| `kzocr/scheduler/verifier.py` | 360 | E3 GlyphVerifier + 5 检测器 |
| `kzocr/scheduler/orchestrator.py` | 440 | E4 OrchestrateBook + timeout + resume + DB |
| `kzocr/engine/toc.py` | 300 | F1 TOC 抽取（discover/parse/build/enrich） |
| `kzocr/storage/db.py` | 140 | F2 BookDB SQLite 管理器 |
| `kzocr/adapters/engine_runners.py` | 100 | E5 引擎适配器（Mock/BookPipeline/VLM） |
| `tests/test_scheduler.py` | 186 | E2 调度器测试 |
| `tests/test_scheduler_integration.py` | 133 | E1+E2 集成 |
| `tests/test_verifier.py` | 208 | E3 验证器测试 |
| `tests/test_orchestrator.py` | 310 | E4 编排 10 种路径 |
| `tests/test_toc.py` | 217 | F1 19 例测试 |
| `tests/test_db.py` | 130 | F2 9 例测试 |
| `tests/test_resume.py` | 120 | F3 6 例测试 |
| `tests/test_engine_runners.py` | 140 | E5 适配器测试 |
| `docs/deploy-v07.md` | 247 | 部署文档 |
| `docs/reviews/toc_plan_review_r1.md` | 202 | F1 多角色评审报告 |
| `docs/plans/f1-toc-plan-revised.md` | 190 | F1 修订版计划 |

### 投产方式

```bash
# 启用 v0.7 编排（旧签名保留，--use-v07 开关）
kzocr pipeline book.pdf --book-code TCM-001 --use-v07

# 环境变量模式
export KZOCR_USE_V07=1
kzocr pipeline book.pdf --book-code TCM-001
```

默认 `use_v07=False`，旧管道完全不受影响。

### 验证

| 测试组 | 用例数 | 覆盖内容 |
|--------|--------|----------|
| 全量 pytest | 396 | ruff clean，~15s |
| 手动冒烟 | ✅ | 2页 PDF → E4 编排 → E3 验证 → F2 DB 全链路通过 |

---

## v2026-07-10 — v0.7.1 集成测试 + 429 限流 + benchmark 表

> **4 commits, 5 files changed, 474 insertions, 396 tests passed**

### 日期：2026-07-10

### 新增/修改

| Commit | 模块 | 说明 |
|--------|------|------|
| `3f1e61b` | 测试 | `tests/test_integration.py` 10 例全链路集成测试（三级降级/DB/CLI/TOC） |
| `603148d` | CI | `test.yml` 补 pillow 依赖（PIL） |
| `b5645d0` | 适配 | 429 限流处理（`RateLimitedError` → `_rate_limited_until` → `select_candidates` 退避） |
| `b5645d0` | DB | `benchmark_results` 表 + `BookDB.write_benchmark()` |
| `b5645d0` | 编排 | `orchestrate_book` 书完成时自动写入 benchmark 汇总 |

### 采纳 traedocu V3.4 功能

| 功能 | traedocu 参考 | KZOCR 实现 |
|------|--------------|-----------|
| 429 限流退避 | `AdaptiveTokenBucket` 降速 | `except RateLimitedError` → 记录退避到期时间，`select_candidates` 排除冷却期引擎 |
| benchmark 汇总 | `benchmark_results` SQLite 表 | `write_benchmark()` 写入 engine/total_pages/error_rate/latency_p50/throughput |

---

### 说明
修复 GitHub Actions CI 持续失败的根因（两层问题叠加），并同步文档一致性。

| PR | 模块 | 说明 |
|----|------|------|
| #5 | CI | `test.yml` 第 35 行 `run: echo "Tests: ✅"` 在严格 YAML 下非法，改为单引号包裹含冒号命令，使 workflow 可被 GitHub 解析并创建 job |
| #6 | `kzocr/modelscope_pool.py` | `openai` 由顶层硬 `import` 改为可选导入（`try/except ImportError` → `OpenAI = None`），缺依赖时对应 provider 在初始化时自动禁用，消除 CI 最小环境下 `ImportError: No module named 'openai'` |
| #4 | 文档 | 修正 egress 路径过时记录（概览 §4.5 已于 PR #1 更正，SEC-2 标记已修复） |
| #3 | 文档 | 修正 CODEBUDDY.md 过时条目（`ProbeResult.keys` 已同步为 `dict[str, bool]`；round 计数 round1→round9） |
| #7 | 文档 | README 新增 CI 小节，记录上述修复与根因 |

修复后 CI 全绿：`lint` + `test`（Python 3.10 / 3.11 / 3.12）均 `success`，本地 268 测试通过。

---

## v2026-07-10 — v0.6 测试覆盖与项目基础设施

### 日期：2026-07-10

### 新增/修改

| 提交 | 模块 | 说明 |
|------|------|------|
| `5c813d1` | CI | GitHub Actions CI 工作流（3 Python 版本 + ruff lint） |
| `f6e2f7d` | 文档 | README（项目概述/快速开始/架构/配置参考） |
| `2aeed98` | 测试 | CLI 入口测试 23 例 |
| `ce0d544` | 配置 | 新增 `_safe_int` 安全类型解析 + config validation 15 测试 |
| `1b30562` | 测试 | modelscope_pool 测试 23 例 |
| `1a6a349` | 测试 | kHUB 客户端测试 22 例 |
| `0c5d88f` | 文档 | CONTRIBUTING.md + issue/PR templates |
| `a0a2a8a` | 测试 | 真实引擎 mock 测试 9 例（路由 + 内部） |
| `66ae7aa` | 版本 | pyproject.toml 0.2.0 → 0.6.0 |

**总计：268 测试全通过 ✅（~15s）**

---

## v2026-07-10 — v0.5 AMEND 异常处理体系改进实施完成

### 日期：2026-07-10 19:52 CST

实施提交（自 d6e4845 起，HEAD `1f52052`）：

| Commit | 模块 | 说明 |
|--------|------|------|
| `c4120cd` | D0+D1 | Config扩展 (`kzocr_output_dir`, `cache_ttl_seconds`) + errors.py (5异常类 + retry_with_policy) |
| `dd9b76f` | D2 | VLM主循环重试 (_process_vlm_page, 降DPI重试, failed_pages追踪) |
| `cc6f52a` | D4 | 层级异常检测 (HierarchyAnomaly + check_hierarchy_anomaly) |
| `1f52052` | D3 | VLM断点续跑缓存 (config_hash + TTL + KZOCR_CLEAR_CACHE=1) |
| — | 冲突-2 | 移除leakage.py L3日志标记（由D2取代） |

### 评审历程

| 轮次 | 时间 | 角色数 | 结果 |
|------|------|--------|------|
| round6 | 2026-07-10 | 5角色（架构/软件工程/测试/安全/领域） | APPROVED |
| round7 | 2026-07-10 | 5角色再评审 | APPROVED |
| round8 | 2026-07-10 | 6角色（+性能工程师） | APPROVED |
| round9 | 2026-07-10 | 7角色（+运维+产品经理） | APPROVED |
| round10 | 2026-07-10 | 7角色终签 | 全部APPROVED ✅ |

评审报告存档：`docs/reviews/2026-07-10-round{6,7,8,9,10}/`

### 新增测试

| 文件 | 用例数 | 覆盖内容 |
|------|--------|----------|
| `tests/test_config.py` | 6 | D0: 默认值、环境变量覆盖、类型校验 |
| `tests/test_errors.py` | 24 | D1: 异常继承、retry_with_policy、回调、backoff配置 |
| `tests/test_hierarchy.py` | 17 | D4: 邻居窗口、异常检测、严重度缩放 |
| `tests/test_vlm.py` | +16 | D2: 8重试测试 + D3: 8缓存测试 |

**总计：177 测试全通过 ✅（0.94s）**

### 新增/修改文件

| 文件 | 状态 | 行数 |
|------|------|------|
| `kzocr/config.py` | 修改 | +7 |
| `kzocr/engine/run.py` | 修改 | +199 |
| `kzocr/engine/types.py` | 修改 | +1 |
| `kzocr/engines/errors.py` | 新增 | 109 |
| `kzocr/engines/hierarchy.py` | 新增 | 134 |
| `kzocr/engines/leakage.py` | 修改 | -7（L3移除）|
| `kzocr/engines/__init__.py` | 修改 | +22 |
| `tests/test_config.py` | 新增 | 51 |
| `tests/test_errors.py` | 新增 | 217 |
| `tests/test_hierarchy.py` | 新增 | 125 |
| `tests/test_vlm.py` | 修改 | +535 |

---

## v2026-07-07 — 全链路打通

### 日期：2026-07-07 18:00 ~ 23:30 CST

#### KZOCR 仓库（`/home/keen/KZOCR`）

| 时间 | 改动 | 文件 | 说明 |
|------|------|------|------|
| 18:00 | 修复 cli ↔ engine 调用断点 | `cli.py`, `engine/run.py`, `config.py` | `write_book_to_zai` → `push_book_to_zai`；`Config.from_env()` → `load_config()`；pipeline/smoke 默认使用隔离 DB |
| 18:10 | 对齐 engine 到真实 BookPipeline 接口 | `engine/run.py` | `run_book` → `run_engine`，加 `book_code`/`config` 参数；`_run_real` 调用 `BookPipeline(config).process_book(pdf, book_id)`；新增 `_build_engine_config()` 从环境变量构造配置字典 |
| 18:20 | 补齐 engine_configs 结构 | `engine/run.py` | 传入完整的 `engine_configs`（paddleocr / shizhengpt / mineru / tesseract / cloud_llm） |
| 18:30 | 测试 & git 初始化 | `tests/test_pipeline.py`, `.gitignore` | 新增 4 个回归测试；忽略 `*.db` 运行时产物 |
| 18:40 | smoke 冒烟通过 ✅ | — | `python -m kzocr.cli smoke --skip-push` 全流程通过 |

#### kimi 引擎仓库（`/home/keen/kimi_agent_ocr/tcm_ocr_system_v1.1`）

| 时间 | 改动 | 文件 | 说明 |
|------|------|------|------|
| 19:20 | 修复 PaddleOCR 引擎 import | `tcm_ocr/pipeline/book_pipeline.py` | `_init_engines`: `tcm_ocr.ocr.paddle_engine.PaddleOCREngine` → `tcm_ocr.core.engines.paddleocr_adapter.PaddleOCRAdapter`（构造参数 `device`） |
| 19:20 | 修复 MinerU 引擎 import | `tcm_ocr/pipeline/book_pipeline.py` | `tcm_ocr.ocr.mineru_engine.MinerUEngine` → `tcm_ocr.core.engines.mineru_adapter.MinerUAdapter` |
| 19:20 | 修复 云端 LLM 引擎 import | `tcm_ocr/pipeline/book_pipeline.py` | `tcm_ocr.ocr.cloud_llm_engine.CloudLLMEngine` → `tcm_ocr.llm.cloud.cloud_llm.CloudLLMClient`（无参构造） |
| 19:20 | 补 page_pipeline 缺失 import | `tcm_ocr/pipeline/page_pipeline.py` | 添加 `import json`（`json` 未定义 bug） |
| 19:25 | 补 page_pipeline 缺失 datetime import | `tcm_ocr/pipeline/page_pipeline.py` | `import datetime` → `from datetime import datetime`（`module 'datetime' has no attribute 'now'` bug） |
| 19:30 | 修 engine.recognize 返回值解包 | `tcm_ocr/pipeline/page_pipeline.py` | 适配器返回 `str`，原代码要求 `(text, confidence)` tuple，改为兼容两者 |
| 19:30 | 防止本地 LLM 模型在线下载 | `tcm_ocr/pipeline/deliverables.py` | `_call_local_llm` 增加模型目录存在性检查，不存在则立即报错，不触发 HuggingFace 下载 |
| 19:45 | 修复 PaddleOCR 初始化参数 | `tcm_ocr/core/engines/paddleocr_adapter.py` | 去掉 `show_log`、`use_gpu`、`gpu_id`、`enable_mkldnn`、`use_angle_cls`（PaddleOCR v3.7 不支持）；改用 `paddle.set_device()` + `PaddleOCR(lang='ch')` |
| 20:00 | 适配 PaddleOCR v3.7 API | `tcm_ocr/core/engines/paddleocr_adapter.py` | `ocr.ocr(img, det=False, cls=False)` → `ocr.predict(img, use_doc_orientation_classify=False, use_doc_unwarping=False, use_textline_orientation=False, text_det_limit_side_len=0)` |
| 20:00 | 限制裁剪行宽度防止 OneDNN 崩溃 | `tcm_ocr/core/engines/paddleocr_adapter.py` | 添加 `_max_rec_width = 2048`，对超宽行等比缩放 |
| 20:10 | CloudLLMClient 兼容 page_pipeline 调用 | `tcm_ocr/llm/cloud/cloud_llm.py` | 添加 `generate(prompt, max_tokens, temperature)` 方法；`_call_glm` 中 `GLM_MODEL` 环境变量覆写模型名 |
| 20:15 | 交付物内容回退机制 | `tcm_ocr/pipeline/deliverables.py` | `_build_final_doc_from_book_db`: `content_node` 为空时从 `proofread_record` 读行级文本 |
| 20:15 | 修复 image_path 列名 | `tcm_ocr/pipeline/deliverables.py` | `_load_disputed_lines` SQL 中删除不存在的 `image_path` 列 |
| 20:15 | 修复 formula_ingredient Row.get | `tcm_ocr/pipeline/deliverables.py` | `ing_cursor.fetchall()` 的 `r.get()` → 先 `dict(r)` 再 `.get()` |
| 20:20 | 云端 LLM 模型名环境变量 | `tcm_ocr/pipeline/deliverables.py` | `_call_cloud_llm` 中 hardcode `"qwen-max"` → `os.environ.get("TCM_OCR_CLOUD_LLM_MODEL", "qwen-max")` |

#### 运行验证记录

| 时间 | 验证 | 结果 |
|------|------|------|
| 18:50 | `kzocr smoke --skip-push`（mock 全链路） | ✅ 通过 |
| 19:28 | 真实引擎首次调通（样本 PDF） | ✅ 导入正确，PaddleOCR 识别 3 行 |
| 20:20 | 本地 LLM 快速降级验证 | ✅ body.md 写出（空内容，因无 LLM 争议未解决） |
| 20:29 | 云端 LLM（agnes-2.0-flash）HTTP 200 | ✅ 云端 LLM 连接成功 |
| 21:00 | 真实 TCM 书页 `page_0969.webp` 识别 | ✅ body.md 有内容（81 行原始 OCR 文本） |
| 21:30 | 内容回退 proofread_record → body.md | ✅ 49 行收录，39 行入 final_doc content |

### 后续验证（2026-07-07 23:35）

| 项目 | 结果 | 原因 |
|------|------|------|
| MinerU v3（已安装 `mineru 3.2.3`） | ❌ 无法运行 | 适配器 import 旧包名 `magic_pdf`，但 MinerU v3 改用 `mineru` 且无 GPU；layout 模型需要 HuggingFace 下载，当前环境无 GPU + 无 HF token |
| Tesseract | ❌ 已从 `book_pipeline._init_engines` 删除 | 项目 `SPEC.md` 中该引擎不存在，是重构前遗留的死代码 |

### 修正（2026-07-08 04:55）

| 项目 | 变更 |
|------|------|
| **PP-OCRv6 速度** | 发现 4 分钟/页是 PaddleOCR v3.7 `predict()` 错误用法所致。改用 **MinerU 的 PytorchPaddleOCR** 后端，`ocr(img, det=False)` 每行 **0.05 秒**，一页 50 行约 **2.5 秒**（非 4 分钟） |
| **paddleocr_adapter.py** | 重写 `_init_engine`：优先走 MinerU shared model pool（`custom_model_init`），降级走 standalone PaddleOCR；`_init_standalone` 备用；`recognize` 改用 `PytorchPaddleOCR.ocr()` 解析格式 |

### 云端 API 配置（2026-07-08 04:58）

硅基流动 API key 已测试可用。云端 API 配置速查见下文。

### ModelScope（`https://api-inference.modelscope.cn/v1`）

| 模型 ID | 类型 | 每日限额 | 状态 |
|---------|------|---------|------|
| `ZhipuAI/GLM-5.2` | 文本 | 45次 | ✅ | 
| `ZhipuAI/GLM-4.7-Flash` | 文本 | 45次 | ✅ |
| `ZhipuAI/GLM-4.7:DashScope` | 文本 | 45次 | ✅ |
| `Qwen/Qwen3.5-35B-A3B` | 文本 | 45次 | ✅ |
| `Qwen/Qwen3.5-27B` | 文本 | 45次 | ✅ |
| `Qwen/Qwen3.5-122B-A10B` | 文本 | 45次 | ✅ |
| `Qwen/Qwen3.5-397B-A17B` | 文本 | 45次 | ✅ |
| `deepseek-ai/DeepSeek-V4-Pro` | 推理 | 45次 | ✅ |
| `deepseek-ai/DeepSeek-V4-Flash` | 推理 | 45次 | ✅ |
| `moonshotai/Kimi-K2.6:DashScope` | 文本 | 45次 | ✅ |
| Key: `ms-40d78a2b-f786-433a-92e3-8e5f4049f602`

**自动故障转移客户端**: `kzocr/modelscope_pool.py` — `ModelScopePool` 类，10 个模型逐个重试，失败自动切换下一个。 | | |
| 说明 | 注册地址：`modelscope.cn/my/accountsettings`，完成实名后在 `api-inference.modelscope.cn/v1` 使用 |

| 平台 | 端点 | 模型名 | Key 状态 |
|------|------|--------|---------|
| **Ofox AI** | `https://api.ofox.io/v1` | `z-ai/glm-4.7-flash:free` | ✅ `ofox.ai` 国内受限，换 `ofox.io` 后连通（429 限流，key 有效） |
| 说明 | 订阅帖子提到为免费第三方聚合，需解决网络访问后再测 |

### RapidOCR 适配器（2026-07-08 06:50）

| 项目 | 说明 |
|------|------|
| 新文件 | `tcm_ocr/core/engines/rapidocr_adapter.py` |
| 引擎注册 | `book_pipeline._init_engines` + `KZOCR _build_engine_config` |
| 模型 | ONNX PP-OCRv4（自动缓存） |

### UniRec 适配器（2026-07-08 06:55）

| 项目 | 说明 |
|------|------|
| 新文件 | `tcm_ocr/core/engines/unirec_adapter.py` |
| 模型 | `unirec_encoder.onnx` + `unirec_decoder.onnx`（`/home/keen/unirec_0_1b_onnx/`） |
| 状态 | 结构正确，推理耗时 2.8s/行；预处理需调参（当前输出为语言先验幻觉，图片特征未正确传入） |

### ShizhenGPT-7B-VL 适配器（2026-07-08 09:05）

| 项目 | 说明 |
|------|------|
| 新文件 | `tcm_ocr/core/engines/shizhengpt_adapter.py` |
| 模型 | `ShizhenGPT-7B-VL.i1-Q4_K_M.gguf` (4.4GB) + `mmproj` (817MB) |
| 默认 | 禁用（`enabled: False`） |
| 端口 | 18083 |

### MinerU v3 适配器接入

| 项目 | 说明 |
|------|------|
| 文件 | `tcm_ocr/core/engines/mineru_adapter.py`（重写） |
| Layout | 全页 PytorchPaddleOCR 检测，45 blocks，5s/页（CPU） |
| OCR 识别 | 共享 MinerU 模型池，0.076s/行 |
| KZOCR 配置 | 默认启用 (`enabled: True`) |

| 项目 | 说明 |
|------|------|
| 新文件 | `tcm_ocr/core/engines/paddleocr_vl16_adapter.py` |
| 后端 | llama-server（`/home/keen/llama.cpp/build/bin/llama-server`） |
| 模型 | `PaddleOCR-VL-1.6-GGUF.gguf` (893MB) + `mmproj` (841MB) |
| 默认 | 禁用（`enabled: False`），需显式启用 |
| 启用方式 | 环境变量 `KZOCR_PADDLE_VL16_ENABLED=1` + `engine_configs` |

---

## 配置速查

```bash
# === 最小运行（mock）===
kzocr smoke --skip-push

# === 真实 PaddleOCR（CPU，~4 分/页）===
KZOCR_PADDLE_GPU=0 KZOCR_USE_MOCK=0 kzocr pipeline <pdf>

# === 真实 PaddleOCR + 云端 LLM 校对 ===
KZOCR_PADDLE_GPU=0 KZOCR_USE_MOCK=0 \
  KZOCR_LLM_ENABLED=1 \
  GLM_API_KEY=sk-xxx \
  GLM_API_BASE=https://your-api/v1 \
  GLM_MODEL=agnes-2.0-flash \
  kzocr pipeline <pdf>

# === 环境变量参考 ===
KZOCR_PADDLE_GPU        # 1=GPU，0=CPU（默认 0）
KZOCR_ENGINE_LIB_DIR    # 引擎工作目录（默认 /home/keen/kzocr_engine_lib）
KZOCR_ENGINE_OUTPUT_DIR # 交付物输出目录
KZOCR_PG_DSN            # PostgreSQL DSN（空则禁用）
KZOCR_LLM_ENABLED       # 1 启用 LLM 校对
KZOCR_LLM_API_KEY       # 云端 LLM API Key
KZOCR_LLM_BASE_URL      # 云端 LLM Base URL
KZOCR_LLM_MODEL         # 云端 LLM 模型名
GLM_API_KEY / GLM_API_BASE / GLM_MODEL / CLOUD_LLM_PRIMARY  # 引擎内部 LLM 配置
TCM_OCR_CLOUD_LLM_API_KEY / TCM_OCR_CLOUD_LLM_BASE_URL / TCM_OCR_CLOUD_LLM_MODEL  # 交付物 LLM 配置

## 云端 API 配置

### 硅基流动（`https://api.siliconflow.cn/v1`）

| 类型 | 模型名（严格大小写） | 用途 |
|------|---------------------|------|
| 视觉 VLM | `Qwen/Qwen3.5-4B` | 通用图文理解 |
| 文档 OCR | `deepseek-ai/DeepSeek-OCR` | 文字提取 |
| 文档 OCR | `PaddlePaddle/PaddleOCR-VL-1.5` | 飞桨文档 VL |
| 纯文本 | `THUDM/GLM-4-9B-0414` | 文字校对 |
| 纯文本 | `THUDM/GLM-Z1-9B-0414` | 文字校对 |
| 纯文本 | `Qwen/Qwen2.5-7B-Instruct` | 文字校对 |
| 纯文本 | `Qwen/Qwen3-8B` | 文字校对 |
| 纯文本 | `deepseek-ai/DeepSeek-R1-0528-Qwen3-8B` | 推理强化 |
| 纯文本 | `PaddlePaddle/PaddleOCR-VL-1.5` | 文字校对 |

### z.ai（`https://api.z.ai/api/paas/v4`，模型名全小写）

| 类型 | 模型名 | 上下文 | 用途 | Key 状态 |
|------|--------|--------|------|---------|
| 视觉 VLM | `glm-4.6v-flash` | — | 免费图文识别 | ✅ 已配置（密钥经环境变量注入，不入库） |
| 纯文本 | `glm-4.7-flash` | — | 免费校对 | ✅ 同上 |
| 纯文本 | `glm-4.5-flash` | 128K | 免费校对 | ✅ 同上 |

### 智谱主站（`https://open.bigmodel.cn/api/paas/v4`）

| 类型 | 模型名（大写标准） | 用途 | Key 状态 |
|------|------------------|------|---------|
| 视觉 VLM | `GLM-4.6V-Flash` | 图文识别 | ✅ 已测通（密钥经环境变量注入，不入库） |
| 纯文本 | `GLM-4.7-Flash` | 文本校对 | ✅ 同上 |
```
