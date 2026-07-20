# KZOCR

**中医古籍 OCR 编排系统** — 从 PDF 到结构化数据的全自动流水线。

![Test Status](https://img.shields.io/github/actions/workflow/status/keenkuang/KZOCR/test.yml?branch=main)
![Python Version](https://img.shields.io/badge/python-%3E%3D3.10-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Version](https://img.shields.io/badge/version-0.21.0-orange)
![Tests](https://img.shields.io/badge/tests-820-success)

---

## 功能一览

| 功能 | 说明 |
|------|------|
| **引擎编排** | Tier1 书级 → Tier2 云端 VLM → Tier3 本地 LLM 三级降级 |
| **字形验证** | 5 检测器链（毒性剂量/跨页泄漏/字符尖峰/形似混淆/术语库） |
| **TOC 抽取** | 纯文本方案，自动识别目录页并构建 1-5 层章节树 |
| **方剂解析** | 9 字段分割 + 药材解析（含"各X克"句式）+ 加减解析 |
| **质量检测** | 字段完整性/剂量合理性/药名可疑自动检查 |
| **Web 登记** | OCR 前填入书籍元数据和 1-5 层目录层级 |
| **批量处理** | `kzocr batch` 扫描目录批量处理 PDF |
| **SQLite 持久化** | 逐页进度三态机 + benchmark 汇总 + 异常校对工单 |
| **并发调度** | 多引擎并发执行 + 自适应并发控制 + 429 退避 |
| **自适应调速** | 滚动窗口混合评分 + backoff 过滤 + AdaptiveController |
| **Web 面板** | 书籍管理/方剂详情/引擎看板/校对工作台/跨书搜索 |
| **REST API** | 8 个 JSON 端点，支持第三方系统集成 |
| **Docker 部署** | 一键 docker compose up |
| **Celery 异步** | 独立 worker 消费任务，redis broker/backend |

---

## 快速开始

### 安装

```bash
# 最小安装（编排 + 方剂解析）
pip install kzocr

# 含 Web 面板
pip install "kzocr[web]"
```

### 冒烟测试

```bash
kzocr smoke --skip-push
```

### 处理一本书

```bash
kzocr pipeline book.pdf --book-code TCM001
```

### 导出结果

```bash
# Markdown 格式
kzocr export TCM001

# JSON 结构化格式（含 recipes/herbs/quality）
kzocr export TCM001 --format json
```

### 启动 Web 面板

```bash
kzocr web
# 浏览器访问 http://localhost:8080
```

### Docker 部署

```bash
docker compose up -d
```

### Celery Worker 部署

异步任务（`process_book_task` 等）由独立的 Celery worker 进程消费，broker/backend 走 Redis。镜像**不含 OCR 引擎**（仅 PyMuPDF / numpy / celery / redis），引擎在运行时另行安装。

```bash
# 启动 redis + worker（worker 依赖 redis，自动排序）
docker compose up -d redis worker
```

`docker-compose.yml` 中的 `worker` 服务：

```yaml
worker:
  build: .
  command: ["celery", "-A", "kzocr.tcm_ocr.celery_tasks.tasks", "worker",
            "-Q", "books,pages,maintenance,archival,knowledge",
            "-c", "4", "--loglevel=info"]
  environment:
    - KZOCR_DB_DIR=/app/db
    - CELERY_BROKER_URL=redis://redis:6379/0
    - CELERY_RESULT_BACKEND=redis://redis:6379/1
  depends_on: [redis]
  restart: unless-stopped
```

关键环境变量：

| 变量 | 默认 | 说明 |
|------|------|------|
| `CELERY_BROKER_URL` | `redis://localhost:6379/0` | 任务队列 broker |
| `CELERY_RESULT_BACKEND` | `redis://localhost:6379/1` | 任务结果后端 |
| `KZOCR_PERSIST_DB` | `0` | 置 `1` 时任务完成后落库主线 BookDB（book/page/line + 字符级 bbox） |

worker 启动时日志会打印 `Celery worker 启动 | celery=<版本> app=tcm_ocr broker=<broker>`，用于生产环境无需 SSH 即可核对实际 celery 版本。

> 注意：Docker 镜像不包含 OCR 引擎。若通过 worker 跑真实 OCR，需在容器内安装对应引擎（PaddleOCR / RapidOCR / 本地 LLM），否则任务会在文档树重建阶段失败。

---

## 架构

```
PDF → run_engine()
        │
        ├── EngineRegistry (E1) — 注册可用引擎
        ├── EngineScheduler (E2) — 九步候选选择
        ├── GlyphVerifier (E3) — 5 检测器验证
        ├── OrchestrateBook (E4) — Tier123 编排主循环
        │     ├── Tier1: 书级引擎（kimi BookPipeline / Mock）
        │     ├── Tier2: 云端 VLM（SenseNova）
        │     ├── Tier3: 本地 LLM
        │     └── HumanGate: 全部失败记录
        ├── BookDB (F2) — SQLite page_progress + benchmark
        ├── TOC enrich (F1) — 章节树重建
        └── QualityChecker — LLM 质检
                │
                └── BookResult → pages text / recipes / anomalies / trace
                                 → kzocr export (md/json)
                                 → kzocr web  (dashboard / search)
```

---

## 命令参考

| 命令 | 说明 |
|------|------|
| `kzocr pipeline <pdf>` | 处理单本书（`--cross-check` 启用成功页跨引擎采样比对） |
| `kzocr batch <pdf_dir>` | 批量处理目录内所有 PDF |
| `kzocr export <code>` | 导出 Markdown（默认）或 JSON（`--format json`） |
| `kzocr smoke` | 端到端冒烟测试（`--skip-push` 不推送 kHUB） |
| `kzocr web` | 启动 Web 管理面板 |
| `kzocr review manifest <code>` | 生成全书审核清单（P0/P1/P2 优先级） |
| `kzocr review apply <code>` | 回写审核修正到 BookDB |
| `kzocr quality check <code>` | 运行方剂质检并写入 DB |
| `kzocr quality list <code>` | 列出质检结果 |
| `kzocr benchmark [status\|history\|run\|reset]` | 引擎性能基准查询与管理 |
| `kzocr completion <shell>` | 输出 shell 自动补全脚本（bash/zsh/fish） |
| `kzocr push <file>` | 推送文档到 kHUB |

---

## 配置

主要环境变量：

| 变量 | 默认 | 说明 |
|------|------|------|
| `KZOCR_DB_DIR` | `./db` | 数据库目录 |
| `KZOCR_USE_MOCK` | `0` | 启用 mock 引擎 |
| `KZOCR_SENSENOVA_API_KEY` | — | 云端 VLM 密钥 |
| `KIMI_ENGINE_DIR` | — | 书级引擎目录 |
| `KZOCR_ALLOW_CLOUD_VISION` | `0` | 允许发送图像到云端 |

完整配置见 `docs/deploy-v07.md`。

---

## REST API

启动 Web 面板后，`/api/` 提供 JSON 端点：

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/books` | 书籍列表 |
| GET | `/api/books/{code}` | 书籍详情 |
| GET | `/api/books/{code}/pages` | 逐页进度 |
| GET | `/api/books/{code}/anomalies` | 异常列表（`?status=pending`） |
| POST | `/api/books/{code}/anomalies/{id}/resolve` | 标记决议 |
| GET | `/api/books/{code}/recipes` | 方剂列表 |

FastAPI 自动生成 Swagger 文档：启动后访问 `http://localhost:8080/docs`

---

## 项目状态

| 版本 | 方向 | 测试 |
|------|------|------|
| v0.7 | 引擎编排层 | 396 |
| v0.8 | 方剂解析/章节合并/并发/校对 CLI/质量工程 | 441 |
| v0.9 | 并发集成/Web 面板 | 447 |
| v0.10 | 性能优化 | 449 |
| v0.11 | REST API/Docker | 455 |
| v0.12 | 仓库清理 | 455 |
| v0.13 | LLM 质检管道 | 462 |
| v0.14 | 产品化/可视化/批量 | 468 |
| v0.15 | 文档体系 + 评审修复 | 471 |
| v0.16 | LLM 质检增强 + API 文档 | 475 |
| v0.17 | Web 书籍登记 + 目录表单 | **479** |

---

## 相关项目

- [kHUB](https://github.com/keenkuang/kHUB) — 文档推送服务
- [秘方求真 OCR Pipeline](https://github.com/your-org/traedocu) — 姊妹项目（VLM+Web）
