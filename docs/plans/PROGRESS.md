# KZOCR 工作状态快照（crash-safe）

> 最后更新：2026-07-10 19:52 CST
> 用途：CodeBuddy 当机/重启后，从这里恢复上下文。本文件随代码一起提交并推送到 GitHub。
> 当前焦点：**v0.5 AMEND — 异常处理体系改进**（D0-D4 已实施并合并，HEAD `1f52052`）

## 1. 最近已落地并推送 GitHub 的提交（origin/main，用 id_ed25519_kzocr key）

| commit | 内容 |
|---|---|
| `2904869` | **docs: v0.5 AMEND** — 异常处理体系改进方案 (D1-D4) |
| `e9c3c44` | fix: B7 crop_img 瞬态 — mock 示例填充 crop_img_path |
| `e2df42e` | feat: B5 内置种子资源目录 — 4 个非空 JSON + ResourceStore 加载器 |
| `835df7d` | fix: C2+C3 安全加固 — 路径穿越防御 + 限流器持久化 + 数据上限守卫 |
| `7c7dff8` | feat: B6 MAX_PAGES=50 + TOTAL_TIMEOUT=7200s wall-clock budget |
| `f5168d8` | feat: B3 egress allowlist — code-level hardcoded domain whitelist |
| `581a958` | feat: Stage 1 implementation — C1 Leakage + C2 Atomic + C3 RateLimiter |
| `0ac0d85` | docs: round5 multi-role review (6 roles) — v0.4 AMEND summary |
| `cf3561d` | docs: v0.4 AMEND — absorb TOC project experience (C1-C5) |
| `0630d57` | fix: round4 review residual issues — freeze contract types + conversion |
| `d94ec0e` | docs: add test report (21 tests, coverage gaps) |
| `2e15e87` | test: CloudLLM env mapping unit tests (5 cases) |
| `a33325d` | docs: v0.3 定稿冻结(B1-B8 裁决) + 更新状态快照 |

- **推送方式**：本仓库已设 `git config core.sshCommand "ssh -i ~/.ssh/id_ed25519_kzocr -o IdentitiesOnly=yes"`（默认 `id_ed25519` 是 khub-TCM 只读 deploy key，会被拒）。推送 KZOCR 必须用 kzocr key。
- 测试：`pytest tests/` 全 15 例通过（整改提交前已验证）。

## 2. 本次会话已完成

1. **恢复引擎集成**：修复 2 个之前未提交的整改 bug（`to_zai_prisma.py` 缺 `logger` NameError；`test_vlm.py` mock 缺 `engine_label`）。
2. **解锁真实 kimi 引擎**：上游已修复，`BookPipeline(config)` + `process_book(pdf, book_id)` 与 `kzocr/engine/run.py:_run_real` 调用一致。
3. **VLM 直连已验证**：`run.py:_run_vlm` 已实现+测试，开关 `KZOCR_USE_VLM=1`。
4. **round3 多角色评审（8 角色）完成**：`docs/reviews/2026-07-09-round3/` 下 8 份 + `summary.md`。
5. **方案修订到 v0.2**：吸收评审，落定 5 道硬门槛、结构化适配器返回、字形 `glyph_status` 枚举、版心裁剪非脱敏修正、`UNKNOWN` 入 HumanGate、`is_mock` 阻断 publish、目标 schema=规范 `schema.prisma`、6 项假设裁决。
6. **round4 多角色评审（v0.2）完成**：`docs/reviews/2026-07-09-round4/` 下 8 份 + `summary.md`。结论 v0.2「有条件定稿」。
7. **v0.3 定稿冻结（B1–B8）**：写入 `docs/plans/ocr-engine-unification.v0.3-FREEZE.md`，8 项 blocker 全部拍板。
8. **v0.4 AMEND 落地**：C1-C5 全部实现，114 测试通过。C2+C3 安全加固（Security C2/C3）。B5/B6/B7 内建种子资源、MAX_PAGES、crop_img 瞬态修复。
9. **kimi_agent_ocr C4 修复**：INSERT OR REPLACE → ON CONFLICT DO UPDATE（本地 `d833cb4`，远程仓库未创建）。
10. **v0.5 AMEND 方案**：D1-D4 异常处理体系改进方案已多次评审并实施。

## 3. v0.5 AMEND 实施记录（2026-07-10）

实施详情和测试结果见 `CHANGELOG.md` v2026-07-10 章节。

| 模块 | 提交 | 状态 |
|------|------|------|
| D0: Config扩展 | `c4120cd` | ✅ |
| D1: errors.py | `c4120cd` | ✅ |
| D2: VLM重试 | `dd9b76f` | ✅ |
| D3: VLM断点续跑 | `1f52052` | ✅ |
| D4: 层级异常检测 | `cc6f52a` | ✅ |

## 4. 环境关键事实

- KZOCR 仓库：`/home/keen/KZOCR`，分支 `main`，远程 `git@github.com:keenkuang/KZOCR.git`（SSH）。
- kimi 真实引擎：`/home/keen/kimi_agent_ocr/tcm_ocr_system_v1.1`（经 `KIMI_ENGINE_DIR` 注入 sys.path），现已可导入。
- VLM 本地服务（PaddleOCR-VL-1.6 llama-server）**当前未监听** 127.0.0.1:18080 → 跑 VLM 直连需先起服务或配 SenseNova key。
- 无 GPU 环境：真实 kimi 多引擎逐行 OCR 可能跑不动；VLM 整页推理是无 GPU 最佳出路。

## 4. round3 + round4 评审核心结论

- round3：8/8「有条件通过」，进入实现前须先闭合 5 道硬门槛（契约冻结/共享逻辑下沉/安全端点收敛/目标 schema 对齐/性能预算）。最危险三处：归档层误把扁平子集当事实、版心裁剪被误当出境脱敏、`UNKNOWN` 未触发人工兜底致错字漏放。
- round4（审 v0.2）：8/8「有条件通过」，v0.2 相对 v0.1 是质变性改善，round3 问题全被接住（无一项未闭合）。但定稿前须冻结 8 项 blocker（B1–B8），最大 blocker：`glyph_status`/`glyph_verified` 二择一未决 + `AdapterPageResult→LineResult` 转换责任悬空 + 性能双闸阈值互斥。
- v0.3 已把 B1–B8 全部裁决冻结（详见第 6 节）。

## 5. 任务清单

- v0.3 定稿冻结 B1–B8 — ✅ 已冻结
- v0.4 AMEND C1–C5 — ✅ 已实现（114 测试通过）
- Stage 1 实现 (C1 Leakage + C2 Atomic + C3 RateLimiter) — ✅ 已落地
- B3 egress allowlist — ✅ 已落地
- B4 is_mock sink 守卫 — ✅ 已落地
- B5 内置种子资源 — ✅ 已落地
- B6 MAX_PAGES/TOTAL_TIMEOUT — ✅ 已落地
- B7 crop_img 瞬态 — ✅ 已落地
- C2+C3 安全加固 — ✅ 已落地
- **v0.5 AMEND D1-D4 — ✅ 已实施**（HEAD `1f52052`，177 测试通过）

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
