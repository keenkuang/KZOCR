# KZOCR 工作状态快照（crash-safe）

> 最后更新：2026-07-09
> 用途：CodeBuddy 当机/重启后，从这里恢复上下文。本文件随代码一起提交并推送到 GitHub。
> 当前焦点：**统一 OCR 引擎架构方案**（用户愿景：统一适配器 / 可切换路由 / 字形校验门 / 人工兜底 / 校对后结构化归档）

## 1. 最近已落地并推送 GitHub 的提交（origin/main）

| commit | 内容 |
|---|---|
| `a16af41` | docs: 统一 OCR 引擎架构方案（草案 v0.1）→ `docs/plans/ocr-engine-unification.md` |
| `1a0f27c` | fix/feat: 落地第2轮评审整改(H1–H8) + 修复 2 个集成阻塞 bug（见下） |

- 推送方式：本仓库已设 `git config core.sshCommand "ssh -i ~/.ssh/id_ed25519_kzocr -o IdentitiesOnly=yes"`（默认 `id_ed25519` 是 khub-TCM 只读 deploy key，会被拒）。**推送 KZOCR 必须用 kzocr key**。
- 测试：`pytest tests/` 全 15 例通过（提交前已验证）。

## 2. 本次会话已完成的动作

1. **恢复引擎集成**：审查并修复了 2 个之前未提交的整改 bug：
   - `kzocr/adapter/to_zai_prisma.py` 缺失模块级 `logger` → `NameError`，已加 `logger = logging.getLogger(__name__)`。
   - `tests/test_vlm.py` 夹具：mock 适配器缺 `engine_label` 属性 → 断言失败，已补 `mock_vlm.engine_label = VLM_ENGINE_LABEL`。
2. **解锁真实 kimi 引擎**：记忆中记录的"破损重构态"已在上游修复。实测 `from tcm_ocr.pipeline.book_pipeline import BookPipeline` 可导入；签名 `BookPipeline(config)` + `process_book(pdf_path, book_id)` 与 `kzocr/engine/run.py:_run_real` 调用方式完全一致 → 集成点已对齐。
3. **VLM 直连已验证**：`run.py:_run_vlm`（绕过 BookPipeline）已实现+测试，开关 `KZOCR_USE_VLM=1`。
4. **写出架构方案草案** `docs/plans/ocr-engine-unification.md`（五原则、分层架构、适配器清单、路由层、字形校验层、人工兜底、归档层、阶段0-6、6 项待评审假设）。
5. 创建任务 #15–#18（见第 5 节）。
6. 开始 **round3 多角色评审**：已建目录 `docs/reviews/2026-07-09-round3/`，但 8 角色评审文件**尚未撰写**（本次会话只触发了 testing 角色 agent，结果未收回）。

## 3. 环境关键事实（重启后先读这个）

- KZOCR 仓库：`/home/keen/KZOCR`，分支 `main`，远程 `git@github.com:keenkuang/KZOCR.git`（SSH）。
- kimi 真实引擎：`/home/keen/kimi_agent_ocr/tcm_ocr_system_v1.1`（经 `KIMI_ENGINE_DIR` 注入 sys.path），现已可导入。
- VLM 本地服务（PaddleOCR-VL-1.6 llama-server）**当前未监听** 127.0.0.1:18080 → 跑 VLM 直连需先起服务或配 SenseNova key。
- 无 GPU 环境：真实 kimi 多引擎逐行 OCR 可能跑不动；VLM 整页推理是无 GPU 的最佳出路。

## 4. round3 多角色评审（✅ 已完成，2026-07-09）

- 8 角色评审文件均已产出：`docs/reviews/2026-07-09-round3/{architect,security,performance,domain,maintainability,data_integrity,proofreading_ux,testing}.md`
- 综合汇总：`docs/reviews/2026-07-09-round3/summary.md`
- **总评**：有条件通过。方向获一致认可，但有 12 项 High 级硬伤须先闭合（A1–A4 架构契约/职责、B1–B3 安全暴露面/数据最小化/假数据、C1–C4 性能资源、D1–D4 领域贴合、E1–E5 数据完整与校对闭环）。
- **第 8 章 6 项假设已全部收口**（见 summary 第二节表格），其中假设 1/2/5 被修订。
- **下一步**：据 summary 修订 `ocr-engine-unification.md` → v0.2，提交推送，再进阶段 1 实施。

## 5. 任务清单（TaskCreate #15–#18）

- #15 编写统一 OCR 引擎架构方案（草案）—— ✅ 已完成
- #16 多角色评审统一 OCR 引擎方案 —— 🔄 进行中（round3，待补齐 8 角色）
- #17 依评审修订方案并频繁提交到 github —— ⏳ 待 #16 完成
- #18 实施阶段1-6（适配器注册表→路由→字形校验→人工兜底→归档）—— ⏳ 待方案定稿

## 6. 方案第 8 章"待评审确认"的 6 项假设

1. 字形校验机制：字典/知识库 + 置信度 + 多引擎共识 为主，暂不加独立再识别视觉模型？
2. 最小小节定义：TOC 三级标题，还是更小（段落/方证）？
3. 方剂库归属：写 zai `Formula` 表即可，还是必须同步 khub 方剂系统（跨库）？
4. consensus 模式成本：无 GPU 下是否默认 single、consensus 仅可选？
5. 适配器配置存放：集中 `config.py` 字段 vs 每适配器独立 `*.toml`（倾向后者）？
6. 字形知识库来源：复用 kimi `term_kb`/RuntimeDB，还是 KZOCR 内置精简白名单（避免与引擎仓库强耦合）？

## 7. 推荐的恢复后第一步

1. 读 `docs/plans/ocr-engine-unification.md` 与 `docs/reviews/2026-07-09-round2/summary.md`（既有 H1–H8 整改背景）。
2. 继续 round3：spawn 8 个并行评审 agent（general-purpose），各自 Read 方案后写 `docs/reviews/2026-07-09-round3/<role>.md`，再综合写 `summary.md`。
3. 依据评审修订方案 → 提交并推送（用 kzocr key）→ 进入阶段 1 实施。
4. 每完成一个里程碑，提交+推送一次（防当机丢进度）。
