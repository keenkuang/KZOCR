# KZOCR 工作状态快照（crash-safe）

> 最后更新：2026-07-09
> 用途：CodeBuddy 当机/重启后，从这里恢复上下文。本文件随代码一起提交并推送到 GitHub。
> 当前焦点：**统一 OCR 引擎架构方案**（用户愿景：统一适配器 / 可切换路由 / 字形校验门 / 人工兜底 / 校对后结构化归档）

## 1. 最近已落地并推送 GitHub 的提交（origin/main，用 id_ed25519_kzocr key）

| commit | 内容 |
|---|---|
| （待提交） | docs: v0.3 定稿冻结(B1–B8) + 更新 PROGRESS |
| `1afca27` | docs: round4 多角色评审 v0.2 (8角色) + 汇总 |
| `a92935d` | docs: round3 评审汇总 summary（含 6 项假设裁决 + 修订清单） |
| `f3ce06b` | docs: round3 多角色评审(8角色)初稿 |
| `d69c37f` | docs: 加入工作状态快照 PROGRESS.md |
| `a16af41` | docs: 统一 OCR 引擎架构方案（草案 v0.1） |
| `1a0f27c` | fix/feat: 落地第2轮评审整改(H1–H8) + 修复 2 个集成阻塞 bug |

- **推送方式**：本仓库已设 `git config core.sshCommand "ssh -i ~/.ssh/id_ed25519_kzocr -o IdentitiesOnly=yes"`（默认 `id_ed25519` 是 khub-TCM 只读 deploy key，会被拒）。推送 KZOCR 必须用 kzocr key。
- 测试：`pytest tests/` 全 15 例通过（整改提交前已验证）。

## 2. 本次会话已完成

1. **恢复引擎集成**：修复 2 个之前未提交的整改 bug（`to_zai_prisma.py` 缺 `logger` NameError；`test_vlm.py` mock 缺 `engine_label`）。
2. **解锁真实 kimi 引擎**：上游已修复，`BookPipeline(config)` + `process_book(pdf, book_id)` 与 `kzocr/engine/run.py:_run_real` 调用一致。
3. **VLM 直连已验证**：`run.py:_run_vlm` 已实现+测试，开关 `KZOCR_USE_VLM=1`。
4. **round3 多角色评审（8 角色）完成**：`docs/reviews/2026-07-09-round3/` 下 8 份 + `summary.md`。
5. **方案修订到 v0.2**：吸收评审，落定 5 道硬门槛、结构化适配器返回、字形 `glyph_status` 枚举、版心裁剪非脱敏修正、`UNKNOWN` 入 HumanGate、`is_mock` 阻断 publish、目标 schema=规范 `schema.prisma`、6 项假设裁决。
6. **round4 多角色评审（v0.2）完成**：`docs/reviews/2026-07-09-round4/` 下 8 份 + `summary.md`。结论 v0.2「有条件定稿」。
7. **v0.3 定稿冻结（B1–B8）**：写入 `docs/plans/ocr-engine-unification.v0.3-FREEZE.md`，8 项 blocker 全部拍板（不再"二者择一"悬决），可直接进阶段 1。

## 3. 环境关键事实（重启后先读这个）

- KZOCR 仓库：`/home/keen/KZOCR`，分支 `main`，远程 `git@github.com:keenkuang/KZOCR.git`（SSH）。
- kimi 真实引擎：`/home/keen/kimi_agent_ocr/tcm_ocr_system_v1.1`（经 `KIMI_ENGINE_DIR` 注入 sys.path），现已可导入。
- VLM 本地服务（PaddleOCR-VL-1.6 llama-server）**当前未监听** 127.0.0.1:18080 → 跑 VLM 直连需先起服务或配 SenseNova key。
- 无 GPU 环境：真实 kimi 多引擎逐行 OCR 可能跑不动；VLM 整页推理是无 GPU 最佳出路。

## 4. round3 + round4 评审核心结论

- round3：8/8「有条件通过」，进入实现前须先闭合 5 道硬门槛（契约冻结/共享逻辑下沉/安全端点收敛/目标 schema 对齐/性能预算）。最危险三处：归档层误把扁平子集当事实、版心裁剪被误当出境脱敏、`UNKNOWN` 未触发人工兜底致错字漏放。
- round4（审 v0.2）：8/8「有条件通过」，v0.2 相对 v0.1 是质变性改善，round3 问题全被接住（无一项未闭合）。但定稿前须冻结 8 项 blocker（B1–B8），最大 blocker：`glyph_status`/`glyph_verified` 二择一未决 + `AdapterPageResult→LineResult` 转换责任悬空 + 性能双闸阈值互斥。
- v0.3 已把 B1–B8 全部裁决冻结（详见第 6 节）。

## 5. 任务清单（TaskCreate #15–#20）

- #15 编写统一 OCR 引擎架构方案（草案）—— ✅ 已完成（v0.2）
- #16 多角色评审统一 OCR 引擎方案 —— ✅ 已完成（round3，8 角色 + summary）
- #17 依评审修订方案并频繁提交到 github —— ✅ 已完成（v0.2）
- #18 实施阶段1-6（适配器注册表→路由→字形校验→人工兜底→归档）—— ⏳ 定稿后启动
- #19 round4 多角色评审 v0.2 方案 —— ✅ 已完成（8 角色 + summary）
- #20 定稿冻结 B1–B8（v0.3）+ 启动阶段1 实施 —— 🔄 进行中（v0.3 已写，待提交；下一步切片① B4）

## 6. v0.3 定稿冻结 B1–B8（全部裁决，无悬决）

| # | blocker | 裁决 |
|---|---|---|
| B1 | `glyph_status`(枚举) vs `glyph_verified`(文本) 二择一未决 | **两者共存**：`glyph_verified` 保留为校验后文本，`glyph_status` 仅作判定枚举，职责切死 |
| B2 | `AdapterPageResult→LineResult` 转换责任悬空 | **单点归 `_common.py` + `EngineRouter`**：`adapter_to_line_result()` 唯一折算，适配器不得自行折算 |
| B3 | 出境 allowlist 治理缺位（toml 可绕过 SSRF） | **allowlist 写死代码常量**（`kzocr/security/egress.py`），toml 不得增删出境目标 |
| B4 | `is_mock` sink 端未落地（假古籍风险） | **阶段1 切片①立即做**：`to_zai_prisma.py` Book 表加 `is_mock` 列 + publish 守卫（is_mock 阻断入库） |
| B5 | 领域 4 资源文件库里为空 | **随包发布非空种子文件**（variant_map/confusion_set/rare_allowlist/toxic_herbs），阶段 3 落地 |
| B6 | `TOTAL_TIMEOUT=7200s` 与 `MAX_PAGES=500` 数学互斥 | **解耦双闸**：`MAX_PAGES=50`（内存闸）+ `TOTAL_TIMEOUT=7200s`（时间闸），文档明示关系 |
| B7 | `crop_img: ndarray` 长期驻留击穿内存 | **仅瞬态使用**，存储改 `(page,bbox)` 引用，不存像素 |
| B8 | 默认引擎反向（无 GPU→PaddleOCR vs 无 GPU 唯一可行是 VLM） | **无 GPU 默认 VLM/视觉优先**（本地服务在听 / 有云端 key 且允许出境） |

## 7. 推荐的恢复后第一步

1. 读 `docs/plans/ocr-engine-unification.md`（v0.2）、`v0.3-FREEZE.md`（裁决）、`docs/reviews/2026-07-09-round{3,4}/summary.md`。
2. **阶段 1 切片①（B4）**：给 `to_zai_prisma.py` 的 Book 表加 `is_mock` 列，并在 `push_book_to_zai` 入口对 `is_mock=True` 阻断 publish（ERROR + 返回 blocked）。这是最该死的"假古籍"缺口。
3. 后续切片②（B2/B3 `_common.py` 下沉 + `egress.py` allowlist）→ 阶段 2/3 → 4/5。
4. 每完成一个切片/里程碑，提交+推送（kzocr key），防当机丢进度。
