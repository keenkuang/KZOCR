# KZOCR — kimi OCR + zai 校对台 + kHUB 文档推送

[![Test Status](https://github.com/keenkuang/KZOCR/actions/workflows/test.yml/badge.svg)](https://github.com/keenkuang/KZOCR/actions/workflows/test.yml)
![Python Version](https://img.shields.io/badge/python-%3E%3D3.10-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Version](https://img.shields.io/badge/version-0.2.0-orange)

---

## 概述

KZOCR 是一个**中医古籍 OCR 编排工具**，将 kimi OCR 引擎（BookPipeline）对 PDF 的识别结果写入 zai 人工校对台（SQLite），最终导出经过人工终校的 Markdown 并推送至 kHUB 文档服务，形成**扫描 → OCR → 校对 → 发布**的完整闭环。

项目面向以下场景：

- **中医古籍数字化**：处理大量 TCM 古籍 PDF，生成可校对的结构化数据
- **人工校对衔接**：与 zai 校对台零改动集成，数据模型对齐 Prisma schema
- **多引擎调度**：mock 引擎（测试）、VLM 直连（无 GPU 环境）、真实引擎（全管线）三模式切换

### 引擎模式

| 模式 | 环境变量 | 说明 |
|------|---------|------|
| **mock** | `KZOCR_USE_MOCK=1` | 桩数据，端到端测试用，不调用任何引擎 |
| **VLM 直连** | `KZOCR_USE_VLM=1` | 绕过 BookPipeline，用 PaddleOCR-VL-1.6 或 SenseNova 逐页 VLM OCR，适合无 GPU 环境 |
| **真实引擎** | 默认 | 调用 kimi 的 `BookPipeline`（PaddleOCR + MinerU + RapidOCR + UniRec + 云端 LLM），失败自动降级 |

---

## 快速开始

```bash
# 安装
pip install kzocr

# 端到端冒烟测试（mock 引擎 → 适配器 → 导出 → 推送）
kzocr smoke --skip-push

# 运行 OCR（mock 模式）
KZOCR_USE_MOCK=1 kzocr pipeline sample.pdf --book-code TCM-001

# 从 zai 库导出校正后 Markdown
kzocr export TCM-001

# 推送文档至 kHUB
kzocr push output.md --title "中医古籍"
```

---

## 配置说明

KZOCR 通过环境变量配置，支持 `load_config()` 读取并构造 `Config` 单例。

### 核心路径

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `KIMI_ENGINE_DIR` | `""` | kimi OCR 引擎目录（含 `tcm_ocr` 包） |
| `ZAI_DIR` | `/home/keen/tcm_ocr_zai` | zai 校对台项目目录 |
| `KZOCR_OUTPUT_DIR` | `/tmp/kzocr/output` | VLM 缓存及中间产物输出目录（v0.5 D0） |
| `KHUB_BASE_URL` | `http://127.0.0.1:8000` | kHUB 服务基址 |

### 引擎模式

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `KZOCR_USE_MOCK` | `0` | 强制使用 mock 引擎 |
| `KZOCR_USE_VLM` | `0` | 启用 VLM 直连模式 |
| `KZOCR_REQUIRE_REAL` | `0` | 真实引擎失败时抛错而非降级 |

### VLM 模式

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `KZOCR_VLM_ENGINE` | `auto` | VLM 引擎选择：`auto` / `sensenova` / `paddleocr_vl16` |
| `KZOCR_VLM_HOST` | `127.0.0.1` | llama-server 地址（PaddleOCR-VL 用） |
| `KZOCR_VLM_PORT` | `18080` | llama-server 端口 |
| `SENSENOVA_API_KEY` | `""` | SenseNova 云端 API Key |
| `SENSENOVA_MODEL` | `sensenova-6.7-flash-lite` | SenseNova 模型名 |
| `SENSENOVA_BASE_URL` | `https://token.sensenova.cn/v1/chat/completions` | SenseNova 端点 |
| `SENSENOVA_TIMEOUT` | `180` | SenseNova 超时（秒） |
| `KZOCR_ALLOW_CLOUD_VISION` | `0` | 允许发图像至云端（需数据出境许可） |

### LLM 校对

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `KZOCR_LLM_ENABLED` | `0` | 启用云端 LLM 校对 |
| `KZOCR_LLM_API_KEY` | `""` | 云端 LLM API Key |
| `KZOCR_LLM_BASE_URL` | `""` | 云端 LLM Base URL |
| `KZOCR_LLM_MODEL` | `qwen-max` | 云端 LLM 模型名 |

> KZOCR 自动将 `KZOCR_LLM_*` 映射为引擎内部 `GLM_*` 环境变量（仅当后者未设置时）。

### 安全与限流

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `KZOCR_MAX_PAGES` | `50` | VLM 模式最大处理页数（资源耗尽 DoS 防护） |
| `KZOCR_TOTAL_TIMEOUT` | `7200` | VLM 模式 wall-clock 总预算（秒） |
| `KZOCR_CACHE_TTL` | `86400` | VLM 缓存 TTL（秒，v0.5 D0） |
| `KZOCR_CLEAR_CACHE` | `""` | 设为 `1` 清除 VLM 缓存后重新识别（v0.5 D3） |

---

## 使用示例

### 基本管线

```bash
# 运行 OCR 管线（默认引擎），指定书号
kzocr pipeline book.pdf --book-code TCM-001

# 输出： BOOK_CODE=TCM-001
```

### 指定数据库

```bash
# pipeline 默认写入 kzocr.db（工作目录下的隔离库）
# 可指定自定义 zai 库路径
kzocr pipeline book.pdf --db /path/to/zai.db
```

### VLM 模式（无 GPU 环境）

```bash
# 使用本地 PaddleOCR-VL-1.6（需先启动 llama-server）
KZOCR_USE_VLM=1 kzocr pipeline book.pdf

# 使用 SenseNova 云端 VLM
SENSENOVA_API_KEY=sk-xxx KZOCR_USE_VLM=1 KZOCR_VLM_ENGINE=sensenova kzocr pipeline book.pdf
```

### 全链路冒烟测试

```bash
# mock 管道 + 导出验证（不推送 kHUB）
kzocr smoke --skip-push

# 带 kHUB 推送验证
kzocr smoke --verify
```

### 导出与推送

```bash
# 从 zai 库导出校正后 Markdown
kzocr export TCM-001 --out output.md

# 推送至 kHUB
kzocr push output.md --title "中医古籍"
```

---

## 架构

```
┌──────────────────────────────────────────────────────────────────┐
│  CLI (argparse)                                                  │
│  ~~~~~~~~~~~~~~~~                                                │
│  kzocr pipeline/export/push/smoke                                │
│  kzocr.cli:main                                                  │
└─────────┬────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────┐     ┌─────────────────────────────────────────┐
│  engine/run.py   │────▶│  run_engine()                          │
│  ───────────────  │     │  ├─ use_mock  → mock_book_result()    │
│  run_engine()     │     │  ├─ use_vlm   → _run_vlm()            │
│  _run_vlm()       │     │  └─ default   → _run_real()           │
│  _run_real()      │     │    (BookPipeline)                      │
└─────────────────┘     └──────────────┬──────────────────────────┘
                                       │
                                       ▼
                              ┌────────────────┐
                              │  BookResult     │
                              │  ────────────   │
                              │  .pages         │
                              │  .final_markdown│
                              │  .is_mock        │
                              │  .failed_pages   │
                              └────────┬───────┘
                                       │
                              ┌────────▼────────────┐
                              │  adapter/            │
                              │  to_zai_prisma.py    │
                              │  ────────────────    │
                              │  push_book_to_zai()  │
                              │  → SQLite (zai DB)    │
                              └────────┬────────────┘
                                       │
                              ┌────────▼────────────┐
                              │  export_zai.py       │
                              │  ────────────────    │
                              │  export_book_markdown│
                              │  → Markdown 文件     │
                              └────────┬────────────┘
                                       │
                              ┌────────▼────────────┐
                              │  khub/client.py      │
                              │  ────────────────    │
                              │  push_document()     │
                              │  → kHUB API          │
                              └─────────────────────┘
```

### 关键模块

| 模块 | 路径 | 职责 |
|------|------|------|
| CLI | `kzocr/cli.py` | argparse 入口，调度 pipeline/export/push/smoke 四条子命令 |
| 配置 | `kzocr/config.py` | `Config` dataclass + 环境变量读取 |
| 引擎调度 | `kzocr/engine/run.py` | mock/VLM/real 三模式调度，PDF 渲染与版心裁剪 |
| 数据结构 | `kzocr/engine/types.py` | `BookResult` / `PageResult` / `LineResult` 等归一化 dataclass |
| 错误处理 | `kzocr/engines/errors.py` | D1 异常分类体系 + `retry_with_policy` 指数退避重试 |
| 泄漏防御 | `kzocr/engines/leakage.py` | C1 四层跨页文本泄漏检测 |
| 原子写入 | `kzocr/engines/atomic.py` | C2 原子写入 + 路径穿越防御 |
| 限流器 | `kzocr/engines/ratelimit.py` | C3 自适应速率限制 + 指数退避 |
| 层级异常 | `kzocr/engines/hierarchy.py` | D4 字符数尖峰异常检测 |
| 适配器 | `kzocr/adapter/to_zai_prisma.py` | `BookResult` → zai SQLite 直写，自动建表 |
| 导出 | `kzocr/export_zai.py` | zai 库 → Markdown 导出 |
| kHUB 客户端 | `kzocr/khub/client.py` | `POST /documents` 推送 API 调用 |
| 出站安全 | `kzocr/security/egress.py` | B3 代码级硬编码 egress allowlist + DNS 复检 |
| 内置资源 | `kzocr/resources/` | B5 种子数据（异体字映射、形似混淆、罕见字白名单、毒性药材） |

---

## 功能历史

### v0.3 FREEZE（B1–B8）— 基础架构

- **B1** — 字形验证体系：`glyph_status` 枚举 + `glyph_verified` 文本列
- **B2** — `_common.py` 适配器到 LineResult 统一折算
- **B3** — 出站 egress allowlist 代码级硬编码（无 toml 绕过）
- **B4** — `is_mock` sink 守卫：mock 数据禁止写入 zai DB
- **B5** — 内置种子资源：`variant_map` / `confusion_set` / `rare_allowlist` / `toxic_herbs`
- **B6** — 双重上限守卫：`MAX_PAGES=50` + `TOTAL_TIMEOUT=7200s`
- **B7** — `crop_img` 瞬态修复：存路径引用，不存像素数据
- **B8** — 默认 VLM/视觉优先（无 GPU 环境自动选择）

### v0.4 AMEND（C1–C5）— 健壮性

- **C1** — 四层跨页泄漏防御（`leakage.py`）
- **C2** — 原子写入 + 路径穿越防御（`atomic.py`）
- **C3** — 自适应速率限制 + 指数退避（`ratelimit.py`）
- **C2+C3** — 安全加固：持久化限流器 + `max_entries` 守卫

### v0.5 AMEND（D0–D4）— 异常处理

- **D0** — Config 扩展：`kzocr_output_dir`、`cache_ttl_seconds`
- **D1** — 异常分类体系（5 类异常 + `retry_with_policy` 重试策略）
- **D2** — VLM 主循环结构化重试（API 重试、OverSize DPI 降低、`failed_pages` 追踪）
- **D3** — VLM 逐页缓存（`config_hash` + TTL + `KZOCR_CLEAR_CACHE=1` 清除）
- **D4** — 层级异常检测（`char_count_spike` 字符数尖峰检测）

---

## 开发

```bash
# 克隆仓库
git clone git@github.com:keenkuang/KZOCR.git
cd KZOCR

# 安装开发版本
pip install -e .

# 运行测试（177 个用例，~1s）
python -m pytest tests/ -v

# 端到端冒烟
kzocr smoke --skip-push
```

### 测试结构

| 文件 | 用例数 | 覆盖 |
|------|--------|------|
| `tests/test_pipeline.py` | 4 | 全链路回归 |
| `tests/test_config.py` | 6 | D0 配置加载 |
| `tests/test_errors.py` | 24 | D1 异常 + 重试 |
| `tests/test_hierarchy.py` | 17 | D4 层级异常检测 |
| `tests/test_vlm.py` | 16+ | D2 重试 + D3 缓存 |
| `tests/test_common.py` | — | B2 适配器折算 |
| `tests/test_leakage.py` | — | C1 泄漏防御 |
| `tests/test_atomic.py` | — | C2 原子写入 |
| `tests/test_ratelimit.py` | — | C3 限流器 |
| `tests/test_egress.py` | — | B3 出站校验 |
| `tests/test_types.py` | — | 数据模型 |
| `tests/test_resources.py` | — | B5 种子数据 |
| `tests/test_cloudllm_env.py` | — | LLM 环境变量映射 |

---

## 项目状态

**最新版本**：v0.2.0（语义版本号，功能里程碑见 CHANGELOG.md）

**已实现**：v0.3 FREEZE 基础架构 + v0.4 AMEND 健壮性 + v0.5 AMEND 异常处理体系

**测试覆盖率**：177 个测试全通过（~1s）

**文档**：架构设计文档见 `docs/plans/`，多角色评审报告见 `docs/reviews/`

---

最后更新：2026-07-10
