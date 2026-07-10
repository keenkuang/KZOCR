# v0.7 自适应引擎编排系统 — 部署与用户指南

> 从 v0.6.1 升级到 v0.7 无需配置变更，新编排系统通过 `--use-v07` 开关启用，
> 默认保持向后兼容。

---

## 1. 快速启用

```bash
# CLI 模式（推荐试运行）
kzocr pipeline book.pdf --book-code MY-BOOK --use-v07

# 冒烟测试（验证编排链路是否正常）
kzocr smoke --skip-push --use-v07

# 环境变量模式（全局生效）
export KZOCR_USE_V07=1
kzocr pipeline book.pdf --book-code MY-BOOK
```

### 效果对比

| 维度 | v0.6 (默认) | v0.7 (--use-v07) |
|------|------------|-------------------|
| 引擎选择 | 硬编码 if-else | EngineRegistry + 贝叶斯评分择优 |
| 错误兜底 | 单引擎 → mock | Tier1→Tier2→Tier3 三级降级 |
| 验证 | 无 | GlyphVerifier（5 检测器链） |
| TOC 抽取 | 无 | 文本式自动目录树 |
| 持久化 | 无 | SQLite page_progress + benchmark NDJSON |
| 断点续跑 | 不支持 | --resume / --retry-failed |

---

## 2. 架构概览

```
run_engine(pdf_path)                          # 入口，签名未变
   │
   ├── cfg.use_v07=False ──→ 旧路径（_run_real / _run_vlm）
   │
   └── cfg.use_v07=True ──→ EngineRegistry    # E1: 注册可用引擎
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
| `use_v07` | bool | False | KZOCR_USE_V07 | 启用 v0.7 编排 |
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

# 4. 启用 v0.7
export KZOCR_USE_V07=1

# 5. 运行管道
kzocr pipeline /path/to/book.pdf --book-code TCM001
```

### 6.2 验证 checklist

- [ ] `kzocr smoke --skip-push` — 基础冒烟通过
- [ ] `kzocr smoke --skip-push --use-v07` — v07 编排冒烟通过
- [ ] `kzocr pipeline <real.pdf> --book-code TEST --use-v07` — 真实 PDF 正常处理
- [ ] `$KZOCR_DB_DIR/{book_code}.db` 文件已生成，包含 page_progress 记录
- [ ] `$PWD/trace/{book_code}_trace.jsonl` 文件已生成
- [ ] 失败页自动降级（可故意设置无效 API key 验证 Tier1→Tier2→Tier3 链）

### 6.3 故障排查

| 现象 | 检查项 |
|------|--------|
| 未走 v07 路径 | `cfg.use_v07` 是否为 True；`--use-v07` 是否传递 |
| 引擎未被注册 | 对应 API key/env 是否设置；`_init_v07_registry` 日志 |
| 所有页失败 | tier1 engine 是否正确；`run_book` 是否返回 pages |
| verify 全 PASS 但预期应抓问题 | GlyphVerifier 资源文件（`kzocr/resources/*.json`）是否存在 |
| DB 未写入 | `KZOCR_DB_DIR` 是否可写；`book_code` 是否非空 |

---

## 7. 回退方案

出问题时，关闭 `--use-v07`（或取消设置 `KZOCR_USE_V07`）即可回到 v0.6 旧管道，
所有旧功能不受影响。

```bash
# 临时回退
kzocr pipeline book.pdf --book-code TEST   # 不加 --use-v07

# 或移除环境变量
unset KZOCR_USE_V07
```

---

## 8. 性能参考

| 组件 | 单次耗时预算 | 说明 |
|------|-------------|------|
| `select_candidates` | <10ms | 贝叶斯评分排序 |
| `GlyphVerifier.verify` | <50ms | 5 检测器串行（资源已预热） |
| `BookDB.init_page` | <5ms | UPSERT 幂等 |
| BookDB write per page | <20ms | WAL 模式 + 批量 commit |
| TOC extraction | <100ms/10%页 | 仅首次构建 |
| 编排总开销 | <10% total | 相对引擎调用时间可忽略 |

v0.7 已合并到 `main`。新功能可通过 `kzocr pipeline --use-v07` 体验，旧管道完全不受影响。如有部署中的具体问题，请随时提出。
