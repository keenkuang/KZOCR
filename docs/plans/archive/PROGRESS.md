# KZOCR 工作状态快照（crash-safe）

> 最后更新：2026-07-11 12:15 CST
> 用途：CodeBuddy 当机/重启后，从这里恢复上下文。本文件随代码一起提交并推送到 GitHub。
> 当前焦点：**tcm_ocr 集成 + 视觉回看 + Web UI + 引擎管理**

## 1. 最近已落地并推送 GitHub 的提交（origin/main）

| commit | 内容 |
|---|---|
| 待提交 | **feat: tcm_ocr 子模块代码集成到 kzocr/tcm_ocr** — 94 个 Python 文件，导入路径全部更新为内部引用 |
| 待提交 | **feat: VisionRecheckAdapter 视觉回看** — 两级校验（文本+视觉），温度/max_tokens 统一 |
| 待提交 | **refactor: 移除 Tier2 云端 VLM** — 编排管道跳过 Tier2 直走 Tier3 |
| 待提交 | **feat: Web UI 引擎管理** — 完整 CRUD + 状态检测 + TCM 风格设计 |
| 待提交 | **feat: 监控/基准测试/Prompt 预览页** — 3 个新功能页面 |

- **推送方式**：本仓库已设 `git config core.sshCommand "ssh -i ~/.ssh/id_ed25519_kzocr -o IdentitiesOnly=yes"`（默认 `id_ed25519` 是 khub-TCM 只读 deploy key，会被拒）。推送 KZOCR 必须用 kzocr key。
- 测试：`pytest tests/` 全 **482 通过，1 跳过**（需 kimi 引擎环境）。

## 2. 本次会话已完成

1. **tcm_ocr 子模块代码集成**：将 git 子模块（`kimi_agent_ocr` v1.1）的 94 个 Python 文件复制到 `kzocr/tcm_ocr/`，所有导入路径从 `tcm_ocr.*` 改为 `kzocr.tcm_ocr.*`。
2. **VisionRecheckAdapter 视觉回看**：实现两级校验（文本 + 视觉），支持 SenseNova/ModelScope Qwen，始终执行双检，任一不通过即降级。psycopg2 改为惰性导入。
3. **编排管道重构**：移除 Tier2 云端 VLM，Tier1 失败后直走 Tier3 本地 LLM。
4. **Web UI 引擎管理**：完整 CRUD + 状态检测（在线/需认证/离线三态）+ TCM 草药配色设计。
5. **Web UI 新页面**：监控看板、基准测试、Prompt 模板预览页。
6. **模型参数统一**：所有模型 temperature=0.0，max_tokens=2048。
7. **引擎注册**：30 个引擎（ModelScope 19 + 硅基流动 3 + SenseNova + 本地引擎）。
8. **Tier2 移除**：根据用户要求移除 Tier2 云端，Tier1→Tier3→HumanGate。

## 3. v0.5 AMEND 实施记录（2026-07-10）

实施详情和测试结果见 `CHANGELOG.md` v2026-07-10 章节。

| 模块 | 提交 | 状态 |
|------|------|------|
| D0: Config扩展 | `c4120cd` | ✅ |
| D1: errors.py | `c4120cd` | ✅ |
| D2: VLM重试 | `dd9b76f` | ✅ |
| D3: VLM断点续跑 | `1f52052` | ✅ |
| D4: 层级异常检测 | `cc6f52a` | ✅ |

## 4. 当前架构关键事实

- KZOCR 仓库：`/home/keen/KZOCR`，分支 `main`，远程 `git@github.com:keenkuang/KZOCR.git`（SSH）。
- tcm_ocr 引擎代码已集成到 `kzocr/tcm_ocr/`（94 个 Python 文件），不再是外部子模块依赖。
- VisionRecheckAdapter 使用 ModelScope Qwen3-VL-8B（视觉回看），OCR 引擎使用 SenseNova，二者不同供应商。
- 编排管道：Tier1 书级 → 两级校验（文本+视觉）→ 通过放行 / 失败走 Tier3 → HumanGate。
- Web UI 运行于 8088 端口（http://127.0.0.1:8088）
- 30 个引擎已注册（ModelScope 19 + 硅基流动 3 + SenseNova + 本地引擎）
- 数据库：`kzocr/storage/db.py`（SQLite），每个 book_code 对应一个 .db 文件

## 5. 环境变量

| 变量 | 用途 |
|------|------|
| `KZOCR_SENSENOVA_API_KEY` | SenseNova 云端 OCR |
| `KZOCR_MODELSCOPE_API_KEY` | ModelScope 视觉回看 + OCR |
| `KZOCR_SILICONFLOW_API_KEY` | 硅基流动 DeepSeek-OCR |
| `KZOCR_ALLOW_CLOUD_VISION` | 启用云端视觉回看（默认关） |
| `KZOCR_ENGINE_CONFIG_DIR` | 引擎配置 JSON 存储目录 |
| `KZOCR_DB_DIR` | SQLite 数据库目录 |

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
