# KZOCR 架构总览

## 入口

```
kzocr/cli.py           CLI 入口（pipeline / export / push / review / benchmark / web 等）
kzocr/web/app.py       FastAPI Web 管理面板（独立进程）
```

## 模块依赖关系

```
CLI ──→ engine/run.py ──→ adapters/engine_runners.py ──→ scheduler/ ──→ storage/db.py
  │                            │                                               │
  │                            │                                               │
  ├──→ doc/                    └── engine/types.py (核心数据结构)                │
  │    ├── zai.py              └── adapters/ (BookPipelineAdapter, ...)         │
  │    ├── export.py                                                           │
  │    └── proofread.py                                                        │
  │                                                                             │
  ├──→ khub/client.py                                                          │
  │                                                                             │
  └──→ scheduler/ ────────────────────────────────────────────────────────────┘
       ├── orchestrator.py (编排主循环)
       ├── registry.py (引擎注册)      ──→ engines/ (错误/限流/原子写)
       ├── scheduler.py (调度器)
       ├── verifier.py (字形验证 + VL 仲裁)
       ├── cross_align.py (跨引擎分歧对齐)
       ├── concurrency.py (并发控制)
       └── review_manifest.py (校对清单 + 分歧高亮/bbox 可视化)
```

## 层职责

| 层 | 模块 | 职责 |
|----|------|------|
| **引擎层** | `engine/` | OCR 引擎运行、核心类型 `BookResult`/`PageResult`/`LineResult` |
| **适配器层** | `adapters/` | 引擎适配器（BookPipelineAdapter、MockAdapter 等） |
| **编排层** | `scheduler/` | v0.7 自适应引擎编排（注册→调度→验证→仲裁→分歧对齐） |
| **存储层** | `storage/` | BookDB（每书一个 SQLite，系统 of record） |
| **文档层** | `doc/` | zai 校对台写入/导出 Markdown+JSON/校对导入/冻结 |
| **发布层** | `khub/` | 文档 HTTP 推送至 kHUB |
| **Web 面板** | `web/` | FastAPI 管理界面 |
| **共享基础设施** | `engines/` | 错误类型、限流器、原子写、出站安全 |
| **配置** | `config.py` | 全局配置（环境变量 + 默认值） |
| **适配器兼容层** | `adapter/` | 旧 `to_zai_prisma.py` 的向后兼容委托层（已迁移至 `doc/`） |
| **分析** | `analysis/` | 配方解析、质量质检 |
| **平行栈** | `tcm_ocr/` | TCM-OCR 子系统（知识抽取/数据库/管线，独立迭代） |
