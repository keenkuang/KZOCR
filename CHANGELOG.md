# KZOCR 变更日志

> 文档版本：v2026-07-18T20:00+08
> 最后更新：2026-07-18 20:00 CST

---

## v2026-07-18 — v0.20 DB 分层 + 字符级 bbox + 跨引擎分歧门控 + 性能基准

> **615 tests**（全量通过）；真实引擎（PaddleOCR / RapidOCR / GLM-4V-Flash）已接入并做性能基准

| 模块 | 说明 |
|------|------|
| DB 分层 | BookDB 新增 `book`/`page`/`line` 内容表（含 `char_boxes` JSON）；`to_zai_prisma` 重构为「打包导出 / 导入校对包」闭环；旧 `custom.db` 冻结；tcm_ocr 平行栈经 `BookPipelineAdapter` 对接主线 `kzocr.engine`，产出主线 `BookResult` 并走主线 `BookDB`/导出闭环 |
| 字符级 bbox | `AdapterPageResult.char_boxes` 落地（`return_word_box=True`），真实引擎单页 801 逐字框落库读回一致；开销基准证明开启逐字框为**零成本**（<1%） |
| conf≤0.90 门控 | RapidOCR 适配器置信度传递修复（此前写死 0.7 且丢弃 score）；低置信度 PASS 页挂起待人工复核（`record_anomaly(CONF_LOW)`），`KZOCR_CONF_GATE` 可配 |
| 性能基准 | DPI 72 vs 150、进程级单例稳定性、引擎倍速（RapidOCR 单页≈PaddleOCR 1/13）结论归档 `docs/benchmark/engine-perf.md`；char_boxes 零成本基准补跑 |
| 引擎适配器 | `PaddleOCRAdapter` 迁移 `predict`（弃用 `.ocr`）+ 新增弃用告警回归测试（静态源码检查 + 真实引擎动态断言） |
| orchestrator | 修复全路径卡顿（`run_book` 无视 `max_pages` 全本扫描）；e2e 扩面对齐 orchestrator 版心裁切管线，分歧数字严格可比（5 本古籍分歧/页 7.8–15.6） |
| 视觉回看 | GLM-4V-Flash 接入 + 真实端到端验证（图形校验 FAIL/PASS 判定合理，确认模型看图非盲答） |

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
