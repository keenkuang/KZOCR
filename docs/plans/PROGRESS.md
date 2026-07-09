# KZOCR 工作状态快照（crash-safe）

> 最后更新：2026-07-09
> 用途：CodeBuddy 当机/重启后，从这里恢复上下文。本文件随代码一起提交并推送到 GitHub。
> 当前焦点：**统一 OCR 引擎架构方案**（用户愿景：统一适配器 / 可切换路由 / 字形校验门 / 人工兜底 / 校对后结构化归档）

## 1. 最近已落地并推送 GitHub 的提交（origin/main，用 id_ed25519_kzocr key）

| commit | 内容 |
|---|---|
| `a92935d` | docs: round3 评审汇总 summary（含 6 项假设裁决 + 修订清单） |
| `f3ce06b` | docs: round3 多角色评审(8角色)初稿 |
| `d69c37f` | docs: 加入工作状态快照 PROGRESS.md |
| `a16af41` | docs: 统一 OCR 引擎架构方案（草案 v0.1） |
| `1a0f27c` | fix/feat: 落地第2轮评审整改(H1–H8) + 修复 2 个集成阻塞 bug |

- **推送方式**：本仓库已设 `git config core.sshCommand "ssh -i ~/.ssh/id_ed25519_kzocr -o IdentitiesOnly=yes"`（默认 `id_ed25519` 是 khub-TCM 只读 deploy key，会被拒）。推送 KZOCR 必须用 kzocr key。
- 测试：`pytest tests/` 全 15 例通过（整改提交前已验证）。

## 2. 本次会话已完成

1. **恢复引擎集成**：修复 2 个之前未提交的整改 bug（`to_zai_prisma.py` 缺 `logger` NameError；`test_vlm.py` mock 缺 `engine_label`）。
2. **解锁真实 kimi 引擎**：上游已修复，`BookPipeline(config)` + `process_book(pdf, book_id)` 与 `run.py:_run_real` 调用一致。
3. **VLM 直连已验证**：`run.py:_run_vlm` 已实现+测试，开关 `KZOCR_USE_VLM=1`。
4. **round3 多角色评审（8 角色）完成**：`docs/reviews/2026-07-09-round3/` 下 architect/security/performance/domain/maintainability/data_integrity/proofreading_ux/testing 各一份 + `summary.md`。
5. **方案修订到 v0.2**：吸收评审，落定 5 道硬门槛(H0-A~E)、结构化适配器返回、字形 `glyph_status` 枚举、版心裁剪非脱敏修正、`UNKNOWN` 入 HumanGate、`is_mock` 阻断 publish、目标 schema=规范 `schema.prisma`、6 项假设裁决。文件 `docs/plans/ocr-engine-unification.md`。

## 3. 环境关键事实（重启后先读这个）

- KZOCR 仓库：`/home/keen/KZOCR`，分支 `main`，远程 `git@github.com:keenkuang/KZOCR.git`（SSH）。
- kimi 真实引擎：`/home/keen/kimi_agent_ocr/tcm_ocr_system_v1.1`（经 `KIMI_ENGINE_DIR` 注入 sys.path），现已可导入。
- VLM 本地服务（PaddleOCR-VL-1.6 llama-server）**当前未监听** 127.0.0.1:18080 → 跑 VLM 直连需先起服务或配 SenseNova key。
- 无 GPU 环境：真实 kimi 多引擎逐行 OCR 可能跑不动；VLM 整页推理是无 GPU 最佳出路。

## 4. round3 评审核心结论（详细见 summary.md）

- 8/8 角色判「有条件通过」；进入实现前须先闭合 **5 道硬门槛**：契约冻结、共享逻辑下沉、安全端点收敛、目标 schema 对齐、性能预算。
- 最危险三处：归档层误把扁平子集当事实(C1)、版心裁剪被误当出境脱敏、UNKNOWN 未触发人工兜底导致错字漏放。
- 6 项假设裁决：仅「默认 single」「KZOCR 内置字形白名单」直接采纳；其余四项均「调整」。
- `is_mock` 必须强制透传并阻断 publish，否则重演 round2「假古籍」。

## 5. 任务清单（TaskCreate #15–#18）

- #15 编写统一 OCR 引擎架构方案（草案）—— ✅ 已完成（v0.2）
- #16 多角色评审统一 OCR 引擎方案 —— ✅ 已完成（round3，8 角色 + summary）
- #17 依评审修订方案并频繁提交到 github —— ✅ 刚完成（方案 v0.2 已写，待本次提交推送）
- #18 实施阶段1-6（适配器注册表→路由→字形校验→人工兜底→归档）—— ⏳ 待用户确认后启动

## 6. 方案第 8 章 6 项假设裁决（round3）

1. 字形校验：默认不加独立再识别视觉模型，**但预留 VisionRecheckAdapter 挂点**（仅本地视觉执行）。
2. 最小小节：不钉死三级标题，改**可配置 + 按 book_type + 经 contentNodeId 挂载**，严禁重切生成新 Line。
3. 方剂库：主链**只写 zai** 且用规范 FormulaComposition；khub 同步异步可选、不阻塞。
4. consensus：默认 single（**提升为硬约束**）；无 GPU 全本地 consensus 拒绝启动；含云端时 N≤2。
5. 配置：集中 `Config.engines.<name>` + 加载期校验；**密钥绝不进 toml**，每适配器 toml 仅可选覆盖层。
6. 字形知识库：**KZOCR 内置精简白名单为事实源**（进程内镜像），kimi term_kb 仅可选增强。

## 7. 推荐的恢复后第一步

1. 读 `docs/plans/ocr-engine-unification.md`（v0.2）与 `docs/reviews/2026-07-09-round3/summary.md`。
2. 方案已就绪，进入**阶段 1 实施**（任务 #18）：先落 H0-A~E 五门槛 → `_common.py` 共享逻辑下沉 + kimi 薄封装 + 端点 SSRF 收敛 + 配置单一真相源。
3. 每个里程碑提交+推送（kzocr key），防当机丢进度。
4. 若用户只想先验证某条链路（如 VLM 直连），需先起本地 llama-server 或配 SenseNova key。
