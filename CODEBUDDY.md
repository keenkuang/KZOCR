# CODEBUDDY.md

This file provides guidance to CodeBuddy Code when working with code in this repository.

## 项目概述

KZOCR 是**中医古籍 OCR 编排工具**：将 kimi OCR（BookPipeline）对 PDF 的识别结果写入 zai 人工校对台（SQLite），导出经人工终校的 Markdown 后推送 kHUB，形成「扫描 → OCR → 校对 → 发布」闭环。当前发布版本 v0.6.0。

## 会话上下文恢复（失忆防护，必须遵守）
每个新会话开始处理任何任务前，**必须**先读取跨会话记忆以恢复上下文，不要问用户"我们现在要做什么"：
1. Read `memory/MEMORY.md`（路径：`/home/keen/.codebuddy/projects/home-keen-KZOCR/memory/MEMORY.md`）。它顶部有「当前进行中」块，可直接重新定位。
2. 若「当前进行中」指向某专题文件（如 `project_crop_formula.md`），且当前任务相关，**主动 Read 该文件**再动手，不要只看了索引就问"要做什么"。
3. 若 MEMORY.md 显示有未结任务，先确认要继续的方向再执行，优先沿用已落地的决策而非重新提议。
4. 任务有进展/转向时，用 `/context-management:context-save` 或手动更新 MEMORY.md 的「当前进行中」与对应专题文件，保证下次会话可续。

## 常用命令

### 安装 / 构建
```bash
pip install -e .                                  # 开发安装（依赖 PyMuPDF、numpy）
```

### 测试（pytest）
```bash
python -m pytest tests/ -v                        # 全量
python -m pytest tests/test_errors.py -v          # 单个测试文件
python -m pytest tests/test_errors.py::test_xxx -v   # 单个用例
```
仓库无 `requirements.txt`，CI 仅 `pip install PyMuPDF numpy pytest`。真实引擎依赖（MinerU/PaddleOCR/torch/LLM）运行环境另行安装。CI 未设覆盖率门禁；`.coverage` 与 `coverage_report/` 为历史产物，不要误以为是必需配置。

### 静态检查（强制）
```bash
ruff check kzocr/ tests/                          # 提交前必须无报错
```

### 端到端冒烟
```bash
kzocr smoke --skip-push                           # mock 链路 → 适配器 → 导出（不推送 kHUB）
```

### CI
`.github/workflows/test.yml`：`test` job 在 Python 3.10/3.11/3.12 上跑 `pytest tests/ -v`；`lint` job 跑 `ruff check kzocr/ tests/`。两份 job 相互独立。

## 架构（需跨多个文件理解）

### 数据流（v0.6 已实现）
```
CLI(kzocr/cli.py: main)
  → kzocr/engine/run.py: run_engine()
      ├─ KZOCR_USE_MOCK → mock_book_result()
      ├─ KZOCR_USE_VLM  → _run_vlm()
      └─ default        → _run_real()  (kimi BookPipeline)
  → BookResult
  → kzocr/adapter/to_zai_prisma.py: push_book_to_zai()  → zai SQLite（自动建表）
  → kzocr/export_zai.py: export_book_markdown()         → Markdown 文件
  → kzocr/khub/client.py: push_document()              → kHUB API
```

### 两个极易混淆的目录
- **`kzocr/engine/`**（单数）= OCR 引擎**执行层**：`run.py`（三模式调度 + PDF 渲染 `_pdf_page_to_numpy` + 版心裁剪）、`mock.py`、`types.py`（归一化数据结构 + v0.7 `EngineRunner` 协议）。
- **`kzocr/engines/`**（复数）= 共享**基础设施**：`errors.py`（D1 异常分类 + `retry_with_policy` 指数退避）、`atomic.py`（C2 原子写 + 路径穿越防御）、`leakage.py`（C1 四层跨页泄漏）、`ratelimit.py`（C3 自适应限流）、`hierarchy.py`（D4 字符数尖峰）。
调用关系是 `engine/` 依赖 `engines/`，反之不成立。

### 核心数据结构（贯穿全链路）
`kzocr/engine/types.py` 的归一化 dataclass 是整套管道的"通用语"：`EngineResult → LineResult → ParagraphResult → PageResult → BookResult`。zai 适配、导出、khub 推送都以 `BookResult` 为输入；`BookResult.final_markdown` 由人工终校后填充。新加字段若无特殊说明，默认用 `field(default_factory=...)`（已正确避免可变默认陷阱）。

### v0.6 与 v0.7 的关系（最重要）
- **v0.6（当前 `main`）**：引擎选择是硬编码 `if-else`（`run_engine → mock/VLM/real`），无注册中心、无调度器、无字形验证调用。
- **v0.7（设计阶段，尚未实现）**：自适应引擎编排层。方案见 `docs/plans/ocr-engine-unification.v0.7.md`（概览）+ `.v0.7-DETAILED.md`（12 节详设），多角色评审见 `docs/reviews/`。
- 规划的新模块 `kzocr/scheduler/{registry,scheduler,verifier,orchestrator}.py` 目前**不存在**，不要假设它们已落地。唯一已落地的 v0.7 代码是 `types.py` 中的 `EngineRunner(Protocol)` 与扩展后的 `AdapterMeta`（`tier`/`batch_capable`/`probe` 字段）。改动 `run_engine` 等现有入口前，先读 v0.7 概览 §7（迁移策略：委派模式，旧签名保留）。

## 约定（来自 CONTRIBUTING.md）
- 目标 Python 3.10+，每个文件开头 `from __future__ import annotations`。
- 函数参数与返回值**必须**包含完整类型注解。
- 提交信息用 Conventional Commits：`feat:` / `fix:` / `docs:` / `test:` / `refactor:` / `ci:`（中文描述）。
- 分支：`main` 受保护不可直推；并行开发线 `m1`；日常功能分支 `feat/xxx`、`fix/xxx`。
- 测试：pytest，文件放 `tests/`，命名 `test_<module>.py`；网络/文件系统等外部依赖**必须 mock**；新功能须带测试，bugfix 须带回归测试。

## 已知跨文件不一致（2026-07-10，影响实现时需注意）
1. **egress 导入路径**：真实模块位于 `kzocr/security/egress.py`（v0.7 概览 §4.5 与 DETAILED §4.5 均已更正为 `from kzocr.security.egress import validate_url`；`khub/client.py:16` 亦引用 `..security.egress`）。以 `kzocr/security/egress.py` 为准，不要写成 `kzocr.engines.egress`。
2. **功能代号**：代码/提交/文档中大量出现 `B1–B8`、`C1–C5`、`D0–D4`（以及评审角色标签 architect/security/ops/performance/testing/domain/pm/sweng）。它们分别对应 v0.3/v0.4/v0.5 的功能里程碑代号，阅读历史提交与评审报告时需对应（含义见 README「功能历史」）。

> 已解决：原 `#1 ProbeResult.keys 类型`（`dict[str, str]` 含明文 `sk-xxx`）已由 PR #1（`fix: v0.7 types.py 同步`，2026-07-10）同步为 `dict[str, bool]`，与设计 §3.3 对齐，无需再特殊处理。

## 设计文档索引
- 方案：`docs/plans/ocr-engine-unification.{v0.3-FREEZE, v0.4-AMEND, v0.5-AMEND, v0.7, v0.7-DETAILED}.md`
- 评审报告：`docs/reviews/<日期>-round<N>-v0.7/`（多角色）。v0.7 已历经多轮评审（round1→round9），仍处于设计评审阶段，未进入编码。
- 变更记录：`CHANGELOG.md`、`docs/TEST_REPORT.md`。
