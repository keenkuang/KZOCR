# v0.7 自适应引擎编排系统 — 部署与用户指南

> v0.7 是默认启用路径，无需额外标志。v0.6 遗产路由已移除。

---

## 1. 快速启用

```bash
# 标准运行（v0.7 默认启用）
kzocr pipeline book.pdf --book-code MY-BOOK

# 冒烟测试（验证编排链路是否正常）
kzocr smoke --skip-push
```

### 效果对比

| 维度 | v0.6（遗产，已移除） | v0.7（当前默认） |
|------|---------------------|-------------------|
| 引擎选择 | 硬编码 if-else | EngineRegistry + 贝叶斯评分择优 |
| 错误兜底 | 单引擎 → mock | Tier1→Tier2→Tier3 三级降级 |
| 验证 | 无 | GlyphVerifier（5 检测器链） |
| TOC 抽取 | 无 | 文本式自动目录树 |
| 持久化 | 无 | SQLite page_progress + benchmark_results + NDJSON |
| 断点续跑 | 不支持 | `--resume` / `--retry-failed`（EngineOverrides） |
| 429 限流 | 无 | 自动退避冷却 60s |
| 自适应调速 | 无 | 滚动窗口混合评分 + backoff |

---

## 2. 生产配置指南

### 2.1 环境变量速查

| 变量 | 默认 | 说明 |
|------|------|------|
| `KZOCR_USE_MOCK` | 0 | 启用 mock 引擎（冒烟/调试） |
| `KZOCR_DB_DIR` | `$PWD/db/` | 逐页进度与 benchmark 数据库目录 |
| `KZOCR_SENSENOVA_API_KEY` | — | SenseNova 云端 VLM 密钥（Tier2） |
| `KZOCR_ALLOW_CLOUD_VISION` | 0 | 允许发送页面图像到云端 |
| `KIMI_ENGINE_DIR` | — | kimi BookPipeline 引擎目录（Tier1） |
| `KZOCR_ENGINE_LIB_DIR` | — | 引擎工作目录（书籍库/交付物） |
| `KZOCR_LLM_ENABLED` | 0 | 启用云端 LLM 校对 |
| `KZOCR_LLM_API_KEY` | — | 云端 LLM API Key |
| `KZOCR_MAX_PAGES` | 50 | 单书最大处理页数 |
| `KZOCR_TOTAL_TIMEOUT` | 7200 | 全书总超时（秒） |
| `KZOCR_MAX_TIME_PER_PAGE` | 120000 | 单页最大耗时（毫秒） |
| `KZOCR_REQUIRE_REAL` | 0 | 真实引擎失败时抛异常（不降级 mock） |
| `KZOCR_CACHE_TTL` | 86400 | VLM 缓存 TTL（秒） |

### 2.2 引擎注册条件

`_init_v07_registry()` 根据以下条件自动注册引擎：

| 引擎 | Tier | 注册条件 |
|------|------|----------|
| Mock | 1 | `use_mock=True`（仅调试/冒烟） |
| kimi BookPipeline | 1 | `KIMI_ENGINE_DIR` 已配置且目录存在 |
| SenseNova VLM | 2 | `KZOCR_SENSENOVA_API_KEY` 非空 |
| PaddleOCR-VL | 3 | VLM 配置存在（`KZOCR_VLM_HOST`/`KZOCR_VLM_PORT`） |

无可用引擎时，编排循环仍会执行但所有页进入 HumanGate。
生产环境至少应配置一个 Tier1 书级引擎。

### 2.3 数据库与持久化

| 数据 | 位置 | 说明 |
|------|------|------|
| 逐页进度 | `$KZOCR_DB_DIR/{book_code}.db` | `page_progress` 表（OCR→验证→导入三态机）+ `hierarchy_anomaly` + `benchmark_results` |
| 引擎基准 | `$PWD/benchmarks/{engine}.ndjson` | 每引擎独立 NDJSON，贝叶斯评分使用 |
| 调用 Trace | `$PWD/trace/{book_code}_trace.jsonl` | 逐引擎调用记录（含 detector_chain） |

#### 数据库文件位置

```python
# 默认路径：$PWD/db/
# 可通过环境变量覆盖：
export KZOCR_DB_DIR=/data/kzocr/db
```

### 2.4 自适应调度参数

`EngineOverrides` 默认值可在代码中覆盖，生产环境建议按需调整：

| 参数 | 默认 | 说明 |
|------|------|------|
| `backoff_threshold_ms` | 30000 | 引擎即时延迟 > 30s 时暂停调度 |
| `backoff_fail_rate` | 0.5 | 引擎即时失败率 > 50% 时暂停调度 |
| `rate_limited_until` | `{}` | 429 限流退避（自动管理，60s 默认退避） |

当前 KZOCR 串行逐页调用各候选引擎，不涉及并发 Worker 调谐。
云端引擎的 429 限流通过退避机制自动处理（捕获 `RateLimitedError` → 60s 冷却）。

### 2.5 性能参考

| 组件 | 预算 | 说明 |
|------|------|------|
| `select_candidates` | <10ms | 贝叶斯评分排序 + backoff 过滤 |
| `GlyphVerifier.verify` | <50ms | 5 检测器串行（资源已预热） |
| BookDB 写入（每页） | <20ms | WAL 模式 + UPSERT 幂等 |
| TOC extraction | <100ms / 10%页 | 仅首次构建 |
| 编排总开销 | <10% total | 相对引擎调用时间可忽略 |
| benchmark 写入 | <50ms | 书完成时一次性写入 |

### 2.6 部署 CheckList

```bash
# 1. 安装依赖
pip install PyMuPDF numpy pillow

# 2. 配置数据库目录
mkdir -p /data/kzocr/db
export KZOCR_DB_DIR=/data/kzocr/db

# 3. 配置引擎（至少一个）
export KIMI_ENGINE_DIR=/opt/kimi/tcm_ocr

# 4. 可选：云端 API
export KZOCR_SENSENOVA_API_KEY=sk-xxxx
export KZOCR_ALLOW_CLOUD_VISION=1

# 5. 冒烟验证
kzocr smoke --skip-push

# 6. 真实运行
kzocr pipeline /data/books/book.pdf --book-code TCM001
```

### 2.7 故障排查

| 现象 | 检查项 |
|------|--------|
| 所有页 HumanGate | `_init_v07_registry` 日志确认引擎已注册；`KIMI_ENGINE_DIR`/`KZOCR_SENSENOVA_API_KEY` 是否配置 |
| benchmark 未写入 | `KZOCR_DB_DIR` 是否可写；`book_code` 是否非空 |
| DB 文件未生成 | E4 `BookDB` 初始化日志；`config.db_dir` 是否可访问 |
| TOC 未提取 | enrich 被 `logger.warning` 捕获后安全跳过（不影响主流程） |
| 429 限流频繁 | 检查云端 API 限额；`backoff_threshold_ms` 和 `rate_limited_until` 日志 |

### 2.8 升级至 v0.7

v0.7 默认启用，无附加配置变更。旧 API 签名 `run_engine(pdf_path, book_code, config)` 保持不变。
v0.6 遗产路径已移除，全部流量走编排系统。

配置速查：
```bash
# === 最小运行（mock）===
kzocr smoke --skip-push

# === 真实引擎 ===
KZOCR_USE_MOCK=0 kzocr pipeline book.pdf --book-code TCM001
```

---

## 3. 架构概览

```
run_engine(pdf_path)                          # 入口，签名未变
   │
   ├── cfg.use_v07=True（默认）──→ EngineRegistry    # E1: 注册可用引擎
              │                ProbeEngines()
              │                EngineScheduler   # E2: 九步选优
              │                GlyphVerifier     # E3: 5 检测器
              │                └── ToxinDoseDetector (药量安全)
              │                └── LeakageDetector (跨页泄漏)
              │                └── CharCountSpikeDetector (字符尖峰)
              │                └── ConfusionSetDetector (形似混淆)
              │                └── TermKBMatcher (术语库)
              │
              └──→ OrchestrateBook             # E4: 主循环
                     ├── Tier1: 书级引擎（kimi BookPipeline）
                     ├── Tier2: 云端 VLM（SenseNova）
                     ├── Tier3: 本地 LLM
                     ├── HumanGate（全部失败→回退）
                     └── BookDB 写入（page_progress 三态机）  # F2
                     └── TOC enrich（从文本重建目录树）      # F1
```

---

## 3. 引擎适配与 Tier 系统

### 3.1 引擎映射

| 引擎 | Tier | 类型 | 协议 | 条件 |
|------|------|------|------|------|
| Mock | 1 | book | `run_book` | `use_mock=True`（仅冒烟） |
| kimi Pipeline | 1 | book | `run_book` | `KIMI_ENGINE_DIR` 已配置 |
| SenseNova | 2 | page | `run_page` | `KZOCR_SENSENOVA_API_KEY` 已设置 |
| PaddleOCR-VL | 3 | page | `run_page` | 见 VLM 配置 |

引擎注册由 `_init_v07_registry()` 自动完成，基于 Config 字段判断可用性。

### 3.2 降级路径

```
Tier1 (书级) ─→ 成功 → PASS/RARE → 采纳结果
    │失败
    ↓
Tier2 (云端 VLM) ─→ 成功 → 采纳
    │失败
    ↓
Tier3 (本地 LLM) ─→ 成功 → 采纳
    │失败
    ↓
HumanGate（失败页记录到 BookResult.failed_pages）
```

### 3.3 自适应调度

v0.7 调度器（F3）基于 **滚动窗口**（最近 100 次调用）做调速：

- **混合评分**：70% 长期贝叶斯 + 30% 近期指标
- **Backoff**：引擎即时延迟 > `backoff_threshold_ms`(30s) 或
  即时失败率 > `backoff_fail_rate`(0.5) 时暂停调度
- **冷却恢复**：下一轮 select_candidates 时重新评估

可通过 `EngineOverrides` 调整：

```python
EngineOverrides(
    backoff_threshold_ms=10000,   # 10 秒
    backoff_fail_rate=0.3,        # 30% 失败率
    resume=True,                  # 断点续跑
    retry_failed=True,            # 仅重跑失败页
)
```

---

## 4. 配置参考

### 4.1 Config（kzocr/config.py）

| 字段 | 类型 | 默认 | 环境变量 | 说明 |
|------|------|------|----------|------|
| `use_v07` | bool | True | —（默认启用） | v0.7 编排（遗产路径已移除） |
| `use_mock` | bool | False | KZOCR_USE_MOCK | 使用 mock 引擎 |
| `kimi_engine_dir` | str | "" | KIMI_ENGINE_DIR | kimi BookPipeline 目录 |
| `sensenova_api_key` | str | "" | KZOCR_SENSENOVA_API_KEY | SenseNova 云端 API |
| `allow_cloud_vision` | bool | False | KZOCR_ALLOW_CLOUD_VISION | 允许发送图像到云端 |

### 4.2 数据库路径

| 数据 | 默认路径 | 可配置 |
|------|----------|--------|
| page_progress (`{book_code}.db`) | `$PWD/db/` | `KZOCR_DB_DIR` |
| benchmark NDJSON (`{engine}.ndjson`) | `$PWD/benchmarks/` | `EngineRegistry(benchmark_dir=...)` |
| trace (`{book_code}_trace.jsonl`) | `$PWD/trace/` | `config.trace_dir` |

---

## 5. 数据库 Schema

v0.7 为每本书创建独立的 SQLite 数据库，位于 `$KZOCR_DB_DIR/{book_code}.db`。

### 5.1 page_progress — 逐页进度追踪

```sql
CREATE TABLE page_progress (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    page_num        INTEGER NOT NULL UNIQUE,
    char_count      INTEGER DEFAULT 0,
    ocr_status      TEXT DEFAULT 'pending',           -- pending / processing / success / failed / skipped
    ocr_attempts    INTEGER DEFAULT 0,               -- 重试次数
    ocr_elapsed_ms  INTEGER DEFAULT 0,               -- OCR 耗时
    ocr_error       TEXT DEFAULT '',
    verify_status   TEXT DEFAULT 'PENDING',           -- PENDING / PASS / RARE / UNCERTAIN / FAIL / UNKNOWN / SKIPPED
    verify_details  TEXT DEFAULT '',
    import_status   TEXT DEFAULT 'pending',           -- pending / imported / failed / skipped
    import_count    INTEGER DEFAULT 0,
    import_error    TEXT DEFAULT '',
    engine_label    TEXT DEFAULT '',
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now'))
);
```

### 5.2 hierarchy_anomaly — 验证异常（校对工单）

```sql
CREATE TABLE hierarchy_anomaly (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    page_num        INTEGER NOT NULL,
    verdict_status  TEXT NOT NULL,                    -- FAIL / UNKNOWN / UNCERTAIN
    detector_chain  TEXT DEFAULT '',
    details         TEXT DEFAULT '',
    resolution      TEXT DEFAULT 'pending',           -- pending / confirmed / fixed / wontfix
    note            TEXT DEFAULT '',
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now'))
);
```

---

## 6. 部署清单

### 6.1 生产部署步骤

```bash
# 1. 安装/升级 KZOCR
pip install -e .

# 2. 配置 kimi 引擎（如使用书级引擎）
export KIMI_ENGINE_DIR=/path/to/tcm_ocr

# 3. 配置云端 API（如启用 Tier2）
export KZOCR_SENSENOVA_API_KEY=sk-xxxx

# 4. 运行管道
kzocr pipeline /path/to/book.pdf --book-code TCM001
```

### 6.2 验证 checklist

- [ ] `kzocr smoke --skip-push` — 基础冒烟通过
- [ ] `kzocr pipeline <real.pdf> --book-code TEST` — 真实 PDF 正常处理
- [ ] `$KZOCR_DB_DIR/{book_code}.db` 文件已生成，包含 page_progress 记录
- [ ] `$PWD/trace/{book_code}_trace.jsonl` 文件已生成
- [ ] 失败页自动降级（可故意设置无效 API key 验证 Tier1→Tier2→Tier3 链）

### 6.3 故障排查

| 现象 | 检查项 |
|------|--------|
| 未走编排路径 | 本地开发环境无 kimi 引擎？mock 模式？检查 `_init_v07_registry` 日志 |
| 引擎未被注册 | 对应 API key/env 是否设置；`_init_v07_registry` 日志 |
| 所有页失败 | tier1 engine 是否正确；`run_book` 是否返回 pages |
| verify 全 PASS 但预期应抓问题 | GlyphVerifier 资源文件（`kzocr/resources/*.json`）是否存在 |
| DB 未写入 | `KZOCR_DB_DIR` 是否可写；`book_code` 是否非空 |

---

## 7. 回退方案

v0.7 是默认路径，无需任何标志。如有部署中的具体问题，请随时提出。
